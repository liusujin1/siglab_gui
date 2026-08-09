"""Tests for the filter configuration dialog (FilterDlg port)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from python_sidmat.measurement.filters import FILTER_TYPES, filter_name
from python_sidmat.ui.filter_dialog import FilterDialog


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_dialog_lists_24_filter_types():
    _app()
    dlg = FilterDialog(0)
    assert dlg.type_cbx.count() == 24
    assert dlg.type_cbx.itemText(0).startswith("NOFIL")
    assert dlg.type_cbx.itemText(1).startswith("LPF1O")
    assert dlg.type_cbx.itemText(23).startswith("LOPID")
    dlg.deleteLater()


def test_dialog_reflects_initial_values():
    _app()
    dlg = FilterDialog(2, filter_type_id=5, params=[100.0, 0.7, 1.0, 0.0, 0.0])
    assert dlg.type_cbx.currentIndex() == 5
    assert dlg.filter_type_id == 5
    assert dlg.filter_params == [100.0, 0.7, 1.0, 0.0, 0.0]
    dlg.deleteLater()


def test_unused_params_hidden_for_lpf():
    _app()
    dlg = FilterDialog(0, filter_type_id=1)  # LPF1O: params 0, 2 used
    desc = FILTER_TYPES[1][2]
    for i, (lbl, edit) in enumerate(zip(dlg._par_lbls, dlg._par_edits)):
        assert lbl.isHidden() == (desc[i] == "unused")
        assert edit.isHidden() == (desc[i] == "unused")
    dlg.deleteLater()


def test_type_switch_relabels_params():
    _app()
    dlg = FilterDialog(0, filter_type_id=1)
    dlg.type_cbx.setCurrentIndex(6)  # NOTCH: params 0,1,2 used
    desc = FILTER_TYPES[6][2]
    for i, lbl in enumerate(dlg._par_lbls):
        assert lbl.text() == desc[i]
    assert dlg.filter_type_id == 6
    dlg.deleteLater()


def test_type_switch_preserves_typed_values():
    _app()
    dlg = FilterDialog(0, filter_type_id=1, params=[10.0, 2.0, 3.0, 4.0, 5.0])
    dlg._par_edits[0].setText("123.5")
    dlg.type_cbx.setCurrentIndex(6)
    assert dlg._par_edits[0].text() == "123.5"
    dlg.deleteLater()


def test_param_parse_and_accept():
    _app()
    dlg = FilterDialog(0, filter_type_id=5, params=[50.0, 1.0, 2.0, 0.0, 0.0])
    dlg._par_edits[0].setText("120.5")
    dlg._par_edits[1].setText("1.25")
    assert dlg.filter_params[0] == 120.5
    assert dlg.filter_params[1] == 1.25
    dlg.deleteLater()


def test_bad_param_text_is_rejected_instead_of_silently_writing_zero():
    _app()
    dlg = FilterDialog(0, filter_type_id=5, params=[1.0] * 5)
    dlg._par_edits[2].setText("not-a-number")
    with pytest.raises(ValueError, match="filter parameter 3 is not a number"):
        _ = dlg.filter_params
    dlg.deleteLater()


def test_nonfinite_param_is_rejected():
    _app()
    dlg = FilterDialog(0, filter_type_id=5, params=[1.0] * 5)
    dlg._par_edits[0].setText("nan")
    with pytest.raises(ValueError, match="must be finite"):
        _ = dlg.filter_params
    dlg.deleteLater()


def test_unknown_filter_id_does_not_index_past_table():
    assert filter_name(24) == "UKNOWN"
    _app()
    with pytest.raises(ValueError, match="unsupported filter type"):
        FilterDialog(0, filter_type_id=24)


def test_filter_short_names_match_legacy_table():
    assert filter_name(16) == "HPFQF"
    assert filter_name(17) == "LPFQF"
    assert filter_name(21) == "VARFIL"
