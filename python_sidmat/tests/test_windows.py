"""Window function tests — validate against the C# formulas.

References (from ``SAMBA19xLib.PwelchTF.CalcWindow``):
  HAMMING  w[i] = 0.53836 - 0.46164 * cos(2*pi*i/(N-1))
  HANNING  w[i] = 0.5 * (1 - cos(2*pi*i/(N-1)))
  BLACKMAN w[i] = 0.42 + 0.08*cos(4*pi*i/(N-1)) - 0.5*cos(2*pi*i/(N-1))
  BARTLETT w[i] = 2/(N-1) * ((N-1)/2 - |i-(N-1)/2|)
  GAUSS    w[i] = exp(-0.5*((i-(N-1)//2)/(0.4*(N-1)/2))^2)
  LANCZOS  w[i] = sin(pi*x)/(pi*x), x = i/(N-1) - 0.5
  FLATTOP  w[i] = 1 - 1.93 cos(2pi t) + 1.29 cos(4pi t) - 0.388 cos(6pi t)
                  + 0.0322 cos(8pi t), t = i/(N-1)
  BARTLETT_HANN w[i] = 0.62 - 0.48|i/(N-1)-0.5| - 0.38 cos(2pi i/(N-1))
"""

from __future__ import annotations

import numpy as np
import pytest

from python_sidmat.analysis.windows import (
    WindowType,
    calc_window,
    window_scale,
)


def _ref(length: int, wt: WindowType) -> np.ndarray:
    n = length
    i = np.arange(n, dtype=np.float64)
    if wt == WindowType.HAMMING:
        return 0.53836 - 0.46164 * np.cos(2 * np.pi * i / (n - 1))
    if wt == WindowType.HANNING:
        return 0.5 * (1 - np.cos(2 * np.pi * i / (n - 1)))
    if wt == WindowType.BLACKMAN:
        return (
            0.42
            + 0.08 * np.cos(4 * np.pi * i / (n - 1))
            - 0.5 * np.cos(2 * np.pi * i / (n - 1))
        )
    if wt == WindowType.BARTLETT:
        return 2.0 / (n - 1) * ((n - 1) / 2.0 - np.abs(i - (n - 1) / 2.0))
    if wt == WindowType.GAUSS:
        half = (n - 1) // 2  # C# integer division
        x = (i - half) / (0.4 * (n - 1) / 2.0)
        return np.exp(-0.5 * x * x)
    if wt == WindowType.LANCZOS:
        x = i / (n - 1) - 0.5
        return np.where(x == 0, 1.0, np.sin(np.pi * x) / (np.pi * x))
    if wt == WindowType.FLATTOP:
        t = i / (n - 1)
        return (
            1.0
            - 1.93 * np.cos(2 * np.pi * t)
            + 1.29 * np.cos(4 * np.pi * t)
            - 0.388 * np.cos(6 * np.pi * t)
            + 0.0322 * np.cos(8 * np.pi * t)
        )
    if wt == WindowType.BARTLETT_HANN:
        return (
            0.62
            - 0.48 * np.abs(i / (n - 1) - 0.5)
            - 0.38 * np.cos(2 * np.pi * i / (n - 1))
        )
    return np.ones(n)


@pytest.mark.parametrize(
    "wt",
    [
        WindowType.HAMMING,
        WindowType.HANNING,
        WindowType.BLACKMAN,
        WindowType.BARTLETT,
        WindowType.GAUSS,
        WindowType.LANCZOS,
        WindowType.FLATTOP,
        WindowType.BARTLETT_HANN,
        WindowType.RECTANGULAR,
    ],
)
@pytest.mark.parametrize("length", [16, 64, 512, 1024])
def test_window_matches_reference(wt: WindowType, length: int) -> None:
    w, area_corr = calc_window(length, wt)
    ref = _ref(length, wt)
    np.testing.assert_allclose(w, ref, rtol=1e-12, atol=1e-12)
    # WinAreaCorr = sum(w^2)/length
    expected = float(np.sum(ref**2) / length)
    assert area_corr == pytest.approx(expected, rel=1e-12)


def test_rectangular_window_ones() -> None:
    w, area_corr = calc_window(256, WindowType.RECTANGULAR)
    np.testing.assert_array_equal(w, np.ones(256))
    assert area_corr == pytest.approx(1.0)


def test_window_scale_constants() -> None:
    assert window_scale(WindowType.HANNING) == 1.5
    assert window_scale(WindowType.FLATTOP) == 3.77
    assert window_scale(WindowType.RECTANGULAR) == 1.0


def test_window_endpoints() -> None:
    # Hanning starts and ends at 0
    w, _ = calc_window(1024, WindowType.HANNING)
    assert w[0] == pytest.approx(0.0, abs=1e-12)
    assert w[-1] == pytest.approx(0.0, abs=1e-12)
    # Hamming endpoints are small but nonzero
    w, _ = calc_window(1024, WindowType.HAMMING)
    assert w[0] == pytest.approx(0.53836 - 0.46164, abs=1e-12)


def test_invalid_length() -> None:
    with pytest.raises(ValueError):
        calc_window(1, WindowType.HANNING)
