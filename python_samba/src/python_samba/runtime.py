"""Portable-suite runtime configuration shared by SAMBA and SIDMAT.

This module deliberately has no Qt dependency so command-line tools, frozen
executables, and tests can use the same configuration rules.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


_CONFIG_ENVIRONMENT = {
    "backend": "SIGLAB_BACKEND",
    "server": "SIGLAB_SERVER_ENDPOINT",
    "serial_port": "SIGLAB_SERIAL_PORT",
    "baudrate": "SIGLAB_BAUDRATE",
    "comm_server_exe": "SIGLAB_COMM_SERVER_EXE",
    "token_file": "SIGLAB_TOKEN_FILE",
    "data_root": "SIGLAB_DATA_ROOT",
    "local_data_root": "SIGLAB_LOCAL_DATA_ROOT",
}


def configure_qt_dpi_environment() -> None:
    """Set the shared Qt DPI policy before either GUI imports PySide6.

    Samba and SIDMAT use pixel-authored layouts and perform their own
    monitor-aware font/geometry calculations.  Letting Qt apply a second
    automatic scale makes those fixed dimensions grow twice on some Windows
    machines.  ``setdefault`` deliberately preserves an operator's explicit
    Qt scale override; the applications still clamp their geometry and font
    size when such an override is present.
    """

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")


def runtime_asset_path(name: str) -> Path | None:
    """Locate a bundled or source-tree asset without requiring Qt.

    PyInstaller onedir places shared assets under ``_internal/assets`` and a
    one-file server extracts them under ``sys._MEIPASS``.  Source checkouts
    keep the canonical artwork in ``packaging/assets``.  Returning ``None``
    is intentional so a missing optional icon can fall back to Qt's standard
    icon instead of preventing the application from starting.
    """

    filename = Path(name).name
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / filename)
    executable_dir = Path(sys.executable).resolve().parent
    candidates.append(executable_dir / "assets" / filename)
    source_root = Path(__file__).resolve().parents[3]
    candidates.append(source_root / "packaging" / "assets" / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def expand_runtime_path(value: str | os.PathLike[str]) -> Path:
    """Expand environment variables/user markers and return an absolute path."""

    expanded = os.path.expandvars(os.path.expanduser(os.fspath(value)))
    return Path(expanded).resolve()


def apply_suite_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate a suite profile and expose its values through process env vars."""

    config_path = expand_runtime_path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load suite configuration {config_path}: {exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("schema", 0)) != 1:
        raise ValueError("suite configuration must be a schema-1 JSON object")
    backend = str(payload.get("backend", "server")).strip().lower()
    if backend not in {"server", "serial", "mock"}:
        raise ValueError(f"unsupported suite backend: {backend!r}")
    payload["backend"] = backend
    try:
        baudrate = int(payload.get("baudrate", 57600))
    except (TypeError, ValueError) as exc:
        raise ValueError("suite baudrate must be an integer") from exc
    if baudrate <= 0:
        raise ValueError("suite baudrate must be positive")
    payload["baudrate"] = baudrate

    os.environ["SIGLAB_SUITE_CONFIG"] = str(config_path)
    for key, environment_name in _CONFIG_ENVIRONMENT.items():
        value = payload.get(key)
        if value is None or str(value).strip() == "":
            continue
        text = str(value)
        if key in {"comm_server_exe", "token_file", "data_root", "local_data_root"}:
            text = str(expand_runtime_path(text))
        os.environ[environment_name] = text

    os.environ.setdefault(
        "SIGLAB_LOCAL_DATA_ROOT",
        str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SigLabSuite"),
    )
    return payload


def consume_runtime_arguments(argv: Sequence[str] | None = None) -> list[str]:
    """Consume suite-only arguments before Qt or an application parser sees them."""

    original = list(sys.argv if argv is None else argv)
    if not original:
        original = [""]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--suite-config")
    parser.add_argument("--comm-server-exe")
    parser.add_argument("--comm-server-autostart-smoke")
    parser.add_argument("--smoke-test", action="store_true")
    parsed, remaining = parser.parse_known_args(original[1:])
    if parsed.suite_config:
        apply_suite_config(parsed.suite_config)
    if parsed.comm_server_exe:
        os.environ["SIGLAB_COMM_SERVER_EXE"] = str(
            expand_runtime_path(parsed.comm_server_exe)
        )
    if parsed.smoke_test:
        os.environ["SIGLAB_SMOKE_TEST"] = "1"
    if parsed.comm_server_autostart_smoke:
        os.environ["SIGLAB_COMM_SERVER_AUTOSTART_SMOKE"] = str(
            parsed.comm_server_autostart_smoke
        )
    return [original[0], *remaining]


def run_comm_server_autostart_smoke(endpoint: str) -> None:
    """Exercise the frozen Connect auto-start path without real hardware."""

    from python_samba.commserver.protocol import is_loopback_host, parse_endpoint
    from python_samba.transport.comm_server import (
        CommServerConfig,
        CommServerTransport,
        request_server_shutdown,
    )
    from python_samba.transport.serial_port import TransportError

    host, _ = parse_endpoint(endpoint)
    if not is_loopback_host(host):
        raise ValueError("auto-start smoke endpoint must be loopback")
    transport = CommServerTransport(
        CommServerConfig(
            port="SIGLAB_SMOKE_NO_SERIAL",
            endpoint=endpoint,
            auto_start=True,
            client_name="siglab-frozen-autostart-smoke",
            connect_timeout=2.0,
            comm_server_exe=os.environ.get("SIGLAB_COMM_SERVER_EXE") or None,
        )
    )
    try:
        try:
            transport.open()
        except TransportError:
            # Expected: the test-only serial port cannot be attached.  The
            # important assertion is that the packaged server was started and
            # now accepts a protocol-level shutdown request.
            pass
        finally:
            transport.close()
        request_server_shutdown(endpoint, timeout=5.0)
    except BaseException:
        transport.close()
        raise


def runtime_data_root() -> Path:
    configured = os.environ.get("SIGLAB_DATA_ROOT")
    if configured:
        return expand_runtime_path(configured)
    return Path.home() / "Documents" / "SigLabSuite"


def runtime_local_data_root() -> Path | None:
    configured = os.environ.get("SIGLAB_LOCAL_DATA_ROOT")
    return expand_runtime_path(configured) if configured else None


def connection_environment() -> dict[str, str]:
    """Return only explicitly configured connection defaults."""

    return {
        key: value
        for key, environment_name in _CONFIG_ENVIRONMENT.items()
        if (value := os.environ.get(environment_name)) is not None
    }
