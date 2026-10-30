"""Device connection dialog; probing never writes hardware configuration."""
from __future__ import annotations

from dataclasses import replace
import json
import threading

from PySide6 import QtCore, QtWidgets

from python_vna.daq.netdaq import NetDaqBackend, NetDaqProfile, NetDaqSettings


class NetDaqProbeWorker:
    def __init__(self, settings: NetDaqSettings, channels=()):
        self.settings = replace(settings, profile_path="")
        self.channels = channels
        self.stop_event = threading.Event()
        self.message = ""

    def run(self):
        try:
            report = NetDaqBackend(self.settings).probe(self.channels, stop_event=self.stop_event)
            self.message = json.dumps(report, ensure_ascii=False, indent=2)
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
        form.addRow("已验证设备配置", profile_row)
        form.addRow("只读查询通道", self.probe_channels_edit)
        note = QtWidgets.QLabel(
            "NetDAQ 为实验性后端。没有已验证的量程、IEPE、通道顺序及电压标定配置时，"
            "只能只读探测，不能开始采集。触发和 AO 暂不支持。")
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
        row.addStretch()
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
        layout.addWidget(self.report, 1)
        layout.addWidget(self.buttons)
        self.transport_combo.currentIndexChanged.connect(self._transport_changed)
        self._transport_changed()

    def _transport_changed(self):
        usb = self.transport_combo.currentData() == "usb"
        for widget in (self.host_edit, self.legacy_port_edit, self.stream_port_edit):
            widget.setEnabled(not usb)
        self.usb_index_edit.setEnabled(usb)

    def settings(self):
        return NetDaqSettings(self.host_edit.text().strip(), self.legacy_port_edit.value(),
                             self.stream_port_edit.value(), self.timeout_edit.value(),
                             self.profile_edit.text().strip(), self.transport_combo.currentData(),
                             self.usb_index_edit.value())

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择已验证的 NetDAQ 设备配置", self.profile_edit.text(), "JSON (*.json)")
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
            self.report.setPlainText(str(exc))
            return
        self.probe_button.setEnabled(False)
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
        self.report.setPlainText(self._worker.message)
        self._thread = self._worker = None
        self.probe_button.setEnabled(True)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(True)
        if self._pending_close:
            super().reject()

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
