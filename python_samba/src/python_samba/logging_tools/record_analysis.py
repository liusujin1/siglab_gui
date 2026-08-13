"""Numerical analysis helpers for completed logging records.

The controller and communication layers intentionally do not depend on this
module.  The optional GUI record viewer uses a compact NumPy-only numerical
layer so the portable TestKit does not need the full SciPy runtime.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import numpy as np

from python_samba.logging_tools.models import LoggingRecord
from python_samba.logging_tools.numeric_signal import (
    butter_sos,
    detrend,
    periodic_hann,
    sosfiltfilt,
    welch_psd,
)


TIME_HEADERS = {"timestamp_utc", "elapsed_s", "time", "elapsed"}
TIME_PRIORITY = ("elapsed_s", "time", "elapsed")
Domain = Literal["time", "frequency"]
XAxisUnit = Literal["seconds", "samples", "hertz"]
MAX_RESAMPLED_SAMPLES = 10_000_000


def _as_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _unwrapped(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key in ("Value", "value"):
            if key in value:
                return value[key]
    return value


def _positive_float(value: Any) -> float | None:
    result = _as_float(_unwrapped(value))
    return result if math.isfinite(result) and result > 0.0 else None


def _metadata_sample_rate(metadata: Mapping[str, Any]) -> tuple[float | None, str]:
    for key in ("sample_rate_hz", "sample_frequency", "SampleFrequency"):
        value = _positive_float(metadata.get(key))
        if value is not None:
            return value, f"metadata:{key}"

    interval = _positive_float(metadata.get("sample_interval_s"))
    if interval is not None:
        return 1.0 / interval, "metadata:sample_interval_s"
    interval_ms = _positive_float(metadata.get("interval_ms"))
    if interval_ms is not None:
        return 1000.0 / interval_ms, "metadata:interval_ms"

    parameters = metadata.get("Param")
    if isinstance(parameters, Mapping):
        frequency = _positive_float(
            parameters.get("SampleFrequency", parameters.get("sampleFrequency"))
        )
        under_sample = _positive_float(
            parameters.get("UnderSample", parameters.get("underSample"))
        )
        if frequency is not None:
            return frequency / (under_sample or 1.0), "metadata:Param"
    return None, ""


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class SamplingInfo:
    """Sampling information inferred from the record and its metadata."""

    sample_rate_hz: float | None
    source: str
    regular: bool
    jitter_ratio: float
    reason: str
    x_label: str
    uses_sample_index: bool = False


@dataclass(frozen=True)
class NumericCurve:
    """One immutable original or derived numeric curve."""

    curve_id: str
    name: str
    x: np.ndarray = field(repr=False)
    y: np.ndarray = field(repr=False)
    domain: Domain = "time"
    x_unit: XAxisUnit = "seconds"
    source_header: str = ""
    derived: bool = False
    parent_id: str | None = None
    operation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.x.ndim != 1 or self.y.ndim != 1:
            raise ValueError("curve arrays must be one-dimensional")
        if len(self.x) != len(self.y):
            raise ValueError("curve x/y arrays must have matching lengths")
        if self.domain not in {"time", "frequency"}:
            raise ValueError("curve domain must be 'time' or 'frequency'")
        if self.x_unit not in {"seconds", "samples", "hertz"}:
            raise ValueError("unsupported x-axis unit")


class RecordAnalysisSession:
    """Normalize a :class:`LoggingRecord` and manage in-memory derivatives."""

    def __init__(self, record: LoggingRecord) -> None:
        self.record = record
        self._curves: dict[str, NumericCurve] = {}
        self._order: list[str] = []
        self._derived_counter = 0
        self._x_header, x_values, self.sampling = self._record_axis(record)
        self._load_original_curves(record, x_values)

    @classmethod
    def from_record(cls, record: LoggingRecord) -> "RecordAnalysisSession":
        return cls(record)

    @property
    def curves(self) -> tuple[NumericCurve, ...]:
        return tuple(self._curves[curve_id] for curve_id in self._order)

    def curves_for_domain(self, domain: Domain) -> tuple[NumericCurve, ...]:
        return tuple(curve for curve in self.curves if curve.domain == domain)

    def get_curve(self, curve_id: str) -> NumericCurve:
        try:
            return self._curves[curve_id]
        except KeyError as exc:
            raise KeyError(f"unknown curve: {curve_id}") from exc

    @staticmethod
    def _record_axis(
        record: LoggingRecord,
    ) -> tuple[str, np.ndarray, SamplingInfo]:
        row_count = len(record.rows)
        lower = [str(header).strip().lower() for header in record.headers]
        x_index = -1
        x_header = "sample"
        for candidate in TIME_PRIORITY:
            if candidate not in lower:
                continue
            candidate_index = lower.index(candidate)
            values = np.asarray(
                [
                    _as_float(row[candidate_index])
                    if candidate_index < len(row)
                    else math.nan
                    for row in record.rows
                ],
                dtype=np.float64,
            )
            if np.count_nonzero(np.isfinite(values)) >= 2:
                x_index = candidate_index
                x_header = candidate
                x_values = values
                break
        else:
            x_values = np.arange(row_count, dtype=np.float64)

        metadata_rate, metadata_source = _metadata_sample_rate(record.metadata)
        uses_sample_index = x_index < 0
        if uses_sample_index:
            sampling = SamplingInfo(
                sample_rate_hz=metadata_rate,
                source=metadata_source or "sample-index",
                regular=True,
                jitter_ratio=0.0,
                reason=(
                    "Uniform sample index; sample rate read from metadata."
                    if metadata_rate is not None
                    else "Uniform sample index; enter a sample rate for filtering and spectra."
                ),
                x_label="Time (s)" if metadata_rate is not None else "Sample",
                uses_sample_index=True,
            )
            return x_header, _readonly(x_values), sampling

        finite = np.isfinite(x_values)
        finite_values = x_values[finite]
        missing_time = int(np.count_nonzero(finite)) != row_count
        differences = np.diff(finite_values)
        monotonic = len(differences) > 0 and bool(np.all(differences > 0.0))
        positive = differences[differences > 0.0]
        median_step = float(np.median(positive)) if len(positive) else math.nan
        measured_rate = (
            1.0 / median_step
            if math.isfinite(median_step) and median_step > 0.0
            else None
        )
        if len(positive) and median_step > 0.0:
            deviations = np.abs(positive - median_step) / median_step
            jitter_p95 = float(np.quantile(deviations, 0.95))
            maximum_jitter = float(np.max(deviations))
        else:
            jitter_p95 = math.inf
            maximum_jitter = math.inf
        jitter = max(jitter_p95, maximum_jitter)
        regular = monotonic and not missing_time and jitter_p95 <= 0.01 and maximum_jitter <= 0.01

        metadata_trustworthy = (
            metadata_rate is not None
            and measured_rate is not None
            and abs(metadata_rate - measured_rate) / measured_rate <= 0.01
        )
        if metadata_trustworthy:
            rate = metadata_rate
            source = metadata_source
        else:
            rate = measured_rate or metadata_rate
            source = "time-axis" if measured_rate is not None else metadata_source

        reasons: list[str] = []
        if missing_time:
            reasons.append("time column contains missing values")
        if not monotonic:
            reasons.append("time values are not strictly increasing")
        if math.isfinite(jitter) and jitter > 0.01:
            reasons.append(f"time-step jitter is {jitter * 100.0:.3g}%")
        if metadata_rate is not None and measured_rate is not None and not metadata_trustworthy:
            reasons.append("metadata sample rate differs from the time axis; using the time axis")
        if not reasons:
            reasons.append(
                f"Regular time axis ({source or 'unknown sample-rate source'})."
            )

        return x_header, _readonly(x_values), SamplingInfo(
            sample_rate_hz=rate,
            source=source or "unknown",
            regular=regular,
            jitter_ratio=jitter,
            reason="; ".join(reasons),
            x_label="Time (s)",
            uses_sample_index=False,
        )

    def _load_original_curves(
        self, record: LoggingRecord, x_values: np.ndarray
    ) -> None:
        lower = [str(header).strip().lower() for header in record.headers]
        for column, header in enumerate(record.headers):
            if lower[column] in TIME_HEADERS:
                continue
            values = np.asarray(
                [
                    _as_float(row[column]) if column < len(row) else math.nan
                    for row in record.rows
                ],
                dtype=np.float64,
            )
            if not np.any(np.isfinite(values)):
                continue
            name = self._unique_name(str(header).strip() or f"Column {column + 1}")
            curve = NumericCurve(
                curve_id=f"source-{column}",
                name=name,
                x=x_values,
                y=_readonly(values),
                domain="time",
                x_unit="samples" if self.sampling.uses_sample_index else "seconds",
                source_header=str(header),
                operation={"type": "source", "column": column},
            )
            self._curves[curve.curve_id] = curve
            self._order.append(curve.curve_id)

    def _unique_name(self, requested: str) -> str:
        base = requested.strip() or "Curve"
        existing = {curve.name.casefold() for curve in self._curves.values()}
        if base.casefold() not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})".casefold() in existing:
            suffix += 1
        return f"{base} ({suffix})"

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        value = float(sample_rate_hz)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("sample rate must be a positive finite number")
        self.sampling = replace(
            self.sampling,
            sample_rate_hz=value,
            source="user",
            reason=(
                "User sample rate; resample irregular curves before filtering or spectra."
                if not self.sampling.regular
                else "User sample rate."
            ),
            x_label="Time (s)" if self.sampling.uses_sample_index else self.sampling.x_label,
        )

    def _curve_rate(self, curve: NumericCurve) -> float | None:
        operation_rate = _positive_float(curve.operation.get("sample_rate_hz"))
        if operation_rate is not None:
            return operation_rate
        if curve.domain != "time" or not self.sampling.regular:
            return None
        return self.sampling.sample_rate_hz

    def can_process(self, curve_id: str) -> tuple[bool, str]:
        curve = self.get_curve(curve_id)
        if curve.domain != "time":
            return False, "Select a time-domain curve."
        if len(curve.y) < 3:
            return False, "At least three samples are required."
        if not np.all(np.isfinite(curve.x)) or not np.all(np.isfinite(curve.y)):
            return False, "The curve contains gaps; resample it first."
        if self._curve_rate(curve) is None:
            if self.sampling.sample_rate_hz is None:
                return False, "Enter a sample rate first."
            return False, "The time axis is irregular; resample the curve first."
        return True, ""

    def _require_processable(self, curve_id: str) -> tuple[NumericCurve, float]:
        curve = self.get_curve(curve_id)
        allowed, reason = self.can_process(curve_id)
        if not allowed:
            raise ValueError(reason)
        rate = self._curve_rate(curve)
        assert rate is not None
        return curve, rate

    def _add_derived(
        self,
        parent: NumericCurve,
        *,
        name: str,
        x: np.ndarray,
        y: np.ndarray,
        domain: Domain,
        operation: Mapping[str, Any],
        x_unit: XAxisUnit | None = None,
    ) -> NumericCurve:
        self._derived_counter += 1
        curve = NumericCurve(
            curve_id=f"derived-{self._derived_counter}",
            name=self._unique_name(name),
            x=_readonly(x),
            y=_readonly(y),
            domain=domain,
            x_unit=x_unit or ("hertz" if domain == "frequency" else parent.x_unit),
            source_header=parent.source_header,
            derived=True,
            parent_id=parent.curve_id,
            operation=dict(operation),
        )
        self._curves[curve.curve_id] = curve
        self._order.append(curve.curve_id)
        return curve

    def rename_curve(self, curve_id: str, name: str) -> NumericCurve:
        curve = self.get_curve(curve_id)
        if not curve.derived:
            raise ValueError("original curves cannot be renamed")
        stripped = str(name).strip()
        if not stripped:
            raise ValueError("curve name cannot be empty")
        other_names = {
            item.name.casefold()
            for item in self._curves.values()
            if item.curve_id != curve_id
        }
        requested = stripped
        suffix = 2
        while requested.casefold() in other_names:
            requested = f"{stripped} ({suffix})"
            suffix += 1
        updated = replace(curve, name=requested)
        self._curves[curve_id] = updated
        return updated

    def delete_curve(self, curve_id: str) -> None:
        curve = self.get_curve(curve_id)
        if not curve.derived:
            raise ValueError("original curves cannot be deleted")
        pending = [curve_id]
        removal: list[str] = []
        while pending:
            parent = pending.pop()
            removal.append(parent)
            pending.extend(
                item.curve_id
                for item in self._curves.values()
                if item.parent_id == parent
            )
        for selected in reversed(removal):
            if selected in self._curves:
                del self._curves[selected]
            if selected in self._order:
                self._order.remove(selected)

    def replace_curve_data(
        self, curve_id: str, result_curve_id: str
    ) -> NumericCurve:
        """Replace one curve with a computed result while preserving its identity.

        Numerical operations intentionally continue to build an immutable result
        first.  The record viewer then commits that result to the selected row so
        users can process a curve repeatedly without accumulating derivative
        entries in the curve list.
        """

        source = self.get_curve(curve_id)
        result = self.get_curve(result_curve_id)
        if result_curve_id == curve_id:
            return source
        if result.parent_id != curve_id:
            raise ValueError("the computed result does not belong to the selected curve")

        history = list(source.operation.get("processing_chain", ()))
        if (
            source.operation
            and source.operation.get("type") != "source"
            and not history
        ):
            history.append(dict(source.operation))
        step = dict(result.operation)
        step.pop("processing_chain", None)
        history.append(step)
        operation = dict(step)
        operation["processing_chain"] = history

        updated = replace(
            result,
            curve_id=source.curve_id,
            name=source.name,
            source_header=source.source_header,
            derived=source.derived,
            parent_id=source.parent_id,
            operation=operation,
        )
        del self._curves[result_curve_id]
        self._order.remove(result_curve_id)
        self._curves[curve_id] = updated
        return updated

    def resample_curve(
        self, curve_id: str, sample_rate_hz: float | None = None
    ) -> NumericCurve:
        curve = self.get_curve(curve_id)
        if curve.domain != "time":
            raise ValueError("only time-domain curves can be resampled")
        rate = float(sample_rate_hz or self.sampling.sample_rate_hz or 0.0)
        if not math.isfinite(rate) or rate <= 0.0:
            raise ValueError("enter a positive sample rate before resampling")
        finite = np.isfinite(curve.x) & np.isfinite(curve.y)
        if np.count_nonzero(finite) < 2:
            raise ValueError("at least two finite points are required for resampling")
        source_x = np.asarray(curve.x[finite], dtype=np.float64)
        source_y = np.asarray(curve.y[finite], dtype=np.float64)
        if curve.x_unit == "samples":
            source_x = source_x / rate
        order = np.argsort(source_x, kind="stable")
        source_x = source_x[order]
        source_y = source_y[order]
        source_x, unique_indices = np.unique(source_x, return_index=True)
        source_y = source_y[unique_indices]
        if len(source_x) < 2 or source_x[-1] <= source_x[0]:
            raise ValueError("time values must span a positive interval")
        estimated_intervals = (source_x[-1] - source_x[0]) * rate
        if not math.isfinite(estimated_intervals) or estimated_intervals + 1 > MAX_RESAMPLED_SAMPLES:
            raise ValueError(
                f"resampling would create more than {MAX_RESAMPLED_SAMPLES:,} samples"
            )
        sample_count = int(math.floor(estimated_intervals)) + 1
        if sample_count < 2:
            raise ValueError("sample rate is too low for this time span")
        target_x = source_x[0] + np.arange(sample_count, dtype=np.float64) / rate
        target_y = np.interp(target_x, source_x, source_y)
        return self._add_derived(
            curve,
            name=f"{curve.name} [Resampled {rate:g} Hz]",
            x=target_x,
            y=target_y,
            domain="time",
            x_unit="seconds",
            operation={
                "type": "resample",
                "sample_rate_hz": rate,
                "method": "linear",
            },
        )

    def detrend_curve(self, curve_id: str, mode: str = "constant") -> NumericCurve:
        curve, rate = self._require_processable(curve_id)
        kind = str(mode).strip().lower()
        if kind not in {"constant", "linear"}:
            raise ValueError("detrend mode must be 'constant' or 'linear'")
        values = detrend(curve.y, mode=kind)
        label = "Mean removed" if kind == "constant" else "Linear detrend"
        return self._add_derived(
            curve,
            name=f"{curve.name} [{label}]",
            x=curve.x,
            y=values,
            domain="time",
            operation={"type": "detrend", "mode": kind, "sample_rate_hz": rate},
        )

    def smooth_curve(self, curve_id: str, window: int = 5) -> NumericCurve:
        curve, rate = self._require_processable(curve_id)
        size = int(window)
        if size < 3 or size > 1001 or size % 2 == 0:
            raise ValueError("smoothing window must be an odd number from 3 to 1001")
        if size > len(curve.y):
            raise ValueError("smoothing window cannot exceed the sample count")
        pad = size // 2
        padded = np.pad(curve.y, pad, mode="reflect")
        kernel = np.full(size, 1.0 / size, dtype=np.float64)
        values = np.convolve(padded, kernel, mode="valid")
        return self._add_derived(
            curve,
            name=f"{curve.name} [MA {size}]",
            x=curve.x,
            y=values,
            domain="time",
            operation={
                "type": "moving_average",
                "window": size,
                "sample_rate_hz": rate,
            },
        )

    def filter_curve(
        self,
        curve_id: str,
        filter_type: str,
        *,
        low_hz: float | None = None,
        high_hz: float | None = None,
        order: int = 4,
    ) -> NumericCurve:
        curve, rate = self._require_processable(curve_id)
        kind = str(filter_type).strip().lower()
        filter_order = int(order)
        if not 1 <= filter_order <= 12:
            raise ValueError("filter order must be in the range 1..12")
        nyquist = rate / 2.0
        low = _positive_float(low_hz)
        high = _positive_float(high_hz)
        if kind == "lowpass":
            if high is None or high >= nyquist:
                raise ValueError("low-pass cutoff must be below Nyquist")
            critical: float | list[float] = high
            design_kind = "lowpass"
            description = f"LP {high:g} Hz O{filter_order}"
        elif kind == "highpass":
            if low is None or low >= nyquist:
                raise ValueError("high-pass cutoff must be below Nyquist")
            critical = low
            design_kind = "highpass"
            description = f"HP {low:g} Hz O{filter_order}"
        elif kind == "bandpass":
            if low is None or high is None or not 0.0 < low < high < nyquist:
                raise ValueError("band-pass cutoffs must satisfy 0 < low < high < Nyquist")
            critical = [low, high]
            design_kind = "bandpass"
            description = f"BP {low:g}-{high:g} Hz O{filter_order}"
        else:
            raise ValueError("filter type must be lowpass, highpass, or bandpass")
        sections = butter_sos(filter_order, critical, design_kind, rate)
        try:
            values = sosfiltfilt(sections, curve.y)
        except ValueError as exc:
            raise ValueError(f"not enough samples for zero-phase filtering: {exc}") from exc
        return self._add_derived(
            curve,
            name=f"{curve.name} [{description}]",
            x=curve.x,
            y=values,
            domain="time",
            operation={
                "type": kind,
                "low_hz": low,
                "high_hz": high,
                "order": filter_order,
                "sample_rate_hz": rate,
            },
        )

    def fft_curve(self, curve_id: str) -> NumericCurve:
        curve, rate = self._require_processable(curve_id)
        sample_count = len(curve.y)
        if sample_count < 4:
            raise ValueError("at least four samples are required for FFT")
        window = periodic_hann(sample_count)
        centered = curve.y - float(np.mean(curve.y))
        spectrum = np.fft.rfft(centered * window)
        scale = float(np.sum(window))
        amplitude = np.abs(spectrum) / scale
        if sample_count > 1:
            amplitude[1:-1 if sample_count % 2 == 0 else None] *= 2.0
        frequencies = np.fft.rfftfreq(sample_count, d=1.0 / rate)
        return self._add_derived(
            curve,
            name=f"{curve.name} [FFT]",
            x=frequencies,
            y=amplitude,
            domain="frequency",
            x_unit="hertz",
            operation={
                "type": "fft",
                "window": "hann",
                "sample_rate_hz": rate,
                "samples": sample_count,
            },
        )

    def psd_curve(self, curve_id: str, block_size: int = 4096) -> NumericCurve:
        curve, rate = self._require_processable(curve_id)
        if len(curve.y) < 8:
            raise ValueError("at least eight samples are required for PSD")
        requested = max(8, int(block_size))
        maximum = min(requested, len(curve.y))
        nperseg = 1 << int(math.floor(math.log2(maximum)))
        frequencies, density = welch_psd(curve.y, rate, nperseg, overlap=0.5)
        return self._add_derived(
            curve,
            name=f"{curve.name} [PSD {nperseg}]",
            x=frequencies,
            y=density,
            domain="frequency",
            x_unit="hertz",
            operation={
                "type": "psd",
                "window": "hann",
                "block_size": nperseg,
                "overlap": 0.5,
                "sample_rate_hz": rate,
            },
        )

    @staticmethod
    def displayed_y(curve: NumericCurve, *, decibels: bool) -> np.ndarray:
        if not decibels or curve.domain != "frequency":
            return curve.y
        floor = np.finfo(np.float64).tiny
        multiplier = 10.0 if curve.operation.get("type") == "psd" else 20.0
        return multiplier * np.log10(np.maximum(np.abs(curve.y), floor))

    def export_curves(
        self,
        path: str | Path,
        curve_ids: Iterable[str],
        *,
        frequency_decibels: bool = False,
    ) -> Path:
        selected = [self.get_curve(curve_id) for curve_id in curve_ids]
        if not selected:
            raise ValueError("select at least one curve to export")
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        arrays: list[tuple[NumericCurve, np.ndarray]] = [
            (
                curve,
                self.displayed_y(
                    curve,
                    decibels=frequency_decibels and curve.domain == "frequency",
                ),
            )
            for curve in selected
        ]
        maximum_rows = max(len(curve.x) for curve, _values in arrays)
        with output.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            headers: list[str] = []
            for curve, _values in arrays:
                suffix = " (dB)" if frequency_decibels and curve.domain == "frequency" else ""
                headers.extend([f"{curve.name}_x", f"{curve.name}{suffix}_y"])
            writer.writerow(headers)
            for row_index in range(maximum_rows):
                row: list[str] = []
                for curve, values in arrays:
                    if row_index < len(curve.x):
                        x_value = float(curve.x[row_index])
                        if (
                            curve.x_unit == "samples"
                            and self.sampling.sample_rate_hz is not None
                        ):
                            x_value /= self.sampling.sample_rate_hz
                        row.extend(
                            [
                                format(x_value, ".12g"),
                                format(float(values[row_index]), ".12g"),
                            ]
                        )
                    else:
                        row.extend(["", ""])
                writer.writerow(row)

        sidecar = output.with_suffix(output.suffix + ".meta.json")
        payload = {
            "schema": "python-samba-record-analysis/v1",
            "source": self.record.source,
            "source_metadata": self.record.metadata,
            "frequency_decibels": bool(frequency_decibels),
            "sampling": {
                "sample_rate_hz": self.sampling.sample_rate_hz,
                "source": self.sampling.source,
                "regular": self.sampling.regular,
                "jitter_ratio": self.sampling.jitter_ratio,
                "reason": self.sampling.reason,
                "x_label": self.sampling.x_label,
                "uses_sample_index": self.sampling.uses_sample_index,
            },
            "curves": [
                {
                    "curve_id": curve.curve_id,
                    "name": curve.name,
                    "domain": curve.domain,
                    "x_unit": (
                        "seconds"
                        if curve.x_unit == "samples"
                        and self.sampling.sample_rate_hz is not None
                        else curve.x_unit
                    ),
                    "source_x_unit": curve.x_unit,
                    "source_header": curve.source_header,
                    "derived": curve.derived,
                    "parent_id": curve.parent_id,
                    "operation": dict(curve.operation),
                    "samples": len(curve.x),
                }
                for curve in selected
            ],
        }
        sidecar.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return output
