"""Capture and time a complete controller configuration without writing it."""

from __future__ import annotations

import argparse
import hashlib
import statistics
import time
from pathlib import Path
from typing import Any

from python_samba.services.config_reader import capture_config_from_session, save_config
from python_samba.services.session import open_serial


def _mnemonic(frame: bytes) -> str:
    try:
        parts = frame.decode("ascii").split()
        return parts[1][:5] if len(parts) > 1 else "?????"
    except Exception:
        return "?????"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        help="optionally save the captured read-only snapshot as XML",
    )
    args = parser.parse_args()

    session = open_serial(
        args.port,
        args.baud,
        readonly=True,
        timeout=args.timeout,
    )
    current_label = "connect"
    records: list[tuple[str, str, float, str]] = []

    original_transact = session.transact

    def timed_transact(frame: bytes) -> Any:
        started = time.perf_counter()
        mnemonic = _mnemonic(frame)
        outcome = "OK"
        try:
            response = original_transact(frame)
            if not response.ok:
                outcome = f"STATUS_0x{response.status_code:02X}"
            return response
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            records.append(
                (current_label, mnemonic, time.perf_counter() - started, outcome)
            )

    session.transact = timed_transact  # type: ignore[method-assign]

    def progress(label: str) -> None:
        nonlocal current_label
        current_label = label

    started_all = time.perf_counter()
    config = None
    capture_error: Exception | None = None
    try:
        version = session.open()
        print(f"CONNECTED {version.full_text}", flush=True)
        capture_started = time.perf_counter()
        try:
            config = capture_config_from_session(session, progress=progress)
        except Exception as exc:
            capture_error = exc
        capture_elapsed = time.perf_counter() - capture_started
    finally:
        session.close()
    elapsed_all = time.perf_counter() - started_all

    durations = [item[2] for item in records]
    ordered = sorted(durations)
    p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    non_ok = [item for item in records if item[3] != "OK"]
    warnings = config.capture_warnings if config is not None else []
    print(
        f"SNAPSHOT elapsed={capture_elapsed:.4f}s total={elapsed_all:.4f}s "
        f"transactions={len(records)} warnings={len(warnings)} "
        f"non_ok_frames={len(non_ok)}",
        flush=True,
    )
    print(
        f"LATENCY min={min(durations):.4f}s mean={statistics.fmean(durations):.4f}s "
        f"p95={ordered[p95_index]:.4f}s max={max(durations):.4f}s",
        flush=True,
    )

    print("SLOWEST", flush=True)
    for label, mnemonic, elapsed, outcome in sorted(
        records, key=lambda item: item[2], reverse=True
    )[:15]:
        print(f"{elapsed:8.4f}s {mnemonic:5s} {outcome:24.24s} {label}", flush=True)

    if non_ok:
        print("NON_OK_FRAMES", flush=True)
        for label, mnemonic, elapsed, outcome in non_ok:
            print(f"{elapsed:8.4f}s {mnemonic:5s} {outcome} {label}", flush=True)

    if capture_error is not None:
        print(
            f"CAPTURE_ERROR {type(capture_error).__name__}: {capture_error}",
            flush=True,
        )

    if warnings:
        print("CAPTURE_WARNINGS", flush=True)
        for warning in warnings:
            print(warning, flush=True)

    if capture_error is None and not warnings and config is not None and args.output:
        save_config(args.output, config)
        payload = args.output.read_bytes()
        print(
            f"SAVED path={args.output.resolve()} bytes={len(payload)} "
            f"sha256={hashlib.sha256(payload).hexdigest().upper()}",
            flush=True,
        )

    if capture_error is not None:
        return 3
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
