from __future__ import annotations

import json
import socket
import struct
import threading
import time

import pytest

from python_samba.commserver.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolMessageError,
    recv_message,
    send_message,
)
from python_samba.commserver.server import CommunicationServer, ServerAlreadyRunning
from python_samba.services.session import open_comm_server
from python_samba.transport.comm_server import (
    CommServerConfig,
    CommServerTransport,
    request_server_shutdown,
)
from python_samba.transport.mock import MockTransport
from python_samba.transport.serial_port import SerialConfig, Transport, TransportError


class RecordingTransport(Transport):
    def __init__(self, config: SerialConfig, *, binary: bytes | None = None) -> None:
        self.config = config
        self.binary = binary
        self.opened = False
        self.requests: list[bytes] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    @property
    def is_open(self) -> bool:
        return self.opened

    def write(self, data: bytes) -> None:
        raise AssertionError("server must use atomic exchange")

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        raise AssertionError("server must use atomic exchange")

    def exchange(
        self, request: bytes, terminator: bytes = b"\r", timeout: float = 2.0
    ) -> bytes:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.0002)
            self.requests.append(bytes(request))
            return self.binary if self.binary is not None else b"reply:" + request
        finally:
            with self.lock:
                self.active -= 1


class FailingTransport(RecordingTransport):
    def exchange(
        self, request: bytes, terminator: bytes = b"\r", timeout: float = 2.0
    ) -> bytes:
        self.requests.append(bytes(request))
        raise TransportError("simulated physical disconnect")


def _endpoint(server: CommunicationServer) -> str:
    host, port = server.addresses[0]
    return f"{host}:{port}"


def _client(
    server: CommunicationServer,
    *,
    port: str = "COM1",
    token: str | None = None,
    name: str = "test-client",
) -> CommServerTransport:
    return CommServerTransport(
        CommServerConfig(
            port=port,
            endpoint=_endpoint(server),
            token=token,
            auto_start=False,
            client_name=name,
            connect_timeout=1.0,
        )
    )


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_shutdown_request_does_not_attach_serial() -> None:
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> RecordingTransport:
        transport = RecordingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        listen=[("127.0.0.1", 0)], transport_factory=factory
    )
    server.start()
    endpoint = _endpoint(server)
    assert server.is_running

    request_server_shutdown(endpoint, timeout=1.0)

    _wait_until(lambda: not server.addresses)
    assert not server.is_running
    assert transports == []


def test_protocol_handles_fragmented_and_invalid_messages() -> None:
    left, right = socket.socketpair()
    try:
        payload = json.dumps({"op": "status", "text": "中文"}).encode("utf-8")
        packet = struct.pack("!I", len(payload)) + payload
        for byte in packet:
            left.send(bytes([byte]))
        assert recv_message(right) == {"op": "status", "text": "中文"}

        left.sendall(struct.pack("!I", MAX_MESSAGE_BYTES + 1))
        with pytest.raises(ProtocolMessageError, match="invalid message length"):
            recv_message(right)
    finally:
        left.close()
        right.close()

    left, right = socket.socketpair()
    try:
        bad = b"not-json"
        left.sendall(struct.pack("!I", len(bad)) + bad)
        with pytest.raises(ProtocolMessageError, match="valid UTF-8 JSON"):
            recv_message(right)
    finally:
        left.close()
        right.close()


def test_protocol_rejects_oversize_outgoing_message() -> None:
    left, right = socket.socketpair()
    try:
        with pytest.raises(ProtocolMessageError, match="exceeds limit"):
            send_message(left, {"blob": "x" * 100}, max_message_bytes=16)
    finally:
        left.close()
        right.close()


def test_binary_exchange_preserves_nul_and_carriage_return(tmp_path) -> None:
    binary = b"\x00start\rinside\nend\x00\r"
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = RecordingTransport(config, binary=binary)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    client = _client(server)
    try:
        client.open()
        assert client._socket is not None
        assert client._socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        assert client._socket.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        assert client.exchange(b"DGTBB-binary-request", b"\r", 1.0) == binary
        assert transports[0].requests == [b"DGTBB-binary-request"]
    finally:
        client.close()
        server.stop()


def test_batch_exchange_uses_one_rpc_and_preserves_response_order(tmp_path) -> None:
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = RecordingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    client = _client(server)
    try:
        client.open()
        assert "exchange_batch" in client._features
        requests = [b"first\x00", b"second\rinside", b"third"]
        rpc_ops: list[str] = []
        original_rpc = client._rpc

        def counting_rpc(op: str, **kwargs):
            rpc_ops.append(op)
            return original_rpc(op, **kwargs)

        client._rpc = counting_rpc  # type: ignore[method-assign]
        responses = client.exchange_many(
            [(request, b"\r", 1.0) for request in requests]
        )

        assert responses == [b"reply:" + request for request in requests]
        assert transports[0].requests == requests
        assert rpc_ops == ["exchange_batch"]
        assert client.status()["features"] == ["exchange_batch"]
    finally:
        client.close()
        server.stop()


def test_batch_items_are_contiguous_in_global_fifo(tmp_path) -> None:
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = RecordingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    batch_client = _client(server, name="batch")
    other_client = _client(server, name="other")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    batch_requests = [f"B-{index}".encode("ascii") for index in range(60)]

    def run_batch() -> None:
        try:
            barrier.wait()
            responses = batch_client.exchange_many(
                [(request, b"\r", 2.0) for request in batch_requests]
            )
            assert responses == [b"reply:" + request for request in batch_requests]
        except BaseException as exc:
            errors.append(exc)

    def run_singles() -> None:
        try:
            barrier.wait()
            for index in range(20):
                request = f"S-{index}".encode("ascii")
                assert other_client.exchange(request) == b"reply:" + request
        except BaseException as exc:
            errors.append(exc)

    try:
        batch_client.open()
        other_client.open()
        threads = [threading.Thread(target=run_batch), threading.Thread(target=run_singles)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors

        positions = [
            index
            for index, request in enumerate(transports[0].requests)
            if request.startswith(b"B-")
        ]
        assert len(positions) == len(batch_requests)
        assert positions == list(range(positions[0], positions[0] + len(batch_requests)))
        assert transports[0].requests[positions[0] : positions[-1] + 1] == batch_requests
    finally:
        batch_client.close()
        other_client.close()
        server.stop()


def test_physical_failure_aborts_remaining_batch_items(tmp_path) -> None:
    transports: list[FailingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = FailingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0), transport_factory=factory, log_file=tmp_path / "server.log"
    ).start()
    client = _client(server)
    try:
        client.open()
        with pytest.raises(TransportError, match="simulated physical disconnect"):
            client.exchange_many(
                [(f"request-{index}".encode(), b"\r", 1.0) for index in range(40)]
            )
        assert sum(len(transport.requests) for transport in transports) == 1
        assert server.status()["completed_requests"] == 40
    finally:
        client.close()
        server.stop()


def test_new_client_falls_back_to_single_exchange_for_legacy_server() -> None:
    client = CommServerTransport(
        CommServerConfig(port="COM1", endpoint="127.0.0.1:1", auto_start=False)
    )
    calls: list[bytes] = []

    def fake_exchange(
        request: bytes, terminator: bytes = b"\r", timeout: float = 2.0
    ) -> bytes:
        calls.append(request)
        return b"legacy:" + request

    client.exchange = fake_exchange  # type: ignore[method-assign]
    assert client.exchange_many(
        [(b"one", b"\r", 1.0), (b"two", b"\r", 1.0)]
    ) == [b"legacy:one", b"legacy:two"]
    assert calls == [b"one", b"two"]


def test_authentication_and_serial_configuration_conflict(tmp_path) -> None:
    server = CommunicationServer(
        ("127.0.0.1", 0),
        token="correct-token",
        force_token=True,
        transport_factory=lambda config: RecordingTransport(config),
        log_file=tmp_path / "server.log",
    ).start()
    wrong = _client(server, token="wrong-token")
    first = _client(server, token="correct-token", port="COM1", name="first")
    conflict = _client(server, token="correct-token", port="COM2", name="conflict")
    try:
        with pytest.raises(TransportError, match="invalid access token"):
            wrong.open()
        first.open()
        with pytest.raises(TransportError, match="already configured as COM1"):
            conflict.open()
        status = first.status()
        assert status["attached_count"] == 1
        assert status["serial"] == {"open": True, "port": "COM1", "baudrate": 57600}
    finally:
        wrong.close()
        conflict.close()
        first.close()
        server.stop()


def test_two_clients_hundreds_of_requests_no_crossed_responses(tmp_path) -> None:
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = RecordingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    first = _client(server, name="SAMBA")
    second = _client(server, name="SIDMAT")
    errors: list[str] = []

    def run(client: CommServerTransport, prefix: bytes) -> None:
        for index in range(250):
            request = prefix + str(index).encode("ascii")
            response = client.exchange(request, timeout=2.0)
            if response != b"reply:" + request:
                errors.append(f"{request!r} -> {response!r}")

    try:
        first.open()
        second.open()
        threads = [
            threading.Thread(target=run, args=(first, b"S-")),
            threading.Thread(target=run, args=(second, b"D-")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20.0)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        assert len(transports[0].requests) == 500
        assert len(set(transports[0].requests)) == 500
        assert transports[0].max_active == 1

        first.close()
        assert second.exchange(b"still-alive") == b"reply:still-alive"
        assert second.status()["attached_count"] == 1
    finally:
        first.close()
        second.close()
        server.stop()


def test_serial_failure_is_not_retried_and_next_request_reopens(tmp_path) -> None:
    created: list[Transport] = []

    class FailOnce(RecordingTransport):
        def exchange(self, request: bytes, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
            self.requests.append(request)
            raise TransportError("timeout waiting for hardware")

    def factory(config: SerialConfig) -> Transport:
        transport: Transport
        if not created:
            transport = FailOnce(config)
        else:
            transport = RecordingTransport(config)
        created.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    failed_client = _client(server)
    replacement = _client(server, name="replacement")
    try:
        failed_client.open()
        with pytest.raises(TransportError, match="timeout waiting for hardware"):
            failed_client.exchange(b"ACTION", timeout=0.1)
        assert isinstance(created[0], FailOnce)
        assert created[0].requests == [b"ACTION"]

        # The failed TCP client is invalidated, but a new client shares the
        # retained COM configuration and reopens the physical link once.
        replacement.open()
        assert replacement.exchange(b"READ") == b"reply:READ"
        assert len(created) == 2
    finally:
        failed_client.close()
        replacement.close()
        server.stop()


def test_crashed_clients_are_detached_and_last_crash_releases_serial(tmp_path) -> None:
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: RecordingTransport(config),
        log_file=tmp_path / "server.log",
    ).start()
    samba = _client(server, name="SAMBA")
    sidmat = _client(server, name="SIDMAT")
    try:
        samba.open()
        sidmat.open()
        assert server.status()["attached_count"] == 2

        # Simulate a process disappearing without sending the detach RPC.
        samba._drop_socket()
        _wait_until(lambda: server.status()["attached_count"] == 1)
        assert server.status()["serial"]["open"] is True
        assert sidmat.exchange(b"survives-peer-crash") == b"reply:survives-peer-crash"

        sidmat._drop_socket()
        _wait_until(lambda: server.status()["attached_count"] == 0)
        assert server.status()["serial"]["open"] is False
    finally:
        samba.close()
        sidmat.close()
        server.stop()


def test_restart_serial_reopens_once_for_attached_clients(tmp_path) -> None:
    transports: list[RecordingTransport] = []

    def factory(config: SerialConfig) -> Transport:
        transport = RecordingTransport(config)
        transports.append(transport)
        return transport

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=factory,
        log_file=tmp_path / "server.log",
    ).start()
    client = _client(server)
    try:
        client.open()
        assert len(transports) == 1 and transports[0].is_open
        status = client.restart_serial()
        assert len(transports) == 2
        assert transports[0].is_open is False
        assert transports[1].is_open is True
        assert status["attached_count"] == 1
        assert status["serial"]["open"] is True
        assert client.exchange(b"after-restart") == b"reply:after-restart"
    finally:
        client.close()
        server.stop()


def test_mock_controller_ascii_and_dgtbb_through_server(tmp_path) -> None:
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    samba = open_comm_server(
        "COM1", server=_endpoint(server), auto_start=False, readonly=False
    )
    sidmat = open_comm_server(
        "COM1",
        server=_endpoint(server),
        auto_start=False,
        client_name="python_sidmat",
        readonly=False,
    )
    try:
        assert str(samba.open()) == "V3.3.9"
        assert str(sidmat.open()) == "V3.3.9"
        assert samba.get_sample_frequency() == 2000.0
        values = sidmat.get_digital_trace_buffer_binary(0, 4)
        assert len(values) == 8
        assert values[:4] == pytest.approx([0.0, 8.0, 1.0, 9.0])
    finally:
        samba.close()
        sidmat.close()
        server.stop()


def test_page_snapshots_each_use_one_server_batch_rpc(tmp_path) -> None:
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    session = open_comm_server(
        "COM1", server=_endpoint(server), auto_start=False, readonly=True
    )
    try:
        session.open()
        transport = session.transport
        assert isinstance(transport, CommServerTransport)
        rpc_ops: list[str] = []
        original_rpc = transport._rpc

        def counting_rpc(op: str, **kwargs):
            rpc_ops.append(op)
            return original_rpc(op, **kwargs)

        transport._rpc = counting_rpc  # type: ignore[method-assign]
        velocity_keys = [(axis, stage) for axis in range(6) for stage in range(7)]
        position_keys = [(axis, stage) for axis in range(6) for stage in range(4)]
        pff_keys = (
            [(0, source, stage) for source in range(4) for stage in range(6)]
            + [(axis, 0, stage) for axis in range(3) for stage in range(6, 8)]
        )
        pneumatic_keys = [(axis, stage) for axis in range(3) for stage in range(4)]

        session.get_system_setting_snapshot()
        session.get_status_page_snapshot()
        session.get_monitor_page_snapshot(16)
        session.get_adc_dac_snapshot()
        session.get_motor_protection_snapshot(
            linear_offsets=False, include_power_supply=True
        )
        session.get_velocity_tuning_snapshot(velocity_keys)
        session.get_diagnostics_snapshot()
        session.get_position_tuning_snapshot(
            position_keys, proximity_count=6, include_cascaded=True
        )
        session.get_ff_filters(
            [(source, stage) for source in range(7) for stage in range(6)]
            + [(axis, stage) for axis in range(6) for stage in range(6, 8)]
        )
        session.get_ff_runtime_snapshot(7)
        session.get_pff_tuning_snapshot(pff_keys, source_count=4)
        session.get_pneumatic_page_snapshot(pneumatic_keys, include_ramp=True)
        session.get_live_refresh_snapshot(
            include_switch_conditions=True,
            include_axis_status=True,
            include_controller_config=True,
            proximity_count=6,
            include_motor=True,
            include_power_supply=True,
            include_pneumatic=True,
            monitor_count=16,
        )
        session.get_logging_workspace_snapshot(16)
        session.get_internal_logging_snapshot()

        assert rpc_ops == ["exchange_batch"] * 15
    finally:
        session.close()
        server.stop()


def test_trace_buffer_groups_use_one_server_rpc_per_group(tmp_path) -> None:
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    session = open_comm_server(
        "COM1", server=_endpoint(server), auto_start=False, readonly=False
    )
    try:
        session.open()
        transport = session.transport
        assert isinstance(transport, CommServerTransport)
        rpc_ops: list[str] = []
        original_rpc = transport._rpc

        def counting_rpc(op: str, **kwargs):
            rpc_ops.append(op)
            return original_rpc(op, **kwargs)

        transport._rpc = counting_rpc  # type: ignore[method-assign]
        text_chunks = session.get_digital_trace_buffers([0, 16, 32])
        binary_chunks = session.get_digital_trace_buffers_binary(
            [(0, 4), (4, 4), (8, 4)]
        )
        assert [len(values) for values in text_chunks] == [16, 16, 16]
        assert [len(values) for values in binary_chunks] == [8, 8, 8]
        assert rpc_ops == ["exchange_batch", "exchange_batch"]
    finally:
        session.close()
        server.stop()


def test_remote_timer_refresh_is_non_blocking_and_coalesced(tmp_path) -> None:
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.services.safety import SafetyGate
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    apply_all_patches(MainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    session = open_comm_server(
        "COM1", server=_endpoint(server), auto_start=False, readonly=False
    )
    session.open()
    window = MainWindow()
    window.session = session
    window.gate = SafetyGate(session)
    try:
        started = time.perf_counter()
        window._on_timer_tick()
        elapsed = time.perf_counter() - started
        assert elapsed < 0.2
        assert window._remote_live_refresh_inflight
        # A second overdue timer event is discarded rather than starting a
        # second worker against the same page snapshot.
        window._on_timer_tick()
        deadline = time.monotonic() + 5.0
        while window._remote_live_refresh_inflight and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert not window._remote_live_refresh_inflight
        live_reader = window._remote_live_session
        assert live_reader is not None and live_reader is not session
        assert live_reader.transport.status()["completed_requests"] >= 3
    finally:
        window.on_disconnect()
        window.close()
        app.processEvents()
        server.stop()


def test_listener_port_race_has_one_winner(tmp_path) -> None:
    first = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: RecordingTransport(config),
        log_file=tmp_path / "first.log",
    ).start()
    port = first.addresses[0][1]
    second = CommunicationServer(
        ("127.0.0.1", port),
        transport_factory=lambda config: RecordingTransport(config),
        log_file=tmp_path / "second.log",
    )
    try:
        with pytest.raises(ServerAlreadyRunning):
            second.start()
    finally:
        second.stop()
        first.stop()


def test_samba_cli_uses_server_backend(tmp_path, capsys) -> None:
    from python_samba.cli import main

    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    try:
        assert main(
            [
                "connect",
                "--backend",
                "server",
                "--server",
                _endpoint(server),
                "--no-auto-start",
                "--port",
                "COM1",
            ]
        ) == 0
        output = capsys.readouterr().out
        assert "backend : server" in output
        assert f"server  : {_endpoint(server)}" in output
        assert "firmware: V3.3.9" in output
    finally:
        server.stop()


def test_samba_gui_can_connect_through_server(tmp_path) -> None:
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings("python_samba", "SAMBA19xUI")
    keys = [
        "Connection/Backend",
        "Connection/Port",
        "Connection/Baudrate",
        "Connection/Server",
    ]
    saved = {
        key: settings.value(key) if settings.contains(key) else None for key in keys
    }
    server = CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    window = PatchedMainWindow()
    try:
        window.backend.setCurrentText("server")
        window.port.setText("COM1")
        window.server_endpoint.setText(_endpoint(server))
        window.on_connect()
        assert window.session is not None and window.session.connected
        assert window.session.info.backend == "server"
        assert window.session.info.server_endpoint == _endpoint(server)
        assert window._backend_connect.currentText() == "server"
        assert "Server" in window.loop_states.conn_lbl.text()
    finally:
        window.on_disconnect()
        window.close()
        server.stop()
        for key, value in saved.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        app.processEvents()
