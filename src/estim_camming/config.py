"""Configuration schema and loading (TOML). See ``docs/design/configuration.md``."""

from __future__ import annotations

import ipaddress
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from estim_camming.rules import Rule
from estim_camming.safety import SafetyConfig


class ConfigError(Exception):
    pass


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginConfig(_Section):
    type: str
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class SchedulerConfig(_Section):
    tick_hz: float = Field(20.0, gt=0, le=100)
    max_queue: int = Field(50, ge=1)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OverlayConfig(_Section):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    control_token: SecretStr | None = Field(
        None, description="Required for control API calls when set; mandatory off-localhost."
    )
    recent_tips: int = Field(10, ge=0, le=100)

    @model_validator(mode="after")
    def _require_token_off_localhost(self):
        if not _is_loopback(self.host) and self.control_token is None:
            raise ValueError(
                "overlay.control_token must be set when listening on a non-loopback address"
            )
        return self


class AppConfig(_Section):
    device: PluginConfig
    platforms: list[PluginConfig] = Field(default_factory=list)
    rules: list[Rule] = Field(min_length=1)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)


def parse_config(data: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"config file not found: {path} (create one with 'estim-camming init')"
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    try:
        return parse_config(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
