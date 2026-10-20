"""Modal-page controls and asynchronous orchestration; no acquisition ownership."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from python_vna.diagnostic.data import CurvePair

from python_vna.diagnostic.modal_identification import (
    FRFMeasurement, ModalCancelled, ModalSettings, fit_selected_poles,
    identify_modes, prepare_modal_input,
)


class _ModalFactorSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value):
        return self.locale().toString(value, "f", self.decimals()).rstrip("0").rstrip(self.locale().decimalPoint())


class _ModalStatusLabel(QtWidgets.QLabel):
    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)


class ModalIdentificationTask(QtCore.QThread):
    resultReady = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progressChanged = QtCore.Signal(int, str)

    def __init__(self, data, selected=None, parent=None):
        super().__init__(parent)
        self.data = data
        self.selected = selected

    def run(self):
        try:
            kwargs = {"cancel": self.isInterruptionRequested, "progress": self.progressChanged.emit}
            result = (identify_modes(self.data, **kwargs) if self.selected is None
                      else fit_selected_poles(self.data, self.selected, **kwargs))
            self.resultReady.emit(result)
        except ModalCancelled:
            self.failed.emit("已取消模态识别，保留上次结果")
        except Exception as exc:
            self.failed.emit(f"模态识别失败：{exc}")


class ModalIdentificationMixin:
    def _init_identification(self):
        self._identification_result = None
        self._identification_data = None
        self._identification_stale = True
        self._identification_generation = 0
        self._identification_task = None
        self._identification_file_options = {}
        self._identification_refreshing = False

    def _build_identification_settings(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.modal_sources_panel = QtWidgets.QWidget()
        source_layout = QtWidgets.QVBoxLayout(self.modal_sources_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.modal_input_table = QtWidgets.QTableWidget(0, 7)
        self.modal_input_table.setHorizontalHeaderLabels(["文件", "力通道", "力单位", "追加力系数", "响应单位", "追加响应系数", "敲击方向"])
        self.modal_input_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.modal_input_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.modal_input_table.setToolTip("文件已有工程系数自动应用；表内系数为追加修正。响应单位选择只改变单位解释，不重复应用灵敏度。")
        self.modal_input_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.modal_input_table.horizontalHeader().setMinimumSectionSize(90)
        self.modal_input_table.verticalHeader().setDefaultSectionSize(38)
        self.modal_input_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.modal_input_table.setMinimumHeight(100)
        self.modal_show_corrections = QtWidgets.QCheckBox("显示追加系数")
        self.modal_show_corrections.setToolTip("原文件的工程系数始终生效；只有额外修正时才需要修改这两列")
        self.modal_show_corrections.toggled.connect(lambda visible: [self.modal_input_table.setColumnHidden(c, not visible) for c in (3, 5)])
        for column in (3, 5):
            self.modal_input_table.setColumnHidden(column, True)
        source_layout.addWidget(self.modal_show_corrections)
        source_layout.addWidget(self.modal_input_table)
        self.modal_sources_dialog = self._modal_settings_dialog("输入与标定", self.modal_sources_panel, (960, 420))
        self.modal_sources_button = QtWidgets.QPushButton("输入与标定…")
        self.modal_sources_button.clicked.connect(lambda: self._open_modal_settings_dialog(self.modal_sources_dialog))
        self.modal_advanced_button = QtWidgets.QPushButton("高级设置…")
        self.modal_frequency_button = QtWidgets.QPushButton("分析频段…")
        commands = QtWidgets.QHBoxLayout()
        for button in (self.modal_sources_button, self.modal_advanced_button, self.modal_frequency_button):
            commands.addWidget(button)
        layout.addLayout(commands)
        self.modal_source_summary = QtWidgets.QLabel("尚未加载 FRF")
        self.modal_source_summary.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        source_heading = QtWidgets.QHBoxLayout()
        source_heading.addWidget(self.modal_source_summary, 1)
        self.modal_algorithm_summary = QtWidgets.QLabel("候选：CMIF | 默认参数", panel)
        self.modal_algorithm_summary.hide()
        self.modal_advanced_button.setText("算法：CMIF…")
        self.modal_advanced_button.setToolTip(self.modal_algorithm_summary.text())
        grid = QtWidgets.QGridLayout()
        self.modal_driver_combo = QtWidgets.QComboBox()
        self.modal_driver_combo.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.modal_direction_combo = QtWidgets.QComboBox()
        self.modal_direction_combo.addItems(["X", "Y", "Z"])
        self.modal_direction_combo.setToolTip("选择本次识别的力参考组。只合并相同敲击方向的数据，响应仍保留 X/Y/Z；模态质量以驱动点该方向分量为 1。")
        grid.addWidget(QtWidgets.QLabel("驱动点"), 0, 0)
        grid.addWidget(self.modal_driver_combo, 0, 1)
        grid.addWidget(QtWidgets.QLabel("力参考"), 0, 2)
        grid.addWidget(self.modal_direction_combo, 0, 3)
        self.modal_frequency_min = QtWidgets.QDoubleSpinBox()
        self.modal_frequency_max = QtWidgets.QDoubleSpinBox()
        for spin in (self.modal_frequency_min, self.modal_frequency_max):
            spin.setRange(0, 1e9)
            spin.setDecimals(4)
            spin.setSpecialValueText("自动")
            spin.setSuffix(" Hz")
            spin.setKeyboardTracking(False)
            spin.setMinimumWidth(0)
            spin.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        layout.addLayout(grid)
        self.modal_limit_frequency = QtWidgets.QCheckBox("限定分析频段")
        self.modal_limit_frequency.setToolTip("未勾选时，正式识别自动取各 FRF 的共同有效频段")
        self.modal_range_panel = QtWidgets.QWidget()
        range_layout = QtWidgets.QHBoxLayout(self.modal_range_panel)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self.modal_frequency_min, 1)
        range_layout.addWidget(QtWidgets.QLabel("至"))
        range_layout.addWidget(self.modal_frequency_max, 1)
        self.modal_range_panel.hide()
        frequency_panel = QtWidgets.QWidget()
        frequency_layout = QtWidgets.QVBoxLayout(frequency_panel)
        frequency_layout.setContentsMargins(0, 0, 0, 0)
        frequency_layout.addWidget(self.modal_limit_frequency)
        frequency_layout.addWidget(self.modal_range_panel)
        frequency_layout.addStretch(1)
        self.modal_frequency_dialog = self._modal_settings_dialog("分析频段", frequency_panel, (460, 180))
        self.modal_frequency_button.clicked.connect(lambda: self._open_modal_settings_dialog(self.modal_frequency_dialog))
        self._modal_manual_frequency_range = (0., 0.)
        self.modal_force_confirm = QtWidgets.QCheckBox("力已标定，固定施力与工况", panel)
        self.modal_force_confirm.setToolTip("各次测试施力位置、方向和结构工况一致，输入为标定后的实际激励力")
        self.modal_driver_confirm = QtWidgets.QCheckBox("驱动点为同点同向响应", panel)
        self.modal_force_confirm.hide()
        self.modal_driver_confirm.hide()
        self.modal_physical_confirm = QtWidgets.QCheckBox("确认标定及同点同向")
        self.modal_physical_confirm.setToolTip("仅在全部满足时勾选：力已标定；施力位置、方向及测试工况一致；所选驱动点响应与施力同点同向。")
        source_heading.addWidget(self.modal_physical_confirm)
        layout.addLayout(source_heading)
        self.modal_advanced_panel = QtWidgets.QTabWidget()
        candidate_panel = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(candidate_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self._build_candidate_search_controls(advanced_layout, collapsible=False)
        advanced_layout.addStretch(1)
        self.modal_advanced_panel.addTab(candidate_panel, "候选搜索")
        advanced = QtWidgets.QGroupBox("高级拟合参数")
        form = QtWidgets.QFormLayout(advanced)
        self.modal_max_order = QtWidgets.QSpinBox()
        self.modal_max_order.setRange(6, 80)
        self.modal_max_order.setSingleStep(2)
        self.modal_max_order.setValue(24)
        self.modal_max_order.setKeyboardTracking(False)
        self.modal_frequency_tol = self._modal_decimal(.01, .0001, .2, 4)
        self.modal_damping_tol = self._modal_decimal(.1, .001, .9, 3)
        self.modal_mac_tol = self._modal_decimal(.95, .5, 1, 3)
        self.modal_fit_tol = self._modal_decimal(.15, .001, .9, 3)
        self.modal_complexity_tol = self._modal_decimal(.2, .001, .9, 3)
        for title, control in (("最大模型阶次（偶数）", self.modal_max_order),
                               ("频率相对差", self.modal_frequency_tol), ("阻尼相对差", self.modal_damping_tol),
                               ("MAC 下限", self.modal_mac_tol), ("拟合误差上限", self.modal_fit_tol),
                               ("复模态特征上限", self.modal_complexity_tol)):
            form.addRow(title, control)
        formal_panel = QtWidgets.QWidget()
        formal_layout = QtWidgets.QVBoxLayout(formal_panel)
        formal_layout.setContentsMargins(0, 0, 0, 0)
        formal_layout.addWidget(advanced)
        formal_layout.addStretch(1)
        self.modal_advanced_panel.addTab(formal_panel, "正式识别")
        self.modal_advanced_dialog = self._modal_settings_dialog("高级设置", self.modal_advanced_panel, (620, 480))
        self.modal_advanced_button.clicked.connect(lambda: self._open_modal_settings_dialog(self.modal_advanced_dialog))
        self.modal_reset_algorithm_button = QtWidgets.QPushButton("恢复算法默认值")
        self.modal_reset_algorithm_button.setToolTip("恢复 CMIF 和算法参数默认值，不更改标定、驱动点或分析频段")
        self.modal_reset_algorithm_button.clicked.connect(self._reset_modal_algorithm_defaults)
        self.modal_advanced_dialog.layout().insertWidget(1, self.modal_reset_algorithm_button)
        self.identify_button = QtWidgets.QPushButton("识别模态")
        self.modal_cancel_button = QtWidgets.QPushButton("取消")
        self.modal_cancel_button.setEnabled(False)
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(self.identify_button)
        actions.addWidget(self.modal_cancel_button)
        self.modal_progress = QtWidgets.QProgressBar()
        self.modal_progress.setRange(0, 100)
        progress_policy = self.modal_progress.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(True)
        self.modal_progress.setSizePolicy(progress_policy)
        self.modal_progress.hide()
        self.modal_identification_status = _ModalStatusLabel()
        self.modal_identification_status.setText("尚未识别")
        self.modal_identification_status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        details_row = QtWidgets.QHBoxLayout()
        details_button = QtWidgets.QPushButton("诊断详情")
        copy_details = QtWidgets.QPushButton("复制详情")
        details_button.setMaximumWidth(80)
        copy_details.setMaximumWidth(80)
        actions.addWidget(details_button)
        self.modal_identification_details = QtWidgets.QPlainTextEdit()
        self.modal_identification_details.setReadOnly(True)
        self.modal_details_dialog = self._modal_settings_dialog("诊断详情", self.modal_identification_details, (720, 420))
        self.modal_details_dialog.findChild(QtWidgets.QDialogButtonBox).addButton(copy_details, QtWidgets.QDialogButtonBox.ActionRole)
        details_button.clicked.connect(lambda: self._open_modal_settings_dialog(self.modal_details_dialog))
        copy_details.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.modal_identification_details.toPlainText()))
        host = QtWidgets.QWidget()
        self.modal_settings_host = host
        host_layout = QtWidgets.QVBoxLayout(host)
        host_layout.setContentsMargins(4, 4, 4, 4)
        host_layout.setSpacing(4)
        host_layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        host_layout.addWidget(panel, 1)
        self.modal_settings_panel = panel
        host_layout.addLayout(actions)
        self.modal_progress.setMaximumWidth(100)
        details_row.insertWidget(0, self.modal_identification_status, 1)
        details_row.addWidget(self.modal_progress)
        host_layout.addLayout(details_row)
        layout.addStretch(1)
        layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        self.left_work_tabs.addTab(host, "识别设置")
        self.identify_button.clicked.connect(lambda: self.start_modal_identification())
        self.modal_cancel_button.clicked.connect(self.cancel_modal_identification)
        for spin in self._identification_spins().values():
            spin.editingFinished.connect(self._mark_identification_stale)
            spin.valueChanged.connect(self._update_modal_settings_summary)
        self.modal_limit_frequency.toggled.connect(self._toggle_modal_frequency_range)
        for spin in (self.modal_frequency_min, self.modal_frequency_max):
            spin.valueChanged.connect(self._sync_modal_frequency_controls)
        self.modal_candidate_method.currentIndexChanged.connect(self._update_modal_settings_summary)
        for spin in (self.modal_candidate_prominence, self.modal_candidate_spacing, self.modal_candidate_band,
                     self.modal_candidate_count, self.modal_candidate_order):
            spin.valueChanged.connect(self._update_modal_settings_summary)
        for control in (self.modal_driver_combo, self.modal_direction_combo):
            control.currentIndexChanged.connect(self._mark_identification_stale)
        self.modal_direction_combo.currentIndexChanged.connect(self._update_modal_source_summary)
        self.modal_force_confirm.toggled.connect(self._mark_identification_stale)
        self.modal_driver_confirm.toggled.connect(self._mark_identification_stale)
        self.modal_force_confirm.toggled.connect(self._sync_modal_physical_confirmation)
        self.modal_driver_confirm.toggled.connect(self._sync_modal_physical_confirmation)
        self.modal_physical_confirm.toggled.connect(self._set_modal_physical_confirmation)
        self._sync_modal_frequency_controls()
        self.left_work_tabs.currentChanged.connect(lambda _index: self._refresh_identification_sources())
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._shutdown_identification)

    def _modal_settings_dialog(self, title, content, size):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setSizeGripEnabled(True)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(content, 1)
        buttons = QtWidgets.QDialogButtonBox()
        done = buttons.addButton("完成", QtWidgets.QDialogButtonBox.AcceptRole)
        done.setAutoDefault(False)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.resize(*size)
        return dialog

    def _open_modal_settings_dialog(self, dialog):
        # Keep the same controls alive so edits, session restoration and summaries agree.
        screen = self.window().screen()
        if screen is not None:
            available = screen.availableGeometry()
            dialog.resize(min(dialog.width(), int(available.width() * .9)),
                          min(dialog.height(), int(available.height() * .9)))
        dialog.open()
        if screen is not None:
            frame = dialog.frameGeometry()
            center = self.window().frameGeometry().center()
            x = max(available.left() + 8, min(center.x() - frame.width() // 2, available.right() - frame.width() - 7))
            y = max(available.top() + 8, min(center.y() - frame.height() // 2, available.bottom() - frame.height() - 7))
            dialog.move(x, y)
        dialog.raise_()
        dialog.activateWindow()

    def _sync_modal_frequency_controls(self, *_args):
        manual = bool(self.modal_frequency_min.value() or self.modal_frequency_max.value())
        with QtCore.QSignalBlocker(self.modal_limit_frequency):
            self.modal_limit_frequency.setChecked(manual)
        self.modal_range_panel.setVisible(manual)
        self._update_modal_frequency_summary()

    def _toggle_modal_frequency_range(self, limited):
        self.modal_range_panel.setVisible(limited)
        if limited:
            values = self._modal_manual_frequency_range
        else:
            self._modal_manual_frequency_range = (self.modal_frequency_min.value(), self.modal_frequency_max.value())
            values = (0., 0.)
        for spin, value in zip((self.modal_frequency_min, self.modal_frequency_max), values):
            with QtCore.QSignalBlocker(spin):
                spin.setValue(value)
        self._update_modal_frequency_summary()
        self._mark_identification_stale()

    def _update_modal_frequency_summary(self):
        low, high = self.modal_frequency_min.value(), self.modal_frequency_max.value()
        text = f"分析频段：{low or '自动'} 至 {high or '自动'} Hz" if low or high else "分析频段：自动取共同有效频段"
        self.modal_frequency_button.setToolTip(text)
        self.modal_frequency_button.setText("频段（自定义）…" if low or high else "分析频段…")

    def _sync_modal_physical_confirmation(self, *_args):
        confirmed = self.modal_force_confirm.isChecked() and self.modal_driver_confirm.isChecked()
        with QtCore.QSignalBlocker(self.modal_physical_confirm):
            self.modal_physical_confirm.setChecked(confirmed)

    def _set_modal_physical_confirmation(self, confirmed):
        for widget in (self.modal_force_confirm, self.modal_driver_confirm):
            with QtCore.QSignalBlocker(widget):
                widget.setChecked(confirmed)
        self._mark_identification_stale()

    def _reset_modal_algorithm_defaults(self):
        formal = {"modal_max_order": 24, "modal_frequency_tol": .01, "modal_damping_tol": .1,
                  "modal_mac_tol": .95, "modal_fit_tol": .15, "modal_complexity_tol": .2}
        candidate = {"modal_candidate_prominence": 1, "modal_candidate_spacing": 0, "modal_candidate_band": 0,
                     "modal_candidate_count": 24, "modal_candidate_order": 16}
        formal_changed = any(getattr(self, name).value() != value for name, value in formal.items())
        candidate_changed = any(getattr(self, name).value() != value for name, value in candidate.items())
        for name, value in {**formal, **candidate}.items():
            getattr(self, name).setValue(value)
        self.modal_candidate_method.setCurrentIndex(self.modal_candidate_method.findData("cmif"))
        if formal_changed:
            self._mark_identification_stale()
        elif candidate_changed:
            self._invalidate_candidate_search()

    def _update_modal_settings_summary(self, *_args):
        defaults = {"modal_max_order": 24, "modal_frequency_tol": .01, "modal_damping_tol": .1,
                    "modal_mac_tol": .95, "modal_fit_tol": .15, "modal_complexity_tol": .2,
                    "modal_candidate_prominence": 1, "modal_candidate_spacing": 0, "modal_candidate_band": 0,
                    "modal_candidate_count": 24, "modal_candidate_order": 16}
        changed = [name for name, value in defaults.items() if getattr(self, name).value() != value]
        text = f"候选：{self.modal_candidate_method.currentText()} | " + ("参数已自定义" if changed else "默认参数")
        self.modal_algorithm_summary.setText(text)
        self.modal_algorithm_summary.setToolTip(text + "\n" + "\n".join(f"{name}: {getattr(self, name).value()}" for name in changed))
        algorithm = "CMIF" if self.modal_candidate_method.currentData() == "cmif" else "自定义"
        self.modal_advanced_button.setText(f"算法：{algorithm}…")
        self.modal_advanced_button.setToolTip(self.modal_algorithm_summary.toolTip())

    def _update_modal_source_summary(self):
        ready, corrections, details = 0, 0, []
        selected_count = 0
        reference = self.modal_direction_combo.currentText()
        for row in range(self.modal_input_table.rowCount()):
            widgets = [self.modal_input_table.cellWidget(row, i) for i in range(1, 6)]
            if any(widget is None for widget in widgets):
                continue
            force, unit, force_factor, response, response_factor = widgets
            axis = self.modal_input_table.cellWidget(row, 6)
            force_axis = axis.currentData() if axis is not None else ""
            if not force_axis and axis is not None:
                force_axis = axis.property("mappedForceAxis") or ""
            selected = force_axis in {"", reference}
            selected_count += selected
            ready += bool(selected and force.currentData() and unit.currentText() in {"N", "kN", "mN"})
            corrections += int(force_factor.value() != 1 or response_factor.value() != 1)
            details.append(f"{self.modal_input_table.item(row, 0).text()}: 敲击 {force_axis or '当前参考（未单独指定）'}；{force.currentText()} / {unit.currentText()}；响应 {response.currentText()}；追加系数 {force_factor.value():g}, {response_factor.value():g}")
        count = self.modal_input_table.rowCount()
        text = f"{reference} 参考就绪 {ready}/{selected_count} | 共 {count} 文件" if count else "尚未加载 FRF"
        if corrections:
            text += f" | {corrections} 项系数修正"
        self.modal_source_summary.setText(text)
        self.modal_source_summary.setToolTip("\n".join(details))
        if corrections:
            self.modal_show_corrections.setChecked(True)

    def _mapped_modal_force_axes(self):
        axes_by_dataset = {id(file.dataset): set() for file in self.files}
        datasets_by_name = {}
        for row in self._point_rows(require_enabled=True, require_bound=True):
            file_name = str(row["file"])
            if file_name not in datasets_by_name:
                datasets_by_name[file_name] = self._dataset_by_name(file_name)
            dataset = datasets_by_name[file_name]
            if dataset is not None:
                axes_by_dataset[id(dataset)].update(name.upper() for name in "xyz" if int(row[f"{name}_ch"]) > 0)
        return {str(file.path): next(iter(axes)) if len(axes) == 1 else ""
                for file in self.files for axes in (axes_by_dataset[id(file.dataset)],)}

    def _modal_file_force_axis(self, modal_file, mapped_axes=None):
        if mapped_axes is None:
            mapped_axes = self._mapped_modal_force_axes()
        return (self._identification_file_options.get(str(modal_file.path), {}).get("force_axis", "")
                or mapped_axes.get(str(modal_file.path), ""))

    def _modal_reference_matches(self, modal_file, mapped_axes=None):
        axis = self._modal_file_force_axis(modal_file, mapped_axes)
        return axis in {"", self.modal_direction_combo.currentText()}

    @staticmethod
    def _modal_decimal(value, low, high, decimals):
        box = _ModalFactorSpinBox() if decimals == 12 else QtWidgets.QDoubleSpinBox()
        box.setDecimals(decimals)
        box.setRange(low, high)
        box.setValue(value)
        box.setKeyboardTracking(False)
        box.setMinimumWidth(0)
        box.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        return box

    def _identification_spins(self):
        return {name: getattr(self, name) for name in (
            "modal_frequency_min", "modal_frequency_max", "modal_max_order", "modal_frequency_tol",
            "modal_damping_tol", "modal_mac_tol", "modal_fit_tol", "modal_complexity_tol")}

    def _build_identification_results(self, candidate_list, parent_layout):
        self.modal_result_tabs = QtWidgets.QTabWidget()
        self.modal_result_tabs.addTab(candidate_list, "频率候选")
        results = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(results)
        layout.setContentsMargins(0, 0, 0, 0)
        self.modal_result_table = QtWidgets.QTableWidget(0, 7)
        self.modal_result_table.setHorizontalHeaderLabels(["阶次", "频率 Hz", "阻尼 %", "质量 kg", "误差 %", "状态", "刚度 N/m / 阻尼 Ns/m"])
        self.modal_result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.modal_result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.modal_result_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.modal_result_table.setMinimumHeight(140)
        self.modal_result_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.modal_result_table, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.modal_delete_result_button = QtWidgets.QPushButton("删除结果")
        self.modal_export_result_button = QtWidgets.QPushButton("导出结果")
        buttons.addWidget(self.modal_delete_result_button)
        buttons.addWidget(self.modal_export_result_button)
        layout.addLayout(buttons)
        self.modal_result_tabs.addTab(results, "模态结果")
        parent_layout.addWidget(self.modal_result_tabs, 1)
        self.modal_result_table.itemSelectionChanged.connect(self._select_identified_mode)
        self.modal_delete_result_button.clicked.connect(self._delete_identified_modes)
        self.modal_export_result_button.clicked.connect(self._choose_export_identification)
        self.modal_export_result_button.setEnabled(False)

    def _build_identification_plots(self, parent_layout):
        self.modal_plot_tabs = QtWidgets.QTabWidget()
        self.modal_plot_tabs.addTab(self.frf_plot, "FRF / CMIF")
        stability = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(stability)
        layout.setContentsMargins(0, 0, 0, 0)
        self.modal_stability_plot = self._create_plot_widget("稳定图")
        self.modal_stability_plot.setMinimumHeight(100)
        layout.addWidget(self.modal_stability_plot, 1)
        self.modal_pole_table = QtWidgets.QTableWidget(0, 6)
        self.modal_pole_table.setHorizontalHeaderLabels(["拟合", "模型阶次", "频率 Hz", "阻尼 %", "连续稳定阶次", "稳定性诊断"])
        self.modal_pole_table.setMaximumHeight(100)
        self.modal_pole_table.setMinimumHeight(70)
        self.modal_pole_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.modal_pole_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.modal_pole_table)
        self.modal_refit_button = QtWidgets.QPushButton("拟合所选极点")
        self.modal_refit_button.clicked.connect(self._refit_selected_modal_poles)
        layout.addWidget(self.modal_refit_button)
        self.modal_plot_tabs.addTab(stability, "稳定图")
        validation = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(validation)
        layout.setContentsMargins(0, 0, 0, 0)
        self.modal_validation_channel = QtWidgets.QComboBox()
        self.modal_validation_channel.currentIndexChanged.connect(self._draw_modal_validation)
        layout.addWidget(self.modal_validation_channel)
        checks = QtWidgets.QTabWidget()
        self.modal_fit_plot = self._create_plot_widget("FRF 重构校核（位移/力）")
        self.modal_phase_plot = self._create_plot_widget("相位校核")
        self.modal_mac_table = QtWidgets.QTableWidget()
        self.modal_mac_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        checks.addTab(self.modal_fit_plot, "幅值")
        checks.addTab(self.modal_phase_plot, "相位")
        checks.addTab(self.modal_mac_table, "AutoMAC")
        layout.addWidget(checks, 1)
        self.modal_validation_label = QtWidgets.QLabel()
        self.modal_validation_label.setWordWrap(True)
        layout.addWidget(self.modal_validation_label)
        self.modal_plot_tabs.addTab(validation, "拟合校核")
        parent_layout.addWidget(self.modal_plot_tabs)

    def _refresh_identification_sources(self):
        if not hasattr(self, "modal_input_table") or self._identification_refreshing:
            return
        self._identification_refreshing = True
        try:
            self._capture_identification_file_options()
            self.modal_input_table.setRowCount(0)
            mapped_axes = self._mapped_modal_force_axes()
            for modal_file in self.files:
                dataset = modal_file.dataset
                if not dataset.frf:
                    continue
                row = self.modal_input_table.rowCount()
                self.modal_input_table.insertRow(row)
                file_id = str(modal_file.path)
                item = QtWidgets.QTableWidgetItem(modal_file.path.name)
                item.setToolTip(str(modal_file.path))
                item.setData(QtCore.Qt.UserRole, file_id)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.modal_input_table.setItem(row, 0, item)
                options = self._identification_file_options.get(file_id, {})
                force = QtWidgets.QComboBox()
                force.addItem("请选择", "")
                force_keys = sorted({key.split("->")[0] for key in dataset.frf if "->" in key})
                for key in force_keys:
                    force.addItem(f"{key} ({self._endpoint_unit_scale(dataset, key)[0] or '单位未知'})", key)
                selected_force = options.get("force_key", "")
                if selected_force not in force_keys and len(force_keys) == 1:
                    selected_force = force_keys[0]
                force.setCurrentIndex(max(0, force.findData(selected_force)))
                unit = QtWidgets.QComboBox()
                unit.addItems(["请选择", "N", "kN", "mN"])
                declared_unit = str(self._endpoint_unit_scale(dataset, force.currentData())[0] or "").strip()
                inferred_unit = declared_unit if declared_unit in {"N", "kN", "mN"} else "请选择"
                selected_unit = options.get("force_unit", "请选择")
                automatic_unit = options.get("force_unit_auto", selected_unit == "请选择")
                unit.setCurrentText(inferred_unit if automatic_unit or selected_unit == "请选择" else selected_unit)
                unit.setProperty("inferredUnit", automatic_unit or unit.currentText() == inferred_unit)
                unit.currentIndexChanged.connect(lambda _index, box=unit: box.setProperty("inferredUnit", False))
                force_factor = self._modal_decimal(options.get("force_factor", 1), 1e-12, 1e12, 12)
                response = QtWidgets.QComboBox()
                response.addItems(["按通道单位", "g", "m/s^2", "mm/s^2", "m/s", "mm/s", "um/s", "m", "mm", "um"])
                response.setCurrentText(options.get("response_unit", "按通道单位"))
                factor = self._modal_decimal(options.get("response_factor", 1), 1e-12, 1e12, 12)
                for column, widget in enumerate((force, unit, force_factor, response, factor), 1):
                    self.modal_input_table.setCellWidget(row, column, widget)
                    if widget is force:
                        widget.currentIndexChanged.connect(lambda _index, ds=dataset, f=force, u=unit: self._modal_force_reference_changed(ds, f, u))
                    elif isinstance(widget, QtWidgets.QComboBox):
                        widget.currentIndexChanged.connect(self._mark_identification_stale)
                        widget.currentIndexChanged.connect(self._update_modal_source_summary)
                    else:
                        widget.editingFinished.connect(self._mark_identification_stale)
                        widget.valueChanged.connect(self._update_modal_source_summary)
                axis = QtWidgets.QComboBox()
                mapped_axis = mapped_axes.get(str(modal_file.path), "")
                axis.addItem(f"自动：{mapped_axis}" if mapped_axis else "沿当前参考", "")
                axis.setProperty("mappedForceAxis", mapped_axis)
                for name in "XYZ":
                    axis.addItem(name, name)
                axis.setCurrentIndex(max(0, axis.findData(options.get("force_axis", ""))))
                axis.setToolTip("优先按测点表的唯一启用方向自动归组，不需重复填写。仅在该文件实际敲击方向与映射方向不同时手动覆盖；存在多方向响应时不能仅据响应通道猜测敲击方向。")
                self.modal_input_table.setCellWidget(row, 6, axis)
                axis.currentIndexChanged.connect(self._mark_identification_stale)
                axis.currentIndexChanged.connect(self._update_modal_source_summary)
            old = self.modal_driver_combo.currentText()
            self.modal_driver_combo.blockSignals(True)
            self.modal_driver_combo.clear()
            self.modal_driver_combo.addItem("请选择")
            for point in self._point_groups(self._point_rows(require_enabled=True, require_bound=True)):
                self.modal_driver_combo.addItem(str(point["point"]))
            index = self.modal_driver_combo.findText(old)
            self.modal_driver_combo.setCurrentIndex(max(0, index))
            self.modal_driver_combo.blockSignals(False)
        finally:
            self._identification_refreshing = False
        self._update_modal_source_summary()

    def _modal_force_reference_changed(self, dataset, force, unit):
        if unit.property("inferredUnit") or unit.currentText() == "请选择":
            declared = str(self._endpoint_unit_scale(dataset, force.currentData())[0] or "").strip()
            with QtCore.QSignalBlocker(unit):
                unit.setCurrentText(declared if declared in {"N", "kN", "mN"} else "请选择")
            unit.setProperty("inferredUnit", True)
        self._mark_identification_stale()
        self._update_modal_source_summary()

    def _capture_identification_file_options(self):
        if not hasattr(self, "modal_input_table"):
            return
        for row in range(self.modal_input_table.rowCount()):
            item = self.modal_input_table.item(row, 0)
            if item is None:
                continue
            widgets = [self.modal_input_table.cellWidget(row, i) for i in range(1, 6)]
            self._identification_file_options[item.data(QtCore.Qt.UserRole)] = {
                "force_key": widgets[0].currentData(), "force_unit": widgets[1].currentText(),
                "force_factor": widgets[2].value(), "response_unit": widgets[3].currentText(), "response_factor": widgets[4].value(),
                "force_unit_auto": bool(widgets[1].property("inferredUnit")),
                "force_axis": self.modal_input_table.cellWidget(row, 6).currentData()}

    @staticmethod
    def _endpoint_unit_scale(dataset, key):
        legacy = dataset.metadata.get("legacy_channels", {}).get(key)
        for series in dataset.series:
            if series.channel_key == key or (legacy is not None and legacy.get("label") in {series.channel_key, series.display_name}):
                return series.unit, float(series.scale)
        if legacy is not None:
            return str(legacy.get("eu_string", "")), float(legacy.get("euscale_fac", 1.0))
        return "", 1.0

    @staticmethod
    def _response_aliases(dataset, number):
        for series in dataset.series:
            if series.channel_index != number - 1:
                continue
            legacy_keys = {key for key, meta in dataset.metadata.get("legacy_channels", {}).items()
                           if meta.get("label") in {series.channel_key, series.display_name}}
            return {series.channel_key} | (legacy_keys or {f"ai{number - 1}"})
        return {f"ai{number - 1}"}

    @staticmethod
    def _modal_impact_window_warning(dataset, force_key):
        metadata = dataset.metadata
        config = metadata.get("legacy_config_state", {})
        index = config.get("window_index", metadata.get("legacy_winsel"))
        if index != 2 and str(metadata.get("processing_window", "")).lower() not in {"hann", "hanning"}:
            return ""
        label = metadata.get("legacy_channels", {}).get(force_key, {}).get("label", force_key)
        force = np.asarray(dataset.channels.get(label, []), dtype=float).ravel()
        if force.size < 32 or not np.isfinite(force).all():
            return ""
        force = force - np.median(force)
        peak = float(np.max(abs(force)))
        rms = float(np.sqrt(np.mean(force ** 2)))
        position = float(np.argmax(abs(force)) / force.size)
        # A transient at a Hann-window edge is not a stationary FRF amplitude correction.
        if peak <= 8 * rms or not (position < .1 or position > .9):
            return ""
        return (f"锤击位于记录 {100 * position:.2f}% 处且使用 Hanning 窗，绝对留数可能受瞬态加窗偏差影响；"
                "需用合适的锤击窗重新处理完整记录/重新测量，不能用统一幅值系数校正")

    def _modal_input_snapshot(self):
        self._capture_identification_file_options()
        if not self.modal_force_confirm.isChecked():
            raise ValueError("请确认力已标定且测试施力位置、方向和工况一致")
        if not self.modal_driver_confirm.isChecked() or self.modal_driver_combo.currentIndex() <= 0:
            raise ValueError("请选择并确认同点同向驱动点响应")
        measurements, skipped = [], []
        unassigned = set()
        excluded = set()
        mapped_axes = self._mapped_modal_force_axes()
        window_warnings = {}
        for row_index in range(self.point_table.rowCount()):
            if not self._table_bool(self.point_table, row_index, 0, True):
                continue
            for column in range(3, 12):
                try:
                    value = float(self._table_text(self.point_table, row_index, column, "1" if 6 <= column <= 8 else "0"))
                    if not np.isfinite(value) or (column <= 5 and (value < 0 or value != int(value))):
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise ValueError(f"测点表第 {row_index + 1} 行“{self.POINT_HEADERS[column]}”不是有效数值") from None
        rows = self._point_rows(require_enabled=True, require_bound=True)
        enabled = sum(self._table_bool(self.point_table, row, 0, True) for row in range(self.point_table.rowCount()))
        if len(rows) != enabled:
            raise ValueError("启用的测点表存在无效坐标、系数或未绑定文件，请检查测点表")
        driver_index = None
        for row in rows:
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                raise ValueError(f"{row['point']}：找不到映射文件 {row['file']}")
            matching_file = next(item for item in self.files if item.dataset is dataset)
            if not self._modal_reference_matches(matching_file, mapped_axes):
                excluded.add(matching_file.path.name)
                continue
            options = self._identification_file_options.get(str(matching_file.path), {})
            if not self._modal_file_force_axis(matching_file, mapped_axes):
                unassigned.add(matching_file.path.name)
            force_key = options.get("force_key")
            if not force_key:
                raise ValueError(f"{row['file']}：请在识别设置选择力通道")
            if options.get("force_unit") not in {"N", "kN", "mN"}:
                raise ValueError(f"{row['file']}：请选择经过标定的力单位")
            for axis, name in enumerate("xyz"):
                number = int(row[f"{name}_ch"])
                if number <= 0:
                    continue
                aliases = self._response_aliases(dataset, number)
                keys = [key for key in dataset.frf if "->" in key and key.split("->")[0] == force_key and key.split("->")[1] in aliases]
                if not keys:
                    skipped.append(f"{row['point']}-{'XYZ'[axis]}：无对应复数 FRF")
                    continue
                if len(keys) != 1:
                    raise ValueError(f"{row['point']}-{'XYZ'[axis]}：响应通道存在多个 FRF，无法唯一匹配")
                key = keys[0]
                file_id = str(matching_file.path)
                if file_id not in window_warnings:
                    window_warnings[file_id] = self._modal_impact_window_warning(dataset, force_key)
                response_key = key.split("->")[1]
                response_unit, response_scale = self._endpoint_unit_scale(dataset, response_key)
                _force_unit, force_scale = self._endpoint_unit_scale(dataset, force_key)
                unit = options.get("response_unit")
                if unit == "按通道单位":
                    unit = response_unit
                if (str(row["point"]) == self.modal_driver_combo.currentText()
                        and axis == self.modal_direction_combo.currentIndex()):
                    driver_index = len(measurements)
                measurements.append(FRFMeasurement(
                    f"{row['point']}-{'XYZ'[axis]} | {row['file']} | {key}", str(matching_file.path),
                    force_key, response_key, str(row["point"]), axis,
                    tuple(float(row[a]) for a in "xyz"), np.asarray(dataset.frequency_hz).copy(), np.asarray(dataset.frf[key]).copy(),
                    str(unit or ""), str(options["force_unit"]),
                    float(row[f"{name}_scale"]) * response_scale * float(options["response_factor"]),
                    force_scale * float(options["force_factor"]),
                    np.asarray(dataset.coherence[key]).copy() if key in dataset.coherence else None,
                    force_axis=self.modal_direction_combo.currentIndex(), mass_warning=window_warnings[file_id]))
        if driver_index is None:
            raise ValueError(f"{self.modal_direction_combo.currentText()} 力参考组缺少同点同向驱动点 FRF；请检查文件敲击方向、驱动点及响应通道")
        settings = ModalSettings(self.modal_frequency_min.value(), self.modal_frequency_max.value(), self.modal_max_order.value(),
                                 self.modal_frequency_tol.value(), self.modal_damping_tol.value(), self.modal_mac_tol.value(),
                                 maximum_fit_error=self.modal_fit_tol.value(), maximum_complexity=self.modal_complexity_tol.value())
        data = prepare_modal_input(measurements, driver_index, settings)
        if excluded:
            skipped.append(f"当前为 {self.modal_direction_combo.currentText()} 力参考，已排除其他敲击方向的 {len(excluded)} 个文件；未混合三个输入参考")
        if unassigned:
            skipped.append(f"{len(unassigned)} 个文件无法从测点表确定唯一敲击方向，暂按当前 {self.modal_direction_combo.currentText()} 力参考处理；请核实输入与标定中的敲击方向：" + "、".join(sorted(unassigned)))
        return data, skipped

    def _mark_identification_stale(self, *_args):
        if self._identification_refreshing or getattr(self, "_restoring_modal_session", False):
            return
        self._identification_stale = True
        self._identification_generation += 1
        self._invalidate_candidate_search()
        if self.last_mode is not None and self.last_mode.get("preview_only"):
            self._preview_timer.stop()
            self.last_mode = None
        if hasattr(self, "modal_identification_status"):
            self.modal_identification_status.setText("结果待更新" if self._identification_result is not None else "尚未识别")
            self.modal_export_result_button.setEnabled(False)
            if self._identification_result is not None:
                self.frf_plot.setTitle("模态 FRF 候选图 · 识别结果待更新")
                self.modal_fit_plot.setTitle("FRF 重构校核 · 结果待更新")
                self.modal_phase_plot.setTitle("相位校核 · 结果待更新")

    def start_modal_identification(self, selected=None):
        if self._identification_task is not None or self._candidate_task is not None:
            return
        try:
            self._refresh_identification_sources()
            data, skipped = self._modal_input_snapshot()
            if selected is not None and (self._identification_result is None or data.fingerprint != self._identification_result["fingerprint"]):
                raise ValueError("输入已变化，请重新识别后选择极点")
        except (ValueError, TypeError) as exc:
            self.modal_identification_status.setText(str(exc))
            self.modal_identification_details.setPlainText(str(exc))
            self.left_work_tabs.setCurrentIndex(3)
            self._show_status(str(exc))
            return
        self._identification_data = data
        self._identification_run_generation = self._identification_generation
        self._identification_run_poles = self._identification_result.get("poles", []) if selected is not None else None
        self._identification_run_diagnostics = self._identification_result.get("diagnostics") if selected is not None else None
        self._identification_skipped = skipped
        task = ModalIdentificationTask(data, selected, self)
        self._identification_task = task
        task.resultReady.connect(self._accept_modal_identification)
        task.failed.connect(self._modal_identification_failed)
        task.progressChanged.connect(self._modal_identification_progress)
        task.finished.connect(self._modal_identification_finished)
        self.identify_button.setEnabled(False)
        self.modal_delete_result_button.setEnabled(False)
        self.modal_refit_button.setEnabled(False)
        self.find_peaks_button.setEnabled(False)
        self.modal_local_preview_button.setEnabled(False)
        self.modal_cancel_button.setEnabled(True)
        self.modal_progress.setValue(0)
        self.modal_progress.setFormat("%p%")
        self.modal_progress.show()
        self.modal_identification_status.setText("正在识别模态")
        task.start()

    def _modal_identification_progress(self, value, text):
        self.modal_progress.setValue(value)
        self.modal_identification_status.setText(text)

    def cancel_modal_identification(self):
        if self._candidate_task is not None:
            self._candidate_task.requestInterruption()
            self.modal_cancel_button.setEnabled(False)
        if self._identification_task is not None:
            self._identification_task.requestInterruption()
            self.modal_cancel_button.setEnabled(False)

    def _shutdown_identification(self):
        if self._candidate_task is not None:
            self._candidate_task.requestInterruption()
            self._candidate_task.wait()
        if self._identification_task is not None:
            self._identification_task.requestInterruption()
            self._identification_task.wait()

    def _modal_identification_failed(self, text):
        self.modal_identification_status.setText(text)
        self.modal_identification_details.setPlainText(text)
        self._show_status(text)

    def _modal_identification_finished(self):
        task = self._identification_task
        self._identification_task = None
        if task is not None:
            task.deleteLater()
        self.identify_button.setEnabled(True)
        self.modal_delete_result_button.setEnabled(True)
        self.modal_refit_button.setEnabled(True)
        self.find_peaks_button.setEnabled(True)
        self.modal_local_preview_button.setEnabled(True)
        self.modal_cancel_button.setEnabled(False)
        self.modal_progress.hide()

    def _accept_modal_identification(self, result):
        if self._identification_task is not None and self._identification_task.isInterruptionRequested():
            self._modal_identification_failed("已取消模态识别，保留上次结果")
            return
        if self._identification_generation != self._identification_run_generation:
            self._modal_identification_failed("输入在计算期间已变化，本次结果未应用，请重新识别")
            return
        try:
            current, _ = self._modal_input_snapshot()
            if current.fingerprint != result["fingerprint"]:
                raise ValueError("输入已变化")
        except ValueError:
            self._mark_identification_stale()
            self._modal_identification_failed("输入在计算期间已变化，本次结果未应用，请重新识别")
            return
        if self._identification_run_poles is not None:
            result["poles"] = self._identification_run_poles
            if self._identification_run_diagnostics is not None:
                result["diagnostics"] = self._identification_run_diagnostics
        result["warnings"].extend(self._identification_skipped)
        self._identification_result = result
        self._identification_stale = False
        self._refresh_identification_result_views()
        self.modal_result_tabs.setCurrentIndex(1)
        text = f"{self.modal_direction_combo.currentText()} 力参考：已识别 {len(result['modes'])} 阶模态；驱动点分量为 1"
        if result["warnings"]:
            text += f"\n诊断提示 {len(result['warnings'])} 项"
        details = [text, *result.get("diagnostics", {}).get("lines", []), *result["warnings"]]
        if not result["modes"]:
            self.modal_plot_tabs.setCurrentIndex(1)
        details.extend(f"第 {i + 1} 阶：{m['status']}；相干性：{m['coherence'] if m['coherence'] is not None else '未提供'}"
                       for i, m in enumerate(result["modes"]))
        self.modal_identification_details.setPlainText("\n".join(details))
        self.modal_identification_status.setText(text)
        self.modal_export_result_button.setEnabled(bool(result["modes"]))
        self._show_status(text)

    def _refresh_identification_result_views(self):
        result = self._identification_result
        if result is None:
            return
        self.modal_result_table.blockSignals(True)
        self.modal_result_table.setRowCount(len(result["modes"]))
        for i, mode in enumerate(result["modes"]):
            values = [str(i + 1), f"{mode['frequency_hz']:.6g}", f"{mode['damping_ratio'] * 100:.4g}",
                      f"{mode['mass_kg']:.6g}" if mode["mass_kg"] is not None else "不可可靠估计",
                      f"{mode['fit_error'] * 100:.3g}", mode["status"],
                      f"{mode['stiffness_n_m']:.6g} / {mode['damping_n_s_m']:.6g}" if mode["mass_kg"] is not None else "—"]
            for j, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                self.modal_result_table.setItem(i, j, item)
        self.modal_result_table.blockSignals(False)
        poles = result.get("poles", [])
        self.modal_pole_table.setRowCount(len(poles))
        self.modal_stability_plot.clear()
        self._modal_scatter_points = []
        for stable, color in ((False, "#83909b"), (True, "#11855f")):
            indices = [i for i, p in enumerate(poles) if p["stable"] == stable]
            item = pg.ScatterPlotItem(x=[poles[i]["frequency_hz"] for i in indices], y=[poles[i]["order"] for i in indices],
                                      data=indices, size=8, brush=color, pen=None)
            item.sigClicked.connect(self._modal_pole_clicked)
            self.modal_stability_plot.addItem(item)
            self._modal_scatter_points.append(item)
        self.modal_stability_plot.setLabel("bottom", "频率 (Hz)")
        self.modal_stability_plot.setLabel("left", "模型阶次")
        selected = []
        for i, p in enumerate(poles):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            chosen = p["stable"] and not any(abs(p["frequency_hz"] / old - 1) < result["settings"]["frequency_tolerance"] for old in selected)
            check.setCheckState(QtCore.Qt.Checked if chosen else QtCore.Qt.Unchecked)
            if chosen:
                selected.append(p["frequency_hz"])
            self.modal_pole_table.setItem(i, 0, check)
            for j, value in enumerate((p["order"], f"{p['frequency_hz']:.6g}", f"{p['damping_ratio'] * 100:.4g}", p["stable_count"], p.get("stability_reason", "稳定" if p["stable"] else "旧会话未记录")), 1):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.modal_pole_table.setItem(i, j, item)
        self.modal_validation_channel.blockSignals(True)
        self.modal_validation_channel.clear()
        for m in result["measurements"]:
            self.modal_validation_channel.addItem(m["label"])
        self.modal_validation_channel.setCurrentIndex(result["driving_index"])
        self.modal_validation_channel.blockSignals(False)
        n = len(result["modes"])
        self.modal_mac_table.setRowCount(n)
        self.modal_mac_table.setColumnCount(n)
        for i in range(n):
            for j in range(n):
                self.modal_mac_table.setItem(i, j, QtWidgets.QTableWidgetItem(f"{result['automac'][i,j]:.3f}"))
        self._draw_modal_validation()
        self._draw_modal_cmif()
        if n:
            self.modal_result_table.selectRow(0)

    def _draw_modal_cmif(self):
        result = self._identification_result
        if result is None:
            return
        f, h = result["frequency_hz"], result["measured"]
        cmif = 20 * np.log10(np.maximum(np.linalg.norm(h * -(2 * np.pi * f[:, None]) ** 2, axis=1), 1e-300))
        for item in list(self.frf_plot.listDataItems()):
            if item.name() == "CMIF（单输入）":
                self.frf_plot.removeItem(item)
        self.frf_plot.plot(f, cmif, pen=pg.mkPen("#cf7d1c", width=1.5), name="CMIF（单输入）")
        self._plot_curves.setdefault(self.frf_plot, {})["CMIF（单输入）"] = (f, cmif)
        self.frf_plot.setTitle("模态 FRF / CMIF" + (" · 结果待更新" if self._identification_stale else ""))

    def _modal_pole_clicked(self, _item, points):
        if not points:
            return
        row = int(points[0].data())
        item = self.modal_pole_table.item(row, 0)
        item.setCheckState(QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)
        self.modal_pole_table.selectRow(row)

    def _refit_selected_modal_poles(self):
        if self._identification_stale or self._identification_result is None:
            self._show_status("输入已变化，请重新识别")
            return
        poles = self._identification_result.get("poles", [])
        selected = [p for i, p in enumerate(poles) if self.modal_pole_table.item(i, 0).checkState() == QtCore.Qt.Checked]
        if not selected:
            self._show_status("请在稳定图或极点表选择要拟合的极点")
            return
        frequencies = sorted(p["frequency_hz"] for p in selected)
        if any(abs(a / b - 1) < .001 for a, b in zip(frequencies[1:], frequencies[:-1])):
            self._show_status("同一稳定列只选一个极点，请取消重复选择")
            return
        self.start_modal_identification(selected)

    def _draw_modal_validation(self, *_args):
        result = self._identification_result
        index = self.modal_validation_channel.currentIndex()
        if result is None or index < 0:
            return
        if result.get("reconstructed") is None:
            self._clear_plot_widget(self.modal_fit_plot)
            self._clear_plot_widget(self.modal_phase_plot)
            self.modal_validation_label.setText("尚无拟合结果")
            return
        f = result["frequency_hz"]
        for plot, phase in ((self.modal_fit_plot, False), (self.modal_phase_plot, True)):
            curves = [CurvePair(label, f, np.angle(result[key][:, index], deg=True) if phase else abs(result[key][:, index]))
                      for key, label in (("measured", "实测"), ("reconstructed", "重构"))]
            self._plot_curves_on_widget(plot, curves,
                title=("相位校核" if phase else "FRF 重构校核") + (" · 结果待更新" if self._identification_stale else ""),
                x_label="频率 (Hz)", y_label="相位 (°)" if phase else "位移/力 (m/N)", log_y=not phase)
        error = result["channel_errors"][index]
        self.modal_validation_label.setText(f"通道拟合误差：{100 * error:.3g}% | 质量归一化：驱动点分量为 1")

    def _select_identified_mode(self):
        if self._identification_result is None:
            return
        row = self.modal_result_table.currentRow()
        if row < 0 or row >= len(self._identification_result["modes"]):
            return
        self._show_identified_mode(row)

    def _show_identified_mode(self, index):
        result = self._identification_result
        mode = result["modes"][index]
        groups = {}
        for m, value in zip(result["measurements"], mode["shape"]):
            point = groups.setdefault(m["point"], {"coords": m["coordinates"], "disp": np.zeros(3, dtype=complex), "available": [False] * 3})
            point["disp"][m["axis"]] = value
            point["available"][m["axis"]] = True
        labels = list(groups)
        coords = np.array([groups[p]["coords"] for p in labels])
        displacement = np.array([groups[p]["disp"] for p in labels])
        span = max(float(np.max(np.ptp(coords, axis=0))), 1.0)
        base_scale = span * .15 / max(float(np.max(abs(displacement))), 1e-300)
        self.last_mode = {"requested_frequency": mode["frequency_hz"], "actual_frequency": mode["frequency_hz"],
                          "coords": coords, "disp_complex": displacement, "displacements": displacement.real.copy(),
                          "labels": labels, "base_scale": base_scale, "scale": base_scale * self._mode_display_gain(),
                          "lines": self._line_rows(require_enabled=True), "identified": True,
                          "available_axes": [groups[p]["available"] for p in labels], "result_index": index}
        self._preview_timer.stop()
        self._render_layout(coords, labels)
        self._render_mode(self.last_mode, phase=0)

    def _delete_identified_modes(self):
        if self._identification_result is None or self._identification_stale:
            return
        removed = {index.row() for index in self.modal_result_table.selectedIndexes()}
        keep = [i for i in range(len(self._identification_result["modes"])) if i not in removed]
        if not removed:
            return
        # Refit the remaining poles so reconstruction and residuals match the table.
        if keep:
            selected = [{"frequency_hz": self._identification_result["modes"][i]["frequency_hz"],
                         "damping_ratio": self._identification_result["modes"][i]["damping_ratio"],
                         "order": self._identification_result["modes"][i]["source_order"]} for i in keep]
            self.start_modal_identification(selected)
        else:
            self._identification_result["modes"] = []
            self._identification_result["automac"] = np.empty((0, 0))
            self._identification_result["reconstructed"] = None
            self._preview_timer.stop()
            self.last_mode = None
            self.modal_fit_plot.clear()
            self.modal_phase_plot.clear()
            self._refresh_identification_result_views()
            self.modal_export_result_button.setEnabled(False)

    def _choose_export_identification(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出模态结果", str(self._modal_dialog_directory("export") / "modal_results.csv"), "CSV (*.csv)")
        if path:
            try:
                self.export_identification_results(path)
                self._remember_modal_dialog_path(path, "export")
            except (ValueError, OSError) as exc:
                self._show_status(str(exc))

    def export_identification_results(self, path):
        self._require_current_identification()
        result = self._identification_result
        driver = result["measurements"][result["driving_index"]]
        with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["fingerprint", result["fingerprint"], "normalization", "driving_point_unity", "driver", driver["label"],
                             "force_axis", "XYZ"[driver["force_axis"]] if driver.get("force_axis") is not None else "unspecified"])
            writer.writerow(["mode", "frequency_hz", "damping_ratio", "mass_kg", "stiffness_n_m", "damping_n_s_m", "fit_error", "status", "coherence"])
            for i, mode in enumerate(result["modes"], 1):
                writer.writerow([i] + [mode[key] for key in ("frequency_hz", "damping_ratio", "mass_kg", "stiffness_n_m", "damping_n_s_m", "fit_error", "status", "coherence")])
        return Path(path)

    def _require_current_identification(self):
        if not self._identification_stale and self._identification_result is not None:
            try:
                data, _ = self._modal_input_snapshot()
                if data.fingerprint == self._identification_result["fingerprint"]:
                    return
            except ValueError:
                pass
        self._mark_identification_stale()
        raise ValueError("模态结果待更新，请重新识别后导出或预览")

    def _identification_session_state(self):
        self._capture_identification_file_options()
        if self._identification_result is not None:
            try:
                self._require_current_identification()
            except ValueError:
                pass
        return {"schema_version": 1, "files": self._identification_file_options,
                "controls": {name: box.value() for name, box in self._identification_spins().items()},
                "driver": self.modal_driver_combo.currentText(), "direction": self.modal_direction_combo.currentIndex(),
                "force_confirm": self.modal_force_confirm.isChecked(), "driver_confirm": self.modal_driver_confirm.isChecked(),
                "selected_result": self.modal_result_table.currentRow(),
                "stale": self._identification_stale, "result": self._identification_result}

    def _restore_identification_session(self, state):
        self._identification_result = None
        self._identification_data = None
        self._identification_stale = True
        self.modal_result_table.setRowCount(0)
        self.modal_pole_table.setRowCount(0)
        self._identification_file_options = dict(state.get("files", {}))
        # Discard old widgets before refresh, otherwise they overwrite restored settings.
        self.modal_input_table.setRowCount(0)
        self._refresh_identification_sources()
        self._identification_refreshing = True
        try:
            for name, value in state.get("controls", {}).items():
                if name in self._identification_spins():
                    getattr(self, name).setValue(value)
            self.modal_driver_combo.setCurrentText(state.get("driver", "请选择"))
            self.modal_direction_combo.setCurrentIndex(state.get("direction", 0))
            self.modal_force_confirm.setChecked(state.get("force_confirm", False))
            self.modal_driver_confirm.setChecked(state.get("driver_confirm", False))
        finally:
            self._identification_refreshing = False
        self._sync_modal_frequency_controls()
        self._sync_modal_physical_confirmation()
        result = state.get("result")
        if result is not None:
            self._identification_result = result
            try:
                data, _ = self._modal_input_snapshot()
                self._identification_stale = bool(state.get("stale", True)) or data.fingerprint != result["fingerprint"]
                self._identification_data = data
            except ValueError:
                self._identification_stale = True
            self._refresh_identification_result_views()
            selected = state.get("selected_result", 0)
            if 0 <= selected < len(result["modes"]):
                self.modal_result_table.selectRow(selected)
        self.modal_identification_status.setText("结果待更新" if self._identification_stale else "已恢复模态识别结果；驱动点分量为 1")
        self.modal_export_result_button.setEnabled(result is not None and not self._identification_stale and bool(result["modes"]))
