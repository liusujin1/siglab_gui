"""python_sidmat GUI entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6 import QtWidgets

    from python_sidmat.ui.main_window import MainWindow
    from python_sidmat.ui.theme import apply_samba_theme

    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    apply_samba_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
