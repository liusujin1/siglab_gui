from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import csv
import math
import re

import numpy as np

from python_vna.analysis_algorithms import compute_welch_psd
from python_vna.analysis_data import AnalysisDataset, load_analysis_path
from python_vna.diagnostic.data import (
    CurvePair,
    TraceAnalysisFile,
    VibrationAnalysisFile,
    curve_pairs_from_table,
    load_trace_analysis_file,
    load_vibration_analysis_file,
)
from python_vna.optional import require
from python_vna.ui.legend_placement import place_legend_away_from_curves

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
        trajectory = self._mode_trajectory(base, disp_complex)
        point_clouds = [base[:, :3]]
        if target is not None:
            point_clouds.append(target[:, :3])
        point_clouds.extend(trajectory)
        all_points = np.vstack(point_clouds)
        finite_points = all_points[np.all(np.isfinite(all_points), axis=1)]
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
        trajectory_draw = [sample[:, :3] - center[:3] for sample in trajectory]
        span = float(np.nanmax(maxs - mins)) if finite_points.size else 1.0
        if not np.isfinite(span) or span <= 0.0:
            span = 1.0

        self._add_axes(span)
        self._add_bounding_box(mins - center, maxs - center)

        grid = gl.GLGridItem()
        grid.setSize(x=span * 1.5, y=span * 1.5, z=0.0)
        grid.setSpacing(x=max(span / 6.0, 0.1), y=max(span / 6.0, 0.1), z=1.0)
        grid.translate(0.0, 0.0, float(np.nanmin(all_points[:, 2] - center[2])) if all_points.size else 0.0)
        self.add_render_item(grid)

        if target_draw is not None and trajectory_draw:
            self._add_trajectory_paths(trajectory_draw)

        edges = mode_line_edges({"lines": line_rows}, labels)
        for left, right in edges:
            self.add_render_item(
                gl.GLLinePlotItem(
                    pos=np.vstack([base_draw[left], base_draw[right]]),
                    color=(0.55, 0.59, 0.65, 1.0),
                    width=1.4,
                    antialias=True,
                    mode="lines",
                )
            )
            if target_draw is not None:
                self.add_render_item(
                    gl.GLLinePlotItem(
                        pos=np.vstack([target_draw[left], target_draw[right]]),
                        color=(0.84, 0.15, 0.24, 1.0),
                        width=3.0,
                        antialias=True,
                        mode="lines",
                    )
                )

        if target_draw is not None:
            self._add_displacement_vectors(base_draw, target_draw, span)

        self.add_render_item(
            gl.GLScatterPlotItem(
                pos=base_draw,
                color=(0.48, 0.54, 0.61, 1.0),
                size=9.0,
                pxMode=True,
            )
        )
        if target_draw is not None:
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

    @staticmethod
    def _mode_trajectory(base: np.ndarray, disp_complex: np.ndarray | None) -> list[np.ndarray]:
        if disp_complex is None:
            return []
        disp = np.asarray(disp_complex, dtype=complex)
        if disp.ndim != 2 or disp.shape[0] != base.shape[0] or disp.shape[1] < 3:
            return []
        phases = np.linspace(0.0, 2.0 * math.pi, 36, endpoint=False)
        return [base[:, :3] + np.real(disp[:, :3] * np.exp(1j * phase)) for phase in phases]

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

    def _add_trajectory_paths(self, trajectory: list[np.ndarray]) -> None:
        if not trajectory:
            return
        count = min(trajectory[0].shape[0], 80)
        for point_index in range(count):
            path = np.asarray([sample[point_index] for sample in trajectory], dtype=float)
            if path.shape[0] < 2 or not np.all(np.isfinite(path)):
                continue
            path = np.vstack([path, path[0]])
            self.add_render_item(
                gl.GLLinePlotItem(
                    pos=path,
                    color=(0.95, 0.55, 0.12, 0.68),
                    width=1.4,
                    antialias=True,
                    mode="line_strip",
                )
            )

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

    def _add_point_labels(self, labels: list[str], points: np.ndarray, span: float, *, label_limit: int = 12) -> None:
        if points.size == 0:
            return
        offset = np.array([0.025, 0.025, 0.035], dtype=float) * max(span, 1.0)
        selected = sparse_label_indices(len(labels), label_limit=label_limit)
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


TRACE_COLORS = [
    "#1f77b4",
    "#e4572e",
    "#2e8b57",
    "#f2a900",
    "#7b61ff",
    "#00a6a6",
    "#d7263d",
    "#5c677d",
]


def configure_control_panel(widget: QtWidgets.QWidget) -> None:
    widget.setObjectName("diagnosticControlPanel")


def set_button_role(button: QtWidgets.QPushButton, role: str) -> None:
    button.setProperty("role", role)


def create_group_box(title: str, *, layout_type: type[QtWidgets.QLayout] = QtWidgets.QGridLayout) -> tuple[QtWidgets.QGroupBox, QtWidgets.QLayout]:
    group = QtWidgets.QGroupBox(title)
    layout = layout_type(group)
    layout.setContentsMargins(8, 14, 8, 8)
    if isinstance(layout, QtWidgets.QGridLayout):
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
    else:
        layout.setSpacing(6)
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
        self._plot_curves: dict[pg.PlotWidget, dict[str, tuple[np.ndarray, np.ndarray]]] = {}

    def apply_theme(self, theme: dict[str, object]) -> None:
        for plot in self.findChildren(pg.PlotWidget):
            apply_plot_theme(plot, theme)

    def _show_status(self, text: str) -> None:
        self.statusChanged.emit(str(text))

    def _remember_paths(self, paths: list[Path]) -> None:
        if paths:
            self._last_directory = paths[-1].parent

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
    ) -> int:
        plot.clear()
        legend = plot.addLegend(offset=(3, 2))
        if legend is not None:
            legend.clear()
        plot.setTitle(title)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", y_label)
        plot.setLogMode(x=log_x, y=log_y)
        saved: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        plotted = 0
        for index, curve in enumerate(curves):
            x, y = finite_xy(curve.x, curve.y, positive_x=log_x, positive_y=log_y)
            if x.size == 0:
                continue
            color = TRACE_COLORS[index % len(TRACE_COLORS)]
            plot.plot(x, y, pen=pg.mkPen(color, width=1.5), name=curve.label)
            saved[curve.label] = (x, y)
            plotted += 1
        self._plot_curves[plot] = saved
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.enableAutoRange()
        if saved:
            place_legend_away_from_curves(plot, saved)
        return plotted

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
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(300)
        controls.setMaximumWidth(360)
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
        self.file_list.setMinimumHeight(180)
        data_layout.addWidget(self.file_list, 1)
        controls_layout.addWidget(data_group)

        settings_group, settings_layout = create_group_box("2. 分析设置")
        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItems(["叠加", "子图"])
        self.frequency_pair_list = QtWidgets.QListWidget()
        self.frequency_pair_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.frequency_pair_list.setAlternatingRowColors(True)
        self.frequency_pair_list.setMinimumHeight(120)
        self.log_group_combo = QtWidgets.QComboBox()
        self.log_channel_list = QtWidgets.QListWidget()
        self.log_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.log_channel_list.setAlternatingRowColors(True)
        self.log_channel_list.setMinimumHeight(110)
        self.demean_check = QtWidgets.QCheckBox("去均值")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")

        settings_layout.addWidget(QtWidgets.QLabel("绘图模式"), 0, 0)
        settings_layout.addWidget(self.plot_mode_combo, 0, 1)
        settings_layout.addWidget(QtWidgets.QLabel("频响曲线"), 1, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.frequency_pair_list, 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("日志分组"), 2, 0)
        settings_layout.addWidget(self.log_group_combo, 2, 1)
        settings_layout.addWidget(QtWidgets.QLabel("日志通道"), 3, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.log_channel_list, 3, 1)
        settings_layout.addWidget(self.demean_check, 4, 0, 1, 2)
        controls_layout.addWidget(settings_group)

        action_group, action_layout = create_group_box("3. 操作", layout_type=QtWidgets.QVBoxLayout)
        action_layout.addWidget(self.plot_button)
        action_layout.addWidget(self.export_button)
        controls_layout.addWidget(action_group)
        controls_layout.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        self.frequency_plot = pg.PlotWidget(title="频率响应")
        self.log_plot = pg.PlotWidget(title="日志 / 传感器")
        self.tabs.addTab(self.frequency_plot, "频响分析")
        self.tabs.addTab(self.log_plot, "日志 / 传感器")

        layout.addWidget(controls)
        layout.addWidget(self.tabs, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.file_list.currentRowChanged.connect(lambda _row: self._refresh_controls())
        self.log_group_combo.currentIndexChanged.connect(lambda _index: self._refresh_log_channels())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)

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
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh_controls()
        self._show_status(f"已加载上位机数据文件：{loaded} 个")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.frequency_pair_list.clear()
        self.log_group_combo.clear()
        self.log_channel_list.clear()
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
        if self.tabs.currentIndex() == 0:
            curves = self._selected_frequency_pairs(current)
            plotted = self._plot_curves_on_widget(
                self.frequency_plot,
                curves,
                title=f"频率响应 - {current.table.name}",
                x_label="频率 (Hz)",
                y_label="幅值",
                log_x=True,
                log_y=False,
            )
        else:
            curves = self._selected_log_curves(current)
            plotted = self._plot_curves_on_widget(
                self.log_plot,
                curves,
                title=f"日志 / 传感器 - {current.table.name}",
                x_label="样本 / 时间",
                y_label="数值",
            )
        self._show_status(f"已绘制上位机数据曲线：{plotted} 条")

    def _refresh_controls(self) -> None:
        current = self.current_file()
        self.frequency_pair_list.clear()
        self.log_group_combo.clear()
        self.log_channel_list.clear()
        if current is None:
            return
        for pair in current.frequency_pairs:
            item = QtWidgets.QListWidgetItem(pair.label)
            item.setSelected(True)
            self.frequency_pair_list.addItem(item)
        for group in current.log_groups:
            self.log_group_combo.addItem(group)
        self._refresh_log_channels()

    def _refresh_log_channels(self) -> None:
        current = self.current_file()
        self.log_channel_list.clear()
        if current is None:
            return
        group = self.log_group_combo.currentText()
        indices = current.log_groups.get(group, [])
        for index in indices:
            item = QtWidgets.QListWidgetItem(current.table.headers[index])
            item.setData(QtCore.Qt.UserRole, index)
            item.setSelected(True)
            self.log_channel_list.addItem(item)

    def _selected_frequency_pairs(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected = {item.text() for item in self.frequency_pair_list.selectedItems()}
        pairs = [pair for pair in current.frequency_pairs if not selected or pair.label in selected]
        return pairs or current.frequency_pairs[:1]

    def _selected_log_curves(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected_indices = [
            int(item.data(QtCore.Qt.UserRole))
            for item in self.log_channel_list.selectedItems()
            if item.data(QtCore.Qt.UserRole) is not None
        ]
        if not selected_indices and self.log_channel_list.count():
            selected_indices = [int(self.log_channel_list.item(0).data(QtCore.Qt.UserRole))]
        x = np.arange(current.table.row_count, dtype=float)
        curves: list[CurvePair] = []
        for index in selected_indices:
            y = np.asarray(current.table.data[:, index], dtype=float)
            if self.demean_check.isChecked():
                y = y - np.nanmean(y)
            curves.append(CurvePair(current.table.headers[index], x, y, "样本", current.table.headers[index]))
        return curves

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
                self.file_list.takeItem(row)
        self._refresh_controls()
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[TraceAnalysisFile] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(300)
        controls.setMaximumWidth(360)
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
        self.file_list.setMinimumHeight(170)
        data_layout.addWidget(self.file_list, 1)
        controls_layout.addWidget(data_group)

        settings_group, settings_layout = create_group_box("2. 分析设置")
        self.x_axis_combo = QtWidgets.QComboBox()
        self.x_axis_combo.addItem("样本序号", "sample")
        self.x_axis_combo.addItem("时间 (s)", "time")
        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItems(["叠加", "子图"])
        self.range_start = QtWidgets.QSpinBox()
        self.range_start.setRange(1, 2_000_000_000)
        self.range_start.setValue(1)
        self.range_end = QtWidgets.QSpinBox()
        self.range_end.setRange(1, 2_000_000_000)
        self.range_end.setValue(1)
        self.channel_list = QtWidgets.QListWidget()
        self.channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.channel_list.setAlternatingRowColors(True)
        self.channel_list.setMinimumHeight(130)
        self.demean_check = QtWidgets.QCheckBox("去均值")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")

        self.eu_table = QtWidgets.QTableWidget(0, 3)
        self.eu_table.setHorizontalHeaderLabels(["通道", "工程系数", "启用"])
        configure_data_table(self.eu_table, minimum_height=96, maximum_height=132)
        self.eu_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.eu_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.eu_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(5)
        range_row.addWidget(self.range_start)
        range_row.addWidget(QtWidgets.QLabel("至"))
        range_row.addWidget(self.range_end)

        settings_layout.addWidget(QtWidgets.QLabel("X 轴"), 0, 0)
        settings_layout.addWidget(self.x_axis_combo, 0, 1)
        settings_layout.addWidget(QtWidgets.QLabel("绘图模式"), 1, 0)
        settings_layout.addWidget(self.plot_mode_combo, 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("样本范围"), 2, 0)
        settings_layout.addLayout(range_row, 2, 1)
        settings_layout.addWidget(QtWidgets.QLabel("通道"), 3, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.channel_list, 3, 1)
        settings_layout.addWidget(QtWidgets.QLabel("工程单位"), 4, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.eu_table, 4, 1)
        settings_layout.addWidget(self.demean_check, 5, 0, 1, 2)
        controls_layout.addWidget(settings_group)

        action_group, action_layout = create_group_box("3. 操作", layout_type=QtWidgets.QVBoxLayout)
        action_layout.addWidget(self.plot_button)
        action_layout.addWidget(self.export_button)
        controls_layout.addWidget(action_group)
        controls_layout.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        self.ide_time_plot = pg.PlotWidget(title="IDE 时域")
        self.ide_psd_plot = pg.PlotWidget(title="IDE PSD")
        self.hac_plot = pg.PlotWidget(title="HAC 时域")
        self.tabs.addTab(self.ide_time_plot, "IDE 时域")
        self.tabs.addTab(self.ide_psd_plot, "IDE PSD")
        self.tabs.addTab(self.hac_plot, "HAC 时域")

        layout.addWidget(controls)
        layout.addWidget(self.tabs, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.file_list.currentRowChanged.connect(lambda _row: self._refresh_controls())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载减振器软件测试数据",
            str(self._last_directory),
            "测试数据文件 (*.txt *.csv);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        for path in paths:
            try:
                loaded_file = load_trace_analysis_file(path)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self.files.append(loaded_file)
            self.file_list.addItem(f"{loaded_file.table.name} ({loaded_file.trace_kind})")
            loaded += 1
        self._remember_paths(paths)
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh_controls()
        self._show_status(f"已加载测试文件：{loaded} 个")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.channel_list.clear()
        self.eu_table.setRowCount(0)
        for plot in (self.ide_time_plot, self.ide_psd_plot, self.hac_plot):
            plot.clear()
        self._plot_curves.clear()
        self._show_status("减振器软件测试数据分析已清空")

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
        curves = self._selected_time_curves(current)
        if current.trace_kind == "ide_trace":
            self.tabs.setTabEnabled(0, True)
            self.tabs.setTabEnabled(1, True)
            plotted = self._plot_curves_on_widget(
                self.ide_time_plot,
                curves,
                title=f"IDE 时域 - {current.table.name}",
                x_label=self.x_axis_combo.currentText(),
                y_label="工程值",
            )
            psd_curves = self._psd_curves(current, curves)
            self._plot_curves_on_widget(
                self.ide_psd_plot,
                psd_curves,
                title=f"IDE PSD - {current.table.name}",
                x_label="频率 (Hz)",
                y_label="PSD",
                log_x=True,
                log_y=True,
            )
        else:
            self.tabs.setCurrentWidget(self.hac_plot)
            plotted = self._plot_curves_on_widget(
                self.hac_plot,
                curves,
                title=f"HAC 时域 - {current.table.name}",
                x_label=self.x_axis_combo.currentText(),
                y_label="工程值",
            )
        self._show_status(f"已绘制测试通道：{plotted} 个")

    def _refresh_controls(self) -> None:
        current = self.current_file()
        self.channel_list.clear()
        self.eu_table.setRowCount(0)
        if current is None:
            return
        count = max(1, current.table.row_count)
        self.range_start.setRange(1, count)
        self.range_end.setRange(1, count)
        self.range_start.setValue(1)
        self.range_end.setValue(count)
        for row, name in enumerate(current.channels):
            item = QtWidgets.QListWidgetItem(name)
            item.setSelected(True)
            self.channel_list.addItem(item)
            self.eu_table.insertRow(row)
            self.eu_table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.eu_table.setItem(row, 1, QtWidgets.QTableWidgetItem("1.0"))
            enabled = QtWidgets.QTableWidgetItem()
            enabled.setFlags(enabled.flags() | QtCore.Qt.ItemIsUserCheckable)
            enabled.setCheckState(QtCore.Qt.Checked)
            self.eu_table.setItem(row, 2, enabled)

    def _selected_time_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        names = [item.text() for item in self.channel_list.selectedItems()]
        if not names:
            names = list(current.channels)[:1]
        start = max(0, int(self.range_start.value()) - 1)
        end = max(start + 1, int(self.range_end.value()))
        x_full = current.time_s if self.x_axis_combo.currentData() == "time" else np.arange(current.table.row_count, dtype=float)
        curves: list[CurvePair] = []
        for name in names:
            y = np.asarray(current.channels.get(name, np.array([], dtype=float)), dtype=float)
            x = np.asarray(x_full[: y.size], dtype=float)
            x = x[start:end]
            y = y[start:end]
            scale = self._eu_scale(name)
            y = y * scale
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            curves.append(CurvePair(name, x, y, self.x_axis_combo.currentText(), "工程值"))
        return curves

    def _psd_curves(self, current: TraceAnalysisFile, curves: list[CurvePair]) -> list[CurvePair]:
        psd_curves: list[CurvePair] = []
        fs = max(float(current.sample_rate), 1.0)
        for curve in curves:
            y = np.asarray(curve.y, dtype=float)
            if y.size < 4:
                continue
            nperseg = min(1024, max(8, int(2 ** math.floor(math.log2(y.size)))))
            f, pxx = compute_welch_psd(y, fs, nperseg)
            psd_curves.append(CurvePair(curve.label, f, pxx, "频率 (Hz)", "PSD"))
        return psd_curves

    def _eu_scale(self, channel_name: str) -> float:
        for row in range(self.eu_table.rowCount()):
            name_item = self.eu_table.item(row, 0)
            scale_item = self.eu_table.item(row, 1)
            enabled_item = self.eu_table.item(row, 2)
            if name_item is None or name_item.text() != channel_name:
                continue
            if enabled_item is not None and enabled_item.checkState() != QtCore.Qt.Checked:
                return 0.0
            try:
                value = float(scale_item.text()) if scale_item is not None else 1.0
            except ValueError:
                return 1.0
            return value if np.isfinite(value) else 1.0
        return 1.0

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
                self.file_list.takeItem(row)
        self._refresh_controls()
        self._show_status(f"已删除测试文件：{len(rows)} 个")

    def _export_active(self) -> None:
        plot = self.tabs.currentWidget()
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前测试数据图",
            str(self._last_directory / "trace_plot.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_plot_csv(plot, path)
        self._show_status(f"已导出 {Path(path).name}")


@dataclass(slots=True)
class ModalFile:
    path: Path
    dataset: AnalysisDataset


class ModalShapePage(DiagnosticPage):
    POINT_HEADERS = ["启用", "测点", "文件", "X通道", "Y通道", "Z通道", "X系数", "Y系数", "Z系数", "X", "Y", "Z"]
    LINE_HEADERS = ["启用", "起点", "终点", "来源"]
    POINT_CSV_HEADERS = ["point_id", "file_name", "x_ch", "y_ch", "z_ch", "x_scale", "y_scale", "z_scale", "x", "y", "z", "use"]

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self._build_ui_matlab_style()

    def _build_ui_matlab_style(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        controls = QtWidgets.QGroupBox("控制区")
        configure_control_panel(controls)
        controls.setMinimumWidth(460)
        controls.setMaximumWidth(620)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 12, 8, 8)
        controls_layout.setSpacing(7)

        file_button_grid = QtWidgets.QGridLayout()
        file_button_grid.setHorizontalSpacing(8)
        file_button_grid.setVerticalSpacing(6)
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.load_folder_button = QtWidgets.QPushButton("加载文件夹")
        self.delete_button = QtWidgets.QPushButton("删除文件")
        self.import_mapping_button = QtWidgets.QPushButton("导入映射表")
        self.export_mapping_button = QtWidgets.QPushButton("导出映射表")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "secondary")
        set_button_role(self.load_folder_button, "secondary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.import_mapping_button, "secondary")
        set_button_role(self.export_mapping_button, "secondary")
        set_button_role(self.clear_button, "secondary")
        file_button_grid.addWidget(self.load_button, 0, 0)
        file_button_grid.addWidget(self.load_folder_button, 0, 1)
        file_button_grid.addWidget(self.delete_button, 0, 2, 1, 2)
        file_button_grid.addWidget(self.import_mapping_button, 1, 0, 1, 2)
        file_button_grid.addWidget(self.export_mapping_button, 1, 2, 1, 2)
        file_button_grid.addWidget(self.clear_button, 2, 0, 1, 4)
        controls_layout.addLayout(file_button_grid)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(95)
        self.file_list.setMaximumHeight(150)
        controls_layout.addWidget(self.file_list)

        point_header = QtWidgets.QHBoxLayout()
        point_header.setContentsMargins(0, 0, 0, 0)
        point_header.setSpacing(6)
        point_header.addWidget(QtWidgets.QLabel("测点表"))
        point_header.addStretch(1)
        self.add_point_button = QtWidgets.QPushButton("新增测点")
        self.point_row_edit = QtWidgets.QLineEdit()
        self.point_row_edit.setPlaceholderText("行号")
        self.point_row_edit.setMaximumWidth(62)
        self.delete_point_button = QtWidgets.QPushButton("删除测点")
        set_button_role(self.add_point_button, "secondary")
        set_button_role(self.delete_point_button, "danger")
        self.add_point_button.setMinimumWidth(86)
        self.delete_point_button.setMinimumWidth(86)
        point_header.addWidget(self.add_point_button)
        point_header.addWidget(QtWidgets.QLabel("行号"))
        point_header.addWidget(self.point_row_edit)
        point_header.addWidget(self.delete_point_button)
        controls_layout.addLayout(point_header)

        self.point_table = QtWidgets.QTableWidget(0, len(self.POINT_HEADERS))
        self.point_table.setHorizontalHeaderLabels(self.POINT_HEADERS)
        configure_data_table(self.point_table, minimum_height=260)
        self.point_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.point_table.horizontalHeader().setStretchLastSection(True)
        controls_layout.addWidget(self.point_table, 3)

        line_header = QtWidgets.QHBoxLayout()
        line_header.setContentsMargins(0, 0, 0, 0)
        line_header.setSpacing(6)
        line_header.addWidget(QtWidgets.QLabel("连线表"))
        line_header.addStretch(1)
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
        line_header.addWidget(self.auto_lines_button)
        line_header.addWidget(self.add_line_button)
        line_header.addWidget(QtWidgets.QLabel("行号"))
        line_header.addWidget(self.line_row_edit)
        line_header.addWidget(self.delete_line_button)
        controls_layout.addLayout(line_header)

        self.line_table = QtWidgets.QTableWidget(0, len(self.LINE_HEADERS))
        self.line_table.setHorizontalHeaderLabels(self.LINE_HEADERS)
        configure_data_table(self.line_table, minimum_height=240)
        self.line_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.line_table.horizontalHeader().setStretchLastSection(True)
        controls_layout.addWidget(self.line_table, 2)

        preview = QtWidgets.QGroupBox("预览区")
        preview.setMinimumWidth(700)
        preview_layout = QtWidgets.QVBoxLayout(preview)
        preview_layout.setContentsMargins(8, 12, 8, 8)
        preview_layout.setSpacing(8)

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

        top_preview = QtWidgets.QWidget()
        top_preview_layout = QtWidgets.QVBoxLayout(top_preview)
        top_preview_layout.setContentsMargins(0, 0, 0, 0)
        top_preview_layout.setSpacing(8)

        freq_group, freq_layout = create_group_box("频率区", layout_type=QtWidgets.QVBoxLayout)
        freq_group.setMaximumHeight(260)
        self.frequency_edit.setMinimumWidth(130)
        self.frequency_edit.setMaximumWidth(240)
        self.apply_freq_button.setMinimumWidth(86)
        self.apply_freq_button.setMaximumWidth(140)
        for button in (self.find_peaks_button, self.extract_button, self.preview_button, self.export_gif_button, self.delete_peak_button):
            button.setMinimumWidth(86)
            button.setMaximumWidth(140)
        self.view_reset_button.setMinimumWidth(86)
        self.view_reset_button.setMaximumWidth(140)
        for spin in (self.view_azimuth_spin, self.view_elevation_spin, self.mode_gain_spin, self.gif_frame_count_spin):
            spin.setMaximumWidth(140)
        freq_row = QtWidgets.QHBoxLayout()
        freq_row.setContentsMargins(0, 0, 0, 0)
        freq_row.setSpacing(6)
        freq_row.addWidget(QtWidgets.QLabel("模态频率"))
        freq_row.addWidget(self.frequency_edit)
        freq_row.addWidget(self.apply_freq_button)
        freq_row.addStretch(1)
        freq_layout.addLayout(freq_row)

        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(6)
        view_row.addWidget(QtWidgets.QLabel("方位角"))
        view_row.addWidget(self.view_azimuth_spin, 1)
        view_row.addWidget(QtWidgets.QLabel("俯仰角"))
        view_row.addWidget(self.view_elevation_spin, 1)
        view_row.addStretch(1)
        freq_layout.addLayout(view_row)

        render_row = QtWidgets.QHBoxLayout()
        render_row.setContentsMargins(0, 0, 0, 0)
        render_row.setSpacing(6)
        render_row.addWidget(QtWidgets.QLabel("振型放大"))
        render_row.addWidget(self.mode_gain_spin, 1)
        render_row.addWidget(QtWidgets.QLabel("GIF 帧数"))
        render_row.addWidget(self.gif_frame_count_spin, 1)
        render_row.addWidget(self.view_reset_button)
        render_row.addStretch(1)
        freq_layout.addLayout(render_row)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        action_row.addWidget(self.find_peaks_button)
        action_row.addWidget(self.extract_button)
        action_row.addWidget(self.preview_button)
        action_row.addWidget(self.export_gif_button)
        action_row.addWidget(self.delete_peak_button)
        action_row.addStretch(1)
        freq_layout.addLayout(action_row)

        freq_layout.addWidget(QtWidgets.QLabel("频率候选"))
        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.setAlternatingRowColors(True)
        self.candidate_list.setMinimumHeight(54)
        self.candidate_list.setMaximumHeight(72)
        freq_layout.addWidget(self.candidate_list)
        top_preview_layout.addWidget(freq_group, 0)
        self.frf_plot = pg.PlotWidget(title="模态 FRF 候选图")
        top_preview_layout.addWidget(self.frf_plot, 1)
        preview_layout.addWidget(top_preview, 2)

        bottom_preview = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        bottom_preview.setChildrenCollapsible(False)
        self.layout_plot = Modal3DView()
        self.mode_plot = Modal3DView()
        self.layout_plot.setMinimumSize(360, 280)
        self.mode_plot.setMinimumSize(360, 280)
        layout_group, layout_group_layout = create_group_box("测点骨架图", layout_type=QtWidgets.QVBoxLayout)
        mode_group, mode_group_layout = create_group_box("振型预览", layout_type=QtWidgets.QVBoxLayout)
        layout_group_layout.addWidget(self.layout_plot)
        mode_group_layout.addWidget(self.mode_plot)
        bottom_preview.addWidget(layout_group)
        bottom_preview.addWidget(mode_group)
        bottom_preview.setStretchFactor(0, 1)
        bottom_preview.setStretchFactor(1, 1)
        bottom_preview.setSizes([560, 560])
        preview_layout.addWidget(bottom_preview, 3)

        main_splitter.addWidget(controls)
        main_splitter.addWidget(preview)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([520, 1100])
        layout.addWidget(main_splitter)

        self.load_button.clicked.connect(self._choose_files)
        self.load_folder_button.clicked.connect(self._choose_folder)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.import_mapping_button.clicked.connect(self._choose_import_mapping)
        self.export_mapping_button.clicked.connect(self._choose_export_mapping)
        self.add_point_button.clicked.connect(self._add_point_row)
        self.delete_point_button.clicked.connect(self._delete_point_rows)
        self.auto_lines_button.clicked.connect(self.auto_build_lines)
        self.add_line_button.clicked.connect(self._add_line_row)
        self.delete_line_button.clicked.connect(self._delete_line_rows)
        self.apply_freq_button.clicked.connect(self.apply_frequency)
        self.find_peaks_button.clicked.connect(self.find_peaks)
        self.delete_peak_button.clicked.connect(self.delete_selected_peak)
        self.view_azimuth_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.view_elevation_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.mode_gain_spin.valueChanged.connect(lambda _value: self._mode_gain_changed())
        self.view_reset_button.clicked.connect(self._reset_view)
        self.layout_plot.cameraChanged.connect(self._sync_view_from_3d_camera)
        self.mode_plot.cameraChanged.connect(self._sync_view_from_3d_camera)
        self.extract_button.clicked.connect(self.extract_mode)
        self.preview_button.clicked.connect(self.preview_mode)
        self.export_gif_button.clicked.connect(self._choose_export_gif)
        self.candidate_list.currentItemChanged.connect(lambda item, _previous: self._candidate_selected(item))
        self.point_table.itemChanged.connect(lambda _item: self._mapping_changed())
        self.line_table.itemChanged.connect(lambda _item: self._mapping_changed())

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
        self.frf_plot = pg.PlotWidget(title="FRF / 峰值")
        self.layout_plot = Modal3DView()
        self.mode_plot = Modal3DView()
        self.layout_plot.setMinimumSize(360, 260)
        self.mode_plot.setMinimumSize(360, 260)

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
        self.auto_lines_button.clicked.connect(self.auto_build_lines)
        self.add_line_button.clicked.connect(self._add_line_row)
        self.delete_line_button.clicked.connect(self._delete_line_rows)
        self.apply_freq_button.clicked.connect(self.apply_frequency)
        self.find_peaks_button.clicked.connect(self.find_peaks)
        self.delete_peak_button.clicked.connect(self.delete_selected_peak)
        self.view_azimuth_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.view_elevation_spin.valueChanged.connect(lambda _value: self._view_changed())
        self.mode_gain_spin.valueChanged.connect(lambda _value: self._mode_gain_changed())
        self.view_reset_button.clicked.connect(self._reset_view)
        self.layout_plot.cameraChanged.connect(self._sync_view_from_3d_camera)
        self.mode_plot.cameraChanged.connect(self._sync_view_from_3d_camera)
        self.extract_button.clicked.connect(self.extract_mode)
        self.preview_button.clicked.connect(self.preview_mode)
        self.export_gif_button.clicked.connect(self._choose_export_gif)
        self.candidate_list.currentItemChanged.connect(lambda item, _previous: self._candidate_selected(item))
        self.point_table.itemChanged.connect(lambda _item: self._mapping_changed())
        self.line_table.itemChanged.connect(lambda _item: self._mapping_changed())

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
        loaded = 0
        existing = {modal_file.path.name.lower() for modal_file in self.files}
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
            self._append_default_point_row_for_file(path.name)
            existing.add(path.name.lower())
            loaded += 1
        self._remember_paths(paths)
        if loaded:
            self.auto_build_lines(show_status=False)
        self._invalidate_mode()
        self._refresh_layout_plot()
        self.find_peaks()
        self._show_status(f"已加载模态文件：{loaded} 个")

    def clear(self) -> None:
        self._preview_timer.stop()
        self.files.clear()
        self.file_list.clear()
        self.candidate_list.clear()
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
        self.candidate_list.clear()
        self._auto_peaks = []
        if freq.size < 3:
            self._show_status("没有可用于峰值搜索的有效 FRF 曲线")
            return []
        smooth = moving_average(db_values, min(9, max(3, int(freq.size // 20) * 2 + 1)))
        aggregate_peaks = local_peak_frequencies(freq, smooth, max_count=12)
        local_peaks = self._individual_peak_frequencies(max_per_curve=3)
        peaks = merge_frequency_candidates(aggregate_peaks + local_peaks, max_count=24)
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
        labels: list[str] = []
        actual_freqs: list[float] = []
        for group in groups:
            disp, actual = self._extract_group_mode_vector(group["rows"], target)
            if disp is None:
                continue
            coords.append(group["coords"])
            displacements.append(disp)
            labels.append(str(group["point"]))
            if np.isfinite(actual):
                actual_freqs.append(float(actual))
        if not coords:
            self._show_status("模态提取未找到有效测点数据")
            return None
        coord_arr = np.asarray(coords, dtype=float)
        disp_complex = np.asarray(displacements, dtype=complex)
        ref_value = first_nonzero_complex(disp_complex)
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
        saved = False
        if self.last_mode is not None:
            try:
                write_mode_animation_gif(
                    destination,
                    self.last_mode,
                    frame_count=self._gif_frame_count(),
                    azimuth_deg=self._view_azimuth,
                    elevation_deg=self._view_elevation,
                )
                saved = True
            except Exception:
                saved = False
        if not saved:
            pixmap = self.mode_plot.grab()
            if not pixmap.isNull():
                saved = bool(pixmap.save(str(destination), "GIF"))
        if not saved:
            destination.write_bytes(base64.b64decode(_MINIMAL_GIF_BASE64))
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
                        "x": float(self._table_text(self.point_table, row, 9, "0")),
                        "y": float(self._table_text(self.point_table, row, 10, "0")),
                        "z": float(self._table_text(self.point_table, row, 11, "0")),
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
            start = self._table_text(self.line_table, row, 1, "")
            end = self._table_text(self.line_table, row, 2, "")
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
        for modal_file in self.files:
            if modal_file.path.name == name or modal_file.dataset.name == name:
                return modal_file.dataset
        return self.files[0].dataset if self.files else None

    def _modal_channel_value(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> float:
        value = self._modal_channel_value_complex(dataset, channel_number, freq_index)
        return float(np.real(value)) if np.isfinite(np.real(value)) else 0.0

    def _modal_channel_value_complex(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> complex:
        keys = list(dataset.frf) or list(dataset.autospectrum) or dataset.channel_keys
        if not keys:
            return 0.0 + 0.0j
        index = int(channel_number) - 1
        if index < 0 or index >= len(keys):
            return 0.0 + 0.0j
        key = keys[index]
        source = dataset.frf.get(key)
        if source is None:
            source = dataset.autospectrum.get(key)
        if source is None:
            return 0.0 + 0.0j
        arr = np.asarray(source).ravel()
        if arr.size == 0:
            return 0.0 + 0.0j
        value = arr[min(freq_index, arr.size - 1)]
        return complex(value) if np.isfinite(np.real(value)) and np.isfinite(np.imag(value)) else 0.0 + 0.0j

    def _modal_channel_coherence(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> float:
        keys = list(dataset.frf) or list(dataset.autospectrum) or dataset.channel_keys
        if not keys:
            return float("nan")
        index = int(channel_number) - 1
        if index < 0 or index >= len(keys):
            return float("nan")
        values = dataset.coherence.get(keys[index])
        if values is None:
            return float("nan")
        arr = np.asarray(values, dtype=float).ravel()
        if arr.size == 0:
            return float("nan")
        return float(arr[min(freq_index, arr.size - 1)])

    def _extract_group_mode_vector(self, rows: list[dict[str, object]], target: float) -> tuple[list[complex] | None, float]:
        vector: list[complex | None] = [None, None, None]
        coherence = [float("nan"), float("nan"), float("nan")]
        actual_freqs: list[float] = []
        for row in rows:
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
            if freq.size == 0:
                continue
            nearest = int(np.nanargmin(np.abs(freq - target)))
            actual_freqs.append(float(freq[nearest]))
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
                    coherence[axis_index] = coh
        if all(value is None for value in vector):
            return None, float("nan")
        return [value if value is not None else 0.0 + 0.0j for value in vector], (
            float(np.nanmedian(actual_freqs)) if actual_freqs else target
        )

    def _aggregate_frf_curve(self) -> tuple[np.ndarray, np.ndarray]:
        rows = self._point_rows(require_enabled=True, require_bound=True)
        series: list[tuple[np.ndarray, np.ndarray]] = []
        if rows:
            for row in rows:
                dataset = self._dataset_by_name(str(row["file"]))
                if dataset is None:
                    continue
                for channel in (int(row["x_ch"]), int(row["y_ch"]), int(row["z_ch"])):
                    series_values = self._channel_frf_series(dataset, channel)
                    if series_values is not None:
                        series.append(series_values)
        if not series:
            for modal_file in self.files:
                dataset = modal_file.dataset
                freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
                for values in list(dataset.frf.values()) or list(dataset.autospectrum.values()):
                    count = min(freq.size, np.asarray(values).size)
                    if count >= 3:
                        series.append((freq[:count], np.asarray(values).ravel()[:count]))
        if not series:
            return np.array([], dtype=float), np.array([], dtype=float)
        base_freq = np.asarray(series[0][0], dtype=float)
        valid_base = np.isfinite(base_freq) & (base_freq > 0.0)
        base_freq = base_freq[valid_base]
        if base_freq.size < 3:
            return np.array([], dtype=float), np.array([], dtype=float)
        curves: list[np.ndarray] = []
        for freq, values in series:
            freq = np.asarray(freq, dtype=float).ravel()
            values = np.asarray(values).ravel()
            count = min(freq.size, values.size)
            freq = freq[:count]
            values = values[:count]
            mag = np.abs(values)
            mask = np.isfinite(freq) & (freq > 0.0) & np.isfinite(mag) & (mag > 0.0)
            if np.count_nonzero(mask) < 3:
                continue
            db = 20.0 * np.log10(np.maximum(mag[mask], 1e-300))
            try:
                curves.append(np.interp(base_freq, freq[mask], db, left=np.nan, right=np.nan))
            except ValueError:
                continue
        if not curves:
            return np.array([], dtype=float), np.array([], dtype=float)
        return base_freq, np.nanmean(np.vstack(curves), axis=0)

    def _channel_frf_series(self, dataset: AnalysisDataset, channel_number: int) -> tuple[np.ndarray, np.ndarray] | None:
        keys = list(dataset.frf) or list(dataset.autospectrum)
        if not keys:
            return None
        index = int(channel_number) - 1
        if index < 0 or index >= len(keys):
            return None
        source = dataset.frf.get(keys[index]) if keys[index] in dataset.frf else dataset.autospectrum.get(keys[index])
        freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        if source is None or freq.size == 0:
            return None
        values = np.asarray(source).ravel()
        count = min(freq.size, values.size)
        if count < 3:
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
                peaks.extend(local_peak_frequencies(freq[mask], moving_average(db, 5), max_count=max_per_curve))
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
            projected[:, 0],
            projected[:, 1],
            pen=pg.mkPen("#9aa5b1", width=1.0),
            symbol="o",
            symbolBrush="#9aa5b1",
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
            "CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            count = self.import_point_mapping_csv(path)
        except Exception as exc:
            self._show_status(f"导入测点映射失败：{exc}")
            return
        self._remember_paths([Path(path)])
        self._show_status(f"已导入测点映射：{count} 行")

    def _choose_export_mapping(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出测点映射",
            str(self._last_directory / "point_mapping.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_point_mapping_csv(path)
        self._show_status(f"已导出测点映射：{Path(path).name}")

    def import_point_mapping_csv(self, path: str | Path) -> int:
        with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            self.point_table.setRowCount(0)
            for record in reader:
                values = [
                    self._csv_bool(record.get("use"), True),
                    record.get("point_id") or record.get("point") or "",
                    record.get("file_name") or record.get("file") or "",
                    record.get("x_ch") or "2",
                    record.get("y_ch") or "3",
                    record.get("z_ch") or "4",
                    record.get("x_scale") or "1",
                    record.get("y_scale") or "1",
                    record.get("z_scale") or "1",
                    record.get("x") or "0",
                    record.get("y") or "0",
                    record.get("z") or "0",
                ]
                self._insert_point_row(values)
        self._remove_invalid_lines()
        self._invalidate_mode()
        self._refresh_layout_plot()
        return self.point_table.rowCount()

    def export_point_mapping_csv(self, path: str | Path) -> Path:
        destination = Path(path)
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.POINT_CSV_HEADERS)
            writer.writeheader()
            for row in self._point_rows(require_enabled=False, require_bound=False):
                writer.writerow(
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
                        "use": "1",
                    }
                )
        return destination

    def auto_build_lines(self, *, show_status: bool = True) -> None:
        groups = self._point_groups(self._point_rows(require_enabled=True, require_bound=False))
        manual_rows = [row for row in self._line_rows(require_enabled=False) if str(row["source"]).lower() != "auto"]
        self.line_table.setRowCount(0)
        for row in manual_rows:
            self._insert_line_row([True, row["start"], row["end"], row["source"]])
        if len(groups) >= 2:
            coords = np.asarray([group["coords"] for group in groups], dtype=float)
            labels = [str(group["point"]) for group in groups]
            edges = minimum_spanning_edges(coords)
            existing = {tuple(sorted((str(row["start"]), str(row["end"])))) for row in manual_rows}
            for left, right in edges:
                key = tuple(sorted((labels[left], labels[right])))
                if key in existing:
                    continue
                self._insert_line_row([True, labels[left], labels[right], "auto"])
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

    def _append_default_point_row_for_file(self, file_name: str) -> None:
        existing_files = {str(row["file"]).lower() for row in self._point_rows(require_enabled=False, require_bound=False)}
        if file_name.lower() in existing_files:
            return
        self._insert_point_row(self._default_point_values(file_name))

    def _default_point_values(self, file_name: str) -> list[object]:
        index = self.point_table.rowCount()
        x_ch, y_ch, z_ch = self._default_modal_channels(file_name)
        return [True, f"P{index + 1}", file_name, x_ch, y_ch, z_ch, 1, 1, 1, float(index), 0.0, 0.0]

    def _default_modal_channels(self, file_name: str) -> tuple[int, int, int]:
        dataset = self._dataset_by_name(file_name)
        if dataset is None:
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
            item = QtWidgets.QTableWidgetItem("" if isinstance(value, bool) else str(value))
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
            start = self._table_text(self.line_table, row, 1, "")
            end = self._table_text(self.line_table, row, 2, "")
            if start not in valid_points or end not in valid_points:
                self.line_table.removeRow(row)

    def _mapping_changed(self) -> None:
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

    def _refresh_candidate_list(self) -> None:
        selected = self._active_frequency
        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
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
        self.candidate_list.blockSignals(False)

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
    plot.showGrid(x=True, y=True, alpha=float(theme.get("grid_alpha", 0.22)))
    for axis_name in ("left", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(str(theme.get("axis", "#172033"))))
        axis.setTextPen(pg.mkPen(str(theme.get("axis", "#172033"))))


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


def first_nonzero_complex(values: np.ndarray) -> complex | None:
    arr = np.asarray(values, dtype=complex).ravel()
    for value in arr:
        if np.isfinite(np.real(value)) and np.isfinite(np.imag(value)) and abs(value) > 0.0:
            return complex(value)
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


def project_points_3d(coords: np.ndarray, *, azimuth_deg: float = 35.0, elevation_deg: float = 24.0) -> np.ndarray:
    points = np.asarray(coords, dtype=float)
    if points.ndim != 2 or points.shape[1] < 3:
        return np.zeros((0, 2), dtype=float)
    azimuth = math.radians(float(azimuth_deg))
    elevation = math.radians(float(elevation_deg))
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    x_rot = x * math.cos(azimuth) - y * math.sin(azimuth)
    y_rot = x * math.sin(azimuth) + y * math.cos(azimuth)
    y_proj = y_rot * math.cos(elevation) - z * math.sin(elevation)
    return np.column_stack([x_rot, y_proj])


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
        base_xy = map_projected_to_pixels(
            project_points_3d(coords, azimuth_deg=azimuth_deg, elevation_deg=elevation_deg),
            bounds,
            width,
            height,
        )
        def_xy = map_projected_to_pixels(
            project_points_3d(deformed, azimuth_deg=azimuth_deg, elevation_deg=elevation_deg),
            bounds,
            width,
            height,
        )
        frame = np.zeros((height, width), dtype=np.uint8)
        for left, right in edges:
            draw_indexed_line(frame, base_xy[left], base_xy[right], 1)
            draw_indexed_line(frame, def_xy[left], def_xy[right], 3)
        for point in base_xy:
            draw_indexed_circle(frame, point, radius=4, color=2)
        for point in def_xy:
            draw_indexed_circle(frame, point, radius=5, color=4)
        frames.append(frame)
    return frames


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
                if next_code >= (1 << code_size) and code_size < 12:
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


_MINIMAL_GIF_BASE64 = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
