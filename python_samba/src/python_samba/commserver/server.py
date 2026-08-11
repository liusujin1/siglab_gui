"""Single-owner serial server with a global FIFO request queue."""

from __future__ import annotations

import hmac
import itertools
import logging
import os
import queue
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from python_samba.commserver.discovery import is_trusted_peer_host
from python_samba.commserver.protocol import (
    MAX_BATCH_ITEMS,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    SERVER_FEATURES,
    configure_low_latency_socket,
    ProtocolMessageError,
    decode_bytes,
    default_log_file,
    default_token_file,
    encode_bytes,
    format_endpoint,
    is_allowed_listen_host,
    is_loopback_host,
    recv_message,
    send_message,
)
from python_samba.transport.serial_port import (
    SerialConfig,
    SerialTransport,
    Transport,
    TransportError,
)


class ServerAlreadyRunning(RuntimeError):
    """A listener already owns one of the requested endpoints."""


class ServerConfigError(RuntimeError):
    """Unsafe or conflicting server configuration."""


TransportFactory = Callable[[SerialConfig], Transport]


@dataclass
class _Client:
    client_id: str
    name: str
    pid: int | None
    peer: str
    connected_at: float = field(default_factory=time.time)
    attached: bool = False
    port: str | None = None
    baudrate: int | None = None


@dataclass
class _ExchangeTask:
    sequence: int
    client_id: str
    request: bytes
    terminator: bytes
    timeout: float
    enqueued_at: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    response: bytes | None = None
    error: BaseException | None = None
    batch_abort: threading.Event | None = None


def _new_transport(config: SerialConfig) -> Transport:
    return SerialTransport(config)


def _load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if value:
        return value
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def _make_logger(log_file: Path, level: str | int) -> logging.Logger:
    logger = logging.getLogger(f"python_samba.commserver.{id(log_file)}")
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
        )
        logger.addHandler(handler)
    return logger


class CommunicationServer:
    """Own one physical controller and serialize all clients globally."""

    def __init__(
        self,
        listen: list[tuple[str, int]] | tuple[str, int] = ("127.0.0.1", 47619),
        *,
        token: str | None = None,
        token_file: str | Path | None = None,
        force_token: bool = False,
        auth_mode: str = "auto",
        transport_factory: TransportFactory = _new_transport,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        log_file: str | Path | None = None,
        log_level: str | int = "INFO",
        preferred_port: str | None = None,
        preferred_baudrate: int = 57600,
    ) -> None:
        auth_mode = str(auth_mode).strip().lower().replace("_", "-")
        if auth_mode not in {"auto", "token", "trusted-network"}:
            raise ServerConfigError(
                "auth_mode must be 'auto', 'token', or 'trusted-network'"
            )
        if force_token:
            auth_mode = "token"
        if isinstance(listen, tuple):
            listen = [listen]
        if not listen:
            raise ServerConfigError("at least one listen endpoint is required")
        endpoints: list[tuple[str, int]] = []
        for host, port in listen:
            host, port = str(host).strip(), int(port)
            wildcard = host == "0.0.0.0"
            if wildcard and auth_mode not in {"token", "trusted-network"}:
                raise ServerConfigError(
                    "0.0.0.0 requires token or trusted-network authentication mode"
                )
            if not wildcard and not is_allowed_listen_host(host):
                raise ServerConfigError(
                    f"refusing to listen on {host!r}; use loopback, a concrete "
                    "RFC1918 LAN address, or a Tailscale 100.64.0.0/10 address"
                )
            if not 0 <= port <= 65535:
                raise ServerConfigError(f"invalid listen port: {port}")
            endpoints.append((host, port))
        self._requested_endpoints = endpoints
        self._force_token = bool(force_token)
        self._auth_mode = auth_mode
        self._remote_enabled = any(not is_loopback_host(host) for host, _ in endpoints)
        self._token_file = Path(token_file) if token_file else default_token_file()
        self._token = token or (
            _load_or_create_token(self._token_file)
            if (
                self._auth_mode == "token"
                or (self._auth_mode == "auto" and self._remote_enabled)
                or self._force_token
            )
            else None
        )
        self._transport_factory = transport_factory
        self._max_message_bytes = int(max_message_bytes)
        self._logger = _make_logger(
            Path(log_file) if log_file else default_log_file(), log_level
        )
        self._preferred_config = (
            (str(preferred_port).strip(), int(preferred_baudrate))
            if preferred_port
            else None
        )

        self._lock = threading.RLock()
        self._serial_lock = threading.RLock()
        # Queue a batch as one contiguous FIFO group.  Without this lock a
        # second client could insert a request between two items while the
        # first client's handler is still filling the queue.
        self._enqueue_lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False
        self._listeners: list[socket.socket] = []
        self._listener_threads: list[threading.Thread] = []
        self._client_threads: set[threading.Thread] = set()
        self._client_sockets: set[socket.socket] = set()
        self._clients: dict[str, _Client] = {}
        self._serial: Transport | None = None
        self._serial_config: tuple[str, int] | None = None
        self._tasks: queue.Queue[_ExchangeTask | None] = queue.Queue()
        self._sequence = itertools.count(1)
        self._worker: threading.Thread | None = None
        self._last_command: str | None = None
        self._last_duration_ms: float | None = None
        self._last_error: str | None = None
        self._completed_requests = 0

    @property
    def addresses(self) -> list[tuple[str, int]]:
        with self._lock:
            return [listener.getsockname()[:2] for listener in self._listeners]

    @property
    def token_file(self) -> Path:
        return self._token_file

    @property
    def auth_mode(self) -> str:
        if self._auth_mode == "auto":
            return "token" if self._remote_enabled else "local"
        return self._auth_mode

    def start(self) -> "CommunicationServer":
        with self._lock:
            if self._started:
                return self
            listeners: list[socket.socket] = []
            try:
                for host, port in self._requested_endpoints:
                    listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
                    if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        # SO_REUSEADDR on Windows permits a second process to
                        # steal the same port, defeating singleton startup.
                        listener.setsockopt(
                            socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
                        )
                    else:
                        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind((host, port))
                    listener.listen(32)
                    listener.settimeout(0.5)
                    listeners.append(listener)
            except OSError as exc:
                for listener in listeners:
                    listener.close()
                if getattr(exc, "winerror", None) == 10048 or exc.errno in {48, 98, 10048}:
                    raise ServerAlreadyRunning(str(exc)) from exc
                raise
            self._listeners = listeners
            self._started = True
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._exchange_worker,
                name="CommServer-FIFO",
                daemon=True,
            )
            self._worker.start()
            for listener in listeners:
                host = str(listener.getsockname()[0])
                require_token = bool(
                    self._force_token
                    or self._auth_mode == "token"
                    or (
                        self._auth_mode == "auto"
                        and not is_loopback_host(host)
                    )
                )
                thread = threading.Thread(
                    target=self._accept_loop,
                    args=(listener, require_token),
                    name=f"CommServer-Accept-{host}",
                    daemon=True,
                )
                self._listener_threads.append(thread)
                thread.start()
            self._logger.info(
                "server started listeners=%s token_required=%s",
                [format_endpoint(*address) for address in self.addresses],
                self.auth_mode == "token",
            )
        return self

    def serve_forever(self) -> None:
        self.start()
        while not self._stop.wait(0.5):
            pass

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop.set()
            listeners, self._listeners = self._listeners, []
            sockets = list(self._client_sockets)
            self._started = False
        for listener in listeners:
            try:
                listener.close()
            except OSError:
                pass
        for client_sock in sockets:
            try:
                client_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client_sock.close()
            except OSError:
                pass
        # Wake every handler already waiting on a queued request.  Serialize
        # this drain with producers so no request can be inserted behind the
        # worker sentinel and wait forever during shutdown.
        with self._enqueue_lock:
            while True:
                try:
                    pending = self._tasks.get_nowait()
                except queue.Empty:
                    break
                if pending is not None:
                    pending.error = TransportError(
                        "communication server is stopping"
                    )
                    pending.done.set()
                self._tasks.task_done()
            self._tasks.put(None)
        self._close_serial(clear_config=True)
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        self._logger.info("server stopped")

    def status(self) -> dict[str, object]:
        with self._lock:
            config = self._serial_config or self._preferred_config
            clients = [
                {
                    "id": client.client_id,
                    "name": client.name,
                    "pid": client.pid,
                    "peer": client.peer,
                    "attached": client.attached,
                    "port": client.port,
                    "baudrate": client.baudrate,
                    "connected_at": client.connected_at,
                }
                for client in self._clients.values()
            ]
            serial_open = bool(self._serial and self._serial.is_open)
            return {
                "protocol": PROTOCOL_VERSION,
                "features": list(SERVER_FEATURES),
                "auth_mode": self.auth_mode,
                "listeners": [format_endpoint(*address) for address in self.addresses],
                "serial": {
                    "open": serial_open,
                    "port": config[0] if config else None,
                    "baudrate": config[1] if config else None,
                },
                "clients": clients,
                "client_count": len(clients),
                "attached_count": sum(bool(item["attached"]) for item in clients),
                "queue_length": self._tasks.qsize(),
                "last_command": self._last_command,
                "last_duration_ms": self._last_duration_ms,
                "last_error": self._last_error,
                "completed_requests": self._completed_requests,
            }

    def restart_serial(self) -> dict[str, object]:
        with self._lock:
            config = self._serial_config
            attached = any(client.attached for client in self._clients.values())
        self._close_serial(clear_config=False)
        if config and attached:
            self._ensure_serial_open(config)
        return self.status()

    def _accept_loop(self, listener: socket.socket, require_token: bool) -> None:
        while not self._stop.is_set():
            try:
                client_sock, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer_host = str(peer[0])
            if self._auth_mode == "trusted-network" and not is_trusted_peer_host(
                peer_host
            ):
                self._logger.warning("rejected untrusted network peer %s", peer_host)
                try:
                    client_sock.close()
                except OSError:
                    pass
                continue
            configure_low_latency_socket(client_sock)
            client_sock.settimeout(10.0)
            with self._lock:
                self._client_sockets.add(client_sock)
            thread = threading.Thread(
                target=self._client_loop,
                args=(client_sock, peer_host, require_token),
                name=f"CommServer-Client-{peer[0]}",
                daemon=True,
            )
            with self._lock:
                self._client_threads.add(thread)
            thread.start()

    def _client_loop(
        self, client_sock: socket.socket, peer: str, require_token: bool
    ) -> None:
        client_id: str | None = None
        try:
            hello = recv_message(client_sock, max_message_bytes=self._max_message_bytes)
            if hello.get("op") != "hello":
                raise ProtocolMessageError("first message must be hello")
            if hello.get("protocol") != PROTOCOL_VERSION:
                raise ProtocolMessageError(
                    f"protocol mismatch: server={PROTOCOL_VERSION}, "
                    f"client={hello.get('protocol')!r}"
                )
            supplied_token = str(hello.get("token") or "")
            if require_token and (
                not self._token or not hmac.compare_digest(supplied_token, self._token)
            ):
                self._send_error(client_sock, hello.get("id"), "AuthenticationError", "invalid access token")
                return
            client_id = str(uuid.uuid4())
            pid_value = hello.get("pid")
            try:
                pid = int(pid_value) if pid_value is not None else None
            except (TypeError, ValueError):
                pid = None
            client = _Client(
                client_id=client_id,
                name=str(hello.get("name") or "unknown-client")[:128],
                pid=pid,
                peer=peer,
            )
            with self._lock:
                self._clients[client_id] = client
            send_message(
                client_sock,
                {
                    "id": hello.get("id"),
                    "ok": True,
                    "result": {
                        "protocol": PROTOCOL_VERSION,
                        "features": list(SERVER_FEATURES),
                        "client_id": client_id,
                        "status": self.status(),
                    },
                },
                max_message_bytes=self._max_message_bytes,
            )
            client_sock.settimeout(None)
            while not self._stop.is_set():
                message = recv_message(
                    client_sock, max_message_bytes=self._max_message_bytes
                )
                request_id = message.get("id")
                try:
                    result, shutdown = self._dispatch(client_id, message)
                    send_message(
                        client_sock,
                        {"id": request_id, "ok": True, "result": result},
                        max_message_bytes=self._max_message_bytes,
                    )
                    if shutdown:
                        threading.Thread(
                            target=self.stop,
                            name="CommServer-Shutdown",
                            daemon=True,
                        ).start()
                        return
                except BaseException as exc:
                    self._send_error(
                        client_sock,
                        request_id,
                        type(exc).__name__,
                        str(exc),
                    )
        except (EOFError, ConnectionError, OSError):
            pass
        except BaseException as exc:
            self._logger.warning("client %s failed: %s", peer, exc)
            try:
                self._send_error(client_sock, None, type(exc).__name__, str(exc))
            except BaseException:
                pass
        finally:
            if client_id:
                self._detach(client_id, remove=True)
            with self._lock:
                self._client_sockets.discard(client_sock)
                self._client_threads.discard(threading.current_thread())
            try:
                client_sock.close()
            except OSError:
                pass

    def _dispatch(
        self, client_id: str, message: dict[str, object]
    ) -> tuple[object, bool]:
        op = str(message.get("op") or "")
        if op == "attach":
            port = str(message.get("port") or "").strip()
            try:
                baudrate = int(message.get("baudrate") or 0)
            except (TypeError, ValueError) as exc:
                raise ServerConfigError("baudrate must be an integer") from exc
            return self._attach(client_id, port, baudrate), False
        if op == "detach":
            self._detach(client_id, remove=False)
            return self.status(), False
        if op == "status":
            return self.status(), False
        if op == "restart_serial":
            return self.restart_serial(), False
        if op == "shutdown":
            return {"shutting_down": True}, True
        if op not in {"exchange", "exchange_batch"}:
            raise ProtocolMessageError(f"unknown operation: {op!r}")

        client = self._client(client_id)
        if not client.attached:
            raise ServerConfigError("client is not attached to a serial controller")

        if op == "exchange":
            specs = [self._parse_exchange_item(message, field_prefix="")]
        else:
            raw_items = message.get("items")
            if not isinstance(raw_items, list):
                raise ProtocolMessageError("exchange_batch items must be a list")
            if not raw_items:
                raise ProtocolMessageError("exchange_batch items must not be empty")
            if len(raw_items) > MAX_BATCH_ITEMS:
                raise ProtocolMessageError(
                    f"exchange_batch has {len(raw_items)} items; limit is {MAX_BATCH_ITEMS}"
                )
            specs = []
            # Validate the complete message before enqueuing anything.  A bad
            # later item must not cause a partial batch to reach the controller.
            for index, raw_item in enumerate(raw_items):
                if not isinstance(raw_item, dict):
                    raise ProtocolMessageError(
                        f"exchange_batch item {index} must be an object"
                    )
                specs.append(
                    self._parse_exchange_item(
                        raw_item, field_prefix=f"items[{index}]."
                    )
                )

        tasks: list[_ExchangeTask] = []
        batch_abort = threading.Event() if len(specs) > 1 else None
        with self._enqueue_lock:
            if self._stop.is_set():
                raise TransportError("communication server is stopping")
            for request, terminator, timeout in specs:
                task = _ExchangeTask(
                    sequence=next(self._sequence),
                    client_id=client_id,
                    request=request,
                    terminator=terminator,
                    timeout=timeout,
                    batch_abort=batch_abort,
                )
                tasks.append(task)
                self._tasks.put(task)

        # Wait for the whole group before reporting an error.  Every item is
        # already in the global FIFO, so returning early would leave the client
        # unable to know whether later commands ran.
        for task in tasks:
            task.done.wait()
        for task in tasks:
            if task.error is not None:
                raise task.error
            if task.response is None:
                raise TransportError("communication server returned no response")

        results = [
            {"sequence": task.sequence, "response": encode_bytes(task.response or b"")}
            for task in tasks
        ]
        if op == "exchange":
            return results[0], False
        return {"items": results}, False

    @staticmethod
    def _parse_exchange_item(
        message: dict[str, object], *, field_prefix: str
    ) -> tuple[bytes, bytes, float]:
        request = decode_bytes(
            message.get("request"), field=f"{field_prefix}request"
        )
        terminator = decode_bytes(
            message.get("terminator"), field=f"{field_prefix}terminator"
        )
        if not request:
            raise ProtocolMessageError(f"{field_prefix}request must not be empty")
        if not terminator:
            raise ProtocolMessageError(f"{field_prefix}terminator must not be empty")
        try:
            timeout = float(message.get("timeout") or 0)
        except (TypeError, ValueError) as exc:
            raise ProtocolMessageError(f"{field_prefix}timeout must be numeric") from exc
        if not 0 < timeout <= 300.0:
            raise ProtocolMessageError(
                f"{field_prefix}timeout must be in (0, 300] seconds"
            )
        return request, terminator, timeout

    def _client(self, client_id: str) -> _Client:
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                raise ConnectionError("unknown communication-server client")
            return client

    def _attach(self, client_id: str, port: str, baudrate: int) -> dict[str, object]:
        if not port:
            raise ServerConfigError("serial port must not be empty")
        if baudrate <= 0:
            raise ServerConfigError("baudrate must be positive")
        requested = (port.upper() if port.upper().startswith("COM") else port, baudrate)
        with self._lock:
            client = self._clients[client_id]
            if client.attached:
                if (client.port, client.baudrate) != requested:
                    raise ServerConfigError(
                        f"client already attached to {client.port} @ {client.baudrate}"
                    )
                return self.status()
            established = self._serial_config or self._preferred_config
            if established and established != requested:
                raise ServerConfigError(
                    f"controller is already configured as {established[0]} @ "
                    f"{established[1]}; requested {requested[0]} @ {requested[1]}"
                )
            self._serial_config = requested
        try:
            self._ensure_serial_open(requested)
        except BaseException:
            with self._lock:
                if not any(item.attached for item in self._clients.values()):
                    self._serial_config = None
            raise
        with self._lock:
            client = self._clients[client_id]
            client.attached = True
            client.port, client.baudrate = requested
            self._last_error = None
        self._logger.info("client %s attached %s @ %d", client.name, *requested)
        return self.status()

    def _detach(self, client_id: str, *, remove: bool) -> None:
        close_serial = False
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                return
            client.attached = False
            client.port = None
            client.baudrate = None
            if remove:
                del self._clients[client_id]
            if not any(item.attached for item in self._clients.values()):
                close_serial = True
                self._serial_config = None
        if close_serial:
            self._close_serial(clear_config=False)

    def _ensure_serial_open(self, config: tuple[str, int]) -> None:
        with self._serial_lock:
            if self._serial and self._serial.is_open:
                return
            transport = self._transport_factory(
                SerialConfig(port=config[0], baudrate=config[1], timeout=5.0)
            )
            try:
                transport.open()
            except BaseException as exc:
                try:
                    transport.close()
                except BaseException:
                    pass
                with self._lock:
                    self._last_error = str(exc)
                raise
            self._serial = transport

    def _close_serial(self, *, clear_config: bool) -> None:
        with self._serial_lock:
            transport, self._serial = self._serial, None
            if transport:
                try:
                    transport.close()
                except BaseException as exc:
                    self._logger.warning("serial close failed: %s", exc)
        if clear_config:
            with self._lock:
                self._serial_config = None

    def _exchange_worker(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                self._tasks.task_done()
                return
            started = time.monotonic()
            command = self._command_name(task.request)
            try:
                if task.batch_abort is not None and task.batch_abort.is_set():
                    raise TransportError(
                        "batch aborted after an earlier physical exchange failed"
                    )
                with self._lock:
                    config = self._serial_config
                if config is None:
                    raise TransportError("no serial controller is attached")
                with self._serial_lock:
                    if not self._serial or not self._serial.is_open:
                        self._ensure_serial_open(config)
                    assert self._serial is not None
                    task.response = self._serial.exchange(
                        task.request, task.terminator, task.timeout
                    )
            except BaseException as exc:
                task.error = exc
                if task.batch_abort is not None:
                    task.batch_abort.set()
                with self._lock:
                    self._last_error = str(exc)
                if isinstance(exc, (TransportError, OSError)):
                    self._close_serial(clear_config=False)
            finally:
                elapsed = (time.monotonic() - started) * 1000.0
                with self._lock:
                    self._last_command = command
                    self._last_duration_ms = elapsed
                    self._completed_requests += 1
                    if task.error is None:
                        self._last_error = None
                task.done.set()
                self._tasks.task_done()

    @staticmethod
    def _command_name(request: bytes) -> str:
        try:
            parts = request.decode("ascii", errors="ignore").split()
            return parts[1][:16] if len(parts) > 1 else request[:16].hex()
        except BaseException:
            return request[:16].hex()

    def _send_error(
        self,
        client_sock: socket.socket,
        request_id: object,
        kind: str,
        message: str,
    ) -> None:
        send_message(
            client_sock,
            {
                "id": request_id,
                "ok": False,
                "error": {"type": kind, "message": message},
            },
            max_message_bytes=self._max_message_bytes,
        )
