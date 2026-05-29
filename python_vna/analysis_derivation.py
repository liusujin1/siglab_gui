from __future__ import annotations

import numpy as np


DERIVE_BASE_TO_TOP = "base_to_top"
DERIVE_TOP_TO_BASE = "top_to_base"


def derive_psd_from_transfer(
    psd_frequency: np.ndarray,
    psd_values: np.ndarray,
    transfer_frequency: np.ndarray,
    transfer_values: np.ndarray,
    *,
    direction: str = DERIVE_BASE_TO_TOP,
    regularization_floor: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert one endpoint PSD through H_top/base."""
    f_psd, psd = _sorted_finite_real_pair(psd_frequency, psd_values, positive_y=True)
    f_h, h = _sorted_finite_complex_pair(transfer_frequency, transfer_values)
    if f_psd.size < 2 or f_h.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)

    low = max(float(f_psd[0]), float(f_h[0]))
    high = min(float(f_psd[-1]), float(f_h[-1]))
    if not high > low:
        return np.array([], dtype=float), np.array([], dtype=float)
    keep = (f_psd >= low) & (f_psd <= high)
    f_out = f_psd[keep]
    psd_out = psd[keep]
    if f_out.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)

    h_interp = interpolate_complex_transfer(f_h, h, f_out)
    magnitude_sq = np.abs(h_interp) ** 2
    if _normal_direction(direction) == DERIVE_TOP_TO_BASE:
        floor_sq = max(float(regularization_floor), 0.0) ** 2
        psd_out = np.divide(
            psd_out,
            np.maximum(magnitude_sq, floor_sq),
            out=np.zeros_like(psd_out, dtype=float),
            where=np.isfinite(magnitude_sq),
        )
    else:
        psd_out = psd_out * magnitude_sq
    valid = np.isfinite(f_out) & np.isfinite(psd_out) & (f_out > 0.0) & (psd_out > 0.0)
    return f_out[valid], psd_out[valid]


def derive_time_from_transfer(
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate: float,
    transfer_frequency: np.ndarray,
    transfer_values: np.ndarray,
    *,
    direction: str = DERIVE_BASE_TO_TOP,
    regularization_floor: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """Approximate endpoint time history using complex H_top/base."""
    if not np.iscomplexobj(transfer_values):
        return np.array([], dtype=float), np.array([], dtype=float)
    if not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    t, y = _sorted_finite_real_pair(time_s, values, positive_y=False, positive_x=False)
    f_h, h = _sorted_finite_complex_pair(transfer_frequency, transfer_values)
    if t.size < 2 or y.size < 2 or f_h.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)

    count = min(t.size, y.size)
    t = t[:count]
    work = y[:count] - float(np.mean(y[:count]))
    freqs = np.fft.rfftfreq(work.size, d=1.0 / float(sample_rate))
    spectrum = np.fft.rfft(work)
    h_interp = np.zeros(freqs.shape, dtype=complex)
    in_band = (freqs >= float(f_h[0])) & (freqs <= float(f_h[-1])) & (freqs > 0.0)
    if np.any(in_band):
        h_interp[in_band] = interpolate_complex_transfer(f_h, h, freqs[in_band])

    if _normal_direction(direction) == DERIVE_TOP_TO_BASE:
        floor = max(float(regularization_floor), 0.0)
        magnitude = np.abs(h_interp)
        safe = in_band & (magnitude >= floor)
        converted_spectrum = np.zeros_like(spectrum)
        converted_spectrum[safe] = spectrum[safe] / h_interp[safe]
    else:
        converted_spectrum = spectrum * h_interp
    converted = np.fft.irfft(converted_spectrum, n=work.size)
    valid = np.isfinite(t) & np.isfinite(converted)
    return t[valid], converted[valid]


def interpolate_complex_transfer(
    source_frequency: np.ndarray,
    source_transfer: np.ndarray,
    target_frequency: np.ndarray,
) -> np.ndarray:
    f_h, h = _sorted_finite_complex_pair(source_frequency, source_transfer)
    f_target = np.asarray(target_frequency, dtype=float).ravel()
    if f_h.size < 2 or f_target.size == 0:
        return np.zeros(f_target.shape, dtype=complex)
    real = np.interp(f_target, f_h, np.real(h))
    imag = np.interp(f_target, f_h, np.imag(h))
    return real + 1.0j * imag


def has_complex_transfer_phase(transfer_values: np.ndarray) -> bool:
    return bool(np.iscomplexobj(transfer_values))


def _normal_direction(direction: str) -> str:
    text = str(direction or "").strip().lower()
    if text in {DERIVE_TOP_TO_BASE, "top->base", "top_to_base", "顶部到地基"}:
        return DERIVE_TOP_TO_BASE
    return DERIVE_BASE_TO_TOP


def _sorted_finite_real_pair(
    x_values: np.ndarray,
    y_values: np.ndarray,
    *,
    positive_y: bool,
    positive_x: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float).ravel()
    y = np.asarray(y_values, dtype=float).ravel()
    count = min(x.size, y.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    x = x[:count]
    y = y[:count]
    valid = np.isfinite(x) & np.isfinite(y)
    if positive_x:
        valid &= x > 0.0
    if positive_y:
        valid &= y > 0.0
    x = x[valid]
    y = y[valid]
    if x.size == 0:
        return x, y
    order = np.argsort(x)
    return x[order], y[order]


def _sorted_finite_complex_pair(
    frequency: np.ndarray,
    transfer: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequency, dtype=float).ravel()
    h = np.asarray(transfer).ravel()
    count = min(f.size, h.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=complex)
    f = f[:count]
    h = h[:count]
    valid = np.isfinite(f) & np.isfinite(np.real(h)) & np.isfinite(np.imag(h)) & (f > 0.0)
    f = f[valid]
    h = h[valid]
    if f.size == 0:
        return f, h.astype(complex)
    order = np.argsort(f)
    return f[order], h[order].astype(complex)
