"""Reversible COM probe for Pneumatic Floatation Config and steering matrices.

The probe never invokes PAMOV, PAUCO, or any loop-state command.  Every value
it changes is restored from a complete preflight snapshot in ``finally``.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from python_samba.services.session import open_serial


def _equivalent(left: list[Any], right: list[Any]) -> bool:
    return len(left) == len(right) and all(
        math.isclose(float(a), float(b), rel_tol=1e-5, abs_tol=1e-7)
        for a, b in zip(left, right)
    )


def _config_ints(values: list[str]) -> list[int]:
    converted: list[int] = []
    for value in values:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError(f"non-integral PGPCP value: {value!r}")
        converted.append(int(number))
    if len(converted) != 3:
        raise ValueError(f"PGPCP returned {len(converted)} values, expected 3")
    return converted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "hardware_probe_results",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"pneumatic_config_{stamp}_report.json"
    report: dict[str, Any] = {
        "timestamp": stamp,
        "port": args.port,
        "baudrate": args.baudrate,
        "steps": [],
    }

    def record(name: str, status: str, detail: Any) -> None:
        report["steps"].append(
            {"name": name, "status": status, "detail": detail}
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"{status:4} {name}: {detail}", flush=True)

    session = open_serial(args.port, args.baudrate, readonly=False, timeout=3.0)
    original_config: list[str] | None = None
    original_matrices: list[list[float]] | None = None
    original_valve_offsets: list[float] | None = None
    original_setpoint_status: int | None = None
    failure: Exception | None = None
    try:
        version = session.open()
        record("connect", "PASS", str(version))

        original_config = session.get_pneumatic_config()
        config = _config_ints(original_config)
        original_matrices = [
            session.get_pneumatic_steering_matrix(axis) for axis in range(3)
        ]
        if any(len(row) < 2 or len(row) % 2 for row in original_matrices):
            raise ValueError(
                "PGPSM rows must contain equal input/output halves: "
                f"{[len(row) for row in original_matrices]}"
            )
        original_valve_offsets = session.get_pneumatic_valve_offsets()
        original_setpoint_status = session.get_pneumatic_setpoint_status()
        report["snapshot"] = {
            "config": original_config,
            "matrices": original_matrices,
            "valve_offsets": original_valve_offsets,
            "setpoint_status": original_setpoint_status,
        }
        record(
            "preflight snapshot",
            "PASS",
            {
                "config": config,
                "matrix_lengths": [len(row) for row in original_matrices],
                "valve_offset_count": len(original_valve_offsets),
                "setpoint_status": original_setpoint_status,
            },
        )

        session.set_pneumatic_config(*config)
        same_readback = session.get_pneumatic_config()
        if _config_ints(same_readback) != config:
            raise AssertionError(f"same-value PSPCP mismatch: {same_readback}")
        record("PSPCP same-value write/read", "PASS", same_readback)

        changed_config = list(config)
        changed_config[2] += 1
        session.set_pneumatic_config(*changed_config)
        changed_readback = session.get_pneumatic_config()
        if _config_ints(changed_readback) != changed_config:
            raise AssertionError(f"changed PSPCP mismatch: {changed_readback}")
        session.set_pneumatic_config(*config)
        restored_config = session.get_pneumatic_config()
        if _config_ints(restored_config) != config:
            raise AssertionError(f"PSPCP restore mismatch: {restored_config}")
        record(
            "PSPCP one-field change/restore",
            "PASS",
            {"changed": changed_readback, "restored": restored_config},
        )

        axis = 0
        original_row = original_matrices[axis]
        half = len(original_row) // 2

        input_values = list(original_row[:half])
        input_values[0] += 0.125
        session.set_pneumatic_input_steering_matrix(axis, input_values)
        input_readback = session.get_pneumatic_steering_matrix(axis)
        if not _equivalent(input_readback[:half], input_values):
            raise AssertionError(f"input matrix write mismatch: {input_readback}")
        if not _equivalent(input_readback[half:], original_row[half:]):
            raise AssertionError("input matrix write changed output half")
        session.set_pneumatic_steering_matrix(axis, original_row)
        if not _equivalent(
            session.get_pneumatic_steering_matrix(axis), original_row
        ):
            raise AssertionError("input matrix restore mismatch")
        record("input matrix one-cell change/restore", "PASS", "axis=0 row=0")

        output_values = list(original_row[half:])
        output_values[0] += 0.125
        session.set_pneumatic_output_steering_matrix(axis, output_values)
        output_readback = session.get_pneumatic_steering_matrix(axis)
        if not _equivalent(output_readback[:half], original_row[:half]):
            raise AssertionError("output matrix write changed input half")
        if not _equivalent(output_readback[half:], output_values):
            raise AssertionError(f"output matrix write mismatch: {output_readback}")
        session.set_pneumatic_steering_matrix(axis, original_row)
        if not _equivalent(
            session.get_pneumatic_steering_matrix(axis), original_row
        ):
            raise AssertionError("output matrix restore mismatch")
        record("output matrix one-cell change/restore", "PASS", "axis=0 row=0")
    except Exception as exc:
        failure = exc
        record("probe", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        restore_errors: list[str] = []
        if session.connected:
            if original_config is not None:
                try:
                    session.set_pneumatic_config(*original_config)
                except Exception as exc:
                    restore_errors.append(f"config: {exc}")
            if original_matrices is not None:
                for axis, values in enumerate(original_matrices):
                    try:
                        session.set_pneumatic_steering_matrix(axis, values)
                    except Exception as exc:
                        restore_errors.append(f"matrix axis {axis}: {exc}")

            try:
                final_config = session.get_pneumatic_config()
                final_matrices = [
                    session.get_pneumatic_steering_matrix(axis)
                    for axis in range(3)
                ]
                final_valve_offsets = session.get_pneumatic_valve_offsets()
                final_setpoint_status = session.get_pneumatic_setpoint_status()
                report["final"] = {
                    "config": final_config,
                    "matrices": final_matrices,
                    "valve_offsets": final_valve_offsets,
                    "setpoint_status": final_setpoint_status,
                }
                if original_config is not None and (
                    _config_ints(final_config) != _config_ints(original_config)
                ):
                    restore_errors.append("final config differs from snapshot")
                if original_matrices is not None and any(
                    not _equivalent(before, after)
                    for before, after in zip(original_matrices, final_matrices)
                ):
                    restore_errors.append("final matrices differ from snapshot")
                if original_valve_offsets is not None and not _equivalent(
                    original_valve_offsets, final_valve_offsets
                ):
                    restore_errors.append("valve offsets changed")
                if (
                    original_setpoint_status is not None
                    and final_setpoint_status != original_setpoint_status
                ):
                    restore_errors.append("setpoint status changed")
            except Exception as exc:
                restore_errors.append(f"final verification: {exc}")
        session.close()

        if restore_errors:
            failure = RuntimeError("; ".join(restore_errors))
            record("final restore verification", "FAIL", restore_errors)
        elif original_config is not None:
            record("final restore verification", "PASS", "snapshot restored")

    return 2 if failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
