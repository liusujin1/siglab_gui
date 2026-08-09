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


class TracePreview(QtWidgets.QWidget):
    """Small dependency-free line preview for loaded records."""

    COLORS = ("#1875a6", "#dc6b2f", "#3f9b55", "#8a5ab7", "#c43b62", "#6d7a86")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._series: list[tuple[str, list[tuple[float, float]]]] = []
        self.setToolTip("Preview is decimated to keep large records responsive.")

    def set_record(self, record: LoggingRecord) -> None:
        self._series = []
        if not record.headers or not record.rows:
            self.update()
            return
        lower = [header.lower() for header in record.headers]
        x_index = lower.index("elapsed_s") if "elapsed_s" in lower else -1
        excluded = {x_index}
        if "timestamp_utc" in lower:
            excluded.add(lower.index("timestamp_utc"))
        candidates = [index for index in range(len(record.headers)) if index not in excluded]
        step = max(1, len(record.rows) // 1800)
        for column in candidates[:6]:
            points: list[tuple[float, float]] = []
            for row_number in range(0, len(record.rows), step):
                row = record.rows[row_number]
                if column >= len(row):
                    continue
                try:
                    x_value = float(row[x_index]) if x_index >= 0 and x_index < len(row) else float(row_number)
                    y_value = float(row[column])
                except (TypeError, ValueError):
                    continue
                points.append((x_value, y_value))
            if points:
                self._series.append((record.headers[column], points))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#fbfdfe"))
        plot = self.rect().adjusted(52, 16, -18, -38)
        painter.setPen(QtGui.QPen(QtGui.QColor("#9db6c5"), 1))
        painter.drawRect(plot)
        if not self._series or plot.width() <= 0 or plot.height() <= 0:
            painter.setPen(QtGui.QColor("#6c7e89"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Load a record to preview")
            return
        xs = [point[0] for _, points in self._series for point in points]
        ys = [point[1] for _, points in self._series for point in points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if x_max == x_min:
            x_max = x_min + 1.0
        if y_max == y_min:
            y_max = y_min + 1.0
        painter.setPen(QtGui.QColor("#607987"))
        painter.drawText(4, plot.top() + 6, format_ui_number(y_max))
        painter.drawText(4, plot.bottom(), format_ui_number(y_min))
        painter.drawText(plot.left(), self.height() - 12, format_ui_number(x_min))
        painter.drawText(plot.right() - 55, self.height() - 12, format_ui_number(x_max))
        for series_index, (name, points) in enumerate(self._series):
            color = QtGui.QColor(self.COLORS[series_index % len(self.COLORS)])
            painter.setPen(QtGui.QPen(color, 1.4))
            path = QtGui.QPainterPath()
            for index, (x_value, y_value) in enumerate(points):
                x_pos = plot.left() + (x_value - x_min) * plot.width() / (x_max - x_min)
                y_pos = plot.bottom() - (y_value - y_min) * plot.height() / (y_max - y_min)
                if index == 0:
                    path.moveTo(x_pos, y_pos)
                else:
                    path.lineTo(x_pos, y_pos)
            painter.drawPath(path)
            painter.drawText(plot.left() + series_index * 130, self.height() - 12, name[:18])


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
        self.monitor_definitions: list[tuple[int, int, int]] = [(0, index, 0) for index in range(40)]
        self.monitor_names: list[str] = [
            IOSignalButton.format_io_signal(tokens) for tokens in self.monitor_definitions
        ]
        self._definitions_loaded = False
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
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(8)
        banner = QtWidgets.QFrame()
        banner.setObjectName("loggingBanner")
        banner.setStyleSheet(
            "QFrame#loggingBanner { background:#e7f3f9; border:1px solid #a9c7d7;"
            " border-radius:8px; }"
        )
        banner_layout = QtWidgets.QHBoxLayout(banner)
        title = QtWidgets.QLabel("Controller Logging & Trace Acquisition")
        title.setStyleSheet("font-size:18px; font-weight:700; color:#234c64;")
        subtitle = QtWidgets.QLabel(
            "40 monitor channels · internal event traces · streamed host files"
        )
        subtitle.setStyleSheet("color:#5c7483;")
        subtitle.setWordWrap(False)
        subtitle.setMinimumWidth(240)
        subtitle.setMaximumWidth(650)
        subtitle.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        self.page_status = QtWidgets.QLabel("Ready")
        self.page_status.setStyleSheet(
            "background:#ffffff; border:1px solid #aac0cc; border-radius:10px;"
            " padding:4px 12px; color:#31566c;"
        )
        banner_layout.addWidget(title)
        banner_layout.addSpacing(12)
        banner_layout.addWidget(subtitle)
        banner_layout.addStretch(1)
        banner_layout.addWidget(self.page_status)
        root.addWidget(banner)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("loggingWorkspaceTabs")
        self.tabs.addTab(self._build_monitor_tab(), "Monitor Signals")
        self.tabs.addTab(self._build_internal_tab(), "Internal Trace")
        self.tabs.addTab(self._build_file_tab(), "File Logging")
        self.tabs.addTab(self._build_records_tab(), "Records / Plot")
        self.tabs.addTab(self._build_analysis_tab(), "Analysis Filter")
        root.addWidget(self.tabs, 1)

    def _build_monitor_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        controls = QtWidgets.QHBoxLayout()
        self.monitor_used = QtWidgets.QSpinBox()
        self.monitor_used.setRange(1, 40)
        self.monitor_used.setValue(3)
        self.monitor_used.setSuffix(" channels")
        self.btn_monitor_defs = FlatPush("Read all definitions")
        self.btn_monitor_live = FlatPush("Read live values")
        controls.addWidget(QtWidgets.QLabel("Signals used"))
        controls.addWidget(self.monitor_used)
        controls.addSpacing(18)
        controls.addWidget(self.btn_monitor_defs)
        controls.addWidget(self.btn_monitor_live)
        controls.addStretch(1)
        root.addLayout(controls)

        self.monitor_table = QtWidgets.QTableWidget(40, 6)
        self.monitor_table.setHorizontalHeaderLabels(
            ["#", "Signal", "Type", "Main", "Sub", "Live value"]
        )
        self.monitor_table.setAlternatingRowColors(True)
        self.monitor_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.monitor_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.monitor_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.monitor_table.verticalHeader().setVisible(False)
        self.monitor_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for column in (0, 2, 3, 4, 5):
            self.monitor_table.horizontalHeader().setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeToContents
            )
        for row in range(40):
            self._set_monitor_row(row, self.monitor_definitions[row], None)
        self.monitor_table.selectRow(0)
        root.addWidget(self.monitor_table, 1)

        editor = GroupPanel("Selected monitor channel")
        edit_layout = QtWidgets.QHBoxLayout(editor)
        self.monitor_number = QtWidgets.QSpinBox()
        self.monitor_number.setRange(0, 39)
        self.monitor_selector = IOSignalButton(tokens=self.monitor_definitions[0])
        self.monitor_selector.setMinimumWidth(260)
        self.monitor_raw = QtWidgets.QLabel("Type 0 · Main 0 · Sub 0")
        self.btn_monitor_write = FlatPush("Apply selected channel")
        edit_layout.addWidget(QtWidgets.QLabel("Channel"))
        edit_layout.addWidget(self.monitor_number)
        edit_layout.addWidget(self.monitor_selector, 1)
        edit_layout.addWidget(self.monitor_raw)
        edit_layout.addWidget(self.btn_monitor_write)
        root.addWidget(editor)
        self.monitor_table.currentCellChanged.connect(self._monitor_row_changed)
        self.monitor_number.valueChanged.connect(self._monitor_number_changed)
        self.monitor_selector.ioSignalChanged.connect(self._monitor_selector_changed)
        self.btn_monitor_defs.clicked.connect(self.read_monitor_definitions)
        self.btn_monitor_live.clicked.connect(self.read_live_values)
        self.btn_monitor_write.clicked.connect(self.write_selected_monitor)
        return page

    def _build_internal_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        top = QtWidgets.QGridLayout()
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)
        params_group = GroupPanel("Internal trace parameters (DGETP / DSETP)")
        params_form = QtWidgets.QFormLayout(params_group)
        specs = (
            ("Trace mode", 0, 3, 0),
            ("Samples", 1, 0x20000, 1024),
            ("Monitor signals", 1, 40, 3),
            ("Under-sampling", 1, 100000, 1),
            ("Delay samples", 0, 0x7FFFFFFF, 1),
            ("Average", 0, 1, 0),
        )
        self.internal_param_spins: list[QtWidgets.QSpinBox] = []
        for label, minimum, maximum, value in specs:
            spin = QtWidgets.QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setValue(value)
            spin.setGroupSeparatorShown(True)
            self.internal_param_spins.append(spin)
            params_form.addRow(label, spin)
        top.addWidget(params_group, 0, 0)

        event_group = GroupPanel("Event signal (DGETS / DSETS)")
        event_form = QtWidgets.QFormLayout(event_group)
        self.event_selector = IOSignalButton(tokens=(0, 0, 0))
        self.event_selector.setMinimumWidth(260)
        self.event_threshold = QtWidgets.QDoubleSpinBox()
        self.event_threshold.setRange(-1e12, 1e12)
        self.event_threshold.setDecimals(8)
        self.event_threshold.setGroupSeparatorShown(True)
        self.event_trigger_samples = QtWidgets.QSpinBox()
        self.event_trigger_samples.setRange(0, 0x7FFFFFFF)
        event_form.addRow("Signal", self.event_selector)
        event_form.addRow("Threshold", self.event_threshold)
        event_form.addRow("Trigger samples", self.event_trigger_samples)
        top.addWidget(event_group, 0, 1)

        status_group = GroupPanel("Trace status (DGETI)")
        status_form = QtWidgets.QHBoxLayout(status_group)
        self.trace_status_labels: dict[str, QtWidgets.QLabel] = {}
        for key, label in (
            ("status", "Status"),
            ("maximum", "Maximum traces"),
            ("saved", "Saved traces"),
            ("error", "Error"),
            ("logged", "Samples logged"),
        ):
            value = QtWidgets.QLabel("—")
            value.setStyleSheet("font-weight:700; color:#254f68;")
            self.trace_status_labels[key] = value
            metric = QtWidgets.QWidget()
            metric_layout = QtWidgets.QVBoxLayout(metric)
            metric_layout.setContentsMargins(8, 2, 8, 2)
            metric_layout.addWidget(QtWidgets.QLabel(label))
            metric_layout.addWidget(value)
            status_form.addWidget(metric, 1)
        top.addWidget(status_group, 1, 0, 1, 2)
        root.addLayout(top)

        action = QtWidgets.QHBoxLayout()
        self.btn_internal_read = FlatPush("Read controller")
        self.btn_internal_apply = FlatPush("Apply configuration")
        self.btn_internal_start = FlatPush("Start internal trace")
        self.btn_internal_stop = FlatPush("Stop trace")
        action.addWidget(self.btn_internal_read)
        action.addWidget(self.btn_internal_apply)
        action.addSpacing(18)
        action.addWidget(self.btn_internal_start)
        action.addWidget(self.btn_internal_stop)
        action.addStretch(1)
        root.addLayout(action)

        download = GroupPanel("Saved controller traces")
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
        download_layout.addWidget(QtWidgets.QLabel("Saved trace"), 0, 0)
        download_layout.addWidget(self.trace_selector, 0, 1)
        download_layout.addWidget(self.trace_event_time, 0, 2, 1, 2)
        download_layout.addWidget(QtWidgets.QLabel("Output file"), 1, 0)
        download_layout.addWidget(self.trace_output, 1, 1, 1, 2)
        download_layout.addWidget(self.btn_trace_browse, 1, 3)
        download_layout.addWidget(self.btn_trace_download, 2, 1)
        download_layout.addWidget(self.btn_trace_cancel, 2, 2)
        download_layout.addWidget(self.trace_progress, 2, 3)
        download_layout.addWidget(self.trace_download_summary, 3, 0, 1, 4)
        root.addWidget(download)
        root.addStretch(1)

        self.btn_internal_read.clicked.connect(self.read_internal)
        self.btn_internal_apply.clicked.connect(self.apply_internal)
        self.btn_internal_start.clicked.connect(self.start_internal)
        self.btn_internal_stop.clicked.connect(self.stop_internal)
        self.btn_trace_browse.clicked.connect(self.browse_trace_output)
        self.btn_trace_download.clicked.connect(self.download_trace)
        self.btn_trace_cancel.clicked.connect(self.cancel_trace_download)
        self.trace_selector.currentIndexChanged.connect(self._trace_selection_changed)
        return page

    def _build_file_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        config = GroupPanel("Host file logging")
        grid = QtWidgets.QGridLayout(config)
        self.file_signal_count = QtWidgets.QSpinBox()
        self.file_signal_count.setRange(1, 40)
        self.file_signal_count.setValue(3)
        self.file_interval = QtWidgets.QSpinBox()
        self.file_interval.setRange(10, 3_600_000)
        self.file_interval.setValue(500)
        self.file_interval.setSuffix(" ms")
        self.file_start_after = QtWidgets.QDoubleSpinBox()
        self.file_start_after.setRange(0, 100000)
        self.file_start_after.setDecimals(4)
        self.file_start_after.setValue(0.01)
        self.file_start_after.setSuffix(" h")
        self.file_duration = QtWidgets.QDoubleSpinBox()
        self.file_duration.setRange(0.0001, 100000)
        self.file_duration.setDecimals(4)
        self.file_duration.setValue(1.0)
        self.file_duration.setSuffix(" h")
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
        grid.addWidget(QtWidgets.QLabel("Monitor signals"), 0, 0)
        grid.addWidget(self.file_signal_count, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Polling interval"), 0, 2)
        grid.addWidget(self.file_interval, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Start after"), 1, 0)
        grid.addWidget(self.file_start_after, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Duration"), 1, 2)
        grid.addWidget(self.file_duration, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Delimiter"), 2, 0)
        grid.addWidget(self.file_delimiter, 2, 1)
        grid.addWidget(self.file_continuous, 2, 2, 1, 2)
        grid.addWidget(QtWidgets.QLabel("Output file"), 3, 0)
        grid.addWidget(self.file_output, 3, 1, 1, 2)
        grid.addWidget(self.btn_file_browse, 3, 3)
        root.addWidget(config)

        controls = QtWidgets.QHBoxLayout()
        self.btn_file_check = FlatPush("Check serial rate")
        self.btn_file_start = FlatPush("Start logging")
        self.btn_file_stop = FlatPush("Stop")
        self.btn_file_stop.setEnabled(False)
        self.file_rate_result = QtWidgets.QLabel("Serial rate not checked")
        controls.addWidget(self.btn_file_check)
        controls.addWidget(self.btn_file_start)
        controls.addWidget(self.btn_file_stop)
        controls.addSpacing(18)
        controls.addWidget(self.file_rate_result)
        controls.addStretch(1)
        root.addLayout(controls)

        status = GroupPanel("Acquisition status")
        status_grid = QtWidgets.QGridLayout(status)
        self.file_state = QtWidgets.QLabel("Idle")
        self.file_samples = QtWidgets.QLabel("0")
        self.file_elapsed = QtWidgets.QLabel("0 s")
        self.file_actual_interval = QtWidgets.QLabel("—")
        self.file_late = QtWidgets.QLabel("0")
        self.file_message = QtWidgets.QLabel("—")
        self.file_message.setWordWrap(True)
        for column, (caption, widget) in enumerate(
            (
                ("State", self.file_state),
                ("Samples", self.file_samples),
                ("Elapsed", self.file_elapsed),
                ("Actual interval", self.file_actual_interval),
                ("Late samples", self.file_late),
            )
        ):
            status_grid.addWidget(QtWidgets.QLabel(caption), 0, column)
            widget.setStyleSheet("font-size:17px; font-weight:700; color:#25516a;")
            status_grid.addWidget(widget, 1, column)
        status_grid.addWidget(QtWidgets.QLabel("Message"), 2, 0)
        status_grid.addWidget(self.file_message, 2, 1, 1, 4)
        root.addWidget(status)
        note = QtWidgets.QLabel(
            "Rows are flushed as they arrive. A .meta.json sidecar records completion, "
            "cancellation, timing and errors. Other pages remain usable while acquisition runs."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#607785; padding:8px;")
        root.addWidget(note)
        root.addStretch(1)
        self.file_continuous.toggled.connect(lambda checked: self.file_duration.setEnabled(not checked))
        self.btn_file_browse.clicked.connect(self.browse_file_output)
        self.btn_file_check.clicked.connect(self.check_file_rate)
        self.btn_file_start.clicked.connect(self.start_file_logging)
        self.btn_file_stop.clicked.connect(self.stop_file_logging)
        return page

    def _build_records_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        toolbar = QtWidgets.QHBoxLayout()
        self.record_path = QtWidgets.QLineEdit()
        self.record_path.setPlaceholderText(
            "CSV/TSV or legacy .LoggRecJson/.LoggRecXml/.ILogRecJson/.ILogRecXml"
        )
        self.btn_record_browse = FlatPush("Open record…")
        self.record_summary = QtWidgets.QLabel("No record loaded")
        toolbar.addWidget(self.record_path, 1)
        toolbar.addWidget(self.btn_record_browse)
        toolbar.addWidget(self.record_summary)
        root.addLayout(toolbar)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.record_plot = TracePreview()
        self.record_table = QtWidgets.QTableWidget()
        self.record_table.setAlternatingRowColors(True)
        self.record_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        splitter.addWidget(self.record_plot)
        splitter.addWidget(self.record_table)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.btn_record_browse.clicked.connect(self.open_record_dialog)
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
        if generation != self._generation:
            return
        if error is not None:
            if label == "Read monitor definitions" and self._pending_file_start:
                self._pending_file_start = False
                self.btn_file_start.setEnabled(True)
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
        self, row: int, tokens: tuple[int, int, int], live: float | None
    ) -> None:
        name = IOSignalButton.format_io_signal(tokens)
        values = (str(row), name, str(tokens[0]), str(tokens[1]), str(tokens[2]))
        for column, value in enumerate(values):
            item = self.monitor_table.item(row, column) or QtWidgets.QTableWidgetItem()
            item.setText(value)
            self.monitor_table.setItem(row, column, item)
        if live is not None:
            item = self.monitor_table.item(row, 5) or QtWidgets.QTableWidgetItem()
            item.setText(format_ui_number(live))
            self.monitor_table.setItem(row, 5, item)

    def _monitor_row_changed(self, row: int, _column: int, *_args) -> None:
        if not 0 <= row < 40:
            return
        self.monitor_number.blockSignals(True)
        self.monitor_number.setValue(row)
        self.monitor_number.blockSignals(False)
        tokens = self.monitor_definitions[row]
        self.monitor_selector.set_io_signal(tokens)
        self.monitor_raw.setText(f"Type {tokens[0]} · Main {tokens[1]} · Sub {tokens[2]}")
        self.log_mon_num.setValue(row)
        self.log_mon_sig.setText(" ".join(str(value) for value in tokens))

    def _monitor_number_changed(self, row: int) -> None:
        self.monitor_table.selectRow(row)

    def _monitor_selector_changed(self, tokens: Any) -> None:
        row = self.monitor_number.value()
        values = tuple(int(value) for value in tokens[:3])
        self.monitor_definitions[row] = values
        self.monitor_names[row] = IOSignalButton.format_io_signal(values)
        self._set_monitor_row(row, values, None)
        self.monitor_raw.setText(f"Type {values[0]} · Main {values[1]} · Sub {values[2]}")
        self.log_mon_sig.setText(" ".join(str(value) for value in values))

    def read_monitor_definitions(self) -> None:
        def work():
            session = self._session()
            definitions = []
            for index in range(40):
                values = [int(value) for value in session.get_monitor_signal(index)[:3]]
                values.extend([0] * (3 - len(values)))
                definitions.append(tuple(values[:3]))
            return definitions

        def done(definitions):
            for row, tokens in enumerate(definitions):
                self.monitor_definitions[row] = tokens
                self.monitor_names[row] = IOSignalButton.format_io_signal(tokens)
                self._set_monitor_row(row, tokens, None)
            self._definitions_loaded = True
            self._monitor_row_changed(self.monitor_number.value(), 0)
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
        row = self.monitor_number.value()
        tokens = self.monitor_selector.io_tokens()

        def work():
            self.host._set_writable(True)
            self._session().set_monitor_signal(row, *tokens)
            return tuple(int(value) for value in self._session().get_monitor_signal(row)[:3])

        def done(readback):
            self.monitor_definitions[row] = readback
            self.monitor_names[row] = IOSignalButton.format_io_signal(readback)
            self._set_monitor_row(row, readback, None)
            self.log_mon_num.setValue(row)
            self.log_mon_sig.setText(" ".join(str(value) for value in readback))

        self._submit(f"Apply monitor channel {row}", work, done)

    def read_internal(self) -> None:
        trace_number = int(self.trace_selector.currentData() or 0)

        def work():
            session = self._session()
            params = session.get_event_trace_params()
            info = session.get_event_trace_info()
            event = session.get_event_signal()
            event_time = session.get_event_time(trace_number) if len(info) > 2 and _protocol_int(info[2]) > trace_number else []
            return params, info, event, event_time

        self._submit("Read internal trace", work, self._apply_internal_readback)

    def _apply_internal_readback(self, payload) -> None:
        params, info, event, event_time = payload
        for spin, value in zip(self.internal_param_spins, params):
            spin.setValue(_protocol_int(value, spin.value()))
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
        params = tuple(spin.value() for spin in self.internal_param_spins)
        event_tokens = self.event_selector.io_tokens()
        event = (*event_tokens, self.event_threshold.value(), self.event_trigger_samples.value())

        def work():
            self.host._set_writable(True)
            session = self._session()
            session.set_event_trace_params(*params)
            session.set_event_signal(*event)
            return (
                session.get_event_trace_params(),
                session.get_event_trace_info(),
                session.get_event_signal(),
                [],
            )

        self._submit("Apply internal trace configuration", work, self._apply_internal_readback)

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
            QtWidgets.QMessageBox.critical(self, "Start file logging", str(exc))
            return
        self.file_service = service
        self.btn_file_start.setEnabled(False)
        self.btn_file_stop.setEnabled(True)
        self.file_state.setText("Waiting" if config.start_after_s else "Running")
        self.page_status.setText("File logging active")
        self.host.log_msg(f"File logging scheduled: {config.path}")

    def stop_file_logging(self) -> None:
        if self.file_service:
            self.file_service.stop()
            self.file_state.setText("Stopping…")

    @QtCore.Slot(object, object, int)
    def _on_file_sample(self, stats, values, generation: int) -> None:
        if generation != self._generation:
            return
        self.file_state.setText("Running")
        self.file_samples.setText(str(stats.samples))
        self.file_elapsed.setText(f"{stats.elapsed_s:.2f} s")
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
            self,
            "Open logging record",
            self.record_path.text(),
            "Logging records (*.csv *.tsv *.txt *.LoggRecJson *.LoggRecXml *.LoggRecTxt *.ILogRecJson *.ILogRecXml *.ILogRecTxt *.json *.xml);;All files (*.*)",
        )
        if not path:
            return
        try:
            record = load_logging_record(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Open logging record", str(exc))
            return
        self.record_path.setText(path)
        self._show_record(record)

    def _show_record(self, record: LoggingRecord) -> None:
        self.record_plot.set_record(record)
        preview = record.rows[:1000]
        self.record_table.setColumnCount(len(record.headers))
        self.record_table.setHorizontalHeaderLabels(record.headers)
        self.record_table.setRowCount(len(preview))
        for row_index, row in enumerate(preview):
            for column, value in enumerate(row[: len(record.headers)]):
                self.record_table.setItem(
                    row_index, column, QtWidgets.QTableWidgetItem(format_ui_number(value))
                )
        self.record_table.resizeColumnsToContents()
        suffix = " (first 1000 shown)" if len(record.rows) > 1000 else ""
        self.record_summary.setText(
            f"{len(record.rows)} samples · {len(record.signal_names)} signals{suffix}"
        )
        self.record_path.setText(record.source)

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
        self._definitions_loaded = False
        self._pending_file_start = False
        self.btn_file_start.setEnabled(True)
        self.btn_file_stop.setEnabled(False)
        self.btn_trace_download.setEnabled(True)
        self.btn_trace_cancel.setEnabled(False)
        self.page_status.setText("Ready")

    def shutdown(self) -> None:
        """Request worker cancellation before the shared serial session closes."""
        self._generation += 1
        self._shutdown = True
        self._pending_file_start = False
        self._download_cancel.set()
        if self.file_service:
            self.file_service.stop(wait=True, timeout=1.0)
