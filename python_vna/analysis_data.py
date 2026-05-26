from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from python_vna.continuous_recording import iter_dat_frames, read_dat_header, read_dat_table_info
from python_vna.measurement_filter import filter_measurement_to_enabled_channels
from python_vna.models import MeasurementSet, SavedSession
from python_vna.storage import load_legacy_vna


@dataclass(slots=True)
class AnalysisSeries:
    dataset_id: int
    channel_index: int
    channel_key: str
    display_name: str
    unit: str = ""
    scale: float = 1.0

    @property
    def id(self) -> str:
        return f"{self.dataset_id}:{self.channel_key}"


@dataclass(slots=True)
class ContinuousSegmentInfo:
    path: Path
    samples: int = 0
    frames: int = 0
    start_unix_ns: int | None = None


@dataclass(slots=True)
class AnalysisDataset:
    id: int
    path: Path
    name: str
    sample_rate: float
    series: list[AnalysisSeries] = field(default_factory=list)
    time_s: np.ndarray | None = None
    channels: dict[str, np.ndarray] = field(default_factory=dict)
    frequency_hz: np.ndarray | None = None
    autospectrum: dict[str, np.ndarray] = field(default_factory=dict)
    frf: dict[str, np.ndarray] = field(default_factory=dict)
    coherence: dict[str, np.ndarray] = field(default_factory=dict)
    rbw_hz: float = 0.0
    wincor: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    continuous_segments: list[ContinuousSegmentInfo] = field(default_factory=list)

    @property
    def is_continuous(self) -> bool:
        return bool(self.continuous_segments)

    @property
    def channel_keys(self) -> list[str]:
        return [series.channel_key for series in self.series]

    def channel_unit(self, channel_key: str) -> str:
        for series in self.series:
            if series.channel_key == channel_key:
                return series.unit
        return ""

    def channel_scale(self, channel_key: str) -> float:
        for series in self.series:
            if series.channel_key == channel_key:
                return float(series.scale)
        return 1.0

    def load_time_series(
        self,
        channel_key: str,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        max_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        if self.is_continuous:
            return _load_continuous_channel(
                self,
                channel_key,
                start_s=start_s,
                end_s=end_s,
                max_points=max_points,
            )
        if channel_key not in self.channels:
            return np.array([], dtype=float), np.array([], dtype=float)
        y = np.asarray(self.channels[channel_key], dtype=float).ravel()
        if self.time_s is None or np.asarray(self.time_s).size == 0:
            t = np.arange(y.size, dtype=float) / max(float(self.sample_rate), 1e-20)
        else:
            t = np.asarray(self.time_s, dtype=float).ravel()
        count = min(t.size, y.size)
        t = t[:count]
        y = y[:count]
        mask = np.isfinite(t) & np.isfinite(y)
        if start_s is not None and np.isfinite(start_s):
            mask &= t >= float(start_s)
        if end_s is not None and np.isfinite(end_s):
            mask &= t <= float(end_s)
        t = t[mask]
        y = y[mask]
        return _downsample_xy(t, y, max_points)


def load_analysis_path(path: str | Path, *, fs_hint: float = 1000.0, dataset_id: int = 1) -> AnalysisDataset:
    source = Path(path)
    if source.is_dir():
        manifest = source / "manifest.json"
        if manifest.exists():
            return load_continuous_recording(manifest, dataset_id=dataset_id)
        raise ValueError("Folder does not contain a Python VNA manifest.json.")
    if source.name.lower() == "manifest.json":
        return load_continuous_recording(source, dataset_id=dataset_id)
    suffix = source.suffix.lower()
    if suffix in {".vna", ".mat"}:
        return load_legacy_analysis_dataset(source, dataset_id=dataset_id)
    if suffix == ".xlsx":
        raise ValueError("XLSX is not supported in Analysis Viewer to keep the package small.")
    if suffix == ".dat":
        try:
            header = read_dat_header(source)
            if header.get("format") == "python_vna_continuous_text_dat":
                return load_continuous_segment(source, header=header, dataset_id=dataset_id)
        except Exception:
            pass
    if suffix in {".txt", ".csv", ".dat"}:
        return load_numeric_text_dataset(source, fs_hint=fs_hint, dataset_id=dataset_id)
    raise ValueError(f"Unsupported analysis file type: {source.suffix}")


def load_legacy_analysis_dataset(path: str | Path, *, dataset_id: int = 1) -> AnalysisDataset:
    saved = load_legacy_vna(path)
    if saved.measurement is None:
        raise ValueError("VNA file does not contain measurement data.")
    return dataset_from_saved_session(saved, dataset_id=dataset_id)


def dataset_from_measurement(
    measurement: MeasurementSet,
    *,
    session_config=None,
    dataset_id: int = 1,
    name: str = "Current Measurement",
) -> AnalysisDataset:
    from python_vna.storage import default_session_config

    saved = SavedSession(
        config=session_config or default_session_config(),
        measurement=filter_measurement_to_enabled_channels(
            measurement,
            session_config or default_session_config(),
        ),
        source_path=Path(name),
    )
    dataset = dataset_from_saved_session(saved, dataset_id=dataset_id)
    dataset.name = name
    dataset.path = Path(name)
    dataset.metadata["source"] = "current_measurement"
    return dataset


def dataset_from_saved_session(saved: SavedSession, *, dataset_id: int = 1) -> AnalysisDataset:
    measurement = saved.measurement
    if measurement is None:
        raise ValueError("Saved session does not contain measurement data.")
    path = saved.source_path or Path(saved.config.title)
    time_channels = dict(measurement.time_data.get("channels", {}))
    time_s = np.asarray(measurement.time_data.get("t", np.array([], dtype=float)), dtype=float).ravel()
    frequency = np.asarray(measurement.spectra.get("f", np.array([], dtype=float)), dtype=float).ravel()
    legacy_channels = measurement.metadata.get("legacy_channels", {})
    series: list[AnalysisSeries] = []
    channel_keys = _ordered_time_channel_keys(time_channels, saved)
    for index, key in enumerate(channel_keys):
        channel = _channel_for_key(saved.config.ai_channels, key, fallback_index=index)
        label = str(getattr(channel, "label", "") or key)
        unit = str(getattr(channel, "engineering_unit", "") or "")
        scale = float(getattr(channel, "sensitivity", 1.0) or 1.0)
        if isinstance(legacy_channels, dict):
            legacy = legacy_channels.get(str(getattr(channel, "name", key)), {})
            if not legacy:
                legacy = legacy_channels.get(str(key), {})
            if isinstance(legacy, dict):
                unit = str(legacy.get("eu_string") or unit)
                scale = float(legacy.get("euscale_fac", scale) or scale)
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=index,
                channel_key=key,
                display_name=label,
                unit=unit,
                scale=scale,
            )
        )
    return AnalysisDataset(
        id=dataset_id,
        path=Path(path),
        name=Path(path).name,
        sample_rate=float(measurement.sample_rate),
        series=series,
        time_s=time_s,
        channels=time_channels,
        frequency_hz=frequency,
        autospectrum=dict(measurement.spectra.get("autospectrum", {})),
        frf=dict(measurement.frf),
        coherence=dict(measurement.coherence),
        rbw_hz=float(measurement.metadata.get("rbw_hz", _infer_rbw(frequency))),
        wincor=float(
            measurement.metadata.get(
                "legacy_runtime_wincor",
                measurement.metadata.get("legacy_wincor", 1.0),
            )
        ),
        metadata=dict(measurement.metadata),
    )


def load_continuous_recording(path: str | Path, *, dataset_id: int = 1) -> AnalysisDataset:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    segments: list[ContinuousSegmentInfo] = []
    for raw in manifest.get("segments", []):
        if not isinstance(raw, dict):
            continue
        segment_path = base / str(raw.get("path", ""))
        if not segment_path.exists():
            continue
        segments.append(
            ContinuousSegmentInfo(
                path=segment_path,
                samples=int(raw.get("samples", 0) or 0),
                frames=int(raw.get("frames", 0) or 0),
                start_unix_ns=int(raw["start_unix_ns"]) if raw.get("start_unix_ns") is not None else None,
            )
        )
    active = manifest.get("active_segment")
    if isinstance(active, dict):
        active_path = base / str(active.get("path", ""))
        if active_path.exists() and all(segment.path != active_path for segment in segments):
            segments.append(
                ContinuousSegmentInfo(
                    path=active_path,
                    samples=int(active.get("samples", 0) or 0),
                    frames=int(active.get("frames", 0) or 0),
                    start_unix_ns=int(active["start_unix_ns"]) if active.get("start_unix_ns") is not None else None,
                )
            )
    if not segments:
        raise ValueError("Continuous recording manifest has no readable segment_*.dat files.")
    first_header = read_dat_header(segments[0].path)
    return _continuous_dataset_from_header(
        manifest_path,
        first_header,
        segments,
        manifest=manifest,
        dataset_id=dataset_id,
    )


def load_continuous_segment(
    path: str | Path,
    *,
    header: dict[str, Any] | None = None,
    dataset_id: int = 1,
) -> AnalysisDataset:
    segment_path = Path(path)
    header = header or read_dat_header(segment_path)
    segment = ContinuousSegmentInfo(
        path=segment_path,
        samples=0,
        frames=0,
        start_unix_ns=int(header["start_unix_ns"]) if header.get("start_unix_ns") is not None else None,
    )
    return _continuous_dataset_from_header(
        segment_path,
        header,
        [segment],
        manifest={},
        dataset_id=dataset_id,
    )


def load_numeric_text_dataset(path: str | Path, *, fs_hint: float = 1000.0, dataset_id: int = 1) -> AnalysisDataset:
    source = Path(path)
    delimiter = "," if source.suffix.lower() == ".csv" else None
    data = np.genfromtxt(
        source,
        comments="#",
        delimiter=delimiter,
        dtype=float,
        invalid_raise=False,
    )
    if data.size == 0:
        raise ValueError("Text file does not contain numeric data.")
    data = np.asarray(data, dtype=float)
    if data.ndim == 0:
        data = data.reshape((1, 1))
    if data.ndim == 1:
        values = data.ravel()
        fs = _validate_fs_hint(fs_hint)
        time_s = np.arange(values.size, dtype=float) / fs
        channel_matrix = values.reshape((-1, 1))
    else:
        data = np.atleast_2d(data)
        data = data[np.any(np.isfinite(data), axis=1)]
        if data.shape[1] >= 2 and _is_time_like(data[:, 0]):
            time_s = data[:, 0].astype(float)
            channel_matrix = data[:, 1:]
            fs = float(1.0 / np.mean(np.diff(time_s)))
        else:
            fs = _validate_fs_hint(fs_hint)
            channel_matrix = data
            time_s = np.arange(channel_matrix.shape[0], dtype=float) / fs
    series: list[AnalysisSeries] = []
    channels: dict[str, np.ndarray] = {}
    for index in range(channel_matrix.shape[1]):
        key = f"Ch {index + 1}"
        channels[key] = np.asarray(channel_matrix[:, index], dtype=float).ravel()
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=index,
                channel_key=key,
                display_name=key,
                unit="",
                scale=1.0,
            )
        )
    return AnalysisDataset(
        id=dataset_id,
        path=source,
        name=source.name,
        sample_rate=fs,
        series=series,
        time_s=np.asarray(time_s, dtype=float).ravel(),
        channels=channels,
        metadata={"source": "numeric_text"},
    )


def _continuous_dataset_from_header(
    path: Path,
    header: dict[str, Any],
    segments: list[ContinuousSegmentInfo],
    *,
    manifest: dict[str, Any],
    dataset_id: int,
) -> AnalysisDataset:
    channel_names = [str(name) for name in header.get("channel_names", [])]
    session = header.get("session", {})
    ai_channels = session.get("ai_channels", []) if isinstance(session, dict) else []
    series: list[AnalysisSeries] = []
    for index, name in enumerate(channel_names):
        channel_cfg = ai_channels[index] if index < len(ai_channels) and isinstance(ai_channels[index], dict) else {}
        label = str(channel_cfg.get("label") or name)
        unit = str(channel_cfg.get("engineering_unit") or "")
        scale = float(channel_cfg.get("sensitivity", 1.0) or 1.0)
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=index,
                channel_key=name,
                display_name=label,
                unit=unit,
                scale=scale,
            )
        )
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.parent.name if path.name == "manifest.json" else path.name,
        sample_rate=float(header.get("sample_rate", manifest.get("sample_rate", 1000.0))),
        series=series,
        metadata={
            "source": "continuous_recording",
            "header": header,
            "manifest": manifest,
            "total_samples": manifest.get("total_samples"),
            "frame_size": header.get("frame_size", manifest.get("frame_size")),
            "start_time_local": manifest.get("start_time_local", header.get("start_time_local")),
        },
        continuous_segments=segments,
    )


def _load_continuous_channel(
    dataset: AnalysisDataset,
    channel_key: str,
    *,
    start_s: float | None,
    end_s: float | None,
    max_points: int | None,
) -> tuple[np.ndarray, np.ndarray]:
    time_chunks: list[np.ndarray] = []
    data_chunks: list[np.ndarray] = []
    sample_offset = 0
    channel_index: int | None = None
    total_samples = _continuous_total_samples(dataset)
    start_sample = max(0, int(np.floor(float(start_s) * dataset.sample_rate))) if start_s is not None and np.isfinite(start_s) else 0
    if end_s is not None and np.isfinite(end_s):
        end_sample = max(start_sample, int(np.ceil(float(end_s) * dataset.sample_rate)))
    else:
        end_sample = total_samples if total_samples > 0 else None
    stride = 1
    if max_points is not None and max_points > 0 and end_sample is not None:
        visible_samples = max(0, end_sample - start_sample)
        if visible_samples > max_points:
            stride = max(1, int(np.ceil(visible_samples / int(max_points))))
    for segment in dataset.continuous_segments:
        for _header, frame in iter_dat_frames(segment.path):
            names = [str(name) for name in frame.get("channel_names", [])]
            if channel_index is None:
                try:
                    channel_index = names.index(channel_key)
                except ValueError:
                    return np.array([], dtype=float), np.array([], dtype=float)
            data = np.asarray(frame.get("data", np.empty((0, 0))), dtype=float)
            if data.ndim != 2 or channel_index >= data.shape[0]:
                continue
            sample_count = int(data.shape[1])
            global_samples = np.arange(sample_count, dtype=np.int64) + sample_offset
            frame_time = global_samples.astype(float) / max(dataset.sample_rate, 1e-20)
            y = data[channel_index]
            sample_offset += sample_count
            mask = np.ones(sample_count, dtype=bool)
            if start_s is not None and np.isfinite(start_s):
                mask &= frame_time >= float(start_s)
            if end_s is not None and np.isfinite(end_s):
                mask &= frame_time <= float(end_s)
            if stride > 1:
                mask &= ((global_samples - start_sample) % stride) == 0
            if not np.any(mask):
                if end_s is not None and np.isfinite(end_s) and frame_time.size and frame_time[0] > float(end_s):
                    break
                continue
            time_chunks.append(frame_time[mask])
            data_chunks.append(y[mask])
        if end_s is not None and np.isfinite(end_s) and time_chunks and time_chunks[-1].size and time_chunks[-1][-1] >= float(end_s):
            break
    if not time_chunks:
        return np.array([], dtype=float), np.array([], dtype=float)
    t = np.concatenate(time_chunks)
    y = np.concatenate(data_chunks)
    return _downsample_xy(t, y, max_points)


def load_continuous_channels(
    dataset: AnalysisDataset,
    channel_keys: list[str],
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    max_points: int | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    keys = [str(key) for key in channel_keys if str(key)]
    if not keys:
        return np.array([], dtype=float), {}
    if not dataset.is_continuous:
        loaded: dict[str, np.ndarray] = {}
        time_s: np.ndarray | None = None
        for key in keys:
            t, y = dataset.load_time_series(key, start_s=start_s, end_s=end_s, max_points=max_points)
            if time_s is None or t.size > time_s.size:
                time_s = t
            loaded[key] = y
        return (time_s if time_s is not None else np.array([], dtype=float)), loaded

    total_samples = _continuous_total_samples(dataset)
    start_sample = max(0, int(np.floor(float(start_s) * dataset.sample_rate))) if start_s is not None and np.isfinite(start_s) else 0
    if end_s is not None and np.isfinite(end_s):
        end_sample = max(start_sample, int(np.ceil(float(end_s) * dataset.sample_rate)))
    else:
        end_sample = total_samples if total_samples > 0 else None
    stride = 1
    if max_points is not None and max_points > 0 and end_sample is not None:
        visible_samples = max(0, end_sample - start_sample)
        if visible_samples > max_points:
            stride = max(1, int(np.ceil(visible_samples / int(max_points))))

    time_chunks: list[np.ndarray] = []
    data_chunks: dict[str, list[np.ndarray]] = {key: [] for key in keys}
    sample_offset = 0
    for segment in dataset.continuous_segments:
        try:
            header, columns, data_start_line = read_dat_table_info(segment.path)
            has_local_time = len(columns) > 1 and columns[1] == "local_time"
            data_start_col = 5 if has_local_time else 4
            segment_channel_names = [str(name) for name in columns[data_start_col:]]
            selected_indices = [segment_channel_names.index(key) for key in keys]
        except (OSError, ValueError):
            segment_samples = max(0, int(segment.samples))
            sample_offset += segment_samples
            continue

        raw = np.genfromtxt(
            segment.path,
            comments="#",
            delimiter="\t",
            skip_header=max(0, int(data_start_line)),
            usecols=[0, *[data_start_col + index for index in selected_indices]],
            dtype=float,
            invalid_raise=False,
        )
        if raw.size == 0:
            sample_offset += max(0, int(segment.samples))
            continue
        raw = np.asarray(raw, dtype=float)
        if raw.ndim == 1:
            raw = raw.reshape((1, -1))
        sample_count = raw.shape[0]
        global_samples = np.arange(sample_count, dtype=np.int64) + sample_offset
        sample_offset += sample_count
        frame_time = global_samples.astype(float) / max(dataset.sample_rate, 1e-20)
        mask = np.ones(sample_count, dtype=bool)
        if start_s is not None and np.isfinite(start_s):
            mask &= frame_time >= float(start_s)
        if end_s is not None and np.isfinite(end_s):
            mask &= frame_time <= float(end_s)
        if stride > 1:
            mask &= ((global_samples - start_sample) % stride) == 0
        if not np.any(mask):
            if end_s is not None and np.isfinite(end_s) and frame_time.size and frame_time[0] > float(end_s):
                break
            continue
        time_chunks.append(frame_time[mask])
        for output_index, key in enumerate(keys):
            data_chunks[key].append(raw[:, output_index + 1][mask])
        if end_s is not None and np.isfinite(end_s) and time_chunks[-1].size and time_chunks[-1][-1] >= float(end_s):
            break

    if not time_chunks:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}
    time_s = np.concatenate(time_chunks)
    channels = {
        key: np.concatenate(chunks) if chunks else np.array([], dtype=float)
        for key, chunks in data_chunks.items()
    }
    return time_s, channels


def _continuous_total_samples(dataset: AnalysisDataset) -> int:
    value = dataset.metadata.get("total_samples")
    try:
        total = int(value)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        return total
    return sum(max(0, int(segment.samples)) for segment in dataset.continuous_segments)


def _downsample_xy(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int | None,
) -> tuple[np.ndarray, np.ndarray]:
    if max_points is None or max_points <= 0 or x.size <= max_points:
        return x, y
    step = max(1, int(np.ceil(x.size / int(max_points))))
    return x[::step], y[::step]


def _ordered_time_channel_keys(
    channels: dict[str, np.ndarray],
    saved: SavedSession,
) -> list[str]:
    keys: list[str] = []
    for index, channel in enumerate(saved.config.ai_channels):
        candidates = [
            str(getattr(channel, "label", "") or ""),
            str(getattr(channel, "name", f"ai{index}") or f"ai{index}"),
            f"ai{index}",
            f"Ch {index + 1}",
            f"Channel {index + 1}",
        ]
        for candidate in candidates:
            if candidate and candidate in channels and candidate not in keys:
                keys.append(candidate)
                break
    for key in channels:
        if key not in keys:
            keys.append(key)
    return keys


def _channel_for_key(ai_channels: list[object], key: str, *, fallback_index: int):
    for index, channel in enumerate(ai_channels):
        candidates = {
            str(getattr(channel, "label", "") or ""),
            str(getattr(channel, "name", f"ai{index}") or f"ai{index}"),
            str(getattr(channel, "physical_name", "") or ""),
            f"ai{index}",
            f"Ch {index + 1}",
            f"Channel {index + 1}",
        }
        expanded: set[str] = set()
        for candidate in candidates:
            if candidate:
                expanded.add(candidate)
                if "/" in candidate:
                    expanded.add(candidate.rsplit("/", 1)[-1])
        if str(key) in expanded:
            return channel
    if fallback_index < len(ai_channels):
        return ai_channels[fallback_index]
    return None


def _infer_rbw(frequencies: np.ndarray) -> float:
    f = np.asarray(frequencies, dtype=float).ravel()
    if f.size < 2:
        return 0.0
    diffs = np.diff(f)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return 0.0
    return float(np.median(diffs))


def _is_time_like(values: np.ndarray) -> bool:
    column = np.asarray(values, dtype=float).ravel()
    column = column[np.isfinite(column)]
    if column.size < 3:
        return False
    diffs = np.diff(column)
    positive = diffs[np.isfinite(diffs)]
    if positive.size < 2 or np.any(positive <= 0.0):
        return False
    mean = float(np.mean(positive))
    if mean <= 0.0:
        return False
    return float(np.std(positive)) <= max(mean * 0.05, 1e-12)


def _validate_fs_hint(fs_hint: float) -> float:
    fs = float(fs_hint)
    if not np.isfinite(fs) or fs <= 0.0:
        raise ValueError("Fs hint must be a positive number for files without a time column.")
    return fs
