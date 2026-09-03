from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading

import numpy as np

from python_vna.analysis_derivation import (
    DERIVE_BASE_TO_TOP,
    DERIVE_TOP_TO_BASE,
    diagonal_psd_matrix,
    derive_psd_from_transfer,
    derive_time_from_transfer,
    has_complex_transfer_phase,
    interpolate_complex_transfer,
    invert_mimo_input_psd,
    predict_mimo_response_psd,
    psd_matrix_diagonal,
    synthesize_time_from_psd_matrix,
    solve_mimo_independent_psd,
    synthesize_time_from_psd,
)
from python_vna.analysis_algorithms import (
    FilterConfig,
    apply_filter_to_signal,
    apply_time_window,
    compute_cumulative_spectrum,
    compute_dynamic_stiffness,
    compute_coherence_welch,
    compute_cross_spectrum_periodogram,
    compute_cross_spectrum_welch,
    compute_mimo_transfer_function_welch,
    compute_periodogram_psd,
    compute_third_octave_velocity_rms,
    compute_transfer_function_welch,
    compute_welch_psd,
    convert_acceleration_psd,
    convert_acceleration_time_series,
    crop_signal_edges,
    normalize_quantity_mode,
    quantity_cumulative_label,
    quantity_psd_label,
    quantity_time_label,
    third_octave_bands,
)
from python_vna.analysis_curve_editing import (
    apply_db_magnitude_profile,
    apply_power_db_profile,
    log_frequency_grid,
    sample_curve_as_db_points,
    stitch_frequency_curves,
    transfer_from_db_points,
)
from python_vna.analysis_data import (
    AnalysisDataset,
    AnalysisSeries,
    dataset_from_measurement,
    load_continuous_channels,
    load_analysis_path,
)
from python_vna.diagnostics import append_log
from python_vna.diagnostic.processing_workflow import (
    CurveDescriptor,
    ProcessingIssue,
    ProcessingRecipe,
    ValidationReport,
    parse_optional_number,
    validate_control_points,
    validate_processing_task,
)
from python_vna.display_transforms import transform_legacy_autospectrum
from python_vna.optional import require
from python_vna.ui.plot_interactions import (
    DataTipPoint,
    DataTipText,
    VnaAxisItem,
    VnaViewBox,
    _apply_text_item_style,
    _cursor_palette_for_background,
    _data_tip_anchor_for_label_drag,
    copy_widget_image_to_clipboard,
)
from python_vna.ui.legend_placement import place_legend_away_from_curves
from python_vna.ui.diagnostic_theme import (
    LIGHT_TRACE_COLORS,
    VC_REFERENCE_COLORS as SHARED_VC_REFERENCE_COLORS,
    apply_plot_legend_theme,
    build_diagnostic_stylesheet,
    color_for_trace_name,
    color_map_for_trace_names,
    default_diagnostic_theme,
    set_button_role,
    trace_colors_for_theme,
)

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")
shiboken6 = require("shiboken6", "python -m pip install -e .[gui]")
pg = require("pyqtgraph", "python -m pip install -e .[gui]")


TRACE_COLORS = list(LIGHT_TRACE_COLORS)

TEXT_FILE_FS_HINT_HZ = 1000.0
VC_REFERENCE_NAMES = ("VC A", "VC B", "VC C", "VC D", "VC E", "VC F")
VC_REFERENCE_LEVELS_UM_S = {
    "VC A": 50.0,
    "VC B": 25.0,
    "VC C": 12.5,
    "VC D": 6.25,
    "VC E": 3.125,
    "VC F": 1.5625,
}
VC_REFERENCE_COLORS = dict(SHARED_VC_REFERENCE_COLORS)


@dataclass
class PlotCurveInfo:
    curve_id: int
    label: str
    curve_type: str
    source: str
    removable: bool = True
    exportable: bool = True


@dataclass
class WorkspaceCurve:
    curve_id: int
    name: str
    curve_type: str
    frequency_hz: np.ndarray
    values: np.ndarray
    source: str


class EditableControlPoint(DataTipPoint):
    def __init__(self, *args, on_drag_started=None, on_drag_finished=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_drag_started = on_drag_started
        self._on_drag_finished = on_drag_finished

    def mouseDragEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton and event.isStart() and self._on_drag_started is not None:
            self._on_drag_started()
        super().mouseDragEvent(event)
        if event.isAccepted() and event.isFinish() and self._on_drag_finished is not None:
            self._on_drag_finished()


class AnalysisDataStore(QtCore.QObject):
    changed = QtCore.Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.datasets: list[AnalysisDataset] = []
        self.next_dataset_id = 1
        self.custom_series_labels: dict[tuple[int, int], str] = {}
        self.custom_series_scales: dict[tuple[int, int], float] = {}
        self.original_series_scales: dict[tuple[int, int], float] = {}
        self.current_measurement_dataset_id: int | None = None


class AnalysisLoadSignals(QtCore.QObject):
    item_finished = QtCore.Signal(int, object, object)
    finished = QtCore.Signal(bool)


class AnalysisLoadTask(QtCore.QRunnable):
    def __init__(self, paths: list[Path], *, first_dataset_id: int, import_kind: str | None):
        super().__init__()
        self.paths = list(paths)
        self.first_dataset_id = int(first_dataset_id)
        self.import_kind = import_kind
        self.signals = AnalysisLoadSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @QtCore.Slot()
    def run(self) -> None:
        cancelled = False
        for index, path in enumerate(self.paths):
            if self._cancelled.is_set():
                cancelled = True
                break
            try:
                dataset = load_analysis_path(
                    path,
                    fs_hint=TEXT_FILE_FS_HINT_HZ,
                    dataset_id=self.first_dataset_id + index,
                    import_kind=self.import_kind,
                )
                error = None
            except Exception as exc:
                dataset = None
                error = str(exc)
            self.signals.item_finished.emit(index, dataset, error)
        self.signals.finished.emit(cancelled or self._cancelled.is_set())


class AnalysisWorkbench(QtWidgets.QWidget):
    def __init__(
        self,
        parent=None,
        *,
        theme: dict[str, object] | None = None,
        current_measurement_provider=None,
        derived_only: bool = False,
        include_derived_tab: bool = True,
        data_store: AnalysisDataStore | None = None,
    ):
        super().__init__(parent)
        self._derived_only = bool(derived_only)
        self._include_derived_tab = bool(include_derived_tab) or self._derived_only
        self.setWindowTitle("VNA 换算工具" if self._derived_only else "Analysis Viewer")
        self.resize(980, 720)
        self._status_bar = QtWidgets.QStatusBar(self)
        self._data_store = data_store if data_store is not None else AnalysisDataStore(self)
        self._current_measurement_provider = current_measurement_provider
        self._time_series_cache: dict[tuple[int, str, float | None, float | None, int | None], tuple[np.ndarray, np.ndarray]] = {}
        self._bulk_time_series_cache: dict[tuple[int, float | None, float | None, int | None], tuple[np.ndarray, dict[str, np.ndarray]]] = {}
        self._selected_channel_keys_by_dataset: dict[int, set[str]] = {}
        self._theme = dict(theme or {})
        self._last_directory = Path.cwd()
        self._suspend_auto_plot = False
        self._plot_curves: dict[pg.PlotWidget, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        self._plot_curve_info: dict[pg.PlotWidget, dict[str, PlotCurveInfo]] = {}
        self._next_plot_curve_id = 1
        self._active_trace: dict[pg.PlotWidget, str | None] = {}
        self._active_plot: pg.PlotWidget | None = None
        self._data_tip_items: dict[pg.PlotWidget, list[dict[str, object]]] = {}
        self._data_tip_enabled = False
        self._suppress_next_plot_context_menu = False
        self._cursor_enabled = True
        self._cursor_items: dict[pg.PlotWidget, dict[str, object]] = {}
        self._cursor_positions: dict[pg.PlotWidget, tuple[float, float] | None] = {}
        self._axis_history: dict[pg.PlotWidget, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
        self._axis_scaling_plot: pg.PlotWidget | None = None
        self._log_modes: dict[pg.PlotWidget, tuple[bool, bool]] = {}
        self._plot_export_excluded: dict[pg.PlotWidget, set[str]] = {}
        self._plot_curve_kind: dict[pg.PlotWidget, str] = {}
        self._time_curve_psd_sources: dict[
            pg.PlotWidget,
            dict[str, tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray] | None]],
        ] = {}
        self._series_labels: dict[str, str] = {}
        self._rename_edit_autofill_text = ""
        self._readme_panel_restore_size: QtCore.QSize | None = None
        self._readme_panel_restore_minimum_size: QtCore.QSize | None = None
        self._derived_result_cache: dict[tuple[object, ...], tuple[object, ...]] = {}
        self._last_derived_results: list[dict[str, object]] | None = None
        self._delete_in_progress = False
        self._clear_plots_pending = False
        self._single_plot_windows: list[QtWidgets.QDialog] = []
        self._manual_transfer_points: tuple[np.ndarray, np.ndarray] = (
            np.array([10.0, 100.0], dtype=float),
            np.array([0.0, 0.0], dtype=float),
        )
        self._transfer_edit_points: dict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = {}
        self._psd_edit_points: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._curve_point_edit_mode = "transfer"
        self._active_psd_edit_label: str | None = None
        self._curve_edit_items: dict[pg.PlotWidget, list[object]] = {}
        self._updating_transfer_point_table = False
        self._workspace_curves: list[WorkspaceCurve] = []
        self._next_workspace_curve_id = 1
        self._workspace_operation_sources: dict[str, object | None] = {"a": None, "b": None}
        self._interpolation_resolution_hz = 1.0
        self._interpolation_resolution_s = 0.001
        self._last_time_pair_transfer_description: str | None = None
        self._mimo_current_grid: np.ndarray | None = None
        self._derived_results_stale = True
        self._derived_stale_reason = "尚未计算"
        self._derived_cancel_requested = False
        self._last_processing_recipe: ProcessingRecipe | None = None
        self._last_processing_report: ValidationReport | None = None
        self._last_load_report: list[tuple[str, str, str]] = []
        self._curve_edit_undo: list[tuple[str, object, np.ndarray, np.ndarray]] = []
        self._curve_edit_redo: list[tuple[str, object, np.ndarray, np.ndarray]] = []
        self._restoring_curve_edit = False
        self._background_load_task: AnalysisLoadTask | None = None
        self._background_load_progress: QtWidgets.QProgressDialog | None = None
        self._background_load_rows: list[tuple[str, str, str]] = []
        self._background_load_paths: list[Path] = []
        self._build_ui()
        self.apply_theme(self._theme)
        self._data_store.changed.connect(self._on_shared_data_store_changed)

    def statusBar(self):
        return self._status_bar

    def _notify_data_store_changed(self, reason: str, payload: object | None = None) -> None:
        append_log(f"analysis.notify.begin reason={reason} payload_self={payload is self}")
        self._data_store.changed.emit(reason, payload)
        append_log(f"analysis.notify.end reason={reason}")

    def _notify_data_store_changed_later(self, reason: str, payload: object | None = None) -> None:
        append_log(f"analysis.notify_later.schedule reason={reason}")
        QtCore.QTimer.singleShot(
            0,
            lambda: self._notify_data_store_changed(reason, payload) if _qt_object_is_valid(self) else None,
        )

    def _on_shared_data_store_changed(self, reason: str, payload: object) -> None:
        append_log(f"analysis.shared.begin reason={reason} payload_self={payload is self}")
        if payload is self:
            append_log(f"analysis.shared.skip_self reason={reason}")
            return
        previous_suspend = self._suspend_auto_plot
        self._suspend_auto_plot = True
        self._time_series_cache.clear()
        self._bulk_time_series_cache.clear()
        self._derived_result_cache.clear()
        self._last_derived_results = None
        try:
            if reason in {"delete", "clear"}:
                append_log(f"analysis.shared.minimal_refresh.begin reason={reason}")
                self._refresh_dataset_series_list_only()
                append_log(f"analysis.shared.minimal_refresh.end reason={reason}")
            else:
                self._refresh_dataset_lists()
                self._sync_workspace_operation_labels()
        finally:
            self._suspend_auto_plot = previous_suspend
        if self._derived_only:
            self._mark_derived_results_stale("共享数据已变化")
        if reason in {"refresh", "current_measurement"} and self.isVisible():
            self._clear_plots_later(show_status=False)
        append_log(f"analysis.shared.end reason={reason} datasets={len(self._datasets)}")

    @property
    def _datasets(self) -> list[AnalysisDataset]:
        return self._data_store.datasets

    @_datasets.setter
    def _datasets(self, value: list[AnalysisDataset]) -> None:
        self._data_store.datasets = list(value)

    @property
    def _next_dataset_id(self) -> int:
        return self._data_store.next_dataset_id

    @_next_dataset_id.setter
    def _next_dataset_id(self, value: int) -> None:
        self._data_store.next_dataset_id = int(value)

    @property
    def _custom_series_labels(self) -> dict[tuple[int, int], str]:
        return self._data_store.custom_series_labels

    @_custom_series_labels.setter
    def _custom_series_labels(self, value: dict[tuple[int, int], str]) -> None:
        self._data_store.custom_series_labels = dict(value)

    @property
    def _custom_series_scales(self) -> dict[tuple[int, int], float]:
        return self._data_store.custom_series_scales

    @_custom_series_scales.setter
    def _custom_series_scales(self, value: dict[tuple[int, int], float]) -> None:
        self._data_store.custom_series_scales = dict(value)

    @property
    def _original_series_scales(self) -> dict[tuple[int, int], float]:
        return self._data_store.original_series_scales

    @_original_series_scales.setter
    def _original_series_scales(self, value: dict[tuple[int, int], float]) -> None:
        self._data_store.original_series_scales = dict(value)

    @property
    def _current_measurement_dataset_id(self) -> int | None:
        return self._data_store.current_measurement_dataset_id

    @_current_measurement_dataset_id.setter
    def _current_measurement_dataset_id(self, value: int | None) -> None:
        self._data_store.current_measurement_dataset_id = value

    def apply_theme(self, theme: dict[str, object] | None) -> None:
        if theme:
            self._theme = dict(theme)
        if not self._theme:
            self._theme = default_diagnostic_theme()
        theme = self._theme
        # Keep module-level TRACE_COLORS in sync for existing plot helpers.
        global TRACE_COLORS
        TRACE_COLORS = trace_colors_for_theme(theme)
        stylesheet = self._theme_stylesheet(theme)
        self.setStyleSheet(stylesheet)
        for list_widget in self.findChildren(QtWidgets.QListWidget):
            self._apply_list_widget_palette(list_widget)
        for dialog_name in (
            "derived_config_dialog",
            "derived_curve_dialog",
            "derived_parameter_dialog",
            "derived_processing_dialog",
        ):
            dialog = getattr(self, dialog_name, None)
            if dialog is not None:
                dialog.setStyleSheet(stylesheet)
        for dialog in list(self._single_plot_windows):
            dialog.setStyleSheet(stylesheet)
            for plot in dialog.findChildren(pg.PlotWidget):
                self._apply_plot_theme(plot)
        for plot in self.findChildren(pg.PlotWidget):
            self._apply_plot_theme(plot)

    def _trace_colors(self) -> list[str]:
        return trace_colors_for_theme(self._theme or default_diagnostic_theme())

    def _color_for_label(self, label: object) -> str:
        return color_for_trace_name(label, self._trace_colors(), theme=self._theme)

    def _pen_for_label(self, label: object, *, width: float = 1.3, style=None):
        kwargs = {"width": width}
        if style is not None:
            kwargs["style"] = style
        return pg.mkPen(self._color_for_label(label), **kwargs)

    def _pen_for_curve_index(self, index: int, *, width: float = 1.3, style=None):
        """Assign distinct colors to curves that share the same channel name."""
        kwargs = {"width": width}
        if style is not None:
            kwargs["style"] = style
        colors = self._trace_colors()
        color = colors[int(index) % len(colors)] if colors else self._color_for_label(index)
        return pg.mkPen(color, **kwargs)

    def _apply_list_widget_palette(self, list_widget: QtWidgets.QListWidget) -> None:
        theme = self._theme
        palette = list_widget.palette()
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(str(theme.get("table_bg", "#ffffff"))))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(str(theme.get("panel_bg_alt", "#edf3fa"))))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(str(theme.get("text", "#102033"))))
        palette.setColor(
            QtGui.QPalette.Highlight,
            QtGui.QColor(str(theme.get("selection_bg", theme.get("accent", "#1d72c9")))),
        )
        palette.setColor(
            QtGui.QPalette.HighlightedText,
            QtGui.QColor(str(theme.get("selection_text", "#ffffff"))),
        )
        list_widget.setPalette(palette)
        list_widget.viewport().setAutoFillBackground(True)

    @staticmethod
    def _theme_stylesheet(theme: dict[str, object]) -> str:
        return build_diagnostic_stylesheet(theme)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(central, 1)
        outer_layout.addWidget(self._status_bar, 0)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.readme_panel = self._build_readme_panel()
        layout.addWidget(self.readme_panel)

        self.left_panel = QtWidgets.QWidget()
        self.left_panel.setMinimumWidth(0 if self._derived_only else 240)
        self.left_panel.setMaximumWidth(320 if self._derived_only else 300)
        self.left_panel.setMinimumSize(0, 0)
        self.left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored if self._derived_only else QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        self.left_layout = left_layout
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)
        left_layout.addWidget(self._build_load_group())
        if self._derived_only:
            self._hidden_series_group = self._build_series_group()
            self._hidden_series_group.hide()
        else:
            left_layout.addWidget(self._build_series_group(), 4)
        self.processing_controls_group = self._build_controls_group()
        if self._derived_only:
            left_layout.addWidget(self._build_slot_selection_group())
            left_layout.addWidget(self._build_workspace_group(), 1)
            left_layout.addWidget(self._build_settings_buttons_group())
            left_scroll = QtWidgets.QScrollArea()
            left_scroll.setObjectName("derivedControlScroll")
            left_scroll.setWidgetResizable(True)
            left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            left_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            left_scroll.setMinimumWidth(230)
            left_scroll.setMaximumWidth(326)
            left_scroll.setWidget(self.left_panel)
            self.left_panel_scroll = left_scroll
            layout.addWidget(left_scroll)
        else:
            left_layout.addWidget(self.processing_controls_group)
            layout.addWidget(self.left_panel)

        if self._derived_only:
            self.derived_tab = QtWidgets.QWidget()
            self.tabs = None
            layout.addWidget(self.derived_tab, 1)
        else:
            self.tabs = QtWidgets.QTabWidget()
            self.main_tab = QtWidgets.QWidget()
            self.foundation_tab = QtWidgets.QWidget()
            self.tabs.addTab(self.main_tab, "主界面")
            self.tabs.addTab(self.foundation_tab, "地面振动")
            if self._include_derived_tab:
                self.derived_tab = QtWidgets.QWidget()
                self.tabs.addTab(self.derived_tab, "换算")
            layout.addWidget(self.tabs, 1)
        if not self._derived_only:
            self._build_main_tab()
            self._build_foundation_tab()
        if self._include_derived_tab:
            self._build_derived_tab()
        self._refresh_dataset_lists()
        self.statusBar().showMessage("Ready")

    def _create_plot_widget(self, title: str = "") -> pg.PlotWidget:
        view_box = VnaViewBox()
        plot_item = pg.PlotItem(
            title=title,
            viewBox=view_box,
            axisItems={
                "bottom": VnaAxisItem(orientation="bottom"),
                "left": VnaAxisItem(orientation="left"),
            },
        )
        plot = pg.PlotWidget(plotItem=plot_item)
        plot_item.setDownsampling(auto=True, mode="peak")
        plot_item.setClipToView(True)
        view_box._on_left_drag = (
            lambda scene_pos, plot_widget=plot: self._move_cursor_from_scene_pos(plot_widget, scene_pos)
        )
        view_box._on_right_drag_zoom = (
            lambda start, stop, plot_widget=plot: self._zoom_plot_to_view_rect(plot_widget, start, stop)
        )
        plot.getPlotItem().setMenuEnabled(False)
        plot.getPlotItem().vb.setMenuEnabled(False)
        plot.addLegend(offset=(4, 2), labelTextSize="7pt")
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.scene().sigMouseClicked.connect(
            lambda event, plot_widget=plot: self._handle_plot_click(plot_widget, event)
        )
        plot.getPlotItem().vb.sigRangeChanged.connect(
            lambda *_args, plot_widget=plot: self._remember_axis_range(plot_widget)
        )
        self._plot_curves[plot] = {}
        self._plot_curve_info[plot] = {}
        self._active_trace[plot] = None
        if self._active_plot is None:
            self._active_plot = plot
        self._data_tip_items[plot] = []
        self._cursor_items[plot] = self._create_cursor_items(plot)
        self._cursor_positions[plot] = None
        self._axis_history[plot] = []
        self._log_modes[plot] = (False, False)
        self._plot_export_excluded[plot] = set()
        self._curve_edit_items[plot] = []
        return plot

    def _curve_info_for(self, plot: pg.PlotWidget, label: str) -> PlotCurveInfo:
        text = str(label)
        info = self._plot_curve_info.setdefault(plot, {}).get(text)
        if info is not None:
            return info
        is_vc = text.startswith("VC ") or text.startswith("VC参考线")
        is_stiffness = text.startswith("1e8 N/m") or "动刚度标准线" in text
        protected = is_vc or is_stiffness
        info = PlotCurveInfo(
            curve_id=self._next_plot_curve_id,
            label=text,
            curve_type="参考线" if protected else self._plot_curve_kind.get(plot, "数据"),
            source="参考标准" if protected else "当前图窗数据",
            removable=not protected,
            exportable=not protected,
        )
        self._next_plot_curve_id += 1
        self._plot_curve_info.setdefault(plot, {})[text] = info
        return info

    def _register_plot_curve(
        self,
        plot: pg.PlotWidget,
        label: str,
        *,
        curve_type: str | None = None,
        source: str | None = None,
        removable: bool | None = None,
        exportable: bool | None = None,
    ) -> None:
        info = self._curve_info_for(plot, label)
        if curve_type is not None:
            info.curve_type = str(curve_type)
        if source is not None:
            info.source = str(source)
        if removable is not None:
            info.removable = bool(removable)
        if exportable is not None:
            info.exportable = bool(exportable)

    def _build_load_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("1. 数据" if self._derived_only else "[-] 数据")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
        self.load_file_button = QtWidgets.QPushButton("加载文件")
        self.load_folder_button = QtWidgets.QPushButton("加载文件夹")
        self.clear_button = QtWidgets.QPushButton("删除所选")
        set_button_role(self.load_file_button, "primary")
        set_button_role(self.load_folder_button, "secondary")
        set_button_role(self.clear_button, "danger")
        if self._derived_only:
            self.derived_manage_data_button = QtWidgets.QPushButton("管理数据")
            set_button_role(self.derived_manage_data_button, "secondary")
        self.fs_hint_spin = QtWidgets.QDoubleSpinBox()
        self.fs_hint_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.fs_hint_spin.setRange(16.0, 1_048_576.0)
        self.fs_hint_spin.setDecimals(0)
        self.fs_hint_spin.setSingleStep(256.0)
        self.fs_hint_spin.setValue(4096.0)
        layout.addWidget(self.load_file_button, 0, 0)
        layout.addWidget(self.load_folder_button, 0, 1)
        if self._derived_only:
            layout.addWidget(self.derived_manage_data_button, 1, 0, 1, 2)
            self.derived_manage_data_button.clicked.connect(self._show_data_manager_dialog)
        else:
            layout.addWidget(self.clear_button, 0, 2)
        if not self._derived_only:
            layout.addWidget(QtWidgets.QLabel("FFT块长"), 1, 0)
            layout.addWidget(self.fs_hint_spin, 1, 1, 1, 2)
        self.load_file_button.clicked.connect(self._load_file)
        self.load_folder_button.clicked.connect(self._load_folder)
        if not self._derived_only:
            self.clear_button.clicked.connect(self._request_delete_selected_datasets)
        return group

    def _build_series_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("数据列表")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(5)
        layout.setVerticalSpacing(5)
        self.series_list = QtWidgets.QListWidget()
        self.series_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.series_list.setAlternatingRowColors(True)
        self.series_list.setMinimumHeight(84)
        self.series_list.setToolTip(
            "选择两条没有原生传递率的时域数据时，列表中靠前的数据作为输入，靠后的数据作为响应。"
        )
        self.rename_edit = QtWidgets.QLineEdit()
        self.factor_edit = QtWidgets.QLineEdit("1")
        self.rename_edit.setPlaceholderText("selected channel name")
        self.factor_edit.setMaximumWidth(64)
        layout.addWidget(self.series_list, 0, 0, 1, 4)
        layout.addWidget(QtWidgets.QLabel("重命名"), 1, 0)
        layout.addWidget(self.rename_edit, 1, 1)
        layout.addWidget(QtWidgets.QLabel("系数"), 1, 2)
        layout.addWidget(self.factor_edit, 1, 3)
        buttons = QtWidgets.QHBoxLayout()
        self.select_all_button = QtWidgets.QPushButton("全选")
        self.select_none_button = QtWidgets.QPushButton("全不选")
        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.show_readme_button = QtWidgets.QPushButton("查看 readme")
        self.show_readme_button.setCheckable(True)
        self.show_readme_button.setEnabled(False)
        self.show_readme_button.setToolTip("未加载数据")
        set_button_role(self.select_all_button, "secondary")
        set_button_role(self.select_none_button, "secondary")
        set_button_role(self.refresh_button, "secondary")
        set_button_role(self.show_readme_button, "secondary")
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.select_none_button)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.show_readme_button)
        layout.addLayout(buttons, 2, 0, 1, 4)
        self.series_list.itemSelectionChanged.connect(self._on_series_selection_changed)
        self.rename_edit.editingFinished.connect(self._rename_selected_series_from_editor)
        self.rename_edit.returnPressed.connect(self._rename_selected_series_confirmed)
        self.factor_edit.editingFinished.connect(self._set_selected_series_scale_from_editor)
        self.select_all_button.clicked.connect(self._select_all_series)
        self.select_none_button.clicked.connect(self._select_no_series)
        self.refresh_button.clicked.connect(self.refresh_data_sources)
        self.show_readme_button.toggled.connect(self._toggle_readme_panel)
        return group

    def _build_readme_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("readmePanel")
        panel.setMinimumWidth(245)
        panel.setMaximumWidth(320)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        title = QtWidgets.QLabel("readme / 工况")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self.readme_summary_label = QtWidgets.QLabel("未加载数据")
        self.readme_summary_label.setWordWrap(True)
        layout.addWidget(self.readme_summary_label)

        self.readme_panel_preview = QtWidgets.QPlainTextEdit()
        self.readme_panel_preview.setReadOnly(True)
        self.readme_panel_preview.setMinimumHeight(150)
        self.readme_panel_preview.setPlaceholderText("没有可显示的 readme 内容")
        layout.addWidget(self.readme_panel_preview)

        panel.setVisible(False)
        return panel

    def _build_slot_selection_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("2. 当前选择")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)
        self._slot_value_labels: dict[str, QtWidgets.QLabel] = {}
        slots = (
            ("transfer", "传递率曲线"),
            ("input", "待换算数据"),
        )
        for row, (role, label_text) in enumerate(slots):
            layout.addWidget(QtWidgets.QLabel(label_text), row, 0)
            value_label = QtWidgets.QLabel("(未选择)")
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)
            self._slot_value_labels[role] = value_label
            button = QtWidgets.QPushButton("选择")
            button.clicked.connect(lambda _checked=False, slot_role=role: self._show_slot_selector(slot_role))
            layout.addWidget(value_label, row, 1)
            layout.addWidget(button, row, 2)
        self.derived_batch_target_list = QtWidgets.QListWidget()
        self.derived_batch_target_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.derived_batch_target_list.setMinimumHeight(76)
        self.derived_batch_target_list.setMaximumHeight(126)
        self.derived_batch_target_list.setToolTip("按 Ctrl 或 Shift 可一次选择多个待换算目标")
        layout.addWidget(QtWidgets.QLabel("批量待换算目标"), 2, 0, 1, 3)
        layout.addWidget(self.derived_batch_target_list, 3, 0, 1, 3)
        self.derived_batch_target_list.itemSelectionChanged.connect(self._on_batch_target_selection_changed)
        return group

    def _build_workspace_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("3. 工作区曲线")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(6)
        self.workspace_curve_table = QtWidgets.QTableWidget(0, 3)
        self.workspace_curve_table.setHorizontalHeaderLabels(["名称", "类型", "来源"])
        self.workspace_curve_table.verticalHeader().setVisible(False)
        self.workspace_curve_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.workspace_curve_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.workspace_curve_table.setAlternatingRowColors(True)
        self.workspace_curve_table.setMinimumHeight(132)
        self.workspace_curve_table.setMaximumHeight(210)
        self.workspace_curve_table.horizontalHeader().setStretchLastSection(True)
        self.workspace_curve_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.workspace_curve_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.workspace_curve_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.workspace_curve_table)
        button_row = QtWidgets.QGridLayout()
        button_row.setHorizontalSpacing(5)
        button_row.setVerticalSpacing(5)
        self.workspace_add_current_button = QtWidgets.QPushButton("加入当前PSD")
        self.workspace_plot_selected_button = QtWidgets.QPushButton("绘制选中")
        self.workspace_delete_button = QtWidgets.QPushButton("删除选中")
        set_button_role(self.workspace_add_current_button, "secondary")
        set_button_role(self.workspace_plot_selected_button, "primary")
        set_button_role(self.workspace_delete_button, "danger")
        button_row.addWidget(self.workspace_add_current_button, 0, 0)
        button_row.addWidget(self.workspace_plot_selected_button, 0, 1)
        button_row.addWidget(self.workspace_delete_button, 1, 0, 1, 2)
        layout.addLayout(button_row)
        self.workspace_add_current_button.clicked.connect(self._save_current_psd_curve_to_workspace)
        self.workspace_plot_selected_button.clicked.connect(self._plot_selected_workspace_curves)
        self.workspace_delete_button.clicked.connect(self._delete_selected_workspace_curves)
        return group

    def _build_settings_buttons_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("4. 批量与配方")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)
        self.derived_batch_calculate_button = QtWidgets.QPushButton("全部计算")
        self.derived_batch_export_button = QtWidgets.QPushButton("全部导出")
        self.derived_batch_cancel_button = QtWidgets.QPushButton("取消任务")
        self.derived_recipe_save_button = QtWidgets.QPushButton("保存配方")
        self.derived_recipe_load_button = QtWidgets.QPushButton("加载配方")
        self.derived_load_report_button = QtWidgets.QPushButton("加载详情")
        for button in (
            self.derived_batch_calculate_button,
            self.derived_batch_export_button,
            self.derived_batch_cancel_button,
            self.derived_recipe_save_button,
            self.derived_recipe_load_button,
            self.derived_load_report_button,
        ):
            set_button_role(button, "secondary")
        set_button_role(self.derived_batch_calculate_button, "primary")
        set_button_role(self.derived_batch_cancel_button, "danger")
        layout.addWidget(self.derived_batch_calculate_button, 0, 0)
        layout.addWidget(self.derived_batch_export_button, 0, 1)
        layout.addWidget(self.derived_batch_cancel_button, 1, 0)
        layout.addWidget(self.derived_load_report_button, 1, 1)
        layout.addWidget(self.derived_recipe_save_button, 2, 0)
        layout.addWidget(self.derived_recipe_load_button, 2, 1)
        self.derived_batch_status_table = QtWidgets.QTableWidget(0, 3)
        self.derived_batch_status_table.setHorizontalHeaderLabels(["目标", "状态", "说明"])
        self.derived_batch_status_table.verticalHeader().setVisible(False)
        self.derived_batch_status_table.setMaximumHeight(126)
        self.derived_batch_status_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.derived_batch_status_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.derived_batch_status_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.derived_batch_status_table, 3, 0, 1, 2)
        self.derived_batch_name_edit = QtWidgets.QLineEdit("{name}_{mode}")
        self.derived_batch_name_edit.setToolTip("批量导出命名模板，可使用 {name} 和 {mode}")
        layout.addWidget(QtWidgets.QLabel("输出命名"), 4, 0)
        layout.addWidget(self.derived_batch_name_edit, 4, 1)
        self.derived_batch_calculate_button.clicked.connect(self._calculate_all_derived_targets)
        self.derived_batch_export_button.clicked.connect(self._export_all_derived_results)
        self.derived_batch_cancel_button.clicked.connect(self._cancel_derived_batch)
        self.derived_recipe_save_button.clicked.connect(self._save_processing_recipe)
        self.derived_recipe_load_button.clicked.connect(self._load_processing_recipe)
        self.derived_load_report_button.clicked.connect(self._show_last_load_report)
        self.derived_batch_export_button.setEnabled(False)
        self.derived_batch_cancel_button.setEnabled(False)
        self.derived_load_report_button.setEnabled(False)
        return group

    def _build_workspace_operation_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("工作区运算")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)
        self.workspace_op_labels: dict[str, QtWidgets.QLabel] = {}
        for row, key in enumerate(("a", "b")):
            layout.addWidget(QtWidgets.QLabel(f"输入{key.upper()}"), row, 0)
            value_label = QtWidgets.QLabel("(未选择)")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.workspace_op_labels[key] = value_label
            button = QtWidgets.QPushButton("选择")
            button.clicked.connect(lambda _checked=False, target=key: self._show_workspace_source_selector(target))
            layout.addWidget(value_label, row, 1)
            layout.addWidget(button, row, 2)
        self.workspace_op_type_combo = QtWidgets.QComboBox()
        self.workspace_op_type_combo.addItem("拼合", "stitch")
        self.workspace_op_type_combo.addItem("相加", "add")
        self.workspace_op_type_combo.addItem("相减", "subtract")
        self.workspace_op_type_combo.currentIndexChanged.connect(lambda _index: self._sync_workspace_operation_ui())
        self.workspace_op_output_edit = QtWidgets.QLineEdit()
        self.workspace_op_output_edit.setPlaceholderText("自动命名")
        self.workspace_op_order_combo = QtWidgets.QComboBox()
        self.workspace_op_order_combo.addItem("A在前，B在后", "a_first")
        self.workspace_op_order_combo.addItem("B在前，A在后", "b_first")
        self.workspace_op_split_edit = QtWidgets.QLineEdit("30")
        self.workspace_op_split_edit.setMaximumWidth(86)
        self.workspace_stitch_blend_check = QtWidgets.QCheckBox("平滑过渡")
        self.workspace_stitch_blend_width_spin = QtWidgets.QDoubleSpinBox()
        self.workspace_stitch_blend_width_spin.setRange(0.01, 1e6)
        self.workspace_stitch_blend_width_spin.setValue(2.0)
        self.workspace_stitch_blend_width_spin.setSuffix(" Hz")
        self.workspace_stitch_blend_width_spin.setEnabled(False)
        self.workspace_stitch_blend_check.toggled.connect(self.workspace_stitch_blend_width_spin.setEnabled)
        self.workspace_op_execute_button = QtWidgets.QPushButton("执行并保存到工作区")
        set_button_role(self.workspace_op_execute_button, "primary")
        self.workspace_op_execute_button.clicked.connect(self._execute_workspace_operation)
        layout.addWidget(QtWidgets.QLabel("操作"), 2, 0)
        layout.addWidget(self.workspace_op_type_combo, 2, 1, 1, 2)
        layout.addWidget(QtWidgets.QLabel("输出名"), 3, 0)
        layout.addWidget(self.workspace_op_output_edit, 3, 1, 1, 2)
        self.workspace_stitch_controls = QtWidgets.QWidget()
        stitch_layout = QtWidgets.QGridLayout(self.workspace_stitch_controls)
        stitch_layout.setContentsMargins(0, 0, 0, 0)
        stitch_layout.setHorizontalSpacing(6)
        stitch_layout.setVerticalSpacing(6)
        stitch_layout.addWidget(QtWidgets.QLabel("顺序"), 0, 0)
        stitch_layout.addWidget(self.workspace_op_order_combo, 0, 1)
        stitch_layout.addWidget(QtWidgets.QLabel("分界Hz"), 1, 0)
        stitch_layout.addWidget(self.workspace_op_split_edit, 1, 1)
        stitch_layout.addWidget(self.workspace_stitch_blend_check, 2, 0)
        stitch_layout.addWidget(self.workspace_stitch_blend_width_spin, 2, 1)
        layout.addWidget(self.workspace_stitch_controls, 4, 0, 1, 3)
        layout.addWidget(self.workspace_op_execute_button, 5, 0, 1, 3)
        self._sync_workspace_operation_ui()
        return group

    def _sync_workspace_operation_ui(self) -> None:
        if not hasattr(self, "workspace_stitch_controls"):
            return
        is_stitch = self.workspace_op_type_combo.currentData() == "stitch"
        self.workspace_stitch_controls.setVisible(is_stitch)

    def _build_mimo_dialog_combo(
        self,
        options: list[tuple[str, object]],
        *,
        preferred: object | None = None,
    ) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setMinimumWidth(220)
        combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for label, data in options:
            combo.addItem(label, data)
            combo.setItemData(combo.count() - 1, label, QtCore.Qt.ToolTipRole)
        index = self._combo_index_for_data(combo, preferred)
        if index < 0 and combo.count() > 0:
            index = 0
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _show_mimo_dialog(self) -> None:
        transfer_options = self._mimo_transfer_options()
        target_options = self._mimo_target_options()
        if not transfer_options or not target_options:
            self.statusBar().showMessage("三轴耦合计算需要至少一组传递率和目标 PSD/VC 曲线")
            return
        direction = str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP)
        input_endpoint, target_endpoint = self._mimo_direction_endpoints(direction)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("三轴耦合计算")
        dialog.setModal(True)
        dialog.resize(980, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(
            f"按 y = H u 计算三轴耦合；H 行为响应 X/Y/Z，列为输入 X/Y/Z。"
            f"当前方向为 {input_endpoint} -> {target_endpoint}，将按目标{target_endpoint} PSD 反推{input_endpoint}输入。"
            "H 不会自动求逆，请在矩阵中直接选择当前输入轴到目标响应轴的传递率。"
            "默认假设三轴输入互不相关。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        transfer_group = QtWidgets.QGroupBox("3×3 传递率矩阵 H")
        transfer_layout = QtWidgets.QGridLayout(transfer_group)
        transfer_layout.setContentsMargins(8, 14, 8, 8)
        transfer_layout.setHorizontalSpacing(6)
        transfer_layout.setVerticalSpacing(6)
        axes = ("X", "Y", "Z")
        for column, axis in enumerate(axes, start=1):
            transfer_layout.addWidget(QtWidgets.QLabel(f"输入{axis}"), 0, column)
        transfer_combos: list[list[QtWidgets.QComboBox]] = []
        for row, output_axis in enumerate(axes, start=1):
            transfer_layout.addWidget(QtWidgets.QLabel(f"响应{output_axis}"), row, 0)
            combo_row: list[QtWidgets.QComboBox] = []
            for column, input_axis in enumerate(axes, start=1):
                preferred = self._preferred_mimo_transfer_data(output_index=row - 1, input_index=column - 1)
                combo = self._build_mimo_dialog_combo(transfer_options, preferred=preferred)
                transfer_layout.addWidget(combo, row, column)
                combo_row.append(combo)
            transfer_combos.append(combo_row)
        layout.addWidget(transfer_group)

        target_group = QtWidgets.QGroupBox(f"{target_endpoint}目标 PSD")
        target_layout = QtWidgets.QGridLayout(target_group)
        target_layout.setContentsMargins(8, 14, 8, 8)
        target_layout.setHorizontalSpacing(6)
        target_layout.setVerticalSpacing(6)
        target_combos: list[QtWidgets.QComboBox] = []
        for column, axis in enumerate(axes):
            target_layout.addWidget(QtWidgets.QLabel(f"响应{axis}目标"), 0, column)
            combo = self._build_mimo_dialog_combo(target_options, preferred=("vc_reference", "VC C"))
            target_layout.addWidget(combo, 1, column)
            target_combos.append(combo)
        layout.addWidget(target_group)

        option_row = QtWidgets.QHBoxLayout()
        relation_combo = QtWidgets.QComboBox()
        relation_combo.addItem("独立三轴随机输入", "independent")
        relation_combo.setToolTip("适用于 X/Y/Z 三个激励信号互不相关的随机输入。")
        regularization_spin = QtWidgets.QDoubleSpinBox()
        regularization_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        regularization_spin.setDecimals(9)
        regularization_spin.setRange(0.0, 1e6)
        regularization_spin.setSingleStep(1e-6)
        regularization_spin.setValue(float(self.derived_regularization_spin.value()))
        output_prefix_edit = QtWidgets.QLineEdit(self._mimo_default_output_prefix(direction))
        output_prefix_edit.setMinimumWidth(140)
        option_row.addWidget(QtWidgets.QLabel("输入关系"))
        option_row.addWidget(relation_combo)
        option_row.addWidget(QtWidgets.QLabel("反推下限"))
        option_row.addWidget(regularization_spin)
        option_row.addWidget(QtWidgets.QLabel("输出前缀"))
        option_row.addWidget(output_prefix_edit)
        option_row.addStretch(1)
        layout.addLayout(option_row)

        button_row = QtWidgets.QHBoxLayout()
        calculate_button = QtWidgets.QPushButton("计算并绘图")
        cancel_button = QtWidgets.QPushButton("取消")
        button_row.addStretch(1)
        button_row.addWidget(calculate_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        def calculate() -> None:
            config = {
                "transfers": [[combo.currentData() for combo in row] for row in transfer_combos],
                "targets": [combo.currentData() for combo in target_combos],
                "relation": relation_combo.currentData(),
                "regularization": float(regularization_spin.value()),
                "prefix": output_prefix_edit.text().strip() or "MIMO输入",
            }
            if self._execute_mimo_coupling(config):
                dialog.accept()

        calculate_button.clicked.connect(calculate)
        cancel_button.clicked.connect(dialog.reject)
        dialog.exec()

    def _build_controls_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("滤波与处理" if self._derived_only else "[+] 主处理")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 12, 8, 7)
        layout.setHorizontalSpacing(5)
        layout.setVerticalSpacing(3)

        self.time_start_edit = QtWidgets.QLineEdit()
        self.time_end_edit = QtWidgets.QLineEdit()
        self.time_end_edit.setPlaceholderText("auto")
        self.psd_source_combo = QtWidgets.QComboBox()
        self.psd_source_combo.addItems(["VNA raw aspec", "Periodogram from time"])
        self.quantity_combo = QtWidgets.QComboBox()
        self.quantity_combo.addItems(["Acceleration", "Velocity", "Displacement", "Force"])
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.scale_spin.setRange(1e-12, 1e12)
        self.scale_spin.setDecimals(6)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setToolTip("主界面/地面振动页的统一倍率；换算页使用传递率系数和数据系数。")
        self.lowpass_check = QtWidgets.QCheckBox("Low")
        self.lowpass_spin = QtWidgets.QDoubleSpinBox()
        self.lowpass_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.lowpass_spin.setRange(0.001, 1_000_000.0)
        self.lowpass_spin.setValue(100.0)
        self.lowpass_spin.setSuffix(" Hz")
        self.highpass_check = QtWidgets.QCheckBox("High")
        self.highpass_spin = QtWidgets.QDoubleSpinBox()
        self.highpass_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.highpass_spin.setRange(0.001, 1_000_000.0)
        self.highpass_spin.setValue(5.0)
        self.highpass_spin.setSuffix(" Hz")
        self.detrend_check = QtWidgets.QCheckBox("Detrend")
        self.filter_order_spin = QtWidgets.QSpinBox()
        self.filter_order_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.filter_order_spin.setRange(1, 12)
        self.filter_order_spin.setValue(4)
        self.plot_button = QtWidgets.QPushButton("换算绘图" if self._derived_only else "绘图")
        set_button_role(self.plot_button, "primary")
        self.hold_button = QtWidgets.QPushButton("保持:关")
        self.hold_button.setCheckable(True)
        set_button_role(self.hold_button, "secondary")
        self.clear_plots_button = QtWidgets.QPushButton("清空图像")
        self.export_button = QtWidgets.QPushButton("导出数据")
        set_button_role(self.clear_plots_button, "secondary")
        set_button_role(self.export_button, "secondary")

        layout.addWidget(QtWidgets.QLabel("起始时间"), 0, 0)
        layout.addWidget(self.time_start_edit, 0, 1)
        layout.addWidget(QtWidgets.QLabel("结束时间"), 0, 2)
        layout.addWidget(self.time_end_edit, 0, 3)
        layout.addWidget(QtWidgets.QLabel("PSD 来源"), 1, 0)
        layout.addWidget(self.psd_source_combo, 1, 1, 1, 3)
        layout.addWidget(QtWidgets.QLabel("物理量"), 2, 0)
        layout.addWidget(self.quantity_combo, 2, 1, 1, 3)
        if not self._derived_only:
            layout.addWidget(QtWidgets.QLabel("主图倍率"), 3, 0)
            layout.addWidget(self.scale_spin, 3, 1, 1, 3)
        layout.addWidget(self.lowpass_check, 4, 0)
        layout.addWidget(self.lowpass_spin, 4, 1, 1, 3)
        layout.addWidget(self.highpass_check, 5, 0)
        layout.addWidget(self.highpass_spin, 5, 1, 1, 3)
        layout.addWidget(self.detrend_check, 6, 0)
        layout.addWidget(QtWidgets.QLabel("Order"), 6, 1)
        layout.addWidget(self.filter_order_spin, 6, 2, 1, 2)
        if self._derived_only:
            layout.addWidget(self.hold_button, 7, 0)
            layout.addWidget(self.clear_plots_button, 7, 1, 1, 3)
        else:
            layout.addWidget(self.plot_button, 7, 0)
            layout.addWidget(self.hold_button, 7, 1)
            layout.addWidget(self.clear_plots_button, 7, 2)
            layout.addWidget(self.export_button, 7, 3)
        self.plot_button.clicked.connect(self.plot_current)
        self.hold_button.toggled.connect(self._hold_toggled)
        self.clear_plots_button.clicked.connect(self._clear_plots)
        self.export_button.clicked.connect(self._export_current_csv)
        self.time_start_edit.editingFinished.connect(self._auto_plot_from_control_change)
        self.time_end_edit.editingFinished.connect(self._auto_plot_from_control_change)
        self.psd_source_combo.currentTextChanged.connect(lambda _text: self._auto_plot_from_control_change())
        self.quantity_combo.currentTextChanged.connect(lambda _text: self._auto_plot_from_control_change())
        self.scale_spin.valueChanged.connect(lambda _value: self._auto_plot_from_control_change())
        self.lowpass_check.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        self.lowpass_spin.valueChanged.connect(lambda _value: self._auto_plot_from_control_change())
        self.highpass_check.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        self.highpass_spin.valueChanged.connect(lambda _value: self._auto_plot_from_control_change())
        self.detrend_check.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        self.filter_order_spin.valueChanged.connect(lambda _value: self._auto_plot_from_control_change())
        self.fs_hint_spin.editingFinished.connect(self._auto_plot_from_control_change)
        return group

    def _build_main_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.main_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        self.main_mode_combos: list[QtWidgets.QComboBox] = []
        self.main_open_buttons: list[QtWidgets.QPushButton] = []
        self.main_interpolate_buttons: list[QtWidgets.QPushButton] = []
        self.main_export_buttons: list[QtWidgets.QPushButton] = []
        self.main_plots: list[pg.PlotWidget] = []
        for index, (label, default) in enumerate((("图窗 1", "Time"), ("图窗 2", "PSD"), ("图窗 3", "Trans"))):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QtWidgets.QLabel(f"{label}:"))
            combo = QtWidgets.QComboBox()
            combo.addItems(["Time", "PSD", "CumPSD", "Trans", "Coherence"])
            combo.setCurrentText(default)
            combo.currentTextChanged.connect(lambda _text: self._auto_plot_from_control_change())
            self.main_mode_combos.append(combo)
            row.addWidget(combo)
            open_button = QtWidgets.QPushButton("图窗")
            interpolate_button = QtWidgets.QPushButton("插值")
            export_button = QtWidgets.QPushButton("导出数据")
            open_button.clicked.connect(lambda _checked=False, i=index: self._open_plot_window_for_plot(self.main_plots[i]))
            interpolate_button.clicked.connect(lambda _checked=False, i=index: self._show_interpolation_dialog_for_plot(self.main_plots[i]))
            export_button.clicked.connect(lambda _checked=False, i=index: self._export_plot_csv(self.main_plots[i]))
            self.main_open_buttons.append(open_button)
            self.main_interpolate_buttons.append(interpolate_button)
            self.main_export_buttons.append(export_button)
            row.addWidget(open_button)
            row.addWidget(interpolate_button)
            row.addWidget(export_button)
            row.addStretch(1)
            layout.addLayout(row)
            plot = self._create_plot_widget()
            layout.addWidget(plot, 1)
            self.main_plots.append(plot)

    def _build_foundation_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.foundation_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        controls = QtWidgets.QVBoxLayout()
        controls.setSpacing(4)
        file_grid = QtWidgets.QGridLayout()
        file_grid.setHorizontalSpacing(5)
        file_grid.setVerticalSpacing(4)
        vc_row = QtWidgets.QHBoxLayout()
        vc_row.setSpacing(6)
        self.foundation_vib_file_combo = QtWidgets.QComboBox()
        self.foundation_stiff_file_combo = QtWidgets.QComboBox()
        self.foundation_vib_edit = QtWidgets.QLineEdit("2,3,4")
        self.foundation_resp_edit = QtWidgets.QLineEdit("4")
        self.foundation_vib_edit.setMinimumWidth(64)
        self.foundation_vib_edit.setMaximumWidth(86)
        self.foundation_resp_edit.setMinimumWidth(48)
        self.foundation_resp_edit.setMaximumWidth(64)
        for combo in (self.foundation_vib_file_combo, self.foundation_stiff_file_combo):
            combo.setMinimumWidth(140)
            combo.setMaximumWidth(260)
            combo.setMinimumContentsLength(16)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        file_grid.addWidget(QtWidgets.QLabel("振动文件"), 0, 0)
        file_grid.addWidget(self.foundation_vib_file_combo, 0, 1)
        file_grid.addWidget(QtWidgets.QLabel("振动通道"), 0, 2)
        file_grid.addWidget(self.foundation_vib_edit, 0, 3)
        file_grid.addWidget(QtWidgets.QLabel("动刚度文件"), 1, 0)
        file_grid.addWidget(self.foundation_stiff_file_combo, 1, 1)
        file_grid.addWidget(QtWidgets.QLabel("响应通道"), 1, 2)
        file_grid.addWidget(self.foundation_resp_edit, 1, 3)
        file_grid.setColumnStretch(1, 1)
        self.vc_a_check = QtWidgets.QCheckBox("VC A")
        self.vc_b_check = QtWidgets.QCheckBox("VC B")
        self.vc_c_check = QtWidgets.QCheckBox("VC C")
        self.vc_d_check = QtWidgets.QCheckBox("VC D")
        self.vc_e_check = QtWidgets.QCheckBox("VC E")
        self.vc_f_check = QtWidgets.QCheckBox("VC F")
        self.vc_a_check.setChecked(False)
        self.vc_b_check.setChecked(True)
        self.vc_c_check.setChecked(True)
        self.vc_d_check.setChecked(False)
        self.vc_e_check.setChecked(False)
        self.vc_f_check.setChecked(False)
        vc_row.addWidget(QtWidgets.QLabel("VC参考线"))
        for checkbox in (
            self.vc_a_check,
            self.vc_b_check,
            self.vc_c_check,
            self.vc_d_check,
            self.vc_e_check,
            self.vc_f_check,
        ):
            checkbox.setObjectName("vcCheck")
            vc_row.addWidget(checkbox)
            checkbox.toggled.connect(lambda _checked: self._auto_plot_from_control_change())
        vc_row.addStretch(1)
        controls.addLayout(file_grid)
        controls.addLayout(vc_row)
        layout.addLayout(controls)
        self.foundation_plots: list[pg.PlotWidget] = []
        self.foundation_open_buttons: list[QtWidgets.QPushButton] = []
        self.foundation_export_buttons: list[QtWidgets.QPushButton] = []
        for index, title in enumerate(("Ground Vibration", "Dynamic Stiffness", "Coherence")):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(5)
            row.addStretch(1)
            open_button = QtWidgets.QPushButton("图窗")
            export_button = QtWidgets.QPushButton("导出数据")
            open_button.clicked.connect(lambda _checked=False, i=index: self._open_plot_window_for_plot(self.foundation_plots[i]))
            export_button.clicked.connect(lambda _checked=False, i=index: self._export_plot_csv(self.foundation_plots[i]))
            self.foundation_open_buttons.append(open_button)
            self.foundation_export_buttons.append(export_button)
            row.addWidget(open_button)
            row.addWidget(export_button)
            layout.addLayout(row)
            plot = self._create_plot_widget(title)
            layout.addWidget(plot, 1)
            self.foundation_plots.append(plot)
        self.foundation_vib_file_combo.currentIndexChanged.connect(
            lambda _index, combo=self.foundation_vib_file_combo: self._on_foundation_file_combo_changed(combo)
        )
        self.foundation_stiff_file_combo.currentIndexChanged.connect(
            lambda _index, combo=self.foundation_stiff_file_combo: self._on_foundation_file_combo_changed(combo)
        )
        self.foundation_vib_edit.editingFinished.connect(self._auto_plot_from_control_change)
        self.foundation_resp_edit.editingFinished.connect(self._auto_plot_from_control_change)

    def _build_derived_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.derived_tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QtWidgets.QGroupBox("换算参数" if self._derived_only else "传递率换算")
        control_layout = QtWidgets.QVBoxLayout(controls)
        control_layout.setContentsMargins(8, 14, 8, 8)
        control_layout.setSpacing(4)

        self.derived_transfer_combo = QtWidgets.QComboBox()
        self.derived_direction_combo = QtWidgets.QComboBox()
        self.derived_direction_combo.addItem("地基 -> 顶部", DERIVE_BASE_TO_TOP)
        self.derived_direction_combo.addItem("顶部 -> 地基", DERIVE_TOP_TO_BASE)
        self.derived_input_series_combo = QtWidgets.QComboBox()
        self.derived_transfer_factor_spin = QtWidgets.QDoubleSpinBox()
        self.derived_input_factor_spin = QtWidgets.QDoubleSpinBox()
        for spin in (self.derived_transfer_factor_spin, self.derived_input_factor_spin):
            spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            spin.setKeyboardTracking(False)
            spin.setRange(1e-12, 1e12)
            spin.setDecimals(1)
            spin.setValue(1.0)
            spin.setSingleStep(0.1)
            spin.setMaximumWidth(86)
        self.derived_freq_min_edit = QtWidgets.QLineEdit()
        self.derived_freq_max_edit = QtWidgets.QLineEdit()
        for edit in (self.derived_freq_min_edit, self.derived_freq_max_edit):
            edit.setMinimumWidth(58)
            edit.setMaximumWidth(78)
        self.derived_freq_min_edit.setPlaceholderText("auto")
        self.derived_freq_max_edit.setPlaceholderText("auto")
        self.derived_regularization_spin = QtWidgets.QDoubleSpinBox()
        self.derived_regularization_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.derived_regularization_spin.setDecimals(9)
        self.derived_regularization_spin.setRange(0.0, 1e6)
        self.derived_regularization_spin.setSingleStep(1e-6)
        self.derived_regularization_spin.setValue(0.0)
        self.derived_regularization_spin.setToolTip("反推除法的传递率下限；力/加速度等带单位传递率建议保持 0。")
        self.derived_plot_button = QtWidgets.QPushButton("计算 / 更新结果" if self._derived_only else "应用")
        set_button_role(self.derived_plot_button, "primary")
        self.derived_show_source_check = QtWidgets.QCheckBox("绘制待换算数据")
        self.derived_show_source_check.setObjectName("vcCheck")
        self.derived_show_source_check.setChecked(False)
        self.derived_coherence_correction_check = QtWidgets.QCheckBox("相干修正")
        self.derived_coherence_correction_check.setObjectName("vcCheck")
        self.derived_coherence_correction_check.setToolTip(
            "使用传递率对应的相干性修正 PSD：正向除以 coh，反向乘以 coh；低相干频点按下限保护。"
        )
        self.derived_coherence_correction_check.setChecked(True)
        self.derived_vc_checks: dict[str, QtWidgets.QCheckBox] = {}

        if self._derived_only:
            workflow_group = QtWidgets.QGroupBox("处理流程")
            workflow_group.setMinimumWidth(0)
            workflow_group.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            workflow_layout = QtWidgets.QGridLayout(workflow_group)
            workflow_layout.setContentsMargins(8, 14, 8, 8)
            workflow_layout.setHorizontalSpacing(6)
            workflow_layout.setVerticalSpacing(5)
            workflow_layout.addWidget(QtWidgets.QLabel("传递率"), 0, 0)
            workflow_layout.addWidget(self.derived_transfer_combo, 0, 1)
            workflow_layout.addWidget(QtWidgets.QLabel("方向"), 0, 2)
            workflow_layout.addWidget(self.derived_direction_combo, 0, 3)
            workflow_layout.addWidget(self.derived_plot_button, 0, 4)
            workflow_layout.addWidget(QtWidgets.QLabel("待换算数据"), 1, 0)
            workflow_layout.addWidget(self.derived_input_series_combo, 1, 1, 1, 4)
            workflow_layout.setColumnStretch(1, 2)
            workflow_layout.setColumnStretch(3, 1)
            self.derived_task_summary_label = QtWidgets.QLabel("请选择传递率和待换算数据")
            self.derived_task_summary_label.setWordWrap(True)
            self.derived_task_summary_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.derived_task_summary_label.setObjectName("processingTaskSummary")
            workflow_layout.addWidget(self.derived_task_summary_label, 2, 0, 1, 5)
            self.derived_issue_label = QtWidgets.QLabel("结果待更新")
            self.derived_issue_label.setWordWrap(True)
            self.derived_issue_label.setObjectName("processingIssue")
            workflow_layout.addWidget(self.derived_issue_label, 3, 0, 1, 5)
            self.derived_verification_label = QtWidgets.QLabel("结果校核：等待计算")
            self.derived_verification_label.setWordWrap(True)
            self.derived_verification_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            workflow_layout.addWidget(self.derived_verification_label, 4, 0, 1, 5)
            layout.addWidget(workflow_group)

        for combo in (self.derived_transfer_combo, self.derived_input_series_combo):
            combo.setMinimumWidth(100 if self._derived_only else 150)
            combo.setMaximumWidth(16777215)
            combo.setMinimumContentsLength(12)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.derived_direction_combo.setMaximumWidth(140)
        self.derived_regularization_spin.setMaximumWidth(150)
        self.derived_plot_button.setMaximumWidth(150)

        if self._derived_only:
            transfer_factor_row = QtWidgets.QHBoxLayout()
            transfer_factor_row.setSpacing(6)
            transfer_factor_row.addWidget(QtWidgets.QLabel("传递率系数"))
            transfer_factor_row.addWidget(self.derived_transfer_factor_spin)
            transfer_factor_row.addStretch(1)
            control_layout.addLayout(transfer_factor_row)

            input_factor_row = QtWidgets.QHBoxLayout()
            input_factor_row.setSpacing(6)
            input_factor_row.addWidget(QtWidgets.QLabel("数据系数"))
            input_factor_row.addWidget(self.derived_input_factor_spin)
            input_factor_row.addStretch(1)
            control_layout.addLayout(input_factor_row)

            freq_row = QtWidgets.QHBoxLayout()
            freq_row.setSpacing(6)
            freq_row.addWidget(QtWidgets.QLabel("频率下限"))
            freq_row.addWidget(self.derived_freq_min_edit)
            freq_row.addWidget(QtWidgets.QLabel("频率上限"))
            freq_row.addWidget(self.derived_freq_max_edit)
            control_layout.addLayout(freq_row)
            regularization_row = QtWidgets.QHBoxLayout()
            regularization_row.setSpacing(6)
            regularization_row.addWidget(QtWidgets.QLabel("反推下限"))
            regularization_row.addWidget(self.derived_regularization_spin)
            regularization_row.addStretch(1)
            control_layout.addLayout(regularization_row)
        else:
            transfer_row = QtWidgets.QHBoxLayout()
            transfer_row.setSpacing(6)
            transfer_row.addWidget(QtWidgets.QLabel("传递率曲线"))
            transfer_row.addWidget(self.derived_transfer_combo, 1)
            transfer_row.addWidget(QtWidgets.QLabel("传递率系数"))
            transfer_row.addWidget(self.derived_transfer_factor_spin)
            control_layout.addLayout(transfer_row)

            input_row = QtWidgets.QHBoxLayout()
            input_row.setSpacing(6)
            input_row.addWidget(QtWidgets.QLabel("待换算数据"))
            input_row.addWidget(self.derived_input_series_combo, 1)
            input_row.addWidget(QtWidgets.QLabel("数据系数"))
            input_row.addWidget(self.derived_input_factor_spin)
            control_layout.addLayout(input_row)

            freq_row = QtWidgets.QHBoxLayout()
            freq_row.setSpacing(6)
            freq_row.addWidget(QtWidgets.QLabel("换算方向"))
            freq_row.addWidget(self.derived_direction_combo)
            freq_row.addWidget(QtWidgets.QLabel("频率下限"))
            freq_row.addWidget(self.derived_freq_min_edit)
            freq_row.addWidget(QtWidgets.QLabel("频率上限"))
            freq_row.addWidget(self.derived_freq_max_edit)
            freq_row.addWidget(QtWidgets.QLabel("反推下限"))
            freq_row.addWidget(self.derived_regularization_spin)
            freq_row.addWidget(self.derived_plot_button)
            freq_row.addStretch(1)
            control_layout.addLayout(freq_row)
        vc_grid = QtWidgets.QGridLayout()
        vc_grid.setHorizontalSpacing(6)
        vc_grid.setVerticalSpacing(3)
        vc_grid.addWidget(QtWidgets.QLabel("VC参考线"), 0, 0, 2, 1)
        for index, name in enumerate(VC_REFERENCE_NAMES):
            checkbox = QtWidgets.QCheckBox(name)
            checkbox.setObjectName("vcCheck")
            checkbox.setChecked(False)
            checkbox.toggled.connect(lambda _checked: self._auto_plot_derived_from_control_change())
            self.derived_vc_checks[name] = checkbox
            vc_grid.addWidget(checkbox, index // 3, 1 + index % 3)
        vc_grid.addWidget(self.derived_coherence_correction_check, 0, 4)
        vc_grid.addWidget(self.derived_show_source_check, 1, 4)
        vc_grid.setColumnStretch(5, 1)

        edit_group = QtWidgets.QGroupBox("曲线编辑" if self._derived_only else "曲线编辑与拼合")
        self.derived_curve_group = edit_group
        if self._derived_only:
            edit_layout = QtWidgets.QVBoxLayout(edit_group)
            edit_layout.setContentsMargins(8, 14, 8, 8)
            edit_layout.setSpacing(5)
            edit_group.setMinimumWidth(0)
            edit_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        else:
            edit_layout = QtWidgets.QGridLayout(edit_group)
            edit_layout.setContentsMargins(8, 14, 8, 8)
            edit_layout.setHorizontalSpacing(6)
            edit_layout.setVerticalSpacing(4)
        self.derived_curve_point_label = QtWidgets.QLabel("传递率点")
        self.derived_transfer_point_table = QtWidgets.QTableWidget(0, 2)
        self.derived_transfer_point_table.setHorizontalHeaderLabels(["Hz", "dB"])
        self.derived_transfer_point_table.verticalHeader().setVisible(False)
        self.derived_transfer_point_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.derived_transfer_point_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        if self._derived_only:
            self.derived_transfer_point_table.setMinimumHeight(150)
            self.derived_transfer_point_table.setMaximumHeight(260)
            self.derived_transfer_point_table.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )
        else:
            self.derived_transfer_point_table.setMinimumHeight(72)
            self.derived_transfer_point_table.setMaximumHeight(112)
        self.derived_transfer_point_table.horizontalHeader().setStretchLastSection(True)
        self.derived_transfer_edit_button = QtWidgets.QPushButton("编辑当前传递率")
        self.derived_transfer_add_point_button = QtWidgets.QPushButton("加点")
        self.derived_transfer_delete_point_button = QtWidgets.QPushButton("删点")
        self.derived_transfer_reset_button = QtWidgets.QPushButton("清除传递率编辑")
        self.derived_psd_edit_button = QtWidgets.QPushButton("编辑当前PSD")
        self.derived_psd_reset_button = QtWidgets.QPushButton("清除PSD编辑")
        self.derived_curve_undo_button = QtWidgets.QPushButton("撤销")
        self.derived_curve_redo_button = QtWidgets.QPushButton("重做")
        self.derived_curve_copy_button = QtWidgets.QPushButton("复制")
        self.derived_curve_paste_button = QtWidgets.QPushButton("粘贴")
        self.derived_curve_import_button = QtWidgets.QPushButton("导入点表")
        self.derived_curve_export_button = QtWidgets.QPushButton("导出点表")
        self.derived_stitch_enabled_check = QtWidgets.QCheckBox("拼合")
        self.derived_stitch_order_combo = QtWidgets.QComboBox()
        self.derived_stitch_order_combo.addItem("换算结果在前", "primary_first")
        self.derived_stitch_order_combo.addItem("导入数据在前", "secondary_first")
        self.derived_stitch_series_combo = QtWidgets.QComboBox()
        self.derived_stitch_series_combo.setMinimumWidth(210)
        self.derived_stitch_series_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.derived_stitch_split_edit = QtWidgets.QLineEdit("30")
        self.derived_stitch_split_edit.setMaximumWidth(72)
        if self._derived_only:
            edit_layout.addWidget(self.derived_curve_point_label)
            edit_layout.addWidget(self.derived_transfer_point_table, 1)
            transfer_actions = QtWidgets.QHBoxLayout()
            transfer_actions.setSpacing(5)
            transfer_actions.addWidget(self.derived_transfer_edit_button)
            transfer_actions.addWidget(self.derived_transfer_reset_button)
            edit_layout.addLayout(transfer_actions)
            history_actions = QtWidgets.QHBoxLayout()
            history_actions.setSpacing(5)
            history_actions.addWidget(self.derived_curve_undo_button)
            history_actions.addWidget(self.derived_curve_redo_button)
            edit_layout.addLayout(history_actions)
            point_actions = QtWidgets.QHBoxLayout()
            point_actions.setSpacing(5)
            point_actions.addWidget(self.derived_transfer_add_point_button)
            point_actions.addWidget(self.derived_transfer_delete_point_button)
            edit_layout.addLayout(point_actions)
            clipboard_actions = QtWidgets.QHBoxLayout()
            clipboard_actions.setSpacing(5)
            clipboard_actions.addWidget(self.derived_curve_copy_button)
            clipboard_actions.addWidget(self.derived_curve_paste_button)
            edit_layout.addLayout(clipboard_actions)
            psd_actions = QtWidgets.QHBoxLayout()
            psd_actions.setSpacing(5)
            psd_actions.addWidget(self.derived_psd_edit_button)
            psd_actions.addWidget(self.derived_psd_reset_button)
            edit_layout.addLayout(psd_actions)
            file_actions = QtWidgets.QHBoxLayout()
            file_actions.addWidget(self.derived_curve_import_button)
            file_actions.addWidget(self.derived_curve_export_button)
            edit_layout.addLayout(file_actions)
            self.derived_stitch_enabled_check.setVisible(False)
            self.derived_stitch_order_combo.setVisible(False)
            self.derived_stitch_series_combo.setVisible(False)
            self.derived_stitch_split_edit.setVisible(False)
        else:
            edit_layout.addWidget(self.derived_curve_point_label, 0, 0)
            edit_layout.addWidget(self.derived_transfer_point_table, 0, 1, 2, 4)
            edit_layout.addWidget(self.derived_transfer_edit_button, 0, 5)
            edit_layout.addWidget(self.derived_transfer_reset_button, 1, 5)
            edit_layout.addWidget(self.derived_transfer_add_point_button, 2, 1)
            edit_layout.addWidget(self.derived_transfer_delete_point_button, 2, 2)
            edit_layout.addWidget(self.derived_psd_edit_button, 2, 3)
            edit_layout.addWidget(self.derived_psd_reset_button, 2, 4)
            edit_layout.addWidget(self.derived_stitch_enabled_check, 3, 0)
            edit_layout.addWidget(self.derived_stitch_order_combo, 3, 1)
            edit_layout.addWidget(self.derived_stitch_series_combo, 3, 2, 1, 2)
            edit_layout.addWidget(QtWidgets.QLabel("分界Hz"), 3, 4)
            edit_layout.addWidget(self.derived_stitch_split_edit, 3, 5)

        if self._derived_only:
            self.derived_config_dialog = None
        else:
            self.derived_config_dialog = QtWidgets.QDialog(self)
            self.derived_config_dialog.setWindowTitle("数据配置")
            self.derived_config_dialog.setModal(False)
            self.derived_config_dialog.setSizeGripEnabled(True)
            config_dialog_layout = QtWidgets.QVBoxLayout(self.derived_config_dialog)
            config_dialog_layout.addWidget(controls)
            self.derived_config_dialog.resize(760, 180)

        if self._derived_only:
            self.derived_curve_dialog = None
            self.derived_parameter_dialog = None
            self.derived_processing_dialog = None
            self.derived_right_toolbox = QtWidgets.QToolBox()
            self.derived_right_toolbox.setMinimumWidth(250)
            self.derived_right_toolbox.setMaximumWidth(370)
            self.derived_right_toolbox.addItem(controls, "参数")
            workspace_operation_group = self._build_workspace_operation_group()
            self.derived_right_toolbox.addItem(edit_group, "曲线编辑")
            self.derived_right_toolbox.addItem(workspace_operation_group, "工作区运算")
            self.derived_right_toolbox.addItem(self.processing_controls_group, "时域与滤波")
            for panel in (controls, edit_group, workspace_operation_group, self.processing_controls_group):
                panel.setMinimumWidth(0)
                panel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            self.derived_settings_stack = self.derived_right_toolbox
        else:
            self.derived_curve_dialog = QtWidgets.QDialog(self)
            self.derived_curve_dialog.setWindowTitle("曲线编辑与拼合")
            self.derived_curve_dialog.setModal(False)
            self.derived_curve_dialog.setSizeGripEnabled(True)
            curve_dialog_layout = QtWidgets.QVBoxLayout(self.derived_curve_dialog)
            curve_dialog_layout.addWidget(edit_group)
            self.derived_curve_dialog.resize(780, 220)

        if not self._derived_only:
            action_row = QtWidgets.QHBoxLayout()
            action_row.setSpacing(6)
            self.derived_main_plot_button = QtWidgets.QPushButton("换算绘图")
            self.derived_config_button = QtWidgets.QPushButton("数据配置")
            set_button_role(self.derived_main_plot_button, "primary")
            set_button_role(self.derived_config_button, "secondary")
            action_row.addWidget(self.derived_config_button)
            self.derived_curve_button = QtWidgets.QPushButton("曲线编辑与拼合")
            set_button_role(self.derived_curve_button, "secondary")
            action_row.addWidget(self.derived_curve_button)
            action_row.addWidget(self.derived_main_plot_button)
            action_row.addStretch(1)
            layout.addLayout(action_row)
        if self._derived_only:
            control_layout.addLayout(vc_grid)
            self.derived_dimensionless_check = QtWidgets.QCheckBox("单位未知时按无量纲继续")
            self.derived_dimensionless_check.setChecked(True)
            self.derived_dimensionless_check.setToolTip("仅在确认单位处理由外部流程完成时使用；该覆盖会写入配方和导出元数据。")
            control_layout.addWidget(self.derived_dimensionless_check)
        else:
            layout.addLayout(vc_grid)

        if self._derived_only:
            plot_host = QtWidgets.QWidget()
            plot_host.setMinimumWidth(0)
            plot_host.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding)
            plot_layout = QtWidgets.QVBoxLayout(plot_host)
            plot_layout.setContentsMargins(0, 0, 0, 0)
            plot_layout.setSpacing(6)
        else:
            plot_host = None
            plot_layout = layout

        self.derived_plots: list[pg.PlotWidget] = []
        self.derived_open_buttons: list[QtWidgets.QPushButton] = []
        self.derived_interpolate_buttons: list[QtWidgets.QPushButton] = []
        self.derived_export_buttons: list[QtWidgets.QPushButton] = []
        self.derived_result_mode_combo = QtWidgets.QComboBox()
        self.derived_result_mode_combo.addItems(["PSD", "CumPSD", "地基振动", "近似时域"])
        self.derived_result_mode_combo.setCurrentText("PSD")
        self.derived_result_mode_combo.currentTextChanged.connect(self._on_derived_result_mode_changed)
        for index, title in enumerate(("传递率曲线", "换算图窗")):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QtWidgets.QLabel(title))
            if index > 0:
                row.addWidget(self.derived_result_mode_combo)
            row.addStretch(1)
            open_button = QtWidgets.QPushButton("图窗")
            interpolate_button = QtWidgets.QPushButton("插值")
            export_button = QtWidgets.QPushButton("导出数据")
            for action_button in (open_button, interpolate_button, export_button):
                action_button.setMinimumWidth(0)
                action_button.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            open_button.clicked.connect(lambda _checked=False, i=index: self._open_plot_window_for_plot(self.derived_plots[i]))
            interpolate_button.clicked.connect(lambda _checked=False, i=index: self._show_interpolation_dialog_for_plot(self.derived_plots[i]))
            export_button.clicked.connect(lambda _checked=False, i=index: self._export_plot_csv(self.derived_plots[i]))
            self.derived_open_buttons.append(open_button)
            self.derived_interpolate_buttons.append(interpolate_button)
            self.derived_export_buttons.append(export_button)
            row.addWidget(open_button)
            row.addWidget(interpolate_button)
            row.addWidget(export_button)
            plot_layout.addLayout(row)
            plot = self._create_plot_widget(title)
            plot_layout.addWidget(plot, 1)
            self.derived_plots.append(plot)

        if self._derived_only and plot_host is not None:
            content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            content_splitter.setChildrenCollapsible(False)
            content_splitter.addWidget(plot_host)
            content_splitter.addWidget(self.derived_right_toolbox)
            content_splitter.setStretchFactor(0, 1)
            content_splitter.setStretchFactor(1, 0)
            content_splitter.setSizes([720, 300])
            layout.addWidget(content_splitter, 1)
            self.derived_content_splitter = content_splitter

        self.derived_transfer_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_transfer_combo.currentIndexChanged.connect(lambda _index: self._sync_slot_labels())
        self.derived_direction_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_input_series_combo.currentIndexChanged.connect(self._on_derived_input_combo_changed)
        self.derived_show_source_check.toggled.connect(lambda _checked: self._auto_plot_derived_from_control_change())
        self.derived_coherence_correction_check.toggled.connect(
            lambda _checked: self._auto_plot_derived_from_control_change()
        )
        self.derived_transfer_factor_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_input_factor_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_freq_min_edit.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_freq_max_edit.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_regularization_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        if self._derived_only:
            self.derived_dimensionless_check.toggled.connect(lambda _checked: self._mark_derived_results_stale("单位确认已变化"))
        if self._derived_only:
            self.derived_plot_button.clicked.connect(
                lambda _checked=False: self._plot_derived(keep_existing=self._hold_enabled())
            )
        else:
            self.derived_plot_button.clicked.connect(self._apply_derived_config)
            self.derived_config_button.clicked.connect(self._show_derived_config_dialog)
        if not self._derived_only:
            self.derived_curve_button.clicked.connect(self._show_derived_curve_dialog)
            self.derived_main_plot_button.clicked.connect(
                lambda _checked=False: self._plot_derived(keep_existing=self._hold_enabled())
            )
        self.derived_transfer_combo.currentIndexChanged.connect(lambda _index: self._sync_transfer_point_table())
        self.derived_transfer_edit_button.clicked.connect(self._initialize_transfer_edit_points_from_current)
        self.derived_transfer_add_point_button.clicked.connect(self._add_transfer_control_point)
        self.derived_transfer_delete_point_button.clicked.connect(self._delete_selected_transfer_control_point)
        self.derived_transfer_reset_button.clicked.connect(self._clear_current_transfer_edit_points)
        self.derived_transfer_point_table.cellChanged.connect(lambda _row, _col: self._transfer_point_table_changed())
        self.derived_psd_edit_button.clicked.connect(self._initialize_psd_edit_points_from_active_curve)
        self.derived_psd_reset_button.clicked.connect(self._clear_active_psd_edit_points)
        self.derived_curve_undo_button.clicked.connect(self._undo_curve_edit)
        self.derived_curve_redo_button.clicked.connect(self._redo_curve_edit)
        self.derived_curve_copy_button.clicked.connect(self._copy_selected_curve_points)
        self.derived_curve_paste_button.clicked.connect(self._paste_curve_points)
        self.derived_curve_import_button.clicked.connect(self._import_curve_points)
        self.derived_curve_export_button.clicked.connect(self._export_curve_points)
        self.derived_stitch_enabled_check.toggled.connect(lambda _checked: self._auto_plot_derived_from_control_change())
        self.derived_stitch_series_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_stitch_order_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_stitch_order_combo.currentIndexChanged.connect(lambda _index: self._sync_slot_labels())
        self.derived_stitch_split_edit.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self._sync_transfer_point_table()
        self._sync_slot_labels()

    def _show_derived_config_dialog(self) -> None:
        self._refresh_config_dataset_list()
        if self.derived_config_dialog is None:
            return
        self.derived_config_dialog.show()
        self.derived_config_dialog.raise_()
        self.derived_config_dialog.activateWindow()

    def _apply_derived_config(self) -> None:
        self._auto_plot_derived_from_control_change()
        if self.derived_config_dialog is not None:
            self.derived_config_dialog.hide()
        self.statusBar().showMessage("换算配置已应用")

    def _show_derived_parameter_dialog(self) -> None:
        dialog = getattr(self, "derived_parameter_dialog", None)
        if dialog is None:
            return
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_derived_processing_dialog(self) -> None:
        dialog = getattr(self, "derived_processing_dialog", None)
        if dialog is None:
            return
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _show_settings_panel(self, index: int) -> None:
        if not hasattr(self, "derived_settings_stack"):
            return
        index = int(index)
        if index < 0 or index >= self.derived_settings_stack.count():
            return
        if self._derived_only:
            self.derived_settings_stack.setCurrentIndex(index)
            self.derived_settings_stack.setVisible(True)
            return
        if not self.derived_settings_stack.isHidden() and self.derived_settings_stack.currentIndex() == index:
            self.derived_settings_stack.setVisible(False)
            return
        self.derived_settings_stack.setCurrentIndex(index)
        self.derived_settings_stack.setVisible(True)
        if self._derived_only and hasattr(self, "left_panel_scroll"):
            QtCore.QTimer.singleShot(
                0,
                lambda: self.left_panel_scroll.ensureWidgetVisible(self.derived_settings_stack, 0, 8),
            )

    def _show_data_manager_dialog(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("管理数据")
        dialog.setModal(True)
        dialog.resize(720, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["文件", "ID", "通道数", "路径"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        button_row = QtWidgets.QHBoxLayout()
        delete_button = QtWidgets.QPushButton("删除选中数据")
        close_button = QtWidgets.QPushButton("关闭")
        button_row.addStretch(1)
        button_row.addWidget(delete_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        def refresh_table() -> None:
            table.setRowCount(0)
            for row, dataset in enumerate(self._datasets):
                table.insertRow(row)
                values = (
                    _series_display_file_name(dataset.name),
                    str(dataset.id),
                    str(len(dataset.series)),
                    str(dataset.path),
                )
                for col, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setData(QtCore.Qt.UserRole, dataset.id)
                    table.setItem(row, col, item)

        def delete_selected() -> None:
            dataset_ids = {
                table.item(index.row(), 0).data(QtCore.Qt.UserRole)
                for index in table.selectedIndexes()
                if table.item(index.row(), 0) is not None
            }
            self._delete_datasets_by_ids({int(dataset_id) for dataset_id in dataset_ids})
            refresh_table()

        delete_button.clicked.connect(delete_selected)
        close_button.clicked.connect(dialog.accept)
        refresh_table()
        dialog.exec()

    def _slot_options_for_role(self, role: str) -> list[tuple[str, str, object]]:
        options: list[tuple[str, str, object]] = []
        if role == "transfer":
            for index in range(self.derived_transfer_combo.count()):
                data = self.derived_transfer_combo.itemData(index)
                if data is not None:
                    options.append(("传递率", self.derived_transfer_combo.itemText(index), data))
            return options
        if role == "input":
            for index in range(self.derived_input_series_combo.count()):
                data = self.derived_input_series_combo.itemData(index)
                if data is not None:
                    options.append(("待换算", self.derived_input_series_combo.itemText(index), data))
            return options
        return options

    def _show_slot_selector(self, role: str) -> None:
        options = self._slot_options_for_role(role)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择数据")
        dialog.setModal(True)
        dialog.resize(760, 420)
        layout = QtWidgets.QVBoxLayout(dialog)
        search_edit = QtWidgets.QLineEdit()
        search_edit.setPlaceholderText("搜索文件、通道或类型")
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["类型", "数据"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(search_edit)
        layout.addWidget(table)
        button_row = QtWidgets.QHBoxLayout()
        select_button = QtWidgets.QPushButton("选择")
        cancel_button = QtWidgets.QPushButton("取消")
        button_row.addStretch(1)
        button_row.addWidget(select_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        def refresh_table() -> None:
            pattern = search_edit.text().strip().lower()
            table.setRowCount(0)
            for kind, label, data in options:
                searchable = f"{kind} {label}".lower()
                if pattern and pattern not in searchable:
                    continue
                row = table.rowCount()
                table.insertRow(row)
                kind_item = QtWidgets.QTableWidgetItem(kind)
                label_item = QtWidgets.QTableWidgetItem(label)
                kind_item.setData(QtCore.Qt.UserRole, data)
                label_item.setData(QtCore.Qt.UserRole, data)
                table.setItem(row, 0, kind_item)
                table.setItem(row, 1, label_item)
            if table.rowCount() > 0:
                table.selectRow(0)

        def choose_current() -> None:
            selected = table.selectedIndexes()
            if not selected:
                return
            item = table.item(selected[0].row(), 0)
            if item is None:
                return
            self._apply_slot_selection(role, item.data(QtCore.Qt.UserRole))
            dialog.accept()

        search_edit.textChanged.connect(refresh_table)
        table.itemDoubleClicked.connect(lambda _item: choose_current())
        select_button.clicked.connect(choose_current)
        cancel_button.clicked.connect(dialog.reject)
        refresh_table()
        dialog.exec()

    def _apply_slot_selection(self, role: str, data: object) -> None:
        if role == "transfer":
            index = self._combo_index_for_data(self.derived_transfer_combo, data)
            if index >= 0:
                self.derived_transfer_combo.setCurrentIndex(index)
        elif role == "input":
            index = self._combo_index_for_data(self.derived_input_series_combo, data)
            if index >= 0:
                self.derived_input_series_combo.setCurrentIndex(index)
        self._sync_slot_labels()
        self._auto_plot_derived_from_control_change()

    def _sync_slot_labels(self) -> None:
        if not hasattr(self, "_slot_value_labels"):
            return

        def set_label(role: str, text: str, data: object | None = None) -> None:
            label = self._slot_value_labels.get(role)
            if label is None:
                return
            full_text = text or "(未选择)"
            shown = full_text if len(full_text) <= 46 else f"{full_text[:43]}..."
            label.setText(shown)
            tooltip = full_text
            if data is not None:
                tooltip = f"{full_text}\n{data}"
            label.setToolTip(tooltip)

        transfer_text = (
            self.derived_transfer_combo.currentText()
            if self.derived_transfer_combo.currentData() is not None
            else "(未选择)"
        )
        input_text = (
            self.derived_input_series_combo.currentText()
            if self.derived_input_series_combo.currentData() is not None
            else "(未选择)"
        )
        set_label("transfer", transfer_text, self.derived_transfer_combo.currentData())
        set_label("input", input_text, self.derived_input_series_combo.currentData())

    def _workspace_source_options(self) -> list[tuple[str, str, object]]:
        options: list[tuple[str, str, object]] = []
        if hasattr(self, "derived_plots") and len(self.derived_plots) > 1 and self.derived_result_mode_combo.currentText() == "PSD":
            excluded = self._plot_export_excluded.get(self.derived_plots[1], set())
            for label in self._plot_curves.get(self.derived_plots[1], {}):
                if label in excluded:
                    continue
                options.append(("当前结果", label, ("current_result_curve", label)))
        for dataset in self._datasets:
            for series in dataset.series:
                options.append(("导入PSD", self._series_label(dataset, series), ("dataset_psd_curve", series.id)))
        for curve in self._workspace_curves:
            options.append(("工作区", curve.name, ("workspace_curve", curve.curve_id)))
        return options

    def _mimo_transfer_options(self) -> list[tuple[str, object]]:
        options: list[tuple[str, object]] = []
        seen: set[object] = set()
        for label, data in self._derived_transfer_options():
            if isinstance(data, tuple) and len(data) == 5:
                options.append((label, data))
                seen.add(data)
        for dataset in self._datasets:
            if dataset.frf or len(dataset.series) < 2:
                continue
            display_name = _series_display_file_name(dataset.name)
            for base_series in dataset.series:
                for top_series in dataset.series:
                    if base_series.channel_key == top_series.channel_key:
                        continue
                    key = f"{base_series.channel_key}->{top_series.channel_key}"
                    data = (
                        dataset.id,
                        key,
                        base_series.channel_key,
                        top_series.channel_key,
                        "time",
                    )
                    if data in seen:
                        continue
                    label = (
                        f"{display_name} | Ch {base_series.channel_index + 1}"
                        f"->Ch {top_series.channel_index + 1} (time)"
                    )
                    options.append((label, data))
                    seen.add(data)
        return options

    def _mimo_target_options(self) -> list[tuple[str, object]]:
        options: list[tuple[str, object]] = []
        for name in VC_REFERENCE_NAMES:
            options.append((f"VC参考线 | {name}", ("vc_reference", name)))
        for kind, label, data in self._workspace_source_options():
            if kind == "当前结果":
                continue
            options.append((f"{kind} | {label}", data))
        return options

    def _preferred_mimo_transfer_data(self, *, output_index: int, input_index: int) -> object | None:
        preferred_keys = (
            f"ai{input_index}->ai{output_index + 3}",
            f"ch{input_index + 1}->ch{output_index + 4}",
            f"ai{input_index}->ai{output_index}",
            f"ch{input_index + 1}->ch{output_index + 1}",
        )
        for _label, data in self._mimo_transfer_options():
            if not isinstance(data, tuple) or len(data) != 5:
                continue
            key = str(data[1]).strip().lower()
            if key in preferred_keys:
                return data
        return None

    @staticmethod
    def _mimo_direction_endpoints(direction: str) -> tuple[str, str]:
        if direction == DERIVE_TOP_TO_BASE:
            return "顶部", "地基"
        return "地基", "顶部"

    @classmethod
    def _mimo_default_output_prefix(cls, direction: str) -> str:
        input_endpoint, _target_endpoint = cls._mimo_direction_endpoints(direction)
        return f"{input_endpoint}输入"

    def _acceleration_psd_from_mimo_target(self, data: object) -> tuple[np.ndarray, np.ndarray, str, tuple[np.ndarray, np.ndarray] | None] | None:
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "vc_reference":
            name = str(data[1])
            dense = None
            if hasattr(self, "_mimo_current_grid") and self._mimo_current_grid is not None:
                dense = _vc_reference_acceleration_psd_for_transfer_grid(name, self._mimo_current_grid)
            if dense is None:
                frequency, psd = _vc_reference_acceleration_psd(name)
            else:
                frequency, psd = dense
            if frequency.size < 2:
                return None
            return frequency, psd, name, _vc_band_edges_for_frequencies(name, frequency)
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "dataset_psd_curve":
            for dataset in self._datasets:
                for series in dataset.series:
                    if series.id == data[1]:
                        frequency, psd = self._psd_for_series(
                            dataset,
                            series,
                            scale=float(self.scale_spin.value()) * float(series.scale or 1.0),
                        )
                        if frequency.size < 2 or psd.size < 2:
                            return None
                        return np.asarray(frequency, dtype=float), np.asarray(psd, dtype=float), self._series_label(dataset, series), None
        curve = self._curve_from_workspace_source(data)
        if curve is None:
            return None
        frequency, psd, label = curve
        return np.asarray(frequency, dtype=float), np.asarray(psd, dtype=float), label, None

    def _dataset_series_by_id(self, series_id: object) -> tuple[AnalysisDataset, AnalysisSeries] | None:
        target_id = str(series_id)
        for dataset in self._datasets:
            for series in dataset.series:
                if series.id == target_id:
                    return dataset, series
        return None

    @staticmethod
    def _dataset_cross_spectrum_key(a_key: str, b_key: str) -> tuple[str, bool]:
        forward = f"{a_key}->{b_key}"
        reverse = f"{b_key}->{a_key}"
        return forward, forward == reverse

    def _dataset_cross_psd_for_pair(
        self,
        dataset: AnalysisDataset,
        left: AnalysisSeries,
        right: AnalysisSeries,
        grid: np.ndarray,
    ) -> np.ndarray | None:
        global_scale = float(self.scale_spin.value()) if hasattr(self, "scale_spin") else 1.0
        if left.channel_key == right.channel_key:
            f, psd = self._psd_for_series(dataset, left, scale=global_scale * float(left.scale or 1.0))
            if f.size < 2:
                return None
            return np.interp(grid, f, psd, left=0.0, right=0.0).astype(complex)

        forward = f"{left.channel_key}->{right.channel_key}"
        reverse = f"{right.channel_key}->{left.channel_key}"
        cross_values = None
        conj_needed = False
        if forward in dataset.cross_spectra:
            cross_values = np.asarray(dataset.cross_spectra[forward], dtype=complex)
        elif reverse in dataset.cross_spectra:
            cross_values = np.asarray(dataset.cross_spectra[reverse], dtype=complex)
            conj_needed = True
        if cross_values is None:
            return None
        frequency = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float).ravel()
        count = min(frequency.size, cross_values.size)
        if count < 2:
            return None
        freq = frequency[:count]
        values = cross_values[:count]
        valid = (
            np.isfinite(freq)
            & (freq > 0.0)
            & np.isfinite(np.real(values))
            & np.isfinite(np.imag(values))
        )
        freq = freq[valid]
        values = values[valid]
        if freq.size < 2:
            return None
        order = np.argsort(freq)
        freq = freq[order]
        values = values[order]
        scale = (global_scale * float(left.scale or 1.0)) * (global_scale * float(right.scale or 1.0))
        values = values * scale
        if str(dataset.metadata.get("autospectrum_kind", "")).lower() != "psd":
            rbw = dataset.rbw_hz if dataset.rbw_hz > 0.0 else _infer_rbw(freq)
            values = values / max(float(rbw), 1e-20)
        if conj_needed:
            values = np.conj(values)
        real = np.interp(grid, freq, np.real(values), left=0.0, right=0.0)
        imag = np.interp(grid, freq, np.imag(values), left=0.0, right=0.0)
        return real + 1.0j * imag

    def _time_domain_cross_psd_matrix_for_targets(
        self,
        dataset: AnalysisDataset,
        series_items: list[AnalysisSeries],
        grid: np.ndarray,
    ) -> np.ndarray | None:
        if len(series_items) != 3 or grid.size < 2:
            return None
        start_s, end_s = self._analysis_window_for_dataset(dataset)
        global_scale = float(self.scale_spin.value()) if hasattr(self, "scale_spin") else 1.0
        loaded: list[np.ndarray] = []
        min_count: int | None = None
        for series in series_items:
            _t, values = self._load_analysis_time_series(
                dataset,
                series.channel_key,
                start_s=start_s,
                end_s=end_s,
            )
            arr = np.asarray(values, dtype=float).ravel()
            arr = arr[np.isfinite(arr)]
            if arr.size < 8:
                return None
            if min_count is None or arr.size < min_count:
                min_count = int(arr.size)
            loaded.append(arr)
        if min_count is None or min_count < 8:
            return None
        use_periodogram = "Periodogram" in self.psd_source_combo.currentText()
        filter_config = self._filter_config()
        matrix = np.zeros((grid.size, 3, 3), dtype=complex)
        processed: list[np.ndarray] = []
        for row, values in enumerate(loaded):
            work = values[:min_count] * global_scale * float(series_items[row].scale or 1.0)
            filtered, trim = apply_filter_to_signal(work, dataset.sample_rate, filter_config)
            if trim > 0 and filtered.size > trim * 2:
                filtered = filtered[trim:-trim]
            if filtered.size < 8:
                return None
            processed.append(np.asarray(filtered, dtype=float).ravel())
        min_processed = min(arr.size for arr in processed)
        if min_processed < 8:
            return None
        processed = [arr[:min_processed] for arr in processed]
        block_size = self._fft_block_size(min_processed, dataset)
        for row, row_values in enumerate(loaded):
            row_values = processed[row]
            for column, col_values in enumerate(processed):
                if row == column:
                    if use_periodogram or block_size >= row_values.size:
                        f, psd = compute_periodogram_psd(row_values, dataset.sample_rate)
                    else:
                        f, psd = compute_welch_psd(row_values, dataset.sample_rate, block_size)
                    if f.size < 2:
                        return None
                    matrix[:, row, column] = np.interp(grid, f, psd, left=0.0, right=0.0)
                else:
                    if use_periodogram or block_size >= row_values.size:
                        f, cross = compute_cross_spectrum_periodogram(row_values, col_values, dataset.sample_rate)
                    else:
                        f, cross = compute_cross_spectrum_welch(row_values, col_values, dataset.sample_rate, block_size)
                    if f.size < 2:
                        return None
                    real = np.interp(grid, f, np.real(cross), left=0.0, right=0.0)
                    imag = np.interp(grid, f, np.imag(cross), left=0.0, right=0.0)
                    matrix[:, row, column] = real + 1.0j * imag
        for index in range(grid.size):
            matrix[index] = 0.5 * (matrix[index] + np.conj(matrix[index].T))
        return matrix

    def _mimo_target_psd_matrix(
        self,
        target_items: list[object],
        grid: np.ndarray,
    ) -> tuple[np.ndarray | None, list[tuple[np.ndarray, np.ndarray, str, tuple[np.ndarray, np.ndarray] | None]] | None, str | None]:
        curves = self._mimo_target_curves(target_items, grid)
        if curves is None:
            return None, None, None

        dataset_refs: list[tuple[AnalysisDataset, AnalysisSeries]] = []
        same_dataset = True
        dataset_id: int | None = None
        for item in target_items:
            if not (isinstance(item, tuple) and len(item) == 2 and item[0] == "dataset_psd_curve"):
                same_dataset = False
                break
            pair = self._dataset_series_by_id(item[1])
            if pair is None:
                same_dataset = False
                break
            if dataset_id is None:
                dataset_id = pair[0].id
            elif pair[0].id != dataset_id:
                same_dataset = False
                break
            dataset_refs.append(pair)

        if same_dataset and len(dataset_refs) == 3:
            dataset = dataset_refs[0][0]
            series_items = [pair[1] for pair in dataset_refs]
            matrix = self._time_domain_cross_psd_matrix_for_targets(dataset, series_items, grid)
            if matrix is not None:
                return matrix, curves, "time_recomputed"

            matrix = np.zeros((grid.size, 3, 3), dtype=complex)
            matrix_ok = True
            for row, left in enumerate(series_items):
                for column, right in enumerate(series_items):
                    values = self._dataset_cross_psd_for_pair(dataset, left, right, grid)
                    if values is None:
                        matrix_ok = False
                        break
                    matrix[:, row, column] = values
                if not matrix_ok:
                    break
            if matrix_ok:
                for index in range(grid.size):
                    matrix[index] = 0.5 * (matrix[index] + np.conj(matrix[index].T))
                return matrix, curves, "cross_spectra"

        diagonal = np.empty((grid.size, 3), dtype=float)
        for index, curve in enumerate(curves):
            frequency, values, _label, _band_edges = curve
            f, psd = _finite_aligned_xy(frequency, values)
            valid = (f > 0.0) & (psd > 0.0)
            f = f[valid]
            psd = psd[valid]
            if f.size < 2:
                return None, curves, None
            diagonal[:, index] = np.interp(grid, f, psd, left=0.0, right=0.0)
        return diagonal_psd_matrix(diagonal), curves, "diagonal_only"

    def _mimo_target_curves(
        self,
        target_items: list[object],
        grid: np.ndarray,
    ) -> list[tuple[np.ndarray, np.ndarray, str, tuple[np.ndarray, np.ndarray] | None]] | None:
        previous_grid = self._mimo_current_grid
        self._mimo_current_grid = np.asarray(grid, dtype=float)
        try:
            target_curves = [self._acceleration_psd_from_mimo_target(item) for item in target_items]
        finally:
            self._mimo_current_grid = previous_grid
        if any(curve is None for curve in target_curves):
            return None
        return [curve for curve in target_curves if curve is not None]

    def _transfer_from_mimo_data(self, data: object) -> tuple[np.ndarray, np.ndarray, str, bool] | None:
        if not isinstance(data, tuple) or len(data) != 5:
            return None
        dataset_id, transfer_key, base_key, top_key, source_kind = data
        dataset = self._dataset_by_id(int(dataset_id))
        if dataset is None:
            return None
        base_series = self._series_for_transfer_endpoint(dataset, str(base_key))
        top_series = self._series_for_transfer_endpoint(dataset, str(top_key))
        if base_series is None or top_series is None:
            return None
        transfer = self._transfer_for_derived(
            dataset,
            str(transfer_key),
            str(source_kind),
            base_series,
            top_series,
            transfer_factor=self._derived_transfer_factor(),
            edit_key=self._transfer_edit_key_from_data(data),
        )
        if transfer is None:
            return None
        frequency, values, phase_available = transfer
        return frequency, values, self._series_label(dataset, top_series), phase_available

    def _mimo_time_transfer_matrix_from_rows(
        self,
        transfer_rows: list[object],
        *,
        regularization: float,
    ) -> tuple[np.ndarray, np.ndarray, list[str]] | None:
        if len(transfer_rows) != 3:
            return None
        parsed: list[list[tuple[int, str, str, str]]] = []
        dataset_id: int | None = None
        for row in transfer_rows:
            if not isinstance(row, list) or len(row) != 3:
                return None
            parsed_row: list[tuple[int, str, str, str]] = []
            for item in row:
                if not isinstance(item, tuple) or len(item) != 5:
                    return None
                item_dataset_id, _transfer_key, base_key, top_key, source_kind = item
                if str(source_kind) != "time":
                    return None
                current_dataset_id = int(item_dataset_id)
                if dataset_id is None:
                    dataset_id = current_dataset_id
                elif current_dataset_id != dataset_id:
                    return None
                parsed_row.append((current_dataset_id, str(base_key), str(top_key), str(source_kind)))
            parsed.append(parsed_row)
        dataset = self._dataset_by_id(dataset_id)
        if dataset is None or not np.isfinite(dataset.sample_rate) or dataset.sample_rate <= 0.0:
            return None

        input_series: list[AnalysisSeries] = []
        output_series: list[AnalysisSeries] = []
        for column in range(3):
            base_key = parsed[0][column][1]
            if any(parsed[row][column][1] != base_key for row in range(3)):
                return None
            series = self._series_for_transfer_endpoint(dataset, base_key)
            if series is None:
                return None
            input_series.append(series)
        for row in range(3):
            top_key = parsed[row][0][2]
            if any(parsed[row][column][2] != top_key for column in range(3)):
                return None
            series = self._series_for_transfer_endpoint(dataset, top_key)
            if series is None:
                return None
            output_series.append(series)
        if len({series.channel_key for series in input_series}) != 3 or len({series.channel_key for series in output_series}) != 3:
            return None

        start_s, end_s = self._analysis_window_for_dataset(dataset)
        loaded: list[np.ndarray] = []
        trims: list[int] = []
        min_count: int | None = None
        filter_config = self._filter_config()
        for series in input_series + output_series:
            _time_s, raw = self._load_analysis_time_series(
                dataset,
                series.channel_key,
                start_s=start_s,
                end_s=end_s,
            )
            values = np.asarray(raw, dtype=float).ravel()
            if values.size < 8:
                return None
            if min_count is None or values.size < min_count:
                min_count = int(values.size)
            loaded.append(values)
        if min_count is None or min_count < 8:
            return None

        processed: list[np.ndarray] = []
        for values, series in zip(loaded, input_series + output_series):
            filtered, trim = apply_filter_to_signal(values[:min_count], dataset.sample_rate, filter_config)
            trims.append(int(trim))
            processed.append(np.asarray(filtered, dtype=float).ravel() * float(series.scale or 1.0))
        trim = max(trims) if trims else 0
        if trim > 0:
            trimmed: list[np.ndarray] = []
            for values in processed:
                if values.size <= trim * 2:
                    return None
                trimmed.append(values[trim:-trim])
            processed = trimmed
        min_processed = min(values.size for values in processed)
        if min_processed < 8:
            return None
        processed = [values[:min_processed] for values in processed]
        inputs = np.vstack(processed[:3])
        outputs = np.vstack(processed[3:])
        block_size = self._fft_block_size(min_processed, dataset)
        frequency, matrix = compute_mimo_transfer_function_welch(
            inputs,
            outputs,
            dataset.sample_rate,
            block_size,
            regularization_floor=regularization,
        )
        if frequency.size < 2 or matrix.shape != (frequency.size, 3, 3):
            return None
        matrix = matrix * float(self._derived_transfer_factor())
        labels = [self._series_label(dataset, series) for series in output_series]
        return frequency, matrix, labels

    @staticmethod
    def _mimo_common_frequency_grid(curves: list[tuple[object, ...]]) -> np.ndarray:
        if not curves:
            return np.array([], dtype=float)
        lows: list[float] = []
        highs: list[float] = []
        points: list[np.ndarray] = []
        for curve in curves:
            if not isinstance(curve, tuple) or len(curve) < 2:
                return np.array([], dtype=float)
            f = np.asarray(curve[0], dtype=float).ravel()
            f = np.unique(np.sort(f[np.isfinite(f) & (f > 0.0)]))
            if f.size < 2:
                return np.array([], dtype=float)
            lows.append(float(f[0]))
            highs.append(float(f[-1]))
            points.append(f)
        low = max(lows)
        high = min(highs)
        if not high > low:
            return np.array([], dtype=float)
        grid = np.unique(np.concatenate([f[(f >= low) & (f <= high)] for f in points] + [np.array([low, high], dtype=float)]))
        return grid[np.isfinite(grid) & (grid > 0.0)]


    def _execute_mimo_coupling(self, config: dict[str, object]) -> bool:
        transfer_rows = config.get("transfers")
        target_items = config.get("targets")
        if not isinstance(transfer_rows, list) or len(transfer_rows) != 3 or not isinstance(target_items, list) or len(target_items) != 3:
            self.statusBar().showMessage("三轴耦合配置不完整")
            return False

        regularization = float(config.get("regularization", 0.0))
        transfer_curves: list[tuple[np.ndarray, np.ndarray, str, bool]] = []
        time_mimo_transfer = self._mimo_time_transfer_matrix_from_rows(
            transfer_rows,
            regularization=regularization,
        )
        if time_mimo_transfer is not None:
            frequency, transfer_matrix_from_time, output_labels = time_mimo_transfer
            for output_index in range(3):
                for input_index in range(3):
                    transfer_curves.append(
                        (
                            frequency,
                            transfer_matrix_from_time[:, output_index, input_index],
                            output_labels[output_index],
                            True,
                        )
                    )
        else:
            for row in transfer_rows:
                if not isinstance(row, list) or len(row) != 3:
                    self.statusBar().showMessage("请选择完整的 3x3 传递率矩阵")
                    return False
                for item in row:
                    transfer = self._transfer_from_mimo_data(item)
                    if transfer is None:
                        self.statusBar().showMessage("三轴耦合传递率中存在无效曲线")
                        return False
                    transfer_curves.append(transfer)

        grid = self._mimo_common_frequency_grid(transfer_curves)
        if grid.size < 2:
            self.statusBar().showMessage("三轴耦合传递率没有重叠频率范围")
            return False

        target_curves = self._mimo_target_curves(target_items, grid)
        if target_curves is None:
            self.statusBar().showMessage("三轴耦合目标曲线无效")
            return False

        all_curves = transfer_curves + [(curve[0], curve[1], curve[2]) for curve in target_curves]
        grid = self._mimo_common_frequency_grid(all_curves)
        if grid.size < 2:
            self.statusBar().showMessage("三轴耦合目标和传递率没有重叠频率范围")
            return False

        target_matrix, target_curves, target_matrix_mode = self._mimo_target_psd_matrix(target_items, grid)
        if target_matrix is None or target_curves is None:
            self.statusBar().showMessage("三轴耦合目标曲线无效")
            return False

        transfer_matrix = np.empty((grid.size, 3, 3), dtype=complex)
        transfer_phase_complete = True
        for output_index in range(3):
            for input_index in range(3):
                frequency, values, _label, phase_available = transfer_curves[output_index * 3 + input_index]
                transfer_phase_complete = transfer_phase_complete and bool(phase_available)
                transfer_matrix[:, output_index, input_index] = interpolate_complex_transfer(
                    frequency,
                    values,
                    grid,
                )

        target_psd = np.empty((grid.size, 3), dtype=float)
        target_labels: list[str] = []
        target_band_edges: list[tuple[np.ndarray, np.ndarray] | None] = []
        for index, curve in enumerate(target_curves):
            frequency, values, label, band_edges = curve
            f, psd = _finite_aligned_xy(frequency, values)
            valid = (f > 0.0) & (psd > 0.0)
            f = f[valid]
            psd = psd[valid]
            if f.size < 2:
                self.statusBar().showMessage("三轴耦合目标曲线有效频点不足")
                return False
            target_psd[:, index] = np.interp(grid, f, psd, left=0.0, right=0.0)
            target_labels.append(label)
            target_band_edges.append(_vc_band_edges_for_frequencies(label, grid) if label in VC_REFERENCE_NAMES else band_edges)

        direction = str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP)
        input_endpoint, target_endpoint = self._mimo_direction_endpoints(direction)
        solve_f = grid
        solve_transfer_matrix = transfer_matrix

        if target_matrix_mode in {"cross_spectra", "time_recomputed"} and transfer_phase_complete:
            out_f, input_matrix = invert_mimo_input_psd(
                solve_f,
                solve_transfer_matrix,
                target_matrix,
                regularization_floor=regularization,
            )
            if out_f.size < 2 or input_matrix.ndim != 3:
                self.statusBar().showMessage("三轴耦合计算失败：未得到有效输入互谱矩阵")
                return False
            _pred_f, predicted_matrix = predict_mimo_response_psd(
                out_f,
                solve_transfer_matrix[: out_f.size],
                input_matrix,
            )
            input_psd = psd_matrix_diagonal(input_matrix)
            predicted_psd = psd_matrix_diagonal(predicted_matrix)
        else:
            if target_matrix_mode in {"cross_spectra", "time_recomputed"} and not transfer_phase_complete:
                target_matrix_mode = "diagonal_only"
            out_f, input_psd, predicted_psd = solve_mimo_independent_psd(
                solve_f,
                solve_transfer_matrix,
                target_psd,
                regularization_floor=regularization,
            )
            if out_f.size < 2 or input_psd.shape != (out_f.size, 3):
                self.statusBar().showMessage("三轴耦合计算失败：未得到有效输入 PSD")
                return False

        default_prefix = self._mimo_default_output_prefix(direction)
        prefix = str(config.get("prefix") or default_prefix).strip() or default_prefix
        axes = ("X", "Y", "Z")
        results: list[dict[str, object]] = []
        for index, axis in enumerate(axes):
            f_quantity, psd_quantity = convert_acceleration_psd(
                out_f,
                input_psd[:, index],
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if f_quantity.size < 2:
                continue
            label = f"{prefix}{axis}"
            results.append(
                {
                    "label": label,
                    "source_label": f"目标{target_endpoint}响应{axis}: {target_labels[index]}",
                    "psd": (f_quantity, psd_quantity),
                    "cumulative": compute_cumulative_spectrum(f_quantity, psd_quantity),
                    "foundation": compute_third_octave_velocity_rms(out_f, input_psd[:, index], _infer_rbw(out_f)),
                }
            )
            if target_matrix_mode in {"cross_spectra", "time_recomputed"}:
                results[-1]["mimo_time_group"] = "input"
                results[-1]["mimo_axis_index"] = index
            self._save_workspace_curve(label, "三轴耦合输入PSD", f_quantity, psd_quantity, "MIMO三轴耦合反推")

        for index, axis in enumerate(axes):
            f_quantity, psd_quantity = convert_acceleration_psd(
                out_f,
                predicted_psd[:, index],
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if f_quantity.size < 2:
                continue
            results.append(
                {
                    "label": f"校核{target_endpoint}响应{axis}",
                    "source_label": f"目标{target_endpoint}响应{axis}: {target_labels[index]}",
                    "psd": (f_quantity, psd_quantity),
                    "display_psd": (f_quantity, psd_quantity),
                    "cumulative": compute_cumulative_spectrum(f_quantity, psd_quantity),
                    "foundation": compute_third_octave_velocity_rms(out_f, predicted_psd[:, index], _infer_rbw(out_f)),
                    "psd_band_edges": target_band_edges[index],
                }
            )
            if target_matrix_mode in {"cross_spectra", "time_recomputed"}:
                results[-1]["mimo_time_group"] = "response"
                results[-1]["mimo_axis_index"] = index

        if not results:
            self.statusBar().showMessage("三轴耦合计算没有可绘制结果")
            return False

        if target_matrix_mode in {"cross_spectra", "time_recomputed"}:
            for result in results:
                group = result.get("mimo_time_group")
                if group == "input":
                    result["mimo_time_matrix"] = input_matrix
                    result["mimo_time_frequency"] = out_f
                elif group == "response":
                    result["mimo_time_matrix"] = predicted_matrix
                    result["mimo_time_frequency"] = out_f

        self.derived_result_mode_combo.setCurrentText("PSD")
        self._last_derived_results = [dict(result) for result in results]
        self._plot_derived_result_axis(
            self.derived_plots[1],
            "PSD",
            results,
            keep_existing=self._hold_enabled(),
        )
        mode_text = {
            "cross_spectra": "完整互谱反演",
            "time_recomputed": "时域重算互谱反演",
            "diagonal_only": "独立PSD近似",
        }.get(target_matrix_mode or "", "三轴耦合")
        self.statusBar().showMessage(
            f"{mode_text}完成：已生成 X/Y/Z {input_endpoint}输入 PSD，并完成{target_endpoint}响应校核"
        )
        return True

    def _show_workspace_source_selector(self, target: str) -> None:
        options = self._workspace_source_options()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择工作区输入")
        dialog.setModal(True)
        dialog.resize(760, 420)
        layout = QtWidgets.QVBoxLayout(dialog)
        search_edit = QtWidgets.QLineEdit()
        search_edit.setPlaceholderText("搜索名称、类型或来源")
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["类型", "数据"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(search_edit)
        layout.addWidget(table)
        button_row = QtWidgets.QHBoxLayout()
        select_button = QtWidgets.QPushButton("选择")
        cancel_button = QtWidgets.QPushButton("取消")
        button_row.addStretch(1)
        button_row.addWidget(select_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        def refresh_table() -> None:
            pattern = search_edit.text().strip().lower()
            table.setRowCount(0)
            for kind, label, data in options:
                searchable = f"{kind} {label}".lower()
                if pattern and pattern not in searchable:
                    continue
                row = table.rowCount()
                table.insertRow(row)
                kind_item = QtWidgets.QTableWidgetItem(kind)
                label_item = QtWidgets.QTableWidgetItem(label)
                kind_item.setData(QtCore.Qt.UserRole, data)
                label_item.setData(QtCore.Qt.UserRole, data)
                table.setItem(row, 0, kind_item)
                table.setItem(row, 1, label_item)
            if table.rowCount() > 0:
                table.selectRow(0)

        def choose_current() -> None:
            selected = table.selectedIndexes()
            if not selected:
                return
            item = table.item(selected[0].row(), 0)
            if item is None:
                return
            self._workspace_operation_sources[target] = item.data(QtCore.Qt.UserRole)
            self._sync_workspace_operation_labels()
            dialog.accept()

        search_edit.textChanged.connect(refresh_table)
        table.itemDoubleClicked.connect(lambda _item: choose_current())
        select_button.clicked.connect(choose_current)
        cancel_button.clicked.connect(dialog.reject)
        refresh_table()
        dialog.exec()

    def _workspace_curve_by_id(self, curve_id: int) -> WorkspaceCurve | None:
        for curve in self._workspace_curves:
            if curve.curve_id == curve_id:
                return curve
        return None

    def _workspace_source_label(self, data: object | None) -> str:
        if data is None:
            return "(未选择)"
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "workspace_curve":
            curve = self._workspace_curve_by_id(int(data[1]))
            return curve.name if curve is not None else "(工作区曲线已失效)"
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "current_result_curve":
            return str(data[1])
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "dataset_psd_curve":
            for dataset in self._datasets:
                for series in dataset.series:
                    if series.id == data[1]:
                        return self._series_label(dataset, series)
            return "(导入曲线已失效)"
        return str(data)

    def _sync_workspace_operation_labels(self) -> None:
        if not hasattr(self, "workspace_op_labels"):
            return
        for key, label in self.workspace_op_labels.items():
            full_text = self._workspace_source_label(self._workspace_operation_sources.get(key))
            shown = full_text if len(full_text) <= 42 else f"{full_text[:39]}..."
            label.setText(shown)
            label.setToolTip(full_text)

    def _refresh_workspace_curve_table(self) -> None:
        if not hasattr(self, "workspace_curve_table"):
            return
        selected_ids = {
            self.workspace_curve_table.item(index.row(), 0).data(QtCore.Qt.UserRole)
            for index in self.workspace_curve_table.selectedIndexes()
            if self.workspace_curve_table.item(index.row(), 0) is not None
        }
        self.workspace_curve_table.setRowCount(0)
        for row, curve in enumerate(self._workspace_curves):
            self.workspace_curve_table.insertRow(row)
            name_item = QtWidgets.QTableWidgetItem(curve.name)
            name_item.setData(QtCore.Qt.UserRole, curve.curve_id)
            type_item = QtWidgets.QTableWidgetItem(curve.curve_type)
            source_item = QtWidgets.QTableWidgetItem(curve.source)
            source_item.setToolTip(curve.source)
            self.workspace_curve_table.setItem(row, 0, name_item)
            self.workspace_curve_table.setItem(row, 1, type_item)
            self.workspace_curve_table.setItem(row, 2, source_item)
            if curve.curve_id in selected_ids:
                self.workspace_curve_table.selectRow(row)

    def _selected_workspace_curve_ids(self) -> list[int]:
        ids: list[int] = []
        if not hasattr(self, "workspace_curve_table"):
            return ids
        for index in self.workspace_curve_table.selectionModel().selectedRows():
            item = self.workspace_curve_table.item(index.row(), 0)
            if item is not None:
                curve_id = item.data(QtCore.Qt.UserRole)
                if isinstance(curve_id, int):
                    ids.append(curve_id)
        return ids

    def _unique_workspace_curve_name(self, name: str) -> str:
        base = str(name or "工作区曲线").strip() or "工作区曲线"
        existing = {curve.name for curve in self._workspace_curves}
        if base not in existing:
            return base
        index = 2
        while f"{base}#{index}" in existing:
            index += 1
        return f"{base}#{index}"

    def _save_workspace_curve(
        self,
        name: str,
        curve_type: str,
        frequency_hz: np.ndarray,
        values: np.ndarray,
        source: str,
    ) -> WorkspaceCurve | None:
        f, y = _finite_aligned_xy(frequency_hz, values)
        positive = np.isfinite(f) & np.isfinite(y) & (f > 0.0) & (y > 0.0)
        f = f[positive]
        y = y[positive]
        if f.size < 2:
            return None
        order = np.argsort(f)
        curve = WorkspaceCurve(
            curve_id=self._next_workspace_curve_id,
            name=self._unique_workspace_curve_name(name),
            curve_type=curve_type,
            frequency_hz=np.asarray(f[order], dtype=float),
            values=np.asarray(y[order], dtype=float),
            source=str(source),
        )
        self._next_workspace_curve_id += 1
        self._workspace_curves.append(curve)
        self._refresh_workspace_curve_table()
        self._sync_workspace_operation_labels()
        return curve

    def _save_current_psd_curve_to_workspace(self) -> None:
        if self.derived_result_mode_combo.currentText() != "PSD":
            self.statusBar().showMessage("请先切换到 PSD 结果图窗后再加入工作区")
            return
        if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
            return
        plot = self.derived_plots[1]
        curves = self._plot_curves.get(plot, {})
        excluded = self._plot_export_excluded.get(plot, set())
        if not curves:
            self.statusBar().showMessage("当前没有可保存的 PSD 曲线")
            return
        label = self._active_trace.get(plot)
        if label not in curves or label in excluded:
            label = next((name for name in curves if name not in excluded), None)
        if label is None:
            self.statusBar().showMessage("当前没有可保存的 PSD 曲线")
            return
        x, y = curves[label]
        saved = self._save_workspace_curve(label, "换算结果PSD", x, y, f"当前结果 | {label}")
        if saved is None:
            self.statusBar().showMessage("保存当前 PSD 失败：曲线无有效数据")
            return
        self.statusBar().showMessage(f"已加入工作区：{saved.name}")

    def _delete_selected_workspace_curves(self) -> None:
        selected_ids = set(self._selected_workspace_curve_ids())
        if not selected_ids:
            self.statusBar().showMessage("未选择工作区曲线")
            return
        self._workspace_curves = [curve for curve in self._workspace_curves if curve.curve_id not in selected_ids]
        for key, data in list(self._workspace_operation_sources.items()):
            if isinstance(data, tuple) and len(data) == 2 and data[0] == "workspace_curve" and data[1] in selected_ids:
                self._workspace_operation_sources[key] = None
        self._refresh_workspace_curve_table()
        self._sync_workspace_operation_labels()
        self.statusBar().showMessage(f"已删除 {len(selected_ids)} 条工作区曲线")

    def _plot_selected_workspace_curves(self) -> None:
        selected_ids = self._selected_workspace_curve_ids()
        curves = [self._workspace_curve_by_id(curve_id) for curve_id in selected_ids]
        curves = [curve for curve in curves if curve is not None]
        if not curves:
            self.statusBar().showMessage("未选择工作区曲线")
            return
        target_mode = self.derived_result_mode_combo.currentText()
        if target_mode not in {"PSD", "近似时域"}:
            target_mode = "PSD"
            self.derived_result_mode_combo.setCurrentText(target_mode)
        results = [{"label": curve.name, "psd": (curve.frequency_hz, curve.values)} for curve in curves]
        self._plot_derived_result_axis(
            self.derived_plots[1],
            target_mode,
            results,
            keep_existing=self._hold_enabled(),
        )
        self.statusBar().showMessage(f"绘制了 {len(curves)} 条工作区曲线")

    def _curve_from_workspace_source(self, data: object | None) -> tuple[np.ndarray, np.ndarray, str] | None:
        if data is None:
            return None
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "workspace_curve":
            curve = self._workspace_curve_by_id(int(data[1]))
            if curve is None:
                return None
            return curve.frequency_hz.copy(), curve.values.copy(), curve.name
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "current_result_curve":
            if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
                return None
            label = str(data[1])
            curve = self._plot_curves.get(self.derived_plots[1], {}).get(label)
            if curve is None:
                return None
            return np.asarray(curve[0], dtype=float), np.asarray(curve[1], dtype=float), label
        if isinstance(data, tuple) and len(data) == 2 and data[0] == "dataset_psd_curve":
            for dataset in self._datasets:
                for series in dataset.series:
                    if series.id == data[1]:
                        curve = self._curve_for_mode(dataset, series, "PSD")
                        if curve is None:
                            return None
                        return curve
        return None

    def _aligned_psd_operation(
        self,
        left_x: np.ndarray,
        left_y: np.ndarray,
        right_x: np.ndarray,
        right_y: np.ndarray,
        *,
        subtract: bool,
    ) -> tuple[np.ndarray, np.ndarray, bool]:
        lx, ly = _finite_aligned_xy(left_x, left_y)
        rx, ry = _finite_aligned_xy(right_x, right_y)
        valid_left = (lx > 0.0) & (ly > 0.0)
        valid_right = (rx > 0.0) & (ry > 0.0)
        lx = lx[valid_left]
        ly = ly[valid_left]
        rx = rx[valid_right]
        ry = ry[valid_right]
        if lx.size < 2 or rx.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float), False
        low = max(float(np.min(lx)), float(np.min(rx)))
        high = min(float(np.max(lx)), float(np.max(rx)))
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            return np.array([], dtype=float), np.array([], dtype=float), False
        grid = np.unique(
            np.concatenate(
                (
                    lx[(lx >= low) & (lx <= high)],
                    rx[(rx >= low) & (rx <= high)],
                    np.array([low, high], dtype=float),
                )
            )
        )
        if grid.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float), False
        left_interp = np.interp(np.log10(grid), np.log10(lx), ly)
        right_interp = np.interp(np.log10(grid), np.log10(rx), ry)
        values = left_interp - right_interp if subtract else left_interp + right_interp
        clipped = bool(np.any(values <= 0.0))
        values = np.maximum(values, 1e-300)
        return grid, values, clipped

    def _execute_workspace_operation(self) -> None:
        left_source = self._curve_from_workspace_source(self._workspace_operation_sources.get("a"))
        right_source = self._curve_from_workspace_source(self._workspace_operation_sources.get("b"))
        if left_source is None or right_source is None:
            self.statusBar().showMessage("请先为输入A和输入B选择有效曲线")
            return
        left_x, left_y, left_label = left_source
        right_x, right_y, right_label = right_source
        operation = self.workspace_op_type_combo.currentData()
        output_name = self.workspace_op_output_edit.text().strip()
        curve_type = "工作区PSD"
        source_text = ""
        clipped = False
        if operation == "stitch":
            split = _parse_optional_float(self.workspace_op_split_edit.text())
            if split is None:
                self.statusBar().showMessage("请输入有效的拼合分界频率")
                return
            left_range = (float(np.min(left_x)), float(np.max(left_x)))
            right_range = (float(np.min(right_x)), float(np.max(right_x)))
            overlap_low = max(left_range[0], right_range[0])
            overlap_high = min(left_range[1], right_range[1])
            if overlap_high < overlap_low:
                self.statusBar().showMessage(
                    f"拼合失败：两条曲线存在频段断裂 ({min(left_range[1], right_range[1]):g} - {max(left_range[0], right_range[0]):g} Hz)"
                )
                return
            if not overlap_low <= float(split) <= overlap_high:
                self.statusBar().showMessage(f"拼合失败：分界频率应位于重叠频段 {overlap_low:g} - {overlap_high:g} Hz")
                return
            if self.workspace_op_order_combo.currentData() == "b_first":
                primary_x, primary_y, secondary_x, secondary_y = right_x, right_y, left_x, left_y
                if not output_name:
                    output_name = f"{right_label}|{left_label}@{float(split):.6g}Hz"
                source_text = f"拼合: {right_label} -> {left_label} @ {float(split):.6g}Hz"
            else:
                primary_x, primary_y, secondary_x, secondary_y = left_x, left_y, right_x, right_y
                if not output_name:
                    output_name = f"{left_label}|{right_label}@{float(split):.6g}Hz"
                source_text = f"拼合: {left_label} -> {right_label} @ {float(split):.6g}Hz"
            out_x, out_y = stitch_frequency_curves(primary_x, primary_y, secondary_x, secondary_y, float(split))
            if self.workspace_stitch_blend_check.isChecked():
                width = float(self.workspace_stitch_blend_width_spin.value())
                blend_low = max(overlap_low, float(split) - width / 2.0)
                blend_high = min(overlap_high, float(split) + width / 2.0)
                if blend_high > blend_low:
                    blend_grid = np.unique(
                        np.concatenate((
                            np.asarray(primary_x)[(np.asarray(primary_x) >= blend_low) & (np.asarray(primary_x) <= blend_high)],
                            np.asarray(secondary_x)[(np.asarray(secondary_x) >= blend_low) & (np.asarray(secondary_x) <= blend_high)],
                            np.array([blend_low, blend_high]),
                        ))
                    )
                    primary_interp = np.interp(np.log10(blend_grid), np.log10(primary_x), primary_y)
                    secondary_interp = np.interp(np.log10(blend_grid), np.log10(secondary_x), secondary_y)
                    weight = (blend_grid - blend_low) / (blend_high - blend_low)
                    blend_values = np.exp((1.0 - weight) * np.log(np.maximum(primary_interp, 1e-300)) + weight * np.log(np.maximum(secondary_interp, 1e-300)))
                    outside = (out_x < blend_low) | (out_x > blend_high)
                    out_x = np.concatenate((out_x[outside], blend_grid))
                    out_y = np.concatenate((out_y[outside], blend_values))
                    order = np.argsort(out_x)
                    out_x, out_y = out_x[order], out_y[order]
                    source_text += f"，平滑过渡 {width:g} Hz"
            curve_type = "拼合结果PSD"
        elif operation == "subtract":
            out_x, out_y, clipped = self._aligned_psd_operation(left_x, left_y, right_x, right_y, subtract=True)
            if not output_name:
                output_name = f"{left_label} - {right_label}"
            source_text = f"相减: {left_label} - {right_label}"
            curve_type = "相减结果PSD"
        else:
            out_x, out_y, _clipped_unused = self._aligned_psd_operation(left_x, left_y, right_x, right_y, subtract=False)
            if not output_name:
                output_name = f"{left_label} + {right_label}"
            source_text = f"相加: {left_label} + {right_label}"
            curve_type = "相加结果PSD"
        saved = self._save_workspace_curve(output_name, curve_type, out_x, out_y, source_text)
        if saved is None:
            self.statusBar().showMessage("运算失败：输出曲线没有有效频点")
            return
        self.workspace_op_output_edit.clear()
        self.derived_result_mode_combo.setCurrentText("PSD")
        self._plot_derived_result_axis(
            self.derived_plots[1],
            "PSD",
            [{"label": saved.name, "psd": (saved.frequency_hz, saved.values)}],
            keep_existing=self._hold_enabled(),
        )
        if clipped:
            self.statusBar().showMessage(f"已保存到工作区：{saved.name}（相减结果含非正值，已截断到最小正值）")
        else:
            self.statusBar().showMessage(f"已保存到工作区：{saved.name}")

    def _show_derived_curve_dialog(self) -> None:
        if self.derived_curve_dialog is None:
            return
        self.derived_curve_dialog.show()
        self.derived_curve_dialog.raise_()
        self.derived_curve_dialog.activateWindow()

    def _load_file(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Load analysis file(s)",
            str(self._last_directory),
            "Data Files (*.vna *.mat *.txt *.csv *.dat);;All Files (*.*)",
        )
        if not paths:
            return
        path_objects = [Path(path) for path in paths]
        import_kind = self._prompt_import_kind_for_paths(path_objects)
        if import_kind == "cancel":
            return
        self._dispatch_load_paths(path_objects, import_kind=import_kind)

    def _load_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Load continuous recording folder or data folder",
            str(self._last_directory),
        )
        if not path:
            return
        folder = Path(path)
        self._last_directory = folder
        manifest = folder / "manifest.json"
        if manifest.exists():
            self._dispatch_load_paths([folder])
            return
        paths = _supported_files_in_folder(folder)
        if not paths:
            QtWidgets.QMessageBox.warning(self, "Load failed", "Folder does not contain supported analysis files.")
            self.statusBar().showMessage("Load failed: folder has no supported analysis files")
            return
        self._dispatch_load_paths(paths, quiet_failures=True)

    def _load_path(self, path: Path, *, import_kind: str | None = None) -> None:
        self._dispatch_load_paths([path], import_kind=import_kind)

    def _dispatch_load_paths(
        self,
        paths: list[Path],
        *,
        quiet_failures: bool = False,
        import_kind: str | None = None,
    ) -> None:
        use_background = False
        if self._derived_only and self.isVisible() and paths:
            total_bytes = 0
            for path in paths:
                try:
                    total_bytes += int(path.stat().st_size) if path.is_file() else 16 * 1024 * 1024
                except OSError:
                    pass
            use_background = len(paths) > 1 or total_bytes >= 16 * 1024 * 1024
        if use_background:
            self._load_paths_in_background(paths, import_kind=import_kind)
            return
        self._load_paths(paths, quiet_failures=quiet_failures, import_kind=import_kind)

    def _load_paths_in_background(self, paths: list[Path], *, import_kind: str | None) -> None:
        if self._background_load_task is not None:
            self.statusBar().showMessage("已有数据加载任务正在运行")
            return
        self._background_load_paths = list(paths)
        self._background_load_rows = [(path.name, "等待", "") for path in paths]
        task = AnalysisLoadTask(paths, first_dataset_id=self._next_dataset_id, import_kind=import_kind)
        self._background_load_task = task
        progress = QtWidgets.QProgressDialog("正在后台加载数据...", "取消", 0, len(paths), self)
        progress.setWindowTitle("加载数据")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(task.cancel)
        self._background_load_progress = progress
        task.signals.item_finished.connect(self._on_background_load_item_finished)
        task.signals.finished.connect(self._on_background_load_finished)
        QtCore.QThreadPool.globalInstance().start(task)
        self.statusBar().showMessage(f"已开始后台加载 {len(paths)} 个数据源")

    @QtCore.Slot(int, object, object)
    def _on_background_load_item_finished(self, index: int, dataset: object, error: object) -> None:
        if index < 0 or index >= len(self._background_load_rows):
            return
        path = self._background_load_paths[index]
        if isinstance(dataset, AnalysisDataset):
            self._apply_loaded_dataset_ui_defaults(dataset)
            self._datasets.append(dataset)
            self._next_dataset_id = max(self._next_dataset_id, int(dataset.id) + 1)
            self._background_load_rows[index] = (path.name, "成功", "")
        else:
            self._background_load_rows[index] = (path.name, "失败", str(error or "无法识别数据"))
        progress = self._background_load_progress
        if progress is not None:
            progress.setLabelText(f"已处理 {index + 1}/{len(self._background_load_paths)}")
            progress.setValue(index + 1)

    @QtCore.Slot(bool)
    def _on_background_load_finished(self, cancelled: bool) -> None:
        if cancelled:
            for index, (name, status, detail) in enumerate(self._background_load_rows):
                if status == "等待":
                    self._background_load_rows[index] = (name, "跳过", "用户取消加载")
        self._last_load_report = list(self._background_load_rows)
        loaded_count = sum(status == "成功" for _name, status, _detail in self._last_load_report)
        failed_count = sum(status == "失败" for _name, status, _detail in self._last_load_report)
        skipped_count = len(self._last_load_report) - loaded_count - failed_count
        if self._background_load_paths:
            last_path = self._background_load_paths[-1]
            self._last_directory = last_path if last_path.is_dir() else last_path.parent
        if self._background_load_progress is not None:
            self._background_load_progress.close()
        self._background_load_progress = None
        self._background_load_task = None
        self._background_load_paths = []
        self._background_load_rows = []
        if hasattr(self, "derived_load_report_button"):
            self.derived_load_report_button.setEnabled(bool(self._last_load_report))
        if loaded_count:
            self._derived_result_cache.clear()
            self._last_derived_results = None
        self._refresh_dataset_lists()
        if loaded_count:
            self._notify_data_store_changed("load", self)
            self._mark_derived_results_stale("已加载新数据")
        self.statusBar().showMessage(
            f"后台加载完成：成功 {loaded_count}，失败 {failed_count}，跳过 {skipped_count}"
        )

    def _load_paths(
        self,
        paths: list[Path],
        *,
        quiet_failures: bool = False,
        import_kind: str | None = None,
    ) -> None:
        loaded: list[str] = []
        failed: list[str] = []
        report_rows: list[tuple[str, str, str]] = []
        progress = None
        if len(paths) > 1 and self.isVisible():
            progress = QtWidgets.QProgressDialog("正在加载数据...", "取消", 0, len(paths), self)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(250)
        for index, path in enumerate(paths):
            if progress is not None:
                progress.setValue(index)
                progress.setLabelText(f"正在加载 {path.name} ({index + 1}/{len(paths)})")
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    report_rows.extend((remaining.name, "跳过", "用户取消加载") for remaining in paths[index:])
                    break
            if self._load_one_path(path, quiet=quiet_failures, import_kind=import_kind):
                loaded.append(path.name if not path.is_dir() else path.name)
                report_rows.append((path.name, "成功", ""))
            else:
                failed.append(path.name if not path.is_dir() else path.name)
                report_rows.append((path.name, "失败", getattr(self, "_last_load_failure_reason", "无法识别数据")))
        if progress is not None:
            progress.setValue(len(paths))
            progress.close()
        self._last_load_report = report_rows
        if hasattr(self, "derived_load_report_button"):
            self.derived_load_report_button.setEnabled(bool(report_rows))
        if paths:
            self._last_directory = paths[-1] if paths[-1].is_dir() else paths[-1].parent
        if loaded:
            self._derived_result_cache.clear()
            self._last_derived_results = None
        self._refresh_dataset_lists()
        if loaded:
            self._notify_data_store_changed("load", self)
            self._mark_derived_results_stale("已加载新数据")
        if failed:
            self.statusBar().showMessage(f"加载完成：成功 {len(loaded)}，失败 {len(failed)}；点击“加载详情”查看原因")
        elif loaded:
            self.statusBar().showMessage(f"加载完成：成功 {len(loaded)}，失败 0；请选择数据后计算")

    def _prompt_import_kind_for_paths(self, paths: list[Path]) -> str | None:
        if self._derived_only:
            return None
        if not any(path.suffix.lower() in {".csv", ".mat", ".dat"} for path in paths if not path.is_dir()):
            return None
        choices = ["自动识别", "时域数据", "PSD数据", "传递率数据"]
        selected, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "导入数据类型",
            "CSV / MAT / DAT 数据按哪种类型读取？",
            choices,
            0,
            False,
        )
        if not accepted:
            return "cancel"
        return {
            "自动识别": None,
            "时域数据": "time",
            "PSD数据": "psd",
            "传递率数据": "transfer",
        }.get(str(selected), None)

    def _load_one_path(self, path: Path, *, quiet: bool = False, import_kind: str | None = None) -> bool:
        self._last_load_failure_reason = ""
        try:
            dataset = load_analysis_path(
                path,
                fs_hint=TEXT_FILE_FS_HINT_HZ,
                dataset_id=self._next_dataset_id,
                import_kind=import_kind,
            )
        except Exception as exc:
            self._last_load_failure_reason = str(exc)
            if not quiet:
                QtWidgets.QMessageBox.warning(self, "Load failed", str(exc))
                self.statusBar().showMessage(f"Load failed: {exc}")
            return False
        self._apply_loaded_dataset_ui_defaults(dataset)
        self._next_dataset_id += 1
        self._datasets.append(dataset)
        return True

    def _apply_loaded_dataset_ui_defaults(self, dataset: AnalysisDataset) -> None:
        if not hasattr(self, "foundation_vib_edit"):
            return
        if str(dataset.metadata.get("source", "")) != "floor_response_eu_ascii":
            return
        current = self.foundation_vib_edit.text().strip().replace(" ", "")
        if current not in {"", "2,3,4"}:
            return
        channel_count = min(3, len(dataset.series))
        if channel_count >= 1:
            self.foundation_vib_edit.setText(",".join(str(index) for index in range(1, channel_count + 1)))

    def set_current_measurement_provider(self, provider) -> None:
        self._current_measurement_provider = provider

    def sync_current_measurement(self, measurement, session_config=None) -> bool:
        if measurement is None:
            return False
        if self._current_measurement_dataset_id is not None:
            self._discard_dataset_caches({self._current_measurement_dataset_id})
            self._datasets = [
                dataset
                for dataset in self._datasets
                if dataset.id != self._current_measurement_dataset_id
            ]
        dataset_id = self._next_dataset_id
        dataset = dataset_from_measurement(
            measurement,
            session_config=session_config,
            dataset_id=dataset_id,
            name="Current Measurement",
        )
        self._next_dataset_id += 1
        self._current_measurement_dataset_id = dataset_id
        self._datasets.insert(0, dataset)
        self._refresh_dataset_lists()
        self._notify_data_store_changed("current_measurement", self)
        self.statusBar().showMessage("Synced current main-window measurement")
        return True

    def refresh_data_sources(self) -> None:
        selected_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        refreshed: list[AnalysisDataset] = []
        failed: list[str] = []
        if self._current_measurement_provider is not None:
            try:
                measurement, session_config = self._current_measurement_provider()
                if measurement is not None:
                    dataset_id = self._current_measurement_dataset_id or self._next_dataset_id
                    if self._current_measurement_dataset_id is None:
                        self._next_dataset_id += 1
                    dataset = dataset_from_measurement(
                        measurement,
                        session_config=session_config,
                        dataset_id=dataset_id,
                        name="Current Measurement",
                    )
                    self._current_measurement_dataset_id = dataset_id
                    refreshed.append(dataset)
            except Exception as exc:
                failed.append(f"Current Measurement ({exc})")

        for dataset in list(self._datasets):
            if dataset.id == self._current_measurement_dataset_id:
                if self._current_measurement_provider is None:
                    refreshed.append(dataset)
                continue
            try:
                reloaded = load_analysis_path(
                    dataset.path,
                    fs_hint=TEXT_FILE_FS_HINT_HZ,
                    dataset_id=dataset.id,
                    import_kind=dataset.metadata.get("import_kind"),
                )
            except Exception as exc:
                failed.append(f"{dataset.name} ({exc})")
                refreshed.append(dataset)
                continue
            refreshed.append(reloaded)

        self._datasets = refreshed
        self._time_series_cache.clear()
        self._bulk_time_series_cache.clear()
        self._derived_result_cache.clear()
        if self._datasets:
            self._next_dataset_id = max(self._next_dataset_id, max(dataset.id for dataset in self._datasets) + 1)
        self._refresh_dataset_lists()
        self._notify_data_store_changed("refresh", self)
        for index in range(self.series_list.count()):
            item = self.series_list.item(index)
            item.setSelected(item.data(QtCore.Qt.UserRole) in selected_ids)
        self._auto_plot_from_control_change()
        if failed:
            self.statusBar().showMessage(f"Refreshed data with {len(failed)} warning(s)")
        else:
            self.statusBar().showMessage("Refreshed analysis data")

    def _clear_datasets(self) -> None:
        self._discard_dataset_caches()
        self._datasets.clear()
        self._series_labels.clear()
        self._custom_series_labels.clear()
        self._custom_series_scales.clear()
        self._original_series_scales.clear()
        self._current_measurement_dataset_id = None
        self._refresh_dataset_lists()
        self._sync_workspace_operation_labels()
        self._clear_plots(show_status=False)
        self._notify_data_store_changed("clear", self)
        self.statusBar().showMessage("Analysis data cleared")

    def _delete_selected_datasets(self) -> None:
        self._request_delete_selected_datasets()

    def _request_delete_selected_datasets(self) -> None:
        selected_dataset_ids = self._selected_dataset_ids_from_series_selection()
        append_log(
            f"analysis.delete.request selected={sorted(selected_dataset_ids)} "
            f"datasets={len(self._datasets)} series_items={self.series_list.count()}"
        )
        if not selected_dataset_ids:
            self.statusBar().showMessage("No selected data to delete")
            append_log("analysis.delete.request.empty")
            return
        if self._delete_in_progress:
            append_log("analysis.delete.request.skip_in_progress")
            return
        self._delete_in_progress = True
        if hasattr(self, "clear_button"):
            self.clear_button.setEnabled(False)
        append_log("analysis.delete.request.timer_schedule")
        QtCore.QTimer.singleShot(0, lambda ids=set(selected_dataset_ids): self._finish_scheduled_dataset_delete(ids))

    def _finish_scheduled_dataset_delete(self, selected_dataset_ids: set[int]) -> None:
        append_log(f"analysis.delete.finish.begin selected={sorted(selected_dataset_ids)}")
        try:
            self._delete_datasets_by_ids(selected_dataset_ids)
        finally:
            append_log("analysis.delete.finish.finally")
            self._delete_in_progress = False
            if hasattr(self, "clear_button"):
                self.clear_button.setEnabled(True)
            append_log("analysis.delete.finish.end")

    def _selected_dataset_ids_from_series_selection(self) -> set[int]:
        selected_series_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        return {
            dataset.id
            for dataset in self._datasets
            for series in dataset.series
            if series.id in selected_series_ids
        }

    def _delete_selected_config_datasets(self) -> None:
        if not hasattr(self, "derived_config_dataset_list"):
            return
        selected_dataset_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.derived_config_dataset_list.selectedItems()
            if isinstance(item.data(QtCore.Qt.UserRole), int)
        }
        self._delete_datasets_by_ids(selected_dataset_ids)

    def _delete_datasets_by_ids(self, selected_dataset_ids: set[int]) -> None:
        append_log(f"analysis.delete.by_ids.begin selected={sorted(selected_dataset_ids)}")
        if not selected_dataset_ids:
            self.statusBar().showMessage("No selected data to delete")
            append_log("analysis.delete.by_ids.empty")
            return
        previous_suspend = self._suspend_auto_plot
        self._suspend_auto_plot = True
        try:
            append_log(f"analysis.delete.by_ids.before_filter datasets={len(self._datasets)}")
            self._datasets = [dataset for dataset in self._datasets if dataset.id not in selected_dataset_ids]
            self._discard_dataset_caches(selected_dataset_ids)
            append_log(f"analysis.delete.by_ids.after_filter datasets={len(self._datasets)}")
            self._custom_series_labels = {
                key: value
                for key, value in self._custom_series_labels.items()
                if key[0] not in selected_dataset_ids
            }
            self._custom_series_scales = {
                key: value
                for key, value in self._custom_series_scales.items()
                if key[0] not in selected_dataset_ids
            }
            self._original_series_scales = {
                key: value
                for key, value in self._original_series_scales.items()
                if key[0] not in selected_dataset_ids
            }
            if self._current_measurement_dataset_id in selected_dataset_ids:
                self._current_measurement_dataset_id = None
            append_log("analysis.delete.by_ids.before_refresh_lists")
            self._refresh_dataset_series_list_only()
            append_log("analysis.delete.by_ids.after_refresh_lists")
            self._notify_data_store_changed_later("delete", self)
            append_log("analysis.delete.by_ids.after_notify_schedule")
        finally:
            self._suspend_auto_plot = previous_suspend
            append_log("analysis.delete.by_ids.finally_restore_suspend")
        self.statusBar().showMessage(f"已删除 {len(selected_dataset_ids)} 个数据；图像将在下次绘图时刷新")

    def _discard_dataset_caches(self, dataset_ids: set[int] | None = None) -> None:
        if dataset_ids is None:
            self._time_series_cache.clear()
            self._bulk_time_series_cache.clear()
            self._selected_channel_keys_by_dataset.clear()
        else:
            ids = {int(dataset_id) for dataset_id in dataset_ids}
            self._time_series_cache = {
                key: value for key, value in self._time_series_cache.items() if key[0] not in ids
            }
            self._bulk_time_series_cache = {
                key: value for key, value in self._bulk_time_series_cache.items() if key[0] not in ids
            }
            self._selected_channel_keys_by_dataset = {
                key: value for key, value in self._selected_channel_keys_by_dataset.items() if key not in ids
            }
        self._derived_result_cache.clear()
        self._last_derived_results = None
        self._last_time_pair_transfer_description = None

    def _refresh_dataset_series_list_only(self) -> None:
        selected_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        self._sync_original_and_custom_series_scales()
        self.series_list.blockSignals(True)
        self.series_list.clear()
        self._series_labels = self._build_series_labels()
        for dataset in self._datasets:
            for series in dataset.series:
                label = self._series_list_label(dataset, series)
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, series.id)
                item.setToolTip(self._dataset_readme_tooltip(dataset))
                self.series_list.addItem(item)
                item.setSelected(series.id in selected_ids if selected_ids else False)
        self.series_list.blockSignals(False)
        self._sync_series_editors_from_selection()
        self._update_readme_button_state()

    def _refresh_config_dataset_list(self) -> None:
        if not hasattr(self, "derived_config_dataset_list"):
            return
        selected_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.derived_config_dataset_list.selectedItems()
            if isinstance(item.data(QtCore.Qt.UserRole), int)
        }
        self.derived_config_dataset_list.blockSignals(True)
        self.derived_config_dataset_list.clear()
        if not self._datasets:
            item = QtWidgets.QListWidgetItem("(暂无已加载数据)")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
            self.derived_config_dataset_list.addItem(item)
        else:
            for dataset in self._datasets:
                label = f"{_series_display_file_name(dataset.name)} [id:{dataset.id}] - {len(dataset.series)} 通道"
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, dataset.id)
                item.setToolTip(str(dataset.path))
                self.derived_config_dataset_list.addItem(item)
                item.setSelected(dataset.id in selected_ids)
        self.derived_config_dataset_list.blockSignals(False)

    def _refresh_dataset_lists(self) -> None:
        selected_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        self._sync_original_and_custom_series_scales()
        self.series_list.blockSignals(True)
        self.series_list.clear()
        self._series_labels = self._build_series_labels()
        for dataset in self._datasets:
            for series in dataset.series:
                label = self._series_list_label(dataset, series)
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, series.id)
                item.setToolTip(self._dataset_readme_tooltip(dataset))
                self.series_list.addItem(item)
                item.setSelected(series.id in selected_ids if selected_ids else False)
        self.series_list.blockSignals(False)
        self._refresh_config_dataset_list()
        self._refresh_foundation_file_selectors()
        self._refresh_derived_selectors()
        self._sync_series_editors_from_selection()
        self._update_readme_button_state()

    def _sync_original_and_custom_series_scales(self) -> None:
        live_keys: set[tuple[int, int]] = set()
        for dataset in self._datasets:
            for series in dataset.series:
                key = (dataset.id, series.channel_index + 1)
                live_keys.add(key)
                if key not in self._original_series_scales:
                    self._original_series_scales[key] = float(series.scale or 1.0)
                if key in self._custom_series_scales:
                    series.scale = float(self._custom_series_scales[key])
        self._original_series_scales = {
            key: value
            for key, value in self._original_series_scales.items()
            if key in live_keys
        }

    def _dataset_readme_tooltip(self, dataset: AnalysisDataset) -> str:
        number = dataset.condition_number or "--"
        condition = dataset.condition_text or "未匹配到工况"
        readme = str(dataset.readme_path) if dataset.readme_path is not None else "未找到 readme.txt"
        return f"{dataset.name}\n编号: {number}\n工况: {condition}\nreadme: {readme}"

    def _series_list_label(self, dataset: AnalysisDataset, series: AnalysisSeries) -> str:
        label = self._series_label(dataset, series)
        condition = _inline_condition_text(dataset.condition_text)
        if condition:
            return f"{label}（{condition}）"
        return label

    def _dataset_for_readme_panel(self) -> AnalysisDataset | None:
        selected = self._selected_series()
        if selected:
            return selected[0][0]
        if self._datasets:
            return self._datasets[-1]
        return None

    def _readme_condition_status(self, dataset: AnalysisDataset) -> str:
        if dataset.condition_text:
            return dataset.condition_text
        if dataset.readme_path is None:
            return "未找到 readme.txt"
        return "readme.txt 中未匹配到当前编号"

    def _readme_dialog_summary(self, dataset: AnalysisDataset) -> str:
        number = dataset.condition_number or "--"
        readme_path = str(dataset.readme_path) if dataset.readme_path is not None else "未找到"
        return (
            f"文件: {dataset.name}\n"
            f"编号: {number}\n"
            f"工况: {self._readme_condition_status(dataset)}\n"
            f"readme: {readme_path}"
        )

    def _readme_dialog_text(self, dataset: AnalysisDataset) -> str:
        if dataset.readme_text:
            return dataset.readme_text
        if dataset.notes_fallback:
            return dataset.notes_fallback
        return "未找到 readme.txt，且该数据没有可用的 VNA notes。"

    def _update_readme_button_state(self) -> None:
        if not hasattr(self, "show_readme_button"):
            return
        dataset = self._dataset_for_readme_panel()
        if dataset is None:
            self.show_readme_button.setEnabled(False)
            self.show_readme_button.setToolTip("未加载数据")
            self.show_readme_button.blockSignals(True)
            self.show_readme_button.setChecked(False)
            self.show_readme_button.setText("查看 readme")
            self.show_readme_button.blockSignals(False)
            self._refresh_readme_panel(None)
            return
        has_preview = bool(dataset.readme_text or dataset.condition_text or dataset.notes_fallback)
        self.show_readme_button.setEnabled(has_preview)
        self.show_readme_button.setToolTip(self._readme_dialog_summary(dataset))
        if not has_preview and self.show_readme_button.isChecked():
            self.show_readme_button.blockSignals(True)
            self.show_readme_button.setChecked(False)
            self.show_readme_button.setText("查看 readme")
            self.show_readme_button.blockSignals(False)
            if hasattr(self, "readme_panel"):
                self.readme_panel.setVisible(False)
        self._refresh_readme_panel(dataset)

    def _toggle_readme_panel(self, visible: bool) -> None:
        if not hasattr(self, "readme_panel"):
            return
        if visible and not self.show_readme_button.isEnabled():
            self.show_readme_button.blockSignals(True)
            self.show_readme_button.setChecked(False)
            self.show_readme_button.blockSignals(False)
            self.readme_panel.setVisible(False)
            return
        if visible and self.readme_panel.isHidden():
            self._readme_panel_restore_size = self.size()
            self._readme_panel_restore_minimum_size = self.minimumSize()
        self.show_readme_button.setText("收起 readme" if visible else "查看 readme")
        self.readme_panel.setVisible(bool(visible))
        self._refresh_readme_panel(self._dataset_for_readme_panel())
        if not visible:
            restore_size = self._readme_panel_restore_size
            restore_minimum_size = self._readme_panel_restore_minimum_size
            self._readme_panel_restore_size = None
            self._readme_panel_restore_minimum_size = None
            if restore_size is not None:
                QtCore.QTimer.singleShot(
                    0,
                    lambda size=restore_size, minimum_size=restore_minimum_size: self._restore_readme_panel_size(
                        size, minimum_size
                    ),
                )

    def _restore_readme_panel_size(
        self, size: QtCore.QSize, minimum_size: QtCore.QSize | None = None
    ) -> None:
        if self.readme_panel.isHidden():
            if minimum_size is not None:
                self.setMinimumSize(minimum_size)
            self.resize(size)

    def _refresh_readme_panel(self, dataset: AnalysisDataset | None) -> None:
        if not hasattr(self, "readme_summary_label") or not hasattr(self, "readme_panel_preview"):
            return
        if dataset is None:
            self.readme_summary_label.setText("未加载数据")
            self.readme_panel_preview.setPlainText("")
            return
        self.readme_summary_label.setText(self._readme_dialog_summary(dataset))
        self.readme_panel_preview.setPlainText(self._readme_dialog_text(dataset))

    def _refresh_foundation_file_selectors(self) -> None:
        if not hasattr(self, "foundation_vib_file_combo"):
            return
        previous_vib_id = self.foundation_vib_file_combo.currentData()
        previous_stiff_id = self.foundation_stiff_file_combo.currentData()
        self.foundation_vib_file_combo.blockSignals(True)
        self.foundation_stiff_file_combo.blockSignals(True)
        try:
            for combo in (self.foundation_vib_file_combo, self.foundation_stiff_file_combo):
                combo.clear()
                if not self._datasets:
                    combo.addItem("(none)", None)
                    combo.setItemData(0, "(none)", QtCore.Qt.ToolTipRole)
                    combo.setEnabled(False)
                    continue
                combo.setEnabled(True)
                for dataset in self._datasets:
                    label = f"{_series_display_file_name(dataset.name)} [id:{dataset.id}]"
                    combo.addItem(label, dataset.id)
                    combo.setItemData(combo.count() - 1, label, QtCore.Qt.ToolTipRole)

            vib_index = self._combo_index_for_data(self.foundation_vib_file_combo, previous_vib_id)
            stiff_index = self._combo_index_for_data(self.foundation_stiff_file_combo, previous_stiff_id)
            if vib_index < 0:
                vib_index = 0 if self.foundation_vib_file_combo.count() else -1
            if stiff_index < 0:
                stiff_index = max(0, self.foundation_stiff_file_combo.count() - 1)
            if vib_index >= 0:
                self.foundation_vib_file_combo.setCurrentIndex(vib_index)
            if stiff_index >= 0:
                self.foundation_stiff_file_combo.setCurrentIndex(stiff_index)
            self._update_foundation_file_combo_tooltip(self.foundation_vib_file_combo)
            self._update_foundation_file_combo_tooltip(self.foundation_stiff_file_combo)
        finally:
            self.foundation_vib_file_combo.blockSignals(False)
            self.foundation_stiff_file_combo.blockSignals(False)

    def _on_foundation_file_combo_changed(self, combo: QtWidgets.QComboBox) -> None:
        self._update_foundation_file_combo_tooltip(combo)
        self._auto_plot_from_control_change()

    @staticmethod
    def _update_foundation_file_combo_tooltip(combo: QtWidgets.QComboBox) -> None:
        combo.setToolTip(combo.currentText())

    def _refresh_derived_selectors(self) -> None:
        if not hasattr(self, "derived_transfer_combo"):
            return
        previous_transfer = self.derived_transfer_combo.currentData()
        previous_input_id = self.derived_input_series_combo.currentData()
        previous_batch_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in getattr(self, "derived_batch_target_list", QtWidgets.QListWidget()).selectedItems()
        }
        previous_stitch_id = (
            self.derived_stitch_series_combo.currentData()
            if hasattr(self, "derived_stitch_series_combo")
            else None
        )
        self.derived_transfer_combo.blockSignals(True)
        self.derived_input_series_combo.blockSignals(True)
        if hasattr(self, "derived_stitch_series_combo"):
            self.derived_stitch_series_combo.blockSignals(True)
        try:
            self.derived_transfer_combo.clear()
            transfer_options = self._derived_transfer_options()
            if not transfer_options:
                self.derived_transfer_combo.addItem("(no transfer)", None)
                self.derived_transfer_combo.setEnabled(False)
            else:
                self.derived_transfer_combo.setEnabled(True)
                for label, data in transfer_options:
                    self.derived_transfer_combo.addItem(label, data)
                    self.derived_transfer_combo.setItemData(
                        self.derived_transfer_combo.count() - 1,
                        label,
                        QtCore.Qt.ToolTipRole,
                    )
            transfer_index = self._combo_index_for_data(self.derived_transfer_combo, previous_transfer)
            if previous_transfer == ("manual_transfer",) and self.derived_transfer_combo.count() > 1:
                transfer_index = 0
            if transfer_index < 0:
                transfer_index = 0 if self.derived_transfer_combo.count() else -1
            if transfer_index >= 0:
                self.derived_transfer_combo.setCurrentIndex(transfer_index)
            self._update_foundation_file_combo_tooltip(self.derived_transfer_combo)

            self.derived_input_series_combo.clear()
            for dataset in self._datasets:
                for series in dataset.series:
                    label = self._series_label(dataset, series)
                    self.derived_input_series_combo.addItem(label, series.id)
                    self.derived_input_series_combo.setItemData(
                        self.derived_input_series_combo.count() - 1,
                        label,
                        QtCore.Qt.ToolTipRole,
                    )
            for name in VC_REFERENCE_NAMES:
                label = f"VC参考线 | {name}"
                data = ("vc_reference", name)
                self.derived_input_series_combo.addItem(label, data)
                self.derived_input_series_combo.setItemData(
                    self.derived_input_series_combo.count() - 1,
                    label,
                    QtCore.Qt.ToolTipRole,
                )
            input_index = self._combo_index_for_data(self.derived_input_series_combo, previous_input_id)
            if input_index < 0:
                input_index = 0 if self.derived_input_series_combo.count() else -1
            if input_index >= 0:
                self.derived_input_series_combo.setCurrentIndex(input_index)
            self.derived_input_series_combo.setEnabled(self.derived_input_series_combo.count() > 0)
            self._update_foundation_file_combo_tooltip(self.derived_input_series_combo)

            if hasattr(self, "derived_batch_target_list"):
                batch_list = self.derived_batch_target_list
                batch_list.blockSignals(True)
                batch_list.clear()
                for index in range(self.derived_input_series_combo.count()):
                    item = QtWidgets.QListWidgetItem(self.derived_input_series_combo.itemText(index))
                    data = self.derived_input_series_combo.itemData(index)
                    item.setData(QtCore.Qt.UserRole, data)
                    item.setToolTip(self.derived_input_series_combo.itemText(index))
                    batch_list.addItem(item)
                    if data in previous_batch_ids or (not previous_batch_ids and data == self.derived_input_series_combo.currentData()):
                        item.setSelected(True)
                batch_list.blockSignals(False)

            if hasattr(self, "derived_stitch_series_combo"):
                self.derived_stitch_series_combo.clear()
                self.derived_stitch_series_combo.addItem("(no stitch source)", None)
                for dataset in self._datasets:
                    for series in dataset.series:
                        label = self._series_label(dataset, series)
                        self.derived_stitch_series_combo.addItem(label, series.id)
                        self.derived_stitch_series_combo.setItemData(
                            self.derived_stitch_series_combo.count() - 1,
                            label,
                            QtCore.Qt.ToolTipRole,
                        )
                stitch_index = self._combo_index_for_data(self.derived_stitch_series_combo, previous_stitch_id)
                if stitch_index < 0:
                    stitch_index = 0
                self.derived_stitch_series_combo.setCurrentIndex(stitch_index)
                self.derived_stitch_series_combo.setEnabled(self.derived_stitch_series_combo.count() > 1)
                self._update_foundation_file_combo_tooltip(self.derived_stitch_series_combo)
        finally:
            self.derived_transfer_combo.blockSignals(False)
            self.derived_input_series_combo.blockSignals(False)
            if hasattr(self, "derived_stitch_series_combo"):
                self.derived_stitch_series_combo.blockSignals(False)
        self._curve_point_edit_mode = "transfer"
        self._active_psd_edit_label = None
        self._sync_transfer_point_table()
        self._sync_slot_labels()
        self._update_processing_task_summary()

    def _on_batch_target_selection_changed(self) -> None:
        if not hasattr(self, "derived_batch_target_list"):
            return
        selected = self.derived_batch_target_list.selectedItems()
        if selected:
            first_data = selected[0].data(QtCore.Qt.UserRole)
            index = self._combo_index_for_data(self.derived_input_series_combo, first_data)
            if index >= 0:
                self.derived_input_series_combo.blockSignals(True)
                self.derived_input_series_combo.setCurrentIndex(index)
                self.derived_input_series_combo.blockSignals(False)
        self._sync_slot_labels()
        self._mark_derived_results_stale("待换算目标已变化")

    def _derived_transfer_options(self) -> list[tuple[str, tuple[object, ...]]]:
        options: list[tuple[str, tuple[object, ...]]] = []
        for dataset in self._datasets:
            display_name = _series_display_file_name(dataset.name)
            if dataset.frf:
                for key in sorted(dataset.frf):
                    if "->" not in key:
                        continue
                    base_key, top_key = key.split("->", 1)
                    base_series = self._series_for_transfer_endpoint(dataset, base_key)
                    top_series = self._series_for_transfer_endpoint(dataset, top_key)
                    base_label = f"Ch {base_series.channel_index + 1}" if base_series is not None else base_key
                    top_label = f"Ch {top_series.channel_index + 1}" if top_series is not None else top_key
                    label = f"{display_name} | {base_label}->{top_label} ({key})"
                    options.append((label, (dataset.id, key, base_key, top_key, "stored")))
                continue
            has_time_data = bool(dataset.channels) or dataset.is_continuous
            if has_time_data and len(dataset.series) >= 2:
                base_series = dataset.series[0]
                for top_series in dataset.series[1:]:
                    key = f"{base_series.channel_key}->{top_series.channel_key}"
                    label = f"{display_name} | Ch {base_series.channel_index + 1}->Ch {top_series.channel_index + 1} (time)"
                    options.append(
                        (
                            label,
                            (
                                dataset.id,
                                key,
                                base_series.channel_key,
                                top_series.channel_key,
                                "time",
                            ),
                        )
                    )
        options.extend(self._derived_psd_ratio_transfer_options())
        options.append(("手工传递率 | 控制点(dB)", ("manual_transfer",)))
        return options

    def _derived_psd_ratio_transfer_options(self) -> list[tuple[str, tuple[object, ...]]]:
        psd_series: list[tuple[AnalysisDataset, AnalysisSeries]] = []
        for dataset in self._datasets:
            if dataset.frequency_hz is None or not dataset.autospectrum:
                continue
            for series in dataset.series:
                if series.channel_key in dataset.autospectrum:
                    psd_series.append((dataset, series))
        if len(psd_series) < 2 or len(psd_series) > 24:
            return []

        options: list[tuple[str, tuple[object, ...]]] = []
        seen: set[tuple[int, str, int, str]] = set()
        for base_dataset, base_series in psd_series:
            for top_dataset, top_series in psd_series:
                if base_dataset.id == top_dataset.id and base_series.channel_key == top_series.channel_key:
                    continue
                key = (base_dataset.id, base_series.channel_key, top_dataset.id, top_series.channel_key)
                if key in seen:
                    continue
                seen.add(key)
                base_label = self._series_label(base_dataset, base_series)
                top_label = self._series_label(top_dataset, top_series)
                label = f"PSD比值 | {base_label}->{top_label}"
                transfer_key = (
                    f"{base_dataset.id}:{base_series.channel_key}"
                    f"->{top_dataset.id}:{top_series.channel_key}"
                )
                options.append(
                    (
                        label,
                        (
                            "psd_pair",
                            transfer_key,
                            base_dataset.id,
                            base_series.channel_key,
                            top_dataset.id,
                            top_series.channel_key,
                        ),
                    )
                )
        return options

    def _dataset_by_id(self, dataset_id: int | None) -> AnalysisDataset | None:
        if dataset_id is None:
            return None
        for dataset in self._datasets:
            if dataset.id == dataset_id:
                return dataset
        return None

    @staticmethod
    def _combo_index_for_data(combo: QtWidgets.QComboBox, value: object) -> int:
        if value is None:
            return -1
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                return index
        return -1

    @staticmethod
    def _transfer_edit_key_from_data(data: object) -> tuple[object, ...] | None:
        if isinstance(data, tuple):
            return tuple(data)
        return None

    def _current_transfer_edit_key(self) -> tuple[object, ...] | None:
        if not hasattr(self, "derived_transfer_combo"):
            return None
        return self._transfer_edit_key_from_data(self.derived_transfer_combo.currentData())

    def _current_transfer_control_points(self) -> tuple[np.ndarray, np.ndarray]:
        key = self._current_transfer_edit_key()
        if key == ("manual_transfer",):
            return self._manual_transfer_points
        if key in self._transfer_edit_points:
            return self._transfer_edit_points[key]
        return np.array([], dtype=float), np.array([], dtype=float)

    def _current_psd_edit_label(self) -> str | None:
        if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
            return None
        plot = self.derived_plots[1]
        label = self._active_psd_edit_label
        curves = self._plot_curves.get(plot, {})
        if label in curves or label in self._psd_edit_points:
            return label
        label = self._active_trace.get(plot)
        if label in curves or label in self._psd_edit_points:
            return label
        return next(iter(curves), None)

    def _current_psd_control_points(self) -> tuple[np.ndarray, np.ndarray]:
        label = self._current_psd_edit_label()
        if label in self._psd_edit_points:
            return self._psd_edit_points[label]
        return np.array([], dtype=float), np.array([], dtype=float)

    def _current_curve_control_points(self) -> tuple[np.ndarray, np.ndarray]:
        if self._curve_point_edit_mode == "psd":
            return self._current_psd_control_points()
        return self._current_transfer_control_points()

    def _curve_edit_identity(self) -> tuple[str, object]:
        if self._curve_point_edit_mode == "psd":
            return "psd", self._current_psd_edit_label()
        return "transfer", self._current_transfer_edit_key()

    def _push_curve_edit_history(self) -> None:
        if self._restoring_curve_edit:
            return
        mode, key = self._curve_edit_identity()
        frequency, values = self._current_curve_control_points()
        self._curve_edit_undo.append((mode, key, frequency.copy(), values.copy()))
        self._curve_edit_undo = self._curve_edit_undo[-100:]
        self._curve_edit_redo.clear()

    def _restore_curve_edit_snapshot(self, snapshot: tuple[str, object, np.ndarray, np.ndarray]) -> None:
        mode, key, frequency, values = snapshot
        self._restoring_curve_edit = True
        try:
            self._curve_point_edit_mode = mode
            if mode == "psd":
                self._active_psd_edit_label = str(key) if key is not None else None
                if key is not None and frequency.size >= 2:
                    self._psd_edit_points[str(key)] = (frequency.copy(), values.copy())
                elif key is not None:
                    self._psd_edit_points.pop(str(key), None)
            else:
                transfer_key = key if isinstance(key, tuple) else None
                if transfer_key == ("manual_transfer",) and frequency.size >= 2:
                    self._manual_transfer_points = (frequency.copy(), values.copy())
                elif transfer_key is not None and frequency.size >= 2:
                    self._transfer_edit_points[transfer_key] = (frequency.copy(), values.copy())
                elif transfer_key is not None:
                    self._transfer_edit_points.pop(transfer_key, None)
            self._derived_result_cache.clear()
            self._sync_transfer_point_table()
            self._mark_derived_results_stale("曲线编辑已变化")
        finally:
            self._restoring_curve_edit = False

    def _undo_curve_edit(self) -> None:
        if not self._curve_edit_undo:
            self.statusBar().showMessage("没有可撤销的曲线编辑")
            return
        mode, key = self._curve_edit_identity()
        frequency, values = self._current_curve_control_points()
        self._curve_edit_redo.append((mode, key, frequency.copy(), values.copy()))
        self._restore_curve_edit_snapshot(self._curve_edit_undo.pop())

    def _redo_curve_edit(self) -> None:
        if not self._curve_edit_redo:
            self.statusBar().showMessage("没有可重做的曲线编辑")
            return
        mode, key = self._curve_edit_identity()
        frequency, values = self._current_curve_control_points()
        self._curve_edit_undo.append((mode, key, frequency.copy(), values.copy()))
        self._restore_curve_edit_snapshot(self._curve_edit_redo.pop())

    def _copy_selected_curve_points(self) -> None:
        table = self.derived_transfer_point_table
        rows = sorted({index.row() for index in table.selectedIndexes()})
        if not rows:
            rows = list(range(table.rowCount()))
        lines = ["frequency_hz,value_db"]
        for row in rows:
            frequency = table.item(row, 0)
            value = table.item(row, 1)
            if frequency is not None and value is not None:
                lines.append(f"{frequency.text()},{value.text()}")
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage(f"已复制 {max(0, len(lines) - 1)} 个控制点")

    @staticmethod
    def _curve_points_from_text(text: str) -> tuple[np.ndarray, np.ndarray]:
        frequencies: list[float] = []
        values: list[float] = []
        for line in str(text).replace("\t", ",").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                frequency, value = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            frequencies.append(frequency)
            values.append(value)
        return np.asarray(frequencies, dtype=float), np.asarray(values, dtype=float)

    def _paste_curve_points(self) -> None:
        frequency, values = self._curve_points_from_text(QtWidgets.QApplication.clipboard().text())
        if not self._set_current_curve_control_points(frequency, values):
            return
        self.statusBar().showMessage(f"已粘贴 {frequency.size} 个控制点")

    def _import_curve_points(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "导入曲线控制点", str(self._last_directory), "CSV Files (*.csv *.txt)")
        if not path:
            return
        try:
            frequency, values = self._curve_points_from_text(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "导入失败", str(exc))
            return
        if self._set_current_curve_control_points(frequency, values):
            self._last_directory = Path(path).parent
            self.statusBar().showMessage(f"已导入 {frequency.size} 个控制点")

    def _export_curve_points(self) -> None:
        frequency, values = self._current_curve_control_points()
        if frequency.size < 2:
            self.statusBar().showMessage("当前没有可导出的控制点")
            return
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self, "导出曲线控制点", str(self._last_directory / "curve_points.csv"), "CSV Files (*.csv)")
        if not path:
            return
        destination = Path(path)
        with destination.open("w", encoding="utf-8-sig", newline="\n") as handle:
            handle.write("frequency_hz,value_db\n")
            for frequency_hz, value_db in zip(frequency, values):
                handle.write(f"{frequency_hz:.17g},{value_db:.17g}\n")
        self._last_directory = destination.parent
        self.statusBar().showMessage(f"已导出控制点：{destination.name}")

    @staticmethod
    def _edit_control_frequencies(frequency_hz: np.ndarray) -> np.ndarray:
        f = np.asarray(frequency_hz, dtype=float).ravel()
        f = f[np.isfinite(f) & (f > 0.0)]
        if f.size < 2:
            return np.array([], dtype=float)
        min_f = float(np.min(f))
        max_f = float(np.max(f))
        centers, lower_edges, upper_edges = third_octave_bands(min_f, max_f)
        band_points = np.concatenate((lower_edges, centers, upper_edges))
        band_points = band_points[np.isfinite(band_points) & (band_points >= min_f) & (band_points <= max_f)]
        if band_points.size:
            return np.unique(np.concatenate(([min_f], band_points, [max_f])))
        return log_frequency_grid(float(np.min(f)), float(np.max(f)), points=min(max(2, f.size), 8))

    @staticmethod
    def _edit_control_point_limit(frequency_hz: np.ndarray, target_frequency_hz: np.ndarray) -> int:
        f = np.asarray(frequency_hz, dtype=float).ravel()
        f = f[np.isfinite(f) & (f > 0.0)]
        if f.size < 2:
            return 2
        target_count = int(np.asarray(target_frequency_hz, dtype=float).size)
        return min(int(f.size), max(48, min(180, target_count * 3)))

    def _set_current_transfer_control_points(
        self,
        frequency_hz: np.ndarray,
        magnitude_db: np.ndarray,
        *,
        replot: bool = True,
    ) -> bool:
        f, db, issues = validate_control_points(frequency_hz, magnitude_db)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            self.statusBar().showMessage(errors[0].message)
            return False
        key = self._current_transfer_edit_key()
        self._push_curve_edit_history()
        if key == ("manual_transfer",):
            self._manual_transfer_points = (f, db)
        elif key is not None:
            self._transfer_edit_points[key] = (f, db)
        else:
            return False
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        if replot:
            self._auto_plot_derived_from_control_change()
        return True

    def _set_current_psd_control_points(
        self,
        frequency_hz: np.ndarray,
        power_db: np.ndarray,
        *,
        replot: bool = True,
    ) -> bool:
        label = self._current_psd_edit_label()
        if not label:
            self.statusBar().showMessage("当前换算图窗没有可编辑PSD曲线")
            return False
        f, db, issues = validate_control_points(frequency_hz, power_db)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            self.statusBar().showMessage(errors[0].message)
            return False
        self._push_curve_edit_history()
        self._active_psd_edit_label = label
        self._psd_edit_points[label] = (f, db)
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        if replot:
            self._auto_plot_derived_from_control_change()
        return True

    def _set_current_curve_control_points(
        self,
        frequency_hz: np.ndarray,
        value_db: np.ndarray,
        *,
        replot: bool = True,
    ) -> bool:
        if self._curve_point_edit_mode == "psd":
            return self._set_current_psd_control_points(frequency_hz, value_db, replot=replot)
        return self._set_current_transfer_control_points(frequency_hz, value_db, replot=replot)

    def _sync_transfer_point_table(self) -> None:
        if not hasattr(self, "derived_transfer_point_table"):
            return
        self._updating_transfer_point_table = True
        try:
            table = self.derived_transfer_point_table
            f, db = self._current_curve_control_points()
            table.setRowCount(0)
            for row, (freq, value_db) in enumerate(zip(f, db)):
                table.insertRow(row)
                table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{freq:.12g}"))
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{value_db:.12g}"))
            key = self._current_transfer_edit_key()
            has_points = f.size >= 2
            if hasattr(self, "derived_curve_point_label"):
                self.derived_curve_point_label.setText(
                    "PSD修正点" if self._curve_point_edit_mode == "psd" else "传递率点"
                )
            table.setEnabled((key is not None) if self._curve_point_edit_mode != "psd" else self._current_psd_edit_label() is not None)
            self.derived_transfer_delete_point_button.setEnabled(has_points and f.size > 2)
            self.derived_transfer_reset_button.setEnabled(
                key == ("manual_transfer",) or key in self._transfer_edit_points
            )
            self.derived_psd_reset_button.setEnabled(
                self._current_psd_edit_label() in self._psd_edit_points
                if hasattr(self, "derived_psd_reset_button")
                else False
            )
        finally:
            self._updating_transfer_point_table = False

    def _transfer_points_from_table(self) -> tuple[np.ndarray, np.ndarray]:
        table = self.derived_transfer_point_table
        frequencies: list[float] = []
        values: list[float] = []
        for row in range(table.rowCount()):
            freq_item = table.item(row, 0)
            db_item = table.item(row, 1)
            if freq_item is None or db_item is None:
                continue
            try:
                freq = float(freq_item.text())
                db = float(db_item.text())
            except ValueError:
                continue
            if np.isfinite(freq) and np.isfinite(db) and freq > 0.0:
                frequencies.append(freq)
                values.append(db)
        return np.asarray(frequencies, dtype=float), np.asarray(values, dtype=float)

    def _transfer_point_table_changed(self) -> None:
        if self._updating_transfer_point_table:
            return
        f, db = self._transfer_points_from_table()
        self._set_current_curve_control_points(f, db)

    def _add_transfer_control_point(self) -> None:
        f, db = self._current_curve_control_points()
        if f.size < 2:
            if self._curve_point_edit_mode == "psd":
                self._initialize_psd_edit_points_from_active_curve()
            else:
                self._initialize_transfer_edit_points_from_current()
            f, db = self._current_curve_control_points()
        if f.size < 2:
            f = np.array([10.0, 100.0], dtype=float)
            db = np.array([0.0, 0.0], dtype=float)
        else:
            new_f = float(np.sqrt(f[-2] * f[-1])) if f.size >= 2 else float(f[-1] * 2.0)
            new_db = float(np.interp(np.log10(new_f), np.log10(f), db))
            f = np.append(f, new_f)
            db = np.append(db, new_db)
        self._set_current_curve_control_points(f, db)

    def _delete_selected_transfer_control_point(self) -> None:
        table = self.derived_transfer_point_table
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        f, db = self._current_curve_control_points()
        keep = np.ones(f.shape, dtype=bool)
        for row in rows:
            if 0 <= row < keep.size:
                keep[row] = False
        if np.count_nonzero(keep) < 2:
            self.statusBar().showMessage("PSD控制点至少保留 2 个" if self._curve_point_edit_mode == "psd" else "传递率控制点至少保留 2 个")
            return
        self._set_current_curve_control_points(f[keep], db[keep])

    def _clear_current_transfer_edit_points(self) -> None:
        key = self._current_transfer_edit_key()
        self._push_curve_edit_history()
        if key == ("manual_transfer",):
            self._manual_transfer_points = (
                np.array([10.0, 100.0], dtype=float),
                np.array([0.0, 0.0], dtype=float),
            )
        elif key in self._transfer_edit_points:
            self._transfer_edit_points.pop(key, None)
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        self._auto_plot_derived_from_control_change()

    def _initialize_transfer_edit_points_from_current(self) -> None:
        self._curve_point_edit_mode = "transfer"
        self._active_psd_edit_label = None
        selected_transfer = self._selected_derived_transfer()
        if selected_transfer is None:
            self.statusBar().showMessage("没有可编辑的传递率曲线")
            return
        transfer_dataset, transfer_key, source_kind, base_series, top_series, _label = selected_transfer
        if source_kind == "manual":
            self._sync_transfer_point_table()
            return
        transfer = self._transfer_for_derived(
            transfer_dataset,
            transfer_key,
            source_kind,
            base_series,
            top_series,
            transfer_factor=self._derived_transfer_factor(),
            edit_key=None,
        )
        if transfer is None:
            self.statusBar().showMessage("无法从当前传递率生成控制点")
            return
        f, h, _phase_available = transfer
        target_f = self._edit_control_frequencies(f)
        control_f, control_db = sample_curve_as_db_points(
            f,
            np.abs(h),
            count=8,
            power_values=False,
            target_frequency_hz=target_f,
            max_count=self._edit_control_point_limit(f, target_f),
            error_threshold_db=2.0,
        )
        key = self._current_transfer_edit_key()
        if key is None or control_f.size < 2:
            self.statusBar().showMessage("无法从当前传递率生成控制点")
            return
        self._transfer_edit_points[key] = (control_f, control_db)
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        self._auto_plot_derived_from_control_change()

    def _drag_transfer_control_point_to_scene_pos(self, plot: pg.PlotWidget, point_index: int, scene_pos) -> bool:
        f, db = self._current_transfer_control_points()
        if point_index < 0 or point_index >= f.size:
            return False
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        freq = self._from_plot_x(plot, float(mouse_point.x()))
        value_db = self._from_plot_y(plot, float(mouse_point.y()))
        if not np.isfinite(freq) or not np.isfinite(value_db) or freq <= 0.0:
            return False
        f = f.copy()
        db = db.copy()
        f[point_index] = freq
        db[point_index] = value_db
        self._restoring_curve_edit = True
        try:
            changed = self._set_current_transfer_control_points(f, db, replot=False)
        finally:
            self._restoring_curve_edit = False
        if not changed:
            return False
        points = self._curve_edit_items.get(plot, [])
        if point_index < len(points):
            points[point_index].setData([self._to_plot_x(plot, freq)], [self._to_plot_y(plot, value_db)])
        selected = self._selected_derived_transfer()
        if selected is not None:
            dataset, transfer_key, source_kind, base_series, top_series, _label = selected
            transfer = self._transfer_for_derived(
                dataset,
                transfer_key,
                source_kind,
                base_series,
                top_series,
                transfer_factor=self._derived_transfer_factor(),
                edit_key=self._current_transfer_edit_key(),
            )
            if transfer is not None:
                preview_f, preview_h, _phase = transfer
                label = self._active_trace.get(plot)
                item = self._plot_item_for_label(plot, label) if label else None
                preview_db = 20.0 * np.log10(np.maximum(np.abs(preview_h), 1e-20))
                if item is not None:
                    item.setData(preview_f, preview_db)
                if label:
                    self._plot_curves[plot][label] = (preview_f, preview_db)
        self._mark_derived_results_stale("传递率控制点已拖动")
        return True

    def _initialize_psd_edit_points_from_active_curve(self) -> None:
        if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
            return
        plot = self.derived_plots[1]
        curves = self._plot_curves.get(plot, {})
        if not curves:
            self.statusBar().showMessage("当前换算图窗没有可编辑PSD曲线")
            return
        label = self._active_trace.get(plot) or next(iter(curves))
        if label not in curves:
            label = next(iter(curves))
        x, y = curves[label]
        self._curve_point_edit_mode = "psd"
        self._active_psd_edit_label = label
        target_f = self._edit_control_frequencies(x)
        control_f, control_db = sample_curve_as_db_points(
            x,
            y,
            count=8,
            power_values=True,
            target_frequency_hz=target_f,
            max_count=self._edit_control_point_limit(x, target_f),
            error_threshold_db=2.0,
        )
        if control_f.size < 2:
            self.statusBar().showMessage("无法从当前PSD生成控制点")
            return
        self._psd_edit_points[label] = (control_f, control_db)
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        self._auto_plot_derived_from_control_change()

    def _clear_active_psd_edit_points(self) -> None:
        if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
            return
        plot = self.derived_plots[1]
        self._curve_point_edit_mode = "psd"
        label = self._current_psd_edit_label() or self._active_trace.get(plot)
        self._push_curve_edit_history()
        if label in self._psd_edit_points:
            self._psd_edit_points.pop(label, None)
        elif self._psd_edit_points:
            self._psd_edit_points.clear()
        self._derived_result_cache.clear()
        self._sync_transfer_point_table()
        self._auto_plot_derived_from_control_change()

    def _drag_psd_control_point_to_scene_pos(
        self,
        plot: pg.PlotWidget,
        label: str,
        point_index: int,
        scene_pos,
    ) -> bool:
        if label not in self._psd_edit_points:
            return False
        f, db = self._psd_edit_points[label]
        if point_index < 0 or point_index >= f.size:
            return False
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        freq = self._from_plot_x(plot, float(mouse_point.x()))
        value = self._from_plot_y(plot, float(mouse_point.y()))
        if not np.isfinite(freq) or not np.isfinite(value) or freq <= 0.0 or value <= 0.0:
            return False
        f = f.copy()
        db = db.copy()
        f[point_index] = freq
        db[point_index] = 10.0 * np.log10(max(value, 1e-300))
        order = np.argsort(f)
        self._psd_edit_points[label] = (f[order], db[order])
        self._derived_result_cache.clear()
        self._curve_point_edit_mode = "psd"
        self._active_psd_edit_label = label
        self._sync_transfer_point_table()
        points = self._curve_edit_items.get(plot, [])
        if point_index < len(points):
            points[point_index].setData([self._to_plot_x(plot, freq)], [self._to_plot_y(plot, value)])
        source_curve = None
        for result in self._last_derived_results or []:
            if str(result.get("label", "")) == label and result.get("psd") is not None:
                source_curve = result["psd"]
                break
        if source_curve is not None:
            preview_x, preview_y = apply_power_db_profile(source_curve[0], source_curve[1], f[order], db[order])
            item = self._plot_item_for_label(plot, label)
            if item is not None and preview_x.size >= 2:
                item.setData(preview_x, preview_y)
                self._plot_curves[plot][label] = (preview_x, preview_y)
        self._mark_derived_results_stale("PSD控制点已拖动")
        return True

    def _begin_curve_control_point_drag(self) -> None:
        self._push_curve_edit_history()

    def _finish_curve_control_point_drag(self) -> None:
        if self._derived_only:
            self._plot_derived(keep_existing=False, quiet=True)
        else:
            self._auto_plot_derived_from_control_change()

    def _selected_stitch_series(self) -> tuple[AnalysisDataset, AnalysisSeries] | None:
        if not hasattr(self, "derived_stitch_series_combo"):
            return None
        selected_id = self.derived_stitch_series_combo.currentData()
        if selected_id is None:
            return None
        for dataset in self._datasets:
            for series in dataset.series:
                if series.id == selected_id:
                    return dataset, series
        return None

    def _curve_for_stitch_mode(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        mode: str,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        if mode in {"PSD", "CumPSD"}:
            return self._curve_for_mode(dataset, series, mode)
        if mode == "地基振动":
            x, y = self._foundation_vibration_curve(dataset, series)
            if x.size < 1:
                return None
            return x, y, self._series_label(dataset, series)
        return None

    def _stitched_curve_for_mode(
        self,
        mode: str,
        primary_x: np.ndarray,
        primary_y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        if not hasattr(self, "derived_stitch_enabled_check") or not self.derived_stitch_enabled_check.isChecked():
            return None
        if str(mode) == "近似时域":
            return None
        split = _parse_optional_float(self.derived_stitch_split_edit.text())
        if split is None:
            return None
        selected = self._selected_stitch_series()
        if selected is None:
            return None
        dataset, series = selected
        stitch_source = self._curve_for_stitch_mode(dataset, series, str(mode))
        if stitch_source is None:
            return None
        secondary_x, secondary_y, secondary_label = stitch_source
        order = (
            self.derived_stitch_order_combo.currentData()
            if hasattr(self, "derived_stitch_order_combo")
            else "primary_first"
        )
        if order == "secondary_first":
            stitched_x, stitched_y = stitch_frequency_curves(
                secondary_x,
                secondary_y,
                primary_x,
                primary_y,
                float(split),
            )
            order_label = f"导入前/换算后 {secondary_label}"
        else:
            stitched_x, stitched_y = stitch_frequency_curves(
                primary_x,
                primary_y,
                secondary_x,
                secondary_y,
                float(split),
            )
            order_label = f"换算前/导入后 {secondary_label}"
        if stitched_x.size < 2:
            return None
        return stitched_x, stitched_y, f"拼合@{float(split):.6g}Hz {order_label}"

    def _select_all_series(self) -> None:
        for index in range(self.series_list.count()):
            self.series_list.item(index).setSelected(True)
        self.statusBar().showMessage(f"已选择 {self.series_list.count()} 个通道")

    def _select_no_series(self) -> None:
        self.series_list.clearSelection()
        self.statusBar().showMessage("已取消选择所有通道")

    def _hold_toggled(self, enabled: bool) -> None:
        self.hold_button.setText("保持:开" if enabled else "保持:关")
        if enabled:
            self.statusBar().showMessage("保持已开启：下一次绘图会叠加到当前图像")
        else:
            self.statusBar().showMessage("保持已关闭：下一次绘图会先清空旧曲线")

    def _on_series_selection_changed(self) -> None:
        self._sync_series_editors_from_selection()
        self._update_readme_button_state()
        self._auto_plot_from_control_change()

    def _auto_plot_from_control_change(self) -> None:
        if self._suspend_auto_plot:
            return
        if self._derived_only:
            self._mark_derived_results_stale("时域或滤波参数已变化")
            return
        if self._include_derived_tab and hasattr(self, "derived_plots") and self._has_derived_input_ready():
            self._plot_derived(keep_existing=self._hold_enabled(), quiet=True)
        if not self._datasets or not self.series_list.selectedItems():
            return
        self.plot_current()

    def _auto_plot_derived_from_control_change(self) -> None:
        if self._suspend_auto_plot:
            return
        if not hasattr(self, "derived_plots"):
            return
        if self._derived_only:
            self._mark_derived_results_stale("参数已变化")
            return
        if self._last_derived_results is not None and self.derived_result_mode_combo.currentText() in {
            "PSD",
            "CumPSD",
            "地基振动",
            "近似时域",
        }:
            self._plot_derived_result_axis(
                self.derived_plots[1],
                self.derived_result_mode_combo.currentText(),
                self._last_derived_results,
                keep_existing=self._hold_enabled(),
            )
            return
        if self._has_derived_input_ready():
            self._plot_derived(keep_existing=self._hold_enabled(), quiet=True)
            return
        if self.derived_result_mode_combo.currentText() == "近似时域" and self._plot_current_psd_curves_as_time(
            keep_existing=self._hold_enabled(),
            quiet=True,
        ):
            return
        self._plot_derived(keep_existing=self._hold_enabled(), quiet=True)

    def _on_derived_result_mode_changed(self, _text: str) -> None:
        if self._last_derived_results is None:
            if self.derived_result_mode_combo.currentText() == "近似时域" and self._plot_current_psd_curves_as_time(
                keep_existing=False,
                quiet=True,
            ):
                return
            if self._derived_only:
                self._mark_derived_results_stale("输出类型已变化")
            return
        self._plot_derived_result_axis(
            self.derived_plots[1],
            self.derived_result_mode_combo.currentText(),
            self._last_derived_results,
            keep_existing=False,
        )

    def _on_derived_input_combo_changed(self, _index: int) -> None:
        if self._derived_only and hasattr(self, "derived_batch_target_list"):
            current_data = self.derived_input_series_combo.currentData()
            self.derived_batch_target_list.blockSignals(True)
            try:
                for row in range(self.derived_batch_target_list.count()):
                    item = self.derived_batch_target_list.item(row)
                    item.setSelected(item.data(QtCore.Qt.UserRole) == current_data)
            finally:
                self.derived_batch_target_list.blockSignals(False)
        self._sync_slot_labels()
        self._mark_derived_results_stale("待换算数据已变化")

    def _mark_derived_results_stale(self, reason: str = "参数已变化") -> None:
        if not self._derived_only:
            return
        self._derived_results_stale = True
        self._derived_stale_reason = str(reason)
        if hasattr(self, "derived_issue_label"):
            self.derived_issue_label.setText(f"结果待更新：{reason}")
            self.derived_issue_label.setStyleSheet("color: #9a6700; font-weight: 600;")
        if hasattr(self, "derived_verification_label"):
            self.derived_verification_label.setText("结果校核：当前参数已变化，旧校核结果不可导出")
        for button in getattr(self, "derived_export_buttons", []):
            button.setEnabled(False)
        if hasattr(self, "derived_batch_export_button"):
            self.derived_batch_export_button.setEnabled(False)
        if hasattr(self, "derived_plot_button"):
            self.derived_plot_button.setText("计算 / 更新结果")
        self._update_processing_task_summary()

    def _set_processing_report(self, report: ValidationReport | None) -> None:
        self._last_processing_report = report
        if not hasattr(self, "derived_issue_label"):
            return
        if report is None:
            self.derived_issue_label.setText(f"结果待更新：{self._derived_stale_reason}")
            return
        messages = [issue.message for issue in report.errors[:2]]
        color = "#b42318"
        if not messages:
            messages = [issue.message for issue in report.warnings[:2]]
            color = "#9a6700"
        if not messages:
            messages = [
                f"校验通过：{report.valid_points} 个有效点，丢弃 {report.discarded_points} 个点，"
                f"实际频段 {report.effective_frequency_min_hz:g} - {report.effective_frequency_max_hz:g} Hz"
            ]
            color = "#18794e"
        self.derived_issue_label.setText("；".join(messages))
        self.derived_issue_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _set_processing_field_errors(self, issues: list[ProcessingIssue] | tuple[ProcessingIssue, ...]) -> None:
        controls = {
            "frequency_min": self.derived_freq_min_edit,
            "frequency_max": self.derived_freq_max_edit,
            "frequency_range": self.derived_freq_min_edit,
            "regularization": self.derived_regularization_spin,
            "unit": getattr(self, "derived_dimensionless_check", None),
            "transfer": self.derived_transfer_combo,
            "target": self.derived_input_series_combo,
        }
        for control in {value for value in controls.values() if value is not None}:
            control.setStyleSheet("")
        for issue in issues:
            if issue.severity != "error":
                continue
            control = controls.get(issue.field)
            if control is not None:
                control.setStyleSheet("border: 1px solid #d1242f;")

    def _update_processing_task_summary(self) -> None:
        if not self._derived_only or not hasattr(self, "derived_task_summary_label"):
            return
        transfer = self.derived_transfer_combo.currentText() or "(未选择)"
        targets = self._derived_input_series() if hasattr(self, "derived_input_series_combo") else []
        target_labels: list[str] = []
        rates: list[str] = []
        units: list[str] = []
        curve_types: list[str] = []
        frequency_ranges: list[str] = []
        for dataset, series in targets:
            if dataset is None:
                target_labels.append(str(series))
                units.append("(VC加速度PSD)")
                curve_types.append("VC参考PSD")
                vc_frequency, _vc_psd = _vc_reference_acceleration_psd(str(series))
                if vc_frequency.size:
                    frequency_ranges.append(f"{float(np.min(vc_frequency)):g}-{float(np.max(vc_frequency)):g} Hz")
            else:
                target_labels.append(self._series_label(dataset, series))
                if np.isfinite(dataset.sample_rate) and dataset.sample_rate > 0.0:
                    rates.append(f"{dataset.sample_rate:g} Hz")
                units.append(series.unit or "单位未知")
                if series.channel_key in dataset.autospectrum:
                    curve_types.append("PSD")
                elif series.channel_key in dataset.channels or dataset.is_continuous:
                    curve_types.append("时域")
                else:
                    curve_types.append("数据曲线")
                frequency = np.asarray(dataset.frequency_hz, dtype=float) if dataset.frequency_hz is not None else np.array([], dtype=float)
                frequency = frequency[np.isfinite(frequency) & (frequency > 0.0)]
                if frequency.size:
                    frequency_ranges.append(f"{float(np.min(frequency)):g}-{float(np.max(frequency)):g} Hz")
                elif np.isfinite(dataset.sample_rate) and dataset.sample_rate > 0.0:
                    frequency_ranges.append(f"0-{float(dataset.sample_rate) / 2.0:g} Hz")
        direction = self.derived_direction_combo.currentText() if hasattr(self, "derived_direction_combo") else ""
        target_text = "、".join(target_labels[:3]) or "(未选择)"
        if len(target_labels) > 3:
            target_text += f" 等 {len(target_labels)} 条"
        detail = f"传递率：{transfer}  |  {direction}  |  目标：{target_text}  |  单位：{', '.join(dict.fromkeys(units)) or '未知'}"
        if curve_types:
            detail += f"  |  类型：{', '.join(dict.fromkeys(curve_types))}"
        if frequency_ranges:
            detail += f"  |  频段：{', '.join(dict.fromkeys(frequency_ranges))}"
        if rates:
            detail += f"  |  采样率：{', '.join(dict.fromkeys(rates))}"
        self.derived_task_summary_label.setText(detail)

    def _has_derived_input_ready(self) -> bool:
        transfer_data = self.derived_transfer_combo.currentData()
        return transfer_data is not None and self.derived_input_series_combo.currentData() is not None

    def _derived_transfer_factor(self) -> float:
        if not hasattr(self, "derived_transfer_factor_spin"):
            return 1.0
        return float(self.derived_transfer_factor_spin.value())

    def _derived_input_factor(self) -> float:
        if not hasattr(self, "derived_input_factor_spin"):
            return 1.0
        return float(self.derived_input_factor_spin.value())

    def _hold_enabled(self) -> bool:
        return bool(getattr(self, "hold_button", None) and self.hold_button.isChecked())

    def plot_current(self) -> None:
        if self._derived_only:
            if self._has_derived_input_ready():
                self._plot_derived(keep_existing=self._hold_enabled())
            elif self.derived_result_mode_combo.currentText() == "近似时域" and self._plot_current_psd_curves_as_time(
                keep_existing=self._hold_enabled(),
            ):
                return
            else:
                self.statusBar().showMessage("换算页缺少传递率曲线或待换算数据")
            return
        selected = self._selected_series()
        if not selected:
            self.statusBar().showMessage("No channels selected")
            return
        self._time_series_cache.clear()
        self._bulk_time_series_cache.clear()
        self._selected_channel_keys_by_dataset = {}
        self._last_time_pair_transfer_description = None
        keep_existing = self._hold_enabled()
        if not keep_existing:
            for plot in self._all_analysis_plots():
                self._clear_data_tips(plot)
        for dataset, series in selected:
            keys = self._selected_channel_keys_by_dataset.setdefault(dataset.id, set())
            keys.add(series.channel_key)
            if dataset.series:
                keys.add(dataset.series[0].channel_key)
        for plot, combo in zip(self.main_plots, self.main_mode_combos):
            self._plot_main_axis(plot, combo.currentText(), selected, keep_existing=keep_existing)
        self._plot_foundation(selected, keep_existing=keep_existing)
        if self._include_derived_tab:
            self._plot_derived(keep_existing=keep_existing, quiet=True)
        if self._last_time_pair_transfer_description:
            self.statusBar().showMessage(
                f"已绘制 {len(selected)} 条数据；传递率方向：{self._last_time_pair_transfer_description}"
            )
        else:
            self.statusBar().showMessage(f"Plotted {len(selected)} selected channel(s)")

    def _clear_plots(self, *, show_status: bool = True) -> None:
        for plot in self._all_analysis_plots():
            self._clear_plot_with_title(plot, "")
        if show_status:
            self.statusBar().showMessage("Cleared analysis plots")

    def _clear_plots_later(self, *, show_status: bool = True) -> None:
        if self._clear_plots_pending:
            return
        self._clear_plots_pending = True
        QtCore.QTimer.singleShot(
            0,
            lambda: self._finish_clear_plots_later(show_status=show_status) if _qt_object_is_valid(self) else None,
        )

    def _finish_clear_plots_later(self, *, show_status: bool = True) -> None:
        self._clear_plots_pending = False
        self._clear_plots(show_status=show_status)

    def _selected_series(self) -> list[tuple[AnalysisDataset, AnalysisSeries]]:
        selected_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        selected: list[tuple[AnalysisDataset, AnalysisSeries]] = []
        for dataset in self._datasets:
            for series in dataset.series:
                if series.id in selected_ids:
                    selected.append((dataset, series))
        return selected

    def _plot_main_axis(
        self,
        plot: pg.PlotWidget,
        mode: str,
        selected: list[tuple[AnalysisDataset, AnalysisSeries]],
        *,
        keep_existing: bool = False,
    ) -> None:
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._apply_plot_theme(plot)
        mode = str(mode)
        self._plot_curve_kind[plot] = {
            "Time": "time",
            "PSD": "psd",
            "CumPSD": "cumulative",
            "Trans": "transfer",
            "Coherence": "coherence",
        }.get(mode, "")
        log_x = mode in {"PSD", "CumPSD", "Trans", "Coherence"}
        log_y = mode == "PSD"
        plot.setLogMode(x=log_x, y=log_y)
        self._log_modes[plot] = (log_x, log_y)
        plot.showGrid(x=True, y=True, alpha=float(self._theme.get("grid_alpha", 0.25)))
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        x_values_for_range: list[np.ndarray] = []
        y_values_for_range: list[np.ndarray] = []
        status_parts: list[str] = []
        curves_to_plot: list[tuple[np.ndarray, np.ndarray, str]] = []
        if mode == "Trans" and len(selected) == 2:
            paired_curve = self._transfer_curve_from_selected_time_series(selected)
            if paired_curve is not None:
                curves_to_plot.append(paired_curve)
        if not curves_to_plot:
            for dataset, series in selected:
                curve = self._curve_for_mode(dataset, series, mode)
                if curve is not None:
                    curves_to_plot.append(curve)
        for curve in curves_to_plot:
            x, y, label = curve
            if x.size < 2 or y.size < 2:
                continue
            x_values_for_range.append(x)
            y_values_for_range.append(y)
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            pen = self._pen_for_curve_index(color_index)
            color_index += 1
            plot.plot(x, y, pen=pen, name=plot_label)
            self._plot_curves[plot][plot_label] = (np.asarray(x, dtype=float), np.asarray(y, dtype=float))
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
        if mode == "Time":
            plot.setLabel("bottom", "Time (s)")
            plot.setLabel("left", quantity_time_label(self.quantity_combo.currentText()))
        elif mode == "PSD":
            plot.setLabel("bottom", "Frequency (Hz)")
            plot.setLabel("left", quantity_psd_label(self.quantity_combo.currentText()))
        elif mode == "CumPSD":
            plot.setLabel("bottom", "Frequency (Hz)")
            plot.setLabel("left", quantity_cumulative_label(self.quantity_combo.currentText()))
        elif mode == "Trans":
            plot.setLabel("bottom", "Frequency (Hz)")
            plot.setLabel("left", "dB")
        elif mode == "Coherence":
            plot.setLabel("bottom", "Frequency (Hz)")
            plot.setLabel("left", "Coherence")
            plot.setYRange(0.0, 1.0, padding=0.0)
        plot.setTitle(mode if color_index else f"{mode} (no valid data)")
        if mode != "Coherence":
            range_curves = self._plot_curves.get(plot, {}) if keep_existing else {}
            if range_curves:
                x_values_for_range = [curve[0] for curve in range_curves.values()]
                y_values_for_range = [curve[1] for curve in range_curves.values()]
            self._auto_range_plot(plot, x_values_for_range, y_values_for_range, log_x=log_x, log_y=log_y)
        if status_parts:
            self.statusBar().showMessage("; ".join(status_parts))

    def _unique_plot_label(self, plot: pg.PlotWidget, label: str) -> str:
        curves = self._plot_curves.setdefault(plot, {})
        if label not in curves:
            return label
        index = 2
        while f"{label}#{index}" in curves:
            index += 1
        return f"{label}#{index}"

    def _curve_for_mode(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        mode: str,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        scale = float(self.scale_spin.value()) * float(series.scale or 1.0)
        start_s, end_s = self._time_window()
        if mode == "Time":
            t, raw = dataset.load_time_series(series.channel_key, start_s=start_s, end_s=end_s, max_points=30_000)
            if raw.size < 2:
                return None
            filtered, trim = apply_filter_to_signal(raw, dataset.sample_rate, self._filter_config())
            t, filtered = crop_signal_edges(t, filtered * scale, trim)
            t, filtered = apply_time_window(t, filtered, start_s, end_s)
            converted = convert_acceleration_time_series(
                filtered,
                dataset.sample_rate,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            count = min(t.size, converted.size)
            return t[:count], converted[:count], self._series_label(dataset, series)

        if mode in {"PSD", "CumPSD"}:
            f, psd = self._psd_for_series(dataset, series, scale=scale)
            if f.size < 2:
                return None
            f, psd = convert_acceleration_psd(
                f,
                psd,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if mode == "CumPSD":
                f, y = compute_cumulative_spectrum(f, psd)
            else:
                y = psd
            return f, y, self._series_label(dataset, series)

        if mode == "Trans":
            return self._transfer_curve(dataset, series)
        if mode == "Coherence":
            return self._coherence_curve(dataset, series)
        return None

    def _psd_for_series(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        *,
        scale: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        use_periodogram = "Periodogram" in self.psd_source_combo.currentText() or series.channel_key not in dataset.autospectrum
        if use_periodogram:
            start_s, end_s = self._analysis_window_for_dataset(dataset)
            _t, raw = self._load_analysis_time_series(dataset, series.channel_key, start_s=start_s, end_s=end_s)
            if raw.size < 2:
                return np.array([], dtype=float), np.array([], dtype=float)
            filtered, trim = apply_filter_to_signal(raw, dataset.sample_rate, self._filter_config())
            if trim > 0 and filtered.size > trim * 2:
                filtered = filtered[trim:-trim]
            block_size = self._fft_block_size(filtered.size, dataset)
            if block_size < filtered.size:
                return compute_welch_psd(filtered * scale, dataset.sample_rate, block_size)
            return compute_periodogram_psd(filtered * scale, dataset.sample_rate)

        frequencies = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        autospectrum = np.asarray(dataset.autospectrum.get(series.channel_key, []), dtype=float)
        count = min(frequencies.size, autospectrum.size)
        f = frequencies[:count]
        power = autospectrum[:count]
        if str(dataset.metadata.get("autospectrum_kind", "")).lower() == "psd":
            psd = power * (float(scale) ** 2)
            valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
            return f[valid], psd[valid]
        rbw = dataset.rbw_hz if dataset.rbw_hz > 0.0 else _infer_rbw(f)
        psd = transform_legacy_autospectrum(
            power,
            "log_power_per_hz",
            rbw,
            euscale_fac=scale,
            wincor=dataset.wincor,
            yapcor_index=int(dataset.metadata.get("legacy_yapcor_index", 1)),
        )
        valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
        return f[valid], psd[valid]

    def _foundation_vibration_curve(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
    ) -> tuple[np.ndarray, np.ndarray]:
        if series.channel_key in dataset.autospectrum and dataset.frequency_hz is not None:
            frequency = np.asarray(dataset.frequency_hz, dtype=float).ravel()
            autospectrum = np.asarray(dataset.autospectrum.get(series.channel_key, []), dtype=float).ravel()
            count = min(frequency.size, autospectrum.size)
            if count >= 3:
                f = frequency[1:count]
                rbw = dataset.rbw_hz if dataset.rbw_hz > 0.0 else _infer_rbw(frequency[:count])
                eu = float(series.scale or 1.0)
                if str(dataset.metadata.get("autospectrum_kind", "")).lower() == "psd":
                    psd = autospectrum[1:count] * (eu**2)
                else:
                    psd = autospectrum[1:count] * (eu**2) / max(float(rbw), 1e-20)
                return compute_third_octave_velocity_rms(f, psd, rbw)

        f, psd = self._psd_for_series(dataset, series, scale=float(series.scale or 1.0))
        if f.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        return compute_third_octave_velocity_rms(f, psd, dataset.rbw_hz if dataset.rbw_hz > 0.0 else _infer_rbw(f))

    def _transfer_curve(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        reference_index = 0
        if series.channel_index == reference_index:
            return None
        ref_key = dataset.series[reference_index].channel_key if dataset.series else "ai0"
        candidates = [
            f"{ref_key}->{series.channel_key}",
            f"ai{reference_index}->ai{series.channel_index}",
            f"ai0->ai{series.channel_index}",
        ]
        frf_values = None
        for key in candidates:
            if key in dataset.frf:
                frf_values = dataset.frf[key]
                break
        if frf_values is None:
            return self._transfer_curve_from_time_data(dataset, series, reference_index)
        f = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        xfer = np.asarray(frf_values)
        count = min(f.size, xfer.size)
        if count < 2:
            return None
        f = f[:count]
        xfer = xfer[:count]
        ref_scale = dataset.series[reference_index].scale if dataset.series else 1.0
        if ref_scale == 0.0:
            return None
        eu_ratio = float(series.scale or 1.0) / float(ref_scale)
        transfer = np.abs(xfer * eu_ratio * float(self.scale_spin.value()))
        valid = np.isfinite(f) & np.isfinite(transfer) & (f > 0.0) & (transfer > 0.0)
        return f[valid], 20.0 * np.log10(np.maximum(transfer[valid], 1e-20)), self._series_label(dataset, series)

    def _transfer_curve_from_selected_time_series(
        self,
        selected: list[tuple[AnalysisDataset, AnalysisSeries]],
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        if len(selected) != 2:
            return None
        if any(dataset.frf for dataset, _series in selected):
            return None
        (reference_dataset, reference), (response_dataset, response) = selected
        if not self._series_has_time_data(reference_dataset, reference) or not self._series_has_time_data(
            response_dataset, response
        ):
            return None

        start_s, end_s = self._time_window()
        t_reference, reference_raw = self._load_analysis_time_series(
            reference_dataset,
            reference.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        t_response, response_raw = self._load_analysis_time_series(
            response_dataset,
            response.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        aligned = self._align_time_series_pair(
            t_reference,
            reference_raw,
            reference_dataset.sample_rate,
            t_response,
            response_raw,
            response_dataset.sample_rate,
        )
        if aligned is None:
            return None
        reference_aligned, response_aligned, sample_rate = aligned
        reference_filtered, reference_trim = apply_filter_to_signal(
            reference_aligned,
            sample_rate,
            self._filter_config(),
        )
        response_filtered, response_trim = apply_filter_to_signal(
            response_aligned,
            sample_rate,
            self._filter_config(),
        )
        trim = max(reference_trim, response_trim)
        if trim > 0 and reference_filtered.size > trim * 2 and response_filtered.size > trim * 2:
            reference_filtered = reference_filtered[trim:-trim]
            response_filtered = response_filtered[trim:-trim]
        block_size = self._fft_block_size(
            min(reference_filtered.size, response_filtered.size),
            reference_dataset,
        )
        frequency, transfer_values = compute_transfer_function_welch(
            reference_filtered,
            response_filtered,
            sample_rate,
            block_size,
        )
        if frequency.size < 2:
            return None
        reference_scale = float(reference.scale or 1.0)
        if reference_scale == 0.0:
            return None
        eu_ratio = float(response.scale or 1.0) / reference_scale
        transfer = np.abs(transfer_values * eu_ratio * float(self.scale_spin.value()))
        valid = np.isfinite(frequency) & np.isfinite(transfer) & (frequency > 0.0) & (transfer > 0.0)
        if np.count_nonzero(valid) < 2:
            return None
        reference_label = self._series_label(reference_dataset, reference)
        response_label = self._series_label(response_dataset, response)
        self._last_time_pair_transfer_description = f"{reference_label} -> {response_label}"
        return (
            frequency[valid],
            20.0 * np.log10(np.maximum(transfer[valid], 1e-20)),
            f"{response_label} / {reference_label}",
        )

    @staticmethod
    def _series_has_time_data(dataset: AnalysisDataset, series: AnalysisSeries) -> bool:
        return bool(dataset.is_continuous or series.channel_key in dataset.channels)

    @staticmethod
    def _align_time_series_pair(
        reference_time: np.ndarray,
        reference_values: np.ndarray,
        reference_sample_rate: float,
        response_time: np.ndarray,
        response_values: np.ndarray,
        response_sample_rate: float,
    ) -> tuple[np.ndarray, np.ndarray, float] | None:
        def prepare(time_values: np.ndarray, signal_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            time_array = np.asarray(time_values, dtype=float).ravel()
            signal_array = np.asarray(signal_values, dtype=float).ravel()
            count = min(time_array.size, signal_array.size)
            time_array = time_array[:count]
            signal_array = signal_array[:count]
            finite = np.isfinite(time_array) & np.isfinite(signal_array)
            time_array = time_array[finite]
            signal_array = signal_array[finite]
            if time_array.size < 2:
                return np.array([], dtype=float), np.array([], dtype=float)
            order = np.argsort(time_array, kind="mergesort")
            time_array = time_array[order]
            signal_array = signal_array[order]
            unique = np.concatenate(([True], np.diff(time_array) > 0.0))
            return time_array[unique], signal_array[unique]

        def effective_rate(time_array: np.ndarray, fallback: float) -> float:
            differences = np.diff(time_array)
            differences = differences[np.isfinite(differences) & (differences > 0.0)]
            if differences.size:
                rate = 1.0 / float(np.median(differences))
                if np.isfinite(rate) and rate > 0.0:
                    return rate
            return float(fallback) if np.isfinite(fallback) and fallback > 0.0 else 1.0

        ref_time, ref_signal = prepare(reference_time, reference_values)
        resp_time, resp_signal = prepare(response_time, response_values)
        if ref_time.size < 2 or resp_time.size < 2:
            return None
        start_time = max(float(ref_time[0]), float(resp_time[0]))
        end_time = min(float(ref_time[-1]), float(resp_time[-1]))
        if not np.isfinite(start_time) or not np.isfinite(end_time) or end_time <= start_time:
            return None
        sample_rate = min(
            effective_rate(ref_time, reference_sample_rate),
            effective_rate(resp_time, response_sample_rate),
        )
        sample_count = int(np.floor((end_time - start_time) * sample_rate + 1e-9)) + 1
        if sample_count < 2:
            return None
        reference_count = int(np.count_nonzero((ref_time >= start_time) & (ref_time <= end_time)))
        response_count = int(np.count_nonzero((resp_time >= start_time) & (resp_time <= end_time)))
        available_count = min(reference_count, response_count)
        if available_count < 2 or sample_count > available_count * 4:
            return None
        common_time = start_time + np.arange(sample_count, dtype=float) / sample_rate
        common_time = common_time[common_time <= end_time + max(1e-12, 1e-9 / sample_rate)]
        if common_time.size < 2:
            return None
        return (
            np.interp(common_time, ref_time, ref_signal),
            np.interp(common_time, resp_time, resp_signal),
            float(sample_rate),
        )

    def _transfer_curve_from_time_data(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        reference_index: int,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        if reference_index >= len(dataset.series):
            return None
        reference = dataset.series[reference_index]
        start_s, end_s = self._analysis_window_for_dataset(dataset)
        _t_ref, ref_raw = self._load_analysis_time_series(
            dataset,
            reference.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        _t_resp, resp_raw = self._load_analysis_time_series(
            dataset,
            series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        count = min(ref_raw.size, resp_raw.size)
        if count < 2:
            return None
        ref_filtered, ref_trim = apply_filter_to_signal(ref_raw[:count], dataset.sample_rate, self._filter_config())
        resp_filtered, resp_trim = apply_filter_to_signal(resp_raw[:count], dataset.sample_rate, self._filter_config())
        trim = max(ref_trim, resp_trim)
        if trim > 0 and ref_filtered.size > trim * 2 and resp_filtered.size > trim * 2:
            ref_filtered = ref_filtered[trim:-trim]
            resp_filtered = resp_filtered[trim:-trim]
        block_size = self._fft_block_size(min(ref_filtered.size, resp_filtered.size), dataset)
        f, xfer = compute_transfer_function_welch(
            ref_filtered,
            resp_filtered,
            dataset.sample_rate,
            block_size,
        )
        if f.size < 2:
            return None
        ref_scale = float(reference.scale or 1.0)
        if ref_scale == 0.0:
            return None
        eu_ratio = float(series.scale or 1.0) / ref_scale
        transfer = np.abs(xfer * eu_ratio * float(self.scale_spin.value()))
        valid = np.isfinite(f) & np.isfinite(transfer) & (f > 0.0) & (transfer > 0.0)
        return f[valid], 20.0 * np.log10(np.maximum(transfer[valid], 1e-20)), self._series_label(dataset, series)

    def _coherence_curve(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        reference_index = 0
        if series.channel_index == reference_index:
            return None
        ref_key = dataset.series[reference_index].channel_key if dataset.series else "ai0"
        candidates = [
            f"{ref_key}->{series.channel_key}",
            f"ai{reference_index}->ai{series.channel_index}",
            f"ai0->ai{series.channel_index}",
        ]
        coherence = None
        for key in candidates:
            if key in dataset.coherence:
                coherence = dataset.coherence[key]
                break
        if coherence is None:
            return self._coherence_curve_from_time_data(dataset, series, reference_index)
        f = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        coh = np.asarray(coherence, dtype=float)
        count = min(f.size, coh.size)
        if count < 2:
            return None
        f = f[:count]
        coh = coh[:count]
        valid = np.isfinite(f) & np.isfinite(coh) & (f > 0.0)
        return f[valid], coh[valid], self._series_label(dataset, series)

    def _coherence_curve_from_time_data(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        reference_index: int,
    ) -> tuple[np.ndarray, np.ndarray, str] | None:
        if reference_index >= len(dataset.series):
            return None
        reference = dataset.series[reference_index]
        start_s, end_s = self._analysis_window_for_dataset(dataset)
        _t_ref, ref_raw = self._load_analysis_time_series(
            dataset,
            reference.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        _t_resp, resp_raw = self._load_analysis_time_series(
            dataset,
            series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        count = min(ref_raw.size, resp_raw.size)
        if count < 2:
            return None
        ref_filtered, ref_trim = apply_filter_to_signal(ref_raw[:count], dataset.sample_rate, self._filter_config())
        resp_filtered, resp_trim = apply_filter_to_signal(resp_raw[:count], dataset.sample_rate, self._filter_config())
        trim = max(ref_trim, resp_trim)
        if trim > 0 and ref_filtered.size > trim * 2 and resp_filtered.size > trim * 2:
            ref_filtered = ref_filtered[trim:-trim]
            resp_filtered = resp_filtered[trim:-trim]
        block_size = self._fft_block_size(min(ref_filtered.size, resp_filtered.size), dataset)
        f, coherence = compute_coherence_welch(
            ref_filtered,
            resp_filtered,
            dataset.sample_rate,
            block_size,
        )
        if f.size < 2:
            return None
        valid = np.isfinite(f) & np.isfinite(coherence) & (f > 0.0)
        return f[valid], coherence[valid], self._series_label(dataset, series)

    def _plot_foundation(
        self,
        selected: list[tuple[AnalysisDataset, AnalysisSeries]],
        *,
        keep_existing: bool = False,
    ) -> None:
        vib_dataset = self._foundation_selected_dataset(self.foundation_vib_file_combo)
        stiff_dataset = self._foundation_selected_dataset(self.foundation_stiff_file_combo)
        if vib_dataset is None and selected:
            vib_dataset = selected[0][0]
        if stiff_dataset is None and selected:
            stiff_dataset = selected[0][0]
        if vib_dataset is not None:
            self._plot_foundation_vibration(self.foundation_plots[0], vib_dataset, keep_existing=keep_existing)
        else:
            self._clear_plot_with_title(self.foundation_plots[0], "Ground vibration (no source)")
        if stiff_dataset is not None:
            self._plot_foundation_stiffness(self.foundation_plots[1], stiff_dataset, keep_existing=keep_existing)
            self._plot_foundation_coherence(self.foundation_plots[2], stiff_dataset, keep_existing=keep_existing)
        else:
            self._clear_plot_with_title(self.foundation_plots[1], "Dynamic stiffness (no source)")
            self._clear_plot_with_title(self.foundation_plots[2], "Coherence (no source)")

    def _processing_target_frequency(
        self,
        dataset: AnalysisDataset | None,
        series: AnalysisSeries | str,
    ) -> tuple[np.ndarray, str, str]:
        if dataset is None or not isinstance(series, AnalysisSeries):
            curve = _vc_reference_acceleration_psd(str(series))
            return np.asarray(curve[0], dtype=float), "加速度PSD", "VC参考线"
        frequency, _psd = self._psd_for_series(
            dataset,
            series,
            scale=float(self._derived_input_factor()) * float(series.scale or 1.0),
        )
        return np.asarray(frequency, dtype=float), str(series.unit or ""), self._series_label(dataset, series)

    def _processing_report_for_target(
        self,
        dataset: AnalysisDataset | None,
        series: AnalysisSeries | str,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        phase_available: bool,
        *,
        freq_min: float | None,
        freq_max: float | None,
    ) -> ValidationReport:
        target_f, unit, _label = self._processing_target_frequency(dataset, series)
        return validate_processing_task(
            transfer_frequency_hz=transfer_f,
            transfer_values=transfer_h,
            target_frequency_hz=target_f,
            requested_min_hz=freq_min,
            requested_max_hz=freq_max,
            direction=str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP),
            regularization_floor=float(self.derived_regularization_spin.value()),
            target_unit=unit,
            phase_available=phase_available,
            result_mode=self.derived_result_mode_combo.currentText(),
            allow_dimensionless=bool(getattr(self, "derived_dimensionless_check", None) is None or self.derived_dimensionless_check.isChecked()),
        )

    @staticmethod
    def _merge_processing_reports(reports: list[ValidationReport]) -> ValidationReport | None:
        if not reports:
            return None
        lows = [report.effective_frequency_min_hz for report in reports if report.effective_frequency_min_hz is not None]
        highs = [report.effective_frequency_max_hz for report in reports if report.effective_frequency_max_hz is not None]
        return ValidationReport(
            tuple(issue for report in reports for issue in report.issues),
            min(lows) if lows else None,
            max(highs) if highs else None,
            sum(report.valid_points for report in reports),
            sum(report.discarded_points for report in reports),
        )

    def _current_processing_recipe(
        self,
        transfer_f: np.ndarray,
        targets: list[tuple[AnalysisDataset | None, AnalysisSeries | str]],
        reports: list[ValidationReport],
    ) -> ProcessingRecipe:
        transfer_frequency = np.asarray(transfer_f, dtype=float)
        transfer_descriptor = CurveDescriptor(
            name=self.derived_transfer_combo.currentText(),
            curve_type="transfer",
            frequency_min_hz=float(np.min(transfer_frequency)) if transfer_frequency.size else None,
            frequency_max_hz=float(np.max(transfer_frequency)) if transfer_frequency.size else None,
            point_count=int(transfer_frequency.size),
        )
        target_descriptors: list[CurveDescriptor] = []
        for (dataset, series), report in zip(targets, reports):
            if dataset is None or not isinstance(series, AnalysisSeries):
                name = str(series)
                unit = "加速度PSD"
                sample_rate = None
                curve_type = "vc_reference"
            else:
                name = self._series_label(dataset, series)
                unit = series.unit or ""
                sample_rate = float(dataset.sample_rate) if np.isfinite(dataset.sample_rate) else None
                curve_type = "series"
            target_descriptors.append(
                CurveDescriptor(
                    name=name,
                    curve_type=curve_type,
                    unit=unit,
                    sample_rate_hz=sample_rate,
                    frequency_min_hz=report.effective_frequency_min_hz,
                    frequency_max_hz=report.effective_frequency_max_hz,
                    point_count=report.valid_points,
                )
            )
        curve_edits = {
            "mode": self._curve_point_edit_mode,
            "identity": self._curve_edit_identity()[1],
            "frequency_hz": self._current_curve_control_points()[0].tolist(),
            "values_db": self._current_curve_control_points()[1].tolist(),
        }
        return ProcessingRecipe(
            transfer=transfer_descriptor,
            targets=tuple(target_descriptors),
            direction=str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP),
            transfer_factor=self._derived_transfer_factor(),
            input_factor=self._derived_input_factor(),
            frequency_min_hz=_parse_optional_float(self.derived_freq_min_edit.text()),
            frequency_max_hz=_parse_optional_float(self.derived_freq_max_edit.text()),
            regularization_floor=float(self.derived_regularization_spin.value()),
            quantity=self.quantity_combo.currentText(),
            result_mode=self.derived_result_mode_combo.currentText(),
            coherence_correction=self.derived_coherence_correction_check.isChecked(),
            allow_dimensionless=bool(
                getattr(self, "derived_dimensionless_check", None) is None
                or self.derived_dimensionless_check.isChecked()
            ),
            output_name_template=(
                self.derived_batch_name_edit.text()
                if hasattr(self, "derived_batch_name_edit")
                else "{name}_{mode}"
            ),
            interpolation={
                "frequency_resolution_hz": float(self._interpolation_resolution_hz),
                "time_resolution_s": float(self._interpolation_resolution_s),
                "time_kind": "statistical_approximation" if self.derived_result_mode_combo.currentText() == "近似时域" else "not_applicable",
            },
            curve_edits=curve_edits,
        )

    def _reset_batch_status(self, targets: list[tuple[AnalysisDataset | None, AnalysisSeries | str]]) -> None:
        table = getattr(self, "derived_batch_status_table", None)
        if table is None:
            return
        table.setRowCount(len(targets))
        if hasattr(self, "derived_batch_cancel_button"):
            self.derived_batch_cancel_button.setEnabled(len(targets) > 1)
            self.derived_batch_cancel_button.setToolTip(
                "在当前目标完成后取消剩余任务" if len(targets) > 1 else "单目标计算不可中途取消"
            )
        for row, (dataset, series) in enumerate(targets):
            label = str(series) if dataset is None else self._series_label(dataset, series)
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            table.setItem(row, 1, QtWidgets.QTableWidgetItem("等待"))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(""))

    def _set_batch_status(self, row: int, status: str, detail: str = "") -> None:
        table = getattr(self, "derived_batch_status_table", None)
        if table is None or row < 0 or row >= table.rowCount():
            return
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(status)))
        table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(detail)))

    def _calculate_all_derived_targets(self) -> None:
        self._plot_derived(keep_existing=False, quiet=False)

    def _cancel_derived_batch(self) -> None:
        if hasattr(self, "derived_batch_cancel_button") and not self.derived_batch_cancel_button.isEnabled():
            self.statusBar().showMessage("单目标计算不可中途取消")
            return
        self._derived_cancel_requested = True
        self.statusBar().showMessage("将在当前目标完成后取消剩余任务")

    def _plot_derived(self, *, keep_existing: bool = False, quiet: bool = False) -> None:
        if not hasattr(self, "derived_plots"):
            return
        selected_transfer = self._selected_derived_transfer()
        input_series = self._derived_input_series()
        if selected_transfer is None or not input_series:
            if self.derived_result_mode_combo.currentText() == "近似时域" and self._plot_current_psd_curves_as_time(
                keep_existing=keep_existing,
                quiet=quiet,
            ):
                return
            issue = ProcessingIssue("error", "transfer" if selected_transfer is None else "target", "请选择传递率曲线和待换算数据。", "missing_selection")
            report = ValidationReport((issue,), None, None, 0, 0)
            self._mark_derived_results_stale("选择不完整")
            self._set_processing_report(report)
            self._set_processing_field_errors(report.issues)
            if not quiet:
                self.statusBar().showMessage("换算页缺少传递率曲线或待换算数据")
            return
        transfer_dataset, transfer_key, source_kind, base_series, top_series, transfer_label = selected_transfer
        if transfer_dataset is not None and base_series is not None and top_series is not None:
            transfer_keys = self._selected_channel_keys_by_dataset.setdefault(transfer_dataset.id, set())
            transfer_keys.update({base_series.channel_key, top_series.channel_key})
        for input_dataset, series in input_series:
            if input_dataset is None or not isinstance(series, AnalysisSeries):
                continue
            input_keys = self._selected_channel_keys_by_dataset.setdefault(input_dataset.id, set())
            input_keys.add(series.channel_key)

        transfer_factor = self._derived_transfer_factor()
        input_factor = self._derived_input_factor()
        transfer = self._transfer_for_derived(
            transfer_dataset,
            transfer_key,
            source_kind,
            base_series,
            top_series,
            transfer_factor=transfer_factor,
            edit_key=self._transfer_edit_key_from_data(self.derived_transfer_combo.currentData()),
        )
        if transfer is None:
            issue = ProcessingIssue("error", "transfer", "无法从所选数据得到有效传递率。", "invalid_transfer")
            report = ValidationReport((issue,), None, None, 0, 0)
            self._mark_derived_results_stale("传递率无效")
            self._set_processing_report(report)
            self._set_processing_field_errors(report.issues)
            if not quiet:
                self.statusBar().showMessage("无法从所选传递率数据得到 H_top/base")
            return
        transfer_f, transfer_h, phase_available = transfer
        direction = str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP)
        regularization = float(self.derived_regularization_spin.value())
        freq_min, min_issue = parse_optional_number(self.derived_freq_min_edit.text(), field="frequency_min")
        freq_max, max_issue = parse_optional_number(self.derived_freq_max_edit.text(), field="frequency_max")
        numeric_issues = tuple(issue for issue in (min_issue, max_issue) if issue is not None)
        if numeric_issues:
            report = ValidationReport(numeric_issues, None, None, 0, 0)
            self._mark_derived_results_stale("参数校验未通过")
            self._set_processing_report(report)
            self._set_processing_field_errors(report.issues)
            self.statusBar().showMessage(numeric_issues[0].message)
            return
        coherence_correction = bool(self.derived_coherence_correction_check.isChecked())
        coherence = (
            self._coherence_for_derived(
                transfer_dataset,
                transfer_key,
                source_kind,
                base_series,
                top_series,
            )
            if coherence_correction
            and transfer_dataset is not None
            and base_series is not None
            and top_series is not None
            else None
        )
        coherence_f = coherence[0] if coherence is not None else None
        coherence_values = coherence[1] if coherence is not None else None

        self._derived_cancel_requested = False
        self._reset_batch_status(input_series)
        reports = [
            self._processing_report_for_target(
                input_dataset,
                series,
                transfer_f,
                transfer_h,
                phase_available,
                freq_min=freq_min,
                freq_max=freq_max,
            )
            for input_dataset, series in input_series
        ]
        merged_report = self._merge_processing_reports(reports)
        self._set_processing_report(merged_report)
        self._set_processing_field_errors(merged_report.issues if merged_report is not None else ())
        results: list[dict[str, object]] = []
        progress = None
        if len(input_series) > 1 and self.isVisible():
            progress = QtWidgets.QProgressDialog("正在批量换算...", "取消", 0, len(input_series), self)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(250)
        for row, ((input_dataset, series), report) in enumerate(zip(input_series, reports)):
            QtWidgets.QApplication.processEvents()
            if self._derived_cancel_requested or (progress is not None and progress.wasCanceled()):
                self._derived_cancel_requested = True
                self._set_batch_status(row, "已取消", "用户取消任务")
                continue
            if progress is not None:
                progress.setValue(row)
                progress.setLabelText(f"正在计算 {row + 1}/{len(input_series)}")
                QtWidgets.QApplication.processEvents()
            if not report.can_run:
                self._set_batch_status(row, "失败", report.errors[0].message)
                continue
            self._set_batch_status(row, "计算中", "")
            if input_dataset is None or not isinstance(series, AnalysisSeries):
                result = self._derived_result_for_vc_reference(
                    str(series),
                    transfer_dataset,
                    base_series,
                    top_series,
                    transfer_f,
                    transfer_h,
                    direction=direction,
                    regularization=regularization,
                    freq_min=freq_min,
                    freq_max=freq_max,
                    input_factor=input_factor,
                    coherence_f=coherence_f,
                    coherence_values=coherence_values,
                    coherence_correction=coherence_correction and coherence is not None,
                )
            else:
                result = self._derived_result_for_series(
                    transfer_dataset,
                    input_dataset,
                    base_series,
                    top_series,
                    series,
                    transfer_f,
                    transfer_h,
                    phase_available,
                    direction=direction,
                    regularization=regularization,
                    freq_min=freq_min,
                    freq_max=freq_max,
                    transfer_factor=transfer_factor,
                    input_factor=input_factor,
                    coherence_f=coherence_f,
                    coherence_values=coherence_values,
                    coherence_correction=coherence_correction and coherence is not None,
                )
            if result is not None:
                results.append(result)
                warning = report.warnings[0].message if report.warnings else ""
                self._set_batch_status(row, "警告" if warning else "完成", warning)
            else:
                self._set_batch_status(row, "失败", "计算未得到有效数据")
        if progress is not None:
            progress.setValue(len(input_series))
            progress.close()
        if not results:
            self._mark_derived_results_stale("计算前校验未通过")
            self._set_processing_report(merged_report)
            self._set_processing_field_errors(merged_report.issues if merged_report is not None else ())
            if not quiet:
                self.statusBar().showMessage("换算未执行：请查看顶部校验信息和批量任务详情")
            return
        self._plot_derived_results(
            transfer_f,
            transfer_h,
            _append_inline_factor_suffix(transfer_label, transfer_factor),
            results,
            keep_existing=keep_existing,
        )
        self._update_derived_result_verification(results)
        self._derived_results_stale = False
        self._derived_stale_reason = ""
        self._last_processing_recipe = self._current_processing_recipe(transfer_f, input_series, reports)
        if hasattr(self, "derived_plot_button"):
            self.derived_plot_button.setText("计算 / 更新结果")
        for button in getattr(self, "derived_export_buttons", []):
            button.setEnabled(True)
        if hasattr(self, "derived_batch_export_button"):
            self.derived_batch_export_button.setEnabled(True)
        if not quiet:
            if results:
                suffix = "（部分目标失败）" if len(results) < len(input_series) else ""
                self.statusBar().showMessage(f"换算完成：{len(results)} 条曲线{suffix}")
            else:
                self.statusBar().showMessage("换算没有得到有效曲线")

    def _update_derived_result_verification(self, results: list[dict[str, object]]) -> None:
        label = getattr(self, "derived_verification_label", None)
        if label is None:
            return
        summaries: list[str] = []
        for result in results[:3]:
            name = str(result.get("label", "结果"))
            metrics: list[str] = []
            psd_curve = result.get("psd")
            if psd_curve is not None:
                frequency, psd = _finite_aligned_xy(psd_curve[0], psd_curve[1])
                valid = np.isfinite(frequency) & np.isfinite(psd) & (psd >= 0.0)
                frequency = frequency[valid]
                psd = psd[valid]
                if frequency.size >= 2:
                    metrics.append(f"PSD RMS={np.sqrt(max(float(np.trapezoid(psd, frequency)), 0.0)):.5g}")
            time_curve = result.get("time")
            if time_curve is not None:
                _time, values = _finite_aligned_xy(time_curve[0], time_curve[1])
                if values.size:
                    prefix = "统计近似时域" if result.get("time_synthesized") else "时域"
                    metrics.append(f"{prefix} RMS={np.sqrt(float(np.mean(values ** 2))):.5g}")
            foundation_curve = result.get("foundation")
            if foundation_curve is not None:
                _centers, velocity = _finite_aligned_xy(foundation_curve[0], foundation_curve[1])
                if velocity.size:
                    metrics.append(f"1/3倍频程峰值={float(np.max(velocity)):.5g} um/s")
            if metrics:
                summaries.append(f"{name}: {', '.join(metrics)}")
        label.setText("结果校核：" + ("；".join(summaries) if summaries else "当前输出无可校核曲线"))

    def _plot_derived_results(
        self,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        transfer_label: str,
        results: list[dict[str, object]],
        *,
        keep_existing: bool,
    ) -> None:
        self._last_derived_results = [dict(result) for result in results]
        self._plot_selected_transfer_axis(
            self.derived_plots[0],
            transfer_f,
            transfer_h,
            transfer_label,
            keep_existing=keep_existing,
        )
        self._plot_derived_result_axis(
            self.derived_plots[1],
            self.derived_result_mode_combo.currentText(),
            results,
            keep_existing=keep_existing,
        )

    def _synthesize_time_curve_from_psd(
        self,
        frequency_hz: object,
        psd_values: object,
        *,
        seed_parts: tuple[object, ...],
        resolution_s: float | None = None,
        duration_s: float | None = None,
        point_count: int | None = None,
        band_edges: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        try:
            f, psd = _finite_aligned_xy(
                np.asarray(frequency_hz, dtype=float),
                np.asarray(psd_values, dtype=float),
            )
        except (TypeError, ValueError):
            return None
        valid = (f > 0.0) & (psd > 0.0)
        f = f[valid]
        psd = psd[valid]
        if f.size < 2:
            return None
        order = np.argsort(f)
        f = f[order]
        psd = psd[order]
        sample_rate = 0.0
        if resolution_s is not None:
            try:
                step = float(resolution_s)
            except (TypeError, ValueError):
                step = 0.0
            if np.isfinite(step) and step > 0.0:
                sample_rate = 1.0 / step
        t, values, _sample_rate = synthesize_time_from_psd(
            f,
            psd,
            sample_rate,
            seed=_stable_seed_from_parts(*seed_parts),
            duration_s=duration_s,
            sample_count=point_count,
            max_samples=max(30000, int(point_count or 0)),
            band_edges=band_edges,
        )
        if t.size < 2 or values.size < 2:
            return None
        count = min(t.size, values.size)
        return t[:count], values[:count]

    def _synthesized_time_curve_from_result(
        self,
        result: dict[str, object],
        psd_key: str,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        mimo_matrix = result.get("mimo_time_matrix")
        mimo_frequency = result.get("mimo_time_frequency")
        axis_index = result.get("mimo_axis_index")
        if (
            psd_key == "psd"
            and isinstance(axis_index, int)
            and mimo_matrix is not None
            and mimo_frequency is not None
        ):
            try:
                t_joint, values_joint, _fs = synthesize_time_from_psd_matrix(
                    np.asarray(mimo_frequency, dtype=float),
                    np.asarray(mimo_matrix, dtype=complex),
                    0.0,
                    seed=_stable_seed_from_parts("mimo_joint_time", result.get("label"), result.get("source_label")),
                    max_samples=30000,
                )
            except Exception:
                t_joint = np.array([], dtype=float)
                values_joint = np.zeros((0, 0), dtype=float)
            if t_joint.size >= 2 and values_joint.ndim == 2 and axis_index < values_joint.shape[1]:
                series = convert_acceleration_time_series(
                    values_joint[:, axis_index],
                    1.0 / max(float(np.median(np.diff(t_joint))), 1e-20),
                    self.quantity_combo.currentText(),
                    highpass_enabled=self.highpass_check.isChecked(),
                    highpass_hz=float(self.highpass_spin.value()),
                )
                count = min(t_joint.size, series.size)
                if count >= 2:
                    return t_joint[:count], series[:count]
        curve = result.get(psd_key)
        if curve is None:
            return None
        try:
            frequency_hz, psd_values = curve
        except (TypeError, ValueError):
            return None
        band_edges = result.get(f"{psd_key}_band_edges")
        return self._synthesize_time_curve_from_psd(
            frequency_hz,
            psd_values,
            seed_parts=("result_psd_to_time", psd_key, result.get("label"), result.get("source_label")),
            band_edges=band_edges if isinstance(band_edges, tuple) else None,
        )

    def _store_time_curve_psd_source(
        self,
        plot: pg.PlotWidget,
        label: str,
        psd_curve: object,
        band_edges: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> None:
        try:
            frequency_hz, psd_values = psd_curve
        except (TypeError, ValueError):
            return
        try:
            f, psd = _finite_aligned_xy(
                np.asarray(frequency_hz, dtype=float),
                np.asarray(psd_values, dtype=float),
            )
        except (TypeError, ValueError):
            return
        valid = (f > 0.0) & (psd > 0.0)
        f = f[valid]
        psd = psd[valid]
        if f.size < 2:
            return
        order = np.argsort(f)
        ordered_band_edges = None
        if band_edges is not None:
            lower = np.asarray(band_edges[0], dtype=float).ravel()
            upper = np.asarray(band_edges[1], dtype=float).ravel()
            if lower.size == valid.size and upper.size == valid.size:
                ordered_band_edges = (lower[valid][order], upper[valid][order])
        self._time_curve_psd_sources.setdefault(plot, {})[label] = (
            np.asarray(f[order], dtype=float),
            np.asarray(psd[order], dtype=float),
            ordered_band_edges,
        )

    def _current_psd_curves_for_time_synthesis(self) -> list[tuple[str, np.ndarray, np.ndarray]]:
        if not hasattr(self, "derived_plots") or len(self.derived_plots) < 2:
            return []
        plot = self.derived_plots[1]
        if self._plot_curve_kind.get(plot) != "psd":
            return []
        excluded = self._plot_export_excluded.get(plot, set())
        curves: list[tuple[str, np.ndarray, np.ndarray]] = []
        for label, curve in self._plot_curves.get(plot, {}).items():
            if label in excluded:
                continue
            x, y = curve
            f, psd = _finite_aligned_xy(x, y)
            valid = (f > 0.0) & (psd > 0.0)
            if np.count_nonzero(valid) >= 2:
                curves.append((label, f[valid], psd[valid]))
        return curves

    def _plot_current_psd_curves_as_time(self, *, keep_existing: bool = False, quiet: bool = False) -> bool:
        del keep_existing
        results: list[dict[str, object]] = []
        for label, frequency_hz, psd_values in self._current_psd_curves_for_time_synthesis():
            time_curve = self._synthesize_time_curve_from_psd(
                frequency_hz,
                psd_values,
                seed_parts=("current_psd_plot_to_time", label),
            )
            if time_curve is None:
                continue
            results.append({
                "label": label,
                "psd": (frequency_hz, psd_values),
                "time": time_curve,
                "time_synthesized": True,
            })
        if not results:
            return False
        self._plot_derived_result_axis(
            self.derived_plots[1],
            "近似时域",
            results,
            keep_existing=False,
        )
        if not quiet:
            self.statusBar().showMessage(f"已由当前 PSD 合成 {len(results)} 条近似时域曲线")
        return True

    def _plot_selected_transfer_axis(
        self,
        plot: pg.PlotWidget,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        label: str,
        *,
        keep_existing: bool,
    ) -> None:
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._plot_curve_info[plot] = {}
            self._plot_export_excluded[plot] = set()
            self._plot_curve_kind[plot] = "transfer"
            self._time_curve_psd_sources[plot] = {}
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._curve_edit_items[plot] = []
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._clear_curve_edit_items(plot)
        self._apply_plot_theme(plot)
        plot.setLogMode(x=True, y=False)
        self._log_modes[plot] = (True, False)
        f = np.asarray(transfer_f, dtype=float).ravel()
        magnitude_db = 20.0 * np.log10(np.maximum(np.abs(transfer_h), 1e-20))
        count = min(f.size, magnitude_db.size)
        f = f[:count]
        magnitude_db = magnitude_db[:count]
        valid = np.isfinite(f) & np.isfinite(magnitude_db) & (f > 0.0)
        f = f[valid]
        magnitude_db = magnitude_db[valid]
        if f.size >= 2:
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(f, magnitude_db, pen=self._pen_for_label(label), name=plot_label)
            self._plot_curves[plot][plot_label] = (f, magnitude_db)
            self._active_trace[plot] = plot_label
        plot.setTitle("传递率曲线" if f.size >= 2 else "传递率曲线 (no valid data)")
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "Trans (dB)")
        self._plot_transfer_control_points(plot)
        self._auto_range_plot(plot, [f], [magnitude_db], log_x=True, log_y=False)

    def _clear_curve_edit_items(self, plot: pg.PlotWidget) -> None:
        for item in list(self._curve_edit_items.get(plot, [])):
            try:
                plot.removeItem(item)
            except Exception:
                pass
        self._curve_edit_items[plot] = []

    def _plot_transfer_control_points(self, plot: pg.PlotWidget) -> None:
        f, db = self._current_transfer_control_points()
        if f.size < 2:
            return
        for index, (freq, value_db) in enumerate(zip(f, db)):
            point = EditableControlPoint(
                x=[self._to_plot_x(plot, float(freq))],
                y=[self._to_plot_y(plot, float(value_db))],
                size=9,
                symbol="o",
                brush=pg.mkBrush("#ffffff"),
                pen=pg.mkPen("#d7263d", width=1.4),
                pxMode=True,
                on_drag=lambda scene_pos, i=index, p=plot: self._drag_transfer_control_point_to_scene_pos(
                    p,
                    i,
                    scene_pos,
                ),
                on_drag_started=self._begin_curve_control_point_drag,
                on_drag_finished=self._finish_curve_control_point_drag,
            )
            point.setZValue(40)
            plot.addItem(point)
            self._curve_edit_items.setdefault(plot, []).append(point)

    def _plot_psd_control_points(self, plot: pg.PlotWidget, label: str) -> None:
        if label not in self._psd_edit_points:
            return
        f, db = self._psd_edit_points[label]
        if f.size < 2:
            return
        for index, (freq, value_db) in enumerate(zip(f, db)):
            value = 10.0 ** (float(value_db) / 10.0)
            point = EditableControlPoint(
                x=[self._to_plot_x(plot, float(freq))],
                y=[self._to_plot_y(plot, float(value))],
                size=9,
                symbol="o",
                brush=pg.mkBrush("#ffffff"),
                pen=pg.mkPen("#d7263d", width=1.4),
                pxMode=True,
                on_drag=lambda scene_pos, i=index, trace=label, p=plot: self._drag_psd_control_point_to_scene_pos(
                    p,
                    trace,
                    i,
                    scene_pos,
                ),
                on_drag_started=self._begin_curve_control_point_drag,
                on_drag_finished=self._finish_curve_control_point_drag,
            )
            point.setZValue(40)
            plot.addItem(point)
            self._curve_edit_items.setdefault(plot, []).append(point)

    def _plot_derived_result_axis(
        self,
        plot: pg.PlotWidget,
        mode: str,
        results: list[dict[str, object]],
        *,
        keep_existing: bool,
    ) -> None:
        psd_label = quantity_psd_label(self.quantity_combo.currentText())
        cumulative_label = quantity_cumulative_label(self.quantity_combo.currentText())
        time_label = quantity_time_label(self.quantity_combo.currentText())
        specs = {
            "PSD": ("PSD", "psd", "Frequency (Hz)", psd_label, True, True),
            "CumPSD": (
                "CumPSD",
                "cumulative",
                "Frequency (Hz)",
                cumulative_label,
                True,
                False,
            ),
            "地基振动": (
                "地基振动",
                "foundation",
                "Third-octave center frequency (Hz)",
                "RMS velocity (um/s)",
                True,
                True,
            ),
            "近似时域": ("近似时域", "time", "Time (s)", time_label, False, False),
        }
        title, curve_key, x_label, y_label, log_x, log_y = specs.get(str(mode), specs["PSD"])
        self._plot_curve_kind[plot] = curve_key
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._plot_export_excluded[plot] = set()
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._curve_edit_items[plot] = []
            self._time_curve_psd_sources[plot] = {}
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._clear_curve_edit_items(plot)
        self._apply_plot_theme(plot)
        plot.setLogMode(x=log_x, y=log_y)
        self._log_modes[plot] = (log_x, log_y)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", y_label)
        x_ranges: list[np.ndarray] = []
        y_ranges: list[np.ndarray] = []
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        for result in results:
            display_key = f"display_{curve_key}"
            curve = result.get(display_key) or result.get(curve_key)
            time_synthesized_from_psd = False
            if curve is None and curve_key == "time":
                curve = self._synthesized_time_curve_from_result(result, "psd")
                time_synthesized_from_psd = curve is not None
            if curve is None:
                continue
            x, y = curve
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            if x.size < 2 or y.size < 2:
                continue
            label = str(result.get("label", "derived"))
            if curve_key == "time" and (result.get("time_synthesized") or time_synthesized_from_psd):
                label = f"{label} | PSD合成"
            if curve_key == "psd" and label in self._psd_edit_points:
                x, y = apply_power_db_profile(x, y, *self._psd_edit_points[label])
                if x.size < 2 or y.size < 2:
                    continue
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(
                x,
                y,
                pen=self._pen_for_label(label),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (x, y)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            if curve_key == "time":
                psd_source_curve = result.get("psd")
                if psd_source_curve is not None and (result.get("time_synthesized") or time_synthesized_from_psd):
                    band_edges = result.get("psd_band_edges")
                    self._store_time_curve_psd_source(
                        plot,
                        plot_label,
                        psd_source_curve,
                        band_edges=band_edges if isinstance(band_edges, tuple) else None,
                    )
            color_index += 1
            x_ranges.append(x)
            y_ranges.append(y)
            if curve_key == "psd":
                self._plot_psd_control_points(plot, label)
            stitched_curve = self._stitched_curve_for_mode(mode, x, y)
            if stitched_curve is not None:
                stitched_x, stitched_y, stitch_label = stitched_curve
                if stitched_x.size >= 2 and stitched_y.size >= 2:
                    stitched_plot_label = self._unique_plot_label(plot, f"{label} + {stitch_label}") if keep_existing else f"{label} + {stitch_label}"
                    plot.plot(
                        stitched_x,
                        stitched_y,
                        pen=self._pen_for_label(stitched_plot_label, width=1.5, style=QtCore.Qt.DashLine),
                        name=stitched_plot_label,
                    )
                    self._plot_curves[plot][stitched_plot_label] = (stitched_x, stitched_y)
                    color_index += 1
                    x_ranges.append(stitched_x)
                    y_ranges.append(stitched_y)
            if self.derived_show_source_check.isChecked():
                source_curve = result.get(f"source_{curve_key}")
                source_time_synthesized_from_psd = False
                if source_curve is None and curve_key == "time":
                    source_curve = self._synthesized_time_curve_from_result(result, "source_psd")
                    source_time_synthesized_from_psd = source_curve is not None
                if source_curve is not None:
                    sx, sy = source_curve
                    sx = np.asarray(sx, dtype=float)
                    sy = np.asarray(sy, dtype=float)
                    if sx.size >= 2 and sy.size >= 2:
                        source_label = str(result.get("source_label", "待换算数据"))
                        if curve_key == "time" and (
                            result.get("source_time_synthesized") or source_time_synthesized_from_psd
                        ):
                            source_label = f"{source_label} | PSD合成"
                        source_plot_label = self._unique_plot_label(plot, source_label) if keep_existing else source_label
                        plot.plot(
                            sx,
                            sy,
                            pen=self._pen_for_label(source_label, width=1.15, style=QtCore.Qt.DashLine),
                            name=source_plot_label,
                        )
                        self._plot_curves[plot][source_plot_label] = (sx, sy)
                        if curve_key == "time":
                            source_psd_curve = result.get("source_psd")
                            if source_psd_curve is not None and (
                                result.get("source_time_synthesized") or source_time_synthesized_from_psd
                            ):
                                source_band_edges = result.get("source_psd_band_edges")
                                self._store_time_curve_psd_source(
                                    plot,
                                    source_plot_label,
                                    source_psd_curve,
                                    band_edges=source_band_edges if isinstance(source_band_edges, tuple) else None,
                                )
                        color_index += 1
                        x_ranges.append(sx)
                        y_ranges.append(sy)
        if curve_key in {"psd", "cumulative", "foundation"}:
            self._add_derived_vc_reference_lines(
                plot,
                curve_key,
                x_ranges,
                y_ranges,
                keep_existing=keep_existing,
            )
        plot.setTitle(title if x_ranges else f"{title} (no valid data)")
        range_curves = self._plot_curves.get(plot, {}) if keep_existing else {}
        if range_curves:
            x_ranges = [curve[0] for curve in range_curves.values()]
            y_ranges = [curve[1] for curve in range_curves.values()]
        self._auto_range_plot(plot, x_ranges, y_ranges, log_x=log_x, log_y=log_y)

    def _add_derived_vc_reference_lines(
        self,
        plot: pg.PlotWidget,
        curve_key: str,
        x_ranges: list[np.ndarray],
        y_ranges: list[np.ndarray],
        *,
        keep_existing: bool,
    ) -> None:
        for name in VC_REFERENCE_NAMES:
            checkbox = self.derived_vc_checks.get(name)
            if checkbox is None or not checkbox.isChecked():
                continue
            ref_f, values = _vc_reference_frequency_velocity(name)
            x_values, y_values = self._vc_reference_curve_for_mode(ref_f, values, curve_key)
            if x_values.size < 2 or y_values.size < 2:
                continue
            plot_label = self._unique_plot_label(plot, name) if keep_existing else name
            plot.plot(
                x_values,
                y_values,
                pen=pg.mkPen(VC_REFERENCE_COLORS[name], width=1.6, style=QtCore.Qt.DashLine),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (x_values, y_values)
            self._plot_export_excluded.setdefault(plot, set()).add(plot_label)
            x_ranges.append(x_values)
            y_ranges.append(y_values)

    def _vc_reference_curve_for_mode(
        self,
        frequencies: np.ndarray,
        velocity_um_s: np.ndarray,
        curve_key: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        f = np.asarray(frequencies, dtype=float).ravel()
        velocity = np.asarray(velocity_um_s, dtype=float).ravel()
        count = min(f.size, velocity.size)
        f = f[:count]
        velocity = velocity[:count]
        valid = np.isfinite(f) & np.isfinite(velocity) & (f > 0.0) & (velocity > 0.0)
        f = f[valid]
        velocity = velocity[valid]
        if f.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        if curve_key == "foundation":
            return f, velocity
        centers, lower_edges, upper_edges = third_octave_bands(float(np.min(f) * 0.8), float(np.max(f) * 1.25))
        if centers.size == 0:
            return np.array([], dtype=float), np.array([], dtype=float)
        bandwidths = np.empty_like(f)
        for index, center in enumerate(f):
            nearest = int(np.argmin(np.abs(centers - center)))
            bandwidths[index] = max(float(upper_edges[nearest] - lower_edges[nearest]), 1e-20)
        velocity_psd_si = (velocity / 1e6) ** 2 / bandwidths
        acceleration_psd = velocity_psd_si * (2.0 * np.pi * f) ** 2
        f_quantity, psd_quantity = convert_acceleration_psd(
            f,
            acceleration_psd,
            self.quantity_combo.currentText(),
            highpass_enabled=self.highpass_check.isChecked(),
            highpass_hz=float(self.highpass_spin.value()),
        )
        if curve_key == "cumulative":
            return compute_cumulative_spectrum(f_quantity, psd_quantity)
        return f_quantity, psd_quantity

    def _derived_result_for_vc_reference(
        self,
        name: str,
        transfer_dataset: AnalysisDataset | None,
        base_series: AnalysisSeries | None,
        top_series: AnalysisSeries | None,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        *,
        direction: str,
        regularization: float,
        freq_min: float | None,
        freq_max: float | None,
        input_factor: float,
        coherence_f: np.ndarray | None = None,
        coherence_values: np.ndarray | None = None,
        coherence_correction: bool = False,
    ) -> dict[str, object] | None:
        del transfer_dataset, base_series, top_series
        dense_source = _vc_reference_acceleration_psd_for_transfer_grid(name, transfer_f)
        if dense_source is None:
            f_source_accel, psd_source_accel = _vc_reference_acceleration_psd(name)
        else:
            f_source_accel, psd_source_accel = dense_source
        if f_source_accel.size < 2:
            return None
        psd_source_accel = psd_source_accel * float(input_factor) ** 2
        source_band_edges = _vc_band_edges_for_frequencies(name, f_source_accel)
        f_accel, psd_accel = derive_psd_from_transfer(
            f_source_accel,
            psd_source_accel,
            transfer_f,
            transfer_h,
            direction=direction,
            regularization_floor=regularization,
            coherence_frequency=coherence_f,
            coherence_values=coherence_values,
            coherence_correction=coherence_correction,
            coherence_floor=regularization,
        )
        if f_accel.size and freq_min is not None:
            keep = f_accel >= float(freq_min)
            f_accel = f_accel[keep]
            psd_accel = psd_accel[keep]
        if f_accel.size and freq_max is not None:
            keep = f_accel <= float(freq_max)
            f_accel = f_accel[keep]
            psd_accel = psd_accel[keep]
        if f_source_accel.size and freq_min is not None:
            keep = f_source_accel >= float(freq_min)
            f_source_accel = f_source_accel[keep]
            psd_source_accel = psd_source_accel[keep]
            source_band_edges = _filter_band_edges(source_band_edges, keep)
        if f_source_accel.size and freq_max is not None:
            keep = f_source_accel <= float(freq_max)
            f_source_accel = f_source_accel[keep]
            psd_source_accel = psd_source_accel[keep]
            source_band_edges = _filter_band_edges(source_band_edges, keep)
        if f_accel.size < 2:
            return None
        result_band_edges = _vc_band_edges_for_frequencies(name, f_accel)

        destination = "顶部估算" if direction == DERIVE_BASE_TO_TOP else "地基估算"
        label = _append_inline_factor_suffix(f"{name} -> {destination}", input_factor)
        source_label = f"待换算: {_append_inline_factor_suffix(name, input_factor)}"
        result: dict[str, object] = {"label": label, "source_label": source_label}
        quantity_mode = normalize_quantity_mode(self.quantity_combo.currentText())
        if quantity_mode == "force":
            f_quantity, psd_quantity = _finite_aligned_xy(f_accel, psd_accel)
            valid = (f_quantity > 0.0) & (psd_quantity > 0.0)
            f_quantity = f_quantity[valid]
            psd_quantity = psd_quantity[valid]
        else:
            f_quantity, psd_quantity = convert_acceleration_psd(
                f_accel,
                psd_accel,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
        if f_quantity.size >= 2:
            result["psd"] = (f_quantity, psd_quantity)
            result["psd_band_edges"] = result_band_edges
            result["cumulative"] = compute_cumulative_spectrum(f_quantity, psd_quantity)
        if quantity_mode != "force":
            result["foundation"] = _third_octave_velocity_from_center_psd(f_accel, psd_accel)

        if quantity_mode != "force":
            f_source_quantity, psd_source_quantity = convert_acceleration_psd(
                f_source_accel,
                psd_source_accel,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if f_source_quantity.size >= 2:
                result["source_psd"] = (f_source_quantity, psd_source_quantity)
                result["source_psd_band_edges"] = source_band_edges
                result["source_cumulative"] = compute_cumulative_spectrum(f_source_quantity, psd_source_quantity)
        source_f, source_velocity = _vc_reference_frequency_velocity(name)
        source_velocity = source_velocity * float(input_factor)
        if source_f.size and freq_min is not None:
            keep = source_f >= float(freq_min)
            source_f = source_f[keep]
            source_velocity = source_velocity[keep]
        if source_f.size and freq_max is not None:
            keep = source_f <= float(freq_max)
            source_f = source_f[keep]
            source_velocity = source_velocity[keep]
        result["source_foundation"] = (source_f, source_velocity)
        return result

    def _derived_result_for_series(
        self,
        transfer_dataset: AnalysisDataset | None,
        input_dataset: AnalysisDataset,
        base_series: AnalysisSeries | None,
        top_series: AnalysisSeries | None,
        input_series: AnalysisSeries,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        phase_available: bool,
        *,
        direction: str,
        regularization: float,
        freq_min: float | None,
        freq_max: float | None,
        transfer_factor: float,
        input_factor: float,
        coherence_f: np.ndarray | None = None,
        coherence_values: np.ndarray | None = None,
        coherence_correction: bool = False,
    ) -> dict[str, object] | None:
        cache_key = self._derived_cache_key(
            transfer_dataset,
            input_dataset,
            base_series,
            top_series,
            input_series,
            direction,
            regularization,
            freq_min,
            freq_max,
            transfer_factor,
            input_factor,
            coherence_correction,
        )
        if cache_key in self._derived_result_cache:
            (
                f_accel,
                psd_accel,
                t_time,
                y_time,
                f_source_accel,
                psd_source_accel,
                t_source,
                y_source,
                time_sample_rate,
                source_time_sample_rate,
                time_synthesized,
                source_time_synthesized,
                label,
                source_label,
            ) = self._derived_result_cache[cache_key]
        else:
            f_in, psd_in = self._psd_for_series(
                input_dataset,
                input_series,
                scale=float(input_factor) * float(input_series.scale or 1.0),
            )
            f_accel, psd_accel = derive_psd_from_transfer(
                f_in,
                psd_in,
                transfer_f,
                transfer_h,
                direction=direction,
                regularization_floor=regularization,
                coherence_frequency=coherence_f,
                coherence_values=coherence_values,
                coherence_correction=coherence_correction,
                coherence_floor=regularization,
            )
            if f_accel.size and freq_min is not None:
                keep = f_accel >= float(freq_min)
                f_accel = f_accel[keep]
                psd_accel = psd_accel[keep]
            if f_accel.size and freq_max is not None:
                keep = f_accel <= float(freq_max)
                f_accel = f_accel[keep]
                psd_accel = psd_accel[keep]
            f_source_accel = np.asarray(f_in, dtype=float)
            psd_source_accel = np.asarray(psd_in, dtype=float)
            if f_source_accel.size and freq_min is not None:
                keep = f_source_accel >= float(freq_min)
                f_source_accel = f_source_accel[keep]
                psd_source_accel = psd_source_accel[keep]
            if f_source_accel.size and freq_max is not None:
                keep = f_source_accel <= float(freq_max)
                f_source_accel = f_source_accel[keep]
                psd_source_accel = psd_source_accel[keep]
            t_time, y_time = self._derived_time_curve(
                input_dataset,
                input_series,
                transfer_f,
                transfer_h,
                phase_available,
                direction=direction,
                regularization=regularization,
                input_factor=input_factor,
            )
            time_sample_rate = float(input_dataset.sample_rate)
            time_synthesized = False
            if t_time.size < 2 and f_accel.size >= 2:
                t_time, y_time, time_sample_rate = synthesize_time_from_psd(
                    f_accel,
                    psd_accel,
                    input_dataset.sample_rate,
                    seed=_stable_seed_from_parts(cache_key, "derived_time"),
                )
                time_synthesized = t_time.size >= 2
            t_source, y_source = self._source_time_curve(input_dataset, input_series, input_factor=input_factor)
            source_time_sample_rate = float(input_dataset.sample_rate)
            source_time_synthesized = False
            if t_source.size < 2 and f_source_accel.size >= 2:
                t_source, y_source, source_time_sample_rate = synthesize_time_from_psd(
                    f_source_accel,
                    psd_source_accel,
                    input_dataset.sample_rate,
                    seed=_stable_seed_from_parts(cache_key, "source_time"),
                )
                source_time_synthesized = t_source.size >= 2
            label = _append_inline_factor_suffix(
                self._derived_result_label(input_dataset, input_series, direction),
                input_factor,
            )
            source_label = f"待换算: {_append_inline_factor_suffix(self._series_label(input_dataset, input_series), input_factor)}"
            self._derived_result_cache[cache_key] = (
                f_accel,
                psd_accel,
                t_time,
                y_time,
                f_source_accel,
                psd_source_accel,
                t_source,
                y_source,
                time_sample_rate,
                source_time_sample_rate,
                time_synthesized,
                source_time_synthesized,
                label,
                source_label,
            )
        if f_accel.size < 2 and t_time.size < 2:
            return None
        result: dict[str, object] = {"label": label, "source_label": source_label}
        quantity_mode = normalize_quantity_mode(self.quantity_combo.currentText())
        if f_accel.size >= 2:
            f_quantity, psd_quantity = convert_acceleration_psd(
                f_accel,
                psd_accel,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if f_quantity.size >= 2:
                result["psd"] = (f_quantity, psd_quantity)
                result["display_psd"] = (f_quantity, psd_quantity)
                result["cumulative"] = compute_cumulative_spectrum(f_quantity, psd_quantity)
            if quantity_mode != "force":
                rbw = _infer_rbw(f_accel)
                result["foundation"] = compute_third_octave_velocity_rms(f_accel, psd_accel, rbw)
        if f_source_accel.size >= 2 and quantity_mode != "force":
            f_source_quantity, psd_source_quantity = convert_acceleration_psd(
                f_source_accel,
                psd_source_accel,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            if f_source_quantity.size >= 2:
                result["source_psd"] = (f_source_quantity, psd_source_quantity)
                result["source_cumulative"] = compute_cumulative_spectrum(f_source_quantity, psd_source_quantity)
            source_rbw = _infer_rbw(f_source_accel)
            result["source_foundation"] = compute_third_octave_velocity_rms(
                f_source_accel,
                psd_source_accel,
                source_rbw,
            )
        if f_source_accel.size >= 2 and quantity_mode == "force" and direction == DERIVE_TOP_TO_BASE:
            source_rbw = _infer_rbw(f_source_accel)
            result["source_foundation"] = compute_third_octave_velocity_rms(
                f_source_accel,
                psd_source_accel,
                source_rbw,
            )
        if t_time.size >= 2:
            converted_time = convert_acceleration_time_series(
                y_time,
                time_sample_rate,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            count = min(t_time.size, converted_time.size)
            result["time"] = (t_time[:count], converted_time[:count])
            if time_synthesized:
                result["time_synthesized"] = True
        if t_source.size >= 2 and quantity_mode != "force":
            converted_source_time = convert_acceleration_time_series(
                y_source,
                source_time_sample_rate,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            count = min(t_source.size, converted_source_time.size)
            result["source_time"] = (t_source[:count], converted_source_time[:count])
            if source_time_synthesized:
                result["source_time_synthesized"] = True
        return result

    def _derived_time_curve(
        self,
        input_dataset: AnalysisDataset,
        input_series: AnalysisSeries,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        phase_available: bool,
        *,
        direction: str,
        regularization: float,
        input_factor: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if not phase_available:
            return np.array([], dtype=float), np.array([], dtype=float)
        start_s, end_s = self._analysis_window_for_dataset(input_dataset)
        t, raw = self._load_analysis_time_series(
            input_dataset,
            input_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        if raw.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        filtered, trim = apply_filter_to_signal(raw, input_dataset.sample_rate, self._filter_config())
        if trim > 0 and filtered.size > trim * 2:
            filtered = filtered[trim:-trim]
            t = t[trim:-trim]
        values = filtered * float(input_factor) * float(input_series.scale or 1.0)
        return derive_time_from_transfer(
            t,
            values,
            input_dataset.sample_rate,
            transfer_f,
            transfer_h,
            direction=direction,
            regularization_floor=regularization,
        )

    def _source_time_curve(
        self,
        input_dataset: AnalysisDataset,
        input_series: AnalysisSeries,
        *,
        input_factor: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        start_s, end_s = self._analysis_window_for_dataset(input_dataset)
        t, raw = self._load_analysis_time_series(
            input_dataset,
            input_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        if raw.size < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        filtered, trim = apply_filter_to_signal(raw, input_dataset.sample_rate, self._filter_config())
        if trim > 0 and filtered.size > trim * 2:
            filtered = filtered[trim:-trim]
            t = t[trim:-trim]
        values = filtered * float(input_factor) * float(input_series.scale or 1.0)
        return t[: values.size], values

    def _derived_cache_key(
        self,
        transfer_dataset: AnalysisDataset | None,
        input_dataset: AnalysisDataset,
        base_series: AnalysisSeries | None,
        top_series: AnalysisSeries | None,
        input_series: AnalysisSeries,
        direction: str,
        regularization: float,
        freq_min: float | None,
        freq_max: float | None,
        transfer_factor: float,
        input_factor: float,
        coherence_correction: bool = False,
    ) -> tuple[object, ...]:
        config = self._filter_config()
        start_s, end_s = self._time_window()
        return (
            transfer_dataset.id if transfer_dataset is not None else "manual_transfer",
            input_dataset.id,
            base_series.id if base_series is not None else "manual_base",
            top_series.id if top_series is not None else "manual_top",
            input_series.channel_key,
            direction,
            round(float(regularization), 12),
            freq_min,
            freq_max,
            self.psd_source_combo.currentText(),
            round(float(transfer_factor), 12),
            round(float(input_factor), 12),
            bool(coherence_correction),
            round(float(input_series.scale or 1.0), 12),
            round(float(base_series.scale or 1.0), 12) if base_series is not None else 1.0,
            round(float(top_series.scale or 1.0), 12) if top_series is not None else 1.0,
            start_s,
            end_s,
            round(float(self.fs_hint_spin.value()), 6),
            config.lowpass_enabled,
            round(float(config.lowpass_hz), 6),
            config.highpass_enabled,
            round(float(config.highpass_hz), 6),
            config.detrend_enabled,
            int(config.order),
        )

    def _derived_input_series(self) -> list[tuple[AnalysisDataset | None, AnalysisSeries | str]]:
        selected_ids: list[object] = []
        if self._derived_only and hasattr(self, "derived_batch_target_list"):
            selected_ids = [item.data(QtCore.Qt.UserRole) for item in self.derived_batch_target_list.selectedItems()]
        if not selected_ids:
            selected_ids = [self.derived_input_series_combo.currentData()]
        selected: list[tuple[AnalysisDataset | None, AnalysisSeries | str]] = []
        for selected_id in selected_ids:
            if isinstance(selected_id, tuple) and len(selected_id) == 2 and selected_id[0] == "vc_reference":
                selected.append((None, str(selected_id[1])))
                continue
            for dataset in self._datasets:
                for series in dataset.series:
                    if series.id == selected_id:
                        selected.append((dataset, series))
        return selected

    def _selected_derived_transfer(
        self,
    ) -> tuple[AnalysisDataset | None, str, str, AnalysisSeries | None, AnalysisSeries | None, str] | None:
        data = self.derived_transfer_combo.currentData()
        if isinstance(data, tuple) and len(data) == 1 and data[0] == "manual_transfer":
            return (
                None,
                "manual_transfer",
                "manual",
                None,
                None,
                self.derived_transfer_combo.currentText(),
            )
        if isinstance(data, tuple) and len(data) == 6 and data[0] == "psd_pair":
            _kind, transfer_key, base_dataset_id, base_key, top_dataset_id, top_key = data
            base_dataset = self._dataset_by_id(int(base_dataset_id))
            top_dataset = self._dataset_by_id(int(top_dataset_id))
            if base_dataset is None or top_dataset is None:
                return None
            base_series = self._series_for_transfer_endpoint(base_dataset, str(base_key))
            top_series = self._series_for_transfer_endpoint(top_dataset, str(top_key))
            if base_series is None or top_series is None:
                return None
            return (
                None,
                str(transfer_key),
                "psd_pair",
                base_series,
                top_series,
                self.derived_transfer_combo.currentText(),
            )
        if not isinstance(data, tuple) or len(data) != 5:
            return None
        dataset_id, transfer_key, base_key, top_key, source_kind = data
        dataset = self._dataset_by_id(int(dataset_id))
        if dataset is None:
            return None
        base_series = self._series_for_transfer_endpoint(dataset, str(base_key))
        top_series = self._series_for_transfer_endpoint(dataset, str(top_key))
        if base_series is None or top_series is None:
            return None
        return (
            dataset,
            str(transfer_key),
            str(source_kind),
            base_series,
            top_series,
            self.derived_transfer_combo.currentText(),
        )

    def _transfer_for_derived(
        self,
        dataset: AnalysisDataset | None,
        transfer_key: str,
        source_kind: str,
        base_series: AnalysisSeries | None,
        top_series: AnalysisSeries | None,
        *,
        transfer_factor: float,
        edit_key: tuple[object, ...] | None = None,
    ) -> tuple[np.ndarray, np.ndarray, bool] | None:
        if source_kind == "manual":
            f_control, db_control = self._manual_transfer_points
            grid = log_frequency_grid(float(f_control[0]), float(f_control[-1]), points=512)
            f, magnitude = transfer_from_db_points(f_control, db_control, grid)
            if f.size >= 2:
                return f, magnitude * float(transfer_factor), False
            return None
        if source_kind == "psd_pair" and base_series is not None and top_series is not None:
            transfer = self._transfer_for_derived_from_psd_pair(
                base_series,
                top_series,
                transfer_factor=transfer_factor,
            )
            if transfer is None:
                return None
            f, h, phase_available = transfer
            if edit_key in self._transfer_edit_points:
                edited_f, edited_h = apply_db_magnitude_profile(f, h, *self._transfer_edit_points[edit_key])
                return edited_f, edited_h, phase_available
            return transfer
        if dataset is None or base_series is None or top_series is None:
            return None
        if source_kind == "stored" and transfer_key in dataset.frf and dataset.frequency_hz is not None:
            frf_values = dataset.frf[transfer_key]
            f = np.asarray(dataset.frequency_hz, dtype=float).ravel()
            h_raw = np.asarray(frf_values).ravel()
            count = min(f.size, h_raw.size)
            if count >= 2:
                eu_ratio = float(top_series.scale or 1.0) / max(float(base_series.scale or 1.0), 1e-20)
                h = h_raw[:count] * eu_ratio * float(transfer_factor)
                frf_kind = str(dataset.metadata.get("frf_kind", "")).strip().lower()
                phase_available = has_complex_transfer_phase(frf_values) and frf_kind != "magnitude_db"
                if edit_key in self._transfer_edit_points:
                    edited_f, edited_h = apply_db_magnitude_profile(
                        f[:count],
                        h,
                        *self._transfer_edit_points[edit_key],
                    )
                    return edited_f, edited_h, phase_available
                return f[:count], h, phase_available
        transfer = self._transfer_for_derived_from_time_data(
            dataset,
            base_series,
            top_series,
            transfer_factor=transfer_factor,
        )
        if transfer is None:
            return None
        f, h, phase_available = transfer
        if edit_key in self._transfer_edit_points:
            edited_f, edited_h = apply_db_magnitude_profile(f, h, *self._transfer_edit_points[edit_key])
            return edited_f, edited_h, phase_available
        return transfer

    def _coherence_for_derived(
        self,
        dataset: AnalysisDataset,
        transfer_key: str,
        source_kind: str,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        if source_kind == "stored" and transfer_key in dataset.coherence and dataset.frequency_hz is not None:
            f = np.asarray(dataset.frequency_hz, dtype=float).ravel()
            coherence = np.asarray(dataset.coherence[transfer_key], dtype=float).ravel()
            count = min(f.size, coherence.size)
            if count >= 2:
                return f[:count], coherence[:count]
        return self._coherence_for_derived_from_time_data(dataset, base_series, top_series)

    def _coherence_for_derived_from_time_data(
        self,
        dataset: AnalysisDataset,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        start_s, end_s = self._analysis_window_for_dataset(dataset)
        _t_base, base_raw = self._load_analysis_time_series(
            dataset,
            base_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        _t_top, top_raw = self._load_analysis_time_series(
            dataset,
            top_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        count = min(base_raw.size, top_raw.size)
        if count < 2:
            return None
        base_filtered, base_trim = apply_filter_to_signal(base_raw[:count], dataset.sample_rate, self._filter_config())
        top_filtered, top_trim = apply_filter_to_signal(top_raw[:count], dataset.sample_rate, self._filter_config())
        trim = max(base_trim, top_trim)
        if trim > 0 and base_filtered.size > trim * 2 and top_filtered.size > trim * 2:
            base_filtered = base_filtered[trim:-trim]
            top_filtered = top_filtered[trim:-trim]
        block_size = self._fft_block_size(min(base_filtered.size, top_filtered.size), dataset)
        f, coherence = compute_coherence_welch(
            base_filtered,
            top_filtered,
            dataset.sample_rate,
            block_size,
        )
        if f.size < 2:
            return None
        return f, coherence

    def _transfer_for_derived_from_time_data(
        self,
        dataset: AnalysisDataset,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
        *,
        transfer_factor: float,
    ) -> tuple[np.ndarray, np.ndarray, bool] | None:
        start_s, end_s = self._analysis_window_for_dataset(dataset)
        _t_base, base_raw = self._load_analysis_time_series(
            dataset,
            base_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        _t_top, top_raw = self._load_analysis_time_series(
            dataset,
            top_series.channel_key,
            start_s=start_s,
            end_s=end_s,
        )
        count = min(base_raw.size, top_raw.size)
        if count < 2:
            return None
        base_filtered, base_trim = apply_filter_to_signal(base_raw[:count], dataset.sample_rate, self._filter_config())
        top_filtered, top_trim = apply_filter_to_signal(top_raw[:count], dataset.sample_rate, self._filter_config())
        trim = max(base_trim, top_trim)
        if trim > 0 and base_filtered.size > trim * 2 and top_filtered.size > trim * 2:
            base_filtered = base_filtered[trim:-trim]
            top_filtered = top_filtered[trim:-trim]
        block_size = self._fft_block_size(min(base_filtered.size, top_filtered.size), dataset)
        f, h_raw = compute_transfer_function_welch(
            base_filtered,
            top_filtered,
            dataset.sample_rate,
            block_size,
        )
        if f.size < 2:
            return None
        eu_ratio = float(top_series.scale or 1.0) / max(float(base_series.scale or 1.0), 1e-20)
        return f, h_raw * eu_ratio * float(transfer_factor), True

    def _transfer_for_derived_from_psd_pair(
        self,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
        *,
        transfer_factor: float,
    ) -> tuple[np.ndarray, np.ndarray, bool] | None:
        base_pair = self._dataset_series_by_id(base_series.id)
        top_pair = self._dataset_series_by_id(top_series.id)
        if base_pair is None or top_pair is None:
            return None
        base_dataset, resolved_base = base_pair
        top_dataset, resolved_top = top_pair
        base_f, base_psd = self._psd_for_series(
            base_dataset,
            resolved_base,
            scale=float(self.scale_spin.value()) * float(resolved_base.scale or 1.0),
        )
        top_f, top_psd = self._psd_for_series(
            top_dataset,
            resolved_top,
            scale=float(self.scale_spin.value()) * float(resolved_top.scale or 1.0),
        )
        base_f, base_psd = _finite_aligned_xy(base_f, base_psd)
        top_f, top_psd = _finite_aligned_xy(top_f, top_psd)
        base_valid = (base_f > 0.0) & (base_psd > 0.0)
        top_valid = (top_f > 0.0) & (top_psd > 0.0)
        base_f = base_f[base_valid]
        base_psd = base_psd[base_valid]
        top_f = top_f[top_valid]
        top_psd = top_psd[top_valid]
        if base_f.size < 2 or top_f.size < 2:
            return None
        low = max(float(base_f[0]), float(top_f[0]))
        high = min(float(base_f[-1]), float(top_f[-1]))
        if not high > low:
            return None
        grid = np.unique(
            np.concatenate(
                [
                    base_f[(base_f >= low) & (base_f <= high)],
                    top_f[(top_f >= low) & (top_f <= high)],
                    np.array([low, high], dtype=float),
                ]
            )
        )
        grid = grid[np.isfinite(grid) & (grid > 0.0)]
        if grid.size < 2:
            return None
        base_i = np.interp(grid, base_f, base_psd, left=np.nan, right=np.nan)
        top_i = np.interp(grid, top_f, top_psd, left=np.nan, right=np.nan)
        valid = np.isfinite(base_i) & np.isfinite(top_i) & (base_i > 0.0) & (top_i > 0.0)
        if np.count_nonzero(valid) < 2:
            return None
        magnitude = np.sqrt(np.maximum(top_i[valid], 0.0) / np.maximum(base_i[valid], 1e-300))
        return grid[valid], magnitude.astype(complex) * float(transfer_factor), False

    def _series_for_transfer_endpoint(
        self,
        dataset: AnalysisDataset,
        endpoint: str,
    ) -> AnalysisSeries | None:
        series = self._series_for_channel_key(dataset, endpoint)
        if series is not None:
            return series
        text = str(endpoint or "").strip().lower()
        if text.startswith("ai"):
            try:
                index = int(text[2:])
            except ValueError:
                return None
            if 0 <= index < len(dataset.series):
                return dataset.series[index]
        return None

    def _series_for_channel_key(
        self,
        dataset: AnalysisDataset | None,
        channel_key: object,
    ) -> AnalysisSeries | None:
        if dataset is None:
            return None
        for series in dataset.series:
            if series.channel_key == channel_key:
                return series
        return None

    @staticmethod
    def _series_for_channel_number(dataset: AnalysisDataset, channel_number: int) -> AnalysisSeries | None:
        index = int(channel_number) - 1
        if index < 0 or index >= len(dataset.series):
            return None
        return dataset.series[index]

    def _derived_result_label(
        self,
        dataset: AnalysisDataset,
        series: AnalysisSeries,
        direction: str,
    ) -> str:
        suffix = "顶部估算" if direction == DERIVE_BASE_TO_TOP else "地基估算"
        return f"{self._series_label(dataset, series)} -> {suffix}"

    def _foundation_selected_dataset(self, combo: QtWidgets.QComboBox) -> AnalysisDataset | None:
        dataset_id = combo.currentData()
        for dataset in self._datasets:
            if dataset.id == dataset_id:
                return dataset
        return None

    @staticmethod
    def _foundation_reference_channel() -> int:
        return 1

    def _all_analysis_plots(self) -> list[pg.PlotWidget]:
        plots: list[pg.PlotWidget] = []
        plots.extend(getattr(self, "main_plots", []))
        plots.extend(getattr(self, "foundation_plots", []))
        plots.extend(getattr(self, "derived_plots", []))
        return plots

    def _clear_plot_with_title(self, plot: pg.PlotWidget, title: str) -> None:
        plot.setUpdatesEnabled(False)
        try:
            for data_tip in list(self._data_tip_items.get(plot, [])):
                self._delete_data_tip(plot, data_tip)
            for item in list(self._curve_edit_items.get(plot, [])):
                try:
                    plot.removeItem(item)
                except Exception:
                    pass
            for item in list(plot.listDataItems()):
                try:
                    plot.removeItem(item)
                except Exception:
                    pass
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            self._plot_curves[plot] = {}
            self._plot_export_excluded[plot] = set()
            self._active_trace[plot] = None
            self._data_tip_items[plot] = []
            self._curve_edit_items[plot] = []
            self._readd_cursor_items(plot)
            self._apply_plot_theme(plot)
            plot.setTitle(title)
        finally:
            plot.setUpdatesEnabled(True)

    def _plot_foundation_vibration(
        self,
        plot: pg.PlotWidget,
        dataset: AnalysisDataset,
        *,
        keep_existing: bool = False,
    ) -> None:
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._apply_plot_theme(plot)
        plot.setLogMode(x=True, y=True)
        self._log_modes[plot] = (True, True)
        f_range: list[np.ndarray] = []
        y_range: list[np.ndarray] = []
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        vib_channels = _parse_channel_list(self.foundation_vib_edit.text())
        for channel_number in vib_channels:
            if channel_number < 1 or channel_number > len(dataset.series):
                continue
            series = dataset.series[channel_number - 1]
            if series.channel_key not in dataset.autospectrum:
                continue
            centers, velocity = self._foundation_vibration_curve(dataset, series)
            if centers.size < 1:
                continue
            label = _foundation_channel_name(channel_number, series.display_name)
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(
                centers,
                velocity,
                pen=self._pen_for_label(label),
                symbol="o",
                symbolSize=5,
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (centers, velocity)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            color_index += 1
            f_range.append(centers)
            y_range.append(velocity)
        vc_checks = {
            "VC A": self.vc_a_check,
            "VC B": self.vc_b_check,
            "VC C": self.vc_c_check,
            "VC D": self.vc_d_check,
            "VC E": self.vc_e_check,
            "VC F": self.vc_f_check,
        }
        for name in VC_REFERENCE_NAMES:
            checkbox = vc_checks.get(name)
            if checkbox is None or not checkbox.isChecked():
                continue
            ref_f, values = _vc_reference_frequency_velocity(name)
            if ref_f.size < 2 or values.size < 2:
                continue
            plot_label = self._unique_plot_label(plot, name) if keep_existing else name
            plot.plot(
                ref_f,
                values,
                pen=pg.mkPen(VC_REFERENCE_COLORS[name], width=1.8, style=QtCore.Qt.DashLine),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (ref_f, values)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            f_range.append(ref_f)
            y_range.append(values)
        plot.setTitle("Ground vibration")
        plot.setLabel("bottom", "Third-octave center frequency (Hz)")
        plot.setLabel("left", "RMS velocity (um/s)")
        range_curves = self._plot_curves.get(plot, {}) if keep_existing else {}
        if range_curves:
            f_range = [curve[0] for curve in range_curves.values()]
            y_range = [curve[1] for curve in range_curves.values()]
        self._auto_range_plot(plot, f_range, y_range, log_x=True, log_y=True)

    def _plot_foundation_stiffness(
        self,
        plot: pg.PlotWidget,
        dataset: AnalysisDataset,
        *,
        keep_existing: bool = False,
    ) -> None:
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._apply_plot_theme(plot)
        plot.setLogMode(x=True, y=True)
        self._log_modes[plot] = (True, True)
        frequency = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        ref_ch = self._foundation_reference_channel()
        resp_channels = _parse_channel_list(self.foundation_resp_edit.text())
        x_ranges: list[np.ndarray] = []
        y_ranges: list[np.ndarray] = []
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        for resp_ch in resp_channels:
            key = f"ai{ref_ch - 1}->ai{resp_ch - 1}"
            if key not in dataset.frf:
                continue
            reference = dataset.series[ref_ch - 1] if ref_ch - 1 < len(dataset.series) else None
            response = dataset.series[resp_ch - 1] if resp_ch - 1 < len(dataset.series) else None
            if response is None:
                continue
            f, stiffness = compute_dynamic_stiffness(
                frequency,
                dataset.frf[key],
                float(response.scale or 1.0),
                float(reference.scale if reference is not None else 1.0),
            )
            if f.size < 2:
                continue
            label = _foundation_channel_name(resp_ch, response.display_name)
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(
                f,
                stiffness,
                pen=self._pen_for_label(label),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (f, stiffness)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            color_index += 1
            x_ranges.append(f)
            y_ranges.append(stiffness)
        if x_ranges:
            spec_x = np.array([max(30.0, min(x[0] for x in x_ranges)), min(1000.0, max(x[-1] for x in x_ranges))])
            if spec_x[1] > spec_x[0]:
                spec_y = np.array([1e8, 1e8])
                spec_label = self._unique_plot_label(plot, "1e8 N/m") if keep_existing else "1e8 N/m"
                plot.plot(spec_x, spec_y, pen=pg.mkPen("#d7263d", width=1.2, style=QtCore.Qt.DashLine), name=spec_label)
                self._plot_curves[plot][spec_label] = (spec_x, spec_y)
                x_ranges.append(spec_x)
                y_ranges.append(spec_y)
        plot.setTitle("Dynamic stiffness")
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "Magnitude (N/m)")
        range_curves = self._plot_curves.get(plot, {}) if keep_existing else {}
        if range_curves:
            x_ranges = [curve[0] for curve in range_curves.values()]
            y_ranges = [curve[1] for curve in range_curves.values()]
        self._auto_range_plot(plot, x_ranges, y_ranges, log_x=True, log_y=True)

    def _plot_foundation_coherence(
        self,
        plot: pg.PlotWidget,
        dataset: AnalysisDataset,
        *,
        keep_existing: bool = False,
    ) -> None:
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._apply_plot_theme(plot)
        plot.setLogMode(x=True, y=False)
        self._log_modes[plot] = (True, False)
        frequency = np.asarray(dataset.frequency_hz if dataset.frequency_hz is not None else [], dtype=float)
        ref_ch = self._foundation_reference_channel()
        resp_channels = _parse_channel_list(self.foundation_resp_edit.text())
        x_ranges: list[np.ndarray] = []
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        for resp_ch in resp_channels:
            key = f"ai{ref_ch - 1}->ai{resp_ch - 1}"
            if key not in dataset.coherence:
                continue
            response = dataset.series[resp_ch - 1] if resp_ch - 1 < len(dataset.series) else None
            coh = np.asarray(dataset.coherence[key], dtype=float)
            count = min(frequency.size, coh.size)
            f = frequency[:count]
            c = coh[:count]
            valid = np.isfinite(f) & np.isfinite(c) & (f > 0.0)
            f = f[valid]
            c = c[valid]
            if f.size < 2:
                continue
            label = _foundation_channel_name(resp_ch, response.display_name if response is not None else f"Ch {resp_ch}")
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(
                f,
                c,
                pen=self._pen_for_label(label),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (f, c)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            color_index += 1
            x_ranges.append(f)
        plot.setTitle("Coherence")
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "Coherence")
        range_curves = self._plot_curves.get(plot, {}) if keep_existing else {}
        if range_curves:
            x_ranges = [curve[0] for curve in range_curves.values()]
        if x_ranges:
            self._auto_range_plot(plot, x_ranges, [np.array([0.0, 1.0])], log_x=True, log_y=False)
        plot.setYRange(0.0, 1.0, padding=0.0)

    def _show_last_load_report(self) -> None:
        if not self._last_load_report:
            self.statusBar().showMessage("当前没有加载报告")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("数据加载报告")
        dialog.resize(680, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        success_count = sum(status == "成功" for _name, status, _detail in self._last_load_report)
        failed_count = len(self._last_load_report) - success_count
        layout.addWidget(QtWidgets.QLabel(f"成功 {success_count}，跳过/失败 {failed_count}"))
        details = "\n".join(
            f"[{status}] {name}{': ' + detail if detail else ''}"
            for name, status, detail in self._last_load_report
        )
        text = QtWidgets.QPlainTextEdit(details)
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy_button = buttons.addButton("复制详情", QtWidgets.QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(details))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _save_processing_recipe(self) -> None:
        if self._last_processing_recipe is None or self._derived_results_stale:
            self.statusBar().showMessage("请先完成一次有效计算，再保存处理配方")
            return
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "保存处理配方",
            str(self._last_directory / "vianalysis_recipe.json"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        destination = Path(path)
        destination.write_text(
            json.dumps(self._last_processing_recipe.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._last_directory = destination.parent
        self.statusBar().showMessage(f"已保存处理配方：{destination.name}")

    def _load_processing_recipe(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "加载处理配方",
            str(self._last_directory),
            "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "配方加载失败", str(exc))
            return
        if not isinstance(payload, dict):
            QtWidgets.QMessageBox.warning(self, "配方加载失败", "配方根节点必须是 JSON 对象。")
            return
        self._suspend_auto_plot = True
        try:
            transfer_payload = payload.get("transfer", {})
            interpolation = payload.get("interpolation", {})
            edits = payload.get("curve_edits", {})
            targets_payload = payload.get("targets", [])
            if not isinstance(transfer_payload, dict):
                raise ValueError("transfer 必须是对象")
            if not isinstance(interpolation, dict):
                raise ValueError("interpolation 必须是对象")
            if not isinstance(edits, dict):
                raise ValueError("curve_edits 必须是对象")
            if not isinstance(targets_payload, list):
                raise ValueError("targets 必须是数组")
            transfer_name = str(transfer_payload.get("name", ""))
            transfer_index = self.derived_transfer_combo.findText(transfer_name)
            if transfer_index >= 0:
                self.derived_transfer_combo.setCurrentIndex(transfer_index)
            direction_index = self._combo_index_for_data(self.derived_direction_combo, payload.get("direction"))
            if direction_index >= 0:
                self.derived_direction_combo.setCurrentIndex(direction_index)
            self.derived_transfer_factor_spin.setValue(float(payload.get("transfer_factor", 1.0)))
            self.derived_input_factor_spin.setValue(float(payload.get("input_factor", 1.0)))
            self.derived_freq_min_edit.setText(_optional_number_text(payload.get("frequency_min_hz")))
            self.derived_freq_max_edit.setText(_optional_number_text(payload.get("frequency_max_hz")))
            self.derived_regularization_spin.setValue(float(payload.get("regularization_floor", 0.0)))
            self.quantity_combo.setCurrentText(str(payload.get("quantity", self.quantity_combo.currentText())))
            self.derived_result_mode_combo.setCurrentText(str(payload.get("result_mode", "PSD")))
            self.derived_coherence_correction_check.setChecked(bool(payload.get("coherence_correction", True)))
            self.derived_dimensionless_check.setChecked(bool(payload.get("allow_dimensionless", False)))
            self.derived_batch_name_edit.setText(str(payload.get("output_name_template", "{name}_{mode}")))
            target_names = {str(item.get("name", "")) for item in targets_payload if isinstance(item, dict)}
            self.derived_batch_target_list.clearSelection()
            for row in range(self.derived_batch_target_list.count()):
                item = self.derived_batch_target_list.item(row)
                item.setSelected(item.text() in target_names)
            self._interpolation_resolution_hz = float(interpolation.get("frequency_resolution_hz", self._interpolation_resolution_hz))
            self._interpolation_resolution_s = float(interpolation.get("time_resolution_s", self._interpolation_resolution_s))
            if edits.get("frequency_hz") and edits.get("values_db"):
                edit_mode = str(edits.get("mode", "transfer"))
                identity = edits.get("identity")
                frequency = np.asarray(edits["frequency_hz"], dtype=float)
                values = np.asarray(edits["values_db"], dtype=float)
                clean_frequency, clean_values, point_issues = validate_control_points(frequency, values)
                point_errors = [issue for issue in point_issues if issue.severity == "error"]
                if point_errors:
                    raise ValueError(point_errors[0].message)
                self._curve_point_edit_mode = edit_mode
                if edit_mode == "psd":
                    if not isinstance(identity, str) or not identity:
                        raise ValueError("PSD 编辑缺少曲线标识")
                    self._active_psd_edit_label = identity
                    self._psd_edit_points[identity] = (clean_frequency, clean_values)
                elif not self._set_current_transfer_control_points(clean_frequency, clean_values, replot=False):
                    raise ValueError("传递率编辑与当前传递率不匹配")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "配方加载失败", f"配方字段无效：{exc}")
            return
        finally:
            self._suspend_auto_plot = False
        self._last_directory = Path(path).parent
        self._mark_derived_results_stale("已加载处理配方")
        self.statusBar().showMessage("处理配方已加载，请检查数据映射后计算")

    def _processing_export_metadata(self) -> dict[str, object]:
        recipe = self._last_processing_recipe.to_dict() if self._last_processing_recipe is not None else {}
        if hasattr(self, "derived_batch_name_edit"):
            recipe["output_name_template"] = self.derived_batch_name_edit.text()
        report = self._last_processing_report
        validation = {}
        if report is not None:
            validation = {
                "effective_frequency_min_hz": report.effective_frequency_min_hz,
                "effective_frequency_max_hz": report.effective_frequency_max_hz,
                "valid_points": report.valid_points,
                "discarded_points": report.discarded_points,
                "issues": [
                    {"severity": issue.severity, "field": issue.field, "code": issue.code, "message": issue.message}
                    for issue in report.issues
                ],
            }
        statistical_time = None
        if self.derived_result_mode_combo.currentText() == "近似时域":
            statistical_time = {
                "kind": "statistical_approximation",
                "seed_policy": "sha256_stable_from_curve_identity",
                "reproduction_seeds": [
                    {
                        "label": str(result.get("label", "")),
                        "seed": _stable_seed_from_parts(
                            "result_psd_to_time",
                            "psd",
                            result.get("label"),
                            result.get("source_label"),
                        ),
                    }
                    for result in self._last_derived_results or []
                ],
                "out_of_band": "zero",
            }
        return {
            "format": "vianalysis_processing_metadata_v1",
            "recipe": recipe,
            "validation": validation,
            "statistical_time": statistical_time,
        }

    def _write_processing_metadata(self, csv_path: Path, *, curve_names: list[str]) -> None:
        metadata = self._processing_export_metadata()
        metadata["exported_curves"] = list(curve_names)
        csv_path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _unique_batch_export_path(directory: Path, output_name: str, reserved: set[Path]) -> Path:
        stem = _safe_filename_part(output_name) or "result"
        candidate = directory / f"{stem}.csv"
        suffix = 2
        while candidate in reserved or candidate.exists() or candidate.with_suffix(".json").exists():
            candidate = directory / f"{stem}#{suffix}.csv"
            suffix += 1
        reserved.add(candidate)
        return candidate

    def _export_all_derived_results(self) -> None:
        if self._derived_results_stale or not self._last_derived_results:
            self.statusBar().showMessage("结果待更新，完成计算后才能批量导出")
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "导出全部换算结果", str(self._last_directory))
        if not directory:
            return
        destination_dir = Path(directory)
        mode = self.derived_result_mode_combo.currentText()
        key = {"PSD": "psd", "CumPSD": "cumulative", "地基振动": "foundation", "近似时域": "time"}.get(mode, "psd")
        exportable = [result for result in self._last_derived_results if result.get(key) is not None]
        preview_names: list[str] = []
        for result in exportable[:5]:
            name = str(result.get("label", "result"))
            try:
                preview_names.append(self.derived_batch_name_edit.text().format(name=name, mode=mode))
            except (KeyError, ValueError):
                preview_names.append(f"{name}_{mode}")
        preview = "\n".join(preview_names)
        if len(exportable) > 5:
            preview += f"\n... 共 {len(exportable)} 条"
        if QtWidgets.QMessageBox.question(
            self,
            "确认批量导出",
            f"将导出 {len(exportable)} 条曲线及同名 JSON 元数据：\n{preview}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        ) != QtWidgets.QMessageBox.Yes:
            return
        exported = 0
        reserved_paths: set[Path] = set()
        for result in exportable:
            curve = result.get(key)
            if curve is None:
                continue
            x_values, y_values = _finite_aligned_xy(curve[0], curve[1])
            if x_values.size < 2:
                continue
            label = str(result.get("label", f"result_{exported + 1}"))
            try:
                output_name = self.derived_batch_name_edit.text().format(name=label, mode=mode)
            except (KeyError, ValueError):
                output_name = f"{label}_{mode}"
            path = self._unique_batch_export_path(destination_dir, output_name, reserved_paths)
            with path.open("w", encoding="utf-8-sig", newline="\n") as handle:
                handle.write("# python_vna_plot_export=1\n")
                handle.write(f"# plot_kind={key}\n")
                handle.write(f"{_safe_header_part(label)}_x,{_safe_header_part(label)}_y\n")
                for x_value, y_value in zip(x_values, y_values):
                    handle.write(f"{x_value:.17g},{y_value:.17g}\n")
            self._write_processing_metadata(path, curve_names=[label])
            exported += 1
        self._last_directory = destination_dir
        self.statusBar().showMessage(f"已导出 {exported} 条结果及 JSON 处理元数据")

    def _export_current_csv(self) -> None:
        main_plots = getattr(self, "main_plots", [])
        plot = self._active_plot or (main_plots[0] if main_plots else None)
        self._export_plot_csv(plot)

    def _plot_interpolation_axis_kind(self, plot: pg.PlotWidget | None) -> str:
        if plot is None:
            return "frequency"
        try:
            label = str(getattr(plot.getAxis("bottom"), "labelText", "") or "")
        except Exception:
            label = ""
        label_lower = label.lower()
        if "time" in label_lower or "时间" in label_lower:
            return "time"
        curve_kind = str(self._plot_curve_kind.get(plot, "") or "").strip().lower()
        if curve_kind == "time":
            return "time"
        return "frequency"

    def _suggest_interpolation_resolution_hz(self, plot: pg.PlotWidget | None) -> float:
        return self._suggest_interpolation_resolution(plot, "frequency")

    def _suggest_interpolation_resolution_s(self, plot: pg.PlotWidget | None) -> float:
        return self._suggest_interpolation_resolution(plot, "time")

    def _suggest_time_interpolation_duration(self, plot: pg.PlotWidget | None) -> float:
        if plot is None:
            return 1.0
        excluded = self._plot_export_excluded.get(plot, set())
        durations: list[float] = []
        for label, (x_values, y_values) in self._plot_curves.get(plot, {}).items():
            if label in excluded:
                continue
            x_arr, _y_arr = _finite_aligned_xy(x_values, y_values)
            x_arr = x_arr[np.isfinite(x_arr)]
            if x_arr.size < 2:
                continue
            duration = float(np.max(x_arr) - np.min(x_arr))
            if np.isfinite(duration) and duration > 0.0:
                durations.append(duration)
        return max(durations) if durations else 1.0

    def _suggest_interpolation_resolution(self, plot: pg.PlotWidget | None, axis_kind: str) -> float:
        default = (
            float(self._interpolation_resolution_s)
            if axis_kind == "time"
            else float(self._interpolation_resolution_hz)
        )
        if plot is None:
            return default
        excluded = self._plot_export_excluded.get(plot, set())
        diffs: list[np.ndarray] = []
        for label, (x_values, y_values) in self._plot_curves.get(plot, {}).items():
            if label in excluded:
                continue
            x_arr, _y_arr = _finite_aligned_xy(x_values, y_values)
            mask = np.isfinite(x_arr)
            if axis_kind == "frequency":
                mask &= x_arr > 0.0
            x_arr = np.unique(np.sort(x_arr[mask]))
            if x_arr.size < 2:
                continue
            delta = np.diff(x_arr)
            delta = delta[np.isfinite(delta) & (delta > 0.0)]
            if delta.size:
                diffs.append(delta)
        if not diffs:
            return default
        resolution = float(np.median(np.concatenate(diffs)))
        if not np.isfinite(resolution) or resolution <= 0.0:
            return default
        return resolution

    def _show_interpolation_dialog_for_plot(self, plot: pg.PlotWidget | None) -> None:
        if plot is None:
            self.statusBar().showMessage("No plot selected for interpolation")
            return
        axis_kind = self._plot_interpolation_axis_kind(plot)
        default_resolution = self._suggest_interpolation_resolution(plot, axis_kind)
        if axis_kind == "time":
            settings = self._show_time_interpolation_dialog(plot, default_resolution)
            if settings is None:
                return
            resolution, duration_s, point_count = settings
            self._interpolation_resolution_s = float(resolution)
            self._interpolate_plot_curves(
                plot,
                float(resolution),
                axis_kind=axis_kind,
                duration_s=duration_s,
                point_count=point_count,
            )
            return
        prompt = "时间步长 (s)" if axis_kind == "time" else "频率分辨率 (Hz)"
        value, accepted = QtWidgets.QInputDialog.getDouble(
            self,
            "曲线插值",
            prompt,
            default_resolution,
            1e-12,
            1e12,
            9,
        )
        if not accepted:
            return
        if axis_kind == "time":
            self._interpolation_resolution_s = float(value)
        else:
            self._interpolation_resolution_hz = float(value)
        self._interpolate_plot_curves(plot, float(value), axis_kind=axis_kind)

    def _show_time_interpolation_dialog(
        self,
        plot: pg.PlotWidget,
        default_resolution: float,
    ) -> tuple[float, float | None, int | None] | None:
        default_duration = self._suggest_time_interpolation_duration(plot)
        default_points = max(2, int(round(default_duration / max(float(default_resolution), 1e-12))) + 1)

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("时域曲线插值")
        layout = QtWidgets.QFormLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        mode_combo = QtWidgets.QComboBox()
        mode_combo.addItem("按步长", "step")
        mode_combo.addItem("按点数", "points")

        step_spin = QtWidgets.QDoubleSpinBox()
        step_spin.setRange(1e-12, 1e12)
        step_spin.setDecimals(9)
        step_spin.setValue(max(float(default_resolution), 1e-12))
        step_spin.setSuffix(" s")

        duration_spin = QtWidgets.QDoubleSpinBox()
        duration_spin.setRange(1e-12, 1e12)
        duration_spin.setDecimals(9)
        duration_spin.setValue(max(float(default_duration), float(default_resolution), 1e-12))
        duration_spin.setSuffix(" s")

        points_spin = QtWidgets.QSpinBox()
        points_spin.setRange(2, 1000000)
        points_spin.setValue(default_points)

        layout.addRow("输出方式", mode_combo)
        layout.addRow("时间步长", step_spin)
        layout.addRow("总时间", duration_spin)
        layout.addRow("数据点数", points_spin)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        syncing = False

        def estimated_points() -> int:
            step = max(float(step_spin.value()), 1e-12)
            duration = max(float(duration_spin.value()), step)
            return max(2, min(points_spin.maximum(), int(np.floor(duration / step)) + 1))

        def sync_dependent_values() -> None:
            nonlocal syncing
            if syncing:
                return
            syncing = True
            try:
                duration = max(float(duration_spin.value()), 1e-12)
                if mode_combo.currentData() == "points":
                    points = max(int(points_spin.value()), 2)
                    step_spin.setValue(duration / max(points - 1, 1))
                else:
                    points_spin.setValue(estimated_points())
            finally:
                syncing = False

        def sync_enabled() -> None:
            by_points = mode_combo.currentData() == "points"
            step_spin.setEnabled(not by_points)
            points_spin.setEnabled(by_points)
            sync_dependent_values()

        mode_combo.currentIndexChanged.connect(lambda _index: sync_enabled())
        step_spin.valueChanged.connect(lambda _value: sync_dependent_values())
        duration_spin.valueChanged.connect(lambda _value: sync_dependent_values())
        points_spin.valueChanged.connect(lambda _value: sync_dependent_values())
        sync_enabled()

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        duration = float(duration_spin.value())
        if mode_combo.currentData() == "points":
            points = int(points_spin.value())
            resolution = duration / max(points - 1, 1)
            return resolution, duration, points
        return float(step_spin.value()), duration, None

    def _interpolate_plot_frequency_curves(self, plot: pg.PlotWidget | None, resolution_hz: float) -> None:
        self._interpolate_plot_curves(plot, resolution_hz, axis_kind="frequency")

    def _interpolate_plot_curves(
        self,
        plot: pg.PlotWidget | None,
        resolution: float,
        *,
        axis_kind: str,
        duration_s: float | None = None,
        point_count: int | None = None,
    ) -> None:
        if plot is None:
            self.statusBar().showMessage("No plot selected for interpolation")
            return
        axis_kind = "time" if axis_kind == "time" else "frequency"
        unit = "s" if axis_kind == "time" else "Hz"
        axis_label = "时间步长" if axis_kind == "time" else "频率分辨率"
        if not np.isfinite(resolution) or resolution <= 0.0:
            self.statusBar().showMessage(f"请输入有效的{axis_label}")
            return
        curves = self._plot_curves.get(plot, {})
        if not curves:
            self.statusBar().showMessage("当前图窗没有可插值曲线")
            return
        log_x, log_y = self._log_modes.get(plot, (False, False))
        if axis_kind == "frequency" and not log_x:
            self.statusBar().showMessage("当前图窗不是频域曲线，无法按 Hz 插值")
            return
        excluded = self._plot_export_excluded.get(plot, set())
        updates: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for label, (x_values, y_values) in curves.items():
            if label in excluded:
                continue
            if axis_kind == "time":
                psd_source = self._time_curve_psd_sources.get(plot, {}).get(label)
                if psd_source is not None and (duration_s is not None or point_count is not None):
                    new_curve = self._synthesize_time_curve_from_psd(
                        psd_source[0],
                        psd_source[1],
                        seed_parts=("time_interpolate_from_psd", label, resolution, duration_s, point_count),
                        resolution_s=resolution,
                        duration_s=duration_s,
                        point_count=point_count,
                        band_edges=psd_source[2] if len(psd_source) > 2 else None,
                    )
                    if new_curve is None:
                        new_x, new_y = np.array([], dtype=float), np.array([], dtype=float)
                    else:
                        new_x, new_y = new_curve
                else:
                    new_x, new_y = _interpolate_linear_x_curve(
                        x_values,
                        y_values,
                        resolution,
                        duration_s=duration_s,
                        point_count=point_count,
                    )
            else:
                new_x, new_y = _interpolate_frequency_curve(
                    x_values,
                    y_values,
                    resolution,
                    log_y=log_y,
                )
            if new_x.size >= 2 and new_y.size >= 2:
                updates[label] = (new_x, new_y)
        if not updates:
            kind_label = "时域" if axis_kind == "time" else "频域"
            self.statusBar().showMessage(f"当前图窗没有可插值的有效{kind_label}曲线")
            return
        data_items_by_name: dict[str, pg.PlotDataItem] = {}
        for item in plot.listDataItems():
            try:
                item_name = item.name()
            except Exception:
                item_name = item.opts.get("name") if hasattr(item, "opts") else None
            if item_name:
                data_items_by_name[str(item_name)] = item
        for label, (new_x, new_y) in updates.items():
            curves[label] = (new_x, new_y)
            item = data_items_by_name.get(label)
            if item is not None:
                item.setData(new_x, new_y)
        self._clear_data_tips(plot)
        self._auto_range_plot(
            plot,
            [curve[0] for curve in curves.values()],
            [curve[1] for curve in curves.values()],
            log_x=log_x,
            log_y=log_y,
        )
        if axis_kind == "time" and point_count is not None:
            self.statusBar().showMessage(f"已按 {point_count} 点插值 {len(updates)} 条曲线")
        elif axis_kind == "time" and duration_s is not None:
            self.statusBar().showMessage(f"已按 {resolution:g} {unit} / {float(duration_s):g} s 插值 {len(updates)} 条曲线")
        else:
            self.statusBar().showMessage(f"已按 {resolution:g} {unit} 插值 {len(updates)} 条曲线")

    def _export_plot_csv(self, plot: pg.PlotWidget | None) -> None:
        if self._derived_only and plot in getattr(self, "derived_plots", []) and self._derived_results_stale:
            self.statusBar().showMessage("结果待更新，重新计算后才能导出")
            return
        if plot is None:
            self.statusBar().showMessage("No active plot for export")
            return
        self._active_plot = plot
        curves = self._plot_curves.get(plot, {})
        if not curves:
            self.statusBar().showMessage("No curves in selected plot for export")
            return
        excluded = self._plot_export_excluded.get(plot, set())
        title = _safe_filename_part(plot.getPlotItem().titleLabel.text or "plot")
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export active plot data",
            str(self._last_directory / f"analysis_{title}.csv"),
            "CSV Files (*.csv);;DAT Text (*.dat)",
        )
        if not path:
            return
        destination = Path(path)
        self._last_directory = destination.parent
        rows: list[tuple[str, np.ndarray, np.ndarray]] = []
        max_count = 0
        for label, (x, y) in curves.items():
            if label in excluded or not self._curve_info_for(plot, label).exportable:
                continue
            x_arr, y_arr = _finite_aligned_xy(x, y)
            if x_arr.size == 0:
                continue
            rows.append((label, x_arr, y_arr))
            max_count = max(max_count, x_arr.size)
        if not rows:
            self.statusBar().showMessage("No exportable active plot data")
            return
        encoding = "utf-8-sig" if destination.suffix.lower() == ".csv" else "utf-8"
        with destination.open("w", encoding=encoding, newline="\n") as handle:
            plot_item = plot.getPlotItem()
            title_text = str(plot_item.titleLabel.text or title)
            plot_kind = str(self._plot_curve_kind.get(plot, "") or "unknown")
            handle.write("# python_vna_plot_export=1\n")
            handle.write(f"# plot_kind={plot_kind}\n")
            handle.write(f"# title={title_text.replace(chr(10), ' ').replace(chr(13), ' ')}\n")
            header = []
            for label, _x, _y in rows:
                safe_label = _safe_header_part(label)
                header.extend([f"{safe_label}_x", f"{safe_label}_y"])
            handle.write(",".join(header) + "\n")
            for row_index in range(max_count):
                values = []
                for _label, x, y in rows:
                    if row_index < x.size and row_index < y.size:
                        values.extend([f"{x[row_index]:.17g}", f"{y[row_index]:.17g}"])
                    else:
                        values.extend(["", ""])
                handle.write(",".join(values) + "\n")
        if self._derived_only and plot in getattr(self, "derived_plots", []):
            self._write_processing_metadata(destination, curve_names=[label for label, _x, _y in rows])
        self.statusBar().showMessage(f"Exported active plot to {destination.name}")

    def _open_plot_window_for_plot(self, plot: pg.PlotWidget | None) -> None:
        if plot is None:
            self.statusBar().showMessage("No plot selected")
            return
        self._active_plot = plot
        curves = self._plot_curves.get(plot, {})
        if not curves:
            self.statusBar().showMessage("Current plot is empty")
            return
        dialog = QtWidgets.QDialog(None)
        dialog.setWindowFlags(
            dialog.windowFlags()
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
        )
        dialog.setSizeGripEnabled(True)
        dialog.setStyleSheet(self._theme_stylesheet(self._theme))
        dialog.setWindowTitle(str(plot.getPlotItem().titleLabel.text or "Analysis Plot"))
        dialog.resize(900, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        detached_plot = self._create_plot_widget(str(plot.getPlotItem().titleLabel.text or "Analysis Plot"))
        layout.addWidget(detached_plot, 1)
        self._apply_plot_theme(detached_plot)
        log_x, log_y = self._log_modes.get(plot, (False, False))
        detached_plot.setLogMode(x=log_x, y=log_y)
        self._log_modes[detached_plot] = (log_x, log_y)
        detached_plot.setLabel("bottom", plot.getAxis("bottom").labelText or "X")
        detached_plot.setLabel("left", plot.getAxis("left").labelText or "Y")
        for index, (label, (x, y)) in enumerate(curves.items()):
            source_item = self._plot_item_for_label(plot, label)
            source_pen = source_item.opts.get("pen") if source_item is not None else None
            detached_plot.plot(
                x,
                y,
                pen=pg.mkPen(source_pen) if source_pen is not None else self._pen_for_curve_index(index),
                name=label,
            )
            self._plot_curves[detached_plot][label] = (np.asarray(x, dtype=float), np.asarray(y, dtype=float))
            if self._active_trace[detached_plot] is None:
                self._active_trace[detached_plot] = label
        self._auto_scale_current_plot(detached_plot)
        self._single_plot_windows.append(dialog)
        dialog.finished.connect(lambda _result, window=dialog: self._forget_single_plot_window(window))
        dialog.show()
        self.statusBar().showMessage("Opened current analysis plot")

    def _forget_single_plot_window(self, dialog: QtWidgets.QDialog) -> None:
        if dialog in self._single_plot_windows:
            self._single_plot_windows.remove(dialog)
        try:
            plots = dialog.findChildren(pg.PlotWidget)
        except RuntimeError:
            return
        for plot in plots:
            self._plot_curves.pop(plot, None)
            self._plot_curve_info.pop(plot, None)
            self._active_trace.pop(plot, None)
            self._data_tip_items.pop(plot, None)
            self._cursor_items.pop(plot, None)
            self._cursor_positions.pop(plot, None)
            self._axis_history.pop(plot, None)
            self._log_modes.pop(plot, None)
            self._plot_export_excluded.pop(plot, None)

    def _time_window(self) -> tuple[float | None, float | None]:
        return _parse_optional_float(self.time_start_edit.text()), _parse_optional_float(self.time_end_edit.text())

    def _analysis_window_for_dataset(self, dataset: AnalysisDataset) -> tuple[float | None, float | None]:
        start_s, end_s = self._time_window()
        return start_s, end_s

    def _load_analysis_time_series(
        self,
        dataset: AnalysisDataset,
        channel_key: str,
        *,
        start_s: float | None,
        end_s: float | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        cache_key = (dataset.id, channel_key, start_s, end_s, None)
        if cache_key not in self._time_series_cache:
            if dataset.is_continuous:
                bulk_key = (dataset.id, start_s, end_s, None)
                if bulk_key not in self._bulk_time_series_cache:
                    keys = sorted(self._selected_channel_keys_by_dataset.get(dataset.id, {channel_key}))
                    self._bulk_time_series_cache[bulk_key] = load_continuous_channels(
                        dataset,
                        keys,
                        start_s=start_s,
                        end_s=end_s,
                        max_points=None,
                    )
                time_s, channels = self._bulk_time_series_cache[bulk_key]
                if channel_key not in channels:
                    keys = set(channels)
                    keys.add(channel_key)
                    keys.update(self._selected_channel_keys_by_dataset.get(dataset.id, set()))
                    self._bulk_time_series_cache[bulk_key] = load_continuous_channels(
                        dataset,
                        sorted(keys),
                        start_s=start_s,
                        end_s=end_s,
                        max_points=None,
                    )
                    time_s, channels = self._bulk_time_series_cache[bulk_key]
                self._time_series_cache[cache_key] = (
                    time_s,
                    channels.get(channel_key, np.array([], dtype=float)),
                )
            else:
                self._time_series_cache[cache_key] = dataset.load_time_series(
                    channel_key,
                    start_s=start_s,
                    end_s=end_s,
                    max_points=None,
                )
        return self._time_series_cache[cache_key]

    def _fft_block_size(self, sample_count: int, dataset: AnalysisDataset) -> int:
        try:
            requested = int(round(float(self.fs_hint_spin.value())))
        except (TypeError, ValueError):
            requested = int(dataset.metadata.get("frame_size", 4096) or 4096)
        if requested <= 0:
            requested = int(dataset.metadata.get("frame_size", 4096) or 4096)
        sample_count = max(0, int(sample_count))
        if sample_count <= 0:
            return max(2, requested)
        return max(2, min(requested, sample_count))

    def _filter_config(self) -> FilterConfig:
        return FilterConfig(
            lowpass_enabled=self.lowpass_check.isChecked(),
            lowpass_hz=float(self.lowpass_spin.value()),
            highpass_enabled=self.highpass_check.isChecked(),
            highpass_hz=float(self.highpass_spin.value()),
            detrend_enabled=self.detrend_check.isChecked(),
            order=int(self.filter_order_spin.value()),
        )

    def _series_label(self, dataset: AnalysisDataset, series: AnalysisSeries) -> str:
        return self._series_labels.get(series.id, _default_series_label(dataset, series))

    def _series_base_label(self, dataset: AnalysisDataset, series: AnalysisSeries) -> str:
        label = self._custom_series_labels.get(
            (dataset.id, series.channel_index + 1),
            _default_series_label(dataset, series),
        )
        return _strip_series_scale_suffix(_strip_vna_suffix_in_series_label(label))

    def _build_series_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        used: set[str] = set()
        for dataset in self._datasets:
            for series in dataset.series:
                key = (dataset.id, series.channel_index + 1)
                label = self._series_base_label(dataset, series)
                if key in self._custom_series_scales:
                    original_scale = self._original_series_scales.get(key, float(series.scale or 1.0))
                    label = _append_series_scale_suffix(
                        label,
                        _safe_scale_ratio(self._custom_series_scales[key], original_scale),
                    )
                root = label
                duplicate_index = 2
                while label in used:
                    label = f"{root}#{duplicate_index}"
                    duplicate_index += 1
                used.add(label)
                labels[series.id] = label
        return labels

    def _sync_series_editors_from_selection(self) -> None:
        selected = self._selected_series()
        self.rename_edit.blockSignals(True)
        self.factor_edit.blockSignals(True)
        try:
            if len(selected) == 1:
                dataset, series = selected[0]
                rename_text = dataset.condition_text or self._series_base_label(dataset, series)
                self._rename_edit_autofill_text = rename_text
                self.rename_edit.setText(rename_text)
                scale = self._custom_series_scales.get(
                    (dataset.id, series.channel_index + 1),
                    float(series.scale or 1.0),
                )
                self.factor_edit.setText(f"{scale:g}")
            else:
                self._rename_edit_autofill_text = ""
                self.rename_edit.setText("")
                self.factor_edit.setText("1")
        finally:
            self.rename_edit.blockSignals(False)
            self.factor_edit.blockSignals(False)

    def _rename_selected_series_confirmed(self) -> None:
        self._rename_selected_series_from_editor(force=True)

    def _rename_selected_series_from_editor(self, *, force: bool = False) -> None:
        selected = self._selected_series()
        if len(selected) != 1:
            return
        dataset, series = selected[0]
        label = self.rename_edit.text().strip()
        if not label:
            self._sync_series_editors_from_selection()
            return
        if not force and label == self._rename_edit_autofill_text:
            return
        self._custom_series_labels[(dataset.id, series.channel_index + 1)] = _strip_series_scale_suffix(label)
        self._derived_result_cache.clear()
        self._refresh_dataset_lists()
        self._notify_data_store_changed("metadata", self)
        self.statusBar().showMessage(f"Renamed channel to {label}")
        self.plot_current()

    def _set_selected_series_scale_from_editor(self) -> None:
        selected = self._selected_series()
        if len(selected) != 1:
            return
        try:
            scale = float(self.factor_edit.text())
        except ValueError:
            self._sync_series_editors_from_selection()
            self.statusBar().showMessage("Factor must be a numeric value")
            return
        if not np.isfinite(scale):
            self._sync_series_editors_from_selection()
            self.statusBar().showMessage("Factor must be finite")
            return
        dataset, series = selected[0]
        self._custom_series_scales[(dataset.id, series.channel_index + 1)] = scale
        series.scale = scale
        self._derived_result_cache.clear()
        self.factor_edit.setText(f"{scale:g}")
        self._refresh_dataset_lists()
        self._notify_data_store_changed("metadata", self)
        self.statusBar().showMessage(f"Updated {self._series_label(dataset, series)} factor to {scale:g}")
        self.plot_current()

    def _apply_plot_theme(self, plot: pg.PlotWidget) -> None:
        theme = self._theme
        plot.setBackground(str(theme.get("plot_bg", "#ffffff")))
        plot.showGrid(x=True, y=True, alpha=float(theme.get("grid_alpha", 0.20)))
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(str(theme.get("axis", "#172033"))))
            axis.setTextPen(pg.mkPen(str(theme.get("axis", "#172033"))))
        apply_plot_legend_theme(plot, theme)
        self._apply_cursor_theme(plot)

    def _cursor_palette(self) -> dict[str, object]:
        return _cursor_palette_for_background(str(self._theme.get("plot_bg", "#ffffff")))

    def _apply_cursor_theme(self, plot: pg.PlotWidget) -> None:
        items = self._cursor_items.get(plot)
        if not items:
            return
        palette = self._cursor_palette()
        items["line"].setPen(pg.mkPen(palette["line"], width=1.4))
        items["point"].setPen(pg.mkPen(palette["line"], width=1.8))
        items["point"].setBrush(pg.mkBrush(255, 255, 255, 0))
        _apply_text_item_style(
            items["text"],
            color=palette["text"],
            fill=palette["fill"],
            border=palette["border"],
        )

    def _toggle_data_tip_mode(self, enabled: bool) -> None:
        self._data_tip_enabled = bool(enabled)
        self.statusBar().showMessage(f"数据提示模式：{'开启' if enabled else '关闭'}")

    def _toggle_cursor_readout(self, enabled: bool) -> None:
        self._cursor_enabled = bool(enabled)
        for items in self._cursor_items.values():
            for item in items.values():
                item.setVisible(False)
        self.statusBar().showMessage(f"读数游标：{'开启' if enabled else '关闭'}")

    def _create_cursor_items(self, plot: pg.PlotWidget) -> dict[str, object]:
        palette = self._cursor_palette()
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(palette["line"], width=1.4))
        line.setZValue(30)
        line.setVisible(False)
        plot.addItem(line, ignoreBounds=True)
        point = pg.ScatterPlotItem(
            size=10,
            symbol="+",
            brush=pg.mkBrush(255, 255, 255, 0),
            pen=pg.mkPen(palette["line"], width=1.8),
            pxMode=True,
        )
        point.setZValue(31)
        point.setVisible(False)
        plot.addItem(point)
        text = pg.TextItem(
            text="",
            color=palette["text"],
            anchor=(-0.05, 1.05),
            fill=pg.mkBrush(palette["fill"]),
            border=pg.mkPen(palette["border"], width=0.9),
        )
        text.setZValue(32)
        text.setVisible(False)
        plot.addItem(text)
        return {"line": line, "point": point, "text": text}

    def _readd_cursor_items(self, plot: pg.PlotWidget) -> None:
        items = self._cursor_items.get(plot)
        if not items:
            return
        if items["line"].scene() is None:
            plot.addItem(items["line"], ignoreBounds=True)
        if items["point"].scene() is None:
            plot.addItem(items["point"])
        if items["text"].scene() is None:
            plot.addItem(items["text"])

    def _set_cursor_position(
        self, plot: pg.PlotWidget, cursor_x: float, cursor_y: float, trace: str | None = None
    ) -> bool:
        if trace:
            self._active_trace[plot] = trace
        self._cursor_positions[plot] = (float(cursor_x), float(cursor_y))
        if not self._cursor_enabled:
            self._toggle_cursor_readout(True)
        items = self._cursor_items.get(plot)
        if not items:
            return False
        plot_x = self._to_plot_x(plot, cursor_x)
        plot_y = self._to_plot_y(plot, cursor_y)
        items["line"].setValue(plot_x)
        items["line"].setVisible(True)
        items["point"].setData([plot_x], [plot_y])
        items["point"].setVisible(True)
        items["text"].setText(f"X {cursor_x:.6g}\nY {cursor_y:.6g}")
        if hasattr(items["text"], "setAnchor"):
            items["text"].setAnchor(self._data_tip_anchor_for_plot_point(plot, cursor_x, cursor_y))
        items["text"].setPos(plot_x, plot_y)
        items["text"].setVisible(True)
        self.statusBar().showMessage(f"读数：x={cursor_x:.4g}, y={cursor_y:.4g}")
        return True

    def _handle_plot_click(self, plot: pg.PlotWidget, event) -> None:
        if not plot.sceneBoundingRect().contains(event.scenePos()):
            return
        self._active_plot = plot
        if event.button() == QtCore.Qt.RightButton:
            event.accept()
            if self._suppress_next_plot_context_menu:
                self._suppress_next_plot_context_menu = False
                return
            trace = self._legend_trace_at_scene_pos(plot, event.scenePos())
            if trace is None:
                mouse_point = plot.getPlotItem().vb.mapSceneToView(event.scenePos())
                trace = self._nearest_trace_name(
                    plot,
                    self._from_plot_x(plot, float(mouse_point.x())),
                    self._from_plot_y(plot, float(mouse_point.y())),
                )
            if trace:
                self._active_trace[plot] = trace
            self._show_plot_context_menu(plot, event.screenPos())
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        mouse_point = plot.getPlotItem().vb.mapSceneToView(event.scenePos())
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        trace = self._nearest_trace_name(plot, click_x, click_y)
        if trace:
            self._active_trace[plot] = trace
        if self._data_tip_enabled:
            self._place_data_tip(plot, click_x, click_y)
        else:
            snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
            if snapped is not None:
                x, y, snapped_trace = snapped
                self._set_cursor_position(plot, x, y, snapped_trace)

    def _show_plot_context_menu(self, plot: pg.PlotWidget, screen_pos) -> None:
        self._active_plot = plot
        menu, actions = self._build_plot_context_menu(plot)
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is actions["back"]:
            self._restore_axis_history(plot)
        elif action is actions["auto"]:
            self._auto_scale_current_plot(plot)
        elif action is actions["data_tip"]:
            self._toggle_data_tip_mode(not self._data_tip_enabled)
        elif action is actions["cursor"]:
            self._toggle_cursor_readout(not self._cursor_enabled)
        elif action is actions["clear_tips"]:
            self._clear_data_tips(plot)
        elif action is actions["delete_curve"]:
            trace = self._active_trace.get(plot)
            if trace:
                self._remove_plot_curves(plot, {trace})
        elif action is actions["manage_curves"]:
            self._show_curve_manager(plot)
        elif action is actions["copy_image"]:
            if copy_widget_image_to_clipboard(plot):
                self.statusBar().showMessage("已复制图像到剪贴板")
            else:
                self.statusBar().showMessage("复制图像失败")

    @staticmethod
    def _legend_trace_at_scene_pos(plot: pg.PlotWidget, scene_pos) -> str | None:
        legend = plot.plotItem.legend
        if legend is None or not legend.sceneBoundingRect().contains(scene_pos):
            return None
        for sample, label_item in getattr(legend, "items", []):
            if not (sample.sceneBoundingRect().contains(scene_pos) or label_item.sceneBoundingRect().contains(scene_pos)):
                continue
            curve_item = getattr(sample, "item", None)
            if curve_item is None:
                continue
            try:
                name = curve_item.name()
            except Exception:
                name = curve_item.opts.get("name") if hasattr(curve_item, "opts") else None
            if name:
                return str(name)
        return None

    def _plot_item_for_label(self, plot: pg.PlotWidget, label: str):
        for item in plot.listDataItems():
            try:
                item_name = item.name()
            except Exception:
                item_name = item.opts.get("name") if hasattr(item, "opts") else None
            if str(item_name or "") == str(label):
                return item
        return None

    def _remove_plot_curves(self, plot: pg.PlotWidget, labels: set[str]) -> int:
        curves = self._plot_curves.get(plot, {})
        removable = {label for label in labels if label in curves and self._curve_info_for(plot, label).removable}
        if not removable:
            self.statusBar().showMessage("未选择可删除的数据曲线")
            return 0
        for data_tip in list(self._data_tip_items.get(plot, [])):
            if data_tip.get("trace") in removable:
                self._delete_data_tip(plot, data_tip)
        legend = plot.plotItem.legend
        for label in removable:
            item = self._plot_item_for_label(plot, label)
            if item is not None:
                if legend is not None:
                    try:
                        legend.removeItem(item)
                    except (AttributeError, RuntimeError):
                        pass
                try:
                    plot.removeItem(item)
                except (RuntimeError, TypeError):
                    pass
            curves.pop(label, None)
            self._plot_curve_info.setdefault(plot, {}).pop(label, None)
            self._plot_export_excluded.setdefault(plot, set()).discard(label)
            self._time_curve_psd_sources.setdefault(plot, {}).pop(label, None)
        if self._active_trace.get(plot) in removable:
            self._active_trace[plot] = next(iter(curves), None)
        if curves:
            log_x, log_y = self._log_modes.get(plot, (False, False))
            self._auto_range_plot(plot, [curve[0] for curve in curves.values()], [curve[1] for curve in curves.values()], log_x=log_x, log_y=log_y)
        else:
            plot.setTitle("")
        self.statusBar().showMessage(f"已从当前图窗删除 {len(removable)} 条曲线")
        return len(removable)

    def _show_curve_manager(self, plot: pg.PlotWidget | None) -> None:
        if plot is None:
            return
        curves = self._plot_curves.get(plot, {})
        if not curves:
            self.statusBar().showMessage("当前图窗没有可管理的曲线")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("当前图窗曲线")
        dialog.resize(620, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["曲线", "类型", "来源"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        button_row = QtWidgets.QHBoxLayout()
        select_all = QtWidgets.QPushButton("全选")
        select_none = QtWidgets.QPushButton("全不选")
        delete_button = QtWidgets.QPushButton("删除选中")
        close_button = QtWidgets.QPushButton("关闭")
        button_row.addWidget(select_all)
        button_row.addWidget(select_none)
        button_row.addStretch(1)
        button_row.addWidget(delete_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        for row, label in enumerate(curves):
            info = self._curve_info_for(plot, label)
            table.insertRow(row)
            for column, value in enumerate((label, info.curve_type, info.source)):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, label)
                table.setItem(row, column, item)
                if not info.removable:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)

        def selected_labels() -> set[str]:
            labels: set[str] = set()
            for index in table.selectionModel().selectedRows():
                item = table.item(index.row(), 0)
                if item is not None:
                    labels.add(str(item.data(QtCore.Qt.UserRole)))
            return labels

        def delete_selected() -> None:
            if self._remove_plot_curves(plot, selected_labels()):
                dialog.accept()

        select_all.clicked.connect(table.selectAll)
        select_none.clicked.connect(table.clearSelection)
        delete_button.clicked.connect(delete_selected)
        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    def _build_plot_context_menu(self, plot: pg.PlotWidget) -> tuple[QtWidgets.QMenu, dict[str, object]]:
        menu = QtWidgets.QMenu(plot)
        actions: dict[str, object] = {}
        actions["back"] = menu.addAction("返回上一缩放")
        actions["auto"] = menu.addAction("自动缩放")
        menu.addSeparator()
        actions["data_tip"] = menu.addAction("数据提示")
        actions["data_tip"].setCheckable(True)
        actions["data_tip"].setChecked(self._data_tip_enabled)
        actions["cursor"] = menu.addAction("读数游标")
        actions["cursor"].setCheckable(True)
        actions["cursor"].setChecked(self._cursor_enabled)
        actions["clear_tips"] = menu.addAction("清除数据提示")
        menu.addSeparator()
        actions["delete_curve"] = menu.addAction("删除当前曲线")
        actions["manage_curves"] = menu.addAction("管理当前图窗曲线")
        trace = self._active_trace.get(plot)
        actions["delete_curve"].setEnabled(
            trace is not None and self._curve_info_for(plot, trace).removable
        )
        actions["manage_curves"].setEnabled(bool(self._plot_curves.get(plot)))
        menu.addSeparator()
        actions["copy_image"] = menu.addAction("复制图像")
        actions["copy_image"].setEnabled(bool(self._plot_curves.get(plot)))
        return menu, actions

    def _move_cursor_from_scene_pos(self, plot: pg.PlotWidget | None, scene_pos) -> bool:
        if plot is None or not plot.sceneBoundingRect().contains(scene_pos):
            return False
        self._active_plot = plot
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        x, y, trace = snapped
        return self._set_cursor_position(plot, x, y, trace)

    def _zoom_plot_to_view_rect(self, plot: pg.PlotWidget | None, start_point, stop_point) -> bool:
        if plot is None:
            return False
        x0 = float(start_point.x())
        x1 = float(stop_point.x())
        y0 = float(start_point.y())
        y1 = float(stop_point.y())
        if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
            return False
        xmin = min(x0, x1)
        xmax = max(x0, x1)
        ymin = min(y0, y1)
        ymax = max(y0, y1)
        plot.setXRange(xmin, xmax, padding=0.0)
        plot.setYRange(ymin, ymax, padding=0.0)
        self.statusBar().showMessage("已缩放图像坐标轴")
        return True

    def _remember_axis_range(self, plot: pg.PlotWidget) -> None:
        if self._axis_scaling_plot is plot:
            return
        ranges = self._current_plot_ranges(plot)
        history = self._axis_history.setdefault(plot, [])
        if not history or not _ranges_close(history[-1], ranges):
            history.append(ranges)
            if len(history) > 8:
                del history[0]

    @staticmethod
    def _current_plot_ranges(plot: pg.PlotWidget) -> tuple[tuple[float, float], tuple[float, float]]:
        x_range, y_range = plot.viewRange()
        return (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )

    def _restore_axis_history(self, plot: pg.PlotWidget) -> bool:
        history = self._axis_history.setdefault(plot, [])
        if len(history) < 2:
            self._auto_scale_current_plot(plot)
            return False
        history.pop()
        x_range, y_range = history.pop()
        self._axis_scaling_plot = plot
        try:
            plot.setXRange(x_range[0], x_range[1], padding=0.0)
            plot.setYRange(y_range[0], y_range[1], padding=0.0)
        finally:
            self._axis_scaling_plot = None
        self.statusBar().showMessage("已恢复上一缩放")
        return True

    def _auto_scale_current_plot(self, plot: pg.PlotWidget) -> None:
        curves = self._plot_curves.get(plot, {})
        log_x, log_y = self._log_modes.get(plot, (False, False))
        self._auto_range_plot(
            plot,
            [curve[0] for curve in curves.values()],
            [curve[1] for curve in curves.values()],
            log_x=log_x,
            log_y=log_y,
        )
        self.statusBar().showMessage("已自动缩放图像")

    def _auto_place_legend(self, plot: pg.PlotWidget) -> None:
        log_x, log_y = self._log_modes.get(plot, (False, False))
        place_legend_away_from_curves(
            plot,
            self._plot_curves.get(plot, {}),
            log_x=log_x,
            log_y=log_y,
            default_offset=(4, 2),
        )

    def _nearest_trace_name(self, plot: pg.PlotWidget, click_x: float, click_y: float) -> str | None:
        curves = self._plot_curves.get(plot, {})
        if not curves:
            return None
        best_name: str | None = None
        best_score: float | None = None
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._to_plot_x(plot, click_x)
        click_plot_y = self._to_plot_y(plot, click_y)
        for name, (x_data, y_data) in curves.items():
            x_arr, y_arr = _finite_aligned_xy(x_data, y_data)
            if x_arr.size == 0:
                continue
            index = int(np.clip(np.searchsorted(x_arr, click_x), 0, x_arr.size - 1))
            if index > 0 and abs(x_arr[index - 1] - click_x) <= abs(x_arr[index] - click_x):
                index -= 1
            dx = (self._to_plot_x(plot, float(x_arr[index])) - click_plot_x) / x_span
            dy = (self._to_plot_y(plot, float(y_arr[index])) - click_plot_y) / y_span
            score = dx * dx + dy * dy
            if best_score is None or score < best_score:
                best_score = score
                best_name = name
        return best_name

    def _nearest_curve_point_2d(
        self, plot: pg.PlotWidget, click_x: float, click_y: float
    ) -> tuple[float, float, str] | None:
        trace = self._active_trace.get(plot) or self._nearest_trace_name(plot, click_x, click_y)
        curves = self._plot_curves.get(plot, {})
        if trace not in curves:
            return None
        x_arr, y_arr = _finite_aligned_xy(*curves[trace])
        if x_arr.size == 0:
            return None
        x_range, y_range = plot.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-9)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-9)
        click_plot_x = self._to_plot_x(plot, click_x)
        click_plot_y = self._to_plot_y(plot, click_y)
        plot_x = np.asarray([self._to_plot_x(plot, float(value)) for value in x_arr])
        plot_y = np.asarray([self._to_plot_y(plot, float(value)) for value in y_arr])
        scores = ((plot_x - click_plot_x) / x_span) ** 2 + ((plot_y - click_plot_y) / y_span) ** 2
        index = int(np.nanargmin(scores))
        return float(x_arr[index]), float(y_arr[index]), trace

    def _place_data_tip(self, plot: pg.PlotWidget, click_x: float, click_y: float) -> bool:
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        tip_x, tip_y, trace = snapped
        self._active_trace[plot] = trace
        data_tip: dict[str, object] = {"trace": trace, "x": tip_x, "y": tip_y}
        point = DataTipPoint(
            [self._to_plot_x(plot, tip_x)],
            [self._to_plot_y(plot, tip_y)],
            size=9,
            symbol="o",
            brush=pg.mkBrush("#fff59d"),
            pen=pg.mkPen("#111111", width=0.8),
            pxMode=True,
            on_drag=lambda scene_pos, plot_widget=plot, tip=data_tip: self._drag_data_tip_to_scene_pos(
                plot_widget, tip, scene_pos
            ),
            on_context_menu=lambda screen_pos, plot_widget=plot, tip=data_tip: self._show_data_tip_menu(
                plot_widget, tip, screen_pos
            ),
        )
        point.setZValue(40)
        text = DataTipText(
            text=f"X {tip_x:.6g}\nY {tip_y:.6g}",
            color="#111111",
            anchor=self._data_tip_anchor_for_plot_point(plot, tip_x, tip_y),
            fill=pg.mkBrush(255, 245, 157, 230),
            border=pg.mkPen("#111111", width=0.8),
            on_context_menu=lambda screen_pos, plot_widget=plot, tip=data_tip: self._show_data_tip_menu(
                plot_widget, tip, screen_pos
            ),
            on_drag=lambda scene_pos, plot_widget=plot, tip=data_tip: self._drag_data_tip_label_to_scene_pos(
                plot_widget, tip, scene_pos
            ),
        )
        text.setZValue(41)
        text.setPos(self._to_plot_x(plot, tip_x), self._to_plot_y(plot, tip_y))
        plot.addItem(point)
        plot.addItem(text)
        data_tip["point"] = point
        data_tip["text"] = text
        self._data_tip_items[plot].append(data_tip)
        self.statusBar().showMessage(f"数据提示：x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _drag_data_tip_to_scene_pos(
        self, plot: pg.PlotWidget, data_tip: dict[str, object], scene_pos
    ) -> bool:
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        click_x = self._from_plot_x(plot, float(mouse_point.x()))
        click_y = self._from_plot_y(plot, float(mouse_point.y()))
        snapped = self._nearest_curve_point_2d(plot, click_x, click_y)
        if snapped is None:
            return False
        tip_x, tip_y, trace = snapped
        data_tip["trace"] = trace
        data_tip["x"] = tip_x
        data_tip["y"] = tip_y
        point = data_tip["point"]
        text = data_tip["text"]
        point.setData([self._to_plot_x(plot, tip_x)], [self._to_plot_y(plot, tip_y)])
        text.setText(f"X {tip_x:.6g}\nY {tip_y:.6g}")
        if hasattr(text, "setAnchor"):
            text.setAnchor(
                data_tip.get("label_anchor")
                if data_tip.get("label_anchor_manual")
                else self._data_tip_anchor_for_plot_point(plot, tip_x, tip_y)
            )
        text.setPos(self._to_plot_x(plot, tip_x), self._to_plot_y(plot, tip_y))
        self.statusBar().showMessage(f"数据提示已移动：x={tip_x:.4g}, y={tip_y:.4g}")
        return True

    def _drag_data_tip_label_to_scene_pos(
        self, plot: pg.PlotWidget, data_tip: dict[str, object], scene_pos
    ) -> bool:
        mouse_point = plot.getPlotItem().vb.mapSceneToView(scene_pos)
        point_x = self._to_plot_x(plot, float(data_tip["x"]))
        point_y = self._to_plot_y(plot, float(data_tip["y"]))
        anchor = _data_tip_anchor_for_label_drag(
            float(mouse_point.x()),
            float(mouse_point.y()),
            point_x,
            point_y,
        )
        data_tip["label_anchor"] = anchor
        data_tip["label_anchor_manual"] = True
        text = data_tip["text"]
        if hasattr(text, "setAnchor"):
            text.setAnchor(anchor)
        self.statusBar().showMessage("数据提示标签已移动")
        return True

    def _show_data_tip_menu(self, plot: pg.PlotWidget, data_tip: dict[str, object], screen_pos) -> None:
        self._active_plot = plot
        self._suppress_plot_context_menu_once()
        menu = QtWidgets.QMenu(self)
        delete_this = menu.addAction("删除此数据提示")
        delete_all = menu.addAction("删除全部数据提示")
        action = menu.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
        if action is delete_this:
            self._delete_data_tip(plot, data_tip)
        elif action is delete_all:
            self._clear_data_tips(plot)

    def _suppress_plot_context_menu_once(self) -> None:
        self._suppress_next_plot_context_menu = True

    def _delete_data_tip(self, plot: pg.PlotWidget, data_tip: dict[str, object]) -> bool:
        if data_tip not in self._data_tip_items.get(plot, []):
            return False
        plot.removeItem(data_tip["point"])
        plot.removeItem(data_tip["text"])
        self._data_tip_items[plot].remove(data_tip)
        return True

    def _clear_data_tips(self, plot: pg.PlotWidget) -> None:
        for data_tip in list(self._data_tip_items.get(plot, [])):
            self._delete_data_tip(plot, data_tip)
        self.statusBar().showMessage("已清除数据提示")

    def _data_tip_anchor_for_plot_point(
        self, plot: pg.PlotWidget, value_x: float, value_y: float
    ) -> tuple[float, float]:
        plot_x = self._to_plot_x(plot, value_x)
        plot_y = self._to_plot_y(plot, value_y)
        x_range, y_range = plot.viewRange()
        x_span = max(float(x_range[1] - x_range[0]), 1e-20)
        y_span = max(float(y_range[1] - y_range[0]), 1e-20)
        near_right = (plot_x - float(x_range[0])) / x_span > 0.72
        near_top = (plot_y - float(y_range[0])) / y_span > 0.72
        return (1.05 if near_right else -0.05, -0.05 if near_top else 1.05)

    def _to_plot_x(self, plot: pg.PlotWidget, value: float) -> float:
        log_x, _log_y = self._log_modes.get(plot, (False, False))
        if log_x:
            return float(np.log10(max(value, 1e-300)))
        return float(value)

    def _from_plot_x(self, plot: pg.PlotWidget, value: float) -> float:
        log_x, _log_y = self._log_modes.get(plot, (False, False))
        if log_x:
            return float(10.0 ** value)
        return float(value)

    def _to_plot_y(self, plot: pg.PlotWidget, value: float) -> float:
        _log_x, log_y = self._log_modes.get(plot, (False, False))
        if log_y:
            return float(np.log10(max(value, 1e-300)))
        return float(value)

    def _from_plot_y(self, plot: pg.PlotWidget, value: float) -> float:
        _log_x, log_y = self._log_modes.get(plot, (False, False))
        if log_y:
            return float(10.0 ** value)
        return float(value)

    def _auto_range_plot(
        self,
        plot: pg.PlotWidget,
        x_arrays: list[np.ndarray],
        y_arrays: list[np.ndarray],
        *,
        log_x: bool,
        log_y: bool,
    ) -> None:
        xs = _concat_finite(x_arrays, positive_only=log_x)
        ys = _concat_finite(y_arrays, positive_only=log_y)
        self._axis_scaling_plot = plot
        try:
            view_box = plot.getPlotItem().vb
            view_box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            if xs.size:
                xmin, xmax = _safe_extent(xs, log_enabled=log_x)
                if log_x:
                    plot.setXRange(np.log10(xmin), np.log10(xmax), padding=0.04)
                else:
                    plot.setXRange(xmin, xmax, padding=0.04)
            if ys.size:
                ymin, ymax = _safe_extent(ys, log_enabled=log_y)
                if log_y:
                    plot.setYRange(np.log10(ymin), np.log10(ymax), padding=0.08)
                else:
                    plot.setYRange(ymin, ymax, padding=0.08)
        finally:
            self._axis_scaling_plot = None
        self._auto_place_legend(plot)


class AnalysisViewer(AnalysisWorkbench):
    """Top-level-compatible analysis window kept for existing entry points."""


def _concat_finite(arrays: list[np.ndarray], *, positive_only: bool = False) -> np.ndarray:
    cleaned: list[np.ndarray] = []
    for values in arrays:
        arr = np.asarray(values, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if positive_only:
            arr = arr[arr > 0.0]
        if arr.size:
            cleaned.append(arr)
    if not cleaned:
        return np.array([], dtype=float)
    return np.concatenate(cleaned)


def _qt_object_is_valid(obj: object) -> bool:
    try:
        return bool(shiboken6.isValid(obj))
    except RuntimeError:
        return False


def _safe_extent(values: np.ndarray, *, log_enabled: bool = False) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if log_enabled:
        arr = arr[arr > 0.0]
    if arr.size == 0:
        return (1e-12, 1.0) if log_enabled else (-1.0, 1.0)
    minimum = float(np.min(arr))
    maximum = float(np.max(arr))
    if maximum > minimum:
        return minimum, maximum
    if log_enabled:
        factor = 10.0 ** 0.05
        minimum = max(minimum, 1e-300)
        return minimum / factor, minimum * factor
    center = minimum
    span = max(abs(center), 1.0) * 0.05
    return center - span, center + span


def _finite_aligned_xy(x_data: np.ndarray, y_data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x_arr = np.asarray(x_data, dtype=float).ravel()
    y_arr = np.asarray(y_data, dtype=float).ravel()
    count = min(x_arr.size, y_arr.size)
    if count <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    x_arr = x_arr[:count]
    y_arr = y_arr[:count]
    finite = np.isfinite(x_arr) & np.isfinite(y_arr)
    return x_arr[finite], y_arr[finite]


def _interpolate_frequency_curve(
    x_data: np.ndarray,
    y_data: np.ndarray,
    resolution_hz: float,
    *,
    log_y: bool,
) -> tuple[np.ndarray, np.ndarray]:
    x_arr, y_arr = _finite_aligned_xy(x_data, y_data)
    valid = x_arr > 0.0
    x_arr = x_arr[valid]
    y_arr = y_arr[valid]
    if x_arr.size < 2 or not np.isfinite(resolution_hz) or resolution_hz <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    order = np.argsort(x_arr)
    x_arr = x_arr[order]
    y_arr = y_arr[order]
    x_arr, unique_indices = np.unique(x_arr, return_index=True)
    y_arr = y_arr[unique_indices]
    if x_arr.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    start = float(x_arr[0])
    stop = float(x_arr[-1])
    if stop <= start:
        return np.array([], dtype=float), np.array([], dtype=float)
    step = float(resolution_hz)
    tolerance = max(step, 1.0) * 1e-10
    start_index = max(1, int(np.ceil((start - tolerance) / step)))
    stop_index = int(np.floor((stop + tolerance) / step))
    if stop_index < start_index:
        return np.array([], dtype=float), np.array([], dtype=float)
    new_x = np.arange(start_index, stop_index + 1, dtype=float) * step
    new_x = np.round(new_x, 12)
    new_x = np.unique(new_x[(new_x >= start - tolerance) & (new_x <= stop + tolerance)])
    source_x = np.log10(x_arr)
    target_x = np.log10(new_x)
    if log_y and np.all(y_arr > 0.0):
        new_y = 10.0 ** np.interp(target_x, source_x, np.log10(y_arr))
    else:
        new_y = np.interp(target_x, source_x, y_arr)
    valid = np.isfinite(new_x) & np.isfinite(new_y)
    return new_x[valid], new_y[valid]


def _interpolate_linear_x_curve(
    x_data: np.ndarray,
    y_data: np.ndarray,
    resolution: float,
    *,
    duration_s: float | None = None,
    point_count: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    x_arr, y_arr = _finite_aligned_xy(x_data, y_data)
    if x_arr.size < 2 or not np.isfinite(resolution) or resolution <= 0.0:
        return np.array([], dtype=float), np.array([], dtype=float)
    order = np.argsort(x_arr)
    x_arr = x_arr[order]
    y_arr = y_arr[order]
    x_arr, unique_indices = np.unique(x_arr, return_index=True)
    y_arr = y_arr[unique_indices]
    if x_arr.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    start = float(x_arr[0])
    stop = float(x_arr[-1])
    if duration_s is not None:
        try:
            duration = float(duration_s)
        except (TypeError, ValueError):
            duration = np.nan
        if not np.isfinite(duration) or duration <= 0.0:
            return np.array([], dtype=float), np.array([], dtype=float)
        stop = start + duration
    if stop <= start:
        return np.array([], dtype=float), np.array([], dtype=float)
    if point_count is not None:
        try:
            count = int(point_count)
        except (TypeError, ValueError):
            count = 0
        if count < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        new_x = np.linspace(start, stop, count, dtype=float)
        new_x = np.round(new_x, 12)
        new_y = np.interp(new_x, x_arr, y_arr)
        valid = np.isfinite(new_x) & np.isfinite(new_y)
        return new_x[valid], new_y[valid]
    step = float(resolution)
    tolerance = max(step, 1.0) * 1e-10
    start_index = int(np.ceil((start - tolerance) / step))
    stop_index = int(np.floor((stop + tolerance) / step))
    if stop_index < start_index:
        return np.array([], dtype=float), np.array([], dtype=float)
    new_x = np.arange(start_index, stop_index + 1, dtype=float) * step
    new_x = np.round(new_x, 12)
    new_x = np.unique(new_x[(new_x >= start - tolerance) & (new_x <= stop + tolerance)])
    new_y = np.interp(new_x, x_arr, y_arr)
    valid = np.isfinite(new_x) & np.isfinite(new_y)
    return new_x[valid], new_y[valid]


def _ranges_close(
    left: tuple[tuple[float, float], tuple[float, float]],
    right: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    return bool(np.allclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float)))


def _parse_optional_float(text: str) -> float | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        value = float(stripped)
    except ValueError:
        return None
    return value if np.isfinite(value) else None


def _optional_number_text(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:g}" if np.isfinite(number) else ""


def _stable_seed_from_parts(*parts: object) -> int:
    payload = repr(parts).encode("utf-8", errors="replace")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def _parse_channel_list(text: str) -> list[int]:
    channels: list[int] = []
    for token in str(text or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value > 0 and value not in channels:
            channels.append(value)
    return channels


def _parse_positive_int(text: str, default: int) -> int:
    try:
        value = int(str(text or "").strip())
    except ValueError:
        return int(default)
    return max(1, value)


def _series_display_file_name(name: str | Path) -> str:
    text = str(name or "")
    if not text:
        return ""
    path = Path(text)
    display = path.name or text
    if path.suffix.lower() == ".vna":
        return path.stem
    return display


def _inline_condition_text(text: str, *, max_chars: int = 40) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) > max_chars:
        return f"{compact[:max_chars - 1]}..."
    return compact


def _default_series_label(dataset: AnalysisDataset, series: AnalysisSeries) -> str:
    return f"{_series_display_file_name(dataset.name)}+ch{series.channel_index + 1}"


def _strip_vna_suffix_in_series_label(label: str) -> str:
    text = str(label or "")
    marker = ".vna+ch"
    lower = text.lower()
    index = lower.find(marker)
    if index < 0:
        return text
    suffix = text[index + len(".vna") :]
    if suffix.lower().startswith("+ch"):
        return text[:index] + suffix
    return text


def _append_series_scale_suffix(label: str, scale_ratio: float) -> str:
    base = _strip_series_scale_suffix(label)
    try:
        factor = float(scale_ratio)
    except (TypeError, ValueError):
        return base
    if not np.isfinite(factor) or np.isclose(factor, 1.0, rtol=1e-12, atol=1e-12):
        return base
    return f"{base} (*{factor:g})"


def _safe_scale_ratio(scale: float, original_scale: float) -> float:
    try:
        numerator = float(scale)
        denominator = float(original_scale)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(numerator) or not np.isfinite(denominator):
        return 1.0
    if np.isclose(denominator, 0.0, rtol=0.0, atol=1e-20):
        return numerator
    return numerator / denominator


def _vc_reference_frequency_velocity(name: str) -> tuple[np.ndarray, np.ndarray]:
    label = str(name)
    if label not in VC_REFERENCE_LEVELS_UM_S:
        return np.array([], dtype=float), np.array([], dtype=float)
    start_hz = 4.0 if label in {"VC A", "VC B"} else 1.0
    max_hz = 1000.0
    centers, _lower_edges, _upper_edges = third_octave_bands(start_hz, 1125.0)
    if centers.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    centers = np.asarray(centers, dtype=float)
    keep = np.isfinite(centers) & (centers >= start_hz * 0.999) & (centers <= max_hz * 1.001)
    centers = centers[keep]
    level = float(VC_REFERENCE_LEVELS_UM_S[label])
    if label in {"VC A", "VC B"}:
        velocity = np.where(centers < 8.0, level * 8.0 / np.maximum(centers, 1e-20), level)
    else:
        velocity = np.full_like(centers, level, dtype=float)
    return centers, velocity


def _vc_reference_acceleration_psd(name: str) -> tuple[np.ndarray, np.ndarray]:
    frequencies, velocity_um_s = _vc_reference_frequency_velocity(name)
    if frequencies.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    centers, lower_edges, upper_edges = third_octave_bands(
        float(np.min(frequencies)),
        float(np.max(frequencies)),
    )
    bandwidths = np.empty_like(frequencies, dtype=float)
    for index, center in enumerate(frequencies):
        nearest = int(np.argmin(np.abs(centers - center))) if centers.size else -1
        if nearest >= 0:
            bandwidths[index] = max(float(upper_edges[nearest] - lower_edges[nearest]), 1e-20)
        else:
            bandwidths[index] = max(float(center) / 4.0, 1e-20)
    velocity_psd_si = (velocity_um_s / 1e6) ** 2 / bandwidths
    acceleration_psd = velocity_psd_si * (2.0 * np.pi * frequencies) ** 2
    valid = np.isfinite(frequencies) & np.isfinite(acceleration_psd) & (frequencies > 0.0) & (acceleration_psd > 0.0)
    return frequencies[valid], acceleration_psd[valid]


def _vc_reference_acceleration_psd_for_transfer_grid(
    name: str,
    transfer_frequency: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    center_frequencies, center_velocity_um_s = _vc_reference_frequency_velocity(name)
    band_edges = _vc_reference_band_edges(name)
    if center_frequencies.size < 2 or band_edges is None:
        return None
    lower_edges, upper_edges = band_edges
    transfer_f = np.asarray(transfer_frequency, dtype=float).ravel()
    transfer_f = transfer_f[np.isfinite(transfer_f) & (transfer_f > 0.0)]
    output_frequencies: list[np.ndarray] = []
    output_psd: list[np.ndarray] = []
    for center, velocity_um_s, low, high in zip(
        center_frequencies,
        center_velocity_um_s,
        lower_edges,
        upper_edges,
    ):
        if not (
            np.isfinite(center)
            and np.isfinite(velocity_um_s)
            and np.isfinite(low)
            and np.isfinite(high)
            and center > 0.0
            and velocity_um_s > 0.0
            and high > low
        ):
            continue
        in_band_transfer = transfer_f[(transfer_f > low) & (transfer_f < high)]
        band_points = np.unique(np.concatenate((
            np.array([low, center, high], dtype=float),
            in_band_transfer,
            log_frequency_grid(float(low), float(high), points=9),
        )))
        band_points = band_points[np.isfinite(band_points) & (band_points > 0.0)]
        if band_points.size < 2:
            continue
        velocity_psd_si = (float(velocity_um_s) / 1e6) ** 2 / max(float(high - low), 1e-20)
        acceleration_psd = velocity_psd_si * (2.0 * np.pi * band_points) ** 2
        output_frequencies.append(band_points)
        output_psd.append(acceleration_psd)
    if not output_frequencies:
        return None
    frequencies = np.concatenate(output_frequencies)
    psd_values = np.concatenate(output_psd)
    order = np.argsort(frequencies)
    frequencies = frequencies[order]
    psd_values = psd_values[order]
    unique_frequencies, unique_indices = np.unique(frequencies, return_index=True)
    psd_values = psd_values[unique_indices]
    valid = np.isfinite(unique_frequencies) & np.isfinite(psd_values) & (unique_frequencies > 0.0) & (psd_values > 0.0)
    if np.count_nonzero(valid) < 2:
        return None
    return unique_frequencies[valid], psd_values[valid]


def _vc_reference_band_edges(name: str) -> tuple[np.ndarray, np.ndarray] | None:
    frequencies, _velocity_um_s = _vc_reference_frequency_velocity(name)
    if frequencies.size < 2:
        return None
    centers, lower_edges, upper_edges = third_octave_bands(
        float(np.min(frequencies)),
        float(np.max(frequencies)),
    )
    if centers.size == 0:
        return None
    lower = np.empty_like(frequencies, dtype=float)
    upper = np.empty_like(frequencies, dtype=float)
    for index, center in enumerate(frequencies):
        nearest = int(np.argmin(np.abs(centers - center)))
        lower[index] = float(lower_edges[nearest])
        upper[index] = float(upper_edges[nearest])
    valid = np.isfinite(lower) & np.isfinite(upper) & (lower > 0.0) & (upper > lower)
    if not np.all(valid):
        return None
    return lower, upper


def _vc_band_edges_for_frequencies(
    name: str,
    frequencies: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    band_edges = _vc_reference_band_edges(name)
    centers, _velocity_um_s = _vc_reference_frequency_velocity(name)
    if band_edges is None or centers.size < 2:
        return None
    center_lower, center_upper = band_edges
    f = np.asarray(frequencies, dtype=float).ravel()
    lower = np.empty_like(f, dtype=float)
    upper = np.empty_like(f, dtype=float)
    for index, frequency in enumerate(f):
        matches = np.where((frequency >= center_lower) & (frequency <= center_upper))[0]
        if matches.size:
            nearest = int(matches[np.argmin(np.abs(centers[matches] - frequency))])
        else:
            nearest = int(np.argmin(np.abs(centers - frequency)))
        lower[index] = float(center_lower[nearest])
        upper[index] = float(center_upper[nearest])
    valid = np.isfinite(f) & np.isfinite(lower) & np.isfinite(upper) & (f > 0.0) & (upper > lower)
    if not np.all(valid):
        return None
    return lower, upper


def _filter_band_edges(
    band_edges: tuple[np.ndarray, np.ndarray] | None,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    if band_edges is None:
        return None
    lower, upper = band_edges
    mask = np.asarray(mask, dtype=bool).ravel()
    lower = np.asarray(lower, dtype=float).ravel()
    upper = np.asarray(upper, dtype=float).ravel()
    count = min(mask.size, lower.size, upper.size)
    if count == 0:
        return None
    return lower[:count][mask[:count]], upper[:count][mask[:count]]


def _third_octave_velocity_from_center_psd(
    frequencies: np.ndarray,
    acceleration_psd: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(frequencies, dtype=float).ravel()
    psd = np.asarray(acceleration_psd, dtype=float).ravel()
    count = min(f.size, psd.size)
    f = f[:count]
    psd = psd[:count]
    valid = np.isfinite(f) & np.isfinite(psd) & (f > 0.0) & (psd > 0.0)
    f = f[valid]
    psd = psd[valid]
    if f.size < 2:
        return np.array([], dtype=float), np.array([], dtype=float)
    centers, lower_edges, upper_edges = third_octave_bands(float(np.min(f)), float(np.max(f)))
    values = np.full_like(f, np.nan, dtype=float)
    for index, frequency in enumerate(f):
        nearest = int(np.argmin(np.abs(centers - frequency))) if centers.size else -1
        if nearest < 0:
            continue
        bandwidth = max(float(upper_edges[nearest] - lower_edges[nearest]), 1e-20)
        values[index] = np.sqrt(psd[index] / (2.0 * np.pi * frequency) ** 2 * bandwidth) * 1e6
    valid = np.isfinite(values) & (values > 0.0)
    return f[valid], values[valid]


def _append_inline_factor_suffix(label: str, factor: float) -> str:
    try:
        value = float(factor)
    except (TypeError, ValueError):
        return str(label)
    if not np.isfinite(value) or np.isclose(value, 1.0, rtol=1e-12, atol=1e-12):
        return str(label)
    return f"{label} (x{value:g})"


def _strip_series_scale_suffix(label: str) -> str:
    text = str(label or "").strip()
    for marker in (" (*", " (放大", " (缩小到", " (系数"):
        index = text.rfind(marker)
        if index >= 0 and text.endswith(")"):
            return text[:index].strip()
    return text


def _safe_header_part(text: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in str(text or "trace"))
    return safe.strip("_") or "trace"


def _safe_filename_part(text: str) -> str:
    safe = _safe_header_part(text).lower()
    return safe[:48] or "plot"


def _supported_files_in_folder(folder: Path) -> list[Path]:
    suffixes = {".vna", ".mat", ".txt", ".csv", ".dat"}
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in suffixes
    ]


def _foundation_channel_name(channel_number: int, fallback: str) -> str:
    if channel_number == 2:
        return "X"
    if channel_number == 3:
        return "Y"
    if channel_number == 4:
        return "Z"
    return fallback or f"Ch {channel_number}"


def _infer_rbw(frequencies: np.ndarray) -> float:
    values = np.asarray(frequencies, dtype=float).ravel()
    if values.size < 2:
        return 1.0
    diffs = np.diff(values)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0.0)]
    if diffs.size == 0:
        return 1.0
    return float(np.median(diffs))
