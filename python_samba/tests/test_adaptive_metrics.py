from python_samba.ui.adaptive_metrics import metrics_for_work_area


def test_reference_1080p_work_area_uses_reference_size():
    metrics = metrics_for_work_area(1920, 1040)
    assert (metrics.initial_width, metrics.initial_height) == (1240, 780)
    assert (metrics.minimum_width, metrics.minimum_height) == (960, 640)
    assert metrics.density == 1.0
    assert metrics.font_scale == 1.0


def test_local_2880x1800_at_200_percent_is_compact():
    # The local panel is exposed by Qt as a 1440x852 logical work area.
    metrics = metrics_for_work_area(1440, 852)
    assert (metrics.initial_width, metrics.initial_height) == (930, 585)
    assert (metrics.minimum_width, metrics.minimum_height) == (800, 520)
    assert metrics.font_scale == 0.67


def test_metrics_grow_monotonically_and_remain_bounded():
    work_areas = [(1366, 728), (1440, 852), (1920, 1040), (2560, 1400), (3840, 2120)]
    metrics = [metrics_for_work_area(*size) for size in work_areas]
    assert [item.initial_width for item in metrics] == sorted(item.initial_width for item in metrics)
    assert [item.initial_height for item in metrics] == sorted(item.initial_height for item in metrics)
    for (width, height), item in zip(work_areas, metrics):
        assert 0 < item.minimum_width <= item.initial_width <= width
        assert 0 < item.minimum_height <= item.initial_height <= height
        assert 0.72 <= item.density <= 1.25
        assert 0.67 <= item.font_scale <= 1.10
