"""Read-only hardware verification for LoggingTool integration.

The probe never sends DSETP/DSETS/DSMOS/DSSET.  In particular it will not
start controller-internal logging because DSSET=1 deletes saved traces.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from python_samba.logging_tools import (  # noqa: E402
    FileLoggingConfig,
    FileLoggingService,
    load_logging_record,
)
from python_samba.services.session import open_serial  # noqa: E402
from python_samba.ui.classic_widgets import IOSignalButton  # noqa: E402


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--duration-s", type=float, default=3.0)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "_review" / "hardware_probe_results"),
    )
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir).resolve() / f"logging_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "monitor_40ch.csv"
    report_path = output_dir / "logging_hardware_report.json"
    report: dict[str, object] = {
        "port": args.port,
        "baudrate": args.baudrate,
        "readonly": True,
        "controller_writes": 0,
        "checks": [],
    }
    checks: list[dict[str, object]] = report["checks"]  # type: ignore[assignment]

    try:
        with open_serial(args.port, args.baudrate, readonly=True) as session:
            version = session.get_version()
            report["firmware"] = str(version)
            before = {
                "params": session.get_event_trace_params(),
                "info": session.get_event_trace_info(),
                "event": session.get_event_signal(),
                "monitor": [session.get_monitor_signal(index) for index in range(40)],
            }
            checks.append(
                {
                    "name": "read_all_logging_configuration",
                    "status": "PASS",
                    "monitor_definitions": len(before["monitor"]),
                    "params": before["params"],
                    "info": before["info"],
                }
            )

            names = []
            for index, raw in enumerate(before["monitor"]):
                values = [_integer(value) for value in list(raw)[:3]]
                values.extend([0] * (3 - len(values)))
                names.append(IOSignalButton.format_io_signal(values[:3]))

            timings = []
            for _ in range(10):
                started = time.perf_counter()
                values = session.get_monitor_values(0, 39)
                timings.append((time.perf_counter() - started) * 1000.0)
                if len(values) != 40:
                    raise RuntimeError(f"DGMSV returned {len(values)} values, expected 40")
            average_ms = sum(timings) / len(timings)
            checks.append(
                {
                    "name": "dgmsv_40_channel_rate",
                    "status": "PASS",
                    "reads": len(timings),
                    "average_ms": average_ms,
                    "minimum_ms": min(timings),
                    "maximum_ms": max(timings),
                }
            )

            service = FileLoggingService(session)
            interval_ms = max(args.interval_ms, int(math.ceil(average_ms * 1.25)), 10)
            service.start(
                FileLoggingConfig(
                    csv_path,
                    signal_count=40,
                    interval_ms=interval_ms,
                    start_after_s=0,
                    duration_s=args.duration_s,
                    signal_names=tuple(names),
                )
            )
            deadline = time.monotonic() + args.duration_s + 15.0
            while service.running and time.monotonic() < deadline:
                time.sleep(0.05)
            if service.running:
                service.stop(wait=True, timeout=2.0)
                raise TimeoutError("host file logging did not stop after its duration")
            if service.stats.state != "complete":
                raise RuntimeError(
                    f"host file logging ended as {service.stats.state}: {service.stats.message}"
                )
            record = load_logging_record(csv_path)
            if len(record.rows) != service.stats.samples or not record.rows:
                raise RuntimeError("streamed CSV sample count does not match acquisition stats")
            if any(len(row) != 42 for row in record.rows):
                raise RuntimeError("streamed CSV does not contain timestamp + elapsed + 40 values")
            checks.append(
                {
                    "name": "stream_40_channels_to_file",
                    "status": "PASS",
                    "configured_interval_ms": interval_ms,
                    "stats": asdict(service.stats),
                    "csv_rows": len(record.rows),
                    "csv_columns": len(record.rows[0]),
                }
            )

            info = before["info"]
            saved = _integer(info[2]) if len(info) > 2 else 0
            if saved > 0:
                event_time = session.get_event_time(0)
                trace_rows = session.download_logged_trace(0, max_samples=20)
                checks.append(
                    {
                        "name": "read_saved_internal_trace",
                        "status": "PASS",
                        "saved_traces": saved,
                        "rows_read": len(trace_rows),
                        "event_time": event_time,
                    }
                )
            else:
                checks.append(
                    {
                        "name": "read_saved_internal_trace",
                        "status": "SKIP_NO_SAVED_TRACE",
                        "reason": "DSSET was intentionally not started because it deletes saved traces",
                    }
                )

            after = {
                "params": session.get_event_trace_params(),
                "event": session.get_event_signal(),
                "monitor": [session.get_monitor_signal(index) for index in range(40)],
            }
            unchanged = all(after[key] == before[key] for key in after)
            checks.append(
                {
                    "name": "logging_configuration_unchanged",
                    "status": "PASS" if unchanged else "FAIL",
                }
            )
            if not unchanged:
                raise RuntimeError("read-only probe observed a logging configuration change")
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
    else:
        report["status"] = "PASS"

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
