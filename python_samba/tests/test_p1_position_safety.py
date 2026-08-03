"""P1 position-loop and safety tests (mock only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from python_samba.protocol.commands import FilterStage
from python_samba.services.safety import SafetyGate
from python_samba.services.session import open_mock


def test_position_filter_roundtrip(tmp_path: Path):
    with open_mock(readonly=False) as session:
        stage = session.get_proximity_filter(0, 0)
        assert stage.filter_type == 1
        assert stage.params[0] == pytest.approx(0.05)
        new = FilterStage(
            axis=0,
            stage=0,
            filter_type=2,
            params=(0.2, 0.0, 1.0, 0.0, 0.0),
        )
        session.set_proximity_filter(new)
        got = session.get_proximity_filter(0, 0)
        assert got.filter_type == 2
        assert got.params[0] == pytest.approx(0.2)


def test_proximity_offsets_and_cauco():
    with open_mock(readonly=False) as session:
        off = session.get_proximity_offsets()
        assert len(off) == 6
        assert off[0] == pytest.approx(100.0)
        session.set_proximity_offsets([1, 2, 3, 4, 5, 6])
        assert session.get_proximity_offsets() == pytest.approx([1, 2, 3, 4, 5, 6])
        session.use_current_proximity_offsets()
        # mock copies proximity_live
        assert session.get_proximity_offsets()[0] == pytest.approx(110.0)


def test_eight_proximity_extension_roundtrip():
    """CGPOX/CSPOX/CAUCX and PGGIX stay separate from the six-channel path."""
    with open_mock(readonly=False) as session:
        session.transport.state.proximity_offsets = [float(i) for i in range(1, 9)]
        session.transport.state.proximity_live = [float(i) for i in range(11, 19)]

        assert session.get_proximity_offsets(8) == pytest.approx(range(1, 9))
        assert session.get_proximity_input_values(8) == pytest.approx(range(11, 19))

        session.set_proximity_offsets([float(i) for i in range(21, 29)])
        assert session.get_proximity_offsets(8) == pytest.approx(range(21, 29))
        session.use_current_proximity_offsets(8)
        assert session.get_proximity_offsets(8) == pytest.approx(range(11, 19))


def test_position_matrices():
    with open_mock(readonly=False) as session:
        sens = session.get_position_sensor_matrix(0)
        assert len(sens) == 6
        sens[1] = 0.5
        session.set_position_sensor_matrix(0, sens)
        assert session.get_position_sensor_matrix(0)[1] == pytest.approx(0.5)
        motor = session.get_position_motor_matrix(2)
        assert len(motor) == 8
        motor[0] = 2.0
        session.set_position_motor_matrix(2, motor)
        assert session.get_position_motor_matrix(2)[0] == pytest.approx(2.0)


def test_velocity_matrix_write():
    with open_mock(readonly=False) as session:
        row = session.get_velocity_sensor_matrix(0)
        row[2] = 0.33
        session.set_velocity_sensor_matrix(0, row)
        assert session.get_velocity_sensor_matrix(0)[2] == pytest.approx(0.33)
        m = session.get_velocity_motor_matrix(0)
        m[3] = 1.25
        session.set_velocity_motor_matrix(0, m)
        assert session.get_velocity_motor_matrix(0)[3] == pytest.approx(1.25)


def test_safety_gate_snapshot_and_lock(tmp_path: Path):
    session = open_mock(readonly=True)
    session.open()
    gate = SafetyGate(session, snapshot_dir=tmp_path)
    with pytest.raises(PermissionError):
        gate.queue_velocity_filter(session.get_velocity_filter(0, 0))
    gate.unlock()
    snap = gate.take_snapshot()
    assert snap.version.startswith("V3.3")
    files = list(tmp_path.glob("snap_*.json"))
    assert len(files) == 1
    gate.take_snapshot()
    assert len(list(tmp_path.glob("snap_*.json"))) == 2
    change = gate.queue_velocity_filter(
        FilterStage(0, 0, 4, (0.1, 0.0, 1.0, 0.0, 0.0))
    )
    assert "VSVFS" in change.summary
    applied = gate.apply_pending(snapshot_first=True)
    assert len(applied) == 1
    got = session.get_velocity_filter(0, 0)
    assert got.filter_type == 4
    gate.lock()
    with pytest.raises(PermissionError):
        session.set_velocity_filter(got)
    session.close()


def test_failed_pending_change_is_not_lost(tmp_path: Path):
    from python_samba.services.safety import ParamChange

    session = open_mock(readonly=False)
    gate = SafetyGate(session, snapshot_dir=tmp_path)

    def fail() -> None:
        raise RuntimeError("controller rejected write")

    change = ParamChange("test", "failure", None, 1, fail)
    gate.pending.append(change)
    with pytest.raises(RuntimeError, match="rejected"):
        gate.apply_pending(snapshot_first=False)
    assert gate.pending == [change]


def test_gui_imports():
    """Import UI modules without showing a window (requires PySide6)."""
    pytest.importorskip("PySide6")
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.widgets import FilterEditor, MatrixEditor

    assert MainWindow is not None
    assert FilterEditor is not None
    assert MatrixEditor is not None
