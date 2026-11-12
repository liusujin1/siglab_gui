"""Small acquisition-only dialogs; no new GUI or analysis dependencies."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
import re

from PySide6 import QtCore, QtWidgets

from python_vna.daq.dasp_import import load_dasp_channels
from python_vna.models import DEFAULT_DC_REMOVAL_CUTOFF_HZ


class DaspImportDialog(QtWidgets.QDialog):
    def __init__(self, path, parent=None, *, channel_names=None):
        super().__init__(parent)
        self.setWindowTitle("导入 DASP 通道参数（预览）")
        self.resize(720, 390)
        errors = []
        self.channels = load_dasp_channels(path, errors=errors)
        if channel_names is None:
            targets = list(range(max(4, max(c.index for c in self.channels) + 1)))
        else:
            targets = sorted({int(name[2:]) for name in channel_names
                              if re.fullmatch(r"ai\d+", name)})
        self.table = QtWidgets.QTableWidget(len(self.channels), 7)
        self.table.setHorizontalHeaderLabels(
            ["导入", "源通道", "标签 / 传感器", "原灵敏度", "EU/Volt", "单位", "目标通道"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, channel in enumerate(self.channels):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Checked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate((f"CH {channel.index + 1}", channel.label,
                                           f"{channel.source_sensitivity:g}", f"{channel.eu_per_volt:.10g}",
                                           channel.unit), 1):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
            if channel.transducer:
                self.table.item(row, 2).setText(f"{channel.label}\n{channel.transducer}")
            destination = QtWidgets.QComboBox()
            for index in targets:
                destination.addItem(f"CH {index + 1}", index)
            selected = destination.findData(channel.index)
            if selected >= 0:
                destination.setCurrentIndex(selected)
            self.table.setCellWidget(row, 6, destination)
            self.table.item(row, 4).setToolTip(
                f"灵敏度：{channel.source_sensitivity:g} {channel.voltage_unit}/({channel.unit})\n"
                f"Gain={channel.gain:g}；External gain={channel.external_gain:g}\n"
                "EU/Volt = 1 / (灵敏度 × 电压单位换算 × Gain × External gain)\n"
                "仅用于工程量折算，不写入硬件增益。")
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeRowsToContents()
        note = QtWidgets.QLabel(
            "按原灵敏度换算为 EU/Volt，Gain 和 External gain 在工程量换算中各除一次。\n"
            "可按传感器选择导入目标通道；多个源参数不能同时导入同一目标。\n"
            "仅导入标签、启用状态、单位与灵敏度；保留当前零漂、量程、耦合和 IEPE。")
        note.setWordWrap(True)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("应用选中")
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(str(path)))
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        if errors:
            warnings = QtWidgets.QPlainTextEdit("以下通道未导入：\n" + "\n".join(errors))
            warnings.setReadOnly(True)
            warnings.setMaximumHeight(90)
            layout.addWidget(warnings)
        layout.addWidget(self.buttons)

    def selected_channels(self):
        selected = []
        targets = set()
        for row, channel in enumerate(self.channels):
            if self.table.item(row, 0).checkState() != QtCore.Qt.Checked:
                continue
            index = self.table.cellWidget(row, 6).currentData()
            if index is None:
                raise ValueError("没有可导入的目标通道")
            if index in targets:
                raise ValueError(f"多个源参数选择了同一个目标 CH {index + 1}，请只保留一个")
            targets.add(index)
            selected.append(replace(channel, index=index))
        return selected

    def accept(self):
        try:
            self.selected_channels()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "请检查目标通道", str(exc))
            return
        super().accept()


class QualityReportDialog(QtWidgets.QDialog):
    def __init__(self, report, directory, parent=None):
        super().__init__(parent)
        self.setWindowTitle("采集质量报告（电压统计）")
        self.resize(640, 460)
        self._directory = Path(directory)
        self.report_text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        text = QtWidgets.QPlainTextEdit(self.report_text)
        text.setReadOnly(True)
        export = QtWidgets.QPushButton("导出 JSON 报告")
        export.clicked.connect(self._export)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("DC 均值 / RMS / AC RMS / 峰峰值 / 接近量程比例 / 吞吐率"))
        layout.addWidget(text)
        layout.addWidget(export)

    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出采集质量报告", str(self._directory / "acquisition_quality.json"), "JSON (*.json)")
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".json")
        try:
            target.write_text(self.report_text, encoding="utf-8-sig")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "导出失败", str(exc))
            return
        self._directory = target.parent


class DcRemovalDialog(QtWidgets.QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自动去直流（软件 AC 耦合）")
        self.resize(590, 330)
        self.channels = [replace(channel) for channel in session.ai_channels]
        self.rate = session.acquisition.sample_rate
        self.table = QtWidgets.QTableWidget(len(self.channels), 4)
        self.table.setHorizontalHeaderLabels(["通道", "标签", "启用", "截止频率 (Hz)"])
        self.table.setStyleSheet(
            "QTableWidget::indicator { width: 16px; height: 16px; }"
            "QTableWidget::indicator:unchecked { border: 1px solid #7d98a6;"
            " border-radius: 3px; background: transparent; }")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, channel in enumerate(self.channels):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(channel.name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(channel.label))
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Checked if channel.dc_removal_enabled else QtCore.Qt.Unchecked)
            self.table.setItem(row, 2, check)
            cutoff = QtWidgets.QDoubleSpinBox()
            cutoff.setRange(.001, max(.001, self.rate * .49))
            cutoff.setDecimals(3)
            cutoff.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            cutoff.setValue(channel.dc_removal_cutoff_hz)
            cutoff.setEnabled(channel.dc_removal_enabled)
            self.table.setCellWidget(row, 3, cutoff)
        self.table.itemChanged.connect(self._toggle_cutoff)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        note = QtWidgets.QLabel(
            "一阶高通，状态跨帧保留；首次用首个采样值初始化，不丢帧、不逐帧减均值。\n"
            f"默认 {DEFAULT_DC_REMOVAL_CUTOFF_HZ:g} Hz；截止频率附近会衰减和移相，低频振动请按关注频段选择。\n"
            f"此默认值对偏置阶跃约需 {math.log(100) / (2 * math.pi * DEFAULT_DC_REMOVAL_CUTOFF_HZ):.2f} "
            "秒衰减至 1%；启动时可能偏离零轴，不保证每帧均值为零。\n"
            "采集质量报告中可比较原始均值与 processed_voltage（处理后电压）均值。\n"
            "触发、显示、分析用校正信号；连续记录保存原始电压，回放按保存参数处理。\n"
            "VNA 保存当前处理后的时域；普通 Analysis 直接打开连续记录仍显示原始数据。")
        note.setWordWrap(True)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("应用")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _toggle_cutoff(self, item):
        if item.column() == 2:
            self.table.cellWidget(item.row(), 3).setEnabled(item.checkState() == QtCore.Qt.Checked)

    def accept(self):
        from python_vna.daq.dc_removal import DcRemoval
        from python_vna.daq.base import DaqBackendError

        for row, channel in enumerate(self.channels):
            channel.dc_removal_enabled = self.table.item(row, 2).checkState() == QtCore.Qt.Checked
            cutoff = self.table.cellWidget(row, 3)
            cutoff.interpretText()
            channel.dc_removal_cutoff_hz = cutoff.value()
        try:
            DcRemoval(self.channels, self.rate)
        except (DaqBackendError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "配置无效", str(exc))
            return
        super().accept()
