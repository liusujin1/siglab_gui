"""Read-only timing probe for a remote SAMBA controller.

This file deliberately calls getters only.  The session is created with
``readonly=True`` so an accidental setter cannot reach the controller.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from typing import Any

from python_samba.services.session import open_serial


def _short(value: Any, limit: int = 100) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    session = open_serial(
        args.port,
        args.baud,
        readonly=True,
        timeout=args.timeout,
    )
    samples: list[tuple[str, float]] = []
    failures: list[str] = []

    def run(label: str, fn: Callable[[], Any], *, fatal: bool = False) -> Any:
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"ERROR {label:32s} {elapsed:8.4f} s  {type(exc).__name__}: {exc}", flush=True)
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            if fatal:
                raise
            return None
        elapsed = time.perf_counter() - started
        samples.append((label, elapsed))
        print(f"OK    {label:32s} {elapsed:8.4f} s  {_short(value)}", flush=True)
        return value

    started_all = time.perf_counter()
    try:
        version = run("connect + BGVIS", session.open, fatal=True)
        print(f"READONLY={session.readonly} FIRMWARE={version.full_text}", flush=True)

        run("BGSTS loop status", session.get_loop_status)
        run("BGSST extended status", session.get_pos_pneum_digital_status)
        run("NGSFR sample frequency", session.get_sample_frequency)
        run("VGVFS velocity filter 0/0", lambda: session.get_velocity_filter(0, 0))
        run("VGSMV velocity sensor row 0", lambda: session.get_velocity_sensor_matrix(0))
        run("VGMMV velocity motor row 0", lambda: session.get_velocity_motor_matrix(0))
        run("CGPFS position filter 0/0", lambda: session.get_proximity_filter(0, 0))
        run("CGPOV proximity offsets", session.get_proximity_offsets)
        run("PGPAF pneumatic filter 0/0", lambda: session.get_pneumatic_filter(0, 0))
        run("PGPSM pneumatic matrix row 0", lambda: session.get_pneumatic_steering_matrix(0))
        run("PGPVO pneumatic offsets", session.get_pneumatic_valve_offsets)
        run("FGFFC FF configuration", session.get_ff_config)
        run("FGFFL FF output limit", session.get_ff_output_limit)
        run("FGPFS FF filter 0/0", lambda: session.get_ff_filter(0, 0))
        run("PGPFC PFF configuration", session.get_pff_config)
        run("PGPFF PFF filter 0/0/0", lambda: session.get_pff_filter(0, 0, 0))

        # Repeat a lightweight command to expose steady-state latency and jitter.
        for index in range(5):
            run(f"BGSTS repeat {index + 1}", session.get_loop_status)
    finally:
        session.close()

    elapsed_all = time.perf_counter() - started_all
    transaction_times = [elapsed for label, elapsed in samples if not label.startswith("connect")]
    print("SUMMARY", flush=True)
    print(
        f"total={elapsed_all:.4f} s commands={len(samples)} failures={len(failures)}",
        flush=True,
    )
    if transaction_times:
        ordered = sorted(transaction_times)
        p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        print(
            "steady "
            f"min={min(transaction_times):.4f} s "
            f"mean={statistics.fmean(transaction_times):.4f} s "
            f"p95={ordered[p95_index]:.4f} s "
            f"max={max(transaction_times):.4f} s",
            flush=True,
        )
    for failure in failures:
        print(f"FAILURE {failure}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
