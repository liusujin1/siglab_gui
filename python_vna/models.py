from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ChannelConfig:
    name: str
    physical_name: str
    label: str = ""
    enabled: bool = True
    coupling: str = "ac"
    offset: float = 0.0
    iepe_enabled: bool = False
    iepe_current_ma: float = 2.1
    sensitivity: float = 1.0
    engineering_unit: str = "V"
    per_eu_mode: str = "/Volt"
    db_reference: float = 1.0
    full_scale: float = 10.0
    min_value: float = -10.0
    max_value: float = 10.0
    is_reference: bool = False


@dataclass(slots=True)
class TriggerConfig:
    enabled: bool = False
    mode: str = "Off (Free Run)"
    source: str = "immediate"
    level: float = 0.0
    level_percent: float | None = None
    slope: str = "rising"
    pretrigger_samples: int = 0
    timeout_seconds: float = 5.0


@dataclass(slots=True)
class AveragingConfig:
    mode: str = "linear"
    count: int = 20
    exponential_alpha: float = 0.2
    peak_hold: bool = False


@dataclass(slots=True)
class ModalProcessingConfig:
    enabled: bool = False
    force_window_enabled: bool = False
    force_window_fraction: float = 0.2
    exponential_window_enabled: bool = False
    exponential_decay_fraction: float = 0.1
    reject_double_hit: bool = False
    double_hit_threshold: float = 0.5
    double_hit_delay_fraction: float = 0.2
    reject_overload: bool = False


@dataclass(slots=True)
class ExcitationConfig:
    enabled: bool = False
    mode: str = "external"
    amplitude: float = 1.0
    offset: float = 0.0
    random_seed: int = 1234
    chirp_start_hz: float = 10.0
    chirp_stop_hz: float = 2000.0
    tone_hz: float = 100.0


@dataclass(slots=True)
class AcquisitionConfig:
    sample_rate: float = 2560.0
    frame_size: int = 4096
    bandwidth_hz: float = 1000.0
    anti_alias_filters_enabled: bool = True
    processing_window: str = "boxcar"
    overlap_percent: int = 0
    buffer_frames: int = 8
    display_channels: int = 4
    overlay_enabled: bool = False
    reference_channel: str = "ai0"
    response_channels: list[str] = field(default_factory=lambda: ["ai1", "ai2", "ai3"])
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    averaging: AveragingConfig = field(default_factory=AveragingConfig)
    excitation: ExcitationConfig = field(default_factory=ExcitationConfig)
    modal: ModalProcessingConfig = field(default_factory=ModalProcessingConfig)


@dataclass(slots=True)
class SessionConfig:
    title: str = "Untitled Session"
    notes: str = ""
    ai_channels: list[ChannelConfig] = field(default_factory=list)
    ao_channel: str | None = None
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)


@dataclass(slots=True)
class MeasurementSet:
    sample_rate: float
    time_data: dict[str, Any]
    spectra: dict[str, Any]
    frf: dict[str, Any]
    coherence: dict[str, Any]
    cross_spectra: dict[str, Any]
    correlations: dict[str, Any]
    impulse_responses: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SavedSession:
    config: SessionConfig
    measurement: MeasurementSet | None = None
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
