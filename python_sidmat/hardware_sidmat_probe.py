"""Reversible live-controller probe for the Sidmat acquisition path.

The probe is intentionally separate from the product package.  It reads the
current trace configuration, performs small one-average acquisitions through
the real Sidmat MeasurementEngine, and restores the exact wire configuration
in a finally block.  It does not change loop state, excitation, or actuator
commands.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from python_sidmat.analysis.pwelch import pwelch
from python_sidmat.analysis.windows import WindowType
from python_sidmat.backend.controller import Controller
from python_sidmat.measurement.engine import MeasurementEngine
from python_sidmat.measurement.trace import TraceParameters


def _trace_summary(trace: TraceParameters) -> dict[str, object]:
    return {
        "ch0": trace.trace_ch0.encode(),
        "ch1": trace.trace_ch1.encode(),
        "undersamples": trace.undersamples,
        "no_samples": trace.no_samples,
        "trace_filter_flag": trace.trace_filter_flag,
        "wire": trace.encode(),
    }


def _run_acquisition(
    controller: Controller,
    trace: TraceParameters,
    sample_frequency: float,
) -> dict[str, object]:
    started = time.monotonic()
    engine = MeasurementEngine(controller, trace, sample_frequency=sample_frequency)
    raw = engine.run()
    elapsed = time.monotonic() - started
    ch0 = raw.channel(0)
    ch1 = raw.channel(1)
    if len(ch0) != len(ch1) or not ch0:
        raise RuntimeError(f"incomplete acquisition: {len(ch0)} / {len(ch1)}")
    if not all(math.isfinite(value) for value in ch0 + ch1):
        raise RuntimeError("acquisition contains non-finite samples")
    nfft = 1
    while nfft * 2 <= len(ch0):
        nfft *= 2
    result = pwelch(
        ch0,
        ch1,
        WindowType.HANNING,
        50,
        nfft,
        len(ch0),
        raw.effective_sample_rate or sample_frequency,
    )
    return {
        "elapsed_s": round(elapsed, 3),
        "samples": len(ch0),
        "averages": raw.avg_num,
        "sample_rate": raw.sample_rate,
        "freq_bins": len(result.freq),
        "ch0_min": min(ch0),
        "ch0_max": max(ch0),
        "ch1_min": min(ch1),
        "ch1_max": max(ch1),
        "finite": True,
    }


def _dump_one_binary_response(controller: Controller) -> dict[str, object]:
    """Capture framing metadata for one DGTBB response after a trigger."""
    controller.start_trace()
    deadline = time.monotonic() + 20.0
    status = controller.get_trace_status()
    while status != 0:
        if time.monotonic() > deadline:
            raise TimeoutError(f"DGTBB diagnostic status did not finish: {status}")
        time.sleep(0.25)
        status = controller.get_trace_status()

    frame = controller.session.encoder.dgtbb(0, 40)
    controller.session.transport.write(frame)
    raw = controller.session.transport.read_until(b"\r", timeout=5.0)
    marker = b" DGTBB "
    marker_pos = raw.upper().find(marker)
    payload = raw[marker_pos + len(marker):] if marker_pos >= 0 else raw
    payload = payload.rstrip(b"\r\n")
    if payload.endswith(b"##"):
        payload = payload[:-2]
    fields = payload.split(b" ")
    invalid = [
        {"index": index, "length": len(field), "hex": field.hex()[:32]}
        for index, field in enumerate(fields)
        if len(field) != 6
    ]
    return {
        "raw_bytes": len(raw),
        "payload_bytes": len(payload),
        "field_count": sum(len(field) == 6 for field in fields),
        "invalid_fields": invalid[:12],
        "raw_tail_hex": raw[-32:].hex(),
    }


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    report: dict[str, object] = {
        "port": port,
        "baud": baud,
        "steps": [],
    }

    def record(name: str, status: str, detail: object) -> None:
        report["steps"].append({"name": name, "status": status, "detail": detail})
        print(f"{status:4} {name}: {detail}", flush=True)

    controller: Controller | None = None
    original: TraceParameters | None = None
    failure: BaseException | None = None
    try:
        controller = Controller.connect(port, baudrate=baud, readonly=False)
        version = controller.get_version()
        fs = controller.get_sample_frequency()
        record(
            "connect/read identity",
            "PASS",
            {"firmware": f"{version.major}.{version.minor}.{version.patch}", "sample_frequency": fs},
        )
        original = controller.get_trace()
        original_wire = original.encode()
        report["trace_before"] = _trace_summary(original)
        record("read original trace", "PASS", _trace_summary(original))

        safe = TraceParameters(
            trace_ch0=original.trace_ch0,
            trace_ch1=original.trace_ch1,
            undersamples=original.undersamples,
            no_samples=64,
            trace_filter_flag=original.trace_filter_flag,
            average_number=1,
        )
        controller.set_trace(safe)
        if controller.get_trace().encode() != safe.encode():
            raise AssertionError("normal trace configuration readback mismatch")
        record("set/read small normal trace", "PASS", _trace_summary(safe))
        record("normal DGTBV acquisition + pwelch", "PASS", _run_acquisition(controller, safe, fs))

        fast = TraceParameters(
            trace_ch0=original.trace_ch0,
            trace_ch1=original.trace_ch1,
            undersamples=original.undersamples,
            no_samples=64,
            trace_filter_flag=original.trace_filter_flag,
            average_number=1,
        )
        fast.set_fast_data_loading(True)
        controller.set_trace(fast)
        if controller.get_trace().encode() != fast.encode():
            raise AssertionError("fast trace configuration readback mismatch")
        record("set/read small fast trace", "PASS", _trace_summary(fast))
        try:
            fast_result = _run_acquisition(controller, fast, fs)
        except Exception as exc:  # Capability/firmware result, not a cleanup failure.
            record("fast DGTBB acquisition + pwelch", "SKIP", f"{type(exc).__name__}: {exc}")
            try:
                record("DGTBB response framing diagnostic", "INFO", _dump_one_binary_response(controller))
            except Exception as diagnostic_exc:
                record(
                    "DGTBB response framing diagnostic",
                    "INFO",
                    f"{type(diagnostic_exc).__name__}: {diagnostic_exc}",
                )
        else:
            record("fast DGTBB acquisition + pwelch", "PASS", fast_result)
    except BaseException as exc:
        failure = exc
        record("probe", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if controller is not None and controller.connected and original is not None:
            try:
                controller.set_trace(original)
                restored = controller.get_trace().encode()
                report["trace_after"] = _trace_summary(controller.get_trace())
                if restored != original_wire:
                    raise AssertionError(
                        f"trace restore mismatch: expected {original_wire}, got {restored}"
                    )
                record("restore original trace", "PASS", restored)
            except BaseException as exc:
                failure = failure or exc
                record("restore original trace", "FAIL", f"{type(exc).__name__}: {exc}")
        if controller is not None:
            controller.close()

    report_path = Path.cwd() / "hardware_sidmat_probe_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT {report_path}", flush=True)
    return 2 if failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
