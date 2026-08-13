"""Tests for raw-data file I/O (.sidimat19x mat + CSV) and controller methods."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.backend.controller import Controller, ControllerError
from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.datafile import (
    export_raw,
    export_trace_config,
    import_raw,
    import_trace_config,
)
from python_sidmat.measurement.trace import TraceParameters


# ---------------------------------------------------------------------------
# Raw CSV roundtrip
# ---------------------------------------------------------------------------


def _sample_raw() -> MeasurementRawData:
    return MeasurementRawData(
        sig_name=["X1FB", "Y1FB"],
        data=[[float(i) for i in range(10)], [float(i) * 2 for i in range(10)]],
        sample_rate=2000,
        undersample=1,
        avg_num=2,
        sample_num=10,
    )


def test_raw_csv_roundtrip():
    raw = _sample_raw()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "trace.csv")
        export_raw(raw, path)
        rf = import_raw(path)

    assert rf.sample_rate == 2000
    assert rf.undersample == 1
    assert rf.avg_num == 2
    assert rf.sample_num == 10
    assert rf.sig0_name == "X1FB"
    assert rf.sig1_name == "Y1FB"
    assert rf.ch0 == [float(i) for i in range(10)]
    assert rf.ch1 == [float(i) * 2 for i in range(10)]


def test_raw_csv_export_has_metadata_header():
    raw = _sample_raw()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "trace.csv")
        export_raw(raw, path)
        with open(path, encoding="utf-8") as f:
            head = f.read(200)
    assert "sig0_name,X1FB" in head
    assert "sig1_name,Y1FB" in head
    assert "sample_rate,2000" in head
    assert "time,ch0,ch1" in head


def test_raw_csv_headerless_import():
    # Plain two-column numeric CSV with no header must still import.
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "plain.csv")
        with open(path, "w") as f:
            f.write("1.5,2.5\n3.5,4.5\n")
        rf = import_raw(path)
    assert rf.ch0 == [1.5, 3.5]
    assert rf.ch1 == [2.5, 4.5]


def test_raw_csv_export_keeps_longer_second_channel():
    raw = MeasurementRawData(
        sig_name=["a", "b"], data=[[1.0], [2.0, 3.0]], sample_rate=1000
    )
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "unequal.csv")
        export_raw(raw, path)
        rf = import_raw(path)
    assert rf.ch0 == [1.0, 0.0]
    assert rf.ch1 == [2.0, 3.0]


def test_raw_to_raw_back():
    raw = _sample_raw()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "trace.csv")
        export_raw(raw, path)
        rf = import_raw(path)
    raw2 = rf.to_raw()
    assert list(raw2.channel(0)) == list(raw.channel(0))
    assert raw2.sig_name == ["X1FB", "Y1FB"]


def test_raw_metadata_is_sanitized_and_actual_rows_win(tmp_path):
    path = tmp_path / "bad_meta.csv"
    path.write_text(
        "sample_rate,-10\nundersample,-2\navg_num,0\nsample_num,999\n"
        "time,ch0,ch1\n0,1,2\n0.1,3,4\n",
        encoding="utf-8",
    )
    rf = import_raw(path)
    assert rf.sample_rate == 0
    assert rf.undersample == 1
    assert rf.avg_num == 1
    assert rf.sample_num == 2
    raw = rf.to_raw()
    assert isinstance(raw.channel(0), np.ndarray)
    assert raw.sample_num == 2


# ---------------------------------------------------------------------------
# Trace config roundtrip
# ---------------------------------------------------------------------------


def test_trace_config_roundtrip():
    t = TraceParameters()
    t.trace_ch0 = IOType(0, 2, 0)
    t.trace_ch1 = IOType(5, 3, 1)
    t.no_samples = 256
    t.undersamples = 4
    t.average_number = 7
    t.trace_filter_flag = 1
    t.set_fast_data_loading(True)

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "trace.cfg")
        export_trace_config(t, path)
        t2 = TraceParameters()
        import_trace_config(t2, path)

    assert t2.trace_ch0.type == 0 and t2.trace_ch0.main_index == 2
    assert t2.trace_ch1.type == 5 and t2.trace_ch1.main_index == 3 and t2.trace_ch1.sub_index == 1
    assert t2.no_samples == 256
    assert t2.undersamples == 4
    assert t2.average_number == 7
    assert t2.trace_filter_flag == 1
    assert t2.is_fast_data_loading is True


def test_bad_trace_config_does_not_partially_mutate_live_trace(tmp_path):
    trace = TraceParameters(
        trace_ch0=IOType(0, 0, 0),
        trace_ch1=IOType(0, 1, 0),
        no_samples=100,
    )
    path = tmp_path / "broken.cfg"
    path.write_text(
        "trace_ch0 = 2 3 4\nno_samples = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no_samples"):
        import_trace_config(trace, path)
    assert trace.trace_ch0.encode() == (0, 0, 0)
    assert trace.no_samples == 100


# ---------------------------------------------------------------------------
# Controller backend methods (mock)
# ---------------------------------------------------------------------------


def test_controller_axis_loop_states():
    c = Controller.connect_mock(readonly=False)
    try:
        states = c.get_axis_loop_states()
        assert len(states) == 12
        assert all(isinstance(s, bool) for s in states)
        # Toggle one axis on and read it back.
        c.set_axis_loop_state(0, True)
        states2 = c.get_axis_loop_states()
        assert states2[0] is True
    finally:
        c.close()


def test_controller_noise_filter_roundtrip():
    c = Controller.connect_mock(readonly=False)
    try:
        usage = c.get_noise_filter_usage()
        assert isinstance(usage, str)
        c.set_noise_filter_usage("ON")
        assert c.get_noise_filter_usage() == "ON"
        stage = c.get_noise_filter_stage(0)
        assert stage.stage == 0
    finally:
        c.close()


def test_controller_system_info():
    c = Controller.connect_mock(readonly=False)
    try:
        info = c.get_system_info()
        for key in ("firmware", "sample_frequency", "loop", "trace"):
            assert key in info
    finally:
        c.close()


def test_controller_diagnostic_outputs_roundtrip():
    c = Controller.connect_mock(readonly=False)
    try:
        io0 = IOType(2, 1, 2)   # Vel Zrot Stage3
        io1 = IOType(5, 3, 0)   # Pos Ytrans output
        c.set_diagnostic_outputs(io0, io1)
        d0, d1 = c.get_diagnostic_outputs()
        assert d0.type == 2 and d0.main_index == 1 and d0.sub_index == 2
        assert d1.type == 5 and d1.main_index == 3 and d1.sub_index == 0
    finally:
        c.close()


def test_controller_diagnostic_outputs_short_response_is_rejected():
    # A malformed/short DGDOS response must not silently fabricate zero routes.
    c = Controller.connect_mock(readonly=False)
    try:
        # stub the session to return fewer tokens than expected
        orig = c.session.get_diagnostic_outputs
        c.session.get_diagnostic_outputs = lambda: ["0", "0", "0"]
        with pytest.raises(ControllerError, match="short DGDOS response"):
            c.get_diagnostic_outputs()
        c.session.get_diagnostic_outputs = orig
    finally:
        c.close()


def test_failed_session_open_is_closed():
    class BrokenSession:
        def __init__(self):
            self.closed = False

        def open(self):
            raise RuntimeError("negotiation failed")

        def close(self):
            self.closed = True

    session = BrokenSession()
    with pytest.raises(RuntimeError, match="negotiation failed"):
        Controller._open_session(session)
    assert session.closed


# ---------------------------------------------------------------------------
# .sidimat19x MATLAB .mat format (matches Plot2MatFile.cs)
# ---------------------------------------------------------------------------


def _mat_sample() -> MeasurementRawData:
    return MeasurementRawData(
        sig_name=["X1FB", "Y1FB"],
        data=[[float(i) for i in range(6)], [float(i) * 2 for i in range(6)]],
        sample_rate=2000,
        undersample=1,
        avg_num=2,
        sample_num=6,
    )


def test_sidimat_mat_roundtrip():
    from python_sidmat.measurement.matfile import (
        load_sidimat_raw,
        save_sidimat_raw,
    )

    raw = _mat_sample()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "m.sidimat19x")
        save_sidimat_raw([raw], path)
        out = load_sidimat_raw(path)

    assert len(out) == 1
    rf = out[0]
    assert rf.sample_rate == 2000
    assert rf.undersample == 1
    assert rf.avg_num == 2
    assert rf.sample_num == 6
    assert rf.sig0_name == "X1FB"
    assert rf.sig1_name == "Y1FB"
    assert rf.ch0 == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert rf.ch1 == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]


def test_sidimat_mat_multiple_measurements():
    from python_sidmat.measurement.matfile import (
        load_sidimat_raw,
        save_sidimat_raw,
    )

    a = _mat_sample()
    b = MeasurementRawData(
        sig_name=["A", "B"],
        data=[[1.0], [2.0]],
        sample_rate=1000,
        sample_num=1,
        avg_num=1,
    )
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "multi.sidimat19x")
        save_sidimat_raw([a, b], path)
        out = load_sidimat_raw(path)

    assert len(out) == 2
    assert out[0].sig0_name == "X1FB"
    assert out[1].sig0_name == "A"
    assert out[1].ch0 == [1.0]
    assert out[1].ch1 == [2.0]


def test_loads_scipy_mat_v5_golden_with_non_contiguous_rawdat_and_stale_count():
    from python_sidmat.measurement.matfile import load_sidimat_raw

    fixture = Path(__file__).with_name("fixtures") / "scipy_sidimat_v5.sidimat19x"
    records = load_sidimat_raw(str(fixture))

    assert len(records) == 2
    assert records[0].sig0_name == "位移X"
    assert records[0].sig1_name == "Y1FB"
    assert records[0].sample_num == 4  # DataSet wins over stale SampleNumber=999.
    assert records[0].ch1 == [0.0, 2.0, 4.0, 6.0]
    assert records[1].sig0_name == ""
    assert records[1].sig1_name == ""
    assert records[1].sample_rate == 1000
    assert records[1].undersample == 2
    assert records[1].ch0 == [1.0, 2.0, 3.0]
    assert records[1].ch1 == [10.0, 20.0, 30.0]
