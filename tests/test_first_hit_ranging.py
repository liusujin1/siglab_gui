from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from python_vna.controller import VnaController
from python_vna.daq.ni import NIDaqBackend
from python_vna.impact_selection import first_hit_range
from python_vna.models import ChannelConfig, SessionConfig
from python_vna.signal_pipeline import FrameProcessor, detect_overload
from test_impact_selection import impact_config, impact_frame


def ranging_session():
    config = impact_config()
    config.modal.smart_hit_auto_range_enabled = True
    return SessionConfig(acquisition=config, ai_channels=[
        ChannelConfig("ai0", "Dev1/ai0", full_scale=0.01953125, sensitivity=800, engineering_unit="N"),
        ChannelConfig("ai1", "Dev1/ai1", full_scale=10),
        ChannelConfig("ai2", "Dev1/ai2", full_scale=10),
    ])


class RangeBackend:
    def __init__(self, amplitudes=(1, 1, 1)):
        self.amplitudes = iter(amplitudes)
        self.calls = []
        self.configured_levels = []
        self.index = 0

    def configure(self, session, device_name=None):
        self.session = session
        self.configured_levels.append(session.acquisition.trigger.level)

    def start(self):
        pass

    def read_frame(self):
        frame = impact_frame(self.index, next(self.amplitudes))
        self.index += 1
        frame.metadata["channel_full_scales"] = {channel.name: channel.full_scale for channel in self.session.ai_channels}
        frame.metadata["channel_hardware_ranges"] = {channel.name: (-10, 10) for channel in self.session.ai_channels}
        return frame

    def update_trigger_level(self, level_v):
        self.calls.append((threading.get_ident(), level_v))
        return level_v

    def stop(self):
        pass

    def close(self):
        pass


class FirstHitRangeTests(unittest.TestCase):
    def test_each_run_captures_with_independent_voltage_before_switching_to_35_percent(self):
        session = ranging_session()
        session.ai_channels[0].full_scale = 10
        session.acquisition.trigger.level = 3.5
        session.acquisition.trigger.level_percent = 35
        backend = RangeBackend()
        controller = VnaController(backend, session)
        for amplitude in (1., .1):
            backend.amplitudes = iter([amplitude] * 3)
            controller.set_averaging_enabled(True)
            controller.configure()
            self.assertEqual(backend.configured_levels[-1], .01)
            self.assertEqual(session.acquisition.trigger.level, .01)
            self.assertEqual(backend.index, len(backend.calls) * 3)
            # A trigger-aware source would never deliver these hits at the old 3.5 V level.
            self.assertLess(backend.configured_levels[-1], amplitude)
            for _ in range(3):
                result = controller.read_and_process()
            self.assertEqual(result.metadata["average_count"], 3)
            self.assertAlmostEqual(session.acquisition.trigger.level, amplitude * .35)
            self.assertEqual(result.metadata["smart_hit_capture_level_v"], .01)
        self.assertEqual(backend.configured_levels, [.01, .01])
        self.assertEqual(backend.index, 6)
        self.assertEqual(len(backend.calls), 2)

    def test_custom_capture_voltage_is_independent_of_all_display_ranges(self):
        for full_scale in (.01953125, 1.25, 10):
            with self.subTest(full_scale=full_scale):
                session = ranging_session()
                session.ai_channels[0].full_scale = full_scale
                session.acquisition.modal.smart_hit_initial_trigger_v = .002345
                backend = RangeBackend()
                controller = VnaController(backend, session)
                controller.set_averaging_enabled(True)
                controller.configure()
                self.assertEqual(backend.configured_levels, [.002345])
                self.assertEqual(session.ai_channels[0].full_scale, full_scale)
                self.assertAlmostEqual(session.acquisition.trigger.level_percent, .002345 / full_scale * 100)

    def test_noise_trigger_does_not_lock_a_range_or_change_capture_voltage(self):
        session = ranging_session()
        backend = RangeBackend((1, 1, 1))
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        noise = impact_frame()
        noise.data[0] = np.random.default_rng(7).normal(0, .02, 512)
        with mock.patch.object(backend, "read_frame", return_value=noise):
            result = controller.read_and_process()
        self.assertTrue(result.metadata["rejected"])
        self.assertNotIn("smart_hit_auto_range", result.metadata)
        self.assertEqual(session.acquisition.trigger.level, .01)
        self.assertEqual(backend.calls, [])
        for _ in range(3):
            result = controller.read_and_process()
        self.assertEqual(result.metadata["average_count"], 3)
        self.assertEqual(result.metadata["smart_hit_attempted"], 4)
        self.assertEqual(len(backend.calls), 1)

    def test_other_acquisition_modes_keep_the_manual_trigger_voltage(self):
        for mode in ("inst", "auto_off", "smart_off", "count_two", "other_trigger"):
            with self.subTest(mode=mode):
                session = ranging_session()
                session.acquisition.trigger.level = 1.75
                session.acquisition.trigger.level_percent = 17.5
                if mode == "auto_off":
                    session.acquisition.modal.smart_hit_auto_range_enabled = False
                elif mode == "smart_off":
                    session.acquisition.modal.smart_hit_enabled = False
                elif mode == "count_two":
                    session.acquisition.averaging.count = 2
                elif mode == "other_trigger":
                    session.acquisition.trigger.mode = "Manual Arm"
                backend = RangeBackend()
                controller = VnaController(backend, session)
                controller.set_averaging_enabled(mode != "inst")
                controller.configure()
                self.assertEqual(backend.configured_levels, [1.75])
                self.assertEqual(session.acquisition.trigger.level_percent, 17.5)

    def test_capture_validation_and_failed_configuration_leave_original_trigger_unchanged(self):
        for level in (0, -1, 10, float("nan"), float("inf")):
            with self.subTest(level=level):
                session = ranging_session()
                session.acquisition.modal.smart_hit_initial_trigger_v = level
                controller = VnaController(RangeBackend(), session)
                with self.assertRaisesRegex(ValueError, "首锤捕获阈值"):
                    controller.set_averaging_enabled(True)
        session = ranging_session()
        session.acquisition.trigger.level = 3.5
        session.acquisition.trigger.level_percent = 35
        backend = RangeBackend()
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        with mock.patch.object(backend, "configure", side_effect=RuntimeError("configure failed")):
            with self.assertRaisesRegex(RuntimeError, "configure failed"):
                controller.configure()
        self.assertEqual(session.acquisition.trigger.level, 3.5)
        self.assertEqual(session.acquisition.trigger.level_percent, 35)
        self.assertEqual(backend.index, 0)

    def test_positive_and_negative_peak_select_same_gear_and_voltage(self):
        for sign in (-1, 1):
            settings = first_hit_range(impact_frame(amplitude=sign * 0.13).data[0], "ai0")
            self.assertEqual(settings["full_scale_v"], 0.15625)
            self.assertAlmostEqual(settings["trigger_level_v"], 0.0455)
        self.assertEqual(first_hit_range(impact_frame(amplitude=9).data[0], "ai0")["full_scale_v"], 10)

    def test_background_or_zero_signal_cannot_lower_trigger_into_noise(self):
        for signal in (np.zeros(512), np.ones(512) * 4, np.random.default_rng(1).normal(0, 0.1, 512)):
            with self.assertRaises(ValueError):
                first_hit_range(signal, "ai0")

    def test_both_configured_range_and_hardware_overload_are_enforced(self):
        metadata = {"channel_full_scales": {"ai0": 0.01}, "channel_hardware_ranges": {"ai0": (-10, 10)},
                    "overload_channels": []}
        self.assertTrue(detect_overload(np.array([[0, 0.5, -0.7]]), ["ai0"], metadata))
        metadata["channel_full_scales"]["ai0"] = 2.5
        self.assertFalse(detect_overload(np.array([[0, 0.5, -0.7]]), ["ai0"], metadata))
        for value in (-10, 10):
            self.assertTrue(detect_overload(np.array([[value]]), ["ai0"], metadata))
        metadata["overload"] = True
        self.assertTrue(detect_overload(np.array([[0.1]]), ["ai0"], metadata))

    def test_software_range_is_enforced_even_when_hardware_range_is_unknown(self):
        self.assertTrue(detect_overload(np.array([[0.5]]), ["ai0"], {
            "channel_full_scales": {"ai0": 0.01}, "channel_hardware_ranges": {}}))

    def test_first_hit_is_not_discarded_and_settings_lock_until_next_run(self):
        session = ranging_session()
        backend = RangeBackend((1, 3, 1, 1))
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        results = []
        for _ in range(4):
            result = controller.read_and_process()
            results.append(result)
        self.assertTrue(results[1].metadata["overload_rejected"])
        self.assertEqual(results[1].metadata["average_count"], 0)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1], 0.35)
        self.assertEqual(session.ai_channels[0].full_scale, 1.25)
        self.assertEqual([channel.full_scale for channel in session.ai_channels[1:]], [10, 10])
        self.assertAlmostEqual(session.acquisition.trigger.level_percent, 28)
        self.assertEqual(result.metadata["average_count"], 3)
        self.assertEqual(result.metadata["smart_hit_auto_range"]["peak_v"], 1)
        self.assertEqual(result.metadata["smart_hit_force_peak"], 800)
        self.assertEqual(result.metadata["smart_hit_attempted"], 4)
        backend.amplitudes = iter([0.2])
        controller.set_averaging_enabled(True)
        controller.configure()
        result = controller.read_and_process()
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(result.metadata["smart_hit_auto_range"]["full_scale_v"], 0.3125)

    def test_response_software_overload_does_not_block_reference_calibration_or_enter_average(self):
        session = ranging_session()
        session.ai_channels[1].full_scale = 0.1
        backend = RangeBackend((1, 1, 1, 1))
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        result = controller.read_and_process()
        self.assertTrue(result.metadata["overload_rejected"])
        self.assertEqual(result.metadata["average_count"], 0)
        self.assertEqual(controller.processor._impact_selector.pending, [])
        self.assertEqual(result.metadata["smart_hit_auto_range"]["state"], "applied")
        self.assertEqual(session.ai_channels[0].full_scale, 1.25)
        self.assertEqual(session.ai_channels[1].full_scale, 0.1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result.metadata["smart_hit_channel"], "ai1")
        self.assertEqual(result.metadata["overload_details"], [
            {"kind": "software", "channel": "ai1", "peak_v": 2., "limit_v": .1}
        ])
        session.ai_channels[1].full_scale = 10.
        for _ in range(3):
            result = controller.read_and_process()
        self.assertEqual(result.metadata["average_count"], 3)
        self.assertEqual(len(backend.calls), 1)

    def test_response_hardware_overload_still_blocks_reference_calibration(self):
        session = ranging_session()
        processor = FrameProcessor(session.acquisition, channel_configs=session.ai_channels)
        frame = impact_frame()
        frame.metadata["overload_channels"] = ["Dev1/ai1"]
        result = processor.process(frame)
        self.assertTrue(result.metadata["overload_rejected"])
        self.assertNotIn("smart_hit_auto_range", result.metadata)
        self.assertEqual(result.metadata["average_count"], 0)
        self.assertEqual(result.metadata["smart_hit_channel"], "Dev1/ai1")

    def test_double_hit_and_force_limit_do_not_calibrate_even_when_response_is_over_range(self):
        for problem in ("double", "force"):
            with self.subTest(problem=problem):
                session = ranging_session()
                session.ai_channels[1].full_scale = .1
                frame = impact_frame()
                if problem == "double":
                    frame.data[0, 128] = .8
                else:
                    session.acquisition.modal.smart_hit_force_limits = True
                    session.acquisition.modal.smart_hit_force_max = 100.
                processor = FrameProcessor(session.acquisition, channel_configs=session.ai_channels)
                frame.metadata["channel_full_scales"]["ai1"] = .1
                result = processor.process(frame)
                self.assertTrue(result.metadata["rejected"])
                self.assertNotIn("smart_hit_auto_range", result.metadata)
                self.assertEqual(result.metadata["average_count"], 0)

    def test_explicit_manual_range_after_calibration_remains_the_reject_limit(self):
        session = ranging_session()
        backend = RangeBackend((1, 1.5, 0.8))
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        controller.read_and_process()
        session.ai_channels[0].full_scale = 2.5
        result = controller.read_and_process()
        self.assertFalse(result.metadata["overload_rejected"])
        self.assertEqual(result.metadata["average_count"], 2)
        session.ai_channels[0].full_scale = 0.625
        result = controller.read_and_process()
        self.assertTrue(result.metadata["overload_rejected"])
        self.assertEqual(result.metadata["average_count"], 2)
        self.assertEqual(len(backend.calls), 1)

    def test_initial_bad_hit_does_not_lock_range(self):
        session = ranging_session()
        processor = FrameProcessor(session.acquisition, channel_configs=session.ai_channels)
        frame = impact_frame()
        frame.metadata["overload"] = True
        result = processor.process(frame)
        self.assertNotIn("smart_hit_auto_range", result.metadata)
        result = processor.process(impact_frame(1, amplitude=0.2))
        self.assertEqual(result.metadata["smart_hit_auto_range"]["full_scale_v"], 0.3125)

    def test_disabled_auto_range_keeps_trigger_and_range(self):
        session = ranging_session()
        session.acquisition.modal.smart_hit_auto_range_enabled = False
        backend = RangeBackend()
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        result = controller.read_and_process()
        self.assertNotIn("smart_hit_auto_range", result.metadata)
        self.assertEqual(backend.calls, [])
        self.assertEqual(session.ai_channels[0].full_scale, 0.01953125)

    def test_wrong_trigger_source_is_rejected_before_hardware_configuration(self):
        session = ranging_session()
        session.acquisition.trigger.source = "ai1"
        controller = VnaController(RangeBackend(), session)
        with self.assertRaisesRegex(ValueError, "参考通道"):
            controller.set_averaging_enabled(True)

    def test_update_failure_preserves_first_hit_and_does_not_claim_success(self):
        session = ranging_session()
        backend = RangeBackend()
        def fail(_level):
            raise RuntimeError("driver failure")
        backend.update_trigger_level = fail
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        with self.assertRaisesRegex(RuntimeError, "触发更新失败"):
            controller.read_and_process()
        self.assertEqual(controller.state.measurement.metadata["smart_hit_auto_range"]["state"], "failed")
        self.assertEqual(np.max(controller.state.measurement.time_data["channels"]["ai0"]), 1)
        self.assertEqual(session.ai_channels[0].full_scale, 0.01953125)

    def test_stop_between_read_and_update_skips_hardware_change(self):
        session = ranging_session()
        backend = RangeBackend()
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        read = backend.read_frame
        def stopped_read():
            frame = read()
            controller.request_stop()
            return frame
        backend.read_frame = stopped_read
        result = controller.read_and_process()
        self.assertEqual(backend.calls, [])
        self.assertEqual(result.metadata["smart_hit_auto_range"]["state"], "cancelled")


class NITriggerUpdateTests(unittest.TestCase):
    def test_start_and_pretrigger_windows_update_without_task_recreation(self):
        for delay in (0, -20):
            with self.subTest(delay=delay):
                calls = []
                class Trigger:
                    def configure(self, **kwargs):
                        calls.append(kwargs)
                        self.anlg_win_top = kwargs["window_top"]
                    cfg_anlg_window_start_trig = configure
                    cfg_anlg_window_ref_trig = configure
                task = SimpleNamespace(triggers=SimpleNamespace(start_trigger=Trigger(), reference_trigger=Trigger()),
                                       control=lambda mode: calls.append(mode))
                backend = NIDaqBackend()
                backend._ai_task = task
                backend._finite_ai_sampling = True
                backend._session = ranging_session()
                backend._session.acquisition.trigger.pretrigger_samples = delay
                backend._constants = SimpleNamespace(WindowTriggerCondition1=SimpleNamespace(LEAVING_WINDOW="leave"),
                                                      TaskMode=SimpleNamespace(TASK_UNRESERVE="unreserve"))
                result = backend.update_trigger_level(0.013)
                self.assertEqual(result, 0.013)
                self.assertIs(backend._ai_task, task)
                self.assertEqual(calls[0], "unreserve")
                self.assertEqual(calls[1]["window_bottom"], -0.013)
                self.assertEqual(calls[1]["window_top"], 0.013)
                self.assertEqual(calls[1].get("pretrigger_samples", 0), 102 if delay else 0)
                self.assertEqual(backend._session.acquisition.trigger.pretrigger_samples, delay)


if __name__ == "__main__":
    unittest.main()
