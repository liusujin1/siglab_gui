# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os

from PyInstaller.building.datastruct import normalize_toc
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata


PROJECT_ROOT = os.path.abspath(".")


def _normalized_dest(entry):
    return str(entry[0]).replace("\\", "/").lower()


def _filter_bundle_entries(entries):
    blocked_prefixes = (
        "assets/python_vna_icon.png",
        "assets/vianalysis_icon.png",
        "nidaqmx/_stubs/",
        "opengl/dlls/",
        "pyside6/translations/",
        "pyqtgraph/icons/peegee/",
    )
    blocked_exact = {
        "pyside6/qt6pdf.dll",
        "pyside6/qt6quick.dll",
        "pyside6/qt6qml.dll",
        "pyside6/qt6qmlmodels.dll",
        "pyside6/qt6qmlmeta.dll",
        "pyside6/qt6qmlworkerscript.dll",
        "pyside6/qt6virtualkeyboard.dll",
        "pyside6/plugins/platforms/qdirect2d.dll",
        "pyside6/plugins/platforms/qminimal.dll",
        "pyside6/plugins/platforms/qoffscreen.dll",
        "pyside6/plugins/imageformats/qicns.dll",
        "pyside6/plugins/imageformats/qjpeg.dll",
        "pyside6/plugins/imageformats/qpdf.dll",
        "pyside6/plugins/imageformats/qtga.dll",
        "pyside6/plugins/imageformats/qtiff.dll",
        "pyside6/plugins/imageformats/qwbmp.dll",
        "pyside6/plugins/imageformats/qwebp.dll",
    }
    filtered = []
    for entry in entries:
        dest = _normalized_dest(entry)
        if dest in blocked_exact or any(dest.startswith(prefix) for prefix in blocked_prefixes):
            continue
        filtered.append(entry)
    return filtered


def _merged_toc(*parts):
    merged = []
    for part in parts:
        merged.extend(_filter_bundle_entries(part))
    return normalize_toc(merged)


def _build_analysis(
    script,
    *,
    datas=None,
    hiddenimports=None,
    include_common_datas=True,
    include_common_hiddenimports=True,
):
    common_datas = COMMON_DATAS if include_common_datas else []
    common_hiddenimports = COMMON_HIDDENIMPORTS if include_common_hiddenimports else []
    analysis = Analysis(
        [script],
        pathex=[PROJECT_ROOT],
        binaries=[],
        datas=common_datas + list(datas or []),
        hiddenimports=common_hiddenimports + list(hiddenimports or []),
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=True,
        optimize=1,
    )
    analysis.datas = _filter_bundle_entries(analysis.datas)
    analysis.binaries = _filter_bundle_entries(analysis.binaries)
    return analysis


def _build_exe(pyz, scripts, *, name, icon):
    return EXE(
        pyz,
        scripts,
        [],
        exclude_binaries=True,
        name=name,
        icon=icon,
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


COMMON_DATAS = [
    ("dsa\\vna\\default.vna", "dsa\\vna"),
    ("update_config.json", "."),
    ("update_config.example.json", "."),
    ("assets\\python_vna_icon.ico", "assets"),
    ("assets\\python_vna_icon.png", "assets"),
    ("assets\\vianalysis_icon.ico", "assets"),
    ("assets\\vianalysis_icon.png", "assets"),
]

COMMON_HIDDENIMPORTS = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "pyqtgraph",
    "scipy.io",
    "scipy.signal",
]

SCIPY_CURVE_HIDDENIMPORTS = [
    "scipy.interpolate",
]

SCIPY_HIGHS_HIDDENIMPORTS = collect_submodules("scipy.optimize._highspy")

OPENGL_HIDDENIMPORTS = [
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLU",
    "pyqtgraph.opengl",
]

NI_DATAS = []
NI_DATAS += collect_data_files("nidaqmx")
NI_DATAS += collect_data_files("nitypes")
NI_DATAS += copy_metadata("nidaqmx")
NI_DATAS += copy_metadata("nitypes")

NI_HIDDENIMPORTS = [
    "python_vna.daq.ni",
    "python_vna.daq.simulated",
]
NI_HIDDENIMPORTS += collect_submodules("nidaqmx")
NI_HIDDENIMPORTS += collect_submodules("nitypes")

EXCLUDES = [
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
]


# noarchive=True keeps pure Python modules in the shared _internal tree instead
# of embedding large per-exe PYZ archives. The launchers stay small, and updates
# can replace changed Python modules without republishing both main executables.
analysis_vianalysis = _build_analysis(
    "scripts\\entry_vianalysis.py",
    hiddenimports=SCIPY_CURVE_HIDDENIMPORTS + OPENGL_HIDDENIMPORTS,
)
pyz_vianalysis = PYZ(analysis_vianalysis.pure)
vianalysis = _build_exe(
    pyz_vianalysis,
    analysis_vianalysis.scripts,
    name="VIanalysis",
    icon="assets\\vianalysis_icon.ico",
)

analysis_python_vna_test = _build_analysis(
    "scripts\\entry_python_vna_test.py",
    datas=NI_DATAS,
    hiddenimports=SCIPY_CURVE_HIDDENIMPORTS + SCIPY_HIGHS_HIDDENIMPORTS + NI_HIDDENIMPORTS,
)
pyz_python_vna_test = PYZ(analysis_python_vna_test.pure)
python_vna_test = _build_exe(
    pyz_python_vna_test,
    analysis_python_vna_test.scripts,
    name="PythonVNATest",
    icon="assets\\python_vna_icon.ico",
)

analysis_python_vna_updater = _build_analysis(
    "scripts\\entry_python_vna_updater.py",
    include_common_datas=False,
    include_common_hiddenimports=False,
)
pyz_python_vna_updater = PYZ(analysis_python_vna_updater.pure)
python_vna_updater = _build_exe(
    pyz_python_vna_updater,
    analysis_python_vna_updater.scripts,
    name="PythonVNAUpdater",
    icon="assets\\python_vna_icon.ico",
)

coll = COLLECT(
    vianalysis,
    python_vna_test,
    python_vna_updater,
    _merged_toc(
        analysis_vianalysis.binaries,
        analysis_python_vna_test.binaries,
        analysis_python_vna_updater.binaries,
    ),
    _merged_toc(
        analysis_vianalysis.datas,
        analysis_python_vna_test.datas,
        analysis_python_vna_updater.datas,
    ),
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PythonVNA_Suite",
)
