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


@pytest.mark.parametrize("average", [False, True])
def test_start_during_device_refresh_resumes_exactly_once(window, average):
    pending_thread = mock.Mock()
    pending_thread.wait.return_value = True
    window._device_refresh_thread = pending_thread
    with mock.patch.object(window.controller, "configure") as configure:
        window._start_run(average_run=average)
    configure.assert_not_called()
    assert window._pending_acquisition_request == ("avg" if average else "inst")
    assert window.stop_button.isEnabled()
    assert not window.start_button.isEnabled() and not window.avg_button.isEnabled()
    with mock.patch.object(window, "_start_run") as start:
        window._device_refresh_finished()
        window._resume_pending_acquisition()
    start.assert_called_once_with(average_run=average)
    assert window._pending_acquisition_request is None


def test_pending_start_waits_for_both_default_settings_and_device_query(window):
    window._startup_session_thread = mock.Mock()
    window._device_refresh_thread = mock.Mock()
    window._device_refresh_thread.wait.return_value = True
    window._start_average_acquisition()
    with mock.patch.object(window, "_start_run") as start:
        window._device_refresh_finished()
        start.assert_not_called()
        window._startup_session_finished()
    start.assert_called_once_with(average_run=True)


@pytest.mark.parametrize("cancel", ["stop", "close"])
def test_pending_start_can_be_cancelled_and_does_not_resume(window, cancel):
    window._device_refresh_thread = mock.Mock()
    window._device_refresh_thread.wait.return_value = True
    window._start_average_acquisition()
    if cancel == "stop":
        window._stop_acquisition()
        assert window._pending_acquisition_request is None
    else:
        window._close_confirmed = True
    with mock.patch.object(window, "_start_run") as start:
        window._device_refresh_finished()
    start.assert_not_called()


def test_record_button_waits_for_query_before_opening_folder_dialog(window):
    window._device_refresh_thread = mock.Mock()
    window._device_refresh_thread.wait.return_value = True
    with mock.patch("python_vna.ui.main_window.get_existing_directory") as folder:
        window._start_continuous_recording()
    folder.assert_not_called()
    assert window._pending_acquisition_request == "record"
    with mock.patch.object(window, "_start_continuous_recording") as record:
        window._device_refresh_finished()
    record.assert_called_once_with()


def test_pending_start_cancels_when_hardware_discovery_finds_no_device(window):
    window.controller.backend = build_backend("ni")
    window._auto_hardware_presence = False
    window.device_combo.clear()
    window._device_refresh_thread = mock.Mock()
    window._device_refresh_thread.wait.return_value = True
    window._start_average_acquisition()
    with mock.patch.object(window, "_start_run") as start:
        window._device_refresh_finished()
    start.assert_not_called()
    assert window._pending_acquisition_request is None
    assert not window.stop_button.isEnabled()
    assert "未开始" in window.statusBar().currentMessage()


@pytest.mark.parametrize("finish", ["averages", "stop", "transport_error"])
def test_averaging_waits_through_trigger_intervals_without_restarting_or_counting(
        app, monkeypatch, finish):
    from python_vna.daq.base import BackendFrame, DaqBackendError
    from python_vna.daq.software_trigger import SoftwareTriggerTimeout
    from python_vna.ui.main_window import AcquisitionWorker

    session = default_session_config()
    session.acquisition.frame_size = 16
    trigger = session.acquisition.trigger
    trigger.enabled, trigger.source, trigger.level = True, "ai1", 1.8
    backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    controller = VnaController(backend, session)
    lifecycle = {}
    for method in ("configure", "start", "stop", "close"):
        lifecycle[method] = mock.Mock()
        monkeypatch.setattr(backend, method, lifecycle[method])
    worker = AcquisitionWorker(controller, None, average_run=True, target_average_count=2,
                               display_interval_seconds=0)
    calls = []

    def read():
        index = len(calls)
        calls.append(index)
        if index in {0, 2}:
            raise SoftwareTriggerTimeout("ai1", 1.8, 5)
        if finish == "stop":
            worker.request_stop()
            raise SoftwareTriggerTimeout("ai1", 1.8, 5)
        if finish == "transport_error":
            raise DaqBackendError("USB transport timed out")
        return BackendFrame(2560, [f"ai{i}" for i in range(4)], np.ones((4, 16)) * .01,
                            np.arange(16) / 2560, index)

    monkeypatch.setattr(backend, "read_frame", read)
    measurements, errors, statuses = [], [], []
    worker.measurement_ready.connect(measurements.append)
    worker.error.connect(errors.append)
    worker.status_changed.connect(statuses.append)
    worker.run()
    assert any("Ch2" in text and "1.8 V" in text for text in statuses)
    assert all(action.call_count == 1 for action in lifecycle.values())
    if finish == "averages":
        assert [m.metadata["average_count"] for m in measurements] == [1, 2]
        assert len(calls) == 4 and not errors
    else:
        assert not measurements
        assert bool(errors) == (finish == "transport_error")
        if errors:
            assert "transport" in errors[0]


def test_trigger_wait_status_preserves_connection_and_average_count(window):
    window.controller.backend = _selectable_usb_backend()
    window._set_device_connected(True)
    window._update_average_progress(1, 3)
    message = "等待触发：Ch2，|电压| ≥ 1.8 V；平均次数不增加，可点停止取消"
    window._handle_acquisition_status(message)
    assert window.device_status_light.property("connected")
    assert window._average_progress_count == 1
    assert "等待触发" in window.average_progress.format()
    assert window.statusBar().currentMessage() == message


def _ni_device():
    from python_vna.daq.base import BackendDevice

    return BackendDevice("Dev1", "NI USB-4431", [f"Dev1/ai{i}" for i in range(4)], ["Dev1/ao0"])


def _discovery_snapshot(*, ni=False, usb=False):
    return {"ni_devices": [_ni_device()] if ni else [],
            "usb_devices": [{"index": 0}] if usb else [], "errors": {}}


def test_auto_detection_defaults_on_and_can_be_remembered(window, monkeypatch):
    assert window.auto_device_action.isChecked()
    monkeypatch.setattr(window, "refresh_devices_async", lambda: None)
    window._write_connection_preferences(theme="light", netdaq={"host": "example.invalid"})
    window.auto_device_action.setChecked(False)
    settings = window._connection_preferences()
    assert settings["auto_device_detection"] is False
    assert settings["theme"] == "light" and settings["netdaq"]["host"] == "example.invalid"
    window.controller.backend = build_backend("ni")
    assert not window._automatic_hardware_detection()
    window.auto_device_action.setChecked(True)
    assert window._automatic_hardware_detection()


def test_auto_detection_respects_manual_simulation_tcp_and_replay(window):
    assert not window._automatic_hardware_detection()
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True, usb=True))
    assert window.controller.backend.__class__.__name__ == "SimulatedDaqBackend"
    window.controller.backend = NetDaqBackend(NetDaqSettings(host="192.0.2.1"))
    assert not window._automatic_hardware_detection()
    window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window.controller.backend.settings.transport == "tcp"
    from python_vna.daq.replay import ReplayBackend
    window.controller.backend = ReplayBackend("not-opened.vna")
    assert not window._automatic_hardware_detection()


def test_auto_detection_switches_to_unconfigured_usb_without_probing(window, monkeypatch):
    window.controller.backend = build_backend("ni")
    with mock.patch.object(NetDaqBackend, "probe", side_effect=AssertionError("No vendor commands")):
        window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window._hardware_backend_key() == "usb"
    assert window.backend_combo.currentText() == "netdaq"
    assert window.device_combo.currentData() == window.controller.backend.device_name
    assert window.device_status_light.property("connected")
    assert not window.start_button.isEnabled()
    assert not window.avg_button.isEnabled()
    assert "选择已保存" in window.statusBar().currentMessage()
    assert window._connection_preferences()["usbdaq"]["transport"] == "usb"


def test_auto_detection_switches_ready_usb_and_restores_ni_configuration(window, monkeypatch):
    from python_vna import app as app_module

    ni, usb = build_backend("ni"), _selectable_usb_backend()
    window.controller.backend = ni
    window.session.ai_channels[0].label = "NI sensor"
    window.session.ai_channels[0].sensitivity = 7
    window.session.ai_channels[0].iepe_enabled = True
    window.session.ai_channels[0].iepe_current_ma = 2.1
    window.session.acquisition.frame_size = 8192
    window._load_session_to_widgets()
    original_build = app_module.build_backend
    monkeypatch.setattr(app_module, "build_backend",
                        lambda name, **kwargs: usb if name == "netdaq" else original_build(name, **kwargs))
    window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window._hardware_backend_key() == "usb"
    assert window.start_button.isEnabled()
    assert not window.exc_enable.isEnabled()
    assert window.channel_table.item(0, 5).text() == "0"
    window.session.ai_channels[0].label = "USB sensor"
    window.session.acquisition.frame_size = 2048
    window._load_session_to_widgets()
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True))
    assert window._hardware_backend_key() == "ni"
    assert window.exc_enable.isEnabled()
    assert window.session.ai_channels[0].label == "NI sensor"
    assert window.session.ai_channels[0].sensitivity == 7
    assert window.session.ai_channels[0].iepe_current_ma == 2.1
    assert window.session.acquisition.frame_size == 8192
    window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window.session.ai_channels[0].label == "USB sensor"
    assert window.session.ai_channels[0].iepe_current_ma == 0
    assert window.session.acquisition.frame_size == 2048


def test_auto_detection_keeps_last_choice_and_does_not_reset_edits(window):
    window.controller.backend = build_backend("ni")
    window._write_connection_preferences(last_hardware_backend="ni")
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True, usb=True))
    with mock.patch.object(window, "_apply_device_list") as apply:
        window.channel_table.item(0, 10).setText("Edited sensor")
        window._apply_hardware_snapshot(_discovery_snapshot(ni=True, usb=True))
    apply.assert_not_called()
    assert window.channel_table.item(0, 10).text() == "Edited sensor"
    assert window._hardware_backend_key() == "ni"


def test_auto_detection_disconnection_and_reconnection_updates_indicator(window):
    window.controller.backend = build_backend("ni")
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True))
    assert window.device_status_light.property("connected")
    window._apply_hardware_snapshot(_discovery_snapshot())
    window._apply_netdaq_limits()
    assert not window.device_status_light.property("connected")
    assert not window.start_button.isEnabled()
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True))
    window._apply_netdaq_limits()
    assert window.device_status_light.property("connected")
    assert window.start_button.isEnabled()


@pytest.mark.parametrize("state", ["_acquisition_thread", "_recording_thread",
                                  "_startup_session_thread", "_close_confirmed"])
def test_auto_detection_ignores_results_while_busy_or_closing(window, state):
    window.controller.backend = build_backend("ni")
    old = getattr(window, state)
    setattr(window, state, True)
    try:
        window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
        assert window._hardware_backend_key() == "ni"
        with mock.patch.object(window, "refresh_devices_async") as refresh:
            window._poll_device_connection()
        refresh.assert_not_called()
    finally:
        setattr(window, state, old)


def test_auto_detection_does_not_bypass_uncertain_stop(window):
    window.controller.backend = _selectable_usb_backend()
    window.controller.backend.stop_uncertain = True
    assert not window._automatic_hardware_detection()
    window._apply_hardware_snapshot(_discovery_snapshot(ni=True))
    assert window._hardware_backend_key() == "usb"
    with mock.patch.object(window, "refresh_devices_async") as refresh:
        window._poll_device_connection()
    refresh.assert_not_called()


def test_auto_detection_missing_profile_uses_readonly_usb_without_erasing_saved_path(window):
    window.controller.backend = build_backend("ni")
    settings = NetDaqSettings(transport="usb", profile_path="does-not-exist.json")
    window._write_connection_preferences(usbdaq=asdict(settings))
    window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window._hardware_backend_key() == "usb"
    assert not window.start_button.isEnabled()
    assert window.controller.backend.profile is None
    assert "原配置不可用" in window.statusBar().currentMessage()
    assert window._connection_preferences()["usbdaq"]["profile_path"] == settings.profile_path
    window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window._connection_preferences()["usbdaq"]["profile_path"] == settings.profile_path
    assert window._netdaq_preferences().profile_path == settings.profile_path


def test_auto_detection_skips_open_configuration_dialogs(window, monkeypatch):
    window.controller.backend = build_backend("ni")
    monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", lambda: object())
    with mock.patch.object(window, "refresh_devices_async") as refresh:
        window._poll_device_connection()
        window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    refresh.assert_not_called()
    assert window._hardware_backend_key() == "ni"


def test_auto_detection_cannot_switch_when_previous_backend_close_fails(window):
    window.controller.backend = build_backend("ni")
    with mock.patch.object(window.controller, "close", side_effect=RuntimeError("pending native read")):
        window._apply_hardware_snapshot(_discovery_snapshot(usb=True))
    assert window._hardware_backend_key() == "ni"
    assert "未切换连接" in window.statusBar().currentMessage()


def test_auto_detection_preserves_selected_ni_when_device_list_expands(window):
    from python_vna.daq.base import BackendDevice

    window.controller.backend = build_backend("ni")
    device = _ni_device()
    second = BackendDevice("Dev2", "USB-4431", [f"Dev2/ai{i}" for i in range(4)])
    window._apply_device_list([second])
    snapshot = _discovery_snapshot(ni=True)
    snapshot["ni_devices"].append(second)
    window._apply_hardware_snapshot(snapshot)
    assert window.device_combo.currentData() == "Dev2"


def test_auto_detection_background_worker_applies_after_thread_finishes(window, app, monkeypatch):
    import threading
    from python_vna.ui import main_window

    owner = threading.get_ident()
    calls = []
    release = threading.Event()
    def scan():
        calls.append(threading.get_ident())
        release.wait(2)
        return _discovery_snapshot(usb=True)
    monkeypatch.setattr(main_window, "discover_hardware", scan)
    window.controller.backend = build_backend("ni")
    window.refresh_devices_async()
    assert window._device_refresh_thread is not None
    # A second poll cannot start another worker; acquisition cannot race enumeration.
    window.refresh_devices_async()
    window._start_run(False)
    assert window._acquisition_thread is None
    release.set()
    deadline = time.monotonic() + 4
    while window._device_refresh_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert window._device_refresh_thread is None
    assert len(calls) == 1 and calls[0] != owner
    assert window._hardware_backend_key() == "usb"


def test_auto_detection_retains_wrappers_until_native_thread_has_joined(window, monkeypatch):
    thread, worker = mock.Mock(), object()
    thread.wait.side_effect = [False, True]
    window._device_refresh_thread, window._device_refresh_worker = thread, worker
    scheduled = []
    monkeypatch.setattr(QtCore.QTimer, "singleShot", lambda delay, callback: scheduled.append(callback))
    window._device_refresh_finished()
    assert window._device_refresh_thread is thread
    assert window._device_refresh_worker is worker
    thread.deleteLater.assert_not_called()
    assert len(scheduled) == 1
    scheduled[0]()
    assert window._device_refresh_thread is None and window._device_refresh_worker is None
    thread.deleteLater.assert_called_once_with()

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


def _selectable_usb_backend():
    from python_vna.daq.usbdaq import COUPLING_DESCRIPTOR, COUPLING_FIRMWARE

    backend = _usb_channel_setup_backend()
    for entry in backend.profile.channels:
        entry.update(input_mode="iepe", mode_control="usb2404_coupling",
                     iepe_supply="device_fixed", iepe_current_ma=None)
        entry["options"] = [
            dict(coupling=mode, full_scale_v=10, configuration_hex=cfg,
                 volts_per_count=10 / 8388608, zero_count=0)
            for mode, cfg in (("bias", "01"), ("dc", "00"))
        ]
    backend.profile = replace(
        backend.profile, descriptor=COUPLING_DESCRIPTOR, firmware_hex=COUPLING_FIRMWARE.hex(),
        qualification="dasp_parameters", vendor_parameters={"sensor_defaults": []})
    return backend


def _wait_for_defaults(window, app):
    deadline = time.monotonic() + 3
    while window._startup_session_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    assert window._startup_session_thread is None
    assert window._startup_session_worker is None


def test_default_paths_and_device_titles_follow_ni_and_usb(window):
    from python_vna.condition_notes import condition_number_from_path
    from python_vna.resources import resource_path

    window.controller.backend = build_backend("ni")
    window._apply_device_list([_ni_device()])
    window._current_source_path = None
    window._update_window_title()
    assert window._default_vna_path() == resource_path("dsa/vna/default.vna")
    assert window.windowTitle() == "VNA - NI USB-4431 (Dev1)"
    backend = _selectable_usb_backend()
    backend.profile = replace(backend.profile, model="USB2404")
    window.controller.backend = backend
    window.device_combo.clear()
    window._update_window_title()
    assert window._default_vna_path() == resource_path("dsa/vna/usb2404/default.vna")
    assert condition_number_from_path(window._default_vna_path()) is None
    assert window.windowTitle() == "VNA - USB2404"
    assert "USB-4431" not in window.windowTitle()


def test_saving_and_loading_usb_defaults_preserves_exact_config_and_ni_file(window, app, tmp_path, monkeypatch):
    from python_vna.storage import load_saved_session_json

    ni_path, usb_path = tmp_path / "default.vna", tmp_path / "default_usb2404.vna"
    monkeypatch.setattr(window, "_default_vna_path",
                        lambda: usb_path if window._hardware_backend_key() == "usb" else ni_path)
    window.controller.backend = build_backend("ni")
    window.session.ai_channels[0].label = "NI sensor"
    window.session.ai_channels[0].sensitivity = 7
    window.session.acquisition.frame_size = 8192
    window._load_session_to_widgets()
    assert window._save_to_default_vna() == ni_path
    ni_bytes = ni_path.read_bytes()

    backend = _selectable_usb_backend()
    with mock.patch("python_vna.app.build_backend", return_value=backend):
        assert window._backend_changed("netdaq", backend.settings, refresh=False)
    assert window._startup_session_thread is None
    assert window.session.acquisition.frame_size == 4096
    assert window.session.ai_channels[0].label != "NI sensor"
    window.session.acquisition.frame_size = 2048
    window.session.acquisition.sample_rate = 5000
    window.session.ai_channels[0].label = "USB sensor"
    window.session.ai_channels[0].sensitivity = 4
    window.session.ai_channels[0].coupling = "dc"
    window.session.ai_channels[0].iepe_enabled = False
    window.session.ai_channels[0].dc_removal_enabled = True
    window.session.ai_channels[0].dc_removal_cutoff_hz = .73
    window._load_session_to_widgets()
    expected_rate = window.sample_rate_edit.value()
    assert window._save_to_default_vna() == usb_path
    assert ni_path.read_bytes() == ni_bytes
    companion = load_saved_session_json(usb_path.with_suffix(".json")).config
    assert companion.acquisition.sample_rate == expected_rate
    assert companion.ai_channels[0].dc_removal_enabled
    assert companion.ai_channels[0].dc_removal_cutoff_hz == .73

    window.session = default_session_config()
    window.controller.state.session = window.session
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    window.load_startup_session_async()
    _wait_for_defaults(window, app)
    assert window.session.acquisition.frame_size == 2048
    assert window.session.acquisition.sample_rate == expected_rate
    channel = window.session.ai_channels[0]
    assert channel.label == "USB sensor" and channel.sensitivity == 4
    assert channel.coupling == "dc" and not channel.iepe_enabled
    assert channel.dc_removal_enabled and channel.dc_removal_cutoff_hz == .73
    assert window._current_source_path == usb_path


def test_first_device_switch_loads_own_defaults_but_cached_edits_win(window, app, tmp_path, monkeypatch):
    from python_vna.models import SavedSession
    from python_vna.storage import save_legacy_vna, save_session_json

    ni, usb = build_backend("ni"), _selectable_usb_backend()
    usb_path = tmp_path / "default_usb2404.vna"
    config = default_session_config()
    config.acquisition.frame_size = 2048
    config.ai_channels[0].label = "Saved USB"
    config.ai_channels[0].sensitivity = 4
    save_legacy_vna(SavedSession(config=config), usb_path)
    save_session_json(SavedSession(config=config), usb_path.with_suffix(".json"))
    monkeypatch.setattr(window, "_default_vna_path",
                        lambda: usb_path if window._hardware_backend_key() == "usb" else tmp_path / "default.vna")
    monkeypatch.setattr("python_vna.app.build_backend",
                        lambda name, **kwargs: usb if name == "netdaq" else ni)
    window.controller.backend = ni
    window.session.acquisition.frame_size = 8192
    window.session.notes = "当前测试工况"
    window._load_session_to_widgets()
    source = window._current_source_path = tmp_path / "003.vna"
    assert window._backend_changed("netdaq", usb.settings, refresh=False)
    assert window._startup_session_thread is not None
    _wait_for_defaults(window, app)
    assert window.session.acquisition.frame_size == 2048
    assert window.session.ai_channels[0].label == "Saved USB"
    assert window.session.notes == "当前测试工况" and window._current_source_path == source
    window.session.ai_channels[0].sensitivity = 9
    window._load_session_to_widgets()
    assert window._backend_changed("ni", refresh=False)
    assert window.session.acquisition.frame_size == 8192
    assert window._backend_changed("netdaq", usb.settings, refresh=False)
    assert window._startup_session_thread is None
    assert window.session.ai_channels[0].sensitivity == 9


def test_stale_device_default_result_cannot_overwrite_another_device(window):
    from python_vna.models import SavedSession

    window.controller.backend = _selectable_usb_backend()
    session = window.session
    window._startup_device_key = "ni"
    window._startup_preserve_context = True
    window._apply_device_default_result(SavedSession(config=default_session_config()))
    assert window.session is session


def test_usb_configuration_controls_match_supported_hardware_and_host_features(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    assert not window.setup_action.isEnabled()
    assert not window.excitation_setup_page.isEnabled()
    assert not window.exc_amplitude_edit.isEnabled()
    assert not window.ao_channel_edit.isEnabled()
    assert not window.channel_offset_edit.isEnabled()
    assert not window.channel_physical_edit.isEnabled()
    assert not window.channel_reference_checkbox.isEnabled()
    assert not window.channel_auto_range_button.isEnabled()
    assert window.channel_sensitivity_edit.isEnabled()
    assert window.channel_label_edit.isEnabled()
    assert window.window_combo.isEnabled()
    assert "不是可编程硬件低通" in window.bandwidth_combo.toolTip()
    assert "尚未确认" in window.aa_filters_button.toolTip()
    assert not window._read_session_from_widgets().acquisition.anti_alias_filters_enabled
    dialog = MCSetupDialog(window)
    try:
        for row in range(4):
            assert not dialog.table.item(row, 3).flags() & QtCore.Qt.ItemIsEditable
            assert not dialog.table.item(row, 8).flags() & QtCore.Qt.ItemIsEditable
        dialog.table.item(0, 3).setText("2.5")
        dialog._apply_to_main()
        assert window.session.ai_channels[0].offset == 0
    finally:
        dialog.close()
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.setup_action.isEnabled()
    assert window.exc_amplitude_edit.isEnabled()
    assert window.channel_offset_edit.isEnabled()
    assert window.channel_physical_edit.isEnabled()
    assert window.channel_reference_checkbox.isEnabled()
    assert window.channel_grid.item(0, 4).flags() & QtCore.Qt.ItemIsEditable
    assert window.channel_auto_range_button.isEnabled()
    assert window._read_session_from_widgets().acquisition.anti_alias_filters_enabled


def test_dc_removal_dialog_uses_point_seven_default_and_commits_pending_text(app):
    dialog = DcRemovalDialog(default_session_config())
    try:
        assert dialog.table.cellWidget(1, 3).value() == .7
        dialog.table.item(1, 2).setCheckState(QtCore.Qt.Checked)
        dialog.table.cellWidget(1, 3).lineEdit().setText("0.700")
        dialog.accept()
        assert dialog.channels[1].dc_removal_enabled
        assert dialog.channels[1].dc_removal_cutoff_hz == .7
    finally:
        dialog.close()


def test_usb_frequency_preview_and_frame_length_use_actual_clock_without_io(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_netdaq_limits()
    window.bandwidth_combo.setCurrentText("BW=10.0KHz")
    window.frame_size_edit.setValue(8192)
    assert window.sample_rate_edit.value() == 25600
    assert "Fs=32000 Hz" in window.df_label.text()
    assert "dF=3.906" in window.df_label.text()
    assert "每帧 8192 点" in window.df_label.toolTip()
    session = window._read_session_from_widgets()
    assert session.acquisition.frame_size == 8192
    assert session.acquisition.sample_rate == 25600
    session.acquisition.sample_rate = 32000
    window._load_session_to_widgets()
    assert window.sample_rate_edit.value() == 32000
    assert window.session.acquisition.bandwidth_hz == 10000


def test_profile_rate_limit_disables_unavailable_bandwidth_choices(window):
    backend = _selectable_usb_backend()
    backend.profile = replace(backend.profile, max_sample_rate=2000)
    window.controller.backend = backend
    window._apply_netdaq_limits()
    assert window.sample_rate_edit.maximum() == 2000
    low = window.bandwidth_combo.findText("BW=0.39KHz")
    high = window.bandwidth_combo.findText("BW=10.0KHz")
    assert window.bandwidth_combo.model().item(low).isEnabled()
    assert not window.bandwidth_combo.model().item(high).isEnabled()
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.sample_rate_edit.maximum() == 102400
    assert window.bandwidth_combo.model().item(high).isEnabled()


def test_usb_trigger_disables_ignored_overlap_and_disabled_sources(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    window.overlap_combo.setCurrentIndex(window.overlap_combo.findData(50))
    window.trigger_mode_combo.setCurrentIndex(window.trigger_mode_combo.findData("Every Frame"))
    assert not window.overlap_combo.isEnabled()
    assert window.overlap_combo.currentData() == 0
    assert window.pretrigger_samples_edit.minimum() == -99
    dialog = MCSetupDialog(window)
    try:
        dialog.table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
        dialog._apply_to_main()
    finally:
        dialog.close()
    window._apply_netdaq_limits()
    assert not window.trigger_source_combo.model().item(0).isEnabled()
    assert window.trigger_source_combo.currentText() == "Ch2"
    window.trigger_level_edit.setValue(.02)
    session = window._read_session_from_widgets()
    assert session.acquisition.trigger.enabled
    assert session.acquisition.trigger.source == "ai1"
    assert session.acquisition.trigger.level == .02
    window.trigger_mode_combo.setCurrentIndex(window.trigger_mode_combo.findData("Off (Free Run)"))
    assert window.overlap_combo.isEnabled()
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert window.trigger_source_combo.model().item(0).isEnabled()


def test_usb_reference_is_explicit_and_independent_of_channel_editor_and_trigger(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    dialog = MCSetupDialog(window)
    try:
        assert dialog.reference_combo.currentData() == "ai0"
        dialog.reference_combo.setCurrentIndex(dialog.reference_combo.findData("ai1"))
        dialog._apply_to_main()
        assert window._current_reference_name() == "ai1"
        assert window.session.acquisition.reference_channel == "ai1"
        assert window.session.acquisition.response_channels == ["ai0", "ai2", "ai3"]
        assert [c.name for c in window.session.ai_channels if c.is_reference] == ["ai1"]
        window.channel_select_combo.setCurrentIndex(3)
        window.trigger_mode_combo.setCurrentIndex(window.trigger_mode_combo.findData("Every Frame"))
        window.trigger_source_combo.setCurrentText("Ch3")
        window._read_session_from_widgets()
        assert window.session.acquisition.reference_channel == "ai1"
        assert window.session.acquisition.trigger.source == "ai2"
        dialog.reload_from_main()
        assert dialog.reference_combo.currentData() == "ai1"
        dialog.table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
        assert dialog.reference_combo.currentData() == "ai0"
        dialog._apply_to_main()
        assert window.session.acquisition.reference_channel == "ai0"
    finally:
        dialog.close()


def test_usb_processing_configuration_cannot_change_during_a_run(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    window._acquisition_thread = object()
    try:
        window._sync_usb_mode_editability()
        for widget in (window.frame_size_edit, window.sample_rate_edit, window.bandwidth_combo,
                       window.trigger_source_combo, window.channel_sensitivity_edit,
                       window.reject_combo, window.average_count_edit):
            assert not widget.isEnabled()
        window.channel_select_combo.setCurrentIndex(1)
        assert not window.channel_sensitivity_edit.isEnabled()
        dialog = MCSetupDialog(window)
        try:
            assert not dialog.table.isEnabled()
            assert not dialog.reference_combo.isEnabled()
        finally:
            dialog.close()
    finally:
        window._acquisition_thread = None
        window._apply_netdaq_limits()
    assert window.frame_size_edit.isEnabled()
    assert window.channel_sensitivity_edit.isEnabled()


def test_usb_mode_editor_and_mc_setup_apply_dc_iepe_without_ni_defaults(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_backend_sensor_defaults()
    window._load_session_to_widgets()
    window.channel_list.setCurrentRow(1)
    assert window.channel_coupling_combo.isEnabled()
    assert window.channel_coupling_combo.currentText() == "IEPE"
    assert window.channel_coupling_combo.currentData() == "bias"
    assert not window.channel_full_scale_combo.isEnabled()
    assert not window.channel_iepe_current_edit.isEnabled()
    assert window.channel_iepe_current_edit.value() == 0
    assert window._channel_coupling_options(1) == ["DC", "IEPE"]
    window.channel_coupling_combo.setCurrentIndex(window.channel_coupling_combo.findData("dc"))
    session = window._read_session_from_widgets()
    assert session.ai_channels[1].coupling == "dc"
    assert not session.ai_channels[1].iepe_enabled
    assert session.ai_channels[1].iepe_current_ma == 0
    window.channel_coupling_combo.setCurrentIndex(window.channel_coupling_combo.findData("bias"))
    assert window._read_session_from_widgets().ai_channels[1].iepe_enabled
    dialog = MCSetupDialog(window)
    try:
        assert dialog.table.item(1, 2).text() == "IEPE"
        assert dialog.table.item(1, 2).flags() & QtCore.Qt.ItemIsEditable
        assert not dialog.table.item(1, 1).flags() & QtCore.Qt.ItemIsEditable
        editor = dialog.table.itemDelegateForColumn(2).createEditor(
            dialog.table, None, dialog.table.model().index(1, 2))
        assert [editor.itemText(i) for i in range(editor.count())] == ["DC", "IEPE"]
        editor.deleteLater()
        dialog.table.item(1, 2).setText("DC")
        dialog._apply_to_main()
        assert window._read_session_from_widgets().ai_channels[1].coupling == "dc"
        dialog.reload_from_main()
        assert dialog.table.item(1, 2).text() == "DC"
        dialog.table.item(1, 2).setText("IEPE")
        dialog._apply_to_main()
        session = window._read_session_from_widgets()
        assert session.ai_channels[1].coupling == "bias"
        assert session.ai_channels[1].iepe_enabled
        assert session.ai_channels[1].iepe_current_ma == 0
    finally:
        dialog.close()
    window.controller.backend = build_backend("simulated")
    window._apply_netdaq_limits()
    assert "DC=00" not in window.channel_coupling_combo.toolTip()
    assert all(window.channel_coupling_combo.model().item(i).isEnabled()
               for i in range(window.channel_coupling_combo.count()))


def test_usb_mode_selection_is_locked_during_acquisition(window):
    window.controller.backend = _selectable_usb_backend()
    window._apply_netdaq_limits()
    window._acquisition_thread = object()
    try:
        window._sync_usb_mode_editability()
        assert not window.channel_coupling_combo.isEnabled()
        assert not window.channel_grid.item(1, 3).flags() & QtCore.Qt.ItemIsEditable
        dialog = MCSetupDialog(window)
        try:
            assert not dialog.table.item(1, 2).flags() & QtCore.Qt.ItemIsEditable
        finally:
            dialog.close()
    finally:
        window._acquisition_thread = None
        window._sync_usb_mode_editability()
    assert window.channel_coupling_combo.isEnabled()
    assert window.channel_grid.item(1, 3).flags() & QtCore.Qt.ItemIsEditable


@pytest.mark.parametrize("iepe", [False, True])
def test_legacy_ac_setting_uses_explicit_iepe_flag_in_selectable_usb(window, iepe):
    window.controller.backend = _selectable_usb_backend()
    window.channel_table.item(1, 6).setText("ac")
    window.channel_table.item(1, 4).setCheckState(QtCore.Qt.Checked if iepe else QtCore.Qt.Unchecked)
    window._sync_netdaq_channel_mode(1)
    channel = window._read_session_from_widgets().ai_channels[1]
    assert channel.coupling == ("bias" if iepe else "dc")
    assert channel.iepe_enabled is iepe
    assert channel.iepe_current_ma == 0


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

def test_bundled_usb_preset_is_an_explicit_offline_choice(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    saved = str(tmp_path / "custom.json")
    dialog = DeviceConnectionDialog(
        "netdaq", NetDaqSettings(transport="usb", usb_index=1, profile_path=saved))
    try:
        assert dialog.profile_edit.text() == saved
        assert dialog.usb_preset_button.isEnabled()
        with mock.patch.object(NetDaqBackend, "probe", side_effect=AssertionError("No hardware query")):
            dialog._use_usb_preset()
        selected = dialog.settings()
        assert selected.usb_index == 1
        assert selected.profile_path.startswith(str(tmp_path / "AppData"))
        profile = NetDaqProfile.load(selected.profile_path)
        assert profile.qualification == "dasp_parameters"
        assert all(profile.has_usb_coupling_control(row) for row in range(4))
        assert "请核对实际传感器" in dialog.report.toPlainText()
        assert not (tmp_path / "custom.json").exists()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_missing_bundled_usb_preset_preserves_selected_profile(app, tmp_path, monkeypatch):
    import python_vna.ui.netdaq_settings as connection_module

    dialog = DeviceConnectionDialog(
        "netdaq", NetDaqSettings(transport="usb", profile_path="custom.json"))
    try:
        monkeypatch.setattr(connection_module, "resource_path", lambda _: tmp_path / "missing.json")
        dialog._use_usb_preset()
        assert dialog.profile_edit.text() == "custom.json"
        assert "随包配置不可用" in dialog.report.toPlainText()
        dialog.transport_combo.setCurrentIndex(dialog.transport_combo.findData("tcp"))
        assert not dialog.usb_preset_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_unconfigured_usb_channel_setup_opens_connection_settings(window):
    window.controller.backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    with mock.patch.object(window, "_open_device_connection") as configure, \
            mock.patch("python_vna.ui.main_window.MCSetupDialog") as setup:
        window._open_mc_setup_dialog()
    configure.assert_called_once()
    setup.assert_not_called()


def test_configured_usb_channel_setup_continues_after_applying_connection(window):
    window.controller.backend = NetDaqBackend(NetDaqSettings(transport="usb"))
    def configure():
        window.controller.backend = _selectable_usb_backend()
    with mock.patch.object(window, "_open_device_connection", side_effect=configure), \
            mock.patch("python_vna.ui.main_window.MCSetupDialog") as setup:
        window._open_mc_setup_dialog()
    setup.assert_called_once_with(window)
    setup.return_value.exec.assert_called_once()


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
