from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from python_vna.controller import VnaController
from python_vna.diagnostics import append_log, enable_fault_log
from python_vna.optional import require
from python_vna.storage import default_session_config, load_legacy_vna


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path


def build_backend(name: str):
    if name == "ni":
        from python_vna.daq.ni import NIDaqBackend

        return NIDaqBackend()
    if name == "simulated":
        from python_vna.daq.simulated import SimulatedDaqBackend

        return SimulatedDaqBackend()
    raise ValueError(f"Unsupported backend '{name}'.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python VNA for NI USB-4431.")
    parser.add_argument(
        "--probe-ni-devices-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--backend",
        choices=["simulated", "ni"],
        default="ni",
        help="DAQ backend to use.",
    )
    parser.add_argument("--device", default=None, help="Preferred device name.")
    return parser.parse_args(argv)


def default_vna_path() -> Path:
    return resource_path("dsa/vna/default.vna")


def load_startup_session(path: Path | None = None):
    default_path = path or default_vna_path()
    if default_path.exists():
        try:
            return load_legacy_vna(default_path)
        except Exception:
            pass
    return None


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    enable_fault_log()
    append_log("app start")
    args = parse_args(argv)
    if args.probe_ni_devices_json:
        from python_vna.daq.device_probe import main as probe_main

        return probe_main(["--json"])
    QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
    QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
    QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
    from python_vna.ui.main_window import MainWindow

    append_log("qapplication create: begin")
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    append_log("qapplication create: end")
    icon_path = resource_path("assets/python_vna_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    append_log(f"backend build: begin {args.backend}")
    backend = build_backend(args.backend)
    append_log(f"backend build: end {args.backend}")
    session_config = default_session_config()
    controller = VnaController(backend, session_config)
    append_log("main window construct: begin")
    window = MainWindow(controller, session_config)
    append_log("main window construct: end")
    # Keep the combo box in sync with the backend selected at process startup
    # without firing _backend_changed(), which would synchronously enumerate NI
    # devices before the first window paint on some driver installations.
    window.backend_combo.blockSignals(True)
    window.backend_combo.setCurrentText(args.backend)
    window.backend_combo.blockSignals(False)
    if args.device:
        window.device_combo.addItem(args.device, args.device)
        window.device_combo.setCurrentText(args.device)
    window.show()
    append_log("main window shown")
    if _env_flag("PYTHON_VNA_SKIP_DEFAULT_VNA"):
        append_log("startup default vna: skipped by env")
    else:
        append_log("startup default vna: scheduled")
        QtCore.QTimer.singleShot(
            1000, lambda: window.load_startup_session_async(default_vna_path())
        )
    if _env_flag("PYTHON_VNA_SKIP_STARTUP_DEVICE_REFRESH"):
        append_log("startup device refresh: skipped by env")
    else:
        append_log("startup device refresh: scheduled")
        QtCore.QTimer.singleShot(1500, window.refresh_devices_async)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
