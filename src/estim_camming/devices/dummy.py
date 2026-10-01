"""A device that only logs: for testing rules and overlays without hardware."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import Field

from estim_camming.devices.base import Device, DeviceError
from estim_camming.plugins import DEVICES, PluginOptions

log = logging.getLogger(__name__)


@DEVICES.register("dummy")
class DummyDevice(Device):
    """Logs output levels; no hardware."""

    class Options(PluginOptions):
        channels: list[str] = Field(default_factory=lambda: ["A", "B"], min_length=1)
        log_step: float = Field(0.1, gt=0, description="Log when a level moves this much.")

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.levels = {ch: 0.0 for ch in self.options.channels}
        self._logged = dict(self.levels)

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(self.options.channels)

    async def connect(self) -> None:
        log.info("dummy device connected (channels %s)", ", ".join(self.channels))

    async def set_levels(self, levels: Mapping[str, float]) -> None:
        unknown = set(levels) - set(self.levels)
        if unknown:
            raise DeviceError(f"unknown channel(s) {sorted(unknown)}")
        self.levels.update(levels)
        if any(
            abs(self.levels[ch] - self._logged[ch]) >= self.options.log_step
            or (self.levels[ch] == 0.0 != self._logged[ch])
            for ch in self.levels
        ):
            self._logged = dict(self.levels)
            log.info(
                "dummy output %s", "  ".join(f"{ch}={v:4.0%}" for ch, v in self.levels.items())
            )
