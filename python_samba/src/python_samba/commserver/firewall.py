"""Scoped Windows Firewall helpers for the portable server application."""

from __future__ import annotations

import ctypes
import os
import subprocess

TCP_RULE_NAME = "Python SAMBA Communication Server TCP"
UDP_RULE_NAME = "Python SAMBA Communication Server Discovery UDP"


def _powershell() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def firewall_rules_installed() -> bool:
    if os.name != "nt":
        return True
    names = (TCP_RULE_NAME, UDP_RULE_NAME)
    for name in names:
        script = (
            f"if (Get-NetFirewallRule -DisplayName '{name}' "
            "-ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
        )
        try:
            result = subprocess.run(
                [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
    return True


def request_firewall_rules() -> bool:
    """Request elevation and add inbound rules scoped to LAN and Tailscale."""

    if os.name != "nt":
        return True
    script = _firewall_install_script()
    parameters = f'-NoProfile -ExecutionPolicy Bypass -Command "{script}"'
    try:
        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", _powershell(), parameters, None, 0
        )
    except (AttributeError, OSError):
        return False
    return int(result) > 32


def _firewall_install_script() -> str:
    remote = "@('LocalSubnet','100.64.0.0/10')"
    return (
        "$ErrorActionPreference='Stop';"
        f"Get-NetFirewallRule -DisplayName '{TCP_RULE_NAME}' -ErrorAction SilentlyContinue | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue;"
        "New-NetFirewallRule "
        f"-DisplayName '{TCP_RULE_NAME}' -Direction Inbound -Action Allow "
        f"-Protocol TCP -LocalPort 47619 -Profile Any -RemoteAddress {remote} | Out-Null;"
        f"Get-NetFirewallRule -DisplayName '{UDP_RULE_NAME}' -ErrorAction SilentlyContinue | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue;"
        "New-NetFirewallRule "
        f"-DisplayName '{UDP_RULE_NAME}' -Direction Inbound -Action Allow "
        f"-Protocol UDP -LocalPort 47620 -Profile Any -RemoteAddress {remote} | Out-Null"
    )


__all__ = [
    "TCP_RULE_NAME",
    "UDP_RULE_NAME",
    "firewall_rules_installed",
    "request_firewall_rules",
]
