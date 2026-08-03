"""Transactional live-controller probe for every non-Save/Load UI port.

The write phase is deliberately non-destructive: every writable parameter is
read first, the exact controller value is written back, and the value is read
again.  If verification fails, the original value is written once more and
verified immediately.  Physical actions (motion, resets, adopting live values,
starting traces, NVRAM operations) are inventoried but never executed.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from python_samba.protocol.commands import FilterStage, RciCommandError
from python_samba.services.session import open_serial
from python_samba.transport.serial_port import TransportError


Getter = Callable[[], Any]
Setter = Callable[[Any], None]
Projector = Callable[[Any], Any]
WriteGuard = Callable[[Any], tuple[bool, str]]


@dataclass
class Endpoint:
    page: str
    name: str
    read_command: str
    getter: Getter | None
    write_command: str = ""
    setter: Setter | None = None
    projector: Projector | None = None
    risk: str = "parameter"
    note: str = ""
    supported: bool = True
    write_guard: WriteGuard | None = None


@dataclass
class Result:
    page: str
    name: str
    read_command: str
    write_command: str
    phase: str
    status: str
    detail: str = ""


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, FilterStage) and isinstance(right, FilterStage):
        return (
            left.axis == right.axis
            and left.stage == right.stage
            and left.filter_type == right.filter_type
            and _equivalent(left.params, right.params)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _equivalent(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right)
        )
    left_number = _numeric(left)
    right_number = _numeric(right)
    if left_number is not None and right_number is not None:
        return math.isclose(left_number, right_number, rel_tol=1e-5, abs_tol=1e-7)
    return str(left).strip().upper() == str(right).strip().upper()


def _error_status(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, RciCommandError):
        response = exc.response
        if response.status_code == 0x03:
            return "UNSUPPORTED", str(exc)
        return "REJECTED", str(exc)
    if isinstance(exc, TransportError):
        return "TRANSPORT_LOST", str(exc)
    return "ERROR", f"{type(exc).__name__}: {exc}"


def _filter_setter(setter: Callable[[FilterStage], None]) -> Setter:
    return lambda value: setter(value)


def _event_trace_write_guard(value: Any) -> tuple[bool, str]:
    """Do not replay DGETP's disabled sentinel through stricter DSETP."""
    values = list(value) if isinstance(value, (list, tuple)) else []
    try:
        disabled = (
            len(values) == 6
            and tuple(int(item) for item in values[:3]) == (0, 0, 0)
        )
    except (TypeError, ValueError):
        disabled = False
    if disabled:
        return (
            False,
            "DGETP reports the disabled/unconfigured sentinel; DSETP rejects "
            "its zero MaxBuffLen/MonSigNum",
        )
    return True, ""


def build_endpoints(session, constants: list[str]) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    add = endpoints.append
    vel_axes = max(1, min(6, int(constants[1])))
    pneu_axes = max(1, min(3, int(constants[2])))
    pos_axes = max(1, min(12, int(constants[3])))
    prox_count = max(1, min(8, int(constants[5])))
    vel_stages = max(1, min(7, int(constants[6])))
    pos_stages = max(1, min(12, int(constants[7])))
    features = {str(token).upper() for token in constants[11:]}
    cascaded_supported = "CASCADED" in features
    pneumatic_ramp_supported = bool(
        features.intersection({"PNEUMRAMP", "PRAMP"})
    )
    safety_supported = bool(features.intersection({"PNEUMRAMP", "SEQ"}))
    zms_supported = bool(features.intersection({"ZMS", "ZMS2"}))
    analysis_supported = "NAF" not in features

    # Connect / Controller / Status -------------------------------------------------
    add(Endpoint("Connect", "Firmware version", "NGVER", session.get_version))
    add(Endpoint("Controller", "Global system constants", "BGGSC", session.get_global_system_constants))
    add(Endpoint("Controller", "Controller type", "BGCOT", session.get_controller_type,
                 "BSCOT", lambda value: session.set_controller_type(*value), risk="high"))
    add(Endpoint("Controller", "Loop status words", "BGSTS", session.get_loop_status,
                 "BSSTS", lambda value: session.set_loop_status(value.individual, value.system), risk="high"))
    add(Endpoint(
        "Controller", "Position/pneumatic loop words", "BGSST",
        session.get_pos_pneum_digital_status, "BSSST",
        lambda value: session.set_pos_pneum_individual_loop_status(value[0], value[1]),
        projector=lambda value: tuple(value[:2]), risk="high",
    ))
    add(Endpoint("Controller", "Output limit", "BGOPL", session.get_output_limit,
                 "BSOPL", session.set_output_limit, risk="high"))
    add(Endpoint("Controller", "Sample frequency", "NGSFR", session.get_sample_frequency,
                 "NSSFR", session.set_sample_frequency, risk="high"))
    add(Endpoint("Controller", "Startup ramp", "BGSUT", session.get_startup_ramp,
                 "BSSUT", lambda value: session.set_startup_ramp(*value)))
    add(Endpoint("Controller", "Performance monitor", "DGPMV", session.get_performance_monitor,
                 "DSPMV", lambda value: session.set_performance_monitor(*value)))
    add(Endpoint("Controller", "Performance status", "DGPMS", session.get_performance_status))
    add(Endpoint("Controller", "System load", "DGSLO", session.get_system_load))
    add(Endpoint("Controller", "Switch signal", "BGSWS", session.get_switch_signal,
                 "BSSWS", lambda value: session.set_switch_signal(*value)))
    add(Endpoint("Controller", "Switch conditions", "BGOCD", session.get_switch_conditions,
                 "BSOCD", lambda value: session.set_switch_conditions(*value)))
    add(Endpoint("Controller", "Switch status", "DGCSS", session.get_switch_status))
    add(Endpoint("Controller", "Motor protection", "BGOCV", session.get_motor_overcurrent_config,
                 "BSOCV", lambda value: session.set_motor_overcurrent_config(*value), risk="high"))
    add(Endpoint("Controller", "Motor cooling constant", "BGMCC",
                 session.get_motor_overcurrent_cooling_constant, "BSMCC",
                 session.set_motor_overcurrent_cooling_constant, risk="high"))
    add(Endpoint("Controller", "Motor offsets", "CGMOV", session.get_motor_offsets,
                 "CSMOV", session.set_motor_offsets, risk="high"))
    add(Endpoint("Controller", "Motor power values", "BGMPV", session.get_motor_power_values))
    add(Endpoint("Controller", "Motor failsafe status", "BGMPS", session.get_motor_failsafe_status))
    add(Endpoint("Controller", "ADC sequence", "BGADS", session.get_adc_sequence,
                 "BSADS", session.set_adc_sequence, risk="high"))
    add(Endpoint("Controller", "ADC set number", "NGASN", session.get_adc_set_number,
                 "NSASN", session.set_adc_set_number, risk="high"))
    add(Endpoint("Controller", "Temperature ADC mapping", "BGTSA",
                 session.get_temp_sensor_adc_mapping, "BSTSA",
                 session.set_temp_sensor_adc_mapping, risk="high"))
    add(Endpoint("Controller", "DAC sequence", "BGDAS", session.get_dac_sequence,
                 "BSDAS", session.set_dac_sequence, risk="high"))
    add(Endpoint(
        "Controller", "Power-supply current limit", "LGPSL",
        session.get_power_supply_parameters, "LSPSL",
        lambda value: session.set_power_supply_parameters(value[0], value[1], 0, 0),
        projector=lambda value: tuple(value[:2]), risk="high",
    ))
    add(Endpoint("Controller", "Reset power-supply counter", "", None, "LSPSL", None,
                 risk="action", note="irreversible counter reset"))
    add(Endpoint("Controller", "Reset power-supply maximum", "", None, "LSPSL", None,
                 risk="action", note="irreversible maximum reset"))
    add(Endpoint("Status", "Geophone live inputs", "VGGIV", session.get_geophone_inputs))
    add(Endpoint("Status", "Amplifier disable events", "DGADE", session.get_amplifier_disable_events))

    # Signals / Digital I/O ----------------------------------------------------------
    for index in range(16):
        add(Endpoint(
            "Signals", f"Monitor signal {index}", "DGMOS",
            lambda index=index: session.get_monitor_signal(index), "DSMOS",
            lambda value, index=index: session.set_monitor_signal(index, *value),
        ))
    add(Endpoint("Signals", "Monitor live values", "DGMSV",
                 lambda: session.get_monitor_values(0, 15)))
    add(Endpoint("Digital I/O", "Position/pneumatic/digital words", "BGSST",
                 session.get_pos_pneum_digital_status))

    # Velocity ----------------------------------------------------------------------
    add(Endpoint("Velocity", "Axis output limiter", "BGFBL",
                 session.get_vel_axes_output_limiter, "BSFBL",
                 session.set_vel_axes_output_limiter, risk="high"))
    for axis in range(vel_axes):
        add(Endpoint(
            "Velocity", f"Sensor matrix axis {axis}", "VGSMV",
            lambda axis=axis: session.get_velocity_sensor_matrix(axis), "VSSMV",
            lambda value, axis=axis: session.set_velocity_sensor_matrix(axis, value), risk="high",
        ))
        add(Endpoint(
            "Velocity", f"Motor matrix axis {axis}", "VGMMV",
            lambda axis=axis: session.get_velocity_motor_matrix(axis), "VSMMV",
            lambda value, axis=axis: session.set_velocity_motor_matrix(axis, value), risk="high",
        ))
        for stage in range(vel_stages):
            add(Endpoint(
                "Velocity", f"Filter axis {axis}/stage {stage}", "VGVFS",
                lambda axis=axis, stage=stage: session.get_velocity_filter(axis, stage), "VSVFS",
                _filter_setter(session.set_velocity_filter),
            ))

    # Position / Excitation / Diagnostics -------------------------------------------
    add(Endpoint(
        "Position", "Proximity offsets", "CGPOV" if prox_count == 6 else "CGPOX",
        lambda: session.get_proximity_offsets(prox_count),
        "CSPOV" if prox_count == 6 else "CSPOX", session.set_proximity_offsets, risk="high",
    ))
    add(Endpoint(
        "Position", "Proximity live values", "PGGIV" if prox_count == 6 else "PGGIX",
        lambda: session.get_proximity_input_values(prox_count),
    ))
    for axis in range(pos_axes):
        add(Endpoint(
            "Position", f"Sensor devices axis {axis}", "CGPSD",
            lambda axis=axis: session.get_position_sensor_devices_for_axis(axis), "CSPSD",
            lambda value, axis=axis: session.set_position_sensor_devices_for_axis(
                axis, [int(item) for item in value]
            ), risk="high",
        ))
        add(Endpoint(
            "Position", f"Sensor matrix axis {axis}", "CGSMV",
            lambda axis=axis: session.get_position_sensor_matrix(axis), "CSSMV",
            lambda value, axis=axis: session.set_position_sensor_matrix(axis, value), risk="high",
        ))
        add(Endpoint(
            "Position", f"Motor devices axis {axis}", "CGPMD",
            lambda axis=axis: session.get_position_motor_devices_for_axis(axis), "CSPMD",
            lambda value, axis=axis: session.set_position_motor_devices_for_axis(
                axis, [int(item) for item in value]
            ), risk="high",
        ))
        add(Endpoint(
            "Position", f"Motor matrix axis {axis}", "CGMMV",
            lambda axis=axis: session.get_position_motor_matrix(axis), "CSMMV",
            lambda value, axis=axis: session.set_position_motor_matrix(axis, value), risk="high",
        ))
        for stage in range(pos_stages):
            add(Endpoint(
                "Position", f"Filter axis {axis}/stage {stage}", "CGPFS",
                lambda axis=axis, stage=stage: session.get_proximity_filter(axis, stage), "CSPFS",
                _filter_setter(session.set_proximity_filter),
            ))
    add(Endpoint("Position", "Non-linear position parameters", "CGPNP",
                 session.get_non_linear_position_parameter, "CSSFP",
                 lambda value: session.set_non_linear_position_parameter(*value), risk="high"))
    add(Endpoint("Position", "Cascaded position parameters", "CGPCM",
                 session.get_cascaded_position_parameter, "CSCPP",
                 lambda value: session.set_cascaded_position_parameter(*value), risk="high",
                 note="requires BGGSC Cascaded", supported=cascaded_supported))
    for stage in range(3):
        add(Endpoint(
            "Position", f"Cascaded filter stage {stage}", "CGPCF",
            lambda stage=stage: session.get_cascaded_position_filter(stage), "CSCPF",
            lambda value, stage=stage: session.set_cascaded_position_filter(
                stage, value.filter_type, *value.params
            ), risk="high", note="requires BGGSC Cascaded",
            supported=cascaded_supported,
        ))
    add(Endpoint("Position", "Excitation parameters", "DGESP", session.get_excitation_params,
                 "DSESP", lambda value: session.set_excitation_params(*value), risk="high"))
    add(Endpoint("Position", "Noise type", "DGNTY", session.get_noise_type,
                 "DSNTY", session.set_noise_type, risk="high"))
    add(Endpoint("Position", "Noise gain", "DGNSG", session.get_noise_gain,
                 "DSNSG", session.set_noise_gain, risk="high"))
    add(Endpoint("Position", "Noise injection signal", "DGNIP", session.get_noise_inject_point,
                 "DSNIP", lambda value: session.set_noise_inject_point(*value), risk="high"))
    add(Endpoint("Position", "Noise frequency", "DGNSF", session.get_noise_frequency,
                 "DSNSF", session.set_noise_frequency, risk="high"))
    add(Endpoint("Position", "Noise filter usage", "DGNFU", session.get_noise_filter_usage,
                 "DSNFU", session.set_noise_filter_usage, risk="high"))
    for stage in range(4):
        add(Endpoint(
            "Position", f"Excitation filter stage {stage}", "DGNFS",
            lambda stage=stage: session.get_noise_filter_stage(stage), "DSNFS",
            _filter_setter(session.set_noise_filter_stage), risk="high",
        ))
    add(Endpoint("Position", "Diagnostic output signals", "DGDOS",
                 session.get_diagnostic_outputs, "DSDOS",
                 lambda value: session.set_diagnostic_outputs(*value), risk="high"))
    add(Endpoint("Position", "Diagnostic test mode", "DGTMO", session.get_test_mode,
                 "DSTMO", lambda value: session.set_test_mode(*value), risk="high"))
    add(Endpoint("Position", "Digital trace setup", "DGTIV", session.get_digital_trace_info,
                 "DSTIV", lambda value: session.set_digital_trace_info(*value), risk="high"))
    add(Endpoint("Position", "Digital trace status", "DGTAS", session.get_digital_trace_status))
    add(Endpoint(
        "Position", "Digital trace buffer", "DGTBV",
        session.get_digital_trace_buffer, risk="stateful-read",
        note="requires an active/completed digital trace",
    ))
    add(Endpoint("Position", "Start digital trace", "", None, "DASTA", None,
                 risk="action", note="starts hardware acquisition"))
    add(Endpoint("Position", "Use current proximity offsets", "", None,
                 "CAUCO" if prox_count == 6 else "CAUCX", None,
                 risk="action", note="overwrites calibrated offsets from live sensors"))

    # Pneumatic ---------------------------------------------------------------------
    for axis in range(pneu_axes):
        add(Endpoint(
            "Pneumatic", f"Steering matrix axis {axis}", "PGPSM",
            lambda axis=axis: session.get_pneumatic_steering_matrix(axis), "PSPSM",
            lambda value, axis=axis: session.set_pneumatic_steering_matrix(axis, value), risk="high",
        ))
        for stage in range(4):
            add(Endpoint(
                "Pneumatic", f"Filter axis {axis}/stage {stage}", "PGPAF",
                lambda axis=axis, stage=stage: session.get_pneumatic_filter(axis, stage), "PSPAF",
                _filter_setter(session.set_pneumatic_filter),
            ))
    add(Endpoint("Pneumatic", "Floatation configuration", "PGPCP",
                 session.get_pneumatic_config, "PSPCP",
                 lambda value: session.set_pneumatic_config(*value), risk="high"))
    add(Endpoint("Pneumatic", "Valve offsets", "PGPVO",
                 session.get_pneumatic_valve_offsets, "PSPVO",
                 session.set_pneumatic_valve_offsets, risk="high"))
    add(Endpoint("Pneumatic", "Dither value", "PGDIT", session.get_dither_value,
                 "PSDIT", session.set_dither_value, risk="high"))
    add(Endpoint("Pneumatic", "Dither frequency", "PGDFR", session.get_dither_frequency,
                 "PSDFR", session.set_dither_frequency, risk="high"))
    add(Endpoint("Pneumatic", "Dither compensation", "PGDCA", session.get_dither_alpha,
                 "PSDCA", session.set_dither_alpha, risk="high"))
    add(Endpoint("Pneumatic", "Setpoint mode", "PGPSS",
                 session.get_pneumatic_setpoint_status, "PSPSS",
                 session.set_pneumatic_setpoint_status, risk="high"))
    add(Endpoint("Pneumatic", "Ramp parameters", "PGPRP",
                 session.get_pneumatic_ramp_parameters, "PSPRP",
                 lambda value: session.set_pneumatic_ramp_parameters(*value), risk="high",
                 note="requires BGGSC PRamp/PneumRamp",
                 supported=pneumatic_ramp_supported))
    add(Endpoint("Pneumatic", "Axes status", "PGPAS", session.get_pneumatic_axes_status))
    add(Endpoint("Pneumatic", "Heights and valves", "PGPHV", session.get_pneumatic_heights_valves))
    add(Endpoint("Pneumatic", "OK/NOK timers", "PGPST", session.get_pneumatic_status_timer))
    add(Endpoint("Pneumatic", "Move system up", "", None, "PAMOV", None,
                 risk="action", note="physical motion"))
    add(Endpoint("Pneumatic", "Move system down", "", None, "PAMOV", None,
                 risk="action", note="physical motion"))
    add(Endpoint("Pneumatic", "Use live pressure as up offsets", "", None, "PAUCO 1", None,
                 risk="action", note="overwrites pneumatic up offsets"))
    add(Endpoint("Pneumatic", "Use live pressure as down offsets", "", None, "PAUCO 2", None,
                 risk="action", note="overwrites pneumatic down offsets"))

    # Feed Forward ------------------------------------------------------------------
    add(Endpoint("Feed Forward", "Loop status/config", "FGFFS", session.get_ff_status,
                 "FSFFS", lambda value: session.set_ff_status(*value), risk="high"))
    add(Endpoint("Feed Forward", "Gain configuration", "FGFFC", session.get_ff_config,
                 "FSFFC", lambda value: session.set_ff_config(*value), risk="high"))
    add(Endpoint("Feed Forward", "Output limit", "BGFFL", session.get_ff_output_limit,
                 "BSFFL", session.set_ff_output_limit, risk="high"))
    add(Endpoint("Feed Forward", "Source inputs", "FGFFI", session.get_ff_inputs,
                 "FSFFI", lambda value: session.set_ff_inputs(*value), risk="high"))
    for source in range(7):
        add(Endpoint(
            "Feed Forward", f"Source {source} parameters", "FGFFP",
            lambda source=source: session.get_ff_parameters(source), "FSFFP",
            lambda value, source=source: session.set_ff_parameters(
                source, value[0], value[1], float(value[2])
            ), risk="high",
        ))
        add(Endpoint(
            "Feed Forward", f"Source {source} gains", "FGFFG",
            lambda source=source: session.get_ff_gains(source), "FSFFG",
            lambda value, source=source: session.set_ff_gains(source, *value), risk="high",
        ))
        for stage in range(6):
            add(Endpoint(
                "Feed Forward", f"Source {source} filter stage {stage}", "FGPFS",
                lambda source=source, stage=stage: session.get_ff_filter(source, stage), "FSPFS",
                _filter_setter(session.set_ff_filter),
            ))
    for axis in range(vel_axes):
        for stage in range(6, 8):
            add(Endpoint(
                "Feed Forward", f"Error filter axis {axis}/stage {stage}", "FGPFS",
                lambda axis=axis, stage=stage: session.get_ff_filter(axis, stage), "FSPFS",
                _filter_setter(session.set_ff_filter),
            ))
    add(Endpoint("Feed Forward", "Stage multipliers", "FGSFM",
                 session.get_stage_ff_multipliers, "FSSFM",
                 session.set_stage_ff_multipliers, risk="high"))
    add(Endpoint("Feed Forward", "Z-rotation parameters", "FGZRP",
                 session.get_ff_zrot_parameters, "FSZRP",
                 lambda value: session.set_ff_zrot_parameters(*value), risk="high"))
    add(Endpoint("Feed Forward", "Adaptive algorithm", "FGFAT",
                 session.get_ff_adaptive_algo, "FSFAT",
                 session.set_ff_adaptive_algo, risk="high"))
    add(Endpoint("Feed Forward", "Reset FIR", "", None, "FARFF", None,
                 risk="action", note="clears adaptive FIR state"))

    # Pneumatic Feed Forward ---------------------------------------------------------
    add(Endpoint("Pneumatic FF", "Gain configuration", "FGCPF", session.get_pff_config,
                 "FSCPF", lambda value: session.set_pff_config(*value), risk="high"))
    add(Endpoint("Pneumatic FF", "Source inputs", "FGIPF", session.get_pff_inputs,
                 "FSIPF", session.set_pff_inputs, risk="high"))
    for source in range(4):
        add(Endpoint(
            "Pneumatic FF", f"Source {source} parameters", "FGPPF",
            lambda source=source: session.get_pff_parameters(source), "FSPPF",
            lambda value, source=source: session.set_pff_parameters(
                source, value[0], float(value[1])
            ), risk="high",
        ))
        for stage in range(6):
            add(Endpoint(
                "Pneumatic FF", f"Source {source} filter stage {stage}", "FGFSP",
                lambda source=source, stage=stage: session.get_pff_filter(0, source, stage), "FSFSP",
                lambda value, source=source, stage=stage: session.set_pff_filter(
                    0, source, stage, value.filter_type, value.params
                ),
            ))
        for axis in range(pneu_axes):
            add(Endpoint(
                "Pneumatic FF", f"Gains axis {axis}/source {source}", "FGGPF",
                lambda axis=axis, source=source: session.get_pff_gains(axis, source), "FSGPF",
                lambda value, axis=axis, source=source: session.set_pff_gains(axis, source, value),
                risk="high",
            ))
    for axis in range(pneu_axes):
        for stage in range(6, 8):
            add(Endpoint(
                "Pneumatic FF", f"Error filter axis {axis}/stage {stage}", "FGFSP",
                lambda axis=axis, stage=stage: session.get_pff_filter(axis, 0, stage), "FSFSP",
                lambda value, axis=axis, stage=stage: session.set_pff_filter(
                    axis, 0, stage, value.filter_type, value.params
                ),
            ))
    add(Endpoint("Pneumatic FF", "Reset FIR", "", None, "FARPF", None,
                 risk="action", note="clears adaptive FIR state"))

    # Safety / ZMS ------------------------------------------------------------------
    add(Endpoint("Safety", "Safety/earthquake configuration", "LGSEP",
                 session.get_safety_and_earthquake_config, "LSSEP",
                 session.set_safety_and_earthquake_config, risk="high",
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "Safety RMS/fault words", "LGSRV",
                 session.get_sensor_safety_rms_values,
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "Earthquake RMS/fault words", "LGERV",
                 session.get_sensor_earthquake_rms_values,
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "Safety geophone fault", "LGSRV", session.get_safety_geo_fault,
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "Safety proximity fault", "LGSRV", session.get_safety_prox_fault,
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "Earthquake geophone fault", "LGERV", session.get_earthquake_geo_fault,
                 note="requires BGGSC PneumRamp/SEQ", supported=safety_supported))
    add(Endpoint("Safety", "ZMS thresholds", "BGSVT", session.get_zms_stability_thresholds,
                 "BSSVT", lambda value: session.set_zms_stability_thresholds(*value), risk="high",
                 note="requires BGGSC ZMS/ZMS2", supported=zms_supported))
    add(Endpoint("Safety", "ZMS last failed event", "BGLSE", session.get_zms_last_failed_event,
                 note="requires BGGSC ZMS/ZMS2", supported=zms_supported))
    add(Endpoint("Safety", "ZMS RMS values", "BGSRV", session.get_zms_rms_values,
                 note="requires BGGSC ZMS/ZMS2", supported=zms_supported))
    add(Endpoint("Safety", "ZMS status", "BGSRV", session.get_zms_stability_status,
                 note="requires BGGSC ZMS/ZMS2", supported=zms_supported))

    # Hidden advanced logging/analysis page: config ports only; action commands skip.
    add(Endpoint("Logging", "Event trace parameters", "DGETP", session.get_event_trace_params,
                 "DSETP", lambda value: session.set_event_trace_params(*value),
                 write_guard=_event_trace_write_guard))
    add(Endpoint("Logging", "Event trace information", "DGETI", session.get_event_trace_info))
    add(Endpoint("Logging", "Event signal", "DGETS", session.get_event_signal,
                 "DSETS", lambda value: session.set_event_signal(*value)))
    add(Endpoint("Logging", "Analysis parameters", "LGANP", session.get_analysis_params,
                 "LSANP", lambda value: session.set_analysis_params(*value),
                 note="disabled by BGGSC NAF", supported=analysis_supported))
    add(Endpoint("Logging", "Analysis input", "LGAIS", session.get_analysis_input,
                 "LSAIS", lambda value: session.set_analysis_input(*value),
                 note="disabled by BGGSC NAF", supported=analysis_supported))
    add(Endpoint("Logging", "Analysis filter specification", "LGAFS",
                 session.get_analysis_filter_spec, "LSAFS",
                 lambda value: session.set_analysis_filter_spec(*value),
                 note="disabled by BGGSC NAF", supported=analysis_supported))
    add(Endpoint("Logging", "Analysis filter outputs", "LGAFO", session.get_analysis_filter_outputs,
                 note="disabled by BGGSC NAF", supported=analysis_supported))
    add(Endpoint("Logging", "Analysis events", "LGAEV", session.get_analysis_events,
                 note="disabled by BGGSC NAF", supported=analysis_supported))
    add(Endpoint("Logging", "Start/stop event trace", "", None, "DSSET", None,
                 risk="action", note="changes acquisition state"))

    return endpoints


class Probe:
    def __init__(self, session, mode: str, output_dir: Path, *, quiet: bool = False) -> None:
        self.session = session
        self.mode = mode
        self.output_dir = output_dir
        self.results: list[Result] = []
        self.values: dict[str, Any] = {}
        self.endpoints: list[Endpoint] = []
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.quiet = quiet

    def _key(self, endpoint: Endpoint) -> str:
        return f"{endpoint.page}/{endpoint.name}"

    def _add_result(self, endpoint: Endpoint, phase: str, status: str, detail: str = "") -> None:
        self.results.append(Result(
            endpoint.page, endpoint.name, endpoint.read_command,
            endpoint.write_command, phase, status, detail,
        ))
        if not self.quiet or status != "PASS":
            print(f"{phase:8} {status:14} {endpoint.page} / {endpoint.name}"
                  + (f" :: {detail}" if detail else ""), flush=True)
        self.save()

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.output_dir / f"hardware_ui_ports_{self.stamp}"
        snapshot = {
            "mode": self.mode,
            "timestamp": self.stamp,
            "values": {key: _json_value(value) for key, value in self.values.items()},
        }
        report = {
            "mode": self.mode,
            "timestamp": self.stamp,
            "results": [asdict(result) for result in self.results],
        }
        prefix.with_name(prefix.name + "_snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prefix.with_name(prefix.name + "_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def preflight(self) -> None:
        for endpoint in self.endpoints:
            if not endpoint.supported:
                self._add_result(
                    endpoint, "INVENTORY", "SKIP_UNSUPPORTED", endpoint.note
                )
                continue
            if endpoint.risk == "action":
                self._add_result(endpoint, "INVENTORY", "SKIP_ACTION", endpoint.note)
                continue
            if endpoint.getter is None:
                self._add_result(endpoint, "READ", "NO_GETTER")
                continue
            try:
                value = endpoint.getter()
                self.values[self._key(endpoint)] = copy.deepcopy(value)
                preview = json.dumps(_json_value(value), ensure_ascii=False)
                self._add_result(endpoint, "READ", "PASS", preview[:240])
            except Exception as exc:
                status, detail = _error_status(exc)
                if (
                    endpoint.risk == "stateful-read"
                    and status == "REJECTED"
                    and "TIMEOUT" in detail.upper()
                ):
                    status = "STATE_UNAVAILABLE"
                    detail = f"{endpoint.note}: {detail}"
                self._add_result(endpoint, "READ", status, detail)
                if status == "TRANSPORT_LOST":
                    raise

    def write_same_values(self) -> None:
        # Ordinary filters/config first; high-impact routing/loop values last.
        writable = [
            endpoint for endpoint in self.endpoints
            if endpoint.setter is not None and self._key(endpoint) in self.values
        ]
        writable.sort(key=lambda endpoint: endpoint.risk == "high")
        for endpoint in writable:
            key = self._key(endpoint)
            original = copy.deepcopy(self.values[key])
            if endpoint.write_guard is not None:
                allowed, reason = endpoint.write_guard(original)
                if not allowed:
                    self._add_result(endpoint, "WRITE", "SKIP_STATE", reason)
                    continue
            projector = endpoint.projector or (lambda value: value)
            write_completed = False
            try:
                endpoint.setter(original)
                write_completed = True
                current = endpoint.getter() if endpoint.getter is not None else None
                if not _equivalent(projector(original), projector(current)):
                    raise AssertionError(
                        "readback mismatch: original="
                        f"{_json_value(projector(original))!r}, "
                        f"current={_json_value(projector(current))!r}"
                    )
                self._add_result(endpoint, "WRITE", "PASS", "same-value write/readback")
            except Exception as exc:
                status, detail = _error_status(exc)
                # A rejected same-value command has not changed the controller.
                # Multi-command setters can only have written a prefix, but
                # every prefix value is also the captured original value.
                if not write_completed:
                    self._add_result(endpoint, "WRITE", status, detail)
                    if status == "TRANSPORT_LOST":
                        raise
                    continue
                restore_detail = ""
                try:
                    endpoint.setter(original)
                    restored = endpoint.getter() if endpoint.getter is not None else None
                    if not _equivalent(projector(original), projector(restored)):
                        raise AssertionError(
                            f"restore mismatch: {_json_value(projector(restored))!r}"
                        )
                    restore_detail = "; original restored"
                except Exception as restore_exc:
                    restore_detail = (
                        "; RESTORE_FAILED "
                        f"{type(restore_exc).__name__}: {restore_exc}"
                    )
                    status = "RESTORE_FAILED"
                self._add_result(endpoint, "WRITE", status, detail + restore_detail)
                if status in {"TRANSPORT_LOST", "RESTORE_FAILED"}:
                    raise

    def summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for result in self.results:
            summary[result.status] = summary.get(result.status, 0) + 1
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--mode", choices=("read", "same-write"), default="read")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parent / "hardware_probe_results",
    )
    args = parser.parse_args(argv)

    session = open_serial(
        args.port, args.baudrate, readonly=args.mode == "read", timeout=3.0
    )
    probe = Probe(session, args.mode, args.output_dir, quiet=args.quiet)
    try:
        version = session.open()
        print(f"CONNECTED {version} readonly={session.readonly}", flush=True)
        constants = session.get_global_system_constants()
        print(f"CAPABILITIES {constants}", flush=True)
        probe.endpoints = build_endpoints(session, constants)
        probe.preflight()
        if args.mode == "same-write":
            probe.write_same_values()
    finally:
        session.close()
        probe.save()
        print(f"SUMMARY {json.dumps(probe.summary(), sort_keys=True)}", flush=True)

    bad_statuses = {"ERROR", "REJECTED", "TRANSPORT_LOST", "RESTORE_FAILED"}
    return 2 if any(result.status in bad_statuses for result in probe.results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
