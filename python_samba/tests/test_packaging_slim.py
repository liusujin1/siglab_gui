from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "testkit_pyinstaller_slim", ROOT / "packaging" / "pyinstaller_slim.py"
)
assert SPEC is not None and SPEC.loader is not None
SLIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SLIM)


def _entry(destination: str) -> tuple[str, str, str]:
    return destination, f"C:/hook-output/{destination}", "BINARY"


def test_qt_payload_filter_keeps_only_supported_runtime_assets() -> None:
    kept = [
        "PySide6/Qt6Core.dll",
        "PySide6/Qt6Gui.dll",
        "PySide6/Qt6Widgets.dll",
        "PySide6/plugins/platforms/qwindows.dll",
        "PySide6/plugins/platforms/qoffscreen.dll",
        "PySide6/plugins/imageformats/qjpeg.dll",
        "PySide6/plugins/imageformats/qico.dll",
        "PySide6/translations/qtbase_en.qm",
        "PySide6/translations/qtbase_zh_CN.qm",
    ]
    removed = [
        "icuuc.dll",
        "icudt78.dll",
        "PySide6/opengl32sw.dll",
        "PySide6/Qt6Qml.dll",
        "PySide6/Qt6Quick.dll",
        "PySide6/Qt6Pdf.dll",
        "PySide6/Qt6VirtualKeyboard.dll",
        "PySide6/QtTest.pyd",
        "PySide6/plugins/platforms/qminimal.dll",
        "PySide6/plugins/platforms/qdirect2d.dll",
        "PySide6/plugins/imageformats/qsvg.dll",
        "PySide6/plugins/imageformats/qwebp.dll",
        "PySide6/translations/qtbase_de.qm",
        "OpenGL/GL/__init__.pyc",
        "pyqtgraph/opengl/GLViewWidget.pyc",
    ]

    output = SLIM.filter_testkit_entries([_entry(path) for path in kept + removed])
    destinations = [entry[0] for entry in output]

    assert destinations == kept


def test_qt_payload_filter_is_case_and_separator_independent() -> None:
    entries = [
        _entry(r"PYSIDE6\QT6QUICK.DLL"),
        _entry(r"PySide6\plugins\platforms\QWINDOWS.DLL"),
        _entry(r"PySide6\translations\qtbase_ZH_CN.qm"),
    ]

    assert [item[0] for item in SLIM.filter_testkit_entries(entries)] == [
        r"PySide6\plugins\platforms\QWINDOWS.DLL",
        r"PySide6\translations\qtbase_ZH_CN.qm",
    ]
