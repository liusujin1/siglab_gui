"""Live-controller probe for clickable loop and matrix controls.

Every controller mutation follows the same sequence: capture the original
value, toggle one documented bit, read it back, restore the original value,
and verify the restoration.  The probe is intended for an authorized bench
controller and never saves values to NVRAM.
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

from python_samba.services.session import open_mock, open_serial


@dataclass
class ProbeResult:
    name: str
    command: str
    status: str
    detail: str = ""


def _decimal_int(value: Any) -> int:
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        if any(character in "abcdefABCDEF" for character in text):
            return int(text, 16)
        return int(float(text))


def _hex_mask(value: Any) -> int:
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 16)


def _same_number(left: Any, right: Any) -> bool:
    try:
        return math.isclose(
            float(left), float(right), rel_tol=1e-6, abs_tol=1e-8
        )
    except (TypeError, ValueError):
        return str(left).strip().upper() == str(right).strip().upper()


class ControlSwitchProbe:
    def __init__(self, session, output_dir: Path, settle_seconds: float) -> None:
        self.session = session
        self.output_dir = output_dir
        self.settle_seconds = max(0.0, float(settle_seconds))
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results: list[ProbeResult] = []
        self.snapshots: dict[str, Any] = {}

    def save(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"hardware_control_switches_{self.stamp}_report.json"
        path.write_text(
            json.dumps(
                {
                    "timestamp": self.stamp,
                    "results": [asdict(item) for item in self.results],
                    "snapshots": self.snapshots,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def capture(self) -> dict[str, Any]:
        loop = self.session.get_loop_status()
        return {
            "loop": {"individual": loop.individual, "system": loop.system},
            "pos_pneum_digital": self.session.get_pos_pneum_digital_status(),
            "pneumatic_setpoint": self.session.get_pneumatic_setpoint_status(),
            "switch_conditions": self.session.get_switch_conditions(),
            "switch_status": self.session.get_switch_status(),
            "controller_config": self.session.get_controller_config(),
            "ff_source_0": self.session.get_ff_parameters(0),
            "pff_source_0": self.session.get_pff_parameters(0),
            "ff_error_filters": [
                asdict(self.session.get_ff_filter(axis, stage))
                for axis in range(6)
                for stage in (6, 7)
            ],
        }

    def run(self, name: str, command: str, action: Callable[[], str]) -> None:
        try:
            detail = action()
            result = ProbeResult(name, command, "PASS", detail)
        except Exception as exc:
            result = ProbeResult(
                name, command, "FAIL", f"{type(exc).__name__}: {exc}"
            )
        self.results.append(result)
        print(
            f"{result.status:4} {command:12} {name}"
            + (f" :: {result.detail}" if result.detail else ""),
            flush=True,
        )
        self.save()
        if result.status == "FAIL":
            raise RuntimeError(
                f"aborting after failed control {command}: {result.detail}"
            )

    def toggle_system_loop(self, name: str, bit: int) -> str:
        before = self.session.get_loop_status()
        target = before.system ^ bit
        toggled = None
        try:
            self.session.set_loop_status(before.individual, target)
            time.sleep(self.settle_seconds)
            toggled = self.session.get_loop_status()
            if bool(toggled.system & bit) == bool(before.system & bit):
                raise AssertionError(
                    f"target bit 0x{bit:X} did not change: 0x{before.system:X} -> "
                    f"0x{toggled.system:X}"
                )
        finally:
            self.session.set_loop_status(before.individual, before.system)
        restored = self.session.get_loop_status()
        if restored.individual != before.individual or restored.system != before.system:
            raise AssertionError(
                f"loop status was not restored: {asdict(before)} -> {asdict(restored)}"
            )
        return (
            f"{name} bit 0x{bit:X}: 0x{before.system:X} -> "
            f"0x{toggled.system:X} -> 0x{restored.system:X}"
        )

    def toggle_velocity_individual(self, name: str, bit: int) -> str:
        before = self.session.get_loop_status()
        target = before.individual ^ bit
        changed = None
        try:
            self.session.set_loop_status(target, before.system)
            time.sleep(self.settle_seconds)
            changed = self.session.get_loop_status()
            if changed.individual != target or changed.system != before.system:
                raise AssertionError(
                    f"velocity individual bit 0x{bit:X} did not read back: "
                    f"{asdict(changed)}"
                )
        finally:
            self.session.set_loop_status(before.individual, before.system)
        restored = self.session.get_loop_status()
        if restored != before:
            raise AssertionError(
                f"velocity individual status was not restored: "
                f"{asdict(before)} -> {asdict(restored)}"
            )
        return (
            f"{name} bit 0x{bit:X}: 0x{before.individual:X} -> "
            f"0x{changed.individual:X} -> 0x{restored.individual:X}"
        )

    def toggle_pneumatic_individual(self, name: str, bit: int) -> str:
        before = self.session.get_pos_pneum_digital_status()
        position, pneumatic, _digital_in, _digital_out = before
        target = pneumatic ^ bit
        changed = None
        try:
            self.session.set_pos_pneum_individual_loop_status(position, target)
            time.sleep(self.settle_seconds)
            changed = self.session.get_pos_pneum_digital_status()
            if changed[0] != position or changed[1] != target:
                raise AssertionError(
                    f"pneumatic individual bit 0x{bit:X} did not read back: "
                    f"{changed}"
                )
        finally:
            self.session.set_pos_pneum_individual_loop_status(
                position, pneumatic
            )
        restored = self.session.get_pos_pneum_digital_status()
        if restored[:2] != before[:2]:
            raise AssertionError(
                f"pneumatic individual status was not restored: "
                f"{before} -> {restored}"
            )
        return (
            f"{name} bit 0x{bit:X}: 0x{pneumatic:X} -> "
            f"0x{changed[1]:X} -> 0x{restored[1]:X}"
        )

    def toggle_position_individual(self, name: str, bit: int) -> str:
        before = self.session.get_pos_pneum_digital_status()
        position, pneumatic, _digital_in, _digital_out = before
        target = position ^ bit
        changed = None
        try:
            self.session.set_pos_pneum_individual_loop_status(
                target, pneumatic
            )
            time.sleep(self.settle_seconds)
            changed = self.session.get_pos_pneum_digital_status()
            if changed[0] != target or changed[1] != pneumatic:
                raise AssertionError(
                    f"position individual bit 0x{bit:X} did not read back: "
                    f"{changed}"
                )
        finally:
            self.session.set_pos_pneum_individual_loop_status(
                position, pneumatic
            )
        restored = self.session.get_pos_pneum_digital_status()
        if restored[:2] != before[:2]:
            raise AssertionError(
                f"position individual status was not restored: "
                f"{before} -> {restored}"
            )
        return (
            f"{name} bit 0x{bit:X}: 0x{position:X} -> "
            f"0x{changed[0]:X} -> 0x{restored[0]:X}"
        )

    def set_switch_mode(self, name: str, target: int) -> str:
        before = self.session.get_switch_conditions()
        if len(before) < 4:
            raise AssertionError(f"BGOCD returned too few fields: {before}")
        original = _decimal_int(before[3])
        changed = None
        try:
            self.session.set_switch_conditions(
                before[0], before[1], before[2], target
            )
            changed = self.session.get_switch_conditions()
            if len(changed) < 4 or _decimal_int(changed[3]) != target:
                raise AssertionError(
                    f"SwitchConfig did not read back as {target}: {changed}"
                )
        finally:
            self.session.set_switch_conditions(
                before[0], before[1], before[2], original
            )
        restored = self.session.get_switch_conditions()
        if len(restored) < 4 or any(
            not _same_number(left, right)
            for left, right in zip(before[:4], restored[:4])
        ):
            raise AssertionError(
                f"switch conditions were not restored: {restored}"
            )
        return f"{name}: {original} -> {_decimal_int(changed[3])} -> {original}"

    def same_write_ff_error_filters(self) -> str:
        checked = 0
        for axis in range(6):
            for stage in (6, 7):
                before = self.session.get_ff_filter(axis, stage)
                self.session.set_ff_filter(before)
                after = self.session.get_ff_filter(axis, stage)
                if after.filter_type != before.filter_type or any(
                    not _same_number(left, right)
                    for left, right in zip(before.params, after.params)
                ):
                    raise AssertionError(
                        f"FF error filter changed at axis={axis}, "
                        f"stage={stage}: {before} -> {after}"
                    )
                checked += 1
        return f"read/same-write/read passed for {checked} error filters"

    def toggle_pneumatic_setpoint(self) -> str:
        before = int(self.session.get_pneumatic_setpoint_status())
        target = 0 if before else 1
        changed = None
        try:
            self.session.set_pneumatic_setpoint_status(target)
            time.sleep(self.settle_seconds)
            changed = int(self.session.get_pneumatic_setpoint_status())
            if changed != target:
                raise AssertionError(
                    f"PSPSS did not read back {target}: {changed}"
                )
        finally:
            self.session.set_pneumatic_setpoint_status(before)
        restored = int(self.session.get_pneumatic_setpoint_status())
        if restored != before:
            raise AssertionError(
                f"pneumatic setpoint status was not restored: "
                f"{before} -> {restored}"
            )
        return f"PGPSS/PSPSS: {before} -> {changed} -> {restored}"

    def toggle_manual_loop(self, name: str, bit: int) -> str:
        before = self.session.get_switch_conditions()
        if len(before) < 4:
            raise AssertionError(f"BGOCD returned too few fields: {before}")
        original_config = _decimal_int(before[3])
        if original_config & 0x01:
            raise RuntimeError(
                "automatic loop switching is enabled; legacy UI intentionally "
                "disables manual Velocity/Position buttons"
            )
        target = original_config ^ bit
        status_before = self.session.get_switch_status()
        changed = None
        try:
            self.session.set_switch_conditions(before[0], before[1], before[2], target)
            time.sleep(self.settle_seconds)
            changed = self.session.get_switch_conditions()
            if len(changed) < 4 or _decimal_int(changed[3]) != target:
                raise AssertionError(
                    f"SwitchConfig did not read back as {target}: {changed}"
                )
        finally:
            self.session.set_switch_conditions(
                before[0], before[1], before[2], original_config
            )
        restored = self.session.get_switch_conditions()
        if len(restored) < 4 or any(
            not _same_number(a, b) for a, b in zip(before[:4], restored[:4])
        ):
            raise AssertionError(f"switch conditions were not restored: {restored}")
        status_after = self.session.get_switch_status()
        return (
            f"{name} config bit 0x{bit:X}: {original_config} -> {target} -> "
            f"{_decimal_int(restored[3])}; DGCSS {status_before} -> {status_after}"
        )

    def toggle_controller_config(self, name: str, bit: int) -> str:
        before = self.session.get_controller_config()
        if not before:
            raise AssertionError("NGEXL returned no configuration word")
        original = _decimal_int(before[0])
        target = original ^ bit
        changed = None
        try:
            self.session.set_controller_config(target)
            time.sleep(self.settle_seconds)
            changed = self.session.get_controller_config()
            if not changed or _decimal_int(changed[0]) != target:
                raise AssertionError(
                    f"NSEXL bit 0x{bit:X} did not read back: {changed}"
                )
        finally:
            self.session.set_controller_config(original)
        restored = self.session.get_controller_config()
        if not restored or _decimal_int(restored[0]) != original:
            raise AssertionError(
                f"controller configuration was not restored: {restored}"
            )
        return (
            f"{name} bit 0x{bit:X}: 0x{original:X} -> "
            f"0x{_decimal_int(changed[0]):X} -> 0x{_decimal_int(restored[0]):X}"
        )

    def toggle_ff_matrix(self, source: int, axis: int) -> str:
        before = self.session.get_ff_parameters(source)
        if len(before) < 3:
            raise AssertionError(f"FGFFP returned too few fields: {before}")
        original = _hex_mask(before[0])
        target = original ^ (1 << axis)
        changed = None
        try:
            self.session.set_ff_parameters(source, target, before[1], float(before[2]))
            changed = self.session.get_ff_parameters(source)
            if not changed or _hex_mask(changed[0]) != target:
                raise AssertionError(f"FF output mask did not change to 0x{target:X}: {changed}")
        finally:
            self.session.set_ff_parameters(
                source, original, before[1], float(before[2])
            )
        restored = self.session.get_ff_parameters(source)
        if _hex_mask(restored[0]) != original or any(
            not _same_number(a, b) for a, b in zip(before[1:3], restored[1:3])
        ):
            raise AssertionError(f"FF parameters were not restored: {restored}")
        return f"source={source} axis={axis}: 0x{original:X} -> 0x{target:X} -> 0x{original:X}"

    def toggle_pff_matrix(self, source: int, axis: int) -> str:
        before = self.session.get_pff_parameters(source)
        if len(before) < 2:
            raise AssertionError(f"FGPPF returned too few fields: {before}")
        original = _hex_mask(before[0])
        target = original ^ (1 << axis)
        changed = None
        try:
            self.session.set_pff_parameters(source, target, float(before[1]))
            changed = self.session.get_pff_parameters(source)
            if not changed or _hex_mask(changed[0]) != target:
                raise AssertionError(
                    f"PFF output mask did not change to 0x{target:X}: {changed}"
                )
        finally:
            self.session.set_pff_parameters(source, original, float(before[1]))
        restored = self.session.get_pff_parameters(source)
        if _hex_mask(restored[0]) != original or not _same_number(
            restored[1], before[1]
        ):
            raise AssertionError(f"PFF parameters were not restored: {restored}")
        return f"source={source} axis={axis}: 0x{original:X} -> 0x{target:X} -> 0x{original:X}"

    def verify_final(self, before: dict[str, Any]) -> None:
        after = self.capture()
        self.snapshots["final"] = after
        changed: list[str] = []
        if after["loop"] != before["loop"]:
            changed.append("loop")
        if after["pos_pneum_digital"][:2] != before["pos_pneum_digital"][:2]:
            changed.append("pos_pneum_digital")
        if after["pneumatic_setpoint"] != before["pneumatic_setpoint"]:
            changed.append("pneumatic_setpoint")
        if any(
            not _same_number(a, b)
            for a, b in zip(
                before["switch_conditions"][:4], after["switch_conditions"][:4]
            )
        ):
            changed.append("switch_conditions")
        if (
            not after["controller_config"]
            or not before["controller_config"]
            or _decimal_int(after["controller_config"][0])
            != _decimal_int(before["controller_config"][0])
        ):
            changed.append("controller_config")
        if _hex_mask(after["ff_source_0"][0]) != _hex_mask(before["ff_source_0"][0]):
            changed.append("ff_source_0.outputs")
        if any(
            not _same_number(a, b)
            for a, b in zip(before["ff_source_0"][1:], after["ff_source_0"][1:])
        ):
            changed.append("ff_source_0.parameters")
        if _hex_mask(after["pff_source_0"][0]) != _hex_mask(before["pff_source_0"][0]):
            changed.append("pff_source_0.outputs")
        if any(
            not _same_number(a, b)
            for a, b in zip(before["pff_source_0"][1:], after["pff_source_0"][1:])
        ):
            changed.append("pff_source_0.parameters")
        if after["ff_error_filters"] != before["ff_error_filters"]:
            changed.append("ff_error_filters")
        self.snapshots["restorable_changed"] = changed
        if changed:
            raise AssertionError(f"final configuration changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=0.2)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "hardware_probe_results",
    )
    args = parser.parse_args()

    session = (
        open_mock(readonly=False)
        if args.mock
        else open_serial(
            args.port, args.baudrate, readonly=False, timeout=3.0
        )
    )
    probe = ControlSwitchProbe(session, args.output_dir, args.settle_seconds)
    try:
        version = session.open()
        print(f"CONNECTED {version} readonly={session.readonly}", flush=True)
        constants = [str(value) for value in session.get_global_system_constants()]
        features = {value.upper() for value in constants[11:]}
        auto_loop_switch = "NALS" not in features
        before = probe.capture()
        probe.snapshots["initial"] = before
        probe.snapshots["capabilities"] = constants
        probe.save()

        for name, bit in (
            ("Overall", 0x00001),
            ("FF Adaptive", 0x00002),
            ("Feed Forward", 0x00004),
            ("Move Up At Startup", 0x00008),
            ("Pneumatic", 0x00040),
            ("FF UseFBForFF", 0x01000),
            ("Dither Compensation", 0x02000),
            ("Pneumatic FF", 0x04000),
            ("Pneumatic FF Adaptive", 0x08000),
            ("Reference Metrology", 0x20000),
        ):
            probe.run(
                f"Toggle {name} loop",
                f"BSSTS 0x{bit:X}",
                lambda name=name, bit=bit: probe.toggle_system_loop(name, bit),
            )
        for name, bit in (
            ("Xtrans", 0x01),
            ("Zrot", 0x02),
            ("Ytrans", 0x04),
            ("Ztrans", 0x08),
            ("Yrot", 0x10),
            ("Xrot", 0x20),
        ):
            probe.run(
                f"Toggle FF individual {name}",
                f"BSSTS IND 0x{bit:X}",
                lambda name=name, bit=bit: probe.toggle_velocity_individual(
                    name, bit
                ),
            )
        for name, bit in (
            ("Xrot", 0x01),
            ("Yrot", 0x02),
            ("Xtrans", 0x04),
            ("Ytrans", 0x08),
            ("Zrot", 0x10),
            ("Ztrans", 0x20),
        ):
            probe.run(
                f"Toggle Position individual {name}",
                f"BSSST POS 0x{bit:X}",
                lambda name=name, bit=bit: probe.toggle_position_individual(
                    name, bit
                ),
            )
        for name, bit in (
            ("Ztpneu", 0x01),
            ("Yrpneu", 0x02),
            ("Xrpneu", 0x04),
        ):
            probe.run(
                f"Toggle PFF/Pneumatic individual {name}",
                f"BSSST 0x{bit:X}",
                lambda name=name, bit=bit: probe.toggle_pneumatic_individual(
                    name, bit
                ),
            )
        probe.run(
            "Toggle Pneumatic Setpoint Status",
            "PSPSS",
            probe.toggle_pneumatic_setpoint,
        )
        for name, bit in (
            ("Velocity configured", 0x01),
            ("Position configured", 0x02),
            ("Pneumatic configured", 0x04),
            ("FF configured", 0x10),
            ("Stage FF configured", 0x20),
            ("Floor FF configured", 0x40),
            ("Pneumatic FF configured", 0x80),
        ):
            probe.run(
                f"Toggle {name}",
                f"NSEXL 0x{bit:X}",
                lambda name=name, bit=bit: probe.toggle_controller_config(name, bit),
            )
        for name, config in (
            ("Always Velocity", 1),
            ("Always Position", 2),
            ("Always Velocity+Position", 3),
        ):
            probe.run(
                f"Set Loop Switch {name}",
                f"BSOCD {config}",
                lambda name=name, config=config: probe.set_switch_mode(
                    name, config
                ),
            )
        if auto_loop_switch:
            for name, bit in (("Velocity", 0x20), ("Position", 0x40)):
                result = ProbeResult(
                    f"Toggle {name} loop",
                    f"BSOCD 0x{bit:X}",
                    "SKIP_CAPABILITY",
                    "BGGSC does not advertise NALS; legacy UI exposes this "
                    "control as a read-only RunningV/RunningP status lamp",
                )
                probe.results.append(result)
                print(
                    f"{result.status:15} {result.command:12} {result.name} :: "
                    f"{result.detail}",
                    flush=True,
                )
                probe.save()
        else:
            probe.run(
                "Toggle Velocity loop", "BSOCD 0x20",
                lambda: probe.toggle_manual_loop("Velocity", 0x20),
            )
            probe.run(
                "Toggle Position loop", "BSOCD 0x40",
                lambda: probe.toggle_manual_loop("Position", 0x40),
            )
        probe.run(
            "Toggle Feed Forward matrix lamp", "FSFFP",
            lambda: probe.toggle_ff_matrix(0, 0),
        )
        probe.run(
            "Toggle Pneumatic FF matrix lamp", "FSPPF",
            lambda: probe.toggle_pff_matrix(0, 0),
        )
        probe.run(
            "Read and same-write FF Error Path filters",
            "FGPFS/FSPFS",
            probe.same_write_ff_error_filters,
        )
        probe.verify_final(before)
        probe.results.append(
            ProbeResult(
                "Final restorable-value verification",
                "GET compare",
                "PASS",
                "loop, position/pneumatic individual status, setpoint status, "
                "switch, controller configuration, FF and PFF settings match "
                "the preflight snapshot",
            )
        )
    except Exception as exc:
        probe.results.append(
            ProbeResult(
                "Probe-level verification", "", "FAIL",
                f"{type(exc).__name__}: {exc}",
            )
        )
        print(f"FAIL probe-level verification :: {type(exc).__name__}: {exc}")
    finally:
        session.close()
        report = probe.save()

    summary: dict[str, int] = {}
    for result in probe.results:
        summary[result.status] = summary.get(result.status, 0) + 1
    print(f"REPORT {report}", flush=True)
    print(f"SUMMARY {json.dumps(summary, sort_keys=True)}", flush=True)
    return 2 if summary.get("FAIL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
