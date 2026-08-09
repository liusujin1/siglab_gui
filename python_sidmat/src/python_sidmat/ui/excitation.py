"""Excitation control — port of ``SAMBA19xUI.UserControls.Excitation``.

Configures the injected noise/excitation signal: type, four parameters
(meaning depends on type), the noise injection IO point, and applies it to
the controller.

Excitation Filters sub-group mirrors the original layout:
  * a round status LED (``ExcitFilterStatusBtn``) that doubles as the noise
    filter chain ON/OFF switch (DSNFU): click it to toggle the chain
  * four filter-stage buttons (``ExcFilterMatrix``), each showing the current
    filter type short name (``----`` for NOFIL).  Clicking a button emits
    ``filterClicked(stage)`` so the main window opens the FilterDlg port.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets
from python_samba.protocol.commands import FilterStage

from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.excitation import (
    EXCITATION_TYPE_NAMES,
    ExcitationParameters,
)
from python_sidmat.measurement.filters import (
    filter_description,
    filter_name,
)
from python_sidmat.ui.io_signal_button import IOSignalButton

__all__ = ["ExcitationWidget"]

_NOISE_FILTER_STAGES = 4  # GetDiagnosticNoiseFilterStage(0..3)

# parameter labels per excitation type (from the RCI doc 4.3.5)
_PARAM_LABELS: dict[int, tuple[str, str, str, str]] = {
    0: ("Param1", "Param2", "Param3", "Param4"),        # NoNoise
    1: ("Gain", "Param2", "Param3", "Param4"),          # WhiteNoise
    2: ("Gain", "Freq (Hz)", "Param3", "Param4"),       # SineWave
    4: ("Gain", "High (ms)", "Low (ms)", "Param4"),     # DutyCycle
    5: ("Gain", "Start (Hz)", "End (Hz)", "Time (ms)"), # ChirpSine
    6: ("Gain", "Freq (Hz)", "Param3", "Param4"),       # Triangular
    7: ("Gain", "Freq (Hz)", "Param3", "Param4"),       # Sawtooth
    8: ("Param1", "Param2", "Param3", "Param4"),        # Step
}
_DEFAULT_LABELS = _PARAM_LABELS[0]


class _FilterLed(QtWidgets.QAbstractButton):
    """Round 60×60 status LED; clicking toggles the filter chain (DSNFU)."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to toggle the noise filter chain")
        self._on = False

    def set_on(self, on: bool) -> None:
        self._on = bool(on)
        self.update()

    def is_on(self) -> bool:
        return self._on

    def paintEvent(self, event) -> None:
        from PySide6 import QtGui

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        color = (0x2ECC40 if self._on else 0x999999)
        painter.setBrush(QtGui.QColor(color))
        painter.setPen(QtGui.QPen(QtGui.QColor(0x555555), 2))
        diameter = min(self.width(), self.height()) - 6
        left = (self.width() - diameter) // 2
        top = (self.height() - diameter) // 2
        painter.drawEllipse(left, top, diameter, diameter)
        # short label inside the circle, as the original LEDBtn shows
        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 0))
        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                         "ON" if self._on else "OFF")
        painter.end()


class ExcitationWidget(QtWidgets.QGroupBox):
    applyRequested = QtCore.Signal()
    filterClicked = QtCore.Signal(int)    # stage index -> open FilterDialog
    filterUsageToggled = QtCore.Signal(bool)  # noise filter chain ON/OFF

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Excitation", parent)
        self.excitation = ExcitationParameters()
        self._filter_stages = [
            FilterStage(0, index, 0, (1.0, 0.0, 0.0, 0.0, 0.0))
            for index in range(_NOISE_FILTER_STAGES)
        ]
        self._build_ui()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(2)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)

        grid.addWidget(QtWidgets.QLabel("Type"), 0, 0)
        self.type_cbx = QtWidgets.QComboBox()
        self.type_cbx.addItems(EXCITATION_TYPE_NAMES)
        self.type_cbx.currentIndexChanged.connect(self._on_type_changed)
        grid.addWidget(self.type_cbx, 0, 1)

        self.param_lbls: list[QtWidgets.QLabel] = []
        self.param_edits: list[QtWidgets.QLineEdit] = []
        for row in range(4):
            lbl = QtWidgets.QLabel(_DEFAULT_LABELS[row])
            edit = QtWidgets.QLineEdit("0")
            grid.addWidget(lbl, row + 1, 0)
            grid.addWidget(edit, row + 1, 1)
            self.param_lbls.append(lbl)
            self.param_edits.append(edit)

        # DC offset of the excitation (extended-excitation feature).
        grid.addWidget(QtWidgets.QLabel("Offset [-1,1]"), 5, 0)
        self.offset_edit = QtWidgets.QLineEdit("0")
        grid.addWidget(self.offset_edit, 5, 1)

        grid.addWidget(QtWidgets.QLabel("Inject at"), 6, 0)
        self.inject_btn = IOSignalButton(IOType(3, 0, 0))
        grid.addWidget(self.inject_btn, 6, 1)

        self.set_btn = QtWidgets.QPushButton("Set to Controller")
        self.set_btn.clicked.connect(self.apply_to_controller)
        grid.addWidget(self.set_btn, 7, 0, 1, 2)

        outer.addLayout(grid)

        # Excitation Filters sub-group (mirrors the original layout)
        filters = QtWidgets.QGroupBox("Excitation Filters")
        flo = QtWidgets.QHBoxLayout(filters)
        flo.setContentsMargins(3, 2, 3, 2)
        flo.setSpacing(2)

        self.filter_led = _FilterLed()
        self.filter_led.clicked.connect(self._on_led_clicked)
        flo.addWidget(self.filter_led)

        self.filter_btns: list[QtWidgets.QPushButton] = []
        for i in range(_NOISE_FILTER_STAGES):
            b = QtWidgets.QPushButton("----")
            b.setObjectName("filterStageButton")
            b.setFixedSize(54, 28)
            b.setToolTip(f"Filter {i + 1}: off")
            b.clicked.connect(lambda checked, idx=i: self._on_filter_clicked(idx))
            flo.addWidget(b)
            self.filter_btns.append(b)
        flo.addStretch(1)
        outer.addWidget(filters)

    # -- public API -------------------------------------------------------

    def current_excitation(self) -> ExcitationParameters:
        """Build an ExcitationParameters from the widget state."""
        values: list[float] = []
        for index, edit in enumerate(self.param_edits, start=1):
            text = edit.text().strip()
            try:
                value = float(text)
            except ValueError as exc:
                raise ValueError(f"excitation parameter {index} is not a number") from exc
            if not math.isfinite(value):
                raise ValueError(f"excitation parameter {index} must be finite")
            values.append(value)
        offset_text = self.offset_edit.text().strip()
        try:
            offset = float(offset_text)
        except ValueError as exc:
            raise ValueError("excitation offset is not a number") from exc
        if not math.isfinite(offset):
            raise ValueError("excitation offset must be finite")
        self.excitation = ExcitationParameters(
            type=max(0, self.type_cbx.currentIndex()),
            params=values,
            noise_injection_io=self.inject_btn.io_type(),
            noise_filter_usage=self.filter_led.is_on(),
            noise_filters=list(self._filter_stages),
            diag_io0=self.excitation.diag_io0,
            diag_io1=self.excitation.diag_io1,
            offset=offset,
        )
        return self.excitation

    def apply_excitation(self, exc: ExcitationParameters) -> None:
        """Populate the widget from an ExcitationParameters (DGESP readback)."""
        exc.validate()
        self.excitation = exc
        self.type_cbx.blockSignals(True)
        self.type_cbx.setCurrentIndex(max(0, min(exc.type, len(EXCITATION_TYPE_NAMES) - 1)))
        self.type_cbx.blockSignals(False)
        self._on_type_changed()
        for edit, value in zip(self.param_edits, exc.params):
            edit.setText(f"{value:g}")
        self.offset_edit.setText(f"{exc.offset:g}")
        self.inject_btn.set_io(exc.noise_injection_io, emit=False)

    def set_filter_usage(self, on: bool) -> None:
        """Update the round status LED from DSNFU readback."""
        self.filter_led.set_on(bool(on))
        self.excitation.noise_filter_usage = bool(on)

    def apply_filters(self, stages) -> None:
        """Label the 4 filter buttons from DGNFS readback.

        Stages with ``filter_type == NOFIL(0)`` show ``----``; configured
        stages show their short name (``GetFilterName`` semantics).
        """
        normalized: list[FilterStage] = []
        for i, btn in enumerate(self.filter_btns):
            stage = stages[i] if i < len(stages) else None
            if stage is None:
                stage = FilterStage(0, i, 0, (1.0, 0.0, 0.0, 0.0, 0.0))
            params = [
                float(value)
                for value in getattr(stage, "params", (0, 0, 0, 0, 0))[:5]
            ]
            params.extend([0.0] * (5 - len(params)))
            normalized.append(
                FilterStage(
                    int(getattr(stage, "axis", 0)),
                    i,
                    int(getattr(stage, "filter_type", 0)),
                    tuple(params),
                )
            )
            ftype = getattr(stage, "filter_type", 0) or 0
            btn.setText(filter_name(ftype))
            if ftype:
                btn.setToolTip(f"Filter {i + 1}: {filter_name(ftype)} — "
                               f"{filter_description(ftype)}")
            else:
                btn.setToolTip(f"Filter {i + 1}: off")
        self._filter_stages = normalized
        self.excitation.noise_filters = list(normalized)

    def current_filters(self) -> list[FilterStage]:
        """Return the four locally cached stages for offline use/settings."""

        return list(self._filter_stages)

    # -- handlers ---------------------------------------------------------

    def _on_type_changed(self) -> None:
        labels = _PARAM_LABELS.get(self.type_cbx.currentIndex(), _DEFAULT_LABELS)
        for lbl, text in zip(self.param_lbls, labels):
            lbl.setText(text)

    def _on_led_clicked(self) -> None:
        """LED click toggles the noise filter chain (DSNFU)."""
        self.filter_led.set_on(not self.filter_led.is_on())
        self.filterUsageToggled.emit(self.filter_led.is_on())

    def _on_filter_clicked(self, stage: int) -> None:
        """Clicking a filter button opens the configuration dialog (FilterDlg)."""
        self.filterClicked.emit(stage)

    def apply_to_controller(self) -> None:
        """Emit a signal for the main window to write excitation to hardware."""
        self.applyRequested.emit()
