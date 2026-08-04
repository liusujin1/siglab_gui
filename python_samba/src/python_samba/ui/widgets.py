"""Reusable UI widgets for filter/matrix editing."""

from __future__ import annotations

from python_samba.protocol.codes import FilterType, filter_small_name, filter_param_descriptions, filter_param_count
from python_samba.protocol.commands import FilterStage

try:
    from PySide6 import QtCore, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc


VEL_AXIS_LABELS = [
    "0 Xt",
    "1 Zr",
    "2 Yt",
    "3 Zt",
    "4 Yr",
    "5 Xr",
]

POS_AXIS_LABELS = [
    "0 XpRot",
    "1 YpRot",
    "2 Xtrans",
    "3 Ytrans",
    "4 ZpRot",
    "5 Zptrans",
]


class FilterEditor(QtWidgets.QWidget):
    """Axis/stage selector + type + 5 params."""

    changed = QtCore.Signal()

    def __init__(self, axis_labels: list[str], max_stage: int = 6, parent=None) -> None:
        super().__init__(parent)
        form = QtWidgets.QFormLayout(self)

        self.axis = QtWidgets.QComboBox()
        for i, label in enumerate(axis_labels):
            self.axis.addItem(label, i)
        self.stage = QtWidgets.QSpinBox()
        self.stage.setRange(0, max_stage)

        self.ftype = QtWidgets.QComboBox()
        for ft in FilterType:
            self.ftype.addItem(f"{int(ft)} {ft.name}", int(ft))

        self.params = [QtWidgets.QDoubleSpinBox() for _ in range(5)]
        for sp in self.params:
            sp.setDecimals(6)
            sp.setRange(-1e9, 1e9)
            sp.setSingleStep(0.01)

        form.addRow("Axis", self.axis)
        form.addRow("Stage", self.stage)
        form.addRow("Type", self.ftype)
        for i, sp in enumerate(self.params):
            form.addRow(f"P{i+1}", sp)

        self.axis.currentIndexChanged.connect(self.changed)
        self.stage.valueChanged.connect(self.changed)
        self.ftype.currentIndexChanged.connect(self.changed)
        for sp in self.params:
            sp.valueChanged.connect(self.changed)

    def axis_index(self) -> int:
        return int(self.axis.currentData())

    def stage_index(self) -> int:
        return int(self.stage.value())

    def set_stage(self, stage: FilterStage) -> None:
        self.axis.blockSignals(True)
        self.stage.blockSignals(True)
        self.ftype.blockSignals(True)
        for sp in self.params:
            sp.blockSignals(True)
        try:
            idx = self.axis.findData(stage.axis)
            if idx >= 0:
                self.axis.setCurrentIndex(idx)
            self.stage.setValue(stage.stage)
            tidx = self.ftype.findData(stage.filter_type)
            if tidx >= 0:
                self.ftype.setCurrentIndex(tidx)
            else:
                self.ftype.setCurrentIndex(0)
            for sp, val in zip(self.params, stage.params):
                sp.setValue(float(val))
        finally:
            self.axis.blockSignals(False)
            self.stage.blockSignals(False)
            self.ftype.blockSignals(False)
            for sp in self.params:
                sp.blockSignals(False)

    def to_stage(self) -> FilterStage:
        params = tuple(float(sp.value()) for sp in self.params)
        while len(params) < 5:
            params = params + (0.0,)
        return FilterStage(
            axis=self.axis_index(),
            stage=self.stage_index(),
            filter_type=int(self.ftype.currentData()),
            params=params[:5],  # type: ignore[arg-type]
        )


class FilterDlg(QtWidgets.QDialog):
    """Filter editor dialog matching SAMBA19xUI FilterDlg.xaml.

    Shows a type dropdown, 5 parameter labels/edits, and buttons:
    - Update (single)
    - Update for all axes (optional)
    - Update for all sources (optional)
    - Done/Close
    """

    # Signals: (FilterStage, changeForAllAxes, changeForAllSources)
    filterChanged = QtCore.Signal(object, bool, bool)  # FilterStage, allAxes, allSources

    def __init__(
        self,
        axis_labels: list[str],
        max_stage: int = 6,
        *,
        show_all_axes: bool = False,
        show_all_sources: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter Editor")
        self.setWindowFlags(
            QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowCloseButtonHint
        )
        self.setMinimumWidth(360)

        # Data
        self.axis_labels = list(axis_labels)
        self.max_stage = int(max_stage)
        self._stage = 0
        self._axis = 0

        # Layout
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setSpacing(8)
        vbox.setContentsMargins(12, 12, 12, 12)

        # The caller already selected the matrix cell.  Keep the widgets as
        # hidden state holders for compatibility, but do not expose duplicate
        # Axis/Stage selectors that can change the wire address accidentally.
        self.target_selector = QtWidgets.QWidget()
        ax_row = QtWidgets.QHBoxLayout(self.target_selector)
        ax_row.setContentsMargins(0, 0, 0, 0)
        ax_row.addWidget(QtWidgets.QLabel("Axis:"))
        self.axis_cbx = QtWidgets.QComboBox()
        for i, label in enumerate(axis_labels):
            self.axis_cbx.addItem(label, i)
        self.axis_cbx.currentIndexChanged.connect(self._on_axis_changed)
        ax_row.addWidget(self.axis_cbx, 1)
        ax_row.addWidget(QtWidgets.QLabel("Stage:"))
        self.stage_spin = QtWidgets.QSpinBox()
        self.stage_spin.setRange(0, max_stage)
        self.stage_spin.valueChanged.connect(self._on_stage_changed)
        ax_row.addWidget(self.stage_spin)
        self.target_selector.hide()
        vbox.addWidget(self.target_selector)

        # Filter type
        type_row = QtWidgets.QHBoxLayout()
        type_row.addWidget(QtWidgets.QLabel("Filter Type:"))
        self.ftype = QtWidgets.QComboBox()
        for ft in FilterType:
            self.ftype.addItem(f"{int(ft)} {ft.name}", int(ft))
        self.ftype.currentIndexChanged.connect(self._on_ftype_changed)
        type_row.addWidget(self.ftype, 1)
        vbox.addLayout(type_row)

        # Parameter labels and edits
        self.param_labels: list[QtWidgets.QLabel] = []
        self.param_edits: list[QtWidgets.QDoubleSpinBox] = []
        for i in range(5):
            row = QtWidgets.QHBoxLayout()
            lbl = QtWidgets.QLabel(f"P{i+1}:")
            lbl.setMinimumWidth(140)
            self.param_labels.append(lbl)
            ed = QtWidgets.QDoubleSpinBox()
            ed.setDecimals(6)
            ed.setRange(-1e9, 1e9)
            ed.setSingleStep(0.01)
            ed.setMinimumWidth(120)
            ed.valueChanged.connect(self._on_param_changed)
            self.param_edits.append(ed)
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            vbox.addLayout(row)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_update = QtWidgets.QPushButton("Update")
        self.btn_update.clicked.connect(self._on_update)
        btn_row.addWidget(self.btn_update)

        self.btn_all_axes = QtWidgets.QPushButton("Update for all axes")
        self.btn_all_axes.clicked.connect(self._on_update_all_axes)
        self.btn_all_axes.setVisible(show_all_axes)
        btn_row.addWidget(self.btn_all_axes)

        self.btn_all_sources = QtWidgets.QPushButton("Update for all sources")
        self.btn_all_sources.clicked.connect(self._on_update_all_sources)
        self.btn_all_sources.setVisible(show_all_sources)
        btn_row.addWidget(self.btn_all_sources)

        self.btn_done = QtWidgets.QPushButton("Done")
        self.btn_done.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_done)

        vbox.addLayout(btn_row)

        self.setStyleSheet("""
            QDialog {
                background: #e8eef3;
            }
            QLabel {
                color: #243447;
            }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #ffffff, stop:1 #dfe7ed);
                color: #263b4d;
                border: 1px solid #8fa1b0;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 23px;
            }
            QPushButton:hover { background: #eef6fc; border-color:#4f99d0; }
            QDoubleSpinBox, QComboBox, QSpinBox {
                background: #fff;
                color: #1f3445;
                border: 1px solid #9caeba;
                border-radius: 2px;
                padding: 3px 5px;
            }
        """)

        # Initialize parameter labels
        self._update_param_labels()

    def _on_axis_changed(self, idx: int) -> None:
        self._axis = int(self.axis_cbx.currentData())
        self.filterChanged.emit(self._to_stage_internal(), False, False)

    def _on_stage_changed(self, val: int) -> None:
        self._stage = int(val)
        self.filterChanged.emit(self._to_stage_internal(), False, False)

    def _on_ftype_changed(self, idx: int) -> None:
        self._update_param_labels()

    def _on_param_changed(self) -> None:
        pass

    def _update_param_labels(self) -> None:
        ftype = int(self.ftype.currentData())
        descs = filter_param_descriptions(ftype)
        count = filter_param_count(ftype)
        for i in range(5):
            label = descs[i] if descs[i] != "unused" else ""
            if label:
                self.param_labels[i].setText(label + ":")
                self.param_edits[i].setVisible(True)
                self.param_labels[i].setVisible(True)
            else:
                self.param_edits[i].setVisible(False)
                self.param_labels[i].setVisible(False)

    def _to_stage_internal(self) -> FilterStage:
        params = tuple(float(ed.value()) for ed in self.param_edits)
        while len(params) < 5:
            params = params + (0.0,)
        return FilterStage(
            axis=self._axis,
            stage=self._stage,
            filter_type=int(self.ftype.currentData()),
            params=params[:5],
        )

    def set_stage(self, stage: FilterStage) -> None:
        """Load values from a FilterStage."""
        self._axis = stage.axis
        self._stage = stage.stage
        self.axis_cbx.blockSignals(True)
        self.stage_spin.blockSignals(True)
        self.ftype.blockSignals(True)
        for ed in self.param_edits:
            ed.blockSignals(True)
        try:
            idx = self.axis_cbx.findData(stage.axis)
            if idx >= 0:
                self.axis_cbx.setCurrentIndex(idx)
            self.stage_spin.setValue(stage.stage)
            tidx = self.ftype.findData(stage.filter_type)
            if tidx >= 0:
                self.ftype.setCurrentIndex(tidx)
            for ed, val in zip(self.param_edits, stage.params):
                ed.setValue(float(val))
            self._update_param_labels()
        finally:
            self.axis_cbx.blockSignals(False)
            self.stage_spin.blockSignals(False)
            self.ftype.blockSignals(False)
            for ed in self.param_edits:
                ed.blockSignals(False)

    def _on_update(self) -> None:
        self.filterChanged.emit(self._to_stage_internal(), False, False)
        self.accept()

    def _on_update_all_axes(self) -> None:
        self.filterChanged.emit(self._to_stage_internal(), True, False)
        self.accept()

    def _on_update_all_sources(self) -> None:
        self.filterChanged.emit(self._to_stage_internal(), False, True)
        self.accept()


class MatrixEditor(QtWidgets.QWidget):
    """Simple row of double spinboxes."""

    def __init__(self, n: int, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spins = []
        for i in range(n):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setDecimals(6)
            sp.setRange(-1e6, 1e6)
            sp.setSingleStep(0.1)
            sp.setPrefix(f"{i}:")
            layout.addWidget(sp)
            self.spins.append(sp)

    def set_values(self, values: list[float]) -> None:
        for sp, v in zip(self.spins, values):
            sp.blockSignals(True)
            sp.setValue(float(v))
            sp.blockSignals(False)

    def values(self) -> list[float]:
        return [float(sp.value()) for sp in self.spins]
