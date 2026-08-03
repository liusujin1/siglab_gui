"""Unified patch for the screenshot-oriented Special workspace.

Signal Display and DigIO belong to the Status workspace in the supplied
SAMBA19xUI reference.  Their builders remain here for reuse, while the visible
Special tabs are Safety, System Safety and Polynomials.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    ClassicFilterPanel,
    FilterStageBar,
    FilterStageCell,
    FlatPush,
    GroupPanel,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
)
from python_samba.ui.main_window import PNEU_AXES_NAMES, POS_AXES_NAMES, VEL_AXES_NAMES


def _build_special_tab(self) -> None:
    """Build the three Special tabs shown in the supplied reference UI."""
    from python_samba.ui.main_window import SamTabWidget

    tabs = SamTabWidget()
    self.special_tabs = tabs

    # 1. Safety tab
    tabs.addTab(_build_safety_tab_reference(self), "Safety")

    # 2. System safety / ZMS tab
    tabs.addTab(_build_zms_tab(self), "System Safety")

    # 3. Polynomial compensation tab
    # The dedicated polynom patch provides the complete LGPCP/LSPCP and
    # LGPSP/LSPSP implementation.  Calling through the instance preserves
    # that functional builder instead of replacing it with the old visual
    # placeholder from this module.
    tabs.addTab(self._build_polynom_tab(), "Polynomials")

    self.main_tabs.addTab(tabs, "Special")


# =========================================================================
# Safety tab
# =========================================================================
def _build_safety_tab(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # Config
    g_cfg = GroupPanel("Safety & Earthquake Configuration")
    cf = QtWidgets.QFormLayout(g_cfg)
    self.safety_geo_upper = SciEdit("1.00000e+000")
    self.safety_geo_lower = SciEdit("1.00000e-001")
    self.safety_prox_upper = SciEdit("1.00000e+000")
    self.safety_prox_lower = SciEdit("1.00000e-001")
    self.safety_rms_window = SciEdit("1.00000e+000")
    self.safety_eq_geo_limit = SciEdit("1.00000e+000")
    self.safety_eq_prox_limit = SciEdit("1.00000e+000")
    self.safety_eq_rms_window = SciEdit("1.00000e+000")
    cf.addRow("Geo upper limit:", self.safety_geo_upper)
    cf.addRow("Geo lower limit:", self.safety_geo_lower)
    cf.addRow("Prox upper limit:", self.safety_prox_upper)
    cf.addRow("Prox lower limit:", self.safety_prox_lower)
    cf.addRow("RMS time window:", self.safety_rms_window)
    cf.addRow("EQ geo limit:", self.safety_eq_geo_limit)
    cf.addRow("EQ prox limit:", self.safety_eq_prox_limit)
    cf.addRow("EQ RMS window:", self.safety_eq_rms_window)
    root.addWidget(g_cfg)

    # Status LEDs
    g_led = GroupPanel("Status")
    led_row = QtWidgets.QHBoxLayout(g_led)
    self.safety_led_geo = LedIndicator(10)
    self.safety_led_prox = LedIndicator(10)
    self.safety_led_eq = LedIndicator(10)
    for led, label in (
        (self.safety_led_geo, "Safety Vibration"),
        (self.safety_led_prox, "Safety Position"),
        (self.safety_led_eq, "Earthquake"),
    ):
        col = QtWidgets.QVBoxLayout()
        col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
        col.addWidget(QtWidgets.QLabel(label), 0, QtCore.Qt.AlignHCenter)
        led_row.addLayout(col)
    led_row.addStretch(1)
    root.addWidget(g_led)

    # Safety RMS values
    g_rms = GroupPanel("Safety RMS Values")
    rms_grid = QtWidgets.QGridLayout(g_rms)
    self.safety_rms_labels = {}
    geo_names = ["SY1", "SZ1", "SX2", "SZ2", "SY3", "SZ3"]
    prox_names = ["SProx1", "SProx2", "SProx3", "SProxH1", "SProxH2", "SProxH3"]
    rms_grid.addWidget(QtWidgets.QLabel("Geophone"), 0, 0, 1, 3)
    rms_grid.addWidget(QtWidgets.QLabel("Proximity"), 0, 3, 1, 3)
    for i, name in enumerate(geo_names):
        lbl = QtWidgets.QLabel("---")
        lbl.setStyleSheet("background:#e8e8e8; padding:2px 6px; border:1px solid #c0c0c0;")
        self.safety_rms_labels[name] = lbl
        rms_grid.addWidget(QtWidgets.QLabel(name + ":"), i + 1, 0)
        rms_grid.addWidget(lbl, i + 1, 1)
    for i, name in enumerate(prox_names):
        lbl = QtWidgets.QLabel("---")
        lbl.setStyleSheet("background:#e8e8e8; padding:2px 6px; border:1px solid #c0c0c0;")
        self.safety_rms_labels[name] = lbl
        rms_grid.addWidget(QtWidgets.QLabel(name + ":"), i + 1, 3)
        rms_grid.addWidget(lbl, i + 1, 4)
    root.addWidget(g_rms)

    # Earthquake RMS values
    g_eq = GroupPanel("Earthquake RMS Values")
    eq_grid = QtWidgets.QGridLayout(g_eq)
    self.safety_eq_labels = {}
    eq_geo_names = ["EQY1", "EQZ1", "EQX2", "EQZ2", "EQY3", "EQZ3"]
    eq_prox_names = ["EQProx1", "EQProx2", "EQProx3", "EQProxH1", "EQProxH2", "EQProxH3"]
    eq_grid.addWidget(QtWidgets.QLabel("Geophone"), 0, 0, 1, 3)
    eq_grid.addWidget(QtWidgets.QLabel("Proximity"), 0, 3, 1, 3)
    for i, name in enumerate(eq_geo_names):
        lbl = QtWidgets.QLabel("---")
        lbl.setStyleSheet("background:#e8e8e8; padding:2px 6px; border:1px solid #c0c0c0;")
        self.safety_eq_labels[name] = lbl
        eq_grid.addWidget(QtWidgets.QLabel(name + ":"), i + 1, 0)
        eq_grid.addWidget(lbl, i + 1, 1)
    for i, name in enumerate(eq_prox_names):
        lbl = QtWidgets.QLabel("---")
        lbl.setStyleSheet("background:#e8e8e8; padding:2px 6px; border:1px solid #c0c0c0;")
        self.safety_eq_labels[name] = lbl
        eq_grid.addWidget(QtWidgets.QLabel(name + ":"), i + 1, 3)
        eq_grid.addWidget(lbl, i + 1, 4)
    root.addWidget(g_eq)

    # Amplifier events
    g_amp = GroupPanel("Amplifier Events")
    amp_grid = QtWidgets.QGridLayout(g_amp)
    self.safety_amp_leds = []
    for i in range(12):
        led = LedIndicator(10)
        self.safety_amp_leds.append(led)
        amp_grid.addWidget(led, i // 4, (i % 4) * 2)
        amp_grid.addWidget(QtWidgets.QLabel(f"Motor {i+1}"), i // 4, (i % 4) * 2 + 1)
    root.addWidget(g_amp)

    # Buttons
    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_cfg = FlatPush("Read config")
    btn_w = FlatPush("Write config...")
    btn_r.clicked.connect(self.on_safety_read)
    btn_cfg.clicked.connect(self.on_safety_read_config)
    btn_w.clicked.connect(self.on_safety_write_config)
    act.addWidget(btn_r)
    act.addWidget(btn_cfg)
    act.addWidget(btn_w)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)
    return w


# =========================================================================
# Screenshot-oriented Safety tab
# =========================================================================

class _SafetyPill(QtWidgets.QLabel):
    """Reference-style status pill with the LedIndicator compatibility API."""

    def __init__(self, ok_text: str = "Okay", fault_text: str = "Fault") -> None:
        super().__init__(ok_text)
        self._ok_text = ok_text
        self._fault_text = fault_text
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setFixedSize(145, 62)
        self.set_color("green")

    def set_color(self, color: str) -> None:
        ok = str(color).lower() not in {"red", "#ef4444", "#ff0000", "fault"}
        self.setText(self._ok_text if ok else self._fault_text)
        colors = ("#eaff7a", "#80e315", "#d9ffd0") if ok else (
            "#ffb0a8", "#ef4444", "#8d1010"
        )
        self.setStyleSheet(
            "background:qradialgradient(cx:.5,cy:.5,radius:.6,"
            f"stop:0 {colors[0]},stop:.55 {colors[1]},stop:1 {colors[2]});"
            "border-radius:30px;font-size:22px;font-weight:700;"
        )

    def set_on(self, on: bool, _color: str | None = None) -> None:
        self.set_color("green" if on else "red")

def _build_safety_tab_reference(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QHBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(12)

    left = QtWidgets.QVBoxLayout()
    safety_status = GroupPanel("Safety Status")
    safety_status.setFixedSize(435, 155)
    status_row = QtWidgets.QHBoxLayout(safety_status)
    status_pills = []
    for title in ("Vibration", "Position"):
        column = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(title)
        label.setAlignment(QtCore.Qt.AlignCenter)
        value = _SafetyPill()
        status_pills.append(value)
        column.addWidget(label)
        column.addWidget(value)
        status_row.addLayout(column)
    left.addWidget(safety_status)

    setting = GroupPanel("Safety Setting")
    setting.setFixedSize(435, 140)
    form = QtWidgets.QFormLayout(setting)
    self.safety_geo_upper = SciEdit("1000")
    self.safety_prox_upper = SciEdit("50")
    self.safety_rms_window = SciEdit("1")
    form.addRow("Geophone Limit:", self.safety_geo_upper)
    form.addRow("Proximity Limit:", self.safety_prox_upper)
    form.addRow("RMS Time Window:", self.safety_rms_window)
    left.addWidget(setting)
    left.addStretch(1)
    root.addLayout(left)

    middle = QtWidgets.QVBoxLayout()
    earthquake_status = GroupPanel("Earth Quake Status")
    earthquake_status.setFixedSize(435, 155)
    eq_layout = QtWidgets.QVBoxLayout(earthquake_status)
    eq_title = QtWidgets.QLabel("Earth quake")
    eq_title.setAlignment(QtCore.Qt.AlignCenter)
    eq_value = _SafetyPill("NO", "YES")
    eq_layout.addWidget(eq_title)
    eq_layout.addWidget(eq_value, alignment=QtCore.Qt.AlignHCenter)
    middle.addWidget(earthquake_status)

    earthquake_setting = GroupPanel("Earth Quake Setting")
    earthquake_setting.setFixedSize(435, 110)
    eq_form = QtWidgets.QFormLayout(earthquake_setting)
    self.safety_eq_geo_limit = SciEdit("6000")
    self.safety_eq_rms_window = SciEdit("2")
    eq_form.addRow("Geophone Limit:", self.safety_eq_geo_limit)
    eq_form.addRow("RMS Time Window:", self.safety_eq_rms_window)
    middle.addWidget(earthquake_setting)
    middle.addStretch(1)
    root.addLayout(middle)

    rms = GroupPanel("RMS Values")
    rms.setFixedSize(660, 640)
    values = QtWidgets.QGridLayout(rms)
    values.setContentsMargins(12, 18, 12, 12)
    values.setHorizontalSpacing(8)
    values.setVerticalSpacing(4)
    values.addWidget(QtWidgets.QLabel("Safety Values"), 0, 0, 1, 2, QtCore.Qt.AlignCenter)
    values.addWidget(QtWidgets.QLabel("Earthquake Values"), 0, 2, 1, 2, QtCore.Qt.AlignCenter)
    self.safety_rms_labels = {}
    safety_names = [
        ("Y1Geoph", "SY1"), ("Z1Geoph", "SZ1"), ("X2Geoph", "SX2"),
        ("Z2Geoph", "SZ2"), ("Y3Geoph", "SY3"), ("Z3Geoph", "SZ3"),
        ("Prox1", "SProx1"), ("Prox2", "SProx2"), ("Prox3", "SProx3"),
        ("ProxH1", "SProxH1"), ("ProxH2", "SProxH2"), ("ProxH3", "SProxH3"),
    ]
    for row, (display, key) in enumerate(safety_names, 1):
        label = QtWidgets.QLabel("0")
        label.setFixedSize(200, 34)
        label.setStyleSheet("background:#59e20a;border:1px solid #999;padding:2px;")
        self.safety_rms_labels[key] = label
        values.addWidget(QtWidgets.QLabel(display), row, 0)
        values.addWidget(label, row, 1)

    self.safety_eq_labels = {}
    eq_names = [("XFFGeoph", "EQY1"), ("YFFGeoph", "EQZ1"), ("ZFFGeoph", "EQX2")]
    for row, (display, key) in enumerate(eq_names, 1):
        label = QtWidgets.QLabel("0")
        label.setFixedSize(170, 34)
        label.setStyleSheet("background:#59e20a;border:1px solid #999;padding:2px;")
        self.safety_eq_labels[key] = label
        values.addWidget(QtWidgets.QLabel(display), row, 2)
        values.addWidget(label, row, 3)
    root.addWidget(rms, alignment=QtCore.Qt.AlignTop)
    root.addStretch(1)

    # Compatibility fields retained for existing safety handlers.
    self.safety_geo_lower = SciEdit("0")
    self.safety_prox_lower = SciEdit("0")
    self.safety_eq_prox_limit = SciEdit("0")
    self.safety_vibration_led = status_pills[0]
    self.safety_position_led = status_pills[1]
    self.eq_status_led = eq_value
    self.safety_led_geo = self.safety_vibration_led
    self.safety_led_prox = self.safety_position_led
    self.safety_led_eq = self.eq_status_led
    for widget in (
        self.safety_geo_lower, self.safety_prox_lower, self.safety_eq_prox_limit,
    ):
        widget.hide()
    for missing_key in ("EQZ2", "EQY3", "EQZ3", "EQProx1", "EQProx2", "EQProx3", "EQProxH1", "EQProxH2", "EQProxH3"):
        hidden = QtWidgets.QLabel("0")
        hidden.hide()
        self.safety_eq_labels[missing_key] = hidden
    self.safety_amp_leds = [LedIndicator(10) for _ in range(12)]
    for led in self.safety_amp_leds:
        led.hide()

    # Attribute names used by the source-aligned handlers.
    self.safety_geo_upper_limit = self.safety_geo_upper
    self.safety_geo_lower_limit = self.safety_geo_lower
    self.safety_prox_upper_limit = self.safety_prox_upper
    self.safety_prox_lower_limit = self.safety_prox_lower
    self.safety_rms_time_window = self.safety_rms_window
    self.eq_geo_limit = self.safety_eq_geo_limit
    self.eq_prox_limit = self.safety_eq_prox_limit
    self.eq_rms_time_window = self.safety_eq_rms_window
    safety_order = (
        "SY1", "SZ1", "SX2", "SZ2", "SY3", "SZ3",
        "SProx1", "SProx2", "SProx3", "SProxH1", "SProxH2", "SProxH3",
    )
    eq_order = (
        "EQY1", "EQZ1", "EQX2", "EQZ2", "EQY3", "EQZ3",
        "EQProx1", "EQProx2", "EQProx3", "EQProxH1", "EQProxH2", "EQProxH3",
    )
    self.safety_rms_geo = [self.safety_rms_labels[key] for key in safety_order[:6]]
    self.safety_rms_prox = [self.safety_rms_labels[key] for key in safety_order[6:]]
    self.eq_rms_geo = [self.safety_eq_labels[key] for key in eq_order[:6]]
    self.eq_rms_prox = [self.safety_eq_labels[key] for key in eq_order[6:]]

    # The original WPF page writes configuration when a field loses focus.
    for editor in (
        self.safety_geo_upper, self.safety_prox_upper, self.safety_rms_window,
        self.safety_eq_geo_limit, self.safety_eq_rms_window,
    ):
        editor.editingFinished.connect(self.on_safety_write_config)
    return w


# =========================================================================
# ZMS tab
# =========================================================================
def _build_zms_tab(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # Status LEDs
    g_led = GroupPanel("ZMS Status")
    led_row = QtWidgets.QHBoxLayout(g_led)
    self.zms_led_vib = LedIndicator(10)
    self.zms_led_pos = LedIndicator(10)
    led_row.addWidget(self.zms_led_vib, 0, QtCore.Qt.AlignHCenter)
    led_row.addWidget(QtWidgets.QLabel("Vibration Status"), 0, QtCore.Qt.AlignHCenter)
    led_row.addSpacing(20)
    led_row.addWidget(self.zms_led_pos, 0, QtCore.Qt.AlignHCenter)
    led_row.addWidget(QtWidgets.QLabel("Position Status"), 0, QtCore.Qt.AlignHCenter)
    led_row.addStretch(1)
    root.addWidget(g_led)

    # Last failed event
    g_fail = GroupPanel("Last Failed Event")
    fail_grid = QtWidgets.QFormLayout(g_fail)
    self.zms_failed_axis = QtWidgets.QLabel("---")
    self.zms_failed_rms = QtWidgets.QLabel("---")
    fail_grid.addRow("Failed axis:", self.zms_failed_axis)
    fail_grid.addRow("Failed RMS:", self.zms_failed_rms)
    root.addWidget(g_fail)

    # Velocity thresholds + RMS
    g_vel = GroupPanel("Velocity Thresholds & RMS")
    vel_grid = QtWidgets.QGridLayout(g_vel)
    vel_grid.addWidget(QtWidgets.QLabel("Axis"), 0, 0)
    vel_grid.addWidget(QtWidgets.QLabel("Threshold"), 0, 1)
    vel_grid.addWidget(QtWidgets.QLabel("Actual RMS"), 0, 2)
    self.zms_vel_thr = []
    self.zms_vel_rms = []
    for i, name in enumerate(VEL_AXES_NAMES[:6]):
        ed = SciEdit("1.00000e+000")
        ed.setFixedWidth(100)
        self.zms_vel_thr.append(ed)
        lbl = QtWidgets.QLabel("---")
        self.zms_vel_rms.append(lbl)
        vel_grid.addWidget(QtWidgets.QLabel(name), i + 1, 0)
        vel_grid.addWidget(ed, i + 1, 1)
        vel_grid.addWidget(lbl, i + 1, 2)
    root.addWidget(g_vel)

    # Position thresholds + RMS
    g_pos = GroupPanel("Position Thresholds & RMS")
    pos_grid = QtWidgets.QGridLayout(g_pos)
    pos_grid.addWidget(QtWidgets.QLabel("Axis"), 0, 0)
    pos_grid.addWidget(QtWidgets.QLabel("Threshold"), 0, 1)
    pos_grid.addWidget(QtWidgets.QLabel("Actual RMS"), 0, 2)
    self.zms_pos_thr = []
    self.zms_pos_rms = []
    for i, name in enumerate(POS_AXES_NAMES[:6]):
        ed = SciEdit("1.00000e+000")
        ed.setFixedWidth(100)
        self.zms_pos_thr.append(ed)
        lbl = QtWidgets.QLabel("---")
        self.zms_pos_rms.append(lbl)
        pos_grid.addWidget(QtWidgets.QLabel(name), i + 1, 0)
        pos_grid.addWidget(ed, i + 1, 1)
        pos_grid.addWidget(lbl, i + 1, 2)
    root.addWidget(g_pos)

    # Buttons
    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_w = FlatPush("Write thresholds...")
    btn_r.clicked.connect(self.on_zms_read)
    btn_w.clicked.connect(self.on_zms_write)
    act.addWidget(btn_r)
    act.addWidget(btn_w)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)

    # Compatibility names used by the complete safety_zms handlers.
    self.zms_vibration_led = self.zms_led_vib
    self.zms_position_led = self.zms_led_pos
    self.zms_vel_thresholds = self.zms_vel_thr
    self.zms_pos_thresholds = self.zms_pos_thr
    self.zms_vel_values = self.zms_vel_rms
    self.zms_pos_values = self.zms_pos_rms
    return w


# =========================================================================
# Polynom tab
# =========================================================================
def _build_polynom_tab(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    g_poly = GroupPanel("Polynom Configuration")
    form = QtWidgets.QFormLayout(g_poly)
    self.poly_num = QtWidgets.QComboBox()
    self.poly_num.addItems([f"Polynom {i+1}" for i in range(19)])
    self.poly_type = QtWidgets.QComboBox()
    self.poly_type.addItems(["Input", "Output"])
    self.poly_active = RockerButton("On", "Off")
    self.poly_coeffs = []
    for i in range(5):
        ed = SciEdit("0.00000e+000")
        self.poly_coeffs.append(ed)
    form.addRow("Polynom:", self.poly_num)
    form.addRow("Type:", self.poly_type)
    form.addRow("Active:", self.poly_active)
    for i in range(5):
        form.addRow(f"Coeff {i+1}:", self.poly_coeffs[i])

    self.poly_limiter = SciEdit("0.00000e+000")
    form.addRow("Limiter:", self.poly_limiter)

    self.poly_input_sig = QtWidgets.QComboBox()
    self.poly_output_sig = QtWidgets.QComboBox()
    self.poly_input_sig.addItem("None", (0, 0, 0))
    self.poly_output_sig.addItem("None", (0, 0, 0))
    for index, name in enumerate((
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
        "Prox1", "Prox2", "Prox3", "Xpos", "Xacc", "Ypos", "Yacc",
    )):
        self.poly_input_sig.addItem(name, (1, index, 0))
        self.poly_output_sig.addItem(name, (2, index, 0))
    form.addRow("Input signal:", self.poly_input_sig)
    form.addRow("Output signal:", self.poly_output_sig)
    root.addWidget(g_poly)

    # Source-compatible state controls used by LGPSP/LGPIV/LGPOV and ramp
    # handlers.  They remain hidden because the reference page only exposes
    # the per-polynom editor.
    self.poly_active_led = self.poly_active
    self.poly_processing_led = QtWidgets.QPushButton("Processing")
    self.poly_overall_active_led = QtWidgets.QPushButton("Active")
    self.poly_input_val = QtWidgets.QLineEdit("0")
    self.poly_output_val = QtWidgets.QLineEdit("0")
    self.poly_ramp_led = QtWidgets.QPushButton("Ramp")
    self.poly_ramp_start = SciEdit("0")
    self.poly_ramp_end = SciEdit("0")
    self.poly_ramp_time = SciEdit("0")
    for widget in (
        self.poly_processing_led, self.poly_overall_active_led,
        self.poly_input_val, self.poly_output_val, self.poly_ramp_led,
        self.poly_ramp_start, self.poly_ramp_end, self.poly_ramp_time,
    ):
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.setCheckable(True)
        widget.hide()

    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read", self.on_poly_read),
        ("Write...", self.on_poly_write),
        ("Read All", self.on_poly_read_all),
        ("Write All...", self.on_poly_write_all),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)
    return w


# =========================================================================
# Signal Display tab
# =========================================================================
def _build_signal_display_page(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    g = GroupPanel("Signal Continue Display")
    form = QtWidgets.QFormLayout(g)

    # 16 signal selectors
    self.sig_selectors = []
    for i in range(16):
        cb = QtWidgets.QComboBox()
        cb.addItems([f"Signal {j}" for j in range(32)])
        self.sig_selectors.append(cb)
        form.addRow(f"Ch {i+1}:", cb)

    # Value display
    self.sig_values = []
    val_row = QtWidgets.QHBoxLayout()
    for i in range(8):
        v = QtWidgets.QLabel("---")
        v.setStyleSheet("background:#fff; border:1px solid #808080; padding:2px 4px; min-width:70px;")
        self.sig_values.append(v)
        val_row.addWidget(v)
    val_row.addStretch(1)
    form.addRow("Values:", val_row)

    root.addWidget(g)

    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read")
    btn_mon = FlatPush("Start Monitoring")
    btn_mon.clicked.connect(lambda: self._toggle_signal_monitoring())
    btn_save = FlatPush("Save settings")
    btn_load = FlatPush("Load settings")
    btn_r.clicked.connect(self.on_signal_continue_read)
    btn_save.clicked.connect(self.on_sig_save_settings)
    btn_load.clicked.connect(self.on_sig_load_settings)
    act.addWidget(btn_r)
    act.addWidget(btn_mon)
    act.addWidget(btn_save)
    act.addWidget(btn_load)
    act.addStretch(1)
    root.addLayout(act)
    self._sig_monitoring = False
    self._sig_mon_btn = btn_mon
    root.addStretch(1)
    return w


# =========================================================================
# DigIO Status tab
# =========================================================================
def _build_digio_tab(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    g = GroupPanel("Digital IO Status")
    grid = QtWidgets.QGridLayout(g)
    grid.setSpacing(6)

    # Position individual loop status
    grid.addWidget(QtWidgets.QLabel("Position Individual Loop Status"), 0, 0, 1, 6)
    self._digio_pos_leds = []
    for i, name in enumerate(POS_AXES_NAMES):
        led = LedIndicator(10)
        self._digio_pos_leds.append(led)
        r, c = divmod(i, 4)
        grid.addWidget(led, r + 1, c * 2)
        grid.addWidget(QtWidgets.QLabel(name), r + 1, c * 2 + 1)

    # Pneumatic individual loop status
    grid.addWidget(QtWidgets.QLabel("Pneumatic Individual Loop Status"), 4, 0, 1, 6)
    self._digio_pneu_leds = []
    for i, name in enumerate(PNEU_AXES_NAMES[:3]):
        led = LedIndicator(10)
        self._digio_pneu_leds.append(led)
        grid.addWidget(led, 5, i * 2)
        grid.addWidget(QtWidgets.QLabel(name), 5, i * 2 + 1)

    root.addWidget(g)

    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_r.clicked.connect(self._on_digio_read)
    act.addWidget(btn_r)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)
    return w


def _on_digio_read(self) -> None:
    if not self.session or not self.session.connected:
        return
    try:
        _position, _pneumatic, input_word, output_word = (
            self.session.get_pos_pneum_digital_status()
        )
        for index, led in enumerate(getattr(self, '_digio_input_leds', [])):
            led.set_on(bool(input_word & (1 << index)))
        for index, led in enumerate(getattr(self, '_digio_output_leds', [])):
            led.set_on(bool(output_word & (1 << index)))
        self.log_msg(
            f"DigIO status read input=0x{input_word:X} output=0x{output_word:X}"
        )
    except Exception as exc:
        self.log_msg(f"ERROR DigIO status read: {exc}")


# =========================================================================
# View3D tab
# =========================================================================
def _build_view3d_tab(self) -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    has_3d = False
    try:
        import pyqtgraph.opengl as gl
        has_3d = True
    except ImportError:
        pass

    if has_3d:
        try:
            from pyqtgraph.opengl import GLViewWidget
            gl_view = GLViewWidget()
            root.addWidget(gl_view, 1)
            g = gl.GLGridItem()
            gl_view.addItem(g)
        except Exception:
            has_3d = False

    if not has_3d:
        msg = QtWidgets.QLabel(
            "3D View\n\nTo enable 3D, install pyqtgraph:\n  pip install pyqtgraph\n\n"
            "The original SAMBA19xUI loads '0002507.stl' using\nHelixToolkit.Wpf.\n\n"
            "This placeholder provides the same tab structure."
        )
        msg.setWordWrap(True)
        msg.setAlignment(QtCore.Qt.AlignCenter)
        msg.setStyleSheet("color:#505050; font-size:13px; padding:40px;")
        root.addWidget(msg, 1)

    g_rot = GroupPanel("Rotation")
    rot = QtWidgets.QHBoxLayout(g_rot)
    btn_rot = FlatPush("Rotate 90 deg")
    rot.addWidget(btn_rot)
    rot.addStretch(1)
    root.addWidget(g_rot)
    root.addStretch(1)
    return w


# =========================================================================
# Handler stubs
# =========================================================================
def on_safety_read(self) -> None:
    def work() -> None:
        s = self._require_session()
        try:
            events = s.get_amplifier_disable_events()
            for i, led in enumerate(self.safety_amp_leds):
                if i < len(events):
                    led.set_on(events[i] > 0, "#ef4444")
                else:
                    led.set_on(False)
        except Exception:
            pass
        self.log_msg("safety status read")
    self._run("Read safety", work)


def on_safety_read_config(self) -> None:
    def work() -> None:
        s = self._require_session()
        self.log_msg("safety config read (stub)")
    self._run("Read safety config", work)


def on_safety_write_config(self) -> None:
    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write("Write safety config"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        self._set_writable(True)
        self.log_msg("safety config written (stub)")
    self._run("Write safety config", work)


def on_zms_read(self) -> None:
    def work() -> None:
        s = self._require_session()
        try:
            status = s.get_zms_stability_status()
            if len(status) >= 2:
                self.zms_led_vib.set_on(status[0] == "0")
                self.zms_led_pos.set_on(status[1] == "0")
        except Exception:
            pass
        self.log_msg("ZMS status read")
    self._run("Read ZMS", work)


def on_zms_write(self) -> None:
    def work() -> None:
        s = self._require_session()
        assert self.gate
        vals = [float(ed.text()) for ed in self.zms_vel_thr + self.zms_pos_thr]
        if not self._confirm_write(f"ZMS thresholds: {vals}"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        self._set_writable(True)
        self.log_msg("ZMS thresholds written (stub)")
    self._run("Write ZMS", work)


def on_poly_read(self) -> None:
    self.log_msg("Polynom read (stub)")


def on_poly_write(self) -> None:
    self.log_msg("Polynom write (stub)")


def on_poly_read_all(self) -> None:
    self.log_msg("Polynom read all (stub)")


def on_poly_write_all(self) -> None:
    self.log_msg("Polynom write all (stub)")


def on_signal_continue_read(self) -> None:
    self.log_msg("Signal continue read (stub)")


def on_sig_save_settings(self) -> None:
    self.log_msg("Signal settings saved (stub)")


def on_sig_load_settings(self) -> None:
    self.log_msg("Signal settings loaded (stub)")


def _toggle_signal_monitoring(self) -> None:
    self._sig_monitoring = not self._sig_monitoring
    if self._sig_monitoring:
        self._sig_mon_btn.setText("Stop Monitoring")
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
            self._auto_refresh = True
    else:
        self._sig_mon_btn.setText("Start Monitoring")
    self.log_msg(f"Signal monitoring {'started' if self._sig_monitoring else 'stopped'}")


def show_ui_options(self) -> None:
    dlg = QtWidgets.QDialog(self)
    dlg.setWindowTitle("UI Options")
    dlg.setMinimumWidth(360)
    root = QtWidgets.QVBoxLayout(dlg)
    root.setContentsMargins(12, 12, 12, 12)
    info = QtWidgets.QLabel(
        "python_samba — vendor-free SAMBA-compatible host\n"
        "Pure RCI serial (no Rci32.dll / CommServer)\n"
        "Tab structure matches SAMBA19xUI\n\n"
        "Right-click for context menu (About / Timer / UI Options)"
    )
    info.setWordWrap(True)
    info.setStyleSheet("color:#404040; padding:8px; background:#f7f7f7; border:1px solid #c0c0c0;")
    root.addWidget(info)
    cb = QtWidgets.QCheckBox("Load system config from controller on connect")
    cb.setChecked(True)
    root.addWidget(cb)
    btn_row = QtWidgets.QHBoxLayout()
    btn_close = QtWidgets.QPushButton("Close")
    btn_close.clicked.connect(dlg.accept)
    btn_row.addStretch(1)
    btn_row.addWidget(btn_close)
    root.addLayout(btn_row)
    dlg.setStyleSheet("""
        QDialog { background: #f0f0f0; }
        QPushButton {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #f7f7f7, stop:1 #d8d8d8);
            border: 1px solid #808080; border-radius: 3px; padding: 4px 14px;
        }
    """)
    dlg.exec()


def apply_patches(cls: type) -> None:
    """Apply all unified special-tab patches."""
    for name in [
        "_build_special_tab", "_build_safety_tab", "_build_zms_tab",
        "_build_polynom_tab",
        "_build_signal_display_page",
        "_build_digio_tab", "_build_view3d_tab",
        "_on_digio_read", "_toggle_signal_monitoring",
        "on_safety_read", "on_safety_read_config", "on_safety_write_config",
        "on_zms_read", "on_zms_write",
        "on_signal_continue_read",
        "show_ui_options",
    ]:
        fn = globals().get(name)
        if fn:
            setattr(cls, name, fn)

    # Reuse the source-aligned Safety/ZMS implementations.  This module owns
    # the screenshot-oriented builders; safety_zms_patch owns the protocol
    # behaviour.  Loading only the handlers prevents it from replacing the
    # visible Special workspace.
    from python_samba.ui.patches import load_patch_module
    functional = load_patch_module("safety_zms_patch")
    if functional is not None:
        for name in (
            "on_safety_read", "on_safety_read_config", "on_safety_write_config",
            "on_zms_read", "on_zms_write",
        ):
            setattr(cls, name, getattr(functional, name))
