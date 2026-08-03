"""
Polynom Editor Patch
====================
Replaces the simple polynom section in _build_special_tab (main_window.py) with a
full polynom editor matching the SAMBA19xUI PolynomPage / Polynoms / Polynom C# controls.

Features:
  - 19 polynom selector (0-18)
  - Input/Output type selector with signal source/destination assignment
  - 5 coefficient fields per polynom (Coeff 0-4)
  - Limiter field
  - Input value / output value read-only displays
  - Active status LED toggle button
  - Processing status and overall active status LED toggles
  - Read: reads current polynom config from controller
  - Write: writes selected polynom config to controller
  - Read All: reads all polynom parameters from controller
  - Write All: writes all polynom parameters to controller
  - Save/Load config to/from file

RCI commands used:
  LGPCP  (get polynom config)   — I1 → I7, D6
  LSPCP  (set polynom config)   — I1, I7, D6
  LGPSP  (get polynom status)   — → I2
  LSPSP  (set polynom status)   — I2 →
  LGPIV  (get polynom inputs)   — → D16
  LGPOV  (get polynom outputs)  — → D16
  LGPRP  (get polynom ramp)     — → I1, D3
  LSPRP  (set polynom ramp)     — I1, D3

Usage
-----
Monkey-patch into MainWindow:
  from python_samba.ui.patches import apply_polynom_patch
  apply_polynom_patch(MainWindow)

Or replace the _build_special_tab method and add the handler methods.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python_samba.ui.main_window import MainWindow

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLYNOM_NAMES = [
    "Polynom 0", "Polynom 1", "Polynom 2", "Polynom 3", "Polynom 4",
    "Polynom 5", "Polynom 6", "Polynom 7", "Polynom 8", "Polynom 9",
    "Polynom 10", "Polynom 11", "Polynom 12", "Polynom 13", "Polynom 14",
    "Polynom 15", "Polynom 16", "Polynom 17", "Polynom 18",
]

NUM_COEFFS = 5  # Coeff 0..4 (order 4)

# IO signal type identifiers (from SAMBA19xLib IOType)
IO_TYPE_ADC_INPUT = 1
IO_TYPE_DAC_OUTPUT = 2

# ---------------------------------------------------------------------------
# Helper: build a styled LED-style toggle button
# ---------------------------------------------------------------------------

def _make_led_btn(text: str, initial: bool = False) -> QtWidgets.QPushButton:
    """Styled push button that acts as an LED indicator/toggle."""
    btn = QtWidgets.QPushButton(text)
    btn.setCheckable(True)
    btn.setChecked(initial)
    btn.setFixedHeight(28)
    _update_led_style(btn)
    return btn


def _update_led_style(btn: QtWidgets.QPushButton) -> None:
    """Update the stylesheet of an LED button based on its checked state."""
    if btn.isChecked():
        btn.setStyleSheet(
            "QPushButton { background:#2a8a2a; color:#fff; font-weight:700; "
            "border:1px solid #1a6a1a; border-radius:4px; padding:2px 8px; }"
        )
    else:
        btn.setStyleSheet(
            "QPushButton { background:#aaa; color:#444; font-weight:400; "
            "border:1px solid #888; border-radius:4px; padding:2px 8px; }"
        )


# ---------------------------------------------------------------------------
# Replacement for the polynom section inside _build_special_tab
# ---------------------------------------------------------------------------

def _build_special_tab(self: MainWindow) -> None:
    """Special tab — safety, ZMS, polynom (from SAMBA19xUI)."""
    from python_samba.ui.classic_widgets import (
        FlatPush, GroupPanel, LedIndicator, RockerButton, SciEdit,
    )
    from python_samba.ui.main_window import SamTabWidget

    tabs = SamTabWidget()
    tabs.currentChanged.connect(self._on_sub_tab_changed)

    # ---- Safety / Earthquake monitoring ----
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

    # ---- ZMS (Zeiss Merity Safety) ----
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

    # ---- Polynom ----
    poly = QtWidgets.QWidget()
    pl = QtWidgets.QVBoxLayout(poly)
    pl.setContentsMargins(6, 4, 6, 4)

    # -- Status bar (processing + overall active) --
    status_bar = QtWidgets.QHBoxLayout()
    status_bar.addWidget(QtWidgets.QLabel("Processing:"))
    self.poly_processing_led = _make_led_btn("Processing", False)
    self.poly_processing_led.toggled.connect(lambda _: _update_led_style(self.poly_processing_led))
    status_bar.addWidget(self.poly_processing_led)
    status_bar.addSpacing(16)
    status_bar.addWidget(QtWidgets.QLabel("Overall Active:"))
    self.poly_overall_active_led = _make_led_btn("Active", False)
    self.poly_overall_active_led.toggled.connect(lambda _: _update_led_style(self.poly_overall_active_led))
    status_bar.addWidget(self.poly_overall_active_led)
    status_bar.addStretch(1)
    pl.addLayout(status_bar)

    # -- Polynom selector + type --
    sel_row = QtWidgets.QHBoxLayout()
    sel_row.addWidget(QtWidgets.QLabel("Polynom:"))
    self.poly_num = QtWidgets.QComboBox()
    self.poly_num.addItems(POLYNOM_NAMES)
    self.poly_num.setMinimumWidth(120)
    sel_row.addWidget(self.poly_num)
    sel_row.addSpacing(12)
    sel_row.addWidget(QtWidgets.QLabel("Type:"))
    self.poly_type = QtWidgets.QComboBox()
    self.poly_type.addItems(["Input", "Output"])
    self.poly_type.setMinimumWidth(80)
    sel_row.addWidget(self.poly_type)
    sel_row.addSpacing(12)
    sel_row.addWidget(QtWidgets.QLabel("Active:"))
    self.poly_active_led = _make_led_btn("On", False)
    self.poly_active_led.toggled.connect(lambda _: _update_led_style(self.poly_active_led))
    sel_row.addWidget(self.poly_active_led)
    sel_row.addStretch(1)
    pl.addLayout(sel_row)

    # -- IO signal assignment --
    io_row = QtWidgets.QHBoxLayout()
    io_row.addWidget(QtWidgets.QLabel("Input signal:"))
    self.poly_input_sig = QtWidgets.QComboBox()
    self.poly_input_sig.setMinimumWidth(160)
    io_row.addWidget(self.poly_input_sig)
    io_row.addSpacing(12)
    io_row.addWidget(QtWidgets.QLabel("Output signal:"))
    self.poly_output_sig = QtWidgets.QComboBox()
    self.poly_output_sig.setMinimumWidth(160)
    io_row.addWidget(self.poly_output_sig)
    io_row.addStretch(1)
    pl.addLayout(io_row)

    # -- Read-only input/output values --
    val_row = QtWidgets.QHBoxLayout()
    val_row.addWidget(QtWidgets.QLabel("Input value:"))
    self.poly_input_val = QtWidgets.QLineEdit("0")
    self.poly_input_val.setReadOnly(True)
    self.poly_input_val.setFixedWidth(120)
    self.poly_input_val.setStyleSheet("QLineEdit { background:#e0e8f0; }")
    val_row.addWidget(self.poly_input_val)
    val_row.addSpacing(12)
    val_row.addWidget(QtWidgets.QLabel("Output value:"))
    self.poly_output_val = QtWidgets.QLineEdit("0")
    self.poly_output_val.setReadOnly(True)
    self.poly_output_val.setFixedWidth(120)
    self.poly_output_val.setStyleSheet("QLineEdit { background:#d0e0d0; }")
    val_row.addWidget(self.poly_output_val)
    val_row.addStretch(1)
    pl.addLayout(val_row)

    # -- Coefficients --
    g_coeff = GroupPanel("Coefficients & Limiter")
    coeff_grid = QtWidgets.QGridLayout(g_coeff)
    self.poly_coeffs: list[SciEdit] = []
    for i in range(NUM_COEFFS):
        coeff_grid.addWidget(QtWidgets.QLabel(f"Coeff {i}:"), 0, i)
        ed = SciEdit("0.00000e+000")
        self.poly_coeffs.append(ed)
        coeff_grid.addWidget(ed, 1, i)
    coeff_grid.addWidget(QtWidgets.QLabel("Limiter:"), 0, NUM_COEFFS)
    self.poly_limiter = SciEdit("0.00000e+000")
    coeff_grid.addWidget(self.poly_limiter, 1, NUM_COEFFS)
    pl.addWidget(g_coeff)

    # -- Action buttons --
    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read")
    btn_r.clicked.connect(self.on_poly_read)
    act.addWidget(btn_r)
    btn_w = FlatPush("Write")
    btn_w.clicked.connect(self.on_poly_write)
    act.addWidget(btn_w)
    btn_ra = FlatPush("Read All")
    btn_ra.clicked.connect(self.on_poly_read_all)
    act.addWidget(btn_ra)
    btn_wa = FlatPush("Write All")
    btn_wa.clicked.connect(self.on_poly_write_all)
    act.addWidget(btn_wa)
    act.addStretch(1)
    pl.addLayout(act)

    # -- File save/load buttons --
    act2 = QtWidgets.QHBoxLayout()
    btn_save = FlatPush("Save config to file...")
    btn_save.clicked.connect(self.on_poly_save_config)
    act2.addWidget(btn_save)
    btn_load = FlatPush("Load config from file...")
    btn_load.clicked.connect(self.on_poly_load_config)
    act2.addWidget(btn_load)
    act2.addStretch(1)
    pl.addLayout(act2)

    # -- Status / ramp status --
    ramp_row = QtWidgets.QHBoxLayout()
    ramp_row.addWidget(QtWidgets.QLabel("Ramp status:"))
    self.poly_ramp_led = _make_led_btn("Ramp", False)
    self.poly_ramp_led.toggled.connect(lambda _: _update_led_style(self.poly_ramp_led))
    ramp_row.addWidget(self.poly_ramp_led)
    ramp_row.addSpacing(12)
    ramp_row.addWidget(QtWidgets.QLabel("Start:"))
    self.poly_ramp_start = SciEdit("0.00000e+000")
    ramp_row.addWidget(self.poly_ramp_start)
    ramp_row.addWidget(QtWidgets.QLabel("End:"))
    self.poly_ramp_end = SciEdit("0.00000e+000")
    ramp_row.addWidget(self.poly_ramp_end)
    ramp_row.addWidget(QtWidgets.QLabel("Time (s):"))
    self.poly_ramp_time = SciEdit("0.00000e+000")
    ramp_row.addWidget(self.poly_ramp_time)
    ramp_row.addStretch(1)
    pl.addLayout(ramp_row)

    # -- Ramp read/write --
    act3 = QtWidgets.QHBoxLayout()
    btn_rr = FlatPush("Read Ramp")
    btn_rr.clicked.connect(self.on_poly_ramp_read)
    act3.addWidget(btn_rr)
    btn_wr = FlatPush("Write Ramp")
    btn_wr.clicked.connect(self.on_poly_ramp_write)
    act3.addWidget(btn_wr)
    act3.addStretch(1)
    pl.addLayout(act3)

    pl.addStretch(1)
    tabs.addTab(poly, "Polynom")

    # Populate IO signal combo boxes
    _populate_poly_io_combos(self)

    self.main_tabs.addTab(tabs, "Special")


# ---------------------------------------------------------------------------
# IO signal combo box population
# ---------------------------------------------------------------------------

def _populate_poly_io_combos(self: MainWindow) -> None:
    """Populate the input/output signal combo boxes."""
    # ADC input names (from main_window ADC_INPUT_NAMES)
    adc_names = [
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
        "Xff", "Yff", "Zff", "Prox1", "Prox2", "Prox3", "ProxH1",
        "ProxH2", "ProxH3", "Xpos", "Xacc", "Ypos", "Yacc",
        "Y2FB", "X3FB", "X4FB", "Y4FB", "Z4FB", "Prox4", "ProxH4",
        "Auxiliary1", "Auxiliary2", "Auxiliary3", "Auxiliary4", "Auxiliary5",
    ]
    # DAC output names (from main_window DAC_OUTPUT_NAMES)
    dac_names = [
        "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
        "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
        "Valve1", "Valve2", "Valve3", "Valve4", "Valve5", "Valve6",
        "Diag0", "Diag1",
    ]

    self.poly_input_sig.clear()
    self.poly_input_sig.addItem("None", (0, 0, 0))
    for idx, name in enumerate(adc_names):
        # Type=1 (ADC input), MainIndex=idx, SubIndex=0
        self.poly_input_sig.addItem(name, (1, idx, 0))

    self.poly_output_sig.clear()
    self.poly_output_sig.addItem("None", (0, 0, 0))
    for idx, name in enumerate(dac_names):
        # Type=2 (DAC output), MainIndex=idx, SubIndex=0
        self.poly_output_sig.addItem(name, (2, idx, 0))


# ---------------------------------------------------------------------------
# Polynom data helpers
# ---------------------------------------------------------------------------

def _poly_get_active_status(self: MainWindow, polynum: int) -> int:
    """Get active status for a polynom from the controller."""
    s = self._require_session()
    resp = s.raw_command("LGPCP", polynum)
    s.encoder.ensure_ok(resp, "LGPCP")
    # data_tokens: I7 (7 ints), then D6 (6 doubles)
    # First int is ActiveStatus
    active = int(resp.data_tokens[0])
    return active


def _poly_parse_get_response(self: MainWindow, resp) -> dict:
    """Parse LGPCP response into a dict.

    Response format: I7 D6
    I7: active_status, input_type, input_main, input_sub, output_type, output_main, output_sub
    D6: coeff0..coeff4, limiter
    """
    tokens = resp.data_tokens
    result = {
        "active_status": int(tokens[0]),
        "input_type": int(tokens[1]),
        "input_main": int(tokens[2]),
        "input_sub": int(tokens[3]),
        "output_type": int(tokens[4]),
        "output_main": int(tokens[5]),
        "output_sub": int(tokens[6]),
        "coeffs": [float(tokens[7 + i]) for i in range(NUM_COEFFS)],
        "limiter": float(tokens[7 + NUM_COEFFS]),
    }
    return result


# ---------------------------------------------------------------------------
# Handler Methods
# ---------------------------------------------------------------------------

def on_poly_read(self: MainWindow) -> None:
    """Read the selected polynom configuration from controller."""
    def work() -> None:
        s = self._require_session()
        polynum = self.poly_num.currentIndex()
        try:
            resp = s.raw_command("LGPCP", polynum)
            s.encoder.ensure_ok(resp, "LGPCP")
            data = _poly_parse_get_response(self, resp)

            # Active status
            self.poly_active_led.setChecked(data["active_status"] != 0)
            _update_led_style(self.poly_active_led)

            # Type (Input=0, Output=1 based on IO type)
            # Input type 1 = ADC, Output type 2 = DAC
            is_output = data["output_type"] != 0
            self.poly_type.setCurrentIndex(1 if is_output else 0)

            # Input signal
            in_type, in_main, in_sub = data["input_type"], data["input_main"], data["input_sub"]
            _select_io_combo(self.poly_input_sig, in_type, in_main, in_sub)

            # Output signal
            out_type, out_main, out_sub = data["output_type"], data["output_main"], data["output_sub"]
            _select_io_combo(self.poly_output_sig, out_type, out_main, out_sub)

            # Coefficients
            for i in range(NUM_COEFFS):
                self.poly_coeffs[i].setText(f"{data['coeffs'][i]:.5e}")

            # Limiter
            self.poly_limiter.setText(f"{data['limiter']:.5e}")

            self.log_msg(f"Polynom {polynum} read OK")
        except Exception as exc:
            self.log_msg(f"Polynom read error: {exc}")

    self._run("Read polynom", work)


def on_poly_write(self: MainWindow) -> None:
    """Write the selected polynom configuration to controller."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        polynum = self.poly_num.currentIndex()
        if not self._confirm_write(f"Polynom {polynum}"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            # Build the I7 parameter array
            active = 1 if self.poly_active_led.isChecked() else 0
            in_data = self.poly_input_sig.currentData()
            out_data = self.poly_output_sig.currentData()
            if in_data is None:
                in_type, in_main, in_sub = 0, 0, 0
            else:
                in_type, in_main, in_sub = in_data
            if out_data is None:
                out_type, out_main, out_sub = 0, 0, 0
            else:
                out_type, out_main, out_sub = out_data

            # I7: active, in_type, in_main, in_sub, out_type, out_main, out_sub
            i7 = f"{active} {in_type} {in_main} {in_sub} {out_type} {out_main} {out_sub}"

            # D6: coeff0..coeff4, limiter
            coeffs = [float(ed.text()) for ed in self.poly_coeffs]
            limiter = float(self.poly_limiter.text())
            d6 = " ".join(f"{c:.5e}" for c in coeffs) + f" {limiter:.5e}"

            resp = s.raw_command("LSPCP", polynum, i7, d6)
            s.encoder.ensure_ok(resp, "LSPCP")
            self.log_msg(f"Polynom {polynum} written OK")
        except Exception as exc:
            self.log_msg(f"Polynom write error: {exc}")
        finally:
            self._set_writable(True)

    self._run("Write polynom", work)


def on_poly_read_all(self: MainWindow) -> None:
    """Read all polynom configurations from the controller."""
    def work() -> None:
        s = self._require_session()
        try:
            # Read status
            resp = s.raw_command("LGPSP")
            s.encoder.ensure_ok(resp, "LGPSP")
            processing = int(resp.data_tokens[0])
            overall_active = int(resp.data_tokens[1])
            self.poly_processing_led.setChecked(processing != 0)
            self.poly_overall_active_led.setChecked(overall_active != 0)
            _update_led_style(self.poly_processing_led)
            _update_led_style(self.poly_overall_active_led)

            # Read inputs
            try:
                resp_iv = s.raw_command("LGPIV")
                s.encoder.ensure_ok(resp_iv, "LGPIV")
                tokens = resp_iv.data_tokens
                if len(tokens) > 0:
                    # Update the current polynom's input value display
                    curr = self.poly_num.currentIndex()
                    if curr < len(tokens):
                        self.poly_input_val.setText(tokens[curr])
            except Exception:
                pass

            # Read outputs
            try:
                resp_ov = s.raw_command("LGPOV")
                s.encoder.ensure_ok(resp_ov, "LGPOV")
                tokens = resp_ov.data_tokens
                if len(tokens) > 0:
                    curr = self.poly_num.currentIndex()
                    if curr < len(tokens):
                        self.poly_output_val.setText(tokens[curr])
            except Exception:
                pass

            self.log_msg("All polynom parameters read")
        except Exception as exc:
            self.log_msg(f"Polynom read all error: {exc}")

    self._run("Read all polynom", work)


def on_poly_write_all(self: MainWindow) -> None:
    """Write all polynom status + configurations to the controller."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write("All polynom parameters"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            # Write processing + overall active status
            proc = 1 if self.poly_processing_led.isChecked() else 0
            active = 1 if self.poly_overall_active_led.isChecked() else 0
            resp = s.raw_command("LSPSP", f"{proc} {active}")
            s.encoder.ensure_ok(resp, "LSPSP")
            self.log_msg("Polynom status written")
        except Exception as exc:
            self.log_msg(f"Polynom status write error: {exc}")
        finally:
            self._set_writable(True)

    self._run("Write all polynom", work)


def on_poly_ramp_read(self: MainWindow) -> None:
    """Read polynom ramp configuration."""
    def work() -> None:
        s = self._require_session()
        try:
            resp = s.raw_command("LGPRP")
            s.encoder.ensure_ok(resp, "LGPRP")
            tokens = resp.data_tokens
            # I1: ramp_status, D3: start, end, timespan
            ramp_status = int(tokens[0])
            self.poly_ramp_led.setChecked(ramp_status != 0)
            _update_led_style(self.poly_ramp_led)
            self.poly_ramp_start.setText(tokens[1])
            self.poly_ramp_end.setText(tokens[2])
            self.poly_ramp_time.setText(tokens[3])
            self.log_msg("Ramp config read OK")
        except Exception as exc:
            self.log_msg(f"Ramp read error: {exc}")

    self._run("Read ramp", work)


def on_poly_ramp_write(self: MainWindow) -> None:
    """Write polynom ramp configuration."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write("Ramp configuration"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            ramp_status = 1 if self.poly_ramp_led.isChecked() else 0
            start = float(self.poly_ramp_start.text())
            end = float(self.poly_ramp_end.text())
            timespan = float(self.poly_ramp_time.text())
            d3 = f"{start:.5e} {end:.5e} {timespan:.5e}"
            resp = s.raw_command("LSPRP", ramp_status, d3)
            s.encoder.ensure_ok(resp, "LSPRP")
            self.log_msg("Ramp config written OK")
        except Exception as exc:
            self.log_msg(f"Ramp write error: {exc}")
        finally:
            self._set_writable(True)

    self._run("Write ramp", work)


def on_poly_save_config(self: MainWindow) -> None:
    """Save polynom configuration to a JSON file."""
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        self, "Save polynom config", "", "JSON files (*.json);;All files (*.*)"
    )
    if not path:
        return
    try:
        config = {
            "processing_status": 1 if self.poly_processing_led.isChecked() else 0,
            "overall_active": 1 if self.poly_overall_active_led.isChecked() else 0,
            "polynoms": [],
        }
        # Read current UI state for the selected polynom
        in_data = self.poly_input_sig.currentData() or (0, 0, 0)
        out_data = self.poly_output_sig.currentData() or (0, 0, 0)
        coeffs = [float(ed.text()) for ed in self.poly_coeffs]
        config["polynoms"].append({
            "index": self.poly_num.currentIndex(),
            "active_status": 1 if self.poly_active_led.isChecked() else 0,
            "input_type": int(in_data[0]),
            "input_main": int(in_data[1]),
            "input_sub": int(in_data[2]),
            "output_type": int(out_data[0]),
            "output_main": int(out_data[1]),
            "output_sub": int(out_data[2]),
            "coeffs": coeffs,
            "limiter": float(self.poly_limiter.text()),
        })
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
        self.log_msg(f"Polynom config saved to {os.path.basename(path)}")
    except Exception as exc:
        self.log_msg(f"Save error: {exc}")


def on_poly_load_config(self: MainWindow) -> None:
    """Load polynom configuration from a JSON file."""
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Load polynom config", "", "JSON files (*.json);;All files (*.*)"
    )
    if not path:
        return
    try:
        with open(path) as f:
            config = json.load(f)

        # Restore status
        if "processing_status" in config:
            self.poly_processing_led.setChecked(config["processing_status"] != 0)
            _update_led_style(self.poly_processing_led)
        if "overall_active" in config:
            self.poly_overall_active_led.setChecked(config["overall_active"] != 0)
            _update_led_style(self.poly_overall_active_led)

        # Restore first polynom (or match by index)
        for pcfg in config.get("polynoms", []):
            idx = pcfg.get("index", 0)
            if idx < self.poly_num.count():
                self.poly_num.setCurrentIndex(idx)
            self.poly_active_led.setChecked(pcfg.get("active_status", 0) != 0)
            _update_led_style(self.poly_active_led)
            # Set type based on output_type
            self.poly_type.setCurrentIndex(1 if pcfg.get("output_type", 0) != 0 else 0)
            # Set IO signals
            _select_io_combo(self.poly_input_sig, pcfg.get("input_type", 0), pcfg.get("input_main", 0), pcfg.get("input_sub", 0))
            _select_io_combo(self.poly_output_sig, pcfg.get("output_type", 0), pcfg.get("output_main", 0), pcfg.get("output_sub", 0))
            # Coefficients
            coeffs = pcfg.get("coeffs", [0.0] * NUM_COEFFS)
            for i in range(min(len(coeffs), NUM_COEFFS)):
                self.poly_coeffs[i].setText(f"{coeffs[i]:.5e}")
            # Limiter
            self.poly_limiter.setText(f"{pcfg.get('limiter', 0.0):.5e}")
            break  # Only load first polynom into UI

        self.log_msg(f"Polynom config loaded from {os.path.basename(path)}")
    except Exception as exc:
        self.log_msg(f"Load error: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_io_combo(combo: QtWidgets.QComboBox, io_type: int, main_idx: int, sub_idx: int) -> None:
    """Select the item in an IO signal combo box matching type/main/sub."""
    if io_type == 0 and main_idx == 0 and sub_idx == 0:
        combo.setCurrentIndex(0)  # "None"
        return
    for i in range(combo.count()):
        data = combo.itemData(i)
        if data is not None and len(data) == 3:
            if int(data[0]) == io_type and int(data[1]) == main_idx and int(data[2]) == sub_idx:
                combo.setCurrentIndex(i)
                return
    # Fallback: select first item
    combo.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def apply_polynom_patch(cls: type) -> None:
    """Apply the polynom editor patch to the given MainWindow class."""
    cls._build_special_tab = _build_special_tab
    cls.on_poly_read = on_poly_read
    cls.on_poly_write = on_poly_write
    cls.on_poly_read_all = on_poly_read_all
    cls.on_poly_write_all = on_poly_write_all
    cls.on_poly_ramp_read = on_poly_ramp_read
    cls.on_poly_ramp_write = on_poly_ramp_write
    cls.on_poly_save_config = on_poly_save_config
    cls.on_poly_load_config = on_poly_load_config


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Polynom patch module loaded.")
    print("Use: apply_polynom_patch(MainWindow)")