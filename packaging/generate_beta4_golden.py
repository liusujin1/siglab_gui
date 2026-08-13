"""Regenerate immutable beta.4 compatibility fixtures with SciPy installed.

This script is not part of the runtime or normal build.  It records the
pre-beta.4 SciPy behavior so the NumPy/MAT-v5 replacements can be tested on a
machine where SciPy is completely absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import scipy
from scipy import signal
from scipy.io import savemat


ROOT = Path(__file__).resolve().parents[1]


def _floats(values) -> list[float]:
    return [float(value) for value in np.asarray(values).reshape(-1)]


def write_signal_reference() -> None:
    fs = 1000.0
    index = np.arange(256, dtype=np.float64)
    values = (
        0.75
        + 0.0015 * index
        + np.sin(2.0 * np.pi * 17.0 * index / fs)
        + 0.35 * np.sin(2.0 * np.pi * 183.0 * index / fs)
        + 0.05 * np.cos(2.0 * np.pi * 311.0 * index / fs)
    )
    cases: list[dict[str, object]] = []
    for kind, cutoff in (
        ("lowpass", 80.0),
        ("highpass", 20.0),
        ("bandpass", [20.0, 80.0]),
    ):
        for order in (1, 2, 4, 12):
            sos = signal.butter(order, cutoff, btype=kind, fs=fs, output="sos")
            cases.append(
                {
                    "kind": kind,
                    "cutoff": cutoff,
                    "order": order,
                    "sos": np.asarray(sos).tolist(),
                    "output": _floats(signal.sosfiltfilt(sos, values)),
                }
            )
    for kind, cutoff, order in (
        ("lowpass", 499.0, 4),
        ("highpass", 0.5, 4),
        ("bandpass", [0.5, 499.0], 2),
    ):
        sos = signal.butter(order, cutoff, btype=kind, fs=fs, output="sos")
        cases.append(
            {
                "kind": kind,
                "cutoff": cutoff,
                "order": order,
                "sos": np.asarray(sos).tolist(),
                "output": _floats(signal.sosfiltfilt(sos, values)),
            }
        )

    window = signal.windows.hann(len(values), sym=False)
    centered = values - np.mean(values)
    fft_values = np.abs(np.fft.rfft(centered * window)) / np.sum(window)
    fft_values[1:-1] *= 2.0
    welch_frequency, welch_density = signal.welch(
        values,
        fs=fs,
        window="hann",
        nperseg=64,
        noverlap=32,
        detrend="constant",
        scaling="density",
    )
    payload = {
        "schema": 1,
        "oracle": f"scipy {scipy.__version__}",
        "numpy": np.__version__,
        "fs": fs,
        "input": _floats(values),
        "detrend_constant": _floats(signal.detrend(values, type="constant")),
        "detrend_linear": _floats(signal.detrend(values, type="linear")),
        "periodic_hann": _floats(window),
        "fft_frequency": _floats(np.fft.rfftfreq(len(values), d=1.0 / fs)),
        "fft_amplitude": _floats(fft_values),
        "welch_frequency": _floats(welch_frequency),
        "welch_density": _floats(welch_density),
        "filters": cases,
    }
    destination = ROOT / "python_samba" / "tests" / "fixtures"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "numeric_signal_scipy_1_18.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_mat_references() -> None:
    destination = ROOT / "python_sidmat" / "tests" / "fixtures"
    destination.mkdir(parents=True, exist_ok=True)
    raw = {
        "Version": np.array([2.0]),
        "SampleRate": np.array([2000], dtype=np.int32),
        "UnderSample": np.array([1], dtype=np.int32),
        "SampleNumber": np.array([999], dtype=np.int32),
        "AverageNumber": np.array([2], dtype=np.int32),
        "SignalName": {"Sig0": "位移X", "Sig1": "Y1FB"},
        "DataSet": np.array(
            [[0.0, 1.0, 2.0, 3.0], [0.0, 2.0, 4.0, 6.0]], dtype=np.float64
        ),
    }
    raw3 = {
        "Version": np.array([2.0]),
        "SampleRate": np.array([1000], dtype=np.int32),
        "UnderSample": np.array([2], dtype=np.int32),
        "SampleNumber": np.array([3], dtype=np.int32),
        "AverageNumber": np.array([1], dtype=np.int32),
        "SignalName": {},
        # N×2 orientation must remain accepted by the reader.
        "DataSet": np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]),
    }
    savemat(
        destination / "scipy_sidimat_v5.sidimat19x",
        {
            "MeasurementType": "SiDiMat19x",
            "Version": np.array([2.0]),
            "RawDat1": raw,
            "RawDat3": raw3,
        },
        do_compression=False,
        format="5",
    )
    axis = {
        "Version": np.array([2.0]),
        "Title": "频率",
        "TitleFontSize": np.array([10.0]),
        "Prop": np.array([[1.0, 2.0, 3.0, 4.0]]),
    }
    model = {
        "Version": np.array([2.0]),
        "Title": "FRF",
        "LogX": np.array([1], dtype=np.int16),
        "LogY": np.array([0], dtype=np.int16),
        "Grid": "on",
        "Legend": np.array([1], dtype=np.int16),
        "Xaxis": axis,
        "Yaxis": {**axis, "Title": "幅值"},
        "Annotations": {"Version": np.array([2.0])},
        "Series": {
            "Serie1": {
                "Title": "H1",
                "Serie": np.array([[1.0, 2.0, 4.0], [0.5, 0.25, 0.125]]),
            },
            "Serie3": {
                "Title": "H3",
                # N×2 orientation must remain accepted by the reader.
                "Serie": np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]),
            },
        },
    }
    savemat(
        destination / "scipy_idefigure_v5.idefigure",
        {
            "MeasurementType": "IdeFigure",
            "Version": np.array([2.0]),
            "FigureTitle": "兼容性测试",
            "FigureTitleFontSize": np.array([12.0]),
            "RowNumber": np.array([1], dtype=np.int32),
            "ColumnNumber": np.array([2], dtype=np.int32),
            "Model0": model,
            "Model2": {**model, "Title": "Model 2", "Series": {}},
        },
        do_compression=False,
        format="5",
    )


if __name__ == "__main__":
    write_signal_reference()
    write_mat_references()
