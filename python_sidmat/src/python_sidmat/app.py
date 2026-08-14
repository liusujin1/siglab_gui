"""python_sidmat GUI entry point."""

from __future__ import annotations

import os
import sys

from python_samba.runtime import configure_qt_dpi_environment


configure_qt_dpi_environment()


def main(argv: list[str] | None = None) -> int:
    from python_samba.runtime import consume_runtime_arguments, runtime_asset_path
    from PySide6 import QtCore, QtGui, QtWidgets

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

    from python_sidmat.ui.main_window import MainWindow

    app = QtWidgets.QApplication(app_argv)
    icon_path = runtime_asset_path("sidmat_icon.ico")
    if icon_path is not None:
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    if os.environ.get("SIGLAB_SMOKE_TEST") == "1":
        QtCore.QTimer.singleShot(750, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
