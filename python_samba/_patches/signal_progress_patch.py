"""Patch for SignalContinueDisplayPage, ProgressDialog, and SaveLoadPage improvements.

All are module-level functions so they can be monkey-patched onto MainWindow.

Usage:
    from python_samba.ui.main_window import MainWindow
    from _patches.signal_progress_patch import (
        _build_special_tab,
        _build_saveload_tab,
        _on_timer_tick,
        _show_progress_dialog,
        _hide_progress_dialog,
        on_signal_continue_read,
        on_signal_continue_start,
        on_label_file_generate,
        on_label_file_load,
        on_si_unit_select,
        on_build_checksum,
        on_read_checksum,
    )
    MainWindow._build_special_tab = _build_special_tab
    MainWindow._build_saveload_tab = _build_saveload_tab
    MainWindow._on_timer_tick = _on_timer_tick
    MainWindow._show_progress_dialog = _show_progress_dialog
    MainWindow._hide_progress_dialog = _hide_progress_dialog
    MainWindow.on_signal_continue_read = on_signal_continue_read
    MainWindow.on_signal_continue_start = on_signal_continue_start
    MainWindow.on_label_file_generate = on_label_file_generate
    MainWindow.on_label_file_load = on_label_file_load
    MainWindow.on_si_unit_select = on_si_unit_select
    MainWindow.on_build_checksum = on_build_checksum
    MainWindow.on_read_checksum = on_read_checksum
"""

from __future__ import annotations

import os
import threading
import xml.etree.ElementTree as ET

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc

from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    IOSignalButton,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
    format_ui_number,
)
from python_samba.ui.main_window import SamTabWidget


# ---------------------------------------------------------------------------
# IO signal names — combined from SAMBA19xLabels (main_window.py top-level)
# ---------------------------------------------------------------------------
ALL_SIGNAL_NAMES = [
    # Velocity axes (6)
    "Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot",
    # Position axes (12)
    "Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
    "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
    # Pneumatic axes (3)
    "Ztpneu", "Yrpneu", "Xrpneu",
    # Velocity input 7 (8)
    "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "Z4FB",
    # Velocity input 8 (8)
    "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "X4FB", "Z4FB",
    # Velocity output (12)
    "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
    # ADC Input (32)
    "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
    "Xff", "Yff", "Zff",
    "Prox1", "Prox2", "Prox3", "ProxH1", "ProxH2", "ProxH3",
    "Xpos", "Xacc", "Ypos", "Yacc",
    "Y2FB", "X3FB", "X4FB", "Y4FB", "Z4FB",
    "Prox4", "ProxH4",
    "Auxiliary1", "Auxiliary2", "Auxiliary3", "Auxiliary4", "Auxiliary5",
    # DAC Output (20)
    "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
    "Valve1", "Valve2", "Valve3", "Valve4", "Valve5", "Valve6",
    "Diag0", "Diag1",
    # Filter stages
    "Fil1", "Fil2", "Fil3", "Fil4", "Fil5", "Fil6", "Fil7",
    # Motor names
    "M1", "M2", "M3", "M4", "M5", "M6",
    "M7", "M8", "M9", "M10", "M11", "M12",
    # Additional diagnostic signals
    "DiagSig0", "DiagSig1",
    "NoiseInjSig",
    "PerfMonSig",
    "SwitchSig",
    "TraceSig0", "TraceSig1",
]

# Deduplicate while preserving order
_SIGNAL_NAMES: list[str] = []
for name in ALL_SIGNAL_NAMES:
    if name not in _SIGNAL_NAMES:
        _SIGNAL_NAMES.append(name)

SIGNAL_NAMES = _SIGNAL_NAMES
NUM_SIGNALS = 16  # 16 monitor signal slots (matching C#)


# ===================================================================
# _build_special_tab  — replaces MainWindow._build_special_tab
# Adds "Signal Display" sub-tab for continuous signal monitoring
# ===================================================================
def _build_special_tab(win) -> None:
    """Special tab — safety, ZMS, polynom, signal display (from SAMBA19xUI)."""
    tabs = SamTabWidget()
    tabs.currentChanged.connect(win._on_sub_tab_changed)

    # ================================================================
    # Safety / Earthquake monitoring
    # ================================================================
    w = QtWidgets.QWidget()
    wl = QtWidgets.QVBoxLayout(w)
    wl.setContentsMargins(6, 4, 6, 4)

    g_safety = GroupPanel("Safety & Earthquake Monitoring")
    sf = QtWidgets.QFormLayout(g_safety)
    win.safety_earthquake = RockerButton("On", "Off")
    win.safety_temp_sensors = RockerButton("On", "Off")
    win.safety_horiz_gain = SciEdit("1.00000e+000")
    win.safety_vert_gain = SciEdit("1.00000e+000")
    win.safety_ref_temp = SciEdit("2.50000e+001")
    win.safety_status = QtWidgets.QLabel("--")
    sf.addRow("Earthquake monitoring:", win.safety_earthquake)
    sf.addRow("Use temp sensors:", win.safety_temp_sensors)
    sf.addRow("Horizontal gain:", win.safety_horiz_gain)
    sf.addRow("Vertical gain:", win.safety_vert_gain)
    sf.addRow("Reference temp:", win.safety_ref_temp)
    sf.addRow("Status:", win.safety_status)
    wl.addWidget(g_safety)

    g_amplifier = GroupPanel("Amplifier Events")
    amp_grid = QtWidgets.QGridLayout(g_amplifier)
    win.safety_amp_leds: list[LedIndicator] = []
    for i in range(12):
        led = LedIndicator(10)
        win.safety_amp_leds.append(led)
        amp_grid.addWidget(led, i // 4, i % 4)
        amp_grid.addWidget(QtWidgets.QLabel(f"Motor {i+1}"), i // 4 + 1, i % 4)
    wl.addWidget(g_amplifier)

    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_r.clicked.connect(win.on_safety_read)
    act.addWidget(btn_r)
    act.addStretch(1)
    wl.addLayout(act)
    wl.addStretch(1)
    tabs.addTab(w, "Safety")

    # ================================================================
    # ZMS (Zeiss Merity Safety)
    # ================================================================
    zms = QtWidgets.QWidget()
    zl = QtWidgets.QVBoxLayout(zms)
    zl.setContentsMargins(6, 4, 6, 4)

    g_zms = GroupPanel("ZMS Stability Monitoring")
    zf = QtWidgets.QFormLayout(g_zms)
    win.zms_vibration = QtWidgets.QLabel("--")
    win.zms_position = QtWidgets.QLabel("--")
    win.zms_rms = QtWidgets.QLabel("--")
    win.zms_axis = QtWidgets.QLabel("--")
    win.zms_thresholds: list[SciEdit] = []
    for i in range(6):
        ed = SciEdit("1.00000e+000")
        win.zms_thresholds.append(ed)
    zf.addRow("Vibration status:", win.zms_vibration)
    zf.addRow("Position status:", win.zms_position)
    zf.addRow("Last failed RMS:", win.zms_rms)
    zf.addRow("Last failed axis:", win.zms_axis)
    for i in range(6):
        zf.addRow(f"Threshold axis {i+1}:", win.zms_thresholds[i])
    zl.addWidget(g_zms)

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
    # Polynom
    # ================================================================
    poly = QtWidgets.QWidget()
    pl = QtWidgets.QVBoxLayout(poly)
    pl.setContentsMargins(6, 4, 6, 4)

    g_poly = GroupPanel("Polynom Configuration")
    pf = QtWidgets.QFormLayout(g_poly)
    win.poly_num = QtWidgets.QComboBox()
    win.poly_num.addItems([f"Polynom {i+1}" for i in range(19)])
    win.poly_type = QtWidgets.QComboBox()
    win.poly_type.addItems(["Input", "Output"])
    win.poly_coeffs: list[SciEdit] = []
    for i in range(5):
        ed = SciEdit("0.00000e+000")
        win.poly_coeffs.append(ed)
    pf.addRow("Polynom:", win.poly_num)
    pf.addRow("Type:", win.poly_type)
    for i in range(5):
        pf.addRow(f"Coeff {i+1}:", win.poly_coeffs[i])
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

    # ================================================================
    # Signal Display (matching C# SignalContinueDisplayPage)
    # ================================================================
    sig_page = _build_signal_display_page(win)
    tabs.addTab(sig_page, "Signal Display")

    win.main_tabs.addTab(tabs, "Special")


# ===================================================================
# _build_signal_display_page  — called from _build_special_tab
# ===================================================================
def _build_signal_display_page(win) -> QtWidgets.QWidget:
    """Build the Signal Continue Display page (like C# SignalContinueDisplayPage).

    Shows 16 signal selector combo boxes with real-time value displays,
    plus Save/Load buttons for signal settings.
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    # -- Signal selector grid --
    g_sig = GroupPanel("Monitor Signals (16 channels)")
    grid = QtWidgets.QGridLayout(g_sig)
    grid.setSpacing(4)

    # Column headers
    grid.addWidget(QtWidgets.QLabel("#"), 0, 0)
    grid.addWidget(QtWidgets.QLabel("Signal Name"), 0, 1)
    grid.addWidget(QtWidgets.QLabel("Current Value"), 0, 2)

    win.sig_selectors: list[QtWidgets.QComboBox] = []
    win.sig_values: list[QtWidgets.QLabel] = []

    for i in range(NUM_SIGNALS):
        row = i + 1
        lbl = QtWidgets.QLabel(f"{i}:")
        lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        grid.addWidget(lbl, row, 0)

        cb = QtWidgets.QComboBox()
        cb.addItem("-- None --")  # index 0 = none
        cb.addItems(SIGNAL_NAMES)
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        cb.currentIndexChanged.connect(
            lambda idx, n=i, c=cb: _on_sig_selector_changed(win, n, idx, c)
        )
        win.sig_selectors.append(cb)
        grid.addWidget(cb, row, 1)

        val = QtWidgets.QLabel("--")
        val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        val.setMinimumWidth(100)
        val.setStyleSheet(
            "background-color: #f0f0f0; color: #202020; "
            "padding: 2px 6px; border: 1px solid #c0c0c0; "
            "font-family: Consolas, monospace; font-size: 11px;"
        )
        win.sig_values.append(val)
        grid.addWidget(val, row, 2)

    root.addWidget(g_sig)

    # -- Action buttons --
    act = QtWidgets.QHBoxLayout()

    btn_read = FlatPush("Read Signals")
    btn_read.clicked.connect(win.on_signal_continue_read)
    act.addWidget(btn_read)

    btn_start = FlatPush("Start Monitoring")
    btn_start.clicked.connect(win.on_signal_continue_start)
    act.addWidget(btn_start)

    btn_save = FlatPush("Save Settings...")
    btn_save.clicked.connect(lambda: _on_sig_save_settings(win))
    act.addWidget(btn_save)

    btn_load = FlatPush("Load Settings...")
    btn_load.clicked.connect(lambda: _on_sig_load_settings(win))
    act.addWidget(btn_load)

    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)

    # Track whether auto-monitoring is active
    win._sig_monitoring_active = False

    return w


# ===================================================================
# Signal Display helpers
# ===================================================================
def _on_sig_selector_changed(win, num: int, selected, cb: QtWidgets.QWidget) -> None:
    """Called when a signal selector combo box changes selection.
    Sends the new signal selection to the controller (like C# SetMonitorSignal).
    """
    if isinstance(cb, IOSignalButton):
        try:
            signal_data = tuple(int(value) for value in selected[:3])
        except (TypeError, ValueError):
            signal_data = cb.io_tokens()
        signal_name = cb.text().strip()
    else:
        idx = int(selected)
        if idx <= 0:
            return  # "-- None --" selected
        signal_name = cb.currentText().strip()
        if not signal_name or signal_name == "-- None --":
            return
        data = cb.currentData()
        signal_data = (
            tuple(int(value) for value in data[:3])
            if isinstance(data, (tuple, list)) and len(data) >= 3
            else (0, idx - 1, 0)
        )
    try:
        if win.session and win.session.connected:
            if not win._confirm_write(
                f"Monitor signal {num} = {signal_name} {signal_data}"
            ):
                return
            if win.gate is not None:
                win.gate.take_snapshot()
            win._set_writable(True)
            try:
                win.session.set_monitor_signal(num, *signal_data)
            finally:
                win._set_writable(True)
            win.log_msg(f"Monitor signal {num} set to {signal_name}")
    except Exception as exc:
        win.log_msg(f"ERROR setting monitor signal {num}: {exc}")


def _on_sig_save_settings(win) -> None:
    """Save signal selector settings to a file (like C# SavSignalsSettingBtn_Click)."""
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        win, "Save Monitor Signal Settings", "",
        "SAMBA19x Signal files (*.sigsig);;All (*.*)"
    )
    if not path:
        return
    try:
        root = ET.Element("MonitorSignalSettings")
        for i, cb in enumerate(win.sig_selectors):
            sig_elem = ET.SubElement(root, f"Signal{i}")
            if isinstance(cb, IOSignalButton):
                io_type, main, sub = cb.io_tokens()
                sig_elem.set("Type", str(io_type))
                sig_elem.set("MainIndex", str(main))
                sig_elem.set("SubIndex", str(sub))
                sig_elem.text = cb.text()
            else:
                sig_elem.text = cb.currentText()
        tree = ET.ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        win.log_msg(f"Signal settings saved to {path}")
    except Exception as exc:
        win.log_msg(f"ERROR saving signal settings: {exc}")


def _on_sig_load_settings(win) -> None:
    """Load signal selector settings from a file (like C# OpenSignalsSettingBtn_Click)."""
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        win, "Load Monitor Signal Settings", "",
        "SAMBA19x Signal files (*.sigsig);;All (*.*)"
    )
    if not path:
        return
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        for i, cb in enumerate(win.sig_selectors):
            sig_elem = root.find(f"Signal{i}")
            if sig_elem is not None and sig_elem.text:
                if isinstance(cb, IOSignalButton) and all(
                    key in sig_elem.attrib for key in ("Type", "MainIndex", "SubIndex")
                ):
                    tokens = tuple(
                        int(sig_elem.get(key, "0"))
                        for key in ("Type", "MainIndex", "SubIndex")
                    )
                    cb.set_io_signal(tokens)
                elif isinstance(cb, IOSignalButton):
                    for action in cb._leaf_actions:
                        if action.text() == sig_elem.text:
                            cb.set_io_signal(action.data())
                            break
                else:
                    idx = cb.findText(sig_elem.text)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
                    else:
                        cb.setEditText(sig_elem.text)
        win.log_msg(f"Signal settings loaded from {path}")
    except Exception as exc:
        win.log_msg(f"ERROR loading signal settings: {exc}")


# ===================================================================
# _on_timer_tick  — replaces MainWindow._on_timer_tick
# Adds signal display value updates
# ===================================================================
def _on_timer_tick_sync(win) -> None:
    """1-second refresh timer — updates loop status LEDs and signal display values."""
    if win.session and win.session.connected:
        main = win.main_tabs.tabText(win.main_tabs.currentIndex())
        sub = win._current_subtab_text()
        try:
            loop = win.session.get_loop_status()
            if hasattr(win, "sb_loop"):
                win.sb_loop.setText(f"  Loop: {loop.individual:X}/{loop.system:X}  ")
            win._refresh_status_loop_state(
                loop,
                include_axis_status=main == "Status" and sub == "Status",
            )
        except Exception as exc:
            win._report_live_refresh_error("loop", exc)
            return

        if (
            main == "Controller"
            and sub == "System Setting"
            and hasattr(win, "_refresh_system_loop_configuration")
        ):
            try:
                win._refresh_system_loop_configuration()
            except Exception as exc:
                win._report_live_refresh_error("loop configuration", exc)

        # The old page polls monitor values while it is visible.  Do not send
        # DGMSV once per second from unrelated tuning pages; that needlessly
        # consumes the serial link and was a major source of perceived lag.
        try:
            if main == "Position" and sub in {"Tuning", "Proxy Adjustment"}:
                win._refresh_position_live_state()
            elif main == "Controller" and sub == "Motor Protection":
                # MotorProtectionPage.UpdateStates() in the legacy UI polls
                # BGMPV/BGMPS (and optional LGPSL) once per second.  Keep the
                # same lightweight live path here; the full threshold/offset
                # configuration remains a page-entry/manual read.
                win._refresh_motor_protection_live_state(loop)
            elif main == "Status" and sub == "DigIO Status":
                win._on_digio_read()
            elif main == "Pneumatic" and hasattr(
                win, "_refresh_pneumatic_live_state"
            ):
                win._refresh_pneumatic_live_state(loop)
            elif main == "Pneum. SFF" and hasattr(
                win, "_set_pff_individual_loop_buttons"
            ):
                _position, pneumatic, _digital_in, _digital_out = (
                    win.session.get_pos_pneum_digital_status()
                )
                win._set_pff_individual_loop_buttons(pneumatic)
        except Exception as exc:
            win._report_live_refresh_error(f"{main}/{sub}", exc)

        monitor_visible = main == "Status" and sub == "Signals Display"
        if getattr(win, "_sig_monitoring_active", False) or monitor_visible:
            try:
                _update_signal_display_values(win)
            except Exception:
                pass


class _LiveRefreshBridge(QtCore.QObject):
    finished = QtCore.Signal(object, object, object, object)


def _is_remote_server_session(session) -> bool:
    info = getattr(session, "info", None)
    return getattr(info, "backend", "") == "server"


def _live_refresh_context(win) -> dict[str, object]:
    main = win.main_tabs.tabText(win.main_tabs.currentIndex())
    sub = win._current_subtab_text()
    monitor_visible = main == "Status" and sub == "Signals Display"
    monitor_count = NUM_SIGNALS if (
        getattr(win, "_sig_monitoring_active", False) or monitor_visible
    ) else 0
    include_power_supply = False
    if main == "Controller" and sub == "Motor Protection" and hasattr(
        win, "ps_current_limit"
    ):
        features = getattr(win, "_controller_features", None)
        include_power_supply = features is None or "PSUCL" in features
    include_axis_status = (
        (main == "Status" and sub in {"Status", "DigIO Status"})
        or main in {"Pneumatic", "Pneum. SFF"}
    )
    return {
        "main": main,
        "sub": sub,
        "include_switch_conditions": not getattr(
            win, "_switch_config_loaded", False
        ),
        "include_axis_status": include_axis_status,
        "include_controller_config": main == "Controller"
        and sub == "System Setting",
        "proximity_count": getattr(win, "_proximity_count", 6)
        if main == "Position" and sub in {"Tuning", "Proxy Adjustment"}
        else 0,
        "include_motor": main == "Controller" and sub == "Motor Protection",
        "include_power_supply": include_power_supply,
        "include_pneumatic": main == "Pneumatic",
        "monitor_count": monitor_count,
    }


def _remote_live_reader(win, source_session):
    reader = getattr(win, "_remote_live_session", None)
    source = getattr(win, "_remote_live_source_session", None)
    if reader is not None and source is source_session and reader.connected:
        return reader
    if reader is not None:
        try:
            reader.close()
        except Exception:
            pass
    open_reader = getattr(source_session, "open_background_reader", None)
    candidate = open_reader("python_samba-live-refresh") if callable(open_reader) else None
    reader = candidate or source_session
    win._remote_live_session = reader
    win._remote_live_source_session = source_session
    return reader


def _on_timer_tick(win) -> None:
    """Keep remote polling off the Qt thread and coalesce overdue ticks."""
    session = getattr(win, "session", None)
    if not session or not session.connected:
        return
    snapshot_reader = getattr(session, "get_live_refresh_snapshot", None)
    if not _is_remote_server_session(session) or not callable(snapshot_reader):
        _on_timer_tick_sync(win)
        return
    if getattr(win, "_remote_live_refresh_inflight", False):
        return

    bridge = getattr(win, "_remote_live_refresh_bridge", None)
    if bridge is None:
        bridge = _LiveRefreshBridge(win)
        bridge.finished.connect(win._on_remote_live_refresh_finished)
        win._remote_live_refresh_bridge = bridge
    context = _live_refresh_context(win)
    win._remote_live_refresh_inflight = True

    def target() -> None:
        payload = None
        error = None
        try:
            reader_session = _remote_live_reader(win, session)
            payload = reader_session.get_live_refresh_snapshot(
                include_switch_conditions=bool(
                    context["include_switch_conditions"]
                ),
                include_axis_status=bool(context["include_axis_status"]),
                include_controller_config=bool(
                    context["include_controller_config"]
                ),
                proximity_count=int(context["proximity_count"]),
                include_motor=bool(context["include_motor"]),
                include_power_supply=bool(context["include_power_supply"]),
                include_pneumatic=bool(context["include_pneumatic"]),
                monitor_count=int(context["monitor_count"]),
            )
        except BaseException as exc:
            error = exc
        try:
            bridge.finished.emit(session, context, payload, error)
        except RuntimeError:
            pass

    threading.Thread(
        target=target,
        name="SambaRemoteLiveRefresh",
        daemon=True,
    ).start()


@QtCore.Slot(object, object, object, object)
def _on_remote_live_refresh_finished(win, source_session, context, payload, error) -> None:
    win._remote_live_refresh_inflight = False
    if getattr(win, "session", None) is not source_session or not source_session.connected:
        reader = getattr(win, "_remote_live_session", None)
        if reader is not None and reader is not source_session:
            try:
                reader.close()
            except Exception:
                pass
        win._remote_live_session = None
        win._remote_live_source_session = None
        return
    if error is not None:
        win._report_live_refresh_error("remote live refresh", error)
        return
    if not isinstance(payload, dict):
        return

    from python_samba.ui.main_window import _parse_protocol_int

    loop = payload.get("loop")
    if loop is None:
        return
    if hasattr(win, "sb_loop"):
        win.sb_loop.setText(f"  Loop: {loop.individual:X}/{loop.system:X}  ")
    switch = payload.get("switch_status", [])
    switch_word = _parse_protocol_int(switch[0]) if switch else 0
    if not getattr(win, "_switch_config_loaded", False):
        conditions = payload.get("switch_conditions", [])
        if len(conditions) > 3:
            win._switch_config = _parse_protocol_int(conditions[3])
            win._switch_config_loaded = True
    status_words = (
        payload.get("axis_status") if context.get("include_axis_status") else None
    )
    win._update_status_loop_widgets(
        loop,
        switch_word,
        status_words,
        getattr(win, "_switch_config", 0),
    )

    main = win.main_tabs.tabText(win.main_tabs.currentIndex())
    sub = win._current_subtab_text()
    if main != context.get("main") or sub != context.get("sub"):
        return
    if "controller_config" in payload and hasattr(
        win, "_apply_system_loop_configuration"
    ):
        win._apply_system_loop_configuration(payload["controller_config"])
    if "proximity_values" in payload:
        win._apply_position_live_values(payload["proximity_values"])
    if "motor_power" in payload:
        win._apply_motor_protection_live_snapshot(payload, loop)
    if "pneumatic_axes_status" in payload and hasattr(
        win, "_apply_pneumatic_live_snapshot"
    ):
        win._apply_pneumatic_live_snapshot(payload, loop)
    if main == "Status" and sub == "DigIO Status" and status_words:
        _position, _pneumatic, input_word, output_word = status_words
        win._apply_digio_words(int(input_word), int(output_word))
    if main == "Pneum. SFF" and status_words and hasattr(
        win, "_set_pff_individual_loop_buttons"
    ):
        win._set_pff_individual_loop_buttons(int(status_words[1]))
    if "monitor_values" in payload:
        _apply_signal_display_values(win, payload["monitor_values"])


def _apply_signal_display_values(win, values) -> None:
    for index, value in enumerate(values[: len(getattr(win, "sig_values", ())) ]):
        win.sig_values[index].setText(format_ui_number(value))


def _update_signal_display_values(win) -> None:
    """Fetch current monitor signal values from controller and update display labels.

    Corresponds to C# SignalContinueDisplayPage.UpdateStates() which calls
    Controller.tcmfd.GetMonitorSignalValues(0, 15).
    """
    if not hasattr(win, "sig_values") or not win.sig_values:
        return
    if not win.session or not win.session.connected:
        return
    try:
        values = win.session.get_monitor_values(0, NUM_SIGNALS - 1)
        _apply_signal_display_values(win, values)
    except Exception:
        # If DGMSV fails, try individual DGMOS for each signal
        for i in range(len(win.sig_values)):
            try:
                resp = win.session.get_monitor_signal(i)
                if resp and len(resp) > 0:
                    win.sig_values[i].setText(format_ui_number(resp[-1]))
            except Exception:
                pass


# ===================================================================
# on_signal_continue_read  — handler for "Read Signals" button
# ===================================================================
def on_signal_continue_read(win) -> None:
    """Read current monitor signal configuration from controller.

    Corresponds to C# SignalContinueDisplayPage.UpdatePage() which calls
    GetMonitorSignal(i) for all 16 channels.
    """
    def work() -> None:
        s = win._require_session()
        for i in range(NUM_SIGNALS):
            try:
                resp = s.get_monitor_signal(i)
                if resp and len(resp) >= 3:
                    cb = win.sig_selectors[i]
                    tokens = tuple(int(value) for value in resp[:3])
                    if isinstance(cb, IOSignalButton):
                        cb.set_io_signal(tokens)
                        labels = getattr(win, "sig_name_labels", [])
                        if i < len(labels):
                            labels[i].setText(cb.text())
                    else:
                        for index in range(cb.count()):
                            data = cb.itemData(index)
                            if isinstance(data, (tuple, list)) and tuple(data[:3]) == tokens:
                                cb.setCurrentIndex(index)
                                break
            except Exception as exc:
                win.log_msg(f"ERROR reading monitor signal {i}: {exc}")
        win.log_msg("Monitor signal configuration read from controller")
    win._run("Read Signal Config", work)


# ===================================================================
# on_signal_continue_start  — handler for "Start Monitoring" button
# ===================================================================
def on_signal_continue_start(win) -> None:
    """Start/stop continuous monitoring of signals.

    Toggles monitoring mode. When active, signal values are updated
    on each 1-second timer tick.
    """
    if not win.session or not win.session.connected:
        QtWidgets.QMessageBox.warning(win, "Not connected", "Connect to a controller first")
        return

    win._sig_monitoring_active = not win._sig_monitoring_active
    if win._sig_monitoring_active:
        # Read all signal settings first
        on_signal_continue_read(win)
        win.log_msg("Signal monitoring started (updates every 1s)")
        # Find the start button and update its text
        _update_monitoring_button_text(win, True)
    else:
        win.log_msg("Signal monitoring stopped")
        _update_monitoring_button_text(win, False)


def _update_monitoring_button_text(win, active: bool) -> None:
    """Update the Start/Stop Monitoring button text."""
    # Walk the signal display page to find the button
    for i in range(win.main_tabs.count()):
        tab = win.main_tabs.widget(i)
        if tab and hasattr(tab, "findChild"):
            btns = tab.findChildren(QtWidgets.QPushButton)
            for btn in btns:
                if "Monitoring" in btn.text():
                    btn.setText("Stop Monitoring" if active else "Start Monitoring")
                    if active:
                        btn.setStyleSheet("background-color: #fef0f0; font-weight: bold;")
                    else:
                        btn.setStyleSheet("")
                    return


# ===================================================================
# ProgressDialog — simple progress dialog for save/load operations
# ===================================================================
def _show_progress_dialog(win, title: str = "Operation in progress...") -> QtWidgets.QDialog:
    """Show a modal progress dialog with progress bar, status text, and cancel button.

    Corresponds to C# ProgressDialog (ProgressWindow).
    Returns the dialog so the caller can update progress value/info.
    """
    dlg = QtWidgets.QDialog(win)
    dlg.setWindowTitle("Progress")
    dlg.setModal(True)
    dlg.setMinimumWidth(400)
    dlg.resize(420, 200)

    root = QtWidgets.QVBoxLayout(dlg)
    root.setContentsMargins(12, 10, 12, 10)
    root.setSpacing(8)

    # Info label
    info_lbl = QtWidgets.QLabel(title)
    info_lbl.setWordWrap(True)
    info_lbl.setStyleSheet("font-weight: 600;")
    root.addWidget(info_lbl)
    dlg._info_lbl = info_lbl

    # Progress bar
    pbar = QtWidgets.QProgressBar(dlg)
    pbar.setRange(0, 100)
    pbar.setValue(0)
    pbar.setTextVisible(True)
    root.addWidget(pbar)
    dlg._pbar = pbar

    # Progress info area (scrollable)
    progress_info = QtWidgets.QTextEdit(dlg)
    progress_info.setReadOnly(True)
    progress_info.setMaximumHeight(80)
    progress_info.setVisible(False)
    root.addWidget(progress_info)
    dlg._progress_info = progress_info

    # Cancel button
    btn_cancel = QtWidgets.QPushButton("Cancel")
    btn_cancel.clicked.connect(dlg.reject)
    root.addWidget(btn_cancel, alignment=QtCore.Qt.AlignRight)

    # Store helper methods
    def progress_value(value: float) -> None:
        """Add value to the progress bar (like C# ProgressValue which adds, not sets)."""
        if pbar.value() + value <= pbar.maximum():
            pbar.setValue(int(pbar.value() + value))
        else:
            pbar.setValue(pbar.maximum())
        QtWidgets.QApplication.processEvents()

    def progress_info_value(value: str) -> None:
        """Append text to the progress info area (like C# ProgressInfoValue)."""
        progress_info.setVisible(True)
        progress_info.append(value.strip())
        scrollbar = progress_info.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
        QtWidgets.QApplication.processEvents()

    def reset_progress() -> None:
        pbar.setValue(0)
        progress_info.clear()
        progress_info.setVisible(False)

    dlg.progress_value = progress_value
    dlg.progress_info_value = progress_info_value
    dlg.reset_progress = reset_progress

    dlg.show()
    QtWidgets.QApplication.processEvents()
    return dlg


def _hide_progress_dialog(win, dlg: QtWidgets.QDialog | None) -> None:
    """Close and clean up a progress dialog."""
    if dlg is not None:
        try:
            dlg.reset_progress()
            dlg.accept()
            dlg.close()
        except Exception:
            pass


# ===================================================================
# _build_saveload_tab  — replaces MainWindow._build_saveload_tab
# Adds progress bar, label file, SI unit, checksum, protection LED
# ===================================================================
def _build_saveload_tab(win) -> None:
    """Save/Load configuration page — with progress bar, label file, checksum, etc.

    Corresponds to C# SaveLoadPage with all UI elements.
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    # ============================================================
    # Configuration File section
    # ============================================================
    g = GroupPanel("Configuration File (.SAMBA19x_Config)")
    form = QtWidgets.QFormLayout(g)

    win.setup_file_lbl = QtWidgets.QLabel("No file selected")
    win.setup_file_lbl.setWordWrap(True)
    form.addRow("Setup file:", win.setup_file_lbl)

    win.si_file_lbl = QtWidgets.QLabel("No file selected")
    form.addRow("SI file:", win.si_file_lbl)

    win.nvram_cs_fw = QtWidgets.QLabel("-")
    win.nvram_cs_mon = QtWidgets.QLabel("-")
    win.nvram_cs_cfg = QtWidgets.QLabel("-")
    form.addRow("Firmware checksum:", win.nvram_cs_fw)
    form.addRow("Monitor checksum:", win.nvram_cs_mon)
    form.addRow("Config checksum:", win.nvram_cs_cfg)

    # Hidden fields for advanced use
    win.nvram_fs = SciSpin()
    win.nvram_fs.setRange(0, 10000)
    win.nvram_fs.setValue(1836)
    win.nvram_cfg = SciEdit()
    win.nvram_adcs = QtWidgets.QSpinBox()
    win.nvram_adcs.setRange(0, 32)
    form.addRow("Sample freq:", win.nvram_fs)
    form.addRow("Config:", win.nvram_cfg)
    form.addRow("ADC set num:", win.nvram_adcs)
    win.nvram_fs.setVisible(False)
    win.nvram_cfg.setVisible(False)
    win.nvram_adcs.setVisible(False)

    root.addWidget(g)

    # ============================================================
    # Label File section (matching C# LabelFileGenerateBtn, LabelFileLoadBtn, UseDefaultLabelBtn)
    # ============================================================
    g_label = GroupPanel("Label File (.SAMBA19xLabel)")
    label_form = QtWidgets.QFormLayout(g_label)

    win.label_path_lbl = QtWidgets.QLabel("No file selected")
    win.label_path_lbl.setWordWrap(True)
    label_form.addRow("Label file:", win.label_path_lbl)

    label_btn_row = QtWidgets.QHBoxLayout()
    btn_gen = FlatPush("Generate Label File...")
    btn_gen.clicked.connect(win.on_label_file_generate)
    label_btn_row.addWidget(btn_gen)

    btn_load = FlatPush("Load Label File...")
    btn_load.clicked.connect(win.on_label_file_load)
    label_btn_row.addWidget(btn_load)

    btn_def = FlatPush("Use Default Labels")
    btn_def.clicked.connect(lambda: _on_use_default_label(win))
    label_btn_row.addWidget(btn_def)

    label_btn_row.addStretch(1)
    label_form.addRow(label_btn_row)

    root.addWidget(g_label)

    # ============================================================
    # SI Unit file section (matching C# SetSiUnitsBtn)
    # ============================================================
    g_si = GroupPanel("SI Units")
    si_form = QtWidgets.QFormLayout(g_si)
    win.si_unit_path_lbl = QtWidgets.QLabel("No file selected")
    si_form.addRow("SI unit file:", win.si_unit_path_lbl)
    btn_si = FlatPush("Select SI Unit File...")
    btn_si.clicked.connect(win.on_si_unit_select)
    si_form.addRow(btn_si)
    root.addWidget(g_si)

    # ============================================================
    # NVRAM Checksum section (matching C# BuildCheckSumBtn, ReadCheckSumBtn)
    # ============================================================
    g_cs = GroupPanel("NVRAM Checksum")
    cs_form = QtWidgets.QFormLayout(g_cs)

    win.protection_led = LedIndicator(14)
    cs_form.addRow("Protection Status:", win.protection_led)

    cs_btn_row = QtWidgets.QHBoxLayout()
    btn_build = FlatPush("Build Checksum")
    btn_build.clicked.connect(win.on_build_checksum)
    cs_btn_row.addWidget(btn_build)

    btn_read_cs = FlatPush("Read Checksum")
    btn_read_cs.clicked.connect(win.on_read_checksum)
    cs_btn_row.addWidget(btn_read_cs)

    cs_btn_row.addStretch(1)
    cs_form.addRow(cs_btn_row)

    root.addWidget(g_cs)

    # ============================================================
    # Progress section (hidden by default, shown during operations)
    # ============================================================
    g_progress = GroupPanel("Progress")
    progress_layout = QtWidgets.QVBoxLayout(g_progress)

    win.progress_bar = QtWidgets.QProgressBar()
    win.progress_bar.setRange(0, 100)
    win.progress_bar.setValue(0)
    progress_layout.addWidget(win.progress_bar)

    win.progress_txt = QtWidgets.QLabel("")
    win.progress_txt.setWordWrap(True)
    win.progress_txt.setStyleSheet("color: #404040; font-size: 11px;")
    progress_layout.addWidget(win.progress_txt)

    btn_cancel = FlatPush("Cancel")
    btn_cancel.clicked.connect(lambda: _on_progress_cancel(win))
    progress_layout.addWidget(btn_cancel, alignment=QtCore.Qt.AlignRight)

    win.progress_bdr = g_progress  # reference to the group panel
    win.progress_bar.setVisible(False)
    win.progress_txt.setVisible(False)
    btn_cancel.setVisible(False)
    win._progress_cancel = False

    root.addWidget(g_progress)

    # ============================================================
    # Action buttons row
    # ============================================================
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Load setup file...", win.on_setup_load_file),
        ("Apply file to controller...", win.on_setup_apply_file),
        ("Save setup file...", win.on_setup_save_file),
        ("Read checksums", win.on_nvram_checksums),
        ("Save to NVRAM...", lambda: win.on_nvram("save")),
        ("Restore from NVRAM...", lambda: win.on_nvram("restore")),
        ("Clear NVRAM...", lambda: win.on_nvram("clear")),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)

    win.main_tabs.addTab(w, "Save/Load")


# ===================================================================
# Screenshot-oriented Save/Load layout
# ===================================================================

def _build_saveload_tab_reference(win) -> None:
    """Build the single Save/Load sub-page from the supplied SAMBA UI."""
    from python_samba.ui.main_window import SamTabWidget, SidebarLoopButton

    tabs = SamTabWidget()
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(14, 4, 6, 4)
    root.setSpacing(10)

    top = QtWidgets.QHBoxLayout()
    top.setSpacing(12)
    nvram = GroupPanel("Save/Restore/Clear NVRAM")
    nvram.setFixedSize(485, 135)
    win.nvram_group = nvram
    row = QtWidgets.QGridLayout(nvram)
    # QGroupBox reserves its title strip above contentsRect().  The previous
    # 14/10 vertical margins left less room than the Protection label + LED,
    # so the LED crossed the lower frame and also compressed the three action
    # buttons.  Keep the reference height and use the actual content area.
    row.setContentsMargins(16, 0, 16, 0)
    row.setHorizontalSpacing(10)
    row.setVerticalSpacing(0)
    win.nvram_save_button = FlatPush("⇩\nSave")
    win.nvram_restore_button = FlatPush("⇧\nRestore")
    win.nvram_clear_button = FlatPush("×\nClear")
    win.nvram_save_button.clicked.connect(lambda: win.on_nvram("save"))
    win.nvram_restore_button.clicked.connect(lambda: win.on_nvram("restore"))
    win.nvram_clear_button.clicked.connect(lambda: win.on_nvram("clear"))
    for column, button in enumerate((
        win.nvram_save_button,
        win.nvram_restore_button,
        win.nvram_clear_button,
    )):
        button.setFixedSize(88, 82)
        row.addWidget(button, 0, column, 2, 1, alignment=QtCore.Qt.AlignVCenter)
    protection_label = QtWidgets.QLabel("Protection")
    protection_label.setAlignment(QtCore.Qt.AlignCenter)
    row.addWidget(protection_label, 0, 3, alignment=QtCore.Qt.AlignCenter)
    win.protection_led = SidebarLoopButton()
    win.protection_led.setFixedSize(68, 66)
    win.protection_led.setToolTip("NVRAM Write/Clear protection")
    win.protection_led.clicked.connect(lambda: _on_protection_led_clicked(win))
    row.addWidget(win.protection_led, 1, 3, alignment=QtCore.Qt.AlignCenter)
    win._nvram_protected = True
    _sync_nvram_protection_controls(win, protected=True, connected=False)
    top.addWidget(nvram)

    files = GroupPanel("Save/Load File")
    files.setFixedSize(390, 135)
    file_row = QtWidgets.QHBoxLayout(files)
    win.setup_save_file_button = FlatPush("Controller  →  Save File")
    win.setup_load_file_button = FlatPush("Open File  →  Controller")
    win.setup_save_file_button.setFixedSize(175, 82)
    win.setup_load_file_button.setFixedSize(175, 82)
    win.setup_save_file_button.clicked.connect(win.on_setup_save_file)
    win.setup_load_file_button.clicked.connect(win.on_setup_load_file)
    file_row.addWidget(win.setup_save_file_button)
    file_row.addWidget(win.setup_load_file_button)
    top.addWidget(files)
    top.addStretch(1)
    root.addLayout(top)

    checksums = GroupPanel("NVRAM Areas Check Sum Information")
    checksums.setFixedSize(885, 435)
    check = QtWidgets.QGridLayout(checksums)
    check.setContentsMargins(16, 18, 16, 16)
    check.addWidget(QtWidgets.QLabel("Saved"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    check.addWidget(QtWidgets.QLabel("Actual"), 0, 2, alignment=QtCore.Qt.AlignCenter)
    check.addWidget(QtWidgets.QLabel("Status"), 0, 3, alignment=QtCore.Qt.AlignCenter)
    def readonly_field(text: str) -> QtWidgets.QLineEdit:
        field = QtWidgets.QLineEdit(text)
        field.setReadOnly(True)
        field.setFocusPolicy(QtCore.Qt.StrongFocus)
        field.setStyleSheet("background:white; border:1px solid #999; padding:6px;")
        return field

    win.nvram_cs_mon = readonly_field("0")
    win.nvram_cs_fw = readonly_field("0")
    win.nvram_cs_cfg = readonly_field("0")
    actual_labels = []
    status_labels = []
    for row_index, (name, saved) in enumerate((
        ("Monitor", win.nvram_cs_mon),
        ("Firmware", win.nvram_cs_fw),
        ("Configuration", win.nvram_cs_cfg),
    ), 1):
        check.addWidget(QtWidgets.QLabel(name), row_index, 0)
        saved.setMinimumSize(215, 66)
        check.addWidget(saved, row_index, 1)
        actual = readonly_field("0")
        actual.setMinimumSize(215, 66)
        actual_labels.append(actual)
        check.addWidget(actual, row_index, 2)
        status = QtWidgets.QLabel("NOK")
        status.setObjectName("classicStatusBadge")
        status.setAlignment(QtCore.Qt.AlignCenter)
        status.setFixedSize(70, 50)
        status_labels.append(status)
        check.addWidget(status, row_index, 3, alignment=QtCore.Qt.AlignCenter)
    win._nvram_actual_labels = actual_labels
    win._nvram_status_labels = status_labels
    win.nvram_build_checksum_button = FlatPush("Build Check Sum")
    win.nvram_read_checksum_button = FlatPush("Read Check Sum")
    win.nvram_build_checksum_button.setProperty("dangerAction", True)
    win.nvram_build_checksum_button.clicked.connect(win.on_build_checksum)
    win.nvram_read_checksum_button.clicked.connect(win.on_read_checksum)
    check.addWidget(win.nvram_build_checksum_button, 4, 0, 1, 2)
    check.addWidget(win.nvram_read_checksum_button, 4, 2, 1, 2)
    root.addWidget(checksums)

    label_group = GroupPanel("Set/Generate Label File")
    label_group.setFixedSize(815, 240)
    label_layout = QtWidgets.QVBoxLayout(label_group)
    buttons = QtWidgets.QHBoxLayout()
    win.label_generate_button = FlatPush("Generate Label File")
    win.label_set_button = FlatPush("Set Label File")
    win.label_default_button = FlatPush("Use Default Labels")
    win.si_unit_set_button = FlatPush("Set SIUnit")
    win.label_generate_button.clicked.connect(win.on_label_file_generate)
    win.label_set_button.clicked.connect(win.on_label_file_load)
    win.label_default_button.clicked.connect(lambda: _on_use_default_label(win))
    win.si_unit_set_button.clicked.connect(win.on_si_unit_select)
    for button in (
        win.label_generate_button,
        win.label_set_button,
        win.label_default_button,
        win.si_unit_set_button,
    ):
        buttons.addWidget(button)
    label_layout.addLayout(buttons)
    win.label_path_lbl = readonly_field("No File")
    win.label_file_lbl = win.label_path_lbl
    win.si_unit_path_lbl = readonly_field("No File")
    win.si_file_lbl = win.si_unit_path_lbl
    for caption, label in (("Label File:", win.label_path_lbl), ("SI File:", win.si_unit_path_lbl)):
        label_layout.addWidget(QtWidgets.QLabel(caption))
        label_layout.addWidget(label)
    root.addWidget(label_group)
    root.addStretch(1)

    win.setup_file_lbl = QtWidgets.QLabel("No file selected")
    win.nvram_fs = SciSpin()
    win.nvram_cfg = SciEdit()
    win.nvram_adcs = QtWidgets.QSpinBox()
    for widget in (win.setup_file_lbl, win.nvram_fs, win.nvram_cfg, win.nvram_adcs):
        widget.hide()
    win.progress_bar = QtWidgets.QProgressBar()
    win.progress_txt = QtWidgets.QLabel()
    win.progress_bdr = QtWidgets.QFrame()
    win.progress_bar.hide()
    win.progress_txt.hide()
    win.progress_bdr.hide()
    win._progress_cancel = False

    settings = QtCore.QSettings("python_samba", "SAMBA19xUI")
    saved_label_path = str(settings.value("LabelPath", "No File") or "No File")
    win.label_path_lbl.setText(saved_label_path)
    saved_si_path = str(settings.value("SIFile", "") or "")
    if saved_si_path:
        win.si_unit_path_lbl.setText(saved_si_path)
    if saved_si_path and os.path.isfile(saved_si_path):
        try:
            _read_si_units_file(win, saved_si_path)
        except Exception as exc:
            if hasattr(win, "log"):
                win.log_msg(f"ERROR loading saved SI unit file: {exc}")

    tabs.addTab(w, "Save/Load")
    win.main_tabs.addTab(tabs, "Save/Load")


def _sync_nvram_protection_controls(
    win, protected: bool | None = None, *, connected: bool | None = None
) -> None:
    """Apply the old NVRAM-only Protection state to its three actions."""
    if protected is not None:
        win._nvram_protected = bool(protected)
    protected = bool(getattr(win, "_nvram_protected", True))
    if connected is None:
        connected = bool(win.session and win.session.connected)

    protection = getattr(win, "protection_led", None)
    if protection is not None:
        protection.set_on(protected)
        protection.setEnabled(bool(connected))
    save = getattr(win, "nvram_save_button", None)
    clear = getattr(win, "nvram_clear_button", None)
    restore = getattr(win, "nvram_restore_button", None)
    if save is not None:
        save.setEnabled(bool(connected) and not protected)
    if clear is not None:
        clear.setEnabled(bool(connected) and not protected)
    if restore is not None:
        restore.setEnabled(bool(connected))


def _on_protection_led_clicked(win) -> None:
    """Toggle only NVRAM Save/Clear protection, matching the legacy page."""
    if not win.session or not win.session.connected:
        _sync_nvram_protection_controls(win, connected=False)
        return
    _sync_nvram_protection_controls(
        win,
        protected=not bool(getattr(win, "_nvram_protected", True)),
        connected=True,
    )


# ===================================================================
# Progress helpers
# ===================================================================
def _on_progress_cancel(win) -> None:
    """Set the cancel flag to stop the current progress operation."""
    win._progress_cancel = True
    win.log_msg("Operation cancelled by user")


def _show_progress_local(win, status_text: str = "") -> None:
    """Show the embedded progress bar and status text."""
    if hasattr(win, "progress_bar"):
        win.progress_bar.setVisible(True)
        win.progress_bar.setValue(0)
    if hasattr(win, "progress_txt"):
        win.progress_txt.setVisible(True)
        win.progress_txt.setText(status_text)
    win._progress_cancel = False
    # Find the cancel button and show it
    if hasattr(win, "progress_bdr"):
        for child in win.progress_bdr.findChildren(QtWidgets.QPushButton):
            if child.text() == "Cancel":
                child.setVisible(True)
                break
    QtWidgets.QApplication.processEvents()


def _hide_progress_local(win) -> None:
    """Hide the embedded progress bar and status text."""
    if hasattr(win, "progress_bar"):
        win.progress_bar.setVisible(False)
    if hasattr(win, "progress_txt"):
        win.progress_txt.setVisible(False)
    if hasattr(win, "progress_bdr"):
        for child in win.progress_bdr.findChildren(QtWidgets.QPushButton):
            if child.text() == "Cancel":
                child.setVisible(False)
                break
    QtWidgets.QApplication.processEvents()


# ===================================================================
# on_label_file_generate  — handler for "Generate Label File" button
# ===================================================================
def on_label_file_generate(win) -> None:
    """Generate a SAMBA19xLabel XML file with the current label names.

    Corresponds to C# LabelFileGenerateBtn_Click.
    """
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        win, "Save SAMBA19x labels file", "",
        "SAMBA19xLabel files (*.SAMBA19xLabel);;All files (*.*)"
    )
    if not path:
        return
    try:
        from python_samba.ui.label_files import write_label_file
        from python_samba.ui.main_window import current_runtime_label_values

        write_label_file(path, current_runtime_label_values())
        win.log_msg(f"Label file generated: {path}")
    except Exception as exc:
        win.log_msg(f"ERROR generating label file: {exc}")
        QtWidgets.QMessageBox.critical(win, "Error", str(exc))


def _add_label_array(parent: ET.Element, name: str, items: list[str]) -> None:
    """Add an array of label strings as an XML element."""
    elem = ET.SubElement(parent, name)
    for item in items:
        child = ET.SubElement(elem, "string")
        child.text = item


# ===================================================================
# on_label_file_load  — handler for "Load Label File" button
# ===================================================================
def on_label_file_load(win) -> None:
    """Load a SAMBA19xLabel XML file and save its path to registry-equivalent.

    Corresponds to C# LabelFileLoadBtn_Click.
    """
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        win, "Open SAMBA19x labels file", "",
        "SAMBA19xLabel files (*.SAMBA19xLabel);;All files (*.*)"
    )
    if not path:
        return
    try:
        # Fully parse the file now so an invalid selection is never persisted.
        from python_samba.ui.label_files import parse_label_file

        parse_label_file(path)

        QtCore.QSettings("python_samba", "SAMBA19xUI").setValue(
            "LabelPath", path
        )
        if hasattr(win, "label_path_lbl"):
            win.label_path_lbl.setText(path)
        win.log_msg(f"Label file loaded: {path}")
        QtWidgets.QMessageBox.information(
            win, "Label File",
            f"Label file loaded.\nPath: {path}\n\nReload the application to validate this modification."
        )
    except Exception as exc:
        win.log_msg(f"ERROR loading label file: {exc}")
        QtWidgets.QMessageBox.critical(win, "Error", str(exc))


def _on_use_default_label(win) -> None:
    """Reset to default labels (like C# UseDefaultLabelBtn_Click)."""
    QtCore.QSettings("python_samba", "SAMBA19xUI").setValue(
        "LabelPath", "No File"
    )
    if hasattr(win, "label_path_lbl"):
        win.label_path_lbl.setText("No File")
    win.log_msg("Using default labels. Reload the application to validate this modification.")
    QtWidgets.QMessageBox.information(
        win, "Labels",
        "Default labels will be used.\nReload the application to validate this modification."
    )


# ===================================================================
# on_si_unit_select  — handler for "Select SI Unit File" button
# ===================================================================
def on_si_unit_select(win) -> None:
    """Select an SI unit file (.SI) for unit display.

    Corresponds to C# SetSiUnitsBtn_Click.
    """
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        win, "Open SAMBA19x SIUNIT file", "",
        "SAMBA19x SIUNIT files (*.SI);;All files (*.*)"
    )
    if not path:
        return
    try:
        if hasattr(win, "si_unit_path_lbl"):
            win.si_unit_path_lbl.setText(path)
        if hasattr(win, "si_file_lbl"):
            win.si_file_lbl.setText(path)
        QtCore.QSettings("python_samba", "SAMBA19xUI").setValue("SIFile", path)
        win.log_msg(f"SI unit file selected: {path}")

        # Ask if user wants to activate immediately
        reply = QtWidgets.QMessageBox.question(
            win, "SI Units",
            "The modification is valid by the next application start.\n"
            "Do you want to activate the modification directly?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            # Attempt to read SI units (matching HelpMethodes.ReadSIUnitsFile)
            try:
                _read_si_units_file(win, path)
            except Exception as exc:
                win.log_msg(f"Error reading SI units: {exc}")
    except Exception as exc:
        win.log_msg(f"ERROR selecting SI unit file: {exc}")
        QtWidgets.QMessageBox.critical(win, "Error", str(exc))


def _read_si_units_file(win, path: str) -> None:
    """Load legacy displacement factors into the eight proximity cards."""
    from python_samba.ui.main_window import PROX_DISPLAY_NAMES, PROX_RAW_TO_DISPLAY

    def log(message: str) -> None:
        if hasattr(win, "log"):
            win.log_msg(message)

    log(f"Reading SI units from {path}")
    tree = ET.parse(path)

    def local_name(element) -> str:
        return str(element.tag).rsplit("}", 1)[-1]

    raw_index_by_name = {
        "InpZ1Prox": 0,
        "InpZ2Prox": 1,
        "InpZ3Prox": 2,
        "InpH1Prox": 3,
        "InpH2Prox": 4,
        "InpH3Prox": 5,
        "InpZ4Prox": 6,
        "InpH4Prox": 7,
    }
    factors: dict[int, float] = {}
    for si_unit in tree.getroot().iter():
        if local_name(si_unit) != "SIUnit" or si_unit.get("Name") != "Displacement":
            continue
        for factor in si_unit.iter():
            if local_name(factor) != "SIFactor":
                continue
            fields = {
                local_name(child): (child.text or "").strip()
                for child in list(factor)
            }
            name = fields.get("Name", "")
            if name in raw_index_by_name and fields.get("Value", ""):
                factors[raw_index_by_name[name]] = float(fields["Value"])

    if not factors:
        raise ValueError("No Displacement/ArraySIFactor values found in SI file")
    editors = getattr(win, "proxy_si_unit_edits", {})
    for raw_index, value in factors.items():
        display_index = PROX_RAW_TO_DISPLAY[raw_index]
        name = PROX_DISPLAY_NAMES[display_index]
        editor = editors.get(name)
        if editor is not None:
            editor.setText(format_ui_number(value))
        log(f"  SI factor: {name} = {format_ui_number(value)} digits/µm")
    if hasattr(win, "_on_proximity_si_unit_changed"):
        win._on_proximity_si_unit_changed()
    log(f"SI units applied ({len(factors)} proximity factors)")


# ===================================================================
# on_build_checksum  — handler for "Build Checksum" button
# ===================================================================
def on_build_checksum(win) -> None:
    """Build NVRAM checksum on the controller.

    Corresponds to C# BuildCheckSumBtn_Click.
    """
    def work() -> None:
        s = win._require_session()
        if not win._confirm_write("BBNCS build NVRAM checksums"):
            return
        if win.gate is not None:
            win.gate.take_snapshot()
        was_readonly = bool(s.readonly)
        was_unlocked = bool(win.gate.unlocked) if win.gate is not None else None
        s.readonly = False
        if win.gate is not None:
            win.gate.unlocked = True
        try:
            values = s.build_nvram_checksums()
        finally:
            s.readonly = was_readonly
            if win.gate is not None and was_unlocked is not None:
                win.gate.unlocked = was_unlocked
        win.log_msg(
            "NVRAM checksums built: "
            f"monitor={values[0]}, firmware={values[1]}, config={values[2]}"
        )
        if hasattr(win, "_nvram_actual_labels"):
            for label, value in zip(win._nvram_actual_labels, values):
                label.setText(str(value))
    win._run("Build Checksum", work)


# ===================================================================
# on_read_checksum  — handler for "Read Checksum" button
# ===================================================================
def on_read_checksum(win) -> None:
    """Read NVRAM checksum from the controller.

    Corresponds to C# ReadCheckSumBtn_Click.
    """
    def work() -> None:
        s = win._require_session()
        values = s.check_nvram_checksums()
        status = values[0]
        saved = [values[1], values[3], values[5]]
        actual = [values[2], values[4], values[6]]
        win.log_msg(
            "NVRAM checksums read: "
            f"status=0x{status:X}, saved={saved}, actual={actual}"
        )
        for label, value in zip(
            (win.nvram_cs_mon, win.nvram_cs_fw, win.nvram_cs_cfg), saved
        ):
            label.setText(str(value))
        if hasattr(win, "_nvram_actual_labels"):
            for label, value in zip(win._nvram_actual_labels, actual):
                label.setText(str(value))
        if hasattr(win, "_nvram_status_labels"):
            for index, label in enumerate(win._nvram_status_labels):
                ok = (status & (1 << index)) == 0
                label.setText("OK" if ok else "NOK")
                label.setProperty("active", ok)
                label.style().unpolish(label)
                label.style().polish(label)
    win._run("Read Checksum", work)


# ===================================================================
# Patch application helper
# ===================================================================
def apply_patches(main_window_cls) -> None:
    """Apply all patches to the MainWindow class at once."""
    # Don't overwrite _build_special_tab — unified_special_tab handles it
    main_window_cls._build_saveload_tab = _build_saveload_tab_reference
    main_window_cls._on_timer_tick = _on_timer_tick
    main_window_cls._on_remote_live_refresh_finished = (
        _on_remote_live_refresh_finished
    )
    main_window_cls._show_progress_dialog = staticmethod(_show_progress_dialog)
    main_window_cls._hide_progress_dialog = staticmethod(_hide_progress_dialog)
    main_window_cls.on_signal_continue_read = on_signal_continue_read
    main_window_cls.on_signal_continue_start = on_signal_continue_start
    main_window_cls._on_sig_selector_changed = _on_sig_selector_changed
    main_window_cls.on_sig_save_settings = _on_sig_save_settings
    main_window_cls.on_sig_load_settings = _on_sig_load_settings
    main_window_cls.on_label_file_generate = on_label_file_generate
    main_window_cls.on_label_file_load = on_label_file_load
    main_window_cls.on_si_unit_select = on_si_unit_select
    main_window_cls.on_build_checksum = on_build_checksum
    main_window_cls.on_read_checksum = on_read_checksum
    main_window_cls._sync_nvram_protection_controls = (
        _sync_nvram_protection_controls
    )
