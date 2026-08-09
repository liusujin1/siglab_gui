"""Crash-tolerant streaming files and legacy LoggingTool record import."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from python_samba.logging_tools.models import FileLoggingConfig, LoggingRecord


SCHEMA = "python-samba-monitor-log/v1"


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Path)):
        return str(value)
    return repr(value)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class DelimitedStreamWriter:
    """Append monitor rows immediately and maintain a small status sidecar."""

    def __init__(self, config: FileLoggingConfig) -> None:
        config.validate()
        self.config = config
        self.path = Path(config.path).expanduser().resolve()
        self.meta_path = self.path.with_suffix(self.path.suffix + ".meta.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._stream, delimiter=config.delimiter)
        names = list(config.signal_names[: config.signal_count])
        names.extend(
            f"Monitor {index + 1}" for index in range(len(names), config.signal_count)
        )
        self.signal_names = names
        self._writer.writerow(["timestamp_utc", "elapsed_s", *names])
        self._stream.flush()
        self.samples = 0
        self.started_utc = datetime.now(timezone.utc).isoformat()
        self._metadata: dict[str, Any] = {
            "schema": SCHEMA,
            "state": "waiting" if config.start_after_s else "running",
            "started_utc": self.started_utc,
            "signal_count": config.signal_count,
            "signal_names": names,
            "interval_ms": config.interval_ms,
            "start_after_s": config.start_after_s,
            "duration_s": config.duration_s,
            "delimiter": config.delimiter,
            "data_file": self.path.name,
            "samples": 0,
        }
        _write_json_atomic(self.meta_path, self._metadata)

    def set_running(self) -> None:
        self._metadata["state"] = "running"
        self._metadata["acquisition_started_utc"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(self.meta_path, self._metadata)

    def append(self, timestamp_utc: datetime, elapsed_s: float, values: Iterable[float]) -> None:
        row = [timestamp_utc.astimezone(timezone.utc).isoformat(), f"{elapsed_s:.9f}"]
        row.extend(format(float(value), ".12g") for value in values)
        self._writer.writerow(row)
        # Flush every row so a process or controller failure loses at most the
        # write currently in progress, rather than an hour-long in-memory log.
        self._stream.flush()
        self.samples += 1
        if self.samples == 1 or self.samples % 20 == 0:
            self._metadata["samples"] = self.samples
            _write_json_atomic(self.meta_path, self._metadata)

    def finish(self, state: str, *, message: str = "", stats: dict[str, Any] | None = None) -> None:
        if self._stream.closed:
            return
        self._stream.flush()
        self._stream.close()
        self._metadata.update(
            {
                "state": state,
                "samples": self.samples,
                "finished_utc": datetime.now(timezone.utc).isoformat(),
                "message": message,
            }
        )
        if stats:
            self._metadata.update(stats)
        _write_json_atomic(self.meta_path, self._metadata)


def save_trace_record(
    path: str | Path,
    rows: Iterable[Iterable[float]],
    signal_names: Iterable[str],
    *,
    sample_interval_s: float,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a controller-internal trace as an analysis-friendly CSV file."""

    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    names = list(signal_names)
    with output.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["elapsed_s", *names])
        for index, values in enumerate(rows):
            writer.writerow(
                [f"{index * sample_interval_s:.9f}", *[format(float(v), ".12g") for v in values]]
            )
    sidecar = output.with_suffix(output.suffix + ".meta.json")
    payload = {
        "schema": "python-samba-internal-trace/v1",
        "state": "complete",
        "data_file": output.name,
        "signal_names": names,
        "sample_interval_s": sample_interval_s,
        "samples": index + 1 if "index" in locals() else 0,
    }
    if metadata:
        payload.update(metadata)
    _write_json_atomic(sidecar, payload)
    return output


def _unwrap(value: Any, default: Any = None) -> Any:
    if isinstance(value, dict):
        for key in ("Value", "value"):
            if key in value:
                return value[key]
    return default if value is None else value


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Newtonsoft sometimes emits /Date(1483228800000+0000)/.
        match = re.search(r"/Date\((-?\d+)", text)
        if match:
            return datetime.fromtimestamp(int(match.group(1)) / 1000.0, timezone.utc)
    return None


def _legacy_json_record(path: Path, payload: dict[str, Any]) -> LoggingRecord:
    names = [str(item) for item in payload.get("SigName", payload.get("sigName", []))]
    data = payload.get("Data", payload.get("data", [])) or []
    if "Param" in payload or (data and isinstance(data[0], list)):
        param = payload.get("Param", {}) or {}
        under = float(_unwrap(param.get("UnderSample"), 1) or 1)
        frequency = float(_unwrap(param.get("SampleFrequency"), 1) or 1)
        interval = under / frequency if frequency else 1.0
        rows = [[index * interval, *[float(v) for v in values]] for index, values in enumerate(data)]
        return LoggingRecord(
            ["elapsed_s", *names], rows, str(path), {"legacy": "InternalLoggingRecord", "Param": param}
        )

    parsed: list[tuple[datetime | None, list[float]]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        timestamp = _parse_datetime(item.get("time", item.get("Time")))
        values = item.get("SigVal", item.get("sigVal", [])) or []
        parsed.append((timestamp, [float(value) for value in values]))
    first = next((stamp for stamp, _ in parsed if stamp is not None), None)
    rows: list[list[Any]] = []
    for index, (stamp, values) in enumerate(parsed):
        elapsed = (stamp - first).total_seconds() if stamp is not None and first is not None else float(index)
        rows.append([stamp.isoformat() if stamp else "", elapsed, *values])
    return LoggingRecord(
        ["timestamp_utc", "elapsed_s", *names],
        rows,
        str(path),
        {"legacy": "FileLoggingRecord"},
    )


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _xml_values(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    leaves = [child.text or "" for child in element.iter() if len(child) == 0]
    return [value.strip() for value in leaves if value.strip()]


def _legacy_xml_record(path: Path) -> LoggingRecord:
    root = ET.parse(path).getroot()
    lookup: dict[str, list[ET.Element]] = {}
    for element in root.iter():
        lookup.setdefault(_tag(element), []).append(element)
    names = _xml_values((lookup.get("SigName") or [None])[0])
    internal = _tag(root).lower().startswith("internal") or "Param" in lookup
    if internal:
        data_root = (lookup.get("Data") or [None])[0]
        data_rows: list[list[float]] = []
        if data_root is not None:
            for child in data_root:
                values = _xml_values(child)
                if values:
                    data_rows.append([float(value) for value in values])
        def param_value(name: str, default: float) -> float:
            elements = lookup.get(name, [])
            values = _xml_values(elements[0]) if elements else []
            return float(values[0]) if values else default
        frequency = param_value("SampleFrequency", 1.0)
        under = param_value("UnderSample", 1.0)
        interval = under / frequency if frequency else 1.0
        return LoggingRecord(
            ["elapsed_s", *names],
            [[index * interval, *row] for index, row in enumerate(data_rows)],
            str(path),
            {"legacy": "InternalLoggingRecord XML"},
        )

    rows: list[list[Any]] = []
    records = lookup.get("RecordData", [])
    first: datetime | None = None
    for index, record in enumerate(records):
        children = {_tag(child): child for child in record}
        stamps = _xml_values(children.get("time")) or _xml_values(children.get("Time"))
        stamp = _parse_datetime(stamps[0]) if stamps else None
        if first is None and stamp is not None:
            first = stamp
        values = [float(value) for value in _xml_values(children.get("SigVal"))]
        elapsed = (stamp - first).total_seconds() if stamp and first else float(index)
        rows.append([stamp.isoformat() if stamp else "", elapsed, *values])
    return LoggingRecord(
        ["timestamp_utc", "elapsed_s", *names], rows, str(path), {"legacy": "FileLoggingRecord XML"}
    )


def _detect_delimiter(header: str) -> str:
    candidates = ["\t", ";", ","]
    counts = [(header.count(delimiter), delimiter) for delimiter in candidates]
    count, delimiter = max(counts)
    return delimiter if count else " "


def _coerce_cell(value: str) -> Any:
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        return value


def _delimited_record(path: Path) -> LoggingRecord:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        return LoggingRecord([], [], str(path))
    delimiter = _detect_delimiter(lines[0])
    if delimiter == " ":
        parsed = [line.split() for line in lines if line.strip()]
    else:
        parsed = list(csv.reader(lines, delimiter=delimiter))
    if not parsed:
        return LoggingRecord([], [], str(path))
    headers = [str(value).strip() for value in parsed[0]]
    rows = [[_coerce_cell(value) for value in row] for row in parsed[1:] if row]
    metadata: dict[str, Any] = {"delimiter": delimiter}
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    if sidecar.exists():
        try:
            metadata.update(json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return LoggingRecord(headers, rows, str(path), metadata)


def _legacy_text_record(path: Path, *, internal: bool) -> LoggingRecord:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if internal:
        if len(lines) < 5:
            raise ValueError("incomplete legacy internal logging text file")
        values: dict[str, float] = {}
        for line in lines[:4]:
            key, _, raw = line.partition(":")
            try:
                values[key.strip().lower()] = float(raw.strip())
            except ValueError:
                pass
        names = [name.strip() for name in lines[4].split(";") if name.strip()]
        frequency = values.get("samplefrequency", 1.0)
        under_sample = values.get("undersamplenum", 1.0)
        interval = under_sample / frequency if frequency else 1.0
        data = [[float(value) for value in line.split()] for line in lines[5:]]
        return LoggingRecord(
            ["elapsed_s", *names],
            [[index * interval, *row] for index, row in enumerate(data)],
            str(path),
            {"legacy": "InternalLoggingRecord text"},
        )
    if len(lines) < 2:
        raise ValueError("incomplete legacy file logging text file")
    names = [name.strip() for name in lines[1].split(";") if name.strip()]
    rows = [[float(value) for value in line.split()] for line in lines[2:]]
    return LoggingRecord(
        ["elapsed_s", *names], rows, str(path), {"legacy": "FileLoggingRecord text"}
    )


def load_logging_record(path: str | Path) -> LoggingRecord:
    """Load CSV/TSV records and legacy *.LoggRec* / *.ILogRec* files."""

    source = Path(path).expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix in {".loggrecjson", ".iloggrecjson", ".ilogrecjson", ".json"}:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("logging JSON root must be an object")
        return _legacy_json_record(source, payload)
    if suffix in {".loggrecxml", ".iloggrecxml", ".ilogrecxml", ".xml"}:
        return _legacy_xml_record(source)
    if suffix == ".loggrectxt":
        return _legacy_text_record(source, internal=False)
    if suffix in {".iloggrectxt", ".ilogrectxt"}:
        return _legacy_text_record(source, internal=True)
    return _delimited_record(source)
