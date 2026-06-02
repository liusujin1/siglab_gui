from __future__ import annotations

import numpy as np


def place_legend_away_from_curves(
    plot,
    curves: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    log_x: bool = False,
    log_y: bool = False,
    default_offset: tuple[int, int] = (4, 2),
) -> str | None:
    legend = plot.plotItem.legend
    if legend is None or not getattr(legend, "items", None):
        return None
    if not curves:
        legend.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=default_offset)
        legend._vna_auto_corner = "top_left"
        return "top_left"

    x_range, y_range = plot.viewRange()
    x_span = max(abs(float(x_range[1]) - float(x_range[0])), 1e-12)
    y_span = max(abs(float(y_range[1]) - float(y_range[0])), 1e-12)
    normalized_x: list[np.ndarray] = []
    normalized_y: list[np.ndarray] = []
    for x_data, y_data in curves.values():
        x_arr, y_arr = _finite_aligned_xy(x_data, y_data)
        if log_x:
            keep = x_arr > 0.0
            x_arr = x_arr[keep]
            y_arr = y_arr[keep]
        if log_y:
            keep = y_arr > 0.0
            x_arr = x_arr[keep]
            y_arr = y_arr[keep]
        if x_arr.size == 0:
            continue
        if x_arr.size > 1200:
            indices = np.linspace(0, x_arr.size - 1, 1200).astype(int)
            x_arr = x_arr[indices]
            y_arr = y_arr[indices]
        plot_x = np.log10(x_arr) if log_x else x_arr
        plot_y = np.log10(y_arr) if log_y else y_arr
        nx = (plot_x - float(x_range[0])) / x_span
        ny = (plot_y - float(y_range[0])) / y_span
        visible = (
            np.isfinite(nx)
            & np.isfinite(ny)
            & (nx >= 0.0)
            & (nx <= 1.0)
            & (ny >= 0.0)
            & (ny <= 1.0)
        )
        if np.any(visible):
            normalized_x.append(nx[visible])
            normalized_y.append(ny[visible])
    if not normalized_x:
        legend.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=default_offset)
        legend._vna_auto_corner = "top_left"
        return "top_left"

    x_norm = np.concatenate(normalized_x)
    y_norm = np.concatenate(normalized_y)
    label_lengths = [len(str(label.text)) for _sample, label in legend.items]
    max_label_length = max(label_lengths) if label_lengths else 12
    width = min(0.44, max(0.24, 0.16 + 0.006 * max_label_length))
    height = min(0.46, max(0.16, 0.08 + 0.055 * len(legend.items)))
    x_left = x_norm <= width
    x_right = x_norm >= 1.0 - width
    y_top = y_norm >= 1.0 - height
    y_bottom = y_norm <= height
    corners = (
        ("top_left", (0, 0), (0, 0), default_offset, x_left & y_top),
        (
            "top_right",
            (1, 0),
            (1, 0),
            (-default_offset[0], default_offset[1]),
            x_right & y_top,
        ),
        (
            "bottom_left",
            (0, 1),
            (0, 1),
            (default_offset[0], -default_offset[1]),
            x_left & y_bottom,
        ),
        (
            "bottom_right",
            (1, 1),
            (1, 1),
            (-default_offset[0], -default_offset[1]),
            x_right & y_bottom,
        ),
    )
    name, item_pos, parent_pos, offset, _mask = min(
        corners,
        key=lambda corner: int(np.count_nonzero(corner[4])),
    )
    legend.anchor(itemPos=item_pos, parentPos=parent_pos, offset=offset)
    legend._vna_auto_corner = name
    return name


def _finite_aligned_xy(
    x_data: np.ndarray,
    y_data: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    x_arr = np.asarray(x_data, dtype=float).ravel()
    y_arr = np.asarray(y_data, dtype=float).ravel()
    point_count = min(x_arr.size, y_arr.size)
    if point_count == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    x_arr = x_arr[:point_count]
    y_arr = y_arr[:point_count]
    keep = np.isfinite(x_arr) & np.isfinite(y_arr)
    return x_arr[keep], y_arr[keep]
