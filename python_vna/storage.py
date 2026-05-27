from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from python_vna.models import SavedSession, SessionConfig


def _construct_dataclass(cls, payload: dict[str, Any]):
    field_types = {field.name: field.type for field in cls.__dataclass_fields__.values()}
    kwargs = {}
    for key, value in payload.items():
        if key not in field_types:
            continue
        kwargs[key] = value
    return cls(**kwargs)


def _as_int_list(value: Any) -> list[int]:
    squeezed = np.squeeze(value)
    if getattr(squeezed, "size", 0) == 0:
        return []
    if np.isscalar(squeezed):
        return [int(squeezed)]
    return [int(x) for x in np.ravel(squeezed).tolist()]


def _legacy_scalar(value: Any, default: float = 0.0) -> float:
    try:
        squeezed = np.squeeze(value)
        if getattr(squeezed, "size", 0) == 0:
            return default
        if np.isscalar(squeezed):
            return float(squeezed)
        return float(np.ravel(squeezed)[0])
    except Exception:
        return default


def _legacy_string(value: Any, default: str = "") -> str:
    try:
        squeezed = np.squeeze(value)
        if getattr(squeezed, "size", 0) == 0:
            return default
        text = str(squeezed if np.isscalar(squeezed) else np.ravel(squeezed)[0])
    except Exception:
        text = str(value)
    parts = [part.strip() for part in text.split("~")]
    return next((part for part in parts if part), default).strip()


def _legacy_packed_strings(value: Any) -> tuple[str, str]:
    try:
        squeezed = np.squeeze(value)
        text = str(squeezed if np.isscalar(squeezed) else np.ravel(squeezed)[0])
    except Exception:
        text = str(value)
    parts = [part.strip() for part in text.split("~")]
    clean = [part for part in parts if part]
    if len(clean) >= 2:
        return clean[0], clean[1]
    if clean:
        return clean[0], ""
    return "", ""


def _legacy_unwrap_cell(value: Any) -> Any:
    current = value
    while isinstance(current, np.ndarray) and current.dtype == object and current.size == 1:
        current = np.ravel(current)[0]
    return current


_LEGACY_FULL_SCALE_BY_INDEX = {
    1: 10.0,
    2: 5.0,
    3: 2.5,
    4: 1.25,
    5: 0.625,
    6: 0.3125,
    7: 0.15625,
    8: 0.078125,
    9: 0.0390625,
    10: 0.01953125,
    11: -1.0,
}

_LEGACY_COUPLING_BY_INDEX = {
    0: "ac",
    1: "dc",
    2: "bias",
}

_LEGACY_PER_EU_BY_INDEX = {
    0: "Off",
    1: "/Volt",
    2: "/mV",
    3: "/uV",
    4: "/kV",
}

_LEGACY_AVERAGE_MODE_BY_INDEX = {
    1: "linear",
    2: "exponential",
    3: "peak",
    4: "exponential",
    5: "linear",
}

_LEGACY_PROCESSING_WINDOW_BY_INDEX = {
    1: "boxcar",
    2: "hanning",
    3: "flattop",
    4: "flat301",
    5: "flat201",
    6: "potter210",
    7: "potter310",
    8: "hamming",
    9: "blackman",
    10: "exact_blackman",
    11: "blackman_harris_61",
    12: "blackman_harris_67",
    13: "blackman_harris_74",
    14: "blackman_harris_92",
    15: "modal_box_exp_0_1",
    16: "modal_box_exp_0_01",
    17: "modal_force20_exp_0_1",
    18: "modal_force20_exp_0_01",
    19: "modal_user",
}

_LEGACY_TRIGGER_MODE_BY_INDEX = {
    1: "Off (Free Run)",
    2: "Every Frame",
    3: "1st Frame",
    4: "Manual Arm",
    5: "1st-Manual Arm",
}

_LEGACY_OVERLAP_PERCENT_BY_INDEX = {
    1: 0,
    2: 50,
    3: 100,
}

_LEGACY_USB4431_SYSTEM_CLOCK = 51200
_LEGACY_USB4431_DECIMATIONS = [1, 2, 4, 10, 20, 40, 100, 200, 400, 1000, 2000]

_LEGACY_FULL_SCALE_INDEX_BY_VALUE = {
    value: index for index, value in _LEGACY_FULL_SCALE_BY_INDEX.items()
}
_LEGACY_COUPLING_INDEX_BY_VALUE = {
    value: index for index, value in _LEGACY_COUPLING_BY_INDEX.items()
}
_LEGACY_PER_EU_INDEX_BY_VALUE = {
    value: index for index, value in _LEGACY_PER_EU_BY_INDEX.items()
}
_LEGACY_AVERAGE_MODE_INDEX_BY_VALUE = {
    "linear": 1,
    "exponential": 2,
    "peak": 3,
    "off": 1,
}
_LEGACY_PROCESSING_WINDOW_INDEX_BY_VALUE = {
    value: index for index, value in _LEGACY_PROCESSING_WINDOW_BY_INDEX.items()
}
_LEGACY_WINDOW_POWER_CORRECTION_BY_INDEX = {
    1: 1.0,
    2: 0.6666666667,
    3: 0.2617872274,
    4: 0.2920416990,
    5: 0.3378558539,
    6: 0.5642952975,
    7: 0.4947506823,
    8: 0.7311777141,
    9: 0.5791201619,
    10: 0.5904311459,
    11: 0.6208287665,
    12: 0.5852957066,
    13: 0.5583575867,
    14: 0.4989141365,
    15: 0.99,
    16: 0.99,
    17: 0.99,
    18: 0.99,
    19: 0.99,
}
_LEGACY_TRIGGER_MODE_INDEX_BY_VALUE = {
    value: index for index, value in _LEGACY_TRIGGER_MODE_BY_INDEX.items()
}
_LEGACY_OVERLAP_INDEX_BY_PERCENT = {
    value: index for index, value in _LEGACY_OVERLAP_PERCENT_BY_INDEX.items()
}

_LEGACY_DISPLAY_MODE_BY_INDEX = {
    1: "time",
    2: "autospectrum",
    3: "frf",
    4: "coherence",
    5: "cross_spectrum",
    6: "auto_correlation",
    7: "cross_correlation",
    8: "impulse_response",
    9: "fft",
}
_LEGACY_DISPLAY_INDEX_BY_MODE = {
    value: index for index, value in _LEGACY_DISPLAY_MODE_BY_INDEX.items()
}

_LEGACY_VALUE_MODE_BY_DISPLAY_INDEX = {
    "time": {
        1: "real",
        2: "mag",
        3: "imag",
    },
    "autospectrum": {
        1: "dB",
        2: "dB_per_sqrt_hz",
        3: "linear",
        4: "power",
        5: "linear_per_sqrt_hz",
        6: "power_per_hz",
        8: "log_linear",
        9: "log_power",
        10: "log_linear_per_sqrt_hz",
        11: "log_power_per_hz",
    },
    "frf": {
        1: "real",
        2: "mag",
        3: "imag",
        4: "dB",
        5: "log_mag",
        6: "phase",
        7: "phase_u",
        8: "nyquist",
    },
    "coherence": {
        1: "mag",
    },
    "cross_spectrum": {
        1: "real",
        2: "mag",
        3: "imag",
        4: "dB",
        5: "log_mag",
        6: "phase",
        7: "phase_u",
        8: "nyquist",
    },
    "auto_correlation": {
        1: "real",
        2: "mag",
        3: "imag",
    },
    "cross_correlation": {
        1: "real",
        2: "mag",
        3: "imag",
    },
    "impulse_response": {
        1: "real",
        2: "mag",
        3: "imag",
    },
    "fft": {
        1: "real",
        2: "mag",
        3: "imag",
        4: "dB",
    },
}
_LEGACY_VALUE_INDEX_BY_DISPLAY_MODE = {
    display_mode: {
        value_mode: index
        for index, value_mode in value_modes.items()
    }
    for display_mode, value_modes in _LEGACY_VALUE_MODE_BY_DISPLAY_INDEX.items()
}


def _json_default(value: Any):
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return {"real": value.real.tolist(), "imag": value.imag.tolist()}
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _legacy_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_legacy_scalar(value, float(default))))
    except Exception:
        return default


def _legacy_row_values(value: Any) -> list[float]:
    try:
        squeezed = np.squeeze(value)
        if getattr(squeezed, "size", 0) == 0:
            return []
        return [float(item) for item in np.ravel(squeezed).tolist()]
    except Exception:
        return []


def _legacy_trigger_percent(index: int) -> float:
    n_levels = 32.0
    root_two = np.sqrt(2.0)
    max_level = round(0.7 * n_levels / (2.0 * root_two))
    values = [
        level * 100.0 * root_two / (n_levels / 2.0)
        for level in range(max_level, -max_level - 1, -1)
    ]
    if 1 <= index <= len(values):
        return float(values[index - 1])
    zero_index = len(values) // 2
    return float(values[zero_index])


def _legacy_sample_rate_from_hdlg(
    hdlg1_s1: list[float],
    system_clock: float,
    default: float,
) -> tuple[float, float]:
    if not hdlg1_s1:
        return default, default / 2.56
    decimation_tables = {
        51200: [1, 2, 4, 10, 20, 40, 100, 200, 400, 1000, 2000],
        128000: [1, 2.5, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
    }
    clock_key = min(
        decimation_tables,
        key=lambda candidate: abs(float(candidate) - float(system_clock)),
    )
    if abs(float(clock_key) - float(system_clock)) > 1.0:
        return default, default / 2.56
    sample_index = max(1, int(round(hdlg1_s1[0])))
    table = decimation_tables[clock_key]
    decimation = float(table[min(sample_index - 1, len(table) - 1)])
    sample_rate = float(system_clock) / decimation
    if sample_rate < 1000.0:
        sample_rate = 1000.0
    return sample_rate, sample_rate / 2.56


def _legacy_sample_index_for_rate(sample_rate: float) -> tuple[int, float, float]:
    target = max(float(sample_rate), 1.0)
    rates = [
        _LEGACY_USB4431_SYSTEM_CLOCK / float(decimation)
        for decimation in _LEGACY_USB4431_DECIMATIONS
    ]
    index = min(range(len(rates)), key=lambda idx: abs(rates[idx] - target))
    rate = max(rates[index], 1000.0)
    return index + 1, rate, rate / 2.56


def _legacy_strpack(length: int, *parts: str) -> str:
    text = "~".join(str(part).strip() for part in parts)
    if len(text) < length:
        text += "~" * (length - len(text))
        return text
    return text[: length - 1] + "~"


def _legacy_window_power_correction(window_index: int) -> float:
    return float(_LEGACY_WINDOW_POWER_CORRECTION_BY_INDEX.get(int(window_index), 1.0))


def _legacy_char_matrix(values: list[str], width: int) -> np.ndarray:
    if not values:
        return np.empty((0,), dtype=f"<U{width}")
    return np.asarray(
        [
            value[:width].ljust(width)
            for value in values
        ],
        dtype=f"<U{width}",
    )


def _legacy_trigger_index_for_percent(percent: float | None) -> int:
    if percent is None:
        return 9
    values = [_legacy_trigger_percent(index) for index in range(1, 18)]
    return min(range(1, len(values) + 1), key=lambda idx: abs(values[idx - 1] - float(percent)))


def _legacy_trigger_source_index(source: str, input_channel_count: int, output_channel_count: int) -> int:
    normalized = (source or "").strip().lower()
    if normalized.startswith("ai"):
        try:
            return max(1, int(normalized[2:]) + 1)
        except ValueError:
            return 1
    if normalized.startswith("ao"):
        try:
            return input_channel_count + max(1, int(normalized[2:]) + 1)
        except ValueError:
            return input_channel_count + 1
    if normalized in {"external", "ext"}:
        return input_channel_count + output_channel_count + 1
    return 1


def _legacy_trigger_source(source_index: int, input_channel_count: int, output_channel_count: int) -> str:
    if 1 <= source_index <= max(input_channel_count, 0):
        return f"ai{source_index - 1}"
    if input_channel_count < source_index <= input_channel_count + max(output_channel_count, 0):
        return f"ao{source_index - input_channel_count - 1}"
    return "external"


def _legacy_display_trace_names(
    mode: str,
    channel_flags: list[bool],
    channel_count: int,
    channel_trace_names: list[str],
    reference_name: str,
    response_names: list[str],
) -> list[str]:
    selected_channels = [
        f"ai{index}" for index, enabled in enumerate(channel_flags[:channel_count]) if enabled
    ]
    if not selected_channels:
        return []
    if mode in {"frf", "coherence", "cross_spectrum", "cross_correlation", "impulse_response"}:
        valid_responses = set(response_names)
        traces = [
            f"{reference_name}->{name}"
            for name in selected_channels
            if name != reference_name and (not valid_responses or name in valid_responses)
        ]
        return traces
    return [
        channel_trace_names[int(name[2:])]
        for name in selected_channels
        if name.startswith("ai") and int(name[2:]) < len(channel_trace_names)
    ]


def _legacy_x_axis_unit_factor(index: int) -> float:
    if index == 2:
        return 1000.0
    if index == 3:
        return 1.0 / 60.0
    if index == 4:
        return 1000.0 / 60.0
    return 1.0


def _legacy_trace_response_name(trace_name: str) -> str:
    return str(trace_name).split("->")[-1].strip()


def _legacy_trace_reference_name(trace_name: str, default: str = "ai0") -> str:
    trace_text = str(trace_name)
    if "->" in trace_text:
        return trace_text.split("->", 1)[0].strip()
    return default


def _legacy_channel_alias_maps(channels: list[Any]) -> tuple[dict[str, int], list[str]]:
    alias_to_index: dict[str, int] = {}
    channel_trace_names: list[str] = []
    for index, channel in enumerate(channels):
        name = str(getattr(channel, "name", f"ai{index}") or f"ai{index}")
        label = str(getattr(channel, "label", "") or "").strip()
        aliases = {name, f"ai{index}", f"Channel {index + 1}", f"Ch {index + 1}"}
        if label:
            aliases.add(label)
        for alias in aliases:
            if alias:
                alias_to_index.setdefault(alias, index)
        channel_trace_names.append(label or name)
    return alias_to_index, channel_trace_names


def _legacy_channel_index_from_trace(
    trace_name: Any,
    alias_to_index: dict[str, int],
    *,
    relation_endpoint: str = "response",
) -> int | None:
    trace_text = str(trace_name).strip()
    if not trace_text:
        return None
    endpoint = trace_text
    if "->" in trace_text:
        left, right = [part.strip() for part in trace_text.split("->", 1)]
        endpoint = left if relation_endpoint == "reference" else right
    if endpoint in alias_to_index:
        return alias_to_index[endpoint]
    if endpoint.startswith("ai"):
        try:
            return int(endpoint[2:])
        except ValueError:
            return None
    return None


def _parse_legacy_display_state(
    mat: dict[str, Any],
    input_channel_count: int,
    channel_trace_names: list[str],
    reference_names: list[str],
    response_names: list[str],
) -> dict[str, Any]:
    raw_state = mat.get("xplot_s1")
    if raw_state is None:
        return {}
    axes = np.asarray(mat.get("xplot_axes", np.empty((0, 0))), dtype=float)
    reference_name = reference_names[0] if reference_names else "ai0"
    display_state: dict[str, Any] = {}
    for key, index in (("top", 0), ("bottom", 1)):
        if raw_state.size <= index:
            continue
        state = np.ravel(raw_state)[index]
        mode_index = _legacy_int(getattr(state, "ypu1sel", np.array([[1]])), 1)
        mode = _LEGACY_DISPLAY_MODE_BY_INDEX.get(mode_index)
        if mode is None:
            continue
        value_index = _legacy_int(getattr(state, "ypu2sel", np.array([[1]])), 1)
        value_mode = _LEGACY_VALUE_MODE_BY_DISPLAY_INDEX.get(mode, {}).get(value_index)
        x_index = _legacy_int(getattr(state, "xpu1sel", np.array([[1]])), 1)
        xscale = "log" if mode in {"autospectrum", "frf", "coherence", "cross_spectrum"} and x_index == 2 else "linear"
        channel_flags = [bool(value) for value in np.ravel(getattr(state, "ylcb", np.array([], dtype=int))).tolist()]
        trace_names = _legacy_display_trace_names(
            mode,
            channel_flags,
            input_channel_count,
            channel_trace_names,
            reference_name,
            response_names,
        )
        panel_state: dict[str, Any] = {
            "mode": mode,
            "legacy_mode_index": mode_index,
            "xscale": xscale,
            "trace_names": trace_names,
            "reference_channel": reference_name,
        }
        if value_mode is not None:
            panel_state["value_mode"] = value_mode
        panel_state["legacy_yintfac_index"] = _legacy_int(
            getattr(state, "yintfac", np.array([[1]])), 1
        )
        panel_state["legacy_yapcor_index"] = _legacy_int(
            getattr(state, "yapcor", np.array([[1]])), 1
        )
        panel_state["legacy_xcref_index"] = _legacy_int(
            getattr(state, "xcref", np.array([[1]])), 1
        )
        x_unit_index = _legacy_int(getattr(state, "xpu2sel", np.array([[1]])), 1)
        panel_state["legacy_x_unit_index"] = x_unit_index
        axis_row_index = index * 5
        if axes.shape[0] > axis_row_index and axes.shape[1] >= 5:
            row = axes[axis_row_index]
            if row[4] >= 0:
                factor = _legacy_x_axis_unit_factor(x_unit_index)
                panel_state["axis_range"] = {
                    "xmin": float(row[0] * factor),
                    "xmax": float(row[1] * factor),
                    "ymin": float(row[2]),
                    "ymax": float(row[3]),
                    "visible": bool(row[4]),
                }
        display_state[key] = panel_state
    if display_state:
        display_state["layout"] = "single" if _legacy_int(getattr(np.ravel(raw_state)[0], "plot_mode", np.array([[2]])), 2) == 1 else "dual"
    return display_state


def save_session_json(session: SavedSession, path: str | Path) -> Path:
    destination = Path(path)
    destination.write_text(
        json.dumps(asdict(session), indent=2, default=_json_default),
        encoding="utf-8",
    )
    return destination


def _nearest_legacy_full_scale_index(value: float) -> int:
    if value < 0.0:
        return 11
    full_scale_value = min(
        _LEGACY_FULL_SCALE_INDEX_BY_VALUE,
        key=lambda candidate: abs(float(candidate) - float(value)),
    )
    return _LEGACY_FULL_SCALE_INDEX_BY_VALUE[full_scale_value]


def _measurement_channel_values(
    values_by_name: dict[str, Any],
    channel_name: str,
    channel_label: str,
    default: np.ndarray,
) -> np.ndarray:
    for key in (channel_label, channel_name):
        if key in values_by_name:
            return np.asarray(values_by_name[key])
    return default


def _mat_column(values: Any, dtype: Any | None = None) -> np.ndarray:
    array = np.asarray(values, dtype=dtype)
    return np.ravel(array).reshape((-1, 1))


def _mat_cell(values: list[Any], shape: tuple[int, int] | None = None) -> np.ndarray:
    if shape is None:
        shape = (1, len(values))
    cell = np.empty(shape, dtype=object)
    for index, value in enumerate(values):
        cell.flat[index] = value
    return cell


def _finite_limits(
    values: Any,
    fallback: tuple[float, float],
    *,
    positive_only: bool = False,
    padding: float = 0.0,
) -> tuple[float, float]:
    try:
        array = np.asarray(values)
        if np.iscomplexobj(array):
            array = np.abs(array)
        array = np.asarray(array, dtype=float).ravel()
    except Exception:
        array = np.array([], dtype=float)
    finite = array[np.isfinite(array)]
    if positive_only:
        finite = finite[finite > 0.0]
    if finite.size:
        minimum = float(np.min(finite))
        maximum = float(np.max(finite))
    else:
        minimum, maximum = fallback
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        minimum, maximum = fallback
    if positive_only:
        if maximum <= 0.0:
            fallback_min, fallback_max = fallback
            minimum = fallback_min if fallback_min > 0.0 else 1e-20
            maximum = fallback_max if fallback_max > minimum else minimum * 10.0
        elif minimum <= 0.0:
            minimum = max(maximum * 1e-12, 1e-300)
    if maximum <= minimum:
        if positive_only and minimum > 0.0:
            minimum *= 0.9
            maximum *= 1.1
        else:
            span = max(abs(minimum), 1.0) * 0.05
            minimum -= span
            maximum += span
    elif padding > 0.0:
        span = (maximum - minimum) * padding
        minimum -= span
        maximum += span
        if positive_only and minimum <= 0.0:
            minimum = max((maximum - span) * 1e-12, 1e-300)
    if maximum <= minimum:
        minimum, maximum = fallback
    if maximum <= minimum:
        maximum = minimum + max(abs(minimum), 1.0) * 0.1
    return float(minimum), float(maximum)


def _legacy_measurement_values_for_mode(
    mode: str,
    trace_name: str,
    measurement: Any,
    time_channels: dict[str, Any],
    fallback: np.ndarray,
) -> np.ndarray:
    if measurement is None:
        return fallback
    if mode == "time":
        return np.asarray(time_channels.get(trace_name, fallback))
    if mode == "autospectrum":
        return np.asarray(measurement.spectra.get("autospectrum", {}).get(trace_name, fallback))
    if mode == "fft":
        return np.asarray(measurement.spectra.get("fft", {}).get(trace_name, fallback))
    if mode == "frf":
        return np.asarray(measurement.frf.get(trace_name, fallback))
    if mode == "coherence":
        return np.asarray(measurement.coherence.get(trace_name, fallback))
    if mode == "cross_spectrum":
        return np.asarray(measurement.cross_spectra.get(trace_name, fallback))
    if mode in {"auto_correlation", "cross_correlation"}:
        return np.asarray(measurement.correlations.get(trace_name, fallback))
    if mode == "impulse_response":
        return np.asarray(measurement.impulse_responses.get(trace_name, fallback))
    return fallback


def _legacy_axis_values_for_mode(
    mode: str,
    tdxvec: np.ndarray,
    fdxvec: np.ndarray,
    y_values: np.ndarray,
    sample_rate: float,
) -> np.ndarray:
    if mode in {"autospectrum", "frf", "coherence", "cross_spectrum", "fft"}:
        return np.asarray(fdxvec, dtype=float)
    if mode in {"auto_correlation", "cross_correlation", "impulse_response"}:
        length = int(np.asarray(y_values).size) or int(np.asarray(tdxvec).size) or 1
        return np.arange(length, dtype=float) / max(float(sample_rate), 1e-20)
    return np.asarray(tdxvec, dtype=float)


def _legacy_xplot_axes(
    tdxvec: np.ndarray,
    fdxvec: np.ndarray,
    time_channels: dict[str, Any],
    channels: list[Any],
    channel_count: int,
    sample_rate: float,
    measurement: Any | None = None,
    display_state: dict[str, Any] | None = None,
) -> np.ndarray:
    axes = np.zeros((10, 5), dtype=float)
    axes[:, 4] = -1.0
    fallback_duration = (
        (max(int(np.asarray(tdxvec).size), 1) - 1) / max(float(sample_rate), 1e-20)
    )
    fallback_x = (0.0, max(fallback_duration, 1.0 / max(float(sample_rate), 1.0)))
    alias_to_index, channel_trace_names = _legacy_channel_alias_maps(channels)
    for panel_index in range(2):
        panel_key = "top" if panel_index == 0 else "bottom"
        panel_state = (
            display_state.get(panel_key, {})
            if isinstance(display_state, dict)
            else {}
        )
        if not isinstance(panel_state, dict):
            panel_state = {}
        mode = str(panel_state.get("mode") or "time")
        trace_names = panel_state.get("trace_names")
        trace_name = ""
        if isinstance(trace_names, list) and trace_names:
            trace_name = str(trace_names[0])
        channel_index = _legacy_channel_index_from_trace(trace_name, alias_to_index)
        if channel_index is None:
            channel_index = min(panel_index, max(channel_count - 1, 0))
        if 0 <= channel_index < len(channels):
            channel = channels[channel_index]
            channel_name = getattr(channel, "name", f"ai{channel_index}")
            channel_label = getattr(channel, "label", "") or f"Channel {channel_index + 1}"
            full_scale = abs(float(getattr(channel, "full_scale", 1.0) or 1.0))
        else:
            channel_name = f"ai{channel_index}"
            channel_label = f"Channel {channel_index + 1}"
            full_scale = 1.0
        default_y = np.zeros_like(np.asarray(tdxvec, dtype=float), dtype=float)
        if not trace_name:
            trace_name = (
                channel_trace_names[channel_index]
                if 0 <= channel_index < len(channel_trace_names)
                else channel_name
            )
        y_values = _legacy_measurement_values_for_mode(
            mode,
            trace_name,
            measurement,
            time_channels,
            _measurement_channel_values(
                time_channels,
                channel_name,
                channel_label,
                default_y,
            ),
        )
        x_values = _legacy_axis_values_for_mode(
            mode,
            tdxvec,
            fdxvec,
            np.asarray(y_values),
            sample_rate,
        )
        positive_x = (
            panel_state.get("xscale") == "log"
            and mode in {"autospectrum", "frf", "coherence", "cross_spectrum"}
        )
        x_limits = _finite_limits(x_values, fallback_x, positive_only=positive_x)
        y_limits = _finite_limits(y_values, (-full_scale, full_scale), padding=0.05)
        axes[panel_index * 5] = [
            x_limits[0],
            x_limits[1],
            y_limits[0],
            y_limits[1],
            1.0,
        ]
    return axes


def save_legacy_vna(session: SavedSession, path: str | Path) -> Path:
    """Save a Python VNA session as a MATLAB MAT-file with a .vna extension.

    The file is intentionally written with the legacy field names that this
    project imports from original VNA files. It is not a byte-for-byte clone of
    the old application output, but it keeps the same practical data model:
    channel setup, time data, spectra, FRF, coherence, cross spectra, and
    impulse responses.
    """

    from scipy.io import savemat

    destination = Path(path)
    config = session.config
    measurement = session.measurement
    channels = list(config.ai_channels)
    channel_count = max(len(channels), 1)
    sample_rate = float(
        measurement.sample_rate if measurement is not None else config.acquisition.sample_rate
    )
    frame_size = int(config.acquisition.frame_size)
    if measurement is not None and np.asarray(measurement.time_data.get("t", [])).size:
        tdxvec = np.asarray(measurement.time_data["t"], dtype=float)
    else:
        tdxvec = np.arange(frame_size, dtype=float) / max(sample_rate, 1e-20)
    if measurement is not None and np.asarray(measurement.spectra.get("f", [])).size:
        fdxvec = np.asarray(measurement.spectra["f"], dtype=float)
    else:
        fdxvec = np.fft.rfftfreq(max(tdxvec.size, 1), d=1.0 / max(sample_rate, 1e-20))

    max_legacy_channels = max(16, channel_count)
    max_legacy_references = min(4, max_legacy_channels)
    vdlg1_s1 = np.zeros((max_legacy_channels, 7), dtype=float)
    vdlg1_s2_values: list[str] = []
    chan_label_values: list[str] = []
    eu_label_values: list[str] = []
    chan_stat_rows: list[list[float]] = []
    scmeas_dtype = [
        ("tdmeas", "O"),
        ("aspec", "O"),
        ("fft", "O"),
        ("acor", "O"),
        ("label", "O"),
        ("eu_on_off", "O"),
        ("euscale_fac", "O"),
        ("eu_string", "O"),
        ("eu_val", "O"),
        ("fs_val", "O"),
        ("a_r_flag", "O"),
        ("db_ref", "O"),
    ]
    scmeas = np.empty((1, max_legacy_channels), dtype=scmeas_dtype)
    for index in range(max_legacy_channels):
        scmeas[0, index]["tdmeas"] = np.empty((0, 0), dtype=float)
        scmeas[0, index]["aspec"] = np.empty((0, 0), dtype=float)
        scmeas[0, index]["fft"] = np.empty((0, 0), dtype=complex)
        scmeas[0, index]["acor"] = np.empty((0, 0), dtype=float)
        scmeas[0, index]["label"] = ""
        scmeas[0, index]["eu_on_off"] = np.array([[0.0]], dtype=float)
        scmeas[0, index]["euscale_fac"] = np.array([[1.0]], dtype=float)
        scmeas[0, index]["eu_string"] = ""
        scmeas[0, index]["eu_val"] = np.array([[1.0]], dtype=float)
        scmeas[0, index]["fs_val"] = np.array([[10.0]], dtype=float)
        scmeas[0, index]["a_r_flag"] = np.array([[0.0]], dtype=float)
        scmeas[0, index]["db_ref"] = np.array([[1.0]], dtype=float)

    empty_time = np.zeros_like(tdxvec, dtype=float)
    empty_complex = np.zeros_like(fdxvec, dtype=complex)
    empty_power = np.zeros_like(fdxvec, dtype=float)
    time_channels = measurement.time_data.get("channels", {}) if measurement is not None else {}
    fft_channels = measurement.spectra.get("fft", {}) if measurement is not None else {}
    autospectrum_channels = (
        measurement.spectra.get("autospectrum", {}) if measurement is not None else {}
    )

    for index in range(channel_count):
        channel = channels[index] if index < len(channels) else None
        channel_name = channel.name if channel is not None else f"ai{index}"
        label = channel.label if channel is not None and channel.label else f"Channel {index + 1}"
        eu = channel.engineering_unit if channel is not None else "V"
        full_scale = float(channel.full_scale) if channel is not None else 10.0
        coupling = (channel.coupling if channel is not None else "ac").lower()
        per_eu = channel.per_eu_mode if channel is not None else "Off"
        sensitivity = float(channel.sensitivity) if channel is not None else 1.0
        db_reference = float(channel.db_reference) if channel is not None else 1.0
        offset = float(channel.offset) if channel is not None else 0.0
        enabled = bool(channel.enabled) if channel is not None else True

        vdlg1_s1[index, 0] = _nearest_legacy_full_scale_index(full_scale)
        vdlg1_s1[index, 1] = 1.0 if enabled else 0.0
        vdlg1_s1[index, 2] = _LEGACY_COUPLING_INDEX_BY_VALUE.get(coupling, 0)
        vdlg1_s1[index, 3] = int(round(db_reference))
        vdlg1_s1[index, 4] = _LEGACY_PER_EU_INDEX_BY_VALUE.get(per_eu, 0)
        vdlg1_s1[index, 5] = int(round(offset))
        vdlg1_s1[index, 6] = int(round(sensitivity))
        vdlg1_s2_values.append(_legacy_strpack(19, label, eu))
        if enabled:
            chan_label_values.append(_legacy_strpack(20, label))
            eu_label_values.append(_legacy_strpack(20, eu))
            chan_stat_rows.append(
                [index + 1, 1 if per_eu != "Off" else 0, sensitivity, 0.0, full_scale, db_reference]
            )

        scmeas[0, index]["label"] = label
        scmeas[0, index]["eu_string"] = eu
        scmeas[0, index]["fs_val"] = np.array([[full_scale]], dtype=float)
        scmeas[0, index]["db_ref"] = np.array([[db_reference]], dtype=float)
        scmeas[0, index]["euscale_fac"] = np.array([[sensitivity]], dtype=float)
        scmeas[0, index]["eu_val"] = np.array([[sensitivity]], dtype=float)
        scmeas[0, index]["eu_on_off"] = np.array([[1.0 if per_eu != "Off" else 0.0]], dtype=float)
        if enabled:
            scmeas[0, index]["tdmeas"] = _mat_column(
                _measurement_channel_values(time_channels, channel_name, label, empty_time),
                dtype=float,
            )
            scmeas[0, index]["fft"] = _mat_column(
                _measurement_channel_values(fft_channels, channel_name, label, empty_complex),
                dtype=complex,
            )
            scmeas[0, index]["aspec"] = _mat_column(
                _measurement_channel_values(
                    autospectrum_channels, channel_name, label, empty_power
                ),
                dtype=float,
            )
        else:
            scmeas[0, index]["tdmeas"] = np.empty((0, 0), dtype=float)
            scmeas[0, index]["fft"] = np.empty((0, 0), dtype=complex)
            scmeas[0, index]["aspec"] = np.empty((0, 0), dtype=float)

    for index in range(channel_count, max_legacy_channels):
        vdlg1_s1[index, 0] = 1
        vdlg1_s1[index, 1] = 0
        vdlg1_s1[index, 2] = 0
        vdlg1_s1[index, 3] = 1
        vdlg1_s1[index, 4] = 0
        vdlg1_s1[index, 5] = 0
        vdlg1_s1[index, 6] = 1
        vdlg1_s2_values.append(_legacy_strpack(19, f"Channel {index + 1}", "Gs"))

    chan_stat = (
        np.asarray(chan_stat_rows, dtype=float)
        if chan_stat_rows
        else np.empty((0, 6), dtype=float)
    )
    vdlg1_s2 = _legacy_char_matrix(vdlg1_s2_values[:max_legacy_channels], 19)
    chan_label = _legacy_char_matrix(chan_label_values, 20)
    eu_label = _legacy_char_matrix(eu_label_values, 20)

    reference_name = config.acquisition.reference_channel or "ai0"
    try:
        reference_index = int(reference_name[2:]) if reference_name.startswith("ai") else 0
    except ValueError:
        reference_index = 0
    response_names = list(config.acquisition.response_channels)
    if measurement is not None and measurement.frf:
        response_names = sorted(
            {
                key.split("->", 1)[1]
                for key in measurement.frf
                if "->" in key and key.split("->", 1)[0] == reference_name
            }
            or set(response_names)
        )
    response_indices: list[int] = []
    for response_name in response_names:
        try:
            response_index = int(response_name[2:]) if response_name.startswith("ai") else -1
        except ValueError:
            response_index = -1
        if 0 <= response_index < channel_count and response_index != reference_index:
            response_indices.append(response_index)

    resp_dtype = [("r", "O")]
    resp = np.empty((1, max_legacy_references), dtype=resp_dtype)
    for index in range(max_legacy_references):
        resp[0, index]["r"] = np.empty((1, 0), dtype=float)
    if 0 <= reference_index < max_legacy_references:
        resp[0, reference_index]["r"] = np.asarray(
            [[index + 1 for index in response_indices]], dtype=float
        )
    xcstate_dtype = [("resp", "O"), ("refc", "O"), ("clist", "O")]
    xcstate = np.empty((1, 1), dtype=xcstate_dtype)
    xcstate[0, 0]["resp"] = resp
    xcstate[0, 0]["refc"] = np.asarray([[reference_index + 1]], dtype=float)
    xcstate[0, 0]["clist"] = np.arange(1, channel_count + 1, dtype=float).reshape((1, -1))
    xcmeas_dtype = [
        ("xfer", "O"),
        ("coh", "O"),
        ("cspec", "O"),
        ("ccor", "O"),
        ("imp", "O"),
    ]
    xcmeas = np.empty((max_legacy_references, max_legacy_channels), dtype=xcmeas_dtype)
    for ref_index in range(max_legacy_references):
        for channel_index in range(max_legacy_channels):
            xcmeas[ref_index, channel_index]["xfer"] = np.empty((0, 0), dtype=complex)
            xcmeas[ref_index, channel_index]["coh"] = np.empty((0, 0), dtype=float)
            xcmeas[ref_index, channel_index]["cspec"] = np.empty((0, 0), dtype=complex)
            xcmeas[ref_index, channel_index]["ccor"] = np.empty((0, 0), dtype=float)
            xcmeas[ref_index, channel_index]["imp"] = np.empty((0, 0), dtype=float)
    frf = measurement.frf if measurement is not None else {}
    coherence = measurement.coherence if measurement is not None else {}
    cross_spectra = measurement.cross_spectra if measurement is not None else {}
    correlations = measurement.correlations if measurement is not None else {}
    impulse_responses = measurement.impulse_responses if measurement is not None else {}
    empty_impulse = np.zeros_like(tdxvec, dtype=float)
    for index in range(channel_count):
        key = f"{reference_name}->ai{index}"
        xcmeas[reference_index, index]["xfer"] = _mat_column(
            frf.get(key, np.array([], dtype=complex)),
            dtype=complex,
        )
        xcmeas[reference_index, index]["coh"] = _mat_column(
            coherence.get(key, np.array([], dtype=float)),
            dtype=float,
        )
        xcmeas[reference_index, index]["cspec"] = _mat_column(
            cross_spectra.get(key, np.array([], dtype=complex)),
            dtype=complex,
        )
        xcmeas[reference_index, index]["ccor"] = _mat_column(
            correlations.get(key, np.array([], dtype=float)),
            dtype=float,
        )
        xcmeas[reference_index, index]["imp"] = _mat_column(
            impulse_responses.get(
                key,
                empty_impulse if index in response_indices else np.array([], dtype=float),
            ),
            dtype=float,
        )

    units_dtype = [("val", "O"), ("str", "O")]
    units = np.empty((1, 1), dtype=units_dtype)
    units[0, 0]["val"] = np.array([[1.0]], dtype=float)
    units[0, 0]["str"] = "rms"
    filestor_state_values = [
        1 if measurement is not None and measurement.time_data.get("channels") else 0,
        1 if measurement is not None and measurement.spectra.get("autospectrum") else 0,
        1 if frf else 0,
        1 if coherence else 0,
        1 if cross_spectra else 0,
        1 if measurement is not None and measurement.correlations else 0,
        1 if measurement is not None and measurement.correlations else 0,
        1 if impulse_responses else 0,
        1 if measurement is not None and measurement.spectra.get("fft") else 0,
        0,
    ]
    filestor = {
        "label": _mat_cell(
            ["y(t)", "aspec", "xfer", "coh", "cspec", "acor", "ccor", "impulse", "fft", "displayed"],
        ),
        "state": _mat_cell(
            [np.array([[value]], dtype=np.uint8) for value in filestor_state_values],
            shape=(10, 1),
        ),
        "fields": _mat_cell(
            ["tdmeas", "aspec", "xfer", "coh", "cspec", "acor", "ccor", "imp", "fft"],
        ),
    }
    metadata = measurement.metadata if measurement is not None and isinstance(measurement.metadata, dict) else {}
    legacy_config = metadata.get("legacy_config_state", {}) if isinstance(metadata, dict) else {}
    sample_index, legacy_sample_rate, legacy_bandwidth = _legacy_sample_index_for_rate(sample_rate)
    hdlg1_previous = legacy_config.get("hdlg1_s1", []) if isinstance(legacy_config, dict) else []
    hdlg1_s1 = np.array(
        [[
            sample_index,
            int(round(hdlg1_previous[1])) if len(hdlg1_previous) >= 2 else 0,
            frame_size,
            int(round(hdlg1_previous[3])) if len(hdlg1_previous) >= 4 else 10000,
            1 if config.acquisition.anti_alias_filters_enabled else 0,
            _LEGACY_USB4431_SYSTEM_CLOCK,
            int(round(hdlg1_previous[6])) if len(hdlg1_previous) >= 7 else sample_index,
            sample_index,
        ]],
        dtype=np.uint16,
    )
    avg_mode_index = _LEGACY_AVERAGE_MODE_INDEX_BY_VALUE.get(
        config.acquisition.averaging.mode, 1
    )
    window_index = _LEGACY_PROCESSING_WINDOW_INDEX_BY_VALUE.get(
        config.acquisition.processing_window, 1
    )
    overlap_index = _LEGACY_OVERLAP_INDEX_BY_PERCENT.get(
        int(config.acquisition.overlap_percent), 1
    )
    if config.acquisition.modal.reject_double_hit and config.acquisition.modal.reject_overload:
        reject_index = 4
    elif config.acquisition.modal.reject_double_hit:
        reject_index = 3
    elif config.acquisition.modal.reject_overload:
        reject_index = 2
    else:
        reject_index = 1
    vdlg2_s1 = np.array(
        [[
            avg_mode_index,
            max(1, int(config.acquisition.averaging.count)),
            float(np.clip(1.0 - config.acquisition.averaging.exponential_alpha, 0.0, 1.0)),
            1.0,
            window_index,
            overlap_index,
            reject_index,
            0.0,
        ]],
        dtype=float,
    )
    trigger = config.acquisition.trigger
    trigger_mode_index = _LEGACY_TRIGGER_MODE_INDEX_BY_VALUE.get(trigger.mode, 1)
    if not trigger.enabled:
        trigger_mode_index = 1
    hdlg2_s1 = np.array(
        [[
            _legacy_trigger_source_index(
                trigger.source if trigger.enabled else config.acquisition.reference_channel,
                channel_count,
                1,
            ),
            0,
            int(trigger.pretrigger_samples),
            _legacy_trigger_index_for_percent(trigger.level_percent),
            1 if trigger.slope == "falling" else 0,
            0,
            trigger_mode_index,
            channel_count,
            1,
        ]],
        dtype=np.int16,
    )
    excitation = config.acquisition.excitation
    linked_excitation = bool(excitation.enabled)
    hdlg2_vis = "off" if linked_excitation else "on"
    exdlg2_vis = "on" if linked_excitation else "off"
    exdlg2_s1 = np.array(
        [[
            float(excitation.amplitude),
            float(excitation.offset),
            1.0 if excitation.mode == "chirp" else 2.0,
            1.0 if excitation.enabled else 0.0,
        ]],
        dtype=float,
    )
    modal = {
        "dblpcnt": np.array([[config.acquisition.modal.double_hit_threshold * 100.0]], dtype=float),
        "dbldelay": np.array([[config.acquisition.modal.double_hit_delay_fraction * 100.0]], dtype=float),
        "forcewin": np.array([[config.acquisition.modal.force_window_fraction * 100.0]], dtype=float),
        "expdecay": np.array([[config.acquisition.modal.exponential_decay_fraction * 100.0]], dtype=float),
    }
    rbw_hz = (
        float(np.asarray(fdxvec, dtype=float)[1] - np.asarray(fdxvec, dtype=float)[0])
        if np.asarray(fdxvec).size > 1
        else sample_rate / max(np.asarray(tdxvec).size, 1)
    )
    measured_average_count = (
        int(metadata.get("legacy_measured_average_count", 0))
        if isinstance(metadata, dict)
        else 0
    )
    if measured_average_count <= 0:
        measured_average_count = max(1, int(config.acquisition.averaging.count))
    slm = {
        "fdxvec": np.ravel(fdxvec).reshape((1, -1)),
        "tdxvec": np.ravel(tdxvec).reshape((1, -1)),
        "clist": np.arange(1, channel_count + 1, dtype=float).reshape((1, -1)),
        "numin": np.array([[channel_count]], dtype=float),
        "navg": np.array([[measured_average_count]], dtype=float),
        "units": units,
        # Original VNA files commonly persist 1 here; plot_vna refreshes the
        # runtime correction from vdlg2_s1(winsel) after loading.
        "wincor": np.array([[1.0]], dtype=float),
        "winsel": np.array([[window_index]], dtype=float),
        "rbw": np.array([[rbw_hz]], dtype=float),
        "zpad": np.array([[0.0]], dtype=float),
        "ovld": np.array([[0.0]], dtype=float),
        "zoomcf": np.array([[0.0]], dtype=float),
        "filestor": filestor,
        "scmeas": scmeas,
        "xcstate": xcstate,
        "xcmeas": xcmeas,
        "modal": modal,
    }
    system_clock = _LEGACY_USB4431_SYSTEM_CLOCK
    xplot_dtype = [
        ("ylcb", "O"),
        ("ypu1sel", "O"),
        ("ypu2sel", "O"),
        ("xpu1sel", "O"),
        ("yintfac", "O"),
        ("xcref", "O"),
        ("yapcor", "O"),
        ("xchanv", "O"),
        ("plot_mode", "O"),
        ("xpu2sel", "O"),
        ("xc_rmax", "O"),
        ("xc_cmax", "O"),
    ]
    xchanv_dtype = [("xc_ckstate", "O")]
    xplot_s1 = np.empty((1, 2), dtype=xplot_dtype)
    xc_ckstate = np.zeros((max_legacy_references, max_legacy_channels), dtype=float)
    if 0 <= reference_index < max_legacy_references:
        xc_ckstate[reference_index, reference_index] = 1
        for response_index in response_indices:
            xc_ckstate[reference_index, response_index] = 1
    legacy_display_state = (
        metadata.get("legacy_display_state", {})
        if isinstance(metadata, dict)
        else {}
    )
    if not isinstance(legacy_display_state, dict):
        legacy_display_state = {}
    alias_to_index, _channel_trace_names = _legacy_channel_alias_maps(channels)
    relation_modes = {
        "frf",
        "coherence",
        "cross_spectrum",
        "cross_correlation",
        "impulse_response",
    }
    for panel_index in range(2):
        panel_key = "top" if panel_index == 0 else "bottom"
        panel_state = legacy_display_state.get(panel_key, {})
        if not isinstance(panel_state, dict):
            panel_state = {}
        default_mode = "time" if panel_index == 0 else ("frf" if frf else "time")
        mode = str(panel_state.get("mode") or default_mode)
        mode_index = int(
            panel_state.get(
                "legacy_mode_index",
                _LEGACY_DISPLAY_INDEX_BY_MODE.get(mode, 1),
            )
        )
        mode = _LEGACY_DISPLAY_MODE_BY_INDEX.get(mode_index, mode)
        value_index = int(
            panel_state.get(
                "legacy_value_mode_index",
                _LEGACY_VALUE_INDEX_BY_DISPLAY_MODE.get(mode, {}).get(
                    str(panel_state.get("value_mode", "")),
                    1,
                ),
            )
        )
        xscale = str(panel_state.get("xscale", "linear")).lower()
        xscale_index = 2 if (
            xscale == "log"
            and mode in {"autospectrum", "frf", "coherence", "cross_spectrum"}
        ) else 1
        trace_names = panel_state.get("trace_names", [])
        y_channel_flags = np.zeros((1, channel_count), dtype=float)
        if isinstance(trace_names, list) and trace_names:
            for trace_name in trace_names:
                channel_index = _legacy_channel_index_from_trace(
                    trace_name,
                    alias_to_index,
                    relation_endpoint="response" if mode in relation_modes else "channel",
                )
                if channel_index is not None and 0 <= channel_index < channel_count:
                    y_channel_flags[0, channel_index] = 1
        elif channel_count:
            y_channel_flags[0, min(panel_index, channel_count - 1)] = 1
        panel_reference = str(panel_state.get("reference_channel") or reference_name)
        panel_reference_index = _legacy_channel_index_from_trace(
            panel_reference,
            alias_to_index,
            relation_endpoint="reference",
        )
        if panel_reference_index is None:
            panel_reference_index = reference_index
        xchanv = np.empty((1, 1), dtype=xchanv_dtype)
        xchanv[0, 0]["xc_ckstate"] = xc_ckstate if panel_index == 0 else np.empty((0, 0), dtype=np.uint8)
        xplot_s1[0, panel_index]["ylcb"] = y_channel_flags
        xplot_s1[0, panel_index]["ypu1sel"] = np.array([[mode_index]], dtype=float)
        xplot_s1[0, panel_index]["ypu2sel"] = np.array([[value_index]], dtype=float)
        xplot_s1[0, panel_index]["xpu1sel"] = np.array([[xscale_index]], dtype=float)
        xplot_s1[0, panel_index]["yintfac"] = np.array(
            [[int(panel_state.get("legacy_yintfac_index", 1))]], dtype=float
        )
        xplot_s1[0, panel_index]["xcref"] = np.array([[panel_reference_index + 1]], dtype=float)
        xplot_s1[0, panel_index]["yapcor"] = np.array(
            [[int(panel_state.get("legacy_yapcor_index", 1))]], dtype=float
        )
        xplot_s1[0, panel_index]["xchanv"] = xchanv
        xplot_s1[0, panel_index]["plot_mode"] = (
            np.array([[1 if legacy_display_state.get("layout") == "single" else 2]], dtype=float)
            if panel_index == 0
            else np.empty((0, 0), dtype=np.uint8)
        )
        xplot_s1[0, panel_index]["xpu2sel"] = np.array(
            [[int(panel_state.get("legacy_x_unit_index", 1))]], dtype=float
        )
        xplot_s1[0, panel_index]["xc_rmax"] = (
            np.array([[max_legacy_references]], dtype=float)
            if panel_index == 0
            else np.empty((1, 0), dtype=float)
        )
        xplot_s1[0, panel_index]["xc_cmax"] = (
            np.array([[max_legacy_channels]], dtype=float)
            if panel_index == 0
            else np.empty((1, 0), dtype=float)
        )
    xplot_axes = _legacy_xplot_axes(
        tdxvec,
        fdxvec,
        time_channels,
        channels,
        channel_count,
        legacy_sample_rate,
        measurement=measurement,
        display_state=legacy_display_state,
    )
    savemat(
        destination,
        {
            "key": "DSPt vna_2 file",
            "SampleRate": np.array([[round(legacy_sample_rate)]], dtype=np.uint16),
            "CenterFreq": np.array([[0]], dtype=np.uint8),
            "num_io": np.array([[channel_count, 1]], dtype=np.uint8),
            "SystemClk": np.array([[system_clock]], dtype=np.uint16),
            "UniformFlg": np.array([[1]], dtype=np.uint8),
            "ch_ptr": np.array([[1]], dtype=np.uint8),
            "grids": "off",
            "hdlg1_s1": hdlg1_s1,
            "hdlg2_s1": hdlg2_s1,
            "hdlg2_vis": hdlg2_vis,
            "exdlg2_s1": exdlg2_s1,
            "exdlg2_vis": exdlg2_vis,
            "vdlg2_s1": vdlg2_s1,
            "xplot_s1": xplot_s1,
            "xplot_s2": np.array([[252, 11, 640, 436]], dtype=float),
            "xplot_axes": xplot_axes,
            "vna_pos": np.array([[4, 61, 241, 386]], dtype=float),
            "vi_timestamp": np.array([[1998, 1, 1, 0, 0, 0]], dtype=float),
            "Cmprssd_Notes": config.notes or "Enter your notes here.",
            "vdlg1_s1": vdlg1_s1,
            "vdlg1_s2": vdlg1_s2,
            "ChanStat": chan_stat,
            "ChanLabel": chan_label,
            "EULabel": eu_label,
            "SLm": slm,
        },
        do_compression=False,
    )
    return destination


def load_session_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_saved_session_json(path: str | Path) -> SavedSession:
    from python_vna.models import (
        AcquisitionConfig,
        AveragingConfig,
        ChannelConfig,
        ExcitationConfig,
        MeasurementSet,
        ModalProcessingConfig,
        SavedSession,
        SessionConfig,
        TriggerConfig,
    )

    payload = load_session_json(path)
    config_payload = payload["config"]
    channels = [
        _construct_dataclass(ChannelConfig, channel) for channel in config_payload["ai_channels"]
    ]
    acquisition_payload = config_payload["acquisition"]
    trigger = _construct_dataclass(TriggerConfig, acquisition_payload["trigger"])
    averaging = _construct_dataclass(AveragingConfig, acquisition_payload["averaging"])
    excitation = _construct_dataclass(ExcitationConfig, acquisition_payload["excitation"])
    modal = _construct_dataclass(
        ModalProcessingConfig, acquisition_payload.get("modal", {})
    )
    acquisition = AcquisitionConfig(
        sample_rate=acquisition_payload["sample_rate"],
        frame_size=acquisition_payload["frame_size"],
        bandwidth_hz=acquisition_payload.get("bandwidth_hz", 1000.0),
        anti_alias_filters_enabled=acquisition_payload.get("anti_alias_filters_enabled", True),
        processing_window=acquisition_payload.get("processing_window", "boxcar"),
        overlap_percent=acquisition_payload.get("overlap_percent", 0),
        buffer_frames=acquisition_payload["buffer_frames"],
        display_channels=acquisition_payload["display_channels"],
        overlay_enabled=acquisition_payload.get("overlay_enabled", False),
        reference_channel=acquisition_payload.get("reference_channel", "ai0"),
        response_channels=acquisition_payload.get("response_channels", ["ai1", "ai2", "ai3"]),
        trigger=trigger,
        averaging=averaging,
        excitation=excitation,
        modal=modal,
    )
    session = SessionConfig(
        title=config_payload["title"],
        notes=config_payload["notes"],
        ai_channels=channels,
        ao_channel=config_payload.get("ao_channel"),
        acquisition=acquisition,
    )
    measurement_payload = payload.get("measurement")
    measurement = None
    if measurement_payload is not None:
        measurement = MeasurementSet(
            sample_rate=measurement_payload["sample_rate"],
            time_data=measurement_payload["time_data"],
            spectra=measurement_payload["spectra"],
            frf=measurement_payload["frf"],
            coherence=measurement_payload["coherence"],
            cross_spectra=measurement_payload["cross_spectra"],
            correlations=measurement_payload["correlations"],
            impulse_responses=measurement_payload["impulse_responses"],
            metadata=measurement_payload.get("metadata", {}),
        )
    return SavedSession(config=session, measurement=measurement, source_path=Path(path))


def save_measurement_npz(session: SavedSession, path: str | Path) -> Path:
    if session.measurement is None:
        raise ValueError("Cannot export NPZ without measurement data.")
    destination = Path(path)
    payload: dict[str, Any] = {
        "sample_rate": session.measurement.sample_rate,
        "time_t": np.asarray(session.measurement.time_data["t"]),
    }
    for channel_name, values in session.measurement.time_data["channels"].items():
        payload[f"time_{channel_name}"] = np.asarray(values)
    for channel_name, values in session.measurement.spectra["fft"].items():
        payload[f"fft_{channel_name}"] = np.asarray(values)
    for key, values in session.measurement.frf.items():
        payload[f"frf_{key}"] = np.asarray(values)
    np.savez(destination, **payload)
    return destination


def save_measurement_csv(session: SavedSession, path: str | Path) -> Path:
    if session.measurement is None:
        raise ValueError("Cannot export CSV without measurement data.")
    destination = Path(path)
    time_data = session.measurement.time_data
    channels = time_data["channels"]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["time_seconds", *channels.keys()]
        writer.writerow(header)
        rows = zip(time_data["t"], *channels.values())
        writer.writerows(rows)
    return destination


def save_measurement_hdf5(session: SavedSession, path: str | Path) -> Path:
    if session.measurement is None:
        raise ValueError("Cannot export HDF5 without measurement data.")
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Missing optional dependency 'h5py'.") from exc

    destination = Path(path)
    with h5py.File(destination, "w") as handle:
        handle.attrs["sample_rate"] = session.measurement.sample_rate
        time_group = handle.create_group("time_data")
        time_group.create_dataset("t", data=session.measurement.time_data["t"])
        for name, values in session.measurement.time_data["channels"].items():
            time_group.create_dataset(name, data=values)

        spectra_group = handle.create_group("spectra")
        spectra_group.create_dataset("f", data=session.measurement.spectra["f"])
        fft_group = spectra_group.create_group("fft")
        autospectrum_group = spectra_group.create_group("autospectrum")
        for name, values in session.measurement.spectra["fft"].items():
            fft_group.create_dataset(name, data=values)
        for name, values in session.measurement.spectra["autospectrum"].items():
            autospectrum_group.create_dataset(name, data=values)

        frf_group = handle.create_group("frf")
        for name, values in session.measurement.frf.items():
            frf_group.create_dataset(name, data=values)
    return destination


def default_session_config() -> SessionConfig:
    from python_vna.models import ChannelConfig

    channels = [
        ChannelConfig(
            name=f"ai{i}",
            physical_name=f"ai{i}",
            label=f"Ch {i + 1}",
            is_reference=(i == 0),
            coupling="ac" if i == 0 else "bias",
            offset=0.0,
            engineering_unit="m/s^2",
            sensitivity=1.0,
            per_eu_mode="/Volt",
            db_reference=1.0,
            iepe_enabled=(i > 0),
            iepe_current_ma=2.1,
        )
        for i in range(4)
    ]
    return SessionConfig(
        title="USB-4431 Default Session",
        ai_channels=channels,
        ao_channel="ao0",
    )


def load_legacy_vna(path: str | Path) -> SavedSession:
    from scipy.io import loadmat

    from python_vna.models import (
        AcquisitionConfig,
        AveragingConfig,
        ChannelConfig,
        ExcitationConfig,
        MeasurementSet,
        ModalProcessingConfig,
        SessionConfig,
        TriggerConfig,
    )

    mat = loadmat(path, squeeze_me=False, struct_as_record=False)
    sample_rate = float(np.squeeze(mat.get("SampleRate", [[10240.0]])).item())
    center_freq = float(np.squeeze(mat.get("CenterFreq", [[0.0]])).item())
    slm = mat.get("SLm")
    if slm is None:
        raise ValueError("Legacy VNA file does not contain SLm.")

    slm = slm[0, 0]
    fdxvec = np.squeeze(getattr(slm, "fdxvec", np.array([], dtype=float)))
    tdxvec = np.squeeze(getattr(slm, "tdxvec", np.array([], dtype=float)))
    clist = _as_int_list(getattr(slm, "clist", np.array([])))
    num_io = np.asarray(mat.get("num_io", np.empty((0,), dtype=int))).squeeze()
    if getattr(num_io, "size", 0):
        num_io_values = np.ravel(num_io)
        input_channel_count = int(num_io_values[0])
        output_channel_count = int(num_io_values[1]) if num_io_values.size > 1 else 0
    elif clist:
        input_channel_count = max(clist)
        output_channel_count = 0
    else:
        input_channel_count = 4
        output_channel_count = 0
    vdlg1_s1 = np.asarray(mat.get("vdlg1_s1", np.empty((0, 0))))
    vdlg1_s2 = np.ravel(np.asarray(mat.get("vdlg1_s2", np.empty((0,), dtype=object))))
    hdlg1_s1 = _legacy_row_values(mat.get("hdlg1_s1", np.empty((0, 0))))
    hdlg2_s1 = _legacy_row_values(mat.get("hdlg2_s1", np.empty((0, 0))))
    vdlg2_s1 = _legacy_row_values(mat.get("vdlg2_s1", np.empty((0, 0))))
    exdlg2_s1 = _legacy_row_values(mat.get("exdlg2_s1", np.empty((0, 0))))
    chan_stat = np.asarray(mat.get("ChanStat", np.empty((0, 0))))
    chan_label = np.ravel(np.asarray(mat.get("ChanLabel", np.empty((0,), dtype=object))))
    eu_label = np.ravel(np.asarray(mat.get("EULabel", np.empty((0,), dtype=object))))
    channels: list[ChannelConfig] = []
    channel_trace_names: list[str] = []
    legacy_channels: dict[str, dict[str, Any]] = {}
    time_channels: dict[str, np.ndarray] = {}
    fft_channels: dict[str, np.ndarray] = {}
    autospectrum_channels: dict[str, np.ndarray] = {}
    frf: dict[str, np.ndarray] = {}
    coherence: dict[str, np.ndarray] = {}
    cross_spectra: dict[str, np.ndarray] = {}
    impulse_responses: dict[str, np.ndarray] = {}
    scmeas = getattr(slm, "scmeas", None)
    if scmeas is not None:
        for channel_id in range(1, input_channel_count + 1):
            idx = channel_id - 1
            entry = _legacy_unwrap_cell(scmeas[0, idx])
            channel_name = f"ai{idx}"
            packed_label, packed_eu = (
                _legacy_packed_strings(vdlg1_s2[idx])
                if idx < vdlg1_s2.size
                else ("", "")
            )
            label_text = packed_label
            if not label_text and idx < chan_label.size:
                label_text = _legacy_string(chan_label[idx], "")
            if not label_text:
                label_text = _legacy_string(getattr(entry, "label", ""), channel_name)
            eu_text = packed_eu
            if not eu_text and idx < eu_label.size:
                eu_text = _legacy_string(eu_label[idx], "")
            if not eu_text:
                eu_text = _legacy_string(getattr(entry, "eu_string", ""), "V")
            channel_trace_names.append(label_text)
            full_scale = _legacy_scalar(getattr(entry, "fs_val", np.array([[10.0]])), 10.0)
            coupling = "ac"
            offset = 0.0
            db_reference = _legacy_scalar(getattr(entry, "db_ref", np.array([[1.0]])), 1.0)
            sensitivity = _legacy_scalar(getattr(entry, "euscale_fac", np.array([[1.0]])), 1.0)
            per_eu_mode = "/Volt"
            enabled = True
            if idx < vdlg1_s1.shape[0] and vdlg1_s1.shape[1] >= 7:
                full_scale = _LEGACY_FULL_SCALE_BY_INDEX.get(int(vdlg1_s1[idx, 0]), full_scale)
                enabled = bool(vdlg1_s1[idx, 1])
                coupling = _LEGACY_COUPLING_BY_INDEX.get(int(vdlg1_s1[idx, 2]), coupling)
                db_reference = float(vdlg1_s1[idx, 3])
                per_eu_mode = _LEGACY_PER_EU_BY_INDEX.get(int(vdlg1_s1[idx, 4]), "Off")
                offset = float(vdlg1_s1[idx, 5])
                sensitivity = float(vdlg1_s1[idx, 6])
            elif idx < chan_stat.shape[0] and chan_stat.shape[1] >= 5:
                enabled = bool(chan_stat[idx, 1])
                sensitivity = float(chan_stat[idx, 2])
                full_scale = float(chan_stat[idx, 4])
                per_eu_mode = "/Volt" if enabled else "Off"
            channels.append(
                ChannelConfig(
                    name=channel_name,
                    physical_name=channel_name,
                    label=label_text,
                    enabled=enabled,
                    coupling=coupling,
                    is_reference=(idx == 0),
                    offset=offset,
                    iepe_enabled=(coupling == "bias"),
                    engineering_unit=eu_text,
                    sensitivity=sensitivity,
                    per_eu_mode=per_eu_mode,
                    db_reference=db_reference,
                    full_scale=full_scale,
                    min_value=-abs(full_scale) if full_scale > 0.0 else -10.0,
                    max_value=abs(full_scale) if full_scale > 0.0 else 10.0,
                )
            )
            legacy_channels[channel_name] = {
                "name": channel_name,
                "label": label_text,
                "euscale_fac": float(sensitivity),
                "db_ref": float(db_reference),
                "fs_val": float(full_scale),
                "eu_string": eu_text,
                "per_eu_mode": per_eu_mode,
            }
            tdmeas = np.squeeze(getattr(entry, "tdmeas", np.array([], dtype=float)))
            if tdmeas.size:
                time_channels[label_text] = tdmeas
            fft = np.squeeze(getattr(entry, "fft", np.array([], dtype=complex)))
            if fft.size:
                fft_channels[label_text] = fft
            aspec = np.squeeze(getattr(entry, "aspec", np.array([], dtype=float)))
            if aspec.size:
                autospectrum_channels[label_text] = aspec

    xcstate = getattr(slm, "xcstate", None)
    xcmeas = getattr(slm, "xcmeas", None)
    reference_names: list[str] = []
    response_names: list[str] = []
    hdlg_frame_size = int(round(hdlg1_s1[2])) if len(hdlg1_s1) >= 3 else 0
    frame_size = hdlg_frame_size if hdlg_frame_size > 0 else (int(tdxvec.size) if tdxvec.size else 4096)
    measured_average_count = _legacy_int(getattr(slm, "navg", np.array([[0]])), 0)
    units = getattr(slm, "units", None)
    if units is not None and getattr(units, "size", 0):
        units_entry = np.ravel(units)[0]
        legacy_units_value = _legacy_scalar(getattr(units_entry, "val", np.array([[1.0]])), 1.0)
        legacy_units_label = _legacy_string(getattr(units_entry, "str", ""), "rms")
    else:
        legacy_units_value = 1.0
        legacy_units_label = "rms"
    legacy_wincor = _legacy_scalar(getattr(slm, "wincor", np.array([[1.0]])), 1.0)
    legacy_winsel = _legacy_int(getattr(slm, "winsel", np.array([[1]])), 1)
    system_clock = _legacy_scalar(mat.get("SystemClk", np.array([[sample_rate]])), sample_rate)
    hdlg_sample_rate, hdlg_bandwidth = _legacy_sample_rate_from_hdlg(
        hdlg1_s1,
        system_clock,
        sample_rate,
    )
    if sample_rate <= 0.0:
        sample_rate = hdlg_sample_rate
    if frame_size <= 0:
        frame_size = hdlg_frame_size
    avg_mode_index = int(round(vdlg2_s1[0])) if len(vdlg2_s1) >= 1 else 1
    vdlg_count = int(round(vdlg2_s1[1])) if len(vdlg2_s1) >= 2 else 0
    average_count = max(1, vdlg_count or measured_average_count or 1)
    exponential_lambda = float(vdlg2_s1[2]) if len(vdlg2_s1) >= 3 else 0.637
    window_index = int(round(vdlg2_s1[4])) if len(vdlg2_s1) >= 5 else legacy_winsel
    overlap_index = int(round(vdlg2_s1[5])) if len(vdlg2_s1) >= 6 else 1
    reject_index = int(round(vdlg2_s1[6])) if len(vdlg2_s1) >= 7 else 1
    zpad_enabled = bool(round(vdlg2_s1[7])) if len(vdlg2_s1) >= 8 else bool(
        _legacy_int(getattr(slm, "zpad", np.array([[0]])), 0)
    )

    trigger_mode_index = int(round(hdlg2_s1[6])) if len(hdlg2_s1) >= 7 else 1
    trigger_source_index = int(round(hdlg2_s1[0])) if len(hdlg2_s1) >= 1 else 1
    trigger_delay_percent = float(hdlg2_s1[2]) if len(hdlg2_s1) >= 3 else 0.0
    trigger_threshold_index = int(round(hdlg2_s1[3])) if len(hdlg2_s1) >= 4 else 9
    trigger_slope = "falling" if len(hdlg2_s1) >= 5 and int(round(hdlg2_s1[4])) == 1 else "rising"
    trigger_mode = _LEGACY_TRIGGER_MODE_BY_INDEX.get(
        trigger_mode_index, "Off (Free Run)"
    )
    trigger_source = _legacy_trigger_source(
        trigger_source_index,
        input_channel_count,
        output_channel_count,
    )
    trigger_percent = _legacy_trigger_percent(trigger_threshold_index)
    trigger_full_scale = 10.0
    if trigger_source.startswith("ai"):
        try:
            trigger_channel = int(trigger_source[2:])
        except ValueError:
            trigger_channel = 0
        if 0 <= trigger_channel < len(channels):
            trigger_full_scale = abs(float(channels[trigger_channel].full_scale)) or 10.0
    trigger_enabled = trigger_mode != "Off (Free Run)" and trigger_source != "immediate"
    # The legacy UI stores trigger delay as percent of frame length. Keep that
    # value for the Python UI, and let the NI backend translate negative delay
    # into DAQmx pretrigger samples at run time.
    trigger_delay_setting = int(round(trigger_delay_percent))

    modal_raw = getattr(slm, "modal", None)
    modal = ModalProcessingConfig()
    modal.force_window_enabled = window_index in {17, 18, 19}
    modal.exponential_window_enabled = window_index in {15, 16, 17, 18, 19}
    if reject_index in {3, 4}:
        modal.reject_double_hit = True
    if reject_index in {0, 2, 4}:
        modal.reject_overload = True
    if modal_raw is not None and getattr(modal_raw, "size", 0):
        modal_entry = _legacy_unwrap_cell(np.ravel(modal_raw)[0])
        modal.force_window_fraction = _legacy_scalar(
            getattr(modal_entry, "forcewin", np.array([[20.0]])), 20.0
        ) / 100.0
        modal.exponential_decay_fraction = _legacy_scalar(
            getattr(modal_entry, "expdecay", np.array([[10.0]])), 10.0
        ) / 100.0
        modal.double_hit_threshold = _legacy_scalar(
            getattr(modal_entry, "dblpcnt", np.array([[50.0]])), 50.0
        ) / 100.0
        modal.double_hit_delay_fraction = _legacy_scalar(
            getattr(modal_entry, "dbldelay", np.array([[20.0]])), 20.0
        ) / 100.0
    modal.enabled = (
        modal.force_window_enabled
        or modal.exponential_window_enabled
        or modal.reject_double_hit
        or modal.reject_overload
    )

    excitation = ExcitationConfig()
    if exdlg2_s1:
        excitation.amplitude = float(exdlg2_s1[0]) if len(exdlg2_s1) >= 1 else excitation.amplitude
        excitation.offset = float(exdlg2_s1[1]) if len(exdlg2_s1) >= 2 else excitation.offset
        excitation.mode = "chirp" if int(round(exdlg2_s1[2] if len(exdlg2_s1) >= 3 else 1)) == 1 else "random"
        excitation.enabled = bool(round(exdlg2_s1[3])) if len(exdlg2_s1) >= 4 else False

    acquisition = AcquisitionConfig(
        sample_rate=sample_rate,
        frame_size=frame_size,
        bandwidth_hz=hdlg_bandwidth,
        anti_alias_filters_enabled=bool(int(round(hdlg1_s1[4]))) if len(hdlg1_s1) >= 5 else True,
        processing_window=_LEGACY_PROCESSING_WINDOW_BY_INDEX.get(window_index, "boxcar"),
        overlap_percent=_LEGACY_OVERLAP_PERCENT_BY_INDEX.get(overlap_index, 0),
        trigger=TriggerConfig(
            enabled=trigger_enabled,
            mode=trigger_mode,
            source=trigger_source if trigger_enabled else "immediate",
            level=trigger_full_scale * trigger_percent / 100.0,
            level_percent=trigger_percent,
            slope=trigger_slope,
            pretrigger_samples=trigger_delay_setting,
        ),
        averaging=AveragingConfig(
            mode=_LEGACY_AVERAGE_MODE_BY_INDEX.get(avg_mode_index, "linear"),
            count=average_count,
            exponential_alpha=float(np.clip(1.0 - exponential_lambda, 0.0, 1.0)),
            peak_hold=avg_mode_index == 3,
        ),
        excitation=excitation,
        modal=modal,
    )
    if xcstate is not None and xcmeas is not None:
        xs = _legacy_unwrap_cell(xcstate[0, 0])
        refc = _as_int_list(getattr(xs, "refc", np.array([])))
        reference_names = [f"ai{ref - 1}" for ref in refc]
        if reference_names:
            acquisition.reference_channel = reference_names[0]
        resp_container = getattr(xs, "resp", None)
        for ref_idx, ref_channel in enumerate(refc):
            if resp_container is None or ref_idx >= resp_container.shape[1]:
                continue
            resp_entry = _legacy_unwrap_cell(resp_container[0, ref_idx])
            responses = _as_int_list(getattr(resp_entry, "r", np.array([])))
            for resp_channel in responses:
                key = f"ai{ref_channel - 1}->ai{resp_channel - 1}"
                response_names.append(f"ai{resp_channel - 1}")
                cell = _legacy_unwrap_cell(xcmeas[ref_idx, resp_channel - 1])
                xfer = np.squeeze(getattr(cell, "xfer", np.array([], dtype=complex)))
                coh = np.squeeze(getattr(cell, "coh", np.array([], dtype=float)))
                cspec = np.squeeze(getattr(cell, "cspec", np.array([], dtype=complex)))
                imp = np.squeeze(getattr(cell, "imp", np.array([], dtype=float)))
                if xfer.size:
                    frf[key] = xfer
                if coh.size:
                    coherence[key] = coh
                if cspec.size:
                    cross_spectra[key] = cspec
                if imp.size:
                    impulse_responses[key] = imp

    if reference_names:
        acquisition.reference_channel = reference_names[0]
    if response_names:
        acquisition.response_channels = sorted(set(response_names))
    legacy_display_state = _parse_legacy_display_state(
        mat,
        input_channel_count,
        channel_trace_names,
        reference_names,
        sorted(set(response_names)),
    )
    session = SessionConfig(
        title=Path(path).stem,
        notes=f"Imported legacy VNA file: {Path(path).name}",
        ai_channels=channels,
        acquisition=acquisition,
    )
    measurement = MeasurementSet(
        sample_rate=sample_rate,
        time_data={"t": tdxvec, "channels": time_channels},
        spectra={"f": fdxvec, "fft": fft_channels, "autospectrum": autospectrum_channels},
        frf=frf,
        coherence=coherence,
        cross_spectra=cross_spectra,
        correlations={},
        impulse_responses=impulse_responses,
        metadata={
            "source": "legacy_vna",
            "center_freq": center_freq,
            "rbw_hz": float(fdxvec[1] - fdxvec[0]) if fdxvec.size > 1 else 0.0,
            "legacy_channels": legacy_channels,
            "legacy_units_value": float(legacy_units_value),
            "legacy_units_label": legacy_units_label,
            "legacy_wincor": float(legacy_wincor),
            "legacy_runtime_wincor": _legacy_window_power_correction(window_index),
            "legacy_winsel": int(legacy_winsel),
            "legacy_measured_average_count": int(measured_average_count),
            "legacy_config_state": {
                "hdlg1_s1": hdlg1_s1,
                "hdlg2_s1": hdlg2_s1,
                "vdlg2_s1": vdlg2_s1,
                "exdlg2_s1": exdlg2_s1,
                "sample_rate_from_hdlg": float(hdlg_sample_rate),
                "bandwidth_from_hdlg": float(hdlg_bandwidth),
                "average_mode_index": int(avg_mode_index),
                "window_index": int(window_index),
                "overlap_index": int(overlap_index),
                "reject_index": int(reject_index),
                "zpad_enabled": zpad_enabled,
                "trigger_mode_index": int(trigger_mode_index),
                "trigger_source_index": int(trigger_source_index),
                "trigger_delay_percent": float(trigger_delay_percent),
                "trigger_threshold_index": int(trigger_threshold_index),
            },
            "reference_channels": reference_names,
            "response_channels": sorted(set(response_names)),
            "legacy_display_state": legacy_display_state,
        },
    )
    return SavedSession(config=session, measurement=measurement, source_path=Path(path))
