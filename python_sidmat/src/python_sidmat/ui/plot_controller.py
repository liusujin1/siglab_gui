"""Samba Records/Plot-style interaction controller for SiDiMaT graphs."""

from __future__ import annotations

from collections import deque
import csv
from pathlib import Path
import time
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.plot_interactions import (
    CURVE_COLORS,
    DataTipPoint,
    DataTipText,
    plot_font,
    short_number,
)

__all__ = ["SidmatPlotInteractionController"]


class SidmatPlotInteractionController(QtCore.QObject):
    """Add Records/Plot navigation, pointers and annotations to four plots.

    The measurement window owns the plot widgets and their data.  This object
    deliberately owns only transient interaction state, so measurement, file
    and figure persistence behaviour remains unchanged.
    """

    def __init__(
        self,
        host: QtWidgets.QMainWindow,
        views: list[QtWidgets.QWidget],
        *,
        cursor_button: QtWidgets.QAbstractButton,
        data_tip_button: QtWidgets.QAbstractButton,
        marker_readout: QtWidgets.QLabel,
    ) -> None:
        super().__init__(host)
        self.host = host
        self._views = {
            str(getattr(view, "_plot_key")): view for view in views
        }
        self._active_key = next(iter(self._views), "time")
        self.cursor_button = cursor_button
        self.data_tip_button = data_tip_button
        self.marker_readout = marker_readout
        self._zoom_history = {
            key: deque(maxlen=5) for key in self._views
        }
        self._last_saved_range: dict[
            str, tuple[tuple[float, float], tuple[float, float]] | None
        ] = {key: None for key in self._views}
        self._selected_items: dict[str, Any] = {}
        self._cursor_state: dict[str, dict[str, Any]] = {}
        self._data_tips: dict[int, dict[str, Any]] = {}
        self._tip_counter = 0
        self._tip_menu_suppressed_until = 0.0
        self._markers: dict[str, dict[str, dict[str, Any]]] = {
            key: {} for key in self._views
        }
        self._moving_marker = False
        self.set_pointer_tool("cursor", True)
        self._update_marker_readout(self.active_view())

    # ------------------------------------------------------------------
    # Plot and curve selection
    # ------------------------------------------------------------------

    @staticmethod
    def _key(view: QtWidgets.QWidget) -> str:
        return str(getattr(view, "_plot_key"))

    @staticmethod
    def _view_box(view: QtWidgets.QWidget):
        return getattr(view, "_view_box", view._pw.getViewBox())

    def active_view(self) -> QtWidgets.QWidget:
        return self._views[self._active_key]

    def activate(self, view: QtWidgets.QWidget) -> None:
        key = self._key(view)
        if key not in self._views:
            return
        self._active_key = key
        view._pw.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        self._update_marker_readout(view)

    def activate_key(self, key: str) -> None:
        view = self._views.get(key)
        if view is not None:
            self.activate(view)

    @staticmethod
    def _curve_name(item: Any) -> str:
        try:
            name = item.name()
        except (AttributeError, RuntimeError):
            name = None
        if not name:
            name = (getattr(item, "opts", {}) or {}).get("name")
        return str(name or "Curve")

    def bind_curves(self, view: QtWidgets.QWidget) -> None:
        """Enable curve picking after a redraw without replacing data items."""

        key = self._key(view)
        current_items = list(view._pw.listDataItems())
        if self._selected_items.get(key) not in current_items:
            self._selected_items.pop(key, None)
        for index, item in enumerate(current_items):
            if not hasattr(item, "_sidmat_base_pen"):
                pen = (getattr(item, "opts", {}) or {}).get("pen")
                item._sidmat_base_pen = pg.mkPen(
                    pen if pen is not None else CURVE_COLORS[index % len(CURVE_COLORS)]
                )
            if getattr(item, "_sidmat_interaction_bound", False):
                continue
            item._sidmat_interaction_bound = True
            item.setCurveClickable(True, width=10)
            item.sigClicked.connect(
                lambda selected, event, selected_view=view: self._curve_clicked(
                    selected_view, selected, event
                )
            )
        legend = getattr(view, "_legend", None)
        if legend is not None:
            for _sample, label in legend.items:
                target = getattr(label, "item", label)
                if hasattr(target, "setFont"):
                    target.setFont(plot_font())
        self._sync_legend_visibility(view)
        self._update_curve_pens(view)

    def _curve_clicked(self, view, item, _event) -> None:
        self.activate(view)
        self._selected_items[self._key(view)] = item
        self._update_curve_pens(view)
        self._set_status(f"Selected curve: {self._curve_name(item)}")

    def _update_curve_pens(self, view: QtWidgets.QWidget) -> None:
        selected = self._selected_items.get(self._key(view))
        for item in view._pw.listDataItems():
            base = pg.mkPen(getattr(item, "_sidmat_base_pen", item.opts.get("pen")))
            if item is selected:
                base.setWidthF(max(2.4, base.widthF() + 0.9))
                item.setZValue(3)
            else:
                item.setZValue(0)
            item.setPen(base)

    @staticmethod
    def _sync_legend_visibility(view: QtWidgets.QWidget) -> None:
        legend = getattr(view, "_legend", None)
        if legend is None:
            return
        for sample, label in legend.items:
            curve = getattr(sample, "item", None)
            visible = curve is None or curve.isVisible()
            sample.setVisible(visible)
            label.setVisible(visible)

    def hide_selected_curve(self, view: QtWidgets.QWidget | None = None) -> bool:
        view = view or self.active_view()
        item = self._selected_items.pop(self._key(view), None)
        if item is None or item not in view._pw.listDataItems():
            self._set_status("Click a curve before hiding it")
            return False
        item.setVisible(False)
        self._sync_legend_visibility(view)
        self._update_curve_pens(view)
        self._set_status(f"Hidden curve: {self._curve_name(item)}")
        return True

    def show_all_curves(self, view: QtWidgets.QWidget | None = None) -> None:
        view = view or self.active_view()
        for item in view._pw.listDataItems():
            item.setVisible(True)
        self._sync_legend_visibility(view)
        self._set_status("All plot curves are visible")

    # ------------------------------------------------------------------
    # Refresh lifecycle
    # ------------------------------------------------------------------

    def prepare_refresh(self, view: QtWidgets.QWidget) -> None:
        self.clear_annotations(view=view)
        key = self._key(view)
        self._selected_items.pop(key, None)
        self._zoom_history[key].clear()
        self._last_saved_range[key] = None

    def finish_refresh(
        self, view: QtWidgets.QWidget, *, auto_fit: bool = False
    ) -> None:
        self.bind_curves(view)
        if auto_fit and view._pw.listDataItems():
            self.auto_range(view=view, remember=False)

    # ------------------------------------------------------------------
    # Data/display coordinates and nearest-point lookup
    # ------------------------------------------------------------------

    @staticmethod
    def _log_modes(view: QtWidgets.QWidget) -> tuple[bool, bool]:
        ctrl = getattr(view._pw.getPlotItem(), "ctrl", None)
        if ctrl is None:
            return False, False
        return bool(ctrl.logXCheck.isChecked()), bool(ctrl.logYCheck.isChecked())

    def _curve_arrays(self, view, item):
        raw_x = getattr(item, "xData", None)
        raw_y = getattr(item, "yData", None)
        if raw_x is None or raw_y is None:
            try:
                raw_x, raw_y = item.getOriginalDataset()
            except (AttributeError, TypeError, ValueError):
                return None
        raw_x = np.asarray(raw_x, dtype=float)
        raw_y = np.asarray(raw_y, dtype=float)
        length = min(raw_x.size, raw_y.size)
        if length == 0:
            return None
        raw_x = raw_x[:length]
        raw_y = raw_y[:length]
        finite = np.isfinite(raw_x) & np.isfinite(raw_y)
        display_x = raw_x.copy()
        display_y = raw_y.copy()
        log_x, log_y = self._log_modes(view)
        if log_x:
            finite &= raw_x > 0.0
            with np.errstate(divide="ignore", invalid="ignore"):
                display_x = np.log10(raw_x)
        if log_y:
            finite &= raw_y > 0.0
            with np.errstate(divide="ignore", invalid="ignore"):
                display_y = np.log10(raw_y)
        indices = np.flatnonzero(
            finite & np.isfinite(display_x) & np.isfinite(display_y)
        )
        return raw_x, raw_y, display_x, display_y, indices

    def _candidate_items(
        self, view: QtWidgets.QWidget, only_item: Any | None = None
    ) -> list[Any]:
        visible = [item for item in view._pw.listDataItems() if item.isVisible()]
        if only_item is not None:
            return [only_item] if only_item in visible else []
        selected = self._selected_items.get(self._key(view))
        if selected in visible:
            return [selected]
        return visible

    def nearest_point(
        self,
        view: QtWidgets.QWidget,
        scene_position,
        *,
        only_item: Any | None = None,
    ) -> dict[str, Any] | None:
        view_box = self._view_box(view)
        if not view_box.sceneBoundingRect().contains(scene_position):
            return None
        point = view_box.mapSceneToView(scene_position)
        x_range, y_range = view_box.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-12)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-12)
        best: dict[str, Any] | None = None
        best_distance = float("inf")
        for item in self._candidate_items(view, only_item):
            arrays = self._curve_arrays(view, item)
            if arrays is None:
                continue
            raw_x, raw_y, display_x, display_y, indices = arrays
            if not len(indices):
                continue
            candidate_x = display_x[indices]
            if len(candidate_x) > 1 and np.all(np.diff(candidate_x) >= 0.0):
                insertion = int(np.searchsorted(candidate_x, point.x()))
                local_positions = {
                    max(0, min(insertion, len(indices) - 1)),
                    max(0, min(insertion - 1, len(indices) - 1)),
                }
                candidates = [indices[position] for position in local_positions]
            else:
                candidates = [
                    indices[int(np.argmin(np.abs(candidate_x - point.x())))]
                ]
            for index in candidates:
                plot_x = float(display_x[index])
                plot_y = float(display_y[index])
                distance = ((plot_x - point.x()) / x_span) ** 2 + (
                    (plot_y - point.y()) / y_span
                ) ** 2
                if distance < best_distance:
                    best_distance = distance
                    best = {
                        "item": item,
                        "index": int(index),
                        "name": self._curve_name(item),
                        "x": float(raw_x[index]),
                        "y": float(raw_y[index]),
                        "plot_x": plot_x,
                        "plot_y": plot_y,
                    }
        return best

    def _nearest_for_view_x(self, view, item, view_x: float):
        arrays = self._curve_arrays(view, item)
        if arrays is None:
            return None
        raw_x, raw_y, display_x, display_y, indices = arrays
        if not len(indices):
            return None
        candidates = display_x[indices]
        if len(candidates) > 1 and np.all(np.diff(candidates) >= 0.0):
            insertion = int(np.searchsorted(candidates, view_x))
            choices = [
                max(0, min(insertion, len(indices) - 1)),
                max(0, min(insertion - 1, len(indices) - 1)),
            ]
            local = min(
                choices, key=lambda position: abs(float(candidates[position]) - view_x)
            )
            index = int(indices[local])
        else:
            index = int(indices[int(np.argmin(np.abs(candidates - view_x)))])
        return {
            "item": item,
            "index": index,
            "name": self._curve_name(item),
            "x": float(raw_x[index]),
            "y": float(raw_y[index]),
            "plot_x": float(display_x[index]),
            "plot_y": float(display_y[index]),
        }

    # ------------------------------------------------------------------
    # Pointer tools
    # ------------------------------------------------------------------

    def set_pointer_tool(self, tool: str, checked: bool = True) -> None:
        if tool == "cursor":
            if checked:
                self.cursor_button.setChecked(True)
                self.data_tip_button.setChecked(False)
            elif not self.data_tip_button.isChecked():
                self.cursor_button.setChecked(True)
        else:
            if checked:
                self.data_tip_button.setChecked(True)
                self.cursor_button.setChecked(False)
            elif not self.cursor_button.isChecked():
                self.cursor_button.setChecked(True)

    def pointer_tool(self) -> str:
        return "data-tip" if self.data_tip_button.isChecked() else "cursor"

    def scene_clicked(self, view: QtWidgets.QWidget, event) -> None:
        self.activate(view)
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            if time.monotonic() >= self._tip_menu_suppressed_until:
                self._show_plot_menu(view, event.screenPos())
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.handle_pointer(view, event.scenePos(), dragging=False)

    def handle_pointer(
        self, view: QtWidgets.QWidget, scene_position, *, dragging: bool
    ) -> None:
        self.activate(view)
        nearest = self.nearest_point(view, scene_position)
        if nearest is None:
            return
        if self.pointer_tool() == "data-tip":
            if not dragging:
                self._add_data_tip(view, nearest)
        else:
            self._update_cursor(view, nearest)

    @staticmethod
    def _format_point_label(prefix: str, nearest: dict[str, Any]) -> str:
        return (
            f"{prefix}{nearest['name']}\n"
            f"X {short_number(nearest['x'])}\n"
            f"Y {short_number(nearest['y'])}"
        )

    def _update_cursor(self, view, nearest) -> None:
        key = self._key(view)
        view_box = self._view_box(view)
        state = self._cursor_state.get(key)
        if state is None:
            line = pg.InfiniteLine(
                angle=90, movable=False, pen=pg.mkPen("#0f4c81", width=1.2)
            )
            point = pg.ScatterPlotItem(
                size=9,
                pen=pg.mkPen("#071827", width=1.0),
                brush=pg.mkBrush("#fff176"),
            )
            label = pg.TextItem(
                color="#071827",
                fill=pg.mkBrush(255, 245, 157, 225),
                border=pg.mkPen("#071827", width=0.8),
                anchor=(0.0, 1.0),
            )
            label.setFont(plot_font())
            line.setZValue(30)
            point.setZValue(31)
            label.setZValue(32)
            view_box.addItem(line, ignoreBounds=True)
            view_box.addItem(point, ignoreBounds=True)
            view_box.addItem(label, ignoreBounds=True)
            state = {"line": line, "point": point, "label": label}
            self._cursor_state[key] = state
        state["line"].setValue(nearest["plot_x"])
        state["point"].setData([nearest["plot_x"]], [nearest["plot_y"]])
        state["label"].setText(self._format_point_label("", nearest))
        state["label"].setPos(nearest["plot_x"], nearest["plot_y"])
        state["nearest"] = nearest
        self._set_status(
            f"Cursor · {nearest['name']} · sample {nearest['index'] + 1} · "
            f"X {short_number(nearest['x'])} · Y {short_number(nearest['y'])}"
        )

    def _add_data_tip(self, view, nearest) -> None:
        self._tip_counter += 1
        tip_id = self._tip_counter
        key = self._key(view)
        item = nearest["item"]
        base_pen = pg.mkPen(getattr(item, "_sidmat_base_pen", item.opts.get("pen")))
        color = base_pen.color() if base_pen is not None else QtGui.QColor(CURVE_COLORS[0])
        point = DataTipPoint(
            [nearest["plot_x"]],
            [nearest["plot_y"]],
            size=10,
            pen=pg.mkPen("#071827", width=1.0),
            brush=pg.mkBrush(color),
            on_drag=lambda scene, selected=tip_id: self._drag_data_tip(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label = DataTipText(
            self._format_point_label("", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color, width=1.0),
            anchor=(-0.05, 1.05),
            on_drag=lambda scene, selected=tip_id: self._drag_tip_label(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label.setFont(plot_font())
        point.setZValue(40)
        label.setZValue(41)
        label.setPos(nearest["plot_x"], nearest["plot_y"])
        view_box = self._view_box(view)
        view_box.addItem(point, ignoreBounds=True)
        view_box.addItem(label, ignoreBounds=True)
        self._data_tips[tip_id] = {
            "key": key,
            "item": item,
            "nearest": nearest,
            "point": point,
            "label": label,
        }
        self._set_status(f"Data tip added to {nearest['name']}.")

    def _drag_data_tip(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        view = self._views[tip["key"]]
        nearest = self.nearest_point(view, scene_position, only_item=tip["item"])
        if nearest is None:
            return
        tip["nearest"] = nearest
        tip["point"].setData([nearest["plot_x"]], [nearest["plot_y"]])
        tip["label"].setText(self._format_point_label("", nearest))
        tip["label"].setPos(nearest["plot_x"], nearest["plot_y"])

    def _drag_tip_label(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        view = self._views[tip["key"]]
        mouse = self._view_box(view).mapSceneToView(scene_position)
        nearest = tip["nearest"]
        right = mouse.x() >= nearest["plot_x"]
        above = mouse.y() >= nearest["plot_y"]
        tip["label"].setAnchor(
            (-0.05 if right else 1.05, 1.05 if above else -0.05)
        )
        tip["label"].setPos(nearest["plot_x"], nearest["plot_y"])

    # ------------------------------------------------------------------
    # Markers and annotations
    # ------------------------------------------------------------------

    def set_marker(self, name: str, *, view: QtWidgets.QWidget | None = None) -> None:
        view = view or self.active_view()
        self.activate(view)
        candidates = self._candidate_items(view)
        if not candidates:
            self._set_status("Plot a curve before setting a marker.")
            return
        item = candidates[0]
        key = self._key(view)
        cursor = self._cursor_state.get(key, {}).get("nearest")
        if cursor is not None and cursor["item"] is item:
            nearest = cursor
        else:
            x_range = self._view_box(view).viewRange()[0]
            nearest = self._nearest_for_view_x(
                view, item, (x_range[0] + x_range[1]) / 2.0
            )
        if nearest is None:
            return
        self._remove_marker(key, name)
        color = "#e64a19" if name == "A" else "#7b1fa2"
        line = pg.InfiniteLine(
            pos=nearest["plot_x"],
            angle=90,
            movable=True,
            pen=pg.mkPen(color, width=1.6),
        )
        point = pg.ScatterPlotItem(
            [nearest["plot_x"]],
            [nearest["plot_y"]],
            size=10,
            pen=pg.mkPen("#ffffff", width=1.0),
            brush=pg.mkBrush(color),
        )
        label = pg.TextItem(
            self._format_point_label(f"{name} · ", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color, width=1.0),
            anchor=(0.0, 1.0),
        )
        label.setFont(plot_font())
        line.setZValue(20)
        point.setZValue(21)
        label.setZValue(22)
        label.setPos(nearest["plot_x"], nearest["plot_y"])
        view_box = self._view_box(view)
        view_box.addItem(line, ignoreBounds=True)
        view_box.addItem(point, ignoreBounds=True)
        view_box.addItem(label, ignoreBounds=True)
        self._markers[key][name] = {
            "item": item,
            "nearest": nearest,
            "line": line,
            "point": point,
            "label": label,
        }
        line.sigPositionChanged.connect(
            lambda _line=None, selected_key=key, selected_name=name: self._marker_moved(
                selected_key, selected_name, False
            )
        )
        line.sigPositionChangeFinished.connect(
            lambda _line=None, selected_key=key, selected_name=name: self._marker_moved(
                selected_key, selected_name, True
            )
        )
        self._update_marker_readout(view)

    def _marker_moved(self, key: str, name: str, finished: bool) -> None:
        if self._moving_marker:
            return
        marker = self._markers.get(key, {}).get(name)
        if marker is None:
            return
        view = self._views[key]
        nearest = self._nearest_for_view_x(
            view, marker["item"], float(marker["line"].value())
        )
        if nearest is None:
            return
        marker["nearest"] = nearest
        marker["point"].setData([nearest["plot_x"]], [nearest["plot_y"]])
        marker["label"].setText(self._format_point_label(f"{name} · ", nearest))
        marker["label"].setPos(nearest["plot_x"], nearest["plot_y"])
        if finished:
            self._moving_marker = True
            try:
                marker["line"].setValue(nearest["plot_x"])
            finally:
                self._moving_marker = False
        self._update_marker_readout(view)

    def _update_marker_readout(self, view: QtWidgets.QWidget) -> None:
        if self._key(view) != self._active_key:
            return
        markers = self._markers.get(self._key(view), {})
        a = markers.get("A", {}).get("nearest")
        b = markers.get("B", {}).get("nearest")
        a_text = "—" if a is None else f"{short_number(a['x'])}, {short_number(a['y'])}"
        b_text = "—" if b is None else f"{short_number(b['x'])}, {short_number(b['y'])}"
        if a is not None and b is not None:
            delta = (
                f"{short_number(b['x'] - a['x'])}, "
                f"{short_number(b['y'] - a['y'])}"
            )
        else:
            delta = "—"
        self.marker_readout.setText(f"A {a_text}   B {b_text}   Δ {delta}")

    @staticmethod
    def _safe_remove(view_box, item) -> None:
        try:
            view_box.removeItem(item)
        except (RuntimeError, ValueError):
            pass

    def _remove_data_tip(self, tip_id: int) -> None:
        tip = self._data_tips.pop(tip_id, None)
        if tip is None:
            return
        view_box = self._view_box(self._views[tip["key"]])
        self._safe_remove(view_box, tip["point"])
        self._safe_remove(view_box, tip["label"])

    def clear_data_tips(self, view: QtWidgets.QWidget | None = None) -> None:
        key = self._key(view) if view is not None else None
        for tip_id, tip in list(self._data_tips.items()):
            if key is None or tip["key"] == key:
                self._remove_data_tip(tip_id)

    def _remove_cursor(self, key: str) -> None:
        state = self._cursor_state.pop(key, None)
        if state is None:
            return
        view_box = self._view_box(self._views[key])
        for item_name in ("line", "point", "label"):
            self._safe_remove(view_box, state[item_name])

    def _remove_marker(self, key: str, name: str) -> None:
        marker = self._markers.get(key, {}).pop(name, None)
        if marker is None:
            return
        view_box = self._view_box(self._views[key])
        for item_name in ("line", "point", "label"):
            self._safe_remove(view_box, marker[item_name])

    def clear_annotations(
        self,
        *,
        view: QtWidgets.QWidget | None = None,
        all_views: bool = False,
    ) -> None:
        views = list(self._views.values()) if all_views else [view or self.active_view()]
        for selected in views:
            key = self._key(selected)
            self.clear_data_tips(selected)
            self._remove_cursor(key)
            for marker_name in list(self._markers[key]):
                self._remove_marker(key, marker_name)
            self._update_marker_readout(selected)

    # ------------------------------------------------------------------
    # Zoom/navigation
    # ------------------------------------------------------------------

    def remember_range(self, view: QtWidgets.QWidget) -> None:
        key = self._key(view)
        ranges = self._view_box(view).viewRange()
        snapshot = (
            (float(ranges[0][0]), float(ranges[0][1])),
            (float(ranges[1][0]), float(ranges[1][1])),
        )
        if self._last_saved_range[key] == snapshot:
            return
        self._zoom_history[key].append(snapshot)
        self._last_saved_range[key] = snapshot

    def rubber_zoom(self, view: QtWidgets.QWidget, start, stop) -> None:
        self.activate(view)
        if abs(stop.x() - start.x()) < 1e-12 or abs(stop.y() - start.y()) < 1e-12:
            return
        self.remember_range(view)
        self._view_box(view).setRange(
            xRange=sorted((float(start.x()), float(stop.x()))),
            yRange=sorted((float(start.y()), float(stop.y()))),
            padding=0.0,
        )

    @staticmethod
    def _padded(low: float, high: float) -> tuple[float, float]:
        margin = max(abs(low) * 0.05, 1.0) if high <= low else (high - low) * 0.04
        return low - margin, high + margin

    def auto_range(
        self,
        *,
        view: QtWidgets.QWidget | None = None,
        remember: bool = True,
    ) -> None:
        view = view or self.active_view()
        if remember:
            self.activate(view)
            self.remember_range(view)
        x_parts: list[np.ndarray] = []
        y_parts: list[np.ndarray] = []
        for item in view._pw.listDataItems():
            if not item.isVisible():
                continue
            arrays = self._curve_arrays(view, item)
            if arrays is None:
                continue
            _raw_x, _raw_y, display_x, display_y, indices = arrays
            if len(indices):
                x_parts.append(display_x[indices])
                y_parts.append(display_y[indices])
        if not x_parts:
            view._pw.getPlotItem().autoRange()
            return
        x_values = np.concatenate(x_parts)
        y_values = np.concatenate(y_parts)
        y_range = (
            (0.0, 1.05)
            if self._key(view) == "coherence"
            else self._padded(float(np.min(y_values)), float(np.max(y_values)))
        )
        self._view_box(view).setRange(
            xRange=self._padded(float(np.min(x_values)), float(np.max(x_values))),
            yRange=y_range,
            padding=0.0,
        )
        if remember:
            self._set_status(
                f"Auto fit: {getattr(view, '_plot_title', self._key(view))}"
            )

    def previous_zoom(self, *, view: QtWidgets.QWidget | None = None) -> bool:
        view = view or self.active_view()
        self.activate(view)
        key = self._key(view)
        if not self._zoom_history[key]:
            self._set_status("No previous zoom range is available")
            return False
        x_range, y_range = self._zoom_history[key].pop()
        self._view_box(view).setRange(
            xRange=x_range, yRange=y_range, padding=0.0
        )
        self._last_saved_range[key] = None
        self._set_status("Previous zoom restored")
        return True

    # ------------------------------------------------------------------
    # Menus, export and status
    # ------------------------------------------------------------------

    @staticmethod
    def _screen_point(position) -> QtCore.QPoint:
        if hasattr(position, "toPoint"):
            return position.toPoint()
        return QtCore.QPoint(int(position.x()), int(position.y()))

    def _show_plot_menu(self, view, screen_position) -> None:
        menu = QtWidgets.QMenu(self.host)
        previous = menu.addAction("Previous zoom")
        auto = menu.addAction("Auto fit")
        menu.addSeparator()
        cursor = menu.addAction("Cursor")
        cursor.setCheckable(True)
        cursor.setChecked(self.pointer_tool() == "cursor")
        data_tip = menu.addAction("Data tip")
        data_tip.setCheckable(True)
        data_tip.setChecked(self.pointer_tool() == "data-tip")
        clear_tips = menu.addAction("Clear data tips")
        marker_a = menu.addAction("Set marker A")
        marker_b = menu.addAction("Set marker B")
        clear_annotations = menu.addAction("Clear annotations")
        menu.addSeparator()
        hide_curve = menu.addAction("Hide selected curve")
        hide_curve.setEnabled(
            self._selected_items.get(self._key(view)) in view._pw.listDataItems()
        )
        show_curves = menu.addAction("Show all curves")
        menu.addSeparator()
        copy_image = menu.addAction("Copy image")
        export = menu.addAction("Export plot curves")
        action = menu.exec(self._screen_point(screen_position))
        if action == previous:
            self.previous_zoom(view=view)
        elif action == auto:
            self.auto_range(view=view)
        elif action == cursor:
            self.set_pointer_tool("cursor", True)
        elif action == data_tip:
            self.set_pointer_tool("data-tip", True)
        elif action == clear_tips:
            self.clear_data_tips(view)
        elif action == marker_a:
            self.set_marker("A", view=view)
        elif action == marker_b:
            self.set_marker("B", view=view)
        elif action == clear_annotations:
            self.clear_annotations(view=view)
        elif action == hide_curve:
            self.hide_selected_curve(view)
        elif action == show_curves:
            self.show_all_curves(view)
        elif action == copy_image:
            self.host._copy_active_plot()
        elif action == export:
            self.export_active_dialog()

    def _show_tip_menu(self, tip_id: int, screen_position) -> None:
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        menu = QtWidgets.QMenu(self.host)
        remove = menu.addAction("Delete this data tip")
        clear = menu.addAction("Clear all data tips")
        action = menu.exec(self._screen_point(screen_position))
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        if action == remove:
            self._remove_data_tip(tip_id)
        elif action == clear:
            self.clear_data_tips()

    def export_curves_to(
        self, path: str | Path, *, view: QtWidgets.QWidget | None = None
    ) -> Path:
        view = view or self.active_view()
        items = self._candidate_items(view)
        if not items:
            raise ValueError("the active plot has no curves")
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["curve", "x", "y"])
            for item in items:
                arrays = self._curve_arrays(view, item)
                if arrays is None:
                    continue
                raw_x, raw_y, _display_x, _display_y, indices = arrays
                name = self._curve_name(item)
                writer.writerows(
                    (name, f"{raw_x[index]:.17g}", f"{raw_y[index]:.17g}")
                    for index in indices
                )
        return output

    def export_active_dialog(self) -> None:
        default = f"sidmat_{self._active_key}_curves.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.host, "Export plot curves", default, "CSV (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            output = self.export_curves_to(path)
        except (OSError, ValueError) as exc:
            self._set_status(f"Export failed: {exc}")
            return
        self._set_status(f"Exported plot curves to {output}")

    def _set_status(self, message: str) -> None:
        label = getattr(self.host, "status_lbl", None)
        if label is not None:
            label.setText(str(message))
