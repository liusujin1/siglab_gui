"""Window functions for Welch transfer-function estimation.

Direct port of ``SAMBA19xLib.PwelchTF.CalcWindow`` from the decompiled
C# source.  Returns both the window samples and the C# ``WinAreaCorr``
(= sum(w[i]**2) / length), which is what ``pwelch`` uses for amplitude
normalisation.

The C# implementation performs integer division in two spots that matter
for exactness: ``(length - 1) // 2`` in GAUSS and the LANCZOS ``x`` term.
Those are reproduced here so results match the original bit-for-bit.
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np

__all__ = [
    "WindowType",
    "WINDOW_NAMES",
    "calc_window",
    "window_scale",
]


class WindowType(IntEnum):
    """Mirror ``PwelchTF.FFTWindowsType`` (order matters)."""

    RECTANGULAR = 0
    BLACKMAN = 1
    HAMMING = 2
    HANNING = 3
    LANCZOS = 4
    FLATTOP = 5
    BARTLETT = 6
    BARTLETT_HANN = 7
    GAUSS = 8
    LASTWINDOW = 9


WINDOW_NAMES: dict[WindowType, str] = {
    WindowType.RECTANGULAR: "Rectangular",
    WindowType.BLACKMAN: "Blackman",
    WindowType.HAMMING: "Hamming",
    WindowType.HANNING: "Hanning",
    WindowType.LANCZOS: "Lanczos",
    WindowType.FLATTOP: "FlatTop",
    WindowType.BARTLETT: "Bartlett",
    WindowType.BARTLETT_HANN: "Bartlett-Hann",
    WindowType.GAUSS: "Gauss",
}


def window_scale(wintype: WindowType | int) -> float:
    """Return the C# ``WinScale`` constant (informational; unused in pwelch)."""
    return {
        WindowType.RECTANGULAR: 1.0,
        WindowType.BLACKMAN: 1.73,
        WindowType.HAMMING: 1.37,
        WindowType.HANNING: 1.5,
        WindowType.LANCZOS: 1.3,
        WindowType.FLATTOP: 3.77,
        WindowType.BARTLETT: 1.33,
        WindowType.BARTLETT_HANN: 1.46,
        WindowType.GAUSS: 1.45,
    }[WindowType(wintype)]


def calc_window(length: int, wintype: WindowType | int) -> tuple[np.ndarray, float]:
    """Build one analysis window.

    Returns ``(windata, win_area_corr)`` where ``windata`` has shape
    ``(length,)`` and ``win_area_corr == sum(w[i]**2) / length``.
    """
    n = int(length)
    if n <= 1:
        raise ValueError(f"window length must be > 1, got {n}")
    wt = WindowType(wintype)
    i = np.arange(n, dtype=np.float64)

    if wt == WindowType.HAMMING:
        w = 0.53836 - 0.46164 * np.cos(2.0 * np.pi * i / (n - 1))
    elif wt == WindowType.HANNING:
        w = 0.5 * (1.0 - np.cos(2.0 * np.pi * i / (n - 1)))
    elif wt == WindowType.GAUSS:
        # C#: num2 = (i - (length-1)/2) / (0.4*(length-1)/2) with INTEGER division
        # of (length-1)/2.  Reproduce exactly.
        half = (n - 1) // 2
        denom = 0.4 * (n - 1) / 2.0
        x = (i - half) / denom
        w = np.exp(-0.5 * x * x)
    elif wt == WindowType.BLACKMAN:
        w = (
            0.42
            + 0.08 * np.cos(4.0 * i * np.pi / (n - 1))
            - 0.5 * np.cos(2.0 * i * np.pi / (n - 1))
        )
    elif wt == WindowType.LANCZOS:
        x = i / (n - 1) - 0.5
        # np.sinc is sin(pi*x)/(pi*x) and defines the centre sample as 1.0,
        # avoiding the 0/0 warning produced by the original vector formula.
        w = np.sinc(x)
    elif wt == WindowType.FLATTOP:
        t = i / (n - 1)
        w = (
            1.0
            - 1.93 * np.cos(2.0 * np.pi * t)
            + 1.29 * np.cos(4.0 * np.pi * t)
            - 0.388 * np.cos(6.0 * np.pi * t)
            + 0.0322 * np.cos(8.0 * np.pi * t)
        )
    elif wt == WindowType.BARTLETT:
        w = 2.0 / (n - 1) * ((n - 1) / 2.0 - np.abs(i - (n - 1) / 2.0))
    elif wt == WindowType.BARTLETT_HANN:
        w = (
            0.62
            - 0.48 * np.abs(i / (n - 1) - 0.5)
            - 0.38 * np.cos(2.0 * np.pi * i / (n - 1))
        )
    else:  # RECTANGULAR (and unknown → rectangular, matching C# default case)
        w = np.ones(n, dtype=np.float64)

    win_area_corr = float(np.sum(w * w) / n)
    return w, win_area_corr
