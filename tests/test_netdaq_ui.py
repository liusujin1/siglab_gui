from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import numpy as np
from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest

from python_vna.app import build_backend, parse_args
from python_vna.controller import VnaController
from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings
from python_vna.storage import default_session_config
from python_vna.ui.main_window import MainWindow, MCSetupDialog
from python_vna.ui.netdaq_settings import DeviceConnectionDialog
from python_vna.ui.offline_daq_tools import DaspImportDialog, QualityReportDialog, DcRemovalDialog


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_VNA_UI_SETTINGS", str(tmp_path / "ui.json"))
    session = default_session_config()
    window = MainWindow(VnaController(build_backend("simulated"), session), session)
    window._device_poll_timer.stop()
    yield window
    window._close_confirmed = True
    window.close()
    app.processEvents()


def test_ni_remains_default_and_netdaq_cli_is_explicit():
    assert parse_args([]).backend == "ni"
    args = parse_args(["--backend", "netdaq", "--netdaq-host", "127.0.0.1"])
    assert args.netdaq_host == "127.0.0.1"
    assert args.netdaq_profile == ""


def test_connection_entry_unknown_profile_gates_controls_and_indicator(window):
    assert window.device_connection_action.text() == "设备连接..."
    window.controller.backend = NetDaqBackend(NetDaqSettings(host="127.0.0.1"))
    window._apply_netdaq_limits()
    assert not window.start_button.isEnabled()
    assert not window.avg_button.isEnabled()
    assert not window.record_button.isEnabled()
    assert not window.trigger_mode_combo.isEnabled()
    assert not window.exc_enable.isEnabled()
    window._set_device_connected(True)
    assert window.device_status_light.property("connected")
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.start_button.isEnabled()
    assert window.trigger_mode_combo.isEnabled()
    assert window.exc_enable.isEnabled()
    window._set_device_connected(True)
    assert not window.device_status_light.property("connected")


def _usb_channel_setup_backend():
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    entries = tuple({
        "wire_number": i, "channel_type": 0, "input_mode": "voltage_single_ended_dc",
        "options": [{"coupling": "dc", "full_scale_v": 10, "configuration_hex": "01"}],
    } for i in range(4))
    backend.profile = NetDaqProfile(
        "SYNTHETIC UI ONLY", b"", "raw32", bytes(13), 0, 64000,
        entries, "test", "No hardware qualification", transport="usb")
    return backend


def test_usb_mc_setup_can_disable_ch1_and_use_ch2_as_reference_and_trigger(window):
    window.controller.backend = _usb_channel_setup_backend()
    window._apply_netdaq_limits()
    dialog = MCSetupDialog(window)
    try:
        assert dialog.table.item(0, 0).flags() & QtCore.Qt.ItemIsEnabled
        for row in (0, 2, 3):
            dialog.table.item(row, 0).setCheckState(QtCore.Qt.Unchecked)
        dialog._apply_to_main()
        session = window._read_session_from_widgets()
        assert [c.name for c in session.ai_channels if c.enabled] == ["ai1"]
        assert session.acquisition.reference_channel == "ai1"
        assert [c.name for c in session.ai_channels if c.is_reference] == ["ai1"]
        window.trigger_mode_combo.setCurrentIndex(
            window.trigger_mode_combo.findData("Every Frame"))
        session = window._read_session_from_widgets()
        assert session.acquisition.trigger.source == "ai1"
        assert session.acquisition.trigger.enabled
        dialog.reload_from_main()
        assert dialog.table.item(0, 0).checkState() == QtCore.Qt.Unchecked
        for row in range(dialog.table.rowCount()):
            dialog.table.item(row, 0).setCheckState(QtCore.Qt.Unchecked)
        assert any(dialog.table.item(row, 0).checkState() == QtCore.Qt.Checked
                   for row in range(dialog.table.rowCount()))
    finally:
        dialog.close()


def test_usb_mc_setup_and_matrix_show_hardware_options_read_only(window):
    window.controller.backend = _usb_channel_setup_backend()
    window._apply_netdaq_limits()
    window._sync_channel_grid()
    dialog = MCSetupDialog(window)
    try:
        for row in range(4):
            for column in (1, 2):
                assert not dialog.table.item(row, column).flags() & QtCore.Qt.ItemIsEditable
                assert "只读" in dialog.table.item(row, column).toolTip()
            for column in (2, 3):
                assert not window.channel_grid.item(row, column).flags() & QtCore.Qt.ItemIsEditable
            assert dialog.table.item(row, 5).flags() & QtCore.Qt.ItemIsEditable
    finally:
        dialog.close()
    window.controller.backend = build_backend("simulated")
    dialog = MCSetupDialog(window)
    try:
        assert not dialog.table.item(0, 0).flags() & QtCore.Qt.ItemIsEnabled
        assert dialog.table.item(0, 1).flags() & QtCore.Qt.ItemIsEditable
    finally:
        dialog.close()


def test_dasp_unknown_frontend_is_not_displayed_as_hardware_dc(window):
    backend = _usb_channel_setup_backend()
    for entry in backend.profile.channels:
        entry["input_mode"] = "device_fixed_unknown"
    backend.profile = replace(backend.profile, qualification="dasp_parameters",
                              vendor_parameters={"sensor_defaults": []})
    window.controller.backend = backend
    window.channel_list.setCurrentRow(1)
    window._apply_netdaq_limits()
    window._sync_channel_grid()
    assert window.channel_coupling_combo.currentText() == "设备"
    assert window.channel_coupling_combo.currentData() == "dc"
    assert window.channel_full_scale_combo.currentText().endswith("*")
    count = window.channel_full_scale_combo.count()
    for _ in range(5):
        window._read_session_from_widgets()
        window._load_channel_editor_from_row(1)
    assert window.channel_full_scale_combo.count() == count
    assert window.channel_grid.item(1, 3).text() == "设备当前模式"
    dialog = MCSetupDialog(window)
    try:
        assert dialog.table.horizontalHeaderItem(2).text() == "输入模式"
        assert dialog.table.item(1, 2).text() == "设备当前模式"
        assert "标称" in dialog.table.item(1, 1).text()
        dialog._apply_to_main()
        session = window._read_session_from_widgets()
        assert session.ai_channels[1].coupling == "dc"
        assert session.ai_channels[1].full_scale == 10
    finally:
        dialog.close()
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.channel_coupling_combo.currentText() == "dc"
    assert not window.channel_full_scale_combo.currentText().endswith("*")


def test_verified_iepe_frontend_display_keeps_independent_coupling_value(window):
    backend = _usb_channel_setup_backend()
    backend.profile.channels[1].update(
        input_mode="iepe", iepe_supply="device_fixed", iepe_current_ma=2.0)
    backend.profile.channels[1]["options"][0]["coupling"] = "ac"
    window.controller.backend = backend
    window.channel_list.setCurrentRow(1)
    window._apply_netdaq_limits()
    assert window.channel_coupling_combo.currentText() == "IEPE"
    assert window.channel_coupling_combo.currentData() == "ac"
    assert window._read_session_from_widgets().ai_channels[1].iepe_enabled
    dialog = MCSetupDialog(window)
    try:
        assert dialog.table.item(1, 2).text() == "IEPE（固定）"
        dialog._apply_to_main()
        assert window._read_session_from_widgets().ai_channels[1].coupling == "ac"
    finally:
        dialog.close()


def test_tcp_editable_coupling_is_not_replaced_by_fixed_input_mode_label(window):
    backend = _usb_channel_setup_backend()
    backend.settings = replace(backend.settings, transport="tcp")
    backend.profile = replace(backend.profile, transport="tcp")
    backend.profile.channels[1].update(
        input_mode="iepe", iepe_supply="device_fixed", iepe_current_ma=2.0)
    window.controller.backend = backend
    window._apply_netdaq_limits()
    dialog = MCSetupDialog(window)
    try:
        assert dialog.table.item(1, 2).text() in {"ac", "dc", "bias"}
        assert dialog.table.item(1, 2).flags() & QtCore.Qt.ItemIsEditable
        dialog.table.item(1, 2).setText("dc")
        dialog._apply_to_main()
        assert window._read_session_from_widgets().ai_channels[1].coupling == "dc"
    finally:
        dialog.close()


@pytest.mark.parametrize("record", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_netdaq_stop_does_not_hide_data_error(app, tmp_path, monkeypatch, record, corrupt):
    from python_vna.daq.netdaq_protocol import NetDaqProtocolError, NetDaqStopped
    from python_vna.ui.main_window import AcquisitionWorker, ContinuousRecordingWorker

    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    session = default_session_config()
    controller = VnaController(backend, session)
    for name in ("configure", "start", "stop", "close"):
        monkeypatch.setattr(backend, name, lambda *a, **kw: None)
    monkeypatch.setattr(backend, "acquisition_provenance", lambda: {"backend": "netdaq"})
    worker = (ContinuousRecordingWorker(controller, None, tmp_path / "record")
              if record else AcquisitionWorker(controller, None))
    def read():
        worker.request_stop()
        raise (NetDaqProtocolError("injected corrupt USB frame") if corrupt
               else NetDaqStopped("requested cancellation"))
    monkeypatch.setattr(backend, "read_frame", read)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert bool(errors) == corrupt
    if corrupt:
        assert any("corrupt USB" in error for error in errors)
    if record:
        manifest = json.loads((tmp_path / "record/manifest.json").read_text())
        assert manifest["completed"] == (not corrupt)
        if corrupt:
            assert "corrupt USB" in manifest["error"]


@pytest.mark.parametrize("record", [False, True])
def test_netdaq_close_error_is_reported_and_record_marked_incomplete(
        app, tmp_path, monkeypatch, record):
    from python_vna.daq.base import DaqBackendError
    from python_vna.daq.netdaq_protocol import NetDaqStopped
    from python_vna.ui.main_window import AcquisitionWorker, ContinuousRecordingWorker

    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    controller = VnaController(backend, default_session_config())
    for name in ("configure", "start", "stop"):
        monkeypatch.setattr(backend, name, lambda *a, **kw: None)
    monkeypatch.setattr(backend, "acquisition_provenance", lambda: {"backend": "netdaq"})
    worker = (ContinuousRecordingWorker(controller, None, tmp_path / "record")
              if record else AcquisitionWorker(controller, None))
    def read():
        worker.request_stop()
        raise NetDaqStopped("requested cancellation")
    def close():
        raise DaqBackendError("injected release failure")
    monkeypatch.setattr(backend, "read_frame", read)
    monkeypatch.setattr(backend, "close", close)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert any("release failure" in error for error in errors)
    if record:
        manifest = json.loads((tmp_path / "record/manifest.json").read_text())
        assert not manifest["completed"]
        assert "release failure" in manifest["error"]


def test_dasp_preview_import_selection_and_units(app, tmp_path):
    path = tmp_path / "Channel.ini"
    path.write_text("[Channel 0]\nUnit=V\nSensitivity Physical Unit=V\nVoltage Unit=V\nSensitivity=2\n"
                    "[Channel 1]\nUnit=m/s^2\nSensitivity Physical Unit=m/s^2\nSensitivity=50\nGain=2\nExternal gain=10\n",
                    encoding="utf-8")
    dialog = DaspImportDialog(path)
    assert dialog.table.rowCount() == 2
    assert dialog.channels[1].eu_per_volt == 1
    assert "Gain=2" in dialog.table.item(1, 4).toolTip()
    assert "External gain=10" in dialog.table.item(1, 4).toolTip()
    dialog.table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
    assert [c.index for c in dialog.selected_channels()] == [1]
    dialog.reject()
    dialog.deleteLater()


def test_dc_removal_dialog_keeps_channel_settings_independent_and_cancel_is_safe(app):
    s = default_session_config()
    dialog = DcRemovalDialog(s)
    assert not dialog.table.cellWidget(0, 3).isEnabled()
    dialog.table.item(0, 2).setCheckState(QtCore.Qt.Checked)
    assert dialog.table.cellWidget(0, 3).isEnabled()
    dialog.table.cellWidget(0, 3).setValue(.2)
    assert not s.ai_channels[0].dc_removal_enabled
    dialog.accept()
    assert dialog.channels[0].dc_removal_enabled
    assert dialog.channels[0].dc_removal_cutoff_hz == .2
    assert not dialog.channels[1].dc_removal_enabled
    dialog.deleteLater()


def test_dc_removal_ui_apply_and_sensor_edits_keep_configuration(window):
    window._device_refresh_thread = None
    window.controller.backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    window._apply_netdaq_limits()
    assert window.dc_removal_action.isEnabled()

    def accept(dialog):
        dialog.table.item(0, 2).setCheckState(QtCore.Qt.Checked)
        dialog.table.cellWidget(0, 3).setValue(.3)
        dialog.accept()
        return QtWidgets.QDialog.Accepted

    with mock.patch.object(DcRemovalDialog, "exec", accept):
        window._configure_dc_removal()
    channel = window._read_session_from_widgets().ai_channels[0]
    assert channel.dc_removal_enabled and channel.dc_removal_cutoff_hz == .3
    window.channel_sensitivity_edit.setValue(1.96133)
    assert window._read_session_from_widgets().ai_channels[0].dc_removal_enabled
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert not window.dc_removal_action.isEnabled()


def test_dasp_sensor_can_move_to_ch1_without_hardware_or_second_scaling(app, tmp_path):
    from python_vna.daq.dasp_import import apply_dasp_channels

    path = tmp_path / "Channel.ini"
    path.write_text("[Channel 1]\nLabel=Top X\nTransducer=SAE300X1C_X\nUnit=m/s^2\n"
                    "Sensitivity Physical Unit=m/s^2\nSensitivity=511\nGain=1\nExternal gain=1\n",
                    encoding="utf-8")
    dialog = DaspImportDialog(path, channel_names=["ai0", "ai1", "ai2", "ai3"])
    assert "SAE300X1C_X" in dialog.table.item(0, 2).text()
    target = dialog.table.cellWidget(0, 6)
    target.setCurrentIndex(target.findData(0))
    selected = dialog.selected_channels()
    assert dialog.channels[0].index == 1
    assert selected[0].index == 0
    session = default_session_config()
    before = asdict(session.ai_channels[0])
    apply_dasp_channels(session.ai_channels, selected)
    actual = asdict(session.ai_channels[0])
    assert actual["sensitivity"] == pytest.approx(1000 / 511)
    assert actual["engineering_unit"] == "m/s^2"
    for field in ("offset", "full_scale", "coupling", "iepe_enabled", "iepe_current_ma"):
        assert actual[field] == before[field]
    assert session.ai_channels[1].sensitivity == 1
    dialog.accept()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    dialog.deleteLater()


def test_dasp_import_duplicate_or_missing_destinations_cannot_apply(app, tmp_path):
    path = tmp_path / "Channel.ini"
    path.write_text("[Channel 0]\nSensitivity=50\n[Channel 1]\nSensitivity=100\n",
                    encoding="utf-8")
    dialog = DaspImportDialog(path, channel_names=["ai0"])
    with pytest.raises(ValueError, match="同一个目标"):
        dialog.selected_channels()
    with mock.patch.object(QtWidgets.QMessageBox, "warning") as warning:
        dialog.accept()
    assert warning.called and dialog.result() != QtWidgets.QDialog.Accepted
    dialog.reject()
    missing = DaspImportDialog(path, channel_names=[])
    with pytest.raises(ValueError, match="没有可导入"):
        missing.selected_channels()
    dialog.deleteLater()
    missing.deleteLater()


def test_profile_preview_is_offline_even_without_device_address(app, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}")
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings())
    dialog.profile_edit.setText(str(path))
    with mock.patch.object(NetDaqBackend, "probe", side_effect=AssertionError("hardware access")):
        dialog._preview_profile()
    report = json.loads(dialog.report.toPlainText())
    assert not report["valid"] and not report["hardware_accessed"]
    dialog.reject()
    dialog.deleteLater()


def test_replay_ui_loads_channel_metadata_and_cannot_turn_indicator_green(window, tmp_path):
    from python_vna.daq.replay import ReplayBackend
    from python_vna.continuous_recording import ContinuousDatWriter
    from python_vna.daq.base import BackendFrame
    session = default_session_config()
    for c in session.ai_channels:
        c.enabled = c.name in {"ai0", "ai2"}
    session.ai_channels[2].label = "顶部Z"
    folder = tmp_path / "record"
    writer = ContinuousDatWriter(folder, session, device_name=None, channel_names=["ai0", "ai2"],
                                 software_version="test", storage_format="binary")
    writer.write_frame(BackendFrame(2560, ["ai0", "ai2"], np.ones((2, 8)), np.arange(8) / 2560, 0))
    writer.close()
    backend = ReplayBackend(folder, realtime=False)
    window.controller.backend = backend
    window._apply_device_list(backend.list_devices())
    window._apply_netdaq_limits()
    assert window.channel_table.rowCount() == 2
    assert window.session.ai_channels[1].label == "顶部Z"
    assert window.trigger_mode_combo.isEnabled()
    assert not window.exc_enable.isEnabled()
    assert not window.device_status_light.property("connected")


def test_quality_dialog_exports_utf8_bom_json_without_extension(app, tmp_path):
    report = {"note": "采集报告", "channels": {}}
    dialog = QualityReportDialog(report, tmp_path)
    with mock.patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(tmp_path / "报告"), "JSON")):
        dialog._export()
    path = tmp_path / "报告.json"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(path.read_text(encoding="utf-8-sig")) == report
    dialog.close()
    dialog.deleteLater()


def test_theme_save_preserves_connection_preferences(window):
    preferences = {"theme": "dark", "netdaq": asdict(NetDaqSettings(host="192.0.2.10"))}
    window._ui_settings_path.write_text(json.dumps(preferences), encoding="utf-8")
    window._set_theme("light")
    loaded = json.loads(window._ui_settings_path.read_text())
    assert loaded["netdaq"]["host"] == "192.0.2.10"
    assert loaded["theme"] == "light"
    assert window._netdaq_preferences().host == "192.0.2.10"


def test_load_vna_cannot_enable_unverified_netdaq_trigger(window):
    window.controller.backend = NetDaqBackend(NetDaqSettings(host="127.0.0.1"))
    window.session.acquisition.trigger.enabled = True
    window.session.acquisition.trigger.mode = "Every Frame"
    window.session.acquisition.excitation.enabled = True
    window._load_session_to_widgets()
    config = window._read_session_from_widgets()
    assert not config.acquisition.trigger.enabled
    assert not config.acquisition.excitation.enabled
    assert not window.start_button.isEnabled()


def test_probe_dialog_accepts_unknown_profile_only_for_read_only_access(app):
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings(host="192.0.2.10"))
    dialog.accept()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert not NetDaqBackend(dialog.settings()).ready
    dialog.deleteLater()


def test_stop_failure_is_not_silently_reported_as_stopped(window):
    from python_vna.ui.main_window import AcquisitionWorker
    from python_vna.daq.base import DaqBackendError
    backend = NetDaqBackend(NetDaqSettings(host="192.0.2.10"))
    window.controller.backend = backend
    worker = AcquisitionWorker(window.controller, None)
    errors = []
    worker.error.connect(errors.append)
    with mock.patch.object(window.controller, "configure"), mock.patch.object(
        window.controller, "start", side_effect=ValueError("synthetic start failure")
    ), mock.patch.object(window.controller, "stop", side_effect=DaqBackendError("stop not acknowledged")), mock.patch.object(
        window.controller, "close"
    ):
        worker.run()
    assert any("stop not acknowledged" in error for error in errors)
    backend.stop_uncertain = True
    window._apply_netdaq_limits()
    assert not window.start_button.isEnabled()
    assert "停止未确认" in window.start_button.toolTip()


def test_reported_iepe_mode_is_read_only_not_a_coupling_switch(window):
    backend = NetDaqBackend(NetDaqSettings(host="192.0.2.10"))
    # Arbitrary test type; not a claim about the real manufacturer's IEPE enum.
    entry = {"wire_number": 1, "channel_type": 3, "input_mode": "iepe", "iepe_current_ma": 2.0,
             "options": [{"coupling": "ac", "full_scale_v": 10, "configuration_hex": "aa00"}]}
    backend.profile = NetDaqProfile("synthetic", b"!D!" + bytes(30), "raw24", bytes(13),
                                   0, 64000, (entry,), "test", "test-only")
    window.controller.backend = backend
    window.channel_list.setCurrentRow(0)
    window._load_channel_editor_from_row(0)
    window._apply_netdaq_limits()
    assert window.channel_iepe_checkbox.isChecked()
    assert not window.channel_iepe_checkbox.isEnabled()
    assert window.channel_iepe_current_edit.value() == 2
    assert not window.channel_iepe_current_edit.isEnabled()
    assert not window.channel_coupling_combo.isEnabled()
    assert not window.channel_full_scale_combo.isEnabled()
    assert window._read_session_from_widgets().ai_channels[0].iepe_enabled
    entry["channel_type"] = 6
    window._load_channel_editor_from_row(0)
    window.channel_coupling_combo.setCurrentText("dc")
    assert window._read_session_from_widgets().ai_channels[0].iepe_enabled


def test_probe_runs_off_ui_thread_and_cancels_without_destroying_thread(app):
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings(host="192.0.2.10"))
    entered = []
    def probe(self, channels=(), *, stop_event=None):
        entered.append(QtCore.QThread.currentThread() != app.thread())
        stop_event.wait(1)
        return {"cancelled": stop_event.is_set()}
    with mock.patch.object(NetDaqBackend, "probe", probe):
        dialog._probe()
        deadline = time.monotonic() + 3
        while not entered and time.monotonic() < deadline:
            QTest.qWait(5)
        dialog.reject()
        while dialog._thread is not None and time.monotonic() < deadline:
            QTest.qWait(5)
        assert entered == [True]
        assert dialog._thread is None
        assert dialog.result() == QtWidgets.QDialog.Rejected
    dialog.deleteLater()


def test_usb_dialog_needs_no_ip_and_remembers_explicit_transport(app):
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings(transport="usb", usb_index=1))
    assert not dialog.host_edit.isEnabled()
    assert not dialog.legacy_port_edit.isEnabled()
    assert dialog.usb_index_edit.isEnabled()
    assert dialog.settings().transport == "usb"
    assert dialog.settings().usb_index == 1
    dialog.accept()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert not NetDaqBackend(dialog.settings()).ready
    dialog.deleteLater()


def test_usb_probe_uses_same_cancellable_worker_without_an_ip(app):
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings(transport="usb"))
    observed = []
    def probe(self, channels=(), *, stop_event=None):
        observed.append((self.settings.transport, tuple(channels)))
        return {"transport": "usb", "acquisition_started": False}
    dialog.probe_channels_edit.setText("0,1,2,3")
    with mock.patch.object(NetDaqBackend, "probe", probe):
        dialog._probe()
        deadline = time.monotonic() + 3
        while dialog._thread is not None and time.monotonic() < deadline:
            QTest.qWait(5)
    assert observed == [("usb", (0, 1, 2, 3))]
    assert '"transport": "usb"' in dialog.report.toPlainText()
    dialog.reject()
    dialog.deleteLater()


def test_usb_fixed_ranges_and_iepe_are_not_editable(window):
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    entry = {"wire_number": 0, "channel_type": 0, "input_mode": "voltage_single_ended_dc",
             "options": [{"coupling": "dc", "full_scale_v": 10, "configuration_hex": "06"}]}
    backend.profile = NetDaqProfile("synthetic", b"!D!" + bytes(30), "raw32", bytes(13),
                                   0, 1000, (entry,), "test", "test-only", "usb")
    window.controller.backend = backend
    window._apply_netdaq_limits()
    window.channel_list.setCurrentRow(0)
    window._load_channel_editor_from_row(0)
    assert not window.channel_coupling_combo.isEnabled()
    assert not window.channel_full_scale_combo.isEnabled()
    assert not window.channel_iepe_checkbox.isEnabled()
    assert not window.channel_iepe_checkbox.isChecked()


@pytest.mark.parametrize("supply", ["device_fixed", "external"])
@pytest.mark.parametrize("current", [None, 2.0])
def test_usb_iepe_unknown_current_and_supply_survive_editor_changes(window, supply, current):
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    # Synthetic profile facts; vendor type 0 alone does not establish IEPE.
    entries = tuple({
        "wire_number": i, "channel_type": 0,
        "input_mode": "iepe" if i else "voltage_single_ended_dc",
        "iepe_supply": supply, "iepe_current_ma": current,
        "options": [{"coupling": "ac" if i else "dc", "full_scale_v": 10,
                     "configuration_hex": "01"}],
    } for i in range(4))
    backend.profile = NetDaqProfile("synthetic", b"!D!" + bytes(30), "raw32", bytes(13),
                                   0, 1000, entries, "test", "test-only", "usb")
    window.controller.backend = backend
    window.channel_list.setCurrentRow(1)
    window._load_channel_editor_from_row(1)
    window._apply_netdaq_limits()
    assert window.channel_iepe_checkbox.isChecked()
    assert not window.channel_iepe_checkbox.isEnabled()
    assert not window.channel_iepe_current_edit.isEnabled()
    assert window.channel_iepe_current_edit.value() == (current or 0)
    if current is None:
        assert window.channel_iepe_current_edit.text() == "未确认"
    expected = "外部适调器供电" if supply == "external" else "设备固定供电"
    assert expected in window.channel_iepe_checkbox.toolTip()
    window.channel_sensitivity_edit.setValue(20)
    config = window._read_session_from_widgets()
    assert config.ai_channels[1].iepe_current_ma == (current or 0)
    assert config.ai_channels[1].iepe_enabled
    assert config.ai_channels[1].sensitivity == 20
    assert config.ai_channels[1].coupling == "ac"
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.channel_iepe_current_edit.isEnabled()
    assert window.channel_iepe_current_edit.specialValueText() == ""
    assert not window.channel_iepe_checkbox.toolTip()


def test_iepe_sensitivity_display_uses_reciprocal_mv_per_unit_once(window):
    from python_vna.daq.base import BackendFrame
    from python_vna.signal_pipeline import FrameProcessor

    session = window.session
    channel = session.ai_channels[0]
    channel.iepe_enabled = True
    channel.iepe_current_ma = 0
    channel.sensitivity = 1000 / 51.567
    channel.engineering_unit = "m/s^2"
    channel.per_eu_mode = "/Volt"
    session.acquisition.frame_size = 64
    waveform = np.sin(2 * np.pi * np.arange(64) / 8)
    frame = BackendFrame(1000, ["ai0"], (waveform * .051567)[None, :],
                         np.arange(64) / 1000, 0, {})
    measurement = FrameProcessor(session.acquisition).process(frame)
    window.controller.state.measurement = measurement
    window.top_display_combo.setCurrentText("y(t)")
    window.top_value_mode_combo.setCurrentText("real")
    window._plot_measurement(measurement)
    _, y = window._last_plot_cache["top"]["ai0"]
    np.testing.assert_allclose(y, waveform, atol=1e-12)


def test_dasp_compatibility_unknown_iepe_and_fixed_ranges_are_visible(window):
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    entries = tuple({
        "wire_number": i, "channel_type": 0, "input_mode": "device_fixed_unknown",
        "options": [{"coupling": "dc", "full_scale_v": 10, "configuration_hex": "01"}],
    } for i in range(4))
    backend.profile = NetDaqProfile(
        "synthetic DASP declaration", b"!D!" + bytes(30), "raw32", bytes(13),
        0, 64000, entries, "test", "test-only", "usb", "", "dasp_parameters", {})
    window.controller.backend = backend
    window._apply_netdaq_limits()
    window.channel_list.setCurrentRow(1)
    window._load_channel_editor_from_row(1)
    assert window.start_button.isEnabled()
    assert "未确认" in window.channel_iepe_checkbox.text()
    assert "未确认" in window.channel_iepe_current_edit.text()
    assert not window.channel_iepe_checkbox.isEnabled()
    assert not window.channel_full_scale_combo.isEnabled()
    assert not window.channel_coupling_combo.isEnabled()
    assert "不设置硬件耦合" in window.channel_coupling_combo.toolTip()
    assert "DASP 参数兼容" in window.avg_button.toolTip()
    config = window._read_session_from_widgets()
    assert not config.ai_channels[1].iepe_enabled
    assert config.ai_channels[1].iepe_current_ma == 0
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.channel_coupling_combo.isEnabled()
    assert "DASP 参数兼容" not in window.channel_coupling_combo.toolTip()


def test_dasp_connection_generation_queries_then_saves_without_start(app, tmp_path, monkeypatch):
    dialog = DeviceConnectionDialog("netdaq", NetDaqSettings(transport="usb"))
    source = tmp_path / "Channel.ini"
    source.write_text("[Channel 0]\nSensitivity=1\nUnit=mV\n", encoding="utf-8")
    report = dict(transport="usb", usb={"product": "USB2404V10"},
                  descriptor_hex="21442100011804040001f400ffffd8f000002710ff800000007fffff000081160d",
                  firmware_hex="2156070200800d", channels={
                      str(i): dict(type=0, config_bytes=1, current_configuration_hex="01")
                      for i in range(4)})
    observed = []

    def probe(_self, channels, **kwargs):
        observed.append(tuple(channels))
        return report

    monkeypatch.setattr(NetDaqBackend, "probe", probe)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a: (str(source), ""))
    target = tmp_path / "compatible"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
    dialog._create_dasp_profile()
    deadline = time.monotonic() + 3
    while dialog._thread is not None and time.monotonic() < deadline:
        QTest.qWait(5)
    assert observed == [(0, 1, 2, 3)]
    result = NetDaqProfile.load(target.with_suffix(".json"))
    assert result.qualification == "dasp_parameters"
    assert "dasp_parameters" in dialog.report.toPlainText()
    assert dialog.profile_edit.text() == str(target.with_suffix(".json"))
    dialog.reject()
    dialog.deleteLater()


def test_dasp_defaults_apply_at_startup_but_do_not_undo_user_edits(app, tmp_path, monkeypatch):
    from python_vna.daq.dasp_import import DaspChannel

    monkeypatch.setenv("PYTHON_VNA_UI_SETTINGS", str(tmp_path / "ui.json"))
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    entries = tuple({
        "wire_number": i, "channel_type": 0, "input_mode": "device_fixed_unknown",
        "options": [{"coupling": "dc", "full_scale_v": 10, "configuration_hex": "01"}],
    } for i in range(4))
    sensor = DaspChannel(1, "DASP Y", True, "m/s^2", 20.0, 50.0, ())
    backend.profile = NetDaqProfile(
        "synthetic DASP declaration", b"!D!" + bytes(30), "raw32", bytes(13),
        0, 64000, entries, "test", "test-only", "usb", "", "dasp_parameters",
        {"sensor_defaults": [asdict(sensor)]})
    session = default_session_config()
    window = MainWindow(VnaController(backend, session), session)
    window._device_poll_timer.stop()
    try:
        window.channel_list.setCurrentRow(1)
        window._load_channel_editor_from_row(1)
        assert window.channel_sensitivity_edit.value() == 20
        assert window.channel_unit_edit.text() == "m/s^2"
        window.channel_sensitivity_edit.setValue(30)
        assert window._read_session_from_widgets().ai_channels[1].sensitivity == 30
        window._apply_netdaq_limits()
        assert window._read_session_from_widgets().ai_channels[1].sensitivity == 30
    finally:
        window._close_confirmed = True
        window.close()
        app.processEvents()
