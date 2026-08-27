"""Shared logical-pixel sizing for the Samba and SIDMAT desktop shells."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdaptiveUiMetrics:
    density: float
    font_scale: float
    margin: int
    initial_width: int
    initial_height: int
    minimum_width: int
    minimum_height: int


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def metrics_for_work_area(width: int, height: int) -> AdaptiveUiMetrics:
    """Return bounded UI metrics for a Qt logical work area.

    The 1920x1040 reference represents a 1920x1080 desktop with a typical
    taskbar.  Qt already converts physical pixels through Per-Monitor-V2, so
    this function deliberately uses only logical work-area dimensions.
    """

    work_width = max(1, int(width))
    work_height = max(1, int(height))
    density = _clamp(min(work_width / 1920.0, work_height / 1040.0), 0.72, 1.25)
    font_scale = _clamp(density, 0.85, 1.10)
    margin = int(_clamp(round(20.0 * density), 12, 24))
    usable_width = max(1, work_width - margin * 2)
    usable_height = max(1, work_height - margin * 2)

    minimum_width = min(usable_width, max(800, round(960 * density)))
    minimum_height = min(usable_height, max(520, round(640 * density)))
    initial_width = min(usable_width, max(minimum_width, round(1240 * density)))
    initial_height = min(usable_height, max(minimum_height, round(780 * density)))
    return AdaptiveUiMetrics(
        density=density,
        font_scale=font_scale,
        margin=margin,
        initial_width=initial_width,
        initial_height=initial_height,
        minimum_width=minimum_width,
        minimum_height=minimum_height,
    )
