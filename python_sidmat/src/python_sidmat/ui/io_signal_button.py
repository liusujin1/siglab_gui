"""IO signal selection button — port of ``SAMBA19xUI.UserControls.IOSignalBtn``.

A button showing the currently selected IO signal name; clicking opens a
two-level menu (signal type → individual signals).  Emits ``ioChanged`` when a
new (Type, MainIndex, SubIndex) triple is picked.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from python_sidmat.backend.iosignal import (
    IOType,
    IO_TYPE_NAMES,
    io_signal_list,
    io_type_name,
)

__all__ = ["IOSignalButton"]


class IOSignalButton(QtWidgets.QToolButton):
    """QToolButton with a signal-picker popup menu."""

    ioChanged = QtCore.Signal(object)  # IOType

    def __init__(
        self,
        io: IOType | None = None,
        *,
        supported_types: tuple[int, ...] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ioSelectorButton")
        # Signal names are descriptive and can be wider than the compact
        # measurement sidebar.  Let the button follow its cell width; the
        # tooltip/menu still expose the complete signal name.
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._io = io if io is not None else IOType()
        self._supported_types = (
            tuple(IO_TYPE_NAMES.keys())
            if supported_types is None
            else tuple(supported_types)
        )
        self.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._refresh_text()
        menu = QtWidgets.QMenu(self)
        self.setMenu(menu)
        self._rebuild_menu()

    # -- IO accessors -----------------------------------------------------

    def io_type(self) -> IOType:
        return self._io

    def set_io(self, io: IOType, *, emit: bool = True) -> None:
        self._io = io
        self._refresh_text()
        if emit:
            self.ioChanged.emit(io)

    def refresh_signals(self) -> None:
        """Refresh the current label and picker after NGEXL count changes."""

        self._io.name = io_type_name(self._io)
        self._refresh_text()
        self._rebuild_menu()

    # -- internals --------------------------------------------------------

    def _refresh_text(self) -> None:
        self.setText(self._io.name or io_type_name(self._io))
        self.setToolTip(
            f"Type {self._io.type}: {self._io.name} "
            f"({self._io.main_index}, {self._io.sub_index})"
        )

    def _rebuild_menu(self) -> None:
        menu = self.menu()
        menu.clear()
        for io_type in self._supported_types:
            if io_type not in IO_TYPE_NAMES:
                continue
            signals = io_signal_list(io_type)
            if not signals:
                continue
            type_menu = menu.addMenu(IO_TYPE_NAMES[io_type])
            for io in signals:
                action = type_menu.addAction(io.name)
                action.setData((io.type, io.main_index, io.sub_index))
                action.triggered.connect(
                    lambda _checked=False, data=action.data(): self._pick(data)
                )

    def _pick(self, data: tuple[int, int, int]) -> None:
        self.set_io(IOType(*data))
