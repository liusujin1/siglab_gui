"""LAN and Tailscale discovery for the shared Communication Server.

Discovery is deliberately separate from the length-prefixed TCP protocol.  It
uses small, bounded UDP JSON datagrams and never advertises access tokens or a
client-supplied connect address: clients connect back to the datagram source.
"""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from python_samba.commserver.protocol import (
    PROTOCOL_VERSION,
    default_data_dir,
    format_endpoint,
)

DISCOVERY_VERSION = 1
DISCOVERY_PORT = 47620
DISCOVERY_MAGIC = "python-samba-commserver"
MAX_DISCOVERY_BYTES = 4096

_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class DiscoveryMessageError(ValueError):
    """A discovery datagram is malformed or unsupported."""


def _ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return None
    return address if isinstance(address, ipaddress.IPv4Address) else None


def is_tailscale_host(host: str) -> bool:
    address = _ipv4(host)
    return bool(address and address in _TAILSCALE_NETWORK)


def is_trusted_peer_host(host: str) -> bool:
    """Return whether a peer is local, RFC1918 LAN, or Tailscale IPv4."""

    address = _ipv4(host)
    if address is None:
        return False
    return bool(
        address.is_loopback
        or address in _TAILSCALE_NETWORK
        or any(address in network for network in _RFC1918_NETWORKS)
    )


def encode_discovery_message(message: dict[str, object]) -> bytes:
    try:
        raw = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DiscoveryMessageError(f"discovery message is not JSON serializable: {exc}") from exc
    if not raw or len(raw) > MAX_DISCOVERY_BYTES:
        raise DiscoveryMessageError(
            f"discovery message length {len(raw)} exceeds {MAX_DISCOVERY_BYTES}"
        )
    return raw


def decode_discovery_message(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_DISCOVERY_BYTES:
        raise DiscoveryMessageError("invalid discovery message length")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryMessageError("discovery message is not UTF-8 JSON") from exc
    if not isinstance(message, dict):
        raise DiscoveryMessageError("discovery message root must be an object")
    if message.get("magic") != DISCOVERY_MAGIC:
        raise DiscoveryMessageError("not a python_samba discovery message")
    if message.get("discovery_version") != DISCOVERY_VERSION:
        raise DiscoveryMessageError("unsupported discovery protocol version")
    kind = message.get("type")
    if kind not in {"query", "service"}:
        raise DiscoveryMessageError("unknown discovery message type")
    nonce = message.get("nonce")
    if nonce is not None and (not isinstance(nonce, str) or len(nonce) > 128):
        raise DiscoveryMessageError("invalid discovery nonce")
    return message


def _query_message(nonce: str) -> dict[str, object]:
    return {
        "magic": DISCOVERY_MAGIC,
        "discovery_version": DISCOVERY_VERSION,
        "type": "query",
        "nonce": nonce,
    }


def _broadcast_addresses() -> set[str]:
    """Return per-interface broadcasts, with a dependency-free fallback."""

    targets = {"255.255.255.255"}
    try:
        import psutil  # type: ignore[import-not-found]

        stats = psutil.net_if_stats()
        for interface, addresses in psutil.net_if_addrs().items():
            if interface in stats and not stats[interface].isup:
                continue
            for address in addresses:
                if address.family == socket.AF_INET and address.broadcast:
                    value = str(address.broadcast)
                    if value != "127.255.255.255":
                        targets.add(value)
    except (ImportError, OSError):
        pass
    return targets


def parse_tailscale_peer_ips(payload: str | bytes | dict[str, object]) -> list[str]:
    """Extract online Tailscale IPv4 peers from ``tailscale status --json``."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        parsed = json.loads(payload)
    else:
        parsed = payload
    if not isinstance(parsed, dict):
        return []
    peers = parsed.get("Peer")
    if not isinstance(peers, dict):
        return []
    result: set[str] = set()
    for value in peers.values():
        if not isinstance(value, dict) or not value.get("Online"):
            continue
        addresses = value.get("TailscaleIPs")
        if not isinstance(addresses, list):
            continue
        for candidate in addresses:
            text = str(candidate)
            if is_tailscale_host(text):
                result.add(text)
    return sorted(result, key=lambda item: int(ipaddress.ip_address(item)))


def _tailscale_executable() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
        if candidate.exists():
            return str(candidate)
    return None


def tailscale_peer_ips(timeout: float = 2.0) -> list[str]:
    executable = _tailscale_executable()
    if not executable:
        return []
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": max(0.1, float(timeout)),
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run([executable, "status", "--json"], **kwargs)  # noqa: S603
        if completed.returncode != 0:
            return []
        return parse_tailscale_peer_ips(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return []


def load_or_create_server_id(path: str | Path | None = None) -> str:
    """Return the stable discovery identity for this Windows/user profile."""

    identity_file = Path(path) if path else default_data_dir() / "communication_server.id"
    try:
        value = identity_file.read_text(encoding="utf-8").strip()
        uuid.UUID(value)
        return value
    except (FileNotFoundError, OSError, ValueError):
        value = str(uuid.uuid4())
        identity_file.parent.mkdir(parents=True, exist_ok=True)
        identity_file.write_text(value + "\n", encoding="utf-8")
        return value


@dataclass(frozen=True, slots=True)
class DiscoveredServer:
    server_id: str
    name: str
    hostname: str
    host: str
    tcp_port: int
    serial_port: str | None
    baudrate: int | None
    state: str
    client_count: int
    auth_mode: str
    protocol: int
    network: str
    latency_ms: float
    last_seen: float
    addresses: tuple[str, ...] = ()
    error: str | None = None

    @property
    def endpoint(self) -> str:
        return format_endpoint(self.host, self.tcp_port)

    @property
    def ready(self) -> bool:
        return bool(
            self.protocol == PROTOCOL_VERSION
            and self.serial_port
            and self.baudrate
            and self.state in {"ready", "connected"}
        )


def _candidate_from_message(
    message: dict[str, object], source_host: str, latency_ms: float
) -> DiscoveredServer:
    try:
        tcp_port = int(message.get("tcp_port") or 0)
        protocol = int(message.get("protocol") or 0)
        baudrate_value = message.get("baudrate")
        baudrate = int(baudrate_value) if baudrate_value is not None else None
        client_count = int(message.get("client_count") or 0)
    except (TypeError, ValueError) as exc:
        raise DiscoveryMessageError("invalid numeric service metadata") from exc
    server_id = str(message.get("server_id") or "").strip()
    if not server_id or len(server_id) > 128:
        raise DiscoveryMessageError("invalid server id")
    if not 1 <= tcp_port <= 65535:
        raise DiscoveryMessageError("invalid service TCP port")
    serial_value = message.get("serial_port")
    serial_port = str(serial_value).strip() if serial_value else None
    network = "tailscale" if is_tailscale_host(source_host) else "lan"
    endpoint = format_endpoint(source_host, tcp_port)
    return DiscoveredServer(
        server_id=server_id,
        name=str(message.get("name") or "SAMBA Controller")[:128],
        hostname=str(message.get("hostname") or source_host)[:128],
        host=source_host,
        tcp_port=tcp_port,
        serial_port=serial_port,
        baudrate=baudrate,
        state=str(message.get("state") or "unknown")[:32],
        client_count=max(0, client_count),
        auth_mode=str(message.get("auth_mode") or "token")[:32],
        protocol=protocol,
        network=network,
        latency_ms=max(0.0, latency_ms),
        last_seen=time.time(),
        addresses=(endpoint,),
        error=str(message.get("error"))[:256] if message.get("error") else None,
    )


def _merge_server(
    current: DiscoveredServer | None, candidate: DiscoveredServer
) -> DiscoveredServer:
    if current is None:
        return candidate
    all_addresses = tuple(sorted(set(current.addresses + candidate.addresses)))
    current_rank = (0 if current.network == "lan" else 1, current.latency_ms)
    candidate_rank = (0 if candidate.network == "lan" else 1, candidate.latency_ms)
    preferred = candidate if candidate_rank < current_rank else current
    return replace(preferred, addresses=all_addresses, last_seen=max(current.last_seen, candidate.last_seen))


class DiscoveryClient:
    """Scan LAN broadcasts and online Tailscale peers for active servers."""

    def __init__(
        self,
        *,
        discovery_port: int = DISCOVERY_PORT,
        include_tailscale: bool = True,
        direct_hosts: Iterable[str] = (),
    ) -> None:
        self.discovery_port = int(discovery_port)
        self.include_tailscale = bool(include_tailscale)
        self.direct_hosts = tuple(str(item) for item in direct_hosts)

    def scan(
        self,
        timeout: float = 1.5,
        *,
        on_result: Callable[[DiscoveredServer], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> list[DiscoveredServer]:
        duration = max(0.05, float(timeout))
        nonce = uuid.uuid4().hex
        query = encode_discovery_message(_query_message(nonce))
        found: dict[str, DiscoveredServer] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", 0))
            targets = set(self.direct_hosts)
            targets.update(_broadcast_addresses())
            if self.include_tailscale:
                targets.update(tailscale_peer_ips(timeout=min(2.0, max(0.5, duration))))
            started = time.monotonic()
            deadline = started + duration
            for host in targets:
                try:
                    sock.sendto(query, (host, self.discovery_port))
                except OSError:
                    continue
            while time.monotonic() < deadline and not (cancel and cancel.is_set()):
                remaining = deadline - time.monotonic()
                sock.settimeout(max(0.01, min(0.15, remaining)))
                try:
                    raw, peer = sock.recvfrom(MAX_DISCOVERY_BYTES + 1)
                except socket.timeout:
                    continue
                except OSError:
                    break
                source_host = str(peer[0])
                if not is_trusted_peer_host(source_host):
                    continue
                try:
                    message = decode_discovery_message(raw)
                    if message.get("type") != "service":
                        continue
                    response_nonce = message.get("nonce")
                    if response_nonce and response_nonce != nonce:
                        continue
                    candidate = _candidate_from_message(
                        message, source_host, (time.monotonic() - started) * 1000.0
                    )
                except DiscoveryMessageError:
                    continue
                merged = _merge_server(found.get(candidate.server_id), candidate)
                found[candidate.server_id] = merged
                if on_result:
                    on_result(merged)
        finally:
            sock.close()
        return sorted(found.values(), key=lambda item: (item.name.casefold(), item.hostname.casefold()))


class DiscoveryAnnouncer:
    """Periodically advertise a Communication Server and answer probes."""

    def __init__(
        self,
        status_provider: Callable[[], dict[str, object]],
        *,
        server_id: str,
        name: str | Callable[[], str],
        tcp_port: int = 47619,
        discovery_port: int = DISCOVERY_PORT,
        auth_mode: str = "trusted-network",
        interval: float = 2.0,
    ) -> None:
        self.status_provider = status_provider
        self.server_id = str(server_id)
        self.name = name
        self.tcp_port = int(tcp_port)
        self.discovery_port = int(discovery_port)
        self.auth_mode = str(auth_mode)
        self.interval = max(0.2, float(interval))
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._ready.clear()
        self.last_error = None
        self._thread = threading.Thread(
            target=self._run, name="CommServer-Discovery", daemon=True
        )
        self._thread.start()
        self._ready.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _service_message(self, nonce: str | None = None) -> dict[str, object]:
        status = self.status_provider()
        serial = status.get("serial")
        serial = serial if isinstance(serial, dict) else {}
        serial_port = serial.get("port")
        baudrate = serial.get("baudrate")
        error = status.get("last_error")
        if not serial_port:
            state = "no_serial"
        elif error and not serial.get("open"):
            state = "error"
        elif serial.get("open"):
            state = "connected"
        else:
            state = "ready"
        message: dict[str, object] = {
            "magic": DISCOVERY_MAGIC,
            "discovery_version": DISCOVERY_VERSION,
            "type": "service",
            "server_id": self.server_id,
            "name": self.name() if callable(self.name) else self.name,
            "hostname": platform.node() or socket.gethostname(),
            "protocol": PROTOCOL_VERSION,
            "tcp_port": self.tcp_port,
            "auth_mode": self.auth_mode,
            "serial_port": serial_port,
            "baudrate": baudrate,
            "state": state,
            "client_count": int(status.get("client_count") or 0),
            "error": str(error)[:256] if error else None,
        }
        if nonce:
            message["nonce"] = nonce
        return message

    def _send_broadcasts(self, sock: socket.socket) -> None:
        raw = encode_discovery_message(self._service_message())
        for host in _broadcast_addresses():
            try:
                sock.sendto(raw, (host, self.discovery_port))
            except OSError:
                continue

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind(("0.0.0.0", self.discovery_port))
            except OSError as exc:
                self.last_error = str(exc)
                return
            self._ready.set()
            sock.settimeout(0.2)
            next_announce = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_announce:
                    self._send_broadcasts(sock)
                    next_announce = now + self.interval
                try:
                    raw, peer = sock.recvfrom(MAX_DISCOVERY_BYTES + 1)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not is_trusted_peer_host(str(peer[0])):
                    continue
                try:
                    query = decode_discovery_message(raw)
                except DiscoveryMessageError:
                    continue
                if query.get("type") != "query":
                    continue
                response = encode_discovery_message(
                    self._service_message(str(query.get("nonce") or ""))
                )
                try:
                    sock.sendto(response, peer)
                except OSError:
                    continue
        finally:
            self._ready.set()
            if self._socket is sock:
                self._socket = None
            try:
                sock.close()
            except OSError:
                pass


__all__ = [
    "DISCOVERY_MAGIC",
    "DISCOVERY_PORT",
    "DISCOVERY_VERSION",
    "DiscoveredServer",
    "DiscoveryAnnouncer",
    "DiscoveryClient",
    "DiscoveryMessageError",
    "decode_discovery_message",
    "encode_discovery_message",
    "is_tailscale_host",
    "is_trusted_peer_host",
    "load_or_create_server_id",
    "parse_tailscale_peer_ips",
    "tailscale_peer_ips",
]
