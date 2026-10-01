"""Plugin base class and registries.

Every pluggable component (platform, device, pattern) is a :class:`Plugin`
subclass with a nested ``Options`` pydantic model, registered under a short
name in one of the registries below. Built-in plugins register themselves with
the ``register`` decorator; third-party packages use entry points.

See ``docs/design/plugins.md``.
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


class PluginError(Exception):
    """Raised when a plugin cannot be found or configured."""


class PluginOptions(BaseModel):
    """Base class for plugin options. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


class Plugin(ABC):  # noqa: B024 - abstract methods live in the subclasses
    """Base class for all plugins."""

    #: Registry name, set by :meth:`Registry.register`.
    name: ClassVar[str] = ""
    #: Options schema; override in subclasses.
    Options: ClassVar[type[PluginOptions]] = PluginOptions

    def __init__(self, options: PluginOptions | None = None) -> None:
        self.options: Any = options if options is not None else self.Options()

    @classmethod
    def from_config(cls, options: Mapping[str, Any] | None = None):
        try:
            return cls(cls.Options.model_validate(dict(options or {})))
        except ValidationError as exc:
            raise PluginError(f"invalid options for '{cls.name}':\n{exc}") from exc


P = TypeVar("P", bound=Plugin)


class Registry(Generic[P]):
    """Name -> plugin class mapping, fed by built-ins and entry points."""

    def __init__(self, kind: str, group: str, builtin_module: str) -> None:
        self.kind = kind
        self.group = group
        self._builtin_module = builtin_module
        self._items: dict[str, type[P]] = {}
        self._loaded = False

    def register(self, name: str) -> Callable[[type[P]], type[P]]:
        def decorator(cls: type[P]) -> type[P]:
            existing = self._items.get(name)
            if existing is not None and existing is not cls:
                raise PluginError(f"{self.kind} '{name}' is already registered by {existing!r}")
            cls.name = name
            self._items[name] = cls
            return cls

        return decorator

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        importlib.import_module(self._builtin_module)
        for ep in entry_points(group=self.group):
            if ep.name in self._items:
                continue
            try:
                cls = ep.load()
            except Exception:
                log.exception("failed to load %s plugin '%s' from %s", self.kind, ep.name, ep.value)
                continue
            cls.name = ep.name
            self._items[ep.name] = cls

    def get(self, name: str) -> type[P]:
        self._ensure_loaded()
        try:
            return self._items[name]
        except KeyError:
            available = ", ".join(self.names()) or "none"
            raise PluginError(f"unknown {self.kind} '{name}' (available: {available})") from None

    def create(self, name: str, options: Mapping[str, Any] | None = None) -> P:
        return self.get(name).from_config(options)

    def names(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._items)


PLATFORMS: Registry = Registry("platform", "estim_camming.platforms", "estim_camming.platforms")
DEVICES: Registry = Registry("device", "estim_camming.devices", "estim_camming.devices")
PATTERNS: Registry = Registry("pattern", "estim_camming.patterns", "estim_camming.patterns")
