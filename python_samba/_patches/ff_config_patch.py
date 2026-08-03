from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from python_samba.ui.classic_widgets import (
    FlatPush,
    GroupPanel,
    IOSignalButton,
    SciEdit,
)
from python_samba.ui.widgets import (
    VEL_AXIS_LABELS,
    FilterDlg,
    MatrixEditor,
)
from python_samba.ui.main_window import VEL_AXES_NAMES

"""
FF Config Page Patch
====================
Replaces the simple _build_ff_config_page method with a version that adds
7 source selector buttons (Source1-Source7) and a 6x5 gain matrix grid
matching the SAMBA19xUI FFConfigPage C# code.

Changes
-------
1. Source selector buttons (Source1-Source7) in a horizontal row
   - Clicked source highlights green, others return to gray
   - Clicking a source loads that source's gains from the controller
2. 6x5 gain matrix grid (6 velocity axes x 5 gains) using SciEdit cells
   - Row labels: Xtrans, Zrot, Ytrans, Ztrans, Yrot, Xrot
   - Column labels: Gain1-Gain5
   - Editing a cell writes all 30 gains back via FSFFG
3. Keeps existing signal multipliers, offsets, and multipliers sections
4. Adds "Read gains" and "Write gains" action buttons

Usage
-----
Replace the existing _build_ff_config_page method in main_window.py with the
one below, and add the new handler methods to the MainWindow class.
"""

# ---------------------------------------------------------------------------
# Replacement for MainWindow._build_ff_config_page
# ---------------------------------------------------------------------------

def _build_ff_config_page(self) -> QtWidgets.QWidget:
    """FF config page — source selector, gain matrix, multipliers, offsets.

    Layout (matching SAMBA19xUI FFConfigPage):
      - Source definition  (GroupPanel)
          [Source1] [Source2] [Source3] [Source4] [Source5] [Source6] [Source7]
          Source signal: [_______]
      - Gain Matrix        (GroupPanel, 6 velocity axes x 5 gains)
             Gain1  Gain2  Gain3  Gain4  Gain5
           Xtrans [___]  [___]  [___]  [___]  [___]
           Zrot   [___]  [___]  [___]  [___]  [___]
           Ytrans [___]  [___]  [___]  [___]  [___]
           Ztrans [___]  [___]  [___]  [___]  [___]
           Yrot   [___]  [___]  [___]  [___]  [___]
           Xrot   [___]  [___]  [___]  [___]  [___]
      - Signal Multipliers | Offsets | Multipliers  (existing)
      - Action buttons: Read config, Write config, Write inputs, Write mult,
                        Read gains, Write gains
    """
    from python_samba.ui.classic_widgets import FlatPush, SciEdit, GroupPanel

    w = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(w)
    root.setContentsMargins(6, 4, 6, 4)
    root.setSpacing(4)

    # ==================================================================
    # Source definition — selector buttons + signal name
    # ==================================================================
    g_sd = GroupPanel("Source definition")
    sd = QtWidgets.QVBoxLayout(g_sd)
    sd.setSpacing(4)

    # Source selector buttons row (Source1-Source7, like C# FFConfigPage)
    src_row = QtWidgets.QHBoxLayout()
    src_row.setSpacing(2)
    self.ff_source_buttons: list[QtWidgets.QPushButton] = []
    self._ff_selected_source = 0
    for i in range(7):
        btn = FlatPush(f"Source{i+1}")
        btn.setCheckable(True)
        btn.setFixedHeight(28)
        # Default: gray; checked: green (like C# Colors.GreenYellow)
        btn.setStyleSheet(
            "QPushButton { background:#999; color:white; border:1px solid #777;"
            "  border-radius:3px; padding:2px 8px; font-weight:600; }"
            "QPushButton:checked { background:#6a0; color:white; }"
        )
        btn.clicked.connect(lambda checked, s=i: self._on_ff_source_clicked(s))
        if i == 0:
            btn.setChecked(True)
        self.ff_source_buttons.append(btn)
        src_row.addWidget(btn)
    src_row.addStretch(1)
    sd.addLayout(src_row)

    # Source signal definition (keep existing)
    sig_row = QtWidgets.QHBoxLayout()
    sig_row.addWidget(QtWidgets.QLabel("Source signal:"))
    self.ff_src_sig = SciEdit("InpXPOS")
    self.ff_src_sig.setFixedWidth(120)
    sig_row.addWidget(self.ff_src_sig)
    sig_row.addStretch(1)
    sd.addLayout(sig_row)
    root.addWidget(g_sd)

    # ==================================================================
    # Gain matrix — 6 velocity axes x 5 gains
    # ==================================================================
    g_mat = GroupPanel("Gain Matrix")
    mat = QtWidgets.QGridLayout(g_mat)
    mat.setSpacing(2)
    mat.setContentsMargins(4, 4, 4, 4)

    # Column headers (Gain1-Gain5, matching C# ElementLbls)
    GAIN_LABELS = ["Gain1", "Gain2", "Gain3", "Gain4", "Gain5"]
    for j, lbl in enumerate(GAIN_LABELS):
        label = QtWidgets.QLabel(lbl)
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet("font-weight:600; color:#1e5aa8; font-size:10px;")
        mat.addWidget(label, 0, j + 1)

    # Row headers (velocity axis names, matching C# AxisLbls from VelAxesName)
    self.ff_gain_cells: dict[tuple[int, int], SciEdit] = {}
    for i, axis_name in enumerate(VEL_AXES_NAMES):
        lbl = QtWidgets.QLabel(axis_name)
        lbl.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
        lbl.setStyleSheet("font-weight:600; color:#303030; font-size:10px;")
        mat.addWidget(lbl, i + 1, 0)
        for j in range(5):
            ed = SciEdit("0.00000e+000")
            ed.setFixedWidth(95)
            ed.setAlignment(QtCore.Qt.AlignCenter)
            # Use editingFinished to write only on Enter or focus-loss
            ed.editingFinished.connect(lambda a=i, g=j: self._on_ff_gain_changed(a, g))
            self.ff_gain_cells[(i, j)] = ed
            mat.addWidget(ed, i + 1, j + 1)
    root.addWidget(g_mat)

    # ==================================================================
    # Signal Multipliers, Offsets, Multipliers (keep existing)
    # ==================================================================
    mid = QtWidgets.QHBoxLayout()

    g_sm = GroupPanel("Signal Multipliers")
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

    g_off = GroupPanel("Offsets")
    of = QtWidgets.QFormLayout(g_off)
    self.ff_off_xpos = SciEdit("0.00000e+000")
    self.ff_off_ypos = SciEdit("0.00000e+000")
    of.addRow("XPos:", self.ff_off_xpos)
    of.addRow("YPos:", self.ff_off_ypos)
    mid.addWidget(g_off)

    g_mu = GroupPanel("Multipliers")
    mu = QtWidgets.QFormLayout(g_mu)
    self.ff_mul_xacc = SciEdit("0.00000e+000")
    self.ff_mul_yacc = SciEdit("0.00000e+000")
    mu.addRow("Xacc:", self.ff_mul_xacc)
    mu.addRow("Yacc:", self.ff_mul_yacc)
    mid.addWidget(g_mu)
    root.addLayout(mid)

    # ==================================================================
    # Action buttons
    # ==================================================================
    act = QtWidgets.QHBoxLayout()
    for text, slot in (
        ("Read config", self.on_ff_status_read_classic),
        ("Write config...", self.on_ff_write_cfg),
        ("Write inputs...", self.on_ff_write_inputs),
        ("Write mult...", self.on_ff_write_mult),
        ("Read gains", self.on_ff_read_gains),
        ("Write gains", self.on_ff_write_gains_from_matrix),
    ):
        b = FlatPush(text)
        b.clicked.connect(slot)
        act.addWidget(b)
    act.addStretch(1)
    root.addLayout(act)
    root.addStretch(1)
    return w


# ---------------------------------------------------------------------------
# New handler methods to add to the MainWindow class
# ---------------------------------------------------------------------------

def _on_ff_source_clicked(self, source: int) -> None:
    """Source selector button clicked — load gains for this source.

    Mimics C# PrepareSource(): grays out old button, greens the new one,
    reads gains from controller for all 6 axes, populates the matrix.
    """
    # Update button states (gray out all, green the selected one)
    for i, btn in enumerate(self.ff_source_buttons):
        btn.setChecked(i == source)
    self._ff_selected_source = source

    # Load gains from controller for this source (like C# GetFFStageGains loop)
    def work() -> None:
        s = self._require_session()
        try:
            inputs = s.get_ff_inputs()
            for button, value in zip(self.ff_source_buttons, inputs):
                try:
                    index = int(value)
                    button.setText(IOSignalButton.INPUT_NAMES[index])
                except (ValueError, IndexError):
                    button.setText(str(value))
            gains = s.get_ff_gains(source)
            self._populate_ff_gain_matrix(gains)
            self.log_msg(f"FF gains loaded for source {source+1}")
        except Exception as exc:
            self.log_msg(f"FF gains read failed for source {source+1}: {exc}")

    self._run("Read FF gains", work)


def _populate_ff_gain_matrix(self, gains: list[float]) -> None:
    """Fill the 6x5 gain matrix from a flat list of 30 values.

    Gains are indexed as [axis * 5 + gain] for axis=0..5, gain=0..4.
    Signals are blocked during population to avoid spurious write-backs.
    """
    for i in range(6):
        for j in range(5):
            idx = i * 5 + j
            if idx < len(gains):
                ed = self.ff_gain_cells.get((i, j))
                if ed is not None:
                    ed.blockSignals(True)
                    ed.setText(f"{gains[idx]:.5e}")
                    ed.blockSignals(False)


def on_ff_read_gains(self) -> None:
    """Read gains for the currently selected source and populate the matrix."""
    def work() -> None:
        s = self._require_session()
        try:
            inputs = s.get_ff_inputs()
            for button, value in zip(self.ff_source_buttons, inputs):
                try:
                    index = int(value)
                    button.setText(IOSignalButton.INPUT_NAMES[index])
                except (ValueError, IndexError):
                    button.setText(str(value))
            gains = s.get_ff_gains(self._ff_selected_source)
            self._populate_ff_gain_matrix(gains)
            self.log_msg(f"FF gains read for source {self._ff_selected_source+1}")
        except Exception as exc:
            self.log_msg(f"FF gains read failed: {exc}")
    self._run("Read FF gains", work)


def on_ff_write_gains_from_matrix(self) -> None:
    """Write all 30 gains from the matrix to the controller for the selected source."""
    # Collect all 30 gains from the matrix cells
    gains: list[float] = []
    for i in range(6):
        for j in range(5):
            ed = self.ff_gain_cells.get((i, j))
            if ed is not None:
                try:
                    gains.append(float(ed.text()))
                except ValueError:
                    self.log_msg(
                        f"Invalid gain value at axis {i}, gain {j}: '{ed.text()}'"
                    )
                    return
    if len(gains) != 30:
        self.log_msg(f"Expected 30 gains, got {len(gains)}")
        return

    src = self._ff_selected_source

    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write(f"FSFFG source={src}"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        s.set_ff_gains(src, *gains)
        self._set_writable(True)
        self.log_msg(f"FSFFG source={src} applied ({len(gains)} gains)")

    self._run("FF gains write", work)


def _on_ff_gain_changed(self, axis: int, gain_idx: int) -> None:
    """A gain cell was edited — write all 30 gains for the current source.

    This fires on editingFinished (Enter or focus-loss), matching the C#
    behavior where OnSteeringMatrixChanged fires on each cell edit and
    calls SetFFStageGains(axis, selectedSource).
    """
    # Collect all 30 gains
    gains: list[float] = []
    for i in range(6):
        for j in range(5):
            ed = self.ff_gain_cells.get((i, j))
            if ed is not None:
                try:
                    gains.append(float(ed.text()))
                except ValueError:
                    return  # Invalid input, skip write
    if len(gains) != 30:
        return

    src = self._ff_selected_source

    def work() -> None:
        s = self._require_session()
        assert self.gate
        if not self._confirm_write(f"FSFFG source={src} axis={axis}"):
            return
        self.gate.take_snapshot()
        self._set_writable(True)
        s.set_ff_gains(src, *gains)
        self._set_writable(True)
        self.log_msg(f"FSFFG source={src} axis={axis} gain={gain_idx} applied")

    self._run("FF gains write", work)
