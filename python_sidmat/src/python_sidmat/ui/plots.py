"""Measurement plots (time waveform, FRF magnitude/phase, coherence).

Built on pyqtgraph.  The FRF is plotted with a logarithmic X axis; magnitude
is shown in dB.  Each plot has a crosshair that reports the value under the
cursor.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from python_sidmat.analysis.pwelch import PwelchResult

__all__ = ["MeasurementPlots"]


class _CrosshairPlot(pg.PlotWidget):
    """PlotWidget with a draggable crosshair readout and clean light theme."""

    def __init__(self, *args, default_pen_color: str = "#2563eb", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setBackground("#ffffff")
        self._default_pen_color = default_pen_color
        self.showGrid(x=True, y=True, alpha=0.35)

        # Style axes
        for axis_name in ("left", "bottom", "right", "top"):
            ax = self.getAxis(axis_name)
            if ax:
                ax.setPen(pg.mkPen("#cbd5e1", width=1))
                ax.setTextPen(pg.mkPen("#475569"))

        crosshair_pen = pg.mkPen("#2563eb", width=1, style=QtCore.Qt.PenStyle.DashLine)
        self._v_line = pg.InfiniteLine(angle=90, movable=True, pen=crosshair_pen)
        self._h_line = pg.InfiniteLine(angle=0, movable=True, pen=crosshair_pen)
        self.addItem(self._v_line)
        self.addItem(self._h_line)
        self._label = pg.TextItem(anchor=(1, 1), color="#0f172a", fill=pg.mkBrush("#f8fafc"))
        self.addItem(self._label)
        self._label.setPos(0, 0)
        self._v_line.setPos(0)
        self._h_line.setPos(0)
        self._label.hide()
        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._last_data: tuple[np.ndarray, np.ndarray] | None = None

    def set_data(self, x: np.ndarray, y: np.ndarray, name: str = "y", pen_color: str | None = None) -> None:
        self._last_data = (np.asarray(x), np.asarray(y))
        self._data_name = name
        color = pen_color or self._default_pen_color
        self.clear()
        self.addItem(self._v_line)
        self.addItem(self._h_line)
        self.addItem(self._label)
        self.plot(x, y, pen=pg.mkPen(color, width=2))

    def _on_mouse_moved(self, pos: QtCore.QPointF) -> None:
        if self._last_data is None:
            return
        vb = self.getViewBox()
        if vb is None:
            return
        # pyqtgraph's scene signal already supplies scene coordinates.  Mapping
        # it through the widget a second time offsets the crosshair on screen.
        scene_pos = pos
        if not vb.sceneBoundingRect().contains(scene_pos):
            return
        mouse_point = vb.mapSceneToView(scene_pos)
        x, y = mouse_point.x(), mouse_point.y()
        self._v_line.setPos(x)
        self._h_line.setPos(y)
        self._label.setText(
            f" x={x:.4g}  {self._data_name}={y:.4g} ", color="#0f172a"
        )
        self._label.setVisible(True)


class MeasurementPlots(QtWidgets.QWidget):
    """Vertical stack of time / FRF-mag / FRF-phase / coherence plots."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.time_plot = _CrosshairPlot(default_pen_color="#2563eb")
        self.time_plot.setLabel("bottom", "Time", units="s")
        self.time_plot.setLabel("left", "Amplitude")
        legend = self.time_plot.addLegend(offset=(10, 10))
        if legend:
            legend.setBrush(pg.mkBrush("#ffffff"))
            legend.setPen(pg.mkPen("#cbd5e1"))

        self.frf_plot = _CrosshairPlot(default_pen_color="#1d4ed8")
        self.frf_plot.setLogMode(x=True, y=False)
        self.frf_plot.setLabel("bottom", "Frequency", units="Hz")
        self.frf_plot.setLabel("left", "Magnitude", units="dB")

        self.phase_plot = _CrosshairPlot(default_pen_color="#d97706")
        self.phase_plot.setLogMode(x=True, y=False)
        self.phase_plot.setLabel("bottom", "Frequency", units="Hz")
        self.phase_plot.setLabel("left", "Phase", units="deg")

        self.coh_plot = _CrosshairPlot(default_pen_color="#059669")
        self.coh_plot.setLogMode(x=True, y=False)
        self.coh_plot.setLabel("bottom", "Frequency", units="Hz")
        self.coh_plot.setLabel("left", "Coherence", units="")
        self.coh_plot.setYRange(0, 1.05)

        for w, stretch in (
            (self.time_plot, 2),
            (self.frf_plot, 2),
            (self.phase_plot, 2),
            (self.coh_plot, 1),
        ):
            layout.addWidget(w, stretch)

    def clear_all(self) -> None:
        for p in (self.time_plot, self.frf_plot, self.phase_plot, self.coh_plot):
            p._last_data = None
            p._label.hide()
            p.clear()
            p.addItem(p._v_line)
            p.addItem(p._h_line)

    def show_raw(
        self,
        ch0: np.ndarray,
        ch1: np.ndarray,
        sample_rate: float,
        name0: str,
        name1: str,
        undersample: int = 1,
    ) -> None:
        """Plot the acquired time series (all averages concatenated)."""
        effective_rate = sample_rate / max(1, int(undersample)) if sample_rate else 0.0
        dt = 1.0 / effective_rate if effective_rate else 1.0
        t = np.arange(len(ch0)) * dt
        self.time_plot._last_data = (np.asarray(t), np.asarray(ch0))
        self.time_plot._data_name = name0 or "Ch0"
        self.time_plot.clear()
        self.time_plot.addItem(self.time_plot._v_line)
        self.time_plot.addItem(self.time_plot._h_line)
        self.time_plot.addItem(self.time_plot._label)
        self.time_plot.plot(t, np.asarray(ch0), pen=pg.mkPen("#2563eb", width=2.0), name=name0)
        self.time_plot.plot(t, np.asarray(ch1), pen=pg.mkPen("#dc2626", width=2.0), name=name1)

    def show_pwelch(self, result: PwelchResult) -> None:
        """Plot FRF magnitude (dB), phase and coherence from a pwelch result."""
        freq = result.freq
        # drop DC bin for the log axis (log(0) is undefined)
        mask = freq > 0
        mag_db = 20.0 * np.log10(np.maximum(result.amplitude, 1e-30))
        self.frf_plot.set_data(freq[mask], mag_db[mask], name="mag", pen_color="#1d4ed8")
        self.phase_plot.set_data(freq[mask], result.phase_deg[mask], name="phase", pen_color="#d97706")
        self.coh_plot.set_data(freq[mask], result.coherence[mask], name="coh", pen_color="#059669")
