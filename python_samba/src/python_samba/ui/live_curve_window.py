"""Persistent non-modal real-time monitor curve window."""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pyqtgraph as pg

from python_samba.logging_tools.live_curve import (
    LiveCurveAcquisitionService,
    LiveCurveConfig,
    LiveCurveSessionBuffer,
    LiveCurveSnapshot,
    MonitorCapabilities,
    MonitorSignalSpec,
    build_monitor_signal_catalog,
)
from python_samba.services.monitor_lease import (
    MonitorSlotLease,
    controller_endpoint_identity,
)
from python_samba.ui.classic_widgets import FlatPush, GroupPanel, format_ui_number
from python_samba.ui.plot_interactions import (
    CURVE_COLORS,
    DataTipPoint,
    DataTipText,
    InteractiveViewBox,
    PLOT_BACKGROUND,
    PLOT_FOREGROUND,
    PlainAxisItem,
    copy_plot_image,
    plot_font,
    short_number,
)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc


class _LiveCurveBridge(QtCore.QObject):
    prepared = QtCore.Signal(int, object)
    sample = QtCore.Signal(int, object, object)
    acquisition_finished = QtCore.Signal(int, object, object)
    restore_finished = QtCore.Signal(int, bool, str)


class LiveCurveWindow(QtWidgets.QDialog):
    """Select IOSignals, lease monitor slots, and plot their live values."""

    lease_active_changed = QtCore.Signal(bool)
    open_record_requested = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("realTimeCurveWindow")
        self.setWindowTitle("Real-time Curve")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(1500, 680)
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().size()
            target_width = min(1840, max(1500, int(available.width() * 0.96)))
            self.resize(
                max(1500, min(available.width(), target_width)),
                min(1040, max(760, int(available.height() * 0.92))),
            )
        else:
            self.resize(1600, 900)

        self.bridge = _LiveCurveBridge(self)
        self.bridge.prepared.connect(self._on_prepared)
        self.bridge.sample.connect(self._on_sample)
        self.bridge.acquisition_finished.connect(self._on_acquisition_finished)
        self.bridge.restore_finished.connect(self._on_restore_finished)

        self._session = None
        self._constants: tuple[str, ...] = ()
        self._version = None
        self._controller: dict[str, Any] = {}
        self._busy_checker: Callable[[], str] | None = None
        self._lease_callback: Callable[[bool], None] | None = None
        self._lease: MonitorSlotLease | None = None
        self._service: LiveCurveAcquisitionService | None = None
        self._buffer: LiveCurveSessionBuffer | None = None
        self._prepare_thread: threading.Thread | None = None
        self._restore_thread: threading.Thread | None = None
        self._generation = 0
        self._shutdown_requested = False
        self._lease_interlock_active = False
        self._preparing = False
        self._restoring = False
        self._populating_tree = False
        self._populating_table = False
        self._catalog: tuple[MonitorSignalSpec, ...] = ()
        self._selected_specs: list[MonitorSignalSpec] = []
        self._spec_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._parent_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self._visible_keys: set[str] = set()
        self._colors: dict[str, str] = {}
        self._curve_items: dict[str, pg.PlotDataItem] = {}
        self._snapshot: LiveCurveSnapshot | None = None
        self._latest_values: list[float] = []
        self._latest_stats = None
        self._last_plot_generation = -1
        self._follow = True
        self._auto_y = True
        self._follow_span_s = 60.0
        self._zoom_history: deque[
            tuple[tuple[float, float], tuple[float, float]]
        ] = deque(maxlen=5)
        self._last_saved_range = None
        self._cursor_state: dict[str, Any] | None = None
        self._data_tips: dict[int, dict[str, Any]] = {}
        self._tip_counter = 0
        self._markers: dict[str, dict[str, Any]] = {}
        self._moving_marker = False
        self._tip_menu_suppressed_until = 0.0

        self._build_ui()
        self._plot_timer = QtCore.QTimer(self)
        self._plot_timer.setInterval(100)
        self._plot_timer.timeout.connect(self._refresh_plot)
        self._plot_timer.start()
        self._populate_signal_tree(MonitorCapabilities())
        self._update_connection_state()

    # ---- public connection/lifecycle API -----------------------------

    def set_connection(
        self,
        session,
        *,
        constants: Sequence[object] = (),
        version: object | None = None,
        controller: dict[str, Any] | None = None,
        busy_checker: Callable[[], str] | None = None,
        lease_callback: Callable[[bool], None] | None = None,
    ) -> None:
        """Bind the current controller without starting any acquisition."""

        if self.running or self._preparing or self._restoring:
            self.stop_and_restore()
        self._session = session
        self._constants = tuple(str(value) for value in constants)
        self._version = version
        self._controller = dict(controller or {})
        self._controller.setdefault("endpoint", controller_endpoint_identity(session))
        self._busy_checker = busy_checker
        self._lease_callback = lease_callback
        capabilities = MonitorCapabilities.from_controller(self._constants, version)
        self._populate_signal_tree(capabilities)
        self._update_connection_state()
        self._check_pending_restore()

    @property
    def running(self) -> bool:
        return bool(self._service and self._service.running)

    @property
    def monitor_slots_active(self) -> bool:
        return bool(
            self._preparing
            or self._restoring
            or self.running
            or (self._lease and self._lease.active)
        )

    def stop_and_restore(self, *, timeout: float = 12.0) -> bool:
        """Synchronously stop polling and restore slots before connection close."""

        self._generation += 1
        generation = self._generation
        self._shutdown_requested = True
        service = self._service
        if service is not None:
            service.stop(wait=True, timeout=max(1.0, timeout / 3.0))
        prepare = self._prepare_thread
        if prepare is not None and prepare.is_alive():
            prepare.join(max(1.0, timeout / 3.0))
        restore_thread = self._restore_thread
        if restore_thread is not None and restore_thread.is_alive():
            restore_thread.join(max(1.0, timeout / 3.0))
        success = True
        message = "Monitor slots restored and verified."
        lease = self._lease
        if lease is not None and (lease.active or lease.recovery_path.exists()):
            success = lease.restore()
            message = message if success else lease.restore_error
        self._preparing = False
        self._restoring = False
        self._service = None
        if success:
            self._lease = None
        self._set_lease_active(False)
        self._set_running_ui(False)
        self._shutdown_requested = False
        if success:
            self.state_value.setText("Stopped")
            self.message_label.setText(message)
        else:
            self.state_value.setText("Restore pending")
            self.message_label.setText(message)
        self._check_pending_restore()
        return success

    def shutdown(self) -> bool:
        success = self.stop_and_restore()
        self.hide()
        return success

    # ---- UI construction ---------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(7)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Real-time Curve")
        title.setStyleSheet("font-size:18px; font-weight:700; color:#194b68;")
        header.addWidget(title)
        self.connection_label = QtWidgets.QLabel("Not connected")
        self.connection_label.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        header.addWidget(self.connection_label, 1)
        root.addLayout(header)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_signal_panel())
        self.splitter.addWidget(self._build_selection_panel())
        self.splitter.addWidget(self._build_plot_panel())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([480, 400, 1000])
        root.addWidget(self.splitter, 1)

        self.message_label = QtWidgets.QLabel("Select signals, then start acquisition.")
        self.message_label.setObjectName("liveCurveMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet(
            "QLabel#liveCurveMessage { background:#e7f3f9; border:1px solid #a9c7d7;"
            " border-radius:6px; padding:5px 9px; color:#31566c; }"
        )
        root.addWidget(self.message_label)

    def _build_signal_panel(self) -> QtWidgets.QWidget:
        panel = GroupPanel("Signal Selection")
        # Reserve enough room for the longest firmware signal name even when
        # the application's high-DPI font scaling is active.
        panel.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(7, 9, 7, 7)
        self.signal_filter = QtWidgets.QLineEdit()
        self.signal_filter.setPlaceholderText("Filter signal names…")
        self.signal_filter.textChanged.connect(self._filter_signal_tree)
        layout.addWidget(self.signal_filter)
        self.signal_tree = QtWidgets.QTreeWidget()
        self.signal_tree.setHeaderLabels(["Signal"])
        self.signal_tree.setAlternatingRowColors(True)
        self.signal_tree.setRootIsDecorated(True)
        self.signal_tree.setIndentation(14)
        self.signal_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.signal_tree.itemChanged.connect(self._signal_item_changed)
        layout.addWidget(self.signal_tree, 1)
        self.selection_count = QtWidgets.QLabel("0 / 40 selected")
        layout.addWidget(self.selection_count)
        return panel

    def _build_selection_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(380)
        root = QtWidgets.QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        selected = GroupPanel("Selected Signals")
        selected_layout = QtWidgets.QVBoxLayout(selected)
        selected_layout.setContentsMargins(7, 9, 7, 7)
        self.selected_table = QtWidgets.QTableWidget(0, 3)
        self.selected_table.setHorizontalHeaderLabels(["Color", "Signal", "Live"])
        self.selected_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.selected_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.selected_table.setAlternatingRowColors(True)
        header = self.selected_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        header.resizeSection(0, 34)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        header.resizeSection(2, 92)
        selected_layout.addWidget(self.selected_table, 1)
        root.addWidget(selected, 1)

        controls = GroupPanel("Sampling Control")
        form = QtWidgets.QFormLayout(controls)
        form.setContentsMargins(8, 10, 8, 8)
        self.interval_ms = QtWidgets.QSpinBox()
        self.interval_ms.setRange(20, 5000)
        self.interval_ms.setValue(100)
        self.interval_ms.setSuffix(" ms")
        self.interval_ms.setKeyboardTracking(False)
        self.span_seconds = QtWidgets.QDoubleSpinBox()
        self.span_seconds.setRange(1.0, 86400.0)
        self.span_seconds.setValue(60.0)
        self.span_seconds.setDecimals(1)
        self.span_seconds.setSuffix(" s")
        self.span_seconds.setKeyboardTracking(False)
        form.addRow("Request period", self.interval_ms)
        form.addRow("Initial span", self.span_seconds)

        self.btn_start = FlatPush("Start Timed Acquisition")
        self.btn_stop = FlatPush("Stop and Restore")
        self.btn_stop.setEnabled(False)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self.btn_start)
        button_row.addWidget(self.btn_stop)
        form.addRow(button_row)

        self.btn_save = FlatPush("Save Session")
        self.btn_open = FlatPush("Open Record")
        save_row = QtWidgets.QHBoxLayout()
        save_row.addWidget(self.btn_save)
        save_row.addWidget(self.btn_open)
        form.addRow(save_row)

        self.btn_retry_restore = FlatPush("Retry Restore")
        self.btn_retry_restore.setVisible(False)
        form.addRow(self.btn_retry_restore)

        self.state_value = QtWidgets.QLabel("Disconnected")
        self.samples_value = QtWidgets.QLabel("0")
        self.actual_interval_value = QtWidgets.QLabel("—")
        self.late_value = QtWidgets.QLabel("0")
        self.elapsed_value = QtWidgets.QLabel("0 s")
        form.addRow("State", self.state_value)
        form.addRow("Samples", self.samples_value)
        form.addRow("Actual average", self.actual_interval_value)
        form.addRow("Late samples", self.late_value)
        form.addRow("Elapsed", self.elapsed_value)
        root.addWidget(controls)

        self.btn_start.clicked.connect(self._start_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.btn_save.clicked.connect(self._save_session)
        self.btn_open.clicked.connect(self._open_record)
        self.btn_retry_restore.clicked.connect(self._retry_restore)
        return panel

    def _build_plot_panel(self) -> QtWidgets.QWidget:
        panel = GroupPanel("Live Curves")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(7, 9, 7, 7)
        toolbar = QtWidgets.QHBoxLayout()
        self.btn_resume_follow = FlatPush("Resume Follow")
        self.btn_auto_fit = FlatPush("Auto Fit")
        self.btn_previous_zoom = FlatPush("Previous Zoom")
        self.btn_cursor = FlatPush("Cursor")
        self.btn_cursor.setCheckable(True)
        self.btn_cursor.setChecked(True)
        self.btn_data_tip = FlatPush("Data Tip")
        self.btn_data_tip.setCheckable(True)
        self.btn_marker_a = FlatPush("Set A")
        self.btn_marker_b = FlatPush("Set B")
        self.btn_clear = FlatPush("Clear Annotations")
        self.btn_copy = FlatPush("Copy Image")
        for button in (
            self.btn_resume_follow,
            self.btn_auto_fit,
            self.btn_previous_zoom,
            self.btn_cursor,
            self.btn_data_tip,
            self.btn_marker_a,
            self.btn_marker_b,
            self.btn_clear,
            self.btn_copy,
        ):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.marker_readout = QtWidgets.QLabel("A —   B —   Δ —")
        self.marker_readout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        layout.addWidget(self.marker_readout)

        view_box = InteractiveViewBox(
            on_left_drag=lambda scene: self._handle_pointer(scene, dragging=True),
            on_right_zoom=self._rubber_zoom,
            on_navigation_start=self._navigation_started,
            on_wheel_start=self._wheel_navigation_started,
            on_wheel_finish=self._wheel_navigation_finished,
            on_axis_drag_start=self._axis_drag_started,
        )
        axes = {
            "bottom": PlainAxisItem(orientation="bottom"),
            "left": PlainAxisItem(orientation="left"),
        }
        self.plot_widget = pg.PlotWidget(viewBox=view_box, axisItems=axes)
        self.plot_widget.setBackground(PLOT_BACKGROUND)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.14)
        self.plot_widget.setLabel("bottom", "Elapsed time (s)")
        self.plot_widget.setLabel("left", "Controller raw value")
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        self.plot_widget.getPlotItem().hideButtons()
        self.plot_widget.getAxis("bottom").setTextPen(PLOT_FOREGROUND)
        self.plot_widget.getAxis("left").setTextPen(PLOT_FOREGROUND)
        self._view_box = view_box
        self._view_box.sigRangeChanged.connect(self._view_range_changed)
        self._legend = self.plot_widget.addLegend(offset=(8, 8))
        self._legend.setLabelTextSize("9pt")
        self._legend.setBrush(pg.mkBrush(255, 255, 255, 224))
        self._legend.setPen(pg.mkPen("#9bb4c2", width=0.8))
        self._legend.setZValue(20)
        self.plot_widget.scene().sigMouseClicked.connect(self._plot_scene_clicked)
        layout.addWidget(self.plot_widget, 1)

        help_label = QtWidgets.QLabel(
            "Left: cursor/data tip · Right drag: zoom · Middle drag: pan · Wheel: zoom"
        )
        help_label.setAlignment(QtCore.Qt.AlignCenter)
        help_label.setStyleSheet("color:#56788c;")
        layout.addWidget(help_label)

        self.btn_resume_follow.clicked.connect(self.resume_follow)
        self.btn_auto_fit.clicked.connect(self.auto_fit)
        self.btn_previous_zoom.clicked.connect(self.previous_zoom)
        self.btn_cursor.clicked.connect(lambda checked: self._set_pointer_tool("cursor", checked))
        self.btn_data_tip.clicked.connect(lambda checked: self._set_pointer_tool("data-tip", checked))
        self.btn_marker_a.clicked.connect(lambda: self.set_marker("A"))
        self.btn_marker_b.clicked.connect(lambda: self.set_marker("B"))
        self.btn_clear.clicked.connect(self.clear_annotations)
        self.btn_copy.clicked.connect(self.copy_plot)
        return panel

    # ---- signal catalog and selection --------------------------------

    def _populate_signal_tree(self, capabilities: MonitorCapabilities) -> None:
        previous = {spec.tokens for spec in self._selected_specs}
        self._catalog = build_monitor_signal_catalog(capabilities)
        self._populating_tree = True
        self.signal_tree.clear()
        self._spec_items.clear()
        self._parent_items.clear()
        try:
            by_category: dict[str, list[MonitorSignalSpec]] = {}
            for spec in self._catalog:
                by_category.setdefault(spec.category, []).append(spec)
            for category, specs in by_category.items():
                parent = QtWidgets.QTreeWidgetItem([category, str(len(specs))])
                parent.setFlags(parent.flags() | QtCore.Qt.ItemIsUserCheckable)
                parent.setCheckState(0, QtCore.Qt.Unchecked)
                parent.setData(0, QtCore.Qt.UserRole, None)
                self.signal_tree.addTopLevelItem(parent)
                self._parent_items[category] = parent
                for spec in specs:
                    child = QtWidgets.QTreeWidgetItem([spec.name])
                    child.setFlags(child.flags() | QtCore.Qt.ItemIsUserCheckable)
                    child.setCheckState(
                        0,
                        QtCore.Qt.Checked
                        if spec.tokens in previous
                        else QtCore.Qt.Unchecked,
                    )
                    child.setData(0, QtCore.Qt.UserRole, spec)
                    parent.addChild(child)
                    self._spec_items[spec.key] = child
                self._sync_parent_state(parent)
        finally:
            self._populating_tree = False
        self._selected_specs = [
            spec for spec in self._catalog if spec.tokens in previous
        ][:40]
        self._rebuild_selected_table()
        self.signal_tree.collapseAll()

    def _sync_parent_state(self, parent: QtWidgets.QTreeWidgetItem) -> None:
        checked = sum(
            parent.child(index).checkState(0) == QtCore.Qt.Checked
            for index in range(parent.childCount())
        )
        state = (
            QtCore.Qt.Unchecked
            if checked == 0
            else QtCore.Qt.Checked
            if checked == parent.childCount()
            else QtCore.Qt.PartiallyChecked
        )
        parent.setCheckState(0, state)

    @QtCore.Slot(QtWidgets.QTreeWidgetItem, int)
    def _signal_item_changed(self, item, column: int) -> None:
        if self._populating_tree or column != 0:
            return
        self._populating_tree = True
        try:
            spec = item.data(0, QtCore.Qt.UserRole)
            if isinstance(spec, MonitorSignalSpec):
                if item.checkState(0) == QtCore.Qt.Checked:
                    current = sum(
                        child.checkState(0) == QtCore.Qt.Checked
                        for child in self._spec_items.values()
                    )
                    if current > 40:
                        item.setCheckState(0, QtCore.Qt.Unchecked)
                        self.message_label.setText(
                            "A maximum of 40 signals can be selected."
                        )
                parent = item.parent()
                if parent is not None:
                    self._sync_parent_state(parent)
            else:
                desired = item.checkState(0) == QtCore.Qt.Checked
                children = [item.child(index) for index in range(item.childCount())]
                selected_elsewhere = sum(
                    child.checkState(0) == QtCore.Qt.Checked
                    for child in self._spec_items.values()
                    if child.parent() is not item
                )
                if desired and selected_elsewhere + len(children) > 40:
                    self._sync_parent_state(item)
                    self.message_label.setText(
                        "That group would exceed the 40-signal limit; selection was unchanged."
                    )
                else:
                    state = QtCore.Qt.Checked if desired else QtCore.Qt.Unchecked
                    for child in children:
                        child.setCheckState(0, state)
                    self._sync_parent_state(item)
        finally:
            self._populating_tree = False
        self._selected_specs = [
            spec
            for spec in self._catalog
            if self._spec_items[spec.key].checkState(0) == QtCore.Qt.Checked
        ]
        self._visible_keys = {spec.key for spec in self._selected_specs}
        self.selection_count.setText(f"{len(self._selected_specs)} / 40 selected")
        self._rebuild_selected_table()
        if self._buffer is not None and tuple(spec.tokens for spec in self._selected_specs) != tuple(
            spec.tokens for spec in self._buffer.signals
        ):
            self.message_label.setText(
                "Selection changed. The next Start begins a new session and clears the live plot."
            )

    def _filter_signal_tree(self, text: str) -> None:
        needle = text.strip().casefold()
        for parent in self._parent_items.values():
            visible = False
            for index in range(parent.childCount()):
                child = parent.child(index)
                show = not needle or needle in child.text(0).casefold()
                child.setHidden(not show)
                visible = visible or show
            parent.setHidden(not visible)
            if needle and visible:
                parent.setExpanded(True)

    def _rebuild_selected_table(self) -> None:
        self._populating_table = True
        self.selected_table.setRowCount(len(self._selected_specs))
        try:
            self._visible_keys = {spec.key for spec in self._selected_specs}
            for row, spec in enumerate(self._selected_specs):
                color = self._colors.setdefault(
                    spec.key, CURVE_COLORS[row % len(CURVE_COLORS)]
                )
                swatch = QtWidgets.QTableWidgetItem("●")
                swatch.setTextAlignment(QtCore.Qt.AlignCenter)
                swatch.setForeground(QtGui.QColor(color))
                name = QtWidgets.QTableWidgetItem(spec.name)
                name.setToolTip(
                    f"Type={spec.io_type}, MainIndex={spec.main_index}, SubIndex={spec.sub_index}"
                )
                live = QtWidgets.QTableWidgetItem("—")
                live.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if row < len(self._latest_values):
                    live.setText(format_ui_number(self._latest_values[row]))
                self.selected_table.setItem(row, 0, swatch)
                self.selected_table.setItem(row, 1, name)
                self.selected_table.setItem(row, 2, live)
        finally:
            self._populating_table = False

    # ---- start / stop / restore --------------------------------------

    def _start_clicked(self) -> None:
        if self.monitor_slots_active:
            return
        if not self._session or not self._session.connected:
            self.message_label.setText("Connect a controller before starting.")
            return
        if self._busy_checker is not None:
            reason = str(self._busy_checker() or "")
            if reason:
                self.message_label.setText(reason)
                return
        config = LiveCurveConfig(
            tuple(self._selected_specs),
            interval_ms=self.interval_ms.value(),
            initial_span_s=self.span_seconds.value(),
        )
        try:
            config.validate()
        except ValueError as exc:
            self.message_label.setText(str(exc))
            return

        selection_changed = self._buffer is None or tuple(
            spec.tokens for spec in self._buffer.signals
        ) != tuple(spec.tokens for spec in config.signals)
        if selection_changed:
            self._buffer = LiveCurveSessionBuffer(config.signals)
            self._latest_values = []
            self._snapshot = None
            self._last_plot_generation = -1
            self.clear_annotations()
            self._reset_curve_items(config.signals)
            self.samples_value.setText("0")
            self.elapsed_value.setText("0 s")
            self.actual_interval_value.setText("—")
            self.late_value.setText("0")
        elif not self._curve_items:
            self._reset_curve_items(config.signals)
        self._follow_span_s = float(config.initial_span_s)
        self._follow = True
        self._generation += 1
        generation = self._generation
        self._shutdown_requested = False
        self._preparing = True
        self._lease = MonitorSlotLease(
            self._session,
            controller=self._controller,
        )
        self._set_lease_active(True)
        self._set_running_ui(True, preparing=True)
        self.state_value.setText("Preparing")
        self.message_label.setText(
            "Snapshotting 40 monitor slots, applying the selection, and verifying readback…"
        )

        def target() -> None:
            error: BaseException | None = None
            try:
                event_info = self._session.get_event_trace_info()
                if event_info and int(str(event_info[0]), 0) != 0:
                    raise RuntimeError(
                        "Stop Internal Logging before starting Real-time Curve."
                    )
                assert self._lease is not None
                self._lease.acquire([spec.tokens for spec in config.signals])
            except BaseException as exc:
                error = exc
            self.bridge.prepared.emit(generation, (config, error))

        self._prepare_thread = threading.Thread(
            target=target, name="SambaLiveCurvePrepare", daemon=True
        )
        self._prepare_thread.start()

    @QtCore.Slot(int, object)
    def _on_prepared(self, generation: int, payload: object) -> None:
        if generation != self._generation:
            return
        config, error = payload
        self._preparing = False
        self._prepare_thread = None
        if error is not None:
            self._lease = None
            self._set_lease_active(False)
            self._set_running_ui(False)
            self.state_value.setText("Error")
            self.message_label.setText(f"Monitor slot configuration failed and was rolled back: {error}")
            self._check_pending_restore()
            return
        assert self._buffer is not None
        self._service = LiveCurveAcquisitionService(self._session, self._buffer)
        try:
            self._service.start(
                config,
                on_sample=lambda stats, values, selected=generation: self.bridge.sample.emit(
                    selected, stats, values
                ),
                on_finished=lambda stats, failure, selected=generation: self.bridge.acquisition_finished.emit(
                    selected, stats, failure
                ),
            )
        except BaseException as exc:
            self.message_label.setText(f"Acquisition start failed: {exc}")
            self._begin_restore_async()
            return
        self.state_value.setText("Running")
        self.message_label.setText(
            f"Acquiring {len(config.signals)} signals. Monitor definitions will be restored on stop."
        )

    @QtCore.Slot(int, object, object)
    def _on_sample(self, generation: int, stats: object, values: object) -> None:
        if generation != self._generation:
            return
        self._latest_stats = stats
        self._latest_values = [float(value) for value in values]
        self.state_value.setText("Running")
        self.samples_value.setText(
            str(self._buffer.sample_count if self._buffer is not None else stats.samples)
        )
        self.elapsed_value.setText(f"{stats.elapsed_s:.2f} s")
        self.actual_interval_value.setText(
            f"{stats.actual_interval_ms:.2f} ms" if stats.actual_interval_ms else "—"
        )
        self.late_value.setText(str(stats.late_samples))
        for row, value in enumerate(self._latest_values[: self.selected_table.rowCount()]):
            item = self.selected_table.item(row, 2)
            if item is not None:
                item.setText(format_ui_number(value))
        if stats.message:
            self.message_label.setText(stats.message)

    @QtCore.Slot(int, object, object)
    def _on_acquisition_finished(
        self, generation: int, stats: object, error: object
    ) -> None:
        if generation != self._generation:
            return
        self._latest_stats = stats
        if error is not None:
            self.message_label.setText(f"Acquisition stopped: {error}")
        elif stats.message:
            self.message_label.setText(stats.message)
        self._begin_restore_async()

    def _stop_clicked(self) -> None:
        if not self.monitor_slots_active:
            return
        self._generation += 1
        service = self._service
        if service is not None:
            service.stop(wait=False)
        self._begin_restore_async()

    def _begin_restore_async(self) -> None:
        if self._restoring:
            return
        self._generation += 1
        generation = self._generation
        self._restoring = True
        self._preparing = False
        self._set_running_ui(True)
        self.state_value.setText("Restoring")
        self.message_label.setText("Stopping acquisition and restoring all 40 monitor slots…")

        def target() -> None:
            prepare = self._prepare_thread
            if (
                prepare is not None
                and prepare is not threading.current_thread()
                and prepare.is_alive()
            ):
                prepare.join(12.0)
            service = self._service
            if service is not None:
                service.stop(wait=True, timeout=8.0)
            lease = self._lease
            if lease is None:
                success, message = True, "No monitor lease was active."
            else:
                success = lease.restore()
                message = (
                    "Monitor slots restored and verified."
                    if success
                    else lease.restore_error
                )
            if generation != self._generation:
                return
            self.bridge.restore_finished.emit(generation, success, message)

        self._restore_thread = threading.Thread(
            target=target, name="SambaLiveCurveRestore", daemon=True
        )
        self._restore_thread.start()

    @QtCore.Slot(int, bool, str)
    def _on_restore_finished(
        self, generation: int, success: bool, message: str
    ) -> None:
        if generation != self._generation:
            return
        self._restoring = False
        self._restore_thread = None
        self._service = None
        if success:
            self._lease = None
        self._set_lease_active(False)
        self._set_running_ui(False)
        self.state_value.setText("Stopped" if success else "Restore pending")
        self.message_label.setText(message)
        self._check_pending_restore()

    def _set_lease_active(self, active: bool) -> None:
        active = bool(active)
        if self._lease_interlock_active == active:
            return
        self._lease_interlock_active = active
        self.lease_active_changed.emit(bool(active))
        if self._lease_callback is not None:
            self._lease_callback(bool(active))

    def _set_running_ui(self, active: bool, *, preparing: bool = False) -> None:
        self.signal_tree.setEnabled(not active)
        self.signal_filter.setEnabled(not active)
        self.interval_ms.setEnabled(not active)
        self.span_seconds.setEnabled(not active)
        self.btn_start.setEnabled(not active and self._connection_ready())
        self.btn_stop.setEnabled(active)
        self.btn_stop.setText(
            "Cancel Preparation" if preparing else "Stop and Restore"
        )

    def _retry_restore(self) -> None:
        if not self._connection_ready() or self.monitor_slots_active:
            return
        success, message = MonitorSlotLease.retry_pending(self._session)
        self.message_label.setText(message)
        self.state_value.setText("Stopped" if success else "Restore pending")
        if success:
            self._lease = None
        self._check_pending_restore()

    def _check_pending_restore(self) -> None:
        pending = None
        if self._connection_ready():
            try:
                pending = MonitorSlotLease.pending_for_session(self._session)
            except Exception as exc:
                self.message_label.setText(f"Could not inspect monitor recovery file: {exc}")
        self.btn_retry_restore.setVisible(pending is not None)
        if pending is not None and not self.monitor_slots_active:
            self._set_lease_active(True)
            self.state_value.setText("Restore pending")
            self.message_label.setText(
                "A monitor-slot restore is pending for this controller endpoint. "
                "Use Retry Restore before starting a new session."
            )
            self.btn_start.setEnabled(False)
        elif pending is None and not self.monitor_slots_active:
            self._set_lease_active(False)

    def _connection_ready(self) -> bool:
        return bool(self._session and self._session.connected)

    def _update_connection_state(self) -> None:
        connected = self._connection_ready()
        if connected:
            identity = controller_endpoint_identity(self._session)
            endpoint = identity["server_endpoint"] or identity["port"] or identity["backend"]
            self.connection_label.setText(
                f"Connected · {endpoint} · {identity['port']} @ {identity['baudrate']}"
            )
            if not self.monitor_slots_active:
                self.state_value.setText("Ready")
        else:
            self.connection_label.setText("Not connected")
            self.state_value.setText("Disconnected")
        self.btn_start.setEnabled(connected and not self.monitor_slots_active)
        self.btn_retry_restore.setEnabled(connected and not self.monitor_slots_active)

    # ---- live plot data ----------------------------------------------

    def _reset_curve_items(self, signals: Sequence[MonitorSignalSpec]) -> None:
        self.clear_annotations()
        for item in self._curve_items.values():
            try:
                self._view_box.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        self._curve_items.clear()
        self._legend.clear()
        self._visible_keys = {spec.key for spec in signals}
        self._auto_y = True
        for index, spec in enumerate(signals):
            color = self._colors.setdefault(
                spec.key, CURVE_COLORS[index % len(CURVE_COLORS)]
            )
            curve_color = QtGui.QColor(color)
            curve_color.setAlpha(225)
            pen = pg.mkPen(curve_color, width=1.2)
            pen.setCosmetic(True)
            item = pg.PlotDataItem(
                [],
                [],
                name=spec.name,
                pen=pen,
                connect="finite",
                antialias=True,
            )
            item.setDownsampling(auto=True, method="peak")
            item.setClipToView(True)
            item.setVisible(spec.key in self._visible_keys)
            self._view_box.addItem(item)
            self._curve_items[spec.key] = item
        self._sync_legend()
        self._rebuild_selected_table()

    def _sync_legend(self) -> None:
        self._legend.clear()
        for spec in self._selected_specs:
            item = self._curve_items.get(spec.key)
            if item is not None and spec.key in self._visible_keys:
                self._legend.addItem(item, spec.name)

    def _refresh_plot(self) -> None:
        if self._buffer is None:
            return
        generation = self._buffer.generation
        if generation == self._last_plot_generation:
            return
        try:
            if self._follow:
                bounds = self._buffer.time_bounds
                latest = 0.0 if bounds is None else bounds[1]
                span = max(1e-6, float(self._follow_span_s))
                requested_start = max(0.0, latest - span)
                snapshot = self._buffer.snapshot(start_s=requested_start)
            else:
                x_range = self._view_box.viewRange()[0]
                padding = max(1.0, abs(float(x_range[1] - x_range[0])) * 0.25)
                snapshot = self._buffer.snapshot(
                    start_s=max(0.0, float(x_range[0]) - padding),
                    end_s=float(x_range[1]) + padding,
                )
        except MemoryError:
            self.message_label.setText(
                "Memory exhausted while preparing plot data; acquisition stopped safely."
            )
            if self.running:
                self._begin_restore_async()
            return
        self._snapshot = snapshot
        self._last_plot_generation = snapshot.generation
        display_x, display_values = self._display_arrays(snapshot)
        for column, spec in enumerate(self._buffer.signals):
            item = self._curve_items.get(spec.key)
            if item is not None:
                item.setData(display_x, display_values[:, column])
        if not snapshot.elapsed_s.size:
            return
        latest = float(snapshot.elapsed_s[-1])
        if self._follow:
            span = max(1e-6, float(self._follow_span_s))
            x_range = (0.0, span) if latest <= span else (latest - span, latest)
            self._view_box.setXRange(*x_range, padding=0.0)
            if self._auto_y:
                self._auto_y_for_range(*x_range)

    def _display_arrays(
        self, snapshot: LiveCurveSnapshot
    ) -> tuple[np.ndarray, np.ndarray]:
        """Insert NaN separators so stopped intervals render as blank gaps."""

        if (
            self._buffer is None
            or snapshot.elapsed_s.size < 2
            or not self._buffer.completed_gaps
        ):
            return snapshot.elapsed_s, snapshot.values
        x_parts: list[np.ndarray] = []
        value_parts: list[np.ndarray] = []
        cursor = 0
        for start, end in self._buffer.completed_gaps:
            index = int(np.searchsorted(snapshot.elapsed_s, end, side="left"))
            if index <= cursor or index >= snapshot.elapsed_s.size:
                continue
            if float(snapshot.elapsed_s[index - 1]) >= end:
                continue
            x_parts.append(snapshot.elapsed_s[cursor:index])
            value_parts.append(snapshot.values[cursor:index, :])
            x_parts.append(np.asarray([start, end], dtype=np.float64))
            value_parts.append(
                np.full((2, snapshot.values.shape[1]), np.nan, dtype=np.float64)
            )
            cursor = index
        if not x_parts:
            return snapshot.elapsed_s, snapshot.values
        x_parts.append(snapshot.elapsed_s[cursor:])
        value_parts.append(snapshot.values[cursor:, :])
        return np.concatenate(x_parts), np.concatenate(value_parts, axis=0)

    def _auto_y_for_range(self, start: float, stop: float) -> None:
        snapshot = self._snapshot
        if snapshot is None or not snapshot.elapsed_s.size:
            return
        lo = int(np.searchsorted(snapshot.elapsed_s, start, side="left"))
        hi = int(np.searchsorted(snapshot.elapsed_s, stop, side="right"))
        if hi <= lo:
            return
        columns = [
            index
            for index, spec in enumerate(self._buffer.signals)
            if spec.key in self._visible_keys
        ]
        if not columns:
            return
        values = snapshot.values[lo:hi, :][:, columns]
        finite = values[np.isfinite(values)]
        if not finite.size:
            return
        minimum = float(np.min(finite))
        maximum = float(np.max(finite))
        margin = (maximum - minimum) * 0.08
        if margin <= 0:
            margin = max(1.0, abs(maximum) * 0.08)
        self._view_box.setYRange(minimum - margin, maximum + margin, padding=0.0)

    # ---- pointer, tips, markers, and navigation ----------------------

    def _pointer_tool(self) -> str:
        return "data-tip" if self.btn_data_tip.isChecked() else "cursor"

    def _set_pointer_tool(self, tool: str, checked: bool) -> None:
        if not checked:
            if not self.btn_cursor.isChecked() and not self.btn_data_tip.isChecked():
                self.btn_cursor.setChecked(True)
            return
        self.btn_cursor.blockSignals(True)
        self.btn_data_tip.blockSignals(True)
        self.btn_cursor.setChecked(tool == "cursor")
        self.btn_data_tip.setChecked(tool == "data-tip")
        self.btn_cursor.blockSignals(False)
        self.btn_data_tip.blockSignals(False)

    def _nearest_point(self, scene_position, only_key: str | None = None):
        snapshot = self._snapshot
        if snapshot is None or not snapshot.elapsed_s.size or self._buffer is None:
            return None
        point = self._view_box.mapSceneToView(scene_position)
        ranges = self._view_box.viewRange()
        x_span = max(abs(ranges[0][1] - ranges[0][0]), 1e-12)
        y_span = max(abs(ranges[1][1] - ranges[1][0]), 1e-12)
        insertion = int(np.searchsorted(snapshot.elapsed_s, float(point.x())))
        candidate_indices = {
            max(0, min(snapshot.elapsed_s.size - 1, insertion)),
            max(0, min(snapshot.elapsed_s.size - 1, insertion - 1)),
        }
        best = None
        best_distance = float("inf")
        for column, spec in enumerate(self._buffer.signals):
            if spec.key not in self._visible_keys:
                continue
            if only_key is not None and spec.key != only_key:
                continue
            for index in candidate_indices:
                x_value = float(snapshot.elapsed_s[index])
                y_value = float(snapshot.values[index, column])
                if not np.isfinite(y_value):
                    continue
                distance = ((x_value - point.x()) / x_span) ** 2 + (
                    (y_value - point.y()) / y_span
                ) ** 2
                if distance < best_distance:
                    best_distance = distance
                    best = (spec, index, x_value, y_value, x_value, y_value)
        return best

    def _nearest_for_x(self, key: str, x_value: float):
        snapshot = self._snapshot
        if snapshot is None or not snapshot.elapsed_s.size or self._buffer is None:
            return None
        column = next(
            (index for index, spec in enumerate(self._buffer.signals) if spec.key == key),
            None,
        )
        if column is None:
            return None
        insertion = int(np.searchsorted(snapshot.elapsed_s, x_value))
        choices = [
            max(0, min(snapshot.elapsed_s.size - 1, insertion)),
            max(0, min(snapshot.elapsed_s.size - 1, insertion - 1)),
        ]
        index = min(choices, key=lambda value: abs(float(snapshot.elapsed_s[value]) - x_value))
        y = float(snapshot.values[index, column])
        return (
            self._buffer.signals[column],
            index,
            float(snapshot.elapsed_s[index]),
            y,
            float(snapshot.elapsed_s[index]),
            y,
        )

    def _plot_scene_clicked(self, event) -> None:
        if event.button() == QtCore.Qt.RightButton:
            if time.monotonic() >= self._tip_menu_suppressed_until:
                self._show_plot_menu(event.screenPos())
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._handle_pointer(event.scenePos(), dragging=False)

    def _handle_pointer(self, scene_position, *, dragging: bool) -> None:
        nearest = self._nearest_point(scene_position)
        if nearest is None:
            return
        if self._pointer_tool() == "data-tip":
            if not dragging:
                self._add_data_tip(nearest)
        else:
            self._update_cursor(nearest)

    @staticmethod
    def _point_label(prefix: str, nearest) -> str:
        spec, _index, x_value, y_value, _plot_x, _plot_y = nearest
        return (
            f"{prefix}{spec.name}\n"
            f"X {short_number(x_value)} s\nY {short_number(y_value)}"
        )

    def _update_cursor(self, nearest) -> None:
        if self._cursor_state is None:
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#0f4c81", width=1.2))
            point = pg.ScatterPlotItem(
                size=9, pen=pg.mkPen("#071827"), brush=pg.mkBrush("#fff176")
            )
            label = pg.TextItem(
                color="#071827",
                fill=pg.mkBrush(255, 245, 157, 225),
                border=pg.mkPen("#071827", width=0.8),
                anchor=(0.0, 1.0),
            )
            label.setFont(plot_font())
            self._view_box.addItem(line, ignoreBounds=True)
            self._view_box.addItem(point, ignoreBounds=True)
            self._view_box.addItem(label, ignoreBounds=True)
            self._cursor_state = {
                "line": line,
                "point": point,
                "label": label,
                "nearest": nearest,
            }
        state = self._cursor_state
        state["line"].setValue(nearest[4])
        state["point"].setData([nearest[4]], [nearest[5]])
        state["label"].setText(self._point_label("", nearest))
        state["label"].setPos(nearest[4], nearest[5])
        state["nearest"] = nearest
        self.message_label.setText(
            f"Cursor · {nearest[0].name} · sample {nearest[1] + 1} · "
            f"X {short_number(nearest[2])} · Y {short_number(nearest[3])}"
        )

    def _add_data_tip(self, nearest) -> None:
        self._tip_counter += 1
        tip_id = self._tip_counter
        spec = nearest[0]
        color = self._colors.get(spec.key, CURVE_COLORS[0])
        point = DataTipPoint(
            [nearest[4]],
            [nearest[5]],
            size=10,
            pen=pg.mkPen("#071827"),
            brush=pg.mkBrush(color),
            on_drag=lambda scene, selected=tip_id: self._drag_data_tip(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label = DataTipText(
            self._point_label("", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color),
            anchor=(-0.05, 1.05),
            on_drag=lambda scene, selected=tip_id: self._drag_tip_label(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label.setFont(plot_font())
        label.setPos(nearest[4], nearest[5])
        self._view_box.addItem(point, ignoreBounds=True)
        self._view_box.addItem(label, ignoreBounds=True)
        self._data_tips[tip_id] = {
            "key": spec.key,
            "nearest": nearest,
            "point": point,
            "label": label,
        }

    def _drag_data_tip(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        nearest = self._nearest_point(scene_position, only_key=tip["key"])
        if nearest is None:
            return
        tip["nearest"] = nearest
        tip["point"].setData([nearest[4]], [nearest[5]])
        tip["label"].setText(self._point_label("", nearest))
        tip["label"].setPos(nearest[4], nearest[5])

    def _drag_tip_label(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        mouse = self._view_box.mapSceneToView(scene_position)
        nearest = tip["nearest"]
        tip["label"].setAnchor(
            (-0.05 if mouse.x() >= nearest[4] else 1.05,
             1.05 if mouse.y() >= nearest[5] else -0.05)
        )
        tip["label"].setPos(nearest[4], nearest[5])

    def _show_tip_menu(self, tip_id: int, screen_position) -> None:
        # pyqtgraph emits a scene-level click after a graphics item has
        # accepted the same right-click.  The menu is blocking, so refresh the
        # suppression window after it closes as well as before it opens.
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        menu = QtWidgets.QMenu(self)
        remove = menu.addAction("Delete this data tip")
        clear = menu.addAction("Clear all data tips")
        action = menu.exec(self._screen_point(screen_position))
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        if action == remove:
            self._remove_data_tip(tip_id)
        elif action == clear:
            self.clear_data_tips()

    def _remove_data_tip(self, tip_id: int) -> None:
        tip = self._data_tips.pop(tip_id, None)
        if tip is None:
            return
        self._view_box.removeItem(tip["point"])
        self._view_box.removeItem(tip["label"])

    def clear_data_tips(self) -> None:
        for tip_id in list(self._data_tips):
            self._remove_data_tip(tip_id)

    def set_marker(self, name: str) -> None:
        keys = [spec.key for spec in self._selected_specs if spec.key in self._visible_keys]
        if not keys:
            self.message_label.setText("Select at least one curve before setting a marker.")
            return
        nearest = None
        if self._cursor_state is not None:
            cursor = self._cursor_state.get("nearest")
            if cursor is not None and cursor[0].key in keys:
                nearest = cursor
        if nearest is None:
            x_range = self._view_box.viewRange()[0]
            nearest = self._nearest_for_x(keys[0], sum(x_range) / 2.0)
        if nearest is None:
            return
        self._remove_marker(name)
        color = "#e64a19" if name == "A" else "#7b1fa2"
        line = pg.InfiniteLine(
            pos=nearest[4], angle=90, movable=True, pen=pg.mkPen(color, width=1.6)
        )
        point = pg.ScatterPlotItem(
            [nearest[4]], [nearest[5]], size=10, brush=pg.mkBrush(color)
        )
        label = pg.TextItem(
            self._point_label(f"{name} · ", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color),
            anchor=(0.0, 1.0),
        )
        label.setFont(plot_font())
        label.setPos(nearest[4], nearest[5])
        self._view_box.addItem(line, ignoreBounds=True)
        self._view_box.addItem(point, ignoreBounds=True)
        self._view_box.addItem(label, ignoreBounds=True)
        self._markers[name] = {
            "key": nearest[0].key,
            "nearest": nearest,
            "line": line,
            "point": point,
            "label": label,
        }
        line.sigPositionChanged.connect(
            lambda _line=None, selected=name: self._marker_moved(selected, False)
        )
        line.sigPositionChangeFinished.connect(
            lambda _line=None, selected=name: self._marker_moved(selected, True)
        )
        self._update_marker_readout()

    def _marker_moved(self, name: str, finished: bool) -> None:
        if self._moving_marker:
            return
        marker = self._markers.get(name)
        if marker is None:
            return
        nearest = self._nearest_for_x(marker["key"], float(marker["line"].value()))
        if nearest is None:
            return
        marker["nearest"] = nearest
        marker["point"].setData([nearest[4]], [nearest[5]])
        marker["label"].setText(self._point_label(f"{name} · ", nearest))
        marker["label"].setPos(nearest[4], nearest[5])
        if finished:
            self._moving_marker = True
            try:
                marker["line"].setValue(nearest[4])
            finally:
                self._moving_marker = False
        self._update_marker_readout()

    def _remove_marker(self, name: str) -> None:
        marker = self._markers.pop(name, None)
        if marker is None:
            return
        for key in ("line", "point", "label"):
            self._view_box.removeItem(marker[key])

    def _update_marker_readout(self) -> None:
        a = self._markers.get("A", {}).get("nearest")
        b = self._markers.get("B", {}).get("nearest")
        a_text = "—" if a is None else f"{short_number(a[2])}, {short_number(a[3])}"
        b_text = "—" if b is None else f"{short_number(b[2])}, {short_number(b[3])}"
        delta = (
            "—"
            if a is None or b is None
            else f"{short_number(b[2] - a[2])}, {short_number(b[3] - a[3])}"
        )
        self.marker_readout.setText(f"A {a_text}   B {b_text}   Δ {delta}")

    def clear_annotations(self) -> None:
        self.clear_data_tips()
        if self._cursor_state is not None:
            for key in ("line", "point", "label"):
                self._view_box.removeItem(self._cursor_state[key])
            self._cursor_state = None
        for name in list(self._markers):
            self._remove_marker(name)
        self._update_marker_readout()

    def _navigation_started(self) -> None:
        self._remember_range()
        self._follow = False
        self.btn_resume_follow.setText("Resume Follow")
        self._last_plot_generation = -1

    def _wheel_navigation_started(self, axis: int | None = None) -> None:
        """Remember wheel zoom without leaving the live-follow mode."""

        self._remember_range()

    def _wheel_navigation_finished(self, axis: int | None = None) -> None:
        """Adopt the wheel-selected span and keep following the newest point."""

        if axis in (None, 0):
            current = self._view_box.viewRange()[0]
            span = abs(float(current[1] - current[0]))
            if span > 1e-9:
                self._follow_span_s = span
            self._follow = True
            self.btn_resume_follow.setText("Following")
        if axis in (None, 1):
            self._auto_y = False
        self._last_plot_generation = -1

    def _axis_drag_started(self, axis: int) -> None:
        """Keep X following during Y-axis navigation; X-axis drag still pans."""

        self._remember_range()
        if axis == 1:
            self._auto_y = False
            return
        self._navigation_started()

    def _view_range_changed(self, *_args) -> None:
        if not self._follow:
            self._last_plot_generation = -1

    def _remember_range(self) -> None:
        ranges = self._view_box.viewRange()
        snapshot = (
            (float(ranges[0][0]), float(ranges[0][1])),
            (float(ranges[1][0]), float(ranges[1][1])),
        )
        if snapshot != self._last_saved_range:
            self._zoom_history.append(snapshot)
            self._last_saved_range = snapshot

    def _rubber_zoom(self, start, stop) -> None:
        if abs(stop.x() - start.x()) < 1e-12 or abs(stop.y() - start.y()) < 1e-12:
            return
        self._navigation_started()
        self._view_box.setRange(
            xRange=sorted((float(start.x()), float(stop.x()))),
            yRange=sorted((float(start.y()), float(stop.y()))),
            padding=0.0,
        )

    def resume_follow(self) -> None:
        current = self._view_box.viewRange()[0]
        span = abs(float(current[1] - current[0]))
        self._follow_span_s = span if span > 1e-9 else self.span_seconds.value()
        self._follow = True
        self.btn_resume_follow.setText("Following")
        self._last_plot_generation = -1
        self._refresh_plot()

    def auto_fit(self) -> None:
        if self._buffer is None:
            return
        try:
            snapshot = self._buffer.snapshot()
        except MemoryError:
            self.message_label.setText(
                "The complete session is too large to auto-fit; use the current visible window."
            )
            return
        if not snapshot.elapsed_s.size:
            return
        self._auto_y = True
        self._snapshot = snapshot
        display_x, display_values = self._display_arrays(snapshot)
        for column, spec in enumerate(self._buffer.signals):
            item = self._curve_items.get(spec.key)
            if item is not None:
                item.setData(display_x, display_values[:, column])
        self._navigation_started()
        start = float(snapshot.elapsed_s[0])
        stop = float(snapshot.elapsed_s[-1])
        if stop <= start:
            stop = start + 1.0
        self._view_box.setXRange(start, stop, padding=0.02)
        self._auto_y_for_range(start, stop)

    def previous_zoom(self) -> None:
        if not self._zoom_history:
            self.message_label.setText("No previous zoom range is available.")
            return
        x_range, y_range = self._zoom_history.pop()
        self._follow = False
        self._view_box.setRange(xRange=x_range, yRange=y_range, padding=0.0)
        self._last_saved_range = None

    def copy_plot(self) -> bool:
        success = copy_plot_image(self.plot_widget)
        self.message_label.setText(
            "Live plot copied to the clipboard."
            if success
            else "The live plot image could not be copied."
        )
        return success

    def _show_plot_menu(self, screen_position) -> None:
        menu = QtWidgets.QMenu(self)
        resume = menu.addAction("Resume follow")
        previous = menu.addAction("Previous zoom")
        auto = menu.addAction("Auto fit")
        menu.addSeparator()
        cursor = menu.addAction("Cursor")
        tip = menu.addAction("Data tip")
        marker_a = menu.addAction("Set marker A")
        marker_b = menu.addAction("Set marker B")
        clear = menu.addAction("Clear annotations")
        menu.addSeparator()
        copy = menu.addAction("Copy image")
        action = menu.exec(self._screen_point(screen_position))
        if action == resume:
            self.resume_follow()
        elif action == previous:
            self.previous_zoom()
        elif action == auto:
            self.auto_fit()
        elif action == cursor:
            self._set_pointer_tool("cursor", True)
        elif action == tip:
            self._set_pointer_tool("data-tip", True)
        elif action == marker_a:
            self.set_marker("A")
        elif action == marker_b:
            self.set_marker("B")
        elif action == clear:
            self.clear_annotations()
        elif action == copy:
            self.copy_plot()

    @staticmethod
    def _screen_point(position) -> QtCore.QPoint:
        return position.toPoint() if hasattr(position, "toPoint") else QtCore.QPoint(
            int(position.x()), int(position.y())
        )

    # ---- save/open ----------------------------------------------------

    def _save_session(self) -> None:
        if self._buffer is None or self._buffer.sample_count == 0:
            self.message_label.setText("There is no captured session to save.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save real-time curve session", "real_time_curve.csv", "CSV (*.csv)"
        )
        if not path:
            return
        stats = self._latest_stats
        try:
            output = self._buffer.export_csv(
                path,
                colors=self._colors,
                controller=self._controller,
                requested_interval_ms=self.interval_ms.value(),
                actual_interval_ms=float(getattr(stats, "actual_interval_ms", 0.0)),
                late_samples=int(getattr(stats, "late_samples", 0)),
            )
        except Exception as exc:
            self.message_label.setText(f"Save failed: {exc}")
            return
        self.message_label.setText(f"Saved {self._buffer.sample_count} samples to {output}")

    def _open_record(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open logging record",
            "",
            "Logging records (*.csv *.tsv *.txt *.LoggRecJson *.LoggRecXml *.ILogRecJson *.ILogRecXml);;All files (*.*)",
        )
        if path:
            self.open_record_requested.emit(str(Path(path).resolve()))

    # ---- Qt lifecycle -------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self.monitor_slots_active:
            self.stop_and_restore()
        self.hide()
        event.ignore()


__all__ = ["LiveCurveWindow"]
