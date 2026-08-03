"""Authorized live-controller probe for state-changing UI action ports.

Run only after the operator has confirmed that motion and calibration/reset
actions are permitted.  Restorable controller values are snapshotted and
written back immediately after each action.
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

from python_samba.services.session import open_serial


@dataclass
class ActionResult:
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


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right)
        )
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None and right_number is not None:
        return math.isclose(left_number, right_number, rel_tol=1e-5, abs_tol=1e-7)
    return str(left).strip().upper() == str(right).strip().upper()


def _integer(value: Any) -> int:
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 16)


def _event_trace_params_disabled(params: list[str]) -> bool:
    """Firmware zero sentinel: DSSET is accepted but logging stays stopped."""
    return (
        len(params) >= 3
        and _integer(params[1]) == 0
        and _integer(params[2]) == 0
    )


class ActionProbe:
    def __init__(self, session, output_dir: Path) -> None:
        self.session = session
        self.output_dir = output_dir
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results: list[ActionResult] = []
        self.snapshots: dict[str, Any] = {}

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"hardware_action_ports_{self.stamp}_report.json"
        payload = {
            "timestamp": self.stamp,
            "results": [asdict(result) for result in self.results],
            "snapshots": self.snapshots,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def run(self, name: str, command: str, action: Callable[[], str]) -> None:
        try:
            detail = action()
            result = ActionResult(name, command, "PASS", detail)
        except Exception as exc:
            result = ActionResult(
                name, command, "FAIL", f"{type(exc).__name__}: {exc}"
            )
        self.results.append(result)
        print(
            f"{result.status:4} {command:10} {name}"
            + (f" :: {result.detail}" if result.detail else ""),
            flush=True,
        )
        self.save()
        # A failed write may have been accepted by the controller even when
        # its acknowledgement/read-back was lost.  Do not continue issuing
        # unrelated state-changing commands after that point.
        if result.status == "FAIL":
            raise RuntimeError(
                f"aborting after failed action {command}: {result.detail}"
            )

    def capture_restorable(self, proximity_count: int) -> dict[str, Any]:
        return {
            "power_supply": self.session.get_power_supply_parameters(),
            "digital_trace_setup": self.session.get_digital_trace_info(),
            "event_trace_params": self.session.get_event_trace_params(),
            "event_trace_info": self.session.get_event_trace_info(),
            "proximity_offsets": self.session.get_proximity_offsets(proximity_count),
            "pneumatic_valve_offsets": self.session.get_pneumatic_valve_offsets(),
            "ff_source_0_gains": self.session.get_ff_gains(0),
            "pff_axis_0_source_0_gains": self.session.get_pff_gains(0, 0),
        }

    def verify_restored(self, before: dict[str, Any], proximity_count: int) -> None:
        after = self.capture_restorable(proximity_count)
        # Power-supply counters/maxima and event tracing information are action
        # state, not restorable configuration.  Compare only their settings.
        comparisons = {
            "power_supply_settings": (before["power_supply"][:2], after["power_supply"][:2]),
            "digital_trace_setup": (
                before["digital_trace_setup"], after["digital_trace_setup"]
            ),
            "event_trace_params": (
                before["event_trace_params"], after["event_trace_params"]
            ),
            "event_trace_state": (
                [before["event_trace_info"][0], before["event_trace_info"][2]],
                [after["event_trace_info"][0], after["event_trace_info"][2]],
            ),
            "proximity_offsets": (
                before["proximity_offsets"], after["proximity_offsets"]
            ),
            "pneumatic_valve_offsets": (
                before["pneumatic_valve_offsets"],
                after["pneumatic_valve_offsets"],
            ),
            "ff_source_0_gains": (
                before["ff_source_0_gains"], after["ff_source_0_gains"]
            ),
            "pff_axis_0_source_0_gains": (
                before["pff_axis_0_source_0_gains"],
                after["pff_axis_0_source_0_gains"],
            ),
        }
        changed = [
            key for key, values in comparisons.items()
            if not _equivalent(values[0], values[1])
        ]
        self.snapshots["final"] = after
        self.snapshots["restorable_changed"] = changed
        if changed:
            raise AssertionError(f"restorable values changed: {changed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument(
        "--allow-delete-event-traces",
        action="store_true",
        help="allow DSSET start even when DGETI reports saved traces",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parent / "hardware_probe_results",
    )
    args = parser.parse_args()

    session = open_serial(args.port, args.baudrate, readonly=False, timeout=3.0)
    probe = ActionProbe(session, args.output_dir)
    try:
        version = session.open()
        constants = session.get_global_system_constants()
        proximity_count = 8 if int(constants[5]) == 8 else 6
        print(f"CONNECTED {version} readonly={session.readonly}", flush=True)
        before = probe.capture_restorable(proximity_count)
        probe.snapshots["initial"] = before
        probe.save()

        def reset_power_counter() -> str:
            values = session.get_power_supply_parameters()
            session.set_power_supply_parameters(values[0], values[1], 1, 0)
            current = session.get_power_supply_parameters()
            if not _equivalent(values[:2], current[:2]):
                raise AssertionError("power-supply settings changed")
            return f"counter {values[7] if len(values) > 7 else '?'} -> " \
                   f"{current[7] if len(current) > 7 else '?'}"

        probe.run("Reset power-supply error counter", "LSPSL", reset_power_counter)

        def reset_power_maximum() -> str:
            values = session.get_power_supply_parameters()
            session.set_power_supply_parameters(values[0], values[1], 0, 1)
            current = session.get_power_supply_parameters()
            if not _equivalent(values[:2], current[:2]):
                raise AssertionError("power-supply settings changed")
            return f"maximum {values[6] if len(values) > 6 else '?'} -> " \
                   f"{current[6] if len(current) > 6 else '?'}"

        probe.run("Reset power-supply maximum", "LSPSL", reset_power_maximum)

        def start_digital_trace() -> str:
            setup = session.get_digital_trace_info()
            result = session.start_digital_trace()
            if result and _integer(result[0]) != 0:
                raise AssertionError(f"DASTA error code {result}")
            sample_frequency = max(1.0, session.get_sample_frequency())
            expected_seconds = 0.0
            if len(setup) >= 8:
                expected_seconds = (
                    max(1, int(float(setup[6])))
                    * max(1, int(float(setup[7])))
                    / sample_frequency
                )
            deadline = time.monotonic() + min(60.0, max(15.0, expected_seconds + 10.0))
            status: list[str] = []
            while time.monotonic() < deadline:
                status = session.get_digital_trace_status()
                if status and _integer(status[0]) == 0:
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError(f"digital trace did not finish; status={status}")
            values = session.get_digital_trace_buffer(0)
            if not values:
                raise AssertionError("DGTBV returned an empty response")
            if len(values) % 2:
                raise AssertionError(
                    f"DGTBV returned an odd flattened payload: {len(values)}"
                )
            sample_count = len(values) // 2
            if not 0 <= sample_count <= 16:
                raise AssertionError(f"invalid DGTBV sample count {sample_count}")
            return (
                f"DASTA={result}, status={status}, "
                f"buffer_samples={sample_count}"
            )

        probe.run("Start/read digital trace", "DASTA/DGTBV", start_digital_trace)

        def adopt_proximity_offsets() -> str:
            original = session.get_proximity_offsets(proximity_count)
            try:
                session.use_current_proximity_offsets(proximity_count)
                adopted = session.get_proximity_offsets(proximity_count)
            finally:
                session.set_proximity_offsets(original)
            restored = session.get_proximity_offsets(proximity_count)
            if not _equivalent(original, restored):
                raise AssertionError("proximity offsets were not restored")
            return f"adopted={adopted}; restored=true"

        probe.run(
            "Use current proximity values as offsets",
            "CAUCO" if proximity_count == 6 else "CAUCX",
            adopt_proximity_offsets,
        )

        def move_pneumatic(action: int) -> str:
            session.move_pneumatic(action)
            return f"action={action}, status={session.get_pneumatic_axes_status()}"

        probe.run("Move pneumatic system up", "PAMOV 1", lambda: move_pneumatic(1))
        probe.run("Move pneumatic system down", "PAMOV 2", lambda: move_pneumatic(2))

        def adopt_pressure_offsets(condition: int) -> str:
            original = session.get_pneumatic_valve_offsets()
            try:
                session.use_current_pressure_offsets(condition)
                adopted = session.get_pneumatic_valve_offsets()
            finally:
                session.set_pneumatic_valve_offsets(original)
            restored = session.get_pneumatic_valve_offsets()
            if not _equivalent(original, restored):
                raise AssertionError("pneumatic valve offsets were not restored")
            return f"condition={condition}, adopted={adopted}; restored=true"

        probe.run(
            "Use live pressure as up offsets", "PAUCO 1",
            lambda: adopt_pressure_offsets(1),
        )
        probe.run(
            "Use live pressure as down offsets", "PAUCO 2",
            lambda: adopt_pressure_offsets(2),
        )

        def reset_ff_fir() -> str:
            original = session.get_ff_gains(0)
            try:
                session.reset_ff_fir(0)
                reset = session.get_ff_gains(0)
                if any(abs(value) > 1e-7 for value in reset):
                    raise AssertionError(f"FARFF gains not zero: {reset}")
            finally:
                session.set_ff_gains(0, *original)
            if not _equivalent(original, session.get_ff_gains(0)):
                raise AssertionError("FF gains were not restored")
            return "source=0, six axes reset and restored"

        probe.run("Reset Feed Forward FIR", "FARFF", reset_ff_fir)

        def reset_pff_fir() -> str:
            original = session.get_pff_gains(0, 0)
            try:
                session.reset_pff_fir(0, 0)
                reset = session.get_pff_gains(0, 0)
                if any(abs(value) > 1e-7 for value in reset):
                    raise AssertionError(f"FARPF gains not zero: {reset}")
            finally:
                session.set_pff_gains(0, 0, original)
            if not _equivalent(original, session.get_pff_gains(0, 0)):
                raise AssertionError("PFF gains were not restored")
            return "axis=0 source=0 reset and restored"

        probe.run("Reset Pneumatic FF FIR", "FARPF", reset_pff_fir)

        def start_stop_event_trace() -> str:
            before_info = session.get_event_trace_info()
            trace_params = session.get_event_trace_params()
            disabled_params = _event_trace_params_disabled(trace_params)
            if len(before_info) < 3:
                raise AssertionError(
                    f"unexpected event trace information: {before_info}"
                )
            if _integer(before_info[0]) != 0:
                raise RuntimeError(
                    "event tracing was already active; refusing to change its state"
                )
            if _integer(before_info[2]) != 0 and not args.allow_delete_event_traces:
                raise RuntimeError(
                    "saved event traces exist; DSSET start would delete them"
                )
            started_info: list[str] = []
            try:
                session.start_stop_event_tracing(1)
                started_info = session.get_event_trace_info()
                started_status = (
                    _integer(started_info[0]) if started_info else -1
                )
                if disabled_params:
                    if started_status != 0:
                        raise AssertionError(
                            "disabled event trace unexpectedly became active: "
                            f"{started_info}"
                        )
                elif started_status != 1:
                    raise AssertionError(f"event trace did not start: {started_info}")
            finally:
                session.start_stop_event_tracing(0)
            stopped_info = session.get_event_trace_info()
            if not stopped_info or _integer(stopped_info[0]) != 0:
                raise AssertionError(f"event trace did not stop: {stopped_info}")
            mode = "disabled parameters; start/stop acknowledged" \
                if disabled_params else "configured parameters; state toggled"
            return (
                f"before={before_info}, started={started_info}, "
                f"stopped={stopped_info}; {mode}"
            )

        probe.run("Start/stop event tracing", "DSSET 1/0", start_stop_event_trace)

        probe.verify_restored(before, proximity_count)
        probe.results.append(
            ActionResult(
                "Final restorable-value verification", "GET compare", "PASS",
                "all restorable settings match the action preflight snapshot",
            )
        )
    except Exception as exc:
        probe.results.append(
            ActionResult(
                "Probe-level verification", "", "FAIL",
                f"{type(exc).__name__}: {exc}",
            )
        )
        print(f"FAIL probe-level verification :: {type(exc).__name__}: {exc}")
    finally:
        session.close()
        probe.save()

    summary: dict[str, int] = {}
    for result in probe.results:
        summary[result.status] = summary.get(result.status, 0) + 1
    print(f"SUMMARY {json.dumps(summary, sort_keys=True)}", flush=True)
    return 2 if summary.get("FAIL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
