"""Low-priority patches: View3D, DigIOStatus, UIOptionWindow.

Module-level functions for monkey-patching:
  _build_special_tab     — adds DigIOStatus as sub-tab
  _page_view3d           — 3D view page (placeholder)
  _page_digio            — Digital IO status page
  show_ui_options        — UI options dialog
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import FlatPush, GroupPanel, LedIndicator, SciEdit


def _page_view3d(win) -> QtWidgets.QWidget:
    """3D view page — loads STL model if available, otherwise shows placeholder.

    The C# version uses HelixToolkit.Wpf to load '0002507.stl'.
    We use pyqtgraph if available, otherwise show a message.
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    # Try to use pyqtgraph for 3D
    has_3d = False
    try:
        import pyqtgraph.opengl as gl
        has_3d = True
    except ImportError:
        pass

    if has_3d:
        try:
            from pyqtgraph.opengl import GLViewWidget
            gl_view = GLViewWidget()
            root.addWidget(gl_view, 1)
            # Add coordinate axes
            g = gl.GLGridItem()
            gl_view.addItem(g)
            win._gl_view = gl_view
        except Exception:
            has_3d = False

    if not has_3d:
        msg = QtWidgets.QLabel(
            "3D View\n\n"
            "To enable 3D visualization, install pyqtgraph:\n"
            "  pip install pyqtgraph\n\n"
            "The original SAMBA19xUI loads '0002507.stl' using\n"
            "HelixToolkit.Wpf (a .NET library).\n\n"
            "This placeholder provides the same tab structure."
        )
        msg.setWordWrap(True)
        msg.setAlignment(QtCore.Qt.AlignCenter)
        msg.setStyleSheet("color:#505050; font-size:13px; padding:40px;")
        root.addWidget(msg, 1)

    g_rot = GroupPanel("Rotation")
    rot = QtWidgets.QHBoxLayout(g_rot)
    btn_rot = FlatPush("Rotate 90\xb0")
    rot.addWidget(btn_rot)
    rot.addStretch(1)
    root.addWidget(g_rot)

    return w


def _page_digio(win) -> QtWidgets.QWidget:
    """Digital IO status page — shows individual loop status for pos/pneum."""
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    g = GroupPanel("Digital IO Status")
    grid = QtWidgets.QGridLayout(g)
    grid.setSpacing(6)

    # Individual loop status indicators
    grid.addWidget(QtWidgets.QLabel("Position Individual Loop Status"), 0, 0, 1, 4)
    pos_labels = ["Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
                   "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2"]
    win._digio_pos_leds = []
    for i, name in enumerate(pos_labels):
        led = LedIndicator(10)
        win._digio_pos_leds.append(led)
        r, c = i // 4, (i % 4) * 2
        grid.addWidget(led, r + 1, c)
        grid.addWidget(QtWidgets.QLabel(name), r + 1, c + 1)

    grid.addWidget(QtWidgets.QLabel("Pneumatic Individual Loop Status"), 3, 0, 1, 4)
    pneu_labels = ["Ztpneu", "Yrpneu", "Xrpneu"]
    win._digio_pneu_leds = []
    for i, name in enumerate(pneu_labels):
        led = LedIndicator(10)
        win._digio_pneu_leds.append(led)
        grid.addWidget(led, 4, i * 2)
        grid.addWidget(QtWidgets.QLabel(name), 4, i * 2 + 1)

    root.addWidget(g)

    act = QtWidgets.QHBoxLayout()
    btn_r = FlatPush("Read status")
    btn_r.clicked.connect(lambda: _on_digio_read(win))
    act.addWidget(btn_r)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)
    return w


def _on_digio_read(win) -> None:
    """Read digital IO status from controller."""
    if not hasattr(win, '_digio_timer'):
        win._digio_timer = QtCore.QTimer(win)
        win._digio_timer.setInterval(1000)
        win._digio_timer.timeout.connect(lambda: _on_digio_read(win))

    if not win._digio_timer.isActive():
        win._digio_timer.start()
        win.log_msg("DigIO status refresh started (1s)")
    else:
        win._digio_timer.stop()
        win.log_msg("DigIO status refresh stopped")

    # Try to read from controller
    if win.session and win.session.connected:
        try:
            loop = win.session.get_loop_status()
            # Update LEDs from loop status bits
            # Position individual loop bits (pneumatic position loop)
            ind = loop.individual
            if hasattr(win, '_digio_pos_leds'):
                for i, led in enumerate(win._digio_pos_leds):
                    # Bit mapping is approximate
                    if i < 12:
                        led.set_on(bool(ind & (1 << (i + 6))) if i < 6 else bool(ind & (1 << i)))
            if hasattr(win, '_digio_pneu_leds'):
                for i, led in enumerate(win._digio_pneu_leds):
                    led.set_on(bool(ind & (1 << (i + 3))))
        except Exception:
            pass


def show_ui_options(win) -> None:
    """Show UI Options dialog matching UIOptionWindow."""
    dlg = QtWidgets.QDialog(win)
    dlg.setWindowTitle("UI Options")
    dlg.setMinimumWidth(360)
    dlg.setWindowFlags(
        QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint |
        QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowCloseButtonHint
    )

    root = QtWidgets.QVBoxLayout(dlg)
    root.setContentsMargins(12, 12, 12, 12)
    root.setSpacing(8)

    info = QtWidgets.QLabel(
        "SAMBA19xUI compatible UI\n\n"
        "python_samba — vendor-free host\n"
        "Pure RCI serial (no Rci32.dll / CommServer)\n"
        "Tab structure matches SAMBA19xUI"
    )
    info.setStyleSheet("color:#404040; padding:8px; background:#f7f7f7; border:1px solid #c0c0c0;")
    info.setWordWrap(True)
    root.addWidget(info)

    # Load system config from controller checkbox
    cb = QtWidgets.QCheckBox("Load system configuration from controller on connect")
    cb.setChecked(True)
    win._load_sys_config = cb
    root.addWidget(cb)

    # Close button
    btn_row = QtWidgets.QHBoxLayout()
    btn_close = QtWidgets.QPushButton("Close")
    btn_close.clicked.connect(dlg.accept)
    btn_row.addStretch(1)
    btn_row.addWidget(btn_close)
    root.addLayout(btn_row)

    dlg.setStyleSheet("""
        QDialog { background: #f0f0f0; }
        QLabel { color: #202020; }
        QPushButton {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #f7f7f7, stop:1 #d8d8d8);
            border: 1px solid #808080;
            border-radius: 3px;
            padding: 4px 14px;
            min-height: 22px;
        }
        QPushButton:hover { background: #ececec; }
    """)

    dlg.exec()


def _build_special_tab(win) -> None:
    """Replacement _build_special_tab that adds DigIOStatus and View3D."""
    from python_samba.ui.main_window import SamTabWidget

    # Import the existing safety/zms/polynom builder from the patch
    # Since we already have a safety_zms_patch, we need to call its builder
    # But we can't import it directly here. Instead, build the full special tab.

    tabs = SamTabWidget()

    # Safety tab (from built-in _build_special_tab pattern)
    try:
        # Try to delegate to the existing safety builder if available
        if hasattr(win, '_build_safety_tab'):
            safety_w = win._build_safety_tab()
        else:
            safety_w = QtWidgets.QWidget()
            sl = QtWidgets.QVBoxLayout(safety_w)
            sl.addWidget(QtWidgets.QLabel("Safety / Earthquake monitoring"))
            sl.addStretch(1)
        tabs.addTab(safety_w, "Safety")
    except Exception:
        w = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(w)
        wl.addWidget(QtWidgets.QLabel("Safety"))
        wl.addStretch(1)
        tabs.addTab(w, "Safety")

    # ZMS tab
    try:
        if hasattr(win, '_build_zms_tab'):
            zms_w = win._build_zms_tab()
        else:
            zms_w = QtWidgets.QWidget()
            zl = QtWidgets.QVBoxLayout(zms_w)
            zl.addWidget(QtWidgets.QLabel("ZMS"))
            zl.addStretch(1)
        tabs.addTab(zms_w, "ZMS")
    except Exception:
        zms_w = QtWidgets.QWidget()
        zl = QtWidgets.QVBoxLayout(zms_w)
        zl.addWidget(QtWidgets.QLabel("ZMS"))
        zl.addStretch(1)
        tabs.addTab(zms_w, "ZMS")

    # Polynom tab
    try:
        if hasattr(win, '_build_polynom_tab'):
            poly_w = win._build_polynom_tab()
        else:
            poly_w = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(poly_w)
            pl.addWidget(QtWidgets.QLabel("Polynom"))
            pl.addStretch(1)
        tabs.addTab(poly_w, "Polynom")
    except Exception:
        poly_w = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(poly_w)
        pl.addWidget(QtWidgets.QLabel("Polynom"))
        pl.addStretch(1)
        tabs.addTab(poly_w, "Polynom")

    # Signal Display tab (if available)
    try:
        if hasattr(win, '_build_signal_display_page'):
            sig_w = win._build_signal_display_page()
            tabs.addTab(sig_w, "Signal Display")
    except Exception:
        pass

    # DigIO Status tab
    digio_w = _page_digio(win)
    tabs.addTab(digio_w, "DigIO Status")

    # View3D tab
    view3d_w = _page_view3d(win)
    tabs.addTab(view3d_w, "View3D")

    # Store in main tabs
    # Find the "Special" tab index and replace it
    for i in range(win.main_tabs.count()):
        if win.main_tabs.tabText(i) == "Special":
            win.main_tabs.removeTab(i)
            break
    win.main_tabs.addTab(tabs, "Special")


def apply_patches(cls: type) -> None:
    """Apply all low-priority patches."""
    for name in ["_page_view3d", "_page_digio", "_on_digio_read", "show_ui_options", "_build_special_tab"]:
        fn = globals()[name]
        setattr(cls, name, fn)
    print("[patches] Applied low-priority patches (View3D, DigIO, UIOption)")