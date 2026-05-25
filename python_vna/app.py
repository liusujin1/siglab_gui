from __future__ import annotations

import argparse
from pathlib import Path
import sys

from python_vna.controller import VnaController
from python_vna.daq import NIDaqBackend, SimulatedDaqBackend
from python_vna.diagnostics import append_log, enable_fault_log
from python_vna.optional import require
from python_vna.storage import default_session_config, load_legacy_vna


def build_backend(name: str):
    if name == "ni":
        return NIDaqBackend()
    if name == "simulated":
        return SimulatedDaqBackend()
    raise ValueError(f"Unsupported backend '{name}'.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python VNA for NI USB-4431.")
    parser.add_argument(
        "--backend",
        choices=["simulated", "ni"],
        default="ni",
        help="DAQ backend to use.",
    )
    parser.add_argument("--device", default=None, help="Preferred device name.")
    return parser.parse_args(argv)


def default_vna_path() -> Path:
    return Path(__file__).resolve().parents[1] / "dsa" / "vna" / "default.vna"


def load_startup_session(path: Path | None = None):
    default_path = path or default_vna_path()
    if default_path.exists():
        try:
            return load_legacy_vna(default_path)
        except Exception:
            pass
    return None


def main(argv: list[str] | None = None) -> int:
    enable_fault_log()
    append_log("app start")
    args = parse_args(argv)
    QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
    from python_vna.ui.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    backend = build_backend(args.backend)
    startup_session = load_startup_session()
    session_config = startup_session.config if startup_session is not None else default_session_config()
    controller = VnaController(backend, session_config)
    if startup_session is not None:
        controller.state.measurement = startup_session.measurement
    window = MainWindow(controller, session_config)
    if startup_session is not None:
        window._current_source_path = startup_session.source_path
        window._update_window_title()
        if startup_session.measurement is not None:
            window._plot_measurement(startup_session.measurement)
    window.backend_combo.setCurrentText(args.backend)
    if args.device:
        window.device_combo.addItem(args.device, args.device)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
