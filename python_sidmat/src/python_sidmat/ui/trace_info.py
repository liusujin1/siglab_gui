"""Trace information control — port of ``SAMBA19xUI.UserControls.TraceInfo``.

Lets the user pick the two trace channels, the trace length, undersampling,
anti-aliasing filter and the number of averages, then start/stop a
measurement.  The start button merely emits ``startRequested``; the main
window runs the actual measurement engine on a worker thread.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.trace import TraceParameters
from python_sidmat.ui.io_signal_button import IOSignalButton

__all__ = ["TraceInfoWidget"]


def _int_validator(lo: int, hi: int) -> QtGui.QIntValidator:
    return QtGui.QIntValidator(lo, hi)


class TraceInfoWidget(QtWidgets.QGroupBox):
    startRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Trace", parent)
        self.trace = TraceParameters()
        self._build_ui()
        self._measuring = False

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(3, 3, 3, 3)
        grid.setSpacing(2)

        grid.addWidget(QtWidgets.QLabel("Ch0"), 0, 0)
        self.ch0_btn = IOSignalButton(IOType(0, 0, 0))
        grid.addWidget(self.ch0_btn, 0, 1)

        grid.addWidget(QtWidgets.QLabel("Ch1"), 1, 0)
        self.ch1_btn = IOSignalButton(IOType(0, 1, 0))
        grid.addWidget(self.ch1_btn, 1, 1)

        grid.addWidget(QtWidgets.QLabel("Length"), 2, 0)
        self.length_edit = QtWidgets.QLineEdit("100")
        self.length_edit.setValidator(_int_validator(2, 8192))
        grid.addWidget(self.length_edit, 2, 1)

        grid.addWidget(QtWidgets.QLabel("Undersample"), 3, 0)
        self.undersample_edit = QtWidgets.QLineEdit("1")
        self.undersample_edit.setValidator(_int_validator(1, 65534))
        grid.addWidget(self.undersample_edit, 3, 1)

        grid.addWidget(QtWidgets.QLabel("Anti-alias"), 4, 0)
        self.aa_check = QtWidgets.QCheckBox("Disable filter")
        # TraceParameters and the original controller default to flag 0:
        # anti-aliasing enabled.  The checkbox represents the inverse flag.
        self.aa_check.setChecked(False)
        grid.addWidget(self.aa_check, 4, 1)

        grid.addWidget(QtWidgets.QLabel("Averages"), 5, 0)
        self.avg_edit = QtWidgets.QLineEdit("3")
        self.avg_edit.setValidator(_int_validator(1, 1000))
        grid.addWidget(self.avg_edit, 5, 1)

        grid.addWidget(QtWidgets.QLabel("Fast Data Load"), 6, 0)
        self.fast_load_check = QtWidgets.QCheckBox("")
        self.fast_load_check.setToolTip(
            "Fast binary DGTBB data loading (up to 40 sample pairs per read).")
        grid.addWidget(self.fast_load_check, 6, 1)

        grid.addWidget(QtWidgets.QLabel("Sample Freq"), 7, 0)
        self.sample_freq_edit = QtWidgets.QLineEdit("-")
        self.sample_freq_edit.setValidator(_int_validator(1, 200000))
        grid.addWidget(self.sample_freq_edit, 7, 1)

        self.start_btn = QtWidgets.QPushButton(" Start")
        self.start_btn.setObjectName("primaryAction")
        self.start_btn.setIcon(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self.start_btn.clicked.connect(self._on_start_clicked)
        grid.addWidget(self.start_btn, 8, 0, 1, 2)

    # -- public API -------------------------------------------------------

    def set_measuring(self, measuring: bool) -> None:
        self._measuring = measuring
        self.start_btn.setText(" Stop" if measuring else " Start")
        self.start_btn.setIcon(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaStop if measuring
            else QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        for w in (
            self.ch0_btn, self.ch1_btn, self.length_edit, self.undersample_edit,
            self.aa_check, self.avg_edit, self.fast_load_check, self.sample_freq_edit,
        ):
            w.setEnabled(not measuring)

    def set_sample_frequency(self, fs: float) -> None:
        self.sample_freq_edit.setText(f"{fs:.0f}" if fs else "-")

    def sample_frequency(self) -> float | None:
        try:
            value = float(self.sample_freq_edit.text())
            return value if value > 0 else None
        except ValueError:
            return None

    def current_trace(self) -> TraceParameters:
        """Build a TraceParameters from the current widget state."""
        def read_int(edit: QtWidgets.QLineEdit, name: str, default: int) -> int:
            text = edit.text().strip()
            if not text:
                return default
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer, got {text!r}") from exc

        candidate = replace(
            self.trace,
            trace_ch0=self.ch0_btn.io_type(),
            trace_ch1=self.ch1_btn.io_type(),
            no_samples=read_int(self.length_edit, "Length", 100),
            undersamples=read_int(self.undersample_edit, "Undersample", 1),
            average_number=read_int(self.avg_edit, "Averages", 3),
            # RCI flag: 0 = use anti-aliasing filter, 1 = don't.
            trace_filter_flag=1 if self.aa_check.isChecked() else 0,
        )
        candidate.set_fast_data_loading(self.fast_load_check.isChecked())
        candidate.validate()
        self.trace = candidate
        return self.trace

    def apply_trace(self, trace: TraceParameters) -> None:
        """Populate the widget from a TraceParameters (e.g. DGTIV readback)."""
        trace.validate()
        self.trace.trace_ch0 = trace.trace_ch0
        self.trace.trace_ch1 = trace.trace_ch1
        self.trace.no_samples = trace.no_samples
        self.trace.undersamples = trace.undersamples
        self.trace.trace_filter_flag = trace.trace_filter_flag
        self.trace.average_number = trace.average_number
        self.trace.status = trace.status
        self.trace.set_fast_data_loading(trace.is_fast_data_loading)
        self.ch0_btn.set_io(trace.trace_ch0, emit=False)
        self.ch1_btn.set_io(trace.trace_ch1, emit=False)
        self.length_edit.setText(str(trace.no_samples))
        self.undersample_edit.setText(str(trace.undersamples))
        self.avg_edit.setText(str(trace.average_number))
        self.aa_check.setChecked(trace.trace_filter_flag != 0)
        self.fast_load_check.setChecked(trace.is_fast_data_loading)

    # -- handlers ---------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._measuring:
            self.stopRequested.emit()
        else:
            self.startRequested.emit()
