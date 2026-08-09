"""Measurement engine — port of ``SAMBA19xUI.UserControls.TraceInfo``
``StartTracing`` / ``LookForTraceStatus`` / ``GetTraceData``.

For each average the engine:

1. triggers a trace (DASTA)
2. polls the trace status (DGTAS) until the controller reports done, with a
   timeout of ``max(100, undersample*no_samples/sample_freq + 10)`` seconds
3. reads the buffer in chunks of up to 16 pairs (DGTBV), or 40 pairs through
   the legacy binary DGTBB path when Fast Data Load is enabled

All averages are concatenated into one long Ch1/Ch2 time series so a single
``pwelch`` call over the concatenated data performs spectral averaging across
both Welch segments and repeated traces — the standard multi-average
noise-reduction behaviour.

The engine is pure Python: UI code runs it on a worker thread and forwards
the callbacks through Qt signals.  ``stop()`` is honoured between operations.
"""

from __future__ import annotations

from collections.abc import Callable
import math
import threading
import time

from python_sidmat.analysis.types import MeasurementRawData
from python_sidmat.backend.controller import Controller, ControllerError
from python_sidmat.measurement.trace import TraceParameters

__all__ = ["MeasurementEngine", "MeasurementCancelled"]

_POLL_INTERVAL = 0.25  # seconds between DGTAS polls (original slept ~1 s)


def _token_int(token: str) -> int:
    """Parse an RCI token (decimal or hex like '0x1A') into an int."""
    try:
        return int(token, 0)
    except ValueError:
        try:
            return int(token)
        except ValueError:
            return -1


class MeasurementCancelled(RuntimeError):
    """Raised when the measurement was stopped by the user."""


class MeasurementEngine:
    def __init__(
        self,
        controller: Controller,
        trace: TraceParameters,
        sample_frequency: float,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        on_average_complete: Callable[[int, list[float], list[float]], None] | None = None,
    ) -> None:
        self.controller = controller
        self.trace = trace
        self.sample_frequency = float(sample_frequency)
        self.on_progress = on_progress
        self.on_average_complete = on_average_complete
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Request cancellation; effective at the next check point."""
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise MeasurementCancelled()

    # -- public API -------------------------------------------------------

    def run(self) -> MeasurementRawData:
        """Execute the full multi-average acquisition and return the data.

        ``data`` is shaped ``[signal][sample]`` with the concatenated
        time series of all averages (Ch1 first, then Ch2).
        """
        trace = self.trace
        trace.validate()
        if not math.isfinite(self.sample_frequency) or self.sample_frequency <= 0:
            raise ValueError(
                f"sample_frequency must be positive, got {self.sample_frequency!r}"
            )
        all_ch1: list[float] = []
        all_ch2: list[float] = []
        completed = 0
        trace.current_avg_num = 0
        trace.measuring = True
        try:
            for avg in range(trace.average_number):
                self._check_stop()
                if self.on_progress:
                    self.on_progress(avg, trace.average_number)

                rc = self.controller.start_trace()
                # DASTA may return a non-zero error code: the original software
                # skips that average and moves on (StartTraceAction ref ErrorCode).
                if rc and _token_int(rc[0]) != 0:
                    continue

                self._wait_for_trace_status()
                self._check_stop()

                ch1, ch2 = self._read_trace_data()
                all_ch1.extend(ch1)
                all_ch2.extend(ch2)
                completed += 1
                trace.current_avg_num = completed

                if self.on_average_complete:
                    self.on_average_complete(avg, ch1, ch2)
        finally:
            trace.measuring = False

        return MeasurementRawData(
            sig_name=[trace.trace_ch0.name, trace.trace_ch1.name],
            data=[all_ch1, all_ch2],
            sample_rate=int(round(self.sample_frequency)),
            undersample=trace.undersamples,
            avg_num=completed,
            # Data is concatenated across successful averages; sample_num is
            # therefore the actual stored sample count, not the requested
            # count when the controller rejected or truncated a trace.
            sample_num=len(all_ch1),
        )

    # -- internals (mirror the C# methods) --------------------------------

    def _wait_for_trace_status(self) -> None:
        """Poll DGTAS until the trace is done (status == 0)."""
        trace = self.trace
        measure_time = (trace.undersamples * trace.no_samples) / self.sample_frequency
        timeout = max(100.0, measure_time + 10.0)  # seconds (C# semantics)

        started = time.monotonic()
        status = self.controller.get_trace_status()
        while status != 0:
            self._check_stop()
            if time.monotonic() - started > timeout:
                raise ControllerError(
                    f"trace status timed out after {timeout:.0f}s "
                    f"(last status {status})"
                )
            time.sleep(_POLL_INTERVAL)
            status = self.controller.get_trace_status()

    def _read_trace_data(self) -> tuple[list[float], list[float]]:
        """Read one full trace using text DGTBV or legacy binary DGTBB."""
        trace = self.trace
        ch1: list[float] = []
        ch2: list[float] = []
        requested = int(trace.no_samples)
        offset = 0
        while len(ch1) < requested:
            self._check_stop()
            want = min(trace.data_pairs_per_read, requested - len(ch1))
            if trace.is_fast_data_loading:
                c1, c2 = self.controller.get_trace_buffer_binary(offset, want)
            else:
                c1, c2 = self.controller.get_trace_buffer(offset)
            count = min(len(c1), len(c2), requested - len(ch1))
            if count <= 0:
                raise ControllerError(
                    f"DGTBV returned no complete samples at offset {offset} "
                    f"({len(ch1)}/{requested} read)"
                )
            ch1.extend(c1[:count])
            ch2.extend(c2[:count])
            # The offset is expressed in samples.  Advancing by the number
            # actually decoded also handles a short-but-valid controller chunk
            # without skipping data on the next request.
            offset += count
        return ch1, ch2
