"""Chaturbate via the broadcaster Events API (long polling).

The broadcaster generates a personal Events API URL (containing a secret
token) on the Chaturbate site. Each response contains ``events`` and
a ``nextUrl`` to poll next. Only ``tip`` events are turned into tips.
See ``docs/design/platforms.md``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

import aiohttp
from pydantic import Field, SecretStr, field_validator
from yarl import URL

from estim_camming.events import TipEvent
from estim_camming.platforms.base import Platform, PlatformError
from estim_camming.plugins import PLATFORMS, PluginOptions

log = logging.getLogger(__name__)


def parse_tips(payload: dict[str, Any], platform: str = "chaturbate") -> Iterator[TipEvent]:
    """Extract tips from one Events API response."""
    for event in payload.get("events") or []:
        if event.get("method") != "tip":
            continue
        obj = event.get("object") or {}
        tip = obj.get("tip") or {}
        user = obj.get("user") or {}
        anonymous = bool(tip.get("isAnon", False))
        try:
            tokens = int(tip.get("tokens", 0))
        except (TypeError, ValueError):
            log.warning("ignoring tip with invalid token amount: %r", tip.get("tokens"))
            continue
        if tokens <= 0:
            continue
        yield TipEvent(
            platform=platform,
            username="anonymous" if anonymous else str(user.get("username", "unknown")),
            tokens=tokens,
            message=str(tip.get("message") or ""),
            anonymous=anonymous,
            event_id=str(event.get("id", "")),
        )


@PLATFORMS.register("chaturbate")
class ChaturbatePlatform(Platform):
    """Chaturbate broadcaster Events API (long polling)."""

    class Options(PluginOptions):
        url: SecretStr = Field(description="Events API URL including your token. Keep it secret.")
        timeout: int = Field(10, ge=0, le=90, description="Long-poll timeout in seconds.")

        @field_validator("url")
        @classmethod
        def _check_url(cls, value: SecretStr) -> SecretStr:
            if not value.get_secret_value().startswith("https://"):
                raise ValueError("url must be the https:// Events API URL")
            return value

    def describe(self) -> str:
        # The URL path contains the username followed by the secret token.
        parts = URL(self.options.url.get_secret_value()).path.strip("/").split("/")
        username = parts[1] if len(parts) > 1 else "?"
        return f"chaturbate ({username})"

    async def stream(self) -> AsyncIterator[TipEvent]:
        url = URL(self.options.url.get_secret_value())
        seen: set[str] = set()
        client_timeout = aiohttp.ClientTimeout(total=self.options.timeout + 20)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            while True:
                request_url = url.update_query(timeout=str(self.options.timeout))
                async with session.get(request_url) as resp:
                    if resp.status in (401, 403, 404):
                        raise PlatformError(
                            f"Events API returned HTTP {resp.status}; check the URL/token",
                            fatal=True,
                        )
                    if resp.status != 200:
                        raise PlatformError(f"Events API returned HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
                for tip in parse_tips(payload, self.name):
                    if tip.event_id:
                        if tip.event_id in seen:
                            continue
                        seen.add(tip.event_id)
                        if len(seen) > 10_000:
                            seen.clear()
                    yield tip
                if next_url := payload.get("nextUrl"):
                    url = URL(next_url)
