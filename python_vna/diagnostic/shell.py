from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from python_vna.diagnostic.pages import (
    ModalShapePage,
    TraceAnalysisPage,
    VibrationAnalysisPage,
)
from python_vna.diagnostic.data import detect_trace_file_kind
from python_vna import __version__ as PYTHON_VNA_VERSION
from python_vna.optional import require
from python_vna.ui.analysis_viewer import AnalysisDataStore, AnalysisWorkbench
from python_vna.ui.diagnostic_theme import (
    build_diagnostic_stylesheet,
    default_diagnostic_theme as shared_default_diagnostic_theme,
)
from python_vna.update_client import (
    fetch_manifest,
    launch_updater,
    load_update_settings,
    select_update,
)

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
        self._build_menus()
        self._build_ui()
        self._resize_to_available_screen()
        self.apply_theme(self._theme)
        if startup_paths:
            self.load_startup_paths(startup_paths)

    def _build_menus(self) -> None:
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("检查更新", self._check_for_updates)

    def _check_for_updates(self) -> None:
        try:
            settings = load_update_settings()
            if settings is None:
                QtWidgets.QMessageBox.information(
                    self,
                    "检查更新",
                    "未配置更新地址。请在程序目录创建 update_config.json，并填写 NAS 上的 manifest.json 地址。",
                )
                return
            manifest = fetch_manifest(settings.manifest_url)
            decision = select_update(
                manifest,
                current_version=PYTHON_VNA_VERSION,
                manifest_url=settings.manifest_url,
                allow_full=True,
            )
            if not decision.available or decision.package is None:
                QtWidgets.QMessageBox.information(
                    self,
                    "检查更新",
                    f"当前版本：{decision.current_version}\n最新版本：{decision.latest_version}\n{decision.message}",
                )
                return
            reply = QtWidgets.QMessageBox.question(
                self,
                "发现更新",
                (
                    f"当前版本：{decision.current_version}\n"
                    f"最新版本：{decision.latest_version}\n\n"
                    "是否关闭当前程序并开始更新？"
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
            launch_updater(
                manifest_url=settings.manifest_url,
                current_version=PYTHON_VNA_VERSION,
                restart_executable="VIanalysis.exe",
            )
            self.close()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "检查更新失败", str(exc))

    def _resize_to_available_screen(self) -> None:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1360, 800)
            return
        available = screen.availableGeometry()
        width = min(1560, max(760, int(available.width() * 0.94)), max(640, available.width() - 24))
        height = min(920, max(520, int(available.height() * 0.92)), max(480, available.height() - 24))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        central.setObjectName("diagnosticRoot")
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav_rail = QtWidgets.QFrame()
        self.nav_rail.setObjectName("diagnosticRail")
        self.nav_rail.setFixedWidth(204)
        rail_layout = QtWidgets.QVBoxLayout(self.nav_rail)
        rail_layout.setContentsMargins(0, 16, 0, 10)
        rail_layout.setSpacing(0)

        brand_wrap = QtWidgets.QWidget()
        brand_wrap.setObjectName("diagnosticBrandWrap")
        brand_outer = QtWidgets.QHBoxLayout(brand_wrap)
        brand_outer.setContentsMargins(14, 2, 12, 8)
        brand_outer.setSpacing(10)

        brand_mark = QtWidgets.QLabel("VI")
        brand_mark.setObjectName("diagnosticBrandMark")
        brand_mark.setAlignment(QtCore.Qt.AlignCenter)
        brand_outer.addWidget(brand_mark, 0, QtCore.Qt.AlignVCenter)

        brand_layout = QtWidgets.QVBoxLayout()
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(1)
        brand_english = QtWidgets.QLabel("VI ANALYSIS")
        brand_english.setObjectName("diagnosticBrandEnglish")
        brand_title = QtWidgets.QLabel("振动诊断软件")
        brand_title.setObjectName("diagnosticBrand")
        brand_version = QtWidgets.QLabel(f"v{PYTHON_VNA_VERSION}")
        brand_version.setObjectName("diagnosticVersion")
        brand_layout.addWidget(brand_english)
        brand_layout.addWidget(brand_title)
        brand_layout.addWidget(brand_version)
        brand_outer.addLayout(brand_layout, 1)
        rail_layout.addWidget(brand_wrap)

        nav_divider = QtWidgets.QFrame()
        nav_divider.setObjectName("diagnosticNavDivider")
        nav_divider.setFrameShape(QtWidgets.QFrame.NoFrame)
        rail_layout.addWidget(nav_divider)

        self.nav_list = QtWidgets.QListWidget()
        self.nav_list.setObjectName("diagnosticNav")
        self.nav_list.setWordWrap(True)
        self.nav_list.setTextElideMode(QtCore.Qt.ElideRight)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.nav_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        rail_layout.addWidget(self.nav_list, 1)
        layout.addWidget(self.nav_rail)

        content = QtWidgets.QWidget()
        content.setObjectName("diagnosticContent")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setMinimumWidth(0)
        # Hidden pages have large minimum hints; the shell must still follow the usable screen height.
        self.stack.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)

        self.analysis_data_store = AnalysisDataStore(self)
        self.analysis_page = AnalysisWorkbench(
            derived_only=False,
            include_derived_tab=False,
            data_store=self.analysis_data_store,
        )
        self.data_processing_page = AnalysisWorkbench(
            derived_only=True,
            data_store=self.analysis_data_store,
        )
        self.modal_page = ModalShapePage(data_store=self.analysis_data_store)
        self.vibration_page = VibrationAnalysisPage()
        self.trace_page = TraceAnalysisPage()
        self.analysis_page.statusBar().messageChanged.connect(self.statusBar().showMessage)
        self.analysis_page.statusBar().hide()
        self.data_processing_page.statusBar().messageChanged.connect(self.statusBar().showMessage)
        self.data_processing_page.statusBar().hide()
        self.pages: list[DiagnosticPageSpec] = [
            DiagnosticPageSpec("VNA数据分析", "VNA 数据分析", self.analysis_page),
            DiagnosticPageSpec("数据处理", "换算 / 曲线处理", self.data_processing_page),
            DiagnosticPageSpec("模态振型", "模态识别", self.modal_page),
            DiagnosticPageSpec("上位机数据分析", "频响 / 日志", self.vibration_page),
            DiagnosticPageSpec("减振器软件测试数据分析", "IDE / HAC 数据", self.trace_page),
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
        self._configure_tab_bars()
        self.statusBar().showMessage("就绪")

    def _configure_tab_bars(self) -> None:
        for tabs in self.findChildren(QtWidgets.QTabWidget):
            bar = tabs.tabBar()
            bar.setUsesScrollButtons(True)
            bar.setElideMode(QtCore.Qt.ElideRight)
            bar.setExpanding(False)

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
            trace_kind = detect_trace_file_kind(path) if path.exists() and suffix in {".txt", ".csv", ".dat", ".mat", ".rpt", ".para"} else ""
            if trace_kind:
                trace_paths.append(path)
                if suffix in {".txt", ".csv", ".dat"}:
                    vibration_paths.append(path)
            elif suffix in {".vna", ".mat"}:
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
    return shared_default_diagnostic_theme()


def _theme_stylesheet(theme: dict[str, object]) -> str:
    return build_diagnostic_stylesheet(theme)
