"""Classic SAMBA_UI look-alike widgets (LEDs, rockers, panels, expanders)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover
    raise ImportError("PySide6 required for GUI: pip install python-samba[gui]") from exc


# Screenshot-oriented SAMBA19xUI palette.
BG = "#e8f1f6"
PANEL = "#f7fafc"
TAB_BG = "#f2f7fb"
TAB_ACTIVE = "#dbeef8"
BORDER = "#b5c8d4"
TEXT = "#203443"
LED_OFF = "#3a3a3a"
LED_GREEN = "#22c55e"
LED_RED = "#ef4444"
LED_GRAY = "#9ca3af"
ROCKER_OFF = "#2a2a2a"
ROCKER_ON = "#1a1a1a"


class LedIndicator(QtWidgets.QWidget):
    """Round status LED (green / red / gray / off). Clickable variant."""

    clicked = QtCore.Signal()

    def __init__(self, diameter: int = 16, parent=None, clickable: bool = False) -> None:
        super().__init__(parent)
        self._color = LED_OFF
        self._diameter = diameter
        self._clickable = clickable
        self.setFixedSize(diameter + 4, diameter + 4)
        if clickable:
            self.setCursor(QtCore.Qt.PointingHandCursor)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def set_on(self, on: bool, color: str = LED_GREEN) -> None:
        self._color = color if on else LED_OFF
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        d = self._diameter
        x = (self.width() - d) // 2
        y = (self.height() - d) // 2
        # outer rim
        p.setPen(QtGui.QPen(QtGui.QColor("#555"), 1))
        grad = QtGui.QRadialGradient(x + d * 0.35, y + d * 0.35, d * 0.7)
        c = QtGui.QColor(self._color)
        grad.setColorAt(0.0, c.lighter(160))
        grad.setColorAt(0.55, c)
        grad.setColorAt(1.0, c.darker(140))
        p.setBrush(QtGui.QBrush(grad))
        p.drawEllipse(x, y, d, d)


class RockerButton(QtWidgets.QToolButton):
    """Black rocker-style On/Off toggle matching classic SAMBA buttons."""

    toggled_text = QtCore.Signal(bool)

    def __init__(self, on_text: str = "On", off_text: str = "Off", parent=None) -> None:
        super().__init__(parent)
        self._on_text = on_text
        self._off_text = off_text
        self.setCheckable(True)
        self.setFixedSize(52, 56)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggled.connect(self._refresh)
        self._refresh(False)

    def _refresh(self, checked: bool) -> None:
        label = self._on_text if checked else self._off_text
        # red LED pip when On, dark when Off
        led = "#ef4444" if checked else "#222"
        self.setText(label)
        self.setStyleSheet(
            f"""
            QToolButton {{
                background-color: #1c1c1c;
                color: #f0f0f0;
                border: 2px solid #555;
                border-radius: 6px;
                font-weight: 700;
                padding-top: 18px;
            }}
            QToolButton:checked {{
                background-color: #111;
                border: 2px solid #777;
            }}
            QToolButton:hover {{
                border-color: #999;
            }}
            """
        )
        # draw a small LED above the text via stylesheet is limited; use icon
        pix = QtGui.QPixmap(14, 14)
        pix.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(led))
        painter.setPen(QtGui.QPen(QtGui.QColor("#333"), 1))
        painter.drawEllipse(1, 1, 12, 12)
        painter.end()
        self.setIcon(QtGui.QIcon(pix))
        self.setIconSize(QtCore.QSize(14, 14))
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.toggled_text.emit(checked)


class FlatPush(QtWidgets.QPushButton):
    """Classic flat gray push button (Up/Down style)."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        # Dialog push buttons must never steal Enter from an active editor.
        # Individual actions are explicit clicks throughout this UI.
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumHeight(30)
        self.setStyleSheet(
            """
            QPushButton {
                background: #f8fbfd;
                border: 1px solid #9db6c5;
                border-radius: 5px;
                padding: 5px 12px;
                color: #28475b;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #eaf5fa; border-color: #5d9abb; }
            QPushButton:pressed { background: #d7ebf3; }
            QPushButton:disabled { color: #8c9ca6; background: #e3ebef; border-color:#c5d2d9; }
            """
        )


class ClassicExpander(QtWidgets.QWidget):
    """Reference-style collapsible section with a clickable arrow/title row."""

    expandedChanged = QtCore.Signal(bool)

    def __init__(
        self,
        title: str,
        content: QtWidgets.QWidget | None = None,
        *,
        expanded: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._expanded = bool(expanded)
        self._content: QtWidgets.QWidget | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        self.header = QtWidgets.QWidget(self)
        self.header.setObjectName("classicExpanderHeader")
        header_row = QtWidgets.QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        self.arrow_button = QtWidgets.QToolButton(self.header)
        self.arrow_button.setObjectName("classicExpanderArrow")
        self.arrow_button.setFixedSize(30, 30)
        self.arrow_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.arrow_button.setStyleSheet(
            "QToolButton { background:#f2f7fb; color:#31566c; border:1px solid #9db6c5;"
            " border-radius:15px; padding:3px; }"
            "QToolButton:hover { background:#ffffff; border-color:#4d8eaf; }"
        )

        self.title_button = QtWidgets.QPushButton(title, self.header)
        self.title_button.setObjectName("classicExpanderTitle")
        self.title_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.title_button.setFlat(True)
        self.title_button.setStyleSheet(
            "QPushButton { color:#31566c; background:transparent; border:none;"
            " text-align:left; padding:0; font-size:16px; font-weight:650; }"
            "QPushButton:hover { color:#1f7199; }"
        )
        header_row.addWidget(self.arrow_button)
        header_row.addWidget(self.title_button, 1)
        root.addWidget(self.header)

        self.arrow_button.clicked.connect(self.toggle)
        self.title_button.clicked.connect(self.toggle)
        if content is not None:
            self.set_content(content)
        self._refresh_state()

    @property
    def content(self) -> QtWidgets.QWidget | None:
        return self._content

    def set_content(self, content: QtWidgets.QWidget) -> None:
        if self._content is content:
            return
        if self._content is not None:
            self.layout().removeWidget(self._content)
            self._content.setParent(None)
        self._content = content
        self.layout().addWidget(content)
        content.setVisible(self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    @QtCore.Slot()
    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    @QtCore.Slot(bool)
    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._refresh_state()
        self.expandedChanged.emit(expanded)

    def _refresh_state(self) -> None:
        self.arrow_button.setArrowType(
            QtCore.Qt.UpArrow if self._expanded else QtCore.Qt.DownArrow
        )
        self.arrow_button.setToolTip("Collapse" if self._expanded else "Expand")
        if self._content is not None:
            self._content.setVisible(self._expanded)
            self._content.updateGeometry()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        # QScrollArea caches the child widget's size hint.  Invalidate the
        # short parent chain so expanding one section cannot overlap the next.
        parent = self.parentWidget()
        for _ in range(3):
            if parent is None:
                break
            parent_layout = parent.layout()
            if parent_layout is not None:
                parent_layout.invalidate()
                parent_layout.activate()
            parent.updateGeometry()
            parent = parent.parentWidget()


class IOSignalButton(FlatPush):
    """Hierarchical SAMBA IOSignal selector carrying real RCI token triples."""

    ioSignalChanged = QtCore.Signal(object)

    SENSOR = 0x0001
    ACTUATOR = 0x0002
    VELOCITY = 0x0004
    EXCITATION = 0x0008
    POSITION = 0x0020
    PNEUMATIC = 0x0100
    FF = 0x0400
    PFF = 0x0800
    POLYNOM = 0x2000
    PROX_CORRECTION = 0x4000
    CORE_SIGNALS = SENSOR | ACTUATOR | VELOCITY | EXCITATION | POSITION | PNEUMATIC
    ALL_SIGNALS = CORE_SIGNALS | FF | PFF | POLYNOM | PROX_CORRECTION

    # Keep this list in the exact order used by SAMBA19xLabels.InputName.
    # Monitor/trace IO uses this 46-entry table, while the AD converter page
    # intentionally uses the shorter 32-entry ADCInputName table.
    INPUT_NAMES = [
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
        "XFF", "YFF", "ZFF", "Prox1", "Prox2", "Prox3", "ProxH1",
        "ProxH2", "ProxH3", "XPOS", "XACC", "YPOS", "YACC",
        "Y2FB", "X3FB", "X4FB", "Y4FB", "Z4FB",
        "Prox1-Off", "Prox2-Off", "Prox3-Off", "ProxH1-Off",
        "ProxH2-Off", "ProxH3-Off", "Zr_XACC", "Zr_YACC", "C_XACC",
        "C_YACC", "XPosRaw", "YPosRaw", "Prox4", "ProxH4",
        "Auxiliary1", "Auxiliary2", "Auxiliary3", "Auxiliary4",
        "Auxiliary5", "Prox4-Off", "ProxH4-Off",
    ]
    TEMPERATURE_NAMES = [
        "OutX1Temp", "OutY1Temp", "OutZ1Temp", "OutX2Temp",
        "OutY2Temp", "OutZ2Temp", "OutX3Temp", "OutY3Temp",
        "OutZ3Temp", "OutX4Temp", "OutY4Temp", "OutZ4Temp",
    ]
    OUTPUT_NAMES = [
        "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
        "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
        "Valve1", "Valve2", "Valve3", "Valve4", "Valve5", "Valve6",
        "Diag0", "Diag1",
    ]
    VELOCITY_AXES = ["Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"]
    POSITION_AXES = [
        "Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
        "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
    ]
    PNEUMATIC_AXES = ["Ztpneu", "Yrpneu", "Xrpneu"]
    PROX_CORRECTIONS = [
        "Prox1", "Prox2", "Prox3", "Prox4",
        "ProxH1", "ProxH2", "ProxH3", "ProxH4",
    ]

    def __init__(
        self,
        text: str | None = None,
        *,
        tokens: tuple[int, int, int] = (0, 0, 0),
        supported_io: int = ALL_SIGNALS,
        position_stages: int = 4,
        parent=None,
    ) -> None:
        super().__init__(text or "", parent)
        self.supported_io = int(supported_io)
        self.position_stages = max(1, int(position_stages))
        self._leaf_actions: list[QtGui.QAction] = []
        self._menu = QtWidgets.QMenu(self)
        self._menu.setStyleSheet(
            "QMenu { background:#f7f6fb; color:#111; border:1px solid #777;"
            " padding:3px; font-size:16px; }"
            "QMenu::item { padding:5px 28px 5px 24px; }"
            "QMenu::item:selected { background:#83c8ed; color:#111; }"
            "QMenu::indicator:checked { background:#7ccf35; border:1px solid #477c1e; }"
        )
        # PySide can collect wrappers for nested QMenus even though Qt has
        # reparented them.  Keep explicit references for the button lifetime.
        self._submenus: list[QtWidgets.QMenu] = [self._menu]
        self._submenu_actions: list[QtGui.QAction] = []
        self._build_menu()
        self._menu.aboutToShow.connect(self._refresh_checks)
        self.setMenu(self._menu)
        self.set_io_signal(tokens, label=text)

    def io_tokens(self) -> tuple[int, int, int]:
        value = self.property("io_tokens")
        if value is None:
            return (0, 0, 0)
        try:
            values = tuple(int(item) for item in value)
        except (TypeError, ValueError):
            return (0, 0, 0)
        return (values + (0, 0, 0))[:3]

    def set_io_signal(
        self, tokens, *, label: str | None = None, emit: bool = False
    ) -> None:
        try:
            values = tuple(int(item) for item in list(tokens)[:3])
        except (TypeError, ValueError):
            return
        values = (values + (0, 0, 0))[:3]
        self.setProperty("io_tokens", values)
        self.setText(
            label
            or self.format_io_signal(
                values, position_stages=self.position_stages
            )
        )
        self.setToolTip(
            f"IOSignal Type={values[0]}, MainIndex={values[1]}, SubIndex={values[2]}"
        )
        self._refresh_checks()
        if emit:
            self.ioSignalChanged.emit(values)

    @classmethod
    def format_io_signal(cls, tokens, *, position_stages: int = 4) -> str:
        io_type, main, sub = (tuple(int(item) for item in tokens) + (0, 0, 0))[:3]
        if io_type == 0 and 0 <= main < len(cls.INPUT_NAMES):
            return cls.INPUT_NAMES[main]
        if io_type == 1 and 0 <= main < len(cls.OUTPUT_NAMES):
            return cls.OUTPUT_NAMES[main]
        if io_type == 12 and 0 <= main < len(cls.TEMPERATURE_NAMES):
            return cls.TEMPERATURE_NAMES[main]
        if io_type == 2 and 0 <= main < len(cls.VELOCITY_AXES):
            if sub == -1:
                stage = "Raw"
            elif sub == 7:
                # Older parameter files can contain the legacy type-2 output
                # encoding.  New selections use canonical type 4 below.
                stage = "Output"
            elif 0 <= sub <= 6:
                stage = f"Stage{sub + 1}"
            else:
                return "Unknown Vel"
            return f"Vel {cls.VELOCITY_AXES[main]} {stage}"
        if io_type == 4 and 0 <= main < len(cls.VELOCITY_AXES):
            return f"Vel {cls.VELOCITY_AXES[main]} Output"
        if io_type == 3:
            return "Excitation"
        if io_type == 5 and 0 <= main < len(cls.POSITION_AXES):
            position_stages = max(1, int(position_stages))
            if sub == -1:
                stage = "Raw"
            elif sub == position_stages:
                stage = "Output"
            elif 0 <= sub < position_stages:
                stage = f"Stage{sub + 1}"
            else:
                return "Unknown Pos"
            return f"Pos {cls.POSITION_AXES[main]} {stage}"
        if io_type == 8 and 0 <= main < len(cls.PNEUMATIC_AXES):
            if sub == -1:
                stage = "Raw"
            elif sub == 4:
                stage = "Output"
            elif 0 <= sub < 4:
                stage = f"Stage{sub + 1}"
            else:
                return "Unknown Pneu"
            return f"Pneu {cls.PNEUMATIC_AXES[main]} {stage}"
        if io_type == 10:
            return f"FF Ch{main + 1} {cls._ff_sub_label(sub, False)}"
        if io_type == 11:
            return f"PFF Ch{main + 1} {cls._ff_sub_label(sub, True)}"
        if io_type == 13:
            return f"Polynom{main + 1}, {'Input' if sub == 0 else 'Output'}"
        if io_type == 14 and 0 <= main < len(cls.PROX_CORRECTIONS):
            return cls.PROX_CORRECTIONS[main]
        return f"{io_type}:{main}:{sub}"

    @classmethod
    def _ff_sub_label(cls, sub: int, pneumatic: bool) -> str:
        if sub < 3:
            return f"RefFil{sub + 1}"
        if sub < 6:
            return f"SecFil{sub - 2}"
        axes = cls.PNEUMATIC_AXES if pneumatic else cls.VELOCITY_AXES
        index = sub - 6
        return (
            f"{axes[index]} Out"
            if 0 <= index < len(axes)
            else f"Output {index + 1}"
        )

    def _add_leaf(
        self, menu: QtWidgets.QMenu, label: str, tokens: tuple[int, int, int]
    ) -> None:
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setData(tokens)
        action.triggered.connect(
            lambda _checked=False, selected=action: self._select_action(selected)
        )
        self._leaf_actions.append(action)

    def _add_submenu(
        self, parent: QtWidgets.QMenu, title: str
    ) -> QtWidgets.QMenu:
        menu = parent.addMenu(title)
        self._submenus.append(menu)
        self._submenu_actions.append(menu.menuAction())
        return menu

    def _add_axis_menu(
        self,
        root: QtWidgets.QMenu,
        axes: list[str],
        io_type: int,
        stage_labels: list[str],
    ) -> None:
        for main, axis in enumerate(axes):
            axis_menu = self._add_submenu(root, axis)
            for sub, label in enumerate(stage_labels):
                self._add_leaf(axis_menu, label, (io_type, main, sub))

    def _build_menu(self) -> None:
        if self.supported_io & self.SENSOR:
            menu = self._add_submenu(self._menu, "Sensor")
            for index, name in enumerate(self.INPUT_NAMES):
                self._add_leaf(menu, name, (0, index, 0))
            temperature = self._add_submenu(self._menu, "Temperature Sensor")
            for index, name in enumerate(self.TEMPERATURE_NAMES):
                self._add_leaf(temperature, name, (12, index, 0))
        if self.supported_io & self.ACTUATOR:
            menu = self._add_submenu(self._menu, "Actuator")
            for index, name in enumerate(self.OUTPUT_NAMES):
                self._add_leaf(menu, name, (1, index, 0))
        if self.supported_io & self.VELOCITY:
            menu = self._add_submenu(self._menu, "Velocity Axes")
            for main, axis in enumerate(self.VELOCITY_AXES):
                axis_menu = self._add_submenu(menu, axis)
                self._add_leaf(axis_menu, "Raw", (2, main, -1))
                for stage in range(7):
                    self._add_leaf(
                        axis_menu, f"Stage{stage + 1}", (2, main, stage)
                    )
                self._add_leaf(axis_menu, "Output", (4, main, 0))
        if self.supported_io & self.POSITION:
            menu = self._add_submenu(self._menu, "Position Axes")
            for main, axis in enumerate(self.POSITION_AXES):
                axis_menu = self._add_submenu(menu, axis)
                self._add_leaf(axis_menu, "Raw", (5, main, -1))
                for stage in range(self.position_stages):
                    self._add_leaf(
                        axis_menu, f"Stage{stage + 1}", (5, main, stage)
                    )
                self._add_leaf(
                    axis_menu, "Output", (5, main, self.position_stages)
                )
        if self.supported_io & self.PNEUMATIC:
            menu = self._add_submenu(self._menu, "Pneumatic Axes")
            for main, axis in enumerate(self.PNEUMATIC_AXES):
                axis_menu = self._add_submenu(menu, axis)
                self._add_leaf(axis_menu, "Raw", (8, main, -1))
                for stage in range(4):
                    self._add_leaf(
                        axis_menu, f"Stage{stage + 1}", (8, main, stage)
                    )
                self._add_leaf(axis_menu, "Output", (8, main, 4))
        if self.supported_io & self.FF:
            root = self._add_submenu(self._menu, "FF")
            for main in range(7):
                channel = self._add_submenu(root, f"FF-Channel{main + 1}")
                ref = self._add_submenu(channel, "Ref. Filter")
                sec = self._add_submenu(channel, "Sec. Filter")
                output = self._add_submenu(channel, "Output")
                for stage in range(3):
                    self._add_leaf(ref, f"Ref. Stage{stage + 1}", (10, main, stage))
                    self._add_leaf(sec, f"Sec. Stage{stage + 1}", (10, main, stage + 3))
                for axis, name in enumerate(self.VELOCITY_AXES):
                    self._add_leaf(output, name, (10, main, axis + 6))
        if self.supported_io & self.PFF:
            root = self._add_submenu(self._menu, "Pneum. FF")
            for main in range(4):
                channel = self._add_submenu(root, f"PFF-Channel{main + 1}")
                ref = self._add_submenu(channel, "Ref. Filter")
                sec = self._add_submenu(channel, "Sec. Filter")
                output = self._add_submenu(channel, "Output")
                for stage in range(3):
                    self._add_leaf(ref, f"Ref. Stage{stage + 1}", (11, main, stage))
                    self._add_leaf(sec, f"Sec. Stage{stage + 1}", (11, main, stage + 3))
                for axis, name in enumerate(self.PNEUMATIC_AXES):
                    self._add_leaf(output, name, (11, main, axis + 6))
        if self.supported_io & self.PROX_CORRECTION:
            menu = self._add_submenu(self._menu, "Prox Corrections")
            for index, name in enumerate(self.PROX_CORRECTIONS):
                self._add_leaf(menu, name, (14, index, 0))
        if self.supported_io & self.POLYNOM:
            root = self._add_submenu(self._menu, "Polynoms")
            for main in range(4):
                menu = self._add_submenu(root, f"Polynom{main + 1}")
                self._add_leaf(menu, "Input", (13, main, 0))
                self._add_leaf(menu, "Output", (13, main, 1))
        if self.supported_io & self.EXCITATION:
            self._add_leaf(self._menu, "Excitation", (3, 0, 0))

    def _select_action(self, action: QtGui.QAction) -> None:
        self.set_io_signal(action.data(), emit=True)

    def _refresh_checks(self) -> None:
        current = self.io_tokens()
        for action in self._leaf_actions:
            try:
                selected = tuple(int(item) for item in action.data()) == current
            except (TypeError, ValueError):
                selected = False
            action.setChecked(selected)


def format_ui_number(value: object) -> str:
    """Render one numeric value without scientific notation.

    Controller/config serialization deliberately keeps its own invariant
    formatting.  This helper is only for text shown to the operator.
    ``Decimal(str(value))`` preserves the shortest decimal representation of
    Python floats while still expanding exponent notation for very small and
    very large values.
    """

    text = str(value).strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if number.is_nan():
        return "NaN"
    if number.is_infinite():
        return "-Inf" if number.is_signed() else "Inf"
    if number.is_zero():
        return "0"
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def format_ui_text(value: object) -> str:
    """Normalize a numeric UI token only when it uses exponent notation."""

    text = str(value)
    if "e" not in text.lower():
        return text
    return format_ui_number(text)


class SciSpin(QtWidgets.QDoubleSpinBox):
    """Scientific-notation friendly spin matching SAMBA numeric fields."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDecimals(6)
        self.setRange(-1e12, 1e12)
        self.setSingleStep(0.1)
        self.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.setMinimumWidth(110)
        self.setStyleSheet(
            """
            QDoubleSpinBox {
                background: #ffffff;
                color: #203443;
                border: 1px solid #9db6c5;
                border-radius: 4px;
                padding: 4px 7px;
                font-size: 14px;
            }
            QDoubleSpinBox:focus { border-color:#4b82a2; }
            """
        )


class SciEdit(QtWidgets.QLineEdit):
    """Read/write numeric text field rendered in fixed decimal notation."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__("", parent)
        self.setText(text)
        self.setMinimumWidth(110)
        self.setStyleSheet(
            """
            QLineEdit {
                background: #ffffff;
                color: #203443;
                border: 1px solid #9db6c5;
                border-radius: 4px;
                padding: 4px 7px;
                font-size: 14px;
            }
            QLineEdit:read-only {
                background: #eef5f8;
                color: #5b7180;
            }
            QLineEdit:focus { border-color:#3d86ad; }
            """
        )

    def setText(self, text: object) -> None:  # noqa: N802
        """Keep arbitrary labels intact and expand numeric exponent strings."""

        super().setText(format_ui_text(text))


class GroupPanel(QtWidgets.QGroupBox):
    """Beveled group box like classic Win32 SAMBA panels."""

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(title, parent)
        self.setStyleSheet(
            """
            QGroupBox {
                background: #f7fafc;
                border: 1px solid #b5c8d4;
                border-radius: 8px;
                margin-top: 12px;
                padding: 10px 8px 8px 8px;
                font-weight: 650;
                font-size: 15px;
                color: #27485d;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #315b72;
                background: #f7fafc;
            }
            """
        )


class FilterStageCell(QtWidgets.QFrame):
    """Clickable blue-top filter stage cell matching classic SAMBA_UI.

    Emits ``clicked(stage_index)`` when the user selects this stage.
    Call ``set_info(type_name, params)`` after a Read to refresh the label.
    """

    clicked = QtCore.Signal(int)

    def __init__(
        self,
        stage_index: int,
        label: str = "",
        width: int = 48,
        height: int = 56,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.stage_index = int(stage_index)
        self._selected = False
        initial = (label or "").strip()
        self._type_name = "---" if initial.upper() in {"NOFIL", "----", "---"} else initial
        self.setFixedSize(width, height)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"Stage {stage_index}: click to select / edit")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(2, 10, 2, 2)
        lay.setSpacing(0)

        self._lab = QtWidgets.QLabel(self._type_name or f"S{stage_index}")
        self._lab.setAlignment(QtCore.Qt.AlignCenter)
        self._lab.setWordWrap(True)
        self._lab.setStyleSheet(
            "border:none; background:transparent; color:#f4f8fc; "
            "font-weight:650; font-size:13px;"
        )
        lay.addWidget(self._lab)
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            border = "#d18b45"
            start = "#5d9abb"
            end = "#376d8b"
        else:
            border = "#8bb4c8"
            start = "#376d8b"
            end = "#244b66"
        self.setStyleSheet(
            f"FilterStageCell {{"
            "  background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"    stop:0 {start}, stop:1 {end});"
            f"  border:1px solid {border};"
            "  border-radius:5px;"
            f"}}"
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._apply_style()

    def is_selected(self) -> bool:
        return self._selected

    def set_info(self, type_name: str = "", short: str | None = None) -> None:
        """Update the caption (e.g. 'HOPT', 'PID', 'LPF1O')."""
        name = (short or type_name or "").strip()
        if name.upper() in {"NOFIL", "----", "---"}:
            name = "---"
        self._type_name = name
        self._lab.setText(name if name else f"S{self.stage_index}")
        tip = f"Stage {self.stage_index}"
        if type_name:
            tip += f": {type_name}"
        self.setToolTip(tip + " — click to edit")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.stage_index)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space):
            self.clicked.emit(self.stage_index)
            return
        super().keyPressEvent(event)


class FilterStageBar(QtWidgets.QWidget):
    """Row of clickable :class:`FilterStageCell` widgets with single selection."""

    stage_selected = QtCore.Signal(int)

    def __init__(
        self,
        n_stages: int,
        labels: list[str] | None = None,
        cell_w: int = 48,
        cell_h: int = 56,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cells: list[FilterStageCell] = []
        self._current = -1
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        labels = list(labels or [])
        for i in range(n_stages):
            lab = labels[i] if i < len(labels) else ""
            cell = FilterStageCell(i, lab, width=cell_w, height=cell_h)
            cell.clicked.connect(self._on_clicked)
            self._cells.append(cell)
            row.addWidget(cell)
        row.addStretch(1)

    @property
    def cells(self) -> list[FilterStageCell]:
        return self._cells

    def current_stage(self) -> int:
        return self._current

    def set_current(self, stage: int, *, emit: bool = False) -> None:
        self._current = int(stage)
        for c in self._cells:
            c.set_selected(c.stage_index == self._current)
        if emit and 0 <= self._current < len(self._cells):
            self.stage_selected.emit(self._current)

    def set_stage_info(self, stage: int, type_name: str) -> None:
        if 0 <= stage < len(self._cells):
            # short label: keep classic short names when possible
            short = type_name
            if len(short) > 6:
                short = short[:6]
            self._cells[stage].set_info(type_name, short=short)

    def _on_clicked(self, stage: int) -> None:
        self.set_current(stage)
        self.stage_selected.emit(stage)


class ClassicFilterPanel(QtWidgets.QGroupBox):
    """Inline classic filter editor: type + 5 params, Read/Write buttons.

    Mirrors a :class:`~python_samba.ui.widgets.FilterEditor` so existing
    RCI handlers keep working; also usable stand-alone.
    """

    read_clicked = QtCore.Signal()
    write_clicked = QtCore.Signal()
    stage_changed = QtCore.Signal()

    def __init__(self, title: str = "Filter parameters", parent=None) -> None:
        super().__init__(title, parent)
        from python_samba.protocol.codes import FilterType

        self.setStyleSheet(
            """
            QGroupBox {
                background: #f7fafc;
                border: 1px solid #b5c8d4;
                border-radius: 8px;
                margin-top: 12px;
                padding: 10px 8px 8px 8px;
                font-weight: 650;
                font-size: 15px;
                color: #27485d;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #315b72;
                background: #f7fafc;
            }
            """
        )
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(8, 14, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)

        self.stage_lbl = QtWidgets.QLabel("Stage 0")
        self.stage_lbl.setStyleSheet("font-weight:700; color:#1c4f6b;")
        form.addRow("Selected:", self.stage_lbl)

        self.ftype = QtWidgets.QComboBox()
        for ft in FilterType:
            self.ftype.addItem(f"{int(ft)} {ft.name}", int(ft))
        self.ftype.setMinimumWidth(160)
        form.addRow("Type:", self.ftype)

        self.params: list[QtWidgets.QDoubleSpinBox] = []
        for i in range(5):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setDecimals(6)
            sp.setRange(-1e9, 1e9)
            sp.setSingleStep(0.01)
            sp.setMinimumWidth(130)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
            sp.setStyleSheet(
                "QDoubleSpinBox { background:#fff; color:#203443; font-size:14px; "
                "border:1px solid #9db6c5; border-radius:4px; padding:4px 7px; }"
            )
            form.addRow(f"P{i + 1}:", sp)
            self.params.append(sp)

        btns = QtWidgets.QHBoxLayout()
        self.btn_read = FlatPush("Read")
        self.btn_write = FlatPush("Write...")
        self.btn_read.clicked.connect(self.read_clicked.emit)
        self.btn_write.clicked.connect(self.write_clicked.emit)
        btns.addWidget(self.btn_read)
        btns.addWidget(self.btn_write)
        btns.addStretch(1)
        form.addRow(btns)

        self.ftype.currentIndexChanged.connect(self.stage_changed)
        for sp in self.params:
            sp.valueChanged.connect(self.stage_changed)

    def set_stage_index(self, stage: int) -> None:
        self.stage_lbl.setText(f"Stage {int(stage)}")

    def set_from_filter_editor(self, editor) -> None:
        """Copy values from a FilterEditor into this panel."""
        self.ftype.blockSignals(True)
        for sp in self.params:
            sp.blockSignals(True)
        try:
            tidx = self.ftype.findData(int(editor.ftype.currentData()))
            if tidx >= 0:
                self.ftype.setCurrentIndex(tidx)
            for sp, src in zip(self.params, editor.params):
                sp.setValue(float(src.value()))
            self.set_stage_index(editor.stage_index())
        finally:
            self.ftype.blockSignals(False)
            for sp in self.params:
                sp.blockSignals(False)

    def apply_to_filter_editor(self, editor) -> None:
        """Push panel values into a FilterEditor (axis/stage already set)."""
        editor.ftype.blockSignals(True)
        for sp in editor.params:
            sp.blockSignals(True)
        try:
            tidx = editor.ftype.findData(int(self.ftype.currentData()))
            if tidx >= 0:
                editor.ftype.setCurrentIndex(tidx)
            for dest, sp in zip(editor.params, self.params):
                dest.setValue(float(sp.value()))
        finally:
            editor.ftype.blockSignals(False)
            for sp in editor.params:
                sp.blockSignals(False)

    def set_filter_type(self, filter_type: int) -> None:
        tidx = self.ftype.findData(int(filter_type))
        if tidx >= 0:
            self.ftype.blockSignals(True)
            self.ftype.setCurrentIndex(tidx)
            self.ftype.blockSignals(False)

    def set_params(self, params) -> None:
        for sp, val in zip(self.params, params):
            sp.blockSignals(True)
            sp.setValue(float(val))
            sp.blockSignals(False)

    def filter_type(self) -> int:
        return int(self.ftype.currentData())

    def param_values(self) -> tuple[float, float, float, float, float]:
        vals = tuple(float(sp.value()) for sp in self.params)
        while len(vals) < 5:
            vals = vals + (0.0,)
        return vals[:5]  # type: ignore[return-value]


def hline() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Sunken)
    return line


def labeled_row(label: str, widget: QtWidgets.QWidget, unit: str = "") -> QtWidgets.QWidget:
    w = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lab = QtWidgets.QLabel(label)
    lab.setMinimumWidth(110)
    row.addWidget(lab)
    row.addWidget(widget, 1)
    if unit:
        row.addWidget(QtWidgets.QLabel(unit))
    return w
