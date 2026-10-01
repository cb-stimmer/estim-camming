"""In-process asynchronous publish/subscribe event bus."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from estim_camming.events import Event

log = logging.getLogger(__name__)


class Subscription:
    """A queue of events matching the requested types.

    Iterate with ``async for``. Bounded subscriptions drop the *oldest* event
    when full, so a slow consumer (e.g. an overlay) never blocks publishers.
    """

    def __init__(self, bus: EventBus, types: tuple[type[Event], ...], maxsize: int) -> None:
        self._bus = bus
        self._types = types
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self.dropped = 0

    def accepts(self, event: Event) -> bool:
        return isinstance(event, self._types)

    def offer(self, event: Event) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        return await self._queue.get()

    def close(self) -> None:
        self._bus._subscriptions.discard(self)

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        return await self._queue.get()


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()

    def subscribe(self, *types: type[Event], maxsize: int = 0) -> Subscription:
        """Subscribe to events of the given types (all events if none given).

        ``maxsize=0`` means unbounded: use it for consumers that must not miss
        events, such as the tip dispatcher.
        """
        sub = Subscription(self, types or (Event,), maxsize)
        self._subscriptions.add(sub)
        return sub

    def publish(self, event: Event) -> None:
        """Deliver ``event`` to all matching subscribers. Never blocks."""
        log.debug("event %s", event)
        for sub in list(self._subscriptions):
            if sub.accepts(event):
                sub.offer(event)
