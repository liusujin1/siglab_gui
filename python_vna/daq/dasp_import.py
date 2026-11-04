"""Read DASP text parameters without interpreting unknown hardware enumerations."""
from __future__ import annotations

import configparser
from dataclasses import dataclass
import math
from pathlib import Path
import re

from .base import DaqBackendError


def read_parameters(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise DaqBackendError("DASP 参数文件编码无法识别")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise DaqBackendError(f"DASP 参数文件无效：{exc}") from exc
    return parser


def _number(section, key, default=None):
    value = float(section.get(key, default))
    if not math.isfinite(value):
        raise ValueError(f"{key} 必须是有限数")
    return value


def dasp_voltage_scale(section):
    """Voltage limits bound ADC quantization bins, not two inclusive endpoints."""
    low, high = _number(section, "Range from"), _number(section, "Range to")
    minimum, maximum = _number(section, "Voltage from"), _number(section, "Voltage to")
    factor = {"mv": .001, "v": 1.0}.get(section.get("Voltage Unit", "mV").strip().lower())
    if (factor is None or (low, high) != (-8388608, 8388607) or maximum <= minimum
            or int(section["AD Bit"]) != 24 or int(section.get("Buffer data type", "2")) != 2):
        raise DaqBackendError("DASP 回放仅支持参数完整的有符号 24 位 int32 原始电压记录")
    code_count = int(high - low + 1)
    step = (maximum - minimum) * factor / code_count
    if not math.isfinite(step) or step <= 0:
        raise DaqBackendError("DASP 电压量化步长无效")
    zero = low - minimum * factor / step
    if not math.isfinite(zero):
        raise DaqBackendError("DASP 电压零点无效")
    return {
        "adc_bits": 24, "adc_min": int(low), "adc_max": int(high), "adc_code_count": code_count,
        "voltage_min_v": minimum * factor, "voltage_max_v": maximum * factor,
        "volts_per_count": step, "zero_count": zero,
        "count_encoding": "signed_twos_complement", "basis": "dasp_declared_quantization",
        "electrical_calibration_verified": False,
    }


@dataclass(frozen=True)
class DaspChannel:
    index: int
    label: str
    enabled: bool
    unit: str
    eu_per_volt: float
    source_sensitivity: float
    warnings: tuple[str, ...]
    gain: float = 1.0
    external_gain: float = 1.0
    voltage_unit: str = "mV"


def load_dasp_channels(path, *, errors=None):
    parser = read_parameters(path)
    results = []
    for name in parser.sections():
        match = re.fullmatch(r"Channel (\d+)", name, re.I)
        if match:
            index = int(match[1])
        elif name.lower() == "setup" and Path(path).suffix.lower() == ".par":
            index = int(parser[name].get("Channel no", "1")) - 1
        else:
            continue
        section = parser[name]
        try:
            sensitivity = _number(section, "Sensitivity", "1")
            gain = _number(section, "Gain", "1")
            external_gain = _number(section, "External gain", "1")
            voltage_unit = section.get("Voltage Unit", "mV").strip().lower()
            volts = {"mv": .001, "v": 1.0, "uv": 1e-6}.get(voltage_unit)
            if (volts is None or sensitivity <= 0 or gain <= 0 or external_gain <= 0
                    or not 0 <= index < 64):
                raise ValueError("通道号、灵敏度、Gain、外部增益或电压单位无效")
            if _number(section, "Inner gain", "1") != 1:
                raise ValueError("Inner gain 的含义尚未确认，不能直接当作 Gain 倍数")
            denominator = sensitivity * volts * gain * external_gain
            if not math.isfinite(denominator) or denominator <= 0:
                raise ValueError("灵敏度与增益乘积超出有效范围")
            unit = section.get("Unit", "V").strip()
            if not unit:
                raise ValueError("工程单位不能为空")
            declared = section.get("Sensitivity Physical Unit", unit).strip()
            warnings = ["增益仅用于主机工程量换算，不写入硬件；不导入零漂、耦合、IEPE 和自动量程"]
            if declared.lower() != unit.lower():
                raise ValueError(f"灵敏度物理单位 {declared!r} 与测量单位 {unit!r} 不一致，不能安全换算")
            if section.get("Calibration enable", "0") != "0":
                raise ValueError("此通道启用了额外标定曲线，首版不导入")
            if section.get("bVirtualChannel", "0") != "0":
                raise ValueError("虚拟通道不能映射到物理 AI")
            if section.get("Polarity", "0") != "0":
                raise ValueError("负极性参数尚未确认，不自动导入")
            results.append(DaspChannel(index, section.get("Label") or section.get("Name", f"CH {index + 1}"),
                                       section.get("Used", "1") == "1", unit,
                                       1.0 / denominator,
                                       sensitivity, tuple(warnings), gain, external_gain,
                                       {"mv": "mV", "v": "V", "uv": "uV"}[voltage_unit]))
        except (ValueError, TypeError) as exc:
            message = f"{name}：{exc}"
            if errors is None:
                raise DaqBackendError(message) from exc
            errors.append(message)
    if not results:
        raise DaqBackendError("\n".join(errors or []) or "没有找到可导入的 [Channel n] 或 CH n.par [Setup]")
    return results


def apply_dasp_channels(channels, imported):
    """Only sensor scaling, labels, units and enable flags; never hardware state."""
    by_index = {c.index: c for c in imported}
    for channel in channels:
        if not re.fullmatch(r"ai\d+", channel.name):
            continue
        item = by_index.get(int(channel.name[2:]))
        if item is not None:
            channel.label, channel.enabled = item.label, item.enabled
            channel.sensitivity, channel.engineering_unit = item.eu_per_volt, item.unit
            channel.per_eu_mode = "/Volt"
