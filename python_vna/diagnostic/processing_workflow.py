from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal

import numpy as np


IssueSeverity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ProcessingIssue:
    severity: IssueSeverity
    field: str
    message: str
    code: str


@dataclass(frozen=True, slots=True)
class CurveDescriptor:
    name: str
    curve_type: str
    unit: str = ""
    sample_rate_hz: float | None = None
    frequency_min_hz: float | None = None
    frequency_max_hz: float | None = None
    point_count: int = 0


@dataclass(frozen=True, slots=True)
class ProcessingRecipe:
    transfer: CurveDescriptor
    targets: tuple[CurveDescriptor, ...]
    direction: str
    transfer_factor: float
    input_factor: float
    frequency_min_hz: float | None
    frequency_max_hz: float | None
    regularization_floor: float
    quantity: str
    result_mode: str
    coherence_correction: bool
    allow_dimensionless: bool
    output_name_template: str = "{name}_{mode}"
    interpolation: dict[str, object] = field(default_factory=dict)
    curve_edits: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ProcessingIssue, ...]
    effective_frequency_min_hz: float | None
    effective_frequency_max_hz: float | None
    valid_points: int
    discarded_points: int

    @property
    def errors(self) -> tuple[ProcessingIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ProcessingIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def can_run(self) -> bool:
        return not self.errors


def parse_optional_number(text: object, *, field: str) -> tuple[float | None, ProcessingIssue | None]:
    value_text = str(text or "").strip()
    if not value_text:
        return None, None
    try:
        value = float(value_text)
    except (TypeError, ValueError):
        return None, ProcessingIssue("error", field, "请输入有效数字。", "invalid_number")
    if not np.isfinite(value):
        return None, ProcessingIssue("error", field, "数值必须为有限值。", "nonfinite_number")
    return value, None


def validate_control_points(
    frequency_hz: Iterable[float],
    values_db: Iterable[float],
) -> tuple[np.ndarray, np.ndarray, tuple[ProcessingIssue, ...]]:
    frequency = np.asarray(tuple(frequency_hz), dtype=float).ravel()
    values = np.asarray(tuple(values_db), dtype=float).ravel()
    issues: list[ProcessingIssue] = []
    if frequency.size != values.size:
        issues.append(ProcessingIssue("error", "curve_points", "频率和值的数量不一致。", "point_count_mismatch"))
        return np.array([], dtype=float), np.array([], dtype=float), tuple(issues)
    finite = np.isfinite(frequency) & np.isfinite(values) & (frequency > 0.0)
    if np.count_nonzero(finite) != frequency.size:
        issues.append(ProcessingIssue("warning", "curve_points", "已丢弃非有限或非正频率控制点。", "invalid_points_dropped"))
    frequency = frequency[finite]
    values = values[finite]
    if frequency.size < 2:
        issues.append(ProcessingIssue("error", "curve_points", "至少需要 2 个有效控制点。", "too_few_points"))
        return frequency, values, tuple(issues)
    order = np.argsort(frequency, kind="stable")
    frequency = frequency[order]
    values = values[order]
    unique_frequency, unique_indices = np.unique(frequency, return_index=True)
    if unique_frequency.size != frequency.size:
        issues.append(ProcessingIssue("error", "curve_points", "控制点包含重复频率，请合并后再计算。", "duplicate_frequency"))
    return unique_frequency, values[unique_indices], tuple(issues)


def validate_processing_task(
    *,
    transfer_frequency_hz: Iterable[float],
    transfer_values: Iterable[complex],
    target_frequency_hz: Iterable[float],
    requested_min_hz: float | None,
    requested_max_hz: float | None,
    direction: str,
    regularization_floor: float,
    target_unit: str,
    phase_available: bool,
    result_mode: str,
    allow_dimensionless: bool,
) -> ValidationReport:
    issues: list[ProcessingIssue] = []
    transfer_f = np.asarray(tuple(transfer_frequency_hz), dtype=float).ravel()
    transfer_h = np.asarray(tuple(transfer_values), dtype=complex).ravel()
    target_f = np.asarray(tuple(target_frequency_hz), dtype=float).ravel()
    transfer_count = min(transfer_f.size, transfer_h.size)
    transfer_f = transfer_f[:transfer_count]
    transfer_h = transfer_h[:transfer_count]
    transfer_valid = np.isfinite(transfer_f) & np.isfinite(transfer_h.real) & np.isfinite(transfer_h.imag) & (transfer_f > 0.0)
    target_valid = np.isfinite(target_f) & (target_f > 0.0)
    transfer_f = transfer_f[transfer_valid]
    transfer_h = transfer_h[transfer_valid]
    target_f = target_f[target_valid]

    if requested_min_hz is not None and requested_max_hz is not None and requested_min_hz >= requested_max_hz:
        issues.append(ProcessingIssue("error", "frequency_range", "频率下限必须小于频率上限。", "reversed_frequency_range"))
    if requested_min_hz is not None and requested_min_hz < 0.0:
        issues.append(ProcessingIssue("error", "frequency_min", "频率下限不能小于 0 Hz。", "negative_frequency"))
    if requested_max_hz is not None and requested_max_hz <= 0.0:
        issues.append(ProcessingIssue("error", "frequency_max", "频率上限必须大于 0 Hz。", "nonpositive_frequency"))
    if not np.isfinite(regularization_floor) or regularization_floor < 0.0:
        issues.append(ProcessingIssue("error", "regularization", "反推下限必须是大于等于 0 的有限值。", "invalid_regularization"))
    if transfer_f.size < 2:
        issues.append(ProcessingIssue("error", "transfer", "传递率没有足够的有效频点。", "invalid_transfer"))
    if target_f.size < 2:
        issues.append(ProcessingIssue("error", "target", "待换算数据没有足够的有效频点。", "invalid_target"))

    low = max(float(np.min(transfer_f)) if transfer_f.size else np.inf, float(np.min(target_f)) if target_f.size else np.inf)
    high = min(float(np.max(transfer_f)) if transfer_f.size else -np.inf, float(np.max(target_f)) if target_f.size else -np.inf)
    if requested_min_hz is not None:
        low = max(low, float(requested_min_hz))
    if requested_max_hz is not None:
        high = min(high, float(requested_max_hz))
    effective_low = low if np.isfinite(low) else None
    effective_high = high if np.isfinite(high) else None
    if effective_low is None or effective_high is None or effective_high <= effective_low:
        issues.append(ProcessingIssue("error", "frequency_range", "传递率与待换算数据没有可用的重叠频段。", "no_frequency_overlap"))
        valid_points = 0
    else:
        valid_points = int(np.count_nonzero((target_f >= effective_low) & (target_f <= effective_high)))
        if valid_points < 2:
            issues.append(ProcessingIssue("error", "frequency_range", "实际计算频段内不足 2 个数据点。", "too_few_overlap_points"))
    discarded_points = max(0, int(target_valid.size) - valid_points)

    reverse = str(direction).strip().lower() in {"top_to_base", "top->base", "reverse"}
    if reverse and transfer_h.size:
        near_zero = np.abs(transfer_h) <= max(float(regularization_floor), np.finfo(float).eps)
        if np.any(near_zero):
            severity: IssueSeverity = "error" if regularization_floor <= 0.0 else "warning"
            message = "反算频段含零或近零传递率，请设置反推下限。" if severity == "error" else "近零传递率将按反推下限进行正则化。"
            issues.append(ProcessingIssue(severity, "regularization", message, "near_zero_transfer"))
    if str(result_mode) == "近似时域" and not phase_available:
        issues.append(ProcessingIssue("warning", "result_mode", "传递率没有相位，输出为统计近似时域，不代表原始相位。", "statistical_time_only"))
    if str(result_mode) == "近似时域":
        issues.append(ProcessingIssue("warning", "result_mode", "传递率有效频段之外默认按零处理。", "out_of_band_zero"))

    normalized_unit = str(target_unit or "").strip().lower().replace(" ", "")
    known_dimensionless = normalized_unit in {"", "1", "-", "none", "无量纲"}
    if known_dimensionless and not allow_dimensionless:
        issues.append(ProcessingIssue("error", "unit", "待换算数据单位为空或无量纲，请确认后勾选“按无量纲继续”。", "unit_confirmation_required"))
    elif known_dimensionless:
        issues.append(ProcessingIssue("warning", "unit", "本次按无量纲数据继续，导出元数据会记录此覆盖。", "dimensionless_override"))

    return ValidationReport(tuple(issues), effective_low, effective_high, valid_points, discarded_points)
