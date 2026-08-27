"""Deterministic PyInstaller payload filters for the Windows TestKit.

PyInstaller's PySide6 hook intentionally collects a broad Qt runtime.  The
TestKit uses QtCore/Gui/Widgets only, so keeping that unfiltered hook output
would also ship QML, Quick, PDF, software OpenGL, every translation, and many
unused plugins.  This module filters Analysis TOCs after hooks have run, which
prevents later hook changes from silently re-introducing those resources.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import TypeVar


TocEntry = TypeVar("TocEntry", bound=tuple)

_BLOCKED_BASENAMES = {
    # Qt 6.11 uses the Windows system ICU shim.  Do not let PyInstaller pick
    # an unrelated icuuc/icudt pair from a developer PATH (for example a
    # Poppler runtime); that produces WinError 127 while importing QtCore.
    "icudt78.dll",
    "icuuc.dll",
    "opengl32sw.dll",
    "qt6pdf.dll",
    "qt6pdfwidgets.dll",
    "qt6qml.dll",
    "qt6qmlmeta.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
    "qt6quick.dll",
    "qt6quick3d.dll",
    "qt6quickcontrols2.dll",
    "qt6quickwidgets.dll",
    "qt6test.dll",
    "qt6virtualkeyboard.dll",
    "qtpdf.pyd",
    "qtpdfwidgets.pyd",
    "qtqml.pyd",
    "qtquick.pyd",
    "qtquick3d.pyd",
    "qtquickcontrols2.pyd",
    "qtquickwidgets.pyd",
    "qttest.pyd",
    "qtvirtualkeyboard.pyd",
}

_BLOCKED_PREFIXES = (
    "opengl/",
    "pyqtgraph/opengl/",
    "pyside6/qml/",
    "pyside6/plugins/generic/",
    "pyside6/plugins/iconengines/",
    "pyside6/plugins/networkinformation/",
    "pyside6/plugins/platforminputcontexts/",
    "pyside6/plugins/styles/",
)

_PLATFORM_PLUGINS = {"qoffscreen.dll", "qwindows.dll"}
_IMAGE_PLUGINS = {"qico.dll", "qjpeg.dll"}
_TRANSLATION_SUFFIXES = ("_en.qm", "_zh_cn.qm")


def _normalized_destination(entry: tuple) -> str:
    return str(PurePosixPath(str(entry[0]).replace("\\", "/"))).lower()


def keep_testkit_entry(entry: tuple) -> bool:
    """Return whether a PyInstaller TOC entry belongs in the TestKit."""

    destination = _normalized_destination(entry)
    basename = destination.rsplit("/", 1)[-1]

    if basename in _BLOCKED_BASENAMES:
        return False
    if destination.startswith(_BLOCKED_PREFIXES):
        return False

    if "/pyside6/plugins/platforms/" in f"/{destination}":
        return basename in _PLATFORM_PLUGINS
    if "/pyside6/plugins/imageformats/" in f"/{destination}":
        return basename in _IMAGE_PLUGINS
    if "/pyside6/translations/" in f"/{destination}" and basename.endswith(".qm"):
        return basename.endswith(_TRANSLATION_SUFFIXES)
    return True


def filter_testkit_entries(entries: Iterable[TocEntry]) -> list[TocEntry]:
    """Filter hook-produced TOC entries while preserving their order."""

    return [entry for entry in entries if keep_testkit_entry(entry)]


def rejected_destinations(entries: Iterable[tuple]) -> list[str]:
    """Expose rejected paths for build diagnostics and unit tests."""

    return [str(entry[0]) for entry in entries if not keep_testkit_entry(entry)]
