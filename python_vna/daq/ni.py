from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from python_vna.daq.base import (
    BackendCapability,
    BackendDevice,
    BackendFrame,
    BaseDaqBackend,
    DaqBackendError,
    preferred_usb4431_device,
)
from python_vna.diagnostics import append_log
from python_vna.models import SessionConfig
from python_vna.optional import require


class NIDaqBackend(BaseDaqBackend):
    """NI-DAQmx backend for USB-4431-class devices."""

    def __init__(self) -> None:
        self._nidaqmx = None
        self._constants = None
        self._stream_readers = None
        self._device_name: str | None = None
        self._session: SessionConfig | None = None
        self._ai_task = None
        self._ao_task = None
        self._reader = None
        self._frame_index = 0
        self._channel_names: list[str] = []
        self._finite_ai_sampling = False
        self._stop_requested = False
        self._external_stop_event: threading.Event | None = None

    IEPE_BIAS_CURRENT_MA = 2.1

    @staticmethod
    def _safe_getattr(obj: Any, attr_name: str, default: Any = None) -> Any:
        try:
            return getattr(obj, attr_name)
        except Exception:
            return default

    def _load(self) -> None:
        if self._nidaqmx is not None:
            return
        self._nidaqmx = require("nidaqmx", "python -m pip install -e .[ni]")
        self._constants = require(
            "nidaqmx.constants", "python -m pip install -e .[ni]"
        )
        self._stream_readers = require(
            "nidaqmx.stream_readers", "python -m pip install -e .[ni]"
        )

    def _terminal_configuration(self):
        return self._terminal_configuration_candidates()[0]

    def _terminal_configuration_candidates(
        self, physical_name: str | None = None
    ) -> list[Any]:
        terminal_config = self._safe_getattr(
            self._constants, "TerminalConfiguration", None
        )
        if terminal_config is None:
            return [None]
        preferred = [
            getattr(terminal_config, attr_name)
            for attr_name in (
                "PSEUDODIFFERENTIAL",
                "PSEUDO_DIFF",
                "DIFF",
                "RSE",
                "NRSE",
                "DEFAULT",
            )
            if hasattr(terminal_config, attr_name)
        ]
        supported = self._ai_terminal_configurations_for_channel(physical_name)
        if supported:
            candidates = [
                candidate
                for candidate in preferred
                if any(candidate == supported_config for supported_config in supported)
            ]
            candidates.extend(
                supported_config
                for supported_config in supported
                if not any(supported_config == candidate for candidate in candidates)
            )
            return candidates or supported
        return preferred or [None]

    def _ai_terminal_configurations_for_channel(
        self, physical_name: str | None
    ) -> list[Any]:
        if not physical_name:
            return []
        try:
            devices = self._nidaqmx.system.System.local().devices
        except Exception:
            return []
        normalized = physical_name.strip().lstrip("/")
        for device in devices:
            for ai_channel in self._safe_getattr(device, "ai_physical_chans", []):
                channel_name = str(self._safe_getattr(ai_channel, "name", "")).strip().lstrip("/")
                if channel_name != normalized:
                    continue
                try:
                    return list(ai_channel.ai_term_cfgs)
                except Exception:
                    return []
        return []

    def _add_ai_voltage_channel(
        self,
        physical_name: str,
        channel,
        min_val: float,
        max_val: float,
    ):
        candidates = self._terminal_configuration_candidates(physical_name)
        last_exc: Exception | None = None
        for terminal_config in candidates:
            kwargs = {
                "name_to_assign_to_channel": channel.name,
                "min_val": min_val,
                "max_val": max_val,
            }
            if terminal_config is not None:
                kwargs["terminal_config"] = terminal_config
            try:
                ai_channel = self._ai_task.ai_channels.add_ai_voltage_chan(
                    physical_name,
                    **kwargs,
                )
                append_log(
                    f"ni.configure: {channel.name} terminal_config={terminal_config}"
                )
                return ai_channel
            except Exception as exc:  # pragma: no cover - requires NI runtime/hardware
                last_exc = exc
                append_log(
                    f"ni.configure: {channel.name} terminal_config={terminal_config} failed: {exc}"
                )
        raise DaqBackendError(
            f"Failed to configure AI channel {channel.name} ({physical_name}): {last_exc}"
        ) from last_exc

    def _excitation_source_internal(self):
        excitation_source = self._constants.ExcitationSource
        for attr_name in ("INTERNAL",):
            if hasattr(excitation_source, attr_name):
                return getattr(excitation_source, attr_name)
        return None

    @staticmethod
    def _normalize_iepe_current_amps(current_ma: float) -> float:
        supported_ma = (0.0, 2.1)
        target = min(supported_ma, key=lambda value: abs(value - float(current_ma)))
        return target / 1000.0

    @staticmethod
    def _uses_iepe_bias(channel) -> bool:
        return bool(channel.iepe_enabled or channel.coupling.strip().lower() == "bias")

    @staticmethod
    def _frame_timestamps(frame_size: int, sample_rate: float) -> np.ndarray:
        return np.arange(frame_size, dtype=float) / sample_rate

    @staticmethod
    def _trigger_delay_samples(session: SessionConfig) -> int:
        trigger = session.acquisition.trigger
        if not trigger.enabled or trigger.source == "immediate":
            return 0
        frame_size = int(session.acquisition.frame_size)
        delay_percent = int(trigger.pretrigger_samples)
        return int(round(frame_size * delay_percent / 100.0))

    @staticmethod
    def _continuous_input_buffer_samples(session: SessionConfig) -> int:
        frame_size = max(1, int(session.acquisition.frame_size))
        sample_rate = max(1.0, float(session.acquisition.sample_rate))
        configured = frame_size * max(1, int(session.acquisition.buffer_frames))
        ten_seconds = int(round(sample_rate * 10.0))
        return max(configured, ten_seconds, frame_size * 16)

    @staticmethod
    def _normalize_physical_name(device_name: str, channel_name: str) -> str:
        normalized = (channel_name or "").strip()
        if not normalized:
            raise DaqBackendError("Channel physical name is empty.")
        if "/" not in normalized:
            normalized = f"{device_name}/{normalized}"
        return normalized

    @staticmethod
    def _channel_voltage_limits(channel) -> tuple[float, float]:
        full_scale = abs(float(channel.full_scale or channel.max_value or 10.0))
        if full_scale <= 0.0:
            min_value = float(channel.min_value)
            max_value = float(channel.max_value)
            if max_value > min_value:
                return min_value, max_value
            full_scale = 10.0
        return -full_scale, full_scale

    def _coupling_constant(self, channel):
        coupling = channel.coupling.strip().lower()
        coupling_constants = self._safe_getattr(self._constants, "Coupling", None)
        if coupling_constants is None:
            return None
        if coupling in {"ac", "bias"} and hasattr(coupling_constants, "AC"):
            return coupling_constants.AC
        if coupling == "dc" and hasattr(coupling_constants, "DC"):
            return coupling_constants.DC
        return None

    def _configure_iepe_voltage_channel(self, ai_channel, channel) -> None:
        excitation_source = self._excitation_source_internal()
        excitation_type = self._safe_getattr(
            self._safe_getattr(self._constants, "ExcitationVoltageOrCurrent", None),
            "USE_CURRENT",
            None,
        )
        if excitation_source is not None:
            try:
                ai_channel.ai_excit_src = excitation_source
            except Exception as exc:  # pragma: no cover - hardware/runtime dependent
                raise DaqBackendError(
                    f"Failed to enable IEPE excitation for {channel.name}: {exc}"
                ) from exc
        if excitation_type is not None:
            try:
                ai_channel.ai_excit_voltage_or_current = excitation_type
            except Exception as exc:  # pragma: no cover - hardware/runtime dependent
                raise DaqBackendError(
                    f"Failed to select IEPE current excitation for {channel.name}: {exc}"
                ) from exc
        try:
            ai_channel.ai_excit_val = self._normalize_iepe_current_amps(
                channel.iepe_current_ma or self.IEPE_BIAS_CURRENT_MA
            )
        except Exception as exc:  # pragma: no cover - hardware/runtime dependent
            raise DaqBackendError(
                f"Failed to set IEPE current for {channel.name}: {exc}"
            ) from exc
        try:
            ai_channel.ai_excit_use_for_scaling = False
        except Exception:
            pass

    def _apply_voltage_channel_properties(self, ai_channel, channel) -> None:
        coupling = self._coupling_constant(channel)
        if coupling is not None:
            try:
                ai_channel.ai_coupling = coupling
            except Exception:
                pass
        if self._uses_iepe_bias(channel):
            self._configure_iepe_voltage_channel(ai_channel, channel)

    @staticmethod
    def _measurement_type_names(ai_channel) -> set[str]:
        try:
            measurement_types = ai_channel.ai_meas_types
        except Exception:
            return set()
        names: set[str] = set()
        for measurement_type in measurement_types:
            names.add(str(getattr(measurement_type, "name", measurement_type)).upper())
        return names

    @classmethod
    def _device_supports_iepe(cls, device) -> bool:
        product_type = str(getattr(device, "product_type", "")).lower()
        if "4431" in product_type:
            return True
        iepe_tokens = {
            "ACCELERATION_ACCELEROMETER_CURRENT_INPUT",
            "SOUND_PRESSURE_MICROPHONE",
            "FORCE_IEPE_SENSOR",
            "VELOCITY_IEPE_SENSOR",
        }
        for ai_channel in getattr(device, "ai_physical_chans", []):
            if cls._measurement_type_names(ai_channel).intersection(iepe_tokens):
                return True
        return False

    @classmethod
    def _device_supports_dsa_triggering(cls, device) -> bool:
        product_type = str(getattr(device, "product_type", "")).lower()
        return "4431" in product_type or cls._device_supports_iepe(device)

    def list_devices(self) -> list[BackendDevice]:
        self._load()
        system = self._nidaqmx.system.System.local()
        devices: list[BackendDevice] = []
        for device in system.devices:
            ai_channels = [chan.name for chan in device.ai_physical_chans]
            ao_channels = [chan.name for chan in device.ao_physical_chans]
            ai_max_rate = self._safe_getattr(device, "ai_max_single_chan_rate", None)
            ao_max_rate = self._safe_getattr(device, "ao_max_rate", None)
            supports_iepe = self._device_supports_iepe(device)
            supports_dsa_triggering = self._device_supports_dsa_triggering(device)
            devices.append(
                BackendDevice(
                    name=device.name,
                    product_type=getattr(device, "product_type", device.name),
                    ai_channels=ai_channels,
                    ao_channels=ao_channels,
                    capability=BackendCapability(
                        supports_iepe=supports_iepe,
                        supports_output=len(ao_channels) > 0,
                        supports_analog_trigger=supports_dsa_triggering,
                        supports_pretrigger=supports_dsa_triggering,
                        max_ai_sample_rate=ai_max_rate,
                        max_ao_sample_rate=ao_max_rate,
                    ),
                )
            )
        return devices

    def configure(self, session: SessionConfig, device_name: str | None = None) -> None:
        self._load()
        append_log("ni.configure: closing previous task")
        self.close()

        enabled = [channel for channel in session.ai_channels if channel.enabled]
        if not enabled:
            raise DaqBackendError("At least one enabled input channel is required.")

        devices = self.list_devices()
        if not devices:
            raise DaqBackendError("No NI-DAQmx devices found.")

        selected = device_name or preferred_usb4431_device(devices) or devices[0].name
        selected_device = next((device for device in devices if device.name == selected), None)
        if selected_device is None:
            raise DaqBackendError(f"NI device '{selected}' was not found.")
        if (
            any(self._uses_iepe_bias(channel) for channel in enabled)
            and not selected_device.capability.supports_iepe
        ):
            raise DaqBackendError(
                f"Device '{selected}' ({selected_device.product_type}) does not support IEPE/current excitation. "
                "Select the USB-4431 device or disable Bias/IEPE channels."
            )
        self._device_name = selected
        append_log(f"ni.configure: selected={selected}")
        self._session = session
        self._channel_names = [channel.name for channel in enabled]
        self._frame_index = 0
        self._finite_ai_sampling = False
        self._stop_requested = False

        constants = self._constants
        self._ai_task = self._nidaqmx.Task(new_task_name=f"{selected}_ai")
        for channel in enabled:
            physical_name = self._normalize_physical_name(
                selected, channel.physical_name or channel.name
            )
            min_val, max_val = self._channel_voltage_limits(channel)
            ai_channel = self._add_ai_voltage_channel(
                physical_name,
                min_val=min_val,
                max_val=max_val,
                channel=channel,
            )
            self._apply_voltage_channel_properties(ai_channel, channel)

        sample_rate = session.acquisition.sample_rate
        frame_size = session.acquisition.frame_size
        trigger = session.acquisition.trigger
        trigger_mode = trigger.mode.strip().lower()
        finite_ai_sampling = trigger.enabled and (
            trigger_mode == "every frame" or trigger_mode == "manual arm"
        )
        self._finite_ai_sampling = finite_ai_sampling
        self._ai_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=(
                constants.AcquisitionType.FINITE
                if finite_ai_sampling
                else constants.AcquisitionType.CONTINUOUS
            ),
            samps_per_chan=(
                frame_size
                if finite_ai_sampling
                else self._continuous_input_buffer_samples(session)
            ),
        )
        if not finite_ai_sampling:
            try:
                self._ai_task.in_stream.input_buf_size = self._continuous_input_buffer_samples(session)
            except Exception:
                pass

        if trigger.enabled and trigger.source != "immediate":
            source = self._normalize_trigger_source(trigger.source, selected)
            trigger_level = abs(float(trigger.level))
            delay_setting = int(trigger.pretrigger_samples)
            pretrigger_samples = (
                int(round(abs(delay_setting) * frame_size / 100.0))
                if delay_setting < 0
                else 0
            )
            if pretrigger_samples > 0 and finite_ai_sampling:
                self._ai_task.triggers.reference_trigger.cfg_anlg_window_ref_trig(
                    trigger_source=source,
                    pretrigger_samples=pretrigger_samples,
                    window_top=trigger_level,
                    window_bottom=-trigger_level,
                    trigger_when=constants.WindowTriggerCondition1.LEAVING_WINDOW,
                )
            else:
                self._ai_task.triggers.start_trigger.cfg_anlg_window_start_trig(
                    trigger_source=source,
                    window_top=trigger_level,
                    window_bottom=-trigger_level,
                    trigger_when=constants.WindowTriggerCondition1.LEAVING_WINDOW,
                )

        if session.acquisition.excitation.enabled and session.ao_channel:
            if not selected_device.capability.supports_output:
                raise DaqBackendError(
                    f"Device '{selected}' does not expose any AO channels. Disable excitation or choose a device with output."
                )
            self._ao_task = self._nidaqmx.Task(new_task_name=f"{selected}_ao")
            ao_name = self._normalize_physical_name(selected, session.ao_channel)
            self._ao_task.ao_channels.add_ao_voltage_chan(
                ao_name,
                min_val=-10.0,
                max_val=10.0,
            )
            ao_rate_limit = selected_device.capability.max_ao_sample_rate or 96000.0
            self._ao_task.timing.cfg_samp_clk_timing(
                rate=min(sample_rate, ao_rate_limit),
                sample_mode=constants.AcquisitionType.CONTINUOUS,
                samps_per_chan=frame_size,
            )

        self._reader = self._stream_readers.AnalogMultiChannelReader(
            self._ai_task.in_stream
        )

    @staticmethod
    def _normalize_trigger_source(source: str, _device_name: str) -> str:
        normalized = (source or "").strip()
        if normalized.lower() == "immediate":
            return "immediate"
        # DAQmx analog edge triggers accept task virtual channel names. We assign
        # channels as ai0/ai1/... above, so keep those names instead of expanding
        # to /Dev1/ai0, which USB-4431 rejects for this task.
        if normalized.startswith("ai"):
            return normalized
        return normalized

    def start(self) -> None:
        if self._ai_task is None or self._session is None:
            raise DaqBackendError("Backend must be configured before start().")

        append_log("ni.start")
        self._stop_requested = False
        if self._ao_task is not None:
            waveform = self._build_excitation_waveform(self._session)
            self._ao_task.write(waveform, auto_start=False)
            self._ao_task.start()
        if not self._finite_ai_sampling:
            self._ai_task.start()

    def _build_excitation_waveform(self, session: SessionConfig) -> np.ndarray:
        excitation = session.acquisition.excitation
        sample_rate = min(session.acquisition.sample_rate, 96000.0)
        frame_size = session.acquisition.frame_size
        time_vector = np.arange(frame_size, dtype=float) / sample_rate
        if excitation.mode == "tone":
            waveform = excitation.amplitude * np.sin(
                2.0 * np.pi * excitation.tone_hz * time_vector
            )
        elif excitation.mode == "chirp":
            scipy_signal = require("scipy.signal", "python -m pip install -e .")
            waveform = excitation.amplitude * scipy_signal.chirp(
                time_vector,
                f0=excitation.chirp_start_hz,
                f1=excitation.chirp_stop_hz,
                t1=max(time_vector[-1], 1.0 / sample_rate),
                method="logarithmic",
            )
        elif excitation.mode == "random":
            rng = np.random.default_rng(excitation.random_seed)
            waveform = excitation.amplitude * rng.standard_normal(frame_size)
        else:
            waveform = np.zeros(frame_size, dtype=float)
        return np.clip(waveform + excitation.offset, -10.0, 10.0)

    def read_frame(self) -> BackendFrame:
        if self._ai_task is None or self._reader is None or self._session is None:
            raise DaqBackendError("Backend is not running.")

        append_log(f"ni.read_frame: begin frame={self._frame_index}")
        frame_size = self._session.acquisition.frame_size
        num_channels = len(self._channel_names)
        data = np.zeros((num_channels, frame_size), dtype=np.float64)
        read_timeout = self._session.acquisition.trigger.timeout_seconds
        try:
            if self._finite_ai_sampling:
                self._ai_task.start()
                sample_rate = max(float(self._session.acquisition.sample_rate), 1.0)
                frame_duration = frame_size / sample_rate
                read_timeout = max(read_timeout, frame_duration + 0.25)
                self._wait_until_done_with_stop_poll(read_timeout)
            if self._finite_ai_sampling:
                self._reader.read_many_sample(
                    data,
                    number_of_samples_per_channel=frame_size,
                    timeout=read_timeout,
                )
            else:
                samples_read = 0
                sample_rate = float(self._session.acquisition.sample_rate)
                chunk_size = max(1, min(frame_size, int(round(sample_rate * 0.1))))
                while samples_read < frame_size:
                    if self._should_stop_read():
                        raise DaqBackendError("NI-DAQmx read stopped by user.")
                    remaining = frame_size - samples_read
                    requested = min(chunk_size, remaining)
                    chunk = np.zeros((num_channels, requested), dtype=np.float64)
                    self._reader.read_many_sample(
                        chunk,
                        number_of_samples_per_channel=requested,
                        timeout=min(max(requested / max(sample_rate, 1.0) + 0.25, 0.25), read_timeout),
                    )
                    data[:, samples_read : samples_read + requested] = chunk
                    samples_read += requested
            read_end_unix_ns = time.time_ns()
        except Exception as exc:  # pragma: no cover - requires NI runtime/hardware
            raise DaqBackendError(f"NI-DAQmx read failed: {exc}") from exc
        finally:
            if self._finite_ai_sampling:
                try:
                    self._ai_task.stop()
                except Exception:
                    pass

        sample_rate = self._session.acquisition.sample_rate
        frame_start_unix_ns = read_end_unix_ns - int(
            round(max(frame_size - 1, 0) * 1_000_000_000 / sample_rate)
        )
        trigger_delay_samples = self._trigger_delay_samples(self._session)
        timestamps = self._frame_timestamps(frame_size, sample_rate)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=self._channel_names,
            data=data,
            timestamps=timestamps,
            frame_index=self._frame_index,
            metadata={
                "backend": "ni",
                "device": self._device_name,
                "trigger_delay_percent": int(self._session.acquisition.trigger.pretrigger_samples)
                if self._session.acquisition.trigger.enabled
                else 0,
                "trigger_delay_samples": trigger_delay_samples,
                "frame_start_unix_ns": frame_start_unix_ns,
                "read_end_unix_ns": read_end_unix_ns,
                "channel_full_scales": {
                    channel.name: abs(float(channel.full_scale or channel.max_value or 0.0))
                    for channel in self._session.ai_channels
                    if channel.enabled
                },
            },
        )
        self._frame_index += 1
        append_log(f"ni.read_frame: end frame={frame.frame_index}")
        return frame

    def stop(self) -> None:
        append_log("ni.stop: begin")
        self._stop_requested = True
        for task in (self._ao_task, self._ai_task):
            if task is not None:
                try:
                    task.stop()
                except Exception:
                    pass
        append_log("ni.stop: end")

    def abort(self) -> None:
        append_log("ni.abort: begin")
        self._stop_requested = True
        task_mode = self._safe_getattr(self._constants, "TaskMode", None)
        abort_mode = self._safe_getattr(task_mode, "TASK_ABORT", None)
        if self._ai_task is not None:
            if abort_mode is not None:
                try:
                    self._ai_task.control(abort_mode)
                except Exception:
                    pass
        if self._ao_task is not None:
            if abort_mode is not None:
                try:
                    self._ao_task.control(abort_mode)
                except Exception:
                    pass
        append_log("ni.abort: end")

    def close(self) -> None:
        append_log("ni.close: begin")
        for task_name in ("_ai_task", "_ao_task"):
            task = getattr(self, task_name, None)
            if task is not None:
                try:
                    task.close()
                except Exception:
                    pass
            setattr(self, task_name, None)
        self._reader = None
        self._session = None
        self._finite_ai_sampling = False
        self._stop_requested = False
        self._external_stop_event = None
        append_log("ni.close: end")

    def request_stop(self) -> None:
        append_log("ni.request_stop")
        self._stop_requested = True

    def set_stop_event(self, stop_event: threading.Event | None) -> None:
        self._external_stop_event = stop_event

    def _should_stop_read(self) -> bool:
        return bool(
            self._stop_requested
            or (
                self._external_stop_event is not None
                and self._external_stop_event.is_set()
            )
        )

    def _wait_until_done_with_stop_poll(self, timeout: float) -> None:
        wait_until_done = getattr(self._ai_task, "wait_until_done", None)
        if wait_until_done is None:
            return
        poll_timeout = min(max(float(timeout), 0.01), 0.1)
        while True:
            if self._should_stop_read():
                raise DaqBackendError("NI-DAQmx read stopped by user.")
            try:
                wait_until_done(timeout=poll_timeout)
                return
            except Exception as exc:  # pragma: no cover - requires NI runtime/hardware
                if self._should_stop_read():
                    raise DaqBackendError("NI-DAQmx read stopped by user.") from exc
                if "timeout" in str(exc).lower():
                    continue
                raise DaqBackendError(
                    f"NI-DAQmx triggered acquisition failed: {exc}"
                ) from exc
