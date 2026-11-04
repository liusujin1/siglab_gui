"""Small acquisition-only dialogs; no new GUI or analysis dependencies."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from python_vna.daq.dasp_import import load_dasp_channels


class DaspImportDialog(QtWidgets.QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 DASP 通道参数（预览）")
        self.resize(720, 390)
        errors = []
        self.channels = load_dasp_channels(path, errors=errors)
        self.table = QtWidgets.QTableWidget(len(self.channels), 6)
        self.table.setHorizontalHeaderLabels(["导入", "通道", "标签", "原灵敏度", "EU/Volt", "单位"])
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
            self.table.item(row, 4).setToolTip(
                f"灵敏度：{channel.source_sensitivity:g} {channel.voltage_unit}/({channel.unit})\n"
                f"Gain={channel.gain:g}；External gain={channel.external_gain:g}\n"
                "EU/Volt = 1 / (灵敏度 × 电压单位换算 × Gain × External gain)\n"
                "仅用于工程量折算，不写入硬件增益。")
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        note = QtWidgets.QLabel(
            "按原灵敏度换算为 EU/Volt，Gain 和 External gain 在工程量换算中各除一次。\n"
            "仅导入勾选通道的标签、启用状态、单位与灵敏度；保留当前零漂、量程、耦合和 IEPE。")
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
        return [channel for row, channel in enumerate(self.channels)
                if self.table.item(row, 0).checkState() == QtCore.Qt.Checked]


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
