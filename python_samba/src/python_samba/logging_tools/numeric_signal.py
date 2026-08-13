"""Small NumPy-only signal-processing primitives used by SAMBA records.

The Butterworth pole transforms, pole/zero pairing, and forward/backward SOS
filter follow the algorithms used by SciPy's ``signal`` package.  They are
kept here so the portable SAMBA/SIDMAT TestKit does not need the full SciPy
runtime for this deliberately narrow feature set.  See THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

from collections.abc import Sequence
import math

import numpy as np


def detrend(y: Sequence[float] | np.ndarray, mode: str = "constant") -> np.ndarray:
    """Remove a mean or least-squares linear trend from one-dimensional data."""

    values = np.asarray(y, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("detrend input must be one-dimensional")
    kind = str(mode).strip().lower()
    if kind == "constant":
        return values - np.mean(values)
    if kind != "linear":
        raise ValueError("detrend mode must be 'constant' or 'linear'")
    count = values.size
    if count == 0:
        return values.copy()
    design = np.ones((count, 2), dtype=np.float64)
    design[:, 0] = np.arange(1, count + 1, dtype=np.float64) / count
    coefficients = np.linalg.lstsq(design, values, rcond=None)[0]
    return values - design @ coefficients


def periodic_hann(length: int) -> np.ndarray:
    """Return the periodic Hann window used by FFT and Welch analysis."""

    size = int(length)
    if size < 0:
        raise ValueError("window length must be non-negative")
    if size == 0:
        return np.empty(0, dtype=np.float64)
    index = np.arange(size, dtype=np.float64)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * index / size)


def welch_psd(
    y: Sequence[float] | np.ndarray,
    fs: float,
    nperseg: int,
    overlap: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate a one-sided density-scaled PSD using periodic Hann segments."""

    values = np.asarray(y, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("Welch input must be one-dimensional")
    rate = float(fs)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample rate must be positive")
    block = int(nperseg)
    if block < 1 or block > values.size:
        raise ValueError("nperseg must be between 1 and the input length")
    fraction = float(overlap)
    if not 0.0 <= fraction < 1.0:
        raise ValueError("overlap must satisfy 0 <= overlap < 1")
    overlap_samples = int(block * fraction)
    step = block - overlap_samples
    starts = range(0, values.size - block + 1, step)
    window = periodic_hann(block)
    scale = rate * float(np.sum(window * window))
    accumulated = np.zeros(block // 2 + 1, dtype=np.float64)
    segment_count = 0
    for start in starts:
        segment = values[start : start + block]
        segment = segment - float(np.mean(segment))
        spectrum = np.fft.rfft(segment * window)
        density = np.abs(spectrum) ** 2 / scale
        if block % 2 == 0:
            density[1:-1] *= 2.0
        else:
            density[1:] *= 2.0
        accumulated += density
        segment_count += 1
    if not segment_count:
        raise ValueError("input does not contain a complete Welch segment")
    frequencies = np.fft.rfftfreq(block, d=1.0 / rate)
    return frequencies, accumulated / segment_count


def _relative_degree(zeros: np.ndarray, poles: np.ndarray) -> int:
    degree = len(poles) - len(zeros)
    if degree < 0:
        raise ValueError("improper transfer function")
    return degree


def _butterworth_prototype(order: int) -> tuple[np.ndarray, np.ndarray, float]:
    indices = np.arange(-order + 1, order, 2, dtype=np.float64)
    poles = -np.exp(1j * np.pi * indices / (2 * order))
    return np.empty(0, dtype=np.float64), poles, 1.0


def _lowpass_zpk(
    zeros: np.ndarray, poles: np.ndarray, gain: float, cutoff: float
) -> tuple[np.ndarray, np.ndarray, float]:
    degree = _relative_degree(zeros, poles)
    return cutoff * zeros, cutoff * poles, gain * cutoff**degree


def _highpass_zpk(
    zeros: np.ndarray, poles: np.ndarray, gain: float, cutoff: float
) -> tuple[np.ndarray, np.ndarray, float]:
    degree = _relative_degree(zeros, poles)
    transformed_zeros = cutoff / zeros if len(zeros) else zeros.astype(complex)
    transformed_poles = cutoff / poles
    transformed_zeros = np.concatenate((transformed_zeros, np.zeros(degree)))
    transformed_gain = gain * np.real(
        np.prod(-zeros, dtype=complex) / np.prod(-poles, dtype=complex)
    )
    return transformed_zeros, transformed_poles, float(transformed_gain)


def _bandpass_zpk(
    zeros: np.ndarray,
    poles: np.ndarray,
    gain: float,
    center: float,
    bandwidth: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    degree = _relative_degree(zeros, poles)
    zero_lp = np.asarray(zeros * bandwidth / 2.0, dtype=np.complex128)
    pole_lp = np.asarray(poles * bandwidth / 2.0, dtype=np.complex128)
    transformed_zeros = np.concatenate(
        (
            zero_lp + np.sqrt(zero_lp**2 - center**2),
            zero_lp - np.sqrt(zero_lp**2 - center**2),
            np.zeros(degree),
        )
    )
    transformed_poles = np.concatenate(
        (
            pole_lp + np.sqrt(pole_lp**2 - center**2),
            pole_lp - np.sqrt(pole_lp**2 - center**2),
        )
    )
    return transformed_zeros, transformed_poles, gain * bandwidth**degree


def _bilinear_zpk(
    zeros: np.ndarray, poles: np.ndarray, gain: float, fs: float
) -> tuple[np.ndarray, np.ndarray, float]:
    degree = _relative_degree(zeros, poles)
    fs2 = 2.0 * fs
    digital_zeros = (fs2 + zeros) / (fs2 - zeros)
    digital_poles = (fs2 + poles) / (fs2 - poles)
    digital_zeros = np.concatenate((digital_zeros, -np.ones(degree)))
    digital_gain = gain * np.real(
        np.prod(fs2 - zeros, dtype=complex) / np.prod(fs2 - poles, dtype=complex)
    )
    return digital_zeros, digital_poles, float(digital_gain)


def _cplxreal(values: np.ndarray, tolerance: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    data = np.atleast_1d(values)
    if data.size == 0:
        return data, data
    if data.ndim != 1:
        raise ValueError("complex roots must be one-dimensional")
    if tolerance is None:
        tolerance = 100 * np.finfo((1.0 * data).dtype).eps
    data = data[np.lexsort((abs(data.imag), data.real))]
    real_indices = abs(data.imag) <= tolerance * abs(data)
    real_values = data[real_indices].real
    if len(real_values) == len(data):
        return np.array([]), real_values
    data = data[~real_indices]
    positive = data[data.imag > 0]
    negative = data[data.imag < 0]
    if len(positive) != len(negative):
        raise ValueError("complex root has no matching conjugate")
    same_real = np.diff(positive.real) <= tolerance * abs(positive[:-1])
    differences = np.diff(np.concatenate(([0], same_real, [0])))
    starts = np.nonzero(differences > 0)[0]
    stops = np.nonzero(differences < 0)[0]
    for start, stop_index in zip(starts, stops, strict=True):
        stop = stop_index + 1
        for chunk in (positive[start:stop], negative[start:stop]):
            chunk[...] = chunk[np.lexsort([abs(chunk.imag)])]
    if np.any(abs(positive - negative.conj()) > tolerance * abs(negative)):
        raise ValueError("complex root has no matching conjugate")
    return (positive + negative.conj()) / 2.0, real_values


def _nearest_root_index(values: np.ndarray, target: complex, kind: str) -> int:
    order = np.argsort(np.abs(values - target))
    if kind == "any":
        return int(order[0])
    mask = np.isreal(values[order])
    if kind == "complex":
        mask = ~mask
    return int(order[np.nonzero(mask)[0][0]])


def _single_sos(zeros: Sequence[complex], poles: Sequence[complex], gain: float) -> np.ndarray:
    numerator = np.atleast_1d(gain * np.poly(zeros))
    denominator = np.atleast_1d(np.poly(poles))
    numerator = np.asarray(np.real_if_close(numerator, tol=1000), dtype=np.float64)
    denominator = np.asarray(np.real_if_close(denominator, tol=1000), dtype=np.float64)
    section = np.zeros(6, dtype=np.float64)
    section[3 - len(numerator) : 3] = numerator
    section[6 - len(denominator) : 6] = denominator
    return section


def _zpk_to_sos(zeros: np.ndarray, poles: np.ndarray, gain: float) -> np.ndarray:
    zeros = np.asarray(zeros)
    poles = np.asarray(poles)
    poles = np.concatenate((poles, np.zeros(max(len(zeros) - len(poles), 0))))
    zeros = np.concatenate((zeros, np.zeros(max(len(poles) - len(zeros), 0))))
    section_count = (max(len(poles), len(zeros)) + 1) // 2
    if len(poles) % 2 == 1:
        poles = np.concatenate((poles, [0.0]))
        zeros = np.concatenate((zeros, [0.0]))
    zeros = np.concatenate(_cplxreal(zeros))
    poles = np.concatenate(_cplxreal(poles))
    sections = np.zeros((section_count, 6), dtype=np.float64)

    def worst(values: np.ndarray) -> int:
        return int(np.argmin(np.abs(1.0 - np.abs(values))))

    for section_index in range(section_count - 1, -1, -1):
        pole1_index = worst(poles)
        pole1 = poles[pole1_index]
        poles = np.delete(poles, pole1_index)
        if np.isreal(pole1) and np.isreal(poles).sum() == 0:
            zero1_index = _nearest_root_index(zeros, pole1, "real")
            zero1 = zeros[zero1_index]
            zeros = np.delete(zeros, zero1_index)
            sections[section_index] = _single_sos([zero1, 0], [pole1, 0], 1.0)
        elif (
            len(poles) + 1 == len(zeros)
            and not np.isreal(pole1)
            and np.isreal(poles).sum() == 1
            and np.isreal(zeros).sum() == 1
        ):
            zero1_index = _nearest_root_index(zeros, pole1, "complex")
            zero1 = zeros[zero1_index]
            zeros = np.delete(zeros, zero1_index)
            sections[section_index] = _single_sos(
                [zero1, zero1.conj()], [pole1, pole1.conj()], 1.0
            )
        else:
            if np.isreal(pole1):
                real_indices = np.flatnonzero(np.isreal(poles))
                pole2_index = int(real_indices[worst(poles[real_indices])])
                pole2 = poles[pole2_index]
                poles = np.delete(poles, pole2_index)
            else:
                pole2 = pole1.conj()
            if len(zeros):
                zero1_index = _nearest_root_index(zeros, pole1, "any")
                zero1 = zeros[zero1_index]
                zeros = np.delete(zeros, zero1_index)
                if not np.isreal(zero1):
                    sections[section_index] = _single_sos(
                        [zero1, zero1.conj()], [pole1, pole2], 1.0
                    )
                elif len(zeros):
                    zero2_index = _nearest_root_index(zeros, pole1, "real")
                    zero2 = zeros[zero2_index]
                    zeros = np.delete(zeros, zero2_index)
                    sections[section_index] = _single_sos(
                        [zero1, zero2], [pole1, pole2], 1.0
                    )
                else:
                    sections[section_index] = _single_sos([zero1], [pole1, pole2], 1.0)
            else:
                sections[section_index] = _single_sos([], [pole1, pole2], 1.0)
    if len(poles) or len(zeros):
        raise RuntimeError("failed to consume Butterworth roots")
    sections[0, :3] *= float(gain)
    return sections


def butter_sos(
    order: int,
    cutoff: float | Sequence[float],
    kind: str,
    fs: float,
) -> np.ndarray:
    """Design a digital low/high/band-pass Butterworth SOS filter."""

    filter_order = int(order)
    if filter_order != order or filter_order < 1:
        raise ValueError("filter order must be a positive integer")
    rate = float(fs)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample rate must be positive")
    normalized_kind = str(kind).strip().lower()
    critical = np.atleast_1d(np.asarray(cutoff, dtype=np.float64))
    if np.any(~np.isfinite(critical)) or np.any(critical <= 0.0) or np.any(critical >= rate / 2.0):
        raise ValueError("cutoff frequencies must satisfy 0 < cutoff < Nyquist")
    if normalized_kind == "bandpass":
        if len(critical) != 2 or critical[0] >= critical[1]:
            raise ValueError("band-pass cutoffs must be increasing")
    elif len(critical) != 1 or normalized_kind not in {"lowpass", "highpass"}:
        raise ValueError("filter type must be lowpass, highpass, or bandpass")

    # SciPy normalizes Wn by Nyquist and then performs the analog transform at
    # an internal fs=2.  Keeping that numerically equivalent route (instead of
    # cancelling the scale algebraically) preserves its rounding at extreme
    # cutoff frequencies and therefore the stored compatibility oracle.
    warped = 4.0 * np.tan(np.pi * critical / rate)
    zeros, poles, gain = _butterworth_prototype(filter_order)
    if normalized_kind == "lowpass":
        zeros, poles, gain = _lowpass_zpk(zeros, poles, gain, float(warped[0]))
    elif normalized_kind == "highpass":
        zeros, poles, gain = _highpass_zpk(zeros, poles, gain, float(warped[0]))
    else:
        center = math.sqrt(float(warped[0] * warped[1]))
        zeros, poles, gain = _bandpass_zpk(
            zeros, poles, gain, center, float(warped[1] - warped[0])
        )
    zeros, poles, gain = _bilinear_zpk(zeros, poles, gain, 2.0)
    return _zpk_to_sos(zeros, poles, gain)


def _lfilter_zi(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    b = np.asarray(numerator, dtype=np.float64)
    a = np.asarray(denominator, dtype=np.float64)
    if a[0] != 1.0:
        b, a = b / a[0], a / a[0]
    size = max(len(a), len(b))
    b = np.pad(b, (0, size - len(b)))
    a = np.pad(a, (0, size - len(a)))
    steady = np.sum(b) / np.sum(a)
    return np.cumsum((b - steady * a)[::-1])[::-1][1:]


def _sos_zi(sections: np.ndarray) -> np.ndarray:
    states = np.empty((len(sections), 2), dtype=np.float64)
    scale = 1.0
    for index, section in enumerate(sections):
        states[index] = scale * _lfilter_zi(section[:3], section[3:])
        scale *= np.sum(section[:3]) / np.sum(section[3:])
    return states


def _sos_filter(
    sections: np.ndarray, values: np.ndarray, initial: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    output = np.asarray(values, dtype=np.float64).copy()
    state = np.asarray(initial, dtype=np.float64).copy()
    for section_index, section in enumerate(sections):
        b0, b1, b2, a0, a1, a2 = section
        if a0 != 1.0:
            b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
        z0, z1 = state[section_index]
        for sample_index, sample in enumerate(output):
            filtered = b0 * sample + z0
            z0 = b1 * sample - a1 * filtered + z1
            z1 = b2 * sample - a2 * filtered
            output[sample_index] = filtered
        state[section_index] = (z0, z1)
    return output, state


def sosfiltfilt(sections: np.ndarray, y: Sequence[float] | np.ndarray) -> np.ndarray:
    """Apply cascaded SOS sections forward/backward with SciPy-compatible padding."""

    sos = np.asarray(sections, dtype=np.float64)
    values = np.asarray(y, dtype=np.float64)
    if sos.ndim != 2 or sos.shape[1] != 6:
        raise ValueError("sos must be shape (n_sections, 6)")
    if values.ndim != 1:
        raise ValueError("filter input must be one-dimensional")
    taps = 2 * len(sos) + 1
    taps -= min(int(np.sum(sos[:, 2] == 0)), int(np.sum(sos[:, 5] == 0)))
    edge = 3 * taps
    if values.size <= edge:
        raise ValueError(
            "The length of the input vector x must be greater than padlen, "
            f"which is {edge}."
        )
    extension = np.concatenate(
        (
            2.0 * values[0] - values[edge:0:-1],
            values,
            2.0 * values[-1] - values[-2 : -(edge + 2) : -1],
        )
    )
    steady_state = _sos_zi(sos)
    forward, _ = _sos_filter(sos, extension, steady_state * extension[0])
    backward, _ = _sos_filter(sos, forward[::-1], steady_state * forward[-1])
    return backward[::-1][edge:-edge]


__all__ = [
    "butter_sos",
    "detrend",
    "periodic_hann",
    "sosfiltfilt",
    "welch_psd",
]
