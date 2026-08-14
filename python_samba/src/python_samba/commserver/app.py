"""Portable PySide6 application for the discoverable Communication Server."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from python_samba.commserver.discovery import (
    DISCOVERY_PORT,
    DiscoveryAnnouncer,
    is_tailscale_host,
    is_trusted_peer_host,
    load_or_create_server_id,
)
from python_samba.commserver.firewall import (
    firewall_rules_installed,
    request_firewall_rules,
)
from python_samba.commserver.protocol import (
    DEFAULT_PORT,
    default_config_file,
    default_log_file,
    format_endpoint,
)
from python_samba.commserver.server import (
    CommunicationServer,
    ServerAlreadyRunning,
)
from python_samba.runtime import configure_qt_dpi_environment, runtime_asset_path

configure_qt_dpi_environment()

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 is required for the Communication Server app") from exc


CONFIG_FILE = default_config_file()


def _load_config(path: Path = CONFIG_FILE) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_config(value: dict[str, object], path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _serial_identity(port: object) -> str:
    serial_number = str(getattr(port, "serial_number", "") or "").strip()
    vid = getattr(port, "vid", None)
    pid = getattr(port, "pid", None)
    if serial_number or vid is not None or pid is not None:
        location = str(
            getattr(port, "location", "")
            or getattr(port, "hwid", "")
            or getattr(port, "device", "")
        )
        return f"{int(vid or 0):04X}:{int(pid or 0):04X}:{serial_number or location}"
    return str(getattr(port, "hwid", "") or getattr(port, "device", ""))


def _is_usb_serial_port(port: object) -> bool:
    """Return whether a port has a USB hardware identity suitable for auto-pick."""

    if getattr(port, "vid", None) is not None:
        return True
    hardware_id = str(getattr(port, "hwid", "") or "").upper()
    return "USB" in hardware_id or "FTDIBUS" in hardware_id


def _network_endpoints(tcp_port: int) -> list[str]:
    """List trusted, non-loopback IPv4 endpoints advertised by this host."""

    addresses: set[str] = set()
    try:
        import psutil  # type: ignore[import-not-found]

        for interface_addresses in psutil.net_if_addrs().values():
            for item in interface_addresses:
                host = str(item.address).split("%", 1)[0]
                if (
                    item.family == socket.AF_INET
                    and host != "127.0.0.1"
                    and is_trusted_peer_host(host)
                ):
                    addresses.add(host)
    except (ImportError, OSError):
        pass
    if not addresses:
        try:
            for host in socket.gethostbyname_ex(socket.gethostname())[2]:
                if host != "127.0.0.1" and is_trusted_peer_host(host):
                    addresses.add(host)
        except OSError:
            pass
    ordered = sorted(
        addresses,
        key=lambda host: (1 if is_tailscale_host(host) else 0, host),
    )
    return [format_endpoint(host, tcp_port) for host in ordered]


class CommunicationServerWindow(QtWidgets.QMainWindow):
    """Small controller-owner UI; closing it keeps the tray server alive."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        auto_start: bool = True,
        offer_firewall: bool = True,
        tcp_port: int = DEFAULT_PORT,
        discovery_port: int = DISCOVERY_PORT,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Python SAMBA Communication Server")
        self.resize(690, 520)
        self.setMinimumSize(620, 460)
        self._config_path = Path(config_path) if config_path else CONFIG_FILE
        self._tcp_port = int(tcp_port)
        self._discovery_port = int(discovery_port)
        self._config = _load_config(self._config_path)
        self._server: CommunicationServer | None = None
        self._announcer: DiscoveryAnnouncer | None = None
        self._allow_close = False
        self._service_name = str(
            self._config.get("name")
            or f"{socket.gethostname()} – SAMBA Controller"
        )
        self._dirty = False
        self._firewall_ok = firewall_rules_installed()

        self._build_ui()
        self._build_tray()
        self._refresh_ports()

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(1000)
        if auto_start:
            QtCore.QTimer.singleShot(0, self.start_server)
        if offer_firewall:
            QtCore.QTimer.singleShot(800, self._offer_firewall_setup)

    @property
    def server(self) -> CommunicationServer | None:
        return self._server

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QtWidgets.QLabel("Communication Server")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel(
            "唯一占用真机串口，并让 SAMBA 与 SIDMAT 通过局域网或 Tailscale 共享访问。"
        )
        subtitle.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(subtitle)

        self.warning_label = QtWidgets.QLabel(
            "可信网络免验证：同一局域网或 Tailnet 中的用户可以控制真机。"
        )
        self.warning_label.setObjectName("warning")
        self.warning_label.setWordWrap(True)
        outer.addWidget(self.warning_label)

        settings = QtWidgets.QGroupBox("Server Settings")
        form = QtWidgets.QGridLayout(settings)
        form.setColumnStretch(1, 1)

        self.name_edit = QtWidgets.QLineEdit(self._service_name)
        self.name_edit.setMaxLength(128)
        self.name_edit.textChanged.connect(self._settings_changed)
        form.addWidget(QtWidgets.QLabel("Service name"), 0, 0)
        form.addWidget(self.name_edit, 0, 1, 1, 3)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.currentTextChanged.connect(self._settings_changed)
        self.refresh_ports_button = QtWidgets.QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        form.addWidget(QtWidgets.QLabel("Controller port"), 1, 0)
        form.addWidget(self.port_combo, 1, 1)
        form.addWidget(self.refresh_ports_button, 1, 2)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["19200", "38400", "57600", "115200", "230400"])
        saved_baud = str(self._config.get("baudrate") or "57600")
        self.baud_combo.setCurrentText(
            saved_baud if self.baud_combo.findText(saved_baud) >= 0 else "57600"
        )
        self.baud_combo.currentTextChanged.connect(self._settings_changed)
        form.addWidget(QtWidgets.QLabel("Baud"), 1, 3)
        form.addWidget(self.baud_combo, 1, 4)

        self.auth_combo = QtWidgets.QComboBox()
        self.auth_combo.addItem("Trusted LAN / Tailscale (no token)", "trusted-network")
        self.auth_combo.addItem("Access token", "token")
        saved_auth = str(self._config.get("auth_mode") or "trusted-network")
        index = self.auth_combo.findData(saved_auth)
        self.auth_combo.setCurrentIndex(max(0, index))
        self.auth_combo.currentIndexChanged.connect(self._settings_changed)
        form.addWidget(QtWidgets.QLabel("Access"), 2, 0)
        form.addWidget(self.auth_combo, 2, 1, 1, 4)
        self._update_access_warning()
        outer.addWidget(settings)

        actions = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start Server")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_server)
        self.stop_button = QtWidgets.QPushButton("Stop Server")
        self.stop_button.clicked.connect(self.stop_server)
        self.firewall_button = QtWidgets.QPushButton("Configure Firewall")
        self.firewall_button.clicked.connect(self._configure_firewall)
        self.log_button = QtWidgets.QPushButton("Open Log")
        self.log_button.clicked.connect(self._open_log)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.firewall_button)
        actions.addWidget(self.log_button)
        actions.addStretch(1)
        outer.addLayout(actions)

        state_box = QtWidgets.QGroupBox("Live Status")
        state_layout = QtWidgets.QVBoxLayout(state_box)
        self.status_label = QtWidgets.QLabel("Starting…")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_layout.addWidget(self.status_label)
        outer.addWidget(state_box, 1)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #eef3f7; color: #243447;
                font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 12px; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #17324d; }
            QLabel#warning { background: #fff3cd; color: #7a4b00; border: 1px solid #e7c76d;
                border-radius: 5px; padding: 8px; font-weight: 600; }
            QGroupBox { background: white; border: 1px solid #c9d5df; border-radius: 7px;
                margin-top: 9px; padding-top: 8px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLineEdit, QComboBox { background: white; border: 1px solid #9fb2c2;
                border-radius: 4px; padding: 5px; min-height: 22px; }
            QPushButton { background: #f7fafc; border: 1px solid #9fb2c2; border-radius: 4px;
                padding: 6px 11px; }
            QPushButton:hover { background: #e3edf5; }
            QPushButton#primary { background: #2477b3; color: white; border-color: #1a6399;
                font-weight: 600; }
            QPushButton:disabled { color: #8b99a5; background: #e8edf1; }
            """
        )

    def _build_tray(self) -> None:
        icon_path = runtime_asset_path("commserver_icon.ico")
        icon = (
            QtGui.QIcon(str(icon_path))
            if icon_path is not None
            else self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        )
        self.setWindowIcon(icon)
        self.tray = QtWidgets.QSystemTrayIcon(icon, self)
        menu = QtWidgets.QMenu(self)
        show_action = menu.addAction("Show Communication Server")
        start_action = menu.addAction("Start / Restart Server")
        stop_action = menu.addAction("Stop Server")
        menu.addSeparator()
        log_action = menu.addAction("Open Log")
        exit_action = menu.addAction("Exit")
        show_action.triggered.connect(self._show_window)
        start_action.triggered.connect(self.start_server)
        stop_action.triggered.connect(self.stop_server)
        log_action.triggered.connect(self._open_log)
        exit_action.triggered.connect(self._exit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    def _settings_changed(self, *_args: object) -> None:
        self._service_name = self.name_edit.text().strip() or "SAMBA Controller"
        self._update_access_warning()
        if self._server is not None:
            self._dirty = True
            self.start_button.setText("Apply / Restart")

    def _update_access_warning(self) -> None:
        if not hasattr(self, "warning_label") or not hasattr(self, "auth_combo"):
            return
        if self.auth_combo.currentData() == "token":
            self.warning_label.setText(
                "令牌鉴权：远程客户端必须使用服务电脑上的访问令牌；局域网发现仍可见。"
            )
        else:
            self.warning_label.setText(
                "可信网络免验证：同一局域网或 Tailnet 中的用户可以控制真机。"
            )

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText().strip() if self.port_combo.count() else ""
        remembered_port = str(self._config.get("port") or current)
        remembered_identity = str(self._config.get("serial_identity") or "")
        try:
            from serial.tools import list_ports

            ports = list(list_ports.comports())
        except Exception:
            ports = []
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        selected = -1
        candidates: list[int] = []
        for item in sorted(ports, key=lambda value: str(value.device)):
            identity = _serial_identity(item)
            description = str(item.description or item.device)
            self.port_combo.addItem(f"{item.device} — {description}", (item.device, identity))
            index = self.port_combo.count() - 1
            if remembered_identity and identity == remembered_identity:
                selected = index
            elif selected < 0 and str(item.device).casefold() == remembered_port.casefold():
                selected = index
            if "bluetooth" not in description.casefold() and _is_usb_serial_port(item):
                candidates.append(index)
        if selected < 0 and len(candidates) == 1:
            selected = candidates[0]
        if selected >= 0:
            self.port_combo.setCurrentIndex(selected)
        elif remembered_port:
            self.port_combo.setEditText(remembered_port)
        else:
            self.port_combo.setCurrentIndex(-1)
            self.port_combo.setEditText("")
        self.port_combo.blockSignals(False)
        self._settings_changed()

    def _selected_port(self) -> tuple[str | None, str]:
        data = self.port_combo.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            return str(data[0]).strip() or None, str(data[1])
        text = self.port_combo.currentText().strip()
        if " — " in text:
            text = text.split(" — ", 1)[0].strip()
        return text or None, ""

    def _save_current_config(self) -> None:
        port, identity = self._selected_port()
        self._config.update(
            {
                "name": self._service_name,
                "port": port,
                "serial_identity": identity,
                "baudrate": int(self.baud_combo.currentText()),
                "auth_mode": str(self.auth_combo.currentData()),
            }
        )
        _save_config(self._config, self._config_path)

    @QtCore.Slot()
    def start_server(self) -> None:
        self.stop_server()
        self._save_current_config()
        port, _identity = self._selected_port()
        auth_mode = str(self.auth_combo.currentData())
        try:
            server = CommunicationServer(
                [("0.0.0.0", self._tcp_port)],
                auth_mode=auth_mode,
                preferred_port=port,
                preferred_baudrate=int(self.baud_combo.currentText()),
            )
            server.start()
            announcer = DiscoveryAnnouncer(
                server.status,
                server_id=load_or_create_server_id(),
                name=lambda: self._service_name,
                tcp_port=self._tcp_port,
                discovery_port=self._discovery_port,
                auth_mode=server.auth_mode,
            )
            announcer.start()
            if announcer.last_error:
                server.stop()
                raise RuntimeError(
                    f"UDP discovery port {self._discovery_port} failed: {announcer.last_error}"
                )
        except ServerAlreadyRunning as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Server already running",
                f"TCP {self._tcp_port} is already in use. Another Communication Server may be running.\n\n{exc}",
            )
            self._refresh_status()
            return
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Start failed", str(exc))
            self._refresh_status()
            return
        self._server = server
        self._announcer = announcer
        self._dirty = False
        self.start_button.setText("Restart Server")
        self._refresh_status()

    @QtCore.Slot()
    def stop_server(self) -> None:
        announcer, self._announcer = self._announcer, None
        server, self._server = self._server, None
        if announcer is not None:
            announcer.stop()
        if server is not None:
            server.stop()
        self.start_button.setText("Start Server")
        self._refresh_status()

    def _refresh_status(self) -> None:
        server = self._server
        firewall = "configured" if self._firewall_ok else "not configured"
        if server is None:
            text = (
                f"Server: stopped\nDiscovery: stopped (UDP {self._discovery_port})\n"
                f"Firewall: {firewall}\nLog: {default_log_file()}"
            )
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.tray.setToolTip("Python SAMBA Communication Server — stopped")
        else:
            state = server.status()
            serial = state.get("serial")
            serial = serial if isinstance(serial, dict) else {}
            endpoints = ", ".join(_network_endpoints(self._tcp_port))
            endpoints = endpoints or f"no trusted IPv4 interface (TCP {self._tcp_port})"
            clients_value = state.get("clients")
            clients = clients_value if isinstance(clients_value, list) else []
            client_lines: list[str] = []
            for item in clients[:8]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "unknown")
                pid = item.get("pid")
                peer = str(item.get("peer") or "?")
                attached = "attached" if item.get("attached") else "registered"
                client_lines.append(
                    f"  • {name}{f' (PID {pid})' if pid is not None else ''} — {peer}, {attached}"
                )
            client_details = "\n" + "\n".join(client_lines) if client_lines else ""
            dirty = "\nSettings changed: click Apply / Restart." if self._dirty else ""
            text = (
                f"Server: running\nNetwork: {endpoints}\n"
                f"Discovery: broadcasting UDP {self._discovery_port}\n"
                f"Access: {server.auth_mode}\n"
                f"Controller: {serial.get('port') or 'not selected'} @ "
                f"{serial.get('baudrate') or '—'} "
                f"({'open' if serial.get('open') else 'idle'})\n"
                f"Clients: {state.get('client_count', 0)} "
                f"(attached {state.get('attached_count', 0)}) · "
                f"Queue: {state.get('queue_length', 0)}{client_details}\n"
                f"Last command: {state.get('last_command') or '—'} · "
                f"{float(state.get('last_duration_ms') or 0):.1f} ms\n"
                f"Last error: {state.get('last_error') or '—'}\n"
                f"Firewall: {firewall}{dirty}"
            )
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.tray.setToolTip(
                f"{self._service_name}\n{serial.get('port') or 'No serial'} · "
                f"{state.get('client_count', 0)} client(s)"
            )
        self.status_label.setText(text)

    def _offer_firewall_setup(self) -> None:
        if os.name != "nt" or self._firewall_ok:
            return
        if self._config.get("firewall_prompted"):
            return
        self._config["firewall_prompted"] = True
        _save_config(self._config, self._config_path)
        answer = QtWidgets.QMessageBox.question(
            self,
            "Allow network discovery",
            "Communication Server needs scoped inbound rules for TCP 47619 and UDP 47620.\n"
            "Only LocalSubnet and Tailscale 100.64.0.0/10 will be allowed. Configure now?",
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._configure_firewall()

    def _configure_firewall(self) -> None:
        if request_firewall_rules():
            QtWidgets.QMessageBox.information(
                self,
                "Firewall",
                "Administrator approval was requested. The status will refresh automatically.",
            )
            QtCore.QTimer.singleShot(2500, self._check_firewall)
        else:
            QtWidgets.QMessageBox.warning(
                self,
                "Firewall",
                "Could not request administrator approval. Allow TCP 47619 and UDP 47620 manually.",
            )

    def _check_firewall(self) -> None:
        self._firewall_ok = firewall_rules_installed()
        self._refresh_status()

    def _open_log(self) -> None:
        path = default_log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_application(self) -> None:
        self._allow_close = True
        self.stop_server()
        self.tray.hide()
        QtWidgets.QApplication.quit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self._allow_close:
            self.stop_server()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Communication Server",
            "Server is still running in the notification area.",
            QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            2500,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="PythonSambaCommServer")
    parser.add_argument("--config", default=None, help="alternate JSON config path")
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--no-firewall-prompt", action="store_true")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--discovery-port", type=int, default=DISCOVERY_PORT, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--exit-after",
        type=int,
        default=0,
        metavar="MILLISECONDS",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("Python SAMBA Communication Server")
    app.setOrganizationName("python_samba")
    app.setQuitOnLastWindowClosed(False)
    window = CommunicationServerWindow(
        config_path=args.config,
        auto_start=not args.no_auto_start,
        offer_firewall=not args.no_firewall_prompt,
        tcp_port=args.tcp_port,
        discovery_port=args.discovery_port,
    )
    window.show()
    if args.exit_after > 0:
        QtCore.QTimer.singleShot(args.exit_after, window._exit_application)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CommunicationServerWindow", "main"]
