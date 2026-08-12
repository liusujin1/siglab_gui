"""Off-screen interaction tests for the standalone logging record viewer."""

from __future__ import annotations

from math import pi
from pathlib import Path
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("scipy")

from PySide6 import QtCore, QtWidgets

from python_samba.logging_tools.models import LoggingRecord
from python_samba.ui.record_plot import RecordPlotWindow


def _application() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _record(channels: int = 8, samples: int = 512) -> LoggingRecord:
    sample_rate = 1000.0
    x = np.arange(samples, dtype=np.float64) / sample_rate
    headers = ["elapsed_s", *[f"Signal {index + 1}" for index in range(channels)]]
    rows = [
        [
            float(x_value),
            *[
                float(np.sin(2.0 * pi * (10.0 + channel) * x_value))
                for channel in range(channels)
            ],
        ]
        for x_value in x
    ]
    return LoggingRecord(
        headers,
        rows,
        source="record.csv",
        metadata={"sample_rate_hz": sample_rate},
    )


def _select_curve(window: RecordPlotWindow, curve_id: str) -> None:
    window.curve_tree.clearSelection()
    for row in range(window.curve_tree.topLevelItemCount()):
        item = window.curve_tree.topLevelItem(row)
        if item.data(0, QtCore.Qt.UserRole) == curve_id:
            item.setSelected(True)
            window.curve_tree.setCurrentItem(item)
            return
    raise AssertionError(f"curve not found: {curve_id}")


def test_record_window_lists_all_numeric_channels_and_shows_first_six():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=40))
        app.processEvents()

        assert window.isWindow()
        assert window.plot_tabs.count() == 2
        assert window.curve_tree.topLevelItemCount() == 40
        assert len(window._visible_ids) == 6
        assert len(window._curve_items["time"]) == 6
        assert not hasattr(window, "record_table")
        assert window.width() >= 1050
        assert window.height() >= 700
        assert window.record_summary.text().startswith("512 samples · 40 numeric signals")

        first = window.curve_tree.topLevelItem(0)
        first_id = first.data(0, QtCore.Qt.UserRole)
        first.setCheckState(0, QtCore.Qt.Unchecked)
        app.processEvents()
        assert first_id not in window._visible_ids
        assert len(window._curve_items["time"]) == 5

        previous_item = next(iter(window._curve_items["time"].values()))
        window.set_record(_record(channels=1, samples=64))
        app.processEvents()
        assert previous_item not in window.time_plot._record_view_box.addedItems
        assert len(window._curve_items["time"]) == 1
    finally:
        window.close()
        app.processEvents()


def test_processing_replaces_selected_curve_without_adding_rows(tmp_path: Path):
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=2, samples=2048))
        source = window.analysis_session.curves[0]
        source_copy = source.y.copy()
        original_count = window.curve_tree.topLevelItemCount()
        _select_curve(window, source.curve_id)

        detrended = window.detrend_selected("constant")
        assert len(detrended) == 1
        assert detrended[0].curve_id == source.curve_id
        assert detrended[0].name == source.name
        assert not detrended[0].derived
        assert not np.array_equal(detrended[0].y, source_copy)
        assert window.curve_tree.topLevelItemCount() == original_count
        assert window.curve_tree.topLevelItem(0).text(3) == "Modified"
        assert detrended[0].curve_id in window._visible_ids

        _select_curve(window, detrended[0].curve_id)
        spectra = window.fft_selected()
        assert len(spectra) == 1
        assert spectra[0].domain == "frequency"
        assert window.plot_tabs.currentIndex() == 1
        assert spectra[0].curve_id == source.curve_id
        assert spectra[0].curve_id in window._curve_items["frequency"]
        assert source.curve_id in window._curve_items["time"]
        assert window.analysis_session.get_curve(source.curve_id).domain == "time"
        assert window.curve_tree.topLevelItemCount() == original_count
        assert window.curve_tree.topLevelItem(0).text(2) == "Time + Spectrum"

        window.frequency_db.setChecked(True)
        nearest = window._nearest_for_view_x("frequency", spectra[0].curve_id, 10.0)
        window._update_cursor("frequency", nearest)
        window.set_marker("A")
        assert "dB" in window.marker_readout.text()
        assert "dB" in window.status_label.text()

        output = window.export_selected_to(tmp_path / "selected.csv")
        assert output.exists()
        assert output.with_suffix(".csv.meta.json").exists()
    finally:
        window.close()
        app.processEvents()


def test_frequency_defaults_cutoffs_and_auto_fit_are_usable():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=1, samples=4096))
        source = window.analysis_session.curves[0]
        _select_curve(window, source.curve_id)

        assert window.frequency_x_log.isChecked()
        assert window.frequency_db.isChecked()
        assert window.filter_low.value() == pytest.approx(5.0)
        assert window.filter_high.value() == pytest.approx(100.0)
        assert window.filter_low.isEnabled()
        assert window.filter_high.isEnabled()
        assert not window.filter_low.lineEdit().isReadOnly()
        assert not window.filter_high.lineEdit().isReadOnly()
        window.filter_low.setValue(7.5)
        window.filter_high.setValue(125.0)
        assert window.filter_low.value() == pytest.approx(7.5)
        assert window.filter_high.value() == pytest.approx(125.0)

        spectrum = window.fft_selected()[0]
        x_values, y_values, finite = window._display_data(spectrum)
        assert len(finite) > 0
        assert np.all(np.isfinite(x_values[finite]))
        assert np.all(np.isfinite(y_values[finite]))
        assert np.all(spectrum.x[finite] > 0.0)

        x_range, y_range = window.frequency_plot._record_view_box.viewRange()
        assert np.all(np.isfinite([*x_range, *y_range]))
        assert y_range[1] - y_range[0] < 135.0
    finally:
        window.close()
        app.processEvents()


def test_cursor_markers_tips_zoom_and_copy_are_available():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=1, samples=512))
        window.show()
        app.processEvents()
        source = window.analysis_session.curves[0]
        _select_curve(window, source.curve_id)
        nearest = window._nearest_for_view_x("time", source.curve_id, 0.2)
        assert nearest is not None

        window._update_cursor("time", nearest)
        assert window._cursor_state["time"]["nearest"][0].curve_id == source.curve_id
        window.set_marker("A")
        window.time_plot._record_view_box.setRange(xRange=(0.3, 0.5), padding=0)
        window._update_cursor(
            "time", window._nearest_for_view_x("time", source.curve_id, 0.4)
        )
        window.set_marker("B")
        assert set(window._markers["time"]) == {"A", "B"}
        assert "Δ" in window.marker_readout.text()

        window._add_data_tip("time", nearest)
        assert len(window._data_tips) == 1
        window.clear_data_tips("time")
        assert not window._data_tips

        original_range = window.time_plot._record_view_box.viewRange()
        window._remember_range("time")
        window.time_plot._record_view_box.setRange(xRange=(0.1, 0.2), padding=0)
        window.previous_zoom(domain="time")
        restored = window.time_plot._record_view_box.viewRange()
        assert restored[0] == pytest.approx(original_range[0])
        assert window.copy_active_plot()
        assert not QtWidgets.QApplication.clipboard().pixmap().isNull()
    finally:
        window.close()
        app.processEvents()


def test_checking_or_plot_clicking_other_curves_changes_processing_target():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=3, samples=2048))
        sources = window.analysis_session.curves[:3]

        second_item = window.curve_tree.topLevelItem(1)
        window.curve_tree.itemClicked.emit(second_item, 0)
        app.processEvents()
        assert window.selected_curve_ids() == [sources[1].curve_id]
        second_fft = window.fft_selected()
        assert len(second_fft) == 1
        assert second_fft[0].curve_id == sources[1].curve_id
        assert second_fft[0].curve_id in window._curve_items["frequency"]
        assert len(window.frequency_plot._record_legend.items) == 1

        window.plot_tabs.setCurrentIndex(0)
        fake_click = type(
            "CurveClick",
            (),
            {"modifiers": lambda self: QtCore.Qt.NoModifier},
        )()
        previous_item = window._curve_items["time"][sources[2].curve_id]
        previous_curve_item = previous_item.curve
        window._plot_curve_clicked(sources[2].curve_id, fake_click)
        assert window.selected_curve_ids() == [sources[2].curve_id]
        assert window._curve_items["time"][sources[2].curve_id] is previous_item
        assert previous_item.curve is previous_curve_item
        # Accessing flags reproduces the pyqtgraph mouse-release crash when
        # the C++ PlotCurveItem was deleted by the click callback.
        previous_curve_item.flags()
        third_detrended = window.detrend_selected("constant")
        assert len(third_detrended) == 1
        assert third_detrended[0].curve_id == sources[2].curve_id
    finally:
        window.close()
        app.processEvents()


def test_ctrl_plot_click_selects_multiple_curves_for_processing():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=3, samples=512))
        sources = window.analysis_session.curves[:3]
        _select_curve(window, sources[0].curve_id)
        additive_click = type(
            "CurveClick",
            (),
            {"modifiers": lambda self: QtCore.Qt.ControlModifier},
        )()
        window._plot_curve_clicked(sources[1].curve_id, additive_click)
        assert set(window.selected_curve_ids()) == {
            sources[0].curve_id,
            sources[1].curve_id,
        }
        updated = window.detrend_selected("constant")
        assert {curve.curve_id for curve in updated} == {
            sources[0].curve_id,
            sources[1].curve_id,
        }
        assert window.curve_tree.topLevelItemCount() == 3
    finally:
        window.close()
        app.processEvents()


def test_data_tip_context_suppresses_duplicate_plot_menu():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=1, samples=64))
        source = window.analysis_session.curves[0]
        nearest = window._nearest_for_view_x("time", source.curve_id, 0.02)
        window._add_data_tip("time", nearest)
        # The tip item's accepted right click sets this guard before opening
        # its own menu.  Verify that the following scene click cannot open the
        # plot menu as well; do not launch a modal QMenu in offscreen CI.
        window._tip_menu_suppressed_until = time.monotonic() + 0.5

        calls: list[str] = []
        window._show_plot_menu = lambda domain, _position: calls.append(domain)
        right_click = type(
            "SceneClick",
            (),
            {
                "button": lambda self: QtCore.Qt.RightButton,
                "screenPos": lambda self: QtCore.QPointF(20.0, 20.0),
            },
        )()
        window._plot_scene_clicked("time", right_click)
        assert calls == []
    finally:
        window.close()
        app.processEvents()


def test_irregular_record_resample_replaces_selected_curve():
    app = _application()
    window = RecordPlotWindow()
    record = LoggingRecord(
        ["elapsed_s", "A"],
        [[0.0, 0.0], [0.01, 1.0], [0.0208, 0.0], [0.03, -1.0], [0.04, 0.0]],
        metadata={"sample_rate_hz": 100.0},
    )
    try:
        window.set_record(record)
        source = window.analysis_session.curves[0]
        _select_curve(window, source.curve_id)
        assert not window.analysis_session.sampling.regular
        updated = window.resample_selected()
        assert len(updated) == 1
        assert updated[0].curve_id == source.curve_id
        assert updated[0].operation["type"] == "resample"
        assert window.curve_tree.topLevelItemCount() == 1
        assert window.analysis_session.can_process(updated[0].curve_id) == (True, "")
    finally:
        window.close()
        app.processEvents()


def test_sample_index_axis_and_rate_control_reset_between_records():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=1, samples=64))
        assert window.sample_rate.value() == pytest.approx(1000.0)
        assert window.time_plot.getPlotItem().getAxis("bottom").labelText == "Time (s)"

        window.set_record(LoggingRecord(["A"], [[0.0], [1.0], [0.0], [-1.0]]))
        assert window.sample_rate.value() == 0.0
        assert window.sample_rate.text() == "Not set"
        assert window.time_plot.getPlotItem().getAxis("bottom").labelText == "Sample"

        window.sample_rate.setValue(10.0)
        window._sample_rate_edited()
        assert window.time_plot.getPlotItem().getAxis("bottom").labelText == "Time (s)"
        curve = window.analysis_session.curves[0]
        displayed_x, _displayed_y = window._display_arrays(curve)
        assert displayed_x.tolist() == pytest.approx([0.0, 0.1, 0.2, 0.3])
    finally:
        window.close()
        app.processEvents()


def test_processing_clears_stale_cursor_annotation_and_keeps_curve_row():
    app = _application()
    window = RecordPlotWindow()
    try:
        window.set_record(_record(channels=1, samples=128))
        source = window.analysis_session.curves[0]
        _select_curve(window, source.curve_id)
        nearest = window._nearest_for_view_x("time", source.curve_id, 0.05)
        window._update_cursor("time", nearest)
        assert "time" in window._cursor_state

        updated = window.detrend_selected("constant")[0]
        assert "time" not in window._cursor_state
        assert updated.curve_id == source.curve_id
        assert len(window.analysis_session.curves) == 1
    finally:
        window.close()
        app.processEvents()


def test_large_record_plot_setup_uses_full_data_with_view_downsampling():
    app = _application()
    samples = 100_000
    x = np.arange(samples, dtype=np.float64) / 5000.0
    record = LoggingRecord(
        ["elapsed_s", "A", "B"],
        np.column_stack((x, np.sin(2 * pi * 5 * x), np.cos(2 * pi * 7 * x))).tolist(),
        metadata={"sample_rate_hz": 5000.0},
    )
    window = RecordPlotWindow()
    try:
        started = time.perf_counter()
        window.set_record(record)
        app.processEvents()
        elapsed = time.perf_counter() - started
        assert elapsed < 8.0
        assert len(window.analysis_session.curves[0].y) == samples
        plot_item = window._curve_items["time"][window.analysis_session.curves[0].curve_id]
        assert plot_item.opts["autoDownsample"] is True
        assert plot_item.opts["clipToView"] is True
    finally:
        window.close()
        app.processEvents()
