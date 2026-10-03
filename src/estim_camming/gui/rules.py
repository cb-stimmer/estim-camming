"""The rules editor window: edit the tip menu while the engine runs.

A client of the control API like the main window (see ``docs/design/rules-editor.md``):
it loads ``GET /api/rules``, edits a local draft, checks it locally (the same
``check_rules`` the engine uses) and with ``POST /api/rules/check``, and saves
with ``POST /api/rules``, which applies the rules at once and writes config.toml.
"""

from __future__ import annotations

import copy
import itertools
import json
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from estim_camming.gui import model, rules_model
from estim_camming.gui.client import ControlClient
from estim_camming.gui.help import open_help
from estim_camming.rules import Rule
from estim_camming.rules_io import check_rules, uncovered_amounts

MAX_TOKENS = 10_000_000
CHECK_DELAY_MS = 300
ICONS = {"error": "⛔", "warning": "⚠", "info": "ℹ"}
CHANNEL_COLORS = ["#1e88e5", "#fb8c00", "#43a047", "#8e24aa"]
ORANGE = "#ef6c00"
RED = "#c62828"
GREEN = "#2e7d32"


def _plain(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class _NoWheelFilter(QObject):
    """Scrolling the form must not change a spin box or combo under the mouse."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


_NO_WHEEL = _NoWheelFilter()


def _guard(widget: QWidget) -> QWidget:
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_NO_WHEEL)
    return widget


# -- small editors ---------------------------------------------------------------------


class IntensityEdit(QWidget):
    """Slider + spin box for 0..1, optionally with a "set" checkbox."""

    changed = Signal()

    def __init__(self, optional: bool = False, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("set")
        self.enabled.setVisible(optional)
        self.enabled.setChecked(True)
        self.slider = _guard(QSlider(Qt.Orientation.Horizontal))
        self.slider.setRange(0, 100)
        self.spin = _guard(QDoubleSpinBox())
        self.spin.setRange(0, 1)
        self.spin.setDecimals(2)
        self.spin.setSingleStep(0.05)
        row.addWidget(self.enabled)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        self.slider.valueChanged.connect(lambda v: self._sync(v / 100, from_slider=True))
        self.spin.valueChanged.connect(lambda v: self._sync(v, from_slider=False))
        self.enabled.toggled.connect(self._on_toggle)

    def _sync(self, value: float, from_slider: bool) -> None:
        widget = self.spin if from_slider else self.slider
        widget.blockSignals(True)
        if from_slider:
            self.spin.setValue(value)
        else:
            self.slider.setValue(round(value * 100))
        widget.blockSignals(False)
        self.changed.emit()

    def _on_toggle(self, on: bool) -> None:
        self.slider.setEnabled(on)
        self.spin.setEnabled(on)
        self.changed.emit()

    def set_optional(self, optional: bool) -> None:
        self.enabled.setVisible(optional)
        if not optional:
            self.enabled.setChecked(True)

    def set_value(self, value: float | None) -> None:
        self.blockSignals(True)
        self.enabled.setChecked(value is not None)
        self.spin.setValue(value if value is not None else 0.3)
        self.slider.setValue(round(self.spin.value() * 100))
        self.slider.setEnabled(value is not None)
        self.spin.setEnabled(value is not None)
        self.blockSignals(False)

    def value(self) -> float | None:
        return round(self.spin.value(), 2) if self.enabled.isChecked() else None


class ParamsForm(QWidget):
    """Pattern options form built from the pattern's JSON schema. Only values that
    differ from the default are returned; unknown keys are kept as they are."""

    changed = Signal()

    def __init__(self, schema: dict[str, Any], values: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self._fields = rules_model.param_fields(schema)
        known = {f.name for f in self._fields}
        self._extra = {k: v for k, v in values.items() if k not in known}
        self._readers = {}
        for field in self._fields:
            widget, reader = self._build(field, values.get(field.name, field.default))
            widget.setToolTip(field.description)
            form.addRow(field.title, widget)
            self._readers[field.name] = (field, reader)
        if not self._fields:
            form.addRow(_plain("No options."))
        if self._extra:
            extra = _plain(f"Unknown options kept: {', '.join(self._extra)}")
            extra.setStyleSheet(f"color: {RED};")
            form.addRow(extra)

    def _build(self, field: rules_model.ParamField, value: Any):
        # The widgets' signals carry the new value; changed() takes no arguments.
        def emit(*_: Any) -> None:
            self.changed.emit()

        if field.kind in ("number", "integer"):
            spin = _guard(QDoubleSpinBox() if field.kind == "number" else QSpinBox())
            low = field.minimum if field.minimum is not None else -1e9
            high = field.maximum if field.maximum is not None else 1e9
            if field.kind == "number":
                spin.setDecimals(3)
                step = 0.001
                spin.setSingleStep(0.05 if high <= 1 else 0.1)
            else:
                low, high, step = max(int(low), -(2**31)), min(int(high), 2**31 - 1), 1
            spin.setRange(
                low + (step if field.exclusive_minimum else 0),
                high - (step if field.exclusive_maximum else 0),
            )
            if value is not None:
                spin.setValue(value)
            spin.valueChanged.connect(emit)
            if not field.optional:
                return spin, spin.value
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            enabled = QCheckBox("set")
            enabled.setChecked(value is not None)
            spin.setEnabled(value is not None)
            enabled.toggled.connect(spin.setEnabled)
            enabled.toggled.connect(emit)
            row.addWidget(enabled)
            row.addWidget(spin, 1)
            return box, lambda: spin.value() if enabled.isChecked() else None
        if field.kind == "boolean":
            check = QCheckBox()
            check.setChecked(bool(value))
            check.toggled.connect(emit)
            return check, check.isChecked
        if field.kind == "choice":
            combo = _guard(QComboBox())
            for choice in field.choices:
                combo.addItem(str(choice), choice)
            if value in field.choices:
                combo.setCurrentIndex(field.choices.index(value))
            combo.currentIndexChanged.connect(emit)
            return combo, combo.currentData
        edit = QLineEdit("" if value is None else json.dumps(value))
        edit.textChanged.connect(emit)

        def read() -> Any:
            text = edit.text().strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except ValueError:
                return text  # the check reports it

        return edit, read

    def values(self) -> dict[str, Any]:
        result = {}
        for name, (field, reader) in self._readers.items():
            value = reader()
            if isinstance(value, float):
                value = round(value, 3)
            if value != field.default:
                result[name] = value
        return result | self._extra


class OutputEditor(QWidget):
    """Pattern, its options and an intensity."""

    changed = Signal()

    def __init__(self, patterns: dict[str, Any], optional_intensity=False, parent=None) -> None:
        super().__init__(parent)
        self._patterns = patterns
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.pattern = _guard(QComboBox())
        for name, info in patterns.items():
            self.pattern.addItem(name, name)
            self.pattern.setItemData(
                self.pattern.count() - 1, info.get("description", ""), Qt.ItemDataRole.ToolTipRole
            )
        self.params_box = QVBoxLayout()
        self.params_box.setContentsMargins(12, 0, 0, 0)
        self.params: ParamsForm | None = None
        self.intensity = IntensityEdit(optional_intensity)
        self.hint = _plain()
        self.hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("Pattern", self.pattern)
        form.addRow(self.params_box)
        form.addRow("Intensity", self.intensity)
        form.addRow(self.hint)
        self.pattern.currentIndexChanged.connect(self._on_pattern)
        self.intensity.changed.connect(self.changed.emit)
        self._set_params({})

    def _set_params(self, values: dict[str, Any]) -> None:
        if self.params is not None:
            # Hide it now: deleteLater only runs once control is back in the event loop.
            self.params_box.removeWidget(self.params)
            self.params.hide()
            self.params.setParent(None)
            self.params.deleteLater()
        name = self.pattern.currentData()
        schema = (self._patterns.get(name) or {}).get("schema", {})
        self.params = ParamsForm(schema, values)
        self.params.changed.connect(self.changed.emit)
        self.params_box.addWidget(self.params)

    def _on_pattern(self) -> None:
        self._set_params({})  # options of the old pattern don't apply
        self.changed.emit()

    def load(self, pattern: str, params: dict[str, Any], intensity: float | None) -> None:
        self.blockSignals(True)
        if self.pattern.findData(pattern) < 0:
            self.pattern.addItem(f"{pattern} (unknown)", pattern)
        self.pattern.blockSignals(True)
        self.pattern.setCurrentIndex(self.pattern.findData(pattern))
        self.pattern.blockSignals(False)
        self._set_params(dict(params or {}))
        self.intensity.set_value(intensity)
        self.blockSignals(False)

    def value(self) -> tuple[str, dict[str, Any], float | None]:
        assert self.params is not None
        return self.pattern.currentData(), self.params.values(), self.intensity.value()


class ChannelTab(QWidget):
    """Per-channel settings: active, own pattern + options, own intensity."""

    changed = Signal()

    def __init__(self, channel: str, patterns: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.channel = channel
        layout = QVBoxLayout(self)
        self.active = QCheckBox(f"Channel {channel} plays this rule (otherwise it stays at 0)")
        self.own_pattern = QCheckBox("Own pattern")
        self.own_intensity = QCheckBox("Own intensity")
        self.output = OutputEditor(patterns)
        layout.addWidget(self.active)
        layout.addWidget(self.own_pattern)
        layout.addWidget(self.own_intensity)
        layout.addWidget(self.output)
        layout.addStretch(1)
        for box in (self.active, self.own_pattern, self.own_intensity):
            box.toggled.connect(self._update)
        self.output.changed.connect(self.changed.emit)

    def _update(self) -> None:
        active = self.active.isChecked()
        self.own_pattern.setEnabled(active)
        self.own_intensity.setEnabled(active)
        self.output.pattern.setEnabled(active and self.own_pattern.isChecked())
        if self.output.params is not None:
            self.output.params.setEnabled(active and self.own_pattern.isChecked())
        self.output.intensity.setEnabled(active and self.own_intensity.isChecked())
        self.changed.emit()

    def load(self, spec: dict[str, Any] | None, rule: dict[str, Any]) -> None:
        self.blockSignals(True)
        spec = spec if isinstance(spec, dict) else None
        rule_pattern = rule.get("pattern", "constant")
        own_pattern = bool(spec) and ("pattern" in spec or "params" in spec)
        pattern = (spec or {}).get("pattern", rule_pattern)
        if spec and "params" in spec:
            params = spec["params"]
        else:
            params = rule.get("params", {}) if pattern == rule_pattern else {}
        intensity = (spec or {}).get("intensity", rule.get("intensity"))
        self.active.setChecked(spec is not None)
        self.own_pattern.setChecked(own_pattern)
        self.own_intensity.setChecked(bool(spec) and "intensity" in spec)
        self.output.load(pattern, params, intensity if intensity is not None else 0.3)
        self.blockSignals(False)
        self._update()

    def spec(self) -> dict[str, Any] | None:
        if not self.active.isChecked():
            return None
        pattern, params, intensity = self.output.value()
        spec: dict[str, Any] = {}
        if self.own_pattern.isChecked():
            spec["pattern"] = pattern
            spec["params"] = params  # explicit: never inherit options from the rule
        if self.own_intensity.isChecked():
            spec["intensity"] = intensity
        return spec


class PreviewWidget(QWidget):
    """Level per channel over the rule's (base) duration. Random patterns show
    their range as a band. 1.0 = the safety maximum of the channel."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(90)
        self._series: dict[str, tuple[list[float], list[float]]] = {}
        self._duration = 0.0
        self._message = ""

    def show_series(self, series: dict[str, tuple[list[float], list[float]]], duration: float):
        self._series, self._duration, self._message = series, duration, ""
        self.update()

    def show_message(self, message: str) -> None:
        self._series, self._message = {}, message
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -16)
        painter.setPen(QPen(QColor("#9e9e9e")))
        painter.drawRect(rect)
        if self._message or not self._series:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message or "No output")
            return
        painter.drawText(
            self.rect().adjusted(6, 0, -6, 0),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
            f"0–{self._duration:g} s",
        )
        legend = []
        for i, (channel, (low, high)) in enumerate(self._series.items()):
            color = QColor(CHANNEL_COLORS[i % len(CHANNEL_COLORS)])
            legend.append((channel, color))
            n = len(low)

            def point(j: int, level: float, n: int = n) -> QPointF:
                return QPointF(
                    rect.left() + rect.width() * j / max(n - 1, 1),
                    rect.bottom() - rect.height() * level,
                )

            if any(h - lo > 1e-6 for lo, h in zip(low, high, strict=True)):
                # Varies per tip (random patterns): show the range only.
                band = QPainterPath(point(0, high[0]))
                for j in range(1, n):
                    band.lineTo(point(j, high[j]))
                for j in reversed(range(n)):
                    band.lineTo(point(j, low[j]))
                band.closeSubpath()
                fill = QColor(color)
                fill.setAlpha(70)
                painter.fillPath(band, QBrush(fill))
                continue
            line = QPainterPath(point(0, high[0]))
            for j in range(1, n):
                line.lineTo(point(j, high[j]))
            painter.setPen(QPen(color, 2))
            painter.drawPath(line)
        x = 8
        for channel, color in legend:
            painter.setPen(QPen(color))
            painter.drawText(x, self.height() - 3, channel)
            x += 18


# -- the rule form ---------------------------------------------------------------------


class RuleForm(QWidget):
    """Edits one rule (a config-shaped dict)."""

    changed = Signal()

    def __init__(self, info: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._info = info
        self._loading = False
        self._extra_channels: Any = None
        self._channels: list[str] = list(info["channels"])
        patterns = info["patterns"]
        limits = info["limits"]
        self._limits = limits
        layout = QVBoxLayout(self)

        basics = QGroupBox("Rule")
        form = QFormLayout(basics)
        self.name = QLineEdit()
        self.name.setToolTip("Identifier used in logs and events")
        self.label = QLineEdit()
        self.label.setPlaceholderText("(the name)")
        self.label.setToolTip("Text in the overlay tip menu")
        form.addRow("Name", self.name)
        form.addRow("Label", self.label)

        tokens = QWidget()
        grid = QVBoxLayout(tokens)
        grid.setContentsMargins(0, 0, 0, 0)
        self.exact = QRadioButton("exactly")
        self.ranged = QRadioButton("from")
        self.token_mode = QButtonGroup(self)
        self.token_mode.addButton(self.exact)
        self.token_mode.addButton(self.ranged)
        self.tokens = _guard(QSpinBox())
        self.tokens.setRange(1, MAX_TOKENS)
        self.min_tokens = _guard(QSpinBox())
        self.min_tokens.setRange(1, MAX_TOKENS)
        self.max_tokens = _guard(QSpinBox())
        self.max_tokens.setRange(0, MAX_TOKENS)
        self.max_tokens.setSpecialValueText("no limit")
        exact_row = QHBoxLayout()
        exact_row.addWidget(self.exact)
        exact_row.addWidget(self.tokens, 1)
        range_row = QHBoxLayout()
        range_row.addWidget(self.ranged)
        range_row.addWidget(self.min_tokens, 1)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.max_tokens, 1)
        grid.addLayout(exact_row)
        grid.addLayout(range_row)
        form.addRow("Tokens", tokens)

        self.duration = _guard(QDoubleSpinBox())
        self.duration.setRange(0.01, 86400)
        self.duration.setDecimals(2)
        self.duration.setSuffix(" s")
        self.random_duration = QCheckBox("random up to")
        self.duration_max = _guard(QDoubleSpinBox())
        self.duration_max.setRange(0.01, 86400)
        self.duration_max.setDecimals(2)
        self.duration_max.setSuffix(" s")
        self.per_token = _guard(QDoubleSpinBox())
        self.per_token.setRange(0, 3600)
        self.per_token.setDecimals(3)
        self.per_token.setSingleStep(0.05)
        self.per_token.setSuffix(" s per token")
        time_row = QHBoxLayout()
        time_row.addWidget(self.duration, 1)
        time_row.addWidget(self.random_duration)
        time_row.addWidget(self.duration_max, 1)
        time_box = QWidget()
        time_layout = QVBoxLayout(time_box)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.addLayout(time_row)
        time_layout.addWidget(self.per_token)
        self.time_hint = _plain()
        self.time_hint.setStyleSheet("color: gray; font-size: 11px;")
        time_layout.addWidget(self.time_hint)
        form.addRow("Time", time_box)
        self.show_in_menu = QCheckBox("Show in the tip menu")
        form.addRow(self.show_in_menu)
        layout.addWidget(basics)

        output = QGroupBox("Output")
        out = QVBoxLayout(output)
        mode_row = QHBoxLayout()
        self.same = QRadioButton("Same on all channels")
        self.per_channel = QRadioButton("Per channel")
        self.output_mode = QButtonGroup(self)
        self.output_mode.addButton(self.same)
        self.output_mode.addButton(self.per_channel)
        mode_row.addWidget(self.same)
        mode_row.addWidget(self.per_channel)
        mode_row.addStretch(1)
        out.addLayout(mode_row)
        self.main_title = QLabel()
        self.main_title.setStyleSheet("color: gray;")
        out.addWidget(self.main_title)
        self.main = OutputEditor(patterns)
        out.addWidget(self.main)
        self.channel_row = QWidget()
        row = QHBoxLayout(self.channel_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Channels"))
        self.channel_checks = {}
        for ch in self._channels:
            box = QCheckBox(ch)
            box.toggled.connect(self._on_edit)
            self.channel_checks[ch] = box
            row.addWidget(box)
        row.addStretch(1)
        out.addWidget(self.channel_row)
        self.tabs = QTabWidget()
        self.channel_tabs = {}
        for ch in self._channels:
            tab = ChannelTab(ch, patterns)
            tab.changed.connect(self._on_edit)
            self.channel_tabs[ch] = tab
            self.tabs.addTab(tab, ch)
        out.addWidget(self.tabs)
        out.addWidget(QLabel("Preview (1.0 = the safety maximum)"))
        self.preview = PreviewWidget()
        out.addWidget(self.preview)
        layout.addWidget(output)
        layout.addStretch(1)

        for widget in (self.name, self.label):
            widget.textChanged.connect(self._on_edit)
        for button in (self.exact, self.ranged, self.same):
            button.toggled.connect(self._on_edit)
        self.per_channel.toggled.connect(self._on_mode)
        for spin in (self.tokens, self.min_tokens, self.max_tokens):
            spin.valueChanged.connect(self._on_edit)
        for spin in (self.duration, self.duration_max, self.per_token):
            spin.valueChanged.connect(self._on_edit)
        self.random_duration.toggled.connect(self._on_edit)
        self.show_in_menu.toggled.connect(self._on_edit)
        self.main.changed.connect(self._on_edit)

    # -- load / save -------------------------------------------------------------

    def load(self, rule: dict[str, Any]) -> None:
        self._loading = True
        try:
            self.name.setText(str(rule.get("name", "")))
            self.label.setText(str(rule.get("label") or ""))
            low = rule.get("tokens", rule.get("min_tokens"))
            high = rule.get("tokens", rule.get("max_tokens"))
            exact = "tokens" in rule or (low is not None and low == high)
            self.exact.setChecked(exact)
            self.ranged.setChecked(not exact)
            self.tokens.setValue(int(low or 1))
            self.min_tokens.setValue(int(low or 1))
            self.max_tokens.setValue(int(high or 0) if not exact else 0)
            self.duration.setValue(float(rule.get("duration") or 5))
            self.random_duration.setChecked(rule.get("duration_max") is not None)
            self.duration_max.setValue(float(rule.get("duration_max") or rule.get("duration") or 5))
            self.per_token.setValue(float(rule.get("duration_per_token") or 0))
            self.show_in_menu.setChecked(rule.get("show_in_menu", True) is not False)
            channels = rule.get("channels")
            per_channel = isinstance(channels, dict)
            self.per_channel.setChecked(per_channel)
            self.same.setChecked(not per_channel)
            listed = channels if isinstance(channels, list) else None
            for ch, box in self.channel_checks.items():
                box.setChecked(listed is None or ch in listed)
            known = set(self._channels)
            if isinstance(channels, dict):
                self._extra_channels = {k: v for k, v in channels.items() if k not in known}
            elif listed is not None:
                self._extra_channels = [c for c in listed if c not in known]
            else:
                self._extra_channels = None
            self.main.load(
                rule.get("pattern", "constant"), rule.get("params", {}), rule.get("intensity")
            )
            for ch, tab in self.channel_tabs.items():
                tab.load(channels.get(ch) if per_channel else None, rule)
        finally:
            self._loading = False
        self._update_mode()
        self._update_hints()

    def to_dict(self) -> dict[str, Any]:
        rule: dict[str, Any] = {"name": self.name.text().strip()}
        if self.label.text().strip():
            rule["label"] = self.label.text().strip()
        if self.exact.isChecked():
            rule["tokens"] = self.tokens.value()
        else:
            rule["min_tokens"] = self.min_tokens.value()
            if self.max_tokens.value():
                rule["max_tokens"] = self.max_tokens.value()
        pattern, params, intensity = self.main.value()
        rule["pattern"] = pattern
        if params:
            rule["params"] = params
        if intensity is not None:
            rule["intensity"] = intensity
        rule["duration"] = round(self.duration.value(), 2)
        if self.random_duration.isChecked():
            rule["duration_max"] = round(self.duration_max.value(), 2)
        if self.per_token.value():
            rule["duration_per_token"] = round(self.per_token.value(), 3)
        if not self.show_in_menu.isChecked():
            rule["show_in_menu"] = False
        if self.per_channel.isChecked():
            specs = {ch: tab.spec() for ch, tab in self.channel_tabs.items()}
            channels = {ch: spec for ch, spec in specs.items() if spec is not None}
            if isinstance(self._extra_channels, dict):
                channels |= self._extra_channels
            rule["channels"] = channels
        else:
            checked = [ch for ch, box in self.channel_checks.items() if box.isChecked()]
            extra = self._extra_channels if isinstance(self._extra_channels, list) else []
            if len(checked) != len(self._channels) or extra:
                rule["channels"] = checked + extra
        return rule

    # -- reactions -----------------------------------------------------------------

    def _on_mode(self) -> None:
        if self._loading:
            return
        if self.per_channel.isChecked():
            # Start each channel from the rule's settings.
            rule = self.to_dict()
            rule.pop("channels", None)
            for ch, tab in self.channel_tabs.items():
                tab.load({} if self.channel_checks[ch].isChecked() else None, rule)
        else:
            specs = [tab.spec() for tab in self.channel_tabs.values()]
            if any(spec for spec in specs):
                answer = QMessageBox.question(
                    self,
                    "Same on all channels",
                    "The channels have their own settings. Use the rule's pattern and "
                    "intensity for all channels instead?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._loading = True
                    self.per_channel.setChecked(True)
                    self._loading = False
                    return
            for ch, box in self.channel_checks.items():
                box.setChecked(self.channel_tabs[ch].active.isChecked())
        self._update_mode()
        self._on_edit()

    def _update_mode(self) -> None:
        per = self.per_channel.isChecked()
        self.tabs.setVisible(per)
        self.channel_row.setVisible(not per)
        self.main.intensity.set_optional(per)
        self.main_title.setText(
            "Rule defaults (channels without their own settings use these)" if per else ""
        )
        self.main_title.setVisible(per)
        self.max_tokens.setEnabled(self.ranged.isChecked())
        self.min_tokens.setEnabled(self.ranged.isChecked())
        self.tokens.setEnabled(self.exact.isChecked())
        self.duration_max.setEnabled(self.random_duration.isChecked())

    def _on_edit(self) -> None:
        if self._loading:
            return
        self._update_mode()
        self._update_hints()
        self.changed.emit()

    def _update_hints(self) -> None:
        cap = float(self._limits["max_action_seconds"])
        rule = self.to_dict()
        per = self.per_channel.isChecked()
        _, _, intensity = self.main.value()
        if per:
            inherit = [
                ch
                for ch, tab in self.channel_tabs.items()
                if tab.active.isChecked() and not tab.own_intensity.isChecked()
            ]
        else:
            inherit = [ch for ch, box in self.channel_checks.items() if box.isChecked()]
        self.main.hint.setText(
            rules_model.intensity_hint(intensity, inherit, self._limits)
            if intensity is not None
            else ""
        )
        for ch, tab in self.channel_tabs.items():
            own = tab.output.intensity.value()
            tab.output.hint.setText(
                rules_model.intensity_hint(own, [ch], self._limits)
                if tab.own_intensity.isChecked() and own is not None
                else ""
            )
        base = rule["duration"]
        longest = rule.get("duration_max", base)
        per_token = rule.get("duration_per_token", 0)
        hint = f"Plays {rules_model.time_text(rule)}"
        if longest + per_token * (rule.get("tokens") or rule.get("min_tokens") or 1) > cap:
            hint += f"; cut to {cap:g} s (safety.max_action_seconds)"
        self.time_hint.setText(hint)
        self._update_preview(rule)

    def _update_preview(self, data: dict[str, Any]) -> None:
        try:
            rule = Rule.model_validate(data)
            outputs = rule.outputs(self._channels)
        except Exception:
            self.preview.show_message("Fix the errors to see a preview")
            return
        series = {}
        for output in outputs:
            curve = rules_model.preview(
                output.pattern, output.params, output.intensity, rule.duration
            )
            if curve is None:
                self.preview.show_message(f"No preview for {output.pattern!r}")
                return
            series[output.channel] = curve
        self.preview.show_series(series, rule.duration)

    def focus_field(self, field: str) -> None:
        parts = field.split(".")
        head = parts[0] if parts else ""
        if head == "channels" and len(parts) > 1 and parts[1] in self.channel_tabs:
            self.tabs.setCurrentWidget(self.channel_tabs[parts[1]])
            target: QWidget = self.channel_tabs[parts[1]].output.pattern
        else:
            target = {
                "name": self.name,
                "label": self.label,
                "tokens": self.tokens if self.exact.isChecked() else self.min_tokens,
                "min_tokens": self.min_tokens,
                "max_tokens": self.max_tokens,
                "duration": self.duration,
                "duration_max": self.duration_max,
                "duration_per_token": self.per_token,
                "pattern": self.main.pattern,
                "params": self.main.params or self.main.pattern,
                "intensity": self.main.intensity.spin,
                "channels": self.channel_row if self.same.isChecked() else self.tabs,
            }.get(head, self.name)
        target.setFocus()


# -- the window ------------------------------------------------------------------------


class RulesWindow(QWidget):
    """Top-level editor window; not modal, so STOP in the main window stays usable."""

    saved = Signal(int)  # new revision

    def __init__(self, client: ControlClient, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.client = client
        self.setWindowTitle("Rules – estim-camming")
        self.resize(980, 760)
        self._info: dict[str, Any] | None = None
        self._revision = 0
        self._draft: list[dict[str, Any]] = []
        self._keys: list[int] = []
        self._new_keys: set[int] = set()
        self._counter = itertools.count(1)
        self._current: int | None = None
        self._dirty = False
        self._busy = False
        self._version = 0  # of the draft, to match check replies
        self._problems: list[dict[str, Any]] = []
        self._armed = False
        self._ignored_revision: int | None = None
        self._close_after_save = False
        self._message: tuple[str, str | None] = ("", None)
        self.form: RuleForm | None = None

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_toolbar())
        self.banner = QWidget()
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(8, 4, 8, 4)
        self.banner_text = _plain()
        self.banner_reload = QPushButton("Reload")
        self.banner_keep = QPushButton("Keep editing")
        self.banner_overwrite = QPushButton("Overwrite")
        banner.addWidget(self.banner_text, 1)
        for button in (self.banner_reload, self.banner_overwrite, self.banner_keep):
            banner.addWidget(button)
        self.banner.setStyleSheet(f"background: {ORANGE}; color: white;")
        self.banner.setVisible(False)
        self.banner_reload.clicked.connect(self.load)
        self.banner_keep.clicked.connect(self._keep_editing)
        self.banner_overwrite.clicked.connect(self._overwrite)
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["#", "Tokens", "Label", "Pattern", "Time"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._sync_order))
        self.tree.model().rowsInserted.connect(lambda *_: QTimer.singleShot(0, self._sync_order))
        splitter.addWidget(self.tree)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        splitter.addWidget(self.form_scroll)
        splitter.setSizes([440, 600])
        layout.addWidget(splitter, 1)

        self.problems = QListWidget()
        self.problems.setMaximumHeight(110)
        self.problems.itemActivated.connect(self._on_problem)
        self.problems.itemClicked.connect(self._on_problem)
        layout.addWidget(self.problems)

        bottom = QHBoxLayout()
        self.status = _plain("Loading…")
        self.revert_button = QPushButton("Revert")
        self.revert_button.setToolTip("Discard your changes and load the rules from the engine")
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip("Apply the rules now (next tips) and write config.toml")
        self.revert_button.clicked.connect(self._revert)
        self.save_button.clicked.connect(self.save)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.revert_button)
        bottom.addWidget(self.save_button)
        layout.addLayout(bottom)

        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save)
        self.help_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), self, self.show_help)
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._move(-1))
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._move(1))

        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self._remote_check)
        client.stateChanged.connect(self._on_state)
        self._render_status()

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        buttons = (
            ("+ Add", "Add a rule after the selected one", self._add),
            ("Duplicate", "Copy the selected rule", self._duplicate),
            ("Delete", "Delete the selected rule", self._delete),
            ("▲", "Move up: checked earlier (Ctrl+Up)", lambda: self._move(-1)),
            ("▼", "Move down: checked later (Ctrl+Down)", lambda: self._move(1)),
        )
        self.toolbar_buttons = []
        for text, tip, slot in buttons:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.toolbar_buttons.append(button)
        self.help_button = QPushButton("Help")
        self.help_button.setToolTip("Open this part of the user guide in your browser (F1)")
        self.help_button.clicked.connect(self.show_help)
        bar.addWidget(self.help_button)
        bar.addStretch(1)
        bar.addWidget(QLabel("Try:"))
        self.try_tokens = _guard(QSpinBox())
        self.try_tokens.setRange(1, MAX_TOKENS)
        self.try_tokens.setValue(10)
        self.try_tokens.setSuffix(" tokens")
        self.try_tokens.valueChanged.connect(self._update_try)
        self.try_result = _plain()
        self.try_result.setMinimumWidth(240)
        bar.addWidget(self.try_tokens)
        bar.addWidget(self.try_result)
        return bar

    def show_help(self) -> None:
        if open_help(model.HELP_RULES) is None:
            self._render_status(f"Could not open a browser. The guide: {model.DOCS_URL}", RED)

    # -- loading -----------------------------------------------------------------

    def load(self) -> None:
        """(Re)load the rules from the engine, discarding the draft."""
        self._busy = True
        self.banner.setVisible(False)
        self._render_status("Loading…")
        self.client.request("GET", "/api/rules", None, self._on_loaded)

    def _on_loaded(self, status: int, data: Any, error: str) -> None:
        self._busy = False
        if status != 200 or not isinstance(data, dict):
            message = "invalid control token" if status == 401 else error or f"HTTP {status}"
            self._render_status(f"Cannot load the rules: {message}", RED)
            return
        self._info = data
        self._revision = data["revision"]
        self._ignored_revision = None
        self._message = ("", None)
        # A new form each time: channels and patterns come with the rules.
        self.form = RuleForm(data)
        self.form.changed.connect(self._on_form_changed)
        self.form_scroll.setWidget(self.form)
        keep = self._current or 0
        self._draft = copy.deepcopy(data["rules"])
        self._keys = [next(self._counter) for _ in self._draft]
        self._new_keys = set()
        self._dirty = False
        self._rebuild_tree(min(keep, len(self._draft) - 1))
        self._after_change(dirty=False)

    # -- the list ------------------------------------------------------------------

    def _rebuild_tree(self, select: int | None = None) -> None:
        # Only the widget's own signals are blocked: the view must see the model
        # changes. The deferred _sync_order finds the order unchanged.
        self.tree.blockSignals(True)
        self.tree.clear()
        for key in self._keys:
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self._refresh_rows()
        if select is not None and 0 <= select < len(self._draft):
            self.tree.setCurrentItem(self.tree.topLevelItem(select))
            self._show_rule(select)
        elif not self._draft and self.form is not None:
            self.form.setEnabled(False)
        for col in range(5):
            self.tree.resizeColumnToContents(col)

    def _refresh_rows(self) -> None:
        marks: dict[int, tuple[str, list[str]]] = {}
        order = {"info": 0, "warning": 1, "error": 2}
        for problem in self._problems:
            index = problem.get("rule")
            if index is None:
                continue
            level, messages = marks.get(index, ("info", []))
            if order[problem["level"]] > order[level]:
                level = problem["level"]
            marks[index] = (level, [*messages, problem["message"]])
        for i, rule in enumerate(self._draft):
            item = self.tree.topLevelItem(i)
            if item is None:
                continue
            tokens, label, pattern, time_ = rules_model.summary(rule)
            level, messages = marks.get(i, ("info", []))
            icon = ICONS[level] + " " if level != "info" else ""
            item.setText(0, f"{icon}{i + 1}")
            for col, text in enumerate((tokens, label, pattern, time_), start=1):
                item.setText(col, text)
                item.setToolTip(col, "\n".join(messages))
            item.setToolTip(0, "\n".join(messages))
            font = item.font(2)
            font.setItalic(rule.get("show_in_menu") is False)
            item.setFont(2, font)

    def _sync_order(self) -> None:
        """After drag and drop: take the order from the list."""
        keys = [
            self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.tree.topLevelItemCount())
        ]
        if keys == self._keys or sorted(keys) != sorted(self._keys):
            return
        by_key = dict(zip(self._keys, self._draft, strict=True))
        self._keys = keys
        self._draft = [by_key[k] for k in keys]
        current = self.tree.currentItem()
        self._current = self.tree.indexOfTopLevelItem(current) if current else None
        self._after_change()

    def _on_select(self, current: QTreeWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        self._show_rule(self.tree.indexOfTopLevelItem(current))

    def _show_rule(self, index: int) -> None:
        if self.form is None or not 0 <= index < len(self._draft):
            return
        self._current = index
        self.form.setEnabled(True)
        self.form.load(self._draft[index])

    # -- editing ---------------------------------------------------------------------

    def _on_form_changed(self) -> None:
        if self.form is None or self._current is None:
            return
        self._draft[self._current] = self.form.to_dict()
        self._after_change()

    def _after_change(self, dirty: bool = True) -> None:
        if dirty:
            self._dirty = True
            self._message = ("", None)
        self._version += 1
        self._local_check()
        self._refresh_rows()
        self._update_try()
        self._render_status()
        if self._info is not None:
            self._check_timer.start()

    def _unique_name(self, base: str) -> str:
        names = {r.get("name") for r in self._draft}
        if base not in names:
            return base
        return next(f"{base}-{n}" for n in itertools.count(2) if f"{base}-{n}" not in names)

    def _free_amount(self) -> int:
        """The smallest amount no valid draft rule takes (1 if all are taken)."""
        rules = []
        for data in self._draft:
            try:
                rules.append(Rule.model_validate(data))
            except ValueError:
                continue
        free = uncovered_amounts(rules)
        return free[0][0] if free else 1

    def _insert(self, rule: dict[str, Any]) -> None:
        index = (self._current + 1) if self._current is not None else len(self._draft)
        key = next(self._counter)
        self._draft.insert(index, rule)
        self._keys.insert(index, key)
        self._new_keys.add(key)
        self._after_change()
        self._rebuild_tree(index)
        if self.form is not None:
            self.form.name.setFocus()

    def _add(self) -> None:
        if self._info is None:
            return
        self._insert(
            {
                "name": self._unique_name("rule"),
                "label": "New rule",
                "tokens": self._free_amount(),
                "pattern": "constant",
                "intensity": 0.3,
                "duration": 5,
            }
        )

    def _duplicate(self) -> None:
        if self._current is None or not self._draft:
            return
        rule = copy.deepcopy(self._draft[self._current])
        rule["name"] = self._unique_name(f"{rule.get('name', 'rule')}-copy")
        self._insert(rule)

    def _delete(self) -> None:
        if self._current is None or not self._draft:
            return
        index = self._current
        if self._keys[index] not in self._new_keys:
            label = rules_model.summary(self._draft[index])[1]
            answer = QMessageBox.question(self, "Delete rule", f"Delete rule {index + 1} {label}?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        del self._draft[index]
        del self._keys[index]
        self._current = min(index, len(self._draft) - 1) if self._draft else None
        self._after_change()
        self._rebuild_tree(self._current)

    def _move(self, step: int) -> None:
        if self._current is None:
            return
        i, j = self._current, self._current + step
        if not 0 <= j < len(self._draft):
            return
        self._draft[i], self._draft[j] = self._draft[j], self._draft[i]
        self._keys[i], self._keys[j] = self._keys[j], self._keys[i]
        self._current = j
        self._after_change()
        self._rebuild_tree(j)

    # -- checks ------------------------------------------------------------------------

    def _local_check(self) -> None:
        if self._info is None:
            return
        result = check_rules(
            self._draft, self._info["channels"], self._info["limits"]["max_action_seconds"]
        )
        self._set_problems([p.to_dict() for p in result.problems])

    def _remote_check(self) -> None:
        if self._info is None:
            return
        version = self._version
        self.client.request(
            "POST",
            "/api/rules/check",
            {"rules": self._draft},
            lambda status, data, error: self._on_remote_check(version, status, data),
        )

    def _on_remote_check(self, version: int, status: int, data: Any) -> None:
        if version != self._version or status != 200 or not isinstance(data, dict):
            return  # outdated, or the engine is away: the local check stands
        problems = [p | {"level": "error"} for p in data.get("errors", [])]
        problems += data.get("warnings", [])
        if problems != self._problems:
            self._set_problems(problems)
            self._refresh_rows()
            self._render_status()

    def _set_problems(self, problems: list[dict[str, Any]]) -> None:
        self._problems = problems
        self.problems.clear()
        rank = {"error": 0, "warning": 1, "info": 2}
        for problem in sorted(problems, key=lambda p: rank[p["level"]]):
            index = problem.get("rule")
            where = ""
            if index is not None and 0 <= index < len(self._draft):
                where = f'{index + 1} "{rules_model.summary(self._draft[index])[1]}": '
            field = f"{problem['field']}: " if problem.get("field") else ""
            item = QListWidgetItem(f"{ICONS[problem['level']]} {where}{field}{problem['message']}")
            item.setData(Qt.ItemDataRole.UserRole, (index, problem.get("field", "")))
            self.problems.addItem(item)
        self.problems.setVisible(bool(problems))

    def _errors(self) -> int:
        return sum(1 for p in self._problems if p["level"] == "error")

    def _on_problem(self, item: QListWidgetItem) -> None:
        index, field = item.data(Qt.ItemDataRole.UserRole)
        if index is None or not 0 <= index < len(self._draft):
            return
        self.tree.setCurrentItem(self.tree.topLevelItem(index))
        if self.form is not None:
            self.form.focus_field(field)

    def _update_try(self) -> None:
        if self._info is None:
            return
        self.try_result.setText(
            "→ "
            + rules_model.try_amount(
                self._draft, self.try_tokens.value(), self._info["limits"]["max_action_seconds"]
            )
        )

    # -- saving ------------------------------------------------------------------------

    def save(self) -> None:
        if self._info is None or self._busy:
            return
        if self._errors():
            self._render_status("Fix the errors before saving.", RED)
            return
        self._busy = True
        self._render_status("Saving…")
        self.client.request(
            "POST",
            "/api/rules",
            {"revision": self._revision, "rules": self._draft},
            self._on_saved,
        )

    def _on_saved(self, status: int, data: Any, error: str) -> None:
        self._busy = False
        data = data if isinstance(data, dict) else {}
        if status == 200:
            self._revision = data["revision"]
            self._dirty = False
            self._new_keys = set()
            self.banner.setVisible(False)
            self.saved.emit(self._revision)
            if data.get("saved"):
                self._render_status(f"Saved and applied. {data.get('detail', '')}", GREEN)
            else:
                self._render_status(
                    f"Applied, but NOT saved to the file: {data.get('detail', '')}", ORANGE
                )
            if self._close_after_save:
                self.close()
            return
        self._close_after_save = False
        if status == 409:
            self._show_banner(
                f"The rules were changed elsewhere (revision {data.get('revision')}). Reload "
                "them, or overwrite them with yours.",
                overwrite=data.get("revision"),
            )
            self._render_status("Not saved: conflict.", RED)
        elif status == 400 and "errors" in data:
            self._set_problems(
                [p | {"level": "error"} for p in data["errors"]] + data.get("warnings", [])
            )
            self._refresh_rows()
            self._render_status("Not saved: the engine found errors.", RED)
        else:
            message = "invalid control token" if status == 401 else error or f"HTTP {status}"
            self._render_status(f"Not saved: {message}", RED)

    def _revert(self) -> None:
        if self._dirty:
            answer = QMessageBox.question(self, "Revert", "Discard your changes?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.load()

    def _overwrite(self) -> None:
        revision = self.banner_overwrite.property("revision")
        if revision is None:
            return
        self._revision = int(revision)
        self.banner.setVisible(False)
        self.save()

    def _keep_editing(self) -> None:
        self._ignored_revision = self.banner_keep.property("revision")
        self.banner.setVisible(False)

    def _show_banner(self, text: str, overwrite: int | None = None, keep: int | None = None):
        self.banner_text.setText(text)
        self.banner_overwrite.setVisible(overwrite is not None)
        self.banner_overwrite.setProperty("revision", overwrite)
        self.banner_keep.setVisible(keep is not None)
        self.banner_keep.setProperty("revision", keep)
        self.banner.setVisible(True)

    # -- engine state ------------------------------------------------------------------

    def _on_state(self, state: dict[str, Any]) -> None:
        self._armed = bool(state.get("armed"))
        revision = state.get("rules_revision")
        if self._info is not None and not self._busy and revision is not None:
            if revision != self._revision and revision != self._ignored_revision:
                if not self._dirty:
                    self.load()
                elif not self.banner.isVisible():
                    self._show_banner(
                        "The rules were changed elsewhere while you were editing.",
                        keep=revision,
                    )
        self._render_status()

    def _render_status(self, message: str | None = None, color: str | None = None) -> None:
        """Status line: unsaved/armed/errors, plus the last message (cleared by edits)."""
        if message is not None:
            self._message = (message, color)
        text, msg_color = self._message
        errors = self._errors()
        parts = []
        if self._dirty:
            parts.append("● Unsaved changes")
        if self._armed:
            parts.append("ARMED: saved rules apply to the next tip")
        if errors:
            parts.append(f"{errors} error(s)")
        if text:
            parts.append(text)
        self.status.setText(" · ".join(parts))
        self.status.setStyleSheet(f"color: {msg_color};" if text and msg_color else "")
        ready = self._info is not None and not self._busy
        self.save_button.setEnabled(ready and errors == 0 and bool(self._draft))
        self.revert_button.setEnabled(ready)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._dirty and not self._close_after_save:
            answer = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save your changes to the rules?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Save:
                self._close_after_save = True
                self.save()
                event.ignore()
                return
            if answer != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
            self._dirty = False
        self._close_after_save = False
        event.accept()
