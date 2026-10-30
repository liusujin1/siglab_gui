"""Gated USB2404 transport. No channel writes, firmware upload, or driver install.

Wire evidence: _ana/PROTOCOL_USB.md, USB2404V10 firmware 2.0. ctypes ABI:
libusb-win32 lusb0_usb.h (byte-packed structures, including usb_device/bus).
Only the acquisition/probe owner calls native code; Stop only sets an event.
"""
from __future__ import annotations

import ctypes as ct
import os
from pathlib import Path
import struct
import threading
import time

from .netdaq_protocol import (
    NetDaqProtocolError, NetDaqStopped, checked_ack, descriptor_data, legacy_request,
)

VID, PID = 0x0547, 0x1003
_lease = threading.Lock()
_library = None
_quarantined = []
CANCEL_CONFIRM_SECONDS = 2.0
DRAIN_SECONDS = 1.0


class DeviceDescriptor(ct.Structure):
    _pack_ = 1
    _fields_ = [
        ("length", ct.c_ubyte), ("kind", ct.c_ubyte), ("usb", ct.c_ushort),
        ("device_class", ct.c_ubyte), ("subclass", ct.c_ubyte),
        ("protocol", ct.c_ubyte), ("packet_size", ct.c_ubyte),
        ("vid", ct.c_ushort), ("pid", ct.c_ushort), ("release", ct.c_ushort),
        ("manufacturer", ct.c_ubyte), ("product", ct.c_ubyte),
        ("serial", ct.c_ubyte), ("configurations", ct.c_ubyte),
    ]


class UsbDevice(ct.Structure):
    _pack_ = 1


UsbDevice._fields_ = [
    ("next", ct.POINTER(UsbDevice)), ("prev", ct.POINTER(UsbDevice)),
    ("filename", ct.c_char * 512), ("bus", ct.c_void_p),
    ("descriptor", DeviceDescriptor), ("config", ct.c_void_p),
    ("dev", ct.c_void_p), ("devnum", ct.c_ubyte),
    ("num_children", ct.c_ubyte), ("children", ct.c_void_p),
]


class UsbBus(ct.Structure):
    _pack_ = 1


UsbBus._fields_ = [
    ("next", ct.POINTER(UsbBus)), ("prev", ct.POINTER(UsbBus)),
    ("dirname", ct.c_char * 512), ("devices", ct.POINTER(UsbDevice)),
    ("location", ct.c_uint32), ("root_dev", ct.c_void_p),
]


def load_library():
    global _library
    if _library is not None:
        return _library
    if os.name != "nt":
        raise NetDaqProtocolError("USBDAQ requires Windows and the vendor libusb-win32 driver")
    # Never search cwd/PATH or load the installer's incompatible 32-bit DLL.
    system = "System32" if ct.sizeof(ct.c_void_p) == 8 else "SysWOW64"
    lib = ct.CDLL(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / system / "libusb0.dll"))
    p, i, buf = ct.c_void_p, ct.c_int, ct.c_void_p
    signatures = {
        "usb_init": (None, []), "usb_find_busses": (i, []), "usb_find_devices": (i, []),
        "usb_get_busses": (ct.POINTER(UsbBus), []),
        "usb_open": (p, [ct.POINTER(UsbDevice)]), "usb_close": (i, [p]),
        "usb_claim_interface": (i, [p, i]), "usb_release_interface": (i, [p, i]),
        "usb_get_string_simple": (i, [p, i, buf, i]),
        "usb_get_descriptor": (i, [p, ct.c_ubyte, ct.c_ubyte, buf, i]),
        "usb_bulk_write": (i, [p, i, buf, i, i]),
        "usb_bulk_read": (i, [p, i, buf, i, i]),
        "usb_clear_halt": (i, [p, i]), "usb_strerror": (ct.c_char_p, []),
        "usb_bulk_setup_async": (i, [p, ct.POINTER(p), ct.c_ubyte]),
        "usb_submit_async": (i, [p, buf, i]),
        "usb_reap_async_nocancel": (i, [p, i]),
        "usb_cancel_async": (i, [p]), "usb_free_async": (i, [ct.POINTER(p)]),
    }
    for name, (result, arguments) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, arguments
    _library = lib
    return lib


def checked_reply(raw, command, size=0):
    if (len(raw) < 6 or (size and len(raw) != size)
            or raw[:2] != bytes((0x21, command)) or raw[2] != len(raw)
            or raw[-1] != 13 or raw[-2] != sum(raw[:-2]) & 255):
        raise NetDaqProtocolError(f"USBDAQ invalid {chr(command)!r} reply: {raw.hex()}")
    return raw


def check_endpoints(raw):
    """Validate the one supported interface from standard USB descriptors."""
    if len(raw) < 9 or raw[1] != 2 or int.from_bytes(raw[2:4], "little") != len(raw):
        raise NetDaqProtocolError("USBDAQ invalid USB configuration descriptor")
    offset, interface, endpoints = 0, None, {}
    while offset < len(raw):
        length = raw[offset]
        if length < 2 or offset + length > len(raw):
            raise NetDaqProtocolError("USBDAQ malformed USB descriptor")
        item = raw[offset:offset + length]
        if item[1] == 4:
            if length < 9:
                raise NetDaqProtocolError("USBDAQ truncated interface descriptor")
            interface = (item[2], item[3])
        elif item[1] == 5 and interface == (0, 0):
            if length < 7:
                raise NetDaqProtocolError("USBDAQ truncated endpoint descriptor")
            endpoints[item[2]] = (item[3] & 3, int.from_bytes(item[4:6], "little"))
        offset += length
    required = {0x08: (2, 128), 0x86: (2, 128), 0x82: (2, 512)}
    if any(endpoints.get(ep) != entry for ep, entry in required.items()):
        raise NetDaqProtocolError("USBDAQ endpoints differ from qualified USB2404 layout")
    return endpoints


class UsbConnection:
    """One owner, one handle. Unknown start/stop state always requires !POK."""
    def __init__(self, index=0, timeout=2.0, stop_event=None, *, library=None):
        self.index, self.timeout, self.stop_event = index, timeout, stop_event
        self.lib = library
        self.handle = None
        self.owner = None
        self.info = {}
        self.may_be_running = False
        self.stop_uncertain = False
        self._leased = False
        self._context = ct.c_void_p()
        self._buffer = ct.create_string_buffer(512)
        self._pending = False

    @property
    def connected(self):
        return bool(self.handle)

    def _owner_check(self):
        if self.owner is not None and self.owner != threading.get_ident():
            raise NetDaqProtocolError("USBDAQ native calls must stay on their owner thread")

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise NetDaqStopped("USBDAQ acquisition stopped")

    def _check(self, result, operation):
        if result < 0:
            error = self.lib.usb_strerror()
            raise NetDaqProtocolError(f"USBDAQ {operation} ({result}): {error!r}")
        return result

    def connect(self):
        if self.handle or self.stop_uncertain:
            raise NetDaqProtocolError("USBDAQ already open or previous device state unconfirmed")
        self._check_stop()
        if not _lease.acquire(blocking=False):
            raise NetDaqProtocolError("USBDAQ is busy with another acquisition/probe")
        self._leased, self.owner = True, threading.get_ident()
        try:
            self.lib = self.lib or load_library()
            lib = self.lib
            lib.usb_init()
            self._check(lib.usb_find_busses(), "enumerate buses")
            self._check(lib.usb_find_devices(), "enumerate devices")
            devices, bus = [], lib.usb_get_busses()
            while bus:
                dev = bus.contents.devices
                while dev:
                    d = dev.contents.descriptor
                    if (d.vid, d.pid) == (VID, PID):
                        devices.append(dev)
                    dev = dev.contents.next
                bus = bus.contents.next
            if not devices or not 0 <= self.index < len(devices):
                raise NetDaqProtocolError("USBDAQ target not found (0547:1003); check driver/device index")
            device = devices[self.index]
            self.handle = lib.usb_open(device)
            if not self.handle:
                raise NetDaqProtocolError("USBDAQ open failed; device may be in use by vendor software")
            # Use the driver's active configuration; SET_CONFIGURATION could reset
            # an ongoing device. Never dereference guessed config-structure offsets.
            self._check(lib.usb_claim_interface(self.handle, 0), "claim interface 0")
            for key in ("manufacturer", "product", "serial"):
                index = getattr(device.contents.descriptor, key)
                b = ct.create_string_buffer(256)
                n = lib.usb_get_string_simple(self.handle, index, b, len(b)) if index else 0
                self.info[key] = b.raw[:n].decode("ascii", errors="replace").strip() if 0 < n <= len(b) else ""
            self.info["node"] = device.contents.filename.decode(errors="replace")
            if self.info["product"] != "USB2404V10":
                raise NetDaqProtocolError("USBDAQ unqualified product; no vendor commands were sent")
            b = ct.create_string_buffer(4096)
            n = self._check(lib.usb_get_descriptor(self.handle, 2, 0, b, len(b)), "USB configuration")
            if n > len(b):
                raise NetDaqProtocolError("USBDAQ invalid descriptor length")
            check_endpoints(b.raw[:n])
            return self
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    def command(self, command, payload=b"", size=6):
        self._owner_check()
        self._check_stop()
        if not self.handle:
            raise NetDaqProtocolError("USBDAQ is not open")
        if command not in {0x44, 0x56, 0x41, 0x42, 0x49, 0x53, 0x50}:
            raise NetDaqProtocolError("USBDAQ unsupported command (channel writes are disabled)")
        frame = legacy_request(command, payload)
        if command == 0x53:
            if self.stop_uncertain or self.may_be_running:
                raise NetDaqProtocolError("USBDAQ must confirm Stop before another Start")
            self.may_be_running = True
        try:
            timeout = max(1, int(self.timeout * 1000))
            data = ct.create_string_buffer(frame)
            n = self._check(self.lib.usb_bulk_write(self.handle, 0x08, data, len(frame), timeout), "command write")
            if n != len(frame):
                raise NetDaqProtocolError("USBDAQ partial command write")
            b = ct.create_string_buffer(128)
            n = self._check(self.lib.usb_bulk_read(self.handle, 0x86, b, len(b), timeout), "command reply")
            if n > len(b):
                raise NetDaqProtocolError("USBDAQ reply exceeds buffer")
            raw = b.raw[:n]
            if command in {0x49, 0x53, 0x50}:
                checked_ack(raw, command)
            else:
                checked_reply(raw, command, size)
                if payload and raw[3] != payload[0]:
                    raise NetDaqProtocolError("USBDAQ channel echo mismatch")
            if command == 0x50:
                self.may_be_running = self.stop_uncertain = False
            return raw
        except Exception:
            if command in {0x53, 0x50}:
                self.may_be_running = self.stop_uncertain = True
            raise

    def descriptor(self):
        raw = self.command(0x44, size=33)
        descriptor_data(raw)
        return raw

    def channel_descriptor(self, number):
        raw = self.command(0x41, bytes((number,)), 8)
        return raw[4], raw[5]

    def channel_configuration(self, number):
        return self.command(0x42, bytes((number,)), 0)[4:-2]

    def initialize_ad(self, payload):
        if len(payload) != 13:
            raise NetDaqProtocolError("USBDAQ InitAD requires 13 captured bytes")
        self.command(0x49, payload)

    def start(self):
        self._owner_check()
        self._check_stop()
        if self.stop_uncertain or self.may_be_running:
            raise NetDaqProtocolError("USBDAQ must confirm Stop before another Start")
        self._check(self.lib.usb_clear_halt(self.handle, 0x82), "clear data halt")
        self.command(0x53)

    def read_chunk(self, deadline):
        self._owner_check()
        self._check_stop()
        if not self._context.value:
            self._check(self.lib.usb_bulk_setup_async(self.handle, ct.byref(self._context), 0x82), "setup data read")
        if not self._pending:
            self._check(self.lib.usb_submit_async(self._context, self._buffer, len(self._buffer)), "submit data read")
            self._pending = True
        while True:
            self._check_stop()
            if time.monotonic() >= deadline:
                raise NetDaqProtocolError("USBDAQ sample stream timed out (no silent continuation)")
            # A polling timeout leaves the SAME native transfer pending. Reissuing
            # synchronous short-timeout reads could silently discard partial samples.
            n = self.lib.usb_reap_async_nocancel(self._context, 50)
            if n == -116:
                continue
            self._pending = False
            self._check(n, "read sample stream")
            if not 0 < n <= len(self._buffer):
                raise NetDaqProtocolError("USBDAQ empty/invalid sample block")
            return self._buffer.raw[:n]

    def prepare_stream(self, rate, channel_count):
        self._owner_check()
        if self._pending or self._context.value:
            raise NetDaqProtocolError("USBDAQ cannot resize an in-flight sample buffer")
        # Approximately 100 ms of data, in endpoint-sized multiples. Small native
        # reads bound normal Stop latency even for old drivers with broken abort.
        size = max(512, min(16384, int(rate * channel_count * 4 * .1) // 512 * 512))
        self._buffer = ct.create_string_buffer(size)

    def cancel_data(self):
        self._owner_check()
        if self._pending:
            deadline = time.monotonic() + DRAIN_SECONDS
            while time.monotonic() < deadline:
                if self.lib.usb_reap_async_nocancel(self._context, 50) != -116:
                    self._pending = False
                    break
        if self._pending:
            self._check(self.lib.usb_cancel_async(self._context), "cancel data read")
            deadline = time.monotonic() + CANCEL_CONFIRM_SECONDS
            while self.lib.usb_reap_async_nocancel(self._context, 50) == -116:
                if time.monotonic() >= deadline:
                    raise NetDaqProtocolError("USBDAQ native read cancellation unconfirmed; restart application/device")
            self._pending = False
        if self._context.value:
            self._check(self.lib.usb_free_async(ct.byref(self._context)), "free data read")
            self._context = ct.c_void_p()

    def stop(self):
        self._owner_check()
        self.cancel_data()
        event, self.stop_event = self.stop_event, None
        try:
            self.command(0x50)
        finally:
            self.stop_event = event

    def close(self):
        self._owner_check()
        error = None
        try:
            if self.handle and (self.may_be_running or self.stop_uncertain):
                self.stop()
            else:
                self.cancel_data()
        except Exception as exc:
            self.stop_uncertain = True
            error = exc
        finally:
            if self._pending:
                # Do not free a buffer/context that a broken native driver may
                # still be writing. Retain its lease and fail closed, not crash.
                if self not in _quarantined:
                    _quarantined.append(self)
            elif self.handle:
                self.lib.usb_release_interface(self.handle, 0)
                self.lib.usb_close(self.handle)
                self.handle = None
            if self._leased and not self._pending:
                self._leased = False
                _lease.release()
            if not self._pending and self in _quarantined:
                _quarantined.remove(self)
        if error is not None:
            raise NetDaqProtocolError(f"USBDAQ stop/cleanup unconfirmed: {error}") from error


class UsbStream:
    def __init__(self, control):
        self.control = control
        self.pending = bytearray()

    def receive(self, size, *, deadline=None):
        if not 0 < size <= 64 * 1024 * 1024:
            raise NetDaqProtocolError("USBDAQ invalid frame size")
        deadline = time.monotonic() + self.control.timeout if deadline is None else deadline
        while len(self.pending) < size:
            self.pending.extend(self.control.read_chunk(deadline))
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result

    def close(self):
        self.control.cancel_data()
        self.pending.clear()
