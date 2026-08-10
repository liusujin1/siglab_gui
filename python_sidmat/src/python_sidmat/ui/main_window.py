"""SiDiMaT main window — layout from a live UI Automation dump of the
original SiDiMaT19xA.exe, every button wired to a real backend call.

Layout (mirrors the original):
  Menu bar   : 系统 (Exit / About)
  Left column: emoji toolbar row + 8 collapsible groups
      ☏ Connection | 🔊 Excitation/Diag | 📈 Trace Setting
      📂💾 Open/Save Setting | 🔧 System Setting
      📉🖑 Measuring Helping Hand & Offline Tuner | 🔃 Convert to json | ❓ Help
  Right panel : plot toolbar (3 segments) + 2×2 plot matrix
  Status bar  : progress / messages
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import replace

import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets
from python_samba.transport.comm_server import CommServerConfig, CommServerTransport
from python_samba.ui.server_discovery import choose_communication_server

from python_sidmat.analysis.pwelch import pwelch
from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.analysis.windows import WindowType
from python_sidmat.backend.controller import Controller
from python_sidmat.backend.iosignal import (
    DEFAULT_POSITION_FILTER_COUNT,
    DEFAULT_VELOCITY_FILTER_COUNT,
    IOType,
    configure_filter_counts,
)
from python_sidmat.measurement.datafile import (
    export_trace_config,
    import_trace_config,
)
from python_sidmat.measurement.matfile import (
    load_sidimat_raw,
    save_sidimat_raw,
)
from python_sidmat.measurement.settings import (
    load_measurement_settings,
    save_measurement_settings,
)
from python_sidmat.measurement.engine import MeasurementCancelled, MeasurementEngine
from python_sidmat.measurement.filter_tf import apply_filter_chain, generate_closed_loop
from python_sidmat.measurement.figurefile import (
    FigureModel,
    FigureSeries,
    IdeFigure,
    load_idefigure,
    save_idefigure,
)
from python_sidmat.measurement.trace import TraceParameters
from python_sidmat.ui.excitation import ExcitationWidget
from python_sidmat.ui.trace_info import TraceInfoWidget
from python_sidmat.ui.theme import apply_samba_theme

__all__ = ["MainWindow"]


class _MeasurementWorker(QtCore.QThread):
    finishedOk = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()
    progress = QtCore.Signal(int, int)
    averageComplete = QtCore.Signal(int, int, int)

    def __init__(self, controller, trace, sample_frequency, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._trace = trace
        self._sample_frequency = sample_frequency
        self._engine = None
        self._stop_requested = False

    def run(self):
        self._engine = MeasurementEngine(
            self._controller,
            self._trace,
            self._sample_frequency,
            on_progress=lambda current, total: self.progress.emit(current, total),
            on_average_complete=lambda average, ch0, _ch1: self.averageComplete.emit(
                average + 1, self._trace.average_number, len(ch0)
            ),
        )
        if self._stop_requested:
            self._engine.stop()
        try:
            raw = self._engine.run()
        except MeasurementCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finishedOk.emit(raw)

    def stop(self):
        self._stop_requested = True
        if self._engine is not None:
            self._engine.stop()


class CollapsibleGroup(QtWidgets.QWidget):
    """WPF-Expander style collapsible group (as seen in the original UI)."""

    def __init__(self, title: str, *, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self._toggle = QtWidgets.QToolButton()
        self._toggle.setObjectName("sectionHeader")
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(not collapsed)
        self._toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(
            QtCore.Qt.ArrowType.RightArrow if collapsed else QtCore.Qt.ArrowType.DownArrow)
        self._toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        self._content = QtWidgets.QWidget()
        self._content_lo = QtWidgets.QVBoxLayout(self._content)
        self._content_lo.setContentsMargins(8, 2, 4, 4)
        self._content_lo.setSpacing(3)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line.setStyleSheet("color: #486a7d;")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._toggle)
        outer.addWidget(self._content)
        outer.addWidget(line)

        self._toggle.toggled.connect(self._on_toggled)
        self._content.setVisible(not collapsed)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

    def addWidget(self, w: QtWidgets.QWidget) -> None:
        self._content_lo.addWidget(w)

    def addLayout(self, lo: QtWidgets.QLayout) -> None:
        self._content_lo.addLayout(lo)


_AXIS_NAMES = (
    "Xtrans", "Zrot", "Ytrans", "Ztrans",
    "Yrot", "Xrot", "Xrot2", "Yrot2",
    "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
)
_VEL_AXIS_NAMES = _AXIS_NAMES[:6]
_POS_AXIS_NAMES = (
    "Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
    "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
)

# The RCI OnOff wire type is one character: N = on, F = off.  The UI keeps
# displaying the friendlier ON/OFF labels, but must send the documented wire
# values to real controllers.
_NOISE_FILTER_ON = "N"
_NOISE_FILTER_OFF = "F"


def _measurement_stage_labels(filter_count: int) -> list[str]:
    return ["Raw", *(f"Stage{index + 1}" for index in range(filter_count)), "Output"]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        # Keep Sidmat visually consistent with the current python_samba UI,
        # including GUI tests that construct MainWindow directly.
        apply_samba_theme(QtWidgets.QApplication.instance())
        self.setObjectName("sidmatMainWindow")
        self.setWindowTitle("python_sidmat — SiDiMaT")
        self._apply_startup_size()

        self.controller: Controller | None = None
        self.worker: _MeasurementWorker | None = None
        self._close_pending = False
        self._sample_frequency = 1000.0
        self._dark = False
        self._last_raw: MeasurementRawData | None = None
        self._last_pwelch = None
        self._offline_filtered = None
        self._offline_cl = None
        self._raw_cache: list[MeasurementRawData] = []
        self._added_cache_ids: set[int] = set()
        self._velocity_filter_count = DEFAULT_VELOCITY_FILTER_COUNT
        self._position_filter_count = DEFAULT_POSITION_FILTER_COUNT
        configure_filter_counts(
            velocity=self._velocity_filter_count,
            position=self._position_filter_count,
        )

        self._build_menus()
        self._build_statusbar()
        self._build_central()
        self._connect_signals()

        self._axis_timer = QtCore.QTimer(self)
        self._axis_timer.setInterval(1000)
        self._axis_timer.timeout.connect(self._refresh_axis_leds)

    def _apply_startup_size(self) -> None:
        """Pick a compact default size that fits the primary screen.

        High-DPI scaling shrinks the logical desktop, so a hard-coded
        1500x900 can overflow it.  Clamp the window to the available screen
        area while keeping a usable minimum.
        """
        screen = QtGui.QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
        w = min(1240, max(760, geo.width() - 24))
        h = min(780, max(560, geo.height() - 48))
        self.resize(w, h)
        self.setMinimumSize(720, 540)

    # ====================================================================
    # Menu bar
    # ====================================================================

    def _build_menus(self) -> None:
        menubar = self.menuBar()
        sys_menu = menubar.addMenu("系统")
        sys_menu.addAction("关于", self._show_about)
        sys_menu.addSeparator()
        sys_menu.addAction("退出", self.close)

    # ====================================================================
    # Status bar
    # ====================================================================

    def _build_statusbar(self) -> None:
        self.status_lbl = QtWidgets.QLabel(" Ready")
        self.status_lbl.setObjectName("statusMessage")
        self.status_lbl.setContentsMargins(6, 0, 0, 0)
        self.statusBar().addWidget(self.status_lbl, 1)

    # ====================================================================
    # Central widget
    # ====================================================================

    def _build_central(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("sidmatRoot")
        self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)

        # ---- Left column ------------------------------------------------
        left_wrap = QtWidgets.QWidget()
        left_wrap.setObjectName("sidmatSidebar")
        left_wrap.setMinimumWidth(340)
        left_lo = QtWidgets.QVBoxLayout(left_wrap)
        left_lo.setContentsMargins(0, 0, 0, 0)
        left_lo.setSpacing(0)

        self._build_left_toolbar(left_lo)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("sidmatScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        stack = QtWidgets.QWidget()
        stack.setObjectName("sidmatLeftStack")
        stack_lo = QtWidgets.QVBoxLayout(stack)
        stack_lo.setContentsMargins(4, 4, 4, 4)
        stack_lo.setSpacing(3)
        self._build_left_groups(stack_lo)
        stack_lo.addStretch(1)
        scroll.setWidget(stack)
        left_lo.addWidget(scroll, 1)

        # ---- Right panel: plot toolbar + mutually-exclusive views ---------
        # The original shows either the time-spec graph or the FRF/coherence
        # graph (TimeSpecBtn / FRFBtn toggle which is visible).
        right_wrap = QtWidgets.QWidget()
        right_wrap.setObjectName("sidmatWorkspace")
        right_wrap.setMinimumWidth(440)
        right_lo = QtWidgets.QVBoxLayout(right_wrap)
        right_lo.setContentsMargins(0, 0, 0, 0)
        right_lo.setSpacing(2)
        self._build_plot_toolbar(right_lo)

        self.time_plot = self._make_plot_widget("Time Spec")
        self.frf_plot = self._make_plot_widget("FRF  |H1| (dB)")
        self.phase_plot = self._make_plot_widget("FRF Phase")
        self.coh_plot = self._make_plot_widget("Coherence γ²")

        self.plot_stack = QtWidgets.QStackedWidget()
        self.plot_stack.addWidget(self.time_plot)                    # view 0
        frf_view = QtWidgets.QWidget()
        frf_lo = QtWidgets.QVBoxLayout(frf_view)
        frf_lo.setContentsMargins(0, 0, 0, 0)
        frf_lo.setSpacing(2)
        frf_lo.addWidget(self.frf_plot, 2)
        frf_lo.addWidget(self.phase_plot, 1)
        frf_lo.addWidget(self.coh_plot, 1)
        self.plot_stack.addWidget(frf_view)                          # view 1
        right_lo.addWidget(self.plot_stack, 1)

        # ---- Splitter: user-draggable left/right divide -----------------
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_wrap)
        splitter.addWidget(right_wrap)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 860])
        hbox.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    # Left column: top toolbar (emoji buttons)
    # ------------------------------------------------------------------

    def _build_left_toolbar(self, parent: QtWidgets.QVBoxLayout) -> None:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        row.setContentsMargins(2, 2, 2, 4)
        specs = [
            ("📊", "TimeSpec", self._toggle_time_plot),
            ("📉", "FRF", self._toggle_frf_plot),
            ("➕", "Cache data", self._cache_current),
            ("✂", "Clear", self._clear_measurement),
            ("+📄", "Add raw to plot", self._add_raw),
            ("📄", "Open raw .sidimat19x", self._open_raw),
            ("💾", "Save raw .sidimat19x", self._save_raw),
            ("AM", "Start measurement", self._start_measurement),
        ]
        for glyph, tip, slot in specs:
            btn = QtWidgets.QToolButton()
            btn.setObjectName("toolbarButton")
            btn.setText(glyph)
            btn.setToolTip(tip)
            btn.setFixedHeight(22)
            btn.setAutoRaise(False)
            if slot:
                btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        parent.addLayout(row)

    # ------------------------------------------------------------------
    # Left column: collapsible groups
    # ------------------------------------------------------------------

    def _build_left_groups(self, parent: QtWidgets.QVBoxLayout) -> None:
        self._build_conn_group(parent)
        self._build_excitation_group(parent)
        self._build_trace_group(parent)
        self._build_open_save_group(parent)
        self._build_system_group(parent)
        self._build_measuring_group(parent)
        self._build_convert_group(parent)
        self._build_help_group(parent)

    def _build_conn_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        conn = CollapsibleGroup("☏ Connection")
        self._connection_settings = QtCore.QSettings("python_samba", "SiDiMaT")

        # Backend selector on its own row; keeping the actions below prevents
        # the compact sidebar from clipping the Disconnect button.
        r = QtWidgets.QHBoxLayout()
        r.setSpacing(3)
        self.backend_cbx = QtWidgets.QComboBox()
        self.backend_cbx.addItems(["server", "serial", "mock"])
        backend = str(
            self._connection_settings.value("Connection/Backend", "server")
        )
        self.backend_cbx.setCurrentText(
            backend if backend in {"server", "serial", "mock"} else "server"
        )
        r.addWidget(QtWidgets.QLabel("Backend:"))
        r.addWidget(self.backend_cbx)
        r.addStretch(1)
        conn.addLayout(r)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(3)
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.setObjectName("primaryAction")
        actions.addWidget(self.connect_btn)
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        actions.addWidget(self.disconnect_btn)
        actions.addStretch(1)
        conn.addLayout(actions)

        # Port (editable combo: type or pick from the enumerated list) + baud.
        r2 = QtWidgets.QHBoxLayout()
        self.port_cbx = QtWidgets.QComboBox()
        self.port_cbx.setEditable(True)
        self.port_cbx.addItem(
            str(self._connection_settings.value("Connection/Port", "COM1"))
        )
        self.port_cbx.setFixedWidth(110)
        self.baud_cbx = QtWidgets.QComboBox()
        self.baud_cbx.addItems(["19200", "38400", "57600", "115200", "230400"])
        saved_baud = str(
            self._connection_settings.value("Connection/Baudrate", "57600")
        )
        self.baud_cbx.setCurrentText(
            saved_baud if self.baud_cbx.findText(saved_baud) >= 0 else "57600"
        )
        r2.addWidget(QtWidgets.QLabel("Port:"))
        r2.addWidget(self.port_cbx)
        r2.addWidget(QtWidgets.QLabel("Baud:"))
        r2.addWidget(self.baud_cbx)
        conn.addLayout(r2)

        server_row = QtWidgets.QHBoxLayout()
        self.server_endpoint_edit = QtWidgets.QLineEdit(
            str(
                self._connection_settings.value(
                    "Connection/Server", "127.0.0.1:47619"
                )
            )
        )
        server_row.addWidget(QtWidgets.QLabel("Server:"))
        server_row.addWidget(self.server_endpoint_edit, 1)
        conn.addLayout(server_row)

        self.discover_server_btn = QtWidgets.QPushButton("Discover Server")
        self.discover_server_btn.setToolTip(
            "Find Communication Servers on the local network and Tailscale"
        )
        self.discover_server_btn.clicked.connect(self._discover_server)
        conn.addWidget(self.discover_server_btn)

        self.update_ports_btn = QtWidgets.QPushButton("Update Comm Ports List")
        self.update_ports_btn.clicked.connect(self._update_ports)
        self.terminate_btn = QtWidgets.QPushButton(
            "Communication Server Status / Reopen"
        )
        self.terminate_btn.clicked.connect(self._terminate_and_connect)
        conn.addWidget(self.update_ports_btn)
        conn.addWidget(self.terminate_btn)

        self.server_status_lbl = QtWidgets.QLabel(
            "Shared mode: requests use a global FIFO; the last parameter write wins."
        )
        self.server_status_lbl.setWordWrap(True)
        self.server_status_lbl.setObjectName("sidebarText")
        conn.addWidget(self.server_status_lbl)

        # Firmware Version Info (inline, filled after connect).
        fw = QtWidgets.QGroupBox("Firmware Version Info")
        fw_lo = QtWidgets.QVBoxLayout(fw)
        fw_lo.setContentsMargins(6, 2, 6, 2)
        self.version_lbl = QtWidgets.QLabel("—")
        self.version_lbl.setWordWrap(True)
        self.version_lbl.setObjectName("sidebarText")
        fw_lo.addWidget(self.version_lbl)
        conn.addWidget(fw)

        # System Config Info (inline, filled after connect).
        sc = QtWidgets.QGroupBox("System Config Info")
        sc_lo = QtWidgets.QVBoxLayout(sc)
        sc_lo.setContentsMargins(6, 2, 6, 2)
        self.system_config_lbl = QtWidgets.QLabel("—")
        self.system_config_lbl.setWordWrap(True)
        self.system_config_lbl.setObjectName("sidebarText")
        sc_lo.addWidget(self.system_config_lbl)
        conn.addWidget(sc)

        self.sample_freq_lbl = QtWidgets.QLabel("Sample Freq: —")
        self.sample_freq_lbl.setObjectName("sidebarText")
        conn.addWidget(self.sample_freq_lbl)

        self.output_limit_lbl = QtWidgets.QLabel("Output Limit: —")
        self.output_limit_lbl.setObjectName("sidebarText")
        conn.addWidget(self.output_limit_lbl)

        self.system_info_btn = QtWidgets.QPushButton("System Config Info")
        self.system_info_btn.clicked.connect(self._show_system_info)
        conn.addWidget(self.system_info_btn)

        self.about_btn = QtWidgets.QPushButton("About")
        self.about_btn.clicked.connect(self._show_about)
        conn.addWidget(self.about_btn)
        parent.addWidget(conn)
        self.backend_cbx.currentTextChanged.connect(self._sync_backend_controls)
        self._sync_backend_controls(self.backend_cbx.currentText())

    def _sync_backend_controls(self, backend: str) -> None:
        physical = backend in {"server", "serial"}
        connected = bool(self.controller and self.controller.connected)
        self.backend_cbx.setEnabled(not connected)
        self.port_cbx.setEnabled(physical and not connected)
        self.baud_cbx.setEnabled(physical and not connected)
        self.server_endpoint_edit.setEnabled(backend == "server" and not connected)
        self.discover_server_btn.setEnabled(backend == "server" and not connected)
        self.update_ports_btn.setEnabled(physical and not connected)
        self.terminate_btn.setEnabled(backend == "server")

    def _on_disconnect_clicked(self) -> None:
        """Explicit Disconnect button."""
        if self._disconnect():
            self.connect_btn.blockSignals(True)
            self.connect_btn.setChecked(False)
            self.connect_btn.blockSignals(False)

    def _build_excitation_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        exc = CollapsibleGroup("🔊 Excitation / Diag")
        self.excitation_widget = ExcitationWidget()
        exc.addWidget(self.excitation_widget)

        diag = QtWidgets.QGroupBox("Diagnostic Signals")
        dlo = QtWidgets.QGridLayout(diag)
        dlo.setContentsMargins(3, 3, 3, 3)
        dlo.setHorizontalSpacing(3)
        dlo.setVerticalSpacing(2)
        from python_sidmat.backend.iosignal import IOType
        from python_sidmat.ui.io_signal_button import IOSignalButton
        dlo.addWidget(QtWidgets.QLabel("Diag0:"), 0, 0)
        self.diag0_btn = IOSignalButton(IOType(0, 0, 0))
        dlo.addWidget(self.diag0_btn, 0, 1)
        dlo.addWidget(QtWidgets.QLabel("Diag1:"), 1, 0)
        self.diag1_btn = IOSignalButton(IOType(0, 1, 0))
        dlo.addWidget(self.diag1_btn, 1, 1)
        self.diag_set_btn = QtWidgets.QPushButton("Set")
        self.diag_set_btn.clicked.connect(self._set_diagnostics)
        dlo.addWidget(self.diag_set_btn, 0, 2, 2, 1)
        exc.addWidget(diag)
        parent.addWidget(exc)

    def _build_trace_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        tr = CollapsibleGroup("📈 Trace Setting")
        self.trace_info = TraceInfoWidget()
        tr.addWidget(self.trace_info)

        cfg = QtWidgets.QGridLayout()
        cfg.setHorizontalSpacing(3)
        cfg.setVerticalSpacing(2)
        cfg.addWidget(QtWidgets.QLabel("Loop:"), 0, 0)
        self.loop_type_cbx = QtWidgets.QComboBox()
        self.loop_type_cbx.addItems(["Velocity", "Position"])
        cfg.addWidget(self.loop_type_cbx, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Meas:"), 0, 2)
        self.meas_type_cbx = QtWidgets.QComboBox()
        self.meas_type_cbx.addItems(
            _measurement_stage_labels(self._velocity_filter_count)
        )
        cfg.addWidget(self.meas_type_cbx, 0, 3)
        tr.addLayout(cfg)
        parent.addWidget(tr)

    def _build_open_save_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        os = CollapsibleGroup("📂💾 Open / Save Setting", collapsed=True)
        row = QtWidgets.QHBoxLayout()
        self.open_cfg_btn = QtWidgets.QPushButton("📂 Open")
        self.save_cfg_btn = QtWidgets.QPushButton("💾 Save")
        self.open_cfg_btn.clicked.connect(self._open_trace_config)
        self.save_cfg_btn.clicked.connect(self._save_trace_config)
        row.addWidget(self.open_cfg_btn)
        row.addWidget(self.save_cfg_btn)
        os.addLayout(row)
        parent.addWidget(os)

    def _build_system_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        sys = CollapsibleGroup("🔧 System Setting")
        sys.addWidget(QtWidgets.QLabel("Velocity Individual Loop Status"))
        self._build_velocity_leds(sys)
        parent.addWidget(sys)

    def _build_velocity_leds(self, group: CollapsibleGroup) -> None:
        """Six velocity-axis ON/OFF switches (clickable, write BSSTS).

        The original shows passive LEDs here; making them switches is more
        useful and stays in sync with the Measuring group toggles.
        """
        grid_w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(grid_w)
        g.setSpacing(2)
        self.axis_leds: list[QtWidgets.QPushButton] = []
        self.axis_buttons: list[QtWidgets.QPushButton] = []
        for i in range(6):
            g.addWidget(QtWidgets.QLabel(_AXIS_NAMES[i]), 0, i)
            btn = QtWidgets.QPushButton("OFF")
            btn.setObjectName("axisToggle")
            btn.setFixedSize(46, 22)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._on_axis_clicked(idx, checked))
            g.addWidget(btn, 1, i)
            self.axis_leds.append(btn)
            self.axis_buttons.append(btn)
        group.addWidget(grid_w)

    def _build_measuring_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        mt = CollapsibleGroup("📉 Measuring / Offline Tuner")

        row = QtWidgets.QGridLayout()
        row.setHorizontalSpacing(3)
        row.setVerticalSpacing(2)
        row.addWidget(QtWidgets.QLabel("Loop Type:"), 0, 0)
        self.mh_loop_cbx = QtWidgets.QComboBox()
        self.mh_loop_cbx.addItems(["Velocity", "Position"])
        row.addWidget(self.mh_loop_cbx, 0, 1)
        row.addWidget(QtWidgets.QLabel("Measure after:"), 0, 2)
        self.mh_stage_cbx = QtWidgets.QComboBox()
        self.mh_stage_cbx.addItems(
            _measurement_stage_labels(self._velocity_filter_count)
        )
        row.addWidget(self.mh_stage_cbx, 0, 3)
        mt.addLayout(row)

        # THH selection buttons route diagnostic signals; they are deliberately
        # separate from the System Setting loop switches above.
        ax_w = QtWidgets.QWidget()
        ag = QtWidgets.QGridLayout(ax_w)
        ag.setSpacing(2)
        self.mh_axis_buttons: list[QtWidgets.QPushButton] = []
        self.mh_axis_labels: list[QtWidgets.QLabel] = []
        self._mh_selected_axis = 0
        for i in range(12):
            base_row = (i // 6) * 2
            col = i % 6
            label = QtWidgets.QLabel(_VEL_AXIS_NAMES[i] if i < 6 else _POS_AXIS_NAMES[i])
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            ag.addWidget(label, base_row, col)
            btn = QtWidgets.QPushButton("Select" if i == 0 else "—")
            btn.setObjectName("axisToggle")
            btn.setFixedSize(44, 22)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, idx=i: self._on_mh_axis_clicked(idx, checked))
            ag.addWidget(btn, base_row + 1, col)
            self.mh_axis_labels.append(label)
            self.mh_axis_buttons.append(btn)
        mt.addWidget(ax_w)

        self.offline_cbx = QtWidgets.QCheckBox("Offline Tuner")
        mt.addWidget(self.offline_cbx)

        acc_gen = QtWidgets.QHBoxLayout()
        self.accept_btn = QtWidgets.QPushButton("Accept filter")
        self.accept_btn.clicked.connect(self._accept_offline_filter)
        acc_gen.addWidget(self.accept_btn)
        self.gen_ol_btn = QtWidgets.QPushButton("Generate CL")
        self.gen_ol_btn.setToolTip("Generate closed-loop TF from the filtered open-loop result")
        self.gen_ol_btn.clicked.connect(self._generate_offline_cl)
        acc_gen.addWidget(self.gen_ol_btn)
        mt.addLayout(acc_gen)
        parent.addWidget(mt)

    def _build_convert_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        cv = CollapsibleGroup("🔃 Convert to json", collapsed=True)
        row = QtWidgets.QHBoxLayout()
        self.xml2json_btn = QtWidgets.QPushButton("xml → json")
        self.xml2json_btn.clicked.connect(self._xml_to_json)
        self.check_dirs_btn = QtWidgets.QPushButton("Check double directives")
        self.check_dirs_btn.clicked.connect(self._check_double_directives)
        row.addWidget(self.xml2json_btn)
        row.addWidget(self.check_dirs_btn)
        cv.addLayout(row)
        parent.addWidget(cv)

    def _build_help_group(self, parent: QtWidgets.QVBoxLayout) -> None:
        hp = CollapsibleGroup("❓ Help", collapsed=True)
        hp.addWidget(QtWidgets.QLabel("python_sidmat — SiDiMaT reconstruction"))
        hp.addWidget(QtWidgets.QLabel("选 mock 后端 → Connect → 点 Start 测量 → 四图出曲线"))
        parent.addWidget(hp)

    # ------------------------------------------------------------------
    # Right panel: plot toolbar
    # ------------------------------------------------------------------

    def _build_plot_toolbar(self, parent: QtWidgets.QVBoxLayout) -> None:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(2)
        bar.setContentsMargins(2, 2, 2, 2)
        groups = [
            [("🔃", "Refresh all", self._refresh_all),
             ("▤", "Fullscreen", self._fullscreen_active_plot),
             ("📂F", "Open .idefigure", self._open_figure),
             ("💾F", "Save .idefigure", self._save_figure),
             ("📂", "Open image", self._open_image),
             ("💾", "Save image", self._save_image)],
            [("📋", "Copy to clipboard", self._copy_active_plot),
             ("🌷", "Snapshot", self._snapshot),
             ("▦ Grid", "Toggle grid", self._toggle_grid),
             ("▤", "Zoom fit", self._zoom_fit),
             ("❔ Help", "Help", self._show_help)],
            [("🌑", "Dark theme", lambda: self._set_theme(True)),
             ("🌓", "Light theme", lambda: self._set_theme(False))],
        ]
        for gi, group in enumerate(groups):
            if gi:
                sep = QtWidgets.QFrame()
                sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
                sep.setStyleSheet("color: #486a7d;")
                sep.setFixedHeight(22)
                bar.addWidget(sep)
            for glyph, tip, slot in group:
                btn = QtWidgets.QToolButton()
                btn.setObjectName("toolbarButton")
                btn.setText(glyph)
                btn.setToolTip(tip)
                btn.setFixedHeight(22)
                btn.setAutoRaise(False)
                if slot:
                    btn.clicked.connect(slot)
                bar.addWidget(btn)
        bar.addStretch(1)
        parent.addLayout(bar)

    def _make_plot_widget(self, title: str) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lo = QtWidgets.QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lbl = QtWidgets.QLabel(title)
        lbl.setObjectName("plotTitle")
        lbl.setContentsMargins(4, 1, 0, 1)
        lo.addWidget(lbl)
        import pyqtgraph as pg

        pw = pg.PlotWidget()
        pw.setObjectName("sidmatPlot")
        pw.setBackground("w")
        pw.showGrid(x=True, y=True, alpha=0.3)
        # Large traces are common on real controllers.  Let pyqtgraph reduce
        # off-screen/detail points instead of repainting every sample.
        pw.getPlotItem().setDownsampling(auto=True, mode="peak")
        pw.getPlotItem().setClipToView(True)
        legend = pw.addLegend(offset=(6, 6), labelTextSize="8pt")
        legend.setBrush(QtGui.QColor(255, 255, 255, 220))
        legend.setPen(QtGui.QColor("#c8d0d8"))
        legend.hide()
        if title == "Time Spec":
            pw.setLabel("bottom", "Time", units="s")
        else:
            pw.setLogMode(x=True, y=False)
            pw.setLabel("bottom", "Frequency", units="Hz")
        if title.startswith("Coherence"):
            pw.setLabel("left", "Coherence")
            pw.setYRange(0.0, 1.05, padding=0)
            pw.setLimits(yMin=0.0, yMax=1.05)
        lo.addWidget(pw)
        setattr(w, "_pw", pw)
        setattr(w, "_legend", legend)
        return w

    def _plot_widgets(self) -> list:
        return [self.time_plot, self.frf_plot, self.phase_plot, self.coh_plot]

    # ====================================================================
    # Signals
    # ====================================================================

    def _connect_signals(self) -> None:
        self.connect_btn.toggled.connect(self._on_connect_toggled)
        self.excitation_widget.applyRequested.connect(self._apply_excitation)
        self.excitation_widget.filterClicked.connect(self._open_filter_dialog)
        self.excitation_widget.filterUsageToggled.connect(self._toggle_noise_filter)
        # Single Start button lives inside TraceInfo; wire it to the engine.
        self.trace_info.startRequested.connect(self._start_measurement)
        self.trace_info.stopRequested.connect(self._stop_measurement)
        # Keep the two Loop-type selectors in sync (Trace Setting <-> Measuring).
        self.loop_type_cbx.currentIndexChanged.connect(self._on_loop_type_sync)
        self.mh_loop_cbx.currentIndexChanged.connect(self._on_mh_loop_sync)
        self.meas_type_cbx.currentIndexChanged.connect(self._on_trace_stage_sync)
        self.mh_stage_cbx.currentIndexChanged.connect(self._on_mh_stage_sync)
        self._on_mh_mode_changed(self.mh_loop_cbx.currentIndex())

    def _on_loop_type_sync(self, index: int) -> None:
        self.mh_loop_cbx.blockSignals(True)
        self.mh_loop_cbx.setCurrentIndex(index)
        self.mh_loop_cbx.blockSignals(False)
        self._on_mh_mode_changed(index)

    def _on_mh_loop_sync(self, index: int) -> None:
        self.loop_type_cbx.blockSignals(True)
        self.loop_type_cbx.setCurrentIndex(index)
        self.loop_type_cbx.blockSignals(False)
        self._on_mh_mode_changed(index)

    def _on_trace_stage_sync(self, index: int) -> None:
        self.mh_stage_cbx.blockSignals(True)
        self.mh_stage_cbx.setCurrentIndex(index)
        self.mh_stage_cbx.blockSignals(False)
        self._apply_mh_selection()

    def _on_mh_stage_sync(self, index: int) -> None:
        self.meas_type_cbx.blockSignals(True)
        self.meas_type_cbx.setCurrentIndex(index)
        self.meas_type_cbx.blockSignals(False)
        self._apply_mh_selection()

    def _update_measurement_stage_options(self, position: bool) -> None:
        count = (
            self._position_filter_count if position else self._velocity_filter_count
        )
        labels = _measurement_stage_labels(count)
        current = min(self.mh_stage_cbx.currentIndex(), len(labels) - 1)
        for combo in (self.meas_type_cbx, self.mh_stage_cbx):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(labels)
            combo.setCurrentIndex(max(0, current))
            combo.blockSignals(False)

    def _on_mh_mode_changed(self, index: int) -> None:
        """Switch the helping-hand labels between velocity and position axes."""
        position = int(index) == 1
        self._update_measurement_stage_options(position)
        names = _POS_AXIS_NAMES if position else _VEL_AXIS_NAMES
        visible_count = 12 if position else 6
        if self._mh_selected_axis >= visible_count:
            self._mh_selected_axis = 0
        for axis, (label, button) in enumerate(
            zip(self.mh_axis_labels, self.mh_axis_buttons)
        ):
            label.setText(names[axis] if axis < len(names) else "—")
            visible = axis < visible_count
            label.setVisible(visible)
            button.setVisible(visible)
            button.blockSignals(True)
            button.setChecked(visible and axis == self._mh_selected_axis)
            button.setText("Select" if visible and axis == self._mh_selected_axis else "—")
            button.blockSignals(False)
        self._apply_mh_selection()

    # ====================================================================
    # Connection
    # ====================================================================

    def _discover_server(self) -> None:
        last_server_id = str(
            self._connection_settings.value("Connection/ServerId", "")
        )
        selected = choose_communication_server(
            self, last_server_id=last_server_id
        )
        if selected is None:
            return
        self.backend_cbx.setCurrentText("server")
        self.server_endpoint_edit.setText(selected.endpoint)
        self.port_cbx.setEditText(str(selected.serial_port or ""))
        baud_text = str(selected.baudrate or 57600)
        if self.baud_cbx.findText(baud_text) >= 0:
            self.baud_cbx.setCurrentText(baud_text)
        self._connection_settings.setValue("Connection/ServerId", selected.server_id)
        self._connection_settings.setValue("Connection/Server", selected.endpoint)
        self._connection_settings.setValue("Connection/Port", selected.serial_port or "")
        self._connection_settings.setValue("Connection/Baudrate", baud_text)
        self.connect_btn.setChecked(True)

    def _on_connect_toggled(self, checked: bool) -> None:
        if checked:
            self._connect()
        else:
            if not self._disconnect():
                # Keep the visual state honest while a serial request is still
                # winding down; the controller must not be closed underneath
                # the worker.
                self.connect_btn.blockSignals(True)
                self.connect_btn.setChecked(True)
                self.connect_btn.blockSignals(False)

    def _connect(self) -> None:
        try:
            backend = self.backend_cbx.currentText()
            if backend == "mock":
                ctrl = Controller.connect_mock(readonly=False)
            elif backend == "server":
                baud = int(self.baud_cbx.currentText())
                ctrl = Controller.connect_server(
                    self.port_cbx.currentText().strip(),
                    baudrate=baud,
                    server=self.server_endpoint_edit.text().strip(),
                    auto_start=True,
                    readonly=False,
                )
            else:
                baud = int(self.baud_cbx.currentText())
                ctrl = Controller.connect(self.port_cbx.currentText().strip(), baudrate=baud)
        except Exception as exc:
            self.connect_btn.blockSignals(True)
            self.connect_btn.setChecked(False)
            self.connect_btn.blockSignals(False)
            QtWidgets.QMessageBox.critical(self, "Connect failed", str(exc))
            return
        self.controller = ctrl
        self._sync_backend_controls(backend)
        self._connection_settings.setValue("Connection/Backend", backend)
        self._connection_settings.setValue(
            "Connection/Port", self.port_cbx.currentText().strip()
        )
        self._connection_settings.setValue(
            "Connection/Baudrate", self.baud_cbx.currentText()
        )
        self._connection_settings.setValue(
            "Connection/Server", self.server_endpoint_edit.text().strip()
        )
        v = ctrl.version
        if v:
            raw = v.raw_info or ""
            # Vendor text (e.g. "FWCompiler: ... FWBldDate: ...") is kept
            # verbatim; bare numeric responses are formatted nicely.
            if any(ch.isalpha() for ch in raw):
                self.version_lbl.setText(raw)
            else:
                lines = [f"Firmware Version: {v.major}.{v.minor}.{v.patch}"]
                if v.lib:
                    lines.append(f"Lib Version: {v.lib}")
                if v.main_board is not None:
                    lines.append(f"Main Board: {v.main_board}")
                self.version_lbl.setText("\n".join(lines))
        else:
            self.version_lbl.setText("Firmware Version: —")
        # System config info (NGEXL).
        try:
            cfg = ctrl.get_system_config()
            self.system_config_lbl.setText("\n".join(cfg) if cfg else "—")
            self._apply_controller_filter_counts(cfg)
        except Exception:
            self.system_config_lbl.setText("—")
        # Safety: with a real controller the excitation drives actuators.
        # Surface the output limit so the user can confirm it before injecting.
        try:
            limit = ctrl.get_output_limit()
            self.output_limit_lbl.setText(f"Output Limit: {limit}%")
        except Exception:
            self.output_limit_lbl.setText("Output Limit: —")
        self.connect_btn.setChecked(True)
        self.disconnect_btn.setEnabled(True)
        self._refresh_controller()
        self._refresh_excitation_readback()
        self._axis_timer.start()
        self._refresh_axis_leds()
        if backend == "server" and isinstance(
            ctrl.session.transport, CommServerTransport
        ):
            try:
                state = ctrl.session.transport.status()
            except Exception as exc:
                self.status_lbl.setText("Connected via server")
                self.server_status_lbl.setText(f"Server status unavailable: {exc}")
            else:
                self.status_lbl.setText(
                    f"Connected via server · {state.get('client_count', 1)} client(s)"
                )
                self.server_status_lbl.setText(self._format_server_status(state))
        else:
            self.status_lbl.setText("Connected")

    def _disconnect(self) -> bool:
        if not self._stop_measurement():
            return False
        self._axis_timer.stop()
        if self.controller:
            try:
                self.controller.close()
            except Exception:
                pass
        self.controller = None
        self._sync_backend_controls(self.backend_cbx.currentText())
        self.version_lbl.setText("—")
        self.system_config_lbl.setText("—")
        self.sample_freq_lbl.setText("Sample Freq: —")
        self.output_limit_lbl.setText("Output Limit: —")
        self.server_status_lbl.setText(
            "Shared mode: requests use a global FIFO; the last parameter write wins."
        )
        self.disconnect_btn.setEnabled(False)
        self.status_lbl.setText("Disconnected")
        for led in self.axis_leds:
            led.setChecked(False)
            led.setText("OFF")
        return True

    def _update_ports(self) -> None:
        """Enumerate serial ports into the editable port combo."""
        try:
            from serial.tools import list_ports
            ports = sorted(p.device for p in list_ports.comports())
        except Exception:
            self.status_lbl.setText("Port scan unavailable")
            return
        current = self.port_cbx.currentText().strip()
        self.port_cbx.clear()
        self.port_cbx.addItems(ports if ports else ["COM1"])
        if current and current in ports:
            self.port_cbx.setCurrentText(current)
        elif ports:
            self.port_cbx.setCurrentText(ports[0])
        self.status_lbl.setText(f"{len(ports)} COM port(s) found" if ports
                                else "No COM ports found")

    def _terminate_and_connect(self) -> None:
        """Show the shared server and optionally reopen its physical serial."""
        temporary: CommServerTransport | None = None
        try:
            if self.controller and isinstance(
                self.controller.session.transport, CommServerTransport
            ):
                transport = self.controller.session.transport
            else:
                temporary = CommServerTransport(
                    CommServerConfig(
                        port=self.port_cbx.currentText().strip(),
                        baudrate=int(self.baud_cbx.currentText()),
                        endpoint=self.server_endpoint_edit.text().strip(),
                        auto_start=True,
                        client_name="python_sidmat-server-admin",
                    )
                )
                temporary.open()
                transport = temporary
            state = transport.status()
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Communication Server")
            box.setIcon(QtWidgets.QMessageBox.Information)
            box.setText(self._format_server_status(state))
            restart = box.addButton(
                "Reopen Serial Port", QtWidgets.QMessageBox.ActionRole
            )
            box.addButton(QtWidgets.QMessageBox.Close)
            box.exec()
            if box.clickedButton() is restart:
                state = transport.restart_serial()
                self.server_status_lbl.setText(self._format_server_status(state))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Communication Server", str(exc))
        finally:
            if temporary is not None:
                temporary.close()

    @staticmethod
    def _format_server_status(state: dict[str, object]) -> str:
        serial = state.get("serial")
        serial = serial if isinstance(serial, dict) else {}
        return (
            f"Serial {serial.get('port') or '—'} @ {serial.get('baudrate') or '—'} "
            f"({'open' if serial.get('open') else 'closed'}) · "
            f"clients {state.get('client_count', 0)} · "
            f"queue {state.get('queue_length', 0)} · "
            f"last {state.get('last_command') or '—'} "
            f"{float(state.get('last_duration_ms') or 0):.1f} ms"
        )

    def _port_owner_processes(self, port: str) -> list[tuple[str, int]]:
        """Return known owners of a COM port, when an owner tool is available.

        ``serial.tools.list_ports`` reports USB metadata, not a process owner.
        Treating a non-empty ``interface`` field as an owner used to block
        every normal USB serial connection, so this best-effort hook is kept
        conservative and returns no false positives.
        """
        return []

    def _refresh_controller(self) -> None:
        if not self.controller or not self.controller.connected:
            return
        try:
            fs = self.controller.get_sample_frequency()
            self._sample_frequency = fs or 1000.0
            self.sample_freq_lbl.setText(f"Sample Freq: {self._sample_frequency:.0f} Hz")
            self.trace_info.set_sample_frequency(fs)
            trace = self.controller.get_trace()
            # DGTIV has no average-count or fast-loading fields; keep those
            # UI-only choices when refreshing the controller-owned fields.
            trace.average_number = self.trace_info.trace.average_number
            trace.set_fast_data_loading(
                self.trace_info.trace.is_fast_data_loading
            )
            self.trace_info.apply_trace(trace)
        except Exception as exc:
            self.status_lbl.setText(f"read: {exc}")

    def _apply_controller_filter_counts(self, config: list[str]) -> None:
        """Apply NGEXL NumVelFilt/NumPosFilt to labels and stage selectors."""

        tokens: list[str] = []
        for item in config:
            tokens.extend(str(item).split())
        if len(tokens) <= 7:
            return
        try:
            velocity = int(tokens[6], 0)
            position = int(tokens[7], 0)
            configure_filter_counts(velocity=velocity, position=position)
        except (TypeError, ValueError):
            return
        self._velocity_filter_count = velocity
        self._position_filter_count = position
        self._update_measurement_stage_options(
            self.mh_loop_cbx.currentIndex() == 1
        )
        from python_sidmat.ui.io_signal_button import IOSignalButton

        for button in self.findChildren(IOSignalButton):
            button.refresh_signals()

    def _refresh_excitation_readback(self) -> None:
        if not self.controller or not self.controller.connected:
            return
        try:
            exc = self.controller.get_excitation()
            self.excitation_widget.apply_excitation(exc)
        except Exception as exc:
            self.status_lbl.setText(f"exc read: {exc}")
        try:
            inject = self.controller.get_noise_inject()
            self.excitation_widget.inject_btn.set_io(inject, emit=False)
        except Exception as exc:
            self.status_lbl.setText(f"inject read: {exc}")
        try:
            self.excitation_widget.offset_edit.setText(
                f"{self.controller.get_excitation_offset():g}"
            )
        except Exception:
            # DGEOV/DSEOV is an optional extended-excitation command.
            pass
        try:
            diag0, diag1 = self.controller.get_diagnostic_outputs()
            self.diag0_btn.set_io(diag0, emit=False)
            self.diag1_btn.set_io(diag1, emit=False)
        except Exception as exc:
            self.status_lbl.setText(f"diag read: {exc}")
        self._refresh_noise_filters()

    def _refresh_noise_filters(self) -> None:
        """Read DSNFU + DGNFS(0..3) and update the filter LED/buttons."""
        if not self.controller or not self.controller.connected:
            return
        try:
            usage = self.controller.get_noise_filter_usage()
            self.excitation_widget.set_filter_usage(
                str(usage).strip().upper() in (_NOISE_FILTER_ON, "ON", "1", "TRUE")
            )
        except Exception:
            pass
        try:
            stages = [
                self.controller.get_noise_filter_stage(i) for i in range(4)
            ]
            self.excitation_widget.apply_filters(stages)
        except Exception:
            pass

    def _open_filter_dialog(self, stage: int) -> None:
        """Open the filter configuration dialog (FilterDlg port) for one stage."""
        local_stages = self.excitation_widget.current_filters()
        current = local_stages[stage]
        try:
            if self.controller and self.controller.connected:
                current = self.controller.get_noise_filter_stage(stage)
        except Exception:
            # Editing remains useful for offline tuning/settings even when a
            # controller read is temporarily unavailable.
            pass
        cur_type = int(current.filter_type)
        cur_params = [float(p) for p in current.params]

        from python_sidmat.ui.filter_dialog import FilterDialog

        try:
            dlg = FilterDialog(stage, cur_type, cur_params, parent=self)
        except ValueError as exc:
            self.status_lbl.setText(f"Filter {stage + 1} cannot be edited: {exc}")
            return
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        from python_samba.protocol.commands import FilterStage

        filt = FilterStage(0, stage, dlg.filter_type_id, tuple(dlg.filter_params))
        local_stages[stage] = filt
        # Configuring a stage and enabling the chain are separate controls in
        # the legacy UI.  Do not unexpectedly turn excitation filtering on.
        usage = self.excitation_widget.filter_led.is_on()
        self.excitation_widget.apply_filters(local_stages)
        self.excitation_widget.set_filter_usage(usage)
        if self.controller and self.controller.connected:
            try:
                self.controller.set_noise_filter_stage(filt)
                self.controller.set_noise_filter_usage(
                    _NOISE_FILTER_ON if usage else _NOISE_FILTER_OFF
                )
            except Exception as exc:
                self.status_lbl.setText(f"Filter saved locally; controller write failed: {exc}")
                return
        from python_sidmat.measurement.filters import filter_name
        self.status_lbl.setText(
            f"Filter {stage + 1}: {filter_name(dlg.filter_type_id)}")

    def _toggle_noise_filter(self, on: bool) -> None:
        """LED click — toggle the noise filter chain (DSNFU)."""
        self.excitation_widget.set_filter_usage(on)
        if not self.controller or not self.controller.connected:
            self.status_lbl.setText(f"Offline noise filter {'ON' if on else 'OFF'}")
            return
        try:
            self.controller.set_noise_filter_usage(
                _NOISE_FILTER_ON if on else _NOISE_FILTER_OFF)
            self.status_lbl.setText(f"Noise filter {'ON' if on else 'OFF'}")
        except Exception as exc:
            self.excitation_widget.set_filter_usage(not on)
            self.status_lbl.setText(f"Filter fail: {exc}")

    def _refresh_axis_leds(self) -> None:
        """Sync only the System Setting loop switches from controller state."""
        if not self.controller or not self.controller.connected:
            return
        try:
            states = self.controller.get_axis_loop_states()
        except Exception:
            return
        for i, on in enumerate(states[:6]):
            btn = self.axis_leds[i]
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.setText("ON" if on else "OFF")
            btn.blockSignals(False)

    def _refresh_all(self) -> None:
        self._refresh_controller()
        self._refresh_excitation_readback()
        self._refresh_axis_leds()
        self.status_lbl.setText("Refreshed")

    # ====================================================================
    # Axis selection + loop toggle
    # ====================================================================

    def _on_axis_clicked(self, idx: int, checked: bool | None = None) -> None:
        """Toggle one velocity-axis loop in System Setting."""
        btn = self.axis_leds[idx]
        on = checked if checked is not None else btn.isChecked()
        if self.controller and self.controller.connected:
            try:
                self.controller.set_axis_loop_state(idx, on)
                self._refresh_axis_leds()
            except Exception as exc:
                self.status_lbl.setText(f"Axis {_AXIS_NAMES[idx]}: {exc}")
                btn.setChecked(not on)
                return
            self.status_lbl.setText(
                f"{_AXIS_NAMES[idx]} loop {'ON' if on else 'OFF'}")
        else:
            self.status_lbl.setText(f"Axis {idx + 1} selected")

    def _on_mh_axis_clicked(self, idx: int, checked: bool) -> None:
        """Select a measurement axis and route its diagnostic signals.

        These buttons intentionally never call ``set_axis_loop_state``.  The
        old Helping Hand selects the signal under test; loop enable/disable is
        a separate System Setting operation.
        """
        if not checked and idx == self._mh_selected_axis:
            button = self.mh_axis_buttons[idx]
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)
            return
        if checked:
            self._mh_selected_axis = int(idx)
            visible_count = 12 if self.mh_loop_cbx.currentIndex() == 1 else 6
            for axis, button in enumerate(self.mh_axis_buttons):
                if axis >= visible_count:
                    continue
                button.blockSignals(True)
                button.setChecked(axis == self._mh_selected_axis)
                button.setText("Select" if axis == self._mh_selected_axis else "—")
                button.blockSignals(False)
            self._apply_mh_selection()

    def _apply_mh_selection(self) -> None:
        """Apply the old Velocity/Position Helping Hand routing.

        The combo uses ``Raw`` at index 0, then Stage1…Stage7 and Output.  A
        selected stage therefore maps to the diagnostic signal sub-index by
        subtracting one, matching the original THH code.
        """
        if not hasattr(self, "mh_stage_cbx"):
            return
        if getattr(self, "_suspend_hardware_routes", False):
            return
        axis = int(getattr(self, "_mh_selected_axis", 0))
        position = self.mh_loop_cbx.currentIndex() == 1
        max_axis = 12 if position else 6
        axis = max(0, min(axis, max_axis - 1))
        # Raw is wire sub-index -1, stages are 0..N-1, output is N.
        stage_subindex = self.mh_stage_cbx.currentIndex() - 1
        current_diag0 = self.diag0_btn.io_type()
        diag0 = IOType(3, current_diag0.main_index, current_diag0.sub_index)
        if position:
            diag1 = IOType(5, axis, stage_subindex)
            # Position excitation is always injected after the configured
            # position-filter chain; only the diagnostic tap follows the
            # selected measurement stage (legacy PositionTHH semantics).
            inject = IOType(5, axis, self._position_filter_count)
            axis_name = _POS_AXIS_NAMES[axis]
        else:
            diag1 = IOType(2, axis, stage_subindex)
            # VelocityTHH changes only Type/MainIndex in the original code;
            # preserve the controller's existing sub-index (some live units
            # report non-zero values here).
            current_inject = self.excitation_widget.inject_btn.io_type()
            inject = IOType(4, axis, current_inject.sub_index)
            axis_name = _VEL_AXIS_NAMES[axis]

        if not self.controller or not self.controller.connected:
            self.status_lbl.setText(f"Selected {axis_name}; connect to route signals")
            return
        try:
            self.controller.set_diagnostic_outputs(diag0, diag1)
            self.controller.set_noise_inject(inject)
            self.diag0_btn.set_io(diag0, emit=False)
            self.diag1_btn.set_io(diag1, emit=False)
            self.excitation_widget.inject_btn.set_io(inject, emit=False)
        except Exception as exc:
            self.status_lbl.setText(f"Helping Hand: {exc}")
            return
        self.status_lbl.setText(
            f"{('Position' if position else 'Velocity')} {axis_name} → "
            f"{self.mh_stage_cbx.currentText()}"
        )

    # ====================================================================
    # Excitation + diagnostics
    # ====================================================================

    def _apply_excitation(self) -> None:
        """Set button → DSESP + DSNIP + optional extended offset."""
        if not self.controller or not self.controller.connected:
            self.status_lbl.setText("Not connected")
            return
        try:
            exc = self.excitation_widget.current_excitation()
        except ValueError as exc_err:
            self.status_lbl.setText(f"Excitation parameters invalid: {exc_err}")
            return
        try:
            self.controller.set_excitation(exc)
            self.controller.set_noise_inject(exc.noise_injection_io)
        except Exception as exc_err:
            self.status_lbl.setText(f"Exc fail: {exc_err}")
            return
        try:
            # Zero is a real write: it must clear a previously configured
            # controller offset instead of leaving stale hardware state.
            self.controller.set_excitation_offset(exc.offset)
        except Exception as offset_exc:
            if abs(float(exc.offset)) > 1e-15:
                self.status_lbl.setText(
                    f"Excitation set; offset unsupported: {offset_exc}")
                return
        self.status_lbl.setText("Excitation set")

    def _set_diagnostics(self) -> None:
        if not self.controller or not self.controller.connected:
            self.status_lbl.setText("Not connected")
            return
        io0 = self.diag0_btn.io_type()
        io1 = self.diag1_btn.io_type()
        try:
            self.controller.set_diagnostic_outputs(io0, io1)
            self.status_lbl.setText(
                f"Diag0={io0.name} Diag1={io1.name}")
        except Exception as exc:
            self.status_lbl.setText(f"Diag fail: {exc}")

    # ====================================================================
    # Measurement
    # ====================================================================

    def _start_measurement(self) -> None:
        if not self.controller or not self.controller.connected:
            self.status_lbl.setText("Connect first")
            return
        if self.worker and self.worker.isRunning():
            return
        try:
            trace = self.trace_info.current_trace()
        except ValueError as exc:
            self.status_lbl.setText(f"Trace parameters invalid: {exc}")
            return
        fs_edit = self.trace_info.sample_frequency()
        if fs_edit:
            self._sample_frequency = fs_edit
        # Send the trace parameters to the controller before measuring; the
        # original software calls SetTraceInformationValues on any change.
        self._axis_timer.stop()
        try:
            self.controller.set_trace(trace)
        except Exception as exc:
            self._axis_timer.start()
            self.status_lbl.setText(f"Trace write failed: {exc}")
            return
        self.trace_info.set_measuring(True)
        self.status_lbl.setText("Measuring...")
        self.worker = _MeasurementWorker(
            self.controller, trace, self._sample_frequency, parent=self,
        )
        self.worker.finishedOk.connect(self._on_measurement_done)
        self.worker.failed.connect(self._on_measurement_failed)
        self.worker.cancelled.connect(self._on_measurement_cancelled)
        self.worker.progress.connect(self._on_measurement_progress)
        self.worker.averageComplete.connect(self._on_average_complete)
        self.worker.start()

    def _stop_measurement(self, wait_ms: int = 6000) -> bool:
        """Stop the worker before touching its controller.

        A QThread must not be discarded while it is still using the serial
        session.  The old code did that after a fixed two-second wait, which
        could close the port underneath an in-flight request.  Return False
        when the request is still in progress so callers can retry safely.
        """
        worker = self.worker
        if worker is not None:
            if worker.isRunning():
                worker.stop()
                if not worker.wait(wait_ms):
                    self.status_lbl.setText("Stopping measurement...")
                    return False
            self.worker = None
        self.trace_info.set_measuring(False)
        return True

    def _on_measurement_done(self, raw) -> None:
        if self.controller and self.controller.connected:
            self._axis_timer.start()
        self.trace_info.set_measuring(False)
        self.status_lbl.setText("Done")
        self.worker = None
        self._last_raw = raw
        ch0 = raw.channel(0)
        ch1 = raw.channel(1)
        if raw.sample_num <= 0 or len(ch0) == 0:
            self.status_lbl.setText("No data acquired (DASTA rejected?)")
            return
        fs = raw.effective_sample_rate or self._sample_frequency
        nfft = self._pick_nfft(len(ch0))
        try:
            result = pwelch(ch0, ch1, WindowType.HANNING, 50, nfft, len(ch0), fs)
        except Exception as exc:
            self.status_lbl.setText(f"pw: {exc}")
            return
        self._update_plots(
            ch0,
            ch1,
            fs,
            result,
            raw.sig_name[0] if raw.sig_name else "Ch0",
            raw.sig_name[1] if len(raw.sig_name) > 1 else "Ch1",
        )

    def _on_measurement_failed(self, msg: str) -> None:
        if self.controller and self.controller.connected:
            self._axis_timer.start()
        self.trace_info.set_measuring(False)
        self.worker = None
        self.status_lbl.setText(f"Fail: {msg}")

    def _on_measurement_cancelled(self) -> None:
        if self.controller and self.controller.connected:
            self._axis_timer.start()
        self.trace_info.set_measuring(False)
        self.worker = None
        self.status_lbl.setText("Measurement cancelled")

    def _on_measurement_progress(self, current: int, total: int) -> None:
        self.status_lbl.setText(f"Measuring average {current + 1}/{total}…")

    def _on_average_complete(self, current: int, total: int, samples: int) -> None:
        self.status_lbl.setText(
            f"Average {current}/{total} complete ({samples} samples)")

    def _update_plots(
        self,
        ch0,
        ch1,
        fs,
        result,
        name0: str = "Ch0",
        name1: str = "Ch1",
    ) -> None:
        self._last_pwelch = result
        self._offline_filtered = None
        self._offline_cl = None
        for view in (self.frf_plot, self.phase_plot, self.coh_plot):
            view._legend.hide()
        freq = result.freq
        mask = freq > 0

        pw = self.time_plot._pw
        pw.clear()
        dt = 1.0 / fs if fs else 1.0
        t = np.arange(len(ch0), dtype=float) * dt
        pw.plot(t, ch0, pen="b", name=name0 or "Ch0")
        pw.plot(t, ch1, pen="r", name=name1 or "Ch1")
        self.time_plot._legend.show()
        pw.setLabel("bottom", "Time", units="s")

        pw2 = self.frf_plot._pw
        pw2.clear()
        mag_db = 20.0 * np.log10(np.maximum(result.amplitude, 1e-30))
        pw2.plot(freq[mask], mag_db[mask], pen="b", name="|H1| (dB)")
        pw2.setLabel("bottom", "Frequency", units="Hz")
        pw2.setLabel("left", "Mag (dB)")

        pw3 = self.phase_plot._pw
        pw3.clear()
        pw3.plot(freq[mask], result.phase_deg[mask], pen="b", name="Phase")
        pw3.setLabel("bottom", "Frequency", units="Hz")
        pw3.setLabel("left", "Phase (deg)")

        pw4 = self.coh_plot._pw
        pw4.clear()
        pw4.plot(freq[mask], result.coherence[mask], pen="b", name="γ²")
        pw4.setLabel("bottom", "Frequency", units="Hz")
        pw4.setLabel("left", "Coherence")
        pw4.setYRange(0.0, 1.05, padding=0)
        self._set_theme(self._dark)

    def _accept_offline_filter(self) -> None:
        """Apply the four controller filter stages to the last measured TF."""
        result = self._last_pwelch
        if result is None:
            self.status_lbl.setText("Measure or open a raw file first")
            return
        stages = self.excitation_widget.current_filters()
        source = "local"
        if self.controller and self.controller.connected:
            try:
                stages = [self.controller.get_noise_filter_stage(i) for i in range(4)]
            except Exception:
                # A transient read error must not make offline tuning unusable;
                # the four last-known stages are maintained by the widget.
                pass
            else:
                source = "controller"
                self.excitation_widget.apply_filters(stages)
        try:
            self._offline_filtered = apply_filter_chain(
                result.freq,
                result.re + 1j * result.im,
                stages,
            )
        except Exception as exc:
            self.status_lbl.setText(f"Offline filter failed: {exc}")
            return
        self._offline_cl = None
        self._redraw_offline_tf()
        self.status_lbl.setText(f"Offline filter accepted ({source} settings)")

    def _generate_offline_cl(self) -> None:
        """Generate the legacy closed-loop TF from filtered open-loop data."""
        if self._last_pwelch is None:
            self.status_lbl.setText("Measure or open a raw file first")
            return
        if self._offline_filtered is None:
            self._accept_offline_filter()
            if self._offline_filtered is None:
                return
        self._offline_cl = generate_closed_loop(self._offline_filtered)
        self._redraw_offline_tf()
        self.status_lbl.setText("Closed-loop TF generated")

    def _redraw_offline_tf(self) -> None:
        """Overlay filtered OL/CL curves while keeping the original result."""
        result = self._last_pwelch
        if result is None:
            return
        freq = np.asarray(result.freq)
        mask = freq > 0
        pw = self.frf_plot._pw
        pw.clear()
        original = result.re + 1j * result.im
        pw.plot(
            freq[mask],
            20.0 * np.log10(np.maximum(np.abs(original[mask]), 1e-30)),
            pen="b",
            name="Original |H1| (dB)",
        )
        if self._offline_filtered is not None:
            pw.plot(
                freq[mask],
                20.0 * np.log10(np.maximum(np.abs(self._offline_filtered[mask]), 1e-30)),
                pen="g",
                name="Filtered OL (dB)",
            )
        if self._offline_cl is not None:
            pw.plot(
                freq[mask],
                20.0 * np.log10(np.maximum(np.abs(self._offline_cl[mask]), 1e-30)),
                pen=QtGui.QPen(QtGui.QColor("#d62728"), 1, QtCore.Qt.PenStyle.DashLine),
                name="Closed loop (dB)",
            )
        self.frf_plot._legend.show()
        pw.setLabel("bottom", "Frequency", units="Hz")
        pw.setLabel("left", "Mag (dB)")

        phase = self.phase_plot._pw
        phase.clear()
        phase.plot(freq[mask], np.angle(original[mask], deg=True), pen="b", name="Original phase")
        if self._offline_filtered is not None:
            phase.plot(
                freq[mask],
                np.angle(self._offline_filtered[mask], deg=True),
                pen="g",
                name="Filtered OL phase",
            )
        if self._offline_cl is not None:
            phase.plot(
                freq[mask],
                np.angle(self._offline_cl[mask], deg=True),
                pen=QtGui.QPen(QtGui.QColor("#d62728"), 1, QtCore.Qt.PenStyle.DashLine),
                name="Closed loop phase",
            )
        self.phase_plot._legend.show()
        phase.setLabel("bottom", "Frequency", units="Hz")
        phase.setLabel("left", "Phase (deg)")
        self._set_theme(self._dark)

    @staticmethod
    def _pick_nfft(n: int) -> int:
        if n < 2:
            raise ValueError("at least two samples are required for FRF analysis")
        m = 1
        while m * 2 <= n:
            m *= 2
        return max(2, min(m, 4096))

    # ====================================================================
    # Data cache / raw file I/O
    # ====================================================================

    def _cache_current(self) -> None:
        if self._last_raw is None:
            self.status_lbl.setText("No measurement to cache")
            return
        if all(raw is not self._last_raw for raw in self._raw_cache):
            self._raw_cache.append(self._last_raw)
        self.status_lbl.setText(f"Cached (#{len(self._raw_cache)})")

    def _add_raw(self) -> None:
        if not self._raw_cache:
            self.status_lbl.setText("Cache is empty — measure first")
            return
        raw = next(
            (
                item
                for item in self._raw_cache
                if item is not self._last_raw
                and id(item) not in self._added_cache_ids
            ),
            None,
        )
        if raw is None:
            self.status_lbl.setText("All cached raw data is already plotted")
            return
        self._added_cache_ids.add(id(raw))
        pw = self.time_plot._pw
        ch0 = raw.channel(0)
        ch1 = raw.channel(1)
        fs = raw.effective_sample_rate or self._sample_frequency
        dt = 1.0 / fs if fs else 1.0
        base = 0.0
        for it in pw.plotItem.items:
            x = getattr(it, "xData", None)
            if x is not None and len(x):
                base = max(base, float(x[-1]) + dt)
        t = base + np.arange(len(ch0), dtype=float) * dt
        pw.plot(t, ch0, pen="g", name="Cached Ch0")
        pw.plot(t, ch1, pen="m", name="Cached Ch1")
        self.time_plot._legend.show()
        self._set_theme(self._dark)
        self.status_lbl.setText("Added cached raw to time plot")

    def _open_raw(self) -> None:
        """Open a .sidimat19x file (original MATLAB .mat format)."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open raw measurement",
            "", "SiDiMaT19x raw data (*.sidimat19x);;All files (*.*)")
        if not path:
            return
        try:
            raws = load_sidimat_raw(path)
        except Exception as exc:
            self.status_lbl.setText(f"Open failed: {exc}")
            return
        if not raws:
            self.status_lbl.setText("No measurements in file")
            return
        self._show_rawfile(raws[0])
        self._last_raw = raws[0].to_raw()
        # The first entry is already displayed; keep only the remaining
        # measurements in the add/cache queue to avoid duplicate saves.
        self._raw_cache = [rf.to_raw() for rf in raws[1:]]
        self._added_cache_ids.clear()
        self.status_lbl.setText(f"Opened {path} ({len(raws)} measurement(s))")

    def _save_raw(self) -> None:
        """Save to a .sidimat19x file (original MATLAB .mat format)."""
        if self._last_raw is None and not self._raw_cache:
            self.status_lbl.setText("Nothing to save — measure first")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save raw measurement", "measurement.sidimat19x",
            "SiDiMaT19x raw data (*.sidimat19x);;All files (*.*)")
        if not path:
            return
        if not path.lower().endswith(".sidimat19x"):
            path += ".sidimat19x"
        entries: list[MeasurementRawData] = []
        if self._last_raw is not None:
            entries.append(self._last_raw)
        entries.extend(raw for raw in self._raw_cache if raw is not self._last_raw)
        try:
            save_sidimat_raw(entries, path)
        except Exception as exc:
            self.status_lbl.setText(f"Save failed: {exc}")
            return
        self.status_lbl.setText(f"Saved {path} ({len(entries)} measurement(s))")

    def _show_rawfile(self, rf) -> None:
        """Plot a loaded RawFile's two channels in the time plot."""
        self._last_pwelch = None
        self._offline_filtered = None
        self._offline_cl = None
        ch0 = np.asarray(rf.ch0, dtype=float)
        ch1 = np.asarray(rf.ch1, dtype=float)
        n = max(len(ch0), len(ch1))
        if n == 0:
            for view in (self.time_plot, self.frf_plot, self.phase_plot, self.coh_plot):
                view._pw.clear()
                view._legend.hide()
            self.status_lbl.setText("Loaded file contains no samples")
            return
        if len(ch0) != n:
            ch0 = np.pad(ch0, (0, n - len(ch0)))
        if len(ch1) != n:
            ch1 = np.pad(ch1, (0, n - len(ch1)))
        fs = (rf.sample_rate / max(1, rf.undersample)) if rf.sample_rate else self._sample_frequency
        # Opening a raw file must refresh the FRF plots as well; otherwise the
        # old measurement remained visible beside the newly loaded waveform.
        if n >= 8:
            try:
                nfft = self._pick_nfft(n)
                result = pwelch(ch0, ch1, WindowType.HANNING, 50, nfft, n, fs)
            except Exception:
                result = None
            if result is not None:
                self._update_plots(
                    ch0,
                    ch1,
                    fs,
                    result,
                    rf.sig0_name or "Ch0",
                    rf.sig1_name or "Ch1",
                )
                return
        for view in (self.frf_plot, self.phase_plot, self.coh_plot):
            view._pw.clear()
            view._legend.hide()
        pw = self.time_plot._pw
        pw.clear()
        dt = 1.0 / fs if fs else 1.0
        t = np.arange(n, dtype=float) * dt
        pw.plot(t, ch0, pen="b", name=rf.sig0_name or "Ch0")
        pw.plot(t, ch1, pen="r", name=rf.sig1_name or "Ch1")
        self.time_plot._legend.show()
        pw.setLabel("bottom", "Time", units="s")
        self._set_theme(self._dark)

    def _open_trace_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open measurement setting",
            "",
            "Sidmat settings (*.sidmat.json *.json);;Trace config (*.cfg *.txt *.csv)",
        )
        if not path:
            return
        if path.lower().endswith(".json"):
            try:
                payload = load_measurement_settings(path)
                self._apply_measurement_settings(payload)
            except Exception as exc:
                self.status_lbl.setText(f"Load settings failed: {exc}")
                return
            self.status_lbl.setText(f"Loaded {path}")
            return
        try:
            # Import into a detached copy so malformed files and failed
            # controller writes cannot half-modify the live editor model.
            trace = replace(self.trace_info.current_trace())
            import_trace_config(trace, path)
            if self.controller and self.controller.connected:
                self.controller.set_trace(trace)
        except Exception as exc:
            self.status_lbl.setText(f"Load failed: {exc}")
            return
        self.trace_info.apply_trace(trace)
        self.status_lbl.setText(f"Loaded {path}")

    def _save_trace_config(self) -> None:
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save measurement setting",
            "measurement.sidmat.json",
            "Sidmat settings (*.sidmat.json *.json);;Trace config (*.cfg)",
        )
        if not path:
            return
        lower_path = path.lower()
        if not lower_path.endswith((".json", ".cfg", ".txt", ".csv")):
            path += ".cfg" if selected_filter.startswith("Trace config") else ".sidmat.json"
        if path.lower().endswith(".json"):
            try:
                save_measurement_settings(self._measurement_settings_payload(), path)
            except Exception as exc:
                self.status_lbl.setText(f"Save settings failed: {exc}")
                return
            self.status_lbl.setText(f"Saved {path}")
            return
        try:
            export_trace_config(self.trace_info.current_trace(), path)
        except Exception as exc:
            self.status_lbl.setText(f"Save failed: {exc}")
            return
        self.status_lbl.setText(f"Saved {path}")

    def _measurement_settings_payload(self) -> dict:
        trace = self.trace_info.current_trace()
        exc = self.excitation_widget.current_excitation()
        filter_usage = bool(self.excitation_widget.filter_led.is_on())
        stages = self.excitation_widget.current_filters()
        if self.controller and self.controller.connected:
            try:
                filter_usage = str(self.controller.get_noise_filter_usage()).strip().upper() in (
                    _NOISE_FILTER_ON, "ON", "1", "TRUE"
                )
                stages = [
                    self.controller.get_noise_filter_stage(index) for index in range(4)
                ]
            except Exception:
                # Preserve the last-known/local configuration when a live
                # readback is unavailable instead of saving an empty chain.
                pass
            else:
                self.excitation_widget.apply_filters(stages)
                self.excitation_widget.set_filter_usage(filter_usage)
        filters = [
            {
                "stage": index,
                "type": int(stage.filter_type),
                "params": [float(value) for value in stage.params],
            }
            for index, stage in enumerate(stages[:4])
        ]
        return {
            "trace": {
                "ch0": list(trace.trace_ch0.encode()),
                "ch1": list(trace.trace_ch1.encode()),
                "undersamples": int(trace.undersamples),
                "no_samples": int(trace.no_samples),
                "trace_filter_flag": int(trace.trace_filter_flag),
                "average_number": int(trace.average_number),
                "fast_data_loading": bool(trace.is_fast_data_loading),
            },
            "excitation": {
                "type": int(exc.type),
                "params": [float(value) for value in exc.params],
                "offset": float(exc.offset),
                "inject": list(exc.noise_injection_io.encode()),
            },
            "diagnostic": {
                "diag0": list(self.diag0_btn.io_type().encode()),
                "diag1": list(self.diag1_btn.io_type().encode()),
            },
            "noise_filters": {"usage": filter_usage, "stages": filters},
            "helping_hand": {
                "loop_type": int(self.mh_loop_cbx.currentIndex()),
                "stage": int(self.mh_stage_cbx.currentIndex()),
                "axis": int(self._mh_selected_axis),
            },
        }

    def _apply_measurement_settings(self, payload: dict) -> None:
        from python_samba.protocol.commands import FilterStage
        from python_sidmat.measurement.excitation import ExcitationParameters
        from python_sidmat.measurement.filters import FILTER_TYPES

        def parse_io(value, label: str) -> IOType:
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError(f"{label} must contain exactly three integers")
            return IOType(*(int(item) for item in value))

        def parse_bool(value, label: str) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in (
                "true", "false", "1", "0", "on", "off",
            ):
                return value.strip().lower() in ("true", "1", "on")
            raise ValueError(f"{label} must be a boolean")

        trace_data = payload.get("trace", {})
        if not isinstance(trace_data, dict):
            raise ValueError("trace settings must be an object")
        base_trace = self.trace_info.current_trace()
        fast_loading = parse_bool(
            trace_data.get("fast_data_loading", base_trace.is_fast_data_loading),
            "trace.fast_data_loading",
        )
        trace = replace(
            base_trace,
            trace_ch0=parse_io(
                trace_data.get("ch0", base_trace.trace_ch0.encode()), "trace.ch0"
            ),
            trace_ch1=parse_io(
                trace_data.get("ch1", base_trace.trace_ch1.encode()), "trace.ch1"
            ),
            undersamples=int(trace_data.get("undersamples", base_trace.undersamples)),
            no_samples=int(trace_data.get("no_samples", base_trace.no_samples)),
            trace_filter_flag=int(
                trace_data.get("trace_filter_flag", base_trace.trace_filter_flag)
            ),
            average_number=int(
                trace_data.get("average_number", base_trace.average_number)
            ),
            is_fast_data_loading=fast_loading,
        )
        trace.set_fast_data_loading(fast_loading)
        trace.validate()

        exc_data = payload.get("excitation", {})
        if not isinstance(exc_data, dict):
            raise ValueError("excitation settings must be an object")
        current_exc = ExcitationParameters(
            type=int(exc_data.get("type", 0)),
            params=[float(value) for value in exc_data.get("params", [0, 0, 0, 0])[:4]],
            offset=float(exc_data.get("offset", 0.0)),
            noise_injection_io=parse_io(
                exc_data.get("inject", [3, 0, 0]), "excitation.inject"
            ),
        )

        diag_data = payload.get("diagnostic", {})
        if not isinstance(diag_data, dict):
            raise ValueError("diagnostic settings must be an object")
        diag0 = parse_io(diag_data.get("diag0", [0, 0, 0]), "diagnostic.diag0")
        diag1 = parse_io(diag_data.get("diag1", [0, 1, 0]), "diagnostic.diag1")

        helping = payload.get("helping_hand", {})
        if not isinstance(helping, dict):
            raise ValueError("helping_hand settings must be an object")
        loop_index = max(0, min(1, int(helping.get("loop_type", 0))))
        helping_stage = int(helping.get("stage", 0))
        helping_axis = max(0, min(11, int(helping.get("axis", 0))))

        filter_data = payload.get("noise_filters", {})
        if not isinstance(filter_data, dict):
            raise ValueError("noise_filters settings must be an object")
        usage = parse_bool(
            filter_data.get("usage", self.excitation_widget.filter_led.is_on()),
            "noise_filters.usage",
        )
        stage_data = filter_data.get("stages", [])
        if not isinstance(stage_data, list):
            raise ValueError("noise_filters.stages must be a list")
        stages = self.excitation_widget.current_filters()
        for index, item in enumerate(stage_data[:4]):
            if not isinstance(item, dict):
                raise ValueError(f"noise_filters.stages[{index}] must be an object")
            filter_type = int(item.get("type", 0))
            if not 0 <= filter_type < len(FILTER_TYPES):
                raise ValueError(f"unsupported filter type {filter_type} at stage {index + 1}")
            params = [
                float(value)
                for value in item.get("params", [0, 0, 0, 0, 0])[:5]
            ]
            params.extend([0.0] * (5 - len(params)))
            if not all(np.isfinite(params)):
                raise ValueError(f"filter stage {index + 1} parameters must be finite")
            stages[index] = FilterStage(
                0,
                index,
                filter_type,
                tuple(params),
            )

        # Only mutate visible/live state after every section has parsed and
        # validated successfully.
        self._suspend_hardware_routes = True
        try:
            self.trace_info.apply_trace(trace)
            self.excitation_widget.apply_excitation(current_exc)
            self.diag0_btn.set_io(diag0, emit=False)
            self.diag1_btn.set_io(diag1, emit=False)
            self.loop_type_cbx.setCurrentIndex(loop_index)
            self._mh_selected_axis = helping_axis
            self._on_mh_mode_changed(loop_index)
            self.mh_stage_cbx.setCurrentIndex(
                max(0, min(self.mh_stage_cbx.count() - 1, helping_stage))
            )
            self.excitation_widget.apply_filters(stages)
            self.excitation_widget.set_filter_usage(usage)
        finally:
            self._suspend_hardware_routes = False

        if self.controller and self.controller.connected:
            self.controller.set_trace(trace)
            self.controller.set_excitation(current_exc)
            self.controller.set_noise_inject(current_exc.noise_injection_io)
            try:
                self.controller.set_excitation_offset(current_exc.offset)
            except Exception:
                if abs(current_exc.offset) > 1e-15:
                    raise
            self.controller.set_diagnostic_outputs(diag0, diag1)
            for stage in stages:
                self.controller.set_noise_filter_stage(stage)
            self.controller.set_noise_filter_usage(
                _NOISE_FILTER_ON if usage else _NOISE_FILTER_OFF
            )

    # ====================================================================
    # Convert to json
    # ====================================================================

    def _xml_to_json(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open XML", "", "XML (*.xml)")
        if not path:
            return
        try:
            tree = ET.parse(path)
        except Exception as exc:
            self.status_lbl.setText(f"XML parse failed: {exc}")
            return
        data = self._etree_to_json(tree.getroot())
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save JSON", os.path.splitext(path)[0] + ".json", "JSON (*.json)")
        if not out_path:
            return
        if not out_path.lower().endswith(".json"):
            out_path += ".json"
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.status_lbl.setText(f"JSON save failed: {exc}")
            return
        self.status_lbl.setText(f"Converted {path} → {out_path}")

    @staticmethod
    def _etree_to_json(el: ET.Element) -> dict:
        node: dict[str, object] = {"tag": el.tag}
        if el.attrib:
            node["attributes"] = dict(el.attrib)
        children = list(el)
        if children:
            node["children"] = [MainWindow._etree_to_json(c) for c in children]
        elif el.text and el.text.strip():
            node["text"] = el.text.strip()
        return node

    def _check_double_directives(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open XML to check", "", "XML (*.xml)")
        if not path:
            return
        try:
            tree = ET.parse(path)
        except Exception as exc:
            self.status_lbl.setText(f"XML parse failed: {exc}")
            return
        from collections import Counter
        tags = [el.tag for el in tree.iter()]
        dupes = {tag: count for tag, count in Counter(tags).items() if count > 1}
        if dupes:
            lines = "\n".join(f"  {tag}: {count}×" for tag, count in dupes.items())
            QtWidgets.QMessageBox.warning(
                self, "Duplicate directives", f"重复的指令元素:\n{lines}")
            self.status_lbl.setText(f"{len(dupes)} duplicate directive(s)")
        else:
            self.status_lbl.setText("No duplicate directives")

    # ====================================================================
    # Plot toolbar actions
    # ====================================================================

    def _active_plot(self):
        """Return the focused visible plot, or the current view's primary plot."""
        visible = (
            [self.time_plot]
            if self.plot_stack.currentIndex() == 0
            else [self.frf_plot, self.phase_plot, self.coh_plot]
        )
        focused = QtWidgets.QApplication.focusWidget()
        for w in visible:
            if focused is w._pw or (focused is not None and w._pw.isAncestorOf(focused)):
                return w
        return visible[0]

    def _toggle_time_plot(self) -> None:
        """TimeSpec button — show the time-spec graph only."""
        self.plot_stack.setCurrentIndex(0)

    def _toggle_frf_plot(self) -> None:
        """FRF button — show the FRF/coherence graph only."""
        self.plot_stack.setCurrentIndex(1)

    def _toggle_grid(self) -> None:
        self._grid_on = not getattr(self, "_grid_on", True)
        for w in self._plot_widgets():
            w._pw.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.3)

    def _set_theme(self, dark: bool) -> None:
        self._dark = dark
        bg = "#1e1e1e" if dark else "w"
        fg = "#d8dee9" if dark else "#20252b"
        for w in self._plot_widgets():
            w._pw.setBackground(bg)
            for name in ("left", "bottom", "top", "right"):
                axis = w._pw.getAxis(name)
                axis.setPen(fg)
                axis.setTextPen(fg)
            legend = getattr(w, "_legend", None)
            if legend is not None:
                legend.setBrush(
                    QtGui.QColor(30, 30, 30, 220)
                    if dark else QtGui.QColor(255, 255, 255, 220)
                )
                legend.setPen(QtGui.QColor("#56616d" if dark else "#c8d0d8"))
                for _sample, label in legend.items:
                    label.setText(label.text, color=fg)

    def _clear_measurement(self) -> None:
        for w in self._plot_widgets():
            w._pw.clear()
            w._legend.hide()
        self._raw_cache.clear()
        self._added_cache_ids.clear()
        self._last_raw = None
        self._last_pwelch = None
        self._offline_filtered = None
        self._offline_cl = None
        self._set_theme(self._dark)
        self.status_lbl.setText("Cleared")

    def _zoom_fit(self) -> None:
        pw = self._active_plot()._pw
        pw.getPlotItem().autoRange()
        self.status_lbl.setText("Zoom fit")

    def _save_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save plot image", "plot.png", "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self._active_plot()._pw.grab().save(path, "PNG"):
            self.status_lbl.setText(f"Saved {path}")
        else:
            self.status_lbl.setText(f"Save image failed: {path}")

    def _save_figure(self) -> None:
        """Save the four current plot models in the original MAT format."""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save IDEFigure",
            "measurement.idefigure",
            "IDEFigure (*.idefigure);;All files (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".idefigure"):
            path += ".idefigure"
        try:
            figure = self._collect_figure()
            save_idefigure(figure, path)
        except Exception as exc:
            self.status_lbl.setText(f"Save figure failed: {exc}")
            return
        self.status_lbl.setText(f"Saved figure {path}")

    def _open_figure(self) -> None:
        """Open a C#/Python ``.idefigure`` file and redraw its models."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open IDEFigure",
            "",
            "IDEFigure (*.idefigure);;All files (*.*)",
        )
        if not path:
            return
        try:
            figure = load_idefigure(path)
            self._apply_figure(figure)
        except Exception as exc:
            self.status_lbl.setText(f"Open figure failed: {exc}")
            return
        self.status_lbl.setText(f"Opened figure {path}")

    def _collect_figure(self) -> IdeFigure:
        views = self._plot_widgets()
        models: list[FigureModel] = []
        for view in views:
            plot = view._pw
            title_label = view.findChild(QtWidgets.QLabel, "plotTitle")
            title = title_label.text() if title_label else ""
            series: list[FigureSeries] = []
            for item in plot.listDataItems():
                x = getattr(item, "xData", None)
                y = getattr(item, "yData", None)
                if x is None or y is None:
                    continue
                opts = getattr(item, "opts", {}) or {}
                name = opts.get("name") or ""
                series.append(
                    FigureSeries(
                        title=str(name),
                        x=np.asarray(x, dtype=float),
                        y=np.asarray(y, dtype=float),
                    )
                )
            ctrl = getattr(plot, "ctrl", None)
            log_x_check = getattr(ctrl, "logXCheck", None)
            log_y_check = getattr(ctrl, "logYCheck", None)
            log_x = bool(log_x_check.isChecked()) if log_x_check is not None else view is not self.time_plot
            log_y = bool(log_y_check.isChecked()) if log_y_check is not None else False
            x_range, y_range = plot.getPlotItem().viewRange()
            x_min, x_max = (float(value) for value in x_range)
            y_min, y_max = (float(value) for value in y_range)
            with np.errstate(over="ignore", invalid="ignore"):
                if log_x:
                    x_min, x_max = (
                        float(np.power(10.0, x_min)),
                        float(np.power(10.0, x_max)),
                    )
                if log_y:
                    y_min, y_max = (
                        float(np.power(10.0, y_min)),
                        float(np.power(10.0, y_max)),
                    )
            models.append(
                FigureModel(
                    title=title,
                    series=series,
                    log_x=log_x,
                    log_y=log_y,
                    grid="on" if getattr(self, "_grid_on", True) else "off",
                    legend=bool(getattr(view, "_legend", None).isVisible()),
                    x_title=plot.getAxis("bottom").labelText,
                    y_title=plot.getAxis("left").labelText,
                    x_prop=(x_min, x_max, 0.0, 0.0),
                    y_prop=(y_min, y_max, 0.0, 0.0),
                )
            )
        return IdeFigure(
            figure_title="SiDiMaT measurement",
            figure_title_font_size=12.0,
            rows=2,
            columns=2,
            models=models,
        )

    def _apply_figure(self, figure: IdeFigure) -> None:
        views = self._plot_widgets()
        for index, view in enumerate(views):
            plot = view._pw
            plot.clear()
            if index >= len(figure.models):
                view._legend.hide()
                continue
            model = figure.models[index]
            for item in model.series:
                plot.plot(item.x, item.y, name=item.title or None)
            plot.setLogMode(x=model.log_x, y=model.log_y)
            plot.showGrid(
                x=model.grid.lower() == "on",
                y=model.grid.lower() == "on",
                alpha=0.3,
            )
            if model.x_title:
                plot.setLabel("bottom", model.x_title)
            if model.y_title:
                plot.setLabel("left", model.y_title)
            x_min, x_max = model.x_prop[:2]
            y_min, y_max = model.y_prop[:2]
            if np.isfinite((x_min, x_max)).all() and x_max > x_min:
                if model.log_x:
                    if x_min > 0 and x_max > 0:
                        plot.setXRange(
                            float(np.log10(x_min)),
                            float(np.log10(x_max)),
                            padding=0,
                        )
                else:
                    plot.setXRange(float(x_min), float(x_max), padding=0)
            if np.isfinite((y_min, y_max)).all() and y_max > y_min:
                if model.log_y:
                    if y_min > 0 and y_max > 0:
                        plot.setYRange(
                            float(np.log10(y_min)),
                            float(np.log10(y_max)),
                            padding=0,
                        )
                else:
                    plot.setYRange(float(y_min), float(y_max), padding=0)
            legend = getattr(view, "_legend", None)
            if legend is not None:
                legend.setVisible(model.legend)
            title_label = view.findChild(QtWidgets.QLabel, "plotTitle")
            if title_label and model.title:
                title_label.setText(model.title)
        self._last_raw = None
        self._last_pwelch = None
        self._offline_filtered = None
        self._offline_cl = None
        self._set_theme(self._dark)

    def _open_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open image", "", "Images (*.png *.jpg *.bmp)")
        if not path:
            return
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            self.status_lbl.setText(f"Image open failed: {path}")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Image — {os.path.basename(path)}")
        dialog.resize(min(1000, max(360, pixmap.width() + 32)),
                      min(760, max(260, pixmap.height() + 32)))
        scroll = QtWidgets.QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pixmap)
        scroll.setWidget(label)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(scroll)
        dialog.exec()

    def _copy_active_plot(self) -> None:
        pix = self._active_plot()._pw.grab()
        QtGui.QGuiApplication.clipboard().setPixmap(pix)
        self.status_lbl.setText("Plot copied to clipboard")

    def _snapshot(self) -> None:
        import time as _time
        fname = f"sidimat_snapshot_{_time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(os.getcwd(), fname)
        if self._active_plot()._pw.grab().save(path):
            self.status_lbl.setText(f"Snapshot → {fname}")

    def _fullscreen_active_plot(self) -> None:
        """Show an interactive full-screen copy without detaching the live plot."""
        import pyqtgraph as pg

        source = self._active_plot()._pw
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("SiDiMaT plot — Esc to close")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        target = pg.PlotWidget()
        target.setBackground("#1e1e1e" if getattr(self, "_dark", False) else "w")
        target.showGrid(
            x=getattr(self, "_grid_on", True),
            y=getattr(self, "_grid_on", True),
            alpha=0.3,
        )
        target.getPlotItem().setDownsampling(auto=True, mode="peak")
        target.getPlotItem().setClipToView(True)
        for item in source.listDataItems():
            x, y = item.getData()
            if x is None or y is None:
                continue
            opts = getattr(item, "opts", {}) or {}
            target.plot(
                np.asarray(x),
                np.asarray(y),
                pen=opts.get("pen"),
                name=opts.get("name") or None,
            )
        ctrl = getattr(source.getPlotItem(), "ctrl", None)
        if ctrl is not None:
            target.setLogMode(
                x=bool(ctrl.logXCheck.isChecked()),
                y=bool(ctrl.logYCheck.isChecked()),
            )
        for axis_name in ("left", "bottom"):
            label = source.getAxis(axis_name).labelText
            if label:
                target.setLabel(axis_name, label)
        layout.addWidget(target)
        QtCore.QTimer.singleShot(0, dialog.showFullScreen)
        dialog.exec()

    # ====================================================================
    # Dialogs
    # ====================================================================

    def _show_system_info(self) -> None:
        if not self.controller or not self.controller.connected:
            self.status_lbl.setText("Connect first")
            return
        try:
            info = self.controller.get_system_info()
        except Exception as exc:
            self.status_lbl.setText(f"sysinfo: {exc}")
            return
        lines = [
            f"Firmware:      {info.get('firmware', '?')}",
            f"Sample Freq:   {info.get('sample_frequency', 0):.0f} Hz",
        ]
        loop = info.get("loop")
        if loop is not None:
            lines.append(f"Loop status:   individual=0x{loop.individual:X} system=0x{loop.system:X}")
        for key in ("position", "pneumatic", "digital_input", "digital_output"):
            if key in info:
                lines.append(f"{key:14s} 0x{info[key]:X}")
        trace = info.get("trace")
        if trace is not None:
            # DGTIV reports samples/undersampling but not the UI-only average
            # count; do not display TraceParameters' default as hardware state.
            lines.append(
                f"Trace:         {trace.no_samples} samples, "
                f"undersample {trace.undersamples}"
            )
        QtWidgets.QMessageBox.information(
            self, "System Config Info", "\n".join(lines))

    def _show_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            "关于 python_sidmat",
            "python_sidmat — SiDiMaT 测量软件重构版\n"
            "布局对齐原版 SiDiMaT19xA（UI Automation 反推）\n"
            "测量核心移植自 SAMBA19xLib (PwelchTF / TraceInfo)\n"
            "后端复用 python_samba RCI 层（mock / serial / server）",
        )

    def _show_help(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "帮助",
            "1. 后端选 mock（无硬件）、serial（独占真机）或 server（共享真机）\n"
            "2. Connect 连接\n"
            "3. 📈 Trace Setting 设长度/平均/通道\n"
            "4. 点 Start 开始测量\n"
            "5. 右区四图显示 时间 / FRF幅频 / 相频 / 相干性\n"
            "6. 💾 Save raw 保存 .sidimat19x，📄 Open raw 导入\n"
            "7. 工具栏 🔃 刷新、🌑/🌓 主题、📋 复制、🌷 快照",
        )

    # ====================================================================
    # Window lifecycle
    # ====================================================================

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            if not self._stop_measurement():
                event.ignore()
                if not self._close_pending:
                    self._close_pending = True
                    QtCore.QTimer.singleShot(250, self.close)
                return
        self._disconnect()
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            return
        event.accept()
        super().closeEvent(event)
