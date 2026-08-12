from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import numpy as np

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6 import QtCore, QtWidgets

from python_samba.logging_tools.live_curve import LiveCurveSessionBuffer, MonitorSignalSpec
from python_samba.services.session import open_mock
from python_samba.ui.live_curve_window import LiveCurveWindow
from python_samba.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    instance = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield instance


def _process(app, rounds=8):
    for _ in range(rounds):
        app.processEvents(QtCore.QEventLoop.AllEvents, 20)


def test_navigation_action_opens_nonmodal_without_switching_page(app):
    window = MainWindow()
    try:
        # Geometry assertions are meaningful only after the top-level window
        # has completed its first layout pass.
        window.show()
        _process(app)
        initial = window.main_tabs.currentIndex()
        assert window.realtime_curve_nav_button.text() == "Real-time curve"
        window.realtime_curve_nav_button.click()
        _process(app)
        assert window.main_tabs.currentIndex() == initial
        assert window.realtime_curve_window.isVisible()
        assert window.realtime_curve_window.isModal() is False
        navigation_scroll = window.findChild(
            QtWidgets.QScrollArea, "mainNavigationScroll"
        )
        assert navigation_scroll is not None
        assert navigation_scroll.horizontalScrollBar().maximum() == 0
        assert window.main_navigation.width() <= navigation_scroll.viewport().width()
    finally:
        window.realtime_curve_window.hide()
        window.close()


def test_three_state_tree_limit_and_first_six_visible(app):
    window = LiveCurveWindow()
    try:
        children = list(window._spec_items.values())[:41]
        assert len(children) == 41
        for child in children:
            child.setCheckState(0, QtCore.Qt.Checked)
        _process(app)
        assert len(window._selected_specs) == 40
        assert sum(key in window._visible_keys for key in [spec.key for spec in window._selected_specs]) == 6
        parent = children[0].parent()
        assert parent.checkState(0) in {QtCore.Qt.PartiallyChecked, QtCore.Qt.Checked}
    finally:
        window.hide()
        window.deleteLater()


def test_plot_items_keep_identity_and_follow_pauses_on_navigation(app):
    window = LiveCurveWindow()
    signals = tuple(
        MonitorSignalSpec(f"S{index}", "Sensor", 0, index, 0)
        for index in range(3)
    )
    window._selected_specs = list(signals)
    window._buffer = LiveCurveSessionBuffer(signals, chunk_size=16)
    window._reset_curve_items(signals)
    identities = {key: id(item) for key, item in window._curve_items.items()}
    window._buffer.start_segment(1.0)
    for index in range(20):
        window._buffer.append(
            f"2026-08-12T00:00:{index:02d}+00:00",
            float(index),
            [index, index * 2, -index],
        )
    window._refresh_plot()
    assert identities == {key: id(item) for key, item in window._curve_items.items()}
    assert window._view_box.viewRange()[0][1] == pytest.approx(60.0, abs=0.2)
    window._navigation_started()
    assert window._follow is False
    window._view_box.setXRange(5, 15, padding=0)
    window.resume_follow()
    assert window._follow is True
    assert window._follow_span_s == pytest.approx(10.0)
    window.copy_plot()
    assert not QtWidgets.QApplication.clipboard().pixmap().isNull()
    window.hide()
    window.deleteLater()


def test_stop_restart_gap_is_rendered_with_nan_separator(app):
    window = LiveCurveWindow()
    signal = MonitorSignalSpec("S0", "Sensor", 0, 0, 0)
    buffer = LiveCurveSessionBuffer((signal,), chunk_size=16)
    buffer.start_segment(10.0)
    buffer.append("2026-08-12T00:00:00+00:00", 0.0, [1.0])
    buffer.append("2026-08-12T00:00:01+00:00", 1.0, [2.0])
    buffer.stop_segment(12.0, "operator stop")
    buffer.start_segment(20.0)
    buffer.append("2026-08-12T00:00:10+00:00", 10.0, [3.0])
    window._selected_specs = [signal]
    window._buffer = buffer
    window._reset_curve_items((signal,))
    window._refresh_plot()
    x_data, y_data = window._curve_items[signal.key].getData()
    assert len(x_data) == 5
    assert np.count_nonzero(np.isnan(y_data)) == 2
    window.hide()
    window.deleteLater()


def test_cursor_markers_tips_and_zoom_history(app):
    window = LiveCurveWindow()
    signal = MonitorSignalSpec("S0", "Sensor", 0, 0, 0)
    buffer = LiveCurveSessionBuffer((signal,), chunk_size=16)
    buffer.start_segment(1.0)
    for index in range(8):
        buffer.append(
            f"2026-08-12T00:00:{index:02d}+00:00",
            float(index),
            [float(index * index)],
        )
    window._selected_specs = [signal]
    window._buffer = buffer
    window._reset_curve_items((signal,))
    window._refresh_plot()
    nearest = window._nearest_for_x(signal.key, 3.2)
    assert nearest is not None
    window._update_cursor(nearest)
    window._add_data_tip(nearest)
    assert window._cursor_state is not None
    assert len(window._data_tips) == 1
    window.set_marker("A")
    window._update_cursor(window._nearest_for_x(signal.key, 6.0))
    window.set_marker("B")
    assert set(window._markers) == {"A", "B"}
    assert "Δ" in window.marker_readout.text()
    before = window._view_box.viewRange()
    window._rubber_zoom(QtCore.QPointF(1.0, 0.0), QtCore.QPointF(5.0, 30.0))
    assert window._zoom_history
    window.previous_zoom()
    after = window._view_box.viewRange()
    assert after[0][0] == pytest.approx(before[0][0])
    window.clear_annotations()
    assert window._cursor_state is None
    assert not window._markers and not window._data_tips
    window.hide()
    window.deleteLater()


def test_logging_and_status_monitor_paths_are_interlocked(app):
    from python_samba.ui.patches import apply_all_patches

    class PatchedMainWindow(MainWindow):
        pass

    apply_all_patches(PatchedMainWindow, strict=True)
    main = PatchedMainWindow()
    session = open_mock(readonly=False)
    session.open()
    try:
        main.session = session
        main._on_realtime_curve_lease_changed(True)
        page = main.logging_page_widget
        assert page._external_monitor_lease is True
        assert page.btn_file_start.isEnabled() is False
        assert page.btn_internal_start.isEnabled() is False
        assert page.btn_internal_stop.isEnabled() is False
        assert page.logging_type_combo.isEnabled() is False
        assert page.event_selector.isEnabled() is False
        assert page.event_threshold.isEnabled() is False
        assert page.btn_trace_download.isEnabled() is False
        assert page.trace_selector.isEnabled() is False
        assert page.monitor_table.isEnabled() is False
        before = tuple(session.get_monitor_signal(0))
        main._on_sig_selector_changed(0, (1, 0, 0), main.sig_selectors[0])
        assert tuple(session.get_monitor_signal(0)) == before
        main._on_realtime_curve_lease_changed(False)
        assert page._external_monitor_lease is False
    finally:
        main.session = None
        session.close()
        main.close()


def test_start_stop_restores_mock_monitor_slots_and_logging_interlock(app, tmp_path, monkeypatch):
    session = open_mock(readonly=False)
    session.open()
    original = tuple(session.get_monitor_signals(40))
    window = LiveCurveWindow()
    from python_samba.services import monitor_lease
    from python_samba.ui import live_curve_window

    class TemporaryLease(monitor_lease.MonitorSlotLease):
        def __init__(self, session, **kwargs):
            super().__init__(session, recovery_directory=tmp_path, **kwargs)

    monkeypatch.setattr(live_curve_window, "MonitorSlotLease", TemporaryLease)
    try:
        window.set_connection(session, constants=[0, 6, 3, 6, 0, 6, 7, 4, 3, 0, 5000])
        children = list(window._spec_items.values())
        for child in children[10:13]:
            child.setCheckState(0, QtCore.Qt.Checked)
        window.interval_ms.setValue(20)
        changes = []
        window.lease_active_changed.connect(changes.append)
        window._start_clicked()
        deadline = QtCore.QDeadlineTimer(4000)
        while not window.running and not deadline.hasExpired():
            _process(app, 2)
        assert window.running
        assert tuple(session.get_monitor_signals(3)) != original[:3]
        identity_before = {key: id(item) for key, item in window._curve_items.items()}
        sample_deadline = QtCore.QDeadlineTimer(2000)
        while window._buffer.sample_count < 2 and not sample_deadline.hasExpired():
            _process(app, 2)
        window._refresh_plot()
        assert identity_before == {key: id(item) for key, item in window._curve_items.items()}
        assert window.stop_and_restore()
        assert tuple(session.get_monitor_signals(40)) == original
        assert True in changes and False in changes
    finally:
        window.hide()
        window.deleteLater()
        session.close()
