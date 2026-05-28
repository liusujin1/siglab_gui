from .base import (
    BackendCapability,
    BackendDevice,
    BackendFrame,
    BaseDaqBackend,
    DaqBackendError,
    preferred_usb4431_device,
)

__all__ = [
    "BackendCapability",
    "BackendDevice",
    "BackendFrame",
    "BaseDaqBackend",
    "DaqBackendError",
    "preferred_usb4431_device",
    "NIDaqBackend",
    "SimulatedDaqBackend",
]


def __getattr__(name):
    if name == "NIDaqBackend":
        from .ni import NIDaqBackend

        return NIDaqBackend
    if name == "SimulatedDaqBackend":
        from .simulated import SimulatedDaqBackend

        return SimulatedDaqBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
