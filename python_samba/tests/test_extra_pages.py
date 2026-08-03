"""Tests for newly wired pneumatic / system / dac / logging pages."""

from __future__ import annotations

import pytest

from python_samba.protocol.commands import FilterStage
from python_samba.services.session import open_mock
from python_samba.ui.page_specs import PAGE_SPECS


def test_real_controller_firmware_info_is_formatted_like_source_ui():
    from _patches.connect_page_patch import parse_fw_version

    raw = (
        "3 3 122 103 9 FWCompiler: 7004021 "
        "FWBldDate: Mar 15 2019 FWBldTime: 11:05:37 "
        "LibCompiler: 7004016 LibBldDate: Jun 24 2016 LibBldTime: 08:28:47"
    )
    formatted = parse_fw_version(raw)

    assert formatted.startswith(
        "Firmware Version: 3.3.122\nLib Version: 103\nMain Board Version: 9"
    )
    assert "FWCompiler: 7004021" in formatted
    assert formatted.endswith("LibBldTime: 08:28:47")


def test_no_stub_pages_remain():
    stubs = [p for p in PAGE_SPECS if p.status == "stub"]
    assert stubs == [], f"still stub: {[p.page_id for p in stubs]}"


def test_pneumatic_filter_and_status():
    with open_mock(readonly=False) as s:
        fs = s.get_pneumatic_filter(0, 0)
        assert fs.filter_type == 1
        s.set_pneumatic_filter(FilterStage(0, 0, 3, (0.2, 0.0, 1.0, 0.0, 0.0)))
        assert s.get_pneumatic_filter(0, 0).filter_type == 3
        assert s.get_pneumatic_axes_status()
        assert s.get_pneumatic_heights_valves()
        assert s.get_pneumatic_status_timer() == pytest.approx((12.5, 0.25))
        row = s.get_pneumatic_steering_matrix(0)
        assert len(row) >= 1
        row[1] = 0.5
        s.set_pneumatic_steering_matrix(0, row)
        assert s.get_pneumatic_steering_matrix(0)[1] == pytest.approx(0.5)


def test_floatation_dither_ramp():
    with open_mock(readonly=False) as s:
        cfg = s.get_pneumatic_config()
        assert cfg
        s.set_pneumatic_config("120", "0.6", "1.1")
        assert s.get_pneumatic_config()[0] == "120"
        s.set_pneumatic_valve_offsets([1, 2, 3, 4, 5, 6, 7, 8])
        assert s.get_pneumatic_valve_offsets()[0] == pytest.approx(1)
        s.set_pneumatic_setpoint_status(1)
        assert s.get_pneumatic_setpoint_status() == 1
        s.use_current_pressure_offsets(1)
        assert s.get_pneumatic_valve_offsets()[:4] == pytest.approx(
            [0.1, 0.2, 0.3, 0.4]
        )
        s.use_current_pressure_offsets(2)
        assert s.get_pneumatic_valve_offsets()[4:] == pytest.approx(
            [0.1, 0.2, 0.3, 0.4]
        )
        s.set_dither_value(12.5)
        s.set_dither_frequency(40)
        s.set_dither_alpha(0.002)
        assert s.get_dither_value() == pytest.approx(12.5)
        assert s.get_dither_frequency() == pytest.approx(40)
        assert s.get_dither_alpha() == pytest.approx(0.002)
        s.set_startup_ramp(1, 3.5)
        ramp = s.get_startup_ramp()
        assert ramp[0] in ("1", "1.0") or float(ramp[0]) == 1


def test_pneumatic_system_status_uses_source_bit_masks():
    with open_mock() as s:
        s.transport.state.individual_loop = 0x1F
        s.transport.state.system_status = 0x2604D
        states = s.get_system_loop_status()
        assert states["overall"]
        assert states["pneumatic"]
        assert states["ff"]
        assert states["pff"]
        assert states["dither_compensation"]
        assert states["reference_metrology"]
        assert states["move_up_at_startup"]

        # These were the old, incorrect Pneumatic/Dither/Reference masks.
        s.transport.state.system_status = 0x16
        states = s.get_system_loop_status()
        assert not states["pneumatic"]
        assert not states["dither_compensation"]
        assert not states["reference_metrology"]


def test_performance_switch_motor():
    with open_mock(readonly=False) as s:
        s.set_performance_monitor("1", "2.0", "0.25")
        assert s.get_performance_monitor()[0] == "1"
        assert s.get_system_load() == pytest.approx(12.5)
        assert s.get_performance_status()
        s.set_switch_signal("1", "2", "3")
        assert s.get_switch_signal() == ["1", "2", "3"]
        s.set_switch_conditions("1.5", "3.0", "2")
        assert s.get_switch_conditions()[0] == "1.5"
        cfg = s.get_motor_overcurrent_config()
        assert cfg
        s.set_motor_overcurrent_config(*cfg)
        assert len(s.get_motor_power_values()) == 12
        assert len(s.get_motor_failsafe_status()) == 12


def test_dac_adc_logging_pff():
    with open_mock(readonly=False) as s:
        adc = s.get_adc_sequence()
        dac = s.get_dac_sequence()
        assert len(adc) == 25
        assert len(dac) == 20
        adc[0] = 7
        s.set_adc_sequence(adc)
        assert s.get_adc_sequence()[0] == 7
        s.set_event_trace_params("2", "512", "2", "1", "0", "0")
        assert s.get_event_trace_params()[1] == "512"
        s.start_stop_event_tracing(1)
        assert s.get_event_trace_info()[0] == "1"
        s.set_pff_config("8")
        assert s.get_pff_config() == ["8"]
        s.set_pff_gains(0.1, 0.2, 0.3)
        assert s.get_pff_gains()[0] == pytest.approx(0.1)


def test_disabled_event_trace_get_value_is_not_writable():
    from python_samba.ui.extra_pages import event_trace_params_are_disabled

    assert event_trace_params_are_disabled(["0", "0", "0", "1", "5000", "0"])
    assert not event_trace_params_are_disabled(["2", "512", "2", "1", "5", "0"])


def test_hardware_action_probe_restores_mock_values(tmp_path, monkeypatch):
    import sys
    from _review import hardware_action_port_probe as action_probe

    monkeypatch.setattr(
        action_probe, "open_serial",
        lambda *_args, **_kwargs: open_mock(readonly=False),
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "hardware_action_port_probe.py",
            "--allow-delete-event-traces",
            "--output-dir", str(tmp_path),
        ],
    )

    assert action_probe.main() == 0
    report_path = next(tmp_path.glob("hardware_action_ports_*_report.json"))
    import json
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert {result["status"] for result in report["results"]} == {"PASS"}
    assert report["snapshots"]["restorable_changed"] == []


def test_hardware_action_probe_recognizes_disabled_event_trace_params():
    from _review.hardware_action_port_probe import _event_trace_params_disabled

    assert _event_trace_params_disabled(["0", "0", "0", "1", "5000", "0"])
    assert not _event_trace_params_disabled(["2", "512", "2", "1", "5", "0"])


def test_gui_builds_all_pages():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    # SAMBA19xUI-style: 10 main tabs with nested sub-tabs
    assert win.main_tabs.count() >= 8
    # Check that tab texts match expected
    tab_texts = [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
    for expected in ("Connect", "Controller", "Status", "Velocity", "Position"):
        assert expected in tab_texts, f"{expected} not in tabs {tab_texts}"
    # The primary tab API is preserved, while the user-facing navigation is
    # the fixed left sidebar used by the original SAMBA UI hierarchy.
    assert win.main_tabs.tabBar().isHidden()
    # Logging remains available internally/contextually, but the supplied
    # SAMBA19xUI reference has no visible Logging navigation button.  The
    # sidebar also uses the reference labels (Pneum. FF / Save/Load Setup).
    visible_tabs = [text for text in tab_texts if text != "Logging"]
    display_names = {
        "Pneum. SFF": "Pneum. FF",
        "Save/Load": "Save/Load Setup",
    }
    assert [button.text() for button in win.nav_buttons] == [
        display_names.get(text, text) for text in visible_tabs
    ]
    velocity_index = tab_texts.index("Velocity")
    win.nav_buttons[velocity_index].click()
    assert win.main_tabs.currentIndex() == velocity_index
    assert win.page_title_lbl.text() == "Velocity"

    # System loop types come from BGSTS.System; velocity/position come from
    # DGCSS rather than the six-axis BGSTS.Individual word.
    win.loop_states.update_loop(0x15, 0x4045, 0x20)
    assert win.loop_states.state_labels["overall"].text() == "ON"
    assert win.loop_states.state_labels["velocity"].text() == "ON"
    assert win.loop_states.state_labels["position"].text() == "OFF"
    assert win.loop_states.state_labels["pneumatic"].text() == "ON"
    assert win.loop_states.state_labels["pff"].text() == "ON"

    win.console_toggle.setChecked(True)
    assert not win.console_panel.isHidden()
    win.console_toggle.setChecked(False)
    assert not win.console_panel.isVisible()
    # Check filter controls
    assert hasattr(win, "vel_stage_bar")
    assert hasattr(win, "vel_filter_panel")
    assert hasattr(win, "pos_filter_buttons")
    assert hasattr(win, "ff_filter_buttons")
    win.close()


def test_formal_gui_patch_contract_builds_extended_pages():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import PATCH_MODULES, apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    report = apply_all_patches(PatchedMainWindow, strict=True)
    assert report.ok
    assert report.applied == tuple(PATCH_MODULES)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    # These controls distinguish the feature-complete builders from their
    # base fallbacks and catch a loaded-but-not-bound patch immediately.
    assert hasattr(win, "_port_group")
    assert hasattr(win, "_conn_page_connect_btn")
    assert hasattr(win, "_fw_leds")
    assert hasattr(win, "loop_opl_slider")
    assert not hasattr(win, "write_protection_switch")
    assert not hasattr(win, "unlock")
    assert win.protection_led.text() == "ON"
    assert not win.protection_led.isEnabled()
    assert len(win.nav_buttons) == win.main_tabs.count() - 1
    win.close()


def test_saveload_protection_only_gates_nvram_save_and_clear(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from python_samba.services.safety import SafetyGate
    from python_samba.services.session import open_mock
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    session = open_mock(readonly=False)
    session.open()
    win.session = session
    win.gate = SafetyGate(session)
    win.gate.snapshot_dir = tmp_path / "snapshots"
    win._set_connection_display(True, "mock")
    page_index = [
        win.main_tabs.tabText(index) for index in range(win.main_tabs.count())
    ].index("Save/Load")
    win.main_tabs.setCurrentIndex(page_index)
    win.resize(1840, 1240)
    win.show()
    app.processEvents()
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "exec",
        lambda _box: QtWidgets.QMessageBox.Ok,
    )

    assert win.protection_led.isEnabled()
    assert win.protection_led.text() == "ON"
    assert not session.readonly
    assert win.gate.unlocked
    assert not win.nvram_save_button.isEnabled()
    assert win.nvram_restore_button.isEnabled()
    assert not win.nvram_clear_button.isEnabled()
    assert (
        win.protection_led.geometry().bottom()
        <= win.nvram_group.contentsRect().bottom()
    )
    assert win.nvram_group.childAt(win.nvram_save_button.geometry().center()) is win.nvram_save_button
    assert (
        win.nvram_group.childAt(win.nvram_restore_button.geometry().center())
        is win.nvram_restore_button
    )

    QtTest.QTest.mouseClick(win.protection_led, QtCore.Qt.LeftButton)
    assert not session.readonly
    assert win.gate.unlocked
    assert win.protection_led.text() == "OFF"
    assert win.nvram_save_button.isEnabled()
    assert win.nvram_restore_button.isEnabled()
    assert win.nvram_clear_button.isEnabled()
    session.transport.state.individual_loop = 0x12
    QtTest.QTest.mouseClick(win.nvram_save_button, QtCore.Qt.LeftButton)
    assert session.transport.state.nvram_user["individual_loop"] == 0x12
    assert not session.readonly

    QtTest.QTest.mouseClick(win.protection_led, QtCore.Qt.LeftButton)
    assert not session.readonly
    assert win.gate.unlocked
    assert win.protection_led.text() == "ON"
    assert not win.nvram_save_button.isEnabled()
    assert win.nvram_restore_button.isEnabled()
    assert not win.nvram_clear_button.isEnabled()
    session.transport.state.individual_loop = 0x34
    QtTest.QTest.mouseClick(win.nvram_restore_button, QtCore.Qt.LeftButton)
    assert session.transport.state.individual_loop == 0x12
    assert not session.readonly

    win.on_disconnect()
    assert not win.protection_led.isEnabled()
    assert win.protection_led.text() == "ON"
    assert not win.nvram_save_button.isEnabled()
    assert not win.nvram_restore_button.isEnabled()
    assert not win.nvram_clear_button.isEnabled()
    labels = [label.text() for label in win.findChildren(QtWidgets.QLabel)]
    assert labels.count("Protection") == 1
    assert "Write Protection" not in labels
    win.close()


def test_saveload_fields_and_local_file_buttons_accept_real_mouse_clicks(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings("python_samba", "SAMBA19xUI")
    old_label = settings.value("LabelPath") if settings.contains("LabelPath") else None
    old_si = settings.value("SIFile") if settings.contains("SIFile") else None
    label_file = tmp_path / "labels.SAMBA19xLabel"
    si_file = tmp_path / "units.SI"
    si_file.write_text(
        '<SIUnits><SIUnit Name="Displacement"><ArraySIFactor>'
        '<SIFactor><Name>InpZ1Prox</Name><Value>2</Value></SIFactor>'
        '</ArraySIFactor></SIUnit></SIUnits>',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(label_file), ""),
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda _parent, title, *_args, **_kwargs: (
            str(label_file) if "labels" in title.lower() else str(si_file),
            "",
        ),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.No,
    )

    win = PatchedMainWindow()
    try:
        page_index = [
            win.main_tabs.tabText(index) for index in range(win.main_tabs.count())
        ].index("Save/Load")
        win.main_tabs.setCurrentIndex(page_index)
        win.resize(1840, 1240)
        win.show()
        app.processEvents()

        for field in (
            win.nvram_cs_mon,
            win.nvram_cs_fw,
            win.nvram_cs_cfg,
            *win._nvram_actual_labels,
            win.label_path_lbl,
            win.si_unit_path_lbl,
        ):
            assert isinstance(field, QtWidgets.QLineEdit)
            assert field.isReadOnly()
            assert field.isEnabled()
            QtTest.QTest.mouseClick(field, QtCore.Qt.LeftButton)
            assert field.hasFocus()

        QtTest.QTest.mouseClick(win.label_generate_button, QtCore.Qt.LeftButton)
        assert label_file.is_file()
        from python_samba.ui.label_files import LABEL_FILE_DEFAULTS, parse_label_file

        generated_labels = parse_label_file(label_file)
        assert set(generated_labels) == set(LABEL_FILE_DEFAULTS)
        assert len(generated_labels["InputName"]) == 46
        assert len(generated_labels["MotorTemperaturSensorName"]) == 12
        QtTest.QTest.mouseClick(win.label_set_button, QtCore.Qt.LeftButton)
        assert settings.value("LabelPath") == str(label_file)
        assert win.label_path_lbl.text() == str(label_file)
        QtTest.QTest.mouseClick(win.label_default_button, QtCore.Qt.LeftButton)
        assert settings.value("LabelPath") == "No File"
        assert win.label_path_lbl.text() == "No File"
        QtTest.QTest.mouseClick(win.si_unit_set_button, QtCore.Qt.LeftButton)
        assert settings.value("SIFile") == str(si_file)
        assert win.si_unit_path_lbl.text() == str(si_file)
    finally:
        win.close()
        if old_label is None:
            settings.remove("LabelPath")
        else:
            settings.setValue("LabelPath", old_label)
        if old_si is None:
            settings.remove("SIFile")
        else:
            settings.setValue("SIFile", old_si)


def test_saved_label_file_is_applied_before_page_widgets_are_built(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets
    from python_samba.ui.classic_widgets import IOSignalButton
    from python_samba.ui.label_files import LABEL_FILE_DEFAULTS, write_label_file
    from python_samba.ui.main_window import (
        MainWindow,
        PNEU_AXES_NAMES,
        POS_AXES_NAMES,
        VEL_AXES_NAMES,
        _apply_runtime_label_values,
    )
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings("python_samba", "SAMBA19xUI")
    old_label = settings.value("LabelPath") if settings.contains("LabelPath") else None
    label_file = tmp_path / "runtime.SAMBA19xLabel"
    overrides = {
        name: list(items) for name, items in LABEL_FILE_DEFAULTS.items()
    }
    overrides["InputName"] = [f"Input{index:02d}" for index in range(46)]
    overrides["VelAxesName"] = [f"Velocity{index}" for index in range(6)]
    overrides["PosAxesName"] = [f"Position{index}" for index in range(12)]
    overrides["PneuAxesName"] = [f"Pneumatic{index}" for index in range(3)]
    overrides["Vel7InputName"] = [f"V7Input{index}" for index in range(8)]
    overrides["Vel8InputName"] = [f"V8Input{index}" for index in range(8)]
    overrides["VelOutputName"] = [f"VelOutput{index}" for index in range(12)]
    overrides["ADCInputName"] = [f"ADC{index}" for index in range(32)]
    overrides["DACOutputName"] = [f"DAC{index}" for index in range(20)]
    overrides["MotorTemperaturSensorName"] = [
        f"Temperature{index}" for index in range(12)
    ]
    write_label_file(label_file, overrides)
    settings.setValue("LabelPath", str(label_file))

    win = None
    default_win = None
    try:
        win = PatchedMainWindow()
        assert win._label_load_warnings == []
        assert VEL_AXES_NAMES[0] == "Velocity0"
        assert POS_AXES_NAMES[0] == "Position0"
        assert PNEU_AXES_NAMES[0] == "Pneumatic0"
        assert IOSignalButton.format_io_signal((0, 0, 0)) == "Input00"
        assert IOSignalButton.format_io_signal((1, 0, 0)) == "DAC0"
        labels = {label.text() for label in win.findChildren(QtWidgets.QLabel)}
        assert "Velocity0" in labels
        assert "Position0" in labels
        assert "Pneumatic0" in labels

        # "Use Default Labels" is intentionally restart-scoped in the legacy
        # UI.  A second construction must reset every process-global list.
        settings.setValue("LabelPath", "No File")
        default_win = PatchedMainWindow()
        assert VEL_AXES_NAMES[0] == LABEL_FILE_DEFAULTS["VelAxesName"][0]
        assert IOSignalButton.INPUT_NAMES[0] == LABEL_FILE_DEFAULTS["InputName"][0]
    finally:
        if win is not None:
            win.close()
        if default_win is not None:
            default_win.close()
        if old_label is None:
            settings.remove("LabelPath")
        else:
            settings.setValue("LabelPath", old_label)
        _apply_runtime_label_values()
        app.processEvents()


def test_open_setup_file_button_applies_to_controller_and_restores_lock(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from python_samba.services.config_reader import (
        capture_config_from_session,
        save_config,
    )
    from python_samba.services.safety import SafetyGate
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    session = open_mock(readonly=False)
    session.open()
    config = capture_config_from_session(session)
    assert config.capture_warnings == []
    config.motors_limit = 77
    config_file = tmp_path / "apply.SAMBA19x_Config"
    save_config(config_file, config)
    session.set_output_limit(12)

    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(config_file), ""),
    )

    def unexpected_dialog(*_args, **_kwargs):
        raise AssertionError("valid Open File -> Controller must not ask again")

    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", unexpected_dialog)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", unexpected_dialog)

    win = PatchedMainWindow()
    win.session = session
    win.gate = SafetyGate(session, snapshot_dir=tmp_path / "snapshots")
    win.gate.lock()
    win._set_connection_display(True, "mock")
    page_index = [
        win.main_tabs.tabText(index) for index in range(win.main_tabs.count())
    ].index("Save/Load")
    win.main_tabs.setCurrentIndex(page_index)
    win.resize(1840, 1240)
    win.show()
    app.processEvents()

    QtTest.QTest.mouseClick(win.setup_load_file_button, QtCore.Qt.LeftButton)
    assert session.get_output_limit() == 77
    assert win._loaded_config_path == str(config_file)
    assert session.readonly
    assert not win.gate.unlocked
    assert list((tmp_path / "snapshots").glob("snap_*.json"))
    win.close()
    session.close()


def test_saveload_save_and_checksum_buttons_work_without_clear(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from python_samba.services.config_reader import load_config
    from python_samba.services.safety import SafetyGate
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config_file = tmp_path / "saved.SAMBA19x_Config"
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(config_file), ""),
    )

    session = open_mock(readonly=False)
    session.open()

    def forbidden_clear():
        raise AssertionError("Clear NVRAM is excluded from Save/Load testing")

    monkeypatch.setattr(session, "nvram_clear", forbidden_clear)
    win = PatchedMainWindow()
    win.session = session
    win.gate = SafetyGate(session, snapshot_dir=tmp_path / "snapshots")
    win._set_connection_display(True, "mock")
    page_index = [
        win.main_tabs.tabText(index) for index in range(win.main_tabs.count())
    ].index("Save/Load")
    win.main_tabs.setCurrentIndex(page_index)
    win.resize(1840, 1240)
    win.show()
    app.processEvents()

    QtTest.QTest.mouseClick(win.setup_save_file_button, QtCore.Qt.LeftButton)
    assert config_file.is_file()
    assert load_config(config_file).motors_limit == session.get_output_limit()
    QtTest.QTest.mouseClick(
        win.nvram_build_checksum_button, QtCore.Qt.LeftButton
    )
    QtTest.QTest.mouseClick(
        win.nvram_read_checksum_button, QtCore.Qt.LeftButton
    )
    for field in (
        win.nvram_cs_mon,
        win.nvram_cs_fw,
        win.nvram_cs_cfg,
        *win._nvram_actual_labels,
    ):
        int(field.text())
    assert all(label.text() == "OK" for label in win._nvram_status_labels)
    win.close()
    session.close()


def test_invalid_setup_file_is_not_marked_as_loaded(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    invalid = tmp_path / "invalid.SAMBA19x_Config"
    invalid.write_text("<not-a-samba-config />", encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(invalid), ""),
    )
    messages = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "critical",
        lambda _parent, title, text: messages.append((title, text)),
    )
    win = PatchedMainWindow()
    win._loaded_config_path = "previous.SAMBA19x_Config"
    win.setup_file_lbl.setText("previous.SAMBA19x_Config")
    win.on_setup_load_file()
    assert win._loaded_config_path == "previous.SAMBA19x_Config"
    assert win.setup_file_lbl.text() == "previous.SAMBA19x_Config"
    assert messages and "Unexpected root tag" in messages[0][1]
    win.close()


def test_connect_is_fast_and_reuses_initial_firmware_query(monkeypatch):
    """Connect only performs BGVIS; pages/config save own later reads."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    import python_samba.ui.main_window as main_window
    from python_samba.protocol.commands import FirmwareVersion
    from python_samba.services.session import ConnectionInfo
    from python_samba.ui.patches import apply_all_patches

    class FakeSession:
        def __init__(self):
            self.info = ConnectionInfo("mock")
            self.readonly = True
            self._connected = False
            self.open_count = 0
            self.extra_version_reads = 0

        @property
        def connected(self):
            return self._connected

        def open(self):
            self.open_count += 1
            self._connected = True
            return FirmwareVersion(
                3, 3, 122, 103, 9,
                "3 3 122 103 9 FWCompiler: 7004021 FWBldDate: Mar 15 2019 "
                "FWBldTime: 11:05:37 LibCompiler: 7004016 "
                "LibBldDate: Jun 24 2016 LibBldTime: 08:28:47",
            )

        def get_version(self):
            self.extra_version_reads += 1
            raise AssertionError("cached BGVIS result should be reused")

        def close(self):
            self._connected = False

    fake = FakeSession()
    monkeypatch.setattr(main_window, "open_mock", lambda **_kwargs: fake)

    class PatchedMainWindow(main_window.MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.backend.setCurrentText("mock")

    win.on_connect()
    win._update_fw_version_display()

    assert fake.open_count == 1
    assert fake.extra_version_reads == 0
    assert "Firmware Version: 3.3.122" in win.fw_version.text()
    assert "Auto-reading all controller parameters" not in win.log.toPlainText()
    assert "selected page reads its parameters on demand" in win.log.toPlainText()
    win.close()


def test_page_change_skips_global_refresh_and_duplicate_qt_event():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    controller_index = next(
        index for index in range(win.main_tabs.count())
        if win.main_tabs.tabText(index) == "Controller"
    )
    win.main_tabs.setCurrentIndex(controller_index)
    calls = []
    win._read_system_setting_reference = lambda: calls.append("page")
    win.on_refresh = lambda: (_ for _ in ()).throw(
        AssertionError("page change must not run the global refresh")
    )
    win.session = SimpleNamespace(connected=True)

    win._refresh_current_page(force=True)
    win._refresh_current_page()

    assert calls == ["page"]
    win.session = None
    win.close()


def test_reference_position_and_pneumatic_expanders():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()

    expected = {
        "pos_proximity_expander": True,
        "pos_excitation_expander": False,
        "pos_helping_expander": False,
        "pos_cascaded_expander": True,
        "pos_nonlinear_expander": True,
        "pneum_sensor_expander": True,
        "pneum_valve_matrix_expander": True,
        "pneum_valve_offsets_expander": True,
        "pneum_iso_dither_expander": True,
        "pneum_ramp_expander": False,
    }
    for name, initial in expected.items():
        expander = getattr(win, name)
        assert expander.is_expanded() is initial
        expander.title_button.click()
        assert expander.is_expanded() is (not initial)
        assert expander.content.isHidden() is initial
        expander.arrow_button.click()
        assert expander.is_expanded() is initial

    win.pneum_ramp_expander.set_expanded(True)
    assert not win.pneum_ramp_expander.content.isHidden()
    assert win.pneum_ramp_setpoint_grad.isVisibleTo(
        win.pneum_ramp_expander.content
    )
    win.close()


def test_bggsc_capabilities_hide_unsupported_groups():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win._controller_capabilities_loaded = True
    win._controller_features = frozenset(
        {"NAF", "TMPSENS", "PPILS", "SBTBV", "SEEXCIT", "PSUCL"}
    )
    win._apply_controller_capabilities()

    assert win.pos_cascaded_expander.isHidden()
    assert win.pneum_ramp_expander.isHidden()
    assert win.analysis_logging_group.isHidden()
    special_visibility = {
        win.special_tabs.tabText(index): win.special_tabs.isTabVisible(index)
        for index in range(win.special_tabs.count())
    }
    assert special_visibility["Safety"] is False
    assert special_visibility["System Safety"] is False
    assert special_visibility["Polynomials"] is True
    assert win._supports_controller_feature("auto_loop_switch") is True

    win._controller_features = frozenset({"NALS"})
    assert win._supports_controller_feature("auto_loop_switch") is False

    # Unknown capability state (for example if BGGSC itself is unavailable)
    # keeps controls visible instead of incorrectly denying a feature.
    win._controller_features = None
    win._apply_controller_capabilities()
    assert not win.pos_cascaded_expander.isHidden()
    assert not win.pneum_ramp_expander.isHidden()
    assert not win.analysis_logging_group.isHidden()
    win.close()


def test_visible_logging_writes_unpack_protocol_parameters():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    class FakeGate:
        def take_snapshot(self):
            return None

    class FakeSession:
        connected = True
        readonly = True

        def __init__(self):
            self.calls = []

        def set_event_trace_params(self, *params):
            self.calls.append(("DSETP", params))

        def set_event_signal(self, *params):
            self.calls.append(("DSETS", params))

        def set_monitor_signal(self, number, *params):
            self.calls.append(("DSMOS", number, params))

        def start_stop_event_tracing(self, status):
            self.calls.append(("DSSET", status))

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    fake = FakeSession()
    win.session = fake
    win.gate = FakeGate()
    win._confirm_write = lambda _summary: True
    win._run = lambda _title, work: work()

    win.log_params.setText("2 512 2 1 5 0")
    win.log_event.setText("1 2 3 4 5")
    win.log_mon_num.setValue(7)
    win.log_mon_sig.setText("0 10 0")
    win.on_logging_write_params()
    win.on_logging_write_event()
    win.on_logging_write_monitor()
    win.on_logging_startstop(1)

    assert fake.calls == [
        ("DSETP", ("2", "512", "2", "1", "5", "0")),
        ("DSETS", ("1", "2", "3", "4", "5")),
        ("DSMOS", 7, ("0", "10", "0")),
        ("DSSET", 1),
    ]

    fake.calls.clear()
    win.log_params.setText("0 0 0 1 5000 0")
    win.on_logging_write_params()
    assert fake.calls == []
    win.session = None
    win.close()


def test_iosignal_menu_preserves_original_token_triple():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    button = win.pos_noise_inject
    top_level = {action.text(): action for action in button.menu().actions()}
    assert {
        "Sensor", "Actuator", "Velocity Axes", "Position Axes",
        "Pneumatic Axes", "Excitation",
    } <= set(top_level)

    position_menu = top_level["Position Axes"].menu()
    xrot_menu = position_menu.actions()[0].menu()
    stage_one = next(
        action for action in xrot_menu.actions() if action.text() == "Stage1"
    )
    # The RCI stores Stage1 as SubIndex=0.  The old WPF IOSignalBtn adds one
    # only while constructing its display menu, then subtracts it again before
    # sending the IOType back to the controller.
    assert tuple(stage_one.data()) == (5, 0, 0)
    stage_one.trigger()
    assert button.io_tokens() == (5, 0, 0)
    assert "Xrot" in button.text()
    win.close()


def test_reference_refresh_populates_new_controller_and_velocity_fields():
    """Regression for fields that existed visually but were never refreshed."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.backend.setCurrentText("mock")
    win.on_connect()

    assert win._auto_refresh is True
    assert win._refresh_timer.isActive()

    win.on_adc_read()
    assert win.adc_set_num.currentIndex() == 3
    assert win.adc_set_num.currentText() == "18"

    win.on_motor_prot_read()
    assert win.power_supply_expander.title_button.text() == "Power Supply Current Limit"
    assert win.ps_current_limit.text() == "1000"
    assert win.ps_current_si_unit.text() == "1"
    assert [editor.text() for editor in win.ps_actual_values] == ["0"] * 5

    win.on_vel_read_all_filters()
    assert [editor.text() for editor in win.vel_axis_limiters] == ["1000"] * 6
    win.on_diag_read()
    assert len(win.exc_filter_buttons) == 4
    assert win.exc_filter_buttons[1]._lab.text() == "---"

    win.on_disconnect()
    assert not win._refresh_timer.isActive()
    win.close()


def test_adc_set_display_and_legacy_motor_offsets_follow_firmware_contract(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.backend.setCurrentText("mock")
    win.on_connect()
    win._refresh_timer.stop()
    win.gate.snapshot_dir = tmp_path / "snapshots"

    # NGASN value 7 is the eighth ADC set and is presented as 40 channels.
    win.session.transport.state.adc_set_num = 7
    win.on_adc_read()
    assert win.adc_set_num.currentIndex() == 7
    assert win.adc_set_num.currentText() == "40"

    # Emulate the connected V3.3.122 controller, which has no SALMO marker.
    state = win.session.transport.state
    state.global_constants = [
        token for token in state.global_constants if token.upper() != "SALMO"
    ]
    state.motor_offsets = [10, 20, 30, 40, 50, 60, 70, 80, 111, 222, 333]
    win._controller_capabilities_loaded = False
    win._controller_features = None
    win.on_motor_prot_read()

    assert [win.mot_offsets[index].text() for index in (0, 1, 3, 4, 6, 7, 9, 10)] == [
        "60", "10", "20", "50", "80", "30", "40", "70",
    ]
    for index in (2, 5, 8, 11):
        assert win.mot_offsets[index].isReadOnly()
        assert win.mot_offsets[index].text() == ""

    win.mot_offsets[0].setText("61")
    win.on_motor_offset_write()
    assert state.motor_offsets[5] == pytest.approx(61)
    assert state.motor_offsets[8:] == pytest.approx([111, 222, 333])

    win.on_disconnect()
    win.close()


def test_iosignal_names_and_filter_bypass_match_source_ui():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.protocol.codes import filter_small_name
    from python_samba.ui.classic_widgets import FilterStageCell, IOSignalButton

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    button = IOSignalButton(tokens=(2, 0, 6))

    assert len(IOSignalButton.INPUT_NAMES) == 46
    assert IOSignalButton.INPUT_NAMES[25] == "Prox1-Off"
    assert button.text() == "Vel Xtrans Stage7"
    assert IOSignalButton.format_io_signal((4, 0, 0)) == "Vel Xtrans Output"
    assert IOSignalButton.format_io_signal((5, 0, 0)) == "Pos Xrot Stage1"
    assert IOSignalButton.format_io_signal((8, 0, 0)) == "Pneu Ztpneu Stage1"

    top_level = {action.text(): action for action in button.menu().actions()}
    assert "Temperature Sensor" in top_level
    velocity = top_level["Velocity Axes"].menu().actions()[0].menu()
    output = next(action for action in velocity.actions() if action.text() == "Output")
    assert tuple(output.data()) == (4, 0, 0)

    bypass = FilterStageCell(0, "NOFIL")
    assert bypass._lab.text() == "---"
    assert filter_small_name(0) == "---"


def test_status_event_group_and_hex_format_match_legacy_page(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.backend.setCurrentText("mock")
    win.on_connect()
    monkeypatch.setattr(
        win.session,
        "get_amplifier_disable_events",
        lambda: [1, 0x40, 0x100] + [0] * 7,
    )

    win._refresh_status_reference()
    assert any(
        group.title() == "Event" for group in win.findChildren(QtWidgets.QGroupBox)
    )
    assert win.status_events.rowCount() == 3
    assert [win.status_events.item(row, 1).text() for row in range(3)] == [
        "1", "40", "100"
    ]
    win.close()


def test_pneumatic_filter_cell_opens_dialog_with_pneumatic_axes(monkeypatch):
    """Regression: clicking a pneumatic cell must not reference a stale name."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    import python_samba.ui.main_window as main_window
    from python_samba.ui.extra_pages import PNEUM_AXIS_LABELS
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(main_window.MainWindow):
        pass

    class DummySignal:
        def connect(self, _slot):
            pass

    class DummyCombo:
        def setCurrentIndex(self, _index):
            pass

        def setEnabled(self, _enabled):
            pass

    captured = {}

    class DummyFilterDlg:
        def __init__(self, axis_labels, **_kwargs):
            captured["axis_labels"] = axis_labels
            self.axis_cbx = DummyCombo()
            self.filterChanged = DummySignal()

        def setWindowTitle(self, _title):
            pass

        def set_stage(self, _stage):
            pass

        def exec(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_window, "FilterDlg", DummyFilterDlg)
    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()

    win._on_pneum_filter_cell_clicked(0, 0)

    assert captured["axis_labels"] == PNEUM_AXIS_LABELS
    win.close()


def test_new_alignment_controls_use_legacy_names_and_fixed_numbers():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.classic_widgets import (
        IOSignalButton,
        SciEdit,
        format_ui_number,
    )
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()

    assert [win.vel_measure_stage.itemText(i) for i in range(
        win.vel_measure_stage.count()
    )] == [
        "Raw", "Stage1", "Stage2", "Stage3", "Stage4",
        "Stage5", "Stage6", "Stage7", "Output",
    ]
    win.vel_measure_stage.setCurrentIndex(8)
    assert win.diag_1.io_tokens() == (2, 0, 7)
    win._on_vel_help_selection_changed(2)
    assert win.diag_1.io_tokens() == (2, 2, 7)
    assert win.noise_inject.io_tokens() == (4, 2, 0)

    assert win.perf_signal.property("io_tokens") == (0, 0, 0)
    assert [action.text() for action in win.perf_signal.menu().actions()] == (
        IOSignalButton.INPUT_NAMES
    )
    win._set_system_io_button(win.perf_signal, (0, 10, 0))
    assert win.perf_signal.text() == "Prox1"
    win._set_system_io_button(win.perf_signal, (1, 0, 0))
    assert win.perf_signal.text() == "OutX1"

    assert win.ff_source_cbx[0].count() == 46
    assert win.ff_source_cbx[0].itemText(10) == "Prox1"
    win._controller_capabilities_loaded = True
    win._input_signal_count = 37
    win._init_ff_source_combos()
    win._rebuild_system_io_menu(win.perf_signal)
    assert win.ff_source_cbx[0].count() == 37
    assert len(win.perf_signal.menu().actions()) == 37

    win._update_proximity_offset_widgets([1, 2, 3, 11, 12, 13], 6)
    assert [win.prox_edits[index].text() for index in (0, 1, 2, 4, 5, 6)] == [
        "1", "2", "3", "11", "12", "13",
    ]
    assert win.prox_edits[3].isHidden()
    assert win.prox_edits[7].isHidden()

    assert format_ui_number(1e-7) == "0.0000001"
    assert format_ui_number(1e20) == "100000000000000000000"
    assert format_ui_number(-0.0) == "0"
    edit = SciEdit("1.00000e-009")
    assert edit.text() == "0.000000001"
    edit.setText("FWCompiler:")
    assert edit.text() == "FWCompiler:"
    win.close()


def test_visible_page_timer_refreshes_status_position_and_pneumatic():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    class FakeSession:
        connected = True
        readonly = True

        def __init__(self):
            self.loop_calls = 0
            self.position_live_calls = 0
            self.pneumatic_calls = 0
            self.controller_config_calls = 0
            self.constants = [
                "0", "6", "3", "6", "7", "6", "7", "4", "8", "3", "5000",
                "PNEUMIO#4#4",
            ]

        def get_loop_status(self):
            self.loop_calls += 1
            return SimpleNamespace(individual=0x07, system=0x45)

        def get_switch_status(self):
            return ["0x60", "0"]

        def get_switch_conditions(self):
            return ["70", "0.5", "15", "0", "0"]

        def get_pos_pneum_digital_status(self):
            return 0x05, 0x03, 0, 0

        def get_global_system_constants(self):
            return list(self.constants)

        def get_controller_config(self):
            self.controller_config_calls += 1
            return ["F7"]

        def get_proximity_input_values(self, count):
            self.position_live_calls += 1
            assert count == 6
            return [101, 102, 103, 111, 112, 113]

        def get_pneumatic_axes_status(self):
            self.pneumatic_calls += 1
            return [7, 9, 1, 2, 3, 4, 5, 6]

        def get_pneumatic_heights_valves(self):
            self.pneumatic_calls += 1
            return [1.1, 1.2, 1.3, 1.4, 0.1, 0.2, 0.3, 0.4]

        def get_pneumatic_status_timer(self):
            self.pneumatic_calls += 1
            return 12.5, 0.25

        def get_pneumatic_setpoint_status(self):
            self.pneumatic_calls += 1
            return 1

        def close(self):
            pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    fake = FakeSession()

    controller_index = next(
        i for i in range(win.main_tabs.count())
        if win.main_tabs.tabText(i) == "Controller"
    )
    win.main_tabs.setCurrentIndex(controller_index)
    win.session = fake
    win._on_timer_tick()
    assert fake.controller_config_calls == 1
    assert [lamp.text() for lamp in win.system_loop_lamps] == ["ON"] * 7

    win.session = None
    status_index = next(
        i for i in range(win.main_tabs.count())
        if win.main_tabs.tabText(i) == "Status"
    )
    win.main_tabs.setCurrentIndex(status_index)
    win.session = fake
    win._on_timer_tick()
    assert win.status_loop_badges["Velocity Loop"].text() == "ON"
    assert win.status_loop_badges["Position Loop"].text() == "ON"
    assert win.status_position_axis_lamps[0].text() == "ON"
    assert win.status_position_axis_lamps[1].text() == "OFF"

    win.session = None
    position_index = next(
        i for i in range(win.main_tabs.count())
        if win.main_tabs.tabText(i) == "Position"
    )
    win.main_tabs.setCurrentIndex(position_index)
    position_tabs = win.main_tabs.currentWidget().findChild(type(win.main_tabs))
    proxy_index = next(
        i for i in range(position_tabs.count())
        if position_tabs.tabText(i) == "Proxy Adjustment"
    )
    position_tabs.setCurrentIndex(proxy_index)
    win._controller_capabilities_loaded = False
    win._last_proximity_offsets = [1, 2, 3, 11, 12, 13]
    win.proxy_si_unit_edits["Prox1"].setText("2")
    win.session = fake
    win._on_timer_tick()
    assert fake.position_live_calls == 1
    assert win.proxy_value_labels["Prox1"].text() == "50.5"
    assert win.proxy_value_labels["ProxH1"].text() == "111"
    assert win.proxy_cards["Prox4"].isHidden()

    win.session = None
    pneumatic_index = next(
        i for i in range(win.main_tabs.count())
        if win.main_tabs.tabText(i) == "Pneumatic"
    )
    win.main_tabs.setCurrentIndex(pneumatic_index)
    win._controller_capabilities_loaded = False
    win.session = fake
    win._on_timer_tick()
    assert fake.pneumatic_calls == 4
    assert win._pneu_status_items["Status"].text(1) == "OK"
    assert win._pneu_status_items["Status"].text(3) == "RefPoint"
    assert win._pneu_status_items["Status"].text(4) == "9"
    assert win._pneu_status_items["AxesInput"].text(1) == "1"
    assert win._pneu_status_items["HeightSet1"].text(1) == "1.1"
    assert win._pneu_status_items["ValveSet1"].text(4) == "0.4"
    assert win._pneu_status_items["TimerStatus"].text(1) == "12.5"
    assert win._pneu_status_items["TimerStatus"].text(4) == "0.25"
    assert win.pneum_loop_leds["pneu"].text() == "ON"
    assert win.pneum_loop_leds["use_setpoint_all"].text() == "ON"
    assert win.pneum_loop_leds["ref_metrology"].text() == "OFF"
    assert win._pneu_status_items["ValveSet2"].isHidden()
    assert win._pneu_status_items["HeightSet2"].isHidden()
    assert win._pneu_status_items["PosError"].isHidden()
    win.close()


def test_proximity_si_units_scale_cards_and_parse_legacy_xml(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from _patches.signal_progress_patch import _read_si_units_file
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win._last_proximity_values = [108, 54, 27, 216, 162, 81, 270, 324]
    win._update_proxy_readouts_from_raw(win._last_proximity_values)
    assert win.proxy_value_labels["Prox1"].text() == "108"

    win.proxy_si_unit_edits["Prox1"].setText("5.4")
    win.proxy_si_unit_edits["Prox1"].editingFinished.emit()
    assert win.proxy_value_labels["Prox1"].text() == "20"

    si_file = tmp_path / "legacy.SI"
    si_file.write_text(
        """<SIUnits><SIUnit Name="Displacement"><ArraySIFactor>
        <SIFactor><Name>InpZ4Prox</Name><Value>5.4</Value></SIFactor>
        <SIFactor><Name>InpH1Prox</Name><Value>2</Value></SIFactor>
        </ArraySIFactor></SIUnit></SIUnits>""",
        encoding="utf-8",
    )
    _read_si_units_file(win, str(si_file))
    assert win.proxy_si_unit_edits["Prox4"].text() == "5.4"
    assert win.proxy_si_unit_edits["ProxH1"].text() == "2"
    # Raw index 6 is Prox4 in display order; raw index 3 is ProxH1.
    assert win.proxy_value_labels["Prox4"].text() == "50"
    assert win.proxy_value_labels["ProxH1"].text() == "108"
    win.close()


def test_loop_matrix_and_protection_buttons_write_without_generic_confirmation(
    tmp_path, monkeypatch
):
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtTest, QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.backend.setCurrentText("mock")
    win.on_connect()
    win._refresh_timer.stop()
    win.gate.snapshot_dir = tmp_path / "snapshots"
    win.show()
    app.processEvents()

    def select_page(title: str) -> None:
        index = next(
            i for i in range(win.main_tabs.count())
            if win.main_tabs.tabText(i) == title
        )
        win.main_tabs.setCurrentIndex(index)
        app.processEvents()

    def mouse_click(button) -> None:
        assert button.isVisibleTo(win)
        assert button.isEnabled()
        QtTest.QTest.mouseClick(
            button,
            QtCore.Qt.LeftButton,
            pos=button.rect().center(),
        )
        app.processEvents()

    def unexpected_dialog(*_args, **_kwargs):
        pytest.fail("ordinary parameter change opened a confirmation dialog")

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", unexpected_dialog)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", unexpected_dialog)

    # Legacy Protection controls only NVRAM Save/Clear, not global writes.
    assert win.protection_led.text() == "ON"
    win.protection_led.click()
    assert win.protection_led.text() == "OFF"
    assert not win.session.readonly
    assert win.nvram_save_button.isEnabled()
    assert win.nvram_clear_button.isEnabled()

    before_loop = win.session.get_loop_status()
    win.loop_states.loop_btns["pneumatic"].click()
    after_loop = win.session.get_loop_status()
    assert after_loop.individual == before_loop.individual
    assert after_loop.system == before_loop.system ^ 0x40
    win.loop_states.loop_btns["pneumatic"].click()  # restore

    # Status-page lamps route to the same real BSSTS handler.
    before_ff = win.session.get_loop_status().system
    win.status_loop_badges["Feed Forward"].click()
    assert win.session.get_loop_status().system == before_ff ^ 0x04
    win.status_loop_badges["Feed Forward"].click()  # restore

    # The mock advertises automatic loop switching, so RunningV/RunningP are
    # status-only exactly as in the legacy UI.  NALS firmware enables them.
    assert not win.loop_states.loop_btns["velocity"].isEnabled()
    assert not win.status_loop_badges["Position Loop"].isEnabled()
    win._controller_features = frozenset({"NALS"})
    win._refresh_status_loop_state()
    assert win.loop_states.loop_btns["velocity"].isEnabled()
    assert win.status_loop_badges["Position Loop"].isEnabled()

    conditions = win.session.get_switch_conditions()
    before_config = int(conditions[3], 0)
    win.loop_states.loop_btns["velocity"].click()
    after_config = int(win.session.get_switch_conditions()[3], 0)
    assert after_config == before_config ^ 0x20
    win.loop_states.loop_btns["velocity"].click()  # restore

    before_outputs = int(win.session.get_ff_parameters(0)[0], 16)
    assert win.ff_status_rows[0][1]._clickable
    win.ff_status_rows[0][1].clicked.emit()
    assert int(win.session.get_ff_parameters(0)[0], 16) == before_outputs ^ 0x02
    win.ff_status_rows[0][1].clicked.emit()  # restore

    for button, bit in (
        (win.ff_led_active, 0x0004),
        (win.ff_led_adapt, 0x0002),
        (win.ff_led_rawinput, 0x1000),
    ):
        before_system = win.session.get_loop_status().system
        button.click()
        assert win.session.get_loop_status().system == before_system ^ bit
        button.click()  # restore

    select_page("Feed Forward")
    before_individual = win.session.get_loop_status().individual
    mouse_click(win.ff_individual_loop_leds[0])
    assert win.session.get_loop_status().individual == before_individual ^ 0x01
    mouse_click(win.ff_individual_loop_leds[0])  # restore

    before_pff_outputs = int(win.session.get_pff_parameters(0)[0], 16)
    assert win.pff_status_rows[0][2]._clickable
    win.pff_status_rows[0][2].clicked.emit()
    assert int(win.session.get_pff_parameters(0)[0], 16) == before_pff_outputs ^ 0x04
    win.pff_status_rows[0][2].clicked.emit()  # restore
    for button, bit in (
        (win.pff_active_led, 0x4000),
        (win.pff_adaptive_led, 0x8000),
    ):
        before_system = win.session.get_loop_status().system
        button.click()
        assert win.session.get_loop_status().system == before_system ^ bit
        button.click()  # restore


    select_page("Pneum. SFF")
    before_position, before_pneumatic, _din, _dout = (
        win.session.get_pos_pneum_digital_status()
    )
    mouse_click(win.pff_individual_loop_leds[1])
    after_position, after_pneumatic, _din, _dout = (
        win.session.get_pos_pneum_digital_status()
    )
    assert after_position == before_position
    assert after_pneumatic == before_pneumatic ^ 0x02
    mouse_click(win.pff_individual_loop_leds[1])  # restore

    select_page("Pneumatic")
    before_position, before_pneumatic, _din, _dout = (
        win.session.get_pos_pneum_digital_status()
    )
    mouse_click(win.pneum_individual_loop_leds[2])
    after_position, after_pneumatic, _din, _dout = (
        win.session.get_pos_pneum_digital_status()
    )
    assert after_position == before_position
    assert after_pneumatic == before_pneumatic ^ 0x04
    mouse_click(win.pneum_individual_loop_leds[2])  # restore

    for key, bit in (
        ("pneu", 0x00040),
        ("move_up_startup", 0x00008),
        ("dither_comp", 0x02000),
        ("ref_metrology", 0x20000),
    ):
        before_system = win.session.get_loop_status().system
        mouse_click(win.pneum_loop_leds[key])
        assert win.session.get_loop_status().system == before_system ^ bit
        mouse_click(win.pneum_loop_leds[key])  # restore

    before_setpoint = win.session.get_pneumatic_setpoint_status()
    mouse_click(win.pneum_loop_leds["use_setpoint_all"])
    assert win.session.get_pneumatic_setpoint_status() == (0 if before_setpoint else 1)
    mouse_click(win.pneum_loop_leds["use_setpoint_all"])  # restore
    win.on_disconnect()
    win.close()


def test_pneumatic_expanders_use_natural_height_without_overlap_caps():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    win.show()
    app.processEvents()

    for expander in (
        win.pneum_sensor_expander,
        win.pneum_valve_matrix_expander,
        win.pneum_iso_dither_expander,
    ):
        content = expander.content
        assert content.maximumHeight() > 1_000_000
        assert content.sizePolicy().verticalPolicy() == QtWidgets.QSizePolicy.Preferred
        expander.set_expanded(False)
        expander.set_expanded(True)
    app.processEvents()
    idg = win.pneum_iso_dither_expander.content.layout()
    assert idg.verticalSpacing() >= 5
    win.close()
