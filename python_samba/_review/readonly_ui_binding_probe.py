"""Read-only live-controller probe for the operator-visible UI bindings."""

from __future__ import annotations

import os
import re
import sys
import math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from python_samba.services.session import open_serial
from python_samba.ui.main_window import LEGACY_MOTOR_OFFSET_UI_TO_WIRE, MainWindow
from python_samba.ui.patches import apply_all_patches


SCIENTIFIC_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+$"
)


def _tree_row(tree: QtWidgets.QTreeWidget, name: str) -> list[str]:
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.text(0) == name:
            return [item.text(column) for column in range(tree.columnCount())]
    return []


def _protocol_int(value) -> int:
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        if any(character in "abcdefABCDEF" for character in text):
            return int(text, 16)
        return int(float(text))


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 57600
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    QtWidgets.QMessageBox.critical = staticmethod(
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.Ok
    )
    apply_all_patches(MainWindow, strict=True)
    window = MainWindow()
    window._refresh_timer.stop()

    session = open_serial(port, baudrate, readonly=True, timeout=3.0)
    try:
        print("VERSION", session.open())
        assert session.readonly
        window.session = session
        window._controller_capabilities_loaded = False
        window._ensure_controller_capabilities()
        print(
            "CAPABILITIES",
            "inputs=", window._input_signal_count,
            "proximity=", window._proximity_count,
        )
        capability_widgets = {
            "cascaded_position": window.pos_cascaded_expander,
            "pneumatic_ramp": window.pneum_ramp_expander,
            "analysis": window.analysis_logging_group,
        }
        capability_visibility = {
            feature: not widget.isHidden()
            for feature, widget in capability_widgets.items()
        }
        for feature, visible in capability_visibility.items():
            assert visible is window._supports_controller_feature(feature)
        special_visibility = {
            window.special_tabs.tabText(index):
                window.special_tabs.isTabVisible(index)
            for index in range(window.special_tabs.count())
        }
        assert special_visibility["Safety"] is window._supports_controller_feature(
            "safety"
        )
        assert special_visibility["System Safety"] is window._supports_controller_feature(
            "zms"
        )
        print("CAPABILITY_UI", capability_visibility, special_visibility)

        # Exercise the same full-page read path used when the operator opens
        # Pneumatic.  In particular, verify the legacy command order is kept:
        # Soft-Up Height, Setpoint, Mode Tolerance.
        window._on_pneu_read_all()
        pneumatic_config = session.get_pneumatic_config()
        pneumatic_ui = [
            window.pneum_float_softup.text(),
            window.pneum_float_setpoint.text(),
            window.pneum_float_mode_tol.text(),
        ]
        assert [int(float(value)) for value in pneumatic_ui] == [
            int(float(value)) for value in pneumatic_config
        ]
        assert hasattr(window, "_on_pneu_input_matrix_changed")
        assert hasattr(window, "_on_pneu_output_matrix_changed")
        print("PNEUMATIC_FLOATATION_CONFIG", pneumatic_ui)
        dither_frequency = session.get_dither_frequency()
        assert math.isclose(
            float(window.pneum_dither_freq.text()),
            dither_frequency,
            rel_tol=0.0,
            abs_tol=1e-7,
        )
        print("PNEUMATIC_DITHER_FREQUENCY", window.pneum_dither_freq.text())

        window._read_system_setting_reference()
        config_tokens = session.get_controller_config()
        config_mask = _protocol_int(config_tokens[0]) if config_tokens else 0
        config_bits = (0x01, 0x02, 0x04, 0x10, 0x20, 0x40, 0x80)
        assert [lamp.text() for lamp in window.system_loop_lamps] == [
            "ON" if config_mask & bit else "OFF" for bit in config_bits
        ]
        print(
            "CONTROLLER_LOOP_CONFIGURATION",
            hex(config_mask),
            [lamp.text() for lamp in window.system_loop_lamps],
        )
        print(
            "CONTROLLER_SIGNALS",
            "monitor=", window.perf_signal.text(),
            "monitor_tokens=", window.perf_signal.property("io_tokens"),
            "switch=", window.sw_signal.text(),
            "switch_tokens=", window.sw_signal.property("io_tokens"),
        )

        window.on_ff_status_read_classic()
        window.on_ff_read_gains()
        window.on_pff_read_all_filters()
        loop = session.get_loop_status()
        position_word, pneumatic_word, _digital_in, _digital_out = (
            session.get_pos_pneum_digital_status()
        )
        expected_ff_individual = [
            "ON" if loop.individual & (1 << axis) else "OFF"
            for axis in range(6)
        ]
        expected_pff_individual = [
            "ON" if pneumatic_word & (1 << axis) else "OFF"
            for axis in range(3)
        ]
        assert [lamp.text() for lamp in window.ff_individual_loop_leds] == (
            expected_ff_individual
        )
        assert [lamp.text() for lamp in window.pff_individual_loop_leds] == (
            expected_pff_individual
        )
        assert all(lamp._clickable for row in window.ff_status_rows for lamp in row)
        assert all(lamp._clickable for row in window.pff_status_rows for lamp in row)
        assert callable(getattr(window.protection_led, "click", None))
        assert not hasattr(window, "write_protection_switch")
        print("FF_INDIVIDUAL_LOOPS", expected_ff_individual)
        print("PFF_INDIVIDUAL_LOOPS", expected_pff_individual)
        print(
            "FF_TUNING_CHANNELS",
            [combo.currentText() for combo in window.ff_source_cbx],
        )
        print(
            "FF_GAINS_CHANNELS",
            [button.text() for button in window.ff_source_buttons],
        )

        window.on_adc_read()
        adc_set = session.get_adc_set_number()
        adc_counts = (0, 6, 12, 18, 24, 30, 36, 40)
        assert window.adc_set_num.currentIndex() == adc_set
        assert window.adc_set_num.currentText() == str(adc_counts[adc_set])
        print("USED_ADC_NUM", adc_set, "->", window.adc_set_num.currentText())

        window.on_motor_prot_read()
        linear_12 = bool(
            window._controller_features and "SALMO" in window._controller_features
        )
        if linear_12:
            raw_offsets = session.get_linear_motor_offsets()
            assert [float(editor.text()) for editor in window.mot_offsets] == raw_offsets
        else:
            raw_offsets = session.get_motor_offsets()
            for ui_index, wire_index in LEGACY_MOTOR_OFFSET_UI_TO_WIRE.items():
                assert math.isclose(
                    float(window.mot_offsets[ui_index].text()),
                    float(raw_offsets[wire_index]),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            for ui_index in (2, 5, 8, 11):
                assert window.mot_offsets[ui_index].isReadOnly()
                assert window.mot_offsets[ui_index].text() == ""
        print(
            "MOTOR_OFFSET_MODE",
            "SALMO-12" if linear_12 else "CGMOV-8+ISO3",
            [editor.text() for editor in window.mot_offsets],
        )

        window.on_prox_read_classic()
        window._refresh_position_live_state()
        visible_names = ["Prox1", "Prox2", "Prox3", "ProxH1", "ProxH2", "ProxH3"]
        print(
            "POSITION_PROXY_VALUES",
            {name: window.proxy_value_labels[name].text() for name in visible_names},
        )
        print("POSITION_PROXY_VALUE_ROW", _tree_row(window.prox_status_tree, "Value"))
        print("POSITION_PROXY_ERROR_ROW", _tree_row(window.prox_status_tree, "Error"))
        # Use the exact sample cached by the preceding live refresh; taking a
        # second hardware sample can legitimately differ by one or more digits.
        raw_proximity = list(window._last_proximity_values)
        window.proxy_si_unit_edits["Prox1"].setText("2")
        window._on_proximity_si_unit_changed()
        scaled_prox1 = float(window.proxy_value_labels["Prox1"].text())
        assert math.isclose(
            scaled_prox1, raw_proximity[0] / 2.0, rel_tol=0.0, abs_tol=0.051
        )
        print("POSITION_SI_SCALE", raw_proximity[0], "digits ->", scaled_prox1, "um")

        loop = session.get_loop_status()
        window._refresh_status_loop_state(loop)
        switch_status = session.get_switch_status()
        switch_word = _protocol_int(switch_status[0]) if switch_status else 0
        expected_loops = {
            "Overall Active": bool(loop.system & 0x01),
            "Pneumatic": bool(loop.system & 0x40),
            "Feed Forward": bool(loop.system & 0x04),
            "Pneumatic FF": bool(loop.system & 0x4000),
            "Velocity Loop": bool(switch_word & 0x20),
            "Position Loop": bool(switch_word & 0x40),
        }
        for name, expected in expected_loops.items():
            assert window.status_loop_badges[name].text() == ("ON" if expected else "OFF")
        status_only = window._supports_controller_feature("auto_loop_switch")
        for key in ("velocity", "position"):
            assert window.loop_states.loop_btns[key].isEnabled() is not status_only
        for name in ("Velocity Loop", "Position Loop"):
            assert window.status_loop_badges[name].isEnabled() is not status_only
        print(
            "STATUS_LOOPS",
            {name: badge.text() for name, badge in window.status_loop_badges.items()},
        )
        print("STATUS_LOOP_MODE", "status-only" if status_only else "manual-click")
        assert window.ff_led_active.text() == (
            "ON" if loop.system & 0x0004 else "OFF"
        )
        assert window.ff_led_adapt.text() == (
            "ON" if loop.system & 0x0002 else "OFF"
        )
        assert window.ff_led_rawinput.text() == (
            "ON" if loop.system & 0x1000 else "OFF"
        )
        assert window.pff_active_led.text() == (
            "ON" if loop.system & 0x4000 else "OFF"
        )
        assert window.pff_adaptive_led.text() == (
            "ON" if loop.system & 0x8000 else "OFF"
        )

        window._refresh_pneumatic_live_state(loop)
        assert [lamp.text() for lamp in window.pneum_individual_loop_leds] == (
            expected_pff_individual
        )
        def pneumatic_row(key: str) -> list[str]:
            item = window._pneu_status_items[key]
            return [item.text(column) for column in range(window.pneum_live_list.columnCount())]

        print("PNEUMATIC_STATUS", pneumatic_row("Status"))
        print("PNEUMATIC_AXES_INPUT", pneumatic_row("AxesInput"))
        print("PNEUMATIC_AXES_OUTPUT", pneumatic_row("AxesOutput"))
        print("PNEUMATIC_HEIGHTS", pneumatic_row("HeightSet1"))
        print("PNEUMATIC_VALVES", pneumatic_row("ValveSet1"))
        print("PNEUMATIC_TIMERS", pneumatic_row("TimerStatus"))
        assert pneumatic_row("Status")[0] == "Status"
        assert pneumatic_row("Status")[1] in {
            "Down", "Going2SoftStop", "Up Soft", "Going Up", "UP",
            "Going Down", "Initialisation", "OK",
        }
        assert pneumatic_row("Status")[3] == "RefPoint"
        assert pneumatic_row("Status")[4] != ""
        assert pneumatic_row("TimerStatus")[0] == "OK Time"
        assert pneumatic_row("TimerStatus")[3] == "NOK Time"
        assert pneumatic_row("TimerStatus")[1] != ""
        assert pneumatic_row("TimerStatus")[4] != ""
        print(
            "PNEUMATIC_LEDS",
            {key: lamp.text() for key, lamp in window.pneum_loop_leds.items()},
        )
        print(
            "PNEUMATIC_INDIVIDUAL_LOOPS",
            [lamp.text() for lamp in window.pneum_individual_loop_leds],
        )
        window._set_connection_display(True, "probe")
        assert window.protection_led.text() == "ON"
        assert not window.nvram_save_button.isEnabled()
        assert window.nvram_restore_button.isEnabled()
        assert not window.nvram_clear_button.isEnabled()
        window.protection_led.click()
        assert window.protection_led.text() == "OFF"
        assert session.readonly
        assert window.nvram_save_button.isEnabled()
        assert window.nvram_restore_button.isEnabled()
        assert window.nvram_clear_button.isEnabled()
        window.protection_led.click()
        assert window.protection_led.text() == "ON"
        print("NVRAM_PROTECTION", "ON -> OFF -> ON", "session_readonly=True")
        for expander in (
            window.pneum_sensor_expander,
            window.pneum_valve_matrix_expander,
            window.pneum_iso_dither_expander,
        ):
            assert expander.content.maximumHeight() > 1_000_000

        print(
            "VELOCITY_MEASURE_STAGES",
            [
                window.vel_measure_stage.itemText(index)
                for index in range(window.vel_measure_stage.count())
            ],
        )

        scientific_fields: list[tuple[str, str]] = []
        for widget in window.findChildren(QtWidgets.QLineEdit):
            text = widget.text().strip()
            if SCIENTIFIC_NUMBER.fullmatch(text):
                scientific_fields.append((type(widget).__name__, text))
        for widget in window.findChildren(QtWidgets.QLabel):
            text = widget.text().strip()
            if SCIENTIFIC_NUMBER.fullmatch(text):
                scientific_fields.append((type(widget).__name__, text))
        print("VISIBLE_SCIENTIFIC_NUMBERS", scientific_fields)
        assert scientific_fields == []
    finally:
        window.session = None
        session.close()
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
