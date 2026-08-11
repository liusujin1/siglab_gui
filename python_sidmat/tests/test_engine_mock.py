"""Measurement engine tests against the python_samba mock controller."""

from __future__ import annotations

import pytest

from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.backend.controller import Controller
from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.engine import (
    MeasurementCancelled,
    MeasurementEngine,
)
from python_sidmat.measurement.trace import TraceParameters


def _make_controller() -> Controller:
    ctrl = Controller.connect_mock(readonly=False)
    return ctrl


def test_trace_encode_9_params() -> None:
    trace = TraceParameters(
        trace_ch0=IOType(0, 3, 0),
        trace_ch1=IOType(2, 0, 0),
        undersamples=1,
        no_samples=64,
        trace_filter_flag=1,
    )
    enc = trace.encode()
    assert enc == (0, 3, 0, 2, 0, 0, 1, 64, 1)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"undersamples": 65535}, "undersamples must be <= 65534"),
        ({"average_number": 1001}, "average_number must be <= 1000"),
    ],
)
def test_trace_rejects_values_beyond_ui_and_protocol_limits(kwargs, message):
    with pytest.raises(ValueError, match=message):
        TraceParameters(**kwargs)


def test_trace_from_tokens() -> None:
    trace = TraceParameters.from_tokens(["0", "1", "0", "2", "0", "0", "2", "128", "0"])
    assert trace.trace_ch0.encode() == (0, 1, 0)
    assert trace.trace_ch1.encode() == (2, 0, 0)
    assert trace.undersamples == 2
    assert trace.no_samples == 128
    assert trace.trace_filter_flag == 0


def test_read_chunk_count() -> None:
    trace = TraceParameters(no_samples=64, max_data_pair_per_rci=16)
    assert trace.read_chunk_count == 4
    trace = TraceParameters(no_samples=70, max_data_pair_per_rci=16)
    assert trace.read_chunk_count == 5


def test_set_get_trace_roundtrip() -> None:
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 2, 0),
            trace_ch1=IOType(2, 0, 7),
            undersamples=2,
            no_samples=128,
            trace_filter_flag=0,
        )
        ctrl.set_trace(trace)
        got = ctrl.get_trace()
        assert got.trace_ch0.encode() == (0, 2, 0)
        assert got.trace_ch1.encode() == (2, 0, 7)
        assert got.undersamples == 2
        assert got.no_samples == 128
        assert got.trace_filter_flag == 0


def test_excitation_roundtrip() -> None:
    from python_sidmat.measurement.excitation import ExcitationParameters

    with _make_controller() as ctrl:
        exc = ExcitationParameters(type=2, params=[0.5, 10.0, 0.0, 0.0])
        ctrl.set_excitation(exc)
        got = ctrl.get_excitation()
        assert got.type == 2
        assert got.params[0] == pytest.approx(0.5)
        assert got.params[1] == pytest.approx(10.0)


def test_noise_inject_roundtrip() -> None:
    with _make_controller() as ctrl:
        ctrl.set_noise_inject(IOType(2, 0, 0))
        io = ctrl.get_noise_inject()
        assert io.encode() == (2, 0, 0)


def test_diagnostic_and_excitation_offset_roundtrip() -> None:
    with _make_controller() as ctrl:
        ctrl.set_diagnostic_outputs(IOType(3, 0, 0), IOType(2, 4, 6))
        io0, io1 = ctrl.get_diagnostic_outputs()
        assert io0.encode() == (3, 0, 0)
        assert io1.encode() == (2, 4, 6)
        ctrl.set_excitation_offset(0.125)
        assert ctrl.get_excitation_offset() == pytest.approx(0.125)


def test_engine_single_average_mock() -> None:
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            undersamples=1,
            no_samples=64,
            average_number=1,
        )
        engine = MeasurementEngine(ctrl, trace, sample_frequency=1000.0)
        raw = engine.run()
        assert isinstance(raw, MeasurementRawData)
        assert raw.sample_num == 64
        assert raw.avg_num == 1
        # mock returns fewer pairs than requested; engine must still produce
        # data (real hardware fills up to no_samples)
        assert len(raw.channel(0)) > 0
        assert len(raw.channel(0)) == len(raw.channel(1))
        assert raw.sample_rate == 1000


def test_engine_multi_average_concatenates() -> None:
    with _make_controller() as ctrl:
        single_trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            undersamples=1,
            no_samples=32,
            average_number=1,
        )
        single = MeasurementEngine(ctrl, single_trace, 1000.0).run()
        single_len = len(single.channel(0))

        multi_trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            undersamples=1,
            no_samples=32,
            average_number=3,
        )
        multi = MeasurementEngine(ctrl, multi_trace, 1000.0).run()
        assert multi.avg_num == 3
        assert multi.sample_num == 32 * 3
        # each average yields the same chunk count, so 3x the single length
        assert len(multi.channel(0)) == single_len * 3
        assert len(multi.channel(1)) == len(multi.channel(0))


def test_engine_skips_average_on_dasta_error() -> None:
    """A non-zero DASTA error code skips that average (C# semantics)."""
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            no_samples=64,
            average_number=2,
        )
        engine = MeasurementEngine(ctrl, trace, sample_frequency=1000.0)
        # Simulate the controller rejecting every trigger.
        original = ctrl.start_trace
        ctrl.start_trace = lambda: ["1"]
        try:
            raw = engine.run()
        finally:
            ctrl.start_trace = original
        # Both averages were skipped -> no data, but no fabricated sample
        # count should be reported.
        assert raw.sample_num == 0
        assert raw.avg_num == 0
        assert len(raw.channel(0)) == 0


def test_engine_fast_load_keeps_full_trace_length() -> None:
    """The legacy 40-pair DGTBB path must keep the full trace."""
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            no_samples=64,
            average_number=1,
            is_fast_data_loading=True,
        )
        raw = MeasurementEngine(ctrl, trace, sample_frequency=1000.0).run()
        assert len(raw.channel(0)) == 64
        assert len(raw.channel(1)) == 64


@pytest.mark.parametrize("fast", [False, True])
def test_remote_engine_batches_trace_download(fast: bool) -> None:
    with _make_controller() as ctrl:
        ctrl.session.info.backend = "server"
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            no_samples=128,
            average_number=1,
            is_fast_data_loading=fast,
        )
        calls: list[list[object]] = []
        if fast:
            original = ctrl.get_trace_buffers_binary

            def wrapped(requests):
                calls.append(list(requests))
                return original(requests)

            ctrl.get_trace_buffers_binary = wrapped
        else:
            original = ctrl.get_trace_buffers

            def wrapped(offsets):
                calls.append(list(offsets))
                return original(offsets)

            ctrl.get_trace_buffers = wrapped
        raw = MeasurementEngine(ctrl, trace, sample_frequency=1000.0).run()
        assert raw.sample_num == 128
        # The text mock deliberately returns only eight pairs for a requested
        # 16-pair chunk.  The first batch detects that real controller limit and
        # the second batch adapts; DGTBB returns the full requested 40 pairs.
        assert len(calls) == (1 if fast else 2)
        assert len(calls[0]) == (4 if fast else 8)


def test_binary_trace_roundtrip_decodes_interleaved_pairs() -> None:
    with _make_controller() as ctrl:
        ch1, ch2 = ctrl.get_trace_buffer_binary(0, 4)
        assert ch1 == pytest.approx([0.0, 1.0, 2.0, 3.0])
        assert ch2 == pytest.approx([8.0, 9.0, 10.0, 11.0])


def test_engine_average_callback() -> None:
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            undersamples=1,
            no_samples=32,
            average_number=2,
        )
        completed: list[int] = []
        engine = MeasurementEngine(
            ctrl, trace, sample_frequency=1000.0,
            on_average_complete=lambda avg, _c1, _c2: completed.append(avg),
        )
        engine.run()
        assert completed == [0, 1]


def test_engine_stop_raises() -> None:
    with _make_controller() as ctrl:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            undersamples=1,
            no_samples=32,
            average_number=5,
        )
        engine = MeasurementEngine(ctrl, trace, sample_frequency=1000.0)
        engine.stop()
        with pytest.raises(MeasurementCancelled):
            engine.run()


def test_sample_frequency_read() -> None:
    with _make_controller() as ctrl:
        fs = ctrl.get_sample_frequency()
        assert fs > 0
