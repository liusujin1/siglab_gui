from dataclasses import replace
import threading
import time

import numpy as np
import pytest

from python_vna.diagnostic.modal_candidates import (
    CandidateSettings, merge_candidates, prepare_candidate_input, search_modal_candidates,
)
from python_vna.diagnostic.modal_identification import (
    FRFMeasurement, ModalCancelled, ModalSettings, fit_local_preview, prepare_modal_input,
)
from test_modal_identification import modal_page, wait_for_task


def curves(frequencies=(3., 12., 52.), damping=.015):
    f = np.linspace(.2, 80, 2400)
    shapes = np.array([[1, .05, .4], [.1, 2, -.3], [-.2, .8, 1.]])[:, :len(frequencies)]
    h = np.zeros((f.size, 3), complex)
    for i, fn in enumerate(frequencies):
        q = f / fn
        h += (-q ** 2 / (1 - q ** 2 + 2j * damping * q))[:, None] * shapes[:, i]
    return [{"label": f"P{j}", "frequency_hz": f, "values": h[:, j]} for j in range(3)]


def preview_input(raw):
    measurements = [FRFMeasurement(c["label"], "synthetic", "force", c["label"], c["label"], 0, (j, 0, 0),
                                   c["frequency_hz"], c["values"], "m/s^2") for j, c in enumerate(raw)]
    return prepare_modal_input(measurements, 0, ModalSettings(maximum_order=12))


@pytest.mark.parametrize("method", ["cmif", "prominence", "segmented", "consensus"])
def test_candidate_methods_recover_low_and_weak_modes_without_changing_frf(method):
    raw = curves()
    original = [c["values"].copy() for c in raw]
    data = prepare_candidate_input(raw, CandidateSettings(method=method, band_width_hz=32, maximum_order=12))
    result = search_modal_candidates(data)
    found = [c["frequency_hz"] for c in result["candidates"]]
    for truth in (3., 12., 52.):
        assert any(abs(f - truth) < .15 for f in found), (method, truth, found)
    assert all(c["methods"] and c["channels"] for c in result["candidates"])
    if method == "consensus":
        assert all(c["confirmed"] and set(c["families"]) == {"spectral", "pole"} for c in result["candidates"])
    for c, original_values in zip(raw, original):
        np.testing.assert_array_equal(c["values"], original_values)
    assert not data["values"].flags.writeable


def test_cmif_does_not_require_phase_for_peak_but_rejects_real_psd_as_frf():
    raw = curves((3.,))
    raw[0]["values"] = abs(raw[0]["values"])
    with pytest.raises(ValueError, match="复数 FRF"):
        prepare_candidate_input(raw[:1], CandidateSettings())


def test_missing_frequency_samples_not_zero_filled_or_interpolated_across():
    raw = curves()
    for c in raw:
        c["values"][300:340] = np.nan
    data = prepare_candidate_input(raw, CandidateSettings())
    assert np.isnan(data["values"][300:340]).all()
    result = search_modal_candidates(data)
    assert np.isnan(result["indicator_db"][300:340]).all()
    assert any("未补零" in warning for warning in result["warnings"])


def test_constant_data_does_not_manufacture_a_highest_peak():
    raw = [{"label": "flat", "frequency_hz": np.arange(1., 101.), "values": np.ones(100, complex)}]
    result = search_modal_candidates(prepare_candidate_input(raw, CandidateSettings()))
    assert result["candidates"] == []


def test_cross_check_requires_spectral_and_pole_evidence_not_two_correlated_peak_detectors():
    def row(method, family, f):
        return {"frequency_hz": f, "methods": [method], "families": [family], "score": 5.,
                "channels": [0], "bands": [], "stable_orders": 3 if family == "pole" else 0, "damping_ratio": None}
    settings = CandidateSettings(method="consensus")
    assert merge_candidates([row("CMIF", "spectral", 50), row("突出度", "spectral", 50)], settings) == []
    result = merge_candidates([row("CMIF", "spectral", 50), row("分频段极点", "pole", 50.05)], settings)
    assert len(result) == 1 and result[0]["confirmed"]
    assert result[0]["frequency_hz"] == 50.05
    assert merge_candidates([row("CMIF", "spectral", 10), row("分频段极点", "pole", 20)], settings) == []


@pytest.mark.parametrize("changes", [{"minimum_hz": 10, "maximum_hz": 2}, {"band_overlap": .9},
                                         {"maximum_order": 7}, {"prominence_db": float("nan")}])
def test_invalid_candidate_settings(changes):
    with pytest.raises(ValueError):
        prepare_candidate_input(curves(), replace(CandidateSettings(), **changes))


def test_candidate_cancellation():
    data = prepare_candidate_input(curves(), CandidateSettings(method="segmented"))
    with pytest.raises(ModalCancelled):
        search_modal_candidates(data, cancel=lambda: True)


def test_tiny_manual_band_is_rejected_before_allocating_millions_of_bands():
    data = prepare_candidate_input(curves(), CandidateSettings(method="segmented", band_width_hz=.0001))
    with pytest.raises(ValueError, match="128"):
        search_modal_candidates(data)


def test_local_preview_retains_common_residue_scale_and_never_outputs_mass():
    data = preview_input(curves((12.,)))
    result = fit_local_preview(data, 12.)
    assert result["preview_only"] and result["mass_kg"] is None
    assert result["accepted"].all()
    np.testing.assert_allclose(result["coefficients"], [1, .1, -.2], rtol=1e-4, atol=1e-5)
    assert max(result["errors"]) < 1e-4


def test_local_preview_sparse_data_is_failure_not_original_frequency_bin():
    data = preview_input(curves((12.,)))
    sparse = replace(data, frequency_hz=data.frequency_hz[::500], receptance=data.receptance[::500])
    with pytest.raises(ValueError, match="未使用原始 FRF 回退"):
        fit_local_preview(sparse, 12.)


def test_local_preview_rejects_bad_channels_instead_of_mixing_frf_and_coefficients():
    raw = curves((12.,))
    raw[2]["values"] = np.random.default_rng(53).normal(size=2400) + 1j * np.random.default_rng(54).normal(size=2400)
    data = preview_input(raw)
    result = fit_local_preview(data, 12.)
    assert not result["accepted"][2]
    assert np.isnan(result["coefficients"][2])
    assert result["warnings"]


def wait_candidate(page, app):
    deadline = time.monotonic() + 20
    while page._candidate_task is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert page._candidate_task is None


def test_candidate_ui_source_labels_export_session_and_stale_guard(modal_page, tmp_path):
    page, app = modal_page
    assert page.find_peaks()
    assert "CMIF" in page.candidate_list.item(0).text()
    assert "measured.vna" in page.candidate_list.item(0).toolTip()
    exported = page.export_modal_candidates(tmp_path / "candidates.csv")
    assert "modal_candidates_not_identified_modes" in exported.read_text(encoding="utf-8-sig")
    destination = page.export_session(tmp_path / "session.vnamodal")
    page.import_session(destination)
    assert page._candidate_search_result is not None
    assert not page._candidate_search_result["stale"]
    assert page.modal_candidate_method.currentData() == "cmif"
    page.modal_candidate_prominence.setValue(3.)
    with pytest.raises(ValueError, match="待更新"):
        page.export_modal_candidates(tmp_path / "stale.csv")


def test_candidate_settings_and_search_do_not_replace_or_invalidate_formal_results(modal_page):
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    result = page._identification_result
    page.modal_candidate_prominence.setValue(2.)
    page.modal_candidate_prominence.editingFinished.emit()
    page.find_peaks()
    assert page._identification_result is result
    assert not page._identification_stale
    assert all("mass_kg" not in row for row in page._candidate_search_result["candidates"])


def test_local_preview_animation_does_not_turn_into_raw_frequency_preview(modal_page):
    page, app = modal_page
    page.frequency_edit.setValue(10.)
    page._start_candidate_task(preview=True)
    wait_candidate(page, app)
    assert page.last_mode["preview_only"]
    assert "非正式局部预览" in page.modal_preview_status.text()
    assert page._identification_result is None
    mode = page.last_mode
    page.preview_mode()
    assert page.last_mode is mode
    assert page._preview_timer.isActive()
    page.modal_force_confirm.setChecked(False)
    assert page.last_mode is None
    assert not page._preview_timer.isActive()


def test_failed_local_preview_preserves_previous_image_and_reports_reason(modal_page, monkeypatch):
    import python_vna.diagnostic.modal_candidate_ui as ui
    page, app = modal_page
    page.extract_mode()
    previous = page.last_mode

    def failed(*args, **kwargs):
        raise ValueError("局部拟合失败；未使用原始 FRF 回退")

    monkeypatch.setattr(ui, "fit_local_preview", failed)
    page._start_candidate_task(preview=True)
    wait_candidate(page, app)
    assert page.last_mode is previous
    assert "未使用原始 FRF 回退" in page.modal_candidate_status.text()


def test_local_preview_session_roundtrip_and_formal_state_is_separate(modal_page, tmp_path):
    page, app = modal_page
    page.frequency_edit.setValue(10.)
    page._start_candidate_task(preview=True)
    wait_candidate(page, app)
    before = page.last_mode["disp_complex"].copy()
    path = page.export_session(tmp_path / "preview.vnamodal")
    page.import_session(path)
    assert page.last_mode["preview_only"]
    assert page._identification_result is None
    np.testing.assert_array_equal(page.last_mode["disp_complex"], before)
    page.preview_mode()
    assert page.last_mode["preview_only"]


def test_local_preview_does_not_overwrite_formal_mass_or_result_export(modal_page, tmp_path):
    page, app = modal_page
    page.start_modal_identification()
    wait_for_task(page, app)
    formal = page._identification_result
    mass = formal["modes"][0]["mass_kg"]
    page.frequency_edit.setValue(10.)
    page._start_candidate_task(preview=True)
    wait_candidate(page, app)
    assert page.last_mode["preview_only"]
    assert page._identification_result is formal
    assert not page._identification_stale
    assert formal["modes"][0]["mass_kg"] == mass
    assert page.export_identification_results(tmp_path / "formal.csv").exists()


@pytest.mark.parametrize("operation", ["cancel", "input_change"])
def test_async_search_cancel_or_changed_input_preserves_previous_result(modal_page, monkeypatch, operation):
    import python_vna.diagnostic.modal_candidate_ui as ui
    page, app = modal_page
    page.find_peaks()
    previous = page._candidate_search_result
    gate = threading.Event()
    original = ui.search_modal_candidates

    def delayed(data, **kwargs):
        gate.wait(5)
        return original(data, **kwargs)

    monkeypatch.setattr(ui, "search_modal_candidates", delayed)
    page._start_candidate_task()
    if operation == "cancel":
        page.cancel_modal_identification()
    else:
        page.point_table.item(0, 6).setText("-1")
    gate.set()
    wait_candidate(page, app)
    assert page._candidate_search_result is previous


def test_invalid_candidate_session_rejected_before_replacing_data(modal_page, tmp_path):
    from python_vna.diagnostic.modal_session import load_modal_session, save_modal_session
    page, app = modal_page
    page.find_peaks()
    path = page.export_session(tmp_path / "bad.vnamodal")
    state = load_modal_session(path)
    state["candidate_search"]["result"]["candidates"][0]["channels"] = [999]
    save_modal_session(path, state)
    original = page.files[0].dataset
    with pytest.raises(ValueError, match="candidate"):
        page.import_session(path)
    assert page.files[0].dataset is original
