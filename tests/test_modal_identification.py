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
