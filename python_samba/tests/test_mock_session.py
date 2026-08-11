"""Mock backend session tests — no hardware, no vendor DLL."""

from __future__ import annotations

import threading
import time

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


def test_bulk_filter_helpers_use_transport_batch_interface() -> None:
    with open_mock() as session:
        original_exchange_many = session.transport.exchange_many
        batch_sizes: list[int] = []

        def counting_exchange_many(requests):
            items = list(requests)
            batch_sizes.append(len(items))
            return original_exchange_many(items)

        session.transport.exchange_many = counting_exchange_many  # type: ignore[method-assign]

        velocity = session.get_velocity_filters([(0, 0), (1, 1)])
        position = session.get_proximity_filters([(0, 0), (1, 1)])
        ff = session.get_ff_filters([(0, 0), (5, 6)])
        pneumatic = session.get_pneumatic_filters([(0, 0), (2, 3)])
        pff = session.get_pff_filters([(0, 0, 0), (2, 0, 7)])

        assert batch_sizes == [2, 2, 2, 2, 2]
        assert [stage.stage for stage in velocity] == [0, 1]
        assert [stage.stage for stage in position] == [0, 1]
        assert [stage.stage for stage in ff] == [0, 6]
        assert [stage.stage for stage in pneumatic] == [0, 3]
        assert [stage.stage for stage in pff] == [0, 7]


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


def test_mock_binary_trace_and_excitation_offset():
    with open_mock(readonly=False) as session:
        values = session.get_digital_trace_buffer_binary(0, 4)
        assert len(values) == 8
        assert values[0] == pytest.approx(0.0)
        assert values[1] == pytest.approx(8.0)
        session.set_excitation_offset(0.25)
        assert session.get_excitation_offset() == pytest.approx(0.25)


def test_binary_trace_accepts_real_ascii_crc_after_last_value():
    """Real firmware appends two CRC characters instead of the ``##`` token."""
    with open_mock(readonly=False) as session:
        original_read_until = session.transport.read_until

        def read_until_with_crc(terminator=b"\r", timeout=2.0):
            raw = original_read_until(terminator, timeout)
            if b" DGTBB " in raw:
                assert raw.endswith(b"##\r")
                raw = raw[:-3] + b"2D\r"
            return raw

        session.transport.read_until = read_until_with_crc  # type: ignore[method-assign]
        values = session.get_digital_trace_buffer_binary(0, 4)
        assert len(values) == 8
        assert values[-1] == pytest.approx(11.0)


def test_serialized_request_response_transactions():
    """Concurrent callers must not overlap one transport request/response."""
    with open_mock() as session:
        original_write = session.transport.write
        original_read = session.transport.read_until
        counter_lock = threading.Lock()
        active = 0
        max_active = 0

        def write(data: bytes) -> None:
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.002)
            original_write(data)

        def read_until(terminator=b"\r", timeout=2.0):
            nonlocal active
            try:
                time.sleep(0.002)
                return original_read(terminator, timeout)
            finally:
                with counter_lock:
                    active -= 1

        session.transport.write = write  # type: ignore[method-assign]
        session.transport.read_until = read_until  # type: ignore[method-assign]
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                session.get_loop_status()
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert max_active == 1
