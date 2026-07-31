from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_SIGNAL_MODULE = None


def _signal():
    global _SIGNAL_MODULE
    if _SIGNAL_MODULE is None:
        from scipy import signal as scipy_signal

        _SIGNAL_MODULE = scipy_signal
    return _SIGNAL_MODULE


@dataclass(slots=True)
class FilterConfig:
    lowpass_enabled: bool = False
    lowpass_hz: float = 100.0
    highpass_enabled: bool = False
    highpass_hz: float = 5.0
    detrend_enabled: bool = False
    order: int = 4


def apply_time_window(
    time_s: np.ndarray,
    values: np.ndarray,
    start_s: float | None = None,
    end_s: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(time_s, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(t.size, y.size)
    t = t[:count]
    y = y[:count]
    if count == 0:
        return t, y
    mask = np.isfinite(t) & np.isfinite(y)
    if start_s is not None and np.isfinite(start_s):
        mask &= t >= float(start_s)
    if end_s is not None and np.isfinite(end_s):
        mask &= t <= float(end_s)
    return t[mask], y[mask]


def apply_filter_to_signal(
    values: np.ndarray,
    sample_rate: float,
    config: FilterConfig | None = None,
) -> tuple[np.ndarray, int]:
    y = np.asarray(values, dtype=float).ravel()
    if config is None or y.size == 0:
        return y.copy(), 0
    if not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return y.copy(), 0

    order = max(1, int(round(config.order)))
    low = float(config.lowpass_hz)
    high = float(config.highpass_hz)
    use_low = bool(config.lowpass_enabled and np.isfinite(low) and low > 0.0)
    use_high = bool(config.highpass_enabled and np.isfinite(high) and high > 0.0)
    nyquist = float(sample_rate) / 2.0
    if use_low and low >= nyquist:
        use_low = False
    if use_high and high >= nyquist:
        use_high = False
    if use_low and use_high and high >= low:
        return y.copy(), 0
    if not use_low and not use_high and not config.detrend_enabled:
        return y.copy(), 0
    if y.size < max(12, 3 * (order + 1)):
        return y.copy(), 0

    y_work = y - float(np.nanmean(y))
    if config.detrend_enabled:
        y_work = _signal().detrend(y_work)
    if not use_low and not use_high:
        return np.asarray(y_work, dtype=float), 0

    try:
        if use_low and use_high:
            cutoff = [high / nyquist, low / nyquist]
            btype = "bandpass"
            cutoff_ref: float | list[float] = [high, low]
        elif use_high:
            cutoff = high / nyquist
            btype = "high"
            cutoff_ref = high
        else:
            cutoff = low / nyquist
            btype = "low"
            cutoff_ref = low
        b, a = _signal().butter(order, cutoff, btype=btype)
        filt_len = max(len(a), len(b))
        pad_len = min((y.size - 1) // 2, max(3 * filt_len, 24))
        if pad_len >= 2:
            left = 2.0 * y_work[0] - y_work[pad_len:0:-1]
            right = 2.0 * y_work[-1] - y_work[-2 : -pad_len - 2 : -1]
            y_pad = np.concatenate((left, y_work, right))
        else:
            y_pad = y_work
        filtered = _signal().filtfilt(b, a, y_pad)
        if pad_len >= 2:
            filtered = filtered[pad_len : pad_len + y.size]
    except Exception:
        return y.copy(), 0

    trim = compute_filter_trim_samples(y.size, sample_rate, cutoff_ref, order, pad_len)
    if filtered.size != y.size or not np.all(np.isfinite(filtered)):
        return y.copy(), 0
    return np.asarray(filtered, dtype=float), trim


def compute_filter_trim_samples(
    sample_count: int,
    sample_rate: float,
    cutoff_hz: float | np.ndarray | list[float],
    order: int,
    pad_len: int | None = None,
) -> int:
    if sample_count < 3 or sample_rate <= 0.0:
        return 0
    cutoff_values = np.asarray(cutoff_hz, dtype=float).ravel()
    cutoff_values = cutoff_values[np.isfinite(cutoff_values) & (cutoff_values > 0.0)]
    if cutoff_values.size == 0:
        return 0
    cutoff_min = float(np.min(cutoff_values))
    edge_seconds = max(float(order) / cutoff_min, 0.02)
    trim = int(np.ceil(edge_seconds * float(sample_rate)))
    limits = [(sample_count - 2) // 2, trim]
    if pad_len is not None:
        limits.append(int(np.ceil(max(0, int(pad_len)) / 2.0)))
    return max(0, min(limits))


def crop_signal_edges(
    time_s: np.ndarray,
    values: np.ndarray,
    trim_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(time_s, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(t.size, y.size)
    t = t[:count]
    y = y[:count]
    trim = int(max(0, trim_samples))
    if trim <= 0 or count < 3:
        return t, y
    trim = min(trim, (count - 2) // 2)
    if trim <= 0:
        return t, y
    return t[trim:-trim], y[trim:-trim]


def compute_periodogram_psd(
    values: np.ndarray,
    sample_rate: float,
) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(values, dtype=float).ravel()
    y = y[np.isfinite(y)]
    if y.size < 2 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    y = y - float(np.mean(y))
    freqs, psd = _signal().periodogram(y, fs=float(sample_rate), window="boxcar", detrend=False)
    valid = np.isfinite(freqs) & np.isfinite(psd) & (freqs > 0.0) & (psd > 0.0)
    return freqs[valid], psd[valid]


def compute_cross_spectrum_periodogram(
    reference: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
) -> tuple[np.ndarray, np.ndarray]:
    ref, resp = _finite_pair(reference, response)
    if ref.size < 2 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=complex)
    freqs, cross = _signal().csd(
        ref,
        resp,
        fs=float(sample_rate),
        window="boxcar",
        nperseg=ref.size,
        noverlap=0,
        nfft=ref.size,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    valid = np.isfinite(freqs) & np.isfinite(np.real(cross)) & np.isfinite(np.imag(cross)) & (freqs > 0.0)
    return freqs[valid], cross[valid]


def compute_hann_periodogram_psd(
    values: np.ndarray,
    sample_rate: float,
    *,
    skip_initial: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(values, dtype=float).ravel()
    y = y[np.isfinite(y)]
    if y.size < 4 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    freqs, psd = _signal().periodogram(
        y,
        fs=float(sample_rate),
        window=np.hanning(y.size),
        nfft=y.size,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    valid = np.isfinite(freqs) & np.isfinite(psd) & (freqs > 0.0) & (psd > 0.0)
    freqs = freqs[valid]
    psd = psd[valid]
    skip = max(0, int(skip_initial))
    if skip and freqs.size > skip:
        freqs = freqs[skip:]
        psd = psd[skip:]
    return freqs, psd


def compute_welch_psd(
    values: np.ndarray,
    sample_rate: float,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    y = _finite_signal(values)
    nperseg = _validated_block_size(y.size, sample_rate, block_size)
    if nperseg < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    freqs, psd = _signal().welch(
        y,
        fs=float(sample_rate),
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    valid = np.isfinite(freqs) & np.isfinite(psd) & (freqs > 0.0) & (psd > 0.0)
    return freqs[valid], psd[valid]


def compute_transfer_function_welch(
    reference: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    ref, resp = _finite_pair(reference, response)
    nperseg = _validated_block_size(ref.size, sample_rate, block_size)
    if nperseg < 2:
        return np.array([], dtype=float), np.array([], dtype=complex)
    sig = _signal()
    freqs, g_xx = sig.welch(
        ref,
        fs=float(sample_rate),
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    _freqs, g_yx = sig.csd(
        ref,
        resp,
        fs=float(sample_rate),
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    frf = np.divide(g_yx, g_xx, out=np.zeros_like(g_yx), where=np.abs(g_xx) > 0.0)
    valid = np.isfinite(freqs) & np.isfinite(frf) & (freqs > 0.0)
    return freqs[valid], frf[valid]


def compute_mimo_transfer_function_welch(
    inputs: np.ndarray,
    outputs: np.ndarray,
    sample_rate: float,
    block_size: int,
    *,
    regularization_floor: float = 1e-9,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate a MIMO transfer matrix with H(f) = S_yx(f) S_xx(f)^-1."""
    x = np.asarray(inputs, dtype=float)
    y = np.asarray(outputs, dtype=float)
    if x.ndim == 1:
        x = x.reshape(1, -1)
    if y.ndim == 1:
        y = y.reshape(1, -1)
    if x.ndim != 2 or y.ndim != 2 or x.shape[0] == 0 or y.shape[0] == 0:
        return np.array([], dtype=float), np.zeros((0, 0, 0), dtype=complex)
    count = min(x.shape[1], y.shape[1])
    if count < 2 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
    x = x[:, :count]
    y = y[:, :count]
    finite = np.all(np.isfinite(x), axis=0) & np.all(np.isfinite(y), axis=0)
    x = x[:, finite]
    y = y[:, finite]
    if x.shape[1] < 2:
        return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
    x = x - np.mean(x, axis=1, keepdims=True)
    y = y - np.mean(y, axis=1, keepdims=True)
    nperseg = _validated_block_size(x.shape[1], sample_rate, block_size)
    if nperseg < 2:
        return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)

    sig = _signal()
    csd_kwargs = {
        "fs": float(sample_rate),
        "window": "hann",
        "nperseg": nperseg,
        "noverlap": nperseg // 2,
        "detrend": "constant",
        "scaling": "density",
    }
    freqs: np.ndarray | None = None
    sxx: np.ndarray | None = None
    syx: np.ndarray | None = None
    for input_i in range(x.shape[0]):
        for input_j in range(x.shape[0]):
            current_freqs, cross = sig.csd(x[input_i], x[input_j], **csd_kwargs)
            if freqs is None:
                freqs = np.asarray(current_freqs, dtype=float)
                sxx = np.zeros((freqs.size, x.shape[0], x.shape[0]), dtype=complex)
                syx = np.zeros((freqs.size, y.shape[0], x.shape[0]), dtype=complex)
            if sxx is None or current_freqs.size != freqs.size:
                return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
            sxx[:, input_i, input_j] = cross
    if freqs is None or sxx is None or syx is None:
        return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
    for output_i in range(y.shape[0]):
        for input_i in range(x.shape[0]):
            current_freqs, cross = sig.csd(x[input_i], y[output_i], **csd_kwargs)
            if current_freqs.size != freqs.size:
                return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
            syx[:, output_i, input_i] = cross

    valid = (
        np.isfinite(freqs)
        & (freqs > 0.0)
        & np.all(np.isfinite(np.real(sxx)) & np.isfinite(np.imag(sxx)), axis=(1, 2))
        & np.all(np.isfinite(np.real(syx)) & np.isfinite(np.imag(syx)), axis=(1, 2))
    )
    if not np.any(valid):
        return np.array([], dtype=float), np.zeros((0, y.shape[0], x.shape[0]), dtype=complex)
    freqs = freqs[valid]
    sxx = sxx[valid]
    syx = syx[valid]
    transfer = np.empty((freqs.size, y.shape[0], x.shape[0]), dtype=complex)
    floor = max(float(regularization_floor), 0.0)
    for index in range(freqs.size):
        current_sxx = sxx[index]
        scale = float(np.real(np.trace(current_sxx))) / max(current_sxx.shape[0], 1)
        if floor > 0.0 and np.isfinite(scale) and scale > 0.0:
            current_sxx = current_sxx + floor * scale * np.eye(current_sxx.shape[0], dtype=complex)
        for output_i in range(y.shape[0]):
            try:
                transfer[index, output_i, :] = np.linalg.solve(current_sxx, syx[index, output_i, :])
            except np.linalg.LinAlgError:
                transfer[index, output_i, :] = np.linalg.pinv(current_sxx) @ syx[index, output_i, :]
    finite_transfer = np.all(np.isfinite(np.real(transfer)) & np.isfinite(np.imag(transfer)), axis=(1, 2))
    return freqs[finite_transfer], transfer[finite_transfer]


def compute_matlab_tfestimate(
    reference: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
) -> tuple[np.ndarray, np.ndarray]:
    ref, resp = _finite_pair(reference, response)
    n = ref.size
    if n < 8 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=complex)
    nfft = n // 3
    if nfft < 8:
        nfft = n
    nfft = min(nfft, n)
    window = np.hanning(nfft)
    sig = _signal()
    freqs, g_xx = sig.welch(
        ref,
        fs=float(sample_rate),
        window=window,
        nperseg=nfft,
        noverlap=0,
        nfft=nfft,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    _freqs, g_yx = sig.csd(
        ref,
        resp,
        fs=float(sample_rate),
        window=window,
        nperseg=nfft,
        noverlap=0,
        nfft=nfft,
        detrend=False,
        scaling="density",
        return_onesided=True,
    )
    frf = np.divide(g_yx, g_xx, out=np.zeros_like(g_yx), where=np.abs(g_xx) > 0.0)
    valid = np.isfinite(freqs) & np.isfinite(frf) & (freqs > 0.0)
    return freqs[valid], frf[valid]


def compute_coherence_welch(
    reference: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    ref, resp = _finite_pair(reference, response)
    nperseg = _validated_block_size(ref.size, sample_rate, block_size)
    if nperseg < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    freqs, coherence = _signal().coherence(
        ref,
        resp,
        fs=float(sample_rate),
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
    )
    valid = np.isfinite(freqs) & np.isfinite(coherence) & (freqs > 0.0)
    return freqs[valid], coherence[valid]


def compute_cross_spectrum_welch(
    reference: np.ndarray,
    response: np.ndarray,
    sample_rate: float,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    ref, resp = _finite_pair(reference, response)
    nperseg = _validated_block_size(ref.size, sample_rate, block_size)
    if nperseg < 2:
        return np.array([], dtype=float), np.array([], dtype=complex)
    freqs, cross = _signal().csd(
        ref,
        resp,
        fs=float(sample_rate),
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )
    valid = np.isfinite(freqs) & np.isfinite(np.real(cross)) & np.isfinite(np.imag(cross)) & (freqs > 0.0)
    return freqs[valid], cross[valid]


def _finite_signal(values: np.ndarray) -> np.ndarray:
    y = np.asarray(values, dtype=float).ravel()
    y = y[np.isfinite(y)]
    if y.size:
        y = y - float(np.mean(y))
    return y


def _finite_pair(reference: np.ndarray, response: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.asarray(reference, dtype=float).ravel()
    resp = np.asarray(response, dtype=float).ravel()
    count = min(ref.size, resp.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    ref = ref[:count]
    resp = resp[:count]
    finite = np.isfinite(ref) & np.isfinite(resp)
    ref = ref[finite]
    resp = resp[finite]
    if ref.size:
        ref = ref - float(np.mean(ref))
        resp = resp - float(np.mean(resp))
    return ref, resp


def _validated_block_size(sample_count: int, sample_rate: float, block_size: int) -> int:
    if sample_count < 2 or not np.isfinite(sample_rate) or sample_rate <= 0.0:
        return 0
    try:
        requested = int(block_size)
    except (TypeError, ValueError):
        requested = sample_count
    requested = max(2, requested)
    return min(int(sample_count), requested)


def convert_acceleration_time_series(
    acceleration: np.ndarray,
    sample_rate: float,
    quantity_mode: str,
    *,
    highpass_enabled: bool = False,
    highpass_hz: float = 5.0,
) -> np.ndarray:
    values = np.asarray(acceleration, dtype=float).ravel()
    mode = normalize_quantity_mode(quantity_mode)
    if mode in {"acceleration", "force"}:
        return values.copy()
    if values.size < 2 or sample_rate <= 0.0:
        return np.array([], dtype=float)

    work = values - float(np.mean(values))
    freq_signed = np.fft.fftfreq(work.size, d=1.0 / float(sample_rate))
    omega = 2.0 * np.pi * freq_signed
    spectrum = np.fft.fft(work)
    if highpass_enabled and np.isfinite(highpass_hz) and highpass_hz > 0.0:
        spectrum[np.abs(freq_signed) < float(highpass_hz)] = 0.0
    scale = np.zeros_like(omega, dtype=complex)
    nonzero = np.abs(omega) > 0.0
    if mode == "velocity":
        scale[nonzero] = 1.0 / (1.0j * omega[nonzero])
        return np.real(np.fft.ifft(spectrum * scale)) * 1e6
    if mode == "displacement":
        scale[nonzero] = -1.0 / (omega[nonzero] ** 2)
        return np.real(np.fft.ifft(spectrum * scale)) * 1e6
    return values.copy()


def convert_acceleration_psd(
    frequencies: np.ndarray,
    psd_acceleration: np.ndarray,
    quantity_mode: str,
    *,
    highpass_enabled: bool = False,
    highpass_hz: float = 5.0,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    psd = np.asarray(psd_acceleration, dtype=float).ravel()
    count = min(f.size, psd.size)
    f = f[:count]
    psd = psd[:count]
    valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
    f = f[valid]
    psd = psd[valid]
    if f.size == 0:
        return f, psd

    mode = normalize_quantity_mode(quantity_mode)
    omega = 2.0 * np.pi * f
    if mode == "velocity":
        psd = psd / (omega**2) * 1e12
    elif mode == "displacement":
        psd = psd / (omega**4) * 1e12

    if mode not in {"acceleration", "force"} and highpass_enabled and np.isfinite(highpass_hz) and highpass_hz > 0.0:
        keep = f >= float(highpass_hz)
        f = f[keep]
        psd = psd[keep]
    valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
    return f[valid], psd[valid]


def compute_cumulative_spectrum(
    frequencies: np.ndarray,
    psd: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    p = np.asarray(psd, dtype=float).ravel()
    count = min(f.size, p.size)
    f = f[:count]
    p = p[:count]
    valid = np.isfinite(f) & np.isfinite(p) & (f > 0.0) & (p >= 0.0)
    f = f[valid]
    p = p[valid]
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    order = np.argsort(f)
    f = f[order]
    p = p[order]
    df = np.diff(f)
    area = 0.5 * (p[:-1] + p[1:]) * np.maximum(df, 0.0)
    cumulative = np.concatenate(([0.0], np.cumsum(area)))
    return f, 3.0 * np.sqrt(np.maximum(cumulative, 0.0))


def third_octave_bands(min_frequency: float, max_frequency: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not np.isfinite(min_frequency) or not np.isfinite(max_frequency):
        return np.array([]), np.array([]), np.array([])
    if min_frequency <= 0.0 or max_frequency <= min_frequency:
        return np.array([]), np.array([]), np.array([])
    n = 3
    upper_steps = max(1, _matlab_round(n * np.ceil(np.log(max_frequency / 1000.0) / np.log(2.0)) + 1))
    upper = [1000.0]
    for _index in range(upper_steps):
        upper.append(upper[-1] * 10.0 ** (3.0 / (10.0 * n)))
    upper = [value for value in upper if value < max_frequency * (2.0 ** (0.5 / n))]

    lower_steps = max(1, _matlab_round(n * np.ceil(np.log(1000.0 / min_frequency) / np.log(2.0)) + 1))
    lower = [1000.0]
    for _index in range(lower_steps):
        lower.append(lower[-1] / (10.0 ** (3.0 / (10.0 * n))))
    lower = [value for value in lower if value > min_frequency * (2.0 ** (-0.5 / n))]

    centers = np.unique(np.asarray(lower + upper, dtype=float))
    centers = centers[centers < max_frequency * (2.0 ** (0.5 / n))]
    centers = centers[centers > min_frequency * (2.0 ** (-0.5 / n))]
    raw_centers = centers.copy()
    lower_edges = _significant_digit_round(raw_centers / (2.0 ** (1.0 / (2.0 * n))), 3, 5)
    upper_edges = _significant_digit_round(raw_centers * (2.0 ** (1.0 / (2.0 * n))), 3, 5)
    centers = _ansi_preferred_adjust(raw_centers)
    valid = (
        np.isfinite(centers)
        & np.isfinite(lower_edges)
        & np.isfinite(upper_edges)
        & (centers > 0.0)
        & (lower_edges > 0.0)
        & (upper_edges > lower_edges)
    )
    centers = centers[valid]
    lower_edges = lower_edges[valid]
    upper_edges = upper_edges[valid]
    if centers.size and max_frequency < upper_edges[-1]:
        centers = centers[:-1]
        lower_edges = lower_edges[:-1]
        upper_edges = upper_edges[:-1]
    return centers, lower_edges, upper_edges


def compute_third_octave_velocity_rms(
    frequencies: np.ndarray,
    acceleration_psd: np.ndarray,
    rbw_hz: float,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    psd = np.asarray(acceleration_psd, dtype=float).ravel()
    count = min(f.size, psd.size)
    f = f[:count]
    psd = psd[:count]
    valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
    f = f[valid]
    psd = psd[valid]
    if f.size < 2 or rbw_hz <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    centers, lower_edges, upper_edges = third_octave_bands(float(np.min(f)), float(np.max(f)))
    if centers.size == 0:
        return centers, np.array([], dtype=float)
    velocity_psd = psd / ((2.0 * np.pi * f) ** 2)
    values = np.full_like(centers, np.nan, dtype=float)
    for index, (low, high) in enumerate(zip(lower_edges, upper_edges)):
        mask = (f >= low) & (f <= high)
        if np.any(mask):
            values[index] = np.sqrt(np.sum(velocity_psd[mask] * float(rbw_hz))) * 1e6
    valid = np.isfinite(values) & (values > 0.0)
    return centers[valid], values[valid]


def compute_dynamic_stiffness(
    frequencies: np.ndarray,
    frf: np.ndarray,
    response_eu: float,
    reference_eu: float,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    xfer = np.asarray(frf).ravel()
    count = min(f.size, xfer.size)
    f = f[:count]
    xfer = xfer[:count]
    if reference_eu == 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    omega = 2.0 * np.pi * f
    eu_ratio = float(response_eu) / float(reference_eu)
    denominator = xfer / np.maximum(omega, np.finfo(float).eps) ** 2 * eu_ratio
    stiffness = np.abs(np.divide(1.0, denominator, out=np.full_like(denominator, np.nan, dtype=complex), where=np.abs(denominator) > 0.0))
    valid = np.isfinite(f) & np.isfinite(stiffness) & (f > 0.0) & (stiffness > 0.0)
    return f[valid], stiffness[valid]


def normalize_quantity_mode(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("force") or text in {"n", "newton", "newtons", "力"}:
        return "force"
    if text.startswith("vel"):
        return "velocity"
    if text.startswith("disp"):
        return "displacement"
    return "acceleration"


def quantity_time_label(quantity_mode: str) -> str:
    mode = normalize_quantity_mode(quantity_mode)
    if mode == "force":
        return "Force (N)"
    if mode == "velocity":
        return "Velocity (um/s)"
    if mode == "displacement":
        return "Displacement (um)"
    return "Acceleration (m/s^2)"


def quantity_psd_label(quantity_mode: str) -> str:
    mode = normalize_quantity_mode(quantity_mode)
    if mode == "force":
        return "N^2/Hz"
    if mode == "velocity":
        return "(um/s)^2/Hz"
    if mode == "displacement":
        return "um^2/Hz"
    return "(m/s^2)^2/Hz"


def quantity_cumulative_label(quantity_mode: str) -> str:
    mode = normalize_quantity_mode(quantity_mode)
    if mode == "force":
        return "3 sigma force (N)"
    if mode == "velocity":
        return "3 sigma velocity (um/s)"
    if mode == "displacement":
        return "3 sigma displacement (um)"
    return "3 sigma acceleration (m/s^2)"


def _ansi_preferred_adjust(values: np.ndarray) -> np.ndarray:
    rounded_5 = _significant_digit_round(values, 3, 5)
    rounded_100 = _significant_digit_round(values, 3, 100)
    output = rounded_5.copy()
    mask = (
        np.isfinite(rounded_5)
        & np.isfinite(rounded_100)
        & (rounded_100 != 0.0)
        & (np.abs(100.0 * (1.0 - rounded_5 / rounded_100)) < 1.0)
    )
    output[mask] = rounded_100[mask]
    return output


def _significant_digit_round(values: np.ndarray, digits: int, multiple: int) -> np.ndarray:
    output = np.asarray(values, dtype=float).copy()
    digits = max(1, int(_matlab_round(digits)))
    multiple = max(1, int(_matlab_round(multiple)))
    for index, value in enumerate(output):
        if not np.isfinite(value) or value == 0.0:
            continue
        decade = np.ceil(np.log10(abs(value)))
        if abs(value) - 10.0**decade == 0.0:
            decade += 1.0
        scale = 10.0 ** (digits - decade)
        buffer = scale / multiple
        output[index] = _matlab_round(buffer * value) / buffer
    return output


def _matlab_round(value: float) -> int:
    number = float(value)
    if number >= 0.0:
        return int(np.floor(number + 0.5))
    return int(np.ceil(number - 0.5))
