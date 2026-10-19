"""Candidate search and explicitly non-authoritative local modal previews."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets

from python_vna.diagnostic.modal_candidates import CandidateSettings, METHOD_NAMES, prepare_candidate_input, search_modal_candidates
from python_vna.diagnostic.modal_identification import ModalCancelled, fit_local_preview


class ModalCandidateTask(QtCore.QThread):
    resultReady = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progressChanged = QtCore.Signal(int, str)

    def __init__(self, data, target=None, parent=None):
        super().__init__(parent)
        self.data, self.target = data, target

    def run(self):
        try:
            kwargs = {"cancel": self.isInterruptionRequested, "progress": self.progressChanged.emit}
            result = search_modal_candidates(self.data, **kwargs) if self.target is None else fit_local_preview(self.data, self.target, **kwargs)
            self.resultReady.emit(result)
        except ModalCancelled:
            self.failed.emit("已取消，保留上次结果")
        except Exception as exc:
            self.failed.emit(str(exc))


class ModalCandidateMixin:
    def _init_candidate_search(self):
        self._candidate_search_result = None
        self._candidate_task = None
        self._candidate_generation = 0

    def _build_candidate_search_controls(self, layout, *, collapsible=True):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("候选算法"))
        self.modal_candidate_method = QtWidgets.QComboBox()
        for key, text in METHOD_NAMES.items():
            self.modal_candidate_method.addItem(text, key)
        self.modal_candidate_method.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        row.addWidget(self.modal_candidate_method, 1)
        layout.addLayout(row)
        group = QtWidgets.QGroupBox("候选搜索参数")
        group.setCheckable(collapsible)
        group.setChecked(not collapsible)
        group_layout = QtWidgets.QVBoxLayout(group)
        content = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        self.modal_candidate_prominence = self._modal_decimal(1, 0, 100, 2)
        self.modal_candidate_spacing = self._modal_decimal(0, 0, 1e6, 4)
        self.modal_candidate_band = self._modal_decimal(0, 0, 1e9, 4)
        self.modal_candidate_band.setSpecialValueText("自动")
        self.modal_candidate_count = QtWidgets.QSpinBox()
        self.modal_candidate_count.setRange(1, 200)
        self.modal_candidate_count.setValue(24)
        self.modal_candidate_order = QtWidgets.QSpinBox()
        self.modal_candidate_order.setRange(6, 40)
        self.modal_candidate_order.setSingleStep(2)
        self.modal_candidate_order.setValue(16)
        for title, control in (("突出度下限 dB", self.modal_candidate_prominence),
                               ("最小间距 Hz", self.modal_candidate_spacing), ("分频段宽度 Hz", self.modal_candidate_band),
                               ("候选上限", self.modal_candidate_count), ("候选极点最大阶次", self.modal_candidate_order)):
            control.setKeyboardTracking(False)
            form.addRow(title, control)
            control.editingFinished.connect(self._invalidate_candidate_search)
        group_layout.addWidget(content)
        if collapsible:
            content.hide()
            group.toggled.connect(content.setVisible)
        layout.addWidget(group)
        self.modal_candidate_method.currentIndexChanged.connect(self._invalidate_candidate_search)
        self.modal_candidate_status = QtWidgets.QLabel("候选尚未搜索")
        self.modal_candidate_status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self.modal_candidate_status)
        actions = QtWidgets.QHBoxLayout()
        self.modal_local_preview_button = QtWidgets.QPushButton("局部拟合预览")
        self.modal_local_preview_button.setToolTip("局部单模态拟合，仅供预览，不计算模态质量；失败通道不会用原始 FRF 替代留数")
        self.modal_export_candidate_button = QtWidgets.QPushButton("导出候选")
        actions.addWidget(self.modal_local_preview_button)
        actions.addWidget(self.modal_export_candidate_button)
        layout.addLayout(actions)
        self.modal_local_preview_button.clicked.connect(lambda: self._start_candidate_task(preview=True))
        self.modal_export_candidate_button.clicked.connect(self._choose_export_candidates)

    def _candidate_settings(self):
        return CandidateSettings(method=self.modal_candidate_method.currentData(),
            minimum_hz=self.modal_frequency_min.value(), maximum_hz=self.modal_frequency_max.value(),
            prominence_db=self.modal_candidate_prominence.value(), minimum_spacing_hz=self.modal_candidate_spacing.value(),
            maximum_candidates=self.modal_candidate_count.value(), band_width_hz=self.modal_candidate_band.value(),
            maximum_order=self.modal_candidate_order.value())

    def _candidate_input_snapshot(self):
        self._capture_identification_file_options()
        curves = []
        mapped_axes = self._mapped_modal_force_axes()
        for row in self._point_rows(require_enabled=True, require_bound=True):
            dataset = self._dataset_by_name(str(row["file"]))
            if dataset is None:
                continue
            file = next(f for f in self.files if f.dataset is dataset)
            if not self._modal_reference_matches(file, mapped_axes):
                continue
            force = self._identification_file_options.get(str(file.path), {}).get("force_key", "")
            for axis in "xyz":
                number = int(row[f"{axis}_ch"])
                if number <= 0:
                    continue
                aliases = self._response_aliases(dataset, number)
                keys = [key for key in dataset.frf if "->" in key and key.split("->")[1] in aliases
                        and (not force or key.split("->")[0] == force)]
                if len(keys) > 1:
                    raise ValueError(f"{row['point']}-{axis.upper()}：存在多个力参考，请先在识别设置选择力通道")
                if not keys:
                    continue
                curves.append({"label": f"{row['point']}-{axis.upper()} | {file.path.name} | {keys[0]}",
                               "frequency_hz": np.asarray(dataset.frequency_hz).copy(),
                               "values": np.asarray(dataset.frf[keys[0]]).copy() * float(row[f"{axis}_scale"])})
        return prepare_candidate_input(curves, self._candidate_settings())

    def _invalidate_candidate_search(self, *_args):
        if getattr(self, "_restoring_modal_session", False):
            return
        self._candidate_generation += 1
        if self._candidate_search_result is not None:
            self._candidate_search_result["stale"] = True
            self._refresh_candidate_list()
        if hasattr(self, "modal_candidate_status"):
            self.modal_candidate_status.setText("候选待更新")

    def _on_candidate_search(self, *_args):
        if self._candidate_settings().method in {"cmif", "prominence"}:
            self.find_peaks()
        else:
            self._start_candidate_task()

    def _find_modal_candidates(self):
        if self._candidate_task is not None or self._identification_task is not None:
            self._show_status("请先完成或取消当前模态任务")
            return list(self._auto_peaks)
        try:
            result = search_modal_candidates(self._candidate_input_snapshot())
        except (ValueError, TypeError) as exc:
            self._candidate_failure(str(exc))
            return []
        self._apply_candidate_result(result)
        return list(self._auto_peaks)

    def _start_candidate_task(self, *, preview=False):
        if self._candidate_task is not None or self._identification_task is not None:
            self._show_status("请先完成或取消当前模态任务")
            return
        try:
            data = self._modal_input_snapshot()[0] if preview else self._candidate_input_snapshot()
        except (ValueError, TypeError) as exc:
            self._candidate_failure(str(exc))
            return
        self._candidate_run_generation = (self._candidate_generation, self._identification_generation)
        task = ModalCandidateTask(data, float(self.frequency_edit.value()) if preview else None, self)
        self._candidate_task = task
        task.resultReady.connect(self._accept_candidate_task)
        task.failed.connect(self._candidate_failure)
        task.progressChanged.connect(self._candidate_progress)
        task.finished.connect(self._finish_candidate_task)
        for button in (self.identify_button, self.find_peaks_button, self.modal_local_preview_button, self.modal_refit_button):
            button.setEnabled(False)
        self.modal_cancel_button.setEnabled(True)
        self.modal_progress.setValue(0)
        self.modal_progress.setFormat(("局部预览 " if preview else "候选搜索 ") + "%p%")
        self.modal_progress.show()
        task.start()

    def _candidate_progress(self, value, text):
        self.modal_progress.setValue(value)
        self.modal_candidate_status.setText(text)
        self.modal_candidate_status.setToolTip(text)

    def _accept_candidate_task(self, result):
        if self._candidate_task.isInterruptionRequested():
            self._candidate_failure("已取消，保留上次结果")
            return
        if self._candidate_run_generation != (self._candidate_generation, self._identification_generation):
            self._candidate_failure("输入或候选参数已变化，本次结果未应用")
            return
        if result.get("preview_only") and self.frequency_edit.value() != self._candidate_task.target:
            self._candidate_failure("预览目标频率已变化，本次结果未应用")
            return
        try:
            current = self._modal_input_snapshot()[0] if result.get("preview_only") else self._candidate_input_snapshot()
            fingerprint = current.fingerprint if result.get("preview_only") else current["fingerprint"]
            if fingerprint != result["fingerprint"]:
                raise ValueError("输入已变化，本次结果未应用")
        except ValueError as exc:
            self._candidate_failure(str(exc))
            return
        if result.get("preview_only"):
            self._show_local_fit_preview(result, current)
        else:
            self._apply_candidate_result(result)

    def _finish_candidate_task(self):
        task, self._candidate_task = self._candidate_task, None
        if task is not None:
            task.deleteLater()
        for button in (self.identify_button, self.find_peaks_button, self.modal_local_preview_button, self.modal_refit_button):
            button.setEnabled(True)
        self.modal_cancel_button.setEnabled(False)
        self.modal_progress.hide()

    def _candidate_failure(self, text):
        self.modal_candidate_status.setText(text)
        self.modal_candidate_status.setToolTip(text)
        self.modal_identification_details.setPlainText(text)
        self._show_status(text)

    def _apply_candidate_result(self, result):
        self._candidate_search_result = result
        self._auto_peaks = [r["frequency_hz"] for r in result["candidates"]]
        if self._auto_peaks:
            self._active_frequency = self._auto_peaks[0]
            self.frequency_edit.setValue(self._active_frequency)
            self._invalidate_mode(identification_changed=False)
        self._refresh_candidate_list()
        self.modal_result_tabs.setCurrentIndex(0)
        frequency, magnitude = self._aggregate_frf_curve()
        if frequency.size:
            self._render_frf_candidates(frequency, magnitude)
        self._draw_candidate_indicator()
        message = f"{METHOD_NAMES[result['settings']['method']]}：{len(self._auto_peaks)} 个候选（非正式模态）"
        self.modal_candidate_status.setText(message)
        self.modal_candidate_status.setToolTip(message + "\n" + "\n".join(result["warnings"]))
        self.modal_identification_details.setPlainText("\n".join([message, *result["warnings"], *[
            f"{r['frequency_hz']:.8g} Hz | {' + '.join(r['methods'])} | 连续稳定阶次 {r['stable_orders']}"
            for r in result["candidates"]]]))
        self._show_status(message + ("；" + "；".join(result["warnings"]) if result["warnings"] else ""))

    def _draw_candidate_indicator(self):
        result = self._candidate_search_result
        if result is None:
            return
        for item in list(self.frf_plot.listDataItems()):
            if item.name() == "CMIF 候选指标（归一化）":
                self.frf_plot.removeItem(item)
        if result["stale"]:
            return
        f, db = result["frequency_hz"], result["indicator_db"]
        self.frf_plot.plot(f, db, name="CMIF 候选指标（归一化）", pen={"color": "#b45a00", "width": 1.8})
        self._plot_curves.setdefault(self.frf_plot, {})["CMIF 候选指标（归一化）"] = (f, db)

    def _candidate_description(self, frequency):
        result = self._candidate_search_result
        if result is None:
            return "", ""
        row = next((r for r in result["candidates"] if np.isclose(r["frequency_hz"], frequency, rtol=1e-7)), None)
        if row is None:
            return "", ""
        state = "待更新" if result["stale"] else ("交叉支持" if row["confirmed"] else "候选")
        sources = " + ".join(row["methods"])
        bands = "；".join(f"{lo:.5g}–{hi:.5g} Hz" for lo, hi in row["bands"])
        tooltip = f"{state} | {sources}\n连续稳定阶次：{row['stable_orders']}\n频段：{bands or '实测频段'}\n" + "\n".join(result["labels"][j] for j in row["channels"])
        return f"[{state} · {sources}]", tooltip

    def _show_local_fit_preview(self, result, data):
        self.modal_result_tabs.setCurrentIndex(0)
        groups = {}
        for measurement, value, good in zip(data.measurements, result["coefficients"], result["accepted"]):
            if not good:
                continue
            point = groups.setdefault(measurement.point, {"coords": measurement.coordinates, "disp": np.zeros(3, complex), "axes": [False] * 3})
            point["disp"][measurement.axis] = value
            point["axes"][measurement.axis] = True
        coords = np.array([p["coords"] for p in groups.values()])
        displacement = np.array([p["disp"] for p in groups.values()])
        reference = displacement.ravel()[np.argmax(abs(displacement))]
        if abs(reference) <= 1e-300:
            self._candidate_failure("局部留数为零，无法预览；保留上次图像")
            return
        displacement /= reference
        span = max(float(np.max(np.ptp(coords, axis=0))), 1.0)
        base = span * .15
        self.last_mode = {"requested_frequency": result["frequency_hz"], "actual_frequency": result["frequency_hz"],
            "coords": coords, "labels": list(groups), "disp_complex": displacement, "displacements": displacement.real.copy(),
            "base_scale": base, "scale": base * self._mode_display_gain(), "lines": self._line_rows(require_enabled=True),
            "preview_only": True, "local_fit": result, "available_axes": [p["axes"] for p in groups.values()]}
        self._preview_timer.stop()
        self._render_mode(self.last_mode, phase=0)
        self.preview_tabs.setCurrentWidget(self.mode_preview_tab)
        text = f"局部拟合预览：{result['frequency_hz']:.6g} Hz；最大通道误差 {100 * max(result['errors']):.3g}%；不计算质量"
        self.modal_candidate_status.setText(text)
        self.modal_candidate_status.setToolTip(text)
        self.modal_identification_details.setPlainText("\n".join([text, *result["warnings"]]))
        self._show_status(text)

    def _require_current_local_preview(self):
        mode = self.last_mode
        if mode is None or not mode.get("preview_only"):
            return
        data, _ = self._modal_input_snapshot()
        if data.fingerprint != mode["local_fit"]["fingerprint"]:
            raise ValueError("局部预览输入已变化，请重新拟合")

    def _choose_export_candidates(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出候选及来源", str(self._last_directory / "modal_candidates.csv"), "CSV (*.csv)")
        if path:
            try:
                self.export_modal_candidates(path)
            except (ValueError, OSError) as exc:
                self._candidate_failure(str(exc))

    def export_modal_candidates(self, path):
        result = self._candidate_search_result
        if result is None or result["stale"] or self._candidate_input_snapshot()["fingerprint"] != result["fingerprint"]:
            raise ValueError("候选待更新，请重新搜索后导出")
        with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["fingerprint", result["fingerprint"], "type", "modal_candidates_not_identified_modes"])
            writer.writerow(["frequency_hz", "methods", "cross_checked", "stable_orders", "damping_ratio", "channels", "bands_hz"])
            for row in result["candidates"]:
                writer.writerow([row["frequency_hz"], " + ".join(row["methods"]), row["confirmed"], row["stable_orders"], row["damping_ratio"],
                                 " | ".join(result["labels"][j] for j in row["channels"]), str(row["bands"])])
        return Path(path)

    def _candidate_session_state(self):
        if self._candidate_search_result is not None:
            try:
                if self._candidate_input_snapshot()["fingerprint"] != self._candidate_search_result["fingerprint"]:
                    self._candidate_search_result["stale"] = True
            except ValueError:
                self._candidate_search_result["stale"] = True
        return {"settings": asdict(self._candidate_settings()), "result": self._candidate_search_result}

    def _restore_candidate_session(self, state):
        settings = state.get("settings", {})
        index = self.modal_candidate_method.findData(settings.get("method", "cmif"))
        self.modal_candidate_method.setCurrentIndex(max(index, 0))
        for key, widget in (("prominence_db", self.modal_candidate_prominence), ("minimum_spacing_hz", self.modal_candidate_spacing),
                            ("band_width_hz", self.modal_candidate_band), ("maximum_candidates", self.modal_candidate_count),
                            ("maximum_order", self.modal_candidate_order)):
            widget.setValue(settings.get(key, getattr(CandidateSettings(), key)))
        self._candidate_search_result = state.get("result")
        if self._candidate_search_result is not None:
            try:
                self._candidate_search_result["stale"] |= self._candidate_input_snapshot()["fingerprint"] != self._candidate_search_result["fingerprint"]
            except ValueError:
                self._candidate_search_result["stale"] = True
            self._refresh_candidate_list()
            self._draw_candidate_indicator()
