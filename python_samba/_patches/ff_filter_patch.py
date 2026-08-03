from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    ClassicFilterPanel,
    FilterStageBar,
    FilterStageCell,
    FlatPush,
    GroupPanel,
    IOSignalButton,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
)
from python_samba.ui.widgets import (
    VEL_AXIS_LABELS,
    FilterDlg,
    FilterEditor,
    MatrixEditor,
)
from python_samba.ui.main_window import VEL_AXES_NAMES


"""
FF Filter Page Patch
=====================
Replaces the single 7x8 FF filter grid with THREE separate filter grids matching
the SAMBA19xUI FFFilterPage C# code:

  - Ref 7x3  : Reference filters   (7 sources, 3 stages each)  → FGPFS(src, 0..2)
  - Sec 7x3  : Secondary filters   (7 sources, 3 stages each)  → FGPFS(src, 3..5)
  - Err 6x2  : Error filters       (6 velocity axes, 2 stages) → FGPFS(axis, 6..7)

Also adds 7 source combo boxes, 7 adaptive-rate text fields, 3 LED-style status
buttons (Active / Adaptive / UseRawInput), multiplier/offset/maxima fields,
threshold and used-gains fields, plus proper read_all / update methods.

Usage
-----
Replace the existing _build_ff_filter_page method in main_window.py with the one
below, and add the new handler methods to the MainWindow class.

The _build_ff_tab method and _build_ff_config_page method remain unchanged.
"""

# ---------------------------------------------------------------------------
# Replacement for MainWindow._build_ff_filter_page
# ---------------------------------------------------------------------------

def _build_ff_filter_page(self) -> QtWidgets.QWidget:
    """FF filter page — three separate filter grids matching SAMBA19xUI.

    Grids:
      - Ref 7x3  (7 sources × 3 stages: Fil0, Fil1, Fil2)
      - Sec 7x3  (7 sources × 3 stages: Fil0, Fil1, Fil2)
      - Err 6x2  (6 velocity axes × 2 stages: Fil1, Fil2)

    Also includes: 7 source combo boxes, 7 adaptive rate fields,
    3 LED buttons (Active/Adaptive/UseRawInput), multiplier/offset/maxima
    fields, threshold, used gains, and 7 status rows (6 LED indicators each).
    """
    from python_samba.ui.classic_widgets import (
        FilterStageCell, ClassicFilterPanel, FlatPush, SciEdit, GroupPanel,
    )

    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # ==================================================================
    # Top status bar — Active / Adaptive / UseRawInput LED buttons
    # ==================================================================
    top = QtWidgets.QHBoxLayout()
    self.ff_led_active = FlatPush("FF Active")
    self.ff_led_active.setCheckable(True)
    self.ff_led_active.setChecked(True)
    self.ff_led_active.setStyleSheet(
        "FlatPush:checked { background:#2a8a2a; color:#fff; font-weight:700; }"
        "FlatPush:!checked { background:#aaa; }"
    )
    self.ff_led_adapt = FlatPush("Adaptive")
    self.ff_led_adapt.setCheckable(True)
    self.ff_led_adapt.setChecked(True)
    self.ff_led_adapt.setStyleSheet(self.ff_led_active.styleSheet())
    self.ff_led_rawinput = FlatPush("Use Raw Input")
    self.ff_led_rawinput.setCheckable(True)
    self.ff_led_rawinput.setChecked(False)
    self.ff_led_rawinput.setStyleSheet(self.ff_led_active.styleSheet())

    top.addWidget(self.ff_led_active)
    top.addWidget(self.ff_led_adapt)
    top.addWidget(self.ff_led_rawinput)
    top.addSpacing(16)

    # Threshold
    top.addWidget(QtWidgets.QLabel("Threshold:"))
    self.ff_threshold = SciEdit("62")
    self.ff_threshold.setFixedWidth(50)
    self.ff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.ff_thr_slider.setRange(0, 100)
    self.ff_thr_slider.setValue(62)
    self.ff_thr_slider.setFixedWidth(100)
    top.addWidget(self.ff_thr_slider)
    # Used gains
    top.addWidget(QtWidgets.QLabel("Used gains:"))
    self.ff_used_gains = SciEdit("5")
    self.ff_used_gains.setFixedWidth(40)
    self.ff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.ff_gains_slider.setRange(1, 16)
    self.ff_gains_slider.setValue(5)
    self.ff_gains_slider.setFixedWidth(80)
    top.addWidget(self.ff_gains_slider)

    top.addStretch(1)
    root.addLayout(top)

    matrix_row = QtWidgets.QHBoxLayout()
    matrix_row.setSpacing(8)
    primary_panel = GroupPanel("Reference/Secondary Path Filters")
    primary_layout = QtWidgets.QHBoxLayout(primary_panel)
    primary_layout.setSpacing(8)

    # ==================================================================
    # Source combo boxes + adaptive rate fields (7 sources)
    # ==================================================================
    src_panel = GroupPanel("Source Configuration")
    src_grid = QtWidgets.QGridLayout(src_panel)
    src_grid.setHorizontalSpacing(8)
    src_grid.setVerticalSpacing(4)
    src_grid.setColumnStretch(3, 1)

    src_grid.addWidget(QtWidgets.QLabel("Source"), 0, 0)
    src_grid.addWidget(QtWidgets.QLabel("Input"), 0, 1)
    src_grid.addWidget(QtWidgets.QLabel("Adaptive Rate"), 0, 2)

    self.ff_source_cbx: list[QtWidgets.QComboBox] = []
    self.ff_adaptive_rate: list[SciEdit] = []
    for i in range(7):
        lbl = QtWidgets.QLabel(f"Ch{i+1}:")
        lbl.setStyleSheet("font-weight:600;")
        src_grid.addWidget(lbl, i + 1, 0)

        cbx = QtWidgets.QComboBox()
        cbx.setMinimumWidth(120)
        # Populated dynamically by init_ff_source_combos
        self.ff_source_cbx.append(cbx)
        src_grid.addWidget(cbx, i + 1, 1)

        rate = SciEdit("0.00000e+000")
        rate.setFixedWidth(100)
        self.ff_adaptive_rate.append(rate)
        src_grid.addWidget(rate, i + 1, 2)

    primary_layout.addWidget(src_panel, 2)

    # ==================================================================
    # Multipliers, offsets, maxima (from SAMBA19xUI)
    # ==================================================================
    mult_panel = GroupPanel("Multipliers / Offsets / Maxima")
    mult_grid = QtWidgets.QGridLayout(mult_panel)
    mult_grid.setHorizontalSpacing(12)
    mult_grid.setVerticalSpacing(4)

    labels_mult = [
        ("XPos Mult:", "ff_xpos_mult"),
        ("XAcc Mult:", "ff_xacc_mult"),
        ("YPos Mult:", "ff_ypos_mult"),
        ("YAcc Mult:", "ff_yacc_mult"),
        ("XPos Offset:", "ff_xpos_offset"),
        ("YPos Offset:", "ff_ypos_offset"),
        ("XPos Maxima:", "ff_xpos_maxima"),
        ("YPos Maxima:", "ff_ypos_maxima"),
    ]
    for col, (label, attr) in enumerate(labels_mult):
        ed = SciEdit("0.00000e+000")
        ed.setFixedWidth(100)
        setattr(self, attr, ed)
        mult_grid.addWidget(QtWidgets.QLabel(label), 0, col * 2)
        mult_grid.addWidget(ed, 0, col * 2 + 1)


    # ==================================================================
    # Ref Filter Grid — 7 sources × 3 stages
    # ==================================================================
    ref_panel = GroupPanel("Reference Filters (RefFilterMatrix)")
    ref_layout = QtWidgets.QGridLayout(ref_panel)
    ref_layout.setHorizontalSpacing(3)
    ref_layout.setVerticalSpacing(3)

    ref_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
    ref_stage_labels = ["Fil0", "Fil1", "Fil2"]
    for j, lab in enumerate(ref_stage_labels):
        lbl = QtWidgets.QLabel(lab)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        ref_layout.addWidget(lbl, 0, j + 1)

    self.ff_ref_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for src in range(7):
        lbl = QtWidgets.QLabel(f"Ch{src+1}")
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        ref_layout.addWidget(lbl, src + 1, 0)
        for st in range(3):
            cell = FilterStageCell(st, "----", width=54, height=52)
            cell.clicked.connect(lambda s=st, a=src: self._on_ff_ref_cell_clicked(a, s))
            self.ff_ref_buttons[(src, st)] = cell
            ref_layout.addWidget(cell, src + 1, st + 1)

    ref_layout.setColumnStretch(4, 1)
    primary_layout.addWidget(ref_panel)

    # ==================================================================
    # Sec Filter Grid — 7 sources × 3 stages
    # ==================================================================
    sec_panel = GroupPanel("Secondary Filters (SecFilterMatrix)")
    sec_layout = QtWidgets.QGridLayout(sec_panel)
    sec_layout.setHorizontalSpacing(3)
    sec_layout.setVerticalSpacing(3)

    sec_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
    for j, lab in enumerate(ref_stage_labels):
        lbl = QtWidgets.QLabel(lab)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        sec_layout.addWidget(lbl, 0, j + 1)

    self.ff_sec_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for src in range(7):
        lbl = QtWidgets.QLabel(f"Ch{src+1}")
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        sec_layout.addWidget(lbl, src + 1, 0)
        for st in range(3):
            cell = FilterStageCell(st, "----", width=54, height=52)
            cell.clicked.connect(lambda s=st, a=src: self._on_ff_sec_cell_clicked(a, s))
            self.ff_sec_buttons[(src, st)] = cell
            sec_layout.addWidget(cell, src + 1, st + 1)

    sec_layout.setColumnStretch(4, 1)
    primary_layout.addWidget(sec_panel)

    # ==================================================================
    # Err Filter Grid — 6 velocity axes × 2 stages
    # ==================================================================
    err_panel = GroupPanel("Error Filters (ErrFilterMatrix)")
    err_layout = QtWidgets.QGridLayout(err_panel)
    err_layout.setHorizontalSpacing(3)
    err_layout.setVerticalSpacing(3)

    err_layout.addWidget(QtWidgets.QLabel("Axis"), 0, 0)
    err_stage_labels = ["Fil1", "Fil2"]
    for j, lab in enumerate(err_stage_labels):
        lbl = QtWidgets.QLabel(lab)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:9px;")
        err_layout.addWidget(lbl, 0, j + 1)

    # Velocity axis names (matching SAMBA19xLabels.VelAxesName)
    self.ff_err_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for ax in range(6):
        lbl = QtWidgets.QLabel(VEL_AXES_NAMES[ax])
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        err_layout.addWidget(lbl, ax + 1, 0)
        for st in range(2):
            cell = FilterStageCell(st, "----", width=58, height=52)
            cell.clicked.connect(lambda s=st, a=ax: self._on_ff_err_cell_clicked(a, s))
            self.ff_err_buttons[(ax, st)] = cell
            err_layout.addWidget(cell, ax + 1, st + 1)

    err_layout.setColumnStretch(3, 1)
    matrix_row.addWidget(primary_panel, 4)
    matrix_row.addWidget(err_panel, 1)
    root.addLayout(matrix_row)
    root.addWidget(mult_panel)

    # ==================================================================
    # Hidden FilterEditor (shared; axis/stage set per-grid when clicked)
    # ==================================================================
    self.ff_filter = FilterEditor([f"src {i}" for i in range(7)], max_stage=7)
    self.ff_filter.setVisible(False)

    # ==================================================================
    # ClassicFilterPanel (shared; used for inline read/write)
    # ==================================================================
    self.ff_filter_panel = ClassicFilterPanel("FF filter (click a cell above)")
    self.ff_filter_panel.read_clicked.connect(self._on_ff_filter_panel_read)
    self.ff_filter_panel.write_clicked.connect(self._on_ff_filter_panel_write)
    self.ff_filter_panel.stage_changed.connect(self._sync_ff_panel_to_editor)
    self.ff_filter_panel.hide()

    # ==================================================================
    # Hidden data holders (compatible with existing read/write helpers)
    # ==================================================================
    self.ff_status = SciEdit()
    self.ff_status.setVisible(False)
    self.ff_inputs = SciEdit()
    self.ff_inputs.setVisible(False)
    self.ff_cfg = SciEdit()
    self.ff_cfg.setVisible(False)
    self.ff_params = SciEdit()
    self.ff_params.setVisible(False)
    self.ff_gains = SciEdit()
    self.ff_gains.setVisible(False)
    self.ff_mult = SciEdit()
    self.ff_mult.setVisible(False)
    self.ff_algo = QtWidgets.QSpinBox()
    self.ff_algo.setVisible(False)

    # Track which grid is currently active
    #   "ref"  → RefFilterMatrix:  source=0..6,  stage=0..2,  protocol_stage=stage
    #   "sec"  → SecFilterMatrix:  source=0..6,  stage=0..2,  protocol_stage=stage+3
    #   "err"  → ErrFilterMatrix:  axis=0..5,    stage=0..1,  protocol_stage=stage+6, protocol_source=axis
    self._ff_active_grid: str = "ref"
    self._ff_active_source: int = 0
    self._ff_active_stage: int = 0

    # ==================================================================
    # Action buttons
    # ==================================================================
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read status", self.on_ff_status_read_classic),
        ("Read all filters", self.on_ff_read_all_filters),
        ("Read filter", self._on_ff_filter_panel_read),
        ("Write filter...", self._on_ff_filter_panel_write),
        ("Write gains...", self.on_ff_write_gains_classic),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        b.hide()
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)

    # Populate source combo boxes
    self._init_ff_source_combos()

    return w


# ---------------------------------------------------------------------------
# Screenshot-oriented reference layout
# ---------------------------------------------------------------------------

def _build_ff_filter_page_reference(self) -> QtWidgets.QWidget:
    """Build the horizontal FF matrix and lower status panels from SAMBA19xUI."""
    from python_samba.ui.main_window import SidebarLoopButton

    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(8)

    matrix_row = QtWidgets.QHBoxLayout()
    matrix_row.setSpacing(10)
    primary = GroupPanel("Reference/Secondary Path Filters")
    primary.setFixedSize(1185, 665)
    grid = QtWidgets.QGridLayout(primary)
    grid.setContentsMargins(10, 18, 10, 12)
    grid.setHorizontalSpacing(3)
    grid.setVerticalSpacing(3)

    for text, column, span in (
        ("Channel", 0, 1),
        ("Reference Filters", 1, 3),
        ("Secondary Filters", 4, 3),
        ("Config. Matrix", 7, 6),
        ("FFRate", 13, 1),
    ):
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(label, 0, column, 1, span)

    self.ff_source_cbx = []
    self.ff_adaptive_rate = []
    self.ff_ref_buttons = {}
    self.ff_sec_buttons = {}
    self.ff_status_rows = []
    for source in range(7):
        cbx = QtWidgets.QComboBox()
        cbx.setFixedSize(180, 48)
        cbx.currentIndexChanged.connect(
            lambda _index, src=source: self._on_ff_inputs_changed(src)
        )
        self.ff_source_cbx.append(cbx)
        grid.addWidget(cbx, source + 1, 0)

        for stage in range(3):
            cell = FilterStageCell(stage, "----", width=90, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _source=source: self._on_ff_ref_cell_clicked(
                    _source, _stage
                )
            )
            self.ff_ref_buttons[(source, stage)] = cell
            grid.addWidget(cell, source + 1, stage + 1)

        for stage in range(3):
            cell = FilterStageCell(stage + 3, "----", width=90, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _source=source: self._on_ff_sec_cell_clicked(
                    _source, _stage
                )
            )
            self.ff_sec_buttons[(source, stage)] = cell
            grid.addWidget(cell, source + 1, stage + 4)

        leds = []
        for offset, name in enumerate(("Xt", "Zr", "Yt", "Zt", "Yr", "Xr")):
            holder = QtWidgets.QWidget()
            col = QtWidgets.QVBoxLayout(holder)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            lbl = QtWidgets.QLabel(name)
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            led = LedIndicator(32, clickable=True)
            led.set_on(offset == source if source < 6 else False)
            led.setToolTip(
                f"Toggle FF source {source + 1} output {name}"
            )
            led.clicked.connect(
                lambda src=source, axis=offset: self._on_ff_matrix_clicked(
                    src, axis
                )
            )
            col.addWidget(lbl)
            col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            grid.addWidget(holder, source + 1, 7 + offset)
            leds.append(led)
        self.ff_status_rows.append(leds)

        rate = SciEdit("0")
        rate.setFixedSize(150, 42)
        rate.editingFinished.connect(
            lambda src=source: self._on_ff_rate_changed(src)
        )
        self.ff_adaptive_rate.append(rate)
        grid.addWidget(rate, source + 1, 13)

    matrix_row.addWidget(primary, 0, QtCore.Qt.AlignTop)

    error = GroupPanel("Error Path Filters")
    error.setFixedSize(295, 665)
    err = QtWidgets.QGridLayout(error)
    err.setContentsMargins(10, 40, 10, 30)
    err.setSpacing(3)
    err.addWidget(QtWidgets.QLabel("Fil1"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    err.addWidget(QtWidgets.QLabel("Fil2"), 0, 2, alignment=QtCore.Qt.AlignCenter)
    self.ff_err_buttons = {}
    for axis, name in enumerate(VEL_AXES_NAMES[:6]):
        err.addWidget(QtWidgets.QLabel(name), axis + 1, 0)
        for stage in range(2):
            cell = FilterStageCell(stage + 6, "----", width=90, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _axis=axis: self._on_ff_err_cell_clicked(
                    _axis, _stage
                )
            )
            self.ff_err_buttons[(axis, stage)] = cell
            err.addWidget(cell, axis + 1, stage + 1)
    matrix_row.addWidget(error, 0, QtCore.Qt.AlignTop)
    matrix_row.addStretch(1)
    root.addLayout(matrix_row)

    lower = QtWidgets.QHBoxLayout()
    lower.setSpacing(12)
    left = QtWidgets.QVBoxLayout()
    loop_group = GroupPanel("Velocity Individual Loop Status")
    loop_group.setFixedSize(520, 145)
    loop_row = QtWidgets.QHBoxLayout(loop_group)
    self.ff_individual_loop_leds = []
    for axis, name in enumerate(VEL_AXES_NAMES[:6]):
        col = QtWidgets.QVBoxLayout()
        lbl = QtWidgets.QLabel(name)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lamp = SidebarLoopButton()
        lamp.set_on(False)
        lamp.setFixedSize(66, 64)
        lamp.setToolTip(f"Toggle velocity individual loop {name}")
        lamp.clicked.connect(
            lambda _checked=False, _axis=axis: self._on_ff_individual_loop_clicked(
                _axis
            )
        )
        self.ff_individual_loop_leds.append(lamp)
        col.addWidget(lbl)
        col.addWidget(lamp)
        loop_row.addLayout(col)
    left.addWidget(loop_group)
    diagnostic = GroupPanel("Diagnostic Signals")
    diagnostic.setFixedSize(520, 120)
    dg = QtWidgets.QGridLayout(diagnostic)
    dg.addWidget(QtWidgets.QLabel("Diag0"), 0, 0, alignment=QtCore.Qt.AlignCenter)
    dg.addWidget(QtWidgets.QLabel("Diag1"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    dg.addWidget(FlatPush("X1FB"), 1, 0)
    dg.addWidget(FlatPush("X1FB"), 1, 1)
    left.addWidget(diagnostic)
    lower.addLayout(left)

    mult = GroupPanel("Stage Signal Multipliers")
    mult.setFixedSize(285, 300)
    mf = QtWidgets.QFormLayout(mult)
    for label, attr in (
        ("XPos", "ff_xpos_mult"), ("XAcc", "ff_xacc_mult"),
        ("YPos", "ff_ypos_mult"), ("YAcc", "ff_yacc_mult"),
    ):
        edit = SciEdit("1")
        edit.setFixedWidth(150)
        setattr(self, attr, edit)
        mf.addRow(label, edit)
    lower.addWidget(mult)

    offsets = QtWidgets.QVBoxLayout()
    off = GroupPanel("Stage Signal Offsets")
    of = QtWidgets.QFormLayout(off)
    for label, attr in (("XPos", "ff_xpos_offset"), ("YPos", "ff_ypos_offset")):
        edit = SciEdit("1")
        edit.setFixedWidth(150)
        setattr(self, attr, edit)
        of.addRow(label, edit)
    offsets.addWidget(off)
    maxima = GroupPanel("Stage Signal Maximums")
    maxf = QtWidgets.QFormLayout(maxima)
    for label, attr, value in (
        ("XPos", "ff_xpos_maxima", "10000"),
        ("YPos", "ff_ypos_maxima", "32000"),
    ):
        edit = SciEdit(value)
        edit.setFixedWidth(150)
        setattr(self, attr, edit)
        maxf.addRow(label, edit)
    offsets.addWidget(maxima)
    lower.addLayout(offsets)

    status = GroupPanel("Threshold/Gains Number")
    status.setFixedSize(355, 300)
    sf = QtWidgets.QFormLayout(status)
    self.ff_threshold = SciEdit("0")
    self.ff_used_gains = SciEdit("0")
    self.ff_led_active = SidebarLoopButton()
    self.ff_led_adapt = SidebarLoopButton()
    self.ff_led_rawinput = SidebarLoopButton()
    self.ff_led_active.setToolTip("Toggle FF active status (BSSTS bit 0x0004)")
    self.ff_led_adapt.setToolTip("Toggle adaptive status (BSSTS bit 0x0002)")
    self.ff_led_rawinput.setToolTip(
        "Toggle legacy UseFBForFF status (BSSTS bit 0x1000)"
    )
    self.ff_led_active.clicked.connect(
        lambda _checked=False: self._on_ff_status_button_clicked("active")
    )
    self.ff_led_adapt.clicked.connect(
        lambda _checked=False: self._on_ff_status_button_clicked("adaptive")
    )
    self.ff_led_rawinput.clicked.connect(
        lambda _checked=False: self._on_ff_status_button_clicked("raw")
    )
    sf.addRow("Threshold [%]", self.ff_threshold)
    sf.addRow("Gain Number", self.ff_used_gains)
    sf.addRow("Active", self.ff_led_active)
    sf.addRow("Adaptive", self.ff_led_adapt)
    sf.addRow("Use Raw Axis Input", self.ff_led_rawinput)
    self.ff_threshold.editingFinished.connect(self._on_ff_config_changed)
    self.ff_used_gains.editingFinished.connect(self._on_ff_config_changed)
    lower.addWidget(status)
    lower.addStretch(1)
    root.addLayout(lower)
    root.addStretch(1)

    self.ff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.ff_thr_slider.setRange(0, 100)
    self.ff_thr_slider.hide()
    self.ff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    self.ff_gains_slider.setRange(1, 16)
    self.ff_gains_slider.hide()
    self.ff_filter = FilterEditor([f"src {i}" for i in range(7)], max_stage=7)
    self.ff_filter.hide()
    self.ff_filter_panel = ClassicFilterPanel("FF filter")
    self.ff_filter_panel.read_clicked.connect(self._on_ff_filter_panel_read)
    self.ff_filter_panel.write_clicked.connect(self._on_ff_filter_panel_write)
    self.ff_filter_panel.stage_changed.connect(self._sync_ff_panel_to_editor)
    self.ff_filter_panel.hide()
    for attr in ("ff_status", "ff_inputs", "ff_cfg", "ff_params", "ff_gains", "ff_mult"):
        edit = SciEdit()
        edit.hide()
        setattr(self, attr, edit)
    self.ff_algo = QtWidgets.QSpinBox()
    self.ff_algo.hide()
    self.ff_gain_edits = [SciEdit("0") for _ in range(5)]
    for edit in self.ff_gain_edits:
        edit.hide()
    self._ff_active_grid = "ref"
    self._ff_active_source = 0
    self._ff_active_stage = 0
    self._init_ff_source_combos()
    for editor in (
        self.ff_xpos_mult, self.ff_xacc_mult,
        self.ff_ypos_mult, self.ff_yacc_mult,
    ):
        editor.editingFinished.connect(self._on_ff_multipliers_changed)
    for editor in (
        self.ff_xpos_maxima, self.ff_ypos_maxima,
        self.ff_xpos_offset, self.ff_ypos_offset,
    ):
        editor.editingFinished.connect(self._on_ff_zrot_changed)
    return w


def _run_confirmed_ff_write(self, title: str, summary: str, callback) -> None:
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


def _parse_ff_output_mask(value) -> int:
    """FGFFP documents Outputs as hexadecimal text without a prefix."""
    return int(str(value).strip(), 16)


def _on_ff_matrix_clicked(self, source: int, axis: int) -> None:
    """Toggle one Config.Matrix output bit and preserve adaptive/rate fields."""
    if not 0 <= source < 7 or not 0 <= axis < 6:
        raise ValueError(f"FF matrix index out of range: source={source}, axis={axis}")
    if not self.session or not self.session.connected:
        return

    def send() -> None:
        session = self._require_session()
        current = session.get_ff_parameters(source)
        if len(current) < 3:
            raise RuntimeError(
                f"FGFFP source {source} returned {len(current)} fields; expected 3"
            )
        outputs = _parse_ff_output_mask(current[0]) ^ (1 << axis)
        session.set_ff_parameters(source, outputs, current[1], float(current[2]))
        self.ff_status_rows[source][axis].set_on(bool(outputs & (1 << axis)))

    _run_confirmed_ff_write(
        self,
        "Toggle FF config matrix",
        f"FF source {source + 1}, output axis {axis + 1}",
        send,
    )


def _set_ff_status_buttons_from_system(self, system: int) -> None:
    """Apply legacy FF LEDBtn states from the BGSTS system word."""
    self.ff_led_active.set_on(bool(system & 0x0004))
    self.ff_led_adapt.set_on(bool(system & 0x0002))
    # The old button is labelled Use Raw but is bound to UseFBForFFBit;
    # SetFFConfig converts this bit to FFUseRaw using inverse polarity.
    self.ff_led_rawinput.set_on(bool(system & 0x1000))


def _on_ff_status_button_clicked(self, kind: str) -> None:
    """Toggle Active/Adaptive/UseFBForFF through BGSTS/BSSTS."""
    bits = {"active": 0x0004, "adaptive": 0x0002, "raw": 0x1000}
    try:
        bit = bits[kind]
    except KeyError as exc:
        raise ValueError(f"unknown FF status button {kind!r}") from exc
    if not self.session or not self.session.connected:
        return

    def send() -> None:
        session = self._require_session()
        loop = session.get_loop_status()
        system = loop.system ^ bit
        session.set_loop_status(loop.individual, system)
        _set_ff_status_buttons_from_system(self, system)
        self._refresh_status_loop_state()

    _run_confirmed_ff_write(
        self,
        "Toggle FF status",
        f"BSSTS FF {kind} bit 0x{bit:X}",
        send,
    )


def _set_ff_individual_loop_buttons(self, individual: int) -> None:
    """Apply the BGSTS velocity-individual word to the six FF page buttons."""
    for axis, lamp in enumerate(getattr(self, "ff_individual_loop_leds", ())):
        lamp.set_on(bool(int(individual) & (1 << axis)))


def _on_ff_individual_loop_clicked(self, axis: int) -> None:
    """Toggle one velocity individual-loop bit through BGSTS/BSSTS."""
    if not 0 <= axis < 6:
        raise ValueError(f"FF individual-loop axis out of range: {axis}")
    if not self.session or not self.session.connected:
        return

    def send() -> None:
        session = self._require_session()
        loop = session.get_loop_status()
        individual = loop.individual ^ (1 << axis)
        session.set_loop_status(individual, loop.system)
        _set_ff_individual_loop_buttons(self, individual)
        self._refresh_status_loop_state()

    _run_confirmed_ff_write(
        self,
        "Toggle velocity individual loop",
        f"BSSTS velocity axis {axis + 1} bit 0x{1 << axis:X}",
        send,
    )


def _on_ff_inputs_changed(self, _source: int) -> None:
    if not self.session or not self.session.connected:
        return
    values = [combo.currentIndex() for combo in self.ff_source_cbx]
    _run_confirmed_ff_write(
        self, "Write FF inputs", f"FF input mapping: {values}",
        lambda: self._require_session().set_ff_inputs(*values),
    )


def _on_ff_rate_changed(self, source: int) -> None:
    if not self.session or not self.session.connected:
        return
    rate = float(self.ff_adaptive_rate[source].text())

    def send() -> None:
        s = self._require_session()
        current = s.get_ff_parameters(source)
        outputs = current[0] if current else 0
        adaptive = current[1] if len(current) > 1 else 0
        s.set_ff_parameters(source, outputs, adaptive, rate)

    _run_confirmed_ff_write(
        self, "Write FF rate", f"FF source {source + 1} adaptation rate={rate}", send,
    )


def _on_ff_config_changed(self) -> None:
    if not self.session or not self.session.connected:
        return
    gains = int(float(self.ff_used_gains.text()))
    threshold = int(float(self.ff_threshold.text()))

    def send() -> None:
        s = self._require_session()
        current = s.get_ff_config()
        use_raw = current[1] if len(current) > 1 else 0
        s.set_ff_config(gains, use_raw)
        s.set_ff_output_limit(threshold)

    _run_confirmed_ff_write(
        self, "Write FF config",
        f"FF gains={gains}, threshold={threshold}%", send,
    )


def _on_ff_multipliers_changed(self) -> None:
    if not self.session or not self.session.connected:
        return
    values = [
        float(self.ff_xpos_mult.text()), float(self.ff_xacc_mult.text()),
        float(self.ff_ypos_mult.text()), float(self.ff_yacc_mult.text()),
    ]
    _run_confirmed_ff_write(
        self, "Write FF multipliers", f"FF multipliers: {values}",
        lambda: self._require_session().set_stage_ff_multipliers(values),
    )


def _on_ff_zrot_changed(self) -> None:
    if not self.session or not self.session.connected:
        return
    values = [
        float(self.ff_xpos_maxima.text()), float(self.ff_ypos_maxima.text()),
        float(self.ff_xpos_offset.text()), float(self.ff_ypos_offset.text()),
    ]
    _run_confirmed_ff_write(
        self, "Write FF Z-rotation config", f"FF Z-rotation values: {values}",
        lambda: self._require_session().set_ff_zrot_parameters(*values),
    )


# ---------------------------------------------------------------------------
# Source combo box initialization
# ---------------------------------------------------------------------------

def _init_ff_source_combos(self) -> None:
    """Populate the 7 source combo boxes with available input names."""
    if self.session and self.session.connected:
        self._ensure_controller_capabilities()
    count = max(1, min(
        len(IOSignalButton.INPUT_NAMES),
        int(getattr(self, "_input_signal_count", len(IOSignalButton.INPUT_NAMES))),
    ))
    input_names = IOSignalButton.INPUT_NAMES[:count]
    for cbx in self.ff_source_cbx:
        selected = cbx.currentIndex()
        cbx.blockSignals(True)
        cbx.clear()
        for name in input_names:
            cbx.addItem(name)
        if 0 <= selected < cbx.count():
            cbx.setCurrentIndex(selected)
        cbx.blockSignals(False)


# ===================================================================
# Cell click handlers
# ===================================================================

def _on_ff_ref_cell_clicked(self, source: int, stage: int) -> None:
    """User clicked a Ref filter cell (7 sources × 3 stages, protocol stage 0..2)."""
    self._ff_active_grid = "ref"
    self._ff_active_source = source
    self._ff_active_stage = stage
    self._ff_open_filter_dlg(
        source, stage,
        axis_labels=[f"Ch{i+1}" for i in range(7)],
        max_stage=2,
        dlg_title=f"FF Ref Filter — Source {source+1}, Stage {stage}",
    )


def _on_ff_sec_cell_clicked(self, source: int, stage: int) -> None:
    """User clicked a Sec filter cell (7 sources × 3 stages, protocol stage 3..5)."""
    self._ff_active_grid = "sec"
    self._ff_active_source = source
    self._ff_active_stage = stage
    self._ff_open_filter_dlg(
        source, stage,
        axis_labels=[f"Ch{i+1}" for i in range(7)],
        max_stage=2,
        dlg_title=f"FF Sec Filter — Source {source+1}, Stage {stage}",
    )


def _on_ff_err_cell_clicked(self, axis: int, stage: int) -> None:
    """User clicked an Err filter cell (6 axes × 2 stages, protocol stage 6..7)."""
    self._ff_active_grid = "err"
    self._ff_active_source = axis
    self._ff_active_stage = stage
    self._ff_open_filter_dlg(
        axis, stage,
        axis_labels=VEL_AXES_NAMES,
        max_stage=1,
        dlg_title=f"FF Err Filter — Axis {VEL_AXES_NAMES[axis]}, Stage {stage}",
    )


def _ff_open_filter_dlg(
    self,
    source: int,
    stage: int,
    axis_labels: list[str],
    max_stage: int,
    dlg_title: str,
) -> None:
    """Open the FilterDlg for a given grid cell.

    Parameters
    ----------
    source : int
        Source index (0..6 for ref/sec) or axis index (0..5 for err).
    stage : int
        Grid-local stage index (0..2 for ref/sec, 0..1 for err).
    axis_labels : list[str]
        Labels for the axis combo box in the dialog.
    max_stage : int
        Maximum stage value for the dialog spinner.
    dlg_title : str
        Dialog window title.
    """
    from python_samba.ui.widgets import FilterDlg
    from python_samba.protocol.commands import FilterStage

    # Set the hidden editor's axis and stage (using grid-local indices)
    self.ff_filter.axis.blockSignals(True)
    self.ff_filter.stage.blockSignals(True)
    try:
        # Find the matching axis label index
        aidx = self.ff_filter.axis.findText(axis_labels[source] if source < len(axis_labels) else f"src {source}")
        if aidx >= 0:
            self.ff_filter.axis.setCurrentIndex(aidx)
        else:
            self.ff_filter.axis.setCurrentIndex(source)
        self.ff_filter.stage.setValue(stage)
    finally:
        self.ff_filter.axis.blockSignals(False)
        self.ff_filter.stage.blockSignals(False)
    self.ff_filter_panel.set_stage_index(stage)

    # Read current filter from controller
    if self.session and self.session.connected:
        self._ff_read_current_stage()

    dlg = FilterDlg(
        axis_labels, max_stage=max_stage,
        show_all_axes=True, show_all_sources=False, parent=self,
    )
    dlg.setWindowTitle(dlg_title)
    fs = self.ff_filter.to_stage()
    dlg.set_stage(fs)
    dlg.axis_cbx.setCurrentIndex(source)
    dlg.axis_cbx.setEnabled(False)

    def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
        if not isinstance(new_stage, FilterStage):
            return
        self.ff_filter.set_stage(new_stage)
        self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
        self._ff_update_current_cell_text()
        if all_axes:
            # Apply to all sources (ref/sec) or all axes (err)
            grid = self._ff_active_grid
            n_rows = 6 if grid == "err" else 7
            for i in range(n_rows):
                self._ff_write_stage_to_controller(
                    source=i,
                    grid_stage=new_stage.stage,
                    filter_type=new_stage.filter_type,
                    params=new_stage.params,
                )
                self._ff_update_cell_text(grid, i, new_stage.stage)
        else:
            self._ff_write_current_stage()

    dlg.filterChanged.connect(on_dlg_changed)
    dlg.exec()
    dlg.deleteLater()


# ===================================================================
# Stage mapping helpers
# ===================================================================

def _ff_grid_stage_to_proto(self, grid: str, source: int, grid_stage: int) -> int:
    """Map grid-local stage index to protocol stage index.

    Ref:  grid stage 0..2 → protocol stage 0..2
    Sec:  grid stage 0..2 → protocol stage 3..5
    Err:  grid stage 0..1 → protocol stage 6..7
    """
    if grid == "sec":
        return grid_stage + 3
    elif grid == "err":
        return grid_stage + 6
    else:  # ref
        return grid_stage


def _ff_proto_source(self, grid: str, source: int) -> int:
    """Map grid source to protocol source parameter.

    Ref/Sec: source = source index (0..6)
    Err:     source = axis index (0..5), protocol source = 0 for err
    """
    if grid == "err":
        return source  # Err filter uses axis as the first FGPFS parameter
    return source


def _ff_proto_to_grid_source(self, grid: str, proto_source: int) -> int:
    """Map protocol source back to grid source index."""
    return proto_source  # Same for all grids


# ===================================================================
# Read / write helpers
# ===================================================================

def _ff_read_current_stage(self) -> None:
    """Read the current active grid cell into self.ff_filter."""
    grid = self._ff_active_grid
    source = self._ff_active_source
    grid_stage = self._ff_active_stage

    src = self._ff_proto_source(grid, source)
    proto_stage = self._ff_grid_stage_to_proto(grid, source, grid_stage)

    def work() -> None:
        s = self._require_session()
        fs = s.get_ff_filter(src, proto_stage)
        self.ff_filter.set_stage(fs)
        self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
        self._ff_update_current_cell_text()
        self.log_msg(
            f"FGPFS grid={grid} src={source} stage={grid_stage} "
            f"proto=({src},{proto_stage}) type={fs.type_name}"
        )
    self._run("Read FF filter stage", work)


def _ff_write_current_stage(self) -> None:
    """Write the current filter editor values to the active grid cell."""
    grid = self._ff_active_grid
    source = self._ff_active_source
    grid_stage = self._ff_active_stage

    stage = self.ff_filter.to_stage()
    self._ff_write_stage_to_controller(source, grid_stage, stage.filter_type, stage.params)


def _ff_write_stage_to_controller(
    self,
    source: int,
    grid_stage: int,
    filter_type: int,
    params: tuple[float, float, float, float, float],
) -> None:
    """Write a filter stage to the controller with proper protocol mapping."""
    from python_samba.protocol.commands import FilterStage

    grid = self._ff_active_grid
    src = self._ff_proto_source(grid, source)
    proto_stage = self._ff_grid_stage_to_proto(grid, source, grid_stage)

    def work() -> None:
        s = self._require_session()
        assert self.gate
        fs = FilterStage(
            axis=src,
            stage=proto_stage,
            filter_type=filter_type,
            params=params,
        )
        summary = f"FSPFS grid={grid} src={source} stage={grid_stage} proto=({src},{proto_stage}) type={filter_type}"
        if not self._confirm_write(summary):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        s.set_ff_filter(fs)
        self._set_writable(True)
        self.log_msg(summary + " applied")
    self._run("Write FF filter", work)


def _ff_update_current_cell_text(self) -> None:
    """Update the text on the currently active grid cell."""
    self._ff_update_cell_text(
        self._ff_active_grid,
        self._ff_active_source,
        self._ff_active_stage,
    )


def _ff_update_cell_text(self, grid: str, source: int, grid_stage: int) -> None:
    """Update the label on a specific grid cell from the filter editor."""
    try:
        name = self.ff_filter.ftype.currentText().split(None, 1)[-1]
        short = name[:5] if len(name) > 5 else name
    except Exception:
        short = ""

    if grid == "ref":
        key = (source, grid_stage)
        if key in self.ff_ref_buttons:
            self.ff_ref_buttons[key].set_info(short)
    elif grid == "sec":
        key = (source, grid_stage)
        if key in self.ff_sec_buttons:
            self.ff_sec_buttons[key].set_info(short)
    elif grid == "err":
        key = (source, grid_stage)
        if key in self.ff_err_buttons:
            self.ff_err_buttons[key].set_info(short)


# ===================================================================
# Panel read/write callbacks (from ClassicFilterPanel buttons)
# ===================================================================

def _on_ff_filter_panel_read(self) -> None:
    """Read button clicked on ClassicFilterPanel — read active cell."""
    self._ff_read_current_stage()


def _on_ff_filter_panel_write(self) -> None:
    """Write button clicked on ClassicFilterPanel — write active cell."""
    self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)
    self._ff_write_current_stage()


def _sync_ff_panel_to_editor(self) -> None:
    """Sync ClassicFilterPanel changes back to the hidden FilterEditor."""
    self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)


# ===================================================================
# Read all filters
# ===================================================================

def on_ff_read_all_filters(self) -> None:
    """Read all 7×3 + 7×3 + 6×2 = 54 filter stages from the controller."""

    def work() -> None:
        s = self._require_session()

        # Ref filters: 7 sources × 3 stages (protocol 0..2)
        for src in range(7):
            for st in range(3):
                try:
                    fs = s.get_ff_filter(src, st)
                    self.ff_ref_buttons[(src, st)].set_info(fs.type_name[:5])
                except Exception:
                    self.ff_ref_buttons[(src, st)].set_info("?")

        # Sec filters: 7 sources × 3 stages (protocol 3..5)
        for src in range(7):
            for st in range(3):
                try:
                    fs = s.get_ff_filter(src, st + 3)
                    self.ff_sec_buttons[(src, st)].set_info(fs.type_name[:5])
                except Exception:
                    self.ff_sec_buttons[(src, st)].set_info("?")

        # Err filters: 6 axes × 2 stages (protocol 6..7)
        for ax in range(6):
            for st in range(2):
                try:
                    fs = s.get_ff_filter(ax, st + 6)
                    self.ff_err_buttons[(ax, st)].set_info(fs.type_name[:5])
                except Exception:
                    self.ff_err_buttons[(ax, st)].set_info("?")

        self.log_msg("FF filters all 54 read (21 ref + 21 sec + 12 err)")

    self._run("Read all FF filters", work)


# ===================================================================
# Status read (compatible with existing on_ff_status_read_classic)
# ===================================================================

def on_ff_status_read_classic(self) -> None:
    """Read FF status, config, parameters, gains, multipliers, etc."""

    def work() -> None:
        s = self._require_session()
        self._init_ff_source_combos()
        self.on_ff_status_read()
        parts = self.ff_gains.text().split()
        # Update hidden gain edits (for write gains button)
        for ed, p in zip(self.ff_gain_edits if hasattr(self, 'ff_gain_edits') else [], parts):
            try:
                ed.setText(f"{float(p):.5e}")
            except Exception:
                ed.setText(p)
        # Source mapping
        inputs = s.get_ff_inputs()
        for index, combo in enumerate(self.ff_source_cbx):
            if index < len(inputs):
                try:
                    selected = int(inputs[index])
                except ValueError:
                    selected = combo.findText(inputs[index])
                if 0 <= selected < combo.count():
                    combo.blockSignals(True)
                    combo.setCurrentIndex(selected)
                    combo.blockSignals(False)

        # Per-source output matrix and adaptation rate.
        for source in range(7):
            try:
                params = s.get_ff_parameters(source)
                outputs = _parse_ff_output_mask(params[0]) if params else 0
                if len(params) > 2:
                    self.ff_adaptive_rate[source].setText(str(params[2]))
                for axis, led in enumerate(self.ff_status_rows[source]):
                    led.set_on(bool(outputs & (1 << axis)))
            except Exception as exc:
                self.log_msg(f"FF source {source + 1} parameters: {exc}")

        config = s.get_ff_config()
        if config:
            self.ff_used_gains.setText(str(config[0]))
        self.ff_threshold.setText(str(s.get_ff_output_limit()))

        multipliers = s.get_stage_ff_multipliers()
        for editor, value in zip((
            self.ff_xpos_mult, self.ff_xacc_mult,
            self.ff_ypos_mult, self.ff_yacc_mult,
        ), multipliers):
            editor.setText(str(value))

        zrot = s.get_ff_zrot_parameters()
        for editor, value in zip((
            self.ff_xpos_maxima, self.ff_ypos_maxima,
            self.ff_xpos_offset, self.ff_ypos_offset,
        ), zrot):
            editor.setText(str(value))

        loop = s.get_loop_status()
        _set_ff_status_buttons_from_system(self, loop.system)
        _set_ff_individual_loop_buttons(self, loop.individual)

    self._run("FF read", work)


def _update_ff_ui_from_params(self) -> None:
    """Update adaptive rate fields, source combo boxes, thresholds, etc.

    from the latest FF parameter data stored in self.ff_params, etc.
    """
    try:
        params_text = self.ff_params.text().strip()
        if params_text:
            parts = params_text.split()
            # Parts 0..6 are adaptive rates for sources 0..6
            for i in range(min(7, len(parts))):
                try:
                    self.ff_adaptive_rate[i].setText(f"{float(parts[i]):.5e}")
                except Exception:
                    pass
    except Exception:
        pass

    try:
        inputs_text = self.ff_inputs.text().strip()
        if inputs_text:
            parts = inputs_text.split()
            for i in range(min(7, len(parts))):
                if i < len(self.ff_source_cbx):
                    cbx = self.ff_source_cbx[i]
                    val = parts[i]
                    # Try to match the value to a combo box item
                    idx = cbx.findText(val)
                    if idx >= 0:
                        cbx.setCurrentIndex(idx)
                    else:
                        # Try as integer index
                        try:
                            idx = int(val)
                            if 0 <= idx < cbx.count():
                                cbx.setCurrentIndex(idx)
                        except ValueError:
                            pass
    except Exception:
        pass

    # Update threshold and used gains
    try:
        thr_text = self.ff_threshold.text()
        if thr_text:
            self.ff_thr_slider.setValue(int(float(thr_text)))
    except Exception:
        pass

    try:
        gains_text = self.ff_used_gains.text()
        if gains_text:
            self.ff_gains_slider.setValue(int(float(gains_text)))
    except Exception:
        pass

    # Update multipliers, offsets, maxima
    try:
        mult_text = self.ff_mult.text().strip()
        if mult_text:
            parts = mult_text.split()
            # 8 parts: XPos, XAcc, YPos, YAcc, XPosOff, YPosOff, XPosMax, YPosMax
            targets = [
                "ff_xpos_mult", "ff_xacc_mult", "ff_ypos_mult", "ff_yacc_mult",
                "ff_xpos_offset", "ff_ypos_offset", "ff_xpos_maxima", "ff_ypos_maxima",
            ]
            for i in range(min(len(targets), len(parts))):
                ed = getattr(self, targets[i], None)
                if ed:
                    try:
                        ed.setText(f"{float(parts[i]):.5e}")
                    except Exception:
                        ed.setText(parts[i])
    except Exception:
        pass


# ===================================================================
# Write gains classic (compatible with existing)
# ===================================================================

def on_ff_write_gains_classic(self) -> None:
    """Write gains from the hidden gain edits."""
    if hasattr(self, 'ff_gain_edits') and self.ff_gain_edits:
        self.ff_gains.setText(" ".join(ed.text() for ed in self.ff_gain_edits))
    self.on_ff_write_gains()


# ===================================================================
# Update page (called from on_refresh)
# ===================================================================

def _update_ff_filter_page(self) -> None:
    """Update the FF filter page from the controller (offline or connected).

    Mirrors SAMBA19xUI FFFilterPage.UpdatePage.
    """
    if self.session and self.session.connected:
        # Connected: read all data from controller
        self.on_ff_read_all_filters()
        self.on_ff_status_read_classic()
    else:
        # Offline: copy from UI params (handled by the UI binding)
        pass


# ===================================================================

def apply_patches(cls: type) -> None:
    """Install all FF handlers and select the screenshot-oriented builder."""
    prefixes = (
        "_build_", "_on_", "on_", "_init_", "_ff_", "_update_", "_sync_",
    )
    for name, value in globals().items():
        if name.startswith(prefixes) and callable(value):
            setattr(cls, name, value)
    cls._set_ff_individual_loop_buttons = _set_ff_individual_loop_buttons
    cls._build_ff_filter_page = _build_ff_filter_page_reference


# ===================================================================
# NOTE: The following methods are shared with the existing code and
# remain unchanged:
#
#   on_ff_status_read(self)
#   on_ff_filter_read(self)      — uses self.ff_filter (hidden editor)
#   on_ff_filter_write(self)     — uses self.ff_filter (hidden editor)
#   on_ff_write_gains(self)      — uses self.ff_gains text
#   on_ff_write_cfg(self)        — uses self.ff_cfg text
#   on_ff_write_mult(self)       — uses self.ff_mult text
#   on_ff_write_inputs(self)     — uses self.ff_inputs text
#
# These are all defined in the original main_window.py and work with
# the hidden SciEdit holders we set up above.
# ===================================================================
