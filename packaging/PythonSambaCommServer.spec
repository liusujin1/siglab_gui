# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent.resolve()
SAMBA = ROOT / "python_samba"

a = Analysis(
    [str(SAMBA / "entry_comm_server.py")],
    pathex=[str(SAMBA / "src")], binaries=[], datas=[],
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
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="PythonSambaCommServer",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
