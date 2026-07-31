from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

from python_vna.analysis_algorithms import compute_hann_periodogram_psd
from python_vna.analysis_data import AnalysisDataset, load_analysis_path
from python_vna.diagnostic.data import (
    CurvePair,
    TraceAnalysisFile,
    VibrationAnalysisFile,
    curve_pairs_from_table,
    load_trace_analysis_file,
    load_vibration_analysis_file,
)
from python_vna.diagnostics import append_log
from python_vna.optional import require
from python_vna.ui.main_window import (
    DataTipPoint,
    DataTipText,
    VnaAxisItem,
    VnaViewBox,
    _apply_text_item_style,
    _cursor_palette_for_background,
    _data_tip_anchor_for_label_drag,
)
from python_vna.ui.legend_placement import place_legend_away_from_curves
from python_vna.ui.diagnostic_theme import (
    LIGHT_TRACE_COLORS,
    apply_plot_legend_theme,
    color_for_trace_name,
    color_map_for_trace_names,
    set_button_role as shared_set_button_role,
    trace_colors_for_theme,
)

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
pg = require("pyqtgraph", "python -m pip install -e .[gui]")
gl = require("pyqtgraph.opengl", "python -m pip install -e .[gui]")


class RotatableProjectionPlot(pg.PlotWidget):
    rotationDragged = QtCore.Signal(float, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_drag_pos: QtCore.QPointF | None = None
        self.setToolTip("按住左键拖动可旋转 3D 预览视角")

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == QtCore.Qt.LeftButton:
            self._last_drag_pos = self._event_position(event)
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        if self._last_drag_pos is not None:
            current = self._event_position(event)
            delta = current - self._last_drag_pos
            self._last_drag_pos = current
            self.rotationDragged.emit(float(delta.x()) * 0.45, -float(delta.y()) * 0.35)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if self._last_drag_pos is not None and event.button() == QtCore.Qt.LeftButton:
            self._last_drag_pos = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @staticmethod
    def _event_position(event) -> QtCore.QPointF:
        if hasattr(event, "position"):
            return event.position()
        return QtCore.QPointF(event.pos())


class Modal3DView(gl.GLViewWidget):
    cameraChanged = QtCore.Signal(float, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._render_items: list[object] = []
        self._label_font = QtGui.QFont("Microsoft YaHei UI", 9)
        self._has_camera_fit = False
        self._scene_auto_distance = 10.0
        self.setBackgroundColor("w")
        self.setCameraPosition(distance=10.0, elevation=24.0, azimuth=35.0)
        self.opts["fov"] = 55
        self.setToolTip("按住左键拖动可自由旋转 3D 预览")

    def clear(self) -> None:
        for item in list(self._render_items):
            try:
                self.removeItem(item)
            except Exception:
                pass
        self._render_items.clear()

    def add_render_item(self, item: object) -> None:
        self.addItem(item)
        self._render_items.append(item)

    def set_view_angles(self, azimuth: float, elevation: float) -> None:
        self.setCameraPosition(azimuth=float(azimuth), elevation=float(elevation))

    def reset_camera_fit(self) -> None:
        self._has_camera_fit = False

    def render_structure(
        self,
        coords: np.ndarray,
        labels: list[str],
        line_rows: list[dict[str, object]],
        *,
        deformed: np.ndarray | None,
        disp_complex: np.ndarray | None = None,
        show_labels: bool = True,
        azimuth: float,
        elevation: float,
    ) -> None:
        self.clear()
        base = np.asarray(coords, dtype=float)
        if base.ndim != 2 or base.shape[0] == 0 or base.shape[1] < 3:
            return
        target = np.asarray(deformed, dtype=float) if deformed is not None else None
        if target is not None and (target.ndim != 2 or target.shape[0] != base.shape[0] or target.shape[1] < 3):
            target = None
        finite_points = base[:, :3][np.all(np.isfinite(base[:, :3]), axis=1)]
        if finite_points.size:
            center = np.nanmean(finite_points, axis=0)
            mins = np.nanmin(finite_points, axis=0)
            maxs = np.nanmax(finite_points, axis=0)
        else:
            center = np.zeros(3, dtype=float)
            mins = np.array([-1.0, -1.0, -1.0], dtype=float)
            maxs = np.array([1.0, 1.0, 1.0], dtype=float)
        if not np.all(np.isfinite(center)):
            center = np.zeros(3, dtype=float)
        base_draw = base[:, :3] - center[:3]
        target_draw = target[:, :3] - center[:3] if target is not None else None
        span = float(np.nanmax(maxs - mins)) if finite_points.size else 1.0
        if not np.isfinite(span) or span <= 0.0:
            span = 1.0

        self._add_axes(span)

        edges = mode_line_edges({"lines": line_rows}, labels)
        line_points = target_draw if target_draw is not None else base_draw
        line_color = (0.84, 0.15, 0.24, 1.0) if target_draw is not None else (0.55, 0.59, 0.65, 1.0)
        line_width = 3.0 if target_draw is not None else 1.4
        for left, right in edges:
            self.add_render_item(
                gl.GLLinePlotItem(
                    pos=np.vstack([line_points[left], line_points[right]]),
                    color=line_color,
                    width=line_width,
                    antialias=True,
                    mode="lines",
                )
            )

        if target_draw is None:
            self.add_render_item(
                gl.GLScatterPlotItem(
                    pos=base_draw,
                    color=(0.10, 0.35, 0.95, 0.22),
                    size=22.0,
                    pxMode=True,
                )
            )
            base_color = (0.05, 0.28, 0.85, 1.0)
            base_size = 13.0
            self.add_render_item(
                gl.GLScatterPlotItem(
                    pos=base_draw,
                    color=base_color,
                    size=base_size,
                    pxMode=True,
                )
            )
        else:
            self.add_render_item(
                gl.GLScatterPlotItem(
                    pos=target_draw,
                    color=(0.84, 0.15, 0.24, 1.0),
                    size=14.0,
                    pxMode=True,
                )
            )
        if show_labels:
            self._add_point_labels(labels, target_draw if target_draw is not None else base_draw, span)
        self._scene_auto_distance = max(span * 3.2, 2.0)
        distance = self._scene_auto_distance if not self._has_camera_fit else self._current_camera_distance()
        self._has_camera_fit = True
        self.setCameraPosition(distance=distance, azimuth=float(azimuth), elevation=float(elevation))

    def _current_camera_distance(self) -> float:
        try:
            distance = float(self.opts.get("distance", self._scene_auto_distance))
        except (TypeError, ValueError):
            distance = self._scene_auto_distance
        if not np.isfinite(distance) or distance <= 0.0:
            return self._scene_auto_distance
        return distance

    def _add_axes(self, span: float) -> None:
        axis_length = max(float(span) * 0.65, 0.75)
        try:
            axes = gl.GLAxisItem(size=QtGui.QVector3D(axis_length, axis_length, axis_length))
            self.add_render_item(axes)
        except Exception:
            segments = [
                [0.0, 0.0, 0.0],
                [axis_length, 0.0, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, axis_length, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, axis_length],
            ]
            self._add_line_segments(segments, (0.17, 0.24, 0.39, 0.85), 1.6)
        self._add_text("X", np.array([axis_length * 1.06, 0.0, 0.0]), "#d7263d")
        self._add_text("Y", np.array([0.0, axis_length * 1.06, 0.0]), "#2e8b57")
        self._add_text("Z", np.array([0.0, 0.0, axis_length * 1.06]), "#1f77b4")

    def _add_bounding_box(self, mins: np.ndarray, maxs: np.ndarray) -> None:
        if not np.all(np.isfinite(mins)) or not np.all(np.isfinite(maxs)):
            return
        if float(np.nanmax(maxs - mins)) <= 0.0:
            return
        x0, y0, z0 = [float(value) for value in mins[:3]]
        x1, y1, z1 = [float(value) for value in maxs[:3]]
        corners = [
            np.array([x0, y0, z0], dtype=float),
            np.array([x1, y0, z0], dtype=float),
            np.array([x1, y1, z0], dtype=float),
            np.array([x0, y1, z0], dtype=float),
            np.array([x0, y0, z1], dtype=float),
            np.array([x1, y0, z1], dtype=float),
            np.array([x1, y1, z1], dtype=float),
            np.array([x0, y1, z1], dtype=float),
        ]
        edge_index = [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 0),
            (4, 5),
            (5, 6),
            (6, 7),
            (7, 4),
            (0, 4),
            (1, 5),
            (2, 6),
            (3, 7),
        ]
        segments: list[np.ndarray] = []
        for left, right in edge_index:
            segments.extend([corners[left], corners[right]])
        self._add_line_segments(segments, (0.68, 0.72, 0.78, 0.35), 1.0)

    def _add_displacement_vectors(self, base_draw: np.ndarray, target_draw: np.ndarray, span: float) -> None:
        segments: list[np.ndarray] = []
        arrow_segments: list[np.ndarray] = []
        for start, end in zip(base_draw, target_draw):
            vector = np.asarray(end - start, dtype=float)
            length = float(np.linalg.norm(vector))
            if not np.isfinite(length) or length <= max(span, 1.0) * 1e-5:
                continue
            segments.extend([start, end])
            direction = vector / length
            reference = np.array([0.0, 0.0, 1.0], dtype=float)
            if abs(float(np.dot(direction, reference))) > 0.92:
                reference = np.array([0.0, 1.0, 0.0], dtype=float)
            side = np.cross(direction, reference)
            side_norm = float(np.linalg.norm(side))
            if not np.isfinite(side_norm) or side_norm <= 0.0:
                continue
            side /= side_norm
            head_length = min(length * 0.35, max(span * 0.08, 0.08))
            head_width = head_length * 0.45
            back = end - direction * head_length
            arrow_segments.extend([end, back + side * head_width, end, back - side * head_width])
        self._add_line_segments(segments, (0.12, 0.35, 0.85, 0.90), 2.0)
        self._add_line_segments(arrow_segments, (0.12, 0.35, 0.85, 0.90), 2.0)

    def _add_point_labels(self, labels: list[str], points: np.ndarray, span: float, *, label_limit: int | None = None) -> None:
        if points.size == 0:
            return
        offset = np.array([0.025, 0.025, 0.035], dtype=float) * max(span, 1.0)
        selected = list(range(len(labels))) if label_limit is None else sparse_label_indices(len(labels), label_limit=label_limit)
        for index in selected:
            if index >= points.shape[0]:
                continue
            label = labels[index]
            point = points[index]
            if not str(label).strip() or not np.all(np.isfinite(point)):
                continue
            self._add_text(str(label), np.asarray(point, dtype=float) + offset, "#334155")

    def _add_text(self, text: str, pos: np.ndarray, color: str) -> None:
        if not hasattr(gl, "GLTextItem"):
            return
        try:
            self.add_render_item(
                gl.GLTextItem(
                    pos=np.asarray(pos, dtype=float),
                    text=str(text),
                    color=QtGui.QColor(color),
                    font=self._label_font,
                )
            )
        except Exception:
            return

    def _add_line_segments(self, segments: list[object], color: tuple[float, float, float, float], width: float) -> None:
        if not segments:
            return
        points = np.asarray(segments, dtype=float).reshape((-1, 3))
        if points.shape[0] < 2 or not np.all(np.isfinite(points)):
            return
        self.add_render_item(
            gl.GLLinePlotItem(
                pos=points,
                color=color,
                width=width,
                antialias=True,
                mode="lines",
            )
        )

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        super().mouseMoveEvent(event)
        self.cameraChanged.emit(float(self.opts.get("azimuth", 35.0)), float(self.opts.get("elevation", 24.0)))


TRACE_COLORS = list(LIGHT_TRACE_COLORS)


def configure_control_panel(widget: QtWidgets.QWidget) -> None:
    widget.setObjectName("diagnosticControlPanel")


def create_control_scroll_area(
    panel: QtWidgets.QWidget,
    *,
    minimum_width: int,
    maximum_width: int,
) -> QtWidgets.QScrollArea:
    panel.setMinimumWidth(0)
    panel.setMaximumWidth(16_777_215)
    panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName("diagnosticControlScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    scroll.setMinimumWidth(minimum_width)
    scroll.setMaximumWidth(maximum_width)
    scroll.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
    scroll.setWidget(panel)
    return scroll


def set_button_role(button: QtWidgets.QPushButton, role: str) -> None:
    shared_set_button_role(button, role)


def create_toggle_button(label: str) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton()
    button.setCheckable(True)
    set_button_role(button, "secondary")
    update_toggle_button_text(button, label)
    button.toggled.connect(lambda _checked, item=button, text=label: update_toggle_button_text(item, text))
    return button


def update_toggle_button_text(button: QtWidgets.QPushButton, label: str) -> None:
    button.setText(f"{label}:开" if button.isChecked() else f"{label}:关")


def create_group_box(title: str, *, layout_type: type[QtWidgets.QLayout] = QtWidgets.QGridLayout) -> tuple[QtWidgets.QGroupBox, QtWidgets.QLayout]:
    group = QtWidgets.QGroupBox(title)
    layout = layout_type(group)
    layout.setContentsMargins(10, 16, 10, 10)
    if isinstance(layout, QtWidgets.QGridLayout):
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
    else:
        layout.setSpacing(7)
    return group, layout


def configure_data_table(table: QtWidgets.QTableWidget, *, minimum_height: int | None = None, maximum_height: int | None = None) -> None:
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    table.horizontalHeader().setStretchLastSection(True)
    if minimum_height is not None:
        table.setMinimumHeight(minimum_height)
    if maximum_height is not None:
        table.setMaximumHeight(maximum_height)


class DiagnosticPage(QtWidgets.QWidget):
    statusChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_directory = Path.cwd()
        self._theme: dict[str, object] = {}
        self._plot_curves: dict[pg.PlotWidget, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        self._active_trace: dict[pg.PlotWidget, str | None] = {}
        self._active_plot: pg.PlotWidget | None = None
        self._data_tip_items: dict[pg.PlotWidget, list[dict[str, object]]] = {}
        self._data_tip_enabled = False
        self._suppress_next_plot_context_menu = False
        self._cursor_enabled = True
        self._cursor_items: dict[pg.PlotWidget, dict[str, object]] = {}
        self._cursor_positions: dict[pg.PlotWidget, tuple[float, float] | None] = {}
        self._axis_history: dict[pg.PlotWidget, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
        self._axis_scaling_plot: pg.PlotWidget | None = None
        self._log_modes: dict[pg.PlotWidget, tuple[bool, bool]] = {}

    def apply_theme(self, theme: dict[str, object]) -> None:
        if theme:
            self._theme = dict(theme)
        global TRACE_COLORS
        TRACE_COLORS = trace_colors_for_theme(self._theme or theme)
        for plot in self.findChildren(pg.PlotWidget):
            apply_plot_theme(plot, theme)
            self._apply_cursor_theme(plot)

    def _trace_colors(self) -> list[str]:
        return trace_colors_for_theme(self._theme)

    def _color_for_label(self, label: object) -> str:
        return color_for_trace_name(label, self._trace_colors(), theme=self._theme)

    def _show_status(self, text: str) -> None:
        self.statusChanged.emit(str(text))

    def _remember_paths(self, paths: list[Path]) -> None:
        if paths:
            self._last_directory = paths[-1].parent

    def _create_plot_widget(self, title: str = "") -> pg.PlotWidget:
        view_box = VnaViewBox()
        plot_item = pg.PlotItem(
            title=title,
            viewBox=view_box,
            axisItems={
                "bottom": VnaAxisItem(orientation="bottom"),
                "left": VnaAxisItem(orientation="left"),
            },
        )
        plot = pg.PlotWidget(plotItem=plot_item)
        view_box._on_left_drag = lambda scene_pos, plot_widget=plot: self._move_cursor_from_scene_pos(
            plot_widget, scene_pos
        )
        view_box._on_right_drag_zoom = lambda start, stop, plot_widget=plot: self._zoom_plot_to_view_rect(
            plot_widget, start, stop
        )
        plot.getPlotItem().setMenuEnabled(False)
        plot.getPlotItem().vb.setMenuEnabled(False)
        plot.addLegend(offset=(4, 2), labelTextSize="7pt")
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.scene().sigMouseClicked.connect(
            lambda event, plot_widget=plot: self._handle_plot_click(plot_widget, event)
        )
        plot.getPlotItem().vb.sigRangeChanged.connect(
            lambda *_args, plot_widget=plot: self._remember_axis_range(plot_widget)
        )
        self._plot_curves[plot] = {}
        self._active_trace[plot] = None
        if self._active_plot is None:
            self._active_plot = plot
        self._data_tip_items[plot] = []
        self._cursor_items[plot] = self._create_cursor_items(plot)
        self._cursor_positions[plot] = None
        self._axis_history[plot] = []
        self._log_modes[plot] = (False, False)
        if self._theme:
            apply_plot_theme(plot, self._theme)
            self._apply_cursor_theme(plot)
        return plot

    def _clear_plot_widget(self, plot: pg.PlotWidget) -> None:
        self._data_tip_items[plot] = []
        plot.clear()
        self._plot_curves[plot] = {}
        self._active_trace[plot] = None
        self._cursor_positions[plot] = None
        self._axis_history[plot] = []
        if plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2), labelTextSize="7pt")
        elif plot.plotItem.legend is not None:
            plot.plotItem.legend.clear()
        self._readd_cursor_items(plot)

    def _plot_curves_on_widget(
        self,
        plot: pg.PlotWidget,
        curves: list[CurvePair],
        *,
        title: str,
        x_label: str,
        y_label: str,
        log_x: bool = False,
        log_y: bool = False,
        subplots: bool = False,
    ) -> int:
        self._clear_plot_widget(plot)
        self._log_modes[plot] = (bool(log_x), bool(log_y) and not subplots)
        plot.setTitle(title)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", "Subplots" if subplots else y_label)
        plot.setLogMode(x=log_x, y=bool(log_y) and not subplots)
        try:
            plot.getAxis("left").setTicks(None)
        except Exception:
            pass
        if self._theme:
            apply_plot_theme(plot, self._theme)
            self._apply_cursor_theme(plot)
        saved: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        plotted = 0
        prepared: list[tuple[CurvePair, np.ndarray, np.ndarray]] = []
        for curve in curves:
            x, y = finite_xy(curve.x, curve.y, positive_x=log_x, positive_y=log_y and not subplots)
            if x.size == 0:
                continue
            prepared.append((curve, x, y))
        if subplots and prepared:
            total = len(prepared)
            tick_values: list[tuple[float, str]] = []
            for index, (curve, x, y) in enumerate(prepared):
                center = float(total - index - 0.5)
                y_min = float(np.nanmin(y))
                y_max = float(np.nanmax(y))
                span = y_max - y_min
                if not np.isfinite(span) or span <= 1e-300:
                    y_plot = np.full_like(y, center, dtype=float)
                else:
                    y_plot = ((y - y_min) / span - 0.5) * 0.72 + center
                color = self._color_for_label(curve.label)
                label = self._unique_saved_label(saved, curve.label)
                plot.plot(x, y_plot, pen=pg.mkPen(color, width=1.35), name=label)
                text = pg.TextItem(label, color=color, anchor=(0.0, 0.5))
                text.setZValue(20)
                text.setPos(float(x[0]), center)
                plot.addItem(text)
                if index < total - 1:
                    divider = pg.InfiniteLine(
                        pos=center - 0.5,
                        angle=0,
                        pen=pg.mkPen("#9ca3af", width=0.6, style=QtCore.Qt.DashLine),
                    )
                    divider.setZValue(5)
                    plot.addItem(divider, ignoreBounds=True)
                saved[label] = (x, y_plot)
                tick_values.append((center, label))
                plotted += 1
            self._plot_curves[plot] = saved
            try:
                plot.getAxis("left").setTicks([tick_values])
            except Exception:
                pass
            plot.showGrid(x=True, y=False, alpha=0.18)
            self._axis_scaling_plot = plot
            try:
                xs = concat_finite([curve[0] for curve in saved.values()], positive_only=log_x)
                if xs.size:
                    xmin, xmax = safe_extent(xs, log_enabled=log_x)
                    if log_x:
                        plot.setXRange(np.log10(xmin), np.log10(xmax), padding=0.04)
                    else:
                        plot.setXRange(xmin, xmax, padding=0.04)
                plot.setYRange(0.0, float(total), padding=0.02)
            finally:
                self._axis_scaling_plot = None
            return plotted
        for index, (curve, x, y) in enumerate(prepared):
            color = self._color_for_label(curve.label)
            label = self._unique_saved_label(saved, curve.label)
            plot.plot(x, y, pen=pg.mkPen(color, width=1.5), name=label)
            saved[label] = (x, y)
            plotted += 1
        self._plot_curves[plot] = saved
        plot.showGrid(x=True, y=True, alpha=0.22)
        if saved:
            self._auto_range_plot(
                plot,
                [curve[0] for curve in saved.values()],
                [curve[1] for curve in saved.values()],
                log_x=log_x,
                log_y=log_y,
            )
        else:
            plot.enableAutoRange()
        return plotted

    @staticmethod
    def _unique_saved_label(saved: dict[str, tuple[np.ndarray, np.ndarray]], label: str) -> str:
        base = str(label or "Curve")
        if base not in saved:
            return base
        index = 2
        while f"{base} ({index})" in saved:
            index += 1
        return f"{base} ({index})"

    def _cursor_palette(self) -> dict[str, object]:
        return _cursor_palette_for_background(str(self._theme.get("plot_bg", "#ffffff")))

    def _apply_cursor_theme(self, plot: pg.PlotWidget) -> None:
        items = self._cursor_items.get(plot)
        if not items:
            return
        palette = self._cursor_palette()
        items["line"].setPen(pg.mkPen(palette["line"], width=1.4))
        items["point"].setPen(pg.mkPen(palette["line"], width=1.8))
        items["point"].setBrush(pg.mkBrush(255, 255, 255, 0))
        _apply_text_item_style(
            items["text"],
            color=palette["text"],
            fill=palette["fill"],
            border=palette["border"],
        )

    def _toggle_data_tip_mode(self, enabled: bool) -> None:
        self._data_tip_enabled = bool(enabled)
        self._show_status(f"数据提示模式：{'开启' if enabled else '关闭'}")

    def _toggle_cursor_readout(self, enabled: bool) -> None:
        self._cursor_enabled = bool(enabled)
        for items in self._cursor_items.values():
            for item in items.values():
                item.setVisible(False)
        self._show_status(f"读数游标：{'开启' if enabled else '关闭'}")

    def _create_cursor_items(self, plot: pg.PlotWidget) -> dict[str, object]:
        palette = self._cursor_palette()
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(palette["line"], width=1.4))
        line.setZValue(30)
        line.setVisible(False)
        plot.addItem(line, ignoreBounds=True)
        point = pg.ScatterPlotItem(
            size=10,
            symbol="+",
            brush=pg.mkBrush(255, 255, 255, 0),
            pen=pg.mkPen(palette["line"], width=1.8),
            pxMode=True,
        )
        point.setZValue(31)
        point.setVisible(False)
        plot.addItem(point)
        text = pg.TextItem(
            text="",
            color=palette["text"],
            anchor=(-0.05, 1.05),
            fill=pg.mkBrush(palette["fill"]),
            border=pg.mkPen(palette["border"], width=0.9),
        )
        text.setZValue(32)
        text.setVisible(False)
        plot.addItem(text)
        return {"line": line, "point": point, "text": text}

    def _readd_cursor_items(self, plot: pg.PlotWidget) -> None:
        items = self._cursor_items.get(plot)
        if not items:
            return
        if items["line"].scene() is None:
            plot.addItem(items["line"], ignoreBounds=True)
        if items["point"].scene() is None:
            plot.addItem(items["point"])
        if items["text"].scene() is None:
            plot.addItem(items["text"])

    def _set_cursor_position(
        self, plot: pg.PlotWidget, cursor_x: float, cursor_y: float, trace: str | None = None
    ) -> bool:
        if trace:
            self._active_trace[plot] = trace
        self._cursor_positions[plot] = (float(cursor_x), float(cursor_y))
        if not self._cursor_enabled:
            self._toggle_cursor_readout(True)
        items = self._cursor_items.get(plot)
        if not items:
            return False
        plot_x = self._to_plot_x(plot, cursor_x)
        plot_y = self._to_plot_y(plot, cursor_y)
        items["line"].setValue(plot_x)
        items["line"].setVisible(True)
        items["point"].setData([plot_x], [plot_y])
        items["point"].setVisible(True)
        items["text"].setText(f"X {cursor_x:.6g}\nY {cursor_y:.6g}")
        if hasattr(items["text"], "setAnchor"):
            items["text"].setAnchor(self._data_tip_anchor_for_plot_point(plot, cursor_x, cursor_y))
        items["text"].setPos(plot_x, plot_y)
        items["text"].setVisible(True)
        self._show_status(f"读数：x={cursor_x:.4g}, y={cursor_y:.4g}")
        return True

    def _handle_plot_click(self, plot: pg.PlotWidget, event) -> None:
        if not plot.sceneBoundingRect().contains(event.scenePos()):
            return
        self._active_plot = plot
        if event.button() == QtCore.Qt.RightButton:
            event.accept()
            if self._suppress_next_plot_context_menu:
                self._suppress_next_plot_context_menu = False
                return
            self._show_plot_context_menu(plot, event.screenPos())
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        mouse_point = plot.getPlotItem().vb.mapSceneToView(event.scenePos())
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        trace = self._nearest_trace_name(plot, click_x, click_y)
        if trace:
            self._active_trace[plot] = trace
        if self._data_tip_enabled:
            self._place_data_tip(plot, click_x, click_y)
        else:
            snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
            if snapped is not None:
                x, y, snapped_trace = snapped
                self._set_cursor_position(plot, x, y, snapped_trace)

    def _show_plot_context_menu(self, plot: pg.PlotWidget, screen_pos) -> None:
        self._active_plot = plot
        menu, actions = self._build_plot_context_menu(plot)
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is actions["back"]:
            self._restore_axis_history(plot)
        elif action is actions["auto"]:
            self._auto_scale_current_plot(plot)
        elif action is actions["data_tip"]:
            self._toggle_data_tip_mode(not self._data_tip_enabled)
        elif action is actions["cursor"]:
            self._toggle_cursor_readout(not self._cursor_enabled)
        elif action is actions["clear_tips"]:
            self._clear_data_tips(plot)

    def _build_plot_context_menu(self, plot: pg.PlotWidget) -> tuple[QtWidgets.QMenu, dict[str, object]]:
        menu = QtWidgets.QMenu(plot)
        actions: dict[str, object] = {}
        actions["back"] = menu.addAction("返回上一缩放")
        actions["auto"] = menu.addAction("自动缩放")
        menu.addSeparator()
        actions["data_tip"] = menu.addAction("数据提示")
        actions["data_tip"].setCheckable(True)
        actions["data_tip"].setChecked(self._data_tip_enabled)
        actions["cursor"] = menu.addAction("读数游标")
        actions["cursor"].setCheckable(True)
        actions["cursor"].setChecked(self._cursor_enabled)
        actions["clear_tips"] = menu.addAction("清除数据提示")
        return menu, actions

    def _move_cursor_from_scene_pos(self, plot: pg.PlotWidget | None, scene_pos) -> bool:
        if plot is None or not plot.sceneBoundingRect().contains(scene_pos):
            return False
        self._active_plot = plot
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        x, y, trace = snapped
        return self._set_cursor_position(plot, x, y, trace)

    def _zoom_plot_to_view_rect(self, plot: pg.PlotWidget | None, start_point, stop_point) -> bool:
        if plot is None:
            return False
        x0 = float(start_point.x())
        x1 = float(stop_point.x())
        y0 = float(start_point.y())
        y1 = float(stop_point.y())
        if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
            return False
        xmin = min(x0, x1)
        xmax = max(x0, x1)
        ymin = min(y0, y1)
        ymax = max(y0, y1)
        plot.setXRange(xmin, xmax, padding=0.0)
        plot.setYRange(ymin, ymax, padding=0.0)
        self._show_status("已缩放图像坐标轴")
        return True

    def _remember_axis_range(self, plot: pg.PlotWidget) -> None:
        if self._axis_scaling_plot is plot:
            return
        ranges = self._current_plot_ranges(plot)
        history = self._axis_history.setdefault(plot, [])
        if not history or not ranges_close(history[-1], ranges):
            history.append(ranges)
            if len(history) > 8:
                del history[0]

    @staticmethod
    def _current_plot_ranges(plot: pg.PlotWidget) -> tuple[tuple[float, float], tuple[float, float]]:
        x_range, y_range = plot.viewRange()
        return (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )

    def _restore_axis_history(self, plot: pg.PlotWidget) -> bool:
        history = self._axis_history.setdefault(plot, [])
        if len(history) < 2:
            self._auto_scale_current_plot(plot)
            return False
        history.pop()
        x_range, y_range = history.pop()
        self._axis_scaling_plot = plot
        try:
            plot.setXRange(x_range[0], x_range[1], padding=0.0)
            plot.setYRange(y_range[0], y_range[1], padding=0.0)
        finally:
            self._axis_scaling_plot = None
        self._show_status("已恢复上一缩放")
        return True

    def _auto_scale_current_plot(self, plot: pg.PlotWidget) -> None:
        curves = self._plot_curves.get(plot, {})
        log_x, log_y = self._log_modes.get(plot, (False, False))
        self._auto_range_plot(
            plot,
            [curve[0] for curve in curves.values()],
            [curve[1] for curve in curves.values()],
            log_x=log_x,
            log_y=log_y,
        )
        self._show_status("已自动缩放图像")

    def _auto_place_legend(self, plot: pg.PlotWidget) -> None:
        log_x, log_y = self._log_modes.get(plot, (False, False))
        place_legend_away_from_curves(
            plot,
            self._plot_curves.get(plot, {}),
            log_x=log_x,
            log_y=log_y,
            default_offset=(4, 2),
        )

    def _nearest_trace_name(self, plot: pg.PlotWidget, click_x: float, click_y: float) -> str | None:
        curves = self._plot_curves.get(plot, {})
        if not curves:
            return None
        best_name: str | None = None
        best_score: float | None = None
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._to_plot_x(plot, click_x)
        click_plot_y = self._to_plot_y(plot, click_y)
        for name, (x_data, y_data) in curves.items():
            x_arr, y_arr = finite_xy(x_data, y_data)
            if x_arr.size == 0:
                continue
            index = int(np.clip(np.searchsorted(x_arr, click_x), 0, x_arr.size - 1))
            if index > 0 and abs(x_arr[index - 1] - click_x) <= abs(x_arr[index] - click_x):
                index -= 1
            dx = (self._to_plot_x(plot, float(x_arr[index])) - click_plot_x) / x_span
            dy = (self._to_plot_y(plot, float(y_arr[index])) - click_plot_y) / y_span
            score = dx * dx + dy * dy
            if best_score is None or score < best_score:
                best_score = score
                best_name = name
        return best_name

    def _nearest_curve_point_2d(
        self, plot: pg.PlotWidget, click_x: float, click_y: float
    ) -> tuple[float, float, str] | None:
        trace = self._active_trace.get(plot) or self._nearest_trace_name(plot, click_x, click_y)
        curves = self._plot_curves.get(plot, {})
        if trace not in curves:
            return None
        x_arr, y_arr = finite_xy(*curves[trace])
        if x_arr.size == 0:
            return None
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._to_plot_x(plot, click_x)
        click_plot_y = self._to_plot_y(plot, click_y)
        plot_x = np.asarray([self._to_plot_x(plot, float(value)) for value in x_arr])
        plot_y = np.asarray([self._to_plot_y(plot, float(value)) for value in y_arr])
        scores = ((plot_x - click_plot_x) / x_span) ** 2 + ((plot_y - click_plot_y) / y_span) ** 2
        index = int(np.nanargmin(scores))
        return float(x_arr[index]), float(y_arr[index]), trace

    def _place_data_tip(self, plot: pg.PlotWidget, click_x: float, click_y: float) -> bool:
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        tip_x, tip_y, trace = snapped
        self._active_trace[plot] = trace
        data_tip: dict[str, object] = {"trace": trace, "x": tip_x, "y": tip_y}
        point = DataTipPoint(
            [self._to_plot_x(plot, tip_x)],
            [self._to_plot_y(plot, tip_y)],
            size=9,
            symbol="o",
            brush=pg.mkBrush("#fff59d"),
            pen=pg.mkPen("#111111", width=0.8),
            pxMode=True,
            on_drag=lambda scene_pos, plot_widget=plot, tip=data_tip: self._drag_data_tip_to_scene_pos(
                plot_widget, tip, scene_pos
            ),
            on_context_menu=lambda screen_pos, plot_widget=plot, tip=data_tip: self._show_data_tip_menu(
                plot_widget, tip, screen_pos
            ),
        )
        point.setZValue(40)
        text = DataTipText(
            text=f"X {tip_x:.6g}\nY {tip_y:.6g}",
            color="#111111",
            anchor=self._data_tip_anchor_for_plot_point(plot, tip_x, tip_y),
            fill=pg.mkBrush(255, 245, 157, 230),
            border=pg.mkPen("#111111", width=0.8),
            on_context_menu=lambda screen_pos, plot_widget=plot, tip=data_tip: self._show_data_tip_menu(
                plot_widget, tip, screen_pos
            ),
            on_drag=lambda scene_pos, plot_widget=plot, tip=data_tip: self._drag_data_tip_label_to_scene_pos(
                plot_widget, tip, scene_pos
            ),
        )
        text.setZValue(41)
        text.setPos(self._to_plot_x(plot, tip_x), self._to_plot_y(plot, tip_y))
        plot.addItem(point)
        plot.addItem(text)
        data_tip["point"] = point
        data_tip["text"] = text
        self._data_tip_items.setdefault(plot, []).append(data_tip)
        self._show_status(f"数据提示：x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _drag_data_tip_to_scene_pos(
        self, plot: pg.PlotWidget, data_tip: dict[str, object], scene_pos
    ) -> bool:
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        tip_x, tip_y, trace = snapped
        data_tip["trace"] = trace
        data_tip["x"] = tip_x
        data_tip["y"] = tip_y
        point = data_tip["point"]
        text = data_tip["text"]
        point.setData([self._to_plot_x(plot, tip_x)], [self._to_plot_y(plot, tip_y)])
        text.setText(f"X {tip_x:.6g}\nY {tip_y:.6g}")
        if hasattr(text, "setAnchor"):
            text.setAnchor(
                data_tip.get("label_anchor")
                if data_tip.get("label_anchor_manual")
                else self._data_tip_anchor_for_plot_point(plot, tip_x, tip_y)
            )
        text.setPos(self._to_plot_x(plot, tip_x), self._to_plot_y(plot, tip_y))
        self._show_status(f"数据提示已移动：x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _drag_data_tip_label_to_scene_pos(
        self, plot: pg.PlotWidget, data_tip: dict[str, object], scene_pos
    ) -> bool:
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        point_x = self._to_plot_x(plot, float(data_tip["x"]))
        point_y = self._to_plot_y(plot, float(data_tip["y"]))
        anchor = _data_tip_anchor_for_label_drag(
            float(mouse_point.x()),
            float(mouse_point.y()),
            point_x,
            point_y,
        )
        data_tip["label_anchor"] = anchor
        data_tip["label_anchor_manual"] = True
        text = data_tip["text"]
        if hasattr(text, "setAnchor"):
            text.setAnchor(anchor)
        self._show_status("数据提示标签已移动")
        return True

    def _show_data_tip_menu(self, plot: pg.PlotWidget, data_tip: dict[str, object], screen_pos) -> None:
        self._active_plot = plot
        self._suppress_plot_context_menu_once()
        menu = QtWidgets.QMenu(self)
        delete_this = menu.addAction("删除此数据提示")
        delete_all = menu.addAction("删除全部数据提示")
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is delete_this:
            self._delete_data_tip(plot, data_tip)
        elif action is delete_all:
            self._clear_data_tips(plot)

    def _suppress_plot_context_menu_once(self) -> None:
        self._suppress_next_plot_context_menu = True

    def _delete_data_tip(self, plot: pg.PlotWidget, data_tip: dict[str, object]) -> bool:
        if data_tip not in self._data_tip_items.get(plot, []):
            return False
        for key in ("point", "text"):
            try:
                plot.removeItem(data_tip[key])
            except Exception:
                pass
        self._data_tip_items[plot].remove(data_tip)
        return True

    def _clear_data_tips(self, plot: pg.PlotWidget) -> None:
        for data_tip in list(self._data_tip_items.get(plot, [])):
            self._delete_data_tip(plot, data_tip)
        self._show_status("已清除数据提示")

    def _data_tip_anchor_for_plot_point(
        self, plot: pg.PlotWidget, value_x: float, value_y: float
    ) -> tuple[float, float]:
        plot_x = self._to_plot_x(plot, value_x)
        plot_y = self._to_plot_y(plot, value_y)
        x_range, y_range = plot.viewRange()
        x_span = max(float(x_range[1] - x_range[0]), 1e-20)
        y_span = max(float(y_range[1] - y_range[0]), 1e-20)
        near_right = (plot_x - float(x_range[0])) / x_span > 0.72
        near_top = (plot_y - float(y_range[0])) / y_span > 0.72
        return (1.05 if near_right else -0.05, -0.05 if near_top else 1.05)

    def _to_plot_x(self, plot: pg.PlotWidget, value: float) -> float:
        log_x, _log_y = self._log_modes.get(plot, (False, False))
        if log_x:
            return float(np.log10(max(value, 1e-300)))
        return float(value)

    def _from_plot_x(self, plot: pg.PlotWidget, value: float) -> float:
        log_x, _log_y = self._log_modes.get(plot, (False, False))
        if log_x:
            return float(10.0 ** value)
        return float(value)

    def _to_plot_y(self, plot: pg.PlotWidget, value: float) -> float:
        _log_x, log_y = self._log_modes.get(plot, (False, False))
        if log_y:
            return float(np.log10(max(value, 1e-300)))
        return float(value)

    def _from_plot_y(self, plot: pg.PlotWidget, value: float) -> float:
        _log_x, log_y = self._log_modes.get(plot, (False, False))
        if log_y:
            return float(10.0 ** value)
        return float(value)

    def _auto_range_plot(
        self,
        plot: pg.PlotWidget,
        x_arrays: list[np.ndarray],
        y_arrays: list[np.ndarray],
        *,
        log_x: bool,
        log_y: bool,
    ) -> None:
        xs = concat_finite(x_arrays, positive_only=log_x)
        ys = concat_finite(y_arrays, positive_only=log_y)
        self._axis_scaling_plot = plot
        try:
            view_box = plot.getPlotItem().vb
            view_box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            if xs.size:
                xmin, xmax = safe_extent(xs, log_enabled=log_x)
                if log_x:
                    plot.setXRange(np.log10(xmin), np.log10(xmax), padding=0.04)
                else:
                    plot.setXRange(xmin, xmax, padding=0.04)
            if ys.size:
                ymin, ymax = safe_extent(ys, log_enabled=log_y)
                if log_y:
                    plot.setYRange(np.log10(ymin), np.log10(ymax), padding=0.08)
                else:
                    plot.setYRange(ymin, ymax, padding=0.08)
        finally:
            self._axis_scaling_plot = None
        self._auto_place_legend(plot)

    def export_plot_csv(self, plot: pg.PlotWidget, path: str | Path) -> Path:
        curves = self._plot_curves.get(plot, {})
        if not curves:
            raise ValueError("没有可导出的曲线。")
        destination = Path(path)
        max_rows = max(len(x) for x, _y in curves.values())
        headers: list[str] = []
        for label in curves:
            headers.extend([f"{label}_X", f"{label}_Y"])
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row_index in range(max_rows):
                row: list[float | str] = []
                for x, y in curves.values():
                    if row_index < len(x) and row_index < len(y):
                        row.extend([float(x[row_index]), float(y[row_index])])
                    else:
                        row.extend(["", ""])
                writer.writerow(row)
        return destination


class VibrationAnalysisPage(DiagnosticPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[VibrationAnalysisFile] = []
        self._updating_controls = False
        self._suppress_auto_plot = False
        self._preferred_frequency_labels: set[str] = set()
        self._preferred_log_group = ""
        self._preferred_log_labels: set[str] = set()
        self._log_ranges: dict[str, tuple[int, int]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(5)

        data_group, data_layout = create_group_box("1. 数据", layout_type=QtWidgets.QVBoxLayout)
        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.delete_button = QtWidgets.QPushButton("删除")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.clear_button, "secondary")
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.clear_button)
        data_layout.addLayout(button_row)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(110)
        data_layout.addWidget(self.file_list, 1)
        controls_layout.addWidget(data_group)

        settings_group, settings_layout = create_group_box("2. 分析设置")
        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItems(["叠加", "子图"])
        self.frequency_pair_list = QtWidgets.QListWidget()
        self.frequency_pair_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.frequency_pair_list.setAlternatingRowColors(True)
        self.frequency_pair_list.setMinimumHeight(84)
        self.log_group_combo = QtWidgets.QComboBox()
        self.log_range_start = QtWidgets.QLineEdit()
        self.log_range_start.setValidator(QtGui.QIntValidator(1, 2_000_000_000, self))
        self.log_range_end = QtWidgets.QLineEdit()
        self.log_range_end.setValidator(QtGui.QIntValidator(1, 2_000_000_000, self))
        self.log_range_start.setMinimumWidth(84)
        self.log_range_end.setMinimumWidth(84)
        self.log_channel_list = QtWidgets.QListWidget()
        self.log_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.log_channel_list.setAlternatingRowColors(True)
        self.log_channel_list.setMinimumHeight(84)
        self.demean_check = create_toggle_button("去均值")
        self.hold_check = create_toggle_button("保持曲线")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")

        log_range_row = QtWidgets.QHBoxLayout()
        log_range_row.setContentsMargins(0, 0, 0, 0)
        log_range_row.setSpacing(5)
        log_range_row.addWidget(self.log_range_start)
        log_range_row.addWidget(QtWidgets.QLabel("至"))
        log_range_row.addWidget(self.log_range_end)

        settings_layout.addWidget(QtWidgets.QLabel("绘图模式"), 0, 0)
        settings_layout.addWidget(self.plot_mode_combo, 0, 1)
        settings_layout.addWidget(QtWidgets.QLabel("频响曲线"), 1, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.frequency_pair_list, 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("日志分组"), 2, 0)
        settings_layout.addWidget(self.log_group_combo, 2, 1)
        settings_layout.addWidget(QtWidgets.QLabel("样本范围"), 3, 0)
        settings_layout.addLayout(log_range_row, 3, 1)
        settings_layout.addWidget(QtWidgets.QLabel("日志通道"), 4, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.log_channel_list, 4, 1)
        controls_layout.addWidget(settings_group)

        action_group, action_layout = create_group_box("3. 操作", layout_type=QtWidgets.QGridLayout)
        self.action_group = action_group
        action_layout.addWidget(self.plot_button, 0, 0)
        action_layout.addWidget(self.export_button, 0, 1)
        action_layout.addWidget(self.demean_check, 1, 0)
        action_layout.addWidget(self.hold_check, 1, 1)
        action_layout.setColumnStretch(0, 1)
        action_layout.setColumnStretch(1, 1)
        controls_layout.addWidget(action_group)
        controls_layout.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        self.frequency_plot = self._create_plot_widget("频率响应")
        self.log_plot = self._create_plot_widget("日志 / 传感器")
        self.tabs.addTab(self.frequency_plot, "频响分析")
        self.tabs.addTab(self.log_plot, "日志 / 传感器")

        controls_layout.removeWidget(action_group)
        self.controls_scroll = create_control_scroll_area(
            controls,
            minimum_width=0,
            maximum_width=16_777_215,
        )
        self.controls_column = QtWidgets.QWidget()
        configure_control_panel(self.controls_column)
        self.controls_column.setMinimumWidth(260)
        self.controls_column.setMaximumWidth(310)
        controls_column_layout = QtWidgets.QVBoxLayout(self.controls_column)
        controls_column_layout.setContentsMargins(0, 0, 0, 0)
        controls_column_layout.setSpacing(5)
        controls_column_layout.addWidget(self.controls_scroll, 1)
        controls_column_layout.addWidget(action_group, 0)
        layout.addWidget(self.controls_column)
        layout.addWidget(self.tabs, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.file_list.currentRowChanged.connect(lambda _row: self._on_file_selection_changed())
        self.log_group_combo.currentIndexChanged.connect(lambda _index: self._on_log_group_changed())
        self.log_range_start.editingFinished.connect(self._on_log_range_changed)
        self.log_range_end.editingFinished.connect(self._on_log_range_changed)
        self.frequency_pair_list.itemSelectionChanged.connect(self._on_frequency_selection_changed)
        self.log_channel_list.itemSelectionChanged.connect(self._on_log_selection_changed)
        self.tabs.currentChanged.connect(lambda _index: self._auto_plot_from_control_change())
        self.plot_mode_combo.currentIndexChanged.connect(lambda _index: self._on_plot_mode_changed())
        self.demean_check.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)
        self._sync_hold_availability()

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载上位机数据文件",
            str(self._last_directory),
            "数据文件 (*.dat *.txt *.csv);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        for path in paths:
            try:
                loaded_file = load_vibration_analysis_file(path)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self.files.append(loaded_file)
            self.file_list.addItem(loaded_file.table.name)
            loaded += 1
        self._remember_paths(paths)
        self._suppress_auto_plot = True
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh_controls()
        self._select_default_tab_for_current_file()
        self._suppress_auto_plot = False
        self._show_status(f"已加载上位机数据文件：{loaded} 个，请选择通道后点击绘图")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.frequency_pair_list.clear()
        self.log_group_combo.clear()
        self.log_channel_list.clear()
        self._log_ranges.clear()
        self._configure_log_range_controls(None)
        self.frequency_plot.clear()
        self.log_plot.clear()
        self._plot_curves.clear()
        self._show_status("上位机数据分析已清空")

    def current_file(self) -> VibrationAnalysisFile | None:
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self.files):
            return None
        return self.files[row]

    def plot_current(self) -> None:
        current = self.current_file()
        if current is None:
            self._show_status("未选择上位机数据文件")
            return
        subplots = self.plot_mode_combo.currentIndex() == 1
        self._sync_hold_availability()
        hold = self.hold_check.isChecked() and not subplots
        if self.tabs.currentIndex() == 0:
            curves = self._selected_frequency_pairs(current)
            x_label = curves[0].x_label if curves else "频率 (Hz)"
            y_label = curves[0].y_label if curves else "幅值"
            plotted = self._plot_vibration_curves_on_widget(
                self.frequency_plot,
                curves,
                title=f"频率响应 - {current.table.name}",
                x_label=x_label,
                y_label=y_label,
                log_x=True,
                log_y=False,
                subplots=subplots,
                hold=hold,
            )
        else:
            curves = self._selected_log_curves(current)
            x_label = curves[0].x_label if curves else "样本序号"
            y_label = curves[0].y_label if curves else "数值"
            plotted = self._plot_vibration_curves_on_widget(
                self.log_plot,
                curves,
                title=f"日志 / 传感器 - {current.table.name}",
                x_label=x_label,
                y_label=y_label,
                subplots=subplots,
                hold=hold,
            )
        self._show_status(f"已绘制上位机数据曲线：{plotted} 条")

    def _on_file_selection_changed(self) -> None:
        self._refresh_controls()
        self._select_default_tab_for_current_file()
        self._auto_plot_from_control_change()

    def _on_log_group_changed(self) -> None:
        if not self._updating_controls:
            self._preferred_log_group = self.log_group_combo.currentText()
            self._preferred_log_labels.clear()
        self._refresh_log_channels()
        self._auto_plot_from_control_change()

    def _on_log_range_changed(self) -> None:
        if self._updating_controls:
            return
        current = self.current_file()
        if current is None:
            return
        key = self._file_key(current)
        start, end = self._resolved_log_range_values(current)
        changed = self._log_ranges.get(key) != (start, end)
        self._log_ranges[key] = (start, end)
        self._updating_controls = True
        try:
            self._set_log_range_controls(current.table.row_count, (start, end), enabled=True)
        finally:
            self._updating_controls = False
        if not changed and self._plot_curves.get(self.log_plot):
            return
        self._auto_plot_from_control_change()

    def _on_plot_mode_changed(self) -> None:
        self._sync_hold_availability()
        self._auto_plot_from_control_change()

    def _auto_plot_from_control_change(self) -> None:
        if self._updating_controls or self._suppress_auto_plot or self.current_file() is None:
            return
        self.plot_current()

    def _on_frequency_selection_changed(self) -> None:
        if not self._updating_controls:
            self._preferred_frequency_labels = {item.text() for item in self.frequency_pair_list.selectedItems()}
        self._auto_plot_from_control_change()

    def _on_log_selection_changed(self) -> None:
        if not self._updating_controls:
            self._preferred_log_labels = {item.text() for item in self.log_channel_list.selectedItems()}
        self._auto_plot_from_control_change()

    def _sync_hold_availability(self) -> None:
        subplots = self.plot_mode_combo.currentIndex() == 1
        if subplots and self.hold_check.isChecked():
            self.hold_check.setChecked(False)
        self.hold_check.setEnabled(not subplots)

    def _plot_vibration_curves_on_widget(
        self,
        plot: pg.PlotWidget,
        curves: list[CurvePair],
        *,
        title: str,
        x_label: str,
        y_label: str,
        log_x: bool = False,
        log_y: bool = False,
        subplots: bool = False,
        hold: bool = False,
    ) -> int:
        render_curves = list(curves)
        if hold and not subplots:
            prior = [
                CurvePair(label, np.asarray(x, dtype=float).copy(), np.asarray(y, dtype=float).copy(), x_label, y_label)
                for label, (x, y) in self._plot_curves.get(plot, {}).items()
            ]
            render_curves = prior + render_curves
        return self._plot_curves_on_widget(
            plot,
            render_curves,
            title=title,
            x_label=x_label,
            y_label=y_label,
            log_x=log_x,
            log_y=log_y,
            subplots=subplots,
        )

    def _select_default_tab_for_current_file(self) -> None:
        current = self.current_file()
        if current is None:
            return
        if current.log_groups and not current.frequency_pairs:
            self.tabs.setCurrentWidget(self.log_plot)
        elif current.frequency_pairs:
            self.tabs.setCurrentWidget(self.frequency_plot)

    def _refresh_controls(self) -> None:
        self._updating_controls = True
        try:
            current = self.current_file()
            self.frequency_pair_list.clear()
            self.log_group_combo.clear()
            self.log_channel_list.clear()
            if current is None:
                self._configure_log_range_controls(None)
                return
            self._configure_log_range_controls(current)
            selected_frequency_labels = set(self._preferred_frequency_labels)
            if not selected_frequency_labels:
                selected_frequency_labels = {item.text() for item in self.frequency_pair_list.selectedItems()}
            for pair in current.frequency_pairs:
                item = QtWidgets.QListWidgetItem(pair.label)
                self.frequency_pair_list.addItem(item)
                item.setSelected(pair.label in selected_frequency_labels or not selected_frequency_labels)
            preferred_group = self._preferred_log_group or self.log_group_combo.currentText()
            for group in current.log_groups:
                self.log_group_combo.addItem(group)
            if preferred_group:
                index = self.log_group_combo.findText(preferred_group)
                if index >= 0:
                    self.log_group_combo.setCurrentIndex(index)
            self._refresh_log_channels()
        finally:
            self._updating_controls = False

    def _refresh_log_channels(self) -> None:
        was_updating = self._updating_controls
        self._updating_controls = True
        try:
            current = self.current_file()
            self.log_channel_list.clear()
            if current is None:
                return
            group = self.log_group_combo.currentText()
            indices = current.log_groups.get(group, [])
            labels = current.log_group_labels.get(group, [])
            selected_labels = set(self._preferred_log_labels)
            if not selected_labels:
                selected_labels = {item.text() for item in self.log_channel_list.selectedItems()}
            for position, index in enumerate(indices):
                label = labels[position] if position < len(labels) else current.table.headers[index]
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, index)
                item.setData(QtCore.Qt.UserRole + 1, label)
                item.setToolTip(current.table.headers[index])
                self.log_channel_list.addItem(item)
                item.setSelected(label in selected_labels or not selected_labels)
        finally:
            self._updating_controls = was_updating

    def _selected_frequency_pairs(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected = {item.text() for item in self.frequency_pair_list.selectedItems()}
        if not selected and self._preferred_frequency_labels:
            return current.frequency_pairs[:1]
        pairs = [pair for pair in current.frequency_pairs if not selected or pair.label in selected]
        return pairs or current.frequency_pairs[:1]

    def _selected_log_curves(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected_items = self.log_channel_list.selectedItems()
        if not selected_items and self.log_channel_list.count():
            selected_items = [self.log_channel_list.item(0)]
        selected_channels = [
            (
                int(item.data(QtCore.Qt.UserRole)),
                str(item.data(QtCore.Qt.UserRole + 1) or item.text()),
            )
            for item in selected_items
            if item.data(QtCore.Qt.UserRole) is not None
        ]
        start, end = self._log_slice_bounds()
        x = np.asarray(
            current.table.metadata.get("sample_index", np.arange(1, current.table.row_count + 1, dtype=float)),
            dtype=float,
        )
        if x.size < current.table.row_count:
            x = np.arange(1, current.table.row_count + 1, dtype=float)
        x = x[start:end]
        curves: list[CurvePair] = []
        for index, label in selected_channels:
            y = np.asarray(current.table.data[start:end, index], dtype=float)
            if self.demean_check.isChecked():
                y = self._demean_vector(y)
            curves.append(CurvePair(label, x[: y.size], y, "样本序号", label))
        return curves

    def _configure_log_range_controls(self, current: VibrationAnalysisFile | None) -> None:
        if current is None or current.table.row_count <= 0:
            self._set_log_range_controls(1, (1, 1), enabled=False)
            return
        count = int(current.table.row_count)
        stored = self._log_ranges.get(self._file_key(current), (1, count))
        self._set_log_range_controls(count, stored, enabled=True)

    def _set_log_range_controls(self, count: int, values: tuple[int, int], *, enabled: bool) -> None:
        count = max(1, int(count))
        start, end = values
        start = max(1, min(int(start), count))
        end = max(1, min(int(end), count))
        if end < start:
            start, end = end, start
        self.log_range_start.setPlaceholderText("1")
        self.log_range_end.setPlaceholderText(str(count))
        self.log_range_start.setText(str(start))
        self.log_range_end.setText(str(end))
        self.log_range_start.setEnabled(enabled)
        self.log_range_end.setEnabled(enabled)

    @staticmethod
    def _parse_log_range_text(text: str, *, default: int, count: int) -> int:
        raw = str(text or "").strip()
        if not raw:
            return default
        try:
            value = int(round(float(raw)))
        except ValueError:
            value = default
        return max(1, min(int(count), value))

    def _resolved_log_range_values(self, current: VibrationAnalysisFile) -> tuple[int, int]:
        count = max(1, int(current.table.row_count))
        start = self._parse_log_range_text(self.log_range_start.text(), default=1, count=count)
        end = self._parse_log_range_text(self.log_range_end.text(), default=count, count=count)
        if end < start:
            start, end = end, start
        return start, end

    def _log_slice_bounds(self) -> tuple[int, int]:
        current = self.current_file()
        if current is None:
            return 0, 1
        start_value, end_value = self._resolved_log_range_values(current)
        start = max(0, start_value - 1)
        end = max(start + 1, end_value)
        count = max(1, current.table.row_count)
        return min(start, count - 1), min(max(end, start + 1), count)

    @staticmethod
    def _file_key(current: VibrationAnalysisFile) -> str:
        return str(current.table.path)

    @staticmethod
    def _demean_vector(values: np.ndarray) -> np.ndarray:
        result = np.asarray(values, dtype=float).copy()
        valid = np.isfinite(result)
        if np.any(valid):
            result[valid] = result[valid] - float(np.mean(result[valid]))
        return result

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                removed = self.files.pop(row)
                self._log_ranges.pop(self._file_key(removed), None)
                self.file_list.takeItem(row)
        self._refresh_controls()
        self._auto_plot_from_control_change()
        self._show_status(f"已删除上位机数据文件：{len(rows)} 个")

    def _export_active(self) -> None:
        plot = self.frequency_plot if self.tabs.currentIndex() == 0 else self.log_plot
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前上位机数据图",
            str(self._last_directory / "vibration_plot.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_plot_csv(plot, path)
        self._show_status(f"已导出 {Path(path).name}")


class TraceAnalysisPage(DiagnosticPage):
    IDE_SUFFIX_CATEGORIES = ("Prox", "FB", "ACC", "POS")
    TRACE_FILE_LIST_MIN_HEIGHT = 90
    TRANS_FILE_LIST_MIN_HEIGHT = 50
    TRANS_INFO_LINE_COUNT = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[TraceAnalysisFile] = []
        self._updating_controls = False
        self._suppress_auto_plot = False
        self._ide_ranges: dict[str, tuple[int, int]] = {}
        self._hac_ranges: dict[str, tuple[int, int]] = {}
        self._cangfu_ranges: dict[str, tuple[int, int]] = {}
        self._ide_trans_frequency_ranges: dict[str, tuple[float, float]] = {}
        self._ide_trans_sampling_settings: dict[str, tuple[float, float]] = {}
        self._hac_trans_frequency_ranges: dict[str, tuple[float, float]] = {}
        self._plot_windows: list[QtWidgets.QDialog] = []
        self._last_trace_file_index: int | None = None
        self._rename_edit_autofill_text = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(5)
        self.controls_layout = controls_layout

        data_group, data_layout = create_group_box("1. 数据", layout_type=QtWidgets.QVBoxLayout)
        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.delete_button = QtWidgets.QPushButton("删除")
        self.clear_button = QtWidgets.QPushButton("清空图像")
        set_button_role(self.load_button, "primary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.clear_button, "secondary")
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.clear_button)
        data_layout.addLayout(button_row)

        self.current_file_edit = QtWidgets.QLineEdit("未选择文件")
        self.current_file_edit.setReadOnly(True)
        data_layout.addWidget(self.current_file_edit)

        rename_row = QtWidgets.QHBoxLayout()
        rename_row.setContentsMargins(0, 0, 0, 0)
        rename_row.addWidget(QtWidgets.QLabel("重命名"))
        self.rename_edit = QtWidgets.QLineEdit()
        self.rename_edit.setPlaceholderText("当前数据名称")
        self.rename_edit.setEnabled(False)
        rename_row.addWidget(self.rename_edit, 1)
        data_layout.addLayout(rename_row)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(self.TRACE_FILE_LIST_MIN_HEIGHT)
        data_layout.addWidget(self.file_list, 1)
        controls_layout.addWidget(data_group)

        self.settings_stack = QtWidgets.QStackedWidget()
        self.ide_settings_group = self._build_ide_settings_group()
        self.hac_settings_group = self._build_hac_settings_group()
        self.cangfu_settings_group = self._build_cangfu_settings_group()
        self.ide_trans_settings_group = self._build_ide_trans_settings_group()
        self.hac_trans_settings_group = self._build_hac_trans_settings_group()
        self.settings_stack.addWidget(self.ide_settings_group)
        self.settings_stack.addWidget(self.hac_settings_group)
        self.settings_stack.addWidget(self.cangfu_settings_group)
        self.settings_stack.addWidget(self.ide_trans_settings_group)
        self.settings_stack.addWidget(self.hac_trans_settings_group)
        controls_layout.addWidget(self.settings_stack, 1)

        action_group, action_layout = create_group_box("3. 操作", layout_type=QtWidgets.QGridLayout)
        self.action_group = action_group
        action_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        action_layout.setContentsMargins(8, 16, 8, 8)
        action_layout.setVerticalSpacing(4)
        self.demean_check = create_toggle_button("去均值")
        self.hold_check = create_toggle_button("保持曲线")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")
        action_layout.addWidget(self.plot_button, 0, 0)
        action_layout.addWidget(self.export_button, 0, 1)
        action_layout.addWidget(self.demean_check, 1, 0)
        action_layout.addWidget(self.hold_check, 1, 1)
        action_layout.setColumnStretch(0, 1)
        action_layout.setColumnStretch(1, 1)
        controls_layout.addWidget(action_group)

        self.tabs = QtWidgets.QTabWidget()
        self.ide_tab = QtWidgets.QWidget()
        ide_layout = QtWidgets.QVBoxLayout(self.ide_tab)
        ide_layout.setContentsMargins(6, 6, 6, 6)
        ide_layout.setSpacing(5)
        ide_header = QtWidgets.QHBoxLayout()
        ide_header.setContentsMargins(0, 0, 0, 0)
        self.ide_selected_label = QtWidgets.QLabel("当前 IDE 文件：未选择")
        self.ide_context_label = QtWidgets.QLabel("模式 / X 轴 / 范围 / 采样率")
        for label in (self.ide_selected_label, self.ide_context_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        ide_text = QtWidgets.QVBoxLayout()
        ide_text.setContentsMargins(0, 0, 0, 0)
        ide_text.addWidget(self.ide_selected_label)
        ide_text.addWidget(self.ide_context_label)
        ide_header.addLayout(ide_text, 1)
        self.ide_time_window_button = QtWidgets.QPushButton("时域图")
        self.ide_psd_window_button = QtWidgets.QPushButton("PSD图")
        set_button_role(self.ide_time_window_button, "secondary")
        set_button_role(self.ide_psd_window_button, "secondary")
        ide_header.addWidget(self.ide_time_window_button)
        ide_header.addWidget(self.ide_psd_window_button)
        ide_layout.addLayout(ide_header)
        ide_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.ide_time_plot = self._create_plot_widget("IDE 时域")
        self.ide_psd_plot = self._create_plot_widget("IDE PSD")
        ide_splitter.addWidget(self.ide_time_plot)
        ide_splitter.addWidget(self.ide_psd_plot)
        ide_splitter.setStretchFactor(0, 1)
        ide_splitter.setStretchFactor(1, 1)
        ide_layout.addWidget(ide_splitter, 1)

        self.hac_tab = QtWidgets.QWidget()
        hac_layout = QtWidgets.QVBoxLayout(self.hac_tab)
        hac_layout.setContentsMargins(6, 6, 6, 6)
        hac_layout.setSpacing(5)
        self.hac_selected_label = QtWidgets.QLabel("当前 HAC 文件：未选择")
        self.hac_context_label = QtWidgets.QLabel("预设 / 模式 / 范围 / X 轴")
        for label in (self.hac_selected_label, self.hac_context_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        hac_header = QtWidgets.QHBoxLayout()
        hac_header.setContentsMargins(0, 0, 0, 0)
        hac_text = QtWidgets.QVBoxLayout()
        hac_text.setContentsMargins(0, 0, 0, 0)
        hac_text.addWidget(self.hac_selected_label)
        hac_text.addWidget(self.hac_context_label)
        hac_header.addLayout(hac_text, 1)
        self.hac_window_button = QtWidgets.QPushButton("HAC图")
        set_button_role(self.hac_window_button, "secondary")
        hac_header.addWidget(self.hac_window_button)
        hac_layout.addLayout(hac_header)
        self.hac_plot = self._create_plot_widget("HAC 时域")
        hac_layout.addWidget(self.hac_plot, 1)
        self.cangfu_tab = QtWidgets.QWidget()
        cangfu_layout = QtWidgets.QVBoxLayout(self.cangfu_tab)
        cangfu_layout.setContentsMargins(6, 6, 6, 6)
        cangfu_layout.setSpacing(5)
        self.cangfu_selected_label = QtWidgets.QLabel("当前 Cangfu 文件：未选择")
        self.cangfu_context_label = QtWidgets.QLabel("模式 / X 轴 / 范围 / 采样率")
        for label in (self.cangfu_selected_label, self.cangfu_context_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        cangfu_header = QtWidgets.QHBoxLayout()
        cangfu_header.setContentsMargins(0, 0, 0, 0)
        cangfu_text = QtWidgets.QVBoxLayout()
        cangfu_text.setContentsMargins(0, 0, 0, 0)
        cangfu_text.addWidget(self.cangfu_selected_label)
        cangfu_text.addWidget(self.cangfu_context_label)
        cangfu_header.addLayout(cangfu_text, 1)
        self.cangfu_time_window_button = QtWidgets.QPushButton("时域图")
        self.cangfu_psd_window_button = QtWidgets.QPushButton("PSD图")
        set_button_role(self.cangfu_time_window_button, "secondary")
        set_button_role(self.cangfu_psd_window_button, "secondary")
        cangfu_header.addWidget(self.cangfu_time_window_button)
        cangfu_header.addWidget(self.cangfu_psd_window_button)
        cangfu_layout.addLayout(cangfu_header)
        cangfu_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.cangfu_time_plot = self._create_plot_widget("Cangfu Trace 时域")
        self.cangfu_psd_plot = self._create_plot_widget("Cangfu Trace PSD")
        cangfu_splitter.addWidget(self.cangfu_time_plot)
        cangfu_splitter.addWidget(self.cangfu_psd_plot)
        cangfu_splitter.setStretchFactor(0, 1)
        cangfu_splitter.setStretchFactor(1, 1)
        cangfu_layout.addWidget(cangfu_splitter, 1)

        self.ide_trans_tab = QtWidgets.QWidget()
        ide_trans_layout = QtWidgets.QVBoxLayout(self.ide_trans_tab)
        ide_trans_layout.setContentsMargins(6, 6, 6, 6)
        ide_trans_layout.setSpacing(5)
        self.ide_trans_selected_label = QtWidgets.QLabel("当前 IDE Trans 文件：未选择")
        self.ide_trans_context_label = QtWidgets.QLabel("采样率 / 频率分辨率 / 频率范围")
        for label in (self.ide_trans_selected_label, self.ide_trans_context_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        ide_trans_layout.addWidget(self.ide_trans_selected_label)
        ide_trans_layout.addWidget(self.ide_trans_context_label)

        self.ide_trans_time_plot = self._create_plot_widget("IDE Trans 时域")
        self.ide_trans_power_plot = self._create_plot_widget("IDE Trans 功率谱")
        self.ide_trans_magnitude_plot = self._create_plot_widget("IDE Trans 传递函数幅值")
        self.ide_trans_phase_plot = self._create_plot_widget("IDE Trans 相位")
        self.ide_trans_coherence_plot = self._create_plot_widget("IDE Trans 相干性")

        self.ide_trans_view_tabs = QtWidgets.QTabWidget()

        def add_plot_window_button_row(target_layout, *buttons: QtWidgets.QPushButton) -> None:
            button_row = QtWidgets.QHBoxLayout()
            button_row.setContentsMargins(0, 0, 0, 0)
            button_row.setSpacing(5)
            button_row.addStretch(1)
            for button in buttons:
                button_row.addWidget(button)
            target_layout.addLayout(button_row)

        time_power_page = QtWidgets.QWidget()
        time_power_layout = QtWidgets.QVBoxLayout(time_power_page)
        time_power_layout.setContentsMargins(0, 0, 0, 0)
        time_power_layout.setSpacing(4)
        add_plot_window_button_row(
            time_power_layout,
            self.ide_trans_time_window_button,
            self.ide_trans_power_window_button,
        )
        time_power_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        time_power_splitter.addWidget(self.ide_trans_time_plot)
        time_power_splitter.addWidget(self.ide_trans_power_plot)
        time_power_splitter.setStretchFactor(0, 1)
        time_power_splitter.setStretchFactor(1, 1)
        time_power_layout.addWidget(time_power_splitter)

        transfer_page = QtWidgets.QWidget()
        transfer_layout = QtWidgets.QVBoxLayout(transfer_page)
        transfer_layout.setContentsMargins(0, 0, 0, 0)
        transfer_layout.setSpacing(4)
        add_plot_window_button_row(
            transfer_layout,
            self.ide_trans_magnitude_window_button,
            self.ide_trans_phase_window_button,
        )
        transfer_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        transfer_splitter.addWidget(self.ide_trans_magnitude_plot)
        transfer_splitter.addWidget(self.ide_trans_phase_plot)
        transfer_splitter.setStretchFactor(0, 1)
        transfer_splitter.setStretchFactor(1, 1)
        transfer_layout.addWidget(transfer_splitter)

        coherence_page = QtWidgets.QWidget()
        coherence_layout = QtWidgets.QVBoxLayout(coherence_page)
        coherence_layout.setContentsMargins(0, 0, 0, 0)
        coherence_layout.setSpacing(4)
        add_plot_window_button_row(coherence_layout, self.ide_trans_coherence_window_button)
        coherence_layout.addWidget(self.ide_trans_coherence_plot)

        self.ide_trans_view_tabs.addTab(time_power_page, "时域 / 功率谱")
        self.ide_trans_view_tabs.addTab(transfer_page, "传函 / 相位")
        self.ide_trans_view_tabs.addTab(coherence_page, "相干性")
        ide_trans_layout.addWidget(self.ide_trans_view_tabs, 1)

        self.hac_trans_tab = QtWidgets.QWidget()
        hac_trans_layout = QtWidgets.QVBoxLayout(self.hac_trans_tab)
        hac_trans_layout.setContentsMargins(6, 6, 6, 6)
        hac_trans_layout.setSpacing(5)
        self.hac_trans_selected_label = QtWidgets.QLabel("当前 HAC Trans 文件：未选择")
        self.hac_trans_context_label = QtWidgets.QLabel("采样率 / 频率分辨率 / 频率范围")
        for label in (self.hac_trans_selected_label, self.hac_trans_context_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        hac_trans_layout.addWidget(self.hac_trans_selected_label)
        hac_trans_layout.addWidget(self.hac_trans_context_label)

        self.hac_trans_time_plot = self._create_plot_widget("HAC Trans 时域")
        self.hac_trans_magnitude_plot = self._create_plot_widget("HAC Trans 传递函数幅值")
        self.hac_trans_phase_plot = self._create_plot_widget("HAC Trans 相位")
        self.hac_trans_coherence_plot = self._create_plot_widget("HAC Trans 相干性")
        self.hac_trans_view_tabs = QtWidgets.QTabWidget()

        hac_time_page = QtWidgets.QWidget()
        hac_time_layout = QtWidgets.QVBoxLayout(hac_time_page)
        hac_time_layout.setContentsMargins(0, 0, 0, 0)
        hac_time_layout.setSpacing(4)
        add_plot_window_button_row(hac_time_layout, self.hac_trans_time_window_button)
        hac_time_layout.addWidget(self.hac_trans_time_plot)

        hac_transfer_page = QtWidgets.QWidget()
        hac_transfer_layout = QtWidgets.QVBoxLayout(hac_transfer_page)
        hac_transfer_layout.setContentsMargins(0, 0, 0, 0)
        hac_transfer_layout.setSpacing(4)
        add_plot_window_button_row(
            hac_transfer_layout,
            self.hac_trans_magnitude_window_button,
            self.hac_trans_phase_window_button,
        )
        hac_transfer_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        hac_transfer_splitter.addWidget(self.hac_trans_magnitude_plot)
        hac_transfer_splitter.addWidget(self.hac_trans_phase_plot)
        hac_transfer_splitter.setStretchFactor(0, 1)
        hac_transfer_splitter.setStretchFactor(1, 1)
        hac_transfer_layout.addWidget(hac_transfer_splitter)

        hac_coherence_page = QtWidgets.QWidget()
        hac_coherence_layout = QtWidgets.QVBoxLayout(hac_coherence_page)
        hac_coherence_layout.setContentsMargins(0, 0, 0, 0)
        hac_coherence_layout.setSpacing(4)
        add_plot_window_button_row(hac_coherence_layout, self.hac_trans_coherence_window_button)
        hac_coherence_layout.addWidget(self.hac_trans_coherence_plot)

        self.hac_trans_view_tabs.addTab(hac_time_page, "激励 / 响应")
        self.hac_trans_view_tabs.addTab(hac_transfer_page, "传函 / 相位")
        self.hac_trans_view_tabs.addTab(hac_coherence_page, "相干性")
        hac_trans_layout.addWidget(self.hac_trans_view_tabs, 1)

        self.tabs.addTab(self.ide_tab, "IDE Trace")
        self.tabs.addTab(self.hac_tab, "HAC Trace")
        self.tabs.addTab(self.cangfu_tab, "Cangfu Trace")
        self.tabs.addTab(self.ide_trans_tab, "IDE Trans")
        self.tabs.addTab(self.hac_trans_tab, "HAC Trans")

        controls_layout.removeWidget(action_group)
        self.controls_scroll = create_control_scroll_area(
            controls,
            minimum_width=0,
            maximum_width=16_777_215,
        )
        self.controls_scroll.setMinimumHeight(0)
        scroll_policy = self.controls_scroll.sizePolicy()
        scroll_policy.setVerticalPolicy(QtWidgets.QSizePolicy.Ignored)
        self.controls_scroll.setSizePolicy(scroll_policy)
        self.controls_column = QtWidgets.QWidget()
        configure_control_panel(self.controls_column)
        self.controls_column.setMinimumWidth(260)
        self.controls_column.setMaximumWidth(300)
        controls_column_layout = QtWidgets.QVBoxLayout(self.controls_column)
        controls_column_layout.setContentsMargins(0, 0, 0, 0)
        controls_column_layout.setSpacing(5)
        controls_column_layout.addWidget(self.controls_scroll, 1)
        controls_column_layout.addWidget(action_group, 0)
        controls_column_layout.setStretch(0, 1)
        controls_column_layout.setStretch(1, 0)
        layout.addWidget(self.controls_column)
        layout.addWidget(self.tabs, 1)

        self._install_legacy_aliases()

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear_plots)
        self.file_list.currentRowChanged.connect(lambda _row: self._on_file_selection_changed())
        self.rename_edit.editingFinished.connect(self._rename_current_file_from_editor)
        self.rename_edit.returnPressed.connect(self._rename_current_file_confirmed)
        self.ide_x_axis_combo.currentIndexChanged.connect(lambda _index: self._auto_plot_from_control_change())
        self.ide_plot_mode_combo.currentIndexChanged.connect(lambda _index: self._on_plot_mode_changed())
        self.ide_range_start.valueChanged.connect(lambda _value: self._on_range_changed("ide"))
        self.ide_range_end.valueChanged.connect(lambda _value: self._on_range_changed("ide"))
        self.ide_channel_list.itemSelectionChanged.connect(self._auto_plot_from_control_change)
        self.ide_eu_table.itemChanged.connect(self._on_ide_eu_item_changed)
        self.ide_suffix_apply_button.clicked.connect(self._apply_ide_suffix_eu)
        self.hac_preset_combo.currentIndexChanged.connect(lambda _index: self._on_hac_preset_changed())
        self.hac_plot_mode_combo.currentIndexChanged.connect(lambda _index: self._on_plot_mode_changed())
        self.hac_range_start.valueChanged.connect(lambda _value: self._on_range_changed("hac"))
        self.hac_range_end.valueChanged.connect(lambda _value: self._on_range_changed("hac"))
        self.hac_channel_list.itemSelectionChanged.connect(self._auto_plot_from_control_change)
        self.cangfu_x_axis_combo.currentIndexChanged.connect(lambda _index: self._auto_plot_from_control_change())
        self.cangfu_plot_mode_combo.currentIndexChanged.connect(lambda _index: self._on_plot_mode_changed())
        self.cangfu_range_start.valueChanged.connect(lambda _value: self._on_range_changed("cangfu"))
        self.cangfu_range_end.valueChanged.connect(lambda _value: self._on_range_changed("cangfu"))
        self.cangfu_channel_list.itemSelectionChanged.connect(self._auto_plot_from_control_change)
        self.ide_trans_frequency_min.editingFinished.connect(self._on_ide_trans_frequency_changed)
        self.ide_trans_frequency_max.editingFinished.connect(self._on_ide_trans_frequency_changed)
        self.ide_trans_sample_frequency.editingFinished.connect(self._on_ide_trans_sampling_changed)
        self.ide_trans_update_rate.editingFinished.connect(self._on_ide_trans_sampling_changed)
        self.ide_trans_update_rate_reset.clicked.connect(self._reset_ide_trans_update_rate)
        self.hac_trans_frequency_min.editingFinished.connect(self._on_hac_trans_frequency_changed)
        self.hac_trans_frequency_max.editingFinished.connect(self._on_hac_trans_frequency_changed)
        self.demean_check.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        self.tabs.currentChanged.connect(lambda _index: self._on_tab_changed())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)
        self.ide_time_window_button.clicked.connect(
            lambda: self._open_plot_window(self.ide_time_plot, "IDE 时域图")
        )
        self.ide_psd_window_button.clicked.connect(
            lambda: self._open_plot_window(self.ide_psd_plot, "IDE PSD 图")
        )
        self.hac_window_button.clicked.connect(
            lambda: self._open_plot_window(self.hac_plot, "HAC 时域图")
        )
        self.cangfu_time_window_button.clicked.connect(
            lambda: self._open_plot_window(self.cangfu_time_plot, "Cangfu Trace 时域图")
        )
        self.cangfu_psd_window_button.clicked.connect(
            lambda: self._open_plot_window(self.cangfu_psd_plot, "Cangfu Trace PSD 图")
        )
        for button, plot, title in (
            (self.ide_trans_time_window_button, self.ide_trans_time_plot, "IDE Trans 时域图"),
            (self.ide_trans_power_window_button, self.ide_trans_power_plot, "IDE Trans 功率谱图"),
            (self.ide_trans_magnitude_window_button, self.ide_trans_magnitude_plot, "IDE Trans 传递函数幅值图"),
            (self.ide_trans_phase_window_button, self.ide_trans_phase_plot, "IDE Trans 相位图"),
            (self.ide_trans_coherence_window_button, self.ide_trans_coherence_plot, "IDE Trans 相干性图"),
        ):
            button.clicked.connect(lambda _checked=False, source=plot, name=title: self._open_plot_window(source, name))
        for button, plot, title in (
            (self.hac_trans_time_window_button, self.hac_trans_time_plot, "HAC Trans 激励/响应图"),
            (self.hac_trans_magnitude_window_button, self.hac_trans_magnitude_plot, "HAC Trans 传递函数幅值图"),
            (self.hac_trans_phase_window_button, self.hac_trans_phase_plot, "HAC Trans 相位图"),
            (self.hac_trans_coherence_window_button, self.hac_trans_coherence_plot, "HAC Trans 相干性图"),
        ):
            button.clicked.connect(lambda _checked=False, source=plot, name=title: self._open_plot_window(source, name))
        self._update_settings_stack()
        self._sync_hold_availability()

    def _build_ide_settings_group(self) -> QtWidgets.QGroupBox:
        group, layout = create_group_box("2. IDE Trace 设置")
        self.ide_x_axis_combo = QtWidgets.QComboBox()
        self.ide_x_axis_combo.addItem("样本序号", "sample")
        self.ide_x_axis_combo.addItem("时间 (s)", "time")
        self.ide_plot_mode_combo = QtWidgets.QComboBox()
        self.ide_plot_mode_combo.addItems(["叠加", "子图"])
        self.ide_range_start = QtWidgets.QSpinBox()
        self.ide_range_start.setRange(1, 2_000_000_000)
        self.ide_range_end = QtWidgets.QSpinBox()
        self.ide_range_end.setRange(1, 2_000_000_000)
        for spin in (self.ide_range_start, self.ide_range_end):
            spin.setMinimumWidth(72)
            spin.setMaximumWidth(96)
        self.ide_channel_list = QtWidgets.QListWidget()
        self.ide_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.ide_channel_list.setAlternatingRowColors(True)
        self.ide_channel_list.setMinimumHeight(78)

        self.ide_eu_table = QtWidgets.QTableWidget(0, 3)
        self.ide_eu_table.setHorizontalHeaderLabels(["通道", "工程系数", "启用"])
        configure_data_table(self.ide_eu_table, minimum_height=78, maximum_height=120)
        self.ide_eu_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.ide_eu_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
        self.ide_eu_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        self.ide_eu_table.horizontalHeader().resizeSection(1, 72)
        self.ide_eu_table.horizontalHeader().resizeSection(2, 44)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(5)
        range_row.addWidget(self.ide_range_start)
        range_row.addWidget(QtWidgets.QLabel("至"))
        range_row.addWidget(self.ide_range_end)

        suffix_widget = QtWidgets.QWidget()
        suffix_layout = QtWidgets.QGridLayout(suffix_widget)
        suffix_layout.setContentsMargins(0, 0, 0, 0)
        suffix_layout.setHorizontalSpacing(5)
        suffix_layout.setVerticalSpacing(4)
        self.ide_suffix_edits: dict[str, QtWidgets.QLineEdit] = {}
        for column, category in enumerate(self.IDE_SUFFIX_CATEGORIES):
            label = QtWidgets.QLabel(f"{category}:")
            edit = QtWidgets.QLineEdit()
            edit.setMinimumWidth(42)
            edit.setMaximumWidth(66)
            self.ide_suffix_edits[category] = edit
            suffix_layout.addWidget(label, column // 2, (column % 2) * 2)
            suffix_layout.addWidget(edit, column // 2, (column % 2) * 2 + 1)
        self.ide_suffix_apply_button = QtWidgets.QPushButton("按后缀应用")
        set_button_role(self.ide_suffix_apply_button, "secondary")
        suffix_layout.addWidget(self.ide_suffix_apply_button, 2, 0, 1, 4)

        layout.addWidget(QtWidgets.QLabel("X 轴"), 0, 0)
        layout.addWidget(self.ide_x_axis_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("绘图模式"), 1, 0)
        layout.addWidget(self.ide_plot_mode_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("样本范围"), 2, 0)
        layout.addLayout(range_row, 2, 1)
        layout.addWidget(QtWidgets.QLabel("通道"), 3, 0, QtCore.Qt.AlignTop)
        layout.addWidget(self.ide_channel_list, 3, 1)
        layout.addWidget(QtWidgets.QLabel("按后缀工程系数"), 4, 0, 1, 2)
        layout.addWidget(suffix_widget, 5, 0, 1, 2)
        layout.addWidget(QtWidgets.QLabel("工程单位"), 6, 0, 1, 2)
        layout.addWidget(self.ide_eu_table, 7, 0, 1, 2)
        layout.setColumnMinimumWidth(0, 62)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(3, 1)
        return group

    def _build_hac_settings_group(self) -> QtWidgets.QGroupBox:
        group, layout = create_group_box("2. HAC Trace 设置")
        self.hac_preset_combo = QtWidgets.QComboBox()
        self.hac_plot_mode_combo = QtWidgets.QComboBox()
        self.hac_plot_mode_combo.addItems(["叠加", "子图"])
        self.hac_range_start = QtWidgets.QSpinBox()
        self.hac_range_start.setRange(1, 2_000_000_000)
        self.hac_range_end = QtWidgets.QSpinBox()
        self.hac_range_end.setRange(1, 2_000_000_000)
        for spin in (self.hac_range_start, self.hac_range_end):
            spin.setMinimumWidth(72)
            spin.setMaximumWidth(96)
        self.hac_channel_list = QtWidgets.QListWidget()
        self.hac_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.hac_channel_list.setAlternatingRowColors(True)
        self.hac_channel_list.setMinimumHeight(120)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(5)
        range_row.addWidget(self.hac_range_start)
        range_row.addWidget(QtWidgets.QLabel("至"))
        range_row.addWidget(self.hac_range_end)

        layout.addWidget(QtWidgets.QLabel("预设分组"), 0, 0)
        layout.addWidget(self.hac_preset_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("绘图模式"), 1, 0)
        layout.addWidget(self.hac_plot_mode_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("样本范围"), 2, 0)
        layout.addLayout(range_row, 2, 1)
        layout.addWidget(QtWidgets.QLabel("通道"), 3, 0, QtCore.Qt.AlignTop)
        layout.addWidget(self.hac_channel_list, 3, 1)
        layout.setRowStretch(3, 1)
        return group

    def _build_cangfu_settings_group(self) -> QtWidgets.QGroupBox:
        group, layout = create_group_box("2. Cangfu Trace 设置")
        self.cangfu_x_axis_combo = QtWidgets.QComboBox()
        self.cangfu_x_axis_combo.addItem("样本序号", "sample")
        self.cangfu_x_axis_combo.addItem("时间 (s)", "time")
        self.cangfu_plot_mode_combo = QtWidgets.QComboBox()
        self.cangfu_plot_mode_combo.addItems(["叠加", "子图"])
        self.cangfu_range_start = QtWidgets.QSpinBox()
        self.cangfu_range_start.setRange(1, 2_000_000_000)
        self.cangfu_range_end = QtWidgets.QSpinBox()
        self.cangfu_range_end.setRange(1, 2_000_000_000)
        for spin in (self.cangfu_range_start, self.cangfu_range_end):
            spin.setMinimumWidth(72)
            spin.setMaximumWidth(96)
        self.cangfu_channel_list = QtWidgets.QListWidget()
        self.cangfu_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.cangfu_channel_list.setAlternatingRowColors(True)
        self.cangfu_channel_list.setMinimumHeight(120)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(5)
        range_row.addWidget(self.cangfu_range_start)
        range_row.addWidget(QtWidgets.QLabel("至"))
        range_row.addWidget(self.cangfu_range_end)

        layout.addWidget(QtWidgets.QLabel("X 轴"), 0, 0)
        layout.addWidget(self.cangfu_x_axis_combo, 0, 1)
        layout.addWidget(QtWidgets.QLabel("绘图模式"), 1, 0)
        layout.addWidget(self.cangfu_plot_mode_combo, 1, 1)
        layout.addWidget(QtWidgets.QLabel("样本范围"), 2, 0)
        layout.addLayout(range_row, 2, 1)
        layout.addWidget(QtWidgets.QLabel("通道"), 3, 0, QtCore.Qt.AlignTop)
        layout.addWidget(self.cangfu_channel_list, 3, 1)
        layout.setRowStretch(3, 1)
        return group

    def _build_ide_trans_settings_group(self) -> QtWidgets.QGroupBox:
        group, layout = create_group_box("2. IDE Trans 设置")
        self.ide_trans_frequency_min = QtWidgets.QDoubleSpinBox()
        self.ide_trans_frequency_max = QtWidgets.QDoubleSpinBox()
        for spin in (self.ide_trans_frequency_min, self.ide_trans_frequency_max):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(4)
            spin.setSuffix(" Hz")
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(86)
        self.ide_trans_frequency_min.setValue(0.1)
        self.ide_trans_frequency_max.setValue(400.0)

        self.ide_trans_sample_frequency = QtWidgets.QDoubleSpinBox()
        self.ide_trans_sample_frequency.setRange(0.000001, 100_000_000.0)
        self.ide_trans_sample_frequency.setDecimals(1)
        self.ide_trans_sample_frequency.setSuffix(" Hz")
        self.ide_trans_sample_frequency.setKeyboardTracking(False)
        self.ide_trans_sample_frequency.setFixedWidth(76)
        self.ide_trans_update_rate = QtWidgets.QSpinBox()
        self.ide_trans_update_rate.setRange(1, 1_000_000)
        self.ide_trans_update_rate.setKeyboardTracking(False)
        self.ide_trans_update_rate.setFixedWidth(52)
        self.ide_trans_update_rate_reset = QtWidgets.QPushButton("自动")
        self.ide_trans_update_rate_reset.setFixedWidth(42)
        set_button_role(self.ide_trans_update_rate_reset, "secondary")

        frequency_row = QtWidgets.QHBoxLayout()
        frequency_row.setContentsMargins(0, 0, 0, 0)
        frequency_row.setSpacing(5)
        frequency_row.addWidget(self.ide_trans_frequency_min)
        frequency_row.addWidget(QtWidgets.QLabel("至"))
        frequency_row.addWidget(self.ide_trans_frequency_max)

        update_rate_row = QtWidgets.QHBoxLayout()
        update_rate_row.setContentsMargins(0, 0, 0, 0)
        update_rate_row.setSpacing(5)
        update_rate_row.addWidget(self.ide_trans_sample_frequency, 1)
        update_rate_row.addWidget(QtWidgets.QLabel("÷"))
        update_rate_row.addWidget(self.ide_trans_update_rate)
        update_rate_row.addWidget(self.ide_trans_update_rate_reset)

        self.ide_trans_info_label = QtWidgets.QLabel("请选择 SDM 文件")
        self._configure_trans_info_label(self.ide_trans_info_label)
        self.ide_trans_info_label.setFixedHeight(self.ide_trans_info_label.fontMetrics().lineSpacing() + 2)
        self.ide_trans_time_window_button = QtWidgets.QPushButton("时域图")
        self.ide_trans_power_window_button = QtWidgets.QPushButton("功率谱")
        self.ide_trans_magnitude_window_button = QtWidgets.QPushButton("传函幅值")
        self.ide_trans_phase_window_button = QtWidgets.QPushButton("相位")
        self.ide_trans_coherence_window_button = QtWidgets.QPushButton("相干性")
        for button in (
            self.ide_trans_time_window_button,
            self.ide_trans_power_window_button,
            self.ide_trans_magnitude_window_button,
            self.ide_trans_phase_window_button,
            self.ide_trans_coherence_window_button,
        ):
            set_button_role(button, "secondary")

        layout.addWidget(QtWidgets.QLabel("频率范围"), 0, 0)
        layout.addLayout(frequency_row, 0, 1)
        layout.addWidget(QtWidgets.QLabel("采样/更新"), 1, 0)
        layout.addLayout(update_rate_row, 1, 1)
        layout.addWidget(self.ide_trans_info_label, 2, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        return group

    def _build_hac_trans_settings_group(self) -> QtWidgets.QGroupBox:
        group, layout = create_group_box("2. HAC Trans 设置")
        self.hac_trans_frequency_min = QtWidgets.QDoubleSpinBox()
        self.hac_trans_frequency_max = QtWidgets.QDoubleSpinBox()
        for spin in (self.hac_trans_frequency_min, self.hac_trans_frequency_max):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(4)
            spin.setSuffix(" Hz")
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(78)
        self.hac_trans_frequency_min.setValue(0.1)
        self.hac_trans_frequency_max.setValue(625.0)

        frequency_row = QtWidgets.QHBoxLayout()
        frequency_row.setContentsMargins(0, 0, 0, 0)
        frequency_row.setSpacing(5)
        frequency_row.addWidget(self.hac_trans_frequency_min)
        frequency_row.addWidget(QtWidgets.QLabel("至"))
        frequency_row.addWidget(self.hac_trans_frequency_max)

        self.hac_trans_info_label = QtWidgets.QLabel("请选择 HAC 传函 CSV 文件")
        self._configure_trans_info_label(self.hac_trans_info_label)
        self.hac_trans_time_window_button = QtWidgets.QPushButton("激励/响应")
        self.hac_trans_magnitude_window_button = QtWidgets.QPushButton("传函幅值")
        self.hac_trans_phase_window_button = QtWidgets.QPushButton("相位")
        self.hac_trans_coherence_window_button = QtWidgets.QPushButton("相干性")
        for button in (
            self.hac_trans_time_window_button,
            self.hac_trans_magnitude_window_button,
            self.hac_trans_phase_window_button,
            self.hac_trans_coherence_window_button,
        ):
            set_button_role(button, "secondary")

        layout.addWidget(QtWidgets.QLabel("频率范围"), 0, 0)
        layout.addLayout(frequency_row, 0, 1)
        layout.addWidget(self.hac_trans_info_label, 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        return group

    def _configure_trans_info_label(self, label: QtWidgets.QLabel) -> None:
        label.setWordWrap(False)
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        line_height = label.fontMetrics().lineSpacing()
        label.setFixedHeight(line_height * self.TRANS_INFO_LINE_COUNT + 6)

    @staticmethod
    def _set_trans_info_text(label: QtWidgets.QLabel, *lines: str) -> None:
        text = "\n".join(lines)
        label.setText(text)
        label.setToolTip(text)

    def _install_legacy_aliases(self) -> None:
        self.x_axis_combo = self.ide_x_axis_combo
        self.plot_mode_combo = self.ide_plot_mode_combo
        self.range_start = self.ide_range_start
        self.range_end = self.ide_range_end
        self.channel_list = self.ide_channel_list
        self.eu_table = self.ide_eu_table

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载减振器软件测试数据",
            str(self._last_directory),
            "测试数据文件 (*.txt *.csv *.dat *.mat *.rpt *.para *.sdm);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        reloaded = 0
        reloaded_rows: set[int] = set()
        trans_target_row: int | None = None
        load_paths = self._dedupe_trace_paths(paths)
        for path in load_paths:
            dataset_key = self._trace_dataset_key(path)
            existing_index = next(
                (
                    index
                    for index, item in enumerate(self.files)
                    if self._trace_dataset_key(item.table.path) == dataset_key
                ),
                None,
            )
            if existing_index is not None:
                existing = self.files[existing_index]
                same_path = self._resolved_path_text(existing.table.path) == self._resolved_path_text(path)
                if not same_path and self._trace_path_priority(existing.table.path) <= self._trace_path_priority(path):
                    continue
            try:
                loaded_file = load_trace_analysis_file(path)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            if existing_index is None:
                self.files.append(loaded_file)
                self.file_list.addItem(self._file_list_text(loaded_file))
                target_row = len(self.files) - 1
                loaded += 1
            else:
                previous = self.files[existing_index]
                if previous.table.metadata.get("trace_custom_display_name"):
                    loaded_file.table.name = previous.table.name
                    loaded_file.table.metadata["trace_custom_display_name"] = True
                previous_key = self._file_key(previous)
                self._ide_ranges.pop(previous_key, None)
                self._hac_ranges.pop(previous_key, None)
                self._cangfu_ranges.pop(previous_key, None)
                self._ide_trans_frequency_ranges.pop(previous_key, None)
                self._ide_trans_sampling_settings.pop(previous_key, None)
                self._hac_trans_frequency_ranges.pop(previous_key, None)
                self.files[existing_index] = loaded_file
                item = self.file_list.item(existing_index)
                if item is not None:
                    item.setText(self._file_list_text(loaded_file))
                target_row = existing_index
                reloaded += 1
                reloaded_rows.add(existing_index)
            if loaded_file.trace_kind in {"ide_trans", "hac_trans"}:
                trans_target_row = target_row
        self._remember_paths(load_paths or paths)
        self._suppress_auto_plot = True
        if trans_target_row is not None:
            self.file_list.setCurrentRow(trans_target_row)
        elif self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._last_trace_file_index = self._valid_file_index(self.file_list.currentRow())
        self._refresh_controls()
        self._select_default_tab_for_current_file()
        self._suppress_auto_plot = False
        current_row = self.file_list.currentRow()
        if current_row in reloaded_rows:
            current = self.current_file()
            if current is not None:
                self._clear_plots_for_kind(current.trace_kind)
                self.plot_current()
        if reloaded:
            self._show_status(f"已加载 {loaded} 个、重新读取 {reloaded} 个测试文件")
        else:
            self._show_status(f"已加载测试文件：{loaded} 个，请选择通道后点击绘图")

    def _dedupe_trace_paths(self, paths: list[Path]) -> list[Path]:
        selected: dict[tuple[str, str], Path] = {}
        for raw_path in paths:
            path = Path(raw_path)
            key = self._trace_dataset_key(path)
            current = selected.get(key)
            if current is None or self._trace_path_priority(path) <= self._trace_path_priority(current):
                selected[key] = path
        return list(selected.values())

    @staticmethod
    def _resolved_path_text(path: Path) -> str:
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return str(Path(path).absolute()).lower()

    @staticmethod
    def _trace_dataset_key(path: Path) -> tuple[str, str]:
        resolved = Path(path)
        try:
            resolved = resolved.resolve()
        except OSError:
            pass
        if resolved.suffix.lower() in {".txt", ".mat"}:
            return (str(resolved.parent).lower(), resolved.stem.lower())
        return (str(resolved.parent).lower(), resolved.name.lower())

    @staticmethod
    def _trace_path_priority(path: Path) -> int:
        suffix = Path(path).suffix.lower()
        if suffix == ".txt":
            return 0
        if suffix == ".mat":
            return 1
        return 2

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.ide_channel_list.clear()
        self.hac_channel_list.clear()
        self.cangfu_channel_list.clear()
        self.hac_preset_combo.clear()
        self.ide_eu_table.setRowCount(0)
        self._ide_ranges.clear()
        self._hac_ranges.clear()
        self._cangfu_ranges.clear()
        self._ide_trans_frequency_ranges.clear()
        self._ide_trans_sampling_settings.clear()
        self._hac_trans_frequency_ranges.clear()
        self._last_trace_file_index = None
        self._close_plot_windows()
        self.clear_plots(show_status=False)
        self._refresh_controls()
        self._show_status("减振器软件测试数据分析已清空")

    def clear_plots(self, *, show_status: bool = True) -> None:
        for plot in (
            self.ide_time_plot,
            self.ide_psd_plot,
            self.hac_plot,
            self.cangfu_time_plot,
            self.cangfu_psd_plot,
            self.ide_trans_time_plot,
            self.ide_trans_power_plot,
            self.ide_trans_magnitude_plot,
            self.ide_trans_phase_plot,
            self.ide_trans_coherence_plot,
            self.hac_trans_time_plot,
            self.hac_trans_magnitude_plot,
            self.hac_trans_phase_plot,
            self.hac_trans_coherence_plot,
        ):
            self._clear_plot_widget(plot)
        self._close_plot_windows()
        if show_status:
            self._show_status("已清空减振器软件测试数据图像")

    def _clear_plots_for_kind(self, kind: str) -> None:
        groups = {
            "ide_trace": (self.ide_time_plot, self.ide_psd_plot),
            "hac_trace": (self.hac_plot,),
            "cangfu_trace": (self.cangfu_time_plot, self.cangfu_psd_plot),
            "ide_trans": (
                self.ide_trans_time_plot,
                self.ide_trans_power_plot,
                self.ide_trans_magnitude_plot,
                self.ide_trans_phase_plot,
                self.ide_trans_coherence_plot,
            ),
            "hac_trans": (
                self.hac_trans_time_plot,
                self.hac_trans_magnitude_plot,
                self.hac_trans_phase_plot,
                self.hac_trans_coherence_plot,
            ),
        }
        for plot in groups.get(kind, ()):
            self._clear_plot_widget(plot)
        self._close_plot_windows()

    def current_file(self) -> TraceAnalysisFile | None:
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self.files):
            return None
        return self.files[row]

    def plot_current(self) -> None:
        current = self.current_file()
        if current is None:
            self._show_status("未选择测试文件")
            return
        active_kind = self._active_tab_kind()
        if current.trace_kind != active_kind:
            self._show_status(f"当前页面需要选择 {self._kind_label(active_kind)} 文件")
            return
        if active_kind == "hac_trace":
            self._plot_hac_current(current)
        elif active_kind == "cangfu_trace":
            self._plot_cangfu_current(current)
        elif active_kind == "ide_trans":
            self._plot_ide_trans_current(current)
        elif active_kind == "hac_trans":
            self._plot_hac_trans_current(current)
        else:
            self._plot_ide_current(current)

    def _on_file_selection_changed(self) -> None:
        self._commit_ide_eu_table_for_index(self._last_trace_file_index)
        self._last_trace_file_index = self._valid_file_index(self.file_list.currentRow())
        self._refresh_controls()
        self._select_default_tab_for_current_file()
        self._auto_plot_from_control_change()

    def _auto_plot_from_control_change(self) -> None:
        if self._updating_controls or self._suppress_auto_plot or self.current_file() is None:
            return
        self.plot_current()

    def _on_tab_changed(self) -> None:
        self._commit_ide_eu_table_for_index(self._valid_file_index(self.file_list.currentRow()))
        self._update_settings_stack()
        self._sync_hold_availability()
        self._refresh_controls()
        self._auto_plot_from_control_change()

    def _on_plot_mode_changed(self) -> None:
        self._sync_hold_availability()
        self._auto_plot_from_control_change()

    def _on_range_changed(self, kind: str) -> None:
        current = self.current_file()
        if current is not None and current.trace_kind == f"{kind}_trace":
            if kind == "ide":
                ranges = self._ide_ranges
                start_box = self.ide_range_start
                end_box = self.ide_range_end
            elif kind == "hac":
                ranges = self._hac_ranges
                start_box = self.hac_range_start
                end_box = self.hac_range_end
            else:
                ranges = self._cangfu_ranges
                start_box = self.cangfu_range_start
                end_box = self.cangfu_range_end
            ranges[self._file_key(current)] = self._spin_range_values(start_box, end_box)
        self._auto_plot_from_control_change()

    def _on_ide_trans_frequency_changed(self) -> None:
        current = self.current_file()
        if current is not None and current.trace_kind == "ide_trans":
            low = float(self.ide_trans_frequency_min.value())
            high = float(self.ide_trans_frequency_max.value())
            if high < low:
                low, high = high, low
            self._ide_trans_frequency_ranges[self._file_key(current)] = (low, high)
        self._auto_plot_from_control_change()

    def _on_ide_trans_sampling_changed(self) -> None:
        current = self.current_file()
        if current is None or current.trace_kind != "ide_trans":
            return
        sample_frequency = float(self.ide_trans_sample_frequency.value())
        update_rate = float(self.ide_trans_update_rate.value())
        if not np.isfinite(sample_frequency) or sample_frequency <= 0.0:
            return
        if not np.isfinite(update_rate) or update_rate <= 0.0:
            return
        file_key = self._file_key(current)
        self._ide_trans_sampling_settings[file_key] = (sample_frequency, update_rate)
        self._ide_trans_frequency_ranges.pop(file_key, None)
        self._apply_ide_trans_sampling(current, sample_frequency, update_rate)
        self.ide_trans_update_rate_reset.setEnabled(True)
        self._refresh_ide_trans_frequency_controls(current)
        self._auto_plot_from_control_change()

    def _reset_ide_trans_update_rate(self) -> None:
        current = self.current_file()
        if current is None or current.trace_kind != "ide_trans":
            return
        file_key = self._file_key(current)
        self._ide_trans_sampling_settings.pop(file_key, None)
        self._ide_trans_frequency_ranges.pop(file_key, None)
        sample_frequency, update_rate = self._inferred_ide_trans_sampling(current)
        self.ide_trans_sample_frequency.blockSignals(True)
        self.ide_trans_sample_frequency.setValue(sample_frequency)
        self.ide_trans_sample_frequency.blockSignals(False)
        self.ide_trans_update_rate.blockSignals(True)
        self.ide_trans_update_rate.setValue(int(round(update_rate)))
        self.ide_trans_update_rate.blockSignals(False)
        self.ide_trans_update_rate_reset.setEnabled(False)
        self._apply_ide_trans_sampling(current, sample_frequency, update_rate)
        self._refresh_ide_trans_frequency_controls(current)
        self._auto_plot_from_control_change()

    @staticmethod
    def _inferred_ide_trans_sampling(current: TraceAnalysisFile) -> tuple[float, float]:
        metadata = current.table.metadata
        sample_frequency = float(metadata.get("ide_trans_sample_frequency_hz", 0.0) or 0.0)
        update_rate = float(metadata.get("ide_trans_update_rate", 0.0) or 0.0)
        if not np.isfinite(sample_frequency) or sample_frequency <= 0.0:
            sample_frequency = float(metadata.get("ide_trans_inferred_update_rate_hz", 0.0) or 0.0)
            update_rate = 1.0
        if not np.isfinite(sample_frequency) or sample_frequency <= 0.0:
            sample_frequency = float(current.sample_rate)
        if not np.isfinite(update_rate) or update_rate <= 0.0:
            update_rate = 1.0
        return max(sample_frequency, 0.000001), max(update_rate, 0.000001)

    @staticmethod
    def _apply_ide_trans_sampling(
        current: TraceAnalysisFile,
        sample_frequency: float,
        update_rate: float,
    ) -> None:
        metadata = current.table.metadata
        effective_rate = float(sample_frequency) / float(update_rate)
        time_count = max((np.asarray(values).size for values in current.channels.values()), default=0)
        current.sample_rate = effective_rate
        current.time_s = np.arange(time_count, dtype=float) / effective_rate

        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        sample_count = int(metadata.get("ide_trans_sample_count", 0) or 0)
        if sample_count <= 0:
            sample_count = max(time_count, frequency.size * 2, 1)
        df_hz = effective_rate / float(sample_count)
        metadata["ide_trans_df_hz"] = df_hz
        metadata["ide_trans_effective_sample_rate_hz"] = effective_rate
        metadata["ide_trans_frequency_hz"] = np.arange(frequency.size, dtype=float) * df_hz

    def _on_hac_trans_frequency_changed(self) -> None:
        current = self.current_file()
        if current is not None and current.trace_kind == "hac_trans":
            low = float(self.hac_trans_frequency_min.value())
            high = float(self.hac_trans_frequency_max.value())
            if high < low:
                low, high = high, low
            self._hac_trans_frequency_ranges[self._file_key(current)] = (low, high)
        self._auto_plot_from_control_change()

    def _on_hac_preset_changed(self) -> None:
        self._refresh_hac_channels()
        self._auto_plot_from_control_change()

    def _valid_file_index(self, row: int | None) -> int | None:
        if row is None:
            return None
        row = int(row)
        return row if 0 <= row < len(self.files) else None

    def _commit_ide_eu_table_for_index(self, row: int | None) -> None:
        valid_row = self._valid_file_index(row)
        if valid_row is None:
            return
        current = self.files[valid_row]
        if current.trace_kind != "ide_trace" or self.ide_eu_table.rowCount() <= 0:
            return
        enabled = self._ide_enabled_channels(current)
        changed = False
        previous_updating = self._updating_controls
        self._updating_controls = True
        try:
            for table_row in range(self.ide_eu_table.rowCount()):
                name_item = self.ide_eu_table.item(table_row, 0)
                scale_item = self.ide_eu_table.item(table_row, 1)
                enabled_item = self.ide_eu_table.item(table_row, 2)
                if name_item is None:
                    continue
                name = name_item.text()
                if name not in current.channels:
                    continue
                if scale_item is not None:
                    try:
                        value = float(scale_item.text())
                    except ValueError:
                        value = float("nan")
                    if np.isfinite(value) and value != 0.0:
                        if abs(float(current.channel_eu.get(name, 1.0)) - float(value)) > 1e-12:
                            changed = True
                        current.channel_eu[name] = float(value)
                    else:
                        scale_item.setText(f"{current.channel_eu.get(name, 1.0):.8g}")
                if enabled_item is not None:
                    item_enabled = enabled_item.checkState() == QtCore.Qt.Checked
                    if enabled.get(name, True) != item_enabled:
                        changed = True
                    enabled[name] = item_enabled
        finally:
            self._updating_controls = previous_updating
        if changed and valid_row == self._valid_file_index(self.file_list.currentRow()):
            self._refresh_ide_suffix_controls(current)

    def _selected_ide_files_for_eu_batch(self) -> list[TraceAnalysisFile]:
        rows = sorted({index.row() for index in self.file_list.selectedIndexes()})
        targets = [
            self.files[row]
            for row in rows
            if 0 <= row < len(self.files) and self.files[row].trace_kind == "ide_trace"
        ]
        if targets:
            return targets
        current = self.current_file()
        if current is not None and current.trace_kind == "ide_trace":
            return [current]
        return []

    def _set_ide_eu_for_targets(
        self,
        targets: list[TraceAnalysisFile],
        name: str,
        *,
        scale: float | None = None,
        enabled: bool | None = None,
    ) -> int:
        updated = 0
        for target in targets:
            if name not in target.channels:
                continue
            if scale is not None:
                target.channel_eu[name] = float(scale)
            if enabled is not None:
                self._ide_enabled_channels(target)[name] = bool(enabled)
            updated += 1
        return updated

    def _set_ide_suffix_eu_for_targets(
        self,
        targets: list[TraceAnalysisFile],
        parsed: dict[str, float],
    ) -> int:
        updated = 0
        for target in targets:
            for name in target.channels:
                category = self._ide_suffix_category(name)
                if category not in parsed:
                    continue
                target.channel_eu[name] = float(parsed[category])
                updated += 1
        return updated

    def _refresh_controls(self) -> None:
        self._updating_controls = True
        try:
            current = self.current_file()
            self._update_current_file_label(current)
            self._refresh_ide_controls(current if current and current.trace_kind == "ide_trace" else None)
            self._refresh_hac_controls(current if current and current.trace_kind == "hac_trace" else None)
            self._refresh_cangfu_controls(current if current and current.trace_kind == "cangfu_trace" else None)
            self._refresh_ide_trans_controls(current if current and current.trace_kind == "ide_trans" else None)
            self._refresh_hac_trans_controls(current if current and current.trace_kind == "hac_trans" else None)
            self._update_settings_stack()
        finally:
            self._updating_controls = False

    def _selected_time_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        if current.trace_kind == "hac_trace":
            return self._selected_hac_curves(current)
        if current.trace_kind == "cangfu_trace":
            return self._selected_cangfu_curves(current)
        return self._selected_ide_curves(current)

    @staticmethod
    def _curves_with_file_display_name(current: TraceAnalysisFile, curves: list[CurvePair]) -> list[CurvePair]:
        if not current.table.metadata.get("trace_custom_display_name"):
            return curves
        prefix = str(current.table.name).strip()
        if not prefix:
            return curves
        return [
            CurvePair(f"{prefix} | {curve.label}", curve.x, curve.y, curve.x_label, curve.y_label)
            for curve in curves
        ]

    def _selected_ide_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        names = [item.text() for item in self.ide_channel_list.selectedItems()]
        if not names:
            enabled = self._ide_enabled_channels(current)
            names = [name for name in current.channels if enabled.get(name, True)] or list(current.channels)[:1]
        start, end = self._slice_bounds(self.ide_range_start, self.ide_range_end)
        if self.ide_x_axis_combo.currentData() == "time":
            x_full = np.asarray(current.time_s, dtype=float)
            x_label = "时间 (s)"
        else:
            x_full = np.asarray(current.table.metadata.get("sample_index", np.arange(1, current.table.row_count + 1, dtype=float)), dtype=float)
            x_label = "样本序号"
        curves: list[CurvePair] = []
        for name in names:
            y = np.asarray(current.channels.get(name, np.array([], dtype=float)), dtype=float)
            x = np.asarray(x_full[: y.size], dtype=float)
            x = x[start:end]
            y = y[start:end]
            eu = self._eu_scale(name)
            if eu is None:
                continue
            y = y / eu
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            curves.append(CurvePair(name, x, y, x_label, "工程值"))
        return curves

    def _selected_hac_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        selected_items = self.hac_channel_list.selectedItems()
        if not selected_items:
            selected_items = [self.hac_channel_list.item(index) for index in range(self.hac_channel_list.count())]
        selected_indices = [
            int(item.data(QtCore.Qt.UserRole))
            for item in selected_items
            if item is not None and item.data(QtCore.Qt.UserRole) is not None
        ]
        if not selected_indices:
            selected_indices = list(self._selected_hac_group_indices(current))
        start, end = self._slice_bounds(self.hac_range_start, self.hac_range_end)
        if current.time_s.size:
            x_full = np.asarray(current.time_s, dtype=float)
            x_label = "Elapsed Time (s)"
        else:
            x_full = np.asarray(current.table.metadata.get("sample_index", np.arange(1, current.table.row_count + 1, dtype=float)), dtype=float)
            x_label = "样本序号"
        curves: list[CurvePair] = []
        for index in selected_indices:
            if index < 0 or index >= len(current.table.headers):
                continue
            name = current.table.headers[index]
            y = np.asarray(current.channels.get(name, np.array([], dtype=float)), dtype=float)
            x = np.asarray(x_full[: y.size], dtype=float)
            x = x[start:end]
            y = y[start:end]
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            curves.append(CurvePair(name, x, y, x_label, "工程值"))
        return curves

    def _selected_cangfu_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        transfer_pairs = self._cangfu_transfer_pairs(current)
        if transfer_pairs:
            names = {item.text() for item in self.cangfu_channel_list.selectedItems()}
            start, end = self._slice_bounds(self.cangfu_range_start, self.cangfu_range_end)
            curves: list[CurvePair] = []
            for pair in transfer_pairs:
                if names and pair.label not in names:
                    continue
                curves.append(
                    CurvePair(
                        pair.label,
                        np.asarray(pair.x, dtype=float)[start:end],
                        np.asarray(pair.y, dtype=float)[start:end],
                        pair.x_label,
                        pair.y_label,
                    )
                )
            return curves
        names = [item.text() for item in self.cangfu_channel_list.selectedItems()]
        if not names:
            names = list(current.channels)[: min(8, len(current.channels))]
        start, end = self._slice_bounds(self.cangfu_range_start, self.cangfu_range_end)
        if self.cangfu_x_axis_combo.currentData() == "time":
            x_full = np.asarray(current.time_s, dtype=float)
            x_label = "时间 (s)"
        else:
            x_full = np.asarray(current.table.metadata.get("sample_index", np.arange(1, current.table.row_count + 1, dtype=float)), dtype=float)
            x_label = "样本序号"
        curves: list[CurvePair] = []
        for name in names:
            y = np.asarray(current.channels.get(name, np.array([], dtype=float)), dtype=float)
            x = np.asarray(x_full[: y.size], dtype=float)
            x = x[start:end]
            y = y[start:end]
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            curves.append(CurvePair(name, x, y, x_label, "工程值"))
        return curves

    def _psd_curves(self, current: TraceAnalysisFile, curves: list[CurvePair]) -> list[CurvePair]:
        psd_curves: list[CurvePair] = []
        fs = max(float(current.sample_rate), 1.0)
        for curve in curves:
            y = np.asarray(curve.y, dtype=float)
            if y.size < 4:
                continue
            f, pxx = compute_hann_periodogram_psd(y, fs, skip_initial=2)
            psd_curves.append(CurvePair(curve.label, f, pxx, "频率 (Hz)", "PSD"))
        return psd_curves

    def _eu_scale(self, channel_name: str) -> float | None:
        for row in range(self.ide_eu_table.rowCount()):
            name_item = self.ide_eu_table.item(row, 0)
            scale_item = self.ide_eu_table.item(row, 1)
            enabled_item = self.ide_eu_table.item(row, 2)
            if name_item is None or name_item.text() != channel_name:
                continue
            if enabled_item is not None and enabled_item.checkState() != QtCore.Qt.Checked:
                return None
            try:
                value = float(scale_item.text()) if scale_item is not None else 1.0
            except ValueError:
                return 1.0
            return value if np.isfinite(value) and value != 0.0 else 1.0
        return 1.0

    def _plot_ide_current(self, current: TraceAnalysisFile) -> None:
        curves = self._selected_ide_curves(current)
        curves = self._curves_with_file_display_name(current, curves)
        if not curves:
            self._show_status("没有可绘制的 IDE 通道")
            return
        subplots = self.ide_plot_mode_combo.currentIndex() == 1
        self._sync_hold_availability()
        hold = self.hold_check.isChecked() and not subplots
        x_label = curves[0].x_label
        plotted = self._plot_trace_curves_on_widget(
            self.ide_time_plot,
            curves,
            title=f"IDE 时域 - {current.table.name}",
            x_label=x_label,
            y_label="工程值",
            subplots=subplots,
            hold=hold,
        )
        psd_curves = self._psd_curves(current, curves)
        self._plot_trace_curves_on_widget(
            self.ide_psd_plot,
            psd_curves,
            title=f"IDE PSD - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="PSD",
            log_x=True,
            log_y=True,
            subplots=subplots,
            hold=hold,
        )
        self._update_ide_context(current, plotted)
        self._show_status(f"已绘制 IDE 通道：{plotted} 个")

    def _plot_hac_current(self, current: TraceAnalysisFile) -> None:
        curves = self._selected_hac_curves(current)
        curves = self._curves_with_file_display_name(current, curves)
        if not curves:
            self._show_status("没有可绘制的 HAC 通道")
            return
        subplots = self.hac_plot_mode_combo.currentIndex() == 1
        self._sync_hold_availability()
        hold = self.hold_check.isChecked() and not subplots
        x_label = curves[0].x_label
        group_name = self.hac_preset_combo.currentText() or "All Channels"
        plotted = self._plot_trace_curves_on_widget(
            self.hac_plot,
            curves,
            title=f"HAC 时域 - {group_name} | {current.table.name}",
            x_label=x_label,
            y_label="工程值",
            subplots=subplots,
            hold=hold,
        )
        self._update_hac_context(current, plotted)
        self._show_status(f"已绘制 HAC 通道：{plotted} 个")

    def _plot_cangfu_current(self, current: TraceAnalysisFile) -> None:
        transfer_pairs = self._cangfu_transfer_pairs(current)
        if transfer_pairs:
            curves = self._selected_cangfu_curves(current)
            curves = self._curves_with_file_display_name(current, curves)
            if not curves:
                self._show_status("没有可绘制的 Cangfu Trace 传递函数")
                return
            subplots = self.cangfu_plot_mode_combo.currentIndex() == 1
            self._sync_hold_availability()
            hold = self.hold_check.isChecked() and not subplots
            plotted = self._plot_trace_curves_on_widget(
                self.cangfu_time_plot,
                curves,
                title=f"Cangfu Trace 传递函数 - {current.table.name}",
                x_label=curves[0].x_label,
                y_label=curves[0].y_label,
                log_x=True,
                log_y=False,
                subplots=subplots,
                hold=hold,
            )
            self._clear_plot_widget(self.cangfu_psd_plot)
            self.cangfu_psd_plot.setTitle("Cangfu Trace PSD - 传递函数模式不适用")
            self.cangfu_psd_plot.setLabel("bottom", "Frequency (Hz)")
            self.cangfu_psd_plot.setLabel("left", "PSD")
            self._update_cangfu_context(current, plotted)
            self._show_status(f"已绘制 Cangfu Trace 传递函数：{plotted} 条")
            return
        curves = self._selected_cangfu_curves(current)
        curves = self._curves_with_file_display_name(current, curves)
        if not curves:
            self._show_status("没有可绘制的 Cangfu Trace 通道")
            return
        subplots = self.cangfu_plot_mode_combo.currentIndex() == 1
        self._sync_hold_availability()
        hold = self.hold_check.isChecked() and not subplots
        x_label = curves[0].x_label
        plotted = self._plot_trace_curves_on_widget(
            self.cangfu_time_plot,
            curves,
            title=f"Cangfu Trace 时域 - {current.table.name}",
            x_label=x_label,
            y_label="工程值",
            subplots=subplots,
            hold=hold,
        )
        psd_curves = self._psd_curves(current, curves)
        self._plot_trace_curves_on_widget(
            self.cangfu_psd_plot,
            psd_curves,
            title=f"Cangfu Trace PSD - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="PSD",
            log_x=True,
            log_y=True,
            subplots=subplots,
            hold=hold,
        )
        self._update_cangfu_context(current, plotted)
        self._show_status(f"已绘制 Cangfu Trace 通道：{plotted} 个")

    def _plot_ide_trans_current(self, current: TraceAnalysisFile) -> None:
        metadata = current.table.metadata
        input_name = str(metadata.get("ide_trans_input_name") or "激励信号")
        response_name = str(metadata.get("ide_trans_response_name") or "响应信号")
        hold = self.hold_check.isChecked()

        time_curves: list[CurvePair] = []
        for name, values in current.channels.items():
            y = np.asarray(values, dtype=float)
            x = np.asarray(current.time_s[: y.size], dtype=float)
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            time_curves.append(CurvePair(name, x, y, "时间 (s)", "幅值 (digits)"))
        time_curves = self._curves_with_file_display_name(current, time_curves)
        time_count = self._plot_trace_curves_on_widget(
            self.ide_trans_time_plot,
            time_curves,
            title=f"IDE Trans 时域 - {current.table.name}",
            x_label="时间 (s)",
            y_label="幅值 (digits)",
            hold=hold,
        )

        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        frequency_min, frequency_max = self._ide_trans_frequency_range(current, frequency)

        power_curves: list[CurvePair] = []
        for label, key in (
            (input_name, "ide_trans_autospectrum_input"),
            (response_name, "ide_trans_autospectrum_response"),
        ):
            values = metadata.get(key)
            if values is None:
                continue
            power = np.asarray(values, dtype=float)
            count = min(frequency.size, power.size)
            power_db = 10.0 * np.log10(np.maximum(np.abs(power[:count]), 1e-30))
            power_curves.append(CurvePair(label, frequency[:count], power_db, "频率 (Hz)", "幅值 (dB)"))
        power_curves = self._frequency_limited_curves(power_curves, frequency_min, frequency_max)
        power_curves = self._curves_with_file_display_name(current, power_curves)
        power_count = self._plot_trace_curves_on_widget(
            self.ide_trans_power_plot,
            power_curves,
            title=f"IDE Trans 功率谱 - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="幅值 (dB)",
            log_x=True,
            hold=hold,
        )

        transfer_label = f"{response_name} / {input_name}"
        magnitude = np.asarray(metadata.get("ide_trans_magnitude_db", np.array([], dtype=float)), dtype=float)
        magnitude_count = min(frequency.size, magnitude.size)
        magnitude_curves = self._frequency_limited_curves(
            [CurvePair(transfer_label, frequency[:magnitude_count], magnitude[:magnitude_count], "频率 (Hz)", "幅值 (dB)")],
            frequency_min,
            frequency_max,
        )
        magnitude_curves = self._curves_with_file_display_name(current, magnitude_curves)
        magnitude_plotted = self._plot_trace_curves_on_widget(
            self.ide_trans_magnitude_plot,
            magnitude_curves,
            title=f"传递函数（输出 / 输入）- {current.table.name}",
            x_label="频率 (Hz)",
            y_label="幅值 (dB)",
            log_x=True,
            hold=hold,
        )

        phase = np.asarray(metadata.get("ide_trans_phase_deg", np.array([], dtype=float)), dtype=float)
        phase_count = min(frequency.size, phase.size)
        phase_curves = self._frequency_limited_curves(
            [CurvePair(transfer_label, frequency[:phase_count], phase[:phase_count], "频率 (Hz)", "相位 (deg)")],
            frequency_min,
            frequency_max,
        )
        phase_curves = self._curves_with_file_display_name(current, phase_curves)
        phase_plotted = self._plot_trace_curves_on_widget(
            self.ide_trans_phase_plot,
            phase_curves,
            title=f"IDE Trans 相位 - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="相位 (deg)",
            log_x=True,
            hold=hold,
        )
        if phase_plotted:
            self.ide_trans_phase_plot.setYRange(-180.0, 180.0, padding=0.02)

        coherence = np.asarray(metadata.get("ide_trans_coherence", np.array([], dtype=float)), dtype=float)
        coherence_count = min(frequency.size, coherence.size)
        coherence_curves = self._frequency_limited_curves(
            [
                CurvePair(
                    transfer_label,
                    frequency[:coherence_count],
                    np.clip(coherence[:coherence_count], 0.0, 1.0),
                    "频率 (Hz)",
                    "相干性",
                )
            ],
            frequency_min,
            frequency_max,
        )
        coherence_curves = self._curves_with_file_display_name(current, coherence_curves)
        coherence_plotted = self._plot_trace_curves_on_widget(
            self.ide_trans_coherence_plot,
            coherence_curves,
            title=f"IDE Trans 相干性 - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="相干性",
            log_x=True,
            hold=hold,
        )
        if coherence_plotted:
            self.ide_trans_coherence_plot.setYRange(0.0, 1.0, padding=0.02)

        self._update_ide_trans_context(current)
        total = time_count + power_count + magnitude_plotted + phase_plotted + coherence_plotted
        self._show_status(f"已绘制 IDE Trans：{total} 条曲线")

    def _ide_trans_frequency_range(
        self,
        current: TraceAnalysisFile,
        frequency: np.ndarray,
    ) -> tuple[float, float]:
        stored = self._ide_trans_frequency_ranges.get(self._file_key(current))
        if stored is not None:
            return stored
        positive = np.asarray(frequency, dtype=float)
        positive = positive[np.isfinite(positive) & (positive > 0.0)]
        if positive.size:
            return float(positive[0]), float(positive[-1])
        return 0.0, 0.0

    def _plot_hac_trans_current(self, current: TraceAnalysisFile) -> None:
        metadata = current.table.metadata
        input_name = str(metadata.get("ide_trans_input_name") or "激励信号")
        response_name = str(metadata.get("ide_trans_response_name") or "响应信号")
        hold = self.hold_check.isChecked()

        time_curves: list[CurvePair] = []
        for name, values in current.channels.items():
            y = np.asarray(values, dtype=float)
            x = np.asarray(current.time_s[: y.size], dtype=float)
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            time_curves.append(CurvePair(name, x, y, "时间 (s)", "幅值"))
        time_curves = self._curves_with_file_display_name(current, time_curves)
        time_count = self._plot_trace_curves_on_widget(
            self.hac_trans_time_plot,
            time_curves,
            title=f"HAC Trans 激励 / 响应 - {current.table.name}",
            x_label="时间 (s)",
            y_label="幅值",
            hold=hold,
        )

        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        frequency_min, frequency_max = self._hac_trans_frequency_range(current, frequency)
        transfer_label = f"{response_name} / {input_name}"

        magnitude = np.asarray(metadata.get("ide_trans_magnitude_db", np.array([], dtype=float)), dtype=float)
        count = min(frequency.size, magnitude.size)
        magnitude_curves = self._frequency_limited_curves(
            [CurvePair(transfer_label, frequency[:count], magnitude[:count], "频率 (Hz)", "幅值 (dB)")],
            frequency_min,
            frequency_max,
        )
        magnitude_curves = self._curves_with_file_display_name(current, magnitude_curves)
        magnitude_count = self._plot_trace_curves_on_widget(
            self.hac_trans_magnitude_plot,
            magnitude_curves,
            title=f"HAC Trans 传递函数（输出 / 输入）- {current.table.name}",
            x_label="频率 (Hz)",
            y_label="幅值 (dB)",
            log_x=True,
            hold=hold,
        )

        phase = np.asarray(metadata.get("ide_trans_phase_deg", np.array([], dtype=float)), dtype=float)
        count = min(frequency.size, phase.size)
        phase_curves = self._frequency_limited_curves(
            [CurvePair(transfer_label, frequency[:count], phase[:count], "频率 (Hz)", "相位 (deg)")],
            frequency_min,
            frequency_max,
        )
        phase_curves = self._curves_with_file_display_name(current, phase_curves)
        phase_count = self._plot_trace_curves_on_widget(
            self.hac_trans_phase_plot,
            phase_curves,
            title=f"HAC Trans 相位 - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="相位 (deg)",
            log_x=True,
            hold=hold,
        )
        if phase_count:
            self.hac_trans_phase_plot.setYRange(-180.0, 180.0, padding=0.02)

        coherence = np.asarray(metadata.get("ide_trans_coherence", np.array([], dtype=float)), dtype=float)
        count = min(frequency.size, coherence.size)
        coherence_curves = self._frequency_limited_curves(
            [
                CurvePair(
                    transfer_label,
                    frequency[:count],
                    np.clip(coherence[:count], 0.0, 1.0),
                    "频率 (Hz)",
                    "相干性",
                )
            ],
            frequency_min,
            frequency_max,
        )
        coherence_curves = self._curves_with_file_display_name(current, coherence_curves)
        coherence_count = self._plot_trace_curves_on_widget(
            self.hac_trans_coherence_plot,
            coherence_curves,
            title=f"HAC Trans 相干性 - {current.table.name}",
            x_label="频率 (Hz)",
            y_label="相干性",
            log_x=True,
            hold=hold,
        )
        if coherence_count:
            self.hac_trans_coherence_plot.setYRange(0.0, 1.0, padding=0.02)

        self._update_hac_trans_context(current)
        total = time_count + magnitude_count + phase_count + coherence_count
        self._show_status(f"已绘制 HAC Trans：{total} 条曲线")

    def _hac_trans_frequency_range(
        self,
        current: TraceAnalysisFile,
        frequency: np.ndarray,
    ) -> tuple[float, float]:
        stored = self._hac_trans_frequency_ranges.get(self._file_key(current))
        if stored is not None:
            return stored
        positive = np.asarray(frequency, dtype=float)
        positive = positive[np.isfinite(positive) & (positive > 0.0)]
        if positive.size:
            return float(positive[0]), float(positive[-1])
        return 0.0, 0.0

    @staticmethod
    def _frequency_limited_curves(
        curves: list[CurvePair],
        frequency_min: float,
        frequency_max: float,
    ) -> list[CurvePair]:
        limited: list[CurvePair] = []
        low = min(float(frequency_min), float(frequency_max))
        high = max(float(frequency_min), float(frequency_max))
        for curve in curves:
            x = np.asarray(curve.x, dtype=float)
            y = np.asarray(curve.y, dtype=float)
            count = min(x.size, y.size)
            x = x[:count]
            y = y[:count]
            valid = np.isfinite(x) & np.isfinite(y) & (x > 0.0)
            if high > low:
                valid &= (x >= low) & (x <= high)
            limited.append(CurvePair(curve.label, x[valid], y[valid], curve.x_label, curve.y_label))
        return limited

    def _plot_trace_curves_on_widget(
        self,
        plot: pg.PlotWidget,
        curves: list[CurvePair],
        *,
        title: str,
        x_label: str,
        y_label: str,
        log_x: bool = False,
        log_y: bool = False,
        subplots: bool = False,
        hold: bool = False,
    ) -> int:
        render_curves = list(curves)
        if hold and not subplots:
            prior = [
                CurvePair(label, np.asarray(x, dtype=float).copy(), np.asarray(y, dtype=float).copy(), x_label, y_label)
                for label, (x, y) in self._plot_curves.get(plot, {}).items()
            ]
            render_curves = prior + render_curves
        return self._plot_curves_on_widget(
            plot,
            render_curves,
            title=title,
            x_label=x_label,
            y_label=y_label,
            log_x=log_x,
            log_y=log_y,
            subplots=subplots,
        )

    def _refresh_ide_controls(self, current: TraceAnalysisFile | None) -> None:
        preferred = self._selected_item_texts(self.ide_channel_list)
        self.ide_channel_list.clear()
        self.ide_eu_table.setRowCount(0)
        for edit in self.ide_suffix_edits.values():
            edit.clear()
            edit.setEnabled(False)
        self.ide_suffix_apply_button.setEnabled(False)
        self.ide_settings_group.setEnabled(current is not None)
        if current is None:
            self._configure_range_controls(self.ide_range_start, self.ide_range_end, 1, (1, 1))
            self.ide_selected_label.setText("当前 IDE 文件：未选择")
            self.ide_context_label.setText("模式 / X 轴 / 范围 / 采样率")
            return

        count = max(1, current.table.row_count)
        stored = self._ide_ranges.get(self._file_key(current), (1, count))
        self._configure_range_controls(self.ide_range_start, self.ide_range_end, count, stored)
        enabled = self._ide_enabled_channels(current)
        default_selection = [name for name in current.channels if enabled.get(name, True)]
        selected = preferred if preferred else default_selection
        for name in current.channels:
            item = QtWidgets.QListWidgetItem(name)
            self.ide_channel_list.addItem(item)
            item.setSelected(name in selected or not selected)
        self._populate_ide_eu_table(current)
        self._refresh_ide_suffix_controls(current)
        self._update_ide_context(current, len(default_selection))

    def _refresh_hac_controls(self, current: TraceAnalysisFile | None) -> None:
        previous_group = self.hac_preset_combo.currentText()
        self.hac_preset_combo.clear()
        self.hac_channel_list.clear()
        self.hac_settings_group.setEnabled(current is not None)
        if current is None:
            self.hac_preset_combo.addItem("(none)")
            self._configure_range_controls(self.hac_range_start, self.hac_range_end, 1, (1, 1))
            self.hac_selected_label.setText("当前 HAC 文件：未选择")
            self.hac_context_label.setText("预设 / 模式 / 范围 / X 轴")
            return

        count = max(1, current.table.row_count)
        stored = self._hac_ranges.get(self._file_key(current), (1, count))
        self._configure_range_controls(self.hac_range_start, self.hac_range_end, count, stored)
        groups = self._hac_groups(current)
        if not groups:
            groups = {"All Channels": list(range(len(current.table.headers)))}
        for group_name in groups:
            self.hac_preset_combo.addItem(group_name)
        if previous_group and previous_group in groups:
            self.hac_preset_combo.setCurrentText(previous_group)
        self._refresh_hac_channels()
        self._update_hac_context(current, self.hac_channel_list.count())

    def _refresh_cangfu_controls(self, current: TraceAnalysisFile | None) -> None:
        preferred = self._selected_item_texts(self.cangfu_channel_list)
        self.cangfu_channel_list.clear()
        self.cangfu_settings_group.setEnabled(current is not None)
        self.cangfu_x_axis_combo.setEnabled(True)
        if current is None:
            self._configure_range_controls(self.cangfu_range_start, self.cangfu_range_end, 1, (1, 1))
            self.cangfu_selected_label.setText("当前 Cangfu 文件：未选择")
            self.cangfu_context_label.setText("模式 / X 轴 / 范围 / 采样率")
            return

        transfer_pairs = self._cangfu_transfer_pairs(current)
        count = max(1, transfer_pairs[0].x.size if transfer_pairs else current.table.row_count)
        stored = self._cangfu_ranges.get(self._file_key(current), (1, count))
        self._configure_range_controls(self.cangfu_range_start, self.cangfu_range_end, count, stored)
        if transfer_pairs:
            self.cangfu_x_axis_combo.setEnabled(False)
            selected = preferred if preferred else [pair.label for pair in transfer_pairs]
            for pair in transfer_pairs:
                item = QtWidgets.QListWidgetItem(pair.label)
                self.cangfu_channel_list.addItem(item)
                item.setSelected(pair.label in selected or not selected)
            self._update_cangfu_context(current, len(selected))
            return
        selected = preferred if preferred else list(current.channels)[: min(8, len(current.channels))]
        for name in current.channels:
            item = QtWidgets.QListWidgetItem(name)
            self.cangfu_channel_list.addItem(item)
            item.setSelected(name in selected or not selected)
        self._update_cangfu_context(current, len(selected))

    def _refresh_ide_trans_frequency_controls(self, current: TraceAnalysisFile) -> None:
        metadata = current.table.metadata
        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        positive = frequency[np.isfinite(frequency) & (frequency > 0.0)]
        first_frequency = float(positive[0]) if positive.size else 0.0
        last_frequency = float(positive[-1]) if positive.size else 0.0
        stored = self._ide_trans_frequency_ranges.get(
            self._file_key(current),
            (first_frequency, last_frequency),
        )
        maximum = max(last_frequency, float(stored[1]), 1.0)
        for spin in (self.ide_trans_frequency_min, self.ide_trans_frequency_max):
            spin.blockSignals(True)
            spin.setRange(0.0, maximum)
        self.ide_trans_frequency_min.setValue(max(0.0, min(float(stored[0]), maximum)))
        self.ide_trans_frequency_max.setValue(max(0.0, min(float(stored[1]), maximum)))
        for spin in (self.ide_trans_frequency_min, self.ide_trans_frequency_max):
            spin.blockSignals(False)

    def _refresh_ide_trans_controls(self, current: TraceAnalysisFile | None) -> None:
        enabled = current is not None
        self.ide_trans_settings_group.setEnabled(enabled)
        if current is None:
            self.ide_trans_selected_label.setText("当前 IDE Trans 文件：未选择")
            self.ide_trans_context_label.setText("采样频率 / 更新率 / 频率范围")
            self.ide_trans_update_rate_reset.setEnabled(False)
            self._set_trans_info_text(self.ide_trans_info_label, "请选择 SDM 文件")
            return

        metadata = current.table.metadata
        file_key = self._file_key(current)
        sample_frequency, update_rate = self._ide_trans_sampling_settings.get(
            file_key,
            self._inferred_ide_trans_sampling(current),
        )
        self.ide_trans_sample_frequency.blockSignals(True)
        self.ide_trans_sample_frequency.setValue(sample_frequency)
        self.ide_trans_sample_frequency.blockSignals(False)
        self.ide_trans_update_rate.blockSignals(True)
        self.ide_trans_update_rate.setValue(int(round(update_rate)))
        self.ide_trans_update_rate.blockSignals(False)
        self.ide_trans_update_rate_reset.setEnabled(file_key in self._ide_trans_sampling_settings)
        self._apply_ide_trans_sampling(current, sample_frequency, update_rate)
        self._refresh_ide_trans_frequency_controls(current)

        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        df_hz = float(metadata.get("ide_trans_df_hz", 0.0) or 0.0)
        input_name = str(metadata.get("ide_trans_input_name") or "激励信号")
        response_name = str(metadata.get("ide_trans_response_name") or "响应信号")
        time_source = str(metadata.get("ide_trans_time_source") or "")
        time_source_label = {
            "sdm_raw": "SDM 原始时域",
            "spectrum_ifft": "频谱反变换",
        }.get(time_source, "无")
        self._set_trans_info_text(
            self.ide_trans_info_label,
            f"{input_name} -> {response_name} | 时域：{time_source_label} | 点数：{current.time_s.size}/{frequency.size}",
        )
        self.ide_trans_info_label.setToolTip(
            f"输入：{input_name}\n输出：{response_name}\n"
            f"时域来源：{time_source_label}\n"
            f"时域/频率点：{current.time_s.size}/{frequency.size}  Δf：{df_hz:.6g} Hz"
        )
        self._update_ide_trans_context(current)

    def _refresh_hac_trans_controls(self, current: TraceAnalysisFile | None) -> None:
        enabled = current is not None
        self.hac_trans_settings_group.setEnabled(enabled)
        if current is None:
            self.hac_trans_selected_label.setText("当前 HAC Trans 文件：未选择")
            self.hac_trans_context_label.setText("采样率 / 频率分辨率 / 频率范围")
            self._set_trans_info_text(self.hac_trans_info_label, "请选择 HAC 传函 CSV 文件")
            return

        metadata = current.table.metadata
        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        positive = frequency[np.isfinite(frequency) & (frequency > 0.0)]
        first_frequency = float(positive[0]) if positive.size else 0.0
        last_frequency = float(positive[-1]) if positive.size else 0.0
        stored = self._hac_trans_frequency_ranges.get(
            self._file_key(current),
            (first_frequency, last_frequency),
        )
        maximum = max(last_frequency, float(stored[1]), 1.0)
        for spin in (self.hac_trans_frequency_min, self.hac_trans_frequency_max):
            spin.blockSignals(True)
            spin.setRange(0.0, maximum)
        self.hac_trans_frequency_min.setValue(max(0.0, min(float(stored[0]), maximum)))
        self.hac_trans_frequency_max.setValue(max(0.0, min(float(stored[1]), maximum)))
        for spin in (self.hac_trans_frequency_min, self.hac_trans_frequency_max):
            spin.blockSignals(False)

        df_hz = float(metadata.get("ide_trans_df_hz", 0.0) or 0.0)
        input_name = str(metadata.get("ide_trans_input_name") or "激励信号")
        response_name = str(metadata.get("ide_trans_response_name") or "响应信号")
        header = metadata.get("hac_trans_header", {})
        average = header.get("Average", "--") if isinstance(header, dict) else "--"
        self._set_trans_info_text(
            self.hac_trans_info_label,
            f"输入：{input_name}",
            f"输出：{response_name}",
            f"点数：{current.time_s.size}/{frequency.size}  Δf：{df_hz:.6g} Hz  平均：{average}",
        )
        self._update_hac_trans_context(current)

    def _refresh_hac_channels(self) -> None:
        current = self.current_file()
        if current is None or current.trace_kind != "hac_trace":
            return
        preferred = self._selected_item_texts(self.hac_channel_list)
        self.hac_channel_list.clear()
        indices = self._selected_hac_group_indices(current)
        selected_labels = set(preferred)
        for index in indices:
            if index < 0 or index >= len(current.table.headers):
                continue
            label = current.table.headers[index]
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, int(index))
            self.hac_channel_list.addItem(item)
            item.setSelected(label in selected_labels or not selected_labels)
        self._update_hac_context(current, len(indices))

    def _populate_ide_eu_table(self, current: TraceAnalysisFile) -> None:
        self.ide_eu_table.setRowCount(len(current.channels))
        enabled = self._ide_enabled_channels(current)
        for row, name in enumerate(current.channels):
            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
            scale_item = QtWidgets.QTableWidgetItem(f"{current.channel_eu.get(name, 1.0):.8g}")
            enabled_item = QtWidgets.QTableWidgetItem()
            enabled_item.setFlags(
                QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable
            )
            enabled_item.setCheckState(QtCore.Qt.Checked if enabled.get(name, True) else QtCore.Qt.Unchecked)
            self.ide_eu_table.setItem(row, 0, name_item)
            self.ide_eu_table.setItem(row, 1, scale_item)
            self.ide_eu_table.setItem(row, 2, enabled_item)

    def _refresh_ide_suffix_controls(self, current: TraceAnalysisFile) -> None:
        for category, edit in self.ide_suffix_edits.items():
            names = [name for name in current.channels if self._ide_suffix_category(name) == category]
            edit.setEnabled(bool(names))
            if not names:
                edit.clear()
                continue
            values = [float(current.channel_eu.get(name, 1.0)) for name in names]
            if all(abs(value - values[0]) <= 1e-12 for value in values):
                edit.setText(f"{values[0]:.8g}")
            else:
                edit.setText("mixed")
        self.ide_suffix_apply_button.setEnabled(
            any(edit.isEnabled() for edit in self.ide_suffix_edits.values())
        )

    def _on_ide_eu_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating_controls or item is None:
            return
        current = self.current_file()
        if current is None or current.trace_kind != "ide_trace":
            return
        row = item.row()
        name_item = self.ide_eu_table.item(row, 0)
        if name_item is None:
            return
        name = name_item.text()
        targets = self._selected_ide_files_for_eu_batch()
        if not targets:
            targets = [current]
        if item.column() == 1:
            try:
                value = float(item.text())
            except ValueError:
                value = float("nan")
            if not np.isfinite(value) or value == 0.0:
                self._updating_controls = True
                try:
                    item.setText(f"{current.channel_eu.get(name, 1.0):.8g}")
                finally:
                    self._updating_controls = False
                self._show_status("工程系数必须是有限且非零的数值")
                return
            updated = self._set_ide_eu_for_targets(targets, name, scale=float(value))
            self._refresh_ide_suffix_controls(current)
            if updated > 1:
                self._show_status(f"已批量更新工程系数：{updated} 个文件")
        elif item.column() == 2:
            updated = self._set_ide_eu_for_targets(
                targets,
                name,
                enabled=item.checkState() == QtCore.Qt.Checked,
            )
            if updated > 1:
                self._show_status(f"已批量更新通道启用状态：{updated} 个文件")
        self._auto_plot_from_control_change()

    def _apply_ide_suffix_eu(self) -> None:
        current = self.current_file()
        if current is None or current.trace_kind != "ide_trace":
            self._show_status("请先选择 IDE Trace 文件")
            return
        parsed: dict[str, float] = {}
        for category, edit in self.ide_suffix_edits.items():
            raw = edit.text().strip()
            if not raw or raw.lower() == "mixed":
                continue
            try:
                value = float(raw)
            except ValueError:
                value = float("nan")
            if not np.isfinite(value) or value == 0.0:
                self._show_status(f"{category} 工程系数必须是有限且非零的数值")
                return
            parsed[category] = float(value)
        if not parsed:
            self._show_status("没有可应用的后缀工程系数")
            return
        targets = self._selected_ide_files_for_eu_batch()
        if not targets:
            targets = [current]
        updated = self._set_ide_suffix_eu_for_targets(targets, parsed)
        self._updating_controls = True
        try:
            for row in range(self.ide_eu_table.rowCount()):
                name_item = self.ide_eu_table.item(row, 0)
                scale_item = self.ide_eu_table.item(row, 1)
                if name_item is None or scale_item is None:
                    continue
                name = name_item.text()
                category = self._ide_suffix_category(name)
                if category not in parsed:
                    continue
                value = parsed[category]
                scale_item.setText(f"{value:.8g}")
        finally:
            self._updating_controls = False
        self._refresh_ide_suffix_controls(current)
        self._auto_plot_from_control_change()
        file_count = len(targets)
        self._show_status(f"已按后缀应用工程系数：{updated} 个通道，{file_count} 个文件")

    def _delete_selected(self) -> None:
        self._commit_ide_eu_table_for_index(self._last_trace_file_index)
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        self.file_list.blockSignals(True)
        try:
            for row in rows:
                if 0 <= row < len(self.files):
                    self.files.pop(row)
                    self.file_list.takeItem(row)
        finally:
            self.file_list.blockSignals(False)
        self._last_trace_file_index = self._valid_file_index(self.file_list.currentRow())
        self._refresh_controls()
        self._select_default_tab_for_current_file()
        self._auto_plot_from_control_change()
        self._show_status(f"已删除测试文件：{len(rows)} 个")

    def _export_active(self) -> None:
        if self.tabs.currentWidget() is self.hac_trans_tab:
            plots = (
                self.hac_trans_time_plot,
                self.hac_trans_magnitude_plot,
                self.hac_trans_phase_plot,
                self.hac_trans_coherence_plot,
            )
            plot = self._active_plot if self._active_plot in plots else self.hac_trans_magnitude_plot
            default_names = {
                self.hac_trans_time_plot: "hac_trans_time.csv",
                self.hac_trans_magnitude_plot: "hac_trans_magnitude.csv",
                self.hac_trans_phase_plot: "hac_trans_phase.csv",
                self.hac_trans_coherence_plot: "hac_trans_coherence.csv",
            }
            default_name = default_names.get(plot, "hac_trans_plot.csv")
        elif self.tabs.currentWidget() is self.ide_trans_tab:
            plots = (
                self.ide_trans_time_plot,
                self.ide_trans_power_plot,
                self.ide_trans_magnitude_plot,
                self.ide_trans_phase_plot,
                self.ide_trans_coherence_plot,
            )
            plot = self._active_plot if self._active_plot in plots else self.ide_trans_magnitude_plot
            default_names = {
                self.ide_trans_time_plot: "ide_trans_time.csv",
                self.ide_trans_power_plot: "ide_trans_power.csv",
                self.ide_trans_magnitude_plot: "ide_trans_magnitude.csv",
                self.ide_trans_phase_plot: "ide_trans_phase.csv",
                self.ide_trans_coherence_plot: "ide_trans_coherence.csv",
            }
            default_name = default_names.get(plot, "ide_trans_plot.csv")
        elif self.tabs.currentWidget() is self.cangfu_tab:
            plot = self.cangfu_psd_plot if self._plot_curves.get(self.cangfu_psd_plot) else self.cangfu_time_plot
            default_name = "cangfu_trace_plot.csv"
        elif self.tabs.currentWidget() is self.hac_tab:
            plot = self.hac_plot
            default_name = "hac_trace_plot.csv"
        elif self._plot_curves.get(self.ide_psd_plot) and not self._plot_curves.get(self.ide_time_plot):
            plot = self.ide_psd_plot
            default_name = "ide_psd_plot.csv"
        else:
            plot = self.ide_time_plot
            default_name = "ide_time_plot.csv"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前测试数据图",
            str(self._last_directory / default_name),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_plot_csv(plot, path)
        self._show_status(f"已导出 {Path(path).name}")

    def _open_plot_window(self, source_plot: pg.PlotWidget, title: str) -> None:
        curves = self._plot_curves.get(source_plot, {})
        if not curves:
            self._show_status("当前图像没有可打开的曲线")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(920, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        plot = pg.PlotWidget()
        plot.addLegend(offset=(4, 2), labelTextSize="8pt")
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.setTitle(title)
        plot.setLabel("bottom", source_plot.getAxis("bottom").labelText)
        plot.setLabel("left", source_plot.getAxis("left").labelText)
        log_x, log_y = self._log_modes.get(source_plot, (False, False))
        plot.setLogMode(x=log_x, y=log_y)
        if self._theme:
            apply_plot_theme(plot, self._theme)
        for index, (label, (x, y)) in enumerate(curves.items()):
            plot.plot(
                np.asarray(x, dtype=float),
                np.asarray(y, dtype=float),
                pen=pg.mkPen(self._color_for_label(label), width=1.5),
                name=label,
            )
        layout.addWidget(plot)
        dialog.finished.connect(lambda _result, item=dialog: self._forget_plot_window(item))
        self._plot_windows.append(dialog)
        dialog.show()
        self._show_status(f"已打开 {title}")

    def _forget_plot_window(self, dialog: QtWidgets.QDialog) -> None:
        if dialog in self._plot_windows:
            self._plot_windows.remove(dialog)

    def _close_plot_windows(self) -> None:
        for dialog in list(self._plot_windows):
            dialog.close()
        self._plot_windows.clear()

    def _select_default_tab_for_current_file(self) -> None:
        current = self.current_file()
        if current is None:
            return
        if current.trace_kind == "hac_trace":
            target = self.hac_tab
        elif current.trace_kind == "cangfu_trace":
            target = self.cangfu_tab
        elif current.trace_kind == "ide_trans":
            target = self.ide_trans_tab
        elif current.trace_kind == "hac_trans":
            target = self.hac_trans_tab
        else:
            target = self.ide_tab
        if self.tabs.currentWidget() is not target:
            self.tabs.setCurrentWidget(target)
        self._update_settings_stack()

    def _active_tab_kind(self) -> str:
        if self.tabs.currentWidget() is self.hac_tab:
            return "hac_trace"
        if self.tabs.currentWidget() is self.cangfu_tab:
            return "cangfu_trace"
        if self.tabs.currentWidget() is self.ide_trans_tab:
            return "ide_trans"
        if self.tabs.currentWidget() is self.hac_trans_tab:
            return "hac_trans"
        return "ide_trace"

    def _update_settings_stack(self) -> None:
        if hasattr(self, "settings_stack"):
            active_kind = self._active_tab_kind()
            self.settings_stack.setCurrentIndex(
                {"ide_trace": 0, "hac_trace": 1, "cangfu_trace": 2, "ide_trans": 3, "hac_trans": 4}.get(
                    active_kind, 0
                )
            )
            is_trans_page = active_kind in {"ide_trans", "hac_trans"}
            self.file_list.setMinimumHeight(
                self.TRANS_FILE_LIST_MIN_HEIGHT if is_trans_page else self.TRACE_FILE_LIST_MIN_HEIGHT
            )
            policy = self.settings_stack.sizePolicy()
            if is_trans_page:
                current_group = self.settings_stack.currentWidget()
                if current_group.layout() is not None:
                    current_group.layout().activate()
                compact_height = max(
                    current_group.minimumSizeHint().height(),
                    current_group.sizeHint().height(),
                )
                self.settings_stack.setMinimumHeight(compact_height)
                self.settings_stack.setMaximumHeight(compact_height)
                policy.setVerticalPolicy(QtWidgets.QSizePolicy.Fixed)
                self.controls_layout.setStretchFactor(self.settings_stack, 0)
            else:
                self.settings_stack.setMinimumHeight(0)
                self.settings_stack.setMaximumHeight(16_777_215)
                policy.setVerticalPolicy(QtWidgets.QSizePolicy.Expanding)
                self.controls_layout.setStretchFactor(self.settings_stack, 1)
            self.settings_stack.setSizePolicy(policy)
            self.settings_stack.updateGeometry()

    def _sync_hold_availability(self) -> None:
        if not hasattr(self, "hold_check"):
            return
        active_kind = self._active_tab_kind()
        if active_kind == "hac_trace":
            subplots = self.hac_plot_mode_combo.currentIndex() == 1
        elif active_kind == "cangfu_trace":
            subplots = self.cangfu_plot_mode_combo.currentIndex() == 1
        elif active_kind == "ide_trans":
            subplots = False
        elif active_kind == "hac_trans":
            subplots = False
        else:
            subplots = self.ide_plot_mode_combo.currentIndex() == 1
        if subplots and self.hold_check.isChecked():
            self.hold_check.setChecked(False)
        self.hold_check.setEnabled(not subplots)

    def _update_current_file_label(self, current: TraceAnalysisFile | None) -> None:
        self.rename_edit.blockSignals(True)
        if current is None:
            self.current_file_edit.setText("未选择文件")
            self._rename_edit_autofill_text = ""
            self.rename_edit.clear()
            self.rename_edit.setEnabled(False)
        else:
            self.current_file_edit.setText(f"{current.table.name} ({self._kind_label(current.trace_kind)})")
            self._rename_edit_autofill_text = str(current.table.name)
            self.rename_edit.setText(self._rename_edit_autofill_text)
            self.rename_edit.setEnabled(True)
        self.rename_edit.blockSignals(False)

    def _rename_current_file_confirmed(self) -> None:
        self._rename_current_file_from_editor(force=True)

    def _rename_current_file_from_editor(self, *, force: bool = False) -> None:
        current = self.current_file()
        if current is None:
            return
        name = self.rename_edit.text().strip()
        if not name:
            self._update_current_file_label(current)
            return
        if not force and name == self._rename_edit_autofill_text:
            return
        current.table.name = name
        current.table.metadata["trace_custom_display_name"] = True
        row = self.file_list.currentRow()
        item = self.file_list.item(row)
        if item is not None:
            item.setText(self._file_list_text(current))
        self._update_current_file_label(current)
        self.plot_current()
        self._show_status(f"当前数据已重命名为：{name}")

    def _file_list_text(self, current: TraceAnalysisFile) -> str:
        return f"{current.table.name} ({self._kind_label(current.trace_kind)})"

    def _update_ide_context(self, current: TraceAnalysisFile, plotted_count: int) -> None:
        start, end = self._spin_range_values(self.ide_range_start, self.ide_range_end)
        self.ide_selected_label.setText(f"当前 IDE 文件：{current.table.name}")
        self.ide_context_label.setText(
            f"模式：{self.ide_plot_mode_combo.currentText()} | X 轴：{self.ide_x_axis_combo.currentText()} | "
            f"范围：{start}-{end} | 采样率：{current.sample_rate:.6g} Hz | 通道：{plotted_count}"
        )

    def _update_hac_context(self, current: TraceAnalysisFile, plotted_count: int) -> None:
        start, end = self._spin_range_values(self.hac_range_start, self.hac_range_end)
        x_source = "Elapsed Time (s)" if current.time_s.size else "样本序号"
        self.hac_selected_label.setText(f"当前 HAC 文件：{current.table.name}")
        self.hac_context_label.setText(
            f"预设：{self.hac_preset_combo.currentText()} | 模式：{self.hac_plot_mode_combo.currentText()} | "
            f"范围：{start}-{end} | X 轴：{x_source} | 通道：{plotted_count}"
        )

    def _update_cangfu_context(self, current: TraceAnalysisFile, plotted_count: int) -> None:
        start, end = self._spin_range_values(self.cangfu_range_start, self.cangfu_range_end)
        self.cangfu_selected_label.setText(f"当前 Cangfu 文件：{current.table.name}")
        if self._cangfu_transfer_pairs(current):
            average_count = current.table.metadata.get("cangfu_average_count")
            average_text = f" | 平均次数：{average_count}" if average_count else ""
            self.cangfu_context_label.setText(
                f"类型：传递函数 | 范围：{start}-{end} | 采样率：{current.sample_rate:.6g} Hz | 曲线：{plotted_count}{average_text}"
            )
            return
        self.cangfu_context_label.setText(
            f"模式：{self.cangfu_plot_mode_combo.currentText()} | X 轴：{self.cangfu_x_axis_combo.currentText()} | "
            f"范围：{start}-{end} | 采样率：{current.sample_rate:.6g} Hz | 通道：{plotted_count}"
        )

    def _update_ide_trans_context(self, current: TraceAnalysisFile) -> None:
        metadata = current.table.metadata
        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        low, high = self._ide_trans_frequency_range(current, frequency)
        df_hz = float(metadata.get("ide_trans_df_hz", 0.0) or 0.0)
        file_key = self._file_key(current)
        sample_frequency, update_rate = self._ide_trans_sampling_settings.get(
            file_key,
            self._inferred_ide_trans_sampling(current),
        )
        rate_source = "手动" if file_key in self._ide_trans_sampling_settings else "SDM"
        self.ide_trans_selected_label.setText(f"当前 IDE Trans 文件：{current.table.name}")
        self.ide_trans_context_label.setText(
            f"采样：{sample_frequency:.6g} Hz | 更新：{update_rate:.6g}（{rate_source}） | "
            f"有效：{current.sample_rate:.6g} Hz | Δf：{df_hz:.6g} Hz | "
            f"频率范围：{low:.6g}-{high:.6g} Hz"
        )

    def _update_hac_trans_context(self, current: TraceAnalysisFile) -> None:
        metadata = current.table.metadata
        frequency = np.asarray(metadata.get("ide_trans_frequency_hz", np.array([], dtype=float)), dtype=float)
        low, high = self._hac_trans_frequency_range(current, frequency)
        df_hz = float(metadata.get("ide_trans_df_hz", 0.0) or 0.0)
        self.hac_trans_selected_label.setText(f"当前 HAC Trans 文件：{current.table.name}")
        self.hac_trans_context_label.setText(
            f"采样率：{current.sample_rate:.6g} Hz | Δf：{df_hz:.6g} Hz | "
            f"频率范围：{low:.6g}-{high:.6g} Hz"
        )

    @staticmethod
    def _cangfu_transfer_pairs(current: TraceAnalysisFile) -> list[CurvePair]:
        pairs = current.table.metadata.get("cangfu_transfer_pairs")
        if not isinstance(pairs, list):
            return []
        return [pair for pair in pairs if isinstance(pair, CurvePair)]

    def _configure_range_controls(
        self,
        start_box: QtWidgets.QSpinBox,
        end_box: QtWidgets.QSpinBox,
        count: int,
        values: tuple[int, int],
    ) -> None:
        count = max(1, int(count))
        start, end = values
        start = max(1, min(int(start), count))
        end = max(1, min(int(end), count))
        start_box.setRange(1, count)
        end_box.setRange(1, count)
        start_box.setValue(start)
        end_box.setValue(end)

    @staticmethod
    def _spin_range_values(start_box: QtWidgets.QSpinBox, end_box: QtWidgets.QSpinBox) -> tuple[int, int]:
        start = int(start_box.value())
        end = int(end_box.value())
        if end < start:
            start, end = end, start
        return start, end

    @classmethod
    def _slice_bounds(cls, start_box: QtWidgets.QSpinBox, end_box: QtWidgets.QSpinBox) -> tuple[int, int]:
        start_value, end_value = cls._spin_range_values(start_box, end_box)
        start = max(0, start_value - 1)
        end = max(start + 1, end_value)
        return start, end

    @staticmethod
    def _file_key(current: TraceAnalysisFile) -> str:
        return str(current.table.path)

    @staticmethod
    def _selected_item_texts(list_widget: QtWidgets.QListWidget) -> list[str]:
        return [item.text() for item in list_widget.selectedItems()]

    @staticmethod
    def _kind_label(kind: str) -> str:
        if kind == "cangfu_trace":
            return "Cangfu Trace"
        if kind == "ide_trans":
            return "IDE Trans"
        if kind == "hac_trans":
            return "HAC Trans"
        return "HAC Trace" if kind == "hac_trace" else "IDE Trace"

    @staticmethod
    def _ide_suffix_category(channel_name: str) -> str:
        text = re.sub(r"\([^\)]*\)", "", str(channel_name))
        text = re.sub(r"\[[^\]]*\]", "", text)
        text = re.sub(r"\s+", "", text).upper()
        if text.endswith("PROX"):
            return "Prox"
        if text.endswith("FB"):
            return "FB"
        if text.endswith("ACC"):
            return "ACC"
        if text.endswith("POS"):
            return "POS"
        return ""

    @staticmethod
    def _hac_groups(current: TraceAnalysisFile) -> dict[str, list[int]]:
        groups = current.table.metadata.get("hac_groups", {})
        return groups if isinstance(groups, dict) else {}

    def _selected_hac_group_indices(self, current: TraceAnalysisFile) -> list[int]:
        groups = self._hac_groups(current)
        group_name = self.hac_preset_combo.currentText()
        indices = groups.get(group_name)
        if indices is None:
            indices = next(iter(groups.values()), list(range(len(current.table.headers)))) if groups else list(range(len(current.table.headers)))
        return [int(index) for index in indices]

    @staticmethod
    def _ide_enabled_channels(current: TraceAnalysisFile) -> dict[str, bool]:
        enabled = current.table.metadata.get("ide_enabled_channels")
        if not isinstance(enabled, dict):
            enabled = {name: True for name in current.channels}
            current.table.metadata["ide_enabled_channels"] = enabled
        for name in current.channels:
            enabled.setdefault(name, True)
        return enabled


@dataclass(slots=True)
class ModalFile:
    path: Path
    dataset: AnalysisDataset


class ModalShapePage(DiagnosticPage):
    POINT_HEADERS = ["启用", "测点", "文件", "X通道", "Y通道", "Z通道", "X系数", "Y系数", "Z系数", "X", "Y", "Z"]
    LINE_HEADERS = ["启用", "起点", "终点", "来源"]
    POINT_CSV_HEADERS = ["point_id", "file_name", "x_ch", "y_ch", "z_ch", "x_scale", "y_scale", "z_scale", "x", "y", "z", "use"]

    def __init__(self, parent=None, *, data_store=None):
        super().__init__(parent)
        self._data_store = data_store
        self.files: list[ModalFile] = []
        self.last_mode: dict[str, object] | None = None
        self._active_frequency: float | None = None
        self._auto_peaks: list[float] = []
        self._manual_peaks: list[float] = []
        self._view_azimuth = 35.0
        self._view_elevation = 24.0
        self._preview_phase_index = 0
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._advance_preview)
        self._layout_mode = None
        self._bulk_table_update = False
        self._pending_data_store_sync_reason: str | None = None
        self._retired_candidate_items: list[QtWidgets.QListWidgetItem] = []
        self._build_ui_matlab_style()
        if self._data_store is not None:
            self._data_store.changed.connect(self._on_shared_data_store_changed)
            self.sync_from_data_store(show_status=False)

    def _build_ui_matlab_style(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        left_column = QtWidgets.QWidget()
        configure_control_panel(left_column)
        left_column.setMinimumWidth(320)
        left_column.setMaximumWidth(560)
        left_layout = QtWidgets.QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        left_splitter.setChildrenCollapsible(False)
        left_layout.addWidget(left_splitter)

        self.left_work_tabs = QtWidgets.QTabWidget()
        self.left_work_tabs.setObjectName("modalLeftWorkTabs")
        self.left_work_tabs.setMinimumHeight(200)
        self.left_work_tabs.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        control_tab = QtWidgets.QWidget()
        control_layout = QtWidgets.QVBoxLayout(control_tab)
        control_layout.setContentsMargins(8, 8, 8, 8)
        control_layout.setSpacing(7)

        file_button_grid = QtWidgets.QGridLayout()
        file_button_grid.setHorizontalSpacing(8)
        file_button_grid.setVerticalSpacing(6)
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.load_folder_button = QtWidgets.QPushButton("加载文件夹")
        self.delete_button = QtWidgets.QPushButton("删除文件")
        self.import_mapping_button = QtWidgets.QPushButton("导入映射表")
        self.export_mapping_button = QtWidgets.QPushButton("导出映射表")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.load_folder_button, "secondary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.import_mapping_button, "secondary")
        set_button_role(self.export_mapping_button, "secondary")
        set_button_role(self.clear_button, "secondary")
        for button in (
            self.load_button,
            self.load_folder_button,
            self.delete_button,
            self.import_mapping_button,
            self.export_mapping_button,
            self.clear_button,
        ):
            button.setMinimumWidth(92)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_button_grid.addWidget(self.load_button, 0, 0)
        file_button_grid.addWidget(self.load_folder_button, 0, 1)
        file_button_grid.addWidget(self.delete_button, 0, 2)
        file_button_grid.addWidget(self.import_mapping_button, 1, 0)
        file_button_grid.addWidget(self.export_mapping_button, 1, 1)
        file_button_grid.addWidget(self.clear_button, 1, 2)
        for column in range(3):
            file_button_grid.setColumnStretch(column, 1)
        control_layout.addLayout(file_button_grid)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(110)
        control_layout.addWidget(self.file_list, 1)
        self.left_work_tabs.addTab(control_tab, "控制区")

        point_tab = QtWidgets.QWidget()
        point_layout = QtWidgets.QVBoxLayout(point_tab)
        point_layout.setContentsMargins(8, 8, 8, 8)
        point_layout.setSpacing(7)

        point_action_row = QtWidgets.QHBoxLayout()
        point_action_row.setContentsMargins(0, 0, 0, 0)
        point_action_row.setSpacing(6)
        self.add_point_button = QtWidgets.QPushButton("新增测点")
        self.point_row_edit = QtWidgets.QLineEdit()
        self.point_row_edit.setPlaceholderText("行号")
        self.point_row_edit.setMaximumWidth(62)
        self.delete_point_button = QtWidgets.QPushButton("删除测点")
        set_button_role(self.add_point_button, "secondary")
        set_button_role(self.delete_point_button, "danger")
        self.add_point_button.setMinimumWidth(86)
        self.delete_point_button.setMinimumWidth(86)
        point_action_row.addWidget(self.add_point_button)
        point_action_row.addWidget(QtWidgets.QLabel("行号"))
        point_action_row.addWidget(self.point_row_edit)
        point_action_row.addWidget(self.delete_point_button)
        point_action_row.addStretch(1)
        point_layout.addLayout(point_action_row)

        self.point_table = QtWidgets.QTableWidget(0, len(self.POINT_HEADERS))
        self.point_table.setHorizontalHeaderLabels(self.POINT_HEADERS)
        configure_data_table(self.point_table, minimum_height=130)
        self.point_table.verticalHeader().setVisible(True)
        self.point_table.verticalHeader().setDefaultSectionSize(22)
        self.point_table.verticalHeader().setMinimumWidth(34)
        self.point_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.point_table.horizontalHeader().setStretchLastSection(False)
        self.point_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        for column, width in enumerate((38, 54, 92, 48, 48, 48, 52, 52, 52, 46, 46, 46)):
            self.point_table.setColumnWidth(column, width)
        point_layout.addWidget(self.point_table, 1)
        self.left_work_tabs.addTab(point_tab, "测点表")

        line_tab = QtWidgets.QWidget()
        line_layout = QtWidgets.QVBoxLayout(line_tab)
        line_layout.setContentsMargins(8, 8, 8, 8)
        line_layout.setSpacing(7)

        line_action_row = QtWidgets.QHBoxLayout()
        self.line_action_row = line_action_row
        line_action_row.setContentsMargins(0, 0, 0, 0)
        line_action_row.setSpacing(6)
        self.auto_lines_button = QtWidgets.QPushButton("自动连线")
        self.add_line_button = QtWidgets.QPushButton("新增连线")
        self.line_row_edit = QtWidgets.QLineEdit()
        self.line_row_edit.setPlaceholderText("行号")
        self.line_row_edit.setMaximumWidth(62)
        self.delete_line_button = QtWidgets.QPushButton("删除连线")
        set_button_role(self.auto_lines_button, "secondary")
        set_button_role(self.add_line_button, "secondary")
        set_button_role(self.delete_line_button, "danger")
        self.auto_lines_button.setMinimumWidth(86)
        self.add_line_button.setMinimumWidth(86)
        self.delete_line_button.setMinimumWidth(86)
        line_action_row.addWidget(self.auto_lines_button)
        line_action_row.addWidget(self.add_line_button)
        line_action_row.addWidget(QtWidgets.QLabel("行号"))
        line_action_row.addWidget(self.line_row_edit)
        line_action_row.addWidget(self.delete_line_button)
        line_action_row.addStretch(1)
        line_layout.addLayout(line_action_row)

        self.line_table = QtWidgets.QTableWidget(0, len(self.LINE_HEADERS))
        self.line_table.setHorizontalHeaderLabels(self.LINE_HEADERS)
        configure_data_table(self.line_table, minimum_height=130)
        self.line_table.verticalHeader().setVisible(True)
        self.line_table.verticalHeader().setDefaultSectionSize(22)
        self.line_table.verticalHeader().setMinimumWidth(34)
        self.line_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.line_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((42, 76, 76, 68)):
            self.line_table.setColumnWidth(column, width)
        line_layout.addWidget(self.line_table, 1)
        self.left_work_tabs.addTab(line_tab, "连线表")
        left_splitter.addWidget(self.left_work_tabs)

        self.frequency_edit = QtWidgets.QDoubleSpinBox()
        self.frequency_edit.setDecimals(6)
        self.frequency_edit.setRange(0.0, 1e9)
        self.frequency_edit.setValue(10.0)
        self.apply_freq_button = QtWidgets.QPushButton("应用频率")
        self.find_peaks_button = QtWidgets.QPushButton("自动找峰")
        self.delete_peak_button = QtWidgets.QPushButton("删除候选")
        self.view_azimuth_spin = QtWidgets.QSpinBox()
        self.view_azimuth_spin.setRange(-180, 180)
        self.view_azimuth_spin.setValue(int(self._view_azimuth))
        self.view_azimuth_spin.setSuffix("°")
        self.view_elevation_spin = QtWidgets.QSpinBox()
        self.view_elevation_spin.setRange(-89, 89)
        self.view_elevation_spin.setValue(int(self._view_elevation))
        self.view_elevation_spin.setSuffix("°")
        self.view_reset_button = QtWidgets.QPushButton("重置视角")
        self.gif_frame_count_spin = QtWidgets.QSpinBox()
        self.gif_frame_count_spin.setRange(8, 96)
        self.gif_frame_count_spin.setValue(24)
        self.gif_frame_count_spin.setSuffix(" 帧")
        self.mode_gain_spin = QtWidgets.QDoubleSpinBox()
        self.mode_gain_spin.setDecimals(1)
        self.mode_gain_spin.setRange(0.1, 20.0)
        self.mode_gain_spin.setSingleStep(0.5)
        self.mode_gain_spin.setValue(3.0)
        self.mode_gain_spin.setSuffix(" x")
        self.extract_button = QtWidgets.QPushButton("提取振型")
        self.preview_button = QtWidgets.QPushButton("动画预览")
        self.export_gif_button = QtWidgets.QPushButton("导出 GIF")
        for button in (
            self.apply_freq_button,
            self.find_peaks_button,
            self.delete_peak_button,
            self.view_reset_button,
            self.preview_button,
            self.export_gif_button,
        ):
            set_button_role(button, "secondary")
        set_button_role(self.extract_button, "primary")
        set_button_role(self.delete_peak_button, "danger")

        freq_group, freq_layout = create_group_box("频率区", layout_type=QtWidgets.QVBoxLayout)
        freq_group.setMinimumHeight(285)
        freq_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.frequency_edit.setMinimumWidth(120)
        self.frequency_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for button in (
            self.apply_freq_button,
            self.find_peaks_button,
            self.extract_button,
            self.preview_button,
            self.export_gif_button,
            self.delete_peak_button,
            self.view_reset_button,
        ):
            button.setMinimumWidth(86)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for spin in (self.view_azimuth_spin, self.view_elevation_spin, self.mode_gain_spin, self.gif_frame_count_spin):
            spin.setMinimumWidth(72)
            spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        freq_row = QtWidgets.QHBoxLayout()
        freq_row.setContentsMargins(0, 0, 0, 0)
        freq_row.setSpacing(6)
        freq_row.addWidget(QtWidgets.QLabel("模态频率"))
        freq_row.addWidget(self.frequency_edit, 1)
        freq_row.addWidget(self.apply_freq_button)
        freq_layout.addLayout(freq_row)

        view_grid = QtWidgets.QGridLayout()
        view_grid.setHorizontalSpacing(6)
        view_grid.setVerticalSpacing(5)
        view_grid.addWidget(QtWidgets.QLabel("方位角"), 0, 0)
        view_grid.addWidget(self.view_azimuth_spin, 0, 1)
        view_grid.addWidget(QtWidgets.QLabel("俯仰角"), 0, 2)
        view_grid.addWidget(self.view_elevation_spin, 0, 3)
        view_grid.addWidget(QtWidgets.QLabel("振型放大"), 1, 0)
        view_grid.addWidget(self.mode_gain_spin, 1, 1)
        view_grid.addWidget(QtWidgets.QLabel("GIF 帧数"), 1, 2)
        view_grid.addWidget(self.gif_frame_count_spin, 1, 3)
        view_grid.setColumnStretch(1, 1)
        view_grid.setColumnStretch(3, 1)
        freq_layout.addLayout(view_grid)

        action_grid = QtWidgets.QGridLayout()
        action_grid.setHorizontalSpacing(6)
        action_grid.setVerticalSpacing(5)
        action_grid.addWidget(self.find_peaks_button, 0, 0)
        action_grid.addWidget(self.extract_button, 0, 1)
        action_grid.addWidget(self.preview_button, 0, 2)
        action_grid.addWidget(self.export_gif_button, 1, 0)
        action_grid.addWidget(self.delete_peak_button, 1, 1)
        action_grid.addWidget(self.view_reset_button, 1, 2)
        for column in range(3):
            action_grid.setColumnStretch(column, 1)
        freq_layout.addLayout(action_grid)

        freq_layout.addWidget(QtWidgets.QLabel("频率候选"))
        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.setAlternatingRowColors(True)
        self.candidate_list.setMinimumHeight(180)
        self.candidate_list.setMaximumHeight(320)
        freq_layout.addWidget(self.candidate_list, 1)
        left_splitter.addWidget(freq_group)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setSizes([360, 330])

        right_column = QtWidgets.QWidget()
        right_column.setMinimumWidth(360)
        right_layout = QtWidgets.QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)
        right_layout.addWidget(right_splitter)

        frf_group, frf_layout = create_group_box("FRF图", layout_type=QtWidgets.QVBoxLayout)
        self.frf_plot = self._create_plot_widget("模态 FRF 候选图")
        self.frf_plot.setMinimumHeight(170)
        frf_layout.addWidget(self.frf_plot)
        right_splitter.addWidget(frf_group)

        preview_group, preview_group_layout = create_group_box("3D预览区", layout_type=QtWidgets.QVBoxLayout)
        self.preview_tabs = QtWidgets.QTabWidget()
        self.preview_tabs.setObjectName("modalPreviewTabs")
        self.layout_plot = self._create_modal_preview_widget("结构布局")
        self.mode_plot = self._create_modal_preview_widget("模态振型")
        self.layout_plot.setMinimumSize(320, 240)
        self.mode_plot.setMinimumSize(320, 240)
        self.layout_preview_tab = QtWidgets.QWidget()
        layout_preview_layout = QtWidgets.QVBoxLayout(self.layout_preview_tab)
        layout_preview_layout.setContentsMargins(6, 6, 6, 6)
        layout_preview_layout.addWidget(self.layout_plot)
        self.mode_preview_tab = QtWidgets.QWidget()
        mode_preview_layout = QtWidgets.QVBoxLayout(self.mode_preview_tab)
        mode_preview_layout.setContentsMargins(6, 6, 6, 6)
        mode_preview_layout.addWidget(self.mode_plot)
        self.preview_tabs.addTab(self.layout_preview_tab, "测点骨架图")
        self.preview_tabs.addTab(self.mode_preview_tab, "振型预览")
        preview_group_layout.addWidget(self.preview_tabs)
        right_splitter.addWidget(preview_group)
        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([300, 360])

        main_splitter.addWidget(left_column)
        main_splitter.addWidget(right_column)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([440, 900])
        layout.addWidget(main_splitter)

        self.load_button.clicked.connect(self._choose_files)
        self.load_folder_button.clicked.connect(self._choose_folder)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.import_mapping_button.clicked.connect(self._choose_import_mapping)
        self.export_mapping_button.clicked.connect(self._choose_export_mapping)
        self.add_point_button.clicked.connect(self._add_point_row)
        self.delete_point_button.clicked.connect(self._delete_point_rows)
        self.auto_lines_button.clicked.connect(lambda _checked=False: self.auto_build_lines())
        self.add_line_button.clicked.connect(self._add_line_row)
        self.delete_line_button.clicked.connect(self._delete_line_rows)
        self.apply_freq_button.clicked.connect(self.apply_frequency)
        self.find_peaks_button.clicked.connect(lambda _checked=False: self.find_peaks())
        self.delete_peak_button.clicked.connect(self.delete_selected_peak)
        self.view_azimuth_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.view_elevation_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.mode_gain_spin.valueChanged.connect(lambda _value: self._mode_gain_changed())
        self.view_reset_button.clicked.connect(self._reset_view)
        self._connect_modal_preview_widget(self.layout_plot)
        self._connect_modal_preview_widget(self.mode_plot)
        self.extract_button.clicked.connect(self.extract_mode)
        self.preview_button.clicked.connect(self.preview_mode)
        self.export_gif_button.clicked.connect(self._choose_export_gif)
        self.candidate_list.currentItemChanged.connect(lambda item, _previous: self._candidate_selected(item))
        self.point_table.itemChanged.connect(lambda _item: self._mapping_changed())
        self.line_table.itemChanged.connect(self._line_mapping_changed)
        self._update_layout_mode(self.width())

    def _update_layout_mode(self, width: int) -> None:
        self._layout_mode = "four_zone"

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._update_layout_mode(self.width())
        super().resizeEvent(event)

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(300)
        controls.setMaximumWidth(380)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(5)

        data_group, data_layout = create_group_box("1. 数据", layout_type=QtWidgets.QVBoxLayout)
        button_grid = QtWidgets.QGridLayout()
        button_grid.setHorizontalSpacing(5)
        button_grid.setVerticalSpacing(5)
        self.load_button = QtWidgets.QPushButton("加载 VNA")
        self.load_folder_button = QtWidgets.QPushButton("加载文件夹")
        self.delete_button = QtWidgets.QPushButton("删除文件")
        self.import_mapping_button = QtWidgets.QPushButton("导入映射")
        self.export_mapping_button = QtWidgets.QPushButton("导出映射")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.load_folder_button, "secondary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.import_mapping_button, "secondary")
        set_button_role(self.export_mapping_button, "secondary")
        set_button_role(self.clear_button, "secondary")
        button_grid.addWidget(self.load_button, 0, 0)
        button_grid.addWidget(self.load_folder_button, 0, 1)
        button_grid.addWidget(self.delete_button, 0, 2)
        button_grid.addWidget(self.import_mapping_button, 1, 0)
        button_grid.addWidget(self.export_mapping_button, 1, 1)
        button_grid.addWidget(self.clear_button, 1, 2)
        data_layout.addLayout(button_grid)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(130)
        data_layout.addWidget(self.file_list, 1)
        controls_layout.addWidget(data_group)

        freq_row = QtWidgets.QHBoxLayout()
        freq_row.setContentsMargins(0, 0, 0, 0)
        freq_row.setSpacing(5)
        self.frequency_edit = QtWidgets.QDoubleSpinBox()
        self.frequency_edit.setDecimals(6)
        self.frequency_edit.setRange(0.0, 1e9)
        self.frequency_edit.setValue(10.0)
        self.apply_freq_button = QtWidgets.QPushButton("应用频率")
        self.find_peaks_button = QtWidgets.QPushButton("查找峰值")
        self.delete_peak_button = QtWidgets.QPushButton("删除候选")
        self.view_azimuth_spin = QtWidgets.QSpinBox()
        self.view_azimuth_spin.setRange(-180, 180)
        self.view_azimuth_spin.setValue(int(self._view_azimuth))
        self.view_azimuth_spin.setSuffix("°")
        self.view_elevation_spin = QtWidgets.QSpinBox()
        self.view_elevation_spin.setRange(-89, 89)
        self.view_elevation_spin.setValue(int(self._view_elevation))
        self.view_elevation_spin.setSuffix("°")
        self.view_reset_button = QtWidgets.QPushButton("重置视角")
        self.gif_frame_count_spin = QtWidgets.QSpinBox()
        self.gif_frame_count_spin.setRange(8, 96)
        self.gif_frame_count_spin.setValue(24)
        self.gif_frame_count_spin.setSuffix(" 帧")
        self.mode_gain_spin = QtWidgets.QDoubleSpinBox()
        self.mode_gain_spin.setDecimals(1)
        self.mode_gain_spin.setRange(0.1, 20.0)
        self.mode_gain_spin.setSingleStep(0.5)
        self.mode_gain_spin.setValue(3.0)
        self.mode_gain_spin.setSuffix(" x")
        self.extract_button = QtWidgets.QPushButton("提取振型")
        self.preview_button = QtWidgets.QPushButton("动画预览")
        self.export_gif_button = QtWidgets.QPushButton("导出 GIF")
        set_button_role(self.apply_freq_button, "secondary")
        set_button_role(self.find_peaks_button, "secondary")
        set_button_role(self.delete_peak_button, "danger")
        set_button_role(self.view_reset_button, "secondary")
        set_button_role(self.extract_button, "primary")
        set_button_role(self.preview_button, "secondary")
        set_button_role(self.export_gif_button, "secondary")
        freq_row.addWidget(self.frequency_edit, 1)
        freq_row.addWidget(self.apply_freq_button)
        freq_row.addWidget(self.find_peaks_button)

        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.setAlternatingRowColors(True)
        self.candidate_list.setMinimumHeight(90)

        self.modal_section_stack = QtWidgets.QStackedWidget()
        self.modal_section_stack.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.modal_section_buttons: list[QtWidgets.QPushButton] = []
        section_nav = QtWidgets.QWidget()
        section_grid = QtWidgets.QGridLayout(section_nav)
        section_grid.setContentsMargins(0, 0, 0, 0)
        section_grid.setHorizontalSpacing(5)
        section_grid.setVerticalSpacing(5)
        for index, title in enumerate(("1. 模态设置", "2. 测点映射", "3. 测点连线", "4. 操作")):
            button = QtWidgets.QPushButton(title)
            button.setCheckable(True)
            set_button_role(button, "secondary")
            button.clicked.connect(lambda _checked=False, page_index=index: self._set_modal_section(page_index))
            self.modal_section_buttons.append(button)
            section_grid.addWidget(button, index // 2, index % 2)

        settings_page = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(6, 6, 6, 6)
        settings_layout.setSpacing(5)
        view_grid = QtWidgets.QGridLayout()
        view_grid.setHorizontalSpacing(5)
        view_grid.setVerticalSpacing(5)
        view_grid.addWidget(QtWidgets.QLabel("方位角"), 0, 0)
        view_grid.addWidget(self.view_azimuth_spin, 0, 1)
        view_grid.addWidget(QtWidgets.QLabel("俯仰角"), 1, 0)
        view_grid.addWidget(self.view_elevation_spin, 1, 1)
        view_grid.addWidget(self.view_reset_button, 2, 0, 1, 2)
        gif_row = QtWidgets.QHBoxLayout()
        gif_row.setContentsMargins(0, 0, 0, 0)
        gif_row.setSpacing(5)
        gif_row.addWidget(QtWidgets.QLabel("GIF 帧数"))
        gif_row.addWidget(self.gif_frame_count_spin, 1)
        gain_row = QtWidgets.QHBoxLayout()
        gain_row.setContentsMargins(0, 0, 0, 0)
        gain_row.setSpacing(5)
        gain_row.addWidget(QtWidgets.QLabel("振型放大"))
        gain_row.addWidget(self.mode_gain_spin, 1)
        settings_layout.addWidget(QtWidgets.QLabel("模态频率 Hz"))
        settings_layout.addLayout(freq_row)
        settings_layout.addWidget(QtWidgets.QLabel("3D 视角"))
        settings_layout.addLayout(view_grid)
        settings_layout.addLayout(gain_row)
        settings_layout.addLayout(gif_row)
        settings_layout.addWidget(QtWidgets.QLabel("峰值候选"))
        settings_layout.addWidget(self.candidate_list, 1)
        settings_layout.addWidget(self.delete_peak_button)
        self.modal_section_stack.addWidget(settings_page)

        point_button_row = QtWidgets.QHBoxLayout()
        self.add_point_button = QtWidgets.QPushButton("新增测点")
        self.delete_point_button = QtWidgets.QPushButton("删除测点")
        set_button_role(self.add_point_button, "secondary")
        set_button_role(self.delete_point_button, "danger")
        point_button_row.addWidget(self.add_point_button)
        point_button_row.addWidget(self.delete_point_button)

        self.point_table = QtWidgets.QTableWidget(0, len(self.POINT_HEADERS))
        self.point_table.setHorizontalHeaderLabels(self.POINT_HEADERS)
        configure_data_table(self.point_table, minimum_height=180)
        self.point_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.point_table.horizontalHeader().setStretchLastSection(True)

        points_page = QtWidgets.QWidget()
        points_layout = QtWidgets.QVBoxLayout(points_page)
        points_layout.setContentsMargins(6, 6, 6, 6)
        points_layout.setSpacing(5)
        points_layout.addLayout(point_button_row)
        points_layout.addWidget(self.point_table, 1)
        self.modal_section_stack.addWidget(points_page)

        line_button_row = QtWidgets.QHBoxLayout()
        self.auto_lines_button = QtWidgets.QPushButton("自动连线")
        self.add_line_button = QtWidgets.QPushButton("新增连线")
        self.delete_line_button = QtWidgets.QPushButton("删除连线")
        set_button_role(self.auto_lines_button, "secondary")
        set_button_role(self.add_line_button, "secondary")
        set_button_role(self.delete_line_button, "danger")
        line_button_row.addWidget(self.auto_lines_button)
        line_button_row.addWidget(self.add_line_button)
        line_button_row.addWidget(self.delete_line_button)

        self.line_table = QtWidgets.QTableWidget(0, len(self.LINE_HEADERS))
        self.line_table.setHorizontalHeaderLabels(self.LINE_HEADERS)
        configure_data_table(self.line_table, minimum_height=140)
        self.line_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.line_table.horizontalHeader().setStretchLastSection(True)

        lines_page = QtWidgets.QWidget()
        lines_layout = QtWidgets.QVBoxLayout(lines_page)
        lines_layout.setContentsMargins(6, 6, 6, 6)
        lines_layout.setSpacing(5)
        lines_layout.addLayout(line_button_row)
        lines_layout.addWidget(self.line_table, 1)
        self.modal_section_stack.addWidget(lines_page)

        action_page = QtWidgets.QWidget()
        action_layout = QtWidgets.QVBoxLayout(action_page)
        action_layout.setContentsMargins(6, 6, 6, 6)
        action_layout.setSpacing(5)
        action_layout.addWidget(self.extract_button)
        action_layout.addWidget(self.preview_button)
        action_layout.addWidget(self.export_gif_button)
        action_layout.addStretch(1)
        self.modal_section_stack.addWidget(action_page)

        controls_layout.addWidget(section_nav)
        controls_layout.addWidget(self.modal_section_stack, 1)
        self._set_modal_section(0)

        plots = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        plots.setChildrenCollapsible(False)
        self.frf_plot = self._create_plot_widget("FRF / 峰值")
        self.layout_plot = self._create_modal_preview_widget("结构布局")
        self.mode_plot = self._create_modal_preview_widget("模态振型")
        self.layout_plot.setMinimumSize(280, 220)
        self.mode_plot.setMinimumSize(280, 220)

        frf_panel = QtWidgets.QWidget()
        frf_layout = QtWidgets.QVBoxLayout(frf_panel)
        frf_layout.setContentsMargins(0, 0, 0, 0)
        frf_layout.addWidget(self.frf_plot)

        preview_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        preview_splitter.setChildrenCollapsible(False)
        layout_group, layout_group_layout = create_group_box("结构布局 3D", layout_type=QtWidgets.QVBoxLayout)
        mode_group, mode_group_layout = create_group_box("模态振型 3D", layout_type=QtWidgets.QVBoxLayout)
        layout_group_layout.addWidget(self.layout_plot)
        mode_group_layout.addWidget(self.mode_plot)
        preview_splitter.addWidget(layout_group)
        preview_splitter.addWidget(mode_group)
        preview_splitter.setStretchFactor(0, 1)
        preview_splitter.setStretchFactor(1, 1)

        plots.addWidget(frf_panel)
        plots.addWidget(preview_splitter)
        plots.setStretchFactor(0, 2)
        plots.setStretchFactor(1, 3)
        plots.setSizes([340, 520])
        plots.setMinimumHeight(560)
        layout.addWidget(controls)
        layout.addWidget(plots, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.load_folder_button.clicked.connect(self._choose_folder)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.import_mapping_button.clicked.connect(self._choose_import_mapping)
        self.export_mapping_button.clicked.connect(self._choose_export_mapping)
        self.add_point_button.clicked.connect(self._add_point_row)
        self.delete_point_button.clicked.connect(self._delete_point_rows)
        self.auto_lines_button.clicked.connect(lambda _checked=False: self.auto_build_lines())
        self.add_line_button.clicked.connect(self._add_line_row)
        self.delete_line_button.clicked.connect(self._delete_line_rows)
        self.apply_freq_button.clicked.connect(self.apply_frequency)
        self.find_peaks_button.clicked.connect(lambda _checked=False: self.find_peaks())
        self.delete_peak_button.clicked.connect(self.delete_selected_peak)
        self.view_azimuth_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.view_elevation_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.mode_gain_spin.valueChanged.connect(lambda _value: self._mode_gain_changed())
        self.view_reset_button.clicked.connect(self._reset_view)
        self._connect_modal_preview_widget(self.layout_plot)
        self._connect_modal_preview_widget(self.mode_plot)
        self.extract_button.clicked.connect(self.extract_mode)
        self.preview_button.clicked.connect(self.preview_mode)
        self.export_gif_button.clicked.connect(self._choose_export_gif)
        self.candidate_list.currentItemChanged.connect(lambda item, _previous: self._candidate_selected(item))
        self.point_table.itemChanged.connect(lambda _item: self._mapping_changed())
        self.line_table.itemChanged.connect(self._line_mapping_changed)

    def _set_modal_section(self, index: int) -> None:
        if not hasattr(self, "modal_section_stack"):
            return
        index = max(0, min(self.modal_section_stack.count() - 1, int(index)))
        self.modal_section_stack.setCurrentIndex(index)
        for button_index, button in enumerate(getattr(self, "modal_section_buttons", [])):
            active = button_index == index
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)
            set_button_role(button, "primary" if active else "secondary")
            self._refresh_button_style(button)

    @staticmethod
    def _refresh_button_style(button: QtWidgets.QPushButton) -> None:
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _create_modal_preview_widget(self, title: str) -> QtWidgets.QWidget:
        disabled = os.environ.get("PYTHON_VNA_DISABLE_MODAL_OPENGL", "").strip().lower()
        if disabled in {"1", "true", "yes", "on"}:
            return self._create_modal_projection_widget(title)
        try:
            return Modal3DView()
        except Exception as exc:
            append_log(f"modal 3D preview disabled; using 2D fallback: {exc}")
            return self._create_modal_projection_widget(title)

    def _create_modal_projection_widget(self, title: str) -> pg.PlotWidget:
        plot = self._create_plot_widget(f"{title} (2D 投影兼容模式)")
        plot.setAspectLocked(False)
        return plot

    def _connect_modal_preview_widget(self, widget: QtWidgets.QWidget) -> None:
        if isinstance(widget, Modal3DView):
            widget.cameraChanged.connect(self._sync_view_from_3d_camera)

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载模态 VNA 文件",
            str(self._last_directory),
            "VNA 文件 (*.vna *.mat);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def _choose_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "加载模态 VNA 文件夹", str(self._last_directory))
        if not folder:
            return
        folder_path = Path(folder)
        paths = sorted(folder_path.glob("*.vna")) + sorted(folder_path.glob("*.mat"))
        if not paths:
            self._show_status("所选文件夹内没有 .vna 或 .mat 文件")
            return
        self.load_paths(paths)

    def load_paths(self, paths: list[Path]) -> None:
        if self._data_store is not None:
            self._load_paths_into_data_store(paths)
            return
        loaded = 0
        existing = {modal_file.path.name.lower() for modal_file in self.files}
        existing_point_files = {
            self._table_text(self.point_table, row, 2, "").lower()
            for row in range(self.point_table.rowCount())
            if self._table_text(self.point_table, row, 2, "")
        }
        previous_bulk = self._bulk_table_update
        previous_point_signals = self.point_table.blockSignals(True)
        previous_line_signals = self.line_table.blockSignals(True)
        previous_file_signals = self.file_list.blockSignals(True)
        self._bulk_table_update = True
        self.point_table.setUpdatesEnabled(False)
        self.line_table.setUpdatesEnabled(False)
        self.file_list.setUpdatesEnabled(False)
        try:
            for path in paths:
                if path.name.lower() in existing:
                    continue
                try:
                    dataset = load_analysis_path(path, dataset_id=len(self.files) + 1)
                except Exception as exc:
                    self._show_status(f"加载 {path.name} 失败：{exc}")
                    continue
                self.files.append(ModalFile(path=path, dataset=dataset))
                self.file_list.addItem(path.name)
                self._append_default_point_row_for_file(path.name, existing_files=existing_point_files)
                existing.add(path.name.lower())
                loaded += 1
        finally:
            self.file_list.setUpdatesEnabled(True)
            self.line_table.setUpdatesEnabled(True)
            self.point_table.setUpdatesEnabled(True)
            self.file_list.blockSignals(previous_file_signals)
            self.line_table.blockSignals(previous_line_signals)
            self.point_table.blockSignals(previous_point_signals)
            self._bulk_table_update = previous_bulk
        self._remember_paths(paths)
        if loaded:
            self.auto_build_lines(show_status=False, refresh=False)
        self._invalidate_mode()
        self._refresh_layout_plot()
        self.find_peaks()
        self._show_status(f"已加载模态文件：{loaded} 个")

    def _load_paths_into_data_store(self, paths: list[Path]) -> None:
        loaded = 0
        existing_names = {
            Path(dataset.path).name.lower()
            for dataset in getattr(self._data_store, "datasets", [])
            if self._dataset_supports_modal(dataset)
        }
        for path in paths:
            if path.name.lower() in existing_names:
                continue
            try:
                dataset_id = int(getattr(self._data_store, "next_dataset_id", 1))
                dataset = load_analysis_path(path, dataset_id=dataset_id)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self._data_store.next_dataset_id = dataset_id + 1
            self._data_store.datasets.append(dataset)
            existing_names.add(path.name.lower())
            loaded += 1
        self._remember_paths(paths)
        if loaded:
            self._data_store.changed.emit("load", self)
        else:
            self.sync_from_data_store(show_status=False)
        self._show_status(f"已加载模态文件：{loaded} 个")

    def _on_shared_data_store_changed(self, reason: str, payload: object) -> None:
        append_log(f"modal.shared.begin reason={reason} payload_self={payload is self}")
        if payload is self:
            append_log(f"modal.shared.self_sync.begin reason={reason}")
            self.sync_from_data_store(show_status=False)
            append_log(f"modal.shared.self_sync.end reason={reason}")
            return
        if reason in {"delete", "clear"}:
            self._pending_data_store_sync_reason = None
            append_log(f"modal.shared.delete_sync.begin reason={reason}")
            self._sync_removed_data_store_files_without_plotting(show_status=False)
            append_log(f"modal.shared.delete_sync.end reason={reason}")
            return
        if reason == "load":
            self._pending_data_store_sync_reason = None
            append_log("modal.shared.load_sync.begin")
            self.sync_from_data_store(show_status=False, refresh_candidates=reason == "load")
            append_log("modal.shared.load_sync.end")
            return
        if reason in {"refresh", "current_measurement"}:
            self._pending_data_store_sync_reason = reason
            append_log(f"modal.shared.timer_schedule reason={reason}")
            QtCore.QTimer.singleShot(0, self._flush_data_store_sync)

    def _flush_data_store_sync(self) -> None:
        reason = self._pending_data_store_sync_reason or "refresh"
        self._pending_data_store_sync_reason = None
        self.sync_from_data_store(show_status=False, refresh_candidates=reason not in {"delete", "clear"})

    def _sync_removed_data_store_files_without_plotting(self, *, show_status: bool = True) -> None:
        append_log("modal.sync_removed.begin")
        if self._data_store is None:
            append_log("modal.sync_removed.no_store")
            return
        self._invalidate_mode()
        append_log("modal.sync_removed.after_invalidate")
        datasets = [
            dataset
            for dataset in getattr(self._data_store, "datasets", [])
            if self._dataset_supports_modal(dataset)
        ]
        previous_names = [modal_file.path.name.lower() for modal_file in self.files]
        next_files = [ModalFile(path=Path(dataset.path), dataset=dataset) for dataset in datasets]
        next_names = [modal_file.path.name.lower() for modal_file in next_files]
        removed_names = set(previous_names) - set(next_names)
        append_log(
            f"modal.sync_removed.names previous={len(previous_names)} next={len(next_names)} removed={len(removed_names)}"
        )

        previous_bulk = self._bulk_table_update
        previous_point_signals = self.point_table.blockSignals(True)
        previous_line_signals = self.line_table.blockSignals(True)
        previous_file_signals = self.file_list.blockSignals(True)
        self._bulk_table_update = True
        self.point_table.setUpdatesEnabled(False)
        self.line_table.setUpdatesEnabled(False)
        self.file_list.setUpdatesEnabled(False)
        try:
            append_log("modal.sync_removed.table_update.begin")
            for row in range(self.point_table.rowCount() - 1, -1, -1):
                file_name = self._table_text(self.point_table, row, 2, "").lower()
                if file_name in removed_names:
                    self.point_table.removeRow(row)
            self.files = next_files
            self.file_list.clear()
            for modal_file in self.files:
                self.file_list.addItem(modal_file.path.name)
            append_log("modal.sync_removed.table_update.end")
        finally:
            self.file_list.setUpdatesEnabled(True)
            self.line_table.setUpdatesEnabled(True)
            self.point_table.setUpdatesEnabled(True)
            self.file_list.blockSignals(previous_file_signals)
            self.line_table.blockSignals(previous_line_signals)
            self.point_table.blockSignals(previous_point_signals)
            self._bulk_table_update = previous_bulk
            append_log("modal.sync_removed.table_update.finally")

        if removed_names:
            append_log("modal.sync_removed.remove_invalid_lines.begin")
            self._remove_invalid_lines()
            append_log("modal.sync_removed.remove_invalid_lines.end")
        if not self.files:
            append_log("modal.sync_removed.clear_lists.begin")
            self._clear_candidate_list_safely("sync_removed")
            self._auto_peaks.clear()
            self._manual_peaks.clear()
            self._active_frequency = None
            append_log("modal.sync_removed.clear_lists.end")
        if show_status:
            self._show_status(f"已同步 VNA 数据到模态振型：{len(self.files)} 个")

    def sync_from_data_store(self, *, show_status: bool = True, refresh_candidates: bool = True) -> None:
        if self._data_store is None:
            return
        self._invalidate_mode()
        datasets = [
            dataset
            for dataset in getattr(self._data_store, "datasets", [])
            if self._dataset_supports_modal(dataset)
        ]
        existing_point_files = {
            self._table_text(self.point_table, row, 2, "").lower()
            for row in range(self.point_table.rowCount())
            if self._table_text(self.point_table, row, 2, "")
        }
        previous_names = [modal_file.path.name.lower() for modal_file in self.files]
        next_files = [ModalFile(path=Path(dataset.path), dataset=dataset) for dataset in datasets]
        next_names = [modal_file.path.name.lower() for modal_file in next_files]
        if previous_names == next_names and all(
            old.dataset is new.dataset for old, new in zip(self.files, next_files, strict=False)
        ):
            return
        removed_names = set(previous_names) - set(next_names)

        previous_bulk = self._bulk_table_update
        previous_point_signals = self.point_table.blockSignals(True)
        previous_line_signals = self.line_table.blockSignals(True)
        previous_file_signals = self.file_list.blockSignals(True)
        self._bulk_table_update = True
        self.point_table.setUpdatesEnabled(False)
        self.line_table.setUpdatesEnabled(False)
        self.file_list.setUpdatesEnabled(False)
        appended_points = 0
        try:
            for row in range(self.point_table.rowCount() - 1, -1, -1):
                file_name = self._table_text(self.point_table, row, 2, "").lower()
                if file_name in removed_names:
                    self.point_table.removeRow(row)
            self.files = next_files
            self.file_list.clear()
            for modal_file in self.files:
                self.file_list.addItem(modal_file.path.name)
                if modal_file.path.name.lower() not in existing_point_files:
                    self._append_default_point_row_for_file(modal_file.path.name, existing_files=existing_point_files)
                    existing_point_files.add(modal_file.path.name.lower())
                    appended_points += 1
        finally:
            self.file_list.setUpdatesEnabled(True)
            self.line_table.setUpdatesEnabled(True)
            self.point_table.setUpdatesEnabled(True)
            self.file_list.blockSignals(previous_file_signals)
            self.line_table.blockSignals(previous_line_signals)
            self.point_table.blockSignals(previous_point_signals)
            self._bulk_table_update = previous_bulk

        if removed_names:
            self._remove_invalid_lines()
        if appended_points:
            self.auto_build_lines(show_status=False, refresh=False)
        self._refresh_layout_plot()
        if self.files and refresh_candidates:
            self.find_peaks()
        else:
            self._clear_candidate_list_safely("sync_from_data_store")
            self._auto_peaks.clear()
            self._manual_peaks.clear()
            self._clear_plot_widget(self.frf_plot)
        if show_status:
            self._show_status(f"已同步 VNA 数据到模态振型：{len(self.files)} 个")

    @staticmethod
    def _dataset_supports_modal(dataset: AnalysisDataset) -> bool:
        frequency = getattr(dataset, "frequency_hz", None)
        if frequency is None or np.asarray(frequency).size == 0:
            return False
        return bool(getattr(dataset, "frf", None) or getattr(dataset, "autospectrum", None))

    def clear(self) -> None:
        self._preview_timer.stop()
        self.files.clear()
        self.file_list.clear()
        self._clear_candidate_list_safely("clear")
        self.point_table.setRowCount(0)
        self.line_table.setRowCount(0)
        self.last_mode = None
        self._active_frequency = None
        self._auto_peaks.clear()
        self._manual_peaks.clear()
        for plot in (self.frf_plot, self.layout_plot, self.mode_plot):
            plot.clear()
        self._plot_curves.clear()
        self._show_status("模态振型页面已清空")

    def find_peaks(self) -> list[float]:
        freq, db_values = self._aggregate_frf_curve()
        self._clear_candidate_list_safely("find_peaks")
        self._auto_peaks = []
        if freq.size < 3:
            self._show_status("没有可用于峰值搜索的有效 FRF 曲线")
            return []
        aggregate_peaks, smooth = find_prominent_modal_peaks(freq, db_values, max_count=12)
        local_peaks = self._individual_peak_frequencies(max_per_curve=4)
        peaks = merge_frequency_candidates(aggregate_peaks + local_peaks, max_count=24)
        if not peaks:
            peaks = local_peak_frequencies(freq, db_values, max_count=1)
        self._auto_peaks = peaks
        self._refresh_candidate_list()
        self._render_frf_candidates(freq, db_values, smooth)
        if peaks:
            self._active_frequency = float(peaks[0])
            self.frequency_edit.setValue(float(peaks[0]))
            self._refresh_candidate_list()
            self._render_frf_candidates(freq, db_values, smooth)
        self._show_status(f"已找到模态峰值候选：{len(peaks)} 个")
        return peaks

    def apply_frequency(self) -> None:
        value = float(self.frequency_edit.value())
        if not np.isfinite(value) or value <= 0.0:
            self._show_status("模态频率必须为正数")
            return
        self._active_frequency = value
        self._manual_peaks = merge_frequency_candidates(self._manual_peaks + [value], max_count=24)
        self._refresh_candidate_list()
        freq, db_values = self._aggregate_frf_curve()
        if freq.size:
            self._render_frf_candidates(freq, db_values, moving_average(db_values, 5))
        self._invalidate_mode()
        self._show_status(f"已应用模态频率：{value:.8g} Hz")

    def delete_selected_peak(self) -> None:
        item = self.candidate_list.currentItem()
        freq = self._candidate_frequency(item)
        if freq is None:
            self._show_status("未选择峰值候选")
            return
        self._auto_peaks = remove_matching_frequency(self._auto_peaks, freq)
        self._manual_peaks = remove_matching_frequency(self._manual_peaks, freq)
        if self._active_frequency is not None and frequencies_match(self._active_frequency, freq):
            self._active_frequency = None
            self.last_mode = None
        self._refresh_candidate_list()
        freq_axis, db_values = self._aggregate_frf_curve()
        if freq_axis.size:
            self._render_frf_candidates(freq_axis, db_values, moving_average(db_values, 5))
        self._show_status(f"已删除峰值候选：{freq:.8g} Hz")

    def preview_mode(self) -> None:
        mode = self.extract_mode()
        if mode is None:
            return
        if hasattr(self, "preview_tabs") and hasattr(self, "mode_preview_tab"):
            self.preview_tabs.setCurrentWidget(self.mode_preview_tab)
        self._preview_phase_index = 0
        self._preview_timer.start()
        self._show_status("正在预览模态动画")

    def extract_mode(self) -> dict[str, object] | None:
        if self._active_frequency is None:
            self.apply_frequency()
        target = float(self._active_frequency if self._active_frequency is not None else self.frequency_edit.value())
        points = self._point_rows(require_enabled=True, require_bound=True)
        groups = self._point_groups(points)
        if not groups:
            self._show_status("没有启用的测点映射行")
            return None
        coords: list[list[float]] = []
        displacements: list[list[complex]] = []
        displacement_signs: list[list[float]] = []
        labels: list[str] = []
        actual_freqs: list[float] = []
        source_files: list[str] = []
        coherence_values: list[list[float]] = []
        for group in groups:
            disp, scale_signs, actual, coh_vec, file_label = self._extract_group_mode_vector(group["rows"], target)
            if disp is None:
                continue
            coords.append(group["coords"])
            displacements.append(disp)
            displacement_signs.append(scale_signs)
            labels.append(str(group["point"]))
            if np.isfinite(actual):
                actual_freqs.append(float(actual))
            source_files.append(file_label)
            coherence_values.append(coh_vec)
        if not coords:
            self._show_status("模态提取未找到有效测点数据")
            return None
        coord_arr = np.asarray(coords, dtype=float)
        disp_complex = np.asarray(displacements, dtype=complex)
        scale_sign_arr = np.asarray(displacement_signs, dtype=float)
        ref_value = first_nonzero_complex(disp_complex, scale_sign_arr)
        if ref_value is not None:
            disp_complex = disp_complex / ref_value
        disp_complex[~np.isfinite(np.real(disp_complex)) | ~np.isfinite(np.imag(disp_complex))] = 0.0
        disp_real = np.real(disp_complex)
        base_scale = safe_mode_scale(coord_arr, disp_real)
        scale = base_scale * self._mode_display_gain()
        mode = {
            "requested_frequency": target,
            "actual_frequency": float(np.nanmedian(actual_freqs)) if actual_freqs else target,
            "coords": coord_arr,
            "displacements": disp_real,
            "disp_complex": disp_complex,
            "coherence": np.asarray(coherence_values, dtype=float),
            "file_names": source_files,
            "base_scale": base_scale,
            "labels": labels,
            "scale": scale,
            "lines": self._line_rows(require_enabled=True),
        }
        self.last_mode = mode
        self._preview_timer.stop()
        self._render_layout(coord_arr, labels)
        self._render_mode(mode, phase=0.0)
        self._show_status(f"已提取 {target:.6g} Hz 附近的模态")
        return mode

    def export_mode_gif(self, path: str | Path) -> Path:
        if self.last_mode is None:
            extracted = self.extract_mode()
            if extracted is None:
                raise ValueError("没有可导出的模态振型。")
        destination = Path(path)
        try:
            write_mode_animation_gif(
                destination,
                self.last_mode,
                frame_count=self._gif_frame_count(),
                azimuth_deg=self._view_azimuth,
                elevation_deg=self._view_elevation,
            )
        except Exception as exc:
            self._show_status(f"GIF 导出失败：{exc}")
            raise ValueError(f"GIF 导出失败：{exc}") from exc
        self._show_status(f"已导出 GIF：{destination.name}")
        return destination

    def _refresh_default_point_rows(self) -> None:
        if self.point_table.rowCount() > 0:
            return
        for modal_file in self.files:
            self._append_default_point_row_for_file(modal_file.path.name)
        self.auto_build_lines(show_status=False)

    def _refresh_layout_plot(self) -> None:
        groups = self._point_groups(self._point_rows(require_enabled=True, require_bound=False))
        if not groups:
            self.layout_plot.clear()
            return
        coords = np.asarray([group["coords"] for group in groups], dtype=float)
        labels = [str(group["point"]) for group in groups]
        self._render_layout(coords, labels)

    def _point_rows(self, *, require_enabled: bool, require_bound: bool) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row in range(self.point_table.rowCount()):
            if require_enabled and not self._table_bool(self.point_table, row, 0, True):
                continue
            file_name = self._table_text(self.point_table, row, 2, "")
            if require_bound and not file_name:
                continue
            try:
                x = float(self._table_text(self.point_table, row, 9, "0"))
                y = float(self._table_text(self.point_table, row, 10, "0"))
                z = float(self._table_text(self.point_table, row, 11, "0"))
                if not all(np.isfinite([x, y, z])):
                    continue
                rows.append(
                    {
                        "point": self._table_text(self.point_table, row, 1, f"P{row + 1}"),
                        "file": file_name,
                        "x_ch": int(float(self._table_text(self.point_table, row, 3, "1"))),
                        "y_ch": int(float(self._table_text(self.point_table, row, 4, "2"))),
                        "z_ch": int(float(self._table_text(self.point_table, row, 5, "3"))),
                        "x_scale": float(self._table_text(self.point_table, row, 6, "1")),
                        "y_scale": float(self._table_text(self.point_table, row, 7, "1")),
                        "z_scale": float(self._table_text(self.point_table, row, 8, "1")),
                        "x": x,
                        "y": y,
                        "z": z,
                    }
                )
            except ValueError:
                continue
        return rows

    def _line_rows(self, *, require_enabled: bool) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row in range(self.line_table.rowCount()):
            if require_enabled and not self._table_bool(self.line_table, row, 0, True):
                continue
            start = self._normalize_line_point_text(self._table_text(self.line_table, row, 1, ""))
            end = self._normalize_line_point_text(self._table_text(self.line_table, row, 2, ""))
            if not start or not end or start == end:
                continue
            rows.append({"start": start, "end": end, "source": self._table_text(self.line_table, row, 3, "manual")})
        return rows

    def _point_groups(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            point = str(row["point"]).strip() or "P"
            key = point.upper()
            coords = np.asarray([row["x"], row["y"], row["z"]], dtype=float)
            if key not in grouped:
                grouped[key] = {"point": point, "coords_list": [coords], "rows": [row]}
            else:
                grouped[key]["coords_list"].append(coords)
                grouped[key]["rows"].append(row)
        result: list[dict[str, object]] = []
        for group in grouped.values():
            coord_stack = np.vstack(group["coords_list"])
            result.append(
                {
                    "point": group["point"],
                    "coords": np.nanmean(coord_stack, axis=0).astype(float).tolist(),
                    "rows": group["rows"],
                }
            )
        return result

    def _dataset_by_name(self, name: str) -> AnalysisDataset | None:
        raw = str(name or "").strip()
        if not raw:
            return None
        requested = Path(raw).name
        requested_lower = requested.lower()
        requested_stem = Path(requested).stem.lower()
        for modal_file in self.files:
            aliases = {
                modal_file.path.name.lower(),
                modal_file.path.stem.lower(),
                modal_file.dataset.name.lower(),
                Path(modal_file.dataset.name).stem.lower(),
            }
            if requested_lower in aliases or requested_stem in aliases:
                return modal_file.dataset
        return None

    def _modal_channel_value(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> float:
        value = self._modal_channel_value_complex(dataset, channel_number, freq_index)
        return float(np.real(value)) if np.isfinite(np.real(value)) else 0.0

    def _modal_channel_series_key(
        self,
        series_map: dict[str, np.ndarray],
        dataset: AnalysisDataset,
        channel_number: int,
    ) -> str | None:
        keys = list(series_map)
        if not keys:
            return None
        try:
            channel_index = int(channel_number) - 1
        except (TypeError, ValueError):
            return None
        if channel_index < 0:
            return None

        aliases = {f"ai{channel_index}", str(channel_number)}
        channel_keys = dataset.channel_keys
        if channel_index < len(channel_keys):
            aliases.add(str(channel_keys[channel_index]))
        for series in dataset.series:
            if int(getattr(series, "channel_index", -1)) == channel_index:
                aliases.add(str(series.channel_key))
                aliases.add(str(series.display_name))
        aliases = {alias.strip().lower() for alias in aliases if str(alias).strip()}

        for key in keys:
            text = str(key).strip()
            if "->" not in text:
                continue
            _left, right = text.split("->", 1)
            if right.strip().lower() in aliases:
                return key
        for key in keys:
            if str(key).strip().lower() in aliases:
                return key

        transfer_keys = [key for key in keys if "->" in str(key)]
        if transfer_keys:
            response_index = channel_index - 1
            if 0 <= response_index < len(transfer_keys):
                return transfer_keys[response_index]
            return None
        if channel_index < len(keys):
            return keys[channel_index]
        return None

    def _modal_channel_value_complex(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> complex:
        source = None
        key = self._modal_channel_series_key(dataset.frf, dataset, channel_number)
        if key is not None:
            source = dataset.frf.get(key)
        if source is None:
            key = self._modal_channel_series_key(dataset.autospectrum, dataset, channel_number)
            if key is not None:
                source = dataset.autospectrum.get(key)
        if source is None:
            return 0.0 + 0.0j
        arr = np.asarray(source).ravel()
        if arr.size == 0:
            return 0.0 + 0.0j
        value = arr[min(freq_index, arr.size - 1)]
        return complex(value) if np.isfinite(np.real(value)) and np.isfinite(np.imag(value)) else 0.0 + 0.0j

    def _modal_channel_coherence(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> float:
        key = self._modal_channel_series_key(dataset.coherence, dataset, channel_number)
        if key is None:
            return float("nan")
        values = dataset.coherence.get(key)
        if values is None:
            return float("nan")
        arr = np.asarray(values, dtype=float).ravel()
        if arr.size == 0:
            return float("nan")
        return float(arr[min(freq_index, arr.size - 1)])

    def _extract_group_mode_vector(
        self,
        rows: list[dict[str, object]],
        target: float,
    ) -> tuple[list[complex] | None, list[float], float, list[float], str]:
        vector: list[complex | None] = [None, None, None]
        scale_signs = [1.0, 1.0, 1.0]
        coherence = [float("nan"), float("nan"), float("nan")]
        actual_freqs: list[float] = []
        file_names: list[str] = []
        for row in rows:
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
            if freq.size == 0:
                continue
            nearest = int(np.nanargmin(np.abs(freq - target)))
            actual_freqs.append(float(freq[nearest]))
            file_name = str(row["file"])
            if file_name and file_name not in file_names:
                file_names.append(file_name)
            channels = [int(row["x_ch"]), int(row["y_ch"]), int(row["z_ch"])]
            scales = [float(row["x_scale"]), float(row["y_scale"]), float(row["z_scale"])]
            for axis_index, (channel, scale) in enumerate(zip(channels, scales)):
                value = self._modal_channel_value_complex(dataset, channel, nearest) * (scale if np.isfinite(scale) else 1.0)
                if abs(value) <= 0.0:
                    continue
                coh = self._modal_channel_coherence(dataset, channel, nearest)
                if vector[axis_index] is None or (
                    np.isfinite(coh) and (not np.isfinite(coherence[axis_index]) or coh > coherence[axis_index])
                ):
                    vector[axis_index] = value
                    scale_signs[axis_index] = -1.0 if np.isfinite(scale) and scale < 0.0 else 1.0
                    coherence[axis_index] = coh
        if all(value is None for value in vector):
            return None, scale_signs, float("nan"), coherence, ", ".join(file_names)
        return (
            [value if value is not None else 0.0 + 0.0j for value in vector],
            scale_signs,
            float(np.nanmedian(actual_freqs)) if actual_freqs else target,
            coherence,
            ", ".join(file_names),
        )

    def _aggregate_frf_curve(self) -> tuple[np.ndarray, np.ndarray]:
        rows = self._point_rows(require_enabled=True, require_bound=True)
        ref_freq: np.ndarray | None = None
        curves: list[np.ndarray] = []
        for row in rows:
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            for channel in (int(row["x_ch"]), int(row["y_ch"]), int(row["z_ch"])):
                series_values = self._channel_frf_series(dataset, channel)
                if series_values is None:
                    continue
                freq, values = series_values
                mag = np.abs(values)
                mask = np.isfinite(freq) & (freq > 0.0) & np.isfinite(mag) & (mag > 0.0)
                if np.count_nonzero(mask) < 8:
                    continue
                freq_valid = np.asarray(freq[mask], dtype=float)
                mag_valid = np.asarray(mag[mask], dtype=float)
                if ref_freq is None:
                    ref_freq = freq_valid
                    curves.append(mag_valid)
                    continue
                try:
                    curves.append(np.interp(ref_freq, freq_valid, mag_valid, left=np.nan, right=np.nan))
                except ValueError:
                    continue
        if ref_freq is None or not curves:
            return np.array([], dtype=float), np.array([], dtype=float)
        mag_mean = np.nanmean(np.vstack(curves), axis=0)
        valid = np.isfinite(ref_freq) & np.isfinite(mag_mean) & (ref_freq > 0.0) & (mag_mean > 0.0)
        return ref_freq[valid], 20.0 * np.log10(np.maximum(mag_mean[valid], 1e-300))

    def _channel_frf_series(self, dataset: AnalysisDataset, channel_number: int) -> tuple[np.ndarray, np.ndarray] | None:
        source = None
        key = self._modal_channel_series_key(dataset.frf, dataset, channel_number)
        if key is not None:
            source = dataset.frf.get(key)
        if source is None:
            key = self._modal_channel_series_key(dataset.autospectrum, dataset, channel_number)
            if key is not None:
                source = dataset.autospectrum.get(key)
        freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        if source is None or freq.size == 0:
            return None
        values = np.asarray(source).ravel()
        count = min(freq.size, values.size)
        if count < 8:
            return None
        if float(np.nanmax(np.abs(values[:count]))) <= 0.0:
            return None
        return freq[:count], values[:count]

    def _individual_peak_frequencies(self, *, max_per_curve: int) -> list[float]:
        peaks: list[float] = []
        for row in self._point_rows(require_enabled=True, require_bound=True):
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            for channel in (int(row["x_ch"]), int(row["y_ch"]), int(row["z_ch"])):
                series = self._channel_frf_series(dataset, channel)
                if series is None:
                    continue
                freq, values = series
                mag = np.abs(np.asarray(values).ravel())
                mask = np.isfinite(freq) & (freq > 0.0) & np.isfinite(mag) & (mag > 0.0)
                if np.count_nonzero(mask) < 8:
                    continue
                db = 20.0 * np.log10(np.maximum(mag[mask], 1e-300))
                local_peaks, _smooth = find_prominent_modal_peaks(freq[mask], db, max_count=max_per_curve)
                peaks.extend(local_peaks)
        return peaks

    def _render_layout(self, coords: np.ndarray, labels: list[str]) -> None:
        if isinstance(self.layout_plot, Modal3DView):
            self.layout_plot.render_structure(
                coords,
                labels,
                self._line_rows(require_enabled=True),
                deformed=None,
                disp_complex=None,
                show_labels=True,
                azimuth=self._view_azimuth,
                elevation=self._view_elevation,
            )
            return
        self.layout_plot.clear()
        self.layout_plot.setTitle("结构布局 (2D 投影兼容模式)")
        self.layout_plot.setLabel("bottom", "投影 X")
        self.layout_plot.setLabel("left", "投影 Y")
        if coords.size == 0:
            return
        projected = project_points_3d(coords, azimuth_deg=self._view_azimuth, elevation_deg=self._view_elevation)
        self.layout_plot.plot(projected[:, 0], projected[:, 1], pen=None, symbol="o", symbolBrush="#1f77b4")
        self._render_lines(self.layout_plot, projected, labels, target=None)
        self.layout_plot.enableAutoRange()

    def _render_mode(self, mode: dict[str, object], *, phase: float) -> None:
        coords = np.asarray(mode["coords"], dtype=float)
        disp_complex = np.asarray(mode.get("disp_complex", mode.get("displacements")), dtype=complex)
        scale = float(mode.get("scale", 1.0))
        labels = [str(label) for label in mode.get("labels", [])]
        disp_now = np.real(disp_complex * np.exp(1j * phase))
        deformed = coords + disp_now * scale
        if isinstance(self.mode_plot, Modal3DView):
            self.mode_plot.render_structure(
                coords,
                labels,
                list(mode.get("lines", [])),
                deformed=deformed,
                disp_complex=disp_complex * scale,
                show_labels=False,
                azimuth=self._view_azimuth,
                elevation=self._view_elevation,
            )
            return
        self.mode_plot.clear()
        self.mode_plot.setTitle("模态振型 (2D 投影兼容模式)")
        self.mode_plot.setLabel("bottom", "投影 X")
        self.mode_plot.setLabel("left", "投影 Y")
        projected = project_points_3d(coords, azimuth_deg=self._view_azimuth, elevation_deg=self._view_elevation)
        projected_deformed = project_points_3d(
            deformed,
            azimuth_deg=self._view_azimuth,
            elevation_deg=self._view_elevation,
        )
        self.mode_plot.plot(
            projected_deformed[:, 0],
            projected_deformed[:, 1],
            pen=pg.mkPen("#d7263d", width=1.5),
            symbol="o",
            symbolBrush="#d7263d",
        )
        self._render_lines(self.mode_plot, projected, labels, target=projected_deformed)
        self.mode_plot.enableAutoRange()

    def _render_lines(
        self,
        plot: pg.PlotWidget,
        coords: np.ndarray,
        labels: list[str],
        *,
        target: np.ndarray | None,
    ) -> None:
        point_index = {label: index for index, label in enumerate(labels)}
        target_coords = target if target is not None else coords
        for row in self._line_rows(require_enabled=True):
            left = str(row["start"])
            right = str(row["end"])
            if left not in point_index or right not in point_index:
                continue
            i0 = point_index[left]
            i1 = point_index[right]
            plot.plot(
                [target_coords[i0, 0], target_coords[i1, 0]],
                [target_coords[i0, 1], target_coords[i1, 1]],
                pen=pg.mkPen("#5c677d" if target is None else "#d7263d", width=1.0),
            )

    def _candidate_selected(self, item: QtWidgets.QListWidgetItem | None) -> None:
        value = self._candidate_frequency(item)
        if value is None:
            return
        self.frequency_edit.setValue(value)
        self._active_frequency = value
        self._refresh_candidate_list()
        self._invalidate_mode()
        self.extract_mode()

    def _choose_export_gif(self) -> None:
        default_name = "mode_shape.gif"
        if self.last_mode is not None:
            actual = self.last_mode.get("actual_frequency")
            if isinstance(actual, (float, int)) and np.isfinite(actual):
                default_name = f"{float(actual):.8g}Hz.gif"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出模态振型 GIF",
            str(self._last_directory / default_name),
            "GIF 文件 (*.gif)",
        )
        if path:
            self.export_mode_gif(path)

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                removed = self.files.pop(row)
                self.file_list.takeItem(row)
                self._remove_point_rows_for_file(removed.path.name)
        self._remove_invalid_lines()
        self._invalidate_mode()
        self._refresh_layout_plot()
        self._show_status(f"已删除模态文件：{len(rows)} 个")

    def _choose_import_mapping(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "导入测点映射",
            str(self._last_directory),
            "映射表 (*.xlsx *.csv);;Excel 文件 (*.xlsx);;CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            count = self.import_point_mapping(path)
        except Exception as exc:
            self._show_status(f"导入测点映射失败：{exc}")
            return
        self._remember_paths([Path(path)])
        self._show_status(f"已导入测点映射：{count} 行")

    def _choose_export_mapping(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出测点映射",
            str(self._last_directory / "point_mapping.xlsx"),
            "Excel 文件 (*.xlsx);;CSV 文件 (*.csv)",
        )
        if not path:
            return
        try:
            self.export_point_mapping(path)
        except Exception as exc:
            self._show_status(f"导出测点映射失败：{exc}")
            return
        self._show_status(f"已导出测点映射：{Path(path).name}")

    def import_point_mapping(self, path: str | Path) -> int:
        source = Path(path)
        if source.suffix.lower() == ".xlsx":
            return self.import_point_mapping_xlsx(source)
        return self.import_point_mapping_csv(source)

    def import_point_mapping_csv(self, path: str | Path) -> int:
        with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            records = list(reader)
        return self._import_point_mapping_records(records)

    def import_point_mapping_xlsx(self, path: str | Path) -> int:
        try:
            openpyxl = require("openpyxl", "python -m pip install -e .[gui]")
        except RuntimeError:
            rows = read_xlsx_rows_basic(path)
        else:
            workbook = openpyxl.load_workbook(Path(path), data_only=True, read_only=True)
            try:
                sheet = workbook["PointMap"] if "PointMap" in workbook.sheetnames else workbook[workbook.sheetnames[0]]
                rows = list(sheet.iter_rows(values_only=True))
            finally:
                workbook.close()
        if not rows:
            self.point_table.setRowCount(0)
            return 0
        headers = [str(value or "").strip().lower() for value in rows[0]]
        records: list[dict[str, object]] = []
        for values in rows[1:]:
            if values is None or not any(str(value or "").strip() for value in values):
                continue
            records.append({headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))})
        return self._import_point_mapping_records(records)

    def _import_point_mapping_records(self, records: list[dict[str, object]]) -> int:
        previous_bulk = self._bulk_table_update
        previous_point_signals = self.point_table.blockSignals(True)
        previous_line_signals = self.line_table.blockSignals(True)
        self._bulk_table_update = True
        self.point_table.setUpdatesEnabled(False)
        self.line_table.setUpdatesEnabled(False)
        try:
            self.point_table.setRowCount(0)
            for record in records:
                values = [
                    self._csv_bool(record.get("use"), True),
                    self._mapping_text(record, "point_id", "point"),
                    self._mapping_text(record, "file_name", "file"),
                    self._mapping_text(record, "x_ch", default="2"),
                    self._mapping_text(record, "y_ch", default="3"),
                    self._mapping_text(record, "z_ch", default="4"),
                    self._mapping_text(record, "x_scale", default="1"),
                    self._mapping_text(record, "y_scale", default="1"),
                    self._mapping_text(record, "z_scale", default="1"),
                    self._mapping_text(record, "x", default="0"),
                    self._mapping_text(record, "y", default="0"),
                    self._mapping_text(record, "z", default="0"),
                ]
                if not str(values[1]).strip():
                    continue
                self._insert_point_row(values)
            self._remove_invalid_lines()
        finally:
            self.line_table.setUpdatesEnabled(True)
            self.point_table.setUpdatesEnabled(True)
            self.line_table.blockSignals(previous_line_signals)
            self.point_table.blockSignals(previous_point_signals)
            self._bulk_table_update = previous_bulk
        self.auto_build_lines(show_status=False)
        return self.point_table.rowCount()

    def export_point_mapping(self, path: str | Path) -> Path:
        destination = Path(path)
        if destination.suffix.lower() == ".xlsx":
            return self.export_point_mapping_xlsx(destination)
        return self.export_point_mapping_csv(destination)

    def _point_mapping_export_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for row_index, row in enumerate(self._point_rows(require_enabled=False, require_bound=False)):
            records.append(
                {
                    "point_id": row["point"],
                    "file_name": row["file"],
                    "x_ch": row["x_ch"],
                    "y_ch": row["y_ch"],
                    "z_ch": row["z_ch"],
                    "x_scale": row["x_scale"],
                    "y_scale": row["y_scale"],
                    "z_scale": row["z_scale"],
                    "x": row["x"],
                    "y": row["y"],
                    "z": row["z"],
                    "use": "1" if self._table_bool(self.point_table, row_index, 0, True) else "0",
                }
            )
        return records

    @staticmethod
    def _mapping_text(record: dict[str, object], *keys: str, default: str = "") -> str:
        lowered = {str(key).strip().lower(): value for key, value in record.items()}
        for key in keys:
            value = lowered.get(str(key).strip().lower())
            if value is not None and str(value).strip():
                return str(value).strip()
        return default

    def export_point_mapping_csv(self, path: str | Path) -> Path:
        destination = Path(path)
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.POINT_CSV_HEADERS)
            writer.writeheader()
            for record in self._point_mapping_export_records():
                writer.writerow(record)
        return destination

    def export_point_mapping_xlsx(self, path: str | Path) -> Path:
        destination = Path(path)
        try:
            openpyxl = require("openpyxl", "python -m pip install -e .[gui]")
        except RuntimeError:
            write_xlsx_rows_basic(
                destination,
                [self.POINT_CSV_HEADERS]
                + [[record.get(header, "") for header in self.POINT_CSV_HEADERS] for record in self._point_mapping_export_records()],
            )
            return destination
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "PointMap"
        sheet.append(self.POINT_CSV_HEADERS)
        for record in self._point_mapping_export_records():
            sheet.append([record.get(header, "") for header in self.POINT_CSV_HEADERS])
        for cell in sheet["B"]:
            cell.number_format = "@"
        workbook.save(destination)
        workbook.close()
        return destination

    def auto_build_lines(self, *, show_status: bool = True, refresh: bool = True) -> None:
        groups = self._point_groups(self._point_rows(require_enabled=True, require_bound=False))
        manual_rows = [row for row in self._line_rows(require_enabled=False) if str(row["source"]).lower() != "auto"]
        previous_bulk = self._bulk_table_update
        previous_line_signals = self.line_table.blockSignals(True)
        self._bulk_table_update = True
        self.line_table.setUpdatesEnabled(False)
        try:
            self.line_table.setRowCount(0)
            for row in manual_rows:
                self._insert_line_row([True, row["start"], row["end"], row["source"]])
            if len(groups) >= 2:
                coords = np.asarray([group["coords"] for group in groups], dtype=float)
                labels = [str(group["point"]) for group in groups]
                edges = infer_modal_auto_edges(coords)
                existing = {tuple(sorted((str(row["start"]), str(row["end"])))) for row in manual_rows}
                for left, right in edges:
                    key = tuple(sorted((labels[left], labels[right])))
                    if key in existing:
                        continue
                    self._insert_line_row([True, labels[left], labels[right], "auto"])
        finally:
            self.line_table.setUpdatesEnabled(True)
            self.line_table.blockSignals(previous_line_signals)
            self._bulk_table_update = previous_bulk
        if refresh:
            self._refresh_layout_plot()
        self._invalidate_mode()
        if show_status:
            self._show_status("已自动生成结构连线")

    def _add_point_row(self) -> None:
        selected = self.file_list.currentRow()
        file_name = self.files[selected].path.name if 0 <= selected < len(self.files) else ""
        self._insert_point_row(self._default_point_values(file_name))
        self._refresh_layout_plot()
        self._show_status("已新增测点行")

    def _delete_point_rows(self) -> None:
        rows = sorted({index.row() for index in self.point_table.selectedIndexes()}, reverse=True)
        edit_row = self._row_number_from_edit(getattr(self, "point_row_edit", None), self.point_table.rowCount())
        if edit_row is not None:
            rows = [edit_row]
        if not rows and self.point_table.rowCount():
            rows = [self.point_table.currentRow() if self.point_table.currentRow() >= 0 else self.point_table.rowCount() - 1]
        for row in rows:
            if 0 <= row < self.point_table.rowCount():
                self.point_table.removeRow(row)
        self._remove_invalid_lines()
        self._invalidate_mode()
        self._refresh_layout_plot()
        self._show_status(f"已删除测点行：{len(rows)} 行")

    def _add_line_row(self) -> None:
        groups = self._point_groups(self._point_rows(require_enabled=True, require_bound=False))
        start = str(groups[0]["point"]) if groups else ""
        end = str(groups[1]["point"]) if len(groups) > 1 else ""
        self._insert_line_row([True, start, end, "manual"])
        self._refresh_layout_plot()
        self._show_status("已新增连线行")

    def _delete_line_rows(self) -> None:
        rows = sorted({index.row() for index in self.line_table.selectedIndexes()}, reverse=True)
        edit_row = self._row_number_from_edit(getattr(self, "line_row_edit", None), self.line_table.rowCount())
        if edit_row is not None:
            rows = [edit_row]
        if not rows and self.line_table.rowCount():
            rows = [self.line_table.currentRow() if self.line_table.currentRow() >= 0 else self.line_table.rowCount() - 1]
        for row in rows:
            if 0 <= row < self.line_table.rowCount():
                self.line_table.removeRow(row)
        self._invalidate_mode()
        self._refresh_layout_plot()
        self._show_status(f"已删除连线行：{len(rows)} 行")

    @staticmethod
    def _row_number_from_edit(edit: QtWidgets.QLineEdit | None, row_count: int) -> int | None:
        if edit is None:
            return None
        text = edit.text().strip()
        if not text:
            return None
        try:
            row_number = int(float(text))
        except ValueError:
            return None
        index = row_number - 1
        if 0 <= index < row_count:
            edit.clear()
            return index
        return None

    def _append_default_point_row_for_file(self, file_name: str, *, existing_files: set[str] | None = None) -> None:
        existing_files = (
            existing_files
            if existing_files is not None
            else {str(row["file"]).lower() for row in self._point_rows(require_enabled=False, require_bound=False)}
        )
        if file_name.lower() in existing_files:
            return
        self._insert_point_row(self._default_point_values(file_name))
        existing_files.add(file_name.lower())

    def _default_point_values(self, file_name: str) -> list[object]:
        index = self.point_table.rowCount()
        x_ch, y_ch, z_ch = self._default_modal_channels(file_name)
        return [True, f"P{index + 1}", file_name, x_ch, y_ch, z_ch, 1, 1, 1, float(index), 0.0, 0.0]

    def _default_modal_channels(self, file_name: str) -> tuple[int, int, int]:
        dataset = self._dataset_by_name(file_name)
        if dataset is None:
            return 2, 3, 4
        if any("->" in str(key) for key in dataset.frf):
            return 2, 3, 4
        if dataset.frf and len(dataset.frf) >= 4:
            return 2, 3, 4
        return 1, 2, 3

    def _insert_point_row(self, values: list[object]) -> None:
        row = self.point_table.rowCount()
        self.point_table.insertRow(row)
        for column, value in enumerate(values[: len(self.POINT_HEADERS)]):
            item = QtWidgets.QTableWidgetItem("" if isinstance(value, bool) else str(value))
            if column == 0:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked if bool(value) else QtCore.Qt.Unchecked)
            self.point_table.setItem(row, column, item)

    def _insert_line_row(self, values: list[object]) -> None:
        row = self.line_table.rowCount()
        self.line_table.insertRow(row)
        for column, value in enumerate(values[: len(self.LINE_HEADERS)]):
            text = "" if isinstance(value, bool) else str(value)
            if column in (1, 2):
                text = self._normalize_line_point_text(text)
            item = QtWidgets.QTableWidgetItem(text)
            if column == 0:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked if bool(value) else QtCore.Qt.Unchecked)
            self.line_table.setItem(row, column, item)

    def _remove_point_rows_for_file(self, file_name: str) -> None:
        for row in range(self.point_table.rowCount() - 1, -1, -1):
            if self._table_text(self.point_table, row, 2, "").lower() == file_name.lower():
                self.point_table.removeRow(row)

    def _remove_invalid_lines(self) -> None:
        valid_points = {str(group["point"]) for group in self._point_groups(self._point_rows(require_enabled=False, require_bound=False))}
        for row in range(self.line_table.rowCount() - 1, -1, -1):
            start = self._normalize_line_point_text(self._table_text(self.line_table, row, 1, ""))
            end = self._normalize_line_point_text(self._table_text(self.line_table, row, 2, ""))
            if start not in valid_points or end not in valid_points:
                self.line_table.removeRow(row)

    def _line_mapping_changed(self, item: QtWidgets.QTableWidgetItem | None) -> None:
        if self._bulk_table_update:
            return
        if item is not None and item.column() in (1, 2):
            normalized = self._normalize_line_point_text(item.text())
            if normalized and normalized != item.text().strip():
                self.line_table.blockSignals(True)
                item.setText(normalized)
                self.line_table.blockSignals(False)
        self._mapping_changed()

    def _normalize_line_point_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        groups = self._point_groups(self._point_rows(require_enabled=False, require_bound=False))
        valid_by_upper = {str(group["point"]).upper(): str(group["point"]) for group in groups}
        direct = valid_by_upper.get(raw.upper())
        if direct is not None:
            return direct
        match = re.fullmatch(r"[Pp]?\s*(\d+)(?:\.0+)?", raw)
        if match:
            candidate = f"P{int(match.group(1))}"
            return valid_by_upper.get(candidate.upper(), candidate)
        return raw

    def _mapping_changed(self) -> None:
        if self._bulk_table_update:
            return
        self._invalidate_mode()
        self._refresh_layout_plot()

    def _invalidate_mode(self) -> None:
        self._preview_timer.stop()
        self.last_mode = None

    def _advance_preview(self) -> None:
        if self.last_mode is None:
            self._preview_timer.stop()
            return
        frame_count = self._gif_frame_count()
        self._preview_phase_index = (self._preview_phase_index + 1) % frame_count
        phase = 2.0 * math.pi * self._preview_phase_index / float(frame_count)
        self._render_mode(self.last_mode, phase=phase)

    def _gif_frame_count(self) -> int:
        if hasattr(self, "gif_frame_count_spin"):
            return int(self.gif_frame_count_spin.value())
        return 24

    def _mode_display_gain(self) -> float:
        if hasattr(self, "mode_gain_spin"):
            return float(self.mode_gain_spin.value())
        return 1.0

    def _mode_gain_changed(self) -> None:
        if self.last_mode is None:
            return
        try:
            base_scale = float(self.last_mode.get("base_scale", self.last_mode.get("scale", 1.0)))
        except (TypeError, ValueError):
            base_scale = 1.0
        if not np.isfinite(base_scale) or base_scale <= 0.0:
            base_scale = 1.0
        self.last_mode["scale"] = base_scale * self._mode_display_gain()
        phase = 2.0 * math.pi * self._preview_phase_index / float(self._gif_frame_count())
        self._render_mode(self.last_mode, phase=phase)

    def _view_changed(self) -> None:
        self._view_azimuth = float(self.view_azimuth_spin.value())
        self._view_elevation = float(self.view_elevation_spin.value())
        for view in (getattr(self, "layout_plot", None), getattr(self, "mode_plot", None)):
            if isinstance(view, Modal3DView):
                view.set_view_angles(self._view_azimuth, self._view_elevation)
        self._refresh_layout_plot()
        if self.last_mode is not None:
            phase = 2.0 * math.pi * self._preview_phase_index / float(self._gif_frame_count())
            self._render_mode(self.last_mode, phase=phase)

    def _sync_view_from_3d_camera(self, azimuth: float, elevation: float) -> None:
        self._view_azimuth = float(azimuth)
        self._view_elevation = float(elevation)
        azimuth_value = int(round(max(self.view_azimuth_spin.minimum(), min(self.view_azimuth_spin.maximum(), azimuth))))
        elevation_value = int(round(max(self.view_elevation_spin.minimum(), min(self.view_elevation_spin.maximum(), elevation))))
        self.view_azimuth_spin.blockSignals(True)
        self.view_elevation_spin.blockSignals(True)
        self.view_azimuth_spin.setValue(azimuth_value)
        self.view_elevation_spin.setValue(elevation_value)
        self.view_azimuth_spin.blockSignals(False)
        self.view_elevation_spin.blockSignals(False)
        sender = self.sender()
        for view in (getattr(self, "layout_plot", None), getattr(self, "mode_plot", None)):
            if isinstance(view, Modal3DView) and view is not sender:
                view.set_view_angles(self._view_azimuth, self._view_elevation)

    def _reset_view(self) -> None:
        for view in (getattr(self, "layout_plot", None), getattr(self, "mode_plot", None)):
            if isinstance(view, Modal3DView):
                view.reset_camera_fit()
        self.view_azimuth_spin.blockSignals(True)
        self.view_elevation_spin.blockSignals(True)
        self.view_azimuth_spin.setValue(35)
        self.view_elevation_spin.setValue(24)
        self.view_azimuth_spin.blockSignals(False)
        self.view_elevation_spin.blockSignals(False)
        self._view_changed()

    def _rotate_view_by(self, delta_azimuth: float, delta_elevation: float) -> None:
        new_azimuth = int(round(self.view_azimuth_spin.value() + delta_azimuth))
        new_elevation = int(round(self.view_elevation_spin.value() + delta_elevation))
        new_azimuth = max(self.view_azimuth_spin.minimum(), min(self.view_azimuth_spin.maximum(), new_azimuth))
        new_elevation = max(self.view_elevation_spin.minimum(), min(self.view_elevation_spin.maximum(), new_elevation))
        self.view_azimuth_spin.blockSignals(True)
        self.view_elevation_spin.blockSignals(True)
        self.view_azimuth_spin.setValue(new_azimuth)
        self.view_elevation_spin.setValue(new_elevation)
        self.view_azimuth_spin.blockSignals(False)
        self.view_elevation_spin.blockSignals(False)
        self._view_changed()

    def _render_frf_candidates(self, freq: np.ndarray, db_values: np.ndarray, smooth: np.ndarray | None = None) -> None:
        curves = [CurvePair("综合 FRF", freq, db_values, "频率 (Hz)", "幅值 (dB)")]
        if smooth is not None and smooth.size == db_values.size:
            curves.append(CurvePair("平滑", freq, smooth, "频率 (Hz)", "幅值 (dB)"))
        self._plot_curves_on_widget(
            self.frf_plot,
            curves,
            title="模态 FRF 候选图",
            x_label="频率 (Hz)",
            y_label="幅值 (dB)",
            log_x=True,
        )
        all_peaks = merge_frequency_candidates(self._manual_peaks + self._auto_peaks, max_count=48)
        if all_peaks:
            peak_y = np.interp(np.asarray(all_peaks, dtype=float), freq, db_values)
            self.frf_plot.plot(
                np.asarray(all_peaks, dtype=float),
                peak_y,
                pen=None,
                symbol="o",
                symbolBrush="#ffffff",
                symbolPen=pg.mkPen("#d7263d", width=1.2),
                name="峰值候选",
            )
        if self._active_frequency is not None:
            active = float(self._active_frequency)
            y_min = float(np.nanmin(db_values))
            y_max = float(np.nanmax(db_values))
            if not np.isfinite(y_min) or not np.isfinite(y_max) or y_min == y_max:
                y_min, y_max = -1.0, 1.0
            self.frf_plot.plot([active, active], [y_min, y_max], pen=pg.mkPen("#d7263d", width=1.5), name="当前频率")

    def _clear_candidate_list_safely(self, reason: str) -> None:
        if not hasattr(self, "candidate_list"):
            return
        append_log(f"modal.candidates.clear.begin reason={reason} count={self.candidate_list.count()}")
        previous_signals = self.candidate_list.blockSignals(True)
        self.candidate_list.setUpdatesEnabled(False)
        try:
            self.candidate_list.setCurrentRow(-1)
            while self.candidate_list.count():
                item = self.candidate_list.takeItem(0)
                if item is not None:
                    item.setSelected(False)
                    self._retired_candidate_items.append(item)
        finally:
            self.candidate_list.setUpdatesEnabled(True)
            self.candidate_list.blockSignals(previous_signals)
            append_log(
                f"modal.candidates.clear.end reason={reason} "
                f"count={self.candidate_list.count()} retired={len(self._retired_candidate_items)}"
            )

    def _refresh_candidate_list(self) -> None:
        selected = self._active_frequency
        previous_signals = self.candidate_list.blockSignals(True)
        self._clear_candidate_list_safely("refresh_candidate_list")
        display: list[tuple[str, float]] = []
        for freq in self._manual_peaks:
            display.append((f"[手动] {freq:.8g} Hz", float(freq)))
        for index, freq in enumerate(self._auto_peaks, start=1):
            display.append((f"Peak {index:02d}: {freq:.8g} Hz", float(freq)))
        seen: list[float] = []
        selected_row = 0
        for label, freq in display:
            if any(frequencies_match(freq, old) for old in seen):
                continue
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, float(freq))
            self.candidate_list.addItem(item)
            if selected is not None and frequencies_match(freq, selected):
                selected_row = self.candidate_list.count() - 1
            seen.append(freq)
        if self.candidate_list.count():
            self.candidate_list.setCurrentRow(selected_row)
        self.candidate_list.blockSignals(previous_signals)

    def _candidate_frequency(self, item: QtWidgets.QListWidgetItem | None) -> float | None:
        if item is None:
            return None
        data = item.data(QtCore.Qt.UserRole)
        if data is not None:
            try:
                return float(data)
            except (TypeError, ValueError):
                pass
        numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", item.text())
        if not numbers:
            return None
        return float(numbers[-1])

    @staticmethod
    def _table_bool(table: QtWidgets.QTableWidget, row: int, column: int, default: bool) -> bool:
        item = table.item(row, column)
        if item is None:
            return default
        if item.flags() & QtCore.Qt.ItemIsUserCheckable:
            return item.checkState() == QtCore.Qt.Checked
        text = item.text().strip().lower()
        if not text:
            return default
        return text not in {"0", "false", "no", "n", "off"}

    @staticmethod
    def _csv_bool(value: object, default: bool) -> bool:
        if value is None:
            return default
        text = str(value).strip().lower()
        if not text:
            return default
        return text not in {"0", "false", "no", "n", "off"}

    @staticmethod
    def _table_text(table: QtWidgets.QTableWidget, row: int, column: int, default: str) -> str:
        item = table.item(row, column)
        text = item.text().strip() if item is not None else ""
        return text or default


def apply_plot_theme(plot: pg.PlotWidget, theme: dict[str, object]) -> None:
    if not theme:
        return
    plot.setBackground(str(theme.get("plot_bg", "#ffffff")))
    plot.showGrid(x=True, y=True, alpha=float(theme.get("grid_alpha", 0.20)))
    for axis_name in ("left", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(str(theme.get("axis", "#172033"))))
        axis.setTextPen(pg.mkPen(str(theme.get("axis", "#172033"))))
    apply_plot_legend_theme(plot, theme)


def finite_xy(
    x_values: np.ndarray,
    y_values: np.ndarray,
    *,
    positive_x: bool = False,
    positive_y: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float).ravel()
    y = np.asarray(y_values, dtype=float).ravel()
    count = min(x.size, y.size)
    x = x[:count]
    y = y[:count]
    mask = np.isfinite(x) & np.isfinite(y)
    if positive_x:
        mask &= x > 0.0
    if positive_y:
        mask &= y > 0.0
    return x[mask], y[mask]


def concat_finite(arrays: list[np.ndarray], *, positive_only: bool = False) -> np.ndarray:
    cleaned: list[np.ndarray] = []
    for values in arrays:
        arr = np.asarray(values, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if positive_only:
            arr = arr[arr > 0.0]
        if arr.size:
            cleaned.append(arr)
    if not cleaned:
        return np.array([], dtype=float)
    return np.concatenate(cleaned)


def safe_extent(values: np.ndarray, *, log_enabled: bool = False) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if log_enabled:
        arr = arr[arr > 0.0]
    if arr.size == 0:
        return (1e-12, 1.0) if log_enabled else (-1.0, 1.0)
    minimum = float(np.min(arr))
    maximum = float(np.max(arr))
    if maximum > minimum:
        return minimum, maximum
    if log_enabled:
        factor = 10.0 ** 0.05
        minimum = max(minimum, 1e-300)
        return minimum / factor, minimum * factor
    center = minimum
    span = max(abs(center), 1.0) * 0.05
    return center - span, center + span


def ranges_close(
    left: tuple[tuple[float, float], tuple[float, float]],
    right: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    return bool(np.allclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float)))


def moving_average(values: np.ndarray, width: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 3 or width <= 1:
        return arr
    width = min(int(width), arr.size)
    kernel = np.ones(width, dtype=float) / float(width)
    return np.convolve(arr, kernel, mode="same")


def local_peak_frequencies(freq: np.ndarray, values: np.ndarray, *, max_count: int) -> list[float]:
    f = np.asarray(freq, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(f.size, y.size)
    f = f[:count]
    y = y[:count]
    mask = np.isfinite(f) & np.isfinite(y) & (f > 0.0)
    f = f[mask]
    y = y[mask]
    if f.size < 3:
        return []
    candidates = np.where((y[1:-1] >= y[:-2]) & (y[1:-1] >= y[2:]))[0] + 1
    if candidates.size == 0:
        candidates = np.array([int(np.argmax(y))])
    order = candidates[np.argsort(y[candidates])[::-1]]
    peaks = sorted(float(f[index]) for index in order[:max_count])
    return peaks


def find_prominent_modal_peaks(freq: np.ndarray, db_curve: np.ndarray, *, max_count: int) -> tuple[list[float], np.ndarray]:
    f = np.asarray(freq, dtype=float).ravel()
    db = np.asarray(db_curve, dtype=float).ravel()
    count = min(f.size, db.size)
    f = f[:count]
    db = db[:count]
    valid = np.isfinite(f) & np.isfinite(db) & (f > 0.0)
    f = f[valid]
    db = db[valid]
    if f.size < 8:
        return [], moving_average(db, min(5, max(1, db.size)))

    log_freq = np.log10(f)
    log_grid = np.linspace(float(log_freq[0]), float(log_freq[-1]), max(1200, f.size))
    db_grid = np.interp(log_grid, log_freq, db)
    smooth_short = moving_average(db_grid, 9)
    smooth_long = moving_average(db_grid, 71)
    prominence = smooth_short - smooth_long
    smooth_db = np.interp(log_freq, log_grid, smooth_short)

    log_range = float(log_grid[-1] - log_grid[0])
    min_spacing = max(0.018, 0.05 * log_range / max(1, int(max_count)))
    local_window = max(9, int(round(log_grid.size / 80.0)))
    if local_window % 2 == 0:
        local_window += 1
    local_half = local_window // 2
    global_prom = float(np.nanmax(prominence) - np.nanmin(prominence))
    if not np.isfinite(global_prom) or global_prom <= 0.0:
        global_prom = 1.0
    base_threshold = max(1.5, 0.10 * global_prom)

    candidates: list[int] = []
    scores: list[float] = []
    for index in range(1, log_grid.size - 1):
        if smooth_short[index] < smooth_short[index - 1] or smooth_short[index] < smooth_short[index + 1]:
            continue
        left = max(0, index - local_half)
        right = min(log_grid.size, index + local_half + 1)
        local_prom = float(prominence[index])
        local_floor = float(np.nanmin(prominence[left:right]))
        local_peak = float(np.nanmax(prominence[left:right]))
        local_threshold = max(base_threshold, 0.35 * (local_peak - local_floor))
        if local_prom < local_threshold:
            continue
        candidates.append(index)
        scores.append(local_prom)

    if not candidates:
        return [], smooth_db

    selected: list[int] = []
    for order_index in np.argsort(np.asarray(scores))[::-1]:
        index = candidates[int(order_index)]
        if not selected or all(abs(float(log_grid[index] - log_grid[old])) >= min_spacing for old in selected):
            selected.append(index)
        if len(selected) >= max_count:
            break
    peak_freqs = sorted(float(10.0 ** log_grid[index]) for index in selected)
    peak_freqs = refine_modal_peak_frequencies(f, db, peak_freqs)
    low_band = np.where(f <= np.nanmin(f) * 8.0)[0]
    if low_band.size:
        peak_freqs = ensure_low_frequency_peak(peak_freqs, f, smooth_db, low_band)
    return refine_modal_peak_frequencies(f, db, peak_freqs), smooth_db


def ensure_low_frequency_peak(peak_freqs: list[float], freq: np.ndarray, smooth_db: np.ndarray, low_band: np.ndarray) -> list[float]:
    best_index: int | None = None
    best_value = -float("inf")
    for index in low_band[1:-1]:
        idx = int(index)
        if smooth_db[idx] >= smooth_db[idx - 1] and smooth_db[idx] >= smooth_db[idx + 1] and smooth_db[idx] > best_value:
            best_index = idx
            best_value = float(smooth_db[idx])
    if best_index is None:
        return peak_freqs
    low_freq = float(freq[best_index])
    if not peak_freqs or all(abs(math.log10(value) - math.log10(low_freq)) > 0.03 for value in peak_freqs if value > 0.0):
        return sorted(peak_freqs + [low_freq])
    return peak_freqs


def refine_modal_peak_frequencies(freq: np.ndarray, db_curve: np.ndarray, peak_freqs: list[float]) -> list[float]:
    if not peak_freqs:
        return []
    f = np.asarray(freq, dtype=float).ravel()
    db = np.asarray(db_curve, dtype=float).ravel()
    count = min(f.size, db.size)
    f = f[:count]
    db = db[:count]
    valid = np.isfinite(f) & np.isfinite(db) & (f > 0.0)
    f = f[valid]
    db = db[valid]
    if f.size < 3:
        return sorted({float(value) for value in peak_freqs if np.isfinite(value) and value > 0.0})
    log_freq = np.log10(f)
    d_log = np.diff(log_freq)
    d_log = d_log[np.isfinite(d_log) & (d_log > 0.0)]
    search_half_width = 0.025 if d_log.size == 0 else max(0.015, 6.0 * float(np.nanmedian(d_log)))

    refined: list[float] = []
    for peak in peak_freqs:
        if not np.isfinite(peak) or peak <= 0.0:
            continue
        log_peak = math.log10(float(peak))
        in_window = np.where(np.abs(log_freq - log_peak) <= search_half_width)[0]
        if in_window.size == 0:
            nearest = int(np.nanargmin(np.abs(f - peak)))
            in_window = np.arange(max(0, nearest - 3), min(f.size, nearest + 4))
        best = int(in_window[int(np.nanargmax(db[in_window]))])
        left = best
        while left > int(in_window[0]) and db[left - 1] <= db[left]:
            left -= 1
            if left <= 0:
                break
        right = best
        while right < int(in_window[-1]) and db[right + 1] <= db[right]:
            right += 1
            if right >= f.size - 1:
                break
        local = np.arange(left, right + 1)
        best = int(local[int(np.nanargmax(db[local]))])
        refined.append(float(f[best]))

    refined = sorted({value for value in refined if np.isfinite(value) and value > 0.0})
    if not refined:
        return []
    keep = [True] * len(refined)
    tol_log = max(0.006, 2.0 * search_half_width / 3.0)
    for index in range(1, len(refined)):
        if abs(math.log10(refined[index]) - math.log10(refined[index - 1])) < tol_log:
            current = float(np.interp(refined[index], f, db, left=-np.inf, right=-np.inf))
            previous = float(np.interp(refined[index - 1], f, db, left=-np.inf, right=-np.inf))
            if current <= previous:
                keep[index] = False
            else:
                keep[index - 1] = False
    return [value for value, use in zip(refined, keep) if use]


def safe_mode_scale(coords: np.ndarray, disp: np.ndarray) -> float:
    coord_span = np.nanmax(coords, axis=0) - np.nanmin(coords, axis=0) if coords.size else np.array([1.0])
    max_span = float(np.nanmax(np.abs(coord_span))) if coord_span.size else 1.0
    max_disp = float(np.nanmax(np.abs(disp))) if disp.size else 0.0
    if not np.isfinite(max_span) or max_span <= 0.0:
        max_span = 1.0
    if not np.isfinite(max_disp) or max_disp <= 1e-20:
        return 1.0
    return 0.18 * max_span / max_disp


def sparse_label_indices(count: int, *, label_limit: int) -> list[int]:
    count = max(0, int(count))
    label_limit = max(0, int(label_limit))
    if count == 0 or label_limit == 0:
        return []
    if count <= label_limit:
        return list(range(count))
    indices = np.linspace(0, count - 1, label_limit, dtype=int)
    return sorted({int(index) for index in indices})


def frequencies_match(left: float, right: float) -> bool:
    if not np.isfinite(left) or not np.isfinite(right):
        return False
    return abs(float(left) - float(right)) <= max(1e-9, 1e-6 * max(1.0, abs(float(left)), abs(float(right))))


def merge_frequency_candidates(values: list[float], *, max_count: int) -> list[float]:
    merged: list[float] = []
    for value in values:
        freq = float(value)
        if not np.isfinite(freq) or freq <= 0.0:
            continue
        if any(abs(math.log10(freq) - math.log10(existing)) <= 0.015 for existing in merged if existing > 0.0):
            continue
        merged.append(freq)
        if len(merged) >= max_count:
            break
    return sorted(merged)


def remove_matching_frequency(values: list[float], target: float) -> list[float]:
    return [float(value) for value in values if not frequencies_match(float(value), float(target))]


def first_nonzero_complex(values: np.ndarray, scale_signs: np.ndarray | None = None) -> complex | None:
    arr = np.asarray(values, dtype=complex).ravel()
    signs = np.asarray(scale_signs, dtype=float).ravel() if scale_signs is not None else np.ones(arr.shape, dtype=float)
    if signs.size != arr.size:
        signs = np.ones(arr.shape, dtype=float)
    for value, sign in zip(arr, signs):
        if np.isfinite(np.real(value)) and np.isfinite(np.imag(value)) and abs(value) > 0.0:
            sign_value = -1.0 if np.isfinite(sign) and sign < 0.0 else 1.0
            return complex(value) / sign_value
    return None


def minimum_spanning_edges(coords: np.ndarray) -> list[tuple[int, int]]:
    points = np.asarray(coords, dtype=float)
    count = points.shape[0] if points.ndim == 2 else 0
    if count < 2:
        return []
    visited = {0}
    edges: list[tuple[int, int]] = []
    while len(visited) < count:
        best: tuple[float, int, int] | None = None
        for left in visited:
            for right in range(count):
                if right in visited or right == left:
                    continue
                distance = float(np.linalg.norm(points[left] - points[right]))
                if not np.isfinite(distance):
                    continue
                if best is None or distance < best[0]:
                    best = (distance, left, right)
        if best is None:
            break
        _distance, left, right = best
        visited.add(right)
        edges.append((left, right))
    return edges


def infer_modal_auto_edges(coords: np.ndarray) -> list[tuple[int, int]]:
    points = np.asarray(coords, dtype=float)
    count = points.shape[0] if points.ndim == 2 else 0
    if count < 2:
        return []
    axis_edges = infer_axis_aligned_edges(points)
    if not axis_edges:
        return fallback_nearest_auto_edges(points)
    if component_count(axis_edges, count) > 1:
        return connect_components_with_fallback(axis_edges, fallback_nearest_auto_edges(points), count)
    return sorted(set(tuple(sorted(edge)) for edge in axis_edges))


def infer_axis_aligned_edges(coords: np.ndarray) -> list[tuple[int, int]]:
    points = np.asarray(coords, dtype=float)
    count = points.shape[0] if points.ndim == 2 else 0
    if count < 2:
        return []
    axis_tol = estimate_axis_alignment_tolerance(points)
    edges: set[tuple[int, int]] = set()
    for index in range(count):
        for axis in range(3):
            for direction in (-1, 1):
                neighbor = nearest_aligned_neighbor(points, index, axis, direction, axis_tol)
                if neighbor is not None:
                    edges.add(tuple(sorted((index, neighbor))))
    return sorted(edges)


def estimate_axis_alignment_tolerance(coords: np.ndarray) -> np.ndarray:
    tolerances = np.zeros(3, dtype=float)
    for axis in range(3):
        values = np.sort(np.asarray(coords[:, axis], dtype=float))
        axis_range = float(np.nanmax(values) - np.nanmin(values)) if values.size else 0.0
        noise_floor = max(1e-6, 1e-3 * max(axis_range, 1.0))
        diffs = np.diff(values)
        diffs = diffs[np.isfinite(diffs) & (diffs > noise_floor)]
        spacing = max(axis_range, 1.0) if diffs.size == 0 else float(np.nanmedian(diffs))
        tolerances[axis] = max(5.0 * noise_floor, 0.20 * spacing)
    return tolerances


def nearest_aligned_neighbor(
    coords: np.ndarray,
    base_index: int,
    axis: int,
    direction: int,
    axis_tol: np.ndarray,
) -> int | None:
    delta = coords - coords[base_index, :]
    primary = delta[:, axis]
    other_axes = [idx for idx in range(3) if idx != axis]
    mask = np.ones(coords.shape[0], dtype=bool)
    mask[base_index] = False
    mask &= np.isfinite(primary)
    mask &= np.abs(delta[:, other_axes[0]]) <= axis_tol[other_axes[0]]
    mask &= np.abs(delta[:, other_axes[1]]) <= axis_tol[other_axes[1]]
    if direction > 0:
        mask &= primary > max(axis_tol[axis], 1e-9)
    else:
        mask &= primary < -max(axis_tol[axis], 1e-9)
    candidates = np.where(mask)[0]
    if candidates.size == 0:
        return None
    return int(candidates[int(np.argmin(np.abs(primary[candidates])))])


def fallback_nearest_auto_edges(coords: np.ndarray) -> list[tuple[int, int]]:
    points = np.asarray(coords, dtype=float)
    edges = minimum_spanning_edges(points)
    if not edges:
        return []
    dist = pairwise_distances(points)
    nearest = min_positive_rows(dist)
    nearest = nearest[np.isfinite(nearest)]
    dist_limit = float("inf") if nearest.size == 0 else 1.8 * float(np.nanmedian(nearest))
    base_edges: set[tuple[int, int]] = {tuple(sorted(edge)) for edge in edges}
    candidates: set[tuple[int, int]] = set()
    for index in range(points.shape[0]):
        for other in np.argsort(dist[index]):
            if int(other) == index or not np.isfinite(dist[index, other]):
                continue
            if dist[index, other] > dist_limit:
                break
            pair = tuple(sorted((index, int(other))))
            if pair in base_edges or pair in candidates:
                continue
            candidates.add(pair)
            break
    return sorted(base_edges | candidates)


def min_positive_rows(values: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    result = np.full(matrix.shape[0], np.inf, dtype=float)
    for index in range(matrix.shape[0]):
        row = matrix[index]
        positive = row[np.isfinite(row) & (row > 0.0)]
        if positive.size:
            result[index] = float(np.nanmin(positive))
    return result


def pairwise_distances(coords: np.ndarray) -> np.ndarray:
    points = np.asarray(coords, dtype=float)
    count = points.shape[0] if points.ndim == 2 else 0
    dist = np.full((count, count), np.inf, dtype=float)
    for left in range(count):
        for right in range(left + 1, count):
            value = float(np.linalg.norm(points[left] - points[right]))
            dist[left, right] = value
            dist[right, left] = value
    return dist


def component_count(edges: list[tuple[int, int]], node_count: int) -> int:
    labels = component_labels(edges, node_count)
    return max(labels) if labels else 0


def connect_components_with_fallback(
    edges: list[tuple[int, int]],
    fallback_edges: list[tuple[int, int]],
    node_count: int,
) -> list[tuple[int, int]]:
    output = [tuple(sorted(edge)) for edge in edges]
    labels = component_labels(output, node_count)
    if labels and max(labels) <= 1:
        return sorted(set(output))
    for left, right in fallback_edges:
        if labels and labels[left] == labels[right]:
            continue
        output.append(tuple(sorted((left, right))))
        labels = component_labels(output, node_count)
        if labels and max(labels) <= 1:
            break
    return sorted(set(output))


def component_labels(edges: list[tuple[int, int]], node_count: int) -> list[int]:
    adjacency = [[] for _ in range(node_count)]
    for left, right in edges:
        if 0 <= left < node_count and 0 <= right < node_count:
            adjacency[left].append(right)
            adjacency[right].append(left)
    labels = [0] * node_count
    component = 0
    for start in range(node_count):
        if labels[start] != 0:
            continue
        component += 1
        labels[start] = component
        queue = [start]
        cursor = 0
        while cursor < len(queue):
            node = queue[cursor]
            cursor += 1
            for neighbor in adjacency[node]:
                if labels[neighbor] == 0:
                    labels[neighbor] = component
                    queue.append(neighbor)
    return labels


def project_points_3d(coords: np.ndarray, *, azimuth_deg: float = 35.0, elevation_deg: float = 24.0) -> np.ndarray:
    points = np.asarray(coords, dtype=float)
    if points.ndim != 2 or points.shape[1] < 3:
        return np.zeros((0, 2), dtype=float)
    azimuth = math.radians(float(azimuth_deg))
    elevation = math.radians(float(elevation_deg))
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    # Match GLViewWidget's Euler camera transform: rotate elevation - 90
    # around +X after rotating azimuth + 90 around -Z.
    x_view = -x * math.sin(azimuth) + y * math.cos(azimuth)
    y_view = (
        -x * math.cos(azimuth) * math.sin(elevation)
        - y * math.sin(azimuth) * math.sin(elevation)
        + z * math.cos(elevation)
    )
    return np.column_stack([x_view, y_view])


def write_mode_animation_gif(
    path: str | Path,
    mode: dict[str, object],
    *,
    frame_count: int = 24,
    delay_cs: int = 8,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 24.0,
) -> Path:
    destination = Path(path)
    frames = render_mode_animation_frames(
        mode,
        frame_count=frame_count,
        width=900,
        height=650,
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
    )
    if not frames:
        raise ValueError("没有可写入 GIF 的模态帧。")
    write_indexed_gif(destination, frames, _MODE_GIF_PALETTE, delay_cs=delay_cs)
    return destination


def render_mode_animation_frames(
    mode: dict[str, object],
    *,
    frame_count: int,
    width: int,
    height: int,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 24.0,
) -> list[np.ndarray]:
    coords = np.asarray(mode.get("coords"), dtype=float)
    disp_complex = np.asarray(mode.get("disp_complex", mode.get("displacements")), dtype=complex)
    if coords.ndim != 2 or coords.shape[0] == 0 or disp_complex.shape[0] != coords.shape[0]:
        return []
    scale = float(mode.get("scale", 1.0))
    labels = [str(label) for label in mode.get("labels", [])]
    if len(labels) != coords.shape[0]:
        labels = [f"P{i + 1}" for i in range(coords.shape[0])]
    edges = mode_line_edges(mode, labels)
    projected_samples = [project_points_3d(coords, azimuth_deg=azimuth_deg, elevation_deg=elevation_deg)]
    for index in range(frame_count):
        phase = 2.0 * math.pi * index / max(frame_count, 1)
        projected_samples.append(
            project_points_3d(
                coords + np.real(disp_complex * np.exp(1j * phase)) * scale,
                azimuth_deg=azimuth_deg,
                elevation_deg=elevation_deg,
            )
        )
    bounds = projected_bounds(projected_samples)
    frames: list[np.ndarray] = []
    for index in range(frame_count):
        phase = 2.0 * math.pi * index / max(frame_count, 1)
        deformed = coords + np.real(disp_complex * np.exp(1j * phase)) * scale
        def_xy = map_projected_to_pixels(
            project_points_3d(deformed, azimuth_deg=azimuth_deg, elevation_deg=elevation_deg),
            bounds,
            width,
            height,
        )
        frame = np.zeros((height, width), dtype=np.uint8)
        for left, right in edges:
            draw_indexed_line(frame, def_xy[left], def_xy[right], 3)
        for point in def_xy:
            draw_indexed_circle(frame, point, radius=5, color=4)
        frames.append(frame)
    return frames


def read_xlsx_rows_basic(path: str | Path, preferred_sheet: str = "PointMap") -> list[list[object]]:
    source = Path(path)
    with zipfile.ZipFile(source) as archive:
        shared_strings = _read_xlsx_shared_strings(archive)
        sheet_path = _select_xlsx_sheet_path(archive, preferred_sheet)
        root = ET.fromstring(archive.read(sheet_path))
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[object]] = []
    for row_node in root.findall(".//main:sheetData/main:row", ns):
        row_values: list[object] = []
        for cell in row_node.findall("main:c", ns):
            ref = str(cell.attrib.get("r", ""))
            column_index = _xlsx_column_index(ref)
            while len(row_values) <= column_index:
                row_values.append("")
            row_values[column_index] = _xlsx_cell_value(cell, shared_strings)
        while row_values and str(row_values[-1]).strip() == "":
            row_values.pop()
        if row_values:
            rows.append(row_values)
    return rows


def write_xlsx_rows_basic(path: str | Path, rows: list[list[object]], sheet_name: str = "PointMap") -> Path:
    destination = Path(path)
    sheet_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row, start=1):
            text = _escape_xml_text(str(value if value is not None else ""))
            cells.append(
                f'<c r="{_xlsx_cell_ref(row_index, column_index)}" t="inlineStr"><is><t>{text}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{_escape_xml_text(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>""",
        )
    return destination


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: list[str] = []
    for item in root.findall("main:si", ns):
        parts = [node.text or "" for node in item.findall(".//main:t", ns)]
        strings.append("".join(parts))
    return strings


def _select_xlsx_sheet_path(archive: zipfile.ZipFile, preferred_sheet: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    main_ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
    targets: dict[str, str] = {}
    for rel in rels.findall("rel:Relationship", rel_ns):
        rel_id = str(rel.attrib.get("Id", ""))
        target = str(rel.attrib.get("Target", ""))
        if target:
            clean_target = target.lstrip("/")
            targets[rel_id] = clean_target if clean_target.startswith("xl/") else "xl/" + clean_target
    selected_rel_id = ""
    first_rel_id = ""
    for sheet in workbook.findall(".//main:sheets/main:sheet", main_ns):
        rel_id = str(sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""))
        if not first_rel_id:
            first_rel_id = rel_id
        if str(sheet.attrib.get("name", "")).strip().lower() == preferred_sheet.lower():
            selected_rel_id = rel_id
            break
    sheet_path = targets.get(selected_rel_id or first_rel_id)
    if not sheet_path:
        raise ValueError("Cannot find a worksheet in the .xlsx file.")
    return sheet_path


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> object:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text_node = cell.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        return text_node.text if text_node is not None and text_node.text is not None else ""
    value_node = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
    text = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(text)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "1" if text == "1" else "0"
    if text == "":
        return ""
    try:
        value = float(text)
    except ValueError:
        return text
    if value.is_integer():
        return int(value)
    return value


def _xlsx_column_index(cell_ref: str) -> int:
    letters = re.match(r"^([A-Za-z]+)", cell_ref or "")
    if not letters:
        return 0
    index = 0
    for char in letters.group(1).upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(index - 1, 0)


def _xlsx_cell_ref(row: int, column: int) -> str:
    letters = ""
    value = int(column)
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{int(row)}"


def _escape_xml_text(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def mode_line_edges(mode: dict[str, object], labels: list[str]) -> list[tuple[int, int]]:
    point_index = {label: index for index, label in enumerate(labels)}
    edges: list[tuple[int, int]] = []
    for row in mode.get("lines", []):
        if not isinstance(row, dict):
            continue
        left = point_index.get(str(row.get("start", "")))
        right = point_index.get(str(row.get("end", "")))
        if left is None or right is None or left == right:
            continue
        edge = tuple(sorted((left, right)))
        if edge not in edges:
            edges.append(edge)
    if not edges and len(labels) >= 2:
        edges = [(index, index + 1) for index in range(len(labels) - 1)]
    return edges


def projected_bounds(samples: list[np.ndarray]) -> tuple[float, float, float, float]:
    valid = [sample for sample in samples if sample.size]
    if not valid:
        return -1.0, 1.0, -1.0, 1.0
    stacked = np.vstack(valid)
    x_min = float(np.nanmin(stacked[:, 0]))
    x_max = float(np.nanmax(stacked[:, 0]))
    y_min = float(np.nanmin(stacked[:, 1]))
    y_max = float(np.nanmax(stacked[:, 1]))
    if not np.isfinite(x_min) or not np.isfinite(x_max) or x_min == x_max:
        x_min, x_max = -1.0, 1.0
    if not np.isfinite(y_min) or not np.isfinite(y_max) or y_min == y_max:
        y_min, y_max = -1.0, 1.0
    pad_x = 0.12 * max(x_max - x_min, 1e-9)
    pad_y = 0.12 * max(y_max - y_min, 1e-9)
    return x_min - pad_x, x_max + pad_x, y_min - pad_y, y_max + pad_y


def map_projected_to_pixels(
    points: np.ndarray,
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    *,
    margin: int = 38,
) -> np.ndarray:
    x_min, x_max, y_min, y_max = bounds
    span_x = max(x_max - x_min, 1e-9)
    span_y = max(y_max - y_min, 1e-9)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    draw_w = span_x * scale
    draw_h = span_y * scale
    offset_x = (width - draw_w) / 2.0 - x_min * scale
    offset_y = (height - draw_h) / 2.0 - y_min * scale
    x = points[:, 0] * scale + offset_x
    y = height - (points[:, 1] * scale + offset_y)
    return np.column_stack([x, y]).astype(int)


def draw_indexed_line(frame: np.ndarray, left: np.ndarray, right: np.ndarray, color: int) -> None:
    x0, y0 = int(left[0]), int(left[1])
    x1, y1 = int(right[0]), int(right[1])
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= y0 < frame.shape[0] and 0 <= x0 < frame.shape[1]:
            frame[y0, x0] = color
        if x0 == x1 and y0 == y1:
            break
        twice_error = 2 * error
        if twice_error >= dy:
            error += dy
            x0 += sx
        if twice_error <= dx:
            error += dx
            y0 += sy


def draw_indexed_circle(frame: np.ndarray, center: np.ndarray, *, radius: int, color: int) -> None:
    cx, cy = int(center[0]), int(center[1])
    y_min = max(0, cy - radius)
    y_max = min(frame.shape[0] - 1, cy + radius)
    x_min = max(0, cx - radius)
    x_max = min(frame.shape[1] - 1, cx + radius)
    rr = radius * radius
    for y in range(y_min, y_max + 1):
        for x in range(x_min, x_max + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= rr:
                frame[y, x] = color


def write_indexed_gif(path: Path, frames: list[np.ndarray], palette: list[tuple[int, int, int]], *, delay_cs: int) -> None:
    height, width = frames[0].shape
    palette_bytes = bytearray()
    for red, green, blue in palette[:256]:
        palette_bytes.extend([red & 0xFF, green & 0xFF, blue & 0xFF])
    while len(palette_bytes) < 256 * 3:
        palette_bytes.extend([0, 0, 0])
    with path.open("wb") as handle:
        handle.write(b"GIF89a")
        handle.write(int(width).to_bytes(2, "little"))
        handle.write(int(height).to_bytes(2, "little"))
        handle.write(bytes([0xF7, 0x00, 0x00]))
        handle.write(bytes(palette_bytes))
        handle.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")
        for frame in frames:
            if frame.shape != (height, width):
                raise ValueError("所有 GIF 帧尺寸必须一致。")
            handle.write(b"\x21\xF9\x04\x00")
            handle.write(int(delay_cs).to_bytes(2, "little"))
            handle.write(b"\x00\x00")
            handle.write(b"\x2C\x00\x00\x00\x00")
            handle.write(int(width).to_bytes(2, "little"))
            handle.write(int(height).to_bytes(2, "little"))
            handle.write(b"\x00")
            payload = gif_lzw_encode(bytes(np.asarray(frame, dtype=np.uint8).ravel()), min_code_size=8)
            handle.write(b"\x08")
            for start in range(0, len(payload), 255):
                block = payload[start : start + 255]
                handle.write(bytes([len(block)]))
                handle.write(block)
            handle.write(b"\x00")
        handle.write(b"\x3B")


def gif_lzw_encode(data: bytes, *, min_code_size: int) -> bytes:
    clear_code = 1 << min_code_size
    end_code = clear_code + 1
    dictionary: dict[bytes, int] = {}
    next_code = 0
    code_size = min_code_size + 1
    bit_buffer = 0
    bit_count = 0
    output = bytearray()

    def reset_dictionary() -> None:
        nonlocal dictionary, next_code, code_size
        dictionary = {bytes([index]): index for index in range(clear_code)}
        next_code = end_code + 1
        code_size = min_code_size + 1

    def write_code(code: int) -> None:
        nonlocal bit_buffer, bit_count
        bit_buffer |= int(code) << bit_count
        bit_count += code_size
        while bit_count >= 8:
            output.append(bit_buffer & 0xFF)
            bit_buffer >>= 8
            bit_count -= 8

    reset_dictionary()
    write_code(clear_code)
    if not data:
        write_code(end_code)
    else:
        current = bytes([data[0]])
        for value in data[1:]:
            char = bytes([value])
            combined = current + char
            if combined in dictionary:
                current = combined
                continue
            write_code(dictionary[current])
            if next_code < 4096:
                dictionary[combined] = next_code
                next_code += 1
                # The decoder adds this entry after reading the next emitted
                # code, so the encoder must change width one code later.
                if next_code > (1 << code_size) and code_size < 12:
                    code_size += 1
            else:
                write_code(clear_code)
                reset_dictionary()
            current = char
        write_code(dictionary[current])
        write_code(end_code)
    if bit_count:
        output.append(bit_buffer & 0xFF)
    return bytes(output)


_MODE_GIF_PALETTE = [
    (255, 255, 255),
    (156, 163, 175),
    (120, 130, 145),
    (31, 119, 180),
    (215, 38, 61),
    (16, 32, 51),
    (237, 243, 250),
    (29, 114, 201),
]
