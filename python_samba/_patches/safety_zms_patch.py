"""Patch for _build_special_tab, on_safety_read, on_zms_read, on_zms_write.

All are module-level functions so they can be monkey-patched onto MainWindow.

Usage:
    from python_samba.ui.main_window import MainWindow
    from _patches.safety_zms_patch import _build_special_tab, on_safety_read, on_zms_read, on_zms_write
    MainWindow._build_special_tab = _build_special_tab
    MainWindow.on_safety_read = on_safety_read
    MainWindow.on_zms_read = on_zms_read
    MainWindow.on_zms_write = on_zms_write
"""

from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc

from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    LedIndicator,
    SciEdit,
)
from python_samba.ui.main_window import SamTabWidget


# ---------------------------------------------------------------------------
# Safety RMS axis labels  (matching C#: SafetyPage.setBinding)
# ---------------------------------------------------------------------------
SAFETY_GEO_LABELS = ["SY1", "SZ1", "SX2", "SZ2", "SY3", "SZ3"]
SAFETY_PROX_LABELS = ["SProx1", "SProx2", "SProx3", "SProxH1", "SProxH2", "SProxH3"]
SAFETY_RMS_LABELS = SAFETY_GEO_LABELS + SAFETY_PROX_LABELS  # 12 total

EQ_GEO_LABELS = ["EQY1", "EQZ1", "EQX2", "EQZ2", "EQY3", "EQZ3"]
EQ_PROX_LABELS = ["EQProx1", "EQProx2", "EQProx3", "EQProxH1", "EQProxH2", "EQProxH3"]
EQ_RMS_LABELS = EQ_GEO_LABELS + EQ_PROX_LABELS  # 12 total

ZMS_VEL_LABELS = ["Vel axis 1", "Vel axis 2", "Vel axis 3",
                  "Vel axis 4", "Vel axis 5", "Vel axis 6"]
ZMS_POS_LABELS = ["Pos axis 1", "Pos axis 2", "Pos axis 3",
                  "Pos axis 4", "Pos axis 5", "Pos axis 6"]


# ---------------------------------------------------------------------------
# Color helpers (matching C# converters)
# ---------------------------------------------------------------------------
def _safety_geo_color(rms_value: float, upper_limit: float, lower_limit: float) -> str:
    """Color a geophone safety RMS label background."""
    if rms_value > upper_limit:
        return "#ef4444"  # red — fault
    elif rms_value > lower_limit:
        return "#fbbf24"  # amber — warning
    else:
        return "#22c55e"  # green — OK


def _safety_prox_color(rms_value: float, upper_limit: float, lower_limit: float) -> str:
    """Color a prox safety RMS label background."""
    if rms_value > upper_limit:
        return "#ef4444"
    elif rms_value > lower_limit:
        return "#fbbf24"
    else:
        return "#22c55e"


def _eq_geo_color(rms_value: float, limit: float) -> str:
    """Color an earthquake geo RMS label background."""
    return "#ef4444" if rms_value > limit else "#22c55e"


def _eq_prox_color(rms_value: float, limit: float) -> str:
    """Color an earthquake prox RMS label background."""
    return "#ef4444" if rms_value > limit else "#22c55e"


def _safety_fault_to_led(fault_value: int) -> str:
    """SafetyFaultStatus2SafetyOKConverter: 0 = OK (green), else = fault (red)."""
    return "#22c55e" if fault_value == 0 else "#ef4444"


def _eq_fault_to_led(fault_value: int) -> str:
    """EQFaultStatus2BoolConverter: 0 = no fault (green), else = fault (red)."""
    return "#22c55e" if fault_value == 0 else "#ef4444"


# ---------------------------------------------------------------------------
# Helper: create a gradient-style colour bar below an RMS label
# ---------------------------------------------------------------------------
def _set_rms_label(widget: QtWidgets.QLabel, value: float, color: str) -> None:
    """Update a QLabel with a formatted RMS value and background colour."""
    widget.setText(f"{value:.5e}")
    widget.setStyleSheet(
        f"background-color: {color}; color: #000000; "
        f"padding: 2px 6px; border: 1px solid #808080; "
        f"font-family: Consolas, monospace; font-size: 11px;"
    )


def _make_rms_label(value: str = "—") -> QtWidgets.QLabel:
    """Create a QLabel for RMS value display."""
    lbl = QtWidgets.QLabel(value)
    lbl.setMinimumWidth(110)
    lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    lbl.setStyleSheet(
        "background-color: #e8e8e8; color: #202020; "
        "padding: 2px 6px; border: 1px solid #808080; "
        "font-family: Consolas, monospace; font-size: 11px;"
    )
    return lbl


# ===================================================================
# _build_special_tab  — replaces MainWindow._build_special_tab
# ===================================================================
def _build_special_tab(win) -> None:
    """Special tab — safety, ZMS, polynom (from SAMBA19xUI)."""
    tabs = SamTabWidget()
    tabs.currentChanged.connect(win._on_sub_tab_changed)

    # ================================================================
    # Safety / Earthquake monitoring
    # ================================================================
    w = QtWidgets.QWidget()
    wl = QtWidgets.QVBoxLayout(w)
    wl.setContentsMargins(6, 4, 6, 4)

    # --- Safety configuration (matching C# setBinding) ---
    g_cfg = GroupPanel("Safety & Earthquake Configuration")
    cf = QtWidgets.QFormLayout(g_cfg)
    cf.setHorizontalSpacing(10)
    cf.setVerticalSpacing(4)

    win.safety_geo_upper_limit = SciEdit("1.00000e+000")
    win.safety_geo_lower_limit = SciEdit("5.00000e-001")
    win.safety_prox_upper_limit = SciEdit("1.00000e+000")
    win.safety_prox_lower_limit = SciEdit("5.00000e-001")
    win.safety_rms_time_window = SciEdit("1.00000e+000")
    win.eq_geo_limit = SciEdit("1.00000e+000")
    win.eq_prox_limit = SciEdit("1.00000e+000")
    win.eq_rms_time_window = SciEdit("1.00000e+000")

    cf.addRow("Geo upper limit:", win.safety_geo_upper_limit)
    cf.addRow("Geo lower limit:", win.safety_geo_lower_limit)
    cf.addRow("Prox upper limit:", win.safety_prox_upper_limit)
    cf.addRow("Prox lower limit:", win.safety_prox_lower_limit)
    cf.addRow("RMS time window:", win.safety_rms_time_window)
    cf.addRow("EQ geo limit:", win.eq_geo_limit)
    cf.addRow("EQ prox limit:", win.eq_prox_limit)
    cf.addRow("EQ RMS time window:", win.eq_rms_time_window)
    wl.addWidget(g_cfg)

    # --- Status LEDs (matching C#: SafetyVibrationLed, SafetyPositionLed, EQStatusLed) ---
    g_leds = GroupPanel("Status")
    led_row = QtWidgets.QHBoxLayout(g_leds)
    led_row.setSpacing(16)

    win.safety_vibration_led = LedIndicator(14)
    win.safety_position_led = LedIndicator(14)
    win.eq_status_led = LedIndicator(14)

    led_row.addWidget(QtWidgets.QLabel("Vibration:"))
    led_row.addWidget(win.safety_vibration_led)
    led_row.addWidget(QtWidgets.QLabel("Position:"))
    led_row.addWidget(win.safety_position_led)
    led_row.addWidget(QtWidgets.QLabel("EQ status:"))
    led_row.addWidget(win.eq_status_led)
    led_row.addStretch(1)
    wl.addWidget(g_leds)

    # --- Safety RMS values (matching C#: SY1GeophTbl .. SProxH3Tbl, 12 total) ---
    g_safe_rms = GroupPanel("Safety RMS Values")
    safe_grid = QtWidgets.QGridLayout(g_safe_rms)
    safe_grid.setSpacing(4)

    # Geophone group (6)
    safe_grid.addWidget(QtWidgets.QLabel("Geophone:"), 0, 0, 1, 6)
    win.safety_rms_geo: list[QtWidgets.QLabel] = []
    for i, label in enumerate(SAFETY_GEO_LABELS):
        lbl = _make_rms_label()
        win.safety_rms_geo.append(lbl)
        safe_grid.addWidget(QtWidgets.QLabel(label), 1, i)
        safe_grid.addWidget(lbl, 2, i)

    # Prox group (6)
    safe_grid.addWidget(QtWidgets.QLabel("Prox:"), 3, 0, 1, 6)
    win.safety_rms_prox: list[QtWidgets.QLabel] = []
    for i, label in enumerate(SAFETY_PROX_LABELS):
        lbl = _make_rms_label()
        win.safety_rms_prox.append(lbl)
        safe_grid.addWidget(QtWidgets.QLabel(label), 4, i)
        safe_grid.addWidget(lbl, 5, i)

    # Combined list for indexed access (0-5 geo, 6-11 prox, matching C# indices)
    win.safety_rms_labels = win.safety_rms_geo + win.safety_rms_prox
    wl.addWidget(g_safe_rms)

    # --- Earthquake RMS values (matching C#: EQY1GeophTbl .. EQProxH3Tbl, 12 total) ---
    g_eq_rms = GroupPanel("Earthquake RMS Values")
    eq_grid = QtWidgets.QGridLayout(g_eq_rms)
    eq_grid.setSpacing(4)

    # Geophone group (6)
    eq_grid.addWidget(QtWidgets.QLabel("Geophone:"), 0, 0, 1, 6)
    win.eq_rms_geo: list[QtWidgets.QLabel] = []
    for i, label in enumerate(EQ_GEO_LABELS):
        lbl = _make_rms_label()
        win.eq_rms_geo.append(lbl)
        eq_grid.addWidget(QtWidgets.QLabel(label), 1, i)
        eq_grid.addWidget(lbl, 2, i)

    # Prox group (6)
    eq_grid.addWidget(QtWidgets.QLabel("Prox:"), 3, 0, 1, 6)
    win.eq_rms_prox: list[QtWidgets.QLabel] = []
    for i, label in enumerate(EQ_PROX_LABELS):
        lbl = _make_rms_label()
        win.eq_rms_prox.append(lbl)
        eq_grid.addWidget(QtWidgets.QLabel(label), 4, i)
        eq_grid.addWidget(lbl, 5, i)

    # Combined list for indexed access (0-5 geo, 6-11 prox)
    win.eq_rms_labels = win.eq_rms_geo + win.eq_rms_prox
    wl.addWidget(g_eq_rms)

    # --- Amplifier events (keep existing) ---
    g_amplifier = GroupPanel("Amplifier Events")
    amp_grid = QtWidgets.QGridLayout(g_amplifier)
    win.safety_amp_leds: list[LedIndicator] = []
    for i in range(12):
        led = LedIndicator(10)
        win.safety_amp_leds.append(led)
        amp_grid.addWidget(led, i // 4, i % 4)
        amp_grid.addWidget(QtWidgets.QLabel(f"Motor {i+1}"), i // 4 + 1, i % 4)
    wl.addWidget(g_amplifier)

    # --- Buttons ---
    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_r.clicked.connect(win.on_safety_read)
    act.addWidget(btn_r)
    btn_cfg = FlatPush("Read config")
    btn_cfg.clicked.connect(win.on_safety_read_config)
    act.addWidget(btn_cfg)
    btn_wcfg = FlatPush("Write config...")
    btn_wcfg.clicked.connect(win.on_safety_write_config)
    act.addWidget(btn_wcfg)
    act.addStretch(1)
    wl.addLayout(act)
    wl.addStretch(1)
    tabs.addTab(w, "Safety")

    # ================================================================
    # ZMS (Zeiss Merity Safety) — matching C# ZMSPage
    # ================================================================
    zms = QtWidgets.QWidget()
    zl = QtWidgets.QVBoxLayout(zms)
    zl.setContentsMargins(6, 4, 6, 4)

    # --- ZMS Status LEDs (matching C#: VibrationLed, PositionLed) ---
    g_zms_status = GroupPanel("ZMS Status")
    zms_status_grid = QtWidgets.QGridLayout(g_zms_status)
    zms_status_grid.setSpacing(8)

    win.zms_vibration_led = LedIndicator(14)
    win.zms_position_led = LedIndicator(14)
    win.zms_failed_axis = QtWidgets.QLabel("—")
    win.zms_failed_axis.setStyleSheet(
        "background-color: #ffffff; color: #202020; "
        "padding: 2px 6px; border: 1px solid #808080; "
        "font-family: Consolas, monospace; font-size: 11px;"
    )
    win.zms_failed_axis.setMinimumWidth(80)
    win.zms_failed_rms = QtWidgets.QLabel("—")
    win.zms_failed_rms.setStyleSheet(
        "background-color: #ffffff; color: #202020; "
        "padding: 2px 6px; border: 1px solid #808080; "
        "font-family: Consolas, monospace; font-size: 11px;"
    )
    win.zms_failed_rms.setMinimumWidth(110)

    zms_status_grid.addWidget(QtWidgets.QLabel("Vibration:"), 0, 0)
    zms_status_grid.addWidget(win.zms_vibration_led, 0, 1)
    zms_status_grid.addWidget(QtWidgets.QLabel("Position:"), 0, 2)
    zms_status_grid.addWidget(win.zms_position_led, 0, 3)
    zms_status_grid.addWidget(QtWidgets.QLabel("Last failed axis:"), 1, 0)
    zms_status_grid.addWidget(win.zms_failed_axis, 1, 1)
    zms_status_grid.addWidget(QtWidgets.QLabel("Last failed RMS:"), 1, 2)
    zms_status_grid.addWidget(win.zms_failed_rms, 1, 3)
    zl.addWidget(g_zms_status)

    # --- ZMS Velocity Thresholds (matching C#: VelThr0-VelThr5) ---
    g_vel = GroupPanel("Velocity Thresholds")
    vf = QtWidgets.QFormLayout(g_vel)
    vf.setHorizontalSpacing(10)
    vf.setVerticalSpacing(4)
    win.zms_vel_thresholds: list[SciEdit] = []
    for i in range(6):
        ed = SciEdit("1.00000e+000")
        win.zms_vel_thresholds.append(ed)
        vf.addRow(f"VelThr{i}:", ed)
    zl.addWidget(g_vel)

    # --- ZMS Position Thresholds (matching C#: PosThr0-PosThr5) ---
    g_pos = GroupPanel("Position Thresholds")
    pf = QtWidgets.QFormLayout(g_pos)
    pf.setHorizontalSpacing(10)
    pf.setVerticalSpacing(4)
    win.zms_pos_thresholds: list[SciEdit] = []
    for i in range(6):
        ed = SciEdit("1.00000e+000")
        win.zms_pos_thresholds.append(ed)
        pf.addRow(f"PosThr{i}:", ed)
    zl.addWidget(g_pos)

    # --- ZMS Velocity RMS Values (matching C#: VelVal0-VelVal5) ---
    g_vel_val = GroupPanel("Velocity RMS Values")
    vv = QtWidgets.QFormLayout(g_vel_val)
    vv.setHorizontalSpacing(10)
    vv.setVerticalSpacing(4)
    win.zms_vel_values: list[QtWidgets.QLabel] = []
    for i in range(6):
        lbl = _make_rms_label()
        win.zms_vel_values.append(lbl)
        vv.addRow(f"VelVal{i}:", lbl)
    zl.addWidget(g_vel_val)

    # --- ZMS Position RMS Values (matching C#: PosVal0-PosVal5) ---
    g_pos_val = GroupPanel("Position RMS Values")
    pv = QtWidgets.QFormLayout(g_pos_val)
    pv.setHorizontalSpacing(10)
    pv.setVerticalSpacing(4)
    win.zms_pos_values: list[QtWidgets.QLabel] = []
    for i in range(6):
        lbl = _make_rms_label()
        win.zms_pos_values.append(lbl)
        pv.addRow(f"PosVal{i}:", lbl)
    zl.addWidget(g_pos_val)

    # --- ZMS buttons ---
    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_r.clicked.connect(win.on_zms_read)
    act.addWidget(btn_r)
    btn_w = FlatPush("Write thresholds...")
    btn_w.clicked.connect(win.on_zms_write)
    act.addWidget(btn_w)
    act.addStretch(1)
    zl.addLayout(act)
    zl.addStretch(1)
    tabs.addTab(zms, "ZMS")

    # ================================================================
    # Polynom (keep as-is from original)
    # ================================================================
    poly = QtWidgets.QWidget()
    pl = QtWidgets.QVBoxLayout(poly)
    pl.setContentsMargins(6, 4, 6, 4)

    g_poly = GroupPanel("Polynom Configuration")
    pf_poly = QtWidgets.QFormLayout(g_poly)
    win.poly_num = QtWidgets.QComboBox()
    win.poly_num.addItems([f"Polynom {i+1}" for i in range(19)])
    win.poly_type = QtWidgets.QComboBox()
    win.poly_type.addItems(["Input", "Output"])
    win.poly_coeffs: list[SciEdit] = []
    for i in range(5):
        ed = SciEdit("0.00000e+000")
        win.poly_coeffs.append(ed)
    pf_poly.addRow("Polynom:", win.poly_num)
    pf_poly.addRow("Type:", win.poly_type)
    for i in range(5):
        pf_poly.addRow(f"Coeff {i+1}:", win.poly_coeffs[i])
    pl.addWidget(g_poly)

    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read")
    btn_w = FlatPush("Write...")
    btn_r.clicked.connect(win.on_poly_read)
    btn_w.clicked.connect(win.on_poly_write)
    act.addWidget(btn_r)
    act.addWidget(btn_w)
    act.addStretch(1)
    pl.addLayout(act)
    pl.addStretch(1)
    tabs.addTab(poly, "Polynom")

    win.main_tabs.addTab(tabs, "Special")


# ===================================================================
# on_safety_read  — replaces MainWindow.on_safety_read
# Mirrors C# SafetyPage.UpdateStates() + reading config
# ===================================================================
def on_safety_read(win) -> None:
    """Read safety / earthquake status and RMS values.

    Matches C# SafetyPage.UpdateStates():
        Controller.tcmfd.GetSensorSafetyRMSValues()
        Controller.tcmfd.GetSensorEarthQuakeRMSValues()
    """
    def work() -> None:
        s = win._require_session()

        # --- Get sensor safety RMS values (C#: GetSensorSafetyRMSValues) ---
        try:
            # Controller returns 12 values: 6 geo + 6 prox
            safety_rms = s.get_sensor_safety_rms_values()
            if safety_rms and len(safety_rms) >= 12:
                geo_upper = float(win.safety_geo_upper_limit.text())
                geo_lower = float(win.safety_geo_lower_limit.text())
                prox_upper = float(win.safety_prox_upper_limit.text())
                prox_lower = float(win.safety_prox_lower_limit.text())

                for i in range(6):
                    val = safety_rms[i]
                    color = _safety_geo_color(val, geo_upper, geo_lower)
                    _set_rms_label(win.safety_rms_geo[i], val, color)

                for i in range(6):
                    val = safety_rms[6 + i]
                    color = _safety_prox_color(val, prox_upper, prox_lower)
                    _set_rms_label(win.safety_rms_prox[i], val, color)

                win.log_msg("safety RMS values read")
            else:
                win.log_msg("safety RMS: no data")
        except Exception as exc:
            win.log_msg(f"safety RMS: {exc}")

        # --- Get sensor earthquake RMS values (C#: GetSensorEarthQuakeRMSValues) ---
        try:
            eq_rms = s.get_sensor_earthquake_rms_values()
            if eq_rms and len(eq_rms) >= 12:
                eq_geo_limit = float(win.eq_geo_limit.text())
                eq_prox_limit = float(win.eq_prox_limit.text())

                for i in range(6):
                    val = eq_rms[i]
                    color = _eq_geo_color(val, eq_geo_limit)
                    _set_rms_label(win.eq_rms_geo[i], val, color)

                for i in range(6):
                    val = eq_rms[6 + i]
                    color = _eq_prox_color(val, eq_prox_limit)
                    _set_rms_label(win.eq_rms_prox[i], val, color)

                win.log_msg("earthquake RMS values read")
            else:
                win.log_msg("earthquake RMS: no data")
        except Exception as exc:
            win.log_msg(f"earthquake RMS: {exc}")

        # --- Read amplifier disable events (existing behaviour) ---
        try:
            events = s.get_amplifier_disable_events()
            for i, led in enumerate(win.safety_amp_leds):
                if i < len(events):
                    led.set_on(events[i] > 0, "#ef4444")
                else:
                    led.set_on(False)
        except Exception as exc:
            win.log_msg(f"amplifier events: {exc}")

        # --- Read safety/earthquake fault status for LEDs ---
        try:
            # C# binds:
            #   SafetyVibrationLed ← GeoFault via SafetyFaultStatus2SafetyOKConverter
            #   SafetyPositionLed ← ProxFault via SafetyFaultStatus2SafetyOKConverter
            #   EQStatusLed ← EQGeoFault via EQFaultStatus2BoolConverter
            geo_fault = s.get_safety_geo_fault()
            win.safety_vibration_led.set_color(_safety_fault_to_led(geo_fault))

            prox_fault = s.get_safety_prox_fault()
            win.safety_position_led.set_color(_safety_fault_to_led(prox_fault))

            eq_fault = s.get_earthquake_geo_fault()
            win.eq_status_led.set_color(_eq_fault_to_led(eq_fault))
        except Exception as exc:
            win.log_msg(f"fault status: {exc}")

        win.log_msg("safety status read")

    win._run("Read safety", work)


# ===================================================================
# on_safety_read_config  — reads safety/earthquake config
# Mirrors C# SafetyPage.UpdatePage():
#     Controller.tcmfd.GetSafetyAndEarthQuakeConfig()
# ===================================================================
def on_safety_read_config(win) -> None:
    """Read safety & earthquake configuration from controller."""
    def work() -> None:
        s = win._require_session()
        try:
            cfg = s.get_safety_and_earthquake_config()
            # cfg is expected to be a dict or tuple with 8 values:
            # (geo_upper, geo_lower, prox_upper, prox_lower, rms_time_window,
            #  eq_geo_limit, eq_prox_limit, eq_rms_time_window)
            if cfg and len(cfg) >= 8:
                win.safety_geo_upper_limit.setText(f"{cfg[0]:.5e}")
                win.safety_geo_lower_limit.setText(f"{cfg[1]:.5e}")
                win.safety_prox_upper_limit.setText(f"{cfg[2]:.5e}")
                win.safety_prox_lower_limit.setText(f"{cfg[3]:.5e}")
                win.safety_rms_time_window.setText(f"{cfg[4]:.5e}")
                win.eq_geo_limit.setText(f"{cfg[5]:.5e}")
                win.eq_prox_limit.setText(f"{cfg[6]:.5e}")
                win.eq_rms_time_window.setText(f"{cfg[7]:.5e}")
                win.log_msg("safety config read")
            else:
                win.log_msg("safety config: no data")
        except Exception as exc:
            win.log_msg(f"safety config: {exc}")
    win._run("Read safety config", work)


# ===================================================================
# on_safety_write_config  — writes safety/earthquake config
# Mirrors C# SafetyAndEarthQuakeParam_Changed:
#     Controller.tcmfd.SetSafetyAndEarthQuakeConfig()
# ===================================================================
def on_safety_write_config(win) -> None:
    """Write safety & earthquake configuration to controller."""
    def work() -> None:
        s = win._require_session()
        assert win.gate
        vals = [
            float(win.safety_geo_upper_limit.text()),
            float(win.safety_geo_lower_limit.text()),
            float(win.safety_prox_upper_limit.text()),
            float(win.safety_prox_lower_limit.text()),
            float(win.safety_rms_time_window.text()),
            float(win.eq_geo_limit.text()),
            float(win.eq_prox_limit.text()),
            float(win.eq_rms_time_window.text()),
        ]
        if not win._confirm_write(f"Safety config: {vals}"):
            return
        win.gate.take_snapshot()
        win._set_writable(True)
        s.set_safety_and_earthquake_config(vals)
        win._set_writable(True)
        win.log_msg("safety config written")
    win._run("Write safety config", work)


# ===================================================================
# on_zms_read  — replaces MainWindow.on_zms_read
# Mirrors C# ZMSPage.UpdateStates():
#     Controller.tcmfd.GetLastStabilityFailedEvent_ZMS()
#     Controller.tcmfd.GetStabilityStatusAndRMSValues_ZMS()
# ===================================================================
def on_zms_read(win) -> None:
    """Read ZMS stability status, RMS values, and last failed event.

    Matches C# ZMSPage.UpdateStates():
        Controller.tcmfd.GetLastStabilityFailedEvent_ZMS()
        Controller.tcmfd.GetStabilityStatusAndRMSValues_ZMS()
    """
    def work() -> None:
        s = win._require_session()

        # --- Get last stability failed event (C#: GetLastStabilityFailedEvent_ZMS) ---
        try:
            axis, rms_val = s.get_zms_last_failed_event()
            win.zms_failed_axis.setText(f"Axis {axis}" if axis >= 0 else "—")
            win.zms_failed_rms.setText(f"{rms_val:.5e}" if rms_val >= 0 else "—")
        except Exception:
            win.zms_failed_axis.setText("—")
            win.zms_failed_rms.setText("—")

        # --- Get stability status and RMS values (C#: GetStabilityStatusAndRMSValues_ZMS) ---
        try:
            # Returns tuple of (status_dict, rms_values_array)
            # status_dict: {"vibration": int, "position": int}
            # rms_values: 12 values [vel0..vel5, pos0..pos5]
            result = s.get_zms_stability_status_and_rms_values()
            if result:
                status, rms_values = result if len(result) == 2 else (result, None)

                # Update LEDs
                if isinstance(status, dict):
                    vib = status.get("vibration", 0)
                    pos = status.get("position", 0)
                elif isinstance(status, (list, tuple)) and len(status) >= 2:
                    vib, pos = status[0], status[1]
                else:
                    vib = pos = 0

                # C#: VibrationLed ← ZMSParam.VibrationStatus
                # C#: PositionLed ← ZMSParam.PositionStatus
                # 0 = OK (green), non-zero = fault (red)
                win.zms_vibration_led.set_color("#22c55e" if vib == 0 else "#ef4444")
                win.zms_position_led.set_color("#22c55e" if pos == 0 else "#ef4444")

                # Update RMS values: first 6 = velocity, last 6 = position
                if rms_values and len(rms_values) >= 12:
                    for i in range(6):
                        win.zms_vel_values[i].setText(f"{rms_values[i]:.5e}")
                    for i in range(6):
                        win.zms_pos_values[i].setText(f"{rms_values[6 + i]:.5e}")
                elif rms_values and len(rms_values) >= 6:
                    # Fallback: just 6 values (old API)
                    for i in range(6):
                        win.zms_vel_values[i].setText(f"{rms_values[i]:.5e}")

        except Exception:
            pass

        # --- Also read ZMS thresholds to keep them in sync (C#: UpdatePage calls GetStabilityThreshold_ZMS) ---
        try:
            thresholds = s.get_zms_stability_thresholds()
            if thresholds and len(thresholds) >= 12:
                for i in range(6):
                    win.zms_vel_thresholds[i].setText(f"{thresholds[i]:.5e}")
                for i in range(6):
                    win.zms_pos_thresholds[i].setText(f"{thresholds[6 + i]:.5e}")
            elif thresholds and len(thresholds) >= 6:
                # Old API: just 6 thresholds
                for i in range(6):
                    win.zms_vel_thresholds[i].setText(f"{thresholds[i]:.5e}")
        except Exception:
            pass

        win.log_msg("ZMS status read")

    win._run("Read ZMS", work)


# ===================================================================
# on_zms_write  — replaces MainWindow.on_zms_write
# Mirrors C# Threshold_PropertyChanged:
#     Controller.tcmfd.SetStabilityThreshold_ZMS()
# ===================================================================
def on_zms_write(win) -> None:
    """Write ZMS stability thresholds (velocity + position).

    Matches C#: Controller.tcmfd.SetStabilityThreshold_ZMS()
    which sends all 12 thresholds (6 vel + 6 pos).
    """
    def work() -> None:
        s = win._require_session()
        assert win.gate
        vel_vals = [float(ed.text()) for ed in win.zms_vel_thresholds]
        pos_vals = [float(ed.text()) for ed in win.zms_pos_thresholds]
        all_vals = vel_vals + pos_vals  # 12 total
        if not win._confirm_write(f"ZMS thresholds: {all_vals}"):
            return
        win.gate.take_snapshot()
        win._set_writable(True)
        s.set_zms_stability_thresholds(all_vals)
        win._set_writable(True)
        win.log_msg("ZMS thresholds written")
    win._run("Write ZMS", work)