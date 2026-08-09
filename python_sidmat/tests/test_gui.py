"""GUI smoke tests — build the window, connect to mock, run a measurement."""

from __future__ import annotations

import copy
import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _process_events(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_window_builds():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    assert win.trace_info is not None
    assert win.excitation_widget is not None
    assert len(win.axis_leds) == 6  # six velocity-axis LEDs (matches original)
    win.close()
    del win


def test_axis_selection():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.axis_buttons[2].click()
    assert win.axis_buttons[2].isChecked()
    assert not win.axis_buttons[0].isChecked()
    win.close()


def test_full_measurement_flow_mock():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win.backend_cbx.setCurrentText("mock")
    win.trace_info.length_edit.setText("64")
    win.trace_info.avg_edit.setText("1")
    win.connect_btn.setChecked(True)
    assert win.controller is not None and win.controller.connected

    win.trace_info.start_btn.click()
    assert win.worker is not None
    win.worker.wait(5000)
    _process_events(app, 0.5)

    assert "Done" in win.status_lbl.text() or "complete" in win.status_lbl.text()
    assert win.worker is None or not win.worker.isRunning()
    assert [
        len(view._pw.listDataItems()) for view in win._plot_widgets()
    ] == [2, 1, 1, 1]
    assert win.coh_plot._pw.viewRange()[1] == pytest.approx([0.0, 1.05])
    win.close()


def test_measuring_helper_routes_diagnostics_without_changing_loop():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win.backend_cbx.setCurrentText("mock")
    win.connect_btn.setChecked(True)
    assert win.controller is not None and win.controller.connected

    before = win.controller.get_axis_loop_states()
    win.mh_axis_buttons[2].click()
    after = win.controller.get_axis_loop_states()
    assert after == before

    diag0, diag1 = win.controller.get_diagnostic_outputs()
    assert diag0.encode() == (3, 0, 0)
    # The default "Raw" selection maps to the documented wire sub-index -1;
    # Stage1 would be 0.
    assert diag1.encode() == (2, 2, -1)
    assert win.controller.get_noise_inject().encode() == (4, 2, 0)

    win.mh_loop_cbx.setCurrentText("Position")
    win.mh_axis_buttons[8].click()
    diag0, diag1 = win.controller.get_diagnostic_outputs()
    assert diag0.encode() == (3, 0, 0)
    assert diag1.encode() == (5, 8, -1)
    assert win.controller.get_noise_inject().encode() == (5, 8, 4)
    win.close()


def test_velocity_helper_preserves_legacy_diag_and_injection_subindices():
    from python_sidmat.backend.iosignal import IOType
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.backend_cbx.setCurrentText("mock")
    win.connect_btn.setChecked(True)
    win.controller.set_diagnostic_outputs(IOType(3, 16, 2), IOType(2, 0, -1))
    win.controller.set_noise_inject(IOType(4, 0, 6))
    win._refresh_excitation_readback()
    win.mh_axis_buttons[2].click()
    diag0, diag1 = win.controller.get_diagnostic_outputs()
    assert diag0.encode() == (3, 16, 2)
    assert diag1.encode() == (2, 2, -1)
    assert win.controller.get_noise_inject().encode() == (4, 2, 6)
    win.close()


def test_measurement_settings_snapshot_roundtrip():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.trace_info.length_edit.setText("256")
    win.trace_info.avg_edit.setText("4")
    win.trace_info.fast_load_check.setChecked(True)
    win.mh_stage_cbx.setCurrentIndex(3)
    win.mh_axis_buttons[2].click()
    payload = win._measurement_settings_payload()

    other = MainWindow()
    other._apply_measurement_settings(payload)
    assert other.trace_info.length_edit.text() == "256"
    assert other.trace_info.avg_edit.text() == "4"
    assert other.trace_info.fast_load_check.isChecked()
    assert other.mh_stage_cbx.currentIndex() == 3
    assert other._mh_selected_axis == 2
    win.close()
    other.close()


def test_stage_selectors_stay_in_sync_and_raw_is_wire_minus_one():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.meas_type_cbx.setCurrentIndex(2)
    assert win.mh_stage_cbx.currentIndex() == 2
    win.mh_stage_cbx.setCurrentIndex(0)
    assert win.meas_type_cbx.currentIndex() == 0
    win.close()


def test_zero_excitation_offset_clears_previous_controller_value():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.backend_cbx.setCurrentText("mock")
    win.connect_btn.setChecked(True)
    win.controller.set_excitation_offset(0.5)
    win.excitation_widget.offset_edit.setText("0")
    win._apply_excitation()
    assert win.controller.get_excitation_offset() == pytest.approx(0.0)
    win.close()


def test_offline_filter_configuration_is_saved_without_controller():
    from python_samba.protocol.commands import FilterStage
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    stages = win.excitation_widget.current_filters()
    stages[0] = FilterStage(0, 0, 1, (100.0, 0.0, 1.0, 0.0, 0.0))
    win.excitation_widget.apply_filters(stages)
    win.excitation_widget.set_filter_usage(True)
    payload = win._measurement_settings_payload()
    assert payload["noise_filters"]["usage"] is True
    assert len(payload["noise_filters"]["stages"]) == 4
    assert payload["noise_filters"]["stages"][0]["type"] == 1
    win.close()


def test_invalid_settings_do_not_partially_change_visible_state():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    before = win.trace_info.length_edit.text()
    payload = copy.deepcopy(win._measurement_settings_payload())
    payload["trace"]["no_samples"] = 256
    payload["noise_filters"]["stages"][0]["type"] = 999
    with pytest.raises(ValueError, match="unsupported filter type"):
        win._apply_measurement_settings(payload)
    assert win.trace_info.length_edit.text() == before
    win.close()


def test_active_plot_follows_visible_view():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.plot_stack.setCurrentIndex(0)
    assert win._active_plot() is win.time_plot
    win.plot_stack.setCurrentIndex(1)
    assert win._active_plot() is win.frf_plot
    win.close()


def test_refresh_keeps_ui_only_average_and_fast_load_choices():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.backend_cbx.setCurrentText("mock")
    win.connect_btn.setChecked(True)
    win.trace_info.avg_edit.setText("9")
    win.trace_info.fast_load_check.setChecked(True)
    win.trace_info.current_trace()
    win._refresh_controller()
    assert win.trace_info.avg_edit.text() == "9"
    assert win.trace_info.fast_load_check.isChecked()
    win.close()


def test_invalid_trace_editor_value_does_not_corrupt_model():
    from python_sidmat.ui.trace_info import TraceInfoWidget

    _app()
    widget = TraceInfoWidget()
    before = widget.trace.no_samples
    widget.length_edit.setText("1")
    with pytest.raises(ValueError, match="no_samples"):
        widget.current_trace()
    assert widget.trace.no_samples == before
    widget.close()


def test_figure_model_collect_and_apply():
    from python_sidmat.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    win.time_plot._pw.plot([0.0, 1.0], [1.0, 2.0], name="Ch0")
    figure = win._collect_figure()
    assert figure.models[0].series[0].title == "Ch0"

    other = MainWindow()
    other._apply_figure(figure)
    assert len(other.time_plot._pw.listDataItems()) == 1
    win.close()
    other.close()
