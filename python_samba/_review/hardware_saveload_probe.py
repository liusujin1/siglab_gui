"""Authorized Save/Load Setup probe for a live SAMBA controller.

Every page action is exercised except Clear NVRAM.  NACLR is guarded at
runtime and is also reported as an explicit user-excluded skip.  The probe
saves a complete setup before writing, restores that setup after the file
round-trip, and compares a final full capture with the initial capture.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from python_samba.services.config_reader import (
    SambaConfig,
    apply_config_to_session,
    capture_config_from_session,
    load_config,
    save_config,
)
from python_samba.services.session import open_mock, open_serial


@dataclass
class Result:
    name: str
    command: str
    status: str
    detail: str = ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            equivalent(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            equivalent(a, b) for a, b in zip(left, right)
        )
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None and right_number is not None:
        return math.isclose(
            left_number, right_number, rel_tol=1e-5, abs_tol=1e-7
        )
    return str(left).strip().upper() == str(right).strip().upper()


def _config_payload(config: SambaConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload.pop("capture_warnings", None)
    return payload


def _integer(value: Any) -> int:
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(float(text))


def complete_pending_digital_trace(session) -> str:
    """Explicitly complete a trace left busy by an earlier DSTIV write."""
    initial = session.get_digital_trace_status()
    if not initial or _integer(initial[0]) == 0:
        return f"already ready: {initial}"
    setup = session.get_digital_trace_info()
    started = session.start_digital_trace()
    if started and _integer(started[0]) != 0:
        raise RuntimeError(f"DASTA rejected while DGTAS={initial}: {started}")
    sample_frequency = max(1.0, float(session.get_sample_frequency()))
    expected_seconds = 0.0
    if len(setup) >= 8:
        expected_seconds = (
            max(1, _integer(setup[6]))
            * max(1, _integer(setup[7]))
            / sample_frequency
        )
    deadline = time.monotonic() + min(60.0, max(15.0, expected_seconds + 10.0))
    status = initial
    while time.monotonic() < deadline:
        status = session.get_digital_trace_status()
        if status and _integer(status[0]) == 0:
            values = session.get_digital_trace_buffer(0)
            return (
                f"{initial}->{status}; DASTA={started}; "
                f"buffer_values={len(values)}"
            )
        time.sleep(0.25)
    raise TimeoutError(f"digital trace did not become ready: {status}")


class SaveLoadProbe:
    def __init__(self, session, output_dir: Path) -> None:
        self.session = session
        self.output_dir = output_dir
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results: list[Result] = []
        self.snapshots: dict[str, Any] = {}

    @property
    def prefix(self) -> Path:
        return self.output_dir / f"hardware_saveload_{self.stamp}"

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": self.stamp,
            "results": [asdict(result) for result in self.results],
            "snapshots": self.snapshots,
        }
        self.prefix.with_name(self.prefix.name + "_report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, name: str, command: str, status: str, detail: str = "") -> None:
        result = Result(name, command, status, detail)
        self.results.append(result)
        print(
            f"{status:18} {command:12} {name}"
            + (f" :: {detail}" if detail else ""),
            flush=True,
        )
        self.save()

    def run(self, name: str, command: str, action: Callable[[], str]) -> None:
        try:
            detail = action()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.add(name, command, "FAIL", detail)
            raise RuntimeError(f"{name} failed: {detail}") from exc
        self.add(name, command, "PASS", detail)


def _apply_file(session, path: Path) -> None:
    errors = apply_config_to_session(load_config(path), session)
    if errors:
        preview = "; ".join(errors[:8])
        if len(errors) > 8:
            preview += f"; ... and {len(errors) - 8} more"
        raise RuntimeError(f"{len(errors)} configuration writes failed: {preview}")


def run_probe(session, output_dir: Path) -> SaveLoadProbe:
    probe = SaveLoadProbe(session, output_dir)
    original: SambaConfig | None = None
    restored = False

    def forbidden_clear(*_args, **_kwargs):
        raise AssertionError("NACLR / Clear NVRAM is explicitly excluded")

    # Guard both public entry points.  The probe has no direct transact path for
    # NACLR, and this prevents a future refactor from silently adding one.
    session.nvram_clear = forbidden_clear
    original_raw_command = session.raw_command

    def guarded_raw_command(mnemonic: str, *params):
        if str(mnemonic).strip().upper() == "NACLR":
            return forbidden_clear()
        return original_raw_command(mnemonic, *params)

    session.raw_command = guarded_raw_command
    probe.add(
        "Clear NVRAM",
        "NACLR",
        "SKIP_USER_EXCLUDED",
        "not invoked; runtime guard installed",
    )

    original_file = probe.prefix.with_name(
        probe.prefix.name + "_original.SAMBA19x_Config"
    )
    modified_file = probe.prefix.with_name(
        probe.prefix.name + "_modified.SAMBA19x_Config"
    )

    try:
        def save_controller_file() -> str:
            nonlocal original
            captured = capture_config_from_session(session)
            if captured.capture_warnings:
                raise RuntimeError(
                    "incomplete controller capture: "
                    + "; ".join(captured.capture_warnings[:8])
                )
            original = captured
            save_config(original_file, original)
            loaded = load_config(original_file)
            # The vendor v8 schema deliberately pads several arrays to their
            # maximum size (for example 12 position axes and 8 velocity
            # inputs), so object equality is not a valid file check.  Verify
            # the identifying and safety-critical scalar values instead; the
            # full controller round-trip is checked at the end of the probe.
            for field_name in (
                "firmware_version",
                "system_configuration",
                "loop_status",
                "individual_loop_status",
                "motors_limit",
                "sample_frequency",
                "trace_no_samples",
                "trace_undersample",
                "trace_filter_flag",
            ):
                if not equivalent(
                    getattr(original, field_name), getattr(loaded, field_name)
                ):
                    raise AssertionError(
                        f"saved XML changed {field_name}: "
                        f"{getattr(original, field_name)!r} -> "
                        f"{getattr(loaded, field_name)!r}"
                    )
            probe.snapshots["initial_config"] = _config_payload(original)
            probe.snapshots["original_file"] = str(original_file)
            return f"complete controller snapshot saved to {original_file.name}"

        probe.run(
            "Controller -> Save File",
            "GET/XML",
            save_controller_file,
        )
        assert original is not None

        def read_checksums_before() -> str:
            values = session.check_nvram_checksums()
            probe.snapshots["checksums_before"] = values
            return f"BCNCS={values} (mismatch is allowed before Build Check Sum)"

        probe.run("Read Check Sum (before)", "BCNCS", read_checksums_before)

        probe.run(
            "Save current setup to NVRAM",
            "NASUP",
            lambda: (session.nvram_save() or "current live setup saved"),
        )

        def apply_modified_file() -> str:
            nonlocal restored
            modified = load_config(original_file)
            before = int(round(float(session.get_dither_frequency())))
            changed = before - 1 if before > 1 else before + 1
            modified.dither_frequency = changed
            save_config(modified_file, modified)
            restored = False
            _apply_file(session, modified_file)
            after = int(round(float(session.get_dither_frequency())))
            if after != changed:
                raise AssertionError(
                    f"Open File did not update dither frequency {before}->{changed}: {after}"
                )
            probe.snapshots["file_apply_dither_before"] = before
            probe.snapshots["file_apply_dither_modified"] = after
            return f"dither frequency {before}->{changed}; full file apply succeeded"

        probe.run(
            "Open File -> Controller",
            "XML/SET",
            apply_modified_file,
        )

        def restore_original_file() -> str:
            nonlocal restored
            _apply_file(session, original_file)
            restored_dither = int(round(float(session.get_dither_frequency())))
            expected_dither = probe.snapshots["file_apply_dither_before"]
            if not equivalent(expected_dither, restored_dither):
                raise AssertionError(
                    f"dither frequency was not restored: {restored_dither}"
                )
            restored = True
            return "original XML applied and dither frequency restored"

        probe.run(
            "Restore original setup file",
            "XML/SET",
            restore_original_file,
        )

        def restore_from_nvram() -> str:
            nonlocal restored
            original_frequency = int(round(float(session.get_dither_frequency())))
            changed_frequency = (
                original_frequency - 1 if original_frequency > 1
                else original_frequency + 1
            )
            restored = False
            session.set_dither_frequency(changed_frequency)
            current_frequency = int(round(float(session.get_dither_frequency())))
            if current_frequency != changed_frequency:
                raise AssertionError(
                    "test dither frequency did not change before NARUP: "
                    f"{current_frequency}"
                )
            session.nvram_restore()
            restored_frequency = int(round(float(session.get_dither_frequency())))
            if restored_frequency != original_frequency:
                raise AssertionError(
                    "NARUP dither frequency mismatch: "
                    f"{restored_frequency} != {original_frequency}"
                )
            restored = True
            return (
                f"dither frequency {original_frequency}->{changed_frequency}"
                f"->{restored_frequency}"
            )

        probe.run("Restore setup from NVRAM", "NARUP", restore_from_nvram)

        built_values: list[int] = []

        def build_checksums() -> str:
            built_values.extend(session.build_nvram_checksums())
            probe.snapshots["checksums_built"] = list(built_values)
            return f"BBNCS={built_values}"

        probe.run("Build Check Sum", "BBNCS", build_checksums)

        def read_checksums_after() -> str:
            values = session.check_nvram_checksums()
            if len(values) != 7:
                raise AssertionError(f"expected 7 BCNCS values, got {values}")
            status = _integer(values[0])
            saved = [values[1], values[3], values[5]]
            actual = [values[2], values[4], values[6]]
            if status & 0x7:
                raise AssertionError(f"checksum status bits are not all OK: 0x{status:X}")
            if not equivalent(saved, actual):
                raise AssertionError(f"saved/actual checksum mismatch: {saved} != {actual}")
            if built_values and not equivalent(built_values, actual):
                raise AssertionError(f"BBNCS/BCNCS mismatch: {built_values} != {actual}")
            probe.snapshots["checksums_after"] = values
            return f"status=0x{status:X}, saved={saved}, actual={actual}"

        probe.run("Read Check Sum (after build)", "BCNCS", read_checksums_after)

        def final_compare() -> str:
            final = capture_config_from_session(session)
            if final.capture_warnings:
                raise RuntimeError(
                    "incomplete final capture: "
                    + "; ".join(final.capture_warnings[:8])
                )
            initial_payload = _config_payload(original)
            final_payload = _config_payload(final)
            probe.snapshots["final_config"] = final_payload
            if not equivalent(initial_payload, final_payload):
                changed = [
                    key for key in initial_payload
                    if key not in final_payload
                    or not equivalent(initial_payload[key], final_payload[key])
                ]
                raise AssertionError(f"final setup differs in fields: {changed}")
            return "complete final capture matches the initial setup"

        probe.run("Final full setup verification", "GET compare", final_compare)
    finally:
        if original is not None and not restored:
            try:
                errors = apply_config_to_session(original, session)
                if errors:
                    raise RuntimeError("; ".join(errors[:8]))
                probe.add(
                    "Emergency original-file restore",
                    "XML/SET",
                    "PASS",
                    "original setup restored in finally",
                )
            except Exception as exc:
                probe.add(
                    "Emergency original-file restore",
                    "XML/SET",
                    "RESTORE_FAILED",
                    f"{type(exc).__name__}: {exc}",
                )
        probe.save()
    return probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("serial", "mock"), default="serial")
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument(
        "--complete-pending-digital-trace",
        action="store_true",
        help="run DASTA and wait for ready if DGTAS is nonzero before setup writes",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "hardware_probe_results",
    )
    args = parser.parse_args()

    session = (
        open_mock(readonly=False)
        if args.backend == "mock"
        else open_serial(
            args.port, args.baudrate, readonly=False, timeout=3.0
        )
    )
    probe: SaveLoadProbe | None = None
    try:
        version = session.open()
        print(f"CONNECTED {version} readonly={session.readonly}", flush=True)
        print(f"CAPABILITIES {session.get_global_system_constants()}", flush=True)
        trace_status = session.get_digital_trace_status()
        if trace_status and _integer(trace_status[0]) != 0:
            if not args.complete_pending_digital_trace:
                raise RuntimeError(
                    "digital trace is busy; rerun with "
                    "--complete-pending-digital-trace after operator authorization"
                )
            print(
                "TRACE CLEANUP " + complete_pending_digital_trace(session),
                flush=True,
            )
        probe = run_probe(session, args.output_dir)
    except Exception as exc:
        print(f"FAIL probe-level verification :: {type(exc).__name__}: {exc}", flush=True)
    finally:
        session.close()
        if probe is not None:
            probe.save()

    if probe is None:
        return 2
    summary: dict[str, int] = {}
    for result in probe.results:
        summary[result.status] = summary.get(result.status, 0) + 1
    print(f"SUMMARY {json.dumps(summary, sort_keys=True)}", flush=True)
    bad = {"FAIL", "RESTORE_FAILED"}
    return 2 if any(result.status in bad for result in probe.results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
