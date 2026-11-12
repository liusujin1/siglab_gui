"""Offline acquisition acceptance; no USB, NI driver or network hardware used."""
from __future__ import annotations

from copy import deepcopy
import json
import threading
import time
import tracemalloc

import numpy as np
import pytest

from python_vna.app import build_backend, parse_args
from python_vna.continuous_recording import ContinuousDatWriter
from python_vna.controller import VnaController
from python_vna.daq.base import BackendFrame, DaqBackendError
from python_vna.daq.dasp_import import apply_dasp_channels, dasp_voltage_scale, load_dasp_channels
from python_vna.daq.netdaq import profile_preview
from python_vna.daq.quality import AcquisitionQuality
from python_vna.daq.replay import ReplayBackend, ReplayComplete, ReplaySource
from python_vna.daq.software_trigger import SoftwareTrigger
from python_vna.storage import default_session_config, save_legacy_vna


def session_for(names=("ai0", "ai1"), rate=32, size=16):
    session = default_session_config()
    session.ai_channels = session.ai_channels[:len(names)]
    for channel, name in zip(session.ai_channels, names):
        channel.name = name
        channel.enabled = True
    session.acquisition.sample_rate = rate
    session.acquisition.frame_size = size
    session.acquisition.excitation.enabled = False
    session.acquisition.reference_channel = names[0]
    session.acquisition.response_channels = list(names[1:])
    return session


def frame(data, rate=32, index=0, names=None):
    data = np.asarray(data, dtype=float)
    if data.ndim == 1:
        data = data[np.newaxis]
    return BackendFrame(rate, names or [f"ai{i}" for i in range(len(data))],
                        data, np.arange(data.shape[1]) / rate, index)


def binary_record(tmp_path, data, rate=32, size=16):
    session = session_for(tuple(f"ai{i}" for i in range(len(data))), rate, size)
    output = tmp_path / "record"
    writer = ContinuousDatWriter(output, session, device_name="test", channel_names=[c.name for c in session.ai_channels],
                                 software_version="test", storage_format="binary")
    for begin in range(0, data.shape[1], size):
        writer.write_frame(frame(data[:, begin:begin + size], rate))
    writer.close()
    return output, session


def test_dasp_sensor_import_reciprocal_units_and_no_zero_or_iepe_change(tmp_path):
    path = tmp_path / "Channel.ini"
    path.write_text("[Channel 1]\nLabel=传感器Y\nSensitivity=51.567\nVoltage Unit=mV\n"
                    "Unit=m/s^2\nSensitivity Physical Unit=m/s^2\nOffset comp=9\nUsed=1\n",
                    encoding="gb18030")
    imported = load_dasp_channels(path)
    assert imported[0].eu_per_volt == pytest.approx(1000 / 51.567)
    channels = session_for().ai_channels
    old = deepcopy(channels[1])
    apply_dasp_channels(channels, imported)
    assert channels[1].label == "传感器Y"
    assert channels[1].sensitivity == pytest.approx(1000 / 51.567)
    assert channels[1].offset == old.offset
    assert channels[1].iepe_enabled == old.iepe_enabled
    assert channels[1].coupling == old.coupling


@pytest.mark.parametrize("setting", ["Sensitivity=0", "External gain=nan", "Gain=0", "Gain=nan",
                                   "Inner gain=2", "Gain=1e308\nExternal gain=1e308", "Calibration enable=1",
                                   "Polarity=1", "bVirtualChannel=1",
                                   "Sensitivity Physical Unit=N"])
def test_dasp_unknown_or_invalid_scaling_fails_closed(tmp_path, setting):
    path = tmp_path / "Channel.ini"
    path.write_text(f"[Channel 0]\nSensitivity=1\nUnit=m/s^2\n{setting}\n", encoding="utf-8")
    with pytest.raises(DaqBackendError):
        load_dasp_channels(path)


def trigger_for(signal, *, delay=0, mode="Every Frame", size=8):
    session = session_for(("ai0",), rate=8, size=size)
    trigger = session.acquisition.trigger
    trigger.enabled, trigger.source, trigger.level = True, "ai0", 1.0
    trigger.mode, trigger.pretrigger_samples = mode, delay
    blocks = iter([frame(signal[i:i + size], rate=8) for i in range(0, len(signal), size)])
    def read():
        try:
            return next(blocks)
        except StopIteration:
            raise ReplayComplete("end")
    return SoftwareTrigger(session.acquisition, ["ai0"], read, lambda: None)


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("delay", [-50, 0, 50, 100])
def test_trigger_cross_block_both_polarities_and_delay(sign, delay):
    signal = np.zeros(64)
    signal[8:10] = sign * 2
    trigger = trigger_for(signal, delay=delay)
    output = trigger.read_frame()
    begin = 8 + round(8 * delay / 100)
    np.testing.assert_array_equal(output.data[0], signal[begin:begin + 8])
    assert output.metadata["sample_start_index"] == begin
    assert output.metadata["trigger_sample_index"] == 8
    assert output.timestamps[0] == 0


def test_trigger_rearm_does_not_retrigger_sustained_high_or_drop_buffered_event():
    signal = np.zeros(64)
    signal[4:15] = 2
    signal[18:20] = -2
    trigger = trigger_for(signal)
    assert trigger.read_frame().metadata["trigger_sample_index"] == 4
    assert trigger.read_frame().metadata["trigger_sample_index"] == 18


def test_adjacent_trigger_pre_history_includes_previous_frame_tail():
    signal = np.zeros(48)
    signal[[10, 16]] = [2, -3]
    trigger = trigger_for(signal, delay=-50)
    first = trigger.read_frame()
    second = trigger.read_frame()
    np.testing.assert_array_equal(first.data[0], signal[6:14])
    np.testing.assert_array_equal(second.data[0], signal[12:20])
    assert second.data[0, 4] == -3


def test_replay_partial_tail_is_kept_for_record_but_not_misaveraged(tmp_path):
    folder, session = binary_record(tmp_path, np.ones((2, 35)), size=16)
    controller = VnaController(ReplayBackend(folder, realtime=False), session)
    controller.configure()
    controller.set_averaging_enabled(True)
    controller.start()
    controller.read_and_process()
    controller.read_and_process()
    with pytest.raises(ReplayComplete, match="不足一帧"):
        controller.read_and_process()
    assert controller.state.measurement.metadata["average_count"] == 2
    controller.close()


def test_realtime_trigger_does_not_wait_for_whole_large_frame_before_detection(tmp_path):
    signal = np.zeros((2, 256))
    signal[0, 12:14] = 2
    folder, session = binary_record(tmp_path, signal, rate=256, size=128)
    trigger = session.acquisition.trigger
    trigger.enabled, trigger.source, trigger.level, trigger.mode = True, "ai0", 1, "Every Frame"
    trigger.timeout_seconds = .3
    backend = ReplayBackend(folder)
    backend.configure(session)
    backend.start()
    try:
        result = backend.read_frame()
        assert result.metadata["trigger_sample_index"] == 12
    finally:
        backend.close()


def test_first_frame_then_free_run_preserves_remaining_samples():
    signal = np.arange(40) / 10
    trigger = trigger_for(signal, mode="First Frame")
    first = trigger.read_frame()
    second = trigger.read_frame()
    assert first.metadata["trigger_sample_index"] == 10
    assert not second.metadata["software_trigger"]
    np.testing.assert_array_equal(second.data[0], signal[18:26])


def test_trigger_timeout_and_cancellation_are_bounded(monkeypatch):
    trigger = trigger_for(np.zeros(40))
    clock = iter([0, 0, 6])
    monkeypatch.setattr("python_vna.daq.software_trigger.time.monotonic", lambda: next(clock))
    with pytest.raises(DaqBackendError, match="超时"):
        trigger.read_frame()
    monkeypatch.undo()
    stopped = trigger_for(np.zeros(40))
    def stop():
        raise DaqBackendError("stopped")
    stopped.check_stop = stop
    with pytest.raises(DaqBackendError, match="stopped"):
        stopped.read_frame()


def test_trigger_idle_scan_memory_is_bounded():
    session = session_for(("ai0",), size=256)
    session.acquisition.trigger.enabled = True
    session.acquisition.trigger.source = "ai0"
    session.acquisition.trigger.mode = "Every Frame"
    session.acquisition.trigger.level = 1
    calls = 0
    def read():
        nonlocal calls
        calls += 1
        if calls == 2001:
            raise ReplayComplete("done")
        return frame(np.zeros(256))
    trigger = SoftwareTrigger(session.acquisition, ["ai0"], read, lambda: None)
    with pytest.raises(ReplayComplete):
        trigger.read_frame()
    assert trigger.data.nbytes <= 256 * 8


def test_quality_dc_rms_rails_and_memory():
    session = session_for(("ai0",))
    session.ai_channels[0].min_value, session.ai_channels[0].max_value = -5, 5
    quality = AcquisitionQuality(session.ai_channels)
    quality.add(frame([4, 4, 4, 4, np.nan]))
    report = quality.report()["channels"]["ai0"]
    assert report["mean_v"] == 4
    assert report["rms_v"] == 4
    assert report["ac_rms_v"] == 0
    assert report["invalid_samples"] == 1
    assert report["rail_fraction"] == 0
    quality.add(frame([-5, 5]))
    assert quality.report()["channels"]["ai0"]["rail_samples"] == 2
    assert len(quality.stats) == 1
    json.dumps(quality.report(), allow_nan=False)


def test_replay_binary_order_actual_rate_eof_and_100_restarts(tmp_path):
    original = np.vstack((np.arange(35) + 4, np.arange(35) * 2))
    output, session = binary_record(tmp_path, original, rate=128, size=8)
    backend = ReplayBackend(output, realtime=False)
    assert backend.list_devices()[0].name == "Replay"
    assert backend.source._vna_data is None
    session.acquisition.sample_rate = 999
    for _ in range(100):
        backend.configure(session)
        backend.start()
        collected = []
        with pytest.raises(ReplayComplete):
            while True:
                collected.append(backend.read_frame().data)
        np.testing.assert_array_equal(np.concatenate(collected, axis=1), original)
        assert session.acquisition.sample_rate == 128
        backend.stop()
        backend.close()


def test_replay_stop_interrupts_realtime_pacing(tmp_path):
    output, session = binary_record(tmp_path, np.ones((2, 32)), rate=1)
    backend = ReplayBackend(output)
    backend.configure(session)
    event = threading.Event()
    backend.set_stop_event(event)
    backend.start()
    timer = threading.Timer(.04, event.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(DaqBackendError, match="停止"):
            backend.read_frame()
    finally:
        timer.join()
        backend.close()
    assert time.monotonic() - began < .5


def test_replay_text_and_individual_binary_segment(tmp_path):
    session = session_for()
    writer = ContinuousDatWriter(tmp_path / "text", session, device_name=None, channel_names=["ai0", "ai1"],
                                 software_version="test", compress_closed_segments=False)
    original = np.array([[4, 4.1, 3.9], [1, 2, 3]])
    writer.write_frame(frame(original))
    writer.close()
    for source in (tmp_path / "text", tmp_path / "text/segment_0001.dat"):
        backend = ReplayBackend(source, realtime=False)
        backend.configure(session)
        backend.start()
        np.testing.assert_allclose(backend.read_frame().data, original)
        backend.close()
    folder, session = binary_record(tmp_path, np.ones((2, 32)))
    assert len(list(ReplaySource(folder / "segment_0001.bin").blocks(16))) == 2


def test_replay_dasp_counts_map_to_volts_without_zero_drift(tmp_path):
    path = tmp_path / "CH 1.par"
    path.write_text("[Setup]\nChannel no=1\nAD Bit=24\nRange from=-8388608\nRange to=8388607\n"
                    "Voltage from=-10000\nVoltage to=10000\nSample rate=128000\n"
                    "Offset comp=4000\nBuffer data type=2\n", encoding="utf-8")
    np.array([-8388608, 0, 8388607], dtype="<i4").tofile(path.with_suffix(".sts"))
    source = ReplaySource(tmp_path)
    data = next(source.blocks(8))
    assert data[0, 0] == -10
    assert data[0, -1] == 10 - 10 / 8388608
    assert data[0, 1] == 0
    assert source.header["dasp_conversion"]["ai0"]["volts_per_count"] == 10 / 8388608
    assert source.header["sample_rate_basis"] == "file_declared_unverified"
    assert "未减零漂" in source.header["replay_warning"]


def test_dasp_scale_counts_quantization_steps_not_inclusive_voltage_endpoints():
    scale = dasp_voltage_scale({
        "AD Bit": "24", "Range from": "-8388608", "Range to": "8388607",
        "Voltage from": "-10000", "Voltage to": "10000", "Voltage Unit": "mV",
    })
    assert scale["adc_code_count"] == 16777216
    assert scale["zero_count"] == 0
    assert scale["volts_per_count"] == 10 / 8388608
    assert not scale["electrical_calibration_verified"]
    counts = np.array([-8388608, -4194304, -838861, 0, 838861, 4194304, 8388607])
    values = (counts - scale["zero_count"]) * scale["volts_per_count"]
    np.testing.assert_allclose(values, [-10, -5, -1, 0, 1, 5, 10], rtol=0, atol=10 / 8388608)
    assert values[3] == 0


@pytest.mark.parametrize("gain, external", [(1, 1), (2, 1), (1, 10), (2, 10)])
def test_dasp_gain_and_external_gain_apply_once_in_eu_conversion(tmp_path, gain, external):
    path = tmp_path / "CH 2.par"
    path.write_text(
        f"[Setup]\nChannel no=2\nSensitivity=50\nVoltage Unit=mV\nUnit=m/s^2\n"
        f"Sensitivity Physical Unit=m/s^2\nGain={gain}\nExternal gain={external}\n",
        encoding="utf-8")
    imported = load_dasp_channels(path)
    assert imported[0].gain == gain
    assert imported[0].external_gain == external
    channels = session_for().ai_channels
    apply_dasp_channels(channels, imported)
    assert channels[1].sensitivity == pytest.approx(1000 / (50 * gain * external))
    assert .5 * channels[1].sensitivity == pytest.approx(10 / (gain * external))


def test_dasp_replay_does_not_apply_gain_sensitivity_or_offset_to_raw_volts(tmp_path):
    path = tmp_path / "CH 1.par"
    path.write_text(
        "[Setup]\nChannel no=1\nAD Bit=24\nRange from=-8388608\nRange to=8388607\n"
        "Voltage from=-10000\nVoltage to=10000\nVoltage Unit=mV\nSample rate=128000\n"
        "Gain=10\nExternal gain=20\nSensitivity=50\nOffset comp=4000\n", encoding="utf-8")
    np.array([0, 4194304, -4194304], dtype="<i4").tofile(path.with_suffix(".sts"))
    source = ReplaySource(path)
    np.testing.assert_array_equal(next(source.blocks(8)), [[0, 5, -5]])
    session = session_for(("ai0",), size=8)
    backend = ReplayBackend(path, realtime=False)
    backend.configure(session)
    report = backend.acquisition_provenance()
    assert report["sample_rate_basis"] == "file_declared_unverified"
    assert report["actual_sample_rate"] is None
    assert report["playback_sample_rate"] == 128000
    assert report["voltage_conversion"]["ai0"]["volts_per_count"] == 10 / 8388608
    assert not report["voltage_conversion"]["ai0"]["electrical_calibration_verified"]
    backend.close()


def test_replay_vna_preserves_voltage_not_eu_scaling(tmp_path):
    from python_vna.daq.simulated import SimulatedDaqBackend
    session = session_for(rate=2560, size=128)
    session.ai_channels[0].sensitivity = 800
    controller = VnaController(SimulatedDaqBackend(), session)
    controller.configure()
    controller.start()
    measurement = controller.read_and_process()
    controller.stop()
    controller.close()
    path = tmp_path / "001.vna"
    save_legacy_vna(controller.snapshot(), path)
    source = ReplaySource(path)
    data = np.concatenate(list(source.blocks(64)), axis=1)
    np.testing.assert_allclose(data[0], measurement.time_data["channels"]["ai0"])


def test_replay_trigger_avg_reject_and_overlap_uses_distinct_events(tmp_path):
    signal = np.zeros((2, 128))
    signal[0, [10, 42, 74]] = [2, 20, -2]
    signal[1] = signal[0] / 2
    folder, session = binary_record(tmp_path, signal, size=16)
    trigger = session.acquisition.trigger
    trigger.enabled, trigger.source, trigger.level, trigger.mode = True, "ai0", 1, "Every Frame"
    session.acquisition.overlap_percent = 50
    session.acquisition.modal.enabled = True
    session.acquisition.modal.smart_hit_enabled = False
    session.acquisition.modal.reject_overload = True
    controller = VnaController(ReplayBackend(folder, realtime=False), session)
    controller.configure()
    controller.set_averaging_enabled(True)
    controller.start()
    first = controller.read_and_process()
    assert first.metadata["average_count"] == 1
    second = controller.read_and_process()
    assert second.metadata["rejected"]
    assert second.metadata["average_count"] == 1
    third = controller.read_and_process()
    assert third.metadata["average_count"] == 2
    controller.close()


def test_corrupted_record_and_manifest_path_escape_fail(tmp_path):
    folder, session = binary_record(tmp_path, np.ones((2, 16)))
    part = folder / "segment_0001.bin"
    with part.open("ab") as handle:
        handle.write(b"x")
    backend = ReplayBackend(folder, realtime=False)
    backend.configure(session)
    backend.start()
    with pytest.raises(DaqBackendError, match="截断"):
        backend.read_frame()
    backend.close()
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["segments"][0]["path"] = "../outside.bin"
    path.write_text(json.dumps(manifest))
    with pytest.raises(DaqBackendError, match="越出"):
        ReplaySource(folder)


def test_row_aligned_partial_record_cannot_hide_manifest_count_mismatch(tmp_path):
    folder, session = binary_record(tmp_path, np.ones((2, 32)))
    part = folder / "segment_0001.bin"
    with part.open("r+b") as handle:
        handle.truncate(part.stat().st_size - 16)
    backend = ReplayBackend(folder, realtime=False)
    backend.configure(session)
    backend.start()
    try:
        with pytest.raises(DaqBackendError, match="截断"):
            backend.read_frame()
    finally:
        backend.close()


def test_profile_preview_invalid_file_never_accesses_hardware(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"verified": false}')
    result = profile_preview(path)
    assert not result["valid"]
    assert not result["hardware_accessed"]
    assert "schema_version" in result["reason"]


def test_replay_explicit_cli_and_ni_default():
    assert parse_args([]).backend == "ni"
    args = parse_args(["--backend", "replay", "--replay-path", "record/manifest.json"])
    assert isinstance(build_backend(args.backend, replay_path=args.replay_path), ReplayBackend)
    with pytest.raises(ValueError):
        build_backend("replay")


def test_virtual_13_hour_segments_constant_memory_and_integrity(tmp_path):
    session = session_for(size=64)
    clock = [0.]
    writer = ContinuousDatWriter(tmp_path / "long", session, device_name="offline",
                                 channel_names=["ai0", "ai1"], software_version="test",
                                 segment_seconds=600, storage_format="binary", monotonic_fn=lambda: clock[0])
    data = frame(np.ones((2, 64)))
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for minute in range(13 * 60):
        clock[0] = minute * 60
        writer.write_frame(data)
    writer.close()
    growth = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    manifest = json.loads(writer.manifest_path.read_text())
    assert len(manifest["segments"]) == 78
    assert manifest["total_samples"] == 780 * 64
    assert manifest["completed"]
    assert growth < 4 * 1024 * 1024
    assert sum(p.stat().st_size for p in writer.output_dir.glob("*.bin")) == 780 * 64 * 2 * 8


def test_partial_full_disk_write_rolls_back_and_marks_incomplete(tmp_path, monkeypatch):
    session = session_for()
    writer = ContinuousDatWriter(tmp_path / "full", session, device_name=None,
                                 channel_names=["ai0", "ai1"], software_version="test", storage_format="binary")
    writer.write_frame(frame(np.ones((2, 16))))
    def fail(data):
        writer._segment.file.write(b"partial")
        raise OSError(28, "No space left on device (injected)")
    monkeypatch.setattr(writer, "_write_binary_frame", fail)
    with pytest.raises(OSError):
        writer.write_frame(frame(np.ones((2, 16))))
    writer.close()
    manifest = json.loads(writer.manifest_path.read_text())
    assert not manifest["completed"]
    assert manifest["total_samples"] == 16
    assert "No space" in manifest["error"]
    assert (writer.output_dir / "segment_0001.bin").stat().st_size == 16 * 2 * 8


def test_record_channel_reordering_cannot_silently_corrupt_file(tmp_path):
    output, session = binary_record(tmp_path, np.ones((2, 16)))
    writer = ContinuousDatWriter(tmp_path / "other", session, device_name=None,
                                 channel_names=["ai0", "ai1"], software_version="test")
    with pytest.raises(ValueError, match="channel"):
        writer.write_frame(frame(np.zeros((2, 16)), names=["ai1", "ai0"]))
    writer.close(completed=False, error="channel mismatch")


def test_slow_disk_stop_commits_only_finished_frames(tmp_path, monkeypatch):
    from python_vna.ui.main_window import ContinuousRecordingWorker
    original = np.ones((2, 128))
    folder, session = binary_record(tmp_path, original, size=16)
    controller = VnaController(ReplayBackend(folder, realtime=False), session)
    output = tmp_path / "slow"
    worker = ContinuousRecordingWorker(controller, "Replay", output)
    original_write = ContinuousDatWriter._write_binary_frame
    writes = []
    def slow_write(writer, data):
        time.sleep(.025)
        original_write(writer, data)
        writes.append(1)
        if len(writes) == 3:
            worker.request_stop()
    monkeypatch.setattr(ContinuousDatWriter, "_write_binary_frame", slow_write)
    errors = []
    worker.error.connect(errors.append)
    began = time.monotonic()
    worker.run()
    manifest = json.loads((output / "manifest.json").read_text())
    assert not errors
    assert manifest["completed"]
    assert manifest["total_samples"] == 3 * 16
    assert time.monotonic() - began < 2
    assert len(writes) == 3


def test_flush_failure_closes_handle_and_keeps_incomplete_manifest(tmp_path):
    session = session_for()
    writer = ContinuousDatWriter(tmp_path / "flush", session, device_name=None,
                                 channel_names=["ai0", "ai1"], software_version="test", storage_format="binary")
    writer.write_frame(frame(np.zeros((2, 16))))
    handle = writer._segment.file
    class FailFlush:
        def flush(self):
            raise OSError(28, "full disk on flush (injected)")
        def close(self):
            handle.close()
    writer._segment.file = FailFlush()
    with pytest.raises(OSError, match="flush"):
        writer.close()
    assert handle.closed
    manifest = json.loads(writer.manifest_path.read_text())
    assert not manifest["completed"]
    assert "flush" in manifest["error"]


def test_record_failure_after_disconnect_finalizes_file(tmp_path, monkeypatch):
    from python_vna.ui.main_window import ContinuousRecordingWorker
    folder, session = binary_record(tmp_path, np.ones((2, 64)), size=16)
    backend = ReplayBackend(folder, realtime=False)
    controller = VnaController(backend, session)
    original_read = backend._read_raw
    reads = []
    def interrupted():
        if len(reads) == 2:
            raise DaqBackendError("injected stream disconnected")
        reads.append(1)
        return original_read()
    monkeypatch.setattr(backend, "_read_raw", interrupted)
    output = tmp_path / "interrupted"
    worker = ContinuousRecordingWorker(controller, "Replay", output)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    manifest = json.loads((output / "manifest.json").read_text())
    assert not manifest["completed"]
    assert manifest["total_samples"] == 32
    assert "disconnected" in manifest["error"]
    assert not backend._active
    assert backend._iterator is None
    assert errors


def test_partial_import_skips_unknown_units_and_reports_reason(tmp_path):
    path = tmp_path / "Channel.ini"
    path.write_text("[Channel 0]\nUnit=N\nSensitivity Physical Unit=m/s^2\n"
                    "[Channel 1]\nUnit=m/s^2\nSensitivity Physical Unit=m/s^2\nSensitivity=50\n",
                    encoding="utf-8")
    errors = []
    channels = load_dasp_channels(path, errors=errors)
    assert len(channels) == 1 and channels[0].index == 1
    assert "Channel 0" in errors[0]
