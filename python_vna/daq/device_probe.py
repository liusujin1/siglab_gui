from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from typing import Any

from python_vna.daq.base import BackendCapability, BackendDevice


def _device_from_payload(payload: dict[str, Any]) -> BackendDevice:
    capability_payload = payload.get("capability")
    capability = BackendCapability(
        **capability_payload
    ) if isinstance(capability_payload, dict) else BackendCapability()
    return BackendDevice(
        name=str(payload.get("name") or ""),
        product_type=str(payload.get("product_type") or ""),
        ai_channels=[str(value) for value in payload.get("ai_channels") or []],
        ao_channels=[str(value) for value in payload.get("ao_channels") or []],
        capability=capability,
    )


def probe_ni_devices_subprocess(timeout_s: float = 4.0) -> list[BackendDevice]:
    """Enumerate NI devices in a child process so driver hangs cannot freeze the UI."""

    if getattr(sys, "frozen", False):
        command = [sys.executable, "--probe-ni-devices-json"]
    else:
        command = [sys.executable, "-m", "python_vna.daq.device_probe", "--json"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(0.5, float(timeout_s)),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or f"Device probe failed with exit code {completed.returncode}")
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("Device probe returned invalid payload.")
    return [_device_from_payload(item) for item in payload if isinstance(item, dict)]


def discover_hardware() -> dict:
    """Run on a background worker; USB discovery never opens the device."""
    from python_vna.daq.usbdaq import enumerate_candidates

    result = {"ni_devices": [], "usb_devices": [], "errors": {}}
    for name, probe in (("usb", enumerate_candidates), ("ni", probe_ni_devices_subprocess)):
        try:
            devices = probe()
            # This acquisition UI targets the USB-4431. A reference USB-6000
            # on the same PC must not take priority over the vibration device.
            if name == "ni":
                devices = [d for d in devices if "4431" in d.product_type]
            result[f"{name}_devices"] = devices
        except Exception as exc:
            result["errors"][name] = str(exc)
    return result


def choose_hardware_backend(snapshot: dict, *, preferred: str = "", current: str = "",
                            usb_index: int = 0) -> str | None:
    available = set()
    if snapshot.get("ni_devices"):
        available.add("ni")
    if any(device["index"] == usb_index for device in snapshot.get("usb_devices", [])):
        available.add("usb")
    for choice in (preferred, current, "ni", "usb"):
        if choice in available:
            return choice
    return None


def _probe_ni_devices_json() -> str:
    from python_vna.daq.ni import NIDaqBackend

    return json.dumps([asdict(device) for device in NIDaqBackend().list_devices()])


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv != ["--json"]:
        print("Usage: python -m python_vna.daq.device_probe --json", file=sys.stderr)
        return 2
    try:
        print(_probe_ni_devices_json())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
