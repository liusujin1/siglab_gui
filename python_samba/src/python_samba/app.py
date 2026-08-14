"""GUI application entry point."""

from __future__ import annotations

import os
import sys

from python_samba.runtime import configure_qt_dpi_environment


# The original SAMBA19xUI is pixel-oriented and its supplied reference
# screenshots use physical pixels.  Qt's automatic high-DPI scaling doubled
# every fixed width/height on the development workstation, which made the
# sidebar and all filter matrices much larger than the reference layout.
# Disable that implicit scaling before importing PySide6; the stylesheet and
# the widget dimensions below are already authored in physical pixels.
configure_qt_dpi_environment()


def main(argv: list[str] | None = None) -> int:
    from python_samba.runtime import consume_runtime_arguments, runtime_asset_path

    app_argv = consume_runtime_arguments(sys.argv if argv is None else argv)
    autostart_smoke = os.environ.get("SIGLAB_COMM_SERVER_AUTOSTART_SMOKE")
    if autostart_smoke:
        from python_samba.runtime import run_comm_server_autostart_smoke

        try:
            run_comm_server_autostart_smoke(autostart_smoke)
            return 0
        except BaseException as exc:
            print(f"Communication Server auto-start smoke failed: {exc}", file=sys.stderr)
            return 3
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        print(
            "PySide6 is required for the GUI.\n"
            '  py -3 -m pip install "python-samba[gui]"',
            file=sys.stderr,
        )
        return 1

    from python_samba.ui.main_window import LIVE_CURVE_IMPORT_ERROR, MainWindow

    if os.environ.get("SIGLAB_SMOKE_TEST") == "1" and LIVE_CURVE_IMPORT_ERROR:
        print(
            f"Real-time Curve import failed in frozen runtime: {LIVE_CURVE_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 2

    # Apply SAMBA19xUI gap patches BEFORE constructing the window
    # so that patched _build_* methods are used during __init__
    try:
        from python_samba.ui.patches import apply_all_patches
        apply_all_patches(MainWindow, strict=True)
    except Exception as exc:
        # A partially patched window mixes builders and callbacks from
        # different generations.  Refuse to start instead of exposing controls
        # that can fail only after the operator clicks them.
        print(f"[patches] Error: patch application failed: {exc}", file=sys.stderr)
        return 2

    app = QtWidgets.QApplication(app_argv)
    app.setStyle("Fusion")
    icon_path = runtime_asset_path("samba_icon.ico")
    if icon_path is not None:
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    # Use a readable fallback before MainWindow applies its monitor-aware font
    # scale.  Arial keeps the metrics close to the supplied SAMBA19xUI captures.
    font = QtGui.QFont("Arial", 14)
    app.setFont(font)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    if os.environ.get("SIGLAB_SMOKE_TEST") == "1":
        QtCore.QTimer.singleShot(750, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
