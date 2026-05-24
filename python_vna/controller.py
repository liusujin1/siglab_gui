from __future__ import annotations

from dataclasses import dataclass

from python_vna.daq import BaseDaqBackend, BackendDevice, preferred_usb4431_device
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

    def list_devices(self) -> list[BackendDevice]:
        return self.backend.list_devices()

    def configure(self, device_name: str | None = None) -> None:
        self.device_name = device_name
        self.backend.configure(self.state.session, device_name=device_name)

    def set_averaging_enabled(self, enabled: bool) -> None:
        self.processor = FrameProcessor(self.state.session.acquisition, averaging_enabled=enabled)

    def start(self) -> None:
        self.backend.start()

    def read_and_process(self) -> MeasurementSet:
        frame = self.backend.read_frame()
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
        self.backend.stop()

    def abort(self) -> None:
        self.backend.abort()

    def close(self) -> None:
        self.backend.close()

    def snapshot(self) -> SavedSession:
        return SavedSession(config=self.state.session, measurement=self.state.measurement)

    def set_session(self, session: SessionConfig) -> None:
        self.state.session = session
        self.state.measurement = None
        self.processor = FrameProcessor(session.acquisition, averaging_enabled=False)

    @staticmethod
    def preferred_device(devices: list[BackendDevice]) -> str | None:
        return preferred_usb4431_device(devices)
