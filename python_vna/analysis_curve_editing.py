from __future__ import annotations

import numpy as np


def sorted_finite_points(
    frequency_hz: np.ndarray,
    values: np.ndarray,
    *,
    positive_values: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequency_hz, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(f.size, y.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    f = f[:count]
    y = y[:count]
    valid = np.isfinite(f) & np.isfinite(y) & (f > 0.0)
    if positive_values:
        valid &= y > 0.0
    f = f[valid]
    y = y[valid]
    if f.size == 0:
        return f, y
    order = np.argsort(f)
    f = f[order]
    y = y[order]
    unique_f: list[float] = []
    unique_y: list[float] = []
    for freq, value in zip(f, y):
        if unique_f and np.isclose(freq, unique_f[-1], rtol=1e-12, atol=0.0):
            unique_y[-1] = float(value)
        else:
            unique_f.append(float(freq))
            unique_y.append(float(value))
    return np.asarray(unique_f, dtype=float), np.asarray(unique_y, dtype=float)


def log_frequency_grid(
    min_frequency_hz: float,
    max_frequency_hz: float,
    *,
    points: int = 512,
) -> np.ndarray:
    low = float(min_frequency_hz)
    high = float(max_frequency_hz)
    if not np.isfinite(low) or not np.isfinite(high) or low <= 0.0 or high <= low:
        return np.array([], dtype=float)
    return np.logspace(np.log10(low), np.log10(high), max(2, int(points)))


def evaluate_db_control_curve(
    control_frequency_hz: np.ndarray,
    control_db: np.ndarray,
    target_frequency_hz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    control_f, control_y = sorted_finite_points(control_frequency_hz, control_db)
    target_f = np.asarray(target_frequency_hz, dtype=float).ravel()
    target_f = target_f[np.isfinite(target_f) & (target_f > 0.0)]
    if control_f.size < 2 or target_f.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    in_band = (target_f >= control_f[0]) & (target_f <= control_f[-1])
    target_f = target_f[in_band]
    if target_f.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    log_control = np.log10(control_f)
    log_target = np.log10(target_f)
    if control_f.size == 2:
        fitted = np.interp(log_target, log_control, control_y)
    else:
        from scipy.interpolate import PchipInterpolator

        interpolator = PchipInterpolator(log_control, control_y, extrapolate=False)
        fitted = np.asarray(interpolator(log_target), dtype=float)
    valid = np.isfinite(fitted)
    return target_f[valid], fitted[valid]


def transfer_from_db_points(
    control_frequency_hz: np.ndarray,
    control_db: np.ndarray,
    target_frequency_hz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    f, db = evaluate_db_control_curve(control_frequency_hz, control_db, target_frequency_hz)
    if f.size == 0:
        return f, np.array([], dtype=float)
    return f, 10.0 ** (db / 20.0)


def apply_db_magnitude_profile(
    source_frequency_hz: np.ndarray,
    source_transfer: np.ndarray,
    control_frequency_hz: np.ndarray,
    control_db: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    source_f = np.asarray(source_frequency_hz, dtype=float).ravel()
    source_h = np.asarray(source_transfer).ravel()
    count = min(source_f.size, source_h.size)
    if count < 2:
        return np.array([], dtype=float), np.array([], dtype=complex)
    source_f = source_f[:count]
    source_h = source_h[:count]
    valid = np.isfinite(source_f) & (source_f > 0.0) & np.isfinite(np.real(source_h)) & np.isfinite(np.imag(source_h))
    source_f = source_f[valid]
    source_h = source_h[valid]
    if source_f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=complex)
    order = np.argsort(source_f)
    source_f = source_f[order]
    source_h = source_h[order]
    f, magnitude = transfer_from_db_points(control_frequency_hz, control_db, source_f)
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=complex)
    h_real = np.interp(f, source_f, np.real(source_h))
    h_imag = np.interp(f, source_f, np.imag(source_h))
    phase_source = h_real + 1.0j * h_imag
    phase = np.exp(1.0j * np.angle(phase_source))
    if not np.iscomplexobj(source_transfer):
        return f, magnitude
    return f, magnitude * phase


def apply_power_db_profile(
    source_frequency_hz: np.ndarray,
    source_power: np.ndarray,
    control_frequency_hz: np.ndarray,
    control_db: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    source_f, source_y = sorted_finite_points(source_frequency_hz, source_power, positive_values=True)
    if source_f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    f, db = evaluate_db_control_curve(control_frequency_hz, control_db, source_f)
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    return f, 10.0 ** (db / 10.0)


def sample_curve_as_db_points(
    frequency_hz: np.ndarray,
    values: np.ndarray,
    *,
    count: int = 8,
    power_values: bool,
    target_frequency_hz: np.ndarray | None = None,
    max_count: int | None = None,
    error_threshold_db: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    f, y = sorted_finite_points(frequency_hz, values, positive_values=True)
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    if target_frequency_hz is None:
        target_count = min(max(2, int(count)), int(f.size))
        targets = log_frequency_grid(f[0], f[-1], points=target_count)
    else:
        targets = np.asarray(target_frequency_hz, dtype=float).ravel()
        targets = targets[np.isfinite(targets) & (targets >= f[0]) & (targets <= f[-1]) & (targets > 0.0)]
        if targets.size < 2:
            target_count = min(max(2, int(count)), int(f.size))
            targets = log_frequency_grid(f[0], f[-1], points=target_count)
    if targets.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    log_f = np.log10(f)
    if power_values:
        y_db = 10.0 * np.log10(np.maximum(y, 1e-300))
    else:
        y_db = 20.0 * np.log10(np.maximum(np.abs(y), 1e-300))
    sampled = np.interp(np.log10(targets), log_f, y_db)
    control_f, control_db = sorted_finite_points(targets, sampled)

    if max_count is None or error_threshold_db is None:
        return control_f, control_db
    limit = min(max(2, int(max_count)), int(f.size))
    threshold = float(error_threshold_db)
    if not np.isfinite(threshold) or threshold <= 0.0 or control_f.size >= limit:
        return control_f, control_db

    while control_f.size < limit:
        fitted_f, fitted_db = evaluate_db_control_curve(control_f, control_db, f)
        if fitted_f.size < 2:
            break
        source_db = np.interp(np.log10(fitted_f), log_f, y_db)
        error = np.abs(fitted_db - source_db)
        if error.size == 0:
            break
        for existing_f in control_f:
            error[np.isclose(fitted_f, existing_f, rtol=1e-10, atol=0.0)] = -np.inf
        candidate_index = int(np.argmax(error))
        if not np.isfinite(error[candidate_index]) or error[candidate_index] <= threshold:
            break
        candidate_f = float(fitted_f[candidate_index])
        candidate_db = float(source_db[candidate_index])
        control_f = np.append(control_f, candidate_f)
        control_db = np.append(control_db, candidate_db)
        order = np.argsort(control_f)
        control_f = control_f[order]
        control_db = control_db[order]
    return control_f, control_db


def stitch_frequency_curves(
    primary_frequency_hz: np.ndarray,
    primary_values: np.ndarray,
    secondary_frequency_hz: np.ndarray,
    secondary_values: np.ndarray,
    split_frequency_hz: float,
) -> tuple[np.ndarray, np.ndarray]:
    split = float(split_frequency_hz)
    if not np.isfinite(split) or split <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    primary_f, primary_y = sorted_finite_points(primary_frequency_hz, primary_values, positive_values=True)
    secondary_f, secondary_y = sorted_finite_points(secondary_frequency_hz, secondary_values, positive_values=True)
    if primary_f.size == 0 or secondary_f.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    left = primary_f <= split
    right = secondary_f > split
    out_f_parts: list[np.ndarray] = []
    out_y_parts: list[np.ndarray] = []
    if np.any(left):
        out_f_parts.append(primary_f[left])
        out_y_parts.append(primary_y[left])
    if primary_f[0] < split < primary_f[-1] and (not out_f_parts or out_f_parts[-1][-1] < split):
        out_f_parts.append(np.array([split], dtype=float))
        out_y_parts.append(np.array([np.interp(split, primary_f, primary_y)], dtype=float))
    if np.any(right):
        out_f_parts.append(secondary_f[right])
        out_y_parts.append(secondary_y[right])
    if not out_f_parts:
        return np.array([], dtype=float), np.array([], dtype=float)
    return np.concatenate(out_f_parts), np.concatenate(out_y_parts)
