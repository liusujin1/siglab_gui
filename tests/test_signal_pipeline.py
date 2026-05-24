from __future__ import annotations

import unittest

import numpy as np

from python_vna.daq.base import BackendFrame
from python_vna.daq.ni import NIDaqBackend
from python_vna.models import (
    AcquisitionConfig,
    AveragingConfig,
    ChannelConfig,
    ModalProcessingConfig,
    SessionConfig,
)
from python_vna.signal_pipeline import (
    FrameProcessor,
    RunningAverager,
    apply_modal_processing,
    compute_autospectrum,
    compute_coherence,
    compute_coherence_from_spectra,
    compute_fft,
    compute_frf,
    detect_double_hit,
    exponential_window,
    force_window,
)


class SignalPipelineTests(unittest.TestCase):
    def test_frf_matches_gain_for_scaled_signal(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        reference = np.sin(2.0 * np.pi * 32.0 * t)
        response = 2.5 * reference
        ref_fft = np.fft.rfft(reference)
        resp_fft = np.fft.rfft(response)
        frf = compute_frf(ref_fft, resp_fft)
        peak = np.argmax(np.abs(ref_fft))
        self.assertAlmostEqual(np.abs(frf[peak]), 2.5, places=3)

    def test_coherence_is_near_one_for_identical_signals(self):
        t = np.arange(256) / 256.0
        data = np.sin(2.0 * np.pi * 16.0 * t)
        spectrum = np.fft.rfft(data)
        coherence = compute_coherence(spectrum, spectrum)
        self.assertTrue(np.allclose(coherence[1:], 1.0))

    def test_coherence_from_averaged_spectra_matches_legacy_formula(self):
        gxx = np.array([4.0, 9.0])
        gyy = np.array([16.0, 25.0])
        gxy = np.array([4.0 + 0.0j, 12.0 + 0.0j])

        coherence = compute_coherence_from_spectra(gxx, gyy, gxy)

        np.testing.assert_allclose(coherence, np.array([0.25, 0.64]))

    def test_fft_and_aspec_use_rms_units_like_vna_storage(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ch1"],
            data=np.sin(2.0 * np.pi * 32.0 * t)[None, :],
            timestamps=t,
            frame_index=0,
        )
        freqs, fft_data = compute_fft(frame)
        _, autospectrum = compute_autospectrum(frame)
        peak = int(np.argmin(np.abs(freqs - 32.0)))

        self.assertAlmostEqual(abs(fft_data[0, peak]), 1.0 / np.sqrt(2.0), places=6)
        self.assertAlmostEqual(autospectrum[0, peak], 0.5, places=6)

    def test_fft_dc_bin_is_not_sine_rms_scaled(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ch1"],
            data=np.full((1, samples), 2.0),
            timestamps=t,
            frame_index=0,
        )

        _freqs, fft_data = compute_fft(frame)
        _freqs, autospectrum = compute_autospectrum(frame)

        self.assertAlmostEqual(abs(fft_data[0, 0]), 2.0, places=6)
        self.assertAlmostEqual(autospectrum[0, 0], 4.0, places=6)

    def test_hanning_window_uses_legacy_siglab_coherent_gain(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ch1"],
            data=np.sin(2.0 * np.pi * 32.0 * t)[None, :],
            timestamps=t,
            frame_index=0,
            metadata={"processing_window": "hanning"},
        )

        freqs, fft_data = compute_fft(frame)
        _, autospectrum = compute_autospectrum(frame)
        peak = int(np.argmin(np.abs(freqs - 32.0)))

        self.assertAlmostEqual(abs(fft_data[0, peak]), 1.0 / np.sqrt(2.0), places=6)
        self.assertAlmostEqual(
            autospectrum[0, peak],
            0.5 * 2.0 / 3.0,
            places=6,
        )

    def test_hanning_autospectrum_integral_preserves_rms_power(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        signal = np.sin(2.0 * np.pi * 32.0 * t)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ch1"],
            data=signal[None, :],
            timestamps=t,
            frame_index=0,
            metadata={"processing_window": "hanning"},
        )

        _freqs, autospectrum = compute_autospectrum(frame)

        self.assertAlmostEqual(np.sum(autospectrum[0]), np.mean(signal * signal), places=6)

    def test_hanning_power_spectrum_scale_is_reported_and_preserves_frf(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.processing_window = "hanning"
        acquisition.reference_channel = "ref"
        acquisition.response_channels = ["resp"]
        acquisition.averaging = AveragingConfig(mode="linear", count=1)
        processor = FrameProcessor(acquisition)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ref", "resp"],
            data=np.vstack(
                [
                    np.sin(2.0 * np.pi * 32.0 * t),
                    2.0 * np.sin(2.0 * np.pi * 32.0 * t),
                ]
            ),
            timestamps=t,
            frame_index=0,
        )

        measurement = processor.process(frame)

        peak = int(np.argmin(np.abs(measurement.spectra["f"] - 32.0)))
        self.assertAlmostEqual(
            measurement.metadata["legacy_power_spectrum_scale"],
            2.0 / 3.0,
            places=9,
        )
        self.assertAlmostEqual(
            measurement.spectra["autospectrum"]["ref"][peak],
            0.5 * 2.0 / 3.0,
            places=6,
        )
        self.assertAlmostEqual(abs(measurement.frf["ref->resp"][peak]), 2.0, places=6)
        self.assertAlmostEqual(measurement.coherence["ref->resp"][peak], 1.0, places=6)

    def test_avg_frame_processor_builds_cross_channel_outputs(self):
        sample_rate = 2048.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        ref = np.sin(2.0 * np.pi * 20.0 * t)
        resp = 0.3 * np.sin(2.0 * np.pi * 20.0 * t + 0.2)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ref", "resp"],
            data=np.vstack([ref, resp]),
            timestamps=t,
            frame_index=0,
        )
        acquisition = AcquisitionConfig()
        acquisition.averaging = AveragingConfig(mode="off")
        processor = FrameProcessor(acquisition, averaging_enabled=True)
        measurement = processor.process(frame)
        self.assertIn("ref->resp", measurement.frf)
        self.assertIn("ref->resp", measurement.coherence)
        self.assertEqual(len(measurement.spectra["f"]), samples // 2 + 1)
        self.assertAlmostEqual(measurement.metadata["rbw_hz"], sample_rate / samples)

    def test_frame_processor_averages_spectral_quantities_for_xfer_and_coherence(self):
        sample_rate = 2048.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.reference_channel = "ref"
        acquisition.response_channels = ["resp"]
        acquisition.averaging = AveragingConfig(mode="linear", count=2)
        processor = FrameProcessor(acquisition)

        for gain in (2.0, 2.0):
            ref = np.sin(2.0 * np.pi * 64.0 * t)
            resp = gain * ref
            frame = BackendFrame(
                sample_rate=sample_rate,
                channel_names=["ref", "resp"],
                data=np.vstack([ref, resp]),
                timestamps=t,
                frame_index=0,
            )
            measurement = processor.process(frame)

        peak = int(np.argmin(np.abs(measurement.spectra["f"] - 64.0)))
        self.assertAlmostEqual(abs(measurement.frf["ref->resp"][peak]), 2.0, places=6)
        self.assertAlmostEqual(measurement.coherence["ref->resp"][peak], 1.0, places=6)

    def test_inst_mode_matches_legacy_supported_functions_only(self):
        sample_rate = 2048.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.reference_channel = "ref"
        acquisition.response_channels = ["resp"]
        acquisition.averaging = AveragingConfig(mode="linear", count=4)
        processor = FrameProcessor(acquisition, averaging_enabled=False)

        ref = np.sin(2.0 * np.pi * 64.0 * t)
        resp = -np.sin(2.0 * np.pi * 64.0 * t)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ref", "resp"],
            data=np.vstack([ref, resp]),
            timestamps=t,
            frame_index=0,
        )

        measurement = processor.process(frame)

        self.assertTrue(measurement.time_data["channels"])
        self.assertTrue(measurement.spectra["autospectrum"])
        self.assertTrue(measurement.spectra["fft"])
        self.assertFalse(measurement.frf)
        self.assertFalse(measurement.coherence)
        self.assertFalse(measurement.cross_spectra)
        self.assertFalse(measurement.correlations)
        self.assertFalse(measurement.impulse_responses)
        self.assertEqual(measurement.metadata["legacy_inst_functions"], ["time", "autospectrum", "fft"])
        self.assertTrue(measurement.metadata["cross_functions_require_avg"])
        self.assertFalse(measurement.metadata["cross_functions_available"])

    def test_frame_processor_can_disable_averaging_for_instant_run(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.averaging = AveragingConfig(mode="linear", count=8)
        processor = FrameProcessor(acquisition, averaging_enabled=False)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ch1"],
            data=np.sin(2.0 * np.pi * 32.0 * t)[None, :],
            timestamps=t,
            frame_index=0,
        )

        measurement = processor.process(frame)

        self.assertFalse(measurement.metadata["averaging_enabled"])
        self.assertEqual(measurement.metadata["average_count"], 0)
        self.assertEqual(measurement.metadata["average_target"], 0)

    def test_frame_processor_reports_average_progress(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.averaging = AveragingConfig(mode="linear", count=3)
        processor = FrameProcessor(acquisition, averaging_enabled=True)

        for index in range(2):
            frame = BackendFrame(
                sample_rate=sample_rate,
                channel_names=["ch1"],
                data=np.sin(2.0 * np.pi * 32.0 * t)[None, :],
                timestamps=t,
                frame_index=index,
            )
            measurement = processor.process(frame)

        self.assertTrue(measurement.metadata["averaging_enabled"])
        self.assertEqual(measurement.metadata["average_count"], 2)
        self.assertEqual(measurement.metadata["average_target"], 3)

    def test_linear_averager_holds_target_average_after_extra_update(self):
        averager = RunningAverager(AveragingConfig(mode="linear", count=3))

        first = averager.update(np.array([1.0, 10.0]))
        second = averager.update(np.array([2.0, 20.0]))
        third = averager.update(np.array([3.0, 30.0]))
        extra = averager.update(np.array([99.0, 990.0]))

        np.testing.assert_allclose(first, np.array([1.0, 10.0]))
        np.testing.assert_allclose(second, np.array([1.5, 15.0]))
        np.testing.assert_allclose(third, np.array([2.0, 20.0]))
        np.testing.assert_allclose(extra, np.array([2.0, 20.0]))
        self.assertEqual(averager.linear_count, 3)

    def test_frame_processor_average_count_is_clamped_after_target(self):
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.averaging = AveragingConfig(mode="linear", count=3)
        processor = FrameProcessor(acquisition, averaging_enabled=True)

        for index, amplitude in enumerate((1.0, 2.0, 3.0, 99.0)):
            frame = BackendFrame(
                sample_rate=sample_rate,
                channel_names=["ch1"],
                data=(amplitude * np.sin(2.0 * np.pi * 32.0 * t))[None, :],
                timestamps=t,
                frame_index=index,
            )
            measurement = processor.process(frame)

        peak = int(np.argmin(np.abs(measurement.spectra["f"] - 32.0)))
        self.assertEqual(measurement.metadata["average_count"], 3)
        self.assertEqual(measurement.metadata["average_target"], 3)
        self.assertAlmostEqual(
            measurement.spectra["autospectrum"]["ch1"][peak],
            (1.0**2 + 2.0**2 + 3.0**2) / 3.0 * 0.5,
            places=6,
        )

    def test_double_hit_reject_respects_legacy_delay_percent(self):
        reference = np.zeros(100)
        reference[10] = 1.0
        reference[15] = 0.8
        reference[40] = 0.7

        self.assertFalse(detect_double_hit(reference, threshold=0.5, delay_fraction=0.4))
        self.assertTrue(detect_double_hit(reference, threshold=0.5, delay_fraction=0.2))

    def test_modal_metadata_documents_legacy_processing_flow(self):
        sample_rate = 1024.0
        samples = 256
        t = np.arange(samples) / sample_rate
        acquisition = AcquisitionConfig()
        acquisition.reference_channel = "ref"
        acquisition.response_channels = ["resp"]
        acquisition.modal = ModalProcessingConfig(enabled=True)
        processor = FrameProcessor(acquisition)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ref", "resp"],
            data=np.vstack([
                np.sin(2.0 * np.pi * 32.0 * t),
                0.5 * np.sin(2.0 * np.pi * 32.0 * t),
            ]),
            timestamps=t,
            frame_index=0,
        )

        measurement = processor.process(frame)

        self.assertIn("FRF uses averaged G_yx/G_xx", measurement.metadata["modal_processing_note"])

    def test_modal_processing_uses_all_modal_parameter_controls(self):
        sample_rate = 1000.0
        t = np.arange(10, dtype=float) / sample_rate
        ref = np.zeros(10, dtype=float)
        ref[1] = 1.0
        ref[5] = 0.8
        resp = np.linspace(0.1, 1.0, 10)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ref", "resp"],
            data=np.vstack([ref, resp]),
            timestamps=t,
            frame_index=0,
        )
        modal = ModalProcessingConfig(
            enabled=True,
            force_window_enabled=True,
            force_window_fraction=0.3,
            exponential_window_enabled=True,
            exponential_decay_fraction=0.5,
            reject_double_hit=True,
            double_hit_threshold=0.5,
            double_hit_delay_fraction=0.2,
            reject_overload=True,
        )

        processed, flags = apply_modal_processing(
            frame, {"ref": 0, "resp": 1}, "ref", modal
        )

        self.assertTrue(flags["double_hit_rejected"])
        self.assertTrue(flags["overload_rejected"])
        self.assertTrue(flags["rejected"])
        np.testing.assert_allclose(processed.data[0], ref * force_window(10, 0.3))
        np.testing.assert_allclose(processed.data[1], resp * exponential_window(10, 0.5))

    def test_processing_window_changes_fft_result(self):
        acquisition = AcquisitionConfig()
        acquisition.averaging = AveragingConfig(mode="off")
        sample_rate = 1024.0
        samples = 1024
        t = np.arange(samples) / sample_rate
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=["ai0", "ai1"],
            data=np.vstack(
                [
                    np.sin(2.0 * np.pi * 31.5 * t),
                    0.5 * np.sin(2.0 * np.pi * 31.5 * t + 0.2),
                ]
            ),
            timestamps=t,
            frame_index=0,
        )

        acquisition.processing_window = "boxcar"
        boxcar = FrameProcessor(acquisition).process(frame)

        acquisition.processing_window = "hanning"
        hanning = FrameProcessor(acquisition).process(frame)

        self.assertFalse(
            np.allclose(
                boxcar.spectra["fft"]["ai0"],
                hanning.spectra["fft"]["ai0"],
            )
        )

    def test_ni_backend_frame_timestamps_are_relative_to_each_frame(self):
        sample_rate = 1000.0
        frame_size = 4
        timestamps = NIDaqBackend._frame_timestamps(frame_size, sample_rate)
        np.testing.assert_allclose(timestamps, np.array([0.0, 0.001, 0.002, 0.003]))

    def test_ni_trigger_source_uses_task_virtual_channel_name(self):
        self.assertEqual(NIDaqBackend._normalize_trigger_source("ai0", "Dev1"), "ai0")
        self.assertEqual(
            NIDaqBackend._normalize_trigger_source("/Dev1/PFI0", "Dev1"),
            "/Dev1/PFI0",
        )

    def test_ni_backend_configures_iepe_as_voltage_channel_with_current_excitation(self):
        class _Enum:
            def __init__(self, **values):
                for key, value in values.items():
                    setattr(self, key, value)

        class _Constants:
            TerminalConfiguration = _Enum(DEFAULT="default")
            Coupling = _Enum(AC="ac", DC="dc")
            ExcitationSource = _Enum(INTERNAL="internal", NONE="none")
            ExcitationVoltageOrCurrent = _Enum(USE_CURRENT="use_current")
            AcquisitionType = _Enum(CONTINUOUS="continuous")

        class _AIChannel:
            def __init__(self):
                self.assignments = {}

            def __setattr__(self, name, value):
                if name == "assignments":
                    object.__setattr__(self, name, value)
                else:
                    self.assignments[name] = value

        class _AIChannels:
            def __init__(self):
                self.voltage_calls = []
                self.accel_calls = []
                self.created = []

            def add_ai_voltage_chan(self, *args, **kwargs):
                channel = _AIChannel()
                self.voltage_calls.append((args, kwargs))
                self.created.append(channel)
                return channel

            def add_ai_accel_chan(self, *args, **kwargs):
                self.accel_calls.append((args, kwargs))
                raise AssertionError("IEPE channels should not be configured as acceleration channels")

        class _Timing:
            def cfg_samp_clk_timing(self, **_kwargs):
                return None

        class _Triggers:
            start_trigger = object()
            reference_trigger = object()

        class _Task:
            created_tasks = []

            def __init__(self, *args, **kwargs):
                self.ai_channels = _AIChannels()
                self.timing = _Timing()
                self.triggers = _Triggers()
                self.in_stream = object()
                _Task.created_tasks.append(self)

            def close(self):
                return None

        class _AIPhysicalChannel:
            def __init__(self, name):
                self.name = name

        class _Device:
            name = "Dev1"
            product_type = "NI USB-4431"
            ai_physical_chans = [_AIPhysicalChannel("Dev1/ai0")]
            ao_physical_chans = []

        class _System:
            devices = [_Device()]

            @staticmethod
            def local():
                return _System()

        class _Nidaqmx:
            Task = _Task
            system = type("system", (), {"System": _System})

        class _Readers:
            class AnalogMultiChannelReader:
                def __init__(self, _stream):
                    return None

        session = SessionConfig(
            ai_channels=[
                ChannelConfig(
                    name="ai0",
                    physical_name="ai0",
                    enabled=True,
                    coupling="bias",
                    iepe_enabled=True,
                    iepe_current_ma=2.1,
                    sensitivity=20.0,
                    full_scale=5.0,
                )
            ]
        )
        backend = NIDaqBackend()
        backend._nidaqmx = _Nidaqmx()
        backend._constants = _Constants()
        backend._stream_readers = _Readers()

        backend.configure(session, device_name="Dev1")

        ai_channels = _Task.created_tasks[-1].ai_channels
        self.assertEqual(len(ai_channels.voltage_calls), 1)
        self.assertEqual(ai_channels.accel_calls, [])
        _args, kwargs = ai_channels.voltage_calls[0]
        self.assertEqual(kwargs["min_val"], -5.0)
        self.assertEqual(kwargs["max_val"], 5.0)
        assigned = ai_channels.created[0].assignments
        self.assertEqual(assigned["ai_coupling"], "ac")
        self.assertEqual(assigned["ai_excit_src"], "internal")
        self.assertEqual(assigned["ai_excit_voltage_or_current"], "use_current")
        self.assertAlmostEqual(assigned["ai_excit_val"], 0.0021)
        self.assertFalse(assigned["ai_excit_use_for_scaling"])

    def test_ni_stop_aborts_tasks_before_stop(self):
        class _Task:
            def __init__(self):
                self.calls = []

            def control(self, mode):
                self.calls.append(("control", mode))

            def stop(self):
                self.calls.append(("stop", None))

        class _TaskMode:
            TASK_ABORT = "abort"

        class _Constants:
            TaskMode = _TaskMode

        backend = NIDaqBackend()
        backend._constants = _Constants()
        backend._ai_task = _Task()
        backend._ao_task = _Task()

        backend.stop()

        self.assertEqual(backend._ai_task.calls, [("control", "abort"), ("stop", None)])
        self.assertEqual(backend._ao_task.calls, [("control", "abort"), ("stop", None)])

    def test_ni_abort_does_not_wait_for_stop(self):
        class _Task:
            def __init__(self):
                self.calls = []

            def control(self, mode):
                self.calls.append(("control", mode))

            def stop(self):
                self.calls.append(("stop", None))

        class _TaskMode:
            TASK_ABORT = "abort"

        class _Constants:
            TaskMode = _TaskMode

        backend = NIDaqBackend()
        backend._constants = _Constants()
        backend._ai_task = _Task()
        backend._ao_task = _Task()

        backend.abort()

        self.assertEqual(backend._ai_task.calls, [("control", "abort")])
        self.assertEqual(backend._ao_task.calls, [("control", "abort")])


if __name__ == "__main__":
    unittest.main()
