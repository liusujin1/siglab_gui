from __future__ import annotations

import pytest

from python_samba.commserver.server import CommunicationServer
from python_samba.transport.mock import MockTransport
from python_sidmat.backend.controller import Controller
from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.engine import MeasurementEngine
from python_sidmat.measurement.trace import TraceParameters


def _endpoint(server: CommunicationServer) -> str:
    host, port = server.addresses[0]
    return f"{host}:{port}"


def _server(tmp_path) -> CommunicationServer:
    return CommunicationServer(
        ("127.0.0.1", 0),
        transport_factory=lambda config: MockTransport(),
        log_file=tmp_path / "server.log",
    ).start()


def test_controller_measurement_uses_shared_server(tmp_path) -> None:
    server = _server(tmp_path)
    controller = Controller.connect_server(
        "COM1", server=_endpoint(server), auto_start=False, readonly=False
    )
    try:
        trace = TraceParameters(
            trace_ch0=IOType(0, 0, 0),
            trace_ch1=IOType(0, 1, 0),
            no_samples=64,
            average_number=1,
        )
        controller.set_trace(trace)
        raw = MeasurementEngine(
            controller, trace, sample_frequency=controller.get_sample_frequency()
        ).run()
        assert len(raw.channel(0)) == 64
        assert len(raw.channel(1)) == 64
        assert controller.session.info.backend == "server"
        assert controller.session.transport.status()["attached_count"] == 1
    finally:
        controller.close()
        server.stop()


def test_sidmat_cli_uses_shared_server(tmp_path, capsys) -> None:
    from python_sidmat.cli import main

    server = _server(tmp_path)
    try:
        assert main(
            [
                "--backend",
                "server",
                "--server",
                _endpoint(server),
                "--no-auto-start",
                "--port",
                "COM1",
                "--length",
                "64",
                "--avg",
                "1",
            ]
        ) == 0
        output = capsys.readouterr().out
        assert "Controller connected" in output
        assert "Acquired 64 samples" in output
    finally:
        server.stop()


def test_sidmat_gui_connects_through_shared_server(tmp_path) -> None:
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets
    from python_sidmat.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings("python_samba", "SiDiMaT")
    keys = [
        "Connection/Backend",
        "Connection/Port",
        "Connection/Baudrate",
        "Connection/Server",
    ]
    saved = {
        key: settings.value(key) if settings.contains(key) else None for key in keys
    }
    server = _server(tmp_path)
    window = MainWindow()
    try:
        window.backend_cbx.setCurrentText("server")
        window.port_cbx.setCurrentText("COM1")
        window.server_endpoint_edit.setText(_endpoint(server))
        window.connect_btn.setChecked(True)
        assert window.controller is not None and window.controller.connected
        assert window.controller.session.info.backend == "server"
        assert "via server" in window.status_lbl.text()
        assert "clients 1" in window.server_status_lbl.text()
    finally:
        window._disconnect()
        window.close()
        server.stop()
        for key, value in saved.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        app.processEvents()
