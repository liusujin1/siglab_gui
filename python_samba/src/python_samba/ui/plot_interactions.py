"""Shared pyqtgraph interaction primitives for live and recorded curves."""

from __future__ import annotations

from typing import Any, Callable

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc

try:
    import numpy as np
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional GUI analysis dependencies
    np = None  # type: ignore[assignment]
    pg = None  # type: ignore[assignment]


PLOT_BACKGROUND = "#fbfdfe"
PLOT_FOREGROUND = "#31566c"
CURVE_COLORS = (
    "#1875a6",
    "#dc6b2f",
    "#3f9b55",
    "#8a5ab7",
    "#c43b62",
    "#287d8e",
    "#bd8a22",
    "#526fb4",
    "#8b6f47",
    "#1c9b8e",
    "#b44c9b",
    "#6d7a86",
)
PLOT_FONT_POINTS = 9


def plot_font_pixel_size(points: float = PLOT_FONT_POINTS) -> int:
    """Return an adaptive logical-pixel plot font size."""

    app = QtWidgets.QApplication.instance()
    try:
        scale = float(app.property("python_samba_font_scale") or 1.0) if app else 1.0
    except (TypeError, ValueError):
        scale = 1.0
    return min(14, max(7, int(round(float(points) * (4.0 / 3.0) * scale))))


def plot_font(*, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(plot_font_pixel_size())
    font.setBold(bold)
    return font


def short_number(value: float) -> str:
    """Readable plot text without exponent notation."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if np is not None and not np.isfinite(number):
        return "—"
    absolute = abs(number)
    if absolute >= 1_000_000_000.0:
        return f"{number / 1_000_000_000.0:.6f}".rstrip("0").rstrip(".") + "G"
    if absolute >= 1_000_000.0:
        return f"{number / 1_000_000.0:.6f}".rstrip("0").rstrip(".") + "M"
    if absolute >= 1_000.0:
        return f"{number / 1_000.0:.6f}".rstrip("0").rstrip(".") + "k"
    decimals = 9 if 0.0 < absolute < 1.0 else 6
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".") or "0"


if pg is not None:

    class PlainAxisItem(pg.AxisItem):
        """Axis labels that avoid scientific notation."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.compact_tick_font = plot_font()
            self.setTickFont(self.compact_tick_font)
            self.label.setFont(self.compact_tick_font)
            self.setStyle(
                tickTextHeight=max(11, plot_font_pixel_size() + 3),
                autoExpandTextSpace=False,
            )
            if hasattr(self, "enableAutoSIPrefix"):
                self.enableAutoSIPrefix(False)

        def tickStrings(self, values, scale, spacing):  # noqa: N802
            labels: list[str] = []
            if self.logMode:
                for value in values:
                    linear = (10.0 ** float(value)) * float(scale)
                    if not np.isfinite(linear) or linear <= 0.0:
                        labels.append("")
                        continue
                    exponent = np.log10(linear)
                    labels.append(
                        short_number(linear)
                        if abs(exponent - round(exponent)) < 1e-6
                        else ""
                    )
                return labels
            return [short_number(float(value) * float(scale)) for value in values]


    class InteractiveViewBox(pg.ViewBox):
        """Left tool, right rubber zoom, middle pan, wheel navigation."""

        def __init__(
            self,
            *args,
            on_left_drag: Callable[[Any], None] | None = None,
            on_right_zoom: Callable[[Any, Any], None] | None = None,
            on_navigation_start: Callable[[], None] | None = None,
            on_wheel_start: Callable[[int | None], None] | None = None,
            on_wheel_finish: Callable[[int | None], None] | None = None,
            on_axis_drag_start: Callable[[int], None] | None = None,
            **kwargs,
        ) -> None:
            super().__init__(*args, **kwargs)
            self._on_left_drag = on_left_drag
            self._on_right_zoom = on_right_zoom
            self._on_navigation_start = on_navigation_start
            self._on_wheel_start = on_wheel_start
            self._on_wheel_finish = on_wheel_finish
            self._on_axis_drag_start = on_axis_drag_start
            self._zoom_box = QtWidgets.QGraphicsRectItem()
            self._zoom_box.setPen(
                pg.mkPen("#2a9aab", width=1.6, style=QtCore.Qt.DashLine)
            )
            self._zoom_box.setBrush(pg.mkBrush(42, 154, 171, 48))
            self._zoom_box.setZValue(35)
            self._zoom_box.hide()
            self.addItem(self._zoom_box, ignoreBounds=True)
            self.setMouseMode(pg.ViewBox.PanMode)

        def mouseDragEvent(self, event, axis=None) -> None:  # noqa: N802
            button = event.button()
            if axis is not None and self._on_axis_drag_start is not None:
                if event.isStart():
                    self._on_axis_drag_start(int(axis))
                super().mouseDragEvent(event, axis=axis)
                return
            if button == QtCore.Qt.LeftButton and self._on_left_drag is not None:
                event.accept()
                self._on_left_drag(event.scenePos())
                return
            if button == QtCore.Qt.RightButton and self._on_right_zoom is not None:
                event.accept()
                start = self.mapSceneToView(
                    event.buttonDownScenePos(QtCore.Qt.RightButton)
                )
                stop = self.mapSceneToView(event.scenePos())
                if event.isFinish():
                    self._zoom_box.hide()
                    self._on_right_zoom(start, stop)
                    return
                self._zoom_box.setRect(QtCore.QRectF(start, stop).normalized())
                self._zoom_box.show()
                return
            if button == QtCore.Qt.MiddleButton and event.isStart():
                if self._on_navigation_start is not None:
                    self._on_navigation_start()
            super().mouseDragEvent(event, axis=axis)

        def wheelEvent(self, event, axis=None) -> None:  # noqa: N802
            if self._on_wheel_start is not None:
                self._on_wheel_start(axis)
            elif self._on_navigation_start is not None:
                self._on_navigation_start()
            super().wheelEvent(event, axis=axis)
            if self._on_wheel_finish is not None:
                self._on_wheel_finish(axis)


    class DataTipPoint(pg.ScatterPlotItem):
        def __init__(self, *args, on_drag=None, on_menu=None, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._on_drag = on_drag
            self._on_menu = on_menu

        def mouseClickEvent(self, event) -> None:  # noqa: N802
            if event.button() == QtCore.Qt.RightButton and self._on_menu is not None:
                event.accept()
                self._on_menu(event.screenPos())
                return
            super().mouseClickEvent(event)

        def mouseDragEvent(self, event) -> None:  # noqa: N802
            if event.button() != QtCore.Qt.LeftButton or self._on_drag is None:
                event.ignore()
                return
            event.accept()
            self._on_drag(event.scenePos())


    class DataTipText(pg.TextItem):
        def __init__(self, *args, on_drag=None, on_menu=None, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._on_drag = on_drag
            self._on_menu = on_menu

        def mouseClickEvent(self, event) -> None:  # noqa: N802
            if event.button() == QtCore.Qt.RightButton and self._on_menu is not None:
                event.accept()
                self._on_menu(event.screenPos())
                return
            super().mouseClickEvent(event)

        def mouseDragEvent(self, event) -> None:  # noqa: N802
            if event.button() != QtCore.Qt.LeftButton or self._on_drag is None:
                event.ignore()
                return
            event.accept()
            self._on_drag(event.scenePos())

else:  # pragma: no cover
    PlainAxisItem = InteractiveViewBox = DataTipPoint = DataTipText = None


def copy_plot_image(widget: QtWidgets.QWidget) -> bool:
    pixmap = widget.grab()
    if pixmap.isNull():
        return False
    QtWidgets.QApplication.clipboard().setPixmap(pixmap)
    return True


__all__ = [
    "CURVE_COLORS",
    "DataTipPoint",
    "DataTipText",
    "InteractiveViewBox",
    "PLOT_BACKGROUND",
    "PLOT_FONT_POINTS",
    "PLOT_FOREGROUND",
    "PlainAxisItem",
    "copy_plot_image",
    "plot_font",
    "plot_font_pixel_size",
    "short_number",
]
