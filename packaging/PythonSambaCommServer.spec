# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent.resolve()
sys.path.insert(0, str(ROOT / "packaging"))
from pyinstaller_slim import filter_testkit_entries

SAMBA = ROOT / "python_samba"
ASSETS = ROOT / "packaging" / "assets"

a = Analysis(
    [str(SAMBA / "entry_comm_server.py")],
    pathex=[str(SAMBA / "src")], binaries=[], datas=[(str(ASSETS), "assets")],
    hiddenimports=(
        [
            "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
            "serial", "serial.tools", "serial.tools.list_ports",
            "serial.tools.list_ports_windows", "psutil",
        ]
        + collect_submodules("python_samba.commserver")
        + collect_submodules("python_samba.transport")
    ),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["numpy", "scipy", "pyqtgraph", "matplotlib", "tkinter", "pytest"],
    noarchive=False, optimize=1,
)
a.binaries = filter_testkit_entries(a.binaries)
a.datas = filter_testkit_entries(a.datas)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="PythonSambaCommServer",
    icon=str(ASSETS / "commserver_icon.ico"),
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
