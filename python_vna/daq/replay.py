"""Cancellable offline voltage replay. Source files are always opened read-only."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time

import numpy as np

from .base import BaseDaqBackend, BackendCapability, BackendDevice, BackendFrame, DaqBackendError
from .dasp_import import dasp_voltage_scale, read_parameters, _number
from .software_trigger import SoftwareTrigger
from python_vna.models import ChannelConfig


class ReplayComplete(DaqBackendError):
    """Normal end of a finite replay, distinct from disconnect/corruption."""


class ReplaySource:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.parts = []
        self._part_samples = {}
        self.header = {}
        self.channels = []
        self.kind = ""
        self._vna_data = None
        if self.path.is_dir():
            manifest = self.path / "manifest.json"
            if manifest.is_file():
                self._manifest(manifest)
            else:
                self._dasp(sorted(self.path.glob("CH *.par")))
        elif self.path.suffix.lower() == ".json":
            self._manifest(self.path)
        elif self.path.suffix.lower() in {".par", ".sts"}:
            self._dasp([self.path.with_suffix(".par")])
        elif self.path.suffix.lower() in {".vna", ".mat"}:
            self._vna()
        elif self.path.suffix.lower() in {".bin", ".dat", ".zip"}:
            if self.path.suffix.lower() == ".bin":
                self._manifest(self.path.parent / "manifest.json", only=self.path.name)
            else:
                from python_vna.continuous_recording import read_dat_header

                self.header = read_dat_header(self.path)
                self.rate = float(self.header["sample_rate"])
                self.names = list(self.header["channel_names"])
                self.parts = [self.path]
                self.kind = "text_record"
        else:
            raise DaqBackendError("回放支持 VNA/MAT、DASP CH n.par/sts、连续记录目录或 manifest/DAT/BIN")
        if (not math.isfinite(self.rate) or self.rate <= 0 or not self.names
                or len(self.names) != len(set(self.names)) or len(self.names) > 64):
            raise DaqBackendError("回放文件采样率或通道信息无效")
        if not self.channels:
            saved = {c["name"]: c for c in self.header.get("session", {}).get("ai_channels", [])}
            self.channels = [
                ChannelConfig(**saved[name]) if name in saved else ChannelConfig(name, f"Replay/{name}")
                for name in self.names
            ]

    def _manifest(self, path, only=None):
        self.header = json.loads(path.read_text(encoding="utf-8-sig"))
        if self.header.get("format") != "python_vna_continuous_manifest":
            raise DaqBackendError("不是连续记录 manifest")
        self.rate = float(self.header["sample_rate"])
        self.names = list(self.header["channel_names"])
        self.kind = "binary_record" if self.header.get("storage_format") == "binary" else "text_record"
        for entry in self.header.get("segments", []):
            part = (path.parent / entry["path"]).resolve()
            if not part.is_relative_to(path.parent.resolve()):
                raise DaqBackendError("记录分段路径越出记录目录")
            if only is None or part.name == only:
                if not part.is_file():
                    raise DaqBackendError(f"缺少记录分段：{part.name}")
                expected = entry.get("samples")
                if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                    raise DaqBackendError(f"分段样本数无效：{part.name}")
                self._part_samples[part] = expected
                self.parts.append(part)
        if not self.parts:
            raise DaqBackendError("没有已关闭的记录分段；请先停止记录")
        if not self.header.get("completed"):
            self.header["replay_warning"] = "原记录未完整结束，只回放已关闭分段"

    def _dasp(self, paths):
        self.kind = "dasp"
        if not paths:
            raise DaqBackendError("目录内没有 CH n.par")
        self.names, self._dasp_settings = [], []
        self.header["dasp_conversion"] = {}
        for path in paths:
            section = read_parameters(path)["Setup"]
            number = int(section.get("Channel no", "1"))
            rate = _number(section, "Sample rate", section.get("fsamp"))
            if self.names and rate != self.rate:
                raise DaqBackendError("DASP 多通道声明的采样率不一致")
            self.rate = rate
            scale = dasp_voltage_scale(section)
            sts = path.with_suffix(".sts")
            if not sts.is_file() or not sts.stat().st_size or sts.stat().st_size % 4:
                raise DaqBackendError(f"DASP 原始文件缺失或截断：{sts.name}")
            name = f"ai{number - 1}"
            self.names.append(name)
            self.parts.append(sts)
            self._dasp_settings.append(scale)
            self.header["dasp_conversion"][name] = scale
            self.channels.append(ChannelConfig(name, f"Replay/{name}", label=section.get("Name", f"CH {number}")))
        sizes = [p.stat().st_size for p in self.parts]
        if len(set(sizes)) != 1:
            raise DaqBackendError("DASP 原始多通道样本数不同，不截短或补齐数据")
        self.header["sample_rate_basis"] = "file_declared_unverified"
        self.header["replay_warning"] = (
            "DASP 回放使用文件声明采样率（可能是标称/ADC速率），未验证实际输出时钟；"
            "未减零漂或应用传感器标定")

    def _vna(self):
        from python_vna.storage import load_legacy_vna

        if self.path.stat().st_size > 256 * 1024 * 1024:
            raise DaqBackendError("VNA 超过 256 MiB 回放限制；长记录请使用连续记录目录")
        saved = load_legacy_vna(self.path)
        if saved.measurement is None:
            raise DaqBackendError("VNA 中没有时域数据")
        measurement = saved.measurement
        self._vna_data = measurement.time_data.get("channels", {})
        self.names = list(self._vna_data)
        self.rate = float(measurement.sample_rate)
        self.channels = [deepcopy(c) for c in saved.config.ai_channels if c.name in self.names]
        lengths = {np.asarray(v).size for v in self._vna_data.values()}
        if len(lengths) != 1 or not next(iter(lengths), 0):
            raise DaqBackendError("VNA 时域数据为空或通道长度不一致")
        self.kind = "vna"

    def blocks(self, size):
        if self.kind == "vna":
            length = np.asarray(self._vna_data[self.names[0]]).size
            for begin in range(0, length, size):
                yield np.vstack([np.asarray(self._vna_data[n]).ravel()[begin:begin + size] for n in self.names])
        elif self.kind == "dasp":
            arrays = [np.memmap(p, dtype="<i4", mode="r") for p in self.parts]
            try:
                for begin in range(0, len(arrays[0]), size):
                    rows = []
                    for array, scale in zip(arrays, self._dasp_settings):
                        counts = np.asarray(array[begin:begin + size], dtype=float)
                        if np.any((counts < scale["adc_min"]) | (counts > scale["adc_max"])):
                            raise DaqBackendError("DASP 样本超过声明 ADC 码范围")
                        rows.append((counts - scale["zero_count"]) * scale["volts_per_count"])
                    yield np.vstack(rows)
            finally:
                for array in arrays:
                    array._mmap.close()
        elif self.kind == "binary_record":
            for part in self.parts:
                row_bytes = len(self.names) * 8
                actual_bytes = part.stat().st_size
                if (actual_bytes % row_bytes
                        or actual_bytes != self._part_samples[part] * row_bytes):
                    raise DaqBackendError(f"二进制记录分段被截断：{part.name}")
                if not part.stat().st_size:
                    continue
                array = np.memmap(part, dtype="<f8", mode="r",
                                  shape=(part.stat().st_size // row_bytes, len(self.names)))
                try:
                    for begin in range(0, len(array), size):
                        yield np.array(array[begin:begin + size].T, copy=True)
                finally:
                    array._mmap.close()
        else:
            from python_vna.continuous_recording import iter_dat_frames

            for part in self.parts:
                for header, frame in iter_dat_frames(part):
                    if frame["channel_names"] != self.names or float(header["sample_rate"]) != self.rate:
                        raise DaqBackendError("连续记录分段通道或采样率发生变化")
                    for begin in range(0, frame["data"].shape[1], size):
                        yield frame["data"][:, begin:begin + size]


class ReplayBackend(BaseDaqBackend):
    def __init__(self, path, *, realtime=True):
        self.path = str(path)
        self.source = None
        self.realtime = realtime
        self._cancel = threading.Event()
        self._external_stop = None
        self._iterator = None
        self._active = False
        self._trigger = None

    def _source(self):
        if self.source is None:
            self.source = ReplaySource(self.path)
        return self.source

    def list_devices(self):
        source = self._source()
        return [BackendDevice("Replay", f"离线回放：{source.path.name}",
                              [f"Replay/{name}" for name in source.names], [],
                              BackendCapability(supports_analog_trigger=True, supports_pretrigger=True))]

    def configure(self, session, device_name=None):
        self.stop()
        source = self._source()
        if session.acquisition.excitation.enabled:
            raise DaqBackendError("离线回放不支持 AO")
        self._names = [c.name for c in session.ai_channels if c.enabled]
        if not self._names or any(n not in source.names for n in self._names):
            raise DaqBackendError("请只启用回放文件中包含的通道")
        self._rows = [source.names.index(n) for n in self._names]
        self._size = int(session.acquisition.frame_size)
        if not 1 <= self._size <= 1048576:
            raise DaqBackendError("回放帧长必须在 1..1048576")
        if self._size * len(source.names) * 8 > 64 * 1024 * 1024:
            raise DaqBackendError("回放单块超过 64 MiB，请缩短帧长")
        self._rate = session.acquisition.sample_rate = source.rate
        session.acquisition.bandwidth_hz = source.rate / 2.56
        self._session = session
        self._cancel.clear()
        self._position = self._frame_index = 0
        self._pending = np.empty((len(self._names), 0))
        self._trigger = (SoftwareTrigger(session.acquisition, self._names, self._read_raw, self._check_stop)
                         if session.acquisition.trigger.enabled and session.acquisition.trigger.source != "immediate"
                         else None)
        self._raw_size = min(self._size, max(1, int(self._rate * .05))) if self._trigger else self._size

    def start(self):
        self._check_stop()
        if not hasattr(self, "_session"):
            raise DaqBackendError("请先配置回放")
        if self._active:
            raise DaqBackendError("回放已经开始")
        self._iterator = iter(self.source.blocks(self._raw_size))
        self._started = time.monotonic()
        self._active = True

    def _check_stop(self):
        if self._cancel.is_set() or (self._external_stop and self._external_stop.is_set()):
            raise DaqBackendError("回放已停止")

    def read_frame(self):
        return self._trigger.read_frame() if self._trigger is not None else self._read_raw()

    def _read_raw(self):
        self._check_stop()
        if not self._active:
            raise DaqBackendError("回放尚未启动")
        while self._pending.shape[1] < self._raw_size:
            try:
                block = next(self._iterator)[self._rows]
            except StopIteration:
                if not self._pending.shape[1]:
                    raise ReplayComplete("回放结束")
                break
            self._check_stop()
            self._pending = np.concatenate((self._pending, block), axis=1)
        count = min(self._raw_size, self._pending.shape[1])
        if self.realtime:
            deadline = self._started + (self._position + count) / self._rate
            while time.monotonic() < deadline:
                self._check_stop()
                if (self._trigger and self._trigger.wait_deadline is not None
                        and time.monotonic() >= self._trigger.wait_deadline):
                    raise DaqBackendError("软件触发超时")
                self._cancel.wait(min(.025, max(0, deadline - time.monotonic())))
        data = self._pending[:, :count].copy()
        self._pending = self._pending[:, count:].copy()
        result = BackendFrame(self._rate, self._names, data, np.arange(count) / self._rate, self._frame_index,
                              {**self.acquisition_provenance(), "sample_start_index": self._position})
        self._position += count
        self._frame_index += 1
        return result

    def acquisition_provenance(self):
        rate_basis = self.source.header.get("sample_rate_basis", "recorded")
        return dict(backend="replay", source_path=self.path, source_kind=self.source.kind, units="V",
                    actual_sample_rate=None if rate_basis == "file_declared_unverified" else self._rate,
                    playback_sample_rate=self._rate, physical_device=False, sample_rate_basis=rate_basis,
                    voltage_conversion=deepcopy(self.source.header.get("dasp_conversion")),
                    channel_full_scales={c.name: abs(float(c.full_scale))
                                         for c in self._session.ai_channels if c.enabled},
                    warning=self.source.header.get("replay_warning"))

    def update_trigger_level(self, level_v):
        if self._trigger is None:
            raise DaqBackendError("回放未启用软件触发")
        return self._trigger.update_level(level_v)

    def request_stop(self):
        self._cancel.set()

    def set_stop_event(self, stop_event):
        self._external_stop = stop_event

    def stop(self):
        self._cancel.set()
        self._active = False
        if self._iterator is not None:
            self._iterator.close()
            self._iterator = None

    def close(self):
        self.stop()

    def abort(self):
        self.request_stop()
