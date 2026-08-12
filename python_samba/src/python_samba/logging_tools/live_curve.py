"""Real-time monitor signal catalog, acquisition buffer, and polling service."""

from __future__ import annotations

import csv
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from python_samba.logging_tools.models import AcquisitionStats, LoggingRecord
from python_samba.ui.label_files import LABEL_FILE_DEFAULTS


INPUT_NAMES = LABEL_FILE_DEFAULTS["InputName"]
TEMPERATURE_NAMES = LABEL_FILE_DEFAULTS["MotorTemperaturSensorName"]
OUTPUT_NAMES = LABEL_FILE_DEFAULTS["DACOutputName"]
VELOCITY_AXES = LABEL_FILE_DEFAULTS["VelAxesName"]
POSITION_AXES = LABEL_FILE_DEFAULTS["PosAxesName"]
PNEUMATIC_AXES = LABEL_FILE_DEFAULTS["PneuAxesName"]
PROXIMITY_CORRECTIONS = (
    "Prox1", "Prox2", "Prox3", "Prox4", "ProxH1", "ProxH2", "ProxH3", "ProxH4"
)


def _ff_name(io_type: int, main: int, sub: int) -> str:
    prefix = "FF" if io_type == 10 else "PFF"
    if 0 <= sub <= 2:
        suffix = f"RefFil{sub + 1}"
    elif 3 <= sub <= 5:
        suffix = f"SecFil{sub - 2}"
    else:
        axes = VELOCITY_AXES if io_type == 10 else PNEUMATIC_AXES
        axis = sub - 6
        suffix = f"{axes[axis]} Out" if 0 <= axis < len(axes) else f"Output{sub}"
    return f"{prefix} Ch{main + 1} {suffix}"


@dataclass(frozen=True, slots=True)
class MonitorSignalSpec:
    """One displayable IOSignal identified by its controller triple."""

    name: str
    category: str
    io_type: int
    main_index: int
    sub_index: int

    @property
    def tokens(self) -> tuple[int, int, int]:
        return (self.io_type, self.main_index, self.sub_index)

    @property
    def key(self) -> str:
        return f"{self.io_type}:{self.main_index}:{self.sub_index}"


@dataclass(frozen=True, slots=True)
class LiveCurveConfig:
    signals: tuple[MonitorSignalSpec, ...]
    interval_ms: int = 100
    initial_span_s: float = 60.0

    def validate(self) -> None:
        if not 1 <= len(self.signals) <= 40:
            raise ValueError("select between 1 and 40 signals")
        if len({signal.tokens for signal in self.signals}) != len(self.signals):
            raise ValueError("the selected signal list contains duplicates")
        if not 20 <= int(self.interval_ms) <= 5000:
            raise ValueError("sampling interval must be in the range 20..5000 ms")
        if not math.isfinite(float(self.initial_span_s)) or self.initial_span_s <= 0:
            raise ValueError("initial display span must be positive")


@dataclass(frozen=True, slots=True)
class MonitorCapabilities:
    input_count: int = 37
    velocity_axes: int = 6
    pneumatic_axes: int = 3
    position_axes: int = 6
    velocity_stages: int = 7
    position_stages: int = 4
    proximity_count: int = 6
    temperature: bool = False
    ff_pff: bool = True
    polynom_count: int = 0
    proximity_correction: bool = False
    features: frozenset[str] = frozenset()

    @classmethod
    def from_controller(
        cls,
        constants: Sequence[object] = (),
        version: object | None = None,
    ) -> "MonitorCapabilities":
        values = tuple(str(value) for value in constants)
        features = frozenset(value.upper() for value in values[11:])

        def numeric(index: int, default: int, maximum: int) -> int:
            try:
                return max(1, min(maximum, int(values[index])))
            except (IndexError, TypeError, ValueError):
                return default

        position_axes = numeric(3, 6, len(POSITION_AXES))
        for token in features:
            if token.startswith("POSAXES#"):
                try:
                    position_axes = max(
                        1,
                        min(
                            len(POSITION_AXES),
                            int(token.partition("#")[2]),
                        ),
                    )
                except ValueError:
                    pass

        firmware_tuple = (
            int(getattr(version, "major", 0)),
            int(getattr(version, "minor", 0)),
            int(getattr(version, "patch", 0)),
        )
        known = bool(features)
        polynom_count = 0
        for token in features:
            if token.startswith(("POLYNOM#", "POLY#")):
                try:
                    parts = token.split("#")
                    polynom_count = max(0, min(19, int(parts[1])))
                except (IndexError, ValueError):
                    pass
        if not polynom_count and any("POLY" in token for token in features):
            polynom_count = 19

        proximity_count = numeric(5, 6, 8)
        return cls(
            input_count=(
                46
                if not known or features.intersection({"EADCS", "PNEUMRAMP"})
                else 37
            ),
            velocity_axes=numeric(1, 6, len(VELOCITY_AXES)),
            pneumatic_axes=numeric(2, 3, len(PNEUMATIC_AXES)),
            position_axes=position_axes,
            velocity_stages=numeric(6, 7, 7),
            position_stages=numeric(7, 4, 12),
            proximity_count=proximity_count,
            temperature=(not known or any("TMPSENS" in token or "TEMPSENS" in token for token in features)),
            ff_pff=(not version or firmware_tuple >= (3, 3, 115)),
            polynom_count=polynom_count if known else 19,
            proximity_correction=(
                not known
                or any("PROX" in token and "CORR" in token for token in features)
            ),
            features=features,
        )


def build_monitor_signal_catalog(
    capabilities: MonitorCapabilities,
) -> tuple[MonitorSignalSpec, ...]:
    """Build the original IOSignal tree leaves, clipped to firmware dimensions."""

    out: list[MonitorSignalSpec] = []

    def add(category: str, tokens: tuple[int, int, int], name: str) -> None:
        out.append(
            MonitorSignalSpec(
                name=name,
                category=category,
                io_type=tokens[0],
                main_index=tokens[1],
                sub_index=tokens[2],
            )
        )

    for index in range(min(capabilities.input_count, len(INPUT_NAMES))):
        add("Sensor", (0, index, 0), INPUT_NAMES[index])
    if capabilities.temperature:
        for index, name in enumerate(TEMPERATURE_NAMES):
            add("Temperature", (12, index, 0), name)
    for index, name in enumerate(OUTPUT_NAMES):
        add("Actuator", (1, index, 0), name)
    for axis, axis_name in enumerate(
        VELOCITY_AXES[: capabilities.velocity_axes]
    ):
        add("Velocity", (2, axis, -1), f"{axis_name} Raw")
        for stage in range(capabilities.velocity_stages):
            add("Velocity", (2, axis, stage), f"{axis_name} Stage{stage + 1}")
        add("Velocity", (4, axis, 0), f"{axis_name} Output")
    for axis, axis_name in enumerate(
        POSITION_AXES[: capabilities.position_axes]
    ):
        add("Position", (5, axis, -1), f"{axis_name} Raw")
        for stage in range(capabilities.position_stages):
            add("Position", (5, axis, stage), f"{axis_name} Stage{stage + 1}")
        add(
            "Position",
            (5, axis, capabilities.position_stages),
            f"{axis_name} Output",
        )
    for axis, axis_name in enumerate(
        PNEUMATIC_AXES[: capabilities.pneumatic_axes]
    ):
        add("Pneumatic", (8, axis, -1), f"{axis_name} Raw")
        for stage in range(4):
            add("Pneumatic", (8, axis, stage), f"{axis_name} Stage{stage + 1}")
        add("Pneumatic", (8, axis, 4), f"{axis_name} Output")
    add("Excitation", (3, 0, 0), "Excitation")
    if capabilities.ff_pff:
        for channel in range(7):
            for stage in range(3):
                add("Feed Forward", (10, channel, stage), _ff_name(10, channel, stage))
                add("Feed Forward", (10, channel, stage + 3), _ff_name(10, channel, stage + 3))
            for axis in range(capabilities.velocity_axes):
                add("Feed Forward", (10, channel, axis + 6), _ff_name(10, channel, axis + 6))
        for channel in range(4):
            for stage in range(3):
                add("Pneum. FF", (11, channel, stage), _ff_name(11, channel, stage))
                add("Pneum. FF", (11, channel, stage + 3), _ff_name(11, channel, stage + 3))
            for axis in range(capabilities.pneumatic_axes):
                add("Pneum. FF", (11, channel, axis + 6), _ff_name(11, channel, axis + 6))
    for index in range(capabilities.polynom_count):
        add("Polynom", (13, index, 0), f"Polynom{index + 1} Input")
        add("Polynom", (13, index, 1), f"Polynom{index + 1} Output")
    if capabilities.proximity_correction:
        names = PROXIMITY_CORRECTIONS[: capabilities.proximity_count]
        for index, name in enumerate(names):
            add("Proximity Correction", (14, index, 0), f"{name} Correction")
    return tuple(out)


@dataclass(frozen=True, slots=True)
class LiveCurveSnapshot:
    timestamps_utc: tuple[str, ...]
    elapsed_s: np.ndarray
    values: np.ndarray
    generation: int

    def __post_init__(self) -> None:
        self.elapsed_s.setflags(write=False)
        self.values.setflags(write=False)


@dataclass(slots=True)
class _LiveChunk:
    elapsed_s: np.ndarray
    values: np.ndarray
    timestamps: list[str]
    used: int = 0


class LiveCurveSessionBuffer:
    """Thread-safe chunked in-memory store that never silently truncates."""

    def __init__(
        self,
        signals: Sequence[MonitorSignalSpec],
        *,
        chunk_size: int = 4096,
        session_id: str | None = None,
    ) -> None:
        if not 1 <= len(signals) <= 40:
            raise ValueError("buffer requires between 1 and 40 signals")
        if chunk_size < 16:
            raise ValueError("chunk_size must be at least 16")
        self.signals = tuple(signals)
        self.chunk_size = int(chunk_size)
        self.session_id = session_id or uuid.uuid4().hex
        self._chunks: list[_LiveChunk] = []
        self._lock = threading.RLock()
        self._count = 0
        self._generation = 0
        self._origin_monotonic: float | None = None
        self._active_since: float | None = None
        self._stopped_at: float | None = None
        self.pause_intervals: list[dict[str, float | str]] = []
        self.restart_events: list[float] = []
        self._pending_pause_reason = ""

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._count

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def time_bounds(self) -> tuple[float, float] | None:
        with self._lock:
            if not self._chunks or not self._count:
                return None
            first = next((chunk for chunk in self._chunks if chunk.used), None)
            last = next((chunk for chunk in reversed(self._chunks) if chunk.used), None)
            if first is None or last is None:
                return None
            return (float(first.elapsed_s[0]), float(last.elapsed_s[last.used - 1]))

    @property
    def completed_gaps(self) -> tuple[tuple[float, float], ...]:
        """Return completed stop/start gaps in elapsed-time coordinates."""

        with self._lock:
            gaps: list[tuple[float, float]] = []
            for item in self.pause_intervals:
                try:
                    start = float(item["start_s"])
                    end = float(item["end_s"])
                except (KeyError, TypeError, ValueError):
                    continue
                if end > start:
                    gaps.append((start, end))
            return tuple(gaps)

    def start_segment(self, monotonic_now: float | None = None) -> float:
        now = time.monotonic() if monotonic_now is None else float(monotonic_now)
        with self._lock:
            if self._origin_monotonic is None:
                self._origin_monotonic = now
            elif self._stopped_at is not None:
                pause = {
                        "start_s": self._stopped_at - self._origin_monotonic,
                        "end_s": now - self._origin_monotonic,
                        "duration_s": max(0.0, now - self._stopped_at),
                }
                if self._pending_pause_reason:
                    pause["reason"] = self._pending_pause_reason
                self.pause_intervals.append(pause)
                self.restart_events.append(now - self._origin_monotonic)
            self._active_since = now
            self._stopped_at = None
            self._pending_pause_reason = ""
            return now - self._origin_monotonic

    def stop_segment(self, monotonic_now: float | None = None, reason: str = "stopped") -> None:
        now = time.monotonic() if monotonic_now is None else float(monotonic_now)
        with self._lock:
            if self._origin_monotonic is None or self._active_since is None:
                return
            self._active_since = None
            self._stopped_at = now
            self._pending_pause_reason = str(reason)

    def elapsed_from_monotonic(self, monotonic_now: float) -> float:
        with self._lock:
            if self._origin_monotonic is None:
                self._origin_monotonic = float(monotonic_now)
            return float(monotonic_now) - self._origin_monotonic

    def _new_chunk(self) -> _LiveChunk:
        # Allocation is deliberately isolated so MemoryError leaves every
        # already captured chunk intact.
        return _LiveChunk(
            elapsed_s=np.empty(self.chunk_size, dtype=np.float64),
            values=np.empty((self.chunk_size, len(self.signals)), dtype=np.float64),
            timestamps=[],
        )

    def append(
        self,
        timestamp_utc: datetime | str,
        elapsed_s: float,
        values: Sequence[float],
    ) -> None:
        vector = np.asarray(values, dtype=np.float64)
        if vector.shape != (len(self.signals),):
            raise ValueError(
                f"sample has {vector.size} values; expected {len(self.signals)}"
            )
        elapsed = float(elapsed_s)
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("elapsed time must be finite and non-negative")
        if isinstance(timestamp_utc, datetime):
            stamp = timestamp_utc.astimezone(timezone.utc).isoformat()
        else:
            stamp = str(timestamp_utc)
        with self._lock:
            if self._chunks and self._chunks[-1].used:
                previous = float(self._chunks[-1].elapsed_s[self._chunks[-1].used - 1])
                if elapsed <= previous:
                    raise ValueError("elapsed time must be strictly increasing")
            if not self._chunks or self._chunks[-1].used >= self.chunk_size:
                self._chunks.append(self._new_chunk())
            chunk = self._chunks[-1]
            offset = chunk.used
            chunk.elapsed_s[offset] = elapsed
            chunk.values[offset, :] = vector
            chunk.timestamps.append(stamp)
            chunk.used += 1
            self._count += 1
            self._generation += 1

    def snapshot(
        self,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
    ) -> LiveCurveSnapshot:
        with self._lock:
            elapsed_parts: list[np.ndarray] = []
            value_parts: list[np.ndarray] = []
            timestamps: list[str] = []
            for chunk in self._chunks:
                if not chunk.used:
                    continue
                x = chunk.elapsed_s[: chunk.used]
                lo = 0 if start_s is None else int(np.searchsorted(x, start_s, side="left"))
                hi = chunk.used if end_s is None else int(np.searchsorted(x, end_s, side="right"))
                if hi <= lo:
                    continue
                elapsed_parts.append(x[lo:hi].copy())
                value_parts.append(chunk.values[lo:hi, :].copy())
                timestamps.extend(chunk.timestamps[lo:hi])
            if elapsed_parts:
                elapsed = np.concatenate(elapsed_parts)
                values = np.concatenate(value_parts, axis=0)
            else:
                elapsed = np.empty(0, dtype=np.float64)
                values = np.empty((0, len(self.signals)), dtype=np.float64)
            generation = self._generation
        return LiveCurveSnapshot(tuple(timestamps), elapsed, values, generation)

    def to_logging_record(self, source: str = "") -> LoggingRecord:
        snapshot = self.snapshot()
        rows = [
            [snapshot.timestamps_utc[index], float(snapshot.elapsed_s[index]), *snapshot.values[index].tolist()]
            for index in range(snapshot.elapsed_s.size)
        ]
        return LoggingRecord(
            ["timestamp_utc", "elapsed_s", *[signal.name for signal in self.signals]],
            rows,
            source,
            {
                "kind": "real-time-curve",
                "session_id": self.session_id,
                "signals": [
                    {"name": signal.name, "category": signal.category, "io_signal": list(signal.tokens)}
                    for signal in self.signals
                ],
                "pause_intervals": list(self.pause_intervals),
                "restart_events": list(self.restart_events),
            },
        )

    def export_csv(
        self,
        path: str | Path,
        *,
        colors: dict[str, str] | None = None,
        controller: dict[str, Any] | None = None,
        requested_interval_ms: float = 0.0,
        actual_interval_ms: float = 0.0,
        late_samples: int = 0,
    ) -> Path:
        output = Path(path).expanduser().resolve()
        if output.suffix.lower() != ".csv":
            output = output.with_suffix(".csv")
        output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot()
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["timestamp_utc", "elapsed_s", *[signal.name for signal in self.signals]]
            )
            for index in range(snapshot.elapsed_s.size):
                writer.writerow(
                    [
                        snapshot.timestamps_utc[index],
                        f"{float(snapshot.elapsed_s[index]):.12g}",
                        *[f"{float(value):.12g}" for value in snapshot.values[index]],
                    ]
                )
        metadata = {
            "kind": "real-time-curve",
            "session_id": self.session_id,
            "source": str(output),
            "signals": [
                {
                    "name": signal.name,
                    "category": signal.category,
                    "io_signal": list(signal.tokens),
                    "color": (colors or {}).get(signal.key, ""),
                }
                for signal in self.signals
            ],
            "controller": controller or {},
            "requested_interval_ms": float(requested_interval_ms),
            "actual_interval_ms": float(actual_interval_ms),
            "late_samples": int(late_samples),
            "pause_intervals": list(self.pause_intervals),
            "restart_events": list(self.restart_events),
            "sample_count": int(snapshot.elapsed_s.size),
        }
        sidecar = output.with_suffix(output.suffix + ".meta.json")
        sidecar.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return output


LiveSampleCallback = Callable[[AcquisitionStats, list[float]], None]
LiveFinishedCallback = Callable[[AcquisitionStats, BaseException | None], None]


class LiveCurveAcquisitionService:
    """Poll a leased DGMSV range without catch-up bursts or Qt dependencies."""

    def __init__(self, session, buffer: LiveCurveSessionBuffer) -> None:
        self.session = session
        self.buffer = buffer
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.stats = AcquisitionStats()

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(
        self,
        config: LiveCurveConfig,
        *,
        on_sample: LiveSampleCallback | None = None,
        on_finished: LiveFinishedCallback | None = None,
    ) -> None:
        config.validate()
        if tuple(config.signals) != self.buffer.signals:
            raise ValueError("acquisition config and buffer signals do not match")
        with self._lock:
            if self.running:
                raise RuntimeError("real-time acquisition is already running")
            if not self.session or not self.session.connected:
                raise RuntimeError("controller is not connected")
            self._stop.clear()
            self.stats = AcquisitionStats(
                state="running", requested_interval_ms=float(config.interval_ms)
            )
            self._thread = threading.Thread(
                target=self._run,
                name="SambaRealTimeCurve",
                args=(config, on_sample, on_finished),
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, wait: bool = False, timeout: float | None = None) -> None:
        self._stop.set()
        if self.running:
            self.stats.state = "stopping"
            self.stats.message = "stopping after the current controller read"
        thread = self._thread
        if wait and thread and thread is not threading.current_thread():
            thread.join(timeout)

    def _snapshot_stats(self) -> AcquisitionStats:
        return replace(self.stats)

    def _run(
        self,
        config: LiveCurveConfig,
        on_sample: LiveSampleCallback | None,
        on_finished: LiveFinishedCallback | None,
    ) -> None:
        read_session = self.session
        owned_session = None
        error: BaseException | None = None
        terminal = "stopped"
        try:
            open_reader = getattr(self.session, "open_background_reader", None)
            if callable(open_reader):
                owned_session = open_reader("python_samba-real-time-curve")
                if owned_session is not None:
                    read_session = owned_session
            started = time.monotonic()
            self.buffer.start_segment(started)
            deadline = started
            previous_sample: float | None = None
            interval_total = 0.0
            interval_count = 0
            interval_s = config.interval_ms / 1000.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now < deadline and self._stop.wait(deadline - now):
                    break
                request_started = time.monotonic()
                if request_started - deadline > max(interval_s * 0.25, 0.010):
                    self.stats.late_samples += 1
                values = list(read_session.get_monitor_values(0, len(config.signals) - 1))
                sampled = time.monotonic()
                if self._stop.is_set():
                    break
                if len(values) != len(config.signals):
                    raise RuntimeError(
                        f"DGMSV returned {len(values)} values; expected {len(config.signals)}"
                    )
                elapsed = self.buffer.elapsed_from_monotonic(sampled)
                try:
                    self.buffer.append(datetime.now(timezone.utc), elapsed, values)
                except MemoryError:
                    self.stats.message = "memory exhausted; acquisition stopped safely"
                    terminal = "memory-full"
                    break
                self.stats.samples += 1
                self.stats.elapsed_s = elapsed
                if previous_sample is not None:
                    interval_total += sampled - previous_sample
                    interval_count += 1
                    self.stats.actual_interval_ms = 1000.0 * interval_total / interval_count
                previous_sample = sampled
                if on_sample:
                    on_sample(self._snapshot_stats(), values)
                deadline += interval_s
                if deadline <= sampled:
                    missed = int((sampled - deadline) // interval_s) + 1
                    self.stats.late_samples += missed
                    deadline += missed * interval_s
                request_ms = (sampled - request_started) * 1000.0
                if request_ms > config.interval_ms:
                    self.stats.message = f"controller read needs {request_ms:.1f} ms"
            self.stats.state = terminal
        except BaseException as exc:
            error = exc
            self.stats.state = "error"
            self.stats.message = str(exc)
        finally:
            self.buffer.stop_segment(time.monotonic(), self.stats.state)
            if owned_session is not None:
                try:
                    owned_session.close()
                except Exception:
                    pass
            with self._lock:
                self._thread = None
            if on_finished:
                on_finished(self._snapshot_stats(), error)


__all__ = [
    "LiveCurveAcquisitionService",
    "LiveCurveConfig",
    "LiveCurveSessionBuffer",
    "LiveCurveSnapshot",
    "MonitorCapabilities",
    "MonitorSignalSpec",
    "build_monitor_signal_catalog",
]
