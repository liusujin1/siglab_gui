"""Regression tests for the Samba Records/Plot interaction layer."""

from __future__ import annotations

import csv

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets
from python_samba.ui.plot_interactions import InteractiveViewBox


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _window_with_curve():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win.show()
    win._toggle_time_plot()
    x = np.linspace(0.0, 4.0, 5)
    y = x * x
    win.time_plot._pw.plot(x, y, name="quadratic")
    win.time_plot._legend.show()
    win._finish_plot_refresh(win.time_plot, auto_fit=True)
    app.processEvents()
    return app, win


def test_window_uses_samba_metrics_font_scale_and_resize_grip():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    assert win._DESIGN_WINDOW_SIZE == (1240, 780)
    assert win._DESIGN_MINIMUM_SIZE == (960, 640)
    assert 0.67 <= win._font_scale <= 1.10
    assert app.property("python_samba_font_scale") == pytest.approx(win._font_scale)
    assert win._size_grip.size() == QtCore.QSize(18, 18)
    expected_base = app.font().pixelSize()
    assert f"font-size: {expected_base}px" in app.styleSheet()
    _available, initial, minimum, _scale = win._initial_window_metrics()
    assert win.size() == initial
    assert win.minimumSize() == minimum
    win.close()


def test_plot_pointer_tools_annotations_and_export(tmp_path):
    app, win = _window_with_curve()
    controller = win._plot_controller
    view = win.time_plot
    view_box = view._view_box

    assert isinstance(view_box, InteractiveViewBox)
    assert controller.pointer_tool() == "cursor"
    curve = view._pw.listDataItems()[0]
    assert getattr(curve, "_sidmat_interaction_bound")
    controller._curve_clicked(view, curve, None)
    assert controller.hide_selected_curve(view)
    assert not curve.isVisible()
    controller.show_all_curves(view)
    assert curve.isVisible()

    cursor_scene = view_box.mapViewToScene(QtCore.QPointF(2.0, 4.0))
    view_box._on_left_drag(cursor_scene)
    assert controller._cursor_state["time"]["nearest"]["index"] == 2

    controller.set_pointer_tool("data-tip", True)
    assert win.plot_data_tip_btn.isChecked()
    assert not win.plot_cursor_btn.isChecked()
    controller.handle_pointer(view, cursor_scene, dragging=False)
    assert len(controller._data_tips) == 1

    controller.set_marker("A", view=view)
    other_scene = view_box.mapViewToScene(QtCore.QPointF(4.0, 16.0))
    controller.set_pointer_tool("cursor", True)
    controller.handle_pointer(view, other_scene, dragging=False)
    controller.set_marker("B", view=view)
    assert set(controller._markers["time"]) == {"A", "B"}
    assert "Δ" in win.plot_marker_readout.text()
    assert "—" not in win.plot_marker_readout.text()

    output = controller.export_curves_to(tmp_path / "time.csv", view=view)
    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["curve", "x", "y"]
    assert rows[1] == ["quadratic", "0", "0"]
    assert len(rows) == 6

    controller.clear_annotations(view=view)
    assert "time" not in controller._cursor_state
    assert not controller._data_tips
    assert not controller._markers["time"]
    win.close()
    app.processEvents()


def test_rubber_zoom_and_previous_zoom_restore_range():
    app, win = _window_with_curve()
    controller = win._plot_controller
    view = win.time_plot
    view_box = view._view_box

    view_box.setRange(xRange=(-1.0, 5.0), yRange=(-2.0, 18.0), padding=0.0)
    before = view_box.viewRange()
    view_box._on_right_zoom(
        QtCore.QPointF(1.0, 1.0),
        QtCore.QPointF(3.0, 10.0),
    )
    zoomed = view_box.viewRange()
    assert zoomed[0] == pytest.approx([1.0, 3.0])
    assert zoomed[1] == pytest.approx([1.0, 10.0])
    assert controller.previous_zoom(view=view)
    restored = view_box.viewRange()
    assert restored[0] == pytest.approx(before[0])
    assert restored[1] == pytest.approx(before[1])
    win.close()
    app.processEvents()


def test_log_frequency_cursor_reports_linear_frequency():
    from python_sidmat.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win._toggle_frf_plot()
    win.frf_plot._pw.plot(
        np.array([1.0, 10.0, 100.0]),
        np.array([0.0, -3.0, -12.0]),
        name="FRF",
    )
    win._finish_plot_refresh(win.frf_plot, auto_fit=True)
    win.show()
    app.processEvents()
    view_box = win.frf_plot._view_box
    scene = view_box.mapViewToScene(QtCore.QPointF(1.0, -3.0))
    win._plot_controller.handle_pointer(win.frf_plot, scene, dragging=False)
    nearest = win._plot_controller._cursor_state["frf"]["nearest"]
    assert nearest["x"] == pytest.approx(10.0)
    assert nearest["plot_x"] == pytest.approx(1.0)
    assert "X 10" in win.status_lbl.text()
    win.close()
    app.processEvents()
