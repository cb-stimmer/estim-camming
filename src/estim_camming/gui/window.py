"""The control window: a compact panel meant to be tiled next to OBS and chat."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from estim_camming.gui import model
from estim_camming.gui.client import ControlClient
from estim_camming.gui.engine import EngineProcess
from estim_camming.gui.help import open_help

RED = "#c62828"
GREEN = "#2e7d32"
MAX_LIST_ROWS = 6
LOG_LINES = 1000


def _plain_label(text: str = "") -> QLabel:
    # Usernames and tip messages come from strangers: never render them as rich text.
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class StopKeyFilter(QObject):
    """Esc or Space anywhere in the window = emergency stop (Space still types
    in text fields). Installed on the application, so it runs before buttons
    see the key: Space can never activate the Arm button."""

    def __init__(self, on_stop, parent=None) -> None:
        super().__init__(parent)
        self._on_stop = on_stop

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() != QEvent.Type.KeyPress or event.isAutoRepeat():
            return False
        key = event.key()
        if key not in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            return False
        focus = QApplication.focusWidget()
        if key == Qt.Key.Key_Space and isinstance(focus, (QLineEdit, QAbstractSpinBox)):
            return False
        self._on_stop()
        return True


class ControlWindow(QMainWindow):
    def __init__(self, client: ControlClient, engine: EngineProcess | None = None) -> None:
        super().__init__()
        self.client = client
        self.engine = engine
        self._state: dict[str, Any] | None = None
        self._state_at = time.monotonic()
        self._connected = False
        self._engine_exit: str | None = None
        self._bars: dict[str, QProgressBar] = {}
        self.rules_window = None  # RulesWindow, created on first use

        self.setWindowTitle("estim-camming")
        self.resize(380, 820)
        self.setMinimumWidth(300)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._build_controls())
        layout.addWidget(self._build_levels())
        layout.addWidget(self._build_now_playing())
        layout.addWidget(self._build_tips())
        layout.addWidget(self._build_test_tip())
        if engine is not None:
            layout.addWidget(self._build_log())
        layout.addStretch(1)

        scroll = QScrollArea()  # stays usable when tiled in a short area
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)

        self.help_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), self, self.show_help)
        self._key_filter = StopKeyFilter(self.emergency_stop, self)
        QApplication.instance().installEventFilter(self._key_filter)

        client.stateChanged.connect(self.apply_state)
        client.connectionChanged.connect(self._on_connection)
        client.commandFailed.connect(self._on_command_failed)
        if engine is not None:
            engine.output.connect(self._append_log)
            engine.exited.connect(self._on_engine_exit)

        self._ticker = QTimer(self)  # smooth progress between polls
        self._ticker.setInterval(100)
        self._ticker.timeout.connect(self._update_progress)
        self._ticker.start()
        self._render_status()

    # -- construction ------------------------------------------------------------

    def _build_controls(self) -> QWidget:
        box = QWidget()
        grid = QVBoxLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.connection_label = _plain_label()
        self.connection_label.setStyleSheet("color: gray;")
        self.connection_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self.status_label, 1)
        top.addWidget(self.connection_label, 1)
        grid.addLayout(top)

        self.stop_button = QPushButton("STOP")
        self.stop_button.setToolTip("Emergency stop: disarm and clear the queue (Esc or Space)")
        self.stop_button.setMinimumHeight(72)
        self.stop_button.setStyleSheet(
            f"QPushButton {{ background: {RED}; color: white; font-size: 30px;"
            " font-weight: bold; border-radius: 8px; }"
            "QPushButton:pressed { background: #8e0000; }"
        )
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.clicked.connect(self.emergency_stop)
        grid.addWidget(self.stop_button)

        self.arm_button = QPushButton("Arm output")
        self.arm_button.setMinimumHeight(36)
        self.arm_button.setStyleSheet(
            f"QPushButton {{ background: {GREEN}; color: white; font-weight: bold;"
            " border-radius: 6px; } QPushButton:disabled { background: #9e9e9e; }"
        )
        self.arm_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space/Enter can't arm
        self.arm_button.clicked.connect(self.client.arm)
        grid.addWidget(self.arm_button)

        master = QHBoxLayout()
        master.addWidget(QLabel("Master"))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(0, 100)
        self.scale_slider.setToolTip("Scales all output; can only lower the configured limits")
        self.scale_slider.sliderReleased.connect(self._send_scale)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.scale_value = QLabel("–")
        self.scale_value.setMinimumWidth(40)
        master.addWidget(self.scale_slider, 1)
        master.addWidget(self.scale_value)
        grid.addLayout(master)

        tools = QHBoxLayout()
        self.rules_button = QPushButton("Edit rules…")
        self.rules_button.setToolTip("Edit the tip menu; saved rules apply to the next tip")
        self.rules_button.clicked.connect(self.open_rules)
        self.help_button = QPushButton("User guide")
        self.help_button.setToolTip("Open the user guide in your browser (F1)")
        self.help_button.clicked.connect(self.show_help)
        tools.addWidget(self.rules_button, 1)
        tools.addWidget(self.help_button)
        grid.addLayout(tools)

        self.on_top = QCheckBox("Keep window on top")
        self.on_top.toggled.connect(self._set_on_top)
        grid.addWidget(self.on_top)
        if QApplication.platformName() == "wayland":
            # Wayland doesn't let applications keep themselves on top; only the
            # compositor can (a KWin window rule matching APP_ID).
            self.on_top.setVisible(False)
            self.on_top_hint = _plain_label(
                f"Keep on top: add a KWin window rule for '{model.APP_ID}' (see the user guide)."
            )
            self.on_top_hint.setStyleSheet("color: gray; font-size: 11px;")
            grid.addWidget(self.on_top_hint)
        return box

    def _build_levels(self) -> QWidget:
        self.levels_box = QGroupBox("Output")
        self.levels_grid = QGridLayout(self.levels_box)
        return self.levels_box

    def _build_now_playing(self) -> QWidget:
        box = QGroupBox("Now playing")
        layout = QVBoxLayout(box)
        self.current_label = _plain_label("Idle")
        self.current_label.setStyleSheet("font-weight: bold;")
        self.remaining_label = QLabel()
        self.remaining_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        title = QHBoxLayout()
        title.addWidget(self.current_label, 1)
        title.addWidget(self.remaining_label)
        self.current_detail = _plain_label()
        self.current_progress = QProgressBar()
        self.current_progress.setRange(0, 1000)
        self.current_progress.setTextVisible(False)
        self.current_progress.setMaximumHeight(10)
        self.queue_label = QLabel("Queue")
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(MAX_LIST_ROWS * 20)
        buttons = QHBoxLayout()
        skip = QPushButton("Skip current")
        skip.clicked.connect(self.client.skip)
        clear = QPushButton("Clear queue")
        clear.clicked.connect(self.client.clear_queue)
        buttons.addWidget(skip)
        buttons.addWidget(clear)
        layout.addLayout(title)
        for widget in (
            self.current_detail,
            self.current_progress,
            self.queue_label,
            self.queue_list,
        ):
            layout.addWidget(widget)
        layout.addLayout(buttons)
        return box

    def _build_tips(self) -> QWidget:
        box = QGroupBox("Tips")
        layout = QVBoxLayout(box)
        self.total_label = QLabel("0 tokens this session")
        self.tips_list = QListWidget()
        self.tips_list.setMaximumHeight(MAX_LIST_ROWS * 20)
        self.platforms_label = _plain_label("No platforms")
        self.platforms_label.setStyleSheet("color: gray;")
        layout.addWidget(self.total_label)
        layout.addWidget(self.tips_list)
        layout.addWidget(self.platforms_label)
        return box

    def _build_test_tip(self) -> QWidget:
        box = QGroupBox("Test tip")
        row = QHBoxLayout(box)
        self.tip_user = QLineEdit("test")
        self.tip_user.setMaxLength(64)
        self.tip_tokens = QSpinBox()
        self.tip_tokens.setRange(1, 100000)
        self.tip_tokens.setValue(10)
        send = QPushButton("Send")
        send.clicked.connect(self._send_test_tip)
        self.tip_user.returnPressed.connect(self._send_test_tip)
        row.addWidget(self.tip_user, 1)
        row.addWidget(self.tip_tokens)
        row.addWidget(send)
        return box

    def _build_log(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        toggle = QToolButton()
        toggle.setText("Engine log")
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINES)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMinimumHeight(160)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 11px;")
        self.log_view.setVisible(False)

        def show_log(on: bool) -> None:
            self.log_view.setVisible(on)
            toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

        toggle.toggled.connect(show_log)
        self.log_toggle = toggle
        layout.addWidget(toggle)
        layout.addWidget(self.log_view)
        return box

    # -- actions -----------------------------------------------------------------

    def emergency_stop(self) -> None:
        self.client.emergency_stop()

    def show_help(self) -> None:
        if open_help(model.HELP_MAIN) is None:
            self.statusBar().showMessage(f"Could not open a browser. The guide: {model.DOCS_URL}")

    def open_rules(self) -> None:
        from estim_camming.gui.rules import RulesWindow

        if self.rules_window is None:
            self.rules_window = RulesWindow(self.client)
            self.rules_window.load()
        self.rules_window.show()
        self.rules_window.raise_()
        self.rules_window.activateWindow()

    def _send_scale(self) -> None:
        self.client.set_scale(self.scale_slider.value() / 100)

    def _on_scale_changed(self, value: int) -> None:
        self.scale_value.setText(f"{value}%")
        if not self.scale_slider.isSliderDown():  # keyboard / mouse wheel
            self._send_scale()

    def _send_test_tip(self) -> None:
        self.client.send_tip(self.tip_user.text().strip() or "test", self.tip_tokens.value())

    def _set_on_top(self, on: bool) -> None:
        # Works on X11 (and XWayland). Hidden on Wayland, where it has no effect.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    # -- state -------------------------------------------------------------------

    def apply_state(self, state: dict[str, Any]) -> None:
        self._state = state
        self._state_at = time.monotonic()
        self._render_status()

        if not self.scale_slider.isSliderDown():
            self.scale_slider.blockSignals(True)
            self.scale_slider.setValue(round(state.get("scale", 1.0) * 100))
            self.scale_slider.blockSignals(False)
            self.scale_value.setText(f"{self.scale_slider.value()}%")

        self._render_levels(state.get("channels", []), state.get("levels", {}))

        current = state.get("current")
        if current:
            self.current_label.setText(current.get("label", "?"))
            tip = current.get("tip") or {}
            who = f"{tip.get('username')} · " if tip.get("username") else ""
            self.current_detail.setText(who + model.format_outputs(current))
        else:
            self.current_label.setText("Idle")
            self.current_detail.setText("")
        self._update_progress()

        queue = state.get("queue", [])
        self.queue_label.setText(f"Queue ({len(queue)})")
        self._fill(self.queue_list, [model.queue_text(a) for a in queue])
        self._fill(self.tips_list, [model.tip_text(t) for t in state.get("recent_tips", [])])
        self.total_label.setText(f"{state.get('total_tokens', 0)} tokens this session")
        platforms = state.get("platforms", {})
        self.platforms_label.setText(
            "\n".join(model.platform_text(n, s) for n, s in platforms.items()) or "No platforms"
        )

    def _render_levels(self, channels: list[str], levels: dict[str, float]) -> None:
        for ch in channels:
            if ch not in self._bars:
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setFormat("%p%")
                row = len(self._bars)
                self.levels_grid.addWidget(QLabel(ch), row, 0)
                self.levels_grid.addWidget(bar, row, 1)
                self._bars[ch] = bar
            self._bars[ch].setValue(round(levels.get(ch, 0.0) * 1000))

    def _update_progress(self) -> None:
        current = (self._state or {}).get("current")
        if not current or not current.get("duration"):
            self.current_progress.setValue(0)
            self.remaining_label.setText("")
            return
        elapsed = current.get("elapsed", 0.0) + (time.monotonic() - self._state_at)
        duration = current["duration"]
        self.current_progress.setValue(round(min(elapsed / duration, 1.0) * 1000))
        self.remaining_label.setText(f"{model.format_seconds(duration - elapsed)} left")

    @staticmethod
    def _fill(widget: QListWidget, rows: list[str]) -> None:
        if [widget.item(i).text() for i in range(widget.count())] == rows:
            return
        widget.clear()
        widget.addItems(rows)

    def _render_status(self) -> None:
        armed = bool(self._state and self._state.get("armed"))
        if self._engine_exit is not None:
            text, color = "ENGINE STOPPED", RED
        elif not self._connected:
            text, color = "NOT CONNECTED", RED
        elif armed:
            text, color = "ARMED", GREEN
        else:
            text, color = "DISARMED", RED
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color};")
        self.arm_button.setVisible(not armed or not self._connected)
        self.arm_button.setEnabled(self._connected and self._engine_exit is None)

    # -- events ------------------------------------------------------------------

    def _on_connection(self, connected: bool, detail: str) -> None:
        self._connected = connected
        self.connection_label.setText("connected" if connected else "waiting for app…")
        self.connection_label.setToolTip(
            self.client.base_url if connected else f"{self.client.base_url}: {detail}"
        )
        self._render_status()

    def _on_command_failed(self, path: str, error: str) -> None:
        message = f"command {path} failed: {error}"
        self.statusBar().showMessage(message, 10000)
        if self.engine is not None:
            self._append_log(f"[gui] {message}")

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_engine_exit(self, code: int) -> None:
        if self.engine is not None and self.engine.stopping:
            return
        self._engine_exit = f"exit code {code}"
        self._append_log(f"[gui] engine stopped ({self._engine_exit})")
        self.log_toggle.setChecked(True)  # show why
        self.statusBar().showMessage("The engine stopped - see the log.")
        self._render_status()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.rules_window is not None and self.rules_window.isVisible():
            if not self.rules_window.close():  # asks about unsaved changes
                event.ignore()
                return
        self.client.stop()
        if self.engine is not None and self.engine.running():
            self.status_label.setText("STOPPING…")
            QApplication.processEvents()
            self.engine.shutdown()  # zeroes the device on the way out
        QApplication.instance().removeEventFilter(self._key_filter)
        event.accept()
