from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import csv
import io
import re
import struct
from typing import Iterable

import numpy as np

from python_vna.analysis_algorithms import compute_matlab_tfestimate


@dataclass(slots=True)
class CurvePair:
    label: str
    x: np.ndarray
    y: np.ndarray
    x_label: str = "X"
    y_label: str = "Y"


@dataclass(slots=True)
class NumericTableFile:
    path: Path
    name: str
    headers: list[str]
    data: np.ndarray
    kind: str = "table"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return int(self.data.shape[0]) if self.data.ndim == 2 else 0

    @property
    def column_count(self) -> int:
        return int(self.data.shape[1]) if self.data.ndim == 2 else 0


@dataclass(slots=True)
class VibrationAnalysisFile:
    table: NumericTableFile
    frequency_pairs: list[CurvePair]
    log_groups: dict[str, list[int]]
    log_group_labels: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class TraceAnalysisFile:
    table: NumericTableFile
    trace_kind: str
    time_s: np.ndarray
    channels: dict[str, np.ndarray]
    sample_rate: float
    channel_eu: dict[str, float] = field(default_factory=dict)


_TIME_HEADER_RE = re.compile(r"^(time|t|elapsed|seconds?|sec|timestamp|sample\s*time)$", re.I)
_SAMPLE_HEADER_RE = re.compile(r"^(sample|index|idx|n)$", re.I)


def load_vibration_analysis_file(path: str | Path) -> VibrationAnalysisFile:
    source = Path(path)
    lines = _read_text(source).splitlines()
    kind = _detect_vibration_file_kind(lines, source.stem)
    if kind == "frequency":
        return _load_vibration_frequency_file(source, lines)
    if kind == "ivsa_signal":
        return _load_vibration_signal_file(source, lines)
    wide_log = _try_load_vibration_wide_log(source, lines)
    if wide_log is not None:
        return wide_log
    table = load_numeric_table(path)
    pairs = curve_pairs_from_table(table)
    table.kind = _detect_vibration_kind(table, pairs)
    groups = build_log_groups(table.headers)
    return VibrationAnalysisFile(
        table=table,
        frequency_pairs=pairs,
        log_groups=groups,
        log_group_labels=build_log_group_labels(table.headers, groups),
    )


def load_trace_analysis_file(path: str | Path) -> TraceAnalysisFile:
    source = Path(path)
    if source.suffix.lower() == ".sdm":
        return _load_ide_trans_sdm_file(source)
    trace_kind = detect_trace_file_kind(source)
    if trace_kind == "ide_trace":
        return _load_ide_trace_file(source)
    if trace_kind == "hac_trace":
        return _load_hac_trace_file(source)
    if trace_kind == "hac_trans":
        return _load_hac_trans_csv_file(source)
    if trace_kind == "cangfu_trace":
        return _load_cangfu_trace_file(source)
    table = load_numeric_table(path)
    time_s = _time_axis_from_table(table)
    sample_rate = _infer_sample_rate(time_s)
    channels: dict[str, np.ndarray] = {}
    for index, header in enumerate(table.headers):
        if _is_axis_header(header):
            continue
        column = table.data[:, index]
        if np.isfinite(column).any():
            channels[header] = np.asarray(column, dtype=float)
    if not channels and table.column_count >= 2:
        for index in range(1, table.column_count):
            channels[table.headers[index]] = np.asarray(table.data[:, index], dtype=float)
    table.kind = detect_trace_file_type(table)
    return TraceAnalysisFile(
        table=table,
        trace_kind=table.kind,
        time_s=time_s,
        channels=channels,
        sample_rate=sample_rate,
        channel_eu={name: 1.0 for name in channels},
    )


def load_numeric_table(path: str | Path) -> NumericTableFile:
    source = Path(path)
    text = _read_text(source)
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines:
        raise ValueError(f"{source.name} is empty.")

    header_index = _first_data_or_header_line(raw_lines)
    if header_index is None:
        raise ValueError(f"{source.name} does not contain numeric rows.")

    header_tokens = _split_fields(raw_lines[header_index])
    header_is_numeric = _tokens_are_numeric(header_tokens)
    if header_is_numeric:
        first_numeric_index = header_index
        headers = [f"Col {index + 1}" for index in range(len(header_tokens))]
    else:
        first_numeric_index = header_index + 1
        headers = normalize_headers(header_tokens)

    rows: list[list[float]] = []
    expected = len(headers)
    for line in raw_lines[first_numeric_index:]:
        tokens = _split_fields(line)
        if not tokens:
            continue
        values = [_parse_float(token) for token in tokens]
        if not any(np.isfinite(values)):
            continue
        if len(values) < expected:
            values.extend([np.nan] * (expected - len(values)))
        elif len(values) > expected:
            values = values[:expected]
        rows.append(values)

    if not rows:
        raise ValueError(f"{source.name} does not contain numeric data.")
    data = np.asarray(rows, dtype=float)
    headers = headers[: data.shape[1]]
    if len(headers) < data.shape[1]:
        headers.extend(f"Col {index + 1}" for index in range(len(headers), data.shape[1]))
    return NumericTableFile(path=source, name=source.name, headers=headers, data=data)


def curve_pairs_from_table(table: NumericTableFile) -> list[CurvePair]:
    pairs: list[CurvePair] = []
    used: set[int] = set()
    by_header = {header.lower(): index for index, header in enumerate(table.headers)}
    for index, header in enumerate(table.headers):
        lower = header.lower()
        if index in used or not lower.endswith("_x"):
            continue
        y_header = f"{header[:-2]}_Y"
        y_index = by_header.get(y_header.lower())
        if y_index is None:
            continue
        used.update({index, y_index})
        pairs.append(
            CurvePair(
                label=header[:-2],
                x=np.asarray(table.data[:, index], dtype=float),
                y=np.asarray(table.data[:, y_index], dtype=float),
                x_label=header,
                y_label=y_header,
            )
        )
    if pairs:
        return pairs

    if table.column_count >= 2:
        for index in range(0, table.column_count - 1, 2):
            x = np.asarray(table.data[:, index], dtype=float)
            y = np.asarray(table.data[:, index + 1], dtype=float)
            if not np.isfinite(x).any() or not np.isfinite(y).any():
                continue
            label = _pair_label_from_headers(table.headers[index], table.headers[index + 1], index // 2)
            pairs.append(
                CurvePair(
                    label=label,
                    x=x,
                    y=y,
                    x_label=table.headers[index],
                    y_label=table.headers[index + 1],
                )
            )
    return pairs


def build_log_groups(headers: Iterable[str]) -> dict[str, list[int]]:
    names = list(headers)
    match_names = [_canonical_log_header_name(header) for header in names]
    candidates: dict[str, list[int]] = {}

    def add_match(group_name: str, expr: str) -> None:
        indices = [
            index
            for index, header in enumerate(match_names)
            if not _is_axis_header(names[index]) and re.search(expr, header, re.I)
        ]
        if indices and group_name not in candidates:
            candidates[group_name] = indices

    add_match("BF Velocity", r"^(VEL_BF_|ACC_BF_)")
    add_match("SF Velocity", r"^(VEL_SF_|ACC_SF_)")
    add_match("PROX Position", r"^PROX_")
    add_match("PS Motion", r"^PS_(POS|ACC)_")
    for index in range(1, 5):
        add_match("Valve Output " + str(index), rf"^(VALUE{index}_|VALVE{index}_)")
    add_match("Valve Output All", r"^(VALUE\d+_|VALVE\d+_)")
    add_match("MT Actuator Force", r"^MT_AM\d+_")
    add_match("MT Temperature", r"^MT_TM_")
    add_match("INP FF", r"^INP[XYZ]FF")
    add_match("INP FB", r"^INP[XYZ]\d+FB|^INP[XYZ][A-Z0-9]*FB")
    add_match("INP PROX", r"^INP[HV]?PROX")
    add_match("INP Stage", r"^INP[XY](POS|ACC)")
    add_match("OUT Valve", r"^OUTV\d+")
    add_match("OUT Force", r"^OUT[XYZ][0-9]?_?N$|^OUT[A-Z0-9]+_?N$|^OUT[XYZ][0-9]?\(N\)|^OUT[A-Z0-9]+\(N\)")
    add_match("TEMP", r"^TEMP($|_)")
    add_match("PS Position", r"^PS_")
    add_match("VS Velocity", r"^VS_")
    add_match("VFS Filtered Velocity", r"^VFS_")
    add_match("TS Temperature", r"^TS_")
    add_match("WS/RS Stage", r"^(WS\d*_.*|RS_)")
    add_match("AC Actuator Force", r"^AC_")
    add_match("Reserved", r"^RESERVED")

    non_axis_indices = [index for index, header in enumerate(names) if not _is_axis_header(header)]
    if len(non_axis_indices) == 34:
        _append_legacy_log_groups(candidates, "34", non_axis_indices)
    elif len(non_axis_indices) == 55:
        _append_legacy_log_groups(candidates, "55", non_axis_indices)

    auto_groups: dict[str, list[int]] = {}
    for index, header in enumerate(names):
        if _is_axis_header(header):
            continue
        prefix = _group_prefix(header)
        if prefix:
            auto_groups.setdefault(f"Auto {prefix}", []).append(index)
    for group_name, indices in auto_groups.items():
        if len(indices) >= 2:
            candidates.setdefault(group_name, indices)

    category = _classify_log_category(match_names)
    allowed = _allowed_log_group_names(category)
    if allowed:
        visible = {name: candidates[name] for name in allowed if name in candidates}
        for name, indices in candidates.items():
            if not name.startswith("Legacy ") and name not in visible:
                visible[name] = indices
    else:
        visible = dict(candidates)
    if non_axis_indices:
        visible["All Channels"] = non_axis_indices
    return visible


def build_log_group_labels(headers: Iterable[str], groups: dict[str, list[int]]) -> dict[str, list[str]]:
    names = list(headers)
    labels: dict[str, list[str]] = {}
    for group_name, indices in groups.items():
        legacy_names = _legacy_log_display_names(group_name, len(indices))
        if legacy_names:
            labels[group_name] = [
                legacy_names[position] if position < len(legacy_names) else names[index]
                for position, index in enumerate(indices)
            ]
        else:
            labels[group_name] = [names[index] for index in indices]
    return labels


def _legacy_log_display_names(group_name: str, count: int) -> tuple[str, ...]:
    if group_name == "Legacy Position" and count >= 8:
        return ("V1", "V2", "V3", "V4", "H1", "H2", "H3", "H4")
    if group_name == "Legacy Motor Temperature" and count >= 8:
        return ("Temp V1", "Temp Y1", "Temp V2", "Temp X2", "Temp V3", "Temp Y3", "Temp V4", "Temp X4")
    definitions = {
        "Legacy Floor FF": ("XFF", "YFF", "ZFF"),
        "Legacy Velocity FB": ("Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB"),
        "Legacy Position": ("Z1", "Z2", "Z3", "H1", "H2", "H3"),
        "Legacy Stage FF": ("Xpos", "Xacc", "Ypos", "Yacc"),
        "Legacy Valve Output": ("Value V1", "Value V2", "Value V3"),
        "Legacy Motor Output": ("Y1 Motor", "Z1 Motor", "X2 Motor", "Z2 Motor", "Y3 Motor", "Z3 Motor"),
        "Legacy Motor Temperature": ("Y1 Temp", "Z1 Temp", "X2 Temp", "Z2 Temp", "Y3 Temp", "Z3 Temp"),
        "Legacy Table ACC": ("V1ACC", "V2ACC", "V3ACC", "H1ACC", "H2ACC", "H3ACC"),
        "Legacy FFACC": ("Xpos", "Xacc", "Ypos", "Yacc"),
        "Legacy Valve 1": ("VALVE1-V1V2", "VALVE1-V3V4", "VALVE1-X1X2", "VALVE1-Y1Y2"),
        "Legacy Valve 2": ("VALVE2-V1V2", "VALVE2-V3V4", "VALVE2-X1X2", "VALVE2-Y1Y2"),
        "Legacy Valve 3": ("VALVE3-V1V2", "VALVE3-V3V4", "VALVE3-X1X2", "VALVE3-Y1Y2"),
        "Legacy Valve 4": ("VALVE4-V1V2", "VALVE4-V3V4", "VALVE4-X1X2", "VALVE4-Y1Y2"),
        "Legacy Motor Force": ("MOTOR V1", "MOTOR V2", "MOTOR V3", "MOTOR V4"),
    }
    return definitions.get(group_name, ())


def _classify_log_category(headers: Iterable[str]) -> str:
    inp_score = 0
    s611a_score = 0
    vi_score = 0
    for header in headers:
        name = str(header or "")
        if re.search(r"^(INP|OUT|TEMP_)", name, re.I):
            inp_score += 1
        if re.search(r"^(VEL_BF_|ACC_BF_|VEL_SF_|ACC_SF_|PROX_|VALUE\d+_|VALVE\d+_|MT_)", name, re.I):
            s611a_score += 1
        if re.search(r"^(PS_|VS_|VFS_|TS_|WS\d*_?|RS_|AC_|RESERVED)", name, re.I):
            vi_score += 1
    scores = (inp_score, s611a_score, vi_score)
    best = max(scores)
    if best <= 0:
        return "generic"
    return ("legacy_inp_out", "legacy_611a", "vi_sensor_value")[scores.index(best)]


def _canonical_log_header_name(header: str) -> str:
    text = str(header or "").strip().strip('"')
    text = re.sub(r"\(\s*N\s*\)", "(N)", text, flags=re.I)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[-./]+", "_", text)
    text = re.sub(r"__+", "_", text)
    text = re.sub(r"\b(VALUE|VALVE)_(\d+)_", r"\1\2_", text, flags=re.I)
    return text.strip("_").upper()


def _allowed_log_group_names(category: str) -> tuple[str, ...]:
    if category == "legacy_inp_out":
        return ("INP FF", "INP FB", "INP PROX", "INP Stage", "OUT Valve", "OUT Force", "TEMP", "All Channels")
    if category == "legacy_611a":
        return (
            "BF Velocity",
            "SF Velocity",
            "PROX Position",
            "PS Motion",
            "Valve Output 1",
            "Valve Output 2",
            "Valve Output 3",
            "Valve Output 4",
            "Valve Output All",
            "MT Actuator Force",
            "MT Temperature",
            "All Channels",
        )
    if category == "vi_sensor_value":
        return (
            "PS Position",
            "VS Velocity",
            "VFS Filtered Velocity",
            "TS Temperature",
            "WS/RS Stage",
            "AC Actuator Force",
            "Reserved",
            "All Channels",
        )
    return ()


def _append_legacy_log_groups(groups: dict[str, list[int]], profile: str, non_axis_indices: list[int]) -> None:
    if profile == "34":
        definitions = {
            "Legacy Floor FF": range(0, 3),
            "Legacy Velocity FB": range(3, 9),
            "Legacy Position": range(9, 15),
            "Legacy Stage FF": range(15, 19),
            "Legacy Valve Output": range(19, 22),
            "Legacy Motor Output": range(22, 28),
            "Legacy Motor Temperature": range(28, 34),
        }
    else:
        definitions = {
            "Legacy Floor FF": range(0, 3),
            "Legacy Table ACC": range(3, 9),
            "Legacy Position": range(9, 17),
            "Legacy FFACC": range(17, 21),
            "Legacy Valve 1": range(21, 25),
            "Legacy Valve 2": range(25, 29),
            "Legacy Valve 3": range(29, 33),
            "Legacy Valve 4": range(33, 37),
            "Legacy Motor Force": (37, 39, 41, 43),
            "Legacy Motor Temperature": range(45, 53),
        }
    for name, positions in definitions.items():
        indices = [non_axis_indices[position] for position in positions if position < len(non_axis_indices)]
        if indices:
            groups.setdefault(name, indices)


def detect_trace_file_type(table: NumericTableFile) -> str:
    suffix = table.path.suffix.lower()
    name = table.path.name.lower()
    lower_headers = " ".join(table.headers).lower()
    if suffix in {".rpt", ".para"} or "cangfu" in name or "trace_data_file" in name or _looks_like_cangfu_sidecar_lines(table.headers):
        return "cangfu_trace"
    if suffix == ".csv" or "hac" in lower_headers or "period" in lower_headers:
        return "hac_trace"
    return "ide_trace"


def detect_trace_file_kind(path: str | Path) -> str:
    source = Path(path)
    name_lower = source.name.lower()
    if source.suffix.lower() == ".sdm":
        return "ide_trans"
    if source.suffix.lower() in {".rpt", ".para"} or "cangfu" in name_lower or "trace_data_file" in name_lower:
        return "cangfu_trace"
    if source.suffix.lower() == ".mat":
        return "cangfu_trace" if _looks_like_cangfu_mat_file(source) else ""
    if source.suffix.lower() == ".csv":
        if _looks_like_hac_trans_csv(source):
            return "hac_trans"
        return "hac_trace"
    lines = _read_text(source).splitlines()
    if _looks_like_cangfu_trace_lines(lines) or _looks_like_cangfu_sidecar_lines(lines):
        return "cangfu_trace"
    if len(lines) >= 5:
        checks = (
            re.search(r"^\s*sample frequency\s*:", lines[0], re.I),
            re.search(r"^\s*undersample\s*:", lines[1], re.I),
            re.search(r"^\s*signal num\s*:", lines[2], re.I),
            re.search(r"^\s*buffer length\s*:", lines[3], re.I),
        )
        if all(checks):
            return "ide_trace"
    return ""


def _load_ide_trans_sdm_file(path: Path) -> TraceAnalysisFile:
    data = path.read_bytes()
    if b"SI_1.0" not in data or b"EI_1.0" not in data or b"CB_1.0" not in data:
        raise ValueError("SDM file does not contain the expected SI/EI/CB records.")

    params = _sdm_header_params(data)
    n_samples = int(params["n_samples"])
    n_frequency = n_samples // 2
    header_df_hz = float(params["df_hz"])
    sample_frequency_hz = params.get("sample_frequency_hz")
    update_rate = params.get("update_rate")
    if sample_frequency_hz is not None and update_rate is not None:
        sample_rate = float(sample_frequency_hz) / float(update_rate)
        df_hz = sample_rate / float(n_samples)
    else:
        df_hz = header_df_hz
        sample_rate = df_hz * n_samples
    blocks = [
        _sdm_extract_cb_records(data, match.start(), n_samples)
        for match in re.finditer(rb"CB_1\.0", data)
    ]

    time_input = None
    time_response = None
    time_spectrum_input = None
    time_spectrum_response = None
    time_source = ""
    input_name = "Excitation"
    response_name = "Response"
    autospectrum_input = None
    autospectrum_response = None
    transfer = None
    coherence = None

    for match in re.finditer(rb"\x02SB", data):
        try:
            sample_block = _sdm_extract_sb_records(data, match.start())
        except ValueError:
            continue
        sample_records = sample_block["records"]
        if len(sample_records) < 2:
            continue
        time_input = np.asarray(sample_records[0]["data"], dtype=float).copy()
        time_response = np.asarray(sample_records[1]["data"], dtype=float).copy()
        time_source = "sdm_raw"
        break

    for block in blocks:
        records = block["records"]
        excitation = next((record for record in records if _sdm_is_excitation_record(record)), None)
        response = _sdm_primary_response(records)
        if excitation is not None and response is not None:
            excitation_values = excitation["data"]
            response_values = response["data"]
            input_name = str(excitation["name"] or input_name)
            response_name = str(response["name"] or response_name)
            if _sdm_has_interleaved_imaginary(excitation_values) and _sdm_has_interleaved_imaginary(response_values):
                if time_spectrum_input is None:
                    time_spectrum_input = np.asarray(excitation_values[0::2], dtype=float) + 1j * np.asarray(
                        excitation_values[1::2], dtype=float
                    )
                    time_spectrum_response = np.asarray(response_values[0::2], dtype=float) + 1j * np.asarray(
                        response_values[1::2], dtype=float
                    )
                if time_input is None:
                    time_input = _sdm_one_sided_spectrum_to_time(time_spectrum_input, n_samples)
                    time_response = _sdm_one_sided_spectrum_to_time(time_spectrum_response, n_samples)
                    time_source = "spectrum_ifft"
            elif _sdm_is_real_interleaved_spectrum(excitation_values) and _sdm_is_real_interleaved_spectrum(response_values):
                if autospectrum_input is None:
                    autospectrum_input = np.asarray(excitation_values[0::2], dtype=float).copy()
                    autospectrum_response = np.asarray(response_values[0::2], dtype=float).copy()
            continue

        if response is None:
            continue
        values = np.asarray(response["data"], dtype=float)
        if _sdm_has_interleaved_imaginary(values):
            if transfer is None:
                transfer = values[0::2] + 1j * values[1::2]
                response_name = str(response["name"] or response_name)
        elif _sdm_is_real_interleaved_spectrum(values):
            candidate = values[0::2]
            finite = candidate[np.isfinite(candidate)]
            if finite.size and float(np.nanmin(finite)) >= -1e-9 and float(np.nanmax(finite)) <= 1.000001:
                if coherence is None:
                    coherence = np.asarray(candidate, dtype=float).copy()

    available = any(
        value is not None
        for value in (time_input, time_response, autospectrum_input, autospectrum_response, transfer, coherence)
    )
    if not available:
        raise ValueError("SDM file does not contain supported IDE transfer records.")

    frequency_hz = np.arange(n_frequency, dtype=float) * df_hz
    if time_input is not None and time_response is not None:
        count = min(time_input.size, time_response.size, n_samples)
        time_input = np.asarray(time_input[:count], dtype=float)
        time_response = np.asarray(time_response[:count], dtype=float)
        time_s = np.arange(count, dtype=float) / sample_rate
        headers = normalize_headers([input_name, response_name], 2)
        table_data = np.column_stack((time_input, time_response))
        channels = {headers[0]: time_input, headers[1]: time_response}
    else:
        time_s = np.array([], dtype=float)
        headers = normalize_headers([input_name, response_name], 2)
        table_data = np.empty((0, 2), dtype=float)
        channels = {}

    metadata: dict[str, object] = {
        "sample_index": np.arange(1, time_s.size + 1, dtype=float),
        "ide_trans_source": "sdm",
        "ide_trans_axis": path.stem,
        "ide_trans_sample_count": n_samples,
        "ide_trans_df_hz": df_hz,
        "ide_trans_header_df_hz": header_df_hz,
        "ide_trans_sample_frequency_hz": sample_frequency_hz,
        "ide_trans_update_rate": update_rate,
        "ide_trans_inferred_update_rate_hz": sample_rate,
        "ide_trans_frequency_hz": frequency_hz,
        "ide_trans_channel_index": params.get("channel_index"),
        "ide_trans_input_name": headers[0],
        "ide_trans_response_name": headers[1],
        "ide_trans_time_source": time_source,
    }
    if autospectrum_input is not None:
        metadata["ide_trans_autospectrum_input"] = np.asarray(autospectrum_input[:n_frequency], dtype=float)
    if autospectrum_response is not None:
        metadata["ide_trans_autospectrum_response"] = np.asarray(autospectrum_response[:n_frequency], dtype=float)
    if transfer is not None:
        transfer = np.asarray(transfer[:n_frequency], dtype=complex)
        metadata["ide_trans_transfer"] = transfer
        metadata["ide_trans_magnitude_db"] = 20.0 * np.log10(np.maximum(np.abs(transfer), 1e-30))
        metadata["ide_trans_phase_deg"] = np.degrees(np.angle(transfer))
    if coherence is not None:
        metadata["ide_trans_coherence"] = np.asarray(coherence[:n_frequency], dtype=float)
    if time_spectrum_input is not None:
        metadata["ide_trans_time_spectrum_input"] = np.asarray(time_spectrum_input[:n_frequency], dtype=complex)
    if time_spectrum_response is not None:
        metadata["ide_trans_time_spectrum_response"] = np.asarray(time_spectrum_response[:n_frequency], dtype=complex)

    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=table_data,
        kind="ide_trans",
        metadata=metadata,
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="ide_trans",
        time_s=time_s,
        channels=channels,
        sample_rate=sample_rate,
        channel_eu={name: 1.0 for name in channels},
    )


def _looks_like_hac_trans_csv(path: Path) -> bool:
    try:
        lines = _read_text(path).splitlines()
    except OSError:
        return False
    data_index = next((index for index, line in enumerate(lines[:80]) if line.strip().lower() == "data:"), None)
    if data_index is None or data_index + 1 >= len(lines):
        return False
    headers = [token.strip().lower() for token in _csv_split(lines[data_index + 1], delimiter=",")]
    required = {"frequency", "magnitude response", "phase response", "coherence"}
    return required.issubset(set(headers)) and len(headers) >= 6


def _load_hac_trans_csv_file(path: Path) -> TraceAnalysisFile:
    lines = _read_text(path).splitlines()
    data_index = next((index for index, line in enumerate(lines) if line.strip().lower() == "data:"), None)
    if data_index is None or data_index + 1 >= len(lines):
        raise ValueError("HAC transfer CSV does not contain a Data section.")

    raw_headers = _csv_split(lines[data_index + 1], delimiter=",")
    headers = normalize_headers(raw_headers)
    lower_headers = [header.strip().lower() for header in headers]
    required = ("frequency", "magnitude response", "phase response", "coherence")
    if len(headers) < 6 or any(name not in lower_headers for name in required):
        raise ValueError("HAC transfer CSV is missing frequency, magnitude, phase, or coherence columns.")

    rows: list[list[float]] = []
    for line in lines[data_index + 2 :]:
        if not line.strip():
            continue
        tokens = _csv_split(line, delimiter=",")
        if len(tokens) < len(headers):
            continue
        values = [_parse_float(token) for token in tokens[: len(headers)]]
        if all(np.isfinite(values)):
            rows.append(values)
    if not rows:
        raise ValueError("HAC transfer CSV data block is empty.")

    data = np.asarray(rows, dtype=float)
    header_map = {name: index for index, name in enumerate(lower_headers)}
    frequency = np.asarray(data[:, header_map["frequency"]], dtype=float)
    magnitude_db = np.asarray(data[:, header_map["magnitude response"]], dtype=float)
    phase_deg = np.asarray(data[:, header_map["phase response"]], dtype=float)
    coherence = np.asarray(data[:, header_map["coherence"]], dtype=float)
    input_index = 0
    response_index = 1
    input_name = headers[input_index]
    response_name = headers[response_index]
    input_values = np.asarray(data[:, input_index], dtype=float)
    response_values = np.asarray(data[:, response_index], dtype=float)

    sample_frequency = next(
        (
            _parse_header_scalar(line, "Sample frequency")
            for line in lines[:data_index]
            if re.search(r"^\s*Sample frequency\s*:", line, re.I)
        ),
        float("nan"),
    )
    if not np.isfinite(sample_frequency) or sample_frequency <= 0.0:
        positive_frequency = frequency[np.isfinite(frequency) & (frequency > 0.0)]
        sample_frequency = 2.0 * float(positive_frequency[-1]) if positive_frequency.size else 1.0
    time_s = np.arange(data.shape[0], dtype=float) / float(sample_frequency)
    transfer = np.power(10.0, magnitude_db / 20.0) * np.exp(1j * np.radians(phase_deg))

    header_metadata: dict[str, object] = {}
    for line in lines[:data_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        header_metadata[key.strip()] = value.strip()
    df_hz = float(np.nanmedian(np.diff(frequency))) if frequency.size > 1 else 0.0
    metadata: dict[str, object] = {
        "sample_index": np.arange(1, data.shape[0] + 1, dtype=float),
        "hac_trans_source": "csv",
        "hac_trans_header": header_metadata,
        "ide_trans_axis": path.stem,
        "ide_trans_df_hz": df_hz,
        "ide_trans_frequency_hz": frequency,
        "ide_trans_input_name": input_name,
        "ide_trans_response_name": response_name,
        "ide_trans_transfer": transfer,
        "ide_trans_magnitude_db": magnitude_db,
        "ide_trans_phase_deg": phase_deg,
        "ide_trans_coherence": coherence,
    }
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=[input_name, response_name],
        data=np.column_stack((input_values, response_values)),
        kind="hac_trans",
        metadata=metadata,
    )
    channels = {input_name: input_values, response_name: response_values}
    return TraceAnalysisFile(
        table=table,
        trace_kind="hac_trans",
        time_s=time_s,
        channels=channels,
        sample_rate=float(sample_frequency),
        channel_eu={name: 1.0 for name in channels},
    )


def _sdm_header_params(data: bytes) -> dict[str, object]:
    si = data.find(b"SI_1.0")
    ei = data.find(b"EI_1.0")
    n_samples = 2048
    update_rate = None
    if si >= 0 and si + 14 <= len(data):
        _version, candidate = struct.unpack_from("<2I", data, si + 6)
        if 64 <= int(candidate) <= 1_048_576 and int(candidate) % 2 == 0:
            n_samples = int(candidate)
        if si + 22 <= len(data):
            candidate = int(struct.unpack_from("<I", data, si + 18)[0])
            if 1 <= candidate <= 1_000_000:
                update_rate = candidate

    sample_frequency_hz = None
    if data[:3] == b"\x02CI":
        try:
            offset = 3
            for _index in range(3):
                length = int(data[offset])
                offset += 1 + length
                if _index == 0:
                    offset += 4
            candidate = int(struct.unpack_from("<I", data, offset)[0])
            if 1 <= candidate <= 100_000_000:
                sample_frequency_hz = float(candidate)
        except (IndexError, struct.error):
            sample_frequency_hz = None

    df_hz = 0.05
    if ei >= 0:
        direct_offset = ei + 70
        if direct_offset + 8 <= len(data):
            candidate = float(struct.unpack_from("<d", data, direct_offset)[0])
            if np.isfinite(candidate) and 1e-6 < candidate < 1000.0:
                df_hz = candidate
        if df_hz == 0.05:
            sb = data.find(b"\x02SB", ei)
            end = sb if sb > ei else min(len(data), ei + 120)
            for offset in range(ei + 6, max(ei + 6, end - 7), 4):
                candidate = float(struct.unpack_from("<d", data, offset)[0])
                if np.isfinite(candidate) and 1e-6 < candidate < 1.0:
                    df_hz = candidate
                    break

    channel_index = None
    if len(data) >= 144:
        candidate = int(struct.unpack_from("<I", data, 140)[0])
        if 0 <= candidate <= 32:
            channel_index = candidate
    return {
        "n_samples": n_samples,
        "df_hz": df_hz,
        "sample_frequency_hz": sample_frequency_hz,
        "update_rate": update_rate,
        "channel_index": channel_index,
    }


def _sdm_extract_cb_records(data: bytes, block_offset: int, n_samples: int) -> dict[str, object]:
    position = block_offset + 6
    if position + 8 > len(data):
        raise ValueError("Truncated SDM CB block header.")
    record_count, flags = struct.unpack_from("<II", data, position)
    position += 8
    if record_count < 1 or record_count > 256:
        raise ValueError(f"Invalid SDM CB record count: {record_count}.")
    records: list[dict[str, object]] = []
    payload_size = int(n_samples) * 8
    for _index in range(int(record_count)):
        if position >= len(data):
            raise ValueError("Truncated SDM CB record name.")
        name_length = int(data[position])
        name_end = position + 1 + name_length
        header_end = name_end + 20
        data_end = header_end + payload_size
        if data_end > len(data):
            raise ValueError("Truncated SDM CB record payload.")
        name = data[position + 1 : name_end].decode("ascii", errors="replace")
        header = struct.unpack_from("<5I", data, name_end)
        values = np.frombuffer(data, dtype="<f8", count=n_samples, offset=header_end).copy()
        records.append({"name": name, "header": header, "data": values})
        position = data_end
    return {"record_count": int(record_count), "flags": int(flags), "records": records}


def _sdm_extract_sb_records(data: bytes, block_offset: int) -> dict[str, object]:
    position = block_offset + 3
    if position + 12 > len(data):
        raise ValueError("Truncated SDM sample block header.")
    record_count, sample_count, timestamp = struct.unpack_from("<3I", data, position)
    position += 12
    if record_count < 1 or record_count > 256:
        raise ValueError(f"Invalid SDM sample record count: {record_count}.")
    if sample_count < 1 or sample_count > 1_048_576:
        raise ValueError(f"Invalid SDM sample count: {sample_count}.")

    records: list[dict[str, object]] = []
    payload_size = int(sample_count) * 8
    for _index in range(int(record_count)):
        header_end = position + 16
        data_end = header_end + payload_size
        if data_end > len(data):
            raise ValueError("Truncated SDM sample record payload.")
        header = struct.unpack_from("<4I", data, position)
        values = np.frombuffer(data, dtype="<f8", count=sample_count, offset=header_end).copy()
        records.append({"header": header, "data": values})
        position = data_end
    return {
        "record_count": int(record_count),
        "sample_count": int(sample_count),
        "timestamp": int(timestamp),
        "records": records,
    }


def _sdm_is_excitation_record(record: dict[str, object]) -> bool:
    return str(record.get("name", "")).lower().startswith("excit") and np.count_nonzero(record["data"]) > 10


def _sdm_primary_response(records: list[dict[str, object]]) -> dict[str, object] | None:
    candidates = [
        record
        for record in records
        if np.count_nonzero(record["data"]) > 10
        and not str(record.get("name", "")).lower().startswith("excit")
        and "raw input" not in str(record.get("name", "")).lower()
    ]
    return candidates[0] if candidates else None


def _sdm_has_interleaved_imaginary(values: np.ndarray) -> bool:
    odd = np.asarray(values, dtype=float)[1::2]
    return np.count_nonzero(odd) > max(10, odd.size // 20)


def _sdm_is_real_interleaved_spectrum(values: np.ndarray) -> bool:
    array = np.asarray(values, dtype=float)
    even = array[0::2]
    odd = array[1::2]
    return np.count_nonzero(even) > max(10, even.size // 20) and np.count_nonzero(odd) <= max(10, odd.size // 100)


def _sdm_one_sided_spectrum_to_time(spectrum: np.ndarray, n_samples: int) -> np.ndarray:
    count = max(int(n_samples), 2)
    bins = np.zeros(count // 2 + 1, dtype=complex)
    source = np.asarray(spectrum, dtype=complex).reshape(-1)
    copied = min(source.size, bins.size)
    bins[:copied] = source[:copied]
    bins[-1] = 0.0
    return np.asarray(np.fft.irfft(bins, n=count) * (count / 2.0), dtype=float)


def _looks_like_cangfu_mat_file(path: Path) -> bool:
    try:
        from scipy.io import whosmat

        names = {name for name, _shape, _kind in whosmat(path)}
    except Exception:
        return False
    return {"trace_data", "trace_info"}.issubset(names)


def _looks_like_cangfu_sidecar_lines(lines: Iterable[str]) -> bool:
    first_lines = [str(line or "").strip().lower() for line in list(lines)[:16] if str(line or "").strip()]
    if not first_lines:
        return False
    if first_lines[0] in {"open loop transfer function test", "independent tracing"}:
        return True
    if any(re.match(r"trace\d+\s*:", line) for line in first_lines):
        return True
    if any("sampling frequency" in line for line in first_lines) and any("trace" in line for line in first_lines):
        return True
    return False


def _looks_like_cangfu_trace_lines(lines: list[str]) -> bool:
    first_lines = [line.strip() for line in lines[:12] if line.strip()]
    if not first_lines:
        return False
    first = first_lines[0].lower()
    if "graphic_viewer" in first or "trace data file" in first:
        return True
    if len(first_lines) >= 8 and re.match(r"^\d{8}_\d{6}$", first_lines[1] or ""):
        if _fixed_numeric_row(first_lines[7], 3) is not None:
            return True
    return False


def normalize_headers(raw_headers: Iterable[str], n_expected: int | None = None) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_headers):
        text = str(raw or "").strip().strip('"')
        if not text:
            text = f"Col {index + 1}"
        count = seen.get(text, 0)
        seen[text] = count + 1
        if count:
            text = f"{text}_{count + 1}"
        headers.append(text)
    if n_expected is not None:
        while len(headers) < n_expected:
            headers.append(f"Col {len(headers) + 1}")
        headers = headers[:n_expected]
    return headers


def _load_ide_trace_file(path: Path) -> TraceAnalysisFile:
    lines = _read_text(path).splitlines()
    if len(lines) < 6:
        raise ValueError("IDE trace file is too short.")
    sample_frequency = _parse_header_scalar(lines[0], "sample frequency")
    undersample = _parse_header_scalar(lines[1], "undersample")
    signal_num = int(round(_parse_header_scalar(lines[2], "signal num")))
    buffer_length = int(round(_parse_header_scalar(lines[3], "Buffer length")))
    if not np.isfinite(sample_frequency) or sample_frequency <= 0.0:
        raise ValueError("Invalid sample frequency in IDE header.")
    if not np.isfinite(undersample) or undersample <= 0.0:
        raise ValueError("Invalid undersample value in IDE header.")
    if signal_num < 1 or buffer_length < 1:
        raise ValueError("Invalid IDE trace channel or sample count.")

    headers = normalize_headers(_split_semicolon_line(lines[4])[:signal_num], signal_num)
    rows: list[list[float]] = []
    for row_number, line in enumerate(lines[5:], start=6):
        if not line.strip():
            continue
        values = [_parse_float(token) for token in str(line).split()]
        if len(values) != signal_num or not all(np.isfinite(values)):
            raise ValueError(f"Numeric row {row_number} has {len(values)} columns; expected {signal_num}.")
        rows.append(values)
    if len(rows) != buffer_length:
        raise ValueError(f"Data row count {len(rows)} does not match Buffer length {buffer_length}.")

    data = np.asarray(rows, dtype=float)
    fs = float(sample_frequency) / float(undersample)
    time_s = np.arange(data.shape[0], dtype=float) / fs
    channel_eu = {header: _default_ide_eu(header) for header in headers}
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="ide_trace",
        metadata={
            "sample_frequency": float(sample_frequency),
            "undersample": float(undersample),
            "signal_num": signal_num,
            "buffer_length": buffer_length,
            "sample_index": np.arange(1, data.shape[0] + 1, dtype=float),
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="ide_trace",
        time_s=time_s,
        channels={header: np.asarray(data[:, index], dtype=float) for index, header in enumerate(headers)},
        sample_rate=fs,
        channel_eu=channel_eu,
    )


def _load_hac_trace_file(path: Path) -> TraceAnalysisFile:
    lines = _read_text(path).splitlines()
    if len(lines) < 3:
        raise ValueError("HAC trace file is too short.")
    period_tokens = _csv_split(lines[0], delimiter=",")
    if len(period_tokens) < 2 or period_tokens[0].strip().lower() != "period(ms)":
        raise ValueError("Cannot find valid Period(ms) header.")
    period_ms = _parse_float(period_tokens[1])
    if not np.isfinite(period_ms) or period_ms <= 0.0:
        raise ValueError("Invalid Period(ms) value.")

    header_tokens = _csv_split(lines[1], delimiter=",")
    if len(header_tokens) < 2 or header_tokens[0].strip().lower() != "time":
        raise ValueError("Cannot find valid HAC channel header line.")
    headers = normalize_headers([token for token in header_tokens[1:] if token.strip()])
    if not headers:
        raise ValueError("No HAC channels found.")

    time_text: list[str] = []
    rows: list[list[float]] = []
    for line in lines[2:]:
        if not line.strip():
            continue
        tokens = _csv_split(line, delimiter=",")
        while tokens and not tokens[-1].strip():
            tokens.pop()
        if len(tokens) < len(headers) + 1:
            continue
        values = [_parse_float(token) for token in tokens[1 : len(headers) + 1]]
        if not all(np.isfinite(values)):
            continue
        time_text.append(tokens[0].strip())
        rows.append(values)
    if not rows:
        raise ValueError("HAC data block is empty.")

    data = np.asarray(rows, dtype=float)
    elapsed = _hac_elapsed_seconds(time_text, period_ms, data.shape[0])
    fs = 1000.0 / float(period_ms)
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="hac_trace",
        metadata={
            "period_ms": float(period_ms),
            "raw_time_text": time_text,
            "sample_index": np.arange(1, data.shape[0] + 1, dtype=float),
            "hac_groups": build_hac_groups(headers),
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="hac_trace",
        time_s=elapsed,
        channels={header: np.asarray(data[:, index], dtype=float) for index, header in enumerate(headers)},
        sample_rate=fs,
        channel_eu={header: 1.0 for header in headers},
    )


def _load_cangfu_trace_file(path: Path) -> TraceAnalysisFile:
    if path.suffix.lower() == ".mat":
        return _load_cangfu_mat_trace_file(path, sidecar_path=path.with_suffix(".txt"))
    if path.suffix.lower() == ".txt" and _looks_like_cangfu_sidecar_lines(_read_text(path).splitlines()):
        mat_path = path.with_suffix(".mat")
        if mat_path.exists():
            return _load_cangfu_mat_trace_file(mat_path, sidecar_path=path)
    lines = _read_text(path).splitlines()
    expanded = _load_cangfu_index_file(path, lines)
    if expanded is not None:
        return expanded

    numeric_start = -1
    first_row: list[float] | None = None
    for index, line in enumerate(lines):
        row = _any_numeric_row(line)
        if row is not None and len(row) >= 2:
            numeric_start = index
            first_row = row
            break
    if numeric_start < 0 or first_row is None:
        raise ValueError("Cangfu Trace file does not contain numeric trace rows.")

    n_cols = len(first_row)
    rows: list[list[float]] = []
    for line in lines[numeric_start:]:
        row = _fixed_numeric_row(line, n_cols)
        if row is not None:
            rows.append(row)
    if not rows:
        raise ValueError("Cangfu Trace data block is empty.")

    data_full = np.asarray(rows, dtype=float)
    sample_index = data_full[:, 0]
    data = data_full[:, 1:] if data_full.shape[1] > 1 else data_full
    headers = _cangfu_channel_headers(lines[:numeric_start], data.shape[1])
    fs = _cangfu_sample_rate(lines[:numeric_start], sample_index)
    time_s = sample_index / fs if fs > 0.0 else np.arange(data.shape[0], dtype=float)
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="cangfu_trace",
        metadata={
            "sample_index": sample_index,
            "cangfu_source": "data_file",
            "raw_header": "\n".join(lines[:numeric_start]),
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="cangfu_trace",
        time_s=time_s,
        channels={header: np.asarray(data[:, index], dtype=float) for index, header in enumerate(headers)},
        sample_rate=fs,
        channel_eu={header: 1.0 for header in headers},
    )


def _load_cangfu_mat_trace_file(path: Path, *, sidecar_path: Path | None = None) -> TraceAnalysisFile:
    try:
        from scipy.io import loadmat
    except Exception as exc:
        raise RuntimeError("Missing optional dependency 'scipy'.") from exc

    mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    if "trace_data" not in mat or "trace_info" not in mat:
        raise ValueError("Cangfu MAT file must contain trace_data and trace_info.")
    trace_info = mat["trace_info"]
    info = _cangfu_trace_info_dict(trace_info)
    sidecar_lines = _read_text(sidecar_path).splitlines() if sidecar_path is not None and sidecar_path.exists() else []
    sidecar = _cangfu_sidecar_metadata(sidecar_lines)
    trace_type = str(info.get("type") or sidecar.get("type") or "").strip().lower()
    trace_data = np.asarray(mat["trace_data"], dtype=object).ravel()

    if trace_type in {"independent", "independent tracing"} or _cangfu_trace_cells_are_independent(trace_data):
        return _cangfu_independent_mat_dataset(path, trace_data, info, sidecar)
    return _cangfu_loop_tf_mat_dataset(path, trace_data, info, sidecar)


def _cangfu_trace_info_dict(info_obj: object) -> dict[str, object]:
    info: dict[str, object] = {}
    if hasattr(info_obj, "_fieldnames"):
        for field in info_obj._fieldnames:
            info[field] = _mat_scalar(getattr(info_obj, field))
    return info


def _mat_scalar(value: object) -> object:
    if isinstance(value, np.ndarray):
        if value.size == 1:
            return _mat_scalar(value.item())
        return value
    if isinstance(value, np.generic):
        return value.item()
    return value


def _cangfu_sidecar_metadata(lines: list[str]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    clean = [line.strip() for line in lines if line.strip()]
    if not clean:
        return metadata
    first = clean[0].strip()
    if first:
        metadata["title"] = first
    if first.lower() == "independent tracing":
        metadata["type"] = "independent"
    elif "transfer function" in first.lower():
        metadata["type"] = "looptf"
    traces: list[str] = []
    for line in clean[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            key_norm = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if re.match(r"trace\d+$", key_norm):
                traces.append(value)
            else:
                metadata[key_norm] = value
    if traces:
        metadata["trace_labels"] = traces
    return metadata


def _cangfu_trace_cells_are_independent(cells: np.ndarray) -> bool:
    for item in cells:
        if hasattr(item, "_fieldnames") and "trace" in getattr(item, "_fieldnames", []):
            return True
    return False


def _cangfu_independent_mat_dataset(
    path: Path,
    trace_data: np.ndarray,
    info: dict[str, object],
    sidecar: dict[str, object],
) -> TraceAnalysisFile:
    channels: dict[str, np.ndarray] = {}
    fs_values: list[float] = []
    labels = [str(label) for label in sidecar.get("trace_labels", [])] if isinstance(sidecar.get("trace_labels"), list) else []
    for index, item in enumerate(trace_data):
        if not hasattr(item, "_fieldnames") or "trace" not in getattr(item, "_fieldnames", []):
            continue
        values = np.asarray(getattr(item, "trace"), dtype=float).ravel()
        if values.size < 1 or not np.any(np.isfinite(values)):
            continue
        fs = _parse_positive_float(getattr(item, "fs", np.nan))
        if np.isfinite(fs):
            fs_values.append(float(fs))
        label = str(getattr(item, "signal", "") or "").strip()
        if not label or label.lower() == "undefined":
            label = labels[index] if index < len(labels) else f"trace{index + 1:02d}"
        channels[label] = values
    if not channels:
        raise ValueError("Cangfu independent MAT file does not contain valid trace channels.")
    min_count = min(values.size for values in channels.values())
    headers = normalize_headers(channels.keys(), len(channels))
    normalized_channels = {
        header: np.asarray(channels[old_name][:min_count], dtype=float)
        for header, old_name in zip(headers, channels.keys())
    }
    fs = float(fs_values[0]) if fs_values else _parse_positive_float(info.get("fs", 1.0))
    if not np.isfinite(fs) or fs <= 0.0:
        fs = 1.0
    data = np.column_stack([normalized_channels[header] for header in headers])
    sample_index = np.arange(1, min_count + 1, dtype=float)
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="cangfu_trace",
        metadata={
            "sample_index": sample_index,
            "cangfu_source": "mat_independent",
            "trace_info": info,
            "sidecar": sidecar,
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="cangfu_trace",
        time_s=np.arange(min_count, dtype=float) / fs,
        channels=normalized_channels,
        sample_rate=fs,
        channel_eu={header: 1.0 for header in headers},
    )


def _cangfu_loop_tf_mat_dataset(
    path: Path,
    trace_data: np.ndarray,
    info: dict[str, object],
    sidecar: dict[str, object],
) -> TraceAnalysisFile:
    fs = _parse_positive_float(info.get("fs", sidecar.get("sampling_frequency", 1.0)))
    if not np.isfinite(fs) or fs <= 0.0:
        fs = 1.0

    if trace_data.size < 2:
        raise ValueError("Cangfu loop transfer MAT file must contain excitation and response traces.")
    excitation = _cangfu_loop_trace_matrix(trace_data[0])
    response = _cangfu_loop_trace_matrix(trace_data[1])
    if excitation.size == 0 or response.size == 0:
        raise ValueError("Cangfu loop transfer MAT file does not contain valid excitation/response arrays.")

    sample_count = min(excitation.shape[0], response.shape[0])
    repeat_count = min(excitation.shape[1], response.shape[1])
    if sample_count < 8 or repeat_count < 1:
        raise ValueError("Cangfu loop transfer MAT file does not contain enough samples for transfer estimation.")
    excitation = excitation[:sample_count, :repeat_count]
    response = response[:sample_count, :repeat_count]

    freqs, mean_frf, coherence = _compute_cangfu_get_looptf(excitation, response, fs)
    if freqs.size == 0:
        raise ValueError("Cangfu loop transfer MAT file could not produce a transfer function curve.")
    valid = (
        np.isfinite(freqs)
        & np.isfinite(np.real(mean_frf))
        & np.isfinite(np.imag(mean_frf))
        & np.isfinite(coherence)
        & (freqs > 0.0)
    )
    freqs = freqs[valid]
    mean_frf = mean_frf[valid]
    coherence = coherence[valid]
    if freqs.size == 0:
        raise ValueError("Cangfu loop transfer MAT file produced no valid transfer function points.")

    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(mean_frf), 1e-300))
    phase_deg = np.angle(mean_frf, deg=True)
    loop_label = _cangfu_loop_transfer_label(info, sidecar)
    transfer_pair = CurvePair(loop_label, freqs, magnitude_db, "Frequency (Hz)", "Magnitude (dB)")
    phase_pair = CurvePair(f"{loop_label} 相位", freqs, phase_deg, "Frequency (Hz)", "Phase (deg)")
    coherence_pair = CurvePair(f"{loop_label} 相干", freqs, coherence, "Frequency (Hz)", "Coherence")
    data = np.column_stack([freqs, magnitude_db, phase_deg, coherence])
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=["Frequency_Hz", loop_label, "Phase_deg", "Coherence"],
        data=data,
        kind="cangfu_trace",
        metadata={
            "sample_index": np.arange(1, freqs.size + 1, dtype=float),
            "cangfu_source": "mat_loop_tf",
            "cangfu_mode": "transfer",
            "cangfu_transfer_algorithm": "get_looptf",
            "cangfu_transfer_pairs": [transfer_pair],
            "cangfu_phase_pairs": [phase_pair],
            "cangfu_coherence_pairs": [coherence_pair],
            "cangfu_average_count": repeat_count,
            "cangfu_raw_sample_count": sample_count,
            "trace_info": info,
            "sidecar": sidecar,
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="cangfu_trace",
        time_s=np.array([], dtype=float),
        channels={},
        sample_rate=fs,
        channel_eu={},
    )


def _compute_cangfu_get_looptf(
    excitation: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_matrix = np.asarray(excitation, dtype=float)
    y_matrix = np.asarray(response, dtype=float)
    if x_matrix.ndim != 2 or y_matrix.ndim != 2:
        return np.array([], dtype=float), np.array([], dtype=complex), np.array([], dtype=float)
    sample_count = min(x_matrix.shape[0], y_matrix.shape[0])
    repeat_count = min(x_matrix.shape[1], y_matrix.shape[1])
    if sample_count < 8 or repeat_count < 1 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=complex), np.array([], dtype=float)
    x = np.asarray(x_matrix[:sample_count, :repeat_count], dtype=float).reshape(-1, order="F")
    y = np.asarray(y_matrix[:sample_count, :repeat_count], dtype=float).reshape(-1, order="F")
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return np.array([], dtype=float), np.array([], dtype=complex), np.array([], dtype=float)
    if not np.all(finite):
        x = x[finite]
        y = y[finite]
    if x.size < sample_count:
        return np.array([], dtype=float), np.array([], dtype=complex), np.array([], dtype=float)

    from scipy import signal as scipy_signal

    window = np.hanning(sample_count)
    freqs, g_xx = scipy_signal.welch(
        x,
        fs=float(sample_rate),
        window=window,
        nperseg=sample_count,
        noverlap=0,
        nfft=sample_count,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    _freqs, g_yx = scipy_signal.csd(
        x,
        y,
        fs=float(sample_rate),
        window=window,
        nperseg=sample_count,
        noverlap=0,
        nfft=sample_count,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    frf = np.divide(g_yx, g_xx, out=np.zeros_like(g_yx), where=np.abs(g_xx) > 0.0)
    coh_freqs, coherence = scipy_signal.coherence(
        x,
        y,
        fs=float(sample_rate),
        window=window,
        nperseg=sample_count,
        noverlap=0,
        nfft=sample_count,
        detrend=False,
    )
    if coherence.shape != freqs.shape or not np.allclose(coh_freqs, freqs, rtol=1e-8, atol=1e-12):
        coherence = np.interp(freqs, coh_freqs, coherence, left=np.nan, right=np.nan)
    valid = np.isfinite(freqs) & np.isfinite(frf) & np.isfinite(coherence)
    return freqs[valid], frf[valid], coherence[valid]


def _cangfu_loop_trace_matrix(item: object) -> np.ndarray:
    values = np.asarray(item, dtype=float)
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    if values.ndim != 2 or values.shape[0] < 1 or values.shape[1] < 1:
        return np.empty((0, 0), dtype=float)
    finite_columns = np.any(np.isfinite(values), axis=0)
    if not np.any(finite_columns):
        return np.empty((0, 0), dtype=float)
    return np.asarray(values[:, finite_columns], dtype=float)


def _cangfu_loop_transfer_label(info: dict[str, object], sidecar: dict[str, object]) -> str:
    loopinfo = str(info.get("loopinfo") or sidecar.get("title") or "").strip()
    axis = str(info.get("axis_str") or "").strip()
    loop = str(info.get("loop") or "").strip()
    if loopinfo and ":" in loopinfo:
        loop_part, axis_part = [part.strip() for part in loopinfo.split(":", 1)]
        if axis_part:
            axis = axis_part
        if loop_part:
            loop = loop_part
    parts = [part for part in (loop, axis) if part]
    prefix = " ".join(parts) if parts else "Cangfu"
    return f"{prefix} 传递函数"


def _parse_positive_float(value: object) -> float:
    parsed = _parse_float(str(_mat_scalar(value)))
    return float(parsed) if np.isfinite(parsed) and parsed > 0.0 else float("nan")


def _clean_cangfu_label(label: str) -> str:
    text = re.sub(r"\s+", "_", str(label or "").strip())
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", text)
    return text.strip("_") or "trace"


def _load_cangfu_index_file(path: Path, lines: list[str]) -> TraceAnalysisFile | None:
    referenced = _cangfu_referenced_data_files(path, lines)
    loaded: list[TraceAnalysisFile] = []
    for ref in referenced:
        try:
            item = _load_cangfu_trace_file(ref)
        except Exception:
            continue
        if item.channels:
            loaded.append(item)
    if not loaded:
        return None

    min_rows = min(file.table.row_count for file in loaded)
    if min_rows < 1:
        return None
    channels: dict[str, np.ndarray] = {}
    headers: list[str] = []
    for file_index, file in enumerate(loaded, start=1):
        stem = file.table.path.stem
        for name, values in file.channels.items():
            label = f"{stem}_{name}"
            headers.append(label)
            channels[label] = np.asarray(values[:min_rows], dtype=float)
    if not headers:
        return None
    data = np.column_stack([channels[name] for name in headers])
    sample_index = np.arange(1, min_rows + 1, dtype=float)
    sample_rate = loaded[0].sample_rate if loaded else 1.0
    if sample_rate <= 0.0:
        sample_rate = 1.0
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="cangfu_trace",
        metadata={
            "sample_index": sample_index,
            "cangfu_source": "index_file",
            "referenced_files": [str(file.table.path) for file in loaded],
        },
    )
    return TraceAnalysisFile(
        table=table,
        trace_kind="cangfu_trace",
        time_s=np.arange(min_rows, dtype=float) / sample_rate,
        channels=channels,
        sample_rate=sample_rate,
        channel_eu={header: 1.0 for header in headers},
    )


def _cangfu_referenced_data_files(path: Path, lines: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for line in lines:
        text = line.strip().rstrip(";")
        if not text:
            continue
        match = re.search(r"([./\\A-Za-z0-9_\-\u4e00-\u9fff]+\.dat)\s*;?\s*$", text)
        if not match:
            continue
        raw = match.group(1).replace("\\", "/")
        tail = Path(raw)
        if tail.is_absolute():
            candidate = tail
        else:
            parts = list(tail.parts)
            candidate = path.parent / tail
            if not candidate.exists() and "Ctrl_Error_Trace_Data" in parts:
                idx = parts.index("Ctrl_Error_Trace_Data")
                candidate = path.parent.joinpath(*parts[idx:])
            if not candidate.exists():
                candidate = path.parent / tail.name
        if candidate.exists() and candidate.is_file():
            candidates.append(candidate)
    return candidates


def _cangfu_channel_headers(header_lines: list[str], n_channels: int) -> list[str]:
    names: list[str] = []
    units: list[str] = []
    for line in header_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"\[[^\]]+\]", stripped):
            units.append(stripped)
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
            names.append(stripped)
    if len(names) >= n_channels:
        selected = names[-n_channels:]
    else:
        selected = [f"Ch {index + 1}" for index in range(n_channels)]
    headers: list[str] = []
    for index, name in enumerate(selected):
        unit = units[index] if index < len(units) else ""
        headers.append(f"{name}{unit}" if unit else name)
    return normalize_headers(headers, n_channels)


def _cangfu_sample_rate(header_lines: list[str], sample_index: np.ndarray) -> float:
    text = "\n".join(header_lines)
    for expr in (
        r"sample\s*rate\s*[:=]\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
        r"sample\s*frequency\s*[:=]\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
        r"period\s*\(?\s*ms\s*\)?\s*[:=]\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
    ):
        match = re.search(expr, text, re.I)
        if not match:
            continue
        value = _parse_float(match.group(1))
        if not np.isfinite(value) or value <= 0.0:
            continue
        if "period" in expr:
            return 1000.0 / float(value)
        return float(value)
    diffs = np.diff(np.asarray(sample_index, dtype=float).ravel())
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size and np.nanmedian(diffs) > 0.0:
        return 1.0 / float(np.nanmedian(diffs))
    return 1.0


def _detect_vibration_file_kind(lines: list[str], file_stem: str) -> str:
    signal_kind = _detect_ivsa_signal_kind(lines, file_stem)
    if signal_kind:
        return "ivsa_signal"
    has_update = False
    has_12_numeric = False
    for line in lines[:60]:
        if re.search(r"^\s*Update(?:\s+Rate)?\s*:", line, re.I):
            has_update = True
        if _fixed_numeric_row(line, 12) is not None:
            has_12_numeric = True
    if has_update and has_12_numeric:
        return "frequency"
    if re.search(r"^IVH[FS]A?_", file_stem, re.I):
        return "frequency"
    return "log"


def _load_vibration_frequency_file(path: Path, lines: list[str]) -> VibrationAnalysisFile:
    data_start = -1
    update = float("nan")
    samples = float("nan")
    average = float("nan")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not np.isfinite(update):
            update = _first_finite(update, _parse_header_scalar(line, "Update"), _parse_header_scalar(line, "Update Rate"))
        if not np.isfinite(samples):
            samples = _first_finite(samples, _parse_header_scalar(line, "Samples"))
        if not np.isfinite(average):
            average = _first_finite(average, _parse_header_scalar(line, "Average"))
        if _fixed_numeric_row(line, 12) is not None:
            data_start = index
            break
    if data_start < 0:
        raise ValueError("No 12-column numeric block found.")
    if not np.isfinite(update) or update <= 0.0:
        raise ValueError("Cannot find a valid Update value in file header.")

    rows = [row for line in lines[data_start:] if (row := _fixed_numeric_row(line, 12)) is not None]
    if not rows:
        raise ValueError("Frequency data block is empty.")
    data = np.asarray(rows, dtype=float)
    header_tokens: list[str] = []
    for line in reversed(lines[:data_start]):
        tokens = _split_fields(line)
        if len(tokens) >= 12 and sum(bool(re.search(r"[A-Za-z]", token)) for token in tokens[:12]) >= 6:
            header_tokens = tokens[:12]
            break
    if not header_tokens:
        header_tokens = _default_pair_headers(6)
    headers = normalize_headers(header_tokens, 12)
    fs = 5000.0 / float(update)
    frequency_pairs: list[CurvePair] = []
    phase_pairs: list[CurvePair] = []
    for pair_index in range(6):
        input_col = 2 * pair_index
        output_col = input_col + 1
        freq, frf = compute_matlab_tfestimate(data[:, input_col], data[:, output_col], fs)
        if freq.size == 0:
            continue
        label = _pair_label_from_headers(headers[input_col], headers[output_col], pair_index)
        mag_db = 20.0 * np.log10(np.maximum(np.abs(frf), 1e-300))
        phase_deg = np.angle(frf, deg=True)
        frequency_pairs.append(CurvePair(label, freq, mag_db, "频率 (Hz)", "幅值 (dB)"))
        phase_pairs.append(CurvePair(label, freq, phase_deg, "频率 (Hz)", "相位 (deg)"))
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="frequency",
        metadata={
            "update": float(update),
            "samples": float(samples) if np.isfinite(samples) else np.nan,
            "average": float(average) if np.isfinite(average) else np.nan,
            "fs_numerator": 5000.0,
            "sample_rate": fs,
            "phase_pairs": phase_pairs,
        },
    )
    return VibrationAnalysisFile(table=table, frequency_pairs=frequency_pairs, log_groups={}, log_group_labels={})


def _load_vibration_signal_file(path: Path, lines: list[str]) -> VibrationAnalysisFile:
    data_start = -1
    update = float("nan")
    samples = float("nan")
    n_cols = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if not np.isfinite(update):
            update = _first_finite(update, _parse_header_scalar(line, "Update"), _parse_header_scalar(line, "Update Rate"))
        if not np.isfinite(samples):
            samples = _first_finite(samples, _parse_header_scalar(line, "Samples"))
        row = _any_numeric_row(line)
        if row is not None:
            data_start = index
            n_cols = len(row)
            break
    if data_start < 0 or n_cols < 1:
        raise ValueError("No numeric signal block found.")
    rows = [row for line in lines[data_start:] if (row := _fixed_numeric_row(line, n_cols)) is not None]
    if not rows:
        raise ValueError("Signal data block is empty.")
    header_tokens: list[str] = []
    for line in reversed(lines[:data_start]):
        tokens = _split_fields(line)
        if len(tokens) >= n_cols and sum(bool(re.search(r"[A-Za-z]", token)) for token in tokens[:n_cols]) >= max(2, n_cols // 2):
            header_tokens = tokens[:n_cols]
            break
    headers = normalize_headers(header_tokens or [f"Col {index + 1}" for index in range(n_cols)], n_cols)
    data = np.asarray(rows, dtype=float)
    sample_index = np.arange(1, data.shape[0] + 1, dtype=float)
    pairs = [CurvePair(header, sample_index, np.asarray(data[:, index], dtype=float), "样本序号", header) for index, header in enumerate(headers)]
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="ivsa_signal",
        metadata={
            "update": float(update) if np.isfinite(update) else np.nan,
            "samples": float(samples) if np.isfinite(samples) else np.nan,
            "sample_index": sample_index,
        },
    )
    return VibrationAnalysisFile(table=table, frequency_pairs=pairs, log_groups={}, log_group_labels={})


def _try_load_vibration_wide_log(path: Path, lines: list[str]) -> VibrationAnalysisFile | None:
    nonempty = next((index for index, line in enumerate(lines) if line.strip()), None)
    if nonempty is None:
        return None
    separator = next((index for index in range(nonempty + 1, len(lines)) if re.fullmatch(r"[-=\s]{10,}", lines[index].strip())), None)
    matlab_style = _locate_matlab_style_wide_log(lines, nonempty, separator)
    if matlab_style is not None:
        headers, data_start, n_expected = matlab_style
        return _load_wide_log_block(path, lines, headers, data_start, n_expected)

    first = lines[nonempty]
    first_time, first_nums = _wide_log_row(first, None)
    if first_nums is not None:
        headers = [f"Col {index + 1}" for index in range(len(first_nums))]
        data_start = nonempty
    else:
        header_tokens = _split_fields(first)
        axis_count = _leading_log_axis_count(header_tokens)
        if axis_count > 0 and len(header_tokens) > axis_count:
            headers = header_tokens[axis_count:]
            data_start = nonempty + 1
        else:
            data_start = (separator + 1) if separator is not None else nonempty + 1
            first_data = next(((index, _wide_log_row(lines[index], None)) for index in range(data_start, len(lines)) if _wide_log_row(lines[index], None)[1] is not None), None)
            if first_data is None:
                return None
            data_start = first_data[0]
            n_numeric = len(first_data[1][1])
            headers = header_tokens[2 : n_numeric + 2] if len(header_tokens) >= n_numeric + 2 else [f"Col {index + 1}" for index in range(n_numeric)]
            if len(header_tokens) < n_numeric + 2 and separator is None:
                return None
    return _load_wide_log_block(path, lines, headers, data_start, len(headers))


def _load_wide_log_block(path: Path, lines: list[str], headers: list[str], data_start: int, n_expected: int) -> VibrationAnalysisFile | None:
    rows: list[list[float]] = []
    time_text: list[str] = []
    for line in lines[data_start:]:
        time_value, values = _wide_log_row(line, n_expected)
        if values is None:
            continue
        time_text.append(time_value)
        rows.append(values)
    if not rows:
        return None
    data = np.asarray(rows, dtype=float)
    headers = normalize_headers(headers, data.shape[1])
    table = NumericTableFile(
        path=path,
        name=path.name,
        headers=headers,
        data=data,
        kind="log",
        metadata={
            "raw_time_text": time_text,
            "sample_index": np.arange(1, data.shape[0] + 1, dtype=float),
        },
    )
    groups = build_log_groups(headers)
    return VibrationAnalysisFile(
        table=table,
        frequency_pairs=[],
        log_groups=groups,
        log_group_labels=build_log_group_labels(headers, groups),
    )


def _locate_matlab_style_wide_log(lines: list[str], header_index: int, separator: int | None) -> tuple[list[str], int, int] | None:
    header_time, header_values = _wide_log_row(lines[header_index], None)
    if header_values is not None and _has_leading_time_tokens(lines[header_index], len(header_values)):
        headers = [f"Col {index + 1}" for index in range(len(header_values))]
        return headers, header_index, len(header_values)

    data_start = (separator + 1) if separator is not None else header_index + 1
    first_data: tuple[int, list[float]] | None = None
    for index in range(data_start, len(lines)):
        _time_text, values = _wide_log_row(lines[index], None)
        if values is not None and _has_leading_time_tokens(lines[index], len(values)):
            first_data = (index, values)
            break
    if first_data is None:
        return None
    n_numeric = len(first_data[1])
    header_tokens = _split_wide_log_fields(lines[header_index])
    headers = _derive_wide_log_headers(header_tokens, n_numeric)
    return headers, first_data[0], n_numeric


def _has_leading_time_tokens(line: str, n_numeric: int) -> bool:
    tokens = _split_wide_log_fields(line)
    return len(tokens) >= n_numeric + 2 and all(np.isfinite(_parse_float(token)) for token in tokens[-n_numeric:])


def _derive_wide_log_headers(header_tokens: list[str], n_numeric: int) -> list[str]:
    if len(header_tokens) < n_numeric + 2:
        return [f"Col {index + 1}" for index in range(n_numeric)]
    raw = header_tokens[2:]
    if len(raw) == n_numeric:
        return raw

    starters = {
        "VEL",
        "ACC",
        "PROX",
        "PS",
        "VALUE",
        "VALVE",
        "MT",
        "INP",
        "OUT",
        "TEMP",
        "VS",
        "VFS",
        "TS",
        "WS",
        "RS",
        "AC",
        "RESERVED",
    }
    groups: list[list[str]] = []
    for offset, token in enumerate(raw):
        clean = _canonical_log_header_name(token)
        remaining_tokens = len(raw) - offset
        remaining_groups = n_numeric - len(groups)
        should_start = bool(groups) and _is_log_channel_start(clean, starters) and remaining_tokens >= remaining_groups
        if should_start:
            groups.append([token])
        elif groups:
            groups[-1].append(token)
        else:
            groups.append([token])
        if len(groups) == n_numeric:
            break

    headers = ["_".join(part.strip() for part in group if str(part).strip()) for group in groups]
    if len(headers) < n_numeric:
        headers.extend(f"Col {index + 1}" for index in range(len(headers), n_numeric))
    return headers[:n_numeric]


def _is_log_channel_start(clean_header: str, starters: set[str]) -> bool:
    first_part = clean_header.split("_", 1)[0]
    return first_part in starters or any(first_part.startswith(starter) for starter in starters)


def _leading_log_axis_count(headers: list[str]) -> int:
    if not headers:
        return 0
    first = str(headers[0] or "").strip()
    second = str(headers[1] or "").strip() if len(headers) > 1 else ""
    if re.fullmatch(r"date", first, re.I) and _is_axis_header(second):
        return 2
    if _is_axis_header(first):
        return 1
    return 0


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_header_scalar(line: str, key: str) -> float:
    expr = rf"^\s*{re.escape(key)}\s*:?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    match = re.search(expr, str(line), re.I)
    return _parse_float(match.group(1)) if match else float("nan")


def _split_semicolon_line(line: str) -> list[str]:
    return [part.strip() for part in str(line).split(";")]


def _default_ide_eu(header: str) -> float:
    text = re.sub(r"\([^\)]*\)", "", str(header))
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", "", text).upper()
    return 3.75 if text.endswith("PROX") else 1.0


def _hac_elapsed_seconds(time_text: list[str], period_ms: float, n_rows: int) -> np.ndarray:
    parsed = [_parse_hac_datetime(value) for value in time_text]
    if parsed and all(value is not None for value in parsed):
        first = parsed[0]
        if first is not None:
            return np.asarray([(value - first).total_seconds() for value in parsed if value is not None], dtype=float)
    return np.arange(n_rows, dtype=float) * float(period_ms) / 1000.0


def _parse_hac_datetime(value: str) -> datetime | None:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_hac_groups(headers: Iterable[str]) -> dict[str, list[int]]:
    names = list(headers)
    groups: dict[str, list[int]] = {}
    for group_name, expr in (
        ("位移", r"^位移"),
        ("速度", r"^速度"),
        ("前馈", r"^前馈"),
        ("温度", r"^温度"),
        ("WS", r"^WS"),
        ("Motor", r"^Motor"),
        ("P", r"^P\d+"),
    ):
        indices = [index for index, header in enumerate(names) if re.search(expr, header, re.I)]
        if indices:
            groups[group_name] = indices
    groups["All Channels"] = list(range(len(names)))
    return groups


def _detect_ivsa_signal_kind(lines: list[str], file_stem: str) -> str:
    name_lower = file_stem.lower()
    for line in lines[:24]:
        text = line.strip()
        if not text:
            continue
        lower = text.lower()
        if "sine test" in lower:
            return "sine"
        if "feedforward signal test" in lower:
            return "sff"
        tokens = _split_fields(text)
        token_lower = [token.lower() for token in tokens]
        if len(tokens) >= 6 and any("_prox" in token for token in token_lower) and any("_geo" in token for token in token_lower):
            return "sine"
        if len(tokens) >= 4 and any(token.upper() == "X_ACC[UM/S^2]" for token in tokens):
            return "sff"
    if "_sine_" in name_lower:
        return "sine"
    if "_sff_" in name_lower:
        return "sff"
    return ""


def _fixed_numeric_row(line: str, n_cols: int) -> list[float] | None:
    tokens = _split_fields(line)
    if len(tokens) != n_cols:
        return None
    values = [_parse_float(token) for token in tokens]
    if all(np.isfinite(values)):
        return values
    return None


def _any_numeric_row(line: str) -> list[float] | None:
    tokens = _split_fields(line)
    if not tokens:
        return None
    values = [_parse_float(token) for token in tokens]
    if all(np.isfinite(values)):
        return values
    return None


def _first_finite(*values: float) -> float:
    for value in values:
        if np.isfinite(value):
            return float(value)
    return float("nan")


def _default_pair_headers(n_pairs: int) -> list[str]:
    headers: list[str] = []
    for index in range(n_pairs):
        headers.extend([f"Pair{index + 1}_in", f"Pair{index + 1}_out"])
    return headers


def _wide_log_row(line: str, n_numeric: int | None) -> tuple[str, list[float] | None]:
    tokens = _split_wide_log_fields(line)
    if not tokens:
        return "", None
    numeric_tokens = tokens if n_numeric is None else tokens[-n_numeric:]
    if n_numeric is None:
        values = [_parse_float(token) for token in numeric_tokens]
        values = [value for value in values if np.isfinite(value)]
        if not values:
            return "", None
        if len(values) == len(tokens):
            return "", values
        n_numeric = len(values)
        numeric_tokens = tokens[-n_numeric:]
    values = [_parse_float(token) for token in numeric_tokens]
    if len(values) != n_numeric or not all(np.isfinite(values)):
        return "", None
    time_text = " ".join(tokens[: max(0, len(tokens) - n_numeric)])
    return time_text, values


def _split_wide_log_fields(line: str) -> list[str]:
    text = str(line)
    tokens = _split_fields(line)
    if "\t" not in text:
        return tokens
    whitespace_tokens = re.findall(r"\S+", str(line).strip())
    if len(whitespace_tokens) > len(tokens):
        return whitespace_tokens
    return tokens


def _first_data_or_header_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        tokens = _split_fields(line)
        if not tokens:
            continue
        if _tokens_are_numeric(tokens):
            return index
        following = lines[index + 1 : index + 8]
        if any(_tokens_are_numeric(_split_fields(candidate)) for candidate in following):
            return index
    return None


def _split_fields(line: str) -> list[str]:
    text = str(line).strip()
    if not text:
        return []
    if "\t" in text:
        return [part.strip() for part in text.split("\t")]
    if ";" in text:
        return _csv_split(text, delimiter=";")
    if "," in text:
        return _csv_split(text, delimiter=",")
    return [part.strip() for part in text.split()]


def _csv_split(text: str, *, delimiter: str) -> list[str]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        return [part.strip() for part in next(reader)]
    except StopIteration:
        return []


def _tokens_are_numeric(tokens: list[str]) -> bool:
    if not tokens:
        return False
    values = [_parse_float(token) for token in tokens]
    return any(np.isfinite(values))


def _parse_float(token: str) -> float:
    text = str(token).strip().strip('"')
    if not text:
        return np.nan
    if text.lower() in {"nan", "na", "n/a", "inf", "+inf", "-inf"}:
        try:
            return float(text.replace("+", ""))
        except ValueError:
            return np.nan
    text = text.replace(",", "") if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", text) else text
    try:
        return float(text)
    except ValueError:
        return np.nan


def _detect_vibration_kind(table: NumericTableFile, pairs: list[CurvePair]) -> str:
    upper = " ".join(table.headers).upper()
    if pairs and any(key in upper for key in ("STIFF", "COH", "PSD", "FRF", "VNA")):
        return "frequency"
    if pairs and table.row_count > 2:
        first = pairs[0].x
        finite = first[np.isfinite(first)]
        if finite.size > 2 and np.nanmedian(np.diff(finite[: min(20, finite.size)])) > 0:
            return "frequency"
    return "log"


def _time_axis_from_table(table: NumericTableFile) -> np.ndarray:
    if table.column_count == 0:
        return np.array([], dtype=float)
    time_index = next(
        (index for index, header in enumerate(table.headers) if _TIME_HEADER_RE.search(header.strip())),
        None,
    )
    if time_index is not None:
        axis = np.asarray(table.data[:, time_index], dtype=float)
        if np.isfinite(axis).any():
            return axis
    first = np.asarray(table.data[:, 0], dtype=float)
    if np.isfinite(first).any() and _looks_like_time_axis(first):
        return first
    return np.arange(table.row_count, dtype=float)


def _infer_sample_rate(time_s: np.ndarray) -> float:
    t = np.asarray(time_s, dtype=float).ravel()
    if t.size < 2:
        return 1.0
    diffs = np.diff(t)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return 1.0
    return float(1.0 / np.median(diffs))


def _is_axis_header(header: str) -> bool:
    text = str(header or "").strip()
    return bool(_TIME_HEADER_RE.search(text) or _SAMPLE_HEADER_RE.search(text))


def _looks_like_time_axis(values: np.ndarray) -> bool:
    arr = np.asarray(values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size < 3:
        return False
    diffs = np.diff(arr[: min(arr.size, 128)])
    diffs = diffs[np.isfinite(diffs)]
    return bool(diffs.size and np.nanmedian(diffs) > 0.0 and np.nanmax(np.abs(diffs)) < max(1.0, arr[-1] - arr[0] + 1.0))


def _pair_label_from_headers(left: str, right: str, index: int) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if left_text and right_text:
        common = _common_prefix(left_text, right_text).rstrip("_-/ ")
        if common:
            return common
    return left_text or right_text or f"Pair {index + 1}"


def _common_prefix(left: str, right: str) -> str:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return left[:count]


def _group_prefix(header: str) -> str:
    text = str(header or "").strip().upper()
    text = re.sub(r"\(.*$", "", text)
    text = re.sub(r"\s+", "", text)
    if not text:
        return ""
    parts = re.split(r"[_-]", text)
    if len(parts) >= 2:
        prefix = f"{parts[0]}_{parts[1]}"
    else:
        match = re.search(r"^([A-Z]+)\d+", text)
        if match:
            prefix = match.group(1)
        else:
            match = re.search(r"^([A-Z]+)", text)
            prefix = match.group(1) if match else ""
    if len(prefix) < 2 or prefix == "AUTO":
        return ""
    return prefix
