from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np

from python_vna.controller import VnaController
from python_vna.continuous_recording import (
    ContinuousDatWriter,
    RecordingStatus,
    recording_directory_name,
)
from python_vna.display_transforms import (
    align_vector_to_values,
    legacy_frequency_int_vector,
    legacy_j_factor,
    transform_autospectrum,
    transform_curve,
    transform_legacy_autospectrum,
)
from python_vna.measurement_filter import filter_measurement_to_enabled_channels
from python_vna.models import ChannelConfig, MeasurementSet, SavedSession, SessionConfig
from python_vna.optional import require
from python_vna import __version__ as PYTHON_VNA_VERSION
from python_vna.storage import (
    default_session_config,
    load_legacy_vna,
    load_saved_session_json,
    save_measurement_csv,
    save_measurement_hdf5,
    save_measurement_npz,
    save_legacy_vna,
    save_session_json,
)

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
pg = require("pyqtgraph", "python -m pip install -e .[gui]")

CURVE_Z = 0
LEGEND_Z = 10
MARKER_Z = 20
CURSOR_Z = 30
DATA_TIP_Z = 40


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base_path / relative_path


class DetachedPlotWindow(QtWidgets.QDialog):
    def __init__(self, parent=None, status_callback=None):
        super().__init__(None)
        self._status_callback = status_callback
        self.setWindowTitle("Current Plot Window")
        self.resize(900, 700)
        self._theme: dict[str, object] = {}
        self._data_tip_enabled = False
        self._curves: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {
            "top": {},
            "bottom": {},
        }
        self._log_modes: dict[str, dict[str, bool]] = {
            "top": {"x": False, "y": False},
            "bottom": {"x": False, "y": False},
        }
        self._data_tip_items: dict[str, list[dict[str, object]]] = {
            "top": [],
            "bottom": [],
        }
        self._suppress_next_plot_context_menu = False

        layout = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self.data_tip_button = QtWidgets.QToolButton()
        self.data_tip_button.setText("Data Tip")
        self.data_tip_button.setCheckable(True)
        self.data_tip_button.toggled.connect(self._toggle_data_tip_mode)
        self.clear_tips_button = QtWidgets.QToolButton()
        self.clear_tips_button.setText("Clear Tips")
        self.clear_tips_button.clicked.connect(self._clear_all_data_tips)
        self.auto_scale_button = QtWidgets.QToolButton()
        self.auto_scale_button.setText("Auto Scale")
        self.auto_scale_button.clicked.connect(self._auto_scale_all_plots)
        toolbar.addWidget(self.data_tip_button)
        toolbar.addWidget(self.clear_tips_button)
        toolbar.addWidget(self.auto_scale_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.upper_plot = pg.PlotWidget(title="Upper")
        self.lower_plot = pg.PlotWidget(title="Lower")
        for key, plot in (("top", self.upper_plot), ("bottom", self.lower_plot)):
            legend = plot.addLegend(offset=(3, 2))
            plot.getPlotItem().setMenuEnabled(False)
            self._attach_plot_context_menu(key, plot)
            self._attach_data_tip_clicks(key, plot)
        layout.addWidget(self.upper_plot, 1)
        layout.addWidget(self.lower_plot, 1)
        if parent is not None and hasattr(parent, "_theme"):
            self.apply_theme(parent._theme())

    def set_plot_data(self, panels: dict[str, dict[str, object]]) -> None:
        for key, plot in (("top", self.upper_plot), ("bottom", self.lower_plot)):
            panel = panels.get(key, {})
            curves = panel.get("curves", {})
            self._data_tip_items[key].clear()
            plot.clear()
            legend = plot.plotItem.legend
            if legend is not None:
                legend.clear()
            plot.setTitle(str(panel.get("title", "Upper" if key == "top" else "Lower")))
            plot.setLabel("bottom", str(panel.get("x_label", "X")))
            plot.setLabel("left", str(panel.get("y_label", "Y")))
            plot.setLogMode(
                x=bool(panel.get("log_x", False)),
                y=bool(panel.get("log_y", False)),
            )
            self._apply_plot_theme(plot)
            self._log_modes[key] = {
                "x": bool(panel.get("log_x", False)),
                "y": bool(panel.get("log_y", False)),
            }
            self._curves[key] = {}
            if not isinstance(curves, dict):
                continue
            for index, (trace_name, curve) in enumerate(curves.items()):
                x_data, y_data = curve
                x_arr = np.asarray(x_data, dtype=float)
                y_arr = np.asarray(y_data, dtype=float)
                if x_arr.size == 0 or y_arr.size == 0:
                    continue
                point_count = min(x_arr.size, y_arr.size)
                self._curves[key][trace_name] = (
                    x_arr[:point_count].copy(),
                    y_arr[:point_count].copy(),
                )
                color_map = panel.get("colors", {})
                color = (
                    color_map.get(trace_name)
                    if isinstance(color_map, dict)
                    else None
                ) or MainWindow.TRACE_COLORS[index % len(MainWindow.TRACE_COLORS)]
                plot.plot(
                    x_arr[:point_count],
                    y_arr[:point_count],
                    pen=pg.mkPen(color, width=1.4),
                    name=str(panel.get("legend_names", {}).get(trace_name, trace_name))
                    if isinstance(panel.get("legend_names", {}), dict)
                    else str(trace_name),
                )
            plot.enableAutoRange()

    def apply_theme(self, theme: dict[str, object]) -> None:
        self._theme = dict(theme)
        self.setStyleSheet(
            MainWindow._theme_stylesheet(
                """
                QDialog {
                    background: @window_bg@;
                    color: @text@;
                }
                QToolButton {
                    background: @accent@;
                    color: #ffffff;
                    font-weight: bold;
                    border: 1px solid @accent_hover@;
                    border-radius: 7px;
                    padding: 4px 10px;
                    min-height: 22px;
                }
                QToolButton:hover {
                    background: @accent_hover@;
                }
                QToolButton:checked {
                    background: @accent_alt@;
                    border-color: @accent_alt@;
                }
                """,
                self._theme,
            )
        )
        for plot in (self.upper_plot, self.lower_plot):
            self._apply_plot_theme(plot)

    def _apply_plot_theme(self, plot) -> None:
        if not self._theme:
            return
        plot.setBackground(str(self._theme["plot_bg"]))
        plot.showGrid(x=True, y=True, alpha=float(self._theme["grid_alpha"]))
        legend = plot.plotItem.legend
        if legend is not None:
            legend.setBrush(pg.mkBrush(*self._theme["legend_bg"]))
            legend.setPen(pg.mkPen(str(self._theme["legend_text"]), width=0.8))
            legend.opts["labelTextColor"] = str(self._theme["legend_text"])
            for _sample, label in legend.items:
                label.setText(label.text, color=str(self._theme["legend_text"]))
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(str(self._theme["axis"])))
            axis.setTextPen(pg.mkPen(str(self._theme["axis"])))
        plot.getPlotItem().titleLabel.item.setDefaultTextColor(
            QtGui.QColor(str(self._theme["axis"]))
        )

    def _toggle_data_tip_mode(self, enabled: bool) -> None:
        self._data_tip_enabled = enabled
        self._show_status(f"Detached plot Data Tip mode {'on' if enabled else 'off'}")

    def _auto_scale_all_plots(self) -> None:
        self.upper_plot.enableAutoRange()
        self.lower_plot.enableAutoRange()
        self._show_status("Auto-scaled detached plots")

    def _plot_for_key(self, key: str):
        return self.upper_plot if key == "top" else self.lower_plot

    def _x_to_plot_coord(self, key: str, value: float) -> float:
        if self._log_modes[key]["x"]:
            return float(np.log10(max(value, 1e-300)))
        return value

    def _y_to_plot_coord(self, key: str, value: float) -> float:
        if self._log_modes[key]["y"]:
            return float(np.log10(max(value, 1e-300)))
        return value

    def _x_from_plot_coord(self, key: str, value: float) -> float:
        if self._log_modes[key]["x"]:
            return float(10.0 ** value)
        return value

    def _y_from_plot_coord(self, key: str, value: float) -> float:
        if self._log_modes[key]["y"]:
            return float(10.0 ** value)
        return value

    def _scene_to_data_point(self, key: str, scene_pos) -> tuple[float, float]:
        plot = self._plot_for_key(key)
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        return (
            self._x_from_plot_coord(key, float(mouse_point.x())),
            self._y_from_plot_coord(key, float(mouse_point.y())),
        )

    def _nearest_curve_point(
        self, key: str, click_x: float, click_y: float, trace_name: str | None = None
    ) -> tuple[str | None, float | None, float | None]:
        curves = self._curves.get(key, {})
        if trace_name in curves:
            iterable = [(trace_name, curves[trace_name])]
        else:
            iterable = list(curves.items())
        if not iterable:
            return None, None, None
        plot = self._plot_for_key(key)
        x_range, y_range = plot.viewRange()
        x_span = max(abs(float(x_range[1] - x_range[0])), 1e-9)
        y_span = max(abs(float(y_range[1] - y_range[0])), 1e-9)
        click_plot_x = self._x_to_plot_coord(key, click_x)
        click_plot_y = self._y_to_plot_coord(key, click_y)
        best: tuple[float, str, float, float] | None = None
        for name, (x_data, y_data) in iterable:
            x_arr = np.asarray(x_data, dtype=float)
            y_arr = np.asarray(y_data, dtype=float)
            count = min(x_arr.size, y_arr.size)
            if count == 0:
                continue
            x_arr = x_arr[:count]
            y_arr = y_arr[:count]
            finite = np.isfinite(x_arr) & np.isfinite(y_arr)
            if self._log_modes[key]["x"]:
                finite &= x_arr > 0.0
            if self._log_modes[key]["y"]:
                finite &= y_arr > 0.0
            if not np.any(finite):
                continue
            x_arr = x_arr[finite]
            y_arr = y_arr[finite]
            plot_x = np.asarray([self._x_to_plot_coord(key, float(value)) for value in x_arr])
            plot_y = np.asarray([self._y_to_plot_coord(key, float(value)) for value in y_arr])
            scores = ((plot_x - click_plot_x) / x_span) ** 2 + ((plot_y - click_plot_y) / y_span) ** 2
            index = int(np.nanargmin(scores))
            score = float(scores[index])
            if best is None or score < best[0]:
                best = (score, str(name), float(x_arr[index]), float(y_arr[index]))
        if best is None:
            return None, None, None
        return best[1], best[2], best[3]

    def _data_tip_anchor_for_plot_point(self, key: str, plot_x: float, plot_y: float) -> tuple[float, float]:
        plot = self._plot_for_key(key)
        try:
            x_range, y_range = plot.viewRange()
        except Exception:
            return (-0.05, 1.05)
        x_span = max(float(x_range[1] - x_range[0]), 1e-20)
        y_span = max(float(y_range[1] - y_range[0]), 1e-20)
        near_right = (float(plot_x) - float(x_range[0])) / x_span > 0.72
        near_top = (float(plot_y) - float(y_range[0])) / y_span > 0.72
        return (1.05 if near_right else -0.05, -0.05 if near_top else 1.05)

    def _update_data_tip_position(
        self, key: str, data_tip: dict[str, object], tip_x: float, tip_y: float
    ) -> None:
        plot_x = self._x_to_plot_coord(key, tip_x)
        plot_y = self._y_to_plot_coord(key, tip_y)
        data_tip["x"] = tip_x
        data_tip["y"] = tip_y
        data_tip["point"].setData([plot_x], [plot_y])
        data_tip["text"].setText(f"X {tip_x:.6g}\nY {tip_y:.6g}")
        if hasattr(data_tip["text"], "setAnchor"):
            data_tip["text"].setAnchor(
                self._data_tip_anchor_for_plot_point(key, plot_x, plot_y)
            )
        data_tip["text"].setPos(plot_x, plot_y)

    def _drag_data_tip_to_scene_pos(
        self, key: str, data_tip: dict[str, object], scene_pos
    ) -> bool:
        click_x, click_y = self._scene_to_data_point(key, scene_pos)
        _trace, tip_x, tip_y = self._nearest_curve_point(
            key, click_x, click_y, str(data_tip.get("trace") or "")
        )
        if tip_x is None or tip_y is None:
            return False
        self._update_data_tip_position(key, data_tip, tip_x, tip_y)
        self._show_status(f"{key.title()} plot data tip moved: x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _place_data_tip(self, key: str, click_x: float, click_y: float) -> bool:
        trace_name, tip_x, tip_y = self._nearest_curve_point(key, click_x, click_y)
        if trace_name is None or tip_x is None or tip_y is None:
            return False
        plot = self._plot_for_key(key)
        plot_x = self._x_to_plot_coord(key, tip_x)
        plot_y = self._y_to_plot_coord(key, tip_y)
        data_tip: dict[str, object] = {
            "trace": trace_name,
            "x": tip_x,
            "y": tip_y,
        }
        point = DataTipPoint(
            [plot_x],
            [plot_y],
            size=9,
            symbol="o",
            brush=pg.mkBrush("#fff59d"),
            pen=pg.mkPen("#111111", width=0.8),
            pxMode=True,
            on_drag=lambda scene_pos, plot_key=key, tip=data_tip: self._drag_data_tip_to_scene_pos(
                plot_key, tip, scene_pos
            ),
            on_context_menu=lambda screen_pos, plot_key=key, tip=data_tip: self._show_data_tip_menu(
                plot_key, tip, screen_pos
            ),
        )
        point.setZValue(DATA_TIP_Z + 1)
        text = DataTipText(
            text=f"X {tip_x:.6g}\nY {tip_y:.6g}",
            color="#111111",
            anchor=self._data_tip_anchor_for_plot_point(key, plot_x, plot_y),
            fill=pg.mkBrush(255, 245, 157, 230),
            border=pg.mkPen("#111111", width=0.8),
            on_context_menu=lambda screen_pos, plot_key=key, tip=data_tip: self._show_data_tip_menu(
                plot_key, tip, screen_pos
            ),
        )
        text.setZValue(DATA_TIP_Z + 2)
        text.setPos(plot_x, plot_y)
        plot.addItem(point)
        plot.addItem(text)
        data_tip["point"] = point
        data_tip["text"] = text
        self._data_tip_items[key].append(data_tip)
        self._show_status(f"{key.title()} plot data tip: x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _clear_data_tips(self, key: str) -> None:
        plot = self._plot_for_key(key)
        for data_tip in list(self._data_tip_items[key]):
            plot.removeItem(data_tip["point"])
            plot.removeItem(data_tip["text"])
        self._data_tip_items[key].clear()

    def _clear_all_data_tips(self) -> None:
        self._clear_data_tips("top")
        self._clear_data_tips("bottom")
        self._show_status("Cleared detached plot data tips")

    def _delete_data_tip(self, key: str, data_tip: dict[str, object]) -> bool:
        if data_tip not in self._data_tip_items[key]:
            return False
        plot = self._plot_for_key(key)
        plot.removeItem(data_tip["point"])
        plot.removeItem(data_tip["text"])
        self._data_tip_items[key].remove(data_tip)
        self._show_status(f"Deleted {key} detached plot data tip")
        return True

    def _build_data_tip_menu(self) -> tuple[QtWidgets.QMenu, dict[str, QtGui.QAction]]:
        menu = QtWidgets.QMenu(self)
        actions = {
            "delete_this": menu.addAction("Delete This Data Tip"),
            "delete_all": menu.addAction("Delete All Data Tips"),
        }
        return menu, actions

    def _show_data_tip_menu(self, key: str, data_tip: dict[str, object], screen_pos) -> None:
        self._suppress_plot_context_menu_once()
        menu, actions = self._build_data_tip_menu()
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is actions["delete_this"]:
            self._delete_data_tip(key, data_tip)
        elif action is actions["delete_all"]:
            self._clear_all_data_tips()

    def _suppress_plot_context_menu_once(self) -> None:
        self._suppress_next_plot_context_menu = True

    def _build_plot_context_menu(
        self, key: str, scene_pos=None
    ) -> tuple[QtWidgets.QMenu, dict[str, QtGui.QAction]]:
        menu = QtWidgets.QMenu(self)
        actions: dict[str, QtGui.QAction] = {}
        actions["auto_scale"] = menu.addAction("Auto Scale")
        actions["data_tip"] = menu.addAction("Data Tip")
        actions["data_tip"].setCheckable(True)
        actions["data_tip"].setChecked(self._data_tip_enabled)
        actions["data_tip"].setEnabled(scene_pos is not None and bool(self._curves.get(key)))
        actions["clear_data_tips"] = menu.addAction("Clear Data Tips")
        actions["clear_data_tips"].setEnabled(bool(self._data_tip_items[key]))
        return menu, actions

    def _show_plot_context_menu(self, key: str, scene_pos, screen_pos) -> None:
        menu, actions = self._build_plot_context_menu(key, scene_pos)
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is actions["auto_scale"]:
            self._plot_for_key(key).enableAutoRange()
        elif action is actions["data_tip"]:
            self.data_tip_button.setChecked(not self._data_tip_enabled)
        elif action is actions["clear_data_tips"]:
            self._clear_data_tips(key)

    def _attach_plot_context_menu(self, key: str, plot) -> None:
        def _handle_click(event):
            if event.button() != QtCore.Qt.RightButton:
                return
            if not plot.sceneBoundingRect().contains(event.scenePos()):
                return
            event.accept()
            if self._suppress_next_plot_context_menu:
                self._suppress_next_plot_context_menu = False
                return
            self._show_plot_context_menu(key, event.scenePos(), event.screenPos())

        plot.scene().sigMouseClicked.connect(_handle_click)

    def _attach_data_tip_clicks(self, key: str, plot) -> None:
        def _handle_click(event):
            if event.button() != QtCore.Qt.LeftButton:
                return
            if not self._data_tip_enabled:
                return
            if not plot.sceneBoundingRect().contains(event.scenePos()):
                return
            event.accept()
            click_x, click_y = self._scene_to_data_point(key, event.scenePos())
            self._place_data_tip(key, click_x, click_y)

        plot.scene().sigMouseClicked.connect(_handle_click)

    def _show_status(self, message: str) -> None:
        if self._status_callback is not None:
            self._status_callback(message)


class ComboBoxDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self._items = items

    def createEditor(self, parent, _option, _index):
        editor = QtWidgets.QComboBox(parent)
        editor.addItems(self._items)
        return editor

    def setEditorData(self, editor, index) -> None:
        text = str(index.data() or "")
        position = editor.findText(text, QtCore.Qt.MatchFixedString)
        editor.setCurrentIndex(max(position, 0))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText())

    def updateEditorGeometry(self, editor, option, _index) -> None:
        editor.setGeometry(option.rect)


class VnaAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.orientation in {"left", "right"} and hasattr(self, "enableAutoSIPrefix"):
            self.enableAutoSIPrefix(False)

    @staticmethod
    def _format_plain_tick(value: float) -> str:
        if not np.isfinite(value):
            return ""
        abs_value = abs(value)
        if abs_value >= 1000.0 and abs_value < 1_000_000.0:
            scaled = value / 1000.0
            return f"{scaled:g}k"
        if abs_value >= 1_000_000.0:
            scaled = value / 1_000_000.0
            return f"{scaled:g}M"
        if abs_value >= 1.0:
            return f"{value:g}"
        return f"{value:.3g}"

    @staticmethod
    def _format_y_tick(value: float) -> str:
        if not np.isfinite(value):
            return ""
        if value == 0.0:
            return "0"
        abs_value = abs(value)
        if abs_value >= 1.0e4 or abs_value < 1.0e-3:
            return f"{value:.3e}".replace("e+0", "e+").replace("e-0", "e-")
        return f"{value:.6g}"

    def tickStrings(self, values: list[float], scale: float, spacing: float):
        if self.logMode:
            labels: list[str] = []
            for value in values:
                linear_value = (10.0 ** value) * scale
                if linear_value <= 0.0 or not np.isfinite(linear_value):
                    labels.append("")
                    continue
                exponent = np.log10(linear_value)
                labels.append(
                    self._format_plain_tick(linear_value)
                    if abs(exponent - round(exponent)) < 1e-6
                    else ""
                )
            return labels
        if self.orientation in {"left", "right"}:
            return [self._format_y_tick(float(value) * float(scale)) for value in values]
        return super().tickStrings(values, scale, spacing)


class DataTipPoint(pg.ScatterPlotItem):
    def __init__(self, *args, on_drag=None, on_context_menu=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_drag = on_drag
        self._on_context_menu = on_context_menu

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.RightButton and self._on_context_menu is not None:
            ev.accept()
            self._on_context_menu(ev.screenPos())
            return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev) -> None:
        if ev.button() != QtCore.Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        if self._on_drag is not None:
            self._on_drag(ev.scenePos())


class DataTipText(pg.TextItem):
    def __init__(self, *args, on_context_menu=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_context_menu = on_context_menu

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.RightButton and self._on_context_menu is not None:
            ev.accept()
            self._on_context_menu(ev.screenPos())
            return
        super().mouseClickEvent(ev)


class CompactLegendSample(pg.graphicsItems.LegendItem.ItemSample):
    def __init__(self, item):
        super().__init__(item)
        self.setFixedWidth(8)
        self.setFixedHeight(10)

    def boundingRect(self):
        return QtCore.QRectF(0, 0, 8, 10)

    def paint(self, p, *args):
        opts = self.item.opts
        p.setPen(pg.mkPen(opts["pen"]))
        p.drawLine(0, 5, 8, 5)


class VnaViewBox(pg.ViewBox):
    def __init__(self, *args, on_left_drag=None, on_right_drag_zoom=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_left_drag = on_left_drag
        self._on_right_drag_zoom = on_right_drag_zoom
        self._zoom_box = QtWidgets.QGraphicsRectItem()
        self._zoom_box.setPen(pg.mkPen("#5eead4", width=1.6, style=QtCore.Qt.DashLine))
        self._zoom_box.setBrush(pg.mkBrush(94, 234, 212, 48))
        self._zoom_box.setZValue(CURSOR_Z + 5)
        self._zoom_box.setVisible(False)
        self.addItem(self._zoom_box, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None) -> None:
        if ev.button() == QtCore.Qt.LeftButton and self._on_left_drag is not None:
            ev.accept()
            self._on_left_drag(ev.scenePos())
            return
        if ev.button() == QtCore.Qt.RightButton and self._on_right_drag_zoom is not None:
            ev.accept()
            start = self.mapSceneToView(ev.buttonDownScenePos(QtCore.Qt.RightButton))
            stop = self.mapSceneToView(ev.scenePos())
            if ev.isFinish():
                self._zoom_box.setVisible(False)
                self._on_right_drag_zoom(start, stop)
                return
            rect = QtCore.QRectF(start, stop).normalized()
            self._zoom_box.setRect(rect)
            self._zoom_box.setVisible(True)
            return
        super().mouseDragEvent(ev, axis=axis)


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        text = f"{value:.6g}"
        if "e" in text or "E" in text:
            return text
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"


class MCSetupDialog(QtWidgets.QDialog):
    HEADERS = [
        "On/Off",
        "Full Scale",
        "Coupling",
        "Offset",
        "Label",
        "EU/Volt",
        "EU",
        "Per EU",
        "Invert",
        "0 dB Vref",
    ]

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("MC Setup")
        self.setFixedWidth(930)
        self.setStyleSheet(main_window._mc_setup_dialog_stylesheet())
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.table = QtWidgets.QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.table.setItemDelegateForColumn(
            1,
            ComboBoxDelegate(
                [main_window._format_full_scale_option(value) for value in main_window.CHANNEL_FULL_SCALE_OPTIONS] + ["Auto"],
                self.table,
            ),
        )
        self.table.setItemDelegateForColumn(
            2, ComboBoxDelegate(main_window.CHANNEL_COUPLING_OPTIONS, self.table)
        )
        self.table.setItemDelegateForColumn(
            7, ComboBoxDelegate(main_window.CHANNEL_PER_EU_OPTIONS, self.table)
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        for col, width in enumerate((98, 104, 108, 72, 124, 82, 94, 70, 58, 86)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setFixedWidth(902)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)

        bottom = QtWidgets.QHBoxLayout()
        self.set_all_checkbox = QtWidgets.QCheckBox("Set Ch 3 thru Ch 4 =")
        self.copy_source_combo = QtWidgets.QComboBox()
        self.copy_source_combo.addItems(["Ch 1", "Ch 2", "Ch 3", "Ch 4"])
        self.apply_button = QtWidgets.QPushButton("Apply")
        self.undo_button = QtWidgets.QPushButton("Undo")
        self.save_as_button = QtWidgets.QPushButton("Save As")
        self.close_button = QtWidgets.QPushButton("Close")
        bottom.addWidget(self.set_all_checkbox)
        bottom.addWidget(self.copy_source_combo)
        bottom.addStretch(1)
        bottom.addWidget(self.apply_button)
        bottom.addWidget(self.undo_button)
        bottom.addWidget(self.save_as_button)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)

        self.apply_button.clicked.connect(self._apply_to_main)
        self.undo_button.clicked.connect(self.reload_from_main)
        self.save_as_button.clicked.connect(main_window._save_session)
        self.close_button.clicked.connect(self.accept)
        self.reload_from_main()
        self.setFixedHeight(self.sizeHint().height())

    def _fit_table_height(self) -> None:
        header_height = self.table.horizontalHeader().sizeHint().height()
        row_height = sum(self.table.rowHeight(row) for row in range(self.table.rowCount()))
        frame_height = self.table.frameWidth() * 2
        self.table.setFixedHeight(header_height + row_height + frame_height + 10)

    def reload_from_main(self) -> None:
        self.table.blockSignals(True)
        source = self.main_window.channel_table
        self.table.setRowCount(source.rowCount())
        for row in range(source.rowCount()):
            enabled = source.item(row, 0).checkState() == QtCore.Qt.Checked
            full_scale = source.item(row, 9).text()
            unit = source.item(row, 8).text()
            row_values = [
                f"Ch {row + 1}",
                "Auto" if full_scale.startswith("-") else self.main_window._format_full_scale_option(float(full_scale)),
                source.item(row, 6).text(),
                source.item(row, 11).text(),
                source.item(row, 10).text(),
                source.item(row, 7).text(),
                unit,
                source.item(row, 12).text(),
                "",
                source.item(row, 13).text(),
            ]
            for col, value in enumerate(row_values):
                item = self.table.item(row, col)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.table.setItem(row, col, item)
                item.setText(value)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if col == 0:
                    flags = item.flags() | QtCore.Qt.ItemIsUserCheckable
                    flags &= ~QtCore.Qt.ItemIsEditable
                    item.setFlags(flags)
                    item.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
                    if row == 0:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
                        item.setCheckState(QtCore.Qt.Checked)
        self.table.blockSignals(False)
        self._fit_table_height()

    def _item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() == 0:
            self._enforce_channel_enable_rules(item.row())
        self.apply_button.setEnabled(True)

    def _enforce_channel_enable_rules(self, changed_row: int) -> None:
        if self.table.rowCount() == 0:
            return
        self.table.blockSignals(True)
        first_item = self.table.item(0, 0)
        if first_item is not None:
            first_item.setCheckState(QtCore.Qt.Checked)
        if not any(
            self.table.item(row, 0) is not None
            and self.table.item(row, 0).checkState() == QtCore.Qt.Checked
            for row in range(self.table.rowCount())
        ):
            row = max(0, min(changed_row, self.table.rowCount() - 1))
            item = self.table.item(row, 0) or first_item
            if item is not None:
                item.setCheckState(QtCore.Qt.Checked)
        self.table.blockSignals(False)

    def _apply_to_main(self) -> None:
        target = self.main_window.channel_table
        full_scale_changed_aliases: set[str] = set()
        for row in range(min(self.table.rowCount(), target.rowCount())):
            old_aliases = self.main_window._channel_aliases_for_table_row(row)
            old_full_scale = self.main_window._channel_table_full_scale(row)
            enabled_item = self.table.item(row, 0)
            target.item(row, 0).setCheckState(
                QtCore.Qt.Checked
                if enabled_item is not None and enabled_item.checkState() == QtCore.Qt.Checked
                else QtCore.Qt.Unchecked
            )
            full_scale_text = self.table.item(row, 1).text().strip()
            target.item(row, 9).setText(
                f"{self.main_window._parse_full_scale_text(full_scale_text, 10.0):.6g}"
            )
            target.item(row, 6).setText(self.table.item(row, 2).text().strip() or "ac")
            target.item(row, 6).setText(target.item(row, 6).text().strip().lower())
            target.item(row, 11).setText(f"{self.main_window._parse_float(self.table.item(row, 3).text(), 0.0):.6g}")
            target.item(row, 10).setText(self.table.item(row, 4).text().strip())
            target.item(row, 7).setText(f"{self.main_window._parse_float(self.table.item(row, 5).text(), 1.0):.6g}")
            target.item(row, 8).setText(self.table.item(row, 6).text().strip() or "V")
            target.item(row, 12).setText(self.table.item(row, 7).text().strip() or "/Volt")
            target.item(row, 13).setText(f"{self.main_window._parse_float(self.table.item(row, 9).text(), 1.0):.6g}")
            self.main_window._apply_bias_defaults_to_table_row(row)
            new_full_scale = self.main_window._channel_table_full_scale(row)
            if not np.isclose(old_full_scale, new_full_scale, rtol=1e-12, atol=1e-12):
                full_scale_changed_aliases.update(old_aliases)
                full_scale_changed_aliases.update(self.main_window._channel_aliases_for_table_row(row))
        self.main_window._rebuild_channel_list()
        self.main_window._sync_channel_grid()
        self.main_window._reload_channel_selectors(include_new_responses=True)
        self.main_window._load_channel_editor_from_row(self.main_window.channel_list.currentRow())
        self.main_window._read_session_from_widgets()
        self.main_window._refresh_current_measurement_view()
        if full_scale_changed_aliases:
            self.main_window._refresh_full_scale_axis_ranges_for_channels(
                list(full_scale_changed_aliases),
                force_auto_y=True,
            )
        self.main_window.statusBar().showMessage("MC Setup applied")


class AcquisitionWorker(QtCore.QObject):
    started = QtCore.Signal(object)
    measurement_ready = QtCore.Signal(object)
    status_changed = QtCore.Signal(str)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(
        self,
        controller: VnaController,
        device_name: str | None,
        display_interval_seconds: float = 0.25,
        average_run: bool = False,
        target_average_count: int | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._device_name = device_name
        self._display_interval_seconds = display_interval_seconds
        self._average_run = average_run
        self._target_average_count = target_average_count
        self._stop_requested = threading.Event()
        self._backend_started = threading.Event()

    @QtCore.Slot()
    def run(self) -> None:
        last_emit = 0.0
        latest_measurement = None
        emitted_latest = False
        stop_needed = False
        try:
            self.status_changed.emit("State: starting")
            self._controller.set_averaging_enabled(self._average_run)
            self._controller.configure(device_name=self._device_name)
            self._set_controller_stop_event(self._stop_requested)
            if self._stop_requested.is_set():
                self._request_controller_stop()
                return
            self._controller.start()
            self._backend_started.set()
            if self._stop_requested.is_set():
                self._request_controller_stop()
                return
            self.started.emit(self._device_name)
            self.status_changed.emit("State: running")
            while not self._stop_requested.is_set():
                measurement = self._controller.read_and_process()
                latest_measurement = measurement
                now = time.monotonic()
                if now - last_emit >= self._display_interval_seconds:
                    self.measurement_ready.emit(measurement)
                    last_emit = now
                    emitted_latest = True
                else:
                    emitted_latest = False
                if (
                    self._average_run
                    and self._target_average_count is not None
                    and measurement.metadata.get("average_count", 0) >= self._target_average_count
                ):
                    stop_needed = True
                    break
            if latest_measurement is not None and not emitted_latest and not self._stop_requested.is_set():
                self.measurement_ready.emit(latest_measurement)
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.error.emit(str(exc))
        finally:
            user_requested_stop = self._stop_requested.is_set()
            try:
                self._controller.stop()
            except Exception:
                pass
            if not user_requested_stop:
                try:
                    self._controller.close()
                except Exception:
                    pass
            try:
                self._set_controller_stop_event(None)
            except Exception:
                pass
            self._controller.set_averaging_enabled(False)
            self.finished.emit()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_requested

    def _request_controller_stop(self) -> None:
        try:
            self._controller.request_stop()
        except Exception:
            pass

    def _set_controller_stop_event(self, stop_event: threading.Event | None) -> None:
        setter = getattr(self._controller, "set_stop_event", None)
        if setter is None:
            return
        try:
            setter(stop_event)
        except Exception:
            pass


class ContinuousRecordingWorker(QtCore.QObject):
    started = QtCore.Signal(object)
    preview_ready = QtCore.Signal(object)
    status_changed = QtCore.Signal(str)
    recording_status = QtCore.Signal(object)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(
        self,
        controller: VnaController,
        device_name: str | None,
        output_dir: str | Path,
        preview_interval_seconds: float = 1.0,
        segment_seconds: float = 600.0,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._device_name = device_name
        self._output_dir = Path(output_dir)
        self._preview_interval_seconds = float(preview_interval_seconds)
        self._segment_seconds = float(segment_seconds)
        self._stop_requested = threading.Event()
        self._backend_started = threading.Event()
        self._writer: ContinuousDatWriter | None = None

    @QtCore.Slot()
    def run(self) -> None:
        last_preview = 0.0
        completed = False
        error_message: str | None = None
        try:
            self.status_changed.emit("State: preparing record")
            self._controller.set_averaging_enabled(False)
            self._controller.configure(device_name=self._device_name)
            self._set_controller_stop_event(self._stop_requested)
            if self._stop_requested.is_set():
                self._request_controller_stop()
                return
            channel_names = [
                channel.name
                for channel in self._controller.state.session.ai_channels
                if channel.enabled
            ]
            self._writer = ContinuousDatWriter(
                self._output_dir,
                self._controller.state.session,
                device_name=self._device_name,
                channel_names=channel_names,
                software_version=PYTHON_VNA_VERSION,
                segment_seconds=self._segment_seconds,
            )
            self._writer.start()
            self._controller.start()
            self._backend_started.set()
            if self._stop_requested.is_set():
                self._request_controller_stop()
                completed = True
                return
            self.started.emit(self._device_name)
            self.status_changed.emit("State: recording")
            while not self._stop_requested.is_set():
                frame = self._controller.backend.read_frame()
                if not self._writer.channel_names and frame.channel_names:
                    self._writer.channel_names = list(frame.channel_names)
                status = self._writer.write_frame(frame)
                self.recording_status.emit(status)
                now = time.monotonic()
                if now - last_preview >= self._preview_interval_seconds:
                    self.preview_ready.emit(self._preview_measurement(frame, status))
                    last_preview = now
            completed = True
        except Exception as exc:
            if self._stop_requested.is_set():
                completed = True
                error_message = None
            else:
                error_message = str(exc)
                self.error.emit(error_message)
        finally:
            user_requested_stop = self._stop_requested.is_set()
            try:
                self._controller.stop()
            except Exception:
                pass
            if not user_requested_stop:
                try:
                    self._controller.close()
                except Exception:
                    pass
            try:
                if self._writer is not None:
                    self._writer.close(completed=completed, error=error_message)
            except Exception as exc:
                if not self._stop_requested.is_set():
                    self.error.emit(f"Recording close failed: {exc}")
            try:
                self._set_controller_stop_event(None)
            except Exception:
                pass
            self._controller.set_averaging_enabled(False)
            self.finished.emit()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_requested

    def _request_controller_stop(self) -> None:
        try:
            self._controller.request_stop()
        except Exception:
            pass

    def _set_controller_stop_event(self, stop_event: threading.Event | None) -> None:
        setter = getattr(self._controller, "set_stop_event", None)
        if setter is None:
            return
        try:
            setter(stop_event)
        except Exception:
            pass

    @staticmethod
    def _preview_measurement(frame, status: RecordingStatus) -> MeasurementSet:
        return MeasurementSet(
            sample_rate=frame.sample_rate,
            time_data={
                "t": frame.timestamps,
                "channels": {
                    name: frame.data[index].copy()
                    for index, name in enumerate(frame.channel_names)
                },
            },
            spectra={"f": np.array([], dtype=float), "fft": {}, "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "frame_index": frame.frame_index,
                "recording_preview": True,
                "recording_elapsed_seconds": status.elapsed_seconds,
                "recording_total_samples": status.total_samples,
                "recording_segment_index": status.segment_index,
                "recording_output_dir": str(status.output_dir),
            },
        )


class MainWindow(QtWidgets.QMainWindow):
    DEFAULT_IEPE_CURRENT_MA = 2.1
    DEFAULT_EU_PER_VOLT = 1.0
    DEFAULT_ENGINEERING_UNIT = "m/s^2"
    CHANNEL_COUPLING_OPTIONS = ["ac", "dc", "bias"]
    CHANNEL_PER_EU_OPTIONS = ["Off", "/Volt", "/mV", "/uV", "/kV"]
    PROCESSING_WINDOW_LABELS = {
        "boxcar": "Boxcar",
        "hanning": "Hanning",
        "flattop": "FlatTop",
        "flat301": "Flat301",
        "flat201": "Flat201",
        "potter210": "Potter210",
        "potter310": "Potter310",
        "hamming": "Hamming",
        "blackman": "Blackman",
        "exact_blackman": "Exact-Bl",
        "blackman_harris_61": "BHarris61",
        "blackman_harris_67": "BHarris67",
        "blackman_harris_74": "BHarris74",
        "blackman_harris_92": "BHarris92",
        "modal_box_exp_0_1": "Box_Exp.1",
        "modal_box_exp_0_01": "Box_Exp.01",
        "modal_force20_exp_0_1": "F20_Exp.1",
        "modal_force20_exp_0_01": "F20_Exp.01",
        "modal_user": "User Modal",
    }
    BANDWIDTH_OPTIONS_HZ = {
        "BW=0.64KHz": 640.0,
        "BW=1.0KHz": 1000.0,
        "BW=2.0KHz": 2000.0,
        "BW=5.0KHz": 5000.0,
        "BW=10.0KHz": 10000.0,
    }
    TRIGGER_MODE_OPTIONS = [
        "Off (Free Run)",
        "Every Frame",
        "1st Frame",
        "Manual Arm",
        "1st-Manual Arm",
    ]

    @classmethod
    def _processing_window_label(cls, value: str) -> str:
        return cls.PROCESSING_WINDOW_LABELS.get(value, "Boxcar")

    @classmethod
    def _processing_window_value(cls, label: str) -> str:
        normalized = label.strip().lower()
        for value, known_label in cls.PROCESSING_WINDOW_LABELS.items():
            if known_label.lower() == normalized:
                return value
        return "boxcar"
    TRIGGER_MODE_LABELS = {
        "Off (Free Run)": "Free Run",
        "Every Frame": "Every Frame",
        "1st Frame": "1st Frame",
        "Manual Arm": "Manual Arm",
        "1st-Manual Arm": "1st-Manual",
    }
    TRIGGER_LEVEL_PERCENT_VALUES = [
        round(level * 100.0 * np.sqrt(2.0) / (32.0 / 2.0), 6)
        for level in range(
            round(0.7 * 32.0 / (2.0 * np.sqrt(2.0))),
            -round(0.7 * 32.0 / (2.0 * np.sqrt(2.0))) - 1,
            -1,
        )
    ]
    TRIGGER_LEVEL_PERCENT_OPTIONS = [
        f"{int(round(value))}%" for value in TRIGGER_LEVEL_PERCENT_VALUES
    ]
    TRACE_COLORS = [
        "#56c7ff",
        "#ffd166",
        "#45e6a8",
        "#ff6b8a",
        "#b992ff",
        "#ff9f43",
        "#9be15d",
        "#f7a8ff",
    ]
    UI_THEMES = {
        "dark": {
            "window_bg": "#121822",
            "panel_bg": "#172231",
            "panel_bg_alt": "#1b2533",
            "cell_bg": "#203247",
            "plot_bg": "#000000",
            "plot_workspace_bg": "#121822",
            "menu_bg": "#0b111a",
            "text": "#eef6ff",
            "muted_text": "#a9bed4",
            "label_text": "#d9f7ff",
            "axis": "#ffffff",
            "accent": "#2563eb",
            "accent_hover": "#1d4ed8",
            "accent_alt": "#5eead4",
            "border": "#334155",
            "control_border": "#3a4a60",
            "table_bg": "#0f141c",
            "disabled_bg": "#1b2531",
            "disabled_text": "#64748b",
            "danger": "#dc2626",
            "danger_hover": "#b91c1c",
            "legend_bg": (12, 18, 28, 215),
            "legend_text": "#f6f1df",
            "grid_alpha": 0.35,
        },
        "light": {
            "window_bg": "#f4f7fb",
            "panel_bg": "#ffffff",
            "panel_bg_alt": "#edf3fa",
            "cell_bg": "#e5eef8",
            "plot_bg": "#ffffff",
            "plot_workspace_bg": "#eaf1f8",
            "menu_bg": "#f7fbff",
            "text": "#102033",
            "muted_text": "#395268",
            "label_text": "#17324d",
            "axis": "#172033",
            "accent": "#1d72c9",
            "accent_hover": "#145da8",
            "accent_alt": "#0f9f8f",
            "border": "#b8c6d8",
            "control_border": "#95a9bf",
            "table_bg": "#ffffff",
            "disabled_bg": "#dce5ef",
            "disabled_text": "#7b8794",
            "danger": "#d13f37",
            "danger_hover": "#b52f29",
            "legend_bg": (255, 255, 255, 230),
            "legend_text": "#102033",
            "grid_alpha": 0.22,
        },
    }

    CHANNEL_FULL_SCALE_OPTIONS = [
        10.0,
        5.0,
        2.5,
        1.25,
        0.625,
        0.3125,
        0.15625,
        0.078125,
        0.0390625,
        0.01953125,
    ]
    DISPLAY_MODE_ITEMS = [
        ("y(t)", "time"),
        ("aspec", "autospectrum"),
        ("xfer", "frf"),
        ("coh", "coherence"),
        ("cspec", "cross_spectrum"),
        ("acor", "auto_correlation"),
        ("ccor", "cross_correlation"),
        ("impulse", "impulse_response"),
        ("fft", "fft"),
    ]
    VALUE_MODE_ITEMS = {
        "time": [("real", "real"), ("mag", "mag"), ("imag", "imag")],
        "autospectrum": [
            ("dB rms", "dB"),
            ("dB rms/rt(Hz)", "dB_per_sqrt_hz"),
            ("rms", "linear"),
            ("rms^2", "power"),
            ("rms/rt(Hz)", "linear_per_sqrt_hz"),
            ("rms^2/Hz", "power_per_hz"),
            ("pk", "pk"),
            ("p-p", "p2p"),
            ("Log rms", "log_linear"),
            ("Log rms^2", "log_power"),
            ("Log rms/rt(Hz)", "log_linear_per_sqrt_hz"),
            ("Log rms^2/Hz", "log_power_per_hz"),
            ("Log pk", "log_pk"),
            ("Log p-p", "log_p2p"),
        ],
        "frf": [
            ("real", "real"),
            ("mag", "mag"),
            ("imag", "imag"),
            ("dB", "dB"),
            ("log mag", "log_mag"),
            ("phase", "phase"),
            ("phase u", "phase_u"),
            ("nyquist", "nyquist"),
        ],
        "coherence": [("mag", "mag")],
        "cross_spectrum": [
            ("real", "real"),
            ("mag", "mag"),
            ("imag", "imag"),
            ("dB", "dB"),
            ("log mag", "log_mag"),
            ("phase", "phase"),
            ("phase u", "phase_u"),
            ("nyquist", "nyquist"),
        ],
        "auto_correlation": [("real", "real"), ("mag", "mag"), ("imag", "imag")],
        "cross_correlation": [("real", "real"), ("mag", "mag"), ("imag", "imag")],
        "impulse_response": [("real", "real"), ("mag", "mag"), ("imag", "imag")],
        "fft": [("real", "real"), ("mag", "mag"), ("imag", "imag"), ("dB", "dB")],
    }

    def __init__(self, controller: VnaController, session: SessionConfig | None = None):
        super().__init__()
        icon_path = resource_path("assets/python_vna_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.controller = controller
        self.session = session or default_session_config()
        self._last_plot_cache: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        self._stored_overlays: dict[str, deque[dict[str, tuple[np.ndarray, np.ndarray]]]] = {
            "top": deque(maxlen=6),
            "bottom": deque(maxlen=6),
        }
        self._preferred_trace_checks: dict[str, set[str] | None] = {"top": None, "bottom": None}
        self._trace_checks_user_modified: dict[str, bool] = {"top": False, "bottom": False}
        self._cursor_enabled = True
        self._markers_enabled = False
        self._manual_x_ranges: dict[str, tuple[float, float] | None] = {
            "top": None,
            "bottom": None,
        }
        self._manual_y_ranges: dict[str, tuple[float, float] | None] = {
            "top": None,
            "bottom": None,
        }
        self._marker_positions: dict[str, list[float | None]] = {
            "top": [None, None],
            "bottom": [None, None],
        }
        self._mark_enabled: dict[str, bool] = {"top": False, "bottom": False}
        self._cursor_positions: dict[str, tuple[float, float] | None] = {
            "top": None,
            "bottom": None,
        }
        self._data_tip_enabled = False
        self._data_tip_items: dict[str, list[dict[str, object]]] = {"top": [], "bottom": []}
        self._suppress_next_plot_context_menu = False
        self._marker_next_index: dict[str, int] = {"top": 0, "bottom": 0}
        self._active_marker_index: dict[str, int] = {"top": 0, "bottom": 0}
        self._marker_lines: dict[str, list[pg.InfiniteLine]] = {"top": [], "bottom": []}
        self._marker_points: dict[str, list[pg.ScatterPlotItem]] = {"top": [], "bottom": []}
        self._marker_texts: dict[str, list[pg.TextItem]] = {"top": [], "bottom": []}
        self._cursor_lines: dict[str, pg.InfiniteLine | None] = {"top": None, "bottom": None}
        self._cursor_points: dict[str, pg.ScatterPlotItem | None] = {"top": None, "bottom": None}
        self._cursor_texts: dict[str, pg.TextItem | None] = {"top": None, "bottom": None}
        self._marker_history_points: dict[str, list[pg.ScatterPlotItem]] = {"top": [], "bottom": []}
        self._marker_history_texts: dict[str, list[pg.TextItem]] = {"top": [], "bottom": []}
        self._marker_dragging_key: str | None = None
        self._active_trace_names: dict[str, str | None] = {"top": None, "bottom": None}
        self._plot_curve_items: dict[str, dict[str, object]] = {"top": {}, "bottom": {}}
        self._plot_curve_colors: dict[str, dict[str, str]] = {"top": {}, "bottom": {}}
        self._axis_scaling_key: str | None = None
        self._axis_history_suspended = False
        self._axis_range_history: dict[str, deque[tuple[tuple[float, float], tuple[float, float]]]] = {
            "top": deque(maxlen=5),
            "bottom": deque(maxlen=5),
        }
        self._last_view_ranges: dict[str, tuple[tuple[float, float], tuple[float, float]] | None] = {
            "top": None,
            "bottom": None,
        }
        self._auto_y_follow_visible_x: dict[str, bool] = {"top": True, "bottom": True}
        self._channel_full_scale_focus: dict[str, str | None] = {"top": None, "bottom": None}
        self._channel_editor_loading = False
        self._controls_visible = True
        self._controls_last_sizes = [320, 880]
        self._last_vna_directory = Path.cwd()
        self._current_source_path: Path | None = None
        self._ui_settings_path = self._default_ui_settings_path()
        self._theme_name = self._load_theme_preference()
        self._detached_plot_window = DetachedPlotWindow(self, self._show_status_message)
        self.top_display_strip_combo = QtWidgets.QComboBox()
        self.bottom_display_strip_combo = QtWidgets.QComboBox()
        self.top_value_strip_combo = QtWidgets.QComboBox()
        self.bottom_value_strip_combo = QtWidgets.QComboBox()
        self.top_trace_strip_combo = QtWidgets.QComboBox()
        self.bottom_trace_strip_combo = QtWidgets.QComboBox()
        self._acquisition_thread: QtCore.QThread | None = None
        self._acquisition_worker: AcquisitionWorker | None = None
        self._acquisition_stop_event: threading.Event | None = None
        self._recording_thread: QtCore.QThread | None = None
        self._recording_worker: ContinuousRecordingWorker | None = None
        self._recording_stop_event: threading.Event | None = None
        self._recording_output_dir: Path | None = None
        self._analysis_viewer = None
        self._pending_measurement = None
        self._plot_update_scheduled = False
        self._stop_requested_for_current_run = False
        self._build_ui()
        self._build_menus()
        self._apply_legacy_theme()
        self._refresh_toolbar_size()
        self._load_session_to_widgets()
        self._update_window_title()
        self._refresh_devices()

    def _show_status_message(self, message: str) -> None:
        self.statusBar().showMessage(message)

    @classmethod
    def _default_ui_settings_path(cls) -> Path:
        override = os.environ.get("PYTHON_VNA_UI_SETTINGS")
        if override:
            return Path(override)
        return Path.home() / ".python_vna" / "ui_settings.json"

    def _load_theme_preference(self) -> str:
        try:
            payload = json.loads(self._ui_settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "dark"
        theme = str(payload.get("theme", "dark")).lower() if isinstance(payload, dict) else "dark"
        return theme if theme in self.UI_THEMES else "dark"

    def _save_theme_preference(self) -> None:
        try:
            self._ui_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._ui_settings_path.write_text(
                json.dumps({"theme": self._theme_name}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            self.statusBar().showMessage("Theme preference could not be saved")

    def _theme(self) -> dict[str, object]:
        return self.UI_THEMES.get(self._theme_name, self.UI_THEMES["dark"])

    @staticmethod
    def _theme_stylesheet(template: str, theme: dict[str, object]) -> str:
        stylesheet = template
        for key, value in theme.items():
            if isinstance(value, tuple):
                continue
            stylesheet = stylesheet.replace(f"@{key}@", str(value))
        return stylesheet

    def _set_theme(self, theme_name: str, persist: bool = True) -> None:
        normalized = theme_name if theme_name in self.UI_THEMES else "dark"
        self._theme_name = normalized
        self._apply_legacy_theme()
        if hasattr(self, "light_theme_action"):
            self.light_theme_action.blockSignals(True)
            self.light_theme_action.setChecked(normalized == "light")
            self.light_theme_action.blockSignals(False)
        if persist:
            self._save_theme_preference()
        self.statusBar().showMessage(f"{normalized.title()} theme active")

    def _toggle_light_theme(self, enabled: bool) -> None:
        self._set_theme("light" if enabled else "dark")

    def _apply_legacy_theme(self) -> None:
        theme = self._theme()
        self.setStyleSheet(
            self._theme_stylesheet(
            """
            QMainWindow, QWidget {
                background: @window_bg@;
                color: @text@;
                font-family: "Microsoft Sans Serif", "Noto Sans SC", "Segoe UI", Arial;
            }
            QMenuBar {
                background: @menu_bg@;
                color: @text@;
                border-bottom: 1px solid @border@;
                padding: 2px;
            }
            QMenuBar::item:selected {
                background: @accent@;
                color: #ffffff;
                border-radius: 5px;
            }
            QMenu {
                background: @menu_bg@;
                color: @text@;
                border: 1px solid @border@;
                border-radius: 7px;
                padding: 4px;
            }
            QMenu::item:selected {
                background: @accent@;
                color: #ffffff;
                border-radius: 5px;
            }
            QTabWidget::pane {
                border: 1px solid @border@;
                background: @window_bg@;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: @panel_bg_alt@;
                color: @label_text@;
                font-weight: bold;
                padding: 6px 12px;
                border: 1px solid @border@;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                background: @accent@;
                color: #ffffff;
            }
            QGroupBox {
                background: @panel_bg@;
                color: @text@;
                font-weight: bold;
                border: 1px solid @border@;
                border-radius: 10px;
                margin-top: 18px;
                padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 3px 10px;
                background: @accent@;
                color: #ffffff;
                font-weight: bold;
                border-radius: 6px;
            }
            QLabel, QCheckBox {
                color: @muted_text@;
                font-weight: bold;
            }
            QCheckBox:enabled {
                color: @text@;
            }
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: @panel_bg_alt@;
                color: @text@;
                selection-background-color: @accent@;
                selection-color: #ffffff;
                border: 1px solid @control_border@;
                border-radius: 7px;
                padding: 3px 6px;
                min-height: 22px;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid @accent_alt@;
            }
            QPushButton, QToolButton {
                background: @accent@;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid @accent_hover@;
                border-radius: 8px;
                padding: 3px 9px;
            }
            QPushButton:hover, QToolButton:hover {
                background: @accent_hover@;
                border-color: @accent@;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: @accent_hover@;
            }
            QPushButton:checked, QToolButton:checked {
                background: @accent_alt@;
                border-color: @accent_alt@;
            }
            QPushButton#dangerButton:enabled {
                background: @danger@;
                border-color: @danger_hover@;
            }
            QPushButton#dangerButton:hover:enabled {
                background: @danger_hover@;
            }
            QPushButton:disabled, QToolButton:disabled {
                background: @disabled_bg@;
                color: @disabled_text@;
                border-color: @border@;
            }
            QTableWidget {
                background: @table_bg@;
                color: @text@;
                gridline-color: @border@;
                border: 1px solid @border@;
                border-radius: 8px;
            }
            QHeaderView::section {
                background: @cell_bg@;
                color: @label_text@;
                font-weight: bold;
                padding: 4px;
                border: 1px solid @border@;
            }
            QTableWidget::item {
                background: @panel_bg@;
                color: @text@;
                padding: 2px;
            }
            QScrollArea {
                background: @window_bg@;
                border: none;
            }
            QStatusBar {
                background: @menu_bg@;
                color: @label_text@;
                border-top: 1px solid @border@;
            }
            """,
            theme,
            )
        )
        self._apply_theme_to_child_widgets()

    def _apply_theme_to_child_widgets(self) -> None:
        theme = self._theme()
        if hasattr(self, "left_panel"):
            self.left_panel.setStyleSheet(self._legacy_left_panel_stylesheet(theme))
        if hasattr(self, "right_panel"):
            self.right_panel.setStyleSheet(self._plot_workspace_stylesheet(theme))
        if hasattr(self, "toolbar_container"):
            self.toolbar_container.setStyleSheet(self._toolbar_stylesheet(theme))
        for panel_name in ("upper_axis_panel", "lower_axis_panel"):
            panel = getattr(self, panel_name, None)
            if panel is not None:
                panel.setStyleSheet(self._axis_control_panel_stylesheet(theme))
        for trace_list in (
            getattr(self, "top_trace_list", None),
            getattr(self, "bottom_trace_list", None),
        ):
            if trace_list is not None:
                trace_list.setStyleSheet(self._trace_list_stylesheet(theme))
        trigger_panel = getattr(self, "trigger_panel", None)
        if trigger_panel is not None:
            trigger_panel.setStyleSheet(self._legacy_trigger_panel_stylesheet(theme))
        for plot in (getattr(self, "top_plot", None), getattr(self, "bottom_plot", None)):
            if plot is not None:
                self._apply_plot_theme(plot, theme)
        if hasattr(self, "_cursor_lines"):
            self._apply_cursor_theme()
        detached = getattr(self, "_detached_plot_window", None)
        if detached is not None:
            detached.apply_theme(theme)
        analysis_viewer = getattr(self, "_analysis_viewer", None)
        if analysis_viewer is not None:
            analysis_viewer.apply_theme(theme)

    @staticmethod
    def _plot_workspace_stylesheet(theme: dict[str, object]) -> str:
        return (
            f"#plotWorkspace {{ background: {theme['plot_workspace_bg']}; }} "
            f"#plotWorkspace QLabel {{ color: {theme['label_text']}; }}"
        )

    @staticmethod
    def _toolbar_stylesheet(theme: dict[str, object]) -> str:
        return (
            f"#topToolbar {{ background: {theme['window_bg']}; }} "
            f"#topToolbar QLabel {{ color: {theme['label_text']}; font-weight: bold; }} "
            "#topToolbar QPushButton, #topToolbar QToolButton { border-radius: 7px; min-height: 22px; font-size: 8pt; }"
        )

    @staticmethod
    def _trace_list_stylesheet(theme: dict[str, object]) -> str:
        return (
            f"QListWidget {{ background: {theme['table_bg']}; color: {theme['label_text']}; font-weight: bold; "
            f"border: 1px solid {theme['border']}; border-radius: 7px; }} "
            "QListWidget::item { min-height: 23px; padding: 0px 1px; } "
            f"QListWidget::item:selected {{ background: {theme['cell_bg']}; color: {theme['text']}; }} "
            "QListWidget::indicator { width: 16px; height: 16px; } "
            f"QListWidget::indicator:unchecked {{ border: 2px solid {theme['control_border']}; background: {theme['menu_bg']}; border-radius: 3px; }} "
            f"QListWidget::indicator:checked {{ border: 2px solid {theme['axis']}; background: {theme['accent_alt']}; border-radius: 3px; }}"
        )

    @staticmethod
    def _axis_control_panel_stylesheet(theme: dict[str, object]) -> str:
        return (
            f"QWidget {{ background: {theme['window_bg']}; }} "
            f"QComboBox {{ background: {theme['panel_bg_alt']}; color: {theme['text']}; padding: 2px 18px 2px 5px; "
            f"border: 1px solid {theme['control_border']}; border-radius: 6px; font-size: 8pt; }} "
            f"QComboBox:focus {{ border: 1px solid {theme['accent_alt']}; }} "
            f"QLabel#vnaControlLabel {{ background: {theme['cell_bg']}; color: {theme['label_text']}; padding: 3px; font-weight: bold; border-radius: 5px; font-size: 8pt; }} "
            f"QLabel#vnaPanelTitle {{ color: {theme['accent']}; font-weight: bold; font-size: 8pt; }} "
            f"QLabel#vnaMiniLabel {{ color: {theme['text']}; background: {theme['panel_bg_alt']}; padding: 3px; font-weight: bold; border-radius: 5px; font-size: 8pt; }}"
        )

    @staticmethod
    def _apply_plot_theme(plot, theme: dict[str, object]) -> None:
        plot.setBackground(str(theme["plot_bg"]))
        plot.showGrid(x=True, y=True, alpha=float(theme["grid_alpha"]))
        legend = plot.plotItem.legend
        if legend is not None:
            legend.setBrush(pg.mkBrush(*theme["legend_bg"]))
            legend.setPen(pg.mkPen(str(theme["legend_text"]), width=0.8))
            legend.opts["labelTextColor"] = str(theme["legend_text"])
            for _sample, label in legend.items:
                label.setText(label.text, color=str(theme["legend_text"]))
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(str(theme["axis"])))
            axis.setTextPen(pg.mkPen(str(theme["axis"])))
        plot.getPlotItem().titleLabel.item.setDefaultTextColor(QtGui.QColor(str(theme["axis"])))

    def _update_window_title(self) -> None:
        title = "VNA - USB-4431"
        if self._current_source_path is not None:
            title = f"{title} - {self._current_source_path.name}"
        self.setWindowTitle(title)

    def _screen_available_geometry(self) -> QtCore.QRect:
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return QtCore.QRect(0, 0, 1180, 760)
        return screen.availableGeometry()

    def _apply_adaptive_window_size(self) -> None:
        available = self._screen_available_geometry()
        available_width = max(640, int(available.width()))
        available_height = max(480, int(available.height()))
        target_width = min(1180, max(720, int(available_width * 0.92)))
        target_height = min(760, max(520, int(available_height * 0.88)))
        minimum_width = min(target_width, max(700, int(available_width * 0.74)))
        minimum_height = min(target_height, max(460, int(available_height * 0.70)))
        self._adaptive_plot_min_height = max(150, min(240, int(target_height * 0.27)))
        self.setMinimumSize(minimum_width, minimum_height)
        self.resize(target_width, target_height)
        self.move(
            available.x() + max(0, (available_width - target_width) // 2),
            available.y() + max(0, (available_height - target_height) // 2),
        )

    def _build_ui(self) -> None:
        self.setWindowTitle("VNA - USB-4431")
        self._apply_adaptive_window_size()

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, central)
        layout.addWidget(self.main_splitter)

        self._build_session_tab()
        self.left_panel = self._build_legacy_left_panel()
        self.left_panel.setMinimumWidth(360)
        self.left_panel.setMaximumWidth(430)
        self.main_splitter.addWidget(self.left_panel)

        right_panel = QtWidgets.QWidget()
        self.right_panel = right_panel
        right_panel.setObjectName("plotWorkspace")
        right_panel.setStyleSheet(self._plot_workspace_stylesheet(self._theme()))
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._build_toolbar())
        self._build_top_control_strip()

        self.top_plot = self._create_vna_plot("Upper", "top")
        self.bottom_plot = self._create_vna_plot("Lower", "bottom")

        plot_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, right_panel)
        plot_splitter.setChildrenCollapsible(False)
        plot_splitter.addWidget(self._build_plot_section("Upper", self.top_plot))
        plot_splitter.addWidget(self._build_plot_section("Lower", self.bottom_plot))
        plot_splitter.setStretchFactor(0, 1)
        plot_splitter.setStretchFactor(1, 1)
        plot_splitter.setSizes([340, 340])
        right_layout.addWidget(plot_splitter, 1)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes(self._controls_last_sizes)

        self._create_marker_lines(self.top_plot, "top")
        self._create_marker_lines(self.bottom_plot, "bottom")
        self._create_marker_points(self.top_plot, "top")
        self._create_marker_points(self.bottom_plot, "bottom")
        self._create_cursor_items(self.top_plot, "top")
        self._create_cursor_items(self.bottom_plot, "bottom")
        self._attach_cursor_tracking(self.top_plot, "top")
        self._attach_cursor_tracking(self.bottom_plot, "bottom")
        self._attach_marker_tracking(self.top_plot, "top")
        self._attach_marker_tracking(self.bottom_plot, "bottom")
        self._attach_axis_history(self.top_plot, "top")
        self._attach_axis_history(self.bottom_plot, "bottom")
        self._attach_dynamic_y_scaling(self.top_plot, "top")
        self._attach_dynamic_y_scaling(self.bottom_plot, "bottom")
        self.statusBar().showMessage("Ready")

    def _create_vna_plot(self, title: str, key: str):
        view_box = VnaViewBox(
            on_left_drag=lambda scene_pos, plot_key=key: self._move_cursor_from_scene_pos(plot_key, scene_pos),
            on_right_drag_zoom=lambda start, stop, plot_key=key: self._zoom_plot_to_view_rect(
                plot_key,
                start,
                stop,
            ),
        )
        plot_item = pg.PlotItem(
            title=title,
            viewBox=view_box,
            axisItems={
                "bottom": VnaAxisItem(orientation="bottom"),
                "left": VnaAxisItem(orientation="left"),
            },
        )
        plot = pg.PlotWidget(plotItem=plot_item)
        theme = self._theme()
        legend = plot.addLegend(
            offset=(3, 2),
            brush=pg.mkBrush(*theme["legend_bg"]),
            pen=pg.mkPen(str(theme["legend_text"]), width=0.8),
            labelTextColor=str(theme["legend_text"]),
            labelTextSize="6pt",
            colCount=8,
            horSpacing=0,
            verSpacing=-6,
            sampleType=CompactLegendSample,
        )
        legend.setZValue(LEGEND_Z)
        plot.setBackground(str(theme["plot_bg"]))
        plot.showGrid(x=True, y=True, alpha=float(theme["grid_alpha"]))
        plot.setMinimumHeight(getattr(self, "_adaptive_plot_min_height", 220))
        plot.setDownsampling(auto=True, mode="peak")
        plot.setClipToView(True)
        plot.getPlotItem().setMenuEnabled(False)
        plot.getPlotItem().titleLabel.item.setDefaultTextColor(QtGui.QColor(str(theme["axis"])))
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(str(theme["axis"])))
            axis.setTextPen(pg.mkPen(str(theme["axis"])))
            axis.setStyle(
                hideOverlappingLabels=True,
                autoExpandTextSpace=True,
                tickTextOffset=6,
            )
        plot.getAxis("bottom").setStyle(tickTextWidth=56)
        return plot

    def _build_plot_section(self, title: str, plot) -> QtWidgets.QWidget:
        section = QtWidgets.QWidget()
        section.setObjectName(f"{title.lower()}PlotSection")
        layout = QtWidgets.QHBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if title == "Upper":
            controls = self._build_axis_control_panel(
                title,
                self.top_display_strip_combo,
                self.top_value_strip_combo,
                self.top_trace_strip_combo,
                self.top_xscale_combo,
            )
        else:
            controls = self._build_axis_control_panel(
                title,
                self.bottom_display_strip_combo,
                self.bottom_value_strip_combo,
                self.bottom_trace_strip_combo,
                self.bottom_xscale_combo,
            )
        layout.addWidget(controls)
        layout.addWidget(plot, 1)
        return section

    def _build_axis_control_panel(
        self,
        title: str,
        display_combo: QtWidgets.QComboBox,
        value_combo: QtWidgets.QComboBox,
        trace_combo: QtWidgets.QComboBox,
        xscale_combo: QtWidgets.QComboBox,
    ) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        if title == "Upper":
            self.upper_axis_panel = panel
        else:
            self.lower_axis_panel = panel
        panel.setObjectName(f"{title.lower()}AxisPanel")
        panel.setFixedWidth(176)
        panel.setStyleSheet(self._axis_control_panel_stylesheet(self._theme()))
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)

        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("vnaPanelTitle")
        title_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title_label)

        layout.addWidget(self._combo_row("x:", xscale_combo))
        layout.addWidget(self._combo_row("y:", display_combo))
        self._style_vna_control_combo(value_combo)
        layout.addWidget(value_combo)
        layout.addWidget(self._combo_row("active", trace_combo))
        chan_label = QtWidgets.QLabel("chan sel")
        chan_label.setObjectName("vnaControlLabel")
        layout.addWidget(chan_label)
        trace_list = self.top_trace_list if title == "Upper" else self.bottom_trace_list
        layout.addWidget(trace_list)
        layout.addStretch(1)
        return panel

    def _combo_row(self, label_text: str, combo: QtWidgets.QComboBox) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QtWidgets.QLabel(label_text)
        label.setObjectName("vnaControlLabel")
        label.setMinimumWidth(26 if len(label_text) <= 2 else 46)
        self._style_vna_control_combo(combo)
        layout.addWidget(label)
        layout.addWidget(combo, 1)
        return row

    @staticmethod
    def _style_vna_control_combo(combo: QtWidgets.QComboBox) -> None:
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)

    @staticmethod
    def _make_compact_combo(combo: QtWidgets.QComboBox, contents_length: int = 10) -> None:
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(contents_length)
        font = combo.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        combo.setFont(font)

    def _build_axis_control_strip(
        self,
        title: str,
        display_combo: QtWidgets.QComboBox,
        value_combo: QtWidgets.QComboBox,
        trace_combo: QtWidgets.QComboBox,
    ) -> QtWidgets.QHBoxLayout:
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title_label = QtWidgets.QLabel(title)
        title_label.setMinimumWidth(48)
        for combo in (display_combo, value_combo, trace_combo):
            combo.setMinimumWidth(96)
        layout.addWidget(title_label)
        layout.addWidget(display_combo)
        layout.addWidget(value_combo)
        layout.addWidget(trace_combo)
        layout.addStretch(1)
        return layout

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction("Open VNA", self._import_legacy_vna)
        file_menu.addAction("Save VNA", self._save_session)
        file_menu.addAction("Save to Default", self._save_to_default_vna)
        file_menu.addSeparator()
        file_menu.addAction("Export Data", self._export_data)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        self.mc_setup_action = menu_bar.addAction("MC Setup")
        self.mc_setup_action.triggered.connect(self._open_mc_setup_dialog)

        self.setup_action = menu_bar.addAction("Setup")
        self.setup_action.triggered.connect(
            lambda: self._open_setup_page_dialog("Setup", self.excitation_setup_page)
        )
        self.excitation_enabled_action = QtGui.QAction("Enable AO Excitation", self)
        self.excitation_enabled_action.setCheckable(True)
        self.excitation_enabled_action.triggered.connect(self.exc_enable.setChecked)
        self.modal_enabled_action = QtGui.QAction("Enable Modal Processing", self)
        self.modal_enabled_action.setCheckable(True)
        self.modal_enabled_action.triggered.connect(self.modal_enable_checkbox.setChecked)

        display_menu = menu_bar.addMenu("&Display")
        self.single_layout_action = QtGui.QAction("Single", self)
        self.dual_layout_action = QtGui.QAction("Dual", self)
        self.control_panel_action = QtGui.QAction("Control Panel", self)
        self.control_panel_action.setCheckable(True)
        self.control_panel_action.setChecked(True)
        self.control_panel_action.triggered.connect(self._toggle_control_panel)
        self.overlay_action = QtGui.QAction("Overlay", self)
        self.overlay_action.setCheckable(True)
        self.overlay_action.triggered.connect(self.overlay_checkbox.setChecked)
        self.overlay_upper_action = display_menu.addAction("Overlay Upper", self._capture_top_overlay)
        self.overlay_lower_action = display_menu.addAction("Overlay Lower", self._capture_bottom_overlay)
        display_menu.addAction("Clear Overlays", self._clear_overlays)
        display_menu.addSeparator()
        self.cursor_action = QtGui.QAction("Cursor Readout", self)
        self.cursor_action.setCheckable(True)
        self.cursor_action.setChecked(True)
        self.cursor_action.triggered.connect(self._toggle_cursor_readout)
        self.markers_action = display_menu.addAction("Mark")
        self.markers_action.setCheckable(True)
        self.markers_action.setChecked(False)
        self.markers_action.triggered.connect(
            lambda _checked=False: self._toggle_mark_at_cursor("top")
        )
        self.data_tip_action = QtGui.QAction("Data Tip", self)
        self.data_tip_action.setCheckable(True)
        self.data_tip_action.setChecked(False)
        self.data_tip_action.triggered.connect(self._toggle_data_tips)
        self.grids_action = QtGui.QAction("Grids", self)
        self.grids_action.setCheckable(True)
        self.grids_action.setChecked(True)
        self.grids_action.triggered.connect(self._toggle_grids)
        self.axis_labels_action = QtGui.QAction("Axis Labels", self)
        self.axis_labels_action.setCheckable(True)
        self.axis_labels_action.setChecked(True)
        self.axis_labels_action.triggered.connect(self._update_axis_labels)
        display_menu.addSeparator()
        display_menu.addAction("Open Current Plots", self._open_current_plot_window)
        display_menu.addSeparator()
        self.light_theme_action = QtGui.QAction("Light Theme", self)
        self.light_theme_action.setCheckable(True)
        self.light_theme_action.setChecked(self._theme_name == "light")
        self.light_theme_action.triggered.connect(self._toggle_light_theme)
        display_menu.addAction(self.light_theme_action)

        self.modal_menu_action = menu_bar.addAction("Modal")
        self.modal_menu_action.triggered.connect(self._open_modal_parameters_dialog)
        self.force_window_action = QtGui.QAction("Force Window", self)
        self.force_window_action.setCheckable(True)
        self.force_window_action.triggered.connect(self.force_window_checkbox.setChecked)
        self.exp_window_action = QtGui.QAction("Exponential Window", self)
        self.exp_window_action.setCheckable(True)
        self.exp_window_action.triggered.connect(self.exp_window_checkbox.setChecked)
        self.reject_double_hit_action = QtGui.QAction("Reject Double Hit", self)
        self.reject_double_hit_action.setCheckable(True)
        self.reject_double_hit_action.triggered.connect(
            self.reject_double_hit_checkbox.setChecked
        )
        self.reject_overload_action = QtGui.QAction("Reject Overload", self)
        self.reject_overload_action.setCheckable(True)
        self.reject_overload_action.triggered.connect(self.reject_overload_checkbox.setChecked)

        self.analysis_viewer_action = menu_bar.addAction("Analysis")
        self.analysis_viewer_action.triggered.connect(self._open_analysis_viewer)

    def _open_analysis_viewer(self) -> None:
        if self._analysis_viewer is None:
            from python_vna.ui.analysis_viewer import AnalysisViewer

            self._analysis_viewer = AnalysisViewer(None, theme=self._theme())
            self._analysis_viewer.set_current_measurement_provider(
                lambda: (self.controller.state.measurement, self.session)
            )
        self._analysis_viewer.apply_theme(self._theme())
        self._analysis_viewer.sync_current_measurement(
            self.controller.state.measurement,
            session_config=self.session,
        )
        self._analysis_viewer.show()
        self.statusBar().showMessage("Analysis Viewer opened")

    def _open_mc_setup_dialog(self) -> None:
        dialog = MCSetupDialog(self)
        dialog.exec()

    def _open_top_setup_dialog(self, tab_name: str) -> None:
        if not hasattr(self, "top_control_tabs"):
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"{tab_name} Setup")
        dialog.setStyleSheet(self.styleSheet())
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        tabs = self.top_control_tabs
        tabs.setParent(dialog)
        tabs.setVisible(True)
        layout.addWidget(tabs)
        for index in range(tabs.count()):
            if tabs.tabText(index) == tab_name:
                tabs.setCurrentIndex(index)
                break
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(820, 360)
        try:
            dialog.exec()
        finally:
            tabs.setParent(self)
            tabs.hide()

    def _open_setup_page_dialog(self, title: str, page: QtWidgets.QWidget) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setStyleSheet(self.styleSheet())
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        page.setParent(dialog)
        page.setVisible(True)
        layout.addWidget(page)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._fit_setup_page_dialog(dialog, page, title)
        try:
            dialog.exec()
        finally:
            page.setParent(self.top_control_tabs)
            page.hide()

    @staticmethod
    def _fit_setup_page_dialog(dialog: QtWidgets.QDialog, page: QtWidgets.QWidget, title: str) -> None:
        page.adjustSize()
        dialog.adjustSize()
        page_hint = page.sizeHint()
        dialog_hint = dialog.sizeHint()
        min_width = 760 if title == "Setup" else 620
        min_height = 190 if title == "Setup" else 230
        width = max(min_width, dialog_hint.width(), page_hint.width() + 32)
        height = max(min_height, dialog_hint.height(), page_hint.height() + 82)
        dialog.setMinimumSize(width, height)
        dialog.resize(width, height)

    def _open_modal_parameters_dialog(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Modal Parameters")
        dialog.setModal(True)
        theme = self._theme()
        dialog.setStyleSheet(
            self._theme_stylesheet(
            """
            QDialog {
                background: @window_bg@;
            }
            QLabel {
                background: @cell_bg@;
                color: @label_text@;
                font-size: 8pt;
                font-weight: bold;
                padding: 4px 6px;
                border-radius: 6px;
            }
            QDoubleSpinBox {
                background: @panel_bg_alt@;
                color: @text@;
                font-size: 8pt;
                font-weight: bold;
                min-height: 22px;
                border: 1px solid @control_border@;
                border-radius: 7px;
                padding: 1px 20px 1px 6px;
            }
            QDoubleSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 18px;
                height: 11px;
                border-left: 1px solid @border@;
                border-bottom: 1px solid @border@;
                background: @cell_bg@;
                border-top-right-radius: 6px;
            }
            QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 18px;
                height: 11px;
                border-left: 1px solid @border@;
                background: @cell_bg@;
                border-bottom-right-radius: 6px;
            }
            QDoubleSpinBox::up-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 6px solid @label_text@;
            }
            QDoubleSpinBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid @label_text@;
            }
            QPushButton {
                background: @accent@;
                color: #ffffff;
                font-size: 8pt;
                font-weight: bold;
                min-width: 70px;
                min-height: 20px;
                border: 1px solid @accent_hover@;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: @accent_hover@;
            }
            """,
            theme,
            )
        )
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)

        edits: dict[str, QtWidgets.QDoubleSpinBox] = {}
        specs = [
            (
                "double_hit_threshold",
                "double hit amplitude %",
                10.0,
                100.0,
                self.double_hit_threshold_edit.value() * 100.0,
                "Used only when double hit reject is selected in the Setup PROCESSING controls",
            ),
            (
                "double_hit_delay",
                "double hit delay %",
                20.0,
                50.0,
                self.double_hit_delay_edit.value() * 100.0,
                "Used only when double hit reject is selected in the Setup PROCESSING controls",
            ),
            (
                "force_window",
                "force window size in %",
                5.0,
                100.0,
                self.force_window_fraction_edit.value() * 100.0,
                "Used only when the User Defined Window is selected in the Setup PROCESSING controls",
            ),
            (
                "exp_decay",
                "exponential window decay %",
                1.0,
                100.0,
                self.exp_window_decay_edit.value() * 100.0,
                "Used only when the User Defined Window is selected in the Setup PROCESSING controls",
            ),
        ]
        for key, label_text, minimum, maximum, value, tooltip in specs:
            edit = CompactDoubleSpinBox()
            edit.setRange(minimum, maximum)
            edit.setDecimals(1)
            edit.setSingleStep(1.0)
            edit.setValue(min(max(value, minimum), maximum))
            edit.setToolTip(tooltip)
            edit.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
            edit.setFixedWidth(78)
            edits[key] = edit
            label = QtWidgets.QLabel(label_text)
            label.setMinimumWidth(210)
            label.setToolTip(tooltip)
            form.addRow(label, edit)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Apply).setText("Apply")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancel")

        def apply_values() -> None:
            self.double_hit_threshold_edit.setValue(edits["double_hit_threshold"].value() / 100.0)
            self.double_hit_delay_edit.setValue(edits["double_hit_delay"].value() / 100.0)
            self.force_window_fraction_edit.setValue(edits["force_window"].value() / 100.0)
            self.exp_window_decay_edit.setValue(edits["exp_decay"].value() / 100.0)
            self._read_session_from_widgets()
            self.statusBar().showMessage("Modal parameters applied")

        buttons.clicked.connect(
            lambda button: (
                apply_values()
                if buttons.standardButton(button) == QtWidgets.QDialogButtonBox.Apply
                else dialog.reject()
            )
        )
        layout.addWidget(buttons)
        dialog.setMinimumSize(380, 170)
        dialog.resize(400, 180)
        dialog.exec()

    def _toggle_grids(self, enabled: bool) -> None:
        self.top_plot.showGrid(x=enabled, y=enabled, alpha=0.25)
        self.bottom_plot.showGrid(x=enabled, y=enabled, alpha=0.25)

    def _toggle_cursor_readout(self, enabled: bool) -> None:
        self._cursor_enabled = enabled
        if not enabled:
            self.top_cursor_label.setText("Top Cursor: off")
            self.bottom_cursor_label.setText("Bottom Cursor: off")
            self._cursor_positions["top"] = None
            self._cursor_positions["bottom"] = None
            for key in ("top", "bottom"):
                if self._cursor_lines[key] is not None:
                    self._cursor_lines[key].setVisible(False)
                if self._cursor_points[key] is not None:
                    self._cursor_points[key].setData([], [])
                    self._cursor_points[key].setVisible(False)
                if self._cursor_texts[key] is not None:
                    self._cursor_texts[key].setVisible(False)

    def _create_cursor_items(self, plot, key: str) -> None:
        palette = self._cursor_palette()
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(palette["line"], width=1.4))
        line.setZValue(CURSOR_Z)
        line.setVisible(False)
        plot.addItem(line, ignoreBounds=True)
        point = pg.ScatterPlotItem(
            size=10,
            symbol="+",
            brush=pg.mkBrush(255, 255, 255, 0),
            pen=pg.mkPen(palette["line"], width=1.8),
            pxMode=True,
        )
        point.setZValue(CURSOR_Z + 1)
        point.setVisible(False)
        plot.addItem(point)
        text = pg.TextItem(
            text="",
            color=palette["text"],
            anchor=(-0.05, 1.05),
            fill=pg.mkBrush(palette["fill"]),
            border=pg.mkPen(palette["border"], width=0.9),
        )
        text.setZValue(CURSOR_Z + 2)
        text.setVisible(False)
        plot.addItem(text)
        self._cursor_lines[key] = line
        self._cursor_points[key] = point
        self._cursor_texts[key] = text

    def _cursor_palette(self) -> dict[str, object]:
        return _cursor_palette_for_background(str(self._theme().get("plot_bg", "#ffffff")))

    def _apply_cursor_theme(self) -> None:
        palette = self._cursor_palette()
        for key in ("top", "bottom"):
            if self._cursor_lines.get(key) is not None:
                self._cursor_lines[key].setPen(pg.mkPen(palette["line"], width=1.4))
            if self._cursor_points.get(key) is not None:
                self._cursor_points[key].setPen(pg.mkPen(palette["line"], width=1.8))
                self._cursor_points[key].setBrush(pg.mkBrush(255, 255, 255, 0))
            if self._cursor_texts.get(key) is not None:
                _apply_text_item_style(
                    self._cursor_texts[key],
                    color=palette["text"],
                    fill=palette["fill"],
                    border=palette["border"],
                )

    def _attach_cursor_tracking(self, plot, key: str) -> None:
        def _handle_mouse_move(event):
            if not self._cursor_enabled:
                return
            pos = event[0]
            if plot.sceneBoundingRect().contains(pos):
                if not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton):
                    return
                mouse_point = plot.getPlotItem().vb.mapSceneToView(pos)
                cursor_x = self._x_from_plot_coord(key, float(mouse_point.x()))
                cursor_y = self._y_from_plot_coord(key, float(mouse_point.y()))
                self._move_cursor_to_point(key, cursor_x, cursor_y)

        pg.SignalProxy(plot.scene().sigMouseMoved, rateLimit=30, slot=_handle_mouse_move)

    def _set_cursor_position(
        self,
        key: str,
        cursor_x: float,
        cursor_y: float,
        trace_name: str | None = None,
        announce: bool = True,
    ) -> bool:
        if trace_name:
            self._set_active_trace(key, trace_name)
        self._cursor_positions[key] = (cursor_x, cursor_y)
        if not self._cursor_enabled:
            self._cursor_enabled = True
            if hasattr(self, "cursor_action"):
                self.cursor_action.blockSignals(True)
                self.cursor_action.setChecked(True)
                self.cursor_action.blockSignals(False)
            if key == "top":
                self.bottom_cursor_label.setText("Bottom Cursor: --" if self._cursor_positions["bottom"] is None else self.bottom_cursor_label.text())
            else:
                self.top_cursor_label.setText("Top Cursor: --" if self._cursor_positions["top"] is None else self.top_cursor_label.text())
        plot_x = self._x_to_plot_coord(key, cursor_x)
        plot_y = self._y_to_plot_coord(key, cursor_y)
        if self._cursor_lines[key] is not None:
            self._cursor_lines[key].setValue(plot_x)
            self._cursor_lines[key].setVisible(True)
        if self._cursor_points[key] is not None:
            self._cursor_points[key].setData([plot_x], [plot_y])
            self._cursor_points[key].setVisible(True)
        if self._cursor_texts[key] is not None:
            self._cursor_texts[key].setText(f"X {cursor_x:.6g}\nY {cursor_y:.6g}")
            if hasattr(self._cursor_texts[key], "setAnchor"):
                self._cursor_texts[key].setAnchor(
                    self._data_tip_anchor_for_plot_point(key, plot_x, plot_y)
                )
            self._cursor_texts[key].setPos(plot_x, plot_y)
            self._cursor_texts[key].setVisible(True)
        label = self.top_cursor_label if key == "top" else self.bottom_cursor_label
        label.setText(f"{key.title()} Cursor: x={cursor_x:.4g}, y={cursor_y:.4g}")
        if self._mark_enabled.get(key, False):
            self._marker_positions[key][1] = cursor_x
            self._refresh_markers(key)
        self._update_marker_readout(key)
        if announce:
            self.statusBar().showMessage(
                f"{key.title()} cursor on {self._active_trace_names.get(key) or '--'}: "
                f"x={cursor_x:.4g}, y={cursor_y:.4g}"
            )
        return True

    def _move_cursor_to_point(self, key: str, click_x: float, click_y: float) -> bool:
        trace_name = self._nearest_trace_name(key, click_x, click_y)
        if trace_name:
            self._set_active_trace(key, trace_name)
        cursor_x, cursor_y = self._nearest_curve_point_2d(key, click_x, click_y)
        if cursor_x is None or cursor_y is None:
            cursor_x, cursor_y = self._nearest_curve_point(key, click_x)
        if cursor_x is None or cursor_y is None:
            return False
        return self._set_cursor_position(key, cursor_x, cursor_y)

    def _move_cursor_from_scene_pos(self, key: str, scene_pos) -> bool:
        plot = self._plot_widget_for_key(key)
        if not plot.sceneBoundingRect().contains(scene_pos):
            return False
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._x_from_plot_coord(key, float(mouse_point.x()))
        click_y = self._y_from_plot_coord(key, float(mouse_point.y()))
        return self._move_cursor_to_point(key, click_x, click_y)

    def _attach_dynamic_y_scaling(self, plot, key: str) -> None:
        def _handle_x_range_changed(_view_box, _view_range) -> None:
            if self._axis_scaling_key == key:
                return
            if not self._auto_y_follow_visible_x.get(key, False):
                return
            if self._manual_y_ranges.get(key) is not None:
                return
            if not self._last_plot_cache.get(key):
                return
            self._apply_axis_scale(key, preserve_x=True, y_scope="visible")

        plot.getPlotItem().vb.sigXRangeChanged.connect(_handle_x_range_changed)

    def _attach_axis_history(self, plot, key: str) -> None:
        def _handle_range_changed(_view_box, _ranges) -> None:
            if self._axis_scaling_key == key or self._axis_history_suspended:
                self._last_view_ranges[key] = self._current_plot_ranges(plot)
                return
            current = self._current_plot_ranges(plot)
            previous = self._last_view_ranges.get(key)
            if previous is not None and not self._ranges_close(previous, current):
                self._axis_range_history[key].append(previous)
            self._last_view_ranges[key] = current

        plot.getPlotItem().vb.sigRangeChanged.connect(_handle_range_changed)

    @staticmethod
    def _current_plot_ranges(plot) -> tuple[tuple[float, float], tuple[float, float]]:
        x_range, y_range = plot.viewRange()
        return (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )

    @staticmethod
    def _ranges_close(
        left: tuple[tuple[float, float], tuple[float, float]],
        right: tuple[tuple[float, float], tuple[float, float]],
    ) -> bool:
        return bool(np.allclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float)))

    def _restore_axis_history(self, key: str) -> bool:
        if not self._axis_range_history[key]:
            self._auto_scale_plot_xy(key)
            return False
        plot = self._plot_widget_for_key(key)
        x_range, y_range = self._axis_range_history[key].pop()
        self._axis_history_suspended = True
        try:
            plot.setXRange(x_range[0], x_range[1], padding=0.0)
            plot.setYRange(y_range[0], y_range[1], padding=0.0)
            self._last_view_ranges[key] = (x_range, y_range)
        finally:
            self._axis_history_suspended = False
        self.statusBar().showMessage(f"Restored previous {key} axis range")
        return True

    def _attach_plot_context_menu(self, plot, key: str) -> None:
        plot.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        plot.customContextMenuRequested.connect(
            lambda pos, plot_key=key, plot_widget=plot: self._show_plot_context_menu(
                plot_widget, plot_key, pos
            )
        )

    def _show_plot_context_menu(self, plot, key: str, pos: QtCore.QPoint) -> None:
        if self._suppress_next_plot_context_menu:
            self._suppress_next_plot_context_menu = False
            return
        menu, actions = self._build_plot_context_menu(plot, key)
        action = menu.exec(plot.mapToGlobal(pos))
        if action is None:
            return
        if action is actions["restore_zoom"]:
            self._restore_axis_history(key)
        elif action is actions["auto_scale"]:
            self._auto_scale_plot_xy(key)
        elif action is actions["cursor_readout"]:
            enabled = not self._cursor_enabled
            self._toggle_cursor_readout(enabled)
            self.cursor_action.setChecked(enabled)

    def _build_plot_context_menu(self, plot, key: str) -> tuple[QtWidgets.QMenu, dict[str, QtGui.QAction]]:
        menu = QtWidgets.QMenu(plot)
        menu.setToolTipsVisible(True)
        actions: dict[str, QtGui.QAction] = {}
        actions["restore_zoom"] = menu.addAction("Back One Zoom")
        actions["restore_zoom"].setToolTip("Restore the previous zoom level; falls back to auto scale.")
        actions["auto_scale"] = menu.addAction("Auto Scale")
        actions["auto_scale"].setToolTip("Reset this plot to the current data X/Y range.")
        menu.addSeparator()
        actions["cursor_readout"] = menu.addAction("Cursor Readout")
        actions["cursor_readout"].setCheckable(True)
        actions["cursor_readout"].setChecked(self._cursor_enabled)
        return menu, actions

    def _toggle_markers(self, enabled: bool) -> None:
        self._markers_enabled = enabled
        for key in ("top", "bottom"):
            self._mark_enabled[key] = enabled and self._mark_enabled.get(key, False)
            self._set_mark_button_checked(key, self._mark_enabled[key])
        self._refresh_markers("top")
        self._refresh_markers("bottom")
        self._update_marker_readout("top")
        self._update_marker_readout("bottom")

    def _set_mark_button_checked(self, key: str, checked: bool) -> None:
        button = getattr(self, f"{key}_mark_button", None)
        if button is None:
            return
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)

    def _toggle_data_tips(self, enabled: bool) -> None:
        self._data_tip_enabled = enabled
        for button_name in ("top_data_tip_button", "bottom_data_tip_button", "toolbar_data_tip_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.blockSignals(True)
                button.setChecked(enabled)
                button.blockSignals(False)
        if hasattr(self, "data_tip_action"):
            self.data_tip_action.blockSignals(True)
            self.data_tip_action.setChecked(enabled)
            self.data_tip_action.blockSignals(False)
        state = "on" if enabled else "off"
        self.statusBar().showMessage(f"Data Tip mode {state}")

    def _create_marker_lines(self, plot, key: str) -> None:
        pens = [pg.mkPen("#ffb000", width=1.2), pg.mkPen("#00d1b2", width=1.2)]
        for index, pen in enumerate(pens):
            line = pg.InfiniteLine(angle=90, movable=True, pen=pen)
            line.setZValue(MARKER_Z)
            line.setVisible(False)
            line.sigPositionChanged.connect(
                lambda line_obj, plot_key=key, marker_index=index: self._handle_marker_line_drag(
                    plot_key, marker_index, line_obj
                )
            )
            plot.addItem(line, ignoreBounds=True)
            self._marker_lines[key].append(line)

    def _create_marker_points(self, plot, key: str) -> None:
        brushes = [pg.mkBrush("#ffb000"), pg.mkBrush("#00d1b2")]
        for index, brush in enumerate(brushes):
            point = pg.ScatterPlotItem(
                size=12,
                symbol="o",
                brush=brush,
                pen=pg.mkPen("k", width=0.8),
                pxMode=True,
            )
            point.setZValue(MARKER_Z + 1)
            point.setVisible(False)
            plot.addItem(point)
            self._marker_points[key].append(point)
            text = pg.TextItem(
                text="",
                color=("#ffb000" if index == 0 else "#00d1b2"),
                anchor=(0, 1),
            )
            text.setZValue(MARKER_Z + 2)
            text.setVisible(False)
            plot.addItem(text)
            self._marker_texts[key].append(text)

    def _add_marker_history_point(self, plot, key: str, marker_x: float, marker_y: float) -> None:
        history_point = pg.ScatterPlotItem(
            size=8,
            symbol="x",
            brush=pg.mkBrush(255, 255, 255, 0),
            pen=pg.mkPen("#f5f5f5", width=1.0),
            pxMode=True,
        )
        history_point.setZValue(MARKER_Z + 1)
        plot_x = self._x_to_plot_coord(key, marker_x)
        plot_y = self._y_to_plot_coord(key, marker_y)
        history_point.setData([plot_x], [plot_y])
        plot.addItem(history_point)
        history_text = pg.TextItem(
            text=f"X={marker_x:.6g}\nY={marker_y:.6g}",
            color="#f5f5f5",
            anchor=(0, 1),
        )
        history_text.setZValue(MARKER_Z + 2)
        history_text.setPos(plot_x, plot_y)
        plot.addItem(history_text)
        self._marker_history_points[key].append(history_point)
        self._marker_history_texts[key].append(history_text)

    def _clear_marker_history(self, key: str) -> None:
        plot = self._plot_widget_for_key(key)
        for item in self._marker_history_points[key]:
            plot.removeItem(item)
        for item in self._marker_history_texts[key]:
            plot.removeItem(item)
        self._marker_history_points[key].clear()
        self._marker_history_texts[key].clear()

    def _nearest_curve_point_for_trace(
        self, key: str, trace_name: str | None, click_x: float, click_y: float
    ) -> tuple[float | None, float | None]:
        if not trace_name:
            return self._nearest_curve_point_2d(key, click_x, click_y)
        curves = self._last_plot_cache.get(key, {})
        curve = curves.get(trace_name)
        if curve is None:
            return self._nearest_curve_point_2d(key, click_x, click_y)
        x_data, y_data = curve
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            return None, None
        point_count = min(x_arr.size, y_arr.size)
        x_arr = x_arr[:point_count]
        y_arr = y_arr[:point_count]
        finite = np.isfinite(x_arr) & np.isfinite(y_arr)
        if not np.any(finite):
            return None, None
        x_arr = x_arr[finite]
        y_arr = y_arr[finite]
        plot = self._plot_widget_for_key(key)
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._x_to_plot_coord(key, click_x)
        click_plot_y = self._y_to_plot_coord(key, click_y)
        plot_x = np.asarray([self._x_to_plot_coord(key, float(value)) for value in x_arr])
        plot_y = np.asarray([self._y_to_plot_coord(key, float(value)) for value in y_arr])
        scores = ((plot_x - click_plot_x) / x_span) ** 2 + ((plot_y - click_plot_y) / y_span) ** 2
        index = int(np.nanargmin(scores))
        return float(x_arr[index]), float(y_arr[index])

    def _update_data_tip_position(
        self, key: str, data_tip: dict[str, object], tip_x: float, tip_y: float
    ) -> None:
        plot_x = self._x_to_plot_coord(key, tip_x)
        plot_y = self._y_to_plot_coord(key, tip_y)
        data_tip["x"] = tip_x
        data_tip["y"] = tip_y
        point = data_tip["point"]
        text = data_tip["text"]
        point.setData([plot_x], [plot_y])
        text.setText(f"X {tip_x:.6g}\nY {tip_y:.6g}")
        if hasattr(text, "setAnchor"):
            text.setAnchor(self._data_tip_anchor_for_plot_point(key, plot_x, plot_y))
        text.setPos(plot_x, plot_y)

    def _data_tip_anchor_for_plot_point(self, key: str, plot_x: float, plot_y: float) -> tuple[float, float]:
        plot = self._plot_widget_for_key(key)
        try:
            x_range, y_range = plot.viewRange()
        except Exception:
            return (-0.05, 1.05)
        x_min, x_max = x_range
        y_min, y_max = y_range
        x_span = max(float(x_max - x_min), 1e-20)
        y_span = max(float(y_max - y_min), 1e-20)
        near_right = (float(plot_x) - float(x_min)) / x_span > 0.72
        near_top = (float(plot_y) - float(y_min)) / y_span > 0.72
        return (1.05 if near_right else -0.05, -0.05 if near_top else 1.05)

    def _drag_data_tip_to_scene_pos(self, key: str, data_tip: dict[str, object], scene_pos) -> bool:
        plot = self._plot_widget_for_key(key)
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._x_from_plot_coord(key, float(mouse_point.x()))
        click_y = self._y_from_plot_coord(key, float(mouse_point.y()))
        tip_x, tip_y = self._nearest_curve_point_for_trace(
            key, data_tip.get("trace"), click_x, click_y
        )
        if tip_x is None or tip_y is None:
            return False
        self._update_data_tip_position(key, data_tip, tip_x, tip_y)
        self.statusBar().showMessage(
            f"{key.title()} data tip moved: x={tip_x:.4g}, y={tip_y:.4g}"
        )
        return True

    def _place_data_tip(self, key: str, click_x: float, click_y: float) -> bool:
        trace_name = self._nearest_trace_name(key, click_x, click_y)
        if trace_name:
            self._set_active_trace(key, trace_name)
        tip_x, tip_y = self._nearest_curve_point_2d(key, click_x, click_y)
        if tip_x is None or tip_y is None:
            tip_x, tip_y = self._nearest_curve_point(key, click_x)
        if tip_x is None or tip_y is None:
            return False
        plot = self._plot_widget_for_key(key)
        plot_x = self._x_to_plot_coord(key, tip_x)
        plot_y = self._y_to_plot_coord(key, tip_y)
        data_tip: dict[str, object] = {
            "trace": trace_name,
            "x": tip_x,
            "y": tip_y,
        }
        point = DataTipPoint(
            [plot_x],
            [plot_y],
            size=9,
            symbol="o",
            brush=pg.mkBrush("#fff59d"),
            pen=pg.mkPen("#111111", width=0.8),
            pxMode=True,
            on_drag=lambda scene_pos, plot_key=key, tip=data_tip: self._drag_data_tip_to_scene_pos(
                plot_key, tip, scene_pos
            ),
            on_context_menu=lambda screen_pos, plot_key=key, tip=data_tip: self._show_data_tip_menu(
                plot_key, tip, screen_pos
            ),
        )
        point.setZValue(DATA_TIP_Z + 1)
        text = DataTipText(
            text=f"X {tip_x:.6g}\nY {tip_y:.6g}",
            color="#111111",
            anchor=self._data_tip_anchor_for_plot_point(key, plot_x, plot_y),
            fill=pg.mkBrush(255, 245, 157, 230),
            border=pg.mkPen("#111111", width=0.8),
            on_context_menu=lambda screen_pos, plot_key=key, tip=data_tip: self._show_data_tip_menu(
                plot_key, tip, screen_pos
            ),
        )
        text.setZValue(DATA_TIP_Z + 2)
        text.setPos(plot_x, plot_y)
        plot.addItem(point)
        plot.addItem(text)
        data_tip["point"] = point
        data_tip["text"] = text
        self._data_tip_items[key].append(data_tip)
        self.statusBar().showMessage(
            f"{key.title()} data tip on {trace_name or '--'}: x={tip_x:.4g}, y={tip_y:.4g}"
        )
        return True

    def _clear_data_tips(self, key: str) -> None:
        plot = self._plot_widget_for_key(key)
        for data_tip in self._data_tip_items[key]:
            plot.removeItem(data_tip["point"])
            plot.removeItem(data_tip["text"])
        self._data_tip_items[key].clear()

    def _clear_all_data_tips(self) -> None:
        self._clear_data_tips("top")
        self._clear_data_tips("bottom")
        self.statusBar().showMessage("Cleared data tips")

    def _delete_data_tip(self, key: str, data_tip: dict[str, object]) -> bool:
        if data_tip not in self._data_tip_items[key]:
            return False
        plot = self._plot_widget_for_key(key)
        plot.removeItem(data_tip["point"])
        plot.removeItem(data_tip["text"])
        self._data_tip_items[key].remove(data_tip)
        self.statusBar().showMessage(f"Deleted {key} data tip")
        return True

    def _show_data_tip_menu(self, key: str, data_tip: dict[str, object], screen_pos) -> None:
        self._suppress_plot_context_menu_once()
        menu = QtWidgets.QMenu(self)
        delete_this = menu.addAction("Delete This Data Tip")
        delete_all = menu.addAction("Delete All Data Tips")
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is delete_this:
            self._delete_data_tip(key, data_tip)
        elif action is delete_all:
            self._clear_all_data_tips()

    def _suppress_plot_context_menu_once(self) -> None:
        self._suppress_next_plot_context_menu = True

    @staticmethod
    def _mouse_button_name(button) -> str:
        if button == QtCore.Qt.LeftButton:
            return "left"
        if button == QtCore.Qt.RightButton:
            return "right"
        return "other"

    def _set_active_trace(self, key: str, trace_name: str | None) -> None:
        if not trace_name:
            return
        self._active_trace_names[key] = trace_name
        combo = self._trace_combo_for_key(key)
        combo_index = combo.findData(trace_name)
        if combo_index >= 0 and combo.currentIndex() != combo_index:
            combo.blockSignals(True)
            combo.setCurrentIndex(combo_index)
            combo.blockSignals(False)
        strip_combo = self.top_trace_strip_combo if key == "top" else self.bottom_trace_strip_combo
        strip_index = strip_combo.findData(trace_name)
        if strip_index >= 0 and strip_combo.currentIndex() != strip_index:
            strip_combo.blockSignals(True)
            strip_combo.setCurrentIndex(strip_index)
            strip_combo.blockSignals(False)
        self._refresh_curve_pens(key)
        self._refresh_markers(key)
        self._update_marker_readout(key)

    def _set_active_marker(self, key: str, marker_index: int) -> None:
        self._active_marker_index[key] = marker_index
        self.statusBar().showMessage(f"{key.title()} active marker: {'A' if marker_index == 0 else 'B'}")

    def _clear_marker_pair(self, key: str) -> None:
        self._marker_positions[key] = [None, None]
        self._mark_enabled[key] = False
        self._set_mark_button_checked(key, False)
        self._clear_marker_history(key)
        self._refresh_markers(key)
        self._update_marker_readout(key)
        self.statusBar().showMessage(f"Cleared {key} markers")

    def _toggle_mark_at_cursor(self, key: str) -> bool:
        if self._mark_enabled.get(key, False):
            self._mark_enabled[key] = False
            self._marker_positions[key] = [None, None]
            self._set_mark_button_checked(key, False)
            if not any(self._mark_enabled.values()):
                self._markers_enabled = False
                if hasattr(self, "markers_action"):
                    self.markers_action.blockSignals(True)
                    self.markers_action.setChecked(False)
                    self.markers_action.blockSignals(False)
            self._refresh_markers(key)
            self._update_marker_readout(key)
            self.statusBar().showMessage(f"{key.title()} mark off")
            return False

        cursor = self._cursor_positions.get(key)
        if cursor is None:
            trace_name, curve = self._selected_curve(key)
            if curve is None:
                self.statusBar().showMessage(f"No {key} cursor position is available to mark")
                return False
            x_data, y_data = curve
            if len(x_data) == 0 or len(y_data) == 0:
                self.statusBar().showMessage(f"No {key} samples are available to mark")
                return False
            cursor = (float(x_data[0]), float(y_data[0]))
            self._set_cursor_position(key, cursor[0], cursor[1], trace_name, announce=False)

        self._markers_enabled = True
        self._mark_enabled[key] = True
        self._set_mark_button_checked(key, True)
        if hasattr(self, "markers_action"):
            self.markers_action.blockSignals(True)
            self.markers_action.setChecked(True)
            self.markers_action.blockSignals(False)
        self._marker_positions[key] = [cursor[0], cursor[0]]
        self._active_marker_index[key] = 1
        self._refresh_markers(key)
        self._update_marker_readout(key)
        self.statusBar().showMessage(
            f"{key.title()} mark set at x={cursor[0]:.4g}, y={cursor[1]:.4g}"
        )
        return True

    def _place_marker(self, key: str, marker_index: int, click_x: float, click_y: float) -> bool:
        nearest_trace = self._nearest_trace_name(key, click_x, click_y)
        if nearest_trace:
            self._set_active_trace(key, nearest_trace)
        if not self._markers_enabled:
            self._toggle_markers(True)
        marker_x, marker_y = self._nearest_curve_point_2d(key, click_x, click_y)
        if marker_x is None:
            marker_x, marker_y = self._nearest_curve_point(key, click_x)
        if marker_x is None:
            return False
        self._set_active_marker(key, marker_index)
        self._marker_positions[key][marker_index] = marker_x
        self._marker_next_index[key] = 1 - marker_index
        self._refresh_markers(key)
        self._update_marker_readout(key)
        trace_name = self._active_trace_names.get(key) or "--"
        self.statusBar().showMessage(
            f"{key.title()} marker {'A' if marker_index == 0 else 'B'} on {trace_name}: "
            f"x={marker_x:.4g}, y={0.0 if marker_y is None else marker_y:.4g}"
        )
        return True

    def _attach_marker_tracking(self, plot, key: str) -> None:
        def _handle_click(event):
            if not plot.sceneBoundingRect().contains(event.scenePos()):
                return
            mouse_point = plot.getPlotItem().vb.mapSceneToView(event.scenePos())
            click_x = self._x_from_plot_coord(key, float(mouse_point.x()))
            click_y = self._y_from_plot_coord(key, float(mouse_point.y()))
            button_name = self._mouse_button_name(event.button())
            nearest_trace = self._nearest_trace_name(key, click_x, click_y)
            if nearest_trace:
                self._set_active_trace(key, nearest_trace)
            if button_name == "right":
                event.accept()
                local_pos = plot.mapFromScene(event.scenePos())
                self._show_plot_context_menu(
                    plot,
                    key,
                    QtCore.QPoint(int(local_pos.x()), int(local_pos.y())),
                )
                return
            if button_name == "left":
                if self._data_tip_enabled:
                    self._place_data_tip(key, click_x, click_y)
                    return
                self._move_cursor_to_point(key, click_x, click_y)

        plot.scene().sigMouseClicked.connect(_handle_click)

    def _handle_marker_line_drag(self, key: str, marker_index: int, line) -> None:
        if not self._markers_enabled or not line.isVisible():
            return
        marker_x, _marker_y = self._nearest_curve_point(
            key, self._x_from_plot_coord(key, float(line.value()))
        )
        if marker_x is None:
            return
        self._marker_positions[key][marker_index] = marker_x
        marker_plot_x = self._x_to_plot_coord(key, marker_x)
        if abs(float(line.value()) - marker_plot_x) > 1e-12:
            line.blockSignals(True)
            line.setValue(marker_plot_x)
            line.blockSignals(False)
        self._set_active_marker(key, marker_index)
        self._refresh_markers(key)
        self._update_marker_readout(key)

    def _axis_label_for(self, display_mode: str, value_mode: str) -> tuple[str, str]:
        value_label = self._value_label_for_mode(display_mode, value_mode)
        channel_units = self._active_channel_units()
        displayed_units = self._displayed_channel_units(channel_units)
        response_units = self._active_response_units(channel_units)
        reference_unit = self._reference_unit(channel_units)
        displayed_label = self._join_units(displayed_units)
        response_label = self._join_units(response_units)
        if display_mode == "time":
            return ("Seconds", displayed_label)
        if display_mode == "autospectrum":
            if value_mode in {"dB", "dB_per_sqrt_hz"}:
                db_refs = self._active_db_refs()
                if len(db_refs) == 1:
                    unit = displayed_label if displayed_label else "Volts"
                    return ("Hertz", f"{value_label} (0dB={db_refs[0]:.6g} {unit})")
            return ("Hertz", f"{value_label} ({displayed_label})")
        if display_mode == "fft":
            return ("Hertz", f"{value_label} ({displayed_label})")
        if display_mode == "frf":
            if value_mode == "nyquist":
                return (
                    f"real ({response_label})/{reference_unit}",
                    f"imag ({response_label})/{reference_unit}",
                )
            return ("Hertz", f"{value_label} ({response_label})/{reference_unit}")
        if display_mode == "coherence":
            return ("Hertz", "Coherence")
        if display_mode == "cross_spectrum":
            if value_mode == "nyquist":
                return (
                    f"real ({response_label})*{reference_unit}",
                    f"imag ({response_label})*{reference_unit}",
                )
            return ("Hertz", f"{value_label} ({response_label})*{reference_unit}")
        if display_mode == "impulse_response":
            return ("Seconds", f"{value_label} ({response_label})/{reference_unit}")
        if display_mode in {"auto_correlation", "cross_correlation"}:
            if display_mode == "auto_correlation":
                return ("Seconds", f"{value_label} ({response_label} ^2)")
            return ("Seconds", f"{value_label} ({response_label})*{reference_unit}")
        return ("X", "Y")

    @staticmethod
    def _join_units(units: list[str]) -> str:
        unique_units = []
        for unit in units:
            if unit and unit not in unique_units:
                unique_units.append(unit)
        return ",".join(unique_units) if unique_units else "Volts"

    @staticmethod
    def _unit_from_channel(channel: ChannelConfig) -> str:
        return channel.engineering_unit.strip() if channel.engineering_unit.strip() else "Volts"

    def _active_channel_units(self) -> dict[str, str]:
        if hasattr(self, "channel_table"):
            units = {}
            for row in range(self.channel_table.rowCount()):
                if self.channel_table.item(row, 0).checkState() != QtCore.Qt.Checked:
                    continue
                name = self.channel_table.item(row, 1).text()
                unit = self.channel_table.item(row, 8).text().strip() or "Volts"
                units[name] = unit
            return units
        return {
            channel.name: self._unit_from_channel(channel)
            for channel in self.controller.state.session.ai_channels
            if channel.enabled
        }

    def _active_response_units(self, channel_units: dict[str, str]) -> list[str]:
        responses = self._active_response_names()
        if not responses:
            responses = [
                channel.name
                for channel in self.controller.state.session.ai_channels
                if channel.enabled
            ]
        return [channel_units.get(name, "Volts") for name in responses if name in channel_units]

    def _displayed_channel_units(self, channel_units: dict[str, str]) -> list[str]:
        if hasattr(self, "channel_table"):
            return [
                channel_units.get(self.channel_table.item(row, 1).text(), "Volts")
                for row in range(self.channel_table.rowCount())
                if self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked
            ]
        return [
            channel_units.get(channel.name, "Volts")
            for channel in self.controller.state.session.ai_channels
            if channel.enabled
        ]

    def _active_response_names(self) -> list[str]:
        if hasattr(self, "response_channel_list"):
            selected = [item.text() for item in self.response_channel_list.selectedItems()]
            if selected:
                return selected
        return list(self.controller.state.session.acquisition.response_channels)

    def _reference_unit(self, channel_units: dict[str, str]) -> str:
        if hasattr(self, "reference_channel_combo"):
            reference = self.reference_channel_combo.currentText().strip()
        else:
            reference = self.controller.state.session.acquisition.reference_channel
        return channel_units.get(reference, "Volts")

    def _active_db_refs(self) -> list[float]:
        refs = []
        if hasattr(self, "channel_table"):
            for row in range(self.channel_table.rowCount()):
                if self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked:
                    refs.append(float(self.channel_table.item(row, 13).text()))
        else:
            for channel in self.controller.state.session.ai_channels:
                if channel.enabled:
                    refs.append(channel.db_reference)
        unique_refs = []
        for ref in refs:
            if ref not in unique_refs:
                unique_refs.append(ref)
        return unique_refs

    @staticmethod
    def _value_label_for_mode(display_mode: str, value_mode: str) -> str:
        if value_mode == "dB_per_sqrt_hz":
            return "dB/rt(Hz)"
        if value_mode == "linear":
            return "rms" if display_mode == "autospectrum" else "mag"
        if value_mode == "power":
            return "rms^2"
        if value_mode == "linear_per_sqrt_hz":
            return "rms/rt(Hz)"
        if value_mode == "power_per_hz":
            return "rms^2/Hz"
        if value_mode == "log_linear":
            return "Log rms"
        if value_mode == "log_linear_per_sqrt_hz":
            return "Log rms/rt(Hz)"
        if value_mode == "log_power_per_hz":
            return "Log rms^2/Hz"
        if value_mode == "log_pk":
            return "Log pk"
        if value_mode == "log_p2p":
            return "Log p-p"
        if value_mode == "phase_u":
            return "phase u (degrees)"
        if value_mode == "phase":
            return "phase (degrees)"
        if value_mode == "log_mag":
            return "log mag"
        return value_mode

    @staticmethod
    def _display_mode(combo: QtWidgets.QComboBox) -> str:
        data = combo.currentData()
        return str(data) if data else combo.currentText()

    @staticmethod
    def _value_mode(combo: QtWidgets.QComboBox) -> str:
        data = combo.currentData()
        return str(data) if data else combo.currentText()

    @staticmethod
    def _current_combo_value(combo: QtWidgets.QComboBox) -> str:
        data = combo.currentData()
        return str(data) if data is not None else combo.currentText()

    def _update_axis_labels(self) -> None:
        if not hasattr(self, "top_plot") or not hasattr(self, "bottom_plot"):
            return
        if hasattr(self, "axis_labels_action") and not self.axis_labels_action.isChecked():
            self.top_plot.setLabel("bottom", "")
            self.top_plot.setLabel("left", "")
            self.bottom_plot.setLabel("bottom", "")
            self.bottom_plot.setLabel("left", "")
            return
        top_x, top_y = self._axis_label_for(
            self._display_mode(self.top_display_combo), self._value_mode(self.top_value_mode_combo)
        )
        bottom_x, bottom_y = self._axis_label_for(
            self._display_mode(self.bottom_display_combo),
            self._value_mode(self.bottom_value_mode_combo),
        )
        self.top_plot.setLabel("bottom", top_x)
        self.top_plot.setLabel("left", top_y)
        self.bottom_plot.setLabel("bottom", bottom_x)
        self.bottom_plot.setLabel("left", bottom_y)

    def _build_toolbar(self):
        container = QtWidgets.QWidget()
        container.setObjectName("topToolbar")
        container.setStyleSheet(self._toolbar_stylesheet(self._theme()))
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["simulated", "ni"])
        self.backend_combo.currentTextChanged.connect(self._backend_changed)
        self.device_combo = QtWidgets.QComboBox()
        self.refresh_devices_button = QtWidgets.QPushButton("Refresh")
        self.refresh_devices_button.setToolTip("Refresh Devices")
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        self.open_vna_button = QtWidgets.QPushButton("Open VNA")
        self.open_vna_button.setToolTip("Open legacy VNA and apply its setup/data")
        self.toolbar_data_tip_button = QtWidgets.QPushButton("Data Tip")
        self.toolbar_data_tip_button.setToolTip("Toggle MATLAB-style data tip labels")
        self.toolbar_data_tip_button.setCheckable(True)
        self.toolbar_data_tip_button.toggled.connect(self._toggle_data_tips)
        self.start_button = QtWidgets.QPushButton("Inst")
        self.start_button.setToolTip("Instant acquisition")
        self.avg_button = QtWidgets.QPushButton("Avg")
        self.record_button = QtWidgets.QPushButton("Record")
        self.record_button.setToolTip("Continuously record time data to DAT files")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.export_button = QtWidgets.QPushButton("Export")
        self.export_button.setToolTip("Export current measurement")
        self.save_session_button = QtWidgets.QPushButton("Save VNA")
        self.save_session_button.setToolTip("Save VNA")
        self.load_session_button = QtWidgets.QPushButton("Load")
        self.load_session_button.setToolTip("Load Session")
        self.import_legacy_button = QtWidgets.QPushButton("Import")
        self.import_legacy_button.setToolTip("Import VNA")
        self.controls_button = QtWidgets.QToolButton()
        self.controls_button.setText("Controls")
        self.controls_button.setCheckable(True)
        self.controls_button.setChecked(True)
        self.controls_button.toggled.connect(self._toggle_control_panel)
        self.start_button.clicked.connect(self._start_acquisition)
        self.avg_button.clicked.connect(self._start_average_acquisition)
        self.record_button.clicked.connect(self._start_continuous_recording)
        self.stop_button.clicked.connect(self._stop_acquisition)
        self.export_button.clicked.connect(self._export_data)
        self.save_session_button.clicked.connect(self._save_session)
        self.load_session_button.clicked.connect(self._load_session)
        self.import_legacy_button.clicked.connect(self._import_legacy_vna)
        self.open_vna_button.clicked.connect(self._import_legacy_vna)

        for widget in (
            self.open_vna_button,
            self.save_session_button,
            self.toolbar_data_tip_button,
            self.start_button,
            self.avg_button,
            self.record_button,
            self.stop_button,
            self.controls_button,
        ):
            if isinstance(widget, (QtWidgets.QPushButton, QtWidgets.QToolButton)):
                self._fit_button_text(widget)
            layout.addWidget(widget)
        layout.addStretch(1)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidget(container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.toolbar_scroll_area = scroll_area
        self.toolbar_container = container
        self._refresh_toolbar_size()
        return scroll_area

    def _refresh_toolbar_size(self) -> None:
        if not hasattr(self, "toolbar_scroll_area") or not hasattr(self, "toolbar_container"):
            return
        self.toolbar_container.adjustSize()
        height = max(34, self.toolbar_container.sizeHint().height() + 10)
        self.toolbar_scroll_area.setFixedHeight(height)

    @staticmethod
    def _fit_button_text(button) -> None:
        metrics = button.fontMetrics()
        width = metrics.horizontalAdvance(button.text()) + 36
        button.setMinimumWidth(max(54, width))
        button.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

    def _build_display_strip(self):
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in (
            self.top_display_strip_combo,
            self.bottom_display_strip_combo,
            self.top_value_strip_combo,
            self.bottom_value_strip_combo,
            self.top_trace_strip_combo,
            self.bottom_trace_strip_combo,
        ):
            widget.setMinimumWidth(90)
        layout.addWidget(QtWidgets.QLabel("Upper"))
        layout.addWidget(self.top_display_strip_combo)
        layout.addWidget(self.top_value_strip_combo)
        layout.addWidget(self.top_trace_strip_combo)
        layout.addSpacing(16)
        layout.addWidget(QtWidgets.QLabel("Lower"))
        layout.addWidget(self.bottom_display_strip_combo)
        layout.addWidget(self.bottom_value_strip_combo)
        layout.addWidget(self.bottom_trace_strip_combo)
        layout.addStretch(1)
        return layout

    def _build_top_control_strip(self) -> QtWidgets.QTabWidget:
        self.top_control_tabs = QtWidgets.QTabWidget()
        self.top_control_tabs.setParent(self)
        self.top_control_tabs.setObjectName("topControlTabs")
        self.top_control_tabs.setDocumentMode(True)
        self.top_control_tabs.setUsesScrollButtons(True)
        self.top_control_tabs.setMaximumHeight(172)
        self.excitation_setup_page = self._build_excitation_tab(compact=True)
        self.modal_setup_page = self._build_modal_tab(compact=True)
        self.display_setup_page = self._build_display_controls(compact=True)
        self.top_control_tabs.addTab(self.excitation_setup_page, "Excitation")
        self.top_control_tabs.addTab(self.modal_setup_page, "Modal")
        self.top_control_tabs.addTab(self.display_setup_page, "Display")
        self.top_control_tabs.hide()
        return self.top_control_tabs

    def _build_display_controls(self, compact: bool = False):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.layout_mode_combo = QtWidgets.QComboBox()
        self.layout_mode_combo.addItems(["dual", "single"])
        self.layout_mode_combo.currentTextChanged.connect(self._update_plot_layout)
        self.overlay_checkbox = QtWidgets.QCheckBox("Overlay")
        self.overlay_checkbox.toggled.connect(self._overlay_toggled)
        self.reference_channel_combo = QtWidgets.QComboBox()
        self.response_channel_list = QtWidgets.QListWidget()
        self.response_channel_list.setSelectionMode(
            QtWidgets.QAbstractItemView.MultiSelection
        )
        self.top_display_combo = QtWidgets.QComboBox()
        self.bottom_display_combo = QtWidgets.QComboBox()
        self.top_value_mode_combo = QtWidgets.QComboBox()
        self.bottom_value_mode_combo = QtWidgets.QComboBox()
        self.top_xscale_combo = QtWidgets.QComboBox()
        self.bottom_xscale_combo = QtWidgets.QComboBox()
        self.top_yscale_combo = QtWidgets.QComboBox()
        self.bottom_yscale_combo = QtWidgets.QComboBox()
        self.top_trace_combo = QtWidgets.QComboBox()
        self.bottom_trace_combo = QtWidgets.QComboBox()
        self.top_trace_list = QtWidgets.QListWidget()
        self.bottom_trace_list = QtWidgets.QListWidget()
        for trace_list in (self.top_trace_list, self.bottom_trace_list):
            trace_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            trace_list.setFixedHeight(self._trace_list_height(4))
            trace_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            trace_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            trace_list.setUniformItemSizes(True)
            font = trace_list.font()
            font.setPointSize(max(8, font.pointSize() - 1))
            trace_list.setFont(font)
            trace_list.setStyleSheet(self._trace_list_stylesheet(self._theme()))
        self.top_xscale_combo.addItems(["linear", "log"])
        self.bottom_xscale_combo.addItems(["linear", "log"])
        self.top_yscale_combo.addItems(["linear", "log"])
        self.bottom_yscale_combo.addItems(["linear", "log"])
        for label, mode in self.DISPLAY_MODE_ITEMS:
            self.top_display_combo.addItem(label, mode)
            self.bottom_display_combo.addItem(label, mode)
            self.top_display_strip_combo.addItem(label, mode)
            self.bottom_display_strip_combo.addItem(label, mode)
        self.top_display_combo.setCurrentText("y(t)")
        self.bottom_display_combo.setCurrentText("xfer")
        self.top_display_strip_combo.setCurrentText("y(t)")
        self.bottom_display_strip_combo.setCurrentText("xfer")
        self.top_display_combo.currentTextChanged.connect(
            lambda _mode: self._sync_value_mode_options(
                self.top_value_mode_combo, self._display_mode(self.top_display_combo)
            )
        )
        self.bottom_display_combo.currentTextChanged.connect(
            lambda _mode: self._sync_value_mode_options(
                self.bottom_value_mode_combo, self._display_mode(self.bottom_display_combo)
            )
        )
        self.top_display_combo.currentTextChanged.connect(
            self.top_display_strip_combo.setCurrentText
        )
        self.bottom_display_combo.currentTextChanged.connect(
            self.bottom_display_strip_combo.setCurrentText
        )
        self.top_display_strip_combo.currentTextChanged.connect(
            self.top_display_combo.setCurrentText
        )
        self.bottom_display_strip_combo.currentTextChanged.connect(
            self.bottom_display_combo.setCurrentText
        )
        self.top_display_combo.currentTextChanged.connect(
            lambda _value: self._display_mode_changed("top")
        )
        self.bottom_display_combo.currentTextChanged.connect(
            lambda _value: self._display_mode_changed("bottom")
        )
        self.top_value_mode_combo.currentTextChanged.connect(
            lambda _value: self._value_mode_changed("top")
        )
        self.bottom_value_mode_combo.currentTextChanged.connect(
            lambda _value: self._value_mode_changed("bottom")
        )
        self.top_xscale_combo.currentTextChanged.connect(
            lambda _value: self._refresh_current_measurement_view()
        )
        self.bottom_xscale_combo.currentTextChanged.connect(
            lambda _value: self._refresh_current_measurement_view()
        )
        self.top_yscale_combo.currentTextChanged.connect(
            lambda _value: self._refresh_current_measurement_view()
        )
        self.bottom_yscale_combo.currentTextChanged.connect(
            lambda _value: self._refresh_current_measurement_view()
        )
        self.top_trace_combo.currentIndexChanged.connect(
            lambda _index: self._trace_combo_selection_changed("top")
        )
        self.bottom_trace_combo.currentIndexChanged.connect(
            lambda _index: self._trace_combo_selection_changed("bottom")
        )
        self.top_value_mode_combo.currentTextChanged.connect(
            self.top_value_strip_combo.setCurrentText
        )
        self.bottom_value_mode_combo.currentTextChanged.connect(
            self.bottom_value_strip_combo.setCurrentText
        )
        self.top_value_strip_combo.currentTextChanged.connect(
            lambda text: self._sync_value_combo_from_strip(self.top_value_mode_combo, text)
        )
        self.bottom_value_strip_combo.currentTextChanged.connect(
            lambda text: self._sync_value_combo_from_strip(self.bottom_value_mode_combo, text)
        )
        self.top_trace_combo.currentIndexChanged.connect(
            lambda _index: self._sync_trace_strip_from_combo("top")
        )
        self.bottom_trace_combo.currentIndexChanged.connect(
            lambda _index: self._sync_trace_strip_from_combo("bottom")
        )
        self.top_trace_strip_combo.currentIndexChanged.connect(
            lambda _index: self._sync_trace_combo_from_strip("top")
        )
        self.bottom_trace_strip_combo.currentIndexChanged.connect(
            lambda _index: self._sync_trace_combo_from_strip("bottom")
        )
        self.top_trace_list.itemChanged.connect(
            lambda _item: self._trace_visibility_changed("top")
        )
        self.bottom_trace_list.itemChanged.connect(
            lambda _item: self._trace_visibility_changed("bottom")
        )
        self.device_info_label = QtWidgets.QLabel("Device: n/a")
        self.run_info_label = QtWidgets.QLabel("State: idle")
        self.top_cursor_label = QtWidgets.QLabel("Top Cursor: --")
        self.bottom_cursor_label = QtWidgets.QLabel("Bottom Cursor: --")
        self.top_marker_label = QtWidgets.QLabel("Top Marker: off")
        self.bottom_marker_label = QtWidgets.QLabel("Bottom Marker: off")
        self.top_marker_fields = self._build_marker_fields("Top")
        self.bottom_marker_fields = self._build_marker_fields("Bottom")
        self.response_channel_list.setMaximumHeight(120)
        self.response_channel_list.setMinimumWidth(120)

        controls_tabs = QtWidgets.QTabWidget()
        controls_tabs.setDocumentMode(True)
        controls_tabs.setUsesScrollButtons(True)
        controls_tabs.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed if compact else QtWidgets.QSizePolicy.Expanding,
        )

        display_page = QtWidgets.QWidget()
        display_layout = QtWidgets.QVBoxLayout(display_page)
        display_layout.setContentsMargins(6, 6, 6, 6)
        display_layout.setSpacing(6)
        display_row = QtWidgets.QHBoxLayout() if compact else None
        top_display_panel = self._build_display_panel(
            "Top Display",
            self.top_display_combo,
            self.top_value_mode_combo,
            self.top_xscale_combo,
            self.top_yscale_combo,
            self.top_trace_combo,
        )
        bottom_display_panel = self._build_display_panel(
            "Bottom Display",
            self.bottom_display_combo,
            self.bottom_value_mode_combo,
            self.bottom_xscale_combo,
            self.bottom_yscale_combo,
            self.bottom_trace_combo,
        )
        if display_row is not None:
            display_row.addWidget(top_display_panel)
            display_row.addWidget(bottom_display_panel)
            display_layout.addLayout(display_row)
        else:
            display_layout.addWidget(top_display_panel)
            display_layout.addWidget(bottom_display_panel)
        if not compact:
            display_layout.addStretch(1)
        controls_tabs.addTab(display_page, "Displays")

        cursor_page = QtWidgets.QWidget()
        cursor_layout = QtWidgets.QVBoxLayout(cursor_page)
        cursor_layout.setContentsMargins(6, 6, 6, 6)
        cursor_layout.setSpacing(6)
        readout_row = QtWidgets.QHBoxLayout()
        readout_row.addWidget(self.top_cursor_label)
        readout_row.addWidget(self.bottom_cursor_label)
        readout_row.addWidget(self.top_marker_label)
        readout_row.addWidget(self.bottom_marker_label)
        readout_row.addStretch(1)
        cursor_layout.addLayout(readout_row)
        cursor_layout.addWidget(
            QtWidgets.QLabel(
                "Cursor: left-click selects/moves on the trace; Mark fixes a delta reference; right-click opens scale menu"
            )
        )

        marker_row = QtWidgets.QHBoxLayout()
        marker_row.addWidget(self._build_marker_panel("Top", self.top_marker_fields))
        marker_row.addWidget(self._build_marker_panel("Bottom", self.bottom_marker_fields))
        marker_row.addStretch(1)
        cursor_layout.addLayout(marker_row)
        cursor_layout.addStretch(1)
        controls_tabs.addTab(cursor_page, "Cursor")

        if not compact:
            overview_page = QtWidgets.QWidget()
            overview_layout = QtWidgets.QVBoxLayout(overview_page)
            overview_layout.setContentsMargins(6, 6, 6, 6)
            overview_layout.setSpacing(6)
            overview_layout.addWidget(self._build_general_panel())
            overview_layout.addWidget(self._build_routing_panel())
            overview_layout.addWidget(self._build_status_panel())
            overview_layout.addStretch(1)
            controls_tabs.insertTab(0, overview_page, "Overview")
            controls_tabs.setCurrentIndex(0)
        layout.addWidget(controls_tabs)
        self._sync_value_mode_options(self.top_value_mode_combo, self._display_mode(self.top_display_combo))
        self._sync_value_mode_options(self.bottom_value_mode_combo, self._display_mode(self.bottom_display_combo))
        self._apply_default_value_mode("top")
        self._apply_default_value_mode("bottom")
        self._apply_default_axis_modes("top")
        self._apply_default_axis_modes("bottom")
        self._update_axis_labels()
        self._set_marker_fields(self.top_marker_fields, "off")
        self._set_marker_fields(self.bottom_marker_fields, "off")
        return container

    def _toggle_control_panel(self, visible: bool) -> None:
        self._controls_visible = visible
        if hasattr(self, "control_panel_action") and self.control_panel_action.isChecked() != visible:
            self.control_panel_action.blockSignals(True)
            self.control_panel_action.setChecked(visible)
            self.control_panel_action.blockSignals(False)
        if hasattr(self, "controls_button") and self.controls_button.isChecked() != visible:
            self.controls_button.blockSignals(True)
            self.controls_button.setChecked(visible)
            self.controls_button.blockSignals(False)
        if not hasattr(self, "main_splitter"):
            return
        if visible:
            sizes = self._controls_last_sizes
            if len(sizes) != 2 or sizes[0] <= 0:
                sizes = [430, 970]
            self.main_splitter.setSizes(sizes)
            self.statusBar().showMessage("Control panel shown")
            return
        current_sizes = self.main_splitter.sizes()
        if len(current_sizes) == 2 and current_sizes[0] > 0:
            self._controls_last_sizes = current_sizes
        self.main_splitter.setSizes([0, 1])
        self.statusBar().showMessage("Control panel hidden")

    def _build_general_panel(self) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox("General")
        form = QtWidgets.QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)
        form.addRow("Layout", self.layout_mode_combo)
        form.addRow("Overlay", self.overlay_checkbox)
        return panel

    def _build_routing_panel(self) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox("Routing")
        form = QtWidgets.QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)
        form.addRow("Reference", self.reference_channel_combo)
        form.addRow("Responses", self.response_channel_list)
        return panel

    def _build_status_panel(self) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox("Status")
        form = QtWidgets.QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)
        form.addRow("Device", self.device_info_label)
        form.addRow("Run", self.run_info_label)
        return panel

    def _build_display_panel(
        self,
        title: str,
        display_combo: QtWidgets.QComboBox,
        value_combo: QtWidgets.QComboBox,
        xscale_combo: QtWidgets.QComboBox,
        yscale_combo: QtWidgets.QComboBox,
        trace_combo: QtWidgets.QComboBox,
    ) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(panel)
        form.setContentsMargins(8, 4, 8, 4)
        form.addRow("Mode", display_combo)
        form.addRow("Value", value_combo)
        form.addRow("X Scale", xscale_combo)
        form.addRow("Y Scale", yscale_combo)
        form.addRow("Trace", trace_combo)
        return panel

    def _build_marker_fields(self, prefix: str) -> dict[str, QtWidgets.QLabel]:
        fields: dict[str, QtWidgets.QLabel] = {}
        for key in ("trace", "x1", "y1", "x2", "y2", "dx", "dy"):
            fields[key] = QtWidgets.QLabel("--")
            fields[key].setMinimumWidth(70 if key == "trace" else 58)
        return fields

    def _build_marker_panel(
        self, title: str, fields: dict[str, QtWidgets.QLabel]
    ) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox(f"{title} Marker Readout")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        button_row = QtWidgets.QHBoxLayout()
        peak_button = QtWidgets.QToolButton()
        peak_button.setText("^")
        valley_button = QtWidgets.QToolButton()
        valley_button.setText("v")
        mark_button = QtWidgets.QToolButton()
        mark_button.setText("Mark")
        mark_button.setCheckable(True)
        data_tip_button = QtWidgets.QToolButton()
        data_tip_button.setText("Data Tip")
        data_tip_button.setCheckable(True)
        clear_button = QtWidgets.QToolButton()
        clear_button.setText("Clr")
        clear_tip_button = QtWidgets.QToolButton()
        clear_tip_button.setText("Clr Tip")
        button_row.addWidget(peak_button)
        button_row.addWidget(valley_button)
        button_row.addWidget(mark_button)
        button_row.addWidget(data_tip_button)
        button_row.addWidget(clear_button)
        button_row.addWidget(clear_tip_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        form = QtWidgets.QFormLayout()
        form.addRow("Trace", fields["trace"])
        form.addRow("X1", fields["x1"])
        form.addRow("Y1", fields["y1"])
        form.addRow("X2", fields["x2"])
        form.addRow("Y2", fields["y2"])
        form.addRow("dX", fields["dx"])
        form.addRow("dY", fields["dy"])
        layout.addLayout(form)
        key = title.lower()
        peak_button.clicked.connect(lambda _checked=False, plot_key=key: self._find_trace_extremum(plot_key, "peak"))
        valley_button.clicked.connect(lambda _checked=False, plot_key=key: self._find_trace_extremum(plot_key, "valley"))
        mark_button.clicked.connect(lambda _checked=False, plot_key=key: self._toggle_mark_at_cursor(plot_key))
        data_tip_button.clicked.connect(self._toggle_data_tips)
        clear_button.clicked.connect(lambda _checked=False, plot_key=key: self._clear_marker_pair(plot_key))
        clear_tip_button.clicked.connect(lambda _checked=False, plot_key=key: self._clear_data_tips(plot_key))
        if key == "top":
            self.top_mark_button = mark_button
            self.top_data_tip_button = data_tip_button
        else:
            self.bottom_mark_button = mark_button
            self.bottom_data_tip_button = data_tip_button
        return panel

    def _build_session_tab(self):
        widget = QtWidgets.QWidget()
        self.session_widget = widget
        form = QtWidgets.QFormLayout(widget)
        self.session_title_edit = QtWidgets.QLineEdit()
        self.session_notes_edit = QtWidgets.QPlainTextEdit()
        self.session_notes_edit.setMinimumHeight(140)
        form.addRow("Title", self.session_title_edit)
        form.addRow("Notes", self.session_notes_edit)
        return widget

    def _build_legacy_left_panel(self) -> QtWidgets.QWidget:
        self._channels_backend_widget = self._build_channels_tab()
        self._channels_backend_widget.setParent(self)
        self._channels_backend_widget.hide()
        self._acquisition_backend_widget = self._build_acquisition_tab()
        self._acquisition_backend_widget.setParent(self)
        self._acquisition_backend_widget.hide()
        panel = QtWidgets.QWidget()
        panel.setObjectName("legacyLeftPanel")
        panel.setStyleSheet(self._legacy_left_panel_stylesheet())
        layout = QtWidgets.QGridLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._build_legacy_channel_panel(), 0, 0)
        layout.addWidget(self._build_legacy_frequency_panel(), 0, 1)
        layout.addWidget(self._build_legacy_processing_panel(), 1, 0)
        layout.addWidget(self._build_legacy_trigger_panel(), 1, 1)
        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        return panel

    def _legacy_left_panel_stylesheet(self, theme: dict[str, object] | None = None) -> str:
        theme = theme or self._theme()
        return self._theme_stylesheet(
            """
            QWidget#legacyLeftPanel {
                background: @window_bg@;
            }
            QFrame#legacyPanel {
                background: @panel_bg@;
                border: 1px solid @border@;
                border-radius: 12px;
            }
            QLabel#legacyPanelTitle {
                background: @accent@;
                color: #ffffff;
                font-weight: bold;
                font-size: 10pt;
                padding: 4px 6px;
                border-radius: 7px;
            }
            QLabel#legacyText {
                background: @cell_bg@;
                color: @label_text@;
                font-weight: bold;
                font-size: 8pt;
                padding: 4px;
                border-radius: 6px;
            }
            QLabel#legacyCell {
                background: @panel_bg_alt@;
                color: @text@;
                font-weight: bold;
                font-size: 8pt;
                padding: 4px;
                border: 1px solid @control_border@;
                border-radius: 6px;
            }
            QLabel#legacyGrayCell {
                background: @cell_bg@;
                color: @label_text@;
                font-weight: bold;
                font-size: 8pt;
                padding: 4px;
                border-radius: 6px;
            }
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
                background: @panel_bg_alt@;
                color: @text@;
                font-weight: bold;
                font-size: 8pt;
                min-height: 22px;
                border: 1px solid @control_border@;
                border-radius: 6px;
                padding: 1px 16px 1px 5px;
                selection-background-color: @accent@;
                selection-color: #ffffff;
            }
            QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid @accent_alt@;
            }
            QSpinBox#legacyNoArrowSpinBox, QDoubleSpinBox#legacyNoArrowSpinBox {
                padding: 1px 5px;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 16px;
                height: 12px;
                border-left: 1px solid @border@;
                border-bottom: 1px solid @border@;
                background: @cell_bg@;
                border-top-right-radius: 6px;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 16px;
                height: 12px;
                border-left: 1px solid @border@;
                background: @cell_bg@;
                border-bottom-right-radius: 6px;
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 6px solid @label_text@;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid @label_text@;
            }
            QPushButton {
                background: @accent@;
                color: #ffffff;
                font-weight: bold;
                font-size: 8pt;
                min-height: 22px;
                border: 1px solid @accent_hover@;
                border-radius: 6px;
                padding: 1px 5px;
            }
            QPushButton:hover {
                background: @accent_hover@;
                border-color: @accent@;
            }
            QPushButton:pressed {
                background: @accent_hover@;
            }
            QPushButton:disabled {
                background: @disabled_bg@;
                color: @disabled_text@;
                border-color: @border@;
            }
            QCheckBox {
                background: @cell_bg@;
                color: @text@;
                font-weight: bold;
                font-size: 8pt;
                min-height: 22px;
                padding: 1px 5px;
                border: 1px solid @control_border@;
                border-radius: 6px;
            }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
            }
            QCheckBox::indicator:unchecked {
                background: @table_bg@;
                border: 1px solid @control_border@;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background: @accent_alt@;
                border: 1px solid @axis@;
                border-radius: 3px;
            }
            QSlider::groove:horizontal {
                background: @panel_bg_alt@;
                height: 20px;
                border: 1px solid @control_border@;
                border-radius: 10px;
            }
            QSlider::handle:horizontal {
                background: @accent_alt@;
                width: 12px;
                margin: -2px 0px;
                border: 1px solid @label_text@;
                border-radius: 6px;
            }
        """,
            theme,
        )

    @staticmethod
    def _legacy_panel(title: str) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        panel = QtWidgets.QFrame()
        panel.setObjectName("legacyPanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(8)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("legacyPanelTitle")
        title_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title_label)
        return panel, layout

    @staticmethod
    def _legacy_label(text: str, object_name: str = "legacyCell") -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName(object_name)
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    def _build_legacy_channel_panel(self) -> QtWidgets.QWidget:
        panel, layout = self._legacy_panel("CHANNEL SETUP")
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.channel_select_combo, 1)
        top_row.addWidget(self.channel_enabled_checkbox, 1)
        top_row.addWidget(self._legacy_label("", "legacyCell"), 0)
        layout.addLayout(top_row)

        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(self.channel_full_scale_combo, 1)
        range_row.addWidget(self.channel_coupling_combo, 1)
        layout.addLayout(range_row)
        layout.addStretch(1)

        layout.addWidget(self._legacy_label("Channel Label", "legacyGrayCell"))
        layout.addWidget(self.channel_label_edit)

        eu_row = QtWidgets.QHBoxLayout()
        eu_row.setSpacing(4)
        self.channel_sensitivity_edit.setFixedWidth(48)
        self.channel_sensitivity_edit.setAlignment(QtCore.Qt.AlignCenter)
        self.channel_sensitivity_edit.setObjectName("legacyNoArrowSpinBox")
        self.channel_sensitivity_edit.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.channel_unit_edit.setMinimumWidth(76)
        self.channel_unit_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        unit_suffix = self._legacy_label("/V", "legacyGrayCell")
        unit_suffix.setFixedWidth(24)
        eu_row.addWidget(self.channel_sensitivity_edit, 0)
        eu_row.addWidget(self.channel_unit_edit, 1)
        eu_row.addWidget(unit_suffix, 0)
        layout.addLayout(eu_row)

        db_row = QtWidgets.QHBoxLayout()
        db_row.addStretch(1)
        db_row.addWidget(self.channel_db_ref_edit, 1)
        db_row.addWidget(self._legacy_label("0dB", "legacyGrayCell"), 0)
        layout.addLayout(db_row)
        return panel

    def _build_legacy_frequency_panel(self) -> QtWidgets.QWidget:
        panel, layout = self._legacy_panel("FREQUENCY RNG")
        self.bandwidth_combo = QtWidgets.QComboBox()
        self.bandwidth_combo.addItems(list(self.BANDWIDTH_OPTIONS_HZ))
        self.bandwidth_combo.setCurrentText("BW=1.0KHz")
        self.bandwidth_combo.currentTextChanged.connect(self._bandwidth_changed)
        self._make_compact_combo(self.bandwidth_combo, 9)
        self.aa_filters_button = QtWidgets.QPushButton("AA Filter")
        self.aa_filters_button.setEnabled(False)
        self.aa_filters_button.setToolTip(
            "AA Filters On. NI USB-4431 anti-alias filtering is hardware-managed by DAQmx; "
            "this legacy indicator is shown for reference and is not a user toggle."
        )
        layout.addWidget(self.bandwidth_combo)
        layout.addWidget(self.aa_filters_button)
        layout.addWidget(self._legacy_label("Record Length", "legacyGrayCell"))
        length_row = QtWidgets.QHBoxLayout()
        length_row.setSpacing(4)
        length_min_label = self._legacy_label("2048", "legacyText")
        length_min_label.setFixedWidth(40)
        length_max_label = self._legacy_label("8192", "legacyText")
        length_max_label.setFixedWidth(40)
        self.frame_size_edit.setFixedWidth(66)
        self.frame_size_edit.setAlignment(QtCore.Qt.AlignCenter)
        self.frame_size_edit.setObjectName("legacyNoArrowSpinBox")
        self.frame_size_edit.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        length_row.addWidget(length_min_label)
        length_row.addWidget(self.frame_size_edit, 1)
        length_row.addWidget(length_max_label)
        layout.addLayout(length_row)
        self.frame_size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_size_slider.setRange(2048, 8192)
        self.frame_size_slider.setSingleStep(2048)
        self.frame_size_slider.setPageStep(2048)
        self.frame_size_slider.setValue(4096)
        self.frame_size_slider.valueChanged.connect(self._frame_size_slider_changed)
        self.frame_size_edit.valueChanged.connect(self._frame_size_edit_changed)
        layout.addWidget(self.frame_size_slider)
        layout.addStretch(1)
        self.df_label = self._legacy_label("dF=0.625 Hz", "legacyGrayCell")
        layout.addWidget(self.df_label)
        return panel

    def _build_legacy_processing_panel(self) -> QtWidgets.QWidget:
        panel, layout = self._legacy_panel("PROCESSING")
        layout.addWidget(self.average_mode_combo)
        layout.addWidget(self._legacy_label("Stop at Count", "legacyGrayCell"))
        count_row = QtWidgets.QHBoxLayout()
        count_row.addWidget(self._legacy_label("1", "legacyText"))
        count_row.addWidget(self.average_count_edit, 1)
        count_row.addWidget(self._legacy_label("1000", "legacyText"))
        layout.addLayout(count_row)
        self.average_count_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.average_count_slider.setRange(1, 1000)
        self.average_count_slider.setValue(20)
        self.average_count_slider.valueChanged.connect(self.average_count_edit.setValue)
        self.average_count_edit.valueChanged.connect(self.average_count_slider.setValue)
        layout.addWidget(self.average_count_slider)
        self.reject_combo = QtWidgets.QComboBox()
        self.reject_combo.addItems(
            ["No Reject", "Overload Reject", "Double Hit Reject", "Both Reject"]
        )
        self.reject_combo.setToolTip(
            "Reject mode: No Reject / Overload Reject / Double Hit Reject / Both Reject"
        )
        self._make_compact_combo(self.reject_combo, 9)
        self.reject_combo.currentTextChanged.connect(self._reject_mode_changed)
        self.overlap_combo = QtWidgets.QComboBox()
        self.overlap_combo.addItems(["No Overlap", "50% Overlap", "Max Overlap"])
        self.overlap_combo.setCurrentText("No Overlap")
        self._make_compact_combo(self.overlap_combo, 9)
        self.overlap_combo.setToolTip(
            "Overlap processing: No / 50% / Max. Applies to Inst/Avg processing frames; Record still stores raw time data."
        )
        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.addItems(list(self.PROCESSING_WINDOW_LABELS.values()))
        self.window_combo.setToolTip("Processing window")
        self._make_compact_combo(self.window_combo, 9)
        self.window_combo.currentTextChanged.connect(self._processing_window_changed)
        layout.addWidget(self.reject_combo)
        layout.addWidget(self.overlap_combo)
        layout.addWidget(self.window_combo)
        return panel

    def _build_legacy_trigger_panel(self) -> QtWidgets.QWidget:
        panel, layout = self._legacy_panel("TRIGGER")
        layout.addWidget(self.trigger_mode_combo)

        source_row = QtWidgets.QHBoxLayout()
        source_row.addWidget(self.trigger_source_combo, 1)
        source_row.addWidget(self.trigger_level_percent_combo, 1)
        layout.addLayout(source_row)

        slope_row = QtWidgets.QHBoxLayout()
        slope_row.addStretch(1)
        if not hasattr(self, "trigger_slope_button"):
            self.trigger_slope_button = QtWidgets.QPushButton("Pos")
            self.trigger_slope_button.setMinimumWidth(54)
            self.trigger_slope_button.clicked.connect(self._toggle_trigger_slope)
        slope_row.addWidget(self.trigger_slope_button)
        slope_row.addStretch(1)
        layout.addLayout(slope_row)

        layout.addWidget(self._legacy_label("Delay", "legacyGrayCell"))
        delay_row = QtWidgets.QHBoxLayout()
        delay_row.addWidget(self._legacy_label("-100", "legacyText"))
        delay_row.addWidget(self.pretrigger_samples_edit, 1)
        delay_row.addWidget(self._legacy_label("100", "legacyText"))
        layout.addLayout(delay_row)
        layout.addWidget(self.trigger_delay_slider)
        layout.addWidget(self.trigger_enable)
        return panel

    def _build_channels_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.channel_list = QtWidgets.QListWidget()
        self.channel_list.hide()
        self.channel_list.currentRowChanged.connect(self._load_channel_editor_from_row)

        matrix_group = QtWidgets.QGroupBox("MC Setup")
        matrix_group.hide()
        matrix_layout = QtWidgets.QVBoxLayout(matrix_group)
        matrix_layout.setContentsMargins(6, 6, 6, 6)
        self.channel_grid = QtWidgets.QTableWidget(0, 9)
        self.channel_grid.setHorizontalHeaderLabels(
            ["On", "Chan", "Full Scale", "Coupling", "Offset", "Label", "EU/Volt", "Per EU", "0 dB Ref"]
        )
        self.channel_grid.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.channel_grid.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.channel_grid.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.channel_grid.setAlternatingRowColors(True)
        self.channel_grid.verticalHeader().setVisible(False)
        self.channel_grid.setMinimumHeight(210)
        self.channel_grid.setItemDelegateForColumn(
            0, ComboBoxDelegate(["On", "Off"], self.channel_grid)
        )
        self.channel_grid.setItemDelegateForColumn(
            2,
            ComboBoxDelegate(
                [self._format_full_scale_option(value) for value in self.CHANNEL_FULL_SCALE_OPTIONS] + ["Auto"],
                self.channel_grid,
            ),
        )
        self.channel_grid.setItemDelegateForColumn(
            3, ComboBoxDelegate(self.CHANNEL_COUPLING_OPTIONS, self.channel_grid)
        )
        self.channel_grid.setItemDelegateForColumn(
            7, ComboBoxDelegate(self.CHANNEL_PER_EU_OPTIONS, self.channel_grid)
        )
        self.channel_grid.currentCellChanged.connect(self._channel_grid_row_changed)
        self.channel_grid.itemChanged.connect(self._channel_grid_item_changed)
        grid_header = self.channel_grid.horizontalHeader()
        grid_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        grid_header.setMinimumSectionSize(64)
        self.channel_grid.setColumnWidth(0, 62)
        self.channel_grid.setColumnWidth(1, 62)
        self.channel_grid.setColumnWidth(2, 96)
        self.channel_grid.setColumnWidth(3, 84)
        self.channel_grid.setColumnWidth(4, 72)
        self.channel_grid.setColumnWidth(5, 96)
        self.channel_grid.setColumnWidth(6, 82)
        self.channel_grid.setColumnWidth(7, 76)
        self.channel_grid.setColumnWidth(8, 76)
        matrix_layout.addWidget(self.channel_grid)
        self.channel_grid_group = matrix_group

        editor_panel = QtWidgets.QWidget()
        self.channel_enabled_checkbox = QtWidgets.QCheckBox("Enabled")
        self.channel_select_combo = QtWidgets.QComboBox()
        self.channel_select_combo.setEditable(False)
        self.channel_name_edit = QtWidgets.QLineEdit()
        self.channel_name_edit.setReadOnly(True)
        self.channel_name_edit.hide()
        self.channel_physical_edit = QtWidgets.QLineEdit()
        self.channel_label_edit = QtWidgets.QLineEdit()
        self.channel_reference_checkbox = QtWidgets.QCheckBox("Reference")
        self.channel_iepe_checkbox = QtWidgets.QCheckBox("IEPE")
        self.channel_iepe_current_edit = CompactDoubleSpinBox()
        self.channel_iepe_current_edit.setRange(0.0, 20.0)
        self.channel_iepe_current_edit.setDecimals(3)
        self.channel_coupling_combo = QtWidgets.QComboBox()
        self.channel_coupling_combo.addItems(self.CHANNEL_COUPLING_OPTIONS)
        self.channel_sensitivity_edit = CompactDoubleSpinBox()
        self.channel_sensitivity_edit.setRange(0.0, 1_000_000.0)
        self.channel_sensitivity_edit.setDecimals(6)
        self.channel_unit_edit = QtWidgets.QLineEdit()
        self.channel_offset_edit = QtWidgets.QDoubleSpinBox()
        self.channel_offset_edit.setRange(-8.0, 8.0)
        self.channel_offset_edit.setDecimals(6)
        self.channel_per_eu_combo = QtWidgets.QComboBox()
        self.channel_per_eu_combo.addItems(self.CHANNEL_PER_EU_OPTIONS)
        self.channel_db_ref_edit = CompactDoubleSpinBox()
        self.channel_db_ref_edit.setRange(-1_000_000.0, 1_000_000.0)
        self.channel_db_ref_edit.setDecimals(6)
        self.channel_full_scale_combo = QtWidgets.QComboBox()
        for value in self.CHANNEL_FULL_SCALE_OPTIONS:
            self.channel_full_scale_combo.addItem(self._format_full_scale_option(value), value)
        self.channel_full_scale_combo.addItem("Auto", -1.0)

        self.channel_table = QtWidgets.QTableWidget(0, 15)
        self.channel_table.setHorizontalHeaderLabels(
            [
                "Enabled",
                "Name",
                "Physical",
                "Reference",
                "IEPE",
                "IEPE mA",
                "Coupling",
                "Sensitivity",
                "Unit",
                "Full Scale",
                "Label",
                "Offset",
                "Per EU",
                "0 dB Ref",
                "Reserved",
            ]
        )
        header = self.channel_table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setMinimumSectionSize(72)
        self.channel_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.channel_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.channel_table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.AdjustToContentsOnFirstShow
        )
        self.channel_table.setWordWrap(False)
        self.channel_table.hide()

        editor_layout = QtWidgets.QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)

        button_row = QtWidgets.QHBoxLayout()
        self.channel_apply_button = QtWidgets.QPushButton("Apply")
        self.channel_mc_setup_button = QtWidgets.QPushButton("MC Setup...")
        self.channel_set_all_checkbox = QtWidgets.QCheckBox("Set All")
        self.channel_auto_range_button = QtWidgets.QPushButton("Auto Range")
        self.channel_copy_first_button = QtWidgets.QPushButton("Copy Ch1")
        button_row.addWidget(self.channel_apply_button)
        button_row.addWidget(self.channel_mc_setup_button)
        button_row.addWidget(self.channel_set_all_checkbox)
        button_row.addWidget(self.channel_auto_range_button)
        button_row.addWidget(self.channel_copy_first_button)
        button_row.addStretch(1)
        editor_layout.addLayout(button_row)

        basic_group = QtWidgets.QGroupBox("CHANNEL SETUP")
        basic_form = QtWidgets.QFormLayout(basic_group)
        basic_form.setContentsMargins(8, 6, 8, 6)
        basic_form.addRow(self.channel_enabled_checkbox)
        basic_form.addRow("Channel", self.channel_select_combo)
        basic_form.addRow("Label", self.channel_label_edit)
        basic_form.addRow(self.channel_reference_checkbox)
        basic_form.addRow("Coupling", self.channel_coupling_combo)
        basic_form.addRow("Full Scale", self.channel_full_scale_combo)
        basic_form.addRow("Offset", self.channel_offset_edit)
        editor_layout.addWidget(basic_group)

        advanced_group = QtWidgets.QGroupBox("Advanced NI Device Defaults")
        advanced_group.setCheckable(True)
        advanced_group.setChecked(True)
        advanced_group.setMinimumHeight(260)
        advanced_group.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.MinimumExpanding,
        )
        advanced_layout = QtWidgets.QVBoxLayout(advanced_group)
        advanced_layout.setContentsMargins(8, 8, 8, 8)
        advanced_layout.setSpacing(8)
        hardware_form = QtWidgets.QFormLayout()
        hardware_form.addRow("Physical", self.channel_physical_edit)
        advanced_layout.addLayout(hardware_form)

        sensor_group = QtWidgets.QGroupBox("Sensor / IEPE")
        sensor_form = QtWidgets.QFormLayout(sensor_group)
        sensor_form.setContentsMargins(8, 6, 8, 6)
        sensor_form.addRow(self.channel_iepe_checkbox)
        sensor_form.addRow("IEPE mA", self.channel_iepe_current_edit)
        advanced_layout.addWidget(sensor_group)

        eu_group = QtWidgets.QGroupBox("Engineering Units")
        eu_form = QtWidgets.QFormLayout(eu_group)
        eu_form.setContentsMargins(8, 6, 8, 6)
        eu_form.addRow("EU/Volt", self.channel_sensitivity_edit)
        eu_form.addRow("EU Label", self.channel_unit_edit)
        eu_form.addRow("Per EU", self.channel_per_eu_combo)
        eu_form.addRow("0 dB Ref", self.channel_db_ref_edit)
        advanced_layout.addWidget(eu_group)
        editor_layout.addWidget(advanced_group, 1)
        layout.addWidget(editor_panel, 2)

        self.channel_enabled_checkbox.toggled.connect(self._apply_channel_editor_to_row)
        self.channel_reference_checkbox.toggled.connect(self._apply_channel_editor_to_row)
        self.channel_iepe_checkbox.toggled.connect(self._apply_channel_editor_to_row)
        self.channel_iepe_current_edit.valueChanged.connect(self._apply_channel_editor_to_row)
        self.channel_coupling_combo.currentTextChanged.connect(self._apply_channel_editor_to_row)
        self.channel_offset_edit.valueChanged.connect(self._apply_channel_editor_to_row)
        self.channel_sensitivity_edit.valueChanged.connect(self._apply_channel_editor_to_row)
        self.channel_per_eu_combo.currentTextChanged.connect(self._apply_channel_editor_to_row)
        self.channel_db_ref_edit.valueChanged.connect(self._apply_channel_editor_to_row)
        self.channel_full_scale_combo.currentTextChanged.connect(self._apply_channel_editor_to_row)
        self.channel_select_combo.currentIndexChanged.connect(self._channel_select_combo_changed)
        self.channel_physical_edit.editingFinished.connect(self._apply_channel_editor_to_row)
        self.channel_label_edit.editingFinished.connect(self._apply_channel_editor_to_row)
        self.channel_unit_edit.editingFinished.connect(self._apply_channel_editor_to_row)
        self.channel_apply_button.clicked.connect(self._apply_channel_editor_to_row)
        self.channel_mc_setup_button.clicked.connect(self._open_mc_setup_dialog)
        self.channel_auto_range_button.clicked.connect(self._channel_auto_range_current)
        self.channel_copy_first_button.clicked.connect(self._channel_copy_from_first)
        return widget

    @staticmethod
    def _format_full_scale_option(value: float) -> str:
        if value >= 1.0:
            return f"{value:.3g} V"
        millivolts = value * 1000.0
        if millivolts >= 1.0:
            return f"{millivolts:.3g} mV"
        return f"{value:.6g} V"

    @staticmethod
    def _parse_full_scale_text(text: str, default: float) -> float:
        stripped = str(text).strip().lower()
        if stripped == "auto":
            return -1.0
        multiplier = 1.0
        if stripped.endswith("mv"):
            multiplier = 1e-3
            stripped = stripped[:-2]
        elif stripped.endswith("uv"):
            multiplier = 1e-6
            stripped = stripped[:-2]
        elif stripped.endswith("kv"):
            multiplier = 1e3
            stripped = stripped[:-2]
        elif stripped.endswith("v"):
            stripped = stripped[:-1]
        try:
            return float(stripped.strip()) * multiplier
        except ValueError:
            return default

    def _full_scale_from_combo(self) -> float:
        match_index = self.channel_full_scale_combo.findText(
            self.channel_full_scale_combo.currentText(),
            QtCore.Qt.MatchFixedString,
        )
        if match_index >= 0:
            data = self.channel_full_scale_combo.itemData(match_index)
            if data is not None:
                return float(data)
        return self._parse_full_scale_text(self.channel_full_scale_combo.currentText(), 10.0)

    def _rebuild_channel_list(self) -> None:
        if not hasattr(self, "channel_list"):
            return
        current_row = self.channel_list.currentRow()
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        if hasattr(self, "channel_select_combo"):
            self.channel_select_combo.blockSignals(True)
            self.channel_select_combo.clear()
        for row in range(self.channel_table.rowCount()):
            name = self.channel_table.item(row, 1).text()
            label_item = self.channel_table.item(row, 10) if self.channel_table.columnCount() > 10 else None
            label_text = label_item.text().strip() if label_item is not None else ""
            enabled = self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked
            display_name = f"{name}  {label_text}".strip() if label_text else name
            item_text = display_name if enabled else f"{display_name} (off)"
            self.channel_list.addItem(item_text)
            if hasattr(self, "channel_select_combo"):
                self.channel_select_combo.addItem(item_text, row)
        self.channel_list.blockSignals(False)
        if self.channel_list.count():
            target_row = current_row if 0 <= current_row < self.channel_list.count() else 0
            self.channel_list.setCurrentRow(target_row)
            if hasattr(self, "channel_select_combo"):
                self.channel_select_combo.setCurrentIndex(target_row)
        else:
            self._load_channel_editor_from_row(-1)
        if hasattr(self, "channel_select_combo"):
            self.channel_select_combo.blockSignals(False)
        self._sync_channel_grid()

    def _sync_channel_select_combo(self, row: int) -> None:
        if not hasattr(self, "channel_select_combo"):
            return
        self.channel_select_combo.blockSignals(True)
        if 0 <= row < self.channel_select_combo.count():
            self.channel_select_combo.setCurrentIndex(row)
        else:
            self.channel_select_combo.setCurrentIndex(-1)
        self.channel_select_combo.blockSignals(False)

    def _channel_select_combo_changed(self, index: int) -> None:
        if self._channel_editor_loading or not hasattr(self, "channel_list"):
            return
        row = self.channel_select_combo.itemData(index)
        if row is None:
            row = index
        row = int(row)
        if 0 <= row < self.channel_list.count() and self.channel_list.currentRow() != row:
            self.channel_list.setCurrentRow(row)

    def _sync_channel_grid(self) -> None:
        if not hasattr(self, "channel_grid"):
            return
        current_row = self.channel_list.currentRow()
        self._channel_editor_loading = True
        self.channel_grid.blockSignals(True)
        self.channel_grid.setRowCount(self.channel_table.rowCount())
        for row in range(self.channel_table.rowCount()):
            enabled = self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked
            name = self.channel_table.item(row, 1).text()
            physical = self.channel_table.item(row, 2).text()
            label = self.channel_table.item(row, 10).text()
            coupling = self.channel_table.item(row, 6).text()
            full_scale = self.channel_table.item(row, 9).text()
            offset = self.channel_table.item(row, 11).text()
            sensitivity = self.channel_table.item(row, 7).text()
            per_eu = self.channel_table.item(row, 12).text()
            db_ref = self.channel_table.item(row, 13).text()
            row_values = [
                "On" if enabled else "Off",
                name,
                "Auto" if full_scale.startswith("-") else full_scale,
                coupling,
                offset,
                label,
                sensitivity,
                per_eu,
                db_ref,
            ]
            for col, value in enumerate(row_values):
                item = self.channel_grid.item(row, col)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.channel_grid.setItem(row, col, item)
                item.setText(value)
                if col == 1:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    item.setToolTip(physical)
                else:
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        if 0 <= current_row < self.channel_grid.rowCount():
            self.channel_grid.selectRow(current_row)
            self.channel_grid.setCurrentCell(current_row, 0)
        self.channel_grid.blockSignals(False)
        self._channel_editor_loading = False

    def _channel_grid_row_changed(self, current_row: int, _current_column: int, _prev_row: int, _prev_column: int) -> None:
        if hasattr(self, "channel_list") and self.channel_list.currentRow() != current_row:
            self.channel_list.setCurrentRow(current_row)

    def _channel_grid_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._channel_editor_loading:
            return
        row = item.row()
        col = item.column()
        if row < 0 or row >= self.channel_table.rowCount():
            return
        text = item.text().strip()
        old_aliases = self._channel_aliases_for_table_row(row)
        old_full_scale = self._channel_table_full_scale(row)
        if col == 0:
            self.channel_table.item(row, 0).setCheckState(
                QtCore.Qt.Checked if text.lower() in {"on", "1", "true", "yes"} else QtCore.Qt.Unchecked
            )
        elif col == 2:
            self.channel_table.item(row, 9).setText(
                f"{self._parse_full_scale_text(text, 10.0):.6g}"
            )
        elif col == 3:
            self.channel_table.item(row, 6).setText(text or "ac")
            self._apply_bias_defaults_to_table_row(row)
        elif col == 4:
            self.channel_table.item(row, 11).setText(f"{self._parse_float(text, 0.0):.6g}")
        elif col == 5:
            self.channel_table.item(row, 10).setText(text)
        elif col == 6:
            self.channel_table.item(row, 7).setText(f"{self._parse_float(text, 1.0):.6g}")
        elif col == 7:
            self.channel_table.item(row, 12).setText(text or "/Volt")
        elif col == 8:
            self.channel_table.item(row, 13).setText(f"{self._parse_float(text, 1.0):.6g}")
        else:
            return
        self._rebuild_channel_list()
        self.channel_list.setCurrentRow(row)
        self._reload_channel_selectors(include_new_responses=(col == 0))
        self._read_session_from_widgets()
        if col == 2:
            new_aliases = self._channel_aliases_for_table_row(row)
            new_full_scale = self._channel_table_full_scale(row)
            if not np.isclose(old_full_scale, new_full_scale, rtol=1e-12, atol=1e-12):
                self._refresh_full_scale_axis_ranges_for_channels(
                    list(old_aliases | new_aliases),
                    force_auto_y=True,
                )

    def _apply_bias_defaults_to_table_row(self, row: int) -> None:
        coupling = self.channel_table.item(row, 6).text().strip().lower()
        if coupling != "bias":
            self.channel_table.item(row, 4).setCheckState(QtCore.Qt.Unchecked)
            return
        self.channel_table.item(row, 4).setCheckState(QtCore.Qt.Checked)
        self.channel_table.item(row, 5).setText(f"{self.DEFAULT_IEPE_CURRENT_MA:.6g}")
        if self._parse_float(self.channel_table.item(row, 7).text(), 0.0) <= 1.0:
            self.channel_table.item(row, 7).setText(f"{self.DEFAULT_EU_PER_VOLT:.6g}")
        if not self.channel_table.item(row, 8).text().strip():
            self.channel_table.item(row, 8).setText(self.DEFAULT_ENGINEERING_UNIT)

    @staticmethod
    def _parse_float(text: str, default: float) -> float:
        try:
            return float(text)
        except ValueError:
            return default

    def _load_channel_editor_from_row(self, row: int) -> None:
        self._channel_editor_loading = True
        widgets = (
            self.channel_select_combo,
            self.channel_enabled_checkbox,
            self.channel_name_edit,
            self.channel_physical_edit,
            self.channel_label_edit,
            self.channel_reference_checkbox,
            self.channel_iepe_checkbox,
            self.channel_iepe_current_edit,
            self.channel_coupling_combo,
            self.channel_offset_edit,
            self.channel_sensitivity_edit,
            self.channel_unit_edit,
            self.channel_per_eu_combo,
            self.channel_db_ref_edit,
            self.channel_full_scale_combo,
        )
        valid = 0 <= row < self.channel_table.rowCount()
        for widget in widgets:
            widget.setEnabled(valid)
        if not valid:
            self.channel_enabled_checkbox.setChecked(False)
            self.channel_name_edit.setText("")
            self.channel_physical_edit.setText("")
            self.channel_label_edit.setText("")
            self.channel_reference_checkbox.setChecked(False)
            self.channel_iepe_checkbox.setChecked(False)
            self.channel_iepe_current_edit.setValue(0.0)
            self.channel_coupling_combo.setCurrentText("ac")
            self.channel_offset_edit.setValue(0.0)
            self.channel_sensitivity_edit.setValue(0.0)
            self.channel_unit_edit.setText("")
            self.channel_per_eu_combo.setCurrentText("/Volt")
            self.channel_db_ref_edit.setValue(1.0)
            self.channel_full_scale_combo.setCurrentText(self._format_full_scale_option(10.0))
            self._sync_channel_select_combo(-1)
            if hasattr(self, "channel_grid"):
                self.channel_grid.clearSelection()
            self._channel_editor_loading = False
            return
        self._sync_channel_select_combo(row)
        if hasattr(self, "channel_grid") and self.channel_grid.currentRow() != row:
            self.channel_grid.blockSignals(True)
            self.channel_grid.selectRow(row)
            self.channel_grid.setCurrentCell(row, 0)
            self.channel_grid.blockSignals(False)
        self.channel_enabled_checkbox.setChecked(
            self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked
        )
        self.channel_name_edit.setText(self.channel_table.item(row, 1).text())
        self.channel_physical_edit.setText(self.channel_table.item(row, 2).text())
        label_item = self.channel_table.item(row, 10) if self.channel_table.columnCount() > 10 else None
        self.channel_label_edit.setText(label_item.text() if label_item is not None else "")
        self.channel_reference_checkbox.setChecked(
            self.channel_table.item(row, 3).checkState() == QtCore.Qt.Checked
        )
        self.channel_iepe_checkbox.setChecked(
            self.channel_table.item(row, 4).checkState() == QtCore.Qt.Checked
        )
        self.channel_iepe_current_edit.setValue(float(self.channel_table.item(row, 5).text()))
        self.channel_coupling_combo.setCurrentText(self.channel_table.item(row, 6).text())
        self.channel_offset_edit.setValue(float(self.channel_table.item(row, 11).text()))
        self.channel_sensitivity_edit.setValue(float(self.channel_table.item(row, 7).text()))
        self.channel_unit_edit.setText(self.channel_table.item(row, 8).text())
        self.channel_per_eu_combo.setCurrentText(self.channel_table.item(row, 12).text())
        self.channel_db_ref_edit.setValue(float(self.channel_table.item(row, 13).text()))
        full_scale_value = float(self.channel_table.item(row, 9).text())
        if full_scale_value < 0.0:
            self.channel_full_scale_combo.setCurrentText("Auto")
        else:
            option_text = self._format_full_scale_option(full_scale_value)
            self.channel_full_scale_combo.setCurrentText(option_text)
        self._channel_editor_loading = False

    def _apply_channel_editor_to_row(self, *_args) -> None:
        if self._channel_editor_loading or not hasattr(self, "channel_list"):
            return
        row = self.channel_list.currentRow()
        if row < 0 or row >= self.channel_table.rowCount():
            return
        sender = self.sender()
        full_scale_only = sender is self.channel_full_scale_combo
        rows = (
            range(self.channel_table.rowCount())
            if self.channel_set_all_checkbox.isChecked()
            else [row]
        )
        target_rows = list(rows)
        old_full_scales = {
            target_row: self._channel_table_full_scale(target_row)
            for target_row in target_rows
        }
        old_aliases = {
            target_row: self._channel_aliases_for_table_row(target_row)
            for target_row in target_rows
        }
        for target_row in target_rows:
            self._write_channel_editor_to_table_row(target_row, full_scale_only=full_scale_only)
        self._rebuild_channel_list()
        self.channel_list.setCurrentRow(row)
        self._reload_channel_selectors(include_new_responses=not full_scale_only)
        self._read_session_from_widgets()
        full_scale_changed_aliases: set[str] = set()
        for target_row in target_rows:
            new_full_scale = self._channel_table_full_scale(target_row)
            if not np.isclose(old_full_scales[target_row], new_full_scale, rtol=1e-12, atol=1e-12):
                full_scale_changed_aliases.update(old_aliases[target_row])
                full_scale_changed_aliases.update(self._channel_aliases_for_table_row(target_row))
        self._refresh_full_scale_axis_ranges_for_channels(
            list(full_scale_changed_aliases)
            if full_scale_changed_aliases
            else [self.channel_table.item(target_row, 1).text() for target_row in target_rows],
            force_auto_y=bool(full_scale_changed_aliases),
        )

    def _write_channel_editor_to_table_row(self, row: int, full_scale_only: bool = False) -> None:
        if full_scale_only:
            self.channel_table.item(row, 9).setText(f"{self._full_scale_from_combo():.6g}")
            return
        self.channel_table.item(row, 0).setCheckState(
            QtCore.Qt.Checked if self.channel_enabled_checkbox.isChecked() else QtCore.Qt.Unchecked
        )
        if not self.channel_set_all_checkbox.isChecked() or row == self.channel_list.currentRow():
            self.channel_table.item(row, 2).setText(self.channel_physical_edit.text().strip())
        self.channel_table.item(row, 3).setCheckState(
            QtCore.Qt.Checked if self.channel_reference_checkbox.isChecked() else QtCore.Qt.Unchecked
        )
        coupling = self.channel_coupling_combo.currentText().strip().lower() or "ac"
        bias_selected = coupling == "bias"
        self.channel_table.item(row, 4).setCheckState(
            QtCore.Qt.Checked if (self.channel_iepe_checkbox.isChecked() or bias_selected) else QtCore.Qt.Unchecked
        )
        iepe_current = self.channel_iepe_current_edit.value() or self.DEFAULT_IEPE_CURRENT_MA
        self.channel_table.item(row, 5).setText(f"{iepe_current:.6g}")
        self.channel_table.item(row, 6).setText(coupling)
        self.channel_table.item(row, 7).setText(f"{self.channel_sensitivity_edit.value():.6g}")
        self.channel_table.item(row, 8).setText(self.channel_unit_edit.text().strip() or "V")
        self._apply_bias_defaults_to_table_row(row)
        self.channel_table.item(row, 9).setText(f"{self._full_scale_from_combo():.6g}")
        label_item = self.channel_table.item(row, 10)
        if label_item is not None and (
            not self.channel_set_all_checkbox.isChecked() or row == self.channel_list.currentRow()
        ):
            label_item.setText(self.channel_label_edit.text().strip())
        self.channel_table.item(row, 11).setText(f"{self.channel_offset_edit.value():.6g}")
        self.channel_table.item(row, 12).setText(self.channel_per_eu_combo.currentText().strip() or "/Volt")
        self.channel_table.item(row, 13).setText(f"{self.channel_db_ref_edit.value():.6g}")

    def _channel_auto_range_current(self) -> None:
        row = self.channel_list.currentRow()
        if row < 0:
            return
        self.channel_full_scale_combo.setCurrentText("Auto")
        self._apply_channel_editor_to_row()
        self.statusBar().showMessage(f"Channel {self.channel_name_edit.text()} full scale set to Auto")

    def _channel_copy_from_first(self) -> None:
        if self.channel_table.rowCount() == 0:
            return
        current_row = self.channel_list.currentRow()
        if current_row <= 0:
            return
        self._channel_editor_loading = True
        source_label = self.channel_table.item(0, 10).text() if self.channel_table.item(0, 10) else ""
        self.channel_enabled_checkbox.setChecked(
            self.channel_table.item(0, 0).checkState() == QtCore.Qt.Checked
        )
        self.channel_physical_edit.setText(self.channel_table.item(current_row, 2).text())
        self.channel_label_edit.setText(source_label)
        self.channel_reference_checkbox.setChecked(
            self.channel_table.item(0, 3).checkState() == QtCore.Qt.Checked
        )
        self.channel_iepe_checkbox.setChecked(
            self.channel_table.item(0, 4).checkState() == QtCore.Qt.Checked
        )
        self.channel_iepe_current_edit.setValue(float(self.channel_table.item(0, 5).text()))
        self.channel_coupling_combo.setCurrentText(self.channel_table.item(0, 6).text())
        self.channel_offset_edit.setValue(float(self.channel_table.item(0, 11).text()))
        self.channel_sensitivity_edit.setValue(float(self.channel_table.item(0, 7).text()))
        self.channel_unit_edit.setText(self.channel_table.item(0, 8).text())
        self.channel_per_eu_combo.setCurrentText(self.channel_table.item(0, 12).text())
        self.channel_db_ref_edit.setValue(float(self.channel_table.item(0, 13).text()))
        full_scale_value = float(self.channel_table.item(0, 9).text())
        self.channel_full_scale_combo.setCurrentText(
            "Auto" if full_scale_value < 0.0 else self._format_full_scale_option(full_scale_value)
        )
        self._channel_editor_loading = False
        self._apply_channel_editor_to_row()
        self.statusBar().showMessage(
            f"Copied channel settings from {self.channel_table.item(0, 1).text()} to {self.channel_name_edit.text()}"
        )

    def _trigger_mode_changed(self, mode: str) -> None:
        normalized = self._current_combo_value(self.trigger_mode_combo).strip().lower()
        if normalized == "off (free run)":
            self.trigger_enable.setChecked(False)
            self.trigger_source_edit.setText("immediate")
        elif self.trigger_source_edit.text().strip().lower() == "immediate":
            self.trigger_source_edit.setText("ai0")
        if "manual" not in normalized:
            self.trigger_enable.setChecked(False)
        self._sync_trigger_arm_button()

    def _toggle_trigger_slope(self) -> None:
        next_text = "Neg" if self.trigger_slope_button.text() == "Pos" else "Pos"
        self.trigger_slope_button.setText(next_text)
        self.trigger_slope_combo.setCurrentText(next_text)

    def _toggle_trigger_arm(self) -> None:
        if "manual" not in self._current_combo_value(self.trigger_mode_combo).strip().lower():
            self.trigger_enable.setChecked(False)
        self._sync_trigger_arm_button()

    def _sync_trigger_arm_button(self) -> None:
        if not hasattr(self, "trigger_enable"):
            return
        manual_mode = "manual" in self._current_combo_value(self.trigger_mode_combo).strip().lower()
        self.trigger_enable.setEnabled(manual_mode)
        self.trigger_enable.setText("Armed" if self.trigger_enable.isChecked() else "Arm")
        self.trigger_enable.setToolTip(
            "Manual trigger arm state. Manual Arm modes require this to be Armed before acquisition."
            if manual_mode
            else "Arm is only used by Manual Arm trigger modes."
        )

    def _trigger_source_combo_changed(self, text: str) -> None:
        if text.startswith("Ch"):
            try:
                channel_index = int(text[2:]) - 1
            except ValueError:
                channel_index = 0
            self.trigger_source_edit.setText(f"ai{max(0, channel_index)}")
            self._trigger_level_percent_changed(self.trigger_level_percent_combo.currentText())

    def _trigger_level_percent_changed(self, text: str) -> None:
        percent = self._parse_trigger_percent_text(text)
        channel_index = self._trigger_channel_index()
        full_scale = self._channel_full_scale_for_trigger(channel_index)
        self.trigger_level_edit.setValue(full_scale * percent / 100.0)

    def _trigger_slope_combo_changed(self, text: str) -> None:
        if hasattr(self, "trigger_slope_button"):
            self.trigger_slope_button.setText(text)

    def _trigger_channel_index(self) -> int:
        text = self.trigger_source_combo.currentText()
        if text.startswith("Ch"):
            try:
                return max(0, int(text[2:]) - 1)
            except ValueError:
                return 0
        return 0

    def _channel_full_scale_for_trigger(self, channel_index: int) -> float:
        if 0 <= channel_index < self.channel_table.rowCount():
            full_scale = self._parse_float(self.channel_table.item(channel_index, 9).text(), 10.0)
            if full_scale > 0.0:
                return full_scale
        return 10.0

    @staticmethod
    def _parse_trigger_percent_text(text: str) -> float:
        try:
            return float(text.strip().replace("%", ""))
        except ValueError:
            return 0.0

    def _set_trigger_percent_from_level(self, level: float) -> None:
        full_scale = self._channel_full_scale_for_trigger(self._trigger_channel_index())
        percent = 0.0 if full_scale == 0.0 else level * 100.0 / full_scale
        index = min(
            range(len(self.TRIGGER_LEVEL_PERCENT_VALUES)),
            key=lambda i: abs(self.TRIGGER_LEVEL_PERCENT_VALUES[i] - percent),
        )
        self.trigger_level_percent_combo.blockSignals(True)
        self.trigger_level_percent_combo.setCurrentIndex(index)
        self.trigger_level_percent_combo.blockSignals(False)

    def _frame_size_slider_changed(self, value: int) -> None:
        snapped = min((2048, 4096, 8192), key=lambda candidate: abs(candidate - value))
        if self.frame_size_slider.value() != snapped:
            self.frame_size_slider.blockSignals(True)
            self.frame_size_slider.setValue(snapped)
            self.frame_size_slider.blockSignals(False)
        self.frame_size_edit.setValue(snapped)
        self._update_frequency_readout()

    def _frame_size_edit_changed(self, value: int) -> None:
        if hasattr(self, "frame_size_slider"):
            slider_value = min(max(value, self.frame_size_slider.minimum()), self.frame_size_slider.maximum())
            if self.frame_size_slider.value() != slider_value:
                self.frame_size_slider.blockSignals(True)
                self.frame_size_slider.setValue(slider_value)
                self.frame_size_slider.blockSignals(False)
        self._update_frequency_readout()

    def _update_frequency_readout(self) -> None:
        if not hasattr(self, "df_label"):
            return
        frame_size = max(1, int(self.frame_size_edit.value()))
        sample_rate = float(self.sample_rate_edit.value())
        self.df_label.setText(f"dF={sample_rate / frame_size:.4g} Hz")

    def _bandwidth_changed(self, text: str) -> None:
        bandwidth = self.BANDWIDTH_OPTIONS_HZ.get(text)
        if bandwidth is None:
            return
        self.sample_rate_edit.setValue(2.56 * bandwidth)
        self._update_frequency_readout()

    def _processing_window_changed(self, text: str) -> None:
        normalized = self._processing_window_value(text)
        if normalized == "hanning":
            self.force_window_checkbox.setChecked(False)
            self.exp_window_checkbox.setChecked(False)
        self.statusBar().showMessage(f"Processing window set to {text}")

    def _reject_mode_changed(self, text: str) -> None:
        normalized = text.strip().lower()
        both = "both" in normalized
        self.reject_overload_checkbox.setChecked(both or "overload" in normalized)
        self.reject_double_hit_checkbox.setChecked(both or "double" in normalized)
        if "overload" in normalized or "double" in normalized:
            self.statusBar().showMessage(f"Reject mode set to {text}")

    def _build_acquisition_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        acquisition_group = QtWidgets.QGroupBox("Acquisition")
        form = QtWidgets.QFormLayout(acquisition_group)
        form.setContentsMargins(8, 6, 8, 6)
        self.sample_rate_edit = QtWidgets.QDoubleSpinBox()
        self.sample_rate_edit.setDecimals(1)
        self.sample_rate_edit.setRange(1.0, 102400.0)
        self.sample_rate_edit.setValue(2560.0)
        self.sample_rate_edit.valueChanged.connect(lambda _value: self._update_frequency_readout())
        self.frame_size_edit = QtWidgets.QSpinBox()
        self.frame_size_edit.setRange(128, 65536)
        self.frame_size_edit.setSingleStep(128)
        self.frame_size_edit.setValue(4096)
        self.average_mode_combo = QtWidgets.QComboBox()
        self.average_mode_combo.addItems(["linear", "exponential", "peak", "off"])
        self.average_mode_combo.setCurrentText("linear")
        self._make_compact_combo(self.average_mode_combo, 9)
        self.average_mode_combo.setToolTip(
            "linear: arithmetic average and stop at Average Count; "
            "exponential: running weighted average until Stop; "
            "peak: peak-hold average and stop at Average Count; "
            "off: no averaging."
        )
        self.average_count_edit = QtWidgets.QSpinBox()
        self.average_count_edit.setRange(1, 1024)
        self.average_count_edit.setValue(20)
        self.average_count_edit.setToolTip(
            "Number of frames used by Avg for linear/peak modes. "
            "Exponential mode runs continuously until Stop."
        )
        average_help = QtWidgets.QLabel(
            "Avg: linear = arithmetic average, exponential = running weighted average, "
            "peak = peak hold, off = no averaging. Count stops linear/peak Avg runs."
        )
        average_help.setWordWrap(True)
        self.trigger_enable = QtWidgets.QPushButton("Arm")
        self.trigger_enable.setCheckable(True)
        self.trigger_enable.setEnabled(False)
        self.trigger_enable.clicked.connect(self._toggle_trigger_arm)
        self.trigger_mode_combo = QtWidgets.QComboBox()
        for value in self.TRIGGER_MODE_OPTIONS:
            self.trigger_mode_combo.addItem(self.TRIGGER_MODE_LABELS.get(value, value), value)
        self.trigger_mode_combo.setCurrentIndex(0)
        self._make_compact_combo(self.trigger_mode_combo, 10)
        self.trigger_mode_combo.currentTextChanged.connect(self._trigger_mode_changed)
        self.trigger_source_edit = QtWidgets.QLineEdit("ai0")
        self.trigger_source_edit.hide()
        self.trigger_source_combo = QtWidgets.QComboBox()
        self.trigger_source_combo.addItems(["Ch1", "Ch2", "Ch3", "Ch4"])
        self._make_compact_combo(self.trigger_source_combo, 4)
        self.trigger_source_combo.currentTextChanged.connect(self._trigger_source_combo_changed)
        self.trigger_level_edit = QtWidgets.QDoubleSpinBox()
        self.trigger_level_edit.setRange(-10.0, 10.0)
        self.trigger_level_edit.setDecimals(3)
        self.trigger_level_percent_combo = QtWidgets.QComboBox()
        self.trigger_level_percent_combo.addItems(self.TRIGGER_LEVEL_PERCENT_OPTIONS)
        self.trigger_level_percent_combo.setCurrentText("0%")
        self._make_compact_combo(self.trigger_level_percent_combo, 4)
        self.trigger_level_percent_combo.currentTextChanged.connect(
            self._trigger_level_percent_changed
        )
        self.trigger_slope_combo = QtWidgets.QComboBox()
        self.trigger_slope_combo.addItems(["Pos", "Neg"])
        self.trigger_slope_combo.hide()
        self.trigger_slope_combo.currentTextChanged.connect(self._trigger_slope_combo_changed)
        self.pretrigger_samples_edit = QtWidgets.QSpinBox()
        self.pretrigger_samples_edit.setRange(-65536, 65536)
        self.pretrigger_samples_edit.setValue(0)
        self.trigger_timeout_edit = QtWidgets.QDoubleSpinBox()
        self.trigger_timeout_edit.setRange(0.1, 60.0)
        self.trigger_timeout_edit.setDecimals(2)
        self.trigger_timeout_edit.setValue(5.0)
        self.trigger_delay_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.trigger_delay_slider.setRange(-100, 100)
        self.trigger_delay_slider.valueChanged.connect(self.pretrigger_samples_edit.setValue)
        self.pretrigger_samples_edit.valueChanged.connect(self.trigger_delay_slider.setValue)
        self.trigger_delay_slider.setValue(self.pretrigger_samples_edit.value())

        form.addRow("Sample Rate (Hz)", self.sample_rate_edit)
        form.addRow("Frame Size", self.frame_size_edit)
        form.addRow("Average Mode", self.average_mode_combo)
        form.addRow("Average Count", self.average_count_edit)
        form.addRow("", average_help)
        layout.addWidget(acquisition_group)
        layout.addStretch(1)
        return widget

    def _build_trigger_panel(self) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox("TRIGGER")
        panel.setObjectName("legacyTriggerPanel")
        panel.setStyleSheet(self._legacy_trigger_panel_stylesheet(self._theme()))
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 12, 8, 6)
        layout.setSpacing(5)
        layout.addWidget(self.trigger_mode_combo)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.trigger_source_combo, 1)
        row.addWidget(self.trigger_level_percent_combo, 1)
        layout.addLayout(row)

        slope_row = QtWidgets.QHBoxLayout()
        slope_row.addStretch(1)
        self.trigger_slope_button = QtWidgets.QPushButton("Pos")
        self.trigger_slope_button.clicked.connect(self._toggle_trigger_slope)
        slope_row.addWidget(self.trigger_slope_button)
        slope_row.addStretch(1)
        layout.addLayout(slope_row)

        delay_label = QtWidgets.QLabel("Delay")
        delay_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(delay_label)
        delay_row = QtWidgets.QHBoxLayout()
        delay_row.addWidget(QtWidgets.QLabel("-100"))
        delay_row.addWidget(self.pretrigger_samples_edit)
        delay_row.addWidget(QtWidgets.QLabel("100"))
        layout.addLayout(delay_row)
        layout.addWidget(self.trigger_delay_slider)
        layout.addWidget(self.trigger_enable)
        return panel

    @staticmethod
    def _legacy_trigger_panel_stylesheet(theme: dict[str, object]) -> str:
        return (
            f"QGroupBox#legacyTriggerPanel {{ background: {theme['panel_bg']}; color: {theme['text']}; font-weight: bold; "
            f"border: 1px solid {theme['border']}; border-radius: 12px; margin-top: 18px; }} "
            f"QGroupBox#legacyTriggerPanel::title {{ subcontrol-origin: margin; left: 10px; padding: 3px 12px; background: {theme['accent']}; color: #ffffff; border-radius: 6px; }} "
            f"QGroupBox#legacyTriggerPanel QLabel, QGroupBox#legacyTriggerPanel QCheckBox {{ color: {theme['text']}; }} "
            f"QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {theme['panel_bg_alt']}; color: {theme['text']}; min-height: 22px; font-weight: bold; "
            f"border: 1px solid {theme['control_border']}; border-radius: 7px; padding: 2px 18px 2px 6px; }} "
            f"QPushButton {{ background: {theme['accent']}; color: #ffffff; font-weight: bold; padding: 6px 14px; border: 1px solid {theme['accent_hover']}; border-radius: 8px; }} "
            f"QPushButton:hover {{ background: {theme['accent_hover']}; }} "
            f"QSlider::groove:horizontal {{ background: {theme['panel_bg_alt']}; height: 24px; border: 1px solid {theme['control_border']}; border-radius: 12px; }} "
            f"QSlider::handle:horizontal {{ background: {theme['accent_alt']}; width: 16px; margin: -2px 0px; border: 1px solid {theme['label_text']}; border-radius: 8px; }}"
        )

    def _mc_setup_dialog_stylesheet(self) -> str:
        theme = self._theme()
        return (
            f"QDialog {{ background: {theme['window_bg']}; color: {theme['text']}; }} "
            f"QHeaderView::section {{ background: {theme['cell_bg']}; color: {theme['label_text']}; font-size: 9pt; font-weight: bold; padding: 4px; border: 1px solid {theme['control_border']}; }} "
            f"QTableWidget {{ background: {theme['table_bg']}; color: {theme['text']}; gridline-color: {theme['border']}; font-size: 9pt; font-weight: bold; border: 1px solid {theme['border']}; border-radius: 8px; }} "
            f"QTableWidget::item {{ background: {theme['panel_bg']}; color: {theme['text']}; padding: 2px; border-radius: 3px; }} "
            f"QTableWidget::item:selected {{ background: {theme['accent']}; color: #ffffff; }} "
            f"QPushButton {{ min-width: 72px; padding: 5px 10px; font-size: 9pt; font-weight: bold; background: {theme['accent']}; color: #ffffff; border: 1px solid {theme['accent_hover']}; border-radius: 7px; }} "
            f"QPushButton:hover {{ background: {theme['accent_hover']}; }} "
            f"QPushButton:pressed {{ background: {theme['accent_hover']}; }} "
            f"QLabel, QCheckBox {{ color: {theme['text']}; font-size: 9pt; font-weight: bold; }} "
            f"QComboBox {{ background: {theme['panel_bg_alt']}; color: {theme['text']}; font-size: 9pt; padding: 3px 22px 3px 6px; border: 1px solid {theme['control_border']}; border-radius: 6px; }}"
        )

    def _build_excitation_tab(self, compact: bool = False):
        widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(widget)
        form.setContentsMargins(8, 6, 8, 6)
        form.setSpacing(6 if compact else 8)
        self.exc_enable = QtWidgets.QCheckBox("Enable AO excitation")
        self.exc_mode_combo = QtWidgets.QComboBox()
        self.exc_mode_combo.addItems(["external", "tone", "chirp", "random"])
        self.ao_channel_edit = QtWidgets.QLineEdit("ao0")
        self.exc_amplitude_edit = QtWidgets.QDoubleSpinBox()
        self.exc_amplitude_edit.setRange(0.0, 10.0)
        self.exc_amplitude_edit.setValue(1.0)
        self.exc_offset_edit = QtWidgets.QDoubleSpinBox()
        self.exc_offset_edit.setRange(-10.0, 10.0)
        self.exc_tone_edit = QtWidgets.QDoubleSpinBox()
        self.exc_tone_edit.setRange(1.0, 50000.0)
        self.exc_tone_edit.setValue(100.0)
        self.exc_chirp_start_edit = QtWidgets.QDoubleSpinBox()
        self.exc_chirp_start_edit.setRange(0.1, 50000.0)
        self.exc_chirp_start_edit.setValue(10.0)
        self.exc_chirp_stop_edit = QtWidgets.QDoubleSpinBox()
        self.exc_chirp_stop_edit.setRange(1.0, 50000.0)
        self.exc_chirp_stop_edit.setValue(2000.0)

        form.addRow(self.exc_enable)
        if compact:
            row1 = QtWidgets.QHBoxLayout()
            for label, widget_item in (
                ("AO", self.ao_channel_edit),
                ("Mode", self.exc_mode_combo),
                ("Amp", self.exc_amplitude_edit),
                ("Offset", self.exc_offset_edit),
            ):
                row1.addWidget(QtWidgets.QLabel(label))
                row1.addWidget(widget_item)
            form.addRow(row1)
            row2 = QtWidgets.QHBoxLayout()
            for label, widget_item in (
                ("Tone", self.exc_tone_edit),
                ("Chirp Start", self.exc_chirp_start_edit),
                ("Chirp Stop", self.exc_chirp_stop_edit),
            ):
                row2.addWidget(QtWidgets.QLabel(label))
                row2.addWidget(widget_item)
            row2.addStretch(1)
            form.addRow(row2)
        else:
            form.addRow("AO Channel", self.ao_channel_edit)
            form.addRow("Mode", self.exc_mode_combo)
            form.addRow("Amplitude (V)", self.exc_amplitude_edit)
            form.addRow("Offset (V)", self.exc_offset_edit)
            form.addRow("Tone Frequency (Hz)", self.exc_tone_edit)
            form.addRow("Chirp Start (Hz)", self.exc_chirp_start_edit)
            form.addRow("Chirp Stop (Hz)", self.exc_chirp_stop_edit)
        return widget

    def _build_modal_tab(self, compact: bool = False):
        widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(widget)
        form.setContentsMargins(8, 6, 8, 6)
        form.setSpacing(6 if compact else 8)
        self.modal_enable_checkbox = QtWidgets.QCheckBox("Enable modal processing")
        self.force_window_checkbox = QtWidgets.QCheckBox("Force window on reference")
        self.force_window_fraction_edit = QtWidgets.QDoubleSpinBox()
        self.force_window_fraction_edit.setRange(0.01, 1.0)
        self.force_window_fraction_edit.setDecimals(3)
        self.force_window_fraction_edit.setValue(0.2)
        self.exp_window_checkbox = QtWidgets.QCheckBox("Exponential window on responses")
        self.exp_window_decay_edit = QtWidgets.QDoubleSpinBox()
        self.exp_window_decay_edit.setRange(0.001, 1.0)
        self.exp_window_decay_edit.setDecimals(3)
        self.exp_window_decay_edit.setValue(0.1)
        self.reject_double_hit_checkbox = QtWidgets.QCheckBox("Reject double hit")
        self.double_hit_threshold_edit = QtWidgets.QDoubleSpinBox()
        self.double_hit_threshold_edit.setRange(0.01, 1.0)
        self.double_hit_threshold_edit.setDecimals(3)
        self.double_hit_threshold_edit.setValue(0.5)
        self.double_hit_delay_edit = QtWidgets.QDoubleSpinBox()
        self.double_hit_delay_edit.setRange(0.2, 0.5)
        self.double_hit_delay_edit.setDecimals(3)
        self.double_hit_delay_edit.setValue(0.2)
        self.reject_overload_checkbox = QtWidgets.QCheckBox("Reject overload")

        form.addRow(self.modal_enable_checkbox)
        if compact:
            row1 = QtWidgets.QHBoxLayout()
            row1.addWidget(self.force_window_checkbox)
            row1.addWidget(QtWidgets.QLabel("Force %"))
            row1.addWidget(self.force_window_fraction_edit)
            row1.addWidget(self.exp_window_checkbox)
            row1.addWidget(QtWidgets.QLabel("Exp Decay"))
            row1.addWidget(self.exp_window_decay_edit)
            form.addRow(row1)
            row2 = QtWidgets.QHBoxLayout()
            row2.addWidget(self.reject_double_hit_checkbox)
            row2.addWidget(QtWidgets.QLabel("Amp %"))
            row2.addWidget(self.double_hit_threshold_edit)
            row2.addWidget(QtWidgets.QLabel("Delay %"))
            row2.addWidget(self.double_hit_delay_edit)
            row2.addWidget(self.reject_overload_checkbox)
            form.addRow(row2)
        else:
            form.addRow(self.force_window_checkbox)
            form.addRow("Force Window Fraction", self.force_window_fraction_edit)
            form.addRow(self.exp_window_checkbox)
            form.addRow("Exponential Decay Fraction", self.exp_window_decay_edit)
            form.addRow(self.reject_double_hit_checkbox)
            form.addRow("Double Hit Threshold", self.double_hit_threshold_edit)
            form.addRow("Double Hit Delay", self.double_hit_delay_edit)
            form.addRow(self.reject_overload_checkbox)
        return widget

    def _load_session_to_widgets(self) -> None:
        self.session_title_edit.setText(self.session.title)
        self.session_notes_edit.setPlainText(self.session.notes)
        self.channel_table.setRowCount(len(self.session.ai_channels))
        for row, channel in enumerate(self.session.ai_channels):
            self._set_channel_row(row, channel)
        self._rebuild_channel_list()
        self.sample_rate_edit.setValue(self.session.acquisition.sample_rate)
        self.frame_size_edit.setValue(self.session.acquisition.frame_size)
        if hasattr(self, "bandwidth_combo"):
            bandwidth_label = min(
                self.BANDWIDTH_OPTIONS_HZ,
                key=lambda label: abs(self.BANDWIDTH_OPTIONS_HZ[label] - self.session.acquisition.bandwidth_hz),
            )
            self.bandwidth_combo.setCurrentText(bandwidth_label)
        self.average_mode_combo.setCurrentText(self.session.acquisition.averaging.mode)
        self.average_count_edit.setValue(self.session.acquisition.averaging.count)
        if hasattr(self, "average_count_slider"):
            self.average_count_slider.setValue(min(max(self.session.acquisition.averaging.count, 1), 1000))
        if hasattr(self, "frame_size_slider"):
            self.frame_size_slider.setValue(min(max(self.session.acquisition.frame_size, 2048), 8192))
        self._update_frequency_readout()
        keep_existing_overlay = any(self._stored_overlays[key] for key in ("top", "bottom"))
        overlay_enabled = self.session.acquisition.overlay_enabled or (
            keep_existing_overlay and self.overlay_checkbox.isChecked()
        )
        self.overlay_checkbox.setChecked(overlay_enabled)
        self.excitation_enabled_action.setChecked(self.session.acquisition.excitation.enabled)
        self.overlay_action.setChecked(overlay_enabled)
        self.trigger_enable.setChecked(self.session.acquisition.trigger.enabled)
        trigger_source = self.session.acquisition.trigger.source
        if not self.session.acquisition.trigger.enabled and trigger_source == "immediate":
            trigger_source = self.session.acquisition.reference_channel or "ai0"
        trigger_mode = getattr(self.session.acquisition.trigger, "mode", "Off (Free Run)")
        if trigger_mode not in self.TRIGGER_MODE_OPTIONS:
            trigger_mode = "Every Frame" if self.session.acquisition.trigger.enabled else "Off (Free Run)"
        index = self.trigger_mode_combo.findData(trigger_mode)
        self.trigger_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._sync_trigger_arm_button()
        self.trigger_source_edit.setText(trigger_source)
        if trigger_source.startswith("ai"):
            try:
                trigger_channel = int(trigger_source[2:]) + 1
            except ValueError:
                trigger_channel = 1
            self.trigger_source_combo.setCurrentText(f"Ch{min(max(trigger_channel, 1), 4)}")
        self.trigger_level_edit.setValue(self.session.acquisition.trigger.level)
        stored_trigger_percent = getattr(self.session.acquisition.trigger, "level_percent", None)
        if stored_trigger_percent is None:
            self._set_trigger_percent_from_level(self.session.acquisition.trigger.level)
        else:
            percent_index = min(
                range(len(self.TRIGGER_LEVEL_PERCENT_VALUES)),
                key=lambda index: abs(self.TRIGGER_LEVEL_PERCENT_VALUES[index] - stored_trigger_percent),
            )
            self.trigger_level_percent_combo.blockSignals(True)
            self.trigger_level_percent_combo.setCurrentIndex(percent_index)
            self.trigger_level_percent_combo.blockSignals(False)
            self._trigger_level_percent_changed(self.trigger_level_percent_combo.currentText())
        slope_text = "Neg" if self.session.acquisition.trigger.slope == "falling" else "Pos"
        self.trigger_slope_combo.setCurrentText(slope_text)
        self.trigger_slope_button.setText(slope_text)
        self.pretrigger_samples_edit.setValue(self.session.acquisition.trigger.pretrigger_samples)
        self.trigger_timeout_edit.setValue(self.session.acquisition.trigger.timeout_seconds)
        self.exc_enable.setChecked(self.session.acquisition.excitation.enabled)
        self.exc_mode_combo.setCurrentText(self.session.acquisition.excitation.mode)
        self.ao_channel_edit.setText(self.session.ao_channel or "ao0")
        self.exc_amplitude_edit.setValue(self.session.acquisition.excitation.amplitude)
        self.exc_offset_edit.setValue(self.session.acquisition.excitation.offset)
        self.exc_tone_edit.setValue(self.session.acquisition.excitation.tone_hz)
        self.exc_chirp_start_edit.setValue(self.session.acquisition.excitation.chirp_start_hz)
        self.exc_chirp_stop_edit.setValue(self.session.acquisition.excitation.chirp_stop_hz)
        self.modal_enable_checkbox.setChecked(self.session.acquisition.modal.enabled)
        self.modal_enabled_action.setChecked(self.session.acquisition.modal.enabled)
        self.force_window_checkbox.setChecked(self.session.acquisition.modal.force_window_enabled)
        self.force_window_action.setChecked(self.session.acquisition.modal.force_window_enabled)
        self.force_window_fraction_edit.setValue(self.session.acquisition.modal.force_window_fraction)
        self.exp_window_checkbox.setChecked(
            self.session.acquisition.modal.exponential_window_enabled
        )
        self.exp_window_action.setChecked(
            self.session.acquisition.modal.exponential_window_enabled
        )
        self.exp_window_decay_edit.setValue(
            self.session.acquisition.modal.exponential_decay_fraction
        )
        self.reject_double_hit_checkbox.setChecked(
            self.session.acquisition.modal.reject_double_hit
        )
        self.reject_double_hit_action.setChecked(
            self.session.acquisition.modal.reject_double_hit
        )
        self.double_hit_threshold_edit.setValue(
            self.session.acquisition.modal.double_hit_threshold
        )
        self.double_hit_delay_edit.setValue(
            self.session.acquisition.modal.double_hit_delay_fraction
        )
        self.reject_overload_checkbox.setChecked(
            self.session.acquisition.modal.reject_overload
        )
        self.reject_overload_action.setChecked(
            self.session.acquisition.modal.reject_overload
        )
        if hasattr(self, "window_combo"):
            self.window_combo.setCurrentText(
                self._processing_window_label(self.session.acquisition.processing_window)
            )
        if hasattr(self, "overlap_combo"):
            overlap_text = {
                0: "No Overlap",
                50: "50% Overlap",
                100: "Max Overlap",
            }.get(int(self.session.acquisition.overlap_percent), "No Overlap")
            index = self.overlap_combo.findText(overlap_text)
            if index >= 0:
                self.overlap_combo.setCurrentIndex(index)
        if hasattr(self, "reject_combo"):
            self.reject_combo.blockSignals(True)
            if (
                self.session.acquisition.modal.reject_double_hit
                and self.session.acquisition.modal.reject_overload
            ):
                self.reject_combo.setCurrentText("Both Reject")
            elif self.session.acquisition.modal.reject_double_hit:
                self.reject_combo.setCurrentText("Double Hit Reject")
            elif self.session.acquisition.modal.reject_overload:
                self.reject_combo.setCurrentText("Overload Reject")
            else:
                self.reject_combo.setCurrentText("No Reject")
            self.reject_combo.blockSignals(False)
        self._reload_channel_selectors()
        self._update_axis_labels()
        self._refresh_trace_lists_from_available_sources()

    def _set_channel_row(self, row: int, channel: ChannelConfig) -> None:
        enabled_item = QtWidgets.QTableWidgetItem()
        enabled_item.setFlags(enabled_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        enabled_item.setCheckState(
            QtCore.Qt.Checked if channel.enabled else QtCore.Qt.Unchecked
        )
        ref_item = QtWidgets.QTableWidgetItem()
        ref_item.setFlags(ref_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        ref_item.setCheckState(
            QtCore.Qt.Checked if channel.is_reference else QtCore.Qt.Unchecked
        )
        iepe_item = QtWidgets.QTableWidgetItem()
        iepe_item.setFlags(iepe_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        iepe_item.setCheckState(
            QtCore.Qt.Checked if channel.iepe_enabled else QtCore.Qt.Unchecked
        )
        self.channel_table.setItem(row, 0, enabled_item)
        self.channel_table.setItem(row, 1, QtWidgets.QTableWidgetItem(channel.name))
        self.channel_table.setItem(row, 2, QtWidgets.QTableWidgetItem(channel.physical_name))
        self.channel_table.setItem(row, 3, ref_item)
        self.channel_table.setItem(row, 4, iepe_item)
        self.channel_table.setItem(
            row, 5, QtWidgets.QTableWidgetItem(f"{channel.iepe_current_ma:.6g}")
        )
        self.channel_table.setItem(
            row, 6, QtWidgets.QTableWidgetItem(channel.coupling)
        )
        self.channel_table.setItem(
            row, 7, QtWidgets.QTableWidgetItem(f"{channel.sensitivity:.6g}")
        )
        self.channel_table.setItem(row, 8, QtWidgets.QTableWidgetItem(channel.engineering_unit))
        self.channel_table.setItem(
            row, 9, QtWidgets.QTableWidgetItem(f"{channel.full_scale:.6g}")
        )
        self.channel_table.setItem(
            row,
            10,
            QtWidgets.QTableWidgetItem(channel.label or f"Ch {row + 1}"),
        )
        self.channel_table.setItem(
            row, 11, QtWidgets.QTableWidgetItem(f"{channel.offset:.6g}")
        )
        self.channel_table.setItem(
            row, 12, QtWidgets.QTableWidgetItem(channel.per_eu_mode)
        )
        self.channel_table.setItem(
            row, 13, QtWidgets.QTableWidgetItem(f"{channel.db_reference:.6g}")
        )
        self.channel_table.setItem(row, 14, QtWidgets.QTableWidgetItem(""))

    def _read_session_from_widgets(self) -> SessionConfig:
        channels: list[ChannelConfig] = []
        for row in range(self.channel_table.rowCount()):
            channels.append(
                ChannelConfig(
                    enabled=self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked,
                    name=self.channel_table.item(row, 1).text(),
                    physical_name=self.channel_table.item(row, 2).text(),
                    label=self.channel_table.item(row, 10).text(),
                    is_reference=self.channel_table.item(row, 3).checkState() == QtCore.Qt.Checked,
                    offset=float(self.channel_table.item(row, 11).text()),
                    iepe_enabled=self.channel_table.item(row, 4).checkState() == QtCore.Qt.Checked,
                    iepe_current_ma=float(self.channel_table.item(row, 5).text()),
                    coupling=self.channel_table.item(row, 6).text(),
                    sensitivity=float(self.channel_table.item(row, 7).text()),
                    engineering_unit=self.channel_table.item(row, 8).text(),
                    per_eu_mode=self.channel_table.item(row, 12).text(),
                    db_reference=float(self.channel_table.item(row, 13).text()),
                    full_scale=float(self.channel_table.item(row, 9).text()),
                    min_value=-float(self.channel_table.item(row, 9).text()),
                    max_value=float(self.channel_table.item(row, 9).text()),
                )
            )

        session = self.controller.state.session
        session.title = self.session_title_edit.text().strip() or "Untitled Session"
        session.notes = self.session_notes_edit.toPlainText()
        session.ai_channels = channels
        session.ao_channel = self.ao_channel_edit.text().strip() or None
        session.acquisition.sample_rate = self.sample_rate_edit.value()
        session.acquisition.frame_size = self.frame_size_edit.value()
        if hasattr(self, "bandwidth_combo"):
            session.acquisition.bandwidth_hz = self.BANDWIDTH_OPTIONS_HZ.get(
                self.bandwidth_combo.currentText(),
                session.acquisition.sample_rate / 2.56,
            )
        if hasattr(self, "aa_filters_button"):
            session.acquisition.anti_alias_filters_enabled = True
        if hasattr(self, "window_combo"):
            session.acquisition.processing_window = self._processing_window_value(
                self.window_combo.currentText()
            )
        if hasattr(self, "overlap_combo"):
            session.acquisition.overlap_percent = {
                "No Overlap": 0,
                "50% Overlap": 50,
                "Max Overlap": 100,
                "75% Overlap": 75,
            }.get(self.overlap_combo.currentText(), session.acquisition.overlap_percent)
        session.acquisition.averaging.mode = self.average_mode_combo.currentText()
        session.acquisition.averaging.count = self.average_count_edit.value()
        session.acquisition.overlay_enabled = self.overlay_checkbox.isChecked()
        session.acquisition.reference_channel = (
            self.reference_channel_combo.currentText().strip() or "ai0"
        )
        session.acquisition.response_channels = [
            item.text()
            for item in self.response_channel_list.selectedItems()
        ]
        session.acquisition.trigger.mode = self._current_combo_value(self.trigger_mode_combo)
        trigger_mode = session.acquisition.trigger.mode.strip().lower()
        manual_mode = "manual" in trigger_mode
        session.acquisition.trigger.enabled = (
            trigger_mode != "off (free run)"
            and (not manual_mode or self.trigger_enable.isChecked())
        )
        session.acquisition.trigger.source = (
            self.trigger_source_edit.text().strip()
            if session.acquisition.trigger.enabled
            else "immediate"
        )
        session.acquisition.trigger.level = self.trigger_level_edit.value()
        session.acquisition.trigger.level_percent = self._parse_trigger_percent_text(
            self.trigger_level_percent_combo.currentText()
        )
        session.acquisition.trigger.slope = (
            "falling" if self.trigger_slope_combo.currentText() == "Neg" else "rising"
        )
        session.acquisition.trigger.pretrigger_samples = self.pretrigger_samples_edit.value()
        session.acquisition.trigger.timeout_seconds = self.trigger_timeout_edit.value()
        session.acquisition.excitation.enabled = self.exc_enable.isChecked()
        session.acquisition.excitation.mode = self.exc_mode_combo.currentText()
        session.acquisition.excitation.amplitude = self.exc_amplitude_edit.value()
        session.acquisition.excitation.offset = self.exc_offset_edit.value()
        session.acquisition.excitation.tone_hz = self.exc_tone_edit.value()
        session.acquisition.excitation.chirp_start_hz = self.exc_chirp_start_edit.value()
        session.acquisition.excitation.chirp_stop_hz = self.exc_chirp_stop_edit.value()
        session.acquisition.modal.enabled = self.modal_enable_checkbox.isChecked()
        session.acquisition.modal.force_window_enabled = self.force_window_checkbox.isChecked()
        session.acquisition.modal.force_window_fraction = (
            self.force_window_fraction_edit.value()
        )
        session.acquisition.modal.exponential_window_enabled = (
            self.exp_window_checkbox.isChecked()
        )
        session.acquisition.modal.exponential_decay_fraction = (
            self.exp_window_decay_edit.value()
        )
        session.acquisition.modal.reject_double_hit = self.reject_double_hit_checkbox.isChecked()
        session.acquisition.modal.double_hit_threshold = (
            self.double_hit_threshold_edit.value()
        )
        session.acquisition.modal.double_hit_delay_fraction = (
            self.double_hit_delay_edit.value()
        )
        session.acquisition.modal.reject_overload = self.reject_overload_checkbox.isChecked()
        return session

    def _refresh_devices(self) -> None:
        self.device_combo.clear()
        try:
            devices = self.controller.list_devices()
        except Exception as exc:
            self.statusBar().showMessage(f"Device refresh failed: {exc}")
            return
        for device in devices:
            self.device_combo.addItem(f"{device.name} ({device.product_type})", device.name)
        preferred = self.controller.preferred_device(devices)
        if preferred is not None:
            for index in range(self.device_combo.count()):
                if self.device_combo.itemData(index) == preferred:
                    self.device_combo.setCurrentIndex(index)
                    break
            self._map_channels_to_selected_device()
            self.device_info_label.setText(f"Device: {preferred}")
        if not devices:
            self.statusBar().showMessage("No devices found")
        else:
            self.statusBar().showMessage(f"Found {len(devices)} device(s)")

    def _backend_changed(self, backend_name: str) -> None:
        if self._acquisition_thread is not None:
            self._stop_acquisition()
            return
        self.controller.close()
        if backend_name == "ni":
            from python_vna.app import build_backend

            self.controller.backend = build_backend("ni")
        else:
            from python_vna.app import build_backend

            self.controller.backend = build_backend("simulated")
        self._refresh_devices()

    def _map_channels_to_selected_device(self) -> None:
        device_name = self.device_combo.currentData()
        if not device_name:
            return
        ao_name = f"{device_name}/ao0"
        self.ao_channel_edit.setText(ao_name)
        for row in range(self.channel_table.rowCount()):
            channel_name = self.channel_table.item(row, 1).text()
            self.channel_table.item(row, 2).setText(f"{device_name}/{channel_name}")
        current_row = self.channel_list.currentRow() if hasattr(self, "channel_list") else -1
        self._load_channel_editor_from_row(current_row)
        self._reload_channel_selectors()

    def _reload_channel_selectors(self, *, include_new_responses: bool = False) -> None:
        channels = []
        for row in range(self.channel_table.rowCount()):
            if self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked:
                channels.append(self.channel_table.item(row, 1).text())

        self.reference_channel_combo.blockSignals(True)
        self.reference_channel_combo.clear()
        self.reference_channel_combo.addItems(channels)
        reference = self.session.acquisition.reference_channel
        if reference in channels:
            self.reference_channel_combo.setCurrentText(reference)
        elif channels:
            self.reference_channel_combo.setCurrentText(channels[0])
        self.reference_channel_combo.blockSignals(False)

        self.response_channel_list.clear()
        selected_responses = set(self.session.acquisition.response_channels)
        current_reference = self.reference_channel_combo.currentText()
        available_responses = [channel for channel in channels if channel != current_reference]
        if include_new_responses:
            selected_responses.update(available_responses)
        for channel in channels:
            if channel == current_reference:
                continue
            item = QtWidgets.QListWidgetItem(channel)
            self.response_channel_list.addItem(item)
            if channel in selected_responses:
                item.setSelected(True)
        if not self.response_channel_list.selectedItems():
            for index in range(self.response_channel_list.count()):
                self.response_channel_list.item(index).setSelected(True)
        self._refresh_trace_lists_from_available_sources()

    def _enabled_channel_names(self) -> list[str]:
        channels: list[str] = []
        for row in range(self.channel_table.rowCount()):
            if self.channel_table.item(row, 0).checkState() == QtCore.Qt.Checked:
                channels.append(self.channel_table.item(row, 1).text())
        return channels

    def _selected_response_names(self) -> list[str]:
        if hasattr(self, "response_channel_list") and self.response_channel_list.count():
            selected = [
                item.text()
                for item in self.response_channel_list.selectedItems()
            ]
            if selected:
                return selected
        responses = list(self.session.acquisition.response_channels)
        if responses:
            return responses
        reference = self._current_reference_name()
        return [name for name in self._enabled_channel_names() if name != reference]

    def _current_reference_name(self) -> str:
        if hasattr(self, "reference_channel_combo"):
            reference = self.reference_channel_combo.currentText().strip()
            if reference:
                return reference
        return self.session.acquisition.reference_channel or "ai0"

    def _configured_trace_names_for_display(self, display_mode: str) -> list[str]:
        channels = self._enabled_channel_names()
        if display_mode in {"time", "autospectrum", "fft", "auto_correlation"}:
            return channels
        reference = self._current_reference_name()
        responses = [
            name for name in self._selected_response_names()
            if name and name != reference
        ]
        if not responses:
            responses = [name for name in channels if name != reference]
        pairs = [f"{reference}->{response}" for response in responses]
        if display_mode in {
            "frf",
            "coherence",
            "cross_spectrum",
            "cross_correlation",
            "impulse_response",
        }:
            return pairs
        return channels

    @staticmethod
    def _measurement_trace_names_for_display(measurement, display_mode: str) -> list[str]:
        if measurement is None:
            return []
        if display_mode == "time":
            return list(measurement.time_data.get("channels", {}).keys())
        if display_mode == "autospectrum":
            return list(measurement.spectra.get("autospectrum", {}).keys())
        if display_mode == "fft":
            return list(measurement.spectra.get("fft", {}).keys())
        if display_mode == "frf":
            return list(measurement.frf.keys())
        if display_mode == "coherence":
            return list(measurement.coherence.keys())
        if display_mode == "cross_spectrum":
            return list(measurement.cross_spectra.keys())
        if display_mode in {"auto_correlation", "cross_correlation"}:
            return list(measurement.correlations.keys())
        if display_mode == "impulse_response":
            return list(measurement.impulse_responses.keys())
        return []

    def _available_trace_names_for_key(self, key: str) -> list[str]:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        measurement = self.controller.state.measurement
        names = self._measurement_trace_names_for_display(measurement, display_mode)
        if names:
            return names
        return self._configured_trace_names_for_display(display_mode)

    def _refresh_trace_lists_from_available_sources(self) -> None:
        if not hasattr(self, "top_trace_list") or not hasattr(self, "bottom_trace_list"):
            return
        for key in ("top", "bottom"):
            names = self._available_trace_names_for_key(key)
            combo = self._trace_combo_for_key(key)
            preferred = self._active_trace_names.get(key)
            if preferred not in names:
                preferred = names[0] if names else None
            self._active_trace_names[key] = preferred
            self._populate_trace_combo(combo, key, names, preferred)
            strip_combo = self.top_trace_strip_combo if key == "top" else self.bottom_trace_strip_combo
            self._populate_trace_combo(strip_combo, key, names, preferred)
            self._sync_trace_list_items(self._trace_list_for_key(key), key, names)

    def _start_acquisition(self) -> None:
        self._start_run(average_run=False)

    def _start_average_acquisition(self) -> None:
        self._start_run(average_run=True)

    def _start_continuous_recording(self) -> None:
        if self._acquisition_thread is not None or self._recording_thread is not None:
            return
        parent_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Continuous Recording Folder",
            str(Path.cwd()),
        )
        if not parent_dir:
            return
        output_dir = Path(parent_dir) / recording_directory_name()
        self._map_channels_to_selected_device()
        self._reload_channel_selectors(include_new_responses=True)
        self.average_count_edit.interpretText()
        session = self._read_session_from_widgets()
        self._prepare_trace_selection_for_acquisition()
        self._clear_runtime_axis_ranges()
        self._pending_measurement = None
        self._plot_update_scheduled = False
        self._stop_requested_for_current_run = False
        device_name = self.device_combo.currentData()
        worker = ContinuousRecordingWorker(
            self.controller,
            device_name,
            output_dir,
        )
        stop_event = self._worker_stop_event(worker)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.started.connect(self._recording_started)
        worker.preview_ready.connect(self._handle_recording_preview)
        worker.recording_status.connect(self._handle_recording_status)
        worker.status_changed.connect(self.run_info_label.setText)
        worker.error.connect(self._handle_worker_error)
        worker.finished.connect(self._recording_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._recording_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._recording_worker = worker
        self._recording_thread = thread
        self._recording_stop_event = stop_event
        self._recording_output_dir = output_dir
        self.start_button.setEnabled(False)
        self.avg_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.backend_combo.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.run_info_label.setText("State: starting record")
        self.device_info_label.setText(f"Device: {device_name}")
        self.statusBar().showMessage(f"Starting continuous recording to {output_dir}")
        thread.start()

    def _clear_runtime_axis_ranges(self) -> None:
        for key in ("top", "bottom"):
            self._manual_x_ranges[key] = None
            self._manual_y_ranges[key] = None
            self._auto_y_follow_visible_x[key] = False

    def _prepare_trace_selection_for_acquisition(self) -> None:
        for key in ("top", "bottom"):
            if self._trace_checks_user_modified.get(key, False):
                continue
            display_mode = self._display_mode(self._display_combo_for_key(key))
            names = self._configured_trace_names_for_display(display_mode)
            if names:
                self._preferred_trace_checks[key] = set(names)

    def _start_run(self, average_run: bool) -> None:
        if self._acquisition_thread is not None or self._recording_thread is not None:
            return
        self._map_channels_to_selected_device()
        self._reload_channel_selectors(include_new_responses=True)
        self.average_count_edit.interpretText()
        session = self._read_session_from_widgets()
        self._prepare_trace_selection_for_acquisition()
        if not average_run:
            session.acquisition.trigger.enabled = False
            session.acquisition.trigger.source = "immediate"
        if "manual" in session.acquisition.trigger.mode.strip().lower() and not session.acquisition.trigger.enabled:
            self.run_info_label.setText("State: waiting for arm")
            self.statusBar().showMessage("Manual trigger mode requires Arm before acquisition")
            return
        self._clear_runtime_axis_ranges()
        self._pending_measurement = None
        self._plot_update_scheduled = False
        self._stop_requested_for_current_run = False
        device_name = self.device_combo.currentData()
        target_average_count = (
            session.acquisition.averaging.count
            if average_run and session.acquisition.averaging.mode in {"linear", "peak"}
            else None
        )
        display_interval = self._acquisition_display_interval_seconds(session)
        worker = AcquisitionWorker(
            self.controller,
            device_name,
            average_run=average_run,
            target_average_count=target_average_count,
            display_interval_seconds=display_interval,
        )
        stop_event = self._worker_stop_event(worker)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.started.connect(self._acquisition_started)
        worker.measurement_ready.connect(self._handle_worker_measurement)
        worker.status_changed.connect(self.run_info_label.setText)
        worker.error.connect(self._handle_worker_error)
        worker.finished.connect(self._acquisition_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._acquisition_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._acquisition_worker = worker
        self._acquisition_thread = thread
        self._acquisition_stop_event = stop_event
        self.start_button.setEnabled(False)
        self.avg_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.backend_combo.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.run_info_label.setText("State: starting avg" if average_run else "State: starting")
        self.device_info_label.setText(f"Device: {device_name}")
        self.statusBar().showMessage("Starting averaged acquisition" if average_run else "Starting acquisition")
        thread.start()

    @staticmethod
    def _acquisition_display_interval_seconds(session: SessionConfig) -> float:
        sample_rate = max(float(session.acquisition.sample_rate), 1.0)
        frame_size = max(int(session.acquisition.frame_size), 1)
        frame_duration = frame_size / sample_rate
        return float(np.clip(frame_duration, 0.25, 1.0))

    @staticmethod
    def _worker_stop_event(worker: object) -> threading.Event:
        stop_event = getattr(worker, "stop_event", None)
        if isinstance(stop_event, threading.Event):
            return stop_event
        return threading.Event()

    def _stop_acquisition(self) -> None:
        if self._recording_worker is not None:
            self.stop_button.setEnabled(False)
            self.avg_button.setEnabled(False)
            self.record_button.setEnabled(False)
            self.run_info_label.setText("State: stopping record")
            self.statusBar().showMessage("Stopping continuous recording")
            if self._recording_stop_event is not None:
                self._recording_stop_event.set()
            return
        if self._acquisition_worker is None:
            self.start_button.setEnabled(True)
            self.avg_button.setEnabled(True)
            self.record_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            if self.run_info_label.text() not in {"State: idle", "State: error"}:
                self.run_info_label.setText("State: stopped")
            return
        self.stop_button.setEnabled(False)
        self.avg_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self._pending_measurement = None
        self._stop_requested_for_current_run = True
        self.run_info_label.setText("State: stopping")
        self.statusBar().showMessage("Stopping acquisition")
        if self._acquisition_stop_event is not None:
            self._acquisition_stop_event.set()

    @QtCore.Slot(object)
    def _acquisition_started(self, device_name: str | None) -> None:
        self.device_info_label.setText(f"Device: {device_name}")
        self.run_info_label.setText("State: running")
        self.statusBar().showMessage("Acquisition running")

    @QtCore.Slot(object)
    def _recording_started(self, device_name: str | None) -> None:
        self.device_info_label.setText(f"Device: {device_name}")
        self.run_info_label.setText("State: recording")
        if self._recording_output_dir is not None:
            self.statusBar().showMessage(f"Recording to {self._recording_output_dir}")
        else:
            self.statusBar().showMessage("Recording")

    @QtCore.Slot(object)
    def _handle_worker_measurement(self, measurement) -> None:
        if self._stop_requested_for_current_run:
            return
        if "manual" in self._current_combo_value(self.trigger_mode_combo).strip().lower() and self.trigger_enable.isChecked():
            self.trigger_enable.setChecked(False)
            self._sync_trigger_arm_button()
        self._pending_measurement = measurement
        if self._plot_update_scheduled:
            return
        self._plot_update_scheduled = True
        QtCore.QTimer.singleShot(0, self._flush_pending_measurement)

    def _flush_pending_measurement(self) -> None:
        measurement = self._pending_measurement
        self._pending_measurement = None
        self._plot_update_scheduled = False
        if measurement is None:
            return
        if self._stop_requested_for_current_run:
            return
        self._plot_measurement(measurement)
        average_count = measurement.metadata.get("average_count", 0)
        average_target = measurement.metadata.get("average_target", 0)
        averaging_enabled = measurement.metadata.get("averaging_enabled", False)
        average_suffix = (
            f" | avg:{average_count}/{average_target}"
            if averaging_enabled and average_target
            else f" | avg:{average_count}"
            if averaging_enabled
            else ""
        )
        frame_label = (
            f"avg frame {max(1, int(average_count or 0))}"
            if averaging_enabled and average_count
            else f"frame {measurement.metadata.get('frame_index', '?')}"
        )
        double_hit_suffix = ""
        if measurement.metadata.get("double_hit_reference_channel"):
            double_hit_suffix = (
                f" | ref={measurement.metadata.get('double_hit_reference_channel')}"
                f" peaks={measurement.metadata.get('double_hit_peak_count', 0)}"
            )
        self.run_info_label.setText(
            f"State: {frame_label}"
            f"{average_suffix}"
            f" | dblhit={measurement.metadata.get('double_hit_rejected', False)}"
            f" | ovld={measurement.metadata.get('overload_rejected', False)}"
            f"{double_hit_suffix}"
        )
        if measurement.metadata.get("rejected", False):
            reasons = []
            if measurement.metadata.get("double_hit_rejected", False):
                reasons.append("double hit")
            if measurement.metadata.get("overload_rejected", False):
                reasons.append("overload")
            reason_text = ", ".join(reasons) if reasons else "reject"
            status = f"Rejected frame {measurement.metadata.get('frame_index', '?')} ({reason_text})"
            if measurement.metadata.get("double_hit_reference_channel"):
                status += (
                    f" | ref={measurement.metadata.get('double_hit_reference_channel')}"
                    f" peaks={measurement.metadata.get('double_hit_peak_count', 0)}"
                )
            if averaging_enabled and average_target:
                status += f" | avg {int(average_count)}/{int(average_target)}"
        elif averaging_enabled and average_count:
            status = f"Avg frame {int(average_count)}"
            if average_target:
                status += f"/{int(average_target)}"
            status += " acquired"
        else:
            status = f"Frame {measurement.metadata.get('frame_index', '?')} acquired"
        self.statusBar().showMessage(status)

    @QtCore.Slot(object)
    def _handle_recording_preview(self, measurement) -> None:
        self._plot_measurement_view(
            self.top_plot,
            measurement,
            "time",
            self._value_mode(self.top_value_mode_combo),
        )
        self._plot_measurement_view(
            self.bottom_plot,
            measurement,
            "time",
            self._value_mode(self.bottom_value_mode_combo),
        )

    @QtCore.Slot(object)
    def _handle_recording_status(self, status: RecordingStatus) -> None:
        elapsed = int(status.elapsed_seconds)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        text = (
            f"Recording: {hours:02d}:{minutes:02d}:{seconds:02d} stored"
            f" | segment {status.segment_index}"
            f" | samples {status.total_samples}"
        )
        self.run_info_label.setText(text)
        self.statusBar().showMessage(text)

    @QtCore.Slot(str)
    def _handle_worker_error(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.avg_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        self.run_info_label.setText("State: error")
        self.statusBar().showMessage(f"Acquisition error: {message}")
        QtWidgets.QMessageBox.critical(self, "Acquisition Failed", message)

    @QtCore.Slot()
    def _acquisition_worker_finished(self) -> None:
        self.run_info_label.setText("State: finalizing")

    @QtCore.Slot()
    def _acquisition_thread_finished(self) -> None:
        self._acquisition_worker = None
        self._acquisition_thread = None
        self._acquisition_stop_event = None
        self.start_button.setEnabled(True)
        self.avg_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        if self.run_info_label.text() not in {"State: error"}:
            self.run_info_label.setText("State: stopped")
            self.statusBar().showMessage("Acquisition stopped")

    @QtCore.Slot()
    def _recording_worker_finished(self) -> None:
        self.run_info_label.setText("State: finalizing record")

    @QtCore.Slot()
    def _recording_thread_finished(self) -> None:
        output_dir = self._recording_output_dir
        self._recording_worker = None
        self._recording_thread = None
        self._recording_stop_event = None
        self.start_button.setEnabled(True)
        self.avg_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        if self.run_info_label.text() not in {"State: error"}:
            self.run_info_label.setText("State: stopped")
            if output_dir is not None:
                self.statusBar().showMessage(f"Continuous recording saved to {output_dir}")
            else:
                self.statusBar().showMessage("Continuous recording stopped")

    def _plot_measurement(self, measurement) -> None:
        self._plot_measurement_view(
            self.top_plot,
            measurement,
            self._display_mode(self.top_display_combo),
            self._value_mode(self.top_value_mode_combo),
        )
        self._plot_measurement_view(
            self.bottom_plot,
            measurement,
            self._display_mode(self.bottom_display_combo),
            self._value_mode(self.bottom_value_mode_combo),
        )
        self._update_plot_layout(self.layout_mode_combo.currentText())
        self._update_axis_labels()
        self._apply_axis_scale(
            "top",
            y_scope=self._axis_y_scope_for_plot("top"),
        )
        self._apply_axis_scale(
            "bottom",
            y_scope=self._axis_y_scope_for_plot("bottom"),
        )
        self._refresh_cursor_for_current_curve("top")
        self._refresh_cursor_for_current_curve("bottom")
        self._refresh_markers("top")
        self._refresh_markers("bottom")
        self._update_marker_readout("top")
        self._update_marker_readout("bottom")

    def _axis_y_scope_for_plot(self, key: str) -> str:
        if self._channel_full_scale_focus.get(key) is not None:
            return "channel_full_scale"
        if self._auto_y_follow_visible_x.get(key, False):
            return "visible"
        if self._display_mode(self._display_combo_for_key(key)) == "time":
            return "channel_full_scale"
        return "legacy"

    @staticmethod
    def _value_mode_options(display_mode: str) -> list[str]:
        return [value for _label, value in MainWindow.VALUE_MODE_ITEMS.get(display_mode, [("raw", "raw")])]

    @staticmethod
    def _value_mode_labels(display_mode: str) -> list[tuple[str, str]]:
        return MainWindow.VALUE_MODE_ITEMS.get(display_mode, [("raw", "raw")])

    def _sync_value_mode_options(self, combo: QtWidgets.QComboBox, display_mode: str) -> None:
        current = self._value_mode(combo)
        options = self._value_mode_labels(display_mode)
        combo.blockSignals(True)
        combo.clear()
        for label, value in options:
            combo.addItem(label, value)
        option_values = [value for _label, value in options]
        if current in option_values:
            combo.setCurrentIndex(option_values.index(current))
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
        if combo is self.top_value_mode_combo:
            self._set_combo_items(
                self.top_value_strip_combo,
                [label for label, _value in options],
                combo.currentText(),
            )
        elif combo is self.bottom_value_mode_combo:
            self._set_combo_items(
                self.bottom_value_strip_combo,
                [label for label, _value in options],
                combo.currentText(),
            )

    @staticmethod
    def _set_combo_items(combo: QtWidgets.QComboBox, items: list[str], current: str | None = None) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if current and current in items:
            combo.setCurrentText(current)
        elif items:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    @staticmethod
    def _sync_value_combo_from_strip(combo: QtWidgets.QComboBox, text: str) -> None:
        for index in range(combo.count()):
            if combo.itemText(index) == text:
                combo.setCurrentIndex(index)
                return

    @staticmethod
    def _default_value_mode_for_display(display_mode: str) -> str | None:
        defaults = {
            "autospectrum": "log_power_per_hz",
            "frf": "dB",
        }
        return defaults.get(display_mode)

    def _apply_default_value_mode(self, key: str) -> None:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        default_value_mode = self._default_value_mode_for_display(display_mode)
        value_combo = self._value_combo_for_key(key)
        if default_value_mode in self._value_mode_options(display_mode):
            self._set_combo_data_silent(value_combo, default_value_mode)
            self._sync_value_strip_from_combo(value_combo)

    def _display_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        return self.top_display_combo if key == "top" else self.bottom_display_combo

    def _value_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        return self.top_value_mode_combo if key == "top" else self.bottom_value_mode_combo

    def _display_strip_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        return self.top_display_strip_combo if key == "top" else self.bottom_display_strip_combo

    def _value_strip_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        return self.top_value_strip_combo if key == "top" else self.bottom_value_strip_combo

    def _yscale_combo_for_key(self, key: str) -> QtWidgets.QComboBox:
        return self.top_yscale_combo if key == "top" else self.bottom_yscale_combo

    @staticmethod
    def _default_xscale_for_display(display_mode: str) -> str:
        if display_mode in {"autospectrum", "frf", "coherence", "cross_spectrum"}:
            return "log"
        return "linear"

    @staticmethod
    def _default_yscale_for_value(display_mode: str, value_mode: str) -> str:
        if display_mode == "autospectrum" and value_mode.startswith("log_"):
            return "log"
        if display_mode in {"frf", "cross_spectrum"} and value_mode == "log_mag":
            return "log"
        return "linear"

    def _reset_plot_display_state(self) -> None:
        for key in ("top", "bottom"):
            self._manual_x_ranges[key] = None
            self._manual_y_ranges[key] = None
            self._channel_full_scale_focus[key] = None
            self._auto_y_follow_visible_x[key] = True
            self._preferred_trace_checks[key] = None
            self._active_trace_names[key] = None
            self._trace_checks_user_modified[key] = False
        for combo in (
            self.top_xscale_combo,
            self.bottom_xscale_combo,
            self.top_yscale_combo,
            self.bottom_yscale_combo,
        ):
            self._set_combo_text_silent(combo, "linear")

    def _set_combo_text_silent(self, combo: QtWidgets.QComboBox, text: str) -> None:
        if combo.currentText() == text:
            return
        combo.blockSignals(True)
        combo.setCurrentText(text)
        combo.blockSignals(False)

    def _set_combo_data_silent(self, combo: QtWidgets.QComboBox, value: str) -> None:
        if self._value_mode(combo) == value:
            return
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
                return

    def _sync_display_strip_from_combo(self, combo: QtWidgets.QComboBox) -> None:
        if not hasattr(self, "top_display_strip_combo"):
            return
        if combo is self.top_display_combo:
            self._set_combo_text_silent(self.top_display_strip_combo, combo.currentText())
        elif combo is self.bottom_display_combo:
            self._set_combo_text_silent(self.bottom_display_strip_combo, combo.currentText())

    def _set_display_mode_silent(self, combo: QtWidgets.QComboBox, mode: str) -> None:
        if self._display_mode(combo) == mode:
            self._sync_display_strip_from_combo(combo)
            return
        for index in range(combo.count()):
            if combo.itemData(index) == mode:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
                self._sync_display_strip_from_combo(combo)
                return
        self._set_combo_text_silent(combo, self._display_label_for_mode(mode))
        self._sync_display_strip_from_combo(combo)

    def _sync_value_strip_from_combo(self, combo: QtWidgets.QComboBox) -> None:
        if combo is self.top_value_mode_combo:
            self._set_combo_items(
                self.top_value_strip_combo,
                [combo.itemText(index) for index in range(combo.count())],
                combo.currentText(),
            )
        elif combo is self.bottom_value_mode_combo:
            self._set_combo_items(
                self.bottom_value_strip_combo,
                [combo.itemText(index) for index in range(combo.count())],
                combo.currentText(),
            )

    def _apply_default_axis_modes(self, key: str) -> None:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        self._set_combo_text_silent(
            self._xscale_combo_for_key(key), self._default_xscale_for_display(display_mode)
        )
        self._set_combo_text_silent(
            self._yscale_combo_for_key(key), self._default_yscale_for_value(display_mode, value_mode)
        )

    def _display_mode_changed(self, key: str) -> None:
        self._manual_x_ranges[key] = None
        self._manual_y_ranges[key] = None
        self._channel_full_scale_focus[key] = None
        self._auto_y_follow_visible_x[key] = True
        self._preferred_trace_checks[key] = None
        self._trace_checks_user_modified[key] = False
        self._apply_default_value_mode(key)
        self._apply_default_axis_modes(key)
        self._refresh_trace_lists_from_available_sources()
        self._refresh_current_measurement_view()

    def _value_mode_changed(self, key: str) -> None:
        self._manual_y_ranges[key] = None
        self._channel_full_scale_focus[key] = None
        self._auto_y_follow_visible_x[key] = True
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        self._set_combo_text_silent(
            self._yscale_combo_for_key(key), self._default_yscale_for_value(display_mode, value_mode)
        )
        self._refresh_current_measurement_view()

    def _apply_display_defaults_for_measurement(self, measurement) -> None:
        if measurement is None:
            self._refresh_trace_lists_from_available_sources()
            return
        if self._apply_legacy_display_state(measurement):
            return
        top_mode = "time"
        if not measurement.time_data.get("channels") and measurement.spectra.get("autospectrum"):
            top_mode = "autospectrum"
        bottom_mode = "frf"
        if not measurement.frf:
            if measurement.coherence:
                bottom_mode = "coherence"
            elif measurement.cross_spectra:
                bottom_mode = "cross_spectrum"
            elif measurement.spectra.get("autospectrum"):
                bottom_mode = "autospectrum"
            elif measurement.time_data.get("channels"):
                bottom_mode = "time"
        self.top_display_combo.setCurrentText(self._display_label_for_mode(top_mode))
        self.bottom_display_combo.setCurrentText(self._display_label_for_mode(bottom_mode))
        self._refresh_trace_lists_from_available_sources()

    def _apply_legacy_display_state(self, measurement) -> bool:
        state = getattr(measurement, "metadata", {}).get("legacy_display_state")
        if not isinstance(state, dict) or not state:
            return False
        layout = state.get("layout")
        if layout in {"dual", "single"} and hasattr(self, "layout_mode_combo"):
            self._set_combo_text_silent(self.layout_mode_combo, str(layout))
            self._update_plot_layout(str(layout))
        for key in ("top", "bottom"):
            panel_state = state.get(key)
            if not isinstance(panel_state, dict):
                continue
            display_mode = panel_state.get("mode")
            if isinstance(display_mode, str):
                display_combo = self._display_combo_for_key(key)
                value_combo = self._value_combo_for_key(key)
                self._set_display_mode_silent(display_combo, display_mode)
                self._sync_value_mode_options(value_combo, display_mode)
            value_mode = panel_state.get("value_mode")
            if isinstance(value_mode, str) and value_mode in self._value_mode_options(
                self._display_mode(self._display_combo_for_key(key))
            ):
                self._set_combo_data_silent(self._value_combo_for_key(key), value_mode)
                self._sync_value_strip_from_combo(self._value_combo_for_key(key))
            current_display_mode = self._display_mode(self._display_combo_for_key(key))
            current_value_mode = self._value_mode(self._value_combo_for_key(key))
            self._set_combo_text_silent(
                self._yscale_combo_for_key(key),
                self._default_yscale_for_value(current_display_mode, current_value_mode),
            )
            xscale = panel_state.get("xscale")
            if xscale in {"linear", "log"}:
                self._set_combo_text_silent(self._xscale_combo_for_key(key), str(xscale))
            axis_range = panel_state.get("axis_range")
            if isinstance(axis_range, dict):
                try:
                    xmin = float(axis_range["xmin"])
                    xmax = float(axis_range["xmax"])
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    if xmax > xmin and (xscale != "log" or xmin > 0.0):
                        self._manual_x_ranges[key] = (xmin, xmax)
                    self._manual_y_ranges[key] = None
                    self._channel_full_scale_focus[key] = None
                    self._auto_y_follow_visible_x[key] = True
            trace_names = panel_state.get("trace_names")
            if isinstance(trace_names, list):
                available_names = self._measurement_trace_names_for_display(
                    measurement, self._display_mode(self._display_combo_for_key(key))
                )
                if not available_names:
                    available_names = self._configured_trace_names_for_display(
                        self._display_mode(self._display_combo_for_key(key))
                    )
                resolved_trace_names = self._resolve_trace_names(
                    [str(name) for name in trace_names], available_names
                )
                if not resolved_trace_names:
                    resolved_trace_names = [str(name) for name in trace_names]
                self._preferred_trace_checks[key] = set(resolved_trace_names)
                if resolved_trace_names:
                    self._active_trace_names[key] = resolved_trace_names[0]
        self._refresh_trace_lists_from_available_sources()
        self._update_axis_labels()
        return True

    @classmethod
    def _display_label_for_mode(cls, mode: str) -> str:
        for label, value in cls.DISPLAY_MODE_ITEMS:
            if value == mode:
                return label
        return mode

    def _capture_current_legacy_display_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "layout": self.layout_mode_combo.currentText() if hasattr(self, "layout_mode_combo") else "dual"
        }
        for key in ("top", "bottom"):
            display_mode = self._display_mode(self._display_combo_for_key(key))
            value_mode = self._value_mode(self._value_combo_for_key(key))
            available_names = self._available_trace_names_for_key(key)
            checked_names = self._checked_trace_names(key)
            trace_names = [name for name in available_names if name in checked_names]
            if not trace_names and available_names:
                trace_names = available_names
            panel_state: dict[str, object] = {
                "mode": display_mode,
                "value_mode": value_mode,
                "xscale": self._xscale_combo_for_key(key).currentText(),
                "trace_names": trace_names,
                "reference_channel": self._current_reference_name(),
            }
            existing_state = {}
            measurement = self.controller.state.measurement
            metadata = getattr(measurement, "metadata", {}) if measurement is not None else {}
            legacy_state = metadata.get("legacy_display_state", {}) if isinstance(metadata, dict) else {}
            if isinstance(legacy_state, dict) and isinstance(legacy_state.get(key), dict):
                existing_state = legacy_state[key]
            for field_name in (
                "legacy_yintfac_index",
                "legacy_yapcor_index",
                "legacy_xcref_index",
                "legacy_x_unit_index",
            ):
                panel_state[field_name] = existing_state.get(field_name, 1)
            state[key] = panel_state
        return state

    def _snapshot_with_current_display_state(self):
        snapshot = self.controller.snapshot()
        if snapshot.measurement is None:
            return snapshot
        filtered_measurement = filter_measurement_to_enabled_channels(
            snapshot.measurement,
            snapshot.config,
        )
        if filtered_measurement is None:
            return snapshot
        filtered_measurement.metadata = {
            **filtered_measurement.metadata,
            "legacy_display_state": self._capture_current_legacy_display_state(),
        }
        return SavedSession(
            config=snapshot.config,
            measurement=filtered_measurement,
            source_path=snapshot.source_path,
        )

    @staticmethod
    def _transform_curve(values: np.ndarray, value_mode: str) -> np.ndarray:
        return transform_curve(values, value_mode)

    @staticmethod
    def _legacy_frequency_int_vector(freqs: np.ndarray, yintfac_index: int) -> np.ndarray:
        return legacy_frequency_int_vector(freqs, yintfac_index)

    @staticmethod
    def _legacy_j_factor(yintfac_index: int) -> complex:
        return legacy_j_factor(yintfac_index)

    @staticmethod
    def _align_vector_to_values(vector: np.ndarray | float, values: np.ndarray) -> np.ndarray | float:
        return align_vector_to_values(vector, values)

    @staticmethod
    def _transform_autospectrum(values: np.ndarray, value_mode: str, rbw_hz: float = 1.0) -> np.ndarray:
        return transform_autospectrum(values, value_mode, rbw_hz)

    @staticmethod
    def _transform_legacy_autospectrum(
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
        return transform_legacy_autospectrum(
            values,
            value_mode,
            rbw_hz,
            euscale_fac=euscale_fac,
            db_ref=db_ref,
            units_value=units_value,
            wincor=wincor,
            yapcor_index=yapcor_index,
            int_vec=int_vec,
        )

    @staticmethod
    def _measurement_rbw(measurement) -> float:
        metadata = getattr(measurement, "metadata", {})
        try:
            rbw_hz = float(metadata.get("rbw_hz", 0.0))
        except (TypeError, ValueError, AttributeError):
            rbw_hz = 0.0
        if rbw_hz > 0.0:
            return rbw_hz
        freqs = np.asarray(measurement.spectra.get("f", []), dtype=float)
        if freqs.size > 1:
            return max(float(freqs[1] - freqs[0]), 1e-20)
        return 1.0

    @staticmethod
    def _trace_response_name(trace_name: str) -> str:
        return trace_name.split("->")[-1].strip()

    @staticmethod
    def _trace_reference_name(trace_name: str, default: str = "ai0") -> str:
        if "->" in trace_name:
            return trace_name.split("->", 1)[0].strip()
        return default

    @staticmethod
    def _effective_euscale_fac(sensitivity: float, per_eu_mode: str | None = "/Volt") -> float:
        normalized = (per_eu_mode or "/Volt").strip().lower()
        if normalized in {"", "off", "none"}:
            return 1.0
        per_voltage_factor = {
            "/volt": 1.0,
            "/v": 1.0,
            "/mv": 1_000.0,
            "/millivolt": 1_000.0,
            "/uv": 1_000_000.0,
            "/microvolt": 1_000_000.0,
            "/kv": 0.001,
            "/kilovolt": 0.001,
        }.get(normalized, 1.0)
        return float(sensitivity) * per_voltage_factor

    def _legacy_channel_display_params(self, measurement, trace_name: str) -> dict[str, float | str]:
        metadata = getattr(measurement, "metadata", {})
        legacy_channels = metadata.get("legacy_channels", {})
        if isinstance(legacy_channels, dict):
            if trace_name in legacy_channels:
                return dict(legacy_channels[trace_name])
            for params in legacy_channels.values():
                if not isinstance(params, dict):
                    continue
                if params.get("label") == trace_name or params.get("name") == trace_name:
                    return dict(params)
        for channel in getattr(self.session, "ai_channels", []):
            if channel.name == trace_name or channel.label == trace_name:
                return {
                    "name": channel.name,
                    "label": channel.label,
                    "euscale_fac": self._effective_euscale_fac(
                        channel.sensitivity, channel.per_eu_mode
                    ),
                    "db_ref": float(channel.db_reference),
                    "fs_val": float(channel.full_scale),
                    "eu_string": channel.engineering_unit,
                    "per_eu_mode": channel.per_eu_mode,
                }
        return {
            "name": trace_name,
            "label": trace_name,
            "euscale_fac": 1.0,
            "db_ref": 1.0,
            "fs_val": 1.0,
            "eu_string": "",
        }

    def _channel_display_params(self, measurement, trace_name: str) -> dict[str, float | str]:
        return self._legacy_channel_display_params(measurement, trace_name)

    def _transform_time_for_trace(
        self,
        measurement,
        trace_name: str,
        values: np.ndarray,
        value_mode: str,
    ) -> np.ndarray:
        channel_params = self._channel_display_params(measurement, trace_name)
        scale = float(channel_params.get("euscale_fac", 1.0))
        scaled = np.asarray(values) * scale
        return self._transform_curve(scaled, value_mode)

    def _legacy_panel_state(self, measurement, key: str) -> dict:
        metadata = getattr(measurement, "metadata", {})
        state = metadata.get("legacy_display_state", {})
        panel_state = state.get(key, {}) if isinstance(state, dict) else {}
        return panel_state if isinstance(panel_state, dict) else {}

    @staticmethod
    def _uses_legacy_display_scaling(measurement) -> bool:
        metadata = getattr(measurement, "metadata", {})
        return bool(
            isinstance(metadata, dict)
            and (metadata.get("source") == "legacy_vna" or metadata.get("legacy_channels"))
        )

    def _legacy_panel_int_vec(self, measurement, key: str) -> np.ndarray:
        freqs = np.asarray(measurement.spectra.get("f", []), dtype=float)
        if freqs.size == 0:
            return np.array([], dtype=float)
        panel_state = self._legacy_panel_state(measurement, key)
        yintfac_index = int(panel_state.get("legacy_yintfac_index", 1))
        return self._legacy_frequency_int_vector(freqs, yintfac_index)

    def _panel_int_vec(self, measurement, key: str) -> np.ndarray:
        return self._legacy_panel_int_vec(measurement, key)

    def _panel_yapcor_index(self, measurement, key: str) -> int:
        panel_state = self._legacy_panel_state(measurement, key)
        return int(panel_state.get("legacy_yapcor_index", 1))

    def _measurement_display_units_value(self, measurement) -> float:
        metadata = getattr(measurement, "metadata", {})
        if isinstance(metadata, dict) and "legacy_units_value" in metadata:
            try:
                return float(metadata.get("legacy_units_value", 1.0))
            except (TypeError, ValueError):
                return 1.0
        return 1.0

    def _measurement_window_correction(self, measurement) -> float:
        metadata = getattr(measurement, "metadata", {})
        if isinstance(metadata, dict) and "legacy_runtime_wincor" in metadata:
            try:
                return float(metadata.get("legacy_runtime_wincor", 1.0))
            except (TypeError, ValueError):
                return 1.0
        window_name = str(metadata.get("processing_window", "")) if isinstance(metadata, dict) else ""
        if window_name.strip().lower() in {"hann", "hanning"}:
            return 2.0 / 3.0
        if isinstance(metadata, dict) and "legacy_wincor" in metadata:
            try:
                return float(metadata.get("legacy_wincor", 1.0))
            except (TypeError, ValueError):
                return 1.0
        return 1.0

    def _transform_frf_for_trace(
        self,
        measurement,
        cache_key: str,
        trace_name: str,
        values: np.ndarray,
        value_mode: str,
    ) -> np.ndarray:
        reference_name = self._trace_reference_name(trace_name)
        response_name = self._trace_response_name(trace_name)
        reference = self._channel_display_params(measurement, reference_name)
        response = self._channel_display_params(measurement, response_name)
        ref_scale = max(abs(float(reference.get("euscale_fac", 1.0))), 1e-20)
        resp_scale = float(response.get("euscale_fac", 1.0))
        int_vec = self._panel_int_vec(measurement, cache_key)
        if int_vec.size == 0:
            int_vec = 1.0
        panel_state = self._legacy_panel_state(measurement, cache_key)
        yintfac_index = int(panel_state.get("legacy_yintfac_index", 1))
        xfer_int_vec = self._legacy_j_factor(yintfac_index) * np.sqrt(int_vec)
        xfer_int_vec = self._align_vector_to_values(xfer_int_vec, np.asarray(values))
        values = np.asarray(values)[: np.asarray(xfer_int_vec).size] if np.asarray(xfer_int_vec).ndim else np.asarray(values)
        scaled = (resp_scale / ref_scale) * np.asarray(values) * xfer_int_vec
        return self._transform_curve(scaled, value_mode)

    def _transform_cross_spectrum_for_trace(
        self,
        measurement,
        cache_key: str,
        trace_name: str,
        values: np.ndarray,
        value_mode: str,
    ) -> np.ndarray:
        reference_name = self._trace_reference_name(trace_name)
        response_name = self._trace_response_name(trace_name)
        reference = self._channel_display_params(measurement, reference_name)
        response = self._channel_display_params(measurement, response_name)
        ref_scale = float(reference.get("euscale_fac", 1.0))
        resp_scale = float(response.get("euscale_fac", 1.0))
        int_vec = self._panel_int_vec(measurement, cache_key)
        if int_vec.size == 0:
            int_vec = 1.0
        int_vec = self._align_vector_to_values(int_vec, np.asarray(values))
        values = np.asarray(values)[: np.asarray(int_vec).size] if np.asarray(int_vec).ndim else np.asarray(values)
        scaled = (resp_scale * ref_scale) * np.asarray(values) * int_vec
        if value_mode == "dB":
            return 10.0 * np.log10(np.maximum(np.abs(scaled), 1e-307))
        return self._transform_curve(scaled, value_mode)

    def _plot_measurement_view(self, plot, measurement, mode: str, value_mode: str) -> None:
        plot.clear()
        legend = plot.plotItem.legend
        if legend is not None:
            legend.clear()
        colors = self.TRACE_COLORS
        cache_key = "top" if plot is self.top_plot else "bottom"
        self._set_combo_text_silent(
            self._yscale_combo_for_key(cache_key),
            self._default_yscale_for_value(mode, value_mode),
        )
        self._plot_curve_items[cache_key] = {}
        self._plot_curve_colors[cache_key] = {}
        raw_visible_names = self._checked_trace_names(cache_key)
        preferred_visible_names = self._preferred_trace_checks.get(cache_key)
        def selected_names(available_names: list[str]) -> set[str]:
            if preferred_visible_names is not None:
                preferred_names = set(preferred_visible_names).intersection(available_names)
                if not preferred_names:
                    preferred_names = set(
                        self._resolve_trace_names(list(preferred_visible_names), available_names)
                    )
                if preferred_names:
                    return preferred_names
            if not raw_visible_names:
                return set()
            resolved_names = self._resolve_trace_names(list(raw_visible_names), available_names)
            return set(resolved_names)

        self._configure_plot_xscale(plot, cache_key)
        for line in self._marker_lines[cache_key]:
            plot.addItem(line, ignoreBounds=True)
        for point in self._marker_points[cache_key]:
            plot.addItem(point)
        for text in self._marker_texts[cache_key]:
            plot.addItem(text)
        if self._cursor_lines[cache_key] is not None:
            plot.addItem(self._cursor_lines[cache_key], ignoreBounds=True)
        if self._cursor_points[cache_key] is not None:
            plot.addItem(self._cursor_points[cache_key])
        if self._cursor_texts[cache_key] is not None:
            plot.addItem(self._cursor_texts[cache_key])
        for point in self._marker_history_points[cache_key]:
            plot.addItem(point)
        for text in self._marker_history_texts[cache_key]:
            plot.addItem(text)
        for data_tip in self._data_tip_items[cache_key]:
            plot.addItem(data_tip["point"])
            plot.addItem(data_tip["text"])
        if self.overlay_checkbox.isChecked():
            for history_index, overlay_curves in enumerate(self._stored_overlays[cache_key]):
                alpha = max(45, 165 - history_index * 22)
                for name, (x_data, y_data) in overlay_curves.items():
                    x_plot, y_plot = self._prepare_curve_xy(cache_key, x_data, y_data)
                    if x_plot.size == 0:
                        continue
                    curve_item = plot.plot(
                        x_plot,
                        y_plot,
                        pen=pg.mkPen((220, 220, 220, alpha), width=1.0, style=QtCore.Qt.DashLine),
                        name=None,
                    )
                    curve_item.setZValue(CURVE_Z - 1)
                    curve_item.setCurveClickable(False)

        current_curves: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        active_trace_name = self._active_trace_names.get(cache_key)
        def legend_name(trace_name: str) -> str:
            return self._legend_display_name(trace_name, mode)

        def register_curve(trace_name: str, curve_item, color: str, x_plot: np.ndarray, y_plot: np.ndarray) -> None:
            curve_item.setZValue(CURVE_Z)
            self._plot_curve_items[cache_key][trace_name] = curve_item
            self._plot_curve_colors[cache_key][trace_name] = color
            current_curves[trace_name] = (x_plot, y_plot)

        if mode == "time":
            time_t = measurement.time_data["t"]
            available_names = list(measurement.time_data["channels"].keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.time_data["channels"].items()):
                if visible_names and name not in visible_names:
                    continue
                y = self._transform_time_for_trace(measurement, name, values, value_mode)
                x_plot, y_plot = self._prepare_curve_xy(cache_key, time_t, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return

        freqs = measurement.spectra["f"]
        if mode == "autospectrum":
            rbw_hz = self._measurement_rbw(measurement)
            metadata = getattr(measurement, "metadata", {})
            panel_state = self._legacy_panel_state(measurement, cache_key)
            int_vec = self._legacy_panel_int_vec(measurement, cache_key)
            if int_vec.size == 0:
                int_vec = 1.0
            available_names = list(measurement.spectra["autospectrum"].keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.spectra["autospectrum"].items()):
                if visible_names and name not in visible_names:
                    continue
                channel_params = self._channel_display_params(measurement, name)
                y = self._transform_legacy_autospectrum(
                    values,
                    value_mode,
                    rbw_hz,
                    euscale_fac=float(channel_params.get("euscale_fac", 1.0)),
                    db_ref=float(channel_params.get("db_ref", 1.0)),
                    units_value=self._measurement_display_units_value(measurement),
                    wincor=self._measurement_window_correction(measurement),
                    yapcor_index=self._panel_yapcor_index(measurement, cache_key),
                    int_vec=int_vec,
                )
                x_plot, y_plot = self._prepare_curve_xy(cache_key, freqs, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode == "fft":
            available_names = list(measurement.spectra["fft"].keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.spectra["fft"].items()):
                if visible_names and name not in visible_names:
                    continue
                channel_params = self._channel_display_params(measurement, name)
                scale = (
                    abs(float(channel_params.get("euscale_fac", 1.0)))
                    * self._measurement_display_units_value(measurement)
                )
                y = self._transform_curve(np.asarray(values) * scale, value_mode)
                x_plot, y_plot = self._prepare_curve_xy(cache_key, freqs, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode == "coherence":
            available_names = list(measurement.coherence.keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.coherence.items()):
                if visible_names and name not in visible_names:
                    continue
                x_plot, y_plot = self._prepare_curve_xy(cache_key, freqs, values)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode == "frf":
            available_names = list(measurement.frf.keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.frf.items()):
                if visible_names and name not in visible_names:
                    continue
                if value_mode == "nyquist":
                    scaled = self._transform_frf_for_trace(
                        measurement, cache_key, name, values, "raw"
                    )
                    x_plot, y_plot = self._prepare_curve_xy(cache_key, np.real(scaled), np.imag(scaled))
                else:
                    y = self._transform_frf_for_trace(
                        measurement, cache_key, name, values, value_mode
                    )
                    x_plot, y_plot = self._prepare_curve_xy(cache_key, freqs, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode == "cross_spectrum":
            available_names = list(measurement.cross_spectra.keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.cross_spectra.items()):
                if visible_names and name not in visible_names:
                    continue
                if value_mode == "nyquist":
                    scaled = self._transform_cross_spectrum_for_trace(
                        measurement, cache_key, name, values, "raw"
                    )
                    x_plot, y_plot = self._prepare_curve_xy(cache_key, np.real(scaled), np.imag(scaled))
                else:
                    y = self._transform_cross_spectrum_for_trace(
                        measurement, cache_key, name, values, value_mode
                    )
                    x_plot, y_plot = self._prepare_curve_xy(cache_key, freqs, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode in {"auto_correlation", "cross_correlation"}:
            correlation_map = measurement.correlations
            available_names = list(correlation_map.keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(correlation_map.items()):
                if visible_names and name not in visible_names:
                    continue
                time_axis = np.arange(values.shape[-1], dtype=float) / measurement.sample_rate
                y = self._transform_curve(values, value_mode)
                x_plot, y_plot = self._prepare_curve_xy(cache_key, time_axis, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)
            return
        if mode == "impulse_response":
            available_names = list(measurement.impulse_responses.keys())
            visible_names = selected_names(available_names)
            for idx, (name, values) in enumerate(measurement.impulse_responses.items()):
                if visible_names and name not in visible_names:
                    continue
                time_axis = np.arange(values.shape[-1], dtype=float) / measurement.sample_rate
                y = self._transform_curve(values, value_mode)
                x_plot, y_plot = self._prepare_curve_xy(cache_key, time_axis, y)
                if x_plot.size == 0:
                    continue
                curve_item = plot.plot(
                    x_plot,
                    y_plot,
                    pen=self._curve_pen(colors[idx % len(colors)], name, active_trace_name),
                    name=legend_name(name),
                )
                register_curve(name, curve_item, colors[idx % len(colors)], x_plot, y_plot)
            self._last_plot_cache[cache_key] = current_curves
            self._update_trace_selector(cache_key, current_curves, available_names)

    def _overlay_toggled(self, enabled: bool) -> None:
        self.overlay_action.setChecked(enabled)
        self._refresh_current_measurement_view()

    @staticmethod
    def _curve_pen(color: str, trace_name: str, active_trace_name: str | None):
        if active_trace_name is None or trace_name == active_trace_name:
            return pg.mkPen(color, width=1.8 if active_trace_name == trace_name else 1.1)
        return pg.mkPen(color, width=0.9, style=QtCore.Qt.SolidLine)

    def _trace_combo_for_key(self, key: str):
        return self.top_trace_combo if key == "top" else self.bottom_trace_combo

    def _trace_list_for_key(self, key: str):
        return self.top_trace_list if key == "top" else self.bottom_trace_list

    def _trace_display_name(self, trace_name: str) -> str:
        channel_name = trace_name.split("->")[-1]
        for row in range(self.channel_table.rowCount()):
            if self.channel_table.item(row, 1).text() == channel_name:
                label = self.channel_table.item(row, 10).text().strip()
                return label or channel_name
        return trace_name

    def _trace_display_name_for_mode(self, trace_name: str, display_mode: str) -> str:
        if display_mode in {"frf", "coherence", "cross_spectrum", "cross_correlation", "impulse_response"}:
            return self._trace_display_name(self._trace_response_name(trace_name))
        return self._trace_display_name(trace_name)

    def _legend_display_name(self, trace_name: str, display_mode: str) -> str:
        return self._trace_display_name_for_mode(trace_name, display_mode)

    def _trace_aliases(self, trace_name: str) -> set[str]:
        aliases = {trace_name}
        channel_aliases: dict[str, set[str]] = {}
        alias_to_channel: dict[str, str] = {}
        if hasattr(self, "channel_table"):
            for row in range(self.channel_table.rowCount()):
                name_item = self.channel_table.item(row, 1)
                label_item = self.channel_table.item(row, 10)
                if name_item is None:
                    continue
                channel = name_item.text().strip()
                label = label_item.text().strip() if label_item is not None else ""
                if not channel:
                    continue
                row_aliases = {channel, f"Channel {row + 1}", f"Ch {row + 1}"}
                if label:
                    row_aliases.add(label)
                channel_aliases[channel] = row_aliases
                for alias in row_aliases:
                    alias_to_channel.setdefault(alias, channel)

        def endpoint_aliases(endpoint: str) -> set[str]:
            endpoint = endpoint.strip()
            endpoint_set = {endpoint} if endpoint else set()
            channel = alias_to_channel.get(endpoint)
            if channel is not None:
                endpoint_set.update(channel_aliases.get(channel, {channel}))
            return endpoint_set

        if "->" in trace_name:
            left, right = [part.strip() for part in trace_name.split("->", 1)]
            left_aliases = endpoint_aliases(left)
            right_aliases = endpoint_aliases(right)
            for left_alias in left_aliases:
                for right_alias in right_aliases:
                    if left_alias and right_alias:
                        aliases.add(f"{left_alias}->{right_alias}")
        else:
            aliases.update(endpoint_aliases(trace_name))
        return {alias for alias in aliases if alias}

    def _resolve_trace_names(self, requested_names: list[str], available_names: list[str]) -> list[str]:
        if not requested_names or not available_names:
            return []
        alias_to_available: dict[str, str] = {}
        for available in available_names:
            for alias in self._trace_aliases(available):
                alias_to_available.setdefault(alias, available)
        resolved: list[str] = []
        for requested in requested_names:
            requested_text = str(requested).strip()
            candidates = {requested_text}
            if "->" in requested_text:
                left, right = [part.strip() for part in requested_text.split("->", 1)]
                for left_alias in self._trace_aliases(left):
                    for right_alias in self._trace_aliases(right):
                        if left_alias and right_alias:
                            candidates.add(f"{left_alias}->{right_alias}")
            else:
                candidates.update(self._trace_aliases(requested_text))
            for candidate in candidates:
                match = alias_to_available.get(candidate)
                if match and match not in resolved:
                    resolved.append(match)
                    break
        return resolved

    @staticmethod
    def _trace_list_height(row_count: int) -> int:
        visible_rows = min(max(row_count, 1), 4)
        return 4 + visible_rows * 26

    def _update_trace_selector(
        self,
        key: str,
        curves: dict[str, tuple[np.ndarray, np.ndarray]],
        available_names: list[str] | None = None,
    ) -> None:
        combo = self._trace_combo_for_key(key)
        trace_list = self._trace_list_for_key(key)
        names = available_names or list(curves.keys())
        preferred = self._active_trace_names.get(key)
        if preferred not in curves:
            preferred = next(iter(curves), names[0] if names else None)
        self._active_trace_names[key] = preferred
        self._populate_trace_combo(combo, key, names, preferred)
        strip_combo = self.top_trace_strip_combo if key == "top" else self.bottom_trace_strip_combo
        self._populate_trace_combo(strip_combo, key, names, preferred)
        self._sync_trace_list_items(trace_list, key, names)

    def _populate_trace_combo(
        self,
        combo: QtWidgets.QComboBox,
        key: str,
        names: list[str],
        current: str | None = None,
    ) -> None:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        combo.blockSignals(True)
        combo.clear()
        for name in names:
            combo.addItem(self._trace_display_name_for_mode(name, display_mode), name)
        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _trace_combo_selection_changed(self, key: str) -> None:
        combo = self._trace_combo_for_key(key)
        trace_name = combo.currentData()
        self._trace_selection_changed(key, str(trace_name) if trace_name is not None else "")

    def _sync_trace_strip_from_combo(self, key: str) -> None:
        source = self._trace_combo_for_key(key)
        target = self.top_trace_strip_combo if key == "top" else self.bottom_trace_strip_combo
        trace_name = source.currentData()
        if trace_name is None:
            return
        target.blockSignals(True)
        index = target.findData(trace_name)
        if index >= 0:
            target.setCurrentIndex(index)
        target.blockSignals(False)

    def _sync_trace_combo_from_strip(self, key: str) -> None:
        source = self.top_trace_strip_combo if key == "top" else self.bottom_trace_strip_combo
        target = self._trace_combo_for_key(key)
        trace_name = source.currentData()
        if trace_name is None:
            return
        target.blockSignals(True)
        index = target.findData(trace_name)
        if index >= 0:
            target.setCurrentIndex(index)
        target.blockSignals(False)
        self._trace_selection_changed(key, str(trace_name))

    def _sync_trace_list_items(self, trace_list, key: str, names: list[str]) -> None:
        preferred = self._preferred_trace_checks.get(key)
        if preferred is not None:
            checked_names = set(preferred).intersection(names)
            if not checked_names:
                checked_names = set(self._resolve_trace_names(list(preferred), names))
            self._preferred_trace_checks[key] = None
        else:
            checked_names = self._checked_trace_names(key)
        if not checked_names or checked_names.isdisjoint(names):
            checked_names = set(names)
        trace_list.blockSignals(True)
        trace_list.clear()
        display_mode = self._display_mode(self._display_combo_for_key(key))
        for index, name in enumerate(names, start=1):
            item = QtWidgets.QListWidgetItem(f"{index}  {self._trace_display_name_for_mode(name, display_mode)}")
            item.setSizeHint(QtCore.QSize(0, 26))
            item.setData(QtCore.Qt.UserRole, name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if name in checked_names else QtCore.Qt.Unchecked)
            trace_list.addItem(item)
        trace_list.setFixedHeight(self._trace_list_height(len(names)))
        trace_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        trace_list.blockSignals(False)

    def _checked_trace_names(self, key: str) -> set[str]:
        trace_list = self._trace_list_for_key(key)
        names = set()
        for index in range(trace_list.count()):
            item = trace_list.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                names.add(item.data(QtCore.Qt.UserRole) or item.text())
        return names

    def _filter_visible_curves(
        self, key: str, curves: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        checked = self._checked_trace_names(key)
        if not checked:
            return curves
        return {name: curve for name, curve in curves.items() if name in checked}

    def _trace_visibility_changed(self, key: str) -> None:
        self._trace_checks_user_modified[key] = True
        visible = self._filter_visible_curves(key, self._last_plot_cache.get(key, {}))
        active = self._active_trace_names.get(key)
        if active not in visible and visible:
            self._active_trace_names[key] = next(iter(visible))
        self._refresh_markers(key)
        self._update_marker_readout(key)
        measurement = self.controller.state.measurement
        if measurement is not None:
            self._plot_measurement(measurement)

    def _trace_selection_changed(self, key: str, trace_name: str) -> None:
        self._active_trace_names[key] = trace_name or None
        self._refresh_curve_pens(key)
        self._refresh_markers(key)
        self._update_marker_readout(key)

    def _select_channel_editor_for_trace(self, trace_name: str | None) -> None:
        row = self._channel_row_for_trace(trace_name)
        if row is None or not hasattr(self, "channel_list"):
            return
        if self.channel_list.currentRow() != row:
            self.channel_list.setCurrentRow(row)

    def _channel_row_for_trace(self, trace_name: str | None) -> int | None:
        if not trace_name or not hasattr(self, "channel_table"):
            return None
        response_name = self._trace_response_name(str(trace_name))
        candidates = {str(trace_name), response_name}
        candidates.update(self._trace_aliases(response_name))
        for row in range(self.channel_table.rowCount()):
            if self._channel_aliases_for_table_row(row).intersection(candidates):
                return row
        return None

    def _selected_curve(
        self, key: str
    ) -> tuple[str | None, tuple[np.ndarray, np.ndarray] | None]:
        curves = self._last_plot_cache.get(key, {})
        if not curves:
            return None, None
        trace_name = self._active_trace_names.get(key)
        if trace_name not in curves:
            trace_name = next(iter(curves))
            self._active_trace_names[key] = trace_name
        return trace_name, curves[trace_name]

    def _refresh_curve_pens(self, key: str) -> None:
        active_trace_name = self._active_trace_names.get(key)
        for name, curve_item in self._plot_curve_items.get(key, {}).items():
            if hasattr(curve_item, "setPen"):
                color = self._plot_curve_colors.get(key, {}).get(name, "#00ff00")
                curve_item.setPen(self._curve_pen(color, name, active_trace_name))

    def _refresh_current_measurement_view(self) -> None:
        measurement = self.controller.state.measurement
        if measurement is not None:
            self._plot_measurement(measurement)
        else:
            self._apply_axis_scale("top")
            self._apply_axis_scale("bottom")
            self._refresh_markers("top")
            self._refresh_markers("bottom")
            self._update_marker_readout("top")
            self._update_marker_readout("bottom")

    def _plot_widget_for_key(self, key: str):
        return self.top_plot if key == "top" else self.bottom_plot

    def _xscale_combo_for_key(self, key: str):
        return self.top_xscale_combo if key == "top" else self.bottom_xscale_combo

    def _is_log_xscale(self, key: str) -> bool:
        return self._xscale_combo_for_key(key).currentText() == "log"

    def _is_log_yscale(self, key: str) -> bool:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        return (
            self._yscale_combo_for_key(key).currentText() == "log"
            or self._default_yscale_for_value(display_mode, value_mode) == "log"
        )

    def _is_nyquist_display(self, key: str) -> bool:
        display_combo = self.top_display_combo if key == "top" else self.bottom_display_combo
        value_combo = self.top_value_mode_combo if key == "top" else self.bottom_value_mode_combo
        return (
            self._display_mode(display_combo) in {"frf", "cross_spectrum"}
            and self._value_mode(value_combo) == "nyquist"
        )

    def _configure_plot_xscale(self, plot, key: str) -> None:
        plot.setLogMode(
            x=self._is_log_xscale(key) and not self._is_nyquist_display(key),
            y=self._is_log_yscale(key) and not self._is_nyquist_display(key),
        )

    def _prepare_curve_xy(
        self, key: str, x_data: np.ndarray, y_data: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data)
        if x_arr.size == 0 or y_arr.size == 0:
            return np.array([], dtype=float), np.array([], dtype=float)
        point_count = min(x_arr.size, y_arr.size)
        x_arr = x_arr[:point_count]
        y_arr = y_arr[:point_count]
        if self._is_log_xscale(key) and not self._is_nyquist_display(key):
            positive = x_arr > 0.0
            x_arr = x_arr[positive]
            y_arr = y_arr[positive]
        if self._is_log_yscale(key) and not self._is_nyquist_display(key):
            positive = np.asarray(y_arr, dtype=float) > 0.0
            x_arr = x_arr[positive]
            y_arr = y_arr[positive]
            log_floor = self._legacy_log_floor(key, np.asarray(y_arr, dtype=float))
            if log_floor is not None:
                y_arr = np.maximum(np.asarray(y_arr, dtype=float), log_floor)
        return np.asarray(x_arr, dtype=float), np.asarray(y_arr, dtype=float)

    def _curve_x_extent(self, curves: dict[str, tuple[np.ndarray, np.ndarray]]) -> tuple[float, float] | None:
        mins: list[float] = []
        maxs: list[float] = []
        for x_data, _ in curves.values():
            x_arr = np.asarray(x_data, dtype=float)
            if x_arr.size == 0:
                continue
            mins.append(float(np.min(x_arr)))
            maxs.append(float(np.max(x_arr)))
        if not mins:
            return None
        return min(mins), max(maxs)

    def _curve_y_extent(
        self,
        curves: dict[str, tuple[np.ndarray, np.ndarray]],
        key: str,
        scope: str = "legacy",
    ) -> tuple[float, float] | None:
        if scope != "visible":
            legacy_extent = self._legacy_y_extent_for_display(key, curves)
            if legacy_extent is not None:
                return legacy_extent
        if scope == "channel_full_scale":
            full_scale_extent = self._full_scale_y_extent_for_display(key, curves)
            if full_scale_extent is not None:
                return full_scale_extent
        x_visible_range = self._current_visible_x_range(key) if scope == "visible" else None
        values: list[np.ndarray] = []
        for x_data, y_data in curves.values():
            x_arr = np.asarray(x_data, dtype=float)
            y_arr = np.asarray(y_data, dtype=float)
            if y_arr.size == 0:
                continue
            if x_visible_range is not None:
                point_count = min(x_arr.size, y_arr.size)
                x_arr = x_arr[:point_count]
                y_arr = y_arr[:point_count]
                visible = (x_arr >= x_visible_range[0]) & (x_arr <= x_visible_range[1])
                y_arr = y_arr[visible]
            if self._is_log_yscale(key) and not self._is_nyquist_display(key):
                y_arr = y_arr[y_arr > 0.0]
            y_arr = y_arr[np.isfinite(y_arr)]
            if y_arr.size == 0:
                continue
            values.append(y_arr)
        if not values:
            return None
        all_values = np.concatenate(values)
        if self._is_log_yscale(key) and not self._is_nyquist_display(key):
            if scope == "visible":
                return float(np.min(all_values)), float(np.max(all_values))
            floor = self._legacy_log_floor(key, all_values)
            if floor is not None:
                ymax = float(np.max(all_values))
                return floor, ymax
        return float(np.min(all_values)), float(np.max(all_values))

    def _current_visible_x_range(self, key: str) -> tuple[float, float] | None:
        plot = self._plot_widget_for_key(key)
        try:
            current_range = plot.viewRange()[0]
        except Exception:
            return None
        xmin, xmax = self._range_from_plot_axis(
            float(current_range[0]),
            float(current_range[1]),
            self._is_log_xscale(key) and not self._is_nyquist_display(key),
        )
        if xmax <= xmin:
            return None
        return xmin, xmax

    def _legacy_y_extent_for_display(
        self, key: str, curves: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> tuple[float, float] | None:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        if display_mode == "coherence":
            return 0.0, 1.25
        if display_mode in {"frf", "cross_spectrum"}:
            if value_mode == "dB":
                values = self._finite_curve_values(curves, positive_only=False)
                if values.size == 0:
                    return -80.0, 20.0
                top = max(20.0, float(np.nanmax(values)) + 10.0)
                return top - 100.0, top
            if value_mode == "phase":
                return -250.0, 250.0
            if value_mode == "phase_u":
                return -800.0, 250.0
            if value_mode == "mag":
                values = self._finite_curve_values(curves, positive_only=True)
                ymax = float(np.nanmax(values)) if values.size else 1.0
                return 0.0, max(ymax * 1.25, 1e-12)
            if value_mode == "log_mag":
                values = self._finite_curve_values(curves, positive_only=True)
                if values.size == 0:
                    return 1e-8, 1.0
                ymax = max(float(np.nanmax(values)), 1e-20)
                return ymax * 1e-8, ymax * 1.25
        if self._is_nyquist_display(key):
            values = self._finite_curve_values(curves, positive_only=False, include_x=True)
            if values.size == 0:
                return -1.0, 1.0
            ymax = max(float(np.nanmax(np.abs(values))), 1e-12)
            return -1.25 * ymax, 1.25 * ymax
        return None

    @staticmethod
    def _finite_curve_values(
        curves: dict[str, tuple[np.ndarray, np.ndarray]], positive_only: bool, include_x: bool = False
    ) -> np.ndarray:
        values: list[np.ndarray] = []
        for x_data, y_data in curves.values():
            y_arr = np.asarray(y_data, dtype=float)
            y_arr = y_arr[np.isfinite(y_arr)]
            if positive_only:
                y_arr = y_arr[y_arr > 0.0]
            if y_arr.size:
                values.append(y_arr)
            if include_x:
                x_arr = np.asarray(x_data, dtype=float)
                x_arr = x_arr[np.isfinite(x_arr)]
                if positive_only:
                    x_arr = x_arr[x_arr > 0.0]
                if x_arr.size:
                    values.append(x_arr)
        if not values:
            return np.array([], dtype=float)
        return np.concatenate(values)

    def _full_scale_y_extent_for_display(
        self, key: str, curves: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> tuple[float, float] | None:
        if self._display_mode(self._display_combo_for_key(key)) != "time":
            return None
        focus_trace = self._full_scale_focus_trace_for_display(key, curves)
        if focus_trace is not None:
            focused_full_scale = self._channel_full_scale_extent_by_name(
                self._trace_response_name(focus_trace)
            )
            if focused_full_scale is not None and focused_full_scale > 0.0:
                yover = 1.25
                return -yover * focused_full_scale, yover * focused_full_scale
        return None

    def _full_scale_focus_trace_for_display(
        self, key: str, curves: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> str | None:
        if not curves:
            return None

        def resolve_candidate(candidate: str | None) -> str | None:
            if not candidate:
                return None
            if candidate in curves:
                return candidate
            candidate_aliases = self._trace_aliases(str(candidate))
            for trace_name in curves:
                if not self._trace_aliases(trace_name).isdisjoint(candidate_aliases):
                    return trace_name
            return None

        focused = resolve_candidate(self._channel_full_scale_focus.get(key))
        if focused is not None:
            return focused
        return None

    def _channel_table_full_scale(self, row: int) -> float:
        if not hasattr(self, "channel_table") or row < 0 or row >= self.channel_table.rowCount():
            return 10.0
        item = self.channel_table.item(row, 9)
        if item is None:
            return 10.0
        return self._parse_full_scale_text(item.text(), 10.0)

    def _channel_aliases_for_table_row(self, row: int) -> set[str]:
        if not hasattr(self, "channel_table") or row < 0 or row >= self.channel_table.rowCount():
            return set()
        name_item = self.channel_table.item(row, 1)
        label_item = self.channel_table.item(row, 10)
        aliases = {
            f"Ch {row + 1}",
            f"Channel {row + 1}",
        }
        if name_item is not None and name_item.text().strip():
            aliases.add(name_item.text().strip())
        if label_item is not None and label_item.text().strip():
            aliases.add(label_item.text().strip())
        return aliases

    def _channel_full_scale_by_name(self, channel_name: str) -> float | None:
        normalized_name = channel_name.strip()
        if hasattr(self, "channel_table"):
            for row in range(self.channel_table.rowCount()):
                full_scale_item = self.channel_table.item(row, 9)
                if full_scale_item is None:
                    continue
                if normalized_name not in self._channel_aliases_for_table_row(row):
                    continue
                full_scale = self._parse_full_scale_text(full_scale_item.text(), 10.0)
                return full_scale if full_scale > 0.0 else None
        for channel in self.controller.state.session.ai_channels:
            if channel.name == normalized_name or channel.label == normalized_name:
                full_scale = float(channel.full_scale)
                return full_scale if full_scale > 0.0 else None
        return None

    def _channel_full_scale_extent_by_name(self, channel_name: str) -> float | None:
        normalized_name = channel_name.strip()
        if hasattr(self, "channel_table"):
            for row in range(self.channel_table.rowCount()):
                if normalized_name not in self._channel_aliases_for_table_row(row):
                    continue
                full_scale = self._channel_table_full_scale(row)
                sensitivity_item = self.channel_table.item(row, 7)
                sensitivity = self._parse_float(sensitivity_item.text(), 1.0) if sensitivity_item is not None else 1.0
                per_eu_item = self.channel_table.item(row, 12)
                per_eu_mode = per_eu_item.text() if per_eu_item is not None else "/Volt"
                if full_scale > 0.0:
                    return abs(full_scale * self._effective_euscale_fac(sensitivity, per_eu_mode))
                return None
        for channel in self.controller.state.session.ai_channels:
            if channel.name == normalized_name or channel.label == normalized_name:
                full_scale = float(channel.full_scale)
                if full_scale > 0.0:
                    return abs(
                        full_scale
                        * self._effective_euscale_fac(
                            channel.sensitivity, channel.per_eu_mode
                        )
                    )
        return None

    def _refresh_full_scale_axis_ranges_for_channels(
        self, channel_names: list[str], force_auto_y: bool = False
    ) -> None:
        changed: set[str] = set()
        for name in channel_names:
            changed.update(self._trace_aliases(name))
        if not changed:
            return
        for key in ("top", "bottom"):
            if self._display_mode(self._display_combo_for_key(key)) != "time":
                continue
            if self._manual_y_ranges.get(key) is not None and not force_auto_y:
                continue
            curves = self._last_plot_cache.get(key, {})
            if not curves:
                continue
            visible_channels: set[str] = set()
            focus_trace_name: str | None = None
            for name in curves:
                aliases = self._trace_aliases(name)
                visible_channels.update(aliases)
                if focus_trace_name is None and not aliases.isdisjoint(changed):
                    focus_trace_name = name
            if visible_channels.isdisjoint(changed):
                continue
            if force_auto_y:
                self._manual_y_ranges[key] = None
            self._channel_full_scale_focus[key] = focus_trace_name or self._active_trace_names.get(key)
            self._auto_y_follow_visible_x[key] = False
            self._apply_axis_scale(key, preserve_x=True, y_scope="channel_full_scale")

    def _legacy_log_floor(self, key: str, values: np.ndarray) -> float | None:
        positive = np.asarray(values, dtype=float)
        positive = positive[np.isfinite(positive) & (positive > 0.0)]
        if positive.size == 0:
            return None
        ymax = float(np.max(positive))
        if ymax <= 0.0:
            return None
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        if display_mode == "autospectrum":
            if value_mode in {"log_linear", "log_linear_per_sqrt_hz"}:
                return ymax * 1e-6
            if value_mode in {"log_power", "log_power_per_hz", "log_pk", "log_p2p"}:
                return ymax * 1e-12
        if display_mode in {"frf", "cross_spectrum"} and value_mode == "log_mag":
            return ymax * 1e-8
        return float(np.min(positive))

    @staticmethod
    def _range_for_plot_axis(min_value: float, max_value: float, log_enabled: bool) -> tuple[float, float]:
        if not log_enabled:
            return min_value, max_value
        return np.log10(max(min_value, 1e-300)), np.log10(max(max_value, 1e-300))

    @staticmethod
    def _range_from_plot_axis(min_value: float, max_value: float, log_enabled: bool) -> tuple[float, float]:
        if not log_enabled:
            return min_value, max_value
        return 10.0 ** min_value, 10.0 ** max_value

    def _x_to_plot_coord(self, key: str, value: float) -> float:
        if self._is_log_xscale(key) and not self._is_nyquist_display(key):
            return float(np.log10(max(value, 1e-300)))
        return value

    def _x_from_plot_coord(self, key: str, value: float) -> float:
        if self._is_log_xscale(key) and not self._is_nyquist_display(key):
            return float(10.0 ** value)
        return value

    def _y_to_plot_coord(self, key: str, value: float) -> float:
        if self._is_log_yscale(key) and not self._is_nyquist_display(key):
            return float(np.log10(max(value, 1e-300)))
        return value

    def _y_from_plot_coord(self, key: str, value: float) -> float:
        if self._is_log_yscale(key) and not self._is_nyquist_display(key):
            return float(10.0 ** value)
        return value

    def _apply_axis_scale(
        self,
        key: str,
        preserve_x: bool = False,
        y_scope: str = "legacy",
    ) -> None:
        plot = self._plot_widget_for_key(key)
        self._configure_plot_xscale(plot, key)
        self._axis_scaling_key = key
        manual_range = self._manual_x_ranges[key]
        has_manual_x = manual_range is not None
        try:
            has_legacy_x = False
            current_curves = self._last_plot_cache.get(key, {})
            if not preserve_x:
                if has_manual_x:
                    xmin, xmax = manual_range
                else:
                    legacy_x_extent = self._legacy_x_extent_for_display(key, current_curves)
                    if legacy_x_extent is not None:
                        has_legacy_x = True
                        xmin, xmax = legacy_x_extent
                    else:
                        extents: list[tuple[float, float]] = []
                        current_extent = self._curve_x_extent(current_curves)
                        if current_extent is not None:
                            extents.append(current_extent)
                        if self.overlay_checkbox.isChecked():
                            for overlay_curves in self._stored_overlays[key]:
                                overlay_extent = self._curve_x_extent(overlay_curves)
                                if overlay_extent is not None:
                                    extents.append(overlay_extent)
                        if not extents:
                            return
                        xmin = min(item[0] for item in extents)
                        xmax = max(item[1] for item in extents)
                if xmax <= xmin:
                    span = max(abs(xmin), 1.0) * 0.05
                    xmin -= span
                    xmax += span
                x_range = self._range_for_plot_axis(
                    xmin,
                    xmax,
                    self._is_log_xscale(key) and not self._is_nyquist_display(key),
                )
                plot.setXRange(x_range[0], x_range[1], padding=0.0 if has_manual_x or has_legacy_x else 0.02)
            manual_y_range = self._manual_y_ranges[key]
            has_manual_y = manual_y_range is not None
            if has_manual_y:
                ymin, ymax = manual_y_range
            else:
                y_extent = self._curve_y_extent(current_curves, key, scope=y_scope)
                if y_extent is None:
                    return
                ymin, ymax = y_extent
            if ymax <= ymin:
                span = max(abs(ymin), 1.0) * 0.05
                ymin -= span
                ymax += span
            y_range = self._range_for_plot_axis(
                ymin,
                ymax,
                self._is_log_yscale(key) and not self._is_nyquist_display(key),
            )
            view_box = plot.getPlotItem().vb
            view_box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            has_legacy_y = (
                not has_manual_y
                and y_scope != "visible"
                and self._legacy_y_extent_for_display(key, current_curves) is not None
            )
            plot.setYRange(y_range[0], y_range[1], padding=0.0 if has_manual_y or has_legacy_y else 0.05)
        finally:
            self._axis_scaling_key = None

    def _legacy_x_extent_for_display(
        self, key: str, curves: dict[str, tuple[np.ndarray, np.ndarray]]
    ) -> tuple[float, float] | None:
        if not self._is_nyquist_display(key):
            return None
        values: list[np.ndarray] = []
        for x_data, y_data in curves.values():
            x_arr = np.asarray(x_data, dtype=float)
            y_arr = np.asarray(y_data, dtype=float)
            combined = np.concatenate(
                [x_arr[np.isfinite(x_arr)], y_arr[np.isfinite(y_arr)]]
            )
            if combined.size:
                values.append(combined)
        if not values:
            return -1.0, 1.0
        max_abs = max(float(np.nanmax(np.abs(np.concatenate(values)))), 1e-12)
        return -1.25 * max_abs, 1.25 * max_abs

    def _auto_scale_plot(self, key: str) -> None:
        self._manual_x_ranges[key] = None
        self._apply_axis_scale(
            key,
            y_scope=self._axis_y_scope_for_plot(key),
        )
        self.statusBar().showMessage(f"Auto-scaled {key} X axis")

    def _auto_scale_plot_xy(self, key: str) -> None:
        self._manual_x_ranges[key] = None
        self._manual_y_ranges[key] = None
        self._channel_full_scale_focus[key] = None
        self._auto_y_follow_visible_x[key] = True
        self._apply_axis_scale(key, y_scope="visible")
        self.statusBar().showMessage(f"Auto-scaled {key} X/Y axes")

    def _auto_fit_y_to_visible_x(self, key: str) -> None:
        self._manual_y_ranges[key] = None
        self._channel_full_scale_focus[key] = None
        self._apply_axis_scale(key, preserve_x=True, y_scope="visible")
        self.statusBar().showMessage(f"Auto-fit {key} Y axis to visible X range")

    def _zoom_plot_to_view_rect(self, key: str, start_point, stop_point) -> bool:
        x0 = float(start_point.x())
        x1 = float(stop_point.x())
        y0 = float(start_point.y())
        y1 = float(stop_point.y())
        if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
            return False
        xmin = self._x_from_plot_coord(key, min(x0, x1))
        xmax = self._x_from_plot_coord(key, max(x0, x1))
        ymin = self._y_from_plot_coord(key, min(y0, y1))
        ymax = self._y_from_plot_coord(key, max(y0, y1))
        applied = self._set_manual_xy_values(
            key,
            xmin,
            xmax,
            ymin,
            ymax,
            auto_x=False,
            auto_y=False,
        )
        if applied:
            self.statusBar().showMessage(f"Zoomed {key} axis range")
        return applied

    def _set_manual_xy_values(
        self,
        key: str,
        xmin: float | None,
        xmax: float | None,
        ymin: float | None,
        ymax: float | None,
        auto_x: bool = False,
        auto_y: bool = False,
    ) -> bool:
        if auto_x:
            self._manual_x_ranges[key] = None
        else:
            if xmin is None or xmax is None:
                return False
            if self._is_log_xscale(key) and not self._is_nyquist_display(key) and (xmin <= 0.0 or xmax <= 0.0):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Range",
                    "Log X scale requires both limits to be positive.",
                )
                return False
            if xmax <= xmin:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Range",
                    "X max must be greater than X min.",
                )
                return False
            self._manual_x_ranges[key] = (float(xmin), float(xmax))

        if auto_y:
            self._manual_y_ranges[key] = None
            self._channel_full_scale_focus[key] = None
            self._auto_y_follow_visible_x[key] = True
        else:
            if ymin is None or ymax is None:
                return False
            if self._is_log_yscale(key) and not self._is_nyquist_display(key) and (ymin <= 0.0 or ymax <= 0.0):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Range",
                    "Log Y scale requires both limits to be positive.",
                )
                return False
            if ymax <= ymin:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Range",
                    "Y max must be greater than Y min.",
                )
                return False
            self._manual_y_ranges[key] = (float(ymin), float(ymax))
            self._auto_y_follow_visible_x[key] = False

        self._apply_axis_scale(
            key,
            y_scope=self._axis_y_scope_for_plot(key),
        )
        self.statusBar().showMessage(f"Updated {key} axis ranges")
        return True

    def _set_manual_xy_range(self, key: str) -> bool:
        plot = self._plot_widget_for_key(key)
        current_x, current_y = plot.viewRange()
        default_xmin, default_xmax = self._range_from_plot_axis(
            float(current_x[0]),
            float(current_x[1]),
            self._is_log_xscale(key) and not self._is_nyquist_display(key),
        )
        default_ymin, default_ymax = self._range_from_plot_axis(
            float(current_y[0]),
            float(current_y[1]),
            self._is_log_yscale(key) and not self._is_nyquist_display(key),
        )
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"{key.title()} Axis Range")
        dialog.setModal(True)
        dialog.setStyleSheet(self.styleSheet())
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 10)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        x_auto = QtWidgets.QCheckBox("Auto")
        y_auto = QtWidgets.QCheckBox("Auto")
        x_auto.setChecked(self._manual_x_ranges.get(key) is None)
        y_auto.setChecked(self._manual_y_ranges.get(key) is None)
        x_min_edit = QtWidgets.QLineEdit(f"{default_xmin:.6g}")
        x_max_edit = QtWidgets.QLineEdit(f"{default_xmax:.6g}")
        y_min_edit = QtWidgets.QLineEdit(f"{default_ymin:.6g}")
        y_max_edit = QtWidgets.QLineEdit(f"{default_ymax:.6g}")
        for edit in (x_min_edit, x_max_edit, y_min_edit, y_max_edit):
            edit.setMinimumWidth(96)

        def _range_row(auto_box: QtWidgets.QCheckBox, min_edit: QtWidgets.QLineEdit, max_edit: QtWidgets.QLineEdit) -> QtWidgets.QWidget:
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(auto_box)
            row_layout.addWidget(QtWidgets.QLabel("min"))
            row_layout.addWidget(min_edit)
            row_layout.addWidget(QtWidgets.QLabel("max"))
            row_layout.addWidget(max_edit)
            return row

        def _sync_enabled() -> None:
            for edit in (x_min_edit, x_max_edit):
                edit.setEnabled(not x_auto.isChecked())
            for edit in (y_min_edit, y_max_edit):
                edit.setEnabled(not y_auto.isChecked())

        x_auto.toggled.connect(_sync_enabled)
        y_auto.toggled.connect(_sync_enabled)
        _sync_enabled()
        form.addRow("X Range", _range_row(x_auto, x_min_edit, x_max_edit))
        form.addRow("Y Range", _range_row(y_auto, y_min_edit, y_max_edit))
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Apply).setText("Apply")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancel")
        layout.addWidget(buttons)

        def _apply_and_close() -> None:
            try:
                xmin = None if x_auto.isChecked() else float(x_min_edit.text())
                xmax = None if x_auto.isChecked() else float(x_max_edit.text())
                ymin = None if y_auto.isChecked() else float(y_min_edit.text())
                ymax = None if y_auto.isChecked() else float(y_max_edit.text())
            except ValueError:
                QtWidgets.QMessageBox.warning(dialog, "Invalid Range", "Please enter numeric axis limits.")
                return
            if self._set_manual_xy_values(
                key,
                xmin,
                xmax,
                ymin,
                ymax,
                auto_x=x_auto.isChecked(),
                auto_y=y_auto.isChecked(),
            ):
                dialog.accept()

        buttons.clicked.connect(
            lambda button: (
                _apply_and_close()
                if buttons.standardButton(button) == QtWidgets.QDialogButtonBox.Apply
                else dialog.reject()
            )
        )
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _set_manual_x_range(self, key: str) -> None:
        plot = self._plot_widget_for_key(key)
        current_range = plot.viewRange()[0]
        default_min, default_max = self._range_from_plot_axis(
            float(current_range[0]),
            float(current_range[1]),
            self._is_log_xscale(key) and not self._is_nyquist_display(key),
        )
        xmin, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"{key.title()} X Range",
            "X min",
            default_min,
            decimals=6,
        )
        if not ok:
            return
        xmax, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"{key.title()} X Range",
            "X max",
            default_max,
            decimals=6,
        )
        if not ok:
            return
        if self._is_log_xscale(key) and (xmin <= 0.0 or xmax <= 0.0):
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Range",
                "Log X scale requires both limits to be positive.",
            )
            return
        if xmax <= xmin:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Range",
                "X max must be greater than X min.",
            )
            return
        self._manual_x_ranges[key] = (xmin, xmax)
        self._apply_axis_scale(key)
        self.statusBar().showMessage(f"Set {key} X range to {xmin:.4g} .. {xmax:.4g}")

    def _set_manual_y_range(self, key: str) -> None:
        plot = self._plot_widget_for_key(key)
        current_range = plot.viewRange()[1]
        default_min, default_max = self._range_from_plot_axis(
            float(current_range[0]),
            float(current_range[1]),
            self._is_log_yscale(key) and not self._is_nyquist_display(key),
        )
        ymin, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"{key.title()} Y Range",
            "Y min",
            default_min,
            decimals=6,
        )
        if not ok:
            return
        ymax, ok = QtWidgets.QInputDialog.getDouble(
            self,
            f"{key.title()} Y Range",
            "Y max",
            default_max,
            decimals=6,
        )
        if not ok:
            return
        if self._is_log_yscale(key) and (ymin <= 0.0 or ymax <= 0.0):
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Range",
                "Log Y scale requires both limits to be positive.",
            )
            return
        if ymax <= ymin:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Range",
                "Y max must be greater than Y min.",
            )
            return
        self._auto_y_follow_visible_x[key] = False
        self._channel_full_scale_focus[key] = None
        self._manual_y_ranges[key] = (ymin, ymax)
        self._apply_axis_scale(key, preserve_x=True)
        self.statusBar().showMessage(f"Set {key} Y range to {ymin:.4g} .. {ymax:.4g}")

    def _refresh_markers(self, key: str) -> None:
        visible = self._markers_enabled
        trace_name, curve = self._selected_curve(key)
        for index, line in enumerate(self._marker_lines[key]):
            position = self._marker_positions[key][index]
            line.setVisible(visible and position is not None)
            if visible and position is not None:
                line.setValue(self._x_to_plot_coord(key, position))
            point = self._marker_points[key][index]
            text = self._marker_texts[key][index]
            if not visible or position is None or curve is None:
                point.setData([], [])
                point.setVisible(False)
                text.setVisible(False)
                continue
            x_data, y_data = curve
            marker_y = self._nearest_y_for_curve(x_data, y_data, position)
            if marker_y is None:
                point.setData([], [])
                point.setVisible(False)
                text.setVisible(False)
                continue
            point.setData([self._x_to_plot_coord(key, position)], [self._y_to_plot_coord(key, marker_y)])
            point.setVisible(True)
            text.setText(
                f"{'A' if index == 0 else 'B'}\nX={position:.6g}\nY={marker_y:.6g}"
            )
            text.setPos(self._x_to_plot_coord(key, position), self._y_to_plot_coord(key, marker_y))
            text.setVisible(True)

    @staticmethod
    def _nearest_y_for_curve(
        x_data: np.ndarray, y_data: np.ndarray, marker_x: float
    ) -> float | None:
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            return None
        index = int(np.clip(np.searchsorted(x_arr, marker_x), 0, x_arr.size - 1))
        if index > 0 and abs(x_arr[index - 1] - marker_x) <= abs(x_arr[index] - marker_x):
            index -= 1
        return float(y_arr[index])

    def _nearest_curve_point(self, key: str, marker_x: float) -> tuple[float | None, float | None]:
        _trace_name, curve = self._selected_curve(key)
        if curve is None:
            return None, None
        x_data, y_data = curve
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            return None, None
        index = int(np.clip(np.searchsorted(x_arr, marker_x), 0, x_arr.size - 1))
        if index > 0 and abs(x_arr[index - 1] - marker_x) <= abs(x_arr[index] - marker_x):
            index -= 1
        return float(x_arr[index]), float(y_arr[index])

    def _nearest_curve_point_by_x_for_trace(
        self, key: str, trace_name: str | None, marker_x: float
    ) -> tuple[float | None, float | None, str | None]:
        curves = self._last_plot_cache.get(key, {})
        if not curves:
            return None, None, None
        resolved_trace = trace_name if trace_name in curves else None
        if resolved_trace is None:
            resolved_trace = next(iter(curves))
        x_data, y_data = curves[resolved_trace]
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            return None, None, resolved_trace
        point_count = min(x_arr.size, y_arr.size)
        x_arr = x_arr[:point_count]
        y_arr = y_arr[:point_count]
        finite = np.isfinite(x_arr) & np.isfinite(y_arr)
        if not np.any(finite):
            return None, None, resolved_trace
        x_arr = x_arr[finite]
        y_arr = y_arr[finite]
        index = int(np.clip(np.searchsorted(x_arr, marker_x), 0, x_arr.size - 1))
        if index > 0 and abs(x_arr[index - 1] - marker_x) <= abs(x_arr[index] - marker_x):
            index -= 1
        return float(x_arr[index]), float(y_arr[index]), resolved_trace

    def _refresh_cursor_for_current_curve(self, key: str) -> None:
        if not self._cursor_enabled:
            return
        cursor = self._cursor_positions.get(key)
        if cursor is None:
            return
        cursor_x, _cursor_y = cursor
        trace_name = self._active_trace_names.get(key)
        next_x, next_y, resolved_trace = self._nearest_curve_point_by_x_for_trace(
            key, trace_name, cursor_x
        )
        if next_x is None or next_y is None:
            return
        self._set_cursor_position(key, next_x, next_y, resolved_trace, announce=False)

    def _nearest_curve_point_2d(
        self, key: str, click_x: float, click_y: float
    ) -> tuple[float | None, float | None]:
        _trace_name, curve = self._selected_curve(key)
        if curve is None:
            return None, None
        x_data, y_data = curve
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            return None, None
        point_count = min(x_arr.size, y_arr.size)
        x_arr = x_arr[:point_count]
        y_arr = y_arr[:point_count]
        finite = np.isfinite(x_arr) & np.isfinite(y_arr)
        if not np.any(finite):
            return None, None
        x_arr = x_arr[finite]
        y_arr = y_arr[finite]
        plot = self._plot_widget_for_key(key)
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._x_to_plot_coord(key, click_x)
        click_plot_y = self._y_to_plot_coord(key, click_y)
        plot_x = np.asarray([self._x_to_plot_coord(key, float(value)) for value in x_arr])
        plot_y = np.asarray([self._y_to_plot_coord(key, float(value)) for value in y_arr])
        scores = ((plot_x - click_plot_x) / x_span) ** 2 + ((plot_y - click_plot_y) / y_span) ** 2
        index = int(np.nanargmin(scores))
        return float(x_arr[index]), float(y_arr[index])

    def _nearest_trace_name(self, key: str, click_x: float, click_y: float) -> str | None:
        curves = self._last_plot_cache.get(key, {})
        if not curves:
            return None
        best_name: str | None = None
        best_score: float | None = None
        plot = self._plot_widget_for_key(key)
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._x_to_plot_coord(key, click_x)
        click_plot_y = self._y_to_plot_coord(key, click_y)
        for name, (x_data, y_data) in curves.items():
            x_arr = np.asarray(x_data, dtype=float)
            y_arr = np.asarray(y_data, dtype=float)
            if x_arr.size == 0 or y_arr.size == 0:
                continue
            index = int(np.clip(np.searchsorted(x_arr, click_x), 0, x_arr.size - 1))
            if index > 0 and abs(x_arr[index - 1] - click_x) <= abs(x_arr[index] - click_x):
                index -= 1
            dx = (self._x_to_plot_coord(key, float(x_arr[index])) - click_plot_x) / x_span
            dy = (self._y_to_plot_coord(key, float(y_arr[index])) - click_plot_y) / y_span
            score = dx * dx + dy * dy
            if best_score is None or score < best_score:
                best_score = score
                best_name = name
        return best_name

    def _update_marker_readout(self, key: str) -> None:
        label = self.top_marker_label if key == "top" else self.bottom_marker_label
        fields = self.top_marker_fields if key == "top" else self.bottom_marker_fields
        if not self._markers_enabled:
            label.setText(f"{key.title()} Marker: off")
            self._set_marker_fields(fields, "off")
            return
        trace_name, curve = self._selected_curve(key)
        if curve is None or trace_name is None:
            label.setText(f"{key.title()} Marker: --")
            self._set_marker_fields(fields, None)
            return
        x_data, y_data = curve
        positions = self._marker_positions[key]
        parts: list[str] = []
        values: list[tuple[float, float]] = []
        for name, marker_x in zip(("A", "B"), positions):
            if marker_x is None:
                parts.append(f"{name}=--")
                continue
            marker_y = self._nearest_y_for_curve(x_data, y_data, marker_x)
            if marker_y is None:
                parts.append(f"{name}=--")
                continue
            values.append((float(marker_x), float(marker_y)))
            parts.append(f"{name}({marker_x:.4g}, {marker_y:.4g})")
        if len(values) == 2:
            dx = values[1][0] - values[0][0]
            dy = values[1][1] - values[0][1]
            parts.append(f"dX={dx:.4g}")
            parts.append(f"dY={dy:.4g}")
        label.setText(f"{key.title()} Marker [{trace_name}]: " + " | ".join(parts))
        self._set_marker_fields(fields, trace_name, values)

    @staticmethod
    def _format_marker_value(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.6g}"

    def _set_marker_fields(
        self,
        fields: dict[str, QtWidgets.QLabel],
        trace_name: str | None,
        values: list[tuple[float, float]] | None = None,
    ) -> None:
        if trace_name == "off":
            fields["trace"].setText("off")
            for key in ("x1", "y1", "x2", "y2", "dx", "dy"):
                fields[key].setText("--")
            return
        if trace_name is None:
            for key in ("trace", "x1", "y1", "x2", "y2", "dx", "dy"):
                fields[key].setText("--")
            return
        fields["trace"].setText(trace_name)
        marker_a = values[0] if values and len(values) >= 1 else (None, None)
        marker_b = values[1] if values and len(values) >= 2 else (None, None)
        fields["x1"].setText(self._format_marker_value(marker_a[0]))
        fields["y1"].setText(self._format_marker_value(marker_a[1]))
        fields["x2"].setText(self._format_marker_value(marker_b[0]))
        fields["y2"].setText(self._format_marker_value(marker_b[1]))
        if values and len(values) >= 2:
            fields["dx"].setText(self._format_marker_value(values[1][0] - values[0][0]))
            fields["dy"].setText(self._format_marker_value(values[1][1] - values[0][1]))
        else:
            fields["dx"].setText("--")
            fields["dy"].setText("--")

    def _find_trace_extremum(self, key: str, mode: str) -> None:
        trace_name, curve = self._selected_curve(key)
        if curve is None or trace_name is None:
            self.statusBar().showMessage(f"No {key} trace is available for {mode} search")
            return
        x_data, y_data = curve
        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)
        if x_arr.size == 0 or y_arr.size == 0:
            self.statusBar().showMessage(f"No {key} samples are available for {mode} search")
            return
        plot = self._plot_widget_for_key(key)
        xmin, xmax = plot.viewRange()[0]
        xmin = self._x_from_plot_coord(key, float(xmin))
        xmax = self._x_from_plot_coord(key, float(xmax))
        visible = (x_arr >= xmin) & (x_arr <= xmax)
        if np.any(visible):
            search_x = x_arr[visible]
            search_y = y_arr[visible]
        else:
            search_x = x_arr
            search_y = y_arr
        index = int(np.argmax(search_y) if mode == "peak" else np.argmin(search_y))
        cursor_x = float(search_x[index])
        cursor_y = float(search_y[index])
        self._set_cursor_position(key, cursor_x, cursor_y, trace_name, announce=False)
        self.statusBar().showMessage(
            f"{key.title()} {mode} cursor on {trace_name}: x={cursor_x:.4g}, y={cursor_y:.4g}"
        )

    def _capture_overlay(self, key: str) -> None:
        trace_name, curve = self._selected_curve(key)
        if trace_name is None or curve is None:
            self.statusBar().showMessage(f"No active {key} plot curve available to capture")
            return
        x_data, y_data = curve
        snapshot = {trace_name: (x_data.copy(), y_data.copy())}
        self._stored_overlays[key].appendleft(snapshot)
        if not self.overlay_checkbox.isChecked():
            self.overlay_checkbox.setChecked(True)
        self._refresh_current_measurement_view()
        self.statusBar().showMessage(
            f"Captured {key} overlay for {self._legend_display_name(trace_name, self._display_mode(self._display_combo_for_key(key)))} "
            f"({len(self._stored_overlays[key])} stored)"
        )

    def _capture_top_overlay(self) -> None:
        self._capture_overlay("top")

    def _capture_bottom_overlay(self) -> None:
        self._capture_overlay("bottom")

    def _clear_overlays(self) -> None:
        self._stored_overlays["top"].clear()
        self._stored_overlays["bottom"].clear()
        self._refresh_current_measurement_view()
        self.statusBar().showMessage("Cleared stored overlays")

    def _current_plot_window_panel(self, key: str) -> dict[str, object]:
        display_mode = self._display_mode(self._display_combo_for_key(key))
        value_mode = self._value_mode(self._value_combo_for_key(key))
        x_label, y_label = self._axis_label_for(display_mode, value_mode)
        curves = {
            trace_name: (np.asarray(x_data).copy(), np.asarray(y_data).copy())
            for trace_name, (x_data, y_data) in self._filter_visible_curves(
                key, self._last_plot_cache.get(key, {})
            ).items()
        }
        legend_names = {
            trace_name: self._legend_display_name(trace_name, display_mode)
            for trace_name in curves
        }
        return {
            "title": f"{key.title()} - {self._display_label_for_mode(display_mode)} / {self._value_label_for_mode(display_mode, value_mode)}",
            "curves": curves,
            "legend_names": legend_names,
            "colors": dict(self._plot_curve_colors.get(key, {})),
            "x_label": x_label,
            "y_label": y_label,
            "log_x": self._is_log_xscale(key) and not self._is_nyquist_display(key),
            "log_y": self._is_log_yscale(key) and not self._is_nyquist_display(key),
        }

    def _open_current_plot_window(self) -> None:
        panels = {
            "top": self._current_plot_window_panel("top"),
            "bottom": self._current_plot_window_panel("bottom"),
        }
        has_curves = any(panel.get("curves") for panel in panels.values())
        if not has_curves:
            QtWidgets.QMessageBox.information(
                self, "Current Plot Window", "No plotted curves are available yet."
            )
            return
        self._detached_plot_window.set_plot_data(panels)
        self._detached_plot_window.show()

    def _open_bode_plot(self) -> None:
        self._open_current_plot_window()

    def _update_plot_layout(self, layout_mode: str) -> None:
        single_mode = layout_mode == "single"
        self.bottom_plot.setVisible(not single_mode)

    def _export_data(self) -> None:
        snapshot = self.controller.snapshot()
        if snapshot.measurement is None:
            QtWidgets.QMessageBox.warning(self, "No Data", "Acquire at least one frame first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Session",
            str(Path.cwd() / "session"),
            "All Supported (*.json *.npz *.csv *.h5);;JSON Session (*.json);;NPZ Data (*.npz);;CSV Time Data (*.csv);;HDF5 Data (*.h5)",
        )
        if not path:
            return

        export_path = Path(path)
        suffix = export_path.suffix.lower()
        if suffix == ".npz":
            save_measurement_npz(snapshot, export_path)
        elif suffix == ".csv":
            save_measurement_csv(snapshot, export_path)
        elif suffix in {".h5", ".hdf5"}:
            save_measurement_hdf5(snapshot, export_path)
        elif suffix == ".json":
            save_session_json(snapshot, export_path)
        else:
            json_path = export_path.with_suffix(".json")
            save_session_json(snapshot, json_path)
            save_measurement_npz(snapshot, export_path.with_suffix(".npz"))
            save_measurement_csv(snapshot, export_path.with_suffix(".csv"))
            try:
                save_measurement_hdf5(snapshot, export_path.with_suffix(".h5"))
            except RuntimeError:
                pass
            export_path = json_path
        self.statusBar().showMessage(f"Exported to {export_path}")

    def _save_session(self) -> None:
        self._read_session_from_widgets()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save VNA",
            str((self._current_source_path or Path.cwd() / "session.vna").with_suffix(".vna")),
            "VNA Files (*.vna);;JSON Session (*.json)",
        )
        if not path:
            return
        save_path = Path(path)
        snapshot = self._snapshot_with_current_display_state()
        if save_path.suffix.lower() == ".json":
            save_session_json(snapshot, save_path)
        else:
            save_legacy_vna(snapshot, save_path)
        self.statusBar().showMessage(f"Saved session to {save_path}")

    def _default_vna_path(self) -> Path:
        return resource_path("dsa/vna/default.vna")

    def _save_to_default_vna(self) -> Path:
        self._read_session_from_widgets()
        save_path = self._default_vna_path()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_legacy_vna(self._snapshot_with_current_display_state(), save_path)
        self._current_source_path = save_path
        self._update_window_title()
        self.statusBar().showMessage(f"Saved default VNA to {save_path}")
        return save_path

    def _load_session(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Session",
            str(Path.cwd()),
            "VNA Files (*.vna);;JSON Session (*.json)",
        )
        if not path:
            return
        selected_path = Path(path)
        loaded = (
            load_legacy_vna(selected_path)
            if selected_path.suffix.lower() in {".vna", ".mat"}
            else load_saved_session_json(selected_path)
        )
        self._reset_plot_display_state()
        self.controller.set_session(loaded.config)
        self.session = loaded.config
        self.controller.state.measurement = loaded.measurement
        self._current_source_path = selected_path
        self._update_window_title()
        self._load_session_to_widgets()
        self._apply_display_defaults_for_measurement(loaded.measurement)
        if loaded.measurement is not None:
            self._plot_measurement(loaded.measurement)
        self.statusBar().showMessage(f"Loaded session from {path}")

    def _import_legacy_vna(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Legacy VNA",
            str(self._last_vna_directory),
            "VNA Files (*.vna);;MAT Files (*.mat)",
        )
        if not path:
            return
        selected_path = Path(path)
        self._last_vna_directory = selected_path.parent
        imported = load_legacy_vna(selected_path)
        self._reset_plot_display_state()
        self.controller.set_session(imported.config)
        self.controller.state.measurement = imported.measurement
        self.session = imported.config
        self._current_source_path = selected_path
        self._update_window_title()
        self._load_session_to_widgets()
        self._apply_display_defaults_for_measurement(imported.measurement)
        if imported.measurement is not None:
            self._plot_measurement(imported.measurement)
        self.statusBar().showMessage(f"Opened legacy VNA from {path}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        try:
            if self._recording_worker is not None:
                if self._recording_stop_event is not None:
                    self._recording_stop_event.set()
            if self._recording_thread is not None:
                self._recording_thread.quit()
                if not self._recording_thread.wait(1500):
                    event.ignore()
                    self.statusBar().showMessage("Waiting for continuous recording to stop")
                    return
            if self._acquisition_worker is not None:
                if self._acquisition_stop_event is not None:
                    self._acquisition_stop_event.set()
            if self._acquisition_thread is not None:
                self._acquisition_thread.quit()
                if not self._acquisition_thread.wait(1500):
                    event.ignore()
                    self.statusBar().showMessage("Waiting for acquisition to stop")
                    return
            else:
                self.controller.stop()
            self.controller.close()
        except Exception:
            pass
        if hasattr(self, "_detached_plot_window"):
            self._detached_plot_window.close()
        super().closeEvent(event)


def _cursor_palette_for_background(background: str) -> dict[str, object]:
    text = str(background or "").strip()
    rgb = (255, 255, 255)
    if text.startswith("#") and len(text) in {4, 7}:
        if len(text) == 4:
            rgb = tuple(int(char * 2, 16) for char in text[1:4])
        else:
            rgb = (
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
            )
    luminance = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0
    if luminance > 0.55:
        return {
            "line": "#0f4c81",
            "text": "#071827",
            "fill": (255, 226, 89, 235),
            "border": "#071827",
        }
    return {
        "line": "#fff176",
        "text": "#111111",
        "fill": (255, 245, 157, 230),
        "border": "#f6f1df",
    }


def _apply_text_item_style(text_item, *, color, fill, border) -> None:
    text_item.setColor(color)
    text_item.fill = pg.mkBrush(fill)
    text_item.border = pg.mkPen(border, width=0.9)
    text_item.update()
