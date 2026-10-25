from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

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
        self.index = 0

    def configure(self, session, device_name=None):
        self.session = session

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

    def test_only_reference_software_range_is_bypassed_for_calibration(self):
        session = ranging_session()
        session.ai_channels[1].full_scale = 0.1
        backend = RangeBackend()
        controller = VnaController(backend, session)
        controller.set_averaging_enabled(True)
        controller.configure()
        result = controller.read_and_process()
        self.assertTrue(result.metadata["overload_rejected"])
        self.assertNotIn("smart_hit_auto_range", result.metadata)
        self.assertEqual(backend.calls, [])

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
