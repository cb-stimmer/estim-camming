"""End-to-end test of the control window: real engine process, offscreen Qt."""

import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest_engine import free_port, write_engine_config  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from estim_camming.gui.client import ControlClient  # noqa: E402
from estim_camming.gui.engine import EngineProcess  # noqa: E402
from estim_camming.gui.window import ControlWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(app, predicate, seconds=15.0, what="condition"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def press(app, widget, key):
    app.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


@pytest.fixture
def gui(qapp, tmp_path):
    port = free_port()
    config = write_engine_config(tmp_path, port)
    engine = EngineProcess(str(config))
    client = ControlClient(f"http://127.0.0.1:{port}")
    window = ControlWindow(client, engine)
    window.show()
    engine.start()
    client.start()
    state = {}
    client.stateChanged.connect(state.update)
    wait_until(qapp, lambda: window._connected, what="connection")
    yield qapp, window, engine, state
    window.close()


def test_arm_tip_stop_and_close(gui):
    app, window, engine, state = gui
    assert window.status_label.text() == "DISARMED"

    window.arm_button.click()
    wait_until(app, lambda: state.get("armed") is True, what="armed")
    assert window.status_label.text() == "ARMED"

    window.tip_user.setText("tester")
    window.tip_tokens.setValue(42)
    window._send_test_tip()
    wait_until(app, lambda: state.get("current") is not None, what="action playing")
    wait_until(app, lambda: window.tips_list.count() == 1, what="tip listed")
    assert window.tips_list.item(0).text().startswith("tester  42 tk")
    wait_until(app, lambda: window._bars["A"].value() > 0, what="output bar")

    press(app, window.stop_button, Qt.Key.Key_Escape)  # keyboard emergency stop
    wait_until(app, lambda: state.get("armed") is False, what="stopped")
    assert state["levels"] == {"A": 0.0, "B": 0.0}
    # The playing action ends at the scheduler's next tick.
    wait_until(app, lambda: state.get("current") is None, seconds=2, what="action ended")

    window.close()
    assert not engine.running()
    log = window.log_view.toPlainText()
    assert "controlling GUI went away" in log


def test_space_types_in_text_fields_but_stops_elsewhere(gui):
    app, window, engine, state = gui
    window.arm_button.click()
    wait_until(app, lambda: state.get("armed") is True, what="armed")

    window.tip_user.setFocus()
    app.processEvents()
    press(app, window.tip_user, Qt.Key.Key_Space)
    app.processEvents()
    time.sleep(0.3)
    window.client.poll()
    wait_until(app, lambda: True)
    assert state.get("armed") is True  # Space in a text field doesn't stop

    window.queue_list.setFocus()
    press(app, window.queue_list, Qt.Key.Key_Space)
    wait_until(app, lambda: state.get("armed") is False, what="stopped by Space")


def test_engine_crash_is_shown(gui):
    app, window, engine, state = gui
    engine._proc.kill()
    wait_until(app, lambda: window.status_label.text() == "ENGINE STOPPED", what="crash shown")
    assert window.log_view.isVisible()


def test_window_identity_and_icon(qapp):
    from PySide6.QtGui import QIcon

    from estim_camming.gui import model

    icon = QIcon(str(model.icon_path()))
    assert not icon.isNull() and not icon.pixmap(64, 64).isNull()


def test_on_top_checkbox_is_replaced_by_a_hint_on_wayland(qapp, monkeypatch):
    from estim_camming.gui import window as window_module

    class FakeApp:
        @staticmethod
        def platformName():  # noqa: N802
            return "wayland"

        @staticmethod
        def instance():
            return qapp

    monkeypatch.setattr(window_module, "QApplication", FakeApp)
    w = ControlWindow(ControlClient("http://127.0.0.1:9"))
    assert w.on_top.isHidden() and "KWin" in w.on_top_hint.text()
    qapp.removeEventFilter(w._key_filter)


def test_on_top_checkbox_shown_elsewhere(qapp):
    w = ControlWindow(ControlClient("http://127.0.0.1:9"))
    assert not w.on_top.isHidden() and not hasattr(w, "on_top_hint")
    qapp.removeEventFilter(w._key_filter)
