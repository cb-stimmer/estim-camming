"""Events published on the :class:`~estim_camming.bus.EventBus`.

Events are immutable dataclasses. ``to_dict`` produces the JSON shape sent to
overlay clients (see ``docs/design/overlay.md``).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from estim_camming.actions import Action


@dataclass(frozen=True, kw_only=True)
class Event:
    type: ClassVar[str] = "event"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type
        return data


@dataclass(frozen=True, kw_only=True)
class TipEvent(Event):
    """A tip received from a platform (or injected from the control panel)."""

    type: ClassVar[str] = "tip"
    platform: str
    username: str
    tokens: int
    message: str = ""
    anonymous: bool = False
    #: Platform-specific id, used for de-duplication where available.
    event_id: str = ""


@dataclass(frozen=True, kw_only=True)
class PlatformStatus(Event):
    type: ClassVar[str] = "platform_status"
    platform: str
    connected: bool
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class ActionQueued(Event):
    type: ClassVar[str] = "action_queued"
    action: Action


@dataclass(frozen=True, kw_only=True)
class ActionRejected(Event):
    type: ClassVar[str] = "action_rejected"
    action: Action
    reason: str


@dataclass(frozen=True, kw_only=True)
class ActionStarted(Event):
    type: ClassVar[str] = "action_started"
    action: Action


@dataclass(frozen=True, kw_only=True)
class ActionFinished(Event):
    type: ClassVar[str] = "action_finished"
    action: Action
    interrupted: bool = False


@dataclass(frozen=True, kw_only=True)
class QueueCleared(Event):
    type: ClassVar[str] = "queue_cleared"
    count: int


@dataclass(frozen=True, kw_only=True)
class LevelsChanged(Event):
    """Actual (post-safety) output levels, 0..1 of full device scale."""

    type: ClassVar[str] = "levels"
    levels: dict[str, float]


@dataclass(frozen=True, kw_only=True)
class RulesChanged(Event):
    """The rules were replaced (rules editor). New tips use the new rules."""

    type: ClassVar[str] = "rules_changed"
    revision: int
    saved: bool


@dataclass(frozen=True, kw_only=True)
class SafetyStateChanged(Event):
    type: ClassVar[str] = "safety"
    armed: bool
    scale: float
    reason: str = ""
