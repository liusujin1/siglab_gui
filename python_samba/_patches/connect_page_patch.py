"""Patch: replace _build_connect_page with C#-featured version.

Adds from the C# SAMBA19xUI ConnectionPage:
- COM port scanning with radio buttons (detect available ports)
- Port refresh button
- Kill CommServer button
- CommServer port registry checkboxes (Windows registry)
- Help/About/Third-party license hyperlinks
- Improved firmware version parsing (space-separated -> structured multiline)

Usage in main_window.py:
    from python_samba._patches.connect_page_patch import (
        build_connect_page,
        kill_comm_server_and_connect,
        on_connect_page_connect,
        on_connect_page_disconnect,
        on_refresh_comm_ports,
        on_com_port_changed,
        on_com_server_cb_clicked,
        on_help_hyperlink,
        on_about_hyperlink,
        on_third_party_license_hyperlink,
    )
    MainWindow._build_connect_page = build_connect_page
    MainWindow.kill_comm_server_and_connect = kill_comm_server_and_connect
    MainWindow.on_connect_page_connect = on_connect_page_connect
    MainWindow.on_connect_page_disconnect = on_connect_page_disconnect
    MainWindow.on_refresh_comm_ports = on_refresh_comm_ports
    MainWindow.on_com_port_changed = on_com_port_changed
    MainWindow.on_com_server_cb_clicked = on_com_server_cb_clicked
    MainWindow.on_help_hyperlink = on_help_hyperlink
    MainWindow.on_about_hyperlink = on_about_hyperlink
    MainWindow.on_third_party_license_hyperlink = on_third_party_license_hyperlink
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import FlatPush, GroupPanel

# ---------------------------------------------------------------------------
# Firmware version parser (matching C# FirmwareVersionStr_PropertyChanged)
# ---------------------------------------------------------------------------

FW_LABELS_LONG = (
    (0, 1, 2),       # Firmware Version: X.Y.Z
    (3,),             # Lib Version
    (4,),             # Main Board Version
    (5, 6),
    (7, 8, 9, 10),
    (11, 12),
    (13, 14),
    (15, 16, 17, 18),
    (19, 20),
)

FW_LABEL_NAMES: dict[int, str] = {
    0: "Firmware Version",
    3: "Lib Version",
    4: "Main Board Version",
}


def parse_fw_version(raw: str) -> str:
    """Parse a space-separated firmware version string into structured lines.

    Mirrors the C# ``FirmwareVersionStr_PropertyChanged`` logic.

    - Fewer than 3 non-empty tokens -> returns the raw string unchanged.
    - 3 to 20 tokens -> ``Firmware Version: M.m.p``
    - 21+ tokens -> structured multiline with labelled fields.
    """
    parts = [p for p in raw.split(" ") if p and p != " "]
    if len(parts) < 3:
        return raw

    if len(parts) < 21:
        return f"Firmware Version: {parts[0]}.{parts[1]}.{parts[2]}"

    lines: list[str] = []
    for group in FW_LABELS_LONG:
        label = FW_LABEL_NAMES.get(group[0], "")
        tokens = [parts[i] for i in group if i < len(parts)]
        if group[0] == 0 and tokens:
            lines.append(f"{label}: {'.'.join(tokens)}")
        elif label and tokens:
            lines.append(f"{label}: {tokens[0]}")
        elif tokens:
            lines.append(" ".join(tokens))
        elif label:
            lines.append(f"{label}: —")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COM port scanning (pyserial, already a dependency)
# ---------------------------------------------------------------------------

def get_available_ports() -> list[str]:
    """Return a list of available COM / serial port device names."""
    try:
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]
    except ImportError:
        return []


# ---------------------------------------------------------------------------
# Windows registry helpers for CommServer port configuration
# ---------------------------------------------------------------------------

COMMSERVER_REG_KEY = r"Software\IDE GmbH\Communication Server\Serial"


def _check_com_port_registry(port: str) -> bool:
    """Check whether *port* is enabled in the CommServer registry.

    Reads ``HKCU\\Software\\IDE GmbH\\Communication Server\\Serial\\<port>\\Use``.
    Returns ``False`` on non-Windows or if the key is missing.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = f"{COMMSERVER_REG_KEY}\\{port}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "Use")
            return bool(value)
    except (OSError, ImportError):
        return False


def _write_com_port_registry(port: str, use: bool) -> None:
    """Enable/disable *port* in the CommServer registry.

    On non-Windows this is a no-op.
    """
    if sys.platform != "win32":
        return
    try:
        import winreg
        key_path = f"{COMMSERVER_REG_KEY}\\{port}"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
        except OSError:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        with key:
            winreg.SetValueEx(key, "Use", 0, winreg.REG_DWORD, 1 if use else 0)
    except (OSError, ImportError):
        pass


# ---------------------------------------------------------------------------
# Kill CommServer process (cross-platform)
# ---------------------------------------------------------------------------

def kill_comm_server_processes() -> None:
    """Kill all processes named ``CommServer`` (case-insensitive).

    On Windows uses ``taskkill``; on other platforms uses ``pkill``.
    Falls back to ``psutil`` if installed.
    """
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"] or ""
                if "commserver" in name.lower():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return
    except ImportError:
        pass

    # Fallback to CLI tools
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "CommServer.exe"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["pkill", "-9", "-f", "[Cc]omm[Ss]erver"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hyperlink / dialog helpers
# ---------------------------------------------------------------------------

def _open_help_manual() -> None:
    """Open SAMBA19xUI User Interface Manual.pdf next to the app binary."""
    # Try common locations relative to the running script
    base = os.path.dirname(sys.argv[0]) if sys.argv else os.getcwd()
    candidates = [
        os.path.join(base, "SAMBA19xUI User Interface Manual.pdf"),
        os.path.join(base, "doc", "SAMBA19xUI User Interface Manual.pdf"),
        os.path.join(base, "..", "SAMBA19xUI User Interface Manual.pdf"),
        os.path.join(base, "..", "doc", "SAMBA19xUI User Interface Manual.pdf"),
    ]
    for path in candidates:
        resolved = os.path.normpath(os.path.abspath(path))
        if os.path.isfile(resolved):
            try:
                os.startfile(resolved) if sys.platform == "win32" else None
            except Exception:
                pass
            return


def _open_third_party_license() -> None:
    """Open ThirdPartySoftwareLicense.html next to the app binary."""
    base = os.path.dirname(sys.argv[0]) if sys.argv else os.getcwd()
    candidates = [
        os.path.join(base, "ThirdPartySoftwareLicense.html"),
        os.path.join(base, "doc", "ThirdPartySoftwareLicense.html"),
    ]
    for path in candidates:
        resolved = os.path.normpath(os.path.abspath(path))
        if os.path.isfile(resolved):
            try:
                os.startfile(resolved) if sys.platform == "win32" else None
            except Exception:
                pass
            return


def _show_about_dialog(parent) -> None:
    """Show a simple About dialog matching SAMBA19xUI style."""
    QtWidgets.QMessageBox.about(
        parent,
        "About SAMBA19xUI",
        "SAMBA19xUI — TC-MFD / OPTICON Controller Interface\n"
        "\n"
        "python_samba — Vendor-free host software for\n"
        "IDE TC-MFD / OPTICON active vibration isolation\n"
        "\n"
        "Based on decompiled SAMBA19xUI (C# / .NET WPF)",
    )


# ---------------------------------------------------------------------------
# Replacement method: _build_connect_page
# ---------------------------------------------------------------------------

def build_connect_page(self) -> None:
    """Connection page — full C#-featured version.

    Adds to the tab:
    - COM port scanning with RadioButton selection (``PortListe``)
    - CommServer port enable/disable via CheckBox (``ComServerPortListe``)
    - Refresh COM ports button
    - Kill CommServer + Connect button
    - Connect / Disconnect buttons
    - Firmware version display (parsed multiline)
    - System config display
    - Help / About / Third-party license hyperlinks
    - Raw RCI section (preserved from original)
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(8, 6, 8, 6)

    # ------------------------------------------------------------------
    # Top row: COM port list + CommServer port list side by side
    # ------------------------------------------------------------------
    top_row = QtWidgets.QHBoxLayout()

    # -- COM Port List (PortListe) --
    port_group = GroupPanel("COM Ports")
    port_vbox = QtWidgets.QVBoxLayout(port_group)

    port_btn_row = QtWidgets.QHBoxLayout()
    port_btn_row.addWidget(QtWidgets.QLabel("Available ports:"))
    port_btn_row.addStretch(1)
    self._refresh_ports_btn = FlatPush("Update COM ports")
    self._refresh_ports_btn.clicked.connect(self.on_refresh_comm_ports)
    port_btn_row.addWidget(self._refresh_ports_btn)
    port_vbox.addLayout(port_btn_row)

    self._port_list_box = QtWidgets.QScrollArea()
    self._port_list_box.setWidgetResizable(True)
    self._port_list_box.setFixedHeight(140)
    self._port_list_inner = QtWidgets.QWidget()
    self._port_list_layout = QtWidgets.QVBoxLayout(self._port_list_inner)
    self._port_list_layout.setContentsMargins(2, 2, 2, 2)
    self._port_list_layout.setSpacing(2)
    self._port_list_box.setWidget(self._port_list_inner)
    port_vbox.addWidget(self._port_list_box)

    # Port radio button group (exclusive)
    self._port_group = QtWidgets.QButtonGroup(self)
    self._port_group.idClicked.connect(self.on_com_port_changed)

    # Baud rate combo
    baud_row = QtWidgets.QHBoxLayout()
    baud_row.addWidget(QtWidgets.QLabel("Baud rate:"))
    self._baud_connect = QtWidgets.QComboBox()
    for b in (19200, 38400, 57600, 115200, 230400):
        self._baud_connect.addItem(str(b), b)
    self._baud_connect.setCurrentText("57600")
    baud_row.addWidget(self._baud_connect)
    baud_row.addStretch(1)
    port_vbox.addLayout(baud_row)

    top_row.addWidget(port_group, 1)

    # -- CommServer Port List (ComServerPortListe) --
    cs_group = GroupPanel("CommServer Serial Ports")
    cs_vbox = QtWidgets.QVBoxLayout(cs_group)
    cs_vbox.addWidget(QtWidgets.QLabel("Enable/disable ports (registry):"))

    self._cs_port_box = QtWidgets.QScrollArea()
    self._cs_port_box.setWidgetResizable(True)
    self._cs_port_box.setFixedHeight(140)
    self._cs_port_inner = QtWidgets.QWidget()
    self._cs_port_layout = QtWidgets.QVBoxLayout(self._cs_port_inner)
    self._cs_port_layout.setContentsMargins(2, 2, 2, 2)
    self._cs_port_layout.setSpacing(2)
    self._cs_port_box.setWidget(self._cs_port_inner)
    cs_vbox.addWidget(self._cs_port_box)

    cs_vbox.addWidget(QtWidgets.QLabel(
        "Changes require CommServer restart.\n"
        "Use 'Terminate CommServer and Connect' below."
    ))
    top_row.addWidget(cs_group, 1)

    root.addLayout(top_row)

    # ------------------------------------------------------------------
    # Connection buttons row
    # ------------------------------------------------------------------
    btn_row = QtWidgets.QHBoxLayout()

    self._conn_page_connect_btn = FlatPush("Connect")
    self._conn_page_connect_btn.clicked.connect(self.on_connect_page_connect)
    btn_row.addWidget(self._conn_page_connect_btn)

    self._conn_page_disconnect_btn = FlatPush("Disconnect")
    self._conn_page_disconnect_btn.setEnabled(False)
    self._conn_page_disconnect_btn.clicked.connect(self.on_connect_page_disconnect)
    btn_row.addWidget(self._conn_page_disconnect_btn)

    self._kill_comm_server_btn = FlatPush("Terminate CommServer and Connect")
    self._kill_comm_server_btn.setVisible(False)
    self._kill_comm_server_btn.setStyleSheet(
        "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        "stop:0 #ffd0d0, stop:1 #e0a0a0); color: #800000;"
        "border: 1px solid #c06060; }"
    )
    self._kill_comm_server_btn.clicked.connect(self.kill_comm_server_and_connect)
    btn_row.addWidget(self._kill_comm_server_btn)

    btn_row.addStretch(1)
    root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Connection info panel
    # ------------------------------------------------------------------
    g = GroupPanel("Connection")
    form = QtWidgets.QFormLayout(g)

    self.conn_info = QtWidgets.QLabel("Not connected")
    self.conn_info.setWordWrap(True)
    form.addRow("Session:", self.conn_info)

    self.fw_version = QtWidgets.QLabel("Firmware Version: —")
    self.fw_version.setWordWrap(True)
    form.addRow("Firmware:", self.fw_version)

    self.sys_config = QtWidgets.QLabel("System Config: —")
    self.sys_config.setWordWrap(True)
    form.addRow("Config:", self.sys_config)

    root.addWidget(g)

    # ------------------------------------------------------------------
    # Hyperlinks (Help / About / Third-party license)
    # ------------------------------------------------------------------
    link_row = QtWidgets.QHBoxLayout()
    link_row.setSpacing(16)

    help_link = QtWidgets.QLabel('<a href="help" style="color:#316ac5;">Help — User Manual</a>')
    help_link.setOpenExternalLinks(False)
    help_link.linkActivated.connect(self.on_help_hyperlink)
    link_row.addWidget(help_link)

    about_link = QtWidgets.QLabel('<a href="about" style="color:#316ac5;">About SAMBA19xUI</a>')
    about_link.setOpenExternalLinks(False)
    about_link.linkActivated.connect(self.on_about_hyperlink)
    link_row.addWidget(about_link)

    license_link = QtWidgets.QLabel(
        '<a href="license" style="color:#316ac5;">Third-Party Software License</a>'
    )
    license_link.setOpenExternalLinks(False)
    license_link.linkActivated.connect(self.on_third_party_license_hyperlink)
    link_row.addWidget(license_link)

    link_row.addStretch(1)
    root.addLayout(link_row)

    # ------------------------------------------------------------------
    # Raw RCI (preserved from original)
    # ------------------------------------------------------------------
    raw_box = GroupPanel("Raw RCI")
    raw_form = QtWidgets.QFormLayout(raw_box)
    self.raw_cmd = QtWidgets.QLineEdit("BGVIS")
    self.raw_params = QtWidgets.QLineEdit()
    self.raw_out = QtWidgets.QPlainTextEdit()
    self.raw_out.setReadOnly(True)
    self.raw_out.setFixedHeight(80)
    raw_form.addRow("Mnemonic:", self.raw_cmd)
    raw_form.addRow("Params:", self.raw_params)
    raw_btn = FlatPush("Send")
    raw_btn.clicked.connect(self.on_raw_send)
    raw_form.addRow(raw_btn)
    raw_form.addRow("Response:", self.raw_out)
    root.addWidget(raw_box)

    root.addStretch(1)
    self.main_tabs.addTab(w, "Connect")

    # Populate the port lists
    self._populate_port_lists()


def build_connect_page_reference(self) -> None:
    """Build the compact Connect page seen in the supplied SAMBA screenshots."""
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 27, 6, 8)
    root.setSpacing(4)

    top = QtWidgets.QHBoxLayout()
    top.setSpacing(6)

    self._port_list_box = QtWidgets.QScrollArea()
    self._port_list_box.setObjectName("connectPortList")
    self._port_list_box.setWidgetResizable(True)
    self._port_list_box.setFixedSize(170, 245)
    self._port_list_inner = QtWidgets.QWidget()
    self._port_list_inner.setObjectName("connectPortListInner")
    self._port_list_layout = QtWidgets.QVBoxLayout(self._port_list_inner)
    self._port_list_layout.setContentsMargins(8, 6, 8, 6)
    self._port_list_layout.setSpacing(4)
    self._port_list_box.setWidget(self._port_list_inner)
    top.addWidget(self._port_list_box)

    self._port_group = QtWidgets.QButtonGroup(self)
    self._port_group.idClicked.connect(self.on_com_port_changed)

    controls = QtWidgets.QVBoxLayout()
    controls.setSpacing(5)
    first = QtWidgets.QHBoxLayout()
    button_col = QtWidgets.QVBoxLayout()
    self._conn_page_connect_btn = FlatPush("Connect")
    self._conn_page_connect_btn.setFixedWidth(160)
    self._conn_page_connect_btn.clicked.connect(self.on_connect_page_connect)
    button_col.addWidget(self._conn_page_connect_btn)
    self._conn_page_disconnect_btn = FlatPush("Disconnect")
    self._conn_page_disconnect_btn.setFixedWidth(160)
    self._conn_page_disconnect_btn.setEnabled(False)
    self._conn_page_disconnect_btn.clicked.connect(self.on_connect_page_disconnect)
    button_col.addWidget(self._conn_page_disconnect_btn)
    first.addLayout(button_col)

    baud_col = QtWidgets.QVBoxLayout()
    baud_col.addWidget(QtWidgets.QLabel("Baud Rate"))
    self._baud_connect = QtWidgets.QComboBox()
    for value in (19200, 38400, 57600, 115200, 230400):
        self._baud_connect.addItem(str(value), value)
    self._baud_connect.setCurrentText("57600")
    self._baud_connect.setFixedWidth(130)
    baud_col.addWidget(self._baud_connect)
    baud_col.addStretch(1)
    first.addLayout(baud_col)
    controls.addLayout(first)

    self._kill_comm_server_btn = FlatPush("Communication Server Status / Restart")
    self._kill_comm_server_btn.setMinimumWidth(360)
    self._kill_comm_server_btn.clicked.connect(self.kill_comm_server_and_connect)
    controls.addWidget(self._kill_comm_server_btn, 0, QtCore.Qt.AlignLeft)

    connection_form = QtWidgets.QFormLayout()
    connection_form.setContentsMargins(0, 2, 0, 2)
    self._backend_connect = QtWidgets.QComboBox()
    self._backend_connect.addItems(["server", "serial", "mock"])
    self._backend_connect.setCurrentText(self.backend.currentText())
    self._server_endpoint_connect = QtWidgets.QLineEdit(self.server_endpoint.text())
    self._server_endpoint_connect.setMinimumWidth(220)
    connection_form.addRow("Backend:", self._backend_connect)
    connection_form.addRow("Server:", self._server_endpoint_connect)
    controls.addLayout(connection_form)

    def sync_backend(value: str) -> None:
        self.backend.setCurrentText(value)
        self._sync_port_enabled(value)

    def sync_server(value: str) -> None:
        if self.server_endpoint.text() != value:
            self.server_endpoint.setText(value)

    self._backend_connect.currentTextChanged.connect(sync_backend)
    self._server_endpoint_connect.textChanged.connect(sync_server)

    self._refresh_ports_btn = FlatPush("Update Comm Ports List")
    self._refresh_ports_btn.clicked.connect(self.on_refresh_comm_ports)
    controls.addWidget(self._refresh_ports_btn, 0, QtCore.Qt.AlignLeft)
    controls.addStretch(1)
    top.addLayout(controls)
    top.addStretch(1)
    root.addLayout(top)

    def expander(title: str, content: QtWidgets.QWidget, *, expanded: bool = False):
        # QPushButton honours ``text-align:left`` on Windows, unlike the
        # QToolButton used previously (which centred every section title in
        # the middle of the workspace).
        button = QtWidgets.QPushButton()
        button.setObjectName("connectExpander")
        button.setText(("⌃  " if expanded else "⌄  ") + title)
        button.setCheckable(True)
        button.setChecked(expanded)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        button.setMinimumHeight(40)
        content.setVisible(expanded)

        def toggle(shown: bool) -> None:
            content.setVisible(shown)
            button.setText(("⌃  " if shown else "⌄  ") + title)

        button.toggled.connect(toggle)
        root.addWidget(button)
        root.addWidget(content)
        return button

    # New shared Communication Server content.  The old registry checkboxes
    # controlled a vendor process and are deliberately replaced by observable
    # state from python_samba's own singleton server.
    cs_content = QtWidgets.QFrame()
    cs_content.setObjectName("connectExpandedPanel")
    cs_layout = QtWidgets.QVBoxLayout(cs_content)
    cs_layout.setContentsMargins(14, 4, 14, 8)
    self._cs_port_layout = QtWidgets.QVBoxLayout()
    self._cs_port_layout.setContentsMargins(2, 2, 2, 2)
    self._cs_port_layout.setSpacing(2)
    cs_layout.addLayout(self._cs_port_layout)
    cs_layout.addWidget(QtWidgets.QLabel(
        "The server is the only process that owns the physical COM port.\n"
        "SAMBA and SIDMAT requests share one global FIFO queue; the last write wins."
    ))
    expander("Communication Server", cs_content)

    fw_content = QtWidgets.QFrame()
    fw_form = QtWidgets.QFormLayout(fw_content)
    self.conn_info = QtWidgets.QLabel("Not connected")
    self.fw_version = QtWidgets.QLabel("Firmware Version: —")
    self.fw_version.setWordWrap(True)
    fw_form.addRow("Session:", self.conn_info)
    fw_form.addRow("Firmware:", self.fw_version)
    expander("Firmware Version Info", fw_content)

    sys_content = QtWidgets.QFrame()
    sys_form = QtWidgets.QFormLayout(sys_content)
    self.sys_config = QtWidgets.QLabel("System Config: —")
    self.sys_config.setWordWrap(True)
    sys_form.addRow("Configuration:", self.sys_config)
    expander("System Config Info", sys_content)

    about_content = QtWidgets.QFrame()
    about_row = QtWidgets.QHBoxLayout(about_content)
    for text, link, slot in (
        ("Help — User Manual", "help", self.on_help_hyperlink),
        ("About SAMBA19xUI", "about", self.on_about_hyperlink),
        ("Third-Party Software License", "license", self.on_third_party_license_hyperlink),
    ):
        label = QtWidgets.QLabel(f'<a href="{link}">{text}</a>')
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(slot)
        about_row.addWidget(label)
    about_row.addStretch(1)
    expander("About?", about_content)

    raw_content = QtWidgets.QFrame()
    raw_form = QtWidgets.QFormLayout(raw_content)
    self.raw_cmd = QtWidgets.QLineEdit("BGVIS")
    self.raw_params = QtWidgets.QLineEdit()
    self.raw_out = QtWidgets.QPlainTextEdit()
    self.raw_out.setReadOnly(True)
    self.raw_out.setFixedHeight(80)
    raw_form.addRow("Mnemonic:", self.raw_cmd)
    raw_form.addRow("Params:", self.raw_params)
    raw_btn = FlatPush("Send")
    raw_btn.clicked.connect(self.on_raw_send)
    raw_form.addRow(raw_btn)
    raw_form.addRow("Response:", self.raw_out)
    raw_expander = expander("Advanced / Raw RCI", raw_content)
    raw_expander.hide()

    root.addStretch(1)
    self.main_tabs.addTab(w, "Connect")
    self._populate_port_lists()
    self._sync_port_enabled(self.backend.currentText())


# ---------------------------------------------------------------------------
# Port list population (called at build and on refresh)
# ---------------------------------------------------------------------------

def _populate_port_lists(self) -> None:
    """Scan for available COM ports and rebuild both radio and checkbox lists."""
    ports = get_available_ports()

    # --- Radio button list (PortListe) ---
    # Clear existing buttons
    for btn in self._port_group.buttons():
        self._port_group.removeButton(btn)
    self._clear_layout(self._port_list_layout)

    if not ports:
        lbl = QtWidgets.QLabel("No COM ports detected")
        lbl.setStyleSheet("color:#888; font-style:italic; padding:4px;")
        self._port_list_layout.addWidget(lbl)
    else:
        for i, port_name in enumerate(ports):
            rb = QtWidgets.QRadioButton(port_name)
            self._port_group.addButton(rb, i)
            self._port_list_layout.addWidget(rb)

    self._port_list_layout.addStretch(1)

    # --- Shared-server summary (replaces obsolete vendor registry toggles) ---
    self._clear_layout(self._cs_port_layout)
    endpoint = getattr(self, "server_endpoint", None)
    endpoint_text = endpoint.text().strip() if endpoint is not None else "127.0.0.1:47619"
    self._cs_port_layout.addWidget(QtWidgets.QLabel(f"Endpoint: {endpoint_text}"))
    self._cs_port_layout.addWidget(
        QtWidgets.QLabel("Status is available from the button above or the tray icon.")
    )


# ---------------------------------------------------------------------------
# Helper: clear a layout of all child widgets
# ---------------------------------------------------------------------------

def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
    """Remove all items from *layout*, deleting their widgets."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()
        elif item.layout():
            self._clear_layout(item.layout())


# ---------------------------------------------------------------------------
# Handler: refresh COM ports list
# ---------------------------------------------------------------------------

def on_refresh_comm_ports(self) -> None:
    """Refresh the COM port list (UpdateCommPortsListBtn_Click)."""
    self._populate_port_lists()
    self.log_msg("COM port list refreshed")


# ---------------------------------------------------------------------------
# Handler: COM port radio button changed
# ---------------------------------------------------------------------------

def on_com_port_changed(self, btn_id: int) -> None:
    """When a COM port radio button is selected, sync to the toolbar port field."""
    btn = self._port_group.button(btn_id)
    if btn:
        port_name = btn.text()
        self.port.setText(port_name)
        self.log_msg(f"Selected port: {port_name}")


# ---------------------------------------------------------------------------
# Handler: CommServer port checkbox toggled
# ---------------------------------------------------------------------------

def on_com_server_cb_clicked(self, checked: bool) -> None:
    """When a CommServer port checkbox is toggled, write to registry."""
    cb = self.sender()
    if isinstance(cb, QtWidgets.QCheckBox):
        port_name = cb.text()
        _write_com_port_registry(port_name, checked)
        action = "enabled" if checked else "disabled"
        self.log_msg(f"CommServer port {port_name} {action} in registry")

        # Show info dialog on first use (matching C# WriteComPortUsageRegistry)
        QtWidgets.QMessageBox.information(
            self,
            "CommServer Port Change",
            "This modification needs a restart of the CommServer.\n"
            "Ensure the CommServer is not used by another SAMBA application,\n"
            "and use 'Terminate CommServer and Connect' button to restart it!",
        )


# ---------------------------------------------------------------------------
# Handler: Connect page Connect button
# ---------------------------------------------------------------------------

def on_connect_page_connect(self) -> None:
    """Connect button handler on the Connect page.

    Syncs the selected port and baud rate to the toolbar widgets, then
    delegates to the toolbar's ``on_connect`` (which uses
    ``self.port.text()`` and ``self.baud.currentData()``).
    Mirrors C# ``ConnectBtn_Click`` / ``ConnectToController``.
    """

    # Sync the selected COM port from the port-list radio buttons to the
    # toolbar's ``self.port`` text field (used by ``on_connect``).
    selected = self._port_group.checkedButton()
    if selected is not None:
        self.port.setText(selected.text().strip())

    # Sync the baud rate from the Connect page combo to the toolbar combo.
    baud_val = self._baud_connect.currentData()
    idx = self.baud.findData(baud_val)
    if idx >= 0:
        self.baud.setCurrentIndex(idx)

    backend = getattr(self, "_backend_connect", None)
    self.backend.setCurrentText(backend.currentText() if backend is not None else "server")
    endpoint = getattr(self, "_server_endpoint_connect", None)
    if endpoint is not None:
        self.server_endpoint.setText(endpoint.text().strip())

    self.on_connect()

    # Sync the button states and update the firmware version display
    if self.session and self.session.connected:
        self._conn_page_connect_btn.setEnabled(False)
        self._conn_page_disconnect_btn.setEnabled(True)
        self._kill_comm_server_btn.setVisible(True)
        self._refresh_ports_btn.setEnabled(False)
        self._update_fw_version_display()
    else:
        self._sync_kill_btn_visibility()


# ---------------------------------------------------------------------------
# Handler: Connect page Disconnect button
# ---------------------------------------------------------------------------

def on_connect_page_disconnect(self) -> None:
    """Disconnect button handler on the Connect page.

    Delegates to the toolbar's ``on_disconnect``, then syncs the UI state.
    """
    self.on_disconnect()
    self._conn_page_connect_btn.setEnabled(True)
    self._conn_page_disconnect_btn.setEnabled(False)
    self._refresh_ports_btn.setEnabled(True)
    self._sync_kill_btn_visibility()


# ---------------------------------------------------------------------------
# Handler: Kill CommServer and Connect
# ---------------------------------------------------------------------------

def kill_comm_server_and_connect(self) -> None:
    """Open the new shared server's status/reopen dialog."""
    self.on_comm_server_status()


# ---------------------------------------------------------------------------
# Helper: show/hide kill button based on running CommServer processes
# ---------------------------------------------------------------------------

def _sync_kill_btn_visibility(self) -> None:
    """The shared-server status entry is always available."""
    self._kill_comm_server_btn.setVisible(True)


# ---------------------------------------------------------------------------
# Hyperlink handlers
# ---------------------------------------------------------------------------

def on_help_hyperlink(self, link: str = "") -> None:
    """Open the SAMBA19xUI User Interface Manual.pdf."""
    self.log_msg("Opening help manual...")
    _open_help_manual()


def on_about_hyperlink(self, link: str = "") -> None:
    """Show the About dialog."""
    _show_about_dialog(self)


def on_third_party_license_hyperlink(self, link: str = "") -> None:
    """Open the ThirdPartySoftwareLicense.html."""
    self.log_msg("Opening third-party software license...")
    _open_third_party_license()


# ---------------------------------------------------------------------------
# Override: update firmware version display with structured parsing
# ---------------------------------------------------------------------------

def _update_fw_version_display(self) -> None:
    """Parse the firmware version string into a structured multiline display.

    Call this after connection to update the firmware version label with
    the full parsed output.
    """
    if not self.session:
        self.fw_version.setText("Firmware Version: —")
        return
    try:
        fw = getattr(self, "_last_firmware_version", None)
        if fw is None:
            fw = self.session.get_version()
            self._last_firmware_version = fw
        raw = fw.full_text
        parsed = parse_fw_version(raw)
        self.fw_version.setText(parsed)
    except Exception:
        self.fw_version.setText("Firmware Version: —")


def apply_patches(cls: type) -> None:
    """Install the complete Connect-page extension on ``MainWindow``.

    The builder intentionally has a public name for standalone reuse, so it
    cannot rely on the legacy prefix scanner used by older patch modules.
    Keeping the binding list explicit also makes missing hooks testable.
    """
    cls._build_connect_page = build_connect_page_reference
    for name in (
        "_populate_port_lists",
        "_clear_layout",
        "on_refresh_comm_ports",
        "on_com_port_changed",
        "on_com_server_cb_clicked",
        "on_connect_page_connect",
        "on_connect_page_disconnect",
        "kill_comm_server_and_connect",
        "_sync_kill_btn_visibility",
        "on_help_hyperlink",
        "on_about_hyperlink",
        "on_third_party_license_hyperlink",
        "_update_fw_version_display",
    ):
        setattr(cls, name, globals()[name])
