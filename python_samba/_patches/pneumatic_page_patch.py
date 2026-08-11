"""
Pneumatic Page Patch
====================
Replaces the simple _build_pneumatic_tab method with a comprehensive version
matching the SAMBA19xUI PneuSystemPage C# code.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    ClassicExpander,
    ClassicFilterPanel,
    FilterStageBar,
    FilterStageCell,
    FlatPush,
    GroupPanel,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
    format_ui_number,
)
from python_samba.ui.widgets import FilterDlg, FilterEditor, MatrixEditor
from python_samba.ui.main_window import PNEU_AXES_NAMES as _PNEU_AXES_NAMES

# ---------------------------------------------------------------------------
# Replacement for MainWindow._build_pneumatic_tab
# ---------------------------------------------------------------------------

_SCEDIT_WIDTH = 80
_SMALL_BTN_W = 72
_MED_BTN_W = 90

_PNEU_VERTICAL_STATUS_NAMES = (
    "Down",
    "Going2SoftStop",
    "Up Soft",
    "Going Up",
    "UP",
    "Going Down",
    "Initialisation",
    "OK",
)


def _pneumatic_config_int(value: object) -> int:
    """Parse one legacy Floatation Config Int32 field without truncation."""

    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected an integer, got {value!r}") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"expected an integer, got {value!r}")
    return int(number)


def _build_pneumatic_tab(self) -> None:
    """Pneumatic tab — comprehensive page matching PneuSystemPage.

    Layout (matching SAMBA19xUI PneuSystemPage):
      - Pneumatic Filter Matrix (3 axes x 4 stages, existing)
      - Input Steering Matrix (8 inputs x 3 axes)
      - Output Steering Matrix (8 valves x 3 axes)
      - Valve Up/Down offsets (8 each)
      - ISO offsets (3 motor + 3 isolator)
      - Dither settings
      - Ramp parameters
      - Floatation settings
      - Loop status LEDs
      - Live status ListView
      - Action buttons
    """
    from python_samba.ui.extra_pages import PNEUM_AXIS_LABELS

    # Main widget with scroll area — so many features need scrolling
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea { background: transparent; }")

    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # ==================================================================
    # 1. Pneumatic filter grid: 3 axes x 4 stages (kept from existing)
    # ==================================================================
    g_filt = GroupPanel("Pneumatic Filter Matrix")
    grid = QtWidgets.QGridLayout(g_filt)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)

    for j in range(4):
        lbl = QtWidgets.QLabel(f"Fil{j+1}")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        grid.addWidget(lbl, 0, j + 1)

    self.pneum_filter_buttons: dict[tuple[int, int], FilterStageCell] = {}
    for ax in range(3):
        lbl = QtWidgets.QLabel(_PNEU_AXES_NAMES[ax])
        lbl.setStyleSheet("font-weight:600; color:#303030;")
        grid.addWidget(lbl, ax + 1, 0)
        for st in range(4):
            cell = FilterStageCell(st, f"S{st}", width=40, height=44)
            cell.clicked.connect(lambda s=st, a=ax: self._on_pneum_filter_cell_clicked(a, s))
            self.pneum_filter_buttons[(ax, st)] = cell
            grid.addWidget(cell, ax + 1, st + 1)

    root.addWidget(g_filt)

    self.pneum_axis_combo = QtWidgets.QComboBox()
    for name in _PNEU_AXES_NAMES:
        self.pneum_axis_combo.addItem(name)
    self.pneum_filter = FilterEditor(PNEUM_AXIS_LABELS, max_stage=3)
    self.pneum_filter.setVisible(False)
    self.pneum_filter_panel = ClassicFilterPanel("Pneumatic filter (click a cell above)")
    self.pneum_filter_panel.read_clicked.connect(self.on_pneum_filter_read)
    self.pneum_filter_panel.write_clicked.connect(self.on_pneum_filter_write)
    root.addWidget(self.pneum_filter_panel)

    # ==================================================================
    # 2. Steering matrices — Input (8 inputs x 3 axes) + Output (8 valves x 3 axes)
    # ==================================================================
    steer_row = QtWidgets.QHBoxLayout()

    # --- Input Steering Matrix ---
    g_in = GroupPanel("Input Steering Matrix (8 inputs x 3 axes)")
    in_grid = QtWidgets.QGridLayout(g_in)
    in_grid.setSpacing(2)
    in_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_input_matrix(self, in_grid)
    steer_row.addWidget(g_in)

    # --- Output Steering Matrix ---
    g_out = GroupPanel("Output Steering Matrix (8 valves x 3 axes)")
    out_grid = QtWidgets.QGridLayout(g_out)
    out_grid.setSpacing(2)
    out_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_output_matrix(self, out_grid)
    steer_row.addWidget(g_out)

    root.addLayout(steer_row)

    # ==================================================================
    # 3. Valve Offsets + ISO offsets + Dither + Floatation (right-side panel)
    # ==================================================================
    params_row = QtWidgets.QHBoxLayout()

    # --- Valve Up/Down Offsets (8 each) ---
    g_vo = GroupPanel("Valve Up / Down Offsets")
    vo_grid = QtWidgets.QGridLayout(g_vo)
    vo_grid.setSpacing(2)
    vo_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_valve_offsets(self, vo_grid)
    params_row.addWidget(g_vo)

    # --- Right column: ISO offsets + Dither + Floatation ---
    right_col = QtWidgets.QVBoxLayout()
    right_col.setSpacing(4)

    g_iso = GroupPanel("ISO Offsets (motor[8..10])")
    iso_grid = QtWidgets.QGridLayout(g_iso)
    iso_grid.setSpacing(2)
    iso_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_iso_offsets(self, iso_grid)
    right_col.addWidget(g_iso)

    g_dith = GroupPanel("Dither")
    dith_grid = QtWidgets.QGridLayout(g_dith)
    dith_grid.setSpacing(2)
    dith_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_dither(self, dith_grid)
    right_col.addWidget(g_dith)

    g_float = GroupPanel("Floatation")
    float_grid = QtWidgets.QGridLayout(g_float)
    float_grid.setSpacing(2)
    float_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_floatation(self, float_grid)
    right_col.addWidget(g_float)

    right_w = QtWidgets.QWidget()
    right_w.setLayout(right_col)
    params_row.addWidget(right_w)

    root.addLayout(params_row)

    # ==================================================================
    # 4. Ramp parameters
    # ==================================================================
    g_ramp = GroupPanel("Ramp Parameters")
    ramp_grid = QtWidgets.QGridLayout(g_ramp)
    ramp_grid.setSpacing(2)
    ramp_grid.setContentsMargins(4, 4, 4, 4)
    _build_pneum_ramp_params(self, ramp_grid)
    root.addWidget(g_ramp)

    # ==================================================================
    # 5. Loop status LEDs
    # ==================================================================
    g_loop = GroupPanel("Loop Status")
    loop_row = QtWidgets.QHBoxLayout(g_loop)
    loop_row.setSpacing(8)
    _build_pneum_loop_status(self, loop_row)
    root.addWidget(g_loop)

    # ==================================================================
    # 6. Live status ListView + Move system + Use-current buttons
    # ==================================================================
    status_row = QtWidgets.QHBoxLayout()

    # --- Live status list ---
    g_live = GroupPanel("Live Status")
    live_layout = QtWidgets.QVBoxLayout(g_live)
    live_layout.setSpacing(2)
    self.pneum_live_list = QtWidgets.QTreeWidget()
    self.pneum_live_list.setHeaderLabels(["Item", "Ztpneu", "Yrpneu", "Xrpneu", "Extra"])
    self.pneum_live_list.setRootIsDecorated(False)
    self.pneum_live_list.setAlternatingRowColors(True)
    self.pneum_live_list.setFixedHeight(180)
    self.pneum_live_list.setIndentation(0)
    self.pneum_live_list.setStyleSheet(
        "QTreeWidget { font-size:10px; }"
        "QTreeWidget::item { padding:1px 2px; }"
    )
    # Pre-populate status rows (matching C# GetStatusList1)
    self._pneu_status_items = _build_pneum_live_list_items(self.pneum_live_list)
    live_layout.addWidget(self.pneum_live_list)
    status_row.addWidget(g_live)

    # --- Move system + Use current offsets ---
    g_mv = GroupPanel("System Control")
    mv_layout = QtWidgets.QVBoxLayout(g_mv)
    mv_layout.setSpacing(4)

    # Move Up / Down buttons
    move_row = QtWidgets.QHBoxLayout()
    move_row.addWidget(QtWidgets.QLabel("Move:"))
    self.btn_pneu_move_up = FlatPush("Up")
    self.btn_pneu_move_down = FlatPush("Down")
    self.btn_pneu_move_up.setFixedSize(64, 36)
    self.btn_pneu_move_down.setFixedSize(64, 36)
    self.btn_pneu_move_up.clicked.connect(self._on_pneu_move_up)
    self.btn_pneu_move_down.clicked.connect(self._on_pneu_move_down)
    move_row.addWidget(self.btn_pneu_move_up)
    move_row.addWidget(self.btn_pneu_move_down)
    move_row.addStretch(1)
    mv_layout.addLayout(move_row)

    # Use current valve outputs as offsets (Up / Down)
    use_row = QtWidgets.QHBoxLayout()
    use_row.addWidget(QtWidgets.QLabel("Use current valve outputs as:"))
    self.btn_pneu_use_up = FlatPush("Up Offset")
    self.btn_pneu_use_down = FlatPush("Down Offset")
    self.btn_pneu_use_up.setFixedWidth(_MED_BTN_W)
    self.btn_pneu_use_down.setFixedWidth(_MED_BTN_W)
    self.btn_pneu_use_up.clicked.connect(self._on_pneu_use_up_offset)
    self.btn_pneu_use_down.clicked.connect(self._on_pneu_use_down_offset)
    use_row.addWidget(self.btn_pneu_use_up)
    use_row.addWidget(self.btn_pneu_use_down)
    use_row.addStretch(1)
    mv_layout.addLayout(use_row)

    # Individual loop status toggle buttons (3 axes x 4 fil = 12)
    toggle_row = QtWidgets.QHBoxLayout()
    toggle_row.addWidget(QtWidgets.QLabel("Individual loop status toggles:"))
    self.pneu_loop_toggle_btns: list[QtWidgets.QPushButton] = []
    for ax in range(3):
        for st in range(4):
            btn = FlatPush(f"A{ax}F{st}")
            btn.setCheckable(True)
            btn.setFixedSize(44, 28)
            btn.setStyleSheet(
                "QPushButton { background:#999; color:white; border:1px solid #777;"
                "  border-radius:3px; font-size:9px; font-weight:600; }"
                "QPushButton:checked { background:#6a0; color:white; }"
            )
            btn.clicked.connect(lambda checked, a=ax, s=st: self._on_pneu_loop_toggle(a, s))
            self.pneu_loop_toggle_btns.append(btn)
            toggle_row.addWidget(btn)
    toggle_row.addStretch(1)
    mv_layout.addLayout(toggle_row)

    status_row.addWidget(g_mv)

    root.addLayout(status_row)

    # ==================================================================
    # 7. Action buttons
    # ==================================================================
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read all filters", self._on_pneum_read_all),
        ("Read filter", self.on_pneum_filter_read),
        ("Write filter...", self.on_pneum_filter_write),
        ("Read steer matrix", self.on_pneum_steer_read),
        ("Write steer matrix...", self.on_pneum_steer_write),
        ("Read all", self._on_pneum_read_all),
        ("Write all...", self._on_pneum_write_all),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)

    root.addStretch(1)

    scroll.setWidget(w)
    self.main_tabs.addTab(scroll, "Pneumatic")


# ---------------------------------------------------------------------------
# Screenshot-oriented reference builder
# ---------------------------------------------------------------------------

def _build_pneumatic_tab_reference(self) -> None:
    """Build the two-column pneumatic tuning page from the supplied UI."""
    from python_samba.ui.extra_pages import PNEUM_AXIS_LABELS
    from python_samba.ui.main_window import SamTabWidget, SidebarLoopButton

    tabs = SamTabWidget()
    w = QtWidgets.QWidget()
    root = QtWidgets.QHBoxLayout(w)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(18)

    left_widget = QtWidgets.QWidget()
    left = QtWidgets.QVBoxLayout(left_widget)
    left.setContentsMargins(0, 0, 0, 0)
    left.setSpacing(3)
    left.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
    left_widget.setMinimumWidth(600)

    # Sensor matrix ---------------------------------------------------
    sensor = GroupPanel("")
    sensor.setMinimumWidth(600)
    sensor.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    sg = QtWidgets.QGridLayout(sensor)
    sg.setContentsMargins(16, 12, 16, 10)
    sg.setSpacing(2)
    for column, name in enumerate(_PNEU_AXES_NAMES, 1):
        sg.addWidget(QtWidgets.QLabel(name), 0, column, alignment=QtCore.Qt.AlignCenter)
    self.pneum_input_matrix = {}
    for row, label in enumerate(("Prox1", "Prox2", "Prox3", "Z4FB")):
        sg.addWidget(QtWidgets.QLabel(label), row + 1, 0)
        for axis in range(3):
            edit = SciEdit("0")
            edit.setFixedSize(160, 31)
            edit.editingFinished.connect(
                lambda _axis=axis: self._on_pneu_input_matrix_changed(_axis)
            )
            self.pneum_input_matrix[(row, axis)] = edit
            sg.addWidget(edit, row + 1, axis + 1)
    for row in range(4, 8):
        for axis in range(3):
            edit = SciEdit("0")
            edit.hide()
            self.pneum_input_matrix[(row, axis)] = edit
    self.pneum_sensor_expander = ClassicExpander(
        "Sensor Matrix", sensor, expanded=True
    )
    left.addWidget(self.pneum_sensor_expander)

    # Valve matrix ----------------------------------------------------
    valve = GroupPanel("")
    valve.setMinimumWidth(600)
    valve.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    vg = QtWidgets.QGridLayout(valve)
    vg.setContentsMargins(16, 12, 16, 10)
    vg.setSpacing(2)
    for column, name in enumerate(_PNEU_AXES_NAMES, 1):
        vg.addWidget(QtWidgets.QLabel(name), 0, column, alignment=QtCore.Qt.AlignCenter)
    self.pneum_output_matrix = {}
    for row, label in enumerate(("Valve1", "Valve2", "Valve3", "Valve4")):
        vg.addWidget(QtWidgets.QLabel(label), row + 1, 0)
        for axis in range(3):
            edit = SciEdit("0")
            edit.setFixedSize(160, 31)
            edit.editingFinished.connect(
                lambda _axis=axis: self._on_pneu_output_matrix_changed(_axis)
            )
            self.pneum_output_matrix[(row, axis)] = edit
            vg.addWidget(edit, row + 1, axis + 1)
    for row in range(4, 8):
        for axis in range(3):
            edit = SciEdit("0")
            edit.hide()
            self.pneum_output_matrix[(row, axis)] = edit
    self.pneum_valve_matrix_expander = ClassicExpander(
        "Valve Matrix", valve, expanded=True
    )
    left.addWidget(self.pneum_valve_matrix_expander)

    # Valve offsets ---------------------------------------------------
    offsets = GroupPanel("")
    offsets.setMinimumSize(600, 290)
    offsets.setMaximumHeight(290)
    offsets.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    og = QtWidgets.QGridLayout(offsets)
    og.setContentsMargins(30, 12, 30, 10)
    og.setSpacing(3)
    og.addWidget(QtWidgets.QLabel("Up"), 0, 1, alignment=QtCore.Qt.AlignCenter)
    og.addWidget(QtWidgets.QLabel("Down"), 0, 2, alignment=QtCore.Qt.AlignCenter)
    self.pneum_valve_up_offsets = []
    self.pneum_valve_down_offsets = []
    for row in range(8):
        up = SciEdit("0")
        down = SciEdit("0")
        up.editingFinished.connect(
            lambda _row=row: self._on_pneu_valve_offset_changed(_row, "up")
        )
        down.editingFinished.connect(
            lambda _row=row: self._on_pneu_valve_offset_changed(_row, "down")
        )
        self.pneum_valve_up_offsets.append(up)
        self.pneum_valve_down_offsets.append(down)
        if row < 4:
            og.addWidget(QtWidgets.QLabel(f"Valve{row + 1}"), row + 1, 0)
            up.setFixedSize(220, 38)
            down.setFixedSize(220, 38)
            og.addWidget(up, row + 1, 1)
            og.addWidget(down, row + 1, 2)
        else:
            up.hide()
            down.hide()
    self.pneu_valve_up = self.pneum_valve_up_offsets
    self.pneu_valve_down = self.pneum_valve_down_offsets
    use_up = FlatPush("⇧")
    use_down = FlatPush("⇩")
    use_up.clicked.connect(self._on_pneu_use_up_offset)
    use_down.clicked.connect(self._on_pneu_use_down_offset)
    og.addWidget(use_up, 5, 1, alignment=QtCore.Qt.AlignCenter)
    og.addWidget(use_down, 5, 2, alignment=QtCore.Qt.AlignCenter)
    self.pneum_valve_offsets_expander = ClassicExpander(
        "Valve Offsets", offsets, expanded=True
    )
    left.addWidget(self.pneum_valve_offsets_expander)

    # Isolator offsets / dither --------------------------------------
    iso_dither = GroupPanel("")
    iso_dither.setMinimumWidth(600)
    iso_dither.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
    )
    idg = QtWidgets.QGridLayout(iso_dither)
    idg.setContentsMargins(16, 14, 16, 12)
    idg.setHorizontalSpacing(8)
    idg.setVerticalSpacing(5)
    idg.addWidget(QtWidgets.QLabel("Isolator Offsets"), 0, 0, 1, 2, QtCore.Qt.AlignCenter)
    idg.addWidget(QtWidgets.QLabel("Dithering Config"), 0, 2, 1, 2, QtCore.Qt.AlignCenter)
    self.pneum_iso_offsets = []
    for row in range(3):
        edit = SciEdit("0")
        edit.setFixedWidth(185)
        edit.editingFinished.connect(lambda _row=row: self._on_pneu_iso_offset_changed(_row))
        self.pneum_iso_offsets.append(edit)
        idg.addWidget(QtWidgets.QLabel(f"Iso{row + 1}"), row + 1, 0)
        idg.addWidget(edit, row + 1, 1)
    self.pneum_dither_amount = SciEdit("0")
    self.pneum_dither_freq = SciEdit("0")
    self.pneum_dither_alpha = SciEdit("1")
    for row, (label, edit) in enumerate((
        ("Amount", self.pneum_dither_amount),
        ("Frequency", self.pneum_dither_freq),
        ("Alpha", self.pneum_dither_alpha),
    ), 1):
        edit.setFixedWidth(185)
        idg.addWidget(QtWidgets.QLabel(label), row, 2)
        idg.addWidget(edit, row, 3)
    self.pneum_dither_amount.editingFinished.connect(self._on_pneu_dither_amount_changed)
    self.pneum_dither_freq.editingFinished.connect(self._on_pneu_dither_freq_changed)
    self.pneum_dither_alpha.editingFinished.connect(self._on_pneu_dither_alpha_changed)
    self.pneum_iso_dither_expander = ClassicExpander(
        "Isolator Offsets/Dithering Config", iso_dither, expanded=True
    )
    left.addWidget(self.pneum_iso_dither_expander)

    # The reference page starts with ramp settings collapsed, but the original
    # controls and write handlers remain fully available after expansion.
    ramp = GroupPanel("")
    ramp.setMinimumSize(600, 235)
    ramp.setMaximumHeight(235)
    ramp.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    ramp_grid = QtWidgets.QGridLayout(ramp)
    ramp_grid.setContentsMargins(24, 12, 24, 12)
    ramp_grid.setHorizontalSpacing(12)
    ramp_grid.setVerticalSpacing(5)
    _build_pneum_ramp_params(self, ramp_grid)
    for edit in (
        self.pneum_ramp_setpoint_grad,
        self.pneum_ramp_move_up_grad,
        self.pneum_ramp_move_down_grad,
        self.pneum_ramp_valve_offset_grad,
        self.pneum_ramp_rms_hysteresis,
    ):
        edit.setFixedWidth(260)
    self.pneum_ramp_expander = ClassicExpander(
        "Pneumatic Ramp Setting", ramp, expanded=False
    )
    left.addWidget(self.pneum_ramp_expander)
    for expander in (
        self.pneum_sensor_expander,
        self.pneum_valve_matrix_expander,
        self.pneum_valve_offsets_expander,
        self.pneum_iso_dither_expander,
        self.pneum_ramp_expander,
    ):
        expander.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
    left.addStretch(1)
    left_scroll = QtWidgets.QScrollArea()
    left_scroll.setObjectName("pneumaticSettingsScroll")
    left_scroll.setWidgetResizable(True)
    left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    left_scroll.setStyleSheet(
        "QScrollArea, QScrollArea > QWidget > QWidget {"
        " background:transparent; border:none; }"
    )
    left_scroll.viewport().setStyleSheet("background:transparent;")
    left_widget.setStyleSheet("background:transparent;")
    left_scroll.setWidget(left_widget)
    left_scroll.setMinimumWidth(615)
    left_scroll.setMaximumWidth(680)
    root.addWidget(left_scroll)

    # Right-hand area -------------------------------------------------
    right = QtWidgets.QGridLayout()
    right.setHorizontalSpacing(10)
    right.setVerticalSpacing(6)

    filters = GroupPanel("Pneumatic Filters")
    filters.setFixedSize(550, 325)
    fg = QtWidgets.QGridLayout(filters)
    fg.setContentsMargins(12, 18, 12, 12)
    for stage in range(4):
        fg.addWidget(QtWidgets.QLabel(f"Fil{stage + 1}"), 0, stage + 1, alignment=QtCore.Qt.AlignCenter)
    fg.addWidget(
        QtWidgets.QLabel("Loop Status"),
        0,
        5,
        alignment=QtCore.Qt.AlignCenter,
    )
    self.pneum_filter_buttons = {}
    self.pneum_individual_loop_leds = []
    for axis, name in enumerate(_PNEU_AXES_NAMES):
        fg.addWidget(QtWidgets.QLabel(name), axis + 1, 0)
        for stage in range(4):
            cell = FilterStageCell(stage, "----", width=92, height=78)
            cell.clicked.connect(
                lambda _stage=stage, _axis=axis: self._on_pneum_filter_cell_clicked(
                    _axis, _stage
                )
            )
            self.pneum_filter_buttons[(axis, stage)] = cell
            fg.addWidget(cell, axis + 1, stage + 1)
        lamp = SidebarLoopButton()
        lamp.set_on(False)
        lamp.setFixedSize(58, 58)
        lamp.setToolTip(f"Toggle pneumatic individual loop {name}")
        lamp.clicked.connect(
            lambda _checked=False, _axis=axis: self._on_pneu_individual_loop_clicked(
                _axis
            )
        )
        self.pneum_individual_loop_leds.append(lamp)
        fg.addWidget(lamp, axis + 1, 5)
    right.addWidget(filters, 0, 0, alignment=QtCore.Qt.AlignTop)

    loop = GroupPanel("")
    loop.setFixedSize(305, 320)
    lf = QtWidgets.QFormLayout(loop)
    self.pneum_loop_leds = {}
    for label, key in (
        ("Loop Status", "pneu"),
        ("Setpoint Status", "use_setpoint_all"),
        ("Move Up at Startup", "move_up_startup"),
        ("Dither Comp.", "dither_comp"),
        ("Reset Inclin. Offset", "ref_metrology"),
    ):
        lamp = SidebarLoopButton()
        lamp.setToolTip(f"Toggle {label}")
        lamp.clicked.connect(
            lambda _checked=False, _key=key: self._on_pneu_status_led_clicked(
                _key
            )
        )
        self.pneum_loop_leds[key] = lamp
        lf.addRow(label, lamp)
    right.addWidget(loop, 0, 1, alignment=QtCore.Qt.AlignTop)

    flotation = GroupPanel("Floatation Config")
    flotation.setFixedSize(350, 180)
    ff = QtWidgets.QFormLayout(flotation)
    self.pneum_float_softup = SciEdit("0")
    self.pneum_float_setpoint = SciEdit("0")
    self.pneum_float_mode_tol = SciEdit("0")
    ff.addRow("Soft Up Height", self.pneum_float_softup)
    ff.addRow("Set Point", self.pneum_float_setpoint)
    ff.addRow("Mode tolerance", self.pneum_float_mode_tol)
    self.pneum_float_softup.editingFinished.connect(self._on_pneu_float_softup_changed)
    self.pneum_float_setpoint.editingFinished.connect(self._on_pneu_float_setpoint_changed)
    self.pneum_float_mode_tol.editingFinished.connect(self._on_pneu_float_mode_tol_changed)
    right.addWidget(flotation, 1, 0, alignment=QtCore.Qt.AlignLeft)

    move = GroupPanel("Move System")
    move.setFixedSize(305, 125)
    move_row = QtWidgets.QHBoxLayout(move)
    self.btn_pneu_move_up = FlatPush("⇧  UP")
    self.btn_pneu_move_down = FlatPush("⇩  DOWN")
    self.btn_pneu_move_up.clicked.connect(self._on_pneu_move_up)
    self.btn_pneu_move_down.clicked.connect(self._on_pneu_move_down)
    move_row.addWidget(self.btn_pneu_move_up)
    move_row.addWidget(self.btn_pneu_move_down)
    right.addWidget(move, 1, 1, alignment=QtCore.Qt.AlignTop)

    live = QtWidgets.QTreeWidget()
    live.setHeaderLabels(["Status", "Ztrans", "Yrot", "Xrot", "RefPoint"])
    live.setRootIsDecorated(False)
    live.setIndentation(0)
    live.setFixedSize(875, 305)
    live.setStyleSheet(
        "QTreeWidget { background:#fffda0; border:2px solid #888; }"
        "QHeaderView::section { background:#fffda0; }"
    )
    self.pneum_live_list = live
    self._pneu_status_items = _build_pneum_live_list_items(live)
    right.addWidget(live, 2, 0, 1, 2, alignment=QtCore.Qt.AlignTop)
    right.setRowStretch(3, 1)
    root.addLayout(right, 1)

    # Hidden compatibility controls used by the existing read/write paths.
    self.pneum_axis_combo = QtWidgets.QComboBox()
    self.pneum_axis_combo.addItems(_PNEU_AXES_NAMES)
    self.pneum_axis_combo.hide()
    self.pneum_filter = FilterEditor(PNEUM_AXIS_LABELS, max_stage=3)
    self.pneum_filter.hide()
    self.pneum_filter_panel = ClassicFilterPanel("Pneumatic filter")
    self.pneum_filter_panel.read_clicked.connect(self.on_pneum_filter_read)
    self.pneum_filter_panel.write_clicked.connect(self.on_pneum_filter_write)
    self.pneum_filter_panel.hide()
    self.pneum_use_setpoint_all = RockerButton("On", "Off")
    self.pneum_use_setpoint_all.hide()
    self.btn_pneu_use_up = FlatPush()
    self.btn_pneu_use_down = FlatPush()
    self.btn_pneu_use_up.hide()
    self.btn_pneu_use_down.hide()
    self.pneu_loop_toggle_btns = []
    for _ in range(12):
        button = QtWidgets.QPushButton()
        button.setCheckable(True)
        button.hide()
        self.pneu_loop_toggle_btns.append(button)

    tabs.addTab(w, "Tuning")
    self.main_tabs.addTab(tabs, "Pneumatic")


# ---------------------------------------------------------------------------
# Sub-builders for each section
# ---------------------------------------------------------------------------


def _build_pneum_input_matrix(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the input steering matrix: 8 inputs x 3 axes."""
    INPUT_LABELS = [
        "Inp10(Prox1)", "Inp11(Prox2)", "Inp12(Prox3)", "Inp24(Prox4)",
        "Inp13(PosX)", "Inp14(PosY)", "Inp15(PosZ)", "Inp38(Aux)",
    ]
    # Column headers — axis names
    for j, name in enumerate(_PNEU_AXES_NAMES):
        lbl = QtWidgets.QLabel(name)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        grid.addWidget(lbl, 0, j + 1)

    self.pneum_input_matrix: dict[tuple[int, int], SciEdit] = {}
    for i, inp_name in enumerate(INPUT_LABELS):
        lbl = QtWidgets.QLabel(inp_name)
        lbl.setStyleSheet("font-weight:500; color:#303030; font-size:10px;")
        grid.addWidget(lbl, i + 1, 0)
        for j in range(3):
            ed = SciEdit("0.00000e+000")
            ed.setFixedWidth(_SCEDIT_WIDTH)
            ed.setAlignment(QtCore.Qt.AlignCenter)
            ed.editingFinished.connect(
                lambda a=j, i=i: self._on_pneu_input_matrix_changed(a)
            )
            self.pneum_input_matrix[(i, j)] = ed
            grid.addWidget(ed, i + 1, j + 1)


def _build_pneum_output_matrix(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the output steering matrix: 8 valves x 3 axes."""
    OUTPUT_LABELS = [
        "Valve1(DAC12)", "Valve2(DAC13)", "Valve3(DAC14)", "Valve4(DAC15)",
        "Valve5(DAC16)", "Valve6(DAC17)", "Valve7(DAC6)", "Valve8(DAC10)",
    ]
    # Column headers — axis names
    for j, name in enumerate(_PNEU_AXES_NAMES):
        lbl = QtWidgets.QLabel(name)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        grid.addWidget(lbl, 0, j + 1)

    self.pneum_output_matrix: dict[tuple[int, int], SciEdit] = {}
    for i, out_name in enumerate(OUTPUT_LABELS):
        lbl = QtWidgets.QLabel(out_name)
        lbl.setStyleSheet("font-weight:500; color:#303030; font-size:10px;")
        grid.addWidget(lbl, i + 1, 0)
        for j in range(3):
            ed = SciEdit("0.00000e+000")
            ed.setFixedWidth(_SCEDIT_WIDTH)
            ed.setAlignment(QtCore.Qt.AlignCenter)
            ed.editingFinished.connect(
                lambda a=j, i=i: self._on_pneu_output_matrix_changed(a)
            )
            self.pneum_output_matrix[(i, j)] = ed
            grid.addWidget(ed, i + 1, j + 1)


def _build_pneum_valve_offsets(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the valve up/down offsets: 8 valves each."""
    VALVE_LABELS = ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8"]

    # Header row
    grid.addWidget(QtWidgets.QLabel("Valve"), 0, 0)
    up_hdr = QtWidgets.QLabel("Up Offset")
    up_hdr.setAlignment(QtCore.Qt.AlignCenter)
    up_hdr.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
    grid.addWidget(up_hdr, 0, 1)
    dn_hdr = QtWidgets.QLabel("Down Offset")
    dn_hdr.setAlignment(QtCore.Qt.AlignCenter)
    dn_hdr.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
    grid.addWidget(dn_hdr, 0, 2)

    self.pneum_valve_up_offsets: list[SciEdit] = []
    self.pneum_valve_down_offsets: list[SciEdit] = []
    for i, vname in enumerate(VALVE_LABELS):
        lbl = QtWidgets.QLabel(vname)
        lbl.setStyleSheet("font-weight:500; color:#303030; font-size:10px;")
        grid.addWidget(lbl, i + 1, 0)

        ed_up = SciEdit("0.00000e+000")
        ed_up.setFixedWidth(_SCEDIT_WIDTH)
        ed_up.setAlignment(QtCore.Qt.AlignCenter)
        ed_up.editingFinished.connect(lambda i=i: self._on_pneu_valve_offset_changed(i, "up"))
        self.pneum_valve_up_offsets.append(ed_up)
        grid.addWidget(ed_up, i + 1, 1)

        ed_dn = SciEdit("0.00000e+000")
        ed_dn.setFixedWidth(_SCEDIT_WIDTH)
        ed_dn.setAlignment(QtCore.Qt.AlignCenter)
        ed_dn.editingFinished.connect(lambda i=i: self._on_pneu_valve_offset_changed(i, "down"))
        self.pneum_valve_down_offsets.append(ed_dn)
        grid.addWidget(ed_dn, i + 1, 2)


def _build_pneum_iso_offsets(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the ISO / motor offsets: motor[8], motor[9], motor[10]."""
    ISO_LABELS = ["Motor[8] (Iso1)", "Motor[9] (Iso2)", "Motor[10] (Iso3)"]
    self.pneum_iso_offsets: list[SciEdit] = []
    for i, name in enumerate(ISO_LABELS):
        lbl = QtWidgets.QLabel(name)
        lbl.setStyleSheet("font-weight:500; color:#303030; font-size:10px;")
        grid.addWidget(lbl, i, 0)
        ed = SciEdit("0.00000e+000")
        ed.setFixedWidth(_SCEDIT_WIDTH)
        ed.setAlignment(QtCore.Qt.AlignCenter)
        ed.editingFinished.connect(lambda i=i: self._on_pneu_iso_offset_changed(i))
        self.pneum_iso_offsets.append(ed)
        grid.addWidget(ed, i, 1)


def _build_pneum_dither(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the dither settings: amount, frequency, comp alpha."""
    labels = ["Amount:", "Freq (Hz):", "Comp Alpha:"]
    self.pneum_dither_amount = SciEdit("0.00000e+000")
    self.pneum_dither_amount.setFixedWidth(_SCEDIT_WIDTH)
    self.pneum_dither_freq = SciEdit("0.00000e+000")
    self.pneum_dither_freq.setFixedWidth(_SCEDIT_WIDTH)
    self.pneum_dither_alpha = SciEdit("0.00000e+000")
    self.pneum_dither_alpha.setFixedWidth(_SCEDIT_WIDTH)

    self.pneum_dither_amount.editingFinished.connect(self._on_pneu_dither_amount_changed)
    self.pneum_dither_freq.editingFinished.connect(self._on_pneu_dither_freq_changed)
    self.pneum_dither_alpha.editingFinished.connect(self._on_pneu_dither_alpha_changed)

    for i, (lbl, ed) in enumerate(zip(
        labels,
        [self.pneum_dither_amount, self.pneum_dither_freq, self.pneum_dither_alpha],
    )):
        grid.addWidget(QtWidgets.QLabel(lbl), i, 0)
        grid.addWidget(ed, i, 1)


def _build_pneum_floatation(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the floatation settings: setpoint, soft-up height, mode tolerance."""
    labels = ["Setpoint:", "Soft-Up Height:", "Mode Tolerance:"]
    self.pneum_float_setpoint = SciEdit("0.00000e+000")
    self.pneum_float_setpoint.setFixedWidth(_SCEDIT_WIDTH)
    self.pneum_float_softup = SciEdit("0.00000e+000")
    self.pneum_float_softup.setFixedWidth(_SCEDIT_WIDTH)
    self.pneum_float_mode_tol = SciEdit("0.00000e+000")
    self.pneum_float_mode_tol.setFixedWidth(_SCEDIT_WIDTH)

    self.pneum_float_setpoint.editingFinished.connect(self._on_pneu_float_setpoint_changed)
    self.pneum_float_softup.editingFinished.connect(self._on_pneu_float_softup_changed)
    self.pneum_float_mode_tol.editingFinished.connect(self._on_pneu_float_mode_tol_changed)

    for i, (lbl, ed) in enumerate(zip(
        labels,
        [self.pneum_float_setpoint, self.pneum_float_softup, self.pneum_float_mode_tol],
    )):
        grid.addWidget(QtWidgets.QLabel(lbl), i, 0)
        grid.addWidget(ed, i, 1)

    # Use setpoint for all axes — rocker button (like C# LEDBtn bound to PneuUseSetpointForAllAxes)
    use_row = QtWidgets.QHBoxLayout()
    use_row.addWidget(QtWidgets.QLabel("Use setpoint for all axes:"))
    self.pneum_use_setpoint_all = RockerButton("On", "Off")
    self.pneum_use_setpoint_all.toggled_text.connect(self._on_pneu_use_setpoint_all_toggled)
    use_row.addWidget(self.pneum_use_setpoint_all)
    use_row.addStretch(1)
    grid.addLayout(use_row, 4, 0, 1, 2)


def _build_pneum_ramp_params(self, grid: QtWidgets.QGridLayout) -> None:
    """Build the ramp parameters: setpoint gradient, move up/down gradient, etc."""
    labels = [
        "Setpoint Gradient:",
        "Move Up Gradient:",
        "Move Down Gradient:",
        "Valve Offset Gradient:",
        "RMS Hysteresis Factor:",
    ]
    self.pneum_ramp_setpoint_grad = SciEdit("0.00000e+000")
    self.pneum_ramp_move_up_grad = SciEdit("0.00000e+000")
    self.pneum_ramp_move_down_grad = SciEdit("0.00000e+000")
    self.pneum_ramp_valve_offset_grad = SciEdit("0.00000e+000")
    self.pneum_ramp_rms_hysteresis = SciEdit("0.00000e+000")

    self.pneum_ramp_setpoint_grad.editingFinished.connect(self._on_pneu_ramp_setpoint_grad_changed)
    self.pneum_ramp_move_up_grad.editingFinished.connect(self._on_pneu_ramp_move_up_grad_changed)
    self.pneum_ramp_move_down_grad.editingFinished.connect(self._on_pneu_ramp_move_down_grad_changed)
    self.pneum_ramp_valve_offset_grad.editingFinished.connect(self._on_pneu_ramp_valve_offset_grad_changed)
    self.pneum_ramp_rms_hysteresis.editingFinished.connect(self._on_pneu_ramp_rms_hysteresis_changed)

    for i, (lbl, ed) in enumerate(zip(
        labels,
        [
            self.pneum_ramp_setpoint_grad,
            self.pneum_ramp_move_up_grad,
            self.pneum_ramp_move_down_grad,
            self.pneum_ramp_valve_offset_grad,
            self.pneum_ramp_rms_hysteresis,
        ],
    )):
        grid.addWidget(QtWidgets.QLabel(lbl), i, 0)
        ed.setFixedWidth(_SCEDIT_WIDTH)
        ed.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(ed, i, 1)


def _build_pneum_loop_status(self, row: QtWidgets.QHBoxLayout) -> None:
    """Build the loop status LED indicators."""
    # LED + label pairs (matching C# LEDBtn for each status bit)
    loop_items = [
        ("Pneu", "pneu"),
        ("DitherComp", "dither_comp"),
        ("RefMetrology", "ref_metrology"),
        ("MoveUpAtStartup", "move_up_startup"),
        ("UseSetpointForAll", "use_setpoint_all"),
    ]
    self.pneum_loop_leds: dict[str, LedIndicator] = {}
    for display_name, key in loop_items:
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        led = LedIndicator(14)
        self.pneum_loop_leds[key] = led
        col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
        lbl = QtWidgets.QLabel(display_name)
        lbl.setStyleSheet("font-size:9px; font-weight:600; color:#303030;")
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        col.addWidget(lbl, 0, QtCore.Qt.AlignHCenter)
        row.addLayout(col)

    row.addStretch(1)


def _build_pneum_live_list_items(tree: QtWidgets.QTreeWidget) -> dict[str, list[QtWidgets.QTreeWidgetItem]]:
    """Pre-populate the live status list items (matching C# GetStatusList1)."""
    items: dict[str, list[QtWidgets.QTreeWidgetItem]] = {}

    def add_row(key: str, col0: str, col1: str = "", col2: str = "", col3: str = "", col4: str = "") -> QtWidgets.QTreeWidgetItem:
        it = QtWidgets.QTreeWidgetItem([col0, col1, col2, col3, col4])
        tree.addTopLevelItem(it)
        items[key] = it
        return it

    add_row("Status", "Status", "", "", "RefPoint", "")
    add_row("AxesLabel", "Axis", "Ztpneu", "Yrpneu", "Xrpneu", "")
    add_row("AxesInput", "Input", "", "", "", "")
    add_row("AxesOutput", "Output", "", "", "", "")
    add_row("ValveSet1", "Valve1-4", "", "", "", "")
    add_row("ValveSet2", "Valve5-8", "", "", "", "").setHidden(True)
    add_row("HeightSet1", "Height1-4", "", "", "", "")
    add_row("HeightSet2", "Height5-8", "", "", "", "").setHidden(True)
    add_row("PosError", "Pos. Error", "", "", "", "").setHidden(True)
    add_row("TimerStatus", "OK Time", "", "", "NOK Time", "")
    add_row("Cascaded", "", "", "", "", "").setHidden(True)
    return items


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _on_pneu_move_up(self) -> None:
    """Move pneumatic system up."""
    def work() -> None:
        s = self._require_session()
        if not self._confirm_write("Move pneumatic system UP?"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.move_pneumatic_system_up()
        finally:
            self._set_writable(True)
        self.log_msg("Pneumatic system moved UP")
    self._run("Move pneumatic up", work)


def on_pneum_filter_read(self) -> None:
    """Read the axis/stage selected by the reference filter matrix."""
    def work() -> None:
        s = self._require_session()
        axis = self.pneum_filter.axis_index()
        stage = self.pneum_filter.stage_index()
        value = s.get_pneumatic_filter(axis, stage)
        self.pneum_filter.set_stage(value)
        self.pneum_filter_panel.set_from_filter_editor(self.pneum_filter)
        self._update_pneum_cell_text(axis, stage)
        self.log_msg(f"Pneumatic filter read axis={axis} stage={stage}")
    self._run("Read pneumatic filter", work)


def on_pneum_filter_write(self) -> None:
    """Write the selected pneumatic filter stage."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        value = self.pneum_filter.to_stage()
        if not self._confirm_write(
            f"Pneumatic filter axis={value.axis} stage={value.stage}"
        ):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.set_pneumatic_filter(value)
        finally:
            self._set_writable(True)
        self._update_pneum_cell_text(value.axis, value.stage)
        self.log_msg(f"Pneumatic filter written axis={value.axis} stage={value.stage}")
    self._run("Write pneumatic filter", work)


def on_pneum_status(self) -> None:
    _on_pneu_read_all(self)


def on_pneum_steer_read(self) -> None:
    _on_pneu_read_all(self)


def on_pneum_steer_write(self) -> None:
    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write("Pneumatic input/output steering matrices"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            for axis in range(3):
                inputs = [
                    float(self.pneum_input_matrix[(row, axis)].text())
                    for row in range(8)
                ]
                outputs = [
                    float(self.pneum_output_matrix[(row, axis)].text())
                    for row in range(8)
                ]
                s.set_pneumatic_input_steering_matrix(axis, inputs)
                s.set_pneumatic_output_steering_matrix(axis, outputs)
        finally:
            self._set_writable(True)
        self.log_msg("Pneumatic steering matrices written")
    self._run("Write pneumatic steering matrices", work)


def on_float_read(self) -> None:
    def work() -> None:
        cfg = self._require_session().get_pneumatic_config_parameters()
        if len(cfg) >= 3:
            self.pneum_float_softup.setText(str(cfg[0]))
            self.pneum_float_setpoint.setText(str(cfg[1]))
            self.pneum_float_mode_tol.setText(str(cfg[2]))
    self._run("Read floatation config", work)


def on_float_pauco(self) -> None:
    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_destructive("Use current pressure values as offsets"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.use_current_pressure_offsets()
        finally:
            self._set_writable(True)
    self._run("Use current pressure offsets", work)


def on_dither_read(self) -> None:
    def work() -> None:
        s = self._require_session()
        self.pneum_dither_amount.setText(f"{s.get_dither_value():g}")
        self.pneum_dither_freq.setText(f"{s.get_dither_frequency():g}")
        self.pneum_dither_alpha.setText(f"{s.get_dither_alpha():g}")
    self._run("Read pneumatic dither", work)


def _on_pneu_move_down(self) -> None:
    """Move pneumatic system down."""
    def work() -> None:
        s = self._require_session()
        if not self._confirm_write("Move pneumatic system DOWN?"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.move_pneumatic_system_down()
        finally:
            self._set_writable(True)
        self.log_msg("Pneumatic system moved DOWN")
    self._run("Move pneumatic down", work)


def _on_pneu_use_up_offset(self) -> None:
    """Use current valve outputs as Up offsets."""
    def work() -> None:
        s = self._require_session()
        if not self._confirm_destructive("Use current pressure setpoints as Up offsets?"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.use_current_pressure_setpoints_as_up_offset()
        finally:
            self._set_writable(True)
        self.log_msg("Up offsets updated from current valve outputs")
    self._run("Use Up offsets", work)


def _on_pneu_use_down_offset(self) -> None:
    """Use current valve outputs as Down offsets."""
    def work() -> None:
        s = self._require_session()
        if not self._confirm_destructive("Use current pressure setpoints as Down offsets?"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            s.use_current_pressure_setpoints_as_down_offset()
        finally:
            self._set_writable(True)
        self.log_msg("Down offsets updated from current valve outputs")
    self._run("Use Down offsets", work)


def _on_pneu_loop_toggle(self, axis: int, stage: int) -> None:
    """Compatibility route for the old matrix callback.

    Pneumatic individual-loop status has one bit per axis, not one bit per
    filter stage.  The former implementation accidentally sent the stage as
    a boolean state and used BGSTS/BSSTS instead of BGSST/BSSST.
    """
    _on_pneu_individual_loop_clicked(self, axis)


def _set_pneum_individual_loop_buttons(self, pneumatic: int) -> None:
    """Apply the BGSST pneumatic word to visible pneumatic loop buttons."""
    for axis, lamp in enumerate(
        getattr(self, "pneum_individual_loop_leds", ())
    ):
        lamp.set_on(bool(int(pneumatic) & (1 << axis)))
    pff_setter = getattr(self, "_set_pff_individual_loop_buttons", None)
    if callable(pff_setter):
        pff_setter(pneumatic)


def _on_pneu_individual_loop_clicked(self, axis: int) -> None:
    """Toggle one pneumatic individual-loop bit through BGSST/BSSST."""
    if not 0 <= axis < 3:
        raise ValueError(f"pneumatic individual-loop axis out of range: {axis}")
    self._on_axis_individual_loop_clicked("pneumatic", axis)


def _on_pneu_status_led_clicked(self, key: str) -> None:
    """Toggle one of the five legacy pneumatic Loop Status controls."""
    system_bits = {
        "pneu": 0x00040,
        "move_up_startup": 0x00008,
        "dither_comp": 0x02000,
        "ref_metrology": 0x20000,
    }
    if key not in {*system_bits, "use_setpoint_all"}:
        raise ValueError(f"unknown pneumatic status control {key!r}")
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        session = self._require_session()
        if not self._confirm_write(f"Toggle pneumatic status {key}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            if key == "use_setpoint_all":
                use_all = 0 if session.get_pneumatic_setpoint_status() else 1
                session.set_pneumatic_setpoint_status(use_all)
                self.pneum_loop_leds[key].set_on(bool(use_all))
            else:
                loop = session.get_loop_status()
                system = loop.system ^ system_bits[key]
                session.set_loop_status(loop.individual, system)
                self.pneum_loop_leds[key].set_on(
                    bool(system & system_bits[key])
                )
                self._refresh_status_loop_state()
        finally:
            self._set_writable(True)

    self._run("Toggle pneumatic status", work)


def _on_pneu_input_matrix_changed(self, axis: int) -> None:
    """User edited a cell in the input steering matrix."""
    def work() -> None:
        s = self._require_session()
        # PGPSM/PSPSM transfer one axis at a time.  The old implementation
        # accidentally built an 8x3 nested list, which the command encoder
        # cannot serialize.
        vals = [
            float(self.pneum_input_matrix[(row, axis)].text())
            for row in range(8)
        ]
        s.set_pneumatic_input_steering_matrix(axis, vals)
        self.log_msg(f"Input steering matrix written for axis {axis}")
    self._run("Input steer matrix write", work)


def _on_pneu_output_matrix_changed(self, axis: int) -> None:
    """User edited a cell in the output steering matrix."""
    def work() -> None:
        s = self._require_session()
        vals = [
            float(self.pneum_output_matrix[(row, axis)].text())
            for row in range(8)
        ]
        s.set_pneumatic_output_steering_matrix(axis, vals)
        self.log_msg(f"Output steering matrix written for axis {axis}")
    self._run("Output steer matrix write", work)


def _on_pneu_valve_offset_changed(self, idx: int, direction: str) -> None:
    """User edited a valve offset (up or down)."""
    def work() -> None:
        s = self._require_session()
        if direction == "up":
            vals = [float(ed.text()) for ed in self.pneum_valve_up_offsets]
            s.set_pneumatic_valve_up_offsets(vals)
            self.log_msg(f"Valve up offsets written (idx={idx})")
        else:
            vals = [float(ed.text()) for ed in self.pneum_valve_down_offsets]
            s.set_pneumatic_valve_down_offsets(vals)
            self.log_msg(f"Valve down offsets written (idx={idx})")
    self._run(f"Valve {direction} offset write", work)


def _on_pneu_iso_offset_changed(self, idx: int) -> None:
    """User edited an ISO offset."""
    def work() -> None:
        s = self._require_session()
        vals = [float(ed.text()) for ed in self.pneum_iso_offsets]
        s.set_motor_and_iso_offset_values(vals)
        self.log_msg(f"ISO offsets written (idx={idx})")
    self._run("ISO offset write", work)


def _on_pneu_dither_amount_changed(self) -> None:
    """User edited the dither amount."""
    def work() -> None:
        s = self._require_session()
        val = float(self.pneum_dither_amount.text())
        s.set_pneumatic_dither_value(val)
        self.log_msg(f"Dither amount set to {val}")
    self._run("Dither amount write", work)


def _on_pneu_dither_freq_changed(self) -> None:
    """User edited the dither frequency."""
    def work() -> None:
        s = self._require_session()
        val = _pneumatic_config_int(self.pneum_dither_freq.text())
        s.set_dither_frequency(val)
        self.log_msg(f"Dither frequency set to {val}")
    self._run("Dither frequency write", work)


def _on_pneu_dither_alpha_changed(self) -> None:
    """User edited the dither compensation alpha."""
    def work() -> None:
        s = self._require_session()
        val = float(self.pneum_dither_alpha.text())
        s.set_pneumatic_dither_compensation_alpha(val)
        self.log_msg(f"Dither comp alpha set to {val}")
    self._run("Dither alpha write", work)


def _on_pneu_float_setpoint_changed(self) -> None:
    """User edited the floatation setpoint."""
    def work() -> None:
        s = self._require_session()
        val = _pneumatic_config_int(self.pneum_float_setpoint.text())
        s.set_pneumatic_config_setpoint(val)
        self.log_msg(f"Float setpoint set to {val}")
    self._run("Float setpoint write", work)


def _on_pneu_float_softup_changed(self) -> None:
    """User edited the soft-up height."""
    def work() -> None:
        s = self._require_session()
        val = _pneumatic_config_int(self.pneum_float_softup.text())
        s.set_pneumatic_config_softup_height(val)
        self.log_msg(f"Soft-up height set to {val}")
    self._run("Soft-up height write", work)


def _on_pneu_float_mode_tol_changed(self) -> None:
    """User edited the mode tolerance."""
    def work() -> None:
        s = self._require_session()
        val = _pneumatic_config_int(self.pneum_float_mode_tol.text())
        s.set_pneumatic_position_tolerance(val)
        self.log_msg(f"Mode tolerance set to {val}")
    self._run("Mode tolerance write", work)


def _on_pneu_use_setpoint_all_toggled(self, checked: bool) -> None:
    """User toggled 'Use setpoint for all axes'."""
    def work() -> None:
        s = self._require_session()
        s.set_use_pneum_axis_setpoint_for_all_axes(int(checked))
        self.log_msg(f"Use setpoint for all axes: {checked}")
    self._run("Setpoint for all axes", work)


def _on_pneu_ramp_setpoint_grad_changed(self) -> None:
    """User edited the setpoint gradient ramp parameter."""
    _on_pneu_ramp_param_changed(self, "setpoint_gradient", float(self.pneum_ramp_setpoint_grad.text()))


def _on_pneu_ramp_move_up_grad_changed(self) -> None:
    """User edited the move-up gradient ramp parameter."""
    _on_pneu_ramp_param_changed(self, "move_up_gradient", float(self.pneum_ramp_move_up_grad.text()))


def _on_pneu_ramp_move_down_grad_changed(self) -> None:
    """User edited the move-down gradient ramp parameter."""
    _on_pneu_ramp_param_changed(self, "move_down_gradient", float(self.pneum_ramp_move_down_grad.text()))


def _on_pneu_ramp_valve_offset_grad_changed(self) -> None:
    """User edited the valve offset gradient ramp parameter."""
    _on_pneu_ramp_param_changed(self, "valve_offset_gradient", float(self.pneum_ramp_valve_offset_grad.text()))


def _on_pneu_ramp_rms_hysteresis_changed(self) -> None:
    """User edited the RMS hysteresis factor ramp parameter."""
    _on_pneu_ramp_param_changed(
        self,
        "rms_hysteresis_factor",
        _pneumatic_config_int(self.pneum_ramp_rms_hysteresis.text()),
    )


def _on_pneu_ramp_param_changed(self, param_name: str, value: float) -> None:
    """Generic ramp parameter change handler."""
    self._ensure_controller_capabilities()
    if not self._supports_controller_feature("pneumatic_ramp"):
        self.log_msg("Pneumatic ramp is not supported by this controller")
        return

    def work() -> None:
        s = self._require_session()
        s.set_pneumatic_ramp_parameter(param_name, value)
        self.log_msg(f"Ramp param {param_name} set to {value}")
    self._run(f"Ramp {param_name} write", work)


def _on_pneu_read_all(self) -> None:
    """Read all pneumatic parameters (matching C# UpdatePage)."""
    def work() -> None:
        s = self._require_session()
        self._ensure_controller_capabilities()

        # Read filters (3 axes x 4 stages) in one Communication Server RPC.
        filter_keys = [(ax, st) for ax in range(3) for st in range(4)]
        supports_ramp = self._supports_controller_feature("pneumatic_ramp")
        snapshot = s.get_pneumatic_page_snapshot(
            filter_keys, include_ramp=supports_ramp
        )
        try:
            filters = snapshot["filters"]
            for key, fs in zip(filter_keys, filters):
                self.pneum_filter_buttons[key].set_info(fs.type_name[:5])
        except Exception as exc:
            for key in filter_keys:
                self.pneum_filter_buttons[key].set_info("?")
            self.log_msg(f"Pneumatic filter batch read: {exc}")

        # One request returns both input and output halves.  Do not call the
        # two slicing aliases separately: that reads the same row twice.
        for axis, values in enumerate(snapshot["steering"]):
            try:
                split = len(values) // 2
                in_vals = values[:split]
                out_vals = values[split:]
                for row, value in enumerate(in_vals[:8]):
                    self.pneum_input_matrix[(row, axis)].setText(
                        format_ui_number(value)
                    )
                for row, value in enumerate(out_vals[:8]):
                    self.pneum_output_matrix[(row, axis)].setText(
                        format_ui_number(value)
                    )
            except Exception as exc:
                self.log_msg(f"Pneumatic steering matrix axis {axis}: {exc}")

        # Read valve offsets
        try:
            offsets = snapshot["valve_offsets"]
            split = len(offsets) // 2
            up_vals = offsets[:split]
            dn_vals = offsets[split:]
            for i, ed in enumerate(self.pneum_valve_up_offsets):
                if i < len(up_vals):
                    ed.setText(format_ui_number(up_vals[i]))
            for i, ed in enumerate(self.pneum_valve_down_offsets):
                if i < len(dn_vals):
                    ed.setText(format_ui_number(dn_vals[i]))
        except Exception:
            pass

        # Read ISO offsets
        try:
            iso_vals = snapshot["motor_offsets"]
            iso_tail = iso_vals[-3:]
            for i, ed in enumerate(self.pneum_iso_offsets):
                if i < len(iso_tail):
                    ed.setText(format_ui_number(iso_tail[i]))
        except Exception:
            pass

        # Read dither
        try:
            dith_val = snapshot["dither_value"]
            dith_freq = snapshot["dither_frequency"]
            dith_alpha = snapshot["dither_alpha"]
            self.pneum_dither_amount.setText(format_ui_number(dith_val))
            self.pneum_dither_freq.setText(format_ui_number(dith_freq))
            self.pneum_dither_alpha.setText(format_ui_number(dith_alpha))
        except Exception:
            pass

        # Read floatation
        try:
            cfg = snapshot["config"]
            if len(cfg) != 3:
                raise ValueError(
                    f"PGPCP expected exactly 3 values, got {len(cfg)}: {cfg}"
                )
            # Original COM interface order: soft-up, setpoint, tolerance.
            self.pneum_float_softup.setText(format_ui_number(cfg[0]))
            self.pneum_float_setpoint.setText(format_ui_number(cfg[1]))
            self.pneum_float_mode_tol.setText(format_ui_number(cfg[2]))
        except Exception as exc:
            self.log_msg(f"Pneumatic floatation config: {exc}")

        # Read ramp parameters only when advertised by BGGSC.  Firmware that
        # omits PRamp/PneumRamp responds UNKNOWN COMMAND to PGPRP.
        if supports_ramp:
            try:
                ramp = snapshot["ramp"]
                if len(ramp) != 5:
                    raise ValueError(
                        f"PGPRP expected exactly 5 values, got {len(ramp)}: {ramp}"
                    )
                # PGPRP order: RMS hysteresis, setpoint, move-up, move-down,
                # valve-offset gradient.
                self.pneum_ramp_rms_hysteresis.setText(format_ui_number(ramp[0]))
                self.pneum_ramp_setpoint_grad.setText(format_ui_number(ramp[1]))
                self.pneum_ramp_move_up_grad.setText(format_ui_number(ramp[2]))
                self.pneum_ramp_move_down_grad.setText(format_ui_number(ramp[3]))
                self.pneum_ramp_valve_offset_grad.setText(format_ui_number(ramp[4]))
            except Exception as exc:
                self.log_msg(f"Pneumatic ramp config: {exc}")

        # Read live status
        try:
            axes_status = snapshot["axes_status"]
            _update_pneu_live_status(self, axes_status)
        except Exception:
            pass

        try:
            heights_valves = snapshot["heights_valves"]
            _update_pneu_heights_valves(self, heights_valves)
        except Exception:
            pass

        try:
            _update_pneu_status_timer(self, snapshot["status_timer"])
        except Exception:
            pass

        # Read loop statuses
        try:
            loop = snapshot["loop"]
            loop_bits = {
                "overall": bool(loop.system & 0x00001),
                "velocity": bool(loop.individual & 0x01),
                "position": bool(loop.individual & 0x02),
                "pneumatic": bool(loop.system & 0x00040),
                "ff": bool(loop.system & 0x00004),
                "pff": bool(loop.system & 0x04000),
                "dither_compensation": bool(loop.system & 0x02000),
                "reference_metrology": bool(loop.system & 0x20000),
                "move_up_at_startup": bool(loop.system & 0x00008),
                "use_setpoint_for_all": bool(snapshot["setpoint_status"]),
            }
            _update_pneu_loop_leds(self, loop_bits)
        except Exception:
            pass

        # Read individual loop status
        try:
            _position, pneumatic, _digital_in, _digital_out = snapshot[
                "axis_loop_status"
            ]
            indiv = [int(bool(pneumatic & (1 << bit))) for bit in range(3)]
            _update_pneu_individual_loop_btns(self, indiv)
        except Exception:
            pass

        self.log_msg("All pneumatic parameters read")
    self._run("Read all pneumatic", work)


def _on_pneu_write_all(self) -> None:
    """Write all pneumatic parameters to the controller."""
    def work() -> None:
        s = self._require_session()
        assert self.gate
        self._ensure_controller_capabilities()
        supports_ramp = self._supports_controller_feature("pneumatic_ramp")
        if not self._confirm_write("Write all pneumatic parameters?"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            up_vals = [float(ed.text()) for ed in self.pneum_valve_up_offsets]
            dn_vals = [float(ed.text()) for ed in self.pneum_valve_down_offsets]
            iso_vals = [float(ed.text()) for ed in self.pneum_iso_offsets]
            dith_val = float(self.pneum_dither_amount.text())
            dith_freq = _pneumatic_config_int(self.pneum_dither_freq.text())
            dith_alpha = float(self.pneum_dither_alpha.text())
            softup = _pneumatic_config_int(self.pneum_float_softup.text())
            setpoint = _pneumatic_config_int(self.pneum_float_setpoint.text())
            tolerance = _pneumatic_config_int(self.pneum_float_mode_tol.text())
            ramp = (
                [
                    _pneumatic_config_int(
                        self.pneum_ramp_rms_hysteresis.text()
                    ),
                    float(self.pneum_ramp_setpoint_grad.text()),
                    float(self.pneum_ramp_move_up_grad.text()),
                    float(self.pneum_ramp_move_down_grad.text()),
                    float(self.pneum_ramp_valve_offset_grad.text()),
                ]
                if supports_ramp
                else []
            )

            current_offsets = s.get_pneumatic_valve_offsets()
            half = len(current_offsets) // 2
            s.set_pneumatic_valve_offsets(
                up_vals[:half] + dn_vals[:len(current_offsets) - half]
            )
            s.set_motor_and_iso_offset_values(iso_vals)
            s.set_dither(dith_val, dith_freq, dith_alpha)
            s.set_pneumatic_config(softup, setpoint, tolerance)
            if supports_ramp:
                s.set_pneumatic_ramp_parameters(*ramp)

            for axis in range(3):
                inputs = [
                    float(self.pneum_input_matrix[(row, axis)].text())
                    for row in range(8)
                ]
                outputs = [
                    float(self.pneum_output_matrix[(row, axis)].text())
                    for row in range(8)
                ]
                s.set_pneumatic_input_steering_matrix(axis, inputs)
                s.set_pneumatic_output_steering_matrix(axis, outputs)
        finally:
            self._set_writable(True)

        self.log_msg("All pneumatic parameters written")
    self._run("Write all pneumatic", work)


def _update_pneu_live_status(self, axes_status: list[int]) -> None:
    """Update the live status list with axes status data."""
    items = self._pneu_status_items
    if len(axes_status) >= 8:
        try:
            status_index = int(float(axes_status[0]))
        except (TypeError, ValueError):
            status_index = -1
        status_text = (
            _PNEU_VERTICAL_STATUS_NAMES[status_index]
            if 0 <= status_index < len(_PNEU_VERTICAL_STATUS_NAMES)
            else str(axes_status[0])
        )
        items["Status"].setText(1, status_text)
        # Column 3 contains the literal RefPoint label; its value belongs in
        # column 4, exactly as legacy Member4.
        items["Status"].setText(4, format_ui_number(axes_status[1]))
        for column, value in enumerate(axes_status[2:5], 1):
            items["AxesInput"].setText(column, format_ui_number(value))
        for column, value in enumerate(axes_status[5:8], 1):
            items["AxesOutput"].setText(column, format_ui_number(value))


def _pneumatic_io_counts(self) -> tuple[int, int]:
    """Return PNEUMIO input/output dimensions from the cached BGGSC tokens."""
    for token in getattr(self, "_system_constants", ())[11:]:
        text = str(token).upper()
        if not text.startswith("PNEUMIO#"):
            continue
        parts = text.split("#")
        if len(parts) == 3:
            try:
                return (
                    max(1, min(8, int(parts[1]))),
                    max(1, min(8, int(parts[2]))),
                )
            except ValueError:
                break
    return 4, 4


def _configure_pneu_live_rows(self) -> None:
    """Match legacy conditional rows to PNEUMIO and feature capabilities."""
    num_inputs, num_outputs = _pneumatic_io_counts(self)
    items = self._pneu_status_items
    items["ValveSet2"].setHidden(num_outputs <= 4)
    items["HeightSet2"].setHidden(num_inputs <= 4)
    # The legacy page allocates PosError but never inserts it in the visible
    # collection.  Cascaded is present only when that capability is advertised.
    items["PosError"].setHidden(True)
    items["Cascaded"].setHidden(
        not self._supports_controller_feature("cascaded_position")
    )


def _update_pneu_heights_valves(self, heights_valves: list[float]) -> None:
    """Update the live status list with heights and valve data."""
    items = self._pneu_status_items
    num_inputs, num_outputs = _pneumatic_io_counts(self)
    values = list(heights_valves)
    if len(values) < num_inputs + num_outputs:
        return

    for index in range(min(num_inputs, 8)):
        row = "HeightSet1" if index < 4 else "HeightSet2"
        column = index + 1 if index < 4 else index - 3
        items[row].setText(column, format_ui_number(values[index]))
    for index in range(min(num_outputs, 8)):
        row = "ValveSet1" if index < 4 else "ValveSet2"
        column = index + 1 if index < 4 else index - 3
        items[row].setText(
            column, format_ui_number(values[num_inputs + index])
        )


def _update_pneu_status_timer(self, timers: tuple[float, float]) -> None:
    """Update the source UI's OK/NOK pneumatic status timers."""
    if len(timers) < 2:
        return
    item = self._pneu_status_items["TimerStatus"]
    item.setText(1, format_ui_number(timers[0]))
    item.setText(4, format_ui_number(timers[1]))


def _refresh_pneumatic_live_state(self, loop=None) -> None:
    """Poll only the live pneumatic state required by the visible Status grid."""
    self._ensure_controller_capabilities()
    _configure_pneu_live_rows(self)
    session = self._require_session()
    try:
        _update_pneu_live_status(self, session.get_pneumatic_axes_status())
    except Exception as exc:
        self._report_live_refresh_error("pneumatic axes", exc)
    try:
        _update_pneu_heights_valves(self, session.get_pneumatic_heights_valves())
    except Exception as exc:
        self._report_live_refresh_error("pneumatic heights/valves", exc)
    try:
        _update_pneu_status_timer(self, session.get_pneumatic_status_timer())
    except Exception as exc:
        self._report_live_refresh_error("pneumatic status timer", exc)
    try:
        use_setpoint_for_all = bool(session.get_pneumatic_setpoint_status())
    except Exception as exc:
        use_setpoint_for_all = False
        self._report_live_refresh_error("pneumatic setpoint status", exc)
    if loop is None:
        loop = session.get_loop_status()
    try:
        _position, pneumatic, _digital_in, _digital_out = (
            session.get_pos_pneum_digital_status()
        )
        _set_pneum_individual_loop_buttons(self, pneumatic)
    except Exception as exc:
        self._report_live_refresh_error("pneumatic individual loops", exc)
    _update_pneu_loop_leds(self, {
        "pneumatic": bool(loop.system & 0x00040),
        "dither_compensation": bool(loop.system & 0x02000),
        "reference_metrology": bool(loop.system & 0x20000),
        "move_up_at_startup": bool(loop.system & 0x00008),
        "use_setpoint_for_all": use_setpoint_for_all,
    })


def _apply_pneumatic_live_snapshot(self, snapshot, loop) -> None:
    """Apply one transport-free pneumatic refresh collected in the worker."""
    _configure_pneu_live_rows(self)
    _update_pneu_live_status(self, snapshot.get("pneumatic_axes_status", []))
    _update_pneu_heights_valves(
        self, snapshot.get("pneumatic_heights_valves", [])
    )
    timers = snapshot.get("pneumatic_status_timer", ())
    if timers:
        _update_pneu_status_timer(self, timers)
    use_setpoint_for_all = bool(snapshot.get("pneumatic_setpoint_status", 0))
    axis_status = snapshot.get("axis_status", ())
    if len(axis_status) > 1:
        _set_pneum_individual_loop_buttons(self, int(axis_status[1]))
    _update_pneu_loop_leds(self, {
        "pneumatic": bool(loop.system & 0x00040),
        "dither_compensation": bool(loop.system & 0x02000),
        "reference_metrology": bool(loop.system & 0x20000),
        "move_up_at_startup": bool(loop.system & 0x00008),
        "use_setpoint_for_all": use_setpoint_for_all,
    })


def _update_pneu_loop_leds(self, loop_bits: dict[str, bool]) -> None:
    """Update the loop status LEDs based on system loop bits."""
    mapping = {
        "pneu": loop_bits.get("pneumatic", False),
        "dither_comp": loop_bits.get("dither_compensation", False),
        "ref_metrology": loop_bits.get("reference_metrology", False),
        "move_up_startup": loop_bits.get("move_up_at_startup", False),
        "use_setpoint_all": loop_bits.get("use_setpoint_for_all", False),
    }
    for key, on in mapping.items():
        if key in self.pneum_loop_leds:
            self.pneum_loop_leds[key].set_on(on)


def _update_pneu_individual_loop_btns(self, indiv: list[int]) -> None:
    """Update the individual loop status toggle buttons."""
    for idx, btn in enumerate(self.pneum_loop_toggle_btns):
        if idx < len(indiv):
            btn.setChecked(bool(indiv[idx]))


def _on_pneum_read_all(self) -> None:
    """Read all pneumatic parameters from controller."""
    def work() -> None:
        s = self._require_session()
        # Read filters
        for ax in range(3):
            for st in range(4):
                try:
                    fs = s.get_pneumatic_filter(ax, st)
                    self.pneum_filter_buttons[(ax, st)].set_info(fs.type_name[:5])
                except Exception:
                    pass
        # Read steering matrix (input = first half, output = second half)
        for ax in range(3):
            try:
                row = s.get_pneumatic_steering_matrix(ax)
                half = len(row) // 2
                if hasattr(self, 'pneum_input_matrix'):
                    for i, v in enumerate(row[:half]):
                        if (i, ax) in self.pneum_input_matrix:
                            self.pneum_input_matrix[(i, ax)].setText(
                                format_ui_number(v)
                            )
                if hasattr(self, 'pneum_output_matrix'):
                    for i, v in enumerate(row[half:], start=half):
                        if (i - half, ax) in self.pneum_output_matrix:
                            self.pneum_output_matrix[(i - half, ax)].setText(
                                format_ui_number(v)
                            )
            except Exception:
                pass
        # Read valve offsets
        try:
            off = s.get_pneumatic_valve_offsets()
            half = len(off) // 2
            if hasattr(self, 'pneu_valve_up'):
                for i in range(min(half, len(self.pneu_valve_up))):
                    self.pneu_valve_up[i].setText(format_ui_number(off[i]))
            if hasattr(self, 'pneu_valve_down'):
                for i in range(min(half, len(self.pneu_valve_down))):
                    self.pneu_valve_down[i].setText(format_ui_number(off[i + half]))
        except Exception:
            pass
        # Read dither
        try:
            val = s.get_pneumatic_dither_value()
            self.pneum_dither_amount.setText(format_ui_number(val))
        except Exception:
            pass
        try:
            freq = s.get_dither_frequency()
            self.pneum_dither_freq.setText(format_ui_number(freq))
        except Exception:
            pass
        try:
            alpha = s.get_pneumatic_dither_compensation()
            self.pneum_dither_alpha.setText(format_ui_number(alpha))
        except Exception:
            pass
        self.log_msg("pneumatic all read")
    self._run("Read pneumatic", work)


def _on_pneum_write_all(self) -> None:
    """Compatibility entry point used by the reference page."""
    _on_pneu_write_all(self)


def apply_patches(cls: type) -> None:
    """Install pneumatic handlers and the reference two-column page."""
    prefixes = (
        "_build_", "_on_", "on_", "_pneu_", "_update_", "_apply_", "_sync_", "_refresh_",
    )
    for name, value in globals().items():
        if name.startswith(prefixes) and callable(value):
            setattr(cls, name, value)
    cls._build_pneumatic_tab = _build_pneumatic_tab_reference
