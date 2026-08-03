"""Screenshot-accurate classic SAMBA_UI page layouts.

Layouts follow the native SAMBA_UI.exe DEMO MODE screenshots provided by the user.
RCI wiring hooks attach to MainWindow handlers where they already exist.
"""

from __future__ import annotations

from python_samba.protocol.commands import FilterStage
from python_samba.ui.classic_widgets import (
    ClassicFilterPanel,
    FilterStageBar,
    FilterStageCell,
    FlatPush,
    GroupPanel,
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

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required") from exc


# ---- named channel lists from DA/AD screenshot --------------------------------
AD_CH_LEFT = [
    "InpX1FB", "InpY1FB", "InpZ1FB", "InpX2FB", "InpZ2FB", "InpY3FB", "InpZ3FB",
    "InpXFF", "InpYFF", "InpZFF", "InpZ1Prox", "InpZ2Prox", "InpZ3Prox",
]
AD_CH_RIGHT = [
    "InpH1Prox", "InpH2Prox", "InpH3Prox", "InpXPOS", "InpXACC", "InpYPOS", "InpYACC",
    "InpY2FB", "InpX3FB", "InpX4FB", "InpY4FB", "InpZ4FB",
]
DA_CH_LEFT = [
    "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
]
DA_CH_RIGHT = [
    "OutV1", "OutV2", "OutV3", "OutV1H", "OutV2H", "OutV3H",
    "DiagInp", "DiagOutp",
]

VEL_SENSOR_NAMES = [
    "InpX1FB", "InpY1FB", "InpZ1FB", "InpX2FB", "InpZ2FB", "InpY3FB", "InpZ3FB",
]
VEL_MOTOR_NAMES = [
    "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
    "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
]
VEL_LOOP_NAMES = ["X trans", "Z rot", "Y trans", "Z trans", "Y rot", "X rot"]

POS_SENSOR_NAMES = [
    "InpZ1Prox (fixed)", "InpZ2Prox (fixed)", "InpZ3Prox (fixed)",
    "InpH1Prox (fixed)", "InpH2Prox (fixed)", "InpH3Prox (fixed)",
]
POS_MOTOR_NAMES = [
    "OutY1 (fixed)", "OutX2 (fixed)", "OutY3 (fixed)", "OutX4 (fixed)",
    "OutZ1 (fixed)", "OutZ2 (fixed)", "OutZ3 (fixed)", "OutZ4 (fixed)",
]
POS_LOOP_NAMES = ["XpRot", "YpRot", "XpTrans", "YpTrans", "ZpRot", "ZpTrans"]
POS_OFFSET_NAMES = [
    "InpZ1Prox", "InpZ2Prox", "InpZ3Prox", "InpH1Prox", "InpH2Prox", "InpH3Prox",
]


def _icon_button(text: str, color: str = "#404040", min_w: int = 72, min_h: int = 56) -> QtWidgets.QToolButton:
    btn = QtWidgets.QToolButton()
    btn.setText(text)
    btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
    btn.setMinimumSize(min_w, min_h)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"""
        QToolButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #ffffff, stop:1 #dfe7ed);
            border: 1px solid #8fa1b0;
            border-radius: 4px;
            color: {color};
            font-weight: 700;
            font-size: 11px;
            padding: 4px;
        }}
        QToolButton:hover {{ background: #eef6fc; border-color:#4f99d0; }}
        QToolButton:pressed {{ background: #d7e6f1; }}
        """
    )
    return btn


def _stage_box(label: str = "", width: int = 44, height: int = 52) -> QtWidgets.QFrame:
    """Blue-top filter stage cell like classic SAMBA."""
    box = QtWidgets.QFrame()
    box.setFixedSize(width, height)
    box.setStyleSheet(
        "QFrame {"
        "  background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        "    stop:0 #3569b9, stop:1 #214b91);"
        "  border:1px solid #7aa6d1;"
        "  border-radius:5px;"
        "}"
    )
    if label:
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(2, 10, 2, 2)
        lab = QtWidgets.QLabel(label)
        lab.setAlignment(QtCore.Qt.AlignCenter)
        lab.setStyleSheet(
            "border:none; background:transparent; color:#f4f8fc; "
            "font-weight:700; font-size:10px;"
        )
        lab.setWordWrap(True)
        lay.addWidget(lab)
    return box


def _named_spin_grid(
    names: list[str],
    cols: int = 1,
    default: str = "0.00000e+000",
    ch_mode: bool = False,
) -> tuple[QtWidgets.QWidget, list[QtWidgets.QWidget]]:
    """Build labeled edit grid. ch_mode=True → small channel spinboxes (0-99)."""
    w = QtWidgets.QWidget()
    grid = QtWidgets.QGridLayout(w)
    grid.setContentsMargins(4, 4, 4, 4)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(3)
    edits: list[QtWidgets.QWidget] = []
    for i, name in enumerate(names):
        r, c = divmod(i, cols) if cols > 1 else (i, 0)
        # in 2-col layout of screenshot AD: left col is names 0.., right is separate list
        lab = QtWidgets.QLabel(name + ":")
        lab.setMinimumWidth(90)
        if ch_mode:
            ed = QtWidgets.QSpinBox()
            ed.setRange(0, 99)
            ed.setValue(0)
            ed.setFixedWidth(48)
        else:
            ed = SciEdit(default)
            ed.setMinimumWidth(110)
        edits.append(ed)
        base_c = c * 2
        grid.addWidget(lab, r, base_c)
        grid.addWidget(ed, r, base_c + 1)
    return w, edits


class ClassicPagesMixin:
    """Screenshot-matched page builders. Mix into MainWindow."""

    # ------------------------------------------------------------------ Motor Overcurrent (screenshot 1)

    def _page_motor_protection(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Monitor
        g_mon = GroupPanel("Monitor")
        mf = QtWidgets.QFormLayout(g_mon)
        mf.setLabelAlignment(QtCore.Qt.AlignLeft)
        self.mot_signal = SciEdit("OutX1")
        self.mot_threshold = SciEdit("1.00000e+012")
        thr_row = QtWidgets.QHBoxLayout()
        thr_row.addWidget(self.mot_threshold)
        thr_row.addWidget(QtWidgets.QLabel("N"))
        self.btn_mot_thr_all = FlatPush("Update Threshold for all")
        thr_row.addWidget(self.btn_mot_thr_all)
        thr_row.addStretch(1)
        thr_w = QtWidgets.QWidget()
        thr_w.setLayout(thr_row)
        self.mot_status = SciEdit("Normal operation")
        self.mot_status.setReadOnly(True)
        # keep legacy plain text for handlers
        self.mot_cfg = QtWidgets.QPlainTextEdit()
        self.mot_cfg.setVisible(False)
        self.mot_power = QtWidgets.QPlainTextEdit()
        self.mot_power.setVisible(False)
        self.mot_fs = QtWidgets.QPlainTextEdit()
        self.mot_fs.setVisible(False)
        mf.addRow("Signal:", self.mot_signal)
        mf.addRow("Threshold:", thr_w)
        mf.addRow("Status:", self.mot_status)
        root.addWidget(g_mon)

        # Reset delay + Use temperature
        mid = QtWidgets.QGridLayout()
        mid.setHorizontalSpacing(24)
        mid.setVerticalSpacing(10)
        mid.addWidget(QtWidgets.QLabel("Reset delay time:"), 0, 0)
        self.mot_reset_delay = SciEdit("1.00000e+001")
        rd = QtWidgets.QHBoxLayout()
        rd.addWidget(self.mot_reset_delay)
        rd.addWidget(QtWidgets.QLabel("seconds"))
        rd.addStretch(1)
        rdw = QtWidgets.QWidget()
        rdw.setLayout(rd)
        mid.addWidget(rdw, 0, 1)

        mid.addWidget(QtWidgets.QLabel("Use temperature"), 0, 2, QtCore.Qt.AlignRight)
        self.rocker_use_temp = RockerButton("On", "Off")
        mid.addWidget(self.rocker_use_temp, 0, 3)

        mid.addWidget(QtWidgets.QLabel("Disable all when failure:"), 1, 0)
        self.rocker_disable_fail = RockerButton("On", "Off")
        self.rocker_disable_fail.setChecked(True)
        mid.addWidget(self.rocker_disable_fail, 1, 1, QtCore.Qt.AlignLeft)

        mid.addWidget(QtWidgets.QLabel("Motor overcurrent cooling"), 2, 0)
        self.mot_cooling = SciEdit("5.00000e-004")
        self.mot_cooling.setEnabled(False)
        mid.addWidget(self.mot_cooling, 2, 1)
        self.rocker_use_temp.toggled.connect(self.mot_cooling.setEnabled)

        root.addLayout(mid)

        cont = QtWidgets.QHBoxLayout()
        cont.addWidget(QtWidgets.QLabel("Continuous display:"))
        self.rocker_mot_cont = RockerButton("On", "Off")
        cont.addWidget(self.rocker_mot_cont)
        cont.addStretch(1)
        root.addLayout(cont)

        # action row (RCI)
        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write config...")
        btn_r.clicked.connect(self.on_motor_read)
        btn_w.clicked.connect(self.on_motor_write)
        self.btn_mot_thr_all.clicked.connect(self.on_motor_write)
        act.addWidget(btn_r)
        act.addWidget(btn_w)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    # ------------------------------------------------------------------ Setup/NVRAM (screenshot 2)

    def _page_nvram(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # NVRAM
        g_nv = GroupPanel("NVRAM")
        nl = QtWidgets.QHBoxLayout(g_nv)
        nl.addWidget(QtWidgets.QLabel("Write-Protection"))
        self.rocker_nv_protect = RockerButton("On", "Off")
        self.rocker_nv_protect.setChecked(True)
        nl.addWidget(self.rocker_nv_protect)
        nl.addSpacing(16)
        self.btn_nv_clear = _icon_button("Clear\nNV", color="#c00")
        self.btn_nv_load = _icon_button("Load\nNV ←")
        self.btn_nv_save = _icon_button("Save\n→ NV")
        self.btn_nv_clear.clicked.connect(lambda: self.on_nvram("clear"))
        self.btn_nv_load.clicked.connect(lambda: self.on_nvram("restore"))
        self.btn_nv_save.clicked.connect(lambda: self.on_nvram("save"))
        nl.addWidget(self.btn_nv_clear)
        nl.addWidget(self.btn_nv_load)
        nl.addWidget(self.btn_nv_save)
        nl.addStretch(1)
        root.addWidget(g_nv)

        # Setup from/to file + DAT convert
        row = QtWidgets.QHBoxLayout()
        g_file = GroupPanel("Setup (from/to file)")
        fl = QtWidgets.QHBoxLayout(g_file)
        self.btn_setup_load = _icon_button("Load\nfile → ctrl", min_w=90)
        self.btn_setup_save = _icon_button("Save\nctrl → file", min_w=90)
        self.btn_setup_load.clicked.connect(self.on_setup_load_file)
        self.btn_setup_save.clicked.connect(self.on_setup_save_file)
        fl.addWidget(self.btn_setup_load)
        fl.addWidget(self.btn_setup_save)
        fl.addStretch(1)
        row.addWidget(g_file, 1)

        g_conv = GroupPanel("Convert DAT file to XML Setup file")
        cl = QtWidgets.QHBoxLayout(g_conv)
        self.btn_dat_xml = FlatPush("DAT  →  XML")
        self.btn_dat_xml.setMinimumHeight(40)
        self.btn_dat_xml.setMinimumWidth(160)
        self.btn_dat_xml.setStyleSheet(
            self.btn_dat_xml.styleSheet()
            + "QPushButton { font-weight:700; font-size:13px; }"
        )
        self.btn_dat_xml.clicked.connect(self.on_dat_to_xml)
        cl.addStretch(1)
        cl.addWidget(self.btn_dat_xml)
        cl.addStretch(1)
        row.addWidget(g_conv, 1)
        root.addLayout(row)

        # hidden extras for RCI handlers
        self.nvram_fs = SciSpin()
        self.nvram_fs.setRange(1.0, 20000.0)
        self.nvram_fs.setValue(2000.0)
        self.nvram_fs.setVisible(False)
        self.nvram_cfg = SciEdit()
        self.nvram_cfg.setVisible(False)
        self.nvram_adcs = QtWidgets.QSpinBox()
        self.nvram_adcs.setVisible(False)
        self.setup_file_lbl = QtWidgets.QLabel("")
        self.setup_file_lbl.setVisible(False)

        root.addStretch(1)

        # Edit SI at bottom
        g_si = GroupPanel("Edit SI")
        sil = QtWidgets.QVBoxLayout(g_si)
        sil.addWidget(QtWidgets.QLabel("SI file used by"))
        self.si_file_lbl = SciEdit("")
        self.si_file_lbl.setReadOnly(True)
        sil.addWidget(self.si_file_lbl)
        brow = QtWidgets.QHBoxLayout()
        self.btn_si_sel = FlatPush("Select SI file")
        self.btn_si_start = FlatPush("Start EditSI")
        self.btn_si_sel.setMinimumWidth(140)
        self.btn_si_start.setMinimumWidth(140)
        self.btn_si_sel.clicked.connect(self.on_si_select)
        self.btn_si_start.clicked.connect(self.on_si_start)
        brow.addWidget(self.btn_si_sel)
        brow.addStretch(1)
        brow.addWidget(self.btn_si_start)
        sil.addLayout(brow)
        root.addWidget(g_si)
        return w

    def on_dat_to_xml(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select DAT setup file", "", "DAT files (*.dat);;All (*.*)"
        )
        if not path:
            return
        out, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save XML setup file", "setup.xml", "XML (*.xml)"
        )
        if out:
            self.log_msg(f"DAT→XML requested: {path} → {out}")
            QtWidgets.QMessageBox.information(
                self,
                "DAT → XML",
                "Path pair recorded. Full DAT converter is a later phase;\n"
                "use official SAMBA converter or XML setups for now.",
            )

    # ------------------------------------------------------------------ DA/AD (screenshot 3)

    def _page_dac_adc(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        top = QtWidgets.QGridLayout()
        top.addWidget(QtWidgets.QLabel("Controller"), 0, 0)
        self.dac_ctrl_type = SciEdit("OPTICON")
        self.dac_ctrl_type.setReadOnly(True)
        self.dac_ctrl_type.setMinimumWidth(180)
        top.addWidget(self.dac_ctrl_type, 0, 1)
        top.addWidget(QtWidgets.QLabel("Available Presets:"), 1, 0)
        self.dac_preset = QtWidgets.QComboBox()
        self.dac_preset.addItems(["OPTICON", "TCMFD", "MAXCON"])
        self.dac_preset.setMinimumWidth(180)
        top.addWidget(self.dac_preset, 1, 1)
        self.btn_dac_preset = FlatPush("Load Preset")
        self.btn_dac_preset.clicked.connect(self.on_dacadc_load_preset)
        top.addWidget(self.btn_dac_preset, 1, 2)
        top.setColumnStretch(3, 1)
        root.addLayout(top)

        mats = QtWidgets.QHBoxLayout()
        g_ad = GroupPanel("AD channels")
        adl = QtWidgets.QHBoxLayout(g_ad)
        # two columns like screenshot
        left_w, self.ad_ch_left = _named_spin_grid(AD_CH_LEFT, cols=1, ch_mode=True)
        right_w, self.ad_ch_right = _named_spin_grid(AD_CH_RIGHT, cols=1, ch_mode=True)
        # header row
        head_l = QtWidgets.QHBoxLayout()
        # rebuild with headers
        ad_grid = QtWidgets.QGridLayout()
        ad_grid.addWidget(QtWidgets.QLabel("Input"), 0, 0)
        ad_grid.addWidget(QtWidgets.QLabel("Ch"), 0, 1)
        ad_grid.addWidget(QtWidgets.QLabel("Input"), 0, 2)
        ad_grid.addWidget(QtWidgets.QLabel("Ch"), 0, 3)
        self.ad_edits: list[QtWidgets.QSpinBox] = []
        n = max(len(AD_CH_LEFT), len(AD_CH_RIGHT))
        for i in range(n):
            if i < len(AD_CH_LEFT):
                ad_grid.addWidget(QtWidgets.QLabel(AD_CH_LEFT[i] + ":"), i + 1, 0)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 99)
                sp.setFixedWidth(48)
                self.ad_edits.append(sp)
                ad_grid.addWidget(sp, i + 1, 1)
            if i < len(AD_CH_RIGHT):
                ad_grid.addWidget(QtWidgets.QLabel(AD_CH_RIGHT[i] + ":"), i + 1, 2)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 99)
                sp.setFixedWidth(48)
                self.ad_edits.append(sp)
                ad_grid.addWidget(sp, i + 1, 3)
        ad_box = QtWidgets.QWidget()
        ad_box.setLayout(ad_grid)
        adl.addWidget(ad_box)
        mats.addWidget(g_ad, 1)

        g_da = GroupPanel("DA channels")
        dal = QtWidgets.QVBoxLayout(g_da)
        da_grid = QtWidgets.QGridLayout()
        da_grid.addWidget(QtWidgets.QLabel("Output"), 0, 0)
        da_grid.addWidget(QtWidgets.QLabel("Ch"), 0, 1)
        da_grid.addWidget(QtWidgets.QLabel("Output"), 0, 2)
        da_grid.addWidget(QtWidgets.QLabel("Ch"), 0, 3)
        self.da_edits: list[QtWidgets.QSpinBox] = []
        n = max(len(DA_CH_LEFT), len(DA_CH_RIGHT))
        for i in range(n):
            if i < len(DA_CH_LEFT):
                da_grid.addWidget(QtWidgets.QLabel(DA_CH_LEFT[i] + ":"), i + 1, 0)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 99)
                sp.setFixedWidth(48)
                self.da_edits.append(sp)
                da_grid.addWidget(sp, i + 1, 1)
            if i < len(DA_CH_RIGHT):
                da_grid.addWidget(QtWidgets.QLabel(DA_CH_RIGHT[i] + ":"), i + 1, 2)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 99)
                sp.setFixedWidth(48)
                self.da_edits.append(sp)
                da_grid.addWidget(sp, i + 1, 3)
        da_box = QtWidgets.QWidget()
        da_box.setLayout(da_grid)
        dal.addWidget(da_box)
        mats.addWidget(g_da, 1)
        root.addLayout(mats)

        # hidden plain text for existing RCI handlers
        self.adc_seq = QtWidgets.QPlainTextEdit()
        self.adc_seq.setVisible(False)
        self.dac_seq = QtWidgets.QPlainTextEdit()
        self.dac_seq.setVisible(False)

        act = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read from controller")
        btn_wa = FlatPush("Write ADC...")
        btn_wd = FlatPush("Write DAC...")
        btn_r.clicked.connect(self.on_dacadc_read_classic)
        btn_wa.clicked.connect(self.on_dacadc_write_adc_classic)
        btn_wd.clicked.connect(self.on_dacadc_write_dac_classic)
        act.addWidget(btn_r)
        act.addWidget(btn_wa)
        act.addWidget(btn_wd)
        act.addStretch(1)
        root.addLayout(act)
        root.addStretch(1)
        return w

    def on_dacadc_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            adc = s.get_adc_sequence()
            dac = s.get_dac_sequence()
            self.adc_seq.setPlainText(" ".join(adc))
            self.dac_seq.setPlainText(" ".join(dac))
            for i, sp in enumerate(self.ad_edits):
                if i < len(adc):
                    try:
                        sp.setValue(int(float(adc[i])))
                    except Exception:
                        pass
            for i, sp in enumerate(self.da_edits):
                if i < len(dac):
                    try:
                        sp.setValue(int(float(dac[i])))
                    except Exception:
                        pass
            try:
                self.dac_ctrl_type.setText(" ".join(s.get_controller_type()))
            except Exception:
                pass
            self.log_msg("dac/adc read")

        self._run("DAC/ADC read", work)

    def on_dacadc_write_adc_classic(self) -> None:
        tokens = [str(sp.value()) for sp in self.ad_edits]
        self.adc_seq.setPlainText(" ".join(tokens))
        self.on_dacadc_write_adc()

    def on_dacadc_write_dac_classic(self) -> None:
        tokens = [str(sp.value()) for sp in self.da_edits]
        self.dac_seq.setPlainText(" ".join(tokens))
        self.on_dacadc_write_dac()

    def on_dacadc_load_preset(self) -> None:
        name = self.dac_preset.currentText()
        self.log_msg(f"DAC/ADC preset: {name}")
        # zero channels as safe default visual
        for sp in self.ad_edits + self.da_edits:
            sp.setValue(0)
        self.dac_ctrl_type.setText(name)

    # ------------------------------------------------------------------ Logging (screenshot 4)

    def _page_logging(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Save config to xml", self.on_setup_save_file),
            ("Load config from xml", self.on_setup_load_file),
            ("Define/Show Monitor Signals", self.on_logging_read),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)

        g_ev = GroupPanel("Internal event logging")
        ev = QtWidgets.QGridLayout(g_ev)
        ev.addWidget(QtWidgets.QLabel("Logging"), 0, 0)
        self.log_type = QtWidgets.QComboBox()
        self.log_type.addItems([
            "Overcurrent Event", "Switch Event", "Performance Event", "Custom",
        ])
        self.log_type.setMinimumWidth(140)
        ev.addWidget(self.log_type, 0, 1)
        ev.addWidget(QtWidgets.QLabel("Signals"), 0, 2)
        self.log_signals_n = QtWidgets.QSpinBox()
        self.log_signals_n.setRange(1, 64)
        self.log_signals_n.setValue(10)
        ev.addWidget(self.log_signals_n, 0, 3)
        self.btn_log_set = FlatPush("Set parameters")
        self.btn_log_get = FlatPush("Get parameters")
        self.btn_log_start = FlatPush("Start")
        self.btn_log_stop = FlatPush("Stop")
        self.btn_log_set.clicked.connect(self.on_logging_write_params)
        self.btn_log_get.clicked.connect(self.on_logging_read)
        self.btn_log_start.clicked.connect(lambda: self.on_logging_startstop(1))
        self.btn_log_stop.clicked.connect(lambda: self.on_logging_startstop(0))
        ev.addWidget(self.btn_log_set, 0, 4)
        ev.addWidget(self.btn_log_start, 0, 5)
        ev.addWidget(self.btn_log_get, 1, 4)
        ev.addWidget(self.btn_log_stop, 1, 5)

        ev.addWidget(QtWidgets.QLabel("Samples"), 1, 0)
        self.log_samples = SciEdit("1764564")
        ev.addWidget(self.log_samples, 1, 1)
        ev.addWidget(QtWidgets.QLabel("Delay"), 1, 2)
        self.log_delay = SciEdit("1")
        ev.addWidget(self.log_delay, 1, 3)

        ev.addWidget(QtWidgets.QLabel("Undersample"), 2, 0)
        self.log_undersample = SciEdit("1")
        us = QtWidgets.QHBoxLayout()
        us.addWidget(self.log_undersample)
        self.log_average = QtWidgets.QCheckBox("average")
        us.addWidget(self.log_average)
        us.addStretch(1)
        usw = QtWidgets.QWidget()
        usw.setLayout(us)
        ev.addWidget(usw, 2, 1, 1, 2)
        self.log_run_status = SciEdit("")
        self.log_run_status.setReadOnly(True)
        self.log_run_status.setPlaceholderText("status")
        ev.addWidget(self.log_run_status, 2, 4, 1, 2)

        strow = QtWidgets.QHBoxLayout()
        self.btn_log_status = FlatPush("Get status")
        self.btn_log_status.clicked.connect(self.on_logging_read)
        self.log_status_line = SciEdit("")
        self.log_status_line.setReadOnly(True)
        self.log_status_line.setPlaceholderText("status")
        strow.addWidget(self.btn_log_status)
        strow.addWidget(self.log_status_line, 1)
        ev.addLayout(strow, 3, 0, 1, 6)

        # Event signal sub-box
        g_es = GroupPanel("Event signal")
        es = QtWidgets.QHBoxLayout(g_es)
        es.addWidget(QtWidgets.QLabel("Signal"))
        self.log_event_sig_btn = FlatPush("X trans, raw input")
        self.log_event_sig_btn.setMinimumWidth(140)
        es.addWidget(self.log_event_sig_btn)
        es.addWidget(QtWidgets.QLabel("Threshold"))
        self.log_event_thr = SciEdit("5.00000e+003")
        es.addWidget(self.log_event_thr)
        es.addWidget(QtWidgets.QLabel("digits"))
        es.addWidget(QtWidgets.QLabel("Trigger"))
        self.log_event_trig = SciEdit("1")
        self.log_event_trig.setFixedWidth(60)
        es.addWidget(self.log_event_trig)
        es.addWidget(QtWidgets.QLabel("samples"))
        es.addStretch(1)
        ev.addWidget(g_es, 4, 0, 1, 6)

        # Save trace
        g_tr = GroupPanel("Save trace to file")
        tr = QtWidgets.QVBoxLayout(g_tr)
        tr1 = QtWidgets.QHBoxLayout()
        tr1.addWidget(QtWidgets.QLabel("Trace num:"))
        self.log_trace_num = QtWidgets.QSpinBox()
        self.log_trace_num.setRange(0, 100)
        tr1.addWidget(self.log_trace_num)
        self.btn_save_trace = FlatPush("Save trace")
        self.btn_save_trace.clicked.connect(self.on_logging_download)
        tr1.addWidget(self.btn_save_trace)
        tr1.addStretch(1)
        tr.addLayout(tr1)
        self.log_add_info = QtWidgets.QCheckBox(
            "Add additional info (sample frequency, signals number, undersample ... etc) to the file"
        )
        tr.addWidget(self.log_add_info)
        self.log_data = QtWidgets.QPlainTextEdit()
        self.log_data.setReadOnly(True)
        self.log_data.setFixedHeight(28)
        self.log_data.setPlaceholderText("")
        tr.addWidget(self.log_data)
        ev.addWidget(g_tr, 5, 0, 1, 6)
        root.addWidget(g_ev)

        # File tracing
        g_ft = GroupPanel("File tracing")
        ft = QtWidgets.QGridLayout(g_ft)
        ft.addWidget(QtWidgets.QLabel("Signals num:"), 0, 0)
        self.ft_signals = QtWidgets.QSpinBox()
        self.ft_signals.setRange(1, 64)
        self.ft_signals.setValue(11)
        ft.addWidget(self.ft_signals, 0, 1)
        ft.addWidget(QtWidgets.QLabel("Update"), 0, 2)
        self.ft_update = SciEdit("500")
        self.ft_update.setFixedWidth(70)
        ft.addWidget(self.ft_update, 0, 3)
        ft.addWidget(QtWidgets.QLabel("ms"), 0, 4)
        self.btn_ft_check = FlatPush("Check update rate")
        ft.addWidget(self.btn_ft_check, 0, 5)

        ft.addWidget(QtWidgets.QLabel("Delimiter:"), 1, 0)
        self.ft_delim = QtWidgets.QComboBox()
        self.ft_delim.addItems(["space", "comma", "tab", "semicolon"])
        ft.addWidget(self.ft_delim, 1, 1)
        ft.addWidget(QtWidgets.QLabel("Start after"), 1, 2)
        self.ft_start_after = SciEdit("0.00000e+000")
        ft.addWidget(self.ft_start_after, 1, 3)
        ft.addWidget(QtWidgets.QLabel("hours"), 1, 4)
        ft.addWidget(QtWidgets.QLabel("Trace duration:"), 1, 5)
        self.ft_duration = SciEdit("1.00000e+000")
        ft.addWidget(self.ft_duration, 1, 6)
        ft.addWidget(QtWidgets.QLabel("hours"), 1, 7)

        ft_btn = QtWidgets.QVBoxLayout()
        self.btn_ft_start = FlatPush("Start")
        self.btn_ft_stop = FlatPush("Stop")
        ft_btn.addWidget(self.btn_ft_start)
        ft_btn.addWidget(self.btn_ft_stop)
        ft.addLayout(ft_btn, 2, 0, 2, 1)
        self.ft_status1 = SciEdit("")
        self.ft_status1.setReadOnly(True)
        self.ft_status2 = SciEdit("")
        self.ft_status2.setReadOnly(True)
        ft.addWidget(self.ft_status1, 2, 1, 1, 7)
        ft.addWidget(self.ft_status2, 3, 1, 1, 7)
        self.ft_view = QtWidgets.QPlainTextEdit()
        self.ft_view.setReadOnly(True)
        self.ft_view.setMinimumHeight(80)
        ft.addWidget(self.ft_view, 4, 0, 1, 8)
        root.addWidget(g_ft, 1)

        # hidden legacy fields for handlers
        self.log_params = SciEdit()
        self.log_params.setVisible(False)
        self.log_info = SciEdit()
        self.log_info.setVisible(False)
        self.log_event = SciEdit()
        self.log_event.setVisible(False)
        self.log_mon_num = QtWidgets.QSpinBox()
        self.log_mon_num.setVisible(False)
        self.log_mon_sig = SciEdit()
        self.log_mon_sig.setVisible(False)
        self.log_live = SciEdit()
        self.log_live.setVisible(False)
        self.log_event_time = SciEdit()
        self.log_event_time.setVisible(False)
        self.analysis_params = SciEdit()
        self.analysis_params.setVisible(False)
        self.analysis_input = SciEdit()
        self.analysis_input.setVisible(False)
        self.analysis_out = SciEdit()
        self.analysis_out.setVisible(False)
        self.analysis_events = SciEdit()
        self.analysis_events.setVisible(False)
        return w

    # ------------------------------------------------------------------ Velocity loop (screenshot 5)

    def _page_velocity_loop(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # Axis specific
        g_ax = GroupPanel("Axis specific")
        ax = QtWidgets.QHBoxLayout(g_ax)
        col_for = QtWidgets.QVBoxLayout()
        col_for.addWidget(QtWidgets.QLabel("for"))
        self.vel_axis_name = SciEdit("X trans")
        self.vel_axis_name.setReadOnly(True)
        self.vel_axis_name.setFixedWidth(90)
        col_for.addWidget(self.vel_axis_name)
        self.vel_mat_axis = QtWidgets.QComboBox()
        for i, label in enumerate(VEL_AXIS_LABELS):
            self.vel_mat_axis.addItem(label, i)
        self.vel_mat_axis.currentIndexChanged.connect(self._on_vel_axis_changed)
        col_for.addWidget(self.vel_mat_axis)
        ax.addLayout(col_for)

        ax.addWidget(QtWidgets.QLabel("Filter:"))
        stage_labels = ["1st", "HOPT", "", "", "", "PID", ""]
        self.vel_stage_bar = FilterStageBar(7, stage_labels, cell_w=48, cell_h=56)
        self.vel_stage_boxes = self.vel_stage_bar.cells  # compat
        self.vel_stage_bar.stage_selected.connect(self._on_vel_stage_selected)
        ax.addWidget(self.vel_stage_bar)
        ax.addStretch(1)
        ax.addWidget(QtWidgets.QLabel("AxisOutput"))
        self.vel_axis_out = SciEdit("1.00000e+005")
        self.vel_axis_out.setFixedWidth(110)
        ax.addWidget(self.vel_axis_out)
        ax.addWidget(QtWidgets.QLabel("digits"))
        root.addWidget(g_ax)

        # FilterEditor kept for RCI handlers (axis/stage/type/params source of truth)
        self.vel_filter = FilterEditor(VEL_AXIS_LABELS, max_stage=6)
        self.vel_filter.setVisible(False)

        # Visible classic filter param panel — click a stage cell to edit
        mid_filt = QtWidgets.QHBoxLayout()
        self.vel_filter_panel = ClassicFilterPanel("Velocity filter (click a stage above)")
        self.vel_filter_panel.read_clicked.connect(self.on_vel_read_classic)
        self.vel_filter_panel.write_clicked.connect(self.on_vel_write_classic)
        self.vel_filter_panel.stage_changed.connect(self._sync_vel_panel_to_editor)
        mid_filt.addWidget(self.vel_filter_panel)

        # Sensor + Motor matrices with names
        mats = QtWidgets.QVBoxLayout()
        g_s = GroupPanel("Sensor Matrix")
        sg = QtWidgets.QGridLayout(g_s)
        self.vel_sens_edits: list[SciEdit] = []
        for i, name in enumerate(VEL_SENSOR_NAMES):
            sg.addWidget(QtWidgets.QLabel(name), i, 0)
            ed = SciEdit("0.00000e+000")
            if name == "InpX2FB":
                ed.setText("1.00000e+000")
            self.vel_sens_edits.append(ed)
            sg.addWidget(ed, i, 1)
        self.vel_sens = MatrixEditor(7)
        self.vel_sens.setVisible(False)
        mats.addWidget(g_s)

        g_m = GroupPanel("Motor Matrix")
        mg = QtWidgets.QGridLayout(g_m)
        self.vel_motor_edits: list[SciEdit] = []
        for i, name in enumerate(VEL_MOTOR_NAMES):
            col = 0 if i < 6 else 2
            row = i if i < 6 else i - 6
            mg.addWidget(QtWidgets.QLabel(name), row, col)
            default = "0.00000e+000"
            if name == "OutX2":
                default = "5.00000e-001"
            elif name == "OutX4":
                default = "-5.00000e-001"
            ed = SciEdit(default)
            self.vel_motor_edits.append(ed)
            mg.addWidget(ed, row, col + 1)
        self.vel_motor = MatrixEditor(12)
        self.vel_motor.setVisible(False)
        mats.addWidget(g_m)
        mid_filt.addLayout(mats, 1)
        root.addLayout(mid_filt)

        mrow = QtWidgets.QHBoxLayout()
        btn_mr = FlatPush("Read matrices")
        btn_mw = FlatPush("Write matrices...")
        btn_fr = FlatPush("Read all stages")
        btn_fw = FlatPush("Write filter...")
        btn_mr.clicked.connect(self.on_vel_mat_read_classic)
        btn_mw.clicked.connect(self.on_vel_mat_write_classic)
        btn_fr.clicked.connect(self.on_vel_read_all_stages)
        btn_fw.clicked.connect(self.on_vel_write_classic)
        for b in (btn_fr, btn_fw, btn_mr, btn_mw):
            mrow.addWidget(b)
        mrow.addStretch(1)
        root.addLayout(mrow)

        # default-select stage 0 so the panel is live on first open
        self.vel_stage_bar.set_current(0, emit=True)

        # Individual loop status + Overall + Loop switch
        bot = QtWidgets.QHBoxLayout()
        g_il = GroupPanel("Individual loop status")
        il = QtWidgets.QHBoxLayout(g_il)
        self.vel_loop_rockers: list[RockerButton] = []
        self.vel_loop_leds = []
        for name in VEL_LOOP_NAMES:
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            rk = RockerButton("On", "Off")
            self.vel_loop_rockers.append(rk)
            self.vel_loop_leds.append(rk)  # compatibility
            col.addWidget(rk, 0, QtCore.Qt.AlignHCenter)
            il.addLayout(col)
        bot.addWidget(g_il, 3)

        g_ov = GroupPanel("Overall\nActive:")
        ov = QtWidgets.QVBoxLayout(g_ov)
        self.rocker_vel_overall = RockerButton("On", "Off")
        self.led_vel_loop = LedIndicator()
        ov.addWidget(self.rocker_vel_overall, 0, QtCore.Qt.AlignHCenter)
        bot.addWidget(g_ov)

        g_sw = GroupPanel("Loop switch criterium")
        sw = QtWidgets.QHBoxLayout(g_sw)
        self.vel_sw_vel = RockerButton("On", "Off")
        self.vel_sw_pos = RockerButton("On", "Off")
        self.vel_sw_auto = RockerButton("On", "Off")
        self.vel_sw_auto.setChecked(True)
        self.led_sw_vel = LedIndicator()
        self.led_sw_vel.set_on(True)
        self.led_sw_pos = LedIndicator()
        self.led_sw_pos.set_color("#9ca3af")
        for led, rk, lab in (
            (self.led_sw_vel, self.vel_sw_vel, "Velocity"),
            (self.led_sw_pos, self.vel_sw_pos, "Position"),
            (None, self.vel_sw_auto, "Auto"),
        ):
            col = QtWidgets.QVBoxLayout()
            if led:
                col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(rk, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(QtWidgets.QLabel(lab), 0, QtCore.Qt.AlignHCenter)
            sw.addLayout(col)
        bot.addWidget(g_sw)
        root.addLayout(bot)

        # Excitation / Diagnostics
        ex = QtWidgets.QHBoxLayout()
        g_ex = GroupPanel("Excitation/Diagnostic")
        ef = QtWidgets.QFormLayout(g_ex)
        self.noise_inject = SciEdit("X trans, stage 6")
        self.noise_type = QtWidgets.QComboBox()
        self.noise_type.addItems(["No noise", "Random/White", "Sine", "Duty cycle", "Chirp sine"])
        self.noise_gain = SciEdit("1.00000e-001")
        self.noise_freq = SciEdit("9.00000e-001")
        self.noise_filt_usage = QtWidgets.QComboBox()
        self.noise_filt_usage.addItems(["F", "N"])
        self.noise_filt_usage.setVisible(False)
        self.noise_excit = SciEdit()
        self.noise_excit.setVisible(False)
        # map combo index to protocol value
        ef.addRow("Injection Point:", self.noise_inject)
        ef.addRow("Noise Type:", self.noise_type)
        gain_row = QtWidgets.QHBoxLayout()
        # noise_gain as SciEdit for classic look; handlers use .value() on spin — bridge
        self._noise_gain_spin = SciSpin()
        self._noise_gain_spin.setValue(0.1)
        self._noise_gain_spin.setVisible(False)
        gain_row.addWidget(self.noise_gain)
        gw = QtWidgets.QWidget()
        gw.setLayout(gain_row)
        ef.addRow("Gain:", gw)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self.noise_freq)
        fr.addWidget(QtWidgets.QLabel("Hz"))
        fr.addStretch(1)
        fw = QtWidgets.QWidget()
        fw.setLayout(fr)
        ef.addRow("Frequency:", fw)
        ex.addWidget(g_ex, 1)

        right = QtWidgets.QVBoxLayout()
        g_diag = GroupPanel("Diagnostics")
        df = QtWidgets.QFormLayout(g_diag)
        self.diag_0 = FlatPush("X trans, output")
        self.diag_1 = FlatPush("X trans, stage 6")
        self.diag_outputs = SciEdit()
        self.diag_outputs.setVisible(False)
        self.test_mode = SciEdit()
        self.test_mode.setVisible(False)
        self.dig_trace_info = SciEdit()
        self.dig_trace_info.setVisible(False)
        self.dig_trace_status = SciEdit()
        self.dig_trace_status.setVisible(False)
        self.dig_trace_buf = QtWidgets.QPlainTextEdit()
        self.dig_trace_buf.setVisible(False)
        df.addRow("Diag 0:", self.diag_0)
        df.addRow("Diag 1:", self.diag_1)
        right.addWidget(g_diag)

        g_wn = GroupPanel("White noise filter")
        wn = QtWidgets.QHBoxLayout(g_wn)
        self.rocker_wn = RockerButton("On", "Off")
        wn.addWidget(self.rocker_wn)
        for _ in range(4):
            wn.addWidget(_stage_box("", 36, 40))
        wn.addStretch(1)
        right.addWidget(g_wn)
        ex.addLayout(right, 1)
        root.addLayout(ex)

        drow = QtWidgets.QHBoxLayout()
        btn_dr = FlatPush("Read noise")
        btn_dw = FlatPush("Write noise...")
        btn_dr.clicked.connect(self.on_diag_read_classic)
        btn_dw.clicked.connect(self.on_diag_write_classic)
        drow.addWidget(btn_dr)
        drow.addWidget(btn_dw)
        drow.addStretch(1)
        root.addLayout(drow)

        # Tuning helping hand
        g_th = GroupPanel("Tuning helping hand")
        th = QtWidgets.QHBoxLayout(g_th)
        th.addWidget(QtWidgets.QLabel("Measurement"))
        self.vel_meas = QtWidgets.QComboBox()
        self.vel_meas.addItems(["Raw", "Filtered", "Control"])
        th.addWidget(self.vel_meas)
        self.vel_help_btns = []
        for name in VEL_LOOP_NAMES:
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            b = RockerButton("On", "Off")
            b.setFixedSize(40, 36)
            self.vel_help_btns.append(b)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
            th.addLayout(col)
        th.addStretch(1)
        root.addWidget(g_th)
        return w

    def _on_vel_axis_changed(self, idx: int) -> None:
        label = self.vel_mat_axis.itemText(idx)
        pretty = {
            0: "X trans", 1: "Z rot", 2: "Y trans", 3: "Z trans", 4: "Y rot", 5: "X rot",
        }
        self.vel_axis_name.setText(pretty.get(idx, label))
        self.vel_filter.axis.setCurrentIndex(idx)
        # re-read selected stage for the new axis when connected
        if getattr(self, "session", None) is not None:
            self.on_vel_read_classic()

    def _on_vel_stage_selected(self, stage: int) -> None:
        """User clicked a filter stage cell — open FilterDlg dialog."""
        self.vel_filter.stage.blockSignals(True)
        self.vel_filter.stage.setValue(int(stage))
        self.vel_filter.stage.blockSignals(False)
        self.vel_filter_panel.set_stage_index(stage)
        # sync axis from combo
        axis = int(self.vel_mat_axis.currentData()) if self.vel_mat_axis.currentData() is not None else 0
        self.vel_filter.axis.blockSignals(True)
        aidx = self.vel_filter.axis.findData(axis)
        if aidx >= 0:
            self.vel_filter.axis.setCurrentIndex(aidx)
        self.vel_filter.axis.blockSignals(False)

        # Read current filter if connected, then show dialog
        if getattr(self, "session", None) is not None:
            self.on_vel_read_classic()

        # Open FilterDlg dialog
        dlg = FilterDlg(VEL_AXIS_LABELS, max_stage=6, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Velocity Filter — Stage {stage}")
        # Build FilterStage from current filter editor
        fs = self.vel_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setEnabled(False)  # axis locked to current selection

        def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
            """Handle dialog update."""
            if not isinstance(new_stage, FilterStage):
                return
            # Push into hidden editor
            self.vel_filter.set_stage(new_stage)
            # Update panel
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self._update_vel_stage_caption_from_editor()
            # Write via safety path
            if all_axes:
                # Write to all axes
                for ax in range(6):
                    s = FilterStage(ax, new_stage.stage, new_stage.filter_type, new_stage.params)
                    self.vel_filter.axis.blockSignals(True)
                    aidx2 = self.vel_filter.axis.findData(ax)
                    if aidx2 >= 0:
                        self.vel_filter.axis.setCurrentIndex(aidx2)
                    self.vel_filter.axis.blockSignals(False)
                    self.vel_filter.set_stage(s)
                    self.on_vel_write()
            else:
                self.on_vel_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _sync_vel_panel_to_editor(self) -> None:
        """User edited type/params in the classic panel → push into FilterEditor."""
        self.vel_filter_panel.apply_to_filter_editor(self.vel_filter)
        self._update_vel_stage_caption_from_editor()

    def _update_vel_stage_caption_from_editor(self) -> None:
        stage = self.vel_filter.stage_index()
        try:
            name = self.vel_filter.ftype.currentText().split(None, 1)[-1]
        except Exception:
            name = ""
        self.vel_stage_bar.set_stage_info(stage, name)

    def on_vel_read_classic(self) -> None:
        """Read currently selected stage into panel + stage bar caption."""
        def work() -> None:
            s = self._require_session()
            axis = int(self.vel_mat_axis.currentData())
            stage_i = self.vel_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
            # keep FilterEditor in sync (handlers use it)
            self.vel_filter.axis.blockSignals(True)
            aidx = self.vel_filter.axis.findData(axis)
            if aidx >= 0:
                self.vel_filter.axis.setCurrentIndex(aidx)
            self.vel_filter.axis.blockSignals(False)
            self.vel_filter.stage.blockSignals(True)
            self.vel_filter.stage.setValue(stage_i)
            self.vel_filter.stage.blockSignals(False)

            fs = s.get_velocity_filter(axis, stage_i)
            self.vel_filter.set_stage(fs)
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self.vel_stage_bar.set_stage_info(stage_i, fs.type_name)
            self.vel_stage_bar.set_current(stage_i)
            self.log_msg(f"VGVFS axis={axis} stage={stage_i} type={fs.type_name}")

        self._run("Read velocity filter", work)

    def on_vel_write_classic(self) -> None:
        """Write panel values for the selected stage via existing safety path."""
        # push panel → editor first, then reuse on_vel_write
        stage_i = self.vel_stage_bar.current_stage()
        if stage_i < 0:
            stage_i = 0
        axis = int(self.vel_mat_axis.currentData()) if self.vel_mat_axis.currentData() is not None else 0
        self.vel_filter.axis.blockSignals(True)
        aidx = self.vel_filter.axis.findData(axis)
        if aidx >= 0:
            self.vel_filter.axis.setCurrentIndex(aidx)
        self.vel_filter.axis.blockSignals(False)
        self.vel_filter.stage.blockSignals(True)
        self.vel_filter.stage.setValue(stage_i)
        self.vel_filter.stage.blockSignals(False)
        self.vel_filter_panel.apply_to_filter_editor(self.vel_filter)
        self.on_vel_write()
        # refresh caption after write attempt
        self._update_vel_stage_caption_from_editor()

    def on_vel_read_all_stages(self) -> None:
        """Read all stages for current axis and paint captions on the stage bar."""
        def work() -> None:
            s = self._require_session()
            axis = int(self.vel_mat_axis.currentData())
            n = len(self.vel_stage_bar.cells)
            for st in range(n):
                try:
                    fs = s.get_velocity_filter(axis, st)
                    self.vel_stage_bar.set_stage_info(st, fs.type_name)
                except Exception as exc:
                    self.vel_stage_bar.set_stage_info(st, "?")
                    self.log_msg(f"stage {st} read failed: {exc}")
            # also load the currently selected stage into the panel
            stage_i = self.vel_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
                self.vel_stage_bar.set_current(0)
            fs = s.get_velocity_filter(axis, stage_i)
            self.vel_filter.set_stage(fs)
            self.vel_filter_panel.set_from_filter_editor(self.vel_filter)
            self.log_msg(f"velocity filters axis={axis} all stages read")

        self._run("Read all velocity stages", work)

    def on_vel_mat_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.vel_mat_axis.currentData())
            sens = s.get_velocity_sensor_matrix(axis)
            motor = s.get_velocity_motor_matrix(axis)
            self.vel_sens.set_values(sens)
            self.vel_motor.set_values(motor)
            for ed, v in zip(self.vel_sens_edits, sens):
                ed.setText(f"{float(v):.5e}")
            for ed, v in zip(self.vel_motor_edits, motor):
                ed.setText(f"{float(v):.5e}")
            self.log_msg(f"velocity matrices axis={axis} read")

        self._run("Read velocity matrices", work)

    def on_vel_mat_write_classic(self) -> None:
        sens = [float(ed.text()) for ed in self.vel_sens_edits]
        motor = [float(ed.text()) for ed in self.vel_motor_edits]
        self.vel_sens.set_values(sens)
        self.vel_motor.set_values(motor)
        self.on_vel_mat_write()

    def on_diag_read_classic(self) -> None:
        # bridge SciEdit gain/freq into spin for handler if needed
        try:
            self._noise_gain_spin.setValue(float(self.noise_gain.text()))
        except Exception:
            pass
        # temporarily expose .value for handler compatibility
        self.noise_gain_value_bridge()
        self.on_diag_read()

    def noise_gain_value_bridge(self) -> None:
        """Make noise_gain quack like QDoubleSpinBox for existing handlers."""
        if not hasattr(self.noise_gain, "value"):
            edit = self.noise_gain

            def value() -> float:
                try:
                    return float(edit.text())
                except Exception:
                    return 0.0

            def setValue(v: float) -> None:  # noqa: N802
                edit.setText(f"{float(v):.5e}")

            edit.value = value  # type: ignore[attr-defined]
            edit.setValue = setValue  # type: ignore[attr-defined]
        if not hasattr(self.noise_freq, "value"):
            edit = self.noise_freq

            def value() -> float:
                try:
                    return float(edit.text())
                except Exception:
                    return 0.0

            def setValue(v: float) -> None:  # noqa: N802
                edit.setText(f"{float(v):.5e}")

            edit.value = value  # type: ignore[attr-defined]
            edit.setValue = setValue  # type: ignore[attr-defined]
        # noise_type currentData: map combo text to int
        if isinstance(self.noise_type, QtWidgets.QComboBox) and self.noise_type.itemData(0) is None:
            mapping = {"No noise": 0, "Random/White": 1, "Sine": 2, "Duty cycle": 3, "Chirp sine": 4}
            for i in range(self.noise_type.count()):
                t = self.noise_type.itemText(i)
                self.noise_type.setItemData(i, mapping.get(t, i))

    def on_diag_write_classic(self) -> None:
        self.noise_gain_value_bridge()
        self.on_diag_write()

    # ------------------------------------------------------------------ Position loop (screenshot 6)

    def _page_position_loop(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        g_ax = GroupPanel("Axis specific")
        ax = QtWidgets.QHBoxLayout(g_ax)
        ax.addWidget(QtWidgets.QLabel("for"))
        self.pos_axis_name = SciEdit("XpRot")
        self.pos_axis_name.setReadOnly(True)
        self.pos_axis_name.setFixedWidth(90)
        ax.addWidget(self.pos_axis_name)
        self.pos_sens_axis = QtWidgets.QComboBox()
        self.pos_motor_axis = QtWidgets.QComboBox()
        for i, label in enumerate(POS_AXIS_LABELS):
            self.pos_sens_axis.addItem(label, i)
            self.pos_motor_axis.addItem(label, i)
        self.pos_sens_axis.currentIndexChanged.connect(self._on_pos_axis_changed)
        ax.addWidget(self.pos_sens_axis)
        ax.addWidget(QtWidgets.QLabel("Filter:"))
        self.pos_stage_bar = FilterStageBar(4, ["", "", "", ""], cell_w=48, cell_h=52)
        self.pos_stage_bar.stage_selected.connect(self._on_pos_stage_selected)
        ax.addWidget(self.pos_stage_bar)
        ax.addStretch(1)
        ax.addWidget(QtWidgets.QLabel("Overall Active:"))
        self.rocker_pos_overall = RockerButton("On", "Off")
        self.led_pos_loop = LedIndicator()
        ax.addWidget(self.rocker_pos_overall)
        root.addWidget(g_ax)

        self.pos_filter = FilterEditor(POS_AXIS_LABELS, max_stage=3)
        self.pos_filter.setVisible(False)

        mid_filt = QtWidgets.QHBoxLayout()
        self.pos_filter_panel = ClassicFilterPanel("Position filter (click a stage above)")
        self.pos_filter_panel.read_clicked.connect(self.on_pos_read_classic)
        self.pos_filter_panel.write_clicked.connect(self.on_pos_write_classic)
        self.pos_filter_panel.stage_changed.connect(self._sync_pos_panel_to_editor)
        mid_filt.addWidget(self.pos_filter_panel)

        mats = QtWidgets.QVBoxLayout()
        g_s = GroupPanel("Sensor Matrix")
        sg = QtWidgets.QGridLayout(g_s)
        self.pos_sens_edits = []
        for i, name in enumerate(POS_SENSOR_NAMES):
            sg.addWidget(QtWidgets.QLabel(name), i, 0)
            ed = SciEdit("0.00000e+000")
            self.pos_sens_edits.append(ed)
            sg.addWidget(ed, i, 1)
        self.pos_sens = MatrixEditor(6)
        self.pos_sens.setVisible(False)
        mats.addWidget(g_s)

        g_m = GroupPanel("Motor Matrix")
        mg = QtWidgets.QGridLayout(g_m)
        self.pos_motor_edits = []
        for i, name in enumerate(POS_MOTOR_NAMES):
            col = 0 if i < 4 else 2
            row = i if i < 4 else i - 4
            mg.addWidget(QtWidgets.QLabel(name), row, col)
            ed = SciEdit("0.00000e+000")
            self.pos_motor_edits.append(ed)
            mg.addWidget(ed, row, col + 1)
        self.pos_motor = MatrixEditor(8)
        self.pos_motor.setVisible(False)
        mats.addWidget(g_m)
        mid_filt.addLayout(mats, 1)
        root.addLayout(mid_filt)

        self.pos_stage_bar.set_current(0, emit=True)

        # Proximity offsets + loop switch
        mid = QtWidgets.QHBoxLayout()
        g_po = GroupPanel("Proximity offsets")
        po = QtWidgets.QGridLayout(g_po)
        self.prox_edits = []
        for i, name in enumerate(POS_OFFSET_NAMES):
            col = 0 if i < 3 else 2
            row = i if i < 3 else i - 3
            po.addWidget(QtWidgets.QLabel(name), row, col)
            ed = SciEdit("0.00000e+000")
            self.prox_edits.append(ed)
            po.addWidget(ed, row, col + 1)
            po.addWidget(QtWidgets.QLabel("µ"), row, col + 2)
        self.prox_off = MatrixEditor(6)
        self.prox_off.setVisible(False)
        brow = QtWidgets.QHBoxLayout()
        self.btn_pos_cauco = FlatPush("Use current signals as offset")
        self.btn_pos_cauco.clicked.connect(self.on_prox_cauco)
        brow.addWidget(self.btn_pos_cauco)
        self.rocker_pos_cont = RockerButton("On", "Off")
        self.rocker_pos_cont.setFixedSize(40, 36)
        brow.addWidget(self.rocker_pos_cont)
        brow.addWidget(QtWidgets.QLabel("Continuous"))
        brow.addStretch(1)
        po.addLayout(brow, 3, 0, 1, 6)
        mid.addWidget(g_po, 2)

        g_sw = GroupPanel("Loop switch criterium")
        sw = QtWidgets.QHBoxLayout(g_sw)
        self.pos_sw_vel = RockerButton("On", "Off")
        self.pos_sw_pos = RockerButton("On", "Off")
        self.pos_sw_auto = RockerButton("On", "Off")
        self.pos_sw_auto.setChecked(True)
        led_v = LedIndicator()
        led_v.set_on(True)
        led_p = LedIndicator()
        led_p.set_color("#9ca3af")
        for led, rk, lab in (
            (led_v, self.pos_sw_vel, "Velocity"),
            (led_p, self.pos_sw_pos, "Position"),
            (None, self.pos_sw_auto, "Auto"),
        ):
            col = QtWidgets.QVBoxLayout()
            if led:
                col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(rk, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(QtWidgets.QLabel(lab), 0, QtCore.Qt.AlignHCenter)
            sw.addLayout(col)
        mid.addWidget(g_sw)
        root.addLayout(mid)

        # devices hidden
        self.pos_sensor_dev = SciEdit()
        self.pos_sensor_dev.setVisible(False)
        self.pos_motor_dev = SciEdit()
        self.pos_motor_dev.setVisible(False)
        self.pos_motor_off = SciEdit()
        self.pos_motor_off.setVisible(False)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read filter", self.on_pos_read_classic),
            ("Write filter...", self.on_pos_write_classic),
            ("Read all stages", self.on_pos_read_all_stages),
            ("Read offsets", self.on_prox_read_classic),
            ("Write offsets...", self.on_prox_write_classic),
            ("Read matrices", self.on_pos_mat_read_classic),
            ("Write matrices...", self.on_pos_mat_write_classic),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        # Excitation (shared look)
        ex = QtWidgets.QHBoxLayout()
        g_ex = GroupPanel("Excitation/Diagnostic")
        ef = QtWidgets.QFormLayout(g_ex)
        # reuse velocity noise widgets if already created; else create
        if not hasattr(self, "noise_inject"):
            self.noise_inject = SciEdit("X trans, stage 6")
            self.noise_type = QtWidgets.QComboBox()
            self.noise_type.addItems(["No noise", "Random/White", "Sine", "Duty cycle", "Chirp sine"])
            self.noise_gain = SciEdit("1.00000e-001")
            self.noise_freq = SciEdit("9.00000e-001")
            self.noise_filt_usage = QtWidgets.QComboBox()
            self.noise_filt_usage.addItems(["F", "N"])
            self.noise_excit = SciEdit()
            self.diag_outputs = SciEdit()
            self.test_mode = SciEdit()
            self.dig_trace_info = SciEdit()
            self.dig_trace_status = SciEdit()
            self.dig_trace_buf = QtWidgets.QPlainTextEdit()
        ef.addRow("Injection Point:", self.noise_inject)
        ef.addRow("Noise Type:", self.noise_type)
        ef.addRow("Gain:", self.noise_gain)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self.noise_freq)
        fr.addWidget(QtWidgets.QLabel("Hz"))
        fr.addStretch(1)
        fw = QtWidgets.QWidget()
        fw.setLayout(fr)
        ef.addRow("Frequency:", fw)
        ex.addWidget(g_ex, 1)

        right = QtWidgets.QVBoxLayout()
        g_diag = GroupPanel("Diagnostics")
        df = QtWidgets.QFormLayout(g_diag)
        if not hasattr(self, "diag_0"):
            self.diag_0 = FlatPush("X trans, output")
            self.diag_1 = FlatPush("X trans, stage 6")
        df.addRow("Diag 0:", self.diag_0)
        df.addRow("Diag 1:", self.diag_1)
        right.addWidget(g_diag)
        g_wn = GroupPanel("White noise filter")
        wn = QtWidgets.QHBoxLayout(g_wn)
        if not hasattr(self, "rocker_wn"):
            self.rocker_wn = RockerButton("On", "Off")
        wn.addWidget(self.rocker_wn)
        for _ in range(4):
            wn.addWidget(_stage_box("", 36, 40))
        wn.addStretch(1)
        right.addWidget(g_wn)
        ex.addLayout(right, 1)
        root.addLayout(ex)

        g_th = GroupPanel("Tuning helping hand")
        th = QtWidgets.QHBoxLayout(g_th)
        th.addWidget(QtWidgets.QLabel("Measurement"))
        self.pos_meas = QtWidgets.QComboBox()
        self.pos_meas.addItems(["Raw", "Filtered", "Control"])
        th.addWidget(self.pos_meas)
        for name in POS_LOOP_NAMES:
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            b = RockerButton("On", "Off")
            b.setFixedSize(40, 36)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
            th.addLayout(col)
        th.addStretch(1)
        root.addWidget(g_th)
        return w

    def _on_pos_axis_changed(self, idx: int) -> None:
        pretty = {
            0: "XpRot", 1: "YpRot", 2: "Xtrans", 3: "Ytrans", 4: "ZpRot", 5: "Zptrans",
        }
        self.pos_axis_name.setText(pretty.get(idx, self.pos_sens_axis.itemText(idx)))
        self.pos_motor_axis.setCurrentIndex(idx)
        self.pos_filter.axis.setCurrentIndex(idx)
        if getattr(self, "session", None) is not None:
            self.on_pos_read_classic()

    def _on_pos_stage_selected(self, stage: int) -> None:
        self.pos_filter.stage.blockSignals(True)
        self.pos_filter.stage.setValue(int(stage))
        self.pos_filter.stage.blockSignals(False)
        self.pos_filter_panel.set_stage_index(stage)
        axis = int(self.pos_sens_axis.currentData()) if self.pos_sens_axis.currentData() is not None else 0
        self.pos_filter.axis.blockSignals(True)
        aidx = self.pos_filter.axis.findData(axis)
        if aidx >= 0:
            self.pos_filter.axis.setCurrentIndex(aidx)
        self.pos_filter.axis.blockSignals(False)
        if getattr(self, "session", None) is not None:
            self.on_pos_read_classic()

        # Open FilterDlg dialog
        dlg = FilterDlg(POS_AXIS_LABELS, max_stage=3, show_all_axes=True, parent=self)
        dlg.setWindowTitle(f"Position Filter — Stage {stage}")
        fs = self.pos_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.pos_filter.set_stage(new_stage)
            self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self._update_pos_stage_caption_from_editor()
            if all_axes:
                for ax in range(6):
                    s = FilterStage(ax, new_stage.stage, new_stage.filter_type, new_stage.params)
                    self.pos_filter.axis.blockSignals(True)
                    aidx2 = self.pos_filter.axis.findData(ax)
                    if aidx2 >= 0:
                        self.pos_filter.axis.setCurrentIndex(aidx2)
                    self.pos_filter.axis.blockSignals(False)
                    self.pos_filter.set_stage(s)
                    self.on_pos_write()
            else:
                self.on_pos_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _sync_pos_panel_to_editor(self) -> None:
        self.pos_filter_panel.apply_to_filter_editor(self.pos_filter)

    def _update_pos_stage_caption_from_editor(self) -> None:
        stage = self.pos_filter.stage_index()
        try:
            name = self.pos_filter.ftype.currentText().split(None, 1)[-1]
        except Exception:
            name = ""
        self.pos_stage_bar.set_stage_info(stage, name)

    def on_pos_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.pos_sens_axis.currentData())
            stage_i = self.pos_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
            self.pos_filter.axis.blockSignals(True)
            aidx = self.pos_filter.axis.findData(axis)
            if aidx >= 0:
                self.pos_filter.axis.setCurrentIndex(aidx)
            self.pos_filter.axis.blockSignals(False)
            self.pos_filter.stage.blockSignals(True)
            self.pos_filter.stage.setValue(stage_i)
            self.pos_filter.stage.blockSignals(False)

            fs = s.get_proximity_filter(axis, stage_i)
            self.pos_filter.set_stage(fs)
            self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self.pos_stage_bar.set_stage_info(stage_i, fs.type_name)
            self.pos_stage_bar.set_current(stage_i)
            self.log_msg(f"CGPFS axis={axis} stage={stage_i} type={fs.type_name}")

        self._run("Read proximity filter", work)

    def on_pos_write_classic(self) -> None:
        stage_i = self.pos_stage_bar.current_stage()
        if stage_i < 0:
            stage_i = 0
        axis = int(self.pos_sens_axis.currentData()) if self.pos_sens_axis.currentData() is not None else 0
        self.pos_filter.axis.blockSignals(True)
        aidx = self.pos_filter.axis.findData(axis)
        if aidx >= 0:
            self.pos_filter.axis.setCurrentIndex(aidx)
        self.pos_filter.axis.blockSignals(False)
        self.pos_filter.stage.blockSignals(True)
        self.pos_filter.stage.setValue(stage_i)
        self.pos_filter.stage.blockSignals(False)
        self.pos_filter_panel.apply_to_filter_editor(self.pos_filter)
        self.on_pos_write()
        self._update_pos_stage_caption_from_editor()

    def on_pos_read_all_stages(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.pos_sens_axis.currentData())
            n = len(self.pos_stage_bar.cells)
            for st in range(n):
                try:
                    fs = s.get_proximity_filter(axis, st)
                    self.pos_stage_bar.set_stage_info(st, fs.type_name)
                except Exception:
                    self.pos_stage_bar.set_stage_info(st, "?")
            stage_i = self.pos_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
                self.pos_stage_bar.set_current(0)
            fs = s.get_proximity_filter(axis, stage_i)
            self.pos_filter.set_stage(fs)
            self.pos_filter_panel.set_from_filter_editor(self.pos_filter)
            self.log_msg(f"position filters axis={axis} all stages read")

        self._run("Read all position stages", work)

    def on_prox_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            vals = s.get_proximity_offsets()
            self.prox_off.set_values(vals)
            for ed, v in zip(self.prox_edits, vals):
                ed.setText(f"{float(v):.5e}")
            self.log_msg("proximity offsets read")

        self._run("Read proximity", work)

    def on_prox_write_classic(self) -> None:
        vals = [float(ed.text()) for ed in self.prox_edits]
        self.prox_off.set_values(vals)
        self.on_prox_write()

    def on_pos_mat_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.pos_sens_axis.currentData())
            sens = s.get_position_sensor_matrix(axis)
            motor = s.get_position_motor_matrix(axis)
            self.pos_sens.set_values(sens)
            self.pos_motor.set_values(motor)
            for ed, v in zip(self.pos_sens_edits, sens):
                ed.setText(f"{float(v):.5e}")
            for ed, v in zip(self.pos_motor_edits, motor):
                ed.setText(f"{float(v):.5e}")
            self.log_msg(f"position matrices axis={axis}")

        self._run("Read pos matrices", work)

    def on_pos_mat_write_classic(self) -> None:
        self.pos_sens.set_values([float(ed.text()) for ed in self.pos_sens_edits])
        self.pos_motor.set_values([float(ed.text()) for ed in self.pos_motor_edits])
        self.on_pos_mat_write("sensor")
        self.on_pos_mat_write("motor")

    # ------------------------------------------------------------------ Feed-Forward (screenshot 7)

    def _page_ff(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # top status row
        top = QtWidgets.QHBoxLayout()
        self.rocker_ff_active = RockerButton("On", "Off")
        self.rocker_ff_active.setChecked(True)
        self.rocker_ff_adapt = RockerButton("On", "Off")
        self.rocker_ff_adapt.setChecked(True)
        col1 = QtWidgets.QVBoxLayout()
        col1.addWidget(self.rocker_ff_active, 0, QtCore.Qt.AlignHCenter)
        col1.addWidget(QtWidgets.QLabel("FF active"), 0, QtCore.Qt.AlignHCenter)
        col2 = QtWidgets.QVBoxLayout()
        col2.addWidget(self.rocker_ff_adapt, 0, QtCore.Qt.AlignHCenter)
        col2.addWidget(QtWidgets.QLabel("Adaptive"), 0, QtCore.Qt.AlignHCenter)
        top.addLayout(col1)
        top.addLayout(col2)
        top.addWidget(QtWidgets.QLabel("Error signal"))
        self.ff_err_fb = FlatPush("FB")
        self.ff_err_raw = FlatPush("Raw")
        top.addWidget(self.ff_err_fb)
        top.addWidget(self.ff_err_raw)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Threshold (%):"))
        self.ff_threshold = SciEdit("62")
        self.ff_threshold.setFixedWidth(50)
        top.addWidget(self.ff_threshold)
        self.ff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ff_thr_slider.setRange(0, 100)
        self.ff_thr_slider.setValue(62)
        self.ff_thr_slider.setFixedWidth(120)
        top.addWidget(self.ff_thr_slider)
        top.addWidget(QtWidgets.QLabel("Used gains"))
        self.ff_used_gains = SciEdit("5")
        self.ff_used_gains.setFixedWidth(40)
        top.addWidget(self.ff_used_gains)
        self.ff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ff_gains_slider.setRange(1, 16)
        self.ff_gains_slider.setValue(5)
        self.ff_gains_slider.setFixedWidth(100)
        top.addWidget(self.ff_gains_slider)
        top.addStretch(1)
        root.addLayout(top)

        # Source + Filter stages + Error/Output Axis
        g_main = GroupPanel("")
        main = QtWidgets.QGridLayout(g_main)
        main.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        self.ff_source_name = SciEdit("InpXPOS")
        self.ff_source_name.setFixedWidth(100)
        main.addWidget(self.ff_source_name, 1, 0)
        self.ff_src = QtWidgets.QSpinBox()
        self.ff_src.setRange(0, 7)
        self.ff_src.setVisible(False)

        main.addWidget(QtWidgets.QLabel("Filter"), 0, 1)
        stage_labs = ["VLoop", "Stretch", "1st", "VLoop", "", "VLoop", "VLoop", "Stretch"]
        self.ff_stage_bar = FilterStageBar(8, stage_labs, cell_w=48, cell_h=52)
        self.ff_stage_bar.stage_selected.connect(self._on_ff_stage_selected)
        main.addWidget(self.ff_stage_bar, 1, 1, 1, 4)

        main.addWidget(QtWidgets.QLabel("Error/Output Axis"), 0, 5)
        self.ff_err_axis = SciEdit("X trans")
        self.ff_err_axis.setFixedWidth(90)
        main.addWidget(self.ff_err_axis, 1, 5)

        main.addWidget(QtWidgets.QLabel("FF rate:"), 2, 0)
        self.ff_rate = SciEdit("0.00000e+000")
        main.addWidget(self.ff_rate, 2, 1)
        main.addWidget(QtWidgets.QLabel("Gains:"), 3, 0)
        gains_row = QtWidgets.QHBoxLayout()
        self.ff_gain_edits = []
        for _ in range(5):
            ed = SciEdit("0.000e+000")
            ed.setFixedWidth(90)
            self.ff_gain_edits.append(ed)
            gains_row.addWidget(ed)
        gains_row.addStretch(1)
        gw = QtWidgets.QWidget()
        gw.setLayout(gains_row)
        main.addWidget(gw, 3, 1, 1, 4)

        # Matrix configuration checkboxes
        main.addWidget(QtWidgets.QLabel("Matrix configuration"), 4, 0)
        mc = QtWidgets.QHBoxLayout()
        self.ff_matrix_checks = []
        for name in VEL_LOOP_NAMES:
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            cb = QtWidgets.QCheckBox()
            self.ff_matrix_checks.append(cb)
            col.addWidget(cb, 0, QtCore.Qt.AlignHCenter)
            mc.addLayout(col)
        mc.addStretch(1)
        mcw = QtWidgets.QWidget()
        mcw.setLayout(mc)
        main.addWidget(mcw, 4, 1, 1, 4)

        # Reset buttons
        rst = QtWidgets.QVBoxLayout()
        for text, slot in (
            ("Reset this", self.on_ff_reset),
            ("Reset all", self.on_ff_reset),
            ("Reset all, this axis", self.on_ff_reset),
            ("Reset all, this source", self.on_ff_reset),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            rst.addWidget(b)
        rst.addStretch(1)
        rw = QtWidgets.QWidget()
        rw.setLayout(rst)
        main.addWidget(rw, 2, 5, 3, 1)
        root.addWidget(g_main)

        # Source definition + Stage multipliers
        mid = QtWidgets.QHBoxLayout()
        g_sd = GroupPanel("Source definition")
        sd = QtWidgets.QFormLayout(g_sd)
        self.ff_src_num = QtWidgets.QComboBox()
        self.ff_src_num.addItems([f"Source{i}" for i in range(1, 9)])
        self.ff_src_sig = SciEdit("InpXPOS")
        sd.addRow("Source number", self.ff_src_num)
        sd.addRow("Source signal", self.ff_src_sig)
        mid.addWidget(g_sd)

        g_sm = GroupPanel("Stage Feedforward Signal Multipliers")
        sm = QtWidgets.QGridLayout(g_sm)
        self.ff_mult_edits = {}
        for r, (a, b) in enumerate((("XPos", "XAcc"), ("YPos", "YAcc"))):
            sm.addWidget(QtWidgets.QLabel(a + ":"), r, 0)
            ea = SciEdit("1.00000e+000")
            self.ff_mult_edits[a] = ea
            sm.addWidget(ea, r, 1)
            sm.addWidget(QtWidgets.QLabel(b + ":"), r, 2)
            eb = SciEdit("1.00000e+000")
            self.ff_mult_edits[b] = eb
            sm.addWidget(eb, r, 3)
        mid.addWidget(g_sm)
        root.addLayout(mid)

        bot = QtWidgets.QHBoxLayout()
        g_off = GroupPanel("Offsets")
        of = QtWidgets.QFormLayout(g_off)
        self.ff_off_xpos = SciEdit("0.00000e+000")
        self.ff_off_ypos = SciEdit("0.00000e+000")
        of.addRow("XPos:", self.ff_off_xpos)
        of.addRow("YPos:", self.ff_off_ypos)
        bot.addWidget(g_off)
        g_mu = GroupPanel("Multipliers")
        mu = QtWidgets.QFormLayout(g_mu)
        self.ff_mul_xacc = SciEdit("0.00000e+000")
        self.ff_mul_yacc = SciEdit("0.00000e+000")
        mu.addRow("Xacc:", self.ff_mul_xacc)
        mu.addRow("Yacc:", self.ff_mul_yacc)
        bot.addWidget(g_mu)
        bot.addStretch(1)
        root.addLayout(bot)

        # legacy fields for handlers
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
        self.ff_filter = FilterEditor([f"src {i}" for i in range(7)], max_stage=7)
        self.ff_filter.setVisible(False)

        self.ff_filter_panel = ClassicFilterPanel("FF filter (click a stage above)")
        self.ff_filter_panel.read_clicked.connect(self.on_ff_filter_read_classic)
        self.ff_filter_panel.write_clicked.connect(self.on_ff_filter_write_classic)
        self.ff_filter_panel.stage_changed.connect(self._sync_ff_panel_to_editor)
        root.addWidget(self.ff_filter_panel)
        self.ff_stage_bar.set_current(0, emit=True)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read status/config", self.on_ff_status_read_classic),
            ("Write config...", self.on_ff_write_cfg),
            ("Write gains...", self.on_ff_write_gains_classic),
            ("Write mult...", self.on_ff_write_mult),
            ("Write inputs...", self.on_ff_write_inputs),
            ("Read filter", self.on_ff_filter_read_classic),
            ("Write filter...", self.on_ff_filter_write_classic),
            ("Read all stages", self.on_ff_read_all_stages),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        cont = QtWidgets.QHBoxLayout()
        cont.addStretch(1)
        cont.addWidget(QtWidgets.QLabel("Continuous"))
        self.rocker_ff_cont = RockerButton("On", "Off")
        self.rocker_ff_cont.setFixedSize(40, 36)
        cont.addWidget(self.rocker_ff_cont)
        root.addLayout(act)
        root.addLayout(cont)
        return w

    def _on_ff_stage_selected(self, stage: int) -> None:
        self.ff_filter.stage.blockSignals(True)
        self.ff_filter.stage.setValue(int(stage))
        self.ff_filter.stage.blockSignals(False)
        self.ff_filter_panel.set_stage_index(stage)
        if getattr(self, "session", None) is not None:
            self.on_ff_filter_read_classic()

        # Open FilterDlg for FF (7 sources, 8 stages)
        dlg = FilterDlg(
            [f"src {i}" for i in range(7)], max_stage=7,
            show_all_axes=True, show_all_sources=True, parent=self
        )
        dlg.setWindowTitle(f"FF Filter — Stage {stage}")
        fs = self.ff_filter.to_stage()
        dlg.set_stage(fs)
        dlg.axis_cbx.setEnabled(False)

        def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.ff_filter.set_stage(new_stage)
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            try:
                name = self.ff_filter.ftype.currentText().split(None, 1)[-1]
            except Exception:
                name = ""
            self.ff_stage_bar.set_stage_info(stage, name)
            self.ff_stage_bar.set_current(stage)
            if all_axes:
                for src in range(7):
                    s = FilterStage(src, stage, new_stage.filter_type, new_stage.params)
                    self.ff_filter.axis.blockSignals(True)
                    aidx2 = self.ff_filter.axis.findData(src)
                    if aidx2 >= 0:
                        self.ff_filter.axis.setCurrentIndex(aidx2)
                    self.ff_filter.axis.blockSignals(False)
                    self.ff_filter.set_stage(s)
                    self.on_ff_filter_write()
            elif all_sources:
                for s_stage in range(8):
                    s = FilterStage(new_stage.axis, s_stage, new_stage.filter_type, new_stage.params)
                    self.ff_filter.stage.setValue(s_stage)
                    self.ff_filter.set_stage(s)
                    self.on_ff_filter_write()
                self.ff_filter.stage.setValue(stage)
            else:
                self.on_ff_filter_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _sync_ff_panel_to_editor(self) -> None:
        self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)

    def on_ff_filter_read_classic(self) -> None:
        def work() -> None:
            s = self._require_session()
            src = int(self.ff_src.value())
            stage_i = self.ff_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
            # FilterEditor axis is source index for FF
            self.ff_filter.axis.blockSignals(True)
            aidx = self.ff_filter.axis.findData(src)
            if aidx < 0:
                # labels are "src N" with data=N from construction
                self.ff_filter.axis.setCurrentIndex(min(src, self.ff_filter.axis.count() - 1))
            else:
                self.ff_filter.axis.setCurrentIndex(aidx)
            self.ff_filter.axis.blockSignals(False)
            self.ff_filter.stage.blockSignals(True)
            self.ff_filter.stage.setValue(stage_i)
            self.ff_filter.stage.blockSignals(False)
            # reuse main handler path which uses ff_filter
            self.on_ff_filter_read()
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            try:
                name = self.ff_filter.ftype.currentText().split(None, 1)[-1]
            except Exception:
                name = ""
            self.ff_stage_bar.set_stage_info(stage_i, name)
            self.ff_stage_bar.set_current(stage_i)

        self._run("Read FF filter", work)

    def on_ff_filter_write_classic(self) -> None:
        stage_i = self.ff_stage_bar.current_stage()
        if stage_i < 0:
            stage_i = 0
        self.ff_filter.stage.blockSignals(True)
        self.ff_filter.stage.setValue(stage_i)
        self.ff_filter.stage.blockSignals(False)
        self.ff_filter_panel.apply_to_filter_editor(self.ff_filter)
        self.on_ff_filter_write()

    def on_ff_read_all_stages(self) -> None:
        def work() -> None:
            # walk stages via FilterEditor + existing read
            n = len(self.ff_stage_bar.cells)
            for st in range(n):
                self.ff_filter.stage.blockSignals(True)
                self.ff_filter.stage.setValue(st)
                self.ff_filter.stage.blockSignals(False)
                try:
                    self.on_ff_filter_read()
                    name = self.ff_filter.ftype.currentText().split(None, 1)[-1]
                    self.ff_stage_bar.set_stage_info(st, name)
                except Exception:
                    self.ff_stage_bar.set_stage_info(st, "?")
            stage_i = self.ff_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
            self.ff_filter.stage.setValue(stage_i)
            self.on_ff_filter_read()
            self.ff_filter_panel.set_from_filter_editor(self.ff_filter)
            self.log_msg("FF all stages read")

        self._run("Read all FF stages", work)

    def on_ff_status_read_classic(self) -> None:
        def work() -> None:
            self.on_ff_status_read()
            # mirror gains text into gain edits
            parts = self.ff_gains.text().split()
            for ed, p in zip(self.ff_gain_edits, parts):
                try:
                    ed.setText(f"{float(p):.5e}")
                except Exception:
                    ed.setText(p)

        self._run("FF read", work)

    def on_ff_write_gains_classic(self) -> None:
        self.ff_gains.setText(" ".join(ed.text() for ed in self.ff_gain_edits))
        self.on_ff_write_gains()

    # ------------------------------------------------------------------ Pneumatic SFF (screenshot 8)

    def _page_pff(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.rocker_pff_active = RockerButton("On", "Off")
        self.rocker_pff_active.setChecked(True)
        self.rocker_pff_adapt = RockerButton("On", "Off")
        self.rocker_pff_adapt.setChecked(True)
        c1 = QtWidgets.QVBoxLayout()
        c1.addWidget(self.rocker_pff_active, 0, QtCore.Qt.AlignHCenter)
        c1.addWidget(QtWidgets.QLabel("Pneum. FF active"), 0, QtCore.Qt.AlignHCenter)
        c2 = QtWidgets.QVBoxLayout()
        c2.addWidget(self.rocker_pff_adapt, 0, QtCore.Qt.AlignHCenter)
        c2.addWidget(QtWidgets.QLabel("Pneum FF adaptive"), 0, QtCore.Qt.AlignHCenter)
        top.addLayout(c1)
        top.addLayout(c2)
        top.addSpacing(20)
        top.addWidget(QtWidgets.QLabel("Threshold (%):"))
        self.pff_threshold = SciEdit("62")
        self.pff_threshold.setFixedWidth(50)
        top.addWidget(self.pff_threshold)
        self.pff_thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pff_thr_slider.setRange(0, 100)
        self.pff_thr_slider.setValue(62)
        self.pff_thr_slider.setFixedWidth(120)
        top.addWidget(self.pff_thr_slider)
        top.addWidget(QtWidgets.QLabel("Used gains"))
        self.pff_used_gains = SciEdit("5")
        self.pff_used_gains.setFixedWidth(40)
        top.addWidget(self.pff_used_gains)
        self.pff_gains_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pff_gains_slider.setRange(1, 16)
        self.pff_gains_slider.setValue(5)
        self.pff_gains_slider.setFixedWidth(100)
        top.addWidget(self.pff_gains_slider)
        top.addStretch(1)
        root.addLayout(top)

        g_main = GroupPanel("")
        main = QtWidgets.QGridLayout(g_main)
        main.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        self.pff_source_name = SciEdit("InpXPOS")
        self.pff_source_name.setFixedWidth(100)
        main.addWidget(self.pff_source_name, 1, 0)
        main.addWidget(QtWidgets.QLabel("Filter"), 0, 1)
        self.pff_stage_bar = FilterStageBar(8, [""] * 8, cell_w=44, cell_h=48)
        self.pff_stage_bar.stage_selected.connect(self._on_pff_stage_selected)
        main.addWidget(self.pff_stage_bar, 1, 1, 1, 3)
        main.addWidget(QtWidgets.QLabel("Error/Output Axis"), 0, 4)
        self.pff_err_axis = SciEdit("ZtPneu")
        self.pff_err_axis.setFixedWidth(90)
        main.addWidget(self.pff_err_axis, 1, 4)

        main.addWidget(QtWidgets.QLabel("FF rate:"), 2, 0)
        self.pff_rate = SciEdit("0.00000e+000")
        main.addWidget(self.pff_rate, 2, 1)
        main.addWidget(QtWidgets.QLabel("Gains:"), 3, 0)
        gr = QtWidgets.QHBoxLayout()
        self.pff_gain_edits = []
        for _ in range(5):
            ed = SciEdit("0.000e+000")
            ed.setFixedWidth(90)
            self.pff_gain_edits.append(ed)
            gr.addWidget(ed)
        gr.addStretch(1)
        gw = QtWidgets.QWidget()
        gw.setLayout(gr)
        main.addWidget(gw, 3, 1, 1, 3)

        main.addWidget(QtWidgets.QLabel("Matrix configuration"), 4, 0)
        mc = QtWidgets.QHBoxLayout()
        self.pff_matrix_checks = []
        for name in ("ZtPneu", "YrPneu", "XrPneu"):
            col = QtWidgets.QVBoxLayout()
            col.addWidget(QtWidgets.QLabel(name), 0, QtCore.Qt.AlignHCenter)
            cb = QtWidgets.QCheckBox()
            self.pff_matrix_checks.append(cb)
            col.addWidget(cb, 0, QtCore.Qt.AlignHCenter)
            mc.addLayout(col)
        mc.addStretch(1)
        mcw = QtWidgets.QWidget()
        mcw.setLayout(mc)
        main.addWidget(mcw, 4, 1, 1, 3)

        rst = QtWidgets.QVBoxLayout()
        for text in ("Reset this", "Reset all", "Reset all, this axis", "Reset all, this source"):
            b = FlatPush(text)
            b.clicked.connect(self.on_pff_reset)
            rst.addWidget(b)
        rst.addStretch(1)
        rw = QtWidgets.QWidget()
        rw.setLayout(rst)
        main.addWidget(rw, 2, 4, 3, 1)
        root.addWidget(g_main)

        g_sd = GroupPanel("Source definition")
        sd = QtWidgets.QFormLayout(g_sd)
        self.pff_src_num = QtWidgets.QComboBox()
        self.pff_src_num.addItems([f"Source{i}" for i in range(0, 8)])
        self.pff_src_sig = SciEdit("InpXPOS")
        sd.addRow("Source number", self.pff_src_num)
        sd.addRow("Source signal", self.pff_src_sig)
        root.addWidget(g_sd)

        # legacy
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
        self.pff_filter = FilterEditor(["axis via spin"], max_stage=7)
        self.pff_filter.setVisible(False)
        self.pff_filter.axis.setEnabled(False)

        self.pff_filter_panel = ClassicFilterPanel("PFF filter (click a stage above)")
        self.pff_filter_panel.read_clicked.connect(self.on_pff_filter_read_classic)
        self.pff_filter_panel.write_clicked.connect(self.on_pff_filter_write_classic)
        self.pff_filter_panel.stage_changed.connect(self._sync_pff_panel_to_editor)
        root.addWidget(self.pff_filter_panel)
        self.pff_stage_bar.set_current(0, emit=True)

        act = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all", self.on_pff_read_classic),
            ("Write config...", self.on_pff_write_cfg),
            ("Write gains...", self.on_pff_write_gains_classic),
            ("Write params...", self.on_pff_write_params),
            ("Write inputs...", self.on_pff_write_inputs),
            ("Read filter", self.on_pff_filter_read_classic),
            ("Write filter...", self.on_pff_filter_write_classic),
            ("Reset FIR...", self.on_pff_reset),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        cont = QtWidgets.QHBoxLayout()
        cont.addStretch(1)
        cont.addWidget(QtWidgets.QLabel("Continuous"))
        self.rocker_pff_cont = RockerButton("On", "Off")
        self.rocker_pff_cont.setFixedSize(40, 36)
        cont.addWidget(self.rocker_pff_cont)
        root.addLayout(cont)
        root.addStretch(1)
        return w

    def _on_pff_stage_selected(self, stage: int) -> None:
        self.pff_stage.blockSignals(True)
        self.pff_stage.setValue(int(stage))
        self.pff_stage.blockSignals(False)
        self.pff_filter.stage.blockSignals(True)
        self.pff_filter.stage.setValue(int(stage))
        self.pff_filter.stage.blockSignals(False)
        self.pff_filter_panel.set_stage_index(stage)
        if getattr(self, "session", None) is not None:
            self.on_pff_filter_read_classic()

        # Open FilterDlg for PFF
        dlg = FilterDlg(
            [f"src {i}" for i in range(4)], max_stage=7,
            show_all_axes=True, show_all_sources=True, parent=self
        )
        dlg.setWindowTitle(f"PFF Filter — Stage {stage}")
        fs = self.pff_filter.to_stage()
        dlg.set_stage(fs)

        def on_dlg_changed(new_stage: object, all_axes: bool, all_sources: bool) -> None:
            if not isinstance(new_stage, FilterStage):
                return
            self.pff_filter.set_stage(new_stage)
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
            try:
                name = self.pff_filter.ftype.currentText().split(None, 1)[-1]
            except Exception:
                name = ""
            self.pff_stage_bar.set_stage_info(stage, name)
            self.pff_stage_bar.set_current(stage)
            # Write via safety path
            self.pff_stage.setValue(stage)
            self.pff_filter.stage.setValue(stage)
            self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)
            self.on_pff_filter_write()

        dlg.filterChanged.connect(on_dlg_changed)
        dlg.exec()
        dlg.deleteLater()

    def _sync_pff_panel_to_editor(self) -> None:
        self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)

    def on_pff_filter_read_classic(self) -> None:
        def work() -> None:
            stage_i = self.pff_stage_bar.current_stage()
            if stage_i < 0:
                stage_i = 0
            self.pff_stage.setValue(stage_i)
            self.pff_filter.stage.setValue(stage_i)
            self.on_pff_filter_read()
            self.pff_filter_panel.set_from_filter_editor(self.pff_filter)
            try:
                name = self.pff_filter.ftype.currentText().split(None, 1)[-1]
            except Exception:
                name = ""
            self.pff_stage_bar.set_stage_info(stage_i, name)
            self.pff_stage_bar.set_current(stage_i)

        self._run("Read PFF filter", work)

    def on_pff_filter_write_classic(self) -> None:
        stage_i = self.pff_stage_bar.current_stage()
        if stage_i < 0:
            stage_i = 0
        self.pff_stage.setValue(stage_i)
        self.pff_filter.stage.setValue(stage_i)
        self.pff_filter_panel.apply_to_filter_editor(self.pff_filter)
        self.on_pff_filter_write()

    def on_pff_read_classic(self) -> None:
        def work() -> None:
            self.on_pff_read()
            parts = self.pff_gains.text().split()
            for ed, p in zip(self.pff_gain_edits, parts):
                try:
                    ed.setText(f"{float(p):.5e}")
                except Exception:
                    ed.setText(p)

        self._run("PFF read", work)

    def on_pff_write_gains_classic(self) -> None:
        self.pff_gains.setText(" ".join(ed.text() for ed in self.pff_gain_edits))
        self.on_pff_write_gains()

    # ------------------------------------------------------------------ Kassandra (screenshot 9)

    def _page_kassandra(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        g_ex = GroupPanel("Excitation")
        ef = QtWidgets.QFormLayout(g_ex)
        self.kass_inject = SciEdit("None")
        self.kass_noise = SciEdit("0.00000e+000")
        ef.addRow("Injection Point:", self.kass_inject)
        ef.addRow("Noise Level:", self.kass_noise)
        top.addWidget(g_ex, 1)
        g_dg = GroupPanel("Diagnostic")
        df = QtWidgets.QFormLayout(g_dg)
        self.kass_diag0 = FlatPush("<IDC_INJECT_KASS>")
        self.kass_diag1 = FlatPush("<IDC_INJECT_KASS>")
        df.addRow("Diag0:", self.kass_diag0)
        df.addRow("Diag1:", self.kass_diag1)
        top.addWidget(g_dg, 1)
        root.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        mid.addWidget(QtWidgets.QLabel("Used axes"))
        self.kass_axes = QtWidgets.QComboBox()
        self.kass_axes.addItems([""] + [str(i) for i in range(1, 9)])
        mid.addWidget(self.kass_axes)
        mid.addWidget(QtWidgets.QLabel("Used biquads"))
        self.kass_biquads = QtWidgets.QComboBox()
        self.kass_biquads.addItems([""] + [str(i) for i in range(1, 9)])
        mid.addWidget(self.kass_biquads)
        mid.addStretch(1)
        root.addLayout(mid)

        # 8 axes rows
        for ax in range(1, 9):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(f"Axis {ax}"))
            # left slider-like
            sl = QtWidgets.QSlider(QtCore.Qt.Vertical)
            sl.setFixedSize(18, 36)
            sl.setRange(0, 100)
            sl.setValue(50)
            row.addWidget(sl)
            for _ in range(8):
                row.addWidget(_stage_box("", 32, 36))
            rk = RockerButton("On", "Off")
            rk.setFixedSize(36, 32)
            row.addWidget(rk)
            # right transfer arrows placeholder
            arr = QtWidgets.QFrame()
            arr.setFixedSize(24, 36)
            arr.setStyleSheet("background:#e8e8e8; border:1px solid #a0a0a0;")
            row.addWidget(arr)
            row.addStretch(1)
            root.addLayout(row)

        ov = QtWidgets.QHBoxLayout()
        ov.addStretch(1)
        ov.addWidget(QtWidgets.QLabel("overall loop:"))
        self.kass_overall = RockerButton("On", "Off")
        self.kass_overall.setFixedSize(40, 36)
        ov.addWidget(self.kass_overall)
        root.addLayout(ov)

        bot = QtWidgets.QHBoxLayout()
        g_nv = GroupPanel("Kassandra NVRAM")
        nl = QtWidgets.QHBoxLayout(g_nv)
        self.btn_kass_clear = _icon_button("Clear\nNV", color="#c00", min_w=56)
        self.btn_kass_load = _icon_button("Load\nNV", min_w=56)
        self.btn_kass_save = _icon_button("Save\nNV", min_w=56)
        nl.addWidget(self.btn_kass_clear)
        nl.addWidget(self.btn_kass_load)
        nl.addWidget(self.btn_kass_save)
        bot.addWidget(g_nv)

        g_su = GroupPanel("Kassandra Setup (from/to file)")
        sl = QtWidgets.QHBoxLayout(g_su)
        sl.addWidget(_icon_button("Load\nfile", min_w=56))
        sl.addWidget(_icon_button("Save\nfile", min_w=56))
        bot.addWidget(g_su)

        g_fw = GroupPanel("Kassandra Firmware")
        fl = QtWidgets.QHBoxLayout(g_fw)
        fl.addWidget(QtWidgets.QLabel("Current Version"))
        self.kass_fw = SciEdit("")
        self.kass_fw.setReadOnly(True)
        self.kass_fw.setFixedWidth(100)
        fl.addWidget(self.kass_fw)
        self.btn_kass_fw = _icon_button("C\nK", color="#c00", min_w=48)
        fl.addWidget(self.btn_kass_fw)
        bot.addWidget(g_fw)
        root.addLayout(bot)
        root.addStretch(1)
        return w

    # aliases expected by MainWindow builders
    def _page_velocity_tuning(self) -> QtWidgets.QWidget:
        return self._page_velocity_loop()

    def _page_velocity_matrix(self) -> QtWidgets.QWidget:
        return self._page_velocity_loop()

    def _page_position_tuning(self) -> QtWidgets.QWidget:
        return self._page_position_loop()

    def _page_position_sensor(self) -> QtWidgets.QWidget:
        return self._page_position_loop()

    def _page_position_motor(self) -> QtWidgets.QWidget:
        return self._page_position_loop()
