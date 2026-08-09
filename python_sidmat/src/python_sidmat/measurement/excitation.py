"""Excitation (noise) parameters — port of ``SAMBA19xLib.ExcitationParameters``.

The excitation signal is what SiDiMaT injects to drive a measurement.
``params`` meaning depends on ``type`` (from the RCI doc 4.3.5):

* White noise  — [gain, -, -, -]
* Sine wave    — [gain, frequency(Hz), -, -]
* Duty cycle   — [gain, high period(ms), low period(ms), -]
* Chirp sine   — [gain, start freq, end freq, chirp period(ms)]
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from python_samba.protocol.commands import FilterStage

from python_sidmat.backend.iosignal import IOType

__all__ = ["ExcitationParameters", "EXCITATION_TYPE_NAMES"]

# Order matches SAMBA19xLabels.ExcitTypeName.
EXCITATION_TYPE_NAMES: tuple[str, ...] = (
    "NoNoise", "WhiteNoise", "SineWave", "External_NotUsed",
    "DutyCycle", "ChirpSine", "Triangular", "Sawtooth", "Step",
)


@dataclass(slots=True)
class ExcitationParameters:
    """Excitation (noise) signal configuration."""

    type: int = 0
    params: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    noise_injection_io: IOType = field(default_factory=lambda: IOType(3, 0, 0))
    noise_filter_usage: bool = False
    noise_filters: list[FilterStage] = field(
        default_factory=lambda: [
            FilterStage(0, i, 0, (1.0, 0.0, 0.0, 0.0, 0.0)) for i in range(4)
        ]
    )
    diag_io0: IOType = field(default_factory=IOType)
    diag_io1: IOType = field(default_factory=IOType)
    offset: float = 0.0

    def __post_init__(self) -> None:
        self.type = int(self.type)
        self.params = [float(value) for value in self.params[:4]]
        self.params.extend([0.0] * (4 - len(self.params)))
        self.offset = float(self.offset)
        self.validate()

    def validate(self) -> None:
        if not 0 <= int(self.type) < len(EXCITATION_TYPE_NAMES):
            raise ValueError(f"unsupported excitation type {self.type}")
        if len(self.params) != 4 or not all(
            math.isfinite(float(value)) for value in self.params
        ):
            raise ValueError("excitation parameters must contain four finite numbers")
        if not math.isfinite(float(self.offset)):
            raise ValueError("excitation offset must be finite")

    @property
    def type_name(self) -> str:
        if 0 <= self.type < len(EXCITATION_TYPE_NAMES):
            return EXCITATION_TYPE_NAMES[self.type]
        return f"Type{self.type}"

    def encode(self) -> tuple[object, ...]:
        """5 parameters for DSESP: NoiseType + Params[0..3]."""
        self.validate()
        return (int(self.type), *[float(p) for p in self.params])

    @classmethod
    def from_tokens(cls, tokens: list[str] | tuple[str, ...]) -> "ExcitationParameters":
        if len(tokens) < 5:
            raise ValueError(f"need 5 DGESP tokens, got {len(tokens)}")
        try:
            exc_type = int(str(tokens[0]), 0)
        except ValueError:
            exc_type = int(str(tokens[0]))
        params = [float(token) for token in tokens[1:5]]
        return cls(type=exc_type, params=params)
