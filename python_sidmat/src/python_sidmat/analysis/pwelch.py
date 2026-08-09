"""Welch transfer-function estimation (H1) — port of ``SAMBA19xLib.PwelchTF``.

The decompiled C# ``pwelch`` computes, over overlapping windowed segments:

* auto-spectra  ``Sxx`` (input) and ``Syy`` (output) as sum of |FFT|^2
* cross-spectrum ``Cxy`` = sum(conj(X) * Y) — equivalently the C# real/imag
  accumulation
* single-sided spectra with negative-frequency folding
* **H1** = Cxy / Sxx, coherence gamma^2 = |Cxy|^2 / (Sxx*Syy),
  auto-spectra (RMS amplitude), magnitude and phase of H1

The C# FFT is called with ``Direction.Backward`` which applies **no** 1/N
normalisation — exactly matching numpy's default ``np.fft.fft``, so no
scaling differences arise.

Outputs are returned as a conventional one-sided spectrum with
``nout = nfft // 2 + 1`` bins: DC … Nyquist (or the highest positive bin for
odd ``nfft``).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np

from python_sidmat.analysis.windows import WindowType, calc_window

__all__ = ["pwelch", "PwelchResult"]


@dataclass(frozen=True, slots=True)
class PwelchResult:
    """One-sided transfer-function estimate (H1)."""

    freq: np.ndarray          # Hz, length nout
    re: np.ndarray            # H1 real part
    im: np.ndarray            # H1 imaginary part
    coherence: np.ndarray     # gamma^2, 0..1
    spec1: np.ndarray         # RMS amplitude spectrum of input
    spec2: np.ndarray         # RMS amplitude spectrum of output
    amplitude: np.ndarray     # |H1|
    phase_deg: np.ndarray     # phase of H1 in degrees


def _fold_negative(
    sxx: np.ndarray,
    syy: np.ndarray,
    cxy: np.ndarray,
    nout: int,
    nfft: int,
) -> None:
    """Fold the negative-frequency bins into a one-sided spectrum.

    An even-length FFT has a Nyquist bin which is kept as-is.  An odd-length
    FFT has no Nyquist bin, so its last positive-frequency bin must also be
    folded.  The old implementation always treated the last bin as Nyquist,
    which made odd ``nfft`` results lose energy and reduced coherence.
    """
    last_fold = nout - 2 if nfft % 2 == 0 else nout - 1
    for idx in range(1, last_fold + 1):
        mirror = len(sxx) - idx
        sxx[idx] += sxx[mirror]
        syy[idx] += syy[mirror]
        cxy[idx] += np.conj(cxy[mirror])


def pwelch(
    data1: np.ndarray | list[float],
    data2: np.ndarray | list[float],
    wintype: WindowType | int,
    overlap_pct: int,
    nfft: int,
    length: int | None = None,
    fs: float = 1.0,
) -> PwelchResult:
    """Estimate the transfer function ``H1 = Cxy/Sxx`` with Welch's method.

    Parameters match the C# ``PwelchTF.pwelch`` signature:

    * ``data1`` / ``data2`` — input (reference) and output (response) signals
    * ``wintype`` — one of :class:`~python_sidmat.analysis.windows.WindowType`
    * ``overlap_pct`` — segment overlap in percent (0..99)
    * ``nfft`` — FFT length (power of two in the original; any int works)
    * ``length`` — number of samples to process (defaults to len(data1));
      every complete segment in the requested range is used
    * ``fs`` — sample rate in Hz, used only to build the frequency axis
    """
    x = np.asarray(data1, dtype=np.float64)
    y = np.asarray(data2, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1:
        raise ValueError("data1 and data2 must be one-dimensional")
    if length is None:
        length = len(x)
    length = int(length)
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    if len(x) < length or len(y) < length:
        raise ValueError(
            f"data length {len(x)}/{len(y)} shorter than requested {length}"
        )
    x = x[:length]
    y = y[:length]
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("data1 and data2 must contain only finite values")

    overlap_pct = int(overlap_pct)
    if not 0 <= overlap_pct < 100:
        raise ValueError(f"overlap_pct must be in [0, 100), got {overlap_pct}")

    nfft = int(nfft)
    if nfft < 2 or nfft > length:
        raise ValueError(f"nfft={nfft} out of range for length={length}")
    fs = float(fs)
    if not isfinite(fs) or fs <= 0.0:
        raise ValueError(f"fs must be a finite positive number, got {fs}")

    overlap_samples = nfft * overlap_pct // 100
    step = nfft - overlap_samples
    # Use every complete window.  The previous code limited the segment count
    # to ``length // nfft``, silently discarding the tail when overlap was on.
    starts = range(0, length - nfft + 1, step)
    nseg = len(starts)
    if nseg < 1:  # defensive; nfft <= length already guarantees this
        raise ValueError(f"length={length} too short for nfft={nfft}")
    nout = nfft // 2 + 1

    win, win_area_corr = calc_window(nfft, wintype)

    sxx = np.zeros(nfft, dtype=np.float64)
    syy = np.zeros(nfft, dtype=np.float64)
    cxy = np.zeros(nfft, dtype=np.complex128)

    for start in starts:
        xx = x[start:start + nfft] * win
        yy = y[start:start + nfft] * win
        xxf = np.fft.fft(xx)
        yyf = np.fft.fft(yy)
        sxx += xxf.real**2 + xxf.imag**2
        syy += yyf.real**2 + yyf.imag**2
        # C# accumulates Cxy as conj(X)*Y (real Xr*Yr+Xi*Yi, imag Xr*Yi-Xi*Yr).
        # This keeps H1 = Cxy/Sxx = Y/X, matching the decompiled PwelchTF.
        cxy += np.conj(xxf) * yyf

    _fold_negative(sxx, syy, cxy, nout, nfft)

    scale = 1.0 / (np.sqrt(win_area_corr * 2.0) * nout)

    spec1 = np.sqrt(sxx[:nout] / nseg) * scale
    spec2 = np.sqrt(syy[:nout] / nseg) * scale

    with np.errstate(divide="ignore", invalid="ignore"):
        re = np.where(sxx[:nout] == 0.0, 1e-32, cxy[:nout].real / sxx[:nout])
        im = np.where(sxx[:nout] == 0.0, 1e-32, cxy[:nout].imag / sxx[:nout])
        re = np.where((sxx[:nout] == 0.0) & (cxy[:nout].real == 0.0), 0.0, re)
        im = np.where((sxx[:nout] == 0.0) & (cxy[:nout].imag == 0.0), 0.0, im)

        coh = np.ones(nout, dtype=np.float64)
        denom = sxx[:nout] * syy[:nout]
        nonzero = denom != 0.0
        mag2 = cxy[:nout].real**2 + cxy[:nout].imag**2
        coh[nonzero] = mag2[nonzero] / denom[nonzero]
        # Round-off can produce a value a few ulps above one.  Coherence is a
        # bounded quantity and the UI relies on that invariant for its axis.
        coh = np.clip(coh, 0.0, 1.0)

        amp = np.sqrt(re**2 + im**2)
        phase = np.arctan2(im, re) * 180.0 / np.pi

    freq = np.fft.rfftfreq(nfft, d=1.0 / fs)[:nout]

    return PwelchResult(
        freq=freq,
        re=re,
        im=im,
        coherence=coh,
        spec1=spec1,
        spec2=spec2,
        amplitude=amp,
        phase_deg=phase,
    )
