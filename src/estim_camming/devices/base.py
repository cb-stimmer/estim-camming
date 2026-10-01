"""Device plugin interface."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping

from estim_camming.plugins import Plugin


class DeviceError(Exception):
    pass


class Device(Plugin):
    """An output device with one or more named channels.

    Levels are normalised floats in 0..1 (fraction of the device's full
    scale); the device maps them to its native units. Devices are only ever
    driven through :class:`~estim_camming.safety.SafetyGuard`.
    """

    @property
    @abstractmethod
    def channels(self) -> tuple[str, ...]:
        """Channel names, e.g. ``("A", "B")``."""

    async def connect(self) -> None:
        """Open the connection. Raise :class:`DeviceError` on failure."""

    async def disconnect(self) -> None:
        """Close the connection. Must be safe to call when not connected."""

    @abstractmethod
    async def set_levels(self, levels: Mapping[str, float]) -> None:
        """Set output levels. ``levels`` contains every channel."""

    async def stop(self) -> None:
        """Drive every channel to zero as fast as the device allows."""
        await self.set_levels({ch: 0.0 for ch in self.channels})
