from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import queue
from tempfile import NamedTemporaryFile
import threading
import time
from typing import Any
import zipfile

import numpy as np

from python_vna.daq.base import BackendFrame
from python_vna.models import SessionConfig


DEFAULT_SEGMENT_SECONDS = 600.0
DEFAULT_MAX_SEGMENT_BYTES = 1_500_000_000
TEXT_DAT_FORMAT = "python_vna_continuous_text_dat"
TEXT_DAT_VERSION = 3
BINARY_DAT_FORMAT = "python_vna_continuous_binary_float64"
BINARY_DAT_VERSION = 1
SEGMENT_COMPRESSION_FORMAT = "zip"


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


@dataclass(slots=True)
class _CompressionResult:
    segment_path: Path
    archive_path: Path | None = None
    raw_bytes: int = 0
    compressed_bytes: int = 0
    error: str | None = None


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


def _normalize_storage_format(value: str) -> str:
    text = str(value or "text").strip().lower()
    if text in {"binary", "bin", "binary_float64", BINARY_DAT_FORMAT}:
        return "binary"
    return "text"


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
        max_segment_bytes: int | None = DEFAULT_MAX_SEGMENT_BYTES,
        manifest_interval_seconds: float = 5.0,
        compress_closed_segments: bool = True,
        storage_format: str = "text",
        time_fn=time.time_ns,
        monotonic_fn=time.monotonic,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.session = session
        self.device_name = device_name
        self.channel_names = list(channel_names)
        self.software_version = software_version
        self.segment_seconds = float(segment_seconds)
        self.max_segment_bytes = None if max_segment_bytes is None else int(max_segment_bytes)
        self.manifest_interval_seconds = float(manifest_interval_seconds)
        self.storage_format = _normalize_storage_format(storage_format)
        self.compress_closed_segments = bool(compress_closed_segments) and self.storage_format == "text"
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
        self._compression_queue: queue.Queue[Path | None] | None = None
        self._compression_results: queue.Queue[_CompressionResult] | None = None
        self._compression_thread: threading.Thread | None = None

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
        if self.compress_closed_segments:
            self._start_compression_worker()
        self._open_segment(1)
        self._write_manifest(completed=False, force=True)

    def write_frame(self, frame: BackendFrame) -> RecordingStatus:
        if self._closed:
            raise RuntimeError("Cannot write to a closed continuous recording.")
        if self._segment is None:
            self.start()
        segment = self._require_open_segment()
        elapsed_segment = float(self._monotonic_fn()) - segment.start_monotonic
        if segment.frames > 0 and (
            elapsed_segment >= self.segment_seconds
            or self._segment_reached_size_limit()
        ):
            self._close_segment()
            self._open_segment(self._segments[-1]["index"] + 1)
            segment = self._require_open_segment()

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
        if self.storage_format == "binary":
            self._write_binary_frame(data)
        else:
            self._write_text_frame(
                data,
                frame_index=int(frame.frame_index),
                frame_start_unix_ns=frame_unix_ns,
                frame_start_sample=frame_start_sample,
                sample_rate=sample_rate,
            )
        segment.frames += 1
        segment.samples += int(sample_count)
        self._total_frames += 1
        self._total_samples += int(sample_count)
        self._drain_compression_results()
        now = float(self._monotonic_fn())
        if now - self._last_manifest_monotonic >= self.manifest_interval_seconds:
            segment.file.flush()
            self._write_manifest(completed=False, force=True)
        return self.status()

    def _require_open_segment(self) -> _SegmentState:
        if self._segment is None:
            raise RuntimeError("Continuous recording segment is not open.")
        return self._segment

    def _write_text_frame(
        self,
        data: np.ndarray,
        *,
        frame_index: int,
        frame_start_unix_ns: int,
        frame_start_sample: int,
        sample_rate: float,
    ) -> None:
        segment = self._require_open_segment()
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
            segment.file.write("\t".join(values))
            segment.file.write("\n")

    def _write_binary_frame(self, data: np.ndarray) -> None:
        segment = self._require_open_segment()
        # Store sample-major rows so appending frames is a single sequential write.
        np.asarray(data.T, dtype="<f8", order="C").tofile(segment.file)

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
        self._finish_compression_worker()
        self._drain_compression_results()
        self._closed = True
        self._write_manifest(completed=completed, error=error, force=True)

    def _open_segment(self, index: int) -> None:
        if self._start_unix_ns is None:
            self._start_unix_ns = int(self._time_fn())
        suffix = ".bin" if self.storage_format == "binary" else ".dat"
        path = self.output_dir / f"segment_{index:04d}{suffix}"
        start_unix_ns = int(self._time_fn())
        if self.storage_format == "binary":
            handle = path.open("wb")
        else:
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
        segment = self._require_open_segment()
        segment.file.flush()
        segment.file.close()
        segment_path = segment.path
        raw_bytes = segment_path.stat().st_size
        manifest_path = segment_path.name
        compressed = False
        compression_error = None
        if self.compress_closed_segments:
            self._queue_segment_compression(segment_path)
        self._segments.append(
            {
                "index": segment.index,
                "path": manifest_path,
                "raw_path": segment_path.name,
                "start_unix_ns": segment.start_unix_ns,
                "start_time_local": local_timestamp_from_unix_ns(segment.start_unix_ns),
                "start_time_utc": utc_timestamp_from_unix_ns(segment.start_unix_ns),
                "frames": segment.frames,
                "samples": segment.samples,
                "bytes": segment_path.stat().st_size,
                "raw_bytes": raw_bytes,
                "compressed": compressed,
                "compression": SEGMENT_COMPRESSION_FORMAT if compressed else None,
                "compression_error": compression_error,
                "storage_format": self.storage_format,
                "dtype": "float64",
                "layout": "sample_major",
            }
        )
        self._segment = None
        self._drain_compression_results()

    def _start_compression_worker(self) -> None:
        if self._compression_thread is not None:
            return
        self._compression_queue = queue.Queue()
        self._compression_results = queue.Queue()
        self._compression_thread = threading.Thread(
            target=self._compression_worker_main,
            name="PythonVNARecordingCompressor",
            daemon=True,
        )
        self._compression_thread.start()

    def _queue_segment_compression(self, segment_path: Path) -> None:
        if self._compression_queue is None:
            archive_path = self._compress_segment(segment_path)
            if self._compression_results is not None:
                self._compression_results.put(
                    _CompressionResult(
                        segment_path=segment_path,
                        archive_path=archive_path,
                        raw_bytes=0,
                        compressed_bytes=archive_path.stat().st_size,
                    )
                )
            return
        self._compression_queue.put(segment_path)

    def _compression_worker_main(self) -> None:
        assert self._compression_queue is not None
        assert self._compression_results is not None
        while True:
            segment_path = self._compression_queue.get()
            if segment_path is None:
                self._compression_queue.task_done()
                break
            raw_bytes = 0
            try:
                raw_bytes = segment_path.stat().st_size
                archive_path = self._compress_segment(segment_path)
            except Exception as exc:
                self._compression_results.put(
                    _CompressionResult(
                        segment_path=segment_path,
                        raw_bytes=raw_bytes,
                        error=str(exc),
                    )
                )
            else:
                self._compression_results.put(
                    _CompressionResult(
                        segment_path=segment_path,
                        archive_path=archive_path,
                        raw_bytes=raw_bytes,
                        compressed_bytes=archive_path.stat().st_size,
                    )
                )
            finally:
                self._compression_queue.task_done()

    def _finish_compression_worker(self) -> None:
        if self._compression_queue is not None:
            self._compression_queue.put(None)
            self._compression_queue.join()
        if self._compression_thread is not None:
            self._compression_thread.join(timeout=30.0)
            self._compression_thread = None
        self._compression_queue = None

    def _drain_compression_results(self) -> None:
        if self._compression_results is None:
            return
        while True:
            try:
                result = self._compression_results.get_nowait()
            except queue.Empty:
                break
            self._apply_compression_result(result)

    def _apply_compression_result(self, result: _CompressionResult) -> None:
        raw_name = result.segment_path.name
        for segment in self._segments:
            if segment.get("raw_path") != raw_name and segment.get("path") != raw_name:
                continue
            if result.archive_path is not None and result.error is None:
                segment["path"] = result.archive_path.name
                segment["bytes"] = int(result.compressed_bytes or result.archive_path.stat().st_size)
                segment["raw_bytes"] = int(result.raw_bytes or segment.get("raw_bytes") or 0)
                segment["compressed"] = True
                segment["compression"] = SEGMENT_COMPRESSION_FORMAT
                segment["compression_error"] = None
            else:
                segment["path"] = raw_name
                segment["bytes"] = int(result.raw_bytes or segment.get("raw_bytes") or segment.get("bytes") or 0)
                segment["compressed"] = False
                segment["compression"] = None
                segment["compression_error"] = result.error
            break

    def _segment_reached_size_limit(self) -> bool:
        if self.max_segment_bytes is None or self.max_segment_bytes <= 0:
            return False
        if self._segment is None or self._segment.file is None:
            return False
        try:
            position = self._segment.file.tell()
        except (OSError, ValueError):
            try:
                position = self._segment.path.stat().st_size
            except OSError:
                return False
        return int(position) >= int(self.max_segment_bytes)

    def _compress_segment(self, segment_path: Path) -> Path:
        archive_path = segment_path.with_suffix(".zip")
        with NamedTemporaryFile(
            "wb",
            delete=False,
            dir=str(segment_path.parent),
            prefix=f".{segment_path.stem}_",
            suffix=".zip.tmp",
        ) as tmp_handle:
            tmp_path = Path(tmp_handle.name)
        try:
            with zipfile.ZipFile(
                tmp_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.write(segment_path, arcname=segment_path.name)
            tmp_path.replace(archive_path)
            segment_path.unlink()
            return archive_path
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

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
                "storage_format": self.storage_format,
                "dtype": "float64" if self.storage_format == "binary" else "float64_text",
                "layout": "sample_major" if self.storage_format == "binary" else "text_table",
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
            "session": asdict(self.session),
            "total_frames": self._total_frames,
            "total_samples": self._total_samples,
            "segment_seconds": self.segment_seconds,
            "max_segment_bytes": self.max_segment_bytes,
            "segment_compression": SEGMENT_COMPRESSION_FORMAT if self.compress_closed_segments else None,
            "storage_format": self.storage_format,
            "binary_format": BINARY_DAT_FORMAT if self.storage_format == "binary" else None,
            "binary_format_version": BINARY_DAT_VERSION if self.storage_format == "binary" else None,
            "binary_dtype": "float64" if self.storage_format == "binary" else None,
            "binary_layout": "sample_major" if self.storage_format == "binary" else None,
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


def read_dat_table_info(path: str | Path) -> tuple[dict[str, Any], list[str], int]:
    return _read_text_header_with_data_start(path)


def read_dat_numeric_columns(
    path: str | Path,
    usecols: list[int] | tuple[int, ...],
    *,
    data_start_line: int | None = None,
) -> np.ndarray:
    """Read selected numeric columns from a readable continuous DAT/ZIP segment."""
    skip_rows = max(0, int(data_start_line or 0))
    try:
        with _open_dat_text(path) as handle:
            raw = np.loadtxt(
                handle,
                comments="#",
                delimiter="\t",
                skiprows=skip_rows,
                usecols=list(usecols),
                dtype=float,
                ndmin=2,
            )
    except ValueError:
        with _open_dat_text(path) as handle:
            raw = np.genfromtxt(
                handle,
                comments="#",
                delimiter="\t",
                skip_header=skip_rows,
                usecols=list(usecols),
                dtype=float,
                invalid_raise=False,
            )
    if raw.size == 0:
        return np.empty((0, len(usecols)), dtype=float)
    raw = np.asarray(raw, dtype=float)
    if raw.ndim == 1:
        raw = raw.reshape((1, -1))
    return raw


def read_dat_sampled_numeric_columns(
    path: str | Path,
    usecols: list[int] | tuple[int, ...],
    *,
    start_row: int = 0,
    stop_row: int | None = None,
    global_offset: int = 0,
    stride_origin: int = 0,
    stride: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Read sparse rows from a continuous DAT/ZIP without converting skipped rows."""
    selected_rows: list[int] = []
    values: list[list[float]] = []
    cols = [int(col) for col in usecols]
    if not cols:
        return np.array([], dtype=np.int64), np.empty((0, 0), dtype=float)
    start = max(0, int(start_row))
    stop = None if stop_row is None else max(start, int(stop_row))
    step = max(1, int(stride))
    origin = int(stride_origin)
    offset = int(global_offset)
    max_col = max(cols)
    data_row = -1
    with _open_dat_text(path) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#") or line.startswith("time_s\t"):
                continue
            data_row += 1
            if data_row < start:
                continue
            if stop is not None and data_row >= stop:
                break
            global_row = offset + data_row
            if step > 1 and ((global_row - origin) % step) != 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max_col:
                continue
            try:
                values.append([float(parts[col]) for col in cols])
            except ValueError:
                continue
            selected_rows.append(data_row)
    if not values:
        return np.array([], dtype=np.int64), np.empty((0, len(cols)), dtype=float)
    return np.asarray(selected_rows, dtype=np.int64), np.asarray(values, dtype=float)


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

    with _open_dat_text(path) as handle:
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
    header, columns, _data_start_line = _read_text_header_with_data_start(path)
    return header, columns


def _read_text_header_with_data_start(path: str | Path) -> tuple[dict[str, Any], list[str], int]:
    parsed_header: dict[str, Any] = {}
    columns: list[str] | None = None
    data_start_line = 0
    with _open_dat_text(path) as handle:
        first_line = handle.readline()
        if not first_line.startswith("# Python VNA continuous DAT"):
            raise ValueError("Not a readable Python VNA continuous DAT file.")
        for line_number, line in enumerate(handle, start=2):
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
            data_start_line = line_number
            break
    if columns is None:
        columns = list(parsed_header.get("columns", []))
    if not parsed_header:
        raise ValueError("Python VNA DAT header is missing.")
    if "columns" not in parsed_header:
        parsed_header["columns"] = columns
    return parsed_header, columns, data_start_line


def _open_dat_text(path: str | Path):
    source = Path(path)
    if source.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(source, mode="r")
        dat_names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(".dat")
        ]
        if not dat_names:
            archive.close()
            raise ValueError("Compressed recording segment does not contain a DAT file.")
        raw_handle = archive.open(dat_names[0], mode="r")
        import io

        text_handle = io.TextIOWrapper(raw_handle, encoding="utf-8-sig", newline="")

        class _ZipTextContext:
            def __enter__(self):
                return text_handle

            def __exit__(self, exc_type, exc, tb):
                text_handle.close()
                archive.close()
                return False

        return _ZipTextContext()
    return source.open("r", encoding="utf-8-sig", newline="")
