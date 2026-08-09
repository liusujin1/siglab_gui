"""Shared test isolation for process-global Qt settings."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_qsettings(tmp_path_factory):
    """Prevent GUI tests from overwriting the operator's saved connection."""

    try:
        from PySide6 import QtCore
    except ImportError:
        yield
        return

    root = tmp_path_factory.mktemp("qt-settings")
    previous = QtCore.QSettings.defaultFormat()
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat,
        QtCore.QSettings.Scope.UserScope,
        str(root),
    )
    try:
        yield
    finally:
        QtCore.QSettings.setDefaultFormat(previous)
