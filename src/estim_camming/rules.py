"""Tip rules: map a tip amount to an :class:`~estim_camming.actions.Action`."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from estim_camming.actions import Action, ChannelOutput
from estim_camming.events import TipEvent
from estim_camming.plugins import PATTERNS, PluginError


class ChannelSpec(BaseModel):
    """Per-channel settings of a rule. Unset fields come from the rule itself."""

    model_config = ConfigDict(extra="forbid")

    intensity: float | None = Field(None, ge=0, le=1)
    pattern: str | None = None
    params: dict[str, Any] | None = Field(
        None, description="Pattern options; default: the rule's params if the pattern is the same."
    )


class Rule(BaseModel):
    """One entry of the tip menu. Rules are matched in order; first match wins."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str | None = Field(None, description="Text shown in the overlay tip menu.")
    tokens: int | None = Field(None, ge=1, description="Shorthand for min_tokens = max_tokens.")
    min_tokens: int | None = Field(None, ge=1)
    max_tokens: int | None = Field(None, ge=1)
    pattern: str = "constant"
    params: dict[str, Any] = Field(default_factory=dict)
    intensity: float | None = Field(
        None, ge=0, le=1, description="Relative intensity; per channel via 'channels'."
    )
    duration: float = Field(gt=0, description="Seconds.")
    duration_max: float | None = Field(
        None, gt=0, description="If set, the duration is random between duration and this."
    )
    duration_per_token: float = Field(0.0, ge=0, description="Extra seconds per token tipped.")
    channels: list[str] | dict[str, ChannelSpec] | None = Field(
        None,
        description="Channels to drive (default all): a list, or a table with "
        "per-channel intensity/pattern/params.",
    )
    show_in_menu: bool = True

    @model_validator(mode="after")
    def _normalise_tokens(self):
        if self.tokens is not None:
            if self.min_tokens is not None or self.max_tokens is not None:
                raise ValueError("use either 'tokens' or 'min_tokens'/'max_tokens', not both")
            self.min_tokens = self.max_tokens = self.tokens
        if self.min_tokens is None:
            raise ValueError("one of 'tokens' or 'min_tokens' is required")
        if self.max_tokens is not None and self.max_tokens < self.min_tokens:
            raise ValueError("max_tokens must be >= min_tokens")
        if self.duration_max is not None and self.duration_max < self.duration:
            raise ValueError("duration_max must be >= duration")
        if isinstance(self.channels, list) and len(set(self.channels)) != len(self.channels):
            raise ValueError("channels contains duplicates")
        if self.channels is not None and len(self.channels) == 0:
            raise ValueError("channels must not be empty")
        if self.intensity is None:
            specs = self.channels.values() if isinstance(self.channels, dict) else ()
            if not isinstance(self.channels, dict) or any(s.intensity is None for s in specs):
                raise ValueError("set 'intensity' on the rule or on every channel")
        return self

    def outputs(self, device_channels: Sequence[str]) -> tuple[ChannelOutput, ...]:
        """What each channel does when this rule fires."""
        if isinstance(self.channels, dict):
            specs = self.channels
        else:
            specs = {ch: ChannelSpec() for ch in (self.channels or device_channels)}
        result = []
        for channel, spec in specs.items():
            pattern = spec.pattern or self.pattern
            if spec.params is not None:
                params = spec.params
            else:
                # A channel with its own pattern doesn't inherit the rule's params,
                # which belong to a different pattern.
                params = self.params if pattern == self.pattern else {}
            intensity = spec.intensity if spec.intensity is not None else self.intensity
            assert intensity is not None  # guaranteed by the validator
            result.append(
                ChannelOutput(
                    channel=channel, pattern=pattern, intensity=intensity, params=dict(params)
                )
            )
        return tuple(result)

    @property
    def display_label(self) -> str:
        return self.label or self.name

    def matches(self, tokens: int) -> bool:
        assert self.min_tokens is not None
        return tokens >= self.min_tokens and (self.max_tokens is None or tokens <= self.max_tokens)


class RuleEngine:
    def __init__(
        self,
        rules: Sequence[Rule],
        device_channels: Sequence[str],
        max_action_seconds: float,
        rng: random.Random | None = None,
    ) -> None:
        self.rules = list(rules)
        self._rng = rng or random.Random()
        self.device_channels = tuple(device_channels)
        self.max_action_seconds = max_action_seconds
        self._validate()

    def _validate(self) -> None:
        for rule in self.rules:
            problems = wiring_problems(rule, self.device_channels)
            if problems:
                raise PluginError(f"rule '{rule.name}': {problems[0][1]}")

    def match(self, tokens: int) -> Rule | None:
        return next((r for r in self.rules if r.matches(tokens)), None)

    def action_for(self, tip: TipEvent) -> Action | None:
        rule = self.match(tip.tokens)
        if rule is None:
            return None
        base = rule.duration
        if rule.duration_max is not None:
            base = self._rng.uniform(rule.duration, rule.duration_max)
        duration = base + rule.duration_per_token * tip.tokens
        return Action(
            label=rule.display_label,
            duration=min(duration, self.max_action_seconds),
            outputs=rule.outputs(self.device_channels),
            rule=rule.name,
            tip=tip,
        )

    def menu(self) -> list[dict[str, Any]]:
        """Tip menu entries for the overlay."""
        return [
            {
                "label": r.display_label,
                "min_tokens": r.min_tokens,
                "max_tokens": r.max_tokens,
                "duration": r.duration,
                "duration_max": r.duration_max,
            }
            for r in self.rules
            if r.show_in_menu
        ]


def wiring_problems(rule: Rule, device_channels: Sequence[str]) -> list[tuple[str, str]]:
    """(field, message) for channels the device lacks, unknown patterns and pattern
    params that don't validate. Empty if the rule can run on this device."""
    unknown = set(rule.channels or ()) - set(device_channels)
    if unknown:
        return [
            (
                "channels",
                f"unknown channel(s) {sorted(unknown)}; device has {list(device_channels)}",
            )
        ]
    specs = rule.channels if isinstance(rule.channels, dict) else None
    problems = []
    for output in rule.outputs(device_channels):
        spec = specs[output.channel] if specs else None
        where = f"channel {output.channel}: " if specs else ""
        if output.pattern not in PATTERNS.names():
            field = f"channels.{output.channel}.pattern" if spec and spec.pattern else "pattern"
            problems.append((field, f"{where}unknown pattern {output.pattern!r}"))
        else:
            own_params = spec is not None and spec.params is not None
            prefix = f"channels.{output.channel}.params" if own_params else "params"
            try:
                PATTERNS.get(output.pattern).Options.model_validate(output.params)
            except ValidationError as exc:
                for error in exc.errors():
                    field = ".".join([prefix, *(str(part) for part in error["loc"])])
                    message = error["msg"].removeprefix("Value error, ")
                    problems.append((field, f"{where}{output.pattern} {field}: {message}"))
        if problems and specs is None:
            break  # every channel shares the rule's pattern and params
    return problems
