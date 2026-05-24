from __future__ import annotations

import numpy as np


def transform_curve(values: np.ndarray, value_mode: str) -> np.ndarray:
    if value_mode == "raw":
        return np.asarray(values)
    if value_mode == "linear":
        return np.abs(values)
    if value_mode == "power":
        return np.asarray(values)
    if value_mode == "linear_per_sqrt_hz":
        return np.sqrt(np.maximum(np.abs(values), 1e-20))
    if value_mode == "power_per_hz":
        return np.asarray(values)
    if value_mode == "pk":
        return np.abs(values) * np.sqrt(2.0)
    if value_mode == "p2p":
        return np.abs(values) * 2.0 * np.sqrt(2.0)
    if value_mode == "mag":
        return np.abs(values)
    if value_mode == "dB":
        return 20.0 * np.log10(np.maximum(np.abs(values), 1e-20))
    if value_mode == "dB_per_sqrt_hz":
        return 20.0 * np.log10(np.maximum(np.sqrt(np.abs(values)), 1e-20))
    if value_mode == "log_linear":
        return np.abs(values)
    if value_mode == "log_power":
        return np.asarray(values)
    if value_mode == "log_linear_per_sqrt_hz":
        return np.sqrt(np.maximum(np.abs(values), 0.0))
    if value_mode == "log_power_per_hz":
        return np.abs(values)
    if value_mode == "log_pk":
        return np.abs(values) * np.sqrt(2.0)
    if value_mode == "log_p2p":
        return np.abs(values) * 2.0 * np.sqrt(2.0)
    if value_mode == "log_mag":
        return np.abs(values)
    if value_mode == "real":
        return np.real(values)
    if value_mode == "imag":
        return np.imag(values)
    if value_mode == "phase":
        return np.angle(values) * 180.0 / np.pi
    if value_mode == "phase_u":
        return np.unwrap(np.angle(values)) * 180.0 / np.pi
    return np.asarray(values)


def legacy_frequency_int_vector(freqs: np.ndarray, yintfac_index: int) -> np.ndarray:
    freqs = np.asarray(freqs, dtype=float)
    base = 2.0 * np.pi * freqs
    if yintfac_index == 2:
        vector = np.divide(1.0, base**2, out=np.full_like(base, np.nan), where=base != 0.0)
    elif yintfac_index == 3:
        vector = np.divide(1.0, base**4, out=np.full_like(base, np.nan), where=base != 0.0)
    elif yintfac_index == 4:
        vector = base**2
    elif yintfac_index == 5:
        vector = base**4
    else:
        return np.ones_like(freqs, dtype=float)
    vector = np.asarray(vector, dtype=float)
    if vector.size and (not np.isfinite(vector[0]) or vector[0] == 0.0):
        vector[0] = np.nan
    return vector


def legacy_j_factor(yintfac_index: int) -> complex:
    return {
        1: 1.0 + 0.0j,
        2: -1.0j,
        3: -1.0 + 0.0j,
        4: 1.0j,
        5: -1.0 + 0.0j,
    }.get(yintfac_index, 1.0 + 0.0j)


def align_vector_to_values(vector: np.ndarray | float, values: np.ndarray) -> np.ndarray | float:
    vector_arr = np.asarray(vector)
    if vector_arr.ndim == 0:
        return vector_arr.item()
    point_count = min(vector_arr.size, np.asarray(values).size)
    return vector_arr[:point_count]


def transform_autospectrum(values: np.ndarray, value_mode: str, rbw_hz: float = 1.0) -> np.ndarray:
    power = np.maximum(np.abs(values), 0.0)
    rbw_hz = max(float(rbw_hz), 1e-20)
    density_power = power / rbw_hz
    if value_mode == "dB":
        return 10.0 * np.log10(np.maximum(power, 1e-20))
    if value_mode == "dB_per_sqrt_hz":
        return 10.0 * np.log10(np.maximum(density_power, 1e-20))
    if value_mode == "linear":
        return np.sqrt(power)
    if value_mode == "linear_per_sqrt_hz":
        return np.sqrt(density_power)
    if value_mode == "power":
        return power
    if value_mode == "power_per_hz":
        return density_power
    if value_mode == "pk":
        return np.sqrt(power) * np.sqrt(2.0)
    if value_mode == "p2p":
        return np.sqrt(power) * 2.0 * np.sqrt(2.0)
    if value_mode == "log_linear":
        return np.sqrt(power)
    if value_mode == "log_linear_per_sqrt_hz":
        return np.sqrt(density_power)
    if value_mode == "log_power":
        return power
    if value_mode == "log_power_per_hz":
        return density_power
    if value_mode == "log_pk":
        return np.sqrt(power) * np.sqrt(2.0)
    if value_mode == "log_p2p":
        return np.sqrt(power) * 2.0 * np.sqrt(2.0)
    return power


def transform_legacy_autospectrum(
    values: np.ndarray,
    value_mode: str,
    rbw_hz: float,
    euscale_fac: float = 1.0,
    db_ref: float = 1.0,
    units_value: float = 1.0,
    wincor: float = 1.0,
    yapcor_index: int = 1,
    int_vec: np.ndarray | float = 1.0,
) -> np.ndarray:
    power = np.maximum(np.abs(values), 0.0)
    rbw_hz = max(float(rbw_hz), 1e-20)
    wcor_fac = float(wincor) if int(yapcor_index) == 2 else 1.0
    eu_units = float(euscale_fac) * float(units_value)
    int_vec_arr = np.asarray(int_vec, dtype=float)
    if int_vec_arr.ndim == 0:
        int_vec_arr = 1.0
    else:
        int_vec_arr = align_vector_to_values(int_vec_arr, power)
        power = power[: np.asarray(int_vec_arr).size]
    db_ref = max(abs(float(db_ref)), 1e-20)
    if value_mode == "dB":
        scale = wcor_fac * (abs(eu_units) / db_ref) ** 2
        return 10.0 * np.log10(np.maximum(scale * power * int_vec_arr, 1e-307))
    if value_mode == "dB_per_sqrt_hz":
        scale = (wcor_fac / rbw_hz) * (abs(eu_units) / db_ref) ** 2
        return 10.0 * np.log10(np.maximum(scale * power * int_vec_arr, 1e-307))
    if value_mode in {"linear", "log_linear"}:
        scale = wcor_fac * (eu_units ** 2)
        return np.sqrt(np.maximum(scale * power * int_vec_arr, 0.0))
    if value_mode in {"linear_per_sqrt_hz", "log_linear_per_sqrt_hz"}:
        scale = (wcor_fac / rbw_hz) * (eu_units ** 2)
        return np.sqrt(np.maximum(scale * power * int_vec_arr, 0.0))
    if value_mode in {"power", "log_power"}:
        scale = wcor_fac * (eu_units ** 2)
        return scale * power * int_vec_arr
    if value_mode in {"power_per_hz", "log_power_per_hz"}:
        scale = (wcor_fac / rbw_hz) * (eu_units ** 2)
        return scale * power * int_vec_arr
    if value_mode == "pk":
        return (
            transform_legacy_autospectrum(
                values,
                "linear",
                rbw_hz,
                euscale_fac,
                db_ref,
                units_value,
                wincor,
                yapcor_index,
                int_vec_arr,
            )
            * np.sqrt(2.0)
        )
    if value_mode == "p2p":
        return (
            transform_legacy_autospectrum(
                values,
                "linear",
                rbw_hz,
                euscale_fac,
                db_ref,
                units_value,
                wincor,
                yapcor_index,
                int_vec_arr,
            )
            * 2.0
            * np.sqrt(2.0)
        )
    if value_mode == "log_pk":
        return (
            transform_legacy_autospectrum(
                values,
                "log_linear",
                rbw_hz,
                euscale_fac,
                db_ref,
                units_value,
                wincor,
                yapcor_index,
                int_vec_arr,
            )
            * np.sqrt(2.0)
        )
    if value_mode == "log_p2p":
        return (
            transform_legacy_autospectrum(
                values,
                "log_linear",
                rbw_hz,
                euscale_fac,
                db_ref,
                units_value,
                wincor,
                yapcor_index,
                int_vec_arr,
            )
            * 2.0
            * np.sqrt(2.0)
        )
    return power
