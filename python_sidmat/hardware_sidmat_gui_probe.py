"""Headless Qt UI probe against the live controller.

This exercises the same MainWindow connection and measurement-worker path as
the application, while keeping the trace change small and restoring the exact
original DGTIV values before disconnecting.
"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from python_sidmat.ui.main_window import MainWindow


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    report: dict[str, object] = {"port": port, "baud": baud, "steps": []}

    def record(name: str, status: str, detail: object) -> None:
        report["steps"].append({"name": name, "status": status, "detail": detail})
        print(f"{status:4} {name}: {detail}", flush=True)

    window = MainWindow()
    window.backend_cbx.setCurrentText("serial")
    window.port_cbx.setCurrentText(port)
    window.baud_cbx.setCurrentText(str(baud))
    original = None
    failure: BaseException | None = None
    try:
        # Use the real button path.  Calling _connect() directly while the
        # button is unchecked emits the toggled signal from inside _connect
        # and would open a second serial session in this probe.
        window.connect_btn.click()
        app.processEvents()
        if window.controller is None or not window.controller.connected:
            raise RuntimeError("MainWindow did not connect to the controller")
        original = window.controller.get_trace()
        record(
            "MainWindow connect/readback",
            "PASS",
            {
                "status": window.status_lbl.text(),
                "firmware": str(window.controller.version),
                "sample_frequency": window._sample_frequency,
                "trace": original.encode(),
            },
        )

        window.trace_info.length_edit.setText("64")
        window.trace_info.avg_edit.setText("1")
        window.trace_info.fast_load_check.setChecked(False)
        window._start_measurement()
        worker = window.worker
        if worker is None:
            raise RuntimeError("MainWindow did not create a measurement worker")
        if not worker.wait(120000):
            raise TimeoutError("MainWindow measurement worker did not finish")
        app.processEvents()
        raw = window._last_raw
        if raw is None or len(raw.channel(0)) != 64:
            raise RuntimeError(
                f"MainWindow measurement result incomplete: "
                f"{None if raw is None else len(raw.channel(0))}"
            )
        record(
            "MainWindow normal measurement",
            "PASS",
            {
                "status": window.status_lbl.text(),
                "samples": len(raw.channel(0)),
                "averages": raw.avg_num,
                "pwelch_bins": len(window._last_pwelch.freq),
            },
        )
    except BaseException as exc:
        failure = exc
        record("MainWindow probe", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if window.controller is not None and window.controller.connected and original is not None:
            try:
                window.controller.set_trace(original)
                restored = window.controller.get_trace().encode()
                if restored != original.encode():
                    raise AssertionError(
                        f"trace restore mismatch: expected {original.encode()}, got {restored}"
                    )
                record("MainWindow restore original trace", "PASS", restored)
            except BaseException as exc:
                failure = failure or exc
                record(
                    "MainWindow restore original trace",
                    "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
        window._disconnect()
        window.close()
        app.processEvents()

    report_path = "hardware_sidmat_gui_probe_report.json"
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"REPORT {report_path}", flush=True)
    return 2 if failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
