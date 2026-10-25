from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from python_vna.daq.base import BackendFrame
from python_vna.models import AcquisitionConfig


HAMMER_FULL_SCALE_STEPS = tuple(10.0 / 2 ** index for index in range(10))


def first_hit_range(voltage: np.ndarray, reference: str) -> dict:
    peak = float(np.max(np.abs(voltage), initial=0.0))
    baseline = float(np.median(voltage))
    noise = float(1.4826 * np.median(np.abs(voltage - baseline)))
    level = peak * 0.35
    if not np.isfinite(peak) or level <= 0 or abs(baseline) + 6 * noise >= level:
        raise ValueError("首锤基线或背景噪声过大，请调整耦合或重新敲击")
    requested = min(10.0, peak * 1.2)
    full_scale = min(value for value in HAMMER_FULL_SCALE_STEPS if value >= requested)
    return {"state": "requested", "channel": reference, "peak_v": peak,
            "full_scale_v": full_scale, "trigger_level_v": level}


def smart_hit_active(acquisition: AcquisitionConfig, averaging: bool) -> bool:
    return bool(
        averaging and acquisition.modal.smart_hit_enabled
        and acquisition.averaging.mode == "linear" and acquisition.averaging.count >= 3
        and acquisition.trigger.enabled
        and acquisition.trigger.mode.strip().lower() == "every frame"
    )


def validate_first_hit_source(acquisition: AcquisitionConfig, averaging: bool) -> None:
    if smart_hit_active(acquisition, averaging) and acquisition.modal.smart_hit_auto_range_enabled:
        source = acquisition.trigger.source.strip().split("/")[-1]
        reference = acquisition.reference_channel.strip().split("/")[-1]
        if source != reference:
            raise ValueError("首锤自动量程需要以力锤参考通道触发，请调整触发通道或关闭首锤自动量程")


@dataclass
class ImpactCandidate:
    number: int
    frame: BackendFrame | None
    spectrum: np.ndarray
    force_peak: float
    warnings: list[str]


class ImpactSelector:
    def __init__(self, acquisition: AcquisitionConfig) -> None:
        modal = acquisition.modal
        if not np.isfinite(modal.smart_hit_tolerance) or modal.smart_hit_tolerance <= 0:
            raise ValueError("智能选锤一致性容差必须大于零")
        if acquisition.overlap_percent:
            raise ValueError("力锤智能选锤需要独立敲击，请将重叠设为无重叠")
        upper = min(acquisition.bandwidth_hz, acquisition.sample_rate / 2)
        if modal.smart_hit_max_hz > 0:
            upper = min(upper, modal.smart_hit_max_hz)
        if (not np.isfinite(modal.smart_hit_min_hz) or not np.isfinite(upper)
                or modal.smart_hit_min_hz < 0 or upper <= modal.smart_hit_min_hz):
            raise ValueError("智能选锤判定频带无效")
        if modal.smart_hit_response_channels == []:
            raise ValueError("请至少选择一个智能选锤判定通道")
        if modal.smart_hit_force_limits and not (
            np.isfinite(modal.smart_hit_force_min) and np.isfinite(modal.smart_hit_force_max)
            and 0 <= modal.smart_hit_force_min < modal.smart_hit_force_max
        ):
            raise ValueError("力峰值范围必须满足 0 ≤ 下限 < 上限")
        self.config = acquisition
        self.pending: list[ImpactCandidate] = []
        self.accepted: list[ImpactCandidate] = []
        self.history: deque[dict] = deque(maxlen=200)
        self.reasons: Counter = Counter()
        self.attempted = 0
        self.consecutive_rejections = 0
        self.last: dict = {}

    def begin(self, frame: BackendFrame, force_peak: float) -> None:
        self.attempted += 1
        self.last = dict(number=self.attempted, frame_index=int(frame.frame_index),
                         state="pending", reason="建立参考组", force_peak=float(force_peak),
                         deviation=None, channel="", warnings=[])
        self.history.append(self.last)

    def _resolve(self, number: int, state: str, reason: str) -> None:
        for item in self.history:
            if item["number"] == number:
                item.update(state=state, reason=reason)
                break
        if state == "rejected":
            self.reasons[reason] += 1
            self.consecutive_rejections += 1
        elif state == "accepted":
            self.consecutive_rejections = 0

    def reject(self, reason: str) -> dict:
        self._resolve(self.attempted, "rejected", reason)
        return self.report()

    def report(self) -> dict:
        return dict(
            smart_hit_active=True, smart_hit_state=self.last.get("state", "pending"),
            smart_hit_reason=self.last.get("reason", ""),
            smart_hit_force_peak=self.last.get("force_peak", 0.0),
            smart_hit_deviation=self.last.get("deviation"),
            smart_hit_channel=self.last.get("channel", ""),
            smart_hit_warnings=list(self.last.get("warnings", [])),
            smart_hit_attempted=self.attempted, smart_hit_accepted=len(self.accepted),
            smart_hit_consecutive_rejections=self.consecutive_rejections,
            smart_hit_reasons=dict(self.reasons),
            smart_hit_history=[dict(item) for item in self.history],
            smart_hit_complete=len(self.accepted) >= self.config.averaging.count,
            rejected=self.last.get("state") != "accepted",
        )

    def _distance(self, left: ImpactCandidate, right: ImpactCandidate,
                  frequencies: np.ndarray, names: list[str], reference: str,
                  responses: list[str]) -> tuple[float | None, str]:
        modal = self.config.modal
        upper = min(self.config.bandwidth_hz, frequencies[-1])
        if modal.smart_hit_max_hz > 0:
            upper = min(upper, modal.smart_hit_max_hz)
        band = (frequencies > max(0.0, modal.smart_hit_min_hz)) & (frequencies <= upper)
        if np.count_nonzero(band) < 8:
            return None, ""
        ref_index = names.index(reference)
        ref_left, ref_right = left.spectrum[ref_index], right.spectrum[ref_index]

        def strong(values, fraction=0.01):
            magnitude = np.abs(values)
            peak = float(np.max(magnitude[band], initial=0.0))
            return (magnitude >= peak * fraction) & (magnitude > np.finfo(float).tiny)

        reference_mask = band & strong(ref_left) & strong(ref_right)
        if np.count_nonzero(reference_mask) < max(8, int(np.ceil(np.count_nonzero(band) * 0.2))):
            return None, ""
        worst, worst_channel = 0.0, ""
        for channel in responses:
            if channel not in names:
                return None, channel
            index = names.index(channel)
            mask = reference_mask & strong(left.spectrum[index], 0.001) & strong(right.spectrum[index], 0.001)
            if np.count_nonzero(mask) < 8:
                return None, channel
            first = left.spectrum[index, mask] / ref_left[mask]
            second = right.spectrum[index, mask] / ref_right[mask]
            normalization = np.mean((np.abs(first) ** 2 + np.abs(second) ** 2) / 2)
            if normalization <= 0 or not np.isfinite(normalization):
                return None, channel
            distance = float(np.sqrt(np.mean(np.abs(first - second) ** 2) / normalization))
            if not np.isfinite(distance):
                return None, channel
            if distance >= worst:
                worst, worst_channel = distance, channel
        return worst, worst_channel

    def offer(self, candidate: ImpactCandidate, frequencies: np.ndarray, names: list[str],
              reference: str, responses: list[str]) -> tuple[list[ImpactCandidate], dict]:
        self.last["warnings"] = list(candidate.warnings)
        configured = self.config.modal.smart_hit_response_channels
        selected = responses if configured is None else [name for name in configured if name in responses]
        if not selected:
            return [], self.reject("未选择有效判定通道")
        upper = min(self.config.bandwidth_hz, frequencies[-1])
        if self.config.modal.smart_hit_max_hz > 0:
            upper = min(upper, self.config.modal.smart_hit_max_hz)
        band = (frequencies > max(0.0, self.config.modal.smart_hit_min_hz)) & (frequencies <= upper)
        rows = [names.index(name) for name in [reference, *selected]]
        candidate.spectrum = candidate.spectrum[rows][:, band].copy()
        names = [reference, *selected]
        frequencies = frequencies[band]
        if frequencies.size < 8:
            return [], self.reject("有效频点不足，无法判定")
        validity, channel = self._distance(candidate, candidate, frequencies, names, reference, selected)
        if validity is None:
            self.last["channel"] = channel
            return [], self.reject("有效频点不足，无法判定")
        comparisons = []
        group = self.accepted if self.accepted else self.pending
        for previous in group:
            deviation, channel = self._distance(candidate, previous, frequencies, names,
                                                reference, selected)
            comparisons.append((deviation, channel))
        finite = [(value, channel) for value, channel in comparisons if value is not None]
        if finite:
            value, channel = max(finite)
            self.last.update(deviation=value, channel=channel)
        tolerance = self.config.modal.smart_hit_tolerance
        if self.accepted:
            if any(value is None for value, _ in comparisons):
                return [], self.reject("有效频点不足，无法判定")
            if any(value > tolerance for value, _ in comparisons):
                return [], self.reject("FRF 不一致")
            self.accepted.append(candidate)
            self._resolve(candidate.number, "accepted", "合格")
            return [candidate], self.report()
        self.pending.append(candidate)
        pairs = []
        for first, second in combinations(self.pending, 2):
            deviation, channel = self._distance(first, second, frequencies, names, reference, selected)
            if deviation is not None and deviation <= tolerance:
                pairs.append((deviation, first.number, first, second))
        if pairs:
            _, _, first, second = min(pairs, key=lambda entry: (entry[0], entry[1]))
            self.accepted = [first, second]
            for entry in self.pending:
                chosen = entry is first or entry is second
                self._resolve(entry.number, "accepted" if chosen else "rejected",
                              "合格" if chosen else "FRF 不一致")
            self.pending.clear()
            return [first, second], self.report()
        if len(self.pending) >= 3:
            oldest = self.pending.pop(0)
            reason = "有效频点不足，无法判定" if not finite else "FRF 不一致"
            self._resolve(oldest.number, "rejected", reason)
            self.last["reason"] = reason + "；继续建立参考组"
        return [], self.report()
