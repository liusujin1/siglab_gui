from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

from python_vna.diagnostic.pages import ModalShapePage


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_VNA_DISABLE_MODAL_OPENGL", "1")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings(str(tmp_path / "modal.ini"), QtCore.QSettings.IniFormat)
    widget = ModalShapePage(directory_settings=settings)
    widget._bulk_table_update = True
    for index in range(3):
        widget._insert_point_row([True, f"P{index + 1}", "", 0, 0, 0, 1, 1, 1, index, 0, 0])
    for index in range(2):
        widget._insert_line_row([True, f"P{index + 1}", f"P{index + 2}", "manual"])
    widget._bulk_table_update = False
    yield widget
    widget._preview_timer.stop()
    widget._shutdown_identification()
    widget.close()
    app.processEvents()


def select_rows(table, rows):
    table.clearSelection()
    flags = QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows
    for row in rows:
        table.selectionModel().select(table.model().index(row, 0), flags)


def test_modal_directories_persist_import_and_export_independently(page, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_: (str(source / "points.csv"), ""))
    monkeypatch.setattr(page, "import_point_mapping", lambda _: 3)
    page._choose_import_mapping()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_: (str(destination / "points.csv"), ""))
    monkeypatch.setattr(page, "export_point_mapping", lambda _: None)
    page._choose_export_mapping()
    reopened = ModalShapePage(directory_settings=QtCore.QSettings(str(tmp_path / "modal.ini"), QtCore.QSettings.IniFormat))
    try:
        assert reopened._modal_dialog_directory("import") == source
        assert reopened._modal_dialog_directory("export") == destination
        assert reopened._last_directory == destination
        reopened.clear()
        assert reopened._modal_dialog_directory("import") == source
        assert reopened._modal_dialog_directory("export") == destination
    finally:
        reopened.close()


@pytest.mark.parametrize("method,kwargs,dialog,purpose", [
    ("_choose_files", {}, "getOpenFileNames", "import"),
    ("_choose_folder", {}, "getExistingDirectory", "import"),
    ("_choose_import_mapping", {}, "getOpenFileName", "import"),
    ("_choose_modal_session", {"export": False}, "getOpenFileName", "import"),
    ("_choose_export_mapping", {}, "getSaveFileName", "export"),
    ("_choose_modal_session", {"export": True}, "getSaveFileName", "export"),
    ("_choose_export_gif", {}, "getSaveFileName", "export"),
    ("_choose_export_identification", {}, "getSaveFileName", "export"),
    ("_choose_export_candidates", {}, "getSaveFileName", "export"),
])
def test_all_modal_dialogs_start_in_remembered_directory_and_cancel_keeps_it(page, tmp_path, monkeypatch,
                                                                          method, kwargs, dialog, purpose):
    for kind in ("import", "export"):
        directory = tmp_path / kind
        directory.mkdir()
        page._remember_modal_dialog_path(directory, kind, is_directory=True)
    settings = page._modal_directory_settings
    before = {key: settings.value(key) for key in settings.allKeys()}
    calls = []

    def cancel(*args):
        calls.append(args)
        return "" if dialog == "getExistingDirectory" else ([], "") if dialog == "getOpenFileNames" else ("", "")

    monkeypatch.setattr(QtWidgets.QFileDialog, dialog, cancel)
    getattr(page, method)(**kwargs)
    initial = Path(calls[0][2])
    assert initial == tmp_path / purpose or initial.parent == tmp_path / purpose
    assert {key: settings.value(key) for key in settings.allKeys()} == before


def test_modal_session_dialogs_remember_success_but_not_rejected_import(page, tmp_path, monkeypatch):
    folder = tmp_path / "sessions"
    folder.mkdir()
    imported = Mock()
    exported = Mock()
    monkeypatch.setattr(page, "import_session", imported)
    monkeypatch.setattr(page, "export_session", exported)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_: (str(folder / "sample.vnamodal"), ""))
    page._choose_modal_session(export=False)
    imported.assert_called_once()
    assert page._modal_dialog_directory("import") == folder
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_: (str(folder / "saved"), ""))
    page._choose_modal_session(export=True)
    exported.assert_called_once_with(str(folder / "saved") + ".vnamodal")
    assert page._modal_dialog_directory("export") == folder
    before = page._modal_directory_settings.value("modal/directories/import")
    page.files = [object()]
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *_: QtWidgets.QMessageBox.No)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_: (str(tmp_path / "other.vnamodal"), ""))
    page._choose_modal_session(export=False)
    assert imported.call_count == 1
    assert page._modal_directory_settings.value("modal/directories/import") == before
    page.files = []


def test_missing_remembered_folder_uses_existing_fallback(page, tmp_path):
    page._modal_directory_settings.setValue("modal/directories/import", str(tmp_path / "deleted"))
    page._last_directory = tmp_path
    assert page._modal_dialog_directory("import") == tmp_path


def test_session_import_button_precedes_export_and_preview_settings_are_inline(page):
    grid = page.import_session_button.parentWidget().layout().itemAt(0).layout()
    session_row = grid.itemAtPosition(2, 0).layout()
    assert session_row.itemAt(0).widget() is page.import_session_button
    assert session_row.itemAt(1).widget() is page.export_session_button
    page.resize(1200, 720)
    page.show()
    QtWidgets.QApplication.processEvents()
    assert not hasattr(page, "modal_preview_dialog")
    for spin in (page.view_azimuth_spin, page.view_elevation_spin, page.mode_gain_spin, page.gif_frame_count_spin):
        assert page.modal_preview_settings_panel.isAncestorOf(spin)
        assert spin.isVisible()


def test_point_selection_can_be_enabled_and_disabled_with_one_refresh(page, monkeypatch):
    refresh = Mock(wraps=page._mapping_changed)
    monkeypatch.setattr(page, "_mapping_changed", refresh)
    select_rows(page.point_table, [0, 2])
    menu = page._modal_table_batch_menu(page.point_table)
    page._identification_stale = False
    menu.actions()[1].trigger()
    assert [page._table_bool(page.point_table, row, 0, True) for row in range(3)] == [False, True, False]
    assert refresh.call_count == 1
    assert page._identification_stale
    assert {i.row() for i in page.point_table.selectedIndexes()} == {0, 2}
    menu.actions()[0].trigger()
    assert all(page._table_bool(page.point_table, row, 0, False) for row in range(3))
    assert refresh.call_count == 2
    menu.deleteLater()


def test_checkbox_edit_applies_to_selected_rows_without_repeated_refresh(page, monkeypatch):
    refresh = Mock(wraps=page._mapping_changed)
    monkeypatch.setattr(page, "_mapping_changed", refresh)
    select_rows(page.point_table, [0, 1, 2])
    page.point_table.item(0, 0).setCheckState(QtCore.Qt.Unchecked)
    assert all(not page._table_bool(page.point_table, row, 0, True) for row in range(3))
    assert refresh.call_count == 1


@pytest.mark.parametrize("table_name,tab,rows", [("point_table", 1, [0, 2]), ("line_table", 2, [0, 1])])
def test_mouse_checkbox_batch_preserves_selection_for_the_next_toggle(page, table_name, tab, rows):
    from PySide6 import QtTest
    page.resize(1200, 720)
    page.show()
    page.left_work_tabs.setCurrentIndex(tab)
    QtWidgets.QApplication.processEvents()
    table = getattr(page, table_name)
    select_rows(table, rows)
    index = table.model().index(rows[0], 0)
    option = QtWidgets.QStyleOptionViewItem()
    option.initFrom(table)
    option.rect = table.visualRect(index)
    table.itemDelegate().initStyleOption(option, index)
    indicator = table.style().subElementRect(QtWidgets.QStyle.SE_ItemViewItemCheckIndicator, option, table)
    for expected in (False, True):
        QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=indicator.center())
        QtWidgets.QApplication.processEvents()
        assert {item.row() for item in table.selectedIndexes()} == set(rows)
        assert all(page._table_bool(table, row, 0, not expected) == expected for row in rows)


def test_line_enable_batch_preserves_modal_results_and_updates_animation_once(page, monkeypatch):
    render = Mock()
    monkeypatch.setattr(page, "_render_mode", render)
    page.last_mode = {"lines": page._line_rows(require_enabled=True)}
    mode = page.last_mode
    page._identification_stale = False
    page._preview_timer.start()
    select_rows(page.line_table, [0, 1])
    menu = page._modal_table_batch_menu(page.line_table)
    menu.actions()[1].trigger()
    assert page._line_rows(require_enabled=True) == []
    assert page.last_mode is mode and mode["lines"] == []
    assert not page._identification_stale
    assert page._preview_timer.isActive()
    assert render.call_count == 1
    menu.actions()[0].trigger()
    assert len(mode["lines"]) == 2
    assert render.call_count == 2
    menu.deleteLater()


def test_batch_commands_without_selection_do_not_change_data(page):
    menu = page._modal_table_batch_menu(page.point_table)
    assert all(not action.isEnabled() for action in menu.actions())
    assert page._set_modal_rows_enabled(page.point_table, False) == 0
    assert all(page._table_bool(page.point_table, row, 0, False) for row in range(3))
    menu.deleteLater()
