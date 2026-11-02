"""Experimental NetDAQ backend, gated by an independently verified device profile."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
import threading
import time

import numpy as np

from .base import BaseDaqBackend, BackendCapability, BackendDevice, BackendFrame, DaqBackendError
from .netdaq_protocol import (
    LegacyConnection, WireConnection, NetDaqProtocolError, NetDaqStopped,
    decode_counts, descriptor_data, sampling_divisor,
)


@dataclass(frozen=True)
class NetDaqSettings:
    host: str = ""
    legacy_port: int = 60000
    stream_port: int = 60003
    timeout: float = 2.0
    profile_path: str = ""
    transport: str = "tcp"
    usb_index: int = 0

    def __post_init__(self):
        if self.transport not in {"tcp", "usb"}:
            raise DaqBackendError("NetDAQ transport must be tcp or usb")
        if isinstance(self.usb_index, bool) or not isinstance(self.usb_index, int) or not 0 <= self.usb_index < 16:
            raise DaqBackendError("USBDAQ device index must be between 0 and 15")
        if (not isinstance(self.host, str) or len(self.host) > 253
                or any(c in self.host for c in "/\\\0 \r\n")):
            raise DaqBackendError("NetDAQ address must be an IP address or hostname, without a port")
        for port in (self.legacy_port, self.stream_port):
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise DaqBackendError("NetDAQ port must be between 1 and 65535")
        if not math.isfinite(self.timeout) or not 0.1 <= self.timeout <= 10:
            raise DaqBackendError("NetDAQ timeout must be between 0.1 and 10 seconds")


@dataclass(frozen=True)
class NetDaqProfile:
    """Explicit, per-device calibration. Nothing is inferred from sensor names."""
    model: str
    descriptor: bytes
    stream_format: str
    init_ad_payload: bytes
    divisor_offset: int
    max_sample_rate: float
    channels: tuple[dict, ...]
    digest: str
    evidence: str
    transport: str = "tcp"
    firmware_hex: str = ""

    @classmethod
    def load(cls, path: str | Path):
        try:
            raw = Path(path).read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
            if data.get("schema_version") != 1 or data.get("verified") is not True:
                raise ValueError("profile must have schema_version=1 and verified=true")
            model, evidence = str(data["model"]).strip(), str(data["evidence"]).strip()
            desc = bytes.fromhex(data["descriptor_hex"])
            descriptor_data(desc)
            transport = data.get("transport", "tcp")
            if transport not in {"tcp", "usb"}:
                raise ValueError("unsupported transport")
            fmt = data["stream_format"]
            if fmt not in {"packed24", "raw16", "raw24", "raw32"}:
                raise ValueError("unsupported stream format")
            if (fmt == "packed24") != (desc[7] == 3):
                raise ValueError("stream format disagrees with device descriptor")
            init = bytes.fromhex(data["init_ad_payload_hex"])
            offset = data["divisor_offset"]
            maximum = float(data["max_sample_rate"])
            channels = data["channels"]
            if transport == "usb":
                from .usbdaq import checked_reply, descriptor_info

                usb_info = descriptor_info(desc)
                checked_reply(bytes.fromhex(data["firmware_hex"]), 0x56, 7)
                if (fmt != "raw32"
                        or offset != 0 or not isinstance(channels, list) or not 1 <= len(channels) <= 4):
                    raise ValueError("USB2404 supports only qualified raw32/24-bit profiles with 1..4 channels")
                if maximum > usb_info["nominal_max_sample_rate"]:
                    raise ValueError("USBDAQ verified rate cannot exceed the reported device limit")
            if (not model or not evidence or len(init) != 13 or isinstance(offset, bool)
                    or not isinstance(offset, int) or not 0 <= offset <= 9
                    or not math.isfinite(maximum) or maximum <= 0
                    or not isinstance(channels, list) or not 1 <= len(channels) <= 64):
                raise ValueError("invalid model, evidence, InitAD template or channel count")
            wire_numbers = set()
            for index, channel in enumerate(channels):
                wire = channel["wire_number"]
                if (isinstance(wire, bool) or not isinstance(wire, int)
                        or not 0 <= wire <= 255 or wire in wire_numbers):
                    raise ValueError("wire channel numbers must be unique bytes")
                wire_numbers.add(wire)
                kind = channel["channel_type"]
                mode = channel["input_mode"]
                if (isinstance(kind, bool) or not isinstance(kind, int) or not 0 <= kind <= 255
                        or mode not in {"iepe", "voltage_single_ended_dc", "voltage_single_ended_ac",
                                        "voltage_differential_dc", "quarter_bridge", "half_bridge", "full_bridge"}):
                    raise ValueError("channel_type and independently verified input_mode are required")
                current = float(channel.get("iepe_current_ma", 0))
                if not math.isfinite(current) or current < 0 or (mode == "iepe" and current <= 0):
                    raise ValueError("IEPE current must be established independently, not encoded as coupling")
                options = channel["options"]
                if not isinstance(options, list) or not options:
                    raise ValueError(f"channel ai{index} has no verified range/coupling options")
                for option in options:
                    if "iepe_enabled" in option or "iepe_current_ma" in option:
                        raise ValueError("IEPE belongs to the reported channel mode, not a range/coupling option")
                    for key in ("full_scale_v", "volts_per_count", "zero_count"):
                        value = float(option[key])
                        if not math.isfinite(value):
                            raise ValueError(f"non-finite {key}")
                    if float(option["full_scale_v"]) <= 0 or float(option["volts_per_count"]) <= 0:
                        raise ValueError("range and calibration must be positive")
                    if option["coupling"] not in {"ac", "dc", "bias"}:
                        raise ValueError("invalid coupling")
                    if kind in {0, 6, 8} and transport != "usb":
                        for key in ("coupling_index", "gain_index"):
                            value = option[key]
                            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
                                raise ValueError(f"invalid {key}")
                    elif len(options) != 1 or not bytes.fromhex(option["configuration_hex"]):
                        raise ValueError("non-writable types need one captured, fixed configuration")
            if transport == "usb":
                wires = [c["wire_number"] for c in channels]
                if wires != sorted(wires) or any(n >= usb_info["channel_count"] for n in wires):
                    raise ValueError("USB2404 requires ascending channels within the reported physical count")
                mask = sum(1 << n for n in wires)
                if init[4:] != bytes((1,)) + struct.pack("<I", mask) + bytes(4):
                    raise ValueError("USB2404 captured channel mask disagrees with stream channel order")
            return cls(model, desc, fmt, init, offset, maximum, tuple(channels),
                       hashlib.sha256(raw).hexdigest(), evidence, transport, data.get("firmware_hex", ""))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DaqBackendError(f"NetDAQ device profile is invalid: {exc}") from exc

    def option(self, index: int, channel) -> dict:
        reported = self.channels[index]
        iepe = reported["input_mode"] == "iepe"
        if channel.iepe_enabled != iepe or (iepe and not math.isclose(
                float(reported["iepe_current_ma"]), channel.iepe_current_ma, abs_tol=1e-6)):
            raise DaqBackendError(f"NetDAQ {channel.name}: IEPE is a read-only channel mode, not a coupling switch")
        for option in reported["options"]:
            if (math.isclose(float(option["full_scale_v"]), channel.full_scale, rel_tol=1e-8)
                    and option["coupling"] == channel.coupling.lower()):
                return option
        raise DaqBackendError(
            f"NetDAQ {channel.name}: no verified option for range/coupling/IEPE; no settings were written")


class NetDaqBackend(BaseDaqBackend):
    """Free-run AI only until device-specific trigger/AO behavior is verified.

    The captured InitAD payload fixes the hardware stream channel mask/order.
    Disabled application channels are filtered on the host, not guessed mask bits.
    """
    def __init__(self, settings: NetDaqSettings | None = None):
        self.settings = settings or NetDaqSettings()
        self.profile = NetDaqProfile.load(self.settings.profile_path) if self.settings.profile_path else None
        if self.profile and self.profile.transport != self.settings.transport:
            raise DaqBackendError("Device profile transport does not match connection settings")
        self._cancel = threading.Event()
        self._external_stop: threading.Event | None = None
        self._control: LegacyConnection | None = None
        self._stream: WireConnection | None = None
        self._session = None
        self._owner: int | None = None
        self._active = False
        self._may_be_running = False
        self.stop_uncertain = False
        self._pending = bytearray()
        self._sample_index = 0
        self._frame_index = 0

    @property
    def device_name(self) -> str:
        if self.settings.transport == "usb":
            return f"USBDAQ@0547:1003:{self.settings.usb_index}"
        return f"NetDAQ@{self.settings.host}:{self.settings.legacy_port}"

    @property
    def ready(self) -> bool:
        return bool((self.settings.transport == "usb" or self.settings.host)
                    and self.profile and not self.stop_uncertain)

    def is_set(self):
        return self._cancel.is_set() or bool(self._external_stop and self._external_stop.is_set())

    def _check_stop(self):
        if self.is_set():
            raise NetDaqStopped("NetDAQ acquisition stopped")

    def _check_owner(self):
        if self._owner is not None and self._owner != threading.get_ident():
            raise DaqBackendError("NetDAQ resources must be accessed by their owning worker thread")

    def _connection(self, *, cancellable=True):
        if self.settings.transport == "usb":
            from .usbdaq import UsbConnection

            return UsbConnection(self.settings.usb_index, self.settings.timeout, self if cancellable else None)
        return LegacyConnection(self.settings.host, self.settings.legacy_port,
                                self.settings.timeout, self if cancellable else None)

    def probe(self, channel_numbers=(), *, stop_event=None) -> dict:
        if self.settings.transport != "usb" and not self.settings.host:
            raise DaqBackendError("请先设置 NetDAQ 设备 IP；未自动连接任何默认地址")
        channel_numbers = tuple(channel_numbers)
        if (len(channel_numbers) > 64 or len(set(channel_numbers)) != len(channel_numbers)
                or any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 255
                       for n in channel_numbers)):
            raise DaqBackendError("NetDAQ probe requires unique physical channel numbers in 0..255")
        control = self._connection(cancellable=False)
        control.stop_event = stop_event
        with control:
            raw = control.descriptor()
            usb_info = getattr(control, "device_info", None)
            if usb_info and any(n >= usb_info["channel_count"] for n in channel_numbers):
                raise DaqBackendError("USBDAQ probe channel exceeds the reported physical channel count")
            # Firmware is retained as raw bytes until its version encoding is verified.
            firmware = control.command(0x56, size=7)
            channels = {}
            for number in channel_numbers:
                kind, count = control.channel_descriptor(number)
                channels[str(number)] = {
                    "type": kind, "config_bytes": count,
                    "current_configuration_hex": control.channel_configuration(number).hex(),
                }
        matches = bool(self.profile and raw == self.profile.descriptor)
        if self.settings.transport == "usb":
            matches = matches and firmware.hex() == self.profile.firmware_hex
        return {"host": self.settings.host, "transport": self.settings.transport,
                "usb": getattr(control, "info", None), "descriptor_hex": raw.hex(),
                "usb_descriptor": usb_info,
                "firmware_hex": firmware.hex(), "channels": channels,
                "profile_matches": matches,
                "experimental": True}

    def list_devices(self):
        if self.settings.transport != "usb" and not self.settings.host:
            return []
        result = self.probe()
        profile = self.profile if result["profile_matches"] else None
        iepe = bool(profile and any(c["input_mode"] == "iepe" for c in profile.channels))
        return [BackendDevice(
            self.device_name, f"NetDAQ {profile.model if profile else '未标定 / 仅只读探测'} (experimental)",
            [f"{self.device_name}/ai{i}" for i in range(len(profile.channels))] if profile else [], [],
            BackendCapability(supports_iepe=iepe, max_ai_sample_rate=profile.max_sample_rate if profile else None),
        )]

    def configure(self, session, device_name=None):
        if self._owner is not None:
            self._check_owner()
            self.close()
        self._cancel.clear()
        if self.stop_uncertain:
            raise DaqBackendError("NetDAQ 上次停止未确认；请检查设备状态后重新应用连接设置")
        if not self.ready:
            raise DaqBackendError("NetDAQ 尚未标定：请先获取设备描述并导入经实机验证的设备配置；禁止采集")
        if device_name not in (None, self.device_name):
            raise DaqBackendError("NetDAQ selected device does not match connection settings")
        acq = session.acquisition
        if acq.excitation.enabled:
            raise DaqBackendError("NetDAQ AO is not verified and is disabled")
        if acq.trigger.enabled or acq.trigger.pretrigger_samples:
            raise DaqBackendError("NetDAQ trigger/pretrigger is not verified; use free-run only")
        if not 1 <= acq.frame_size <= 1048576:
            raise DaqBackendError("NetDAQ frame size is outside 1..1048576")
        profile = self.profile
        if acq.frame_size * len(profile.channels) * 8 > 64 * 1024 * 1024:
            raise DaqBackendError("NetDAQ frame allocation exceeds the 64 MiB safety limit")
        if not math.isfinite(acq.sample_rate) or acq.sample_rate > profile.max_sample_rate:
            raise DaqBackendError("NetDAQ requested sample rate exceeds the verified device limit")
        rate_encoder = sampling_divisor
        if self.settings.transport == "usb":
            from .usbdaq import sampling_divisor as rate_encoder

        divisor, actual = rate_encoder(profile.descriptor, acq.sample_rate)
        if actual > profile.max_sample_rate:
            raise DaqBackendError("NetDAQ coerced sample rate exceeds the verified device limit")
        selected = []
        for channel in session.ai_channels:
            if not channel.enabled:
                continue
            if not channel.name.startswith("ai") or not channel.name[2:].isdigit():
                raise DaqBackendError(f"Unsupported NetDAQ channel name {channel.name}")
            index = int(channel.name[2:])
            if index >= len(profile.channels) or any(entry[0] == index for entry in selected):
                raise DaqBackendError(f"Invalid or duplicate NetDAQ channel {channel.name}")
            selected.append((index, channel.name, profile.option(index, channel)))
        if not selected:
            raise DaqBackendError("NetDAQ requires at least one enabled channel")
        self._owner = threading.get_ident()
        self._control = self._connection().connect()
        try:
            raw = self._control.descriptor()
            if raw != profile.descriptor:
                raise DaqBackendError("NetDAQ descriptor differs from verified profile; no settings were written")
            if self.settings.transport == "usb":
                firmware = self._control.command(0x56, size=7)
                if firmware.hex() != profile.firmware_hex:
                    raise DaqBackendError("USBDAQ firmware differs from verified profile; no settings were written")
        except Exception:
            self.close()
            raise
        self._session = session
        self._selected = selected
        self._divisor = divisor
        self._requested_rate = acq.sample_rate
        self._rate = actual
        # Existing controller/Record headers use this same AcquisitionConfig object.
        acq.sample_rate = actual
        self._pending.clear()
        self._sample_index = self._frame_index = 0

    def start(self):
        self._check_owner()
        self._check_stop()
        if self._session is None or self._control is None:
            raise DaqBackendError("NetDAQ must be configured before starting")
        if self._active:
            raise DaqBackendError("NetDAQ is already running")
        profile = self.profile
        try:
            # Channel type is device-owned. Validate ALL selected types before
            # writing anything; input mode is never switched via !C.
            for index, name, option in self._selected:
                entry = profile.channels[index]
                kind, _count = self._control.channel_descriptor(entry["wire_number"])
                if kind != entry["channel_type"]:
                    raise DaqBackendError(f"NetDAQ {name}: reported channel type changed; no settings were written")
                if kind not in {0, 6, 8} or self.settings.transport == "usb":
                    current = self._control.channel_configuration(entry["wire_number"])
                    if current != bytes.fromhex(option["configuration_hex"]):
                        raise DaqBackendError(f"NetDAQ {name}: read-only channel configuration changed")
            for index, name, option in self._selected:
                self._check_stop()
                entry = profile.channels[index]
                if entry["channel_type"] in {0, 6, 8} and self.settings.transport != "usb":
                    self._control.set_channel(entry["wire_number"],
                                              option["coupling_index"], option["gain_index"],
                                              expected_type=entry["channel_type"])
            template = bytearray(profile.init_ad_payload)
            struct.pack_into(">I" if self.settings.transport == "usb" else "<I",
                             template, profile.divisor_offset, self._divisor)
            self._control.initialize_ad(bytes(template))
            if self.settings.transport == "usb":
                from .usbdaq import UsbStream

                self._control.prepare_stream(self._rate, len(profile.channels))
                self._stream = UsbStream(self._control)
            else:
                self._stream = WireConnection(self.settings.host, self.settings.stream_port,
                                              self.settings.timeout, self).connect()
            self._check_stop()
            # A start request may take effect even if its acknowledgement is lost.
            self._may_be_running = True
            self._control.start()
            self._active = True
        except Exception:
            self.stop()
            raise

    def read_frame(self):
        self._check_owner()
        self._check_stop()
        if not self._active:
            raise DaqBackendError("NetDAQ is not running")
        profile = self.profile
        bits = int(profile.stream_format[-2:])
        count = len(profile.channels)
        frame_size = self._session.acquisition.frame_size
        needed = frame_size * count * (bits // 8)
        startup = 5.0 if self.settings.transport == "usb" and self._frame_index == 0 else 0.0
        deadline = time.monotonic() + startup + frame_size / self._rate + self.settings.timeout
        while len(self._pending) < needed:
            self._check_stop()
            if profile.stream_format == "packed24":
                magic, size = struct.unpack("<IH", self._stream.receive(6, deadline=deadline))
                if magic != 0xFFFFAAAA or not size or size % 3:
                    raise NetDaqProtocolError("NetDAQ malformed packed24 stream block")
                self._pending.extend(self._stream.receive(size, deadline=deadline))
            else:
                self._pending.extend(self._stream.receive(needed - len(self._pending), deadline=deadline))
        values = decode_counts(bytes(self._pending[:needed]), bits).reshape(frame_size, count).T
        del self._pending[:needed]
        data = np.vstack([(values[index].astype(np.float64) - float(option["zero_count"]))
                          * float(option["volts_per_count"]) for index, name, option in self._selected])
        frame = BackendFrame(
            self._rate, [name for index, name, option in self._selected], data,
            np.arange(frame_size, dtype=float) / self._rate, self._frame_index,
            {"backend": "netdaq", "experimental": True, "device_model": profile.model,
             "transport": self.settings.transport,
             "profile_sha256": profile.digest, "requested_sample_rate": self._requested_rate,
             "actual_sample_rate": self._rate, "sample_start_index": self._sample_index,
             "channel_modes": {name: {"channel_type": profile.channels[index]["channel_type"],
                                       "wire_number": profile.channels[index]["wire_number"],
                                       "input_mode": profile.channels[index]["input_mode"]}
                               for index, name, option in self._selected},
             "units": "V", "stream_continuity_verified": False},
        )
        self._sample_index += frame_size
        self._frame_index += 1
        return frame

    def request_stop(self):
        self._cancel.set()

    def set_stop_event(self, stop_event):
        self._external_stop = stop_event

    def stop(self):
        self._check_owner()
        self._cancel.set()
        error = None
        try:
            if self._may_be_running:
                # A fresh legacy connection avoids reusing a partial/cancelled reply.
                control = self._control
                connected = (getattr(control, "connected", False) if self.settings.transport == "usb"
                             else control is not None and control.sock is not None)
                if not connected:
                    control = self._connection(cancellable=False).connect()
                    self._control = control
                control.stop_event = None
                control.timeout = min(self.settings.timeout, 0.5)
                control.stop()
        except Exception as exc:
            error = exc
        finally:
            self._active = self._may_be_running = False
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception as exc:
                    error = error or exc
                self._stream = None
            if self._control is not None:
                try:
                    self._control.close()
                except Exception as exc:
                    error = error or exc
                self._control = None
            self._pending.clear()
        if error:
            self.stop_uncertain = True
            raise DaqBackendError(f"NetDAQ stop was not acknowledged; check device state: {error}") from error

    def close(self):
        self._check_owner()
        try:
            self.stop()
        finally:
            self._owner = None
            self._session = None

    def abort(self):
        self.request_stop()
