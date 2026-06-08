from __future__ import annotations

import os
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from python_vna.diagnostic.app import parse_args
from python_vna.diagnostic.data import (
    CurvePair,
    curve_pairs_from_table,
    load_numeric_table,
    load_trace_analysis_file,
    load_vibration_analysis_file,
)
from python_vna.diagnostic.pages import (
    Modal3DView,
    ModalShapePage,
    TraceAnalysisPage,
    VibrationAnalysisPage,
    fallback_nearest_auto_edges,
    read_xlsx_rows_basic,
    render_mode_animation_frames,
)
from python_vna.diagnostic.shell import DiagnosticMainWindow
from python_vna.models import ChannelConfig, MeasurementSet, SavedSession
from python_vna.storage import default_session_config, save_legacy_vna
from python_vna.ui.analysis_viewer import AnalysisWorkbench, AnalysisViewer
from python_vna.ui.main_window import DataTipPoint, VnaViewBox


class DiagnosticAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_diagnostic_app_accepts_startup_paths(self):
        args = parse_args(["one.vna", "trace.csv"])

        self.assertEqual(args.paths, ["one.vna", "trace.csv"])

    def test_analysis_viewer_is_still_top_level_compatible_workbench(self):
        viewer = AnalysisViewer()

        self.assertIsInstance(viewer, AnalysisWorkbench)
        self.assertEqual(viewer.statusBar().currentMessage(), "Ready")

    def test_diagnostic_shell_builds_expected_pages(self):
        window = DiagnosticMainWindow()

        self.assertEqual(window.stack.count(), 4)
        self.assertEqual(window.page_titles(), ["VNA数据分析", "上位机数据分析", "减振器软件测试数据分析", "模态振型"])
        self.assertIs(window.stack.currentWidget(), window.analysis_page)
        window.nav_list.setCurrentRow(2)
        self.assertIs(window.stack.currentWidget(), window.trace_page)

    def test_numeric_table_parser_reads_matlab_style_curve_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PSD.dat"
            path.write_text(
                "006_ch2_X\t006_ch2_Y\n"
                "1.0\t2.0\n"
                "2.0\t4.0\n",
                encoding="utf-8",
            )

            table = load_numeric_table(path)
            pairs = curve_pairs_from_table(table)

        self.assertEqual(table.headers, ["006_ch2_X", "006_ch2_Y"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].label, "006_ch2")
        np.testing.assert_allclose(pairs[0].y, [2.0, 4.0])

    def test_vibration_page_loads_and_plots_frequency_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PSD.dat"
            path.write_text(
                "FRF_Z_X\tFRF_Z_Y\n"
                "1.0\t2.0\n"
                "2.0\t6.0\n"
                "3.0\t4.0\n",
                encoding="utf-8",
            )
            page = VibrationAnalysisPage()

            page.load_paths([path])
            page.plot_current()

        self.assertEqual(len(page.files), 1)
        self.assertEqual(page.frequency_pair_list.count(), 1)
        self.assertIn("FRF_Z", page._plot_curves[page.frequency_plot])

    def test_diagnostic_2d_plots_use_vna_marker_interactions(self):
        vibration = VibrationAnalysisPage()
        trace = TraceAnalysisPage()
        modal = ModalShapePage()

        plots = [
            vibration.frequency_plot,
            vibration.log_plot,
            trace.ide_time_plot,
            trace.ide_psd_plot,
            trace.hac_plot,
            modal.frf_plot,
        ]

        for plot in plots:
            self.assertIsInstance(plot.getPlotItem().vb, VnaViewBox)
            self.assertIn(plot, vibration._cursor_items | trace._cursor_items | modal._cursor_items)

    def test_diagnostic_plot_cursor_and_data_tip_snap_to_curve_points(self):
        page = VibrationAnalysisPage()
        page._plot_curves_on_widget(
            page.frequency_plot,
            [CurvePair("FRF_Z", np.array([1.0, 2.0, 3.0]), np.array([2.0, 6.0, 4.0]))],
            title="测试曲线",
            x_label="频率 (Hz)",
            y_label="幅值",
            log_x=False,
        )

        snapped = page._nearest_curve_point_2d(page.frequency_plot, 2.1, 5.8)
        self.assertEqual(snapped, (2.0, 6.0, "FRF_Z"))
        self.assertTrue(page._set_cursor_position(page.frequency_plot, 2.0, 6.0, "FRF_Z"))
        self.assertEqual(page._cursor_positions[page.frequency_plot], (2.0, 6.0))
        self.assertTrue(page._place_data_tip(page.frequency_plot, 2.1, 5.8))
        self.assertIsInstance(page._data_tip_items[page.frequency_plot][0]["point"], DataTipPoint)

    def test_trace_page_loads_and_plots_time_and_psd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            values = "\n".join(f"{i / 100.0:.3f}\t{np.sin(i / 4.0):.6f}" for i in range(64))
            path.write_text("Time\tACC_1\n" + values + "\n", encoding="utf-8")
            parsed = load_trace_analysis_file(path)
            page = TraceAnalysisPage()

            page.load_paths([path])
            page.plot_current()

        self.assertEqual(parsed.trace_kind, "ide_trace")
        self.assertIn("ACC_1", parsed.channels)
        self.assertIn("ACC_1", page._plot_curves[page.ide_time_plot])
        self.assertIn("ACC_1", page._plot_curves[page.ide_psd_plot])

    def test_trace_ide_parser_matches_matlab_header_eu_and_psd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ide.txt"
            rows = "\n".join(f"{i + 1:.6f} {np.sin(i / 3.0) + 2.0:.6f}" for i in range(16))
            path.write_text(
                "sample frequency: 1000\n"
                "undersample: 2\n"
                "signal num: 2\n"
                "Buffer length: 16\n"
                "X_PROX;Y_ACC\n"
                f"{rows}\n",
                encoding="utf-8",
            )
            parsed = load_trace_analysis_file(path)
            page = TraceAnalysisPage()

            page.load_paths([path])
            curves = page._selected_time_curves(parsed)
            psd_curves = page._psd_curves(parsed, curves)

        self.assertEqual(parsed.trace_kind, "ide_trace")
        self.assertAlmostEqual(parsed.sample_rate, 500.0)
        self.assertAlmostEqual(parsed.channel_eu["X_PROX"], 3.75)
        self.assertAlmostEqual(curves[0].y[0], 1.0 / 3.75)
        self.assertTrue(psd_curves)
        self.assertGreaterEqual(psd_curves[0].x[0], 3 * parsed.sample_rate / 16)

    def test_trace_hac_parser_uses_period_elapsed_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hac.csv"
            path.write_text(
                "Period(ms),10\n"
                "time,位移1,速度1\n"
                "bad_time,1,2\n"
                "bad_time2,3,4\n",
                encoding="utf-8",
            )

            parsed = load_trace_analysis_file(path)

        self.assertEqual(parsed.trace_kind, "hac_trace")
        self.assertAlmostEqual(parsed.sample_rate, 100.0)
        np.testing.assert_allclose(parsed.time_s, [0.0, 0.01])

    def test_vibration_frequency_parser_computes_matlab_tfestimate_magnitude(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IVHFA_demo.dat"
            headers = " ".join(f"Pair{i}_{suffix}" for i in range(1, 7) for suffix in ("in", "out"))
            t = np.arange(96, dtype=float)
            x = np.sin(2 * np.pi * t / 16.0)
            lines = ["Update: 1", headers]
            for value in x:
                row = []
                for _pair in range(6):
                    row.extend([value, 2.0 * value])
                lines.append(" ".join(f"{item:.8f}" for item in row))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            parsed = load_vibration_analysis_file(path)

        self.assertEqual(parsed.table.kind, "frequency")
        self.assertEqual(len(parsed.frequency_pairs), 6)
        self.assertAlmostEqual(float(np.nanmedian(parsed.frequency_pairs[0].y)), 20.0 * np.log10(2.0), places=1)

    def test_vibration_parser_builds_log_groups_for_wide_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.csv"
            path.write_text("Time,ACC_X,ACC_Y,DISP_X\n0,1,2,3\n1,4,5,6\n", encoding="utf-8")

            parsed = load_vibration_analysis_file(path)

        self.assertIn("All Channels", parsed.log_groups)

    def test_vibration_log_auto_prefix_groups_match_matlab_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.csv"
            path.write_text(
                "Time,VEL_BF_X,VEL_BF_Y,VEL_SF_X\n"
                "0,1,2,3\n"
                "1,4,5,6\n",
                encoding="utf-8",
            )

            parsed = load_vibration_analysis_file(path)

        self.assertEqual(parsed.log_groups["BF Velocity"], [0, 1])
        self.assertEqual(parsed.log_groups["Auto VEL_BF"], [0, 1])
        self.assertIn("All Channels", parsed.log_groups)

    def test_vibration_log_groups_match_matlab_named_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wide_log.csv"
            path.write_text(
                "Date,Time,VEL_BF_X,ACC_BF_Y,PROX_1,PS_POS_X,VALUE1_A,MT_TM_1\n"
                "2026-01-01,00:00:00,1,2,3,4,5,6\n"
                "2026-01-01,00:00:01,2,3,4,5,6,7\n",
                encoding="utf-8",
            )

            parsed = load_vibration_analysis_file(path)

        self.assertEqual(parsed.log_groups["BF Velocity"], [0, 1])
        self.assertEqual(parsed.log_groups["PROX Position"], [2])
        self.assertIn("Valve Output 1", parsed.log_groups)
        self.assertIn("MT Temperature", parsed.log_groups)
        self.assertIn("All Channels", parsed.log_groups)

    def test_vibration_log_groups_tolerate_units_spaces_and_matlab_wide_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wide_log.txt"
            path.write_text(
                "Date Time VEL BF X(mm/s) ACC BF Y PROX 1 VALUE 1 A OUTX1(N) TEMP A\n"
                "===============================================================\n"
                "2026-01-01 00:00:00 1 2 3 4 5 6\n"
                "2026-01-01 00:00:01 2 3 4 5 6 7\n",
                encoding="utf-8",
            )

            parsed = load_vibration_analysis_file(path)

        self.assertEqual(parsed.table.kind, "log")
        self.assertIn("BF Velocity", parsed.log_groups)
        self.assertIn("PROX Position", parsed.log_groups)
        self.assertIn("Valve Output 1", parsed.log_groups)
        self.assertIn("OUT Force", parsed.log_groups)
        self.assertIn("TEMP", parsed.log_groups)

    def test_vibration_log_groups_show_legacy_profiles_for_34_column_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy34.txt"
            headers = " ".join(f"CH{i:02d}" for i in range(1, 35))
            row1 = " ".join(str(i) for i in range(1, 35))
            row2 = " ".join(str(i + 1) for i in range(1, 35))
            path.write_text(
                f"Date Time {headers}\n"
                f"2026-01-01 00:00:00 {row1}\n"
                f"2026-01-01 00:00:01 {row2}\n",
                encoding="utf-8",
            )

            parsed = load_vibration_analysis_file(path)

        self.assertIn("Legacy Floor FF", parsed.log_groups)
        self.assertIn("Legacy Motor Temperature", parsed.log_groups)
        self.assertIn("Auto CH", parsed.log_groups)
        self.assertIn("All Channels", parsed.log_groups)

    def test_vibration_legacy_log_channels_use_matlab_display_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy34.txt"
            headers = " ".join(f"CH{i:02d}" for i in range(1, 35))
            row1 = " ".join(str(i) for i in range(1, 35))
            row2 = " ".join(str(i + 1) for i in range(1, 35))
            path.write_text(
                f"Date Time {headers}\n"
                f"2026-01-01 00:00:00 {row1}\n"
                f"2026-01-01 00:00:01 {row2}\n",
                encoding="utf-8",
            )
            page = VibrationAnalysisPage()

            page.load_paths([path])
            page.log_group_combo.setCurrentText("Legacy Floor FF")
            page._refresh_log_channels()
            curves = page._selected_log_curves(page.current_file())

        self.assertEqual(page.files[0].log_group_labels["Legacy Floor FF"], ["XFF", "YFF", "ZFF"])
        self.assertEqual([page.log_channel_list.item(i).text() for i in range(3)], ["XFF", "YFF", "ZFF"])
        self.assertEqual([curve.label for curve in curves], ["XFF", "YFF", "ZFF"])

    def test_vibration_page_does_not_auto_plot_on_load_but_updates_after_parameter_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "log.csv"
            log_path.write_text("Time,ACC_X,ACC_Y,DISP_X\n0,1,2,3\n1,4,5,6\n", encoding="utf-8")
            page = VibrationAnalysisPage()

            page.load_paths([log_path])

        self.assertIs(page.tabs.currentWidget(), page.log_plot)
        self.assertEqual(page._plot_curves[page.log_plot], {})
        page.plot_current()
        self.assertIn("ACC_X", page._plot_curves[page.log_plot])
        page.demean_check.setChecked(True)
        y = page._plot_curves[page.log_plot]["ACC_X"][1]
        self.assertAlmostEqual(float(np.nanmean(y)), 0.0)

    def test_vibration_operation_toggle_uses_button_style(self):
        page = VibrationAnalysisPage()

        self.assertIsInstance(page.demean_check, QtWidgets.QPushButton)
        self.assertIsInstance(page.hold_check, QtWidgets.QPushButton)
        self.assertTrue(page.demean_check.isCheckable())
        self.assertTrue(page.hold_check.isCheckable())
        self.assertEqual(page.demean_check.property("role"), "secondary")
        self.assertEqual(page.hold_check.property("role"), "secondary")

    def test_vibration_page_hold_overlay_keeps_existing_curves(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "log.csv"
            log_path.write_text("Time,ACC_X,ACC_Y\n0,1,10\n1,4,20\n", encoding="utf-8")
            page = VibrationAnalysisPage()

            page.load_paths([log_path])
            page.plot_current()
            page.hold_check.setChecked(True)
            page.log_channel_list.item(1).setSelected(False)
            page.plot_current()

        self.assertIn("ACC_X", page._plot_curves[page.log_plot])
        self.assertIn("ACC_Y", page._plot_curves[page.log_plot])
        self.assertIn("ACC_X (2)", page._plot_curves[page.log_plot])

    def test_vibration_page_file_switch_preserves_selected_log_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "log_a.csv"
            path_b = Path(tmp) / "log_b.csv"
            path_a.write_text("Time,ACC_X,ACC_Y\n0,1,10\n1,4,20\n", encoding="utf-8")
            path_b.write_text("Time,ACC_X,ACC_Y\n0,2,30\n1,5,40\n", encoding="utf-8")
            page = VibrationAnalysisPage()

            page.load_paths([path_a, path_b])
            page.plot_current()
            page.hold_check.setChecked(True)
            page.log_channel_list.item(1).setSelected(False)
            page.file_list.setCurrentRow(1)

        self.assertIn("ACC_X", page._plot_curves[page.log_plot])
        self.assertIn("ACC_Y", page._plot_curves[page.log_plot])
        self.assertIn("ACC_X (2)", page._plot_curves[page.log_plot])
        self.assertNotIn("ACC_Y (2)", page._plot_curves[page.log_plot])

    def test_vibration_page_file_switch_without_matching_channel_uses_first_channel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "log_a.csv"
            path_b = Path(tmp) / "log_b.csv"
            path_a.write_text("Time,ACC_X,ACC_Y\n0,1,10\n1,4,20\n", encoding="utf-8")
            path_b.write_text("Time,VEL_X,VEL_Y\n0,2,30\n1,5,40\n", encoding="utf-8")
            page = VibrationAnalysisPage()

            page.load_paths([path_a, path_b])
            page.plot_current()
            page.hold_check.setChecked(True)
            page.log_channel_list.item(1).setSelected(False)
            page.file_list.setCurrentRow(1)

        self.assertIn("ACC_X", page._plot_curves[page.log_plot])
        self.assertIn("ACC_Y", page._plot_curves[page.log_plot])
        self.assertIn("VEL_X", page._plot_curves[page.log_plot])
        self.assertNotIn("VEL_Y", page._plot_curves[page.log_plot])

    def test_vibration_page_subplot_mode_disables_hold(self):
        page = VibrationAnalysisPage()

        page.hold_check.setChecked(True)
        page.plot_mode_combo.setCurrentIndex(1)

        self.assertFalse(page.hold_check.isChecked())
        self.assertFalse(page.hold_check.isEnabled())

    def test_vibration_page_subplot_mode_separates_selected_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "log.csv"
            log_path.write_text("Time,ACC_X,ACC_Y,DISP_X\n0,1,10,100\n1,4,20,200\n", encoding="utf-8")
            page = VibrationAnalysisPage()

            page.load_paths([log_path])
            page.log_group_combo.setCurrentText("All Channels")
            page.plot_mode_combo.setCurrentIndex(1)

        plotted = page._plot_curves[page.log_plot]
        self.assertEqual(set(plotted), {"ACC_X", "ACC_Y", "DISP_X"})
        self.assertEqual(page.log_plot.getAxis("left").labelText, "Subplots")
        for _name, (_x, y) in plotted.items():
            self.assertGreaterEqual(float(np.nanmin(y)), 0.0)
            self.assertLessEqual(float(np.nanmax(y)), len(plotted))

    def test_trace_page_does_not_auto_plot_on_load_but_updates_after_parameter_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            values = "\n".join(f"{i / 100.0:.3f}\t{np.sin(i / 4.0):.6f}" for i in range(64))
            path.write_text("Time\tACC_1\n" + values + "\n", encoding="utf-8")
            page = TraceAnalysisPage()

            page.load_paths([path])
            self.assertEqual(page._plot_curves[page.ide_time_plot], {})
            self.assertEqual(page._plot_curves[page.ide_psd_plot], {})
            page.plot_current()
            before = page._plot_curves[page.ide_time_plot]["ACC_1"][0].size
            page.range_end.setValue(16)
            after = page._plot_curves[page.ide_time_plot]["ACC_1"][0].size

        self.assertIn("ACC_1", page._plot_curves[page.ide_time_plot])
        self.assertIn("ACC_1", page._plot_curves[page.ide_psd_plot])
        self.assertLess(after, before)

    def test_trace_page_subplot_mode_applies_to_time_and_psd_plots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            values = "\n".join(
                f"{i / 100.0:.3f}\t{np.sin(i / 4.0):.6f}\t{np.cos(i / 5.0):.6f}" for i in range(64)
            )
            path.write_text("Time\tACC_1\tACC_2\n" + values + "\n", encoding="utf-8")
            page = TraceAnalysisPage()

            page.load_paths([path])
            page.plot_mode_combo.setCurrentIndex(1)

        self.assertEqual(page.ide_time_plot.getAxis("left").labelText, "Subplots")
        self.assertEqual(page.ide_psd_plot.getAxis("left").labelText, "Subplots")
        self.assertEqual(page._log_modes[page.ide_psd_plot], (True, False))
        for plot in (page.ide_time_plot, page.ide_psd_plot):
            plotted = page._plot_curves[plot]
            self.assertEqual(set(plotted), {"ACC_1", "ACC_2"})
            for _name, (_x, y) in plotted.items():
                self.assertGreaterEqual(float(np.nanmin(y)), 0.0)
                self.assertLessEqual(float(np.nanmax(y)), len(plotted))

    def test_trace_operation_toggles_use_button_style(self):
        page = TraceAnalysisPage()

        self.assertIsInstance(page.demean_check, QtWidgets.QPushButton)
        self.assertIsInstance(page.hold_check, QtWidgets.QPushButton)
        self.assertTrue(page.demean_check.isCheckable())
        self.assertTrue(page.hold_check.isCheckable())
        self.assertEqual(page.demean_check.property("role"), "secondary")
        self.assertEqual(page.hold_check.property("role"), "secondary")

    def test_trace_page_ide_suffix_eu_matches_matlab_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ide_suffix.txt"
            rows = "\n".join(f"{10 + i:.6f} {20 + i:.6f} {30 + i:.6f} {40 + i:.6f}" for i in range(32))
            path.write_text(
                "sample frequency: 1000\n"
                "undersample: 1\n"
                "signal num: 4\n"
                "Buffer length: 32\n"
                "X_PROX;Y_FB;Z_ACC;Q_POS\n"
                f"{rows}\n",
                encoding="utf-8",
            )
            page = TraceAnalysisPage()

            page.load_paths([path])
            page.ide_suffix_edits["Prox"].setText("2")
            page.ide_suffix_edits["FB"].setText("4")
            page.ide_suffix_edits["ACC"].setText("5")
            page.ide_suffix_edits["POS"].setText("8")
            page.ide_suffix_apply_button.click()

        eu_values = {
            page.ide_eu_table.item(row, 0).text(): float(page.ide_eu_table.item(row, 1).text())
            for row in range(page.ide_eu_table.rowCount())
        }
        self.assertEqual(eu_values, {"X_PROX": 2.0, "Y_FB": 4.0, "Z_ACC": 5.0, "Q_POS": 8.0})
        self.assertAlmostEqual(page._plot_curves[page.ide_time_plot]["X_PROX"][1][0], 5.0)
        self.assertAlmostEqual(page._plot_curves[page.ide_time_plot]["Z_ACC"][1][0], 6.0)

    def test_trace_page_ide_eu_persists_after_file_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "ide_a.txt"
            path_b = Path(tmp) / "ide_b.txt"
            rows_a = "\n".join(f"{10 + i:.6f} {20 + i:.6f}" for i in range(32))
            rows_b = "\n".join(f"{30 + i:.6f} {40 + i:.6f}" for i in range(32))
            header = (
                "sample frequency: 1000\n"
                "undersample: 1\n"
                "signal num: 2\n"
                "Buffer length: 32\n"
                "X_PROX;Y_FB\n"
            )
            path_a.write_text(header + rows_a + "\n", encoding="utf-8")
            path_b.write_text(header + rows_b + "\n", encoding="utf-8")
            page = TraceAnalysisPage()

            page.load_paths([path_a, path_b])
            page.ide_eu_table.item(0, 1).setText("7")
            page.ide_eu_table.item(1, 2).setCheckState(QtCore.Qt.Unchecked)
            page.file_list.setCurrentRow(1)
            page.file_list.setCurrentRow(0)

        eu_values = {
            page.ide_eu_table.item(row, 0).text(): float(page.ide_eu_table.item(row, 1).text())
            for row in range(page.ide_eu_table.rowCount())
        }
        enabled_values = {
            page.ide_eu_table.item(row, 0).text(): page.ide_eu_table.item(row, 2).checkState() == QtCore.Qt.Checked
            for row in range(page.ide_eu_table.rowCount())
        }
        self.assertEqual(eu_values["X_PROX"], 7.0)
        self.assertFalse(enabled_values["Y_FB"])
        self.assertEqual(page.files[0].channel_eu["X_PROX"], 7.0)
        self.assertFalse(page.files[0].table.metadata["ide_enabled_channels"]["Y_FB"])

    def test_trace_page_ide_eu_updates_selected_files_in_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "ide_a.txt"
            path_b = Path(tmp) / "ide_b.txt"
            rows_a = "\n".join(f"{10 + i:.6f} {20 + i:.6f}" for i in range(32))
            rows_b = "\n".join(f"{30 + i:.6f} {40 + i:.6f}" for i in range(32))
            header = (
                "sample frequency: 1000\n"
                "undersample: 1\n"
                "signal num: 2\n"
                "Buffer length: 32\n"
                "X_PROX;Y_FB\n"
            )
            path_a.write_text(header + rows_a + "\n", encoding="utf-8")
            path_b.write_text(header + rows_b + "\n", encoding="utf-8")
            page = TraceAnalysisPage()

            page.load_paths([path_a, path_b])
            page.file_list.item(0).setSelected(True)
            page.file_list.item(1).setSelected(True)
            page.ide_eu_table.item(0, 1).setText("11")
            page.ide_eu_table.item(1, 2).setCheckState(QtCore.Qt.Unchecked)

        self.assertEqual(page.files[0].channel_eu["X_PROX"], 11.0)
        self.assertEqual(page.files[1].channel_eu["X_PROX"], 11.0)
        self.assertFalse(page.files[0].table.metadata["ide_enabled_channels"]["Y_FB"])
        self.assertFalse(page.files[1].table.metadata["ide_enabled_channels"]["Y_FB"])

    def test_trace_page_ide_suffix_eu_updates_selected_files_in_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "ide_a.txt"
            path_b = Path(tmp) / "ide_b.txt"
            rows_a = "\n".join(f"{10 + i:.6f} {20 + i:.6f}" for i in range(32))
            rows_b = "\n".join(f"{30 + i:.6f} {40 + i:.6f}" for i in range(32))
            header = (
                "sample frequency: 1000\n"
                "undersample: 1\n"
                "signal num: 2\n"
                "Buffer length: 32\n"
                "X_PROX;Y_FB\n"
            )
            path_a.write_text(header + rows_a + "\n", encoding="utf-8")
            path_b.write_text(header + rows_b + "\n", encoding="utf-8")
            page = TraceAnalysisPage()

            page.load_paths([path_a, path_b])
            page.file_list.item(0).setSelected(True)
            page.file_list.item(1).setSelected(True)
            page.ide_suffix_edits["Prox"].setText("3")
            page.ide_suffix_edits["FB"].setText("6")
            page.ide_suffix_apply_button.click()

        self.assertEqual(page.files[0].channel_eu["X_PROX"], 3.0)
        self.assertEqual(page.files[1].channel_eu["X_PROX"], 3.0)
        self.assertEqual(page.files[0].channel_eu["Y_FB"], 6.0)
        self.assertEqual(page.files[1].channel_eu["Y_FB"], 6.0)

    def test_trace_page_hac_presets_match_matlab_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hac.csv"
            path.write_text(
                "Period(ms),10\n"
                "time,位移1,速度1,温度1\n"
                "bad_time,1,2,3\n"
                "bad_time2,4,5,6\n",
                encoding="utf-8",
            )
            page = TraceAnalysisPage()

            page.load_paths([path])
            presets = [page.hac_preset_combo.itemText(index) for index in range(page.hac_preset_combo.count())]
            page.hac_preset_combo.setCurrentText("速度")

        self.assertEqual(presets, ["位移", "速度", "温度", "All Channels"])
        self.assertIs(page.tabs.currentWidget(), page.hac_tab)
        self.assertEqual([page.hac_channel_list.item(index).text() for index in range(page.hac_channel_list.count())], ["速度1"])
        self.assertEqual(set(page._plot_curves[page.hac_plot]), {"速度1"})

    def test_trace_page_hac_subplot_mode_uses_separate_hac_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hac.csv"
            path.write_text(
                "Period(ms),10\n"
                "time,位移1,速度1\n"
                "bad_time,1,10\n"
                "bad_time2,4,20\n",
                encoding="utf-8",
            )
            page = TraceAnalysisPage()

            page.load_paths([path])
            page.hac_preset_combo.setCurrentText("All Channels")
            page.hac_channel_list.selectAll()
            page.hold_check.setChecked(True)
            page.hac_plot_mode_combo.setCurrentIndex(1)

        self.assertFalse(page.hold_check.isChecked())
        self.assertFalse(page.hold_check.isEnabled())
        self.assertEqual(page.hac_plot.getAxis("left").labelText, "Subplots")
        self.assertEqual(set(page._plot_curves[page.hac_plot]), {"位移1", "速度1"})

    def test_modal_page_loads_vna_extracts_mode_and_exports_gif(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])
            peaks = page.find_peaks()
            mode = page.extract_mode()
            page.preview_tabs.setCurrentWidget(page.layout_preview_tab)
            page.preview_mode()
            gif_path = page.export_mode_gif(Path(tmp) / "mode.gif")

            self.assertEqual(len(page.files), 1)
            self.assertTrue(peaks)
            self.assertIsNotNone(mode)
            self.assertIs(page.preview_tabs.currentWidget(), page.mode_preview_tab)
            self.assertTrue(gif_path.exists())
            gif_data = gif_path.read_bytes()
            self.assertEqual(gif_data[:6], b"GIF89a")
            self.assertIn(b"NETSCAPE2.0", gif_data)
            self.assertGreaterEqual(gif_data.count(b"\x21\xF9\x04"), 24)
            page._preview_timer.stop()

    def test_modal_page_uses_true_opengl_3d_views(self):
        page = ModalShapePage()
        mode = {
            "coords": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.5], [1.0, 1.0, 1.0]], dtype=float),
            "disp_complex": np.array(
                [[0.0 + 0.0j, 0.2 + 0.1j, 0.0 + 0.0j], [0.3 + 0.1j, 0.0 + 0.0j, 0.2j], [0.0, 0.2, 0.3]],
                dtype=complex,
            ),
            "labels": ["P1", "P2", "P3"],
            "scale": 1.0,
            "lines": [{"start": "P1", "end": "P2"}, {"start": "P2", "end": "P3"}],
        }

        page._render_mode(mode, phase=0.0)

        self.assertIsInstance(page.layout_plot, Modal3DView)
        self.assertIsInstance(page.mode_plot, Modal3DView)
        self.assertGreater(len(page.mode_plot._render_items), 5)
        self.assertFalse(
            any(getattr(item, "mode", None) == "line_strip" for item in page.mode_plot._render_items)
        )

    def test_modal_3d_preview_preserves_zoom_between_animation_frames(self):
        page = ModalShapePage()
        mode = {
            "coords": np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [2.0, 0.0, 0.3]], dtype=float),
            "disp_complex": np.array([[0.0, 0.4, 0.1j], [0.2, 0.0, 0.3j], [0.0, 0.4, 0.2j]], dtype=complex),
            "labels": ["P1", "P2", "P3"],
            "base_scale": 1.0,
            "scale": 1.0,
            "lines": [{"start": "P1", "end": "P2"}, {"start": "P2", "end": "P3"}],
        }

        page._render_mode(mode, phase=0.0)
        page.mode_plot.setCameraPosition(distance=42.0)
        page._render_mode(mode, phase=1.0)

        self.assertAlmostEqual(float(page.mode_plot.opts["distance"]), 42.0)

    def test_modal_3d_preview_only_shows_current_shape_without_arrows(self):
        page = ModalShapePage()
        mode = {
            "coords": np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [2.0, 0.0, 0.3]], dtype=float),
            "disp_complex": np.array([[0.0, 0.4, 0.1j], [0.2, 0.0, 0.3j], [0.0, 0.4, 0.2j]], dtype=complex),
            "labels": ["P1", "P2", "P3"],
            "base_scale": 1.0,
            "scale": 1.0,
            "lines": [{"start": "P1", "end": "P2"}, {"start": "P2", "end": "P3"}],
        }

        page._render_mode(mode, phase=0.0)
        first_shape_line = np.asarray(page.mode_plot._render_items[4].pos, dtype=float).copy()
        page._render_mode(mode, phase=math.pi / 2.0)
        second_shape_line = np.asarray(page.mode_plot._render_items[4].pos, dtype=float).copy()

        self.assertFalse(any(item.__class__.__name__ == "GLGridItem" for item in page.mode_plot._render_items))
        self.assertFalse(
            any(
                item.__class__.__name__ == "GLLinePlotItem" and np.asarray(getattr(item, "pos", [])).shape == (24, 3)
                for item in page.mode_plot._render_items
            )
        )
        line_colors = [
            tuple(getattr(item, "color"))
            for item in page.mode_plot._render_items
            if item.__class__.__name__ == "GLLinePlotItem"
        ]
        scatter_sizes = [
            float(item.size)
            for item in page.mode_plot._render_items
            if item.__class__.__name__ == "GLScatterPlotItem"
        ]
        self.assertEqual(set(line_colors), {(0.84, 0.15, 0.24, 1.0)})
        self.assertEqual(scatter_sizes, [14.0])
        with self.assertRaises(AssertionError):
            np.testing.assert_allclose(first_shape_line, second_shape_line)

    def test_modal_animation_gif_frames_do_not_draw_base_skeleton(self):
        mode = {
            "coords": np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [2.0, 0.0, 0.3]], dtype=float),
            "disp_complex": np.array([[0.0, 0.4, 0.1j], [0.2, 0.0, 0.3j], [0.0, 0.4, 0.2j]], dtype=complex),
            "labels": ["P1", "P2", "P3"],
            "scale": 1.0,
            "lines": [{"start": "P1", "end": "P2"}, {"start": "P2", "end": "P3"}],
        }

        frames = render_mode_animation_frames(mode, frame_count=4, width=160, height=120)

        self.assertEqual(len(frames), 4)
        for frame in frames:
            self.assertNotIn(1, np.unique(frame))
            self.assertNotIn(2, np.unique(frame))
            self.assertIn(3, np.unique(frame))
            self.assertIn(4, np.unique(frame))

    def test_modal_page_uses_matlab_style_control_layout(self):
        page = ModalShapePage()

        self.assertEqual(page.load_button.text(), "加载文件")
        self.assertEqual(page.find_peaks_button.text(), "自动找峰")
        self.assertTrue(hasattr(page, "point_row_edit"))
        self.assertTrue(hasattr(page, "line_row_edit"))
        self.assertEqual(page.point_table.columnCount(), 12)
        self.assertEqual(page.line_table.columnCount(), 4)
        self.assertEqual([page.left_work_tabs.tabText(index) for index in range(3)], ["控制区", "测点表", "连线表"])
        self.assertEqual([page.preview_tabs.tabText(index) for index in range(2)], ["测点骨架图", "振型预览"])
        self.assertFalse(page.point_table.verticalHeader().isHidden())
        self.assertFalse(page.line_table.verticalHeader().isHidden())
        widgets = [page.line_action_row.itemAt(index).widget() for index in range(page.line_action_row.count())]
        labels = [widget.text() for widget in widgets if widget is not None and hasattr(widget, "text")]
        self.assertEqual(labels[:5], ["自动连线", "新增连线", "行号", "", "删除连线"])
        self.assertLessEqual(page.point_table.columnWidth(0), 42)
        self.assertLessEqual(page.point_table.columnWidth(2), 96)
        self.assertGreaterEqual(page.candidate_list.minimumHeight(), 180)
        self.assertGreaterEqual(page.candidate_list.maximumHeight(), 320)

    def test_modal_row_number_delete_matches_matlab_workflow(self):
        page = ModalShapePage()
        page._insert_point_row([True, "P1", "a.vna", 1, 2, 3, 1, 1, 1, 0, 0, 0])
        page._insert_point_row([True, "P2", "b.vna", 1, 2, 3, 1, 1, 1, 1, 0, 0])

        page.point_row_edit.setText("1")
        page._delete_point_rows()

        self.assertEqual(page.point_table.rowCount(), 1)
        self.assertEqual(page.point_table.item(0, 1).text(), "P2")

    def test_modal_page_imports_mapping_and_auto_builds_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping = Path(tmp) / "mapping.csv"
            mapping.write_text(
                "point_id,file_name,x_ch,y_ch,z_ch,x_scale,y_scale,z_scale,x,y,z,use\n"
                "P1,a.vna,2,3,4,1,1,1,0,0,0,1\n"
                "P2,b.vna,2,3,4,1,1,1,1,0,0,1\n",
                encoding="utf-8",
            )
            export_path = Path(tmp) / "mapping_out.csv"
            page = ModalShapePage()

            imported = page.import_point_mapping_csv(mapping)
            page.point_table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
            page.export_point_mapping_csv(export_path)

            self.assertEqual(imported, 2)
            self.assertEqual(page.point_table.columnCount(), 12)
            self.assertEqual(page.line_table.columnCount(), 4)
            self.assertEqual(page.line_table.rowCount(), 1)
            self.assertEqual(page.line_table.item(0, 3).text(), "auto")
            exported = export_path.read_text(encoding="utf-8")
            self.assertIn("x_scale", exported)
            self.assertIn("P2,b.vna,2,3,4,1.0,1.0,1.0,1.0,0.0,0.0,0", exported)

    def test_modal_line_table_accepts_numeric_point_ids(self):
        page = ModalShapePage()
        page._insert_point_row([True, "P1", "", 1, 2, 3, 1, 1, 1, 0, 0, 0])
        page._insert_point_row([True, "P2", "", 1, 2, 3, 1, 1, 1, 1, 0, 0])
        page._insert_line_row([True, "1", "2", "manual"])

        self.assertEqual(page.line_table.item(0, 1).text(), "P1")
        self.assertEqual(page.line_table.item(0, 2).text(), "P2")
        page.line_table.item(0, 1).setText("1")
        page.line_table.item(0, 2).setText("2")

        self.assertEqual(page.line_table.item(0, 1).text(), "P1")
        self.assertEqual(page.line_table.item(0, 2).text(), "P2")
        self.assertEqual(page._line_rows(require_enabled=True)[0]["start"], "P1")
        self.assertEqual(page._line_rows(require_enabled=True)[0]["end"], "P2")

    def test_modal_page_imports_xlsx_mapping_without_openpyxl(self):
        with tempfile.TemporaryDirectory() as tmp:
            mapping = Path(tmp) / "mapping.xlsx"
            self._write_minimal_mapping_xlsx(mapping)
            page = ModalShapePage()

            rows = read_xlsx_rows_basic(mapping)
            imported = page.import_point_mapping_xlsx(mapping)

            self.assertEqual(rows[0][:3], ["point_id", "file_name", "x_ch"])
            self.assertEqual(imported, 2)
            self.assertEqual(page.point_table.item(0, 1).text(), "P1")
            self.assertEqual(page.point_table.item(1, 2).text(), "b.vna")

    def test_modal_import_mapping_refreshes_layout_once(self):
        page = ModalShapePage()
        refresh_calls = []
        page._refresh_layout_plot = lambda: refresh_calls.append(True)
        records = [
            {
                "point_id": f"P{index + 1}",
                "file_name": f"p{index + 1}.vna",
                "x_ch": "2",
                "y_ch": "3",
                "z_ch": "4",
                "x": str(index),
                "y": "0",
                "z": "0",
                "use": "1",
            }
            for index in range(30)
        ]

        imported = page._import_point_mapping_records(records)

        self.assertEqual(imported, 30)
        self.assertEqual(len(refresh_calls), 1)

    def test_modal_find_peaks_button_click_does_not_pass_checked_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.find_peaks_button.click()
            page.load_paths([path])
            page.find_peaks_button.click()

            self.assertGreaterEqual(page.candidate_list.count(), 1)

    def test_modal_channel_numbers_follow_matlab_response_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])
            dataset = page.files[0].dataset

            self.assertEqual(page._modal_channel_series_key(dataset.frf, dataset, 2), "ai0->ai1")
            self.assertEqual(page._modal_channel_series_key(dataset.frf, dataset, 3), "ai0->ai2")
            self.assertEqual(page._modal_channel_series_key(dataset.frf, dataset, 4), "ai0->ai3")
            self.assertAlmostEqual(abs(page._modal_channel_value_complex(dataset, 2, 1)), abs(8.0 + 1.0j))
            self.assertAlmostEqual(abs(page._modal_channel_value_complex(dataset, 3, 1)), 4.0)
            self.assertAlmostEqual(abs(page._modal_channel_value_complex(dataset, 4, 1)), 2.0)

    def test_modal_missing_mapping_file_does_not_fallback_to_first_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])

            self.assertIsNone(page._dataset_by_name("missing.vna"))

    def test_modal_shape_preserves_relative_amplitude_between_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            strong = Path(tmp) / "strong.vna"
            weak = Path(tmp) / "weak.vna"
            mapping = Path(tmp) / "mapping.csv"
            self._write_modal_vna(strong, x_gain=1.0, y_gain=1.0, z_gain=1.0)
            self._write_modal_vna(weak, x_gain=0.25, y_gain=0.25, z_gain=0.25)
            mapping.write_text(
                "point_id,file_name,x_ch,y_ch,z_ch,x_scale,y_scale,z_scale,x,y,z,use\n"
                "P1,strong.vna,2,3,4,1,1,1,0,0,0,1\n"
                "P2,weak.vna,2,3,4,1,1,1,1,0,0,1\n",
                encoding="utf-8",
            )
            page = ModalShapePage()

            page.load_paths([strong, weak])
            page.import_point_mapping_csv(mapping)
            page.frequency_edit.setValue(10.0)
            page.apply_frequency()
            mode = page.extract_mode()

            self.assertIsNotNone(mode)
            disp = np.asarray(mode["disp_complex"], dtype=complex)
            self.assertAlmostEqual(abs(disp[0, 0]), 1.0)
            self.assertAlmostEqual(abs(disp[1, 0]), 0.25)
            self.assertLess(np.linalg.norm(disp[1]), np.linalg.norm(disp[0]) * 0.35)

    def test_modal_aggregate_frf_matches_matlab_mean_on_reference_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            strong = Path(tmp) / "strong.vna"
            weak = Path(tmp) / "weak.vna"
            mapping = Path(tmp) / "mapping.csv"
            self._write_modal_vna(strong, x_gain=1.0, y_gain=2.0, z_gain=3.0)
            self._write_modal_vna(weak, x_gain=0.5, y_gain=1.0, z_gain=1.5)
            mapping.write_text(
                "point_id,file_name,x_ch,y_ch,z_ch,x_scale,y_scale,z_scale,x,y,z,use\n"
                "P1,strong.vna,2,3,4,1,1,1,0,0,0,1\n"
                "P2,weak.vna,2,3,4,1,1,1,1,0,0,1\n",
                encoding="utf-8",
            )
            page = ModalShapePage()

            page.load_paths([strong, weak])
            page.import_point_mapping_csv(mapping)
            freq, db = page._aggregate_frf_curve()

            expected_freq = page.files[0].dataset.frequency_hz
            base_x = np.array([1.0 + 0.0j, 8.0 + 1.0j, 2.0 + 0.0j, 1.6, 1.2, 1.0, 0.8, 0.6, 0.4])
            base_y = np.array([0.5 + 0.0j, 4.0 + 0.0j, 1.0 + 0.0j, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2])
            base_z = np.array([0.2 + 0.0j, 2.0 + 0.0j, 0.5 + 0.0j, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1])
            expected_mag = np.nanmean(
                np.vstack(
                    [
                        np.abs(base_x),
                        np.abs(2.0 * base_y),
                        np.abs(3.0 * base_z),
                        np.abs(0.5 * base_x),
                        np.abs(1.0 * base_y),
                        np.abs(1.5 * base_z),
                    ]
                ),
                axis=0,
            )

            np.testing.assert_allclose(freq, expected_freq)
            np.testing.assert_allclose(db, 20.0 * np.log10(expected_mag))

    def test_modal_aggregate_frf_requires_mapped_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])
            page.point_table.setRowCount(0)
            freq, db = page._aggregate_frf_curve()

            self.assertEqual(freq.size, 0)
            self.assertEqual(db.size, 0)

    def test_modal_negative_point_scale_flips_mode_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])
            page.point_table.item(0, 6).setText("-1")
            mode = page.extract_mode()

            self.assertIsNotNone(mode)
            self.assertLess(float(np.real(mode["disp_complex"][0, 0])), 0.0)

    def test_modal_partial_point_rows_merge_without_losing_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            x_file = Path(tmp) / "x_only.vna"
            yz_file = Path(tmp) / "yz_only.vna"
            self._write_modal_vna(x_file, x_gain=1.0, y_gain=0.0, z_gain=0.0)
            self._write_modal_vna(yz_file, x_gain=0.0, y_gain=2.0, z_gain=3.0)
            page = ModalShapePage()

            page.load_paths([x_file, yz_file])
            page.point_table.setRowCount(0)
            page._insert_point_row([True, "P1", x_file.name, 2, 99, 99, 1, 1, 1, 0, 0, 0])
            page._insert_point_row([True, "P1", yz_file.name, 99, 3, 4, 1, 1, 1, 0, 0, 0])
            page.frequency_edit.setValue(10.0)
            page._active_frequency = 10.0
            mode = page.extract_mode()

        self.assertIsNotNone(mode)
        self.assertEqual(mode["labels"], ["P1"])
        self.assertIn(x_file.name, mode["file_names"][0])
        self.assertIn(yz_file.name, mode["file_names"][0])
        self.assertGreater(abs(mode["disp_complex"][0, 0]), 0.0)
        self.assertGreater(abs(mode["disp_complex"][0, 1]), 0.0)
        self.assertGreater(abs(mode["disp_complex"][0, 2]), 0.0)

    def test_modal_auto_lines_prefer_axis_aligned_neighbors(self):
        page = ModalShapePage()
        page._insert_point_row([True, "P1", "", 1, 2, 3, 1, 1, 1, 0, 0, 0])
        page._insert_point_row([True, "P2", "", 1, 2, 3, 1, 1, 1, 1, 0, 0])
        page._insert_point_row([True, "P3", "", 1, 2, 3, 1, 1, 1, 0, 1, 0])
        page._insert_point_row([True, "P4", "", 1, 2, 3, 1, 1, 1, 1, 1, 0])

        page.auto_build_lines(show_status=False)
        pairs = {
            tuple(sorted((page.line_table.item(row, 1).text(), page.line_table.item(row, 2).text())))
            for row in range(page.line_table.rowCount())
        }

        self.assertEqual(pairs, {("P1", "P2"), ("P1", "P3"), ("P2", "P4"), ("P3", "P4")})

    def test_modal_fallback_auto_lines_match_matlab_nearest_limit(self):
        coords = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [10.0, 10.0, 0.0],
            ],
            dtype=float,
        )

        edges = set(fallback_nearest_auto_edges(coords))

        self.assertEqual(edges, {(0, 1), (0, 2), (1, 2), (1, 3)})
        self.assertNotIn((2, 3), edges)
        self.assertNotIn((0, 3), edges)

    def test_modal_layout_3d_view_emphasizes_skeleton_points(self):
        import pyqtgraph.opengl as gl

        view = Modal3DView()
        view.render_structure(
            np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=float),
            ["P1", "P2", "P3"],
            [{"start": "P1", "end": "P2"}, {"start": "P1", "end": "P3"}],
            deformed=None,
            show_labels=True,
            azimuth=35.0,
            elevation=24.0,
        )

        scatter_sizes = [
            float(item.size) for item in view._render_items if isinstance(item, gl.GLScatterPlotItem)
        ]

        self.assertIn(22.0, scatter_sizes)
        self.assertIn(13.0, scatter_sizes)

    def test_modal_layout_3d_view_shows_all_point_labels(self):
        labels = [f"P{index}" for index in range(1, 16)]
        coords = np.column_stack([np.arange(15, dtype=float), np.zeros(15), np.zeros(15)])
        view = Modal3DView()

        view.render_structure(coords, labels, [], deformed=None, show_labels=True, azimuth=35.0, elevation=24.0)

        point_labels = {
            str(getattr(item, "text", ""))
            for item in view._render_items
            if item.__class__.__name__ == "GLTextItem" and str(getattr(item, "text", "")).startswith("P")
        }
        self.assertEqual(point_labels, set(labels))

    def _write_minimal_mapping_xlsx(self, path: Path) -> None:
        headers = ["point_id", "file_name", "x_ch", "y_ch", "z_ch", "x_scale", "y_scale", "z_scale", "x", "y", "z", "use"]
        rows = [
            headers,
            ["P1", "a.vna", "2", "3", "4", "1", "1", "1", "0", "0", "0", "1"],
            ["P2", "b.vna", "2", "3", "4", "1", "1", "1", "1", "0", "0", "1"],
        ]

        def cell_ref(row: int, column: int) -> str:
            letters = ""
            value = column
            while value:
                value, remainder = divmod(value - 1, 26)
                letters = chr(ord("A") + remainder) + letters
            return f"{letters}{row}"

        sheet_rows = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for column_index, value in enumerate(row, start=1):
                cells.append(
                    f'<c r="{cell_ref(row_index, column_index)}" t="inlineStr"><is><t>{value}</t></is></c>'
                )
            sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
            )
            archive.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
            )
            archive.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="PointMap" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>""",
            )

    @staticmethod
    def _write_modal_vna(path: Path, *, x_gain: float = 1.0, y_gain: float = 1.0, z_gain: float = 1.0) -> None:
        session = default_session_config()
        session.ai_channels = [
            ChannelConfig(name="ai0", physical_name="Dev1/ai0", label="REF", is_reference=True),
            ChannelConfig(name="ai1", physical_name="Dev1/ai1", label="X"),
            ChannelConfig(name="ai2", physical_name="Dev1/ai2", label="Y"),
            ChannelConfig(name="ai3", physical_name="Dev1/ai3", label="Z"),
        ]
        session.acquisition.reference_channel = "ai0"
        session.acquisition.response_channels = ["ai1", "ai2", "ai3"]
        frequency = np.array([5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0], dtype=float)
        measurement = MeasurementSet(
            sample_rate=1000.0,
            time_data={"t": np.arange(16, dtype=float) / 1000.0, "channels": {}},
            spectra={"f": frequency, "autospectrum": {"ai0": np.array([1.0, 8.0, 2.0, 1.6, 1.2, 1.0, 0.8, 0.6, 0.4])}},
            frf={
                "ai0->ai1": x_gain
                * np.array([1.0 + 0.0j, 8.0 + 1.0j, 2.0 + 0.0j, 1.6, 1.2, 1.0, 0.8, 0.6, 0.4]),
                "ai0->ai2": y_gain * np.array([0.5 + 0.0j, 4.0 + 0.0j, 1.0 + 0.0j, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2]),
                "ai0->ai3": z_gain * np.array([0.2 + 0.0j, 2.0 + 0.0j, 0.5 + 0.0j, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1]),
            },
            coherence={
                "ai0->ai1": np.array([0.9, 0.95, 0.9, 0.88, 0.86, 0.85, 0.84, 0.83, 0.82]),
                "ai0->ai2": np.array([0.9, 0.94, 0.9, 0.88, 0.86, 0.85, 0.84, 0.83, 0.82]),
                "ai0->ai3": np.array([0.9, 0.93, 0.9, 0.88, 0.86, 0.85, 0.84, 0.83, 0.82]),
            },
            cross_spectra={},
            correlations={},
            impulse_responses={},
        )
        save_legacy_vna(SavedSession(config=session, measurement=measurement, source_path=path), path)


if __name__ == "__main__":
    unittest.main()
