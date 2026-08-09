"""Deep Event Logging + PFF tests."""

from __future__ import annotations

import pytest

from python_samba.services.session import open_mock
from python_samba.ui.page_specs import PAGE_SPECS


def test_all_pages_ready():
    assert all(p.status == "ready" for p in PAGE_SPECS), [
        p.page_id for p in PAGE_SPECS if p.status != "ready"
    ]


def test_monitor_signals_and_live_values():
    with open_mock(readonly=False) as s:
        assert s.get_monitor_signal(0) == ["0", "0", "0"]
        s.set_monitor_signal(2, 1, 5, 0)
        assert s.get_monitor_signal(2) == ["1", "5", "0"]
        live = s.get_monitor_values(0, 3)
        assert len(live) == 4


def test_download_logged_trace_and_event_time():
    with open_mock(readonly=False) as s:
        rows = s.download_logged_trace(0)
        assert len(rows) == 8
        assert len(rows[0]) == 2
        assert rows[3][0] == pytest.approx(3.0)
        et = s.get_event_time(0)
        assert len(et) == 4
        # start clears, stop creates
        s.start_stop_event_tracing(1)
        info = s.get_event_trace_info()
        assert info[0] == "1"
        s.start_stop_event_tracing(0)
        rows2 = s.download_logged_trace(0, max_samples=5)
        assert len(rows2) == 5
        assert len(rows2[0]) == 2


def test_pff_filter_params_gains_reset_inputs():
    with open_mock(readonly=False) as s:
        fs = s.get_pff_filter(0, 0, 0)
        assert fs.filter_type == 3
        s.set_pff_filter(0, 0, 0, 2, (0.3, 0.0, 1.0, 0.0, 0.0))
        assert s.get_pff_filter(0, 0, 0).filter_type == 2
        s.set_pff_parameters(1, "5", 0.02)
        assert s.get_pff_parameters(1)[0] == "5"
        s.set_pff_gains_as(0, 0, [9.0, 8.0, 7.0])
        assert s.get_pff_gains_as(0, 0) == pytest.approx([9.0, 8.0, 7.0])
        s.reset_pff_fir(0, 0)
        assert all(v == 0.0 for v in s.get_pff_gains_as(0, 0))
        s.set_pff_inputs([10, 11, 12])
        assert s.get_pff_inputs() == [10, 11, 12]


def test_gui_logging_pff_widgets_exist():
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    from python_samba.ui.main_window import MainWindow
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = PatchedMainWindow()
    assert hasattr(win, "pff_filter")
    assert hasattr(win, "pff_filter_panel")
    assert len(win.pff_ref_buttons) == 12
    assert len(win.pff_sec_buttons) == 12
    assert len(win.pff_err_buttons) == 6
    assert win.pff_filter.stage.maximum() == 7
    assert hasattr(win, "on_pff_reset")
    assert hasattr(win, "on_setup_load_file")
    assert hasattr(win, "on_nvram")
    assert "Logging" in [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
    win.close()
