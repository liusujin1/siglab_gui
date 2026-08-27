"""SAMBA19xUI-compatible visual theme for the Sidmat widgets.

The controller application and Sidmat share the same operator workflow.  Keep
their visual language in one place so a new control does not accidentally
reintroduce the old green/gray ad-hoc styling.
"""

from __future__ import annotations

import re

from PySide6 import QtGui, QtWidgets

__all__ = ["SAMBA_UI_STYLESHEET", "apply_samba_theme"]


SAMBA_UI_STYLESHEET = r"""
/* === SAMBA19xUI operator theme =========================================
   Font sizes intentionally use the same source metrics as python_samba.
   apply_samba_theme() applies the same monitor-aware font multiplier. */

QMainWindow, QWidget {
    color: #203443;
    font-family: "Segoe UI", "Microsoft YaHei UI", "Arial", sans-serif;
    font-size: 13px;
}

QMainWindow, QWidget#sidmatRoot, QWidget#sidmatWorkspace,
QWidget#sidmatBody,
QStackedWidget, QStackedWidget > QWidget,
QScrollArea#sidmatScroll, QScrollArea#sidmatScroll > QWidget > QWidget {
    background: #e8f1f6;
}
QWidget#sidmatRoot { border: 1px solid #173047; }

QFrame#applicationHeader {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #173047, stop:0.62 #244b66, stop:1 #376d8b);
    border: none;
    border-bottom: 2px solid #78b5d2;
}
QLabel#brandMark {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #102638, stop:1 #315b76);
    color: #ffffff;
    border: 1px solid #a8d0e4;
    border-radius: 5px;
    font-size: 14px;
    font-weight: 800;
    font-style: italic;
}
QLabel#applicationTitle {
    color: #ffffff;
    font-size: 24px;
    font-weight: 800;
}
QLabel#applicationSubtitle {
    color: #cbe2ee;
    font-size: 14px;
    font-weight: 500;
}
QToolButton#windowControlButton {
    min-width: 42px;
    max-width: 42px;
    min-height: 36px;
    max-height: 36px;
    color: #ffffff;
    background: rgba(10, 30, 45, 150);
    border: 1px solid #9cc7dc;
    border-radius: 6px;
    padding: 0;
    font-size: 20px;
    font-weight: 700;
}
QToolButton#windowControlButton:hover { background: #3a7190; }
QToolButton#windowControlButton[closeButton="true"]:hover {
    background: #b83030;
    border-color: #dc7777;
}
QToolButton#headerMenuButton {
    color: #ffffff;
    background: rgba(10, 30, 45, 120);
    border: 1px solid #9cc7dc;
    border-radius: 6px;
    padding: 0 8px;
    font-size: 14px;
    font-weight: 650;
}
QToolButton#headerMenuButton:hover { background: #3a7190; }
QLabel#connectionStateLabel {
    color: #ffffff;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #4f7185, stop:0.45 #294252, stop:1 #1b2e3b);
    border: 1px solid #6b91a5;
    border-radius: 4px;
    font-size: 14px;
    font-weight: 700;
    padding: 2px 6px;
}
QLabel#connectionStateLabel[connected="true"] {
    color: #b9f2cb;
    border-color: #6fa982;
}

QWidget#sidmatSidebar {
    background: #263c4c;
    border-right: 1px solid #152936;
}
QScrollArea#sidmatScroll,
QScrollArea#sidmatScroll > QWidget > QWidget {
    background: #263c4c;
}
QWidget#sidmatLeftStack {
    background: #263c4c;
}
QWidget#sidmatSection {
    color: #28475b;
    background: #f7fafc;
    border: 1px solid #b5c8d4;
    border-radius: 8px;
}
QWidget#sidmatSectionContent { background: #f7fafc; }
QFrame#sectionDivider {
    color: #b5c8d4;
    background: #b5c8d4;
    max-height: 1px;
}

QMenuBar {
    color: #203443;
    background: #ffffff;
    border-bottom: 1px solid #b5c8d4;
    padding: 2px 6px;
    font-size: 13px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background: #dbeef8;
    color: #1f7199;
}

QMenu {
    color: #203443;
    background: #ffffff;
    border: 1px solid #9db6c5;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #2f789e;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #d6e3e9;
    margin: 4px 6px;
}

QStatusBar {
    color: #526b7a;
    background: #ffffff;
    border-top: 1px solid #b5c8d4;
    font-size: 13px;
}
QStatusBar::item { border: none; }

QSplitter::handle {
    background: #d8e5ec;
    border: 1px solid #b5c8d4;
}
QSplitter::handle:hover {
    background: #6e9bb1;
}

QScrollArea { border: none; background: #e8f1f6; }
QScrollBar:vertical, QScrollBar:horizontal {
    background: #d8e5ec;
    border: none;
}
QScrollBar:vertical { width: 12px; margin: 2px; }
QScrollBar:horizontal { height: 12px; margin: 2px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #91afbf;
    border-radius: 5px;
    min-height: 28px;
    min-width: 28px;
}
QScrollBar::handle:hover {
    background: #6e9bb1;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
    border: none;
}

/* PushButtons */
QPushButton {
    color: #28475b;
    background: #f8fbfd;
    border: 1px solid #9db6c5;
    border-radius: 5px;
    padding: 5px 10px;
    min-width: 0px;
    min-height: 30px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: #eaf5fa;
    border-color: #5d9abb;
    color: #1f5877;
}
QPushButton:pressed {
    background: #d7ebf3;
    border-color: #3d86ad;
}
QPushButton:focus {
    border-color: #3d86ad;
}
QPushButton:checked {
    color: #ffffff;
    background: #58ad2b;
    border-color: #4f9b61;
}
QPushButton:disabled {
    color: #8c9ca6;
    background: #e3ebef;
    border-color: #c5d2d9;
}

QPushButton#primaryAction {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2f80b2, stop:1 #23678e);
    border: 1px solid #23678e;
}
QPushButton#primaryAction:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3f91d8, stop:1 #2f789e);
    border-color: #2f789e;
}
QPushButton#primaryAction:pressed {
    background: #1b5678;
}
QPushButton#primaryAction:checked {
    color: #ffffff;
    background: #58ad2b;
    border-color: #4f9b61;
}

/* ToolButtons */
QToolButton {
    color: #28475b;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px 6px;
    font-size: 13px;
}
QToolButton:hover {
    background: #eaf5fa;
    border-color: #5d9abb;
    color: #1f7199;
}
QToolButton:pressed {
    background: #d7ebf3;
}
QToolButton:checked {
    color: #ffffff;
    background: #2f789e;
    border-color: #23678e;
}

QWidget#sidmatActionBar {
    background: #263c4c;
    border-bottom: 1px solid #152936;
}
QLabel#actionBarTitle {
    color: #bcd7e5;
    font-size: 14px;
    font-weight: 750;
    letter-spacing: 1px;
}
QPushButton#runViewButton,
QPushButton#runSecondaryButton,
QToolButton#runMenuButton {
    color: #e9f3f8;
    background: #304d60;
    border: 1px solid #486a7d;
    border-radius: 5px;
    min-height: 28px;
    padding: 1px 6px;
    font-size: 14px;
    font-weight: 700;
}
QPushButton#runViewButton:hover,
QPushButton#runSecondaryButton:hover,
QToolButton#runMenuButton:hover {
    color: #ffffff;
    background: #3b5d72;
    border-color: #8ac4df;
}
QPushButton#runViewButton:checked {
    color: #1c4056;
    background: #e2f0f7;
    border-color: #9cc9dd;
    border-left: 3px solid #4f9ac0;
}
QPushButton#quickStartButton {
    color: #ffffff;
    background: #2f789e;
    border: 1px solid #8fc4db;
    border-radius: 5px;
    min-height: 28px;
    padding: 1px 8px;
    font-size: 14px;
    font-weight: 750;
}
QPushButton#quickStartButton:hover { background: #3f91d8; }
QPushButton#quickStartButton[measuring="true"] {
    background: #a93a3a;
    border-color: #d47a7a;
}
QWidget#sidmatPlotToolbar {
    background: #dbeaf1;
    border-bottom: 1px solid #b5c8d4;
}
QLabel#plotMarkerReadout {
    color: #31566c;
    background: #e7f3f9;
    border: 1px solid #a9c7d7;
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 14px;
    font-weight: 600;
}
QFrame#plotToolbarDivider {
    color: #8aa7b7;
    background: #8aa7b7;
    max-width: 1px;
}
QToolButton#sectionHeader {
    color: #e9f3f8;
    background: #304d60;
    border: 1px solid #486a7d;
    border-left: 3px solid transparent;
    border-radius: 5px;
    padding: 3px 10px;
    min-height: 34px;
    text-align: left;
    font-size: 14px;
    font-weight: 650;
}
QToolButton#sectionHeader:hover {
    background: #3b5d72;
    border-left-color: #8ac4df;
}
QToolButton#sectionHeader:checked {
    color: #1c4056;
    background: #e2f0f7;
    border-color: #9cc9dd;
    border-left-color: #4f9ac0;
}

QToolButton#toolbarButton {
    color: #e9f3f8;
    background: #304d60;
    border: 1px solid #486a7d;
    border-radius: 5px;
    padding: 0 2px;
    min-width: 0px;
    min-height: 26px;
    font-size: 13px;
    font-weight: 650;
}
QToolButton#toolbarButton:hover {
    background: #3b5d72;
    border-color: #8ac4df;
    color: #ffffff;
}
QToolButton#toolbarButton:pressed { background: #244b66; }

QToolButton#plotToolButton {
    color: #28475b;
    background: #f8fbfd;
    border: 1px solid #9db6c5;
    border-radius: 5px;
    padding: 2px 8px;
    min-height: 30px;
    font-size: 14px;
    font-weight: 600;
}
QToolButton#plotToolButton:hover {
    background: #eaf5fa;
    border-color: #5d9abb;
    color: #1f5877;
}
QToolButton#plotToolButton:pressed { background: #d7ebf3; }

QToolButton#ioSelectorButton {
    background: #ffffff;
    color: #315b72;
    border: 1px solid #9db6c5;
    border-radius: 5px;
    padding: 4px 7px;
    min-height: 27px;
    font-weight: 600;
    text-align: left;
}
QToolButton#ioSelectorButton:hover {
    background: #eaf5fa;
    border-color: #5d9abb;
}
QToolButton#ioSelectorButton:pressed {
    background: #d7ebf3;
}

/* Inputs & Form Controls */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QPlainTextEdit, QTextEdit {
    color: #203443;
    background: #ffffff;
    border: 1px solid #9db6c5;
    border-radius: 4px;
    padding: 4px 7px;
    min-height: 27px;
    selection-background-color: #b9ddea;
    font-size: 14px;
}
QLineEdit:read-only, QSpinBox:read-only, QDoubleSpinBox:read-only {
    background: #eef5f8;
    color: #5b7180;
    border-color: #c5d2d9;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #3d86ad;
    background: #ffffff;
}

QComboBox { padding-right: 24px; }
QComboBox::drop-down {
    width: 22px;
    border-left: 1px solid #c3d2db;
    background: #edf5f8;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}
QComboBox QAbstractItemView {
    color: #203443;
    background: #ffffff;
    selection-background-color: #2f789e;
    selection-color: #ffffff;
    border: 1px solid #b5c8d4;
}

/* GroupBoxes & Cards */
QGroupBox {
    color: #27485d;
    background: #f7fafc;
    border: 1px solid #b5c8d4;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-size: 15px;
    font-weight: 650;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #315b72;
    background: #f7fafc;
}

QLabel {
    color: #28475b;
    background: transparent;
}
QLabel#plotTitle {
    color: #315b72;
    font-size: 15px;
    font-weight: 650;
    letter-spacing: 0.5px;
}

QWidget#sidmatSidebar QLabel,
QWidget#sidmatSidebar QLabel#sidebarText {
    color: #28475b;
}
QWidget#sidmatSidebar QLabel#actionBarTitle { color: #bcd7e5; }
QWidget#sidmatSidebar QGroupBox QLabel,
QWidget#sidmatSidebar QGroupBox QCheckBox {
    color: #28475b;
}
QWidget#sidmatSidebar QCheckBox { color: #28475b; }

QCheckBox { spacing: 6px; background: transparent; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 2px solid #8c8c8c;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #3d86ad; }
QCheckBox::indicator:checked {
    background: #58ad2b;
    border-color: #4f9b61;
}

/* Custom Axis Toggle & Filter Buttons */
QPushButton#axisToggle {
    color: #475569;
    background: #ffffff;
    border: 1px solid #b5c8d4;
    border-radius: 6px;
    min-height: 20px;
    padding: 1px 6px;
    font-weight: 700;
}
QPushButton#axisToggle:hover {
    border-color: #3d86ad;
    color: #315b72;
}
QPushButton#axisToggle:checked {
    color: #ffffff;
    background: #58ad2b;
    border-color: #4f9b61;
}

QPushButton#filterStageButton {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #376d8b, stop:1 #244b66);
    border: 1px solid #8bb4c8;
    border-radius: 5px;
    font-weight: 700;
}
QPushButton#filterStageButton:hover {
    background: #376d8b;
}
QPushButton#filterStageButton:pressed { background: #1e3a8a; }

QDialog {
    background: #e8f1f6;
    color: #203443;
}
"""


_FONT_SIZE_PATTERN = re.compile(
    r"(font-size\s*:\s*)(\d+(?:\.\d+)?)px", re.IGNORECASE
)


def _font_pixel_size(original: float, font_scale: float) -> int:
    """Use the same normal-text scaling and caps as current python_samba."""

    value = float(original)
    if value > 22:
        return int(round(value))
    if value <= 13:
        return 12
    return min(20, max(12, int(round(value * 0.85 * font_scale))))


def _scaled_stylesheet(stylesheet: str, font_scale: float) -> str:
    return _FONT_SIZE_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}"
            f"{_font_pixel_size(float(match.group(2)), font_scale)}px"
        ),
        stylesheet,
    )


def apply_samba_theme(
    app: QtWidgets.QApplication | None = None,
    *,
    font_scale: float | None = None,
) -> None:
    """Apply the Samba palette and its monitor-aware font scaling."""

    application = app or QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError("apply_samba_theme requires a QApplication")
    if font_scale is None:
        font_scale = float(application.property("python_samba_font_scale") or 1.0)
    font_scale = 1.0
    application.setProperty("python_samba_font_scale", font_scale)
    application.setStyle("Fusion")
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(12)
    application.setFont(font)
    application.setStyleSheet(_scaled_stylesheet(SAMBA_UI_STYLESHEET, font_scale))
