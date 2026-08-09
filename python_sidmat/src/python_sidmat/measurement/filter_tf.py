"""Frequency-domain filter transfer functions used by Offline Tuner.

This is the small, deterministic part of the old ``FilterTF`` helper.  It
applies the controller's configured filter stages to an already measured
open-loop transfer function; it never changes controller state.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

__all__ = [
    "filter_response",
    "apply_filter_chain",
    "generate_closed_loop",
]


def _safe_div(numerator, denominator):
    numerator, denominator = np.broadcast_arrays(
        np.asarray(numerator, dtype=float),
        np.asarray(denominator, dtype=float),
    )
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        result = np.divide(
            numerator,
            denominator,
            out=np.zeros_like(numerator, dtype=float),
            where=np.abs(denominator) > np.finfo(float).eps,
        )
    return result


def _p(params: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(params), dtype=float).reshape(-1)
    if values.size < 5:
        values = np.pad(values, (0, 5 - values.size), constant_values=0.0)
    return values[:5]


def filter_response(freq, filter_type: int, params: Iterable[float]):
    """Return a filter's complex response at ``freq`` (Hz).

    The equations follow ``SAMBA19xLib.FilterTF``.  Zero denominators are
    mapped to zero so a malformed offline filter cannot fill a plot with NaN.
    """
    f = np.asarray(freq, dtype=float)
    p = _p(params)
    t = int(filter_type)
    x = np.zeros_like(f, dtype=float)
    y = np.zeros_like(f, dtype=float)

    if t == 0:
        return np.ones_like(f, dtype=complex)
    if t == 1:  # LPF1O
        c, gain = p[0], p[2]
        den = f**2 + c**2
        x = _safe_div(gain * c**2, den)
        y = _safe_div(-gain * f * c, den)
    elif t == 2:  # LPF2O
        c, gain = p[0], p[2]
        den = f**4 + c**4
        x = _safe_div(-gain * (f**2 - c**2) * c**2, den)
        y = _safe_div(-gain * np.sqrt(2.0) * c**3 * f, den)
    elif t == 3:  # HPF1O
        c, gain = p[0], p[2]
        den = f**2 + c**2
        x = _safe_div(gain * f**2, den)
        y = _safe_div(gain * f * c, den)
    elif t == 4:  # HPF2O
        c, gain = p[0], p[2]
        den = f**4 + c**4
        x = _safe_div(gain * (f**2 - c**2) * f**2, den)
        y = _safe_div(gain * np.sqrt(2.0) * f**3 * c, den)
    elif t == 5:  # BPF
        c, q, gain = p[0], p[1], p[2]
        den = q**2 * f**4 - 2.0 * q**2 * f**2 * c**2 + c**4 * q**2 + f**2 * c**2
        x = _safe_div(gain * f**2 * c**2, den)
        y = _safe_div(-gain * f * (f**2 - c**2) * c * q, den)
    elif t == 6:  # NOTCH
        c, q, gain = p[0], p[1], p[2]
        den = q**2 * f**4 - 2.0 * q**2 * f**2 * c**2 + c**4 * q**2 + f**2 * c**2
        num = f**2 - 1.0001 * c**2
        x = _safe_div(gain * num**2 * q**2, den)
        y = _safe_div(gain * num * f * c * q, den)
    elif t == 7:  # PID
        pg, ig, dg = p[:3]
        x = np.full_like(f, pg)
        y = _safe_div(2.0 * dg * f**2 - ig / np.pi, 2.0 * f)
    elif t == 9:  # INOTCH
        c, numerator, denominator = p[:3]
        den = f**4 - 2.0 * f**2 * c**2 + c**4 + 4.0 * denominator**2 * c**2 * f**2
        x = _safe_div(
            f**4 - 2.0 * f**2 * c**2 + c**4
            + 4.0 * f**2 * numerator * c**2 * denominator,
            den,
        )
        y = _safe_div(-2.0 * f * c * (f**2 - c**2) * (numerator - denominator), den)
    elif t == 13:  # LL1O
        f1, f2, gain = p[:3]
        den = f2**2 + f**2
        x = _safe_div(gain * (f1 * f2 + f**2), den)
        y = _safe_div(-gain * f * (-f2 + f1), den)
    elif t == 14:  # LL2O
        f1, f2, gain = p[:3]
        den = 2500.0 * f**4 + 41.0 * f**2 * f2**2 + 2500.0 * f2**4
        x = _safe_div(
            gain * (
                2500.0 * f**4 - 2500.0 * f**2 * f2**2
                - 2500.0 * f1**2 * f**2 + 2500.0 * f1**2 * f2**2
                + 5041.0 * f1 * f**2 * f2
            ),
            den,
        )
        y = _safe_div(
            -3550.0 * gain * f * (
                f1 * f**2 - f1 * f2**2 - f2 * f**2 + f2 * f1**2
            ),
            den,
        )
    elif t == 15:  # ANOTCH
        f1, f2, gain = p[:3]
        den = f1**2 * (f**4 - 2.0 * f**2 * f2**2 + f2**4 + 4.0 * gain**2 * f**2 * f2**2)
        x = _safe_div(
            f2**2 * (
                f**4 - f**2 * f2**2 - f1**2 * f**2 + f1**2 * f2**2
                + 4.0 * gain**2 * f**2 * f2 * f1
            ),
            den,
        )
        y = _safe_div(
            2.0 * gain * f * f2**2 * (
                -f**2 * f1 + f1 * f2**2 + f2 * f**2 - f2 * f1**2
            ),
            den,
        )
    elif t == 16:  # HPFQF in the original numeric table
        c, q = p[0], p[1]
        den = f**4 - 2.0 * f**2 * c**2 + c**4 + 4.0 * q**2 * f**2 * c**2
        x = _safe_div((f**2 - c**2) * f**2, den)
        y = _safe_div(2.0 * q * c * f**3, den)
    elif t == 17:  # LPFQF in the original numeric table
        c, q = p[0], p[1]
        den = f**4 - 2.0 * f**2 * c**2 + c**4 + 4.0 * q**2 * f**2 * c**2
        x = _safe_div(-(f**2 - c**2) * c**2, den)
        y = _safe_div(-2.0 * q * f * c**3, den)
    elif t == 19:  # BPF2E
        f1, f2, gain = p[:3]
        center = np.sqrt(np.maximum(f1 * f2, 0.0))
        q = np.where(np.isclose(f1, f2), np.sqrt(2.0), center * f1 / (center**2 - f1**2))
        inv_q = _safe_div(1.0, q)
        den = inv_q**2 * f**2 * center**2 - 2.0 * center**2 * f**2 + center**4 + f**4
        x = _safe_div(gain * inv_q**2 * f**2 * center**2, den)
        y = _safe_div(gain * f * center * inv_q * (center**2 - f**2), den)
    elif t == 20:  # LINTEG
        lim, zero_db, gain = p[:3]
        den = lim**2 + f**2
        x = _safe_div(gain * lim * zero_db, den)
        y = _safe_div(-gain * f * zero_db, den)
    elif t == 21:  # VAR_FILT (legacy FilterTF equation)
        den_f, num_f, num_d, den_d, gain = p
        den = den_f**2 * (
            f**4 - 2.0 * f**2 * num_f**2 + num_f**4
            + 4.0 * den_d**2 * f**2 * num_f**2
        )
        x = _safe_div(
            gain * num_f**2 * (
                f**4 - f**2 * num_f**2 - den_f**2 * f**2 + den_f**2 * num_f**2
                + 4.0 * num_d * f**2 * den_d * num_f * den_f
            ),
            den,
        )
        y = _safe_div(
            2.0 * gain * f * num_f**2 * (
                -num_d * f**2 * den_f + num_d * den_f * num_f**2
                + den_d * num_f * f**2 - den_d * num_f * den_f**2
            ),
            den,
        )
    elif t == 22:  # ANOTCH5P
        num_f, _num_d, den_f, den_d, gain = p
        den = num_f**2 * (
            f**4 - 2.0 * f**2 * den_f**2 + den_f**4
            + 4.0 * den_d**2 * f**2 * den_f**2
        )
        x = _safe_div(
            gain * den_f**2 * (
                f**4 - f**2 * den_f**2 - num_f**2 * f**2 + num_f**2 * den_f**2
                + 4.0 * den_d**2 * f**2 * den_f * num_f
            ),
            den,
        )
        y = _safe_div(
            2.0 * gain * f * den_f**2 * (
                -f**2 * num_f + num_f * den_f**2 + den_f * f**2 - den_f * num_f**2
            ),
            den,
        )
    elif t == 23:  # LOPID
        p_gain, i_gain, d_gain, low, high = p
        x = np.full_like(f, p_gain)
        y = _safe_div(2.0 * d_gain * f**2 - i_gain / np.pi, 2.0 * f)
        magnitude = np.abs(x + 1j * y)
        scale = np.ones_like(f)
        if high > 0:
            scale = np.where(
                magnitude > high,
                high / np.maximum(magnitude, np.finfo(float).eps),
                scale,
            )
        if low > 0:
            scale = np.where(
                (magnitude < low) & (magnitude > 0), low / magnitude, scale
            )
        x *= scale
        y *= scale
    elif t == 24:  # SAMPHold
        # SAMPHold, the last legacy enum, is the only type 24 equation in the
        # original FilterTF source.
        sample_period, hold_fraction = p[:2]
        omega = 2.0 * np.pi * f
        angle = _safe_div(omega, sample_period)
        hold_angle = _safe_div(omega * hold_fraction, sample_period)
        x = _safe_div(
            sample_period
            * (
                np.sin(angle) * np.cos(hold_angle)
                - np.sin(hold_angle)
                + np.sin(hold_angle) * np.cos(angle)
            ),
            omega,
        )
        y = _safe_div(
            -sample_period
            * (
                np.cos(hold_angle)
                - np.cos(hold_angle) * np.cos(angle)
                + np.sin(angle) * np.sin(hold_angle)
            ),
            omega,
        )
    elif t == 8 or t == 10 or t == 11 or t == 12 or t == 18:
        raise ValueError(f"filter type {t} is not supported by the legacy TF equations")
    else:
        raise ValueError(f"unknown filter type {t}")
    return np.nan_to_num(x + 1j * y, nan=0.0, posinf=0.0, neginf=0.0)


def apply_filter_chain(freq, amplitude, filters: Iterable[object]):
    """Apply controller ``FilterStage`` objects in order to a complex TF."""
    freq = np.asarray(freq, dtype=float)
    result = np.asarray(amplitude, dtype=complex).copy()
    if result.shape != freq.shape:
        raise ValueError("frequency and transfer-function arrays must have the same shape")
    for stage in filters:
        filter_type = int(getattr(stage, "filter_type", getattr(stage, "type", 0)))
        params = getattr(stage, "params", getattr(stage, "par", ()))
        result *= filter_response(freq, filter_type, params)
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def generate_closed_loop(filtered_open_loop):
    """Port ``FilterTF.GenerateTFCL`` exactly, including its sign convention."""
    h = np.asarray(filtered_open_loop, dtype=complex)
    current = -h.real + 1j * h.imag
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        value = current / (1.0 + current)
    result = -value.real + 1j * value.imag
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
