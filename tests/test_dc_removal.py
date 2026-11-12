"""Streaming correction and raw-record regression tests; no hardware access."""
import json

import numpy as np
import pytest
from scipy.signal import butter, sosfilt, sosfilt_zi

from python_vna.continuous_recording import ContinuousDatWriter
from python_vna.controller import VnaController
from python_vna.daq.base import BackendFrame, DaqBackendError
from python_vna.daq.dc_removal import DcRemoval
from python_vna.daq.replay import ReplayBackend, ReplayComplete, ReplaySource
from python_vna.models import SavedSession
from python_vna.storage import default_session_config, load_legacy_vna, save_legacy_vna


def session_for(rate=128, size=32):
    session = default_session_config()
    session.acquisition.sample_rate = rate
    session.acquisition.frame_size = size
    session.acquisition.excitation.enabled = False
    session.acquisition.trigger.enabled = False
    session.acquisition.modal.smart_hit_enabled = False
    for index, channel in enumerate(session.ai_channels):
        channel.enabled = index < 2
        channel.dc_removal_enabled = index == 0
        channel.dc_removal_cutoff_hz = .1
    return session


def write_raw(folder, session, data):
    writer = ContinuousDatWriter(
        folder, session, device_name=None, channel_names=["ai0", "ai1"],
        software_version="test", storage_format="binary", segment_seconds=.5)
    size = session.acquisition.frame_size
    for begin in range(0, data.shape[1], size):
        block = data[:, begin:begin + size]
        writer.write_frame(BackendFrame(session.acquisition.sample_rate, ["ai0", "ai1"], block,
                                       np.arange(block.shape[1]) / session.acquisition.sample_rate,
                                       begin // size))
    writer.close()


def test_filter_is_continuous_across_arbitrary_frames_and_preserves_dc_channel():
    s = session_for()
    t = np.arange(4096) / 128
    original = np.vstack((3 + .5 * np.sin(2 * np.pi * t), np.full(t.size, 4.0)))
    f = DcRemoval(s.ai_channels, 128)
    pieces = []
    begin = 0
    for count in [1, 13, 32, 7, 256, 2, 1500, 2285]:
        pieces.append(f.process(["ai0", "ai1"], original[:, begin:begin + count]))
        begin += count
    assert begin == 4096
    output = np.concatenate(pieces, axis=1)
    sos = butter(1, .1, btype="highpass", fs=128, output="sos")
    expected, _ = sosfilt(sos, original[0], zi=sosfilt_zi(sos) * original[0, 0])
    np.testing.assert_allclose(output[0], expected, rtol=0, atol=1e-13)
    np.testing.assert_array_equal(output[1], original[1])
    np.testing.assert_array_equal(original[1], np.full(t.size, 4))
    assert abs(output[0, -2048:].mean()) < .001
    assert np.std(output[0, -2048:]) == pytest.approx(.5 / np.sqrt(2) / np.sqrt(1.01), abs=.001)
    assert f.filters["ai0"]["state"].shape == (1, 2)


def test_constant_dc_is_removed_from_first_sample_and_disabled_mode_is_identity():
    s = session_for()
    f = DcRemoval(s.ai_channels, 128)
    data = np.full((2, 32), 2.5)
    for _ in range(5):
        corrected = f.process(["ai0", "ai1"], data)
        np.testing.assert_allclose(corrected[0], 0, atol=1e-10)
        np.testing.assert_array_equal(corrected[1], data[1])
    for c in s.ai_channels:
        c.dc_removal_enabled = False
    assert DcRemoval(s.ai_channels, 128).process(["ai0", "ai1"], data) is data


@pytest.mark.parametrize("cutoff", [0, -1, float("nan"), float("inf"), 64, 65])
def test_invalid_cutoff_cannot_start(cutoff):
    s = session_for()
    s.ai_channels[0].dc_removal_cutoff_hz = cutoff
    with pytest.raises(DaqBackendError, match="截止频率"):
        DcRemoval(s.ai_channels, 128)


def test_raw_record_replay_reset_and_overlap_remain_consistent(tmp_path):
    s = session_for()
    t = np.arange(512) / 128
    data = np.vstack((3 + np.sin(t * 2 * np.pi), np.full(t.size, 4.0)))
    folder = tmp_path / "source"
    write_raw(folder, s, data)
    source = ReplaySource(folder)
    np.testing.assert_array_equal(np.concatenate(list(source.blocks(13)), axis=1), data)
    expected = DcRemoval(s.ai_channels, 128).process(["ai0", "ai1"], data)
    backend = ReplayBackend(folder, realtime=False)
    for _ in range(2):
        backend.configure(s)
        backend.start()
        outputs, raw = [], []
        try:
            while True:
                frame = backend.read_frame()
                assert frame.metadata["dc_removed"]
                outputs.append(frame.data)
                raw.append(frame.raw_data)
        except ReplayComplete:
            pass
        finally:
            backend.close()
        np.testing.assert_allclose(np.concatenate(outputs, axis=1), expected, atol=1e-13)
        np.testing.assert_array_equal(np.concatenate(raw, axis=1), data)
    s.acquisition.overlap_percent = 50
    controller = VnaController(ReplayBackend(folder, realtime=False), s)
    controller.configure()
    controller.start()
    try:
        for begin in (0, 16, 32, 48, 64):
            frame = controller._read_processing_frame()
            np.testing.assert_array_equal(frame.raw_data, data[:, begin:begin + 32])
            np.testing.assert_allclose(frame.data, expected[:, begin:begin + 32], atol=1e-13)
    finally:
        controller.close()


def test_quality_reports_raw_dc_and_rail_even_when_preview_is_centered():
    from python_vna.daq.quality import AcquisitionQuality

    s = session_for()
    frame = BackendFrame(128, ["ai0", "ai1"], np.full((2, 32), 10.0), np.arange(32) / 128, 0)
    DcRemoval(s.ai_channels, 128).apply_frame(frame)
    q = AcquisitionQuality(s.ai_channels)
    q.add(frame)
    report = q.report()["channels"]["ai0"]
    assert report["mean_v"] == 10
    assert report["rail_fraction"] == 1
    assert abs(report["processed_voltage"]["mean_v"]) < 1e-9
    assert abs(report["processed_voltage"]["last_frame_mean_v"]) < 1e-9
    np.testing.assert_allclose(frame.data[0], 0, atol=1e-9)


def test_selected_cutoff_changes_response_without_forcing_each_frame_mean_to_zero():
    s = session_for(rate=256, size=128)
    time = np.arange(256 * 60) / 256
    signal = 3.5 + .02 * np.sin(2 * np.pi * .2 * time)
    rms = []
    for cutoff in (.1, .7, 1.0):
        s.ai_channels[0].dc_removal_cutoff_hz = cutoff
        correction = DcRemoval(s.ai_channels[:1], 256)
        frames = [correction.process(["ai0"], signal[None, i:i + 128])[0]
                  for i in range(0, signal.size, 128)]
        values = np.concatenate(frames)
        assert correction.description()["channels"]["ai0"]["cutoff_hz"] == cutoff
        assert abs(values[-2560:].mean()) < 1e-7
        assert abs(frames[-1].mean()) > 1e-6
        rms.append(np.sqrt(np.mean(values[-2560:] ** 2)))
    assert rms[0] == pytest.approx(.02 / np.sqrt(2) * .2 / np.hypot(.2, .1), rel=.001)
    assert rms[1] == pytest.approx(.02 / np.sqrt(2) * .2 / np.hypot(.2, .7), rel=.001)
    assert rms[2] == pytest.approx(.02 / np.sqrt(2) * .2 / np.hypot(.2, 1), rel=.001)
    assert rms[0] > rms[2] * 4


def test_quality_keeps_raw_and_processed_statistics_separate_across_frames():
    from python_vna.daq.quality import AcquisitionQuality

    s = session_for()
    quality = AcquisitionQuality(s.ai_channels)
    for values in (np.array([.1, .2, -.1]), np.array([-.2, .3])):
        data = values[None, :]
        frame = BackendFrame(128, ["ai0"], data, np.arange(values.size) / 128, 0,
                             raw_data=data + 3)
        quality.add(frame)
    report = quality.report()["channels"]["ai0"]
    assert report["mean_v"] == pytest.approx(3.06)
    processed = report["processed_voltage"]
    assert processed["samples"] == 5
    assert processed["mean_v"] == pytest.approx(.06)
    assert processed["rms_v"] == pytest.approx(np.sqrt(.19 / 5))
    assert processed["last_frame_mean_v"] == pytest.approx(.05)


@pytest.mark.parametrize("enabled", [True, False])
def test_save_default_configuration_before_any_capture_keeps_dc_options(tmp_path, enabled):
    s = session_for()
    s.ai_channels[0].dc_removal_enabled = enabled
    s.ai_channels[0].dc_removal_cutoff_hz = .2
    path = tmp_path / "default.vna"
    save_legacy_vna(SavedSession(s), path)
    loaded = load_legacy_vna(path)
    assert loaded.config.ai_channels[0].dc_removal_enabled is enabled
    assert loaded.config.ai_channels[0].dc_removal_cutoff_hz == .2


def test_vna_configuration_and_processed_data_roundtrip_without_double_filter(tmp_path):
    s = session_for()
    data = np.vstack((3 + np.sin(np.arange(64)), np.full(64, 4.0)))
    write_raw(tmp_path / "record", s, data)
    ctrl = VnaController(ReplayBackend(tmp_path / "record", realtime=False), s)
    ctrl.configure()
    ctrl.start()
    try:
        measurement = ctrl.read_and_process()
    finally:
        ctrl.close()
    path = tmp_path / "processed.vna"
    save_legacy_vna(SavedSession(s, measurement), path)
    restored = load_legacy_vna(path)
    assert restored.config.ai_channels[0].dc_removal_enabled
    assert restored.config.ai_channels[0].dc_removal_cutoff_hz == .1
    assert restored.measurement.metadata["daq_provenance"]["dc_removed"]
    replay = ReplayBackend(path, realtime=False)
    replay.configure(restored.config)
    replay.start()
    try:
        frame = replay.read_frame()
        np.testing.assert_array_equal(frame.data[0], measurement.time_data["channels"]["ai0"])
        assert frame.raw_data is None
        assert frame.metadata["dc_removed"]
    finally:
        replay.close()
    # Recording an already processed source cannot pretend to recover raw DC.
    from python_vna.ui.main_window import ContinuousRecordingWorker

    output = tmp_path / "processed_record"
    controller = VnaController(ReplayBackend(path, realtime=False), restored.config)
    worker = ContinuousRecordingWorker(controller, None, output)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert not errors
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["daq_provenance"]["dc_removed"]
    again = ReplayBackend(output, realtime=False)
    again.configure(restored.config)
    again.start()
    try:
        frame = again.read_frame()
        np.testing.assert_array_equal(frame.data[0], measurement.time_data["channels"]["ai0"])
        assert frame.raw_data is None
    finally:
        again.close()


def test_record_worker_keeps_raw_bytes_but_previews_corrected_voltage(tmp_path):
    from python_vna.ui.main_window import ContinuousRecordingWorker

    s = session_for()
    raw = np.full((2, 128), 3.0)
    source = tmp_path / "source"
    write_raw(source, s, raw)
    controller = VnaController(ReplayBackend(source, realtime=False), s)
    output = tmp_path / "output"
    worker = ContinuousRecordingWorker(controller, None, output, preview_interval_seconds=0,
                                       segment_seconds=.5)
    errors, previews = [], []
    worker.error.connect(errors.append)
    worker.preview_ready.connect(previews.append)
    worker.run()
    assert not errors
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["completed"]
    provenance = manifest["daq_provenance"]
    assert not provenance["dc_removed"]
    assert provenance["preview_dc_removed"]
    assert provenance["dc_removal"]["channels"]["ai0"]["cutoff_hz"] == .1
    stored = np.concatenate(list(ReplaySource(output).blocks(17)), axis=1)
    np.testing.assert_array_equal(stored, raw)
    assert previews
    for preview in previews:
        np.testing.assert_allclose(preview.time_data["channels"]["ai0"], 0, atol=1e-10)
        np.testing.assert_array_equal(preview.time_data["channels"]["ai1"], np.full(32, 3.0))
        assert preview.metadata["daq_provenance"]["dc_removed"]
