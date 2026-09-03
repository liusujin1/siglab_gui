from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from python_vna.diagnostics import append_log, enable_fault_log
from python_vna.optional import require
from python_vna.resources import resource_path
from python_vna.update_client import cleanup_stale_updater_runner


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Python VNA vibration diagnostic software.")
    parser.add_argument("paths", nargs="*", help="Optional diagnostic files to load at startup.")
    return parser.parse_args(argv)


def configure_qt_rendering() -> None:
    enabled = os.environ.get("PYTHON_VNA_FORCE_SOFTWARE_OPENGL", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")


def main(argv: list[str] | None = None) -> int:
    configure_qt_rendering()
    enable_fault_log()
    append_log("diagnostic app start")
    cleanup_stale_updater_runner()
    args = parse_args(argv)
    QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
    QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
    if os.environ.get("QT_OPENGL", "").strip().lower() == "software":
        QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
        if hasattr(QtCore.Qt, "AA_UseSoftwareOpenGL"):
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseSoftwareOpenGL, True)
    from python_vna.diagnostic.shell import DiagnosticMainWindow

    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setStyle("Fusion")
    font = QtGui.QFont("Segoe UI", 9)
    font.setStyleHint(QtGui.QFont.SansSerif)
    app.setFont(font)
    icon_path = resource_path("assets/vianalysis_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = DiagnosticMainWindow(startup_paths=[Path(path) for path in args.paths])
    window.show()
    append_log("diagnostic window shown")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
