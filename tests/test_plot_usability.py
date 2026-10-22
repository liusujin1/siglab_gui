from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtTest, QtWidgets

from python_vna.diagnostic.data import CurvePair, NumericTableFile, TraceAnalysisFile
from python_vna.diagnostic.pages import TraceAnalysisPage, VibrationAnalysisPage
from python_vna.ui.analysis_viewer import AnalysisWorkbench
from python_vna.ui.plot_interactions import edit_legend_size


@pytest.fixture
def app():
    instance = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield instance
    instance.processEvents()


@pytest.fixture(params=[AnalysisWorkbench, VibrationAnalysisPage, TraceAnalysisPage])
def page(request, app):
    widget = request.param()
    yield widget
    for dialog in list(getattr(widget, "_single_plot_windows", [])) + list(getattr(widget, "_plot_windows", [])):
        dialog.close()
    widget.close()


def populated_plot(page):
    plot = page._create_plot_widget("Original title")
    plot.setLabel("bottom", "Time")
    plot.setLabel("left", "Response")
    plot.plot([1., 2., 3.], [4., 5., 6.], name="Signal")
    page._plot_curves[plot] = {"Signal": (np.arange(1., 4.), np.arange(4., 7.))}
    page._active_trace[plot] = "Signal"
    return plot


def test_data_annotations_disable_cursor_until_switched_off(page):
    plot = populated_plot(page)
    assert page._set_cursor_position(plot, 2., 5.)
    page._toggle_data_tip_mode(True)
    assert not page._cursor_enabled
    assert all(not item.isVisible() for item in page._cursor_items[plot].values())
    page._toggle_cursor_readout(True)
    assert not page._cursor_enabled
    assert not page._set_cursor_position(plot, 1., 4.)
    menu, actions = page._build_plot_context_menu(plot)
    assert actions["data_tip"].text() == "数据标注"
    assert actions["data_tip"].isChecked()
    assert not actions["cursor"].isEnabled()
    assert page._place_data_tip(plot, 2., 5.)
    assert len(page._data_tip_items[plot]) == 1
    page._toggle_data_tip_mode(False)
    assert page._cursor_enabled
    assert page._set_cursor_position(plot, 1., 4.)
    assert len(page._data_tip_items[plot]) == 1
    menu.deleteLater()
    plot.close()


def test_detached_legend_editor_is_owned_by_plot_window(page, monkeypatch):
    plot = populated_plot(page)
    dialog = QtWidgets.QDialog()
    QtWidgets.QVBoxLayout(dialog).addWidget(plot)
    prompt = Mock(return_value=("Renamed", True))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", prompt)
    try:
        assert page._prompt_rename_plot_curve(plot, "Signal")
        assert prompt.call_args.args[0] is dialog
        assert "Renamed" in page._plot_curves[plot]
        assert plot.plotItem.legend.items[0][1].text == "Renamed"
    finally:
        dialog.close()


@pytest.mark.parametrize("target", ["title", "bottom", "left"])
def test_plot_labels_edit_on_real_double_click(page, target, app, monkeypatch):
    plot = populated_plot(page)
    plot.resize(800, 480)
    plot.show()
    app.processEvents()
    prompt = Mock(return_value=("Edited label", True))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", prompt)
    item = plot.plotItem.titleLabel.item if target == "title" else plot.getAxis(target).label
    position = plot.mapFromScene(item.sceneBoundingRect().center())
    QtTest.QTest.mouseClick(plot.viewport(), QtCore.Qt.LeftButton, pos=position)
    QtTest.QTest.mouseDClick(plot.viewport(), QtCore.Qt.LeftButton, pos=position)
    app.processEvents()
    assert prompt.call_count == 1
    assert prompt.call_args.args[0] is plot.window()
    actual = plot.plotItem.titleLabel.text if target == "title" else plot.getAxis(target).labelText
    assert actual == "Edited label"
    plot.close()


def test_legend_size_can_be_changed_by_wheel_and_menu(page, monkeypatch):
    plot = populated_plot(page)
    legend = plot.plotItem.legend
    initial_height = legend.items[0][1].item.boundingRect().height()
    initial = legend.font_point_size()
    wheel = SimpleNamespace(delta=lambda: 120, accept=Mock())
    legend.wheelEvent(wheel)
    assert legend.font_point_size() == initial + 1
    wheel.accept.assert_called_once()
    monkeypatch.setattr(QtWidgets.QInputDialog, "getInt", lambda *args: (16, True))
    edit_legend_size(plot)
    assert legend.font_point_size() == 16
    assert legend.items[0][1].opts["size"] == "16pt"
    assert legend.items[0][1].item.boundingRect().height() > initial_height
    plot.plot([1., 2.], [1., 2.], name="New curve")
    assert legend.items[-1][1].opts["size"] == "16pt"
    plot.close()


def test_vna_clear_all_removes_data_plots_annotations_and_detached_windows(app, tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("0,1\n0.1,2\n0.2,3\n0.3,4\n", encoding="utf-8")
    page = AnalysisWorkbench(include_derived_tab=False)
    try:
        page._load_path(path)
        page.series_list.item(0).setSelected(True)
        page.plot_current()
        plot = page.main_plots[0]
        page._set_cursor_position(plot, .1, 2.)
        page._place_data_tip(plot, .1, 2.)
        page._open_plot_window_for_plot(plot)
        assert page._single_plot_windows
        page.clear_all_button.click()
        assert not page._datasets and not page.series_list.count()
        assert not page._single_plot_windows
        assert not page._time_series_cache
        for item in page._all_analysis_plots():
            assert not page._plot_curves[item]
            assert not page._data_tip_items[item]
            assert all(not cursor.isVisible() for cursor in page._cursor_items[item].values())
    finally:
        page.close()


@pytest.mark.parametrize("page_type", [VibrationAnalysisPage, TraceAnalysisPage])
def test_diagnostic_clear_all_removes_data_and_plot_interaction_state(page_type, app, tmp_path):
    page = page_type()
    path = tmp_path / "trace.txt"
    path.write_text("Time\tACC_1\n0\t1\n0.1\t2\n0.2\t3\n0.3\t4\n", encoding="utf-8")
    try:
        page.load_paths([path])
        assert page.files
        plot = page.log_plot if isinstance(page, VibrationAnalysisPage) else page.ide_time_plot
        page._plot_curves_on_widget(plot, [CurvePair("Signal", np.array([1., 2.]), np.array([3., 4.]), "X", "Y")],
                                    title="Data", x_label="X", y_label="Y")
        page._set_cursor_position(plot, 1., 3.)
        page._place_data_tip(plot, 1., 3.)
        if isinstance(page, TraceAnalysisPage):
            page._open_plot_window(plot, "Wrong title")
        page.clear_all_button.click()
        assert not page.files and page.file_list.count() == 0
        assert not getattr(page, "_plot_windows", [])
        assert not page._plot_curves[plot] and not page._data_tip_items[plot]
        assert page._cursor_positions[plot] is None
        assert all(not cursor.isVisible() for cursor in page._cursor_items[plot].values())
    finally:
        page.close()


def test_renaming_vna_keeps_current_row_scroll_and_current_name(app, tmp_path, monkeypatch):
    path = tmp_path / "012.csv"
    path.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("012:Original condition\n", encoding="utf-8")
    page = AnalysisWorkbench(include_derived_tab=False)
    monkeypatch.setattr(page, "plot_current", lambda: None)
    try:
        for _ in range(30):
            page._load_path(path)
        for dataset in page._datasets:
            dataset.name = "Dataset_" + "long_name_" * 12
        page._refresh_dataset_lists()
        page.series_list.setFixedSize(140, 100)
        page.show()
        app.processEvents()
        page.series_list.setCurrentRow(20)
        page.series_list.verticalScrollBar().setValue(50)
        page.series_list.horizontalScrollBar().setValue(50)
        before = page.series_list.verticalScrollBar().value()
        horizontal_before = page.series_list.horizontalScrollBar().value()
        assert before > 0 and horizontal_before > 0
        current_id = page.series_list.currentItem().data(QtCore.Qt.UserRole)
        page.rename_edit.setText("Current renamed signal")
        page._rename_selected_series_from_editor()
        app.processEvents()
        assert page.series_list.currentItem().data(QtCore.Qt.UserRole) == current_id
        assert page.series_list.verticalScrollBar().value() == before
        assert page.series_list.horizontalScrollBar().value() == horizontal_before
        page.series_list.setCurrentRow(0)
        page.series_list.setCurrentRow(20)
        page._sync_series_editors_from_selection()
        assert page.rename_edit.text() == "Current renamed signal"
    finally:
        page.close()


def test_trace_detached_window_uses_actual_plot_title(app):
    page = TraceAnalysisPage()
    try:
        page._plot_curves_on_widget(page.ide_trans_magnitude_plot,
                                   [CurvePair("Signal", np.array([1., 2.]), np.array([3., 4.]), "Hz", "dB")],
                                   title="IDE Trans 幅频响应", x_label="Hz", y_label="dB")
        page._open_plot_window(page.ide_trans_magnitude_plot, "IDE Trans 时域图")
        dialog = page._plot_windows[0]
        assert dialog.windowTitle() == "IDE Trans 幅频响应"
        assert page.ide_trans_magnitude_window_button.text() == "幅频图"
        assert page.ide_trans_phase_window_button.text() == "相频图"
    finally:
        page._close_plot_windows()
        page.close()


def test_cangfu_transfer_button_matches_actual_plot_and_restores_time_mode(app):
    page = TraceAnalysisPage()
    pairs = [CurvePair("Transfer", np.array([1., 2.]), np.array([3., 4.]), "频率 (Hz)", "幅值 (dB)")]
    table = NumericTableFile(Path("trace084.txt"), "trace084", [], np.empty((2, 0)),
                             metadata={"cangfu_transfer_pairs": pairs})
    current = TraceAnalysisFile(table, "cangfu_trace", np.array([]), {}, 100.)
    try:
        page._refresh_cangfu_controls(current)
        assert page.cangfu_time_window_button.text() == "传函图"
        assert not page.cangfu_psd_window_button.isEnabled()
        page._plot_cangfu_current(current)
        page._open_plot_window(page.cangfu_time_plot, "Cangfu Trace 时域图")
        assert "传递函数" in page._plot_windows[0].windowTitle()
        table.metadata.clear()
        page._refresh_cangfu_controls(current)
        assert page.cangfu_time_window_button.text() == "时域图"
        assert page.cangfu_psd_window_button.isEnabled()
    finally:
        page._close_plot_windows()
        page.close()
