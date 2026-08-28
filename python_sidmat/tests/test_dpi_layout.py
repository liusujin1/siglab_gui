"""Cross-package DPI smoke checks for the portable TestKit GUIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("package", ["samba", "sidmat"])
@pytest.mark.parametrize("scale", ["1", "2"])
def test_packaged_layout_fits_logical_work_area(package: str, scale: str) -> None:
    """A 100% and a 200% logical desktop must not clip the main window."""

    if package == "samba":
        import_line = "from python_samba.ui.main_window import MainWindow"
    else:
        import_line = "from python_sidmat.ui.main_window import MainWindow"
    script = f"""
import json
import sys
sys.frozen = True
{import_line}
from PySide6 import QtWidgets
app = QtWidgets.QApplication([])
window = MainWindow()
screen = app.primaryScreen()
geo = screen.availableGeometry()
app.processEvents()
print(json.dumps({{
    'screen': [geo.width(), geo.height()],
    'window': [window.width(), window.height()],
    'minimum': [window.minimumWidth(), window.minimumHeight()],
    'font_pixel_size': app.font().pixelSize(),
    'font_scale': window._font_scale,
}}))
window.close()
app.processEvents()
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(_ROOT / "python_samba" / "src"), str(_ROOT / "python_sidmat" / "src")]
            ),
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
        }
    )
    for key in (
        "QT_SCREEN_SCALE_FACTORS",
        "QT_AUTO_SCREEN_SCALE_FACTOR",
        "QT_SCALE_FACTOR_ROUNDING_POLICY",
    ):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        pytest.fail(
            f"{package} DPI smoke failed at QT_SCALE_FACTOR={scale}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    screen_w, screen_h = payload["screen"]
    window_w, window_h = payload["window"]
    minimum_w, minimum_h = payload["minimum"]
    assert 0 < window_w <= screen_w
    assert 0 < window_h <= screen_h
    assert 0 < minimum_w <= screen_w
    assert 0 < minimum_h <= screen_h
    assert 8 <= payload["font_pixel_size"] <= 13
    assert 0.67 <= payload["font_scale"] <= 1.10


@pytest.mark.parametrize("package", ["samba", "sidmat"])
def test_1080p_100_percent_uses_compact_default_window(package: str, monkeypatch) -> None:
    """The common 1920x1080/100% workstation must not open near full-screen."""

    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui

    if package == "samba":
        from python_samba.ui.main_window import MainWindow
    else:
        from python_sidmat.ui.main_window import MainWindow

    class Screen1080p:
        @staticmethod
        def availableGeometry():
            # Typical 1080p work area with a 40-pixel Windows taskbar.
            return QtCore.QRect(0, 0, 1920, 1040)

        @staticmethod
        def devicePixelRatio():
            return 1.0

        @staticmethod
        def logicalDotsPerInch():
            return 96.0

    monkeypatch.setattr(QtGui.QGuiApplication, "primaryScreen", lambda: Screen1080p())
    available, initial, minimum, _scale = MainWindow._initial_window_metrics()
    assert available.size() == QtCore.QSize(1920, 1040)
    assert initial == QtCore.QSize(1240, 780)
    assert minimum == QtCore.QSize(960, 640)
    assert initial.width() <= int(available.width() * 0.75)
    assert initial.height() <= int(available.height() * 0.80)


@pytest.mark.parametrize("package", ["samba", "sidmat"])
def test_local_200_percent_logical_work_area_is_compact(package: str, monkeypatch) -> None:
    """A 2880x1800 panel at 200% exposes 1440x852 logical pixels."""

    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui

    if package == "samba":
        from python_samba.ui.main_window import MainWindow
    else:
        from python_sidmat.ui.main_window import MainWindow

    class LocalScreen:
        @staticmethod
        def availableGeometry():
            return QtCore.QRect(0, 0, 1440, 852)

    monkeypatch.setattr(QtGui.QGuiApplication, "primaryScreen", lambda: LocalScreen())
    available, initial, minimum, density = MainWindow._initial_window_metrics()
    assert available.size() == QtCore.QSize(1440, 852)
    assert initial == QtCore.QSize(930, 585)
    assert minimum == QtCore.QSize(800, 520)
    assert density == pytest.approx(0.75)
    assert MainWindow._font_scale_for_display(density) == pytest.approx(0.67)


@pytest.mark.parametrize(
    ("work_width", "work_height", "expected_window"),
    [
        (1440, 852, (930, 585)),
        (1920, 1040, (1240, 780)),
    ],
)
def test_samba_status_dashboard_fits_without_horizontal_overflow(
    monkeypatch,
    work_width: int,
    work_height: int,
    expected_window: tuple[int, int],
) -> None:
    """Status controls must fit the actual initial window, not a larger test size."""

    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class FakeScreen:
        @staticmethod
        def availableGeometry():
            return QtCore.QRect(0, 0, work_width, work_height)

        @staticmethod
        def devicePixelRatio():
            return 1.0

        @staticmethod
        def logicalDotsPerInch():
            return 96.0

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    monkeypatch.setattr(QtGui.QGuiApplication, "primaryScreen", lambda: FakeScreen())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = PatchedMainWindow()
    window.show()
    app.processEvents()
    try:
        assert (window.width(), window.height()) == expected_window
        status_index = next(
            index
            for index in range(window.main_tabs.count())
            if window.main_tabs.tabText(index) == "Status"
        )
        window.main_tabs.setCurrentIndex(status_index)
        app.processEvents()
        outer_scroll = window.main_tabs.widget(status_index)
        status_tabs = outer_scroll.widget()
        status_scroll = status_tabs.widget(0)
        viewport = status_scroll.viewport()
        app.processEvents()

        assert status_scroll.horizontalScrollBar().maximum() == 0
        assert status_scroll.widget().width() <= viewport.width()
        controls = [
            *window.status_loop_badges.values(),
            *window.status_velocity_axis_lamps,
            *window.status_position_axis_lamps,
            *window.status_pneumatic_axis_lamps,
            window.status_events,
        ]
        for control in controls:
            top_left = control.mapTo(viewport, QtCore.QPoint(0, 0))
            assert top_left.x() >= 0
            assert top_left.x() + control.width() <= viewport.width()
    finally:
        window.close()
        app.processEvents()
