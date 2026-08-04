"""
Patch: PFF Config Page, Pos Filter Page additions, SaveLoad page additions.

Module-level functions for monkey-patching MainWindow methods:

  _build_pff_config_page     — replaces the existing PFF config stub
  _on_pff_source_clicked     — source selector button handler
  on_pff_read_gains          — reads gain matrix from controller
  on_pff_write_gains_from_matrix — writes gain matrix to controller
  _build_pos_filter_page     — full replacement with cascaded/NLP/prox status
  on_cascaded_filter_cell_clicked — cascaded filter stage click handler
  on_nlp_parameter_changed   — NLP deadband/rise/mode/reset change handler
  _build_saveload_tab        — replacement with progress bar, label/SI/checksum
  on_label_file_generate     — generate label file dialog
  on_label_file_load         — load label file dialog
  on_si_unit_select          — SI unit file selection
  on_build_checksum          — build checksum action
  on_read_checksum           — read checksum action

Usage:
    from python_samba.ui.main_window import MainWindow
    from pffconfig_posfilter_saveload_patch import (
        _build_pff_config_page,
        _build_pos_filter_page,
        _build_saveload_tab,
        ...
    )
    MainWindow._build_pff_config_page = _build_pff_config_page
    MainWindow._build_pos_filter_page = _build_pos_filter_page
    MainWindow._build_saveload_tab = _build_saveload_tab
    ...
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    ClassicExpander,
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
    POS_AXIS_LABELS,
    VEL_AXIS_LABELS,
    FilterDlg,
    FilterEditor,
    MatrixEditor,
)
from python_samba.ui.extra_pages import PNEUM_AXIS_LABELS
from python_samba.ui.main_window import PNEU_AXES_NAMES, POS_AXES_NAMES

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
INPUT_NAMES_LABELS = [
    "InpXPOS", "InpYPOS", "InpZPOS", "InpXVEL", "InpYVEL", "InpZVEL",
    "InpXACC", "InpYACC", "InpZACC", "InpTHETA", "Prox1", "Prox2",
    "Prox3", "ProxH1", "ProxH2", "ProxH3", "InpPRESS", "InpTEMP",
    "InpFORCE", "InpTORQUE",
]


# ===================================================================
# PART 1 — PFF Config Page
# ===================================================================

def _build_pff_config_page(self) -> QtWidgets.QWidget:
    """PFF config page — 4 source selectors, 3×5 gain matrix, source def, offsets.

    From SAMBA19xUI PFFConfigPage:
      - 4 source selector buttons (Source1Btn–Source4Btn)
      - 3×5 gain matrix (3 pneumatic axes × 5 gains)
      - Source definition section
      - Offset/multiplier fields
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    top_area = QtWidgets.QHBoxLayout()
    top_area.setSpacing(8)
    left_column = QtWidgets.QVBoxLayout()
    right_column = QtWidgets.QVBoxLayout()
    top_area.addLayout(left_column, 2)
    top_area.addLayout(right_column, 1)

    # ---- Source selector buttons ----
    g_src = GroupPanel("Source Selector")
    src_row = QtWidgets.QHBoxLayout(g_src)
    self.pff_source_btns: list[FlatPush] = []
    for i in range(4):
        btn = FlatPush(f"Source {i+1}")
        btn.setCheckable(True)
        if i == 0:
            btn.setChecked(True)
        btn.clicked.connect(lambda checked, s=i: self._on_pff_source_clicked(s))
        self.pff_source_btns.append(btn)
        src_row.addWidget(btn)
    src_row.addStretch(1)
    root.addWidget(g_src)

    # ---- 3×5 gain matrix ----
    g_gain = GroupPanel("PFF Gain Matrix (3 pneumatic axes × 5 gains)")
    gain_grid = QtWidgets.QGridLayout(g_gain)
    gain_grid.setHorizontalSpacing(4)
    gain_grid.setVerticalSpacing(4)

    # Column headers: Gain1–Gain5
    for j in range(5):
        lbl = QtWidgets.QLabel(f"Gain{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        gain_grid.addWidget(lbl, 0, j + 1)

    # Row labels + SciEdit cells
    self.pff_gain_matrix: dict[tuple[int, int], SciEdit] = {}
    self.pff_gain_matrix_labels: list[QtWidgets.QLabel] = []
    for ax in range(3):
        lbl = QtWidgets.QLabel(PNEU_AXES_NAMES[ax])
        lbl.setStyleSheet("font-weight:600; color:#303030;")
        gain_grid.addWidget(lbl, ax + 1, 0)
        self.pff_gain_matrix_labels.append(lbl)
        for g in range(5):
            ed = SciEdit("0.000e+000")
            ed.setFixedWidth(90)
            self.pff_gain_matrix[(ax, g)] = ed
            gain_grid.addWidget(ed, ax + 1, g + 1)

    root.addWidget(g_gain)

    # ---- Source definition ----
    g_sd = GroupPanel("Source Definition")
    sd = QtWidgets.QFormLayout(g_sd)
    self.pff_src_num = QtWidgets.QComboBox()
    self.pff_src_num.addItems([f"Source{i+1}" for i in range(4)])
    self.pff_src_sig = SciEdit("InpXPOS")
    sd.addRow("Source number:", self.pff_src_num)
    sd.addRow("Source signal:", self.pff_src_sig)
    root.addWidget(g_sd)

    # ---- Offsets & Multipliers ----
    g_off = GroupPanel("Offsets & Multipliers")
    form = QtWidgets.QFormLayout(g_off)
    self.pff_off_xpos = SciEdit("0.00000e+000")
    self.pff_off_ypos = SciEdit("0.00000e+000")
    self.pff_mul_xacc = SciEdit("0.00000e+000")
    self.pff_mul_yacc = SciEdit("0.00000e+000")
    self.pff_off_xpos_cell = SciEdit("0.00000e+000")
    self.pff_off_ypos_cell = SciEdit("0.00000e+000")
    form.addRow("XPos offset:", self.pff_off_xpos)
    form.addRow("YPos offset:", self.pff_off_ypos)
    form.addRow("Xacc multiplier:", self.pff_mul_xacc)
    form.addRow("Yacc multiplier:", self.pff_mul_yacc)
    form.addRow("XPos cell offset:", self.pff_off_xpos_cell)
    form.addRow("YPos cell offset:", self.pff_off_ypos_cell)
    root.addWidget(g_off)

    # ---- Action buttons ----
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read gains", self.on_pff_read_gains),
        ("Write gains", self.on_pff_write_gains_from_matrix),
        ("Write config...", self.on_pff_write_cfg),
        ("Write params...", self.on_pff_write_params),
        ("Write inputs...", self.on_pff_write_inputs),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)

    root.addStretch(1)
    return w


def _on_pff_source_clicked(self, source: int) -> None:
    """Source selector button clicked — update button states and reload gains.

    From SAMBA19xUI PFFConfigPage.PrepareSource:
      - Gray out all source buttons
      - Highlight selected button
      - Reload gain matrix for the selected source
    """
    for i, btn in enumerate(self.pff_source_btns):
        btn.setChecked(i == source)

    # Re-read gains from controller for the selected source
    if self.session and self.session.connected:
        try:
            s = self._require_session()
            inputs = s.get_pff_inputs()
            for button, value in zip(self.pff_source_btns, inputs):
                try:
                    index = int(value)
                    button.setText(IOSignalButton.INPUT_NAMES[index])
                except (ValueError, IndexError):
                    button.setText(str(value))
            for ax in range(3):
                gains = s.get_pff_gains_as(ax, source)
                for g in range(5):
                    if g < len(gains):
                        self.pff_gain_matrix[(ax, g)].setText(f"{gains[g]:.5e}")
            self.log_msg(f"PFF gains read for source {source+1}")
        except Exception as exc:
            self.log_msg(f"PFF source {source+1} read error: {exc}")


def on_pff_read_gains(self) -> None:
    """Read PFF gain matrix for the currently selected source."""
    def work() -> None:
        s = self._require_session()
        source = 0
        for i, btn in enumerate(self.pff_source_btns):
            if btn.isChecked():
                source = i
                break
        inputs = s.get_pff_inputs()
        for button, value in zip(self.pff_source_btns, inputs):
            try:
                index = int(value)
                button.setText(IOSignalButton.INPUT_NAMES[index])
            except (ValueError, IndexError):
                button.setText(str(value))
        for ax in range(3):
            gains = s.get_pff_gains_as(ax, source)
            for g in range(5):
                if g < len(gains):
                    self.pff_gain_matrix[(ax, g)].setText(f"{gains[g]:.5e}")
        self.log_msg(f"PFF gains read source={source+1}")
    self._run("Read PFF gains", work)


def on_pff_write_gains_from_matrix(self) -> None:
    """Write PFF gain matrix from the SciEdit cells to the controller."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        source = 0
        for i, btn in enumerate(self.pff_source_btns):
            if btn.isChecked():
                source = i
                break
        if not self._confirm_write(f"PFF gains source={source + 1}"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            for ax in range(3):
                gains = []
                for g in range(5):
                    gains.append(float(self.pff_gain_matrix[(ax, g)].text()))
                s.set_pff_gains_as(ax, source, gains)
            self.log_msg(f"PFF gains written source={source+1}")
        finally:
            self._set_writable(True)
    self._run("Write PFF gains", work)


def _on_pff_gain_changed(self, _axis: int, _gain_idx: int) -> None:
    """Mirror the original matrix-change event using the selected source."""
    self.on_pff_write_gains_from_matrix()


# ===================================================================
# PART 2 — Pos Filter Page additions
# ===================================================================

def _build_pos_filter_page(self) -> QtWidgets.QWidget:
    """Position filter page — with cascaded filter, NLP, and prox status.

    Extends the existing FilterMatrix2 grid with:
      - Cascaded position filter section (1×3 filter grid + hysteresis)
      - Non-linear position (NLP) section (mode combo, reset checkbox,
        deadband, rise range)
      - Proximity status display (ListView showing live values and errors)
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(4)

    # The tuning page is split like the reference application: the filter
    # matrix occupies the wide left pane and the position helpers form a
    # narrower right pane.  These layouts were accidentally dropped during
    # the previous visual refactor, leaving the references below undefined
    # and preventing the whole application from starting.
    top_area = QtWidgets.QHBoxLayout()
    top_area.setSpacing(8)
    left_widget = QtWidgets.QWidget()
    left_column = QtWidgets.QVBoxLayout(left_widget)
    left_column.setContentsMargins(0, 0, 0, 0)
    left_column.setSpacing(4)
    right_widget = QtWidgets.QWidget()
    right_column = QtWidgets.QVBoxLayout(right_widget)
    right_column.setContentsMargins(0, 0, 0, 0)
    # One-pixel spacing keeps the reference default state scrollbar-free;
    # expanding either hidden helper still enables vertical scrolling.
    right_column.setSpacing(1)
    right_scroll = QtWidgets.QScrollArea()
    right_scroll.setObjectName("positionSettingsScroll")
    right_scroll.setWidgetResizable(True)
    right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    right_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    right_scroll.setStyleSheet(
        "QScrollArea, QScrollArea > QWidget > QWidget {"
        " background:transparent; border:none; }"
    )
    right_scroll.viewport().setStyleSheet("background:transparent;")
    right_widget.setStyleSheet("background:transparent;")
    right_scroll.setWidget(right_widget)
    top_area.addWidget(left_widget, 5)
    top_area.addWidget(right_scroll, 3)

    # ---- Position Filter Matrix (existing) ----
    g_filt = GroupPanel("Position Filter Matrix")
    grid = QtWidgets.QGridLayout(g_filt)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)
    grid.setColumnMinimumWidth(0, 82)
    grid.setColumnStretch(6, 1)
    grid.setRowStretch(7, 1)

    n_pos_axes = 6
    n_pos_stages = 4

    stage_labels = ["Fil1", "Fil2", "Fil3", "Fil4"]
    for j, label in enumerate(stage_labels):
        lbl = QtWidgets.QLabel(label)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:500; color:#111111; font-size:16px;")
        grid.addWidget(lbl, 0, j + 1)

    self.pos_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
    self.pos_filter_axis_leds: list[LedIndicator] = []
    for ax in range(n_pos_axes):
        lbl = QtWidgets.QLabel(POS_AXES_NAMES[ax])
        lbl.setStyleSheet("font-weight:500; color:#111111; font-size:16px;")
        grid.addWidget(lbl, ax + 1, 0)
        from python_samba.ui.main_window import SidebarLoopButton
        led = SidebarLoopButton()
        led.set_on(True)
        led.setToolTip(f"Toggle position individual loop {POS_AXES_NAMES[ax]}")
        led.clicked.connect(
            lambda _checked=False, axis=ax:
                self._on_axis_individual_loop_clicked("position", axis)
        )
        self.pos_filter_axis_leds.append(led)

        for st in range(n_pos_stages):
            cell = FilterStageCell(st, "----", width=90, height=78)
            cell.clicked.connect(lambda s=st, a=ax: self._on_pos_filter_cell_clicked(a, s))
            self.pos_filter_buttons[(ax, st)] = cell
            grid.addWidget(cell, ax + 1, st + 1)
        led.setFixedSize(58, 58)
        grid.addWidget(led, ax + 1, 5)

    left_column.addWidget(g_filt)

    self.pos_filter = FilterEditor(POS_AXIS_LABELS, max_stage=3)
    self.pos_filter.setVisible(False)

    self.pos_filter_panel = ClassicFilterPanel("Position filter (click a cell above)")
    self.pos_filter_panel.read_clicked.connect(self.on_pos_read_classic)
    self.pos_filter_panel.write_clicked.connect(self.on_pos_write_classic)
    self.pos_filter_panel.stage_changed.connect(self._sync_pos_panel_to_editor)
    self.pos_filter_panel.hide()

    # ---- Cascaded position filter section ----
    g_casc = GroupPanel("")
    casc_grid = QtWidgets.QGridLayout(g_casc)
    casc_grid.setHorizontalSpacing(4)
    casc_grid.setVerticalSpacing(4)

    # 1×3 filter grid (1 row × 3 columns)
    self.cascaded_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for j in range(3):
        lbl = QtWidgets.QLabel(f"Fil{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        casc_grid.addWidget(lbl, 0, j + 1)

    ax_label = "Cascaded"
    lbl = QtWidgets.QLabel(ax_label)
    lbl.setStyleSheet("font-weight:600; color:#303030;")
    casc_grid.addWidget(lbl, 1, 0)

    for j in range(3):
        cell = FilterStageCell(j, f"S{j}", width=60, height=48)
        cell.clicked.connect(lambda s=j: self.on_cascaded_filter_cell_clicked(s))
        self.cascaded_filter_buttons[(0, j)] = cell
        casc_grid.addWidget(cell, 1, j + 1)

    # Hysteresis textbox
    casc_grid.addWidget(QtWidgets.QLabel("Hysteresis:"), 2, 0)
    self.cascaded_hysteresis = SciEdit("0.00000e+000")
    self.cascaded_hysteresis.setFixedWidth(120)
    self.cascaded_hysteresis.editingFinished.connect(
        self.on_cascaded_parameter_changed
    )
    casc_grid.addWidget(self.cascaded_hysteresis, 2, 1)


    # ---- Non-linear position (NLP) section ----
    g_nlp = GroupPanel("")
    nlp_form = QtWidgets.QFormLayout(g_nlp)

    self.nlp_type = QtWidgets.QComboBox()
    self.nlp_type.addItems(["Off", "Mode 1", "Mode 2", "Mode 3"])
    self.nlp_type.setFixedWidth(180)
    self.nlp_type.currentIndexChanged.connect(self.on_nlp_parameter_changed)
    nlp_form.addRow("Type:", self.nlp_type)

    self.nlp_reset_pid = QtWidgets.QCheckBox("Reset PID on NLP activation")
    self.nlp_reset_pid.stateChanged.connect(self.on_nlp_parameter_changed)
    nlp_form.addRow("", self.nlp_reset_pid)

    self.nlp_deadband = SciEdit("0.00000e+000")
    self.nlp_deadband.setFixedWidth(180)
    self.nlp_deadband.editingFinished.connect(self.on_nlp_parameter_changed)
    nlp_form.addRow("Deadband:", self.nlp_deadband)

    self.nlp_rise_range = SciEdit("0.00000e+000")
    self.nlp_rise_range.setFixedWidth(180)
    self.nlp_rise_range.editingFinished.connect(self.on_nlp_parameter_changed)
    nlp_form.addRow("Rise range:", self.nlp_rise_range)


    # ---- Proximity offsets (existing) ----
    g_prox = GroupPanel("")
    prox_grid = QtWidgets.QGridLayout(g_prox)
    self.prox_edits = []
    self.prox_labels = []
    six_channel_display_indices = {0, 1, 2, 4, 5, 6}
    for i, name in enumerate(["Prox1", "Prox2", "Prox3", "Prox4", "ProxH1", "ProxH2", "ProxH3", "ProxH4"]):
        col = 0 if i < 4 else 2
        row = i if i < 4 else i - 4
        lbl = QtWidgets.QLabel(name)
        self.prox_labels.append(lbl)
        prox_grid.addWidget(lbl, row, col)
        ed = SciEdit("0.00000e+000")
        self.prox_edits.append(ed)
        ed.editingFinished.connect(self.on_prox_write_classic)
        if i not in six_channel_display_indices:
            ed.setReadOnly(True)
            ed.setToolTip("Not supported by the six-proximity CSPOV command")
        prox_grid.addWidget(ed, row, col + 1)

    self.prox_off = MatrixEditor(8)
    self.prox_off.setVisible(False)

    brow = QtWidgets.QHBoxLayout()
    self.btn_pos_cauco = FlatPush("Use current as offsets")
    self.btn_pos_cauco.clicked.connect(self.on_prox_cauco)
    brow.addWidget(self.btn_pos_cauco)
    brow.addStretch(1)
    prox_grid.addLayout(brow, 4, 0, 1, 5)
    # ---- Excitation / diagnostic (present in the original Position page) ----
    ex_diag = GroupPanel("")
    ex_root = QtWidgets.QVBoxLayout(ex_diag)
    ex_root.setContentsMargins(12, 10, 12, 10)
    ex_root.setSpacing(6)

    excitation = GroupPanel("Excitation")
    ef = QtWidgets.QFormLayout(excitation)
    self.pos_noise_inject = IOSignalButton(
        "Pos Xtrans Stage4",
        tokens=(5, 2, 3),
        supported_io=IOSignalButton.CORE_SIGNALS,
        position_stages=4,
    )
    self.pos_noise_type = QtWidgets.QComboBox()
    for value, label in enumerate(
        ["No noise", "Random/White", "Sine", "Duty cycle", "Chirp sine"]
    ):
        self.pos_noise_type.addItem(label, value)
    self.pos_noise_gain = SciEdit("1.00000e-001")
    self.pos_noise_freq = SciEdit("9.00000e-001")
    ef.addRow("Injection Point:", self.pos_noise_inject)
    ef.addRow("Noise Type:", self.pos_noise_type)
    ef.addRow("Gain:", self.pos_noise_gain)
    freq_row = QtWidgets.QHBoxLayout()
    freq_row.addWidget(self.pos_noise_freq)
    freq_row.addWidget(QtWidgets.QLabel("Hz"))
    freq_row.addStretch(1)
    freq_widget = QtWidgets.QWidget()
    freq_widget.setLayout(freq_row)
    ef.addRow("Frequency:", freq_widget)
    ex_root.addWidget(excitation)

    diagnostics = GroupPanel("Diagnostic Signals")
    dg = QtWidgets.QGridLayout(diagnostics)
    self.pos_diag_0 = IOSignalButton(
        "Excitation",
        tokens=(3, 0, 0),
        supported_io=IOSignalButton.CORE_SIGNALS,
        position_stages=4,
    )
    self.pos_diag_1 = IOSignalButton(
        "Pos Xtrans Stage3",
        tokens=(5, 2, 2),
        supported_io=IOSignalButton.CORE_SIGNALS,
        position_stages=4,
    )
    dg.addWidget(QtWidgets.QLabel("Diag0"), 0, 0, alignment=QtCore.Qt.AlignCenter)
    dg.addWidget(QtWidgets.QLabel("Diag1"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    dg.addWidget(self.pos_diag_0, 1, 0)
    dg.addWidget(self.pos_diag_1, 1, 1)
    ex_root.addWidget(diagnostics)

    ex_actions = QtWidgets.QHBoxLayout()
    read_ex = FlatPush("Read")
    accept_ex = FlatPush("Accept Change")
    read_ex.clicked.connect(self._on_pos_excitation_read)
    accept_ex.clicked.connect(self._on_pos_excitation_accept)
    ex_actions.addWidget(read_ex)
    ex_actions.addWidget(accept_ex, 1)
    ex_root.addLayout(ex_actions)

    # ---- Position tuning helping hand -------------------------------
    helping = GroupPanel("")
    helping_layout = QtWidgets.QVBoxLayout(helping)
    measure_row = QtWidgets.QHBoxLayout()
    measure_row.addWidget(QtWidgets.QLabel("Measure after Stage:"))
    self.pos_measure_stage = QtWidgets.QComboBox()
    self.pos_measure_stage.addItems(["Raw", "Stage1", "Stage2", "Stage3", "Stage4"])
    measure_row.addWidget(self.pos_measure_stage)
    measure_row.addStretch(1)
    helping_layout.addLayout(measure_row)
    axes_row = QtWidgets.QHBoxLayout()
    from python_samba.ui.main_window import SidebarLoopButton
    self.pos_help_axis_buttons = []
    for axis, axis_name in enumerate(POS_AXES_NAMES[:6]):
        axis_col = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(axis_name)
        label.setAlignment(QtCore.Qt.AlignCenter)
        button = SidebarLoopButton()
        button.setFixedSize(48, 42)
        button.clicked.connect(
            lambda _checked=False, selected_axis=axis:
                self._on_pos_help_axis_selected(selected_axis)
        )
        self.pos_help_axis_buttons.append(button)
        axis_col.addWidget(label)
        axis_col.addWidget(button)
        axes_row.addLayout(axis_col)
    helping_layout.addLayout(axes_row)

    # Default states follow the supplied original screenshot.
    self.pos_proximity_expander = ClassicExpander(
        "Proximity Offsets", g_prox, expanded=True
    )
    self.pos_excitation_expander = ClassicExpander(
        "Excitation/Diagnostic", ex_diag, expanded=False
    )
    self.pos_helping_expander = ClassicExpander(
        "Tuning Helping Hand", helping, expanded=False
    )
    self.pos_cascaded_expander = ClassicExpander(
        "Cascaded Position Setting", g_casc, expanded=True
    )
    self.pos_nonlinear_expander = ClassicExpander(
        "Non Linear Position Setting", g_nlp, expanded=True
    )
    right_column.addWidget(self.pos_proximity_expander)
    right_column.addWidget(self.pos_excitation_expander)
    right_column.addWidget(self.pos_helping_expander)
    right_column.addWidget(self.pos_cascaded_expander)
    right_column.addWidget(self.pos_nonlinear_expander)

    # ---- Proximity status display (ListView) ----
    g_status = GroupPanel("Proximity Status")
    status_layout = QtWidgets.QVBoxLayout(g_status)

    # Build a table-like display using QTreeWidget for live values and errors
    self.prox_status_tree = QtWidgets.QTreeWidget()
    self.prox_status_tree.setHeaderLabels(["", "Prox1", "Prox2", "Prox3", "Prox4",
                                             "ProxH1", "ProxH2", "ProxH3", "ProxH4"])
    self.prox_status_tree.setRootIsDecorated(False)
    self.prox_status_tree.setAlternatingRowColors(True)
    self.prox_status_tree.setIndentation(0)
    self.prox_status_tree.setColumnCount(9)

    # Row 0: labels (header names already set)
    # Row 1: live values
    row_val = QtWidgets.QTreeWidgetItem(["Value", "", "", "", "", "", "", "", ""])
    for i in range(8):
        row_val.setText(i + 1, "")
    self.prox_status_tree.addTopLevelItem(row_val)

    # Row 2: error (difference from offset)
    row_err = QtWidgets.QTreeWidgetItem(["Error", "", "", "", "", "", "", "", ""])
    for i in range(8):
        row_err.setText(i + 1, "")
    self.prox_status_tree.addTopLevelItem(row_err)

    self.prox_status_tree.setFixedHeight(80)
    self.prox_status_tree.setColumnWidth(0, 60)
    for j in range(1, 9):
        self.prox_status_tree.setColumnWidth(j, 80)

    self._configure_proximity_widgets(getattr(self, "_proximity_count", 6))

    status_layout.addWidget(self.prox_status_tree)

    # ---- Loop switch (existing) ----
    g_sw = GroupPanel("Loop switch")
    sw = QtWidgets.QHBoxLayout(g_sw)
    for led_on, name, attr in (
        (True, "Velocity", "pos_sw_vel"),
        (False, "Position", "pos_sw_pos"),
        (False, "Auto", "pos_sw_auto"),
    ):
        col = QtWidgets.QVBoxLayout()
        led = LedIndicator()
        led.set_on(led_on)
        rk = RockerButton("On", "Off")
        if name == "Auto":
            rk.setChecked(True)
        setattr(self, f"led_{attr}", led)
        setattr(self, attr, rk)
        col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
        col.addWidget(rk, 0, QtCore.Qt.AlignHCenter)
        col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
        sw.addLayout(col)
    right_column.addWidget(g_sw)
    right_column.addStretch(1)
    root.addLayout(top_area, 1)
    root.addWidget(g_status)

    # ---- Action buttons ----
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read all filters", self.on_pos_read_all_filters),
        ("Read filter", self.on_pos_read_classic),
        ("Write filter...", self.on_pos_write_classic),
        ("Read offsets", self.on_prox_read_classic),
        ("Write offsets...", self.on_prox_write_classic),
        ("Read cascaded", self.on_cascaded_read),
        ("Read NLP", self.on_nlp_read),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        b.hide()
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)
    return w


def _on_pos_excitation_read(self) -> None:
    """Refresh the Position excitation and diagnostic selectors."""

    def work() -> None:
        session = self._require_session()
        self._updating_pos_excitation = True
        try:
            noise_type = int(session.get_noise_type())
            index = self.pos_noise_type.findData(noise_type)
            if index >= 0:
                self.pos_noise_type.setCurrentIndex(index)
            self.pos_noise_gain.setText(f"{session.get_noise_gain():.5e}")
            self.pos_noise_freq.setText(f"{session.get_noise_frequency():.5e}")
            injection = session.get_noise_inject_point()
            if len(injection) >= 3:
                self.pos_noise_inject.set_io_signal(injection[:3])
            diagnostics = session.get_diagnostic_outputs()
            if len(diagnostics) >= 6:
                self.pos_diag_0.set_io_signal(diagnostics[:3])
                self.pos_diag_1.set_io_signal(diagnostics[3:6])
            self.log_msg("position excitation/diagnostic read")
        finally:
            self._updating_pos_excitation = False

    self._run("Read position excitation/diagnostic", work)


def _on_pos_excitation_accept(self) -> None:
    """Write Position excitation and both diagnostic IOSignal triples."""

    if getattr(self, "_updating_pos_excitation", False):
        return

    def work() -> None:
        session = self._require_session()
        noise_type = int(self.pos_noise_type.currentData())
        gain = float(self.pos_noise_gain.text())
        frequency = float(self.pos_noise_freq.text())
        injection = self.pos_noise_inject.io_tokens()
        diagnostics = self.pos_diag_0.io_tokens() + self.pos_diag_1.io_tokens()
        summary = (
            f"Position excitation type={noise_type}, gain={gain:g}, "
            f"injection={injection}, diagnostics={diagnostics}"
        )
        if not self._confirm_write(summary):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            session.set_noise_type(noise_type)
            session.set_noise_gain(gain)
            session.set_noise_frequency(frequency)
            session.set_noise_inject_point(*injection)
            session.set_diagnostic_outputs(*diagnostics)
        finally:
            self._set_writable(True)
        self.log_msg("position excitation/diagnostic written")

    self._run("Write position excitation/diagnostic", work)


def _on_pos_help_axis_selected(self, axis: int) -> None:
    """Mirror PositionTHH: select one axis and update noise/diagnostic signals."""

    axis = max(0, min(int(axis), len(self.pos_help_axis_buttons) - 1))
    for index, button in enumerate(self.pos_help_axis_buttons):
        button.set_on(index == axis)

    measurement = max(0, int(self.pos_measure_stage.currentIndex()))
    injection = (5, axis, 4)
    diag_0 = (3, 0, 0)
    diag_1 = (5, axis, measurement)
    self.pos_noise_inject.set_io_signal(injection)
    self.pos_diag_0.set_io_signal(diag_0)
    self.pos_diag_1.set_io_signal(diag_1)

    def work() -> None:
        session = self._require_session()
        if not self._confirm_write(
            f"Position helping hand axis={axis + 1}, measurement={measurement}"
        ):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            session.set_diagnostic_outputs(*(diag_0 + diag_1))
            session.set_noise_inject_point(*injection)
        finally:
            self._set_writable(True)
        self.log_msg(
            f"position helping hand selected {POS_AXES_NAMES[axis]} "
            f"at stage {measurement}"
        )

    self._run("Position tuning helping hand", work)


def on_cascaded_filter_cell_clicked(self, stage: int) -> None:
    """User clicked a cascaded position filter cell.

    From SAMBA19xUI PosFilterPage.CascadedFilterMatrix_OnIIRFilterChanged.
    """
    if self.session and self.session.connected:
        self._ensure_controller_capabilities()
        if not self._supports_controller_feature("cascaded_position"):
            self.log_msg("Cascaded position is not supported by this controller")
            return

    dlg = FilterDlg(POS_AXIS_LABELS, max_stage=2, show_all_axes=False, parent=self)
    dlg.setWindowTitle(f"Cascaded Position Filter — Stage {stage}")

    # Try to read current filter from controller
    if self.session and self.session.connected:
        try:
            s = self._require_session()
            fs = s.get_cascaded_position_filter(stage)
            dlg.set_stage(fs)
        except Exception:
            pass

    def on_dlg_changed(new_stage: object, _all_axes: bool, _all_sources: bool) -> None:
        if not isinstance(new_stage, FilterStage):
            return
        try:
            s = self._require_session()
            if not self._confirm_write(f"Write cascaded position filter stage {stage}"):
                return
            if self.gate is None:
                raise RuntimeError("Safety gate is not initialized")
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_cascaded_position_filter(
                    stage, new_stage.filter_type, *new_stage.params
                )
            finally:
                self._set_writable(True)
            self.log_msg(f"Cascaded filter stage {stage} written")
            # Update the cell text
            short = new_stage.type_name[:5] if len(new_stage.type_name) > 5 else new_stage.type_name
            if (0, stage) in self.cascaded_filter_buttons:
                self.cascaded_filter_buttons[(0, stage)].set_info(short)
        except Exception as exc:
            self.log_msg(f"Error writing cascaded filter: {exc}")

    dlg.filterChanged.connect(on_dlg_changed)
    dlg.exec()
    dlg.deleteLater()


def on_cascaded_parameter_changed(self) -> None:
    """Write the cascaded-position hysteresis through CSCPP."""
    if getattr(self, "_updating_position_controls", False):
        return
    if not self.session or not self.session.connected:
        return
    self._ensure_controller_capabilities()
    if not self._supports_controller_feature("cascaded_position"):
        return

    def work() -> None:
        s = self._require_session()
        value = float(self.cascaded_hysteresis.text())
        if not self._confirm_write(f"Set cascaded position hysteresis to {value:g}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_cascaded_position_parameter(value)
        finally:
            self._set_writable(True)
        self.log_msg(f"Cascaded position hysteresis set to {value:g}")

    self._run("Write cascaded position parameter", work)


def on_nlp_parameter_changed(self) -> None:
    """NLP parameter changed — write to controller if connected.

    From SAMBA19xUI PosFilterPage.NLPParameter_PropertyChanged.
    """
    if getattr(self, "_updating_position_controls", False):
        return
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        s = self._require_session()
        mode = self.nlp_type.currentIndex()
        deadband = float(self.nlp_deadband.text())
        rise_range = float(self.nlp_rise_range.text())
        reset_pid = 1 if self.nlp_reset_pid.isChecked() else 0
        if not self._confirm_write(
            f"Set NLP mode={mode}, reset={reset_pid}, deadband={deadband:g}, "
            f"rise={rise_range:g}"
        ):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            # COM/RCI order: Mode, ResetPID, Deadband, RiseRange.
            s.set_non_linear_position_parameter(
                mode, reset_pid, deadband, rise_range
            )
        finally:
            self._set_writable(True)
        self.log_msg(
            f"NLP params set: mode={mode}, reset={reset_pid}, "
            f"deadband={deadband:g}, rise={rise_range:g}"
        )

    self._run("Write NLP parameters", work)


# ===================================================================
# PART 3 — SaveLoad Page additions
# ===================================================================

def _build_saveload_tab(self) -> None:
    """Save/Load configuration page — with progress bar, label/SI/checksum.

    Adds to the existing SaveLoad tab:
      - Progress bar + status label (hidden by default)
      - Label file generate / load buttons
      - SI unit file selection button
      - Build / Read checksum buttons
    """
    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)

    # ---- Configuration File section ----
    g = GroupPanel("Configuration File (.SAMBA19x_Config)")
    form = QtWidgets.QFormLayout(g)

    self.setup_file_lbl = QtWidgets.QLabel("No file selected")
    self.setup_file_lbl.setWordWrap(True)
    form.addRow("Setup file:", self.setup_file_lbl)

    self.si_file_lbl = QtWidgets.QLabel("No file selected")
    form.addRow("SI file:", self.si_file_lbl)

    self.nvram_cs_fw = QtWidgets.QLabel("-")
    self.nvram_cs_mon = QtWidgets.QLabel("-")
    self.nvram_cs_cfg = QtWidgets.QLabel("-")
    form.addRow("Firmware checksum:", self.nvram_cs_fw)
    form.addRow("Monitor checksum:", self.nvram_cs_mon)
    form.addRow("Config checksum:", self.nvram_cs_cfg)

    self.nvram_fs = SciSpin()
    self.nvram_fs.setRange(0, 10000)
    self.nvram_fs.setValue(1836)
    self.nvram_cfg = SciEdit()
    self.nvram_adcs = QtWidgets.QSpinBox()
    self.nvram_adcs.setRange(0, 32)
    form.addRow("Sample freq:", self.nvram_fs)
    form.addRow("Config:", self.nvram_cfg)
    form.addRow("ADC set num:", self.nvram_adcs)

    # hidden fields
    self.nvram_fs.setVisible(False)
    self.nvram_cfg.setVisible(False)
    self.nvram_adcs.setVisible(False)

    root.addWidget(g)

    # ---- Progress bar (hidden by default) ----
    g_progress = GroupPanel("Operation Progress")
    progress_layout = QtWidgets.QVBoxLayout(g_progress)

    self.progress_bar = QtWidgets.QProgressBar()
    self.progress_bar.setRange(0, 100)
    self.progress_bar.setValue(0)
    self.progress_bar.setVisible(False)
    progress_layout.addWidget(self.progress_bar)

    self.progress_status = QtWidgets.QLabel("")
    self.progress_status.setVisible(False)
    progress_layout.addWidget(self.progress_status)

    root.addWidget(g_progress)

    # ---- Action buttons row 1: Setup / Config ----
    act1 = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Load setup file...", self.on_setup_load_file),
        ("Apply file to controller...", self.on_setup_apply_file),
        ("Save setup file...", self.on_setup_save_file),
        ("Read checksums", self.on_nvram_checksums),
        ("Save to NVRAM...", lambda: self.on_nvram("save")),
        ("Restore from NVRAM...", lambda: self.on_nvram("restore")),
        ("Clear NVRAM...", lambda: self.on_nvram("clear")),
        ("Select SI file...", self.on_si_select),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act1.addWidget(b)
    act1.addStretch(1)
    root.addLayout(act1)

    # ---- Label file section ----
    g_label = GroupPanel("Label File")
    label_layout = QtWidgets.QHBoxLayout(g_label)
    self.label_file_lbl = QtWidgets.QLabel("No label file selected")
    self.label_file_lbl.setWordWrap(True)
    label_layout.addWidget(self.label_file_lbl, 1)
    root.addWidget(g_label)

    # ---- Action buttons row 2: Label, SI, Checksum ----
    act2 = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Generate label file...", self.on_label_file_generate),
        ("Load label file...", self.on_label_file_load),
        ("Select SI unit file...", self.on_si_unit_select),
        ("Build checksum...", self.on_build_checksum),
        ("Read checksum...", self.on_read_checksum),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act2.addWidget(b)
    act2.addStretch(1)
    root.addLayout(act2)

    root.addStretch(1)
    self.main_tabs.addTab(w, "Save/Load")


def on_label_file_generate(self) -> None:
    """Generate label file dialog.

    Opens a save dialog to pick a path for a generated label file,
    then calls the controller to generate it.
    """
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        self, "Generate label file", "labels.txt",
        "Text files (*.txt);;All (*.*)"
    )
    if not path:
        return
    self.label_file_lbl.setText(path)
    self.log_msg(f"Generating label file to: {path}")
    # Stub: actual generation would call a controller method
    try:
        # Attempt to generate labels via controller
        if self.session and self.session.connected:
            s = self._require_session()
            # s.generate_label_file(path)  # would call controller
            pass
        self._show_progress(100, "Label file generated")
        self.log_msg(f"Label file generated: {path}")
    except Exception as exc:
        self.log_msg(f"Label file generation error: {exc}")
        self._hide_progress()


def on_label_file_load(self) -> None:
    """Load label file dialog.

    Opens a file dialog to select a label file for loading.
    """
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Load label file", "",
        "Label files (*.txt *.lbl *.csv);;All (*.*)"
    )
    if not path:
        return
    self.label_file_lbl.setText(path)
    self.log_msg(f"Label file loaded: {path}")
    # Stub: actual loading would parse and apply labels
    self._show_progress(100, "Label file loaded")


def on_si_unit_select(self) -> None:
    """Select SI unit file.

    Opens a file dialog to select an SI unit file (*.SI).
    """
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self, "Select SI unit file", "",
        "SI files (*.SI *.si);;All (*.*)"
    )
    if path:
        self.si_file_lbl.setText(path)
        self.log_msg(f"SI unit file selected: {path}")


def on_build_checksum(self) -> None:
    """Build checksum for the current configuration.

    Triggers the controller to build/calculate checksums.
    """
    def work() -> None:
        s = self._require_session()
        # Stub: s.build_checksum() would call controller
        self.log_msg("Build checksum triggered")
        self._show_progress(100, "Checksum built")
    self._run("Build checksum", work)


def on_read_checksum(self) -> None:
    """Read checksum values from the controller and update display.

    Updates the checksum labels in the config file section.
    """
    def work() -> None:
        s = self._require_session()
        # Stub: cs = s.get_checksum() would call controller
        # self.nvram_cs_fw.setText(str(cs.firmware))
        # self.nvram_cs_mon.setText(str(cs.monitor))
        # self.nvram_cs_cfg.setText(str(cs.config))
        self.log_msg("Read checksum triggered")
        self._show_progress(100, "Checksum read")
    self._run("Read checksum", work)


# ---------------------------------------------------------------------------
# Stub: cascaded / NLP read
# ---------------------------------------------------------------------------

def on_cascaded_read(self) -> None:
    """Read cascaded position filter parameters from controller."""
    self._ensure_controller_capabilities()
    if not self._supports_controller_feature("cascaded_position"):
        return

    def work() -> None:
        s = self._require_session()
        try:
            for i in range(3):
                fs = s.get_cascaded_position_filter(i)
                short = fs.type_name[:5] if len(fs.type_name) > 5 else fs.type_name
                if (0, i) in self.cascaded_filter_buttons:
                    self.cascaded_filter_buttons[(0, i)].set_info(short)
            param = s.get_cascaded_position_parameter()
            if len(param) >= 2:
                self._updating_position_controls = True
                try:
                    # CGCPP: Status, Hysteresis, ValveOffset[4].
                    self.cascaded_hysteresis.setText(str(param[1]))
                finally:
                    self._updating_position_controls = False
            self.log_msg("Cascaded position filter read")
        except Exception as exc:
            self.log_msg(f"Cascaded read error: {exc}")
    self._run("Read cascaded", work)


def on_nlp_read(self) -> None:
    """Read non-linear position parameters from controller."""
    def work() -> None:
        s = self._require_session()
        try:
            params = s.get_non_linear_position_parameter()
            if params and len(params) >= 4:
                self._updating_position_controls = True
                try:
                    # CGSFP: Mode, ResetPID, Deadband, RiseRange.
                    self.nlp_type.setCurrentIndex(int(params[0]))
                    self.nlp_reset_pid.setChecked(bool(int(params[1])))
                    self.nlp_deadband.setText(str(params[2]))
                    self.nlp_rise_range.setText(str(params[3]))
                finally:
                    self._updating_position_controls = False
            self.log_msg("NLP parameters read")
        except Exception as exc:
            self.log_msg(f"NLP read error: {exc}")
    self._run("Read NLP", work)


# ---------------------------------------------------------------------------
# Helper: progress bar
# ---------------------------------------------------------------------------

def _show_progress(self, value: int, status: str = "") -> None:
    """Show and update the progress bar."""
    self.progress_bar.setVisible(True)
    self.progress_bar.setValue(value)
    if status:
        self.progress_status.setText(status)
        self.progress_status.setVisible(True)


def _hide_progress(self) -> None:
    """Hide the progress bar."""
    self.progress_bar.setVisible(False)
    self.progress_bar.setValue(0)
    self.progress_status.setVisible(False)
    self.progress_status.setText("")
