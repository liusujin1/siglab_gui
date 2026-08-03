"""Transport boundary and request/response correlation tests."""

from __future__ import annotations

import pytest

from python_samba.protocol.frame import ProtocolError, format_crc, xor_checksum
from python_samba.services.session import ControllerSession
from python_samba.transport.serial_port import (
    SerialConfig,
    SerialTransport,
    Transport,
    TransportError,
)


def _response(msg_id: str, crl: str, mnemonic: str) -> bytes:
    data = f"{crl} 00 {mnemonic} 3 3 9 0"
    body_core = f"{msg_id}0{data}"
    mid = f"{2 + len(body_core):02X}{body_core}"
    return f":{mid}{format_crc(xor_checksum(mid))}\r".encode("ascii")


class _StaticTransport(Transport):
    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def write(self, data: bytes) -> None:
        self.last_write = data

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        return self.reply

    @property
    def is_open(self) -> bool:
        return self._open


class _SequenceTransport(_StaticTransport):
    def __init__(self, replies: list[bytes]) -> None:
        super().__init__(b"")
        self.replies = list(replies)

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        if not self.replies:
            raise TransportError("no queued response")
        return self.replies.pop(0)


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (_response("$", "00", "BGVIS"), "message id"),
        (_response("?", "FF", "BGVIS"), "CRL"),
        (_response("?", "00", "BGSTS"), "mnemonic"),
    ],
)
def test_session_rejects_stale_or_unrelated_response(reply: bytes, error: str):
    transport = _StaticTransport(reply)
    session = ControllerSession(transport)
    transport.open()
    with pytest.raises(ProtocolError, match=error):
        session.get_version()


def test_session_skips_real_controller_blank_frame_separators():
    transport = _SequenceTransport([
        b"\n\r",
        b"\r",
        _response("?", "00", "BGVIS"),
    ])
    session = ControllerSession(transport)
    transport.open()

    version = session.get_version()

    assert (version.major, version.minor, version.patch) == (3, 3, 9)
    assert transport.replies == []


class _FakeSerial:
    def __init__(self, incoming: bytes = b"", *, written: int | None = None) -> None:
        self.incoming = bytearray(incoming)
        self.written = written
        self.is_open = True
        self.timeout = 0.0
        self.flushed = False
        self.read_sizes: list[int] = []

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def write(self, data: bytes) -> int:
        return len(data) if self.written is None else self.written

    def flush(self) -> None:
        self.flushed = True

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if not self.incoming:
            return b""
        value = bytes(self.incoming[:size])
        del self.incoming[:size]
        return value


def test_serial_accepts_response_larger_than_legacy_700_byte_limit():
    transport = SerialTransport(SerialConfig("COM1", max_response_bytes=1024))
    fake = _FakeSerial(b"x" * 800 + b"\r")
    transport._ser = fake
    assert len(transport.read_until(timeout=0.5)) == 801
    assert max(fake.read_sizes) > 1


def test_serial_write_does_not_force_slow_windows_driver_flush():
    transport = SerialTransport(SerialConfig("COM1"))
    fake = _FakeSerial()
    transport._ser = fake

    transport.write(b"abcd")

    assert not fake.flushed


def test_serial_rejects_short_write():
    transport = SerialTransport(SerialConfig("COM1"))
    transport._ser = _FakeSerial(written=2)
    with pytest.raises(TransportError, match="short write"):
        transport.write(b"abcd")


def test_session_invalidates_serial_handle_after_write_failure():
    class FailingTransport(_StaticTransport):
        def write(self, data: bytes) -> None:
            raise TransportError("write failed: access denied")

    transport = FailingTransport(b"")
    session = ControllerSession(transport)
    transport.open()
    session._connected = True

    with pytest.raises(TransportError, match="access denied"):
        session.get_version()

    assert not session.connected
    assert not transport.is_open
