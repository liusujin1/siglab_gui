"""MAT v5 .idefigure compatibility tests."""

from __future__ import annotations

import numpy as np
from pathlib import Path

from python_sidmat.measurement.figurefile import (
    FigureModel,
    FigureSeries,
    IdeFigure,
    load_idefigure,
    save_idefigure,
)


def test_idefigure_roundtrip(tmp_path):
    path = tmp_path / "plot.idefigure"
    source = IdeFigure(
        figure_title="Measurement",
        rows=2,
        columns=2,
        models=[
            FigureModel(
                title="FRF",
                log_x=True,
                grid="on",
                series=[
                    FigureSeries(
                        title="H1",
                        x=np.array([1.0, 2.0, 4.0]),
                        y=np.array([0.5, 0.25, 0.125]),
                    )
                ],
            )
        ],
    )
    save_idefigure(source, str(path))
    loaded = load_idefigure(str(path))
    assert loaded.rows == 2
    assert loaded.columns == 2
    assert loaded.models[0].title == "FRF"
    assert loaded.models[0].log_x
    np.testing.assert_allclose(loaded.models[0].series[0].x, [1.0, 2.0, 4.0])
    np.testing.assert_allclose(loaded.models[0].series[0].y, [0.5, 0.25, 0.125])


def test_loads_scipy_mat_v5_golden_with_sparse_model_and_series_numbers():
    fixture = Path(__file__).with_name("fixtures") / "scipy_idefigure_v5.idefigure"
    loaded = load_idefigure(str(fixture))

    assert loaded.figure_title == "兼容性测试"
    assert loaded.rows == 1
    assert loaded.columns == 2
    assert [model.title for model in loaded.models] == ["FRF", "Model 2"]
    assert loaded.models[0].log_x is True
    assert loaded.models[0].x_title == "频率"
    assert loaded.models[0].y_title == "幅值"
    assert [series.title for series in loaded.models[0].series] == ["H1", "H3"]
    np.testing.assert_allclose(loaded.models[0].series[1].x, [1.0, 2.0, 3.0])
    np.testing.assert_allclose(loaded.models[0].series[1].y, [10.0, 20.0, 30.0])
    assert loaded.models[1].series == []
