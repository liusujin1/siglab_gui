"""Regression tests for the reusable crosshair plot panel."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from python_sidmat.ui.plots import MeasurementPlots


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_show_raw_keeps_crosshair_data_and_two_curves():
    _app()
    plots = MeasurementPlots()
    plots.show_raw(
        np.array([1.0, 2.0, 3.0]),
        np.array([3.0, 2.0, 1.0]),
        sample_rate=1000.0,
        name0="input",
        name1="output",
        undersample=2,
    )
    assert plots.time_plot._last_data is not None
    np.testing.assert_allclose(plots.time_plot._last_data[0], [0.0, 0.002, 0.004])
    assert plots.time_plot._data_name == "input"
    assert len(plots.time_plot.listDataItems()) == 2
    assert plots.time_plot._label in plots.time_plot.getPlotItem().items
    plots.close()
