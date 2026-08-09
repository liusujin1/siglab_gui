"""pwelch transfer-function tests.

Validates the numpy port of ``SAMBA19xLib.PwelchTF.pwelch`` against known
signals: a pure-sine input/output pair must give H1 ~ amplitude ratio,
coherence ~ 1, and phase ~ 0.
"""

from __future__ import annotations

import numpy as np
import pytest

from python_sidmat.analysis.pwelch import PwelchResult, pwelch
from python_sidmat.analysis.windows import WindowType


def _sine(freq: float, fs: float, n: int) -> np.ndarray:
    t = np.arange(n) / fs
    return np.sin(2 * np.pi * freq * t)


def test_output_structure() -> None:
    fs = 1000.0
    n = 4096
    x = _sine(100.0, fs, n)
    res = pwelch(x, x, WindowType.HANNING, 50, 1024, n, fs)
    assert isinstance(res, PwelchResult)
    nout = 1024 // 2 + 1
    assert len(res.freq) == nout
    for arr in (res.re, res.im, res.coherence, res.spec1, res.spec2,
                res.amplitude, res.phase_deg):
        assert arr.shape == (nout,)


def test_identity_transfer_function() -> None:
    """y = x  →  H1 = 1, coherence = 1, phase = 0 at the driving frequency."""
    fs = 2000.0
    n = 8192
    x = _sine(200.0, fs, n)
    y = x.copy()
    res = pwelch(x, y, WindowType.HANNING, 50, 1024, n, fs)

    # Locate the bin nearest 200 Hz
    idx = int(np.argmin(np.abs(res.freq - 200.0)))
    assert res.amplitude[idx] == pytest.approx(1.0, rel=0.05)
    assert abs(res.phase_deg[idx]) < 5.0
    assert res.coherence[idx] == pytest.approx(1.0, abs=0.05)


def test_gain_amplitude() -> None:
    """y = 3*x  →  |H1| = 3 at the driving frequency."""
    fs = 1000.0
    n = 8192
    x = _sine(80.0, fs, n)
    y = 3.0 * x
    res = pwelch(x, y, WindowType.HANNING, 50, 1024, n, fs)
    idx = int(np.argmin(np.abs(res.freq - 80.0)))
    assert res.amplitude[idx] == pytest.approx(3.0, rel=0.05)


def test_phase_shift() -> None:
    """y = x delayed by 90 deg  →  phase(H1) ≈ +90 at that frequency."""
    fs = 1000.0
    n = 8192
    f = 100.0
    x = _sine(f, fs, n)
    y = np.cos(2 * np.pi * f * np.arange(n) / fs)  # 90 deg ahead of sine
    res = pwelch(x, y, WindowType.HANNING, 50, 1024, n, fs)
    idx = int(np.argmin(np.abs(res.freq - f)))
    assert res.phase_deg[idx] == pytest.approx(90.0, abs=6.0)


def test_coherence_drops_with_noise() -> None:
    """Adding strong noise reduces coherence well below 1."""
    rng = np.random.default_rng(42)
    fs = 1000.0
    n = 8192
    x = _sine(100.0, fs, n)
    y = x + 2.0 * rng.standard_normal(n)
    res = pwelch(x, y, WindowType.HANNING, 50, 1024, n, fs)
    idx = int(np.argmin(np.abs(res.freq - 100.0)))
    # Coherence stays reasonably high at the tone but is not ~1 everywhere
    assert res.coherence[idx] > 0.5
    assert float(np.median(res.coherence)) < 0.9


def test_frequency_axis() -> None:
    res = pwelch(_sine(10, 1000, 2048), _sine(10, 1000, 2048),
                 WindowType.RECTANGULAR, 0, 256, 2048, fs=1000.0)
    expected = np.fft.rfftfreq(256, d=1.0 / 1000.0)[: 256 // 2 + 1]
    np.testing.assert_allclose(res.freq, expected, rtol=1e-12)


def test_rectangular_no_overlap_matches_direct_fft() -> None:
    """Single segment, rectangular window, no overlap: H1 equals ratio of
    the single-segment DFTs."""
    fs = 1000.0
    n = 1024
    x = _sine(50.0, fs, n)
    y = 2.0 * _sine(50.0, fs, n) + 0.1 * _sine(200.0, fs, n)
    res = pwelch(x, y, WindowType.RECTANGULAR, 0, n, n, fs)

    X = np.fft.fft(x)
    Y = np.fft.fft(y)
    nout = n // 2 + 1
    H1 = Y[:nout] / X[:nout]
    np.testing.assert_allclose(res.re + 1j * res.im, H1, rtol=1e-10)


def test_length_param_truncates() -> None:
    fs = 1000.0
    full = 8192
    x = _sine(50.0, fs, full)
    y = x.copy()
    # Use only first 4096 samples
    res = pwelch(x, y, WindowType.HANNING, 50, 512, 4096, fs)
    assert res.amplitude.shape[0] == 512 // 2 + 1


def test_invalid_args() -> None:
    x = np.zeros(256)
    with pytest.raises(ValueError):
        pwelch(x, x, WindowType.HANNING, 50, 1024, 256, 1000.0)  # nfft > length
    with pytest.raises(ValueError):
        pwelch(np.zeros(100), np.zeros(100), WindowType.HANNING, 50, 64, 200, 1.0)
    with pytest.raises(ValueError):
        pwelch(x, x, WindowType.HANNING, 100, 64, 256, 1000.0)
    with pytest.raises(ValueError):
        pwelch(x, x, WindowType.HANNING, 50, 64, 256, 0.0)


def test_odd_nfft_folds_last_positive_bin() -> None:
    """Odd FFTs have no Nyquist bin; the last positive bin is still valid."""
    x = _sine(50.0, 1000.0, 3000)
    res = pwelch(x, 2.0 * x, WindowType.HANNING, 50, 15, len(x), 1000.0)
    idx = int(np.argmin(np.abs(res.freq - 50.0)))
    assert res.amplitude[idx] == pytest.approx(2.0, rel=0.05)
    assert res.coherence[idx] == pytest.approx(1.0, abs=0.05)
