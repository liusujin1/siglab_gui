from __future__ import annotations

import ctypes as ct
from copy import deepcopy
import json
import struct
import threading
import time

import numpy as np
import pytest

from python_vna.app import parse_args
from python_vna.daq.base import DaqBackendError
from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings
from python_vna.daq.netdaq_protocol import NetDaqStopped, sampling_divisor
from python_vna.daq import usbdaq as u
from python_vna.storage import default_session_config, save_legacy_vna, load_legacy_vna


REAL_DESCRIPTOR = bytes.fromhex("21442100011804040001f400ffffd8f000002710ff800000007fffff000081160d")
USB_CONFIGURATION = bytes.fromhex(
    "09022e00010100a0320904000004ff000000"
    "07058202000200070584020800000705860280000007050802800000")


def reply(command, data=None, *, accepted=True):
    if data is None:
        head = b"!" + bytes([command]) + (b"OK" if accepted else b"NO")
    else:
        head = bytes([33, command, len(data) + 5]) + data
    return head + bytes([sum(head) & 255, 13])


class FakeUsb:
    """ctypes-facing USB test double. Never a real hardware qualification."""
    def __init__(self):
        self.dev = u.UsbDevice()
        self.dev.filename = b"TEST DOUBLE ONLY"
        self.dev.descriptor = u.DeviceDescriptor()
        d = self.dev.descriptor
        d.vid, d.pid, d.manufacturer, d.product = u.VID, u.PID, 1, 2
        self.bus = u.UsbBus()
        self.bus.devices = ct.pointer(self.dev)
        self.response = b""
        self.commands, self.native_threads = [], []
        self.release_count = self.close_count = self.cancel_count = self.submit_count = 0
        self.pending = False
        self.cancelled = False
        self.drop_start, self.reject_stop = False, False
        self.pause = self.disconnect = self.corrupt_reply = False
        self.claim_error = 0
        self.descriptor = REAL_DESCRIPTOR
        self.config_lengths = {n: 1 for n in range(4)}
        self.firmware = reply(0x56, bytes((2, 0)))
        self.data = np.arange(16000, dtype="<i4").tobytes()
        self.position = 0
        self.chunk_size = 127
        self.product = b"USB2404V10"
        self.configurations = {n: bytes((n + 1,)) for n in range(4)}

    def note(self):
        self.native_threads.append(threading.get_ident())

    def usb_init(self):
        self.note()

    def usb_find_busses(self):
        return 1

    def usb_find_devices(self):
        return 1

    def usb_get_busses(self):
        return ct.pointer(self.bus)

    def usb_open(self, device):
        self.note()
        return 123

    def usb_claim_interface(self, handle, interface):
        self.note()
        return self.claim_error

    def usb_release_interface(self, *args):
        self.note()
        self.release_count += 1
        return 0

    def usb_close(self, *args):
        self.note()
        self.close_count += 1
        return 0

    def usb_get_string_simple(self, handle, index, buffer, size):
        data = b"USBACQ" if index == 1 else self.product
        ct.memmove(buffer, data, len(data))
        return len(data)

    def usb_get_descriptor(self, handle, kind, index, buffer, size):
        ct.memmove(buffer, USB_CONFIGURATION, len(USB_CONFIGURATION))
        return len(USB_CONFIGURATION)

    def usb_bulk_write(self, handle, endpoint, buffer, size, timeout):
        self.note()
        assert endpoint == 0x08
        raw = ct.string_at(buffer, size)
        length, cmd = raw[2], raw[1]
        assert raw[length - 2] == sum(raw[:length - 2]) & 255
        self.commands.append((cmd, raw[3:length - 2]))
        body = raw[3:length - 2]
        if cmd == 0x44:
            self.response = self.descriptor
        elif cmd == 0x56:
            self.response = self.firmware
        elif cmd == 0x41:
            self.response = reply(cmd, body + bytes((0, self.config_lengths[body[0]])))
        elif cmd == 0x42:
            self.response = reply(cmd, body + self.configurations[body[0]])
        elif cmd in (0x49, 0x53, 0x50):
            self.response = reply(cmd, accepted=not (cmd == 0x50 and self.reject_stop))
            if cmd == 0x53:
                self.position = 0
                if self.drop_start:
                    self.response = b""
        else:
            raise AssertionError("Unsafe command: %r" % cmd)
        return size

    def usb_bulk_read(self, handle, endpoint, buffer, size, timeout):
        self.note()
        assert endpoint == 0x86
        if not self.response:
            return -116
        data = self.response
        if self.corrupt_reply:
            data = data[:-2] + bytes([data[-2] ^ 1, 13])
        ct.memmove(buffer, data, len(data))
        return len(data)

    def usb_bulk_setup_async(self, handle, context, ep):
        self.note()
        assert ep == 0x82
        ct.cast(context, ct.POINTER(ct.c_void_p))[0] = ct.c_void_p(456)
        return 0

    def usb_submit_async(self, context, buffer, size):
        self.note()
        assert not self.pending
        self.pending = True
        self.cancelled = False
        self.buffer, self.buffer_size = buffer, size
        self.submit_count += 1
        return 0

    def usb_reap_async_nocancel(self, context, timeout):
        self.note()
        if self.cancelled:
            return -5
        if self.pause:
            time.sleep(.005)
            return -116
        self.pending = False
        if self.disconnect:
            return -5
        size = min(self.chunk_size, len(self.data) - self.position)
        block = self.data[self.position:self.position + size]
        ct.memmove(self.buffer, block, size)
        self.position += size
        return size

    def usb_cancel_async(self, context):
        self.note()
        self.pending = False
        self.cancelled = True
        self.cancel_count += 1
        return 0

    def usb_free_async(self, context):
        self.note()
        assert not self.pending
        ct.cast(context, ct.POINTER(ct.c_void_p))[0] = ct.c_void_p()
        return 0

    def usb_clear_halt(self, handle, endpoint):
        self.note()
        return 0

    def usb_strerror(self):
        return b"synthetic error"


@pytest.fixture
def usb(monkeypatch):
    lib = FakeUsb()
    monkeypatch.setattr(u, "load_library", lambda: lib)
    monkeypatch.setattr(u, "DRAIN_SECONDS", .03)
    return lib


def profile(tmp_path):
    data = {
        "schema_version": 1, "verified": True, "transport": "usb",
        "model": "TEST DOUBLE ONLY", "evidence": "Synthetic fixture, NOT real calibration",
        "descriptor_hex": REAL_DESCRIPTOR.hex(), "firmware_hex": reply(0x56, bytes((2, 0))).hex(),
        "stream_format": "raw32", "max_sample_rate": 2000,
        "init_ad_payload_hex": (struct.pack(">I", 64) + b"\x01" + struct.pack("<I", 15) + bytes(4)).hex(),
        "divisor_offset": 0,
        "channels": [{"wire_number": n, "channel_type": 0, "input_mode": "voltage_single_ended_dc",
                      "options": [{"full_scale_v": 10, "coupling": "dc", "volts_per_count": .001,
                                   "zero_count": 0, "configuration_hex": bytes((n + 1,)).hex()}]}
                     for n in range(4)],
    }
    path = tmp_path / "synthetic-profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def config():
    session = default_session_config()
    session.acquisition.sample_rate = 1000
    session.acquisition.frame_size = 8
    session.acquisition.trigger.enabled = False
    session.acquisition.trigger.pretrigger_samples = 0
    session.acquisition.excitation.enabled = False
    for ch in session.ai_channels:
        ch.iepe_enabled = False
        ch.coupling, ch.full_scale = "dc", 10
    return session


def backend(tmp_path):
    return NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(profile(tmp_path)), timeout=.2))


@pytest.mark.parametrize("mode", ["Every Frame", "Manual Arm", "1st Frame", "1st-Manual Arm"])
def test_usb_host_trigger_uses_raw_stream_without_hardware_trigger_commands(usb, tmp_path, mode):
    samples = np.zeros((4096, 4), dtype="<i4")
    samples[:, 1] = np.arange(4096)
    samples[20:22, 0] = -2000
    usb.data = samples.tobytes()
    session = config()
    session.acquisition.frame_size = 16
    session.acquisition.trigger.enabled = True
    session.acquisition.trigger.mode = mode
    session.acquisition.trigger.source = "ai0"
    session.acquisition.trigger.level = 1
    session.acquisition.trigger.pretrigger_samples = -25
    b = backend(tmp_path)
    try:
        b.configure(session)
        b.start()
        result = b.read_frame()
        assert result.metadata["software_trigger"]
        assert result.metadata["trigger_sample_index"] == 20
        assert result.metadata["sample_start_index"] == 16
        np.testing.assert_allclose(result.data, samples[16:32].T * .001)
        assert result.timestamps[0] == 0
        assert result.metadata["channel_full_scales"]["ai0"] == 10
        b.stop()
    finally:
        b.close()
    assert all(command not in {0x43} for command, _ in usb.commands)


def test_offline_profile_preview_preserves_unconfirmed_current(usb, tmp_path):
    from python_vna.daq.netdaq import profile_preview
    b, _ = iepe_backend(tmp_path, supply="external", current=None)
    usb.commands.clear()
    report = profile_preview(b.settings.profile_path)
    assert report["valid"] and not report["hardware_accessed"]
    assert report["channels"][1]["iepe"]["current_ma"] is None
    assert report["channels"][1]["input_mode"] == "iepe"
    assert not usb.commands


def iepe_backend(tmp_path, *, supply="device_fixed", current=None):
    path = profile(tmp_path)
    data = json.loads(path.read_text())
    for entry in data["channels"][1:]:
        entry.update(input_mode="iepe", iepe_supply=supply, iepe_current_ma=current)
        entry["options"][0]["coupling"] = "ac"
    path.write_text(json.dumps(data))
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path), timeout=.2))
    session = config()
    session.acquisition.modal.enabled = False
    for channel in session.ai_channels[1:]:
        channel.iepe_enabled = True
        channel.iepe_current_ma = current or 0.0
        channel.coupling = "ac"
        channel.sensitivity = 1000 / 51.567
        channel.per_eu_mode = "/Volt"
        channel.engineering_unit = "m/s^2"
    return b, session


@pytest.mark.parametrize("supply", ["device_fixed", "external"])
@pytest.mark.parametrize("current", [None, 0, 2.0])
def test_iepe_supply_and_current_are_profile_facts_not_usb_writes(usb, tmp_path, supply, current):
    b, session = iepe_backend(tmp_path, supply=supply, current=current)
    assert b.list_devices()[0].capability.supports_iepe
    b.configure(session)
    try:
        b.start()
        frame = b.read_frame()
        info = frame.metadata["channel_modes"]["ai1"]["iepe"]
        assert info == {
            "enabled": True, "supply": supply, "current_ma": current or None,
            "current_known": bool(current), "control": "read_only",
        }
        assert frame.metadata["channel_modes"]["ai1"]["channel_type"] == 0
        np.testing.assert_allclose(frame.data[1], np.arange(1, 32, 4) * .001)
        assert frame.metadata["units"] == "V"
    finally:
        b.close()
    assert not any(cmd in {0x43, 0x4F} for cmd, _ in usb.commands)


@pytest.mark.parametrize("change", [
    {"iepe_current_ma": None, "iepe_supply": "unspecified"},
    {"iepe_current_ma": -1}, {"iepe_current_ma": float("nan")},
    {"iepe_current_ma": True}, {"iepe_supply": "switch_on_by_usb"},
])
def test_iepe_unknown_supply_or_invalid_current_never_becomes_a_profile(tmp_path, change):
    path = profile(tmp_path)
    data = json.loads(path.read_text())
    entry = data["channels"][1]
    entry.update(input_mode="iepe", iepe_supply="device_fixed", iepe_current_ma=2.0)
    entry.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError, match="IEPE"):
        NetDaqProfile.load(path)


def test_unknown_current_does_not_inherit_ni_default(usb, tmp_path):
    b, session = iepe_backend(tmp_path)
    session.ai_channels[1].iepe_current_ma = 2.1
    with pytest.raises(DaqBackendError, match="read-only channel mode"):
        b.configure(session)
    assert not usb.commands


def test_unknown_current_usb_exception_does_not_change_tcp_profile_rules(tmp_path):
    iepe_backend(tmp_path)
    path = tmp_path / "synthetic-profile.json"
    data = json.loads(path.read_text())
    data["transport"] = "tcp"
    for entry in data["channels"]:
        entry["channel_type"] = 3
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError, match="IEPE"):
        NetDaqProfile.load(path)


def test_usb_iepe_voltage_sensitivity_and_vna_roundtrip(usb, tmp_path):
    from python_vna.controller import VnaController
    from python_vna.ui.main_window import MainWindow

    b, session = iepe_backend(tmp_path)
    counts = np.zeros((64, 4), dtype="<i4")
    counts[:, 1] = np.round(51567 * np.sin(2 * np.pi * np.arange(64) / 8)).astype(np.int32)
    usb.data = counts.tobytes()
    session.acquisition.frame_size = 64
    path = tmp_path / "synthetic-profile.json"
    data = json.loads(path.read_text())
    for entry in data["channels"][1:]:
        entry["options"][0]["volts_per_count"] = 1e-6
    path.write_text(json.dumps(data))
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path)))
    ctl = VnaController(b, session)
    ctl.configure()
    try:
        ctl.start()
        measurement = ctl.read_and_process()
    finally:
        ctl.close()
    volts = measurement.time_data["channels"]["ai1"]
    np.testing.assert_allclose(volts, counts[:, 1] * 1e-6)
    channel = session.ai_channels[1]
    eu = volts * MainWindow._effective_euscale_fac(channel.sensitivity, channel.per_eu_mode)
    np.testing.assert_allclose(eu, np.sin(2 * np.pi * np.arange(64) / 8), atol=1e-5)
    target = tmp_path / "iepe.vna"
    save_legacy_vna(ctl.snapshot(), target)
    loaded = load_legacy_vna(target)
    assert loaded.config.ai_channels[1].iepe_enabled
    assert loaded.config.ai_channels[1].iepe_current_ma == 0
    assert loaded.config.ai_channels[1].engineering_unit == "m/s^2"
    assert loaded.config.ai_channels[1].sensitivity == pytest.approx(1000 / 51.567)
    saved_iepe = loaded.measurement.metadata["daq_provenance"]["channel_modes"]["ai1"]["iepe"]
    assert saved_iepe["supply"] == "device_fixed" and saved_iepe["current_ma"] is None
    np.testing.assert_allclose(
        loaded.measurement.time_data["channels"][loaded.config.ai_channels[1].label], volts)


@pytest.mark.parametrize("storage_format", ["text", "binary"])
def test_iepe_record_retains_supply_without_scaling_voltage(usb, tmp_path, storage_format):
    from python_vna.continuous_recording import ContinuousDatWriter, read_dat_header
    from python_vna.analysis_data import load_analysis_path, load_continuous_channels

    b, session = iepe_backend(tmp_path, supply="external")
    b.configure(session)
    output = tmp_path / "record"
    writer = ContinuousDatWriter(
        output, session, device_name=b.device_name, channel_names=["ai0", "ai1", "ai2", "ai3"],
        software_version="synthetic test", storage_format=storage_format,
        compress_closed_segments=False, daq_provenance=b.acquisition_provenance())
    writer.start()
    try:
        b.start()
        frame = b.read_frame()
        writer.write_frame(frame)
    finally:
        b.close()
        writer.close(completed=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    info = manifest["daq_provenance"]["channel_modes"]["ai1"]["iepe"]
    assert info["supply"] == "external" and info["current_ma"] is None
    assert manifest["session"]["ai_channels"][1]["iepe_current_ma"] == 0
    if storage_format == "text":
        header = read_dat_header(output / manifest["segments"][0]["path"])
        assert header["daq_provenance"]["channel_modes"]["ai1"]["iepe"] == info
    dataset = load_analysis_path(output / "manifest.json")
    _, channels = load_continuous_channels(dataset, ["ai1"])
    np.testing.assert_allclose(channels["ai1"], frame.data[1])


def test_iepe_record_worker_supplies_provenance_without_spectrum_processing(usb, tmp_path):
    from python_vna.controller import VnaController
    from python_vna.signal_pipeline import FrameProcessor
    from python_vna.ui.main_window import ContinuousRecordingWorker
    from unittest.mock import patch

    b, session = iepe_backend(tmp_path)
    ctl = VnaController(b, session)
    worker = ContinuousRecordingWorker(ctl, b.device_name, tmp_path / "worker-record")
    errors = []
    worker.error.connect(errors.append)
    worker.recording_status.connect(lambda status: worker.request_stop() if status.total_frames == 3 else None)
    with patch.object(FrameProcessor, "process", side_effect=AssertionError("Record must not compute spectra")):
        worker.run()
    assert not errors and ctl.state.measurement is None
    manifest = json.loads((tmp_path / "worker-record" / "manifest.json").read_text())
    assert manifest["completed"] and manifest["total_frames"] == 3
    assert manifest["daq_provenance"]["channel_modes"]["ai1"]["iepe"]["current_ma"] is None


def test_ctypes_byte_packed_abi_and_endpoint_validation():
    p = ct.sizeof(ct.c_void_p)
    assert ct.sizeof(u.DeviceDescriptor) == 18
    assert u.UsbDevice.descriptor.offset == 512 + 3 * p
    assert u.UsbDevice.config.offset == 512 + 3 * p + 18
    assert u.UsbBus.devices.offset == 512 + 2 * p
    assert u.check_endpoints(USB_CONFIGURATION)[0x82] == (2, 512)
    for bad in (USB_CONFIGURATION[:-1], USB_CONFIGURATION.replace(b"\x82", b"\x81")):
        with pytest.raises(DaqBackendError):
            u.check_endpoints(bad)


def test_read_only_probe_no_ip_or_profile_required(usb):
    b = NetDaqBackend(NetDaqSettings(transport="usb"))
    assert not b.ready
    report = b.probe((0, 1))
    assert report["usb"]["product"] == "USB2404V10"
    assert report["transport"] == "usb"
    assert report["channels"]["0"]["current_configuration_hex"] == "01"
    info = report["usb_descriptor"]
    assert info["channel_count"] == 4 and info["adc_bits"] == 24
    assert (info["nominal_voltage_min_mv"], info["nominal_voltage_max_mv"]) == (-10000, 10000)
    assert info["nominal_volts_per_count"] == 10 / 8388608
    assert info["voltage_unit"] == "mV"
    assert info["count_encoding"] == "signed_twos_complement"
    assert info["adc_code_count"] == 2**24
    assert info["calibration_verified"] is False
    assert {cmd for cmd, _ in usb.commands} == {0x44, 0x56, 0x41, 0x42}
    assert usb.close_count == 1
    with pytest.raises(DaqBackendError):
        b.configure(config())
    assert len(usb.commands) == 6


def changed_descriptor(offset, value):
    raw = bytearray(REAL_DESCRIPTOR)
    raw[3 + offset] = value
    raw[-2] = sum(raw[:-2]) & 255
    return bytes(raw)


@pytest.mark.parametrize("offset,value", [(0, 1), (2, 32), (3, 0), (3, 5), (4, 3)])
def test_usb_descriptor_rejects_unsupported_or_impossible_limits(offset, value):
    with pytest.raises(DaqBackendError):
        u.descriptor_info(changed_descriptor(offset, value))


def test_probe_checks_physical_count_before_querying_echo_only_channels(usb):
    with pytest.raises(DaqBackendError, match="physical channel count"):
        NetDaqBackend(NetDaqSettings(transport="usb")).probe((0, 4))
    assert [cmd for cmd, _ in usb.commands] == [0x44]
    assert usb.close_count == 1


def test_probe_profile_match_includes_firmware(usb, tmp_path):
    b = backend(tmp_path)
    assert b.probe()["profile_matches"] is True
    usb.firmware = reply(0x56, bytes((3, 0)))
    assert b.probe()["profile_matches"] is False
    assert not any(cmd in {0x49, 0x53, 0x43} for cmd, _ in usb.commands)


def test_missing_device_releases_lease_without_open_or_commands(usb):
    usb.bus.devices = ct.POINTER(u.UsbDevice)()
    with pytest.raises(DaqBackendError, match="target not found"):
        u.UsbConnection().connect()
    assert not usb.commands and usb.close_count == 0
    usb.bus.devices = ct.pointer(usb.dev)
    with u.UsbConnection():
        pass
    assert usb.close_count == 1


@pytest.mark.parametrize("number", [-1, 4, 255, True])
def test_channel_queries_reject_invalid_physical_indices(usb, number):
    with u.UsbConnection() as control:
        for operation in (control.channel_descriptor, control.channel_configuration):
            with pytest.raises(DaqBackendError, match="physical channel"):
                operation(number)
    assert not usb.commands


@pytest.mark.parametrize("actual", [b"", b"\x06\x01"])
def test_channel_configuration_must_match_declared_length(usb, actual):
    usb.configurations[0] = actual
    with u.UsbConnection() as control:
        with pytest.raises(DaqBackendError, match="reply"):
            control.channel_configuration(0)
    assert [cmd for cmd, _ in usb.commands] == [0x41, 0x42]


@pytest.mark.parametrize("declared", [0, 123, 255])
def test_invalid_declared_configuration_length_never_queries_config(usb, declared):
    usb.config_lengths[0] = declared
    with u.UsbConnection() as control:
        with pytest.raises(DaqBackendError, match="configuration length"):
            control.channel_configuration(0)
    assert [cmd for cmd, _ in usb.commands] == [0x41]


def test_channel_configuration_keeps_opaque_bytes_separate_from_echo(usb):
    usb.configurations[0], usb.configurations[1] = b"\x06", b"\x01"
    with u.UsbConnection() as control:
        assert control.channel_configuration(0) == b"\x06"
        assert control.channel_configuration(1) == b"\x01"
        assert control.channel_descriptor(0) == (0, 1)
    assert not any(cmd == 0x43 for cmd, _ in usb.commands)


@pytest.mark.parametrize("selector,divisor", [
    (0, 64), (0x81, 64), (0x82, 500), (0x84, 128), (0x85, 64),
    (0x86, 64), (0x87, 100), (0x88, 48), (0x89, 1000), (0x8A, 96), (0x90, 64),
])
def test_usb_clock_selectors_follow_usb_plugin_rules(selector, divisor):
    raw = changed_descriptor(27, selector)
    assert u.sampling_divisor(raw, 1000) == (divisor, 1000)


def test_usb_default_clock_uses_reported_maximum_not_network_table():
    raw = bytearray(changed_descriptor(27, 0x85))
    raw[8:12] = struct.pack(">i", 10000)
    raw[-2] = sum(raw[:-2]) & 255
    assert u.sampling_divisor(bytes(raw), 1000) == (5, 1000)
    assert u.sampling_divisor(REAL_DESCRIPTOR, 3000) == (21, 64000 / 21)
    assert u.sampling_divisor(REAL_DESCRIPTOR, 128000 / 43) == (21, 64000 / 21)


@pytest.mark.parametrize("rate", [0, -1, True, "1000", float("nan"), float("inf"), 128000, 1e-10])
def test_usb_invalid_divisors_are_rejected(rate):
    with pytest.raises(DaqBackendError):
        u.sampling_divisor(REAL_DESCRIPTOR, rate)


def test_usb_clock_without_init_ad_sequence_is_rejected():
    with pytest.raises(DaqBackendError, match="0x83"):
        u.sampling_divisor(changed_descriptor(27, 0x83), 1000)


@pytest.mark.parametrize("mask,divisor", [(0, 64), (16, 64), (31, 64), (1, 0)])
def test_init_ad_rejects_nonphysical_mask_or_zero_divisor_before_write(usb, mask, divisor):
    payload = struct.pack(">I", divisor) + b"\x01" + struct.pack("<I", mask) + bytes(4)
    with u.UsbConnection() as control:
        with pytest.raises(DaqBackendError, match="InitAD"):
            control.initialize_ad(payload)
    assert [cmd for cmd, _ in usb.commands] == [0x44]


def test_usb_profile_respects_declared_channel_count_and_rate(tmp_path):
    path = profile(tmp_path)
    data = json.loads(path.read_text())
    data["max_sample_rate"] = 128001
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError, match="reported device limit"):
        NetDaqProfile.load(path)
    data["max_sample_rate"] = 2000
    data["descriptor_hex"] = changed_descriptor(3, 2).hex()
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError, match="physical count"):
        NetDaqProfile.load(path)


@pytest.mark.parametrize("selector,divisor", [(0x87, 100), (0x89, 1000)])
def test_backend_uses_usb_clock_rules_for_init_ad_and_actual_rate(usb, tmp_path, selector, divisor):
    path = profile(tmp_path)
    data = json.loads(path.read_text())
    usb.descriptor = changed_descriptor(27, selector)
    data["descriptor_hex"] = usb.descriptor.hex()
    path.write_text(json.dumps(data))
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path)))
    b.configure(config())
    try:
        b.start()
        frame = b.read_frame()
        assert frame.sample_rate == 1000
        assert frame.metadata["actual_sample_rate"] == 1000
        payload = next(body for cmd, body in usb.commands if cmd == 0x49)
        assert int.from_bytes(payload[:4], "big") == divisor
    finally:
        b.close()


@pytest.mark.parametrize("corrupt", [False, True])
def test_lost_or_corrupt_start_ack_always_attempts_stop_on_close(usb, corrupt):
    control = u.UsbConnection(timeout=.1).connect()
    usb.drop_start, usb.corrupt_reply = not corrupt, corrupt
    with pytest.raises(DaqBackendError):
        control.start()
    assert control.may_be_running and control.stop_uncertain
    usb.corrupt_reply = False
    control.close()
    assert [cmd for cmd, _ in usb.commands] == [0x53, 0x50]
    assert not control.stop_uncertain
    assert usb.close_count == 1


def test_stop_rejected_latches_unknown_state_and_blocks_restart(usb):
    control = u.UsbConnection().connect()
    control.start()
    usb.reject_stop = True
    with pytest.raises(DaqBackendError):
        control.stop()
    n = len(usb.commands)
    with pytest.raises(DaqBackendError):
        control.start()
    assert len(usb.commands) == n
    with pytest.raises(DaqBackendError):
        control.close()
    assert control.stop_uncertain and not control.connected
    assert usb.close_count == 1


def test_probe_failure_releases_lease_and_resource(usb):
    usb.claim_error = -1
    with pytest.raises(DaqBackendError):
        u.UsbConnection().connect()
    assert usb.close_count == 1
    usb.claim_error = 0
    with u.UsbConnection():
        pass
    assert usb.close_count == 2


def test_usb_native_calls_reject_wrong_thread(usb):
    control = u.UsbConnection().connect()
    n = len(usb.native_threads)
    failures = []
    def run():
        try:
            control.close()
        except Exception as exc:
            failures.append(exc)
    t = threading.Thread(target=run)
    t.start(); t.join(2)
    assert len(failures) == 1 and len(usb.native_threads) == n
    control.close()


@pytest.mark.parametrize("selector,divisor", [(0, 64), (0x81, 64), (0x82, 500), (0x84, 128), (0x85, 5)])
def test_all_clock_rates(selector, divisor):
    raw = bytearray(REAL_DESCRIPTOR)
    raw[30] = selector
    raw[-2] = sum(raw[:-2]) & 255
    assert sampling_divisor(bytes(raw), 1000) == (divisor, 1000)


def test_usb_backend_split_bytes_channel_order_and_voltage_once(usb, tmp_path):
    b, session = backend(tmp_path), config()
    session.ai_channels[1].enabled = False
    b.configure(session)
    b.start()
    first, second = b.read_frame(), b.read_frame()
    np.testing.assert_array_equal(first.data, np.arange(32).reshape(8, 4).T[[0, 2, 3]] * .001)
    np.testing.assert_array_equal(second.data, np.arange(32, 64).reshape(8, 4).T[[0, 2, 3]] * .001)
    assert first.channel_names == ["ai0", "ai2", "ai3"]
    assert second.metadata["sample_start_index"] == 8
    assert first.metadata["transport"] == "usb"
    assert not first.metadata["stream_continuity_verified"]
    init = next(body for cmd, body in usb.commands if cmd == 0x49)
    assert init == struct.pack(">I", 64) + b"\x01" + struct.pack("<I", 15) + bytes(4)
    assert all(cmd != 0x43 for cmd, _ in usb.commands)
    b.close()
    assert not usb.pending and not b.stop_uncertain


def test_stop_request_only_sets_event_during_native_read(usb, tmp_path):
    b, ready, errors = backend(tmp_path), threading.Event(), []
    usb.pause = True
    def run():
        try:
            b.configure(config()); b.start(); ready.set(); b.read_frame()
        except NetDaqStopped:
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            b.close()
    t = threading.Thread(target=run)
    t.start()
    assert ready.wait(2)
    deadline = time.monotonic() + 2
    while not usb.pending and time.monotonic() < deadline:
        time.sleep(.001)
    b.request_stop()
    t.join(2)
    assert not t.is_alive() and not errors
    assert usb.submit_count == 1 and usb.cancel_count == 1
    assert set(usb.native_threads) == {t.ident}


def test_polling_timeout_keeps_same_transfer(usb):
    control = u.UsbConnection().connect()
    usb.pause = True
    with pytest.raises(DaqBackendError, match="timed out"):
        control.read_chunk(time.monotonic() + .03)
    assert usb.submit_count == 1 and usb.pending and usb.cancel_count == 0
    usb.pause = False
    data = control.read_chunk(time.monotonic() + 1)
    assert data == usb.data[:127] and usb.submit_count == 1
    control.close()


def test_unconfirmed_native_cancel_never_frees_pending_buffer(usb, monkeypatch):
    control = u.UsbConnection().connect()
    usb.pause = True
    with pytest.raises(DaqBackendError):
        control.read_chunk(time.monotonic() + .01)
    original_reap = usb.usb_reap_async_nocancel
    monkeypatch.setattr(usb, "usb_reap_async_nocancel", lambda *a: -116)
    monkeypatch.setattr(u, "CANCEL_CONFIRM_SECONDS", .01)
    try:
        with pytest.raises(DaqBackendError, match="unconfirmed"):
            control.close()
        assert control._pending and control.connected
        assert control in u._quarantined
        assert usb.close_count == 0 and control._context.value
        with pytest.raises(DaqBackendError, match="busy"):
            u.UsbConnection().connect()
    finally:
        monkeypatch.setattr(usb, "usb_reap_async_nocancel", original_reap)
        usb.pause = False
        control.close()


def test_normal_stop_drains_pending_block_without_driver_abort(usb):
    control = u.UsbConnection().connect()
    usb.pause = True
    with pytest.raises(DaqBackendError):
        control.read_chunk(time.monotonic() + .01)
    usb.pause = False
    control.close()
    assert usb.cancel_count == 0 and not control._pending
    assert not control._context.value and usb.close_count == 1


def test_usb_disconnect_errors_not_zeros(usb, tmp_path):
    b = backend(tmp_path)
    b.configure(config()); b.start()
    usb.disconnect = True
    with pytest.raises(DaqBackendError):
        b.read_frame()
    b.close()


def test_backend_stop_failure_disables_ready_and_reconfigure(usb, tmp_path):
    b = backend(tmp_path)
    b.configure(config()); b.start()
    usb.reject_stop = True
    with pytest.raises(DaqBackendError, match="stop was not acknowledged"):
        b.close()
    assert not b.ready and b.stop_uncertain and usb.close_count == 1
    with pytest.raises(DaqBackendError):
        b.configure(config())


def test_firmware_or_channel_configuration_change_fails_before_init(usb, tmp_path):
    b = backend(tmp_path)
    usb.firmware = reply(0x56, bytes((3, 0)))
    with pytest.raises(DaqBackendError, match="firmware"):
        b.configure(config())
    usb.firmware = reply(0x56, bytes((2, 0)))
    b.configure(config())
    usb.configurations[1] = b"\x07"
    with pytest.raises(DaqBackendError, match="configuration changed"):
        b.start()
    b.close()
    assert not any(cmd in {0x49, 0x53, 0x43} for cmd, _ in usb.commands)


@pytest.mark.parametrize("change", ["transport", "checksum", "mask", "order", "format", "firmware"])
def test_profile_rejects_unqualified_layout(tmp_path, change):
    path = profile(tmp_path)
    data = json.loads(path.read_text())
    if change == "transport":
        with pytest.raises(DaqBackendError, match="transport"):
            NetDaqBackend(NetDaqSettings(profile_path=str(path)))
        return
    if change == "checksum":
        data["descriptor_hex"] = data["descriptor_hex"][:-4] + "000d"
    elif change == "mask":
        data["init_ad_payload_hex"] = bytes(13).hex()
    elif change == "order":
        data["channels"].reverse()
    elif change == "format":
        data["stream_format"] = "raw24"
    elif change == "firmware":
        del data["firmware_hex"]
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError):
        NetDaqProfile.load(path)


def test_100_simulated_cycles(usb, tmp_path):
    b = backend(tmp_path)
    for _ in range(100):
        b.configure(config()); b.start(); b.read_frame(); b.close()
    assert usb.close_count == 100
    assert sum(cmd == 0x50 for cmd, _ in usb.commands) == 100


def test_hardware_validation_default_probe_sends_no_acquisition_commands(usb):
    from python_vna.daq.usbdaq_validation import validate_usb_device

    report = validate_usb_device()
    assert report["status"] == "passed"
    assert report["configuration_unchanged"] and report["transport_closed"]
    assert report["stop_acknowledged"] is None
    assert not report["calibration_verified"] and not report["stream_continuity_verified"]
    assert not any(cmd in {0x49, 0x53, 0x50, 0x43} for cmd, _ in usb.commands)


def test_hardware_validation_streams_statistics_without_changing_configuration(usb):
    from python_vna.daq.usbdaq_validation import validate_usb_device

    samples = np.array([[-8388608, 0, 8388607, 1], [0, 2, -4, -1]], dtype="<i4")
    usb.data = samples.tobytes()
    report = validate_usb_device(seconds=.002, sample_rate=1000)
    assert report["samples_per_channel"] == 2
    assert report["counts_min"] == [-8388608, 0, -4, -1]
    assert report["counts_max"] == [0, 2, 8388607, 1]
    assert report["stop_acknowledged"] and report["configuration_unchanged"]
    assert report["configured_sample_rate"] == 1000
    assert report["quality"]["channels"]["ai0"]["mean_v"] == -5
    assert report["quality"]["channels"]["ai1"]["mean_v"] == 10 / 8388608
    assert report["quality"]["channels"]["ai2"]["rail_fraction"] == .5
    assert all(cmd != 0x43 for cmd, _ in usb.commands)


def test_hardware_validation_invalid_samples_stop_and_preserve_failure_report(usb):
    from python_vna.daq.usbdaq_validation import validate_usb_device, UsbValidationFailure

    usb.data = np.array([8388608, 0, 0, 0], dtype="<i4").tobytes()
    with pytest.raises(UsbValidationFailure) as caught:
        validate_usb_device(seconds=.001, sample_rate=1000)
    report = caught.value.report
    assert report["status"] == "failed" and report["stop_acknowledged"]
    assert report["transport_closed"] and not usb.pending
    assert "invalid signed 24-bit" in report["error"]


def test_hardware_validation_stop_failure_cannot_report_success(usb):
    from python_vna.daq.usbdaq_validation import validate_usb_device, UsbValidationFailure

    usb.reject_stop = True
    with pytest.raises(UsbValidationFailure) as caught:
        validate_usb_device(seconds=.001, sample_rate=1000)
    report = caught.value.report
    assert report["status"] == "failed" and report["stop_acknowledged"] is False
    assert report["transport_closed"]


def test_hardware_validation_configuration_drift_detected(usb, monkeypatch):
    from python_vna.daq.usbdaq_validation import validate_usb_device, UsbValidationFailure

    original = usb.usb_bulk_write

    def change_after_stop(handle, endpoint, buffer, size, timeout):
        result = original(handle, endpoint, buffer, size, timeout)
        if usb.commands[-1][0] == 0x50:
            usb.configurations[0] = b"\x07"
        return result

    monkeypatch.setattr(usb, "usb_bulk_write", change_after_stop)
    with pytest.raises(UsbValidationFailure) as caught:
        validate_usb_device(seconds=.001, sample_rate=1000)
    assert caught.value.report["configuration_unchanged"] is False


@pytest.mark.parametrize("kwargs", [
    {"seconds": -1}, {"seconds": float("nan")}, {"sample_rate": 0},
    {"sample_rate": float("inf")}, {"channel_mask": 0}, {"channel_mask": True},
])
def test_hardware_validation_bad_arguments_never_open_device(usb, kwargs):
    from python_vna.daq.usbdaq_validation import validate_usb_device

    with pytest.raises(ValueError):
        validate_usb_device(**kwargs)
    assert not usb.native_threads


def test_hardware_validation_cli_keeps_report_and_refuses_overwrite(usb, tmp_path):
    from python_vna.daq.usbdaq_validation import main

    target = tmp_path / "hardware-report.json"
    assert main(["--output", str(target), "--cycles", "2"]) == 0
    report = json.loads(target.read_text())
    assert report["status"] == "passed" and len(report["cycles"]) == 2
    count = len(usb.commands)
    with pytest.raises(FileExistsError):
        main(["--output", str(target)])
    assert len(usb.commands) == count


def test_hardware_validation_cancel_pending_read_keeps_owner_stop(usb, monkeypatch):
    from python_vna.daq.usbdaq_validation import validate_usb_device, UsbValidationFailure

    event = threading.Event()
    original = usb.usb_submit_async

    def cancel_after_submission(*args):
        result = original(*args)
        event.set()
        return result

    monkeypatch.setattr(usb, "usb_submit_async", cancel_after_submission)
    with pytest.raises(UsbValidationFailure) as caught:
        validate_usb_device(seconds=1, sample_rate=1000, stop_event=event)
    report = caught.value.report
    assert report["status"] == "cancelled" and report["stop_acknowledged"]
    assert report["transport_closed"] and not usb.pending
    assert set(usb.native_threads) == {threading.get_ident()}


def test_hardware_validation_cli_failure_is_saved_and_does_not_open_device(usb, tmp_path):
    from python_vna.daq.usbdaq_validation import main

    target = tmp_path / "failed-report.json"
    assert main(["--output", str(target), "--seconds", "-1"]) == 1
    report = json.loads(target.read_text())
    assert report["status"] == "failed"
    assert "ValueError" in report["cycles"][0]["error"]
    assert not usb.native_threads


@pytest.fixture
def dasp_declared(usb, tmp_path):
    from python_vna.daq.dasp_profile import build_dasp_usb_profile, save_dasp_usb_profile

    source = tmp_path / "Channel.ini"
    source.write_text(
        "[Channel 0]\nSensitivity=0.08\nUnit=N\nSensitivity Physical Unit=m/s^2\n"
        "[Channel 1]\nLabel=Sensor Y\nUnit=m/s^2\nSensitivity Physical Unit=m/s^2\n"
        "Voltage Unit=mV\nSensitivity=50\nGain=2\nExternal gain=5\nOffset comp=4000\n",
        encoding="utf-8")
    report = NetDaqBackend(NetDaqSettings(transport="usb")).probe(range(4))
    data = build_dasp_usb_profile(source, report)
    target = tmp_path / "dasp-compatible.json"
    save_dasp_usb_profile(data, target)
    return data, target, source


def test_dasp_compatibility_preserves_unknown_modes_and_provenance(usb, dasp_declared):
    from python_vna.daq.dasp_profile import apply_dasp_profile_sensors

    data, path, source = dasp_declared
    assert data["verified"] is False
    assert data["vendor_parameters"]["sensor_import_errors"]
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path)))
    assert b.ready and b.profile.qualification == "dasp_parameters"
    assert b.profile.iepe_settings(1)["enabled"] is None
    session = config()
    session.acquisition.frame_size = 2
    for channel in session.ai_channels:
        channel.full_scale = 10
    previous_unit = session.ai_channels[0].engineering_unit
    apply_dasp_profile_sensors(session.ai_channels, b.profile)
    assert session.ai_channels[1].sensitivity == 2
    assert session.ai_channels[1].offset == 0
    assert session.ai_channels[0].engineering_unit == previous_unit
    samples = np.array([[0, 4194304, -4194304, 0], [8388607, 0, 0, -8388608]], dtype="<i4")
    usb.data = samples.tobytes()
    b.configure(session)
    try:
        b.start()
        frame = b.read_frame()
        np.testing.assert_array_equal(frame.data, samples.T.astype(float) * 10 / 8388608)
        # Sensor gains affect engineering units only, not backend volts.
        assert frame.data[1, 0] == 5
        assert frame.data[1, 0] * session.ai_channels[1].sensitivity == 10
        assert not frame.metadata["electrical_calibration_verified"]
        assert frame.metadata["hardware_coupling"] == "unknown_preserved"
        assert frame.metadata["channel_modes"]["ai1"]["iepe"]["enabled"] is None
        assert "channel_hardware_ranges" not in frame.metadata
        assert frame.metadata["vendor_parameter_source"] == str(source.resolve())
    finally:
        b.close()
    assert all(cmd != 0x43 for cmd, _ in usb.commands)


@pytest.mark.parametrize("change", [
    "verified", "tcp", "iepe", "coupling", "scale", "offset", "range", "rate", "missing_source",
    "firmware", "partial_stream",
])
def test_dasp_compatibility_declaration_cannot_claim_extra_qualification(dasp_declared, change):
    data, path, _ = dasp_declared
    data = deepcopy(data)
    if change == "verified":
        data["verified"] = True
    elif change == "tcp":
        data["transport"] = "tcp"
    elif change == "iepe":
        data["channels"][0]["input_mode"] = "iepe"
    elif change == "coupling":
        data["channels"][0]["options"][0]["coupling"] = "ac"
    elif change in {"scale", "offset", "range"}:
        key = {"scale": "volts_per_count", "offset": "zero_count", "range": "full_scale_v"}[change]
        data["channels"][0]["options"][0][key] = 1
    elif change == "rate":
        data["max_sample_rate"] = 128000
    elif change == "firmware":
        data["firmware_hex"] = reply(0x56, bytes((3, 0))).hex()
    elif change == "partial_stream":
        data["channels"].pop()
        data["init_ad_payload_hex"] = (
            struct.pack(">I", 1) + b"\x01" + struct.pack("<I", 7) + bytes(4)).hex()
    else:
        data.pop("vendor_parameters")
    path.write_text(json.dumps(data))
    with pytest.raises(DaqBackendError):
        NetDaqProfile.load(path)


def test_dasp_compatibility_still_refuses_configuration_drift(usb, dasp_declared):
    _, path, _ = dasp_declared
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path)))
    session = config()
    for channel in session.ai_channels:
        channel.full_scale = 10
    b.configure(session)
    usb.configurations[0] = b"\x07"
    with pytest.raises(DaqBackendError, match="configuration changed"):
        b.start()
    b.close()
    assert not any(cmd in {0x49, 0x53, 0x43} for cmd, _ in usb.commands)


def test_dasp_compatibility_checks_host_disabled_channel_configuration(usb, dasp_declared):
    _, path, _ = dasp_declared
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(path)))
    session = config()
    for channel in session.ai_channels:
        channel.full_scale = 10
    session.ai_channels[0].enabled = False
    b.configure(session)
    usb.configurations[0] = b"\x07"
    with pytest.raises(DaqBackendError, match="ai0.*configuration changed"):
        b.start()
    b.close()
    assert not any(cmd in {0x49, 0x53, 0x43} for cmd, _ in usb.commands)


def test_dasp_compatibility_preserves_source_and_target_on_failed_save(usb, dasp_declared):
    from python_vna.daq.dasp_profile import save_dasp_usb_profile

    data, path, source = dasp_declared
    original, source_original = path.read_bytes(), source.read_bytes()
    data["verified"] = True
    with pytest.raises(DaqBackendError):
        save_dasp_usb_profile(data, path)
    assert path.read_bytes() == original and source.read_bytes() == source_original
    assert not list(path.parent.glob("tmp_dasp_*"))


def test_dasp_compatibility_never_overwrites_vendor_parameters(dasp_declared):
    from python_vna.daq.dasp_profile import save_dasp_usb_profile

    data, _, source = dasp_declared
    original = source.read_bytes()
    with pytest.raises(DaqBackendError, match="不能覆盖"):
        save_dasp_usb_profile(data, source)
    assert source.read_bytes() == original


def test_dasp_compatibility_vna_restores_exact_engineering_scale(usb, dasp_declared, tmp_path):
    from python_vna.controller import VnaController
    from python_vna.daq.dasp_profile import apply_dasp_profile_sensors

    _, profile_path, _ = dasp_declared
    b = NetDaqBackend(NetDaqSettings(transport="usb", profile_path=str(profile_path)))
    session = config()
    for channel in session.ai_channels:
        channel.full_scale = 10
    apply_dasp_profile_sensors(session.ai_channels, b.profile)
    ctl = VnaController(b, session)
    ctl.configure()
    try:
        ctl.start()
        ctl.read_and_process()
    finally:
        ctl.close()
    path = tmp_path / "dasp.vna"
    save_legacy_vna(ctl.snapshot(), path)
    loaded = load_legacy_vna(path)
    assert loaded.config.ai_channels[1].sensitivity == 2
    provenance = loaded.measurement.metadata["daq_provenance"]
    assert provenance["qualification"] == "dasp_parameters"
    assert provenance["electrical_calibration_verified"] is False
    assert provenance["channel_modes"]["ai1"]["iepe"]["enabled"] is None


def test_usb_controller_vna_roundtrip(usb, tmp_path):
    from python_vna.controller import VnaController

    b, session = backend(tmp_path), config()
    session.acquisition.sample_rate = 1500
    session.acquisition.frame_size = 32
    session.acquisition.modal.enabled = False
    ctl = VnaController(b, session)
    ctl.configure(); ctl.start()
    measurement = ctl.read_and_process()
    ctl.close()
    path = tmp_path / "usb.vna"
    save_legacy_vna(ctl.snapshot(), path)
    loaded = load_legacy_vna(path)
    assert loaded.config.acquisition.sample_rate == 64000 / 43
    assert loaded.measurement.metadata["daq_provenance"]["transport"] == "usb"
    assert loaded.measurement.metadata["daq_provenance"]["channel_modes"]["ai1"]["wire_number"] == 1


def test_usb_avg_overlap_uses_existing_processor(usb, tmp_path):
    from python_vna.controller import VnaController

    session = config()
    session.acquisition.frame_size = 64
    session.acquisition.overlap_percent = 50
    session.acquisition.averaging.count = 3
    session.acquisition.modal.enabled = False
    ctl = VnaController(backend(tmp_path), session)
    ctl.set_averaging_enabled(True)
    ctl.configure()
    try:
        ctl.start()
        result = [ctl.read_and_process() for _ in range(3)][-1]
        assert result.metadata["average_count"] == 3
        assert set(result.frf) == {"ai0->ai1", "ai0->ai2", "ai0->ai3"}
        assert result.metadata["daq_provenance"]["transport"] == "usb"
    finally:
        ctl.close()


def test_usb_binary_record_preserves_actual_rate_and_channels(usb, tmp_path):
    from python_vna.continuous_recording import ContinuousDatWriter
    from python_vna.analysis_data import load_analysis_path, load_continuous_channels

    session = config()
    session.acquisition.sample_rate = 1500
    b = backend(tmp_path)
    b.configure(session)
    b.start()
    writer = ContinuousDatWriter(
        tmp_path / "record", session, device_name=b.device_name,
        channel_names=[c.name for c in session.ai_channels], software_version="test",
        storage_format="binary", compress_closed_segments=False)
    writer.start()
    frames = []
    try:
        for _ in range(3):
            frame = b.read_frame()
            frames.append(frame.data.copy())
            writer.write_frame(frame)
    finally:
        b.close()
        writer.close(completed=True)
    path = tmp_path / "record" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["completed"] and manifest["total_samples"] == 24
    assert manifest["sample_rate"] == 64000 / 43
    dataset = load_analysis_path(path)
    time_s, channels = load_continuous_channels(dataset, ["ai0", "ai3"])
    np.testing.assert_allclose(time_s, np.arange(24) / (64000 / 43))
    data = np.concatenate(frames, axis=1)
    np.testing.assert_array_equal(channels["ai0"], data[0])
    np.testing.assert_array_equal(channels["ai3"], data[3])


def test_usb_cli_remains_opt_in():
    assert parse_args([]).backend == "ni"
    args = parse_args(["--probe-netdaq-json", "--netdaq-transport", "usb", "--netdaq-probe-channels", "0,1"])
    assert args.netdaq_transport == "usb" and args.netdaq_usb_index == 0


def test_unknown_product_never_sends_vendor_commands(usb):
    usb.product = b"DifferentModel"
    with pytest.raises(DaqBackendError):
        u.UsbConnection().connect()
    assert not usb.commands and usb.close_count == 1
