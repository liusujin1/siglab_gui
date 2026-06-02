from __future__ import annotations

import argparse
from pathlib import Path
import sys

from python_vna.app import resource_path
from python_vna.diagnostics import append_log, enable_fault_log
from python_vna.optional import require


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Python VNA conversion tool.")
    parser.add_argument("paths", nargs="*", help="Optional analysis files to load at startup.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    enable_fault_log()
    append_log("conversion app start")
    args = parse_args(argv)
    QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
    QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
    from python_vna.ui.analysis_viewer import AnalysisViewer

    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    icon_path = resource_path("assets/python_vna_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = AnalysisViewer(None, derived_only=True)
    startup_paths = [Path(path) for path in args.paths]
    if startup_paths:
        window._load_paths(startup_paths, quiet_failures=True)
    window.show()
    append_log("conversion window shown")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
