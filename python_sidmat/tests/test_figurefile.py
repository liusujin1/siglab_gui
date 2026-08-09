"""MAT v5 .idefigure compatibility tests."""

from __future__ import annotations

import numpy as np

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
