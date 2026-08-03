"""Patch: replace _build_system_setting_page with full C#-featured version.

This module provides:
- ``LedBtn`` — clickable LED toggle button (matching C# ``LEDBtn``)
- ``build_system_setting_page`` — full replacement method including all
  firmware-config LEDs, auto-loop switch, EtherCat status, IOSignal
  selectors, and sample-frequency warning dialog.

Usage in ``main_window.py``:
    from python_samba._patches.system_setting_patch import (
        build_system_setting_page,
    )
    # then replace the method on the class or the instance
    MainWindow._build_system_setting_page = build_system_setting_page
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    IOSignalButton,
    LedIndicator,
    SciEdit,
    SciSpin,
    LED_GREEN,
    LED_OFF,
    LED_RED,
)


# ---------------------------------------------------------------------------
# LedBtn — clickable toggle LED button (matching C# LEDBtn)
# ---------------------------------------------------------------------------

class LedBtn(QtWidgets.QWidget):
    """Clickable LED toggle button with label.

    Mirrors C# SAMBA19xUI ``LEDBtn``: shows a coloured LED indicator,
    toggles on click, and emits ``toggled(bool)``.
    """

    toggled = QtCore.Signal(bool)

    def __init__(
        self,
        text: str = "",
        diameter: int = 18,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._state = False
        self._diameter = diameter
        self._on_color = LED_GREEN

        self._led = LedIndicator(diameter, self)
        self._label = QtWidgets.QLabel(text, self)
        self._label.setStyleSheet("color: #303030; font-weight: 600; font-size: 10px;")
        self._label.setAlignment(QtCore.Qt.AlignCenter)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        lay.addWidget(self._led, 0, QtCore.Qt.AlignCenter)
        lay.addWidget(self._label, 0, QtCore.Qt.AlignCenter)

        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedWidth(max(diameter + 20, 62))
        self.setStyleSheet(
            "LedBtn { border: 1px solid #888; border-radius: 4px; padding: 2px; }"
            "LedBtn:hover { border-color: #aaa; background: #e8e8e8; }"
        )

    # -- public helpers -----------------------------------------------------

    def set_state(self, on: bool) -> None:
        """Set the LED on/off without emitting a signal."""
        self._state = on
        self._led.set_on(on, self._on_color)

    def is_on(self) -> bool:
        return self._state

    def toggle(self) -> None:
        """Flip the state and emit ``toggled``."""
        self._state = not self._state
        self._led.set_on(self._state, self._on_color)
        self.toggled.emit(self._state)

    # -- Qt overrides -------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.toggle()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Replacement _build_system_setting_page
# ---------------------------------------------------------------------------

def build_system_setting_page(self) -> QtWidgets.QWidget:
    """System Setting page — full feature set from C# SAMBA19xUI.

    Adds vs. original:
      - 7 Firmware-configuration LED toggle buttons (Vel / Pos / Pneu /
        FF / SFF / FFF / PFF)
      - Set Firmware Config push button
      - EtherCat Motor and SFF Signal status LED buttons
      - Auto-loop switch LED buttons (AlwaysV, AlwaysP, AutoSwitch)
        with RunningV / RunningP status LEDs
      - Switch and Performance IOSignal selector fields
      - Sample-frequency ``Set`` button with a warning dialog for
        dangerous (higher) values
    """
    w = QtWidgets.QWidget()
    w.setStyleSheet("QLabel{font-size:25px;} QLineEdit,QComboBox{font-size:23px;}")
    root = QtWidgets.QVBoxLayout(w)
    root.setSpacing(6)

    # ==================================================================
    # Row 1 — Output Limit + Loop Configuration (unchanged)
    # ==================================================================
    top = QtWidgets.QHBoxLayout()

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
    top.addLayout(ol)

    # Loop Configuration
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

    # ==================================================================
    # Row 2 — Firmware Configuration LEDs + EtherCat Status
    # ==================================================================
    fw_row = QtWidgets.QHBoxLayout()

    # -- Firmware Configuration (7 clickable LED toggle bits) ----------
    g_fw = GroupPanel("Firmware Configuration")
    fw_grid = QtWidgets.QGridLayout(g_fw)
    fw_grid.setSpacing(6)

    fw_entries = [
        (0, "Vel Loop",   "VelLoopConfigBtn"),
        (1, "Pos Loop",   "PosLoopConfigBtn"),
        (2, "Pneu Loop",  "PneumLoopConfigBtn"),
        (3, "FF Loop",    "FFLoopConfigBtn"),
        (4, "SFF Loop",   "SFFLoopConfigBtn"),
        (5, "FFF Loop",   "FFFLoopConfigBtn"),
        (6, "PFF Loop",   "PFFLoopConfigBtn"),
    ]
    self._fw_leds: list[LedBtn] = []
    for col, label, _attr in fw_entries:
        led = LedBtn(label, 16)
        led.toggled.connect(self._on_fw_led_toggled)
        self._fw_leds.append(led)
        fw_grid.addWidget(led, 0, col)

    btn_fw_set = FlatPush("Set Firmware Config")
    btn_fw_set.clicked.connect(self._on_firmware_config_click)
    fw_grid.addWidget(btn_fw_set, 1, 0, 1, 7)
    fw_row.addWidget(g_fw)

    # -- EtherCat Status (LED buttons for Motor / SFF Signal) ----------
    g_ec = GroupPanel("EtherCat Status")
    ec_lay = QtWidgets.QVBoxLayout(g_ec)
    self._ec_motor_led = LedBtn("Motor", 14)
    self._ec_sff_led = LedBtn("SFF Signal", 14)
    self._ec_motor_led.toggled.connect(self._on_ethercat_led_clicked)
    self._ec_sff_led.toggled.connect(self._on_ethercat_led_clicked)

    ec_row1 = QtWidgets.QHBoxLayout()
    ec_row1.addWidget(self._ec_motor_led)
    ec_row1.addWidget(self._ec_sff_led)
    ec_lay.addLayout(ec_row1)

    # Description label
    ec_note = QtWidgets.QLabel("Click to send SetSystemLoopStatus")
    ec_note.setStyleSheet("color: #666; font-size: 10px;")
    ec_lay.addWidget(ec_note)

    fw_row.addWidget(g_ec)
    fw_row.addStretch(1)
    root.addLayout(fw_row)

    # ==================================================================
    # Row 3 — Performance Monitoring + Switch criterion (enhanced)
    # ==================================================================
    mid = QtWidgets.QHBoxLayout()

    # -- Performance Monitoring (with IOSignal selector) ---------------
    g_perf = GroupPanel("Performance Monitoring")
    pf = QtWidgets.QFormLayout(g_perf)
    self.perf_signal = SciEdit("InpX1FB")
    self.perf_signal.textChanged.connect(self._on_perf_signal_changed)
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

    # -- Switch criterion (with IOSignal selector + auto-switch LEDs) --
    g_sw = GroupPanel("Switch criterion")
    sf = QtWidgets.QFormLayout(g_sw)
    sf.setSpacing(6)

    # IOSignal selector
    self.sw_signal = SciEdit("InpX1FB")
    self.sw_signal.textChanged.connect(self._on_switch_signal_changed)
    sf.addRow("Signal:", self.sw_signal)

    # Trigger parameters
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

    trig_row = QtWidgets.QHBoxLayout()
    trig_row.addWidget(self.sw_trig)
    trig_row.addWidget(QtWidgets.QLabel("%"))
    trig_row.addWidget(self.sw_trig_slider, 1)
    trig_w = QtWidgets.QWidget()
    trig_w.setLayout(trig_row)
    sf.addRow("Trigger Level:", trig_w)
    sf.addRow("Min. trigger:", self._unit_row(self.sw_min, "s"))
    sf.addRow("Hold time:", self._unit_row(self.sw_hold, "s"))

    # -- Auto-loop switch LED buttons (AlwaysV, AlwaysP, AutoSwitch) ---
    # These mirror the C# AlwaysVBtn, AlwaysPBtn, AutoSwitchBtn
    sep = QtWidgets.QLabel("Auto-loop switch:")
    sep.setStyleSheet("font-weight: 700; color: #404040; margin-top: 4px;")
    sf.addRow(sep)

    auto_led_row = QtWidgets.QHBoxLayout()
    self._always_v_led = LedBtn("AlwaysV", 18)
    self._always_p_led = LedBtn("AlwaysP", 18)
    self._auto_switch_led = LedBtn("AutoSwitch", 18)
    self._always_v_led.toggled.connect(self._on_always_btn_clicked)
    self._always_p_led.toggled.connect(self._on_always_btn_clicked)
    self._auto_switch_led.toggled.connect(self._on_auto_switch_clicked)
    auto_led_row.addWidget(self._always_v_led)
    auto_led_row.addWidget(self._always_p_led)
    auto_led_row.addWidget(self._auto_switch_led)
    auto_led_row.addStretch(1)
    sf.addRow(auto_led_row)

    # Status LEDs (RunningV, RunningP — read-only indicators)
    status_row = QtWidgets.QHBoxLayout()
    status_row.addWidget(QtWidgets.QLabel("RunningV:"))
    self._running_v_led = LedIndicator(14)
    status_row.addWidget(self._running_v_led)
    status_row.addWidget(QtWidgets.QLabel("   RunningP:"))
    self._running_p_led = LedIndicator(14)
    status_row.addWidget(self._running_p_led)
    status_row.addStretch(1)
    sf.addRow("Status:", status_row)

    # FB status (existing)
    sf.addRow("FB status:", self.sw_fb)

    mid.addWidget(g_sw, 1)
    root.addLayout(mid)

    # ==================================================================
    # Row 4 — Start-Up Ramp + Sample Frequency (enhanced)
    # ==================================================================
    bot = QtWidgets.QHBoxLayout()

    # Start-Up Ramp (unchanged)
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

    # Sample frequency (enhanced with C#-style set button + warning)
    g_fs = GroupPanel("Sample freq")
    fs = QtWidgets.QFormLayout(g_fs)
    self.fs_sample = SciEdit("1836.0")
    self.fs_sample.setReadOnly(True)
    self.fs_load = SciEdit("0.0")
    self.fs_load.setReadOnly(True)
    fs.addRow("Sample:", self._unit_row(self.fs_sample, "Hz"))
    fs.addRow("Load:", self._unit_row(self.fs_load, "%"))

    # Manual entry + Set button (with warning dialog for higher values)
    self.fs_manual = SciEdit()
    self.fs_manual.setFixedWidth(80)
    self.fs_ok_btn = FlatPush("Set")
    self.fs_ok_btn.clicked.connect(self._on_sample_frequency_set)
    set_row = QtWidgets.QHBoxLayout()
    set_row.addWidget(self.fs_manual)
    set_row.addWidget(self.fs_ok_btn)
    fs.addRow("Set:", set_row)
    bot.addWidget(g_fs)
    root.addLayout(bot)

    # ==================================================================
    # Row 5 — Actions
    # ==================================================================
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


# ===================================================================
# Handler methods (attached to the MainWindow instance at patch time)
# ===================================================================

def build_system_setting_reference(self) -> QtWidgets.QWidget:
    """Build the compact three-column System Settings reference page."""
    from python_samba.ui.main_window import SidebarLoopButton

    w = QtWidgets.QWidget()
    root = QtWidgets.QHBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(14)

    left = QtWidgets.QVBoxLayout()
    loops = GroupPanel("Loops Configuration")
    loops.setToolTip(
        "Configured/processed loops (NGEXL). The main Loops Status panel shows "
        "the separate live running state (BGSTS/DGCSS)."
    )
    loops.setFixedSize(410, 510)
    loop_grid = QtWidgets.QGridLayout(loops)
    loop_grid.setContentsMargins(12, 18, 12, 12)
    loop_rows = (
        ("Velocity Loop", "chk_vel_cfg"),
        ("Position Loop", "chk_pos_cfg"),
        ("Pneumatic Loop", "chk_pneu_cfg"),
        ("FF Loop", "chk_ff_cfg"),
        ("    Stage FF (first 4 channels)", "chk_stage_cfg"),
        ("    Floor FF (last 3 channels)", "chk_floor_cfg"),
        ("Pneum. FF Loop", None),
    )
    self.system_loop_lamps = []
    for row, (label, checkbox_attr) in enumerate(loop_rows):
        loop_grid.addWidget(QtWidgets.QLabel(label), row, 0)
        lamp = SidebarLoopButton()
        lamp.setFixedSize(58, 48)
        lamp.setToolTip(
            f"Configured state for {label.strip()} (not the live running state)"
        )
        self.system_loop_lamps.append(lamp)
        lamp.clicked.connect(
            lambda _checked=False, i=row: self._on_system_loop_lamp_clicked(i)
        )
        loop_grid.addWidget(lamp, row, 1)
        if checkbox_attr:
            checkbox = QtWidgets.QCheckBox()
            checkbox.hide()
            setattr(self, checkbox_attr, checkbox)
    self.chk_vel_cur = QtWidgets.QCheckBox()
    self.chk_pneu_cur = QtWidgets.QCheckBox()
    self.chk_pos_cur = QtWidgets.QCheckBox()
    self.chk_ff_cur = QtWidgets.QCheckBox()
    self.chk_stage_cur = QtWidgets.QCheckBox()
    self.chk_floor_cur = QtWidgets.QCheckBox()
    self.btn_set_cfg = FlatPush("Set Configuration")
    self.btn_set_cfg.clicked.connect(self.on_loop_write)
    self.btn_set_cfg.hide()
    left.addWidget(loops)

    sample = GroupPanel("Sample Frequency/System Load")
    sample.setFixedSize(410, 210)
    sf = QtWidgets.QFormLayout(sample)
    self.fs_sample = SciEdit("4000")
    self.fs_sample.setReadOnly(True)
    self.fs_load = SciEdit("0")
    self.fs_load.setReadOnly(True)
    self.fs_manual = SciEdit("4000")
    self.fs_manual.setFixedWidth(130)
    set_sample = FlatPush("Set Sample Frequency")
    set_sample.clicked.connect(self._on_sample_frequency_set)
    sf.addRow("Sample Frequency [Hz]", self.fs_sample)
    sf.addRow("System Load [%]", self.fs_load)
    sf.addRow("", set_sample)
    left.addWidget(sample)
    left.addStretch(1)
    root.addLayout(left)

    middle = QtWidgets.QVBoxLayout()
    performance = GroupPanel("Performance Monitor Setting")
    performance.setFixedSize(500, 330)
    pf = QtWidgets.QFormLayout(performance)
    self.perf_signal = FlatPush("X1FB")
    self._populate_system_io_menu(self.perf_signal, "performance")
    self.perf_threshold = SciEdit("0")
    self.perf_min_trig = SciEdit("0")
    self.perf_hold = SciEdit("0")
    for editor in (self.perf_threshold, self.perf_min_trig, self.perf_hold):
        editor.editingFinished.connect(self._on_perf_signal_changed)
    self.perf_actual = SciEdit("Perf. Okay")
    self.perf_actual.setReadOnly(True)
    self.perf_timer = SciEdit("0")
    self.perf_timer.setReadOnly(True)
    self.perf_cfg = SciEdit()
    self.perf_cfg.hide()
    self.perf_status = self.perf_actual
    self.perf_load = SciEdit()
    self.perf_load.hide()
    pf.addRow("Signal to Monitor", self.perf_signal)
    pf.addRow("Threshold Level", self.perf_threshold)
    pf.addRow("Min. Trigger Time [sec.]", self.perf_min_trig)
    pf.addRow("Hold Time [sec.]", self.perf_hold)
    pf.addRow("Actual Perf. Status", self.perf_actual)
    pf.addRow("Timer Count[sec.]", self.perf_timer)
    middle.addWidget(performance)

    ethercat = GroupPanel("EtherCat Setting")
    ethercat.setFixedSize(500, 150)
    ec = QtWidgets.QFormLayout(ethercat)
    self._ec_sff_visible = SidebarLoopButton()
    self._ec_motor_visible = SidebarLoopButton()
    self._ec_sff_visible.clicked.connect(
        lambda: self._on_ethercat_visible_clicked("sff")
    )
    self._ec_motor_visible.clicked.connect(
        lambda: self._on_ethercat_visible_clicked("motor")
    )
    ec.addRow("Use EtherCat Stage-FF-Signals:", self._ec_sff_visible)
    ec.addRow("Add EtherCat Output Values:", self._ec_motor_visible)
    middle.addWidget(ethercat)

    ramp = GroupPanel("Ramp Setting")
    ramp.setFixedSize(500, 210)
    ramp_form = QtWidgets.QFormLayout(ramp)
    self.ramp_type_combo = QtWidgets.QComboBox()
    self.ramp_type_combo.addItems(["Actuator Out", "Logical Axes"])
    self.ramp_type_combo.setFixedWidth(205)
    self.ramp_time_edit = SciEdit("1")
    ramp_form.addRow("Ramp Type", self.ramp_type_combo)
    ramp_form.addRow("Ramp Time[Sec.]", self.ramp_time_edit)
    self.ramp_type = QtWidgets.QSpinBox()
    self.ramp_time = SciSpin()
    self.ramp_type.hide()
    self.ramp_time.hide()
    self.ramp_type_combo.currentIndexChanged.connect(self.ramp_type.setValue)
    self.ramp_type_combo.currentIndexChanged.connect(self._on_ramp_changed)
    self.ramp_time_edit.editingFinished.connect(self._on_ramp_changed)
    middle.addWidget(ramp)
    middle.addStretch(1)
    root.addLayout(middle)

    right = QtWidgets.QVBoxLayout()
    switch = GroupPanel("Switch Criterion Setting")
    switch.setFixedSize(500, 510)
    switch_layout = QtWidgets.QVBoxLayout(switch)
    form = QtWidgets.QFormLayout()
    self.sw_signal = FlatPush("X1FB")
    self._populate_system_io_menu(self.sw_signal, "switch")
    self.sw_trig = SciEdit("0")
    self.sw_min = SciEdit("0")
    self.sw_hold = SciEdit("0")
    self.sw_fb = SciEdit("0")
    self.sw_fb.setReadOnly(True)
    for editor in (self.sw_trig, self.sw_min, self.sw_hold):
        editor.editingFinished.connect(self._on_switch_condition_changed)
    self.sw_cond = SciEdit()
    self.sw_cond.hide()
    self.sw_cur = self.sw_fb
    self.sw_trig_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.sw_trig_slider.setRange(0, 100)
    self.sw_trig_slider.hide()
    form.addRow("Signal", self.sw_signal)
    form.addRow("Trigger Level [%]", self.sw_trig)
    form.addRow("Min. Trigger Time [sec.]", self.sw_min)
    form.addRow("Hold Time [sec.]", self.sw_hold)
    form.addRow("Timer [sec.]", self.sw_fb)
    switch_layout.addLayout(form)

    loop_switch = GroupPanel("Loop Switch Setting")
    ls = QtWidgets.QHBoxLayout(loop_switch)
    self.system_switch_lamps = []
    for switch_index, label in enumerate(("Velocity", "Position", "Auto")):
        column = QtWidgets.QVBoxLayout()
        column.addWidget(QtWidgets.QLabel(label), alignment=QtCore.Qt.AlignCenter)
        lamp = SidebarLoopButton()
        lamp.clicked.connect(
            lambda _checked=False, i=switch_index: self._on_visible_switch_clicked(i)
        )
        self.system_switch_lamps.append(lamp)
        column.addWidget(lamp, alignment=QtCore.Qt.AlignCenter)
        led = LedIndicator(28)
        column.addWidget(led, alignment=QtCore.Qt.AlignCenter)
        ls.addLayout(column)
    switch_layout.addWidget(loop_switch)
    right.addWidget(switch)
    right.addStretch(1)
    root.addLayout(right)
    root.addStretch(1)

    # Hidden controls consumed by the extended controller handlers.
    self.loop_opl = QtWidgets.QSpinBox()
    self.loop_opl.setRange(0, 100)
    self.loop_opl.setValue(25)
    self.loop_opl_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.loop_opl_slider.setRange(0, 100)
    self.loop_opl.hide()
    self.loop_opl_slider.hide()
    self._fw_leds = [LedBtn(name, 12) for name in (
        "Vel", "Pos", "Pneu", "FF", "SFF", "FFF", "PFF"
    )]
    self._ec_motor_led = LedBtn("Motor", 12)
    self._ec_sff_led = LedBtn("SFF Signal", 12)
    self._always_v_led = LedBtn("AlwaysV", 12)
    self._always_p_led = LedBtn("AlwaysP", 12)
    self._auto_switch_led = LedBtn("AutoSwitch", 12)
    # Keep the reference-layout spelling as a compatibility alias while all
    # handlers use the canonical name from the original builder.
    self._autoswitch_led = self._auto_switch_led
    self._running_v_led = LedIndicator(10)
    self._running_p_led = LedIndicator(10)
    for widget in (
        *self._fw_leds, self._ec_motor_led, self._ec_sff_led,
        self._always_v_led, self._always_p_led, self._auto_switch_led,
        self._running_v_led, self._running_p_led,
    ):
        widget.hide()
    return w


def _read_system_setting_reference(self) -> None:
    """Refresh all data groups read by the original SystemSettingPage."""
    def work() -> None:
        s = self._require_session()
        loop = s.get_loop_status()

        sample_hz = s.get_sample_frequency()
        self.fs_sample.setText(f"{sample_hz:g}")
        self.fs_manual.setText(f"{sample_hz:g}")
        self.fs_load.setText(f"{s.get_system_load():g}")

        self._updating_system_controls = True
        try:
            self._refresh_system_loop_configuration()
        except Exception as exc:
            self.log_msg(f"Firmware configuration read: {exc}")

        try:
            perf = s.get_performance_monitor()
            perf_offset = 3 if len(perf) >= 6 else 1
            if len(perf) >= 3:
                self._set_system_io_button(
                    self.perf_signal, perf[:3]
                )
            elif perf:
                self.perf_signal.setText(str(perf[0]))
            for editor, index in (
                (self.perf_threshold, perf_offset),
                (self.perf_min_trig, perf_offset + 1),
                (self.perf_hold, perf_offset + 2),
            ):
                if index < len(perf):
                    editor.setText(str(perf[index]))
            status = s.get_performance_status()
            if status:
                self.perf_actual.setText("Perf. Okay" if str(status[0]) == "0" else "Fault")
            if len(status) > 1:
                self.perf_timer.setText(str(status[1]))
        except Exception as exc:
            self.log_msg(f"Performance monitor read: {exc}")

        try:
            switch = s.get_switch_conditions()
            for editor, index in (
                (self.sw_trig, 0), (self.sw_min, 1),
                (self.sw_hold, 2),
            ):
                if index < len(switch):
                    editor.setText(str(switch[index]))
            signal = s.get_switch_signal()
            if len(signal) >= 3:
                self._set_system_io_button(self.sw_signal, signal[:3])
            elif signal:
                self.sw_signal.setText(" ".join(signal))
            self._switch_config = int(switch[3], 0) if len(switch) > 3 else 0
            self._switch_config_loaded = len(switch) > 3
            status = s.get_switch_status()
            status_word = int(status[0], 0) if status else 0
            if len(status) > 1:
                self.sw_fb.setText(str(status[1]))
            for lamp, enabled in zip(
                self.system_switch_lamps,
                (bool(status_word & 0x20), bool(status_word & 0x40), bool(self._switch_config & 0x01)),
            ):
                lamp.set_on(enabled)
        except Exception as exc:
            self.log_msg(f"Switch criterion read: {exc}")

        try:
            ramp = s.get_startup_ramp()
            if ramp:
                self.ramp_type_combo.setCurrentIndex(max(0, min(1, int(ramp[0]))))
            if len(ramp) > 1:
                self.ramp_time_edit.setText(str(ramp[1]))
        except Exception as exc:
            self.log_msg(f"Ramp configuration read: {exc}")

        self._ec_sff_visible.set_on(not bool(loop.system & 0x800))
        self._ec_motor_visible.set_on(bool(loop.system & 0x100))
        self._updating_system_controls = False
        self.log_msg("System Setting page refreshed")

    self._run("Read System Setting", work)


def _refresh_system_loop_configuration(self) -> int:
    """Refresh only the seven NGEXL configuration lamps.

    This lightweight path is safe for the one-second visible-page timer and
    keeps configuration freshness independent from the BGSTS running lamps.
    """
    from python_samba.ui.main_window import _parse_protocol_int

    config = self._require_session().get_controller_config()
    mask = _parse_protocol_int(config[0]) if config else 0
    self._controller_config_mask = mask
    for lamp, bit in zip(
        self.system_loop_lamps,
        (0x01, 0x02, 0x04, 0x10, 0x20, 0x40, 0x80),
    ):
        lamp.set_on(bool(mask & bit))
    return mask


def _populate_system_io_menu(self, button: FlatPush, mode: str) -> None:
    """Attach a compact IOSignal selector while preserving the button layout."""
    menu = QtWidgets.QMenu(button)
    button.setMenu(menu)
    button.setProperty("system_io_mode", mode)
    button.setProperty("io_tokens", (0, 0, 0))
    self._rebuild_system_io_menu(button)
    menu.aboutToShow.connect(lambda b=button: self._rebuild_system_io_menu(b))


def _rebuild_system_io_menu(self, button: FlatPush) -> None:
    """Use the controller's NumInputsSig while keeping pre-connect menus usable."""
    if self.session and self.session.connected:
        self._ensure_controller_capabilities()
    count = max(1, min(
        len(IOSignalButton.INPUT_NAMES),
        int(getattr(self, "_input_signal_count", len(IOSignalButton.INPUT_NAMES))),
    ))
    menu = button.menu()
    if menu is None or int(button.property("system_io_count") or 0) == count:
        return
    menu.clear()
    mode = str(button.property("system_io_mode") or "switch")
    for index, name in enumerate(IOSignalButton.INPUT_NAMES[:count]):
        action = menu.addAction(name)
        action.triggered.connect(
            lambda _checked=False, b=button, m=mode, n=name, i=index:
                self._on_system_io_selected(b, m, n, (0, i, 0))
        )
    button.setProperty("system_io_count", count)


def _on_system_io_selected(
    self, button: FlatPush, mode: str, name: str, tokens: tuple[int, int, int]
) -> None:
    button.setText(name)
    button.setProperty("io_tokens", tokens)
    if mode == "performance":
        self._on_perf_signal_changed()
    else:
        self._on_switch_signal_changed()


def _set_system_io_button(self, button: FlatPush, tokens) -> None:
    values = tuple(int(token) for token in tokens[:3])
    button.setProperty("io_tokens", values)
    button.setText(IOSignalButton.format_io_signal(values))


def _system_io_tokens(self, button: FlatPush) -> tuple[int, int, int]:
    value = button.property("io_tokens")
    if value is None:
        return (0, 0, 0)
    return tuple(int(token) for token in value[:3])


def _on_system_loop_lamp_clicked(self, index: int) -> None:
    """Toggle one NSEXL controller-configuration bit."""
    if getattr(self, "_updating_system_controls", False):
        return
    bits = (0x01, 0x02, 0x04, 0x10, 0x20, 0x40, 0x80)
    mask = int(getattr(self, "_controller_config_mask", 0)) ^ bits[index]

    def work() -> None:
        s = self._require_session()
        if not self._confirm_write(f"NSEXL controller configuration = 0x{mask:X}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_controller_config(mask)
        finally:
            self._set_writable(True)
        self._controller_config_mask = mask
        self.system_loop_lamps[index].set_on(bool(mask & bits[index]))
        self.log_msg(f"Controller configuration set to 0x{mask:X}")

    self._run("Set controller configuration", work)


def _on_ethercat_visible_clicked(self, kind: str) -> None:
    """Update the corresponding system-loop word bit."""
    if getattr(self, "_updating_system_controls", False):
        return

    def work() -> None:
        s = self._require_session()
        loop = s.get_loop_status()
        bit = 0x800 if kind == "sff" else 0x100
        system = loop.system ^ bit
        if not self._confirm_write(f"BSSTS system status = 0x{system:X}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_loop_status(loop.individual, system)
        finally:
            self._set_writable(True)
        self._ec_sff_visible.set_on(not bool(system & 0x800))
        self._ec_motor_visible.set_on(bool(system & 0x100))

    self._run("Set EtherCat loop status", work)


def _on_visible_switch_clicked(self, index: int) -> None:
    """Toggle RunningV/RunningP/AutoSwitch bits in SwitchConfig."""
    if getattr(self, "_updating_system_controls", False):
        return
    bit = (0x20, 0x40, 0x01)[index]
    self._send_switch_config(int(getattr(self, "_switch_config", 0)) ^ bit)


def _on_switch_condition_changed(self) -> None:
    if getattr(self, "_updating_system_controls", False):
        return
    self._send_switch_config(int(getattr(self, "_switch_config", 0)))


def _on_ramp_changed(self, *_args) -> None:
    if getattr(self, "_updating_system_controls", False):
        return
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        ramp_type = self.ramp_type_combo.currentIndex()
        ramp_time = float(self.ramp_time_edit.text())
        if not self._confirm_write(
            f"BSSUT ramp type={ramp_type}, time={ramp_time:g} sec"
        ):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_startup_ramp(ramp_type, ramp_time)
        finally:
            self._set_writable(True)
        self.log_msg(f"Startup ramp set: type={ramp_type}, time={ramp_time:g}")

    self._run("Set startup ramp", work)


def _on_fw_led_toggled(self, _state: bool) -> None:
    """One of the 7 firmware-config LED buttons was toggled.

    Sends the full firmware configuration mask to the controller
    (mirrors C# ``FirmwareConfig_PropertyChanged`` → ``SetFirmwareConfiguration``).
    """
    if getattr(self, "_updating_system_controls", False):
        return
    self._on_firmware_config_click()


def _get_fw_mask(self) -> int:
    """Build the firmware configuration bitmask from the 7 LED states."""
    mask = 0
    if self._fw_leds[0].is_on():   # Vel
        mask |= 0x01
    if self._fw_leds[1].is_on():   # Pos
        mask |= 0x02
    if self._fw_leds[2].is_on():   # Pneu
        mask |= 0x04
    if self._fw_leds[3].is_on():   # FF
        mask |= 0x10
    if self._fw_leds[4].is_on():   # SFF
        mask |= 0x20
    if self._fw_leds[5].is_on():   # FFF
        mask |= 0x40
    if self._fw_leds[6].is_on():   # PFF
        mask |= 0x80
    return mask


def _on_firmware_config_click(self, *_args) -> None:
    """'Set Firmware Config' button clicked — push mask to controller."""
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        mask = self._get_fw_mask()
        if not self._confirm_write(f"NSEXL controller configuration = 0x{mask:X}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_controller_config(mask)
        finally:
            self._set_writable(True)
        self._controller_config_mask = mask
        self.log_msg(f"Controller configuration set to 0x{mask:X}")

    self._run("Set controller configuration", work)


def _on_ethercat_led_clicked(self, _state: bool) -> None:
    """EtherCat Motor or SFF Signal LED clicked — send SetSystemLoopStatus.

    Mirrors C# ``LoopStatusEtherCatSFFSigBit_PropertyChanged`` /
    ``LoopStatusEtherCatMotorBit_PropertyChanged``.
    """
    if getattr(self, "_updating_system_controls", False):
        return
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        loop = s.get_loop_status()
        system = loop.system
        system = (
            system | 0x100 if self._ec_motor_led.is_on() else system & ~0x100
        )
        # The EtherCAT SFF signal flag is active-low in LoopStatus.
        system = (
            system & ~0x800 if self._ec_sff_led.is_on() else system | 0x800
        )
        if not self._confirm_write(f"BSSTS system status = 0x{system:X}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_loop_status(loop.individual, system)
        finally:
            self._set_writable(True)
        self.log_msg(f"System loop status set to 0x{system:X}")

    self._run("Set EtherCAT loop status", work)


def _on_always_btn_clicked(self, _state: bool) -> None:
    """AlwaysV or AlwaysP LED toggled — update SwitchConfig and send.

    Mirrors C# ``AlwaysBtn_Click``:
      Both True  → SwitchConfig = 3
      Both False → SwitchConfig = 0
      V=False, P=True → SwitchConfig = 2
      V=True,  P=False → SwitchConfig = 1
    """
    always_v = self._always_v_led.is_on()
    always_p = self._always_p_led.is_on()

    if always_v and always_p:
        config = 3
    elif not always_v and not always_p:
        config = 0
    elif not always_v and always_p:
        config = 2
    else:  # always_v and not always_p
        config = 1

    self._auto_switch_led.set_state(False)  # AutoSwitch off when manual
    self._send_switch_config(config)


def _on_auto_switch_clicked(self, state: bool) -> None:
    """AutoSwitch LED toggled — SwitchConfig = 1 if on, else 0.

    Mirrors C# ``AutoSwitchBtn_Click``.
    """
    config = 1 if state else 0
    self._always_v_led.set_state(False)
    self._always_p_led.set_state(False)
    self._send_switch_config(config)


def _send_switch_config(self, config: int) -> None:
    """Send SwitchConfig to controller via set_switch_conditions.

    Mirrors C# ``SetSwitchCondition``.  Sends current trigger-level,
    min-trigger-time, hold-time values together with the new config.
    """
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        trig = int(float(self.sw_trig.text()))
        min_t = float(self.sw_min.text())
        hold = float(self.sw_hold.text())
        if not self._confirm_write(
            f"BSOCD trigger={trig}, min={min_t:g}, hold={hold:g}, config=0x{config:X}"
        ):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_switch_conditions(trig, min_t, hold, config)
        finally:
            self._set_writable(True)
        self._switch_config = config
        self._switch_config_loaded = True
        self.log_msg(f"SetSwitchCondition config=0x{config:X}")

    self._run("Set switch condition", work)


def _on_switch_signal_changed(self) -> None:
    """Switch IOSignal changed — send to controller.

    Mirrors C# ``SwitchIOSignal_OnIOSignalChanged`` → ``SetSwitchSignal``.
    """
    if getattr(self, "_updating_system_controls", False):
        return
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        tokens = self._system_io_tokens(self.sw_signal)
        if not self._confirm_write(f"BSSWS IOSignal={tokens}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_switch_signal(*tokens)
        finally:
            self._set_writable(True)
        self.log_msg("SetSwitchSignal")

    self._run("Set switch signal", work)


def _on_perf_signal_changed(self) -> None:
    """Performance IOSignal changed — send to controller.

    Mirrors C# ``PerfIOSignal_OnIOSignalChanged`` → ``SetPerformanceMonitorConfig``.
    """
    if getattr(self, "_updating_system_controls", False):
        return
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        tokens = self._system_io_tokens(self.perf_signal)
        threshold = int(float(self.perf_threshold.text()))
        minimum = float(self.perf_min_trig.text())
        hold = float(self.perf_hold.text())
        if not self._confirm_write(
            f"DSPMV IOSignal={tokens}, threshold={threshold}, min={minimum:g}, hold={hold:g}"
        ):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_performance_monitor(*tokens, threshold, minimum, hold)
        finally:
            self._set_writable(True)
        self.log_msg("SetPerformanceMonitorConfig")

    self._run("Set performance monitor", work)


def _on_sample_frequency_set(self) -> None:
    """Set button for sample frequency — with warning for higher values.

    Mirrors C# ``SetSampleFrequencyBtn_Click``:
      - If new == current → skip
      - If new > current  → show warning dialog
        - Yes → apply
        - No  → revert text
      - If new < current  → apply directly
    """
    s = getattr(self, "session", None)
    if not s or not s.connected:
        return

    try:
        new_val = float(self.fs_manual.text().strip())
    except ValueError:
        QtWidgets.QMessageBox.warning(
            self, "Invalid", "Enter a valid numeric frequency."
        )
        return

    try:
        current_val = float(self.fs_sample.text().strip())
    except ValueError:
        current_val = 0.0

    # If no change — skip
    if abs(new_val - current_val) < 1e-9:
        return

    if new_val > current_val:
        # Dangerous — confirm
        answer = QtWidgets.QMessageBox.warning(
            self,
            "SAMBA19xUI",
            "\nSetting the sample frequency too high may crash the firmware.\n"
            "Press Yes to continue or No to cancel.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            # Revert the manual field to the current value
            self.fs_manual.setText(self.fs_sample.text())
            return

    # Apply
    try:
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        s.set_sample_frequency(new_val)
        self.fs_sample.setText(f"{new_val}")
        self.log_msg(f"SampleFrequency set to {new_val} Hz")
    except Exception as exc:
        self.log_msg(f"ERROR SetSampleFrequency: {exc}")
    finally:
        self._set_writable(True)


# ===================================================================
# Patch installer
# ===================================================================

def patch_system_setting(instance) -> None:
    """Patch a MainWindow instance with the enhanced system-setting page.

    Call this after the window is constructed but before the UI is shown::

        window = MainWindow()
        patch_system_setting(window)
        window.show()
    """
    # Replace the method on the instance
    bound = build_system_setting_page.__get__(instance, type(instance))
    setattr(instance, "_build_system_setting_page", bound)

    # Attach handler methods
    for name in (
        "_on_fw_led_toggled",
        "_get_fw_mask",
        "_on_firmware_config_click",
        "_on_ethercat_led_clicked",
        "_on_always_btn_clicked",
        "_on_auto_switch_clicked",
        "_send_switch_config",
        "_on_switch_signal_changed",
        "_on_perf_signal_changed",
        "_on_sample_frequency_set",
        "_populate_system_io_menu",
        "_rebuild_system_io_menu",
        "_on_system_io_selected",
        "_set_system_io_button",
        "_system_io_tokens",
        "_on_system_loop_lamp_clicked",
        "_on_ethercat_visible_clicked",
        "_on_visible_switch_clicked",
        "_on_switch_condition_changed",
        "_on_ramp_changed",
        "_refresh_system_loop_configuration",
    ):
        if not hasattr(instance, name):
            fn = globals()[name]
            setattr(instance, name, fn.__get__(instance, type(instance)))


def apply_patches(cls: type) -> None:
    """Install the feature-complete System Setting page on a window class."""
    cls._build_system_setting_page = build_system_setting_reference
    for name in (
        "_on_fw_led_toggled",
        "_get_fw_mask",
        "_on_firmware_config_click",
        "_on_ethercat_led_clicked",
        "_on_always_btn_clicked",
        "_on_auto_switch_clicked",
        "_send_switch_config",
        "_on_switch_signal_changed",
        "_on_perf_signal_changed",
        "_on_sample_frequency_set",
        "_populate_system_io_menu",
        "_rebuild_system_io_menu",
        "_on_system_io_selected",
        "_set_system_io_button",
        "_system_io_tokens",
        "_on_system_loop_lamp_clicked",
        "_on_ethercat_visible_clicked",
        "_on_visible_switch_clicked",
        "_on_switch_condition_changed",
        "_on_ramp_changed",
        "_read_system_setting_reference",
        "_refresh_system_loop_configuration",
    ):
        setattr(cls, name, globals()[name])
