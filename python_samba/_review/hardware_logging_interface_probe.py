"""UI-to-controller verification for every Logging workspace interface.

The probe exercises the real LoggingPage controls against a controller, reads
back every writable value, and restores monitor/event/trace configuration in a
``finally`` block.  Event tracing is started only when the controller reports
zero saved traces.  A disabled DGETP sentinel is never replaced because the
firmware rejects that sentinel when written back through DSETP.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6 import QtWidgets  # noqa: E402

from python_samba.logging_tools import load_logging_record  # noqa: E402
from python_samba.services.safety import SafetyGate  # noqa: E402
from python_samba.services.session import open_serial  # noqa: E402
from python_samba.ui.main_window import MainWindow  # noqa: E402


LOGGING_TYPES = (
    ("OverCurrent Event", 0),
    ("Event Signal Event", 1),
    ("Standard", 2),
)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right)
        )
    try:
        return math.isclose(
            float(left), float(right), rel_tol=1e-6, abs_tol=1e-8
        )
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _normalized_signal(values: list[Any] | tuple[Any, ...]) -> tuple[int, int, int]:
    result = [_integer(value) for value in list(values)[:3]]
    result.extend([0] * (3 - len(result)))
    return tuple(result[:3])


def _valid_trace_params(params: list[Any]) -> bool:
    if len(params) != 6:
        return False
    values = [_integer(value, -1) for value in params]
    return (
        0 <= values[0] <= 2
        and 1 <= values[1] <= 0x20000
        and 1 <= values[2] <= 40
        and 1 <= values[3] <= 0xFFFF
        and values[4] >= 0
        and values[5] in (0, 1)
    )


def _wait_page(page, app: QtWidgets.QApplication, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    idle_since: float | None = None
    while time.monotonic() < deadline:
        app.processEvents()
        active = page.serial_worker_active
        if active:
            idle_since = None
        elif idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= 0.15:
            app.processEvents()
            if page.page_status.text() == "Error":
                raise RuntimeError("LoggingPage reported an asynchronous error")
            return
        time.sleep(0.01)
    raise TimeoutError("LoggingPage did not become idle")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "_review" / "hardware_probe_results",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir.resolve() / f"logging_interface_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "logging_interface_report.json"
    stream_path = output_dir / "ui_file_logging_40ch.csv"
    report: dict[str, Any] = {
        "timestamp": stamp,
        "port": args.port,
        "baudrate": args.baudrate,
        "checks": [],
        "writes_restored": False,
    }
    checks: list[dict[str, Any]] = report["checks"]

    def record(name: str, command: str, action: Callable[[], Any]) -> Any:
        try:
            detail = action()
        except Exception as exc:
            checks.append(
                {
                    "name": name,
                    "command": command,
                    "status": "FAIL",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        checks.append(
            {"name": name, "command": command, "status": "PASS", "detail": detail}
        )
        print(f"PASS {command:17} {name}", flush=True)
        return detail

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Never leave a modal dialog waiting in an unattended hardware probe.
    QtWidgets.QMessageBox.critical = staticmethod(
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.Ok
    )
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.Cancel
    )

    session = open_serial(
        args.port, args.baudrate, readonly=False, timeout=3.0
    )
    window: MainWindow | None = None
    initial: dict[str, Any] = {}
    monitor_restore_needed = False
    event_restore_needed = False
    trace_restore_needed = False
    analysis_params_restore: list[str] | None = None
    analysis_input_restore: list[str] | None = None

    try:
        report["firmware"] = str(session.open())
        initial = {
            "trace_params": session.get_event_trace_params(),
            "trace_info": session.get_event_trace_info(),
            "event": session.get_event_signal(),
            "monitor": [session.get_monitor_signal(index) for index in range(40)],
        }
        report["initial"] = initial

        window = MainWindow()
        window.session = session
        window.gate = SafetyGate(session, output_dir / "snapshots")
        page = window.logging_page_widget
        page.on_connected()

        def check_visual_contract() -> dict[str, Any]:
            values = tuple(
                (
                    page.logging_type_combo.itemText(index),
                    page.logging_type_combo.itemData(index),
                )
                for index in range(page.logging_type_combo.count())
            )
            if values != LOGGING_TYPES:
                raise AssertionError(f"logging type mapping is {values!r}")
            if not all(
                widget.isHidden()
                for widget in (
                    page.monitor_number,
                    page.monitor_selector,
                    page.btn_monitor_write,
                )
            ):
                raise AssertionError("redundant Selected Signal editor is visible")
            return {"logging_types": values, "selected_signal_removed": True}

        record("Logging UI protocol contract", "UI", check_visual_contract)

        def update_all() -> dict[str, Any]:
            page.monitor_used.setValue(40)
            page.file_signal_count.setValue(40)
            page.update_workspace()
            _wait_page(page, app)
            definitions = [
                _normalized_signal(values) for values in initial["monitor"]
            ]
            if page.monitor_definitions != definitions:
                raise AssertionError("40 monitor definitions do not match DGMOS")
            live_cells = [
                page.monitor_table.item(row, column).text()
                for row in range(20)
                for column in (2, 5)
            ]
            if len(live_cells) != 40 or any(value == "—" for value in live_cells):
                raise AssertionError("not all 40 DGMSV values reached the table")
            return {
                "definitions": len(definitions),
                "live_values": len(live_cells),
                "frequency": page.trace_status_labels["frequency"].text(),
            }

        record(
            "Update reads every visible logging field",
            "DGMOS/DGMSV/DGETP/DGETI/DGETS",
            update_all,
        )

        def direct_monitor_write() -> dict[str, Any]:
            nonlocal monitor_restore_needed
            channel = 39
            original = _normalized_signal(initial["monitor"][channel])
            candidates = [
                _normalized_signal(values) for values in initial["monitor"]
            ]
            candidate = next(
                (value for value in candidates if value != original), None
            )
            if candidate is None:
                # A freshly initialized controller commonly maps all forty
                # channels to the same signal.  X1FB/X2FB are stable legacy
                # monitor inputs and let the live probe verify an actual value
                # transition before restoring the original definition.
                candidate = next(
                    value
                    for value in ((0, 0, 0), (0, 1, 0))
                    if value != original
                )
            page._select_monitor_channel(channel)
            monitor_restore_needed = True
            page._monitor_selector_changed(candidate)
            _wait_page(page, app)
            readback = _normalized_signal(session.get_monitor_signal(channel))
            if readback != candidate:
                raise AssertionError(f"DSMOS readback {readback} != {candidate}")
            session.set_monitor_signal(channel, *original)
            monitor_restore_needed = False
            restored = _normalized_signal(session.get_monitor_signal(channel))
            if restored != original:
                raise AssertionError(f"monitor restore failed: {restored} != {original}")
            return {
                "channel": channel,
                "written": candidate,
                "readback": readback,
                "restored": restored,
            }

        record(
            "Signal button writes immediately and reads back",
            "DSMOS/DGMOS",
            direct_monitor_write,
        )

        def event_write() -> dict[str, Any]:
            nonlocal event_restore_needed
            original = list(initial["event"])
            if len(original) < 5:
                raise AssertionError(f"DGETS returned {original!r}")
            candidate = list(original)
            trigger = max(0, _integer(original[4]))
            candidate[4] = str(trigger + 1 if trigger < 0x7FFFFFFF else trigger - 1)
            page.event_selector.set_io_signal(_normalized_signal(candidate[:3]))
            page.event_threshold.setValue(float(candidate[3]))
            page.event_trigger_samples.setValue(_integer(candidate[4]))
            event_restore_needed = True
            page._write_event_signal()
            _wait_page(page, app)
            readback = session.get_event_signal()
            if not _equivalent(readback, candidate):
                raise AssertionError(f"DSETS readback {readback} != {candidate}")
            session.set_event_signal(*original)
            event_restore_needed = False
            restored = session.get_event_signal()
            if not _equivalent(restored, original):
                raise AssertionError(f"event restore failed: {restored} != {original}")
            return {"written": candidate, "readback": readback, "restored": restored}

        record("Event Setting writes and reads back", "DSETS/DGETS", event_write)

        if _valid_trace_params(initial["trace_params"]):

            def cycle_logging_types() -> list[dict[str, Any]]:
                nonlocal trace_restore_needed
                original = list(initial["trace_params"])
                cycles: list[dict[str, Any]] = []
                trace_restore_needed = True
                for label, mode in LOGGING_TYPES:
                    page.logging_type_combo.setCurrentIndex(
                        page.logging_type_combo.findData(mode)
                    )
                    page.internal_samples.setValue(_integer(original[1]))
                    page.internal_signal_count.setValue(_integer(original[2]))
                    page.internal_undersample.setValue(_integer(original[3]))
                    page.internal_delay_samples.setValue(max(1, _integer(original[4])))
                    page.internal_average.setChecked(bool(_integer(original[5])))
                    expected = [
                        str(mode),
                        str(page.internal_samples.value()),
                        str(page.internal_signal_count.value()),
                        str(page.internal_undersample.value()),
                        str(page.internal_delay_samples.value()),
                        str(int(page.internal_average.isChecked())),
                    ]
                    page.apply_internal()
                    _wait_page(page, app)
                    readback = session.get_event_trace_params()
                    if not _equivalent(readback, expected):
                        raise AssertionError(
                            f"{label} returned {readback}, expected {expected}"
                        )
                    cycles.append({"label": label, "value": mode, "readback": readback})
                session.set_event_trace_params(*original)
                trace_restore_needed = False
                restored = session.get_event_trace_params()
                if not _equivalent(restored, original):
                    raise AssertionError(
                        f"trace-parameter restore failed: {restored} != {original}"
                    )
                return cycles

            record(
                "All three Logging Type values write/read/restore",
                "DSETP/DGETP",
                cycle_logging_types,
            )
        else:
            checks.append(
                {
                    "name": "All three Logging Type values write/read/restore",
                    "command": "DSETP/DGETP",
                    "status": "SKIP_CONTROLLER_DISABLED_STATE",
                    "detail": (
                        "DGETP returned the firmware disabled sentinel; its zero "
                        "MaxBuffLen/MonSigNum cannot be restored through DSETP"
                    ),
                    "readback": initial["trace_params"],
                }
            )
            print("SKIP DSETP/DGETP       controller disabled sentinel", flush=True)

        def live_values_and_rate() -> dict[str, Any]:
            started = time.perf_counter()
            values = session.get_monitor_values(0, 39)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if len(values) != 40:
                raise AssertionError(f"DGMSV returned {len(values)} values")
            page.check_file_rate()
            _wait_page(page, app)
            if "recommended interval" not in page.file_rate_result.text():
                raise AssertionError(page.file_rate_result.text())
            return {"channels": len(values), "single_read_ms": elapsed_ms}

        record("Live values and rate check", "DGMSV", live_values_and_rate)

        def ui_file_logging() -> dict[str, Any]:
            page.file_output.setText(str(stream_path))
            page.file_signal_count.setValue(40)
            page.file_interval.setValue(50)
            page.file_start_after.setValue(0)
            page.file_continuous.setChecked(False)
            page.file_duration.setValue(0.0001)
            page._definitions_loaded = True
            page.start_file_logging()
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                app.processEvents()
                service = page.file_service
                if service is not None and not service.running:
                    app.processEvents()
                    break
                time.sleep(0.02)
            else:
                page.stop_file_logging()
                raise TimeoutError("UI file logging did not finish")
            record_data = load_logging_record(stream_path)
            if not record_data.rows:
                raise AssertionError("UI file logging wrote no rows")
            if any(len(row) != 42 for row in record_data.rows):
                raise AssertionError("file rows are not timestamp + elapsed + 40 signals")
            return {
                "samples": len(record_data.rows),
                "columns": len(record_data.rows[0]),
                "state": page.file_state.text(),
            }

        record("Start File Log streams all 40 channels", "DGMSV/CSV", ui_file_logging)

        saved = _integer(initial["trace_info"][2]) if len(initial["trace_info"]) > 2 else 0
        if saved == 0:

            def start_stop_trace() -> dict[str, Any]:
                page.start_internal()
                _wait_page(page, app)
                page.stop_internal()
                _wait_page(page, app)
                info = session.get_event_trace_info()
                if _integer(info[0]) != 0:
                    raise AssertionError(f"DSSET stop did not stop trace: {info}")
                return {"before": initial["trace_info"], "after": info}

            record("Start/stop internal logging", "DGETI/DSSET", start_stop_trace)
        else:
            checks.append(
                {
                    "name": "Start/stop internal logging",
                    "command": "DGETI/DSSET",
                    "status": "SKIP_SAVED_TRACES",
                    "detail": f"controller has {saved} saved trace(s)",
                }
            )

        if saved > 0:

            def read_saved_trace() -> dict[str, Any]:
                event_time = session.get_event_time(0)
                rows = session.download_logged_trace(0, max_samples=20)
                return {"event_time": event_time, "rows_read": len(rows)}

            record("Read a saved internal trace", "DGEVT/DGLDV", read_saved_trace)
        else:
            checks.append(
                {
                    "name": "Read a saved internal trace",
                    "command": "DGEVT/DGLDV",
                    "status": "SKIP_NO_SAVED_TRACE",
                    "detail": "controller reports zero saved traces",
                }
            )

        try:
            analysis_params_restore = session.get_analysis_params()
            analysis_input_restore = session.get_analysis_input()
        except Exception as exc:
            checks.append(
                {
                    "name": "Analysis filter logging",
                    "command": "LGANP/LSANP/LGAIS/LGAFO/LGAEV",
                    "status": "SKIP_UNSUPPORTED_FIRMWARE",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            analysis_params_restore = None
            analysis_input_restore = None
        else:

            def analysis_read_same_write() -> dict[str, Any]:
                window.on_analysis_read()
                session.set_analysis_params(*analysis_params_restore)
                session.set_analysis_input(*analysis_input_restore)
                params = session.get_analysis_params()
                inputs = session.get_analysis_input()
                outputs = session.get_analysis_filter_outputs()
                events = session.get_analysis_events()
                if not _equivalent(params, analysis_params_restore):
                    raise AssertionError("analysis parameters changed")
                if not _equivalent(inputs, analysis_input_restore):
                    raise AssertionError("analysis input changed")
                return {
                    "params": params,
                    "input": inputs,
                    "outputs": len(outputs),
                    "events": len(events),
                }

            record(
                "Analysis filter logging read/same-write",
                "LGANP/LSANP/LGAIS/LSAIS/LGAFO/LGAEV",
                analysis_read_same_write,
            )

        final = {
            "trace_params": session.get_event_trace_params(),
            "trace_info": session.get_event_trace_info(),
            "event": session.get_event_signal(),
            "monitor": [session.get_monitor_signal(index) for index in range(40)],
        }
        report["final"] = final
        restorable_comparisons = {
            "trace_params": (initial["trace_params"], final["trace_params"]),
            "event": (initial["event"], final["event"]),
            "monitor": (initial["monitor"], final["monitor"]),
            "trace_status_and_saved": (
                [initial["trace_info"][0], initial["trace_info"][2]],
                [final["trace_info"][0], final["trace_info"][2]],
            ),
        }
        changed = [
            name
            for name, (before, after) in restorable_comparisons.items()
            if not _equivalent(before, after)
        ]
        report["restorable_changed"] = changed
        if changed:
            raise AssertionError(f"logging state was not restored: {changed}")
        report["writes_restored"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if session.connected and initial:
            try:
                session.start_stop_event_tracing(0)
                if trace_restore_needed:
                    session.set_event_trace_params(*initial["trace_params"])
                if event_restore_needed:
                    session.set_event_signal(*initial["event"])
                if monitor_restore_needed:
                    session.set_monitor_signal(39, *initial["monitor"][39])
                if analysis_params_restore is not None:
                    session.set_analysis_params(*analysis_params_restore)
                if analysis_input_restore is not None:
                    session.set_analysis_input(*analysis_input_restore)
            except Exception as restore_exc:
                report["restore_error"] = (
                    f"{type(restore_exc).__name__}: {restore_exc}"
                )
        if window is not None:
            window.close()
            app.processEvents()
        elif session.connected:
            session.close()

    failed = [check for check in checks if check["status"] == "FAIL"]
    report["status"] = (
        "PASS" if not failed and "error" not in report and "restore_error" not in report
        else "FAIL"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report_path, flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
