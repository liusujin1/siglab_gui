from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from python_vna.diagnostic.pages import (
    ModalShapePage,
    TraceAnalysisPage,
    VibrationAnalysisPage,
)
from python_vna.optional import require
from python_vna.ui.analysis_viewer import AnalysisWorkbench

QtCore = require("PySide6.QtCore", "python -m pip install -e .[gui]")
QtGui = require("PySide6.QtGui", "python -m pip install -e .[gui]")
QtWidgets = require("PySide6.QtWidgets", "python -m pip install -e .[gui]")


@dataclass(slots=True)
class DiagnosticPageSpec:
    title: str
    subtitle: str
    widget: QtWidgets.QWidget


class DiagnosticMainWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None, *, startup_paths: list[Path] | None = None):
        super().__init__(parent)
        self.setWindowTitle("振动诊断软件")
        self.setMinimumSize(760, 520)
        self._theme = default_diagnostic_theme()
        self._build_ui()
        self._resize_to_available_screen()
        self.apply_theme(self._theme)
        if startup_paths:
            self.load_startup_paths(startup_paths)

    def _resize_to_available_screen(self) -> None:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1180, 720)
            return
        available = screen.availableGeometry()
        width = min(1240, max(760, int(available.width() * 0.90)), max(640, available.width() - 40))
        height = min(860, max(520, int(available.height() * 0.88)), max(480, available.height() - 40))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav_list = QtWidgets.QListWidget()
        self.nav_list.setObjectName("diagnosticNav")
        self.nav_list.setFixedWidth(230)
        self.nav_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.nav_list)

        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.analysis_page = AnalysisWorkbench(derived_only=False)
        self.vibration_page = VibrationAnalysisPage()
        self.trace_page = TraceAnalysisPage()
        self.modal_page = ModalShapePage()
        self.analysis_page.statusBar().messageChanged.connect(self.statusBar().showMessage)
        self.analysis_page.statusBar().hide()
        self.pages: list[DiagnosticPageSpec] = [
            DiagnosticPageSpec("VNA数据分析", "VNA 数据 / 换算", self.analysis_page),
            DiagnosticPageSpec("上位机数据分析", "频响 / 日志", self.vibration_page),
            DiagnosticPageSpec("减振器软件测试数据分析", "IDE / HAC 数据", self.trace_page),
            DiagnosticPageSpec("模态振型", "模态识别", self.modal_page),
        ]

        for spec in self.pages:
            item = QtWidgets.QListWidgetItem(spec.title)
            item.setToolTip(spec.subtitle)
            self.nav_list.addItem(item)
            self.stack.addWidget(spec.widget)
            if hasattr(spec.widget, "statusChanged"):
                spec.widget.statusChanged.connect(self.statusBar().showMessage)

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav_list.setCurrentRow(0)
        self.statusBar().showMessage("就绪")

    def apply_theme(self, theme: dict[str, object]) -> None:
        self._theme = dict(theme)
        self.setStyleSheet(_theme_stylesheet(self._theme))
        for spec in self.pages:
            if hasattr(spec.widget, "apply_theme"):
                spec.widget.apply_theme(self._theme)

    def load_startup_paths(self, paths: list[Path]) -> None:
        analysis_paths: list[Path] = []
        vibration_paths: list[Path] = []
        trace_paths: list[Path] = []
        modal_paths: list[Path] = []
        for path in paths:
            suffix = path.suffix.lower()
            if suffix in {".vna", ".mat"}:
                analysis_paths.append(path)
                modal_paths.append(path)
            elif suffix == ".csv":
                trace_paths.append(path)
                vibration_paths.append(path)
            elif suffix in {".txt", ".dat"}:
                analysis_paths.append(path)
                vibration_paths.append(path)
        if analysis_paths and hasattr(self.analysis_page, "_load_paths"):
            self.analysis_page._load_paths(analysis_paths, quiet_failures=True)
        if vibration_paths:
            self.vibration_page.load_paths(vibration_paths)
        if trace_paths:
            self.trace_page.load_paths(trace_paths)
        if modal_paths:
            self.modal_page.load_paths(modal_paths)
        self.statusBar().showMessage(f"已加载启动文件：{len(paths)} 个")

    def page_titles(self) -> list[str]:
        return [spec.title for spec in self.pages]


def default_diagnostic_theme() -> dict[str, object]:
    return {
        "window_bg": "#f4f7fb",
        "panel_bg": "#ffffff",
        "panel_bg_alt": "#edf3fa",
        "cell_bg": "#e5eef8",
        "plot_bg": "#ffffff",
        "text": "#102033",
        "muted_text": "#395268",
        "label_text": "#17324d",
        "axis": "#172033",
        "accent": "#1d72c9",
        "accent_alt": "#0f9f8f",
        "border": "#b8c6d8",
        "control_border": "#95a9bf",
        "table_bg": "#ffffff",
        "menu_bg": "#f7fbff",
        "grid_alpha": 0.22,
    }


def _theme_stylesheet(theme: dict[str, object]) -> str:
    return f"""
        QMainWindow, QWidget {{
            background: {theme.get('window_bg')};
            color: {theme.get('text')};
            font-size: 8pt;
        }}
        QWidget#diagnosticControlPanel {{
            background: {theme.get('window_bg')};
            border: 0;
        }}
        QLabel {{
            color: {theme.get('text')};
            font-weight: normal;
        }}
        QListWidget#diagnosticNav {{
            background: {theme.get('panel_bg')};
            color: {theme.get('text')};
            border: 0;
            border-right: 1px solid {theme.get('border')};
            padding: 8px;
            font-size: 10pt;
            outline: 0;
        }}
        QListWidget#diagnosticNav::item {{
            min-height: 38px;
            padding: 6px 10px;
            border-radius: 6px;
        }}
        QListWidget#diagnosticNav::item:selected {{
            background: {theme.get('accent')};
            color: #ffffff;
        }}
        QGroupBox {{
            background: {theme.get('panel_bg')};
            color: {theme.get('label_text')};
            border: 1px solid {theme.get('border')};
            border-radius: 8px;
            margin-top: 12px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }}
        QPushButton, QToolButton {{
            background: {theme.get('accent')};
            color: white;
            border: 1px solid {theme.get('accent')};
            border-radius: 6px;
            padding: 2px 6px;
            font-weight: bold;
            min-height: 20px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {theme.get('accent_alt')};
            border-color: {theme.get('accent_alt')};
        }}
        QPushButton:checked, QToolButton:checked {{
            background: {theme.get('accent_alt')};
            border-color: {theme.get('label_text')};
            color: #ffffff;
        }}
        QPushButton[role="secondary"] {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('control_border')};
        }}
        QPushButton[role="secondary"]:hover {{
            background: {theme.get('cell_bg')};
            border-color: {theme.get('accent')};
        }}
        QPushButton[role="secondary"]:checked {{
            background: {theme.get('accent_alt')};
            color: #ffffff;
            border-color: {theme.get('label_text')};
        }}
        QPushButton[role="danger"] {{
            background: #d7263d;
            color: #ffffff;
            border: 1px solid #d7263d;
        }}
        QPushButton[role="danger"]:hover {{
            background: #b91c2c;
            border-color: #b91c2c;
        }}
        QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('control_border')};
            border-radius: 5px;
            padding: 1px 4px;
            min-height: 18px;
        }}
        QListWidget, QTableWidget {{
            background: {theme.get('table_bg')};
            alternate-background-color: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-radius: 7px;
            selection-background-color: {theme.get('accent')};
            selection-color: #ffffff;
        }}
        QListWidget::item, QTableWidget::item {{
            background: {theme.get('table_bg')};
            color: {theme.get('text')};
            padding: 2px 4px;
            min-height: 18px;
        }}
        QListWidget::item:alternate, QTableWidget::item:alternate {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
        }}
        QListWidget::item:hover, QTableWidget::item:hover {{
            background: {theme.get('cell_bg')};
            color: {theme.get('text')};
        }}
        QListWidget::item:selected, QTableWidget::item:selected {{
            background: {theme.get('accent')};
            color: #ffffff;
        }}
        QHeaderView::section {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('label_text')};
            border: 0;
            border-right: 1px solid {theme.get('border')};
            border-bottom: 1px solid {theme.get('border')};
            padding: 4px;
            font-weight: bold;
        }}
        QTabWidget::pane {{
            border: 1px solid {theme.get('border')};
            background: {theme.get('panel_bg')};
        }}
        QTabBar::tab {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-bottom: 0;
            padding: 5px 12px;
            min-width: 82px;
        }}
        QTabBar::tab:selected {{
            background: {theme.get('accent')};
            color: #ffffff;
            font-weight: bold;
        }}
        QStatusBar {{
            background: {theme.get('menu_bg')};
            color: {theme.get('label_text')};
            border-top: 1px solid {theme.get('border')};
        }}
        QMenu {{
            background: {theme.get('menu_bg')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-radius: 7px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 4px 18px;
            border-radius: 5px;
        }}
        QMenu::item:selected {{
            background: {theme.get('accent')};
            color: #ffffff;
        }}
    """
