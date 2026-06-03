from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from python_vna.diagnostic.app import parse_args
from python_vna.diagnostic.data import (
    curve_pairs_from_table,
    load_numeric_table,
    load_trace_analysis_file,
    load_vibration_analysis_file,
)
from python_vna.diagnostic.pages import ModalShapePage, TraceAnalysisPage, VibrationAnalysisPage
from python_vna.diagnostic.shell import DiagnosticMainWindow
from python_vna.models import ChannelConfig, MeasurementSet, SavedSession
from python_vna.storage import default_session_config, save_legacy_vna
from python_vna.ui.analysis_viewer import AnalysisWorkbench, AnalysisViewer


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

    def test_vibration_parser_builds_log_groups_for_wide_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.csv"
            path.write_text("Time,ACC_X,ACC_Y,DISP_X\n0,1,2,3\n1,4,5,6\n", encoding="utf-8")

            parsed = load_vibration_analysis_file(path)

        self.assertIn("ACC", parsed.log_groups)
        self.assertIn("DISP", parsed.log_groups)

    def test_modal_page_loads_vna_extracts_mode_and_exports_gif(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modal.vna"
            self._write_modal_vna(path)
            page = ModalShapePage()

            page.load_paths([path])
            peaks = page.find_peaks()
            mode = page.extract_mode()
            gif_path = page.export_mode_gif(Path(tmp) / "mode.gif")

            self.assertEqual(len(page.files), 1)
            self.assertTrue(peaks)
            self.assertIsNotNone(mode)
            self.assertTrue(gif_path.exists())
            self.assertEqual(gif_path.read_bytes()[:6], b"GIF89a")

    @staticmethod
    def _write_modal_vna(path: Path) -> None:
        session = default_session_config()
        session.ai_channels = [
            ChannelConfig(name="ai0", physical_name="Dev1/ai0", label="X"),
            ChannelConfig(name="ai1", physical_name="Dev1/ai1", label="Y"),
            ChannelConfig(name="ai2", physical_name="Dev1/ai2", label="Z"),
        ]
        frequency = np.array([5.0, 10.0, 15.0], dtype=float)
        measurement = MeasurementSet(
            sample_rate=1000.0,
            time_data={"t": np.arange(16, dtype=float) / 1000.0, "channels": {}},
            spectra={"f": frequency, "autospectrum": {"ai0": np.array([1.0, 8.0, 2.0])}},
            frf={
                "ai0": np.array([1.0 + 0.0j, 8.0 + 1.0j, 2.0 + 0.0j]),
                "ai1": np.array([0.5 + 0.0j, 4.0 + 0.0j, 1.0 + 0.0j]),
                "ai2": np.array([0.2 + 0.0j, 2.0 + 0.0j, 0.5 + 0.0j]),
            },
            coherence={"ai0": np.array([0.9, 0.95, 0.9])},
            cross_spectra={},
            correlations={},
            impulse_responses={},
        )
        save_legacy_vna(SavedSession(config=session, measurement=measurement, source_path=path), path)


if __name__ == "__main__":
    unittest.main()
