# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata


def _normalized_dest(entry):
    return str(entry[0]).replace("\\", "/").lower()


def _filter_bundle_entries(entries):
    blocked_prefixes = (
        "assets/python_vna_icon.png",
        "nidaqmx/_stubs/",
        "pyside6/translations/",
    )
    blocked_exact = {
        "pyside6/qt6pdf.dll",
        "pyside6/qt6quick.dll",
        "pyside6/qt6qml.dll",
        "pyside6/qt6qmlmodels.dll",
        "pyside6/qt6qmlmeta.dll",
        "pyside6/qt6qmlworkerscript.dll",
        "pyside6/qt6virtualkeyboard.dll",
    }
    filtered = []
    for entry in entries:
        dest = _normalized_dest(entry)
        if dest in blocked_exact or any(dest.startswith(prefix) for prefix in blocked_prefixes):
            continue
        filtered.append(entry)
    return filtered

datas = [
    ('dsa\\vna\\default.vna', 'dsa\\vna'),
    ('assets\\python_vna_icon.ico', 'assets'),
    ('assets\\python_vna_icon.png', 'assets'),
]
hiddenimports = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'pyqtgraph']
datas += collect_data_files('nidaqmx')
datas += collect_data_files('nitypes')
datas += copy_metadata('nidaqmx')
datas += copy_metadata('nitypes')
hiddenimports += collect_submodules('nidaqmx')
hiddenimports += collect_submodules('nitypes')


a = Analysis(
    ['python_vna\\app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['IPython', 'jupyter', 'notebook', 'pytest', 'tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtNetworkAuth', 'PySide6.QtPositioning', 'PySide6.QtSql'],
    noarchive=False,
    optimize=0,
)
a.datas = _filter_bundle_entries(a.datas)
a.binaries = _filter_bundle_entries(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PythonVNA',
    icon='assets\\python_vna_icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='PythonVNA',
)
