from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

import numpy as np

from python_vna.daq.base import BackendFrame
from python_vna.models import SessionConfig


DEFAULT_SEGMENT_SECONDS = 600.0
TEXT_DAT_FORMAT = "python_vna_continuous_text_dat"
TEXT_DAT_VERSION = 3


@dataclass(slots=True)
class RecordingStatus:
    output_dir: Path
    manifest_path: Path
    segment_index: int
    elapsed_seconds: float
    total_samples: int
    total_frames: int


@dataclass(slots=True)
class _SegmentState:
    index: int
    path: Path
    start_unix_ns: int
    start_monotonic: float
    frames: int = 0
    samples: int = 0
    file: Any = None


def _timestamp_from_unix_ns(unix_ns: int, tz=timezone.utc) -> str:
    seconds, nanoseconds = divmod(int(unix_ns), 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, timezone.utc).replace(
        microsecond=nanoseconds // 1_000
    )
    return timestamp.astimezone(tz).isoformat(timespec="microseconds")


def local_timestamp_from_unix_ns(unix_ns: int) -> str:
    return _timestamp_from_unix_ns(unix_ns, datetime.now().astimezone().tzinfo)


def utc_timestamp_from_unix_ns(unix_ns: int) -> str:
    return _timestamp_from_unix_ns(unix_ns, timezone.utc)


def recording_directory_name(prefix: str = "recording") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}"


def session_recording_header(
    session: SessionConfig,
    *,
    device_name: str | None,
    channel_names: list[str],
    start_unix_ns: int,
    software_version: str,
) -> dict[str, Any]:
    return {
        "format": TEXT_DAT_FORMAT,
        "format_version": TEXT_DAT_VERSION,
        "software_version": software_version,
        "device_name": device_name,
        "start_time_local": local_timestamp_from_unix_ns(start_unix_ns),
        "start_time_utc": utc_timestamp_from_unix_ns(start_unix_ns),
        "start_unix_ns": int(start_unix_ns),
        "sample_rate": float(session.acquisition.sample_rate),
        "frame_size": int(session.acquisition.frame_size),
        "data_dtype": "float64_text",
        "delimiter": "tab",
        "time_policy": "frame_start_unix_ns_plus_sample_index_over_sample_rate",
        "channel_names": list(channel_names),
        "session": asdict(session),
        "columns": [
            "time_s",
            "local_time",
            "unix_ns",
            "frame_index",
            "sample_index",
            *channel_names,
        ],
    }


class ContinuousDatWriter:
    def __init__(
        self,
        output_dir: str | Path,
        session: SessionConfig,
        *,
        device_name: str | None,
        channel_names: list[str],
        software_version: str,
        segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
        manifest_interval_seconds: float = 5.0,
        time_fn=time.time_ns,
        monotonic_fn=time.monotonic,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.session = session
        self.device_name = device_name
        self.channel_names = list(channel_names)
        self.software_version = software_version
        self.segment_seconds = float(segment_seconds)
        self.manifest_interval_seconds = float(manifest_interval_seconds)
        self._time_fn = time_fn
        self._monotonic_fn = monotonic_fn
        self.manifest_path = self.output_dir / "manifest.json"
        self._segment: _SegmentState | None = None
        self._segments: list[dict[str, Any]] = []
        self._total_samples = 0
        self._total_frames = 0
        self._start_unix_ns: int | None = None
        self._start_monotonic: float | None = None
        self._last_manifest_monotonic = 0.0
        self._closed = False

    @property
    def total_samples(self) -> int:
        return self._total_samples

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def segment_index(self) -> int:
        return 0 if self._segment is None else self._segment.index

    def start(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self._start_unix_ns = int(self._time_fn())
        self._start_monotonic = float(self._monotonic_fn())
        self._open_segment(1)
        self._write_manifest(completed=False, force=True)

    def write_frame(self, frame: BackendFrame) -> RecordingStatus:
        if self._closed:
            raise RuntimeError("Cannot write to a closed continuous recording.")
        if self._segment is None:
            self.start()
        assert self._segment is not None
        elapsed_segment = float(self._monotonic_fn()) - self._segment.start_monotonic
        if self._segment.frames > 0 and elapsed_segment >= self.segment_seconds:
            self._close_segment()
            self._open_segment(self._segments[-1]["index"] + 1)
        assert self._segment is not None

        data = np.asarray(frame.data, dtype=float)
        if data.ndim != 2:
            raise ValueError("Continuous recording frames must be 2D channel x sample arrays.")
        channel_count, sample_count = data.shape
        if self.channel_names and len(self.channel_names) != channel_count:
            raise ValueError(
                "Continuous recording channel count does not match the configured channels."
            )
        if "read_end_unix_ns" in frame.metadata:
            fallback_end_unix_ns = int(frame.metadata["read_end_unix_ns"])
        else:
            fallback_end_unix_ns = int(self._time_fn())
        sample_rate = float(frame.sample_rate or self.session.acquisition.sample_rate)
        frame_unix_ns = int(
            frame.metadata.get(
                "frame_start_unix_ns",
                frame.metadata.get(
                    "unix_ns",
                    fallback_end_unix_ns
                    - int(round(max(sample_count - 1, 0) * 1_000_000_000 / sample_rate)),
                ),
            )
        )
        frame_start_sample = self._total_samples
        self._write_text_frame(
            data,
            frame_index=int(frame.frame_index),
            frame_start_unix_ns=frame_unix_ns,
            frame_start_sample=frame_start_sample,
            sample_rate=sample_rate,
        )
        self._segment.frames += 1
        self._segment.samples += int(sample_count)
        self._total_frames += 1
        self._total_samples += int(sample_count)
        now = float(self._monotonic_fn())
        if now - self._last_manifest_monotonic >= self.manifest_interval_seconds:
            self._segment.file.flush()
            self._write_manifest(completed=False, force=True)
        return self.status()

    def _write_text_frame(
        self,
        data: np.ndarray,
        *,
        frame_index: int,
        frame_start_unix_ns: int,
        frame_start_sample: int,
        sample_rate: float,
    ) -> None:
        assert self._segment is not None
        channel_count, sample_count = data.shape
        if sample_rate <= 0:
            sample_rate = float(self.session.acquisition.sample_rate)
        for sample_index in range(sample_count):
            absolute_sample = frame_start_sample + sample_index
            elapsed_seconds = absolute_sample / sample_rate
            unix_ns = frame_start_unix_ns + int(round(sample_index * 1_000_000_000 / sample_rate))
            values = [
                _format_float(elapsed_seconds),
                local_timestamp_from_unix_ns(unix_ns),
                str(unix_ns),
                str(frame_index),
                str(sample_index),
            ]
            values.extend(
                _format_float(data[channel_index, sample_index])
                for channel_index in range(channel_count)
            )
            self._segment.file.write("\t".join(values))
            self._segment.file.write("\n")

    def status(self) -> RecordingStatus:
        if self._start_monotonic is None:
            elapsed = 0.0
        else:
            elapsed = max(0.0, float(self._monotonic_fn()) - self._start_monotonic)
        return RecordingStatus(
            output_dir=self.output_dir,
            manifest_path=self.manifest_path,
            segment_index=self.segment_index,
            elapsed_seconds=elapsed,
            total_samples=self._total_samples,
            total_frames=self._total_frames,
        )

    def close(self, *, completed: bool = True, error: str | None = None) -> None:
        if self._closed:
            return
        if self._segment is not None:
            self._close_segment()
        self._closed = True
        self._write_manifest(completed=completed, error=error, force=True)

    def _open_segment(self, index: int) -> None:
        if self._start_unix_ns is None:
            self._start_unix_ns = int(self._time_fn())
        path = self.output_dir / f"segment_{index:04d}.dat"
        start_unix_ns = int(self._time_fn())
        header = session_recording_header(
            self.session,
            device_name=self.device_name,
            channel_names=self.channel_names,
            start_unix_ns=start_unix_ns,
            software_version=self.software_version,
        )
        header["segment_index"] = index
        handle = path.open("w", encoding="utf-8", newline="\n")
        _write_text_header(handle, header)
        self._segment = _SegmentState(
            index=index,
            path=path,
            start_unix_ns=start_unix_ns,
            start_monotonic=float(self._monotonic_fn()),
            file=handle,
        )

    def _close_segment(self) -> None:
        assert self._segment is not None
        self._segment.file.flush()
        self._segment.file.close()
        self._segments.append(
            {
                "index": self._segment.index,
                "path": self._segment.path.name,
                "start_unix_ns": self._segment.start_unix_ns,
                "start_time_local": local_timestamp_from_unix_ns(self._segment.start_unix_ns),
                "start_time_utc": utc_timestamp_from_unix_ns(self._segment.start_unix_ns),
                "frames": self._segment.frames,
                "samples": self._segment.samples,
                "bytes": self._segment.path.stat().st_size,
            }
        )
        self._segment = None

    def _manifest_payload(self, *, completed: bool, error: str | None = None) -> dict[str, Any]:
        now_unix_ns = int(self._time_fn())
        finalized = bool(completed or error is not None or self._closed)
        active_segment = None
        if self._segment is not None:
            active_segment = {
                "index": self._segment.index,
                "path": self._segment.path.name,
                "start_unix_ns": self._segment.start_unix_ns,
                "start_time_local": local_timestamp_from_unix_ns(self._segment.start_unix_ns),
                "start_time_utc": utc_timestamp_from_unix_ns(self._segment.start_unix_ns),
                "frames": self._segment.frames,
                "samples": self._segment.samples,
            }
        return {
            "format": "python_vna_continuous_manifest",
            "format_version": 1,
            "software_version": self.software_version,
            "device_name": self.device_name,
            "created_local": local_timestamp_from_unix_ns(self._start_unix_ns or now_unix_ns),
            "created_utc": utc_timestamp_from_unix_ns(self._start_unix_ns or now_unix_ns),
            "updated_local": local_timestamp_from_unix_ns(now_unix_ns),
            "updated_utc": utc_timestamp_from_unix_ns(now_unix_ns),
            "end_local": local_timestamp_from_unix_ns(now_unix_ns) if finalized else None,
            "end_utc": utc_timestamp_from_unix_ns(now_unix_ns) if finalized else None,
            "end_unix_ns": now_unix_ns if finalized else None,
            "start_unix_ns": self._start_unix_ns,
            "start_time_local": local_timestamp_from_unix_ns(self._start_unix_ns or now_unix_ns),
            "start_time_utc": utc_timestamp_from_unix_ns(self._start_unix_ns or now_unix_ns),
            "completed": bool(completed),
            "error": error,
            "sample_rate": float(self.session.acquisition.sample_rate),
            "frame_size": int(self.session.acquisition.frame_size),
            "channel_names": self.channel_names,
            "total_frames": self._total_frames,
            "total_samples": self._total_samples,
            "segment_seconds": self.segment_seconds,
            "segments": self._segments,
            "active_segment": active_segment,
        }

    def _write_manifest(
        self,
        *,
        completed: bool,
        error: str | None = None,
        force: bool = False,
    ) -> None:
        now = float(self._monotonic_fn())
        if not force and now - self._last_manifest_monotonic < self.manifest_interval_seconds:
            return
        payload = self._manifest_payload(completed=completed, error=error)
        tmp_path = self.manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.manifest_path)
        self._last_manifest_monotonic = now


def read_dat_header(path: str | Path) -> dict[str, Any]:
    header, _columns = _read_text_header(path)
    return header


def iter_dat_frames(path: str | Path):
    header, columns = _read_text_header(path)
    has_local_time = len(columns) > 1 and columns[1] == "local_time"
    unix_col = 2 if has_local_time else 1
    frame_col = 3 if has_local_time else 2
    data_start_col = 5 if has_local_time else 4
    channel_names = columns[data_start_col:]
    current_frame: int | None = None
    current_unix_ns: int | None = None
    samples: list[list[float]] = []

    def emit_frame():
        if current_frame is None:
            return None
        data = np.asarray(samples, dtype=float).T
        return header, {
            "frame_index": int(current_frame),
            "frame_unix_ns": int(current_unix_ns or 0),
            "channel_count": len(channel_names),
            "sample_count": data.shape[1] if data.ndim == 2 else 0,
            "channel_names": list(channel_names),
            "data": data,
        }

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            if line.startswith("time_s\t"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < data_start_col:
                continue
            frame_index = int(parts[frame_col])
            if current_frame is None:
                current_frame = frame_index
                current_unix_ns = int(parts[unix_col])
            elif frame_index != current_frame:
                emitted = emit_frame()
                if emitted is not None:
                    yield emitted
                current_frame = frame_index
                current_unix_ns = int(parts[unix_col])
                samples = []
            samples.append([float(value) for value in parts[data_start_col:]])
    emitted = emit_frame()
    if emitted is not None:
        yield emitted


def _format_float(value: float) -> str:
    return f"{float(value):.17g}"


def _clean_header_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, (list, tuple)):
        return ",".join(str(item).replace("\t", " ").replace("\n", " ") for item in value)
    return str(value).replace("\t", " ").replace("\n", " ")


def _write_text_header(handle: Any, header: dict[str, Any]) -> None:
    handle.write("# Python VNA continuous DAT\n")
    simple_keys = [
        "format",
        "format_version",
        "software_version",
        "device_name",
        "segment_index",
        "start_time_local",
        "start_time_utc",
        "start_unix_ns",
        "sample_rate",
        "frame_size",
        "data_dtype",
        "delimiter",
        "time_policy",
        "channel_names",
    ]
    for key in simple_keys:
        if key in header:
            handle.write(f"# {key}={_clean_header_value(header[key])}\n")
    handle.write(
        "# header_json="
        + json.dumps(header, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    )
    handle.write("# Data columns are tab-delimited.\n")
    handle.write("\t".join(str(column) for column in header["columns"]))
    handle.write("\n")


def _read_text_header(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    parsed_header: dict[str, Any] = {}
    columns: list[str] | None = None
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline()
        if not first_line.startswith("# Python VNA continuous DAT"):
            raise ValueError("Not a readable Python VNA continuous DAT file.")
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                content = stripped[1:].strip()
                if "=" not in content:
                    continue
                key, value = content.split("=", 1)
                if key == "header_json":
                    parsed_header = json.loads(value)
                elif key not in parsed_header:
                    parsed_header[key] = value
                continue
            columns = stripped.split("\t")
            break
    if columns is None:
        columns = list(parsed_header.get("columns", []))
    if not parsed_header:
        raise ValueError("Python VNA DAT header is missing.")
    if "columns" not in parsed_header:
        parsed_header["columns"] = columns
    return parsed_header, columns
