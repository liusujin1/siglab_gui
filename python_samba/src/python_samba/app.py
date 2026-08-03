"""GUI application entry point."""

from __future__ import annotations

import os
import sys


# The original SAMBA19xUI is pixel-oriented and its supplied reference
# screenshots use physical pixels.  Qt's automatic high-DPI scaling doubled
# every fixed width/height on the development workstation, which made the
# sidebar and all filter matrices much larger than the reference layout.
# Disable that implicit scaling before importing PySide6; the stylesheet and
# the widget dimensions below are already authored in physical pixels.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6 import QtGui, QtWidgets
    except ImportError:
        print(
            "PySide6 is required for the GUI.\n"
            '  py -3 -m pip install "python-samba[gui]"',
            file=sys.stderr,
        )
        return 1

    from python_samba.ui.main_window import MainWindow

    # Apply SAMBA19xUI gap patches BEFORE constructing the window
    # so that patched _build_* methods are used during __init__
    try:
        from python_samba.ui.patches import apply_all_patches
        report = apply_all_patches(MainWindow)
        if not report.ok:
            print(
                "[patches] Warning: incomplete UI extensions: "
                + ", ".join(report.failed),
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"[patches] Warning: patch application failed: {exc}")

    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setStyle("Fusion")
    # The supplied SAMBA19xUI captures use Arial metrics.  Several pages are
    # pixel-oriented, so using Segoe UI here changes label widths enough to
    # move controls and makes the interface look noticeably more compact.
    font = QtGui.QFont("Arial", 12)
    app.setFont(font)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
