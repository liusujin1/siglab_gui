"""Client transport for the shared python_samba Communication Server."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from python_samba.commserver.protocol import (
    DEFAULT_ENDPOINT,
    MAX_BATCH_ITEMS,
    PROTOCOL_VERSION,
    ProtocolMessageError,
    configure_low_latency_socket,
    decode_bytes,
    default_token_file,
    encode_bytes,
    is_loopback_host,
    parse_endpoint,
    recv_message,
    send_message,
)
from python_samba.transport.serial_port import Transport, TransportError


class _ServerUnavailable(TransportError):
    """The TCP listener is absent (the only error that permits auto-start)."""


@dataclass
class CommServerConfig:
    port: str
    baudrate: int = 57600
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = 5.0
    token_file: str | Path | None = None
    token: str | None = None
    auto_start: bool = True
    client_name: str = "python_samba"
    connect_timeout: float = 5.0


class CommServerTransport(Transport):
    """Binary-safe request/response relay with one outstanding RPC."""

    def __init__(self, config: CommServerConfig) -> None:
        self.config = config
        self._socket: socket.socket | None = None
        self._client_id: str | None = None
        self._features: frozenset[str] = frozenset()
        self._open = False
        self._rpc_lock = threading.RLock()
        self._next_id = 0
        self._pending_write: bytes | None = None
        self._instance_id = str(uuid.uuid4())

    @property
    def is_open(self) -> bool:
        return bool(self._open and self._socket is not None)

    def open(self) -> None:
        if self.is_open:
            return
        host, _ = parse_endpoint(self.config.endpoint)
        deadline = time.monotonic() + max(0.1, float(self.config.connect_timeout))
        started = False
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                self._connect_once()
                return
            except BaseException as exc:
                last_error = exc
                self._drop_socket()
                if (
                    isinstance(exc, _ServerUnavailable)
                    and not started
                    and self.config.auto_start
                    and is_loopback_host(host)
                ):
                    self._start_local_server()
                    started = True
                elif not started or not is_loopback_host(host):
                    break
                time.sleep(0.08)
        raise TransportError(
            f"connect communication server {self.config.endpoint} failed: {last_error}"
        ) from last_error

    def close(self) -> None:
        with self._rpc_lock:
            if self._socket is not None:
                try:
                    self._rpc("detach", rpc_timeout=2.0)
                except BaseException:
                    pass
            self._drop_socket()
            self._pending_write = None

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise TransportError("communication-server transport is not open")
        if self._pending_write is not None:
            raise TransportError("a communication-server request is already pending")
        self._pending_write = bytes(data)

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        request, self._pending_write = self._pending_write, None
        if request is None:
            raise TransportError("no communication-server request is pending")
        return self.exchange(request, terminator, timeout)

    def exchange(
        self,
        request: bytes,
        terminator: bytes = b"\r",
        timeout: float = 2.0,
    ) -> bytes:
        if not self.is_open:
            raise TransportError("communication-server transport is not open")
        if not request or not terminator:
            raise ValueError("request and terminator must not be empty")
        with self._rpc_lock:
            try:
                result = self._rpc(
                    "exchange",
                    rpc_timeout=max(30.0, float(timeout) + 30.0),
                    request=encode_bytes(bytes(request)),
                    terminator=encode_bytes(bytes(terminator)),
                    timeout=float(timeout),
                )
                if not isinstance(result, dict):
                    raise ProtocolMessageError("exchange result must be an object")
                return decode_bytes(result.get("response"), field="response")
            except BaseException as exc:
                # Never reconnect/retry here: the physical command may already
                # have executed.  A subsequent explicit open starts a new link.
                self._drop_socket()
                if isinstance(exc, TransportError):
                    raise
                raise TransportError(f"communication-server exchange failed: {exc}") from exc

    def exchange_many(
        self,
        requests: Sequence[tuple[bytes, bytes, float]],
    ) -> list[bytes]:
        """Relay a group of exchanges in one RPC when the server supports it."""
        items = list(requests)
        if not items:
            return []
        if len(items) > MAX_BATCH_ITEMS:
            raise ValueError(
                f"exchange batch has {len(items)} items; limit is {MAX_BATCH_ITEMS}"
            )
        if "exchange_batch" not in self._features:
            # Protocol-v1 servers built before batch support omit ``features``.
            # Keep new clients compatible with those running instances.
            return super().exchange_many(items)
        if not self.is_open:
            raise TransportError("communication-server transport is not open")

        payload: list[dict[str, object]] = []
        total_timeout = 0.0
        for request, terminator, timeout in items:
            if not request or not terminator:
                raise ValueError("request and terminator must not be empty")
            timeout_value = float(timeout)
            if not 0 < timeout_value <= 300.0:
                raise ValueError("timeout must be in (0, 300] seconds")
            total_timeout += timeout_value
            payload.append(
                {
                    "request": encode_bytes(bytes(request)),
                    "terminator": encode_bytes(bytes(terminator)),
                    "timeout": timeout_value,
                }
            )

        with self._rpc_lock:
            try:
                result = self._rpc(
                    "exchange_batch",
                    rpc_timeout=max(30.0, total_timeout + 30.0),
                    items=payload,
                )
                if not isinstance(result, dict):
                    raise ProtocolMessageError("exchange_batch result must be an object")
                result_items = result.get("items")
                if not isinstance(result_items, list):
                    raise ProtocolMessageError("exchange_batch items must be a list")
                if len(result_items) != len(items):
                    raise ProtocolMessageError(
                        "exchange_batch response count mismatch: "
                        f"expected {len(items)}, got {len(result_items)}"
                    )
                responses: list[bytes] = []
                for index, item in enumerate(result_items):
                    if not isinstance(item, dict):
                        raise ProtocolMessageError(
                            f"exchange_batch item {index} must be an object"
                        )
                    responses.append(
                        decode_bytes(item.get("response"), field=f"items[{index}].response")
                    )
                return responses
            except BaseException as exc:
                # As with a single exchange, never retry an ambiguous batch:
                # one or more physical commands may already have executed.
                self._drop_socket()
                if isinstance(exc, TransportError):
                    raise
                raise TransportError(
                    f"communication-server batch exchange failed: {exc}"
                ) from exc

    def status(self) -> dict[str, object]:
        result = self._rpc("status", rpc_timeout=5.0)
        if not isinstance(result, dict):
            raise TransportError("communication-server status is malformed")
        return result

    def restart_serial(self) -> dict[str, object]:
        result = self._rpc("restart_serial", rpc_timeout=10.0)
        if not isinstance(result, dict):
            raise TransportError("communication-server restart status is malformed")
        return result

    def shutdown_server(self) -> None:
        self._rpc("shutdown", rpc_timeout=5.0)
        self._drop_socket()

    def _connect_once(self) -> None:
        host, port = parse_endpoint(self.config.endpoint)
        try:
            sock = socket.create_connection(
                (host, port), timeout=max(0.1, float(self.config.connect_timeout))
            )
        except OSError as exc:
            raise _ServerUnavailable(str(exc)) from exc
        configure_low_latency_socket(sock)
        sock.settimeout(max(0.1, float(self.config.connect_timeout)))
        self._socket = sock
        hello = self._rpc(
            "hello",
            include_base=False,
            rpc_timeout=self.config.connect_timeout,
            protocol=PROTOCOL_VERSION,
            name=self.config.client_name,
            pid=os.getpid(),
            instance=self._instance_id,
            token=self._load_token(),
        )
        if not isinstance(hello, dict) or hello.get("protocol") != PROTOCOL_VERSION:
            raise TransportError("communication-server hello response is invalid")
        self._client_id = str(hello.get("client_id") or "")
        feature_values = hello.get("features", [])
        if isinstance(feature_values, list):
            self._features = frozenset(
                str(value) for value in feature_values if isinstance(value, str)
            )
        else:
            self._features = frozenset()
        self._rpc(
            "attach",
            rpc_timeout=max(5.0, float(self.config.timeout) + 1.0),
            port=str(self.config.port).strip(),
            baudrate=int(self.config.baudrate),
        )
        sock.settimeout(None)
        self._open = True

    def _rpc(
        self,
        op: str,
        *,
        include_base: bool = True,
        rpc_timeout: float = 5.0,
        **fields: object,
    ) -> object:
        with self._rpc_lock:
            sock = self._socket
            if sock is None:
                raise TransportError("communication-server socket is not connected")
            self._next_id += 1
            request_id = self._next_id
            message: dict[str, object] = {"id": request_id, "op": op, **fields}
            if include_base and self._client_id:
                message["client_id"] = self._client_id
            previous_timeout = sock.gettimeout()
            sock.settimeout(max(0.1, float(rpc_timeout)))
            try:
                send_message(sock, message)
                response = recv_message(sock)
            except BaseException as exc:
                raise TransportError(f"{op} RPC failed: {exc}") from exc
            finally:
                try:
                    sock.settimeout(previous_timeout)
                except OSError:
                    pass
            if response.get("id") != request_id:
                raise TransportError(
                    f"RPC response id mismatch: expected {request_id}, got {response.get('id')!r}"
                )
            if not response.get("ok"):
                error = response.get("error")
                if isinstance(error, dict):
                    kind = str(error.get("type") or "ServerError")
                    message_text = str(error.get("message") or "unknown server error")
                else:
                    kind, message_text = "ServerError", str(error)
                raise TransportError(f"{kind}: {message_text}")
            return response.get("result")

    def _load_token(self) -> str:
        if self.config.token is not None:
            return self.config.token
        path = Path(self.config.token_file) if self.config.token_file else default_token_file()
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def _drop_socket(self) -> None:
        sock, self._socket = self._socket, None
        self._open = False
        self._client_id = None
        self._features = frozenset()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _start_local_server(self) -> None:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe") if os.name == "nt" else python
        if not pythonw.exists():
            pythonw = python
        command = [
            str(pythonw),
            "-m",
            "python_samba.commserver.cli",
            "--tray",
            "--listen",
            self.config.endpoint,
        ]
        package_src = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        current_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = package_src + (os.pathsep + current_path if current_path else "")
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": env,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)  # noqa: S603
