"""MAT v5 ``.idefigure`` compatibility for the SiDiMaT plot viewer.

The original application stores plot models in MATLAB v5 structures even
though the extension is ``.idefigure``.  The reader is intentionally tolerant
of SciPy's two struct representations and of files written by the C# MAT
writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

import numpy as np
from python_sidmat.measurement.mat_v5 import read_mat_v5, write_mat_v5

__all__ = [
    "FigureSeries",
    "FigureModel",
    "IdeFigure",
    "save_idefigure",
    "load_idefigure",
]

MEASUREMENT_TYPE = "IdeFigure"
VERSION = 2.0


@dataclass(slots=True)
class FigureSeries:
    title: str = ""
    x: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    y: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))


@dataclass(slots=True)
class FigureModel:
    title: str = ""
    series: list[FigureSeries] = field(default_factory=list)
    log_x: bool = False
    log_y: bool = False
    grid: str = "on"
    legend: bool = True
    x_title: str = ""
    y_title: str = ""
    x_prop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    y_prop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass(slots=True)
class IdeFigure:
    figure_title: str = ""
    figure_title_font_size: float = 12.0
    rows: int = 1
    columns: int = 1
    models: list[FigureModel] = field(default_factory=list)


def save_idefigure(figure: IdeFigure, path: str) -> None:
    """Write an :class:`IdeFigure` using the original top-level field names."""
    payload: dict[str, object] = {
        "MeasurementType": MEASUREMENT_TYPE,
        "Version": np.array([VERSION]),
        "FigureTitle": figure.figure_title,
        "FigureTitleFontSize": np.array([float(figure.figure_title_font_size)]),
        "RowNumber": np.array([int(figure.rows)], dtype=np.int32),
        "ColumnNumber": np.array([int(figure.columns)], dtype=np.int32),
    }
    for index, model in enumerate(figure.models):
        payload[f"Model{index}"] = _model_to_dict(model)
    write_mat_v5(path, payload)


def load_idefigure(path: str) -> IdeFigure:
    """Read a C# or Python ``.idefigure`` MAT v5 file."""
    data = read_mat_v5(path)
    measurement_type = _text(data.get("MeasurementType", ""))
    if measurement_type and measurement_type != MEASUREMENT_TYPE:
        raise ValueError(f"unsupported figure type: {measurement_type}")
    version = _number(data.get("Version", VERSION), VERSION)
    if version < 1.0 or version > VERSION:
        raise ValueError(f"unsupported .idefigure version: {version}")

    figure = IdeFigure(
        figure_title=_text(data.get("FigureTitle", "")),
        figure_title_font_size=_number(data.get("FigureTitleFontSize", 12.0), 12.0),
        rows=max(1, int(_number(data.get("RowNumber", 1), 1))),
        columns=max(1, int(_number(data.get("ColumnNumber", 1), 1))),
    )
    for name in _model_names(data):
        figure.models.append(_model_from_obj(data[name]))
    return figure


def _model_to_dict(model: FigureModel) -> dict[str, object]:
    series: dict[str, object] = {}
    # The C# writer names the first entry Serie1 (not Serie0).
    for index, item in enumerate(model.series, start=1):
        x = np.asarray(item.x, dtype=float).reshape(-1)
        y = np.asarray(item.y, dtype=float).reshape(-1)
        n = min(x.size, y.size)
        series[f"Serie{index}"] = {
            "Title": item.title,
            "Serie": np.vstack((x[:n], y[:n])) if n else np.empty((2, 0)),
        }
    return {
        "Version": np.array([VERSION]),
        "Title": model.title,
        "LogX": np.array([int(model.log_x)], dtype=np.int16),
        "LogY": np.array([int(model.log_y)], dtype=np.int16),
        "Grid": model.grid,
        "Legend": np.array([int(model.legend)], dtype=np.int16),
        "Xaxis": _axis_to_dict(model.x_title, model.x_prop),
        "Yaxis": _axis_to_dict(model.y_title, model.y_prop),
        "Annotations": {"Version": np.array([VERSION])},
        "Series": series,
    }


def _axis_to_dict(title: str, prop: tuple[float, float, float, float]) -> dict[str, object]:
    return {
        "Version": np.array([VERSION]),
        "Title": title,
        "TitleFontSize": np.array([10.0]),
        "Prop": np.asarray(prop, dtype=float).reshape(1, 4),
    }


def _model_names(data: dict[str, object]) -> list[str]:
    names = [key for key in data if re.fullmatch(r"Model\d+", key)]
    return sorted(names, key=lambda key: int(key[5:]))


def _model_from_obj(obj) -> FigureModel:
    xaxis = _get(obj, "Xaxis", None)
    yaxis = _get(obj, "Yaxis", None)
    model = FigureModel(
        title=_text(_get(obj, "Title", "")),
        log_x=bool(int(_number(_get(obj, "LogX", 0), 0))),
        log_y=bool(int(_number(_get(obj, "LogY", 0), 0))),
        grid=_text(_get(obj, "Grid", "off")) or "off",
        legend=bool(int(_number(_get(obj, "Legend", 0), 0))),
        x_title=_text(_get(xaxis, "Title", "")),
        y_title=_text(_get(yaxis, "Title", "")),
        x_prop=_prop(_get(xaxis, "Prop", None)),
        y_prop=_prop(_get(yaxis, "Prop", None)),
    )
    series_obj = _get(obj, "Series", None)
    for name in _struct_names(series_obj):
        entry = _get(series_obj, name, None)
        data = np.asarray(_get(entry, "Serie", np.empty((2, 0))), dtype=float)
        data = np.squeeze(data)
        if data.ndim == 1:
            if data.size == 0:
                data = np.empty((2, 0))
            elif data.size % 2 == 0:
                data = data.reshape(2, -1)
            else:
                continue
        if data.ndim != 2:
            continue
        if data.shape[0] != 2 and data.shape[1] == 2:
            data = data.T
        if data.shape[0] < 2:
            continue
        n = data.shape[1]
        model.series.append(
            FigureSeries(
                title=_text(_get(entry, "Title", "")),
                x=np.asarray(data[0, :n], dtype=float),
                y=np.asarray(data[1, :n], dtype=float),
            )
        )
    return model


def _prop(value) -> tuple[float, float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0, 0.0)
    arr = np.asarray(value, dtype=float).reshape(-1)
    values = [float(x) for x in arr[:4]]
    values.extend([0.0] * (4 - len(values)))
    return tuple(values[:4])


def _struct_names(value) -> list[str]:
    value = _unwrap(value)
    if isinstance(value, dict):
        names = list(value)
    elif hasattr(value, "_fieldnames"):
        names = list(value._fieldnames or [])
    elif isinstance(value, np.void) and value.dtype.names:
        names = list(value.dtype.names)
    else:
        names = []
    return sorted(
        names,
        key=lambda name: (0, int(name[5:])) if re.fullmatch(r"Serie\d+", name)
        else (1, name),
    )


def _get(obj, name: str, default):
    obj = _unwrap(obj)
    if isinstance(obj, dict):
        return obj.get(name, default)
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, np.void) and obj.dtype.names and name in obj.dtype.names:
        return obj[name]
    return default


def _unwrap(value):
    while isinstance(value, np.ndarray) and value.size == 1:
        value = value.flat[0]
    return value


def _number(value, default: float) -> float:
    value = _unwrap(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        arr = np.asarray(value).reshape(-1)
        try:
            return float(arr[0])
        except (IndexError, TypeError, ValueError):
            return default


def _text(value) -> str:
    value = _unwrap(value)
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    arr = np.asarray(value)
    if arr.size == 0:
        return ""
    if arr.dtype.kind in "US":
        if arr.dtype.kind == "S":
            return "".join(
                bytes(item).decode("utf-8", errors="replace") for item in arr.flat
            )
        return "".join(str(item) for item in arr.flat)
    try:
        return str(arr.flat[0])
    except Exception:
        return str(value)
