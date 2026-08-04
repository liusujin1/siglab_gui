"""Read-only hardware probe for FF Error Path clicks and DigIO status mapping."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from python_samba.services.safety import SafetyGate
from python_samba.services.session import open_serial
from python_samba.ui.main_window import MainWindow, SamTabWidget
from python_samba.ui.patches import apply_all_patches
from python_samba.ui.widgets import FilterDlg


def _select_page(win: MainWindow, main_title: str, sub_title: str) -> None:
    main_index = next(
        index
        for index in range(win.main_tabs.count())
        if win.main_tabs.tabText(index) == main_title
    )
    win.main_tabs.setCurrentIndex(main_index)
    tabs = win.main_tabs.widget(main_index).findChild(SamTabWidget)
    sub_index = next(
        index for index in range(tabs.count()) if tabs.tabText(index) == sub_title
    )
    tabs.setCurrentIndex(sub_index)


def run_probe(port: str, baudrate: int) -> dict[str, object]:
    session = open_serial(port, baudrate, readonly=True, timeout=3.0)
    version = session.open()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    win = PatchedMainWindow()
    win.session = session
    win.gate = SafetyGate(session)
    win.show()
    app.processEvents()

    original_exec = FilterDlg.exec
    original_critical = QtWidgets.QMessageBox.critical
    critical_errors: list[str] = []
    try:
        words = session.get_pos_pneum_digital_status()
        error_filters = {
            f"axis{axis}_stage{stage}": session.get_ff_filter(axis, stage).filter_type
            for axis in range(6)
            for stage in (6, 7)
        }

        FilterDlg.exec = lambda _dialog: QtWidgets.QDialog.Rejected

        def capture_critical(*args, **_kwargs):
            critical_errors.append(str(args[2] if len(args) > 2 else args))
            return QtWidgets.QMessageBox.Ok

        QtWidgets.QMessageBox.critical = capture_critical
        _select_page(win, "Feed Forward", "FF Tuning")
        app.processEvents()
        error_cell = win.ff_err_buttons[(5, 0)]
        QtTest.QTest.mouseClick(
            error_cell,
            QtCore.Qt.LeftButton,
            pos=error_cell.rect().center(),
        )
        app.processEvents()
        if win._ff_active_stage != 0:
            raise RuntimeError(
                f"Error Path click retained stage {win._ff_active_stage}; expected 0"
            )
        if critical_errors:
            raise RuntimeError("; ".join(critical_errors))

        _select_page(win, "Status", "DigIO Status")
        win._ensure_controller_capabilities()
        win._on_digio_read()
        app.processEvents()
        # Digital outputs can change while pages are being selected.  Compare
        # the lamps with the exact BGSST sample used for the visible refresh.
        display_words = getattr(win, "_digio_last_words", (words[2], words[3]))
        input_word, output_word = map(int, display_words)
        expected_inputs = [bool(input_word & (1 << bit)) for bit in range(14)]
        expected_outputs = [bool(output_word & (1 << bit)) for bit in range(14)]
        actual_inputs = [bool(led._is_on) for led in win._digio_input_leds[:14]]
        actual_outputs = [bool(led._is_on) for led in win._digio_output_leds[:14]]
        board_id = ((input_word & 0xFC000) >> 14) & 0x1F
        reserve = output_word & 0xFC000
        if actual_inputs != expected_inputs:
            raise RuntimeError("DigIO input lamp mapping mismatch")
        if actual_outputs != expected_outputs:
            raise RuntimeError("DigIO output lamp mapping mismatch")
        if win._digio_input_leds[14].text() != str(board_id):
            raise RuntimeError("MBoardID mapping mismatch")
        if win._digio_output_leds[14].text() != str(reserve):
            raise RuntimeError("Reserve mapping mismatch")
        if win._digio_output_names[5] != "OCOUT6":
            raise RuntimeError("DigIO output bit 5 label is not OCOUT6")

        return {
            "status": "PASS",
            "version": str(version),
            "bgsst": list(words),
            "display_bgsst": [input_word, output_word],
            "digio": {
                "input_names": list(win._digio_input_names),
                "input_states": [int(value) for value in actual_inputs],
                "mboard_id": board_id,
                "output_names": list(win._digio_output_names),
                "output_states": [int(value) for value in actual_outputs],
                "reserve": reserve,
            },
            "error_path": {
                "clicked_axis": 5,
                "grid_stage": win._ff_active_stage,
                "protocol_stage": 6,
                "filters": error_filters,
                "critical_errors": critical_errors,
            },
        }
    finally:
        FilterDlg.exec = original_exec
        QtWidgets.QMessageBox.critical = original_critical
        win.close()
        app.processEvents()
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM1")
    parser.add_argument("--baudrate", type=int, default=57600)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report = run_probe(args.port, args.baudrate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = args.output_dir / f"hardware_error_digio_{stamp}_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"REPORT {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
