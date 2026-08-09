"""Length-prefixed JSON protocol used by the communication server."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import socket
import struct
from pathlib import Path

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47619
DEFAULT_ENDPOINT = f"{DEFAULT_HOST}:{DEFAULT_PORT}"
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_HEADER = struct.Struct("!I")


class ProtocolMessageError(RuntimeError):
    """Invalid or incomplete communication-server message."""


def parse_endpoint(value: str) -> tuple[str, int]:
    text = str(value).strip()
    if not text:
        raise ValueError("server endpoint must not be empty")
    if text.startswith("["):
        end = text.find("]")
        if end < 0 or end + 1 >= len(text) or text[end + 1] != ":":
            raise ValueError(f"invalid server endpoint: {value!r}")
        host, port_text = text[1:end], text[end + 2 :]
    else:
        if ":" not in text:
            raise ValueError("server endpoint must be HOST:PORT")
        host, port_text = text.rsplit(":", 1)
    port = int(port_text)
    if not host or not 1 <= port <= 65535:
        raise ValueError(f"invalid server endpoint: {value!r}")
    return host, port


def format_endpoint(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            return all(
                ipaddress.ip_address(item[4][0]).is_loopback
                for item in socket.getaddrinfo(host, None)
            )
        except (OSError, ValueError):
            return False


def is_allowed_listen_host(host: str) -> bool:
    """Allow loopback or a concrete Tailscale CGNAT address only."""
    if is_loopback_host(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    return isinstance(address, ipaddress.IPv4Address) and address in tailscale


def encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: object, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise ProtocolMessageError(f"{field} must be a base64 string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ProtocolMessageError(f"{field} is not valid base64") from exc


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        try:
            chunk = sock.recv(length - len(chunks))
        except socket.timeout as exc:
            raise TimeoutError("timeout receiving communication-server message") from exc
        except OSError as exc:
            raise ConnectionError(f"communication-server receive failed: {exc}") from exc
        if not chunk:
            raise EOFError("communication-server connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_message(
    sock: socket.socket, *, max_message_bytes: int = MAX_MESSAGE_BYTES
) -> dict[str, object]:
    header = _recv_exact(sock, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length <= 0 or length > int(max_message_bytes):
        raise ProtocolMessageError(
            f"invalid message length {length}; limit is {max_message_bytes}"
        )
    raw = _recv_exact(sock, length)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolMessageError("message is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolMessageError("message root must be an object")
    return value


def send_message(
    sock: socket.socket,
    value: dict[str, object],
    *,
    max_message_bytes: int = MAX_MESSAGE_BYTES,
) -> None:
    try:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolMessageError(f"message is not JSON serializable: {exc}") from exc
    if not raw or len(raw) > int(max_message_bytes):
        raise ProtocolMessageError(
            f"message length {len(raw)} exceeds limit {max_message_bytes}"
        )
    try:
        sock.sendall(_HEADER.pack(len(raw)) + raw)
    except OSError as exc:
        raise ConnectionError(f"communication-server send failed: {exc}") from exc


def default_data_dir() -> Path:
    import os

    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "python_samba"
    return Path.home() / ".python_samba"


def default_token_file() -> Path:
    return default_data_dir() / "communication_server.token"


def default_log_file() -> Path:
    return default_data_dir() / "communication_server.log"
