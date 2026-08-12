"""Exercise the actual Real-time Curve Qt window through a remote CommServer.

The probe deliberately avoids DSETP/DSSET.  It snapshots controller logging
state and all forty DGMOS slots, runs the same asynchronous start/stop path as
the GUI for three and forty selected signals, then requires an exact restore.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from python_samba.logging_tools.record_analysis import RecordAnalysisSession
from python_samba.logging_tools.storage import load_logging_record
from python_samba.services.monitor_lease import MonitorSlotLease as BaseMonitorSlotLease
from python_samba.services.session import open_comm_server
from python_samba.ui import live_curve_window as live_curve_module
from python_samba.ui.live_curve_window import LiveCurveWindow


def _process_events(app: QtWidgets.QApplication, milliseconds: int = 20) -> None:
    app.processEvents(QtCore.QEventLoop.AllEvents, milliseconds)


def _wait_until(app, predicate, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _process_events(app)
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError(f"timed out waiting for {description}")


def _controller_snapshot(session) -> dict[str, object]:
    info = list(session.get_event_trace_info())
    return {
        "monitor_definitions": [
            list(definition) for definition in session.get_monitor_signals(40)
        ],
        "event_params": list(session.get_event_trace_params()),
        "event_info": info,
        "saved_trace_num": int(str(info[2]), 0) if len(info) > 2 else 0,
    }


def _select_first(window: LiveCurveWindow, count: int) -> None:
    window._populating_tree = True
    try:
        for item in window._spec_items.values():
            item.setCheckState(0, QtCore.Qt.Unchecked)
        for item in list(window._spec_items.values())[:count]:
            item.setCheckState(0, QtCore.Qt.Checked)
        for parent in window._parent_items.values():
            window._sync_parent_state(parent)
    finally:
        window._populating_tree = False
    window._selected_specs = list(window._catalog[:count])
    window._visible_keys = {spec.key for spec in window._selected_specs[:6]}
    window.selection_count.setText(f"{count} / 40 selected")
    window._rebuild_selected_table()


def _verify_record(path: Path) -> dict[str, object]:
    record = load_logging_record(path)
    analysis = RecordAnalysisSession.from_record(record)
    time_curves = analysis.curves_for_domain("time")
    if not time_curves:
        raise RuntimeError("saved GUI record contains no numeric curves")
    curve_id = time_curves[0].curve_id
    allowed, reason = analysis.can_process(curve_id)
    result: dict[str, object] = {
        "rows": len(record.rows),
        "headers": list(record.headers),
        "numeric_curves": len(time_curves),
        "processable": allowed,
        "processing_reason": reason,
    }
    if not allowed and analysis.sampling.sample_rate_hz:
        derived = analysis.resample_curve(
            curve_id, float(analysis.sampling.sample_rate_hz)
        )
        curve_id = derived.curve_id
        allowed, reason = analysis.can_process(curve_id)
        result.update(
            {
                "resampled": True,
                "resampled_samples": len(derived.y),
                "processable_after_resample": allowed,
                "processing_reason_after_resample": reason,
            }
        )
    curve = analysis.get_curve(curve_id)
    if allowed and len(curve.y) >= 8:
        fft = analysis.fft_curve(curve_id)
        psd = analysis.psd_curve(curve_id, block_size=min(64, len(curve.y)))
        result.update({"fft_points": len(fft.x), "psd_points": len(psd.x)})
    return result


def _exercise_window(
    app: QtWidgets.QApplication,
    window: LiveCurveWindow,
    session,
    *,
    count: int,
    samples: int,
    output: Path,
) -> dict[str, object]:
    before = _controller_snapshot(session)
    _select_first(window, count)
    if len(window._selected_specs) != count:
        raise RuntimeError(f"GUI selected {len(window._selected_specs)}/{count} signals")
    window.interval_ms.setValue(100)
    window._start_clicked()
    _wait_until(
        app,
        lambda: window.running or window.state_value.text() == "Error",
        35.0,
        f"{count}-signal GUI acquisition start",
    )
    if not window.running:
        raise RuntimeError(window.message_label.text())
    _wait_until(
        app,
        lambda: bool(window._buffer and window._buffer.sample_count >= samples),
        max(40.0, samples * 2.0),
        f"{samples} GUI samples from {count} signals",
    )
    window._refresh_plot()
    identities = {key: id(item) for key, item in window._curve_items.items()}
    _process_events(app)
    window._refresh_plot()
    if identities != {key: id(item) for key, item in window._curve_items.items()}:
        raise RuntimeError("PlotDataItem identity changed during live refresh")
    if window.selected_table.item(0, 3).text() == "—":
        raise RuntimeError("GUI live-value table did not refresh")
    if not window.stop_and_restore(timeout=20.0):
        raise RuntimeError(window.message_label.text())
    _process_events(app)
    after = _controller_snapshot(session)
    differences = {
        key: {"before": before[key], "after": after[key]}
        for key in before
        if before[key] != after[key]
    }
    if differences:
        raise RuntimeError(f"controller state changed after GUI stop: {sorted(differences)}")
    if window._buffer is None:
        raise RuntimeError("GUI buffer disappeared after stop")
    stats = window._latest_stats
    saved = window._buffer.export_csv(
        output,
        colors=window._colors,
        controller=window._controller,
        requested_interval_ms=window.interval_ms.value(),
        actual_interval_ms=float(getattr(stats, "actual_interval_ms", 0.0)),
        late_samples=int(getattr(stats, "late_samples", 0)),
    )
    return {
        "signal_count": count,
        "sample_count": window._buffer.sample_count,
        "requested_interval_ms": window.interval_ms.value(),
        "actual_interval_ms": float(getattr(stats, "actual_interval_ms", 0.0)),
        "late_samples": int(getattr(stats, "late_samples", 0)),
        "live_table_value": window.selected_table.item(0, 3).text(),
        "plot_item_count": len(window._curve_items),
        "visible_plot_items": sum(item.isVisible() for item in window._curve_items.values()),
        "record": str(saved),
        "record_analysis": _verify_record(saved),
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recovery_dir = output_dir / "monitor_recovery"

    class ProbeMonitorSlotLease(BaseMonitorSlotLease):
        def __init__(self, session, **kwargs):
            super().__init__(session, recovery_directory=recovery_dir, **kwargs)

    live_curve_module.MonitorSlotLease = ProbeMonitorSlotLease
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    session = open_comm_server(
        args.port,
        baudrate=args.baudrate,
        server=args.server,
        auto_start=False,
        client_name="live-curve-gui-hardware-probe",
        readonly=False,
        timeout=8.0,
    )
    report: dict[str, object] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "server": args.server,
        "port": args.port,
        "baudrate": args.baudrate,
        "checks": {},
    }
    window = None
    before = None
    return_code = 1
    try:
        version = session.open()
        constants = session.get_global_system_constants()
        before = _controller_snapshot(session)
        window = LiveCurveWindow()
        window.set_connection(
            session,
            constants=constants,
            version=version,
            controller={"firmware": str(version), "probe": "LiveCurveWindow"},
        )
        window.show()
        _process_events(app)
        report["checks"] = {
            "connection": {
                "firmware": str(version),
                "firmware_info": version.full_text,
                "system_constants": list(constants),
                "catalog_size": len(window._catalog),
                "nonmodal": not window.isModal(),
            },
            "three_signals": _exercise_window(
                app,
                window,
                session,
                count=3,
                samples=args.samples,
                output=output_dir / "gui_real_time_curve_3.csv",
            ),
            "forty_signals": _exercise_window(
                app,
                window,
                session,
                count=40,
                samples=args.samples,
                output=output_dir / "gui_real_time_curve_40.csv",
            ),
        }
        final = _controller_snapshot(session)
        differences = {
            key: {"before": before[key], "after": final[key]}
            for key in before
            if before[key] != final[key]
        }
        report["checks"]["final_preservation"] = {
            "before": before,
            "after": final,
            "differences": differences,
        }
        if differences:
            raise RuntimeError(f"final controller state changed: {sorted(differences)}")
        report["ok"] = True
        return_code = 0
    except BaseException as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        if before is not None and session.connected:
            try:
                report["failure_snapshot"] = _controller_snapshot(session)
            except Exception as snapshot_error:
                report["failure_snapshot_error"] = str(snapshot_error)
    finally:
        if window is not None:
            try:
                window.stop_and_restore(timeout=20.0)
            except Exception as restore_error:
                report["final_restore_error"] = str(restore_error)
            window.hide()
        session.close()
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report_path = output_dir / "live_curve_gui_hardware_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
