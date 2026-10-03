"""Application: builds the components from config and runs them.

Data flow (see ``docs/design/architecture.md``)::

    Platform(s) --TipEvent--> EventBus --> dispatcher --> RuleEngine --> Scheduler
                                  |                                        |
                                  v                                        v
                            OverlayServer <--- events ---------------- SafetyGuard --> Device
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import asdict
from typing import Any

from estim_camming.bus import EventBus
from estim_camming.config import AppConfig
from estim_camming.devices import Device
from estim_camming.events import PlatformStatus, RulesChanged, TipEvent
from estim_camming.platforms import Platform, PlatformError
from estim_camming.plugins import DEVICES, PATTERNS, PLATFORMS
from estim_camming.rules import RuleEngine
from estim_camming.rules_io import (
    RulesConflict,
    RulesFile,
    RulesFileError,
    RulesInvalid,
    check_rules,
    rule_to_data,
)
from estim_camming.safety import SafetyGuard
from estim_camming.scheduler import Scheduler

log = logging.getLogger(__name__)

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0


class Application:
    """Owns all components. Also implements the controller API used by the
    overlay/control server (``arm``, ``emergency_stop``, ``snapshot``, ...)."""

    def __init__(self, config: AppConfig, rules_file: RulesFile | None = None) -> None:
        self.config = config
        #: Where the rules editor saves rules; None: live changes only.
        self.rules_file = rules_file
        self.rules_revision = 0
        self._rules_lock = asyncio.Lock()
        self.bus = EventBus()
        self.device: Device = DEVICES.create(config.device.type, config.device.options)
        self.platforms: list[Platform] = [
            PLATFORMS.create(p.type, p.options) for p in config.platforms if p.enabled
        ]
        self.rules = RuleEngine(
            config.rules, self.device.channels, config.safety.max_action_seconds
        )
        self.guard = SafetyGuard(self.device, config.safety, self.bus)
        self.scheduler = Scheduler(
            self.guard,
            self.bus,
            self.device.channels,
            tick_hz=config.scheduler.tick_hz,
            max_queue=config.scheduler.max_queue,
        )
        self.guard.on_disarm(self.scheduler.clear)
        self.recent_tips: deque[TipEvent] = deque(maxlen=config.overlay.recent_tips)
        self.total_tokens = 0
        self.platform_status: dict[str, dict[str, Any]] = {
            p.describe(): {"connected": False, "detail": ""} for p in self.platforms
        }

    # -- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        tips = self.bus.subscribe(TipEvent)
        await self.device.connect()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.scheduler.run(), name="scheduler")
                tg.create_task(self._dispatch(tips), name="dispatcher")
                for platform in self.platforms:
                    tg.create_task(self._supervise(platform), name=platform.describe())
                if self.config.overlay.enabled:
                    from estim_camming.overlay.server import OverlayServer

                    tg.create_task(OverlayServer(self.config.overlay, self.bus, self).run())
                if not self.guard.armed:
                    log.warning("output is DISARMED - arm it from the control panel when ready")
        finally:
            tips.close()
            await self.guard.shutdown()
            await self.device.disconnect()

    async def _dispatch(self, tips) -> None:
        async for tip in tips:
            self.recent_tips.appendleft(tip)
            self.total_tokens += tip.tokens
            action = self.rules.action_for(tip)
            log.info(
                "tip %d from %s (%s) -> %s",
                tip.tokens,
                tip.username,
                tip.platform,
                action.label if action else "no matching rule",
            )
            if action is not None:
                self.scheduler.enqueue(action)

    async def _supervise(self, platform: Platform) -> None:
        """Run a platform forever, reconnecting with exponential back-off."""
        name = platform.describe()
        delay = _BACKOFF_INITIAL
        while True:
            try:
                self._set_platform_status(name, True, "")
                async for tip in platform.stream():
                    delay = _BACKOFF_INITIAL
                    self.bus.publish(tip)
                detail = "stream ended"
            except asyncio.CancelledError:
                raise
            except PlatformError as exc:
                detail = str(exc)
                if exc.fatal:
                    log.error("%s: %s (giving up)", name, exc)
                    self._set_platform_status(name, False, detail)
                    return
            except Exception as exc:
                log.debug("platform error", exc_info=True)
                detail = f"{type(exc).__name__}: {exc}"
            log.warning("%s disconnected: %s; retrying in %.0fs", name, detail, delay)
            self._set_platform_status(name, False, detail)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _BACKOFF_MAX)

    def _set_platform_status(self, name: str, connected: bool, detail: str) -> None:
        self.platform_status[name] = {"connected": connected, "detail": detail}
        self.bus.publish(PlatformStatus(platform=name, connected=connected, detail=detail))

    # -- controller API (used by the overlay/control server) -------------

    def arm(self) -> None:
        self.guard.arm("control panel")

    async def emergency_stop(self, reason: str = "control panel") -> None:
        await self.guard.emergency_stop(reason)

    def set_scale(self, scale: float) -> None:
        self.guard.set_scale(scale)

    def skip_current(self) -> None:
        self.scheduler.skip_current()

    def clear_queue(self) -> None:
        self.scheduler.clear()

    def inject_tip(self, username: str, tokens: int, message: str = "") -> None:
        self.bus.publish(
            TipEvent(platform="control-panel", username=username, tokens=tokens, message=message)
        )

    # -- rules editor (see docs/design/rules-editor.md) --------------------

    async def rules_info(self) -> dict[str, Any]:
        file = await asyncio.to_thread(self.rules_file.info) if self.rules_file else None
        safety = self.config.safety
        return {
            "revision": self.rules_revision,
            "rules": [rule_to_data(rule) for rule in self.rules.rules],
            "channels": list(self.device.channels),
            "patterns": {
                name: {
                    "description": (PATTERNS.get(name).__doc__ or "").strip().split("\n")[0],
                    "schema": PATTERNS.get(name).Options.model_json_schema(),
                }
                for name in PATTERNS.names()
            },
            "limits": {
                "max_action_seconds": safety.max_action_seconds,
                "max_level": safety.max_level,
                "channel_max_level": dict(safety.channel_max_level),
            },
            "file": file,
        }

    def check_rules(self, rules: Any) -> dict[str, Any]:
        return self._check(rules).to_dict()

    def _check(self, rules: Any):
        return check_rules(rules, self.device.channels, self.config.safety.max_action_seconds)

    async def replace_rules(self, revision: int, rules: Any) -> dict[str, Any]:
        """Swap in a new rule set (all or nothing), then save it to the config file.

        New tips use the new rules at once; queued and playing actions keep
        theirs. Raises RulesConflict for a stale ``revision`` and RulesInvalid if
        the rules don't validate (the old rules stay live)."""
        async with self._rules_lock:
            if revision != self.rules_revision:
                raise RulesConflict(self.rules_revision)
            check = self._check(rules)
            if not check.ok:
                raise RulesInvalid(check)
            assert check.rules is not None
            self.rules = RuleEngine(
                check.rules, self.device.channels, self.config.safety.max_action_seconds
            )
            self.rules_revision += 1
            log.info(
                "rules replaced (revision %d, %d rules)", self.rules_revision, len(check.rules)
            )
            saved, detail = False, "no config file (live only)"
            if self.rules_file is not None:
                try:
                    await asyncio.to_thread(self.rules_file.write, check.rules)
                    saved, detail = True, f"saved to {self.rules_file.path}"
                except RulesFileError as exc:
                    detail = str(exc)
                    log.warning("rules not saved: %s", detail)
            self.bus.publish(RulesChanged(revision=self.rules_revision, saved=saved))
            return {
                "revision": self.rules_revision,
                "saved": saved,
                "detail": detail,
                "warnings": check.to_dict()["warnings"],
            }

    def snapshot(self) -> dict[str, Any]:
        current = self.scheduler.current
        return {
            "armed": self.guard.armed,
            "scale": self.guard.scale,
            "levels": self.guard.levels,
            "channels": list(self.device.channels),
            "current": (
                asdict(current) | {"elapsed": round(self.scheduler.elapsed(), 2)}
                if current
                else None
            ),
            "queue": [asdict(a) for a in self.scheduler.queue],
            "recent_tips": [t.to_dict() for t in self.recent_tips],
            "total_tokens": self.total_tokens,
            "menu": self.rules.menu(),
            "rules_revision": self.rules_revision,
            "platforms": self.platform_status,
        }
