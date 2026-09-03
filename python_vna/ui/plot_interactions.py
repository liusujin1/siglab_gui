from __future__ import annotations

import numpy as np

from python_vna.optional import require


QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
pg = require("pyqtgraph", "python -m pip install -e .[gui]")


CURVE_Z = 0
LEGEND_Z = 10
MARKER_Z = 20
CURSOR_Z = 30
DATA_TIP_Z = 40


def copy_widget_image_to_clipboard(widget: QtWidgets.QWidget) -> bool:
    """Copy the rendered widget image to the system clipboard."""
    if widget is None:
        return False
    try:
        pixmap = widget.grab()
    except (RuntimeError, TypeError):
        return False
    if pixmap.isNull():
        return False
    clipboard = QtWidgets.QApplication.clipboard()
    clipboard.setPixmap(pixmap, QtGui.QClipboard.Clipboard)
    return True


class VnaAxisItem(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, "enableAutoSIPrefix"):
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
                linear_value = (10.0**value) * scale
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
    def __init__(self, *args, on_context_menu=None, on_drag=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_context_menu = on_context_menu
        self._on_drag = on_drag

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == QtCore.Qt.RightButton and self._on_context_menu is not None:
            ev.accept()
            self._on_context_menu(ev.screenPos())
            return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev) -> None:
        if ev.button() != QtCore.Qt.LeftButton or self._on_drag is None:
            ev.ignore()
            return
        ev.accept()
        self._on_drag(ev.scenePos())


def data_tip_anchor_for_label_drag(
    mouse_plot_x: float,
    mouse_plot_y: float,
    point_plot_x: float,
    point_plot_y: float,
) -> tuple[float, float]:
    right = float(mouse_plot_x) >= float(point_plot_x)
    above = float(mouse_plot_y) >= float(point_plot_y)
    return (-0.05 if right else 1.05, 1.05 if above else -0.05)


class VnaViewBox(pg.ViewBox):
    def __init__(self, *args, on_left_drag=None, on_right_drag_zoom=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_left_drag = on_left_drag
        self._on_right_drag_zoom = on_right_drag_zoom
        self._zoom_box = QtWidgets.QGraphicsRectItem()
        self._zoom_box.setPen(pg.mkPen("#2a9aab", width=1.6, style=QtCore.Qt.DashLine))
        self._zoom_box.setBrush(pg.mkBrush(42, 154, 171, 48))
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


def cursor_palette_for_background(background: str) -> dict[str, object]:
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


def apply_text_item_style(text_item, *, color, fill, border) -> None:
    text_item.setColor(color)
    text_item.fill = pg.mkBrush(fill)
    text_item.border = pg.mkPen(border, width=0.9)
    text_item.update()


# Preserve the private helper names imported by older modules and tests.
_data_tip_anchor_for_label_drag = data_tip_anchor_for_label_drag
_cursor_palette_for_background = cursor_palette_for_background
_apply_text_item_style = apply_text_item_style
