"""Measurement data structures — mirrors ``SAMBA19xLib.MeasurementRawData``
and ``SAMBA19xLib.TFData``."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["MeasurementRawData", "TFData"]


@dataclass(slots=True)
class MeasurementRawData:
    """Acquired time-series data from a trace measurement.

    ``data`` is shaped ``[signal][sample]`` (C# ``data`` is a list of
    per-signal lists), so ``data[0]`` is channel 0's full time series.
    """

    sig_name: list[str] = field(default_factory=list)
    data: list[np.ndarray] = field(default_factory=list)
    sample_rate: int = 0            # Hz
    undersample: int = 1            # decimation factor
    avg_num: int = 1                # number of averages
    sample_num: int = 0             # total stored samples per signal

    def __post_init__(self) -> None:
        self.sig_name = [str(name) for name in self.sig_name]
        self.data = [
            np.asarray(channel, dtype=float).reshape(-1) for channel in self.data
        ]
        self.sample_rate = max(0, int(self.sample_rate))
        self.undersample = max(1, int(self.undersample))
        self.avg_num = max(0, int(self.avg_num))
        # Stored data is authoritative.  This prevents stale controller/file
        # metadata from making plotting, saving, and FFT lengths disagree.
        actual = max((len(channel) for channel in self.data), default=0)
        self.sample_num = actual

    @property
    def channel_count(self) -> int:
        return len(self.data)

    def channel(self, index: int) -> np.ndarray:
        if index < 0:
            raise IndexError(f"channel index must be non-negative, got {index}")
        try:
            return self.data[index]
        except IndexError as exc:
            raise IndexError(
                f"channel index {index} out of range for {len(self.data)} channel(s)"
            ) from exc

    @property
    def effective_sample_rate(self) -> float:
        """Sample rate represented by the stored samples.

        The controller reports its base acquisition rate while trace
        ``undersamples`` decimates the returned data.  Keeping both values in
        the data object allows file compatibility while making plots and FRF
        frequency axes use the correct rate.
        """
        if self.sample_rate <= 0:
            return 0.0
        return float(self.sample_rate) / max(1, int(self.undersample))

    def to_dict(self) -> dict[str, np.ndarray]:
        """Return {signal-name: array} for easy export."""
        out: dict[str, np.ndarray] = {}
        for index, arr in enumerate(self.data):
            name = self.sig_name[index] if index < len(self.sig_name) else ""
            name = name or f"Ch{index}"
            if name in out:
                name = f"{name}_{index}"
            out[name] = np.asarray(arr)
        return out


@dataclass(slots=True)
class TFData:
    """Transfer-function result used by the SiDiMaT plots.

    Complex amplitudes are single-sided (DC … Nyquist) H1 estimates.
    """

    ol_amplitude: np.ndarray = field(default_factory=lambda: np.array([], dtype=complex))
    freq: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    filtered_ol_amplitude: np.ndarray | None = None
    cl_amplitude: np.ndarray | None = None

    @property
    def magnitude(self) -> np.ndarray:
        return np.abs(self.ol_amplitude)

    @property
    def phase_deg(self) -> np.ndarray:
        return np.angle(self.ol_amplitude, deg=True)

    @classmethod
    def from_pwelch(cls, result) -> "TFData":
        """Build a TFData from a :class:`~python_sidmat.analysis.pwelch.PwelchResult`."""
        return cls(
            ol_amplitude=result.re + 1j * result.im,
            freq=result.freq,
        )
