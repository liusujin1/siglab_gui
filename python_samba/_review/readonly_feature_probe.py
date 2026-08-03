"""Read-only controller capability probe used during source alignment review."""

from __future__ import annotations

import sys

from python_samba.services.session import open_serial


READ_ONLY_RAW_COMMANDS = ("PGGIX", "CGPOX", "PGGIV")


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    session = open_serial(port, baudrate, readonly=True, timeout=3.0)
    try:
        print("VERSION", session.open())
        assert session.readonly
        for label, getter in (
            ("FIRMWARE_CONFIG_INFO", session.get_firmware_config_info),
            ("SYSTEM_CONSTANTS", session.get_global_system_constants),
            ("LOOP_STATUS", session.get_loop_status),
            ("POS_PNEUM_DIGITAL_STATUS", session.get_pos_pneum_digital_status),
            ("SWITCH_SIGNAL", session.get_switch_signal),
            ("SWITCH_STATUS", session.get_switch_status),
            ("PERFORMANCE_MONITOR", session.get_performance_monitor),
            ("PERFORMANCE_STATUS", session.get_performance_status),
            ("FF_INPUTS", session.get_ff_inputs),
            ("PROXIMITY_OFFSETS_6", session.get_proximity_offsets),
            (
                "PROXIMITY_INPUT_VALUES_6",
                lambda: session.get_proximity_input_values(6),
            ),
            ("PNEUMATIC_AXES_STATUS", session.get_pneumatic_axes_status),
            (
                "PNEUMATIC_HEIGHTS_VALVES",
                session.get_pneumatic_heights_valves,
            ),
        ):
            try:
                print(label, getter())
            except Exception as exc:  # capability probes must continue
                print(label, "ERROR", type(exc).__name__, str(exc))
        for mnemonic in READ_ONLY_RAW_COMMANDS:
            try:
                response = session.raw_command(mnemonic)
                print(
                    mnemonic,
                    "ok=", response.ok,
                    "status=", response.status_code,
                    "mnemonic=", response.mnemonic,
                    "reject=", response.reject_reason,
                    "data=", list(response.data_tokens),
                )
            except Exception as exc:  # capability probes must continue
                print(mnemonic, "ERROR", type(exc).__name__, str(exc))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
