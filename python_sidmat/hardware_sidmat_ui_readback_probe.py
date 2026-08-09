"""Time each real-controller GET used during MainWindow._connect."""

from __future__ import annotations

import sys
import time

from python_sidmat.backend.controller import Controller


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    controller = Controller.connect(port, baudrate=baud, readonly=True)
    calls = [
        ("get_system_config", controller.get_system_config),
        ("get_output_limit", controller.get_output_limit),
        ("get_sample_frequency", controller.get_sample_frequency),
        ("get_trace", controller.get_trace),
        ("get_excitation", controller.get_excitation),
        ("get_noise_inject", controller.get_noise_inject),
        ("get_excitation_offset", controller.get_excitation_offset),
        ("get_diagnostic_outputs", controller.get_diagnostic_outputs),
        ("get_noise_filter_usage", controller.get_noise_filter_usage),
        ("get_noise_filter_stage_0", lambda: controller.get_noise_filter_stage(0)),
        ("get_noise_filter_stage_1", lambda: controller.get_noise_filter_stage(1)),
        ("get_noise_filter_stage_2", lambda: controller.get_noise_filter_stage(2)),
        ("get_noise_filter_stage_3", lambda: controller.get_noise_filter_stage(3)),
        ("get_axis_loop_states", controller.get_axis_loop_states),
    ]
    try:
        print(f"PASS connect firmware={controller.version}", flush=True)
        for name, callback in calls:
            started = time.monotonic()
            print(f"START {name}", flush=True)
            try:
                value = callback()
            except Exception as exc:
                print(
                    f"FAIL {name} {time.monotonic() - started:.3f}s "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            else:
                print(
                    f"PASS {name} {time.monotonic() - started:.3f}s {value!r}",
                    flush=True,
                )
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
