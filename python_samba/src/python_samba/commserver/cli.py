"""Command-line and optional tray host for the shared communication server."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from pathlib import Path

from python_samba.commserver.protocol import (
    DEFAULT_ENDPOINT,
    default_log_file,
    parse_endpoint,
)
from python_samba.commserver.discovery import (
    DiscoveryAnnouncer,
    load_or_create_server_id,
)
from python_samba.commserver.server import CommunicationServer, ServerAlreadyRunning


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python-samba-comm-server")
    parser.add_argument(
        "--listen",
        action="append",
        default=[],
        metavar="HOST:PORT",
        help="listen endpoint; repeat for loopback/LAN/Tailscale as needed",
    )
    parser.add_argument("--port", default=None, help="preferred physical COM port")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--tray", action="store_true")
    parser.add_argument(
        "--auth",
        choices=["auto", "token", "trusted-network"],
        default="auto",
        help="remote authentication policy (trusted-network is intentionally unauthenticated)",
    )
    parser.add_argument("--discover", action="store_true", help="advertise on LAN/Tailscale UDP discovery")
    parser.add_argument("--name", default=None, help="name shown in discovery results")
    return parser


def _listen_endpoints(values: list[str]) -> list[tuple[str, int]]:
    if not values:
        return [parse_endpoint(DEFAULT_ENDPOINT)]
    endpoints = [parse_endpoint(value) for value in values]
    if any(host.startswith("100.") for host, _ in endpoints) and not any(
        host in {"127.0.0.1", "localhost", "::1"} for host, _ in endpoints
    ):
        remote_port = endpoints[0][1]
        endpoints.insert(0, ("127.0.0.1", remote_port))
    return endpoints


def _run_tray(server: CommunicationServer, log_file: Path) -> int:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        server.serve_forever()
        return 0

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    icon = app.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
    tray = QtWidgets.QSystemTrayIcon(icon)
    menu = QtWidgets.QMenu()
    status_action = menu.addAction("Show Communication Server Status")
    restart_action = menu.addAction("Reopen Serial Port")
    log_action = menu.addAction("Open Log")
    menu.addSeparator()
    exit_action = menu.addAction("Exit Communication Server")
    tray.setContextMenu(menu)

    def show_status() -> None:
        state = server.status()
        serial = state["serial"]
        clients = state["clients"]
        lines = [
            f"Serial: {serial['port'] or '—'} @ {serial['baudrate'] or '—'} "
            f"({'open' if serial['open'] else 'closed'})",
            f"Clients: {state['client_count']} (attached {state['attached_count']})",
            f"Queue: {state['queue_length']}",
            f"Last command: {state['last_command'] or '—'}",
            f"Last duration: {state['last_duration_ms'] or 0:.1f} ms",
            f"Last error: {state['last_error'] or '—'}",
            "",
        ]
        lines.extend(
            f"{item['name']}  PID={item['pid']}  {item['peer']}"
            for item in clients
        )
        QtWidgets.QMessageBox.information(None, "Communication Server", "\n".join(lines))

    def restart() -> None:
        try:
            server.restart_serial()
        except BaseException as exc:
            QtWidgets.QMessageBox.critical(None, "Communication Server", str(exc))

    def open_log() -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(log_file))

    def quit_server() -> None:
        server.stop()
        tray.hide()
        app.quit()

    status_action.triggered.connect(show_status)
    restart_action.triggered.connect(restart)
    log_action.triggered.connect(open_log)
    exit_action.triggered.connect(quit_server)
    tray.activated.connect(
        lambda reason: show_status()
        if reason == QtWidgets.QSystemTrayIcon.Trigger
        else None
    )
    timer = QtCore.QTimer()

    def refresh_tooltip() -> None:
        state = server.status()
        serial = state["serial"]
        tray.setToolTip(
            "python_samba Communication Server\n"
            f"{serial['port'] or 'No serial'} @ {serial['baudrate'] or '—'} — "
            f"{'online' if serial['open'] else 'idle'}\n"
            f"Clients {state['client_count']} · Queue {state['queue_length']}"
        )

    timer.timeout.connect(refresh_tooltip)
    timer.start(1000)
    refresh_tooltip()
    tray.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    log_file = Path(args.log_file) if args.log_file else default_log_file()
    listeners = _listen_endpoints(args.listen)
    if args.discover and args.auth == "trusted-network" and not args.listen:
        listeners = [("0.0.0.0", listeners[0][1])]
    server = CommunicationServer(
        listeners,
        token=args.token,
        token_file=args.token_file,
        auth_mode=args.auth,
        log_file=log_file,
        log_level=args.log_level,
        preferred_port=args.port,
        preferred_baudrate=args.baud,
    )
    announcer: DiscoveryAnnouncer | None = None
    try:
        server.start()
    except ServerAlreadyRunning:
        # Auto-start races are expected: the process that won the port remains
        # the singleton and every losing helper exits quietly.
        return 0
    try:
        if args.discover:
            announcer = DiscoveryAnnouncer(
                server.status,
                server_id=load_or_create_server_id(),
                name=args.name or f"{socket.gethostname()} – SAMBA Controller",
                tcp_port=server.addresses[0][1],
                auth_mode=server.auth_mode,
            )
            announcer.start()
            if announcer.last_error:
                raise RuntimeError(
                    f"UDP discovery failed on port {announcer.discovery_port}: "
                    f"{announcer.last_error}"
                )
        if args.tray:
            return _run_tray(server, log_file)
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if announcer is not None:
            announcer.stop()
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
