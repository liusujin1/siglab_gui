"""Reference-image layouts for pages that do not map to generic forms.

The original SAMBA19xUI pages are deliberately sparse, pixel-oriented
control panels.  These builders keep the existing handler attributes while
presenting the matrices in the same orientation as the supplied screenshots.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from python_samba.ui.classic_widgets import (
    ClassicExpander,
    FlatPush,
    FilterStageCell,
    GroupPanel,
    IOSignalButton,
    LedIndicator,
    RockerButton,
    SciEdit,
)
from python_samba.ui.widgets import MatrixEditor


AXES = ["Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"]
POS_AXES = ["Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans"]
VEL_INPUTS = ["X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB"]
VEL_OUTPUTS = [
    "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
]
POS_INPUTS = ["Input1", "Input2", "Input3", "Input4", "Input5", "Input6"]
POS_OUTPUTS = [f"Output{i}" for i in range(1, 9)]

REFERENCE_PAGE_STYLE = """
    QLabel { font-size: 25px; }
    QComboBox { font-size: 25px; min-height: 32px; }
"""

EDIT_STYLE = """
    QLineEdit {
        background:#fbfbfb; color:#111; border:2px solid #aaa9ad;
        border-radius:0; padding:1px 4px; font-size:25px;
    }
    QLineEdit:read-only { background:#cae5f5; color:#495b65; }
"""


def _edit(text: str = "0", width: int = 90, height: int = 39) -> SciEdit:
    widget = SciEdit(text)
    widget.setFixedSize(width, height)
    widget.setStyleSheet(EDIT_STYLE)
    return widget


def _header(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setAlignment(QtCore.Qt.AlignCenter)
    label.setStyleSheet("font-size:25px;font-weight:500;")
    return label


def _hide(*widgets: QtWidgets.QWidget) -> None:
    for widget in widgets:
        widget.hide()


# ---------------------------------------------------------------------------
# Controller / AD-DA Mapping
# ---------------------------------------------------------------------------

def _build_ad_da_mapping_page_reference(self) -> QtWidgets.QWidget:
    from python_samba.ui.main_window import ADC_INPUT_NAMES, DAC_OUTPUT_NAMES

    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(10)

    adc_group = GroupPanel("AD-Converter Mapping")
    adc_group.setFixedSize(1545, 580)
    grid = QtWidgets.QGridLayout(adc_group)
    grid.setContentsMargins(14, 18, 14, 10)
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(2)

    adc_columns = [range(0, 12), range(12, 24), range(24, 32)]
    self.adc_edits: list[SciEdit | None] = [None] * len(ADC_INPUT_NAMES)
    for column, indices in enumerate(adc_columns):
        base = column * 2
        for row, index in enumerate(indices):
            name = ADC_INPUT_NAMES[index]
            label = QtWidgets.QLabel(name)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            edit = _edit("44" if index >= 25 else "0")
            self.adc_edits[index] = edit
            grid.addWidget(label, row, base)
            grid.addWidget(edit, row, base + 1)

    temperature_defaults = ("44", "26", "27", "28", "44", "29", "44", "30", "31", "32", "44", "33")
    self.adc_temperature_edits = []
    for row, (name, default) in enumerate(
        zip(IOSignalButton.TEMPERATURE_NAMES, temperature_defaults)
    ):
        label = QtWidgets.QLabel(name)
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        edit = _edit(default)
        self.adc_temperature_edits.append(edit)
        grid.addWidget(label, row, 6)
        grid.addWidget(edit, row, 7)

    used_box = QtWidgets.QWidget()
    used = QtWidgets.QVBoxLayout(used_box)
    used.setContentsMargins(12, 0, 0, 0)
    used.addWidget(_header("Used ADC Num"))
    self.adc_set_num = QtWidgets.QComboBox()
    # NGASN/NSASN transport a set index (0..7), while the legacy combo shows
    # the corresponding number of ADC channels.  The final set contains only
    # four more channels, hence 7 means 40 rather than 42.
    self.adc_set_num.addItems(["0", "6", "12", "18", "24", "30", "36", "40"])
    self.adc_set_num.setFixedSize(285, 50)
    used.addWidget(self.adc_set_num)
    set_adc = FlatPush("Set ADC Num")
    set_adc.setFixedSize(285, 50)
    set_adc.clicked.connect(self.on_adc_write)
    used.addWidget(set_adc)
    used.addStretch(1)
    grid.addWidget(used_box, 0, 8, 6, 1)

    self.adc_edits = [editor for editor in self.adc_edits if editor is not None]
    for editor in self.adc_edits + self.adc_temperature_edits:
        editor.editingFinished.connect(self.on_adc_write)
    root.addWidget(adc_group, 0, QtCore.Qt.AlignLeft)

    dac_group = GroupPanel("DA-Converter Mapping")
    dac_group.setFixedSize(1545, 470)
    dac_grid = QtWidgets.QGridLayout(dac_group)
    dac_grid.setContentsMargins(110, 18, 14, 10)
    dac_grid.setHorizontalSpacing(28)
    dac_grid.setVerticalSpacing(2)
    self.dac_edits = []
    for index, name in enumerate(DAC_OUTPUT_NAMES):
        column = 0 if index < 10 else 2
        row = index if index < 10 else index - 10
        label = QtWidgets.QLabel(name)
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        edit = _edit("0")
        self.dac_edits.append(edit)
        edit.editingFinished.connect(self.on_dac_write)
        dac_grid.addWidget(label, row, column)
        dac_grid.addWidget(edit, row, column + 1)
    dac_grid.setColumnStretch(4, 1)
    root.addWidget(dac_group, 0, QtCore.Qt.AlignLeft)
    root.addStretch(1)
    return page


# ---------------------------------------------------------------------------
# Controller / Motor Protection
# ---------------------------------------------------------------------------

def _build_motor_protection_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QHBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(28)

    left = GroupPanel("Motor Threshold Setting")
    left.setFixedSize(980, 720)
    grid = QtWidgets.QGridLayout(left)
    grid.setContentsMargins(10, 18, 10, 10)
    grid.setHorizontalSpacing(2)
    grid.setVerticalSpacing(0)
    for column, text in enumerate(("", "Threshold", "Offset", "Actual Value", "Status", "Motors Limit[%]")):
        grid.addWidget(_header(text), 0, column)

    names = ["X1Out", "Y1Out", "Z1Out", "X2Out", "Y2Out", "Z2Out",
             "X3Out", "Y3Out", "Z3Out", "X4Out", "Y4Out", "Z4Out"]
    self.mot_thresholds = []
    self.mot_offsets = []
    self.mot_offset_labels = []
    self.mot_actual_values = []
    self.mot_status_labels = []
    self.mot_limit_edits = []
    for row, name in enumerate(names, 1):
        name_label = QtWidgets.QLabel(name)
        self.mot_offset_labels.append(name_label)
        threshold = _edit("0.0000E+00", 205, 47)
        offset = _edit("0.0000E+00", 165, 47)
        actual = _edit("0.0000E+00", 165, 47)
        actual.setReadOnly(True)
        status = QtWidgets.QLabel("Normal")
        status.setFixedSize(165, 47)
        status.setStyleSheet("background:#89e88b;border:2px solid #aaa9ad;padding-left:5px;font-size:21px;")
        limit = _edit("0" if row == 1 else "", 165, 47)
        self.mot_thresholds.append(threshold)
        self.mot_offsets.append(offset)
        self.mot_actual_values.append(actual)
        self.mot_status_labels.append(status)
        self.mot_limit_edits.append(limit)
        for column, widget in enumerate((name_label, threshold, offset, actual, status, limit)):
            grid.addWidget(widget, row, column)

    actions = QtWidgets.QHBoxLayout()
    set_threshold = FlatPush("Set Threshold")
    set_offset = FlatPush("Set Offset")
    set_threshold.clicked.connect(self.on_motor_prot_write)
    set_offset.clicked.connect(self.on_motor_offset_write)
    actions.addWidget(set_threshold)
    actions.addWidget(set_offset)
    actions.addStretch(1)
    grid.addLayout(actions, 13, 1, 1, 5)
    root.addWidget(left, 0, QtCore.Qt.AlignTop)

    right = QtWidgets.QVBoxLayout()
    right.setSpacing(5)

    settings = GroupPanel("")
    settings.setFixedSize(540, 200)
    form = QtWidgets.QGridLayout(settings)
    self.mot_cool = _edit("0", 170)
    self.mot_delay = _edit("0", 170)
    self.mot_use_temperature = _ReferenceToggle()
    self.mot_disable = _ReferenceToggle()
    form.addWidget(QtWidgets.QLabel("Motor Cooling Constant"), 0, 0)
    form.addWidget(self.mot_cool, 0, 1)
    form.addWidget(QtWidgets.QLabel("Reset Delay Time[sec.]"), 1, 0)
    form.addWidget(self.mot_delay, 1, 1)
    form.addWidget(QtWidgets.QLabel("Use Temperature Sensors"), 2, 0)
    form.addWidget(self.mot_use_temperature, 2, 1, QtCore.Qt.AlignLeft)
    form.addWidget(QtWidgets.QLabel("Disable all by Failure"), 3, 0)
    form.addWidget(self.mot_disable, 3, 1, QtCore.Qt.AlignLeft)

    self.motor_overheat_expander = ClassicExpander(
        "Motor Overheating Setting", settings, expanded=True
    )
    right.addWidget(self.motor_overheat_expander)

    power = GroupPanel("")
    power.setFixedSize(540, 360)
    power_grid = QtWidgets.QGridLayout(power)
    power_grid.setContentsMargins(8, 12, 8, 8)
    power_grid.setHorizontalSpacing(4)
    power_grid.setVerticalSpacing(0)
    self.ps_current_limit = _edit("0", 170, 38)
    self.ps_current_si_unit = _edit("0", 170, 38)
    self.ps_overpowered = QtWidgets.QLabel("No")
    self.ps_overpowered.setAlignment(QtCore.Qt.AlignCenter)
    self.ps_overpowered.setFixedSize(170, 38)
    self.ps_overpowered.setStyleSheet(
        "background:#89e88b;border:2px solid #aaa9ad;font-size:21px;"
    )
    self.ps_actual_values = []
    power_rows = (
        ("Current Limit [Amp]", self.ps_current_limit),
        ("Current SIUnit [Amp/10V]", self.ps_current_si_unit),
        ("Power Supply Overpowered?", self.ps_overpowered),
    )
    for row, (label, editor) in enumerate(power_rows):
        power_grid.addWidget(QtWidgets.QLabel(label), row, 0)
        power_grid.addWidget(editor, row, 1)
    for row, label in enumerate(
        (
            "Actual PosCurrent:",
            "Actual NegCurrent:",
            "Complete Current:",
            "Max. Complete Current:",
            "Overpowered Counter:",
        ),
        3,
    ):
        editor = _edit("0", 170, 34)
        editor.setReadOnly(True)
        self.ps_actual_values.append(editor)
        power_grid.addWidget(QtWidgets.QLabel(label), row, 0)
        power_grid.addWidget(editor, row, 1)

    reset_counter = FlatPush("Reset Counter")
    reset_max = FlatPush("Reset Max Value")
    reset_counter.clicked.connect(self.on_power_supply_reset_counter)
    reset_max.clicked.connect(self.on_power_supply_reset_max)
    power_grid.addWidget(reset_counter, 8, 0)
    power_grid.addWidget(reset_max, 8, 1)
    self.ps_current_limit.editingFinished.connect(self.on_power_supply_write)
    self.ps_current_si_unit.editingFinished.connect(self.on_power_supply_write)

    self.power_supply_expander = ClassicExpander(
        "Power Supply Current Limit", power, expanded=True
    )
    for expander in (
        self.motor_overheat_expander,
        self.power_supply_expander,
    ):
        expander.setFixedWidth(550)
        expander.title_button.setStyleSheet(
            "QPushButton{color:#008318;background:transparent;border:none;"
            "text-align:left;padding:0;font-size:28px;font-weight:800;"
            "font-style:italic;}QPushButton:hover{color:#006b14;}"
        )
    right.addWidget(self.power_supply_expander)
    right.addStretch(1)
    root.addLayout(right)
    root.addStretch(1)
    return page


# ---------------------------------------------------------------------------
# Status / Signals Display and DigIO
# ---------------------------------------------------------------------------

def _build_signal_display_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(0, 4, 0, 0)
    root.setSpacing(0)
    matrix = QtWidgets.QGridLayout()
    matrix.setContentsMargins(0, 0, 0, 0)
    matrix.setHorizontalSpacing(0)
    matrix.setVerticalSpacing(0)

    self.sig_selectors = []
    self.sig_name_labels = []
    self.sig_values = []
    colors = ["#55484e", "#314986", "#801d08", "#0f5316"]
    for index in range(16):
        row, column = divmod(index, 4)
        card = QtWidgets.QWidget()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        selector = IOSignalButton(
            "X1FB",
            tokens=(0, 0, 0),
            supported_io=IOSignalButton.ALL_SIGNALS,
            position_stages=4,
        )
        selector.setFixedHeight(33)
        selector.setStyleSheet(
            "QPushButton{background:#2b444d;color:white;border:2px solid white;"
            "border-radius:0;font-size:18px;padding:2px 8px;text-align:left;}"
        )
        name = QtWidgets.QLabel("X1FB")
        name.setAlignment(QtCore.Qt.AlignCenter)
        name.setFixedHeight(59)
        name.setStyleSheet("background:#cbbabb;font-size:27px;")
        value = QtWidgets.QLabel("0")
        value.setAlignment(QtCore.Qt.AlignCenter)
        value.setFixedHeight(132)
        value.setStyleSheet(f"background:{colors[row]};color:white;font-size:58px;font-weight:300;")
        selector.ioSignalChanged.connect(
            lambda _tokens, label=name, button=selector: label.setText(button.text())
        )
        selector.ioSignalChanged.connect(
            lambda tokens, num=index, button=selector:
                self._on_sig_selector_changed(num, tokens, button)
        )
        self.sig_selectors.append(selector)
        self.sig_name_labels.append(name)
        self.sig_values.append(value)
        card_layout.addWidget(selector)
        card_layout.addWidget(name)
        card_layout.addWidget(value)
        matrix.addWidget(card, row, column)
    root.addLayout(matrix)

    buttons = QtWidgets.QHBoxLayout()
    buttons.setContentsMargins(0, 0, 0, 0)
    buttons.addStretch(1)
    save = FlatPush("Save...")
    open_button = FlatPush("Open...")
    save.setFixedSize(390, 45)
    open_button.setFixedSize(390, 45)
    save.clicked.connect(self.on_sig_save_settings)
    open_button.clicked.connect(self.on_sig_load_settings)
    buttons.addWidget(save)
    buttons.addWidget(open_button)
    buttons.addStretch(1)
    root.addLayout(buttons)
    root.addStretch(1)

    self._sig_monitoring = False
    self._sig_monitoring_active = False
    self._sig_mon_btn = FlatPush("Start Monitoring")
    self._sig_mon_btn.hide()
    return page


class _StatusOrb(QtWidgets.QLabel):
    def __init__(self, on: bool = False) -> None:
        super().__init__("0")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setFixedSize(38, 38)
        self.set_on(on)

    def set_on(self, on: bool, _color: str | None = None) -> None:
        edge = "#24ae35" if on else "#a7a7a7"
        center = "#3bd34a" if on else "#e7e7e7"
        self.setStyleSheet(
            "QLabel{border:4px solid " + edge + ";border-radius:19px;"
            "background:" + center + ";font-size:17px;font-weight:600;}"
        )

    def set_color(self, color: str) -> None:
        self.set_on(str(color).lower() not in {"", "gray", "grey", "off", "black"})


class _ReferenceToggle(QtWidgets.QToolButton):
    """Compact checkable OFF/ON button used by the reference control panels."""

    def __init__(self) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(58, 48)
        self.toggled.connect(self._refresh)
        self._refresh(False)

    def _refresh(self, checked: bool) -> None:
        self.setText("ON" if checked else "OFF")
        self.setStyleSheet(
            "QToolButton{background:qradialgradient(cx:.45,cy:.45,radius:.75,"
            "stop:0 #f8f8f8,stop:.55 #c5bfc0,stop:1 #8e898a);"
            "border:3px solid #a89fa0;border-radius:7px;font-size:17px;"
            "font-weight:800;color:#111;}"
            "QToolButton:checked{background:qradialgradient(cx:.5,cy:.5,radius:.62,"
            "stop:0 #efff15,stop:.38 #bde514,stop:.72 #3a8f15,stop:1 #0a5016);}"
        )


def _status_item(name: str, orb: _StatusOrb) -> QtWidgets.QWidget:
    widget = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(widget)
    column.setContentsMargins(2, 2, 2, 2)
    label = QtWidgets.QLabel(name)
    label.setAlignment(QtCore.Qt.AlignCenter)
    label.setStyleSheet("font-size:19px;")
    column.addWidget(label)
    column.addWidget(orb, 0, QtCore.Qt.AlignCenter)
    return widget


def _build_digio_tab_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(8)

    input_group = GroupPanel("Digital-IO-Input Status")
    input_group.setFixedSize(1545, 245)
    input_grid = QtWidgets.QGridLayout(input_group)
    input_grid.setContentsMargins(10, 18, 10, 8)
    input_names = [
        "MonBtn", "ResBtn", "OPTOIN1", "OPTOIN2", "OPTOIN3", "OPTOIN4",
        "Amp1TempErr", "Amp1PwrErr", "Amp1Conn",
        "Amp2TempErr", "Amp2PwrErr", "Amp2Conn", "24V", "15V", "MBoardRev",
    ]
    input_orbs = [_StatusOrb(name in {"Amp1Conn", "Amp2Conn"}) for name in input_names]
    self._digio_input_leds = input_orbs
    # Compatibility aliases retained for older page extensions.  These are
    # digital input indicators, not position/pneumatic loop indicators.
    self._digio_pos_leds = input_orbs[:12]
    self._digio_pneu_leds = input_orbs[12:15]
    for index, (name, orb) in enumerate(zip(input_names, input_orbs)):
        row = 0 if index < 9 else 1
        column = index if index < 9 else index - 9
        input_grid.addWidget(_status_item(name, orb), row, column)
    root.addWidget(input_group, 0, QtCore.Qt.AlignLeft)

    output_group = GroupPanel("Digital-IO-Output Status")
    output_group.setFixedSize(1545, 255)
    output_grid = QtWidgets.QGridLayout(output_group)
    output_grid.setContentsMargins(10, 18, 10, 8)
    output_names = [
        "OCOUT1", "OCOUT2", "OCOUT3", "OCOUT4", "OCOUT5", "SysErr",
        "OPTOOUT1", "OPTOOUT2", "OPTOOUT3", "OPTOOUT4", "Amp1On", "Amp2On",
        "MonLed", "ResLed", "Reserve",
    ]
    self._digio_output_leds = [_StatusOrb(False) for _ in output_names]
    for index, (name, orb) in enumerate(zip(output_names, self._digio_output_leds)):
        row = 0 if index < 9 else 1
        column = index if index < 9 else index - 9
        output_grid.addWidget(_status_item(name, orb), row, column)
    root.addWidget(output_group, 0, QtCore.Qt.AlignLeft)
    root.addStretch(1)
    return page


# ---------------------------------------------------------------------------
# Velocity matrices
# ---------------------------------------------------------------------------

def _matrix_edit(value: str = "0", width: int = 160) -> SciEdit:
    return _edit(value, width, 35)


def _build_vel_matrix_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    root.setSpacing(10)

    sensor = GroupPanel("Sensor Matrix")
    sensor.setFixedSize(1190, 440)
    sensor_grid = QtWidgets.QGridLayout(sensor)
    sensor_grid.setContentsMargins(10, 18, 10, 10)
    sensor_grid.setSpacing(0)
    for column, axis in enumerate(AXES, 1):
        sensor_grid.addWidget(_header(axis), 0, column)
    sensor_cells = [[None for _ in VEL_INPUTS] for _ in AXES]
    for input_index, input_name in enumerate(VEL_INPUTS):
        sensor_grid.addWidget(QtWidgets.QLabel(input_name), input_index + 1, 0)
        for axis_index in range(6):
            edit = _matrix_edit("0")
            sensor_cells[axis_index][input_index] = edit
            sensor_grid.addWidget(edit, input_index + 1, axis_index + 1)
    sensor_grid.setRowStretch(len(VEL_INPUTS) + 1, 1)
    self.vel_inp_edits = sensor_cells
    root.addWidget(sensor, 0, QtCore.Qt.AlignLeft)

    motor = GroupPanel("Motor Matrix")
    motor.setFixedSize(1190, 610)
    motor_grid = QtWidgets.QGridLayout(motor)
    motor_grid.setContentsMargins(10, 18, 10, 10)
    motor_grid.setSpacing(0)
    for column, axis in enumerate(AXES, 1):
        motor_grid.addWidget(_header(axis), 0, column)
    motor_cells = [[None for _ in VEL_OUTPUTS] for _ in AXES]
    for output_index, output_name in enumerate(VEL_OUTPUTS):
        motor_grid.addWidget(QtWidgets.QLabel(output_name), output_index + 1, 0)
        for axis_index in range(6):
            edit = _matrix_edit("0")
            motor_cells[axis_index][output_index] = edit
            motor_grid.addWidget(edit, output_index + 1, axis_index + 1)
    motor_grid.setRowStretch(len(VEL_OUTPUTS) + 1, 1)
    self.vel_out_edits = motor_cells
    root.addWidget(motor, 0, QtCore.Qt.AlignLeft)

    self.vel_sens_edits = self.vel_inp_edits[0]
    self.vel_motor_edits = self.vel_out_edits[0]
    self.vel_sens_panel = self.vel_inp_edits
    self.vel_motor_panel = self.vel_out_edits
    self.vel_sens = MatrixEditor(7)
    self.vel_motor = MatrixEditor(12)
    _hide(self.vel_sens, self.vel_motor)

    # The original Matrix control writes the changed axis as soon as a cell
    # is committed.  The reference-layout rebuild had dropped those events.
    for axis, edits in enumerate(self.vel_inp_edits):
        for edit in edits:
            edit.editingFinished.connect(
                lambda a=axis: self._on_vel_matrix_axis_changed("sensor", a)
            )
    for axis, edits in enumerate(self.vel_out_edits):
        for edit in edits:
            edit.editingFinished.connect(
                lambda a=axis: self._on_vel_matrix_axis_changed("motor", a)
            )
    root.addStretch(1)
    return page


def on_vel_mat_read_reference(self) -> None:
    def work() -> None:
        session = self._require_session()
        for axis in range(6):
            sensor = session.get_velocity_sensor_matrix(axis)
            motor = session.get_velocity_motor_matrix(axis)
            for edit, value in zip(self.vel_inp_edits[axis], sensor):
                edit.setText(f"{float(value):g}")
            for edit, value in zip(self.vel_out_edits[axis], motor):
                edit.setText(f"{float(value):g}")
        self.log_msg("velocity matrices read (all 6 axes)")
    self._run("Read velocity matrices", work)


def on_vel_mat_write_reference(self) -> None:
    def work() -> None:
        session = self._require_session()
        if not self._confirm_write("Write velocity sensor/motor matrices for all 6 axes"):
            return
        assert self.gate
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            for axis in range(6):
                session.set_velocity_sensor_matrix(axis, [float(edit.text()) for edit in self.vel_inp_edits[axis]])
                session.set_velocity_motor_matrix(axis, [float(edit.text()) for edit in self.vel_out_edits[axis]])
        finally:
            self._set_writable(True)
        self.log_msg("velocity matrices written (all 6 axes)")
    self._run("Write velocity matrices", work)


def _on_vel_matrix_axis_changed_reference(self, kind: str, axis: int) -> None:
    """Write only the matrix/axis edited by the user, like the C# control."""
    if not self.session or not self.session.connected:
        return

    def work() -> None:
        session = self._require_session()
        if not self._confirm_write(f"Write velocity {kind} matrix axis {axis}"):
            return
        if self.gate is None:
            raise RuntimeError("Safety gate is not initialized")
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            if kind == "sensor":
                values = [float(edit.text()) for edit in self.vel_inp_edits[axis]]
                session.set_velocity_sensor_matrix(axis, values)
            elif kind == "motor":
                values = [float(edit.text()) for edit in self.vel_out_edits[axis]]
                session.set_velocity_motor_matrix(axis, values)
            else:
                raise ValueError(f"unknown velocity matrix kind: {kind}")
        finally:
            self._set_writable(True)
        self.log_msg(f"velocity {kind} matrix axis={axis} written")

    self._run("Write velocity matrix axis", work)


# ---------------------------------------------------------------------------
# Position sensor/motor matrices
# ---------------------------------------------------------------------------

def _device_value_cell(device_names: list[str], default: str) -> tuple[QtWidgets.QWidget, QtWidgets.QComboBox, SciEdit]:
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    combo = QtWidgets.QComboBox()
    combo.addItems(device_names)
    combo.setCurrentText(default)
    combo.setFixedSize(135, 34)
    edit = _edit("0", 135, 31)
    layout.addWidget(combo)
    layout.addWidget(edit)
    return widget, combo, edit


def _build_pos_sensor_matrix_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    group = GroupPanel("Sensor Matrix")
    group.setFixedSize(1545, 550)
    grid = QtWidgets.QGridLayout(group)
    grid.setContentsMargins(10, 18, 10, 10)
    grid.setSpacing(0)
    for column, name in enumerate(POS_INPUTS, 1):
        grid.addWidget(_header(name), 0, column)
    device_names = IOSignalButton.INPUT_NAMES
    self.pos_sensor_device_combos = []
    self.pos_sensor_matrix_edits = []
    for axis, axis_name in enumerate(POS_AXES):
        grid.addWidget(QtWidgets.QLabel(axis_name), axis + 1, 0)
        combo_row = []
        edit_row = []
        for column in range(6):
            cell, combo, edit = _device_value_cell(device_names, "X1FB")
            combo_row.append(combo)
            edit_row.append(edit)
            combo.currentIndexChanged.connect(
                lambda _index: self.on_pos_mat_write("sensor")
            )
            edit.editingFinished.connect(lambda: self.on_pos_mat_write("sensor"))
            grid.addWidget(cell, axis + 1, column + 1)
        self.pos_sensor_device_combos.append(combo_row)
        self.pos_sensor_matrix_edits.append(edit_row)
    grid.setRowStretch(len(POS_AXES) + 1, 1)
    self.pos_sens_edits = self.pos_sensor_matrix_edits[0]
    self.pos_sens = MatrixEditor(6)
    self.pos_sensor_dev = SciEdit()
    self.pos_sens_axis = QtWidgets.QComboBox()
    for index, name in enumerate(POS_AXES):
        self.pos_sens_axis.addItem(name, index)
    _hide(self.pos_sens, self.pos_sensor_dev, self.pos_sens_axis)
    root.addWidget(group, 0, QtCore.Qt.AlignLeft)
    root.addStretch(1)
    return page


def _build_pos_motor_matrix_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    group = GroupPanel("Motor Matrix")
    group.setFixedSize(1545, 580)
    grid = QtWidgets.QGridLayout(group)
    grid.setContentsMargins(10, 18, 10, 10)
    grid.setSpacing(0)
    for column, name in enumerate(POS_OUTPUTS, 1):
        grid.addWidget(_header(name), 0, column)
    self.pos_motor_device_combos = []
    self.pos_motor_matrix_edits = []
    for axis, axis_name in enumerate(POS_AXES):
        grid.addWidget(QtWidgets.QLabel(axis_name), axis + 1, 0)
        combo_row = []
        edit_row = []
        for column in range(8):
            cell, combo, edit = _device_value_cell(
                IOSignalButton.OUTPUT_NAMES, "OutX1"
            )
            combo_row.append(combo)
            edit_row.append(edit)
            combo.currentIndexChanged.connect(
                lambda _index: self.on_pos_mat_write("motor")
            )
            edit.editingFinished.connect(lambda: self.on_pos_mat_write("motor"))
            grid.addWidget(cell, axis + 1, column + 1)
        self.pos_motor_device_combos.append(combo_row)
        self.pos_motor_matrix_edits.append(edit_row)
    grid.setRowStretch(len(POS_AXES) + 1, 1)
    self.pos_motor_edits = self.pos_motor_matrix_edits[0]
    self.pos_motor = MatrixEditor(8)
    self.pos_motor_dev = SciEdit()
    self.pos_motor_off = SciEdit()
    self.pos_motor_axis = QtWidgets.QComboBox()
    for index, name in enumerate(POS_AXES):
        self.pos_motor_axis.addItem(name, index)
    _hide(self.pos_motor, self.pos_motor_dev, self.pos_motor_off, self.pos_motor_axis)
    root.addWidget(group, 0, QtCore.Qt.AlignLeft)
    root.addStretch(1)
    return page


def on_pos_mat_read_reference(self, which: str) -> None:
    def work() -> None:
        session = self._require_session()
        rows = self.pos_sensor_matrix_edits if which == "sensor" else self.pos_motor_matrix_edits
        getter = session.get_position_sensor_matrix if which == "sensor" else session.get_position_motor_matrix
        device_rows = self.pos_sensor_device_combos if which == "sensor" else self.pos_motor_device_combos
        device_getter = (
            session.get_position_sensor_devices_for_axis
            if which == "sensor" else session.get_position_motor_devices_for_axis
        )
        for axis in range(6):
            for edit, value in zip(rows[axis], getter(axis)):
                edit.setText(f"{float(value):g}")
            raw_devices = list(device_getter(axis))
            # CGPSD/CGPMD transfers one IOType triple per matrix element:
            # Type, MainIndex, SubIndex.  Older UI code treated the flattened
            # response as one integer per combo, which shifted every device.
            stride = 3 if len(raw_devices) >= len(device_rows[axis]) * 3 else 1
            for slot, combo in enumerate(device_rows[axis]):
                value_index = slot * stride + (1 if stride == 3 else 0)
                if value_index >= len(raw_devices):
                    continue
                value = raw_devices[value_index]
                try:
                    index = int(value)
                except ValueError:
                    index = combo.findText(str(value))
                if 0 <= index < combo.count():
                    combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(False)
        self.log_msg(f"position {which} matrices read (all 6 axes)")
    self._run("Read position matrix", work)


def on_pos_mat_write_reference(self, which: str) -> None:
    def work() -> None:
        session = self._require_session()
        if not self._confirm_write(f"Write position {which} matrices for all 6 axes"):
            return
        assert self.gate
        rows = self.pos_sensor_matrix_edits if which == "sensor" else self.pos_motor_matrix_edits
        setter = session.set_position_sensor_matrix if which == "sensor" else session.set_position_motor_matrix
        device_rows = self.pos_sensor_device_combos if which == "sensor" else self.pos_motor_device_combos
        device_setter = (
            session.set_position_sensor_devices_for_axis
            if which == "sensor" else session.set_position_motor_devices_for_axis
        )
        self.gate.take_snapshot()
        self._set_writable(True)
        try:
            for axis in range(6):
                setter(axis, [float(edit.text()) for edit in rows[axis]])
                io_type = 0 if which == "sensor" else 1
                devices: list[int] = []
                for combo in device_rows[axis]:
                    devices.extend((io_type, combo.currentIndex(), 0))
                device_setter(axis, devices)
        finally:
            self._set_writable(True)
        self.log_msg(f"position {which} matrices written (all 6 axes)")
    self._run("Write position matrix", work)


# ---------------------------------------------------------------------------
# FF / PFF gains
# ---------------------------------------------------------------------------

def _gain_matrix(group: GroupPanel, row_names: list[str], cell_attr: str) -> dict[tuple[int, int], SciEdit]:
    grid = QtWidgets.QGridLayout(group)
    grid.setContentsMargins(10, 18, 10, 10)
    grid.setSpacing(0)
    for column in range(5):
        grid.addWidget(_header(f"Gain{column + 1}"), 0, column + 1)
    cells: dict[tuple[int, int], SciEdit] = {}
    for row, name in enumerate(row_names):
        grid.addWidget(QtWidgets.QLabel(name), row + 1, 0)
        for column in range(5):
            edit = _edit(str(row), 160, 35)
            cells[(row, column)] = edit
            grid.addWidget(edit, row + 1, column + 1)
    grid.setRowStretch(len(row_names) + 1, 1)
    return cells


def _channel_button(text: str, checked: bool = False) -> FlatPush:
    button = FlatPush(text)
    button.setCheckable(True)
    button.setChecked(checked)
    button.setFixedSize(160, 45)
    button.setStyleSheet(
        "QPushButton{background:#929292;color:#111;border:2px solid #999;"
        "font-size:22px;padding:2px;}QPushButton:checked{background:#a8ff26;}"
    )
    return button


def _build_ff_config_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    group = GroupPanel("FF Gains")
    group.setFixedSize(1160, 445)
    body = QtWidgets.QHBoxLayout(group)
    body.setContentsMargins(10, 18, 32, 10)

    matrix_group = GroupPanel("")
    matrix_group.setStyleSheet("QGroupBox{border:none;background:transparent;margin:0;padding:0;}")
    matrix_group.setFixedSize(900, 280)
    self.ff_gain_cells = _gain_matrix(matrix_group, AXES, "ff_gain_cells")
    for (axis, gain), editor in self.ff_gain_cells.items():
        editor.editingFinished.connect(
            lambda a=axis, g=gain: self._on_ff_gain_changed(a, g)
        )
    body.addWidget(matrix_group, 0, QtCore.Qt.AlignTop)

    channels = QtWidgets.QVBoxLayout()
    channels.addWidget(_header("Channel"))
    self.ff_source_buttons = []
    self._ff_selected_source = 0
    for source in range(7):
        button = _channel_button("X1FB", source == 0)
        button.clicked.connect(lambda _checked=False, s=source: self._on_ff_source_clicked(s))
        self.ff_source_buttons.append(button)
        channels.addWidget(button)
    channels.addStretch(1)
    body.addLayout(channels)
    root.addWidget(group, 0, QtCore.Qt.AlignLeft)

    self.ff_src_sig = SciEdit("InpXPOS")
    self.ff_src_num = QtWidgets.QComboBox()
    self.ff_src_num.addItems([f"Source{i + 1}" for i in range(7)])
    self.ff_mult_edits = {key: SciEdit("1") for key in ("XPos", "XAcc", "YPos", "YAcc")}
    self.ff_off_xpos = SciEdit("0")
    self.ff_off_ypos = SciEdit("0")
    self.ff_mul_xacc = SciEdit("0")
    self.ff_mul_yacc = SciEdit("0")
    _hide(self.ff_src_sig, self.ff_src_num, *self.ff_mult_edits.values(),
          self.ff_off_xpos, self.ff_off_ypos, self.ff_mul_xacc, self.ff_mul_yacc)
    root.addStretch(1)
    return page


def _build_pff_config_page_reference(self) -> QtWidgets.QWidget:
    page = QtWidgets.QWidget()
    page.setStyleSheet(REFERENCE_PAGE_STYLE)
    root = QtWidgets.QVBoxLayout(page)
    root.setContentsMargins(5, 4, 5, 4)
    group = GroupPanel("FF Gains")
    group.setFixedSize(1160, 345)
    body = QtWidgets.QHBoxLayout(group)
    body.setContentsMargins(10, 18, 32, 10)
    matrix_group = GroupPanel("")
    matrix_group.setStyleSheet("QGroupBox{border:none;background:transparent;margin:0;padding:0;}")
    matrix_group.setFixedSize(900, 180)
    pneu_axes = ["Ztpneu", "Yrpneu", "Xrpneu"]
    self.pff_gain_matrix = _gain_matrix(matrix_group, pneu_axes, "pff_gain_matrix")
    for (axis, gain), editor in self.pff_gain_matrix.items():
        editor.editingFinished.connect(
            lambda a=axis, g=gain: self._on_pff_gain_changed(a, g)
        )
    self.pff_gain_matrix_labels = []
    body.addWidget(matrix_group, 0, QtCore.Qt.AlignTop)

    channels = QtWidgets.QVBoxLayout()
    channels.addWidget(_header("Channel"))
    self.pff_source_btns = []
    for source in range(4):
        button = _channel_button("X1FB", source == 0)
        button.clicked.connect(lambda _checked=False, s=source: self._on_pff_source_clicked(s))
        self.pff_source_btns.append(button)
        channels.addWidget(button)
    channels.addStretch(1)
    body.addLayout(channels)
    root.addWidget(group, 0, QtCore.Qt.AlignLeft)

    self.pff_src_num = QtWidgets.QComboBox()
    self.pff_src_num.addItems([f"Source{i + 1}" for i in range(4)])
    self.pff_src_sig = SciEdit("InpXPOS")
    self.pff_off_xpos = SciEdit("0")
    self.pff_off_ypos = SciEdit("0")
    self.pff_mul_xacc = SciEdit("0")
    self.pff_mul_yacc = SciEdit("0")
    self.pff_off_xpos_cell = SciEdit("0")
    self.pff_off_ypos_cell = SciEdit("0")
    _hide(self.pff_src_num, self.pff_src_sig, self.pff_off_xpos, self.pff_off_ypos,
          self.pff_mul_xacc, self.pff_mul_yacc, self.pff_off_xpos_cell, self.pff_off_ypos_cell)
    root.addStretch(1)
    return page


def apply_patches(cls: type) -> None:
    cls._build_ad_da_mapping_page = _build_ad_da_mapping_page_reference
    cls._build_motor_protection_page = _build_motor_protection_page_reference
    cls._build_signal_display_page = _build_signal_display_page_reference
    cls._build_digio_tab = _build_digio_tab_reference
    cls._build_vel_matrix_page = _build_vel_matrix_page_reference
    cls._build_pos_sensor_matrix_page = _build_pos_sensor_matrix_page_reference
    cls._build_pos_motor_matrix_page = _build_pos_motor_matrix_page_reference
    cls._build_ff_config_page = _build_ff_config_page_reference
    cls._build_pff_config_page = _build_pff_config_page_reference
    cls.on_vel_mat_read_classic = on_vel_mat_read_reference
    cls.on_vel_mat_read = on_vel_mat_read_reference
    cls.on_vel_mat_write_classic = on_vel_mat_write_reference
    cls.on_vel_mat_write = on_vel_mat_write_reference
    cls._on_vel_matrix_axis_changed = _on_vel_matrix_axis_changed_reference
    cls.on_pos_mat_read = on_pos_mat_read_reference
    cls.on_pos_mat_write = on_pos_mat_write_reference
