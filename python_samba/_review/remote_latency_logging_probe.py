"""Read/action probe for WAN refresh, host logging, and SIDMAT trace download."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from python_samba.logging_tools import FileLoggingConfig, FileLoggingService
from python_samba.services.session import open_comm_server


def _milliseconds(work):
    started = time.perf_counter()
    value = work()
    return value, (time.perf_counter() - started) * 1000.0


def _wait_for_event_state(session, expected: int, timeout: float = 8.0) -> list[str]:
    deadline = time.monotonic() + timeout
    latest: list[str] = []
    while time.monotonic() < deadline:
        latest = session.get_event_trace_info()
        if latest and int(str(latest[0]), 0) == int(expected):
            return latest
        time.sleep(0.1)
    raise TimeoutError(
        f"event logging did not reach state {expected}; last DGETI={latest!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-actions", action="store_true")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stream_path = output.with_name(output.stem + "_stream.csv")
    report: dict[str, object] = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "server": args.server,
        "port": args.port,
        "baud": args.baud,
        "checks": {},
    }
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]

    session = open_comm_server(
        args.port,
        baudrate=args.baud,
        server=args.server,
        auto_start=False,
        client_name="latency-logging-hardware-probe",
        readonly=False,
        timeout=5.0,
    )
    service: FileLoggingService | None = None
    original_event_state: int | None = None
    try:
        version, open_ms = _milliseconds(session.open)
        checks["connect"] = {"version": str(version), "elapsed_ms": open_ms}

        singles: list[float] = []
        for _ in range(8):
            _value, elapsed = _milliseconds(session.get_loop_status)
            singles.append(elapsed)
        checks["single_loop_reads"] = {
            "count": len(singles),
            "median_ms": statistics.median(singles),
            "maximum_ms": max(singles),
        }

        live, live_ms = _milliseconds(
            lambda: session.get_live_refresh_snapshot(
                include_switch_conditions=True,
                include_axis_status=True,
                include_controller_config=True,
                proximity_count=6,
                include_motor=True,
                include_power_supply=True,
                include_pneumatic=True,
                monitor_count=16,
            )
        )
        checks["combined_live_refresh"] = {
            "elapsed_ms": live_ms,
            "fields": sorted(live),
        }

        workspace, workspace_ms = _milliseconds(
            lambda: session.get_logging_workspace_snapshot(16)
        )
        checks["logging_workspace"] = {
            "elapsed_ms": workspace_ms,
            "definition_count": len(workspace["signals"]),
            "value_count": len(workspace["values"]),
        }

        service = FileLoggingService(session)
        service.start(
            FileLoggingConfig(
                stream_path,
                signal_count=16,
                interval_ms=500,
                start_after_s=0,
                duration_s=None,
                signal_names=tuple(f"Monitor{index + 1}" for index in range(16)),
            )
        )
        deadline = time.monotonic() + 12.0
        foreground_reads: list[float] = []
        while service.stats.samples < 3 and time.monotonic() < deadline:
            _value, elapsed = _milliseconds(session.get_loop_status)
            foreground_reads.append(elapsed)
        if service.stats.samples < 3:
            raise TimeoutError(
                f"host logging produced only {service.stats.samples} samples"
            )
        stop_started = time.perf_counter()
        service.stop(wait=True, timeout=10.0)
        stop_ms = (time.perf_counter() - stop_started) * 1000.0
        if service.running:
            raise TimeoutError("host logging worker did not stop within 10 seconds")
        checks["host_file_logging"] = {
            "samples": service.stats.samples,
            "state": service.stats.state,
            "actual_interval_ms": service.stats.actual_interval_ms,
            "stop_elapsed_ms": stop_ms,
            "foreground_read_median_ms": statistics.median(foreground_reads),
            "output": str(stream_path),
        }

        if not args.skip_actions:
            before_info = session.get_event_trace_info()
            original_event_state = int(str(before_info[0]), 0) if before_info else 0
            started_at = time.perf_counter()
            session.start_stop_event_tracing(1)
            running_info = _wait_for_event_state(session, 1)
            session.start_stop_event_tracing(0)
            stopped_info = _wait_for_event_state(session, 0)
            checks["internal_logging_start_stop"] = {
                "elapsed_ms": (time.perf_counter() - started_at) * 1000.0,
                "running_info": running_info,
                "stopped_info": stopped_info,
                "original_state": original_event_state,
            }

            from python_sidmat.backend.controller import Controller
            from python_sidmat.measurement.engine import MeasurementEngine
            from python_sidmat.measurement.trace import TraceParameters

            trace = TraceParameters.from_tokens(session.get_digital_trace_info())
            trace.average_number = 1
            trace.set_fast_data_loading(True)
            controller = Controller(session)
            sample_frequency = session.get_sample_frequency()
            engine = MeasurementEngine(controller, trace, sample_frequency)
            raw, trace_ms = _milliseconds(engine.run)
            if raw.sample_num != trace.no_samples:
                raise RuntimeError(
                    f"SIDMAT returned {raw.sample_num}/{trace.no_samples} samples"
                )
            checks["sidmat_trace"] = {
                "elapsed_ms": trace_ms,
                "sample_count": raw.sample_num,
                "fast_binary": trace.is_fast_data_loading,
                "chunk_count": trace.read_chunk_count,
            }

        checks["server_status"] = session.transport.status()
        report["ok"] = True
        return_code = 0
    except BaseException as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        return_code = 1
    finally:
        if service is not None and service.running:
            service.stop(wait=True, timeout=10.0)
        if original_event_state is not None and session.connected:
            try:
                current = session.get_event_trace_info()
                current_state = int(str(current[0]), 0) if current else 0
                if current_state != original_event_state:
                    session.start_stop_event_tracing(original_event_state)
                    _wait_for_event_state(session, original_event_state)
            except BaseException as exc:
                report["restore_error"] = f"{type(exc).__name__}: {exc}"
                report["ok"] = False
                return_code = 1
        try:
            session.close()
        except Exception:
            pass
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
