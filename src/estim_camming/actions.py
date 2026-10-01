"""The :class:`Action` value object: one scheduled stimulation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from estim_camming.events import TipEvent


@dataclass(frozen=True, kw_only=True)
class ChannelOutput:
    """What one channel does during an action.

    ``intensity`` is relative (0..1); the safety guard scales it by the
    configured channel maximum before it reaches the device.
    """

    channel: str
    pattern: str
    intensity: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Action:
    """Per-channel outputs played together for a duration. Channels without an
    output stay at 0 while the action plays."""

    label: str
    duration: float
    outputs: tuple[ChannelOutput, ...]
    rule: str = ""
    tip: TipEvent | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(o.channel for o in self.outputs)
