# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent.resolve()
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

HIDDEN = [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "shiboken6",
    "numpy", "scipy", "scipy.io", "scipy.signal", "pyqtgraph",
    "serial", "serial.tools", "serial.tools.list_ports",
    "serial.tools.list_ports_windows", "psutil",
]
HIDDEN += collect_submodules("python_sidmat")
HIDDEN += collect_submodules("python_samba.commserver")
HIDDEN += collect_submodules("python_samba.protocol")
HIDDEN += collect_submodules("python_samba.services")
HIDDEN += collect_submodules("python_samba.transport")

a = Analysis(
    [str(ROOT / "packaging" / "entries" / "entry_sidmat.py")],
    pathex=[str(SIDMAT / "src"), str(SAMBA / "src")],
    binaries=[],
    datas=[(str(PATCHES), "_patches"), (str(PATCHES), "python_samba_patches")],
    hiddenimports=HIDDEN,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=EXCLUDES,
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="SIDMAT",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="SIDMAT"
)
