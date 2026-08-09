"""Raw-data file I/O — save/load measurement traces as CSV.

Format used by ``OpenRaw`` / ``SaveRaw`` / ``AddRaw`` and the Open/Save
Setting group:

::

    # python_sidmat raw trace data
    sig0_name, Xtrans
    sig1_name, Ytrans
    sample_rate, 1000
    undersample, 1
    avg_num, 3
    sample_num, 300
    time, ch0, ch1
    0.0, 0.001, -0.002
    0.001, ...

``time`` (seconds) is derived from the effective sample rate
(``sample_rate / undersample``) on export and ignored on import (only ``ch0`` /
``ch1`` are read back).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from os import PathLike

import numpy as np

from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.measurement.trace import TraceParameters

__all__ = [
    "RawFile",
    "export_raw",
    "import_raw",
    "export_trace_config",
    "import_trace_config",
]

_META_KEYS = ("sig0_name", "sig1_name", "sample_rate",
              "undersample", "avg_num", "sample_num")


@dataclass
class RawFile:
    """One saved measurement: metadata + two channels."""

    sig0_name: str = ""
    sig1_name: str = ""
    sample_rate: int = 0
    undersample: int = 1
    avg_num: int = 1
    sample_num: int = 0
    ch0: list[float] = field(default_factory=list)
    ch1: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return max(len(self.ch0), len(self.ch1))

    def to_raw(self) -> MeasurementRawData:
        return MeasurementRawData(
            sig_name=[self.sig0_name, self.sig1_name],
            data=[np.asarray(self.ch0, dtype=float), np.asarray(self.ch1, dtype=float)],
            sample_rate=self.sample_rate,
            undersample=self.undersample,
            avg_num=self.avg_num,
            sample_num=self.sample_num,
        )


def export_raw(data: MeasurementRawData, path: str | PathLike[str]) -> None:
    """Write a MeasurementRawData to CSV."""
    ch0 = data.channel(0)
    ch1 = data.channel(1) if data.channel_count > 1 else []
    total = max(len(ch0), len(ch1))
    fs = data.effective_sample_rate or 1.0
    dt = 1.0 / fs
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["# python_sidmat raw trace data"])
        w.writerow(["sig0_name", data.sig_name[0] if data.sig_name else ""])
        w.writerow(["sig1_name", data.sig_name[1] if len(data.sig_name) > 1 else ""])
        w.writerow(["sample_rate", data.sample_rate])
        w.writerow(["undersample", data.undersample])
        w.writerow(["avg_num", data.avg_num])
        w.writerow(["sample_num", data.sample_num])
        w.writerow(["time", "ch0", "ch1"])
        for i in range(total):
            t = i * dt
            c1 = ch0[i] if i < len(ch0) else 0.0
            c2 = ch1[i] if i < len(ch1) else 0.0
            # 17 significant digits round-trip normal IEEE-754 doubles while
            # keeping the CSV human-readable.
            w.writerow([f"{t:.17g}", f"{c1:.17g}", f"{c2:.17g}"])


def import_raw(path: str | PathLike[str]) -> RawFile:
    """Read a measurement CSV back into a RawFile."""
    rf = RawFile()
    headerless = False
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        rows = csv.reader(f)
        data_rows = False
        for row in rows:
            if not row:
                continue
            first = row[0].strip()
            if first.startswith("#"):
                continue
            if first.lower() == "time":
                data_rows = True
                continue
            if not data_rows:
                key = first.lower()
                if key in _META_KEYS and len(row) > 1:
                    value = row[1].strip()
                    if key == "sig0_name":
                        rf.sig0_name = value
                    elif key == "sig1_name":
                        rf.sig1_name = value
                    elif key == "sample_rate":
                        rf.sample_rate = max(0, _to_int(value))
                    elif key == "undersample":
                        rf.undersample = max(1, _to_int(value))
                    elif key == "avg_num":
                        rf.avg_num = max(1, _to_int(value))
                    elif key == "sample_num":
                        rf.sample_num = _to_int(value)
                    continue
                # No header found — treat this and the rest as data.
                data_rows = True
                headerless = True
            # data row: time, ch0, ch1 (time ignored on import); headerless
            # files carry plain ch0, ch1.
            try:
                if headerless:
                    rf.ch0.append(float(row[0]))
                    rf.ch1.append(float(row[1]))
                else:
                    rf.ch0.append(float(row[1]))
                    rf.ch1.append(float(row[2]))
            except (ValueError, IndexError):
                continue
    # ``sample_num`` is the total number of stored samples in the Python API.
    # Prefer the actual rows when a stale header claims a different length;
    # otherwise downstream analysis would use inconsistent metadata.
    actual_samples = max(len(rf.ch0), len(rf.ch1))
    if rf.sample_num != actual_samples:
        rf.sample_num = actual_samples
    return rf


def _to_int(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def export_trace_config(trace: TraceParameters, path: str | PathLike[str]) -> None:
    """Write trace settings (DSTIV fields) to a key=value file."""
    trace.validate()
    with open(path, "w", encoding="utf-8") as f:
        f.write("# python_sidmat trace configuration\n")
        f.write(f"trace_ch0 = {trace.trace_ch0.type} {trace.trace_ch0.main_index} {trace.trace_ch0.sub_index}\n")
        f.write(f"trace_ch1 = {trace.trace_ch1.type} {trace.trace_ch1.main_index} {trace.trace_ch1.sub_index}\n")
        f.write(f"no_samples = {trace.no_samples}\n")
        f.write(f"undersamples = {trace.undersamples}\n")
        f.write(f"average_number = {trace.average_number}\n")
        f.write(f"trace_filter_flag = {trace.trace_filter_flag}\n")
        f.write(f"fast_data_loading = {int(trace.is_fast_data_loading)}\n")


def import_trace_config(trace: TraceParameters, path: str | PathLike[str]) -> None:
    """Load trace settings atomically into an existing TraceParameters.

    Parsing and validation happen on a copy first.  A malformed file therefore
    cannot leave the live UI model half-updated.
    """
    from python_sidmat.backend.iosignal import IOType

    candidate = replace(trace)
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            key, value = (p.strip() for p in line.split("=", 1))
            try:
                if key == "trace_ch0":
                    parts = value.split()
                    if len(parts) != 3:
                        raise ValueError("trace_ch0 requires exactly three integers")
                    candidate.trace_ch0 = IOType(*(int(part, 0) for part in parts))
                elif key == "trace_ch1":
                    parts = value.split()
                    if len(parts) != 3:
                        raise ValueError("trace_ch1 requires exactly three integers")
                    candidate.trace_ch1 = IOType(*(int(part, 0) for part in parts))
                elif key == "no_samples":
                    candidate.no_samples = int(value, 0)
                elif key == "undersamples":
                    candidate.undersamples = int(value, 0)
                elif key == "average_number":
                    candidate.average_number = int(value, 0)
                elif key == "trace_filter_flag":
                    candidate.trace_filter_flag = int(value, 0)
                elif key == "fast_data_loading":
                    candidate.set_fast_data_loading(
                        value.lower() in ("1", "true", "on", "yes")
                    )
            except ValueError as exc:
                raise ValueError(f"invalid trace config line {line_number}: {exc}") from exc
    candidate.validate()
    trace.trace_ch0 = candidate.trace_ch0
    trace.trace_ch1 = candidate.trace_ch1
    trace.no_samples = candidate.no_samples
    trace.undersamples = candidate.undersamples
    trace.average_number = candidate.average_number
    trace.trace_filter_flag = candidate.trace_filter_flag
    trace.set_fast_data_loading(candidate.is_fast_data_loading)
