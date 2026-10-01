"""Device plugins. Importing this package registers the built-in devices."""

from estim_camming.devices import dummy, estim2b  # noqa: F401
from estim_camming.devices.base import Device, DeviceError

__all__ = ["Device", "DeviceError"]
