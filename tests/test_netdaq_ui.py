from __future__ import annotations

from dataclasses import asdict
import json
import os
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest

from python_vna.app import build_backend, parse_args
from python_vna.controller import VnaController
from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings
from python_vna.storage import default_session_config
from python_vna.ui.main_window import MainWindow
from python_vna.ui.netdaq_settings import DeviceConnectionDialog


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
