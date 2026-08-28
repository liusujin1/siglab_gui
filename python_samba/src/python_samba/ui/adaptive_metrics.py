"""Shared logical-pixel sizing for the Samba and SIDMAT desktop shells."""

from __future__ import annotations

from dataclasses import dataclass


UNIFIED_FONT_SCALE = 0.92


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
    # Font density needs a steeper response than window geometry.  A 200%
    # laptop panel often exposes only ~75% of the 1080p logical workspace;
    # using density directly still leaves text visually dominant.
    font_scale = _clamp(density ** 1.5, 0.67, 1.10)
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


def metrics_for_legacy_reference(
    width: int,
    height: int,
    display_scale: float,
    *,
    design_width: int = 1840,
    design_height: int = 1240,
    max_work_width_ratio: float = 1.0,
    max_work_height_ratio: float = 1.0,
) -> AdaptiveUiMetrics:
    """Fit a legacy physical-pixel layout into a Qt logical screen.

    Under a Per-Monitor-V2 process Qt exposes logical pixels, so the reference
    geometry must first be divided by the Windows display scale and then, if
    necessary, reduced further to fit the available work area. Optional work
    area ratios prevent a legacy canvas from opening almost full-screen on a
    lower-resolution monitor.
    """

    work_width = max(1, int(width))
    work_height = max(1, int(height))
    monitor_scale = _clamp(float(display_scale), 0.75, 3.0)
    margin = int(_clamp(round(20.0 / monitor_scale), 8, 24))
    usable_width = max(1, work_width - margin * 2)
    usable_height = max(1, work_height - margin * 2)
    capped_width = usable_width * _clamp(float(max_work_width_ratio), 0.1, 1.0)
    capped_height = usable_height * _clamp(float(max_work_height_ratio), 0.1, 1.0)
    density = min(
        1.0 / monitor_scale,
        capped_width / float(design_width),
        capped_height / float(design_height),
        1.0,
    )
    density = max(0.01, density)
    font_scale = _clamp(density, 0.67, 1.0)
    initial_width = min(usable_width, max(1, round(design_width * density)))
    initial_height = min(usable_height, max(1, round(design_height * density)))
    minimum_width = min(initial_width, max(640, round(960 * density)))
    minimum_height = min(initial_height, max(440, round(640 * density)))
    return AdaptiveUiMetrics(
        density=density,
        font_scale=font_scale,
        margin=margin,
        initial_width=initial_width,
        initial_height=initial_height,
        minimum_width=minimum_width,
        minimum_height=minimum_height,
    )
