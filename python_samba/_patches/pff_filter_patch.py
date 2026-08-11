"""PFF filter page patch -- replaces the single 4x8 grid with three separate
filter matrices matching the C# SAMBA19xUI PFFFilterPage layout:

  RefFilterMatrix  4 sources x 3 stages  (stage 0..2)
  SecFilterMatrix  4 sources x 3 stages  (stage 3..5)
  ErrFilterMatrix  3 axes      x 2 stages  (stage 6..7)

Plus 4 source combo boxes, 4 adaptive-rate text fields, 4 status rows
(3 LED buttons each for Ztp/Yrp/Xpr), multiplier/offset fields, Threshold,
UsedGainNum, and LED buttons for Active/Adaptive status.

All methods are module-level functions so they can be monkey-patched onto
a MainWindow instance:

    import pff_filter_patch
    MainWindow._build_pff_filter_page = pff_filter_patch._build_pff_filter_page
    ...

Usage:
    from python_samba.ui.main_window import MainWindow
    import pff_filter_patch
    pff_filter_patch.patch_main_window(MainWindow)
"""

from __future__ import annotations

from python_samba.protocol.codes import FilterType, filter_small_name
from python_samba.protocol.commands import FilterStage
from python_samba.ui.classic_widgets import (
    FilterStageCell,
    FlatPush,
    GroupPanel,
    LedIndicator,
    RockerButton,
    SciEdit,
    ClassicFilterPanel,
)
from python_samba.ui.widgets import FilterDlg, FilterEditor
from python_samba.ui.main_window import PNEU_AXES_NAMES

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc


# --- helper: create a small LED dot ---
def _make_led(diameter: int = 12, *, clickable: bool = False) -> LedIndicator:
    led = LedIndicator(diameter=diameter, clickable=clickable)
    led.set_on(False)
    return led


# ======================================================================
# _build_pff_filter_page
# ======================================================================

def _build_pff_filter_page(self) -> QtWidgets.QWidget:
    """PFF filter page -- three separate filter grids (Ref 4x3, Sec 4x3, Err 3x2).

    Matching C# SAMBA19xUI PFFFilterPage layout:
      - 4 source combo boxes
      - 4 adaptive-rate text fields
      - 4 status rows (Ztp/Yrp/Xpr LEDs each)
      - RefFilterMatrix  (4x3)
      - SecFilterMatrix  (4x3)
      - ErrFilterMatrix  (3x2)
      - Threshold, UsedGainNum
      - Active LED, Adaptive LED
      - Multiplier/offset fields (in a collapsible Config section)
      - ClassicFilterPanel at the bottom
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # ---- top status row (Active/Adaptive LED + Threshold + Used gains) ----
    top = QtWidgets.QHBoxLayout()

    self.pff_active_led = LedIndicator(diameter=16)
    self.pff_active_led.set_on(False)
    c1 = QtWidgets.QVBoxLayout()
    c1.addWidget(self.pff_active_led, 0, QtCore.Qt.AlignHCenter)
    c1.addWidget(QtWidgets.QLabel("PFF active"))
    top.addLayout(c1)

    top.addSpacing(8)

    self.pff_adaptive_led = LedIndicator(diameter=16)
    self.pff_adaptive_led.set_on(False)
    c2 = QtWidgets.QVBoxLayout()
    c2.addWidget(self.pff_adaptive_led, 0, QtCore.Qt.AlignHCenter)
    c2.addWidget(QtWidgets.QLabel("Adaptive"))
    top.addLayout(c2)

    top.addSpacing(12)

    top.addWidget(QtWidgets.QLabel("Threshold:"))
    self.pff_threshold = SciEdit("62")
    self.pff_threshold.setFixedWidth(50)
    self.pff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.pff_thr_slider.setRange(0, 100)
    self.pff_thr_slider.setValue(62)
    self.pff_thr_slider.setFixedWidth(100)
    top.addWidget(self.pff_thr_slider)
    top.addWidget(QtWidgets.QLabel("Used gains:"))
    self.pff_used_gains = SciEdit("5")
    self.pff_used_gains.setFixedWidth(40)
    self.pff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.pff_gains_slider.setRange(1, 16)
    self.pff_gains_slider.setValue(5)
    self.pff_gains_slider.setFixedWidth(80)
    top.addWidget(self.pff_gains_slider)
    top.addStretch(1)
    root.addLayout(top)

    # ---- source combo boxes + adaptive rates + status rows ----
    src_grid = QtWidgets.QGridLayout()
    src_grid.setSpacing(4)

    src_grid.addWidget(QtWidgets.QLabel("Source"), 0, 0)
    src_grid.addWidget(QtWidgets.QLabel("Adaptive rate"), 0, 1)
    src_grid.addWidget(QtWidgets.QLabel("Status (Ztp / Yrp / Xrp)"), 0, 2, 1, 3)

    self.pff_source_cbxs: list[QtWidgets.QComboBox] = []
    self.pff_adaptive_rate_edits: list[SciEdit] = []
    self.pff_status_rows: list[list[LedIndicator]] = []

    for i in range(4):
        # Source combo
        cbx = QtWidgets.QComboBox()
        cbx.addItems([f"InpXPOS", f"InpYPOS", f"InpZPOS", f"InpXACC",
                       f"InpYACC", f"InpZACC", f"InpXFRC", f"InpYFRC",
                       f"InpZFRC", f"InpPROX1", f"InpPROX2", f"InpPROX3",
                       f"InpPROX4", f"InpPROXH1", f"InpPROXH2"])
        self.pff_source_cbxs.append(cbx)
        src_grid.addWidget(QtWidgets.QLabel(f"Ch{i+1}:"), i + 1, 0)
        src_grid.addWidget(cbx, i + 1, 1)

        # Adaptive rate
        rate_ed = SciEdit("0.00000e+000")
        rate_ed.setFixedWidth(100)
        self.pff_adaptive_rate_edits.append(rate_ed)
        src_grid.addWidget(rate_ed, i + 1, 2)

        # Status row: 3 LEDs (Ztp, Yrp, Xrp)
        row_leds: list[LedIndicator] = []
        row_w = QtWidgets.QWidget()
        row_lay = QtWidgets.QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(4)
        for label in ("Ztp", "Yrp", "Xrp"):
            v = QtWidgets.QVBoxLayout()
            led = _make_led(12)
            row_leds.append(led)
            v.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            v.addWidget(QtWidgets.QLabel(label), 0, QtCore.Qt.AlignHCenter)
            row_lay.addLayout(v)
        self.pff_status_rows.append(row_leds)
        src_grid.addWidget(row_w, i + 1, 3, 1, 2)

    root.addLayout(src_grid)

    # ---- Ref filter matrix: 4 sources x 3 stages ----
    g_ref = GroupPanel("Reference filter")
    ref_grid = QtWidgets.QGridLayout(g_ref)
    ref_grid.setSpacing(2)
    ref_grid.addWidget(QtWidgets.QLabel("Source \\ Stage"), 0, 0)
    for j in range(3):
        lbl = QtWidgets.QLabel(f"Fil{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        ref_grid.addWidget(lbl, 0, j + 1)
    self.pff_ref_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for src in range(4):
        lbl = QtWidgets.QLabel(f"Ch{src+1}")
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        ref_grid.addWidget(lbl, src + 1, 0)
        for st in range(3):
            cell = FilterStageCell(st, f"S{st}", width=36, height=42)
            cell.clicked.connect(lambda s=st, a=src: _on_pff_ref_cell_clicked(self, a, s))
            self.pff_ref_buttons[(src, st)] = cell
            ref_grid.addWidget(cell, src + 1, st + 1)
    root.addWidget(g_ref)

    # ---- Sec filter matrix: 4 sources x 3 stages ----
    g_sec = GroupPanel("Secondary filter")
    sec_grid = QtWidgets.QGridLayout(g_sec)
    sec_grid.setSpacing(2)
    sec_grid.addWidget(QtWidgets.QLabel("Source \\ Stage"), 0, 0)
    for j in range(3):
        lbl = QtWidgets.QLabel(f"Fil{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        sec_grid.addWidget(lbl, 0, j + 1)
    self.pff_sec_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for src in range(4):
        lbl = QtWidgets.QLabel(f"Ch{src+1}")
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        sec_grid.addWidget(lbl, src + 1, 0)
        for st in range(3):
            cell = FilterStageCell(st + 3, f"S{st+3}", width=36, height=42)
            cell.clicked.connect(lambda s=st, a=src: _on_pff_sec_cell_clicked(self, a, s))
            self.pff_sec_buttons[(src, st)] = cell
            sec_grid.addWidget(cell, src + 1, st + 1)
    root.addWidget(g_sec)

    # ---- Err filter matrix: 3 axes x 2 stages ----
    g_err = GroupPanel("Error filter")
    err_grid = QtWidgets.QGridLayout(g_err)
    err_grid.setSpacing(2)
    err_grid.addWidget(QtWidgets.QLabel("Axis \\ Stage"), 0, 0)
    for j in range(2):
        lbl = QtWidgets.QLabel(f"Fil{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        err_grid.addWidget(lbl, 0, j + 1)
    self.pff_err_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for ax in range(3):
        lbl = QtWidgets.QLabel(PNEU_AXES_NAMES[ax])
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        err_grid.addWidget(lbl, ax + 1, 0)
        for st in range(2):
            cell = FilterStageCell(st + 6, f"S{st+6}", width=36, height=42)
            cell.clicked.connect(lambda s=st, a=ax: _on_pff_err_cell_clicked(self, a, s))
            self.pff_err_buttons[(ax, st)] = cell
            err_grid.addWidget(cell, ax + 1, st + 1)
    root.addWidget(g_err)

    # ---- hidden fields for dialog state ----
    self.pff_filter = FilterEditor(["axis via spin"], max_stage=7)
    self.pff_filter.setVisible(False)
    self.pff_filter.axis.setEnabled(False)

    self.pff_filter_panel = ClassicFilterPanel("PFF filter (click a cell above)")
    self.pff_filter_panel.read_clicked.connect(self.on_pff_filter_read_classic)
    self.pff_filter_panel.write_clicked.connect(self.on_pff_filter_write_classic)
    self.pff_filter_panel.stage_changed.connect(self._sync_pff_panel_to_editor)
    root.addWidget(self.pff_filter_panel)

    # hidden helper fields
    self.pff_axis = QtWidgets.QSpinBox()
    self.pff_axis.setRange(0, 2)
    self.pff_axis.setVisible(False)
    self.pff_source = QtWidgets.QSpinBox()
    self.pff_source.setRange(0, 7)
    self.pff_source.setVisible(False)
    self.pff_stage = QtWidgets.QSpinBox()
    self.pff_stage.setRange(0, 7)
    self.pff_stage.setVisible(False)
    self.pff_cfg = SciEdit()
    self.pff_cfg.setVisible(False)
    self.pff_params = SciEdit()
    self.pff_params.setVisible(False)
    self.pff_inputs = SciEdit()
    self.pff_inputs.setVisible(False)
    self.pff_gains = SciEdit()
    self.pff_gains.setVisible(False)

    # cache for the currently selected filter type
    self._pff_current_filter_type = 0
    self._pff_current_source = 0
    self._pff_current_stage = 0
    self._pff_current_axis = 0
    self._pff_matrix_kind = "ref"  # "ref", "sec", or "err"

    # ---- action buttons ----
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read all filters", on_pff_read_all_filters),
        ("Read filter", self.on_pff_filter_read_classic),
        ("Write filter...", self.on_pff_filter_write_classic),
        ("Write gains...", self.on_pff_write_gains_classic),
        ("Reset FIR...", self.on_pff_reset),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)

    # ---- store which button dict is active for cell updates ----
    self._pff_active_buttons = self.pff_ref_buttons

    return w


# ======================================================================
# Screenshot-oriented reference layout
# ======================================================================

def _build_pff_filter_page_reference(self) -> QtWidgets.QWidget:
    """Build the compact horizontal PFF page used by SAMBA19xUI."""
    from python_samba.ui.main_window import SidebarLoopButton

    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(8)

    matrix_row = QtWidgets.QHBoxLayout()
    matrix_row.setSpacing(10)

    primary = GroupPanel("Reference/Secondary Path Filters")
    primary.setFixedSize(1215, 465)
    grid = QtWidgets.QGridLayout(primary)
    grid.setContentsMargins(12, 18, 10, 12)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(3)

    headers = (
        ("Channel", 0, 1),
        ("Reference Filters", 1, 3),
        ("Secondary Filters", 4, 3),
        ("Config. Matrix", 7, 3),
        ("FFRate", 10, 1),
    )
    for text, column, span in headers:
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(label, 0, column, 1, span)

    self.pff_source_cbxs = []
    self.pff_adaptive_rate_edits = []
    self.pff_status_rows = []
    self.pff_ref_buttons = {}
    self.pff_sec_buttons = {}
    source_names = [
        "InpXPOS", "InpYPOS", "InpZPOS", "InpXACC", "InpYACC", "InpZACC",
        "InpXFRC", "InpYFRC", "InpZFRC", "InpPROX1", "InpPROX2",
        "InpPROX3", "InpPROX4", "InpPROXH1", "InpPROXH2",
    ]

    for row in range(4):
        cbx = QtWidgets.QComboBox()
        cbx.addItems(source_names)
        cbx.setFixedSize(205, 48)
        cbx.currentIndexChanged.connect(
            lambda _index, source=row: self._on_pff_inputs_changed(source)
        )
        self.pff_source_cbxs.append(cbx)
        grid.addWidget(cbx, row + 1, 0)

        for stage in range(3):
            cell = FilterStageCell(stage, "----", width=92, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _source=row: _on_pff_ref_cell_clicked(
                    self, _source, _stage
                )
            )
            self.pff_ref_buttons[(row, stage)] = cell
            grid.addWidget(cell, row + 1, stage + 1)

        for stage in range(3):
            cell = FilterStageCell(stage + 3, "----", width=92, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _source=row: _on_pff_sec_cell_clicked(
                    self, _source, _stage
                )
            )
            self.pff_sec_buttons[(row, stage)] = cell
            grid.addWidget(cell, row + 1, stage + 4)

        leds = []
        for axis_offset, axis_name in enumerate(("Ztp", "Yrp", "Xpr")):
            holder = QtWidgets.QWidget()
            column = QtWidgets.QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            axis_label = QtWidgets.QLabel(axis_name)
            axis_label.setAlignment(QtCore.Qt.AlignCenter)
            led = _make_led(38, clickable=True)
            led.setToolTip(
                f"Toggle PFF source {row + 1} output {axis_name}"
            )
            led.clicked.connect(
                lambda source=row, axis=axis_offset: self._on_pff_matrix_clicked(
                    source, axis
                )
            )
            column.addWidget(axis_label)
            column.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            leds.append(led)
            grid.addWidget(holder, row + 1, axis_offset + 7)
        self.pff_status_rows.append(leds)

        rate = SciEdit("0")
        rate.setFixedSize(170, 42)
        rate.editingFinished.connect(
            lambda source=row: self._on_pff_rate_changed(source)
        )
        self.pff_adaptive_rate_edits.append(rate)
        grid.addWidget(rate, row + 1, 10)

    matrix_row.addWidget(primary, 0, QtCore.Qt.AlignTop)

    error = GroupPanel("Error Path Filters")
    error.setFixedSize(320, 465)
    err_grid = QtWidgets.QGridLayout(error)
    err_grid.setContentsMargins(12, 80, 12, 24)
    err_grid.setSpacing(3)
    err_grid.addWidget(QtWidgets.QLabel("Fil1"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    err_grid.addWidget(QtWidgets.QLabel("Fil2"), 0, 2, alignment=QtCore.Qt.AlignCenter)
    self.pff_err_buttons = {}
    for axis, name in enumerate(("Ztpneu", "Yrpneu", "Xrpneu")):
        err_grid.addWidget(QtWidgets.QLabel(name), axis + 1, 0)
        for stage in range(2):
            cell = FilterStageCell(stage + 6, "----", width=92, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _axis=axis: _on_pff_err_cell_clicked(
                    self, _axis, _stage
                )
            )
            self.pff_err_buttons[(axis, stage)] = cell
            err_grid.addWidget(cell, axis + 1, stage + 1)
    matrix_row.addWidget(error, 0, QtCore.Qt.AlignTop)
    matrix_row.addStretch(1)
    root.addLayout(matrix_row)

    lower = QtWidgets.QHBoxLayout()
    lower.setSpacing(14)
    left = QtWidgets.QVBoxLayout()
    diagnostic = GroupPanel("Diagnostic Signals")
    diagnostic.setFixedSize(520, 120)
    diag = QtWidgets.QGridLayout(diagnostic)
    diag.addWidget(QtWidgets.QLabel("Diag0"), 0, 0, alignment=QtCore.Qt.AlignCenter)
    diag.addWidget(QtWidgets.QLabel("Diag1"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    diag0 = FlatPush("X1FB")
    diag1 = FlatPush("X1FB")
    diag.addWidget(diag0, 1, 0)
    diag.addWidget(diag1, 1, 1)
    left.addWidget(diagnostic)

    loop_status = GroupPanel("Pneumatic Individual Loop Status")
    loop_status.setFixedSize(400, 140)
    loop_row = QtWidgets.QHBoxLayout(loop_status)
    self.pff_individual_loop_leds = []
    for axis, name in enumerate(("Ztpneu", "Yrpneu", "Xrpneu")):
        col = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(name)
        label.setAlignment(QtCore.Qt.AlignCenter)
        lamp = SidebarLoopButton()
        lamp.set_on(False)
        lamp.setFixedSize(68, 66)
        lamp.setToolTip(f"Toggle pneumatic individual loop {name}")
        lamp.clicked.connect(
            lambda _checked=False, _axis=axis: self._on_pff_individual_loop_clicked(
                _axis
            )
        )
        self.pff_individual_loop_leds.append(lamp)
        col.addWidget(label)
        col.addWidget(lamp)
        loop_row.addLayout(col)
    left.addWidget(loop_status)
    lower.addLayout(left)

    status = GroupPanel("Threshold/Gains Number")
    status.setFixedSize(325, 205)
    form = QtWidgets.QFormLayout(status)
    self.pff_threshold = SciEdit("0")
    self.pff_threshold.setFixedWidth(120)
    self.pff_used_gains = SciEdit("0")
    self.pff_used_gains.setFixedWidth(120)
    self.pff_active_led = SidebarLoopButton()
    self.pff_adaptive_led = SidebarLoopButton()
    self.pff_active_led.setToolTip("Toggle PFF active status (BSSTS bit 0x4000)")
    self.pff_adaptive_led.setToolTip(
        "Toggle PFF adaptive status (BSSTS bit 0x8000)"
    )
    self.pff_active_led.clicked.connect(
        lambda _checked=False: self._on_pff_status_button_clicked("active")
    )
    self.pff_adaptive_led.clicked.connect(
        lambda _checked=False: self._on_pff_status_button_clicked("adaptive")
    )
    form.addRow("Threshold [%]", self.pff_threshold)
    form.addRow("Gain Number", self.pff_used_gains)
    form.addRow("Active Status", self.pff_active_led)
    form.addRow("Adaptive Status", self.pff_adaptive_led)
    self.pff_threshold.editingFinished.connect(self._on_pff_config_changed)
    self.pff_used_gains.editingFinished.connect(self._on_pff_config_changed)
    lower.addWidget(status, 0, QtCore.Qt.AlignTop)
    lower.addStretch(1)
    root.addLayout(lower)
    root.addStretch(1)

    # Compatibility widgets used by the existing protocol handlers.
    self.pff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.pff_thr_slider.setRange(0, 100)
    self.pff_thr_slider.hide()
    self.pff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.pff_gains_slider.setRange(1, 16)
    self.pff_gains_slider.hide()
    self.pff_filter = FilterEditor(["axis via spin"], max_stage=7)
    self.pff_filter.hide()
    self.pff_filter.axis.setEnabled(False)
    self.pff_filter_panel = ClassicFilterPanel("PFF filter")
    self.pff_filter_panel.read_clicked.connect(self.on_pff_filter_read_classic)
    self.pff_filter_panel.write_clicked.connect(self.on_pff_filter_write_classic)
    self.pff_filter_panel.stage_changed.connect(self._sync_pff_panel_to_editor)
    self.pff_filter_panel.hide()
    for name, widget in (
        ("pff_axis", QtWidgets.QSpinBox()),
        ("pff_source", QtWidgets.QSpinBox()),
        ("pff_stage", QtWidgets.QSpinBox()),
        ("pff_cfg", SciEdit()),
        ("pff_params", SciEdit()),
        ("pff_inputs", SciEdit()),
        ("pff_gains", SciEdit()),
    ):
        setattr(self, name, widget)
        widget.hide()
    self.pff_axis.setRange(0, 2)
    self.pff_source.setRange(0, 7)
    self.pff_stage.setRange(0, 7)
    self._pff_current_filter_type = 0
    self._pff_current_source = 0
    self._pff_current_stage = 0
    self._pff_current_axis = 0
    self._pff_matrix_kind = "ref"
    self._pff_active_buttons = self.pff_ref_buttons
    return w


def _run_confirmed_pff_write(self, title: str, summary: str, callback) -> None:
    def work() -> None:
        self._require_session()
        assert self.gate
        if not self._confirm_write(summary):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            callback()
        finally:
            self._set_writable(True)
    self._run(title, work)


def _parse_pff_output_mask(value) -> int:
    """FGPPF documents Outputs as hexadecimal text without a prefix."""
    return int(str(value).strip(), 16)


def _on_pff_matrix_clicked(self, source: int, axis: int) -> None:
    """Toggle one PFF Config.Matrix output bit while preserving its rate."""
    if not 0 <= source < 4 or not 0 <= axis < 3:
        raise ValueError(f"PFF matrix index out of range: source={source}, axis={axis}")
    if not self.session or not self.session.connected:
        return

    def send() -> None:
        session = self._require_session()
        current = session.get_pff_parameters(source)
        if len(current) < 2:
            raise RuntimeError(
                f"FGPPF source {source} returned {len(current)} fields; expected 2"
            )
        outputs = _parse_pff_output_mask(current[0]) ^ (1 << axis)
        session.set_pff_parameters(source, outputs, float(current[1]))
        self.pff_status_rows[source][axis].set_on(bool(outputs & (1 << axis)))

    _run_confirmed_pff_write(
        self,
        "Toggle PFF config matrix",
        f"PFF source {source + 1}, output axis {axis + 1}",
        send,
    )


def _set_pff_status_buttons_from_system(self, system: int) -> None:
    """Apply legacy PFF Active/Adaptive states from BGSTS."""
    self.pff_active_led.set_on(bool(system & 0x4000))
    self.pff_adaptive_led.set_on(bool(system & 0x8000))


def _on_pff_status_button_clicked(self, kind: str) -> None:
    """Toggle PFF Active/Adaptive through BGSTS/BSSTS."""
    bits = {"active": 0x4000, "adaptive": 0x8000}
    try:
        bit = bits[kind]
    except KeyError as exc:
        raise ValueError(f"unknown PFF status button {kind!r}") from exc
    if not self.session or not self.session.connected:
        return

    def send() -> None:
        session = self._require_session()
        loop = session.get_loop_status()
        system = loop.system ^ bit
        session.set_loop_status(loop.individual, system)
        _set_pff_status_buttons_from_system(self, system)
        self._refresh_status_loop_state()

    _run_confirmed_pff_write(
        self,
        "Toggle PFF status",
        f"BSSTS PFF {kind} bit 0x{bit:X}",
        send,
    )


def _set_pff_individual_loop_buttons(self, pneumatic: int) -> None:
    """Apply the BGSST pneumatic word to the three PFF page buttons."""
    for axis, lamp in enumerate(getattr(self, "pff_individual_loop_leds", ())):
        lamp.set_on(bool(int(pneumatic) & (1 << axis)))


def _on_pff_individual_loop_clicked(self, axis: int) -> None:
    """Toggle one pneumatic individual-loop bit through BGSST/BSSST."""
    if not 0 <= axis < 3:
        raise ValueError(f"PFF individual-loop axis out of range: {axis}")
    self._on_axis_individual_loop_clicked("pneumatic", axis)


def _on_pff_inputs_changed(self, _source: int) -> None:
    if not self.session or not self.session.connected:
        return
    values = [combo.currentIndex() for combo in self.pff_source_cbxs]
    _run_confirmed_pff_write(
        self, "Write PFF inputs", f"PFF input mapping: {values}",
        lambda: self._require_session().set_pff_inputs(values),
    )


def _on_pff_rate_changed(self, source: int) -> None:
    if not self.session or not self.session.connected:
        return
    rate = float(self.pff_adaptive_rate_edits[source].text())

    def send() -> None:
        s = self._require_session()
        current = s.get_pff_parameters(source)
        outputs = current[0] if current else 0
        s.set_pff_parameters(source, outputs, rate)

    _run_confirmed_pff_write(
        self, "Write PFF rate", f"PFF source {source + 1} adaptation rate={rate}", send,
    )


def _on_pff_config_changed(self) -> None:
    if not self.session or not self.session.connected:
        return
    gains = int(float(self.pff_used_gains.text()))
    threshold = int(float(self.pff_threshold.text()))
    _run_confirmed_pff_write(
        self, "Write PFF config", f"PFF gains={gains}, threshold={threshold}%",
        lambda: self._require_session().set_pff_config(gains, threshold),
    )


# ======================================================================
# Cell-click handlers
# ======================================================================

def _on_pff_ref_cell_clicked(self, source: int, stage: int) -> None:
    """User clicked a reference filter cell (stage 0..2, source 0..3).

    RCI mapping: GetPFFStageRefFilter / SetPFFStageRefFilter
      _RCI.GetPFFStageFilter(0, Source, Stage, out type, par)  where stage=0..2
      _RCI.SetPFFStageFilter(0, Source, Stage, type, par)
    """
    _pff_open_filter_dlg(
        self, source, stage,
        axis=0,
        matrix_kind="ref",
        button_dict=self.pff_ref_buttons,
        stage_offset=0,
        dlg_title=f"PFF Ref Filter — Source {source+1}, Stage {stage}",
    )


def _on_pff_sec_cell_clicked(self, source: int, stage: int) -> None:
    """User clicked a secondary filter cell (stage 0..2, source 0..3).

    RCI mapping: GetPFFStageSecFilter / SetPFFStageSecFilter
      _RCI.GetPFFStageFilter(0, Source, Stage+3, out type, par)  where stage=0..2
      _RCI.SetPFFStageFilter(0, Source, Stage+3, type, par)
    """
    _pff_open_filter_dlg(
        self, source, stage,
        axis=0,
        matrix_kind="sec",
        button_dict=self.pff_sec_buttons,
        stage_offset=3,
        dlg_title=f"PFF Sec Filter — Source {source+1}, Stage {stage}",
    )


def _on_pff_err_cell_clicked(self, axis: int, stage: int) -> None:
    """User clicked an error filter cell (stage 0..1, axis 0..2).

    RCI mapping: GetPFFStageErrFilter / SetPFFStageErrFilter
      _RCI.GetPFFStageFilter(Axis, 0, Stage+6, out type, par)  where stage=0..1
      _RCI.SetPFFStageFilter(Axis, 0, Stage+6, type, par)
    """
    _pff_open_filter_dlg(
        self, 0, stage,
        axis=axis,
        matrix_kind="err",
        button_dict=self.pff_err_buttons,
        stage_offset=6,
        dlg_title=f"PFF Err Filter — Axis {axis}, Stage {stage}",
        is_err_matrix=True,
    )


# ======================================================================
# Shared dialog opener
# ======================================================================

def _pff_open_filter_dlg(
    self,
    source: int,
    stage: int,
    *,
    axis: int,
    matrix_kind: str,
    button_dict: dict[tuple[int, int], FilterStageCell],
    stage_offset: int,
    dlg_title: str,
    is_err_matrix: bool = False,
) -> None:
    """Open the FilterDlg for a PFF filter cell.

    Parameters
    ----------
    source : int
        Source index (0..3) for ref/sec matrices; 0 for err matrix.
    stage : int
        Logical stage index within the matrix (0..2 for ref/sec, 0..1 for err).
    axis : int
        Axis index (0 for ref/sec, 0..2 for err).
    matrix_kind : str
        "ref", "sec", or "err".
    button_dict : dict
        The button dict to update after change.
    stage_offset : int
        Offset added to stage for RCI call (0 for ref, 3 for sec, 6 for err).
    dlg_title : str
        Dialog window title.
    is_err_matrix : bool
        If True, the dialog shows axes instead of sources.
    """
    # Cache state
    self._pff_current_source = source
    self._pff_current_stage = stage
    self._pff_current_axis = axis
    self._pff_matrix_kind = matrix_kind
    self._pff_active_buttons = button_dict

    # Update hidden spinboxes so existing on_pff_filter_read/write work
    self.pff_axis.setValue(axis)
    self.pff_source.setValue(source)
    self.pff_stage.setValue(stage)

    # Read current filter from session if connected
    if self.session and self.session.connected:
        if is_err_matrix:
            # Err filter: axis varies, source=0, rpc_stage = stage+6
            rpc_stage = stage + stage_offset
            rpc_axis = axis
            rpc_source = 0
        else:
            # Ref/Sec filter: axis=0, source varies, rpc_stage = stage+stage_offset
            rpc_stage = stage + stage_offset
            rpc_axis = 0
            rpc_source = source
        try:
            fs = self.session.get_pff_filter(rpc_axis, rpc_source, rpc_stage)
            self.pff_filter.set_stage(fs)
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
        except Exception:
            pass

    # Build dialog
    if is_err_matrix:
        axis_labels = PNEU_AXES_NAMES
        dlg = FilterDlg(
            axis_labels, max_stage=7,
            show_all_axes=True, show_all_sources=False, parent=self,
        )
        dlg.axis_cbx.setCurrentIndex(axis)
        # Disable axis selection for err matrix (each cell is axis-specific)
        dlg.axis_cbx.setEnabled(False)
    else:
        axis_labels = [f"src {i}" for i in range(4)]
        dlg = FilterDlg(
            axis_labels, max_stage=7,
            show_all_axes=True, show_all_sources=True, parent=self,
        )
        dlg.axis_cbx.setCurrentIndex(source)
        # Disable axis selection (axis is always 0 for ref/sec)
        dlg.axis_cbx.setEnabled(False)

    dlg.setWindowTitle(dlg_title)
    fs = self.pff_filter.to_stage()
    dlg.set_stage(fs)

    def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
        if not isinstance(new_stage, FilterStage):
            return
        self.pff_filter.set_stage(new_stage)
        self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
        _pff_update_cell_text(self, source, stage, button_dict)

        if is_err_matrix:
            # Err matrix: write to all axes if requested
            if all_axes:
                for ax in range(3):
                    _write_pff_err_filter(self, ax, stage, stage_offset, new_stage)
                    _pff_update_cell_text(self, ax, stage, button_dict, key_axis=ax)
            else:
                _write_pff_err_filter(self, axis, stage, stage_offset, new_stage)
        else:
            # Ref / Sec matrix
            _write_pff_ref_sec_filter(
                self, source, stage, stage_offset, matrix_kind, new_stage,
            )
            if all_axes:
                # "all axes" in C# context means "all sources" for ref/sec
                for src in range(4):
                    _write_pff_ref_sec_filter(
                        self, src, stage, stage_offset, matrix_kind, new_stage,
                    )
                    _pff_update_cell_text(self, src, stage, button_dict)

    dlg.filterChanged.connect(on_dlg_changed)
    dlg.exec()
    dlg.deleteLater()


# ======================================================================
# Write helpers
# ======================================================================

def _write_pff_ref_sec_filter(
    self, source: int, stage: int, stage_offset: int, matrix_kind: str,
    fs: FilterStage,
) -> None:
    """Write a ref or sec filter via session.set_pff_filter.

    RCI equivalent:
      Ref: _RCI.SetPFFStageFilter(0, Source, Stage, type, par)
      Sec: _RCI.SetPFFStageFilter(0, Source, Stage+3, type, par)
    """
    if not (self.session and self.session.connected):
        self.log_msg("Not connected, cannot write")
        return
    rpc_stage = stage + stage_offset
    try:
        self.session.set_pff_filter(0, source, rpc_stage, fs.filter_type, fs.params)
        self.log_msg(
            f"PFF {'Ref' if matrix_kind=='ref' else 'Sec'} source={source} "
            f"stage={rpc_stage} type={fs.type_name}"
        )
    except Exception as e:
        self.log_msg(f"Write failed: {e}")


def _write_pff_err_filter(
    self, axis: int, stage: int, stage_offset: int, fs: FilterStage,
) -> None:
    """Write an error filter via session.set_pff_filter.

    RCI equivalent:
      _RCI.SetPFFStageFilter(Axis, 0, Stage+6, type, par)
    """
    if not (self.session and self.session.connected):
        self.log_msg("Not connected, cannot write")
        return
    rpc_stage = stage + stage_offset
    try:
        self.session.set_pff_filter(axis, 0, rpc_stage, fs.filter_type, fs.params)
        self.log_msg(
            f"PFF Err axis={axis} stage={rpc_stage} type={fs.type_name}"
        )
    except Exception as e:
        self.log_msg(f"Write failed: {e}")


# ======================================================================
# Cell text update
# ======================================================================

def _pff_update_cell_text(
    self, source: int, stage: int,
    button_dict: dict[tuple[int, int], FilterStageCell],
    key_axis: int | None = None,
) -> None:
    """Update the text shown on a filter cell button."""
    key = (source, stage) if key_axis is None else (key_axis, stage)
    if key in button_dict:
        try:
            name = self.pff_filter.ftype.currentText().split(None, 1)[-1]
            short = name[:5] if len(name) > 5 else name
        except Exception:
            short = ""
        button_dict[key].set_info(short)


# ======================================================================
# Read-all-filters
# ======================================================================

def on_pff_read_all_filters(self) -> None:
    """Read all PFF filters across all three matrices.

    Matches C# UpdatePage():
      - Ref: 4 sources x 3 stages   (stage=0..2, axis=0)
      - Sec: 4 sources x 3 stages   (stage=3..5, axis=0)
      - Err: 3 axes x 2 stages      (stage=6..7, source=0)
      - Also reads PFF stage params, config, and inputs.
    """
    def work() -> None:
        s = self._require_session()
        first_stage = None
        entries = (
            [("ref", (src, st), (0, src, st)) for src in range(4) for st in range(3)]
            + [
                ("sec", (src, st), (0, src, st + 3))
                for src in range(4)
                for st in range(3)
            ]
            + [
                ("err", (ax, st), (ax, 0, st + 6))
                for ax in range(3)
                for st in range(2)
            ]
        )
        snapshot = s.get_pff_tuning_snapshot(
            [address for _, _, address in entries], source_count=4
        )
        try:
            filters = snapshot["filters"]
            button_groups = {
                "ref": self.pff_ref_buttons,
                "sec": self.pff_sec_buttons,
                "err": self.pff_err_buttons,
            }
            for (group, button_key, _), fs in zip(entries, filters):
                if group == "ref" and button_key == (0, 0):
                    first_stage = fs
                button_groups[group][button_key].set_info(fs.type_name[:5])
        except Exception as exc:
            for group, button_key, _ in entries:
                getattr(self, f"pff_{group}_buttons")[button_key].set_info("?")
            self.log_msg(f"PFF filter batch read: {exc}")

        # Show first filter in the editor
        try:
            if first_stage is None:
                raise RuntimeError("first PFF filter was not available")
            self.pff_filter.set_stage(first_stage)
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
        except Exception:
            pass

        # Source mapping, output matrix, adaptation rate and configuration.
        try:
            inputs = snapshot["inputs"]
            for index, combo in enumerate(self.pff_source_cbxs):
                if index < len(inputs) and 0 <= int(inputs[index]) < combo.count():
                    combo.blockSignals(True)
                    combo.setCurrentIndex(int(inputs[index]))
                    combo.blockSignals(False)
        except Exception as exc:
            self.log_msg(f"PFF inputs read: {exc}")

        for source, params in enumerate(snapshot["parameters"]):
            try:
                outputs = _parse_pff_output_mask(params[0]) if params else 0
                if len(params) > 1:
                    self.pff_adaptive_rate_edits[source].setText(str(params[1]))
                for axis, led in enumerate(self.pff_status_rows[source]):
                    led.set_on(bool(outputs & (1 << axis)))
            except Exception as exc:
                self.log_msg(f"PFF source {source + 1} parameters: {exc}")

        try:
            config = snapshot["config"]
            if config:
                self.pff_used_gains.setText(str(config[0]))
            if len(config) > 1:
                self.pff_threshold.setText(str(config[1]))
        except Exception as exc:
            self.log_msg(f"PFF config read: {exc}")

        loop = snapshot["loop"]
        _set_pff_status_buttons_from_system(self, loop.system)
        try:
            _position, pneumatic, _digital_in, _digital_out = snapshot[
                "axis_loop_status"
            ]
            _set_pff_individual_loop_buttons(self, pneumatic)
        except Exception as exc:
            self.log_msg(f"PFF individual-loop status read: {exc}")

        self.log_msg("PFF filters all (4x3 ref + 4x3 sec + 3x2 err) read")
    self._run("Read all PFF filters", work)


# ======================================================================
# Legacy-compatible wrappers (re-route to the new system)
# ======================================================================

def _pff_filter_cell_clicked_legacy(self, source: int, stage: int) -> None:
    """Legacy handler -- redirect based on stage number."""
    if stage < 3:
        _on_pff_ref_cell_clicked(self, source, stage)
    elif stage < 6:
        _on_pff_sec_cell_clicked(self, source, stage - 3)
    else:
        # stage 6 or 7 -- err filter, source is actually axis
        _on_pff_err_cell_clicked(self, source, stage - 6)


# ======================================================================
# Patch helper
# ======================================================================

def patch_main_window(MainWindowClass: type) -> None:
    """Monkey-patch all PFF filter page methods onto MainWindow."""
    MainWindowClass._build_pff_filter_page = _build_pff_filter_page_reference
    MainWindowClass._on_pff_filter_cell_clicked = _pff_filter_cell_clicked_legacy
    MainWindowClass.on_pff_read_all_filters = on_pff_read_all_filters
    # Replace classic wrappers that reference old widgets
    MainWindowClass.on_pff_filter_read_classic = _on_pff_filter_read_classic
    MainWindowClass.on_pff_filter_write_classic = _on_pff_filter_write_classic
    MainWindowClass.on_pff_read_classic = _on_pff_read_classic
    MainWindowClass.on_pff_write_gains_classic = _on_pff_write_gains_classic
    # Also expose the new handlers so they can be called directly
    MainWindowClass._on_pff_ref_cell_clicked = _on_pff_ref_cell_clicked
    MainWindowClass._on_pff_sec_cell_clicked = _on_pff_sec_cell_clicked
    MainWindowClass._on_pff_err_cell_clicked = _on_pff_err_cell_clicked
    MainWindowClass._pff_open_filter_dlg = _pff_open_filter_dlg
    MainWindowClass._pff_update_cell_text = _pff_update_cell_text
    MainWindowClass._run_confirmed_pff_write = _run_confirmed_pff_write
    MainWindowClass._on_pff_inputs_changed = _on_pff_inputs_changed
    MainWindowClass._on_pff_rate_changed = _on_pff_rate_changed
    MainWindowClass._on_pff_config_changed = _on_pff_config_changed
    MainWindowClass._on_pff_matrix_clicked = _on_pff_matrix_clicked
    MainWindowClass._on_pff_status_button_clicked = _on_pff_status_button_clicked
    MainWindowClass._set_pff_status_buttons_from_system = _set_pff_status_buttons_from_system
    MainWindowClass._on_pff_individual_loop_clicked = _on_pff_individual_loop_clicked
    MainWindowClass._set_pff_individual_loop_buttons = _set_pff_individual_loop_buttons


# ======================================================================
# Replacement classic wrappers (no legacy pff_gain_edits / pff_filter_buttons)
# ======================================================================

def _on_pff_filter_read_classic(self) -> None:
    """Read PFF filter -- update whichever button dict is active."""
    def work() -> None:
        src = self._pff_current_source if hasattr(self, '_pff_current_source') else 0
        stage = self._pff_current_stage if hasattr(self, '_pff_current_stage') else 0
        stage_i = self.pff_filter.stage_index()
        if stage_i < 0:
            stage_i = 0
        self.pff_filter.axis.setCurrentIndex(0)
        self.pff_filter.stage.setValue(stage_i)
        self.on_pff_filter_read()
        self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
        # Update active button dict
        if hasattr(self, '_pff_active_buttons'):
            _pff_update_cell_text(self, src, stage, self._pff_active_buttons)
    self._run("Read PFF filter", work)


def _on_pff_filter_write_classic(self) -> None:
    """Write PFF filter via panel -> editor -> session."""
    self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)
    self.on_pff_filter_write()


def _on_pff_read_classic(self) -> None:
    """Read the selected-source gain matrix on the companion Gains page."""
    self.on_pff_read_gains()


def _on_pff_write_gains_classic(self) -> None:
    """Write the selected-source gain matrix from the companion Gains page."""
    self.on_pff_write_gains_from_matrix()
