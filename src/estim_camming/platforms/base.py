"""Platform (streaming site) plugin interface."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator

from estim_camming.events import TipEvent
from estim_camming.plugins import Plugin


class PlatformError(Exception):
    """Raised by a platform. ``fatal=True`` means retrying will not help."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


class Platform(Plugin):
    """A source of tips.

    ``stream`` is an async generator that connects, yields tips as they
    arrive and runs until cancelled. On connection loss it should raise; the
    application reconnects with back-off (see ``docs/design/platforms.md``).
    """

    @abstractmethod
    def stream(self) -> AsyncIterator[TipEvent]:
        """Yield :class:`TipEvent` objects forever."""

    def describe(self) -> str:
        """Human-readable identity for logs/overlay. Must not reveal secrets."""
        return self.name
