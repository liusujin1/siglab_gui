"""Bounded host-side absolute-window trigger; never sends hardware commands."""
from __future__ import annotations

import math
import time

import numpy as np

from .base import BackendFrame, DaqBackendError


class SoftwareTriggerTimeout(DaqBackendError):
    """A healthy stream did not cross the threshold during one waiting interval."""

    def __init__(self, source, level_v, timeout_seconds):
        self.source = source
        self.level_v = float(level_v)
        self.timeout_seconds = float(timeout_seconds)
        super().__init__(f"软件触发超时（{self.timeout_seconds:g} 秒）")

    @property
    def waiting_message(self):
        channel = (f"Ch{int(self.source[2:]) + 1}"
                   if self.source.startswith("ai") and self.source[2:].isdigit()
                   else self.source)
        return (f"等待触发：{channel}，|电压| ≥ {self.level_v:.6g} V；"
                "平均次数不增加，可点停止取消")


class SoftwareTrigger:
    def __init__(self, acquisition, channel_names, read_block, check_stop):
        trigger = acquisition.trigger
        self.size = int(acquisition.frame_size)
        self.rate = float(acquisition.sample_rate)
        self.names = list(channel_names)
        source = trigger.source.rsplit("/", 1)[-1]
        if source not in self.names:
            raise DaqBackendError("软件触发源必须是已启用的采集通道")
        self.source = self.names.index(source)
        self.level = self._level(trigger.level)
        self.timeout = float(trigger.timeout_seconds)
        self.delay = int(round(self.size * trigger.pretrigger_samples / 100))
        if (not math.isfinite(self.timeout) or self.timeout <= 0
                or not -self.size < self.delay <= self.size):
            raise DaqBackendError("软件触发要求正超时和 (-100%, 100%] 的 Delay")
        mode = trigger.mode.strip().lower()
        if mode not in {"every frame", "manual arm", "first frame", "1st frame", "1st-manual arm"}:
            raise DaqBackendError(f"不支持的软件触发模式：{trigger.mode}")
        self.first_only = mode in {"first frame", "1st frame", "1st-manual arm"}
        self.read_block = read_block
        self.check_stop = check_stop
        self.data = np.empty((len(self.names), 0))
        self.raw_data = None
        self.start = self.scan = self.index = 0
        self.previous_inside = False
        self.last_metadata = {}
        self.wait_deadline = None

    @staticmethod
    def _level(value):
        level = abs(float(value))
        if not math.isfinite(level) or level <= 0:
            raise DaqBackendError("软件触发绝对阈值必须大于 0 V")
        return level

    def update_level(self, value):
        self.level = self._level(value)
        return self.level

    def timeout_error(self):
        self.wait_deadline = None
        return SoftwareTriggerTimeout(self.names[self.source], self.level, self.timeout)

    def _append(self):
        self.check_stop()
        frame = self.read_block()
        if (frame.channel_names != self.names or frame.sample_rate != self.rate
                or frame.data.ndim != 2 or frame.data.shape[0] != len(self.names)
                or not 0 < frame.data.shape[1] <= self.size):
            raise DaqBackendError("软件触发流的通道、采样率或块长度发生改变")
        if frame.raw_data is not None:
            if self.raw_data is None:
                self.raw_data = frame.raw_data.copy()
            else:
                self.raw_data = np.concatenate((self.raw_data, frame.raw_data), axis=1)
        self.data = np.concatenate((self.data, frame.data), axis=1)
        self.last_metadata = dict(frame.metadata)

    def _discard_before(self, absolute):
        count = min(self.data.shape[1], max(0, absolute - self.start))
        self.data = self.data[:, count:].copy()
        if self.raw_data is not None:
            self.raw_data = self.raw_data[:, count:].copy()
        self.start += count

    def read_frame(self):
        self.check_stop()
        deadline = time.monotonic() + self.timeout
        event = None
        pre = max(0, -self.delay)
        free = self.first_only and self.index > 0
        self.wait_deadline = None if free else deadline
        while event is None and not free:
            self.check_stop()
            if time.monotonic() >= deadline:
                raise self.timeout_error()
            offset = self.scan - self.start
            signal = self.data[self.source, offset:]
            if signal.size:
                inside = np.isfinite(signal) & (np.abs(signal) < self.level)
                previous = np.r_[self.previous_inside, inside[:-1]]
                hits = np.flatnonzero(previous & ~inside & np.isfinite(signal))
                hits = hits[self.scan + hits >= pre]
                if hits.size:
                    event = self.scan + int(hits[0])
                    break
                self.previous_inside = bool(inside[-1])
                self.scan += signal.size
                self._discard_before(self.scan - pre)
            self._append()
        self.wait_deadline = None
        begin = self.start if free else event + self.delay
        end = begin + self.size
        while self.start + self.data.shape[1] < end:
            self._append()
        self.check_stop()
        offset = begin - self.start
        data = self.data[:, offset:offset + self.size].copy()
        raw = self.raw_data[:, offset:offset + self.size].copy() if self.raw_data is not None else None
        self.previous_inside = bool(np.isfinite(data[self.source, -1])
                                    and abs(data[self.source, -1]) < self.level)
        # Retain prehistory for an event immediately after this record. It may
        # legitimately overlap the tail of the previous triggered window.
        self._discard_before(end if self.first_only else end - pre)
        self.scan = end
        metadata = {
            **self.last_metadata, "software_trigger": not free,
            "trigger_sample_index": event, "trigger_delay_samples": self.delay if not free else 0,
            "sample_start_index": begin,
        }
        # A raw block's timestamp describes its own start, not this sliced frame.
        raw_start = self.last_metadata.get("sample_start_index")
        raw_time = self.last_metadata.get("frame_start_unix_ns")
        if raw_start is not None and raw_time is not None:
            metadata["frame_start_unix_ns"] = int(raw_time + round((begin - raw_start) * 1e9 / self.rate))
        else:
            metadata.pop("frame_start_unix_ns", None)
        result = BackendFrame(self.rate, self.names, data, np.arange(self.size) / self.rate,
                              self.index, metadata, raw_data=raw)
        self.index += 1
        return result
