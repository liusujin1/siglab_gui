from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

FLOOR_RESPONSE_EU_WINDOW_CORRECTION = 0.9376
STANDARD_GRAVITY_M_S2 = 9.8

from python_vna.continuous_recording import (
    iter_dat_frames,
    read_dat_header,
    read_dat_numeric_columns,
    read_dat_sampled_numeric_columns,
    read_dat_table_info,
)
from python_vna.condition_notes import (
    README_NAME,
    condition_for_number,
    condition_number_from_path,
    read_condition_text_file,
)
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
    channel_names: list[str] = field(default_factory=list)
    storage_format: str = "text"


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
    cross_spectra: dict[str, np.ndarray] = field(default_factory=dict)
    rbw_hz: float = 0.0
    wincor: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    continuous_segments: list[ContinuousSegmentInfo] = field(default_factory=list)
    readme_path: Path | None = None
    readme_text: str = ""
    condition_number: str | None = None
    condition_text: str = ""
    notes_fallback: str = ""

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


def load_analysis_path(
    path: str | Path,
    *,
    fs_hint: float = 1000.0,
    dataset_id: int = 1,
    import_kind: str | None = None,
) -> AnalysisDataset:
    source = Path(path)
    requested_kind = _normalize_import_kind(import_kind)
    if source.is_dir():
        manifest = source / "manifest.json"
        if manifest.exists():
            return _attach_readme_metadata(load_continuous_recording(manifest, dataset_id=dataset_id))
        raise ValueError("Folder does not contain a Python VNA manifest.json.")
    if source.name.lower() == "manifest.json":
        return _attach_readme_metadata(load_continuous_recording(source, dataset_id=dataset_id))
    suffix = source.suffix.lower()
    if suffix == ".vna":
        return _attach_readme_metadata(load_legacy_analysis_dataset(source, dataset_id=dataset_id))
    if suffix == ".mat":
        if requested_kind in {"psd", "transfer"}:
            return _finalize_import_kind(
                _attach_readme_metadata(
                    load_frequency_mat_dataset(source, import_kind=requested_kind, dataset_id=dataset_id)
                ),
                requested_kind,
            )
        return _finalize_import_kind(
            _attach_readme_metadata(load_simulink_mat_dataset(source, fs_hint=fs_hint, dataset_id=dataset_id)),
            requested_kind,
        )
    if suffix == ".xlsx":
        raise ValueError("XLSX is not supported in Analysis Viewer to keep the package small.")
    if suffix in {".dat", ".zip"}:
        try:
            header = read_dat_header(source)
            if header.get("format") == "python_vna_continuous_text_dat":
                return _attach_readme_metadata(load_continuous_segment(source, header=header, dataset_id=dataset_id))
        except Exception:
            if suffix == ".zip":
                raise
            pass
    if suffix in {".txt", ".csv", ".dat"}:
        return _finalize_import_kind(
            _attach_readme_metadata(
                load_numeric_text_dataset(
                    source,
                    fs_hint=fs_hint,
                    dataset_id=dataset_id,
                    import_kind=requested_kind,
                )
            ),
            requested_kind,
        )
    raise ValueError(f"Unsupported analysis file type: {source.suffix}")


def _normalize_import_kind(import_kind: str | None) -> str | None:
    if import_kind is None:
        return None
    text = str(import_kind or "").strip().lower()
    if not text or text in {"auto", "自动", "自动识别"}:
        return None
    aliases = {
        "time": "time",
        "time-domain": "time",
        "timedomain": "time",
        "时域": "time",
        "时域数据": "time",
        "psd": "psd",
        "autospectrum": "psd",
        "功率谱": "psd",
        "功率谱密度": "psd",
        "transfer": "transfer",
        "trans": "transfer",
        "frf": "transfer",
        "传递率": "transfer",
        "传函": "transfer",
        "传递率数据": "transfer",
    }
    if text in aliases:
        return aliases[text]
    raise ValueError(f"Unsupported import kind: {import_kind}")


def _finalize_import_kind(dataset: AnalysisDataset, import_kind: str | None) -> AnalysisDataset:
    if import_kind:
        dataset.metadata["import_kind"] = import_kind
    return dataset


def load_legacy_analysis_dataset(path: str | Path, *, dataset_id: int = 1) -> AnalysisDataset:
    saved = load_legacy_vna(path)
    if saved.measurement is None:
        raise ValueError("VNA file does not contain measurement data.")
    return dataset_from_saved_session(saved, dataset_id=dataset_id)


def load_simulink_mat_dataset(
    path: str | Path,
    *,
    fs_hint: float = 1000.0,
    dataset_id: int = 1,
) -> AnalysisDataset:
    from scipy.io import loadmat

    source = Path(path)
    try:
        mat = loadmat(source, squeeze_me=True, struct_as_record=False)
    except NotImplementedError:
        records = _load_simulink_hdf5_records(source)
    else:
        payload = {key: value for key, value in mat.items() if not str(key).startswith("__")}
        records = _simulink_records_from_payload(payload)
    return _simulink_dataset_from_records(source, records, fs_hint=fs_hint, dataset_id=dataset_id)


def load_frequency_mat_dataset(
    path: str | Path,
    *,
    import_kind: str,
    dataset_id: int = 1,
) -> AnalysisDataset:
    from scipy.io import loadmat

    source = Path(path)
    kind = _normalize_import_kind(import_kind)
    if kind not in {"psd", "transfer"}:
        raise ValueError("MAT frequency import kind must be PSD or transfer.")
    try:
        mat = loadmat(source, squeeze_me=True, struct_as_record=False)
    except NotImplementedError:
        pairs = _frequency_pairs_from_hdf5(source)
    else:
        payload = {key: value for key, value in mat.items() if not str(key).startswith("__")}
        pairs = _frequency_pairs_from_named_arrays(
            {
                str(key): arr
                for key, value in payload.items()
                if (arr := _frequency_numeric_array_or_none(value)) is not None
            }
        )
    if not pairs:
        raise ValueError("MAT file does not contain a usable frequency table for PSD/transfer data.")
    metadata = {"import_kind": kind, "source": "mat_frequency_table"}
    if kind == "psd":
        return _plot_export_psd_dataset(source, pairs, metadata=metadata, dataset_id=dataset_id)
    return _plot_export_transfer_dataset(source, pairs, metadata=metadata, dataset_id=dataset_id)


def _simulink_records_from_payload(payload: dict[str, object]) -> list[tuple[str, np.ndarray, np.ndarray]]:
    records: list[tuple[str, np.ndarray, np.ndarray]] = []
    global_time = _simulink_global_time(payload)
    for key, value in payload.items():
        if _is_simulink_time_key(key):
            continue
        _collect_simulink_time_series(str(key), value, global_time, records, set())
    return records


def _simulink_dataset_from_records(
    source: Path,
    records: list[tuple[str, np.ndarray, np.ndarray]],
    *,
    fs_hint: float,
    dataset_id: int,
) -> AnalysisDataset:
    if not records:
        raise ValueError("No supported Simulink time-series data found.")
    master_time = _simulink_master_time(records, fs_hint)
    if master_time.size < 2:
        raise ValueError("Simulink MAT data does not contain a usable time axis.")
    sample_rate = _sample_rate_from_time_vector(master_time, fs_hint)
    channels: dict[str, np.ndarray] = {}
    series: list[AnalysisSeries] = []
    used_names: set[str] = set()
    for _raw_name, time_s, values in records:
        t, y = _finite_aligned_time_values(time_s, values)
        if t.size < 2 or y.size < 2:
            continue
        if t.size == master_time.size and np.allclose(t, master_time, rtol=1e-8, atol=1e-12):
            aligned = y
        else:
            order = np.argsort(t)
            t_sorted = t[order]
            y_sorted = y[order]
            unique_time, unique_indices = np.unique(t_sorted, return_index=True)
            if unique_time.size < 2:
                continue
            aligned = np.interp(master_time, unique_time, y_sorted[unique_indices])
        key = _unique_simulink_channel_name(_raw_name, used_names)
        channels[key] = np.asarray(aligned, dtype=float).ravel()
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=len(series),
                channel_key=key,
                display_name=key,
                unit="",
                scale=1.0,
            )
        )
    if not channels:
        raise ValueError("No numeric Simulink channels could be aligned to a common time axis.")
    return AnalysisDataset(
        id=dataset_id,
        path=source,
        name=source.name,
        sample_rate=sample_rate,
        series=series,
        time_s=np.asarray(master_time, dtype=float).ravel(),
        channels=channels,
        metadata={"source": "simulink_mat"},
    )



def _attach_readme_metadata(dataset: AnalysisDataset) -> AnalysisDataset:
    dataset.notes_fallback = str(dataset.notes_fallback or dataset.metadata.get("notes", "") or "")
    dataset.condition_number = condition_number_from_path(_dataset_condition_source_path(dataset))
    readme_path = _find_dataset_readme_path(dataset)
    if readme_path is not None:
        try:
            dataset.readme_text = read_condition_text_file(readme_path)
            dataset.readme_path = readme_path
        except OSError:
            dataset.readme_text = ""
            dataset.readme_path = None
    dataset.condition_text = condition_for_number(dataset.readme_text, dataset.condition_number) or dataset.notes_fallback
    return dataset


def _dataset_condition_source_path(dataset: AnalysisDataset) -> Path:
    path = dataset.path
    if dataset.is_continuous and path.name.lower() == "manifest.json":
        return path.parent
    if dataset.is_continuous and path.suffix.lower() in {".dat", ".zip"}:
        return path.parent
    return path


def _find_dataset_readme_path(dataset: AnalysisDataset) -> Path | None:
    candidates: list[Path] = []
    path = dataset.path
    if dataset.is_continuous and path.name.lower() == "manifest.json":
        candidates.extend([path.parent / README_NAME, path.parent.parent / README_NAME])
    elif dataset.is_continuous and path.is_dir():
        candidates.extend([path / README_NAME, path.parent / README_NAME])
    elif dataset.is_continuous and path.suffix.lower() == ".dat":
        candidates.extend([path.parent / README_NAME, path.parent.parent / README_NAME])
    else:
        candidates.append(path.parent / README_NAME)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

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
    metadata = dict(measurement.metadata)
    metadata.setdefault("notes", str(getattr(saved.config, "notes", "") or ""))
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
        cross_spectra=dict(measurement.cross_spectra),
        rbw_hz=float(measurement.metadata.get("rbw_hz", _infer_rbw(frequency))),
        wincor=float(
            measurement.metadata.get(
                "legacy_runtime_wincor",
                measurement.metadata.get("legacy_wincor", 1.0),
            )
        ),
        metadata=metadata,
        notes_fallback=str(getattr(saved.config, "notes", "") or ""),
    )


def load_continuous_recording(path: str | Path, *, dataset_id: int = 1) -> AnalysisDataset:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    segments: list[ContinuousSegmentInfo] = []
    manifest_channel_names = [str(name) for name in manifest.get("channel_names", [])]
    manifest_storage_format = _continuous_storage_format_from_manifest(manifest)
    for raw in manifest.get("segments", []):
        if not isinstance(raw, dict):
            continue
        segment_path = _resolve_continuous_segment_path(base, raw)
        if not segment_path.exists():
            continue
        segments.append(
            ContinuousSegmentInfo(
                path=segment_path,
                samples=int(raw.get("samples", 0) or 0),
                frames=int(raw.get("frames", 0) or 0),
                start_unix_ns=int(raw["start_unix_ns"]) if raw.get("start_unix_ns") is not None else None,
                channel_names=manifest_channel_names,
                storage_format=str(raw.get("storage_format", manifest_storage_format) or manifest_storage_format),
            )
        )
    active = manifest.get("active_segment")
    if isinstance(active, dict):
        active_path = _resolve_continuous_segment_path(base, active)
        if active_path.exists() and all(segment.path != active_path for segment in segments):
            segments.append(
                ContinuousSegmentInfo(
                    path=active_path,
                    samples=int(active.get("samples", 0) or 0),
                    frames=int(active.get("frames", 0) or 0),
                    start_unix_ns=int(active["start_unix_ns"]) if active.get("start_unix_ns") is not None else None,
                    channel_names=manifest_channel_names,
                    storage_format=str(active.get("storage_format", manifest_storage_format) or manifest_storage_format),
                )
            )
    if not segments:
        raise ValueError("Continuous recording manifest has no readable segment_*.dat files.")
    if str(segments[0].storage_format).lower() == "binary" or segments[0].path.suffix.lower() == ".bin":
        first_header = _binary_header_from_manifest(manifest)
    else:
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
        channel_names=[str(name) for name in header.get("channel_names", [])],
    )
    return _continuous_dataset_from_header(
        segment_path,
        header,
        [segment],
        manifest={},
        dataset_id=dataset_id,
    )


def _continuous_storage_format_from_manifest(manifest: dict[str, Any]) -> str:
    value = str(manifest.get("storage_format", "") or "").strip().lower()
    binary_format = str(manifest.get("binary_format", "") or "").strip().lower()
    if value in {"binary", "bin"} or "binary" in binary_format:
        return "binary"
    return "text"


def _binary_header_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": str(manifest.get("binary_format") or "python_vna_continuous_binary_float64"),
        "format_version": int(manifest.get("binary_format_version") or 1),
        "software_version": manifest.get("software_version"),
        "device_name": manifest.get("device_name"),
        "start_time_local": manifest.get("start_time_local"),
        "start_time_utc": manifest.get("start_time_utc"),
        "start_unix_ns": manifest.get("start_unix_ns"),
        "sample_rate": float(manifest.get("sample_rate", 1000.0)),
        "frame_size": int(manifest.get("frame_size", 0) or 0),
        "data_dtype": str(manifest.get("binary_dtype") or "float64"),
        "time_policy": "sample_index_over_sample_rate",
        "channel_names": [str(name) for name in manifest.get("channel_names", [])],
        "session": manifest.get("session", {}),
        "columns": [str(name) for name in manifest.get("channel_names", [])],
    }


def _resolve_continuous_segment_path(base: Path, raw: dict[str, Any]) -> Path:
    path_text = str(raw.get("path", "") or "")
    path = base / path_text
    if path.exists():
        return path
    raw_path_text = str(raw.get("raw_path", "") or "")
    raw_path = base / raw_path_text
    if raw_path.exists():
        return raw_path
    for candidate in (path, raw_path):
        if candidate.suffix.lower() == ".dat":
            zipped = candidate.with_suffix(".zip")
            if zipped.exists():
                return zipped
    return path


def load_numeric_text_dataset(
    path: str | Path,
    *,
    fs_hint: float = 1000.0,
    dataset_id: int = 1,
    import_kind: str | None = None,
) -> AnalysisDataset:
    source = Path(path)
    requested_kind = _normalize_import_kind(import_kind)
    if requested_kind in {None, "psd"}:
        floor_response = _load_floor_response_eu_ascii_dataset(source, dataset_id=dataset_id)
        if floor_response is not None:
            return floor_response

    if requested_kind in {"psd", "transfer"}:
        frequency_table = _load_forced_frequency_text_dataset(
            source,
            import_kind=requested_kind,
            dataset_id=dataset_id,
        )
        if frequency_table is not None:
            return frequency_table
        raise ValueError("Text file does not contain a usable frequency table for PSD/transfer data.")

    if requested_kind != "time":
        plot_export = _load_plot_export_dataset(source, fs_hint=fs_hint, dataset_id=dataset_id)
        if plot_export is not None:
            return plot_export

    header_table = _load_headered_time_table_dataset(source, fs_hint=fs_hint, dataset_id=dataset_id)
    if header_table is not None:
        return header_table

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


def _load_forced_frequency_text_dataset(
    path: Path,
    *,
    import_kind: str,
    dataset_id: int,
) -> AnalysisDataset | None:
    metadata: dict[str, str] = {"import_kind": import_kind}
    pairs: list[tuple[str, np.ndarray, np.ndarray]] = []
    parsed = _parse_plot_export_table(path)
    if parsed is not None:
        parsed_metadata, headers, data = parsed
        metadata.update(parsed_metadata)
        pairs = _curve_pairs_from_headers(headers, data)
        if not pairs:
            pairs = _curve_pairs_from_common_axis(headers, data)
    if not pairs:
        data = _read_numeric_text_matrix(path)
        if data is not None:
            pairs = _curve_pairs_from_common_axis([], data)
    if not pairs:
        return None
    if import_kind == "psd":
        return _plot_export_psd_dataset(path, pairs, metadata=metadata, dataset_id=dataset_id)
    return _plot_export_transfer_dataset(path, pairs, metadata=metadata, dataset_id=dataset_id)


def _load_floor_response_eu_ascii_dataset(path: Path, *, dataset_id: int) -> AnalysisDataset | None:
    try:
        text = _read_text_for_numeric_table(path)
    except OSError:
        return None
    lines = text.splitlines()
    if not _looks_like_floor_response_eu_ascii(lines):
        return None
    data = _floor_response_numeric_matrix(lines)
    if data is None or data.shape[1] < 2:
        return None
    frequency = np.asarray(data[:, 0], dtype=float).ravel()
    if not _is_frequency_like(frequency):
        return None
    raw_values = np.asarray(data[:, 1:], dtype=float)
    channel_count = raw_values.shape[1]
    names = _floor_response_channel_names(lines, channel_count)
    units = _floor_response_channel_units(lines, channel_count)
    labels = _floor_response_location_labels(lines, channel_count)

    series: list[AnalysisSeries] = []
    autospectrum: dict[str, np.ndarray] = {}
    used_names: set[str] = set()
    for index in range(channel_count):
        raw_name = names[index] if index < len(names) else f"Ch {index + 1}"
        location = labels[index] if index < len(labels) else ""
        display_name = raw_name if not location else f"{raw_name} ({location})"
        key = _unique_text_channel_name(display_name, used_names)
        values = np.asarray(raw_values[:, index], dtype=float).ravel()
        unit = units[index] if index < len(units) else ""
        autospectrum[key] = _floor_response_acceleration_psd(values, unit)
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=index,
                channel_key=key,
                display_name=key,
                unit="(m/s^2)^2/Hz",
                scale=1.0,
            )
        )
    if not series:
        return None
    rbw = _infer_rbw(frequency)
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.name,
        sample_rate=0.0,
        series=series,
        frequency_hz=frequency,
        autospectrum=autospectrum,
        rbw_hz=rbw,
        metadata={
            "source": "floor_response_eu_ascii",
            "plot_kind": "psd",
            "autospectrum_kind": "psd",
            "rbw_hz": rbw,
            "input_units": ",".join(units),
            "conversion": f"(value*{STANDARD_GRAVITY_M_S2:g})^2/{FLOOR_RESPONSE_EU_WINDOW_CORRECTION:g}",
        },
    )


def _looks_like_floor_response_eu_ascii(lines: list[str]) -> bool:
    head = "\n".join(lines[:32]).lower()
    return (
        "data written for" in head
        and "channel names" in head
        and "frequency" in head
        and "units:" in head
        and "\t mag" in head
    )


def _floor_response_numeric_matrix(lines: list[str]) -> np.ndarray | None:
    rows: list[list[float]] = []
    width = 0
    after_units = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == "units:":
            after_units = True
            continue
        if not after_units:
            continue
        tokens = _split_numeric_text_fields(line)
        if len(tokens) < 2:
            continue
        first = _parse_numeric_text_float(tokens[0])
        if not np.isfinite(first):
            continue
        values = [_parse_numeric_text_float(token) for token in tokens]
        if len(values) < 2 or not any(np.isfinite(value) for value in values[1:]):
            continue
        rows.append(values)
        width = max(width, len(values))
    if len(rows) < 2 or width < 2:
        return None
    data = np.full((len(rows), width), np.nan, dtype=float)
    for row_index, values in enumerate(rows):
        data[row_index, : len(values)] = values
    finite_columns = np.any(np.isfinite(data), axis=0)
    data = data[:, finite_columns]
    return data if data.shape[1] >= 2 else None


def _floor_response_channel_names(lines: list[str], channel_count: int) -> list[str]:
    for line in lines[:32]:
        if not line.strip().lower().startswith("channel names"):
            continue
        fields = _split_floor_response_header_fields(line)
        if fields and fields[0].lower() == "channel names":
            fields = fields[1:]
        else:
            fields = re.split(r"channel\s+names", line, maxsplit=1, flags=re.I)[-1].strip().split()
        names = [field.strip() for field in fields if field.strip()]
        if names:
            return _pad_channel_labels(names, channel_count, "Ch")
    return [f"Ch {index + 1}" for index in range(channel_count)]


def _floor_response_channel_units(lines: list[str], channel_count: int) -> list[str]:
    for index, line in enumerate(lines):
        if line.strip().lower() != "units:":
            continue
        for candidate in lines[index + 1 : index + 4]:
            fields = _split_floor_response_header_fields(candidate)
            if len(fields) >= 2 and fields[0].strip().lower() in {"hz", "frequency"}:
                return _pad_channel_labels(fields[1:], channel_count, "")
    return [""] * channel_count


def _floor_response_location_labels(lines: list[str], channel_count: int) -> list[str]:
    for index, line in enumerate(lines[:32]):
        if "frequency" not in line.lower() or "mag" not in line.lower():
            continue
        for candidate in reversed(lines[:index]):
            fields = _split_floor_response_header_fields(candidate)
            fields = [field for field in fields if field]
            if len(fields) >= channel_count and not any(
                field.lower().startswith(("channel", "ref chan", "data written")) for field in fields
            ):
                return _pad_channel_labels(fields[-channel_count:], channel_count, "")
    return [""] * channel_count


def _split_floor_response_header_fields(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text:
        return []
    if "\t" in text:
        return [field.strip() for field in text.split("\t") if field.strip()]
    fields = [field.strip() for field in re.split(r"\s{2,}", text) if field.strip()]
    return fields if len(fields) > 1 else text.split()


def _pad_channel_labels(values: list[str], channel_count: int, fallback_prefix: str) -> list[str]:
    labels = [str(value or "").strip() for value in values[:channel_count]]
    while len(labels) < channel_count:
        labels.append(f"{fallback_prefix} {len(labels) + 1}".strip())
    return labels


def _floor_response_acceleration_psd(values: np.ndarray, unit: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    unit_text = str(unit or "").strip().lower()
    if unit_text in {"g", "g rms", "grms"}:
        arr = arr * STANDARD_GRAVITY_M_S2
    psd = (arr**2) / FLOOR_RESPONSE_EU_WINDOW_CORRECTION
    psd[~np.isfinite(psd)] = np.nan
    return psd


def _load_headered_time_table_dataset(path: Path, *, fs_hint: float, dataset_id: int) -> AnalysisDataset | None:
    parsed = _parse_plot_export_table(path)
    if parsed is None:
        return None
    metadata, headers, data = parsed
    del metadata
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 2:
        return None
    headers = [str(header or "").strip() for header in headers[: data.shape[1]]]
    if not headers or _tokens_are_numeric(headers):
        return None
    time_index = _header_time_column_index(headers, data)
    if time_index is None:
        return None
    time_s = np.asarray(data[:, time_index], dtype=float).ravel()
    valid_time = np.isfinite(time_s)
    time_s = time_s[valid_time]
    if time_s.size < 2 or not _is_time_like(time_s):
        return None
    fs = _sample_rate_from_time_vector(time_s, fs_hint)

    series: list[AnalysisSeries] = []
    channels: dict[str, np.ndarray] = {}
    channel_index = 0
    used_names: set[str] = set()
    for column, header in enumerate(headers):
        if column == time_index:
            continue
        values = np.asarray(data[:, column], dtype=float).ravel()
        values = values[valid_time]
        if values.size != time_s.size or not np.any(np.isfinite(values)):
            continue
        name = _unique_channel_name(_clean_header_channel_name(header, fallback=f"Ch {channel_index + 1}"), used_names)
        used_names.add(name)
        channels[name] = values
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=channel_index,
                channel_key=name,
                display_name=name,
                unit="",
                scale=1.0,
            )
        )
        channel_index += 1
    if not series:
        return None
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.name,
        sample_rate=fs,
        series=series,
        time_s=time_s,
        channels=channels,
        metadata={"source": "headered_time_table"},
    )


def _load_plot_export_dataset(path: Path, *, fs_hint: float, dataset_id: int) -> AnalysisDataset | None:
    parsed = _parse_plot_export_table(path)
    if parsed is None:
        return None
    metadata, headers, data = parsed
    pairs = _curve_pairs_from_headers(headers, data)
    if not pairs:
        return None

    explicit_kind = str(metadata.get("plot_kind", "") or "").strip().lower()
    export_flag = str(metadata.get("python_vna_plot_export", "") or "").strip()
    if explicit_kind:
        kind = explicit_kind
    else:
        kind = _infer_plot_export_kind(path, headers, pairs)
        if not kind:
            return None
    if kind not in {"psd", "transfer", "time", "cumulative", "foundation", "coherence"}:
        return None
    if kind == "psd":
        return _plot_export_psd_dataset(path, pairs, metadata=metadata, dataset_id=dataset_id)
    if kind == "transfer":
        return _plot_export_transfer_dataset(path, pairs, metadata=metadata, dataset_id=dataset_id)
    if export_flag == "1" and kind == "time":
        return _plot_export_time_dataset(path, pairs, metadata=metadata, fs_hint=fs_hint, dataset_id=dataset_id)
    return None


def _parse_plot_export_table(path: Path) -> tuple[dict[str, str], list[str], np.ndarray] | None:
    text = _read_text_for_numeric_table(path)
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines:
        return None

    metadata: dict[str, str] = {}
    data_lines: list[str] = []
    for line in raw_lines:
        if line.startswith("#"):
            key_value = line[1:].strip()
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                metadata[key.strip().lower()] = value.strip()
            continue
        data_lines.append(line)
    if len(data_lines) < 2:
        return None

    headers = _split_numeric_text_fields(data_lines[0])
    if not headers or _tokens_are_numeric(headers):
        return None
    rows: list[list[float]] = []
    expected = len(headers)
    for line in data_lines[1:]:
        tokens = _split_numeric_text_fields(line)
        if not tokens:
            continue
        values = [_parse_numeric_text_float(token) for token in tokens]
        if len(values) < expected:
            values.extend([np.nan] * (expected - len(values)))
        elif len(values) > expected:
            values = values[:expected]
        if any(np.isfinite(values)):
            rows.append(values)
    if not rows:
        return None
    data = np.asarray(rows, dtype=float)
    headers = headers[: data.shape[1]]
    if len(headers) < data.shape[1]:
        headers.extend(f"Col {index + 1}" for index in range(len(headers), data.shape[1]))
    return metadata, headers, data


def _read_text_for_numeric_table(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _read_numeric_text_matrix(path: Path) -> np.ndarray | None:
    text = _read_text_for_numeric_table(path)
    rows: list[list[float]] = []
    width = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = _split_numeric_text_fields(line)
        if not tokens:
            continue
        values = [_parse_numeric_text_float(token) for token in tokens]
        if not any(np.isfinite(values)):
            continue
        rows.append(values)
        width = max(width, len(values))
    if len(rows) < 2 or width < 2:
        return None
    data = np.full((len(rows), width), np.nan, dtype=float)
    for row_index, row in enumerate(rows):
        data[row_index, : len(row)] = row
    return data


def _split_numeric_text_fields(line: str) -> list[str]:
    text = str(line).strip()
    if not text:
        return []
    if "," in text:
        return [token.strip().strip('"') for token in next(csv.reader(io.StringIO(text)))]
    if "\t" in text:
        return [token.strip().strip('"') for token in text.split("\t")]
    if ";" in text:
        return [token.strip().strip('"') for token in text.split(";")]
    return [token.strip().strip('"') for token in re.split(r"\s+", text) if token.strip()]


def _tokens_are_numeric(tokens: list[str]) -> bool:
    if not tokens:
        return False
    return all(np.isfinite(_parse_numeric_text_float(token)) for token in tokens)


def _parse_numeric_text_float(token: str) -> float:
    text = str(token).strip().strip('"')
    if not text:
        return float("nan")
    text = text.replace(",", "") if "." in text and "," in text else text
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _header_time_column_index(headers: list[str], data: np.ndarray) -> int | None:
    for index, header in enumerate(headers):
        lower = str(header or "").strip().lower()
        if lower in {"time", "t", "time_s", "time(s)", "time (s)", "elapsed", "elapsed time", "elapsed time (s)", "\u65f6\u95f4"}:
            values = np.asarray(data[:, index], dtype=float).ravel()
            values = values[np.isfinite(values)]
            if values.size >= 2 and _is_time_like(values):
                return index
    for index in range(data.shape[1]):
        values = np.asarray(data[:, index], dtype=float).ravel()
        values = values[np.isfinite(values)]
        if values.size >= 2 and _is_time_like(values):
            return index
    return None


def _clean_header_channel_name(header: str, *, fallback: str) -> str:
    text = str(header or "").strip().strip('"')
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def _unique_channel_name(name: str, used: set[str]) -> str:
    base = str(name or "Ch").strip() or "Ch"
    if base not in used:
        return base
    suffix = 2
    while f"{base} ({suffix})" in used:
        suffix += 1
    return f"{base} ({suffix})"


def _curve_pairs_from_headers(headers: list[str], data: np.ndarray) -> list[tuple[str, np.ndarray, np.ndarray]]:
    pairs: list[tuple[str, np.ndarray, np.ndarray]] = []
    used: set[int] = set()
    by_header = {header.lower(): index for index, header in enumerate(headers)}
    for index, header in enumerate(headers):
        lower = header.lower()
        if index in used or not lower.endswith("_x"):
            continue
        y_header = f"{header[:-2]}_y"
        y_index = by_header.get(y_header.lower())
        if y_index is None:
            continue
        x = np.asarray(data[:, index], dtype=float).ravel()
        y = np.asarray(data[:, y_index], dtype=float).ravel()
        x, y = _finite_aligned_time_values(x, y)
        if x.size < 2 or y.size < 2:
            continue
        pairs.append((_clean_plot_export_label(header[:-2]), x, y))
        used.update({index, y_index})
    return pairs


def _curve_pairs_from_common_axis(headers: list[str], data: np.ndarray) -> list[tuple[str, np.ndarray, np.ndarray]]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 2:
        return []
    frequency = np.asarray(arr[:, 0], dtype=float).ravel()
    if not _is_frequency_like(frequency):
        return []
    pairs: list[tuple[str, np.ndarray, np.ndarray]] = []
    used_names: set[str] = set()
    for column in range(1, arr.shape[1]):
        values = np.asarray(arr[:, column], dtype=float).ravel()
        valid = np.isfinite(frequency) & np.isfinite(values)
        if np.count_nonzero(valid) < 2:
            continue
        raw_label = headers[column] if column < len(headers) else f"Ch {column}"
        label = _unique_text_channel_name(
            _clean_header_channel_name(str(raw_label), fallback=f"Ch {column}"),
            used_names,
        )
        pairs.append((label, frequency, values))
    return pairs


def _frequency_pairs_from_named_arrays(arrays: dict[str, np.ndarray]) -> list[tuple[str, np.ndarray, np.ndarray]]:
    pairs: list[tuple[str, np.ndarray, np.ndarray]] = []
    for name, arr in arrays.items():
        pairs.extend(_frequency_pairs_from_matrix(name, arr))
    if pairs:
        return pairs

    axis_candidates = [
        (name, np.asarray(arr, dtype=float).ravel())
        for name, arr in arrays.items()
        if np.asarray(arr).ndim == 1 and _looks_like_frequency_name(name) and _is_frequency_like(np.asarray(arr, dtype=float))
    ]
    for axis_name, frequency in axis_candidates:
        del axis_name
        for name, arr in arrays.items():
            if np.asarray(arr).ndim == 1 and np.asarray(arr).size == frequency.size and _looks_like_frequency_name(name):
                continue
            pairs.extend(_frequency_pairs_from_axis_values(name, frequency, arr))
        if pairs:
            return pairs
    return []


def _frequency_pairs_from_matrix(name: str, values: np.ndarray) -> list[tuple[str, np.ndarray, np.ndarray]]:
    arr = _frequency_numeric_array_or_none(values)
    if arr is None:
        return []
    if arr.ndim > 2:
        arr = arr.reshape((arr.shape[0], -1))
    if arr.ndim != 2:
        return []
    headers = ["Frequency", *[f"{name}_{index}" for index in range(1, arr.shape[1])]]
    pairs = _curve_pairs_from_common_axis(headers, arr)
    if pairs:
        return pairs
    if arr.shape[0] >= 2 and _is_frequency_like(arr[0, :]):
        transposed = arr.T
        headers = ["Frequency", *[f"{name}_{index}" for index in range(1, transposed.shape[1])]]
        return _curve_pairs_from_common_axis(headers, transposed)
    return []


def _frequency_pairs_from_axis_values(
    name: str,
    frequency: np.ndarray,
    values: np.ndarray,
) -> list[tuple[str, np.ndarray, np.ndarray]]:
    arr = _frequency_numeric_array_or_none(values)
    if arr is None:
        return []
    freq = np.asarray(frequency, dtype=float).ravel()
    if not _is_frequency_like(freq):
        return []
    if arr.ndim == 1:
        return [(name, freq, arr.ravel())] if arr.size == freq.size else []
    if arr.ndim > 2:
        arr = arr.reshape((arr.shape[0], -1))
    if arr.ndim != 2:
        return []
    if arr.shape[0] == freq.size:
        matrix = arr.reshape((freq.size, -1))
    elif arr.shape[-1] == freq.size:
        matrix = np.moveaxis(arr, -1, 0).reshape((freq.size, -1))
    else:
        return []
    pairs: list[tuple[str, np.ndarray, np.ndarray]] = []
    for column in range(matrix.shape[1]):
        label = name if matrix.shape[1] == 1 else f"{name}_{column + 1}"
        pairs.append((label, freq, matrix[:, column]))
    return pairs


def _frequency_pairs_from_hdf5(path: Path) -> list[tuple[str, np.ndarray, np.ndarray]]:
    import h5py

    arrays: dict[str, np.ndarray] = {}
    with h5py.File(path, "r") as handle:
        def collect(name: str, obj: object) -> None:
            arr = _hdf5_numeric_array_or_none(obj)
            if arr is not None:
                arrays[str(name)] = arr

        handle.visititems(collect)
    return _frequency_pairs_from_named_arrays(arrays)


def _frequency_numeric_array_or_none(value: object) -> np.ndarray | None:
    arr = _numeric_array_or_none(value)
    if arr is None:
        return None
    if np.iscomplexobj(arr):
        arr = np.abs(arr)
    try:
        return np.asarray(arr, dtype=float)
    except (TypeError, ValueError):
        return None


def _looks_like_frequency_name(name: str) -> bool:
    text = str(name or "").strip().lower()
    leaf = text.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    return leaf in {"f", "freq", "freq_hz", "frequency", "frequency_hz", "frequencies", "hz"}


def _is_frequency_like(values: np.ndarray) -> bool:
    axis = np.asarray(values, dtype=float).ravel()
    axis = axis[np.isfinite(axis)]
    if axis.size < 2:
        return False
    diffs = np.diff(axis)
    if diffs.size == 0 or np.any(~np.isfinite(diffs)) or np.any(diffs <= 0.0):
        return False
    return np.count_nonzero(axis > 0.0) >= 2 and float(np.nanmax(axis)) > 0.0


def _clean_plot_export_label(label: str) -> str:
    text = str(label or "curve").replace("_", " ").strip()
    return " ".join(text.split()) or "curve"


def _infer_plot_export_kind(
    path: Path,
    headers: list[str],
    pairs: list[tuple[str, np.ndarray, np.ndarray]],
) -> str:
    haystack = " ".join([path.stem, *headers]).lower()
    if any(token in haystack for token in ("transfer", "trans", "frf", "\u4f20\u9012\u7387", "\u50b3\u905e\u7387")):
        return "transfer"
    if any(token in haystack for token in ("psd", "autospectrum", "spectrum", "\u529f\u7387\u8c31", "\u529f\u7387\u8b5c")):
        return "psd"
    positive_frequency_like = 0
    for _label, x, y in pairs:
        x_arr, y_arr = _finite_aligned_time_values(x, y)
        if x_arr.size < 3:
            continue
        diffs = np.diff(x_arr)
        if np.all(np.isfinite(diffs)) and np.all(diffs > 0.0) and np.nanmin(x_arr) > 0.0 and np.nanmax(x_arr) > 1.0:
            positive_frequency_like += 1
            if np.nanmin(y_arr) <= 0.0 or (np.nanmax(y_arr) <= 240.0 and np.nanmin(y_arr) >= -240.0 and np.nanmax(np.abs(y_arr)) < 1e-9):
                return "transfer"
    if positive_frequency_like == len(pairs):
        return "psd"
    return ""


def _plot_export_psd_dataset(
    path: Path,
    pairs: list[tuple[str, np.ndarray, np.ndarray]],
    *,
    metadata: dict[str, str],
    dataset_id: int,
) -> AnalysisDataset:
    frequency = _common_frequency_axis(pairs)
    series: list[AnalysisSeries] = []
    autospectrum: dict[str, np.ndarray] = {}
    used_names: set[str] = set()
    for label, x, y in pairs:
        key = _unique_text_channel_name(label, used_names)
        values = _values_on_frequency_axis(frequency, x, y, positive=True)
        autospectrum[key] = values
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=len(series),
                channel_key=key,
                display_name=key,
                unit="",
                scale=1.0,
            )
        )
    if not series:
        raise ValueError("Plot export does not contain PSD curves.")
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.name,
        sample_rate=0.0,
        series=series,
        frequency_hz=frequency,
        autospectrum=autospectrum,
        rbw_hz=_infer_rbw(frequency),
        metadata={
            **metadata,
            "source": "plot_export",
            "plot_kind": "psd",
            "autospectrum_kind": "psd",
        },
    )


def _plot_export_transfer_dataset(
    path: Path,
    pairs: list[tuple[str, np.ndarray, np.ndarray]],
    *,
    metadata: dict[str, str],
    dataset_id: int,
) -> AnalysisDataset:
    frequency = _common_frequency_axis(pairs)
    input_key = "Input"
    series = [
        AnalysisSeries(
            dataset_id=dataset_id,
            channel_index=0,
            channel_key=input_key,
            display_name=input_key,
            unit="",
            scale=1.0,
        )
    ]
    frf: dict[str, np.ndarray] = {}
    used_names = {input_key}
    for label, x, y in pairs:
        output_key = _unique_text_channel_name(label, used_names)
        magnitude_db = _values_on_frequency_axis(frequency, x, y, positive=False)
        magnitude = 10.0 ** (magnitude_db / 20.0)
        magnitude[~np.isfinite(magnitude)] = np.nan
        frf[f"{input_key}->{output_key}"] = magnitude.astype(complex)
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=len(series),
                channel_key=output_key,
                display_name=output_key,
                unit="",
                scale=1.0,
            )
        )
    if len(series) < 2:
        raise ValueError("Plot export does not contain transfer curves.")
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.name,
        sample_rate=0.0,
        series=series,
        frequency_hz=frequency,
        frf=frf,
        rbw_hz=_infer_rbw(frequency),
        metadata={
            **metadata,
            "source": "plot_export",
            "plot_kind": "transfer",
            "frf_kind": "magnitude_db",
        },
    )


def _plot_export_time_dataset(
    path: Path,
    pairs: list[tuple[str, np.ndarray, np.ndarray]],
    *,
    metadata: dict[str, str],
    fs_hint: float,
    dataset_id: int,
) -> AnalysisDataset:
    master_time = max((pair[1] for pair in pairs), key=lambda arr: arr.size)
    master_time = np.asarray(master_time, dtype=float).ravel()
    fs = _sample_rate_from_time_vector(master_time, fs_hint)
    channels: dict[str, np.ndarray] = {}
    series: list[AnalysisSeries] = []
    used_names: set[str] = set()
    for label, x, y in pairs:
        key = _unique_text_channel_name(label, used_names)
        channels[key] = _values_on_frequency_axis(master_time, x, y, positive=False)
        series.append(
            AnalysisSeries(
                dataset_id=dataset_id,
                channel_index=len(series),
                channel_key=key,
                display_name=key,
                unit="",
                scale=1.0,
            )
        )
    return AnalysisDataset(
        id=dataset_id,
        path=path,
        name=path.name,
        sample_rate=fs,
        series=series,
        time_s=master_time,
        channels=channels,
        metadata={**metadata, "source": "plot_export", "plot_kind": "time"},
    )


def _common_frequency_axis(pairs: list[tuple[str, np.ndarray, np.ndarray]]) -> np.ndarray:
    axes: list[np.ndarray] = []
    for _label, x, y in pairs:
        x_arr, y_arr = _finite_aligned_time_values(x, y)
        valid = np.isfinite(x_arr) & np.isfinite(y_arr) & (x_arr > 0.0)
        if np.count_nonzero(valid) >= 2:
            axes.append(np.asarray(x_arr[valid], dtype=float))
    if not axes:
        raise ValueError("Plot export does not contain a valid frequency axis.")
    first = axes[0]
    if all(axis.size == first.size and np.allclose(axis, first, rtol=1e-8, atol=1e-12) for axis in axes[1:]):
        return first
    merged = np.unique(np.concatenate(axes))
    return merged[np.isfinite(merged) & (merged > 0.0)]


def _values_on_frequency_axis(
    frequency: np.ndarray,
    source_x: np.ndarray,
    source_y: np.ndarray,
    *,
    positive: bool,
) -> np.ndarray:
    x, y = _finite_aligned_time_values(source_x, source_y)
    valid = np.isfinite(x) & np.isfinite(y)
    if positive:
        valid &= y > 0.0
    x = x[valid]
    y = y[valid]
    if x.size < 2:
        return np.full(np.asarray(frequency).shape, np.nan, dtype=float)
    order = np.argsort(x)
    x_sorted = x[order]
    y_sorted = y[order]
    x_unique, unique_indices = np.unique(x_sorted, return_index=True)
    y_unique = y_sorted[unique_indices]
    if x_unique.size < 2:
        return np.full(np.asarray(frequency).shape, np.nan, dtype=float)
    return np.interp(
        np.asarray(frequency, dtype=float),
        x_unique,
        y_unique,
        left=np.nan,
        right=np.nan,
    )


def _unique_text_channel_name(raw_name: str, used_names: set[str]) -> str:
    base = str(raw_name or "curve").replace("\n", " ").replace("\r", " ").strip()
    base = " ".join(base.split()) or "curve"
    candidate = base
    index = 2
    while candidate in used_names:
        candidate = f"{base}_{index}"
        index += 1
    used_names.add(candidate)
    return candidate


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
    time_s, channels = load_continuous_channels(
        dataset,
        [channel_key],
        start_s=start_s,
        end_s=end_s,
        max_points=max_points,
    )
    return time_s, channels.get(channel_key, np.array([], dtype=float))


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

    if _continuous_request_is_full_range(start_sample, end_sample, total_samples) and not _continuous_dataset_uses_binary(dataset):
        cached = _load_or_create_continuous_cache(dataset, keys)
        if cached is not None:
            time_s, channels = cached
            if max_points is not None and max_points > 0 and time_s.size > max_points:
                step = max(1, int(np.ceil(time_s.size / int(max_points))))
                return (
                    time_s[::step],
                    {
                        key: np.asarray(channels.get(key, np.array([], dtype=float)))[::step]
                        for key in keys
                    },
                )
            return time_s, {key: channels.get(key, np.array([], dtype=float)) for key in keys}

    segment_offsets: list[tuple[ContinuousSegmentInfo, int]] = []
    sample_offset = 0
    for segment in dataset.continuous_segments:
        segment_samples = max(0, int(segment.samples))
        segment_start = sample_offset
        segment_end = segment_start + segment_samples
        sample_offset = segment_end
        if end_sample is not None and segment_start > end_sample:
            break
        if segment_samples > 0 and segment_end < start_sample:
            continue
        segment_offsets.append((segment, segment_start))

    if not segment_offsets:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}

    worker_args = [
        (
            segment,
            offset,
            keys,
            float(dataset.sample_rate),
            start_s,
            end_s,
            start_sample,
            stride,
        )
        for segment, offset in segment_offsets
    ]
    workers = min(4, len(worker_args))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_load_continuous_segment_columns, worker_args))
    else:
        results = [_load_continuous_segment_columns(args) for args in worker_args]

    time_chunks: list[np.ndarray] = []
    data_chunks: dict[str, list[np.ndarray]] = {key: [] for key in keys}
    for time_chunk, channel_chunk, _sample_count in results:
        if time_chunk.size == 0:
            continue
        time_chunks.append(time_chunk)
        for key in keys:
            data_chunks[key].append(channel_chunk.get(key, np.array([], dtype=float)))

    if not time_chunks:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}
    time_s = np.concatenate(time_chunks)
    channels = {
        key: np.concatenate(chunks) if chunks else np.array([], dtype=float)
        for key, chunks in data_chunks.items()
    }
    return time_s, channels


def _continuous_request_is_full_range(
    start_sample: int,
    end_sample: int | None,
    total_samples: int,
) -> bool:
    return int(start_sample) <= 0 and int(total_samples) > 0 and (
        end_sample is None or int(end_sample) >= int(total_samples)
    )


def _continuous_dataset_uses_binary(dataset: AnalysisDataset) -> bool:
    for segment in dataset.continuous_segments:
        if str(segment.storage_format).lower() == "binary" or segment.path.suffix.lower() == ".bin":
            return True
    return False


def _load_or_create_continuous_cache(
    dataset: AnalysisDataset,
    keys: list[str],
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    cache_dir = _continuous_cache_path(dataset)
    if cache_dir is None:
        return None
    meta_path = cache_dir / "meta.json"
    meta = _read_continuous_cache_meta(meta_path)
    cached_channels: dict[str, np.ndarray] = {}
    missing_keys: list[str] = []
    for key in keys:
        channel_path = _continuous_channel_cache_file(cache_dir, key)
        if meta and channel_path.exists():
            try:
                cached_channels[key] = np.load(channel_path, allow_pickle=False)
                continue
            except (OSError, ValueError):
                pass
        missing_keys.append(key)

    if missing_keys:
        time_s, loaded_channels = _load_continuous_channels_uncached(dataset, missing_keys)
        if time_s.size == 0:
            return time_s, {key: cached_channels.get(key, np.array([], dtype=float)) for key in keys}
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            for key in missing_keys:
                np.save(
                    _continuous_channel_cache_file(cache_dir, key),
                    np.asarray(loaded_channels.get(key, np.array([], dtype=float)), dtype=np.float64),
                )
            meta = {
                "format": "python_vna_analysis_continuous_cache",
                "version": 1,
                "sample_count": int(time_s.size),
                "sample_rate": float(dataset.sample_rate),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        cached_channels.update(loaded_channels)
    else:
        sample_count = int(meta.get("sample_count", _continuous_total_samples(dataset))) if meta else _continuous_total_samples(dataset)
        sample_rate = float(meta.get("sample_rate", dataset.sample_rate)) if meta else float(dataset.sample_rate)
        time_s = np.arange(sample_count, dtype=float) / max(sample_rate, 1e-20)

    return time_s, {key: cached_channels.get(key, np.array([], dtype=float)) for key in keys}


def _read_continuous_cache_meta(path: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(meta.get("format", "")) != "python_vna_analysis_continuous_cache":
        return None
    return meta


def _continuous_cache_path(dataset: AnalysisDataset) -> Path | None:
    if not dataset.continuous_segments:
        return None
    source = Path(dataset.path)
    folder = source.parent if source.name == "manifest.json" else source
    try:
        manifest_stat = source.stat() if source.exists() else None
    except OSError:
        manifest_stat = None
    fingerprint_parts = [
        str(source.resolve() if source.exists() else source),
        str(getattr(manifest_stat, "st_mtime_ns", 0)),
        str(getattr(manifest_stat, "st_size", 0)),
        str(float(dataset.sample_rate)),
        str(_continuous_total_samples(dataset)),
    ]
    digest = hashlib.sha1("\x1e".join(fingerprint_parts).encode("utf-8", errors="ignore")).hexdigest()[:20]
    return folder / ".analysis_cache" / digest


def _continuous_channel_cache_file(cache_dir: Path, key: str) -> Path:
    digest = hashlib.sha1(str(key).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return cache_dir / f"{_safe_cache_token(key)[:48]}_{digest}.npy"


def _safe_cache_token(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value)).strip("._")
    return text or "ch"


def _load_continuous_channels_uncached(
    dataset: AnalysisDataset,
    keys: list[str],
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    segment_offsets: list[tuple[ContinuousSegmentInfo, int]] = []
    sample_offset = 0
    for segment in dataset.continuous_segments:
        segment_offsets.append((segment, sample_offset))
        sample_offset += max(0, int(segment.samples))
    if not segment_offsets:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}
    worker_args = [
        (
            segment,
            offset,
            keys,
            float(dataset.sample_rate),
            None,
            None,
            0,
            1,
        )
        for segment, offset in segment_offsets
    ]
    workers = min(4, len(worker_args))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_load_continuous_segment_columns, worker_args))
    else:
        results = [_load_continuous_segment_columns(args) for args in worker_args]
    time_chunks: list[np.ndarray] = []
    data_chunks: dict[str, list[np.ndarray]] = {key: [] for key in keys}
    for time_chunk, channel_chunk, _sample_count in results:
        if time_chunk.size == 0:
            continue
        time_chunks.append(time_chunk)
        for key in keys:
            data_chunks[key].append(channel_chunk.get(key, np.array([], dtype=float)))
    if not time_chunks:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}
    return (
        np.concatenate(time_chunks),
        {key: np.concatenate(chunks) if chunks else np.array([], dtype=float) for key, chunks in data_chunks.items()},
    )


def _load_continuous_segment_columns(
    args: tuple[
        ContinuousSegmentInfo,
        int,
        list[str],
        float,
        float | None,
        float | None,
        int,
        int,
    ]
) -> tuple[np.ndarray, dict[str, np.ndarray], int]:
    (
        segment,
        sample_offset,
        keys,
        sample_rate,
        start_s,
        end_s,
        start_sample,
        stride,
    ) = args
    if str(segment.storage_format).lower() == "binary" or segment.path.suffix.lower() == ".bin":
        return _load_binary_continuous_segment_columns(
            segment,
            keys,
            sample_rate,
            sample_offset,
            start_s=start_s,
            end_s=end_s,
            start_sample=start_sample,
            stride=stride,
        )
    try:
        _header, columns, data_start_line = read_dat_table_info(segment.path)
        has_local_time = len(columns) > 1 and columns[1] == "local_time"
        data_start_col = 5 if has_local_time else 4
        segment_channel_names = [str(name) for name in columns[data_start_col:]]
        selected_indices = [segment_channel_names.index(key) for key in keys]
    except (OSError, ValueError):
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, max(0, int(segment.samples))

    try:
        raw = read_dat_numeric_columns(
            segment.path,
            [0, *[data_start_col + index for index in selected_indices]],
            data_start_line=data_start_line,
        )
    except (OSError, ValueError):
        return _load_continuous_segment_by_frames(
            segment,
            keys,
            sample_rate,
            sample_offset,
            start_s=start_s,
            end_s=end_s,
            start_sample=start_sample,
            stride=stride,
        )

    if raw.size == 0:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, max(0, int(segment.samples))
    sample_count = int(raw.shape[0])
    global_samples = np.arange(sample_count, dtype=np.int64) + int(sample_offset)
    frame_time = global_samples.astype(float) / max(sample_rate, 1e-20)
    mask = np.ones(sample_count, dtype=bool)
    if start_s is not None and np.isfinite(start_s):
        mask &= frame_time >= float(start_s)
    if end_s is not None and np.isfinite(end_s):
        mask &= frame_time <= float(end_s)
    if stride > 1:
        mask &= ((global_samples - start_sample) % stride) == 0
    if not np.any(mask):
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, sample_count
    channels = {
        key: raw[:, output_index + 1][mask]
        for output_index, key in enumerate(keys)
    }
    return frame_time[mask], channels, sample_count


def _load_binary_continuous_segment_columns(
    segment: ContinuousSegmentInfo,
    keys: list[str],
    sample_rate: float,
    sample_offset: int,
    *,
    start_s: float | None,
    end_s: float | None,
    start_sample: int,
    stride: int,
) -> tuple[np.ndarray, dict[str, np.ndarray], int]:
    names = [str(name) for name in segment.channel_names]
    if not names:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, max(0, int(segment.samples))
    try:
        selected_indices = [names.index(key) for key in keys]
    except ValueError:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, max(0, int(segment.samples))
    channel_count = len(names)
    sample_count = max(0, int(segment.samples))
    if sample_count <= 0:
        try:
            sample_count = segment.path.stat().st_size // (8 * max(channel_count, 1))
        except OSError:
            sample_count = 0
    if sample_count <= 0:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, 0
    try:
        data = np.memmap(
            segment.path,
            dtype="<f8",
            mode="r",
            shape=(sample_count, channel_count),
            order="C",
        )
    except (OSError, ValueError):
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, sample_count

    local_start = 0
    local_stop = sample_count
    if start_s is not None and np.isfinite(start_s):
        local_start = max(0, int(np.floor(float(start_s) * sample_rate)) - int(sample_offset))
    if end_s is not None and np.isfinite(end_s):
        local_stop = min(
            sample_count,
            max(local_start, int(np.ceil(float(end_s) * sample_rate)) - int(sample_offset) + 1),
        )
    if local_start >= local_stop:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, sample_count

    if stride > 1:
        offset_from_origin = (int(sample_offset) + local_start - int(start_sample)) % int(stride)
        if offset_from_origin:
            local_start += int(stride) - offset_from_origin
    if local_start >= local_stop:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, sample_count

    step = max(1, int(stride))
    local_slice = slice(local_start, local_stop, step)
    global_samples = np.arange(local_start + int(sample_offset), local_stop + int(sample_offset), step, dtype=np.int64)
    frame_time = global_samples.astype(float) / max(sample_rate, 1e-20)
    channels = {
        key: np.asarray(data[local_slice, selected_indices[output_index]], dtype=float)
        for output_index, key in enumerate(keys)
    }
    return frame_time, channels, sample_count


def _load_continuous_segment_by_frames(
    segment: ContinuousSegmentInfo,
    keys: list[str],
    sample_rate: float,
    sample_offset: int,
    *,
    start_s: float | None,
    end_s: float | None,
    start_sample: int,
    stride: int,
) -> tuple[np.ndarray, dict[str, np.ndarray], int]:
    time_chunks: list[np.ndarray] = []
    data_chunks: dict[str, list[np.ndarray]] = {key: [] for key in keys}
    selected_indices: list[int] | None = None
    consumed_samples = 0
    for _header, frame in iter_dat_frames(segment.path):
        names = [str(name) for name in frame.get("channel_names", [])]
        if selected_indices is None:
            try:
                selected_indices = [names.index(key) for key in keys]
            except ValueError:
                return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, max(0, int(segment.samples))
        data = np.asarray(frame.get("data", np.empty((0, 0))), dtype=float)
        if data.ndim != 2:
            continue
        sample_count = int(data.shape[1])
        global_samples = np.arange(sample_count, dtype=np.int64) + int(sample_offset) + consumed_samples
        frame_time = global_samples.astype(float) / max(sample_rate, 1e-20)
        consumed_samples += sample_count
        mask = np.ones(sample_count, dtype=bool)
        if start_s is not None and np.isfinite(start_s):
            mask &= frame_time >= float(start_s)
        if end_s is not None and np.isfinite(end_s):
            mask &= frame_time <= float(end_s)
        if stride > 1:
            mask &= ((global_samples - start_sample) % stride) == 0
        if not np.any(mask):
            continue
        time_chunks.append(frame_time[mask])
        for output_index, key in enumerate(keys):
            channel_index = selected_indices[output_index]
            if channel_index < data.shape[0]:
                data_chunks[key].append(data[channel_index][mask])
    if not time_chunks:
        return np.array([], dtype=float), {key: np.array([], dtype=float) for key in keys}, consumed_samples
    return (
        np.concatenate(time_chunks),
        {key: np.concatenate(chunks) if chunks else np.array([], dtype=float) for key, chunks in data_chunks.items()},
        consumed_samples,
    )


def _append_continuous_frames_from_segment(
    segment: ContinuousSegmentInfo,
    keys: list[str],
    sample_rate: float,
    sample_offset: int,
    *,
    start_s: float | None,
    end_s: float | None,
    start_sample: int,
    stride: int,
    time_chunks: list[np.ndarray],
    data_chunks: dict[str, list[np.ndarray]],
) -> int:
    selected_indices: list[int] | None = None
    for _header, frame in iter_dat_frames(segment.path):
        names = [str(name) for name in frame.get("channel_names", [])]
        if selected_indices is None:
            try:
                selected_indices = [names.index(key) for key in keys]
            except ValueError:
                return sample_offset + max(0, int(segment.samples))
        data = np.asarray(frame.get("data", np.empty((0, 0))), dtype=float)
        if data.ndim != 2:
            continue
        sample_count = int(data.shape[1])
        global_samples = np.arange(sample_count, dtype=np.int64) + sample_offset
        frame_time = global_samples.astype(float) / max(sample_rate, 1e-20)
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
        for output_index, key in enumerate(keys):
            channel_index = selected_indices[output_index]
            if channel_index < data.shape[0]:
                data_chunks[key].append(data[channel_index][mask])
    return sample_offset


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


def _is_simulink_time_key(key: str) -> bool:
    return str(key).strip().lower() in {"t", "time", "tout", "tout_sim", "sim_time"}


def _load_simulink_hdf5_records(path: Path) -> list[tuple[str, np.ndarray, np.ndarray]]:
    import h5py

    records: list[tuple[str, np.ndarray, np.ndarray]] = []
    with h5py.File(path, "r") as handle:
        global_time = _hdf5_global_time(handle)
        for key in handle.keys():
            if str(key).startswith("#") or _is_simulink_time_key(str(key)):
                continue
            _collect_hdf5_time_series(str(key), handle[key], global_time, records, set(), handle)
    return records


def _hdf5_global_time(handle: object) -> np.ndarray | None:
    for key in ("tout", "time", "t", "Time"):
        if key not in handle:
            continue
        values = _hdf5_numeric_array_or_none(handle[key])
        if values is None:
            continue
        time_s = np.asarray(values, dtype=float).ravel()
        if _is_time_like(time_s):
            return time_s
    candidates: list[np.ndarray] = []
    for key in handle.keys():
        if not _is_simulink_time_key(str(key)):
            continue
        values = _hdf5_numeric_array_or_none(handle[key])
        if values is None:
            continue
        time_s = np.asarray(values, dtype=float).ravel()
        if _is_time_like(time_s):
            candidates.append(time_s)
    return max(candidates, key=lambda arr: arr.size) if candidates else None


def _collect_hdf5_time_series(
    name: str,
    obj: object,
    global_time: np.ndarray | None,
    records: list[tuple[str, np.ndarray, np.ndarray]],
    seen: set[str],
    root: object,
) -> None:
    path = str(getattr(obj, "name", name))
    if path in seen:
        return
    seen.add(path)

    if _is_hdf5_dataset(obj):
        for ref_name, ref_obj in _iter_hdf5_references(name, obj, root):
            _collect_hdf5_time_series(ref_name, ref_obj, global_time, records, seen, root)
        numeric = _hdf5_numeric_array_or_none(obj)
        if numeric is None:
            return
        if global_time is not None:
            _append_simulink_matrix_records(records, name, global_time, numeric)
        else:
            arr = np.asarray(numeric)
            if arr.ndim == 2 and arr.shape[1] >= 2 and _is_time_like(arr[:, 0]):
                _append_simulink_matrix_records(records, name, arr[:, 0], arr[:, 1:])
            elif arr.ndim == 2 and arr.shape[0] >= 2 and _is_time_like(arr[0, :]):
                _append_simulink_matrix_records(records, name, arr[0, :], arr[1:, :])
        return

    if not _is_hdf5_group(obj):
        return
    keys = list(obj.keys())
    lowered = {str(key).lower(): str(key) for key in keys}
    time_key = lowered.get("time") or lowered.get("tout") or lowered.get("t")
    data_key = lowered.get("data") or lowered.get("values")
    if time_key and data_key:
        time_values = _hdf5_numeric_array_or_none(obj[time_key])
        data_values = _hdf5_numeric_array_or_none(obj[data_key])
        if time_values is not None and data_values is not None:
            _append_simulink_matrix_records(records, _hdf5_record_name(name, obj), time_values, data_values)
    signals_key = lowered.get("signals")
    if signals_key:
        local_time = global_time
        if time_key:
            maybe_time = _hdf5_numeric_array_or_none(obj[time_key])
            if maybe_time is not None and _is_time_like(np.asarray(maybe_time, dtype=float).ravel()):
                local_time = np.asarray(maybe_time, dtype=float).ravel()
        signals = obj[signals_key]
        if _is_hdf5_group(signals):
            for child_key in signals.keys():
                child = signals[child_key]
                signal_name = _hdf5_record_name(f"{name}.{child_key}", child)
                if _is_hdf5_group(child):
                    child_lowered = {str(key).lower(): str(key) for key in child.keys()}
                    values_key = child_lowered.get("values") or child_lowered.get("data")
                    if values_key and local_time is not None:
                        values = _hdf5_numeric_array_or_none(child[values_key])
                        if values is not None:
                            _append_simulink_matrix_records(records, signal_name, local_time, values)
                            continue
                _collect_hdf5_time_series(signal_name, child, local_time, records, seen, root)
        else:
            for ref_name, ref_obj in _iter_hdf5_references(name, signals, root):
                _collect_hdf5_time_series(ref_name, ref_obj, local_time, records, seen, root)
    for key in keys:
        key_lower = str(key).lower()
        if key_lower in {"time", "tout", "t", "data", "values", "signals"}:
            continue
        _collect_hdf5_time_series(f"{name}.{key}", obj[key], global_time, records, seen, root)


def _is_hdf5_dataset(obj: object) -> bool:
    return hasattr(obj, "shape") and hasattr(obj, "dtype") and hasattr(obj, "__array__")


def _is_hdf5_group(obj: object) -> bool:
    return hasattr(obj, "keys") and hasattr(obj, "__getitem__")


def _hdf5_numeric_array_or_none(obj: object) -> np.ndarray | None:
    if not _is_hdf5_dataset(obj):
        return None
    try:
        arr = np.asarray(obj[()])
    except Exception:
        return None
    if arr.dtype.kind not in "biufc":
        return None
    return np.asarray(arr)


def _iter_hdf5_references(name: str, obj: object, root: object) -> list[tuple[str, object]]:
    if not _is_hdf5_dataset(obj):
        return []
    try:
        arr = np.asarray(obj[()])
    except Exception:
        return []
    if arr.dtype.kind != "O":
        return []
    refs: list[tuple[str, object]] = []
    for index, ref in enumerate(np.ravel(arr).tolist()):
        try:
            target = root[ref]
        except Exception:
            continue
        refs.append((f"{name}_{index + 1}", target))
    return refs


def _hdf5_record_name(default: str, obj: object) -> str:
    for attr_name in ("name", "label", "blockName", "blockPath", "MATLAB_object_name"):
        try:
            value = obj.attrs.get(attr_name)
        except Exception:
            value = None
        text = _mat_text_or_none(value)
        if text:
            return text
    return default


def _simulink_global_time(payload: dict[str, object]) -> np.ndarray | None:
    for key in ("tout", "time", "t", "Time"):
        if key not in payload:
            continue
        values = _numeric_array_or_none(payload[key])
        if values is None:
            continue
        time_s = np.asarray(values, dtype=float).ravel()
        if _is_time_like(time_s):
            return time_s
    candidates: list[np.ndarray] = []
    for key, value in payload.items():
        if not _is_simulink_time_key(key):
            continue
        values = _numeric_array_or_none(value)
        if values is None:
            continue
        time_s = np.asarray(values, dtype=float).ravel()
        if _is_time_like(time_s):
            candidates.append(time_s)
    return max(candidates, key=lambda arr: arr.size) if candidates else None


def _collect_simulink_time_series(
    name: str,
    value: object,
    global_time: np.ndarray | None,
    records: list[tuple[str, np.ndarray, np.ndarray]],
    seen: set[int],
) -> None:
    object_id = id(value)
    if object_id in seen:
        return
    seen.add(object_id)

    if isinstance(value, dict):
        fields = set(value.keys())
        field_getter = value.get
    else:
        fields = set(getattr(value, "_fieldnames", []) or [])
        field_getter = lambda field, default=None: getattr(value, field, default)

    if fields:
        lowered = {str(field).lower(): str(field) for field in fields}
        if "values" in lowered:
            record_name = _simulink_record_name(name, value)
            _collect_simulink_time_series(record_name, field_getter(lowered["values"]), global_time, records, seen)
        time_field = lowered.get("time") or lowered.get("tout")
        data_field = lowered.get("data")
        if time_field and data_field:
            time_values = _numeric_array_or_none(field_getter(time_field))
            data_values = _numeric_array_or_none(field_getter(data_field))
            if time_values is not None and data_values is not None:
                _append_simulink_matrix_records(records, name, time_values, data_values)
        if "signals" in lowered:
            local_time = global_time
            if time_field:
                maybe_time = _numeric_array_or_none(field_getter(time_field))
                if maybe_time is not None and _is_time_like(np.asarray(maybe_time, dtype=float).ravel()):
                    local_time = np.asarray(maybe_time, dtype=float).ravel()
            signals = field_getter(lowered["signals"])
            for index, signal in enumerate(_iter_mat_items(signals)):
                signal_name = _simulink_record_name(f"{name}_{index + 1}", signal)
                signal_fields = set(getattr(signal, "_fieldnames", []) or [])
                if isinstance(signal, dict):
                    signal_fields = set(signal.keys())
                    signal_getter = signal.get
                else:
                    signal_getter = lambda field, default=None, obj=signal: getattr(obj, field, default)
                signal_lowered = {str(field).lower(): str(field) for field in signal_fields}
                values_field = signal_lowered.get("values") or signal_lowered.get("data")
                if values_field and local_time is not None:
                    signal_values = _numeric_array_or_none(signal_getter(values_field))
                    if signal_values is not None:
                        _append_simulink_matrix_records(records, signal_name, local_time, signal_values)
                else:
                    _collect_simulink_time_series(signal_name, signal, local_time, records, seen)
        for field in sorted(fields, key=str):
            field_name = str(field)
            if field_name.lower() in {"time", "tout", "data", "signals", "values"}:
                continue
            _collect_simulink_time_series(f"{name}.{field_name}", field_getter(field), global_time, records, seen)
        return

    numeric = _numeric_array_or_none(value)
    if numeric is None:
        return
    if global_time is not None:
        _append_simulink_matrix_records(records, name, global_time, numeric)
    else:
        numeric = np.asarray(numeric)
        if numeric.ndim == 2 and numeric.shape[1] >= 2 and _is_time_like(numeric[:, 0]):
            _append_simulink_matrix_records(records, name, numeric[:, 0], numeric[:, 1:])


def _simulink_record_name(default: str, value: object) -> str:
    if isinstance(value, dict):
        getter = value.get
        fields = set(value.keys())
    else:
        getter = lambda field, fallback=None: getattr(value, field, fallback)
        fields = set(getattr(value, "_fieldnames", []) or [])
    lowered = {str(field).lower(): str(field) for field in fields}
    for candidate in ("name", "label", "blockname", "blockpath"):
        field = lowered.get(candidate)
        if not field:
            continue
        text = _mat_text_or_none(getter(field))
        if text:
            return text
    return default


def _append_simulink_matrix_records(
    records: list[tuple[str, np.ndarray, np.ndarray]],
    base_name: str,
    time_values: object,
    data_values: object,
) -> None:
    time_s = _numeric_array_or_none(time_values)
    data = _numeric_array_or_none(data_values)
    if time_s is None or data is None:
        return
    t = np.asarray(time_s, dtype=float).ravel()
    if t.size < 2 or not _is_time_like(t):
        return
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        if np.allclose(np.imag(arr), 0.0, rtol=1e-9, atol=1e-12):
            arr = np.real(arr)
        else:
            _append_simulink_matrix_records(records, f"{base_name}.real", t, np.real(arr))
            _append_simulink_matrix_records(records, f"{base_name}.imag", t, np.imag(arr))
            return
    arr = np.asarray(arr, dtype=float)
    if arr.ndim == 0:
        return
    if arr.ndim == 1:
        if arr.size == t.size:
            records.append((base_name, t, arr.ravel()))
        return
    if arr.shape[0] == t.size:
        matrix = arr.reshape((t.size, -1))
        if matrix.shape[1] >= 2 and np.allclose(matrix[:, 0], t, rtol=1e-8, atol=1e-12):
            matrix = matrix[:, 1:]
    elif arr.shape[-1] == t.size:
        matrix = np.moveaxis(arr, -1, 0).reshape((t.size, -1))
    else:
        return
    for column in range(matrix.shape[1]):
        suffix = "" if matrix.shape[1] == 1 else f"_{column + 1}"
        records.append((f"{base_name}{suffix}", t, matrix[:, column]))


def _numeric_array_or_none(value: object) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return None
    if isinstance(value, np.ndarray) and value.dtype == object:
        if value.size == 1:
            return _numeric_array_or_none(np.ravel(value)[0])
        return None
    try:
        arr = np.asarray(value)
    except Exception:
        return None
    if arr.dtype.kind not in "biufc":
        return None
    return arr


def _iter_mat_items(value: object) -> list[object]:
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return [item for item in np.ravel(value).tolist()]
        if value.size == 1:
            return [np.ravel(value)[0]]
        return [item for item in np.ravel(value).tolist()]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _mat_text_or_none(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bytes):
        return value.decode(errors="ignore").strip()
    if isinstance(value, np.ndarray):
        if value.dtype.kind in {"U", "S"}:
            return " ".join(str(part) for part in np.ravel(value).tolist()).strip()
        if value.size == 1:
            return _mat_text_or_none(np.ravel(value)[0])
    text = str(value).strip()
    return text if text and not text.startswith("<") else ""


def _simulink_master_time(
    records: list[tuple[str, np.ndarray, np.ndarray]],
    fs_hint: float,
) -> np.ndarray:
    best_time = max((record[1] for record in records), key=lambda arr: np.asarray(arr).size)
    time_s = np.asarray(best_time, dtype=float).ravel()
    time_s = time_s[np.isfinite(time_s)]
    if time_s.size >= 2:
        return time_s
    fs = _validate_fs_hint(fs_hint)
    longest = max((np.asarray(record[2]).size for record in records), default=0)
    return np.arange(longest, dtype=float) / fs


def _sample_rate_from_time_vector(time_s: np.ndarray, fs_hint: float) -> float:
    t = np.asarray(time_s, dtype=float).ravel()
    diffs = np.diff(t)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size:
        dt = float(np.median(diffs))
        if np.isfinite(dt) and dt > 0.0:
            return float(1.0 / dt)
    return _validate_fs_hint(fs_hint)


def _finite_aligned_time_values(time_s: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(time_s, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(t.size, y.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    t = t[:count]
    y = y[:count]
    finite = np.isfinite(t) & np.isfinite(y)
    return t[finite], y[finite]


def _unique_simulink_channel_name(raw_name: str, used_names: set[str]) -> str:
    base = str(raw_name or "signal").replace("\n", " ").replace("\r", " ").strip()
    base = " ".join(base.split()) or "signal"
    candidate = base
    index = 2
    while candidate in used_names:
        candidate = f"{base}_{index}"
        index += 1
    used_names.add(candidate)
    return candidate


def _validate_fs_hint(fs_hint: float) -> float:
    fs = float(fs_hint)
    if not np.isfinite(fs) or fs <= 0.0:
        raise ValueError("Fs hint must be a positive number for files without a time column.")
    return fs
