"""SAMBA19xUI-compatible visual theme for the Sidmat widgets.

The controller application and Sidmat share the same operator workflow.  Keep
their visual language in one place so a new control does not accidentally
reintroduce the old green/gray ad-hoc styling.
"""

from __future__ import annotations

from PySide6 import QtWidgets

__all__ = ["SAMBA_UI_STYLESHEET", "apply_samba_theme"]


SAMBA_UI_STYLESHEET = r"""
/* === Clean Light Industrial & Scientific Theme for Sidmat / Samba === */

QMainWindow, QWidget {
    color: #1e293b;
    font-family: "Segoe UI", "Inter", "Microsoft YaHei UI", -apple-system, sans-serif;
    font-size: 12px;
}

QMainWindow, QWidget#sidmatRoot, QWidget#sidmatWorkspace,
QStackedWidget, QStackedWidget > QWidget {
    background: #f1f5f9;
}

QWidget#sidmatSidebar, QWidget#sidmatLeftStack,
QScrollArea#sidmatScroll, QScrollArea#sidmatScroll > QWidget > QWidget {
    background: #ffffff;
    border-right: 1px solid #cbd5e1;
}

QMenuBar {
    color: #1e293b;
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    padding: 2px 6px;
    font-size: 12px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background: #eff6ff;
    color: #2563eb;
}

QMenu {
    color: #0f172a;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #2563eb;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #e2e8f0;
    margin: 4px 6px;
}

QStatusBar {
    color: #475569;
    background: #ffffff;
    border-top: 1px solid #e2e8f0;
    font-size: 11px;
}
QStatusBar::item { border: none; }

QSplitter::handle {
    background: #e2e8f0;
    border: 1px solid #cbd5e1;
}
QSplitter::handle:hover {
    background: #2563eb;
}

QScrollArea { border: none; background: #f1f5f9; }
QScrollBar:vertical, QScrollBar:horizontal {
    background: #f1f5f9;
    border: none;
}
QScrollBar:vertical { width: 10px; margin: 0; }
QScrollBar:horizontal { height: 10px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #cbd5e1;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #2563eb;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
    border: none;
}

/* PushButtons */
QPushButton {
    color: #1e293b;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 12px;
    min-height: 24px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background: #f8fafc;
    border-color: #2563eb;
    color: #1d4ed8;
}
QPushButton:pressed {
    background: #eff6ff;
    border-color: #1d4ed8;
}
QPushButton:focus {
    border-color: #2563eb;
}
QPushButton:checked {
    color: #ffffff;
    background: #16a34a;
    border-color: #15803d;
}
QPushButton:disabled {
    color: #94a3b8;
    background: #f1f5f9;
    border-color: #e2e8f0;
}

QPushButton#primaryAction {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2563eb, stop:1 #1d4ed8);
    border: 1px solid #1d4ed8;
}
QPushButton#primaryAction:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d4ed8, stop:1 #1e40af);
    border-color: #1e40af;
}
QPushButton#primaryAction:pressed {
    background: #1e3a8a;
}
QPushButton#primaryAction:checked {
    color: #ffffff;
    background: #16a34a;
    border-color: #15803d;
}

/* ToolButtons */
QToolButton {
    color: #1e293b;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px 6px;
    font-size: 12px;
}
QToolButton:hover {
    background: #f1f5f9;
    border-color: #cbd5e1;
    color: #2563eb;
}
QToolButton:pressed {
    background: #e2e8f0;
}
QToolButton:checked {
    color: #ffffff;
    background: #2563eb;
    border-color: #1d4ed8;
}

QToolButton#sectionHeader {
    color: #1e40af;
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    border-left: 3px solid #2563eb;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 24px;
    text-align: left;
    font-size: 12px;
    font-weight: 700;
}
QToolButton#sectionHeader:hover {
    background: #dbeafe;
    border-color: #3b82f6;
}
QToolButton#sectionHeader:checked {
    color: #ffffff;
    background: #2563eb;
    border-color: #1d4ed8;
    border-left-color: #1d4ed8;
}

QToolButton#toolbarButton {
    color: #334155;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 2px 6px;
    font-size: 12px;
}
QToolButton#toolbarButton:hover {
    background: #f8fafc;
    border-color: #2563eb;
    color: #2563eb;
}
QToolButton#toolbarButton:pressed { background: #e2e8f0; }

QToolButton#ioSelectorButton {
    background: #ffffff;
    color: #2563eb;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 3px 8px;
    min-height: 22px;
    font-weight: 600;
    text-align: left;
}
QToolButton#ioSelectorButton:hover {
    background: #eff6ff;
    border-color: #2563eb;
}

/* Inputs & Form Controls */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QPlainTextEdit, QTextEdit {
    color: #0f172a;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: 3px 6px;
    min-height: 22px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
    font-size: 12px;
}
QLineEdit:read-only, QSpinBox:read-only, QDoubleSpinBox:read-only {
    background: #f8fafc;
    color: #64748b;
    border-color: #e2e8f0;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #2563eb;
    background: #ffffff;
}

QComboBox { padding-right: 24px; }
QComboBox::drop-down {
    width: 22px;
    border-left: 1px solid #cbd5e1;
    background: #f8fafc;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}
QComboBox QAbstractItemView {
    color: #0f172a;
    background: #ffffff;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
    border: 1px solid #cbd5e1;
}

/* GroupBoxes & Cards */
QGroupBox {
    color: #1e40af;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    font-size: 12px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #1e40af;
    background: #ffffff;
}

QLabel {
    color: #334155;
    background: transparent;
}
QLabel#plotTitle {
    color: #1e40af;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
}

QWidget#sidmatSidebar QLabel,
QWidget#sidmatSidebar QLabel#sidebarText {
    color: #1e293b;
}
QWidget#sidmatSidebar QGroupBox QLabel,
QWidget#sidmatSidebar QGroupBox QCheckBox {
    color: #334155;
}
QWidget#sidmatSidebar QCheckBox { color: #1e293b; }

QCheckBox { spacing: 6px; background: transparent; }
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #cbd5e1;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #2563eb; }
QCheckBox::indicator:checked {
    background: #16a34a;
    border-color: #15803d;
}

/* Custom Axis Toggle & Filter Buttons */
QPushButton#axisToggle {
    color: #475569;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    min-height: 20px;
    padding: 1px 6px;
    font-weight: 700;
}
QPushButton#axisToggle:hover {
    border-color: #2563eb;
    color: #1d4ed8;
}
QPushButton#axisToggle:checked {
    color: #ffffff;
    background: #16a34a;
    border-color: #15803d;
}

QPushButton#filterStageButton {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2563eb, stop:1 #1d4ed8);
    border: 1px solid #1d4ed8;
    border-radius: 5px;
    font-weight: 700;
}
QPushButton#filterStageButton:hover {
    background: #1d4ed8;
}
QPushButton#filterStageButton:pressed { background: #1e3a8a; }

QDialog {
    background: #f1f5f9;
    color: #0f172a;
}
"""


def apply_samba_theme(app: QtWidgets.QApplication | None = None) -> None:
    """Apply the common SAMBA UI palette to an application instance."""

    application = app or QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError("apply_samba_theme requires a QApplication")
    application.setStyle("Fusion")
    application.setStyleSheet(SAMBA_UI_STYLESHEET)
