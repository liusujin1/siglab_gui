from __future__ import annotations

import json
from pathlib import Path

import pytest

from python_samba.commserver.protocol import (
    default_config_file,
    default_identity_file,
    default_log_file,
    default_token_file,
)
from python_samba.runtime import apply_suite_config, consume_runtime_arguments
from python_samba.services.monitor_lease import default_recovery_directory
from python_samba.transport import comm_server
from python_samba.transport.comm_server import (
    CommServerConfig,
    CommServerTransport,
    _ServerUnavailable,
    resolve_local_server_executable,
)
from python_samba.transport.serial_port import TransportError


def test_suite_config_sets_shared_environment(tmp_path, monkeypatch) -> None:
    for name in (
        "SIGLAB_BACKEND",
        "SIGLAB_SERVER_ENDPOINT",
        "SIGLAB_SERIAL_PORT",
        "SIGLAB_BAUDRATE",
        "SIGLAB_DATA_ROOT",
        "SIGLAB_LOCAL_DATA_ROOT",
        "SIGLAB_COMM_SERVER_EXE",
    ):
        monkeypatch.delenv(name, raising=False)
    profile = tmp_path / "test local.json"
    profile.write_text(
        json.dumps(
            {
                "schema": 1,
                "profile": "test-local",
                "backend": "server",
                "server": "127.0.0.1:47619",
                "serial_port": "COM9",
                "baudrate": 57600,
                "data_root": str(tmp_path / "user data"),
            }
        ),
        encoding="utf-8",
    )

    payload = apply_suite_config(profile)

    assert payload["profile"] == "test-local"
    assert comm_server.os.environ["SIGLAB_BACKEND"] == "server"
    assert comm_server.os.environ["SIGLAB_SERIAL_PORT"] == "COM9"
    assert Path(comm_server.os.environ["SIGLAB_DATA_ROOT"]).is_absolute()
    assert comm_server.os.environ["SIGLAB_LOCAL_DATA_ROOT"].endswith("SigLabSuite")


def test_suite_runtime_paths_are_partitioned(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIGLAB_LOCAL_DATA_ROOT", str(tmp_path))

    assert default_log_file() == tmp_path / "logs" / "communication_server.log"
    assert default_config_file() == tmp_path / "config" / "communication_server.json"
    assert default_identity_file() == tmp_path / "config" / "communication_server.id"
    assert default_token_file() == tmp_path / "config" / "communication_server.token"
    assert default_recovery_directory() == (
        tmp_path / "recovery" / "monitor_slot_recovery"
    )


def test_runtime_arguments_are_removed_before_qt(tmp_path, monkeypatch) -> None:
    server = tmp_path / "PythonSambaCommServer.exe"
    server.write_bytes(b"MZ")
    monkeypatch.delenv("SIGLAB_COMM_SERVER_EXE", raising=False)

    cleaned = consume_runtime_arguments(
        [
            "Samba.exe", "--comm-server-exe", str(server), "--smoke-test",
            "--comm-server-autostart-smoke", "127.0.0.1:47777", "--qt-arg",
        ]
    )

    assert cleaned == ["Samba.exe", "--qt-arg"]
    assert comm_server.os.environ["SIGLAB_COMM_SERVER_EXE"] == str(server.resolve())
    assert comm_server.os.environ["SIGLAB_SMOKE_TEST"] == "1"
    assert comm_server.os.environ["SIGLAB_COMM_SERVER_AUTOSTART_SMOKE"] == (
        "127.0.0.1:47777"
    )


def test_frozen_server_resolution_search_order(tmp_path, monkeypatch) -> None:
    samba_dir = tmp_path / "apps" / "Samba"
    server_dir = tmp_path / "apps" / "CommServer"
    samba_dir.mkdir(parents=True)
    server_dir.mkdir(parents=True)
    samba = samba_dir / "Samba.exe"
    server = server_dir / "PythonSambaCommServer.exe"
    samba.write_bytes(b"MZ")
    server.write_bytes(b"MZ")
    monkeypatch.setattr(comm_server.sys, "executable", str(samba))
    monkeypatch.setattr(comm_server.sys, "frozen", True, raising=False)
    monkeypatch.delenv("SIGLAB_COMM_SERVER_EXE", raising=False)

    assert resolve_local_server_executable() == server.resolve()

    explicit = tmp_path / "explicit server.exe"
    explicit.write_bytes(b"MZ")
    assert resolve_local_server_executable(explicit) == explicit.resolve()


def test_frozen_missing_server_never_falls_back_to_python(tmp_path, monkeypatch) -> None:
    samba = tmp_path / "apps" / "Samba" / "Samba.exe"
    samba.parent.mkdir(parents=True)
    samba.write_bytes(b"MZ")
    monkeypatch.setattr(comm_server.sys, "executable", str(samba))
    monkeypatch.setattr(comm_server.sys, "frozen", True, raising=False)
    monkeypatch.delenv("SIGLAB_COMM_SERVER_EXE", raising=False)

    with pytest.raises(TransportError, match="Communication Server component is missing"):
        resolve_local_server_executable()


def test_packaged_server_launcher_uses_component_executable(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "PythonSambaCommServer.exe"
    executable.write_bytes(b"MZ")
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        comm_server.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((list(command), kwargs)),
    )
    transport = CommServerTransport(
        CommServerConfig(
            port="COM1",
            endpoint="127.0.0.1:47619",
            comm_server_exe=executable,
        )
    )

    transport._start_local_server()

    assert calls[0][0] == [
        str(executable.resolve()),
        "--tray",
        "--listen",
        "127.0.0.1:47619",
    ]
    assert "-m" not in calls[0][0]


def test_remote_endpoint_failure_never_starts_local_server(monkeypatch) -> None:
    transport = CommServerTransport(
        CommServerConfig(
            port="COM1",
            endpoint="192.0.2.10:47619",
            auto_start=True,
            connect_timeout=0.1,
        )
    )
    monkeypatch.setattr(
        transport, "_connect_once", lambda **_kwargs: (_ for _ in ()).throw(
            _ServerUnavailable("refused")
        )
    )
    starts: list[bool] = []
    monkeypatch.setattr(transport, "_start_local_server", lambda: starts.append(True))

    with pytest.raises(TransportError, match="192.0.2.10:47619"):
        transport.open()
    assert starts == []


def test_loopback_autostart_is_attempted_only_once(monkeypatch) -> None:
    transport = CommServerTransport(
        CommServerConfig(
            port="COM1",
            endpoint="127.0.0.1:47619",
            auto_start=True,
            connect_timeout=0.45,
        )
    )
    attempts: list[bool] = []
    starts: list[bool] = []

    def unavailable(**_kwargs) -> None:
        attempts.append(True)
        raise _ServerUnavailable("refused")

    monkeypatch.setattr(transport, "_connect_once", unavailable)
    monkeypatch.setattr(transport, "_start_local_server", lambda: starts.append(True))

    with pytest.raises(TransportError):
        transport.open()
    assert len(attempts) >= 2
    assert starts == [True]
