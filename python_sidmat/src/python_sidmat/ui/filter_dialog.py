"""Filter configuration dialog — port of ``SAMBA19xUI.FilterDlg``.

Clicking a filter button in the original software opens this modal window:
a type combo (24 filter types) plus five parameter editors, with the
``unused`` parameters hidden.  ``Update Filter`` parses the editors and
closes with the result; ``Done`` closes without saving.

Result is read back from ``filter_type_id`` / ``filter_params`` after the
dialog is accepted.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

from python_sidmat.measurement.filters import FILTER_TYPES

__all__ = ["FilterDialog"]


class FilterDialog(QtWidgets.QDialog):
    def __init__(
        self,
        stage: int,
        filter_type_id: int = 0,
        params: list[float] | None = None,
        *,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Filter {stage + 1} Configuration")
        self.setModal(True)

        self._type = int(filter_type_id)
        if not 0 <= self._type < len(FILTER_TYPES):
            raise ValueError(f"unsupported filter type {filter_type_id}")
        self._params = list(params if params is not None else [1.0] * 5)

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(10, 10, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        # Row 0: filter-type combo (original lists only smallName).
        grid.addWidget(QtWidgets.QLabel("Filter Type"), 0, 0)
        self.type_cbx = QtWidgets.QComboBox()
        for small, long_name, _desc in FILTER_TYPES:
            self.type_cbx.addItem(small)
            self.type_cbx.setItemData(self.type_cbx.count() - 1, long_name,
                                      QtCore.Qt.ItemDataRole.ToolTipRole)
        self.type_cbx.currentIndexChanged.connect(self._on_type_changed)
        grid.addWidget(self.type_cbx, 0, 1)

        # Rows 1..5: parameter label + editor (unused rows hidden).
        self._par_lbls: list[QtWidgets.QLabel] = []
        self._par_edits: list[QtWidgets.QLineEdit] = []
        for i in range(5):
            lbl = QtWidgets.QLabel("")
            edit = QtWidgets.QLineEdit()
            edit.setFixedWidth(96)
            grid.addWidget(lbl, i + 1, 0)
            grid.addWidget(edit, i + 1, 1)
            self._par_lbls.append(lbl)
            self._par_edits.append(edit)

        # Buttons.
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.update_btn = QtWidgets.QPushButton("Update Filter")
        self.update_btn.clicked.connect(self.accept)
        self.done_btn = QtWidgets.QPushButton("Done")
        self.done_btn.clicked.connect(self.reject)
        buttons.addWidget(self.update_btn)
        buttons.addWidget(self.done_btn)
        grid.addLayout(buttons, 6, 0, 1, 2)

        self.type_cbx.setCurrentIndex(self._type)
        self._refresh_params(reset_values=True)
        self.adjustSize()

    # -- results ----------------------------------------------------------

    @property
    def filter_type_id(self) -> int:
        return int(self.type_cbx.currentIndex())

    @property
    def filter_params(self) -> list[float]:
        out: list[float] = []
        for index, edit in enumerate(self._par_edits, start=1):
            try:
                value = float(edit.text().strip())
            except ValueError as exc:
                raise ValueError(f"filter parameter {index} is not a number") from exc
            if not math.isfinite(value):
                raise ValueError(f"filter parameter {index} must be finite")
            out.append(value)
        return out

    # -- internals --------------------------------------------------------

    def _on_type_changed(self) -> None:
        self._refresh_params()

    def _refresh_params(self, *, reset_values: bool = False) -> None:
        """Mirror UpdateFilterParameter: relabel params, hide 'unused'."""
        type_id = self.type_cbx.currentIndex()
        desc = FILTER_TYPES[type_id][2]
        for i, (lbl, edit) in enumerate(zip(self._par_lbls, self._par_edits)):
            name = desc[i] if i < len(desc) else "unused"
            lbl.setText(name)
            if reset_values:
                edit.setText(f"{self._params[i]:g}" if i < len(self._params) else "0")
            visible = name != "unused"
            lbl.setVisible(visible)
            edit.setVisible(visible)
        self.adjustSize()

    def accept(self) -> None:
        try:
            self._params = self.filter_params
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid filter", str(exc))
            return
        super().accept()
