"""Transport interfaces and serial implementation."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


class TransportError(RuntimeError):
    """I/O failure on the controller link."""


class Transport(ABC):
    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    def exchange(
        self,
        request: bytes,
        terminator: bytes = b"\r",
        timeout: float = 2.0,
    ) -> bytes:
        """Atomically write one request and return its non-empty response.

        Concrete transports may override this method when the request/response
        pair has to cross another process boundary.  The default keeps legacy
        serial and mock transports compatible while filtering the controller's
        trailing ``\n\r\r`` separators under one timeout deadline.
        """
        if not terminator:
            raise ValueError("terminator must not be empty")
        self.write(request)
        deadline = time.monotonic() + float(timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError("timeout waiting for response")
            response = self.read_until(terminator, timeout=remaining)
            if response.strip(b" \t\r\n"):
                return response

    def exchange_many(
        self,
        requests: Sequence[tuple[bytes, bytes, float]],
    ) -> list[bytes]:
        """Execute several exchanges in order.

        Direct serial and mock transports deliberately keep the simple
        sequential implementation.  A process-boundary transport can override
        this method to carry the whole group in one network RPC while the
        communication server still executes the physical exchanges in order.
        """
        return [
            self.exchange(request, terminator, timeout)
            for request, terminator, timeout in requests
        ]


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 57600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    timeout: float = 5.0
    max_response_bytes: int = 65536


class SerialTransport(Transport):
    """pyserial-backed RS232 / USB-CDC link. No vendor DLL required."""

    def __init__(self, config: SerialConfig) -> None:
        self.config = config
        self._ser = None

    def open(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise TransportError(
                "pyserial is required for live serial. Install with: pip install pyserial"
            ) from exc
        if self._ser and self._ser.is_open:
            return
        try:
            self._ser = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=self.config.bytesize,
                parity=self.config.parity,
                stopbits=self.config.stopbits,
                timeout=self.config.timeout,
                write_timeout=self.config.timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except Exception as exc:  # serial.SerialException
            raise TransportError(f"open {self.config.port} failed: {exc}") from exc
        # Drop any stale bytes
        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return bool(self._ser is not None and self._ser.is_open)

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise TransportError("serial port not open")
        assert self._ser is not None
        try:
            written = self._ser.write(data)
            if written != len(data):
                raise TransportError(
                    f"short write: wrote {written} of {len(data)} bytes"
                )
            # Do not call Serial.flush() here.  On Windows it maps to
            # FlushFileBuffers and some COM drivers block for up to seconds
            # per tiny RCI telegram.  write() has already copied the complete
            # frame into the driver buffer; the following read provides the
            # required request/response synchronization.
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"write failed: {exc}") from exc

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        if not self.is_open:
            raise TransportError("serial port not open")
        if not terminator:
            raise ValueError("terminator must not be empty")
        assert self._ser is not None
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            self._ser.timeout = min(0.05, remaining) if remaining else 0
            try:
                available = int(getattr(self._ser, "in_waiting", 0) or 0)
                chunk = self._ser.read(max(1, available))
            except Exception as exc:
                raise TransportError(f"read failed: {exc}") from exc
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > self.config.max_response_bytes:
                raise TransportError(
                    f"response exceeded max size ({self.config.max_response_bytes} bytes)"
                )
            if buf.endswith(terminator):
                return bytes(buf)
        raise TransportError(f"timeout waiting for response, partial={bytes(buf)!r}")
