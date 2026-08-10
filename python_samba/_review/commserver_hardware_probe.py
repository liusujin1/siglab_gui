"""Live-controller acceptance probe for the shared Communication Server.

The probe is transactional for every setting it changes.  It snapshots the
digital-trace configuration and output limit, exercises a Sidmat acquisition
while a second Samba client reads status (and, in host mode, temporarily writes
BSOPL), then restores and verifies both settings in ``finally``.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from python_samba.commserver.protocol import parse_endpoint
from python_samba.commserver.server import CommunicationServer
from python_samba.services.session import open_comm_server, open_serial
from python_sidmat.backend.controller import Controller
from python_sidmat.measurement.engine import MeasurementEngine
from python_sidmat.measurement.trace import TraceParameters


def _trace_dict(trace: TraceParameters) -> dict[str, Any]:
    return {
        "trace_ch0": list(trace.trace_ch0.encode()),
        "trace_ch1": list(trace.trace_ch1.encode()),
        "undersamples": trace.undersamples,
        "no_samples": trace.no_samples,
        "trace_filter_flag": trace.trace_filter_flag,
    }


def _wait_for(predicate, timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _alternate_limit(original: int) -> int:
    if not 0 <= original <= 100:
        raise AssertionError(f"unexpected BSOPL value {original!r}")
    return original - 1 if original > 0 else 1


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "server": args.server,
        "port": args.port,
        "baudrate": args.baudrate,
        "host_server": bool(args.host_server),
        "checks": {},
    }
    checks: dict[str, Any] = report["checks"]
    server: CommunicationServer | None = None
    samba = None
    sidmat: Controller | None = None
    original_trace: TraceParameters | None = None
    original_limit: int | None = None
    measurement_error: list[BaseException] = []
    measurement_result: list[Any] = []
    measurement_thread: threading.Thread | None = None

    try:
        if args.host_server:
            server = CommunicationServer(
                parse_endpoint(args.server),
                preferred_port=args.port,
                preferred_baudrate=args.baudrate,
                log_file=args.log_file,
            ).start()
            checks["listener"] = [f"{host}:{port}" for host, port in server.addresses]

        samba = open_comm_server(
            args.port,
            args.baudrate,
            server=args.server,
            token_file=args.token_file,
            auto_start=False,
            client_name="hardware-probe-samba",
            readonly=False,
            timeout=5.0,
        )
        samba_version = samba.open()
        sidmat = Controller.connect_server(
            args.port,
            args.baudrate,
            server=args.server,
            token_file=args.token_file,
            auto_start=False,
            readonly=False,
            timeout=5.0,
        )
        checks["versions"] = {
            "samba": str(samba_version),
            "sidmat": str(sidmat.version),
        }
        status = samba.transport.status()
        if status["attached_count"] != 2 or not status["serial"]["open"]:
            raise AssertionError(f"two-client attach failed: {status!r}")
        checks["two_clients_attached"] = {
            "client_count": status["client_count"],
            "attached_count": status["attached_count"],
            "serial": status["serial"],
        }

        if args.host_server:
            direct = open_serial(args.port, args.baudrate, readonly=True, timeout=1.0)
            try:
                direct.open()
            except BaseException as exc:
                detail = f"{type(exc).__name__}: {exc}"
                denied_markers = ("PermissionError", "Access is denied", "拒绝访问")
                if not any(marker.lower() in detail.lower() for marker in denied_markers):
                    raise AssertionError(
                        f"direct COM open failed for an unexpected reason: {detail}"
                    ) from exc
                checks["physical_serial_single_owner"] = detail
            else:
                raise AssertionError("direct serial unexpectedly opened while server owns COM")
            finally:
                direct.close()

        original_trace = sidmat.get_trace()
        original_limit = samba.get_output_limit()
        checks["before"] = {
            "trace": _trace_dict(original_trace),
            "output_limit": original_limit,
        }
        test_trace = TraceParameters(
            trace_ch0=original_trace.trace_ch0,
            trace_ch1=original_trace.trace_ch1,
            undersamples=1,
            no_samples=args.samples,
            trace_filter_flag=original_trace.trace_filter_flag,
            average_number=1,
            is_fast_data_loading=bool(args.fast),
        )
        sidmat.set_trace(test_trace)
        written_trace = sidmat.get_trace()
        if written_trace.encode() != test_trace.encode():
            raise AssertionError(
                f"DSTIV readback mismatch: {written_trace.encode()} != {test_trace.encode()}"
            )

        trace_triggered = threading.Event()
        real_start_trace = sidmat.start_trace

        def start_trace_and_signal() -> list[str]:
            result = real_start_trace()
            trace_triggered.set()
            return result

        sidmat.start_trace = start_trace_and_signal  # type: ignore[method-assign]
        engine = MeasurementEngine(
            sidmat,
            test_trace,
            sidmat.get_sample_frequency(),
        )

        def measure() -> None:
            try:
                measurement_result.append(engine.run())
            except BaseException as exc:
                measurement_error.append(exc)

        measurement_thread = threading.Thread(
            target=measure, name="HardwareProbe-Sidmat", daemon=True
        )
        measurement_thread.start()
        if not trace_triggered.wait(10.0):
            raise AssertionError("Sidmat did not issue DASTA within 10 seconds")

        if args.host_server or args.exercise_write:
            requested_limit = _alternate_limit(original_limit)
            samba.set_output_limit(requested_limit)
            samba_read = samba.get_output_limit()
            sidmat_read = sidmat.get_output_limit()
            # This firmware stores the percentage in a scaled integer.  Values
            # below 100 may canonicalize one count lower on BGOPL (for example,
            # BSOPL 99 -> BGOPL 98).  Both independent clients must nevertheless
            # observe the same changed controller value.
            if samba_read != sidmat_read or samba_read == original_limit:
                raise AssertionError(
                    "cross-client BSOPL readback mismatch: "
                    f"Samba={samba_read}, Sidmat={sidmat_read}, "
                    f"requested={requested_limit}, original={original_limit}"
                )
            samba.set_output_limit(original_limit)
            if samba.get_output_limit() != original_limit:
                raise AssertionError("BSOPL did not restore immediately")
            checks["cross_client_last_write_wins"] = {
                "original": original_limit,
                "requested": requested_limit,
                "applied": samba_read,
                "samba_read": samba_read,
                "sidmat_read": sidmat_read,
                "restored": original_limit,
            }

        refreshes: list[dict[str, Any]] = []
        deadline = time.monotonic() + 30.0
        while len(refreshes) < 3 or measurement_thread.is_alive():
            loop = samba.get_loop_status()
            position, pneumatic, digital_in, digital_out = (
                samba.get_pos_pneum_digital_status()
            )
            motor = samba.get_motor_power_values()
            pneu = samba.get_pneumatic_axes_status()
            refreshes.append(
                {
                    "individual": loop.individual,
                    "system": loop.system,
                    "position": position,
                    "pneumatic": pneumatic,
                    "digital_input": digital_in,
                    "digital_output": digital_out,
                    "motor_values": len(motor),
                    "pneumatic_status": pneu,
                }
            )
            if time.monotonic() >= deadline:
                raise AssertionError("concurrent status refresh exceeded 30 seconds")
            time.sleep(0.01)

        measurement_thread.join(timeout=120.0)
        if measurement_thread.is_alive():
            engine.stop()
            raise AssertionError("Sidmat measurement thread did not finish")
        if measurement_error:
            raise measurement_error[0]
        if not measurement_result:
            raise AssertionError("Sidmat measurement returned no result")
        raw = measurement_result[0]
        if raw.avg_num != 1 or raw.sample_num != args.samples:
            raise AssertionError(
                f"unexpected measurement size avg={raw.avg_num}, samples={raw.sample_num}"
            )
        checks["concurrent_measurement"] = {
            "average_count": raw.avg_num,
            "sample_count": raw.sample_num,
            "status_refresh_cycles": len(refreshes),
            "last_status": refreshes[-1],
        }

        sidmat.set_trace(original_trace)
        original_trace = None
        if samba.get_output_limit() != original_limit:
            raise AssertionError("BSOPL changed after measurement")
        original_limit = None

        samba.close()
        samba = None
        sidmat_status = sidmat.session.transport.status()
        if sidmat_status["attached_count"] != 1 or not sidmat_status["serial"]["open"]:
            raise AssertionError(f"remaining Sidmat client lost COM: {sidmat_status!r}")
        sidmat.get_loop_status()
        checks["one_client_disconnect"] = {
            "attached_count": sidmat_status["attached_count"],
            "serial_open": sidmat_status["serial"]["open"],
        }

        sidmat.close()
        sidmat = None
        if server is not None:
            _wait_for(
                lambda: server.status()["attached_count"] == 0
                and not server.status()["serial"]["open"],
                3.0,
                "server did not release COM after the last client detached",
            )
            final_status = server.status()
            checks["last_client_disconnect"] = {
                "attached_count": final_status["attached_count"],
                "serial_open": final_status["serial"]["open"],
                "completed_requests": final_status["completed_requests"],
                "last_error": final_status["last_error"],
            }
        report["ok"] = True
        return report
    finally:
        restore_errors: list[str] = []
        if measurement_thread is not None and measurement_thread.is_alive():
            measurement_thread.join(timeout=5.0)
        if sidmat is not None and original_trace is not None:
            try:
                sidmat.set_trace(original_trace)
                restored = sidmat.get_trace()
                if restored.encode() != original_trace.encode():
                    raise AssertionError(
                        f"trace restore mismatch {restored.encode()} != {original_trace.encode()}"
                    )
            except BaseException as exc:
                restore_errors.append(f"trace: {type(exc).__name__}: {exc}")
        if samba is not None and original_limit is not None:
            try:
                samba.set_output_limit(original_limit)
                if samba.get_output_limit() != original_limit:
                    raise AssertionError("output-limit restore readback mismatch")
            except BaseException as exc:
                restore_errors.append(f"output_limit: {type(exc).__name__}: {exc}")
        if samba is not None:
            samba.close()
        if sidmat is not None:
            sidmat.close()
        if server is not None:
            server.stop()
        if restore_errors:
            report["restore_errors"] = restore_errors
            raise RuntimeError("; ".join(restore_errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--server", default="127.0.0.1:47619")
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--host-server", action="store_true")
    parser.add_argument(
        "--exercise-write",
        action="store_true",
        help="temporarily change BSOPL through both clients and restore it",
    )
    parser.add_argument("--samples", type=int, default=1024)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.samples <= 8192:
        parser.error("--samples must be in 2..8192")

    report: dict[str, Any]
    try:
        report = run_probe(args)
    except BaseException as exc:
        report = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "server": args.server,
            "port": args.port,
            "baudrate": args.baudrate,
            "host_server": bool(args.host_server),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
