"""Optional streaming software AC coupling; ADC volts remain the input unit."""
from __future__ import annotations

import math

import numpy as np

from .base import DaqBackendError


class DcRemoval:
    def __init__(self, channels, sample_rate):
        self.rate = float(sample_rate)
        if not math.isfinite(self.rate) or self.rate <= 0:
            raise DaqBackendError("自动去直流要求有效采样率")
        self.filters = {}
        for channel in channels:
            if not channel.enabled or not channel.dc_removal_enabled:
                continue
            cutoff = float(channel.dc_removal_cutoff_hz)
            if not math.isfinite(cutoff) or not 0 < cutoff < self.rate / 2:
                raise DaqBackendError(
                    f"{channel.name} 自动去直流截止频率必须大于 0 且小于 {self.rate / 2:g} Hz")
            from scipy.signal import butter, sosfilt_zi

            sos = butter(1, cutoff, btype="highpass", fs=self.rate, output="sos")
            self.filters[channel.name] = {
                "cutoff_hz": cutoff, "sos": sos, "initial": sosfilt_zi(sos), "state": None,
            }

    @property
    def enabled(self):
        return bool(self.filters)

    def description(self):
        return {
            "method": "butterworth_highpass", "order": 1,
            "initialization": "first_sample_steady_state",
            "channels": {name: {"cutoff_hz": entry["cutoff_hz"]}
                         for name, entry in self.filters.items()},
        }

    def process(self, names, data):
        values = np.asarray(data)
        if not self.enabled:
            return values
        if values.ndim != 2 or values.shape[0] != len(names):
            raise DaqBackendError("自动去直流的通道或数据形状无效")
        if not np.all(np.isfinite(values)):
            raise DaqBackendError("自动去直流收到非有限电压数据")
        from scipy.signal import sosfilt

        result = values.astype(np.float64, copy=True)
        if not values.shape[1]:
            return result
        for row, name in enumerate(names):
            entry = self.filters.get(name)
            if entry is None:
                continue
            state = entry["state"]
            if state is None:
                state = entry["initial"] * result[row, 0]
            result[row], entry["state"] = sosfilt(entry["sos"], result[row], zi=state)
        return result

    def apply_frame(self, frame):
        if self.enabled:
            frame.raw_data = frame.data
            frame.data = self.process(frame.channel_names, frame.data)
        return frame
