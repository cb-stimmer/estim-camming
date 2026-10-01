"""Tip rules: map a tip amount to an :class:`~estim_camming.actions.Action`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    ) -> None:
        self.rules = list(rules)
        self.device_channels = tuple(device_channels)
        self.max_action_seconds = max_action_seconds
        self._validate()

    def _validate(self) -> None:
        for rule in self.rules:
            unknown = set(rule.channels or ()) - set(self.device_channels)
            if unknown:
                raise PluginError(
                    f"rule '{rule.name}': unknown channel(s) {sorted(unknown)}; "
                    f"device has {list(self.device_channels)}"
                )
            for output in rule.outputs(self.device_channels):
                try:
                    PATTERNS.create(output.pattern, output.params)
                except PluginError as exc:
                    raise PluginError(
                        f"rule '{rule.name}', channel {output.channel}: {exc}"
                    ) from exc

    def match(self, tokens: int) -> Rule | None:
        return next((r for r in self.rules if r.matches(tokens)), None)

    def action_for(self, tip: TipEvent) -> Action | None:
        rule = self.match(tip.tokens)
        if rule is None:
            return None
        duration = rule.duration + rule.duration_per_token * tip.tokens
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
            }
            for r in self.rules
            if r.show_in_menu
        ]
