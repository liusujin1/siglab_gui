"""Streaming voltage diagnostics, with constant memory and no signal alteration."""
from __future__ import annotations

import time

import numpy as np


class AcquisitionQuality:
    def __init__(self, channels, *, backend_name=None):
        self.backend_name = backend_name
        self.ranges = {c.name: (float(c.min_value), float(c.max_value)) for c in channels if c.enabled}
        self.stats = {}
        self.processed_stats = {}
        self.rail_basis = {}
        self.started = time.monotonic()
        self.frames = self.bytes = 0

    def add(self, frame):
        self.frames += 1
        self.bytes += int(frame.data.nbytes)
        if frame.raw_data is not None:
            for name, values in zip(frame.channel_names, frame.data):
                x = np.asarray(values, dtype=float)
                finite = x[np.isfinite(x)]
                if not finite.size:
                    continue
                stat = self.processed_stats.setdefault(
                    name, dict(samples=0, mean_v=0.0, sum_squares=0.0))
                count, mean = int(finite.size), float(np.mean(finite))
                stat["mean_v"] += (mean - stat["mean_v"]) * count / (stat["samples"] + count)
                stat["samples"] += count
                stat["sum_squares"] += float(np.dot(finite, finite))
                stat["last_frame_mean_v"] = mean
                stat["last_frame_rms_v"] = float(np.sqrt(np.mean(finite * finite)))
        raw = frame.raw_data if frame.raw_data is not None else frame.data
        for name, values in zip(frame.channel_names, raw):
            reported = frame.metadata.get("channel_hardware_ranges", {}).get(name)
            if reported is not None and len(reported) == 2:
                self.ranges[name] = tuple(float(v) for v in reported)
                self.rail_basis[name] = "reported_hardware_limits"
            x = np.asarray(values, dtype=float)
            finite = x[np.isfinite(x)]
            stat = self.stats.setdefault(name, dict(samples=0, invalid_samples=0, mean_v=0.0,
                m2=0.0, minimum_v=None, maximum_v=None, rail_samples=0))
            stat["invalid_samples"] += int(x.size - finite.size)
            if not finite.size:
                continue
            count, mean = finite.size, float(np.mean(finite))
            old = stat["samples"]
            delta = mean - stat["mean_v"]
            stat["m2"] += float(np.sum((finite - mean) ** 2)) + delta**2 * old * count / (old + count)
            stat["mean_v"] += delta * count / (old + count)
            stat["samples"] += int(count)
            low, high = float(np.min(finite)), float(np.max(finite))
            stat["minimum_v"] = low if stat["minimum_v"] is None else min(low, stat["minimum_v"])
            stat["maximum_v"] = high if stat["maximum_v"] is None else max(high, stat["maximum_v"])
            limits = self.ranges.get(name)
            if limits and limits[1] > limits[0]:
                margin = (limits[1] - limits[0]) * 0.001
                stat["rail_samples"] += int(np.count_nonzero(
                    (finite <= limits[0] + margin) | (finite >= limits[1] - margin)))

    def report(self):
        elapsed = max(time.monotonic() - self.started, 1e-9)
        channels = {}
        for name, stat in self.stats.items():
            count = stat["samples"]
            ac_rms = (stat["m2"] / count) ** .5 if count else None
            channels[name] = {k: v for k, v in stat.items() if k != "m2"}
            channels[name].update(
                rms_v=float(np.hypot(stat["mean_v"], ac_rms)) if count else None,
                ac_rms_v=ac_rms, peak_to_peak_v=stat["maximum_v"] - stat["minimum_v"] if count else None,
                rail_fraction=stat["rail_samples"] / count if count else None,
                rail_limits_v=self.ranges.get(name),
                rail_basis=self.rail_basis.get(name, "configured_input_limits"),
            )
            processed = self.processed_stats.get(name)
            if processed:
                channels[name]["processed_voltage"] = {
                    "samples": processed["samples"], "mean_v": processed["mean_v"],
                    "rms_v": (processed["sum_squares"] / processed["samples"]) ** .5,
                    "last_frame_mean_v": processed["last_frame_mean_v"],
                    "last_frame_rms_v": processed["last_frame_rms_v"],
                }
        return dict(format="python_vna_acquisition_quality", backend=self.backend_name, frames=self.frames,
                    elapsed_seconds=elapsed, bytes_read=self.bytes, bytes_per_second=self.bytes / elapsed,
                    channels=channels, note="主统计为软件去直流前的电压；processed_voltage 为处理后电压。"
                    "连续高通不保证每帧均值为零；吞吐率按交付帧计算，接近量程不等于传感器断线。未自动标定。")
