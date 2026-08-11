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
        assert page.workspace_splitter.count() == 3
        assert page.monitor_table.rowCount() == 20
        assert len(page.monitor_signal_buttons) == 40
        assert page.monitor_signal_buttons[0].text()
        assert page.monitor_signal_buttons[20].text()
        assert page.monitor_table.item(0, 0).text() == "1"
        assert page.monitor_table.item(0, 3).text() == "21"
        assert page.monitor_table.columnWidth(0) == 34
        assert page.monitor_table.columnWidth(3) == 34
        assert page.monitor_table.columnWidth(1) > page.monitor_table.columnWidth(0)
        assert page.monitor_table.columnWidth(4) > page.monitor_table.columnWidth(3)
        assert page.records_window.isHidden()
        page.btn_show_records.click()
        app.processEvents()
        assert not page.records_window.isHidden()
        assert page.records_window.isWindow()
        assert page.record_plot.window() is page.records_window
        page.records_window.hide()
        assert page.auxiliary_panel.isHidden()
        page._show_auxiliary(0)
        assert not page.auxiliary_panel.isHidden()
        assert page.auxiliary_tabs.currentIndex() == 0
        assert page.auxiliary_tabs.count() == 1
        assert page.auxiliary_tabs.tabText(0) == "Analysis Filter"
        page._arrange_logging_toolbar(True)
        assert page._toolbar_compact
        page._arrange_logging_toolbar(False)
        assert not page._toolbar_compact
        assert page.file_signal_count.maximum() == 40
        assert page.trace_progress.maximum() == 100
        assert [
            page.logging_type_combo.itemText(index)
            for index in range(page.logging_type_combo.count())
        ] == ["OverCurrent Event", "Event Signal Event", "Standard"]
        assert [
            page.logging_type_combo.itemData(index)
            for index in range(page.logging_type_combo.count())
        ] == [0, 1, 2]
        assert page.monitor_number.isHidden()
        assert page.monitor_selector.isHidden()
        assert page.btn_monitor_write.isHidden()
        page._show_records_window()
        page.shutdown()
        app.processEvents()
        assert page.records_window.isHidden()
    finally:
        page.records_window.close()
        window.close()
        app.processEvents()


def test_logging_monitor_refresh_does_not_reinsert_owned_table_items():
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets
    from python_samba.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    messages: list[str] = []

    def capture_message(_kind, _context, message):
        messages.append(message)

    previous_handler = QtCore.qInstallMessageHandler(capture_message)
    try:
        page = window.logging_page_widget
        page._set_monitor_row(0, (0, 0, 0), 1.0)
        page._set_monitor_row(0, (1, 2, 3), 2.0)
        page._set_monitor_row(20, (0, 3, 0), 3.0)
        assert page.monitor_signal_buttons[0].text()
        assert page.monitor_table.item(0, 2).text() == "2"
        assert page.monitor_table.item(0, 5).text() == "3"
        assert not any("already owned by another QTableWidget" in msg for msg in messages)
    finally:
        QtCore.qInstallMessageHandler(previous_handler)
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


def test_logging_page_can_cancel_while_monitor_definitions_are_preparing():
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
    original_read = page.read_monitor_definitions
    page.read_monitor_definitions = lambda: None
    try:
        page.start_file_logging()
        assert page._pending_file_start
        assert page.btn_file_stop.isEnabled()
        assert page.file_state.text() == "Preparing"
        page.stop_file_logging()
        assert not page._pending_file_start
        assert page.btn_file_start.isEnabled()
        assert not page.btn_file_stop.isEnabled()
        assert page.file_state.text() == "Cancelled"
    finally:
        page.read_monitor_definitions = original_read
        window.close()
        app.processEvents()


def test_logging_workspace_update_populates_all_three_columns():
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
    try:
        page.update_workspace()
        deadline = time.monotonic() + 3.0
        while page.serial_worker_active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert not page.serial_worker_active
        assert page._definitions_loaded
        assert page.monitor_table.item(0, 2).text() != "—"
        assert page.trace_status_labels["frequency"].text().endswith("Hz")
        assert page.file_state.text() == "Idle"
    finally:
        window.close()
        app.processEvents()


def test_logging_monitor_button_selection_writes_and_reads_back_directly():
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
    try:
        page._select_monitor_channel(7)
        page._monitor_selector_changed((0, 11, 0))
        deadline = time.monotonic() + 3.0
        while page.serial_worker_active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert session.get_monitor_signal(7)[:3] == ["0", "11", "0"]
        assert page.monitor_definitions[7] == (0, 11, 0)
        assert page.monitor_signal_buttons[7].text() == page.monitor_names[7]
    finally:
        window.close()
        app.processEvents()


def test_logging_internal_and_event_controls_follow_protocol_field_order():
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

    def wait_for_page() -> None:
        deadline = time.monotonic() + 3.0
        while page.serial_worker_active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert not page.serial_worker_active

    try:
        page.logging_type_combo.setCurrentIndex(
            page.logging_type_combo.findData(2)
        )
        page.internal_samples.setValue(2048)
        page.internal_signal_count.setValue(6)
        page.internal_undersample.setValue(4)
        page.internal_delay_samples.setValue(20)
        page.internal_average.setChecked(True)
        original_event = session.get_event_signal()
        page.apply_internal()
        wait_for_page()
        assert session.get_event_trace_params() == ["2", "2048", "6", "4", "20", "1"]
        assert session.get_event_signal() == original_event

        page.event_selector.set_io_signal((0, 9, 0))
        page.event_threshold.setValue(12.5)
        page.event_trigger_samples.setValue(17)
        page._write_event_signal()
        wait_for_page()
        event = session.get_event_signal()
        assert event[:3] == ["0", "9", "0"]
        assert float(event[3]) == pytest.approx(12.5)
        assert event[4] == "17"

        page._apply_internal_readback(
            (["2", "100", "4", "5", "10", "1"], ["0"] * 5, event, [], 1000)
        )
        assert page.trace_status_labels["trace_time"].text() == "0.5"
        assert page.trace_status_labels["delay_time"].text() == "0.01"
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


def test_hardware_logging_interface_probe_restores_mock_state(tmp_path: Path, monkeypatch):
    import sys
    from _review import hardware_logging_interface_probe as probe

    monkeypatch.setattr(
        probe, "open_serial", lambda *_args, **_kwargs: open_mock(readonly=False)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hardware_logging_interface_probe.py",
            "--output-dir", str(tmp_path),
        ],
    )
    assert probe.main() == 0
    report_path = next(
        tmp_path.glob("logging_interface_*/logging_interface_report.json")
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["writes_restored"] is True
    assert report["restorable_changed"] == []
    assert all(check["status"] != "FAIL" for check in report["checks"])
