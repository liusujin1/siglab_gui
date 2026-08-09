"""Data models shared by the logging service, storage, and Qt page."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileLoggingConfig:
    """Host-side monitor acquisition settings.

    ``duration_s`` is ``None`` for continuous acquisition.  The controller
    supports 40 monitor definitions, so ``signal_count`` is deliberately not
    tied to the smaller signal cards shown elsewhere in the main application.
    """

    path: Path
    signal_count: int = 3
    interval_ms: int = 500
    start_after_s: float = 36.0
    duration_s: float | None = 3600.0
    delimiter: str = ","
    signal_names: tuple[str, ...] = ()

    def validate(self) -> None:
        if not 1 <= int(self.signal_count) <= 40:
            raise ValueError("signal_count must be in the range 1..40")
        if int(self.interval_ms) < 10:
            raise ValueError("interval_ms must be at least 10 ms")
        if float(self.start_after_s) < 0:
            raise ValueError("start_after_s cannot be negative")
        if self.duration_s is not None and float(self.duration_s) <= 0:
            raise ValueError("duration_s must be positive or None")
        if self.delimiter not in {",", ";", "\t", " "}:
            raise ValueError("delimiter must be comma, semicolon, tab, or space")
        if not str(self.path):
            raise ValueError("an output path is required")


@dataclass
class AcquisitionStats:
    state: str = "idle"
    samples: int = 0
    elapsed_s: float = 0.0
    requested_interval_ms: float = 0.0
    actual_interval_ms: float = 0.0
    late_samples: int = 0
    output_path: str = ""
    message: str = ""


@dataclass
class LoggingRecord:
    """Normalized record returned for new and legacy logging files."""

    headers: list[str]
    rows: list[list[Any]]
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def signal_names(self) -> list[str]:
        if not self.headers:
            return []
        time_columns = {"timestamp_utc", "time", "elapsed_s", "elapsed"}
        return [name for name in self.headers if name.lower() not in time_columns]
