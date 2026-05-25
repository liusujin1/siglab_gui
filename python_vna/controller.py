from __future__ import annotations

from dataclasses import dataclass
import threading

import numpy as np

from python_vna.daq import BaseDaqBackend, BackendDevice, preferred_usb4431_device
from python_vna.daq.base import BackendFrame
from python_vna.models import MeasurementSet, SavedSession, SessionConfig
from python_vna.signal_pipeline import FrameProcessor


@dataclass(slots=True)
class ControllerState:
    session: SessionConfig
    measurement: MeasurementSet | None = None
    last_error: str | None = None


class VnaController:
    def __init__(self, backend: BaseDaqBackend, session: SessionConfig) -> None:
        self.backend = backend
        self.state = ControllerState(session=session)
        self.processor = FrameProcessor(session.acquisition, averaging_enabled=False)
        self.device_name: str | None = None
        self._overlap_data: np.ndarray | None = None
        self._overlap_channel_names: list[str] | None = None
        self._overlap_sample_start_index = 0
        self._overlap_frame_index = 0
        self._overlap_latest_metadata: dict | None = None
        self._overlap_latest_raw_frame_index: int | None = None
        self._stop_requested = threading.Event()

    def list_devices(self) -> list[BackendDevice]:
        return self.backend.list_devices()

    def configure(self, device_name: str | None = None) -> None:
        self.device_name = device_name
        self.backend.configure(self.state.session, device_name=device_name)
        self._reset_overlap_processing()
        self._stop_requested.clear()
        self._set_backend_stop_event(self._stop_requested)

    def set_averaging_enabled(self, enabled: bool) -> None:
        self.processor = FrameProcessor(self.state.session.acquisition, averaging_enabled=enabled)
        self._reset_overlap_processing()

    def start(self) -> None:
        self._stop_requested.clear()
        self.backend.start()

    def read_and_process(self) -> MeasurementSet:
        frame = self._read_processing_frame()
        measurement = self.processor.process(frame)
        if measurement.metadata.get("rejected", False) and self.state.measurement is not None:
            retained = self.state.measurement
            retained.metadata = {
                **retained.metadata,
                "frame_index": measurement.metadata.get("frame_index", retained.metadata.get("frame_index")),
                "rejected": True,
                "double_hit_rejected": measurement.metadata.get("double_hit_rejected", False),
                "overload_rejected": measurement.metadata.get("overload_rejected", False),
            }
            return retained
        self.state.measurement = measurement
        return measurement

    def stop(self) -> None:
        self._stop_requested.set()
        self._reset_overlap_processing()
        self.backend.stop()

    def request_stop(self) -> None:
        self._stop_requested.set()
        request_stop = getattr(self.backend, "request_stop", None)
        if request_stop is not None:
            request_stop()

    def set_stop_event(self, stop_event: threading.Event | None) -> None:
        self._stop_requested = stop_event or threading.Event()
        self._set_backend_stop_event(self._stop_requested)

    def abort(self) -> None:
        self._stop_requested.set()
        self._reset_overlap_processing()
        self.backend.abort()

    def close(self) -> None:
        self._reset_overlap_processing()
        self.backend.close()

    def _set_backend_stop_event(self, stop_event: threading.Event | None) -> None:
        set_stop_event = getattr(self.backend, "set_stop_event", None)
        if set_stop_event is not None:
            set_stop_event(stop_event)

    def snapshot(self) -> SavedSession:
        return SavedSession(config=self.state.session, measurement=self.state.measurement)

    def set_session(self, session: SessionConfig) -> None:
        self.state.session = session
        self.state.measurement = None
        self.processor = FrameProcessor(session.acquisition, averaging_enabled=False)
        self._reset_overlap_processing()

    def _read_processing_frame(self) -> BackendFrame:
        overlap_percent = int(self.state.session.acquisition.overlap_percent)
        if overlap_percent <= 0:
            self._reset_overlap_processing()
            return self.backend.read_frame()

        frame_size = int(self.state.session.acquisition.frame_size)
        hop_size = self._overlap_hop_size(frame_size, overlap_percent)
        while self._overlap_data is None or self._overlap_data.shape[1] < frame_size:
            if self._stop_requested.is_set():
                raise RuntimeError("Acquisition stopped by user.")
            self._append_overlap_raw_frame(self.backend.read_frame())

        data = self._overlap_data[:, :frame_size].copy()
        channel_names = list(self._overlap_channel_names or [])
        output_start_index = self._overlap_sample_start_index
        self._overlap_data = self._overlap_data[:, hop_size:]
        self._overlap_sample_start_index += hop_size
        if self._overlap_data.size == 0:
            self._overlap_data = None
            self._overlap_channel_names = None

        sample_rate = float(self.state.session.acquisition.sample_rate)
        timestamps = np.arange(frame_size, dtype=float) / sample_rate
        metadata = {
            **(self._overlap_latest_metadata or {}),
            "overlap_percent": overlap_percent,
            "overlap_hop_size": hop_size,
            "overlap_keep_size": frame_size - hop_size,
            "overlap_sample_start_index": output_start_index,
            "raw_frame_index": self._overlap_latest_raw_frame_index,
        }
        output_frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=channel_names,
            data=data,
            timestamps=timestamps,
            frame_index=self._overlap_frame_index,
            metadata=metadata,
        )
        self._overlap_frame_index += 1
        return output_frame

    @staticmethod
    def _overlap_hop_size(frame_size: int, overlap_percent: int) -> int:
        frame_size = max(1, int(frame_size))
        if overlap_percent >= 100:
            return max(1, frame_size // 8)
        hop_size = int(round(frame_size * (100 - overlap_percent) / 100.0))
        return min(frame_size, max(1, hop_size))

    def _append_overlap_raw_frame(self, frame: BackendFrame) -> None:
        data = np.asarray(frame.data, dtype=float)
        if (
            self._overlap_data is None
            or self._overlap_channel_names != list(frame.channel_names)
            or self._overlap_data.shape[0] != data.shape[0]
        ):
            self._overlap_data = data.copy()
            self._overlap_channel_names = list(frame.channel_names)
            self._overlap_sample_start_index = 0
            self._overlap_frame_index = 0
        else:
            self._overlap_data = np.concatenate([self._overlap_data, data], axis=1)
        self._overlap_latest_metadata = dict(frame.metadata)
        self._overlap_latest_raw_frame_index = int(frame.frame_index)

    def _reset_overlap_processing(self) -> None:
        self._overlap_data = None
        self._overlap_channel_names = None
        self._overlap_sample_start_index = 0
        self._overlap_frame_index = 0
        self._overlap_latest_metadata = None
        self._overlap_latest_raw_frame_index = None

    @staticmethod
    def preferred_device(devices: list[BackendDevice]) -> str | None:
        return preferred_usb4431_device(devices)
