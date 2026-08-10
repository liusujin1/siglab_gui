from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from python_samba.commserver.discovery import DiscoveredServer
from python_samba.commserver.app import _is_usb_serial_port
from python_samba.commserver.protocol import PROTOCOL_VERSION
from python_samba.ui.server_discovery import ServerDiscoveryDialog


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _server() -> DiscoveredServer:
    return DiscoveredServer(
        server_id="gui-server",
        name="GUI Controller",
        hostname="remote-pc",
        host="192.168.1.40",
        tcp_port=47619,
        serial_port="COM9",
        baudrate=57600,
        state="ready",
        client_count=1,
        auth_mode="trusted-network",
        protocol=PROTOCOL_VERSION,
        network="lan",
        latency_ms=3.5,
        last_seen=__import__("time").time(),
        addresses=("192.168.1.40:47619",),
    )


def test_discovery_dialog_accepts_ready_selected_server() -> None:
    app = _app()
    dialog = ServerDiscoveryDialog(last_server_id="gui-server")
    try:
        dialog._upsert_server(_server())
        dialog.table.selectRow(0)
        assert dialog.connect_button.isEnabled()
        dialog._connect_selected()
        assert dialog.selected_server is not None
        assert dialog.selected_server.endpoint == "192.168.1.40:47619"
    finally:
        dialog.close()
        app.processEvents()


def test_samba_discovery_selection_populates_and_connects(monkeypatch) -> None:
    app = _app()
    from python_samba.ui import main_window as module

    monkeypatch.setattr(module, "choose_communication_server", lambda *_a, **_k: _server())
    settings: dict[str, object] = {}

    class Settings:
        def value(self, key, default=None):
            return settings.get(key, default)

        def setValue(self, key, value):
            settings[key] = value

    backend = QtWidgets.QComboBox()
    backend.addItems(["server", "serial", "mock"])
    baud = QtWidgets.QComboBox()
    for value in (19200, 38400, 57600):
        baud.addItem(str(value), value)
    called: list[bool] = []
    dummy = SimpleNamespace(
        _connection_settings=Settings(),
        backend=backend,
        server_endpoint=QtWidgets.QLineEdit(),
        port=QtWidgets.QLineEdit(),
        baud=baud,
        on_connect=lambda: called.append(True),
    )
    module.MainWindow.on_discover_server(dummy)
    assert backend.currentText() == "server"
    assert dummy.server_endpoint.text() == "192.168.1.40:47619"
    assert dummy.port.text() == "COM9"
    assert baud.currentText() == "57600"
    assert settings["Connection/ServerId"] == "gui-server"
    assert called == [True]
    app.processEvents()


def test_portable_server_window_constructs_without_starting(tmp_path, monkeypatch) -> None:
    app = _app()
    from python_samba.commserver import app as server_app

    monkeypatch.setattr(server_app, "firewall_rules_installed", lambda: True)
    window = server_app.CommunicationServerWindow(
        config_path=tmp_path / "server.json",
        auto_start=False,
        offer_firewall=False,
    )
    try:
        assert window.server is None
        assert window.auth_combo.currentData() == "trusted-network"
        assert "Communication Server" in window.windowTitle()
    finally:
        window._allow_close = True
        window.close()
        app.processEvents()


def test_server_app_only_auto_selects_usb_serial_ports() -> None:
    assert _is_usb_serial_port(SimpleNamespace(vid=0x0403, hwid="FTDIBUS\\VID_0403"))
    assert _is_usb_serial_port(SimpleNamespace(vid=None, hwid="USB VID:PID=1234:5678"))
    assert not _is_usb_serial_port(SimpleNamespace(vid=None, hwid="ACPI\\PNP0501"))
