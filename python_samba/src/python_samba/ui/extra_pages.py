"""Extra SAMBA pages: pneumatic helpers, system, dac/adc, logging, pff.

Page builders use classic SAMBA_UI group-panel chrome via classic_widgets.
"""

from __future__ import annotations

from python_samba.protocol.commands import FilterStage
from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    LedIndicator,
    RockerButton,
    SciEdit,
    SciSpin,
    format_ui_number,
)
from python_samba.ui.widgets import FilterEditor, MatrixEditor

try:
    from PySide6 import QtCore, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required") from exc


PNEUM_AXIS_LABELS = ["0 ZtPneum", "1 YrPneum", "2 XrPneum"]


def event_trace_params_are_disabled(tokens: list[str] | tuple[str, ...]) -> bool:
    """Return whether DGETP reported the firmware's unconfigured sentinel.

    Firmware 3.3.122 returns ``0 0 0 1 5000 0`` before event logging has
    been configured.  The two middle zeroes are legal GET defaults but are
    outside DSETP's SET ranges, so replaying this value is not a no-op.
    """
    if len(tokens) != 6:
        return False
    try:
        return tuple(int(value) for value in tokens[:3]) == (0, 0, 0)
    except (TypeError, ValueError):
        return False


class ExtraPagesMixin:
    """Mixin expected to be used with MainWindow (needs session/gate helpers)."""

    # ---- page builders -------------------------------------------------

    def _page_performance(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = GroupPanel("Performance Monitoring")
        form = QtWidgets.QFormLayout(g)
        self.perf_cfg = SciEdit()
        self.perf_status = SciEdit()
        self.perf_load = SciEdit()
        self.perf_cfg.setReadOnly(True)
        self.perf_status.setReadOnly(True)
        self.perf_load.setReadOnly(True)
        self.perf_write = SciEdit("0 1.0 0.5")
        form.addRow("Monitor config (DGPMV)", self.perf_cfg)
        form.addRow("Status (DGPMS)", self.perf_status)
        form.addRow("DSP load % (DGSLO)", self.perf_load)
        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write config...")
        btn_r.clicked.connect(self.on_perf_read)
        btn_w.clicked.connect(self.on_perf_write)
        row.addWidget(btn_r)
        row.addWidget(self.perf_write)
        row.addWidget(btn_w)
        form.addRow(row)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _page_switch(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = GroupPanel("Switch criterion setting")
        form = QtWidgets.QFormLayout(g)
        self.sw_signal = SciEdit()
        self.sw_cond = SciEdit()
        self.sw_cur = SciEdit()
        form.addRow("Switch signal (BGSWS)", self.sw_signal)
        form.addRow("Switch conditions (BGOCD)", self.sw_cond)
        form.addRow("Current status (DGCSS)", self.sw_cur)
        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_ws = FlatPush("Write signal...")
        btn_wc = FlatPush("Write conditions...")
        btn_r.clicked.connect(self.on_switch_read)
        btn_ws.clicked.connect(self.on_switch_write_signal)
        btn_wc.clicked.connect(self.on_switch_write_cond)
        row.addWidget(btn_r)
        row.addWidget(btn_ws)
        row.addWidget(btn_wc)
        form.addRow(row)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _page_motor_protection(self) -> QtWidgets.QWidget:
        """Classic Motor Overcurrent page (SAMBA_UI §2.2)."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setSpacing(8)

        g_cfg = GroupPanel("Motor Overcurrent / Failsafe")
        cf = QtWidgets.QFormLayout(g_cfg)
        self.mot_cfg = QtWidgets.QPlainTextEdit()
        self.mot_cfg.setFixedHeight(90)
        self.mot_cfg.setPlaceholderText(
            "Overcurrent config tokens (BGOCV) — threshold, reset delay, cooling…"
        )
        self.mot_use_temp = QtWidgets.QCheckBox(
            "Use temperature sensors (fw ≥ 3.3.06)"
        )
        self.mot_cooling = SciEdit()
        self.mot_threshold = SciEdit()
        self.mot_reset_delay = SciEdit()
        cf.addRow("Overcurrent config (BGOCV)", self.mot_cfg)
        cf.addRow("Threshold", self.mot_threshold)
        cf.addRow("Reset delay (s)", self.mot_reset_delay)
        cf.addRow("Cooling constant", self.mot_cooling)
        cf.addRow(self.mot_use_temp)
        root.addWidget(g_cfg)

        g_live = GroupPanel("Continuous display — motor power / temperature")
        lf = QtWidgets.QFormLayout(g_live)
        self.mot_power = QtWidgets.QPlainTextEdit()
        self.mot_power.setReadOnly(True)
        self.mot_power.setFixedHeight(90)
        self.mot_power.setPlaceholderText("Live power / temperature per motor (BGMPV)")
        self.mot_fs = QtWidgets.QPlainTextEdit()
        self.mot_fs.setReadOnly(True)
        self.mot_fs.setFixedHeight(70)
        self.mot_fs.setPlaceholderText("Failsafe status (BGMPS)")
        led_row = QtWidgets.QHBoxLayout()
        self.mot_leds = []
        for i in range(8):
            col = QtWidgets.QVBoxLayout()
            led = LedIndicator(12)
            self.mot_leds.append(led)
            col.addWidget(led, 0, QtCore.Qt.AlignHCenter)
            col.addWidget(QtWidgets.QLabel(f"M{i}"), 0, QtCore.Qt.AlignHCenter)
            led_row.addLayout(col)
        led_row.addStretch(1)
        lf.addRow(led_row)
        lf.addRow("Power / temp (BGMPV)", self.mot_power)
        lf.addRow("Failsafe status (BGMPS)", self.mot_fs)
        root.addWidget(g_live)

        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write config...")
        btn_r.clicked.connect(self.on_motor_read)
        btn_w.clicked.connect(self.on_motor_write)
        row.addWidget(btn_r)
        row.addWidget(btn_w)
        row.addStretch(1)
        cont = QtWidgets.QHBoxLayout()
        cont.addStretch(1)
        cont.addWidget(QtWidgets.QLabel("Continuous"))
        self.rocker_mot_cont = RockerButton("On", "Off")
        cont.addWidget(self.rocker_mot_cont)
        root.addLayout(row)
        root.addLayout(cont)
        root.addStretch(1)
        return w

    def _page_pneumatic_tuning(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        g = GroupPanel("Pneumatic filter (legacy)")
        gl = QtWidgets.QVBoxLayout(g)
        self.pneum_filter = FilterEditor(PNEUM_AXIS_LABELS, max_stage=3)
        gl.addWidget(self.pneum_filter)
        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read PGPAF")
        btn_w = FlatPush("Write PSPAF...")
        btn_r.clicked.connect(self.on_pneum_filter_read)
        btn_w.clicked.connect(self.on_pneum_filter_write)
        row.addWidget(btn_r)
        row.addWidget(btn_w)
        gl.addLayout(row)
        self.pneum_status = QtWidgets.QLabel("-")
        self.pneum_heights = QtWidgets.QLabel("-")
        self.pneum_prox_status = QtWidgets.QLabel("-")
        form = QtWidgets.QFormLayout()
        form.addRow("Axes status (PGPAS)", self.pneum_status)
        form.addRow("Heights/valves (PGPHV)", self.pneum_heights)
        form.addRow("Proximity (PGGIV)", self.pneum_prox_status)
        gl.addLayout(form)
        self.pneum_steer = MatrixEditor(8)
        gl.addWidget(self.pneum_steer)
        layout.addWidget(g)
        layout.addStretch(1)
        return w

    def _page_floatation(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = GroupPanel("Floatation Config")
        form = QtWidgets.QFormLayout(g)
        self.float_cfg = SciEdit()
        self.float_valve = SciEdit()
        self.float_setpoint = QtWidgets.QSpinBox()
        self.float_setpoint.setRange(0, 1)
        form.addRow("Config (PGPCP)", self.float_cfg)
        form.addRow("Valve offsets (PGPVO)", self.float_valve)
        form.addRow("Setpoint for all axes (PGPSS)", self.float_setpoint)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read", self.on_float_read),
            ("Write config...", self.on_float_write_cfg),
            ("Write valves...", self.on_float_write_valve),
            ("Write setpoint...", self.on_float_write_setpoint),
            ("PAUCO...", self.on_float_pauco),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        form.addRow(row)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _page_dither(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = GroupPanel("Dithering Config")
        form = QtWidgets.QFormLayout(g)
        self.dith_val = SciSpin()
        self.dith_val.setRange(-1e6, 1e6)
        self.dith_freq = SciSpin()
        self.dith_freq.setRange(1, 500)
        self.dith_alpha = SciSpin()
        self.dith_alpha.setRange(-1.0, 1.0)
        self.dith_alpha.setSingleStep(1e-4)
        form.addRow("Dither value", self.dith_val)
        form.addRow("Dither frequency Hz", self.dith_freq)
        form.addRow("Comp alpha", self.dith_alpha)
        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read")
        btn_w = FlatPush("Write...")
        btn_r.clicked.connect(self.on_dither_read)
        btn_w.clicked.connect(self.on_dither_write)
        row.addWidget(btn_r)
        row.addWidget(btn_w)
        form.addRow(row)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _page_pneumatic_ramp(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = GroupPanel("Pneumatic / Start-Up Ramp")
        form = QtWidgets.QFormLayout(g)
        self.ramp_type = QtWidgets.QSpinBox()
        self.ramp_type.setRange(0, 1)
        self.ramp_time = SciSpin()
        self.ramp_time.setRange(0.0, 600.0)
        form.addRow("Ramp type (0=actuator, 1=logical axes)", self.ramp_type)
        form.addRow("Start-up time s", self.ramp_time)
        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read BGSUT")
        btn_w = FlatPush("Write BSSUT...")
        btn_r.clicked.connect(self.on_ramp_read)
        btn_w.clicked.connect(self.on_ramp_write)
        row.addWidget(btn_r)
        row.addWidget(btn_w)
        form.addRow(row)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _page_pff(self) -> QtWidgets.QWidget:
        """Classic Pneumatic Stage Feed-Forward page (SAMBA_UI §2.10)."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setSpacing(6)

        head = GroupPanel("Pneumatic FF — axis / source")
        hl = QtWidgets.QHBoxLayout(head)
        hl.addWidget(QtWidgets.QLabel("Axis"))
        self.pff_axis = QtWidgets.QSpinBox()
        self.pff_axis.setRange(0, 2)
        hl.addWidget(self.pff_axis)
        hl.addWidget(QtWidgets.QLabel("Source"))
        self.pff_source = QtWidgets.QSpinBox()
        self.pff_source.setRange(0, 7)
        hl.addWidget(self.pff_source)
        hl.addWidget(QtWidgets.QLabel("Stage"))
        self.pff_stage = QtWidgets.QSpinBox()
        self.pff_stage.setRange(0, 8)
        hl.addWidget(self.pff_stage)
        for _ in range(4):
            bar = QtWidgets.QFrame()
            bar.setFixedSize(36, 40)
            bar.setStyleSheet(
                "background:#ffffff; border:1px solid #606060;"
                "border-top: 6px solid #1e5aa8;"
            )
            hl.addWidget(bar)
        hl.addStretch(1)
        self.led_pff = LedIndicator()
        self.rocker_pff = RockerButton("On", "Off")
        hl.addWidget(QtWidgets.QLabel("PFF"))
        hl.addWidget(self.led_pff)
        hl.addWidget(self.rocker_pff)
        root.addWidget(head)

        g_g = GroupPanel("PFF Global Setting")
        gf = QtWidgets.QFormLayout(g_g)
        self.pff_cfg = SciEdit()
        self.pff_params = SciEdit()
        self.pff_inputs = SciEdit()
        self.pff_gains = SciEdit()
        gf.addRow("Config NoOfGains/Threshold (FGCPF)", self.pff_cfg)
        gf.addRow("Source params Outputs/Rate (FGPPF)", self.pff_params)
        gf.addRow("Inputs (FGIPF)", self.pff_inputs)
        gf.addRow("FIR gains axis+source (FGGPF)", self.pff_gains)
        root.addWidget(g_g)

        g_f = GroupPanel("PFF filter stage (FGFSP / FSFSP)")
        fl = QtWidgets.QVBoxLayout(g_f)
        self.pff_filter = FilterEditor(["axis via spin"], max_stage=8)
        self.pff_filter.axis.setEnabled(False)
        fl.addWidget(self.pff_filter)
        root.addWidget(g_f)

        row = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read all", self.on_pff_read),
            ("Read filter", self.on_pff_filter_read),
            ("Write filter...", self.on_pff_filter_write),
            ("Write config...", self.on_pff_write_cfg),
            ("Write params...", self.on_pff_write_params),
            ("Write inputs...", self.on_pff_write_inputs),
            ("Write gains...", self.on_pff_write_gains),
            ("Reset FIR (FARPF)...", self.on_pff_reset),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)

        g_c = GroupPanel("Continuous display")
        cl = QtWidgets.QVBoxLayout(g_c)
        self.pff_cont = QtWidgets.QPlainTextEdit()
        self.pff_cont.setReadOnly(True)
        self.pff_cont.setFixedHeight(70)
        self.pff_cont.setPlaceholderText("Live PFF gains / status")
        cl.addWidget(self.pff_cont)
        cont = QtWidgets.QHBoxLayout()
        cont.addStretch(1)
        cont.addWidget(QtWidgets.QLabel("Continuous"))
        self.rocker_pff_cont = RockerButton("On", "Off")
        cont.addWidget(self.rocker_pff_cont)
        cl.addLayout(cont)
        root.addWidget(g_c)
        root.addStretch(1)
        return w

    def _page_dac_adc(self) -> QtWidgets.QWidget:
        """Classic DA/AD channels page (SAMBA_UI §2.8)."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setSpacing(8)

        warn = QtWidgets.QLabel(
            "Caution: Selecting incorrect DA/AD channel mappings can damage your controller. "
            "IDE / this host will not take responsibility for improper use."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "color:#800000; padding:8px; background:#ffe8e8; border:1px solid #c06060;"
        )
        root.addWidget(warn)

        g_type = GroupPanel("Controller type")
        tf = QtWidgets.QFormLayout(g_type)
        self.dac_ctrl_type = SciEdit()
        self.dac_ctrl_type.setReadOnly(True)
        self.dac_ctrl_type.setPlaceholderText("Returned by firmware (e.g. OPTICON / TCMFD)")
        tf.addRow("Connected controller", self.dac_ctrl_type)
        root.addWidget(g_type)

        g_map = GroupPanel("DA / AD channel mapping")
        mf = QtWidgets.QFormLayout(g_map)
        self.adc_seq = QtWidgets.QPlainTextEdit()
        self.adc_seq.setFixedHeight(100)
        self.adc_seq.setPlaceholderText("ADC sequence (BGADS) — up to 25 tokens")
        self.dac_seq = QtWidgets.QPlainTextEdit()
        self.dac_seq.setFixedHeight(100)
        self.dac_seq.setPlaceholderText("DAC sequence (BGDAS) — up to 20 tokens")
        mf.addRow("ADC sequence (BGADS, 25)", self.adc_seq)
        mf.addRow("DAC sequence (BGDAS, 20)", self.dac_seq)
        root.addWidget(g_map)

        g_pre = GroupPanel("Presets / files")
        pl = QtWidgets.QHBoxLayout(g_pre)
        self.dac_preset = QtWidgets.QComboBox()
        self.dac_preset.addItems([
            "(select preset)",
            "OPTICON default",
            "TCMFD default",
            "MAXCON default",
        ])
        btn_preset = FlatPush("Load Preset")
        btn_load = FlatPush("Load From File…")
        btn_save = FlatPush("Save DACADC File…")
        btn_preset.clicked.connect(self.on_dacadc_load_preset)
        btn_load.clicked.connect(self.on_dacadc_load_file)
        btn_save.clicked.connect(self.on_dacadc_save_file)
        pl.addWidget(self.dac_preset, 1)
        pl.addWidget(btn_preset)
        pl.addWidget(btn_load)
        pl.addWidget(btn_save)
        root.addWidget(g_pre)

        row = QtWidgets.QHBoxLayout()
        btn_r = FlatPush("Read from controller")
        btn_wa = FlatPush("Write ADC...")
        btn_wd = FlatPush("Write DAC...")
        btn_r.clicked.connect(self.on_dacadc_read)
        btn_wa.clicked.connect(self.on_dacadc_write_adc)
        btn_wd.clicked.connect(self.on_dacadc_write_dac)
        row.addWidget(btn_r)
        row.addWidget(btn_wa)
        row.addWidget(btn_wd)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        return w

    def on_dacadc_load_preset(self) -> None:
        name = self.dac_preset.currentText()
        if name.startswith("("):
            return
        self.log_msg(f"DAC/ADC preset selected: {name} (apply via Write after edit)")
        QtWidgets.QMessageBox.information(
            self,
            "Preset",
            f"Preset «{name}» noted.\n"
            "Fill ADC/DAC sequences then Write. Full preset tables come in a later phase.",
        )

    def on_dacadc_load_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load DACADC config", "", "DACADC (*.xml *.DACADCConfig.xml);;All (*.*)"
        )
        if path:
            self.log_msg(f"DACADC file: {path}")

    def on_dacadc_save_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save DACADC config", "DACADCConfig.xml", "DACADC (*.xml);;All (*.*)"
        )
        if path:
            self.log_msg(f"DACADC save path: {path}")

    def _page_logging(self) -> QtWidgets.QWidget:
        """Classic Logging page (SAMBA_UI §2.9, fw ≥ 3.3.2)."""
        w = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(w)
        root.setSpacing(6)

        g_p = GroupPanel("Event logging parameters")
        form = QtWidgets.QFormLayout(g_p)
        self.log_params = SciEdit()
        self.log_info = SciEdit()
        self.log_event = SciEdit()
        self.log_info.setReadOnly(True)
        self.log_mon_num = QtWidgets.QSpinBox()
        self.log_mon_num.setRange(0, 39)
        self.log_mon_sig = SciEdit()
        self.log_live = SciEdit()
        self.log_live.setReadOnly(True)
        self.log_trace_num = QtWidgets.QSpinBox()
        self.log_trace_num.setRange(0, 100)
        self.log_event_time = SciEdit()
        self.log_event_time.setReadOnly(True)
        form.addRow("Trace params (DGETP)", self.log_params)
        form.addRow("Trace info (DGETI)", self.log_info)
        form.addRow("Event signal (DGETS)", self.log_event)
        form.addRow("Monitor # (DGMOS)", self.log_mon_num)
        form.addRow("Monitor signal type/main/sub", self.log_mon_sig)
        form.addRow("Live monitor values (DGMSV 0..3)", self.log_live)
        form.addRow("Trace #", self.log_trace_num)
        form.addRow("Event time (DGEVT)", self.log_event_time)
        root.addWidget(g_p)

        row = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Read", self.on_logging_read),
            ("Write params...", self.on_logging_write_params),
            ("Write event...", self.on_logging_write_event),
            ("Write monitor...", self.on_logging_write_monitor),
        ):
            b = FlatPush(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        btn_start = FlatPush("Start...")
        btn_stop = FlatPush("Stop...")
        btn_dl = FlatPush("Download trace...")
        btn_start.clicked.connect(lambda: self.on_logging_startstop(1))
        btn_stop.clicked.connect(lambda: self.on_logging_startstop(0))
        btn_dl.clicked.connect(self.on_logging_download)
        row.addWidget(btn_start)
        row.addWidget(btn_stop)
        row.addWidget(btn_dl)
        row.addStretch(1)
        root.addLayout(row)

        g_d = GroupPanel("Downloaded trace")
        dl = QtWidgets.QVBoxLayout(g_d)
        self.log_data = QtWidgets.QPlainTextEdit()
        self.log_data.setReadOnly(True)
        self.log_data.setPlaceholderText("Downloaded trace samples appear here (CSV rows).")
        self.log_data.setMinimumHeight(120)
        dl.addWidget(self.log_data)
        root.addWidget(g_d, 1)

        g_a = GroupPanel("Analysis filter logging (L*)")
        self.analysis_logging_group = g_a
        aform = QtWidgets.QFormLayout(g_a)
        self.analysis_params = SciEdit()
        self.analysis_input = SciEdit()
        self.analysis_out = SciEdit()
        self.analysis_out.setReadOnly(True)
        self.analysis_events = SciEdit()
        self.analysis_events.setReadOnly(True)
        aform.addRow("Params (LGANP)", self.analysis_params)
        aform.addRow("Input (LGAIS)", self.analysis_input)
        aform.addRow("Filter outputs (LGAFO)", self.analysis_out)
        aform.addRow("Events (LGAEV)", self.analysis_events)
        arow = QtWidgets.QHBoxLayout()
        btn_ar = FlatPush("Read analysis")
        btn_aw = FlatPush("Write analysis...")
        btn_ar.clicked.connect(self.on_analysis_read)
        btn_aw.clicked.connect(self.on_analysis_write)
        arow.addWidget(btn_ar)
        arow.addWidget(btn_aw)
        arow.addStretch(1)
        aform.addRow(arow)
        root.addWidget(g_a)
        return w

    # ---- handlers (kept compatible with session API) -------------------

    def on_perf_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            cfg = " ".join(s.get_performance_monitor())
            st = " ".join(s.get_performance_status())
            load = format_ui_number(s.get_system_load())
            if hasattr(self, "perf_cfg"):
                self.perf_cfg.setText(cfg)
            if hasattr(self, "perf_status"):
                self.perf_status.setText(st)
            if hasattr(self, "perf_load"):
                self.perf_load.setText(load)
            if hasattr(self, "perf_write"):
                self.perf_write.setText(cfg)
            if hasattr(self, "perf_actual"):
                self.perf_actual.setText(st or "Perf. okay")
            if hasattr(self, "fs_load"):
                self.fs_load.setText(load)
            self.log_msg("performance read")

        self._run("Performance read", work)

    def on_perf_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            raw = self.perf_write.text().strip() if hasattr(self, "perf_write") else ""
            if not raw and hasattr(self, "perf_cfg"):
                raw = self.perf_cfg.text().strip()
            tokens = raw.split()
            if not self._confirm_write(f"DSPMV {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_performance_monitor(tokens)
            self._set_writable(True)
            self.log_msg("performance written")

        self._run("Performance write", work)

    def on_switch_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            sig = " ".join(s.get_switch_signal())
            cond = " ".join(s.get_switch_conditions())
            cur = " ".join(s.get_switch_status())
            if hasattr(self, "sw_signal"):
                self.sw_signal.setText(sig)
            if hasattr(self, "sw_cond"):
                self.sw_cond.setText(cond)
            if hasattr(self, "sw_cur"):
                self.sw_cur.setText(cur)
            if hasattr(self, "sw_fb"):
                self.sw_fb.setText(cur)
            self.log_msg("switch read")

        self._run("Switch read", work)

    def on_switch_write_signal(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.sw_signal.text().split()
            if not self._confirm_write(f"BSSWS {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_switch_signal(tokens)
            self._set_writable(True)
            self.log_msg("switch signal written")

        self._run("Switch signal write", work)

    def on_switch_write_cond(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.sw_cond.text().split()
            if not self._confirm_write(f"BSOCD {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_switch_conditions(tokens)
            self._set_writable(True)
            self.log_msg("switch conditions written")

        self._run("Switch cond write", work)

    def on_motor_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.mot_cfg.setPlainText(" ".join(s.get_motor_overcurrent_config()))
            self.mot_power.setPlainText(" ".join(s.get_motor_power_values()))
            self.mot_fs.setPlainText(" ".join(s.get_motor_failsafe_status()))
            self.log_msg("motor protection read")

        self._run("Motor read", work)

    def on_motor_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.mot_cfg.toPlainText().split()
            if not self._confirm_write(f"BSOCV {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_motor_overcurrent_config(tokens)
            self._set_writable(True)
            self.log_msg("motor overcurrent written")

        self._run("Motor write", work)

    def on_pneum_filter_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.pneum_filter.axis_index()
            stage = self.pneum_filter.stage_index()
            fs = s.get_pneumatic_filter(axis, stage)
            self.pneum_filter.set_stage(fs)
            self.log_msg(f"PGPAF axis={axis} stage={stage} type={fs.type_name}")

        self._run("Pneum filter read", work)

    def on_pneum_filter_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.pneum_filter.to_stage()
            if not self._confirm_write(
                f"PSPAF axis={stage.axis} stage={stage.stage} type={stage.filter_type}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pneumatic_filter(stage)
            self._set_writable(True)
            self.log_msg("PSPAF applied")

        self._run("Pneum filter write", work)

    def on_pneum_status(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.pneum_status.setText(" ".join(s.get_pneumatic_axes_status()))
            self.pneum_heights.setText(" ".join(s.get_pneumatic_heights_valves()))
            prox_txt = " ".join(
                format_ui_number(x) for x in s.get_pneumatic_proximity_inputs()
            )
            status_lbl = getattr(self, "pneum_prox_status", None)
            if status_lbl is not None:
                status_lbl.setText(prox_txt)
            elif hasattr(self, "pneum_prox") and hasattr(self.pneum_prox, "setText"):
                self.pneum_prox.setText(prox_txt)
            self.log_msg("pneumatic status refreshed")

        self._run("Pneum status", work)

    def on_pneum_steer_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = self.pneum_filter.axis_index()
            vals = list(s.get_pneumatic_steering_matrix(axis))
            while len(vals) < 8:
                vals.append(0.0)
            self.pneum_steer.set_values(vals[:8])
            if hasattr(self, "pneum_prox") and isinstance(self.pneum_prox, list):
                for i, ed in enumerate(self.pneum_prox):
                    if i < len(vals):
                        ed.setText(f"{vals[i]:.5e}")
            if hasattr(self, "pneum_valve") and isinstance(self.pneum_valve, list):
                for i, ed in enumerate(self.pneum_valve):
                    j = i + 4
                    if j < len(vals):
                        ed.setText(f"{vals[j]:.5e}")
            self.log_msg(f"PGPSM axis={axis}")

        self._run("Pneum steer read", work)

    def on_pneum_steer_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            axis = self.pneum_filter.axis_index()
            if (
                hasattr(self, "pneum_prox")
                and isinstance(self.pneum_prox, list)
                and hasattr(self, "pneum_valve")
                and isinstance(self.pneum_valve, list)
            ):
                vals = [float(ed.text()) for ed in self.pneum_prox] + [
                    float(ed.text()) for ed in self.pneum_valve
                ]
                self.pneum_steer.set_values(vals[:8])
            vals = self.pneum_steer.values()
            if not self._confirm_write(f"PSPSM axis={axis} {vals}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pneumatic_steering_matrix(axis, vals)
            self._set_writable(True)
            self.log_msg("PSPSM applied")

        self._run("Pneum steer write", work)

    def on_float_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.float_cfg.setText(" ".join(s.get_floatation_config()))
            self.float_valve.setText(
                " ".join(
                    format_ui_number(x) for x in s.get_pneumatic_valve_offsets()
                )
            )
            try:
                self.float_setpoint.setValue(int(s.get_floatation_setpoint_mode()))
            except Exception:
                pass
            self.log_msg("floatation read")

        self._run("Float read", work)

    def on_float_write_cfg(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.float_cfg.text().split()
            if not self._confirm_write(f"PSPCP {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_floatation_config(tokens)
            self._set_writable(True)
            self.log_msg("float cfg written")

        self._run("Float cfg write", work)

    def on_float_write_valve(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            if hasattr(self, "valve_off_up") and hasattr(self, "valve_off_down"):
                vals = []
                for up, dn in zip(self.valve_off_up, self.valve_off_down):
                    vals.extend([float(up.text()), float(dn.text())])
                self.float_valve.setText(
                    " ".join(format_ui_number(v) for v in vals)
                )
            vals = [float(x) for x in self.float_valve.text().split()]
            if not self._confirm_write(f"PSPVO {vals}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pneumatic_valve_offsets(vals)
            self._set_writable(True)
            self.log_msg("valve offsets written")

        self._run("Float valve write", work)

    def on_float_write_setpoint(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            mode = int(self.float_setpoint.value())
            if not self._confirm_write(f"PSPSS mode={mode}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_floatation_setpoint_mode(mode)
            self._set_writable(True)
            self.log_msg("float setpoint written")

        self._run("Float setpoint write", work)

    def on_float_pauco(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            if not self._confirm_destructive("PAUCO use current valve outputs as offsets"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.use_current_valve_offsets()
            self._set_writable(True)
            self.log_msg("PAUCO applied")

        self._run("PAUCO", work)

    def on_dither_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            val, freq, alpha = s.get_dither()
            self.dith_val.setValue(val)
            self.dith_freq.setValue(freq)
            self.dith_alpha.setValue(alpha)
            self.log_msg("dither read")

        self._run("Dither read", work)

    def on_dither_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            v = self.dith_val.value()
            f = self.dith_freq.value()
            a = self.dith_alpha.value()
            if not self._confirm_write(f"dither val={v} freq={f} alpha={a}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_dither(v, f, a)
            self._set_writable(True)
            self.log_msg("dither written")

        self._run("Dither write", work)

    def on_ramp_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            rtype, rtime = s.get_startup_ramp()
            self.ramp_type.setValue(int(rtype))
            self.ramp_time.setValue(float(rtime))
            if hasattr(self, "ramp_type_combo"):
                self.ramp_type_combo.setCurrentIndex(int(rtype))
            if hasattr(self, "ramp_time_edit"):
                self.ramp_time_edit.setText(f"{float(rtime):.5e}")
            self.log_msg("ramp read")

        self._run("Ramp read", work)

    def on_ramp_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            rtype = int(self.ramp_type.value())
            if hasattr(self, "ramp_type_combo"):
                rtype = int(self.ramp_type_combo.currentIndex())
            rtime = float(self.ramp_time.value())
            if hasattr(self, "ramp_time_edit") and self.ramp_time_edit.text().strip():
                rtime = float(self.ramp_time_edit.text())
            if not self._confirm_write(f"BSSUT type={rtype} time={rtime}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_startup_ramp(rtype, rtime)
            self._set_writable(True)
            self.log_msg("ramp written")

        self._run("Ramp write", work)

    def on_pff_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.pff_cfg.setText(" ".join(s.get_pff_config()))
            self.pff_params.setText(" ".join(s.get_pff_params(self.pff_source.value())))
            self.pff_inputs.setText(" ".join(s.get_pff_inputs()))
            self.pff_gains.setText(
                " ".join(
                    format_ui_number(x)
                    for x in s.get_pff_gains(self.pff_axis.value(), self.pff_source.value())
                )
            )
            if hasattr(self, "pff_cont"):
                self.pff_cont.setPlainText(self.pff_gains.text())
            self.log_msg("pff read")

        self._run("PFF read", work)

    def on_pff_filter_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            axis = int(self.pff_axis.value())
            stage = int(self.pff_stage.value())
            fs = s.get_pff_filter(axis, self.pff_source.value(), stage)
            self.pff_filter.set_stage(fs)
            self.log_msg(f"FGFSP axis={axis} stage={stage}")

        self._run("PFF filter read", work)

    def on_pff_filter_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            stage = self.pff_filter.to_stage()
            stage = FilterStage(
                axis=int(self.pff_axis.value()),
                stage=int(self.pff_stage.value()),
                filter_type=stage.filter_type,
                params=stage.params,
            )
            if not self._confirm_write(
                f"FSFSP axis={stage.axis} src={self.pff_source.value()} stage={stage.stage}"
            ):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_filter(stage, self.pff_source.value())
            self._set_writable(True)
            self.log_msg("FSFSP applied")

        self._run("PFF filter write", work)

    def on_pff_write_cfg(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.pff_cfg.text().split()
            if not self._confirm_write(f"FSCPF {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_config(tokens)
            self._set_writable(True)
            self.log_msg("pff cfg written")

        self._run("PFF cfg write", work)

    def on_pff_write_params(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.pff_params.text().split()
            src = int(self.pff_source.value())
            if not self._confirm_write(f"FSPPF src={src} {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_params(src, tokens)
            self._set_writable(True)
            self.log_msg("pff params written")

        self._run("PFF params write", work)

    def on_pff_write_inputs(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.pff_inputs.text().split()
            if not self._confirm_write(f"FSIPF {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_inputs(tokens)
            self._set_writable(True)
            self.log_msg("pff inputs written")

        self._run("PFF inputs write", work)

    def on_pff_write_gains(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            gains = [float(x) for x in self.pff_gains.text().split()]
            axis = int(self.pff_axis.value())
            src = int(self.pff_source.value())
            if not self._confirm_write(f"FSGPF axis={axis} src={src} n={len(gains)}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_pff_gains(axis, src, gains)
            self._set_writable(True)
            self.log_msg("pff gains written")

        self._run("PFF gains write", work)

    def on_pff_reset(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            axis = int(self.pff_axis.value())
            src = int(self.pff_source.value())
            if not self._confirm_write(f"FARPF reset FIR axis={axis} src={src}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.reset_pff_fir(axis, src)
            self._set_writable(True)
            self.log_msg("FARPF applied")

        self._run("PFF reset", work)

    def on_logging_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.log_params.setText(" ".join(s.get_event_trace_params()))
            self.log_info.setText(" ".join(s.get_event_trace_info()))
            self.log_event.setText(" ".join(s.get_event_signal()))
            mon = int(self.log_mon_num.value())
            self.log_mon_sig.setText(" ".join(s.get_monitor_signal(mon)))
            self.log_live.setText(
                " ".join(format_ui_number(x) for x in s.get_monitor_values())
            )
            self.log_event_time.setText(" ".join(s.get_event_time()))
            self.log_msg("logging read")

        self._run("Logging read", work)

    def on_logging_write_params(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.log_params.text().split()
            if len(tokens) != 6:
                raise ValueError(
                    f"DSETP requires 6 parameters, got {len(tokens)}"
                )
            if event_trace_params_are_disabled(tokens):
                self.log_msg(
                    "Event trace is disabled/unconfigured; edit MaxBuffLen "
                    "and MonSigNum to valid non-zero values before writing"
                )
                return
            if not self._confirm_write(f"DSETP {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_event_trace_params(*tokens)
            finally:
                self._set_writable(True)
            self.log_msg("log params written")

        self._run("Log params write", work)

    def on_logging_write_event(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.log_event.text().split()
            if not self._confirm_write(f"DSETS {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_event_signal(*tokens)
            finally:
                self._set_writable(True)
            self.log_msg("log event written")

        self._run("Log event write", work)

    def on_logging_write_monitor(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            mon = int(self.log_mon_num.value())
            tokens = self.log_mon_sig.text().split()
            if not self._confirm_write(f"DSMOS #{mon} {tokens}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.set_monitor_signal(mon, *tokens)
            finally:
                self._set_writable(True)
            self.log_msg("log monitor written")

        self._run("Log monitor write", work)

    def on_logging_startstop(self, status: int) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            if not self._confirm_write(f"event trace status={status}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                s.start_stop_event_tracing(status)
            finally:
                self._set_writable(True)
            self.log_msg(f"log {'started' if status else 'stopped'}")

        self._run("Log start/stop", work)

    def on_logging_download(self) -> None:
        def work() -> None:
            s = self._require_session()
            n = int(self.log_trace_num.value())
            data = s.download_event_trace(n)
            lines = [" ".join(str(x) for x in row) for row in data]
            self.log_data.setPlainText("\n".join(lines))
            self.log_msg(f"downloaded {len(lines)} trace rows")

        self._run("Log download", work)

    def on_analysis_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.analysis_params.setText(" ".join(s.get_analysis_params()))
            self.analysis_input.setText(" ".join(s.get_analysis_input()))
            self.analysis_out.setText(
                " ".join(
                    format_ui_number(x) for x in s.get_analysis_filter_outputs()
                )
            )
            self.analysis_events.setText(" ".join(s.get_analysis_events()))
            self.log_msg("analysis read")

        self._run("Analysis read", work)

    def on_analysis_write(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            params = self.analysis_params.text().split()
            inputs = self.analysis_input.text().split()
            if not self._confirm_write(f"analysis params={params} input={inputs}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            try:
                if params:
                    s.set_analysis_params(*params)
                if inputs:
                    s.set_analysis_input(*inputs)
            finally:
                self._set_writable(True)
            self.log_msg("analysis written")

        self._run("Analysis write", work)

    def on_dacadc_read(self) -> None:
        def work() -> None:
            s = self._require_session()
            self.adc_seq.setPlainText(" ".join(s.get_adc_sequence()))
            self.dac_seq.setPlainText(" ".join(s.get_dac_sequence()))
            try:
                if hasattr(self, "dac_ctrl_type"):
                    self.dac_ctrl_type.setText(" ".join(s.get_controller_type()))
            except Exception:
                pass
            self.log_msg("dac/adc read")

        self._run("DAC/ADC read", work)

    def on_dacadc_write_adc(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.adc_seq.toPlainText().split()
            if not self._confirm_write(f"BSADS n={len(tokens)}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_adc_sequence(tokens)
            self._set_writable(True)
            self.log_msg("ADC sequence written")

        self._run("ADC write", work)

    def on_dacadc_write_dac(self) -> None:
        def work() -> None:
            s = self._require_session()
            assert self.gate
            tokens = self.dac_seq.toPlainText().split()
            if not self._confirm_write(f"BSDAS n={len(tokens)}"):
                return
            self.gate.take_snapshot()
            self._set_writable(True)
            s.set_dac_sequence(tokens)
            self._set_writable(True)
            self.log_msg("DAC sequence written")

        self._run("DAC write", work)
