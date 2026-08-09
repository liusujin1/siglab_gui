"""Headless MainWindow regression probe against the live Sidmat controller."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from hardware_sidmat_full_probe import _restore, _snapshot, _state_equal
from python_sidmat.measurement.figurefile import load_idefigure, save_idefigure
from python_sidmat.measurement.settings import load_measurement_settings, save_measurement_settings
from python_sidmat.measurement.matfile import load_sidimat_raw, save_sidimat_raw
from python_sidmat.ui.main_window import MainWindow


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    report: dict[str, object] = {"port": port, "baud": baud, "steps": []}
    failures: list[str] = []

    def record(name: str, status: str, detail: object) -> None:
        report["steps"].append({"name": name, "status": status, "detail": detail})
        print(f"{status:4} {name}: {detail}", flush=True)

    def run(name: str, callback: Callable[[], object]) -> object | None:
        try:
            detail = callback()
        except Exception as exc:  # Continue with independent UI checks.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            record(name, "FAIL", f"{type(exc).__name__}: {exc}")
            return None
        record(name, "PASS", detail)
        return detail

    window = MainWindow()
    window.backend_cbx.setCurrentText("serial")
    window.port_cbx.setCurrentText(port)
    window.baud_cbx.setCurrentText(str(baud))
    state = None
    try:
        # Exercise the same visible Connect button as the application.
        window.connect_btn.click()
        app.processEvents()
        if window.controller is None or not window.controller.connected:
            raise RuntimeError("MainWindow did not connect to the real controller")
        state = _snapshot(window.controller)
        record(
            "MainWindow connect and complete readback",
            "PASS",
            {
                "status": window.status_lbl.text(),
                "firmware": str(window.controller.version),
                "sample_frequency": window._sample_frequency,
            },
        )

        run("UI Diagnostic Set button", lambda: _ui_set_diagnostic(window, state))
        run("UI Excitation Set button", lambda: _ui_set_excitation(window, state))
        run("UI noise filter ON/OFF toggle pair", lambda: _ui_toggle_filter(window, state, app))
        run("UI six velocity loop handlers", lambda: _ui_axis_handlers(window, state))
        run("UI Helping Hand velocity/position routing", lambda: _ui_helping_hand(window, state, app))
        run("UI measurement settings JSON roundtrip/apply", lambda: _ui_settings(window, state))
        run("UI live measurement and pwelch plots", lambda: _ui_measurement(window, app))
        run("UI offline filter and closed-loop generation", lambda: _ui_offline(window))
        run("UI plot toolbar actions", lambda: _ui_toolbar(window))
        run("live raw and figure file roundtrip", lambda: _file_roundtrip(window))
    except BaseException as exc:
        failures.append(f"GUI probe setup: {type(exc).__name__}: {exc}")
        record("GUI probe setup", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if window.controller is not None and window.controller.connected and state is not None:
            try:
                _restore(window.controller, state)
                restored = _snapshot(window.controller)
                if not _state_equal(state, restored):
                    raise AssertionError("post-restore controller state differs from snapshot")
                record("MainWindow restore exact controller state", "PASS", "all readbacks match")
            except BaseException as exc:
                failures.append(f"MainWindow restore: {type(exc).__name__}: {exc}")
                record("MainWindow restore exact controller state", "FAIL", f"{type(exc).__name__}: {exc}")
        window._disconnect()
        window.close()
        app.processEvents()

    report["failure_count"] = len(failures)
    if failures:
        report["failures"] = failures
    report_path = Path.cwd() / "hardware_sidmat_full_gui_probe_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT {report_path}", flush=True)
    return 2 if failures else 0


def _check_unchanged(window: MainWindow, state: dict[str, object]) -> str:
    current = _snapshot(window.controller)
    if not _state_equal(state, current):
        raise AssertionError("controller state changed unexpectedly")
    return "same-value write/readback verified"


def _filter_enabled(value: object) -> bool:
    return str(value).strip().upper() in {"N", "ON", "1", "TRUE"}


def _ui_set_diagnostic(window: MainWindow, state: dict[str, object]) -> str:
    window.diag_set_btn.click()
    return _check_unchanged(window, state)


def _ui_set_excitation(window: MainWindow, state: dict[str, object]) -> str:
    window.excitation_widget.set_btn.click()
    return _check_unchanged(window, state)


def _ui_toggle_filter(window: MainWindow, state: dict[str, object], app: QtWidgets.QApplication) -> str:
    original_on = _filter_enabled(state["filter_usage"])
    # First exercise the slot directly so a failed button test distinguishes a
    # Qt signal issue from a controller/protocol issue.
    window._toggle_noise_filter(True)
    direct_on = str(window.controller.get_noise_filter_usage()).strip()
    window._toggle_noise_filter(False)
    direct_off = str(window.controller.get_noise_filter_usage()).strip()
    if direct_on.upper() != "N" or direct_off.upper() != "F":
        raise AssertionError(f"direct filter handler N/F mismatch: {direct_on!r}, {direct_off!r}")
    window.excitation_widget.set_filter_usage(original_on)
    window.controller.set_noise_filter_usage("N" if original_on else "F")
    before_ui = window.excitation_widget.filter_led.is_on()
    window.excitation_widget.filter_led.click()
    app.processEvents()
    toggled_on = _filter_enabled(window.controller.get_noise_filter_usage())
    if toggled_on == original_on:
        # Leave the device safe even when this diagnostic assertion fires.
        window.controller.set_noise_filter_usage("N" if original_on else "F")
        raise AssertionError(
            f"filter ON/OFF first toggle did not change readback: "
            f"before_ui={before_ui}, after_ui={window.excitation_widget.filter_led.is_on()}, "
            f"status={window.status_lbl.text()!r}, "
            f"readback={window.controller.get_noise_filter_usage()!r}"
        )
    window.excitation_widget.filter_led.click()
    app.processEvents()
    return f"direct N/F and Qt button toggle verified; {_check_unchanged(window, state)}"


def _ui_axis_handlers(window: MainWindow, state: dict[str, object]) -> str:
    for index, enabled in enumerate(state["axis_states"][:6]):
        # Pass the existing state explicitly.  This exercises the MainWindow
        # handler and both read/modify/write paths without moving an axis.
        window._on_axis_clicked(index, bool(enabled))
    return _check_unchanged(window, state)


def _ui_helping_hand(window: MainWindow, state: dict[str, object], app: QtWidgets.QApplication) -> str:
    # Velocity: Stage1 of velocity axis 0.
    window.mh_loop_cbx.setCurrentIndex(0)
    window.mh_stage_cbx.setCurrentIndex(1)
    window._on_mh_axis_clicked(0, True)
    app.processEvents()
    diag0, diag1 = window.controller.get_diagnostic_outputs()
    inject = window.controller.get_noise_inject()
    if diag0.encode() != (3, 0, 0) or diag1.encode() != (2, 0, 0) or inject.encode() != (4, 0, 0):
        raise AssertionError(
            f"velocity route mismatch: {diag0.encode()}, {diag1.encode()}, {inject.encode()}"
        )

    # Position: Stage1 of position axis 1.
    window.mh_loop_cbx.setCurrentIndex(1)
    window._on_mh_axis_clicked(1, True)
    app.processEvents()
    diag0, diag1 = window.controller.get_diagnostic_outputs()
    inject = window.controller.get_noise_inject()
    if diag0.encode() != (3, 0, 0) or diag1.encode() != (5, 1, 0) or inject.encode() != (5, 1, 0):
        raise AssertionError(
            f"position route mismatch: {diag0.encode()}, {diag1.encode()}, {inject.encode()}"
        )

    _restore(window.controller, state)
    if not _state_equal(state, _snapshot(window.controller)):
        raise AssertionError("Helping Hand restore mismatch")
    return "velocity and position diagnostic/injection routes verified"


def _ui_settings(window: MainWindow, state: dict[str, object]) -> str:
    # Refresh the widgets after the Helping Hand test before serializing them.
    window._refresh_controller()
    window._refresh_excitation_readback()
    payload = window._measurement_settings_payload()
    with TemporaryDirectory(prefix="sidmat_settings_") as temp:
        path = Path(temp) / "measurement.sidmat.json"
        save_measurement_settings(payload, path)
        loaded = load_measurement_settings(path)
        expected = dict(payload)
        expected.setdefault("schema", "python_sidmat.measurement")
        expected.setdefault("version", 1)
        if loaded != expected:
            raise AssertionError("settings JSON payload changed during roundtrip")
        window._apply_measurement_settings(loaded)
    if not _state_equal(state, _snapshot(window.controller)):
        raise AssertionError("applying a same-state settings file changed hardware state")
    return "settings JSON save/load/apply and hardware readback verified"


def _ui_measurement(window: MainWindow, app: QtWidgets.QApplication) -> str:
    window.trace_info.length_edit.setText("100")
    window.trace_info.avg_edit.setText("3")
    window.trace_info.fast_load_check.setChecked(False)
    # Use the actual visible Start button and keep the Qt event loop alive
    # while the worker runs, exactly as an interactive user does.
    window.trace_info.start_btn.click()
    worker = window.worker
    if worker is None:
        raise RuntimeError("MainWindow did not create a measurement worker")
    deadline = time.monotonic() + 120.0
    while worker.isRunning():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise TimeoutError("MainWindow live measurement did not finish")
        time.sleep(0.02)
    app.processEvents()
    raw = window._last_raw
    if raw is None or len(raw.channel(0)) != 300 or window._last_pwelch is None:
        raise AssertionError(
            "live UI measurement result or pwelch result is incomplete: "
            f"status={window.status_lbl.text()!r}, "
            f"raw_samples={None if raw is None else len(raw.channel(0))}, "
            f"raw_avg={None if raw is None else raw.avg_num}, "
            f"pwelch={window._last_pwelch is not None}, "
            f"start_text={window.trace_info.start_btn.text()!r}"
        )
    plot_counts = [len(view._pw.listDataItems()) for view in window._plot_widgets()]
    if not all(plot_counts):
        raise AssertionError(f"measurement completed but plot curves are missing: {plot_counts}")
    return {
        "status": window.status_lbl.text(),
        "samples": len(raw.channel(0)),
        "averages": raw.avg_num,
        "pwelch_bins": len(window._last_pwelch.freq),
        "plot_curves": plot_counts,
    }


def _ui_offline(window: MainWindow) -> str:
    window._accept_offline_filter()
    if window._offline_filtered is None:
        raise AssertionError("offline filter did not produce a filtered TF")
    window._generate_offline_cl()
    if window._offline_cl is None:
        raise AssertionError("closed-loop TF was not generated")
    return "offline filter and closed-loop TF produced finite plot data"


def _ui_toolbar(window: MainWindow) -> str:
    window._toggle_grid()
    window._toggle_grid()
    window._set_theme(True)
    window._set_theme(False)
    window._toggle_time_plot()
    window._toggle_frf_plot()
    window._zoom_fit()
    window._copy_active_plot()
    return "grid/theme/view/zoom/copy actions completed"


def _file_roundtrip(window: MainWindow) -> str:
    raw = window._last_raw
    if raw is None:
        raise AssertionError("no live raw data available for file roundtrip")
    figure = window._collect_figure()
    with TemporaryDirectory(prefix="sidmat_files_") as temp:
        root = Path(temp)
        raw_path = root / "measurement.sidimat19x"
        save_sidimat_raw([raw], raw_path)
        loaded_raw = load_sidimat_raw(raw_path)
        if len(loaded_raw) != 1 or len(loaded_raw[0].ch0) != len(raw.channel(0)):
            raise AssertionError("raw file roundtrip sample count mismatch")
        figure_path = root / "measurement.idefigure"
        save_idefigure(figure, figure_path)
        loaded_figure = load_idefigure(figure_path)
        if len(loaded_figure.models) != len(figure.models):
            raise AssertionError("figure file roundtrip model count mismatch")
    return {"raw_samples": len(raw.channel(0)), "figure_models": len(figure.models)}


if __name__ == "__main__":
    raise SystemExit(main())
