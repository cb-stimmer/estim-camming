"""Platform plugins. Importing this package registers the built-in platforms."""

from estim_camming.platforms import bridge, chaturbate, simulator, stripchat  # noqa: F401
from estim_camming.platforms.base import Platform, PlatformError

__all__ = ["Platform", "PlatformError"]
