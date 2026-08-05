"""PySide6 main window — matching SAMBA19xUI tab structure (decompiled).

Tab structure from decompiled C#:
  Main tabs: Connect | Controller | Status | Velocity | Position | Pneumatic |
             FF | PFF | SaveLoad | Logging | Special
  Controller sub-tabs: System Setting | AD/DA Mapping | Motor Protection
  Status sub-tabs: Status | Velocity | Position | Pneumatic | FF | PFF | SaveLoad | Special
  Velocity sub-tabs: Filter | Matrix
  Position sub-tabs: Filter | Sensor Matrix | Motor Matrix
  FF sub-tabs: Filter | Config
  PFF sub-tabs: Filter | Config
"""

from __future__ import annotations

import math
import os
import time

from python_samba.protocol.codes import FilterType, SystemStatus, filter_small_name
from python_samba.protocol.commands import FilterStage
from python_samba.services.safety import SafetyGate
from python_samba.services.session import ControllerSession, open_mock, open_serial
from python_samba.transport.serial_port import TransportError
from python_samba.ui.classic_widgets import (
    ClassicExpander,
    ClassicFilterPanel,
    FilterStageBar,
    FilterStageCell,
    FlatPush,
    GroupPanel,
    IOSignalButton,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
    format_ui_number,
)
from python_samba.ui.label_files import (
    LABEL_FILE_DEFAULTS,
    RUNTIME_MIN_COUNTS,
    parse_label_file,
    runtime_label_warnings,
)
from python_samba.ui.widgets import (
    POS_AXIS_LABELS,
    VEL_AXIS_LABELS,
    FilterDlg,
    FilterEditor,
    MatrixEditor,
)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc

from python_samba.ui.extra_pages import ExtraPagesMixin, PNEUM_AXIS_LABELS


# Re-export for tests
CLASSIC_TAB_ROWS: list[list[tuple[str, str]]] = []


# --- SAMBA19xUI labels (from SAMBA19xLabels.cs) ---
VEL_AXES_NAMES = ["Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"]
POS_AXES_NAMES = ["Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
                   "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2"]
PNEU_AXES_NAMES = ["Ztpneu", "Yrpneu", "Xrpneu"]
VEL_INPUT_NAMES_7 = ["X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "Z4FB"]
VEL_INPUT_NAMES_8 = ["Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "X4FB", "Z4FB"]
VEL_OUTPUT_NAMES = ["OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
                     "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4"]
ADC_INPUT_NAMES = ["X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
                    "Xff", "Yff", "Zff", "Prox1", "Prox2", "Prox3", "ProxH1",
                    "ProxH2", "ProxH3", "Xpos", "Xacc", "Ypos", "Yacc",
                    "Y2FB", "X3FB", "X4FB", "Y4FB", "Z4FB", "Prox4", "ProxH4",
                    "Auxiliary1", "Auxiliary2", "Auxiliary3", "Auxiliary4", "Auxiliary5"]
DAC_OUTPUT_NAMES = ["OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
                     "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
                     "Valve1", "Valve2", "Valve3", "Valve4", "Valve5", "Valve6",
                     "Diag0", "Diag1"]
FILTER_STAGE_NAMES = ["Fil1", "Fil2", "Fil3", "Fil4", "Fil5", "Fil6", "Fil7"]


def _apply_runtime_label_values(
    values: dict[str, list[str]] | None = None,
) -> list[str]:
    """Reset then apply the ten arrays used by the legacy label loader.

    All targets are mutated in place because the page-patch modules import
    several of these lists by reference before a ``MainWindow`` is created.
    """
    source = values or {}
    target_groups = {
        "InputName": (IOSignalButton.INPUT_NAMES,),
        "VelAxesName": (VEL_AXES_NAMES,),
        "PosAxesName": (POS_AXES_NAMES,),
        "PneuAxesName": (PNEU_AXES_NAMES,),
        "Vel7InputName": (VEL_INPUT_NAMES_7,),
        "Vel8InputName": (VEL_INPUT_NAMES_8,),
        "VelOutputName": (VEL_OUTPUT_NAMES,),
        "DACOutputName": (DAC_OUTPUT_NAMES, IOSignalButton.OUTPUT_NAMES),
        "MotorTemperaturSensorName": (IOSignalButton.TEMPERATURE_NAMES,),
        "ADCInputName": (ADC_INPUT_NAMES,),
    }
    for name, targets in target_groups.items():
        selected = LABEL_FILE_DEFAULTS[name]
        candidate = source.get(name, ())
        if len(candidate) >= RUNTIME_MIN_COUNTS[name]:
            selected = tuple(str(item) for item in candidate)
        for target in targets:
            target[:] = selected

    # IOSignalButton owns separate menu lists; widgets.py and extra_pages.py
    # likewise expose indexed dialog labels.  Synchronize those copies after
    # every reset/load so a second window in the same process is deterministic.
    IOSignalButton.VELOCITY_AXES[:] = VEL_AXES_NAMES
    IOSignalButton.POSITION_AXES[:] = POS_AXES_NAMES
    IOSignalButton.PNEUMATIC_AXES[:] = PNEU_AXES_NAMES
    VEL_AXIS_LABELS[:] = [f"{index} {name}" for index, name in enumerate(VEL_AXES_NAMES[:6])]
    POS_AXIS_LABELS[:] = [f"{index} {name}" for index, name in enumerate(POS_AXES_NAMES[:6])]
    PNEUM_AXIS_LABELS[:] = [
        f"{index} {name}" for index, name in enumerate(PNEU_AXES_NAMES[:3])
    ]
    return runtime_label_warnings(source) if values is not None else []


def _load_saved_runtime_labels() -> list[str]:
    """Apply QSettings ``LabelPath`` before any page widgets are built."""
    _apply_runtime_label_values()
    settings = QtCore.QSettings("python_samba", "SAMBA19xUI")
    path = str(settings.value("LabelPath", "No File") or "No File")
    if path == "No File":
        return []
    if not os.path.isfile(path):
        return [f"Label file not found: {path}"]
    try:
        return _apply_runtime_label_values(parse_label_file(path))
    except Exception as exc:
        return [f"Could not load label file {path}: {exc}"]


def current_runtime_label_values() -> dict[str, list[str]]:
    """Return current arrays for the Save/Load label-file generator."""
    values = {name: list(items) for name, items in LABEL_FILE_DEFAULTS.items()}
    values.update(
        {
            "InputName": list(IOSignalButton.INPUT_NAMES),
            "VelAxesName": list(VEL_AXES_NAMES),
            "PosAxesName": list(POS_AXES_NAMES),
            "PneuAxesName": list(PNEU_AXES_NAMES),
            "Vel7InputName": list(VEL_INPUT_NAMES_7),
            "Vel8InputName": list(VEL_INPUT_NAMES_8),
            "VelOutputName": list(VEL_OUTPUT_NAMES),
            "DACOutputName": list(DAC_OUTPUT_NAMES),
            "MotorTemperaturSensorName": list(IOSignalButton.TEMPERATURE_NAMES),
            "ADCInputName": list(ADC_INPUT_NAMES),
        }
    )
    return values

# Controller arrays keep the fourth vertical proximity after H1/H2/H3, while
# the UI groups Prox1..4 before ProxH1..4.  Each entry maps raw index -> display
# index and is also valid for the six-channel prefix.
PROX_RAW_TO_DISPLAY = (0, 1, 2, 4, 5, 6, 3, 7)
PROX_DISPLAY_NAMES = (
    "Prox1", "Prox2", "Prox3", "Prox4",
    "ProxH1", "ProxH2", "ProxH3", "ProxH4",
)

# CGMOV/CSMOV use the legacy eight-motor wire order below, followed by three
# isolator offsets.  The Motor Protection grid itself is in natural X/Y/Z
# order, so non-SALMO controllers need an explicit display mapping.
LEGACY_MOTOR_OFFSET_UI_TO_WIRE = {
    0: 5,   # X1Out
    1: 0,   # Y1Out
    3: 1,   # X2Out
    4: 4,   # Y2Out
    6: 7,   # X3Out
    7: 2,   # Y3Out
    9: 3,   # X4Out
    10: 6,  # Y4Out
}


def _parse_protocol_int(value, default: int = 0) -> int:
    """Parse an integer token while preserving the RCI's decimal convention.

    Most status words are returned as decimal text, while test fixtures and a
    few firmware variants use ``0x`` prefixes (or bare hexadecimal digits).
    """
    text = str(value).strip()
    if not text:
        return int(default)
    try:
        return int(text, 0)
    except ValueError:
        if any(character in "abcdefABCDEF" for character in text):
            return int(text, 16)
        return int(float(text))


def _motor_status_presentation(value: object) -> tuple[str, str]:
    """Return the legacy SAMBA motor-protection status text and color.

    BGMPS returns one numeric state per motor.  The old UI treats these as an
    enum rather than a boolean: ``0`` is normal, ``1`` is overheated/overpower,
    and ``2`` is disabled.  Other firmware values are deliberately shown as
    unknown instead of being reported as a generic failure.
    """

    try:
        state = _parse_protocol_int(value, default=-1)
    except (TypeError, ValueError):
        state = -1
    return {
        0: ("Normal", "#90ee90"),
        1: ("Overheated", "#ff0000"),
        2: ("Disabled", "#ffffe0"),
    }.get(state, ("Unknown", "#e7ecef"))


def _motor_disable_flag_enabled(value: object) -> bool:
    """Normalize the legacy BGOCV DisableAllFlag token to a boolean.

    The RCI documentation calls this an On/Off character.  SAMBA firmware
    commonly returns ``N``/``F`` (the setup-file writer uses the same pair),
    while mocks and newer builds may return 0/1 or ON/OFF words.  The legacy
    UI treats every non-zero value as enabled.
    """

    text = str(value).strip().upper()
    if text in {"N", "ON", "TRUE", "Y", "YES"}:
        return True
    if text in {"F", "OFF", "FALSE", "NO", "0", ""}:
        return False
    try:
        return _parse_protocol_int(value, default=0) != 0
    except (TypeError, ValueError):
        return False


def _motor_disable_flag_token(enabled: bool) -> str:
    """Encode DisableAllFlag using the legacy RCI On/Off characters."""

    return "N" if bool(enabled) else "F"


class SidebarLoopButton(QtWidgets.QToolButton):
    """Compact ON/OFF rocker used by the persistent classic sidebar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarLoopButton")
        self.setText("OFF")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(58, 48)

    def set_on(self, on: bool, _color: str | None = None) -> None:
        self.setText("ON" if on else "OFF")
        self.setProperty("active", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_color(self, color: str) -> None:
        """Compatibility with LedIndicator-based update handlers."""
        self.set_on(str(color).lower() not in {"", "gray", "grey", "off", "black"})


class LoopStatesWidget(QtWidgets.QFrame):
    """Persistent loop-state panel used in the main navigation sidebar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("loopStatesPanel")
        self.setFixedHeight(362)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(7, 4, 7, 6)
        column.setSpacing(1)

        heading = QtWidgets.QLabel("Loops Status")
        heading.setObjectName("sidebarSectionTitle")
        column.addWidget(heading)

        self.loop_btns: dict[str, SidebarLoopButton] = {}
        self.state_labels: dict[str, SidebarLoopButton] = {}
        for name, key in (
            ("Overall", "overall"),
            ("Pneumatic", "pneumatic"),
            ("Feed Forward", "ff"),
            ("Pneumatic FF", "pff"),
            ("Velocity", "velocity"),
            ("Position", "position"),
        ):
            lbl = QtWidgets.QLabel(name)
            lbl.setObjectName("loopName")
            state = SidebarLoopButton()
            state.setToolTip(f"Click to toggle {name} loop")
            state.clicked.connect(lambda _checked=False, k=key: self._on_loop_click(k))
            self.loop_btns[key] = state
            self.state_labels[key] = state

            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(lbl, 1)
            row.addWidget(state)
            column.addLayout(row)

        self.page_lbl = QtWidgets.QLabel("Connect")
        self.page_lbl.setObjectName("currentPageLabel")
        self.page_lbl.setWordWrap(True)
        self.page_lbl.hide()

        self.conn_lbl = QtWidgets.QLabel("Not Connected")
        self.conn_lbl.setObjectName("connectionStateLabel")
        self.conn_lbl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        self.conn_lbl.setMinimumHeight(48)
        self._main_window = None

    def set_main_window(self, mw) -> None:
        self._main_window = mw

    def _on_loop_click(self, key: str) -> None:
        """Toggle a loop using the same status word as the legacy UI."""
        mw = self._main_window
        if not mw or not mw.session or not mw.session.connected:
            return

        def work() -> None:
            session = mw._require_session()
            loop = session.get_loop_status()
            ind = loop.individual
            sysv = loop.system
            system_bits = {
                "overall": 0x00001,
                "ff": 0x00004,
                "pneumatic": 0x00040,
                "pff": 0x04000,
            }
            if key in system_bits:
                sysv ^= system_bits[key]
                summary = f"BSSTS individual=0x{ind:X} system=0x{sysv:X}"
                if not mw._confirm_write(summary):
                    return
                if mw.gate is None:
                    raise RuntimeError("Safety gate is not initialized")
                mw.gate.take_snapshot()
                mw._set_writable(True)
                try:
                    session.set_loop_status(ind, sysv)
                finally:
                    mw._set_writable(True)
                mw._refresh_status_loop_state()
                mw.log_msg(f"BSSTS applied from loop status bar ({key})")
                return

            if key not in {"velocity", "position"}:
                return
            if mw._supports_controller_feature("auto_loop_switch"):
                # Legacy LoopStates exposes RunningV/RunningP as read-only
                # lamps when the controller supports automatic switching.
                # Only firmware advertising NALS wires those clicks to BSOCD.
                mw.log_msg(
                    f"{key.title()} loop is status-only on auto-switch firmware"
                )
                mw._refresh_status_loop_state(loop)
                return
            conditions = session.get_switch_conditions()
            if len(conditions) < 4:
                raise RuntimeError(
                    f"BGOCD returned {len(conditions)} fields; expected at least 4"
                )
            config = _parse_protocol_int(conditions[3])
            if config & 0x01:
                mw.log_msg(
                    f"{key.title()} loop is controlled by automatic loop switching"
                )
                mw._refresh_status_loop_state(loop)
                return
            bit = 0x20 if key == "velocity" else 0x40
            config ^= bit
            if not mw._confirm_write(
                f"BSOCD config=0x{config:X} ({key} loop)"
            ):
                return
            if mw.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            mw.gate.take_snapshot()
            mw._set_writable(True)
            try:
                session.set_switch_conditions(
                    conditions[0], conditions[1], conditions[2], config
                )
            finally:
                mw._set_writable(True)
            mw._switch_config = config
            mw._switch_config_loaded = True
            mw._refresh_status_loop_state(loop)
            mw.log_msg(f"BSOCD applied from loop status bar ({key})")

        mw._run("Toggle loop", work)

    def update_loop(
        self,
        individual: int,
        system: int,
        switch_word: int | None = None,
        auto_switch: bool = False,
    ) -> None:
        """Update all LED states from loop status bits."""
        states = {
            "pneumatic": bool(system & 0x00040),
            "ff": bool(system & 0x00004),
            "pff": bool(system & 0x04000),
            "overall": bool(system & 0x01),
        }
        if switch_word is not None:
            states["velocity"] = bool(switch_word & 0x20)
            states["position"] = bool(switch_word & 0x40)
        for key, on in states.items():
            if key in self.loop_btns:
                self.loop_btns[key].set_on(on)
        for key in ("velocity", "position"):
            button = self.loop_btns[key]
            button.setEnabled(not auto_switch)
            button.setToolTip(
                "Controlled by automatic loop switching"
                if auto_switch
                else f"Click to toggle {key.title()} loop"
            )


class SamTabBar(QtWidgets.QTabBar):
    """Tab bar styled like SAMBA19xUI."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setStyleSheet("""
            QTabBar::tab {
                background: #f2f7fb;
                color: #31566c;
                border: 1px solid #b5c8d4;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 7px 18px 6px 18px;
                font-size: 15px;
                min-width: 120px;
                min-height: 30px;
            }
            QTabBar::tab:selected {
                background: #dbeef8;
                color: #1c4056;
                border-color: #73a9c1;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                background: #eaf5fa;
                color: #1f7199;
            }
        """)


class SamTabWidget(QtWidgets.QTabWidget):
    """Tab widget styled like SAMBA19xUI — auto-wraps pages in scroll areas."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTabBar(SamTabBar())
        self.setStyleSheet("""
            QTabWidget::pane {
                background: #e8f1f6;
                border: none;
            }
            QTabWidget::tab-bar { left: 9px; }
        """)

    def addTab(self, page: QtWidgets.QWidget, label: str) -> int:
        """Add tab, auto-wrapping in a scroll area for content-heavy pages."""
        # Check if the page already has a scroll area parent
        if not isinstance(page, QtWidgets.QScrollArea):
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setObjectName("classicPageScroll")
            scroll.setWidget(page)
            return super().addTab(scroll, label)
        return super().addTab(page, label)


class MainNavigation(QtWidgets.QFrame):
    """Fixed-width navigation matching the hierarchy of the original UI."""

    currentChanged = QtCore.Signal(int)

    def __init__(
        self,
        entries: list[tuple[int, str]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainNavigation")
        self.setFixedWidth(265)
        self.buttons: list[QtWidgets.QToolButton] = []
        self.tab_indices: list[int] = []

        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for tab_index, label in entries:
            button = QtWidgets.QToolButton()
            button.setObjectName("mainNavButton")
            button.setText(label)
            button.setCheckable(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setFixedHeight(56)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
            )
            button.clicked.connect(
                lambda _checked=False, idx=tab_index: self.currentChanged.emit(idx)
            )
            self._group.addButton(button, tab_index)
            self.buttons.append(button)
            self.tab_indices.append(tab_index)
            layout.addWidget(button)

        if self.buttons:
            self.buttons[0].setChecked(True)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if index in self.tab_indices:
            self.buttons[self.tab_indices.index(index)].setChecked(True)


class MainWindow(ExtraPagesMixin, QtWidgets.QMainWindow):
    """Main window matching SAMBA19xUI tab structure."""

    def __init__(self) -> None:
        super().__init__()
        self._label_load_warnings = _load_saved_runtime_labels()
        self.setWindowTitle("SAMBA19xUI RC06-Alpha02 V1.9.0.14 — python_samba")
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
        self.resize(1840, 1240)
        self.setMinimumSize(1180, 760)
        self.session: ControllerSession | None = None
        self.gate: SafetyGate | None = None
        self._last_firmware_version = None
        self._last_page_refresh_key: tuple[str, str] | None = None
        self._last_page_refresh_at = 0.0
        self._current_page: str = ""
        self._controller_capabilities_loaded = False
        self._system_constants: tuple[str, ...] = ()
        self._controller_features: frozenset[str] | None = None
        self._proximity_count = 6
        self._input_signal_count = len(IOSignalButton.INPUT_NAMES)
        self._last_proximity_offsets: list[float] = []
        self._live_refresh_errors: dict[str, str] = {}
        self._switch_config = 0
        self._switch_config_loaded = False

        central = QtWidgets.QWidget()
        central.setObjectName("applicationRoot")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The controls are persistent in the sidebar and are also used by
        # the detailed Connect page.  Keeping the original attributes avoids
        # breaking controller handlers and runtime page patches.
        self.connection_panel = self._build_connection_bar()

        # Main pages remain a QTabWidget for API compatibility.  Its outer
        # tab bar is hidden; the fixed sidebar is the visible primary nav.
        self.main_tabs = SamTabWidget()
        self.main_tabs.setObjectName("mainPageStack")

        # === Build pages ===
        self._build_connect_page()
        self._build_controller_tab()
        self._build_status_tab()
        self._build_velocity_tab()
        self._build_position_tab()
        self._build_pneumatic_tab()
        self._build_ff_tab()
        self._build_pff_tab()
        self._build_saveload_tab()
        self._build_logging_tab()
        self._build_special_tab()
        self.main_tabs.tabBar().hide()

        # Persistent status panel in the lower part of the sidebar.
        self.loop_states = LoopStatesWidget()
        self.loop_states.setFixedWidth(250)
        self.loop_states.set_main_window(self)

        # Activity console is available on demand instead of permanently
        # consuming vertical space below every parameter page.
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("activityLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setMinimumHeight(86)
        self.log.setMaximumHeight(150)

        self.header = self._build_header()
        self.header.installEventFilter(self)
        root.addWidget(self.header)

        body = QtWidgets.QWidget()
        body.setObjectName("workspaceBody")
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sidebar = self._build_sidebar()
        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(self.main_tabs, 1)
        root.addWidget(body, 1)

        self.console_panel = self._build_console_panel()
        self.console_panel.setVisible(False)
        root.addWidget(self.console_panel)

        # Connections
        self.btn_connect.clicked.connect(self.on_connect)
        self.btn_disconnect.clicked.connect(self.on_disconnect)
        self.main_navigation.currentChanged.connect(
            self._on_main_navigation_changed
        )
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)
        self.backend.currentTextChanged.connect(self._sync_port_enabled)
        self._sync_port_enabled(self.backend.currentText())

        self._apply_theme()
        self._build_context_menu()

        # The reference UI has no permanent console button in its title bar.
        # Keep the diagnostic console available through F12 and the context
        # menu without changing the visible shell.
        self._console_shortcut = QtGui.QShortcut(QtGui.QKeySequence("F12"), self)
        self._console_shortcut.activated.connect(
            lambda: self.console_toggle.setChecked(not self.console_toggle.isChecked())
        )

        # Refresh timer (1 second, like SAMBA19xUI)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._on_timer_tick)
        # The reference application starts its one-second state dispatcher as
        # soon as a controller is connected.  Keep the preference enabled
        # across disconnect/reconnect, but do not run a serial timer while no
        # session owns the port.
        self._auto_refresh = True

        # Select first tab
        self.main_tabs.setCurrentIndex(0)
        self._on_main_tab_changed(0)

        # Legacy hidden fields used by handlers
        self.loop_ind = SciEdit("3F")
        self.loop_sys = SciEdit("1800")
        self.loop_ind.setVisible(False)
        self.loop_sys.setVisible(False)
        self.loop_fflim = QtWidgets.QSpinBox()
        self.loop_fflim.setRange(0, 100)
        self.loop_fflim.setVisible(False)
        self.loop_fblim = SciEdit()
        self.loop_fblim.setVisible(False)
        self.loop_gsc = SciEdit()
        self.loop_gsc.setVisible(False)
        self.loop_ctype = SciEdit()
        self.loop_ctype.setVisible(False)
        for warning in self._label_load_warnings:
            self.log_msg(f"WARNING loading labels: {warning}")

    def eventFilter(self, watched, event):  # noqa: N802
        """Provide native dragging for the frameless SAMBA title bar."""
        if watched is getattr(self, "header", None):
            if event.type() == QtCore.QEvent.MouseButtonDblClick:
                if self.isMaximized():
                    self.showNormal()
                else:
                    self.showMaximized()
                return True
            if (
                event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.LeftButton
            ):
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                    return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Main window shell
    # ------------------------------------------------------------------

    def _build_header(self) -> QtWidgets.QFrame:
        header = QtWidgets.QFrame()
        header.setObjectName("applicationHeader")
        header.setFixedHeight(59)
        row = QtWidgets.QHBoxLayout(header)
        row.setContentsMargins(5, 0, 3, 0)
        row.setSpacing(5)

        brand = QtWidgets.QLabel("UI\n19x")
        brand.setObjectName("brandMark")
        brand.setAlignment(QtCore.Qt.AlignCenter)
        brand.setFixedSize(60, 54)
        row.addWidget(brand)

        title = QtWidgets.QLabel("SAMBA19xUI RC06-Alpha02 V1.9.0.14")
        title.setObjectName("applicationTitle")
        title.setAlignment(QtCore.Qt.AlignCenter)
        row.addWidget(title, 1)

        # Kept as a compatibility attribute for handlers/tests; the original
        # shell does not show a second title line.
        self.page_title_lbl = QtWidgets.QLabel("Connect")
        self.page_title_lbl.setObjectName("applicationSubtitle")
        self.page_title_lbl.hide()

        self.header_status_lbl = QtWidgets.QLabel("●  OFFLINE")
        self.header_status_lbl.setObjectName("headerConnectionStatus")
        self.header_status_lbl.setProperty("connected", False)
        self.header_status_lbl.hide()

        self.console_toggle = QtWidgets.QToolButton()
        self.console_toggle.setObjectName("consoleToggle")
        self.console_toggle.setText("Console")
        self.console_toggle.setCheckable(True)
        self.console_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.console_toggle.hide()

        self.minimize_button = QtWidgets.QToolButton()
        self.minimize_button.setObjectName("windowControlButton")
        self.minimize_button.setText("—")
        self.minimize_button.setToolTip("Minimize")
        self.minimize_button.clicked.connect(self.showMinimized)
        row.addWidget(self.minimize_button)

        self.close_button = QtWidgets.QToolButton()
        self.close_button.setObjectName("windowControlButton")
        self.close_button.setProperty("closeButton", True)
        self.close_button.setText("×")
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        return header

    def _build_sidebar(self) -> QtWidgets.QFrame:
        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(265)
        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)

        display_names = {
            "Pneum. SFF": "Pneum. FF",
            "Save/Load": "Save/Load Setup",
        }
        entries = [
            (i, display_names.get(self.main_tabs.tabText(i), self.main_tabs.tabText(i)))
            for i in range(self.main_tabs.count())
            if self.main_tabs.tabText(i) != "Logging"
        ]
        self.main_navigation = MainNavigation(entries)
        self.nav_buttons = self.main_navigation.buttons
        layout.addWidget(self.main_navigation)

        # Logging remains feature-complete but is intentionally not a primary
        # navigation item because it is absent from the supplied SAMBA shell.
        self.logging_tab_index = next(
            (i for i in range(self.main_tabs.count()) if self.main_tabs.tabText(i) == "Logging"),
            -1,
        )

        self.update_page_btn = FlatPush("Update Page")
        self.update_page_btn.setObjectName("updatePageButton")
        self.update_page_btn.clicked.connect(self._request_page_refresh)
        layout.addStretch(1)
        layout.addWidget(self.update_page_btn)
        layout.addSpacing(30)
        layout.addWidget(self.loop_states)
        self.loop_states.conn_lbl.setFixedHeight(60)
        layout.addWidget(self.loop_states.conn_lbl)
        return sidebar

    def _build_console_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("consolePanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 5, 8, 8)
        layout.setSpacing(4)

        bar = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("ACTIVITY CONSOLE")
        title.setObjectName("consoleTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        clear_btn = QtWidgets.QToolButton()
        clear_btn.setObjectName("consoleClearButton")
        clear_btn.setText("Clear")
        clear_btn.clicked.connect(self.log.clear)
        bar.addWidget(clear_btn)
        layout.addLayout(bar)
        layout.addWidget(self.log)

        self.console_toggle.toggled.connect(panel.setVisible)
        self.console_toggle.toggled.connect(
            lambda shown: self.console_toggle.setText(
                "Hide Console" if shown else "Console"
            )
        )
        return panel

    def _on_main_navigation_changed(self, index: int) -> None:
        if 0 <= index < self.main_tabs.count():
            self.main_tabs.setCurrentIndex(index)

    def _request_page_refresh(self) -> None:
        if not self.session or not self.session.connected:
            self.log_msg("Update skipped: controller is not connected")
            return
        self._refresh_current_page(force=True)

    def _current_subtab_text(self) -> str:
        page = self.main_tabs.currentWidget()
        if page is None:
            return ""
        tabs = page.findChild(SamTabWidget)
        if tabs is None or tabs.count() == 0:
            return ""
        return tabs.tabText(tabs.currentIndex())

    def _ensure_controller_capabilities(self) -> None:
        """Read BGGSC once, on first page that needs hardware dimensions."""
        if self._controller_capabilities_loaded:
            return
        self._controller_capabilities_loaded = True
        try:
            constants = tuple(str(value) for value in (
                self._require_session().get_global_system_constants()
            ))
            self._system_constants = constants
            if len(constants) > 5:
                self._proximity_count = 8 if int(constants[5]) == 8 else 6
            features = {token.upper() for token in constants[11:]}
            self._controller_features = frozenset(features)
            self._input_signal_count = (
                46
                if "EADCS" in features or "PNEUMRAMP" in features
                else 37
            )
        except Exception as exc:
            # Safe fallbacks match the older controller family.  Do not retry
            # this GET every timer tick if the firmware omits BGGSC.
            self._proximity_count = 6
            self._input_signal_count = min(37, len(IOSignalButton.INPUT_NAMES))
            self._controller_features = None
            self.log_msg(f"Controller capabilities: {exc}")
        self._apply_controller_capabilities()

    def _supports_controller_feature(self, feature: str) -> bool:
        """Return a source-compatible feature decision from cached BGGSC data.

        ``None`` means BGGSC could not be read, in which case controls stay
        visible instead of guessing that a feature is absent.
        """
        features = self._controller_features
        if features is None:
            return True
        checks = {
            "cascaded_position": lambda: "CASCADED" in features,
            "pneumatic_ramp": lambda: bool(
                features.intersection({"PNEUMRAMP", "PRAMP"})
            ),
            "safety": lambda: bool(
                features.intersection({"PNEUMRAMP", "SEQ"})
            ),
            "zms": lambda: bool(features.intersection({"ZMS", "ZMS2"})),
            # NALS is the firmware token for "no automatic loop switching".
            "auto_loop_switch": lambda: "NALS" not in features,
            # NAF is the legacy firmware token for "no analysis filter".
            "analysis": lambda: "NAF" not in features,
            "pos_pneum_digio": lambda: "PPILS" in features,
        }
        try:
            return checks[feature]()
        except KeyError as exc:
            raise ValueError(f"unknown controller feature {feature!r}") from exc

    def _apply_controller_capabilities(self) -> None:
        """Hide controls whose RCI groups are not advertised by BGGSC."""
        for attribute, feature in (
            ("pos_cascaded_expander", "cascaded_position"),
            ("pneum_ramp_expander", "pneumatic_ramp"),
            ("analysis_logging_group", "analysis"),
        ):
            widget = getattr(self, attribute, None)
            if widget is not None:
                widget.setVisible(self._supports_controller_feature(feature))

        tabs = getattr(self, "special_tabs", None)
        if tabs is not None:
            for title, feature in (("Safety", "safety"), ("System Safety", "zms")):
                for index in range(tabs.count()):
                    if tabs.tabText(index) == title:
                        tabs.setTabVisible(
                            index, self._supports_controller_feature(feature)
                        )
                        break

    def _report_live_refresh_error(self, key: str, exc: Exception) -> None:
        """Log a repeating live-read failure only when its message changes."""
        message = f"{type(exc).__name__}: {exc}"
        if self._live_refresh_errors.get(key) != message:
            self._live_refresh_errors[key] = message
            self.log_msg(f"Live refresh {key}: {message}")

    def _on_axis_individual_loop_clicked(self, kind: str, axis: int) -> None:
        """Toggle one velocity, position, or pneumatic individual-loop bit."""
        limits = {"velocity": 6, "position": 6, "pneumatic": 3}
        if kind not in limits:
            raise ValueError(f"unknown individual-loop kind: {kind!r}")
        if not 0 <= axis < limits[kind]:
            raise ValueError(
                f"{kind} individual-loop axis out of range: {axis}"
            )
        if not self.session or not self.session.connected:
            return

        def work() -> None:
            session = self._require_session()
            if kind == "velocity":
                loop = session.get_loop_status()
                session.set_loop_status(
                    loop.individual ^ (1 << axis), loop.system
                )
            else:
                position, pneumatic, _digital_in, _digital_out = (
                    session.get_pos_pneum_digital_status()
                )
                if kind == "position":
                    position ^= 1 << axis
                else:
                    pneumatic ^= 1 << axis
                session.set_pos_pneum_individual_loop_status(
                    position, pneumatic
                )
            self._refresh_status_loop_state(include_axis_status=True)
            self.log_msg(
                f"Toggled {kind} individual loop axis {axis + 1}"
            )

        self._run(f"Toggle {kind} individual loop", work)

    def _update_status_loop_widgets(
        self,
        loop,
        switch_word: int,
        status_words=None,
        switch_config: int = 0,
    ) -> None:
        """Apply already-read loop words without performing serial I/O."""
        states = {
            "Overall Active": bool(loop.system & 0x01),
            "Pneumatic": bool(loop.system & 0x40),
            "Feed Forward": bool(loop.system & 0x04),
            "Pneumatic FF": bool(loop.system & 0x4000),
            "Velocity Loop": bool(switch_word & 0x20),
            "Position Loop": bool(switch_word & 0x40),
        }
        for name, badge in self.status_loop_badges.items():
            enabled = states.get(name, False)
            if hasattr(badge, "set_on"):
                badge.set_on(enabled)
            else:
                badge.setText("ON" if enabled else "OFF")
                badge.setProperty("active", enabled)
                self._refresh_dynamic_style(badge)

        # This is a firmware capability decision in the legacy UI, not the
        # current BGOCD low-bit state.
        auto_switch = self._supports_controller_feature("auto_loop_switch")
        self.loop_states.update_loop(
            int(loop.individual), int(loop.system), switch_word, auto_switch
        )
        ff_setter = getattr(self, "_set_ff_individual_loop_buttons", None)
        if callable(ff_setter):
            ff_setter(int(loop.individual))
        for name in ("Velocity Loop", "Position Loop"):
            badge = self.status_loop_badges.get(name)
            if badge is not None:
                badge.setEnabled(not auto_switch)
                badge.setToolTip(
                    "Controlled by automatic loop switching"
                    if auto_switch
                    else f"Click to toggle {name}"
                )

        def update_axis_lamps(lamps, word: int) -> None:
            for index, lamp in enumerate(lamps):
                enabled = bool(word & (1 << index))
                if hasattr(lamp, "set_on"):
                    lamp.set_on(enabled)
                else:
                    lamp.setText("ON" if enabled else "OFF")
                    lamp.setProperty("active", enabled)
                    self._refresh_dynamic_style(lamp)

        for attribute in (
            "status_velocity_axis_lamps",
            "vel_filter_axis_leds",
            "vel_individual_loop_buttons",
        ):
            update_axis_lamps(
                getattr(self, attribute, ()), int(loop.individual)
            )
        if status_words is not None:
            position, pneumatic = (list(status_words) + [0, 0])[:2]
            for attribute in (
                "status_position_axis_lamps",
                "pos_filter_axis_leds",
            ):
                update_axis_lamps(getattr(self, attribute, ()), int(position))
            update_axis_lamps(
                getattr(self, "status_pneumatic_axis_lamps", ()),
                int(pneumatic),
            )
            pff_setter = getattr(self, "_set_pff_individual_loop_buttons", None)
            if callable(pff_setter):
                pff_setter(int(pneumatic))
            pneu_setter = getattr(self, "_set_pneum_individual_loop_buttons", None)
            if callable(pneu_setter):
                pneu_setter(int(pneumatic))

    def _refresh_status_loop_state(self, loop=None, *, include_axis_status: bool = False) -> None:
        """Lightweight one-second refresh for the Status/Loops Status panel."""
        session = self._require_session()
        if loop is None:
            loop = session.get_loop_status()
        switch = session.get_switch_status()
        switch_word = _parse_protocol_int(switch[0]) if switch else 0
        if not self._switch_config_loaded:
            conditions = session.get_switch_conditions()
            if len(conditions) > 3:
                self._switch_config = _parse_protocol_int(conditions[3])
                self._switch_config_loaded = True
        status_words = (
            session.get_pos_pneum_digital_status() if include_axis_status else None
        )
        self._update_status_loop_widgets(
            loop, switch_word, status_words, self._switch_config
        )

    def _refresh_status_reference(self) -> None:
        """Apply loop words to the visible status badges and axis lamps."""
        s = self._require_session()
        loop = s.get_loop_status()
        self._refresh_status_loop_state(loop, include_axis_status=True)

        events = s.get_amplifier_disable_events()
        rows: list[tuple[str, ...]] = []
        for index, raw_value in enumerate(events[:10]):
            try:
                word = int(str(raw_value), 0)
            except ValueError:
                word = int(str(raw_value), 16)
            if word == 0:
                continue
            rows.append((
                f"Event{index}",
                f"{word:X}",
                "NotConnected" if word & 0x100 else "Connected",
                "YES" if word & 0x40 else "NO",
                "YES" if word & 0x80 else "NO",
                "NotConnected" if word & 0x800 else "Connected",
                # The original AmplifierEvent class uses 0x100 here too.
                "YES" if word & 0x100 else "NO",
                "YES" if word & 0x400 else "NO",
            ))

        self.status_events.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column >= 2:
                    if value == "YES":
                        item.setBackground(QtGui.QColor("#ff2424"))
                    elif value == "NotConnected":
                        item.setBackground(QtGui.QColor("#fff21a"))
                    else:
                        item.setBackground(QtGui.QColor("#0a9817"))
                self.status_events.setItem(row, column, item)

    def _refresh_signal_display_reference(self) -> None:
        """Refresh monitor definitions and live values for all visible cards."""
        s = self._require_session()
        for signal_number, selector in enumerate(self.sig_selectors):
            try:
                definition = s.get_monitor_signal(signal_number)
                if len(definition) >= 3:
                    tokens = tuple(int(value) for value in definition[:3])
                    if isinstance(selector, IOSignalButton):
                        selector.set_io_signal(tokens)
                        labels = getattr(self, "sig_name_labels", [])
                        if signal_number < len(labels):
                            labels[signal_number].setText(selector.text())
                    else:
                        # Compatibility for the compact, non-reference page.
                        for index in range(selector.count()):
                            data = selector.itemData(index)
                            if isinstance(data, (tuple, list)) and tuple(data[:3]) == tokens:
                                selector.blockSignals(True)
                                selector.setCurrentIndex(index)
                                selector.blockSignals(False)
                                break
            except Exception as exc:
                self.log_msg(f"Monitor signal {signal_number} read: {exc}")
        try:
            values = s.get_monitor_values(0, len(self.sig_values) - 1)
            for index, value in enumerate(values[:len(self.sig_values)]):
                self.sig_values[index].setText(format_ui_number(value))
        except Exception as exc:
            self.log_msg(f"Monitor values read: {exc}")

    def _refresh_current_page(self, *, force: bool = False) -> None:
        """Dispatch Update Page like the original page-specific UpdatePage()."""
        self._ensure_controller_capabilities()
        main = self.main_tabs.tabText(self.main_tabs.currentIndex())
        sub = self._current_subtab_text()
        refresh_key = (main, sub)
        if (
            not force
            and refresh_key == self._last_page_refresh_key
            and time.monotonic() - self._last_page_refresh_at < 0.75
        ):
            return

        if main == "Controller":
            if sub == "System Setting":
                self._read_system_setting_reference()
            elif sub == "AD/DA Mapping":
                self.on_adc_read()
            elif sub == "Motor Protection":
                self.on_motor_prot_read()
        elif main == "Status":
            if sub == "Status":
                self._run("Read Status", self._refresh_status_reference)
            elif sub == "Signals Display":
                self._run("Read Signals Display", self._refresh_signal_display_reference)
            elif sub == "DigIO Status":
                self._on_digio_read()
        elif main == "Velocity":
            if sub == "Tuning":
                self.on_vel_read_all_filters()
                self.on_diag_read_classic()
            else:
                self.on_vel_mat_read()
        elif main == "Position":
            if sub == "Tuning":
                self.on_pos_read_all_filters()
                self.on_prox_read_classic()
                if self._supports_controller_feature("cascaded_position"):
                    self.on_cascaded_read()
                self.on_nlp_read()
            elif sub == "Sensor Matrix":
                self.on_pos_mat_read("sensor")
            elif sub == "Motor Matrix":
                self.on_pos_mat_read("motor")
            elif sub == "Proxy Adjustment":
                self.on_prox_read_classic()
        elif main == "Pneumatic":
            self._on_pneu_read_all()
        elif main == "Feed Forward":
            if sub == "FF Tuning":
                self.on_ff_read_all_filters()
                self.on_ff_status_read_classic()
            else:
                self.on_ff_read_gains()
        elif main == "Pneum. SFF":
            if sub == "PFF Tuning":
                self.on_pff_read_all_filters()
            else:
                self.on_pff_read_gains()
        elif main == "Special":
            if sub == "Safety" and self._supports_controller_feature("safety"):
                self.on_safety_read_config()
                self.on_safety_read()
            elif (
                sub in {"ZMS", "System Safety"}
                and self._supports_controller_feature("zms")
            ):
                self.on_zms_read()
            elif sub in {"Polynom", "Polynomials"}:
                self.on_poly_read_all()
                self.on_poly_read()

        self._last_page_refresh_key = refresh_key
        self._last_page_refresh_at = time.monotonic()

    # ------------------------------------------------------------------
    # Connection controls
    # ------------------------------------------------------------------

    def _build_connection_bar(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("quickConnectionPanel")
        conn = QtWidgets.QGridLayout(panel)
        conn.setContentsMargins(9, 8, 9, 8)
        conn.setHorizontalSpacing(5)
        conn.setVerticalSpacing(5)

        title = QtWidgets.QLabel("CONNECTION")
        title.setObjectName("sidebarSectionTitle")
        conn.addWidget(title, 0, 0, 1, 2)

        self.backend = QtWidgets.QComboBox()
        self.backend.addItems(["mock", "serial"])
        self.port = QtWidgets.QLineEdit("COM3")
        self.baud = QtWidgets.QComboBox()
        for b in (19200, 38400, 57600, 115200, 230400):
            self.baud.addItem(str(b), b)
        self.baud.setCurrentText("57600")
        self.btn_connect = FlatPush("Connect")
        self.btn_disconnect = FlatPush("Disconnect")
        self.btn_disconnect.setEnabled(False)
        self.status_lbl = QtWidgets.QLabel("Disconnected")
        self.status_lbl.setObjectName("quickConnectionStatus")
        self.status_lbl.setWordWrap(True)

        conn.addWidget(QtWidgets.QLabel("Mode"), 1, 0)
        conn.addWidget(self.backend, 1, 1)
        conn.addWidget(QtWidgets.QLabel("Port"), 2, 0)
        conn.addWidget(self.port, 2, 1)
        conn.addWidget(QtWidgets.QLabel("Baud"), 3, 0)
        conn.addWidget(self.baud, 3, 1)
        conn.addWidget(self.btn_connect, 4, 0)
        conn.addWidget(self.btn_disconnect, 4, 1)
        conn.addWidget(self.status_lbl, 5, 0, 1, 2)
        return panel

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_modern_theme_unused(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                color: #243447;
                font-family: "Segoe UI", "Tahoma", "Microsoft YaHei UI", sans-serif;
                font-size: 12px;
            }
            QMainWindow, QWidget#applicationRoot, QWidget#workspaceBody {
                background: #e8eef3;
            }
            QFrame#applicationHeader {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #17212b, stop:1 #2f3d4a);
                border-bottom: 2px solid #4fa3e3;
            }
            QLabel#brandMark {
                background: #2f80c9;
                color: white;
                border: 1px solid #78b9e8;
                border-radius: 5px;
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#applicationTitle {
                color: #f7fafc;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#applicationSubtitle {
                color: #a9c1d4;
                font-size: 11px;
            }
            QLabel#headerConnectionStatus {
                color: #ffb4ad;
                font-size: 11px;
                font-weight: 700;
                padding: 5px 9px;
                border: 1px solid #607486;
                border-radius: 4px;
                background: #202e3a;
            }
            QLabel#headerConnectionStatus[connected="true"] {
                color: #7ee2a8;
                border-color: #438865;
            }
            QToolButton#consoleToggle {
                color: #eef6fc;
                background: #344b5e;
                border: 1px solid #71889a;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QToolButton#consoleToggle:hover,
            QToolButton#consoleToggle:checked { background: #2f80c9; }

            QFrame#sidebar {
                background: #273746;
                border-right: 1px solid #17232d;
            }
            QFrame#mainNavigation { background: transparent; }
            QLabel#sidebarSectionTitle {
                background: transparent;
                color: #91a7b9;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QToolButton#mainNavButton {
                background: #314454;
                color: #eaf1f6;
                border: 1px solid #3e5567;
                border-left: 4px solid transparent;
                border-radius: 4px;
                padding: 0 12px;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }
            QToolButton#mainNavButton:hover {
                background: #3b5265;
                border-left-color: #82bce8;
            }
            QToolButton#mainNavButton:checked {
                background: #e8f3fb;
                color: #17324d;
                border-color: #75b6e5;
                border-left-color: #2f80c9;
                font-weight: 750;
            }
            QPushButton#updatePageButton {
                background: #2f80c9;
                color: white;
                border-color: #69afe2;
                font-weight: 700;
            }
            QPushButton#updatePageButton:hover { background: #3f91d8; }

            QFrame#loopStatesPanel, QFrame#quickConnectionPanel {
                background: #1f2d38;
                border: 1px solid #43596a;
                border-radius: 5px;
            }
            QFrame#loopStatesPanel QLabel,
            QFrame#quickConnectionPanel QLabel,
            QFrame#quickConnectionPanel QCheckBox { color: #dce8f1; }
            QFrame#loopStatesPanel QLabel#sidebarSectionTitle,
            QFrame#quickConnectionPanel QLabel#sidebarSectionTitle { color: #91a7b9; }
            QLabel#loopName { font-size: 11px; }
            QLabel#loopStateBadge {
                color: #aebbc5;
                background: #354653;
                border: 1px solid #536674;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 800;
                padding: 2px;
            }
            QLabel#loopStateBadge[active="true"] {
                color: #0b4226;
                background: #66d391;
                border-color: #91e8b2;
            }
            QFrame#sidebarDivider { color: #465b6b; }
            QLabel#currentPageLabel { color: #91a7b9; font-size: 10px; }
            QLabel#connectionStateLabel,
            QLabel#quickConnectionStatus {
                color: #b8c7d2;
                font-size: 10px;
                font-weight: 650;
            }
            QLabel#connectionStateLabel[connected="true"],
            QLabel#quickConnectionStatus[connected="true"] {
                color: #7ee2a8;
            }
            QFrame#quickConnectionPanel QComboBox,
            QFrame#quickConnectionPanel QLineEdit {
                min-height: 20px;
                padding: 1px 4px;
            }
            QFrame#quickConnectionPanel QPushButton {
                min-height: 22px;
                padding: 2px 4px;
            }

            QTabWidget#mainPageStack > QWidget { background: #e8eef3; }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #ffffff, stop:1 #dfe7ed);
                color: #263b4d;
                border: 1px solid #8fa1b0;
                border-radius: 4px;
                padding: 4px 11px;
                min-height: 23px;
            }
            QPushButton:hover { background: #eef6fc; border-color: #4f99d0; }
            QPushButton:pressed { background: #d7e6f1; }
            QPushButton:disabled { color: #9aa6af; background: #e3e7ea; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
            QPlainTextEdit, QTextEdit {
                background-color: #ffffff;
                color: #1f3445;
                border: 1px solid #9caeba;
                border-radius: 2px;
                padding: 3px 5px;
                selection-background-color: #69aee0;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QComboBox:focus { border: 1px solid #2f80c9; }
            QLineEdit:read-only { background: #edf1f4; color: #526270; }
            QGroupBox {
                background: #f8fafb;
                border: 1px solid #aebbc5;
                border-radius: 5px;
                margin-top: 12px;
                padding: 10px 8px 8px;
                font-weight: 700;
                color: #29445b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #29445b;
                background: #f8fafb;
            }
            QScrollArea, QScrollArea > QWidget > QWidget {
                background: #e8eef3;
                border: none;
            }
            QTableView, QTableWidget {
                background: #ffffff;
                alternate-background-color: #f2f6f8;
                gridline-color: #c4ced6;
                border: 1px solid #aab8c3;
            }
            QHeaderView::section {
                background: #dce6ed;
                color: #29445b;
                border: none;
                border-right: 1px solid #bac7d0;
                border-bottom: 1px solid #aab8c3;
                padding: 5px;
                font-weight: 700;
            }
            QPlainTextEdit {
                font-family: Consolas, "Courier New", monospace;
                font-size: 11px;
            }
            QLabel { color: #243447; }
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator {
                width: 13px; height: 13px;
                border: 1px solid #8295a4;
                border-radius: 2px;
                background: #fff;
            }
            QCheckBox::indicator:checked {
                background: #2f80c9;
                border-color: #2f80c9;
            }

            QFrame#consolePanel {
                background: #17232d;
                border-top: 2px solid #2f80c9;
            }
            QLabel#consoleTitle {
                color: #a9c1d4;
                font-size: 10px;
                font-weight: 800;
            }
            QToolButton#consoleClearButton {
                color: #dce8f1;
                background: transparent;
                border: none;
                padding: 2px 8px;
            }
            QToolButton#consoleClearButton:hover { color: white; }
            QPlainTextEdit#activityLog {
                color: #d8e5ef;
                background: #0f1921;
                border: 1px solid #3d5262;
            }
        """)

    def _apply_theme(self) -> None:
        """Apply the screenshot-oriented SAMBA19xUI shell and control theme."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                color: #203443;
                font-family: "Segoe UI", "Microsoft YaHei UI", "Arial", sans-serif;
                font-size: 16px;
            }
            QMainWindow, QWidget#applicationRoot, QWidget#workspaceBody,
            QTabWidget#mainPageStack, QTabWidget#mainPageStack > QWidget,
            QScrollArea#classicPageScroll,
            QScrollArea#classicPageScroll > QWidget > QWidget {
                background: #e8f1f6;
            }

            QFrame#applicationHeader {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #173047, stop:0.62 #244b66, stop:1 #376d8b);
                border-bottom: 2px solid #78b5d2;
            }
            QLabel#brandMark {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #102638, stop:1 #315b76);
                color: white;
                border: 1px solid #a8d0e4;
                border-radius: 5px;
                font-size: 14px;
                font-weight: 800;
                font-style: italic;
            }
            QLabel#applicationTitle {
                color: white;
                font-size: 24px;
                font-weight: 800;
                background: transparent;
            }
            QToolButton#windowControlButton {
                min-width: 42px;
                max-width: 42px;
                min-height: 36px;
                max-height: 36px;
                color: white;
                background: rgba(10, 30, 45, 150);
                border: 1px solid #9cc7dc;
                border-radius: 6px;
                font-size: 20px;
                font-weight: 700;
            }
            QToolButton#windowControlButton:hover { background: #3a7190; }
            QToolButton#windowControlButton[closeButton="true"]:hover {
                background: #b83030;
            }

            QFrame#sidebar {
                background: #263c4c;
                border-right: 1px solid #152936;
            }
            QFrame#mainNavigation { background: transparent; }
            QToolButton#mainNavButton {
                background: #304d60;
                color: #e9f3f8;
                border: 1px solid #486a7d;
                border-left: 3px solid transparent;
                border-radius: 5px;
                margin-left: 8px;
                margin-right: 8px;
                padding: 0 10px;
                text-align: center;
                font-size: 15px;
                font-weight: 650;
            }
            QToolButton#mainNavButton:hover {
                background: #3b5d72;
                border-left-color: #8ac4df;
            }
            QToolButton#mainNavButton:checked {
                background: #e2f0f7;
                color: #1c4056;
                border-color: #9cc9dd;
                border-left-color: #4f9ac0;
                font-weight: 750;
            }
            QPushButton#updatePageButton {
                min-height: 38px;
                margin: 0 8px;
                color: white;
                background: #2f789e;
                border: 1px solid #8fc4db;
                border-radius: 5px;
                font-size: 14px;
                font-weight: 650;
            }

            QFrame#loopStatesPanel {
                background: #f1f7fa;
                border: 1px solid #b5cad6;
                border-radius: 6px;
            }
            QFrame#loopStatesPanel QLabel#sidebarSectionTitle {
                color: #31576d;
                background: transparent;
                font-size: 15px;
                font-weight: 700;
                padding-left: 6px;
            }
            QFrame#loopStatesPanel QLabel#loopName {
                color: #345468;
                background: transparent;
                font-size: 13px;
            }
            QToolButton#sidebarLoopButton {
                color: #111111;
                background: qradialgradient(cx:0.45,cy:0.45,radius:0.75,
                    stop:0 #f8f8f8, stop:0.55 #c5bfc0, stop:1 #8e898a);
                border: 3px solid #a89fa0;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 800;
            }
            QToolButton#sidebarLoopButton[active="true"] {
                background: qradialgradient(cx:0.5,cy:0.5,radius:0.62,
                    stop:0 #ecff20, stop:0.42 #a8e517, stop:0.73 #398a16,
                    stop:1 #0b5017);
                border-color: #427442;
            }
            QLabel#connectionStateLabel {
                color: white;
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #4f7185, stop:0.45 #294252, stop:1 #1b2e3b);
                font-size: 14px;
                padding: 4px 7px;
                border: 1px solid #6b91a5;
                border-radius: 4px;
            }
            QLabel#connectionStateLabel[connected="true"] { color: #b9f2cb; }

            QScrollArea { border: none; background: #e8f1f6; }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #d8e5ec;
                border: none;
            }
            QScrollBar:vertical { width: 12px; margin: 2px; }
            QScrollBar:horizontal { height: 12px; margin: 2px; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #91afbf;
                border-radius: 5px;
                min-height: 28px;
                min-width: 28px;
            }
            QScrollBar::handle:hover { background: #6e9bb1; }
            QScrollBar::add-line, QScrollBar::sub-line,
            QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none; }

            QPushButton {
                color: #28475b;
                background: #f8fbfd;
                border: 1px solid #9db6c5;
                border-radius: 5px;
                padding: 5px 12px;
                min-height: 30px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #eaf5fa; border-color: #5d9abb; }
            QPushButton:pressed { background: #d7ebf3; }
            QPushButton:focus { border: 1px solid #3d86ad; }
            QPushButton:disabled {
                color: #8c9ca6;
                background: #e3ebef;
                border-color: #c5d2d9;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
            QPlainTextEdit, QTextEdit {
                color: #203443;
                background: #ffffff;
                border: 1px solid #9db6c5;
                border-radius: 4px;
                padding: 4px 7px;
                min-height: 27px;
                selection-background-color: #b9ddea;
                font-size: 14px;
            }
            QLineEdit:read-only, QSpinBox:read-only, QDoubleSpinBox:read-only {
                background: #eef5f8;
                color: #5b7180;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
                border: 1px solid #3d86ad;
            }
            QComboBox { background: #ffffff; padding-right: 22px; }
            QComboBox::drop-down {
                width: 22px;
                border-left: 1px solid #c3d2db;
                background: #edf5f8;
            }
            QGroupBox {
                color: #27485d;
                background: #f7fafc;
                border: 1px solid #b5c8d4;
                border-radius: 8px;
                margin-top: 12px;
                padding: 10px 8px 8px 8px;
                font-size: 15px;
                font-weight: 650;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #315b72;
                background: #f7fafc;
            }
            QLabel { color: #28475b; background: transparent; }
            QCheckBox { spacing: 6px; background: transparent; }
            QCheckBox::indicator {
                width: 17px; height: 17px;
                border: 2px solid #8c8c8c;
                background: white;
            }
            QCheckBox::indicator:checked { background: #58ad2b; }
            QTableView, QTableWidget {
                color: #203443;
                background: white;
                alternate-background-color: #f2f7fa;
                gridline-color: #cfdee6;
                border: 1px solid #aebfca;
                font-size: 14px;
            }
            QHeaderView::section {
                color: #31566c;
                background: #e6f0f5;
                border: 1px solid #c0d1db;
                padding: 5px 6px;
                font-size: 14px;
                font-weight: 650;
            }

            QScrollArea#connectPortList,
            QWidget#connectPortListInner {
                color: #111111;
                background: white;
                border: 1px solid #8b8b8b;
            }
            QScrollArea#connectPortList QRadioButton {
                color: #28475b;
                background: white;
                font-size: 14px;
            }
            QPushButton#connectExpander {
                color: #315b72;
                background: transparent;
                border: none;
                padding: 2px 0;
                text-align: left;
                font-size: 15px;
                font-weight: 650;
            }
            QPushButton#connectExpander:hover { color: #174d6d; }
            QFrame#connectExpandedPanel {
                background: #eef5f8;
                border: 1px solid #b5c8d4;
                border-radius: 5px;
            }

            QLabel#classicStatusBadge {
                color: #28475b;
                background: #f0f4f6;
                border: 1px solid #aebfca;
                border-radius: 5px;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#classicAxisLamp {
                color: #173d26;
                background: #b8efc4;
                border: 1px solid #4f9b61;
                border-radius: 5px;
                font-size: 14px;
                font-weight: 700;
            }
            QFrame#proxyReadoutCard {
                background: #eef5f8;
                border: 1px solid #b5c8d4;
                border-radius: 6px;
            }
            QLabel#proxyReadoutTitle {
                color: #31566c;
                background: #dbeef8;
                font-size: 16px;
                font-weight: 650;
                padding: 6px;
            }
            QLabel#proxyReadoutValue {
                color: #203443;
                background: #ffffff;
                font-size: 32px;
                font-weight: 600;
                padding: 8px;
            }
            QLabel#proxyReadoutValue[alternate="true"] { background: #f2f7fb; }

            QFrame#consolePanel {
                background: #1e1b1c;
                border-top: 2px solid white;
            }
            QLabel#consoleTitle, QToolButton#consoleClearButton { color: white; }
            QPlainTextEdit#activityLog {
                color: #e9f2f7;
                background: #0f0e0e;
                border: 1px solid #777;
                font-family: Consolas;
                font-size: 12px;
            }
        """)

    def _build_context_menu(self) -> None:
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos) -> None:
        menu = QtWidgets.QMenu(self)
        act_about = menu.addAction("About")
        act_console = menu.addAction(
            "Hide Activity Console" if self.console_toggle.isChecked()
            else "Show Activity Console (F12)"
        )
        act_logging = None
        if getattr(self, "logging_tab_index", -1) >= 0:
            act_logging = menu.addAction("Open Logging Page")
        act_timer = menu.addAction(
            "Stop Refresh Timer" if self._auto_refresh else "Start Refresh Timer"
        )
        act_ui_options = menu.addAction("UI Options")
        menu.addSeparator()
        act_refresh = menu.addAction("Refresh Now")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_about:
            QtWidgets.QMessageBox.about(self, "About python_samba",
                "<b>python_samba</b> — vendor-free SAMBA-compatible host<br>"
                "Pure RCI serial (no Rci32.dll / CommServer)<br>"
                "Tab structure matches SAMBA19xUI<br><br>"
                "Phase: TC-MFD tuning")
        elif chosen is act_console:
            self.console_toggle.setChecked(not self.console_toggle.isChecked())
        elif act_logging is not None and chosen is act_logging:
            self.main_tabs.setCurrentIndex(self.logging_tab_index)
        elif chosen is act_timer:
            self._toggle_auto_refresh()
        elif chosen is act_ui_options:
            from python_samba.ui.patches import load_patch_module
            mod = load_patch_module("low_priority_patch")
            if mod and hasattr(mod, "show_ui_options"):
                mod.show_ui_options(self)
        elif chosen is act_refresh:
            if self.session and self.session.connected:
                self.on_refresh()

    def _toggle_auto_refresh(self) -> None:
        self._auto_refresh = not self._auto_refresh
        if self._auto_refresh:
            if self.session and self.session.connected:
                self._refresh_timer.start()
                self._on_timer_tick()
                self.log_msg("auto-refresh timer STARTED (1 s)")
            else:
                self.log_msg("auto-refresh enabled; timer will start after connect")
        else:
            self._refresh_timer.stop()
            self.log_msg("auto-refresh timer STOPPED")

    def _on_timer_tick(self) -> None:
        """1-second refresh timer (like SAMBA19xUI dispatcherTimer_Tick).

        Updates loop status LEDs and the current page's state.
        """
        if self.session and self.session.connected:
            main = self.main_tabs.tabText(self.main_tabs.currentIndex())
            sub = self._current_subtab_text()
            try:
                loop = self.session.get_loop_status()
                if hasattr(self, "sb_loop"):
                    self.sb_loop.setText(f"  Loop: {loop.individual:X}/{loop.system:X}  ")
                self._refresh_status_loop_state(
                    loop,
                    include_axis_status=main == "Status" and sub == "Status",
                )
            except Exception as exc:
                self._report_live_refresh_error("loop", exc)
                return

            try:
                if main == "Position" and sub in {"Tuning", "Proxy Adjustment"}:
                    self._refresh_position_live_state()
                elif main == "Controller" and sub == "Motor Protection":
                    # SAMBA19xUI's MotorProtectionPage.UpdateStates() runs on
                    # every dispatcher-timer tick.  Keep this path limited to
                    # live values (actual motor power, failsafe state and the
                    # optional power-supply monitor); configuration fields are
                    # still loaded once by UpdatePage/on_motor_prot_read().
                    self._refresh_motor_protection_live_state(loop)
                elif main == "Status" and sub == "DigIO Status":
                    self._on_digio_read()
                elif main == "Pneumatic" and hasattr(
                    self, "_refresh_pneumatic_live_state"
                ):
                    self._refresh_pneumatic_live_state(loop)
            except Exception as exc:
                self._report_live_refresh_error(f"{main}/{sub}", exc)

    def _sync_port_enabled(self, backend: str) -> None:
        serial = backend == "serial"
        self.port.setEnabled(serial)
        self.baud.setEnabled(serial)

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_connect_page(self) -> None:
        """Connection page — like SAMBA19xUI ConnectionPage."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)

        g = GroupPanel("Connection")
        form = QtWidgets.QFormLayout(g)

        self.conn_info = QtWidgets.QLabel("Not connected")
        self.conn_info.setWordWrap(True)
        form.addRow("Session:", self.conn_info)

        self.fw_version = QtWidgets.QLabel("Firmware Version: —")
        self.fw_version.setWordWrap(True)
        form.addRow("Firmware:", self.fw_version)

        self.sys_config = QtWidgets.QLabel("System Config: —")
        self.sys_config.setWordWrap(True)
        form.addRow("Config:", self.sys_config)

        root.addWidget(g)

        # Raw RCI
        raw_box = GroupPanel("Raw RCI")
        raw_form = QtWidgets.QFormLayout(raw_box)
        self.raw_cmd = QtWidgets.QLineEdit("BGVIS")
        self.raw_params = QtWidgets.QLineEdit()
        self.raw_out = QtWidgets.QPlainTextEdit()
        self.raw_out.setReadOnly(True)
        self.raw_out.setFixedHeight(80)
        raw_form.addRow("Mnemonic:", self.raw_cmd)
        raw_form.addRow("Params:", self.raw_params)
        btn = FlatPush("Send")
        btn.clicked.connect(self.on_raw_send)
        raw_form.addRow(btn)
        raw_form.addRow("Response:", self.raw_out)
        root.addWidget(raw_box)
        root.addStretch(1)
        self.main_tabs.addTab(w, "Connect")

    def _build_controller_tab(self) -> None:
        """Controller tab with sub-tabs: System Setting, AD/DA Mapping, Motor Protection."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)
        self.main_tabs.addTab(tabs, "Controller")

        # System Setting
        sys_w = self._build_system_setting_page()
        tabs.addTab(sys_w, "System Setting")

        # AD/DA Mapping
        adm_w = self._build_ad_da_mapping_page()
        tabs.addTab(adm_w, "AD/DA Mapping")

        # Motor Protection
        mot_w = self._build_motor_protection_page()
        tabs.addTab(mot_w, "Motor Protection")

    def _build_system_setting_page(self) -> QtWidgets.QWidget:
        """System Setting page — loop config, switch, performance, ramp."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setSpacing(6)

        # Output Limit
        ol = QtWidgets.QHBoxLayout()
        ol.addWidget(QtWidgets.QLabel("Output Limit:"))
        self.loop_opl = QtWidgets.QSpinBox()
        self.loop_opl.setRange(0, 100)
        self.loop_opl.setValue(25)
        self.loop_opl.setFixedWidth(60)
        ol.addWidget(self.loop_opl)
        ol.addWidget(QtWidgets.QLabel("%"))
        self.loop_opl_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.loop_opl_slider.setRange(0, 100)
        self.loop_opl_slider.setValue(25)
        self.loop_opl_slider.setFixedWidth(150)
        self.loop_opl.valueChanged.connect(self.loop_opl_slider.setValue)
        self.loop_opl_slider.valueChanged.connect(self.loop_opl.setValue)
        ol.addWidget(self.loop_opl_slider)
        ol.addStretch(1)
        root.addLayout(ol)

        # Loop configuration
        top = QtWidgets.QHBoxLayout()
        g_loop = GroupPanel("Loop Configuration")
        ll = QtWidgets.QGridLayout(g_loop)
        self.chk_vel_cur = QtWidgets.QCheckBox()
        self.chk_vel_cfg = QtWidgets.QCheckBox("Velocity loop")
        self.chk_pneu_cur = QtWidgets.QCheckBox()
        self.chk_pneu_cfg = QtWidgets.QCheckBox("Pneumatic Loop")
        self.chk_pos_cur = QtWidgets.QCheckBox()
        self.chk_pos_cfg = QtWidgets.QCheckBox("Position Loop")
        self.chk_ff_cur = QtWidgets.QCheckBox()
        self.chk_ff_cfg = QtWidgets.QCheckBox("Feed Forward")
        self.chk_stage_cur = QtWidgets.QCheckBox()
        self.chk_stage_cfg = QtWidgets.QCheckBox("Stage")
        self.chk_floor_cur = QtWidgets.QCheckBox()
        self.chk_floor_cfg = QtWidgets.QCheckBox("Floor")
        ll.addWidget(QtWidgets.QLabel("Current"), 0, 0)
        for r, (c1, c2) in enumerate([
            (self.chk_vel_cur, self.chk_vel_cfg),
            (self.chk_pneu_cur, self.chk_pneu_cfg),
            (self.chk_pos_cur, self.chk_pos_cfg),
            (self.chk_ff_cur, self.chk_ff_cfg),
            (self.chk_stage_cur, self.chk_stage_cfg),
            (self.chk_floor_cur, self.chk_floor_cfg),
        ], 1):
            ll.addWidget(c1, r, 0)
            ll.addWidget(c2, r, 1)
        self.btn_set_cfg = FlatPush("Set Configuration")
        self.btn_set_cfg.clicked.connect(self.on_loop_write)
        ll.addWidget(self.btn_set_cfg, 4, 2, 2, 1)
        top.addWidget(g_loop)
        root.addLayout(top)

        # Performance + Switch
        mid = QtWidgets.QHBoxLayout()
        g_perf = GroupPanel("Performance Monitoring")
        pf = QtWidgets.QFormLayout(g_perf)
        self.perf_signal = SciEdit("InpX1FB")
        self.perf_threshold = SciEdit("1.50000e+005")
        self.perf_min_trig = SciEdit("1.00000e-001")
        self.perf_hold = SciEdit("1.00000e+000")
        self.perf_actual = SciEdit("Perf. okay")
        self.perf_actual.setReadOnly(True)
        self.perf_timer = SciEdit("0.000000e+000")
        self.perf_timer.setReadOnly(True)
        self.perf_cfg = SciEdit()
        self.perf_cfg.setVisible(False)
        self.perf_status = self.perf_actual
        self.perf_load = SciEdit()
        self.perf_load.setVisible(False)
        pf.addRow("Signal to:", self.perf_signal)
        pf.addRow("Threshold:", self._unit_row(self.perf_threshold, "µm/s"))
        pf.addRow("Min. trigger:", self._unit_row(self.perf_min_trig, "seconds"))
        pf.addRow("Hold time:", self._unit_row(self.perf_hold, "seconds"))
        pf.addRow("Actual:", self.perf_actual)
        pf.addRow("Timer:", self._unit_row(self.perf_timer, "seconds"))
        mid.addWidget(g_perf, 1)

        g_sw = GroupPanel("Switch criterion")
        sf = QtWidgets.QFormLayout(g_sw)
        self.sw_signal = SciEdit("InpX1FB")
        self.sw_trig = SciEdit("71")
        self.sw_trig_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sw_trig_slider.setRange(0, 100)
        self.sw_trig_slider.setValue(71)
        self.sw_min = SciEdit("5.00000e-001")
        self.sw_hold = SciEdit("1.50000e+001")
        self.sw_fb = SciEdit("V1p0 0.000000")
        self.sw_fb.setReadOnly(True)
        self.sw_cond = SciEdit()
        self.sw_cond.setVisible(False)
        self.sw_cur = self.sw_fb
        sf.addRow("Signal:", self.sw_signal)
        trig_row = QtWidgets.QHBoxLayout()
        trig_row.addWidget(self.sw_trig)
        trig_row.addWidget(QtWidgets.QLabel("%"))
        trig_row.addWidget(self.sw_trig_slider, 1)
        sw = QtWidgets.QWidget()
        sw.setLayout(trig_row)
        sf.addRow("Trigger Level:", sw)
        sf.addRow("Min. trigger:", self._unit_row(self.sw_min, "s"))
        sf.addRow("Hold time:", self._unit_row(self.sw_hold, "s"))
        sf.addRow("FB status:", self.sw_fb)
        mid.addWidget(g_sw, 1)
        root.addLayout(mid)

        # Bottom: Ramp + Floor FF + Sample freq
        bot = QtWidgets.QHBoxLayout()
        g_ramp = GroupPanel("Start-Up Ramp")
        rl = QtWidgets.QHBoxLayout(g_ramp)
        rl.addWidget(QtWidgets.QLabel("Type:"))
        self.ramp_type_combo = QtWidgets.QComboBox()
        self.ramp_type_combo.addItems(["Actuator Output", "Logical Axes"])
        rl.addWidget(self.ramp_type_combo)
        rl.addWidget(QtWidgets.QLabel("Time:"))
        self.ramp_time_edit = SciEdit("1.00000e+000")
        rl.addWidget(self.ramp_time_edit)
        rl.addWidget(QtWidgets.QLabel("s"))
        self.ramp_type = QtWidgets.QSpinBox()
        self.ramp_type.setRange(0, 1)
        self.ramp_type.setVisible(False)
        self.ramp_time = SciSpin()
        self.ramp_time.setVisible(False)
        self.ramp_type_combo.currentIndexChanged.connect(self.ramp_type.setValue)
        bot.addWidget(g_ramp)

        g_fs = GroupPanel("Sample freq")
        fs = QtWidgets.QFormLayout(g_fs)
        self.fs_sample = SciEdit("1836.0")
        self.fs_sample.setReadOnly(True)
        self.fs_load = SciEdit("0.0")
        self.fs_load.setReadOnly(True)
        fs.addRow("Sample:", self._unit_row(self.fs_sample, "Hz"))
        fs.addRow("Load:", self._unit_row(self.fs_load, "%"))
        self.fs_manual = SciEdit()
        self.fs_manual.setFixedWidth(80)
        btn_ok = FlatPush("OK")
        btn_ok.clicked.connect(self.on_loop_limits_write)
        fs_row = QtWidgets.QHBoxLayout()
        fs_row.addWidget(self.fs_manual)
        fs_row.addWidget(btn_ok)
        fs.addRow("Set:", fs_row)
        bot.addWidget(g_fs)
        root.addLayout(bot)

        # Actions
        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all", self.on_controller_read_all),
            ("Write loops...", self.on_loop_write),
            ("Write limits...", self.on_loop_limits_write),
            ("Read performance", self.on_perf_read),
            ("Read switch", self.on_switch_read),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    def _build_ad_da_mapping_page(self) -> QtWidgets.QWidget:
        """AD/DA channel mapping page."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        g = GroupPanel("AD/DA Channel Mapping")
        grid = QtWidgets.QGridLayout(g)

        # Input section
        grid.addWidget(QtWidgets.QLabel("Input"), 0, 0, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Ch"), 0, 3)
        self.adc_edits: list[SciEdit] = []
        for i, name in enumerate(ADC_INPUT_NAMES[:25]):
            r = i + 1
            grid.addWidget(QtWidgets.QLabel(name + ":"), r, 0, 1, 2)
            ed = SciEdit(str(i))
            ed.setFixedWidth(50)
            self.adc_edits.append(ed)
            grid.addWidget(ed, r, 2)

        # Output section
        out_col = 4
        grid.addWidget(QtWidgets.QLabel("Output"), 0, out_col, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Ch"), 0, out_col + 3)
        self.dac_edits: list[SciEdit] = []
        for i, name in enumerate(DAC_OUTPUT_NAMES):
            r = i + 1
            grid.addWidget(QtWidgets.QLabel(name + ":"), r, out_col, 1, 2)
            ed = SciEdit(str(i))
            ed.setFixedWidth(50)
            self.dac_edits.append(ed)
            grid.addWidget(ed, r, out_col + 2)

        root.addWidget(g)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read ADC")
        btn_w = FlatPush("Write ADC...")
        btn_dr = FlatPush("Read DAC")
        btn_dw = FlatPush("Write DAC...")
        btn_r.clicked.connect(self.on_adc_read)
        btn_w.clicked.connect(self.on_adc_write)
        btn_dr.clicked.connect(self.on_dac_read)
        btn_dw.clicked.connect(self.on_dac_write)
        for b in (btn_r, btn_w, btn_dr, btn_dw):
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    def _build_motor_protection_page(self) -> QtWidgets.QWidget:
        """Motor overcurrent protection page."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        g = GroupPanel("Motor Overcurrent Protection")
        form = QtWidgets.QFormLayout(g)
        self.mot_disable = RockerButton("On", "Off")
        self.mot_delay = SciEdit("1.00000e+000")
        self.mot_cool = SciEdit("1.00000e+000")
        self.mot_thresholds: list[SciEdit] = []
        for i in range(12):
            ed = SciEdit("1.00000e+000")
            self.mot_thresholds.append(ed)
        form.addRow("Disable all:", self.mot_disable)
        form.addRow("Reset delay:", self._unit_row(self.mot_delay, "s"))
        form.addRow("Cooling constant:", self.mot_cool)
        for i in range(12):
            form.addRow(f"Motor {i+1} threshold:", self.mot_thresholds[i])
        root.addWidget(g)
        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write...")
        btn_r.clicked.connect(self.on_motor_prot_read)
        btn_w.clicked.connect(self.on_motor_prot_write)
        act.addWidget(btn_r)
        act.addWidget(btn_w)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # Status tab
    # ------------------------------------------------------------------

    def _build_status_tab(self) -> None:
        """Status workspace with the three sub-pages shown in the reference UI."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)

        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(14, 4, 6, 4)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(0)
        loop_group = GroupPanel("Loops Status")
        loop_group.setFixedSize(250, 360)
        loop_grid = QtWidgets.QGridLayout(loop_group)
        self.status_loop_badges: dict[str, SidebarLoopButton] = {}
        loop_names = (
            ("Overall Active", "overall"),
            ("Pneumatic", "pneumatic"),
            ("Feed Forward", "ff"),
            ("Pneumatic FF", "pff"),
            ("Velocity Loop", "velocity"),
            ("Position Loop", "position"),
        )
        for row, (name, key) in enumerate(loop_names):
            loop_grid.addWidget(QtWidgets.QLabel(name), row, 0)
            badge = SidebarLoopButton()
            badge.setFixedSize(58, 44)
            badge.setToolTip(f"Click to toggle {name}")
            badge.clicked.connect(
                lambda _checked=False, loop_key=key: self.loop_states._on_loop_click(
                    loop_key
                )
            )
            self.status_loop_badges[name] = badge
            loop_grid.addWidget(badge, row, 1)
        top.addWidget(loop_group, 0, QtCore.Qt.AlignTop)
        top.addSpacing(30)

        axis_column = QtWidgets.QVBoxLayout()

        def axis_group(
            title: str, names: list[str], kind: str
        ) -> tuple[QtWidgets.QGroupBox, list[SidebarLoopButton]]:
            group = GroupPanel(title)
            group.setFixedSize(490 if len(names) == 6 else 403, 140)
            row = QtWidgets.QHBoxLayout(group)
            lamps: list[SidebarLoopButton] = []
            for axis, name in enumerate(names):
                col = QtWidgets.QVBoxLayout()
                label = QtWidgets.QLabel(name)
                label.setAlignment(QtCore.Qt.AlignCenter)
                lamp = SidebarLoopButton()
                lamp.set_on(False)
                lamp.setFixedSize(64, 58)
                lamp.setToolTip(f"Toggle {kind} individual loop {name}")
                lamp.clicked.connect(
                    lambda _checked=False, loop_kind=kind, selected_axis=axis:
                        self._on_axis_individual_loop_clicked(
                            loop_kind, selected_axis
                        )
                )
                lamps.append(lamp)
                col.addWidget(label)
                col.addWidget(lamp)
                row.addLayout(col)
            return group, lamps

        velocity_group, self.status_velocity_axis_lamps = axis_group(
            "Velocity Individual Loop Status",
            ["Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"],
            "velocity",
        )
        axis_column.addWidget(velocity_group)
        axis_column.addSpacing(28)
        position_group, self.status_position_axis_lamps = axis_group(
            "Position Individual Loop Status",
            ["Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans"],
            "position",
        )
        axis_column.addWidget(position_group)
        axis_column.addStretch(1)
        top.addLayout(axis_column)
        top.addSpacing(140)
        pneumatic_group, self.status_pneumatic_axis_lamps = axis_group(
            "Pneumatic Individual Loop Status",
            ["Ztpneu", "Yrpneu", "Xrpneu"],
            "pneumatic",
        )
        top.addWidget(pneumatic_group, 0, QtCore.Qt.AlignTop)
        top.addStretch(1)
        root.addLayout(top)

        event_group = GroupPanel("Event")
        event_layout = QtWidgets.QVBoxLayout(event_group)
        event_layout.setContentsMargins(8, 18, 8, 8)
        self.status_events = QtWidgets.QTableWidget(0, 8)
        self.status_events.setHorizontalHeaderLabels([
            "Event#", "DigInpWord", "Amplifier1", "Ampl#1 Temp Err",
            "Ampl#1 Pwr Err", "Amplifier2", "Ampl#2 Temp Err", "Ampl#2 Pwr Err",
        ])
        self.status_events.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        self.status_events.verticalHeader().setVisible(False)
        self.status_events.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.status_events.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.status_events.setMinimumHeight(220)
        event_layout.addWidget(self.status_events)
        root.addWidget(event_group, 1)

        # Compatibility labels updated by the existing refresh handlers.
        self.lbl_fw = QtWidgets.QLabel("-")
        self.lbl_loop = QtWidgets.QLabel("-")
        self.lbl_fs = QtWidgets.QLabel("-")
        self.lbl_geo = QtWidgets.QLabel("-")
        self.lbl_opl = QtWidgets.QLabel("-")
        self.lbl_switch = QtWidgets.QLabel("-")
        for label in (
            self.lbl_fw, self.lbl_loop, self.lbl_fs, self.lbl_geo,
            self.lbl_opl, self.lbl_switch,
        ):
            label.hide()

        tabs.addTab(w, "Status")
        if hasattr(self, "_build_signal_display_page"):
            tabs.addTab(self._build_signal_display_page(), "Signals Display")
        else:
            tabs.addTab(QtWidgets.QWidget(), "Signals Display")
        if hasattr(self, "_build_digio_tab"):
            tabs.addTab(self._build_digio_tab(), "DigIO Status")
        else:
            tabs.addTab(QtWidgets.QWidget(), "DigIO Status")
        self.main_tabs.addTab(tabs, "Status")

    # ------------------------------------------------------------------
    # Velocity tab
    # ------------------------------------------------------------------

    def _build_velocity_tab(self) -> None:
        """Velocity tab with sub-tabs: Filter, Matrix."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)
        self.main_tabs.addTab(tabs, "Velocity")

        # Filter page
        filt_w = self._build_vel_filter_page()
        tabs.addTab(filt_w, "Tuning")

        # Matrix page
        mat_w = self._build_vel_matrix_page()
        tabs.addTab(mat_w, "Sensor/Motor Matrix")

    def _build_vel_filter_page(self) -> QtWidgets.QWidget:
        """Velocity filter page — FilterMatrix2 grid (6 axes × 7 stages).

        Layout from SAMBA19xUI VelFilterPage:
          FilterMatrix2 grid (clickable buttons per axis×stage)
          + Excitation (noise injection)
          + Diagnostic signals
          + Velocity THH (tuning helping hand)
        """
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(14, 4, 6, 4)
        root.setSpacing(4)

        top_area = QtWidgets.QHBoxLayout()
        top_area.setSpacing(8)
        left_column = QtWidgets.QVBoxLayout()
        right_column = QtWidgets.QVBoxLayout()
        top_area.addLayout(left_column, 13)
        top_area.addLayout(right_column, 7)

        # FilterMatrix2: 6×7 grid of clickable filter buttons
        g_filt = GroupPanel("Filter Matrix")
        g_filt.setFixedHeight(610)
        grid = QtWidgets.QGridLayout(g_filt)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, 82)
        grid.setColumnStretch(10, 1)
        grid.setRowStretch(7, 1)

        # Column headers (stage names)
        stage_labels = ["Fil1", "Fil2", "Fil3", "Fil4", "Fil5", "Fil6", "Fil7"]
        for j, label in enumerate(stage_labels):
            lbl = QtWidgets.QLabel(label)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:500; color:#111111; font-size:16px;")
            grid.addWidget(lbl, 0, j + 1)
        status_header = QtWidgets.QLabel("Axis")
        status_header.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(status_header, 0, 8)
        limiter_header = QtWidgets.QLabel("Axis Limiter")
        limiter_header.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(limiter_header, 0, 9)

        # Axis labels + filter buttons
        self.vel_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
        self.vel_filter_axis_leds: list[LedIndicator] = []
        self.vel_axis_limiters: list[SciEdit] = []
        for ax in range(6):
            # Axis label
            lbl = QtWidgets.QLabel(VEL_AXES_NAMES[ax])
            lbl.setStyleSheet("font-weight:500; color:#111111; font-size:16px;")
            grid.addWidget(lbl, ax + 1, 0)
            # Axis state rocker and limiter follow the filter cells in the
            # original tuning page.
            led = SidebarLoopButton()
            led.set_on(True)
            led.setToolTip(
                f"Toggle velocity individual loop {VEL_AXES_NAMES[ax]}"
            )
            led.clicked.connect(
                lambda _checked=False, axis=ax:
                    self._on_axis_individual_loop_clicked("velocity", axis)
            )
            self.vel_filter_axis_leds.append(led)

            for st in range(7):
                cell = FilterStageCell(st, "---", width=88, height=78)
                cell.clicked.connect(lambda s=st, a=ax: self._on_vel_filter_cell_clicked(a, s))
                self.vel_filter_buttons[(ax, st)] = cell
                grid.addWidget(cell, ax + 1, st + 1)
            grid.addWidget(led, ax + 1, 8)
            led.setFixedSize(58, 58)
            limiter = SciEdit("10000")
            limiter.setFixedWidth(155)
            limiter.editingFinished.connect(
                lambda axis=ax: self.on_vel_limiter_write(axis)
            )
            self.vel_axis_limiters.append(limiter)
            grid.addWidget(limiter, ax + 1, 9)

        left_column.addWidget(g_filt)
        left_column.addStretch(1)

        # Hidden FilterEditor for RCI handlers
        self.vel_filter = FilterEditor(VEL_AXIS_LABELS, max_stage=6)
        self.vel_filter.setVisible(False)

        # Classic filter panel (shown when a cell is clicked)
        self.vel_filter_panel = ClassicFilterPanel("Velocity filter (click a cell above)")
        self.vel_filter_panel.read_clicked.connect(self.on_vel_read_classic)
        self.vel_filter_panel.write_clicked.connect(self.on_vel_write_classic)
        self.vel_filter_panel.stage_changed.connect(self._sync_vel_panel_to_editor)
        self.vel_filter_panel.hide()

        # Sensor + Motor matrices
        mats = QtWidgets.QHBoxLayout()
        g_s = GroupPanel("Sensor Matrix")
        sg = QtWidgets.QGridLayout(g_s)
        self.vel_sens_edits: list[SciEdit] = []
        for i, name in enumerate(VEL_INPUT_NAMES_7):
            sg.addWidget(QtWidgets.QLabel(name), i, 0)
            ed = SciEdit("0.00000e+000")
            if name == "X2FB":
                ed.setText("1.00000e+000")
            self.vel_sens_edits.append(ed)
            sg.addWidget(ed, i, 1)
        self.vel_sens = MatrixEditor(7)
        self.vel_sens.setVisible(False)
        mats.addWidget(g_s)

        g_m = GroupPanel("Motor Matrix")
        mg = QtWidgets.QGridLayout(g_m)
        self.vel_motor_edits: list[SciEdit] = []
        for i, name in enumerate(VEL_OUTPUT_NAMES):
            col = 0 if i < 6 else 2
            row = i if i < 6 else i - 6
            mg.addWidget(QtWidgets.QLabel(name), row, col)
            default = "0.00000e+000"
            if name == "OutX2":
                default = "5.00000e-001"
            elif name == "OutX4":
                default = "-5.00000e-001"
            ed = SciEdit(default)
            self.vel_motor_edits.append(ed)
            mg.addWidget(ed, row, col + 1)
        self.vel_motor = MatrixEditor(12)
        self.vel_motor.setVisible(False)
        mats.addWidget(g_m)
        g_s.hide()
        g_m.hide()

        # Excitation / Diagnostic
        ex_diag = QtWidgets.QHBoxLayout()
        g_ex = GroupPanel("Excitation")
        ef = QtWidgets.QFormLayout(g_ex)
        self.noise_inject = IOSignalButton(
            "Vel Xtrans Stage7",
            tokens=(2, 0, 6),
            supported_io=IOSignalButton.CORE_SIGNALS,
            position_stages=4,
        )
        self.noise_type = QtWidgets.QComboBox()
        for value, label in enumerate(
            ["No noise", "Random/White", "Sine", "Duty cycle", "Chirp sine"]
        ):
            self.noise_type.addItem(label, value)
        self.noise_gain = SciEdit("1.00000e-001")
        self.noise_freq = SciEdit("9.00000e-001")
        self._noise_gain_spin = SciSpin()
        self._noise_gain_spin.setValue(0.1)
        self._noise_gain_spin.setVisible(False)
        self.noise_filt_usage = QtWidgets.QComboBox()
        self.noise_filt_usage.addItems(["F", "N"])
        self.noise_filt_usage.setVisible(False)
        self.noise_excit = SciEdit()
        self.noise_excit.setVisible(False)
        ef.addRow("Injection:", self.noise_inject)
        ef.addRow("Type:", self.noise_type)
        gain_row = QtWidgets.QHBoxLayout()
        gain_row.addWidget(self.noise_gain)
        gain_row.addStretch(1)
        gw = QtWidgets.QWidget()
        gw.setLayout(gain_row)
        ef.addRow("Gain:", gw)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self.noise_freq)
        fr.addWidget(QtWidgets.QLabel("Hz"))
        fr.addStretch(1)
        fw = QtWidgets.QWidget()
        fw.setLayout(fr)
        ef.addRow("Frequency:", fw)
        accept_noise = FlatPush("Accept Change")
        accept_noise.clicked.connect(self.on_diag_write_classic)
        ef.addRow("", accept_noise)
        right_column.addWidget(g_ex)

        excitation_filters = GroupPanel("")
        excitation_filter_grid = QtWidgets.QGridLayout(excitation_filters)
        excitation_filter_grid.setContentsMargins(8, 10, 8, 8)
        excitation_filter_grid.setHorizontalSpacing(5)
        self.exc_filter_buttons: list[FilterStageCell] = []
        for stage in range(4):
            stage_label = QtWidgets.QLabel(f"Fil{stage + 1}")
            stage_label.setAlignment(QtCore.Qt.AlignCenter)
            cell = FilterStageCell(stage, "---", width=88, height=70)
            cell.clicked.connect(
                lambda _clicked_stage, selected=stage:
                    self._on_excitation_filter_clicked(selected)
            )
            self.exc_filter_buttons.append(cell)
            excitation_filter_grid.addWidget(stage_label, 0, stage)
            excitation_filter_grid.addWidget(cell, 1, stage)
        self.exc_filter_usage_label = QtWidgets.QLabel("OFF")
        self.exc_filter_usage_label.setObjectName("classicStatusBadge")
        self.exc_filter_usage_label.setAlignment(QtCore.Qt.AlignCenter)
        self.exc_filter_usage_label.setFixedSize(58, 44)
        excitation_filter_grid.addWidget(
            self.exc_filter_usage_label, 1, 4, QtCore.Qt.AlignCenter
        )
        self.vel_excitation_filter_expander = ClassicExpander(
            "Excitation Filters", excitation_filters, expanded=True
        )
        right_column.addWidget(self.vel_excitation_filter_expander)

        g_diag = GroupPanel("Diagnostics")
        df = QtWidgets.QFormLayout(g_diag)
        self.diag_0 = IOSignalButton(
            "Vel Xtrans Output",
            tokens=(4, 0, 0),
            supported_io=IOSignalButton.CORE_SIGNALS,
            position_stages=4,
        )
        self.diag_1 = IOSignalButton(
            "Vel Xtrans Stage7",
            tokens=(2, 0, 6),
            supported_io=IOSignalButton.CORE_SIGNALS,
            position_stages=4,
        )
        self.diag_outputs = SciEdit()
        self.diag_outputs.setVisible(False)
        self.test_mode = SciEdit()
        self.test_mode.setVisible(False)
        self.dig_trace_info = SciEdit()
        self.dig_trace_info.setPlaceholderText("type main sub delay samples undersample")
        self.dig_trace_status = SciEdit()
        self.dig_trace_status.setReadOnly(True)
        self.dig_trace_buf = QtWidgets.QPlainTextEdit()
        self.dig_trace_buf.setReadOnly(True)
        self.dig_trace_buf.setPlaceholderText("Digital trace samples")
        self.dig_trace_buf.setMaximumHeight(80)
        df.addRow("Diag 0:", self.diag_0)
        df.addRow("Diag 1:", self.diag_1)
        self.dig_trace_info.hide()
        self.dig_trace_status.hide()
        self.dig_trace_buf.hide()
        trace_actions = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Start trace", self.on_diag_trace_start),
            ("Trace status", self.on_diag_trace_status),
            ("Read buffer", self.on_diag_trace_read_buffer),
        ):
            button = FlatPush(text)
            button.clicked.connect(slot)
            trace_actions.addWidget(button)
        right_column.addWidget(g_diag)

        loop_status = GroupPanel("Velocity Individual Loop Status")
        loop_row = QtWidgets.QHBoxLayout(loop_status)
        self.vel_individual_loop_buttons = []
        for axis, axis_name in enumerate(VEL_AXES_NAMES):
            col = QtWidgets.QVBoxLayout()
            label = QtWidgets.QLabel(axis_name)
            label.setAlignment(QtCore.Qt.AlignCenter)
            lamp = SidebarLoopButton()
            lamp.set_on(True)
            lamp.setFixedSize(48, 45)
            lamp.setToolTip(f"Toggle velocity individual loop {axis_name}")
            lamp.clicked.connect(
                lambda _checked=False, selected_axis=axis:
                    self._on_axis_individual_loop_clicked(
                        "velocity", selected_axis
                    )
            )
            self.vel_individual_loop_buttons.append(lamp)
            col.addWidget(label)
            col.addWidget(lamp)
            loop_row.addLayout(col)
        right_column.addWidget(loop_status)

        helping = GroupPanel("Tuning Helping Hand")
        helping_layout = QtWidgets.QVBoxLayout(helping)
        measure = QtWidgets.QHBoxLayout()
        measure.addWidget(QtWidgets.QLabel("Measure after Stage:"))
        self.vel_measure_stage = QtWidgets.QComboBox()
        self.vel_measure_stage.addItems([
            "Raw", "Stage1", "Stage2", "Stage3", "Stage4",
            "Stage5", "Stage6", "Stage7", "Output",
        ])
        self.vel_measure_stage.currentIndexChanged.connect(
            lambda _index: self._on_vel_help_selection_changed()
        )
        measure.addWidget(self.vel_measure_stage)
        measure.addStretch(1)
        helping_layout.addLayout(measure)
        help_row = QtWidgets.QHBoxLayout()
        self.vel_help_axis_buttons = []
        self._vel_help_axis = 0
        for axis, axis_name in enumerate(VEL_AXES_NAMES):
            col = QtWidgets.QVBoxLayout()
            label = QtWidgets.QLabel(axis_name)
            label.setAlignment(QtCore.Qt.AlignCenter)
            switch = SidebarLoopButton()
            switch.setFixedSize(48, 42)
            switch.clicked.connect(
                lambda _checked=False, selected_axis=axis:
                    self._on_vel_help_selection_changed(selected_axis)
            )
            self.vel_help_axis_buttons.append(switch)
            col.addWidget(label)
            col.addWidget(switch)
            help_row.addLayout(col)
        helping_layout.addLayout(help_row)
        right_column.addWidget(helping)
        self._on_vel_help_selection_changed(0)
        right_column.addStretch(1)
        root.addLayout(top_area)

        # Action buttons
        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all filters", self.on_vel_read_all_filters),
            ("Read filter", self.on_vel_read_classic),
            ("Write filter...", self.on_vel_write_classic),
            ("Read matrices", self.on_vel_mat_read_classic),
            ("Write matrices...", self.on_vel_mat_write_classic),
            ("Read noise", self.on_diag_read_classic),
            ("Write noise...", self.on_diag_write_classic),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            b.hide()
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)

        self.vel_stage_bar.set_current(0, emit=False)
        return w

    def _build_vel_matrix_page(self) -> QtWidgets.QWidget:
        """Velocity matrix page — 7×6 sensor matrix + 6×12 motor matrix.

        From SAMBA19xUI VelMatrixPage:
          InputSteeringMatrix (7 or 8 rows × 6 cols)
          OutputSteeringMatrix (6 rows × 12 cols)
        """
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(6)

        # Input Steering Matrix (Sensor)
        g_s = GroupPanel("Input Steering Matrix (Sensor)")
        sg = QtWidgets.QGridLayout(g_s)
        sg.setHorizontalSpacing(6)
        sg.setVerticalSpacing(3)

        # Headers
        for i, name in enumerate(VEL_INPUT_NAMES_7):
            lbl = QtWidgets.QLabel(name)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
            sg.addWidget(lbl, 0, i + 1)

        self.vel_inp_edits: list[list[SciEdit]] = []
        for ax in range(6):
            lbl = QtWidgets.QLabel(VEL_AXES_NAMES[ax])
            lbl.setStyleSheet("font-weight:600;")
            sg.addWidget(lbl, ax + 1, 0)
            row_edits = []
            for ch in range(7):
                ed = SciEdit("0.00000e+000")
                if ch == 1:
                    ed.setText("1.00000e+000")
                row_edits.append(ed)
                sg.addWidget(ed, ax + 1, ch + 1)
            self.vel_inp_edits.append(row_edits)
        root.addWidget(g_s)

        # Output Steering Matrix (Motor)
        g_m = GroupPanel("Output Steering Matrix (Motor)")
        mg = QtWidgets.QGridLayout(g_m)
        mg.setHorizontalSpacing(6)
        mg.setVerticalSpacing(3)

        for i, name in enumerate(VEL_OUTPUT_NAMES):
            lbl = QtWidgets.QLabel(name)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
            mg.addWidget(lbl, 0, i + 1)

        self.vel_out_edits: list[list[SciEdit]] = []
        for ax in range(6):
            lbl = QtWidgets.QLabel(VEL_AXES_NAMES[ax])
            lbl.setStyleSheet("font-weight:600;")
            mg.addWidget(lbl, ax + 1, 0)
            row_edits = []
            for ch in range(12):
                ed = SciEdit("0.00000e+000")
                row_edits.append(ed)
                mg.addWidget(ed, ax + 1, ch + 1)
            self.vel_out_edits.append(row_edits)
        root.addWidget(g_m)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all", self.on_vel_mat_read_classic),
            ("Write all...", self.on_vel_mat_write_classic),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        # Keep legacy refs
        self.vel_sens_panel = self.vel_inp_edits
        self.vel_motor_panel = self.vel_out_edits
        return w

    # ------------------------------------------------------------------
    # Position tab
    # ------------------------------------------------------------------

    def _build_position_tab(self) -> None:
        """Position tab with sub-tabs: Filter, Sensor Matrix, Motor Matrix."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)
        self.main_tabs.addTab(tabs, "Position")

        filt_w = self._build_pos_filter_page()
        tabs.addTab(filt_w, "Tuning")

        sens_w = self._build_pos_sensor_matrix_page()
        tabs.addTab(sens_w, "Sensor Matrix")

        mot_w = self._build_pos_motor_matrix_page()
        tabs.addTab(mot_w, "Motor Matrix")

        tabs.addTab(self._build_pos_proxy_page(), "Proxy Adjustment")

    def _build_pos_proxy_page(self) -> QtWidgets.QWidget:
        """Large proximity readouts arranged like the supplied adjustment page."""
        page = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.proxy_value_labels: dict[str, QtWidgets.QLabel] = {}
        self.proxy_cards: dict[str, QtWidgets.QFrame] = {}
        self.proxy_si_unit_edits: dict[str, QtWidgets.QLineEdit] = {}
        for index, name in enumerate(PROX_DISPLAY_NAMES):
            row, col = divmod(index, 4)
            card = QtWidgets.QFrame()
            card.setObjectName("proxyReadoutCard")
            layout = QtWidgets.QVBoxLayout(card)
            layout.setContentsMargins(12, 10, 12, 0)
            unit_row = QtWidgets.QHBoxLayout()
            unit_row.addWidget(QtWidgets.QLabel("SIUnit[digits/µm]:"))
            unit = QtWidgets.QLineEdit("1")
            unit.setMaximumWidth(120)
            unit.setToolTip("Proximity conversion factor in digits per micrometre")
            unit.editingFinished.connect(self._on_proximity_si_unit_changed)
            self.proxy_si_unit_edits[name] = unit
            unit_row.addWidget(unit)
            unit_row.addStretch(1)
            layout.addLayout(unit_row)
            title = QtWidgets.QLabel(f"{name}[µm]")
            title.setObjectName("proxyReadoutTitle")
            title.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(title)
            value = QtWidgets.QLabel("0")
            value.setObjectName("proxyReadoutValue")
            value.setAlignment(QtCore.Qt.AlignCenter)
            value.setProperty("alternate", row == 1)
            self.proxy_value_labels[name] = value
            self.proxy_cards[name] = card
            layout.addWidget(value, 1)
            grid.addWidget(card, row, col)
        self._configure_proximity_widgets(self._proximity_count)
        grid.setRowStretch(2, 1)
        return page

    def _build_pos_filter_page(self) -> QtWidgets.QWidget:
        """Position filter page — FilterMatrix2 grid (6 axes × 12 stages).

        From SAMBA19xUI PosFilterPage:
          FilterMatrix2 grid (NumPosAxes × NumPosFilt)
          + Proximity offsets
          + Loop switch
          + Excitation
          + Diagnostic signals
          + Position THH
        """
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # FilterMatrix2: 6×4 grid of clickable filter buttons
        g_filt = GroupPanel("Position Filter Matrix")
        grid = QtWidgets.QGridLayout(g_filt)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        n_pos_axes = 6
        n_pos_stages = 4

        # Column headers
        stage_labels = ["Fil1", "Fil2", "Fil3", "Fil4"]
        for j, label in enumerate(stage_labels):
            lbl = QtWidgets.QLabel(label)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
            grid.addWidget(lbl, 0, j + 1)

        # Axis labels + filter buttons
        self.pos_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
        self.pos_filter_axis_leds: list[LedIndicator] = []
        for ax in range(n_pos_axes):
            lbl = QtWidgets.QLabel(POS_AXES_NAMES[ax])
            lbl.setStyleSheet("font-weight:600; color:#303030;")
            grid.addWidget(lbl, ax + 1, 0)
            led = LedIndicator(10)
            self.pos_filter_axis_leds.append(led)
            grid.addWidget(led, ax + 1, 0, QtCore.Qt.AlignRight)

            for st in range(n_pos_stages):
                cell = FilterStageCell(st, f"S{st}", width=44, height=48)
                cell.clicked.connect(lambda s=st, a=ax: self._on_pos_filter_cell_clicked(a, s))
                self.pos_filter_buttons[(ax, st)] = cell
                grid.addWidget(cell, ax + 1, st + 1)

        root.addWidget(g_filt)

        self.pos_filter = FilterEditor(POS_AXIS_LABELS, max_stage=3)
        self.pos_filter.setVisible(False)

        self.pos_filter_panel = ClassicFilterPanel("Position filter (click a cell above)")
        self.pos_filter_panel.read_clicked.connect(self.on_pos_read_classic)
        self.pos_filter_panel.write_clicked.connect(self.on_pos_write_classic)
        self.pos_filter_panel.stage_changed.connect(self._sync_pos_panel_to_editor)
        root.addWidget(self.pos_filter_panel)

        # Proximity offsets
        g_prox = GroupPanel("Proximity offsets")
        prox_grid = QtWidgets.QGridLayout(g_prox)
        self.prox_edits = []
        for i, name in enumerate(["Prox1", "Prox2", "Prox3", "ProxH1", "ProxH2", "ProxH3"]):
            col = 0 if i < 3 else 2
            row = i if i < 3 else i - 3
            prox_grid.addWidget(QtWidgets.QLabel(name), row, col)
            ed = SciEdit("0.00000e+000")
            self.prox_edits.append(ed)
            prox_grid.addWidget(ed, row, col + 1)
            prox_grid.addWidget(QtWidgets.QLabel("µ"), row, col + 2)
        self.prox_off = MatrixEditor(6)
        self.prox_off.setVisible(False)
        brow = QtWidgets.QHBoxLayout()
        self.btn_pos_cauco = FlatPush("Use current as offsets")
        self.btn_pos_cauco.clicked.connect(self.on_prox_cauco)
        brow.addWidget(self.btn_pos_cauco)
        brow.addStretch(1)
        prox_grid.addLayout(brow, 3, 0, 1, 5)
        root.addWidget(g_prox)

        # Loop switch
        g_sw = GroupPanel("Loop switch")
        sw = QtWidgets.QHBoxLayout(g_sw)
        for led_on, name, attr in (
            (True, "Velocity", "pos_sw_vel"),
            (False, "Position", "pos_sw_pos"),
            (False, "Auto", "pos_sw_auto"),
        ):
            col = QtWidgets.QVBoxLayout()
            led = LedIndicator()
            led.set_on(led_on)
            rk = RockerButton("On", "Off")
            if name == "Auto":
                rk.setChecked(True)
            setattr(self, f"led_{attr}", led)
            setattr(self, attr, rk)
            col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(rk, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            sw.addLayout(col)
        root.addWidget(g_sw)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all filters", self.on_pos_read_all_filters),
            ("Read filter", self.on_pos_read_classic),
            ("Write filter...", self.on_pos_write_classic),
            ("Read offsets", self.on_prox_read_classic),
            ("Write offsets...", self.on_prox_write_classic),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        return w

    def _on_pos_filter_cell_clicked(self, axis: int, stage: int) -> None:
        """User clicked a position filter cell."""
        self.pos_filter.axis.setCurrentIndex(axis)
        self.pos_filter.stage.setValue(stage)
        self.pos_filter_panel.set_stage_index(stage)

        if self.session and self.session.connected:
            self.on_pos_read_classic()

        dlg = FilterDlg(POS_AXIS_LABELS, max_stage=3, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Position Filter — Axis {POS_AXES_NAMES[axis]}, Stage {stage}")
        fs = self.pos_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setCurrentIndex(axis)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, _all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.pos_filter.set_stage(new_stage)
            self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self._update_pos_cell_text(axis, stage)
            if all_axes:
                for ax in range(6):
                    s = FilterStage(ax, new_stage.stage, new_stage.filter_type, new_stage.params)
                    self.pos_filter.axis.setCurrentIndex(ax)
                    self.pos_filter.set_stage(s)
                    self.on_pos_write()
                    self._update_pos_cell_text(ax, stage)
            else:
                self.on_pos_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _update_pos_cell_text(self, axis: int, stage: int) -> None:
        key = (axis, stage)
        if key in self.pos_filter_buttons:
            try:
                name = self.pos_filter.ftype.currentText().split(None, 1)[-1]
                short = name[:5] if len(name) > 5 else name
            except Exception:
                short = ""
            self.pos_filter_buttons[key].set_info(short)

    def on_pos_read_all_filters(self) -> None:
        """Read all position filters and update cell texts."""
        def work() -> None:
            s = self._require_session()
            first_stage = None
            for ax in range(6):
                for st in range(4):
                    try:
                        fs = s.get_proximity_filter(ax, st)
                        if ax == 0 and st == 0:
                            first_stage = fs
                        self.pos_filter_buttons[(ax, st)].set_info(fs.type_name[:5])
                    except Exception:
                        self.pos_filter_buttons[(ax, st)].set_info("?")
            if first_stage is not None:
                self.pos_filter.set_stage(first_stage)
                self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self.log_msg("position filters all 24 read")
        self._run("Read all position filters", work)

    def _build_pos_sensor_matrix_page(self) -> QtWidgets.QWidget:
        """Position sensor matrix page."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        g = GroupPanel("Sensor Matrix")
        grid = QtWidgets.QGridLayout(g)
        self.pos_sens_edits = []
        for i, name in enumerate(["Prox1", "Prox2", "Prox3", "ProxH1", "ProxH2", "ProxH3"]):
            grid.addWidget(QtWidgets.QLabel(name), i, 0)
            ed = SciEdit("0.00000e+000")
            self.pos_sens_edits.append(ed)
            grid.addWidget(ed, i, 1)
        self.pos_sens = MatrixEditor(6)
        self.pos_sens.setVisible(False)
        self.pos_sensor_dev = SciEdit()
        self.pos_sensor_dev.setVisible(False)
        root.addWidget(g)

        self.pos_sens_axis = QtWidgets.QComboBox()
        for name in POS_AXES_NAMES[:6]:
            self.pos_sens_axis.addItem(name)
        ax_row = QtWidgets.QHBoxLayout()
        ax_row.addWidget(QtWidgets.QLabel("Axis:"))
        ax_row.addWidget(self.pos_sens_axis)
        ax_row.addStretch(1)
        root.addLayout(ax_row)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write...")
        btn_r.clicked.connect(lambda: self.on_pos_mat_read("sensor"))
        btn_w.clicked.connect(lambda: self.on_pos_mat_write("sensor"))
        act.addWidget(btn_r)
        act.addWidget(btn_w)
        act.addStretch(1)
        root.addLayout(act)
        return w

    def _build_pos_motor_matrix_page(self) -> QtWidgets.QWidget:
        """Position motor matrix page."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        g = GroupPanel("Motor Matrix")
        grid = QtWidgets.QGridLayout(g)
        self.pos_motor_edits = []
        for i in range(8):
            grid.addWidget(QtWidgets.QLabel(f"Motor {i+1}:"), i, 0)
            ed = SciEdit("0.00000e+000")
            self.pos_motor_edits.append(ed)
            grid.addWidget(ed, i, 1)
        self.pos_motor = MatrixEditor(8)
        self.pos_motor.setVisible(False)
        self.pos_motor_dev = SciEdit()
        self.pos_motor_dev.setVisible(False)
        self.pos_motor_off = SciEdit()
        self.pos_motor_off.setVisible(False)
        root.addWidget(g)

        self.pos_motor_axis = QtWidgets.QComboBox()
        for name in POS_AXES_NAMES[:6]:
            self.pos_motor_axis.addItem(name)
        ax_row = QtWidgets.QHBoxLayout()
        ax_row.addWidget(QtWidgets.QLabel("Axis:"))
        ax_row.addWidget(self.pos_motor_axis)
        ax_row.addStretch(1)
        root.addLayout(ax_row)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write...")
        btn_r.clicked.connect(lambda: self.on_pos_mat_read("motor"))
        btn_w.clicked.connect(lambda: self.on_pos_mat_write("motor"))
        act.addWidget(btn_r)
        act.addWidget(btn_w)
        act.addStretch(1)
        root.addLayout(act)
        return w

    # ------------------------------------------------------------------
    # Pneumatic tab
    # ------------------------------------------------------------------

    def _build_pneumatic_tab(self) -> None:
        """Pneumatic tab — single page with 3 axes × 4 stages grid."""
        from python_samba.ui.extra_pages import PNEUM_AXIS_LABELS

        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # Pneumatic filter grid: 3 axes × 4 stages
        g_filt = GroupPanel("Pneumatic Filter Matrix")
        grid = QtWidgets.QGridLayout(g_filt)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        for j in range(4):
            lbl = QtWidgets.QLabel(f"Fil{j+1}")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
            grid.addWidget(lbl, 0, j + 1)

        self.pneum_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
        for ax in range(3):
            lbl = QtWidgets.QLabel(PNEU_AXES_NAMES[ax])
            lbl.setStyleSheet("font-weight:600; color:#303030;")
            grid.addWidget(lbl, ax + 1, 0)
            for st in range(4):
                cell = FilterStageCell(st, f"S{st}", width=40, height=44)
                cell.clicked.connect(lambda s=st, a=ax: self._on_pneum_filter_cell_clicked(a, s))
                self.pneum_filter_buttons[(ax, st)] = cell
                grid.addWidget(cell, ax + 1, st + 1)

        root.addWidget(g_filt)

        self.pneum_axis_combo = QtWidgets.QComboBox()
        for name in PNEU_AXES_NAMES:
            self.pneum_axis_combo.addItem(name)
        self.pneum_filter = FilterEditor(PNEUM_AXIS_LABELS, max_stage=3)
        self.pneum_filter.setVisible(False)
        self.pneum_filter_panel = ClassicFilterPanel("Pneumatic filter (click a cell above)")
        self.pneum_filter_panel.read_clicked.connect(self.on_pneum_filter_read)
        self.pneum_filter_panel.write_clicked.connect(self.on_pneum_filter_write)
        root.addWidget(self.pneum_filter_panel)

        # Steering matrix: 3 axes × 8 inputs + 4 outputs
        steer = QtWidgets.QHBoxLayout()
        g_s = GroupPanel("Sensor matrix")
        sf = QtWidgets.QGridLayout(g_s)
        self.pneum_prox = []
        for i, name in enumerate(("Prox1", "Prox2", "Prox3", "Prox4")):
            ed = SciEdit("2.50000e-001")
            self.pneum_prox.append(ed)
            r, c = divmod(i, 2)
            sf.addWidget(QtWidgets.QLabel(name + ":"), r, c * 2)
            sf.addWidget(ed, r, c * 2 + 1)
        steer.addWidget(g_s)

        g_m = GroupPanel("Motor matrix")
        mf = QtWidgets.QGridLayout(g_m)
        self.pneum_valve = []
        for i, name in enumerate(("Valve1", "Valve2", "Valve3", "Valve4")):
            ed = SciEdit("-2.50000e-001")
            self.pneum_valve.append(ed)
            r, c = divmod(i, 2)
            mf.addWidget(QtWidgets.QLabel(name + ":"), r, c * 2)
            mf.addWidget(ed, r, c * 2 + 1)
        steer.addWidget(g_m)
        root.addLayout(steer)

        self.pneum_steer = MatrixEditor(8)
        self.pneum_steer.setVisible(False)
        self.pneum_prox_status = QtWidgets.QLabel("-")
        self.pneum_status = QtWidgets.QLabel("-")
        self.pneum_heights = QtWidgets.QLabel("-")

        # Move system
        g_mv = GroupPanel("Move system")
        mv = QtWidgets.QHBoxLayout(g_mv)
        self.btn_move_up = FlatPush("Up")
        self.btn_move_down = FlatPush("Down")
        self.btn_move_up.setFixedSize(64, 48)
        self.btn_move_down.setFixedSize(64, 48)
        mv.addWidget(QtWidgets.QLabel("Up"))
        mv.addWidget(self.btn_move_up)
        mv.addWidget(QtWidgets.QLabel("Down"))
        mv.addWidget(self.btn_move_down)
        mv.addStretch(1)
        root.addWidget(g_mv)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all filters", self.on_pneum_read_all_filters),
            ("Read filter", self.on_pneum_filter_read),
            ("Write filter...", self.on_pneum_filter_write),
            ("Read steer matrix", self.on_pneum_steer_read),
            ("Write steer matrix...", self.on_pneum_steer_write),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        self.main_tabs.addTab(w, "Pneumatic")

    def _on_pneum_filter_cell_clicked(self, axis: int, stage: int) -> None:
        """User clicked a pneumatic filter cell."""
        self.pneum_axis_combo.setCurrentIndex(axis)
        self.pneum_filter.axis.setCurrentIndex(axis)
        self.pneum_filter.stage.setValue(stage)
        self.pneum_filter_panel.set_stage_index(stage)
        if self.session and self.session.connected:
            self.on_pneum_filter_read()
        dlg = FilterDlg(PNEUM_AXIS_LABELS, max_stage=3, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Pneumatic Filter — Axis {PNEU_AXES_NAMES[axis]}, Stage {stage}")
        fs = self.pneum_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setCurrentIndex(axis)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, _all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.pneum_filter.set_stage(new_stage)
            self.pneum_filter_panel.set_from_filter_editor(self.pneum_filter)
            self._update_pneum_cell_text(axis, stage)
            if all_axes:
                for ax in range(3):
                    s = FilterStage(ax, stage, new_stage.filter_type, new_stage.params)
                    self.pneum_filter.axis.setCurrentIndex(ax)
                    self.pneum_filter.set_stage(s)
                    self.on_pneum_filter_write()
                    self._update_pneum_cell_text(ax, stage)
            else:
                self.on_pneum_filter_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _update_pneum_cell_text(self, axis: int, stage: int) -> None:
        key = (axis, stage)
        if key in self.pneum_filter_buttons:
            try:
                name = self.pneum_filter.ftype.currentText().split(None, 1)[-1]
                short = name[:5] if len(name) > 5 else name
            except Exception:
                short = ""
            self.pneum_filter_buttons[key].set_info(short)

    def on_pneum_read_all_filters(self) -> None:
        """Read all pneumatic filters (3 axes × 4 stages)."""
        def work() -> None:
            s = self._require_session()
            for ax in range(3):
                for st in range(4):
                    try:
                        fs = s.get_pneumatic_filter(ax, st)
                        self.pneum_filter_buttons[(ax, st)].set_info(fs.type_name[:5])
                    except Exception:
                        self.pneum_filter_buttons[(ax, st)].set_info("?")
            fs = s.get_pneumatic_filter(0, 0)
            self.pneum_filter.set_stage(fs)
            self.pneum_filter_panel.set_from_filter_editor(self.pneum_filter)
            self.log_msg("pneumatic filters all 12 read")
        self._run("Read all pneumatic filters", work)

    # ------------------------------------------------------------------
    # FF tab
    # ------------------------------------------------------------------

    def _build_ff_tab(self) -> None:
        """FF tab with sub-tabs: Filter, Config."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)
        self.main_tabs.addTab(tabs, "Feed Forward")

        filt_w = self._build_ff_filter_page()
        tabs.addTab(filt_w, "FF Tuning")

        cfg_w = self._build_ff_config_page()
        tabs.addTab(cfg_w, "FF Gains")

    def _build_ff_filter_page(self) -> QtWidgets.QWidget:
        """FF filter page — 7 sources × 8 stages grid."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # Status row (from SAMBA19xUI FFFilterPage)
        top = QtWidgets.QHBoxLayout()
        self.rocker_ff_active = RockerButton("On", "Off")
        self.rocker_ff_active.setChecked(True)
        self.rocker_ff_adapt = RockerButton("On", "Off")
        self.rocker_ff_adapt.setChecked(True)
        c1 = QtWidgets.QVBoxLayout()
        c1.addWidget(self.rocker_ff_active, 0, QtCore.Qt.AlignHCenter)
        c1.addWidget(QtWidgets.QLabel("FF active"))
        c2 = QtWidgets.QVBoxLayout()
        c2.addWidget(self.rocker_ff_adapt, 0, QtCore.Qt.AlignHCenter)
        c2.addWidget(QtWidgets.QLabel("Adaptive"))
        top.addLayout(c1)
        top.addLayout(c2)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Error signal:"))
        self.ff_err_fb = FlatPush("FB")
        self.ff_err_raw = FlatPush("Raw")
        top.addWidget(self.ff_err_fb)
        top.addWidget(self.ff_err_raw)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Threshold:"))
        self.ff_threshold = SciEdit("62")
        self.ff_threshold.setFixedWidth(50)
        self.ff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ff_thr_slider.setRange(0, 100)
        self.ff_thr_slider.setValue(62)
        self.ff_thr_slider.setFixedWidth(100)
        top.addWidget(self.ff_thr_slider)
        top.addWidget(QtWidgets.QLabel("Used gains:"))
        self.ff_used_gains = SciEdit("5")
        self.ff_used_gains.setFixedWidth(40)
        self.ff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ff_gains_slider.setRange(1, 16)
        self.ff_gains_slider.setValue(5)
        self.ff_gains_slider.setFixedWidth(80)
        top.addWidget(self.ff_gains_slider)
        top.addStretch(1)
        root.addLayout(top)

        # Source + Filter grid
        g_main = GroupPanel("")
        main = QtWidgets.QGridLayout(g_main)
        main.addWidget(QtWidgets.QLabel("Source:"), 0, 0)
        self.ff_source_name = SciEdit("InpXPOS")
        self.ff_source_name.setFixedWidth(100)
        main.addWidget(self.ff_source_name, 1, 0)
        self.ff_src = QtWidgets.QSpinBox()
        self.ff_src.setRange(0, 7)
        self.ff_src.setVisible(False)

        # Filter grid: 7 sources × 8 stages
        main.addWidget(QtWidgets.QLabel("Filter"), 0, 1)
        self.ff_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
        ff_stage_labels = ["VLoop", "Stretch", "1st", "VLoop", "", "VLoop", "VLoop", "Stretch"]
        for j, label in enumerate(ff_stage_labels):
            lbl = QtWidgets.QLabel(label)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
            main.addWidget(lbl, 0, j + 1)

        for src in range(7):
            lbl = QtWidgets.QLabel(f"Ch{src+1}")
            lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
            main.addWidget(lbl, src + 1, 0)
            for st in range(8):
                cell = FilterStageCell(st, f"S{st}", width=36, height=42)
                cell.clicked.connect(lambda s=st, a=src: self._on_ff_filter_cell_clicked(a, s))
                self.ff_filter_buttons[(src, st)] = cell
                main.addWidget(cell, src + 1, st + 1)

        main.addWidget(QtWidgets.QLabel("Error/Output Axis:"), 0, 9)
        self.ff_err_axis = SciEdit("Xtrans")
        self.ff_err_axis.setFixedWidth(90)
        main.addWidget(self.ff_err_axis, 1, 9)

        main.addWidget(QtWidgets.QLabel("Rate:"), 2, 0)
        self.ff_rate = SciEdit("0.00000e+000")
        main.addWidget(self.ff_rate, 2, 1)
        main.addWidget(QtWidgets.QLabel("Gains:"), 3, 0)
        gains_row = QtWidgets.QHBoxLayout()
        self.ff_gain_edits = []
        for _ in range(5):
            ed = SciEdit("0.000e+000")
            ed.setFixedWidth(80)
            self.ff_gain_edits.append(ed)
            gains_row.addWidget(ed)
        gains_row.addStretch(1)
        gw = QtWidgets.QWidget()
        gw.setLayout(gains_row)
        main.addWidget(gw, 3, 1, 1, 4)
        root.addWidget(g_main)

        self.ff_filter = FilterEditor([f"src {i}" for i in range(7)], max_stage=7)
        self.ff_filter.setVisible(False)
        self.ff_filter_panel = ClassicFilterPanel("FF filter (click a cell above)")
        self.ff_filter_panel.read_clicked.connect(self.on_ff_filter_read_classic)
        self.ff_filter_panel.write_clicked.connect(self.on_ff_filter_write_classic)
        self.ff_filter_panel.stage_changed.connect(self._sync_ff_panel_to_editor)
        root.addWidget(self.ff_filter_panel)

        self.ff_status = SciEdit()
        self.ff_status.setVisible(False)
        self.ff_inputs = SciEdit()
        self.ff_inputs.setVisible(False)
        self.ff_cfg = SciEdit()
        self.ff_cfg.setVisible(False)
        self.ff_params = SciEdit()
        self.ff_params.setVisible(False)
        self.ff_gains = SciEdit()
        self.ff_gains.setVisible(False)
        self.ff_mult = SciEdit()
        self.ff_mult.setVisible(False)
        self.ff_algo = QtWidgets.QSpinBox()
        self.ff_algo.setVisible(False)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read status", self.on_ff_status_read_classic),
            ("Read all filters", self.on_ff_read_all_filters),
            ("Read filter", self.on_ff_filter_read_classic),
            ("Write filter...", self.on_ff_filter_write_classic),
            ("Write gains...", self.on_ff_write_gains_classic),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        return w

    def _build_ff_config_page(self) -> QtWidgets.QWidget:
        """FF config page — inputs, multipliers, offsets, algorithm."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)

        g_sd = GroupPanel("Source definition")
        sd = QtWidgets.QFormLayout(g_sd)
        self.ff_src_num = QtWidgets.QComboBox()
        self.ff_src_num.addItems([f"Source{i}" for i in range(1, 9)])
        self.ff_src_sig = SciEdit("InpXPOS")
        sd.addRow("Source number:", self.ff_src_num)
        sd.addRow("Source signal:", self.ff_src_sig)
        root.addWidget(g_sd)

        mid = QtWidgets.QHBoxLayout()
        g_sm = GroupPanel("Signal Multipliers")
        sm = QtWidgets.QGridLayout(g_sm)
        self.ff_mult_edits = {}
        for r, (a, b) in enumerate((("XPos", "XAcc"), ("YPos", "YAcc"))):
            sm.addWidget(QtWidgets.QLabel(a + ":"), r, 0)
            ea = SciEdit("1.00000e+000")
            self.ff_mult_edits[a] = ea
            sm.addWidget(ea, r, 1)
            sm.addWidget(QtWidgets.QLabel(b + ":"), r, 2)
            eb = SciEdit("1.00000e+000")
            self.ff_mult_edits[b] = eb
            sm.addWidget(eb, r, 3)
        mid.addWidget(g_sm)

        g_off = GroupPanel("Offsets")
        of = QtWidgets.QFormLayout(g_off)
        self.ff_off_xpos = SciEdit("0.00000e+000")
        self.ff_off_ypos = SciEdit("0.00000e+000")
        of.addRow("XPos:", self.ff_off_xpos)
        of.addRow("YPos:", self.ff_off_ypos)
        mid.addWidget(g_off)

        g_mu = GroupPanel("Multipliers")
        mu = QtWidgets.QFormLayout(g_mu)
        self.ff_mul_xacc = SciEdit("0.00000e+000")
        self.ff_mul_yacc = SciEdit("0.00000e+000")
        mu.addRow("Xacc:", self.ff_mul_xacc)
        mu.addRow("Yacc:", self.ff_mul_yacc)
        mid.addWidget(g_mu)
        root.addLayout(mid)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read config", self.on_ff_status_read_classic),
            ("Write config...", self.on_ff_write_cfg),
            ("Write inputs...", self.on_ff_write_inputs),
            ("Write mult...", self.on_ff_write_mult),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # PFF tab
    # ------------------------------------------------------------------

    def _build_pff_tab(self) -> None:
        """PFF tab with sub-tabs: Filter, Config."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)
        self.main_tabs.addTab(tabs, "Pneum. SFF")

        filt_w = self._build_pff_filter_page()
        tabs.addTab(filt_w, "PFF Tuning")

        cfg_w = self._build_pff_config_page()
        tabs.addTab(cfg_w, "PFF Gains")

    def _build_pff_filter_page(self) -> QtWidgets.QWidget:
        """PFF filter page — 4 sources × 8 stages grid."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # Status row (from SAMBA19xUI PFFFilterPage)
        top = QtWidgets.QHBoxLayout()
        self.rocker_pff_active = RockerButton("On", "Off")
        self.rocker_pff_active.setChecked(True)
        self.rocker_pff_adapt = RockerButton("On", "Off")
        self.rocker_pff_adapt.setChecked(True)
        c1 = QtWidgets.QVBoxLayout()
        c1.addWidget(self.rocker_pff_active, 0, QtCore.Qt.AlignHCenter)
        c1.addWidget(QtWidgets.QLabel("PFF active"))
        c2 = QtWidgets.QVBoxLayout()
        c2.addWidget(self.rocker_pff_adapt, 0, QtCore.Qt.AlignHCenter)
        c2.addWidget(QtWidgets.QLabel("Adaptive"))
        top.addLayout(c1)
        top.addLayout(c2)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Threshold:"))
        self.pff_threshold = SciEdit("62")
        self.pff_threshold.setFixedWidth(50)
        self.pff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pff_thr_slider.setRange(0, 100)
        self.pff_thr_slider.setValue(62)
        self.pff_thr_slider.setFixedWidth(100)
        top.addWidget(self.pff_thr_slider)
        top.addWidget(QtWidgets.QLabel("Used gains:"))
        self.pff_used_gains = SciEdit("5")
        self.pff_used_gains.setFixedWidth(40)
        self.pff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pff_gains_slider.setRange(1, 16)
        self.pff_gains_slider.setValue(5)
        self.pff_gains_slider.setFixedWidth(80)
        top.addWidget(self.pff_gains_slider)
        top.addStretch(1)
        root.addLayout(top)

        # PFF filter grid: 4 sources × 8 stages
        g_main = GroupPanel("")
        main = QtWidgets.QGridLayout(g_main)
        main.addWidget(QtWidgets.QLabel("Source:"), 0, 0)
        self.pff_source_name = SciEdit("InpXPOS")
        self.pff_source_name.setFixedWidth(100)
        main.addWidget(self.pff_source_name, 1, 0)
        self.pff_source = QtWidgets.QSpinBox()
        self.pff_source.setRange(0, 7)
        self.pff_source.setVisible(False)

        main.addWidget(QtWidgets.QLabel("Filter"), 0, 1)
        self.pff_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
        for j in range(8):
            lbl = QtWidgets.QLabel(f"S{j}")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
            main.addWidget(lbl, 0, j + 1)

        for src in range(4):
            lbl = QtWidgets.QLabel(f"Ch{src+1}")
            lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
            main.addWidget(lbl, src + 1, 0)
            for st in range(8):
                cell = FilterStageCell(st, f"S{st}", width=36, height=42)
                cell.clicked.connect(lambda s=st, a=src: self._on_pff_filter_cell_clicked(a, s))
                self.pff_filter_buttons[(src, st)] = cell
                main.addWidget(cell, src + 1, st + 1)

        main.addWidget(QtWidgets.QLabel("Error/Output Axis:"), 0, 9)
        self.pff_err_axis = SciEdit("ZtPneu")
        self.pff_err_axis.setFixedWidth(90)
        main.addWidget(self.pff_err_axis, 1, 9)

        main.addWidget(QtWidgets.QLabel("Rate:"), 2, 0)
        self.pff_rate = SciEdit("0.00000e+000")
        main.addWidget(self.pff_rate, 2, 1)
        main.addWidget(QtWidgets.QLabel("Gains:"), 3, 0)
        gr = QtWidgets.QHBoxLayout()
        self.pff_gain_edits = []
        for _ in range(5):
            ed = SciEdit("0.000e+000")
            ed.setFixedWidth(80)
            self.pff_gain_edits.append(ed)
            gr.addWidget(ed)
        gr.addStretch(1)
        gw = QtWidgets.QWidget()
        gw.setLayout(gr)
        main.addWidget(gw, 3, 1, 1, 4)
        root.addWidget(g_main)

        self.pff_axis = QtWidgets.QSpinBox()
        self.pff_axis.setRange(0, 2)
        self.pff_axis.setVisible(False)
        self.pff_stage = QtWidgets.QSpinBox()
        self.pff_stage.setRange(0, 7)
        self.pff_stage.setVisible(False)
        self.pff_cfg = SciEdit()
        self.pff_cfg.setVisible(False)
        self.pff_params = SciEdit()
        self.pff_params.setVisible(False)
        self.pff_inputs = SciEdit()
        self.pff_inputs.setVisible(False)
        self.pff_gains = SciEdit()
        self.pff_gains.setVisible(False)
        self.pff_filter = FilterEditor(["axis via spin"], max_stage=7)
        self.pff_filter.setVisible(False)
        self.pff_filter.axis.setEnabled(False)
        self.pff_filter_panel = ClassicFilterPanel("PFF filter (click a cell above)")
        self.pff_filter_panel.read_clicked.connect(self.on_pff_filter_read_classic)
        self.pff_filter_panel.write_clicked.connect(self.on_pff_filter_write_classic)
        self.pff_filter_panel.stage_changed.connect(self._sync_pff_panel_to_editor)
        root.addWidget(self.pff_filter_panel)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all", self.on_pff_read_classic),
            ("Read all filters", self.on_pff_read_all_filters),
            ("Read filter", self.on_pff_filter_read_classic),
            ("Write filter...", self.on_pff_filter_write_classic),
            ("Write gains...", self.on_pff_write_gains_classic),
            ("Reset FIR...", self.on_pff_reset),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        return w

    def _on_pff_filter_cell_clicked(self, source: int, stage: int) -> None:
        """User clicked a PFF filter cell."""
        self.pff_source.setValue(source)
        self.pff_stage.setValue(stage)
        self.pff_filter.stage.setValue(stage)
        self.pff_filter_panel.set_stage_index(stage)

        if self.session and self.session.connected:
            self.on_pff_filter_read_classic()

        dlg = FilterDlg(
            [f"src {i}" for i in range(4)], max_stage=7,
            show_all_axes=True, show_all_sources=True, parent=self
        )
        dlg.setWindowTitle(f"PFF Filter — Source {source+1}, Stage {stage}")
        fs = self.pff_filter.to_stage()
        dlg.set_stage(fs)

        def on_dlg_changed(new_stage: object, _all_axes: bool, _all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.pff_filter.set_stage(new_stage)
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
            self._update_pff_cell_text(source, stage)
            self.pff_stage.setValue(stage)
            self.pff_filter.stage.setValue(stage)
            self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)
            self.on_pff_filter_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _update_pff_cell_text(self, source: int, stage: int) -> None:
        key = (source, stage)
        if key in self.pff_filter_buttons:
            try:
                name = self.pff_filter.ftype.currentText().split(None, 1)[-1]
                short = name[:5] if len(name) > 5 else name
            except Exception:
                short = ""
            self.pff_filter_buttons[key].set_info(short)

    def on_pff_read_all_filters(self) -> None:
        """Read all PFF filters (4 sources × 8 stages)."""
        def work() -> None:
            s = self._require_session()
            for src in range(4):
                for st in range(8):
                    try:
                        fs = s.get_pff_filter(0, src, st)
                        self.pff_filter_buttons[(src, st)].set_info(fs.type_name[:5])
                    except Exception:
                        self.pff_filter_buttons[(src, st)].set_info("?")
            fs = s.get_pff_filter(0, 0, 0)
            self.pff_filter.set_stage(fs)
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
            self.log_msg("PFF filters all 32 read")
        self._run("Read all PFF filters", work)

    def _build_pff_config_page(self) -> QtWidgets.QWidget:
        """PFF config page."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)

        g_sd = GroupPanel("Source definition")
        sd = QtWidgets.QFormLayout(g_sd)
        self.pff_src_num = QtWidgets.QComboBox()
        self.pff_src_num.addItems([f"Source{i}" for i in range(1, 5)])
        self.pff_src_sig = SciEdit("InpXPOS")
        sd.addRow("Source number:", self.pff_src_num)
        sd.addRow("Source signal:", self.pff_src_sig)
        root.addWidget(g_sd)

        g_off = GroupPanel("Offsets & Multipliers")
        form = QtWidgets.QFormLayout(g_off)
        self.pff_off_xpos = SciEdit("0.00000e+000")
        self.pff_off_ypos = SciEdit("0.00000e+000")
        self.pff_mul_xacc = SciEdit("0.00000e+000")
        self.pff_mul_yacc = SciEdit("0.00000e+000")
        form.addRow("XPos:", self.pff_off_xpos)
        form.addRow("YPos:", self.pff_off_ypos)
        form.addRow("Xacc:", self.pff_mul_xacc)
        form.addRow("Yacc:", self.pff_mul_yacc)
        root.addWidget(g_off)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Write config...", self.on_pff_write_cfg),
            ("Write params...", self.on_pff_write_params),
            ("Write inputs...", self.on_pff_write_inputs),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # SaveLoad tab
    # ------------------------------------------------------------------

    def _build_saveload_tab(self) -> None:
        """Save/Load configuration page — updated to support .SAMBA19x_Config XML."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(6, 4, 6, 4)

        g = GroupPanel("Configuration File (.SAMBA19x_Config)")
        form = QtWidgets.QFormLayout(g)

        self.setup_file_lbl = QtWidgets.QLabel("No file selected")
        self.setup_file_lbl.setWordWrap(True)
        form.addRow("Setup file:", self.setup_file_lbl)

        self.si_file_lbl = QtWidgets.QLabel("No file selected")
        form.addRow("SI file:", self.si_file_lbl)

        self.nvram_cs_fw = QtWidgets.QLabel("-")
        self.nvram_cs_mon = QtWidgets.QLabel("-")
        self.nvram_cs_cfg = QtWidgets.QLabel("-")
        form.addRow("Firmware checksum:", self.nvram_cs_fw)
        form.addRow("Monitor checksum:", self.nvram_cs_mon)
        form.addRow("Config checksum:", self.nvram_cs_cfg)

        self.nvram_fs = SciSpin()
        self.nvram_fs.setRange(0, 10000)
        self.nvram_fs.setValue(1836)
        self.nvram_cfg = SciEdit()
        self.nvram_adcs = QtWidgets.QSpinBox()
        self.nvram_adcs.setRange(0, 32)
        form.addRow("Sample freq:", self.nvram_fs)
        form.addRow("Config:", self.nvram_cfg)
        form.addRow("ADC set num:", self.nvram_adcs)

        # hidden fields
        self.nvram_fs.setVisible(False)
        self.nvram_cfg.setVisible(False)
        self.nvram_adcs.setVisible(False)

        root.addWidget(g)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Load setup file...", self.on_setup_load_file),
            ("Apply file to controller...", self.on_setup_apply_file),
            ("Save setup file...", self.on_setup_save_file),
            ("Read checksums", self.on_nvram_checksums),
            ("Save to NVRAM...", lambda: self.on_nvram("save")),
            ("Restore from NVRAM...", lambda: self.on_nvram("restore")),
            ("Clear NVRAM...", lambda: self.on_nvram("clear")),
            ("Select SI file...", self.on_si_select),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        self.main_tabs.addTab(w, "Save/Load")

    def _build_logging_tab(self) -> None:
        """Expose the implemented event/analysis logging page."""
        self.main_tabs.addTab(self._page_logging(), "Logging")

    # ------------------------------------------------------------------
    # Special tab
    # ------------------------------------------------------------------

    def _build_special_tab(self) -> None:
        """Special tab — safety, ZMS, polynom (from SAMBA19xUI)."""
        tabs = SamTabWidget()
        tabs.currentChanged.connect(self._on_sub_tab_changed)

        # Safety / Earthquake monitoring
        w = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(w)
        wl.setContentsMargins(6, 4, 6, 4)

        g_safety = GroupPanel("Safety & Earthquake Monitoring")
        sf = QtWidgets.QFormLayout(g_safety)
        self.safety_earthquake = RockerButton("On", "Off")
        self.safety_temp_sensors = RockerButton("On", "Off")
        self.safety_horiz_gain = SciEdit("1.00000e+000")
        self.safety_vert_gain = SciEdit("1.00000e+000")
        self.safety_ref_temp = SciEdit("2.50000e+001")
        self.safety_status = QtWidgets.QLabel("—")
        sf.addRow("Earthquake monitoring:", self.safety_earthquake)
        sf.addRow("Use temp sensors:", self.safety_temp_sensors)
        sf.addRow("Horizontal gain:", self.safety_horiz_gain)
        sf.addRow("Vertical gain:", self.safety_vert_gain)
        sf.addRow("Reference temp:", self.safety_ref_temp)
        sf.addRow("Status:", self.safety_status)
        wl.addWidget(g_safety)

        g_amplifier = GroupPanel("Amplifier Events")
        amp_grid = QtWidgets.QGridLayout(g_amplifier)
        self.safety_amp_leds: list[LedIndicator] = []
        for i in range(12):
            led = LedIndicator(10)
            self.safety_amp_leds.append(led)
            amp_grid.addWidget(led, i // 4, i % 4)
            amp_grid.addWidget(QtWidgets.QLabel(f"Motor {i+1}"), i // 4 + 1, i % 4)
        wl.addWidget(g_amplifier)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read status")
        btn_r.clicked.connect(self.on_safety_read)
        act.addWidget(btn_r)
        act.addStretch(1)
        wl.addLayout(act)
        wl.addStretch(1)
        tabs.addTab(w, "Safety")

        # ZMS (Zeiss Merity Safety)
        zms = QtWidgets.QWidget()
        zl = QtWidgets.QVBoxLayout(zms)
        zl.setContentsMargins(6, 4, 6, 4)

        g_zms = GroupPanel("ZMS Stability Monitoring")
        zf = QtWidgets.QFormLayout(g_zms)
        self.zms_vibration = QtWidgets.QLabel("—")
        self.zms_position = QtWidgets.QLabel("—")
        self.zms_rms = QtWidgets.QLabel("—")
        self.zms_axis = QtWidgets.QLabel("—")
        self.zms_thresholds: list[SciEdit] = []
        for i in range(6):
            ed = SciEdit("1.00000e+000")
            self.zms_thresholds.append(ed)
        zf.addRow("Vibration status:", self.zms_vibration)
        zf.addRow("Position status:", self.zms_position)
        zf.addRow("Last failed RMS:", self.zms_rms)
        zf.addRow("Last failed axis:", self.zms_axis)
        for i in range(6):
            zf.addRow(f"Threshold axis {i+1}:", self.zms_thresholds[i])
        zl.addWidget(g_zms)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read status")
        btn_r.clicked.connect(self.on_zms_read)
        act.addWidget(btn_r)
        btn_w = FlatPush("Write thresholds...")
        btn_w.clicked.connect(self.on_zms_write)
        act.addWidget(btn_w)
        act.addStretch(1)
        zl.addLayout(act)
        zl.addStretch(1)
        tabs.addTab(zms, "ZMS")

        # Polynom
        poly = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(poly)
        pl.setContentsMargins(6, 4, 6, 4)

        g_poly = GroupPanel("Polynom Configuration")
        pf = QtWidgets.QFormLayout(g_poly)
        self.poly_num = QtWidgets.QComboBox()
        self.poly_num.addItems([f"Polynom {i+1}" for i in range(19)])
        self.poly_type = QtWidgets.QComboBox()
        self.poly_type.addItems(["Input", "Output"])
        self.poly_coeffs: list[SciEdit] = []
        for i in range(5):
            ed = SciEdit("0.00000e+000")
            self.poly_coeffs.append(ed)
        pf.addRow("Polynom:", self.poly_num)
        pf.addRow("Type:", self.poly_type)
        for i in range(5):
            pf.addRow(f"Coeff {i+1}:", self.poly_coeffs[i])
        pl.addWidget(g_poly)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write...")
        btn_r.clicked.connect(self.on_poly_read)
        btn_w.clicked.connect(self.on_poly_write)
        act.addWidget(btn_r)
        act.addWidget(btn_w)
        act.addStretch(1)
        pl.addLayout(act)
        pl.addStretch(1)
        tabs.addTab(poly, "Polynom")

        self.main_tabs.addTab(tabs, "Special")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _unit_row(self, widget: QtWidgets.QWidget, unit: str) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(widget, 1)
        lay.addWidget(QtWidgets.QLabel(unit))
        return box

    def log_msg(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _require_session(self) -> ControllerSession:
        if not self.session or not self.session.connected:
            raise RuntimeError("Not connected")
        return self.session

    def _confirm_write(self, summary: str) -> bool:
        return True

    def _confirm_destructive(self, summary: str) -> bool:
        """Confirm destructive actions while ordinary parameter edits stay immediate."""
        if not self._confirm_write(summary):
            return False
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Confirm action")
        box.setText("Apply this destructive action to the controller?")
        box.setInformativeText(summary + "\n\nA local snapshot will be saved first.")
        box.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        return box.exec() == QtWidgets.QMessageBox.Ok

    def _run(self, title: str, fn) -> None:
        started = time.perf_counter()
        try:
            fn()
        except Exception as exc:
            self.log_msg(f"ERROR {title}: {exc}")
            if isinstance(exc, TransportError) and self.session and not self.session.connected:
                self.on_disconnect()
            QtWidgets.QMessageBox.critical(self, title, str(exc))
        else:
            self.log_msg(f"{title}: {time.perf_counter() - started:.3f} s")

    def _set_writable(self, enabled: bool) -> None:
        if self.session:
            self.session.readonly = not enabled

    def _on_main_tab_changed(self, idx: int) -> None:
        """When main tab changes, update page label and auto-refresh current page."""
        tab = self.main_tabs.widget(idx)
        if tab:
            label = self.main_tabs.tabText(idx)
            self._current_page = label
            self.loop_states.page_lbl.setText(f"Current page  ·  {label}")
            if hasattr(self, "page_title_lbl"):
                self.page_title_lbl.setText(label)
            if hasattr(self, "main_navigation"):
                self.main_navigation.setCurrentIndex(idx)
        # Auto-refresh the current page when switching to it (like SAMBA19xUI)
        if self.session and self.session.connected:
            self._refresh_current_page()

    @staticmethod
    def _refresh_dynamic_style(widget: QtWidgets.QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _set_connection_display(self, connected: bool, detail: str = "") -> None:
        self.header_status_lbl.setText(
            f"●  {'ONLINE' if connected else 'OFFLINE'}"
        )
        self.header_status_lbl.setProperty("connected", connected)
        self.status_lbl.setProperty("connected", connected)
        self.loop_states.conn_lbl.setProperty("connected", connected)
        for widget in (
            self.header_status_lbl,
            self.status_lbl,
            self.loop_states.conn_lbl,
        ):
            self._refresh_dynamic_style(widget)
        if connected and detail:
            self.header_status_lbl.setToolTip(detail)
        else:
            self.header_status_lbl.setToolTip("Controller is not connected")

        for name in (
            "_conn_page_connect_btn",
            "_conn_page_disconnect_btn",
            "_refresh_ports_btn",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(
                    (not connected) if name != "_conn_page_disconnect_btn" else connected
                )

        protection_button = getattr(self, "protection_led", None)
        if protection_button is not None:
            protection_button.setEnabled(connected)
        sync_nvram_protection = getattr(
            self, "_sync_nvram_protection_controls", None
        )
        if callable(sync_nvram_protection):
            sync_nvram_protection(connected=connected)

    def _on_sub_tab_changed(self, idx: int) -> None:
        """When a sub-tab changes, auto-refresh to show current data."""
        if self.session and self.session.connected:
            self._refresh_current_page()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def on_connect(self) -> None:
        def work() -> None:
            try:
                if self.session:
                    self.session.close()
                backend = self.backend.currentText()
                if backend == "mock":
                    self.session = open_mock(readonly=False)
                else:
                    self.session = open_serial(
                        self.port.text().strip(),
                        int(self.baud.currentData()),
                        readonly=False,
                    )
                version = self.session.open()
                self._last_firmware_version = version
                self._switch_config = 0
                self._switch_config_loaded = False
                self.gate = SafetyGate(self.session)
                self.btn_connect.setEnabled(False)
                self.btn_disconnect.setEnabled(True)
                self.status_lbl.setText(f"Connected — {version}")
                endpoint = (
                    "Mock controller"
                    if backend == "mock"
                    else f"{self.session.info.port or self.port.text()} @ "
                         f"{self.session.info.baudrate or self.baud.currentData()}"
                )
                self.loop_states.conn_lbl.setText(f"Connected  ·  {endpoint}")
                self._set_connection_display(True, f"{endpoint} — {version}")
                self.conn_info.setText(f"backend={backend}  fw={version}")
                self.fw_version.setText(f"Firmware Version: {version}")
                self.log_msg(f"connected backend={backend} fw={version}")
                self._ensure_controller_capabilities()
                if self._auto_refresh:
                    self._refresh_timer.start()
                    self._on_timer_tick()
                self._refresh_current_page(force=True)
                self.log_msg(
                    "Connection ready; selected page reads its parameters on demand "
                    "(current page loaded)."
                )
            except Exception:
                # Keep the UI and serial ownership consistent when opening or
                # the initial BGVIS query fails part-way through.
                self.on_disconnect()
                raise

        self._run("Connect", work)

    def on_disconnect(self) -> None:
        self._refresh_timer.stop()
        if self.session:
            self.session.close()
        self.session = None
        self.gate = None
        self._last_firmware_version = None
        self._controller_capabilities_loaded = False
        self._system_constants = ()
        self._controller_features = None
        self._proximity_count = 6
        self._input_signal_count = len(IOSignalButton.INPUT_NAMES)
        self._last_proximity_offsets = []
        self._switch_config = 0
        self._switch_config_loaded = False
        self._live_refresh_errors.clear()
        self._apply_controller_capabilities()
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.status_lbl.setText("Disconnected")
        self.loop_states.conn_lbl.setText("Not Connected")
        self._set_connection_display(False)
        self.conn_info.setText("Not connected")
        self.fw_version.setText("Firmware Version: —")
        self.log_msg("disconnected")

    def on_refresh(self) -> None:
        def work() -> None:
            s = self._require_session()
            fw = self._last_firmware_version
            if fw is None:
                fw = s.get_version()
                self._last_firmware_version = fw
            loop = s.get_loop_status()
            self.lbl_fw.setText(str(fw))
            self.lbl_loop.setText(str(loop))
            self.lbl_fw.setText(str(fw))
            try:
                fs = s.get_sample_frequency()
                self.lbl_fs.setText(f"{fs:g} Hz")
                self.fs_sample.setText(f"{fs:g}")
            except Exception as exc:
                self.lbl_fs.setText(f"n/a ({exc})")
            try:
                geo = s.get_geophone_inputs()
                self.lbl_geo.setText(", ".join(str(x) for x in geo))
            except Exception as exc:
                self.lbl_geo.setText(f"n/a ({exc})")
            try:
                opl = s.get_output_limit()
                self.lbl_opl.setText(str(opl))
                self.loop_opl.setValue(int(opl))
            except Exception as exc:
                self.lbl_opl.setText(f"n/a ({exc})")
            try:
                sw = s.get_switch_status()
                self.lbl_switch.setText(" ".join(sw))
            except Exception as exc:
                self.lbl_switch.setText(f"n/a ({exc})")
            self._refresh_status_loop_state(loop)
            self.log_msg(f"refresh ok loop={loop}")

        self._run("Refresh", work)

    # ------------------------------------------------------------------
    # Controller actions
    # ------------------------------------------------------------------

    def on_controller_read_all(self) -> None:
        def work() -> None:
            self.on_loop_read()
            try:
                self.on_perf_read()
            except Exception:
                pass
            try:
                self.on_switch_read()
            except Exception:
                pass
        self._run("Controller read all", work)

    def on_loop_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            loop = s.get_loop_status()
            self.loop_ind.setText(f"{loop.individual:X}")
            self.loop_sys.setText(f"{loop.system:X}")
            self.loop_opl.setValue(s.get_output_limit())
            self.log_msg(f"BGSTS {loop}")
        self._run("Read loop", work)

    def on_loop_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            ind = int(self.loop_ind.text().strip(), 16)
            sysv = int(self.loop_sys.text().strip(), 16)
            if not self._confirm_write(f"BSSTS individual=0x{ind:X} system=0x{sysv:X}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_loop_status(ind, sysv)
            self._set_writable(True)
            self.log_msg("BSSTS applied")
        self._run("Write loop", work)

    def on_loop_limits_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            opl = int(self.loop_opl.value())
            if not self._confirm_write(f"BSOPL={opl}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_output_limit(opl)
            self._set_writable(True)
            self.log_msg("loop limits written")
        self._run("Write limits", work)

    # ------------------------------------------------------------------
    # Velocity filter actions
    # ------------------------------------------------------------------

    @property
    def vel_stage_bar(self) -> FilterStageBar:
        """Compatibility: first row of buttons treated as stage bar."""
        # Create a synthetic bar from the grid
        if not hasattr(self, '_vel_stage_bar_compat'):
            from python_samba.ui.classic_widgets import FilterStageBar
            self._vel_stage_bar_compat = FilterStageBar(7, ["Fil1", "Fil2", "Fil3", "Fil4", "Fil5", "Fil6", "Fil7"])
        return self._vel_stage_bar_compat

    def _on_vel_filter_cell_clicked(self, axis: int, stage: int) -> None:
        """User clicked a cell in the filter matrix grid."""
        self.vel_filter.axis.setCurrentIndex(axis)
        self.vel_filter.stage.setValue(stage)
        self.vel_filter_panel.set_stage_index(stage)

        # Read from controller if connected
        if self.session and self.session.connected:
            self.on_vel_read_classic()

        # Open FilterDlg
        dlg = FilterDlg(VEL_AXIS_LABELS, max_stage=6, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Velocity Filter — Axis {VEL_AXES_NAMES[axis]}, Stage {stage}")
        fs = self.vel_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setCurrentIndex(axis)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, _all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.vel_filter.set_stage(new_stage)
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self._update_vel_cell_text(axis, stage)
            if all_axes:
                for ax in range(6):
                    s = FilterStage(ax, new_stage.stage, new_stage.filter_type, new_stage.params)
                    self.vel_filter.axis.setCurrentIndex(ax)
                    self.vel_filter.set_stage(s)
                    self.on_vel_write()
                    self._update_vel_cell_text(ax, stage)
            else:
                self.on_vel_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _update_vel_cell_text(self, axis: int, stage: int) -> None:
        """Update the cell button text to show the filter name."""
        key = (axis, stage)
        if key in self.vel_filter_buttons:
            try:
                name = self.vel_filter.ftype.currentText().split(None, 1)[-1]
                short = filter_small_name(int(self.vel_filter.ftype.currentData()))
            except Exception:
                short = ""
                name = ""
            self.vel_filter_buttons[key].set_info(name, short=short)

    def on_vel_read_all_filters(self) -> None:
        """Read all 42 filters (6 axes × 7 stages) and update cell texts."""
        def work() -> None:
            s = self._require_session()
            try:
                limiters = s.get_vel_axes_output_limiter()
                for editor, value in zip(self.vel_axis_limiters, limiters):
                    editor.setText(f"{float(value):g}")
            except Exception as exc:
                self.log_msg(f"Velocity axis limiter read: {exc}")
            first_stage = None
            for ax in range(6):
                for st in range(7):
                    try:
                        fs = s.get_velocity_filter(ax, st)
                        if ax == 0 and st == 0:
                            first_stage = fs
                        self.vel_filter_buttons[(ax, st)].set_info(
                            fs.type_name, short=filter_small_name(fs.filter_type)
                        )
                    except Exception:
                        self.vel_filter_buttons[(ax, st)].set_info("?")
            # Load the first cell into the panel
            if first_stage is not None:
                self.vel_filter.set_stage(first_stage)
                self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self.log_msg("velocity filters all 42 read")
        self._run("Read all velocity filters", work)

    def on_vel_limiter_write(self, _axis: int | None = None) -> None:
        if not self.session or not self.session.connected:
            return

        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            values = [float(editor.text()) for editor in self.vel_axis_limiters]
            if not self._confirm_write(f"BSFBL velocity axis limiters={values}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_vel_axes_output_limiter(values)
            finally:
                self._set_writable(True)
            self.log_msg("velocity axis limiters written")

        self._run("Write velocity axis limiters", work)

    def _on_excitation_filter_clicked(self, stage: int) -> None:
        if not self.session or not self.session.connected:
            self.log_msg("Excitation filter read skipped: controller is not connected")
            return
        try:
            current = self.session.get_noise_filter_stage(stage)
        except Exception as exc:
            self.log_msg(f"ERROR Read excitation filter: {exc}")
            return

        dlg = FilterDlg(["Excitation"], max_stage=3, parent=self)
        dlg.setWindowTitle(f"Excitation Filter — Stage {stage + 1}")
        dlg.set_stage(current)
        dlg.axis_cbx.setEnabled(False)
        dlg.stage_spin.setEnabled(False)

        def apply_filter(
            new_stage: object, _all_axes: bool, _all_sources: bool
        ) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            fixed = FilterStage(
                0, stage, new_stage.filter_type, new_stage.params
            )
            self._write_excitation_filter(fixed)

        dlg.filterChanged.connect(apply_filter)
        dlg.exec()
        dlg.deleteLater()

    def _write_excitation_filter(self, stage: FilterStage) -> None:
        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            if not self._confirm_write(
                f"DSNFS stage={stage.stage} type={stage.filter_type}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_noise_filter_stage(stage)
            finally:
                self._set_writable(True)
            self.exc_filter_buttons[stage.stage].set_info(
                stage.type_name, short=filter_small_name(stage.filter_type)
            )
            self.log_msg(f"excitation filter stage {stage.stage + 1} written")

        self._run("Write excitation filter", work)

    def _on_vel_axis_changed(self, idx: int) -> None:
        self.vel_filter.axis.setCurrentIndex(idx)
        if self.session and self.session.connected:
            self.on_vel_read_classic()

    def _on_vel_help_selection_changed(self, axis: int | None = None) -> None:
        """Apply the legacy THH axis/stage selection to diagnostic selectors."""
        if axis is not None:
            self._vel_help_axis = max(0, min(5, int(axis)))
        selected_axis = int(getattr(self, "_vel_help_axis", 0))
        for index, button in enumerate(getattr(self, "vel_help_axis_buttons", [])):
            button.set_on(index == selected_axis)
        combo = getattr(self, "vel_measure_stage", None)
        if combo is None:
            return
        # Old VelocityTHH stores SelectedIndex - 1: Raw=-1,
        # Stage1..7=0..6, Output=7.
        measured_subindex = combo.currentIndex() - 1
        if hasattr(self, "diag_0"):
            self.diag_0.set_io_signal((3, selected_axis, 0))
        if hasattr(self, "diag_1"):
            self.diag_1.set_io_signal((2, selected_axis, measured_subindex))
        if hasattr(self, "noise_inject"):
            self.noise_inject.set_io_signal((4, selected_axis, 0))

    def _on_vel_stage_selected(self, stage: int) -> None:
        self.vel_filter.stage.setValue(stage)
        self.vel_filter_panel.set_stage_index(stage)
        if self.session and self.session.connected:
            self.on_vel_read_classic()
        # Open FilterDlg
        dlg = FilterDlg(VEL_AXIS_LABELS, max_stage=6, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Velocity Filter — Stage {stage}")
        fs = self.vel_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, _all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.vel_filter.set_stage(new_stage)
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self._update_vel_stage_caption_from_editor()
            if all_axes:
                for ax in range(6):
                    s = FilterStage(ax, new_stage.stage, new_stage.filter_type, new_stage.params)
                    self.vel_filter.axis.setCurrentIndex(ax)
                    self.vel_filter.set_stage(s)
                    self.on_vel_write()
            else:
                self.on_vel_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _sync_vel_panel_to_editor(self) -> None:
        self.vel_filter_panel.apply_to_filter_editor(self.vel_filter)
        self._update_vel_stage_caption_from_editor()

    def _update_vel_stage_caption_from_editor(self) -> None:
        stage = self.vel_filter.stage_index()
        try:
            name = self.vel_filter.ftype.currentText().split(None, 1)[-1]
        except Exception:
            name = ""
        # Update all axis cells for this stage
        for ax in range(6):
            self._update_vel_cell_text(ax, stage)

    def on_vel_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.vel_filter.axis_index()
            stage_i = self.vel_filter.stage_index()
            if stage_i < 0:
                stage_i = 0
            fs = s.get_velocity_filter(axis, stage_i)
            self.vel_filter.set_stage(fs)
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self._update_vel_cell_text(axis, stage_i)
            self.log_msg(f"VGVFS axis={axis} stage={stage_i} type={fs.type_name}")
        self._run("Read velocity filter", work)

    def on_vel_write_classic(self) -> None:
        stage_i = self.vel_filter.stage_index()
        if stage_i < 0:
            stage_i = 0
        self.vel_filter_panel.apply_to_filter_editor(self.vel_filter)
        self.on_vel_write()
        self._update_vel_stage_caption_from_editor()

    def on_vel_read_all_stages(self) -> None:
        # Redirect to new grid-based read
        self.on_vel_read_all_filters()

    def on_vel_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.vel_filter.axis_index()
            stage_i = self.vel_filter.stage_index()
            fs = s.get_velocity_filter(axis, stage_i)
            self.vel_filter.set_stage(fs)
            self.log_msg(f"VGVFS axis={axis} stage={stage_i} type={fs.type_name}")
        self._run("Read velocity filter", work)

    def on_vel_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.vel_filter.to_stage()
            before = s.get_velocity_filter(stage.axis, stage.stage)
            summary = (
                f"VSVFS axis={stage.axis} stage={stage.stage}\n"
                f"type {before.filter_type} ({before.type_name}) -> {stage.filter_type}\n"
                f"params {list(before.params)} -> {list(stage.params)}"
            )
            if not self._confirm_write(summary):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_velocity_filter(stage)
            self._set_writable(True)
            self.log_msg("VSVFS applied")
        self._run("Write velocity filter", work)

    # ------------------------------------------------------------------
    # Velocity matrix actions
    # ------------------------------------------------------------------

    def on_vel_mat_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = 0  # default axis for matrix reading
            sens = s.get_velocity_sensor_matrix(axis)
            motor = s.get_velocity_motor_matrix(axis)
            self.vel_sens.set_values(sens)
            self.vel_motor.set_values(motor)
            for ed, v in zip(self.vel_sens_edits, sens):
                ed.setText(f"{float(v):.5e}")
            for ed, v in zip(self.vel_motor_edits, motor):
                ed.setText(f"{float(v):.5e}")
            self.log_msg(f"velocity matrices axis={axis} read")
        self._run("Read velocity matrices", work)

    def on_vel_mat_write_classic(self) -> None:
        sens = [float(ed.text()) for ed in self.vel_sens_edits]
        motor = [float(ed.text()) for ed in self.vel_motor_edits]
        self.vel_sens.set_values(sens)
        self.vel_motor.set_values(motor)
        self.on_vel_mat_write()

    def on_vel_mat_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = 0
            self.vel_sens.set_values(s.get_velocity_sensor_matrix(axis))
            self.vel_motor.set_values(s.get_velocity_motor_matrix(axis))
            self.log_msg(f"velocity matrices axis={axis} read")
        self._run("Read velocity matrices", work)

    def on_vel_mat_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            axis = 0
            if not self._confirm_write(f"Write velocity matrices axis {axis}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_velocity_sensor_matrix(axis, self.vel_sens.values())
            s.set_velocity_motor_matrix(axis, self.vel_motor.values())
            self._set_writable(True)
            self.log_msg("velocity matrices written")
        self._run("Write velocity matrices", work)

    # ------------------------------------------------------------------
    # Position filter actions
    # ------------------------------------------------------------------

    def _on_pos_axis_changed(self, idx: int) -> None:
        self.pos_filter.axis.setCurrentIndex(idx)
        if self.session and self.session.connected:
            self.on_pos_read_classic()

    def _on_pos_stage_selected(self, stage: int) -> None:
        # Legacy handler — redirect to grid cell click for axis 0
        self._on_pos_filter_cell_clicked(0, stage)

    def _sync_pos_panel_to_editor(self) -> None:
        self.pos_filter_panel.apply_to_filter_editor(self.pos_filter)

    def _update_pos_stage_caption_from_editor(self) -> None:
        stage = self.pos_filter.stage_index()
        try:
            name = self.pos_filter.ftype.currentText().split(None, 1)[-1]
        except Exception:
            name = ""
        # update grid cell
        for ax in range(6):
            self._update_pos_cell_text(ax, stage)

    def on_pos_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.pos_filter.axis_index()
            stage_i = self.pos_filter.stage_index()
            if stage_i < 0:
                stage_i = 0
            fs = s.get_proximity_filter(axis, stage_i)
            self.pos_filter.set_stage(fs)
            self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self._update_pos_cell_text(axis, stage_i)
            self.log_msg(f"CGPFS axis={axis} stage={stage_i} type={fs.type_name}")
        self._run("Read proximity filter", work)

    def on_pos_write_classic(self) -> None:
        self.pos_filter_panel.apply_to_filter_editor(self.pos_filter)
        self.on_pos_write()
        self._update_pos_stage_caption_from_editor()

    def on_pos_read_all_stages(self) -> None:
        # Redirect to new grid-based read
        self.on_pos_read_all_filters()

    def on_pos_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.pos_filter.axis_index()
            stage_i = self.pos_filter.stage_index()
            fs = s.get_proximity_filter(axis, stage_i)
            self.pos_filter.set_stage(fs)
            self.log_msg(f"CGPFS axis={axis} stage={stage_i} type={fs.type_name}")
        self._run("Read proximity filter", work)

    def on_pos_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.pos_filter.to_stage()
            before = s.get_proximity_filter(stage.axis, stage.stage)
            summary = (
                f"CSPFS axis={stage.axis} stage={stage.stage}\n"
                f"type {before.filter_type} -> {stage.filter_type}\n"
                f"params {list(before.params)} -> {list(stage.params)}"
            )
            if not self._confirm_write(summary):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_proximity_filter(stage)
            self._set_writable(True)
            self.log_msg("CSPFS applied")
        self._run("Write proximity filter", work)

    # ------------------------------------------------------------------
    # Proximity offsets
    # ------------------------------------------------------------------

    @staticmethod
    def _proximity_raw_to_display(values) -> list[float | None]:
        display: list[float | None] = [None] * len(PROX_DISPLAY_NAMES)
        for raw_index, value in enumerate(list(values)[:8]):
            display[PROX_RAW_TO_DISPLAY[raw_index]] = float(value)
        return display

    def _proximity_si_factors_display(self) -> list[float]:
        """Return proximity conversion factors in the card display order."""
        factors: list[float] = []
        editors = getattr(self, "proxy_si_unit_edits", {})
        for name in PROX_DISPLAY_NAMES:
            editor = editors.get(name)
            try:
                factor = float(editor.text()) if editor is not None else 1.0
            except (TypeError, ValueError):
                factor = 1.0
            # The legacy Divide converter substitutes one for a zero divisor.
            if not math.isfinite(factor) or factor == 0.0:
                factor = 1.0
            factors.append(factor)
        return factors

    def _update_proxy_readouts_from_raw(self, raw_values) -> None:
        display_values = self._proximity_raw_to_display(raw_values)
        factors = self._proximity_si_factors_display()
        for index, name in enumerate(PROX_DISPLAY_NAMES):
            label = getattr(self, "proxy_value_labels", {}).get(name)
            value = display_values[index]
            if label is not None and value is not None:
                # The WPF binding used StringFormat=####0.# for micrometres.
                label.setText(format_ui_number(round(value / factors[index], 1)))

    def _on_proximity_si_unit_changed(self) -> None:
        """Recalculate cached card values; SI factors are UI-only, not RCI data."""
        for editor in getattr(self, "proxy_si_unit_edits", {}).values():
            try:
                value = float(editor.text())
            except ValueError:
                value = 1.0
            if not math.isfinite(value):
                value = 1.0
            editor.setText(format_ui_number(value))
        values = getattr(self, "_last_proximity_values", [])
        if values:
            self._update_proxy_readouts_from_raw(values)

    def _configure_proximity_widgets(self, count: int) -> None:
        available = set(PROX_RAW_TO_DISPLAY[: max(0, min(8, int(count)))])
        editors = getattr(self, "prox_edits", [])
        labels = getattr(self, "prox_labels", [])
        if len(editors) >= 8:
            for index, editor in enumerate(editors[:8]):
                enabled = index in available
                editor.setReadOnly(not enabled)
                editor.setVisible(enabled)
                if index < len(labels):
                    labels[index].setVisible(enabled)
        for index, name in enumerate(PROX_DISPLAY_NAMES):
            card = getattr(self, "proxy_cards", {}).get(name)
            if card is not None:
                card.setVisible(index in available)
        tree = getattr(self, "prox_status_tree", None)
        if tree is not None:
            for display_index in range(8):
                tree.setColumnHidden(display_index + 1, display_index not in available)

    def _update_proximity_offset_widgets(self, values, count: int) -> None:
        raw_values = [float(value) for value in list(values)[:count]]
        self._last_proximity_offsets = raw_values
        if hasattr(self, "prox_off"):
            self.prox_off.set_values(raw_values)
        editors = getattr(self, "prox_edits", [])
        if len(editors) >= 8:
            display = self._proximity_raw_to_display(raw_values)
            for index, editor in enumerate(editors[:8]):
                if display[index] is not None:
                    editor.setText(format_ui_number(display[index]))
        else:
            for editor, value in zip(editors, raw_values):
                editor.setText(format_ui_number(value))
        self._configure_proximity_widgets(count)

    def _proximity_editor_values_raw(self, count: int) -> list[float]:
        editors = getattr(self, "prox_edits", [])
        if len(editors) >= 8:
            display_values = [float(editor.text()) for editor in editors[:8]]
            return [
                display_values[PROX_RAW_TO_DISPLAY[raw_index]]
                for raw_index in range(count)
            ]
        return [float(editor.text()) for editor in editors[:count]]

    def _refresh_position_live_state(self) -> None:
        """Refresh the visible proxy values with one lightweight GET."""
        self._ensure_controller_capabilities()
        count = self._proximity_count
        self._configure_proximity_widgets(count)
        session = self._require_session()
        values = session.get_proximity_input_values(count)
        raw_values = [float(value) for value in values[:count]]
        self._last_proximity_values = raw_values
        display_values = self._proximity_raw_to_display(raw_values)
        display_offsets = self._proximity_raw_to_display(
            self._last_proximity_offsets[:count]
        )

        self._update_proxy_readouts_from_raw(raw_values)

        tree = getattr(self, "prox_status_tree", None)
        if tree is not None and tree.topLevelItemCount() >= 2:
            value_row = tree.topLevelItem(0)
            error_row = tree.topLevelItem(1)
            for index, value in enumerate(display_values):
                if value is None:
                    continue
                value_row.setText(index + 1, format_ui_number(value))
                offset = display_offsets[index]
                error_row.setText(
                    index + 1,
                    format_ui_number(value - offset) if offset is not None else "—",
                )

    def on_prox_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            self._ensure_controller_capabilities()
            count = self._proximity_count
            vals = s.get_proximity_offsets(count)
            self._update_proximity_offset_widgets(vals, count)
            self.log_msg("proximity offsets read")
        self._run("Read proximity", work)

    def on_prox_write_classic(self) -> None:
        self._ensure_controller_capabilities()
        vals = self._proximity_editor_values_raw(self._proximity_count)
        self._pending_proximity_offsets = vals
        self.prox_off.set_values(vals)
        self.on_prox_write()

    def on_prox_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self._ensure_controller_capabilities()
            count = self._proximity_count
            values = s.get_proximity_offsets(count)
            self._update_proximity_offset_widgets(values, count)
            self.log_msg("CGPOX read" if count == 8 else "CGPOV read")
        self._run("Read proximity offsets", work)

    def on_prox_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            self._ensure_controller_capabilities()
            pending = getattr(self, "_pending_proximity_offsets", None)
            vals = list(
                pending
                if pending
                else self.prox_off.values()[:self._proximity_count]
            )
            command = "CSPOX" if len(vals) == 8 else "CSPOV"
            if not self._confirm_write(f"{command} offsets={vals}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_proximity_offsets(vals)
            finally:
                self._set_writable(True)
                self._pending_proximity_offsets = []
            self._last_proximity_offsets = vals
            self.log_msg(f"{command} applied")
        self._run("Write proximity offsets", work)

    def on_prox_cauco(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            self._ensure_controller_capabilities()
            count = self._proximity_count
            command = "CAUCX" if count == 8 else "CAUCO"
            if not self._confirm_destructive(
                f"{command} — use current proximity as offsets"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.use_current_proximity_offsets(count)
            finally:
                self._set_writable(True)
            values = s.get_proximity_offsets(count)
            self._update_proximity_offset_widgets(values, count)
            self.log_msg(f"{command} applied")
        self._run("CAUCO", work)

    # ------------------------------------------------------------------
    # Position matrix actions
    # ------------------------------------------------------------------

    def on_pos_mat_read(self, which: str) -> None:
        def work() -> None:
            s = self._require_session()
            if which == "sensor":
                axis = int(self.pos_sens_axis.currentData()) if hasattr(self, 'pos_sens_axis') and self.pos_sens_axis.currentData() is not None else 0
                self.pos_sens.set_values(s.get_position_sensor_matrix(axis))
            else:
                axis = int(self.pos_motor_axis.currentData()) if hasattr(self, 'pos_motor_axis') and self.pos_motor_axis.currentData() is not None else 0
                self.pos_motor.set_values(s.get_position_motor_matrix(axis))
            self.log_msg(f"position {which} matrix axis={axis} read")
        self._run("Read position matrix", work)

    def on_pos_mat_write(self, which: str) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            if which == "sensor":
                axis = int(self.pos_sens_axis.currentData()) if hasattr(self, 'pos_sens_axis') and self.pos_sens_axis.currentData() is not None else 0
                vals = self.pos_sens.values()
            else:
                axis = int(self.pos_motor_axis.currentData()) if hasattr(self, 'pos_motor_axis') and self.pos_motor_axis.currentData() is not None else 0
                vals = self.pos_motor.values()
            if not self._confirm_write(f"position {which} matrix axis={axis}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            if which == "sensor":
                s.set_position_sensor_matrix(axis, vals)
            else:
                s.set_position_motor_matrix(axis, vals)
            self._set_writable(True)
            self.log_msg(f"position {which} matrix written")
        self._run("Write position matrix", work)

    # ------------------------------------------------------------------
    # FF actions
    # ------------------------------------------------------------------

    def _on_ff_filter_cell_clicked(self, source: int, stage: int) -> None:
        """User clicked an FF filter cell."""
        self.ff_filter.axis.setCurrentIndex(source)
        self.ff_filter.stage.setValue(stage)
        self.ff_filter_panel.set_stage_index(stage)

        if self.session and self.session.connected:
            self.on_ff_filter_read_classic()

        dlg = FilterDlg(
            [f"src {i}" for i in range(7)], max_stage=7,
            show_all_axes=True, show_all_sources=True, parent=self
        )
        dlg.setWindowTitle(f"FF Filter — Source {source+1}, Stage {stage}")
        fs = self.ff_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setCurrentIndex(source)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.ff_filter.set_stage(new_stage)
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            self._update_ff_cell_text(source, stage)
            if all_axes:
                for src in range(7):
                    s = FilterStage(src, stage, new_stage.filter_type, new_stage.params)
                    self.ff_filter.axis.setCurrentIndex(src)
                    self.ff_filter.set_stage(s)
                    self.on_ff_filter_write()
                    self._update_ff_cell_text(src, stage)
            elif all_sources:
                for st in range(8):
                    s = FilterStage(source, st, new_stage.filter_type, new_stage.params)
                    self.ff_filter.stage.setValue(st)
                    self.ff_filter.set_stage(s)
                    self.on_ff_filter_write()
                    self._update_ff_cell_text(source, st)
                self.ff_filter.stage.setValue(stage)
            else:
                self.on_ff_filter_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _update_ff_cell_text(self, source: int, stage: int) -> None:
        key = (source, stage)
        if key in self.ff_filter_buttons:
            try:
                name = self.ff_filter.ftype.currentText().split(None, 1)[-1]
                short = name[:5] if len(name) > 5 else name
            except Exception:
                short = ""
            self.ff_filter_buttons[key].set_info(short)

    def on_ff_read_all_filters(self) -> None:
        """Read all 56 FF filters (7 sources × 8 stages)."""
        def work() -> None:
            s = self._require_session()
            for src in range(7):
                for st in range(8):
                    try:
                        fs = s.get_ff_filter(src, st)
                        self.ff_filter_buttons[(src, st)].set_info(fs.type_name[:5])
                    except Exception:
                        self.ff_filter_buttons[(src, st)].set_info("?")
            fs = s.get_ff_filter(0, 0)
            self.ff_filter.set_stage(fs)
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            self.log_msg("FF filters all 56 read")
        self._run("Read all FF filters", work)

    def _sync_ff_panel_to_editor(self) -> None:
        self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)

    def on_ff_filter_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            src = self.ff_filter.axis_index()
            stage_i = self.ff_filter.stage_index()
            if stage_i < 0:
                stage_i = 0
            self.ff_filter.axis.setCurrentIndex(src)
            self.ff_filter.stage.setValue(stage_i)
            self.on_ff_filter_read()
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            self._update_ff_cell_text(src, stage_i)
        self._run("Read FF filter", work)

    def on_ff_filter_write_classic(self) -> None:
        self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)
        self.on_ff_filter_write()

    def on_ff_status_read_classic(self) -> None:
        def work() -> None:
            self.on_ff_status_read()
            parts = self.ff_gains.text().split()
            for ed, p in zip(self.ff_gain_edits, parts):
                try:
                    ed.setText(f"{float(p):.5e}")
                except Exception:
                    ed.setText(p)
        self._run("FF read", work)

    def on_ff_write_gains_classic(self) -> None:
        self.ff_gains.setText(" ".join(ed.text() for ed in self.ff_gain_edits))
        self.on_ff_write_gains()

    def on_ff_status_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.ff_status.setText(" ".join(s.get_ff_status()))
            self.ff_inputs.setText(" ".join(s.get_ff_inputs()))
            try:
                self.ff_cfg.setText(" ".join(s.get_ff_config()))
            except Exception:
                pass
            try:
                self.ff_params.setText(" ".join(s.get_ff_parameters(0)))
            except Exception:
                pass
            try:
                self.ff_gains.setText(" ".join(f"{x:g}" for x in s.get_ff_gains(0)))
            except Exception:
                pass
            try:
                self.ff_mult.setText(" ".join(f"{x:g}" for x in s.get_stage_ff_multipliers()))
            except Exception:
                pass
            self.log_msg("FF status/config read")
        self._run("Read FF status", work)

    def on_ff_filter_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            src = self.ff_filter.axis_index()
            stg = self.ff_filter.stage_index()
            fs = s.get_ff_filter(src, stg)
            self.ff_filter.set_stage(fs)
            self.log_msg(f"FGPFS source={src} stage={stg} type={fs.type_name}")
        self._run("Read FF filter", work)

    def on_ff_filter_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.ff_filter.to_stage()
            if not self._confirm_write(f"FSPFS source={stage.axis} stage={stage.stage} type={stage.filter_type}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_ff_filter(stage)
            self._set_writable(True)
            self.log_msg("FSPFS applied")
        self._run("Write FF filter", work)

    def on_ff_write_cfg(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            params = self.ff_cfg.text().split()
            if not self._confirm_write(f"FSFFC {params}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_ff_config(*params)
            self._set_writable(True)
            self.log_msg("FSFFC applied")
        self._run("FF config write", work)

    def on_ff_write_gains(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            gains = [float(x) for x in self.ff_gains.text().split()]
            if not self._confirm_write(f"FSFFG gains={gains}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_ff_gains(0, *gains)
            self._set_writable(True)
            self.log_msg("FSFFG applied")
        self._run("FF gains write", work)

    def on_ff_write_mult(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            mult = [float(x) for x in self.ff_mult.text().split()] if self.ff_mult.text().strip() else []
            if not self._confirm_write(f"FSSFM {mult}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            if mult:
                s.set_stage_ff_multipliers(mult)
            self._set_writable(True)
            self.log_msg("FF mult written")
        self._run("FF mult write", work)

    def on_ff_write_inputs(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            params = self.ff_inputs.text().split()
            if not self._confirm_write(f"FSFFI {params}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_ff_inputs(*params)
            self._set_writable(True)
            self.log_msg("FSFFI applied")
        self._run("FF inputs write", work)

    def on_ff_reset(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            source = self.ff_filter.axis_index()
            if not self._confirm_destructive(f"FARFF reset FIR source={source}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.reset_ff_fir(source)
            finally:
                self._set_writable(True)
            self.log_msg(f"FARFF applied to source={source}")
        self._run("FF reset", work)

    # ------------------------------------------------------------------
    # PFF actions
    # ------------------------------------------------------------------

    def _on_pff_stage_selected(self, stage: int) -> None:
        # Legacy handler — redirect to grid cell click for source 0
        self._on_pff_filter_cell_clicked(0, stage)

    def _sync_pff_panel_to_editor(self) -> None:
        self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)

    def on_pff_filter_read_classic(self) -> None:
        def work() -> None:
            src = 0
            stage_i = self.pff_filter.stage_index()
            if stage_i < 0:
                stage_i = 0
            self.pff_filter.axis.setCurrentIndex(0)
            self.pff_filter.stage.setValue(stage_i)
            self.on_pff_filter_read()
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
            self._update_pff_cell_text(src, stage_i)
        self._run("Read PFF filter", work)

    def on_pff_filter_write_classic(self) -> None:
        self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)
        self.on_pff_filter_write()

    def on_pff_read_classic(self) -> None:
        def work() -> None:
            self.on_pff_read()
            parts = self.pff_gains.text().split()
            for ed, p in zip(self.pff_gain_edits, parts):
                try:
                    ed.setText(f"{float(p):.5e}")
                except Exception:
                    ed.setText(p)
        self._run("PFF read", work)

    def on_pff_write_gains_classic(self) -> None:
        self.pff_gains.setText(" ".join(ed.text() for ed in self.pff_gain_edits))
        self.on_pff_write_gains()

    def on_pff_read(self) -> None:
        # Placeholder — actual PFF read logic in extra_pages
        self.log_msg("PFF read (stub)")

    def on_pff_filter_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.pff_axis.value()) if hasattr(self, 'pff_axis') else 0
            src = int(self.pff_source.value()) if hasattr(self, 'pff_source') else 0
            stage = int(self.pff_filter.stage_index())
            fs = s.get_pff_filter(axis, src, stage)
            self.pff_filter.set_stage(fs)
            self.log_msg(f"FGFSP axis={axis} source={src} stage={stage} type={fs.type_name}")
        self._run("Read PFF filter", work)

    def on_pff_filter_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.pff_filter.to_stage()
            axis = int(self.pff_axis.value()) if hasattr(self, "pff_axis") else stage.axis
            src = int(self.pff_source.value()) if hasattr(self, 'pff_source') else 0
            if not self._confirm_write(f"FSFSP axis={axis} source={src} stage={stage.stage}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_filter(axis, src, stage.stage, stage.filter_type, stage.params)
            self._set_writable(True)
            self.log_msg("FSFSP applied")
        self._run("Write PFF filter", work)

    def on_pff_write_cfg(self) -> None:
        self.log_msg("PFF config write (stub)")

    def on_pff_write_params(self) -> None:
        self.log_msg("PFF params write (stub)")

    def on_pff_write_inputs(self) -> None:
        self.log_msg("PFF inputs write (stub)")

    def on_pff_write_gains(self) -> None:
        self.log_msg("PFF gains write (stub)")

    def on_pff_reset(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            axis = int(self.pff_axis.value()) if hasattr(self, "pff_axis") else 0
            source = int(self.pff_source.value()) if hasattr(self, "pff_source") else 0
            if not self._confirm_destructive(
                f"FARPF reset PFF FIR axis={axis} source={source}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.reset_pff_fir(axis, source)
            finally:
                self._set_writable(True)
            self.log_msg(f"FARPF applied to axis={axis} source={source}")
        self._run("PFF reset", work)

    # ------------------------------------------------------------------
    # Pneumatic actions
    # ------------------------------------------------------------------

    def on_pneum_filter_read(self) -> None:
        self.log_msg("Pneumatic filter read (stub)")

    def on_pneum_filter_write(self) -> None:
        self.log_msg("Pneumatic filter write (stub)")

    def on_pneum_status(self) -> None:
        self.log_msg("Pneumatic status (stub)")

    def on_pneum_steer_read(self) -> None:
        self.log_msg("Pneumatic steer read (stub)")

    def on_pneum_steer_write(self) -> None:
        self.log_msg("Pneumatic steer write (stub)")

    def on_float_read(self) -> None:
        self.log_msg("Floatation read (stub)")

    def on_float_pauco(self) -> None:
        self.log_msg("Float PAUCO (stub)")

    def on_dither_read(self) -> None:
        self.log_msg("Dither read (stub)")

    # ------------------------------------------------------------------
    # Diagnostic actions
    # ------------------------------------------------------------------

    def on_diag_read_classic(self) -> None:
        try:
            self._noise_gain_spin.setValue(float(self.noise_gain.text()))
        except Exception:
            pass
        self.on_diag_read()

    def on_diag_write_classic(self) -> None:
        self.on_diag_write()

    def on_diag_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            nt = s.get_noise_type()
            idx = self.noise_type.findData(nt)
            if idx >= 0:
                self.noise_type.setCurrentIndex(idx)
            self.noise_gain.setText(f"{s.get_noise_gain():.5e}")
            self.noise_freq.setText(f"{s.get_noise_frequency():.5e}")
            inject = s.get_noise_inject_point()
            if len(inject) >= 3:
                self.noise_inject.set_io_signal(inject[:3])
            diagnostics = s.get_diagnostic_outputs()
            if len(diagnostics) >= 6:
                self.diag_0.set_io_signal(diagnostics[:3])
                self.diag_1.set_io_signal(diagnostics[3:6])
            try:
                usage = str(s.get_noise_filter_usage()).strip().upper()
                usage_index = self.noise_filt_usage.findText(usage)
                if usage_index >= 0:
                    self.noise_filt_usage.setCurrentIndex(usage_index)
                enabled = usage in {"1", "T", "TRUE", "Y", "YES", "ON"}
                if hasattr(self, "exc_filter_usage_label"):
                    self.exc_filter_usage_label.setText("ON" if enabled else "OFF")
                    self.exc_filter_usage_label.setProperty("active", enabled)
                    self._refresh_dynamic_style(self.exc_filter_usage_label)
            except Exception as exc:
                self.log_msg(f"Excitation filter usage read: {exc}")
            for stage in range(4):
                try:
                    filt = s.get_noise_filter_stage(stage)
                    if stage < len(getattr(self, "exc_filter_buttons", [])):
                        self.exc_filter_buttons[stage].set_info(
                            filt.type_name,
                            short=filter_small_name(filt.filter_type),
                        )
                except Exception as exc:
                    self.log_msg(f"Excitation filter {stage + 1} read: {exc}")
            self.log_msg(f"diag noise_type={nt}")
        self._run("Read diagnostics", work)

    def on_diag_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            nt = int(self.noise_type.currentData())
            gain = float(self.noise_gain.text())
            frequency = float(self.noise_freq.text())
            inject = self.noise_inject.io_tokens()
            diagnostics = self.diag_0.io_tokens() + self.diag_1.io_tokens()
            if not self._confirm_write(
                f"noise type={nt} gain={gain} injection={inject} "
                f"diagnostics={diagnostics}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_noise_type(nt)
                s.set_noise_gain(gain)
                s.set_noise_frequency(frequency)
                s.set_noise_inject_point(*inject)
                s.set_diagnostic_outputs(*diagnostics)
            finally:
                self._set_writable(True)
            self.log_msg("diagnostics written")
        self._run("Write diagnostics", work)

    def on_diag_trace_start(self) -> None:
        """Optionally apply trace setup, then start digital acquisition."""
        def work() -> None:
            s = self._require_session()
            params = self.dig_trace_info.text().split()
            if params:
                if not self._confirm_write(f"DSTIV {' '.join(params)}"):
                    return
                if self.gate is None:
                    raise RuntimeError("Safety gate is not initialized")
                self.gate.take_snapshot()
                s.set_digital_trace_info(*params)
            result = s.start_digital_trace()
            self.dig_trace_status.setText(" ".join(result))
            self.log_msg("digital trace started")

        self._run("Start digital trace", work)

    def on_diag_trace_status(self) -> None:
        def work() -> None:
            status = self._require_session().get_digital_trace_status()
            self.dig_trace_status.setText(" ".join(status))
            self.log_msg("digital trace status read")

        self._run("Digital trace status", work)

    def on_diag_trace_read_buffer(self) -> None:
        def work() -> None:
            values = self._require_session().get_digital_trace_buffer()
            self.dig_trace_buf.setPlainText(
                "\n".join(format_ui_number(value) for value in values)
            )
            self.log_msg(f"digital trace buffer read ({len(values)} samples)")

        self._run("Read digital trace buffer", work)

    # ------------------------------------------------------------------
    # Performance / Switch
    # ------------------------------------------------------------------

    def on_perf_read(self) -> None:
        """Read performance monitor config from controller."""
        def work() -> None:
            s = self._require_session()
            try:
                pm = s.get_performance_monitor()
                if len(pm) >= 3:
                    self.perf_signal.setText(pm[0])
                    self.perf_threshold.setText(pm[1])
                    self.perf_min_trig.setText(pm[2])
                if len(pm) >= 4:
                    self.perf_hold.setText(pm[3])
            except Exception:
                pass
            try:
                self.perf_load.setText(f"{s.get_system_load():.3e}")
            except Exception:
                pass
            try:
                self.lbl_opl.setText(str(s.get_output_limit()))
            except Exception:
                pass
            self.log_msg("performance read")
        self._run("Read performance", work)

    def on_switch_read(self) -> None:
        """Read switch criterion from controller."""
        def work() -> None:
            s = self._require_session()
            try:
                sig = s.get_switch_signal()
                self.sw_signal.setText(" ".join(sig))
            except Exception:
                pass
            try:
                cond = s.get_switch_conditions()
                if len(cond) >= 3:
                    self.sw_trig.setText(cond[0])
                    self.sw_min.setText(cond[1])
                    self.sw_hold.setText(cond[2])
            except Exception:
                pass
            try:
                st = s.get_switch_status()
                self.sw_fb.setText(" ".join(st))
            except Exception:
                pass
            self.log_msg("switch read")
        self._run("Read switch", work)

    # ------------------------------------------------------------------
    # ADC / DAC
    # ------------------------------------------------------------------

    def on_adc_read(self) -> None:
        """Read the complete AD/DA mapping page like ADDAMappingPage.UpdatePage."""
        def work() -> None:
            s = self._require_session()
            seq = s.get_adc_sequence()
            for ed, val in zip(self.adc_edits, seq):
                ed.setText(str(val))
            try:
                adc_count = int(s.get_adc_set_number())
                if isinstance(self.adc_set_num, QtWidgets.QComboBox):
                    if not 0 <= adc_count < self.adc_set_num.count():
                        raise ValueError(f"NGASN returned invalid ADC set {adc_count}")
                    self.adc_set_num.blockSignals(True)
                    self.adc_set_num.setCurrentIndex(adc_count)
                    self.adc_set_num.blockSignals(False)
                else:
                    self.adc_set_num.setValue(adc_count)
            except Exception as exc:
                self.log_msg(f"ADC set number read: {exc}")
            try:
                temperatures = s.get_temp_sensor_adc_mapping()
                for editor, value in zip(self.adc_temperature_edits, temperatures):
                    editor.setText(str(value))
            except Exception as exc:
                self.log_msg(f"Temperature ADC mapping read: {exc}")
            dac = s.get_dac_sequence()
            for editor, value in zip(self.dac_edits, dac):
                editor.setText(str(value))
            self.log_msg(
                f"AD/DA mapping read ({len(seq)} ADC, {len(dac)} DAC channels)"
            )
        self._run("Read ADC", work)

    def on_adc_write(self) -> None:
        """Write ADC sequence, selected count, and temperature mapping."""
        def work() -> None:
            s = self._require_session()
            assert self.gate
            vals = [int(ed.text()) for ed in self.adc_edits]
            temperatures = [int(ed.text()) for ed in self.adc_temperature_edits]
            set_number = (
                self.adc_set_num.currentIndex()
                if isinstance(self.adc_set_num, QtWidgets.QComboBox)
                else self.adc_set_num.value()
            )
            if not self._confirm_write(
                f"ADC count={set_number}, sequence={vals[:5]}..., temp={temperatures}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_adc_set_number(set_number)
                s.set_adc_sequence(vals)
                s.set_temp_sensor_adc_mapping(temperatures)
            finally:
                self._set_writable(True)
            self.log_msg("ADC sequence/count/temperature mapping written")
        self._run("Write ADC", work)

    def on_dac_read(self) -> None:
        """Read DAC channel sequence from controller."""
        def work() -> None:
            s = self._require_session()
            seq = s.get_dac_sequence()
            for ed, val in zip(self.dac_edits, seq):
                ed.setText(str(val))
            self.log_msg(f"DAC sequence read ({len(seq)} channels)")
        self._run("Read DAC", work)

    def on_dac_write(self) -> None:
        """Write DAC channel sequence to controller."""
        def work() -> None:
            s = self._require_session()
            assert self.gate
            vals = [int(ed.text()) for ed in self.dac_edits]
            if not self._confirm_write(f"BSDAS DAC sequence: {vals[:5]}..."):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_dac_sequence(vals)
            self._set_writable(True)
            self.log_msg("DAC sequence written")
        self._run("Write DAC", work)

    # ------------------------------------------------------------------
    # Motor protection
    # ------------------------------------------------------------------

    def _refresh_motor_protection_live_state(self, loop=None) -> None:
        """Refresh the dynamic Motor Threshold Setting values.

        The legacy page separates ``UpdatePage`` (configuration reads) from
        ``UpdateStates`` (one-second live reads).  Calling the full
        :meth:`on_motor_prot_read` method from the timer would reread all
        thresholds, offsets and limits and would make the serial UI sluggish.
        This method therefore mirrors only the commands issued by the legacy
        ``UpdateStates`` implementation: BGMPV, BGMPS and, when advertised,
        LGPSL.
        """

        session = self._require_session()
        if loop is not None and hasattr(self, "mot_use_temperature"):
            self._set_motor_toggle_silently(
                self.mot_use_temperature,
                bool(loop.system & int(SystemStatus.USE_TEMP_SENSORS)),
            )

        power = session.get_motor_power_values()
        for editor, value in zip(
            getattr(self, "mot_actual_values", ()), power
        ):
            editor.setText(format_ui_number(value))

        failsafe = session.get_motor_failsafe_status()
        for label, value in zip(
            getattr(self, "mot_status_labels", ()), failsafe
        ):
            text, color = _motor_status_presentation(value)
            label.setText(text)
            label.setStyleSheet(
                f"background:{color};color:#203443;"
                "border:1px solid #aebfca;border-radius:4px;"
                "padding-left:7px;font-size:14px;"
            )

        # The legacy page queries LGPSL only when the firmware advertises the
        # power-supply-current-limit feature.  Unknown capabilities keep the
        # controls visible, so retain the conservative read in that case.
        if not hasattr(self, "ps_current_limit"):
            return
        features = self._controller_features
        if features is not None and "PSUCL" not in features:
            return
        power_supply = session.get_power_supply_parameters()
        if len(power_supply) < 8:
            return
        self.ps_current_limit.setText(str(power_supply[0]))
        self.ps_current_si_unit.setText(str(power_supply[1]))
        try:
            status_word = int(str(power_supply[2]), 0)
        except ValueError:
            status_word = int(float(power_supply[2]))
        overpowered = bool(status_word & 0x01)
        self.ps_overpowered.setText("Yes" if overpowered else "No")
        self.ps_overpowered.setStyleSheet(
            "background:" + ("#ffb4b4" if overpowered else "#90ee90")
            + ";color:#203443;border:1px solid #aebfca;"
            "border-radius:4px;font-size:14px;"
        )
        for editor, value in zip(self.ps_actual_values, power_supply[3:8]):
            editor.setText(format_ui_number(value))

    @staticmethod
    def _set_motor_toggle_silently(toggle: QtWidgets.QAbstractButton, checked: bool) -> None:
        """Refresh a motor-protection rocker without issuing a write."""

        previous = toggle.blockSignals(True)
        try:
            toggle.setChecked(bool(checked))
        finally:
            toggle.blockSignals(previous)

    def on_motor_use_temperature_toggled(self, checked: bool) -> None:
        """Update only the UseTempSensors bit in the system status word."""

        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            loop = s.get_loop_status()
            bit = int(SystemStatus.USE_TEMP_SENSORS)
            system = (loop.system | bit) if checked else (loop.system & ~bit)
            if system == loop.system:
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_loop_status(loop.individual, system)
            finally:
                self._set_writable(True)
            self.log_msg(
                f"Use temperature sensors {'ON' if checked else 'OFF'} "
                f"(BSSTS system=0x{system:X})"
            )

        self._run("Set motor temperature sensor mode", work)

    def on_motor_disable_toggled(self, checked: bool) -> None:
        """Write DisableAllFlag immediately, matching the legacy rocker."""

        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            cfg = list(s.get_motor_overcurrent_config())
            if len(cfg) < 14:
                raise RuntimeError(
                    f"BGOCV returned {len(cfg)} values; expected flag, delay and 12 thresholds"
                )
            cfg[0] = _motor_disable_flag_token(checked)
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_motor_overcurrent_config(*cfg)
            finally:
                self._set_writable(True)
            self.log_msg(
                f"Disable all by failure {'ON' if checked else 'OFF'} (BSOCV)"
            )

        self._run("Set motor failure protection mode", work)

    def on_motor_prot_read(self) -> None:
        """Read motor overcurrent protection values."""
        def work() -> None:
            s = self._require_session()
            cfg = s.get_motor_overcurrent_config()
            # cfg format: [DisableAllFlag, ResetDelay, thresh0, thresh1, ..., thresh11]
            if len(cfg) >= 2:
                self._set_motor_toggle_silently(
                    self.mot_disable,
                    _motor_disable_flag_enabled(cfg[0]),
                )
                self.mot_delay.setText(cfg[1])
            if len(cfg) >= 14:
                for i in range(12):
                    self.mot_thresholds[i].setText(cfg[i + 2])
            # Read cooling constant
            try:
                self.mot_cool.setText(
                    format_ui_number(s.get_motor_overcurrent_cooling_constant())
                )
            except Exception as exc:
                self.log_msg(f"Motor cooling constant read: {exc}")
            try:
                loop = s.get_loop_status()
                self._set_motor_toggle_silently(
                    self.mot_use_temperature,
                    bool(loop.system & int(SystemStatus.USE_TEMP_SENSORS)),
                )
            except Exception as exc:
                self.log_msg(f"Motor temperature-sensor mode read: {exc}")
            try:
                self._ensure_controller_capabilities()
                linear_12 = bool(
                    self._controller_features
                    and "SALMO" in self._controller_features
                )
                self._configure_motor_offset_mode(linear_12)
                if linear_12:
                    offsets = s.get_linear_motor_offsets()
                    for editor, value in zip(self.mot_offsets, offsets):
                        editor.setText(format_ui_number(value))
                else:
                    offsets = s.get_motor_offsets()
                    if len(offsets) < 8:
                        raise RuntimeError(
                            f"CGMOV returned {len(offsets)} offsets; expected at least 8"
                        )
                    for ui_index, wire_index in LEGACY_MOTOR_OFFSET_UI_TO_WIRE.items():
                        self.mot_offsets[ui_index].setText(
                            format_ui_number(offsets[wire_index])
                        )
            except Exception as exc:
                self.log_msg(f"Motor offsets read: {exc}")
            try:
                power = s.get_motor_power_values()
                for editor, value in zip(self.mot_actual_values, power):
                    editor.setText(format_ui_number(value))
            except Exception as exc:
                self.log_msg(f"Motor power values read: {exc}")
            try:
                failsafe = s.get_motor_failsafe_status()
                for label, value in zip(self.mot_status_labels, failsafe):
                    text, color = _motor_status_presentation(value)
                    label.setText(text)
                    label.setStyleSheet(
                        f"background:{color};color:#203443;"
                        "border:1px solid #aebfca;border-radius:4px;"
                        "padding-left:7px;font-size:14px;"
                    )
            except Exception as exc:
                self.log_msg(f"Motor failsafe status read: {exc}")
            try:
                if self.mot_limit_edits:
                    self.mot_limit_edits[0].setText(str(s.get_output_limit()))
            except Exception as exc:
                self.log_msg(f"Motor limit read: {exc}")
            try:
                power_supply = s.get_power_supply_parameters()
                if len(power_supply) >= 8 and hasattr(self, "ps_current_limit"):
                    self.ps_current_limit.setText(str(power_supply[0]))
                    self.ps_current_si_unit.setText(str(power_supply[1]))
                    try:
                        status_word = int(str(power_supply[2]), 0)
                    except ValueError:
                        status_word = int(float(power_supply[2]))
                    overpowered = bool(status_word & 0x01)
                    self.ps_overpowered.setText("Yes" if overpowered else "No")
                    self.ps_overpowered.setStyleSheet(
                        "background:" + ("#ffb4b4" if overpowered else "#90ee90")
                        + ";color:#203443;border:1px solid #aebfca;"
                        "border-radius:4px;font-size:14px;"
                    )
                    for editor, value in zip(
                        self.ps_actual_values, power_supply[3:8]
                    ):
                        editor.setText(format_ui_number(value))
            except Exception as exc:
                self.log_msg(f"Power supply current limit read: {exc}")
            self.log_msg("motor protection read")
        self._run("Read motor protection", work)

    def _write_power_supply_parameters(
        self, *, reset_counter: bool = False, reset_max: bool = False
    ) -> None:
        if not self.session or not self.session.connected:
            return

        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            limit = float(self.ps_current_limit.text())
            si_unit = float(self.ps_current_si_unit.text())
            summary = (
                f"LSPSL limit={limit:g}, SIUnit={si_unit:g}, "
                f"resetCounter={int(reset_counter)}, resetMax={int(reset_max)}"
            )
            if not self._confirm_write(summary):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_power_supply_parameters(
                    limit, si_unit, int(reset_counter), int(reset_max)
                )
            finally:
                self._set_writable(True)
            self.log_msg("power supply current-limit parameters written")
            self.on_motor_prot_read()

        self._run("Write power supply current limit", work)

    def on_power_supply_write(self) -> None:
        self._write_power_supply_parameters()

    def on_power_supply_reset_counter(self) -> None:
        self._write_power_supply_parameters(reset_counter=True)

    def on_power_supply_reset_max(self) -> None:
        self._write_power_supply_parameters(reset_max=True)

    def _configure_motor_offset_mode(self, linear_12: bool) -> None:
        """Present 12 SALMO offsets or the legacy eight X/Y motor offsets."""
        for index, editor in enumerate(self.mot_offsets):
            enabled = linear_12 or index in LEGACY_MOTOR_OFFSET_UI_TO_WIRE
            editor.setReadOnly(not enabled)
            editor.setEnabled(True)
            if not enabled:
                editor.clear()
            editor.setToolTip(
                "Linear motor offset (LGLMO/LSLMO)"
                if linear_12
                else (
                    "Legacy motor offset (CGMOV/CSMOV)"
                    if enabled
                    else "No Z-axis offset on this controller"
                )
            )

    def on_motor_offset_write(self) -> None:
        """Write only the offset endpoint selected by the SALMO capability."""
        def work() -> None:
            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            self._ensure_controller_capabilities()
            linear_12 = bool(
                self._controller_features and "SALMO" in self._controller_features
            )
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                if linear_12:
                    s.set_linear_motor_offsets(
                        [float(editor.text()) for editor in self.mot_offsets]
                    )
                else:
                    current = list(s.get_motor_offsets())
                    if len(current) != 11:
                        raise RuntimeError(
                            f"CGMOV returned {len(current)} offsets; expected 11"
                        )
                    for ui_index, wire_index in LEGACY_MOTOR_OFFSET_UI_TO_WIRE.items():
                        current[wire_index] = float(self.mot_offsets[ui_index].text())
                    s.set_motor_offsets(current)
            finally:
                self._set_writable(True)
            self.log_msg(
                "12 linear motor offsets written"
                if linear_12 else "8 legacy motor offsets written; isolator offsets preserved"
            )

        self._run("Write motor offsets", work)

    def on_motor_prot_write(self) -> None:
        """Write motor overcurrent protection values."""
        def work() -> None:
            s = self._require_session()
            assert self.gate
            disable = _motor_disable_flag_token(self.mot_disable.isChecked())
            delay = self.mot_delay.text()
            thresh = [ed.text() for ed in self.mot_thresholds]
            params = [disable, delay] + thresh
            cooling = float(self.mot_cool.text())
            limit = int(float(self.mot_limit_edits[0].text() or "0"))
            if not self._confirm_write(f"BSOCV disable={disable} delay={delay}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_motor_overcurrent_config(*params)
                s.set_motor_overcurrent_cooling_constant(cooling)
                s.set_output_limit(limit)
            finally:
                self._set_writable(True)
            self.log_msg("motor protection/limit written")
        self._run("Write motor protection", work)

    # ------------------------------------------------------------------
    # Safety / ZMS / Polynom
    # ------------------------------------------------------------------

    def on_safety_read(self) -> None:
        """Read safety / earthquake status."""
        def work() -> None:
            s = self._require_session()
            try:
                # Read amplifier disable events as a proxy for safety status
                events = s.get_amplifier_disable_events()
                for i, led in enumerate(self.safety_amp_leds):
                    if i < len(events):
                        led.set_on(events[i] > 0, "#ef4444")
                    else:
                        led.set_on(False)
                self.log_msg("safety status read")
            except Exception as exc:
                # Use firmware config as fallback
                self.safety_status.setText(f"n/a: {exc}")
                self.log_msg(f"safety: {exc}")
        self._run("Read safety", work)

    def on_zms_read(self) -> None:
        """Read ZMS stability status."""
        def work() -> None:
            s = self._require_session()
            try:
                status = s.get_zms_stability_status()
                if len(status) >= 2:
                    self.zms_vibration.setText(status[0])
                    self.zms_position.setText(status[1])
            except Exception:
                pass
            try:
                rms = s.get_zms_rms_values()
                if len(rms) >= 6:
                    for i in range(6):
                        self.zms_thresholds[i].setText(f"{rms[i]:.5e}")
            except Exception:
                pass
            try:
                axis, rms_val = s.get_zms_last_failed_event()
                self.zms_axis.setText(f"Axis {axis}")
                self.zms_rms.setText(format_ui_number(rms_val))
            except Exception:
                pass
            self.log_msg("ZMS status read")
        self._run("Read ZMS", work)

    def on_zms_write(self) -> None:
        """Write ZMS stability thresholds."""
        def work() -> None:
            s = self._require_session()
            assert self.gate
            vals = [float(ed.text()) for ed in self.zms_thresholds]
            if not self._confirm_write(f"ZMS thresholds: {vals}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_zms_stability_thresholds(vals)
            self._set_writable(True)
            self.log_msg("ZMS thresholds written")
        self._run("Write ZMS", work)

    def on_poly_read(self) -> None:
        """Read polynom configuration."""
        def work() -> None:
            s = self._require_session()
            self.log_msg("Polynom read (stub)")
        self._run("Read polynom", work)

    def on_poly_write(self) -> None:
        """Write polynom configuration."""
        def work() -> None:
            s = self._require_session()
            assert self.gate
            if not self._confirm_write("Polynom write"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            self._set_writable(True)
            self.log_msg("Polynom written (stub)")
        self._run("Write polynom", work)

    # ------------------------------------------------------------------
    # NVRAM / Setup
    # ------------------------------------------------------------------

    def on_setup_load_file(self) -> None:
        """Load a setup file and, when online, send it to the controller.

        This mirrors the legacy ``Open File -> Controller`` action.  Offline
        selection still validates and remembers the file, while an online
        selection immediately applies it.
        """
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load setup file", "",
            "SAMBA19x Config files (*.SAMBA19x_Config *.xml);;All (*.*)"
        )
        if not path:
            return
        try:
            from python_samba.services.config_reader import load_config

            cfg = load_config(path)
        except Exception as exc:
            self.log_msg(f"Error loading config: {exc}")
            QtWidgets.QMessageBox.critical(self, "Load setup file", str(exc))
            return

        # Commit the selection only after the XML has passed validation.  The
        # previous ordering left a malformed file looking successfully loaded.
        self.setup_file_lbl.setText(path)
        self._loaded_config_path = path
        self.log_msg(f"Loaded config: FW={cfg.firmware_version[:30]}")
        self.log_msg(f"  Velocity filters: {len(cfg.vel_filters)} axes")
        self.log_msg(f"  Position filters: {len(cfg.pos_filters)} axes")
        self.log_msg(
            "  Feed-forward filters: "
            f"{sum(len(values) for values in cfg.ff_ref_filter.values())} reference stages"
        )
        if self.session and self.session.connected:
            self._apply_setup_file(path, cfg)
        else:
            self.log_msg("Setup loaded offline; connect and apply it to send parameters")

    def _apply_setup_file(self, path: str, cfg=None) -> None:
        """Apply one validated setup and restore the prior write-lock state."""
        def work() -> None:
            from python_samba.services.config_reader import (
                apply_config_to_session,
                load_config,
            )

            s = self._require_session()
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            loaded = cfg if cfg is not None else load_config(path)
            self.gate.take_snapshot()
            was_readonly = bool(s.readonly)
            was_unlocked = bool(self.gate.unlocked)
            s.readonly = False
            self.gate.unlocked = True
            try:
                errors = apply_config_to_session(loaded, s)
            finally:
                s.readonly = was_readonly
                self.gate.unlocked = was_unlocked
            if errors:
                preview = "\n".join(errors[:8])
                suffix = "" if len(errors) <= 8 else f"\n... and {len(errors) - 8} more"
                raise RuntimeError(
                    f"{len(errors)} configuration writes failed:\n{preview}{suffix}\n\n"
                    "Some earlier writes may already have succeeded. Restore the original "
                    "setup file before continuing."
                )
            self.log_msg(f"Config fully applied from {path}")

        self._run("Apply config", work)

    def on_setup_apply_file(self) -> None:
        """Apply the loaded config file to the connected controller."""
        path = getattr(self, '_loaded_config_path', None)
        if not path:
            QtWidgets.QMessageBox.warning(self, "No file", "Load a .SAMBA19x_Config file first")
            return
        if not self.session or not self.session.connected:
            QtWidgets.QMessageBox.warning(self, "Not connected", "Connect to a controller first")
            return
        self._apply_setup_file(path)

    def on_setup_save_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save setup file", "setup.SAMBA19x_Config",
            "SAMBA19x Config files (*.SAMBA19x_Config);;XML (*.xml);;All (*.*)"
        )
        if path:
            if not self.session or not self.session.connected:
                QtWidgets.QMessageBox.warning(
                    self, "Not connected", "Connect to a controller before saving."
                )
                return

            def work() -> None:
                from python_samba.services.config_reader import (
                    capture_config_from_session,
                    save_config,
                )
                config = capture_config_from_session(self._require_session())
                if config.capture_warnings:
                    preview = "\n".join(config.capture_warnings[:8])
                    raise RuntimeError(
                        "Controller snapshot is incomplete; no setup file was written.\n"
                        f"{len(config.capture_warnings)} reads failed:\n{preview}"
                    )
                save_config(path, config)
                self.setup_file_lbl.setText(path)
                self._loaded_config_path = path
                self.log_msg(f"Setup snapshot saved: {path}")

            self._run("Save controller setup", work)

    def on_nvram_checksums(self) -> None:
        self.on_read_checksum()

    def on_nvram(self, op: str) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            labels = {
                "save": ("NASUP save to NVRAM", s.nvram_save),
                "restore": ("NARUP restore from NVRAM", s.nvram_restore),
                "clear": ("NACLR CLEAR NVRAM", s.nvram_clear),
            }
            summary, fn = labels[op]
            if op in {"save", "clear"} and bool(
                getattr(self, "_nvram_protected", True)
            ):
                self.log_msg(f"NVRAM {op} blocked: Protection is ON")
                return

            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle("Confirm NVRAM action")
            box.setText("Apply this NVRAM action to the controller?")
            box.setInformativeText(
                summary + "\n\nA local snapshot will be saved first."
            )
            box.setStandardButtons(
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel
            )
            if box.exec() != QtWidgets.QMessageBox.Ok:
                return
            self.gate.take_snapshot()
            was_readonly = bool(s.readonly)
            was_unlocked = bool(self.gate.unlocked)
            s.readonly = False
            self.gate.unlocked = True
            try:
                fn()
            finally:
                s.readonly = was_readonly
                self.gate.unlocked = was_unlocked
            self.log_msg(f"NVRAM {op} done")
        self._run(f"NVRAM {op}", work)

    def on_si_select(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select SI file", "", "SI files (*.SI *.si);;All (*.*)")
        if path:
            self.si_file_lbl.setText(path)

    # ------------------------------------------------------------------
    # Raw RCI
    # ------------------------------------------------------------------

    def on_raw_send(self) -> None:
        def work() -> None:
            s = self._require_session()
            mnemonic = self.raw_cmd.text().strip().upper()
            params: list[str | int | float] = []
            for p in self.raw_params.text().split():
                try:
                    if any(c in p for c in ".eE"):
                        params.append(float(p))
                    else:
                        params.append(int(p, 0))
                except ValueError:
                    params.append(p)
            resp = s.raw_command(mnemonic, *params)
            self.raw_out.setPlainText(
                f"ok={resp.ok} status=0x{resp.status_code:02X}\n"
                f"mnemonic={resp.mnemonic}\n"
                f"data={resp.data_text}\n"
                f"raw={resp.raw}"
            )
            self.log_msg(f"RAW {mnemonic} ok={resp.ok}")
        self._run("Raw RCI", work)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self.session:
            self.session.close()
        super().closeEvent(event)
