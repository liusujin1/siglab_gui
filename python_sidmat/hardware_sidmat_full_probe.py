"""Safe, reversible live-controller regression probe for Sidmat.

The probe covers every writable operation exposed by Sidmat's measurement
controller facade.  It snapshots the current controller state, writes the
same values back, verifies readback, and restores the original state in a
finally block.  It intentionally does not toggle loops, change excitation,
clear/save NVRAM, or issue actuator commands.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Callable

from python_sidmat.backend.controller import Controller
from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.excitation import ExcitationParameters
from python_sidmat.measurement.trace import TraceParameters

from python_samba.protocol.commands import FilterStage


def _io(value: IOType) -> list[int]:
    return [int(item) for item in value.encode()]


def _exc(value: ExcitationParameters) -> dict[str, object]:
    return {"type": int(value.type), "params": [float(item) for item in value.params]}


def _stage(value: FilterStage) -> dict[str, object]:
    return {
        "axis": int(value.axis),
        "stage": int(value.stage),
        "type": int(value.filter_type),
        "params": [float(item) for item in value.params],
    }


def _trace(value: TraceParameters) -> dict[str, object]:
    return {
        "wire": list(value.encode()),
        "ch0": _io(value.trace_ch0),
        "ch1": _io(value.trace_ch1),
        "undersamples": int(value.undersamples),
        "samples": int(value.no_samples),
        "filter_flag": int(value.trace_filter_flag),
    }


def _same_float(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-8, abs_tol=1e-9)


def _same_exc(left: ExcitationParameters, right: ExcitationParameters) -> bool:
    return (
        int(left.type) == int(right.type)
        and len(left.params) == len(right.params)
        and all(_same_float(a, b) for a, b in zip(left.params, right.params))
    )


def _same_stage(left: FilterStage, right: FilterStage) -> bool:
    return (
        int(left.axis) == int(right.axis)
        and int(left.stage) == int(right.stage)
        and int(left.filter_type) == int(right.filter_type)
        and len(left.params) == len(right.params)
        and all(_same_float(a, b) for a, b in zip(left.params, right.params))
    )


def _snapshot(controller: Controller) -> dict[str, object]:
    return {
        "trace": controller.get_trace(),
        "excitation": controller.get_excitation(),
        "offset": controller.get_excitation_offset(),
        "inject": controller.get_noise_inject(),
        "diagnostic": controller.get_diagnostic_outputs(),
        "filter_usage": controller.get_noise_filter_usage(),
        "filter_stages": [controller.get_noise_filter_stage(i) for i in range(4)],
        "axis_states": controller.get_axis_loop_states(),
    }


def _restore(controller: Controller, state: dict[str, object]) -> None:
    """Restore every state item, even when a preceding restore item fails."""
    errors: list[str] = []

    def attempt(name: str, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # pragma: no cover - exercised on hardware
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    trace = state["trace"]
    exc = state["excitation"]
    inject = state["inject"]
    diag0, diag1 = state["diagnostic"]
    stages = state["filter_stages"]
    axis_states = state["axis_states"]
    attempt("trace", lambda: controller.set_trace(trace))
    attempt("excitation", lambda: controller.set_excitation(exc))
    attempt("noise injection", lambda: controller.set_noise_inject(inject))
    attempt("excitation offset", lambda: controller.set_excitation_offset(state["offset"]))
    attempt("diagnostic outputs", lambda: controller.set_diagnostic_outputs(diag0, diag1))
    for index, stage in enumerate(stages):
        attempt(f"filter stage {index}", lambda stage=stage: controller.set_noise_filter_stage(stage))
    attempt("filter usage", lambda: controller.set_noise_filter_usage(state["filter_usage"]))
    for index, enabled in enumerate(axis_states):
        attempt(
            f"axis {index}",
            lambda index=index, enabled=enabled: controller.set_axis_loop_state(index, enabled),
        )
    if errors:
        raise RuntimeError("; ".join(errors))


def _wait_trace(controller: Controller, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    status = controller.get_trace_status()
    while status != 0:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"DGTAS did not finish: {status}")
        time.sleep(0.2)
        status = controller.get_trace_status()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    report: dict[str, object] = {"port": port, "baud": baud, "steps": []}
    failures: list[str] = []

    def record(name: str, status: str, detail: object) -> None:
        report["steps"].append({"name": name, "status": status, "detail": detail})
        print(f"{status:4} {name}: {detail}", flush=True)

    def run(name: str, callback: Callable[[], object]) -> object | None:
        try:
            detail = callback()
        except Exception as exc:  # Continue with the other independent checks.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            record(name, "FAIL", f"{type(exc).__name__}: {exc}")
            return None
        record(name, "PASS", detail)
        return detail

    controller: Controller | None = None
    state: dict[str, object] | None = None
    try:
        controller = Controller.connect(port, baudrate=baud, readonly=False)
        version = controller.get_version()
        record(
            "connect/read identity",
            "PASS",
            {
                "firmware": f"{version.major}.{version.minor}.{version.patch}",
                "library": version.lib,
                "sample_frequency": controller.get_sample_frequency(),
            },
        )
        state = _snapshot(controller)
        report["snapshot"] = {
            "trace": _trace(state["trace"]),
            "excitation": _exc(state["excitation"]),
            "offset": float(state["offset"]),
            "inject": _io(state["inject"]),
            "diagnostic": [_io(state["diagnostic"][0]), _io(state["diagnostic"][1])],
            "filter_usage": state["filter_usage"],
            "filter_stages": [_stage(item) for item in state["filter_stages"]],
            "axis_states": [bool(item) for item in state["axis_states"]],
        }
        record("read all Sidmat state", "PASS", report["snapshot"])

        original_trace: TraceParameters = state["trace"]
        original_exc: ExcitationParameters = state["excitation"]
        original_inject: IOType = state["inject"]
        original_diag0, original_diag1 = state["diagnostic"]
        original_stages: list[FilterStage] = state["filter_stages"]
        original_usage: str = state["filter_usage"]

        run("write/read trace configuration", lambda: _write_trace(controller, original_trace))
        run("write/read excitation parameters", lambda: _write_excitation(controller, original_exc))
        run("write/read excitation offset", lambda: _write_offset(controller, state["offset"]))
        run("write/read noise injection point", lambda: _write_inject(controller, original_inject))
        run("write/read diagnostic outputs", lambda: _write_diagnostic(controller, original_diag0, original_diag1))
        run("write/read noise filter usage", lambda: _write_usage(controller, original_usage))
        run("toggle/read noise filter usage N/F", lambda: _toggle_usage(controller, original_usage))
        for index, original_stage in enumerate(original_stages):
            run(
                f"write/read noise filter stage {index}",
                lambda index=index, original_stage=original_stage: _write_stage(
                    controller, index, original_stage
                ),
            )

        run("write/read all 12 axis loop states", lambda: _write_axes(controller, state["axis_states"]))

        run("direct DASTA/DGTAS/DGTBV path", lambda: _direct_trace(controller, original_trace))
    except BaseException as exc:
        failures.append(f"probe setup: {type(exc).__name__}: {exc}")
        record("probe setup", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if controller is not None and state is not None and controller.connected:
            try:
                _restore(controller, state)
                restored = _snapshot(controller)
                if not _state_equal(state, restored):
                    raise AssertionError("post-restore controller state differs from snapshot")
                record("restore exact controller state", "PASS", "all Sidmat state readbacks match")
            except BaseException as exc:
                failures.append(f"restore exact controller state: {type(exc).__name__}: {exc}")
                record("restore exact controller state", "FAIL", f"{type(exc).__name__}: {exc}")
        if controller is not None:
            controller.close()

    report["failure_count"] = len(failures)
    if failures:
        report["failures"] = failures
    report_path = Path.cwd() / "hardware_sidmat_full_probe_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT {report_path}", flush=True)
    return 2 if failures else 0


def _write_trace(controller: Controller, expected: TraceParameters) -> dict[str, object]:
    controller.set_trace(expected)
    got = controller.get_trace()
    if got.encode() != expected.encode():
        raise AssertionError(f"trace mismatch: {got.encode()} != {expected.encode()}")
    return _trace(got)


def _write_excitation(controller: Controller, expected: ExcitationParameters) -> dict[str, object]:
    controller.set_excitation(expected)
    got = controller.get_excitation()
    if not _same_exc(got, expected):
        raise AssertionError(f"excitation mismatch: {_exc(got)} != {_exc(expected)}")
    return _exc(got)


def _write_offset(controller: Controller, expected: float) -> float:
    controller.set_excitation_offset(expected)
    got = controller.get_excitation_offset()
    if not _same_float(got, expected):
        raise AssertionError(f"offset mismatch: {got} != {expected}")
    return got


def _write_inject(controller: Controller, expected: IOType) -> list[int]:
    controller.set_noise_inject(expected)
    got = controller.get_noise_inject()
    if got.encode() != expected.encode():
        raise AssertionError(f"inject mismatch: {got.encode()} != {expected.encode()}")
    return _io(got)


def _write_diagnostic(controller: Controller, expected0: IOType, expected1: IOType) -> list[list[int]]:
    controller.set_diagnostic_outputs(expected0, expected1)
    got0, got1 = controller.get_diagnostic_outputs()
    if got0.encode() != expected0.encode() or got1.encode() != expected1.encode():
        raise AssertionError(
            f"diagnostic mismatch: {_io(got0)}, {_io(got1)} != {_io(expected0)}, {_io(expected1)}"
        )
    return [_io(got0), _io(got1)]


def _write_usage(controller: Controller, expected: str) -> str:
    controller.set_noise_filter_usage(expected)
    got = controller.get_noise_filter_usage()
    if str(got).strip().upper() != str(expected).strip().upper():
        raise AssertionError(f"filter usage mismatch: {got!r} != {expected!r}")
    return got


def _toggle_usage(controller: Controller, original: str) -> dict[str, str]:
    controller.set_noise_filter_usage("N")
    on_value = str(controller.get_noise_filter_usage()).strip()
    controller.set_noise_filter_usage("F")
    off_value = str(controller.get_noise_filter_usage()).strip()
    controller.set_noise_filter_usage(original)
    if on_value.upper() != "N" or off_value.upper() != "F":
        raise AssertionError(f"N/F readback mismatch: on={on_value!r}, off={off_value!r}")
    return {"on": on_value, "off": off_value}


def _write_stage(controller: Controller, index: int, expected: FilterStage) -> dict[str, object]:
    controller.set_noise_filter_stage(expected)
    got = controller.get_noise_filter_stage(index)
    if not _same_stage(got, expected):
        raise AssertionError(f"stage mismatch: {_stage(got)} != {_stage(expected)}")
    return _stage(got)


def _write_axes(controller: Controller, expected: list[bool]) -> list[bool]:
    for index, enabled in enumerate(expected):
        controller.set_axis_loop_state(index, bool(enabled))
    got = controller.get_axis_loop_states()
    if list(got) != list(expected):
        raise AssertionError(f"axis states mismatch: {got} != {expected}")
    return [bool(item) for item in got]


def _direct_trace(controller: Controller, original: TraceParameters) -> dict[str, object]:
    small = TraceParameters(
        trace_ch0=original.trace_ch0,
        trace_ch1=original.trace_ch1,
        undersamples=original.undersamples,
        no_samples=32,
        trace_filter_flag=original.trace_filter_flag,
        average_number=1,
    )
    controller.set_trace(small)
    controller.start_trace()
    _wait_trace(controller)
    ch0, ch1 = controller.get_trace_buffer(0)
    if not ch0 or len(ch0) != len(ch1):
        raise AssertionError(f"DGTBV returned {len(ch0)} / {len(ch1)} samples")
    if not all(math.isfinite(value) for value in ch0 + ch1):
        raise AssertionError("DGTBV returned a non-finite sample")
    return {"samples_in_first_chunk": len(ch0), "finite": True}


def _state_equal(left: dict[str, object], right: dict[str, object]) -> bool:
    return (
        left["trace"].encode() == right["trace"].encode()
        and _same_exc(left["excitation"], right["excitation"])
        and _same_float(left["offset"], right["offset"])
        and left["inject"].encode() == right["inject"].encode()
        and all(
            a.encode() == b.encode()
            for a, b in zip(left["diagnostic"], right["diagnostic"])
        )
        and str(left["filter_usage"]).upper() == str(right["filter_usage"]).upper()
        and all(
            _same_stage(a, b)
            for a, b in zip(left["filter_stages"], right["filter_stages"])
        )
        and list(left["axis_states"]) == list(right["axis_states"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
