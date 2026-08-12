"""Zero-side-effect Real-time Curve acceptance probe through CommServer."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from python_samba.logging_tools.live_curve import (
    LiveCurveAcquisitionService,
    LiveCurveConfig,
    LiveCurveSessionBuffer,
    MonitorCapabilities,
    build_monitor_signal_catalog,
)
from python_samba.logging_tools.storage import load_logging_record
from python_samba.logging_tools.record_analysis import RecordAnalysisSession
from python_samba.services.monitor_lease import MonitorSlotLease
from python_samba.services.session import open_comm_server


def _wait_samples(service, minimum: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while service.stats.samples < minimum and service.running and time.monotonic() < deadline:
        time.sleep(0.05)
    if service.stats.samples < minimum:
        raise TimeoutError(
            f"captured {service.stats.samples}/{minimum} samples; "
            f"state={service.stats.state} message={service.stats.message!r}"
        )


def _trace_snapshot(session) -> dict[str, object]:
    info = list(session.get_event_trace_info())
    return {
        "monitor_definitions": [list(values) for values in session.get_monitor_signals(40)],
        "event_params": list(session.get_event_trace_params()),
        "event_info": info,
        "saved_trace_num": int(str(info[2]), 0) if len(info) > 2 else 0,
    }


def _exercise(session, signals, interval_ms: int, samples: int, output: Path) -> dict[str, object]:
    buffer = LiveCurveSessionBuffer(signals)
    lease = MonitorSlotLease(
        session,
        recovery_directory=output.parent / "recovery",
        controller={"probe": "live_curve_hardware_probe"},
    )
    service = None
    restore_ok = False
    try:
        lease.acquire([signal.tokens for signal in signals])
        service = LiveCurveAcquisitionService(session, buffer)
        service.start(LiveCurveConfig(signals, interval_ms=interval_ms))
        _wait_samples(service, samples, timeout=max(30.0, samples * interval_ms / 1000.0 * 8.0))
        service.stop(wait=True, timeout=12.0)
        if service.running:
            raise TimeoutError("real-time acquisition worker did not stop")
    finally:
        if service is not None and service.running:
            service.stop(wait=True, timeout=12.0)
        restore_ok = lease.restore()
    if not restore_ok:
        raise RuntimeError(f"monitor restore failed: {lease.restore_error}")
    saved = buffer.export_csv(
        output,
        controller={"firmware": str(session.get_version())},
        requested_interval_ms=interval_ms,
        actual_interval_ms=service.stats.actual_interval_ms,
        late_samples=service.stats.late_samples,
    )
    record = load_logging_record(saved)
    if len(record.rows) != buffer.sample_count:
        raise RuntimeError(
            f"saved record roundtrip mismatch: {len(record.rows)} != {buffer.sample_count}"
        )
    analysis = RecordAnalysisSession.from_record(record)
    time_curves = analysis.curves_for_domain("time")
    processing: dict[str, object] = {"numeric_curves": len(time_curves)}
    if not time_curves:
        raise RuntimeError("saved record contains no numeric curves")
    curve = time_curves[0]
    allowed, reason = analysis.can_process(curve.curve_id)
    processing["processable"] = allowed
    processing["processing_reason"] = reason
    processed_curve_id = curve.curve_id
    if not allowed and analysis.sampling.sample_rate_hz:
        resampled = analysis.resample_curve(
            curve.curve_id, analysis.sampling.sample_rate_hz
        )
        processed_curve_id = resampled.curve_id
        allowed, reason = analysis.can_process(processed_curve_id)
        processing["resampled"] = True
        processing["resampled_samples"] = len(resampled.y)
        processing["processable_after_resample"] = allowed
        processing["processing_reason_after_resample"] = reason
    if allowed and len(analysis.get_curve(processed_curve_id).y) >= 8:
        fft = analysis.fft_curve(processed_curve_id)
        psd = analysis.psd_curve(
            processed_curve_id,
            block_size=min(64, len(analysis.get_curve(processed_curve_id).y)),
        )
        processing.update(
            {
                "fft_points": len(fft.x),
                "psd_points": len(psd.x),
                "fft_finite": bool(all(map(lambda value: value == value, fft.y))),
                "psd_finite": bool(all(map(lambda value: value == value, psd.y))),
            }
        )
    return {
        "signal_count": len(signals),
        "samples": buffer.sample_count,
        "requested_interval_ms": interval_ms,
        "actual_interval_ms": service.stats.actual_interval_ms,
        "late_samples": service.stats.late_samples,
        "saved_record": str(saved),
        "roundtrip_headers": record.headers,
        "record_processing": processing,
        "restored": restore_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples-3", type=int, default=8)
    parser.add_argument("--samples-40", type=int, default=5)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "server": args.server,
        "port": args.port,
        "baudrate": args.baudrate,
        "checks": {},
    }
    session = open_comm_server(
        args.port,
        baudrate=args.baudrate,
        server=args.server,
        auto_start=False,
        client_name="live-curve-hardware-probe",
        readonly=False,
        timeout=8.0,
    )
    return_code = 1
    before = None
    try:
        version = session.open()
        constants = session.get_global_system_constants()
        catalog = build_monitor_signal_catalog(
            MonitorCapabilities.from_controller(constants, version)
        )
        before = _trace_snapshot(session)
        checks = report["checks"]
        checks["connection"] = {
            "firmware": str(version),
            "firmware_info": version.full_text,
            "system_constants": constants,
            "catalog_size": len(catalog),
        }
        checks["three_signals"] = _exercise(
            session,
            tuple(catalog[:3]),
            100,
            args.samples_3,
            output_dir / "real_time_curve_3.csv",
        )
        checks["forty_signals"] = _exercise(
            session,
            tuple(catalog[:40]),
            100,
            args.samples_40,
            output_dir / "real_time_curve_40.csv",
        )
        after = _trace_snapshot(session)
        fields = ("monitor_definitions", "event_params", "event_info", "saved_trace_num")
        differences = {
            field: {"before": before[field], "after": after[field]}
            for field in fields
            if before[field] != after[field]
        }
        checks["preservation"] = {
            "before": before,
            "after": after,
            "differences": differences,
        }
        if differences:
            raise RuntimeError(f"controller state changed: {sorted(differences)}")
        report["ok"] = True
        return_code = 0
    except BaseException as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        if before is not None and session.connected:
            try:
                report["failure_snapshot"] = _trace_snapshot(session)
            except Exception as snapshot_error:
                report["failure_snapshot_error"] = str(snapshot_error)
    finally:
        try:
            session.close()
        except Exception:
            pass
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report_path = output_dir / "live_curve_hardware_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
