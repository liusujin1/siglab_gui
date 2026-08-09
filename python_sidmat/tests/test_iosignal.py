"""IO signal model/naming tests."""

from __future__ import annotations

import pytest

from python_samba.ui.label_files import LABEL_FILE_DEFAULTS

from python_sidmat.backend.iosignal import (
    DEFAULT_POSITION_FILTER_COUNT,
    DEFAULT_VELOCITY_FILTER_COUNT,
    IOType,
    configure_filter_counts,
    io_signal_list,
    io_type_name,
)


def test_io_type_triple_encode() -> None:
    io = IOType(2, 3, 4)
    assert io.encode() == (2, 3, 4)
    assert tuple(io) == (2, 3, 4)


def test_sensor_naming_matches_label_table() -> None:
    for idx, name in enumerate(LABEL_FILE_DEFAULTS["InputName"]):
        assert io_type_name(IOType(0, idx, 0)) == name


def test_sensor_out_of_range() -> None:
    assert io_type_name(IOType(0, 99, 0)) == "Unknown Sens"


def test_actuator_naming() -> None:
    assert io_type_name(IOType(1, 0, 0)) == LABEL_FILE_DEFAULTS["DACOutputName"][0]
    assert io_type_name(IOType(1, 50, 0)) == "Unknown Actua"


def test_velocity_naming() -> None:
    axes = LABEL_FILE_DEFAULTS["VelAxesName"]
    assert io_type_name(IOType(2, 0, 0)) == f"Vel {axes[0]} Stage1"
    assert io_type_name(IOType(2, 1, 7)) == f"Vel {axes[1]} Output"
    assert io_type_name(IOType(2, 2, -1)) == f"Vel {axes[2]} Raw"
    assert io_type_name(IOType(2, 9, 0)) == "Unknown Vel"


def test_noise_type_is_excitation() -> None:
    assert io_type_name(IOType(3, 0, 0)) == "Excitation"


def test_position_naming() -> None:
    try:
        configure_filter_counts(position=4)
        axes = LABEL_FILE_DEFAULTS["PosAxesName"]
        assert io_type_name(IOType(5, 0, -1)) == f"Pos {axes[0]} Raw"
        assert io_type_name(IOType(5, 0, 0)) == f"Pos {axes[0]} Stage1"
        assert io_type_name(IOType(5, 3, 2)) == f"Pos {axes[3]} Stage3"
        assert io_type_name(IOType(5, 0, 4)) == f"Pos {axes[0]} Output"
    finally:
        configure_filter_counts(position=DEFAULT_POSITION_FILTER_COUNT)


def test_dynamic_filter_counts_update_lists_atomically() -> None:
    try:
        configure_filter_counts(velocity=2, position=3)
        velocity = io_signal_list(2)
        position = io_signal_list(5)
        assert len(velocity) == 6 * (2 + 2)  # Raw + 2 stages + Output
        assert len(position) == 12 * (3 + 2)
        assert velocity[0].sub_index == -1
        assert velocity[3].sub_index == 2

        with pytest.raises(ValueError, match="position filter count"):
            configure_filter_counts(velocity=1, position=99)
        # Invalid position must not commit the otherwise-valid velocity value.
        assert io_type_name(IOType(2, 0, 2)).endswith("Output")
    finally:
        configure_filter_counts(
            velocity=DEFAULT_VELOCITY_FILTER_COUNT,
            position=DEFAULT_POSITION_FILTER_COUNT,
        )


def test_temp_sensor_naming() -> None:
    table = LABEL_FILE_DEFAULTS["MotorTemperaturSensorName"]
    assert io_type_name(IOType(12, 0, 0)) == table[0]
    assert io_type_name(IOType(12, 99, 0)) == "Unknown Temp"


def test_ff_pff_naming() -> None:
    assert io_type_name(IOType(10, 0, 0)) == "FF Ch1 RefFil1"
    assert io_type_name(IOType(10, 0, 3)) == "FF Ch1 SecFil1"
    axes = LABEL_FILE_DEFAULTS["VelAxesName"]
    assert io_type_name(IOType(10, 0, 6)) == f"FF Ch1 {axes[0]} Out"
    assert io_type_name(IOType(11, 1, 0)) == "PFF Ch2 RefFil1"


def test_unknown_type() -> None:
    assert io_type_name(IOType(99, 0, 0)) == "Unknown Type"


def test_polynom_and_proximity_correction_fallback_names() -> None:
    assert io_type_name(IOType(13, 0, 0)) == "Prox1 Polynom Input"
    assert io_type_name(IOType(14, 0, 0)) == "Prox1 Corr"


def test_sensor_signal_list_length() -> None:
    lst = io_signal_list(0)
    assert len(lst) == len(LABEL_FILE_DEFAULTS["InputName"])
    assert lst[0].encode() == (0, 0, 0)


def test_signal_lists_have_names() -> None:
    for t in (0, 1, 2, 3, 4, 5, 8, 10, 11, 12, 13, 14):
        lst = io_signal_list(t)
        assert lst, f"no signals for type {t}"
        for io in lst[:5]:
            assert io_type_name(io)
