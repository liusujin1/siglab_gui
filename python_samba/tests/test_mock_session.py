"""Mock backend session tests — no hardware, no vendor DLL."""

from __future__ import annotations

import pytest

from python_samba.protocol.commands import FilterStage, RciCommandError
from python_samba.services.session import open_mock


def test_mock_connect_version():
    session = open_mock()
    version = session.open()
    assert version.major == 3
    assert version.minor == 3
    assert str(version).startswith("V3.3")
    session.close()
    assert not session.connected


def test_mock_status_and_filter():
    with open_mock() as session:
        loop = session.get_loop_status()
        assert loop.individual == 0x7F
        assert loop.system == 0x1800
        stage = session.get_velocity_filter(0, 0)
        assert stage.filter_type == 3  # HPF1O
        assert stage.type_name == "HPF1O"
        assert stage.params[0] == pytest.approx(0.15)
        assert session.get_sample_frequency() == pytest.approx(2000.0)


def test_mock_matrix_and_geophone():
    with open_mock() as session:
        sens = session.get_velocity_sensor_matrix(0)
        assert len(sens) == 7
        assert sens[0] == pytest.approx(1.0)
        motor = session.get_velocity_motor_matrix(1)
        assert len(motor) == 12
        geo = session.get_geophone_inputs()
        assert len(geo) == 7


def test_mock_readonly_blocks_write():
    with open_mock(readonly=True) as session:
        stage = session.get_velocity_filter(0, 0)
        with pytest.raises(PermissionError):
            session.set_velocity_filter(stage)


def test_mock_write_filter_roundtrip():
    with open_mock(readonly=False) as session:
        new_stage = FilterStage(
            axis=0,
            stage=1,
            filter_type=1,
            params=(0.25, 0.0, 1.0, 0.0, 0.0),
        )
        session.set_velocity_filter(new_stage)
        got = session.get_velocity_filter(0, 1)
        assert got.filter_type == 1
        assert got.params[0] == pytest.approx(0.25)


def test_mock_unknown_command_status():
    with open_mock() as session:
        resp = session.raw_command("ZZZZZ")
        assert not resp.ok
        assert resp.status_code == 0x03
