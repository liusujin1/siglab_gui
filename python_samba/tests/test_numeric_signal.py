"""Golden compatibility tests for the NumPy-only record signal layer."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from python_samba.logging_tools.numeric_signal import (
    butter_sos,
    detrend,
    periodic_hann,
    sosfiltfilt,
    welch_psd,
)


FIXTURE = Path(__file__).with_name("fixtures") / "numeric_signal_scipy_1_18.json"


@pytest.fixture(scope="module")
def oracle() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_detrend_and_periodic_hann_match_scipy_golden(oracle):
    values = np.asarray(oracle["input"], dtype=np.float64)
    np.testing.assert_allclose(
        detrend(values, "constant"), oracle["detrend_constant"],
        rtol=1e-9, atol=1e-12,
    )
    np.testing.assert_allclose(
        detrend(values, "linear"), oracle["detrend_linear"],
        rtol=1e-9, atol=1e-12,
    )
    np.testing.assert_allclose(
        periodic_hann(len(values)), oracle["periodic_hann"],
        rtol=1e-9, atol=1e-12,
    )


def test_fft_and_welch_match_scipy_golden(oracle):
    values = np.asarray(oracle["input"], dtype=np.float64)
    rate = float(oracle["fs"])
    window = periodic_hann(len(values))
    spectrum = np.abs(np.fft.rfft((values - np.mean(values)) * window)) / np.sum(window)
    spectrum[1:-1] *= 2.0
    frequency = np.fft.rfftfreq(len(values), d=1.0 / rate)
    np.testing.assert_allclose(frequency, oracle["fft_frequency"], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(spectrum, oracle["fft_amplitude"], rtol=1e-9, atol=1e-12)

    frequency, density = welch_psd(values, rate, 64, overlap=0.5)
    np.testing.assert_allclose(frequency, oracle["welch_frequency"], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(density, oracle["welch_density"], rtol=1e-9, atol=1e-12)


@pytest.mark.parametrize("case_index", range(15))
def test_butterworth_sos_and_zero_phase_output_match_scipy_golden(oracle, case_index):
    case = oracle["filters"][case_index]
    values = np.asarray(oracle["input"], dtype=np.float64)
    sections = butter_sos(case["order"], case["cutoff"], case["kind"], oracle["fs"])
    np.testing.assert_allclose(sections, case["sos"], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(
        sosfiltfilt(sections, values), case["output"], rtol=1e-9, atol=1e-12,
    )


def test_sosfiltfilt_preserves_scipy_pad_length_error():
    sections = butter_sos(4, 80.0, "lowpass", 1000.0)
    with pytest.raises(
        ValueError,
        match=r"length of the input vector x must be greater than padlen, which is 15",
    ):
        sosfiltfilt(sections, np.arange(15, dtype=np.float64))
    assert sosfiltfilt(sections, np.arange(16, dtype=np.float64)).shape == (16,)


def test_numeric_signal_argument_validation():
    with pytest.raises(ValueError, match="one-dimensional"):
        detrend(np.zeros((2, 2)))
    with pytest.raises(ValueError, match="positive integer"):
        butter_sos(0, 10.0, "lowpass", 100.0)
    with pytest.raises(ValueError, match="Nyquist"):
        butter_sos(2, 50.0, "lowpass", 100.0)
    with pytest.raises(ValueError, match="increasing"):
        butter_sos(2, [20.0, 10.0], "bandpass", 100.0)
    with pytest.raises(ValueError, match="overlap"):
        welch_psd(np.arange(16.0), 100.0, 8, overlap=1.0)
