from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time

import numpy as np
import pytest

from python_vna.diagnostic.modal_identification import (
    FRFMeasurement, ModalCancelled, ModalSettings, fit_selected_poles, identify_modes,
    modal_mac, prepare_modal_input,
)


def measurements(modes=((10.0, .02, 12.0),), *, unit="m/s^2", noise=0.0):
    f = np.linspace(.5, 40, 1200)
    w = 2 * np.pi * f
    shapes = np.array([[1., .5, -.3], [1., -.7, .8], [1., .9, .2]])
    h = np.zeros((f.size, 3), dtype=complex)
    for index, (frequency, damping, mass) in enumerate(modes):
        wn = 2 * np.pi * frequency
        h += shapes[index][None, :] / mass / (wn ** 2 - w[:, None] ** 2 + 2j * damping * wn * w[:, None])
    h *= -w[:, None] ** 2
    if noise:
        rng = np.random.default_rng(422)
        h += noise * np.max(abs(h), axis=0) * (rng.normal(size=h.shape) + 1j * rng.normal(size=h.shape))
    if unit == "g":
        h /= 9.80665
    return [FRFMeasurement(f"P{j}-X", "measured.vna", "force", str(j), f"P{j}", 0, (j, 0, 0), f.copy(), h[:, j].copy(), unit)
            for j in range(3)]


@pytest.mark.parametrize("modes", [((10., .02, 12.),), ((10., .02, 12.), (24., .035, 30.)), ((10., .02, 12.), (10.8, .025, 30.))])
def test_exact_modal_frequency_damping_mass_and_synthesis(modes):
    data = prepare_modal_input(measurements(modes), 0, ModalSettings(maximum_order=14))
    result = identify_modes(data)
    assert len(result["modes"]) == len(modes)
    for actual, (frequency, damping, mass) in zip(result["modes"], modes):
        assert actual["frequency_hz"] == pytest.approx(frequency, rel=.002)
        assert actual["damping_ratio"] == pytest.approx(damping, rel=.05)
        assert actual["mass_kg"] == pytest.approx(mass, rel=.02)
        assert actual["shape"][0] == pytest.approx(1)
        assert actual["coherence"] is None
        assert actual["stiffness_n_m"] == pytest.approx(mass * (2 * np.pi * frequency) ** 2, rel=.02)
    assert max(result["channel_errors"]) < 1e-6
    assert any(p["stable_count"] >= 3 for p in result["poles"])
    assert not data.receptance.flags.writeable


def test_unit_factors_fingerprint_and_input_snapshot():
    raw = measurements()
    si = prepare_modal_input(raw, 0, ModalSettings(maximum_order=10))
    g = prepare_modal_input(measurements(unit="g"), 0, ModalSettings(maximum_order=10))
    np.testing.assert_allclose(si.receptance, g.receptance)
    raw[0].values[:] = 0
    assert np.linalg.norm(si.receptance[:, 0]) > 0
    scaled = prepare_modal_input([replace(m, force_factor=2) for m in measurements()], 0, ModalSettings(maximum_order=10))
    result = identify_modes(scaled)
    assert result["modes"][0]["mass_kg"] == pytest.approx(24, rel=.02)
    assert scaled.fingerprint != si.fingerprint
    assert modal_mac(np.array([1, 2j]), np.array([2, 4j])) == pytest.approx(1)


def test_noise_different_grid_missing_axes_and_cancellation():
    raw = measurements(noise=.0002)[:2]
    raw[1] = replace(raw[1], frequency_hz=raw[1].frequency_hz[::2], values=raw[1].values[::2])
    data = prepare_modal_input(raw, 0, ModalSettings(maximum_order=12))
    assert data.receptance.shape[1] == 2
    assert "网格不同" in data.warnings[0]
    result = identify_modes(data)
    nearby = min(result["modes"], key=lambda mode: abs(mode["frequency_hz"] - 10))
    assert nearby["frequency_hz"] == pytest.approx(10, rel=.01)
    assert nearby["mass_kg"] == pytest.approx(12, rel=.05)
    with pytest.raises(ModalCancelled):
        identify_modes(data, cancel=lambda: True)


@pytest.mark.parametrize("change,message", [
    (lambda m: replace(m, values=m.values.real), "复数"),
    (lambda m: replace(m, response_unit="V"), "单位"),
    (lambda m: replace(m, force_unit="V"), "力单位"),
    (lambda m: replace(m, force_factor=0), "力系数"),
    (lambda m: replace(m, frequency_hz=m.frequency_hz[::-1]), "递增"),
])
def test_invalid_physical_inputs_rejected(change, message):
    raw = measurements()
    raw[0] = change(raw[0])
    with pytest.raises(ValueError, match=message):
        prepare_modal_input(raw, 0, ModalSettings())


def test_nonoverlap_reverse_band_and_duplicate_dof_rejected():
    raw = measurements()
    with pytest.raises(ValueError, match="下限"):
        prepare_modal_input(raw, 0, ModalSettings(minimum_hz=20, maximum_hz=10))
    with pytest.raises(ValueError, match="共同"):
        prepare_modal_input(raw, 0, ModalSettings(minimum_hz=50))
    with pytest.raises(ValueError, match="重复"):
        prepare_modal_input([raw[0], raw[0]], 0, ModalSettings())


def test_bad_polarity_complex_mode_node_and_band_edge_suppress_mass():
    raw = measurements()
    poles = [{"frequency_hz": 10., "damping_ratio": .02, "order": 6}]
    for modified, phrase in (
        ([replace(m, values=-m.values) for m in raw], "非正"),
        ([replace(m, values=m.values * (1 + 1j)) for m in raw], "复模态"),
        ([replace(raw[0], values=raw[0].values * 1e-6)] + raw[1:], "节点"),
    ):
        result = fit_selected_poles(prepare_modal_input(modified, 0, ModalSettings()), poles)
        assert result["modes"][0]["mass_kg"] is None
        assert phrase in result["modes"][0]["status"]
    result = fit_selected_poles(prepare_modal_input(raw, 0, ModalSettings(minimum_hz=9.8)), poles)
    assert result["modes"][0]["mass_kg"] is None
    assert "带宽" in result["modes"][0]["status"]


@pytest.fixture
def modal_page():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from python_vna.analysis_data import AnalysisDataset, AnalysisSeries
    from python_vna.diagnostic.pages import ModalFile, ModalShapePage
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = ModalShapePage()
    raw = measurements()
    dataset = AnalysisDataset(1, Path("measured.vna"), "measured.vna", 1000.,
                              series=[AnalysisSeries(1, 0, "force", "Force", "N")] +
                              [AnalysisSeries(1, j + 1, str(j), f"Response {j}", "m/s^2") for j in range(3)],
                              frequency_hz=raw[0].frequency_hz,
                              frf={f"force->{j}": m.values for j, m in enumerate(raw)})
    page.files = [ModalFile(dataset.path, dataset)]
    page._bulk_table_update = True
    for j in range(3):
        page._insert_point_row([True, f"P{j}", "measured.vna", j + 2, 0, 0, 1, 1, 1, j, 0, 0])
    page._bulk_table_update = False
    page._refresh_identification_sources()
    page.modal_input_table.cellWidget(0, 1).setCurrentIndex(1)
    page.modal_input_table.cellWidget(0, 2).setCurrentText("N")
    page.modal_driver_combo.setCurrentText("P0")
    page.modal_force_confirm.setChecked(True)
    page.modal_driver_confirm.setChecked(True)
    page.modal_max_order.setValue(10)
    yield page, app
    page._shutdown_identification()
    page._preview_timer.stop()
    page.close()
    app.processEvents()


def wait_for_task(page, app):
    deadline = time.monotonic() + 30
    while page._identification_task is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert page._identification_task is None


def test_ui_async_result_preview_stale_export_and_display_only_changes(modal_page):
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    assert page._identification_result is not None, page.modal_identification_status.text()
    assert not page._identification_stale
    assert page.modal_result_table.rowCount() == 1
    original = page._identification_result["modes"][0]["mass_kg"]
    page.preview_mode()
    assert page.last_mode["identified"]
    assert page.preview_tabs.currentWidget() is page.mode_preview_tab
    page.mode_gain_spin.setValue(5)
    page.view_azimuth_spin.setValue(45)
    assert not page._identification_stale
    assert page._identification_result["modes"][0]["mass_kg"] == original
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "results.csv"
        page.export_identification_results(path)
        assert "driving_point_unity" in path.read_text(encoding="utf-8-sig")
        page.auto_build_lines()
        gif = page.export_mode_gif(Path(folder) / "identified.gif")
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(gif))
        assert reader.imageCount() == 24
        first = reader.read()
        assert not first.isNull()
        assert reader.read() != first
        del reader
        page.modal_frequency_min.setValue(2)
        page.modal_frequency_min.editingFinished.emit()
        assert page._identification_stale
        with pytest.raises(ValueError, match="待更新"):
            page.export_identification_results(path)
        with pytest.raises(ValueError, match="待更新"):
            page.export_mode_gif(Path(folder) / "stale.gif")


def test_ui_session_roundtrip_cancel_and_partial_mapping(modal_page):
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    result = page._identification_result
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "session.vnamodal"
        page.export_session(path)
        page.import_session(path)
        assert not page._identification_stale
        assert page._identification_result["modes"][0]["mass_kg"] == result["modes"][0]["mass_kg"]
    page.start_modal_identification()
    page.cancel_modal_identification()
    wait_for_task(page, app)
    assert page._identification_result["fingerprint"] == result["fingerprint"]
    page.point_table.item(0, 6).setText("-1")
    assert page._identification_stale


def test_ui_old_session_keeps_legacy_preview(modal_page):
    from python_vna.diagnostic.modal_session import load_modal_session, save_modal_session
    page, app = modal_page
    page.extract_mode()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.vnamodal"
        page.export_session(path)
        state = load_modal_session(path)
        state.pop("identification", None)
        save_modal_session(path, state)
        page.import_session(path)
        assert page._identification_result is None
        assert page.last_mode is not None


def test_ui_legacy_endpoint_aliases_apply_engineering_ratio_once(modal_page):
    page, _app = modal_page
    dataset = page.files[0].dataset
    dataset.frf = {f"ai0->ai{j + 1}": values / 2 for j, values in enumerate(dataset.frf.values())}
    dataset.metadata["legacy_channels"] = {
        f"ai{i}": {"label": s.display_name, "eu_string": s.unit, "euscale_fac": 2 if i == 0 else 4}
        for i, s in enumerate(dataset.series)}
    for i, series in enumerate(dataset.series):
        series.channel_key = f"display-{i}"
        series.scale = 2 if i == 0 else 4
    page._refresh_identification_sources()
    page.modal_input_table.cellWidget(0, 1).setCurrentIndex(1)
    data, skipped = page._modal_input_snapshot()
    assert not skipped
    assert data.measurements[0].response_key == "ai1"
    result = identify_modes(data)
    assert result["modes"][0]["mass_kg"] == pytest.approx(12, rel=.02)


def test_inflight_data_mutation_is_discarded_and_display_changes_keep_results(modal_page):
    page, _app = modal_page
    data, _ = page._modal_input_snapshot()
    result = identify_modes(data)
    page._identification_run_generation = page._identification_generation
    page._identification_run_poles = None
    page._identification_skipped = []
    page.files[0].dataset.frf["force->0"] *= 2
    page._accept_modal_identification(result)
    assert page._identification_result is None
    assert "未应用" in page.modal_identification_status.text()


def test_identified_animation_lines_do_not_invalidate_fit(modal_page):
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    mass = page._identification_result["modes"][0]["mass_kg"]
    page.auto_build_lines()
    page._add_line_row()
    page._delete_line_rows()
    assert not page._identification_stale
    assert page.last_mode["identified"]
    assert page._identification_result["modes"][0]["mass_kg"] == mass
    page.apply_frequency()
    assert not page._identification_stale


def test_invalid_identification_session_is_rejected_before_replacing_data(modal_page):
    from python_vna.diagnostic.modal_session import load_modal_session, save_modal_session
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "invalid.vnamodal"
        page.export_session(path)
        state = load_modal_session(path)
        state["identification"]["result"]["modes"][0]["shape"] = np.zeros(9)
        save_modal_session(path, state)
        original = page.files[0].dataset
        with pytest.raises(ValueError, match="identification"):
            page.import_session(path)
        assert page.files[0].dataset is original
        state["identification"]["result"]["modes"][0]["shape"] = np.ones(3, dtype=complex)
        state["identification"]["result"]["settings"]["frequency_tolerance"] = "invalid"
        save_modal_session(path, state)
        with pytest.raises(ValueError, match="identification"):
            page.import_session(path)
        assert page.files[0].dataset is original


def test_identification_settings_fit_control_panel_and_invalid_mass_is_visible(modal_page):
    from PySide6.QtCore import QPoint
    from python_vna.ui.diagnostic_theme import build_diagnostic_stylesheet, default_diagnostic_theme
    page, app = modal_page
    page.setStyleSheet(build_diagnostic_stylesheet(default_diagnostic_theme()))
    page.resize(960, 680)
    page.show()
    page.left_work_tabs.setCurrentIndex(3)
    page.modal_limit_frequency.setChecked(True)
    app.processEvents()
    scroll = page.modal_settings_scroll
    for widget in (page.modal_driver_combo, page.modal_direction_combo, page.modal_frequency_min,
                   page.modal_frequency_max):
        assert widget.mapTo(scroll.widget(), QPoint(0, 0)).x() + widget.width() <= scroll.viewport().width()
    for button in (page.identify_button, page.modal_cancel_button):
        assert button.isVisible()
        assert button.geometry().right() < button.parentWidget().width()
    for key in page.files[0].dataset.frf:
        page.files[0].dataset.frf[key] *= -1
    page.start_modal_identification()
    wait_for_task(page, app)
    assert page._identification_result["modes"][0]["mass_kg"] is None
    assert page.modal_result_table.item(0, 3).text() == "不可可靠估计"


@pytest.mark.parametrize("window_size", [(960, 600), (1200, 720), (1366, 768)])
def test_modal_layout_expanded_controls_do_not_overlap_in_shell(modal_page, window_size):
    from itertools import combinations
    from PySide6 import QtCore, QtGui, QtWidgets
    from python_vna.diagnostic.shell import DiagnosticMainWindow

    page, app = modal_page
    # The offscreen Windows plugin has no system fonts unless loaded explicitly.
    font_ids = [QtGui.QFontDatabase.addApplicationFont(str(path)) for path in (
        Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/msyh.ttc")) if path.exists()]
    old_font = app.font()
    app.setFont(QtGui.QFont("Segoe UI", 9))
    window = DiagnosticMainWindow()
    window.stack.removeWidget(window.modal_page)
    page.setParent(None)
    window.stack.insertWidget(2, page)
    window.nav_list.setCurrentRow(2)
    window.resize(*window_size)
    window.show()
    try:
        page.left_work_tabs.setCurrentIndex(3)
        app.processEvents()
        app.processEvents()
        if window_size[0] >= 1200:
            viewport = page.modal_frequency_scroll.viewport()
            for button in (page.apply_freq_button, page.find_peaks_button, page.extract_button,
                           page.preview_button, page.export_gif_button, page.delete_peak_button,
                           page.view_reset_button):
                assert viewport.rect().contains(QtCore.QRect(button.mapTo(viewport, QtCore.QPoint()), button.size()))
        page.modal_sources_toggle.setChecked(True)
        page.modal_advanced_toggle.setChecked(True)
        page.modal_limit_frequency.setChecked(True)
        page.modal_preview_settings_toggle.setChecked(True)
        for group in page.findChildren(QtWidgets.QGroupBox):
            if group.title() in {"高级拟合参数", "预览设置", "候选搜索参数"}:
                group.setChecked(True)
        details = next(b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == "诊断详情")
        details.setChecked(True)
        page.modal_identification_status.setText("校验失败，请检查驱动点及输入通道。" * 30)
        page.modal_progress.show()
        app.processEvents()
        app.processEvents()

        def bounds(widget, parent):
            return QtCore.QRect(widget.mapTo(parent, QtCore.QPoint()), widget.size())

        host = page.identify_button.parentWidget()
        footer = [page.modal_settings_scroll, page.identify_button, page.modal_cancel_button,
                  page.modal_progress, page.modal_identification_status, details]
        for widget in footer:
            assert host.rect().contains(bounds(widget, host))
        for first, second in combinations(footer, 2):
            assert not bounds(first, host).intersects(bounds(second, host))
        assert page.modal_identification_status.toolTip() == page.modal_identification_status.text()
        assert page.modal_settings_scroll.verticalScrollBar().maximum() > 0
        assert page.modal_settings_scroll.widget().height() >= page.modal_settings_scroll.widget().minimumSizeHint().height()
        assert page.modal_frequency_scroll.widget().height() >= page.modal_frequency_scroll.widget().minimumSizeHint().height()
        for tab in (0, 1):
            page.modal_result_tabs.setCurrentIndex(tab)
            app.processEvents()
            visible_table = page.candidate_list if tab == 0 else page.modal_result_table
            assert page.rect().contains(bounds(visible_table, page))
            assert visible_table.height() >= visible_table.minimumHeight()
            assert bounds(page.modal_frequency_scroll, page).bottom() < bounds(page.modal_result_tabs, page).top()
        for tab in (0, 1, 2):
            page.modal_plot_tabs.setCurrentIndex(tab)
            app.processEvents()
            assert page.rect().contains(bounds(page.modal_plot_tabs, page))
            assert page.rect().contains(bounds(page.preview_tabs, page))
            assert bounds(page.modal_plot_tabs, page).bottom() < bounds(page.preview_tabs, page).top()
        factor = page.modal_input_table.cellWidget(0, 3)
        assert factor.cleanText() == "1"
        factor.setValue(.000001)
        assert abs(factor.valueFromText(factor.text()) - .000001) < 1e-12
        assert factor.height() <= page.modal_input_table.rowHeight(0)
    finally:
        page.setParent(None)
        window.close()
        app.setFont(old_font)
        for font_id in font_ids:
            if font_id >= 0:
                QtGui.QFontDatabase.removeApplicationFont(font_id)


def test_low_coherence_and_high_error_withhold_mass():
    raw = [replace(m, coherence=np.full(m.frequency_hz.size, .5)) for m in measurements()]
    poles = [{"frequency_hz": 10., "damping_ratio": .02, "order": 6}]
    result = fit_selected_poles(prepare_modal_input(raw, 0, ModalSettings()), poles)
    assert result["modes"][0]["mass_kg"] is None
    assert "相干性" in result["modes"][0]["status"]
    raw = measurements(((10., .02, 12.), (24., .03, 20.)))
    result = fit_selected_poles(prepare_modal_input(raw, 0, ModalSettings()), poles)
    assert result["modes"][0]["mass_kg"] is None
    assert "误差" in result["modes"][0]["status"]


def test_compact_defaults_use_cmif_and_hide_optional_configuration():
    from python_vna.diagnostic.pages import ModalShapePage
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = ModalShapePage()
    try:
        assert page.modal_candidate_method.currentData() == "cmif"
        assert "CMIF" in page.modal_algorithm_summary.text()
        assert page.modal_sources_panel.isHidden()
        assert page.modal_advanced_panel.isHidden()
        assert page.modal_range_panel.isHidden()
        assert not page.modal_physical_confirm.isChecked()
        assert page.modal_input_table.isColumnHidden(3)
        assert page.modal_input_table.isColumnHidden(5)
    finally:
        page.close()
        app.processEvents()


def test_unique_force_and_declared_unit_are_inferred_but_driver_is_not(modal_page):
    page, app = modal_page
    page.modal_input_table.setRowCount(0)
    page._identification_file_options.clear()
    page.modal_driver_combo.setCurrentIndex(0)
    page.modal_physical_confirm.setChecked(False)
    page._refresh_identification_sources()
    assert page.modal_input_table.cellWidget(0, 1).currentData() == "force"
    assert page.modal_input_table.cellWidget(0, 2).currentText() == "N"
    assert page.modal_driver_combo.currentIndex() == 0
    assert not page.modal_force_confirm.isChecked()
    assert not page.modal_driver_confirm.isChecked()
    assert "1/1" in page.modal_source_summary.text()


def test_ambiguous_force_and_unknown_units_are_not_guessed(modal_page):
    page, app = modal_page
    dataset = page.files[0].dataset
    dataset.frf["other->0"] = dataset.frf["force->0"].copy()
    page.modal_input_table.setRowCount(0)
    page._identification_file_options.clear()
    page._refresh_identification_sources()
    assert page.modal_input_table.cellWidget(0, 1).currentData() == ""
    del dataset.frf["other->0"]
    dataset.series[0].unit = "V"
    page.modal_input_table.setRowCount(0)
    page._identification_file_options.clear()
    page._refresh_identification_sources()
    assert page.modal_input_table.cellWidget(0, 2).currentText() == "请选择"
    with pytest.raises(ValueError, match="力单位"):
        page._modal_input_snapshot()


def test_automatic_force_units_follow_metadata_manual_overrides_survive_refresh(modal_page):
    page, app = modal_page
    dataset = page.files[0].dataset
    dataset.series[0].unit = "kN"
    page._refresh_identification_sources()
    assert page.modal_input_table.cellWidget(0, 2).currentText() == "kN"
    page.modal_input_table.cellWidget(0, 2).setCurrentText("mN")
    page._refresh_identification_sources()
    assert page.modal_input_table.cellWidget(0, 2).currentText() == "mN"
    page.modal_input_table.cellWidget(0, 3).setValue(2)
    assert page.modal_show_corrections.isChecked()
    assert "系数修正" in page.modal_source_summary.text()
    assert not page.modal_input_table.isColumnHidden(3)


def test_frequency_limit_is_optional_and_restores_manual_values(modal_page):
    page, app = modal_page
    page.modal_limit_frequency.setChecked(True)
    page.modal_frequency_min.setValue(5)
    page.modal_frequency_max.setValue(25)
    page.modal_limit_frequency.setChecked(False)
    assert page.modal_frequency_min.value() == page.modal_frequency_max.value() == 0
    assert page.modal_range_panel.isHidden()
    page.modal_limit_frequency.setChecked(True)
    assert (page.modal_frequency_min.value(), page.modal_frequency_max.value()) == (5, 25)


def test_combined_physical_confirmation_preserves_both_safety_checks(modal_page):
    page, app = modal_page
    page.modal_physical_confirm.setChecked(False)
    assert not page.modal_force_confirm.isChecked() and not page.modal_driver_confirm.isChecked()
    with pytest.raises(ValueError, match="力已标定"):
        page._modal_input_snapshot()
    page.modal_physical_confirm.setChecked(True)
    assert page.modal_force_confirm.isChecked() and page.modal_driver_confirm.isChecked()
    page.modal_driver_confirm.setChecked(False)
    assert not page.modal_physical_confirm.isChecked()
    with pytest.raises(ValueError, match="同点同向"):
        page._modal_input_snapshot()


def test_reset_algorithm_defaults_restores_cmif_without_clearing_measurement_settings(modal_page):
    page, app = modal_page
    page.modal_max_order.setValue(24)
    page.modal_candidate_method.setCurrentIndex(page.modal_candidate_method.findData("segmented"))
    page.modal_candidate_prominence.setValue(3)
    page.modal_frequency_min.setValue(2)
    page._identification_stale = False
    assert "参数已自定义" in page.modal_algorithm_summary.text()
    page.modal_reset_algorithm_button.click()
    assert page.modal_candidate_method.currentData() == "cmif"
    assert page.modal_candidate_prominence.value() == 1
    assert page.modal_frequency_min.value() == 2
    assert page.modal_driver_combo.currentText() == "P0"
    assert page.modal_physical_confirm.isChecked()
    assert not page._identification_stale


def test_compact_settings_restore_saved_frequency_units_and_preview_on_fresh_page(modal_page, tmp_path):
    from python_vna.diagnostic.pages import ModalShapePage
    page, app = modal_page
    page.modal_frequency_min.setValue(2)
    page.modal_frequency_max.setValue(30)
    page.modal_input_table.cellWidget(0, 3).setValue(2)
    page.frequency_edit.setValue(10)
    page._start_candidate_task(preview=True)
    deadline = time.monotonic() + 20
    while page._candidate_task is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert page.last_mode["preview_only"]
    path = page.export_session(tmp_path / "compact.vnamodal")
    restored = ModalShapePage()
    try:
        restored.import_session(path)
        assert restored.modal_physical_confirm.isChecked()
        assert restored.modal_limit_frequency.isChecked()
        assert not restored.modal_range_panel.isHidden()
        assert restored.modal_show_corrections.isChecked()
        assert restored.last_mode["preview_only"]
        restored.preview_mode()
        assert restored.last_mode["preview_only"]
    finally:
        restored._shutdown_identification()
        restored.close()
