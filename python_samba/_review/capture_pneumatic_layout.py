"""Render the complete pneumatic left settings column for visual QA."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.main_window import MainWindow
from python_samba.ui.patches import apply_all_patches


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "_review/hardware_probe_results/control_ui_20260802/pneumatic_layout.png"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_all_patches(MainWindow, strict=True)
    window = MainWindow()
    window.resize(1920, 1200)
    for index in range(window.main_tabs.count()):
        if window.main_tabs.tabText(index) == "Pneumatic":
            window.main_tabs.setCurrentIndex(index)
            break
    for expander in (
        window.pneum_sensor_expander,
        window.pneum_valve_matrix_expander,
        window.pneum_valve_offsets_expander,
        window.pneum_iso_dither_expander,
    ):
        expander.set_expanded(True)
    window.pneum_ramp_expander.set_expanded(False)
    window.show()
    app.processEvents()

    scroll = window.findChild(QtWidgets.QScrollArea, "pneumaticSettingsScroll")
    if scroll is None or scroll.widget() is None:
        raise RuntimeError("pneumatic settings scroll area was not found")
    content = scroll.widget()
    content.resize(content.sizeHint())
    content.layout().activate()
    app.processEvents()

    image = QtGui.QImage(
        content.size(), QtGui.QImage.Format_ARGB32_Premultiplied
    )
    image.fill(QtCore.Qt.white)
    painter = QtGui.QPainter(image)
    content.render(painter, QtCore.QPoint())
    painter.end()
    if not image.save(str(output)):
        raise RuntimeError(f"could not save {output}")
    print(output.resolve())
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
