"""Conservative NetDAQ wire primitives; no firmware or file-management commands.

Reference: _ana/netdaq_protocol.py v2.6, reviewed 2026-10-29. Device-specific
range tables, stream ordering and calibration MUST come from a verified profile.
"""
from __future__ import annotations

import math
import socket
import struct
import threading
import time

import numpy as np

from .base import DaqBackendError


class NetDaqProtocolError(DaqBackendError):
    pass


class NetDaqStopped(DaqBackendError):
    pass


def legacy_request(command: int, payload: bytes = b"") -> bytes:
    if len(payload) > 250:
        raise NetDaqProtocolError("NetDAQ command payload is too large")
    body = bytes((0x21, command, len(payload) + 5)) + payload
    frame = body + bytes((sum(body) & 255, 13))
    return frame + (b"\0" if len(frame) % 2 else b"")


def checked_ack(frame: bytes, command: int) -> None:
    if (len(frame) != 6 or frame[:4] != b"!" + bytes((command,)) + b"OK"
            or frame[4] != sum(frame[:4]) & 255 or frame[5] != 13):
        raise NetDaqProtocolError(f"NetDAQ rejected {chr(command)!r}: {frame.hex()}")


def descriptor_data(raw: bytes) -> bytes:
    if len(raw) != 33 or raw[:3] != b"!D!":
        raise NetDaqProtocolError("NetDAQ device descriptor must be a 33-byte !D! reply")
    return raw[3:31]


def sample_clock(raw: bytes) -> tuple[float, int]:
    desc = descriptor_data(raw)
    if desc[0] == 1:
        return 50000.0, 1
    if desc[0] != 0:
        raise NetDaqProtocolError(f"Unsupported NetDAQ family {desc[0]}")
    clocks = {0: (64000.0, 1), 0x81: (64000.0, 1),
              0x82: (1000000.0, 2), 0x84: (128000.0, 1), 0x85: (5000.0, 1)}
    if desc[27] not in clocks:
        raise NetDaqProtocolError(f"Unsupported NetDAQ clock selector {desc[27]}")
    return clocks[desc[27]]


def sampling_divisor(raw: bytes, rate: float) -> tuple[int, float]:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise NetDaqProtocolError("NetDAQ sample rate must be a number")
    if not math.isfinite(rate) or rate <= 0:
        raise NetDaqProtocolError("NetDAQ sample rate must be finite and positive")
    clock, factor = sample_clock(raw)
    value = clock / (factor * rate) + 0.4
    if not math.isfinite(value) or value < 1 or value >= 0x100000000:
        raise NetDaqProtocolError("NetDAQ sample rate is outside the divisor field range")
    divisor = int(value)
    return divisor, clock / (factor * divisor)


def decode_counts(payload: bytes, bits: int) -> np.ndarray:
    if bits not in (16, 24, 32) or len(payload) % (bits // 8):
        raise NetDaqProtocolError("NetDAQ truncated or unsupported sample format")
    if bits != 24:
        return np.frombuffer(payload, dtype=f"<i{bits // 8}").astype(np.int32)
    b = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    values = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    return (values ^ 0x800000) - 0x800000


class WireConnection:
    """Socket ownership stays in the caller's acquisition/probe thread."""

    def __init__(self, host: str, port: int, timeout: float = 2.0,
                 stop_event: threading.Event | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.stop_event = stop_event
        self.sock: socket.socket | None = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(min(0.05, self.timeout))
        return self

    def close(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def send(self, data: bytes):
        if self.sock is None:
            raise NetDaqProtocolError("NetDAQ socket is not connected")
        deadline = time.monotonic() + self.timeout
        view = memoryview(data)
        while view:
            self._check_stop()
            if time.monotonic() >= deadline:
                raise NetDaqProtocolError("NetDAQ command write timed out")
            try:
                size = self.sock.send(view)
            except socket.timeout:
                continue
            if size == 0:
                raise NetDaqProtocolError("NetDAQ connection closed during write")
            view = view[size:]

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise NetDaqStopped("NetDAQ acquisition stopped")

    def receive(self, size: int, *, deadline: float | None = None) -> bytes:
        if not 0 <= size <= 64 * 1024 * 1024:
            raise NetDaqProtocolError("NetDAQ invalid receive length")
        if self.sock is None:
            raise NetDaqProtocolError("NetDAQ socket is not connected")
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        data = bytearray()
        while len(data) < size:
            self._check_stop()
            if time.monotonic() >= deadline:
                raise NetDaqProtocolError(f"NetDAQ timed out after {len(data)}/{size} bytes")
            try:
                block = self.sock.recv(size - len(data))
            except socket.timeout:
                continue
            if not block:
                raise NetDaqProtocolError(f"NetDAQ disconnected after {len(data)}/{size} bytes")
            data.extend(block)
        return bytes(data)


class LegacyConnection(WireConnection):
    def command(self, command: int, payload: bytes = b"", size: int = 6) -> bytes:
        try:
            self.send(legacy_request(command, payload))
            deadline = time.monotonic() + self.timeout
            if size == 0:
                head = self.receive(3, deadline=deadline)
                if head[2] < 6:
                    raise NetDaqProtocolError("NetDAQ invalid reply length")
                return head + self.receive(head[2] - 3, deadline=deadline)
            return self.receive(size, deadline=deadline)
        except Exception:
            # A partial response cannot be reused as the next command's reply.
            self.close()
            raise

    def descriptor(self) -> bytes:
        raw = self.command(0x44, size=33)
        descriptor_data(raw)
        return raw

    def channel_descriptor(self, number: int) -> tuple[int, int]:
        raw = self.command(0x41, bytes((number,)), 8)
        if (raw[:4] != bytes((0x21, 0x41, 8, number)) or raw[-1] != 13
                or raw[-2] != sum(raw[:-2]) & 255):
            raise NetDaqProtocolError("NetDAQ invalid channel descriptor")
        return raw[4], raw[5]

    def channel_configuration(self, number: int) -> bytes:
        """Read current settings as opaque bytes; do not infer electrical units."""
        raw = self.command(0x42, bytes((number,)), 0)
        if (raw[:2] != b"!B" or raw[3] != number or raw[-1] != 13
                or raw[-2] != sum(raw[:-2]) & 255):
            raise NetDaqProtocolError("NetDAQ invalid channel configuration")
        return raw[4:-2]

    def set_channel(self, number: int, coupling: int, gain: int, *, expected_type: int | None = None):
        kind, count = self.channel_descriptor(number)
        if expected_type is not None and kind != expected_type:
            raise NetDaqProtocolError("NetDAQ reported channel type differs from verified profile")
        blob = bytearray(self.channel_configuration(number))
        masks = {0: (1, 6, 1), 6: (3, 12, 2), 8: (15, 112, 4)}
        if kind not in masks or len(blob) < 2:
            raise NetDaqProtocolError("NetDAQ unsupported channel configuration")
        cmask, gmask, shift = masks[kind]
        if not 0 <= coupling <= cmask or not 0 <= gain <= gmask >> shift:
            raise NetDaqProtocolError("NetDAQ coupling/range index is out of bounds")
        blob[1] = (blob[1] & ~(cmask | gmask)) | coupling | (gain << shift)
        length = count - 1 if kind == 8 else 1
        if length < 1 or len(blob) < 1 + length:
            raise NetDaqProtocolError("NetDAQ channel configuration length mismatch")
        checked_ack(self.command(0x43, bytes((number,)) + bytes(blob[1:1 + length])), 0x43)

    def initialize_ad(self, payload: bytes):
        if len(payload) != 13:
            raise NetDaqProtocolError("NetDAQ InitAD requires a verified 13-byte payload")
        checked_ack(self.command(0x49, payload), 0x49)

    def start(self):
        checked_ack(self.command(0x53), 0x53)

    def stop(self):
        checked_ack(self.command(0x50), 0x50)
