"""Pure numerical tests for the completed-record analysis layer."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

from python_samba.logging_tools.models import LoggingRecord
from python_samba.logging_tools.record_analysis import RecordAnalysisSession


def _sine_record(
    *, sample_rate: float = 1000.0, samples: int = 4096, frequency: float = 50.0
) -> LoggingRecord:
    time = np.arange(samples, dtype=np.float64) / sample_rate
    values = np.sin(2.0 * np.pi * frequency * time)
    return LoggingRecord(
        ["elapsed_s", "Sine"],
        [[float(x), float(y)] for x, y in zip(time, values, strict=True)],
        source="sine.csv",
        metadata={"sample_interval_s": 1.0 / sample_rate},
    )


def test_record_normalization_prefers_elapsed_and_keeps_ragged_numeric_columns():
    record = LoggingRecord(
        ["timestamp_utc", "time", "elapsed_s", "A", "B", "Comment"],
        [
            ["2026-01-01T00:00:00Z", 100.0, 0.0, "1", 10, "ready"],
            ["2026-01-01T00:00:01Z", 200.0, 0.1, 2],
            ["2026-01-01T00:00:02Z", 300.0, 0.2, "bad", 30, "done"],
        ],
    )
    session = RecordAnalysisSession.from_record(record)

    assert [curve.name for curve in session.curves] == ["A", "B"]
    assert session.sampling.x_label == "Time (s)"
    assert session.curves[0].x.tolist() == pytest.approx([0.0, 0.1, 0.2])
    assert session.curves[0].y[:2].tolist() == pytest.approx([1.0, 2.0])
    assert np.isnan(session.curves[0].y[2])
    assert np.isnan(session.curves[1].y[1])
    assert not session.curves[0].x.flags.writeable
    assert not session.curves[0].y.flags.writeable


def test_sampling_rate_metadata_is_checked_against_time_axis():
    regular = RecordAnalysisSession(_sine_record(sample_rate=500.0, samples=100))
    assert regular.sampling.regular
    assert regular.sampling.sample_rate_hz == pytest.approx(500.0)
    assert regular.sampling.source == "metadata:sample_interval_s"

    irregular_record = LoggingRecord(
        ["elapsed_s", "A"],
        [[0.0, 0.0], [0.01, 1.0], [0.0208, 2.0], [0.03, 3.0]],
        metadata={"sample_interval_s": 0.01},
    )
    irregular = RecordAnalysisSession(irregular_record)
    assert not irregular.sampling.regular
    assert irregular.sampling.jitter_ratio > 0.01
    assert irregular.can_process(irregular.curves[0].curve_id)[0] is False

    irregular.set_sample_rate(100.0)
    resampled = irregular.resample_curve(irregular.curves[0].curve_id)
    assert resampled.operation["type"] == "resample"
    assert irregular.can_process(resampled.curve_id) == (True, "")
    assert np.diff(resampled.x) == pytest.approx(np.full(len(resampled.x) - 1, 0.01))


def test_sample_index_requires_rate_but_becomes_processable_after_user_entry():
    session = RecordAnalysisSession(
        LoggingRecord(["A"], [[0.0], [1.0], [0.0], [-1.0], [0.0]])
    )
    curve = session.curves[0]
    assert session.sampling.uses_sample_index
    assert session.can_process(curve.curve_id)[0] is False
    session.set_sample_rate(100.0)
    assert session.can_process(curve.curve_id) == (True, "")


def test_sample_index_derivative_resamples_in_seconds_and_limits_allocation():
    session = RecordAnalysisSession(
        LoggingRecord(["A"], [[0.0], [1.0], [0.0], [-1.0], [0.0]])
    )
    session.set_sample_rate(10.0)
    detrended = session.detrend_curve(session.curves[0].curve_id)
    resampled = session.resample_curve(detrended.curve_id)
    assert resampled.x_unit == "seconds"
    assert resampled.x.tolist() == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])

    huge = RecordAnalysisSession(
        LoggingRecord(["elapsed_s", "A"], [[0.0, 0.0], [1000.0, 1.0]])
    )
    with pytest.raises(ValueError, match="10,000,000"):
        huge.resample_curve(huge.curves[0].curve_id, 1_000_000.0)


def test_single_missing_interval_marks_long_time_axis_irregular():
    values = np.arange(100, dtype=np.float64) * 0.01
    values[50:] += 0.01
    session = RecordAnalysisSession(
        LoggingRecord(
            ["elapsed_s", "A"],
            [[float(x), float(index)] for index, x in enumerate(values)],
        )
    )
    assert not session.sampling.regular
    assert session.sampling.jitter_ratio > 0.01


def test_time_processing_creates_immutable_chained_derivatives():
    sample_rate = 1000.0
    count = 3000
    time = np.arange(count) / sample_rate
    values = 2.5 + 0.2 * time + np.sin(2 * np.pi * 20 * time) + 0.4 * np.sin(
        2 * np.pi * 220 * time
    )
    session = RecordAnalysisSession(
        LoggingRecord(
            ["elapsed_s", "Mixed"],
            [[float(x), float(y)] for x, y in zip(time, values, strict=True)],
            metadata={"sample_rate_hz": sample_rate},
        )
    )
    original = session.curves[0]
    detrended = session.detrend_curve(original.curve_id, "linear")
    smoothed = session.smooth_curve(detrended.curve_id, 5)
    filtered = session.filter_curve(
        smoothed.curve_id, "lowpass", high_hz=80.0, order=4
    )

    assert detrended.parent_id == original.curve_id
    assert smoothed.parent_id == detrended.curve_id
    assert filtered.parent_id == smoothed.curve_id
    assert len(filtered.y) == count
    assert abs(float(np.mean(detrended.y))) < 1e-10
    assert np.all(np.isfinite(filtered.y))
    assert original.y[0] == pytest.approx(values[0])
    assert not filtered.y.flags.writeable


def test_computed_result_can_replace_original_curve_without_adding_a_row():
    session = RecordAnalysisSession(_sine_record(samples=512))
    original = session.curves[0]
    original_values = original.y.copy()

    temporary = session.detrend_curve(original.curve_id, "constant")
    updated = session.replace_curve_data(original.curve_id, temporary.curve_id)

    assert len(session.curves) == 1
    assert updated.curve_id == original.curve_id
    assert updated.name == original.name
    assert not updated.derived
    assert updated.parent_id is None
    assert updated.operation["type"] == "detrend"
    assert updated.operation["processing_chain"][-1]["type"] == "detrend"
    assert not np.array_equal(updated.y, original_values)

    spectrum = session.fft_curve(updated.curve_id)
    updated = session.replace_curve_data(updated.curve_id, spectrum.curve_id)
    assert len(session.curves) == 1
    assert updated.curve_id == original.curve_id
    assert updated.domain == "frequency"
    assert [step["type"] for step in updated.operation["processing_chain"]] == [
        "detrend",
        "fft",
    ]


def test_fft_and_welch_psd_find_the_tone_and_support_db_display():
    session = RecordAnalysisSession(_sine_record(frequency=73.0))
    source = session.curves[0]
    fft = session.fft_curve(source.curve_id)
    psd = session.psd_curve(source.curve_id, 1024)

    assert fft.domain == "frequency"
    assert psd.domain == "frequency"
    assert fft.x[int(np.argmax(fft.y))] == pytest.approx(73.0, abs=0.3)
    assert psd.x[int(np.argmax(psd.y))] == pytest.approx(73.0, abs=1.0)
    assert np.all(np.isfinite(session.displayed_y(fft, decibels=True)))
    assert np.all(np.isfinite(session.displayed_y(psd, decibels=True)))


def test_derived_names_are_unique_and_parent_delete_cascades():
    session = RecordAnalysisSession(_sine_record(samples=256))
    source = session.curves[0]
    first = session.detrend_curve(source.curve_id)
    second = session.detrend_curve(source.curve_id)
    child = session.smooth_curve(first.curve_id, 5)
    assert first.name != second.name
    renamed = session.rename_curve(second.curve_id, first.name)
    assert renamed.name != first.name

    session.delete_curve(first.curve_id)
    remaining = {curve.curve_id for curve in session.curves}
    assert first.curve_id not in remaining
    assert child.curve_id not in remaining
    with pytest.raises(ValueError, match="original"):
        session.delete_curve(source.curve_id)


def test_export_writes_paired_columns_and_processing_metadata(tmp_path: Path):
    session = RecordAnalysisSession(_sine_record(samples=512))
    source = session.curves[0]
    fft = session.fft_curve(source.curve_id)
    output = session.export_curves(
        tmp_path / "analysis.csv",
        [source.curve_id, fft.curve_id],
        frequency_decibels=True,
    )

    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["Sine_x", "Sine_y", "Sine [FFT]_x", "Sine [FFT] (dB)_y"]
    assert len(rows) == 513

    metadata = json.loads(
        output.with_suffix(".csv.meta.json").read_text(encoding="utf-8")
    )
    assert metadata["schema"] == "python-samba-record-analysis/v1"
    assert metadata["frequency_decibels"] is True
    assert metadata["sampling"]["x_label"] == "Time (s)"
    assert metadata["curves"][1]["operation"]["type"] == "fft"
