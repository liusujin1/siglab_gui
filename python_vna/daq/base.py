from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import threading
from typing import Any

import numpy as np

from python_vna.models import SessionConfig


class DaqBackendError(RuntimeError):
    """Backend-specific runtime failure."""


@dataclass(slots=True)
class BackendCapability:
    supports_iepe: bool = False
    supports_output: bool = False
    supports_analog_trigger: bool = False
    supports_pretrigger: bool = False
    max_ai_sample_rate: float | None = None
    max_ao_sample_rate: float | None = None


@dataclass(slots=True)
class BackendDevice:
    name: str
    product_type: str
    ai_channels: list[str] = field(default_factory=list)
    ao_channels: list[str] = field(default_factory=list)
    capability: BackendCapability = field(default_factory=BackendCapability)


@dataclass(slots=True)
class BackendFrame:
    sample_rate: float
    channel_names: list[str]
    data: np.ndarray
    timestamps: np.ndarray
    frame_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseDaqBackend(ABC):
    """Abstract DAQ backend."""

    @abstractmethod
    def list_devices(self) -> list[BackendDevice]:
        raise NotImplementedError

    @abstractmethod
    def configure(self, session: SessionConfig, device_name: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_frame(self) -> BackendFrame:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def request_stop(self) -> None:
        """Ask an in-flight read to return soon without touching driver state."""

    def set_stop_event(self, stop_event: threading.Event | None) -> None:
        """Share a worker-owned stop event with backends that poll during reads."""

    def abort(self) -> None:
        self.stop()

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


def preferred_usb4431_device(devices: list[BackendDevice]) -> str | None:
    if not devices:
        return None
    for device in devices:
        product = (device.product_type or "").lower()
        name = (device.name or "").lower()
        if "usb-4431" in product or "4431" in product or "usb-4431" in name or "4431" in name:
            return device.name
    for device in devices:
        capability = device.capability
        if (
            capability.supports_iepe
            and capability.supports_output
            and len(device.ai_channels) >= 4
            and len(device.ao_channels) >= 1
        ):
            return device.name
    return devices[0].name
