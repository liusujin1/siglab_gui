from __future__ import annotations

import socket
import time
from types import SimpleNamespace

import pytest

from python_samba.commserver.discovery import (
    DISCOVERY_MAGIC,
    DISCOVERY_VERSION,
    DiscoveryAnnouncer,
    DiscoveryClient,
    DiscoveryMessageError,
    _candidate_from_message,
    decode_discovery_message,
    encode_discovery_message,
    is_trusted_peer_host,
    parse_tailscale_peer_ips,
)
from python_samba.commserver.protocol import PROTOCOL_VERSION
from python_samba.commserver.server import CommunicationServer, ServerConfigError
from python_samba.services.session import open_comm_server
from python_samba.transport.mock import MockTransport


def _service_message(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "magic": DISCOVERY_MAGIC,
        "discovery_version": DISCOVERY_VERSION,
        "type": "service",
        "server_id": "server-1",
        "name": "Lab Controller",
        "hostname": "test-host",
        "protocol": PROTOCOL_VERSION,
        "tcp_port": 47619,
        "auth_mode": "trusted-network",
        "serial_port": "COM7",
        "baudrate": 57600,
        "state": "ready",
        "client_count": 2,
        "error": None,
    }
    value.update(overrides)
    return value


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def test_discovery_message_roundtrip_and_limits() -> None:
    message = _service_message(name="控制器")
    assert decode_discovery_message(encode_discovery_message(message)) == message
    with pytest.raises(DiscoveryMessageError, match="length"):
        decode_discovery_message(b"x" * 5000)
    with pytest.raises(DiscoveryMessageError, match="unsupported"):
        decode_discovery_message(
            encode_discovery_message({**message, "discovery_version": 999})
        )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("172.16.4.5", True),
        ("192.168.3.4", True),
        ("100.64.0.42", True),
        ("8.8.8.8", False),
        ("169.254.1.2", False),
    ],
)
def test_trusted_peer_boundaries(host: str, expected: bool) -> None:
    assert is_trusted_peer_host(host) is expected


def test_tailscale_status_parser_uses_online_ipv4_peers_only() -> None:
    payload = {
        "Peer": {
            "one": {"Online": True, "TailscaleIPs": ["100.64.0.42", "fd7a::1"]},
            "two": {"Online": False, "TailscaleIPs": ["100.70.0.2"]},
            "three": {"Online": True, "TailscaleIPs": ["192.168.1.5"]},
        }
    }
    assert parse_tailscale_peer_ips(payload) == ["100.64.0.42"]


def test_tailscale_peer_command_returns_parsed_list(monkeypatch) -> None:
    from python_samba.commserver import discovery

    payload = {
        "Peer": {
            "remote": {"Online": True, "TailscaleIPs": ["100.64.0.42"]}
        }
    }
    monkeypatch.setattr(discovery, "_tailscale_executable", lambda: "tailscale.exe")
    monkeypatch.setattr(
        discovery.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=__import__("json").dumps(payload)
        ),
    )
    assert discovery.tailscale_peer_ips() == ["100.64.0.42"]


def test_client_uses_datagram_source_not_advertised_address() -> None:
    candidate = _candidate_from_message(
        _service_message(host="8.8.8.8", endpoint="8.8.8.8:9"),
        "192.168.1.44",
        12.5,
    )
    assert candidate.host == "192.168.1.44"
    assert candidate.endpoint == "192.168.1.44:47619"


def test_announcer_and_client_unicast_roundtrip() -> None:
    port = _free_udp_port()
    status = {
        "serial": {"open": False, "port": "COM7", "baudrate": 57600},
        "client_count": 0,
        "last_error": None,
    }
    announcer = DiscoveryAnnouncer(
        lambda: status,
        server_id="server-roundtrip",
        name="Roundtrip Controller",
        tcp_port=47619,
        discovery_port=port,
        interval=30.0,
    )
    announcer.start()
    try:
        deadline = time.monotonic() + 1.0
        while not announcer.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
        servers = DiscoveryClient(
            discovery_port=port,
            include_tailscale=False,
            direct_hosts=["127.0.0.1"],
        ).scan(0.5)
    finally:
        announcer.stop()
    assert len(servers) == 1
    server = servers[0]
    assert server.server_id == "server-roundtrip"
    assert server.serial_port == "COM7"
    assert server.ready


def test_wildcard_listener_requires_explicit_network_auth(tmp_path) -> None:
    with pytest.raises(ServerConfigError, match="requires token or trusted-network"):
        CommunicationServer(("0.0.0.0", 0), log_file=tmp_path / "server.log")
    trusted = CommunicationServer(
        ("0.0.0.0", 0),
        auth_mode="trusted-network",
        log_file=tmp_path / "trusted.log",
    )
    token = CommunicationServer(
        ("0.0.0.0", 0),
        auth_mode="token",
        token="secret",
        log_file=tmp_path / "token.log",
    )
    assert trusted.auth_mode == "trusted-network"
    assert token.auth_mode == "token"


def test_discover_then_connect_without_token_in_trusted_mode(tmp_path) -> None:
    udp_port = _free_udp_port()
    server = CommunicationServer(
        ("0.0.0.0", 0),
        auth_mode="trusted-network",
        preferred_port="COM1",
        transport_factory=lambda _config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()
    announcer = DiscoveryAnnouncer(
        server.status,
        server_id="connectable-server",
        name="Connectable Controller",
        tcp_port=server.addresses[0][1],
        discovery_port=udp_port,
    )
    announcer.start()
    session = None
    try:
        discovered = DiscoveryClient(
            discovery_port=udp_port,
            include_tailscale=False,
            direct_hosts=["127.0.0.1"],
        ).scan(0.5)
        assert len(discovered) == 1 and discovered[0].ready
        session = open_comm_server(
            "COM1",
            server=discovered[0].endpoint,
            auto_start=False,
            readonly=True,
        )
        version = session.open()
        assert str(version).startswith("V3.3")
        assert session.transport.status()["auth_mode"] == "trusted-network"
    finally:
        if session is not None:
            session.close()
        announcer.stop()
        server.stop()


def test_discover_cli_lists_service(monkeypatch, capsys) -> None:
    from python_samba import cli

    candidate = _candidate_from_message(_service_message(), "192.168.1.44", 4.0)

    class Client:
        def __init__(
            self,
            *,
            discovery_port: int,
            include_tailscale: bool,
            direct_hosts: list[str],
        ):
            assert include_tailscale is False
            assert discovery_port == 47620
            assert direct_hosts == []

        def scan(self, timeout: float):
            assert timeout == pytest.approx(0.25)
            return [candidate]

    monkeypatch.setattr(cli, "DiscoveryClient", Client)
    assert cli.main(["discover", "--timeout", "0.25", "--no-tailscale"]) == 0
    output = capsys.readouterr().out
    assert "Lab Controller" in output
    assert "192.168.1.44:47619" in output
    assert "COM7 @ 57600" in output


def test_firewall_rules_are_scoped_to_lan_and_tailscale() -> None:
    from python_samba.commserver.firewall import _firewall_install_script

    script = _firewall_install_script()
    assert "LocalSubnet" in script
    assert "100.64.0.0/10" in script
    assert "LocalPort 47619" in script
    assert "LocalPort 47620" in script
