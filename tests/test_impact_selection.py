from __future__ import annotations

import unittest

import numpy as np

from python_vna.controller import VnaController
from python_vna.daq.base import BackendFrame
from python_vna.impact_selection import smart_hit_active
from python_vna.models import AcquisitionConfig, ChannelConfig, SessionConfig
from python_vna.signal_pipeline import FrameProcessor


def impact_config() -> AcquisitionConfig:
    config = AcquisitionConfig(sample_rate=1024.0, frame_size=512, bandwidth_hz=400.0,
                               response_channels=["ai1", "ai2"])
    config.trigger.enabled = True
    config.trigger.mode = "Every Frame"
    config.trigger.source = "ai0"
    config.modal.smart_hit_auto_range_enabled = False
    config.averaging.count = 3
    return config


def impact_frame(index=0, amplitude=1.0, gain=2.0, other_gain=3.0, delay=3) -> BackendFrame:
    force = np.zeros(512)
    force[32] = amplitude
    data = np.array([force, gain * np.roll(force, delay), other_gain * np.roll(force, 5)])
    return BackendFrame(1024.0, ["ai0", "ai1", "ai2"], data,
                        np.arange(512) / 1024.0, index,
                        {"channel_full_scales": {"ai0": 100, "ai1": 100, "ai2": 100}})


class ImpactSelectionTests(unittest.TestCase):
    def test_only_every_frame_linear_average_is_enabled(self):
        config = impact_config()
        self.assertTrue(smart_hit_active(config, True))
        self.assertFalse(smart_hit_active(config, False))
        for mode in ("off", "peak", "exponential"):
            config.averaging.mode = mode
            self.assertFalse(smart_hit_active(config, True))
        config.averaging.mode = "linear"
        config.averaging.count = 2
        self.assertFalse(smart_hit_active(config, True))
        config.averaging.count = 3
        config.trigger.enabled = False
        self.assertFalse(smart_hit_active(config, True))

    def test_three_consistent_hits_match_existing_spectral_average(self):
        config = impact_config()
        processor = FrameProcessor(config)
        original = impact_config()
        original.modal.smart_hit_enabled = False
        baseline = FrameProcessor(original)
        counts = []
        for index, amplitude in enumerate((0.5, 1.0, 1.5)):
            result = processor.process(impact_frame(index, amplitude))
            expected = baseline.process(impact_frame(index, amplitude))
            counts.append(result.metadata["average_count"])
        self.assertEqual(counts, [0, 2, 3])
        self.assertTrue(result.metadata["smart_hit_complete"])
        for channel in ("ai0", "ai1", "ai2"):
            np.testing.assert_allclose(result.spectra["autospectrum"][channel],
                                       expected.spectra["autospectrum"][channel])
        for pair in ("ai0->ai1", "ai0->ai2"):
            np.testing.assert_allclose(result.frf[pair], expected.frf[pair])
            np.testing.assert_allclose(result.coherence[pair], expected.coherence[pair])
        self.assertEqual(len(processor._impact_selector.pending), 0)
        self.assertTrue(all(item.frame is None for item in processor._impact_selector.accepted))

    def test_bad_first_hit_is_replaced_by_consistent_pair(self):
        processor = FrameProcessor(impact_config())
        for index, gain in enumerate((10, 2, 2, 2)):
            result = processor.process(impact_frame(index, gain=gain))
        self.assertEqual(result.metadata["average_count"], 3)
        self.assertEqual(result.metadata["smart_hit_attempted"], 4)
        history = result.metadata["smart_hit_history"]
        self.assertEqual(history[0]["state"], "rejected")
        self.assertEqual([item.number for item in processor._impact_selector.accepted], [2, 3, 4])
        np.testing.assert_allclose(np.abs(result.frf["ai0->ai1"]), 2)

    def test_outlier_does_not_pollute_averages_and_keeps_current_preview(self):
        processor = FrameProcessor(impact_config())
        processor.process(impact_frame(0))
        before = processor.process(impact_frame(1))
        rejected = processor.process(impact_frame(2, gain=8))
        self.assertTrue(rejected.metadata["rejected"])
        self.assertEqual(rejected.metadata["average_count"], 2)
        self.assertEqual(rejected.metadata["smart_hit_channel"], "ai1")
        np.testing.assert_allclose(rejected.spectra["autospectrum"]["ai1"],
                                   before.spectra["autospectrum"]["ai1"])
        self.assertEqual(np.max(rejected.time_data["channels"]["ai1"]), 8)
        final = processor.process(impact_frame(3))
        self.assertTrue(final.metadata["smart_hit_complete"])
        self.assertFalse(final.metadata["smart_hit_incomplete"])

    def test_phase_outlier_is_detected(self):
        processor = FrameProcessor(impact_config())
        processor.process(impact_frame())
        processor.process(impact_frame(1))
        result = processor.process(impact_frame(2, delay=20))
        self.assertEqual(result.metadata["smart_hit_reason"], "FRF 不一致")

    def test_lightly_damped_narrow_resonance_is_not_rejected_for_low_band_coverage(self):
        processor = FrameProcessor(impact_config())
        frequencies = np.fft.rfftfreq(512, 1 / 1024)
        transfer = 40 ** 2 / (40 ** 2 - frequencies ** 2 + 1j * frequencies * 40 / 500)
        for index in range(3):
            frame = impact_frame(index, amplitude=1 + 0.1 * index)
            frame.data[1] = np.fft.irfft(np.fft.rfft(frame.data[0]) * transfer, n=512)
            result = processor.process(frame)
        self.assertEqual(result.metadata["average_count"], 3)

    def test_unchecked_response_does_not_reject_whole_hit(self):
        config = impact_config()
        config.modal.smart_hit_response_channels = ["ai1"]
        processor = FrameProcessor(config)
        for index, gain in enumerate((3, 8, 12)):
            result = processor.process(impact_frame(index, other_gain=gain))
        self.assertEqual(result.metadata["average_count"], 3)
        self.assertIn("ai0->ai2", result.frf)

    def test_force_limits_use_engineering_units_and_per_voltage(self):
        config = impact_config()
        config.modal.smart_hit_force_limits = True
        config.modal.smart_hit_force_min = 500
        config.modal.smart_hit_force_max = 1000
        channel = ChannelConfig("ai0", "Dev1/ai0", sensitivity=0.8,
                                per_eu_mode="/mV", engineering_unit="N")
        processor = FrameProcessor(config, channel_configs=[channel])
        result = processor.process(impact_frame(amplitude=1))
        self.assertEqual(result.metadata["smart_hit_force_peak"], 800)
        result = processor.process(impact_frame(1, amplitude=0.1))
        self.assertEqual(result.metadata["smart_hit_reason"], "力峰值超限")
        self.assertEqual(result.metadata["average_count"], 0)

    def test_basic_rejects_active_even_if_legacy_reject_flags_are_off(self):
        for bad_kind in ("overload", "double", "nan"):
            with self.subTest(bad_kind=bad_kind):
                processor = FrameProcessor(impact_config())
                frame = impact_frame()
                if bad_kind == "overload":
                    frame.metadata["overload"] = True
                elif bad_kind == "double":
                    frame.data[0, 200] = 0.8
                else:
                    frame.data[1, 0] = np.nan
                result = processor.process(frame)
                self.assertEqual(result.metadata["average_count"], 0)
                self.assertEqual(result.metadata["smart_hit_state"], "rejected")
                self.assertEqual(processor._autospectrum_averager.linear_count, 0)

    def test_insufficient_valid_bins_never_seed_reference_group(self):
        processor = FrameProcessor(impact_config())
        for index in range(3):
            frame = impact_frame(index)
            frame.data[1] = 0
            result = processor.process(frame)
        self.assertEqual(result.metadata["average_count"], 0)
        self.assertIn("无法判定", result.metadata["smart_hit_reason"])
        self.assertFalse(processor._impact_selector.pending)

    def test_rejection_history_is_bounded_and_counters_are_not(self):
        processor = FrameProcessor(impact_config())
        for index in range(220):
            frame = impact_frame(index)
            frame.metadata["overload"] = True
            result = processor.process(frame)
        self.assertEqual(len(result.metadata["smart_hit_history"]), 200)
        self.assertEqual(result.metadata["smart_hit_reasons"]["过载"], 220)
        self.assertEqual(result.metadata["smart_hit_consecutive_rejections"], 220)
        self.assertEqual(result.metadata["smart_hit_history"][0]["number"], 21)

    def test_mutually_inconsistent_seeds_keep_only_latest_two_frames(self):
        processor = FrameProcessor(impact_config())
        for index, gain in enumerate((1, 3, 9, 27, 1, 3)):
            result = processor.process(impact_frame(index, gain=gain))
        self.assertEqual(result.metadata["average_count"], 0)
        self.assertEqual(len(processor._impact_selector.pending), 2)
        self.assertEqual(result.metadata["smart_hit_attempted"], 6)

    def test_smart_rejection_updates_controller_without_mutating_previous_measurement(self):
        frames = iter([impact_frame(0), impact_frame(1), impact_frame(2, gain=9)])
        backend = type("Backend", (), {"read_frame": lambda self: next(frames)})()
        controller = VnaController(backend, SessionConfig(acquisition=impact_config()))
        controller.set_averaging_enabled(True)
        controller.read_and_process()
        previous = controller.read_and_process()
        current = controller.read_and_process()
        self.assertFalse(previous.metadata["rejected"])
        self.assertTrue(current.metadata["rejected"])
        self.assertEqual(np.max(current.time_data["channels"]["ai1"]), 9)
        self.assertIs(controller.state.measurement, current)

    def test_smart_mode_rejects_overlap_instead_of_counting_one_hit_twice(self):
        config = impact_config()
        config.overlap_percent = 50
        with self.assertRaisesRegex(ValueError, "无重叠"):
            FrameProcessor(config)

    def test_frequency_band_ignores_changes_outside_requested_range(self):
        config = impact_config()
        config.modal.smart_hit_max_hz = 100
        processor = FrameProcessor(config)
        for index in range(3):
            frame = impact_frame(index)
            if index == 2:
                spectrum = np.fft.rfft(frame.data[1])
                spectrum[np.fft.rfftfreq(512, 1 / 1024) > 100] *= 10
                frame.data[1] = np.fft.irfft(spectrum, n=512)
            result = processor.process(frame)
        self.assertTrue(result.metadata["smart_hit_complete"])

    def test_antiresonance_zero_does_not_reject_consistent_hits(self):
        processor = FrameProcessor(impact_config())
        for index in range(3):
            frame = impact_frame(index, amplitude=1 + index * 0.2)
            spectrum = np.fft.rfft(frame.data[1])
            spectrum[25:30] = 0
            frame.data[1] = np.fft.irfft(spectrum, n=512)
            result = processor.process(frame)
        self.assertEqual(result.metadata["average_count"], 3)

    def test_ten_rejections_reset_after_accepted_hit(self):
        processor = FrameProcessor(impact_config())
        processor.process(impact_frame())
        processor.process(impact_frame(1))
        for index in range(2, 12):
            result = processor.process(impact_frame(index, gain=10))
        self.assertEqual(result.metadata["smart_hit_consecutive_rejections"], 10)
        result = processor.process(impact_frame(12))
        self.assertEqual(result.metadata["smart_hit_consecutive_rejections"], 0)
        self.assertEqual(result.metadata["average_count"], 3)

    def test_modal_windows_are_not_applied_twice_by_selection(self):
        config = impact_config()
        config.modal.enabled = True
        config.modal.force_window_enabled = True
        config.modal.exponential_window_enabled = True
        original = impact_config()
        original.modal.enabled = True
        original.modal.force_window_enabled = True
        original.modal.exponential_window_enabled = True
        original.modal.smart_hit_enabled = False
        processor, baseline = FrameProcessor(config), FrameProcessor(original)
        for index in range(3):
            result = processor.process(impact_frame(index))
            expected = baseline.process(impact_frame(index))
        np.testing.assert_allclose(result.frf["ai0->ai1"], expected.frf["ai0->ai1"])

    def test_starting_smart_run_clears_old_measurement_even_without_a_hit(self):
        session = SessionConfig(acquisition=impact_config())
        controller = VnaController(object(), session)
        controller.state.measurement = object()
        controller.set_averaging_enabled(True)
        self.assertEqual(controller.state.measurement.metadata["average_count"], 0)
        self.assertTrue(controller.state.measurement.metadata["smart_hit_incomplete"])
        self.assertEqual(controller.state.measurement.time_data["channels"], {})

    def test_runtime_configuration_is_stable_until_next_run(self):
        config = impact_config()
        processor = FrameProcessor(config)
        config.modal.smart_hit_tolerance = 2.0
        processor.process(impact_frame())
        processor.process(impact_frame(1))
        result = processor.process(impact_frame(2, gain=8))
        self.assertEqual(result.metadata["average_count"], 2)

    def test_malformed_frame_is_rejected_without_preview_index_errors(self):
        for data in (np.empty((0, 512)), np.zeros(512), np.empty((3, 0))):
            with self.subTest(shape=data.shape):
                processor = FrameProcessor(impact_config())
                frame = impact_frame()
                frame.data = data
                result = processor.process(frame)
                self.assertEqual(result.metadata["average_count"], 0)
                self.assertEqual(result.metadata["smart_hit_state"], "rejected")
                self.assertEqual(result.time_data["t"].size, 0)


if __name__ == "__main__":
    unittest.main()
