from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
from scipy.io import savemat

from python_vna.analysis_data import AnalysisDataset, AnalysisSeries
from python_vna.diagnostic.data import NumericTableFile, TraceAnalysisFile
from python_vna.diagnostic.pages import TraceAnalysisPage
from python_vna.ui.analysis_viewer import AnalysisWorkbench


@pytest.fixture
def app():
    instance = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield instance
    instance.processEvents()


def acceleration(time, *, velocity_um_s=10., frequency=10.):
    return velocity_um_s * 1e-6 * np.sqrt(2) * 2 * np.pi * frequency * np.sin(2 * np.pi * frequency * time)


@pytest.mark.parametrize("suffix", [".csv", ".dat", ".mat"])
def test_foundation_computes_velocity_bands_from_imported_time_only_files(app, tmp_path, suffix):
    time = np.arange(4096) / 256.
    signal = acceleration(time)
    values = np.column_stack([signal, 2 * signal, 3 * signal])
    path = tmp_path / ("simulation" + suffix)
    if suffix == ".mat":
        savemat(path, {"time": time, "acceleration": values})
    else:
        np.savetxt(path, np.column_stack([time, values]), delimiter=",", header="Time,AX,AY,AZ", comments="")
    page = AnalysisWorkbench(include_derived_tab=False)
    try:
        page._load_path(path)
        dataset = page._datasets[0]
        assert not dataset.autospectrum
        assert len(dataset.series) == 3
        assert page.foundation_vib_edit.text() == "1,2,3"
        page.series_list.setCurrentRow(0)
        page.plot_current()
        assert [name for name in page._plot_curves[page.foundation_plots[0]] if not name.startswith("VC ")] == [
            series.display_name for series in dataset.series]
        data_curves = [(x, y) for name, (x, y) in page._plot_curves[page.foundation_plots[0]].items()
                       if not name.startswith("VC ")]
        assert len(data_curves) == 3
        for index, (frequency, velocity) in enumerate(data_curves):
            peak = np.argmin(abs(frequency - 10.))
            assert frequency[peak] == pytest.approx(10., rel=.01)
            assert velocity[peak] == pytest.approx(10. * (index + 1), rel=.002)
    finally:
        page.close()


def test_foundation_time_uses_computed_fft_spacing_and_channel_factor(app):
    time = np.arange(512) / 512.
    dataset = AnalysisDataset(1, Path("simulation.mat"), "simulation.mat", 512.,
                              time_s=time, channels={"A": acceleration(time, frequency=20.)}, rbw_hz=1000.,
                              series=[AnalysisSeries(1, 0, "A", "Acceleration", "m/s^2", -2.)])
    page = AnalysisWorkbench(include_derived_tab=False)
    try:
        frequency, velocity = page._foundation_vibration_curve(dataset, dataset.series[0])
        peak = np.argmin(abs(frequency - 20.))
        assert velocity[peak] == pytest.approx(20., rel=.01)
    finally:
        page.close()


def test_foundation_time_respects_analysis_window(app):
    time = np.arange(8192) / 256.
    values = acceleration(time) * np.where(time < 16., 1., 4.)
    dataset = AnalysisDataset(1, Path("simulation.mat"), "simulation.mat", 256., time_s=time,
                              channels={"A": values}, series=[AnalysisSeries(1, 0, "A", "Acceleration")])
    page = AnalysisWorkbench(include_derived_tab=False)
    try:
        page.time_start_edit.setText("0")
        page.time_end_edit.setText(str(16. - 1 / 256.))
        f, first = page._foundation_vibration_curve(dataset, dataset.series[0])
        page.time_start_edit.setText("16")
        page.time_end_edit.setText("")
        second_f, second = page._foundation_vibration_curve(dataset, dataset.series[0])
        np.testing.assert_array_equal(f, second_f)
        assert second[np.argmin(abs(f - 10.))] / first[np.argmin(abs(f - 10.))] == pytest.approx(4.)
    finally:
        page.close()


def test_foundation_default_channels_follow_source_without_overwriting_manual_choice(app):
    page = AnalysisWorkbench(include_derived_tab=False)
    def source(index, count, native=False):
        series = [AnalysisSeries(index, channel, str(channel), f"Channel {channel}") for channel in range(count)]
        return AnalysisDataset(index, Path(f"source{index}.mat"), f"source{index}", 100., series=series,
                               channels={item.channel_key: np.ones(32) for item in series},
                               autospectrum={"0": np.ones(3)} if native else {})
    try:
        page._suspend_auto_plot = True
        page._datasets = [source(1, 3), source(2, 4, True), source(3, 1)]
        page._refresh_dataset_lists()
        assert page.foundation_vib_edit.text() == "1,2,3"
        page.foundation_vib_file_combo.setCurrentIndex(1)
        assert page.foundation_vib_edit.text() == "2,3,4"
        page.foundation_vib_file_combo.setCurrentIndex(2)
        assert page.foundation_vib_edit.text() == "1"
        page.foundation_vib_edit.setText("2,3")
        page.foundation_vib_file_combo.setCurrentIndex(0)
        assert page.foundation_vib_edit.text() == "2,3"
    finally:
        page.close()


def cangfu_transfer_file(path):
    rng = np.random.default_rng(401)
    excitation = rng.normal(size=(256, 3))
    response = 2 * np.roll(excitation, 1, axis=0)
    trace_data = np.empty((2, 1), dtype=object)
    trace_data[0, 0], trace_data[1, 0] = excitation, response
    savemat(path, {"trace_data": trace_data,
                   "trace_info": {"fs": 256, "type": "looptf", "loopinfo": "Acceleration: Z"}})


def test_cangfu_transfer_renders_measured_phase_and_opens_matching_window(app, tmp_path, monkeypatch):
    path = tmp_path / "trace084.mat"
    cangfu_transfer_file(path)
    page = TraceAnalysisPage()
    try:
        page.load_paths([path])
        assert page.cangfu_time_window_button.text() == "幅频图"
        assert page.cangfu_psd_window_button.text() == "相频图"
        assert page.cangfu_psd_window_button.isEnabled()
        page.plot_current()
        phase = page.current_file().table.metadata["cangfu_phase_pairs"][0]
        actual_x, actual_y = next(iter(page._plot_curves[page.cangfu_psd_plot].values()))
        assert np.max(abs(actual_y)) > 20.
        np.testing.assert_array_equal(actual_x, phase.x)
        np.testing.assert_array_equal(actual_y, phase.y)
        assert page._log_modes[page.cangfu_psd_plot] == (True, False)
        assert page.cangfu_psd_plot.getAxis("left").labelText == "相位 (deg)"
        page.cangfu_psd_window_button.click()
        assert "相频响应" in page._plot_windows[0].windowTitle()
        page._active_plot = page.cangfu_psd_plot
        exported = tmp_path / "phase.csv"
        dialog = Mock(return_value=(str(exported), "CSV"))
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", dialog)
        page._export_active()
        assert Path(dialog.call_args.args[2]).name == "cangfu_transfer_phase.csv"
        rows = np.loadtxt(exported, delimiter=",", skiprows=1, encoding="utf-8-sig")
        np.testing.assert_allclose(rows[:, 1], phase.y)
    finally:
        page._close_plot_windows()
        page.close()


def test_cangfu_multi_file_phase_selection_and_domain_switch_do_not_mix_curves(app, tmp_path):
    paths = [tmp_path / "trace084.mat", tmp_path / "trace085.mat"]
    for path in paths:
        cangfu_transfer_file(path)
    page = TraceAnalysisPage()
    try:
        page.load_paths(paths)
        page.file_list.selectAll()
        page.plot_current()
        assert len(page._plot_curves[page.cangfu_time_plot]) == 2
        assert len(page._plot_curves[page.cangfu_psd_plot]) == 2
        time = np.arange(256) / 256.
        values = np.sin(2 * np.pi * 10 * time)
        table = NumericTableFile(Path("time.mat"), "time.mat", ["Sensor"], values[:, None])
        page.files.append(TraceAnalysisFile(table, "cangfu_trace", time, {"Sensor": values}, 256.))
        page.file_list.addItem("time.mat")
        page.hold_check.setChecked(True)
        page.file_list.setCurrentRow(2, QtCore.QItemSelectionModel.ClearAndSelect)
        page.plot_current()
        assert page.cangfu_time_window_button.text() == "时域图"
        assert page.cangfu_psd_window_button.text() == "PSD图"
        assert page.cangfu_psd_window_button.isEnabled()
        assert "时域" in page.cangfu_time_plot.plotItem.titleLabel.text
        assert "PSD" in page.cangfu_psd_plot.plotItem.titleLabel.text
        assert all("相位" not in name for name in page._plot_curves[page.cangfu_psd_plot])
    finally:
        page.close()
