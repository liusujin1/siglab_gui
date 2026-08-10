from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from python_samba.commserver.discovery import DiscoveredServer
from python_samba.commserver.protocol import PROTOCOL_VERSION


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_sidmat_discovery_selection_populates_and_requests_connect(monkeypatch) -> None:
    app = _app()
    from python_sidmat.ui import main_window as module

    server = DiscoveredServer(
        server_id="sidmat-server",
        name="SIDMAT Controller",
        hostname="hardware-pc",
        host="100.64.0.42",
        tcp_port=47619,
        serial_port="COM1",
        baudrate=57600,
        state="ready",
        client_count=1,
        auth_mode="trusted-network",
        protocol=PROTOCOL_VERSION,
        network="tailscale",
        latency_ms=15.0,
        last_seen=time.time(),
        addresses=("100.64.0.42:47619",),
    )
    monkeypatch.setattr(module, "choose_communication_server", lambda *_a, **_k: server)
    settings: dict[str, object] = {}

    class Settings:
        def value(self, key, default=None):
            return settings.get(key, default)

        def setValue(self, key, value):
            settings[key] = value

    backend = QtWidgets.QComboBox()
    backend.addItems(["server", "serial", "mock"])
    port = QtWidgets.QComboBox()
    port.setEditable(True)
    baud = QtWidgets.QComboBox()
    baud.addItems(["38400", "57600", "115200"])
    connect = QtWidgets.QPushButton()
    connect.setCheckable(True)
    dummy = SimpleNamespace(
        _connection_settings=Settings(),
        backend_cbx=backend,
        server_endpoint_edit=QtWidgets.QLineEdit(),
        port_cbx=port,
        baud_cbx=baud,
        connect_btn=connect,
    )
    module.MainWindow._discover_server(dummy)
    assert backend.currentText() == "server"
    assert dummy.server_endpoint_edit.text() == "100.64.0.42:47619"
    assert port.currentText() == "COM1"
    assert baud.currentText() == "57600"
    assert connect.isChecked()
    assert settings["Connection/ServerId"] == "sidmat-server"
    app.processEvents()
