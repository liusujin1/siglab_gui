from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from python_vna.daq.base import (
    BackendCapability,
    BackendDevice,
    BackendFrame,
    BaseDaqBackend,
    DaqBackendError,
)
from python_vna.models import SessionConfig


@dataclass(slots=True)
class _SimulationState:
    session: SessionConfig
    frame_index: int = 0
    time_offset: float = 0.0


class SimulatedDaqBackend(BaseDaqBackend):
    """Development backend that synthesizes vibration-like data."""

    def __init__(self) -> None:
        self._state: _SimulationState | None = None
        self._running = False

    def list_devices(self) -> list[BackendDevice]:
        return [
            BackendDevice(
                name="SimulatedUSB4431",
                product_type="Simulated NI USB-4431",
                ai_channels=[f"ai{i}" for i in range(4)],
                ao_channels=["ao0"],
                capability=BackendCapability(
                    supports_iepe=True,
                    supports_output=True,
                    supports_analog_trigger=True,
                    supports_pretrigger=True,
                    max_ai_sample_rate=102400.0,
                    max_ao_sample_rate=96000.0,
                ),
            )
        ]

    def configure(self, session: SessionConfig, device_name: str | None = None) -> None:
        enabled_channels = [ch for ch in session.ai_channels if ch.enabled]
        if not enabled_channels:
            raise DaqBackendError("At least one enabled input channel is required.")
        self._state = _SimulationState(session=session)
        self._running = False

    def start(self) -> None:
        if self._state is None:
            raise DaqBackendError("Backend must be configured before start().")
        self._running = True

    def read_frame(self) -> BackendFrame:
        if not self._running or self._state is None:
            raise DaqBackendError("Backend is not running.")

        session = self._state.session
        enabled = [ch for ch in session.ai_channels if ch.enabled]
        sample_rate = session.acquisition.sample_rate
        frame_size = session.acquisition.frame_size
        time_vector = (
            np.arange(frame_size, dtype=float) / sample_rate + self._state.time_offset
        )
        reference = signal.chirp(
            time_vector,
            f0=session.acquisition.excitation.chirp_start_hz,
            f1=max(
                session.acquisition.excitation.chirp_stop_hz,
                session.acquisition.excitation.chirp_start_hz + 1.0,
            ),
            t1=max(time_vector[-1], 1.0 / sample_rate),
            method="logarithmic",
        )

        frame = np.zeros((len(enabled), frame_size), dtype=float)
        rng = np.random.default_rng(
            session.acquisition.excitation.random_seed + self._state.frame_index
        )
        for idx, channel in enumerate(enabled):
            noise = 0.02 * rng.standard_normal(frame_size)
            resonance = np.sin(2.0 * np.pi * (40.0 + idx * 30.0) * time_vector)
            delayed_ref = np.roll(reference, idx * 8) * (1.0 - idx * 0.1)
            signal_data = 0.4 * resonance + 0.6 * delayed_ref + noise
            if channel.is_reference:
                signal_data = reference + noise
            frame[idx, :] = signal_data * channel.sensitivity

        backend_frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=[channel.name for channel in enabled],
            data=frame,
            timestamps=time_vector,
            frame_index=self._state.frame_index,
            metadata={"backend": "simulated"},
        )
        self._state.frame_index += 1
        self._state.time_offset = float(time_vector[-1] + 1.0 / sample_rate)
        return backend_frame

    def stop(self) -> None:
        self._running = False

    def abort(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self._state = None
