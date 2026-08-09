"""SiDiMaT raw-data .sidimat19x files — MATLAB .mat v5, matching the format
written by ``SAMBA19xLib.Plot2MatFile``.

Top-level variables:
  MeasurementType  char  "SiDiMat19x"
  Version          double 2.0
  RawDat1..RawDatN struct  one entry per cached measurement

Each RawDat struct:
  Version       double 2.0
  SampleRate    int32
  UnderSample   int32
  SampleNumber  int32
  AverageNumber int32
  SignalName    struct  Sig0 / Sig1 / ... (char)
  DataSet       double M×N matrix (M signals, N samples)
"""

from __future__ import annotations

import numpy as np

from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.measurement.datafile import RawFile

__all__ = ["MEASUREMENT_TYPE", "save_sidimat_raw", "load_sidimat_raw"]

MEASUREMENT_TYPE = "SiDiMat19x"
_VERSION = 2.0


def save_sidimat_raw(
    entries: list[RawFile] | RawFile | MeasurementRawData | list[MeasurementRawData],
    path: str,
) -> None:
    """Write one or more measurements to a .sidimat19x file (original format)."""
    raws = _as_raw_files(entries)
    if not raws:
        raise ValueError("no measurements to save")
    try:
        from scipy.io import savemat
    except ImportError as exc:
        raise RuntimeError(
            "MAT-file support requires SciPy; install with 'pip install scipy'"
        ) from exc
    payload: dict[str, object] = {
        "MeasurementType": MEASUREMENT_TYPE,
        "Version": np.array([_VERSION]),
    }
    for i, rf in enumerate(raws, start=1):
        payload[f"RawDat{i}"] = _raw_dat_struct(rf)
    savemat(path, payload, do_compression=False, format="5")


def load_sidimat_raw(path: str) -> list[RawFile]:
    """Read a .sidimat19x file back into a list of RawFile."""
    try:
        from scipy.io import loadmat
    except ImportError as exc:
        raise RuntimeError(
            "MAT-file support requires SciPy; install with 'pip install scipy'"
        ) from exc
    data = loadmat(path, mat_dtype=True)  # struct_as_record=True (default)
    measurement_type = data.get("MeasurementType")
    if measurement_type is not None:
        t = _to_str(measurement_type)
        if t and t != MEASUREMENT_TYPE:
            raise ValueError(f"unsupported measurement type in mat file: {t}")
    version = data.get("Version")
    if version is not None and np.asarray(version).size:
        value = float(np.asarray(version).flat[0])
        if value < 1.0 or value > _VERSION:
            raise ValueError("unsupported .sidimat19x version")
    raw_list: list[RawFile] = []
    i = 1
    while f"RawDat{i}" in data:
        raw_list.append(_raw_dat_to_rawfile(data[f"RawDat{i}"]))
        i += 1
    return raw_list


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _as_raw_files(entries) -> list[RawFile]:
    if isinstance(entries, (list, tuple)):
        out: list[RawFile] = []
        for e in entries:
            out.extend(_as_raw_files(e))
        return out
    if isinstance(entries, RawFile):
        return [entries]
    if isinstance(entries, MeasurementRawData):
        rf = RawFile(
            sig0_name=entries.sig_name[0] if entries.sig_name else "",
            sig1_name=entries.sig_name[1] if len(entries.sig_name) > 1 else "",
            sample_rate=entries.sample_rate,
            undersample=entries.undersample,
            avg_num=entries.avg_num,
            sample_num=entries.sample_num,
            ch0=list(entries.channel(0)) if len(entries.data) > 0 else [],
            ch1=list(entries.channel(1)) if len(entries.data) > 1 else [],
        )
        return [rf]
    raise TypeError(f"cannot save {type(entries).__name__}")


def _raw_dat_struct(rf: RawFile) -> dict:
    n = max(len(rf.ch0), len(rf.ch1))
    avg_num = max(1, int(rf.avg_num))
    if n and n % avg_num:
        raise ValueError(
            f"data length {n} is not divisible by AverageNumber {avg_num}"
        )
    # The legacy MATLAB field SampleNumber means samples per average, while
    # RawFile.sample_num is the total number held by this Python API.
    sample_num = n // avg_num if n else 0
    dataset = np.zeros((2, n), dtype=float)
    if rf.ch0:
        dataset[0, : len(rf.ch0)] = rf.ch0
    if rf.ch1:
        dataset[1, : len(rf.ch1)] = rf.ch1
    signal_names = {}
    if rf.sig0_name:
        signal_names["Sig0"] = rf.sig0_name
    if rf.sig1_name:
        signal_names["Sig1"] = rf.sig1_name
    return {
        "Version": np.array([_VERSION]),
        "SampleRate": np.array([rf.sample_rate], dtype=np.int32),
        "UnderSample": np.array([rf.undersample], dtype=np.int32),
        "SampleNumber": np.array([sample_num], dtype=np.int32),
        "AverageNumber": np.array([avg_num], dtype=np.int32),
        "SignalName": signal_names,
        "DataSet": dataset,
    }


def _raw_dat_to_rawfile(mls) -> RawFile:
    rec = mls[0, 0] if getattr(mls, "ndim", 0) else mls

    def field(name: str):
        if not getattr(rec.dtype, "names", None):
            return None
        return rec[name]

    rf = RawFile()
    v = field("SampleRate")
    if v is not None:
        rf.sample_rate = _scalar(v, int, 0)
    v = field("UnderSample")
    if v is not None:
        rf.undersample = _scalar(v, int, 1)
    v = field("AverageNumber")
    if v is not None:
        rf.avg_num = _scalar(v, int, 1)
    v = field("SampleNumber")
    if v is not None:
        per_average = _scalar(v, int, 0)
        rf.sample_num = per_average * max(1, rf.avg_num)
    v = field("SignalName")
    if v is not None:
        names = []
        sig = v[0, 0] if getattr(v, "ndim", 0) else v
        for fn in (getattr(sig.dtype, "names", None) or ()):
            names.append(_to_str(sig[fn]))
        if names:
            rf.sig0_name = names[0]
        if len(names) > 1:
            rf.sig1_name = names[1]
    v = field("DataSet")
    if v is not None:
        arr = _flatten_float_array(v)
        if arr.ndim == 2:
            # MATLAB stores the original M×N matrix as-is.  Accept a common
            # N×M export too when it has exactly two channels.
            if arr.shape[0] != 2 and arr.shape[1] == 2:
                arr = arr.T
            if arr.shape[0] >= 1:
                rf.ch0 = [float(x) for x in arr[0]]
            if arr.shape[0] >= 2:
                rf.ch1 = [float(x) for x in arr[1]]
        elif arr.ndim == 1:
            rf.ch0 = [float(x) for x in arr]
    # Trust the data matrix over stale metadata.  Legacy files occasionally
    # carry a SampleNumber/AverageNumber combination that no longer matches a
    # manually edited DataSet; downstream FFT code must use the actual rows.
    rf.sample_num = max(len(rf.ch0), len(rf.ch1))
    rf.sample_rate = max(0, int(rf.sample_rate))
    rf.undersample = max(1, int(rf.undersample))
    rf.avg_num = max(1, int(rf.avg_num))
    return rf


def _scalar(value, cast, default):
    """Unwrap the nested object array scipy produces and cast a scalar."""
    v = value
    while isinstance(v, np.ndarray):
        v = v.flat[0]
        if isinstance(v, np.ndarray) and v.size == 1:
            continue
        break
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _flatten_float_array(value) -> np.ndarray:
    v = value
    while isinstance(v, np.ndarray) and v.dtype == object:
        v = v.flat[0]
    return np.asarray(v, dtype=float)


def _to_str(value) -> str:
    arr = np.asarray(value)
    if arr.size == 0:
        return ""
    # MATLAB char arrays are commonly loaded as one element per character;
    # scalar string arrays should remain scalar.  Supporting both forms is
    # required for files written by the original Java MAT writer and scipy.
    if arr.dtype.kind == "U":
        values = [str(item) for item in arr.flat]
        return values[0] if len(values) == 1 else "".join(values)
    if arr.dtype.kind == "S":
        values = [bytes(item).decode("utf-8", errors="replace") for item in arr.flat]
        return values[0] if len(values) == 1 else "".join(values)
    first = arr.flat[0]
    if isinstance(first, bytes):
        return first.decode("utf-8", errors="replace")
    return str(first)
