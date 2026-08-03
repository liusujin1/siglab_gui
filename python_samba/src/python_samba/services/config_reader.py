"""SAMBA19x_Config XML reader/writer.

Exact mirror of SAMBA19xLib.XmlToolSettings (decompiled C#).

ReadFlag=True → read from XML into SambaConfig
ReadFlag=False → write from SambaConfig to XML
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from python_samba import __version__
from python_samba.transport.serial_port import TransportError


# Fixed names matching SAMBA19xLabels / FixedNames
VEL_AXES = ["Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"]
POS_AXES = ["Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
            "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2"]
PNEU_AXES = ["Ztpneu", "Yrpneu", "Xrpneu"]
VEL_INPUT_NAMES = ["Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "X4FB", "Z4FB"]
VEL7_INPUT_NAMES = ["X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "Z4FB"]
VEL_OUTPUT_NAMES = ["OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
                    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4"]
MOTOR_OFFSET_NAMES = ["OutY1", "OutX2", "OutY3", "OutX4", "OutY2", "OutX1",
                      "OutY4", "OutX3", "Iso1", "Iso2", "Iso3"]
ADC_INPUT_NAMES = ["X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
                    "Xff", "Yff", "Zff", "Prox1", "Prox2", "Prox3", "ProxH1",
                    "ProxH2", "ProxH3", "Xpos", "Xacc", "Ypos", "Yacc",
                    "Y2FB", "X3FB", "X4FB", "Y4FB", "Z4FB", "InpProx4",
                    "InpProxH4", "Auxiliary1", "Auxiliary2", "Auxiliary3",
                    "Auxiliary4", "Auxiliary5"]
DAC_OUTPUT_NAMES = ["OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
                     "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
                     "OutV1", "OutV2", "OutV3", "OutHV1", "OutHV2", "OutHV3",
                     "Diag0", "Diag1"]
MOTOR_NAMES = ["OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
               "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4"]
CONFIG_XML_VERSION = 8
GENERATOR_COMMENT = (
    f"This document was generated withpython_samba, Version={__version__}"
)


@dataclass
class FilterTypeXml:
    """FilterType XML node: Type + Par0..Par4 as attributes."""
    type: int = 0
    par: list[float] = field(default_factory=lambda: [0.0] * 5)


@dataclass
class IOTypeXml:
    """IOType XML node: Type + MainIndex + SubIndex as attributes."""
    type: int = 0
    main_index: int = 0
    sub_index: int = 0


@dataclass
class SambaConfig:
    """Complete controller configuration from a .SAMBA19x_Config file."""

    # Version
    firmware_version: str = ""
    xml_file_version: int = 8
    system_configuration: str = ""

    # SystemSetting
    loop_status: int = 0
    individual_loop_status: int = 63
    pos_individual_loop_status: int = 0
    pneum_individual_loop_status: int = 0
    motors_limit: int = 100
    excitation_type: int = 0
    excitation_params: list[float] = field(default_factory=lambda: [0.0] * 4)
    sample_frequency: float = 5000.0
    diag_io_signal_0: IOTypeXml = field(default_factory=IOTypeXml)
    diag_io_signal_1: IOTypeXml = field(default_factory=IOTypeXml)
    noise_injection_signal: IOTypeXml = field(default_factory=IOTypeXml)
    output_ramp_type: int = 0
    output_ramp_time: float = 1.0
    firmware_configuration: int = 0
    excitation_filters: list[FilterTypeXml] = field(default_factory=lambda: [FilterTypeXml() for _ in range(4)])

    # PerformanceMonitorSetting
    perf_monitor_signal: IOTypeXml = field(default_factory=IOTypeXml)
    perf_min_time: float = 0.1
    perf_hold_time: float = 1.0
    perf_threshold: int = 10000

    # MotorOvercurrentSetting
    motor_cooling_constant: float = 0.0002
    reset_delay_time: float = 10.0
    disable_all_flag: int = 1
    motor_thresholds: list[float] = field(default_factory=lambda: [50.0] * 12)

    # AutoLoopSwitchSetting
    loop_switch_config: int = 0
    switch_io_signal: IOTypeXml = field(default_factory=IOTypeXml)
    switch_trigger_level: int = 70
    switch_hold_time: float = 15.0
    switch_min_trigger_time: float = 0.5

    # VelocityLoopSettings
    vel_axis_output_limiter: list[float] = field(default_factory=lambda: [100000.0] * 6)
    vel_sensor_matrix: dict[str, list[float]] = field(default_factory=dict)
    vel_motor_matrix: dict[str, list[float]] = field(default_factory=dict)
    vel_filters: dict[str, list[FilterTypeXml]] = field(default_factory=dict)

    # PositionLoopSettings
    prox_offsets: list[float] = field(default_factory=lambda: [0.0] * 8)
    pos_sensor_devices: dict[str, list[IOTypeXml]] = field(default_factory=dict)
    pos_sensor_matrix: dict[str, list[float]] = field(default_factory=dict)
    pos_motor_devices: dict[str, list[IOTypeXml]] = field(default_factory=dict)
    pos_motor_matrix: dict[str, list[float]] = field(default_factory=dict)
    pos_filters: dict[str, list[FilterTypeXml]] = field(default_factory=dict)

    # PneumaticLoopSettings
    pneum_sensor_matrix: dict[str, list[float]] = field(default_factory=dict)
    pneum_motor_matrix: dict[str, list[float]] = field(default_factory=dict)
    pneum_filters: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    pneum_up_valve_offsets: list[float] = field(default_factory=lambda: [0.0] * 8)
    pneum_down_valve_offsets: list[float] = field(default_factory=lambda: [0.0] * 8)
    pneum_setpoint: int = 0
    pneum_soft_up_height: int = 0
    pneum_mode_tolerance: int = 0
    pneum_use_setpoint_all: int = 0
    dither_value: float = 0.0
    dither_frequency: int = 0
    dither_compensation: float = 0.0
    motor_offset: list[float] = field(default_factory=lambda: [0.0] * 11)
    pneum_ramp_switch_to_up: int = 0
    pneum_ramp_setpoint_gradient: float = 0.0
    pneum_ramp_up_gradient: float = 0.0
    pneum_ramp_down_gradient: float = 0.0
    pneum_ramp_pressure_offset_time: float = 0.0
    linear_motor_offsets: list[float] = field(default_factory=lambda: [0.0] * 12)

    # AD-DA-Mapping
    adc_channel_set_num: int = 0
    adc_mapping: list[int] = field(default_factory=lambda: list(range(32)))
    dac_mapping: list[int] = field(default_factory=lambda: list(range(20)))
    temp_sensor_adc_mapping: list[int] = field(default_factory=lambda: list(range(12)))

    # PowerSupplyCurrentLimitationSetting (XML file version 8)
    power_supply_current_limit: float = 0.0
    power_supply_current_si_unit: float = 0.0

    # Feed-Forward-Setting
    ff_no_gains: int = 5
    ff_output_threshold: int = 100
    ff_xpos_max: float = 0.0
    ff_ypos_max: float = 0.0
    ff_xpos_offset: float = 0.0
    ff_ypos_offset: float = 0.0
    ff_mult_xacc: float = 1.0
    ff_mult_yacc: float = 1.0
    ff_mult_xpos: float = 1.0
    ff_mult_ypos: float = 1.0
    ff_output_matrix: list[int] = field(default_factory=lambda: [0] * 7)
    ff_adaption_rate: list[float] = field(default_factory=lambda: [0.0] * 7)
    ff_source_inputs: list[int] = field(default_factory=lambda: [0] * 7)
    ff_ref_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    ff_sec_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    ff_err_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    ff_gains: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    # PFF
    pff_no_gains: int = 5
    pff_output_threshold: int = 100
    pff_output_matrix: list[int] = field(default_factory=lambda: [0] * 4)
    pff_adaption_rate: list[float] = field(default_factory=lambda: [0.0] * 4)
    pff_source_inputs: list[int] = field(default_factory=lambda: [0] * 4)
    pff_ref_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    pff_sec_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    pff_err_filter: dict[str, list[FilterTypeXml]] = field(default_factory=dict)
    pff_gains: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    # NonlinearPosition
    nlp_mode: int = 0
    nlp_reset_pid: int = 0
    nlp_dead_band: float = 0.0
    nlp_rise_range: float = 0.0

    # CascadedPosition
    cascaded_hysterese: float = 0.0
    cascaded_filters: list[FilterTypeXml] = field(default_factory=lambda: [FilterTypeXml() for _ in range(3)])

    # TraceSetting
    trace_io_0: IOTypeXml = field(default_factory=IOTypeXml)
    trace_io_1: IOTypeXml = field(default_factory=IOTypeXml)
    trace_filter_flag: int = 0
    trace_no_samples: int = 1024
    trace_undersample: int = 1

    # EventLoggingSetting
    event_logging_type: int = 0
    event_used_io_signal_num: int = 1
    event_samples_num: int = 1
    event_undersample: int = 1
    event_delay_samples_num: int = 1
    event_average: int = 0
    event_min_trigger_samples: int = 1
    event_threshold: float = 0.0
    event_io_signal: IOTypeXml = field(default_factory=IOTypeXml)
    event_monitor_signals: list[IOTypeXml] = field(
        default_factory=lambda: [IOTypeXml() for _ in range(40)]
    )

    # ZMS
    zms_thresholds: list[float] = field(default_factory=lambda: [0.0] * 12)

    # Runtime-only diagnostics; not serialized into the controller file.
    capture_warnings: list[str] = field(default_factory=list, repr=False)


# ======================================================================
# Reader
# ======================================================================

def _get_attr(elem: ET.Element | None, name: str, default: Any = 0) -> Any:
    if elem is None:
        return default
    v = elem.get(name)
    if v is None:
        return default
    return v


def _float(v: str) -> float:
    """Parse float in scientific notation (0.000000E+00)."""
    return float(v)


def _int(v: str) -> int:
    return int(v)


def _read_io_type(parent: ET.Element | None, name: str) -> IOTypeXml:
    """Read IOType from XML attributes: Type, MainIndex, SubIndex."""
    io = IOTypeXml()
    if parent is None:
        return io
    node = parent.find(name)
    if node is None:
        return io
    io.type = _int(_get_attr(node, "Type", "0"))
    io.main_index = _int(_get_attr(node, "MainIndex", "0"))
    io.sub_index = _int(_get_attr(node, "SubIndex", "0"))
    return io


def _read_io_axis(
    parent: ET.Element | None,
    axis_name: str,
    item_prefix: str,
    count: int,
) -> list[IOTypeXml]:
    """Read one axis worth of IOType nodes from the vendor XML layout."""
    if parent is None:
        return []
    axis = parent.find(axis_name)
    if axis is None:
        return []
    values: list[IOTypeXml] = []
    for index in range(count):
        name = f"{item_prefix}{index}"
        node = axis.find(name)
        # Older hand-edited files sometimes changed only the first letter's
        # case.  The original writer uses InputN and outputN respectively.
        if node is None:
            alternate = name[:1].swapcase() + name[1:]
            node = axis.find(alternate)
        if node is None:
            values.append(IOTypeXml())
        else:
            values.append(
                IOTypeXml(
                    _int(_get_attr(node, "Type", "0")),
                    _int(_get_attr(node, "MainIndex", "0")),
                    _int(_get_attr(node, "SubIndex", "0")),
                )
            )
    return values


def _read_filter_type(parent: ET.Element | None, name: str) -> FilterTypeXml:
    """Read FilterType from XML attributes: Type, Par0..Par4."""
    fil = FilterTypeXml()
    if parent is None:
        return fil
    node = parent.find(name)
    if node is None:
        return fil
    fil.type = _int(_get_attr(node, "Type", "0"))
    fil.par = [_float(_get_attr(node, f"Par{i}", "0")) for i in range(5)]
    return fil


def _read_matrix_axis(parent: ET.Element | None, name: str, channels: list[str]) -> list[float] | None:
    """Read a matrix row with named channel attributes."""
    if parent is None:
        return None
    node = parent.find(name)
    if node is None:
        return None
    vals = []
    for ch in channels:
        v = node.get(ch)
        if v is not None:
            vals.append(_float(v))
        else:
            vals.append(0.0)
    return vals


def _read_matrix_axis_io(parent: ET.Element | None, name: str, n: int) -> list[IOTypeXml] | None:
    """Read IOType matrix row."""
    if parent is None:
        return None
    node = parent.find(name)
    if node is None:
        return None
    vals = []
    for i in range(n):
        io = IOTypeXml()
        sub = node.find(f"Input{i}")
        if sub is not None:
            io.type = _int(_get_attr(sub, "Type", "0"))
            io.main_index = _int(_get_attr(sub, "MainIndex", "0"))
            io.sub_index = _int(_get_attr(sub, "SubIndex", "0"))
        vals.append(io)
    return vals


def _read_filter_axis(parent: ET.Element | None, name: str, n: int,
                       prefix: str = "Filt_") -> list[FilterTypeXml] | None:
    """Read filter stages for one axis."""
    if parent is None:
        return None
    node = parent.find(name)
    if node is None:
        return None
    filters = []
    for i in range(n):
        fil = _read_filter_type(node, f"{prefix}{i}")
        filters.append(fil)
    return filters


def load_config(path: str | Path) -> SambaConfig:
    """Load a .SAMBA19x_Config XML file (ReadFlag=True)."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    if root.tag != "SAMBA1_9_X_Configuration":
        raise ValueError(f"Unexpected root tag: {root.tag}")

    cfg = SambaConfig()

    # Basic info
    fw = root.find("FirmwareVersionInfo")
    if fw is not None and fw.text:
        cfg.firmware_version = fw.text
    ver = root.find("XML_File_Version")
    if ver is not None and ver.text:
        cfg.xml_file_version = _int(ver.text)
    sc = root.find("SystemConfiguration")
    if sc is not None and sc.text:
        cfg.system_configuration = sc.text

    # === SystemSetting ===
    ss = root.find("SystemSetting")
    if ss is not None:
        _read_int(ss, "LoopStatus", cfg, "loop_status")
        _read_int(ss, "IndividualLoopStatus", cfg, "individual_loop_status")
        _read_int(ss, "PosIndividualLoopStatus", cfg, "pos_individual_loop_status")
        _read_int(ss, "PneumIndividualLoopStatus", cfg, "pneum_individual_loop_status")
        _read_int(ss, "MotorsLimit", cfg, "motors_limit")
        _read_int(ss, "ExcitationType", cfg, "excitation_type")
        _read_float(ss, "ExcitationParam0", cfg, "excitation_params", 0)
        _read_float(ss, "ExcitationParam1", cfg, "excitation_params", 1)
        _read_float(ss, "ExcitationParam2", cfg, "excitation_params", 2)
        _read_float(ss, "ExcitationParam3", cfg, "excitation_params", 3)
        _read_float_text(ss, "SampleFrequency", cfg, "sample_frequency")
        cfg.diag_io_signal_0 = _read_io_type(ss, "DiagIOSignal0")
        cfg.diag_io_signal_1 = _read_io_type(ss, "DiagIOSignal1")
        cfg.noise_injection_signal = _read_io_type(ss, "NoiseInjectionIOSignal")
        _read_int(ss, "OutputRampType", cfg, "output_ramp_type")
        _read_float_text(ss, "OutputRampTime", cfg, "output_ramp_time")
        _read_int(ss, "FirmwareConfiguration", cfg, "firmware_configuration")
        ef = ss.find("ExcitationFilter")
        if ef is not None:
            for i in range(4):
                fil = _read_filter_type(ef, f"Fil{i}")
                if i < len(cfg.excitation_filters):
                    cfg.excitation_filters[i] = fil

    # === PerformanceMonitorSetting ===
    pm = root.find("PerformanceMonitorSetting")
    if pm is not None:
        cfg.perf_monitor_signal = _read_io_type(pm, "PerfMonitorIOSignal")
        _read_float_text(pm, "PerfMinTime", cfg, "perf_min_time")
        _read_float_text(pm, "PerfHoldTime", cfg, "perf_hold_time")
        _read_int(pm, "PerfThreshold", cfg, "perf_threshold")

    # === MotorOvercurrentSetting ===
    mo = root.find("MotorOvercurrentSetting")
    if mo is not None:
        _read_float_text(mo, "MotorOverCurrentCoolingConstant", cfg, "motor_cooling_constant")
        _read_float_text(mo, "ResetDelayTime", cfg, "reset_delay_time")
        _read_int(mo, "DisableAllFlag", cfg, "disable_all_flag")
        mt = mo.find("MotorThresholds")
        if mt is not None:
            for i, name in enumerate(MOTOR_NAMES):
                v = mt.get(name)
                if v is not None:
                    cfg.motor_thresholds[i] = _float(v)

    # === AutoLoopSwitchSetting ===
    als = root.find("AutoLoopSwitchSetting")
    if als is not None:
        _read_int(als, "LoopSwitchConfig", cfg, "loop_switch_config")
        cfg.switch_io_signal = _read_io_type(als, "SwitchIOSignal")
        _read_int(als, "SwitchTriggerLevel", cfg, "switch_trigger_level")
        _read_float_text(als, "SwitchHoldTime", cfg, "switch_hold_time")
        _read_float_text(als, "SwitchMinTriggerTime", cfg, "switch_min_trigger_time")

    # === VelocityLoopSettings ===
    vl = root.find("VelocityLoopSettings")
    if vl is not None:
        lim = vl.find("VelAxisOutputLimiter")
        if lim is not None:
            for i, name in enumerate(VEL_AXES):
                v = lim.get(name)
                if v is not None:
                    cfg.vel_axis_output_limiter[i] = _float(v)
        sm = vl.find("SensorMatrix")
        if sm is not None:
            for name in VEL_AXES:
                vals = _read_matrix_axis(sm, name, VEL_INPUT_NAMES)
                if vals:
                    cfg.vel_sensor_matrix[name] = vals
        mm = vl.find("MotorMatrix")
        if mm is not None:
            for name in VEL_AXES:
                vals = _read_matrix_axis(mm, name, VEL_OUTPUT_NAMES)
                if vals:
                    cfg.vel_motor_matrix[name] = vals
        fs = vl.find("FilterSetting")
        if fs is not None:
            for name in VEL_AXES:
                filts = _read_filter_axis(fs, name, 7, "Filt_")
                if filts:
                    cfg.vel_filters[name] = filts

    # === PositionLoopSettings ===
    pl = root.find("PositionLoopSettings")
    if pl is not None:
        po = pl.find("ProximityOffsets")
        if po is not None:
            prox_names = ["ProxV1", "ProxV2", "ProxV3", "ProxH1", "ProxH2", "ProxH3", "ProxV4", "ProxH4"]
            for i, name in enumerate(prox_names):
                v = po.get(name)
                if v is not None:
                    cfg.prox_offsets[i] = _float(v)
        used_inputs = pl.find("SensorMatrixUsedInput")
        for name in POS_AXES[:12]:
            devices = _read_io_axis(used_inputs, name, "Input", 6)
            if devices:
                cfg.pos_sensor_devices[name] = devices
        sm = pl.find("SensorMatrix")
        if sm is not None:
            for name in POS_AXES[:12]:
                vals = _read_matrix_axis(sm, name, ["Input0", "Input1", "Input2", "Input3", "Input4", "Input5"])
                if vals:
                    cfg.pos_sensor_matrix[name] = vals
        used_outputs = pl.find("MotorMatrixUsedOutput")
        for name in POS_AXES[:12]:
            devices = _read_io_axis(used_outputs, name, "output", 8)
            if devices:
                cfg.pos_motor_devices[name] = devices
        mm = pl.find("MotorMatrix")
        if mm is not None:
            for name in POS_AXES[:12]:
                vals = _read_matrix_axis(mm, name, ["Output0", "Output1", "Output2", "Output3", "Output4", "Output5", "Output6", "Output7"])
                if vals:
                    cfg.pos_motor_matrix[name] = vals
        fs = pl.find("FilterSetting")
        if fs is not None:
            for name in POS_AXES[:12]:
                # XML file version 7+ always stores the complete twelve-stage
                # array, even when the connected controller implements fewer
                # stages.  The controller capability is applied only when the
                # values are sent back to hardware.
                filts = _read_filter_axis(fs, name, 12, "Filt")
                if filts:
                    cfg.pos_filters[name] = filts

    # === PneumaticLoopSettings ===
    pn = root.find("PneumaticLoopSettings")
    if pn is not None:
        sm = pn.find("SensorMatrix")
        if sm is not None:
            for name in PNEU_AXES:
                vals = _read_matrix_axis(sm, name, ["Input0", "Input1", "Input2", "Input3", "Input4", "Input5", "Input6", "Input7"])
                if vals:
                    cfg.pneum_sensor_matrix[name] = vals
        mm = pn.find("MotorMatrix")
        if mm is not None:
            for name in PNEU_AXES:
                vals = _read_matrix_axis(mm, name, ["Output0", "Output1", "Output2", "Output3", "Output4", "Output5", "Output6", "Output7"])
                if vals:
                    cfg.pneum_motor_matrix[name] = vals
        fs = pn.find("FilterSetting")
        if fs is not None:
            for name in PNEU_AXES:
                filts = _read_filter_axis(fs, name, 4, "Filt")
                if filts:
                    cfg.pneum_filters[name] = filts
        vo = pn.find("ValveOffset")
        if vo is not None:
            up = vo.find("Up")
            if up is not None:
                for i in range(8):
                    v = up.get(f"Valve{i}")
                    if v is not None:
                        cfg.pneum_up_valve_offsets[i] = _float(v)
            down = vo.find("Down")
            if down is not None:
                for i in range(8):
                    v = down.get(f"Valve{i}")
                    if v is not None:
                        cfg.pneum_down_valve_offsets[i] = _float(v)
        fl = pn.find("FloatationSetting")
        if fl is not None:
            _read_int(fl, "Setpoint", cfg, "pneum_setpoint")
            _read_int(fl, "SoftUpHeight", cfg, "pneum_soft_up_height")
            _read_int(fl, "ModeTolerance", cfg, "pneum_mode_tolerance")
            _read_int(fl, "UseSetPointForAllAxes", cfg, "pneum_use_setpoint_all")
        di = pn.find("DitherSetting")
        if di is not None:
            _read_float_text(di, "DitherValue", cfg, "dither_value")
            _read_int(di, "DitherFrequency", cfg, "dither_frequency")
            _read_float_text(di, "DitherCompensationValue", cfg, "dither_compensation")
        mo = pn.find("MotorAndIsolatorOffset")
        if mo is not None:
            mot = mo.find("MotorOffset")
            if mot is not None:
                for i, name in enumerate(MOTOR_OFFSET_NAMES[:8]):
                    node = mot.find(name)
                    v = node.text if node is not None else mot.get(name)
                    if v is not None:
                        cfg.motor_offset[i] = _float(v)
            iso = mo.find("IsolatorOffset")
            if iso is not None:
                for i, name in enumerate(MOTOR_OFFSET_NAMES[8:11]):
                    node = iso.find(name)
                    v = node.text if node is not None else iso.get(name)
                    if v is not None:
                        cfg.motor_offset[i + 8] = _float(v)
        ramp = pn.find("PneumaticRampSetting")
        if ramp is not None:
            _read_int(ramp, "SwitchToUpAfterRamp", cfg, "pneum_ramp_switch_to_up")
            _read_float_text(
                ramp, "SetpointGradient", cfg, "pneum_ramp_setpoint_gradient"
            )
            _read_float_text(ramp, "UpGradient", cfg, "pneum_ramp_up_gradient")
            _read_float_text(ramp, "DownGradient", cfg, "pneum_ramp_down_gradient")
            _read_float_text(
                ramp,
                "PressureOffsetRampTime",
                cfg,
                "pneum_ramp_pressure_offset_time",
            )
        linear = pn.find("LinearMotorOffsets")
        if linear is not None:
            for index, name in enumerate(MOTOR_NAMES):
                node = linear.find(name)
                if node is not None and node.text:
                    cfg.linear_motor_offsets[index] = _float(node.text)

    # === AD-DA-Mapping ===
    adm = root.find("AD-DA-Mapping")
    if adm is not None:
        _read_int(adm, "UsedADCSetNumber", cfg, "adc_channel_set_num")
        adc = adm.find("ADC-Mapping")
        if adc is not None:
            for i, name in enumerate(ADC_INPUT_NAMES[:32]):
                v = adc.find(name)
                if v is None and name == "InpProx4":
                    v = adc.find("Prox4")
                elif v is None and name == "InpProxH4":
                    v = adc.find("ProxH4")
                if v is not None and v.text:
                    cfg.adc_mapping[i] = _int(v.text)
        dac = adm.find("DAC-Mapping")
        if dac is not None:
            for i, name in enumerate(DAC_OUTPUT_NAMES):
                v = dac.find(name)
                if v is None and 12 <= i < 18:
                    # Backward compatibility with files produced by early
                    # python_samba builds, which incorrectly used Valve1..6.
                    v = dac.find(f"Valve{i - 11}")
                if v is not None and v.text:
                    cfg.dac_mapping[i] = _int(v.text)
        temp = adm.find("TempSemsor-ADC-Mapping")
        if temp is None:
            temp = adm.find("TempSensor-ADC-Mapping")
        if temp is not None:
            for index, name in enumerate(MOTOR_NAMES):
                node = temp.find(name)
                if node is not None and node.text:
                    cfg.temp_sensor_adc_mapping[index] = _int(node.text)

    power = root.find("PowerSupplyCurrentLimitationSetting")
    if power is not None:
        _read_float_text(
            power,
            "PowerSupplyCurrentLimitValue",
            cfg,
            "power_supply_current_limit",
        )
        _read_float_text(
            power,
            "PowerSupplyCurrentSIUnitValue",
            cfg,
            "power_supply_current_si_unit",
        )

    # === Feed-Forward-Setting ===
    ff = root.find("Feed-Forward-Setting")
    if ff is not None:
        _read_int(ff, "FFNoGains", cfg, "ff_no_gains")
        _read_int(ff, "FFOutputThreshold", cfg, "ff_output_threshold")
        zr = ff.find("ZrotSFFSignalParameters")
        if zr is not None:
            _read_float_attr(zr, "FF_XposMax", cfg, "ff_xpos_max")
            _read_float_attr(zr, "FF_YposMax", cfg, "ff_ypos_max")
            _read_float_attr(zr, "FF_XposOffset", cfg, "ff_xpos_offset")
            _read_float_attr(zr, "FF_YposOffset", cfg, "ff_ypos_offset")
        sm = ff.find("StageFFSignalInputMultipliers")
        if sm is not None:
            _read_float_attr(sm, "XAcc", cfg, "ff_mult_xacc")
            _read_float_attr(sm, "YAcc", cfg, "ff_mult_yacc")
            _read_float_attr(sm, "XPos", cfg, "ff_mult_xpos")
            _read_float_attr(sm, "Ypos", cfg, "ff_mult_ypos")
        om = ff.find("FFOutputMatrix")
        if om is not None:
            for i in range(7):
                v = om.get(f"Source{i}")
                if v is not None:
                    cfg.ff_output_matrix[i] = _int(v)
        ar = ff.find("FFAdaptionConstant")
        if ar is not None:
            for i in range(7):
                v = ar.get(f"Source{i}")
                if v is not None:
                    cfg.ff_adaption_rate[i] = _float(v)
        si = ff.find("FFSourceInputs")
        if si is not None:
            for i in range(7):
                v = si.get(f"Source{i}")
                if v is not None:
                    cfg.ff_source_inputs[i] = _int(v)
        rf = ff.find("FFRefFilter")
        if rf is not None:
            for si in range(7):
                src = f"Source{si}"
                filts = []
                src_node = rf.find(src)
                for st in range(3):
                    filts.append(_read_filter_type(src_node, f"Filt{st}"))
                if src_node is not None:
                    cfg.ff_ref_filter[src] = filts
        sf = ff.find("FFSecFilter")
        if sf is not None:
            for si in range(7):
                src = f"Source{si}"
                src_node = sf.find(src)
                if src_node is not None:
                    filts = []
                    for st in range(3):
                        fil = _read_filter_type(src_node, f"Filt{st}")
                        filts.append(fil)
                    cfg.ff_sec_filter[src] = filts
        ef = ff.find("FFErrorFilter")
        if ef is not None:
            for name in VEL_AXES:
                axis_node = ef.find(name)
                if axis_node is not None:
                    filts = []
                    for st in range(2):
                        fil = _read_filter_type(axis_node, f"Filt{st}")
                        filts.append(fil)
                    cfg.ff_err_filter[name] = filts
        fg = ff.find("FFGains")
        if fg is not None:
            for si in range(7):
                src = f"Source{si}"
                src_node = fg.find(src)
                if src_node is not None:
                    axis_gains = {}
                    for name in VEL_AXES:
                        axis_node = src_node.find(name)
                        if axis_node is not None:
                            gains = []
                            for k in range(5):
                                v = axis_node.get(f"Gain{k}")
                                if v is not None:
                                    gains.append(_float(v))
                            axis_gains[name] = gains
                    cfg.ff_gains[src] = axis_gains

    # === PFF ===
    pff = root.find("Pneum-Feed-Forward-Setting")
    if pff is not None:
        _read_int(pff, "PFFNoGains", cfg, "pff_no_gains")
        _read_int(pff, "PFFOutputThreshold", cfg, "pff_output_threshold")
        om = pff.find("PFFOutputMatrix")
        if om is not None:
            for i in range(4):
                v = om.get(f"Source{i}")
                if v is not None:
                    cfg.pff_output_matrix[i] = _int(v)
        ar = pff.find("PFFAdaptionConstant")
        if ar is not None:
            for i in range(4):
                v = ar.get(f"Source{i}")
                if v is not None:
                    cfg.pff_adaption_rate[i] = _float(v)
        si = pff.find("PFFSourceInputs")
        if si is not None:
            for i in range(4):
                v = si.get(f"Source{i}")
                if v is not None:
                    cfg.pff_source_inputs[i] = _int(v)
        for section_name, target, owners in (
            ("PFFRefFilter", cfg.pff_ref_filter, [f"Source{i}" for i in range(4)]),
            ("PFFSecFilter", cfg.pff_sec_filter, [f"Source{i}" for i in range(4)]),
            ("PFFErrorFilter", cfg.pff_err_filter, PNEU_AXES),
        ):
            section = pff.find(section_name)
            if section is None:
                continue
            count = 2 if section_name == "PFFErrorFilter" else 3
            for owner_name in owners:
                owner = section.find(owner_name)
                if owner is None:
                    continue
                target[owner_name] = [
                    _read_filter_type(owner, f"Filt{stage}")
                    for stage in range(count)
                ]
        gains = pff.find("PFFGains")
        if gains is not None:
            for source in range(4):
                source_name = f"Source{source}"
                source_node = gains.find(source_name)
                if source_node is None:
                    continue
                axes: dict[str, list[float]] = {}
                for axis_name in PNEU_AXES:
                    axis_node = source_node.find(axis_name)
                    if axis_node is None:
                        continue
                    axes[axis_name] = [
                        _float(axis_node.get(f"Gain{index}", "0"))
                        for index in range(5)
                    ]
                cfg.pff_gains[source_name] = axes

    # === NonLinearPositionLoopConfig ===
    nlp = root.find("NonLinearPositionLoopConfig")
    if nlp is not None:
        _read_int(nlp, "Mode", cfg, "nlp_mode")
        _read_int(nlp, "ResetPosFilt", cfg, "nlp_reset_pid")
        _read_float_text(nlp, "DeadBand", cfg, "nlp_dead_band")
        _read_float_text(nlp, "RisingRange", cfg, "nlp_rise_range")

    # === CascadedPositionControlSetting ===
    cpc = root.find("CascadedPositionControlSetting")
    if cpc is not None:
        _read_float_text(cpc, "HystereseValue", cfg, "cascaded_hysterese")
        cf = cpc.find("CascadedFilter")
        if cf is not None:
            for i in range(3):
                fil = _read_filter_type(cf, f"Filt{i}")
                cfg.cascaded_filters[i] = fil

    # === TraceSetting ===
    ts = root.find("TraceSetting")
    if ts is not None:
        cfg.trace_io_0 = _read_io_type(ts, "TraceIOSignal0")
        cfg.trace_io_1 = _read_io_type(ts, "TraceIOSignal1")
        _read_int(ts, "TraceFilterFlag", cfg, "trace_filter_flag")
        _read_int(ts, "SampleNumber", cfg, "trace_no_samples")
        _read_int(ts, "TraceUnderSample", cfg, "trace_undersample")

    # === EventLoggingSetting ===
    event = root.find("EventLoggingSetting")
    if event is not None:
        _read_int(event, "LoggingType", cfg, "event_logging_type")
        _read_int(event, "UsedIOSignalNum", cfg, "event_used_io_signal_num")
        _read_int(event, "SamplesNum", cfg, "event_samples_num")
        _read_int(event, "Undersample", cfg, "event_undersample")
        _read_int(event, "DelaySamplesNum", cfg, "event_delay_samples_num")
        _read_int(event, "Average", cfg, "event_average")
        event_config = event.find("Event")
        if event_config is not None:
            _read_int(
                event_config,
                "MinTriggerSamples",
                cfg,
                "event_min_trigger_samples",
            )
            _read_float_text(event_config, "Threshold", cfg, "event_threshold")
            cfg.event_io_signal = _read_io_type(event_config, "EventIOSignal")
        signals = event.find("LoggingIOSignal")
        if signals is not None:
            cfg.event_monitor_signals = [
                _read_io_type(signals, f"IOSignal{index}") for index in range(40)
            ]

    # === ZMSSetting ===
    zms = root.find("ZMSSetting")
    if zms is not None:
        for i in range(12):
            v = zms.get(f"Threshold{i}")
            if v is not None:
                cfg.zms_thresholds[i] = _float(v)

    return cfg


def _read_int(parent: ET.Element, name: str, cfg: SambaConfig, attr: str) -> None:
    node = parent.find(name)
    if node is not None and node.text:
        setattr(cfg, attr, _int(node.text))


def _read_float_text(parent: ET.Element, name: str, cfg: SambaConfig, attr: str) -> None:
    node = parent.find(name)
    if node is not None and node.text:
        setattr(cfg, attr, _float(node.text))


def _read_float_attr(parent: ET.Element, name: str, cfg: SambaConfig, attr: str) -> None:
    v = parent.get(name)
    if v is not None:
        setattr(cfg, attr, _float(v))


def _read_float(parent: ET.Element, name: str, cfg: SambaConfig, attr: str, idx: int) -> None:
    node = parent.find(name)
    if node is not None and node.text:
        lst = getattr(cfg, attr)
        lst[idx] = _float(node.text)


# ======================================================================
# Controller snapshot + writer
# ======================================================================
def _as_io(tokens: list[str] | tuple[str, ...]) -> IOTypeXml:
    values = list(tokens[:3]) + ["0", "0", "0"]
    return IOTypeXml(int(values[0]), int(values[1]), int(values[2]))


def _as_io_list(
    tokens: list[str] | tuple[str, ...], count: int
) -> list[IOTypeXml]:
    """Decode the flattened IOSignal triples used by the RCI commands."""
    expected = count * 3
    if len(tokens) < expected:
        raise ValueError(
            f"expected {expected} IO tokens ({count} triples), got {len(tokens)}"
        )
    return [_as_io(tokens[index:index + 3]) for index in range(0, expected, 3)]


def _flatten_io(values: list[IOTypeXml], count: int) -> list[int]:
    """Encode IOType objects in the Type/MainIndex/SubIndex source order."""
    flattened: list[int] = []
    padded = list(values[:count])
    padded.extend(IOTypeXml() for _ in range(count - len(padded)))
    for value in padded:
        flattened.extend((value.type, value.main_index, value.sub_index))
    return flattened


def _as_filter(stage) -> FilterTypeXml:
    return FilterTypeXml(int(stage.filter_type), [float(v) for v in stage.params])


def capture_config_from_session(
    session,
    progress: Callable[[str], None] | None = None,
) -> SambaConfig:
    """Read the parameters represented by ``SambaConfig`` from a controller.

    Optional firmware features are collected independently.  Rejected reads
    are recorded in ``capture_warnings`` and are never reported as a
    successful value.
    """
    if not session.connected:
        raise RuntimeError("Session not connected")

    cfg = SambaConfig()

    def read(label: str, fn):
        if progress is not None:
            progress(label)
        try:
            return fn()
        except TransportError:
            # A lost serial handle is fatal for the whole snapshot.  Continuing
            # would only produce hundreds of secondary "port not open" errors.
            raise
        except Exception as exc:
            cfg.capture_warnings.append(f"{label}: {exc}")
            return None

    version = read("firmware version", session.get_version)
    if version is not None:
        cfg.firmware_version = version.full_text

    # BGGSC source order: test modes, velocity axes, pneumatic axes,
    # position axes, geophones, proximities, velocity stages, position
    # stages, FF stages, isolators, sample frequency.
    vel_axis_count = len(VEL_AXES)
    pneum_axis_count = len(PNEU_AXES)
    pos_axis_count = 6
    vel_stage_count = 7
    pos_stage_count = 4
    capability_tokens: list[str] = []
    constants = read("global system constants", session.get_global_system_constants)
    if constants:
        try:
            if len(constants) < 8:
                raise ValueError(f"expected at least 8 values, got {len(constants)}")
            cfg.system_configuration = " ".join(str(value) for value in constants)
            vel_axis_count = max(1, min(len(VEL_AXES), int(constants[1])))
            pneum_axis_count = max(1, min(len(PNEU_AXES), int(constants[2])))
            pos_axis_count = max(1, min(len(POS_AXES), int(constants[3])))
            vel_stage_count = max(1, min(7, int(constants[6])))
            pos_stage_count = max(1, min(12, int(constants[7])))
            capability_tokens = [str(value).upper() for value in constants[11:]]
            for token in capability_tokens:
                if token.startswith("POSAXES#"):
                    _, _, count = token.partition("#")
                    pos_axis_count = max(1, min(len(POS_AXES), int(count)))
                    break
        except (TypeError, ValueError) as exc:
            cfg.capture_warnings.append(f"global system constants: {exc}")

    # BGGSC appends feature markers after its eleven numeric constants.  The
    # original UI uses these flags to avoid sending extension commands that
    # this firmware did not compile in.  If an older/mock response has no
    # marker section, retain the previous probe-and-report behaviour.
    capabilities_known = bool(capability_tokens)

    def supports(*markers: str) -> bool:
        if not capabilities_known:
            return True
        return any(
            marker.upper() in token
            for marker in markers
            for token in capability_tokens
        )

    loop = read("loop status", session.get_loop_status)
    if loop is not None:
        cfg.individual_loop_status = int(loop.individual)
        cfg.loop_status = int(loop.system)
    extended = read(
        "position/pneumatic individual status",
        session.get_pos_pneum_digital_status,
    )
    if extended is not None:
        cfg.pos_individual_loop_status = int(extended[0])
        cfg.pneum_individual_loop_status = int(extended[1])

    value = read("output limit", session.get_output_limit)
    if value is not None:
        cfg.motors_limit = int(value)
    value = read("sample frequency", session.get_sample_frequency)
    if value is not None:
        cfg.sample_frequency = float(value)
    value = read("controller configuration", session.get_controller_config)
    if value:
        cfg.firmware_configuration = int(value[0], 0)
    value = read("startup ramp", session.get_startup_ramp)
    if value and len(value) >= 2:
        cfg.output_ramp_type = int(value[0])
        cfg.output_ramp_time = float(value[1])

    value = read("performance monitor", session.get_performance_monitor)
    if value:
        offset = 3 if len(value) >= 6 else 1
        if len(value) >= 3:
            cfg.perf_monitor_signal = _as_io(value[:3])
        if len(value) > offset:
            cfg.perf_threshold = int(float(value[offset]))
        if len(value) > offset + 1:
            cfg.perf_min_time = float(value[offset + 1])
        if len(value) > offset + 2:
            cfg.perf_hold_time = float(value[offset + 2])

    value = read("motor protection", session.get_motor_overcurrent_config)
    if value:
        cfg.disable_all_flag = 1 if str(value[0]).upper() in {"1", "N", "ON", "TRUE"} else 0
        if len(value) > 1:
            cfg.reset_delay_time = float(value[1])
        cfg.motor_thresholds[: min(12, len(value) - 2)] = [
            float(v) for v in value[2:14]
        ]
    value = read(
        "motor cooling constant", session.get_motor_overcurrent_cooling_constant
    )
    if value is not None:
        cfg.motor_cooling_constant = float(value)

    value = read("switch signal", session.get_switch_signal)
    if value and len(value) >= 3:
        cfg.switch_io_signal = _as_io(value)
    value = read("switch conditions", session.get_switch_conditions)
    if value:
        if len(value) > 0:
            cfg.switch_trigger_level = int(float(value[0]))
        if len(value) > 1:
            cfg.switch_min_trigger_time = float(value[1])
        if len(value) > 2:
            cfg.switch_hold_time = float(value[2])
        if len(value) > 3:
            cfg.loop_switch_config = int(value[3], 0)

    value = read("excitation parameters", session.get_excitation_params)
    if value:
        if len(value) >= 5:
            cfg.excitation_type = int(value[0])
            cfg.excitation_params = [float(item) for item in value[1:5]]
        else:
            cfg.capture_warnings.append(
                f"excitation parameters: expected 5 values, got {len(value)}"
            )
    value = read("diagnostic output signals", session.get_diagnostic_outputs)
    if value:
        try:
            cfg.diag_io_signal_0, cfg.diag_io_signal_1 = _as_io_list(value, 2)
        except (TypeError, ValueError) as exc:
            cfg.capture_warnings.append(f"diagnostic output signals: {exc}")
    value = read("noise injection signal", session.get_noise_inject_point)
    if value:
        try:
            cfg.noise_injection_signal = _as_io_list(value, 1)[0]
        except (TypeError, ValueError) as exc:
            cfg.capture_warnings.append(f"noise injection signal: {exc}")
    for stage in range(4):
        value = read(
            f"excitation filter {stage}",
            lambda stage=stage: session.get_noise_filter_stage(stage),
        )
        if value is not None:
            cfg.excitation_filters[stage] = _as_filter(value)

    value = read("trace information", session.get_digital_trace_info)
    if value:
        if len(value) >= 9:
            try:
                cfg.trace_io_0, cfg.trace_io_1 = _as_io_list(value[:6], 2)
                cfg.trace_undersample = int(value[6])
                cfg.trace_no_samples = int(value[7])
                cfg.trace_filter_flag = int(value[8])
            except (TypeError, ValueError) as exc:
                cfg.capture_warnings.append(f"trace information: {exc}")
        else:
            cfg.capture_warnings.append(
                f"trace information: expected 9 values, got {len(value)}"
            )

    value = read("event trace parameters", session.get_event_trace_params)
    if value:
        if len(value) >= 6:
            cfg.event_logging_type = int(value[0])
            cfg.event_samples_num = int(value[1])
            cfg.event_used_io_signal_num = int(value[2])
            cfg.event_undersample = int(value[3])
            cfg.event_delay_samples_num = int(value[4])
            cfg.event_average = int(value[5])
            event_signal = read("event signal", session.get_event_signal)
            if event_signal:
                if len(event_signal) >= 5:
                    try:
                        cfg.event_io_signal = _as_io_list(event_signal[:3], 1)[0]
                        cfg.event_threshold = float(event_signal[3])
                        cfg.event_min_trigger_samples = int(event_signal[4])
                    except (TypeError, ValueError) as exc:
                        cfg.capture_warnings.append(f"event signal: {exc}")
                else:
                    cfg.capture_warnings.append(
                        f"event signal: expected 5 values, got {len(event_signal)}"
                    )
            for signal_index in range(40):
                signal = read(
                    f"event monitor signal {signal_index}",
                    lambda signal_index=signal_index: session.get_monitor_signal(
                        signal_index
                    ),
                )
                if signal:
                    try:
                        cfg.event_monitor_signals[signal_index] = _as_io_list(
                            signal, 1
                        )[0]
                    except (TypeError, ValueError) as exc:
                        cfg.capture_warnings.append(
                            f"event monitor signal {signal_index}: {exc}"
                        )
        else:
            cfg.capture_warnings.append(
                f"event trace parameters: expected 6 values, got {len(value)}"
            )

    value = read("velocity output limiter", session.get_vel_axes_output_limiter)
    if value:
        cfg.vel_axis_output_limiter[: len(value[:6])] = [float(v) for v in value[:6]]
    for axis, name in enumerate(VEL_AXES[:vel_axis_count]):
        value = read(
            f"velocity sensor matrix {name}",
            lambda axis=axis: session.get_velocity_sensor_matrix(axis),
        )
        if value:
            cfg.vel_sensor_matrix[name] = [float(v) for v in value]
        value = read(
            f"velocity motor matrix {name}",
            lambda axis=axis: session.get_velocity_motor_matrix(axis),
        )
        if value:
            cfg.vel_motor_matrix[name] = [float(v) for v in value]
        filters: list[FilterTypeXml] = []
        for stage in range(vel_stage_count):
            value = read(
                f"velocity filter {name}/{stage}",
                lambda axis=axis, stage=stage: session.get_velocity_filter(axis, stage),
            )
            if value is not None:
                filters.append(_as_filter(value))
        if filters:
            cfg.vel_filters[name] = filters

    proximity_count = max(1, min(8, int(constants[5]))) if constants and len(constants) > 5 else 6
    value = read(
        "proximity offsets",
        lambda: session.get_proximity_offsets(proximity_count),
    )
    if value:
        cfg.prox_offsets[: len(value[:8])] = [float(v) for v in value[:8]]
    for axis, name in enumerate(POS_AXES[:pos_axis_count]):
        value = read(
            f"position sensor devices {name}",
            lambda axis=axis: session.get_position_sensor_devices_for_axis(axis),
        )
        if value:
            try:
                cfg.pos_sensor_devices[name] = _as_io_list(value, 6)
            except (TypeError, ValueError) as exc:
                cfg.capture_warnings.append(
                    f"position sensor devices {name}: {exc}"
                )
        value = read(
            f"position sensor matrix {name}",
            lambda axis=axis: session.get_position_sensor_matrix(axis),
        )
        if value:
            cfg.pos_sensor_matrix[name] = [float(v) for v in value]
        value = read(
            f"position motor devices {name}",
            lambda axis=axis: session.get_position_motor_devices_for_axis(axis),
        )
        if value:
            try:
                cfg.pos_motor_devices[name] = _as_io_list(value, 8)
            except (TypeError, ValueError) as exc:
                cfg.capture_warnings.append(
                    f"position motor devices {name}: {exc}"
                )
        value = read(
            f"position motor matrix {name}",
            lambda axis=axis: session.get_position_motor_matrix(axis),
        )
        if value:
            cfg.pos_motor_matrix[name] = [float(v) for v in value]
        filters = []
        for stage in range(pos_stage_count):
            value = read(
                f"position filter {name}/{stage}",
                lambda axis=axis, stage=stage: session.get_proximity_filter(axis, stage),
            )
            if value is not None:
                filters.append(_as_filter(value))
        if filters:
            cfg.pos_filters[name] = filters

    for axis, name in enumerate(PNEU_AXES[:pneum_axis_count]):
        value = read(
            f"pneumatic steering {name}",
            lambda axis=axis: session.get_pneumatic_steering_matrix(axis),
        )
        if value:
            values = [float(v) for v in value]
            split = len(values) // 2
            cfg.pneum_sensor_matrix[name] = values[:split]
            cfg.pneum_motor_matrix[name] = values[split:]
        filters = []
        for stage in range(4):
            value = read(
                f"pneumatic filter {name}/{stage}",
                lambda axis=axis, stage=stage: session.get_pneumatic_filter(axis, stage),
            )
            if value is not None:
                filters.append(_as_filter(value))
        if filters:
            cfg.pneum_filters[name] = filters

    value = read("pneumatic configuration", session.get_pneumatic_config)
    if value:
        if len(value) > 0:
            cfg.pneum_soft_up_height = int(float(value[0]))
        if len(value) > 1:
            cfg.pneum_setpoint = int(float(value[1]))
        if len(value) > 2:
            cfg.pneum_mode_tolerance = int(float(value[2]))
    value = read("pneumatic valve offsets", session.get_pneumatic_valve_offsets)
    if value:
        half = len(value) // 2
        cfg.pneum_up_valve_offsets[:half] = [float(v) for v in value[:half]]
        cfg.pneum_down_valve_offsets[: len(value) - half] = [
            float(v) for v in value[half:]
        ]
    value = read("pneumatic setpoint mode", session.get_pneumatic_setpoint_status)
    if value is not None:
        cfg.pneum_use_setpoint_all = int(value)
    for label, getter, attr in (
        ("dither value", session.get_dither_value, "dither_value"),
        ("dither frequency", session.get_dither_frequency, "dither_frequency"),
        ("dither compensation", session.get_dither_alpha, "dither_compensation"),
    ):
        value = read(label, getter)
        if value is not None:
            setattr(
                cfg,
                attr,
                int(round(float(value))) if attr == "dither_frequency" else float(value),
            )
    value = read("motor/isolator offsets", session.get_motor_offsets)
    if value:
        cfg.motor_offset[: len(value[:11])] = [float(v) for v in value[:11]]
    if supports("PNEUMRAMP", "PRAMP"):
        value = read("pneumatic ramp parameters", session.get_pneumatic_ramp_parameters)
        if value:
            if len(value) >= 5:
                cfg.pneum_ramp_switch_to_up = int(float(value[0]))
                cfg.pneum_ramp_setpoint_gradient = float(value[1])
                cfg.pneum_ramp_up_gradient = float(value[2])
                cfg.pneum_ramp_down_gradient = float(value[3])
                cfg.pneum_ramp_pressure_offset_time = float(value[4])
            else:
                cfg.capture_warnings.append(
                    f"pneumatic ramp parameters: expected 5 values, got {len(value)}"
                )
    if supports("SALMO"):
        value = read("linear motor offsets", session.get_linear_motor_offsets)
        if value:
            cfg.linear_motor_offsets[: len(value[:12])] = [
                float(item) for item in value[:12]
            ]

    value = read("ADC mapping", session.get_adc_sequence)
    if value:
        cfg.adc_mapping[: len(value[:32])] = [int(v) for v in value[:32]]
    value = read("DAC mapping", session.get_dac_sequence)
    if value:
        cfg.dac_mapping[: len(value[:20])] = [int(v) for v in value[:20]]
    value = read("ADC set number", session.get_adc_set_number)
    if value is not None:
        cfg.adc_channel_set_num = int(value)
    value = read("temperature sensor ADC mapping", session.get_temp_sensor_adc_mapping)
    if value:
        cfg.temp_sensor_adc_mapping[: len(value[:12])] = [
            int(item) for item in value[:12]
        ]
    value = read("power supply current limitation", session.get_power_supply_parameters)
    if value:
        if len(value) >= 2:
            cfg.power_supply_current_limit = float(value[0])
            cfg.power_supply_current_si_unit = float(value[1])
        else:
            cfg.capture_warnings.append(
                f"power supply current limitation: expected 2 values, got {len(value)}"
            )

    value = read("FF configuration", session.get_ff_config)
    if value:
        cfg.ff_no_gains = int(value[0])
        # FGFFC's second field is the OnOff character selecting raw/feedback
        # error signals (``N``/``Y``), not the output threshold.  The latter
        # is a separate BGFFL command in both the RCI manual and C# source.
    value = read("FF output limit", session.get_ff_output_limit)
    if value is not None:
        cfg.ff_output_threshold = int(value)
    value = read("FF source inputs", session.get_ff_inputs)
    if value:
        cfg.ff_source_inputs[: len(value[:7])] = [int(v) for v in value[:7]]
    for source in range(7):
        params = read(
            f"FF source {source} parameters",
            lambda source=source: session.get_ff_parameters(source),
        )
        if params:
            # FGFFP returns Outputs(hex), AdaptiveStatus(T/F), AdaptionRate.
            cfg.ff_output_matrix[source] = int(params[0], 16)
            if len(params) > 2:
                cfg.ff_adaption_rate[source] = float(params[2])
            elif len(params) > 1:
                cfg.capture_warnings.append(
                    f"FF source {source} parameters: expected adaptive flag and rate, "
                    f"got {params}"
                )
        ref_filters: list[FilterTypeXml] = []
        sec_filters: list[FilterTypeXml] = []
        for stage in range(3):
            filt = read(
                f"FF reference filter {source}/{stage}",
                lambda source=source, stage=stage: session.get_ff_filter(source, stage),
            )
            if filt is not None:
                ref_filters.append(_as_filter(filt))
            filt = read(
                f"FF secondary filter {source}/{stage}",
                lambda source=source, stage=stage: session.get_ff_filter(source, stage + 3),
            )
            if filt is not None:
                sec_filters.append(_as_filter(filt))
        if ref_filters:
            cfg.ff_ref_filter[f"Source{source}"] = ref_filters
        if sec_filters:
            cfg.ff_sec_filter[f"Source{source}"] = sec_filters
        gains = read(
            f"FF gains source {source}",
            lambda source=source: session.get_ff_gains(source),
        )
        if gains:
            axis_gains: dict[str, list[float]] = {}
            for axis, name in enumerate(VEL_AXES):
                # The COM interface and RCI response always transfer five
                # stored taps per axis; FFNoGains selects how many are active.
                chunk = gains[axis * 5:(axis + 1) * 5]
                if chunk:
                    axis_gains[name] = [float(v) for v in chunk]
            cfg.ff_gains[f"Source{source}"] = axis_gains
    for axis, name in enumerate(VEL_AXES):
        error_filters = []
        for stage in range(2):
            filt = read(
                f"FF error filter {name}/{stage}",
                lambda axis=axis, stage=stage: session.get_ff_filter(axis, stage + 6),
            )
            if filt is not None:
                error_filters.append(_as_filter(filt))
        if error_filters:
            cfg.ff_err_filter[name] = error_filters
    value = read("FF stage multipliers", session.get_stage_ff_multipliers)
    if value and len(value) >= 4:
        cfg.ff_mult_xacc, cfg.ff_mult_yacc, cfg.ff_mult_xpos, cfg.ff_mult_ypos = (
            float(value[0]), float(value[1]), float(value[2]), float(value[3])
        )
    value = read("FF Zrot parameters", session.get_ff_zrot_parameters)
    if value and len(value) >= 4:
        cfg.ff_xpos_max, cfg.ff_ypos_max, cfg.ff_xpos_offset, cfg.ff_ypos_offset = (
            float(value[0]), float(value[1]), float(value[2]), float(value[3])
        )

    value = read("PFF configuration", session.get_pff_config)
    if value:
        cfg.pff_no_gains = int(value[0])
        if len(value) > 1:
            cfg.pff_output_threshold = int(float(value[1]))
    value = read("PFF source inputs", session.get_pff_inputs)
    if value:
        cfg.pff_source_inputs[: len(value[:4])] = [int(v) for v in value[:4]]
    for source in range(4):
        params = read(
            f"PFF source {source} parameters",
            lambda source=source: session.get_pff_parameters(source),
        )
        if params:
            cfg.pff_output_matrix[source] = int(params[0], 16)
            if len(params) > 1:
                cfg.pff_adaption_rate[source] = float(params[1])
        ref_filters = []
        sec_filters = []
        for stage in range(3):
            filt = read(
                f"PFF reference filter {source}/{stage}",
                lambda source=source, stage=stage: session.get_pff_filter(0, source, stage),
            )
            if filt is not None:
                ref_filters.append(_as_filter(filt))
            filt = read(
                f"PFF secondary filter {source}/{stage}",
                lambda source=source, stage=stage: session.get_pff_filter(0, source, stage + 3),
            )
            if filt is not None:
                sec_filters.append(_as_filter(filt))
        if ref_filters:
            cfg.pff_ref_filter[f"Source{source}"] = ref_filters
        if sec_filters:
            cfg.pff_sec_filter[f"Source{source}"] = sec_filters
        axis_gains = {}
        for axis, name in enumerate(PNEU_AXES):
            gains = read(
                f"PFF gains {name}/source {source}",
                lambda axis=axis, source=source: session.get_pff_gains(axis, source),
            )
            if gains:
                axis_gains[name] = [float(v) for v in gains]
        cfg.pff_gains[f"Source{source}"] = axis_gains
    for axis, name in enumerate(PNEU_AXES):
        error_filters = []
        for stage in range(2):
            filt = read(
                f"PFF error filter {name}/{stage}",
                lambda axis=axis, stage=stage: session.get_pff_filter(axis, 0, stage + 6),
            )
            if filt is not None:
                error_filters.append(_as_filter(filt))
        if error_filters:
            cfg.pff_err_filter[name] = error_filters

    value = read("NLP", session.get_non_linear_position_parameter)
    if value and len(value) >= 4:
        cfg.nlp_mode = int(value[0])
        cfg.nlp_reset_pid = int(value[1])
        cfg.nlp_dead_band = float(value[2])
        cfg.nlp_rise_range = float(value[3])
    if supports("CASCADED"):
        value = read(
            "cascaded position parameter", session.get_cascaded_position_parameter
        )
        if value and len(value) >= 2:
            cfg.cascaded_hysterese = float(value[1])
        for stage in range(3):
            value = read(
                f"cascaded position filter {stage}",
                lambda stage=stage: session.get_cascaded_position_filter(stage),
            )
            if value is not None:
                cfg.cascaded_filters[stage] = _as_filter(value)
    if supports("ZMS"):
        value = read("ZMS thresholds", session.get_zms_stability_thresholds)
        if value:
            cfg.zms_thresholds[: len(value[:12])] = [float(v) for v in value[:12]]
    return cfg


def _put_text(parent: ET.Element, name: str, value) -> ET.Element:
    node = ET.SubElement(parent, name)
    node.text = str(value)
    return node


def _format_float(value: float) -> str:
    """Match XmlToolSettings' invariant ``0.000000E+00`` formatting."""
    return f"{float(value):.6E}"


def _put_float_text(parent: ET.Element, name: str, value: float) -> ET.Element:
    return _put_text(parent, name, _format_float(value))


def _put_io(parent: ET.Element, name: str, value: IOTypeXml) -> None:
    ET.SubElement(parent, name, {
        "Type": str(value.type),
        "MainIndex": str(value.main_index),
        "SubIndex": str(value.sub_index),
    })


def _put_filter(parent: ET.Element, name: str, value: FilterTypeXml) -> None:
    attrs = {"Type": str(value.type)}
    params = (list(value.par[:5]) + [0.0] * 5)[:5]
    attrs.update({f"Par{i}": _format_float(v) for i, v in enumerate(params)})
    ET.SubElement(parent, name, attrs)


def _number_values(values: list[Any], count: int, default: float = 0.0) -> list[Any]:
    return (list(values[:count]) + [default] * count)[:count]


def _io_values(values: list[IOTypeXml], count: int) -> list[IOTypeXml]:
    result = list(values[:count])
    result.extend(IOTypeXml() for _ in range(count - len(result)))
    return result


def _filter_values(values: list[FilterTypeXml], count: int) -> list[FilterTypeXml]:
    result = list(values[:count])
    result.extend(FilterTypeXml() for _ in range(count - len(result)))
    return result


def save_config(path: str | Path, cfg: SambaConfig) -> None:
    """Write a vendor-compatible XML version 8 controller snapshot."""
    root = ET.Element("SAMBA1_9_X_Configuration")
    _put_text(root, "FirmwareVersionInfo", cfg.firmware_version)
    _put_text(root, "XML_File_Version", CONFIG_XML_VERSION)
    _put_text(root, "SystemConfiguration", cfg.system_configuration)

    ss = ET.SubElement(root, "SystemSetting")
    for name, value in (
        ("LoopStatus", cfg.loop_status),
        ("IndividualLoopStatus", cfg.individual_loop_status),
        ("PosIndividualLoopStatus", cfg.pos_individual_loop_status),
        ("PneumIndividualLoopStatus", cfg.pneum_individual_loop_status),
        ("MotorsLimit", cfg.motors_limit),
        ("ExcitationType", cfg.excitation_type),
    ):
        _put_text(ss, name, value)
    excitation_params = _number_values(cfg.excitation_params, 4)
    for index, value in enumerate(excitation_params):
        _put_float_text(ss, f"ExcitationParam{index}", value)
    _put_float_text(ss, "SampleFrequency", cfg.sample_frequency)
    _put_io(ss, "DiagIOSignal0", cfg.diag_io_signal_0)
    _put_io(ss, "DiagIOSignal1", cfg.diag_io_signal_1)
    _put_io(ss, "NoiseInjectionIOSignal", cfg.noise_injection_signal)
    _put_text(ss, "OutputRampType", cfg.output_ramp_type)
    _put_float_text(ss, "OutputRampTime", cfg.output_ramp_time)
    _put_text(ss, "FirmwareConfiguration", cfg.firmware_configuration)
    excitation_filters = ET.SubElement(ss, "ExcitationFilter")
    for index, filt in enumerate(_filter_values(cfg.excitation_filters, 4)):
        _put_filter(excitation_filters, f"Fil{index}", filt)

    pm = ET.SubElement(root, "PerformanceMonitorSetting")
    _put_io(pm, "PerfMonitorIOSignal", cfg.perf_monitor_signal)
    _put_float_text(pm, "PerfMinTime", cfg.perf_min_time)
    _put_float_text(pm, "PerfHoldTime", cfg.perf_hold_time)
    _put_text(pm, "PerfThreshold", cfg.perf_threshold)

    motor = ET.SubElement(root, "MotorOvercurrentSetting")
    _put_float_text(motor, "MotorOverCurrentCoolingConstant", cfg.motor_cooling_constant)
    _put_float_text(motor, "ResetDelayTime", cfg.reset_delay_time)
    _put_text(motor, "DisableAllFlag", cfg.disable_all_flag)
    motor_thresholds = _number_values(cfg.motor_thresholds, 12, 50.0)
    ET.SubElement(motor, "MotorThresholds", {
        name: _format_float(motor_thresholds[index])
        for index, name in enumerate(MOTOR_NAMES)
    })

    switch = ET.SubElement(root, "AutoLoopSwitchSetting")
    _put_text(switch, "LoopSwitchConfig", cfg.loop_switch_config)
    _put_io(switch, "SwitchIOSignal", cfg.switch_io_signal)
    _put_text(switch, "SwitchTriggerLevel", cfg.switch_trigger_level)
    _put_float_text(switch, "SwitchHoldTime", cfg.switch_hold_time)
    _put_float_text(switch, "SwitchMinTriggerTime", cfg.switch_min_trigger_time)

    velocity = ET.SubElement(root, "VelocityLoopSettings")
    velocity_limiters = _number_values(cfg.vel_axis_output_limiter, 6, 100000.0)
    ET.SubElement(velocity, "VelAxisOutputLimiter", {
        name: _format_float(velocity_limiters[index])
        for index, name in enumerate(VEL_AXES)
    })
    sensor = ET.SubElement(velocity, "SensorMatrix")
    motor_matrix = ET.SubElement(velocity, "MotorMatrix")
    filters = ET.SubElement(velocity, "FilterSetting")
    for name in VEL_AXES:
        values = _number_values(cfg.vel_sensor_matrix.get(name, []), 8)
        ET.SubElement(sensor, name, {
            channel: _format_float(values[index])
            for index, channel in enumerate(VEL_INPUT_NAMES)
        })
        values = _number_values(cfg.vel_motor_matrix.get(name, []), 12)
        ET.SubElement(motor_matrix, name, {
            channel: _format_float(values[index])
            for index, channel in enumerate(VEL_OUTPUT_NAMES)
        })
        axis_node = ET.SubElement(filters, name)
        for stage, filt in enumerate(
            _filter_values(cfg.vel_filters.get(name, []), 7)
        ):
            _put_filter(axis_node, f"Filt_{stage}", filt)

    position = ET.SubElement(root, "PositionLoopSettings")
    prox_names = ["ProxV1", "ProxV2", "ProxV3", "ProxH1", "ProxH2", "ProxH3", "ProxV4", "ProxH4"]
    proximity_offsets = _number_values(cfg.prox_offsets, 8)
    ET.SubElement(position, "ProximityOffsets", {
        name: _format_float(proximity_offsets[index])
        for index, name in enumerate(prox_names)
    })
    used_inputs = ET.SubElement(position, "SensorMatrixUsedInput")
    sensor = ET.SubElement(position, "SensorMatrix")
    used_outputs = ET.SubElement(position, "MotorMatrixUsedOutput")
    motor_matrix = ET.SubElement(position, "MotorMatrix")
    filters = ET.SubElement(position, "FilterSetting")
    for name in POS_AXES:
        input_axis = ET.SubElement(used_inputs, name)
        for index, value in enumerate(
            _io_values(cfg.pos_sensor_devices.get(name, []), 6)
        ):
            _put_io(input_axis, f"Input{index}", value)
        values = _number_values(cfg.pos_sensor_matrix.get(name, []), 6)
        ET.SubElement(sensor, name, {
            f"Input{index}": _format_float(value)
            for index, value in enumerate(values)
        })
        output_axis = ET.SubElement(used_outputs, name)
        for index, value in enumerate(
            _io_values(cfg.pos_motor_devices.get(name, []), 8)
        ):
            _put_io(output_axis, f"output{index}", value)
        values = _number_values(cfg.pos_motor_matrix.get(name, []), 8)
        ET.SubElement(motor_matrix, name, {
            f"Output{index}": _format_float(value)
            for index, value in enumerate(values)
        })
        axis_node = ET.SubElement(filters, name)
        for stage, filt in enumerate(
            _filter_values(cfg.pos_filters.get(name, []), 12)
        ):
            _put_filter(axis_node, f"Filt{stage}", filt)

    pneumatic = ET.SubElement(root, "PneumaticLoopSettings")
    sensor = ET.SubElement(pneumatic, "SensorMatrix")
    motor_matrix = ET.SubElement(pneumatic, "MotorMatrix")
    filters = ET.SubElement(pneumatic, "FilterSetting")
    for name in PNEU_AXES:
        sensor_values = _number_values(cfg.pneum_sensor_matrix.get(name, []), 8)
        ET.SubElement(sensor, name, {
            f"Input{index}": _format_float(value)
            for index, value in enumerate(sensor_values)
        })
        motor_values = _number_values(cfg.pneum_motor_matrix.get(name, []), 8)
        ET.SubElement(motor_matrix, name, {
            f"Output{index}": _format_float(value)
            for index, value in enumerate(motor_values)
        })
        axis_node = ET.SubElement(filters, name)
        for stage, filt in enumerate(
            _filter_values(cfg.pneum_filters.get(name, []), 4)
        ):
            _put_filter(axis_node, f"Filt{stage}", filt)
    valves = ET.SubElement(pneumatic, "ValveOffset")
    up_offsets = _number_values(cfg.pneum_up_valve_offsets, 8)
    down_offsets = _number_values(cfg.pneum_down_valve_offsets, 8)
    ET.SubElement(valves, "Up", {
        f"Valve{i}": _format_float(value) for i, value in enumerate(up_offsets)
    })
    ET.SubElement(valves, "Down", {
        f"Valve{i}": _format_float(value) for i, value in enumerate(down_offsets)
    })
    floatation = ET.SubElement(pneumatic, "FloatationSetting")
    _put_text(floatation, "Setpoint", cfg.pneum_setpoint)
    _put_text(floatation, "SoftUpHeight", cfg.pneum_soft_up_height)
    _put_text(floatation, "ModeTolerance", cfg.pneum_mode_tolerance)
    _put_text(floatation, "UseSetPointForAllAxes", cfg.pneum_use_setpoint_all)
    dither = ET.SubElement(pneumatic, "DitherSetting")
    _put_float_text(dither, "DitherValue", cfg.dither_value)
    _put_text(dither, "DitherFrequency", cfg.dither_frequency)
    _put_float_text(dither, "DitherCompensationValue", cfg.dither_compensation)
    offsets = ET.SubElement(pneumatic, "MotorAndIsolatorOffset")
    motor_offsets = _number_values(cfg.motor_offset, 11)
    motor_offset = ET.SubElement(offsets, "MotorOffset")
    for index, name in enumerate(MOTOR_OFFSET_NAMES[:8]):
        _put_float_text(motor_offset, name, motor_offsets[index])
    isolator_offset = ET.SubElement(offsets, "IsolatorOffset")
    for index, name in enumerate(MOTOR_OFFSET_NAMES[8:11], start=8):
        _put_float_text(isolator_offset, name, motor_offsets[index])
    ramp = ET.SubElement(pneumatic, "PneumaticRampSetting")
    _put_text(ramp, "SwitchToUpAfterRamp", cfg.pneum_ramp_switch_to_up)
    _put_float_text(ramp, "SetpointGradient", cfg.pneum_ramp_setpoint_gradient)
    _put_float_text(ramp, "UpGradient", cfg.pneum_ramp_up_gradient)
    _put_float_text(ramp, "DownGradient", cfg.pneum_ramp_down_gradient)
    _put_float_text(
        ramp, "PressureOffsetRampTime", cfg.pneum_ramp_pressure_offset_time
    )
    linear = ET.SubElement(pneumatic, "LinearMotorOffsets")
    linear_offsets = _number_values(cfg.linear_motor_offsets, 12)
    for index, name in enumerate(MOTOR_NAMES):
        _put_float_text(linear, name, linear_offsets[index])

    mapping = ET.SubElement(root, "AD-DA-Mapping")
    _put_text(mapping, "UsedADCSetNumber", cfg.adc_channel_set_num)
    adc = ET.SubElement(mapping, "ADC-Mapping")
    adc_mapping = _number_values(cfg.adc_mapping, 32, 0)
    for index, name in enumerate(ADC_INPUT_NAMES):
        _put_text(adc, name, adc_mapping[index])
    dac = ET.SubElement(mapping, "DAC-Mapping")
    dac_mapping = _number_values(cfg.dac_mapping, 20, 0)
    for index, name in enumerate(DAC_OUTPUT_NAMES):
        _put_text(dac, name, dac_mapping[index])
    temp = ET.SubElement(mapping, "TempSemsor-ADC-Mapping")
    temperature_mapping = _number_values(cfg.temp_sensor_adc_mapping, 12, 0)
    for index, name in enumerate(MOTOR_NAMES):
        _put_text(temp, name, temperature_mapping[index])

    power = ET.SubElement(root, "PowerSupplyCurrentLimitationSetting")
    _put_float_text(
        power, "PowerSupplyCurrentLimitValue", cfg.power_supply_current_limit
    )
    _put_float_text(
        power,
        "PowerSupplyCurrentSIUnitValue",
        cfg.power_supply_current_si_unit,
    )

    ff = ET.SubElement(root, "Feed-Forward-Setting")
    _put_text(ff, "FFNoGains", cfg.ff_no_gains)
    _put_text(ff, "FFOutputThreshold", cfg.ff_output_threshold)
    ET.SubElement(ff, "ZrotSFFSignalParameters", {
        "FF_XposMax": _format_float(cfg.ff_xpos_max),
        "FF_YposMax": _format_float(cfg.ff_ypos_max),
        "FF_XposOffset": _format_float(cfg.ff_xpos_offset),
        "FF_YposOffset": _format_float(cfg.ff_ypos_offset),
    })
    ET.SubElement(ff, "StageFFSignalInputMultipliers", {
        "XAcc": _format_float(cfg.ff_mult_xacc),
        "YAcc": _format_float(cfg.ff_mult_yacc),
        "XPos": _format_float(cfg.ff_mult_xpos),
        "Ypos": _format_float(cfg.ff_mult_ypos),
    })
    ff_output_matrix = _number_values(cfg.ff_output_matrix, 7, 0)
    ET.SubElement(ff, "FFOutputMatrix", {
        f"Source{i}": str(value) for i, value in enumerate(ff_output_matrix)
    })
    ff_adaption_rate = _number_values(cfg.ff_adaption_rate, 7)
    ET.SubElement(ff, "FFAdaptionConstant", {
        f"Source{i}": _format_float(value)
        for i, value in enumerate(ff_adaption_rate)
    })
    ff_source_inputs = _number_values(cfg.ff_source_inputs, 7, 0)
    ET.SubElement(ff, "FFSourceInputs", {
        f"Source{i}": str(value) for i, value in enumerate(ff_source_inputs)
    })
    for section_name, values, owners, count in (
        ("FFRefFilter", cfg.ff_ref_filter, [f"Source{i}" for i in range(7)], 3),
        ("FFSecFilter", cfg.ff_sec_filter, [f"Source{i}" for i in range(7)], 3),
        ("FFErrorFilter", cfg.ff_err_filter, VEL_AXES, 2),
    ):
        section = ET.SubElement(ff, section_name)
        for name in owners:
            owner = ET.SubElement(section, name)
            for index, filt in enumerate(
                _filter_values(values.get(name, []), count)
            ):
                _put_filter(owner, f"Filt{index}", filt)
    gains = ET.SubElement(ff, "FFGains")
    for source_index in range(7):
        source = f"Source{source_index}"
        axes = cfg.ff_gains.get(source, {})
        source_node = ET.SubElement(gains, source)
        for axis in VEL_AXES:
            values = _number_values(axes.get(axis, []), 5)
            ET.SubElement(source_node, axis, {
                f"Gain{i}": _format_float(value)
                for i, value in enumerate(values)
            })

    pff = ET.SubElement(root, "Pneum-Feed-Forward-Setting")
    _put_text(pff, "PFFNoGains", cfg.pff_no_gains)
    _put_text(pff, "PFFOutputThreshold", cfg.pff_output_threshold)
    pff_output_matrix = _number_values(cfg.pff_output_matrix, 4, 0)
    ET.SubElement(pff, "PFFOutputMatrix", {
        f"Source{i}": str(value) for i, value in enumerate(pff_output_matrix)
    })
    pff_adaption_rate = _number_values(cfg.pff_adaption_rate, 4)
    ET.SubElement(pff, "PFFAdaptionConstant", {
        f"Source{i}": _format_float(value)
        for i, value in enumerate(pff_adaption_rate)
    })
    pff_source_inputs = _number_values(cfg.pff_source_inputs, 4, 0)
    ET.SubElement(pff, "PFFSourceInputs", {
        f"Source{i}": str(value) for i, value in enumerate(pff_source_inputs)
    })
    for section_name, values, owners, count in (
        ("PFFRefFilter", cfg.pff_ref_filter, [f"Source{i}" for i in range(4)], 3),
        ("PFFSecFilter", cfg.pff_sec_filter, [f"Source{i}" for i in range(4)], 3),
        ("PFFErrorFilter", cfg.pff_err_filter, PNEU_AXES, 2),
    ):
        section = ET.SubElement(pff, section_name)
        for name in owners:
            owner = ET.SubElement(section, name)
            for index, filt in enumerate(
                _filter_values(values.get(name, []), count)
            ):
                _put_filter(owner, f"Filt{index}", filt)
    gains = ET.SubElement(pff, "PFFGains")
    for source_index in range(4):
        source = f"Source{source_index}"
        axes = cfg.pff_gains.get(source, {})
        source_node = ET.SubElement(gains, source)
        for axis in PNEU_AXES:
            values = _number_values(axes.get(axis, []), 5)
            ET.SubElement(source_node, axis, {
                f"Gain{i}": _format_float(value)
                for i, value in enumerate(values)
            })

    nlp = ET.SubElement(root, "NonLinearPositionLoopConfig")
    _put_text(nlp, "Mode", cfg.nlp_mode)
    _put_text(nlp, "ResetPosFilt", cfg.nlp_reset_pid)
    _put_float_text(nlp, "DeadBand", cfg.nlp_dead_band)
    _put_float_text(nlp, "RisingRange", cfg.nlp_rise_range)
    cascaded = ET.SubElement(root, "CascadedPositionControlSetting")
    _put_float_text(cascaded, "HystereseValue", cfg.cascaded_hysterese)
    casc_filters = ET.SubElement(cascaded, "CascadedFilter")
    for index, filt in enumerate(_filter_values(cfg.cascaded_filters, 3)):
        _put_filter(casc_filters, f"Filt{index}", filt)
    trace = ET.SubElement(root, "TraceSetting")
    _put_io(trace, "TraceIOSignal0", cfg.trace_io_0)
    _put_io(trace, "TraceIOSignal1", cfg.trace_io_1)
    _put_text(trace, "TraceFilterFlag", cfg.trace_filter_flag)
    _put_text(trace, "SampleNumber", cfg.trace_no_samples)
    _put_text(trace, "TraceUnderSample", cfg.trace_undersample)
    event = ET.SubElement(root, "EventLoggingSetting")
    _put_text(event, "LoggingType", cfg.event_logging_type)
    _put_text(event, "UsedIOSignalNum", cfg.event_used_io_signal_num)
    _put_text(event, "SamplesNum", cfg.event_samples_num)
    _put_text(event, "Undersample", cfg.event_undersample)
    _put_text(event, "DelaySamplesNum", cfg.event_delay_samples_num)
    _put_text(event, "Average", cfg.event_average)
    event_config = ET.SubElement(event, "Event")
    _put_text(
        event_config, "MinTriggerSamples", cfg.event_min_trigger_samples
    )
    _put_float_text(event_config, "Threshold", cfg.event_threshold)
    _put_io(event_config, "EventIOSignal", cfg.event_io_signal)
    signals = ET.SubElement(event, "LoggingIOSignal")
    for index, signal in enumerate(_io_values(cfg.event_monitor_signals, 40)):
        _put_io(signals, f"IOSignal{index}", signal)
    zms_thresholds = _number_values(cfg.zms_thresholds, 12)
    ET.SubElement(root, "ZMSSetting", {
        f"Threshold{index}": _format_float(value)
        for index, value in enumerate(zms_thresholds)
    })

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    payload = (
        '<?xml version="1.0"?>\n'
        f'<!--{GENERATOR_COMMENT}-->\n'
        f'{xml_body}'
    ).replace("\n", "\r\n")
    Path(path).write_bytes(payload.encode("utf-8"))


# ======================================================================
# Apply to controller
# ======================================================================
def apply_config_to_session(cfg: SambaConfig, session) -> list[str]:
    """Apply a loaded config and return explicit per-command failures."""
    from python_samba.protocol.commands import FilterStage

    if not session.connected:
        raise RuntimeError("Session not connected")

    errors: list[str] = []

    vel_axis_count = 6
    pneum_axis_count = 3
    pos_axis_count = 6
    geophone_count = 7
    proximity_count = 6
    vel_stage_count = 7
    pos_stage_count = 4
    pneum_input_count = 4
    pneum_output_count = 4
    try:
        system_constants = session.get_global_system_constants()
        vel_axis_count = max(1, min(6, int(system_constants[1])))
        pneum_axis_count = max(1, min(3, int(system_constants[2])))
        pos_axis_count = max(1, min(12, int(system_constants[3])))
        geophone_count = max(1, min(8, int(system_constants[4])))
        proximity_count = max(1, min(8, int(system_constants[5])))
        vel_stage_count = max(1, min(7, int(system_constants[6])))
        pos_stage_count = max(1, min(12, int(system_constants[7])))
        capability_tokens = [str(value).upper() for value in system_constants[11:]]
        for token in capability_tokens:
            if token.startswith("POSAXES#"):
                _, _, count = token.partition("#")
                pos_axis_count = max(1, min(12, int(count)))
            elif token.startswith("PNEUMIO#"):
                parts = token.split("#")
                if len(parts) == 3:
                    pneum_input_count = max(1, min(8, int(parts[1])))
                    pneum_output_count = max(1, min(8, int(parts[2])))
    except Exception:
        capability_tokens = []
    capabilities_known = bool(capability_tokens)

    def supports(*markers: str) -> bool:
        if not capabilities_known:
            return True
        return any(
            marker.upper() in token
            for marker in markers
            for token in capability_tokens
        )

    file_configuration = str(cfg.system_configuration or "").strip()
    file_capability_tokens = [
        token.upper() for token in file_configuration.split()
    ]

    def file_supports(*markers: str) -> bool:
        # Very old/hand-authored files may omit SystemConfiguration entirely;
        # retain the former permissive behaviour for those files.  A normal
        # vendor file contains the BGGSC text and must satisfy the same
        # positive feature marker as the connected controller.
        if not file_configuration:
            return True
        return any(
            marker.upper() in token
            for marker in markers
            for token in file_capability_tokens
        )

    def supports_in_file_and_controller(*markers: str) -> bool:
        return supports(*markers) and file_supports(*markers)

    if capabilities_known and not any(
        token.startswith("PNEUMIO#") for token in capability_tokens
    ):
        if supports("PNEUMRAMP", "PRAMP"):
            pneum_output_count = 6

    def apply(label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    apply("loop status", lambda: session.set_loop_status(
        cfg.individual_loop_status, cfg.loop_status
    ))
    if supports_in_file_and_controller("PPILS"):
        apply("position/pneumatic loop status", lambda: (
            session.set_pos_pneum_individual_loop_status(
                cfg.pos_individual_loop_status, cfg.pneum_individual_loop_status
            )
        ))
    apply("output limit", lambda: session.set_output_limit(cfg.motors_limit))
    apply("sample frequency", lambda: session.set_sample_frequency(cfg.sample_frequency))
    apply("controller configuration", lambda: session.set_controller_config(
        cfg.firmware_configuration
    ))
    apply("startup ramp", lambda: session.set_startup_ramp(
        cfg.output_ramp_type, cfg.output_ramp_time
    ))
    perf = cfg.perf_monitor_signal
    apply("performance monitor", lambda: session.set_performance_monitor(
        perf.type, perf.main_index, perf.sub_index, cfg.perf_threshold,
        cfg.perf_min_time, cfg.perf_hold_time,
    ))
    switch = cfg.switch_io_signal
    apply("switch signal", lambda: session.set_switch_signal(
        switch.type, switch.main_index, switch.sub_index
    ))
    apply("switch conditions", lambda: session.set_switch_conditions(
        cfg.switch_trigger_level, cfg.switch_min_trigger_time,
        cfg.switch_hold_time, cfg.loop_switch_config,
    ))
    apply("excitation parameters", lambda: session.set_excitation_params(
        cfg.excitation_type, *cfg.excitation_params[:4]
    ))
    apply("diagnostic output signals", lambda: session.set_diagnostic_outputs(
        *_flatten_io([cfg.diag_io_signal_0, cfg.diag_io_signal_1], 2)
    ))
    apply("noise injection signal", lambda: session.set_noise_inject_point(
        *_flatten_io([cfg.noise_injection_signal], 1)
    ))
    for stage_index, fil in enumerate(cfg.excitation_filters[:4]):
        stage = FilterStage(
            0, stage_index, fil.type,
            (fil.par[0], fil.par[1], fil.par[2], fil.par[3], fil.par[4]),
        )
        apply(
            f"excitation filter {stage_index}",
            lambda stage=stage: session.set_noise_filter_stage(stage),
        )
    trace_values = _flatten_io([cfg.trace_io_0, cfg.trace_io_1], 2) + [
        cfg.trace_undersample,
        max(1, cfg.trace_no_samples),
        cfg.trace_filter_flag,
    ]

    def same_trace_setup(current: list[Any], target: list[Any]) -> bool:
        if len(current) != len(target):
            return False
        for left, right in zip(current, target):
            try:
                if abs(float(left) - float(right)) > 1e-7:
                    return False
            except (TypeError, ValueError):
                if str(left).strip().upper() != str(right).strip().upper():
                    return False
        return True

    try:
        current_trace_values = session.get_digital_trace_info()
    except Exception:
        current_trace_values = []
    # On this firmware even a same-value DSTIV changes DGTAS to busy.  Avoid
    # disturbing trace state when the setup file contains the live values.
    if not same_trace_setup(current_trace_values, trace_values):
        apply("trace information", lambda: session.set_digital_trace_info(
            *trace_values
        ))
    # DGETP reports a disabled event recorder with zero sample/signal counts.
    # Turning those zeros into ones changed the controller merely by loading a
    # snapshot.  Preserve that sentinel by omitting the complete event group.
    event_recorder_configured = (
        cfg.event_samples_num > 0
        and cfg.event_used_io_signal_num > 0
        and cfg.event_undersample > 0
    )
    if event_recorder_configured:
        apply("event trace parameters", lambda: session.set_event_trace_params(
            cfg.event_logging_type,
            cfg.event_samples_num,
            cfg.event_used_io_signal_num,
            cfg.event_undersample,
            cfg.event_delay_samples_num,
            cfg.event_average,
        ))
        apply("event signal", lambda: session.set_event_signal(
            *_flatten_io([cfg.event_io_signal], 1),
            cfg.event_threshold,
            cfg.event_min_trigger_samples,
        ))
        for signal_index, signal in enumerate(cfg.event_monitor_signals[:40]):
            apply(
                f"event monitor signal {signal_index}",
                lambda signal_index=signal_index, signal=signal: (
                    session.set_monitor_signal(
                        signal_index, *_flatten_io([signal], 1)
                    )
                ),
            )
    apply("motor protection", lambda: session.set_motor_overcurrent_config(
        "N" if cfg.disable_all_flag else "F",
        cfg.reset_delay_time,
        *cfg.motor_thresholds[:12],
    ))
    apply("motor cooling constant", lambda: (
        session.set_motor_overcurrent_cooling_constant(cfg.motor_cooling_constant)
    ))
    apply("velocity output limiter", lambda: (
        session.set_vel_axes_output_limiter(cfg.vel_axis_output_limiter[:6])
    ))

    # Velocity filters
    for axis_name, filters in cfg.vel_filters.items():
        try:
            axis = VEL_AXES.index(axis_name)
        except ValueError:
            continue
        if axis >= vel_axis_count:
            continue
        for st, fil in enumerate(filters[:vel_stage_count]):
            def write_filter(axis=axis, st=st, fil=fil) -> None:
                stage = FilterStage(axis=axis, stage=st, filter_type=fil.type,
                                    params=(fil.par[0], fil.par[1], fil.par[2], fil.par[3], fil.par[4]))
                session.set_velocity_filter(stage)
            apply(f"velocity filter {axis_name}/{st}", write_filter)

    # Velocity sensor matrix
    for axis_name, vals in cfg.vel_sensor_matrix.items():
        if axis_name not in VEL_AXES:
            continue
        axis = VEL_AXES.index(axis_name)
        if axis >= vel_axis_count:
            continue
        apply(f"velocity sensor matrix {axis_name}", lambda axis=axis, vals=vals: (
            session.set_velocity_sensor_matrix(axis, vals[:geophone_count])
        ))

    # Velocity motor matrix
    for axis_name, vals in cfg.vel_motor_matrix.items():
        if axis_name not in VEL_AXES:
            continue
        axis = VEL_AXES.index(axis_name)
        if axis >= vel_axis_count:
            continue
        apply(f"velocity motor matrix {axis_name}", lambda axis=axis, vals=vals: (
            session.set_velocity_motor_matrix(axis, vals[:12])
        ))

    # Proximity offsets
    apply(
        "proximity offsets",
        lambda: session.set_proximity_offsets(cfg.prox_offsets[:proximity_count]),
    )

    # Position filters
    for axis_name, filters in cfg.pos_filters.items():
        try:
            axis = POS_AXES.index(axis_name)
        except ValueError:
            continue
        if axis >= pos_axis_count:
            continue
        for st, fil in enumerate(filters[:pos_stage_count]):
            def write_filter(axis=axis, st=st, fil=fil) -> None:
                stage = FilterStage(axis=axis, stage=st, filter_type=fil.type,
                                    params=(fil.par[0], fil.par[1], fil.par[2], fil.par[3], fil.par[4]))
                session.set_proximity_filter(stage)
            apply(f"position filter {axis_name}/{st}", write_filter)

    for axis_name, vals in cfg.pos_sensor_matrix.items():
        if axis_name not in POS_AXES:
            continue
        axis = POS_AXES.index(axis_name)
        if axis >= pos_axis_count:
            continue
        apply(f"position sensor matrix {axis_name}", lambda axis=axis, vals=vals: (
            session.set_position_sensor_matrix(axis, vals[:6])
        ))
    for axis_name, values in cfg.pos_sensor_devices.items():
        if axis_name not in POS_AXES:
            continue
        axis = POS_AXES.index(axis_name)
        if axis >= pos_axis_count:
            continue
        apply(
            f"position sensor devices {axis_name}",
            lambda axis=axis, values=values: (
                session.set_position_sensor_devices_for_axis(
                    axis, _flatten_io(values, 6)
                )
            ),
        )
    for axis_name, vals in cfg.pos_motor_matrix.items():
        if axis_name not in POS_AXES:
            continue
        axis = POS_AXES.index(axis_name)
        if axis >= pos_axis_count:
            continue
        apply(f"position motor matrix {axis_name}", lambda axis=axis, vals=vals: (
            session.set_position_motor_matrix(axis, vals[:8])
        ))
    for axis_name, values in cfg.pos_motor_devices.items():
        if axis_name not in POS_AXES:
            continue
        axis = POS_AXES.index(axis_name)
        if axis >= pos_axis_count:
            continue
        apply(
            f"position motor devices {axis_name}",
            lambda axis=axis, values=values: (
                session.set_position_motor_devices_for_axis(
                    axis, _flatten_io(values, 8)
                )
            ),
        )

    for axis_name in PNEU_AXES:
        if axis_name in cfg.pneum_sensor_matrix or axis_name in cfg.pneum_motor_matrix:
            axis = PNEU_AXES.index(axis_name)
            if axis >= pneum_axis_count:
                continue
            sensor_values = (
                cfg.pneum_sensor_matrix.get(axis_name, []) + [0.0] * 8
            )[:pneum_input_count]
            motor_values = (
                cfg.pneum_motor_matrix.get(axis_name, []) + [0.0] * 8
            )[:pneum_output_count]
            apply(f"pneumatic steering {axis_name}", lambda axis=axis, values=sensor_values + motor_values: (
                session.set_pneumatic_steering_matrix(axis, values)
            ))
    for axis_name, filters in cfg.pneum_filters.items():
        if axis_name not in PNEU_AXES:
            continue
        axis = PNEU_AXES.index(axis_name)
        if axis >= pneum_axis_count:
            continue
        for stage_index, fil in enumerate(filters):
            stage = FilterStage(
                axis, stage_index, fil.type,
                (fil.par[0], fil.par[1], fil.par[2], fil.par[3], fil.par[4]),
            )
            apply(f"pneumatic filter {axis_name}/{stage_index}", lambda stage=stage: (
                session.set_pneumatic_filter(stage)
            ))
    apply("pneumatic configuration", lambda: session.set_pneumatic_config(
        cfg.pneum_soft_up_height, cfg.pneum_setpoint, cfg.pneum_mode_tolerance
    ))
    apply("pneumatic valve offsets", lambda: session.set_pneumatic_valve_offsets(
        cfg.pneum_up_valve_offsets[:pneum_output_count]
        + cfg.pneum_down_valve_offsets[:pneum_output_count]
    ))
    apply("pneumatic setpoint mode", lambda: session.set_pneumatic_setpoint_status(
        cfg.pneum_use_setpoint_all
    ))
    apply("dither value", lambda: session.set_dither_value(cfg.dither_value))
    apply("dither frequency", lambda: session.set_dither_frequency(cfg.dither_frequency))
    apply("dither compensation", lambda: session.set_dither_alpha(cfg.dither_compensation))
    apply("motor/isolator offsets", lambda: session.set_motor_offsets(cfg.motor_offset[:11]))
    if supports_in_file_and_controller("PNEUMRAMP", "PRAMP"):
        apply("pneumatic ramp parameters", lambda: session.set_pneumatic_ramp_parameters(
            cfg.pneum_ramp_switch_to_up,
            cfg.pneum_ramp_setpoint_gradient,
            cfg.pneum_ramp_up_gradient,
            cfg.pneum_ramp_down_gradient,
            cfg.pneum_ramp_pressure_offset_time,
        ))
    if supports_in_file_and_controller("SALMO"):
        apply("linear motor offsets", lambda: session.set_linear_motor_offsets(
            cfg.linear_motor_offsets[:12]
        ))

    try:
        adc_count = len(session.get_adc_sequence())
    except Exception:
        adc_count = 25
    apply("ADC mapping", lambda: session.set_adc_sequence(cfg.adc_mapping[:adc_count]))
    apply("DAC mapping", lambda: session.set_dac_sequence(cfg.dac_mapping[:20]))
    apply("ADC set number", lambda: session.set_adc_set_number(cfg.adc_channel_set_num))
    if supports_in_file_and_controller("TMPSENS"):
        apply("temperature sensor ADC mapping", lambda: session.set_temp_sensor_adc_mapping(
            cfg.temp_sensor_adc_mapping[:12]
        ))
    if supports_in_file_and_controller("PSUCL"):
        apply("power supply current limitation", lambda: session.set_power_supply_parameters(
            cfg.power_supply_current_limit, cfg.power_supply_current_si_unit, 0, 0
        ))

    # FSFFC expects NoOfGains + UseFBSignals (Y/N).  The source derives the
    # flag from the 0x1000 loop-status bit; FFOutputThreshold is written by
    # the separate BSFFL command.
    ff_use_feedback = "Y" if cfg.loop_status & 0x1000 else "N"
    apply("FF configuration", lambda: session.set_ff_config(
        cfg.ff_no_gains, ff_use_feedback
    ))
    apply("FF output limit", lambda: session.set_ff_output_limit(
        cfg.ff_output_threshold
    ))
    apply("FF source inputs", lambda: session.set_ff_inputs(*cfg.ff_source_inputs[:7]))
    ff_adaptive = "T" if cfg.loop_status & 0x2 else "F"
    for source in range(7):
        apply(f"FF source {source} parameters", lambda source=source: (
            session.set_ff_parameters(
                source,
                cfg.ff_output_matrix[source],
                ff_adaptive,
                cfg.ff_adaption_rate[source],
            )
        ))
        for stage_index, fil in enumerate(cfg.ff_ref_filter.get(f"Source{source}", [])):
            stage = FilterStage(source, stage_index, fil.type, tuple(fil.par[:5]))
            apply(f"FF reference filter {source}/{stage_index}", lambda stage=stage: (
                session.set_ff_filter(stage)
            ))
        for stage_index, fil in enumerate(cfg.ff_sec_filter.get(f"Source{source}", [])):
            stage = FilterStage(source, stage_index + 3, fil.type, tuple(fil.par[:5]))
            apply(f"FF secondary filter {source}/{stage_index}", lambda stage=stage: (
                session.set_ff_filter(stage)
            ))
        flattened: list[float] = []
        axes = cfg.ff_gains.get(f"Source{source}", {})
        for axis_name in VEL_AXES:
            flattened.extend(axes.get(axis_name, []))
        if flattened:
            apply(f"FF gains source {source}", lambda source=source, values=flattened: (
                session.set_ff_gains(source, *values)
            ))
    for axis, axis_name in enumerate(VEL_AXES):
        for stage_index, fil in enumerate(cfg.ff_err_filter.get(axis_name, [])):
            stage = FilterStage(axis, stage_index + 6, fil.type, tuple(fil.par[:5]))
            apply(f"FF error filter {axis_name}/{stage_index}", lambda stage=stage: (
                session.set_ff_filter(stage)
            ))
    apply("FF stage multipliers", lambda: session.set_stage_ff_multipliers([
        cfg.ff_mult_xacc, cfg.ff_mult_yacc, cfg.ff_mult_xpos, cfg.ff_mult_ypos
    ]))
    apply("FF Zrot parameters", lambda: session.set_ff_zrot_parameters(
        cfg.ff_xpos_max, cfg.ff_ypos_max, cfg.ff_xpos_offset, cfg.ff_ypos_offset
    ))

    apply("PFF configuration", lambda: session.set_pff_config(
        cfg.pff_no_gains, cfg.pff_output_threshold
    ))
    apply("PFF source inputs", lambda: session.set_pff_inputs(cfg.pff_source_inputs[:4]))
    for source in range(4):
        apply(f"PFF source {source} parameters", lambda source=source: (
            session.set_pff_parameters(
                source, cfg.pff_output_matrix[source], cfg.pff_adaption_rate[source]
            )
        ))
        for stage_index, fil in enumerate(cfg.pff_ref_filter.get(f"Source{source}", [])):
            apply(f"PFF reference filter {source}/{stage_index}", lambda source=source, stage_index=stage_index, fil=fil: (
                session.set_pff_filter(0, source, stage_index, fil.type, tuple(fil.par[:5]))
            ))
        for stage_index, fil in enumerate(cfg.pff_sec_filter.get(f"Source{source}", [])):
            apply(f"PFF secondary filter {source}/{stage_index}", lambda source=source, stage_index=stage_index, fil=fil: (
                session.set_pff_filter(0, source, stage_index + 3, fil.type, tuple(fil.par[:5]))
            ))
        axes = cfg.pff_gains.get(f"Source{source}", {})
        for axis, axis_name in enumerate(PNEU_AXES):
            values = axes.get(axis_name, [])
            if values:
                apply(f"PFF gains {axis_name}/source {source}", lambda axis=axis, source=source, values=values: (
                    session.set_pff_gains(axis, source, values)
                ))
    for axis, axis_name in enumerate(PNEU_AXES):
        for stage_index, fil in enumerate(cfg.pff_err_filter.get(axis_name, [])):
            apply(f"PFF error filter {axis_name}/{stage_index}", lambda axis=axis, stage_index=stage_index, fil=fil: (
                session.set_pff_filter(axis, 0, stage_index + 6, fil.type, tuple(fil.par[:5]))
            ))

    if supports_in_file_and_controller("NLP"):
        apply("NLP parameters", lambda: session.set_non_linear_position_parameter(
            cfg.nlp_mode, cfg.nlp_reset_pid, cfg.nlp_dead_band, cfg.nlp_rise_range
        ))
    if supports_in_file_and_controller("CASCADED"):
        apply("cascaded position parameter", lambda: session.set_cascaded_position_parameter(
            cfg.cascaded_hysterese
        ))
        for stage_index, fil in enumerate(cfg.cascaded_filters):
            apply(f"cascaded position filter {stage_index}", lambda stage_index=stage_index, fil=fil: (
                session.set_cascaded_position_filter(stage_index, fil.type, *fil.par[:5])
            ))
    if supports_in_file_and_controller("ZMS"):
        apply("ZMS thresholds", lambda: session.set_zms_stability_thresholds(
            *cfg.zms_thresholds[:12]
        ))
    return errors


def load_and_apply(path: str, session) -> list[str]:
    """Load a .SAMBA19x_Config file and apply it to a connected session."""
    cfg = load_config(path)
    return apply_config_to_session(cfg, session)
