"""Device connection dialog; probing never writes hardware configuration."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import threading

from PySide6 import QtCore, QtWidgets

from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings, profile_preview
from python_vna.resources import resource_path


class NetDaqProbeWorker:
    def __init__(self, settings: NetDaqSettings, channels=()):
        self.settings = replace(settings, profile_path="")
        self.channels = channels
        self.stop_event = threading.Event()
        self.message = ""
        self.result = None

    def run(self):
        try:
            self.result = NetDaqBackend(self.settings).probe(self.channels, stop_event=self.stop_event)
            self.message = json.dumps(self.result, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.message = f"只读探测未完成：{exc}"


class DeviceConnectionDialog(QtWidgets.QDialog):
    def __init__(self, backend: str, settings: NetDaqSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设备连接")
        self.resize(550, 530)
        self._thread = None
        self._worker = None
        self._pending_close = False
        self._dasp_source = None
        self._probe_timer = QtCore.QTimer(self)
        self._probe_timer.setInterval(25)
        self._probe_timer.timeout.connect(self._probe_finished)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["ni", "netdaq", "simulated"])
        self.backend_combo.setCurrentText(backend)
        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItem("TCP 网络采集仪", "tcp")
        self.transport_combo.addItem("USB2404V10（0547:1003）", "usb")
        self.transport_combo.setCurrentIndex(self.transport_combo.findData(settings.transport))
        self.usb_index_edit = QtWidgets.QSpinBox()
        self.usb_index_edit.setRange(0, 15)
        self.usb_index_edit.setValue(settings.usb_index)
        self.host_edit = QtWidgets.QLineEdit(settings.host)
        self.host_edit.setPlaceholderText("设备 IP 或主机名（不会自动使用默认地址）")
        self.legacy_port_edit = QtWidgets.QSpinBox()
        self.stream_port_edit = QtWidgets.QSpinBox()
        for edit, value in ((self.legacy_port_edit, settings.legacy_port),
                            (self.stream_port_edit, settings.stream_port)):
            edit.setRange(1, 65535)
            edit.setValue(value)
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 10)
        self.timeout_edit.setValue(settings.timeout)
        self.profile_edit = QtWidgets.QLineEdit(settings.profile_path)
        browse = QtWidgets.QPushButton("选择配置")
        browse.clicked.connect(self._browse)
        profile_row = QtWidgets.QHBoxLayout()
        profile_row.addWidget(self.profile_edit, 1)
        profile_row.addWidget(browse)
        self.probe_channels_edit = QtWidgets.QLineEdit()
        self.probe_channels_edit.setPlaceholderText("可选：厂商物理通道号，如 1,2,3,4；未知时留空")
        form = QtWidgets.QFormLayout()
        form.addRow("设备类型", self.backend_combo)
        form.addRow("NetDAQ / USBDAQ 接口", self.transport_combo)
        form.addRow("USB 设备序号（从 0 开始）", self.usb_index_edit)
        form.addRow("NetDAQ 地址", self.host_edit)
        form.addRow("控制端口", self.legacy_port_edit)
        form.addRow("数据端口", self.stream_port_edit)
        form.addRow("超时（秒）", self.timeout_edit)
        form.addRow("设备配置 / DASP 参数配置", profile_row)
        form.addRow("只读查询通道", self.probe_channels_edit)
        note = QtWidgets.QLabel(
            "NetDAQ 为实验性后端。USB 可从 DASP 参数建立兼容配置，"
            "支持已确认的电压/DC 与 IEPE 选择，使用原软件声明的电压换算及传感器参数；"
            "IEPE 电流与独立电压标定仍未确认。"
            "Avg 使用主机软件触发，AO 暂不支持。")
        note.setWordWrap(True)
        self.probe_button = QtWidgets.QPushButton("只读探测")
        self.probe_button.clicked.connect(self._probe)
        copy = QtWidgets.QPushButton("复制报告")
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.report.toPlainText()))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.probe_button)
        row.addWidget(copy)
        preview = QtWidgets.QPushButton("配置预览 / 校验")
        preview.clicked.connect(self._preview_profile)
        row.addWidget(preview)
        row.addStretch()
        self.dasp_button = QtWidgets.QPushButton("从 DASP 参数建立 USB 兼容配置...")
        self.dasp_button.clicked.connect(self._create_dasp_profile)
        self.usb_preset_button = QtWidgets.QPushButton("使用随包 USB2404 配置")
        self.usb_preset_button.clicked.connect(self._use_usb_preset)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("应用")
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addLayout(row)
        layout.addWidget(self.dasp_button)
        layout.addWidget(self.usb_preset_button)
        layout.addWidget(self.report, 1)
        layout.addWidget(self.buttons)
        self.transport_combo.currentIndexChanged.connect(self._transport_changed)
        self._transport_changed()

    def _preview_profile(self):
        self.report.setPlainText(json.dumps(profile_preview(self.profile_edit.text().strip()),
                                            ensure_ascii=False, indent=2))

    def _transport_changed(self):
        usb = self.transport_combo.currentData() == "usb"
        for widget in (self.host_edit, self.legacy_port_edit, self.stream_port_edit):
            widget.setEnabled(not usb)
        self.usb_index_edit.setEnabled(usb)
        self.dasp_button.setEnabled(usb and self._thread is None)
        self.usb_preset_button.setEnabled(usb and self._thread is None)

    def _use_usb_preset(self):
        if self._thread is not None:
            return
        path = resource_path("profiles/USB2404_DASP_modes.json")
        try:
            profile = NetDaqProfile.load(path)
            if profile.transport != "usb" or not all(
                    profile.has_usb_coupling_control(i) for i in range(len(profile.channels))):
                raise ValueError("随包配置没有有效的 USB DC/IEPE 模式")
            # Keep the selected preset available after moving or replacing the installation.
            content = path.read_bytes()
            directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".python_vna")))
            directory = directory / "PythonVNA" / "devices"
            directory.mkdir(parents=True, exist_ok=True)
            cached = directory / f"USB2404_DASP_{hashlib.sha256(content).hexdigest()[:16]}.json"
            try:
                with cached.open("xb") as stream:
                    stream.write(content)
            except FileExistsError:
                pass
            if cached.read_bytes() != content:
                raise ValueError("本地预设副本已修改，请通过“选择配置”加载")
            path = cached
        except Exception as exc:
            self.report.setPlainText(f"USB2404 随包配置不可用：{exc}")
            return
        self.backend_combo.setCurrentText("netdaq")
        self.transport_combo.setCurrentIndex(self.transport_combo.findData("usb"))
        self.profile_edit.setText(str(path))
        modes = [
            f"CH{i + 1}：{'IEPE' if entry['options'][0]['coupling'] == 'bias' else 'DC'}"
            for i, entry in enumerate(profile.channels)
        ]
        self.report.setPlainText(
            f"{profile.model} / 固件 2.0\n" + "，".join(modes)
            + "\n传感器系数来自既有 DASP 参数，请核对实际传感器。"
            "\n电压按标称值换算；IEPE 电流及独立电压标定未确认。")

    def _create_dasp_profile(self):
        if self._thread is not None:
            return
        source, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 DASP 原通道参数", self.profile_edit.text(), "DASP 参数 (*.ini *.par)")
        if not source:
            return
        self._dasp_source = source
        self.backend_combo.setCurrentText("netdaq")
        self.probe_channels_edit.setText("0,1,2,3")
        self._probe()

    def _save_dasp_profile(self, source, probe):
        from python_vna.daq.dasp_profile import build_dasp_usb_profile, save_dasp_usb_profile

        try:
            data = build_dasp_usb_profile(source, probe, enable_coupling_control=True)
            self.report.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
            initial = self.profile_edit.text() or str(Path(source).parent / "USB2404_DASP.json")
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "保存 USB 兼容配置（DC/IEPE 可选；幅值尚未独立标定）", initial, "JSON (*.json)")
            if not path:
                return
            target = Path(path)
            if target.suffix.lower() != ".json":
                target = target.with_suffix(".json")
            if target.resolve() == Path(source).resolve():
                raise ValueError("设备配置不能覆盖 DASP 原参数")
            save_dasp_usb_profile(data, target)
            self.profile_edit.setText(str(target))
            self.report.setPlainText(json.dumps(profile_preview(target), ensure_ascii=False, indent=2))
        except Exception as exc:
            self.report.setPlainText(f"DASP 兼容配置未保存：{exc}")

    def settings(self):
        return NetDaqSettings(self.host_edit.text().strip(), self.legacy_port_edit.value(),
                             self.stream_port_edit.value(), self.timeout_edit.value(),
                             self.profile_edit.text().strip(), self.transport_combo.currentData(),
                             self.usb_index_edit.value())

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 NetDAQ 设备配置或 DASP 参数兼容配置", self.profile_edit.text(), "JSON (*.json)")
        if path:
            self.profile_edit.setText(path)

    def accept(self):
        if self._thread is not None:
            return
        try:
            settings = self.settings()
            if self.backend_combo.currentText() == "netdaq":
                if settings.transport == "tcp" and not settings.host:
                    raise ValueError("请填写设备 IP 或主机名")
                if settings.profile_path:
                    profile = NetDaqProfile.load(settings.profile_path)
                    if profile.transport != settings.transport:
                        raise ValueError("设备配置与所选 TCP / USB 接口不匹配")
        except Exception as exc:
            self.report.setPlainText(str(exc))
            return
        super().accept()

    def _probe(self):
        if self._thread is not None:
            return
        try:
            settings = self.settings()
            if settings.transport == "tcp" and not settings.host:
                raise ValueError("请先填写设备地址")
            text = self.probe_channels_edit.text().strip()
            channels = tuple(int(n.strip()) for n in text.split(",")) if text else ()
            if len(channels) > 64 or len(set(channels)) != len(channels) or any(not 0 <= n <= 255 for n in channels):
                raise ValueError("通道号必须是互不重复的 0..255 整数，最多 64 个")
        except Exception as exc:
            self._dasp_source = None
            self.report.setPlainText(str(exc))
            return
        self.probe_button.setEnabled(False)
        self.dasp_button.setEnabled(False)
        self.usb_preset_button.setEnabled(False)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(False)
        self.report.setPlainText("正在读取描述、固件及所选通道当前配置；不会设置采样率、量程、IEPE 或启动设备。")
        worker = NetDaqProbeWorker(settings, channels)
        # This short read-only task has no Qt objects on its worker thread.
        # Poll completion on the GUI thread instead of destroying QObjects
        # while a native QThread is still finishing its TLS cleanup.
        thread = threading.Thread(target=worker.run, name="NetDAQ read-only probe", daemon=True)
        self._thread, self._worker = thread, worker
        thread.start()
        self._probe_timer.start()

    @QtCore.Slot()
    def _probe_finished(self):
        if self._thread is None or self._thread.is_alive():
            return
        self._thread.join(0)
        self._probe_timer.stop()
        worker = self._worker
        self.report.setPlainText(worker.message)
        source, self._dasp_source = self._dasp_source, None
        self._thread = self._worker = None
        self.probe_button.setEnabled(True)
        self.dasp_button.setEnabled(self.transport_combo.currentData() == "usb")
        self.usb_preset_button.setEnabled(self.transport_combo.currentData() == "usb")
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(True)
        if self._pending_close:
            super().reject()
        elif source and worker.result is not None:
            self._save_dasp_profile(source, worker.result)

    def reject(self):
        if self._thread is not None:
            self._pending_close = True
            self._worker.stop_event.set()
            return
        super().reject()

    def closeEvent(self, event):
        if self._thread is not None:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
