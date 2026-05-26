from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from python_vna.analysis_algorithms import (
    compute_cumulative_spectrum,
    compute_dynamic_stiffness,
    compute_transfer_function_welch,
    compute_welch_psd,
    compute_periodogram_psd,
    compute_third_octave_velocity_rms,
    convert_acceleration_psd,
    convert_acceleration_time_series,
)
from python_vna.analysis_data import dataset_from_measurement, load_analysis_path, load_continuous_channels
from python_vna.continuous_recording import ContinuousDatWriter
from python_vna.daq.base import BackendFrame
from python_vna.models import MeasurementSet
from python_vna.storage import default_session_config
from python_vna.ui.analysis_viewer import AnalysisViewer
from python_vna.ui.main_window import DataTipText, VnaAxisItem


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
                self.assertEqual(viewer.dataset_list.count(), 1)
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
                self.assertEqual(viewer.dataset_list.count(), 2)
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
                self.assertEqual(viewer.dataset_list.count(), 1)
                self.assertIn("failed 1", viewer.statusBar().currentMessage())
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
            self.assertEqual(viewer.dataset_list.count(), 1)
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
            self.assertEqual(viewer.export_button.text(), "Export")
            self.assertEqual(viewer._foundation_reference_channel(), 1)
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

                self.assertEqual(viewer.dataset_list.count(), 2)
                current = viewer._datasets[0]
                reloaded = viewer._datasets[1]
                self.assertEqual(current.name, "Current Measurement")
                _time_s, values = reloaded.load_time_series(reloaded.series[0].channel_key)
                np.testing.assert_allclose(values, [10.0, 30.0, 50.0])
                self.assertEqual(viewer.refresh_button.text(), "Refresh")
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
            finally:
                viewer.close()


if __name__ == "__main__":
    unittest.main()
