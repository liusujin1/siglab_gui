# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import filecmp
from pathlib import Path
import sys

from PyInstaller.building.datastruct import normalize_toc
from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent.resolve()
sys.path.insert(0, str(ROOT / "packaging"))
from pyinstaller_slim import filter_testkit_entries

SAMBA = ROOT / "python_samba"
SIDMAT = ROOT / "python_sidmat"
PATCHES = SAMBA / "_patches"

EXCLUDES = [
    "IPython", "jupyter", "notebook", "pytest", "tkinter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets", "PySide6.QtSql",
]

COMMON_HIDDEN = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "shiboken6",
    "numpy", "scipy", "scipy.io", "scipy.signal", "pyqtgraph",
    "serial", "serial.tools", "serial.tools.list_ports",
    "serial.tools.list_ports_windows", "psutil",
]


def _same_source(left, right):
    left_path, right_path = Path(left), Path(right)
    try:
        if left_path.resolve() == right_path.resolve():
            return True
        return (
            left_path.is_file()
            and right_path.is_file()
            and filecmp.cmp(left_path, right_path, shallow=False)
        )
    except OSError:
        return False


def _merged_toc(*parts):
    """Merge shared onedir payloads and reject ambiguous target collisions."""

    selected = {}
    for part in parts:
        for entry in part:
            destination, source, kind = entry
            key = str(destination).replace("\\", "/").lower()
            previous = selected.get(key)
            if previous is None:
                selected[key] = entry
                continue
            if previous[2] != kind or not _same_source(previous[1], source):
                raise ValueError(
                    "conflicting shared bundle target "
                    f"{destination!r}: {previous[1]!r} ({previous[2]}) vs "
                    f"{source!r} ({kind})"
                )
    return normalize_toc(list(selected.values()))


def _analysis(script, *, pathex, datas=None, hiddenimports=None):
    analysis = Analysis(
        [str(script)],
        pathex=[str(path) for path in pathex],
        binaries=[],
        datas=list(datas or []),
        hiddenimports=COMMON_HIDDEN + list(hiddenimports or []),
        hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=EXCLUDES,
        # Put pure Python modules in the common _internal tree instead of
        # embedding a second copy in each GUI executable.
        noarchive=True, optimize=1,
    )
    # PySide6 hooks collect all Qt modules/plugins/translations.  Filter their
    # final TOCs so excluded resources cannot leak back through hook data.
    analysis.binaries = filter_testkit_entries(analysis.binaries)
    analysis.datas = filter_testkit_entries(analysis.datas)
    return analysis


def _exe(analysis, name):
    return EXE(
        PYZ(analysis.pure), analysis.scripts, [], exclude_binaries=True,
        name=name, contents_directory="_internal",
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
    )


samba_analysis = _analysis(
    ROOT / "packaging" / "entries" / "entry_samba.py",
    pathex=[SAMBA / "src"],
    datas=[(str(PATCHES), "python_samba_patches")],
    hiddenimports=(
        collect_submodules("python_samba.logging_tools")
        + collect_submodules("python_samba.ui")
    ),
)
sidmat_analysis = _analysis(
    ROOT / "packaging" / "entries" / "entry_sidmat.py",
    pathex=[SIDMAT / "src", SAMBA / "src"],
    hiddenimports=(
        collect_submodules("python_sidmat")
        + collect_submodules("python_samba.commserver")
        + collect_submodules("python_samba.protocol")
        + collect_submodules("python_samba.services")
        + collect_submodules("python_samba.transport")
    ),
)

samba = _exe(samba_analysis, "Samba")
sidmat = _exe(sidmat_analysis, "SIDMAT")

coll = COLLECT(
    samba,
    sidmat,
    _merged_toc(samba_analysis.binaries, sidmat_analysis.binaries),
    _merged_toc(samba_analysis.datas, sidmat_analysis.datas),
    strip=False, upx=False, upx_exclude=[], name="SigLabSuite",
)
