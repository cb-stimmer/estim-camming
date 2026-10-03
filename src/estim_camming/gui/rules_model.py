"""Qt-free helpers for the rules editor: summaries, the try box, params forms
from JSON schemas, intensity hints and preview curves."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from estim_camming.plugins import PATTERNS, PluginError
from estim_camming.rules import Rule
from estim_camming.rules_io import format_range

PREVIEW_INSTANCES = 8  # fresh pattern instances per preview (random patterns vary)


def _seconds(value: Any) -> str:
    try:
        return f"{float(value):g} s"
    except (TypeError, ValueError):
        return "?"


def token_text(rule: Mapping[str, Any]) -> str:
    if rule.get("tokens") is not None:
        return str(rule["tokens"])
    low = rule.get("min_tokens")
    if low is None:
        return "?"
    high = rule.get("max_tokens")
    return format_range(low, math.inf if high is None else high)


def pattern_text(rule: Mapping[str, Any]) -> str:
    channels = rule.get("channels")
    if isinstance(channels, dict) and any(
        isinstance(spec, dict) and "pattern" in spec for spec in channels.values()
    ):
        return "per channel"
    return str(rule.get("pattern", "constant"))


def time_text(rule: Mapping[str, Any]) -> str:
    text = _seconds(rule.get("duration"))
    if rule.get("duration_max") is not None:
        text = f"{text[:-2]}–{_seconds(rule['duration_max'])}"
    if rule.get("duration_per_token"):
        text += f" +{float(rule['duration_per_token']):g}/tk"
    return text


def summary(rule: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Columns of the rule list: tokens, label, pattern, time."""
    label = str(rule.get("label") or rule.get("name") or "(unnamed)")
    if rule.get("show_in_menu") is False:
        label += "  (hidden)"
    return token_text(rule), label, pattern_text(rule), time_text(rule)


def try_amount(draft: Sequence[Mapping[str, Any]], tokens: int, max_seconds: float) -> str:
    """Which draft rule a tip of ``tokens`` would trigger, first match wins."""
    skipped = 0
    for number, data in enumerate(draft, start=1):
        try:
            rule = Rule.model_validate(dict(data))
        except ValidationError:
            skipped += 1
            continue
        if not rule.matches(tokens):
            continue
        low = rule.duration + rule.duration_per_token * tokens
        high = (rule.duration_max or rule.duration) + rule.duration_per_token * tokens
        low, high = min(low, max_seconds), min(high, max_seconds)
        seconds = f"{low:g} s" if low == high else f"{low:g}–{high:g} s"
        return f'rule {number} "{rule.display_label}", {seconds}'
    note = f" ({skipped} invalid rule(s) skipped)" if skipped else ""
    return "no rule: nothing happens" + note


# -- params forms -------------------------------------------------------------------


@dataclass(frozen=True)
class ParamField:
    name: str
    kind: str  # "number", "integer", "boolean", "choice" or "json"
    title: str
    default: Any = None
    description: str = ""
    optional: bool = False  # may be null: shown with a "set" checkbox
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    exclusive_maximum: bool = False
    choices: tuple[Any, ...] = ()


def param_fields(schema: Mapping[str, Any]) -> list[ParamField]:
    """Form fields for a pattern's ``Options`` JSON schema."""
    fields = []
    for name, prop in (schema.get("properties") or {}).items():
        variants = prop.get("anyOf") or [prop]
        optional = any(v.get("type") == "null" for v in variants)
        main = next((v for v in variants if v.get("type") != "null"), prop)
        kind = main.get("type")
        if "enum" in main:
            kind = "choice"
        elif kind not in ("number", "integer", "boolean"):
            kind = "json"
        minimum = main.get("minimum", main.get("exclusiveMinimum"))
        maximum = main.get("maximum", main.get("exclusiveMaximum"))
        fields.append(
            ParamField(
                name=name,
                kind=kind,
                title=prop.get("title") or name,
                default=prop.get("default"),
                description=prop.get("description", ""),
                optional=optional,
                minimum=minimum,
                maximum=maximum,
                exclusive_minimum="exclusiveMinimum" in main,
                exclusive_maximum="exclusiveMaximum" in main,
                choices=tuple(main.get("enum", ())),
            )
        )
    return fields


# -- intensity and preview ------------------------------------------------------------


def channel_max(channel: str, limits: Mapping[str, Any]) -> float:
    return float((limits.get("channel_max_level") or {}).get(channel, limits.get("max_level", 1)))


def intensity_hint(intensity: float, channels: Sequence[str], limits: Mapping[str, Any]) -> str:
    """'≈ A 30 %, B 30 % of the device range' (before the master slider)."""
    parts = [f"{ch} {intensity * channel_max(ch, limits):.0%}" for ch in channels]
    return f"≈ {', '.join(parts)} of the device range" if parts else ""


def preview(
    pattern: str, params: Mapping[str, Any], intensity: float, duration: float, samples: int = 200
) -> tuple[list[float], list[float]] | None:
    """(low, high) level envelope over ``duration`` from several fresh pattern
    instances, scaled by ``intensity``. None if the pattern can't be built here."""
    if duration <= 0 or pattern not in PATTERNS.names():
        return None
    try:
        instances = [PATTERNS.create(pattern, params) for _ in range(PREVIEW_INSTANCES)]
    except PluginError:
        return None
    low, high = [], []
    for i in range(samples):
        t = duration * i / (samples - 1)
        levels = [min(max(p.level(t, duration), 0.0), 1.0) * intensity for p in instances]
        low.append(min(levels))
        high.append(max(levels))
    return low, high
