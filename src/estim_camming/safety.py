"""Safety guard: the only component allowed to send levels to the device.

Invariants (see ``docs/design/safety.md``):

* While disarmed, every channel is driven to 0 and no actions are queued.
* Output = requested level x live scale x channel maximum, clamped to 0..1.
* Increases are rate-limited (``max_change_per_second``); decreases are instant.
* Any device error or timeout triggers an emergency stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from estim_camming.bus import EventBus
from estim_camming.devices.base import Device
from estim_camming.events import LevelsChanged, SafetyStateChanged

log = logging.getLogger(__name__)

#: Upper bound on the time step used for rate limiting, so a long idle gap
#: never permits a large jump in one update.
_MAX_DT = 0.1
_LEVEL_EPSILON = 0.005


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_armed: bool = Field(
        False, description="Arm output at startup (default: require manual arm)."
    )
    max_level: float = Field(0.5, ge=0, le=1, description="Output at rule intensity 1.0.")
    channel_max_level: dict[str, float] = Field(
        default_factory=dict, description="Per-channel override of max_level."
    )
    max_change_per_second: float = Field(
        0.5, gt=0, description="Maximum increase of output per second (fraction of full scale)."
    )
    max_action_seconds: float = Field(60.0, gt=0)
    device_timeout: float = Field(2.0, gt=0, description="Seconds before a device call is a fault.")

    @field_validator("channel_max_level")
    @classmethod
    def _check_channel_levels(cls, value: dict[str, float]) -> dict[str, float]:
        for channel, level in value.items():
            if not 0 <= level <= 1:
                raise ValueError(f"channel_max_level[{channel!r}] must be between 0 and 1")
        return value


class SafetyGuard:
    def __init__(self, device: Device, config: SafetyConfig, bus: EventBus) -> None:
        self._device = device
        self._config = config
        self._bus = bus
        self._armed = asyncio.Event()
        if config.start_armed:
            self._armed.set()
        self._scale = 1.0
        self._levels = {ch: 0.0 for ch in device.channels}
        self._last_update: float | None = None
        self._lock = asyncio.Lock()
        self._disarm_callbacks: list[Callable[[], None]] = []
        unknown = set(config.channel_max_level) - set(device.channels)
        if unknown:
            raise ValueError(f"safety.channel_max_level: unknown channel(s) {sorted(unknown)}")

    # -- state -----------------------------------------------------------

    @property
    def armed(self) -> bool:
        return self._armed.is_set()

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def levels(self) -> dict[str, float]:
        return dict(self._levels)

    def channel_max(self, channel: str) -> float:
        return self._config.channel_max_level.get(channel, self._config.max_level)

    def on_disarm(self, callback: Callable[[], None]) -> None:
        self._disarm_callbacks.append(callback)

    async def wait_armed(self) -> None:
        await self._armed.wait()

    def _publish_state(self, reason: str) -> None:
        self._bus.publish(SafetyStateChanged(armed=self.armed, scale=self._scale, reason=reason))

    # -- controls --------------------------------------------------------

    def arm(self, reason: str = "manual") -> None:
        if not self.armed:
            log.warning("output ARMED (%s)", reason)
            self._armed.set()
            self._publish_state(reason)

    async def emergency_stop(self, reason: str = "manual") -> None:
        """Disarm immediately and drive all channels to zero."""
        was_armed = self.armed
        self._armed.clear()  # synchronous: the scheduler sees this on its next tick
        if was_armed:
            log.warning("EMERGENCY STOP (%s)", reason)
        for callback in self._disarm_callbacks:
            callback()
        async with self._lock:
            try:
                await asyncio.wait_for(self._device.stop(), self._config.device_timeout)
            except Exception:
                log.critical("device.stop() failed - check the device physically!", exc_info=True)
            self._set_known_levels({ch: 0.0 for ch in self._device.channels})
        self._publish_state(reason)

    def set_scale(self, scale: float) -> None:
        """Live master scale (0..1) applied on top of the configured maxima."""
        self._scale = min(max(scale, 0.0), 1.0)
        self._publish_state(f"scale {self._scale:.0%}")

    # -- output ----------------------------------------------------------

    def compute(self, requested: Mapping[str, float], dt: float) -> dict[str, float]:
        """Pure function of the safety rules; exposed for testing."""
        out: dict[str, float] = {}
        max_step = self._config.max_change_per_second * min(max(dt, 0.0), _MAX_DT)
        for ch in self._device.channels:
            target = 0.0
            if self.armed:
                wanted = min(max(requested.get(ch, 0.0), 0.0), 1.0)
                target = wanted * self._scale * self.channel_max(ch)
            current = self._levels.get(ch, 0.0)
            if target > current:
                target = min(target, current + max_step)
            out[ch] = target
        return out

    async def set_levels(self, requested: Mapping[str, float]) -> None:
        now = time.monotonic()
        dt = 0.0 if self._last_update is None else now - self._last_update
        self._last_update = now
        async with self._lock:
            levels = self.compute(requested, dt)
            try:
                await asyncio.wait_for(self._device.set_levels(levels), self._config.device_timeout)
            except Exception:
                log.exception("device error")
                fault = True
            else:
                fault = False
                self._set_known_levels(levels)
        if fault:
            await self.emergency_stop("device error")

    def _set_known_levels(self, levels: dict[str, float]) -> None:
        changed = any(abs(levels[ch] - self._levels.get(ch, 0.0)) > _LEVEL_EPSILON for ch in levels)
        zeroed = any(levels[ch] == 0.0 != self._levels.get(ch, 0.0) for ch in levels)
        self._levels = levels
        if changed or zeroed:
            self._bus.publish(LevelsChanged(levels=dict(levels)))

    async def shutdown(self) -> None:
        await self.emergency_stop("shutdown")
