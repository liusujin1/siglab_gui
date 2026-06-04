# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['python_vna\\diagnostic\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets\\python_vna_icon.ico', 'assets'), ('assets\\python_vna_icon.png', 'assets')],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'OpenGL',
        'OpenGL.GL',
        'OpenGL.GLU',
        'pyqtgraph',
        'pyqtgraph.opengl',
        'scipy.signal',
        'scipy.interpolate',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['IPython', 'jupyter', 'notebook', 'pytest', 'tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtNetworkAuth', 'PySide6.QtPositioning', 'PySide6.QtSql'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PythonVNA_diag',
    icon='assets\\python_vna_icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PythonVNA_diag',
)
