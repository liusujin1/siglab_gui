from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from python_vna.analysis_derivation import (
    DERIVE_BASE_TO_TOP,
    DERIVE_TOP_TO_BASE,
    derive_psd_from_transfer,
    derive_time_from_transfer,
)
from python_vna.analysis_curve_editing import (
    apply_db_magnitude_profile,
    apply_power_db_profile,
    stitch_frequency_curves,
    transfer_from_db_points,
)
from python_vna.analysis_algorithms import (
    FilterConfig,
    apply_filter_to_signal,
    compute_cumulative_spectrum,
    compute_dynamic_stiffness,
    compute_transfer_function_welch,
    compute_welch_psd,
    compute_periodogram_psd,
    third_octave_bands,
    compute_third_octave_velocity_rms,
    convert_acceleration_psd,
    convert_acceleration_time_series,
)
from python_vna.analysis_data import (
    AnalysisDataset,
    AnalysisSeries,
    dataset_from_measurement,
    load_analysis_path,
    load_continuous_channels,
)
from python_vna.continuous_recording import ContinuousDatWriter
from python_vna.daq.base import BackendFrame
from python_vna.models import MeasurementSet
from python_vna.storage import default_session_config
from python_vna.ui.analysis_viewer import AnalysisViewer, _vc_reference_frequency_velocity
from python_vna.ui.main_window import DataTipText, VnaAxisItem


def _matlab_sd_round(value: float, digits: int, multiple: int) -> float:
    decade = np.ceil(np.log10(abs(value)))
    if abs(value) - 10.0**decade == 0.0:
        decade += 1.0
    scale = 10.0 ** (digits - decade)
    buffer = scale / multiple
    return float(np.floor(buffer * value + 0.5) / buffer)


def _dark_analysis_theme() -> dict[str, object]:
    return {
        "window_bg": "#111827",
        "panel_bg": "#172033",
        "panel_bg_alt": "#22304a",
        "cell_bg": "#263653",
        "plot_bg": "#05070d",
        "text": "#f5f7fb",
        "muted_text": "#a9b7c9",
        "label_text": "#f7d774",
        "axis": "#f5f7fb",
        "accent": "#2563eb",
        "accent_alt": "#0f9f8f",
        "border": "#31415f",
        "control_border": "#4b5d7a",
        "table_bg": "#0f172a",
        "menu_bg": "#101827",
        "grid_alpha": 0.25,
    }


class AnalysisAlgorithmTests(unittest.TestCase):
    def test_periodogram_and_cumulative_spectrum_are_stable(self):
        sample_rate = 1000.0
        time_s = np.arange(1000, dtype=float) / sample_rate
        signal = np.sin(2.0 * np.pi * 50.0 * time_s)

        freqs, psd = compute_periodogram_psd(signal, sample_rate)
        welch_freqs, welch_psd = compute_welch_psd(signal, sample_rate, 256)
        peak_frequency = freqs[int(np.argmax(psd))]
        cumulative_f, cumulative = compute_cumulative_spectrum(freqs, psd)

        self.assertAlmostEqual(peak_frequency, 50.0, delta=1.0)
        self.assertAlmostEqual(welch_freqs[int(np.argmax(welch_psd))], 50.0, delta=4.0)
        self.assertEqual(cumulative_f.shape, cumulative.shape)
        self.assertGreater(float(cumulative[-1]), 0.0)

    def test_welch_transfer_from_time_data_recovers_gain(self):
        sample_rate = 1000.0
        time_s = np.arange(4096, dtype=float) / sample_rate
        reference = np.sin(2.0 * np.pi * 40.0 * time_s)
        response = 2.5 * reference

        freqs, xfer = compute_transfer_function_welch(reference, response, sample_rate, 512)

        peak_index = int(np.argmin(np.abs(freqs - 40.0)))
        self.assertAlmostEqual(abs(xfer[peak_index]), 2.5, delta=0.1)

    def test_derived_psd_transfer_converts_both_directions(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        psd = np.array([1.0, 2.0, 3.0], dtype=float)
        transfer = np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)

        top_f, top_psd = derive_psd_from_transfer(
            freqs,
            psd,
            freqs,
            transfer,
            direction=DERIVE_BASE_TO_TOP,
        )
        base_f, base_psd = derive_psd_from_transfer(
            freqs,
            top_psd,
            freqs,
            transfer,
            direction=DERIVE_TOP_TO_BASE,
        )

        np.testing.assert_allclose(top_f, freqs)
        np.testing.assert_allclose(top_psd, psd * 4.0)
        np.testing.assert_allclose(base_f, freqs)
        np.testing.assert_allclose(base_psd, psd)

    def test_derived_psd_transfer_can_apply_coherence_correction(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        psd = np.array([1.0, 2.0, 3.0], dtype=float)
        transfer = np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)
        coherence = np.full(freqs.shape, 0.25, dtype=float)

        top_f, top_psd = derive_psd_from_transfer(
            freqs,
            psd,
            freqs,
            transfer,
            direction=DERIVE_BASE_TO_TOP,
            coherence_frequency=freqs,
            coherence_values=coherence,
            coherence_correction=True,
        )
        base_f, base_psd = derive_psd_from_transfer(
            freqs,
            top_psd,
            freqs,
            transfer,
            direction=DERIVE_TOP_TO_BASE,
            coherence_frequency=freqs,
            coherence_values=coherence,
            coherence_correction=True,
        )

        np.testing.assert_allclose(top_f, freqs)
        np.testing.assert_allclose(top_psd, psd * 16.0)
        np.testing.assert_allclose(base_f, freqs)
        np.testing.assert_allclose(base_psd, psd)

    def test_manual_transfer_points_fit_in_log_frequency(self):
        control_f = np.array([10.0, 100.0, 1000.0], dtype=float)
        control_db = np.array([0.0, 20.0, 40.0], dtype=float)
        target_f = np.array([10.0, 100.0, 1000.0], dtype=float)

        fitted_f, fitted_h = transfer_from_db_points(control_f, control_db, target_f)
        top_f, top_psd = derive_psd_from_transfer(
            target_f,
            np.ones_like(target_f),
            fitted_f,
            fitted_h,
            direction=DERIVE_BASE_TO_TOP,
        )

        np.testing.assert_allclose(fitted_f, target_f)
        np.testing.assert_allclose(20.0 * np.log10(fitted_h), control_db, atol=1e-12)
        np.testing.assert_allclose(top_f, target_f)
        np.testing.assert_allclose(top_psd, [1.0, 100.0, 10000.0], rtol=1e-12)

    def test_transfer_magnitude_edit_preserves_complex_phase(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        phase = np.exp(1.0j * np.array([0.1, 0.2, 0.3], dtype=float))
        source = np.array([1.0, 2.0, 3.0], dtype=float) * phase

        edited_f, edited = apply_db_magnitude_profile(
            freqs,
            source,
            np.array([10.0, 20.0, 30.0], dtype=float),
            np.array([6.0, 6.0, 6.0], dtype=float),
        )

        np.testing.assert_allclose(edited_f, freqs)
        np.testing.assert_allclose(np.angle(edited), np.angle(source), atol=1e-12)
        np.testing.assert_allclose(np.abs(edited), 10.0 ** (6.0 / 20.0), rtol=1e-12)

    def test_psd_edit_and_stitch_helpers(self):
        freqs = np.array([10.0, 20.0, 30.0, 40.0], dtype=float)
        psd = np.ones_like(freqs)

        edited_f, edited_psd = apply_power_db_profile(
            freqs,
            psd,
            np.array([10.0, 20.0, 40.0], dtype=float),
            np.array([0.0, 10.0, 0.0], dtype=float),
        )
        stitched_f, stitched = stitch_frequency_curves(
            edited_f,
            edited_psd,
            np.array([25.0, 35.0, 45.0], dtype=float),
            np.array([100.0, 200.0, 300.0], dtype=float),
            30.0,
        )

        self.assertGreater(edited_psd[1], edited_psd[0])
        np.testing.assert_allclose(stitched_f, [10.0, 20.0, 30.0, 35.0, 45.0])
        np.testing.assert_allclose(stitched[-2:], [200.0, 300.0])

    def test_derived_time_requires_complex_transfer_and_recovers_gain(self):
        sample_rate = 1000.0
        time_s = np.arange(1000, dtype=float) / sample_rate
        signal = np.sin(2.0 * np.pi * 20.0 * time_s)
        transfer_f = np.array([1.0, 20.0, 500.0], dtype=float)
        complex_h = np.full(3, 2.0 + 0.0j, dtype=complex)

        derived_t, derived = derive_time_from_transfer(
            time_s,
            signal,
            sample_rate,
            transfer_f,
            complex_h,
            direction=DERIVE_BASE_TO_TOP,
        )
        no_phase_t, no_phase = derive_time_from_transfer(
            time_s,
            signal,
            sample_rate,
            transfer_f,
            np.full(3, 2.0, dtype=float),
            direction=DERIVE_BASE_TO_TOP,
        )

        np.testing.assert_allclose(derived_t, time_s)
        np.testing.assert_allclose(derived, 2.0 * signal, atol=1e-10)
        self.assertEqual(no_phase_t.size, 0)
        self.assertEqual(no_phase.size, 0)

    def test_acceleration_conversions_match_expected_units(self):
        freqs = np.array([1.0, 10.0, 100.0], dtype=float)
        psd = np.ones(3, dtype=float)
        velocity_f, velocity_psd = convert_acceleration_psd(freqs, psd, "Velocity")

        np.testing.assert_allclose(velocity_f, freqs)
        np.testing.assert_allclose(velocity_psd, psd / ((2.0 * np.pi * freqs) ** 2) * 1e12)

        sample_rate = 1000.0
        signal = np.sin(2.0 * np.pi * 20.0 * np.arange(1000) / sample_rate)
        velocity = convert_acceleration_time_series(signal, sample_rate, "Velocity")
        self.assertEqual(velocity.shape, signal.shape)
        self.assertTrue(np.all(np.isfinite(velocity)))

    def test_foundation_helpers_compute_expected_curves(self):
        freqs = np.linspace(2.0, 200.0, 200)
        psd = np.full_like(freqs, 1e-6)
        centers, velocity = compute_third_octave_velocity_rms(freqs, psd, 1.0)

        self.assertGreater(centers.size, 0)
        self.assertEqual(centers.shape, velocity.shape)
        self.assertTrue(np.all(velocity > 0.0))

        frf = np.full_like(freqs, 2.0 + 0.0j, dtype=complex)
        stiff_f, stiffness = compute_dynamic_stiffness(freqs, frf, 1.0, 800.0)
        self.assertEqual(stiff_f.shape, stiffness.shape)
        self.assertTrue(np.all(stiffness > 0.0))

    def test_highpass_filter_trim_matches_matlab_padding_limit(self):
        sample_rate = 2560.0
        time_s = np.arange(4096, dtype=float) / sample_rate
        signal = 0.2 + np.sin(2.0 * np.pi * 20.0 * time_s)

        filtered, trim = apply_filter_to_signal(
            signal,
            sample_rate,
            FilterConfig(highpass_enabled=True, highpass_hz=5.0, order=4),
        )

        self.assertEqual(filtered.shape, signal.shape)
        self.assertTrue(np.all(np.isfinite(filtered)))
        self.assertLess(trim, 64)

    def test_third_octave_band_edges_follow_matlab_unrounded_centers(self):
        centers, lower_edges, upper_edges = third_octave_bands(1.0, 100.0)
        index = int(np.argmin(np.abs(centers - 20.0)))
        raw_center = 1000.0 / (10.0 ** (3.0 / (10.0 * 3.0))) ** 17

        self.assertAlmostEqual(centers[index], 20.0)
        self.assertAlmostEqual(lower_edges[index], _matlab_sd_round(raw_center / (2.0 ** (1.0 / 6.0)), 3, 5))
        self.assertAlmostEqual(upper_edges[index], _matlab_sd_round(raw_center * (2.0 ** (1.0 / 6.0)), 3, 5))


class AnalysisDataTests(unittest.TestCase):
    def test_load_numeric_text_with_time_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.sample_rate, 10.0)
        self.assertEqual([series.display_name for series in dataset.series], ["Ch 1", "Ch 2"])
        time_s, values = dataset.load_time_series("Ch 2")
        np.testing.assert_allclose(time_s, [0.0, 0.1, 0.2])
        np.testing.assert_allclose(values, [2.0, 4.0, 6.0])

    def test_load_continuous_manifest_lazily_reads_frames(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0", "ai1"],
                    data=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=float),
                    timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")
            time_s, values = dataset.load_time_series("ai1")
            bulk_time_s, bulk_channels = load_continuous_channels(dataset, ["ai0", "ai1"])

        self.assertTrue(dataset.is_continuous)
        self.assertEqual(dataset.sample_rate, 2560.0)
        self.assertEqual(dataset.metadata["frame_size"], 4096)
        np.testing.assert_allclose(values, [4.0, 5.0, 6.0])
        np.testing.assert_allclose(time_s, np.array([0.0, 1.0, 2.0]) / 2560.0)
        np.testing.assert_allclose(bulk_time_s, time_s)
        np.testing.assert_allclose(bulk_channels["ai0"], [1.0, 2.0, 3.0])
        np.testing.assert_allclose(bulk_channels["ai1"], [4.0, 5.0, 6.0])

    def test_load_legacy_vna_exposes_frequency_results(self):
        dataset = load_analysis_path(r"D:\SynologyDrive\codex\vna\dsa\vna\sample.vna")

        self.assertGreaterEqual(len(dataset.series), 4)
        self.assertIsNotNone(dataset.frequency_hz)
        self.assertGreater(np.asarray(dataset.frequency_hz).size, 0)
        self.assertTrue(dataset.autospectrum)
        self.assertTrue(dataset.frf)
        self.assertTrue(dataset.coherence)


class AnalysisViewerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_viewer_loads_text_path_and_plots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,1\n0.3,0\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                self.assertEqual(viewer.series_list.count(), 1)
                self.assertEqual(viewer.series_list.item(0).text(), "data.csv+ch1")
                self.assertFalse(viewer.series_list.item(0).isSelected())
                self.assertEqual(viewer._plot_curves[viewer.main_plots[0]], {})
                viewer.series_list.item(0).setSelected(True)
                self.assertGreater(len(viewer._plot_curves[viewer.main_plots[0]]), 0)
                viewer.main_plots[0].clear()
                viewer.plot_current()
                self.assertGreater(len(viewer._plot_curves[viewer.main_plots[0]]), 0)
            finally:
                viewer.close()

    def test_continuous_recording_psd_uses_fft_block_and_trans_from_time_data(self):
        session = default_session_config()
        session.acquisition.sample_rate = 1024.0
        session.acquisition.frame_size = 1024
        time_s = np.arange(4096, dtype=float) / session.acquisition.sample_rate
        reference = np.sin(2.0 * np.pi * 32.0 * time_s)
        response = 3.0 * reference
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
            )
            writer.start()
            for frame_index in range(4):
                start = frame_index * 1024
                stop = start + 1024
                writer.write_frame(
                    BackendFrame(
                        sample_rate=session.acquisition.sample_rate,
                        channel_names=["ai0", "ai1"],
                        data=np.vstack([reference[start:stop], response[start:stop]]),
                        timestamps=time_s[start:stop],
                        frame_index=frame_index + 1,
                        metadata={},
                    )
                )
            writer.close()

            viewer = AnalysisViewer()
            try:
                viewer._load_path(output_dir / "manifest.json")
                viewer.fs_hint_spin.setValue(512.0)
                dataset = viewer._datasets[0]
                ref_series = dataset.series[0]
                resp_series = dataset.series[1]

                psd_f, psd = viewer._psd_for_series(dataset, ref_series, scale=1.0)
                trans_curve = viewer._transfer_curve(dataset, resp_series)
                viewer.fs_hint_spin.setValue(1024.0)
                psd_f_large, _psd_large = viewer._psd_for_series(dataset, ref_series, scale=1.0)

                self.assertGreater(psd.size, 0)
                self.assertLessEqual(psd_f.size, 256)
                self.assertGreater(psd_f_large.size, psd_f.size)
                self.assertIsNotNone(trans_curve)
                f, trans_db, _label = trans_curve
                target_index = int(np.argmin(np.abs(f - 32.0)))
                self.assertAlmostEqual(trans_db[target_index], 20.0 * np.log10(3.0), delta=0.35)
            finally:
                viewer.close()

    def test_vna_raw_aspec_psd_matches_main_display_density_scaling(self):
        session = default_session_config()
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {"ai0": np.array([1.0, 2.0], dtype=float)},
            },
            spectra={
                "f": np.array([0.0, 10.0, 20.0], dtype=float),
                "autospectrum": {"ai0": np.array([9.0, 18.0, 36.0], dtype=float)},
            },
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "rbw_hz": 2.0,
                "legacy_runtime_wincor": 2.0 / 3.0,
                "legacy_channels": {"ai0": {"euscale_fac": 3.0, "eu_string": "m/s^2"}},
            },
        )
        dataset = dataset_from_measurement(measurement, session_config=session, name="current")
        viewer = AnalysisViewer()
        try:
            series = dataset.series[0]
            f, psd = viewer._psd_for_series(dataset, series, scale=float(series.scale))

            np.testing.assert_allclose(f, [10.0, 20.0])
            np.testing.assert_allclose(psd, [18.0 * (3.0**2) / 2.0, 36.0 * (3.0**2) / 2.0])
        finally:
            viewer.close()

    def test_foundation_vibration_uses_matlab_aspec_without_window_correction(self):
        session = default_session_config()
        freqs = np.arange(0.0, 101.0, 1.0)
        aspec = np.full_like(freqs, 4.0)
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {"ai0": np.array([1.0, 2.0], dtype=float)},
            },
            spectra={"f": freqs, "autospectrum": {"ai0": aspec}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "rbw_hz": 1.0,
                "legacy_runtime_wincor": 0.25,
                "legacy_channels": {"ai0": {"euscale_fac": 2.0, "eu_string": "m/s^2"}},
            },
        )
        dataset = dataset_from_measurement(measurement, session_config=session, name="foundation")
        viewer = AnalysisViewer()
        try:
            centers, velocity = viewer._foundation_vibration_curve(dataset, dataset.series[0])
            expected_f = freqs[1:]
            expected_psd = aspec[1:] * (2.0**2) / 1.0
            expected_centers, expected_velocity = compute_third_octave_velocity_rms(
                expected_f,
                expected_psd,
                1.0,
            )

            np.testing.assert_allclose(centers, expected_centers)
            np.testing.assert_allclose(velocity, expected_velocity)
        finally:
            viewer.close()

    def test_viewer_loads_multiple_files_and_matches_matlab_series_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "first.csv"
            second = Path(tmpdir) / "second.csv"
            first.write_text("0,1,2\n0.1,2,3\n0.2,3,4\n", encoding="utf-8")
            second.write_text("0,5\n0.1,6\n0.2,7\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getOpenFileNames",
                    return_value=([str(first), str(second)], ""),
                ):
                    viewer._load_file()
                self.assertFalse(hasattr(viewer, "dataset_list"))
                labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
                self.assertEqual(labels, ["first.csv+ch1", "first.csv+ch2", "second.csv+ch1"])

                viewer._load_path(first)
                labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
                self.assertIn("first.csv+ch1#2", labels)
                self.assertIn("first.csv+ch2#2", labels)
            finally:
                viewer.close()

    def test_load_folder_skips_bad_supported_files_without_modal_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            good = folder / "good.csv"
            bad = folder / "bad.dat"
            good.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")
            bad.write_bytes(b"\xaf not a python vna dat")
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getExistingDirectory",
                    return_value=str(folder),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QMessageBox.warning"
                ) as warning:
                    viewer._load_folder()

                warning.assert_not_called()
                self.assertEqual(viewer.series_list.count(), 2)
                self.assertIn("failed 1", viewer.statusBar().currentMessage())
            finally:
                viewer.close()

    def test_load_folder_remembers_directory_even_when_no_supported_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getExistingDirectory",
                    return_value=str(folder),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QMessageBox.warning"
                ):
                    viewer._load_folder()

                self.assertEqual(viewer._last_directory, folder)
            finally:
                viewer.close()

    def test_viewer_uses_vna_axis_items_context_menu_and_data_tips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,1\n0.3,0\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                plot = viewer.main_plots[0]
                self.assertIsInstance(plot.getAxis("bottom"), VnaAxisItem)
                menu, actions = viewer._build_plot_context_menu(plot)
                try:
                    action_texts = [
                        action.text()
                        for action in menu.actions()
                        if not action.isSeparator()
                    ]
                    self.assertEqual(
                        action_texts,
                        ["Back One Zoom", "Auto Scale", "Data Tip", "Cursor Readout", "Clear Data Tips"],
                    )
                    self.assertTrue(actions["data_tip"].isCheckable())
                    self.assertTrue(actions["cursor"].isCheckable())
                finally:
                    menu.close()
                self.assertFalse(plot.getPlotItem().menuEnabled())
                self.assertFalse(plot.getPlotItem().vb.menuEnabled())
                self.assertGreaterEqual(viewer.series_list.minimumHeight(), 260)
                self.assertFalse(hasattr(viewer, "cursor_button"))
                self.assertFalse(hasattr(viewer, "data_tip_button"))

                viewer._toggle_data_tip_mode(True)
                placed = viewer._place_data_tip(plot, 0.1, 2.0)
                self.assertTrue(placed)
                self.assertEqual(len(viewer._data_tip_items[plot]), 1)
                data_tip = viewer._data_tip_items[plot][0]
                self.assertIsInstance(data_tip["text"], DataTipText)
                self.assertIn("X 0.1", data_tip["text"].toPlainText())

                moved = viewer._drag_data_tip_to_scene_pos(
                    plot,
                    data_tip,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.2, 1.0)),
                )
                self.assertTrue(moved)
                self.assertAlmostEqual(data_tip["x"], 0.2, places=6)
                label_moved = viewer._drag_data_tip_label_to_scene_pos(
                    plot,
                    data_tip,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.0, 3.0)),
                )
                self.assertTrue(label_moved)
                self.assertTrue(data_tip["label_anchor_manual"])
                self.assertEqual(data_tip["label_anchor"], (1.05, 1.05))
                self.assertAlmostEqual(data_tip["x"], 0.2, places=6)

                viewer._toggle_data_tip_mode(False)
                cursor_moved = viewer._move_cursor_from_scene_pos(
                    plot,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.1, 2.0)),
                )
                self.assertTrue(cursor_moved)
                self.assertIsNotNone(viewer._cursor_positions[plot])
                self.assertTrue(viewer._cursor_items[plot]["text"].isVisible())
                self.assertIn("X 0.1", viewer._cursor_items[plot]["text"].toPlainText())
            finally:
                viewer.close()

    def test_data_tip_context_menu_suppresses_plot_context_menu(self):
        viewer = AnalysisViewer()
        try:
            viewer._suppress_plot_context_menu_once()
            self.assertTrue(viewer._suppress_next_plot_context_menu)
        finally:
            viewer.close()

    def test_analysis_context_menu_selection_style_is_visible(self):
        viewer = AnalysisViewer()
        try:
            self.assertIn("QMenu::item:selected", viewer.styleSheet())
            self.assertIn("background:", viewer.styleSheet())
            self.assertIn("QPushButton:pressed", viewer.styleSheet())
            self.assertIn("QPushButton:checked", viewer.styleSheet())
        finally:
            viewer.close()

    def test_analysis_series_list_keeps_dark_theme_row_colors(self):
        viewer = AnalysisViewer(theme=_dark_analysis_theme())
        try:
            stylesheet = viewer.styleSheet()
            self.assertIn("alternate-background-color: #22304a", stylesheet)
            self.assertIn("QListWidget::item:alternate", stylesheet)
            palette = viewer.series_list.palette()
            self.assertEqual(palette.color(QtGui.QPalette.Base).name(), "#0f172a")
            self.assertEqual(palette.color(QtGui.QPalette.AlternateBase).name(), "#22304a")
            self.assertEqual(palette.color(QtGui.QPalette.Text).name(), "#f5f7fb")
            self.assertEqual(palette.color(QtGui.QPalette.Highlight).name(), "#2563eb")
        finally:
            viewer.close()

    def test_analysis_legend_text_updates_for_dark_theme(self):
        viewer = AnalysisViewer(theme=_dark_analysis_theme())
        try:
            plot = viewer.main_plots[0]
            plot.addLegend()
            plot.plot([1.0, 2.0], [2.0, 3.0], name="trace")

            viewer._apply_plot_theme(plot)

            label = plot.plotItem.legend.items[0][1]
            self.assertEqual(plot.plotItem.legend.opts["labelTextColor"], "#f5f7fb")
            self.assertIn("color:#f5f7fb", label.item.toHtml())
        finally:
            viewer.close()

    def test_cursor_palette_is_readable_on_light_theme(self):
        viewer = AnalysisViewer(theme={"plot_bg": "#ffffff"})
        try:
            palette = viewer._cursor_palette()
            self.assertEqual(palette["line"], "#0f4c81")
            self.assertNotEqual(palette["line"], "#fff176")
        finally:
            viewer.close()

    def test_single_analysis_plot_window_follows_theme(self):
        viewer = AnalysisViewer(theme={"plot_bg": "#ffffff", "window_bg": "#f4f7fb"})
        try:
            plot = viewer.main_plots[0]
            viewer._plot_curves[plot] = {"trace": (np.array([1.0, 2.0]), np.array([3.0, 4.0]))}
            viewer._open_plot_window_for_plot(plot)
            self.assertEqual(len(viewer._single_plot_windows), 1)
            dialog = viewer._single_plot_windows[0]
            detached_plot = dialog.findChildren(type(plot))[0]
            self.assertTrue(dialog.windowFlags() & QtCore.Qt.WindowMaximizeButtonHint)
            self.assertIn("background: #f4f7fb;", dialog.styleSheet())
            self.assertEqual(detached_plot.backgroundBrush().color().name(), "#ffffff")

            viewer.apply_theme({"plot_bg": "#000000", "window_bg": "#111827"})

            self.assertIn("background: #111827;", dialog.styleSheet())
            self.assertEqual(detached_plot.backgroundBrush().color().name(), "#000000")
        finally:
            for dialog in list(viewer._single_plot_windows):
                dialog.close()
            viewer.close()

    def test_export_uses_active_plot_curves_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "active.csv"
            viewer = AnalysisViewer()
            try:
                active = viewer.main_plots[1]
                viewer._active_plot = active
                viewer._plot_curves[viewer.main_plots[0]] = {
                    "top": (np.array([1.0]), np.array([2.0])),
                }
                viewer._plot_curves[active] = {
                    "active trace": (np.array([3.0, 4.0]), np.array([5.0, 6.0])),
                }
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    viewer._export_current_csv()
                text = destination.read_text(encoding="utf-8")
                self.assertIn("active_trace_x,active_trace_y", text)
                self.assertIn("3,5", text)
                self.assertNotIn("top_x", text)
                self.assertEqual(viewer._last_directory, destination.parent)
            finally:
                viewer.close()

    def test_sync_current_measurement_adds_channels(self):
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "ai0": np.array([1.0, 2.0], dtype=float),
                    "ai1": np.array([3.0, 4.0], dtype=float),
                },
            },
            spectra={"f": np.array([0.0, 10.0]), "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )
        viewer = AnalysisViewer()
        try:
            synced = viewer.sync_current_measurement(measurement, default_session_config())
            self.assertTrue(synced)
            self.assertEqual(viewer.series_list.count(), 2)
            labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
            self.assertEqual(labels, ["Current Measurement+ch1", "Current Measurement+ch2"])
        finally:
            viewer.close()

    def test_sync_current_measurement_filters_disabled_channels(self):
        session = default_session_config()
        session.ai_channels[2].enabled = False
        session.ai_channels[3].enabled = False
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "ai0": np.array([1.0, 2.0], dtype=float),
                    "ai1": np.array([3.0, 4.0], dtype=float),
                    "ai2": np.array([5.0, 6.0], dtype=float),
                    "ai3": np.array([7.0, 8.0], dtype=float),
                },
            },
            spectra={
                "f": np.array([0.0, 10.0]),
                "autospectrum": {
                    "ai0": np.array([1.0, 2.0]),
                    "ai1": np.array([3.0, 4.0]),
                    "ai2": np.array([5.0, 6.0]),
                    "ai3": np.array([7.0, 8.0]),
                },
            },
            frf={
                "ai0->ai1": np.array([1.0 + 0.0j]),
                "ai0->ai2": np.array([2.0 + 0.0j]),
            },
            coherence={
                "ai0->ai1": np.array([1.0]),
                "ai0->ai2": np.array([0.5]),
            },
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )
        viewer = AnalysisViewer()
        try:
            viewer.sync_current_measurement(measurement, session)
            dataset = viewer._datasets[0]

            self.assertEqual(dataset.channel_keys, ["ai0", "ai1"])
            self.assertEqual(set(dataset.autospectrum), {"ai0", "ai1"})
            self.assertEqual(set(dataset.frf), {"ai0->ai1"})
            self.assertEqual(viewer.series_list.count(), 2)
        finally:
            viewer.close()

    def test_dataset_from_measurement_filters_disabled_label_keys(self):
        session = default_session_config()
        session.ai_channels[2].label = "disabled accel"
        session.ai_channels[2].enabled = False
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "Ch 1": np.array([1.0, 2.0], dtype=float),
                    "Ch 2": np.array([3.0, 4.0], dtype=float),
                    "disabled accel": np.array([5.0, 6.0], dtype=float),
                },
            },
            spectra={"f": np.array([0.0, 10.0]), "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )

        dataset = dataset_from_measurement(measurement, session_config=session)

        self.assertEqual(dataset.channel_keys, ["Ch 1", "Ch 2"])

    def test_cursor_drag_callback_updates_the_target_analysis_plot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            rows = [f"{index / 10.0},{np.sin(index / 10.0):.12g}" for index in range(40)]
            path.write_text("\n".join(rows), encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                viewer.main_mode_combos[1].setCurrentText("Time")
                viewer.plot_current()
                top_plot = viewer.main_plots[0]
                target_plot = viewer.main_plots[1]
                scene_pos = target_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(1.0, np.sin(1.0)))

                moved = target_plot.getPlotItem().vb._on_left_drag(scene_pos)

                self.assertTrue(moved)
                self.assertIsNone(viewer._cursor_positions[top_plot])
                self.assertIsNotNone(viewer._cursor_positions[target_plot])
                self.assertTrue(viewer._cursor_items[target_plot]["text"].isVisible())
            finally:
                viewer.close()

    def test_foundation_vc_checkboxes_are_visually_distinct(self):
        viewer = AnalysisViewer()
        try:
            self.assertEqual(viewer.vc_a_check.objectName(), "vcCheck")
            self.assertFalse(viewer.vc_a_check.isChecked())
            self.assertTrue(viewer.vc_b_check.isChecked())
            self.assertTrue(viewer.vc_c_check.isChecked())
            self.assertFalse(viewer.vc_d_check.isChecked())
            self.assertFalse(viewer.vc_e_check.isChecked())
            self.assertFalse(viewer.vc_f_check.isChecked())
            self.assertIn("QCheckBox#vcCheck::indicator", viewer.styleSheet())
        finally:
            viewer.close()

    def test_foundation_file_selectors_follow_loaded_datasets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "vib_measurement_with_a_long_file_name.csv"
            second = Path(tmpdir) / "stiff_measurement_with_a_long_file_name.csv"
            first.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
            second.write_text("0,4\n0.1,5\n0.2,6\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_paths([first, second])
                self.assertEqual(viewer.foundation_vib_file_combo.count(), 2)
                self.assertEqual(viewer.foundation_stiff_file_combo.count(), 2)
                self.assertEqual(viewer.foundation_vib_file_combo.currentData(), 1)
                self.assertEqual(viewer.foundation_stiff_file_combo.currentData(), 2)
                self.assertFalse(hasattr(viewer, "foundation_ref_edit"))
                self.assertEqual(viewer.foundation_resp_edit.text(), "4")
                viewer.foundation_resp_edit.setText("2,3")
                viewer.foundation_stiff_file_combo.setCurrentIndex(0)
                self.assertEqual(viewer.foundation_resp_edit.text(), "2,3")

                self.assertGreaterEqual(viewer.foundation_vib_file_combo.minimumWidth(), 180)
                self.assertGreaterEqual(viewer.foundation_vib_file_combo.maximumWidth(), 300)
                self.assertIn("vib_measurement_with_a_long_file_name", viewer.foundation_vib_file_combo.toolTip())
                self.assertIn(
                    "stiff_measurement_with_a_long_file_name",
                    viewer.foundation_stiff_file_combo.itemData(1, QtCore.Qt.ToolTipRole),
                )
                self.assertGreaterEqual(viewer.foundation_vib_edit.minimumWidth(), 82)
            finally:
                viewer.close()

    def test_analysis_defaults_and_labels_match_requested_layout(self):
        viewer = AnalysisViewer()
        try:
            self.assertEqual(viewer.main_mode_combos[2].currentText(), "Trans")
            self.assertEqual(viewer.export_button.text(), "导出数据")
            self.assertEqual(viewer.hold_button.text(), "保持:关")
            foundation_labels = [
                label.text()
                for label in viewer.foundation_tab.findChildren(QtWidgets.QLabel)
            ]
            self.assertIn("振动文件", foundation_labels)
            self.assertIn("动刚度文件", foundation_labels)
            self.assertEqual(viewer._foundation_reference_channel(), 1)
            self.assertEqual(viewer.tabs.tabText(0), "主界面")
            self.assertEqual(viewer.tabs.tabText(1), "地面振动")
            self.assertEqual(viewer.tabs.tabText(2), "换算")
            self.assertEqual(len(viewer.main_open_buttons), 3)
            self.assertEqual(len(viewer.foundation_export_buttons), 3)
            self.assertEqual(len(viewer.derived_export_buttons), 2)
            self.assertLessEqual(viewer.width(), 980)
            self.assertLessEqual(viewer.left_panel.maximumWidth(), 285)
            foundation_controls = viewer.foundation_tab.layout().itemAt(0).layout()
            self.assertEqual(foundation_controls.count(), 2)
            self.assertEqual(viewer.derived_result_mode_combo.currentText(), "PSD")
            self.assertEqual(viewer.derived_direction_combo.currentData(), DERIVE_BASE_TO_TOP)
            self.assertAlmostEqual(viewer.derived_transfer_factor_spin.value(), 1.0)
            self.assertAlmostEqual(viewer.derived_input_factor_spin.value(), 1.0)
            self.assertEqual(viewer.derived_transfer_factor_spin.decimals(), 1)
            self.assertEqual(viewer.derived_input_factor_spin.decimals(), 1)
            self.assertFalse(any(checkbox.isChecked() for checkbox in viewer.derived_vc_checks.values()))
            self.assertFalse(viewer.derived_show_source_check.isChecked())
            self.assertFalse(viewer.derived_coherence_correction_check.isChecked())
        finally:
            viewer.close()

    def test_standalone_conversion_viewer_uses_conversion_only_layout(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            self.assertEqual(viewer.windowTitle(), "VNA 换算工具")
            self.assertIsNone(viewer.tabs)
            self.assertFalse(hasattr(viewer, "main_plots"))
            self.assertFalse(hasattr(viewer, "foundation_plots"))
            self.assertEqual(viewer.derived_config_button.text(), "数据配置")
            self.assertEqual(viewer.derived_plot_button.text(), "应用")
            self.assertIsNone(viewer.clear_button.parentWidget())
            self.assertIsNotNone(viewer.derived_config_button.parentWidget())
            self.assertFalse(hasattr(viewer, "derived_curve_button"))
            self.assertIsNone(viewer.derived_curve_dialog)
            self.assertIs(viewer.left_layout.itemAt(1).widget(), viewer.derived_curve_group)
            self.assertIs(viewer.derived_curve_group.parentWidget(), viewer.left_panel)
            self.assertFalse(viewer._hidden_series_group.isVisible())
            self.assertIsNone(viewer.fs_hint_spin.parentWidget())
            self.assertEqual(viewer.derived_transfer_point_table.maximumHeight(), 420)
            self.assertEqual(viewer.derived_config_dataset_list.count(), 1)
            self.assertFalse(viewer.derived_config_dialog.isVisible())
            viewer._show_derived_config_dialog()
            self.assertTrue(viewer.derived_config_dialog.isVisible())
            viewer.derived_config_dialog.hide()
            self.assertGreaterEqual(
                viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",)),
                0,
            )
            self.assertGreaterEqual(
                viewer._combo_index_for_data(viewer.derived_input_series_combo, ("vc_reference", "VC C")),
                0,
            )
        finally:
            viewer.close()

    def test_manual_transfer_psd_edit_and_stitch_ui_paths(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 40.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        input_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {"ai0": np.array([0.0, 1.0, 1.0, 1.0]), "ai1": np.array([0.0, 9.0, 9.0, 9.0])}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(input_measurement, session_config=session, dataset_id=1, name="input"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            input_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, "1:ai0")
            stitch_index = viewer._combo_index_for_data(viewer.derived_stitch_series_combo, "1:ai1")
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer.derived_input_series_combo.setCurrentIndex(input_index)
            viewer._set_current_transfer_control_points(
                np.array([10.0, 40.0], dtype=float),
                np.array([6.0, 6.0], dtype=float),
                replot=False,
            )

            viewer._plot_derived()
            psd_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(psd_curves), 0)
            label, (before_f, before_psd) = next(iter(psd_curves.items()))
            self.assertAlmostEqual(float(np.median(before_psd)), 10.0 ** (6.0 / 10.0), delta=0.05)

            viewer._active_trace[viewer.derived_plots[1]] = label
            viewer._initialize_psd_edit_points_from_active_curve()
            self.assertIn(label, viewer._psd_edit_points)

            viewer.derived_stitch_enabled_check.setChecked(True)
            viewer.derived_stitch_series_combo.setCurrentIndex(stitch_index)
            viewer.derived_stitch_split_edit.setText("20")
            viewer._plot_derived()
            stitched_labels = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any("拼合@20Hz" in curve_label for curve_label in stitched_labels))
            viewer._refresh_config_dataset_list()
            viewer.derived_config_dataset_list.item(0).setSelected(True)
            viewer._delete_selected_config_datasets()
            self.assertEqual(viewer._datasets, [])
            self.assertEqual(viewer.derived_config_dataset_list.item(0).text(), "(暂无已加载数据)")
            _ = before_f
        finally:
            viewer.close()

    def test_derived_tab_converts_loaded_transfer_and_input_data(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        transfer_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": 2.0 * np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {}},
            frf={"ai0->ai1": np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)},
            coherence={"ai0->ai1": np.full(freqs.shape, 0.25, dtype=float)},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0},
        )
        input_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {"ai0": np.sin(2.0 * np.pi * 10.0 * time_s)},
            },
            spectra={"f": freqs, "autospectrum": {"ai0": np.array([0.0, 1.0, 1.0, 1.0])}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer()
        try:
            viewer._datasets = [
                dataset_from_measurement(transfer_measurement, session_config=session, dataset_id=1, name="transfer"),
                dataset_from_measurement(input_measurement, session_config=session, dataset_id=2, name="input"),
            ]
            viewer._next_dataset_id = 3
            viewer._refresh_dataset_lists()
            input_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, "2:ai0")
            viewer.derived_input_series_combo.setCurrentIndex(input_index)

            viewer._plot_derived()

            transfer_curves = viewer._plot_curves[viewer.derived_plots[0]]
            psd_curves = viewer._plot_curves[viewer.derived_plots[1]]
            transfer_data = viewer.derived_transfer_combo.currentData()
            self.assertEqual(transfer_data[0], 1)
            self.assertEqual(transfer_data[1], "ai0->ai1")
            self.assertIn("ai0->ai1", viewer.derived_transfer_combo.currentText())
            self.assertGreater(len(transfer_curves), 0)
            self.assertGreater(len(psd_curves), 0)
            _label, (f, psd) = next(iter(psd_curves.items()))
            np.testing.assert_allclose(f, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd, [4.0, 4.0, 4.0])

            viewer.derived_coherence_correction_check.setChecked(True)
            viewer._plot_derived()
            corrected_curves = viewer._plot_curves[viewer.derived_plots[1]]
            _label, (f_corrected, psd_corrected) = next(iter(corrected_curves.items()))
            np.testing.assert_allclose(f_corrected, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd_corrected, [16.0, 16.0, 16.0])
            viewer.derived_coherence_correction_check.setChecked(False)

            viewer.scale_spin.setValue(10.0)
            viewer.derived_transfer_factor_spin.setValue(0.5)
            viewer.derived_input_factor_spin.setValue(3.0)
            viewer._plot_derived()
            factored_curves = viewer._plot_curves[viewer.derived_plots[1]]
            _label, (f_factored, psd_factored) = next(iter(factored_curves.items()))
            np.testing.assert_allclose(f_factored, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd_factored, [9.0, 9.0, 9.0])

            factored_curve_count = len(factored_curves)
            viewer.hold_button.setChecked(True)
            viewer.derived_input_factor_spin.setValue(4.0)
            viewer._auto_plot_derived_from_control_change()
            held_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(held_curves), factored_curve_count)
            viewer.hold_button.setChecked(False)

            viewer.derived_show_source_check.setChecked(True)
            viewer._plot_derived()
            psd_with_source = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any(label.startswith("待换算:") for label in psd_with_source))

            viewer.derived_result_mode_combo.setCurrentText("近似时域")
            viewer._plot_derived()
            time_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(time_curves), 0)

            viewer.derived_result_mode_combo.setCurrentText("地基振动")
            viewer.derived_vc_checks["VC A"].setChecked(True)
            viewer._plot_derived()
            foundation_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertIn("VC A", foundation_curves)
            self.assertIn("VC A", viewer._plot_export_excluded[viewer.derived_plots[1]])

            vc_c_index = viewer._combo_index_for_data(
                viewer.derived_input_series_combo,
                ("vc_reference", "VC C"),
            )
            self.assertGreaterEqual(vc_c_index, 0)
            vc_frequencies, vc_velocity = _vc_reference_frequency_velocity("VC C")
            self.assertAlmostEqual(vc_frequencies[0], 1.0)
            self.assertAlmostEqual(vc_frequencies[-1], 80.0)
            self.assertGreater(vc_frequencies.size, 10)
            np.testing.assert_allclose(vc_velocity, np.full_like(vc_velocity, 12.5))

            viewer.derived_input_series_combo.setCurrentIndex(vc_c_index)
            viewer.derived_result_mode_combo.setCurrentText("地基振动")
            viewer._plot_derived()
            vc_derived_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any(label.startswith("VC C") for label in vc_derived_curves))
        finally:
            viewer.close()

    def test_hold_and_selection_buttons_show_feedback(self):
        viewer = AnalysisViewer()
        try:
            viewer.hold_button.setChecked(True)
            self.assertEqual(viewer.hold_button.text(), "保持:开")
            self.assertIn("保持已开启", viewer.statusBar().currentMessage())

            viewer.hold_button.setChecked(False)
            self.assertEqual(viewer.hold_button.text(), "保持:关")
            self.assertIn("保持已关闭", viewer.statusBar().currentMessage())

            viewer.series_list.addItem("one")
            viewer.series_list.addItem("two")
            for index in range(viewer.series_list.count()):
                viewer.series_list.item(index).setData(QtCore.Qt.UserRole, index)
            viewer._select_all_series()
            self.assertIn("已选择 2 个通道", viewer.statusBar().currentMessage())
            viewer._select_no_series()
            self.assertIn("已取消选择所有通道", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_axis_history_range_tracking_does_not_raise(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            viewer._remember_axis_range(plot)
            viewer._remember_axis_range(plot)
            self.assertEqual(len(viewer._axis_history[plot]), 1)
        finally:
            viewer.close()

    def test_log_auto_range_handles_flat_small_values_without_huge_floor(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            x = np.array([10.0, 20.0, 40.0, 80.0], dtype=float)
            y = np.full(4, 2.0e-8, dtype=float)

            viewer._auto_range_plot(plot, [x], [y], log_x=True, log_y=True)

            x_range, y_range = plot.viewRange()
            self.assertGreater(x_range[0], 0.9)
            self.assertLess(x_range[1], 2.0)
            self.assertGreater(y_range[0], -9.0)
            self.assertLess(y_range[1], -7.0)
        finally:
            viewer.close()

    def test_option_changes_replot_selected_data_and_spinboxes_have_no_arrows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,3\n0.3,4\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                before = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.main_plots[0].clear()
                viewer.quantity_combo.setCurrentText("Velocity")
                after = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.main_plots[0].clear()
                viewer.fs_hint_spin.setValue(8192.0)
                after_fft_block_value_change = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.fs_hint_spin.editingFinished.emit()
                after_fft_block_editing_finished = len(viewer.main_plots[0].plotItem.listDataItems())
                self.assertGreater(before, 0)
                self.assertGreater(after, 0)
                self.assertEqual(after_fft_block_value_change, 0)
                self.assertGreater(after_fft_block_editing_finished, 0)
                for spin in (
                    viewer.scale_spin,
                    viewer.lowpass_spin,
                    viewer.highpass_spin,
                    viewer.filter_order_spin,
                ):
                    self.assertEqual(spin.buttonSymbols(), QtWidgets.QAbstractSpinBox.NoButtons)
                self.assertEqual(viewer.fs_hint_spin.suffix(), "")
            finally:
                viewer.close()

    def test_refresh_button_reloads_files_and_current_measurement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")
            session = default_session_config()
            measurement = MeasurementSet(
                sample_rate=100.0,
                time_data={
                    "t": np.array([0.0, 0.1], dtype=float),
                    "channels": {"ai0": np.array([4.0, 5.0], dtype=float)},
                },
                spectra={"f": np.array([], dtype=float), "autospectrum": {}},
                frf={},
                coherence={},
                cross_spectra={},
                correlations={},
                impulse_responses={},
                metadata={},
            )
            viewer = AnalysisViewer(
                current_measurement_provider=lambda: (measurement, session)
            )
            try:
                viewer._load_path(path)
                path.write_text("0,10,20\n0.1,30,40\n0.2,50,60\n", encoding="utf-8")

                viewer.refresh_data_sources()

                self.assertEqual(len(viewer._datasets), 2)
                current = viewer._datasets[0]
                reloaded = viewer._datasets[1]
                self.assertEqual(current.name, "Current Measurement")
                _time_s, values = reloaded.load_time_series(reloaded.series[0].channel_key)
                np.testing.assert_allclose(values, [10.0, 30.0, 50.0])
                self.assertEqual(viewer.refresh_button.text(), "刷新")
            finally:
                viewer.close()

    def test_rename_and_factor_follow_matlab_analysis_panel_behavior(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                self.assertEqual(viewer.rename_edit.text(), "data.csv+ch1")
                viewer.rename_edit.setText("hammer")
                viewer._rename_selected_series_from_editor()
                self.assertEqual(viewer.series_list.item(0).text(), "hammer")

                viewer.factor_edit.setText("2.5")
                viewer._set_selected_series_scale_from_editor()
                self.assertAlmostEqual(viewer._datasets[0].series[0].scale, 2.5)
                self.assertEqual(viewer.factor_edit.text(), "2.5")
                self.assertEqual(viewer.rename_edit.text(), "hammer")
                self.assertIn("(*2.5)", viewer.series_list.item(0).text())
            finally:
                viewer.close()

    def test_factor_suffix_uses_relative_to_loaded_scale(self):
        viewer = AnalysisViewer()
        try:
            dataset = AnalysisDataset(
                id=1,
                path=Path("scaled.csv"),
                name="scaled.csv",
                sample_rate=100.0,
                series=[
                    AnalysisSeries(
                        dataset_id=1,
                        channel_index=0,
                        channel_key="ch1",
                        display_name="ch1",
                        scale=2.0,
                    )
                ],
                time_s=np.array([0.0, 0.1], dtype=float),
                channels={"ch1": np.array([1.0, 2.0], dtype=float)},
            )
            viewer._datasets = [dataset]
            viewer._refresh_dataset_lists()
            viewer.series_list.item(0).setSelected(True)
            viewer.factor_edit.setText("4")
            viewer._set_selected_series_scale_from_editor()

            self.assertAlmostEqual(viewer._datasets[0].series[0].scale, 4.0)
            self.assertIn("(*2)", viewer.series_list.item(0).text())
        finally:
            viewer.close()


if __name__ == "__main__":
    unittest.main()
