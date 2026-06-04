from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import csv
import io
import re
from typing import Iterable

import numpy as np


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


@dataclass(slots=True)
class TraceAnalysisFile:
    table: NumericTableFile
    trace_kind: str
    time_s: np.ndarray
    channels: dict[str, np.ndarray]
    sample_rate: float


_TIME_HEADER_RE = re.compile(r"^(time|t|elapsed|seconds?|sec|timestamp|sample\s*time)", re.I)
_SAMPLE_HEADER_RE = re.compile(r"^(sample|index|idx|n)$", re.I)


def load_vibration_analysis_file(path: str | Path) -> VibrationAnalysisFile:
    table = load_numeric_table(path)
    pairs = curve_pairs_from_table(table)
    kind = _detect_vibration_kind(table, pairs)
    table.kind = kind
    groups = build_log_groups(table.headers)
    return VibrationAnalysisFile(table=table, frequency_pairs=pairs, log_groups=groups)


def load_trace_analysis_file(path: str | Path) -> TraceAnalysisFile:
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
    groups: dict[str, list[int]] = {}
    for index, header in enumerate(headers):
        if _is_axis_header(header):
            continue
        prefix = _group_prefix(header)
        groups.setdefault(prefix, []).append(index)
    return groups


def detect_trace_file_type(table: NumericTableFile) -> str:
    suffix = table.path.suffix.lower()
    lower_headers = " ".join(table.headers).lower()
    if suffix == ".csv" or "hac" in lower_headers or "period" in lower_headers:
        return "hac_trace"
    return "ide_trace"


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


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


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
    text = str(header or "").strip()
    for separator in ("_", "-", ".", " "):
        if separator in text:
            prefix = text.split(separator, 1)[0].strip()
            if prefix:
                return prefix
    return "Channels"
