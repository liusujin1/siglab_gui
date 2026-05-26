from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from python_vna.controller import VnaController
from python_vna.continuous_recording import RecordingStatus
from python_vna.daq.base import BackendFrame
from python_vna.models import MeasurementSet, SavedSession
from python_vna.storage import default_session_config
import python_vna.ui.main_window as main_window_module
from python_vna.ui.main_window import AcquisitionWorker, MCSetupDialog, MainWindow, VnaAxisItem


class _DummyBackend:
    def list_devices(self):
        return []

    def close(self):
        return None

    def stop(self):
        return None

    def abort(self):
        return None


class _SlowStopController:
    def __init__(self):
        self.abort_calls = 0
        self.request_stop_calls = 0

    def abort(self):
        self.abort_calls += 1
        time.sleep(0.25)

    def request_stop(self):
        self.request_stop_calls += 1


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self._settings_tmpdir = tempfile.TemporaryDirectory()
        self._settings_env_patcher = mock.patch.dict(
            os.environ,
            {
                "PYTHON_VNA_UI_SETTINGS": str(
                    Path(self._settings_tmpdir.name) / "ui_settings.json"
                )
            },
        )
        self._settings_env_patcher.start()
        session = default_session_config()
        self.controller = VnaController(_DummyBackend(), session)
        self.window = MainWindow(self.controller, session)

    def tearDown(self):
        self.window.close()
        self._settings_env_patcher.stop()
        self._settings_tmpdir.cleanup()

    @staticmethod
    def _measurement() -> MeasurementSet:
        time_axis = np.array([0.0, 0.1, 0.2, 0.3], dtype=float)
        freq_axis = np.array([0.0, 10.0, 20.0, 40.0], dtype=float)
        ref = np.array([0.0, 1.0, 0.5, -0.25], dtype=float)
        resp = np.array([0.0, 0.5, 0.25, -0.125], dtype=float)
        frf = np.array([1.0 + 0.0j, 2.0 + 1.0j, 3.0 - 2.0j, 4.0 + 0.5j])
        return MeasurementSet(
            sample_rate=100.0,
            time_data={"t": time_axis, "channels": {"ai0": ref, "ai1": resp}},
            spectra={
                "f": freq_axis,
                "fft": {"ai0": frf, "ai1": frf * 0.5},
                "autospectrum": {"ai0": np.abs(frf) ** 2, "ai1": np.abs(frf * 0.5) ** 2},
            },
            frf={"ai0->ai1": frf},
            coherence={"ai0->ai1": np.array([0.0, 1.0, 0.9, 0.8], dtype=float)},
            cross_spectra={"ai0->ai1": frf * 2.0},
            correlations={},
            impulse_responses={"ai0->ai1": np.array([0.0, 0.5, 0.25, 0.0], dtype=float)},
            metadata={"frame_index": 1},
        )

    def test_window_initializes_before_menu_actions_exist(self):
        self.assertEqual(self.window.windowTitle(), "VNA - USB-4431")
        self.assertEqual(self.window.top_cursor_label.text(), "Top Cursor: --")
        self.assertEqual(self.window.bottom_marker_label.text(), "Bottom Marker: off")
        self.assertEqual(self.window.top_marker_fields["trace"].text(), "off")
        self.assertGreaterEqual(len(self.window.findChildren(QtWidgets.QSplitter)), 1)
        self.assertTrue(self.window._controls_visible)
        self.assertLessEqual(self.window.width(), 1180)
        self.assertLessEqual(self.window.left_panel.maximumWidth(), 430)
        self.assertGreaterEqual(self.window.left_panel.minimumWidth(), 360)
        self.assertEqual(self.window.top_display_strip_combo.currentText(), "y(t)")
        self.assertEqual(self.window.bottom_display_strip_combo.currentText(), "xfer")
        self.assertEqual(self.window.bottom_value_mode_combo.currentText(), "dB")
        self.assertEqual(self.window.avg_button.text(), "Avg")
        self.assertTrue(self.window.avg_button.isEnabled())
        self.assertEqual(self.window.record_button.text(), "Record")
        self.assertTrue(self.window.record_button.isEnabled())
        self.assertEqual(self.window.sample_rate_edit.value(), 2560.0)
        self.assertEqual(self.window.average_count_edit.value(), 20)
        self.assertEqual(self.window.refresh_devices_button.text(), "Refresh")
        self.assertEqual(self.window.save_session_button.text(), "Save VNA")
        self.assertEqual(self.window.load_session_button.text(), "Load")
        self.assertEqual(self.window.import_legacy_button.text(), "Import")
        self.assertEqual(self.window.open_vna_button.text(), "Open VNA")
        self.assertEqual(self.window.toolbar_data_tip_button.text(), "Data Tip")
        self.assertIsInstance(self.window.toolbar_data_tip_button, QtWidgets.QPushButton)
        self.assertEqual(self.window.start_button.text(), "Inst")
        self.assertIsNotNone(self.window.mc_setup_action)
        available = self.window._screen_available_geometry()
        self.assertLessEqual(self.window.width(), available.width())
        self.assertLessEqual(self.window.height(), available.height())
        self.assertEqual(self.window.left_panel.objectName(), "legacyLeftPanel")
        legacy_titles = [
            label.text()
            for label in self.window.left_panel.findChildren(QtWidgets.QLabel)
            if label.objectName() == "legacyPanelTitle"
        ]
        self.assertEqual(
            legacy_titles,
            ["CHANNEL SETUP", "FREQUENCY RNG", "PROCESSING", "TRIGGER"],
        )
        self.assertEqual(
            [self.window.top_control_tabs.tabText(index) for index in range(self.window.top_control_tabs.count())],
            ["Excitation", "Modal", "Display"],
        )
        menu_actions = {
            action.text().replace("&", ""): action for action in self.window.menuBar().actions()
        }
        self.assertIn("File", menu_actions)
        self.assertIn("Setup", menu_actions)
        self.assertIn("Modal", menu_actions)
        self.assertIn("Analysis", menu_actions)
        self.assertIsNone(menu_actions["Setup"].menu())
        self.assertIsNone(menu_actions["Modal"].menu())
        self.assertIsNone(menu_actions["Analysis"].menu())
        self.assertIsNot(self.window.excitation_setup_page, self.window.modal_setup_page)
        self.assertNotIn("Units", menu_actions)
        file_menu = menu_actions["File"].menu()
        self.assertIsNotNone(file_menu)
        file_action_texts = [
            action.text().replace("&", "")
            for action in file_menu.actions()
            if not action.isSeparator()
        ]
        self.assertEqual(
            file_action_texts,
            ["Open VNA", "Save VNA", "Save to Default", "Export Data", "Exit"],
        )
        display_menu = menu_actions["Display"].menu()
        self.assertIsNotNone(display_menu)
        display_action_texts = {
            action.text().replace("&", "")
            for action in display_menu.actions()
            if not action.isSeparator()
        }
        self.assertIn("Mark", display_action_texts)
        self.assertIn("Overlay Upper", display_action_texts)
        self.assertIn("Overlay Lower", display_action_texts)
        self.assertIn("Clear Overlays", display_action_texts)
        self.assertIn("Open Current Plots", display_action_texts)
        self.assertIn("Light Theme", display_action_texts)
        self.assertNotIn("Bode Plot", display_action_texts)
        self.assertNotIn("Cursor Readout", display_action_texts)
        self.assertNotIn("Data Tip", display_action_texts)
        self.assertNotIn("Clear Data Tips", display_action_texts)
        self.assertNotIn("Auto Scale Top X", display_action_texts)
        self.assertNotIn("Auto Scale Bottom X", display_action_texts)
        self.assertNotIn("Set Top X Range", display_action_texts)
        self.assertNotIn("Set Bottom X Range", display_action_texts)
        self.assertNotIn("Single", display_action_texts)
        self.assertNotIn("Dual", display_action_texts)
        self.assertNotIn("Control Panel", display_action_texts)
        self.assertNotIn("Overlay", display_action_texts)
        self.assertNotIn("Grids", display_action_texts)
        self.assertNotIn("Axis Labels", display_action_texts)
        self.assertNotIn("Find Top Peak", display_action_texts)
        self.assertNotIn("Find Bottom Peak", display_action_texts)
        self.assertNotIn("Find Top Valley", display_action_texts)
        self.assertNotIn("Find Bottom Valley", display_action_texts)
        toolbar_button_texts = {
            button.text()
            for button in self.window.findChild(QtWidgets.QWidget, "topToolbar").findChildren(QtWidgets.QPushButton)
        }
        self.assertNotIn("Refresh", toolbar_button_texts)
        toolbar_label_texts = {
            label.text()
            for label in self.window.findChild(QtWidgets.QWidget, "topToolbar").findChildren(QtWidgets.QLabel)
        }
        self.assertNotIn("Backend", toolbar_label_texts)
        self.assertNotIn("Device", toolbar_label_texts)
        for button in (
            self.window.open_vna_button,
            self.window.save_session_button,
            self.window.toolbar_data_tip_button,
            self.window.start_button,
            self.window.avg_button,
            self.window.record_button,
            self.window.stop_button,
        ):
            expected = button.fontMetrics().horizontalAdvance(button.text()) + 26
            self.assertGreaterEqual(button.minimumWidth(), expected)
        self.assertEqual(self.window.top_trace_list.count(), 4)
        self.assertEqual(self.window.bottom_trace_list.count(), 3)
        self.assertEqual(
            self.window.top_trace_list.verticalScrollBarPolicy(),
            QtCore.Qt.ScrollBarAlwaysOff,
        )
        self.assertIn("indicator:checked", self.window.top_trace_list.styleSheet())
        self.assertGreaterEqual(self.window.top_trace_list.height(), 4 * 26 + 4)
        self.assertLessEqual(self.window.top_trace_list.height(), 110)

    def test_plot_sections_use_matlab_style_side_control_panels(self):
        side_panels = [
            child for child in self.window.findChildren(QtWidgets.QWidget)
            if child.objectName() in {"upperAxisPanel", "lowerAxisPanel"}
        ]
        self.assertEqual(len(side_panels), 2)
        self.assertTrue(all(panel.width() <= 180 for panel in side_panels))
        self.assertIn("QLabel#vnaMiniLabel { color: #eef6ff", side_panels[0].styleSheet())
        self.assertIn("border-radius: 6px", side_panels[0].styleSheet())

    def test_dark_background_labels_use_light_text(self):
        stylesheet = self.window.styleSheet()

        self.assertEqual(self.window._theme_name, "dark")
        self.assertIn("QLabel, QCheckBox {\n                color: #a9bed4;", stylesheet)
        self.assertIn("QCheckBox:enabled {\n                color: #eef6ff;", stylesheet)
        self.assertNotIn("QGroupBox QLabel, QGroupBox QCheckBox {\n                color: #000000;", stylesheet)
        self.assertNotIn("background: #f0f0f0;\n                color: #202020;", stylesheet)
        self.assertIn("QMenuBar", stylesheet)
        self.assertIn("QMenu", stylesheet)
        self.assertIn("QGroupBox::title", stylesheet)
        self.assertIn("color: #ffffff;", stylesheet)
        self.assertIn("border-radius: 8px", stylesheet)
        self.assertIn("QPushButton#dangerButton:enabled", stylesheet)
        self.assertIn("QPushButton:disabled, QToolButton:disabled", stylesheet)
        self.assertIn("color: #64748b;", stylesheet)

    def test_light_theme_switch_updates_main_panels_plots_and_persists(self):
        self.window.light_theme_action.trigger()

        self.assertEqual(self.window._theme_name, "light")
        self.assertTrue(self.window.light_theme_action.isChecked())
        self.assertIn("background: #f4f7fb;", self.window.styleSheet())
        self.assertIn("#plotWorkspace { background: #eaf1f8; }", self.window.right_panel.styleSheet())
        self.assertIn("background: #ffffff;", self.window.left_panel.styleSheet())
        self.assertIn("color: #102033;", self.window.left_panel.styleSheet())
        self.assertEqual(self.window.top_plot.backgroundBrush().color().name(), "#ffffff")
        self.assertEqual(
            self.window.top_plot.getAxis("bottom").pen().color().name(),
            "#172033",
        )
        self.assertTrue(self.window._ui_settings_path.exists())

        next_controller = VnaController(_DummyBackend(), default_session_config())
        next_window = MainWindow(next_controller, next_controller.state.session)
        try:
            self.assertEqual(next_window._theme_name, "light")
            self.assertTrue(next_window.light_theme_action.isChecked())
        finally:
            next_window.close()

    def test_legacy_left_panel_avoids_gray_background_black_text(self):
        stylesheet = self.window.left_panel.styleSheet()

        self.assertIn("QLabel#legacyGrayCell", stylesheet)
        self.assertIn("background: #203247;", stylesheet)
        self.assertIn("color: #ffffff;", stylesheet)
        self.assertIn("QCheckBox {\n                background: #203247;", stylesheet)
        self.assertIn("QLabel#legacyText {\n                background: #203247;", stylesheet)
        self.assertIn("border-radius: 7px", stylesheet)
        self.assertNotIn("background: #c9c9c9;\n                color: #000000;", stylesheet)
        self.assertNotIn("background: #f0f0f0;\n                color: #606060;", stylesheet)

    def test_mc_setup_dialog_matches_legacy_matrix_headers(self):
        dialog = MCSetupDialog(self.window)
        headers = [
            dialog.table.horizontalHeaderItem(index).text()
            for index in range(dialog.table.columnCount())
        ]

        self.assertEqual(
            headers,
            [
                "On/Off",
                "Full Scale",
                "Coupling",
                "Offset",
                "Label",
                "EU/Volt",
                "EU",
                "Per EU",
                "Invert",
                "0 dB Vref",
            ],
        )
        self.assertEqual(dialog.table.rowCount(), self.window.channel_table.rowCount())
        self.assertLessEqual(dialog.width(), 930)
        self.assertLessEqual(dialog.height(), 210)
        self.assertEqual(
            dialog.table.horizontalScrollBarPolicy(),
            QtCore.Qt.ScrollBarAlwaysOff,
        )
        self.assertEqual(
            dialog.table.verticalScrollBarPolicy(),
            QtCore.Qt.ScrollBarAlwaysOff,
        )
        self.assertIn("Ch 1", dialog.table.item(0, 0).text())
        self.assertTrue(dialog.table.item(0, 0).flags() & QtCore.Qt.ItemIsUserCheckable)
        self.assertEqual(dialog.table.item(0, 0).checkState(), QtCore.Qt.Checked)
        dialog.close()

    def test_mc_setup_table_has_room_for_all_channel_rows(self):
        dialog = MCSetupDialog(self.window)

        row_height = sum(dialog.table.rowHeight(row) for row in range(dialog.table.rowCount()))

        self.assertGreaterEqual(dialog.table.viewport().height(), row_height)
        self.assertGreater(dialog.table.height(), 122)
        dialog.close()

    def test_mc_setup_dialog_applies_channel_edits(self):
        dialog = MCSetupDialog(self.window)
        dialog.table.item(0, 1).setText("5.0 V")
        dialog.table.item(0, 2).setText("Bias")
        dialog.table.item(0, 3).setText("0.25")
        dialog.table.item(0, 4).setText("Ref")
        dialog.table.item(0, 5).setText("2")
        dialog.table.item(0, 6).setText("m/s^2")
        dialog.table.item(0, 7).setText("/Volt")
        dialog.table.item(0, 9).setText("1")

        dialog._apply_to_main()

        self.assertEqual(self.window.channel_table.item(0, 9).text(), "5")
        self.assertEqual(self.window.channel_table.item(0, 6).text(), "bias")
        self.assertEqual(self.window.channel_table.item(0, 11).text(), "0.25")
        self.assertEqual(self.window.channel_table.item(0, 10).text(), "Ref")
        self.assertEqual(self.window.channel_table.item(0, 7).text(), "2")
        self.assertEqual(self.window.channel_table.item(0, 8).text(), "m/s^2")
        self.assertEqual(self.window.channel_grid.item(0, 2).text(), "5")
        self.assertEqual(self.window.channel_grid.item(0, 3).text(), "bias")
        self.assertEqual(self.window.channel_grid.item(0, 5).text(), "Ref")
        self.assertEqual(self.controller.state.session.ai_channels[0].label, "Ref")
        dialog.close()

    def test_mc_setup_channel_enable_checkbox_matches_legacy_on_off(self):
        dialog = MCSetupDialog(self.window)
        self.assertFalse(dialog.table.item(0, 0).flags() & QtCore.Qt.ItemIsEnabled)
        self.assertEqual(dialog.table.item(0, 0).checkState(), QtCore.Qt.Checked)

        dialog.table.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
        dialog._apply_to_main()

        self.assertEqual(self.window.channel_table.item(0, 0).checkState(), QtCore.Qt.Checked)
        self.assertEqual(self.window.channel_table.item(1, 0).checkState(), QtCore.Qt.Unchecked)
        self.assertTrue(self.controller.state.session.ai_channels[0].enabled)
        self.assertFalse(self.controller.state.session.ai_channels[1].enabled)
        dialog.close()

    def test_time_plot_auto_y_range_tracks_channel_full_scale(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "Excitation": np.array([0.0, 0.1, -0.1, 0.0], dtype=float)
        }
        measurement.metadata["frame_index"] = 0
        self.window.channel_table.item(0, 10).setText("Excitation")
        self.window.channel_grid.item(0, 5).setText("Excitation")
        self.controller.state.session.ai_channels[0].label = "Excitation"
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window.top_trace_list.clear()
        self.window._manual_y_ranges["top"] = None

        self.window._plot_measurement(measurement)
        initial_y = self.window.top_plot.viewRange()[1]

        self.window.channel_list.setCurrentRow(0)
        self.window.channel_full_scale_combo.setCurrentText("1.25 V")
        self.window._apply_channel_editor_to_row()
        updated_y = self.window.top_plot.viewRange()[1]

        self.assertLessEqual(updated_y[0], -1.5625)
        self.assertGreaterEqual(updated_y[1], 1.5625)
        self.assertGreater(abs(updated_y[1]), abs(initial_y[1]))
        self.assertEqual(self.controller.state.session.ai_channels[0].full_scale, 1.25)

    def test_time_plot_recovers_when_checked_trace_uses_old_channel_name(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "Excitation": np.array([0.0, 0.1, -0.1, 0.0], dtype=float)
        }
        self.window.channel_table.item(0, 10).setText("Excitation")
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._sync_trace_list_items(self.window.top_trace_list, "top", ["ai0"])

        self.window._plot_measurement(measurement)

        self.assertIn("Excitation", self.window._last_plot_cache["top"])

    def test_channel_full_scale_change_resets_manual_time_y_range(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "Excitation": np.array([0.0, 0.1, -0.1, 0.0], dtype=float)
        }
        self.window.channel_table.item(0, 10).setText("Excitation")
        self.controller.state.session.ai_channels[0].label = "Excitation"
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._manual_y_ranges["top"] = (-0.2, 0.2)
        self.window._plot_measurement(measurement)

        self.window.channel_list.setCurrentRow(0)
        self.window.channel_full_scale_combo.setCurrentText("625 mV")
        self.window._apply_channel_editor_to_row()

        y_range = self.window.top_plot.viewRange()[1]
        self.assertIsNone(self.window._manual_y_ranges["top"])
        self.assertLessEqual(y_range[0], -0.78125)
        self.assertGreaterEqual(y_range[1], 0.78125)
        self.assertAlmostEqual(self.controller.state.session.ai_channels[0].full_scale, 0.625)

    def test_time_auto_scale_uses_data_limits_not_channel_full_scale(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float)
        }
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._manual_y_ranges["top"] = (-10.0, 10.0)
        self.window._plot_measurement(measurement)

        self.window._auto_scale_plot_xy("top")

        y_range = self.window.top_plot.viewRange()[1]
        self.assertLess(abs(y_range[0]), 1.0)
        self.assertLess(abs(y_range[1]), 1.0)
        self.assertIsNone(self.window._channel_full_scale_focus["top"])

    def test_time_full_scale_range_follows_active_channel_even_when_all_channels_visible(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
            "ai2": np.array([0.0, 0.3, -0.3, 0.0], dtype=float),
            "ai3": np.array([0.0, 0.4, -0.4, 0.0], dtype=float),
        }
        for row, full_scale in enumerate((10.0, 5.0, 2.5, 1.25)):
            self.window.channel_table.item(row, 9).setText(f"{full_scale:g}")
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._manual_y_ranges["top"] = (-0.2, 0.2)
        self.window._plot_measurement(measurement)

        self.window.channel_list.setCurrentRow(3)
        self.window.channel_full_scale_combo.setCurrentText("625 mV")

        y_range = self.window.top_plot.viewRange()[1]
        self.assertIsNone(self.window._manual_y_ranges["top"])
        self.assertLessEqual(y_range[0], -0.78125)
        self.assertGreaterEqual(y_range[1], 0.78125)
        self.assertGreater(y_range[0], -2.0)
        self.assertLess(y_range[1], 2.0)

        self.window.channel_list.setCurrentRow(0)
        self.window.channel_full_scale_combo.setCurrentText("5 V")

        y_range = self.window.top_plot.viewRange()[1]
        self.assertLessEqual(y_range[0], -6.25)
        self.assertGreaterEqual(y_range[1], 6.25)
        self.assertGreater(y_range[0], -12.0)
        self.assertLess(y_range[1], 12.0)

    def test_time_full_scale_range_persists_across_live_plot_refresh(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
        }
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._plot_measurement(measurement)

        self.window.channel_list.setCurrentRow(1)
        self.window.channel_full_scale_combo.setCurrentText("625 mV")
        first_y_range = self.window.top_plot.viewRange()[1]

        refreshed = self._measurement()
        refreshed.time_data["channels"] = {
            "ai0": np.array([0.0, 0.12, -0.12, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.18, -0.18, 0.0], dtype=float),
        }
        self.window._plot_measurement(refreshed)

        refreshed_y_range = self.window.top_plot.viewRange()[1]
        self.assertLessEqual(first_y_range[0], -0.78125)
        self.assertGreaterEqual(first_y_range[1], 0.78125)
        self.assertAlmostEqual(refreshed_y_range[0], first_y_range[0], places=6)
        self.assertAlmostEqual(refreshed_y_range[1], first_y_range[1], places=6)

    def test_time_full_scale_without_explicit_change_uses_data_range_not_largest_visible_channel(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
            "ai2": np.array([0.0, 0.3, -0.3, 0.0], dtype=float),
            "ai3": np.array([0.0, 0.4, -0.4, 0.0], dtype=float),
        }
        for row, full_scale in enumerate((10.0, 5.0, 2.5, 0.625)):
            self.window.channel_table.item(row, 9).setText(f"{full_scale:g}")
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._active_trace_names["top"] = "ai3"
        self.window.channel_list.setCurrentRow(3)
        self.window._channel_full_scale_focus["top"] = None
        self.window._auto_y_follow_visible_x["top"] = False

        self.window._plot_measurement(measurement)

        y_range = self.window.top_plot.viewRange()[1]
        self.assertGreater(y_range[0], -1.0)
        self.assertLess(y_range[1], 1.0)

    def test_channel_setup_selection_does_not_change_y_range_without_full_scale_change(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
            "ai2": np.array([0.0, 0.3, -0.3, 0.0], dtype=float),
            "ai3": np.array([0.0, 0.4, -0.4, 0.0], dtype=float),
        }
        for row, full_scale in enumerate((10.0, 5.0, 2.5, 0.625)):
            self.window.channel_table.item(row, 9).setText(f"{full_scale:g}")
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._active_trace_names["top"] = "ai0"
        self.window.channel_list.setCurrentRow(3)
        self.window._channel_full_scale_focus["top"] = None
        self.window._auto_y_follow_visible_x["top"] = False

        self.window._plot_measurement(measurement)

        y_range = self.window.top_plot.viewRange()[1]
        self.assertGreater(y_range[0], -1.0)
        self.assertLess(y_range[1], 1.0)

    def test_inst_start_preserves_channel_full_scale_focus_for_live_refresh(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
        }
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._plot_measurement(measurement)
        self.window.channel_list.setCurrentRow(1)
        self.window.channel_full_scale_combo.setCurrentText("625 mV")
        first_y_range = self.window.top_plot.viewRange()[1]

        self.window._clear_runtime_axis_ranges()
        self.window._plot_measurement(measurement)

        refreshed_y_range = self.window.top_plot.viewRange()[1]
        self.assertEqual(self.window._channel_full_scale_focus["top"], "ai1")
        self.assertAlmostEqual(refreshed_y_range[0], first_y_range[0], places=6)
        self.assertAlmostEqual(refreshed_y_range[1], first_y_range[1], places=6)

    def test_trace_selection_does_not_change_channel_setup_current_channel(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
        }
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._plot_measurement(measurement)
        self.window.channel_list.setCurrentRow(0)

        self.window._trace_selection_changed("top", "ai1")

        self.assertEqual(self.window._active_trace_names["top"], "ai1")
        self.assertEqual(self.window.channel_list.currentRow(), 0)
        self.assertEqual(self.window.channel_select_combo.currentIndex(), 0)
        self.assertEqual(self.window.channel_name_edit.text(), "ai0")

    def test_clicking_curve_does_not_change_channel_setup_current_channel(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, -0.1, 0.0], dtype=float),
            "ai1": np.array([0.0, 0.2, -0.2, 0.0], dtype=float),
        }
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._plot_measurement(measurement)
        self.window.channel_list.setCurrentRow(0)

        self.window._set_active_trace("top", "ai1")

        self.assertEqual(self.window._active_trace_names["top"], "ai1")
        self.assertEqual(self.window.channel_list.currentRow(), 0)
        self.assertEqual(self.window.channel_select_combo.currentIndex(), 0)
        self.assertEqual(self.window.channel_name_edit.text(), "ai0")

    def test_channel_setup_uses_visible_channel_select_combo(self):
        self.assertFalse(self.window.channel_select_combo.isHidden())
        self.assertFalse(self.window.channel_select_combo.isEditable())
        self.assertGreaterEqual(self.window.channel_select_combo.count(), 4)

        self.window.channel_select_combo.setCurrentIndex(1)

        self.assertEqual(self.window.channel_list.currentRow(), 1)
        self.assertEqual(self.window.channel_name_edit.text(), "ai1")

    def test_channel_setup_select_combo_rebuilds_with_labels(self):
        self.window.channel_table.item(1, 10).setText("Resp")
        self.window._rebuild_channel_list()

        self.assertIn("ai1", self.window.channel_select_combo.itemText(1))
        self.assertIn("Resp", self.window.channel_select_combo.itemText(1))

    def test_mc_setup_full_scale_change_resets_manual_time_y_range(self):
        measurement = self._measurement()
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._manual_y_ranges["top"] = (-0.2, 0.2)
        self.window._plot_measurement(measurement)
        dialog = MCSetupDialog(self.window)

        dialog.table.item(0, 1).setText("625 mV")
        dialog._apply_to_main()

        y_range = self.window.top_plot.viewRange()[1]
        self.assertIsNone(self.window._manual_y_ranges["top"])
        self.assertLessEqual(y_range[0], -0.78125)
        self.assertGreaterEqual(y_range[1], 0.78125)
        self.assertAlmostEqual(self.controller.state.session.ai_channels[0].full_scale, 0.625)
        dialog.close()

    def test_mc_setup_keeps_at_least_one_channel_enabled(self):
        dialog = MCSetupDialog(self.window)
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled)
            item.setCheckState(QtCore.Qt.Unchecked)

        dialog._enforce_channel_enable_rules(2)

        self.assertEqual(dialog.table.item(0, 0).checkState(), QtCore.Qt.Checked)
        dialog.close()

    def test_worker_stop_request_does_not_block_ui_thread(self):
        controller = _SlowStopController()
        worker = AcquisitionWorker(controller, "Dev1")

        started = time.perf_counter()
        worker.request_stop()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        self.assertEqual(controller.request_stop_calls, 0)

    def test_worker_prestart_stop_requests_controller_from_worker_thread(self):
        class _Controller:
            def __init__(self):
                self.request_stop_calls = 0
                self.stop_calls = 0
                self.averaging = []

            def set_averaging_enabled(self, enabled):
                self.averaging.append(enabled)

            def configure(self, device_name=None):
                self.device_name = device_name

            def start(self):
                raise AssertionError("start should not run after stop")

            def read_and_process(self):
                raise AssertionError("read should not run after stop")

            def request_stop(self):
                self.request_stop_calls += 1

            def stop(self):
                self.stop_calls += 1

        controller = _Controller()
        worker = AcquisitionWorker(controller, "Dev1")

        worker.request_stop()
        worker.run()

        self.assertEqual(controller.request_stop_calls, 1)
        self.assertEqual(controller.stop_calls, 1)

    def test_worker_stop_before_backend_start_does_not_abort_during_configure(self):
        class _Controller:
            def __init__(self):
                self.abort_calls = 0
                self.stop_calls = 0
                self.request_stop_calls = 0
                self.configured = False
                self.started = False
                self.averaging = []

            def set_averaging_enabled(self, enabled):
                self.averaging.append(enabled)

            def configure(self, device_name=None):
                self.configured = True

            def start(self):
                self.started = True

            def read_and_process(self):
                raise AssertionError("read should not run after a pre-start stop")

            def abort(self):
                self.abort_calls += 1

            def stop(self):
                self.stop_calls += 1

            def request_stop(self):
                self.request_stop_calls += 1

        controller = _Controller()
        worker = AcquisitionWorker(controller, "Dev1")

        worker.request_stop()
        worker.run()

        self.assertTrue(controller.configured)
        self.assertFalse(controller.started)
        self.assertEqual(controller.abort_calls, 0)
        self.assertEqual(controller.stop_calls, 1)
        self.assertEqual(controller.request_stop_calls, 1)

    def test_avg_measurement_status_shows_average_progress(self):
        measurement = self._measurement()
        measurement.metadata.update(
            {
                "averaging_enabled": True,
                "average_count": 2,
                "average_target": 4,
            }
        )

        self.window._handle_worker_measurement(measurement)
        QtWidgets.QApplication.processEvents()

        self.assertIn("avg:2/4", self.window.run_info_label.text())
        self.assertIn("Avg frame 2/4 acquired", self.window.statusBar().currentMessage())

    def test_avg_measurement_status_hides_internal_zero_based_frame_number(self):
        measurement = self._measurement()
        measurement.metadata.update(
            {
                "frame_index": 0,
                "averaging_enabled": True,
                "average_count": 1,
                "average_target": 20,
            }
        )

        self.window._handle_worker_measurement(measurement)
        QtWidgets.QApplication.processEvents()

        self.assertIn("State: avg frame 1", self.window.run_info_label.text())
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Avg frame 1/20 acquired",
        )
        self.assertNotIn("Frame 0 acquired", self.window.statusBar().currentMessage())

    def test_rejected_avg_measurement_status_keeps_average_count(self):
        measurement = self._measurement()
        measurement.metadata.update(
            {
                "frame_index": 5,
                "averaging_enabled": True,
                "average_count": 1,
                "average_target": 20,
                "rejected": True,
                "double_hit_rejected": True,
            }
        )

        self.window._handle_worker_measurement(measurement)
        QtWidgets.QApplication.processEvents()

        self.assertIn("avg:1/20", self.window.run_info_label.text())
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Rejected frame 5 (double hit) | avg 1/20",
        )

    def test_double_hit_status_shows_reference_and_candidate_peak_count(self):
        measurement = self._measurement()
        measurement.metadata.update(
            {
                "frame_index": 5,
                "averaging_enabled": True,
                "average_count": 1,
                "average_target": 20,
                "rejected": True,
                "double_hit_rejected": True,
                "double_hit_reference_channel": "ai0",
                "double_hit_peak_count": 2,
            }
        )

        self.window._handle_worker_measurement(measurement)
        QtWidgets.QApplication.processEvents()

        self.assertIn("ref=ai0 peaks=2", self.window.run_info_label.text())
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Rejected frame 5 (double hit) | ref=ai0 peaks=2 | avg 1/20",
        )

    def test_avg_start_uses_current_average_count_widget_value(self):
        captured = {}
        original_worker = main_window_module.AcquisitionWorker
        original_thread = main_window_module.QtCore.QThread

        class _Worker(QtCore.QObject):
            started = QtCore.Signal(object)
            measurement_ready = QtCore.Signal(object)
            status_changed = QtCore.Signal(str)
            error = QtCore.Signal(str)
            finished = QtCore.Signal()

            def __init__(self, _controller, _device_name, **kwargs):
                super().__init__()
                captured.update(kwargs)

            def moveToThread(self, _thread):
                return None

            def run(self):
                return None

            def request_stop(self):
                return None

        class _Thread(QtCore.QObject):
            started = QtCore.Signal()
            finished = QtCore.Signal()

            def start(self):
                return None

            def quit(self):
                return None

            def deleteLater(self):
                return None

        try:
            main_window_module.AcquisitionWorker = _Worker
            main_window_module.QtCore.QThread = lambda _parent=None: _Thread()
            self.window.average_mode_combo.setCurrentText("linear")
            self.window.average_count_edit.setValue(20)

            self.window._start_average_acquisition()

            self.assertTrue(captured["average_run"])
            self.assertEqual(captured["target_average_count"], 20)
            self.assertAlmostEqual(captured["display_interval_seconds"], 1.0)
        finally:
            main_window_module.AcquisitionWorker = original_worker
            main_window_module.QtCore.QThread = original_thread

    def test_acquisition_display_interval_follows_frame_duration_with_limits(self):
        session = default_session_config()
        session.acquisition.sample_rate = 2560.0
        session.acquisition.frame_size = 4096
        self.assertAlmostEqual(
            self.window._acquisition_display_interval_seconds(session),
            1.0,
        )

        session.acquisition.sample_rate = 51200.0
        session.acquisition.frame_size = 4096
        self.assertAlmostEqual(
            self.window._acquisition_display_interval_seconds(session),
            0.25,
        )

        session.acquisition.sample_rate = 2560.0
        session.acquisition.frame_size = 2048
        self.assertAlmostEqual(
            self.window._acquisition_display_interval_seconds(session),
            0.8,
        )

    def test_avg_worker_never_discards_frames_before_averaging(self):
        class _Backend:
            def __init__(self):
                self.discard_reads = 0

            def read_frame(self):
                self.discard_reads += 1
                return object()

        class _Controller:
            def __init__(self):
                self.backend = _Backend()
                self.processed_reads = 0
                self.set_averaging_calls = []
                self.aborted = False
                self.stopped = False

            def set_averaging_enabled(self, enabled):
                self.set_averaging_calls.append(enabled)

            def configure(self, device_name=None):
                self.device_name = device_name

            def start(self):
                return None

            def read_and_process(self):
                self.processed_reads += 1
                measurement = self.window_measurement()
                measurement.metadata.update(
                    {
                        "averaging_enabled": True,
                        "average_count": self.processed_reads,
                        "average_target": 1,
                    }
                )
                return measurement

            def abort(self):
                self.aborted = True

            def stop(self):
                self.stopped = True

            @staticmethod
            def window_measurement():
                return MainWindowTests._measurement()

        controller = _Controller()
        worker = AcquisitionWorker(
            controller,
            "Dev1",
            average_run=True,
            target_average_count=1,
        )

        worker.run()

        self.assertEqual(controller.backend.discard_reads, 0)
        self.assertEqual(controller.processed_reads, 1)
        self.assertEqual(controller.set_averaging_calls[0], True)
        self.assertFalse(controller.aborted)
        self.assertTrue(controller.stopped)

    def test_worker_user_stop_does_not_close_controller_immediately(self):
        class _Controller:
            def __init__(self):
                self.stop_calls = 0
                self.close_calls = 0
                self.set_averaging_calls = []

            def set_averaging_enabled(self, enabled):
                self.set_averaging_calls.append(enabled)

            def configure(self, device_name=None):
                return None

            def start(self):
                return None

            def request_stop(self):
                return None

            def read_and_process(self):
                raise AssertionError("read should not run after user stop")

            def stop(self):
                self.stop_calls += 1

            def close(self):
                self.close_calls += 1

        controller = _Controller()
        worker = AcquisitionWorker(controller, "Dev1")

        worker.request_stop()
        worker.run()

        self.assertEqual(controller.stop_calls, 1)
        self.assertEqual(controller.close_calls, 0)

    def test_worker_normal_completion_closes_controller(self):
        class _Controller:
            def __init__(self):
                self.stop_calls = 0
                self.close_calls = 0
                self.reads = 0

            def set_averaging_enabled(self, _enabled):
                return None

            def configure(self, device_name=None):
                return None

            def start(self):
                return None

            def read_and_process(self):
                self.reads += 1
                measurement = MainWindowTests._measurement()
                measurement.metadata.update(
                    {
                        "averaging_enabled": True,
                        "average_count": 1,
                        "average_target": 1,
                    }
                )
                return measurement

            def stop(self):
                self.stop_calls += 1

            def close(self):
                self.close_calls += 1

        controller = _Controller()
        worker = AcquisitionWorker(
            controller,
            "Dev1",
            average_run=True,
            target_average_count=1,
        )

        worker.run()

        self.assertEqual(controller.stop_calls, 1)
        self.assertEqual(controller.close_calls, 1)

    def test_inst_start_bypasses_trigger_and_clears_imported_axis_ranges(self):
        captured = {}
        original_worker = main_window_module.AcquisitionWorker
        original_thread = main_window_module.QtCore.QThread

        class _Worker(QtCore.QObject):
            started = QtCore.Signal(object)
            measurement_ready = QtCore.Signal(object)
            status_changed = QtCore.Signal(str)
            error = QtCore.Signal(str)
            finished = QtCore.Signal()

            def __init__(self, controller, _device_name, **kwargs):
                super().__init__()
                captured["trigger_enabled"] = controller.state.session.acquisition.trigger.enabled
                captured["trigger_source"] = controller.state.session.acquisition.trigger.source
                captured.update(kwargs)

            def moveToThread(self, _thread):
                return None

            def run(self):
                return None

            def request_stop(self):
                return None

        class _Thread(QtCore.QObject):
            started = QtCore.Signal()
            finished = QtCore.Signal()

            def start(self):
                return None

            def quit(self):
                return None

            def deleteLater(self):
                return None

        try:
            main_window_module.AcquisitionWorker = _Worker
            main_window_module.QtCore.QThread = lambda _parent=None: _Thread()
            self.window.trigger_mode_combo.setCurrentText("Every Frame")
            self.window._manual_x_ranges["top"] = (10.0, 20.0)
            self.window._manual_y_ranges["bottom"] = (-70.0, 30.0)

            self.window._start_acquisition()

            self.assertFalse(captured["trigger_enabled"])
            self.assertEqual(captured["trigger_source"], "immediate")
            self.assertFalse(captured["average_run"])
            self.assertIsNone(self.window._manual_x_ranges["top"])
            self.assertIsNone(self.window._manual_y_ranges["bottom"])
        finally:
            main_window_module.AcquisitionWorker = original_worker
            main_window_module.QtCore.QThread = original_thread

    def test_inst_start_after_stop_allows_measurement_updates(self):
        captured = {}
        original_worker = main_window_module.AcquisitionWorker
        original_thread = main_window_module.QtCore.QThread

        class _Worker(QtCore.QObject):
            started = QtCore.Signal(object)
            measurement_ready = QtCore.Signal(object)
            status_changed = QtCore.Signal(str)
            error = QtCore.Signal(str)
            finished = QtCore.Signal()

            def __init__(self, _controller, _device_name, **kwargs):
                super().__init__()
                captured.update(kwargs)

            def moveToThread(self, _thread):
                return None

            def run(self):
                return None

            def request_stop(self):
                return None

        class _Thread(QtCore.QObject):
            started = QtCore.Signal()
            finished = QtCore.Signal()

            def start(self):
                return None

            def quit(self):
                return None

            def deleteLater(self):
                return None

        try:
            main_window_module.AcquisitionWorker = _Worker
            main_window_module.QtCore.QThread = lambda _parent=None: _Thread()
            self.window._stop_requested_for_current_run = True

            self.window._start_acquisition()
            self.window._handle_worker_measurement(self._measurement())
            QtWidgets.QApplication.processEvents()

            self.assertFalse(self.window._stop_requested_for_current_run)
            self.assertIn("Frame", self.window.statusBar().currentMessage())
            self.assertIn("acquired", self.window.statusBar().currentMessage())
        finally:
            main_window_module.AcquisitionWorker = original_worker
            main_window_module.QtCore.QThread = original_thread

    def test_record_button_starts_continuous_recording_in_selected_folder(self):
        captured = {}
        original_worker = main_window_module.ContinuousRecordingWorker
        original_thread = main_window_module.QtCore.QThread

        class _Worker(QtCore.QObject):
            started = QtCore.Signal(object)
            preview_ready = QtCore.Signal(object)
            status_changed = QtCore.Signal(str)
            recording_status = QtCore.Signal(object)
            error = QtCore.Signal(str)
            finished = QtCore.Signal()

            def __init__(self, _controller, _device_name, output_dir, **kwargs):
                super().__init__()
                captured["output_dir"] = Path(output_dir)
                captured.update(kwargs)

            def moveToThread(self, _thread):
                return None

            def run(self):
                return None

            def request_stop(self):
                return None

        class _Thread(QtCore.QObject):
            started = QtCore.Signal()
            finished = QtCore.Signal()

            def start(self):
                return None

            def quit(self):
                return None

            def deleteLater(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                main_window_module.ContinuousRecordingWorker = _Worker
                main_window_module.QtCore.QThread = lambda _parent=None: _Thread()
                with mock.patch.object(
                    QtWidgets.QFileDialog,
                    "getExistingDirectory",
                    return_value=tmpdir,
                ):
                    self.window._start_continuous_recording()

                self.assertEqual(captured["output_dir"].parent, Path(tmpdir))
                self.assertTrue(captured["output_dir"].name.startswith("recording_"))
                self.assertFalse(self.window.start_button.isEnabled())
                self.assertFalse(self.window.avg_button.isEnabled())
                self.assertFalse(self.window.record_button.isEnabled())
                self.assertTrue(self.window.stop_button.isEnabled())
            finally:
                main_window_module.ContinuousRecordingWorker = original_worker
                main_window_module.QtCore.QThread = original_thread

    def test_recording_status_updates_elapsed_segment_and_samples(self):
        status = RecordingStatus(
            output_dir=Path("D:/records/session"),
            manifest_path=Path("D:/records/session/manifest.json"),
            segment_index=2,
            elapsed_seconds=65.0,
            total_samples=12345,
            total_frames=3,
        )

        self.window._handle_recording_status(status)

        self.assertIn("00:01:05", self.window.run_info_label.text())
        self.assertIn("segment 2", self.window.run_info_label.text())
        self.assertIn("samples 12345", self.window.run_info_label.text())

    def test_continuous_recording_worker_writes_frames_without_processing(self):
        class _Backend:
            def __init__(self):
                self.reads = 0

            def read_frame(self):
                self.reads += 1
                if self.reads > 2:
                    raise RuntimeError("stop test")
                return BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0", "ai1"],
                    data=np.ones((2, 4), dtype=float) * self.reads,
                    timestamps=np.arange(4, dtype=float) / 2560.0,
                    frame_index=self.reads,
                    metadata={},
                )

        class _Controller:
            def __init__(self):
                self.backend = _Backend()
                self.state = type("State", (), {"session": default_session_config()})()
                self.processed_reads = 0
                self.aborted = False
                self.stopped = False

            def set_averaging_enabled(self, _enabled):
                return None

            def configure(self, device_name=None):
                self.device_name = device_name

            def start(self):
                return None

            def read_and_process(self):
                self.processed_reads += 1
                return MainWindowTests._measurement()

            def abort(self):
                self.aborted = True

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            controller = _Controller()
            worker = main_window_module.ContinuousRecordingWorker(
                controller,
                "Dev1",
                output_dir,
                preview_interval_seconds=0.0,
            )

            worker.run()

            self.assertEqual(controller.processed_reads, 0)
            self.assertFalse(controller.aborted)
            self.assertTrue(controller.stopped)
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "segment_0001.dat").exists())

    def test_continuous_recording_worker_creates_files_before_first_frame(self):
        class _Backend:
            def read_frame(self):
                raise RuntimeError("no frame")

        class _Controller:
            def __init__(self):
                self.backend = _Backend()
                self.state = type("State", (), {"session": default_session_config()})()

            def set_averaging_enabled(self, _enabled):
                return None

            def configure(self, device_name=None):
                self.device_name = device_name

            def start(self):
                return None

            def abort(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            worker = main_window_module.ContinuousRecordingWorker(
                _Controller(),
                "Dev1",
                output_dir,
            )

            worker.run()

            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "segment_0001.dat").exists())

    def test_acquisition_tab_contains_legacy_style_trigger_panel(self):
        groups = {
            group.title(): group
            for group in self.window.findChildren(QtWidgets.QGroupBox)
        }

        self.assertEqual(self.window.left_panel.objectName(), "legacyLeftPanel")
        self.assertNotIn("TRIGGER", groups)
        legacy_titles = [
            label.text()
            for label in self.window.left_panel.findChildren(QtWidgets.QLabel)
            if label.objectName() == "legacyPanelTitle"
        ]
        self.assertIn("TRIGGER", legacy_titles)
        self.assertEqual(
            [
                self.window.trigger_mode_combo.itemData(index)
                for index in range(self.window.trigger_mode_combo.count())
            ],
            [
                "Off (Free Run)",
                "Every Frame",
                "1st Frame",
                "Manual Arm",
                "1st-Manual Arm",
            ],
        )
        self.assertEqual(self.window.trigger_mode_combo.currentText(), "Free Run")
        self.assertEqual(self.window.trigger_mode_combo.currentData(), "Off (Free Run)")
        self.assertEqual(self.window.trigger_source_combo.currentText(), "Ch1")
        self.assertEqual(self.window.trigger_level_percent_combo.currentText(), "0%")
        self.assertEqual(self.window.trigger_level_percent_combo.count(), 17)
        self.assertEqual(self.window.trigger_level_percent_combo.itemText(0), "71%")
        self.assertEqual(self.window.trigger_level_percent_combo.itemText(16), "-71%")
        self.assertEqual(self.window.trigger_slope_button.text(), "Pos")
        self.assertEqual(self.window.trigger_enable.text(), "Arm")
        self.assertFalse(self.window.trigger_enable.isEnabled())
        session = self.window._read_session_from_widgets()
        self.assertFalse(session.acquisition.trigger.enabled)
        self.assertEqual(session.acquisition.trigger.source, "immediate")

        self.window.trigger_mode_combo.setCurrentText("1st Frame")
        self.window.trigger_source_combo.setCurrentText("Ch2")
        self.window.trigger_level_percent_combo.setCurrentText("35%")
        self.window.trigger_slope_button.click()
        self.assertEqual(self.window.trigger_slope_combo.currentText(), "Neg")

        session = self.window._read_session_from_widgets()

        self.assertTrue(session.acquisition.trigger.enabled)
        self.assertEqual(session.acquisition.trigger.mode, "1st Frame")
        self.assertEqual(session.acquisition.trigger.source, "ai1")
        self.assertEqual(session.acquisition.trigger.level_percent, 35.0)
        self.assertAlmostEqual(session.acquisition.trigger.level, 3.5)
        self.assertEqual(session.acquisition.trigger.slope, "falling")
        self.window.trigger_mode_combo.setCurrentText("Manual Arm")
        session = self.window._read_session_from_widgets()
        self.assertFalse(session.acquisition.trigger.enabled)
        self.assertEqual(session.acquisition.trigger.source, "immediate")
        self.assertTrue(self.window.trigger_enable.isEnabled())
        self.window.trigger_enable.click()
        self.assertEqual(self.window.trigger_enable.text(), "Armed")
        session = self.window._read_session_from_widgets()
        self.assertTrue(session.acquisition.trigger.enabled)
        self.assertEqual(session.acquisition.trigger.mode, "Manual Arm")
        self.window.trigger_mode_combo.setCurrentIndex(
            self.window.trigger_mode_combo.findData("Off (Free Run)")
        )
        session = self.window._read_session_from_widgets()
        self.assertFalse(session.acquisition.trigger.enabled)
        self.assertEqual(session.acquisition.trigger.source, "immediate")
        self.window.double_hit_delay_edit.setValue(0.2)
        session = self.window._read_session_from_widgets()
        self.assertAlmostEqual(session.acquisition.modal.double_hit_delay_fraction, 0.2)

    def test_legacy_left_controls_have_real_bindings_or_are_disabled(self):
        self.window.bandwidth_combo.setCurrentText("BW=2.0KHz")
        self.assertEqual(self.window.sample_rate_edit.value(), 5120.0)

        self.window.reject_combo.setCurrentText("Double Hit Reject")
        session = self.window._read_session_from_widgets()
        self.assertTrue(session.acquisition.modal.reject_double_hit)
        self.assertFalse(session.acquisition.modal.reject_overload)
        self.assertFalse(session.acquisition.modal.enabled)

        self.window.reject_combo.setCurrentText("Overload Reject")
        session = self.window._read_session_from_widgets()
        self.assertFalse(session.acquisition.modal.reject_double_hit)
        self.assertTrue(session.acquisition.modal.reject_overload)

        self.window.reject_combo.setCurrentText("Both Reject")
        session = self.window._read_session_from_widgets()
        self.assertTrue(session.acquisition.modal.reject_double_hit)
        self.assertTrue(session.acquisition.modal.reject_overload)

        self.window.window_combo.setCurrentText("Hanning")
        session = self.window._read_session_from_widgets()
        self.assertEqual(session.acquisition.processing_window, "hanning")
        self.window.window_combo.setCurrentText("FlatTop")
        session = self.window._read_session_from_widgets()
        self.assertEqual(session.acquisition.processing_window, "flattop")
        self.assertFalse(self.window.aa_filters_button.isEnabled())
        self.assertTrue(self.window.overlap_combo.isEnabled())

        self.window.overlap_combo.setCurrentText("Max Overlap")
        session = self.window._read_session_from_widgets()
        self.assertEqual(session.acquisition.overlap_percent, 100)

    def test_reject_combo_displays_both_reject_when_both_flags_are_loaded(self):
        self.window.session.acquisition.modal.reject_double_hit = True
        self.window.session.acquisition.modal.reject_overload = True

        self.window._load_session_to_widgets()

        self.assertEqual(self.window.reject_combo.currentText(), "Both Reject")

    def test_modal_parameters_match_legacy_percent_ranges(self):
        self.assertEqual(self.window.double_hit_threshold_edit.minimum(), 0.01)
        self.assertEqual(self.window.double_hit_threshold_edit.maximum(), 1.0)
        self.assertAlmostEqual(self.window.double_hit_delay_edit.minimum(), 0.2)
        self.assertAlmostEqual(self.window.double_hit_delay_edit.maximum(), 0.5)
        self.assertAlmostEqual(self.window.force_window_fraction_edit.minimum(), 0.01)
        self.assertAlmostEqual(self.window.force_window_fraction_edit.maximum(), 1.0)
        self.assertAlmostEqual(self.window.exp_window_decay_edit.minimum(), 0.001)
        self.assertAlmostEqual(self.window.exp_window_decay_edit.maximum(), 1.0)

    def test_modal_parameters_dialog_uses_readable_legacy_style_and_spin_arrows(self):
        captured: dict[str, QtWidgets.QDialog] = {}
        original_exec = QtWidgets.QDialog.exec

        def fake_exec(dialog):
            captured["dialog"] = dialog
            return 0

        with mock.patch.object(QtWidgets.QDialog, "exec", fake_exec):
            self.window._open_modal_parameters_dialog()

        dialog = captured["dialog"]
        stylesheet = dialog.styleSheet()
        self.assertNotIn("background: #d0d0d0", stylesheet)
        self.assertNotIn("color: #000000", stylesheet.split("QDoubleSpinBox", 1)[0])
        self.assertIn("QDoubleSpinBox::up-button", stylesheet)
        self.assertIn("QDoubleSpinBox::down-button", stylesheet)
        spin_boxes = dialog.findChildren(QtWidgets.QDoubleSpinBox)
        self.assertTrue(spin_boxes)
        self.assertTrue(all(spin.buttonSymbols() == QtWidgets.QAbstractSpinBox.UpDownArrows for spin in spin_boxes))
        self.assertGreaterEqual(dialog.minimumWidth(), 380)
        self.assertGreaterEqual(dialog.minimumHeight(), 170)
        self.assertTrue(all(spin.width() >= 78 for spin in spin_boxes))

    def test_setup_dialog_fits_compact_controls_without_clipping(self):
        captured: dict[str, QtWidgets.QDialog] = {}

        def fake_exec(dialog):
            dialog.show()
            QtWidgets.QApplication.processEvents()
            captured["dialog"] = dialog
            return 0

        with mock.patch.object(QtWidgets.QDialog, "exec", fake_exec):
            self.window._open_setup_page_dialog("Setup", self.window.excitation_setup_page)

        dialog = captured["dialog"]
        self.assertGreaterEqual(dialog.minimumWidth(), self.window.excitation_setup_page.sizeHint().width() + 32)
        self.assertGreaterEqual(dialog.minimumHeight(), 190)
        clipped_children = [
            child
            for child in self.window.excitation_setup_page.findChildren(QtWidgets.QWidget)
            if child.isVisible() and child.height() + 1 < child.sizeHint().height()
        ]
        self.assertEqual(clipped_children, [])

    def test_channel_legacy_numeric_editors_use_compact_text(self):
        self.window.channel_sensitivity_edit.setValue(1.0)
        self.window.channel_db_ref_edit.setValue(1.0)
        self.assertEqual(self.window.channel_sensitivity_edit.text(), "1")
        self.assertEqual(self.window.channel_db_ref_edit.text(), "1")

    def test_legacy_left_panel_keeps_unit_and_record_length_values_readable(self):
        self.assertEqual(self.window.channel_sensitivity_edit.width(), 48)
        self.assertGreaterEqual(self.window.channel_unit_edit.minimumWidth(), 76)
        self.assertEqual(self.window.channel_sensitivity_edit.buttonSymbols(), QtWidgets.QAbstractSpinBox.NoButtons)
        self.assertEqual(self.window.frame_size_edit.width(), 66)
        self.assertEqual(self.window.frame_size_edit.buttonSymbols(), QtWidgets.QAbstractSpinBox.NoButtons)
        self.assertGreaterEqual(
            self.window.frame_size_edit.width(),
            self.window.frame_size_edit.fontMetrics().horizontalAdvance("65536") + 10,
        )

    def test_plot_area_uses_matlab_style_axis_layout(self):
        self.assertEqual(self.window.top_plot.getPlotItem().titleLabel.text, "Upper")
        self.assertEqual(self.window.bottom_plot.getPlotItem().titleLabel.text, "Lower")
        self.assertEqual(self.window.top_plot.backgroundBrush().color().name(), "#000000")
        self.assertEqual(self.window.bottom_plot.backgroundBrush().color().name(), "#000000")
        self.assertEqual(self.window.TRACE_COLORS[:4], ["#56c7ff", "#ffd166", "#45e6a8", "#ff6b8a"])

    def test_log_axis_tick_labels_use_plain_values(self):
        axis = VnaAxisItem(orientation="bottom")
        axis.setLogMode(True)
        labels = axis.tickStrings(
            [np.log10(1.0), np.log10(5.0), np.log10(10.0), np.log10(50.0), np.log10(1000.0)],
            1.0,
            1.0,
        )
        self.assertEqual(labels, ["1", "", "10", "", "1k"])

    def test_y_axis_tick_labels_include_scientific_notation_directly(self):
        axis = VnaAxisItem(orientation="left")
        labels = axis.tickStrings([0.0, 1.25e-6, -2.5e5, 12.5], 1.0, 1.0)
        self.assertEqual(labels, ["0", "1.250e-6", "-2.500e+5", "12.5"])

    def test_overlay_capture_and_clear(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window._plot_measurement(measurement)
        self.window._set_active_trace("top", "ai1")
        self.window._capture_top_overlay()
        self.assertEqual(len(self.window._stored_overlays["top"]), 1)
        self.assertEqual(list(self.window._stored_overlays["top"][0].keys()), ["ai1"])
        self.assertTrue(self.window.overlay_checkbox.isChecked())
        self.assertTrue(self.window.overlay_action.isChecked())
        self.window._clear_overlays()
        self.assertEqual(len(self.window._stored_overlays["top"]), 0)
        self.assertEqual(len(self.window._stored_overlays["bottom"]), 0)

    def test_overlay_persists_when_new_session_is_loaded(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window._plot_measurement(measurement)
        self.window._capture_top_overlay()
        self.assertEqual(len(self.window._stored_overlays["top"]), 1)

        self.window.session = default_session_config()
        self.window.session.acquisition.overlay_enabled = False
        self.window._load_session_to_widgets()

        self.assertTrue(self.window.overlay_checkbox.isChecked())
        self.assertEqual(len(self.window._stored_overlays["top"]), 1)

    def test_overlay_draws_on_new_measurement_without_re_capture(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window._plot_measurement(measurement)
        self.window._capture_top_overlay()
        item_count_with_overlay = len(self.window.top_plot.listDataItems())

        new_measurement = self._measurement()
        new_measurement.time_data["channels"]["ai0"] = np.asarray(
            new_measurement.time_data["channels"]["ai0"]
        ) * 0.25
        self.controller.state.measurement = new_measurement
        self.window._plot_measurement(new_measurement)

        self.assertEqual(len(self.window._stored_overlays["top"]), 1)
        self.assertGreaterEqual(len(self.window.top_plot.listDataItems()), item_count_with_overlay)

    def test_save_to_default_uses_legacy_default_vna_path(self):
        self.assertEqual(
            self.window._default_vna_path(),
            Path("D:/SynologyDrive/codex/vna/dsa/vna/default.vna"),
        )

    def test_log_scale_filters_zero_frequency(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_xscale_combo.setCurrentText("log")
        self.window._plot_measurement(measurement)
        cached = self.window._last_plot_cache["top"]["ai0->ai1"][0]
        self.assertTrue(np.all(cached > 0.0))

    def test_marker_readout_reports_delta(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window._plot_measurement(measurement)
        self.window._toggle_markers(True)
        self.window._marker_positions["top"] = [10.0, 40.0]
        self.window._refresh_markers("top")
        self.window._update_marker_readout("top")
        label = self.window.top_marker_label.text()
        self.assertIn("dX=30", label)
        self.assertIn("dY=", label)
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "10")
        self.assertEqual(self.window.top_marker_fields["x2"].text(), "40")
        self.assertEqual(self.window.top_marker_fields["dx"].text(), "30")

    def test_trace_selector_tracks_active_trace(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai1")
        )
        self.assertEqual(self.window._active_trace_names["top"], "ai1")
        self.assertEqual(self.window.top_trace_combo.currentData(), "ai1")
        self.assertEqual(self.window.top_trace_combo.currentText(), "Ch 2")
        self.assertEqual(self.window.top_trace_strip_combo.currentData(), "ai1")
        self.assertEqual(self.window.top_trace_strip_combo.currentText(), "Ch 2")

    def test_chan_sel_and_legend_use_mc_setup_labels_for_single_channel_plots(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.channel_table.item(0, 10).setText("Reference A")
        self.window.channel_table.item(1, 10).setText("Response B")
        self.window._rebuild_channel_list()
        self.window._reload_channel_selectors()
        self.window.top_display_combo.setCurrentText("time")

        self.window._plot_measurement(measurement)

        self.assertEqual(self.window.top_trace_combo.findText("ai0"), -1)
        self.assertEqual(self.window.top_trace_combo.itemText(0), "Reference A")
        self.assertEqual(self.window.top_trace_combo.itemData(0), "ai0")
        self.assertEqual(self.window.top_trace_list.item(1).text(), "2  Response B")
        legend_labels = [
            label.text
            for _sample, label in self.window.top_plot.plotItem.legend.items
        ]
        self.assertIn("Reference A", legend_labels)
        self.assertIn("Response B", legend_labels)
        self.assertNotIn("ai0", legend_labels)

    def test_relation_trace_selector_displays_response_channel_only(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window._plot_measurement(measurement)

        self.assertEqual(self.window._active_trace_names["top"], "ai0->ai1")
        self.assertEqual(self.window.top_trace_combo.currentData(), "ai0->ai1")
        self.assertEqual(self.window.top_trace_combo.currentText(), "Ch 2")
        self.assertNotIn("->", self.window.top_trace_combo.currentText())
        self.assertEqual(self.window.top_trace_strip_combo.currentData(), "ai0->ai1")
        self.assertEqual(self.window.top_trace_strip_combo.currentText(), "Ch 2")
        self.assertNotIn("->", self.window.top_trace_strip_combo.currentText())
        self.assertEqual(self.window.top_trace_list.item(0).data(QtCore.Qt.UserRole), "ai0->ai1")
        self.assertEqual(self.window.top_trace_list.item(0).text(), "1  Ch 2")

    def test_enabled_channels_are_added_to_response_selection_before_acquisition(self):
        self.window.session.acquisition.response_channels = ["ai1"]
        for row in range(4):
            self.window.channel_table.item(row, 0).setCheckState(QtCore.Qt.Checked)

        self.window._reload_channel_selectors(include_new_responses=True)
        session = self.window._read_session_from_widgets()

        self.assertEqual(session.acquisition.reference_channel, "ai0")
        self.assertEqual(session.acquisition.response_channels, ["ai1", "ai2", "ai3"])

    def test_xfer_and_coherence_plot_all_available_response_traces_after_acquisition_prepare(self):
        freq_axis = np.array([1.0, 2.0, 4.0, 8.0], dtype=float)
        relation_values = {
            f"ai0->ai{index}": np.full(freq_axis.shape, index + 0.0j, dtype=complex)
            for index in (1, 2, 3)
        }
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.arange(4, dtype=float) / 100.0,
                "channels": {f"ai{index}": np.ones(4) * index for index in range(4)},
            },
            spectra={"f": freq_axis, "fft": {}, "autospectrum": {}},
            frf=relation_values,
            coherence={name: np.linspace(0.1, 0.9, 4) for name in relation_values},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )
        self.controller.state.measurement = measurement
        self.window.session.acquisition.response_channels = ["ai1"]
        for row in range(4):
            self.window.channel_table.item(row, 0).setCheckState(QtCore.Qt.Checked)
        self.window.bottom_display_combo.setCurrentText("xfer")
        self.window._reload_channel_selectors(include_new_responses=True)
        self.window._read_session_from_widgets()
        self.window._prepare_trace_selection_for_acquisition()

        self.window._plot_measurement(measurement)

        self.assertEqual(
            set(self.window._last_plot_cache["bottom"]),
            {"ai0->ai1", "ai0->ai2", "ai0->ai3"},
        )
        self.window.bottom_display_combo.setCurrentText("coh")
        self.window._prepare_trace_selection_for_acquisition()
        self.window._plot_measurement(measurement)
        self.assertEqual(
            set(self.window._last_plot_cache["bottom"]),
            {"ai0->ai1", "ai0->ai2", "ai0->ai3"},
        )

    def test_current_plot_window_uses_existing_upper_lower_plot_cache(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window.bottom_display_combo.setCurrentText("coh")
        self.window._plot_measurement(measurement)

        self.window._open_current_plot_window()

        upper_items = self.window._detached_plot_window.upper_plot.listDataItems()
        lower_items = self.window._detached_plot_window.lower_plot.listDataItems()
        self.assertGreaterEqual(len(upper_items), 2)
        self.assertEqual(len(lower_items), 1)
        self.assertFalse(self.window._detached_plot_window.upper_plot.getPlotItem().menuEnabled())
        self.assertFalse(self.window._detached_plot_window.lower_plot.getPlotItem().menuEnabled())
        np.testing.assert_allclose(
            upper_items[0].getData()[0],
            self.window._last_plot_cache["top"]["ai0"][0],
        )
        np.testing.assert_allclose(
            lower_items[0].getData()[1],
            self.window._last_plot_cache["bottom"]["ai0->ai1"][1],
        )
        self.assertIn("Coherence", self.window._detached_plot_window.lower_plot.getAxis("left").labelText)

    def test_current_plot_window_is_not_forced_above_main_window(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        detached = self.window._detached_plot_window

        with mock.patch.object(detached, "raise_") as raise_mock, mock.patch.object(
            detached, "activateWindow"
        ) as activate_mock:
            self.window._open_current_plot_window()

        self.assertIsNone(detached.parent())
        raise_mock.assert_not_called()
        activate_mock.assert_not_called()

    def test_analysis_viewer_opens_as_independent_window_not_child_modal(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window._open_analysis_viewer()

        viewer = self.window._analysis_viewer
        self.assertIsNotNone(viewer)
        self.assertIsNone(viewer.parent())
        self.assertFalse(viewer.isModal())
        self.assertEqual(viewer.dataset_list.count(), 1)
        self.assertIn("Current Measurement", viewer.dataset_list.item(0).text())
        viewer.close()

    def test_current_plot_window_context_menu_supports_data_tips(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._open_current_plot_window()
        detached = self.window._detached_plot_window
        scene_pos = detached.upper_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.1, 1.0))

        menu, actions = detached._build_plot_context_menu("top", scene_pos)
        try:
            action_texts = [
                action.text()
                for action in menu.actions()
                if not action.isSeparator()
            ]
            self.assertEqual(action_texts, ["Auto Scale", "Data Tip", "Clear Data Tips"])
            self.assertTrue(actions["data_tip"].isCheckable())
            self.assertFalse(actions["data_tip"].isChecked())
            self.assertTrue(actions["data_tip"].isEnabled())
            self.assertFalse(actions["clear_data_tips"].isEnabled())
        finally:
            menu.close()

        detached.data_tip_button.setChecked(True)
        scene_pos = detached.upper_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.1, 1.0))
        click_x, click_y = detached._scene_to_data_point("top", scene_pos)
        placed = detached._place_data_tip("top", click_x, click_y)

        self.assertTrue(placed)
        self.assertTrue(detached._data_tip_enabled)
        self.assertEqual(len(detached._data_tip_items["top"]), 1)
        menu, actions = detached._build_plot_context_menu("top", scene_pos)
        try:
            self.assertTrue(actions["data_tip"].isChecked())
            self.assertTrue(actions["clear_data_tips"].isEnabled())
        finally:
            menu.close()

    def test_current_plot_window_follows_main_theme(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        self.window._set_theme("light")
        self.window._open_current_plot_window()
        detached = self.window._detached_plot_window

        self.assertIn("background: #f4f7fb;", detached.styleSheet())
        self.assertEqual(detached.upper_plot.backgroundBrush().color().name(), "#ffffff")
        self.assertEqual(
            detached.upper_plot.getAxis("bottom").pen().color().name(),
            "#172033",
        )

        self.window._set_theme("dark")
        self.assertEqual(detached.upper_plot.backgroundBrush().color().name(), "#000000")

    def test_current_plot_window_data_tip_menu_deletes_labels(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._open_current_plot_window()
        detached = self.window._detached_plot_window
        detached._place_data_tip("top", 0.1, 1.0)
        detached._place_data_tip("top", 0.2, 0.5)
        data_tip = detached._data_tip_items["top"][0]

        menu, actions = detached._build_data_tip_menu()
        try:
            self.assertEqual(
                [action.text() for action in menu.actions()],
                ["Delete This Data Tip", "Delete All Data Tips"],
            )
        finally:
            menu.close()

        deleted = detached._delete_data_tip("top", data_tip)

        self.assertTrue(deleted)
        self.assertEqual(len(detached._data_tip_items["top"]), 1)
        self.assertNotIn(data_tip, detached._data_tip_items["top"])

    def test_current_display_state_snapshot_uses_checked_upper_lower_traces(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window.bottom_display_combo.setCurrentText("xfer")
        self.window.bottom_value_mode_combo.setCurrentText("dB")
        self.window._plot_measurement(measurement)
        for index in range(self.window.top_trace_list.count()):
            item = self.window.top_trace_list.item(index)
            item.setCheckState(
                QtCore.Qt.Checked
                if item.data(QtCore.Qt.UserRole) == "ai1"
                else QtCore.Qt.Unchecked
            )
        self.window._plot_measurement(measurement)

        snapshot = self.window._snapshot_with_current_display_state()
        state = snapshot.measurement.metadata["legacy_display_state"]

        self.assertEqual(state["layout"], "dual")
        self.assertEqual(state["top"]["mode"], "time")
        self.assertEqual(state["top"]["trace_names"], ["ai1"])
        self.assertEqual(state["bottom"]["mode"], "frf")
        self.assertEqual(state["bottom"]["value_mode"], "dB")
        self.assertEqual(state["bottom"]["trace_names"], ["ai0->ai1"])

    def test_snapshot_filters_disabled_channel_measurement_data(self):
        time_axis = np.array([0.0, 0.1], dtype=float)
        freq_axis = np.array([0.0, 10.0], dtype=float)
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": time_axis,
                "channels": {
                    "ai0": np.array([1.0, 2.0]),
                    "ai1": np.array([3.0, 4.0]),
                    "ai2": np.array([5.0, 6.0]),
                    "ai3": np.array([7.0, 8.0]),
                },
            },
            spectra={
                "f": freq_axis,
                "fft": {
                    "ai0": np.array([1.0, 2.0]),
                    "ai1": np.array([3.0, 4.0]),
                    "ai2": np.array([5.0, 6.0]),
                },
                "autospectrum": {
                    "ai0": np.array([1.0, 2.0]),
                    "ai1": np.array([3.0, 4.0]),
                    "ai2": np.array([5.0, 6.0]),
                },
            },
            frf={"ai0->ai1": np.array([1.0 + 0.0j]), "ai0->ai2": np.array([2.0 + 0.0j])},
            coherence={"ai0->ai1": np.array([1.0]), "ai0->ai2": np.array([0.5])},
            cross_spectra={"ai0->ai1": np.array([1.0 + 0.0j]), "ai0->ai2": np.array([2.0 + 0.0j])},
            correlations={"ai0:auto": np.array([1.0]), "ai2:auto": np.array([2.0])},
            impulse_responses={"ai0->ai1": np.array([1.0]), "ai0->ai2": np.array([2.0])},
            metadata={},
        )
        self.controller.state.measurement = measurement
        self.window.channel_table.item(2, 0).setCheckState(QtCore.Qt.Unchecked)
        self.window.channel_table.item(3, 0).setCheckState(QtCore.Qt.Unchecked)
        self.window._read_session_from_widgets()

        snapshot = self.window._snapshot_with_current_display_state()
        saved = snapshot.measurement

        self.assertEqual(set(saved.time_data["channels"]), {"ai0", "ai1"})
        self.assertEqual(set(saved.spectra["autospectrum"]), {"ai0", "ai1"})
        self.assertEqual(set(saved.spectra["fft"]), {"ai0", "ai1"})
        self.assertEqual(set(saved.frf), {"ai0->ai1"})
        self.assertEqual(set(saved.coherence), {"ai0->ai1"})
        self.assertEqual(set(saved.cross_spectra), {"ai0->ai1"})
        self.assertEqual(set(saved.correlations), {"ai0:auto"})
        self.assertEqual(set(saved.impulse_responses), {"ai0->ai1"})

    def test_chan_sel_checklist_filters_visible_traces(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        for index in range(self.window.top_trace_list.count()):
            item = self.window.top_trace_list.item(index)
            if item.data(QtCore.Qt.UserRole) == "ai0":
                item.setCheckState(QtCore.Qt.Unchecked)
        QtWidgets.QApplication.processEvents()

        self.assertNotIn("ai0", self.window._last_plot_cache["top"])
        self.assertIn("ai1", self.window._last_plot_cache["top"])

    def test_chan_sel_is_populated_from_channel_setup_before_acquisition(self):
        self.assertEqual(
            [
                self.window.top_trace_list.item(index).data(QtCore.Qt.UserRole)
                for index in range(self.window.top_trace_list.count())
            ],
            ["ai0", "ai1", "ai2", "ai3"],
        )
        self.assertEqual(
            [
                self.window.bottom_trace_list.item(index).data(QtCore.Qt.UserRole)
                for index in range(self.window.bottom_trace_list.count())
            ],
            ["ai0->ai1", "ai0->ai2", "ai0->ai3"],
        )
        self.assertNotIn("->", self.window.bottom_trace_list.item(0).text())
        self.assertIn("Ch 2", self.window.bottom_trace_list.item(0).text())
        self.window.bottom_display_combo.setCurrentText("y(t)")
        self.assertEqual(self.window.bottom_trace_list.count(), 4)
        self.assertLessEqual(self.window.bottom_trace_list.height(), 110)

    def test_legacy_measurement_defaults_choose_available_displays_and_traces(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {}
        self.controller.state.measurement = measurement

        self.window._apply_display_defaults_for_measurement(measurement)

        self.assertEqual(self.window.top_display_combo.currentText(), "aspec")
        self.assertEqual(self.window.bottom_display_combo.currentText(), "xfer")
        self.assertEqual(self.window.top_trace_list.item(0).data(QtCore.Qt.UserRole), "ai0")
        self.assertEqual(self.window.bottom_trace_list.item(0).data(QtCore.Qt.UserRole), "ai0->ai1")

    def test_legacy_vna_display_state_restores_saved_panels(self):
        from python_vna.storage import load_legacy_vna

        candidates = [
            path
            for path in Path("D:/SynologyDrive").rglob("003.vna")
            if "651D-R" in str(path) and str(path).endswith(r"651D-R\003.vna")
        ]
        if not candidates:
            self.skipTest("651D-R 003.vna fixture is not available")
        imported = load_legacy_vna(candidates[0])
        self.controller.set_session(imported.config)
        self.controller.state.measurement = imported.measurement
        self.window.session = imported.config
        self.window._load_session_to_widgets()

        self.window._apply_display_defaults_for_measurement(imported.measurement)
        self.window._plot_measurement(imported.measurement)

        self.assertEqual(self.window.sample_rate_edit.value(), 2560.0)
        self.assertEqual(self.window.frame_size_edit.value(), 4096)
        self.assertEqual(self.window.average_mode_combo.currentText(), "linear")
        self.assertEqual(self.window.average_count_edit.value(), 20)
        self.assertEqual(self.window.window_combo.currentText(), "Hanning")
        self.assertEqual(self.window.trigger_mode_combo.currentData(), "Off (Free Run)")
        self.assertEqual(self.window.trigger_source_combo.currentText(), "Ch1")
        self.assertEqual(self.window.trigger_level_percent_combo.currentText(), "0%")
        self.assertEqual(self.window.pretrigger_samples_edit.value(), -10)
        self.assertEqual(self.window.top_display_combo.currentText(), "y(t)")
        self.assertEqual(self.window.bottom_display_combo.currentText(), "xfer")
        self.assertEqual(self.window.bottom_value_mode_combo.currentText(), "dB")
        self.assertEqual(self.window.bottom_xscale_combo.currentText(), "log")
        self.assertEqual(self.window._checked_trace_names("top"), {"Channel 1", "Channel 2"})
        self.assertEqual(self.window._checked_trace_names("bottom"), {"ai0->ai1"})
        x_range, y_range = self.window.bottom_plot.viewRange()
        self.assertAlmostEqual(x_range[0], np.log10(0.6250000037510972), places=5)
        self.assertAlmostEqual(x_range[1], np.log10(1280.000007682247), places=5)
        visible_values = np.asarray(
            self.window._last_plot_cache["bottom"]["ai0->ai1"][1], dtype=float
        )
        self.assertLess(y_range[0], float(np.min(visible_values)))
        self.assertGreater(y_range[1], float(np.max(visible_values)))
        self.assertNotAlmostEqual(y_range[0], -70.0, places=5)
        self.assertNotAlmostEqual(y_range[1], 30.0, places=5)

    def test_sample_legacy_vna_aspec_xfer_and_coh_display_values_are_stable(self):
        from python_vna.storage import load_legacy_vna

        imported = load_legacy_vna(r"D:\SynologyDrive\codex\vna\dsa\vna\sample.vna")
        self.controller.set_session(imported.config)
        self.controller.state.measurement = imported.measurement
        self.window.session = imported.config
        self.window._load_session_to_widgets()

        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(imported.measurement)

        cached_x, cached_y = self.window._last_plot_cache["top"]["Channel 1"]
        np.testing.assert_allclose(cached_x[:8], np.array([0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0]))
        np.testing.assert_allclose(
            cached_y[:8],
            np.array(
                [
                    4.190866311546e-06,
                    9.071480599232e-06,
                    9.336127550341e-06,
                    9.775744983926e-06,
                    1.039407216012e-05,
                    1.118580577895e-05,
                    1.214489922859e-05,
                    1.325179589912e-05,
                ]
            ),
            rtol=1e-10,
            atol=1e-14,
        )

        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("dB")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(imported.measurement)

        cached_x, cached_y = self.window._last_plot_cache["top"]["ai0->ai1"]
        np.testing.assert_allclose(cached_x[:8], np.array([0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0]))
        np.testing.assert_allclose(
            cached_y[:8],
            np.array(
                [
                    -49.114170620286,
                    -49.114169808056,
                    -45.587809013232,
                    -42.87765251233,
                    -40.399052802243,
                    -38.269423072164,
                    -36.835696688246,
                    -35.643423820211,
                ]
            ),
            rtol=1e-10,
            atol=1e-10,
        )

        self.window.top_display_combo.setCurrentText("coh")
        self.window.top_value_mode_combo.setCurrentText("mag")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(imported.measurement)

        cached_x, cached_y = self.window._last_plot_cache["top"]["ai0->ai1"]
        np.testing.assert_allclose(cached_x[:8], np.array([0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0]))
        np.testing.assert_allclose(
            cached_y[:8],
            np.array(
                [
                    0.999811053276,
                    0.961879611015,
                    0.962687134743,
                    0.989849448204,
                    0.996446728706,
                    0.998735249043,
                    0.999507367611,
                    0.999534249306,
                ]
            ),
            rtol=1e-10,
            atol=1e-12,
        )
        self.assertLessEqual(float(np.nanmax(cached_y)), 1.00001)
        self.assertGreaterEqual(float(np.nanmin(cached_y)), 0.0)

    def test_legacy_display_state_syncs_visible_strip_combos(self):
        measurement = self._measurement()
        measurement.metadata["legacy_display_state"] = {
            "layout": "dual",
            "top": {
                "mode": "coherence",
                "value_mode": "mag",
                "xscale": "log",
                "trace_names": ["ai0->ai1"],
            },
            "bottom": {
                "mode": "autospectrum",
                "value_mode": "dB",
                "xscale": "log",
                "trace_names": ["ai1"],
            },
        }
        self.controller.state.measurement = measurement

        self.window._apply_display_defaults_for_measurement(measurement)

        self.assertEqual(self.window.top_display_combo.currentText(), "coh")
        self.assertEqual(self.window.top_display_strip_combo.currentText(), "coh")
        self.assertEqual(self.window.bottom_display_combo.currentText(), "aspec")
        self.assertEqual(self.window.bottom_display_strip_combo.currentText(), "aspec")
        self.assertEqual(self.window.bottom_value_mode_combo.currentText(), "dB rms")
        self.assertEqual(self.window.bottom_value_strip_combo.currentText(), "dB rms")

    def test_legacy_trace_names_match_channel_labels_or_internal_names(self):
        measurement = self._measurement()
        measurement.metadata["legacy_display_state"] = {
            "layout": "dual",
            "top": {
                "mode": "time",
                "value_mode": "real",
                "xscale": "linear",
                "trace_names": ["Channel 2"],
            },
            "bottom": {
                "mode": "frf",
                "value_mode": "dB",
                "xscale": "log",
                "trace_names": ["Channel 1->Channel 2"],
            },
        }
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 1.0], dtype=float),
            "ai1": np.array([0.0, 2.0], dtype=float),
        }
        self.controller.state.measurement = measurement

        self.window._apply_display_defaults_for_measurement(measurement)

        self.assertEqual(self.window._checked_trace_names("top"), {"ai1"})
        self.assertEqual(self.window._active_trace_names["top"], "ai1")
        self.assertEqual(self.window._checked_trace_names("bottom"), {"ai0->ai1"})
        self.assertEqual(self.window._active_trace_names["bottom"], "ai0->ai1")

    def test_marker_readout_uses_selected_trace(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai1")
        )
        self.window._toggle_markers(True)
        self.window._marker_positions["top"] = [0.1, 0.2]
        self.window._refresh_markers("top")
        self.window._update_marker_readout("top")
        label = self.window.top_marker_label.text()
        self.assertIn("Top Marker [ai1]:", label)
        self.assertEqual(self.window.top_marker_fields["trace"].text(), "ai1")
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "0.1")
        self.assertEqual(self.window.top_marker_fields["x2"].text(), "0.2")

    def test_peak_search_uses_visible_range(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )
        self.window.top_plot.setXRange(0.15, 0.31, padding=0.0)
        self.window._find_trace_extremum("top", "peak")
        self.assertAlmostEqual(self.window._cursor_positions["top"][0], 0.2, places=6)
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "--")

    def test_marker_fields_reset_when_markers_disabled(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("frf")
        self.window._plot_measurement(measurement)
        self.window._toggle_markers(True)
        self.window._marker_positions["top"] = [10.0, 40.0]
        self.window._update_marker_readout("top")
        self.window._toggle_markers(False)
        self.assertEqual(self.window.top_marker_fields["trace"].text(), "off")
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "--")

    def test_control_panel_can_hide_and_restore(self):
        self.window._toggle_control_panel(False)
        self.assertFalse(self.window._controls_visible)
        hidden_sizes = self.window.main_splitter.sizes()
        self.assertEqual(hidden_sizes[0], 0)
        self.window._toggle_control_panel(True)
        self.assertTrue(self.window._controls_visible)
        shown_sizes = self.window.main_splitter.sizes()
        self.assertGreater(shown_sizes[0], 0)

    def test_display_strip_syncs_with_sidebar_controls(self):
        self.window.top_display_strip_combo.setCurrentText("fft")
        self.assertEqual(self.window.top_display_combo.currentText(), "fft")
        self.window.bottom_display_combo.setCurrentText("coh")
        self.assertEqual(self.window.bottom_display_strip_combo.currentText(), "coh")
        self.assertEqual(self.window.top_value_mode_combo.currentText(), "real")
        self.window.top_display_combo.setCurrentText("aspec")
        self.assertIn("pk", [self.window.top_value_mode_combo.itemText(i) for i in range(self.window.top_value_mode_combo.count())])
        self.assertIn("rms/rt(Hz)", [self.window.top_value_mode_combo.itemText(i) for i in range(self.window.top_value_mode_combo.count())])

    def test_aspec_extended_value_modes_plot(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("rms/rt(Hz)")
        self.window._plot_measurement(measurement)
        cached = self.window._last_plot_cache["top"]["ai0"][1]
        self.assertTrue(np.all(cached >= 0.0))

    def test_aspec_density_modes_apply_rbw_like_legacy_vna(self):
        values = np.array([8.0, 18.0], dtype=float)

        rms_density = self.window._transform_legacy_autospectrum(values, "linear_per_sqrt_hz", rbw_hz=2.0)
        power_density = self.window._transform_legacy_autospectrum(values, "power_per_hz", rbw_hz=2.0)
        db_density = self.window._transform_legacy_autospectrum(values, "dB_per_sqrt_hz", rbw_hz=2.0)

        np.testing.assert_allclose(rms_density, np.sqrt(values / 2.0))
        np.testing.assert_allclose(power_density, values / 2.0)
        np.testing.assert_allclose(db_density, 10.0 * np.log10(values / 2.0))

    def test_legacy_aspec_display_applies_engineering_db_rbw_and_window_scaling(self):
        values = np.array([8.0, 18.0], dtype=float)

        db_rms = self.window._transform_legacy_autospectrum(
            values,
            "dB",
            rbw_hz=2.0,
            euscale_fac=3.0,
            db_ref=6.0,
            units_value=np.sqrt(2.0),
            wincor=4.0,
            yapcor_index=2,
        )
        rms_per_root_hz = self.window._transform_legacy_autospectrum(
            values,
            "linear_per_sqrt_hz",
            rbw_hz=2.0,
            euscale_fac=3.0,
            db_ref=6.0,
            units_value=1.0,
            wincor=4.0,
            yapcor_index=2,
        )

        np.testing.assert_allclose(
            db_rms,
            10.0 * np.log10(4.0 * ((3.0 * np.sqrt(2.0) / 6.0) ** 2) * values),
        )
        np.testing.assert_allclose(
            rms_per_root_hz,
            np.sqrt((4.0 / 2.0) * (3.0 ** 2) * values),
        )

    def test_legacy_hanning_display_uses_runtime_power_correction_not_persisted_wincor(self):
        measurement = self._measurement()
        measurement.metadata.update(
            {
                "legacy_wincor": 1.0,
                "legacy_runtime_wincor": 2.0 / 3.0,
                "processing_window": "hanning",
                "legacy_display_state": {
                    "top": {"legacy_yapcor_index": 2},
                },
            }
        )
        measurement.spectra["autospectrum"] = {"ai0": np.array([9.0], dtype=float)}
        measurement.spectra["f"] = np.array([10.0], dtype=float)
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("rms")

        self.window._plot_measurement(measurement)

        _x, y = self.window._last_plot_cache["top"]["ai0"]
        self.assertAlmostEqual(self.window._measurement_window_correction(measurement), 2.0 / 3.0)
        self.assertAlmostEqual(y[0], np.sqrt((2.0 / 3.0) * 9.0), places=9)

    def test_xfer_and_cspec_include_matlab_value_modes(self):
        self.window.top_display_combo.setCurrentText("xfer")
        xfer_labels = [
            self.window.top_value_mode_combo.itemText(index)
            for index in range(self.window.top_value_mode_combo.count())
        ]
        self.assertEqual(
            xfer_labels,
            ["real", "mag", "imag", "dB", "log mag", "phase", "phase u", "nyquist"],
        )

        self.window.top_display_combo.setCurrentText("cspec")
        cspec_labels = [
            self.window.top_value_mode_combo.itemText(index)
            for index in range(self.window.top_value_mode_combo.count())
        ]
        self.assertEqual(
            cspec_labels,
            ["real", "mag", "imag", "dB", "log mag", "phase", "phase u", "nyquist"],
        )
        self.window.top_display_combo.setCurrentText("aspec")
        aspec_labels = [
            self.window.top_value_mode_combo.itemText(index)
            for index in range(self.window.top_value_mode_combo.count())
        ]
        self.assertIn("Log rms^2", aspec_labels)

    def test_display_modes_apply_requested_default_value_units(self):
        self.window.top_display_combo.setCurrentText("xfer")

        self.assertEqual(self.window.top_value_mode_combo.currentText(), "dB")
        self.assertEqual(self.window.top_value_strip_combo.currentText(), "dB")

        self.window.top_display_combo.setCurrentText("aspec")

        self.assertEqual(self.window.top_value_mode_combo.currentText(), "Log rms^2/Hz")
        self.assertEqual(self.window.top_value_strip_combo.currentText(), "Log rms^2/Hz")
        self.assertEqual(self.window.top_yscale_combo.currentText(), "log")

    def test_axis_scales_follow_matlab_display_defaults(self):
        self.window.top_display_combo.setCurrentText("xfer")
        self.assertEqual(self.window.top_xscale_combo.currentText(), "log")
        self.assertEqual(self.window.top_yscale_combo.currentText(), "linear")

        self.window.top_value_mode_combo.setCurrentText("log mag")
        self.assertEqual(self.window.top_yscale_combo.currentText(), "log")

        self.window.top_display_combo.setCurrentText("fft")
        self.assertEqual(self.window.top_xscale_combo.currentText(), "linear")
        self.assertEqual(self.window.top_yscale_combo.currentText(), "linear")

        self.window.top_display_combo.setCurrentText("aspec")
        self.assertEqual(self.window.top_xscale_combo.currentText(), "log")
        self.window.top_value_mode_combo.setCurrentText("Log rms")
        self.assertEqual(self.window.top_yscale_combo.currentText(), "log")

    def test_phase_modes_follow_matlab_wrapped_and_unwrapped_meaning(self):
        values = np.exp(1j * np.deg2rad(np.array([170.0, -170.0, -160.0], dtype=float)))

        wrapped = self.window._transform_curve(values, "phase")
        unwrapped = self.window._transform_curve(values, "phase_u")

        self.assertAlmostEqual(wrapped[0], 170.0, places=6)
        self.assertAlmostEqual(wrapped[1], -170.0, places=6)
        self.assertAlmostEqual(unwrapped[1], 190.0, places=6)
        self.assertAlmostEqual(unwrapped[2], 200.0, places=6)

    def test_legacy_xfer_display_applies_response_over_reference_engineering_scale(self):
        measurement = self._measurement()
        measurement.metadata["legacy_channels"] = {
            "ai0": {"euscale_fac": 2.0, "db_ref": 1.0, "fs_val": 5.0},
            "ai1": {"euscale_fac": 10.0, "db_ref": 1.0, "fs_val": 5.0},
        }
        measurement.metadata["legacy_display_state"] = {
            "top": {"legacy_yintfac_index": 1},
        }
        values = np.array([1.0 + 0.0j, 2.0 + 0.0j], dtype=complex)

        mag = self.window._transform_frf_for_trace(
            measurement, "top", "ai0->ai1", values, "mag"
        )
        db = self.window._transform_frf_for_trace(
            measurement, "top", "ai0->ai1", values, "dB"
        )

        np.testing.assert_allclose(mag, np.array([5.0, 10.0]))
        np.testing.assert_allclose(db, 20.0 * np.log10(np.array([5.0, 10.0])))

    def test_live_aspec_display_applies_channel_engineering_scale_and_db_ref(self):
        measurement = self._measurement()
        self.window.session.ai_channels[0].sensitivity = 3.0
        self.window.session.ai_channels[0].db_reference = 6.0
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("dB rms")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.controller.state.measurement = measurement

        self.window._plot_measurement(measurement)

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0"]
        expected = 10.0 * np.log10(
            np.maximum(((3.0 / 6.0) ** 2) * measurement.spectra["autospectrum"]["ai0"], 1e-307)
        )
        np.testing.assert_allclose(cached_y, expected)

    def test_live_time_display_applies_channel_engineering_scale(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([1.0, -2.0, 0.5, -0.25], dtype=float)
        }
        self.window.session.ai_channels[0].sensitivity = 800.0
        self.window.session.ai_channels[0].engineering_unit = "N"
        self.window.session.ai_channels[0].per_eu_mode = "/Volt"
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window.top_value_mode_combo.setCurrentText("real")
        self.controller.state.measurement = measurement

        self.window._plot_measurement(measurement)

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0"]
        np.testing.assert_allclose(cached_y, np.array([800.0, -1600.0, 400.0, -200.0]))

    def test_live_time_display_leaves_voltage_when_engineering_units_off(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {"ai0": np.array([1.0, -2.0], dtype=float)}
        self.window.session.ai_channels[0].sensitivity = 800.0
        self.window.session.ai_channels[0].per_eu_mode = "Off"
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window.top_value_mode_combo.setCurrentText("real")
        self.controller.state.measurement = measurement

        self.window._plot_measurement(measurement)

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0"]
        np.testing.assert_allclose(cached_y, np.array([1.0, -2.0]))

    def test_effective_engineering_scale_matches_legacy_per_voltage_modes(self):
        self.assertEqual(self.window._effective_euscale_fac(800.0, "/Volt"), 800.0)
        self.assertEqual(self.window._effective_euscale_fac(800.0, "/mV"), 800_000.0)
        self.assertEqual(self.window._effective_euscale_fac(800.0, "/uV"), 800_000_000.0)
        self.assertEqual(self.window._effective_euscale_fac(800.0, "/kV"), 0.8)
        self.assertEqual(self.window._effective_euscale_fac(800.0, "Off"), 1.0)

    def test_live_xfer_display_applies_response_over_reference_engineering_scale(self):
        measurement = self._measurement()
        self.window.session.ai_channels[0].sensitivity = 2.0
        self.window.session.ai_channels[1].sensitivity = 10.0
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("mag")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.controller.state.measurement = measurement

        self.window._plot_measurement(measurement)

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0->ai1"]
        np.testing.assert_allclose(cached_y, np.abs(measurement.frf["ai0->ai1"]) * 5.0)

    def test_axis_labels_follow_matlab_unit_style(self):
        self.window._read_session_from_widgets()
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("rms")
        self.window.bottom_display_combo.setCurrentText("xfer")
        self.window.bottom_value_mode_combo.setCurrentText("dB")

        self.window._update_axis_labels()

        self.assertEqual(self.window.top_plot.getAxis("bottom").labelText, "Hertz")
        self.assertEqual(self.window.top_plot.getAxis("left").labelText, "rms (m/s^2)")
        self.assertEqual(self.window.bottom_plot.getAxis("bottom").labelText, "Hertz")
        self.assertEqual(self.window.bottom_plot.getAxis("left").labelText, "dB (m/s^2)/m/s^2")

    def test_nyquist_axis_labels_use_complex_plane_units(self):
        self.window._read_session_from_widgets()
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("nyquist")

        self.window._update_axis_labels()

        self.assertEqual(self.window.top_plot.getAxis("bottom").labelText, "real (m/s^2)/m/s^2")
        self.assertEqual(self.window.top_plot.getAxis("left").labelText, "imag (m/s^2)/m/s^2")

    def test_log_y_scale_filters_nonpositive_values(self):
        measurement = self._measurement()
        measurement.frf = {"ai0->ai1": np.array([0.0 + 0.0j, 1.0 + 0.0j, -2.0 + 0.0j, 3.0 + 0.0j])}
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("log mag")
        self.window.top_xscale_combo.setCurrentText("linear")

        self.window._plot_measurement(measurement)

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0->ai1"]
        self.assertTrue(np.all(cached_y > 0.0))
        self.assertEqual(self.window.top_yscale_combo.currentText(), "log")

    def test_legacy_axis_ranges_for_coherence_phase_xfer_and_nyquist(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement

        self.window.top_display_combo.setCurrentText("coh")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._plot_measurement(measurement)
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(y_range[0], 0.0, places=6)
        self.assertAlmostEqual(y_range[1], 1.25, places=6)

        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("phase")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._plot_measurement(measurement)
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(y_range[0], -250.0, places=6)
        self.assertAlmostEqual(y_range[1], 250.0, places=6)

        self.window.top_value_mode_combo.setCurrentText("phase u")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._plot_measurement(measurement)
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(y_range[0], -800.0, places=6)
        self.assertAlmostEqual(y_range[1], 250.0, places=6)

        self.window.top_value_mode_combo.setCurrentText("dB")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._plot_measurement(measurement)
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(y_range[1] - y_range[0], 100.0, places=6)

        self.window.top_value_mode_combo.setCurrentText("nyquist")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._plot_measurement(measurement)
        x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(x_range[0], y_range[0], places=6)
        self.assertAlmostEqual(x_range[1], y_range[1], places=6)
        self.assertAlmostEqual(abs(x_range[0]), x_range[1], places=6)

    def test_default_axis_range_auto_fits_coherence_visible_data(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("coh")

        self.window._plot_measurement(measurement)

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[0], 0.75)
        self.assertLess(y_range[1], 1.05)

    def test_log_axis_ranges_are_set_in_plot_coordinates(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("log mag")

        self.window._plot_measurement(measurement)

        x_range, y_range = self.window.top_plot.viewRange()
        expected_y = np.log10(np.abs(measurement.frf["ai0->ai1"][1:]))
        self.assertLess(x_range[0], 1.05)
        self.assertGreater(x_range[1], 1.5)
        self.assertLess(y_range[0], float(np.min(expected_y)))
        self.assertGreater(y_range[1], float(np.max(expected_y)))

    def test_log_y_legacy_scope_uses_legacy_floor_instead_of_data_minimum(self):
        measurement = self._measurement()
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([1e-16, 1e-12, 0.95, 1.0, 1.05, 0.98], dtype=float)
        }
        measurement.spectra["f"] = np.array([0.0, 1.0, 2.0, 4.0, 8.0, 16.0], dtype=float)
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")

        self.window._plot_measurement(measurement)
        self.window._apply_axis_scale("top", y_scope="legacy")

        _cached_x, cached_y = self.window._last_plot_cache["top"]["ai0"]
        self.assertGreater(float(np.min(cached_y)), 1e-13)
        self.assertGreater(float(np.max(cached_y)), 1.0)
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[0], -14.0)
        self.assertLess(y_range[1], 2.0)

    def test_default_y_axis_auto_fits_visible_data_without_legacy_floor(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([1e-9, 2e-4, 3e-4, 20.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window.top_xscale_combo.setCurrentText("linear")

        self.window._plot_measurement(measurement)

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[0], -10.0)
        self.assertLess(y_range[0], -7.0)

    def test_y_auto_fit_uses_only_visible_x_range(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([100.0, 2.0, 3.0, 200.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("rms^2")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(measurement)
        self.window.top_plot.setXRange(1.5, 3.5, padding=0.0)

        self.window._auto_fit_y_to_visible_x("top")

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertLess(y_range[0], 2.0)
        self.assertGreater(y_range[1], 3.0)
        self.assertLess(y_range[1], 10.0)

    def test_log_y_auto_fit_visible_x_uses_data_min_not_legacy_floor(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([1e-9, 2e-4, 3e-4, 20.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(measurement)
        self.window.top_plot.setXRange(1.5, 3.5, padding=0.0)

        self.window._auto_fit_y_to_visible_x("top")

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[0], -5.5)
        self.assertLess(y_range[0], -3.0)
        self.assertLess(y_range[1], -2.5)

    def test_y_auto_follow_updates_with_x_range_and_respects_manual_y(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([1.0, 2.0, 50.0, 80.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("rms^2")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(measurement)
        self.window._auto_y_follow_visible_x["top"] = True

        self.window.top_plot.setXRange(2.5, 4.1, padding=0.0)
        QtWidgets.QApplication.processEvents()

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[1], 80.0)

        self.window._manual_y_ranges["top"] = (0.0, 10.0)
        self.window._apply_axis_scale("top", preserve_x=True)
        self.window.top_plot.setXRange(0.9, 2.1, padding=0.0)
        QtWidgets.QApplication.processEvents()

        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertAlmostEqual(y_range[0], 0.0, places=6)
        self.assertAlmostEqual(y_range[1], 10.0, places=6)

    def test_log_value_mode_forces_log_axis_even_if_scale_combo_is_stale(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.bottom_display_combo.setCurrentText("aspec")
        self.window.bottom_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window.bottom_yscale_combo.blockSignals(True)
        self.window.bottom_yscale_combo.setCurrentText("linear")
        self.window.bottom_yscale_combo.blockSignals(False)

        self.window._plot_measurement(measurement)

        _x_range, y_range = self.window.bottom_plot.viewRange()
        expected_y = np.log10(
            np.asarray(self.window._last_plot_cache["bottom"]["ai0"][1], dtype=float)
        )
        self.assertLess(y_range[0], float(np.min(expected_y)))
        self.assertGreater(y_range[1], float(np.max(expected_y)))

    def test_display_mode_change_clears_imported_manual_ranges(self):
        self.window._manual_x_ranges["bottom"] = (0.625, 1280.0)
        self.window._manual_y_ranges["bottom"] = (-70.0, 30.0)

        self.window.bottom_display_combo.setCurrentText("aspec")

        self.assertIsNone(self.window._manual_x_ranges["bottom"])
        self.assertIsNone(self.window._manual_y_ranges["bottom"])

    def test_display_mode_change_reenables_auto_fit_for_aspec(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.spectra["autospectrum"] = {
            "ai0": np.array([1e-9, 2e-4, 3e-4, 20.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._auto_y_follow_visible_x["top"] = False
        self.window._manual_y_ranges["top"] = (-100.0, 100.0)

        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")

        self.assertTrue(self.window._auto_y_follow_visible_x["top"])
        self.assertIsNone(self.window._manual_y_ranges["top"])
        _x_range, y_range = self.window.top_plot.viewRange()
        self.assertGreater(y_range[0], -10.0)
        self.assertLess(y_range[0], -7.0)

    def test_display_mode_change_reenables_auto_fit_for_xfer(self):
        measurement = self._measurement()
        measurement.spectra["f"] = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)
        measurement.frf = {
            "ai0->ai1": np.array([1e-4 + 0j, 2e-2 + 0j, 3e-2 + 0j, 4e-2 + 0j])
        }
        self.controller.state.measurement = measurement
        self.window.bottom_display_combo.setCurrentText("y(t)")
        self.window._auto_y_follow_visible_x["bottom"] = False
        self.window._manual_y_ranges["bottom"] = (-100.0, 100.0)

        self.window.bottom_display_combo.setCurrentText("xfer")

        self.assertTrue(self.window._auto_y_follow_visible_x["bottom"])
        self.assertIsNone(self.window._manual_y_ranges["bottom"])
        _x_range, y_range = self.window.bottom_plot.viewRange()
        self.assertGreater(y_range[0], -90.0)
        self.assertLess(y_range[1], -20.0)

    def test_reset_plot_display_state_clears_previous_import_scales(self):
        self.window.bottom_display_combo.setCurrentText("aspec")
        self.window.bottom_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window._manual_x_ranges["bottom"] = (0.625, 1280.0)
        self.window._manual_y_ranges["bottom"] = (1e-12, 1.0)
        self.window._preferred_trace_checks["bottom"] = {"old"}
        self.window._active_trace_names["bottom"] = "old"

        self.window._reset_plot_display_state()

        self.assertIsNone(self.window._manual_x_ranges["bottom"])
        self.assertIsNone(self.window._manual_y_ranges["bottom"])
        self.assertIsNone(self.window._preferred_trace_checks["bottom"])
        self.assertIsNone(self.window._active_trace_names["bottom"])
        self.assertEqual(self.window.bottom_yscale_combo.currentText(), "linear")

    def test_legacy_display_state_auto_fits_y_instead_of_reusing_imported_range(self):
        measurement = self._measurement()
        measurement.metadata["legacy_display_state"] = {
            "layout": "dual",
            "bottom": {
                "mode": "frf",
                "value_mode": "dB",
                "xscale": "log",
                "trace_names": ["ai0->ai1"],
                "axis_range": {"xmin": 10.0, "xmax": 40.0, "ymin": -70.0, "ymax": 30.0},
            },
        }
        self.controller.state.measurement = measurement
        self.window.bottom_display_combo.setCurrentText("aspec")
        self.window.bottom_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.assertEqual(self.window.bottom_yscale_combo.currentText(), "log")

        self.window._reset_plot_display_state()
        self.window._apply_display_defaults_for_measurement(measurement)
        self.window._plot_measurement(measurement)

        self.assertEqual(self.window.bottom_display_combo.currentText(), "xfer")
        self.assertEqual(self.window.bottom_value_mode_combo.currentText(), "dB")
        self.assertEqual(self.window.bottom_yscale_combo.currentText(), "linear")
        self.assertFalse(self.window._is_log_yscale("bottom"))
        self.assertIsNone(self.window._manual_y_ranges["bottom"])
        self.assertTrue(self.window._auto_y_follow_visible_x["bottom"])
        _x_range, y_range = self.window.bottom_plot.viewRange()
        x_values, y_values = self.window._last_plot_cache["bottom"]["ai0->ai1"]
        visible_x_min, visible_x_max = self.window._current_visible_x_range("bottom")
        visible_mask = (x_values >= visible_x_min) & (x_values <= visible_x_max)
        visible_values = np.asarray(y_values[visible_mask], dtype=float)
        self.assertLess(y_range[0], float(np.min(visible_values)))
        self.assertGreater(y_range[1], float(np.max(visible_values)))
        self.assertNotAlmostEqual(y_range[0], -70.0, places=5)
        self.assertNotAlmostEqual(y_range[1], 30.0, places=5)

    def test_import_legacy_vna_remembers_last_folder(self):
        first_path = Path(tempfile.gettempdir()) / "vna_first" / "first.vna"
        second_path = Path(tempfile.gettempdir()) / "vna_second" / "second.vna"
        imported = SavedSession(
            config=default_session_config(),
            measurement=self._measurement(),
            source_path=first_path,
        )
        dialog_dirs: list[str] = []

        def fake_open_file(_parent, _title, directory, _filter):
            dialog_dirs.append(directory)
            return (str(first_path if len(dialog_dirs) == 1 else second_path), "")

        with mock.patch.object(main_window_module, "load_legacy_vna", return_value=imported):
            with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName", fake_open_file):
                self.window._import_legacy_vna()
                self.window._import_legacy_vna()

        self.assertEqual(dialog_dirs[0], str(Path.cwd()))
        self.assertEqual(dialog_dirs[1], str(first_path.parent))
        self.assertEqual(self.window._last_vna_directory, second_path.parent)
        self.assertIn(second_path.name, self.window.windowTitle())

    def test_mark_at_cursor_uses_real_coordinates_on_log_axes(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("log mag")
        self.window._plot_measurement(measurement)

        moved = self.window._move_cursor_to_point("top", 20.0, 3.6)
        marked = self.window._toggle_mark_at_cursor("top")

        self.assertTrue(moved)
        self.assertTrue(marked)
        self.assertAlmostEqual(self.window._marker_positions["top"][0], 20.0, places=6)
        self.assertAlmostEqual(self.window._marker_lines["top"][0].value(), np.log10(20.0), places=6)
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "20")

    def test_marker_placement_does_not_change_axis_range(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 0.1, 0.2, 0.3], dtype=float),
            "ai1": np.array([10.0, 10.1, 10.2, 10.3], dtype=float),
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        before = self.window.top_plot.viewRange()

        moved = self.window._move_cursor_to_point("top", 0.1, 10.1)
        marked = self.window._toggle_mark_at_cursor("top")

        after = self.window.top_plot.viewRange()
        self.assertTrue(moved)
        self.assertTrue(marked)
        np.testing.assert_allclose(after[0], before[0])
        np.testing.assert_allclose(after[1], before[1])

    def test_log_trace_selection_uses_plot_coordinates(self):
        measurement = self._measurement()
        measurement.spectra["autospectrum"] = {
            "low": np.array([1.0, 1e-8, 1e-6, 1e-4], dtype=float),
            "high": np.array([1.0, 1e-4, 1e-3, 1e-2], dtype=float),
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("aspec")
        self.window.top_value_mode_combo.setCurrentText("Log rms^2/Hz")
        self.window._plot_measurement(measurement)

        nearest = self.window._nearest_trace_name("top", 20.0, 8e-4)

        self.assertEqual(nearest, "high")

    def test_dragged_marker_line_converts_from_log_axis_coordinates(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window._plot_measurement(measurement)
        self.window._toggle_markers(True)
        line = self.window._marker_lines["top"][0]
        line.setVisible(True)
        line.setValue(np.log10(40.0))

        self.window._handle_marker_line_drag("top", 0, line)

        self.assertAlmostEqual(self.window._marker_positions["top"][0], 40.0, places=6)

    def test_nyquist_plots_complex_plane_without_log_frequency_filtering(self):
        measurement = self._measurement()
        frf = np.array([-1.0 + 0.5j, 0.0 + 1.0j, 2.0 - 0.25j, 3.0 + 0.0j])
        measurement.frf = {"ai0->ai1": frf}
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("xfer")
        self.window.top_value_mode_combo.setCurrentText("nyquist")
        self.window.top_xscale_combo.setCurrentText("log")

        self.window._plot_measurement(measurement)

        cached_x, cached_y = self.window._last_plot_cache["top"]["ai0->ai1"]
        np.testing.assert_allclose(cached_x, np.real(frf))
        np.testing.assert_allclose(cached_y, np.imag(frf))

    def test_channel_matrix_keeps_matlab_mcsetup_columns(self):
        headers = [
            self.window.channel_grid.horizontalHeaderItem(index).text()
            for index in range(self.window.channel_grid.columnCount())
        ]
        self.assertEqual(
            headers,
            ["On", "Chan", "Full Scale", "Coupling", "Offset", "Label", "EU/Volt", "Per EU", "0 dB Ref"],
        )
        self.assertNotIn("Physical", headers)
        self.assertNotIn("Reference", headers)
        self.assertNotIn("IEPE", headers)
        self.assertNotIn("IEPE mA", headers)
        self.assertEqual(self.window.channel_unit_edit.text(), "m/s^2")
        channel_groups = [
            group.title()
            for group in self.window.findChildren(QtWidgets.QGroupBox)
        ]
        self.assertNotIn("MC Setup", channel_groups)
        self.assertIn("CHANNEL SETUP", channel_groups)
        self.assertFalse(self.window.channel_grid.item(0, 1).flags() & QtCore.Qt.ItemIsEditable)
        self.assertFalse(self.window.channel_grid_group.isVisible())
        self.assertEqual(self.window.channel_mc_setup_button.text(), "MC Setup...")
        advanced_group = next(
            group for group in self.window.findChildren(QtWidgets.QGroupBox)
            if group.title() == "Advanced NI Device Defaults"
        )
        self.assertTrue(advanced_group.isChecked())
        self.assertGreaterEqual(advanced_group.minimumHeight(), 260)

    def test_channel_matrix_uses_matlab_style_combo_delegates(self):
        for column in (0, 2, 3, 7):
            self.assertIsNotNone(self.window.channel_grid.itemDelegateForColumn(column))

    def test_channel_table_is_horizontally_resizable(self):
        header = self.window.channel_table.horizontalHeader()
        self.assertEqual(
            header.sectionResizeMode(0),
            QtWidgets.QHeaderView.Interactive,
        )
        self.assertGreaterEqual(self.window.channel_list.count(), 1)
        self.assertEqual(self.window.channel_grid.rowCount(), self.window.channel_table.rowCount())

    def test_channel_combo_boxes_are_selection_only(self):
        self.assertFalse(self.window.channel_coupling_combo.isEditable())
        self.assertFalse(self.window.channel_per_eu_combo.isEditable())
        self.assertFalse(self.window.channel_full_scale_combo.isEditable())

    def test_marker_clicks_snap_to_nearest_data_point(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )
        snapped_x, snapped_y = self.window._nearest_curve_point("top", 0.17)
        self.assertAlmostEqual(snapped_x, 0.2, places=6)
        self.assertAlmostEqual(snapped_y, 0.5, places=6)

    def test_marker_click_uses_two_dimensional_curve_snap(self):
        measurement = self._measurement()
        measurement.time_data["channels"] = {
            "ai0": np.array([0.0, 100.0, 1.0], dtype=float)
        }
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window.top_xscale_combo.setCurrentText("linear")
        self.window._plot_measurement(measurement)
        self.window.top_plot.setXRange(0.0, 0.3, padding=0.0)
        self.window.top_plot.setYRange(0.0, 2.0, padding=0.0)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )

        snapped_x, snapped_y = self.window._nearest_curve_point_2d("top", 0.18, 1.0)

        self.assertAlmostEqual(snapped_x, 0.2, places=6)
        self.assertAlmostEqual(snapped_y, 1.0, places=6)

    def test_cursor_items_follow_nearest_data_point(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        cursor_x, cursor_y = self.window._nearest_curve_point_2d("top", 0.21, 0.48)
        line = self.window._cursor_lines["top"]
        point = self.window._cursor_points["top"]

        line.setValue(self.window._x_to_plot_coord("top", cursor_x))
        line.setVisible(True)
        point.setData(
            [self.window._x_to_plot_coord("top", cursor_x)],
            [self.window._y_to_plot_coord("top", cursor_y)],
        )
        point.setVisible(True)

        self.assertTrue(line.isVisible())
        self.assertTrue(point.isVisible())
        self.assertAlmostEqual(line.value(), 0.2, places=6)

    def test_normal_cursor_click_shows_in_plot_coordinate_label(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        moved = self.window._move_cursor_to_point("top", 0.21, 0.48)

        self.assertTrue(moved)
        self.assertTrue(self.window._cursor_texts["top"].isVisible())
        cursor_text = self.window._cursor_texts["top"].toPlainText()
        self.assertIn("X 0.2", cursor_text)
        self.assertNotIn("ai0", cursor_text)
        curve = self.window._plot_curve_items["top"]["ai0"]
        self.assertGreater(self.window._cursor_texts["top"].zValue(), curve.zValue())

    def test_cursor_readout_follows_refreshed_live_data_at_same_x(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )
        self.window._move_cursor_to_point("top", 0.2, 0.5)

        refreshed = self._measurement()
        refreshed.time_data["channels"]["ai0"] = np.array([9.0, 8.0, 7.0, 6.0], dtype=float)
        self.controller.state.measurement = refreshed
        self.window._plot_measurement(refreshed)

        self.assertAlmostEqual(self.window._cursor_positions["top"][0], 0.2, places=6)
        self.assertAlmostEqual(self.window._cursor_positions["top"][1], 7.0, places=6)
        self.assertIn("Y 7", self.window._cursor_texts["top"].toPlainText())

    def test_axis_history_restore_matches_matlab_zoom_back_behavior(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        previous = ((0.0, 0.3), (-0.5, 1.0))
        self.window._axis_range_history["top"].append(previous)

        restored = self.window._restore_axis_history("top")

        x_range, y_range = self.window.top_plot.viewRange()
        self.assertTrue(restored)
        np.testing.assert_allclose(x_range, previous[0])
        np.testing.assert_allclose(y_range, previous[1])

    def test_channel_editor_writes_back_to_channel_table(self):
        self.window.channel_list.setCurrentRow(0)
        self.window.channel_label_edit.setText("ref0")
        self.window.channel_label_edit.editingFinished.emit()
        self.window.channel_full_scale_combo.setCurrentText("5 V")
        self.assertEqual(self.window.channel_table.item(0, 1).text(), "ai0")
        self.assertEqual(self.window.channel_table.item(0, 10).text(), "ref0")
        self.assertEqual(self.window.channel_table.item(0, 9).text(), "5")
        self.window.channel_coupling_combo.setCurrentText("bias")
        self.assertEqual(self.window.channel_table.item(0, 6).text(), "bias")
        self.assertEqual(self.window.channel_table.item(0, 4).checkState(), QtCore.Qt.Checked)
        self.assertEqual(self.window.channel_table.item(0, 5).text(), "2.1")
        self.window.channel_offset_edit.setValue(0.25)
        self.assertEqual(self.window.channel_table.item(0, 11).text(), "0.25")
        self.assertIn("ref0", self.window.channel_list.item(0).text())

    def test_channel_grid_bias_enables_iepe_defaults(self):
        self.window.channel_grid.item(0, 3).setText("bias")
        self.assertEqual(self.window.channel_table.item(0, 6).text(), "bias")
        self.assertEqual(self.window.channel_table.item(0, 4).checkState(), QtCore.Qt.Checked)
        self.assertEqual(self.window.channel_table.item(0, 5).text(), "2.1")
        self.assertEqual(self.window.channel_table.item(0, 7).text(), "1")
        self.assertEqual(self.window.channel_table.item(0, 8).text(), "m/s^2")

    def test_nearest_trace_selection_prefers_clicked_curve(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        nearest = self.window._nearest_trace_name("top", 0.1, 0.5)
        self.assertEqual(nearest, "ai1")

    def test_relation_plot_legend_shows_response_channel_label(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.bottom_display_combo.setCurrentText("xfer")
        self.window.channel_table.item(1, 10).setText("Resp 1")

        self.window._plot_measurement(measurement)

        legend_labels = [
            label.text
            for _sample, label in self.window.bottom_plot.plotItem.legend.items
        ]
        self.assertIn("Resp 1", legend_labels)
        self.assertNotIn("ai0->ai1", legend_labels)

    def test_plot_legend_is_close_to_corner_and_readable(self):
        legend = self.window.top_plot.plotItem.legend
        self.assertIsNotNone(legend)
        self.assertEqual(legend.opts.get("offset"), (3, 2))
        self.assertEqual(legend.opts.get("labelTextColor"), "#f6f1df")
        self.assertEqual(legend.opts.get("labelTextSize"), "6pt")
        self.assertEqual(legend.layout.horizontalSpacing(), 0)
        self.assertEqual(legend.layout.verticalSpacing(), -6)
        self.assertIs(legend.sampleType, main_window_module.CompactLegendSample)
        self.assertEqual(legend.columnCount, 8)
        self.assertGreater(legend.zValue(), main_window_module.CURVE_Z)
        self.assertLess(legend.zValue(), main_window_module.MARKER_Z)

    def test_right_drag_zoom_box_uses_visible_selection_style(self):
        view_box = self.window.top_plot.getPlotItem().vb
        self.assertEqual(view_box._zoom_box.pen().color().name(), "#5eead4")
        self.assertEqual(view_box._zoom_box.brush().color().alpha(), 48)

    def test_plot_context_menu_matches_legacy_axis_scale_actions(self):
        menu, actions = self.window._build_plot_context_menu(self.window.top_plot, "top")
        try:
            action_texts = [
                action.text()
                for action in menu.actions()
                if not action.isSeparator()
            ]
            self.assertEqual(
                action_texts,
                [
                    "Back One Zoom",
                    "Auto Scale",
                    "Cursor Readout",
                ],
            )
            self.assertIn("auto_scale", actions)
            self.assertTrue(actions["cursor_readout"].isCheckable())
        finally:
            menu.close()

    def test_context_menu_cursor_readout_action_tracks_cursor_state(self):
        self.window._toggle_cursor_readout(False)
        menu, actions = self.window._build_plot_context_menu(self.window.top_plot, "top")
        try:
            self.assertFalse(actions["cursor_readout"].isChecked())
        finally:
            menu.close()

    def test_manual_xy_range_updates_axis_ranges(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        applied = self.window._set_manual_xy_values(
            "top",
            0.05,
            0.25,
            -0.2,
            0.8,
        )

        self.assertTrue(applied)
        self.assertEqual(self.window._manual_x_ranges["top"], (0.05, 0.25))
        self.assertEqual(self.window._manual_y_ranges["top"], (-0.2, 0.8))
        x_range, y_range = self.window.top_plot.viewRange()
        np.testing.assert_allclose(x_range, (0.05, 0.25), atol=1e-9)
        np.testing.assert_allclose(y_range, (-0.2, 0.8), atol=1e-9)

    def test_right_drag_zoom_sets_manual_axis_ranges(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        zoomed = self.window._zoom_plot_to_view_rect(
            "top",
            QtCore.QPointF(0.05, -0.2),
            QtCore.QPointF(0.25, 0.8),
        )

        self.assertTrue(zoomed)
        self.assertEqual(self.window._manual_x_ranges["top"], (0.05, 0.25))
        self.assertEqual(self.window._manual_y_ranges["top"], (-0.2, 0.8))

    def test_marker_points_become_visible_when_marking(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )
        self.window._toggle_markers(True)
        self.window._marker_positions["top"] = [0.2, None]
        self.window._refresh_markers("top")
        self.assertTrue(self.window._marker_points["top"][0].isVisible())
        self.assertTrue(self.window._marker_texts["top"][0].isVisible())
        self.assertIn("X=0.2", self.window._marker_texts["top"][0].toPlainText())

    def test_left_click_semantics_move_cursor_without_marking(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        moved = self.window._move_cursor_to_point("top", 0.1, 0.49)
        self.assertTrue(moved)
        self.assertEqual(self.window._active_trace_names["top"], "ai1")
        self.assertAlmostEqual(self.window._cursor_positions["top"][0], 0.1, places=6)
        self.assertEqual(self.window._marker_positions["top"], [None, None])
        self.assertEqual(len(self.window._marker_history_points["top"]), 0)

    def test_mark_button_sets_reference_at_current_cursor(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._move_cursor_to_point("top", 0.2, 0.5)
        marked = self.window._toggle_mark_at_cursor("top")
        self.assertTrue(marked)
        self.assertAlmostEqual(self.window._marker_positions["top"][0], 0.2, places=6)
        self.assertAlmostEqual(self.window._marker_positions["top"][1], 0.2, places=6)
        self.assertTrue(self.window.top_mark_button.isChecked())

    def test_peak_search_updates_cursor(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_trace_combo.setCurrentIndex(
            self.window.top_trace_combo.findData("ai0")
        )
        self.window._find_trace_extremum("top", "peak")
        self.assertAlmostEqual(self.window._cursor_positions["top"][0], 0.1, places=6)
        self.assertEqual(self.window._marker_positions["top"], [None, None])

    def test_clear_marker_pair_resets_positions(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._marker_positions["top"] = [0.1, 0.2]
        self.window._clear_marker_pair("top")
        self.assertEqual(self.window._marker_positions["top"], [None, None])
        self.assertEqual(self.window.top_marker_fields["x1"].text(), "--")
        self.assertEqual(len(self.window._marker_history_points["top"]), 0)

    def test_data_tips_keep_multiple_coordinate_labels(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._toggle_data_tips(True)
        self.window._place_data_tip("top", 0.1, 1.0)
        self.window._place_data_tip("top", 0.2, 0.5)
        self.assertEqual(len(self.window._data_tip_items["top"]), 2)
        tip_text = self.window._data_tip_items["top"][0]["text"].toPlainText()
        self.assertIn("X 0.1", tip_text)
        self.assertNotIn("ai0", tip_text)
        curve = self.window._plot_curve_items["top"]["ai0"]
        self.assertGreater(self.window._data_tip_items["top"][0]["text"].zValue(), curve.zValue())
        self.assertGreater(self.window._data_tip_items["top"][0]["point"].zValue(), curve.zValue())

    def test_data_tip_label_anchor_avoids_top_and_right_edges(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window.top_plot.setXRange(0.0, 0.3, padding=0.0)
        self.window.top_plot.setYRange(-0.3, 1.0, padding=0.0)

        anchor = self.window._data_tip_anchor_for_plot_point(
            "top",
            self.window._x_to_plot_coord("top", 0.3),
            self.window._y_to_plot_coord("top", 1.0),
        )

        self.assertEqual(anchor, (1.05, -0.05))

    def test_toolbar_data_tip_button_toggles_all_data_tip_controls(self):
        self.window.toolbar_data_tip_button.setChecked(True)
        self.assertTrue(self.window._data_tip_enabled)
        self.assertTrue(self.window.data_tip_action.isChecked())
        self.assertTrue(self.window.top_data_tip_button.isChecked())
        self.assertTrue(self.window.bottom_data_tip_button.isChecked())

        self.window.toolbar_data_tip_button.setChecked(False)
        self.assertFalse(self.window._data_tip_enabled)
        self.assertFalse(self.window.data_tip_action.isChecked())

    def test_data_tip_drag_updates_to_nearest_curve_point(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._place_data_tip("top", 0.1, 1.0)
        data_tip = self.window._data_tip_items["top"][0]

        moved = self.window._drag_data_tip_to_scene_pos(
            "top",
            data_tip,
            self.window.top_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.2, 0.5)),
        )

        self.assertTrue(moved)
        self.assertAlmostEqual(data_tip["x"], 0.2, places=6)
        self.assertIn("X 0.2", data_tip["text"].toPlainText())

    def test_delete_data_tip_removes_only_selected_label(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._place_data_tip("top", 0.1, 1.0)
        self.window._place_data_tip("top", 0.2, 0.5)
        data_tip = self.window._data_tip_items["top"][0]

        deleted = self.window._delete_data_tip("top", data_tip)

        self.assertTrue(deleted)
        self.assertEqual(len(self.window._data_tip_items["top"]), 1)
        self.assertNotIn(data_tip, self.window._data_tip_items["top"])

    def test_data_tip_text_supports_context_menu_callback(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._place_data_tip("top", 0.1, 1.0)
        data_tip = self.window._data_tip_items["top"][0]

        self.assertIsInstance(data_tip["text"], main_window_module.DataTipText)
        self.assertIsNotNone(data_tip["text"]._on_context_menu)

    def test_main_data_tip_context_menu_suppresses_plot_context_menu(self):
        self.window._suppress_plot_context_menu_once()

        self.assertTrue(self.window._suppress_next_plot_context_menu)

        with mock.patch.object(self.window, "_build_plot_context_menu") as build_menu:
            self.window._show_plot_context_menu(self.window.top_plot, "top", QtCore.QPoint(0, 0))

        build_menu.assert_not_called()
        self.assertFalse(self.window._suppress_next_plot_context_menu)

    def test_detached_data_tip_context_menu_suppresses_plot_context_menu(self):
        detached = self.window._detached_plot_window

        detached._suppress_plot_context_menu_once()

        self.assertTrue(detached._suppress_next_plot_context_menu)

    def test_clear_all_data_tips_removes_top_and_bottom_labels(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window.bottom_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)
        self.window._place_data_tip("top", 0.1, 1.0)
        self.window._place_data_tip("bottom", 0.1, 1.0)

        self.window._clear_all_data_tips()

        self.assertEqual(len(self.window._data_tip_items["top"]), 0)
        self.assertEqual(len(self.window._data_tip_items["bottom"]), 0)

    def test_dragging_plot_moves_cursor(self):
        measurement = self._measurement()
        self.controller.state.measurement = measurement
        self.window.top_display_combo.setCurrentText("time")
        self.window._plot_measurement(measurement)

        moved = self.window._move_cursor_from_scene_pos(
            "top",
            self.window.top_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.2, 0.5)),
        )

        self.assertTrue(moved)
        self.assertAlmostEqual(self.window._cursor_positions["top"][0], 0.2, places=6)

    def test_channel_set_all_keeps_other_channel_physical_names(self):
        self.window.channel_list.setCurrentRow(0)
        self.window.channel_set_all_checkbox.setChecked(True)
        self.window.channel_full_scale_combo.setCurrentText("2.5 V")
        self.window.channel_label_edit.setText("ref0")
        self.window._apply_channel_editor_to_row()
        self.assertEqual(self.window.channel_table.item(0, 9).text(), "2.5")
        self.assertEqual(self.window.channel_table.item(1, 9).text(), "2.5")
        self.assertEqual(self.window.channel_table.item(0, 10).text(), "ref0")
        self.assertNotEqual(self.window.channel_table.item(1, 2).text(), self.window.channel_table.item(0, 2).text())

    def test_channel_grid_tracks_selected_row_and_label(self):
        self.window.channel_list.setCurrentRow(1)
        self.assertEqual(self.window.channel_grid.currentRow(), 1)
        self.window.channel_label_edit.setText("resp1")
        self.window._apply_channel_editor_to_row()
        self.assertEqual(self.window.channel_grid.item(1, 5).text(), "resp1")
        self.assertEqual(self.window.channel_grid.item(1, 2).text(), "10")
        self.window.channel_grid.item(1, 4).setText("0.5")
        self.assertEqual(self.window.channel_table.item(1, 11).text(), "0.5")

    def test_channel_grid_full_scale_text_accepts_mv_units(self):
        measurement = self._measurement()
        self.window.top_display_combo.setCurrentText("y(t)")
        self.window._manual_y_ranges["top"] = (-0.2, 0.2)
        self.window._plot_measurement(measurement)

        self.window.channel_grid.item(0, 2).setText("625 mV")

        y_range = self.window.top_plot.viewRange()[1]
        self.assertEqual(self.window.channel_table.item(0, 9).text(), "0.625")
        self.assertIsNone(self.window._manual_y_ranges["top"])
        self.assertLessEqual(y_range[0], -0.78125)
        self.assertGreaterEqual(y_range[1], 0.78125)

    def test_mc_setup_dialog_reflects_imported_legacy_channel_config(self):
        from python_vna.storage import load_legacy_vna

        imported = load_legacy_vna(r"D:\SynologyDrive\codex\vna\dsa\vna\sample.vna")
        self.controller.set_session(imported.config)
        self.window.session = imported.config
        self.window._load_session_to_widgets()

        dialog = MCSetupDialog(self.window)

        self.assertEqual(dialog.table.item(0, 1).text(), "625 mV")
        self.assertEqual(dialog.table.item(1, 1).text(), "2.5 V")
        self.assertEqual(dialog.table.item(0, 4).text(), "Channel 1")
        self.assertEqual(dialog.table.item(0, 6).text(), "Gs")
        self.assertEqual(dialog.table.item(0, 7).text(), "Off")
        dialog.close()

    def test_mc_setup_dialog_reflects_bias_and_eu_from_legacy_vdlg(self):
        from pathlib import Path

        from python_vna.storage import load_legacy_vna

        candidates = [
            path
            for path in Path("D:/SynologyDrive").rglob("003.vna")
            if "651D-R" in str(path) and str(path).endswith(r"651D-R\003.vna")
        ]
        if not candidates:
            self.skipTest("651D-R 003.vna fixture is not available")
        imported = load_legacy_vna(candidates[0])
        self.controller.set_session(imported.config)
        self.window.session = imported.config
        self.window._load_session_to_widgets()

        dialog = MCSetupDialog(self.window)

        self.assertEqual(dialog.table.rowCount(), 4)
        self.assertEqual(dialog.table.item(0, 2).text(), "bias")
        self.assertEqual(dialog.table.item(0, 5).text(), "1")
        self.assertEqual(dialog.table.item(2, 0).text(), "Ch 3")
        self.assertEqual(dialog.table.item(2, 0).checkState(), QtCore.Qt.Unchecked)
        self.assertEqual(dialog.table.item(2, 5).text(), "20")
        self.assertEqual(dialog.table.item(2, 7).text(), "/Volt")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
