from __future__ import annotations

import json
import socket
import socketserver
import struct
import threading
import time
from unittest import mock

import numpy as np
import pytest

from python_vna.app import build_backend, parse_args
from python_vna.controller import VnaController
from python_vna.daq.base import DaqBackendError
from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings
from python_vna.daq.netdaq_protocol import (
    NetDaqProtocolError, NetDaqStopped, LegacyConnection, WireConnection,
    checked_ack, decode_counts, legacy_request, sampling_divisor,
)
from python_vna.storage import default_session_config, save_legacy_vna, load_legacy_vna
from python_vna.continuous_recording import ContinuousDatWriter


def ack(command):
    body = b"!" + bytes((command,)) + b"OK"
    return body + bytes((sum(body) & 255, 13))


def recv_exact(sock, length):
    result = bytearray()
    while len(result) < length:
        data = sock.recv(length - len(result))
        if not data:
            raise EOFError()
        result.extend(data)
    return bytes(result)


class Emulator:
    """Local TCP transport test double, not a hardware qualification profile."""
    def __init__(self, fmt="packed24"):
        self.fmt = fmt
        self.commands = []
        self.invalid_requests = []
        self.started = threading.Event()
        self.closing = threading.Event()
        self.pause = False
        self.drop_start_ack = False
        self.corrupt_magic = False
        self.truncate = False
        self.reject_stop = False
        self.channel_types = {}
        desc = bytearray(28)
        desc[4] = 3 if fmt == "packed24" else 0
        self.descriptor = b"!D!" + bytes(desc) + b"\0\r"
        emulator = self

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        class Control(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(30)
                self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    while not emulator.closing.is_set():
                        header = recv_exact(self.request, 3)
                        length = header[2]
                        frame = header + recv_exact(self.request, length - 3 + length % 2)
                        if frame[:1] != b"!" or frame[length - 2] != sum(frame[:length - 2]) & 255:
                            emulator.invalid_requests.append(frame)
                            return
                        command = frame[1]
                        body = frame[3:length - 2]
                        emulator.commands.append((command, body))
                        if command == 0x44:
                            response = emulator.descriptor
                        elif command == 0x56:
                            response = b"!V\x07\x01\x02\0\r"
                        elif command == 0x41:
                            head = bytes((0x21, command, 8, body[0], emulator.channel_types.get(body[0], 6), 1))
                            response = head + bytes((sum(head) & 255, 13))
                        elif command == 0x42:
                            head = bytes((0x21, command, 8, body[0], 0xAA, 0))
                            response = head + bytes((sum(head) & 255, 13))
                        else:
                            response = ack(command)
                        if command == 0x53:
                            emulator.started.set()
                            if emulator.drop_start_ack:
                                continue
                        if command == 0x50:
                            if emulator.reject_stop:
                                response = ack(0x43)
                            else:
                                emulator.started.clear()
                        # Exercise recv_exact with TCP fragmentation, not preassembled mocks.
                        self.request.sendall(response[:2])
                        self.request.sendall(response[2:])
                except (EOFError, OSError):
                    return

        class Stream(socketserver.BaseRequestHandler):
            def handle(self):
                bits = int(emulator.fmt[-2:])
                counts = np.arange(32, dtype=np.int32)
                payload = b"".join((int(v) & ((1 << bits) - 1)).to_bytes(bits // 8, "little") for v in counts)
                try:
                    while not emulator.closing.is_set():
                        if not emulator.started.wait(0.01) or emulator.pause:
                            emulator.closing.wait(0.01)
                            continue
                        packet = payload
                        if emulator.fmt == "packed24":
                            magic = 0 if emulator.corrupt_magic else 0xFFFFAAAA
                            packet = struct.pack("<IH", magic, len(payload)) + payload
                        if emulator.truncate:
                            self.request.sendall(packet[:8])
                            return
                        self.request.sendall(packet[:5])
                        self.request.sendall(packet[5:])
                        emulator.closing.wait(0.001)
                except OSError:
                    return

        self.control = Server(("127.0.0.1", 0), Control)
        self.stream = Server(("127.0.0.1", 0), Stream)
        self.threads = []

    def __enter__(self):
        for server in (self.control, self.stream):
            thread = threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.01), daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def __exit__(self, *args):
        self.closing.set()
        for server in (self.control, self.stream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)

    def profile(self, path):
        data = {
            "schema_version": 1, "verified": True, "model": "LOCAL TEST DOUBLE ONLY",
            "evidence": "synthetic loopback fixture; never use with hardware",
            "descriptor_hex": self.descriptor.hex(), "stream_format": self.fmt,
            "init_ad_payload_hex": bytes(13).hex(), "divisor_offset": 0,
            "max_sample_rate": 64000,
            "channels": [{"wire_number": n + 1, "channel_type": 6,
                          "input_mode": "voltage_single_ended_ac", "options": [{
                "full_scale_v": 10, "coupling": "ac",
                "coupling_index": 1, "gain_index": 2,
                "volts_per_count": .001 * (n + 1), "zero_count": n,
            }]} for n in range(4)],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def settings(self, profile=""):
        return NetDaqSettings("127.0.0.1", self.control.server_address[1],
                              self.stream.server_address[1], .3, str(profile))


def session():
    result = default_session_config()
    result.acquisition.frame_size = 8
    result.acquisition.sample_rate = 3000
    result.acquisition.trigger.enabled = False
    result.acquisition.trigger.pretrigger_samples = 0
    result.acquisition.excitation.enabled = False
    result.acquisition.modal.smart_hit_enabled = False
    for ch in result.ai_channels:
        ch.enabled = True
        ch.iepe_enabled = False
        ch.coupling = "ac"
        ch.full_scale = 10
    return result


def test_fixed_frames_and_boundaries():
    assert legacy_request(0x44).hex() == "2144056a0d00"
    assert legacy_request(0x41, b"\x01").hex() == "21410601690d"
    assert legacy_request(0x49, bytes(13))[2] == 18
    with pytest.raises(NetDaqProtocolError):
        legacy_request(0x43, bytes(251))
    for value in (b"!COK", b"!CNO\0\r", ack(0x43)[:-1] + b"\0"):
        with pytest.raises(NetDaqProtocolError):
            checked_ack(value, 0x43)
    descriptor = b"!D!" + bytes(28) + bytes(2)
    assert sampling_divisor(descriptor, 3000) == (21, 64000 / 21)
    family1 = descriptor[:3] + b"\x01" + descriptor[4:]
    assert sampling_divisor(family1, 1000) == (50, 1000)
    for rate in (0, -1, float("nan"), float("inf"), 1e9, 1e-10, True):
        with pytest.raises(DaqBackendError):
            sampling_divisor(descriptor, rate)


@pytest.mark.parametrize("bits", [16, 24, 32])
def test_sample_decode_boundaries(bits):
    values = [0, 1, -1, (1 << (bits - 1)) - 1, -(1 << (bits - 1))]
    payload = b"".join((v & ((1 << bits) - 1)).to_bytes(bits // 8, "little") for v in values)
    np.testing.assert_array_equal(decode_counts(payload, bits), values)
    with pytest.raises(NetDaqProtocolError):
        decode_counts(payload[:-1], bits)


def test_unverified_backend_never_opens_socket():
    backend = NetDaqBackend()
    with mock.patch("socket.create_connection") as connect:
        assert backend.list_devices() == []
        with pytest.raises(DaqBackendError, match="禁止采集"):
            backend.configure(session())
        connect.assert_not_called()
    assert parse_args([]).backend == "ni"
    assert isinstance(build_backend("netdaq"), NetDaqBackend)


def test_probe_is_read_only(tmp_path):
    with Emulator() as emulator:
        backend = NetDaqBackend(emulator.settings())
        report = backend.probe([1, 2, 3, 4])
        assert report["descriptor_hex"] == emulator.descriptor.hex()
        assert report["channels"]["1"]["type"] == 6
        assert report["channels"]["1"]["current_configuration_hex"] == "aa00"
        device = backend.list_devices()[0]
        assert device.ai_channels == []
        assert not device.capability.supports_output
        assert all(command in {0x44, 0x56, 0x41, 0x42} for command, body in emulator.commands)


@pytest.mark.parametrize("fmt", ["packed24", "raw16", "raw24", "raw32"])
def test_multi_channel_volts_actual_rate_and_enabled_subset(tmp_path, fmt):
    with Emulator(fmt) as emulator:
        profile = emulator.profile(tmp_path / "device.json")
        backend = NetDaqBackend(emulator.settings(profile))
        cfg = session()
        cfg.ai_channels[1].enabled = False
        backend.configure(cfg)
        assert cfg.acquisition.sample_rate == 64000 / 21
        try:
            backend.start()
            frame = backend.read_frame()
            assert frame.channel_names == ["ai0", "ai2", "ai3"]
            for index, ch in enumerate([0, 2, 3]):
                expected = (np.arange(8) * 4 + ch - ch) * .001 * (ch + 1)
                np.testing.assert_allclose(frame.data[index], expected)
            np.testing.assert_allclose(frame.timestamps, np.arange(8) / frame.sample_rate)
            assert frame.metadata["units"] == "V"
            assert frame.metadata["requested_sample_rate"] == 3000
            assert backend.read_frame().metadata["sample_start_index"] == 8
        finally:
            backend.close()
        assert emulator.commands[-1][0] == 0x50
        assert not emulator.invalid_requests


def test_range_trigger_ao_and_descriptor_gates_before_writes(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        backend = NetDaqBackend(emulator.settings(path))
        for key in ("range", "trigger", "ao"):
            cfg = session()
            if key == "range": cfg.ai_channels[0].full_scale = 1
            if key == "trigger":
                cfg.acquisition.trigger.enabled = True
                cfg.acquisition.trigger.mode = "Every Frame"
                cfg.acquisition.trigger.source = "ai63"
            if key == "ao": cfg.acquisition.excitation.enabled = True
            with pytest.raises(DaqBackendError): backend.configure(cfg)
        assert emulator.commands == []
        emulator.descriptor = emulator.descriptor[:-1] + b"\x01"
        with pytest.raises(DaqBackendError, match="descriptor differs"):
            backend.configure(session())
        assert [command for command, body in emulator.commands] == [0x44]


def test_profile_is_explicitly_verified_and_calibrated(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        valid = json.loads(path.read_text())
        for key, value in (("verified", False), ("max_sample_rate", float("nan")),
                           ("evidence", ""), ("divisor_offset", 12), ("stream_format", "guess")):
            path.write_text(json.dumps({**valid, key: value}))
            with pytest.raises(DaqBackendError): NetDaqProfile.load(path)


def test_cancel_stalled_read_and_restart_100_times(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        backend = NetDaqBackend(emulator.settings(path))
        for _ in range(100):
            backend.configure(session())
            backend.start()
            backend.request_stop()
            with pytest.raises(NetDaqStopped): backend.read_frame()
            backend.close()
            assert backend._owner is None and backend._control is None and backend._stream is None
        assert sum(command == 0x50 for command, body in emulator.commands) == 100
        assert not emulator.invalid_requests
        emulator.pause = True
        entered = threading.Event()
        errors = []
        def acquire():
            try:
                backend.configure(session())
                backend.start()
                entered.set()
                backend.read_frame()
            except NetDaqStopped:
                pass
            except Exception as exc:
                errors.append(exc)
            finally:
                backend.close()
        thread = threading.Thread(target=acquire)
        thread.start()
        assert entered.wait(3)
        start = time.monotonic()
        backend.request_stop()
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert time.monotonic() - start < 1


@pytest.mark.parametrize("fault", ["corrupt_magic", "truncate", "drop_start_ack"])
def test_stream_or_start_failure_still_stops_device(tmp_path, fault):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        setattr(emulator, fault, True)
        backend = NetDaqBackend(emulator.settings(path))
        backend.configure(session())
        try:
            with pytest.raises(DaqBackendError):
                backend.start()
                backend.read_frame()
        finally:
            backend.close()
        assert any(command == 0x50 for command, body in emulator.commands)


def test_partial_tcp_timeout_invalidates_control_connection():
    a, b = socket.socketpair()
    try:
        a.settimeout(.01)
        wire = WireConnection("unused", 1, .05)
        wire.sock = a
        b.sendall(b"ab")
        with pytest.raises(NetDaqProtocolError, match="2/4"):
            wire.receive(4)
    finally:
        a.close(); b.close()


def test_controller_avg_overlap_record_and_legacy_roundtrip(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        backend = NetDaqBackend(emulator.settings(path))
        cfg = session()
        cfg.ai_channels[0].sensitivity = 2
        cfg.acquisition.overlap_percent = 50
        cfg.acquisition.averaging.count = 3
        controller = VnaController(backend, cfg)
        controller.set_averaging_enabled(True)
        controller.configure()
        try:
            controller.start()
            measurements = [controller.read_and_process() for _ in range(3)]
            last = measurements[-1]
            assert last.metadata["average_count"] == 3
            assert "ai0->ai1" in last.frf
            assert last.sample_rate == cfg.acquisition.sample_rate == 64000 / 21
            np.testing.assert_allclose(last.time_data["channels"]["ai0"],
                                       measurements[0].time_data["channels"]["ai0"])
            output = tmp_path / "netdaq.vna"
            last.metadata["daq_provenance"]["device_model"] = "model~with separator"
            save_legacy_vna(controller.snapshot(), output)
            saved = load_legacy_vna(output)
            assert saved.measurement.sample_rate == pytest.approx(last.sample_rate)
            assert saved.measurement.metadata["daq_provenance"]["device_model"] == "model~with separator"
            again = tmp_path / "netdaq-again.vna"
            save_legacy_vna(saved, again)
            assert load_legacy_vna(again).measurement.sample_rate == pytest.approx(last.sample_rate)
            from python_vna.analysis_data import load_legacy_analysis_dataset, load_continuous_recording
            assert load_legacy_analysis_dataset(output).sample_rate == pytest.approx(last.sample_rate)
            clock = [0.0]
            writer = ContinuousDatWriter(tmp_path / "record", cfg,
                                         channel_names=[c.name for c in cfg.ai_channels],
                                         device_name=backend.device_name, software_version="test",
                                         segment_seconds=.004, storage_format="binary",
                                         monotonic_fn=lambda: clock[0])
            writer.start()
            try:
                for _ in range(5):
                    clock[0] += .01
                    writer.write_frame(backend.read_frame())
            finally:
                writer.close(completed=True)
            manifest = json.loads((tmp_path / "record" / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["total_samples"] == 40
            assert len(manifest["segments"]) == 5
            assert manifest["sample_rate"] == pytest.approx(last.sample_rate)
            assert load_continuous_recording(tmp_path / "record" / "manifest.json").sample_rate == pytest.approx(last.sample_rate)
        finally:
            controller.close()


def test_native_extension_does_not_change_ni_legacy_files(tmp_path):
    from scipy.io import loadmat
    from python_vna.models import SavedSession
    target = tmp_path / "ordinary.vna"
    save_legacy_vna(SavedSession(config=default_session_config()), target)
    assert "PythonVNAAcquisition_json" not in loadmat(target)


def test_unacknowledged_stop_latches_safety_gate_and_releases_sockets(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        backend = NetDaqBackend(emulator.settings(path))
        backend.configure(session())
        backend.start()
        emulator.reject_stop = True
        with pytest.raises(DaqBackendError, match="not acknowledged"):
            backend.stop()
        assert backend.stop_uncertain and not backend.ready
        assert backend._stream is None and backend._control is None
        backend.close()
        with pytest.raises(DaqBackendError, match="停止未确认"):
            backend.configure(session())


def test_iepe_mode_is_device_owned_and_nonwritable_type_never_uses_c(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        data = json.loads(path.read_text())
        entry = data["channels"][0]
        # Synthetic type code ONLY; the real vendor enum is not inferred here.
        entry.update(channel_type=3, input_mode="iepe", iepe_current_ma=2.0)
        entry["options"][0].pop("coupling_index")
        entry["options"][0].pop("gain_index")
        entry["options"][0]["configuration_hex"] = "aa00"
        path.write_text(json.dumps(data))
        emulator.channel_types[1] = 3
        backend = NetDaqBackend(emulator.settings(path))
        cfg = session()
        with pytest.raises(DaqBackendError, match="read-only channel mode"):
            backend.configure(cfg)
        assert emulator.commands == []
        cfg.ai_channels[0].iepe_enabled = True
        cfg.ai_channels[0].iepe_current_ma = 2.0
        backend.configure(cfg)
        try:
            backend.start()
            assert backend.read_frame().data.shape == (4, 8)
        finally:
            backend.close()
        writes = [payload for command, payload in emulator.commands if command == 0x43]
        assert writes and all(payload[0] != 1 for payload in writes)


def test_channel_type_mismatch_aborts_before_any_configuration_write(tmp_path):
    with Emulator() as emulator:
        path = emulator.profile(tmp_path / "device.json")
        emulator.channel_types[4] = 8
        backend = NetDaqBackend(emulator.settings(path))
        backend.configure(session())
        try:
            with pytest.raises(DaqBackendError, match="reported channel type changed"):
                backend.start()
        finally:
            backend.close()
        assert all(command in {0x44, 0x41} for command, payload in emulator.commands)
