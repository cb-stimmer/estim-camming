"""End-to-end test of the rules editor: real engine process, offscreen Qt."""

import json
import os
import tomllib
import urllib.request

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest_engine import free_port, write_engine_config  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from test_gui import wait_until  # noqa: E402

from estim_camming.gui.client import ControlClient  # noqa: E402
from estim_camming.gui.engine import EngineProcess  # noqa: E402
from estim_camming.gui.window import ControlWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp, tmp_path):
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
    window.rules_button.click()
    rules = window.rules_window
    wait_until(qapp, lambda: rules._info is not None, what="rules loaded")
    yield qapp, window, rules, state, config, port
    rules._dirty = False  # no prompt on teardown
    window.close()


def saved_rules(config):
    return tomllib.loads(config.read_text())["rules"]


def test_edit_save_applies_and_writes_the_file(editor):
    app, window, rules, state, config, _ = editor
    assert rules.tree.topLevelItemCount() == 1
    assert rules.form.name.text() == "any" and rules.form.ranged.isChecked()
    rules.form.label.setText("Anything")
    rules.form.main.pattern.setCurrentIndex(rules.form.main.pattern.findData("pulse"))
    assert rules._dirty and rules._draft[0]["pattern"] == "pulse"
    assert rules.tree.topLevelItem(0).text(2) == "Anything"
    rules.save_button.click()
    wait_until(app, lambda: not rules._dirty, what="saved")
    assert "Saved and applied" in rules.status.text()
    wait_until(app, lambda: state.get("rules_revision") == 1, what="engine revision")
    assert [m["label"] for m in state["menu"]] == ["Anything"]
    [saved] = saved_rules(config)
    assert saved["label"] == "Anything" and saved["pattern"] == "pulse"


def test_shadowed_rule_warning_and_reorder(editor):
    app, window, rules, state, config, _ = editor
    rules._add()  # after the catch-all "any" (1+): can never match
    assert rules.tree.topLevelItemCount() == 2 and rules._current == 1
    assert rules.tree.topLevelItem(1).text(0).startswith("⚠")
    assert any("never matches" in p["message"] for p in rules._problems)
    assert rules.save_button.isEnabled()  # a warning doesn't block saving
    rules._move(-1)
    assert rules._draft[0]["name"] == "rule" and rules._current == 0
    assert not any(p["level"] == "warning" for p in rules._problems)
    assert "rule 1" in rules.try_result.text() or "rule 2" in rules.try_result.text()


def test_errors_disable_save(editor):
    app, window, rules, state, config, _ = editor
    rules._add()
    rules.form.name.setText("any")  # duplicate name
    assert rules._errors() == 1 and not rules.save_button.isEnabled()
    assert any(p["field"] == "name" for p in rules._problems if p["level"] == "error")
    rules.form.name.setText("other")
    assert rules._errors() == 0 and rules.save_button.isEnabled()


def test_per_channel_rule_round_trip(editor):
    app, window, rules, state, config, _ = editor
    rules.form.per_channel.setChecked(True)
    tab_b = rules.form.channel_tabs["B"]
    tab_b.own_intensity.setChecked(True)
    tab_b.output.intensity.spin.setValue(0.2)
    tab_a = rules.form.channel_tabs["A"]
    tab_a.own_pattern.setChecked(True)
    tab_a.output.pattern.setCurrentIndex(tab_a.output.pattern.findData("wave"))
    assert rules._draft[0]["channels"] == {
        "A": {"pattern": "wave", "params": {}},
        "B": {"intensity": 0.2},
    }
    assert rules._errors() == 0
    rules.save_button.click()
    wait_until(app, lambda: not rules._dirty, what="saved")
    [saved] = saved_rules(config)
    assert saved["channels"] == {"A": {"pattern": "wave", "params": {}}, "B": {"intensity": 0.2}}
    # The form shows what was saved after a reload.
    rules.load()
    wait_until(app, lambda: rules._revision == 1 and not rules._busy, what="reloaded")
    assert rules.form.per_channel.isChecked()
    assert rules.form.channel_tabs["A"].output.pattern.currentData() == "wave"


def test_change_elsewhere_shows_a_banner_and_save_conflicts(editor):
    app, window, rules, state, config, port = editor
    rules.form.label.setText("mine")
    other = [{"name": "theirs", "min_tokens": 1, "intensity": 0.1, "duration": 1}]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/rules",
        data=json.dumps({"revision": 0, "rules": other}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=3).read()
    wait_until(app, lambda: rules.banner.isVisible(), what="banner")
    assert "changed elsewhere" in rules.banner_text.text()
    rules.banner_keep.click()
    rules.save_button.click()
    wait_until(app, lambda: rules.banner_overwrite.isVisible(), what="conflict")
    assert rules._dirty
    rules.banner_overwrite.click()
    wait_until(app, lambda: not rules._dirty, what="overwritten")
    assert saved_rules(config)[0]["label"] == "mine"


def test_close_with_unsaved_changes_asks(editor, monkeypatch):
    app, window, rules, state, config, _ = editor
    rules.form.label.setText("changed")
    answers = iter([QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))
    assert not rules.close() and rules.isVisible()
    assert rules.close() and not rules.isVisible()
    assert "label" not in saved_rules(config)[0]


def test_escape_in_the_editor_stops(editor):
    app, window, rules, state, config, _ = editor
    window.arm_button.click()
    wait_until(app, lambda: state.get("armed") is True, what="armed")
    app.sendEvent(
        rules.tree,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    )
    wait_until(app, lambda: state.get("armed") is False, what="stopped by Esc")


def test_drag_and_drop_reorders_the_draft(editor):
    app, window, rules, state, config, _ = editor
    rules._add()
    names = [r["name"] for r in rules._draft]
    item = rules.tree.takeTopLevelItem(1)  # what a drop does: remove + insert
    rules.tree.insertTopLevelItem(0, item)
    wait_until(app, lambda: [r["name"] for r in rules._draft] == names[::-1], what="reordered")
    assert rules.tree.topLevelItem(0).text(0).endswith("1")
    assert rules._dirty
