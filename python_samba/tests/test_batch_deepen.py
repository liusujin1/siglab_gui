"""Tests for batch-deepened system/FF/diagnostics/position/analysis APIs."""

from __future__ import annotations

import pytest

from python_samba.protocol.commands import FilterStage
from python_samba.services.session import open_mock


def test_system_limits_and_constants():
    with open_mock(readonly=False) as s:
        assert s.get_ff_output_limit() == 100
        s.set_ff_output_limit(80)
        assert s.get_ff_output_limit() == 80
        lim = s.get_fb_limiter()
        assert len(lim) == 6
        s.set_fb_limiter([1, 2, 3, 4, 5, 6])
        assert s.get_fb_limiter()[0] == pytest.approx(1)
        assert s.get_global_system_constants()
        s.set_controller_type("5")
        assert s.get_controller_type() == ["5"]


def test_stage_ff_deep():
    with open_mock(readonly=False) as s:
        gains = s.get_ff_gains(0)
        assert len(gains) == 30
        s.set_ff_gains(0, 9, 8, 7)
        assert s.get_ff_gains(0)[:3] == pytest.approx([9, 8, 7])
        s.reset_ff_fir(0)
        assert all(v == 0.0 for v in s.get_ff_gains(0))
        s.set_ff_parameters(0, 0x30, True, 0.025)
        params = s.get_ff_parameters(0)
        assert params[:2] == ["30", "T"]
        assert float(params[2]) == pytest.approx(0.025)
        s.set_ff_config("6", "50")
        assert s.get_ff_config()[0] == "6"
        s.set_stage_ff_multipliers([2, 2, 2, 2])
        assert s.get_stage_ff_multipliers()[0] == pytest.approx(2)
        s.set_ff_adaptive_algo(1)
        assert s.get_ff_adaptive_algo() == 1
        s.set_ff_inputs("0", "1", "2")
        assert s.get_ff_inputs()[:3] == ["0", "1", "2"]


def test_diagnostics_deep_and_digital_trace():
    with open_mock(readonly=False) as s:
        s.set_excitation_params("2", "0.2", "25", "0", "0")
        assert s.get_excitation_params()[0] == "2"
        s.set_noise_frequency(33.5)
        assert s.get_noise_frequency() == pytest.approx(33.5)
        s.set_noise_filter_usage("N")
        assert s.get_noise_filter_usage() == "N"
        fs = s.get_noise_filter_stage(0)
        assert isinstance(fs, FilterStage)
        s.set_noise_filter_stage(FilterStage(0, 0, 2, (0.2, 0, 1, 0, 0)))
        assert s.get_noise_filter_stage(0).filter_type == 2
        s.set_diagnostic_outputs("1", "2", "3", "4")
        assert s.get_diagnostic_outputs()[0] == "1"
        s.set_digital_trace_info(0, 0, 0, 0, 32, 1)
        assert s.start_digital_trace()
        assert s.get_digital_trace_status()
        buf = s.get_digital_trace_buffer()
        assert len(buf) >= 1


def test_digital_trace_action_respects_readonly_gate():
    with open_mock(readonly=True) as s:
        with pytest.raises(PermissionError, match="readonly"):
            s.start_digital_trace()


def test_position_devices_and_setup_extras():
    with open_mock(readonly=False) as s:
        s.set_position_sensor_devices(*["1"] * 6)
        assert s.get_position_sensor_devices()[0] == "1"
        s.set_position_motor_devices(*["2"] * 8)
        assert s.get_position_motor_devices()[0] == "2"
        s.set_motor_offsets([0.1] * 11)
        assert s.get_motor_offsets()[0] == pytest.approx(0.1)
        s.set_sample_frequency(1500)
        assert s.get_sample_frequency() == pytest.approx(1500)
        s.set_controller_config("1", "0", "1", "1")
        assert s.get_controller_config()[1] == "0"
        s.set_adc_set_number(5)
        assert s.get_adc_set_number() == 5


def test_analysis_logging():
    with open_mock(readonly=False) as s:
        s.set_analysis_params("1", "2", "1")
        assert s.get_analysis_params() == ["1", "2", "1"]
        s.set_analysis_input("0", "1", "0", "0")
        assert s.get_analysis_input()[1] == "1"
        assert s.get_analysis_filter_outputs()
        assert s.get_analysis_events() is not None


def test_gui_deep_widgets(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    for name in (
        "ff_algo",
        "ff_gains",
        "noise_freq",
        "dig_trace_buf",
        "pos_sensor_dev",
        "nvram_fs",
        "analysis_params",
        "on_ff_reset",
        "on_diag_trace_start",
        "on_loop_limits_write",
    ):
        assert hasattr(win, name), name

    assert [win.noise_type.itemData(i) for i in range(win.noise_type.count())] == list(range(5))
    assert win.ff_filter.stage.maximum() == 7

    from python_samba.services.safety import SafetyGate

    session = open_mock(readonly=False)
    session.open()
    win.session = session
    win.gate = SafetyGate(session, snapshot_dir=tmp_path)
    win.on_diag_trace_start()
    win.on_diag_trace_status()
    win.on_diag_trace_read_buffer()
    assert win.dig_trace_buf.toPlainText().splitlines()[0] == "0"

    win.on_analysis_read()
    win.analysis_params.setText("4 5 1")
    win.analysis_input.setText("0 1 2 3")
    win._confirm_write = lambda _summary: True
    win.on_analysis_write()
    assert session.get_analysis_params() == ["4", "5", "1"]
    assert session.get_analysis_input() == ["0", "1", "2", "3"]
    win.close()
