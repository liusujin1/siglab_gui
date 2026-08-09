"""Trace measurement parameters — port of ``SAMBA19xLib.TraceParameters``."""

from __future__ import annotations

from dataclasses import dataclass, field

from python_sidmat.backend.iosignal import IOType

__all__ = ["TraceParameters"]

DEFAULT_MAX_PAIR_PER_RCI = 16
FAST_MAX_PAIR_PER_RCI = 40
MIN_TRACE_SAMPLES = 2
MAX_TRACE_SAMPLES = 8192
MAX_UNDERSAMPLES = 65534
MAX_AVERAGE_NUMBER = 1000


@dataclass(slots=True)
class TraceParameters:
    """Configuration for one digital trace acquisition.

    Mirrors ``SAMBA19xLib.TraceParameters`` defaults:
    ``NoSamples=100``, ``Undersamples=1``, ``TraceFilterFlag=0``,
    ``AverageNumber=3``, ``MaxDataPairPerRCI=16``.
    """

    trace_ch0: IOType = field(default_factory=IOType)
    trace_ch1: IOType = field(default_factory=IOType)
    undersamples: int = 1
    no_samples: int = 100
    trace_filter_flag: int = 0          # 0 = use anti-aliasing, 1 = don't
    status: int = 0                     # 0 idle, 1 measuring, 2 done
    average_number: int = 3
    current_avg_num: int = 0
    measuring: bool = False
    max_data_pair_per_rci: int = DEFAULT_MAX_PAIR_PER_RCI
    is_fast_data_loading: bool = False

    def __post_init__(self) -> None:
        if self.is_fast_data_loading:
            self.max_data_pair_per_rci = FAST_MAX_PAIR_PER_RCI
        self.validate()

    def validate(self) -> None:
        """Validate values that would otherwise create invalid RCI requests."""
        checks = (
            ("undersamples", self.undersamples, 1),
            ("no_samples", self.no_samples, MIN_TRACE_SAMPLES),
            ("average_number", self.average_number, 1),
            ("max_data_pair_per_rci", self.max_data_pair_per_rci, 1),
        )
        for name, value, minimum in checks:
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if number != value or number < minimum:
                raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
        if int(self.no_samples) > MAX_TRACE_SAMPLES:
            raise ValueError(
                f"no_samples must be <= {MAX_TRACE_SAMPLES}, got {self.no_samples}"
            )
        if int(self.undersamples) > MAX_UNDERSAMPLES:
            raise ValueError(
                f"undersamples must be <= {MAX_UNDERSAMPLES}, got {self.undersamples}"
            )
        if int(self.average_number) > MAX_AVERAGE_NUMBER:
            raise ValueError(
                f"average_number must be <= {MAX_AVERAGE_NUMBER}, got {self.average_number}"
            )
        if int(self.trace_filter_flag) not in (0, 1):
            raise ValueError(
                f"trace_filter_flag must be 0 or 1, got {self.trace_filter_flag!r}"
            )

    def set_fast_data_loading(self, enabled: bool) -> None:
        """Select the legacy fast-loader size (40-pair binary DGTBB)."""
        self.is_fast_data_loading = bool(enabled)
        self.max_data_pair_per_rci = (
            FAST_MAX_PAIR_PER_RCI if self.is_fast_data_loading
            else DEFAULT_MAX_PAIR_PER_RCI
        )

    @property
    def data_pairs_per_read(self) -> int:
        if self.is_fast_data_loading:
            return min(max(1, int(self.max_data_pair_per_rci)), FAST_MAX_PAIR_PER_RCI)
        return min(max(1, int(self.max_data_pair_per_rci)), DEFAULT_MAX_PAIR_PER_RCI)

    @property
    def read_chunk_count(self) -> int:
        """Number of DGTBV reads for a full trace (C# ``GetTraceData`` loop)."""
        chunks, rem = divmod(self.no_samples, self.data_pairs_per_read)
        return chunks + (1 if rem else 0)

    def encode(self) -> tuple[int, ...]:
        """9 parameters for DSTIV (two IO triples + undersample + n + flag)."""
        self.validate()
        return (
            *self.trace_ch0.encode(),
            *self.trace_ch1.encode(),
            int(self.undersamples),
            int(self.no_samples),
            int(self.trace_filter_flag),
        )

    @classmethod
    def from_tokens(cls, tokens: list[str] | tuple[str, ...]) -> "TraceParameters":
        """Parse a DGTIV response (9 tokens) into a TraceParameters."""
        vals: list[int] = []
        for token in tokens[:9]:
            try:
                vals.append(int(str(token), 0))
            except ValueError:
                vals.append(int(str(token)))
        if len(vals) < 9:
            raise ValueError(f"need 9 DGTIV tokens, got {len(vals)}")
        return cls(
            trace_ch0=IOType(vals[0], vals[1], vals[2]),
            trace_ch1=IOType(vals[3], vals[4], vals[5]),
            undersamples=vals[6],
            no_samples=vals[7],
            trace_filter_flag=vals[8],
        )
