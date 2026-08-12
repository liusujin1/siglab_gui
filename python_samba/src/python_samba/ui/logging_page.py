"""Complete Logging workspace modeled after the standalone legacy tool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import time
from typing import Any, Callable

from python_samba.logging_tools import (
    FileLoggingConfig,
    FileLoggingService,
    LoggingRecord,
    load_logging_record,
    save_trace_record,
)
from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    IOSignalButton,
    SciEdit,
    format_ui_number,
)
from python_samba.ui.record_plot import RecordPlotWindow

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required") from exc


def _protocol_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


class _LoggingBridge(QtCore.QObject):
    task_finished = QtCore.Signal(int, str, object, object)
    file_sample = QtCore.Signal(object, object, int)
    file_finished = QtCore.Signal(object, object, int)
    download_progress = QtCore.Signal(int, int)


class LoggingPage(QtWidgets.QWidget):
    """Controller trace configuration plus non-blocking host file logging."""

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.bridge = _LoggingBridge(self)
        self.bridge.task_finished.connect(self._on_task_finished)
        self.bridge.file_sample.connect(self._on_file_sample)
        self.bridge.file_finished.connect(self._on_file_finished)
        self.bridge.download_progress.connect(self._on_download_progress)
        self._task_counter = 0
        self._task_callbacks: dict[int, Callable[[Any], None] | None] = {}
        self._task_generations: dict[int, int] = {}
        self._task_threads: set[threading.Thread] = set()
        self._generation = 0
        self._shutdown = False
        self._download_cancel = threading.Event()
        self.file_service: FileLoggingService | None = None
        self._active_file_duration_s: float | None = None
        self.monitor_definitions: list[tuple[int, int, int]] = [(0, index, 0) for index in range(40)]
        self.monitor_names: list[str] = [
            IOSignalButton.format_io_signal(tokens) for tokens in self.monitor_definitions
        ]
        self._definitions_loaded = False
        self._definitions_loading = False
        self._pending_file_start = False
        self._build_ui()
        self._install_compatibility_fields()

    @property
    def serial_worker_active(self) -> bool:
        return bool(
            (self.file_service and self.file_service.running)
            or any(thread.is_alive() for thread in self._task_threads)
        )

    @property
    def file_logging_active(self) -> bool:
        return bool(self.file_service and self.file_service.running)

    def _build_ui(self) -> None:
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding
        )
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(8)
        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("loggingToolbar")
        toolbar.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed
        )
        toolbar.setStyleSheet(
            "QFrame#loggingToolbar { background:#e7f3f9; border:1px solid #a9c7d7;"
            " border-radius:8px; }"
        )
        toolbar_layout = QtWidgets.QGridLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(6)
        self.logging_toolbar_layout = toolbar_layout
        self.btn_internal_start = FlatPush("Start Internal Log")
        self.btn_file_start = FlatPush("Start File Log")
        self.btn_show_records = FlatPush("Records / Plot")
        self.btn_show_analysis = FlatPush("Analysis")
        self.btn_logging_update = FlatPush("Update")
        for button in (
            self.btn_internal_start,
            self.btn_file_start,
            self.btn_show_records,
            self.btn_show_analysis,
            self.btn_logging_update,
        ):
            button.setMinimumHeight(34)
        self.file_toolbar_progress = QtWidgets.QProgressBar()
        self.file_toolbar_progress.setRange(0, 100)
        self.file_toolbar_progress.setValue(0)
        self.file_toolbar_progress.setTextVisible(False)
        self.file_toolbar_progress.setMinimumWidth(120)
        self.file_toolbar_progress.setMaximumWidth(300)
        self.file_toolbar_progress.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self.file_toolbar_elapsed = QtWidgets.QLabel("0 s elapsed")
        self.file_toolbar_elapsed.setMinimumWidth(90)
        self.file_toolbar_elapsed.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.page_status = QtWidgets.QLabel("Ready")
        self.page_status.setStyleSheet(
            "background:#ffffff; border:1px solid #aac0cc; border-radius:10px;"
            " padding:4px 12px; color:#31566c;"
        )
        self._logging_toolbar_buttons = (
            self.btn_internal_start,
            self.btn_file_start,
            self.btn_show_records,
            self.btn_show_analysis,
            self.btn_logging_update,
        )
        self._toolbar_compact: bool | None = None
        self._arrange_logging_toolbar(True)
        root.addWidget(toolbar)

        self.workspace_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.workspace_splitter.setObjectName("loggingWorkspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding
        )
        self.workspace_splitter.addWidget(
            self._wrap_workspace_column(self._build_monitor_tab(), 340, 270)
        )
        self.workspace_splitter.addWidget(
            self._wrap_workspace_column(self._build_internal_tab(), 410, 310)
        )
        self.workspace_splitter.addWidget(
            self._wrap_workspace_column(self._build_file_tab(), 270, 230)
        )
        self.workspace_splitter.setStretchFactor(0, 34)
        self.workspace_splitter.setStretchFactor(1, 41)
        self.workspace_splitter.setStretchFactor(2, 25)
        self.workspace_splitter.setSizes([430, 520, 340])
        root.addWidget(self.workspace_splitter, 3)

        self.auxiliary_panel = QtWidgets.QFrame()
        self.auxiliary_panel.setObjectName("loggingAuxiliaryPanel")
        self.auxiliary_panel.setStyleSheet(
            "QFrame#loggingAuxiliaryPanel { background:#f6fafc;"
            " border:1px solid #afc8d5; border-radius:8px; }"
        )
        auxiliary_layout = QtWidgets.QVBoxLayout(self.auxiliary_panel)
        auxiliary_layout.setContentsMargins(6, 6, 6, 6)
        auxiliary_header = QtWidgets.QHBoxLayout()
        self.auxiliary_title = QtWidgets.QLabel("Analysis Filter")
        self.auxiliary_title.setStyleSheet("font-weight:700; color:#31566c;")
        self.btn_auxiliary_close = FlatPush("Close")
        auxiliary_header.addWidget(self.auxiliary_title)
        auxiliary_header.addStretch(1)
        auxiliary_header.addWidget(self.btn_auxiliary_close)
        auxiliary_layout.addLayout(auxiliary_header)
        self.auxiliary_tabs = QtWidgets.QTabWidget()
        self.auxiliary_tabs.addTab(self._build_analysis_tab(), "Analysis Filter")
        auxiliary_layout.addWidget(self.auxiliary_tabs, 1)
        self.auxiliary_panel.hide()
        root.addWidget(self.auxiliary_panel, 2)

        self.records_window = RecordPlotWindow(self.host)
        self.records_window.open_record_requested.connect(self.open_record_dialog)
        # Compatibility aliases retained for the existing main-window tests
        # and helpers while the standalone window owns the plotting UI.
        self.record_path = self.records_window.record_path
        self.btn_record_browse = self.records_window.btn_record_browse
        self.record_summary = self.records_window.record_summary
        self.record_plot = self.records_window.plot_widget

        self.btn_internal_start.clicked.connect(self.start_internal)
        self.btn_file_start.clicked.connect(self.start_file_logging)
        self.btn_show_records.clicked.connect(self._show_records_window)
        self.btn_show_analysis.clicked.connect(lambda: self._show_auxiliary(0))
        self.btn_logging_update.clicked.connect(self.update_workspace)
        self.btn_auxiliary_close.clicked.connect(self.auxiliary_panel.hide)

    def _arrange_logging_toolbar(self, compact: bool) -> None:
        compact = bool(compact)
        if self._toolbar_compact == compact:
            return
        self._toolbar_compact = compact
        layout = self.logging_toolbar_layout
        while layout.count():
            layout.takeAt(0)
        for column in range(8):
            layout.setColumnStretch(column, 0)
        for column, button in enumerate(self._logging_toolbar_buttons):
            layout.addWidget(button, 0, column)
        if compact:
            layout.addWidget(self.file_toolbar_progress, 1, 0, 1, 4)
            layout.addWidget(self.file_toolbar_elapsed, 1, 4)
            layout.addWidget(self.page_status, 1, 5)
            layout.setColumnStretch(3, 1)
        else:
            layout.addWidget(self.file_toolbar_progress, 0, 5)
            layout.addWidget(self.file_toolbar_elapsed, 0, 6)
            layout.addWidget(self.page_status, 0, 7)
            layout.setColumnStretch(5, 1)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "logging_toolbar_layout"):
            self._arrange_logging_toolbar(event.size().width() < 1320)

    @staticmethod
    def _wrap_workspace_column(
        content: QtWidgets.QWidget, content_width: int, viewport_width: int
    ) -> QtWidgets.QScrollArea:
        content.setMinimumWidth(content_width)
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setMinimumWidth(viewport_width)
        area.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding
        )
        area.setWidget(content)
        return area

    def _show_auxiliary(self, index: int) -> None:
        index = max(0, min(index, self.auxiliary_tabs.count() - 1))
        if self.auxiliary_panel.isVisible() and self.auxiliary_tabs.currentIndex() == index:
            self.auxiliary_panel.hide()
            return
        self.auxiliary_tabs.setCurrentIndex(index)
        self.auxiliary_title.setText(self.auxiliary_tabs.tabText(index))
        self.auxiliary_panel.show()

    def _show_records_window(self) -> None:
        if self.records_window.isMinimized():
            self.records_window.showNormal()
        else:
            self.records_window.show()
        self.records_window.raise_()
        self.records_window.activateWindow()

    def eventFilter(self, watched, event):  # noqa: N802
        if (
            watched is getattr(self, "monitor_table", None)
            and event.type() == QtCore.QEvent.Resize
        ):
            QtCore.QTimer.singleShot(0, self._resize_monitor_columns)
        return super().eventFilter(watched, event)

    def _resize_monitor_columns(self) -> None:
        """Keep channel numbers narrow and give both signal buttons the spare width."""
        table = getattr(self, "monitor_table", None)
        if table is None:
            return
        available = table.viewport().width()
        if available <= 0:
            return

        number_width = 40
        live_width = 62
        minimum_signal = 110
        minimum_total = (number_width + live_width + minimum_signal) * 2
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 3):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            table.setColumnWidth(column, number_width)
        for column in (2, 5):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            table.setColumnWidth(column, live_width)
        for column in (1, 4):
            if available >= minimum_total:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
                table.setColumnWidth(column, minimum_signal)

    def _build_monitor_tab(self) -> QtWidgets.QWidget:
        page = GroupPanel("Monitor Signals Definition")
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(8, 10, 8, 8)
        root.setSpacing(6)
        controls = QtWidgets.QHBoxLayout()
        self.monitor_used = QtWidgets.QSpinBox()
        self.monitor_used.setRange(1, 40)
        self.monitor_used.setValue(3)
        self.monitor_used.setSuffix(" channels")
        self.btn_monitor_defs = FlatPush("Definitions")
        self.btn_monitor_live = FlatPush("Live values")
        controls.addWidget(QtWidgets.QLabel("Signals"))
        controls.addWidget(self.monitor_used)
        controls.addStretch(1)
        controls.addWidget(self.btn_monitor_defs)
        controls.addWidget(self.btn_monitor_live)
        root.addLayout(controls)

        self.monitor_table = QtWidgets.QTableWidget(20, 6)
        self.monitor_table.setHorizontalHeaderLabels(
            ["#", "Signal", "Live", "#", "Signal", "Live"]
        )
        self.monitor_table.setAlternatingRowColors(True)
        self.monitor_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.monitor_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.monitor_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.monitor_table.verticalHeader().setVisible(False)
        header = self.monitor_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(24)
        for column in range(6):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
        self.monitor_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.monitor_signal_buttons: list[FlatPush] = []
        for channel in range(40):
            visual_row = channel % 20
            base_column = 0 if channel < 20 else 3
            index_item = QtWidgets.QTableWidgetItem(str(channel + 1))
            index_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.monitor_table.setItem(visual_row, base_column, index_item)
            signal_button = FlatPush(
                IOSignalButton.format_io_signal(self.monitor_definitions[channel])
            )
            signal_button.setMinimumWidth(0)
            signal_button.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
            )
            signal_button.clicked.connect(
                lambda _checked=False, selected=channel, source=signal_button: (
                    self._open_monitor_signal_menu(selected, source)
                )
            )
            self.monitor_signal_buttons.append(signal_button)
            self.monitor_table.setCellWidget(
                visual_row, base_column + 1, signal_button
            )
            live_item = QtWidgets.QTableWidgetItem("—")
            live_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.monitor_table.setItem(visual_row, base_column + 2, live_item)
            self.monitor_table.setRowHeight(visual_row, 31)
        for channel in range(40):
            self._set_monitor_row(channel, self.monitor_definitions[channel], None)
        self.monitor_table.setCurrentCell(0, 1)
        root.addWidget(self.monitor_table, 1)
        self.monitor_table.installEventFilter(self)
        self._resize_monitor_columns()

        # The legacy UI edits each of the forty signal buttons directly.  Keep
        # one hidden IOSignalButton as the shared popup/menu owner so the table
        # stays lightweight, but do not expose the redundant "Selected Signal"
        # editor that used to require a second Apply click.
        self.monitor_number = QtWidgets.QSpinBox(page)
        self.monitor_number.setRange(1, 40)
        self.monitor_number.setValue(1)
        self.monitor_number.hide()
        self.monitor_selector = IOSignalButton(
            tokens=self.monitor_definitions[0], parent=page
        )
        self.monitor_selector.hide()
        self.monitor_raw = QtWidgets.QLabel(
            "Type 0 · Main 0 · Sub 0", parent=page
        )
        self.monitor_raw.hide()
        self.btn_monitor_write = FlatPush("Apply", parent=page)
        self.btn_monitor_write.hide()
        self.monitor_table.currentCellChanged.connect(self._monitor_row_changed)
        self.monitor_number.valueChanged.connect(self._monitor_number_changed)
        self.monitor_selector.ioSignalChanged.connect(self._monitor_selector_changed)
        self.btn_monitor_defs.clicked.connect(self.read_monitor_definitions)
        self.btn_monitor_live.clicked.connect(self.read_live_values)
        self.btn_monitor_write.clicked.connect(self.write_selected_monitor)
        return page

    def _build_internal_tab(self) -> QtWidgets.QWidget:
        page = GroupPanel("Internal Logging Setting")
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(8, 10, 8, 8)
        root.setSpacing(6)

        params_group = GroupPanel("Parameter Setting")
        params_form = QtWidgets.QFormLayout(params_group)
        self.logging_type_combo = QtWidgets.QComboBox()
        for label, protocol_value in (
            ("OverCurrent Event", 0),
            ("Event Signal Event", 1),
            ("Standard", 2),
        ):
            self.logging_type_combo.addItem(label, protocol_value)

        specs = (
            ("Samples Num", 1, 0x20000, 1024),
            ("Signals Num", 1, 40, 3),
            ("Undersample", 1, 0xFFFF, 1),
            ("Delay Sample", 1, 0x7FFFFFFF, 1),
        )
        parameter_spins: list[QtWidgets.QSpinBox] = []
        for _label, minimum, maximum, value in specs:
            spin = QtWidgets.QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setValue(value)
            spin.setGroupSeparatorShown(True)
            parameter_spins.append(spin)
        (
            self.internal_samples,
            self.internal_signal_count,
            self.internal_undersample,
            self.internal_delay_samples,
        ) = parameter_spins
        self.internal_average = QtWidgets.QCheckBox()
        self.internal_average.setToolTip("Average samples before storing the trace")
        # Compatibility for code that iterates over the numeric parameter
        # editors; protocol type and average now use their proper controls.
        self.internal_param_spins = parameter_spins
        params_form.addRow("Logging Type", self.logging_type_combo)
        params_form.addRow("Signals Num", self.internal_signal_count)
        params_form.addRow("Samples Num", self.internal_samples)
        params_form.addRow("Undersample", self.internal_undersample)
        params_form.addRow("Delay Sample", self.internal_delay_samples)
        params_form.addRow("Average", self.internal_average)
        parameter_actions = QtWidgets.QHBoxLayout()
        self.btn_internal_apply = FlatPush("Set Parameters")
        self.btn_internal_read = FlatPush("Get Parameters")
        parameter_actions.addWidget(self.btn_internal_apply)
        parameter_actions.addWidget(self.btn_internal_read)
        params_form.addRow(parameter_actions)
        root.addWidget(params_group)

        event_group = GroupPanel("Event Setting")
        event_form = QtWidgets.QFormLayout(event_group)
        self.event_selector = IOSignalButton(tokens=(0, 0, 0))
        self.event_selector.setMinimumWidth(180)
        self.event_threshold = QtWidgets.QDoubleSpinBox()
        self.event_threshold.setRange(-1e12, 1e12)
        self.event_threshold.setDecimals(8)
        self.event_threshold.setGroupSeparatorShown(True)
        self.event_trigger_samples = QtWidgets.QSpinBox()
        self.event_trigger_samples.setRange(0, 0x7FFFFFFF)
        event_form.addRow("Signal", self.event_selector)
        event_form.addRow("Threshold", self.event_threshold)
        event_form.addRow("Trigger samples", self.event_trigger_samples)
        root.addWidget(event_group)

        download = GroupPanel("Logged Traces")
        download_layout = QtWidgets.QGridLayout(download)
        self.trace_selector = QtWidgets.QComboBox()
        self.trace_selector.addItem("Trace 0", 0)
        self.trace_event_time = QtWidgets.QLabel("Event time: —")
        self.trace_output = QtWidgets.QLineEdit(
            str(Path.cwd() / "logs" / "controller_trace_0.csv")
        )
        self.btn_trace_browse = FlatPush("Browse…")
        self.btn_trace_download = FlatPush("Download trace")
        self.btn_trace_cancel = FlatPush("Cancel")
        self.btn_trace_cancel.setEnabled(False)
        self.trace_progress = QtWidgets.QProgressBar()
        self.trace_progress.setRange(0, 100)
        self.trace_progress.setValue(0)
        self.trace_download_summary = QtWidgets.QPlainTextEdit()
        self.trace_download_summary.setReadOnly(True)
        self.trace_download_summary.setMaximumHeight(90)
        self.trace_download_summary.setPlaceholderText(
            "Downloaded samples and output path will appear here."
        )
        download_layout.addWidget(QtWidgets.QLabel("Logged trace"), 0, 0)
        download_layout.addWidget(self.trace_selector, 0, 1)
        download_layout.addWidget(self.trace_event_time, 0, 2)
        download_layout.addWidget(QtWidgets.QLabel("Output file"), 1, 0)
        download_layout.addWidget(self.trace_output, 1, 1)
        download_layout.addWidget(self.btn_trace_browse, 1, 2)
        download_layout.addWidget(self.btn_trace_download, 2, 0, 1, 2)
        download_layout.addWidget(self.btn_trace_cancel, 2, 2)
        download_layout.addWidget(self.trace_progress, 3, 0, 1, 3)
        download_layout.addWidget(self.trace_download_summary, 4, 0, 1, 3)
        download_layout.setColumnStretch(1, 1)
        root.addWidget(download)

        status_group = GroupPanel("Info")
        status_form = QtWidgets.QFormLayout(status_group)
        self.trace_status_labels: dict[str, QtWidgets.QLabel] = {}
        for key, label in (
            ("frequency", "Sample Frequency"),
            ("status", "Status"),
            ("maximum", "Max Trace Number"),
            ("saved", "Saved Trace Num"),
            ("logged", "Traced Sample Num"),
            ("error", "Traced Error"),
            ("trace_time", "Trace Time [sec]"),
            ("delay_time", "Delay Time [sec]"),
        ):
            value = QtWidgets.QLabel("—")
            value.setStyleSheet(
                "font-weight:700; color:#254f68; background:#ffffff;"
                " border:1px solid #bdced7; padding:2px 6px;"
            )
            self.trace_status_labels[key] = value
            status_form.addRow(label, value)
        self.btn_internal_stop = FlatPush("Stop internal trace")
        status_form.addRow(self.btn_internal_stop)
        root.addWidget(status_group)
        root.addStretch(1)

        self.btn_internal_read.clicked.connect(self.read_internal)
        self.btn_internal_apply.clicked.connect(self.apply_internal)
        self.btn_internal_stop.clicked.connect(self.stop_internal)
        self.event_selector.ioSignalChanged.connect(self._event_signal_changed)
        self.event_threshold.editingFinished.connect(self._write_event_signal)
        self.event_trigger_samples.editingFinished.connect(self._write_event_signal)
        self.btn_trace_browse.clicked.connect(self.browse_trace_output)
        self.btn_trace_download.clicked.connect(self.download_trace)
        self.btn_trace_cancel.clicked.connect(self.cancel_trace_download)
        self.trace_selector.currentIndexChanged.connect(self._trace_selection_changed)
        return page

    def _build_file_tab(self) -> QtWidgets.QWidget:
        page = GroupPanel("File Logging Setting")
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(8, 10, 8, 8)
        root.setSpacing(8)
        settings = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(settings)
        form.setContentsMargins(0, 0, 0, 0)
        self.file_signal_count = QtWidgets.QSpinBox()
        self.file_signal_count.setRange(1, 40)
        self.file_signal_count.setValue(3)
        self.file_interval = QtWidgets.QSpinBox()
        self.file_interval.setRange(10, 3_600_000)
        self.file_interval.setValue(500)
        self.file_start_after = QtWidgets.QDoubleSpinBox()
        self.file_start_after.setRange(0, 100000)
        self.file_start_after.setDecimals(4)
        self.file_start_after.setValue(0.01)
        self.file_duration = QtWidgets.QDoubleSpinBox()
        self.file_duration.setRange(0.0001, 100000)
        self.file_duration.setDecimals(4)
        self.file_duration.setValue(1.0)
        self.file_continuous = QtWidgets.QCheckBox("Continuous until Stop")
        self.file_delimiter = QtWidgets.QComboBox()
        self.file_delimiter.addItem("Comma (CSV)", ",")
        self.file_delimiter.addItem("Semicolon", ";")
        self.file_delimiter.addItem("Tab (TSV)", "\t")
        self.file_delimiter.addItem("Space", " ")
        default_path = Path.cwd() / "logs" / datetime.now().strftime("samba_log_%Y%m%d_%H%M%S.csv")
        self.file_output = QtWidgets.QLineEdit(str(default_path))
        self.file_output.setMinimumWidth(0)
        self.file_output.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed
        )
        self.btn_file_browse = FlatPush("Browse…")
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(4)
        output_layout.addWidget(self.file_output, 1)
        output_layout.addWidget(self.btn_file_browse)
        form.addRow("Signal Num", self.file_signal_count)
        form.addRow("Log Duration [h]", self.file_duration)
        form.addRow("Start after [h]", self.file_start_after)
        form.addRow("Update Rate [ms]", self.file_interval)
        form.addRow("Delimiter", self.file_delimiter)
        form.addRow("Mode", self.file_continuous)
        form.addRow("Output file", output_row)
        root.addWidget(settings)

        controls = QtWidgets.QHBoxLayout()
        self.btn_file_check = FlatPush("Check rate")
        self.btn_file_stop = FlatPush("Stop")
        self.btn_file_stop.setEnabled(False)
        self.file_rate_result = QtWidgets.QLabel("Serial rate not checked")
        controls.addWidget(self.btn_file_check)
        controls.addWidget(self.btn_file_stop)
        root.addLayout(controls)
        self.file_rate_result.setWordWrap(True)
        self.file_rate_result.setStyleSheet("color:#607785;")
        root.addWidget(self.file_rate_result)

        status = GroupPanel("Acquisition Status")
        status_form = QtWidgets.QFormLayout(status)
        self.file_state = QtWidgets.QLabel("Idle")
        self.file_samples = QtWidgets.QLabel("0")
        self.file_elapsed = QtWidgets.QLabel("0 s")
        self.file_actual_interval = QtWidgets.QLabel("—")
        self.file_late = QtWidgets.QLabel("0")
        self.file_message = QtWidgets.QLabel("—")
        self.file_message.setWordWrap(True)
        for caption, widget in (
            ("State", self.file_state),
            ("Samples", self.file_samples),
            ("Elapsed", self.file_elapsed),
            ("Actual interval", self.file_actual_interval),
            ("Late samples", self.file_late),
        ):
            widget.setStyleSheet("font-size:16px; font-weight:700; color:#25516a;")
            status_form.addRow(caption, widget)
        status_form.addRow("Message", self.file_message)
        root.addWidget(status)
        note = QtWidgets.QLabel(
            "Samples are flushed to disk immediately. Completion, timing and errors are "
            "recorded in the matching .meta.json file."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#607785; padding:8px;")
        root.addWidget(note)
        root.addStretch(1)
        self.file_continuous.toggled.connect(lambda checked: self.file_duration.setEnabled(not checked))
        self.btn_file_browse.clicked.connect(self.browse_file_output)
        self.btn_file_check.clicked.connect(self.check_file_rate)
        self.btn_file_stop.clicked.connect(self.stop_file_logging)
        return page

    def _build_analysis_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        group = GroupPanel("Analysis filter logging (L*)")
        form = QtWidgets.QFormLayout(group)
        self.analysis_params = SciEdit()
        self.analysis_input = SciEdit()
        self.analysis_out = SciEdit()
        self.analysis_out.setReadOnly(True)
        self.analysis_events = SciEdit()
        self.analysis_events.setReadOnly(True)
        form.addRow("Parameters (LGANP)", self.analysis_params)
        form.addRow("Input signal (LGAIS)", self.analysis_input)
        form.addRow("Filter outputs (LGAFO)", self.analysis_out)
        form.addRow("Events (LGAEV)", self.analysis_events)
        buttons = QtWidgets.QHBoxLayout()
        read = FlatPush("Read analysis")
        write = FlatPush("Apply analysis")
        read.clicked.connect(self.host.on_analysis_read)
        write.clicked.connect(self.host.on_analysis_write)
        buttons.addWidget(read)
        buttons.addWidget(write)
        buttons.addStretch(1)
        form.addRow(buttons)
        root.addWidget(group)
        root.addStretch(1)
        self.analysis_logging_group = group
        return page

    def _install_compatibility_fields(self) -> None:
        """Keep the previous handler/test API while the new page owns the UI."""

        hidden = QtWidgets.QWidget(self)
        hidden.hide()
        self.log_params = SciEdit(parent=hidden)
        self.log_info = SciEdit(parent=hidden)
        self.log_event = SciEdit(parent=hidden)
        self.log_mon_num = QtWidgets.QSpinBox(hidden)
        self.log_mon_num.setRange(0, 39)
        self.log_mon_sig = SciEdit(parent=hidden)
        self.log_live = SciEdit(parent=hidden)
        self.log_trace_num = QtWidgets.QSpinBox(hidden)
        self.log_trace_num.setRange(0, 100)
        self.log_event_time = SciEdit(parent=hidden)
        self.log_data = self.trace_download_summary
        aliases = (
            "log_params", "log_info", "log_event", "log_mon_num", "log_mon_sig",
            "log_live", "log_trace_num", "log_event_time", "log_data",
            "analysis_params", "analysis_input", "analysis_out", "analysis_events",
            "analysis_logging_group",
        )
        for name in aliases:
            setattr(self.host, name, getattr(self, name))

    def _session(self):
        return self.host._require_session()

    def _is_connected(self) -> bool:
        session = getattr(self.host, "session", None)
        return bool(session is not None and getattr(session, "connected", False))

    def _submit(
        self,
        label: str,
        work: Callable[[], Any],
        done: Callable[[Any], None] | None = None,
    ) -> None:
        if self._shutdown:
            return
        self._task_counter += 1
        task_id = self._task_counter
        self._task_callbacks[task_id] = done
        self._task_generations[task_id] = self._generation
        self.page_status.setText(label + "…")

        def target() -> None:
            result: Any = None
            error: BaseException | None = None
            try:
                result = work()
            except BaseException as exc:
                error = exc
            self.bridge.task_finished.emit(task_id, label, result, error)

        thread = threading.Thread(target=target, name=f"SambaLogging-{task_id}", daemon=True)
        self._task_threads.add(thread)
        thread.start()

    @QtCore.Slot(int, str, object, object)
    def _on_task_finished(self, task_id: int, label: str, result: Any, error: Any) -> None:
        self._task_threads = {thread for thread in self._task_threads if thread.is_alive()}
        callback = self._task_callbacks.pop(task_id, None)
        generation = self._task_generations.pop(task_id, -1)
        if label == "Read monitor definitions":
            self._definitions_loading = False
        if generation != self._generation:
            return
        if error is not None:
            if label == "Read monitor definitions" and self._pending_file_start:
                self._pending_file_start = False
                self.btn_file_start.setEnabled(True)
                self.btn_file_stop.setEnabled(False)
                self.file_state.setText("Error")
            if label == "Download controller trace":
                self.btn_trace_download.setEnabled(True)
                self.btn_trace_cancel.setEnabled(False)
            self.page_status.setText("Error")
            self.host.log_msg(f"ERROR {label}: {error}")
            if not self._shutdown:
                QtWidgets.QMessageBox.critical(self, label, str(error))
            return
        self.page_status.setText("Ready")
        self.host.log_msg(f"{label} complete")
        if callback:
            callback(result)

    def _set_monitor_row(
        self, channel: int, tokens: tuple[int, int, int], live: float | None
    ) -> None:
        if not 0 <= channel < 40:
            return
        visual_row = channel % 20
        base_column = 0 if channel < 20 else 3
        name = IOSignalButton.format_io_signal(tokens)
        signal_button = self.monitor_signal_buttons[channel]
        signal_button.setText(name)
        signal_button.setToolTip(
            f"IOSignal Type={tokens[0]}, MainIndex={tokens[1]}, SubIndex={tokens[2]}"
        )
        if live is not None:
            item = self.monitor_table.item(visual_row, base_column + 2)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                item.setTextAlignment(
                    QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                )
                self.monitor_table.setItem(visual_row, base_column + 2, item)
            item.setText(format_ui_number(live))

    def _select_monitor_channel(self, channel: int) -> None:
        if not 0 <= channel < 40:
            return
        self.monitor_number.blockSignals(True)
        self.monitor_number.setValue(channel + 1)
        self.monitor_number.blockSignals(False)
        tokens = self.monitor_definitions[channel]
        self.monitor_selector.set_io_signal(tokens)
        self.monitor_raw.setText(f"Type {tokens[0]} · Main {tokens[1]} · Sub {tokens[2]}")
        self.log_mon_num.setValue(channel)
        self.log_mon_sig.setText(" ".join(str(value) for value in tokens))

    def _monitor_row_changed(self, row: int, column: int, *_args) -> None:
        if not 0 <= row < 20 or column < 0:
            return
        channel = row + (20 if column >= 3 else 0)
        self._select_monitor_channel(channel)

    def _monitor_number_changed(self, number: int) -> None:
        channel = number - 1
        if not 0 <= channel < 40:
            return
        visual_row = channel % 20
        signal_column = 1 if channel < 20 else 4
        self.monitor_table.setCurrentCell(visual_row, signal_column)
        self._select_monitor_channel(channel)

    def _open_monitor_signal_menu(
        self, channel: int, source: QtWidgets.QWidget
    ) -> None:
        if not 0 <= channel < 40:
            return
        visual_row = channel % 20
        signal_column = 1 if channel < 20 else 4
        self.monitor_table.setCurrentCell(visual_row, signal_column)
        self._select_monitor_channel(channel)
        menu = self.monitor_selector.menu()
        if menu is not None:
            menu.popup(source.mapToGlobal(QtCore.QPoint(0, source.height())))

    def _monitor_selector_changed(self, tokens: Any) -> None:
        row = self.monitor_number.value() - 1
        values = tuple(int(value) for value in tokens[:3])
        self.monitor_definitions[row] = values
        self.monitor_names[row] = IOSignalButton.format_io_signal(values)
        self._set_monitor_row(row, values, None)
        self.monitor_raw.setText(f"Type {values[0]} · Main {values[1]} · Sub {values[2]}")
        self.log_mon_sig.setText(" ".join(str(value) for value in values))
        self._write_monitor_channel(row, values)

    def read_monitor_definitions(self) -> None:
        if self._definitions_loading:
            return
        self._definitions_loading = True

        def work():
            session = self._session()
            reader = getattr(session, "get_monitor_page_snapshot", None)
            if callable(reader):
                rows = reader(40)["signals"]
            else:
                rows = [session.get_monitor_signal(index) for index in range(40)]
            definitions = []
            for row in rows:
                values = [int(value) for value in row[:3]]
                values.extend([0] * (3 - len(values)))
                definitions.append(tuple(values[:3]))
            return definitions

        def done(definitions):
            for row, tokens in enumerate(definitions):
                self.monitor_definitions[row] = tokens
                self.monitor_names[row] = IOSignalButton.format_io_signal(tokens)
                self._set_monitor_row(row, tokens, None)
            self._definitions_loaded = True
            self._select_monitor_channel(self.monitor_number.value() - 1)
            if self._pending_file_start:
                self._pending_file_start = False
                self.start_file_logging()

        self._submit("Read monitor definitions", work, done)

    def read_live_values(self) -> None:
        count = max(self.monitor_used.value(), self.file_signal_count.value())

        def done(values):
            for row, value in enumerate(values[:40]):
                self._set_monitor_row(row, self.monitor_definitions[row], float(value))
            self.log_live.setText(" ".join(format_ui_number(value) for value in values))

        self._submit(
            "Read live monitor values",
            lambda: self._session().get_monitor_values(0, count - 1),
            done,
        )

    def write_selected_monitor(self) -> None:
        row = self.monitor_number.value() - 1
        self._write_monitor_channel(row, self.monitor_selector.io_tokens())

    def _write_monitor_channel(
        self, row: int, tokens: tuple[int, int, int]
    ) -> None:
        """Write one DSMOS definition immediately, as the legacy buttons do."""

        if not 0 <= row < 40:
            return
        values = tuple(int(value) for value in tokens[:3])
        if not self._is_connected():
            return

        def work():
            self.host._set_writable(True)
            self._session().set_monitor_signal(row, *values)
            return tuple(int(value) for value in self._session().get_monitor_signal(row)[:3])

        def done(readback):
            self.monitor_definitions[row] = readback
            self.monitor_names[row] = IOSignalButton.format_io_signal(readback)
            self._set_monitor_row(row, readback, None)
            if self.monitor_number.value() - 1 == row:
                self.monitor_selector.set_io_signal(readback)
                self.monitor_raw.setText(
                    f"Type {readback[0]} · Main {readback[1]} · Sub {readback[2]}"
                )
            self.log_mon_num.setValue(row)
            self.log_mon_sig.setText(" ".join(str(value) for value in readback))

        self._submit(f"Apply monitor signal {row + 1}", work, done)

    def update_workspace(self) -> None:
        """Refresh the three visible logging columns in one serialized task."""

        trace_number = int(self.trace_selector.currentData() or 0)
        monitor_count = max(self.monitor_used.value(), self.file_signal_count.value())

        def work():
            session = self._session()
            reader = getattr(session, "get_logging_workspace_snapshot", None)
            if callable(reader):
                snapshot = reader(monitor_count)
                raw_definitions = snapshot["signals"]
                live_values = snapshot["values"]
                params = snapshot["params"]
                info = snapshot["info"]
                event = snapshot["event"]
                sample_frequency = snapshot["sample_frequency"]
            else:
                raw_definitions = [
                    session.get_monitor_signal(index) for index in range(40)
                ]
                live_values = session.get_monitor_values(0, monitor_count - 1)
                params = session.get_event_trace_params()
                info = session.get_event_trace_info()
                event = session.get_event_signal()
                sample_frequency = session.get_sample_frequency()
            definitions: list[tuple[int, int, int]] = []
            for row in raw_definitions:
                values = [int(value) for value in row[:3]]
                values.extend([0] * (3 - len(values)))
                definitions.append(tuple(values[:3]))
            event_time = (
                session.get_event_time(trace_number)
                if len(info) > 2 and _protocol_int(info[2]) > trace_number
                else []
            )
            return (
                definitions,
                live_values,
                params,
                info,
                event,
                event_time,
                sample_frequency,
            )

        def done(payload):
            definitions, live_values, params, info, event, event_time, frequency = payload
            for channel, tokens in enumerate(definitions):
                self.monitor_definitions[channel] = tokens
                self.monitor_names[channel] = IOSignalButton.format_io_signal(tokens)
                self._set_monitor_row(channel, tokens, None)
            for channel, value in enumerate(live_values[:40]):
                self._set_monitor_row(
                    channel, self.monitor_definitions[channel], float(value)
                )
            self.log_live.setText(
                " ".join(format_ui_number(value) for value in live_values)
            )
            self._definitions_loaded = True
            self._select_monitor_channel(self.monitor_number.value() - 1)
            self._apply_internal_readback(
                (params, info, event, event_time, frequency)
            )

        self._submit("Update logging workspace", work, done)

    def read_internal(self) -> None:
        trace_number = int(self.trace_selector.currentData() or 0)

        def work():
            session = self._session()
            reader = getattr(session, "get_internal_logging_snapshot", None)
            if callable(reader):
                snapshot = reader()
                params = snapshot["params"]
                info = snapshot["info"]
                event = snapshot["event"]
                sample_frequency = snapshot["sample_frequency"]
            else:
                params = session.get_event_trace_params()
                info = session.get_event_trace_info()
                event = session.get_event_signal()
                sample_frequency = session.get_sample_frequency()
            event_time = session.get_event_time(trace_number) if len(info) > 2 and _protocol_int(info[2]) > trace_number else []
            return params, info, event, event_time, sample_frequency

        self._submit("Read internal trace", work, self._apply_internal_readback)

    def _apply_internal_readback(self, payload) -> None:
        params, info, event, event_time = payload[:4]
        sample_frequency = float(payload[4]) if len(payload) > 4 else 0.0
        if params:
            mode_index = self.logging_type_combo.findData(_protocol_int(params[0]))
            if mode_index >= 0:
                self.logging_type_combo.setCurrentIndex(mode_index)
        for spin, param_index in (
            (self.internal_samples, 1),
            (self.internal_signal_count, 2),
            (self.internal_undersample, 3),
            (self.internal_delay_samples, 4),
        ):
            if len(params) > param_index:
                spin.setValue(_protocol_int(params[param_index], spin.value()))
        if len(params) > 5:
            self.internal_average.setChecked(bool(_protocol_int(params[5])))
        if len(params) > 2:
            self.monitor_used.setValue(max(1, min(40, _protocol_int(params[2], 1))))
        if len(event) >= 3:
            tokens = tuple(_protocol_int(value) for value in event[:3])
            self.event_selector.set_io_signal(tokens)
        if len(event) > 3:
            self.event_threshold.setValue(float(event[3]))
        if len(event) > 4:
            self.event_trigger_samples.setValue(_protocol_int(event[4]))
        self._set_trace_info(info)
        self.trace_event_time.setText(
            "Event time: " + (" ".join(str(value) for value in event_time) if event_time else "—")
        )
        self.trace_status_labels["frequency"].setText(
            f"{format_ui_number(sample_frequency)} Hz" if sample_frequency else "—"
        )
        sample_count = max(0, _protocol_int(params[1])) if len(params) > 1 else 0
        under_sample = max(1, _protocol_int(params[3], 1)) if len(params) > 3 else 1
        delay_samples = max(0, _protocol_int(params[4])) if len(params) > 4 else 0
        if sample_frequency:
            self.trace_status_labels["trace_time"].setText(
                format_ui_number(sample_count * under_sample / sample_frequency)
            )
            self.trace_status_labels["delay_time"].setText(
                format_ui_number(delay_samples / sample_frequency)
            )
        else:
            self.trace_status_labels["trace_time"].setText("—")
            self.trace_status_labels["delay_time"].setText("—")
        self.log_params.setText(" ".join(str(value) for value in params))
        self.log_info.setText(" ".join(str(value) for value in info))
        self.log_event.setText(" ".join(str(value) for value in event))
        self.log_event_time.setText(" ".join(str(value) for value in event_time))

    def _set_trace_info(self, info: list[str]) -> None:
        values = [_protocol_int(value) for value in info]
        while len(values) < 5:
            values.append(0)
        status, maximum, saved, error, logged = values[:5]
        self.trace_status_labels["status"].setText("Running" if status else "Stopped")
        self.trace_status_labels["maximum"].setText(str(maximum))
        self.trace_status_labels["saved"].setText(str(saved))
        self.trace_status_labels["error"].setText(str(error))
        self.trace_status_labels["logged"].setText(str(logged))
        current = int(self.trace_selector.currentData() or 0)
        self.trace_selector.blockSignals(True)
        self.trace_selector.clear()
        for trace in range(saved):
            self.trace_selector.addItem(f"Trace {trace}", trace)
        if saved == 0:
            self.trace_selector.addItem("No saved traces", 0)
        else:
            self.trace_selector.setCurrentIndex(min(current, saved - 1))
        self.trace_selector.blockSignals(False)

    def apply_internal(self) -> None:
        params = (
            int(self.logging_type_combo.currentData()),
            self.internal_samples.value(),
            self.internal_signal_count.value(),
            self.internal_undersample.value(),
            self.internal_delay_samples.value(),
            int(self.internal_average.isChecked()),
        )

        def work():
            self.host._set_writable(True)
            session = self._session()
            session.set_event_trace_params(*params)
            return (
                session.get_event_trace_params(),
                session.get_event_trace_info(),
                session.get_event_signal(),
                [],
                session.get_sample_frequency(),
            )

        self._submit("Apply internal trace configuration", work, self._apply_internal_readback)

    def _event_values(self) -> tuple[int, int, int, float, int]:
        tokens = tuple(int(value) for value in self.event_selector.io_tokens())
        return (
            tokens[0],
            tokens[1],
            tokens[2],
            float(self.event_threshold.value()),
            int(self.event_trigger_samples.value()),
        )

    def _event_signal_changed(self, _tokens: Any) -> None:
        self._write_event_signal()

    def _write_event_signal(self) -> None:
        """Apply DSETS immediately when an Event Setting field is committed."""

        values = self._event_values()
        self.log_event.setText(" ".join(format_ui_number(value) for value in values))
        if not self._is_connected():
            return

        def work():
            self.host._set_writable(True)
            session = self._session()
            session.set_event_signal(*values)
            return session.get_event_signal()

        def done(readback):
            if len(readback) >= 3:
                self.event_selector.set_io_signal(
                    tuple(_protocol_int(value) for value in readback[:3])
                )
            if len(readback) > 3:
                self.event_threshold.setValue(float(readback[3]))
            if len(readback) > 4:
                self.event_trigger_samples.setValue(_protocol_int(readback[4]))
            self.log_event.setText(" ".join(str(value) for value in readback))

        self._submit("Apply event signal", work, done)

    def start_internal(self) -> None:
        def checked(info):
            saved = _protocol_int(info[2]) if len(info) > 2 else 0
            if saved:
                answer = QtWidgets.QMessageBox.warning(
                    self,
                    "Start internal trace",
                    f"Starting a new controller trace deletes {saved} saved trace(s). Continue?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                    QtWidgets.QMessageBox.Cancel,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    return

            def work():
                self.host._set_writable(True)
                self._session().start_stop_event_tracing(1)
                return self._session().get_event_trace_info()

            self._submit("Start internal trace", work, self._set_trace_info)

        self._submit("Check saved traces", lambda: self._session().get_event_trace_info(), checked)

    def stop_internal(self) -> None:
        def work():
            self.host._set_writable(True)
            self._session().start_stop_event_tracing(0)
            return self._session().get_event_trace_info()

        self._submit("Stop internal trace", work, self._set_trace_info)

    def browse_trace_output(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save controller trace", self.trace_output.text(), "CSV (*.csv);;All files (*.*)"
        )
        if path:
            self.trace_output.setText(path)

    def _trace_selection_changed(self) -> None:
        trace = int(self.trace_selector.currentData() or 0)
        self.log_trace_num.setValue(trace)
        current = Path(self.trace_output.text())
        if current.name.startswith("controller_trace_"):
            self.trace_output.setText(str(current.with_name(f"controller_trace_{trace}.csv")))

    def download_trace(self) -> None:
        path = self.trace_output.text().strip()
        if not path:
            self.browse_trace_output()
            path = self.trace_output.text().strip()
        if not path:
            return
        trace = int(self.trace_selector.currentData() or 0)
        self._download_cancel.clear()
        self.btn_trace_download.setEnabled(False)
        self.btn_trace_cancel.setEnabled(True)
        self.trace_progress.setValue(0)

        def work():
            session = self._session()
            params = session.get_event_trace_params()
            monitor_count = max(1, _protocol_int(params[2], 1) if len(params) > 2 else 1)
            under_sample = max(1, _protocol_int(params[3], 1) if len(params) > 3 else 1)
            sample_frequency = float(session.get_sample_frequency())
            rows = session.download_logged_trace(
                trace,
                progress_callback=lambda current, total: self.bridge.download_progress.emit(current, total),
                cancel_event=self._download_cancel,
            )
            if self._download_cancel.is_set():
                return None
            names = self.monitor_names[:monitor_count]
            event_time = session.get_event_time(trace)
            output = save_trace_record(
                path,
                rows,
                names,
                sample_interval_s=under_sample / sample_frequency if sample_frequency else 1.0,
                metadata={"trace_number": trace, "event_time": event_time, "trace_parameters": params},
            )
            return output, load_logging_record(output), event_time

        def done(payload):
            if payload is None:
                self.btn_trace_download.setEnabled(True)
                self.btn_trace_cancel.setEnabled(False)
                self.trace_download_summary.setPlainText("Trace download cancelled")
                self.page_status.setText("Ready")
                return
            output, record, event_time = payload
            self.btn_trace_download.setEnabled(True)
            self.btn_trace_cancel.setEnabled(False)
            self.trace_progress.setValue(100)
            self.trace_download_summary.setPlainText(
                f"Saved {len(record.rows)} samples to {output}\nEvent time: {' '.join(event_time)}"
            )
            self.log_event_time.setText(" ".join(event_time))
            self._show_record(record)
            self._show_records_window()

        self._submit("Download controller trace", work, done)

    def cancel_trace_download(self) -> None:
        self._download_cancel.set()
        self.btn_trace_cancel.setEnabled(False)

    @QtCore.Slot(int, int)
    def _on_download_progress(self, current: int, total: int) -> None:
        self.trace_progress.setRange(0, max(1, total))
        self.trace_progress.setValue(current)

    def browse_file_output(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Stream monitor values", self.file_output.text(), "CSV (*.csv);;TSV (*.tsv);;All files (*.*)"
        )
        if path:
            self.file_output.setText(path)

    def _file_config(self) -> FileLoggingConfig:
        path = Path(self.file_output.text().strip())
        count = self.file_signal_count.value()
        duration = None if self.file_continuous.isChecked() else self.file_duration.value() * 3600.0
        return FileLoggingConfig(
            path=path,
            signal_count=count,
            interval_ms=self.file_interval.value(),
            start_after_s=self.file_start_after.value() * 3600.0,
            duration_s=duration,
            delimiter=str(self.file_delimiter.currentData()),
            signal_names=tuple(self.monitor_names[:count]),
        )

    def start_file_logging(self) -> None:
        if not self._definitions_loaded:
            self._pending_file_start = True
            self.btn_file_start.setEnabled(False)
            self.btn_file_stop.setEnabled(True)
            self.file_state.setText("Preparing")
            self.file_message.setText("Reading monitor definitions…")
            self.read_monitor_definitions()
            return
        try:
            config = self._file_config()
            config.validate()
            service = FileLoggingService(self._session())
            service.start(
                config,
                on_sample=lambda stats, values, generation=self._generation: self.bridge.file_sample.emit(
                    stats, values, generation
                ),
                on_finished=lambda stats, error, generation=self._generation: self.bridge.file_finished.emit(
                    stats, error, generation
                ),
            )
        except Exception as exc:
            self.btn_file_start.setEnabled(True)
            self.btn_file_stop.setEnabled(False)
            QtWidgets.QMessageBox.critical(self, "Start file logging", str(exc))
            return
        self.file_service = service
        self._active_file_duration_s = config.duration_s
        self.btn_file_start.setEnabled(False)
        self.btn_file_stop.setEnabled(True)
        self.file_state.setText("Waiting" if config.start_after_s else "Running")
        self.file_toolbar_elapsed.setText("0 s elapsed")
        if config.duration_s is None:
            self.file_toolbar_progress.setRange(0, 0)
        else:
            self.file_toolbar_progress.setRange(0, 100)
            self.file_toolbar_progress.setValue(0)
        self.page_status.setText("File logging active")
        self.host.log_msg(f"File logging scheduled: {config.path}")

    def stop_file_logging(self) -> None:
        if self._pending_file_start:
            self._pending_file_start = False
            self.btn_file_start.setEnabled(True)
            self.btn_file_stop.setEnabled(False)
            self.file_state.setText("Cancelled")
            self.file_message.setText("Start cancelled")
            self.page_status.setText("Ready")
            return
        if self.file_service:
            self.file_service.stop()
            self.btn_file_stop.setEnabled(False)
            self.file_state.setText("Stopping…")
            self.file_message.setText("Stopping after the current controller read…")

    @QtCore.Slot(object, object, int)
    def _on_file_sample(self, stats, values, generation: int) -> None:
        if generation != self._generation:
            return
        self.file_state.setText("Running")
        self.file_samples.setText(str(stats.samples))
        self.file_elapsed.setText(f"{stats.elapsed_s:.2f} s")
        self.file_toolbar_elapsed.setText(f"{stats.elapsed_s:.1f} s elapsed")
        if self._active_file_duration_s:
            progress = min(
                100, int(round(stats.elapsed_s * 100.0 / self._active_file_duration_s))
            )
            self.file_toolbar_progress.setValue(progress)
        self.file_actual_interval.setText(
            f"{stats.actual_interval_ms:.2f} ms" if stats.actual_interval_ms else "—"
        )
        self.file_late.setText(str(stats.late_samples))
        self.file_message.setText(stats.message or "Acquiring")
        for row, value in enumerate(values[:40]):
            self._set_monitor_row(row, self.monitor_definitions[row], float(value))

    @QtCore.Slot(object, object, int)
    def _on_file_finished(self, stats, error, generation: int) -> None:
        if generation != self._generation:
            return
        self.btn_file_start.setEnabled(True)
        self.btn_file_stop.setEnabled(False)
        self.file_state.setText(stats.state.title())
        self.file_samples.setText(str(stats.samples))
        self.file_elapsed.setText(f"{stats.elapsed_s:.2f} s")
        self.file_toolbar_progress.setRange(0, 100)
        if stats.state == "complete":
            self.file_toolbar_progress.setValue(100)
        elif self._active_file_duration_s:
            self.file_toolbar_progress.setValue(
                min(
                    100,
                    int(round(stats.elapsed_s * 100.0 / self._active_file_duration_s)),
                )
            )
        else:
            self.file_toolbar_progress.setValue(0)
        self.file_toolbar_elapsed.setText(f"{stats.elapsed_s:.1f} s elapsed")
        self._active_file_duration_s = None
        self.file_actual_interval.setText(
            f"{stats.actual_interval_ms:.2f} ms" if stats.actual_interval_ms else "—"
        )
        self.file_late.setText(str(stats.late_samples))
        self.file_message.setText(stats.message or stats.output_path)
        self.page_status.setText("Ready")
        self.host.log_msg(
            f"File logging {stats.state}: {stats.samples} samples -> {stats.output_path}"
        )
        if error is not None and not self._shutdown:
            QtWidgets.QMessageBox.critical(self, "File logging", str(error))

    def check_file_rate(self) -> None:
        count = self.file_signal_count.value()

        def work():
            started = time.perf_counter()
            for _ in range(5):
                self._session().get_monitor_values(0, count - 1)
            return (time.perf_counter() - started) * 1000.0 / 5.0

        def done(milliseconds):
            recommended = max(10, int(milliseconds * 1.25 + 0.5))
            self.file_rate_result.setText(
                f"Measured {milliseconds:.2f} ms/read · recommended interval ≥ {recommended} ms"
            )

        self._submit("Check monitor serial rate", work, done)

    def open_record_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.records_window,
            "Open logging record",
            self.record_path.text(),
            "Logging records (*.csv *.tsv *.txt *.LoggRecJson *.LoggRecXml *.LoggRecTxt *.ILogRecJson *.ILogRecXml *.ILogRecTxt *.json *.xml);;All files (*.*)",
        )
        if not path:
            return
        try:
            record = load_logging_record(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.records_window, "Open logging record", str(exc)
            )
            return
        self.record_path.setText(path)
        self._show_record(record)

    def _show_record(self, record: LoggingRecord) -> None:
        self.records_window.set_record(record)

    def refresh(self) -> None:
        """Called by the main page refresh dispatcher."""
        if not self.serial_worker_active:
            if not self._definitions_loaded:
                self.read_monitor_definitions()
            else:
                self.read_internal()

    def on_connected(self) -> None:
        """Re-arm the page after a disconnect/reconnect cycle."""
        self._generation += 1
        self._shutdown = False
        self._download_cancel.clear()
        self.file_service = None
        self._active_file_duration_s = None
        self._definitions_loaded = False
        self._definitions_loading = False
        self._pending_file_start = False
        self.btn_file_start.setEnabled(True)
        self.btn_file_stop.setEnabled(False)
        self.btn_trace_download.setEnabled(True)
        self.btn_trace_cancel.setEnabled(False)
        self.file_toolbar_progress.setRange(0, 100)
        self.file_toolbar_progress.setValue(0)
        self.file_toolbar_elapsed.setText("0 s elapsed")
        self.page_status.setText("Ready")

    def shutdown(self, *, timeout: float = 5.0) -> bool:
        """Cancel logging work and drain controller tasks before session close.

        ControllerSession transports serialize each request, so closing the
        shared session while one of these workers still owns an exchange can
        race the underlying socket/COM handle.  Trace and file workers receive
        their normal cancellation signals first; short parameter reads are
        then allowed to finish within the bounded disconnect grace period.
        """
        self._generation += 1
        self._shutdown = True
        self._definitions_loading = False
        self._pending_file_start = False
        self._download_cancel.set()
        self.records_window.hide()
        if self.file_service:
            self.file_service.stop(wait=True, timeout=1.0)
        deadline = time.monotonic() + max(0.0, float(timeout))
        current = threading.current_thread()
        for thread in tuple(self._task_threads):
            if thread is current or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            thread.join(remaining)
        self._task_threads = {
            thread for thread in self._task_threads if thread.is_alive()
        }
        return not self._task_threads
