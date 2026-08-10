"""Reusable non-blocking Communication Server discovery dialog."""

from __future__ import annotations

import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.commserver.discovery import DiscoveredServer, DiscoveryClient
from python_samba.commserver.protocol import PROTOCOL_VERSION


class _DiscoveryWorker(QtCore.QThread):
    resultFound = QtCore.Signal(object)
    scanFinished = QtCore.Signal(object)
    scanFailed = QtCore.Signal(str)

    def __init__(self, timeout: float = 1.5, parent=None) -> None:
        super().__init__(parent)
        self._timeout = float(timeout)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            results = DiscoveryClient().scan(
                self._timeout,
                on_result=lambda item: self.resultFound.emit(item),
                cancel=self._cancel,
            )
        except Exception as exc:
            self.scanFailed.emit(str(exc))
            return
        self.scanFinished.emit(results)


class ServerDiscoveryDialog(QtWidgets.QDialog):
    """Discover, inspect, and choose one shared controller server."""

    STALE_SECONDS = 6.0

    def __init__(self, parent=None, *, last_server_id: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Discover Communication Server")
        self.resize(980, 430)
        self.setMinimumSize(760, 350)
        self._last_server_id = str(last_server_id)
        self._servers: dict[str, DiscoveredServer] = {}
        self._worker: _DiscoveryWorker | None = None
        self.selected_server: DiscoveredServer | None = None
        self._closed = False

        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Searching the local network and online Tailscale computers. "
            "Select a ready controller service and connect."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Service",
                "Computer",
                "Controller",
                "Network",
                "Endpoint",
                "Clients",
                "Status",
                "Latency",
            ]
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_args: self._connect_selected())
        layout.addWidget(self.table, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Starting discovery…")
        bottom.addWidget(self.status_label, 1)
        self.rescan_button = QtWidgets.QPushButton("Rescan")
        self.rescan_button.clicked.connect(self.start_scan)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setDefault(True)
        self.connect_button.setEnabled(False)
        self.connect_button.clicked.connect(self._connect_selected)
        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.rescan_button)
        bottom.addWidget(self.connect_button)
        bottom.addWidget(cancel_button)
        layout.addLayout(bottom)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_stale_and_scan)
        self._refresh_timer.start(2000)
        QtCore.QTimer.singleShot(0, self.start_scan)

    @QtCore.Slot()
    def start_scan(self) -> None:
        if self._closed:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.rescan_button.setEnabled(False)
        self.status_label.setText("Scanning LAN and Tailscale…")
        worker = _DiscoveryWorker(parent=self)
        worker.resultFound.connect(self._upsert_server)
        worker.scanFinished.connect(self._scan_finished)
        worker.scanFailed.connect(self._scan_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    @QtCore.Slot(object)
    def _upsert_server(self, server: DiscoveredServer) -> None:
        self._servers[server.server_id] = server
        self._render_table(preferred_id=server.server_id if not self.table.selectedItems() else "")

    @QtCore.Slot(object)
    def _scan_finished(self, results: list[DiscoveredServer]) -> None:
        for server in results:
            self._servers[server.server_id] = server
        self._render_table()
        count = len(self._active_servers())
        self.status_label.setText(
            f"Found {count} active service(s)." if count else "No service found. Check server and firewall status."
        )

    @QtCore.Slot(str)
    def _scan_failed(self, message: str) -> None:
        self.status_label.setText(f"Discovery failed: {message}")

    @QtCore.Slot()
    def _worker_finished(self) -> None:
        self.rescan_button.setEnabled(True)

    def _active_servers(self) -> list[DiscoveredServer]:
        now = time.time()
        return [
            server
            for server in self._servers.values()
            if now - server.last_seen <= self.STALE_SECONDS
        ]

    def _render_table(self, *, preferred_id: str = "") -> None:
        selected = self._selected_server_id() or preferred_id or self._last_server_id
        servers = sorted(
            self._servers.values(),
            key=lambda item: (
                0 if item.server_id == self._last_server_id else 1,
                item.name.casefold(),
                item.hostname.casefold(),
            ),
        )
        self.table.setRowCount(len(servers))
        now = time.time()
        selected_row = -1
        for row, server in enumerate(servers):
            stale = now - server.last_seen > self.STALE_SECONDS
            controller = (
                f"{server.serial_port} @ {server.baudrate}"
                if server.serial_port and server.baudrate
                else "Not selected"
            )
            status = "offline" if stale else server.state.replace("_", " ")
            if server.protocol != PROTOCOL_VERSION:
                status = f"protocol {server.protocol} unsupported"
            elif server.error and server.state == "error":
                status = f"error: {server.error}"
            values = [
                server.name,
                server.hostname,
                controller,
                server.network.upper(),
                server.endpoint,
                str(server.client_count),
                status,
                f"{server.latency_ms:.0f} ms",
            ]
            for column, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                if column == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, server)
                    if server.server_id == self._last_server_id:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        item.setToolTip("Last selected service")
                if stale or not server.ready:
                    item.setForeground(QtGui.QColor("#8a5252" if server.error else "#7b8791"))
                self.table.setItem(row, column, item)
            if server.server_id == selected:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self._selection_changed()

    def _selected_server_id(self) -> str:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 0)
        server = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        return server.server_id if isinstance(server, DiscoveredServer) else ""

    def _current_server(self) -> DiscoveredServer | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        server = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        return server if isinstance(server, DiscoveredServer) else None

    @QtCore.Slot()
    def _selection_changed(self) -> None:
        server = self._current_server()
        active = bool(
            server
            and time.time() - server.last_seen <= self.STALE_SECONDS
            and server.ready
        )
        self.connect_button.setEnabled(active)
        if server and not active:
            if not server.serial_port:
                self.connect_button.setToolTip("Select a controller COM port on the server computer first.")
            elif server.protocol != PROTOCOL_VERSION:
                self.connect_button.setToolTip("Server protocol is not compatible with this client.")
            else:
                self.connect_button.setToolTip("Service is offline or not ready.")
        else:
            self.connect_button.setToolTip("")

    @QtCore.Slot()
    def _connect_selected(self) -> None:
        server = self._current_server()
        if not server or not self.connect_button.isEnabled():
            return
        self.selected_server = server
        self.accept()

    def _refresh_stale_and_scan(self) -> None:
        self._render_table()
        self.start_scan()

    def done(self, result: int) -> None:
        self._closed = True
        self._refresh_timer.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(2500)
        super().done(result)


def choose_communication_server(
    parent=None, *, last_server_id: str = ""
) -> DiscoveredServer | None:
    dialog = ServerDiscoveryDialog(parent, last_server_id=last_server_id)
    if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
        return dialog.selected_server
    return None


__all__ = ["ServerDiscoveryDialog", "choose_communication_server"]
