from __future__ import annotations

from pathlib import Path

import numpy as np

from python_vna.analysis_derivation import (
    DERIVE_BASE_TO_TOP,
    DERIVE_TOP_TO_BASE,
    derive_psd_from_transfer,
    derive_time_from_transfer,
    has_complex_transfer_phase,
)
from python_vna.analysis_algorithms import (
    FilterConfig,
    apply_filter_to_signal,
    apply_time_window,
    compute_cumulative_spectrum,
    compute_dynamic_stiffness,
    compute_coherence_welch,
    compute_periodogram_psd,
    compute_third_octave_velocity_rms,
    compute_transfer_function_welch,
    compute_welch_psd,
    convert_acceleration_psd,
    convert_acceleration_time_series,
    crop_signal_edges,
    quantity_cumulative_label,
    quantity_psd_label,
    quantity_time_label,
    third_octave_bands,
)
from python_vna.analysis_data import (
    AnalysisDataset,
    AnalysisSeries,
    dataset_from_measurement,
    load_continuous_channels,
    load_analysis_path,
)
from python_vna.display_transforms import transform_legacy_autospectrum
from python_vna.optional import require
from python_vna.ui.main_window import (
    DataTipPoint,
    DataTipText,
    VnaAxisItem,
    VnaViewBox,
    _apply_text_item_style,
    _cursor_palette_for_background,
    _data_tip_anchor_for_label_drag,
)

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
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
VC_REFERENCE_COLORS = {
    "VC A": "#202020",
    "VC B": "#1f5fbf",
    "VC C": "#d12f2f",
    "VC D": "#228b3c",
    "VC E": "#8a5a00",
    "VC F": "#6f42c1",
}


class AnalysisViewer(QtWidgets.QMainWindow):
    def __init__(
        self,
        parent=None,
        *,
        theme: dict[str, object] | None = None,
        current_measurement_provider=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Analysis Viewer")
        self.resize(980, 720)
        self._datasets: list[AnalysisDataset] = []
        self._next_dataset_id = 1
        self._current_measurement_provider = current_measurement_provider
        self._time_series_cache: dict[tuple[int, str, float | None, float | None, int | None], tuple[np.ndarray, np.ndarray]] = {}
        self._bulk_time_series_cache: dict[tuple[int, float | None, float | None, int | None], tuple[np.ndarray, dict[str, np.ndarray]]] = {}
        self._selected_channel_keys_by_dataset: dict[int, set[str]] = {}
        self._theme = dict(theme or {})
        self._last_directory = Path.cwd()
        self._suspend_auto_plot = False
        self._plot_curves: dict[pg.PlotWidget, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
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
        self._series_labels: dict[str, str] = {}
        self._custom_series_labels: dict[tuple[int, int], str] = {}
        self._custom_series_scales: dict[tuple[int, int], float] = {}
        self._original_series_scales: dict[tuple[int, int], float] = {}
        self._current_measurement_dataset_id: int | None = None
        self._derived_result_cache: dict[tuple[object, ...], tuple[object, ...]] = {}
        self._single_plot_windows: list[QtWidgets.QDialog] = []
        self._build_ui()
        self.apply_theme(self._theme)

    def apply_theme(self, theme: dict[str, object] | None) -> None:
        if theme:
            self._theme = dict(theme)
        if not self._theme:
            self._theme = {
                "window_bg": "#f4f7fb",
                "panel_bg": "#ffffff",
                "panel_bg_alt": "#edf3fa",
                "cell_bg": "#e5eef8",
                "plot_bg": "#ffffff",
                "text": "#102033",
                "muted_text": "#395268",
                "label_text": "#17324d",
                "axis": "#172033",
                "accent": "#1d72c9",
                "accent_alt": "#0f9f8f",
                "border": "#b8c6d8",
                "control_border": "#95a9bf",
                "table_bg": "#ffffff",
                "menu_bg": "#f7fbff",
                "grid_alpha": 0.22,
            }
        theme = self._theme
        stylesheet = self._theme_stylesheet(theme)
        self.setStyleSheet(stylesheet)
        for dialog in list(self._single_plot_windows):
            dialog.setStyleSheet(stylesheet)
            for plot in dialog.findChildren(pg.PlotWidget):
                self._apply_plot_theme(plot)
        for plot in self.findChildren(pg.PlotWidget):
            self._apply_plot_theme(plot)

    @staticmethod
    def _theme_stylesheet(theme: dict[str, object]) -> str:
        return f"""
            QMainWindow, QWidget {{
                background: {theme.get('window_bg')};
                color: {theme.get('text')};
                font-size: 8pt;
            }}
            QGroupBox {{
                background: {theme.get('panel_bg')};
                color: {theme.get('label_text')};
                border: 1px solid {theme.get('border')};
                border-radius: 8px;
                margin-top: 12px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QLabel, QCheckBox {{
                color: {theme.get('text')};
            }}
            QPushButton, QToolButton {{
                background: {theme.get('accent')};
                color: white;
                border: 1px solid {theme.get('accent')};
                border-radius: 6px;
                padding: 2px 6px;
                font-weight: bold;
            }}
            QPushButton:hover, QToolButton:hover {{
                background: {theme.get('accent_alt')};
                border-color: {theme.get('accent_alt')};
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background: {theme.get('label_text')};
                border-color: {theme.get('label_text')};
                padding-top: 3px;
                padding-left: 7px;
            }}
            QPushButton:checked, QToolButton:checked {{
                background: {theme.get('accent_alt')};
                border: 2px solid {theme.get('label_text')};
                color: white;
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background: {theme.get('panel_bg_alt')};
                color: {theme.get('muted_text')};
                border-color: {theme.get('border')};
            }}
            QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
                background: {theme.get('panel_bg_alt')};
                color: {theme.get('text')};
                border: 1px solid {theme.get('control_border')};
                border-radius: 5px;
                padding: 1px 4px;
                min-height: 18px;
            }}
            QDoubleSpinBox::up-button, QSpinBox::up-button {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 16px;
                min-height: 9px;
                border-left: 1px solid {theme.get('control_border')};
                border-bottom: 1px solid {theme.get('control_border')};
                border-top-right-radius: 4px;
            }}
            QDoubleSpinBox::down-button, QSpinBox::down-button {{
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 16px;
                min-height: 9px;
                border-left: 1px solid {theme.get('control_border')};
                border-bottom-right-radius: 4px;
            }}
            QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
                width: 7px;
                height: 7px;
            }}
            QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
                width: 7px;
                height: 7px;
            }}
            QListWidget {{
                background: {theme.get('table_bg')};
                color: {theme.get('text')};
                border: 1px solid {theme.get('border')};
                border-radius: 7px;
            }}
            QTabWidget::pane {{
                border: 1px solid {theme.get('border')};
                background: {theme.get('panel_bg')};
            }}
            QTabBar::tab {{
                background: {theme.get('panel_bg_alt')};
                color: {theme.get('text')};
                padding: 4px 10px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background: {theme.get('accent')};
                color: white;
            }}
            QStatusBar {{
                background: {theme.get('menu_bg')};
                color: {theme.get('label_text')};
            }}
            QMenu {{
                background: {theme.get('menu_bg')};
                color: {theme.get('text')};
                border: 1px solid {theme.get('border')};
                border-radius: 7px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 4px 18px;
                border-radius: 5px;
            }}
            QMenu::item:selected {{
                background: {theme.get('accent')};
                color: #ffffff;
            }}
            QCheckBox#vcCheck {{
                spacing: 7px;
                font-weight: bold;
            }}
            QCheckBox#vcCheck::indicator {{
                width: 15px;
                height: 15px;
                border: 2px solid {theme.get('accent')};
                border-radius: 3px;
                background: {theme.get('panel_bg')};
            }}
            QCheckBox#vcCheck::indicator:checked {{
                background: {theme.get('accent_alt')};
                border-color: {theme.get('accent_alt')};
            }}
            QCheckBox#vcCheck::indicator:unchecked {{
                background: {theme.get('panel_bg')};
                border-color: {theme.get('control_border')};
            }}
            """

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.left_panel = QtWidgets.QWidget()
        self.left_panel.setMinimumWidth(230)
        self.left_panel.setMaximumWidth(285)
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)
        left_layout.addWidget(self._build_load_group())
        left_layout.addWidget(self._build_series_group(), 4)
        left_layout.addWidget(self._build_controls_group())
        layout.addWidget(self.left_panel)

        self.tabs = QtWidgets.QTabWidget()
        self.main_tab = QtWidgets.QWidget()
        self.foundation_tab = QtWidgets.QWidget()
        self.derived_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.main_tab, "主界面")
        self.tabs.addTab(self.foundation_tab, "地面振动")
        self.tabs.addTab(self.derived_tab, "换算")
        layout.addWidget(self.tabs, 1)
        self._build_main_tab()
        self._build_foundation_tab()
        self._build_derived_tab()
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
        self._active_trace[plot] = None
        if self._active_plot is None:
            self._active_plot = plot
        self._data_tip_items[plot] = []
        self._cursor_items[plot] = self._create_cursor_items(plot)
        self._cursor_positions[plot] = None
        self._axis_history[plot] = []
        self._log_modes[plot] = (False, False)
        self._plot_export_excluded[plot] = set()
        return plot

    def _build_load_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("[-] 数据")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
        self.load_file_button = QtWidgets.QPushButton("加载文件")
        self.load_folder_button = QtWidgets.QPushButton("加载文件夹")
        self.clear_button = QtWidgets.QPushButton("删除所选")
        self.fs_hint_spin = QtWidgets.QDoubleSpinBox()
        self.fs_hint_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.fs_hint_spin.setRange(16.0, 1_048_576.0)
        self.fs_hint_spin.setDecimals(0)
        self.fs_hint_spin.setSingleStep(256.0)
        self.fs_hint_spin.setValue(4096.0)
        layout.addWidget(self.load_file_button, 0, 0)
        layout.addWidget(self.load_folder_button, 0, 1)
        layout.addWidget(self.clear_button, 0, 2)
        layout.addWidget(QtWidgets.QLabel("FFT块长"), 1, 0)
        layout.addWidget(self.fs_hint_spin, 1, 1, 1, 2)
        self.load_file_button.clicked.connect(self._load_file)
        self.load_folder_button.clicked.connect(self._load_folder)
        self.clear_button.clicked.connect(self._delete_selected_datasets)
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
        self.series_list.setMinimumHeight(260)
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
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.select_none_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons, 2, 0, 1, 4)
        self.series_list.itemSelectionChanged.connect(self._on_series_selection_changed)
        self.rename_edit.editingFinished.connect(self._rename_selected_series_from_editor)
        self.factor_edit.editingFinished.connect(self._set_selected_series_scale_from_editor)
        self.select_all_button.clicked.connect(self._select_all_series)
        self.select_none_button.clicked.connect(self._select_no_series)
        self.refresh_button.clicked.connect(self.refresh_data_sources)
        return group

    def _build_controls_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("[+] 主处理")
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
        self.quantity_combo.addItems(["Acceleration", "Velocity", "Displacement"])
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
        self.plot_button = QtWidgets.QPushButton("绘图")
        self.hold_button = QtWidgets.QPushButton("保持:关")
        self.hold_button.setCheckable(True)
        self.clear_plots_button = QtWidgets.QPushButton("清空图像")
        self.export_button = QtWidgets.QPushButton("导出数据")

        layout.addWidget(QtWidgets.QLabel("起始时间"), 0, 0)
        layout.addWidget(self.time_start_edit, 0, 1)
        layout.addWidget(QtWidgets.QLabel("结束时间"), 0, 2)
        layout.addWidget(self.time_end_edit, 0, 3)
        layout.addWidget(QtWidgets.QLabel("PSD 来源"), 1, 0)
        layout.addWidget(self.psd_source_combo, 1, 1, 1, 3)
        layout.addWidget(QtWidgets.QLabel("物理量"), 2, 0)
        layout.addWidget(self.quantity_combo, 2, 1, 1, 3)
        layout.addWidget(QtWidgets.QLabel("主图倍率"), 3, 0)
        layout.addWidget(self.scale_spin, 3, 1, 1, 3)
        layout.addWidget(self.lowpass_check, 4, 0)
        layout.addWidget(self.lowpass_spin, 4, 1, 1, 3)
        layout.addWidget(self.highpass_check, 5, 0)
        layout.addWidget(self.highpass_spin, 5, 1, 1, 3)
        layout.addWidget(self.detrend_check, 6, 0)
        layout.addWidget(QtWidgets.QLabel("Order"), 6, 1)
        layout.addWidget(self.filter_order_spin, 6, 2, 1, 2)
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
            export_button = QtWidgets.QPushButton("导出数据")
            open_button.clicked.connect(lambda _checked=False, i=index: self._open_plot_window_for_plot(self.main_plots[i]))
            export_button.clicked.connect(lambda _checked=False, i=index: self._export_plot_csv(self.main_plots[i]))
            self.main_open_buttons.append(open_button)
            self.main_export_buttons.append(export_button)
            row.addWidget(open_button)
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
        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(4)
        vc_row = QtWidgets.QHBoxLayout()
        vc_row.setSpacing(6)
        self.foundation_vib_file_combo = QtWidgets.QComboBox()
        self.foundation_stiff_file_combo = QtWidgets.QComboBox()
        self.foundation_vib_edit = QtWidgets.QLineEdit("2,3,4")
        self.foundation_resp_edit = QtWidgets.QLineEdit("4")
        self.foundation_vib_edit.setMinimumWidth(82)
        self.foundation_vib_edit.setMaximumWidth(98)
        self.foundation_resp_edit.setMinimumWidth(42)
        self.foundation_resp_edit.setMaximumWidth(58)
        for combo in (self.foundation_vib_file_combo, self.foundation_stiff_file_combo):
            combo.setMinimumWidth(180)
            combo.setMaximumWidth(300)
            combo.setMinimumContentsLength(22)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        file_row.addWidget(QtWidgets.QLabel("振动文件"))
        file_row.addWidget(self.foundation_vib_file_combo)
        file_row.addWidget(QtWidgets.QLabel("动刚度文件"))
        file_row.addWidget(self.foundation_stiff_file_combo)
        file_row.addWidget(QtWidgets.QLabel("Vib Ch"))
        file_row.addWidget(self.foundation_vib_edit)
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
        file_row.addWidget(QtWidgets.QLabel("Stiff Ch"))
        file_row.addWidget(self.foundation_resp_edit)
        file_row.addStretch(1)
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
        controls.addLayout(file_row)
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

        controls = QtWidgets.QGroupBox("传递率换算")
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
        self.derived_regularization_spin.setValue(1e-6)
        self.derived_plot_button = QtWidgets.QPushButton("换算绘图")
        self.derived_show_source_check = QtWidgets.QCheckBox("绘制待换算数据")
        self.derived_show_source_check.setObjectName("vcCheck")
        self.derived_show_source_check.setChecked(False)
        self.derived_coherence_correction_check = QtWidgets.QCheckBox("相干修正")
        self.derived_coherence_correction_check.setObjectName("vcCheck")
        self.derived_coherence_correction_check.setToolTip(
            "使用传递率对应的相干性修正 PSD：正向除以 coh，反向乘以 coh；低相干频点按下限保护。"
        )
        self.derived_coherence_correction_check.setChecked(False)
        self.derived_vc_checks: dict[str, QtWidgets.QCheckBox] = {}

        for combo in (self.derived_transfer_combo, self.derived_input_series_combo):
            combo.setMinimumWidth(210)
            combo.setMaximumWidth(16777215)
            combo.setMinimumContentsLength(16)
            combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.derived_direction_combo.setMaximumWidth(126)
        self.derived_regularization_spin.setMaximumWidth(138)
        self.derived_plot_button.setMaximumWidth(130)

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
        vc_row = QtWidgets.QHBoxLayout()
        vc_row.setSpacing(6)
        vc_row.addWidget(QtWidgets.QLabel("VC参考线"))
        for name in VC_REFERENCE_NAMES:
            checkbox = QtWidgets.QCheckBox(name)
            checkbox.setObjectName("vcCheck")
            checkbox.setChecked(False)
            checkbox.toggled.connect(lambda _checked: self._auto_plot_derived_from_control_change())
            self.derived_vc_checks[name] = checkbox
            vc_row.addWidget(checkbox)
        vc_row.addSpacing(14)
        vc_row.addWidget(self.derived_coherence_correction_check)
        vc_row.addWidget(self.derived_show_source_check)
        vc_row.addStretch(1)
        control_layout.addLayout(vc_row)
        layout.addWidget(controls)

        self.derived_plots: list[pg.PlotWidget] = []
        self.derived_open_buttons: list[QtWidgets.QPushButton] = []
        self.derived_export_buttons: list[QtWidgets.QPushButton] = []
        self.derived_result_mode_combo = QtWidgets.QComboBox()
        self.derived_result_mode_combo.addItems(["PSD", "CumPSD", "地基振动", "近似时域"])
        self.derived_result_mode_combo.setCurrentText("PSD")
        self.derived_result_mode_combo.currentTextChanged.connect(lambda _text: self._auto_plot_derived_from_control_change())
        for index, title in enumerate(("传递率曲线", "换算图窗")):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QtWidgets.QLabel(title))
            if index > 0:
                row.addWidget(self.derived_result_mode_combo)
            row.addStretch(1)
            open_button = QtWidgets.QPushButton("图窗")
            export_button = QtWidgets.QPushButton("导出数据")
            open_button.clicked.connect(lambda _checked=False, i=index: self._open_plot_window_for_plot(self.derived_plots[i]))
            export_button.clicked.connect(lambda _checked=False, i=index: self._export_plot_csv(self.derived_plots[i]))
            self.derived_open_buttons.append(open_button)
            self.derived_export_buttons.append(export_button)
            row.addWidget(open_button)
            row.addWidget(export_button)
            layout.addLayout(row)
            plot = self._create_plot_widget(title)
            layout.addWidget(plot, 1)
            self.derived_plots.append(plot)

        self.derived_transfer_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_direction_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_input_series_combo.currentIndexChanged.connect(
            lambda _index: self._auto_plot_derived_from_control_change()
        )
        self.derived_show_source_check.toggled.connect(lambda _checked: self._auto_plot_derived_from_control_change())
        self.derived_coherence_correction_check.toggled.connect(
            lambda _checked: self._auto_plot_derived_from_control_change()
        )
        self.derived_transfer_factor_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_input_factor_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_freq_min_edit.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_freq_max_edit.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_regularization_spin.editingFinished.connect(self._auto_plot_derived_from_control_change)
        self.derived_plot_button.clicked.connect(
            lambda _checked=False: self._plot_derived(keep_existing=self._hold_enabled())
        )

    def _load_file(self) -> None:
        paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Load analysis file(s)",
            str(self._last_directory),
            "Data Files (*.vna *.mat *.txt *.csv *.dat);;All Files (*.*)",
        )
        if not paths:
            return
        self._load_paths([Path(path) for path in paths])

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
            self._load_path(folder)
            return
        paths = _supported_files_in_folder(folder)
        if not paths:
            QtWidgets.QMessageBox.warning(self, "Load failed", "Folder does not contain supported analysis files.")
            self.statusBar().showMessage("Load failed: folder has no supported analysis files")
            return
        self._load_paths(paths, quiet_failures=True)

    def _load_path(self, path: Path) -> None:
        self._load_paths([path])

    def _load_paths(self, paths: list[Path], *, quiet_failures: bool = False) -> None:
        loaded: list[str] = []
        failed: list[str] = []
        for path in paths:
            if self._load_one_path(path, quiet=quiet_failures):
                loaded.append(path.name if not path.is_dir() else path.name)
            else:
                failed.append(path.name if not path.is_dir() else path.name)
        if paths:
            self._last_directory = paths[-1] if paths[-1].is_dir() else paths[-1].parent
        if loaded:
            self._derived_result_cache.clear()
        self._refresh_dataset_lists()
        if failed:
            self.statusBar().showMessage(f"Loaded {len(loaded)} file(s), failed {len(failed)}")
        elif loaded:
            self.statusBar().showMessage(f"Loaded {len(loaded)} file(s). Select channel(s) or press Plot to draw.")

    def _load_one_path(self, path: Path, *, quiet: bool = False) -> bool:
        try:
            dataset = load_analysis_path(
                path,
                fs_hint=TEXT_FILE_FS_HINT_HZ,
                dataset_id=self._next_dataset_id,
            )
        except Exception as exc:
            if not quiet:
                QtWidgets.QMessageBox.warning(self, "Load failed", str(exc))
                self.statusBar().showMessage(f"Load failed: {exc}")
            return False
        self._next_dataset_id += 1
        self._datasets.append(dataset)
        return True

    def set_current_measurement_provider(self, provider) -> None:
        self._current_measurement_provider = provider

    def sync_current_measurement(self, measurement, session_config=None) -> bool:
        if measurement is None:
            return False
        if self._current_measurement_dataset_id is not None:
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
        for index in range(self.series_list.count()):
            item = self.series_list.item(index)
            item.setSelected(item.data(QtCore.Qt.UserRole) in selected_ids)
        self._auto_plot_from_control_change()
        if failed:
            self.statusBar().showMessage(f"Refreshed data with {len(failed)} warning(s)")
        else:
            self.statusBar().showMessage("Refreshed analysis data")

    def _clear_datasets(self) -> None:
        self._datasets.clear()
        self._series_labels.clear()
        self._custom_series_labels.clear()
        self._custom_series_scales.clear()
        self._original_series_scales.clear()
        self._derived_result_cache.clear()
        self._current_measurement_dataset_id = None
        self._refresh_dataset_lists()
        for plot in self._all_analysis_plots():
            plot.clear()
        self.statusBar().showMessage("Analysis data cleared")

    def _delete_selected_datasets(self) -> None:
        selected_series_ids = {
            item.data(QtCore.Qt.UserRole)
            for item in self.series_list.selectedItems()
        }
        selected_dataset_ids = {
            dataset.id
            for dataset in self._datasets
            for series in dataset.series
            if series.id in selected_series_ids
        }
        if not selected_dataset_ids:
            self.statusBar().showMessage("No selected data to delete")
            return
        self._datasets = [dataset for dataset in self._datasets if dataset.id not in selected_dataset_ids]
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
        self._derived_result_cache.clear()
        if self._current_measurement_dataset_id in selected_dataset_ids:
            self._current_measurement_dataset_id = None
        self._refresh_dataset_lists()
        self._clear_plots()
        self.statusBar().showMessage(f"Deleted {len(selected_dataset_ids)} selected dataset(s)")

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
                label = self._series_label(dataset, series)
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole, series.id)
                self.series_list.addItem(item)
                item.setSelected(series.id in selected_ids if selected_ids else False)
        self.series_list.blockSignals(False)
        self._refresh_foundation_file_selectors()
        self._refresh_derived_selectors()
        self._sync_series_editors_from_selection()

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
        self.derived_transfer_combo.blockSignals(True)
        self.derived_input_series_combo.blockSignals(True)
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
        finally:
            self.derived_transfer_combo.blockSignals(False)
            self.derived_input_series_combo.blockSignals(False)

    def _derived_transfer_options(self) -> list[tuple[str, tuple[int, str, str, str, str]]]:
        options: list[tuple[str, tuple[int, str, str, str, str]]] = []
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
            if len(dataset.series) >= 2:
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
        self._auto_plot_from_control_change()

    def _auto_plot_from_control_change(self) -> None:
        if self._suspend_auto_plot:
            return
        if hasattr(self, "derived_plots") and self._has_derived_input_ready():
            self._plot_derived(keep_existing=self._hold_enabled(), quiet=True)
        if not self._datasets or not self.series_list.selectedItems():
            return
        self.plot_current()

    def _auto_plot_derived_from_control_change(self) -> None:
        if self._suspend_auto_plot:
            return
        if not hasattr(self, "derived_plots") or not self._has_derived_input_ready():
            return
        self._plot_derived(keep_existing=self._hold_enabled(), quiet=True)

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
        selected = self._selected_series()
        if not selected:
            self.statusBar().showMessage("No channels selected")
            return
        self._time_series_cache.clear()
        self._bulk_time_series_cache.clear()
        self._selected_channel_keys_by_dataset = {}
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
        self._plot_derived(keep_existing=keep_existing, quiet=True)
        self.statusBar().showMessage(f"Plotted {len(selected)} selected channel(s)")

    def _clear_plots(self) -> None:
        for plot in self._all_analysis_plots():
            self._clear_plot_with_title(plot, "")
        self.statusBar().showMessage("Cleared analysis plots")

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
        log_x = mode in {"PSD", "CumPSD", "Trans", "Coherence"}
        log_y = mode == "PSD"
        plot.setLogMode(x=log_x, y=log_y)
        self._log_modes[plot] = (log_x, log_y)
        plot.showGrid(x=True, y=True, alpha=float(self._theme.get("grid_alpha", 0.25)))
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        x_values_for_range: list[np.ndarray] = []
        y_values_for_range: list[np.ndarray] = []
        status_parts: list[str] = []
        for dataset, series in selected:
            curve = self._curve_for_mode(dataset, series, mode)
            if curve is None:
                continue
            x, y, label = curve
            if x.size < 2 or y.size < 2:
                continue
            x_values_for_range.append(x)
            y_values_for_range.append(y)
            pen = pg.mkPen(TRACE_COLORS[color_index % len(TRACE_COLORS)], width=1.3)
            color_index += 1
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
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

    def _plot_derived(self, *, keep_existing: bool = False, quiet: bool = False) -> None:
        if not hasattr(self, "derived_plots"):
            return
        selected_transfer = self._selected_derived_transfer()
        input_series = self._derived_input_series()
        if selected_transfer is None or not input_series:
            if not quiet:
                self.statusBar().showMessage("换算页缺少传递率曲线或待换算数据")
            for plot, title in zip(self.derived_plots, ("传递率曲线 (no source)", "换算图窗 1 (no source)", "换算图窗 2 (no source)")):
                self._clear_plot_with_title(plot, title)
            return
        transfer_dataset, transfer_key, source_kind, base_series, top_series, transfer_label = selected_transfer
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
        )
        if transfer is None:
            if not quiet:
                self.statusBar().showMessage("无法从所选传递率数据得到 H_top/base")
            for plot in self.derived_plots:
                self._clear_plot_with_title(plot, "No transfer function")
            return
        transfer_f, transfer_h, phase_available = transfer
        direction = str(self.derived_direction_combo.currentData() or DERIVE_BASE_TO_TOP)
        regularization = float(self.derived_regularization_spin.value())
        freq_min = _parse_optional_float(self.derived_freq_min_edit.text())
        freq_max = _parse_optional_float(self.derived_freq_max_edit.text())
        coherence_correction = bool(self.derived_coherence_correction_check.isChecked())
        coherence = self._coherence_for_derived(
            transfer_dataset,
            transfer_key,
            source_kind,
            base_series,
            top_series,
        ) if coherence_correction else None
        coherence_f = coherence[0] if coherence is not None else None
        coherence_values = coherence[1] if coherence is not None else None

        results: list[dict[str, object]] = []
        for input_dataset, series in input_series:
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
        self._plot_derived_results(
            transfer_f,
            transfer_h,
            _append_inline_factor_suffix(transfer_label, transfer_factor),
            results,
            keep_existing=keep_existing,
        )
        if not quiet:
            if results:
                self.statusBar().showMessage(f"换算完成：{len(results)} 条曲线")
            else:
                self.statusBar().showMessage("换算没有得到有效曲线")

    def _plot_derived_results(
        self,
        transfer_f: np.ndarray,
        transfer_h: np.ndarray,
        transfer_label: str,
        results: list[dict[str, object]],
        *,
        keep_existing: bool,
    ) -> None:
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
            self._plot_export_excluded[plot] = set()
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
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
            plot.plot(f, magnitude_db, pen=pg.mkPen(TRACE_COLORS[0], width=1.3), name=plot_label)
            self._plot_curves[plot][plot_label] = (f, magnitude_db)
            self._active_trace[plot] = plot_label
        plot.setTitle("传递率曲线" if f.size >= 2 else "传递率曲线 (no valid data)")
        plot.setLabel("bottom", "Frequency (Hz)")
        plot.setLabel("left", "Trans (dB)")
        self._auto_range_plot(plot, [f], [magnitude_db], log_x=True, log_y=False)

    def _plot_derived_result_axis(
        self,
        plot: pg.PlotWidget,
        mode: str,
        results: list[dict[str, object]],
        *,
        keep_existing: bool,
    ) -> None:
        specs = {
            "PSD": ("PSD", "psd", "Frequency (Hz)", quantity_psd_label(self.quantity_combo.currentText()), True, True),
            "CumPSD": (
                "CumPSD",
                "cumulative",
                "Frequency (Hz)",
                quantity_cumulative_label(self.quantity_combo.currentText()),
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
            "近似时域": ("近似时域", "time", "Time (s)", quantity_time_label(self.quantity_combo.currentText()), False, False),
        }
        title, curve_key, x_label, y_label, log_x, log_y = specs.get(str(mode), specs["PSD"])
        if not keep_existing:
            plot.clear()
            if plot.plotItem.legend is not None:
                plot.plotItem.legend.clear()
            plot.addLegend(offset=(4, 2))
            self._plot_curves[plot] = {}
            self._plot_export_excluded[plot] = set()
            self._active_trace[plot] = None
            self._data_tip_items[plot].clear()
            self._readd_cursor_items(plot)
        elif plot.plotItem.legend is None:
            plot.addLegend(offset=(4, 2))
        self._apply_plot_theme(plot)
        plot.setLogMode(x=log_x, y=log_y)
        self._log_modes[plot] = (log_x, log_y)
        plot.setLabel("bottom", x_label)
        plot.setLabel("left", y_label)
        x_ranges: list[np.ndarray] = []
        y_ranges: list[np.ndarray] = []
        color_index = len(self._plot_curves.get(plot, {})) if keep_existing else 0
        for result in results:
            curve = result.get(curve_key)
            if not curve:
                continue
            x, y = curve
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            if x.size < 2 or y.size < 2:
                continue
            label = str(result.get("label", "derived"))
            plot_label = self._unique_plot_label(plot, label) if keep_existing else label
            plot.plot(
                x,
                y,
                pen=pg.mkPen(TRACE_COLORS[color_index % len(TRACE_COLORS)], width=1.3),
                name=plot_label,
            )
            self._plot_curves[plot][plot_label] = (x, y)
            if self._active_trace[plot] is None:
                self._active_trace[plot] = plot_label
            color_index += 1
            x_ranges.append(x)
            y_ranges.append(y)
            if self.derived_show_source_check.isChecked():
                source_curve = result.get(f"source_{curve_key}")
                if source_curve:
                    sx, sy = source_curve
                    sx = np.asarray(sx, dtype=float)
                    sy = np.asarray(sy, dtype=float)
                    if sx.size >= 2 and sy.size >= 2:
                        source_label = str(result.get("source_label", "待换算数据"))
                        source_plot_label = self._unique_plot_label(plot, source_label) if keep_existing else source_label
                        plot.plot(
                            sx,
                            sy,
                            pen=pg.mkPen(
                                TRACE_COLORS[color_index % len(TRACE_COLORS)],
                                width=1.15,
                                style=QtCore.Qt.DashLine,
                            ),
                            name=source_plot_label,
                        )
                        self._plot_curves[plot][source_plot_label] = (sx, sy)
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
        transfer_dataset: AnalysisDataset,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
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
        f_source_accel, psd_source_accel = _vc_reference_acceleration_psd(name)
        if f_source_accel.size < 2:
            return None
        psd_source_accel = psd_source_accel * float(input_factor) ** 2
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
        if f_source_accel.size and freq_max is not None:
            keep = f_source_accel <= float(freq_max)
            f_source_accel = f_source_accel[keep]
            psd_source_accel = psd_source_accel[keep]
        if f_accel.size < 2:
            return None

        destination = "顶部估算" if direction == DERIVE_BASE_TO_TOP else "地基估算"
        label = _append_inline_factor_suffix(f"{name} -> {destination}", input_factor)
        source_label = f"待换算: {_append_inline_factor_suffix(name, input_factor)}"
        result: dict[str, object] = {"label": label, "source_label": source_label}
        f_quantity, psd_quantity = convert_acceleration_psd(
            f_accel,
            psd_accel,
            self.quantity_combo.currentText(),
            highpass_enabled=self.highpass_check.isChecked(),
            highpass_hz=float(self.highpass_spin.value()),
        )
        if f_quantity.size >= 2:
            result["psd"] = (f_quantity, psd_quantity)
            result["cumulative"] = compute_cumulative_spectrum(f_quantity, psd_quantity)
        result["foundation"] = _third_octave_velocity_from_center_psd(f_accel, psd_accel)

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
        transfer_dataset: AnalysisDataset,
        input_dataset: AnalysisDataset,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
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
            t_source, y_source = self._source_time_curve(input_dataset, input_series, input_factor=input_factor)
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
                label,
                source_label,
            )
        if f_accel.size < 2 and t_time.size < 2:
            return None
        result: dict[str, object] = {"label": label, "source_label": source_label}
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
                result["cumulative"] = compute_cumulative_spectrum(f_quantity, psd_quantity)
            rbw = _infer_rbw(f_accel)
            result["foundation"] = compute_third_octave_velocity_rms(f_accel, psd_accel, rbw)
        if f_source_accel.size >= 2:
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
        if t_time.size >= 2:
            converted_time = convert_acceleration_time_series(
                y_time,
                input_dataset.sample_rate,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            count = min(t_time.size, converted_time.size)
            result["time"] = (t_time[:count], converted_time[:count])
        if t_source.size >= 2:
            converted_source_time = convert_acceleration_time_series(
                y_source,
                input_dataset.sample_rate,
                self.quantity_combo.currentText(),
                highpass_enabled=self.highpass_check.isChecked(),
                highpass_hz=float(self.highpass_spin.value()),
            )
            count = min(t_source.size, converted_source_time.size)
            result["source_time"] = (t_source[:count], converted_source_time[:count])
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
        transfer_dataset: AnalysisDataset,
        input_dataset: AnalysisDataset,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
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
            transfer_dataset.id,
            input_dataset.id,
            base_series.channel_key,
            top_series.channel_key,
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
            round(float(base_series.scale or 1.0), 12),
            round(float(top_series.scale or 1.0), 12),
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
        selected_id = self.derived_input_series_combo.currentData()
        if (
            isinstance(selected_id, tuple)
            and len(selected_id) == 2
            and selected_id[0] == "vc_reference"
        ):
            return [(None, str(selected_id[1]))]
        selected: list[tuple[AnalysisDataset | None, AnalysisSeries | str]] = []
        for dataset in self._datasets:
            for series in dataset.series:
                if series.id == selected_id:
                    selected.append((dataset, series))
        return selected

    def _selected_derived_transfer(
        self,
    ) -> tuple[AnalysisDataset, str, str, AnalysisSeries, AnalysisSeries, str] | None:
        data = self.derived_transfer_combo.currentData()
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
        dataset: AnalysisDataset,
        transfer_key: str,
        source_kind: str,
        base_series: AnalysisSeries,
        top_series: AnalysisSeries,
        *,
        transfer_factor: float,
    ) -> tuple[np.ndarray, np.ndarray, bool] | None:
        if source_kind == "stored" and transfer_key in dataset.frf and dataset.frequency_hz is not None:
            frf_values = dataset.frf[transfer_key]
            f = np.asarray(dataset.frequency_hz, dtype=float).ravel()
            h_raw = np.asarray(frf_values).ravel()
            count = min(f.size, h_raw.size)
            if count >= 2:
                eu_ratio = float(top_series.scale or 1.0) / max(float(base_series.scale or 1.0), 1e-20)
                return f[:count], h_raw[:count] * eu_ratio * float(transfer_factor), has_complex_transfer_phase(frf_values)
        return self._transfer_for_derived_from_time_data(
            dataset,
            base_series,
            top_series,
            transfer_factor=transfer_factor,
        )

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
        plot.clear()
        if plot.plotItem.legend is not None:
            plot.plotItem.legend.clear()
        self._plot_curves[plot] = {}
        self._plot_export_excluded[plot] = set()
        self._active_trace[plot] = None
        self._data_tip_items[plot].clear()
        self._readd_cursor_items(plot)
        self._apply_plot_theme(plot)
        plot.setTitle(title)

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
                pen=pg.mkPen(TRACE_COLORS[color_index % len(TRACE_COLORS)], width=1.3),
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
                pen=pg.mkPen(TRACE_COLORS[color_index % len(TRACE_COLORS)], width=1.3),
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
                pen=pg.mkPen(TRACE_COLORS[color_index % len(TRACE_COLORS)], width=1.3),
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

    def _export_current_csv(self) -> None:
        plot = self._active_plot or (self.main_plots[0] if self.main_plots else None)
        self._export_plot_csv(plot)

    def _export_plot_csv(self, plot: pg.PlotWidget | None) -> None:
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
            if label in excluded:
                continue
            x_arr, y_arr = _finite_aligned_xy(x, y)
            if x_arr.size == 0:
                continue
            rows.append((label, x_arr, y_arr))
            max_count = max(max_count, x_arr.size)
        if not rows:
            self.statusBar().showMessage("No exportable active plot data")
            return
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
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
            detached_plot.plot(
                x,
                y,
                pen=pg.mkPen(TRACE_COLORS[index % len(TRACE_COLORS)], width=1.3),
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
                self.rename_edit.setText(self._series_base_label(dataset, series))
                scale = self._custom_series_scales.get(
                    (dataset.id, series.channel_index + 1),
                    float(series.scale or 1.0),
                )
                self.factor_edit.setText(f"{scale:g}")
            else:
                self.rename_edit.setText("")
                self.factor_edit.setText("1")
        finally:
            self.rename_edit.blockSignals(False)
            self.factor_edit.blockSignals(False)

    def _rename_selected_series_from_editor(self) -> None:
        selected = self._selected_series()
        if len(selected) != 1:
            return
        dataset, series = selected[0]
        label = self.rename_edit.text().strip()
        if not label:
            self._sync_series_editors_from_selection()
            return
        self._custom_series_labels[(dataset.id, series.channel_index + 1)] = _strip_series_scale_suffix(label)
        self._derived_result_cache.clear()
        self._refresh_dataset_lists()
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
        self.statusBar().showMessage(f"Updated {self._series_label(dataset, series)} factor to {scale:g}")
        self.plot_current()

    def _apply_plot_theme(self, plot: pg.PlotWidget) -> None:
        theme = self._theme
        plot.setBackground(str(theme.get("plot_bg", "#ffffff")))
        plot.showGrid(x=True, y=True, alpha=float(theme.get("grid_alpha", 0.25)))
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(str(theme.get("axis", "#172033"))))
            axis.setTextPen(pg.mkPen(str(theme.get("axis", "#172033"))))
        if plot.plotItem.legend is not None:
            plot.plotItem.legend.setBrush(pg.mkBrush(str(theme.get("panel_bg", "#ffffff"))))
            plot.plotItem.legend.setPen(pg.mkPen(str(theme.get("border", "#b8c6d8"))))
            plot.plotItem.legend.opts["labelTextColor"] = str(theme.get("text", "#102033"))
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
        self.statusBar().showMessage(f"Data Tip mode {'on' if enabled else 'off'}")

    def _toggle_cursor_readout(self, enabled: bool) -> None:
        self._cursor_enabled = bool(enabled)
        for items in self._cursor_items.values():
            for item in items.values():
                item.setVisible(False)
        self.statusBar().showMessage(f"Cursor Readout {'on' if enabled else 'off'}")

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
        plot.addItem(items["line"], ignoreBounds=True)
        plot.addItem(items["point"])
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
        self.statusBar().showMessage(f"Cursor: x={cursor_x:.4g}, y={cursor_y:.4g}")
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

    def _build_plot_context_menu(self, plot: pg.PlotWidget) -> tuple[QtWidgets.QMenu, dict[str, object]]:
        menu = QtWidgets.QMenu(plot)
        actions: dict[str, object] = {}
        actions["back"] = menu.addAction("Back One Zoom")
        actions["auto"] = menu.addAction("Auto Scale")
        menu.addSeparator()
        actions["data_tip"] = menu.addAction("Data Tip")
        actions["data_tip"].setCheckable(True)
        actions["data_tip"].setChecked(self._data_tip_enabled)
        actions["cursor"] = menu.addAction("Cursor Readout")
        actions["cursor"].setCheckable(True)
        actions["cursor"].setChecked(self._cursor_enabled)
        actions["clear_tips"] = menu.addAction("Clear Data Tips")
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
        self.statusBar().showMessage("Zoomed plot axis range")
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
        self.statusBar().showMessage("Restored previous zoom")
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
        self.statusBar().showMessage("Auto-scaled plot")

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
        self.statusBar().showMessage(f"Data tip: x={tip_x:.4g}, y={tip_y:.4g}")
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
        self.statusBar().showMessage(f"Data tip moved: x={tip_x:.4g}, y={tip_y:.4g}")
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
        self.statusBar().showMessage("Data tip label moved")
        return True

    def _show_data_tip_menu(self, plot: pg.PlotWidget, data_tip: dict[str, object], screen_pos) -> None:
        self._active_plot = plot
        self._suppress_plot_context_menu_once()
        menu = QtWidgets.QMenu(self)
        delete_this = menu.addAction("Delete This Data Tip")
        delete_all = menu.addAction("Delete All Data Tips")
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
        self.statusBar().showMessage("Cleared data tips")

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
    centers, _lower_edges, _upper_edges = third_octave_bands(start_hz, 90.0)
    if centers.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    centers = np.asarray(centers, dtype=float)
    keep = np.isfinite(centers) & (centers >= start_hz * 0.999) & (centers <= 80.0 * 1.001)
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
