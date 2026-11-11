"""Reuse declared DASP parameters without inventing electrical mode mappings."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import tempfile

from .base import DaqBackendError
from .dasp_import import apply_dasp_channels, DaspChannel, dasp_voltage_scale, load_dasp_channels, read_parameters
from .usbdaq import checked_reply, descriptor_info, sample_clock


def validate_dasp_declaration(data, info):
    """A declaration stays unverified and cannot broaden the USB wire contract."""
    firmware = bytes.fromhex(data["firmware_hex"])
    if (info["channel_count"] != 4 or len(data["channels"]) != 4
            or [entry["wire_number"] for entry in data["channels"]] != list(range(4))
            or firmware[3:5] != b"\x02\x00"
            or (info["nominal_voltage_min_mv"], info["nominal_voltage_max_mv"]) != (-10000, 10000)):
        raise ValueError("DASP compatibility requires the captured four-channel USB2404/2.0 layout")
    source = data.get("vendor_parameters")
    if (not isinstance(source, dict) or source.get("format") != "dasp_parameter_snapshot"
            or not isinstance(source.get("path"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", "")))
            or not isinstance(source.get("sections"), dict)
            or source.get("electrical_calibration_verified") is not False):
        raise ValueError("DASP parameter source, checksum and unverified status are required")
    step = info["nominal_volts_per_count"]
    low = info["nominal_voltage_min_mv"] * .001
    high = info["nominal_voltage_max_mv"] * .001
    zero = info["adc_min"] - low / step
    for entry in data["channels"]:
        if entry["input_mode"] != "device_fixed_unknown":
            raise ValueError("DASP compatibility cannot assert a known electrical mode or IEPE supply")
        for option in entry["options"]:
            if (option["coupling"] != "dc"
                    or not math.isclose(float(option["full_scale_v"]), max(abs(low), abs(high)), rel_tol=1e-12)
                    or not math.isclose(float(option["volts_per_count"]), step, rel_tol=1e-12)
                    or not math.isclose(float(option["zero_count"]), zero, abs_tol=1e-12)):
                raise ValueError("DASP compatibility must retain nominal ADC volts and raw DC")
    clock, factor = sample_clock(bytes.fromhex(data["descriptor_hex"]))
    if float(data["max_sample_rate"]) > min(info["nominal_max_sample_rate"], clock / factor):
        raise ValueError("DASP compatibility rate exceeds the USB divisor clock")
    indexes = set()
    for item in source.get("sensor_defaults", []):
        expected = sensor_from_snapshot(item)
        if expected.index >= len(data["channels"]) or expected.index in indexes:
            raise ValueError("DASP sensor default exceeds the reported physical channel count")
        indexes.add(expected.index)


def sensor_from_snapshot(item):
    channel = DaspChannel(**{**item, "warnings": tuple(item.get("warnings", ()))})
    unit = {"mv": .001, "v": 1.0, "uv": 1e-6}.get(channel.voltage_unit.lower())
    values = (channel.source_sensitivity, channel.gain, channel.external_gain, channel.eu_per_volt)
    if (isinstance(channel.index, bool) or not isinstance(channel.index, int) or channel.index < 0
            or unit is None or any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values)
            or not isinstance(channel.unit, str) or not channel.unit
            or not math.isclose(channel.eu_per_volt,
                                1 / (channel.source_sensitivity * channel.gain * channel.external_gain * unit),
                                rel_tol=1e-12)):
        raise ValueError("DASP sensor snapshot has inconsistent scaling")
    return channel


def build_dasp_usb_profile(path, probe):
    """Build a compatibility declaration from vendor files and a read-only probe."""
    path = Path(path).resolve()
    if path.suffix.lower() not in {".ini", ".par"}:
        raise DaqBackendError("请选择 DASP Channel.ini 或 CH n.par 参数文件")
    before = path.read_bytes()
    if len(before) > 2 * 1024 * 1024:
        raise DaqBackendError("DASP 参数文件超过 2 MiB")
    parameters = read_parameters(path)
    try:
        if not any(re.fullmatch(r"Channel \d+", name, re.I) for name in parameters.sections()) and not (
                path.suffix.lower() == ".par" and parameters.has_section("Setup")):
            raise ValueError("文件中没有 DASP 通道参数")
        raw = bytes.fromhex(probe["descriptor_hex"])
        info = descriptor_info(raw)
        firmware = bytes.fromhex(probe["firmware_hex"])
        checked_reply(firmware, 0x56, 7)
        if (probe.get("transport") != "usb" or probe.get("usb", {}).get("product") != "USB2404V10"
                or firmware[3:5] != b"\x02\x00" or info["channel_count"] != 4
                or (info["nominal_voltage_min_mv"], info["nominal_voltage_max_mv"]) != (-10000, 10000)):
            raise ValueError("仅支持已审阅的 USB2404V10 / 2.0 / 四通道 / 标称 ±10 V 声明")
        step = info["nominal_volts_per_count"]
        if path.suffix.lower() == ".par":
            scale = dasp_voltage_scale(parameters["Setup"])
            if (not math.isclose(scale["volts_per_count"], step, rel_tol=1e-12)
                    or scale["zero_count"] != 0):
                raise ValueError("DASP 参数中的 ADC 换算与当前设备声明不一致")
        errors = []
        try:
            sensors = load_dasp_channels(path, errors=errors)
        except DaqBackendError as exc:
            sensors = []
            if not errors:
                errors.append(str(exc))
        sensors = [s for s in sensors if s.index < info["channel_count"]]
        entries = []
        for number in range(info["channel_count"]):
            entry = probe["channels"][str(number)]
            cfg = bytes.fromhex(entry["current_configuration_hex"])
            if len(cfg) != entry["config_bytes"] or not cfg:
                raise ValueError(f"CH{number + 1} 当前配置长度与通道描述不一致")
            entries.append(dict(
                wire_number=number, channel_type=entry["type"], input_mode="device_fixed_unknown",
                options=[dict(full_scale_v=10.0, coupling="dc", volts_per_count=step,
                              zero_count=0.0, configuration_hex=cfg.hex())]))
        if path.read_bytes() != before:
            raise ValueError("DASP 参数文件在读取期间发生变化，请重新导入")
        clock, factor = sample_clock(raw)
        data = dict(
            schema_version=1, verified=False, qualification="dasp_parameters", transport="usb",
            model="USB2404V10", descriptor_hex=raw.hex(), firmware_hex=firmware.hex(),
            stream_format="raw32", divisor_offset=0,
            max_sample_rate=min(info["nominal_max_sample_rate"], clock / factor),
            init_ad_payload_hex=(struct.pack(">I", 1) + b"\x01" + struct.pack("<I", 15) + bytes(4)).hex(),
            channels=entries,
            evidence="DASP declared ADC conversion plus checked USB readback; electrical modes "
                     "remain unknown. No channel writes, zero-drift subtraction or recalibration.",
            vendor_parameters=dict(
                format="dasp_parameter_snapshot", path=str(path), sha256=hashlib.sha256(before).hexdigest(),
                imported_utc=datetime.now(timezone.utc).isoformat(),
                electrical_calibration_verified=False, conversion_basis="dasp_declared_nominal_voltage",
                sections={name: dict(parameters[name]) for name in parameters.sections()},
                sensor_defaults=[asdict(s) for s in sensors], sensor_import_errors=errors,
                note="dc means preserve raw DC in software; hardware coupling and IEPE remain unknown."))
        validate_dasp_declaration(data, info)
        return data
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DaqBackendError(f"DASP USB 参数无法建立兼容配置：{exc}") from exc


def save_dasp_usb_profile(data, target):
    """Validate the serialized declaration before atomically replacing its target."""
    from .netdaq import NetDaqProfile

    target = Path(target)
    source_path = data.get("vendor_parameters", {}).get("path")
    if target.suffix.lower() != ".json" or (
            isinstance(source_path, str) and target.resolve() == Path(source_path).resolve()):
        raise DaqBackendError("兼容配置必须保存为独立 JSON，不能覆盖 DASP 原参数")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", dir=target.parent,
                prefix="tmp_dasp_", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, indent=2, ensure_ascii=True, allow_nan=False)
        NetDaqProfile.load(temporary)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_dasp_profile_sensors(channels, profile):
    if profile.qualification == "dasp_parameters":
        apply_dasp_channels(channels, [
            sensor_from_snapshot(item) for item in profile.vendor_parameters.get("sensor_defaults", [])])
