"""Regression tests for paths restored from the decompiled SAMBA19xUI source."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from python_samba.protocol.commands import FilterStage
from python_samba.services.session import open_mock
from python_samba.services.config_reader import (
    SambaConfig,
    apply_config_to_session,
    capture_config_from_session,
    load_config,
    save_config,
)
from python_samba.ui.main_window import MainWindow
from python_samba.ui.patches import apply_all_patches


def test_pneumatic_partial_writes_preserve_companion_values() -> None:
    with open_mock(readonly=False) as session:
        session.set_pneumatic_config(10, 20, 30)
        session.set_pneumatic_config_softup_height(11)
        session.set_pneumatic_config_setpoint(22)
        session.set_pneumatic_position_tolerance(33)
        assert [float(value) for value in session.get_pneumatic_config()] == [
            11.0, 22.0, 33.0
        ]

        session.set_pneumatic_input_steering_matrix(0, [1, 2, 3, 4])
        session.set_pneumatic_output_steering_matrix(0, [5, 6, 7, 8])
        assert session.get_pneumatic_input_steering_matrix(0)[:4] == pytest.approx(
            [1, 2, 3, 4]
        )
        assert session.get_pneumatic_output_steering_matrix(0)[:4] == pytest.approx(
            [5, 6, 7, 8]
        )

        session.set_pneumatic_ramp_parameter("move_up_gradient", 9.5)
        ramp = [float(value) for value in session.get_pneumatic_ramp_parameters()]
        assert len(ramp) == 5
        assert ramp[2] == pytest.approx(9.5)


def test_safety_source_commands_round_trip() -> None:
    with open_mock(readonly=False) as session:
        expected = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        session.set_safety_and_earthquake_config(expected)
        assert session.get_safety_and_earthquake_config() == pytest.approx(expected)
        assert len(session.get_sensor_safety_rms_values()) == 12
        assert len(session.get_sensor_earthquake_rms_values()) == 12
        assert session.get_safety_geo_fault() == 0
        assert session.get_safety_prox_fault() == 0
        assert session.get_earthquake_geo_fault() == 0


def test_polynom_source_commands_are_supported_by_mock() -> None:
    with open_mock(readonly=False) as session:
        status = session.raw_command("LGPSP")
        session.encoder.ensure_ok(status, "LGPSP")
        config = session.raw_command("LGPCP", 0)
        session.encoder.ensure_ok(config, "LGPCP")
        assert len(config.data_tokens) == 13


def test_extended_loop_and_digio_words_do_not_reuse_velocity_word() -> None:
    with open_mock(readonly=False) as session:
        session.set_loop_status(0x15, 0x40)
        session.set_pos_pneum_individual_loop_status(0x2A, 0x05)
        position, pneumatic, digital_in, digital_out = (
            session.get_pos_pneum_digital_status()
        )
        assert (position, pneumatic) == (0x2A, 0x05)
        assert session.get_loop_status().individual == 0x15
        assert session.get_pneumatic_individual_loop_status() == [1, 0, 1]
        session.toggle_pneumatic_individual_loop_status(1, True)
        assert session.get_pos_pneum_digital_status()[:2] == (0x2A, 0x07)
        assert session.get_loop_status().individual == 0x15
        session.toggle_pneumatic_individual_loop_status(1, False)
        assert session.get_pos_pneum_digital_status()[:2] == (0x2A, 0x05)
        assert isinstance(digital_in, int)
        assert isinstance(digital_out, int)


def test_system_and_position_parameter_order_matches_source() -> None:
    with open_mock(readonly=False) as session:
        session.set_performance_monitor(1, 4, 0, 1200, 0.25, 2.5)
        performance = session.get_performance_monitor()
        assert [int(value) for value in performance[:4]] == [1, 4, 0, 1200]
        assert [float(value) for value in performance[4:]] == pytest.approx([0.25, 2.5])
        session.set_switch_conditions(70, 0.5, 15.0, 0x21)
        switch = session.get_switch_conditions()
        assert int(switch[0]) == 70
        assert [float(value) for value in switch[1:3]] == pytest.approx([0.5, 15.0])
        assert int(switch[3]) == 0x21

        # IIDETCMFD2: Mode, ResetPID, Deadband, RiseRange.
        session.set_non_linear_position_parameter(2, 1, 0.125, 3.5)
        nlp = session.get_non_linear_position_parameter()
        assert [int(value) for value in nlp[:2]] == [2, 1]
        assert [float(value) for value in nlp[2:]] == pytest.approx([0.125, 3.5])
        session.set_cascaded_position_parameter(0.75)
        assert float(session.get_cascaded_position_parameter()[1]) == pytest.approx(0.75)


def test_vendor_rci_extensions_do_not_reuse_unrelated_commands() -> None:
    with open_mock(readonly=False) as session:
        session.set_controller_type(7)
        session.set_motor_overcurrent_cooling_constant(0.0125)
        assert session.get_motor_overcurrent_cooling_constant() == pytest.approx(0.0125)
        assert [int(value) for value in session.get_controller_type()] == [7]

        session.transport.state.motor_failsafe[0] = "1"
        session.transport.state.amplifier_disable_events[0] = 0x1A0
        session.transport.state.amplifier_disable_events[1] = 0x100
        assert session.get_motor_failsafe_status()[0] == "1"
        assert session.get_amplifier_disable_events()[0] == 0x1A0
        assert session.get_amplifier_disable_events()[1] == 0x100

        built = session.build_nvram_checksums()
        assert len(built) == 3
        checked = session.check_nvram_checksums()
        assert len(checked) == 7
        assert checked[0] == 0


def test_vendor_mnemonics_round_trip_for_late_firmware_features() -> None:
    with open_mock(readonly=False) as session:
        session.set_actual_time(3, 14, 15, 16)
        assert [int(value) for value in session.get_actual_time()] == [3, 14, 15, 16]

        session.set_floor_ff_adaptive_algo(1)
        assert session.get_floor_ff_adaptive_algo() == 1

        session.set_cascaded_position_filter(1, 4, 1, 2, 3, 4, 5)
        cascaded = session.get_cascaded_position_filter(1)
        assert cascaded.filter_type == 4
        assert cascaded.params == pytest.approx([1, 2, 3, 4, 5])

        thresholds = [0.25 * (index + 1) for index in range(12)]
        session.set_zms_stability_thresholds(*thresholds)
        assert session.get_zms_stability_thresholds() == pytest.approx(thresholds)
        assert session.get_zms_last_failed_event() == pytest.approx((0, 0.0))
        status, rms = session.get_zms_stability_status_and_rms_values()
        assert status == (0, 0)
        assert len(rms) == 12

        session.set_power_supply_parameters(1500, 2.5, 0, 0)
        supply = session.get_power_supply_parameters()
        assert [float(value) for value in supply[:2]] == pytest.approx([1500, 2.5])


def test_setup_snapshot_xml_round_trip_restores_controller(tmp_path) -> None:
    path = tmp_path / "roundtrip.SAMBA19x_Config"
    expected_sensor = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    sensor_devices = [value for index in range(6) for value in (0, index, -1)]
    motor_devices = [value for index in range(8) for value in (1, index, 0)]
    with open_mock(readonly=False) as session:
        session.set_velocity_sensor_matrix(2, expected_sensor)
        session.set_non_linear_position_parameter(3, 1, 0.2, 4.0)
        session.set_excitation_params(2, 0.25, 12.5, 3.0, 4.0)
        session.set_diagnostic_outputs(2, 1, 3, 5, 4, 2)
        session.set_noise_inject_point(5, 2, 1)
        session.set_noise_filter_stage(
            FilterStage(0, 2, 4, (1.0, 2.0, 3.0, 4.0, 5.0))
        )
        session.set_digital_trace_info(2, 0, 3, 5, 1, 2, 4, 768, 0)
        session.set_position_sensor_devices_for_axis(2, sensor_devices)
        session.set_position_motor_devices_for_axis(2, motor_devices)
        session.set_pneumatic_steering_matrix(
            1, [0.1 * (index + 1) for index in range(16)]
        )
        session.set_pneumatic_valve_offsets(
            [10.0 + index for index in range(16)]
        )
        session.set_dither_value(17.5)
        session.set_dither_frequency(43)
        session.set_dither_alpha(0.0125)
        session.set_pneumatic_ramp_parameters(1, 1.25, 2.5, 3.75, 4.5)
        session.set_linear_motor_offsets([0.125 * (index + 1) for index in range(12)])
        session.set_temp_sensor_adc_mapping([11 - index for index in range(12)])
        session.set_power_supply_parameters(1450.0, 2.75, 0, 0)
        session.set_ff_config(6, "N")
        session.set_ff_output_limit(73)
        session.set_event_trace_params(2, 512, 4, 3, 8, 1)
        session.set_event_signal(5, 2, 1, 0.75, 12)
        session.set_monitor_signal(7, 2, 4, 3)
        config = capture_config_from_session(session)
        assert config.capture_warnings == []
        assert "PneumIO#8#8" in config.system_configuration
        save_config(path, config)

        loaded = load_config(path)
        assert loaded.excitation_type == 2
        assert loaded.excitation_params == pytest.approx([0.25, 12.5, 3.0, 4.0])
        assert (
            loaded.diag_io_signal_0.type,
            loaded.diag_io_signal_0.main_index,
            loaded.diag_io_signal_0.sub_index,
        ) == (2, 1, 3)
        assert (
            loaded.trace_io_1.type,
            loaded.trace_io_1.main_index,
            loaded.trace_io_1.sub_index,
        ) == (5, 1, 2)
        assert loaded.trace_undersample == 4
        assert loaded.trace_no_samples == 768
        assert loaded.trace_filter_flag == 0
        assert len(loaded.pos_sensor_devices["Xtrans"]) == 6
        assert len(loaded.pos_motor_devices["Xtrans"]) == 8
        assert loaded.dither_value == pytest.approx(17.5)
        assert loaded.dither_frequency == 43
        assert loaded.dither_compensation == pytest.approx(0.0125)
        assert loaded.pneum_sensor_matrix["Yrpneu"] == pytest.approx(
            [0.1 * (index + 1) for index in range(8)]
        )
        assert loaded.pneum_motor_matrix["Yrpneu"] == pytest.approx(
            [0.1 * (index + 9) for index in range(8)]
        )
        assert loaded.pneum_up_valve_offsets == pytest.approx(
            [10.0 + index for index in range(8)]
        )
        assert loaded.pneum_down_valve_offsets == pytest.approx(
            [18.0 + index for index in range(8)]
        )
        assert loaded.pneum_ramp_switch_to_up == 1
        assert loaded.pneum_ramp_setpoint_gradient == pytest.approx(1.25)
        assert loaded.linear_motor_offsets[11] == pytest.approx(1.5)
        assert loaded.temp_sensor_adc_mapping == list(reversed(range(12)))
        assert loaded.power_supply_current_limit == pytest.approx(1450.0)
        assert loaded.power_supply_current_si_unit == pytest.approx(2.75)
        assert loaded.ff_no_gains == 6
        assert loaded.ff_output_threshold == 73
        assert loaded.event_logging_type == 2
        assert loaded.event_samples_num == 512
        assert loaded.event_io_signal.main_index == 2
        assert loaded.event_monitor_signals[7].sub_index == 3

        session.set_velocity_sensor_matrix(2, [0.0] * 7)
        session.set_non_linear_position_parameter(0, 0, 0.0, 0.0)
        session.set_excitation_params(0, 0, 0, 0, 0)
        session.set_diagnostic_outputs(0, 0, 0, 0, 0, 0)
        session.set_noise_inject_point(0, 0, 0)
        session.set_noise_filter_stage(FilterStage(0, 2, 0, (0.0,) * 5))
        session.set_digital_trace_info(0, 0, 0, 0, 0, 0, 1, 1, 1)
        session.set_position_sensor_devices_for_axis(2, [0] * 18)
        session.set_position_motor_devices_for_axis(2, [0] * 24)
        session.set_pneumatic_steering_matrix(1, [0.0] * 16)
        session.set_pneumatic_valve_offsets([0.0] * 16)
        session.set_dither_value(0)
        session.set_dither_frequency(0)
        session.set_dither_alpha(0)
        session.set_pneumatic_ramp_parameters(0, 0, 0, 0, 0)
        session.set_linear_motor_offsets([0.0] * 12)
        session.set_temp_sensor_adc_mapping([0] * 12)
        session.set_power_supply_parameters(0, 0, 0, 0)
        session.set_ff_config(1, "Y")
        session.set_ff_output_limit(1)
        session.set_event_trace_params(0, 1, 1, 1, 1, 0)
        session.set_event_signal(0, 0, 0, 0, 1)
        session.set_monitor_signal(7, 0, 0, 0)
        assert apply_config_to_session(loaded, session) == []
        assert session.get_velocity_sensor_matrix(2) == pytest.approx(expected_sensor)
        nlp = session.get_non_linear_position_parameter()
        assert [int(value) for value in nlp[:2]] == [3, 1]
        assert [float(value) for value in nlp[2:]] == pytest.approx([0.2, 4.0])
        assert [float(value) for value in session.get_excitation_params()] == pytest.approx(
            [2, 0.25, 12.5, 3.0, 4.0]
        )
        assert session.get_diagnostic_outputs() == ["2", "1", "3", "5", "4", "2"]
        assert session.get_noise_inject_point() == ["5", "2", "1"]
        assert session.get_noise_filter_stage(2).filter_type == 4
        assert session.get_digital_trace_info() == [
            "2", "0", "3", "5", "1", "2", "4", "768", "0"
        ]
        assert [int(value) for value in session.get_position_sensor_devices_for_axis(2)] == sensor_devices
        assert [int(value) for value in session.get_position_motor_devices_for_axis(2)] == motor_devices
        assert session.get_dither_value() == pytest.approx(17.5)
        assert session.get_dither_frequency() == pytest.approx(43)
        assert session.get_dither_alpha() == pytest.approx(0.0125)
        assert session.get_pneumatic_steering_matrix(1) == pytest.approx(
            [0.1 * (index + 1) for index in range(16)]
        )
        assert session.get_pneumatic_valve_offsets() == pytest.approx(
            [10.0 + index for index in range(16)]
        )
        assert [float(value) for value in session.get_pneumatic_ramp_parameters()] == pytest.approx(
            [1, 1.25, 2.5, 3.75, 4.5]
        )
        assert session.get_linear_motor_offsets() == pytest.approx(
            [0.125 * (index + 1) for index in range(12)]
        )
        assert session.get_temp_sensor_adc_mapping() == list(reversed(range(12)))
        assert [float(value) for value in session.get_power_supply_parameters()[:2]] == pytest.approx(
            [1450.0, 2.75]
        )
        # UseFBSignals is derived from the saved 0x1000 loop-status bit, just
        # as TCMFDRCI.SetFFConfig does; it is not an XML field of its own.
        assert session.get_ff_config() == ["6", "Y"]
        assert session.get_ff_output_limit() == 73
        assert [int(value) for value in session.get_event_trace_params()] == [
            2, 512, 4, 3, 8, 1
        ]
        assert [float(value) for value in session.get_event_signal()] == pytest.approx(
            [5, 2, 1, 0.75, 12]
        )
        assert [int(value) for value in session.get_monitor_signal(7)] == [2, 4, 3]


def test_setup_apply_preserves_same_trace_and_disabled_event_sentinel() -> None:
    with open_mock(readonly=False) as session:
        session.set_event_trace_params(0, 0, 0, 1, 5000, 0)
        config = capture_config_from_session(session)
        assert config.capture_warnings == []
        original_trace = session.get_digital_trace_info()
        original_event = session.get_event_trace_params()
        trace_writes = []
        real_set_trace = session.set_digital_trace_info

        def record_trace_write(*values) -> None:
            trace_writes.append(list(values))
            real_set_trace(*values)

        session.set_digital_trace_info = record_trace_write
        assert apply_config_to_session(config, session) == []
        assert trace_writes == []
        assert session.get_digital_trace_info() == original_trace
        assert session.get_event_trace_params() == original_event


def test_setup_writer_matches_vendor_v8_schema_and_lexical_format(tmp_path) -> None:
    path = tmp_path / "vendor-layout.SAMBA19x_Config"
    cfg = SambaConfig(
        firmware_version="3 3 122 103 9 FWCompiler: 7004021",
        system_configuration="0 6 3 6 7 6 7 4 8 3 5000 NAF",
    )
    cfg.motor_offset = [0.25 * (index + 1) for index in range(11)]

    save_config(path, cfg)
    payload = path.read_bytes()
    assert payload.startswith(
        b'<?xml version="1.0"?>\r\n'
        b'<!--This document was generated withpython_samba, Version='
    )
    assert payload.endswith(b"</SAMBA1_9_X_Configuration>")
    assert payload.count(b"\r\n") == 1039
    assert b"0.000000E+00" in payload
    assert b"encoding='utf-8'" not in payload

    root = ET.fromstring(payload)
    assert sum(1 for _ in root.iter()) == 901
    assert root.findtext("XML_File_Version") == "8"
    assert root.findtext("SystemConfiguration") == cfg.system_configuration

    position = root.find("PositionLoopSettings")
    assert position is not None
    position_filters = position.find("FilterSetting")
    assert position_filters is not None
    assert [node.tag for node in position_filters] == [
        "Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
        "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
    ]
    assert all(len(list(axis)) == 12 for axis in position_filters)
    used_inputs = position.find("SensorMatrixUsedInput")
    used_outputs = position.find("MotorMatrixUsedOutput")
    assert used_inputs is not None and used_outputs is not None
    assert all(len(list(axis)) == 6 for axis in used_inputs)
    assert all(len(list(axis)) == 8 for axis in used_outputs)

    motor_offset = root.find(
        "PneumaticLoopSettings/MotorAndIsolatorOffset/MotorOffset"
    )
    isolator_offset = root.find(
        "PneumaticLoopSettings/MotorAndIsolatorOffset/IsolatorOffset"
    )
    assert motor_offset is not None and isolator_offset is not None
    assert motor_offset.attrib == {}
    assert isolator_offset.attrib == {}
    assert motor_offset.findtext("OutY1") == "2.500000E-01"
    assert isolator_offset.findtext("Iso3") == "2.750000E+00"

    adc = root.find("AD-DA-Mapping/ADC-Mapping")
    dac = root.find("AD-DA-Mapping/DAC-Mapping")
    assert adc is not None and dac is not None
    assert adc.find("InpProx4") is not None
    assert adc.find("InpProxH4") is not None
    assert adc.find("Prox4") is None
    assert dac.find("OutV1") is not None
    assert dac.find("OutHV3") is not None
    assert dac.find("Valve1") is None

    ff = root.find("Feed-Forward-Setting")
    pff = root.find("Pneum-Feed-Forward-Setting")
    assert ff is not None and pff is not None
    ff_ref = ff.find("FFRefFilter")
    ff_gains = ff.find("FFGains")
    pff_ref = pff.find("PFFRefFilter")
    pff_gains = pff.find("PFFGains")
    assert ff_ref is not None and ff_gains is not None
    assert pff_ref is not None and pff_gains is not None
    assert len(list(ff_ref)) == 7
    assert len(list(ff_gains)) == 7
    assert len(list(pff_ref)) == 4
    assert len(list(pff_gains)) == 4

    loaded = load_config(path)
    assert loaded.motor_offset == pytest.approx(cfg.motor_offset)
    assert len(loaded.pos_filters["Ztrans2"]) == 12


def test_effective_ui_handlers_are_functional_implementations() -> None:
    report = apply_all_patches(MainWindow, strict=True)
    assert report.ok
    expected_modules = {
        "on_poly_read": "polynom_patch",
        "on_poly_write": "polynom_patch",
        "on_safety_read_config": "safety_zms_patch",
        "on_safety_write_config": "safety_zms_patch",
        "on_zms_write": "safety_zms_patch",
        "on_sig_save_settings": "signal_progress_patch",
        "on_sig_load_settings": "signal_progress_patch",
        "_on_pff_gain_changed": "pffconfig_posfilter_saveload_patch",
        "on_cascaded_parameter_changed": "pffconfig_posfilter_saveload_patch",
        "_on_digio_read": "unified_special_tab",
    }
    for name, module in expected_modules.items():
        assert getattr(MainWindow, name).__module__ == module
