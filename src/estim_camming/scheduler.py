"""Plays queued actions one at a time through the safety guard."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from estim_camming.actions import Action
from estim_camming.bus import EventBus
from estim_camming.events import (
    ActionFinished,
    ActionQueued,
    ActionRejected,
    ActionStarted,
    QueueCleared,
)
from estim_camming.patterns import Pattern
from estim_camming.plugins import PATTERNS
from estim_camming.safety import SafetyGuard

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        guard: SafetyGuard,
        bus: EventBus,
        channels: tuple[str, ...],
        tick_hz: float = 20.0,
        max_queue: int = 50,
    ) -> None:
        self._guard = guard
        self._bus = bus
        self._channels = channels
        self._tick = 1.0 / tick_hz
        self._max_queue = max_queue
        self._queue: deque[Action] = deque()
        self._wakeup = asyncio.Event()
        self._current: Action | None = None
        self._current_started: float | None = None
        self._skip = False

    @property
    def queue(self) -> tuple[Action, ...]:
        return tuple(self._queue)

    @property
    def current(self) -> Action | None:
        return self._current

    def elapsed(self) -> float:
        if self._current_started is None:
            return 0.0
        return asyncio.get_running_loop().time() - self._current_started

    def enqueue(self, action: Action) -> bool:
        if not self._guard.armed:
            reason = "output disarmed"
        elif len(self._queue) >= self._max_queue:
            reason = "queue full"
        else:
            self._queue.append(action)
            self._wakeup.set()
            self._bus.publish(ActionQueued(action=action))
            return True
        log.info("rejected '%s': %s", action.label, reason)
        self._bus.publish(ActionRejected(action=action, reason=reason))
        return False

    def clear(self) -> None:
        count = len(self._queue)
        self._queue.clear()
        self._bus.publish(QueueCleared(count=count))

    def skip_current(self) -> None:
        self._skip = True

    async def run(self) -> None:
        while True:
            await self._guard.wait_armed()
            if not self._queue:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            action = self._queue.popleft()
            self._skip = False
            await self._play(action)

    async def _play(self, action: Action) -> None:
        patterns: list[tuple[str, Pattern, float]] = [
            (o.channel, PATTERNS.create(o.pattern, o.params), o.intensity) for o in action.outputs
        ]
        loop = asyncio.get_running_loop()
        zeros = {ch: 0.0 for ch in self._channels}
        self._current, self._current_started = action, loop.time()
        self._bus.publish(ActionStarted(action=action))
        interrupted = False
        try:
            while (t := loop.time() - self._current_started) < action.duration:
                if self._skip or not self._guard.armed:
                    interrupted = True
                    break
                levels = {
                    ch: min(max(pattern.level(t, action.duration), 0.0), 1.0) * intensity
                    for ch, pattern, intensity in patterns
                }
                await self._guard.set_levels(zeros | levels)
                await asyncio.sleep(self._tick)
        finally:
            await self._guard.set_levels(zeros)
            self._current = self._current_started = None
            self._bus.publish(ActionFinished(action=action, interrupted=interrupted))
