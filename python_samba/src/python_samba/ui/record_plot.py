"""Interactive, standalone viewer for completed logging records."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import time
from dataclasses import replace
from typing import Any, Callable

from python_samba.logging_tools.models import LoggingRecord
from python_samba.ui.classic_widgets import FlatPush, GroupPanel
from python_samba.ui.plot_interactions import (
    CURVE_COLORS,
    DataTipPoint,
    DataTipText,
    InteractiveViewBox,
    PLOT_BACKGROUND,
    PLOT_FONT_POINTS,
    PLOT_FOREGROUND,
    PlainAxisItem,
    plot_font as _plot_font,
    short_number as _short_number,
)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc


_ANALYSIS_IMPORT_ERROR: ImportError | None = None
try:
    import numpy as np
    import pyqtgraph as pg

    from python_samba.logging_tools.record_analysis import (
        NumericCurve,
        RecordAnalysisSession,
    )
except ImportError as exc:  # pragma: no cover - exercised by minimal installs
    _ANALYSIS_IMPORT_ERROR = exc
    np = None  # type: ignore[assignment]
    pg = None  # type: ignore[assignment]
    NumericCurve = Any  # type: ignore[misc,assignment]
    RecordAnalysisSession = Any  # type: ignore[misc,assignment]


class RecordPlotWindow(QtWidgets.QDialog):
    """Non-modal curve viewer and analysis workbench for one logging record."""

    open_record_requested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("loggingRecordsWindow")
        self.setWindowTitle("Logging Records / Plot")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(1050, 700)
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().size()
            self.resize(
                min(1560, max(1180, int(available.width() * 0.94))),
                min(1020, max(760, int(available.height() * 0.94))),
            )
        else:
            self.resize(1440, 940)

        self.analysis_session: RecordAnalysisSession | None = None
        self._populating_curves = False
        self._visible_ids: set[str] = set()
        self._frequency_views: dict[str, str] = {}
        self._curve_items: dict[str, Any] = {}
        self._curve_colors: dict[str, str] = {}
        self._display_cache: dict[tuple[Any, ...], tuple[Any, Any, Any]] = {}
        self._zoom_history = {"time": deque(maxlen=5), "frequency": deque(maxlen=5)}
        self._last_saved_range: dict[str, tuple[tuple[float, float], tuple[float, float]] | None] = {
            "time": None,
            "frequency": None,
        }
        self._cursor_state: dict[str, dict[str, Any]] = {}
        self._data_tips: dict[int, dict[str, Any]] = {}
        self._tip_counter = 0
        self._markers: dict[str, dict[str, dict[str, Any]]] = {
            "time": {},
            "frequency": {},
        }
        self._moving_marker = False
        self._tip_menu_suppressed_until = 0.0

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(7)
        root.addLayout(self._build_file_toolbar())

        if _ANALYSIS_IMPORT_ERROR is None:
            root.addLayout(self._build_plot_toolbar())
            root.addWidget(self._build_analysis_workspace(), 1)
        else:  # pragma: no cover - minimal optional-dependency installation
            root.addWidget(self._build_dependency_fallback(), 1)

        self.status_label = QtWidgets.QLabel("Open a logging record to begin.")
        self.status_label.setObjectName("recordPlotStatus")
        self.status_label.setStyleSheet(
            "QLabel#recordPlotStatus { background:#e7f3f9; border:1px solid #a9c7d7;"
            " border-radius:6px; padding:5px 9px; color:#31566c; }"
        )
        root.addWidget(self.status_label)

    def _build_file_toolbar(self) -> QtWidgets.QHBoxLayout:
        toolbar = QtWidgets.QHBoxLayout()
        self.record_path = QtWidgets.QLineEdit()
        self.record_path.setPlaceholderText(
            "CSV/TSV or legacy .LoggRecJson/.LoggRecXml/.ILogRecJson/.ILogRecXml"
        )
        self.btn_record_browse = FlatPush("Open record…")
        self.record_summary = QtWidgets.QLabel("No record loaded")
        self.record_summary.setMinimumWidth(180)
        self.record_summary.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        toolbar.addWidget(self.record_path, 1)
        toolbar.addWidget(self.btn_record_browse)
        toolbar.addWidget(self.record_summary)
        self.btn_record_browse.clicked.connect(self.open_record_requested.emit)
        return toolbar

    def _build_plot_toolbar(self) -> QtWidgets.QHBoxLayout:
        toolbar = QtWidgets.QHBoxLayout()
        self.btn_auto_range = FlatPush("Auto fit")
        self.btn_previous_zoom = FlatPush("Previous zoom")
        self.btn_cursor = FlatPush("Cursor")
        self.btn_cursor.setCheckable(True)
        self.btn_cursor.setChecked(True)
        self.btn_data_tip = FlatPush("Data tip")
        self.btn_data_tip.setCheckable(True)
        self.btn_marker_a = FlatPush("Set A")
        self.btn_marker_b = FlatPush("Set B")
        self.btn_clear_annotations = FlatPush("Clear annotations")
        self.btn_copy_plot = FlatPush("Copy image")
        self.btn_export_curves = FlatPush("Export selected")
        for button in (
            self.btn_auto_range,
            self.btn_previous_zoom,
            self.btn_cursor,
            self.btn_data_tip,
            self.btn_marker_a,
            self.btn_marker_b,
            self.btn_clear_annotations,
            self.btn_copy_plot,
            self.btn_export_curves,
        ):
            button.setMinimumHeight(30)
            toolbar.addWidget(button)
        self.marker_readout = QtWidgets.QLabel("A —   B —   Δ —")
        self.marker_readout.setMinimumWidth(260)
        self.marker_readout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        toolbar.addWidget(self.marker_readout, 1)

        self.btn_auto_range.clicked.connect(self.auto_range)
        self.btn_previous_zoom.clicked.connect(self.previous_zoom)
        self.btn_cursor.clicked.connect(lambda checked: self._set_pointer_tool("cursor", checked))
        self.btn_data_tip.clicked.connect(lambda checked: self._set_pointer_tool("data-tip", checked))
        self.btn_marker_a.clicked.connect(lambda: self.set_marker("A"))
        self.btn_marker_b.clicked.connect(lambda: self.set_marker("B"))
        self.btn_clear_annotations.clicked.connect(self.clear_annotations)
        self.btn_copy_plot.clicked.connect(self.copy_active_plot)
        self.btn_export_curves.clicked.connect(self.export_selected_dialog)
        return toolbar

    def _build_analysis_workspace(self) -> QtWidgets.QWidget:
        workspace = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self._build_curve_and_processing_panel())

        self.plot_tabs = QtWidgets.QTabWidget()
        self.time_plot = self._create_plot("time")
        self.frequency_plot = self._create_plot("frequency")
        self.plot_tabs.addTab(self.time_plot, "Time domain")
        self.plot_tabs.addTab(self.frequency_plot, "Frequency domain")
        self.plot_tabs.currentChanged.connect(self._active_plot_changed)
        workspace.addWidget(self.plot_tabs)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([480, 960])
        self.plot_widget = self.time_plot
        return workspace

    def _build_dependency_fallback(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        message = QtWidgets.QLabel(
            "Interactive plotting requires NumPy, SciPy and pyqtgraph.\n"
            "Install the GUI dependencies with:\n\n"
            "python -m pip install -e .[gui]\n\n"
            f"Import error: {_ANALYSIS_IMPORT_ERROR}"
        )
        message.setWordWrap(True)
        message.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(message, 1)
        self.plot_widget = message
        return panel

    def _build_curve_and_processing_panel(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("recordProcessingScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(440)
        scroll.setMaximumWidth(560)
        panel = QtWidgets.QWidget()
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )
        root = QtWidgets.QVBoxLayout(panel)
        root.setContentsMargins(2, 2, 5, 2)
        root.setSpacing(7)

        curves_group = GroupPanel("Curves")
        curves_layout = QtWidgets.QVBoxLayout(curves_group)
        curves_layout.setContentsMargins(6, 8, 6, 6)
        self.curve_tree = QtWidgets.QTreeWidget()
        self.curve_tree.setHeaderLabels(["Show", "Curve", "Domain", "State"])
        self.curve_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.curve_tree.setRootIsDecorated(False)
        self.curve_tree.setAlternatingRowColors(True)
        self.curve_tree.setMinimumHeight(180)
        curve_header = self.curve_tree.header()
        curve_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        curve_header.resizeSection(0, 44)
        curve_header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        curve_header.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        curve_header.resizeSection(2, 112)
        curve_header.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)
        curve_header.resizeSection(3, 76)
        curves_layout.addWidget(self.curve_tree, 1)
        root.addWidget(curves_group, 1)

        sampling_group = GroupPanel("Sampling")
        sampling_form = QtWidgets.QFormLayout(sampling_group)
        sampling_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        sampling_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.sample_rate = QtWidgets.QDoubleSpinBox()
        self.sample_rate.setRange(0.0, 1_000_000_000.0)
        self.sample_rate.setDecimals(6)
        self.sample_rate.setSuffix(" Hz")
        self.sample_rate.setSpecialValueText("Not set")
        self.sample_rate.setKeyboardTracking(False)
        self.sampling_state = QtWidgets.QLabel("—")
        self.sampling_state.setWordWrap(True)
        self.btn_resample = FlatPush("Resample selected")
        sampling_form.addRow("Sample rate", self.sample_rate)
        sampling_form.addRow("State", self.sampling_state)
        sampling_form.addRow(self.btn_resample)
        sampling_form.setContentsMargins(7, 8, 7, 6)
        sampling_form.setVerticalSpacing(5)
        root.addWidget(sampling_group)

        detrend_group = GroupPanel("Detrend")
        detrend_layout = QtWidgets.QHBoxLayout(detrend_group)
        self.btn_remove_mean = FlatPush("Remove mean only")
        self.btn_linear_detrend = FlatPush("Remove linear trend")
        detrend_layout.addWidget(self.btn_remove_mean)
        detrend_layout.addWidget(self.btn_linear_detrend)
        detrend_layout.setContentsMargins(7, 8, 7, 6)
        root.addWidget(detrend_group)

        smooth_group = GroupPanel("Moving Average")
        smooth_layout = QtWidgets.QHBoxLayout(smooth_group)
        self.smooth_window = QtWidgets.QSpinBox()
        self.smooth_window.setRange(3, 1001)
        self.smooth_window.setSingleStep(2)
        self.smooth_window.setValue(5)
        self.btn_smooth = FlatPush("Apply")
        smooth_layout.addWidget(QtWidgets.QLabel("Window"))
        smooth_layout.addWidget(self.smooth_window, 1)
        smooth_layout.addWidget(self.btn_smooth)
        smooth_layout.setContentsMargins(7, 8, 7, 6)
        root.addWidget(smooth_group)

        filter_group = GroupPanel("Butterworth Filter")
        filter_form = QtWidgets.QFormLayout(filter_group)
        filter_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        filter_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.filter_type = QtWidgets.QComboBox()
        self.filter_type.addItem("Low-pass", "lowpass")
        self.filter_type.addItem("High-pass", "highpass")
        self.filter_type.addItem("Band-pass", "bandpass")
        self.filter_low = QtWidgets.QDoubleSpinBox()
        self.filter_high = QtWidgets.QDoubleSpinBox()
        for control in (self.filter_low, self.filter_high):
            control.setRange(0.000001, 1_000_000_000.0)
            control.setDecimals(6)
            control.setSuffix(" Hz")
            control.setKeyboardTracking(False)
        # These labels follow the processing UI convention: Low cutoff is the
        # low-pass frequency; High cutoff is the high-pass frequency.
        self.filter_low.setValue(100.0)
        self.filter_high.setValue(5.0)
        self.filter_order = QtWidgets.QSpinBox()
        self.filter_order.setRange(1, 12)
        self.filter_order.setValue(4)
        self.btn_filter = FlatPush("Apply to selected")
        filter_form.addRow("Type", self.filter_type)
        filter_form.addRow("Low cutoff", self.filter_low)
        filter_form.addRow("High cutoff", self.filter_high)
        filter_form.addRow("Order", self.filter_order)
        filter_form.addRow(self.btn_filter)
        filter_form.setContentsMargins(7, 8, 7, 6)
        filter_form.setVerticalSpacing(5)
        root.addWidget(filter_group)

        spectrum_group = GroupPanel("Spectrum")
        spectrum_form = QtWidgets.QFormLayout(spectrum_group)
        spectrum_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        spectrum_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        self.psd_block = QtWidgets.QComboBox()
        for size in (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384):
            self.psd_block.addItem(str(size), size)
        self.psd_block.setCurrentIndex(self.psd_block.findData(4096))
        spectrum_buttons = QtWidgets.QHBoxLayout()
        self.btn_fft = FlatPush("FFT")
        self.btn_psd = FlatPush("Welch PSD")
        spectrum_buttons.addWidget(self.btn_fft)
        spectrum_buttons.addWidget(self.btn_psd)
        spectrum_form.addRow("PSD block", self.psd_block)
        spectrum_form.addRow(spectrum_buttons)
        spectrum_form.setContentsMargins(7, 8, 7, 6)
        spectrum_form.setVerticalSpacing(5)
        root.addWidget(spectrum_group)

        display_group = GroupPanel("Frequency Display")
        display_layout = QtWidgets.QHBoxLayout(display_group)
        self.frequency_x_log = QtWidgets.QCheckBox("Log X")
        self.frequency_db = QtWidgets.QCheckBox("Log Y")
        self.frequency_x_log.setChecked(True)
        self.frequency_db.setChecked(True)
        display_layout.addWidget(self.frequency_x_log)
        display_layout.addWidget(self.frequency_db)
        display_layout.addStretch(1)
        display_layout.setContentsMargins(7, 8, 7, 6)
        root.addWidget(display_group)
        root.addStretch(1)
        scroll.setWidget(panel)

        self.curve_tree.itemChanged.connect(self._curve_visibility_changed)
        self.curve_tree.itemClicked.connect(self._curve_item_clicked)
        self.curve_tree.itemSelectionChanged.connect(self._curve_selection_changed)
        self.sample_rate.editingFinished.connect(self._sample_rate_edited)
        self.btn_resample.clicked.connect(self.resample_selected)
        self.btn_remove_mean.clicked.connect(lambda: self.detrend_selected("constant"))
        self.btn_linear_detrend.clicked.connect(lambda: self.detrend_selected("linear"))
        self.btn_smooth.clicked.connect(self.smooth_selected)
        self.filter_type.currentIndexChanged.connect(self._filter_type_changed)
        self.btn_filter.clicked.connect(self.filter_selected)
        self.btn_fft.clicked.connect(self.fft_selected)
        self.btn_psd.clicked.connect(self.psd_selected)
        self.frequency_x_log.toggled.connect(self._frequency_display_changed)
        self.frequency_db.toggled.connect(self._frequency_display_changed)
        self._filter_type_changed()
        return scroll

    def _create_plot(self, domain: str):
        view_box = InteractiveViewBox(
            on_left_drag=lambda position, selected=domain: self._handle_pointer(
                selected, position, dragging=True
            ),
            on_right_zoom=lambda start, stop, selected=domain: self._rubber_zoom(
                selected, start, stop
            ),
            on_navigation_start=lambda selected=domain: self._remember_range(selected),
        )
        axes = {
            "left": PlainAxisItem(orientation="left"),
            "bottom": PlainAxisItem(orientation="bottom"),
        }
        plot = pg.PlotWidget(viewBox=view_box, axisItems=axes)
        plot.setBackground(PLOT_BACKGROUND)
        plot.getPlotItem().showGrid(x=True, y=True, alpha=0.22)
        plot.getPlotItem().setMenuEnabled(False)
        plot.getPlotItem().setLabel(
            "bottom",
            "Time (s)" if domain == "time" else "Frequency (Hz)",
            **{"font-size": f"{PLOT_FONT_POINTS}pt"},
        )
        plot.getPlotItem().setLabel(
            "left",
            "Value" if domain == "time" else "Amplitude / PSD",
            **{"font-size": f"{PLOT_FONT_POINTS}pt"},
        )
        if domain == "frequency":
            axes["bottom"].setLogMode(True)
            axes["left"].setLogMode(True)
        legend = plot.getPlotItem().addLegend(offset=(8, 8))
        legend.setZValue(10)
        legend.setBrush(pg.mkBrush(255, 255, 255, 228))
        legend.setPen(pg.mkPen("#7595a7", width=1.0))
        if hasattr(legend, "setLabelTextColor"):
            legend.setLabelTextColor(PLOT_FOREGROUND)
        plot._record_domain = domain
        plot._record_view_box = view_box
        plot._record_legend = legend
        plot.scene().sigMouseClicked.connect(
            lambda event, selected=domain: self._plot_scene_clicked(selected, event)
        )
        return plot

    def set_record(self, record: LoggingRecord) -> None:
        """Replace the viewer contents with one completed logging record."""

        suffix = ""
        if _ANALYSIS_IMPORT_ERROR is not None:  # pragma: no cover
            self.record_summary.setText(
                f"{len(record.rows)} samples · plotting dependencies unavailable{suffix}"
            )
            self.record_path.setText(record.source)
            self.status_label.setText(
                f"Plotting unavailable: {_ANALYSIS_IMPORT_ERROR}."
            )
            return

        self.clear_annotations(all_domains=True)
        self._remove_all_curve_items()
        self.analysis_session = RecordAnalysisSession.from_record(record)
        self._frequency_views.clear()
        self._curve_colors.clear()
        self._display_cache.clear()
        self._visible_ids = {
            curve.curve_id for curve in self.analysis_session.curves_for_domain("time")[:6]
        }
        self._zoom_history["time"].clear()
        self._zoom_history["frequency"].clear()
        self._last_saved_range = {"time": None, "frequency": None}
        self._refresh_curve_tree()
        self._update_sampling_controls()
        self._refresh_plots(auto_range=True)
        self.record_summary.setText(
            f"{len(record.rows)} samples · {len(self.analysis_session.curves_for_domain('time'))} numeric signals{suffix}"
        )
        self.record_path.setText(record.source)
        if self.analysis_session.curves:
            self.status_label.setText(
                "Record loaded. Select curves on the left; the first six numeric channels are visible."
            )
        else:
            self.status_label.setText("The record contains no numeric signal columns.")

    def _remove_all_curve_items(self) -> None:
        if _ANALYSIS_IMPORT_ERROR is not None or not hasattr(self, "time_plot"):
            self._curve_items.clear()
            return
        for domain, plot in (("time", self.time_plot), ("frequency", self.frequency_plot)):
            for item in list(self._curve_items.get(domain, {}).values()):
                try:
                    plot._record_view_box.removeItem(item)
                except (RuntimeError, ValueError):
                    pass
            plot._record_legend.clear()
        self._curve_items.clear()

    def _refresh_curve_tree(
        self,
        *,
        select_ids: set[str] | None = None,
        make_visible: set[str] | None = None,
    ) -> None:
        if self.analysis_session is None:
            return
        previous_selection = set(select_ids or self.selected_curve_ids())
        if make_visible:
            self._visible_ids.update(make_visible)
        self._populating_curves = True
        self.curve_tree.clear()
        try:
            for index, curve in enumerate(self._time_curves()):
                color = self._curve_colors.setdefault(
                    curve.curve_id, CURVE_COLORS[index % len(CURVE_COLORS)]
                )
                item = QtWidgets.QTreeWidgetItem(
                    [
                        "",
                        curve.name,
                        (
                            "Time + Spectrum"
                            if curve.curve_id in self._frequency_views
                            else "Time"
                        ),
                        (
                            "Modified"
                            if curve.operation.get("type") != "source"
                            or curve.curve_id in self._frequency_views
                            else "Original"
                        ),
                    ]
                )
                item.setData(0, QtCore.Qt.UserRole, curve.curve_id)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    QtCore.Qt.Checked
                    if curve.curve_id in self._visible_ids
                    else QtCore.Qt.Unchecked,
                )
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))
                if curve.derived:
                    font = item.font(1)
                    font.setItalic(True)
                    item.setFont(1, font)
                self.curve_tree.addTopLevelItem(item)
                if curve.curve_id in previous_selection:
                    item.setSelected(True)
            if not self.curve_tree.selectedItems() and self.curve_tree.topLevelItemCount():
                first_visible = next(
                    (
                        self.curve_tree.topLevelItem(row)
                        for row in range(self.curve_tree.topLevelItemCount())
                        if self.curve_tree.topLevelItem(row).checkState(0)
                        == QtCore.Qt.Checked
                    ),
                    self.curve_tree.topLevelItem(0),
                )
                first_visible.setSelected(True)
                self.curve_tree.setCurrentItem(first_visible)
        finally:
            self._populating_curves = False
        self._curve_selection_changed()

    def selected_curve_ids(self, domain: str | None = None) -> list[str]:
        if _ANALYSIS_IMPORT_ERROR is not None or not hasattr(self, "curve_tree"):
            return []
        result: list[str] = []
        for item in self.curve_tree.selectedItems():
            curve_id = str(item.data(0, QtCore.Qt.UserRole))
            if self.analysis_session is None:
                continue
            try:
                curve = self.analysis_session.get_curve(curve_id)
            except KeyError:
                # The tree is rebuilt after replacing a record or deleting a
                # derived subtree; stale selected items are ignored.
                continue
            if domain is None or domain == "time" or (
                domain == "frequency" and curve_id in self._frequency_views
            ):
                result.append(curve_id)
        return result

    def _time_curves(self) -> tuple[NumericCurve, ...]:
        if self.analysis_session is None:
            return ()
        return tuple(
            curve
            for curve in self.analysis_session.curves
            if not curve.derived and curve.domain == "time"
        )

    def _frequency_curves(self) -> tuple[NumericCurve, ...]:
        if self.analysis_session is None:
            return ()
        curves: list[NumericCurve] = []
        for source in self._time_curves():
            result_id = self._frequency_views.get(source.curve_id)
            if result_id is None:
                continue
            try:
                result = self.analysis_session.get_curve(result_id)
            except KeyError:
                continue
            curves.append(
                replace(result, curve_id=source.curve_id, name=source.name)
            )
        return tuple(curves)

    def _curves_for_domain(self, domain: str) -> tuple[NumericCurve, ...]:
        return self._frequency_curves() if domain == "frequency" else self._time_curves()

    def _curve_for_domain(self, curve_id: str, domain: str) -> NumericCurve:
        curves = self._curves_for_domain(domain)
        for curve in curves:
            if curve.curve_id == curve_id:
                return curve
        raise KeyError(f"unknown {domain} curve: {curve_id}")

    def _curve_visibility_changed(self, item, column: int) -> None:
        if self._populating_curves or column != 0:
            return
        curve_id = str(item.data(0, QtCore.Qt.UserRole))
        if item.checkState(0) == QtCore.Qt.Checked:
            self._visible_ids.add(curve_id)
            self._select_curve_in_tree(curve_id)
        else:
            self._visible_ids.discard(curve_id)
        self._refresh_plots()

    def _curve_item_clicked(self, item, _column: int) -> None:
        """Treat every tree-cell click, including Show, as target selection."""

        curve_id = str(item.data(0, QtCore.Qt.UserRole))
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        additive = bool(modifiers & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier))
        self._select_curve_in_tree(curve_id, additive=additive)

    def _select_curve_in_tree(self, curve_id: str, *, additive: bool = False) -> None:
        """Make a curve the primary operation/annotation target."""

        if not additive:
            self.curve_tree.clearSelection()
        for row in range(self.curve_tree.topLevelItemCount()):
            item = self.curve_tree.topLevelItem(row)
            if str(item.data(0, QtCore.Qt.UserRole)) != curve_id:
                continue
            if additive:
                self.curve_tree.setCurrentItem(
                    item, 0, QtCore.QItemSelectionModel.NoUpdate
                )
                item.setSelected(True)
            else:
                self.curve_tree.setCurrentItem(item)
                item.setSelected(True)
            self.curve_tree.scrollToItem(item)
            break

    def _plot_curve_clicked(self, curve_id: str, event) -> None:
        modifiers = event.modifiers() if hasattr(event, "modifiers") else QtCore.Qt.NoModifier
        additive = bool(modifiers & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier))
        self._select_curve_in_tree(curve_id, additive=additive)
        if self.analysis_session is not None:
            curve = self.analysis_session.get_curve(curve_id)
            self.status_label.setText(f"Selected curve: {curve.name}")

    def _curve_selection_changed(self) -> None:
        if self._populating_curves or self.analysis_session is None:
            return
        self._update_curve_selection_style()

    def _update_curve_selection_style(self) -> None:
        """Update emphasis without replacing a curve during its click event.

        pyqtgraph keeps the clicked PlotCurveItem in its scene mouse-event
        candidate list until mouseReleaseEvent has returned.  Removing and
        recreating PlotDataItems from ``sigClicked`` invalidates that C++
        object mid-dispatch and causes a libshiboken RuntimeError.  Pen changes
        are safe and preserve both the PlotDataItem and its PlotCurveItem.
        """

        if self.analysis_session is None:
            return
        selected = set(self.selected_curve_ids())
        for items in self._curve_items.values():
            for curve_id, item in items.items():
                try:
                    item.setPen(
                        pg.mkPen(
                            self._curve_colors[curve_id],
                            width=2.2 if curve_id in selected else 1.45,
                        )
                    )
                except RuntimeError:
                    # A record replacement may already have scheduled this
                    # item for Qt deletion; the next full refresh owns it.
                    continue

    def _display_data(self, curve: NumericCurve) -> tuple[Any, Any, Any]:
        sample_rate = (
            self.analysis_session.sampling.sample_rate_hz
            if self.analysis_session is not None
            else None
        )
        cache_key = (
            curve.curve_id,
            curve.domain,
            curve.operation.get("type"),
            bool(self.frequency_x_log.isChecked()),
            bool(self.frequency_db.isChecked()),
            sample_rate if curve.x_unit == "samples" else None,
        )
        cached = self._display_cache.get(cache_key)
        if cached is not None:
            return cached
        x_values = curve.x
        if curve.domain == "time" and curve.x_unit == "samples" and sample_rate:
            x_values = curve.x / sample_rate
        if curve.domain == "frequency" and self.frequency_x_log.isChecked():
            x_values = np.full(curve.x.shape, np.nan, dtype=np.float64)
            positive = curve.x > 0.0
            x_values[positive] = np.log10(curve.x[positive])
        y_values = curve.y
        if curve.domain == "frequency" and self.frequency_db.isChecked():
            y_values = np.full(curve.y.shape, np.nan, dtype=np.float64)
            positive = curve.y > 0.0
            y_values[positive] = np.log10(curve.y[positive])
        finite_indices = np.flatnonzero(np.isfinite(x_values) & np.isfinite(y_values))
        result = (x_values, y_values, finite_indices)
        self._display_cache[cache_key] = result
        return result

    def _display_arrays(self, curve: NumericCurve) -> tuple[Any, Any]:
        x_values, y_values, _finite_indices = self._display_data(curve)
        return x_values, y_values

    def _semantic_x_value(self, curve: NumericCurve, index: int) -> float:
        value = float(curve.x[index])
        if (
            curve.domain == "time"
            and curve.x_unit == "samples"
            and self.analysis_session is not None
            and self.analysis_session.sampling.sample_rate_hz
        ):
            value /= self.analysis_session.sampling.sample_rate_hz
        return value

    def _refresh_plots(self, *, auto_range: bool = False) -> None:
        if self.analysis_session is None:
            return
        selected = set(self.selected_curve_ids())
        for domain, plot in (("time", self.time_plot), ("frequency", self.frequency_plot)):
            view_box = plot._record_view_box
            for item in list(self._curve_items.get(domain, {}).values()):
                view_box.removeItem(item)
            self._curve_items[domain] = {}
            plot._record_legend.clear()
            for curve in self._curves_for_domain(domain):
                if curve.curve_id not in self._visible_ids:
                    continue
                x_values, y_values, finite = self._display_data(curve)
                if not len(finite):
                    continue
                width = 2.2 if curve.curve_id in selected else 1.45
                pen = pg.mkPen(self._curve_colors[curve.curve_id], width=width)
                item = pg.PlotDataItem(
                    x_values[finite], y_values[finite], pen=pen, name=curve.name
                )
                item.setCurveClickable(True, width=10)
                item.sigClicked.connect(
                    lambda _item, event, selected_id=curve.curve_id: self._plot_curve_clicked(
                        selected_id, event
                    )
                )
                item.setZValue(0)
                view_box.addItem(item)
                # Enable view-dependent optimisations after the item has a
                # ViewBox parent.  pyqtgraph 0.14 queries that parent
                # immediately when auto-downsampling is switched on.
                item.setDownsampling(auto=True, method="peak")
                item.setClipToView(True)
                plot._record_legend.addItem(item, curve.name)
                legend_label = plot._record_legend.items[-1][1]
                if hasattr(legend_label, "item"):
                    legend_label.item.setFont(_plot_font())
                self._curve_items[domain][curve.curve_id] = item
        if auto_range:
            self._auto_fit_domain("time")
            self._auto_fit_domain("frequency")

    def _auto_fit_domain(self, domain: str) -> None:
        """Fit the visible finite samples, with a useful dB dynamic range."""

        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        x_parts: list[Any] = []
        y_parts: list[Any] = []
        for curve in self._curves_for_domain(domain):
            if curve.curve_id not in self._visible_ids:
                continue
            x_values, y_values, finite = self._display_data(curve)
            if len(finite):
                x_parts.append(x_values[finite])
                y_parts.append(y_values[finite])
        if not x_parts:
            return
        x_values = np.concatenate(x_parts)
        y_values = np.concatenate(y_parts)
        x_low, x_high = float(np.min(x_values)), float(np.max(x_values))
        y_low, y_high = float(np.min(y_values)), float(np.max(y_values))
        def padded(low: float, high: float) -> tuple[float, float]:
            if high <= low:
                margin = max(abs(low) * 0.05, 1.0)
            else:
                margin = (high - low) * 0.04
            return low - margin, high + margin

        plot._record_view_box.setRange(
            xRange=padded(x_low, x_high),
            yRange=padded(y_low, y_high),
            padding=0.0,
        )

    def _frequency_display_changed(self) -> None:
        if self.analysis_session is None:
            return
        self._display_cache.clear()
        axis = self.frequency_plot.getPlotItem().getAxis("bottom")
        axis.setLogMode(self.frequency_x_log.isChecked())
        y_axis = self.frequency_plot.getPlotItem().getAxis("left")
        y_axis.setLogMode(self.frequency_db.isChecked())
        self.frequency_plot.getPlotItem().setLabel(
            "left",
            "Amplitude / PSD",
            **{"font-size": f"{PLOT_FONT_POINTS}pt"},
        )
        self.clear_annotations(domain="frequency")
        self._refresh_plots()
        self._auto_fit_domain("frequency")

    def _active_domain(self) -> str:
        return "frequency" if self.plot_tabs.currentIndex() == 1 else "time"

    def _active_plot(self):
        return self.frequency_plot if self._active_domain() == "frequency" else self.time_plot

    def _active_plot_changed(self, _index: int) -> None:
        self._update_marker_readout(self._active_domain())

    def _set_pointer_tool(self, tool: str, checked: bool) -> None:
        if tool == "cursor":
            if checked:
                self.btn_data_tip.setChecked(False)
            elif not self.btn_data_tip.isChecked():
                self.btn_cursor.setChecked(True)
        else:
            if checked:
                self.btn_cursor.setChecked(False)
            elif not self.btn_cursor.isChecked():
                self.btn_cursor.setChecked(True)

    def _pointer_tool(self) -> str:
        return "data-tip" if self.btn_data_tip.isChecked() else "cursor"

    def _candidate_curves(self, domain: str, only_curve_id: str | None = None):
        if self.analysis_session is None:
            return []
        if only_curve_id:
            try:
                return [self._curve_for_domain(only_curve_id, domain)]
            except KeyError:
                return []
        selected_ids = self.selected_curve_ids(domain)
        selected = set(selected_ids)
        visible = [
            curve
            for curve in self._curves_for_domain(domain)
            if curve.curve_id in self._visible_ids
        ]
        current = self.curve_tree.currentItem()
        current_id = (
            str(current.data(0, QtCore.Qt.UserRole)) if current is not None else ""
        )
        selected_visible = [curve for curve in visible if curve.curve_id in selected]
        selected_visible.sort(key=lambda curve: curve.curve_id != current_id)
        return selected_visible or visible

    def _nearest_point(
        self,
        domain: str,
        scene_position,
        *,
        only_curve_id: str | None = None,
    ) -> tuple[NumericCurve, int, float, float, float, float] | None:
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        view_box = plot._record_view_box
        if not view_box.sceneBoundingRect().contains(scene_position):
            return None
        point = view_box.mapSceneToView(scene_position)
        x_range, y_range = view_box.viewRange()
        x_span = max(abs(x_range[1] - x_range[0]), 1e-12)
        y_span = max(abs(y_range[1] - y_range[0]), 1e-12)
        best = None
        best_distance = float("inf")
        for curve in self._candidate_curves(domain, only_curve_id):
            x_values, y_values, indices = self._display_data(curve)
            if not len(indices):
                continue
            candidate_x = x_values[indices]
            if len(candidate_x) > 1 and np.all(np.diff(candidate_x) >= 0.0):
                insertion = int(np.searchsorted(candidate_x, point.x()))
                local = {
                    max(0, min(insertion, len(indices) - 1)),
                    max(0, min(insertion - 1, len(indices) - 1)),
                }
                candidates = [indices[position] for position in local]
            else:
                candidates = [indices[int(np.argmin(np.abs(candidate_x - point.x())))]]
            for index in candidates:
                plot_x = float(x_values[index])
                plot_y = float(y_values[index])
                distance = ((plot_x - point.x()) / x_span) ** 2 + (
                    (plot_y - point.y()) / y_span
                ) ** 2
                if distance < best_distance:
                    best_distance = distance
                    best = (
                        curve,
                        int(index),
                        self._semantic_x_value(curve, int(index)),
                        float(curve.y[index]),
                        plot_x,
                        plot_y,
                    )
        return best

    def _nearest_for_view_x(
        self, domain: str, curve_id: str, view_x: float
    ) -> tuple[NumericCurve, int, float, float, float, float] | None:
        if self.analysis_session is None:
            return None
        try:
            curve = self._curve_for_domain(curve_id, domain)
        except KeyError:
            return None
        x_values, y_values, indices = self._display_data(curve)
        if not len(indices):
            return None
        candidates = x_values[indices]
        if len(candidates) > 1 and np.all(np.diff(candidates) >= 0.0):
            insertion = int(np.searchsorted(candidates, view_x))
            choices = [
                max(0, min(insertion, len(indices) - 1)),
                max(0, min(insertion - 1, len(indices) - 1)),
            ]
            local = min(choices, key=lambda item: abs(float(candidates[item]) - view_x))
            index = int(indices[local])
        else:
            index = int(indices[int(np.argmin(np.abs(candidates - view_x)))])
        return (
            curve,
            index,
            self._semantic_x_value(curve, index),
            float(curve.y[index]),
            float(x_values[index]),
            float(y_values[index]),
        )

    def _plot_scene_clicked(self, domain: str, event) -> None:
        if event.button() == QtCore.Qt.RightButton:
            if time.monotonic() < self._tip_menu_suppressed_until:
                return
            self._show_plot_menu(domain, event.screenPos())
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._handle_pointer(domain, event.scenePos(), dragging=False)

    def _handle_pointer(self, domain: str, scene_position, *, dragging: bool) -> None:
        nearest = self._nearest_point(domain, scene_position)
        if nearest is None:
            return
        if self._pointer_tool() == "data-tip":
            if not dragging:
                self._add_data_tip(domain, nearest)
        else:
            self._update_cursor(domain, nearest)

    def _format_point_label(self, prefix: str, nearest) -> str:
        curve, _index, x_value, _y_value, _plot_x, _plot_y = nearest
        shown_y = self._displayed_point_y(nearest)
        return (
            f"{prefix}{curve.name}\n"
            f"X {_short_number(x_value)}\nY {_short_number(shown_y)}"
        )

    def _displayed_point_y(self, nearest) -> float:
        curve = nearest[0]
        return float(nearest[3])

    def _update_cursor(self, domain: str, nearest) -> None:
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        view_box = plot._record_view_box
        state = self._cursor_state.get(domain)
        if state is None:
            line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#0f4c81", width=1.2))
            point = pg.ScatterPlotItem(
                size=9,
                pen=pg.mkPen("#071827", width=1.0),
                brush=pg.mkBrush("#fff176"),
            )
            label = pg.TextItem(
                color="#071827",
                fill=pg.mkBrush(255, 245, 157, 225),
                border=pg.mkPen("#071827", width=0.8),
                anchor=(0.0, 1.0),
            )
            label.setFont(_plot_font())
            line.setZValue(30)
            point.setZValue(31)
            label.setZValue(32)
            view_box.addItem(line, ignoreBounds=True)
            view_box.addItem(point, ignoreBounds=True)
            view_box.addItem(label, ignoreBounds=True)
            state = {"line": line, "point": point, "label": label, "nearest": None}
            self._cursor_state[domain] = state
        curve, index, _x, _y, plot_x, plot_y = nearest
        state["line"].setValue(plot_x)
        state["point"].setData([plot_x], [plot_y])
        state["label"].setText(self._format_point_label("", nearest))
        state["label"].setPos(plot_x, plot_y)
        state["nearest"] = nearest
        shown_y = self._displayed_point_y(nearest)
        self.status_label.setText(
            f"Cursor · {curve.name} · sample {index + 1} · X {_short_number(nearest[2])} · Y {_short_number(shown_y)}"
        )

    def _add_data_tip(self, domain: str, nearest) -> None:
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        view_box = plot._record_view_box
        self._tip_counter += 1
        tip_id = self._tip_counter
        curve, _index, _x, _y, plot_x, plot_y = nearest
        color = self._curve_colors.get(curve.curve_id, "#1875a6")
        point = DataTipPoint(
            [plot_x],
            [plot_y],
            size=10,
            pen=pg.mkPen("#071827", width=1.0),
            brush=pg.mkBrush(color),
            on_drag=lambda scene, selected=tip_id: self._drag_data_tip(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label = DataTipText(
            self._format_point_label("", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color, width=1.0),
            anchor=(-0.05, 1.05),
            on_drag=lambda scene, selected=tip_id: self._drag_tip_label(selected, scene),
            on_menu=lambda screen, selected=tip_id: self._show_tip_menu(selected, screen),
        )
        label.setFont(_plot_font())
        point.setZValue(40)
        label.setZValue(41)
        label.setPos(plot_x, plot_y)
        view_box.addItem(point, ignoreBounds=True)
        view_box.addItem(label, ignoreBounds=True)
        self._data_tips[tip_id] = {
            "domain": domain,
            "curve_id": curve.curve_id,
            "nearest": nearest,
            "point": point,
            "label": label,
        }
        self.status_label.setText(f"Data tip added to {curve.name}.")

    def _drag_data_tip(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        nearest = self._nearest_point(
            tip["domain"], scene_position, only_curve_id=tip["curve_id"]
        )
        if nearest is None:
            return
        tip["nearest"] = nearest
        tip["point"].setData([nearest[4]], [nearest[5]])
        tip["label"].setText(self._format_point_label("", nearest))
        tip["label"].setPos(nearest[4], nearest[5])

    def _drag_tip_label(self, tip_id: int, scene_position) -> None:
        tip = self._data_tips.get(tip_id)
        if tip is None:
            return
        plot = self.frequency_plot if tip["domain"] == "frequency" else self.time_plot
        mouse = plot._record_view_box.mapSceneToView(scene_position)
        nearest = tip["nearest"]
        right = mouse.x() >= nearest[4]
        above = mouse.y() >= nearest[5]
        tip["label"].setAnchor(
            (-0.05 if right else 1.05, 1.05 if above else -0.05)
        )
        tip["label"].setPos(nearest[4], nearest[5])

    def _show_tip_menu(self, tip_id: int, screen_position) -> None:
        # pyqtgraph also emits the scene-level right-click signal after a
        # graphics item has accepted the click.  Suppress the plot menu for
        # this event so a data tip produces exactly one context menu.
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        menu = QtWidgets.QMenu(self)
        remove = menu.addAction("Delete this data tip")
        clear = menu.addAction("Clear all data tips")
        action = menu.exec(self._screen_point(screen_position))
        self._tip_menu_suppressed_until = time.monotonic() + 0.5
        if action == remove:
            self._remove_data_tip(tip_id)
        elif action == clear:
            self.clear_data_tips()

    def _remove_data_tip(self, tip_id: int) -> None:
        tip = self._data_tips.pop(tip_id, None)
        if tip is None:
            return
        plot = self.frequency_plot if tip["domain"] == "frequency" else self.time_plot
        plot._record_view_box.removeItem(tip["point"])
        plot._record_view_box.removeItem(tip["label"])

    def clear_data_tips(self, domain: str | None = None) -> None:
        for tip_id, tip in list(self._data_tips.items()):
            if domain is None or tip["domain"] == domain:
                self._remove_data_tip(tip_id)

    def _remove_cursor(self, domain: str) -> None:
        state = self._cursor_state.pop(domain, None)
        if not state:
            return
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        for key in ("line", "point", "label"):
            plot._record_view_box.removeItem(state[key])

    def set_marker(self, name: str) -> None:
        domain = self._active_domain()
        candidates = self._candidate_curves(domain)
        if not candidates:
            self.status_label.setText("Show and select a curve before setting a marker.")
            return
        curve = candidates[0]
        cursor = self._cursor_state.get(domain, {}).get("nearest")
        if cursor is not None and cursor[0].curve_id == curve.curve_id:
            nearest = cursor
        else:
            plot = self._active_plot()
            x_range = plot._record_view_box.viewRange()[0]
            nearest = self._nearest_for_view_x(
                domain, curve.curve_id, (x_range[0] + x_range[1]) / 2.0
            )
        if nearest is None:
            return
        self._remove_marker(domain, name)
        plot = self._active_plot()
        view_box = plot._record_view_box
        color = "#e64a19" if name == "A" else "#7b1fa2"
        line = pg.InfiniteLine(
            pos=nearest[4], angle=90, movable=True, pen=pg.mkPen(color, width=1.6)
        )
        point = pg.ScatterPlotItem(
            [nearest[4]],
            [nearest[5]],
            size=10,
            pen=pg.mkPen("#ffffff", width=1.0),
            brush=pg.mkBrush(color),
        )
        label = pg.TextItem(
            self._format_point_label(f"{name} · ", nearest),
            color="#071827",
            fill=pg.mkBrush(255, 255, 255, 232),
            border=pg.mkPen(color, width=1.0),
            anchor=(0.0, 1.0),
        )
        label.setFont(_plot_font())
        line.setZValue(20)
        point.setZValue(21)
        label.setZValue(22)
        label.setPos(nearest[4], nearest[5])
        view_box.addItem(line, ignoreBounds=True)
        view_box.addItem(point, ignoreBounds=True)
        view_box.addItem(label, ignoreBounds=True)
        marker = {
            "curve_id": curve.curve_id,
            "nearest": nearest,
            "line": line,
            "point": point,
            "label": label,
        }
        self._markers[domain][name] = marker
        line.sigPositionChanged.connect(
            lambda _line=None, selected_domain=domain, selected_name=name: self._marker_moved(
                selected_domain, selected_name, False
            )
        )
        line.sigPositionChangeFinished.connect(
            lambda _line=None, selected_domain=domain, selected_name=name: self._marker_moved(
                selected_domain, selected_name, True
            )
        )
        self._update_marker_readout(domain)

    def _marker_moved(self, domain: str, name: str, finished: bool) -> None:
        if self._moving_marker:
            return
        marker = self._markers.get(domain, {}).get(name)
        if marker is None:
            return
        nearest = self._nearest_for_view_x(
            domain, marker["curve_id"], float(marker["line"].value())
        )
        if nearest is None:
            return
        marker["nearest"] = nearest
        marker["point"].setData([nearest[4]], [nearest[5]])
        marker["label"].setText(self._format_point_label(f"{name} · ", nearest))
        marker["label"].setPos(nearest[4], nearest[5])
        if finished:
            self._moving_marker = True
            try:
                marker["line"].setValue(nearest[4])
            finally:
                self._moving_marker = False
        self._update_marker_readout(domain)

    def _remove_marker(self, domain: str, name: str) -> None:
        marker = self._markers.get(domain, {}).pop(name, None)
        if marker is None:
            return
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        for key in ("line", "point", "label"):
            plot._record_view_box.removeItem(marker[key])

    def _update_marker_readout(self, domain: str) -> None:
        markers = self._markers.get(domain, {})
        a = markers.get("A", {}).get("nearest")
        b = markers.get("B", {}).get("nearest")
        a_text = "—" if a is None else f"{_short_number(a[2])}, {_short_number(self._displayed_point_y(a))}"
        b_text = "—" if b is None else f"{_short_number(b[2])}, {_short_number(self._displayed_point_y(b))}"
        if a is not None and b is not None:
            delta = (
                f"{_short_number(b[2] - a[2])}, "
                f"{_short_number(self._displayed_point_y(b) - self._displayed_point_y(a))}"
            )
        else:
            delta = "—"
        self.marker_readout.setText(f"A {a_text}   B {b_text}   Δ {delta}")

    def clear_annotations(
        self, _checked: bool = False, *, domain: str | None = None, all_domains: bool = False
    ) -> None:
        if _ANALYSIS_IMPORT_ERROR is not None or not hasattr(self, "time_plot"):
            return
        domains = (
            ("time", "frequency")
            if all_domains or domain is None and _checked is False
            else (domain or self._active_domain(),)
        )
        if not all_domains and domain is None:
            domains = (self._active_domain(),)
        for selected in domains:
            self.clear_data_tips(selected)
            self._remove_cursor(selected)
            for marker_name in list(self._markers[selected]):
                self._remove_marker(selected, marker_name)
            self._update_marker_readout(selected)

    @staticmethod
    def _screen_point(position) -> QtCore.QPoint:
        if hasattr(position, "toPoint"):
            return position.toPoint()
        return QtCore.QPoint(int(position.x()), int(position.y()))

    def _show_plot_menu(self, domain: str, screen_position) -> None:
        menu = QtWidgets.QMenu(self)
        previous = menu.addAction("Previous zoom")
        auto = menu.addAction("Auto fit")
        menu.addSeparator()
        cursor = menu.addAction("Cursor")
        cursor.setCheckable(True)
        cursor.setChecked(self._pointer_tool() == "cursor")
        data_tip = menu.addAction("Data tip")
        data_tip.setCheckable(True)
        data_tip.setChecked(self._pointer_tool() == "data-tip")
        clear_tips = menu.addAction("Clear data tips")
        marker_a = menu.addAction("Set marker A")
        marker_b = menu.addAction("Set marker B")
        clear_annotations = menu.addAction("Clear annotations")
        menu.addSeparator()
        copy_image = menu.addAction("Copy image")
        export = menu.addAction("Export selected curves")
        action = menu.exec(self._screen_point(screen_position))
        if action == previous:
            self.previous_zoom(domain=domain)
        elif action == auto:
            self.auto_range(domain=domain)
        elif action == cursor:
            self.btn_cursor.setChecked(True)
            self.btn_data_tip.setChecked(False)
        elif action == data_tip:
            self.btn_cursor.setChecked(False)
            self.btn_data_tip.setChecked(True)
        elif action == clear_tips:
            self.clear_data_tips(domain)
        elif action == marker_a:
            self.plot_tabs.setCurrentIndex(1 if domain == "frequency" else 0)
            self.set_marker("A")
        elif action == marker_b:
            self.plot_tabs.setCurrentIndex(1 if domain == "frequency" else 0)
            self.set_marker("B")
        elif action == clear_annotations:
            self.clear_annotations(domain=domain)
        elif action == copy_image:
            self.copy_active_plot()
        elif action == export:
            self.export_selected_dialog()

    def _remember_range(self, domain: str) -> None:
        if _ANALYSIS_IMPORT_ERROR is not None:
            return
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        ranges = plot._record_view_box.viewRange()
        snapshot = (
            (float(ranges[0][0]), float(ranges[0][1])),
            (float(ranges[1][0]), float(ranges[1][1])),
        )
        if self._last_saved_range[domain] == snapshot:
            return
        self._zoom_history[domain].append(snapshot)
        self._last_saved_range[domain] = snapshot

    def _rubber_zoom(self, domain: str, start, stop) -> None:
        if abs(stop.x() - start.x()) < 1e-12 or abs(stop.y() - start.y()) < 1e-12:
            return
        self._remember_range(domain)
        plot = self.frequency_plot if domain == "frequency" else self.time_plot
        plot._record_view_box.setRange(
            xRange=sorted((float(start.x()), float(stop.x()))),
            yRange=sorted((float(start.y()), float(stop.y()))),
            padding=0.0,
        )

    def auto_range(self, _checked: bool = False, *, domain: str | None = None) -> None:
        if _ANALYSIS_IMPORT_ERROR is not None:
            return
        selected = domain or self._active_domain()
        self._remember_range(selected)
        self._auto_fit_domain(selected)

    def previous_zoom(self, _checked: bool = False, *, domain: str | None = None) -> None:
        if _ANALYSIS_IMPORT_ERROR is not None:
            return
        selected = domain or self._active_domain()
        if not self._zoom_history[selected]:
            self.status_label.setText("No previous zoom range is available.")
            return
        x_range, y_range = self._zoom_history[selected].pop()
        plot = self.frequency_plot if selected == "frequency" else self.time_plot
        plot._record_view_box.setRange(xRange=x_range, yRange=y_range, padding=0.0)
        self._last_saved_range[selected] = None

    def copy_active_plot(self) -> bool:
        if _ANALYSIS_IMPORT_ERROR is not None:
            return False
        pixmap = self._active_plot().grab()
        if pixmap.isNull():
            self.status_label.setText("The plot image could not be copied.")
            return False
        QtWidgets.QApplication.clipboard().setPixmap(pixmap, QtGui.QClipboard.Clipboard)
        self.status_label.setText("Active plot copied to the clipboard.")
        return True

    def _sample_rate_edited(self) -> None:
        if self.analysis_session is None:
            return
        if self.sample_rate.value() <= 0.0:
            self.status_label.setText("Enter a positive sample rate.")
            return
        try:
            self.analysis_session.set_sample_rate(self.sample_rate.value())
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._display_cache.clear()
        self._update_sampling_controls()
        self._refresh_plots(auto_range=True)

    def _update_sampling_controls(self) -> None:
        if self.analysis_session is None:
            return
        sampling = self.analysis_session.sampling
        self.sample_rate.blockSignals(True)
        try:
            if sampling.sample_rate_hz is not None:
                self.sample_rate.setValue(sampling.sample_rate_hz)
            else:
                self.sample_rate.setValue(0.0)
        finally:
            self.sample_rate.blockSignals(False)
        self.time_plot.getPlotItem().setLabel("bottom", sampling.x_label)
        state = "Regular" if sampling.regular else "Irregular — resample before processing"
        jitter = (
            f" · jitter {_short_number(sampling.jitter_ratio * 100.0)}%"
            if np.isfinite(sampling.jitter_ratio)
            else ""
        )
        self.sampling_state.setText(
            f"{state}{jitter}\nSource: {sampling.source}\n{sampling.reason}"
        )
        if sampling.sample_rate_hz is not None:
            nyquist = sampling.sample_rate_hz / 2.0
            self.filter_low.setMaximum(max(0.000001, nyquist * 0.999999))
            self.filter_high.setMaximum(max(0.000001, nyquist * 0.999999))
            if self.filter_low.value() >= nyquist:
                self.filter_low.setValue(max(0.000001, nyquist * 0.8))
            if self.filter_high.value() >= nyquist:
                self.filter_high.setValue(max(0.000001, nyquist * 0.1))

    def _selected_time_ids(self) -> list[str]:
        selected = self.selected_curve_ids("time")
        if not selected:
            self.status_label.setText("Select one or more time-domain curves first.")
        return selected

    def _run_derivation(
        self,
        label: str,
        operation: Callable[[str], NumericCurve],
        *,
        switch_to_frequency: bool = False,
    ) -> list[NumericCurve]:
        if self.analysis_session is None:
            return []
        source_ids = self._selected_time_ids()
        if not source_ids:
            return []
        updated: list[NumericCurve] = []
        errors: list[str] = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for curve_id in source_ids:
                try:
                    result = operation(curve_id)
                    previous_spectrum = self._frequency_views.pop(curve_id, None)
                    updated.append(
                        self.analysis_session.replace_curve_data(
                            curve_id, result.curve_id
                        )
                    )
                    if previous_spectrum is not None:
                        self.analysis_session.delete_curve(previous_spectrum)
                except MemoryError:
                    errors.append(
                        f"{self.analysis_session.get_curve(curve_id).name}: operation needs too much memory"
                    )
                except (ValueError, RuntimeError) as exc:
                    errors.append(f"{self.analysis_session.get_curve(curve_id).name}: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        updated_ids = {curve.curve_id for curve in updated}
        if updated:
            # Existing pointers refer to the pre-processing arrays and must not
            # survive an in-place data/domain replacement.
            self.clear_annotations(all_domains=True)
            self._display_cache.clear()
            self._refresh_curve_tree(select_ids=updated_ids, make_visible=updated_ids)
            self._refresh_plots(auto_range=True)
            if switch_to_frequency:
                self.plot_tabs.setCurrentIndex(1)
            self.status_label.setText(f"{label}: updated {len(updated)} selected curve(s).")
        if errors:
            self._show_error("\n".join(errors))
        return updated

    def resample_selected(self) -> list[NumericCurve]:
        rate = self.sample_rate.value()
        return self._run_derivation(
            "Resample",
            lambda curve_id: self.analysis_session.resample_curve(curve_id, rate),
        )

    def detrend_selected(self, mode: str) -> list[NumericCurve]:
        label = "Remove mean" if mode == "constant" else "Remove linear trend"
        return self._run_derivation(
            label, lambda curve_id: self.analysis_session.detrend_curve(curve_id, mode)
        )

    def smooth_selected(self) -> list[NumericCurve]:
        window = self.smooth_window.value()
        if window % 2 == 0:
            window += 1
            self.smooth_window.setValue(window)
        return self._run_derivation(
            "Moving average",
            lambda curve_id: self.analysis_session.smooth_curve(curve_id, window),
        )

    def _filter_type_changed(self, _index: int = -1) -> None:
        # Keep both cutoff values directly editable.  The selected filter type
        # determines which value(s) the numerical backend consumes.
        self.filter_low.setEnabled(True)
        self.filter_high.setEnabled(True)

    def filter_selected(self) -> list[NumericCurve]:
        kind = str(self.filter_type.currentData())
        # UI terminology matches the reference program: "Low cutoff" is
        # the low-pass edge, while "High cutoff" is the high-pass edge.
        low_pass_hz = self.filter_low.value()
        high_pass_hz = self.filter_high.value()
        return self._run_derivation(
            "Filter",
            lambda curve_id: self.analysis_session.filter_curve(
                curve_id,
                kind,
                low_hz=high_pass_hz,
                high_hz=low_pass_hz,
                order=self.filter_order.value(),
            ),
        )

    def fft_selected(self) -> list[NumericCurve]:
        return self._run_spectrum("FFT", self.analysis_session.fft_curve)

    def psd_selected(self) -> list[NumericCurve]:
        block = int(self.psd_block.currentData())
        return self._run_spectrum(
            "Welch PSD",
            lambda curve_id: self.analysis_session.psd_curve(curve_id, block),
        )

    def _run_spectrum(
        self, label: str, operation: Callable[[str], NumericCurve]
    ) -> list[NumericCurve]:
        """Create/replace frequency views without destroying time curves."""

        if self.analysis_session is None:
            return []
        source_ids = self._selected_time_ids()
        if not source_ids:
            return []
        updated: list[NumericCurve] = []
        errors: list[str] = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for curve_id in source_ids:
                source = self.analysis_session.get_curve(curve_id)
                try:
                    result = operation(curve_id)
                    previous = self._frequency_views.get(curve_id)
                    self._frequency_views[curve_id] = result.curve_id
                    if previous is not None:
                        self.analysis_session.delete_curve(previous)
                    updated.append(
                        replace(result, curve_id=curve_id, name=source.name)
                    )
                except MemoryError:
                    errors.append(f"{source.name}: operation needs too much memory")
                except (ValueError, RuntimeError) as exc:
                    errors.append(f"{source.name}: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if updated:
            updated_ids = {curve.curve_id for curve in updated}
            self.clear_annotations(domain="frequency")
            self._display_cache.clear()
            self._refresh_curve_tree(select_ids=updated_ids, make_visible=updated_ids)
            self._refresh_plots(auto_range=True)
            self.plot_tabs.setCurrentIndex(1)
            self.status_label.setText(
                f"{label}: updated {len(updated)} frequency view(s)."
            )
        if errors:
            self._show_error("\n".join(errors))
        return updated

    def rename_selected_curve(self) -> None:
        if self.analysis_session is None:
            return
        selected = self.selected_curve_ids()
        if len(selected) != 1:
            return
        curve = self.analysis_session.get_curve(selected[0])
        if not curve.derived:
            self.status_label.setText("Original curves cannot be renamed.")
            return
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Rename derived curve", "Curve name", text=curve.name
        )
        if not accepted:
            return
        try:
            updated = self.analysis_session.rename_curve(curve.curve_id, name)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._refresh_curve_tree(select_ids={updated.curve_id})
        self._refresh_plots()

    def delete_selected_curves(self) -> None:
        if self.analysis_session is None:
            return
        selected = [
            curve_id
            for curve_id in self.selected_curve_ids()
            if self.analysis_session.get_curve(curve_id).derived
        ]
        errors: list[str] = []
        for curve_id in reversed(selected):
            try:
                self.analysis_session.delete_curve(curve_id)
                self._visible_ids.discard(curve_id)
            except KeyError:
                # A selected parent removes its complete derived subtree.
                continue
            except ValueError as exc:
                errors.append(str(exc))
        self._prune_annotations()
        self._display_cache.clear()
        self._refresh_curve_tree()
        self._refresh_plots(auto_range=True)
        if errors:
            self._show_error("\n".join(errors))

    def _prune_annotations(self) -> None:
        if self.analysis_session is None:
            return
        valid = {curve.curve_id for curve in self.analysis_session.curves}
        self._visible_ids.intersection_update(valid)
        for tip_id, tip in list(self._data_tips.items()):
            if tip["curve_id"] not in valid:
                self._remove_data_tip(tip_id)
        for domain in ("time", "frequency"):
            cursor = self._cursor_state.get(domain)
            nearest = cursor.get("nearest") if cursor else None
            if nearest is not None and nearest[0].curve_id not in valid:
                self._remove_cursor(domain)
            for name, marker in list(self._markers[domain].items()):
                if marker["curve_id"] not in valid:
                    self._remove_marker(domain, name)

    def export_selected_to(self, path: str | Path) -> Path:
        if self.analysis_session is None:
            raise ValueError("no record is loaded")
        selected = self.selected_curve_ids()
        if not selected:
            raise ValueError("select at least one curve to export")
        return self.analysis_session.export_curves(
            path,
            selected,
            frequency_decibels=False,
        )

    def export_selected_dialog(self) -> None:
        if self.analysis_session is None:
            self.status_label.setText("Open a record before exporting curves.")
            return
        selected = self.selected_curve_ids()
        if not selected:
            self.status_label.setText("Select one or more curves to export.")
            return
        source = Path(self.analysis_session.record.source) if self.analysis_session.record.source else Path("logging_record.csv")
        proposed = source.with_name(f"{source.stem}_analysis.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export selected curves",
            str(proposed),
            "CSV (*.csv);;All files (*.*)",
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv"
        try:
            output = self.export_selected_to(path)
        except (OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self.status_label.setText(f"Exported selected curves to {output}")

    def _show_error(self, message: str) -> None:
        self.status_label.setText(str(message).replace("\n", " · "))
        QtWidgets.QMessageBox.critical(self, "Records / Plot", str(message))
