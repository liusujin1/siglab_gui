"""Live same-value smoke test for protocol setters fixed from hardware evidence."""

from __future__ import annotations

import argparse
import math

from python_samba.services.session import open_serial


def _matches(before: float, after: float) -> bool:
    return math.isclose(float(before), float(after), rel_tol=1e-6, abs_tol=1e-7)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    args = parser.parse_args()

    session = open_serial(
        args.port, args.baudrate, readonly=False, timeout=3.0
    )
    try:
        version = session.open()
        print(f"CONNECTED {version} readonly={session.readonly}", flush=True)

        sample_before = session.get_sample_frequency()
        session.set_sample_frequency(sample_before)
        sample_after = session.get_sample_frequency()
        if not _matches(sample_before, sample_after):
            raise AssertionError(
                f"NSSFR readback mismatch: {sample_before!r} -> {sample_after!r}"
            )
        print(
            f"PASS NSSFR same-value {sample_before!r} -> {sample_after!r}",
            flush=True,
        )

        dither_before = session.get_dither_frequency()
        session.set_dither_frequency(dither_before)
        dither_after = session.get_dither_frequency()
        if not _matches(dither_before, dither_after):
            raise AssertionError(
                f"PSDFR readback mismatch: {dither_before!r} -> {dither_after!r}"
            )
        print(
            f"PASS PSDFR same-value {dither_before!r} -> {dither_after!r}",
            flush=True,
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
