from __future__ import annotations

import numpy as np


DERIVE_BASE_TO_TOP = "base_to_top"
DERIVE_TOP_TO_BASE = "top_to_base"


def diagonal_psd_matrix(psd_values: np.ndarray) -> np.ndarray:
    """Build a cross-spectral matrix with independent channel PSDs on the diagonal."""
    psd = np.asarray(psd_values, dtype=float)
    if psd.ndim == 1:
        psd = psd.reshape(-1, 1)
    if psd.ndim != 2:
        return np.zeros((0, 0, 0), dtype=complex)
    psd = np.maximum(np.nan_to_num(psd, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
    matrices = np.zeros((psd.shape[0], psd.shape[1], psd.shape[1]), dtype=complex)
    axis = np.arange(psd.shape[1])
    matrices[:, axis, axis] = psd
    return matrices


def fully_correlated_psd_matrix(
    psd_values: np.ndarray,
    phase_radians: np.ndarray | None = None,
) -> np.ndarray:
    """Build a rank-1 cross-spectral matrix for phase-locked channels."""
    psd = np.asarray(psd_values, dtype=float)
    if psd.ndim == 1:
        psd = psd.reshape(-1, 1)
    if psd.ndim != 2:
        return np.zeros((0, 0, 0), dtype=complex)
    psd = np.maximum(np.nan_to_num(psd, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
    if phase_radians is None:
        phase = np.zeros(psd.shape, dtype=float)
    else:
        phase = np.asarray(phase_radians, dtype=float)
        if phase.ndim == 1:
            phase = np.broadcast_to(phase.reshape(1, -1), psd.shape)
        elif phase.shape != psd.shape:
            return np.zeros((0, 0, 0), dtype=complex)
    amplitudes = np.sqrt(psd) * np.exp(1.0j * phase)
    matrices = amplitudes[:, :, np.newaxis] * np.conj(amplitudes[:, np.newaxis, :])
    return _hermitian_psd_matrix(matrices)


def psd_matrix_diagonal(psd_matrix: np.ndarray) -> np.ndarray:
    """Return the real PSD diagonal from a frequency-indexed spectral matrix."""
    matrix = np.asarray(psd_matrix)
    if matrix.ndim != 3:
        return np.zeros((0, 0), dtype=float)
    diagonal = np.real(np.diagonal(matrix, axis1=1, axis2=2))
    return np.maximum(np.nan_to_num(diagonal, nan=0.0, posinf=0.0, neginf=0.0), 0.0)


def predict_mimo_response_psd(
    frequency: np.ndarray,
    transfer_matrix: np.ndarray,
    input_psd_matrix: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Predict output cross PSD with Syy = H Suu H^H."""
    f = np.asarray(frequency, dtype=float).ravel()
    h = np.asarray(transfer_matrix, dtype=complex)
    suu = np.asarray(input_psd_matrix, dtype=complex)
    if h.ndim != 3 or suu.ndim != 3 or h.shape[2] != suu.shape[1] or suu.shape[1] != suu.shape[2]:
        return np.array([], dtype=float), np.zeros((0, 0, 0), dtype=complex)
    count = min(f.size, h.shape[0], suu.shape[0])
    if count <= 0:
        return np.array([], dtype=float), np.zeros((0, h.shape[1], h.shape[1]), dtype=complex)
    f = f[:count]
    h = h[:count]
    suu = _hermitian_psd_matrix(suu[:count])
    valid = (
        np.isfinite(f)
        & (f > 0.0)
        & np.all(np.isfinite(np.real(h)) & np.isfinite(np.imag(h)), axis=(1, 2))
        & np.all(np.isfinite(np.real(suu)) & np.isfinite(np.imag(suu)), axis=(1, 2))
    )
    if not np.any(valid):
        return np.array([], dtype=float), np.zeros((0, h.shape[1], h.shape[1]), dtype=complex)
    f = f[valid]
    h = h[valid]
    suu = suu[valid]
    order = np.argsort(f)
    f = f[order]
    h = h[order]
    suu = suu[order]
    syy = np.empty((f.size, h.shape[1], h.shape[1]), dtype=complex)
    for index in range(f.size):
        syy[index] = h[index] @ suu[index] @ np.conj(h[index].T)
    return f, _hermitian_psd_matrix(syy)


def invert_mimo_input_psd(
    frequency: np.ndarray,
    transfer_matrix: np.ndarray,
    target_response_psd_matrix: np.ndarray,
    *,
    regularization_floor: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate a full input cross PSD matrix from target output cross PSD."""
    f = np.asarray(frequency, dtype=float).ravel()
    h = np.asarray(transfer_matrix, dtype=complex)
    syy = np.asarray(target_response_psd_matrix, dtype=complex)
    if h.ndim != 3 or syy.ndim != 3 or h.shape[1] != syy.shape[1] or syy.shape[1] != syy.shape[2]:
        return np.array([], dtype=float), np.zeros((0, 0, 0), dtype=complex)
    count = min(f.size, h.shape[0], syy.shape[0])
    if count <= 0:
        return np.array([], dtype=float), np.zeros((0, h.shape[2], h.shape[2]), dtype=complex)
    f = f[:count]
    h = h[:count]
    syy = _hermitian_psd_matrix(syy[:count])
    valid = (
        np.isfinite(f)
        & (f > 0.0)
        & np.all(np.isfinite(np.real(h)) & np.isfinite(np.imag(h)), axis=(1, 2))
        & np.all(np.isfinite(np.real(syy)) & np.isfinite(np.imag(syy)), axis=(1, 2))
    )
    if not np.any(valid):
        return np.array([], dtype=float), np.zeros((0, h.shape[2], h.shape[2]), dtype=complex)
    f = f[valid]
    h = h[valid]
    syy = syy[valid]
    order = np.argsort(f)
    f = f[order]
    h = h[order]
    syy = syy[order]
    floor_sq = max(float(regularization_floor), 0.0) ** 2
    suu = np.empty((f.size, h.shape[2], h.shape[2]), dtype=complex)
    for index in range(f.size):
        current_h = h[index]
        gram = np.conj(current_h.T) @ current_h
        if floor_sq > 0.0:
            gram = gram + floor_sq * np.eye(gram.shape[0], dtype=complex)
        try:
            h_pinv = np.linalg.solve(gram, np.conj(current_h.T))
        except np.linalg.LinAlgError:
            h_pinv = np.linalg.pinv(current_h, rcond=max(float(regularization_floor), 1e-15))
        suu[index] = h_pinv @ syy[index] @ np.conj(h_pinv.T)
    return f, _hermitian_psd_matrix(suu)


def invert_mimo_independent_input_psd(
    frequency: np.ndarray,
    transfer_matrix: np.ndarray,
    target_response_psd: np.ndarray,
    *,
    regularization_floor: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate independent input PSDs from target output PSDs using |H|^2 p = y."""
    f = np.asarray(frequency, dtype=float).ravel()
    h = np.asarray(transfer_matrix, dtype=complex)
    target = np.asarray(target_response_psd)
    if h.ndim != 3:
        return np.array([], dtype=float), np.zeros((0, 0), dtype=float)
    if target.ndim == 3:
        target = psd_matrix_diagonal(target)
    else:
        target = np.asarray(target, dtype=float)
        if target.ndim == 1:
            target = target.reshape(-1, 1)
    if target.ndim != 2 or h.shape[1] != target.shape[1]:
        return np.array([], dtype=float), np.zeros((0, h.shape[2]), dtype=float)
    count = min(f.size, h.shape[0], target.shape[0])
    if count <= 0:
        return np.array([], dtype=float), np.zeros((0, h.shape[2]), dtype=float)
    f = f[:count]
    h = h[:count]
    target = np.asarray(target[:count], dtype=float)
    valid = (
        np.isfinite(f)
        & (f > 0.0)
        & np.all(np.isfinite(np.real(h)) & np.isfinite(np.imag(h)), axis=(1, 2))
        & np.all(np.isfinite(target), axis=1)
    )
    if not np.any(valid):
        return np.array([], dtype=float), np.zeros((0, h.shape[2]), dtype=float)
    f = f[valid]
    h = h[valid]
    target = np.maximum(target[valid], 0.0)
    order = np.argsort(f)
    f = f[order]
    h = h[order]
    target = target[order]
    floor_sq = max(float(regularization_floor), 0.0) ** 2
    input_psd = np.empty((f.size, h.shape[2]), dtype=float)
    for index in range(f.size):
        a_matrix = np.abs(h[index]) ** 2
        normal = a_matrix.T @ a_matrix
        if floor_sq > 0.0:
            normal = normal + floor_sq * np.eye(normal.shape[0], dtype=float)
        rhs = a_matrix.T @ target[index]
        try:
            solution = np.linalg.solve(normal, rhs)
        except np.linalg.LinAlgError:
            solution = np.linalg.lstsq(a_matrix, target[index], rcond=None)[0]
        input_psd[index] = np.maximum(np.nan_to_num(solution, nan=0.0, posinf=0.0, neginf=0.0), 0.0)
    return f, input_psd


def solve_mimo_independent_psd(
    frequency: np.ndarray,
    transfer_matrix: np.ndarray,
    target_response_psd: np.ndarray,
    *,
    regularization_floor: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Solve independent input PSDs and predict the corresponding output PSDs."""
    f, input_psd = invert_mimo_independent_input_psd(
        frequency,
        transfer_matrix,
        target_response_psd,
        regularization_floor=regularization_floor,
    )
    if f.size < 2 or input_psd.size == 0:
        return f, input_psd, np.zeros_like(input_psd)
    _pred_f, predicted_output = predict_mimo_response_psd(
        f,
        transfer_matrix[: f.size],
        diagonal_psd_matrix(input_psd),
    )
    predicted = psd_matrix_diagonal(predicted_output) if predicted_output.size else np.zeros_like(input_psd)
    count = min(f.size, predicted.shape[0], input_psd.shape[0])
    return f[:count], input_psd[:count], predicted[:count]


def derive_psd_from_transfer(
    psd_frequency: np.ndarray,
    psd_values: np.ndarray,
    transfer_frequency: np.ndarray,
    transfer_values: np.ndarray,
    *,
    direction: str = DERIVE_BASE_TO_TOP,
    regularization_floor: float = 0.0,
    coherence_frequency: np.ndarray | None = None,
    coherence_values: np.ndarray | None = None,
    coherence_correction: bool = False,
    coherence_floor: float = 1e-6,
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
    coherence = None
    if coherence_correction and coherence_frequency is not None and coherence_values is not None:
        coherence = interpolate_coherence(coherence_frequency, coherence_values, f_out)
        coherence = np.maximum(coherence, max(float(coherence_floor), 1e-20))
    if _normal_direction(direction) == DERIVE_TOP_TO_BASE:
        floor_sq = max(float(regularization_floor), 0.0) ** 2
        psd_out = np.divide(
            psd_out,
            np.maximum(magnitude_sq, floor_sq),
            out=np.zeros_like(psd_out, dtype=float),
            where=np.isfinite(magnitude_sq),
        )
        if coherence is not None:
            psd_out = psd_out * coherence
    else:
        psd_out = psd_out * magnitude_sq
        if coherence is not None:
            psd_out = np.divide(
                psd_out,
                coherence,
                out=np.zeros_like(psd_out, dtype=float),
                where=np.isfinite(coherence) & (coherence > 0.0),
            )
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
    regularization_floor: float = 0.0,
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


def synthesize_time_from_psd(
    psd_frequency: np.ndarray,
    psd_values: np.ndarray,
    sample_rate: float,
    *,
    max_samples: int = 30000,
    seed: int | None = None,
    duration_s: float | None = None,
    sample_count: int | None = None,
    band_edges: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Create one stationary random time history matching a one-sided PSD."""
    f, psd = _sorted_finite_real_pair(psd_frequency, psd_values, positive_y=True)
    try:
        source_sample_rate = float(sample_rate)
    except (TypeError, ValueError):
        source_sample_rate = 0.0
    if not np.isfinite(source_sample_rate):
        source_sample_rate = 0.0
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float), source_sample_rate

    diffs = np.diff(f)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float), source_sample_rate

    source_df = float(np.median(diffs))
    if not np.isfinite(source_df) or source_df <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float), source_sample_rate

    inferred_sample_rate = 2.0 * (float(f[-1]) + source_df)
    fs = max(source_sample_rate, inferred_sample_rate)
    requested_duration: float | None = None
    if duration_s is not None:
        try:
            requested_duration = float(duration_s)
        except (TypeError, ValueError):
            requested_duration = None
        if requested_duration is not None and (not np.isfinite(requested_duration) or requested_duration <= 0.0):
            requested_duration = None

    explicit_sample_count = sample_count is not None
    if sample_count is not None:
        try:
            sample_count = int(sample_count)
        except (TypeError, ValueError):
            sample_count = 0
        if sample_count < 2:
            return np.array([], dtype=float), np.array([], dtype=float), fs
        if requested_duration is not None:
            fs = max((sample_count - 1) / requested_duration, 1e-20)
    elif requested_duration is not None:
        sample_count = max(2, int(np.floor(fs * requested_duration)) + 1)
    else:
        sample_count = int(round(fs / source_df))
        sample_count = max(32, sample_count)
        if sample_count % 2:
            sample_count += 1
        if max_samples > 0 and sample_count > int(max_samples):
            sample_count = max(32, int(max_samples))
            if sample_count % 2:
                sample_count -= 1
    if not explicit_sample_count and max_samples > 0 and sample_count > int(max_samples):
        sample_count = max(2, int(max_samples))
    if sample_count < 2:
        return np.array([], dtype=float), np.array([], dtype=float), fs

    freqs = np.fft.rfftfreq(sample_count, d=1.0 / fs)
    if freqs.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float), fs

    band_target_variance: float | None = None
    if band_edges is not None:
        try:
            lower_edges = np.asarray(band_edges[0], dtype=float).ravel()[: f.size]
            upper_edges = np.asarray(band_edges[1], dtype=float).ravel()[: f.size]
        except (TypeError, ValueError, IndexError):
            lower_edges = np.array([], dtype=float)
            upper_edges = np.array([], dtype=float)
        if lower_edges.size == f.size and upper_edges.size == f.size:
            psd_uniform = np.zeros(freqs.shape, dtype=float)
            band_target_variance = 0.0
            cell_lower, cell_upper = _frequency_cells_within_bands(f, lower_edges, upper_edges)
            for center, value, low, high in zip(f, psd, cell_lower, cell_upper):
                if not (
                    np.isfinite(center)
                    and np.isfinite(value)
                    and np.isfinite(low)
                    and np.isfinite(high)
                    and center > 0.0
                    and value > 0.0
                    and high > low
                ):
                    continue
                mask = (freqs >= float(low)) & (freqs <= float(high))
                psd_uniform[mask] = float(value)
        else:
            psd_uniform = np.interp(freqs, f, psd, left=0.0, right=0.0)
    else:
        psd_uniform = np.interp(freqs, f, psd, left=0.0, right=0.0)
    psd_uniform = np.maximum(psd_uniform, 0.0)
    psd_uniform[0] = 0.0
    df = float(fs) / float(sample_count)
    if band_target_variance is not None:
        band_target_variance = float(np.sum(psd_uniform) * df)
    rng = np.random.default_rng(seed)
    spectrum = np.zeros(freqs.shape, dtype=complex)
    if freqs.size > 2:
        phases = rng.uniform(0.0, 2.0 * np.pi, freqs.size - 2)
        amplitudes = sample_count * np.sqrt(psd_uniform[1:-1] * df / 2.0)
        spectrum[1:-1] = amplitudes * np.exp(1.0j * phases)
    if sample_count % 2 == 0 and freqs.size >= 2:
        nyquist_amplitude = sample_count * np.sqrt(psd_uniform[-1] * df)
        spectrum[-1] = nyquist_amplitude * (1.0 if rng.random() >= 0.5 else -1.0)

    values = np.fft.irfft(spectrum, n=sample_count)
    values = np.asarray(values, dtype=float)
    values = values - float(np.mean(values))
    target_variance = float(band_target_variance) if band_target_variance is not None else float(np.trapezoid(psd, f))
    current_rms = float(np.sqrt(np.mean(values**2))) if values.size else 0.0
    if np.isfinite(target_variance) and target_variance > 0.0 and current_rms > 0.0:
        values = values * (np.sqrt(target_variance) / current_rms)
    t = np.arange(sample_count, dtype=float) / fs
    valid = np.isfinite(t) & np.isfinite(values)
    return t[valid], values[valid], fs


def synthesize_time_from_psd_matrix(
    frequency: np.ndarray,
    psd_matrix: np.ndarray,
    sample_rate: float,
    *,
    seed: int | None = None,
    duration_s: float | None = None,
    sample_count: int | None = None,
    max_samples: int = 30000,
) -> tuple[np.ndarray, np.ndarray, float]:
    f = np.asarray(frequency, dtype=float).ravel()
    suu = _hermitian_psd_matrix(psd_matrix)
    if suu.ndim != 3 or suu.shape[0] < 2 or suu.shape[1] != suu.shape[2]:
        return np.array([], dtype=float), np.zeros((0, 0), dtype=float), float(sample_rate) if np.isfinite(sample_rate) else 0.0

    diag = psd_matrix_diagonal(suu)
    t_ref, _values_ref, fs = synthesize_time_from_psd(
        f,
        diag[:, 0],
        sample_rate,
        seed=seed,
        duration_s=duration_s,
        sample_count=sample_count,
        max_samples=max_samples,
    )
    if t_ref.size < 2:
        return np.array([], dtype=float), np.zeros((0, suu.shape[1]), dtype=float), fs

    n = int(t_ref.size)
    freq_uniform = np.fft.rfftfreq(n, d=1.0 / fs)
    if freq_uniform.size < 2:
        return np.array([], dtype=float), np.zeros((0, suu.shape[1]), dtype=float), fs

    interp_matrix = np.zeros((freq_uniform.size, suu.shape[1], suu.shape[2]), dtype=complex)
    for row in range(suu.shape[1]):
        for column in range(suu.shape[2]):
            real = np.interp(freq_uniform, f, np.real(suu[:, row, column]), left=0.0, right=0.0)
            imag = np.interp(freq_uniform, f, np.imag(suu[:, row, column]), left=0.0, right=0.0)
            interp_matrix[:, row, column] = real + 1.0j * imag
    interp_matrix = _hermitian_psd_matrix(interp_matrix)

    rng = np.random.default_rng(seed)
    channel_count = interp_matrix.shape[1]
    spectrum = np.zeros((freq_uniform.size, channel_count), dtype=complex)
    df = float(fs) / float(n)
    for index in range(1, freq_uniform.size - (1 if n % 2 == 0 else 0)):
        current = interp_matrix[index]
        try:
            chol = np.linalg.cholesky(current)
        except np.linalg.LinAlgError:
            eigenvalues, eigenvectors = np.linalg.eigh(current)
            chol = eigenvectors @ np.diag(np.sqrt(np.maximum(eigenvalues, 0.0)))
        noise = (rng.standard_normal(channel_count) + 1.0j * rng.standard_normal(channel_count)) / np.sqrt(2.0)
        spectrum[index] = np.sqrt(max(df, 0.0) * n**2 / 2.0) * (chol @ noise)
    if n % 2 == 0 and freq_uniform.size >= 2:
        current = np.real(interp_matrix[-1])
        eigenvalues, eigenvectors = np.linalg.eigh(current)
        noise = rng.standard_normal(channel_count)
        spectrum[-1] = np.sqrt(max(df, 0.0) * n**2) * (eigenvectors @ (np.sqrt(np.maximum(eigenvalues, 0.0)) * noise))

    values = np.fft.irfft(spectrum, n=n, axis=0)
    values = np.asarray(values, dtype=float)
    values = values - np.mean(values, axis=0, keepdims=True)
    return t_ref, values, fs


def _frequency_cells_within_bands(
    frequencies: np.ndarray,
    lower_edges: np.ndarray,
    upper_edges: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    lower = np.asarray(lower_edges, dtype=float).ravel()
    upper = np.asarray(upper_edges, dtype=float).ravel()
    count = min(f.size, lower.size, upper.size)
    f = f[:count]
    lower = lower[:count]
    upper = upper[:count]
    cell_lower = lower.copy()
    cell_upper = upper.copy()
    for index in range(count):
        same_band = np.where((np.isclose(lower, lower[index], rtol=1e-9, atol=1e-12)) & (np.isclose(upper, upper[index], rtol=1e-9, atol=1e-12)))[0]
        if same_band.size <= 1:
            continue
        position = int(np.where(same_band == index)[0][0])
        if position > 0:
            previous_index = int(same_band[position - 1])
            cell_lower[index] = max(lower[index], 0.5 * (f[previous_index] + f[index]))
        if position + 1 < same_band.size:
            next_index = int(same_band[position + 1])
            cell_upper[index] = min(upper[index], 0.5 * (f[index] + f[next_index]))
    valid = np.isfinite(cell_lower) & np.isfinite(cell_upper) & (cell_upper > cell_lower)
    cell_lower = np.where(valid, cell_lower, lower)
    cell_upper = np.where(valid, cell_upper, upper)
    return cell_lower, cell_upper


def _hermitian_psd_matrix(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=complex)
    if values.ndim != 3 or values.shape[1] != values.shape[2]:
        return np.zeros((0, 0, 0), dtype=complex)
    values = 0.5 * (values + np.conj(np.swapaxes(values, 1, 2)))
    for index in range(values.shape[0]):
        real_diag = np.maximum(np.real(np.diag(values[index])), 0.0)
        values[index][np.diag_indices(values.shape[1])] = real_diag
        eigenvalues, eigenvectors = np.linalg.eigh(values[index])
        if np.min(eigenvalues) < -1e-12:
            eigenvalues = np.maximum(eigenvalues, 0.0)
            values[index] = (eigenvectors * eigenvalues) @ np.conj(eigenvectors.T)
            values[index] = 0.5 * (values[index] + np.conj(values[index].T))
            real_diag = np.maximum(np.real(np.diag(values[index])), 0.0)
            values[index][np.diag_indices(values.shape[1])] = real_diag
    return values


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


def interpolate_coherence(
    source_frequency: np.ndarray,
    source_coherence: np.ndarray,
    target_frequency: np.ndarray,
) -> np.ndarray:
    f, coh = _sorted_finite_real_pair(source_frequency, source_coherence, positive_y=True)
    f_target = np.asarray(target_frequency, dtype=float).ravel()
    if f.size < 2 or f_target.size == 0:
        return np.ones(f_target.shape, dtype=float)
    return np.interp(f_target, f, np.clip(coh, 0.0, 1.0))


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
