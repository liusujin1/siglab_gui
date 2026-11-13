from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from python_vna.controller import VnaController
from python_vna.diagnostics import append_log, enable_fault_log
from python_vna.optional import require
from python_vna.resources import resource_path
from python_vna.storage import default_session_config, load_legacy_vna
from python_vna.update_client import cleanup_stale_updater_runner

def build_backend(name: str, *, netdaq_settings=None, replay_path=None):
    if name == "ni":
        from python_vna.daq.ni import NIDaqBackend

        return NIDaqBackend()
    if name == "simulated":
        from python_vna.daq.simulated import SimulatedDaqBackend

        return SimulatedDaqBackend()
    if name == "netdaq":
        from python_vna.daq.netdaq import NetDaqBackend

        return NetDaqBackend(netdaq_settings)
    if name == "replay":
        from python_vna.daq.replay import ReplayBackend

        if not replay_path:
            raise ValueError("离线回放需要选择数据文件或目录")
        return ReplayBackend(replay_path)
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
        choices=["simulated", "ni", "netdaq", "replay"],
        default="ni",
        help="DAQ backend to use.",
    )
    parser.add_argument("--device", default=None, help="Preferred device name.")
    parser.add_argument("--replay-path", default="", help="Offline VNA/DASP/continuous recording source.")
    parser.add_argument("--netdaq-host", default="", help="Explicit NetDAQ IP/hostname; no automatic default.")
    parser.add_argument("--netdaq-profile", default="", help="Verified NetDAQ device profile JSON.")
    parser.add_argument("--netdaq-transport", choices=["tcp", "usb"], default="tcp")
    parser.add_argument("--netdaq-usb-index", type=int, default=0)
    parser.add_argument("--netdaq-probe-channels", default="", help="Explicit physical channel numbers, comma-separated.")
    parser.add_argument("--netdaq-legacy-port", type=int, default=60000)
    parser.add_argument("--netdaq-stream-port", type=int, default=60003)
    parser.add_argument("--probe-netdaq-json", action="store_true", help="Read-only NetDAQ probe, without GUI.")
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


def configure_live_plot_rendering() -> bool:
    pg = require("pyqtgraph", "python -m pip install -e .[gui]")
    use_opengl = not _env_flag("PYTHON_VNA_DISABLE_OPENGL")
    pg.setConfigOptions(useOpenGL=use_opengl, antialias=use_opengl)
    return use_opengl


def main(argv: list[str] | None = None) -> int:
    enable_fault_log()
    append_log("app start")
    cleanup_stale_updater_runner()
    args = parse_args(argv)
    if args.probe_ni_devices_json:
        from python_vna.daq.device_probe import main as probe_main

        return probe_main(["--json"])
    netdaq_settings = None
    if args.backend == "netdaq" or args.probe_netdaq_json:
        from python_vna.daq.netdaq import NetDaqBackend, NetDaqSettings

        netdaq_settings = NetDaqSettings(
            host=args.netdaq_host, legacy_port=args.netdaq_legacy_port,
            stream_port=args.netdaq_stream_port, profile_path=args.netdaq_profile,
            transport=args.netdaq_transport, usb_index=args.netdaq_usb_index)
        if args.probe_netdaq_json:
            import json

            try:
                channels = tuple(int(n.strip()) for n in args.netdaq_probe_channels.split(",")) if args.netdaq_probe_channels else ()
                print(json.dumps(NetDaqBackend(netdaq_settings).probe(channels), ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False))
                return 1
            return 0
    QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
    QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
    QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
    from python_vna.ui.main_window import MainWindow

    append_log("qapplication create: begin")
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setStyle("Fusion")
    font = QtGui.QFont("Segoe UI", 9)
    font.setStyleHint(QtGui.QFont.SansSerif)
    app.setFont(font)
    append_log("qapplication create: end")
    use_opengl = configure_live_plot_rendering()
    append_log(f"plot rendering: opengl={use_opengl} antialias={use_opengl}")
    icon_path = resource_path("assets/python_vna_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    append_log(f"backend build: begin {args.backend}")
    backend = build_backend(args.backend, netdaq_settings=netdaq_settings, replay_path=args.replay_path)
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
            1000, window.load_startup_session_async
        )
    if _env_flag("PYTHON_VNA_SKIP_STARTUP_DEVICE_REFRESH"):
        append_log("startup device refresh: skipped by env")
    else:
        append_log("startup device refresh: scheduled")
        QtCore.QTimer.singleShot(1500, window.refresh_devices_async)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
