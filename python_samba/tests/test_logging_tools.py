"""Logging acquisition, streamed files, and legacy record compatibility."""

from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

from python_samba.logging_tools import (
    FileLoggingConfig,
    FileLoggingService,
    load_logging_record,
    save_trace_record,
)
from python_samba.services.session import open_mock


def test_file_logging_streams_rows_and_final_metadata(tmp_path: Path):
    with open_mock(readonly=False) as session:
        output = tmp_path / "live.csv"
        service = FileLoggingService(session)
        service.start(
            FileLoggingConfig(
                output,
                signal_count=12,
                interval_ms=10,
                start_after_s=0,
                duration_s=0.06,
                signal_names=tuple(f"S{index}" for index in range(12)),
            )
        )
        deadline = time.monotonic() + 2.0
        while service.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service.running

    record = load_logging_record(output)
    assert len(record.rows) >= 2
    assert record.headers[:3] == ["timestamp_utc", "elapsed_s", "S0"]
    assert len(record.rows[0]) == 14
    metadata = json.loads((tmp_path / "live.csv.meta.json").read_text(encoding="utf-8"))
    assert metadata["state"] == "complete"
    assert metadata["samples"] == len(record.rows)


def test_stream_logging_can_be_cancelled_without_losing_rows(tmp_path: Path):
    with open_mock() as session:
        output = tmp_path / "cancelled.tsv"
        service = FileLoggingService(session)
        service.start(
            FileLoggingConfig(
                output,
                signal_count=4,
                interval_ms=10,
                start_after_s=0,
                duration_s=None,
                delimiter="\t",
            )
        )
        deadline = time.monotonic() + 1.0
        while service.stats.samples < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop(wait=True, timeout=1.0)
        assert not service.running
    metadata = json.loads((tmp_path / "cancelled.tsv.meta.json").read_text(encoding="utf-8"))
    assert metadata["state"] == "cancelled"
    assert metadata["samples"] >= 1


def test_internal_trace_csv_and_legacy_json_are_normalized(tmp_path: Path):
    trace_path = save_trace_record(
        tmp_path / "trace.csv",
        [[1.0, 2.0], [3.0, 4.0]],
        ["A", "B"],
        sample_interval_s=0.002,
    )
    trace = load_logging_record(trace_path)
    assert trace.headers == ["elapsed_s", "A", "B"]
    assert trace.rows[1] == pytest.approx([0.002, 3.0, 4.0])

    legacy_path = tmp_path / "legacy.ILogRecJson"
    legacy_path.write_text(
        json.dumps(
            {
                "Param": {
                    "UnderSample": {"Value": 2},
                    "SampleFrequency": {"Value": 1000},
                },
                "SigName": ["X", "Y"],
                "Data": [[10.0, 20.0], [11.0, 21.0]],
            }
        ),
        encoding="utf-8",
    )
    legacy = load_logging_record(legacy_path)
    assert legacy.headers == ["elapsed_s", "X", "Y"]
    assert legacy.rows[1] == pytest.approx([0.002, 11.0, 21.0])


def test_trace_download_uses_actual_logged_sample_count():
    with open_mock() as session:
        session.transport.state.event_trace_params[1] = "1024"
        session.transport.state.event_trace_info[4] = "8"
        progress: list[tuple[int, int]] = []
        rows = session.download_logged_trace(
            0, progress_callback=lambda current, total: progress.append((current, total))
        )
        assert len(rows) == 8
        assert progress[-1] == (8, 8)


def test_logging_page_is_primary_and_exposes_40_channels():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        labels = [button.text() for button in window.nav_buttons]
        assert "Logging" in labels
        page = window.logging_page_widget
        assert page.monitor_table.rowCount() == 40
        assert page.file_signal_count.maximum() == 40
        assert page.trace_progress.maximum() == 100
    finally:
        window.close()
        app.processEvents()


def test_logging_page_starts_and_stops_background_file_acquisition(tmp_path: Path):
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.services.safety import SafetyGate
    from python_samba.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    session = open_mock(readonly=False)
    session.open()
    window = MainWindow()
    window.session = session
    window.gate = SafetyGate(session)
    page = window.logging_page_widget
    page.on_connected()
    page.file_output.setText(str(tmp_path / "page.csv"))
    page.file_signal_count.setValue(40)
    page.file_interval.setValue(10)
    page.file_start_after.setValue(0)
    page.file_continuous.setChecked(True)
    try:
        page.start_file_logging()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            app.processEvents()
            if page.file_service and page.file_service.stats.samples >= 3:
                break
            time.sleep(0.01)
        assert page.file_service is not None
        assert page.file_service.stats.samples >= 3
        page.stop_file_logging()
        while page.file_service.running and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        record = load_logging_record(tmp_path / "page.csv")
        assert len(record.rows) >= 3
        assert len(record.rows[0]) == 42
        assert page.btn_file_start.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_hardware_logging_probe_is_read_only_on_mock(tmp_path: Path, monkeypatch):
    import sys
    from _review import hardware_logging_probe as probe

    monkeypatch.setattr(
        probe, "open_serial", lambda *_args, **_kwargs: open_mock(readonly=True)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hardware_logging_probe.py",
            "--duration-s", "0.05",
            "--interval-ms", "10",
            "--output-dir", str(tmp_path),
        ],
    )
    assert probe.main() == 0
    report_path = next(tmp_path.glob("logging_*/logging_hardware_report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["controller_writes"] == 0
    assert all(check["status"] != "FAIL" for check in report["checks"])
