from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from python_samba.logging_tools.live_curve import (
    LiveCurveConfig,
    LiveCurveSessionBuffer,
    MonitorCapabilities,
    MonitorSignalSpec,
    build_monitor_signal_catalog,
)
from python_samba.services.monitor_lease import MonitorSlotLease
from python_samba.services.session import open_mock


def _signals(count: int = 3) -> tuple[MonitorSignalSpec, ...]:
    return tuple(
        MonitorSignalSpec(f"Sensor {index}", "Sensor", 0, index, 0)
        for index in range(count)
    )


def test_monitor_catalog_clips_firmware_dimensions_and_encodes_all_types():
    caps = MonitorCapabilities(
        input_count=2,
        velocity_axes=1,
        pneumatic_axes=1,
        position_axes=1,
        velocity_stages=2,
        position_stages=3,
        proximity_count=2,
        temperature=True,
        ff_pff=True,
        polynom_count=2,
        proximity_correction=True,
    )
    catalog = build_monitor_signal_catalog(caps)
    tokens = {signal.tokens for signal in catalog}
    assert {(0, 0, 0), (0, 1, 0)} <= tokens
    assert (0, 2, 0) not in tokens
    assert {(12, 11, 0), (1, 19, 0), (3, 0, 0)} <= tokens
    assert {(2, 0, -1), (2, 0, 0), (2, 0, 1), (4, 0, 0)} <= tokens
    assert {(5, 0, -1), (5, 0, 2), (5, 0, 3)} <= tokens
    assert {(8, 0, -1), (8, 0, 3), (8, 0, 4)} <= tokens
    assert {(10, 6, 6), (11, 3, 6)} <= tokens
    assert (10, 6, 11) not in tokens
    assert (11, 3, 8) not in tokens
    assert {(13, 1, 1), (14, 1, 0)} <= tokens
    assert not any(signal.io_type in {6, 7, 9} for signal in catalog)


def test_capabilities_parse_bggsc_and_feature_gates():
    class Version:
        major, minor, patch = 3, 3, 122

    caps = MonitorCapabilities.from_controller(
        [0, 5, 2, 8, 0, 8, 6, 5, 3, 0, 5000, "TmpSens", "POSAXES#7"],
        Version(),
    )
    assert caps.velocity_axes == 5
    assert caps.pneumatic_axes == 2
    assert caps.position_axes == 7
    assert caps.velocity_stages == 6
    assert caps.position_stages == 5
    assert caps.proximity_count == 8
    assert caps.temperature is True
    assert caps.input_count == 37


def test_capabilities_parse_original_poly_marker_shape():
    caps = MonitorCapabilities.from_controller(
        [0, 6, 3, 6, 0, 6, 7, 4, 8, 3, 5000, "POLY#12#8", "ProxCorr"]
    )
    assert caps.polynom_count == 12
    assert caps.proximity_correction is True


def test_live_config_limits():
    LiveCurveConfig(_signals(1), interval_ms=20).validate()
    with pytest.raises(ValueError, match="20..5000"):
        LiveCurveConfig(_signals(1), interval_ms=19).validate()
    with pytest.raises(ValueError, match="between 1 and 40"):
        LiveCurveConfig((), interval_ms=100).validate()
    with pytest.raises(ValueError, match="duplicates"):
        LiveCurveConfig((_signals(1)[0], _signals(1)[0])).validate()


def test_buffer_keeps_all_chunks_pause_gap_and_immutable_snapshot():
    buffer = LiveCurveSessionBuffer(_signals(2), chunk_size=16)
    buffer.start_segment(100.0)
    for index in range(35):
        buffer.append(
            datetime.now(timezone.utc), index * 0.1, [index, -index]
        )
    buffer.stop_segment(104.0, "operator stop")
    buffer.start_segment(110.0)
    buffer.append(datetime.now(timezone.utc), 10.1, [100, 200])
    snapshot = buffer.snapshot()
    assert buffer.sample_count == 36
    assert snapshot.values.shape == (36, 2)
    assert snapshot.elapsed_s[-1] == pytest.approx(10.1)
    assert snapshot.elapsed_s.flags.writeable is False
    assert snapshot.values.flags.writeable is False
    assert any(item.get("duration_s") == pytest.approx(6.0) for item in buffer.pause_intervals)
    visible = buffer.snapshot(start_s=3.0, end_s=3.3)
    assert np.all((visible.elapsed_s >= 3.0) & (visible.elapsed_s <= 3.3))


def test_buffer_export_roundtrip_metadata(tmp_path):
    signals = _signals(2)
    buffer = LiveCurveSessionBuffer(signals, chunk_size=16)
    buffer.start_segment(1.0)
    buffer.append("2026-08-12T00:00:00+00:00", 0.0, [1.25, 2.5])
    output = buffer.export_csv(
        tmp_path / "session",
        colors={signals[0].key: "#123456"},
        controller={"firmware": "V3.3.122"},
        requested_interval_ms=100,
        actual_interval_ms=101.5,
        late_samples=2,
    )
    assert output.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "timestamp_utc,elapsed_s,Sensor 0,Sensor 1"
    )
    sidecar = json.loads(
        output.with_suffix(".csv.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["signals"][0]["io_signal"] == [0, 0, 0]
    assert sidecar["signals"][0]["color"] == "#123456"
    assert sidecar["actual_interval_ms"] == 101.5


def test_monitor_slot_lease_configures_verifies_and_restores(tmp_path):
    session = open_mock(readonly=False)
    session.open()
    original = tuple(session.get_monitor_signals(40))
    lease = MonitorSlotLease(session, recovery_directory=tmp_path)
    selected = ((1, 4, 0), (2, 0, 3), (5, 1, -1))
    assert lease.acquire(selected) == original
    assert tuple(session.get_monitor_signals(3)) == selected
    assert lease.recovery_path.exists()
    assert lease.restore() is True
    assert tuple(session.get_monitor_signals(40)) == original
    assert not lease.recovery_path.exists()
    session.close()


def test_monitor_slot_lease_partial_failure_rolls_back(tmp_path, monkeypatch):
    session = open_mock(readonly=False)
    session.open()
    original = tuple(session.get_monitor_signals(40))
    real_get = session.get_monitor_signals
    calls = 0

    def wrong_readback(count=40, *, start_index=0):
        nonlocal calls
        calls += 1
        result = real_get(count, start_index=start_index)
        if calls == 2:
            result[0] = (99, 99, 99)
        return result

    monkeypatch.setattr(session, "get_monitor_signals", wrong_readback)
    lease = MonitorSlotLease(session, recovery_directory=tmp_path)
    with pytest.raises(RuntimeError, match="readback mismatch"):
        lease.acquire(((1, 0, 0), (1, 1, 0)))
    monkeypatch.setattr(session, "get_monitor_signals", real_get)
    assert tuple(session.get_monitor_signals(40)) == original
    assert not list(tmp_path.glob("*.json"))
    session.close()


def test_pending_restore_is_endpoint_bound(tmp_path):
    session = open_mock(readonly=False)
    session.open()
    lease = MonitorSlotLease(session, recovery_directory=tmp_path)
    lease.acquire(((1, 0, 0),))
    with pytest.raises(RuntimeError, match="Real-time Curve owns"):
        session.set_monitor_signal(0, 0, 0, 0)
    session.close()
    assert lease.restore() is False
    assert lease.recovery_path.exists()

    replacement = open_mock(readonly=False)
    replacement.open()
    assert MonitorSlotLease.pending_for_session(
        replacement, recovery_directory=tmp_path
    ) is not None
    ok, message = MonitorSlotLease.retry_pending(
        replacement, recovery_directory=tmp_path
    )
    assert ok is True
    assert "restored" in message.lower()
    replacement.close()
