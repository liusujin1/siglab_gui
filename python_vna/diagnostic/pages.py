from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import csv
import math

import numpy as np

from python_vna.analysis_algorithms import compute_welch_psd
from python_vna.analysis_data import AnalysisDataset, load_analysis_path
from python_vna.diagnostic.data import (
    CurvePair,
    TraceAnalysisFile,
    VibrationAnalysisFile,
    curve_pairs_from_table,
    load_trace_analysis_file,
    load_vibration_analysis_file,
)
from python_vna.optional import require
from python_vna.ui.legend_placement import place_legend_away_from_curves

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
pg = require("pyqtgraph", "python -m pip install -e .[gui]")


TRACE_COLORS = [
    "#1f77b4",
    "#e4572e",
    "#2e8b57",
    "#f2a900",
    "#7b61ff",
    "#00a6a6",
    "#d7263d",
    "#5c677d",
]


def configure_control_panel(widget: QtWidgets.QWidget) -> None:
    widget.setObjectName("diagnosticControlPanel")


def set_button_role(button: QtWidgets.QPushButton, role: str) -> None:
    button.setProperty("role", role)


class DiagnosticPage(QtWidgets.QWidget):
    statusChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_directory = Path.cwd()
        self._plot_curves: dict[pg.PlotWidget, dict[str, tuple[np.ndarray, np.ndarray]]] = {}

    def apply_theme(self, theme: dict[str, object]) -> None:
        for plot in self.findChildren(pg.PlotWidget):
            apply_plot_theme(plot, theme)

    def _show_status(self, text: str) -> None:
        self.statusChanged.emit(str(text))

    def _remember_paths(self, paths: list[Path]) -> None:
        if paths:
            self._last_directory = paths[-1].parent

    def _plot_curves_on_widget(
        self,
        plot: pg.PlotWidget,
        curves: list[CurvePair],
        *,
        title: str,
        x_label: str,
        y_label: str,
        log_x: bool = False,
        log_y: bool = False,
    ) -> int:
        plot.clear()
        legend = plot.addLegend(offset=(3, 2))
        if legend is not None:
            legend.clear()
        plot.setTitle(title)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", y_label)
        plot.setLogMode(x=log_x, y=log_y)
        saved: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        plotted = 0
        for index, curve in enumerate(curves):
            x, y = finite_xy(curve.x, curve.y, positive_x=log_x, positive_y=log_y)
            if x.size == 0:
                continue
            color = TRACE_COLORS[index % len(TRACE_COLORS)]
            plot.plot(x, y, pen=pg.mkPen(color, width=1.5), name=curve.label)
            saved[curve.label] = (x, y)
            plotted += 1
        self._plot_curves[plot] = saved
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.enableAutoRange()
        if saved:
            place_legend_away_from_curves(plot, saved)
        return plotted

    def export_plot_csv(self, plot: pg.PlotWidget, path: str | Path) -> Path:
        curves = self._plot_curves.get(plot, {})
        if not curves:
            raise ValueError("没有可导出的曲线。")
        destination = Path(path)
        max_rows = max(len(x) for x, _y in curves.values())
        headers: list[str] = []
        for label in curves:
            headers.extend([f"{label}_X", f"{label}_Y"])
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row_index in range(max_rows):
                row: list[float | str] = []
                for x, y in curves.values():
                    if row_index < len(x) and row_index < len(y):
                        row.extend([float(x[row_index]), float(y[row_index])])
                    else:
                        row.extend(["", ""])
                writer.writerow(row)
        return destination


class VibrationAnalysisPage(DiagnosticPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[VibrationAnalysisFile] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(300)
        controls.setMaximumWidth(360)
        controls_layout = QtWidgets.QVBoxLayout(controls)

        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.delete_button = QtWidgets.QPushButton("删除")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.clear_button, "secondary")
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.clear_button)
        controls_layout.addLayout(button_row)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        controls_layout.addWidget(QtWidgets.QLabel("已加载文件"))
        controls_layout.addWidget(self.file_list, 1)

        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItems(["叠加", "子图"])
        self.frequency_pair_list = QtWidgets.QListWidget()
        self.frequency_pair_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.log_group_combo = QtWidgets.QComboBox()
        self.log_channel_list = QtWidgets.QListWidget()
        self.log_channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.demean_check = QtWidgets.QCheckBox("去均值")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")

        controls_layout.addWidget(QtWidgets.QLabel("绘图模式"))
        controls_layout.addWidget(self.plot_mode_combo)
        controls_layout.addWidget(QtWidgets.QLabel("频响曲线"))
        controls_layout.addWidget(self.frequency_pair_list, 1)
        controls_layout.addWidget(QtWidgets.QLabel("日志分组"))
        controls_layout.addWidget(self.log_group_combo)
        controls_layout.addWidget(QtWidgets.QLabel("日志通道"))
        controls_layout.addWidget(self.log_channel_list, 1)
        controls_layout.addWidget(self.demean_check)
        controls_layout.addWidget(self.plot_button)
        controls_layout.addWidget(self.export_button)

        self.tabs = QtWidgets.QTabWidget()
        self.frequency_plot = pg.PlotWidget(title="频率响应")
        self.log_plot = pg.PlotWidget(title="日志 / 传感器")
        self.tabs.addTab(self.frequency_plot, "频响分析")
        self.tabs.addTab(self.log_plot, "日志 / 传感器")

        layout.addWidget(controls)
        layout.addWidget(self.tabs, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.file_list.currentRowChanged.connect(lambda _row: self._refresh_controls())
        self.log_group_combo.currentIndexChanged.connect(lambda _index: self._refresh_log_channels())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载上位机数据文件",
            str(self._last_directory),
            "数据文件 (*.dat *.txt *.csv);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        for path in paths:
            try:
                loaded_file = load_vibration_analysis_file(path)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self.files.append(loaded_file)
            self.file_list.addItem(loaded_file.table.name)
            loaded += 1
        self._remember_paths(paths)
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh_controls()
        self._show_status(f"已加载上位机数据文件：{loaded} 个")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.frequency_pair_list.clear()
        self.log_group_combo.clear()
        self.log_channel_list.clear()
        self.frequency_plot.clear()
        self.log_plot.clear()
        self._plot_curves.clear()
        self._show_status("上位机数据分析已清空")

    def current_file(self) -> VibrationAnalysisFile | None:
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self.files):
            return None
        return self.files[row]

    def plot_current(self) -> None:
        current = self.current_file()
        if current is None:
            self._show_status("未选择上位机数据文件")
            return
        if self.tabs.currentIndex() == 0:
            curves = self._selected_frequency_pairs(current)
            plotted = self._plot_curves_on_widget(
                self.frequency_plot,
                curves,
                title=f"频率响应 - {current.table.name}",
                x_label="频率 (Hz)",
                y_label="幅值",
                log_x=True,
                log_y=False,
            )
        else:
            curves = self._selected_log_curves(current)
            plotted = self._plot_curves_on_widget(
                self.log_plot,
                curves,
                title=f"日志 / 传感器 - {current.table.name}",
                x_label="样本 / 时间",
                y_label="数值",
            )
        self._show_status(f"已绘制上位机数据曲线：{plotted} 条")

    def _refresh_controls(self) -> None:
        current = self.current_file()
        self.frequency_pair_list.clear()
        self.log_group_combo.clear()
        self.log_channel_list.clear()
        if current is None:
            return
        for pair in current.frequency_pairs:
            item = QtWidgets.QListWidgetItem(pair.label)
            item.setSelected(True)
            self.frequency_pair_list.addItem(item)
        for group in current.log_groups:
            self.log_group_combo.addItem(group)
        self._refresh_log_channels()

    def _refresh_log_channels(self) -> None:
        current = self.current_file()
        self.log_channel_list.clear()
        if current is None:
            return
        group = self.log_group_combo.currentText()
        indices = current.log_groups.get(group, [])
        for index in indices:
            item = QtWidgets.QListWidgetItem(current.table.headers[index])
            item.setData(QtCore.Qt.UserRole, index)
            item.setSelected(True)
            self.log_channel_list.addItem(item)

    def _selected_frequency_pairs(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected = {item.text() for item in self.frequency_pair_list.selectedItems()}
        pairs = [pair for pair in current.frequency_pairs if not selected or pair.label in selected]
        return pairs or current.frequency_pairs[:1]

    def _selected_log_curves(self, current: VibrationAnalysisFile) -> list[CurvePair]:
        selected_indices = [
            int(item.data(QtCore.Qt.UserRole))
            for item in self.log_channel_list.selectedItems()
            if item.data(QtCore.Qt.UserRole) is not None
        ]
        if not selected_indices and self.log_channel_list.count():
            selected_indices = [int(self.log_channel_list.item(0).data(QtCore.Qt.UserRole))]
        x = np.arange(current.table.row_count, dtype=float)
        curves: list[CurvePair] = []
        for index in selected_indices:
            y = np.asarray(current.table.data[:, index], dtype=float)
            if self.demean_check.isChecked():
                y = y - np.nanmean(y)
            curves.append(CurvePair(current.table.headers[index], x, y, "样本", current.table.headers[index]))
        return curves

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
                self.file_list.takeItem(row)
        self._refresh_controls()
        self._show_status(f"已删除上位机数据文件：{len(rows)} 个")

    def _export_active(self) -> None:
        plot = self.frequency_plot if self.tabs.currentIndex() == 0 else self.log_plot
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前上位机数据图",
            str(self._last_directory / "vibration_plot.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_plot_csv(plot, path)
        self._show_status(f"已导出 {Path(path).name}")


class TraceAnalysisPage(DiagnosticPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[TraceAnalysisFile] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(300)
        controls.setMaximumWidth(360)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("加载文件")
        self.delete_button = QtWidgets.QPushButton("删除")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.clear_button, "secondary")
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.clear_button)
        controls_layout.addLayout(button_row)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        controls_layout.addWidget(QtWidgets.QLabel("已加载测试文件"))
        controls_layout.addWidget(self.file_list, 1)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.x_axis_combo.addItem("样本序号", "sample")
        self.x_axis_combo.addItem("时间 (s)", "time")
        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItems(["叠加", "子图"])
        self.range_start = QtWidgets.QSpinBox()
        self.range_start.setRange(1, 2_000_000_000)
        self.range_start.setValue(1)
        self.range_end = QtWidgets.QSpinBox()
        self.range_end.setRange(1, 2_000_000_000)
        self.range_end.setValue(1)
        self.channel_list = QtWidgets.QListWidget()
        self.channel_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.demean_check = QtWidgets.QCheckBox("去均值")
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.export_button = QtWidgets.QPushButton("导出当前数据")
        set_button_role(self.plot_button, "primary")
        set_button_role(self.export_button, "secondary")

        self.eu_table = QtWidgets.QTableWidget(0, 3)
        self.eu_table.setHorizontalHeaderLabels(["通道", "工程系数", "启用"])
        self.eu_table.horizontalHeader().setStretchLastSection(True)
        self.eu_table.setMaximumHeight(110)

        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(self.range_start)
        range_row.addWidget(QtWidgets.QLabel("至"))
        range_row.addWidget(self.range_end)

        controls_layout.addWidget(QtWidgets.QLabel("X 轴"))
        controls_layout.addWidget(self.x_axis_combo)
        controls_layout.addWidget(QtWidgets.QLabel("绘图模式"))
        controls_layout.addWidget(self.plot_mode_combo)
        controls_layout.addWidget(QtWidgets.QLabel("范围"))
        controls_layout.addLayout(range_row)
        controls_layout.addWidget(QtWidgets.QLabel("通道"))
        controls_layout.addWidget(self.channel_list, 1)
        controls_layout.addWidget(QtWidgets.QLabel("工程单位配置"))
        controls_layout.addWidget(self.eu_table)
        controls_layout.addWidget(self.demean_check)
        controls_layout.addWidget(self.plot_button)
        controls_layout.addWidget(self.export_button)

        self.tabs = QtWidgets.QTabWidget()
        self.ide_time_plot = pg.PlotWidget(title="IDE 时域")
        self.ide_psd_plot = pg.PlotWidget(title="IDE PSD")
        self.hac_plot = pg.PlotWidget(title="HAC 时域")
        self.tabs.addTab(self.ide_time_plot, "IDE 时域")
        self.tabs.addTab(self.ide_psd_plot, "IDE PSD")
        self.tabs.addTab(self.hac_plot, "HAC 时域")

        layout.addWidget(controls)
        layout.addWidget(self.tabs, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.file_list.currentRowChanged.connect(lambda _row: self._refresh_controls())
        self.plot_button.clicked.connect(self.plot_current)
        self.export_button.clicked.connect(self._export_active)

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载减振器软件测试数据",
            str(self._last_directory),
            "测试数据文件 (*.txt *.csv);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        for path in paths:
            try:
                loaded_file = load_trace_analysis_file(path)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self.files.append(loaded_file)
            self.file_list.addItem(f"{loaded_file.table.name} ({loaded_file.trace_kind})")
            loaded += 1
        self._remember_paths(paths)
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._refresh_controls()
        self._show_status(f"已加载测试文件：{loaded} 个")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.channel_list.clear()
        self.eu_table.setRowCount(0)
        for plot in (self.ide_time_plot, self.ide_psd_plot, self.hac_plot):
            plot.clear()
        self._plot_curves.clear()
        self._show_status("减振器软件测试数据分析已清空")

    def current_file(self) -> TraceAnalysisFile | None:
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self.files):
            return None
        return self.files[row]

    def plot_current(self) -> None:
        current = self.current_file()
        if current is None:
            self._show_status("未选择测试文件")
            return
        curves = self._selected_time_curves(current)
        if current.trace_kind == "ide_trace":
            self.tabs.setTabEnabled(0, True)
            self.tabs.setTabEnabled(1, True)
            plotted = self._plot_curves_on_widget(
                self.ide_time_plot,
                curves,
                title=f"IDE 时域 - {current.table.name}",
                x_label=self.x_axis_combo.currentText(),
                y_label="工程值",
            )
            psd_curves = self._psd_curves(current, curves)
            self._plot_curves_on_widget(
                self.ide_psd_plot,
                psd_curves,
                title=f"IDE PSD - {current.table.name}",
                x_label="频率 (Hz)",
                y_label="PSD",
                log_x=True,
                log_y=True,
            )
        else:
            self.tabs.setCurrentWidget(self.hac_plot)
            plotted = self._plot_curves_on_widget(
                self.hac_plot,
                curves,
                title=f"HAC 时域 - {current.table.name}",
                x_label=self.x_axis_combo.currentText(),
                y_label="工程值",
            )
        self._show_status(f"已绘制测试通道：{plotted} 个")

    def _refresh_controls(self) -> None:
        current = self.current_file()
        self.channel_list.clear()
        self.eu_table.setRowCount(0)
        if current is None:
            return
        count = max(1, current.table.row_count)
        self.range_start.setRange(1, count)
        self.range_end.setRange(1, count)
        self.range_start.setValue(1)
        self.range_end.setValue(count)
        for row, name in enumerate(current.channels):
            item = QtWidgets.QListWidgetItem(name)
            item.setSelected(True)
            self.channel_list.addItem(item)
            self.eu_table.insertRow(row)
            self.eu_table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.eu_table.setItem(row, 1, QtWidgets.QTableWidgetItem("1.0"))
            enabled = QtWidgets.QTableWidgetItem()
            enabled.setFlags(enabled.flags() | QtCore.Qt.ItemIsUserCheckable)
            enabled.setCheckState(QtCore.Qt.Checked)
            self.eu_table.setItem(row, 2, enabled)

    def _selected_time_curves(self, current: TraceAnalysisFile) -> list[CurvePair]:
        names = [item.text() for item in self.channel_list.selectedItems()]
        if not names:
            names = list(current.channels)[:1]
        start = max(0, int(self.range_start.value()) - 1)
        end = max(start + 1, int(self.range_end.value()))
        x_full = current.time_s if self.x_axis_combo.currentData() == "time" else np.arange(current.table.row_count, dtype=float)
        curves: list[CurvePair] = []
        for name in names:
            y = np.asarray(current.channels.get(name, np.array([], dtype=float)), dtype=float)
            x = np.asarray(x_full[: y.size], dtype=float)
            x = x[start:end]
            y = y[start:end]
            scale = self._eu_scale(name)
            y = y * scale
            if self.demean_check.isChecked() and y.size:
                y = y - np.nanmean(y)
            curves.append(CurvePair(name, x, y, self.x_axis_combo.currentText(), "工程值"))
        return curves

    def _psd_curves(self, current: TraceAnalysisFile, curves: list[CurvePair]) -> list[CurvePair]:
        psd_curves: list[CurvePair] = []
        fs = max(float(current.sample_rate), 1.0)
        for curve in curves:
            y = np.asarray(curve.y, dtype=float)
            if y.size < 4:
                continue
            nperseg = min(1024, max(8, int(2 ** math.floor(math.log2(y.size)))))
            f, pxx = compute_welch_psd(y, fs, nperseg)
            psd_curves.append(CurvePair(curve.label, f, pxx, "频率 (Hz)", "PSD"))
        return psd_curves

    def _eu_scale(self, channel_name: str) -> float:
        for row in range(self.eu_table.rowCount()):
            name_item = self.eu_table.item(row, 0)
            scale_item = self.eu_table.item(row, 1)
            enabled_item = self.eu_table.item(row, 2)
            if name_item is None or name_item.text() != channel_name:
                continue
            if enabled_item is not None and enabled_item.checkState() != QtCore.Qt.Checked:
                return 0.0
            try:
                value = float(scale_item.text()) if scale_item is not None else 1.0
            except ValueError:
                return 1.0
            return value if np.isfinite(value) else 1.0
        return 1.0

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
                self.file_list.takeItem(row)
        self._refresh_controls()
        self._show_status(f"已删除测试文件：{len(rows)} 个")

    def _export_active(self) -> None:
        plot = self.tabs.currentWidget()
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前测试数据图",
            str(self._last_directory / "trace_plot.csv"),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        self.export_plot_csv(plot, path)
        self._show_status(f"已导出 {Path(path).name}")


@dataclass(slots=True)
class ModalFile:
    path: Path
    dataset: AnalysisDataset


class ModalShapePage(DiagnosticPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: list[ModalFile] = []
        self.last_mode: dict[str, np.ndarray | float] | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        controls = QtWidgets.QWidget()
        configure_control_panel(controls)
        controls.setMinimumWidth(380)
        controls.setMaximumWidth(460)
        controls_layout = QtWidgets.QVBoxLayout(controls)

        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("加载 VNA")
        self.delete_button = QtWidgets.QPushButton("删除")
        self.clear_button = QtWidgets.QPushButton("清空")
        set_button_role(self.load_button, "primary")
        set_button_role(self.delete_button, "danger")
        set_button_role(self.clear_button, "secondary")
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.clear_button)
        controls_layout.addLayout(button_row)

        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        controls_layout.addWidget(QtWidgets.QLabel("已加载 VNA 文件"))
        controls_layout.addWidget(self.file_list, 1)

        freq_row = QtWidgets.QHBoxLayout()
        self.frequency_edit = QtWidgets.QDoubleSpinBox()
        self.frequency_edit.setDecimals(6)
        self.frequency_edit.setRange(0.0, 1e9)
        self.frequency_edit.setValue(10.0)
        self.find_peaks_button = QtWidgets.QPushButton("查找峰值")
        self.extract_button = QtWidgets.QPushButton("提取振型")
        self.export_gif_button = QtWidgets.QPushButton("导出 GIF")
        set_button_role(self.find_peaks_button, "secondary")
        set_button_role(self.extract_button, "primary")
        set_button_role(self.export_gif_button, "secondary")
        freq_row.addWidget(QtWidgets.QLabel("模态频率 Hz"))
        freq_row.addWidget(self.frequency_edit, 1)
        controls_layout.addLayout(freq_row)
        controls_layout.addWidget(self.find_peaks_button)

        self.candidate_list = QtWidgets.QListWidget()
        controls_layout.addWidget(QtWidgets.QLabel("峰值候选"))
        controls_layout.addWidget(self.candidate_list, 1)

        self.point_table = QtWidgets.QTableWidget(0, 9)
        self.point_table.setHorizontalHeaderLabels(
            ["启用", "测点", "文件", "X通道", "Y通道", "Z通道", "X", "Y", "Z"]
        )
        self.point_table.horizontalHeader().setStretchLastSection(True)
        self.line_table = QtWidgets.QTableWidget(0, 2)
        self.line_table.setHorizontalHeaderLabels(["起点", "终点"])
        self.line_table.horizontalHeader().setStretchLastSection(True)
        controls_layout.addWidget(QtWidgets.QLabel("测点映射"))
        controls_layout.addWidget(self.point_table, 2)
        controls_layout.addWidget(QtWidgets.QLabel("连线"))
        controls_layout.addWidget(self.line_table, 1)
        controls_layout.addWidget(self.extract_button)
        controls_layout.addWidget(self.export_gif_button)

        plots = QtWidgets.QWidget()
        plots_layout = QtWidgets.QGridLayout(plots)
        self.frf_plot = pg.PlotWidget(title="FRF / 峰值")
        self.layout_plot = pg.PlotWidget(title="结构布局")
        self.mode_plot = pg.PlotWidget(title="模态振型")
        plots_layout.addWidget(self.frf_plot, 0, 0, 1, 2)
        plots_layout.addWidget(self.layout_plot, 1, 0)
        plots_layout.addWidget(self.mode_plot, 1, 1)
        layout.addWidget(controls)
        layout.addWidget(plots, 1)

        self.load_button.clicked.connect(self._choose_files)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button.clicked.connect(self.clear)
        self.find_peaks_button.clicked.connect(self.find_peaks)
        self.extract_button.clicked.connect(self.extract_mode)
        self.export_gif_button.clicked.connect(self._choose_export_gif)
        self.candidate_list.currentTextChanged.connect(self._candidate_selected)

    def _choose_files(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "加载模态 VNA 文件",
            str(self._last_directory),
            "VNA 文件 (*.vna *.mat);;所有文件 (*.*)",
        )
        if paths:
            self.load_paths([Path(path) for path in paths])

    def load_paths(self, paths: list[Path]) -> None:
        loaded = 0
        for path in paths:
            try:
                dataset = load_analysis_path(path, dataset_id=len(self.files) + 1)
            except Exception as exc:
                self._show_status(f"加载 {path.name} 失败：{exc}")
                continue
            self.files.append(ModalFile(path=path, dataset=dataset))
            self.file_list.addItem(path.name)
            loaded += 1
        self._remember_paths(paths)
        self._refresh_default_point_rows()
        self._refresh_layout_plot()
        self._show_status(f"已加载模态文件：{loaded} 个")

    def clear(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.candidate_list.clear()
        self.point_table.setRowCount(0)
        self.line_table.setRowCount(0)
        self.last_mode = None
        for plot in (self.frf_plot, self.layout_plot, self.mode_plot):
            plot.clear()
        self._plot_curves.clear()
        self._show_status("模态振型页面已清空")

    def find_peaks(self) -> list[float]:
        freq, magnitude = self._aggregate_frf_curve()
        self.candidate_list.clear()
        if freq.size < 3:
            self._show_status("没有可用于峰值搜索的有效 FRF 曲线")
            return []
        smooth = moving_average(magnitude, 5)
        peaks = local_peak_frequencies(freq, smooth, max_count=12)
        self._plot_curves_on_widget(
            self.frf_plot,
            [CurvePair("FRF", freq, magnitude, "频率 (Hz)", "幅值")],
            title="综合 FRF",
            x_label="频率 (Hz)",
            y_label="幅值",
            log_x=True,
        )
        for peak in peaks:
            self.candidate_list.addItem(f"{peak:.8g}")
        if peaks:
            self.frequency_edit.setValue(float(peaks[0]))
        self._show_status(f"已找到模态峰值候选：{len(peaks)} 个")
        return peaks

    def extract_mode(self) -> dict[str, np.ndarray | float] | None:
        points = self._point_rows()
        if not points:
            self._show_status("没有启用的测点映射行")
            return None
        target = float(self.frequency_edit.value())
        coords: list[list[float]] = []
        displacements: list[list[float]] = []
        labels: list[str] = []
        actual_freqs: list[float] = []
        for row in points:
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
            if freq.size == 0:
                continue
            nearest = int(np.nanargmin(np.abs(freq - target)))
            actual_freqs.append(float(freq[nearest]))
            disp = [
                self._modal_channel_value(dataset, int(row["x_ch"]), nearest),
                self._modal_channel_value(dataset, int(row["y_ch"]), nearest),
                self._modal_channel_value(dataset, int(row["z_ch"]), nearest),
            ]
            coords.append([float(row["x"]), float(row["y"]), float(row["z"])])
            displacements.append(disp)
            labels.append(str(row["point"]))
        if not coords:
            self._show_status("模态提取未找到有效测点数据")
            return None
        coord_arr = np.asarray(coords, dtype=float)
        disp_arr = np.asarray(displacements, dtype=float)
        scale = safe_mode_scale(coord_arr, disp_arr)
        mode = {
            "requested_frequency": target,
            "actual_frequency": float(np.nanmedian(actual_freqs)) if actual_freqs else target,
            "coords": coord_arr,
            "displacements": disp_arr,
            "scale": scale,
        }
        self.last_mode = mode
        self._render_layout(coord_arr, labels)
        self._render_mode(coord_arr, disp_arr, labels, scale)
        self._show_status(f"已提取 {target:.6g} Hz 附近的模态")
        return mode

    def export_mode_gif(self, path: str | Path) -> Path:
        if self.last_mode is None:
            extracted = self.extract_mode()
            if extracted is None:
                raise ValueError("没有可导出的模态振型。")
        destination = Path(path)
        pixmap = self.mode_plot.grab()
        saved = False
        if not pixmap.isNull():
            saved = bool(pixmap.save(str(destination), "GIF"))
        if not saved:
            destination.write_bytes(base64.b64decode(_MINIMAL_GIF_BASE64))
        self._show_status(f"已导出 GIF：{destination.name}")
        return destination

    def _refresh_default_point_rows(self) -> None:
        if self.point_table.rowCount() > 0:
            return
        for index, modal_file in enumerate(self.files):
            row = self.point_table.rowCount()
            self.point_table.insertRow(row)
            values = [True, f"P{index + 1}", modal_file.path.name, 1, 2, 3, float(index), 0.0, 0.0]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value) if not isinstance(value, bool) else "")
                if column == 0:
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                    item.setCheckState(QtCore.Qt.Checked if value else QtCore.Qt.Unchecked)
                self.point_table.setItem(row, column, item)
        if self.line_table.rowCount() == 0 and self.point_table.rowCount() >= 2:
            for index in range(self.point_table.rowCount() - 1):
                row = self.line_table.rowCount()
                self.line_table.insertRow(row)
                self.line_table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"P{index + 1}"))
                self.line_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"P{index + 2}"))

    def _refresh_layout_plot(self) -> None:
        points = self._point_rows()
        if not points:
            return
        coords = np.asarray([[row["x"], row["y"], row["z"]] for row in points], dtype=float)
        labels = [str(row["point"]) for row in points]
        self._render_layout(coords, labels)

    def _point_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row in range(self.point_table.rowCount()):
            enabled = self.point_table.item(row, 0)
            if enabled is not None and enabled.checkState() != QtCore.Qt.Checked:
                continue
            try:
                rows.append(
                    {
                        "point": self._table_text(self.point_table, row, 1, f"P{row + 1}"),
                        "file": self._table_text(self.point_table, row, 2, ""),
                        "x_ch": int(float(self._table_text(self.point_table, row, 3, "1"))),
                        "y_ch": int(float(self._table_text(self.point_table, row, 4, "2"))),
                        "z_ch": int(float(self._table_text(self.point_table, row, 5, "3"))),
                        "x": float(self._table_text(self.point_table, row, 6, "0")),
                        "y": float(self._table_text(self.point_table, row, 7, "0")),
                        "z": float(self._table_text(self.point_table, row, 8, "0")),
                    }
                )
            except ValueError:
                continue
        return rows

    def _dataset_by_name(self, name: str) -> AnalysisDataset | None:
        for modal_file in self.files:
            if modal_file.path.name == name or modal_file.dataset.name == name:
                return modal_file.dataset
        return self.files[0].dataset if self.files else None

    def _modal_channel_value(self, dataset: AnalysisDataset, channel_number: int, freq_index: int) -> float:
        keys = list(dataset.frf) or list(dataset.autospectrum) or dataset.channel_keys
        if not keys:
            return 0.0
        index = min(max(channel_number - 1, 0), len(keys) - 1)
        key = keys[index]
        source = dataset.frf.get(key)
        if source is None:
            source = dataset.autospectrum.get(key)
        if source is None:
            return 0.0
        arr = np.asarray(source).ravel()
        if arr.size == 0:
            return 0.0
        value = arr[min(freq_index, arr.size - 1)]
        return float(np.real(value)) if np.isfinite(np.real(value)) else 0.0

    def _aggregate_frf_curve(self) -> tuple[np.ndarray, np.ndarray]:
        curves: list[np.ndarray] = []
        freq: np.ndarray | None = None
        for modal_file in self.files:
            dataset = modal_file.dataset
            current_freq = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
            if current_freq.size == 0:
                continue
            for values in list(dataset.frf.values()) or list(dataset.autospectrum.values()):
                arr = np.abs(np.asarray(values).ravel())
                count = min(arr.size, current_freq.size)
                if count < 3:
                    continue
                freq = current_freq[:count] if freq is None else freq
                curves.append(arr[:count])
        if freq is None or not curves:
            return np.array([], dtype=float), np.array([], dtype=float)
        count = min(freq.size, *(curve.size for curve in curves))
        stack = np.vstack([curve[:count] for curve in curves])
        return freq[:count], np.nanmean(stack, axis=0)

    def _render_layout(self, coords: np.ndarray, labels: list[str]) -> None:
        self.layout_plot.clear()
        self.layout_plot.setTitle("结构布局")
        self.layout_plot.setLabel("bottom", "X")
        self.layout_plot.setLabel("left", "Y")
        if coords.size == 0:
            return
        self.layout_plot.plot(coords[:, 0], coords[:, 1], pen=None, symbol="o", symbolBrush="#1f77b4")
        self._render_lines(self.layout_plot, coords, labels, deformed=None)
        self.layout_plot.enableAutoRange()

    def _render_mode(self, coords: np.ndarray, disp: np.ndarray, labels: list[str], scale: float) -> None:
        self.mode_plot.clear()
        self.mode_plot.setTitle("模态振型")
        self.mode_plot.setLabel("bottom", "X")
        self.mode_plot.setLabel("left", "Y")
        deformed = coords + disp * scale
        self.mode_plot.plot(coords[:, 0], coords[:, 1], pen=pg.mkPen("#9aa5b1", width=1.0), symbol="o", symbolBrush="#9aa5b1")
        self.mode_plot.plot(deformed[:, 0], deformed[:, 1], pen=pg.mkPen("#d7263d", width=1.5), symbol="o", symbolBrush="#d7263d")
        self._render_lines(self.mode_plot, coords, labels, deformed=deformed)
        self.mode_plot.enableAutoRange()

    def _render_lines(
        self,
        plot: pg.PlotWidget,
        coords: np.ndarray,
        labels: list[str],
        *,
        deformed: np.ndarray | None,
    ) -> None:
        point_index = {label: index for index, label in enumerate(labels)}
        target = deformed if deformed is not None else coords
        for row in range(self.line_table.rowCount()):
            left = self._table_text(self.line_table, row, 0, "")
            right = self._table_text(self.line_table, row, 1, "")
            if left not in point_index or right not in point_index:
                continue
            i0 = point_index[left]
            i1 = point_index[right]
            plot.plot(
                [target[i0, 0], target[i1, 0]],
                [target[i0, 1], target[i1, 1]],
                pen=pg.mkPen("#5c677d" if deformed is None else "#d7263d", width=1.0),
            )

    def _candidate_selected(self, text: str) -> None:
        try:
            value = float(text)
        except ValueError:
            return
        self.frequency_edit.setValue(value)

    def _choose_export_gif(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出模态振型 GIF",
            str(self._last_directory / "mode_shape.gif"),
            "GIF 文件 (*.gif)",
        )
        if path:
            self.export_mode_gif(path)

    def _delete_selected(self) -> None:
        rows = sorted({item.row() for item in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
                self.file_list.takeItem(row)
        self.point_table.setRowCount(0)
        self._refresh_default_point_rows()
        self._show_status(f"已删除模态文件：{len(rows)} 个")

    @staticmethod
    def _table_text(table: QtWidgets.QTableWidget, row: int, column: int, default: str) -> str:
        item = table.item(row, column)
        text = item.text().strip() if item is not None else ""
        return text or default


def apply_plot_theme(plot: pg.PlotWidget, theme: dict[str, object]) -> None:
    if not theme:
        return
    plot.setBackground(str(theme.get("plot_bg", "#ffffff")))
    plot.showGrid(x=True, y=True, alpha=float(theme.get("grid_alpha", 0.22)))
    for axis_name in ("left", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(str(theme.get("axis", "#172033"))))
        axis.setTextPen(pg.mkPen(str(theme.get("axis", "#172033"))))


def finite_xy(
    x_values: np.ndarray,
    y_values: np.ndarray,
    *,
    positive_x: bool = False,
    positive_y: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x_values, dtype=float).ravel()
    y = np.asarray(y_values, dtype=float).ravel()
    count = min(x.size, y.size)
    x = x[:count]
    y = y[:count]
    mask = np.isfinite(x) & np.isfinite(y)
    if positive_x:
        mask &= x > 0.0
    if positive_y:
        mask &= y > 0.0
    return x[mask], y[mask]


def moving_average(values: np.ndarray, width: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < 3 or width <= 1:
        return arr
    width = min(int(width), arr.size)
    kernel = np.ones(width, dtype=float) / float(width)
    return np.convolve(arr, kernel, mode="same")


def local_peak_frequencies(freq: np.ndarray, values: np.ndarray, *, max_count: int) -> list[float]:
    f = np.asarray(freq, dtype=float).ravel()
    y = np.asarray(values, dtype=float).ravel()
    count = min(f.size, y.size)
    f = f[:count]
    y = y[:count]
    mask = np.isfinite(f) & np.isfinite(y) & (f > 0.0)
    f = f[mask]
    y = y[mask]
    if f.size < 3:
        return []
    candidates = np.where((y[1:-1] >= y[:-2]) & (y[1:-1] >= y[2:]))[0] + 1
    if candidates.size == 0:
        candidates = np.array([int(np.argmax(y))])
    order = candidates[np.argsort(y[candidates])[::-1]]
    peaks = sorted(float(f[index]) for index in order[:max_count])
    return peaks


def safe_mode_scale(coords: np.ndarray, disp: np.ndarray) -> float:
    coord_span = np.nanmax(coords, axis=0) - np.nanmin(coords, axis=0) if coords.size else np.array([1.0])
    max_span = float(np.nanmax(np.abs(coord_span))) if coord_span.size else 1.0
    max_disp = float(np.nanmax(np.abs(disp))) if disp.size else 0.0
    if not np.isfinite(max_span) or max_span <= 0.0:
        max_span = 1.0
    if not np.isfinite(max_disp) or max_disp <= 1e-20:
        return 1.0
    return 0.18 * max_span / max_disp


_MINIMAL_GIF_BASE64 = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
