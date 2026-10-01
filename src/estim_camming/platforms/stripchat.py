"""Stripchat via the browser bridge.

Stripchat has no public tip/events API. The default ``websocket`` mode has the
userscript observe the WebSocket that the performer's own Stripchat page already
uses for chat, and forward its frames. Tips are chat messages with
``type`` ``"tip"`` or ``"privateTip"``::

    {"subscriptionKey": "newChatMessage:...",
     "params": {"message": {"id": ..., "type": "tip",
                            "details": {"amount": 25, "isAnonymous": false, "body": "..."},
                            "userData": {"username": "fan"}}}}

This format is not documented by Stripchat. It was taken from the (unlicensed,
2022) open-source mermaid-extension project and must be verified against the
live site. The parser therefore looks for tip message objects anywhere in a
frame instead of relying on the exact envelope. A viewer typing tip-like text
produces a ``"text"`` message, so it can never be mistaken for a tip.

``mode = "dom"`` (reading tip elements from the page) remains as a fallback.
See ``docs/user-guide/stripchat.md`` and ``docs/design/platforms.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import Field

from estim_camming.events import TipEvent
from estim_camming.platforms.bridge import BridgeOptions, BridgePlatform
from estim_camming.plugins import PLATFORMS

log = logging.getLogger(__name__)

TIP_TYPES = frozenset({"tip", "privateTip"})
_MAX_DEPTH = 12


def _json_documents(frame: str) -> Iterator[Any]:
    """A frame is one JSON document, or several separated by newlines."""
    try:
        yield json.loads(frame)
        return
    except json.JSONDecodeError:
        pass
    for line in frame.splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _tip_messages(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Find chat message objects of a tip type anywhere in a JSON document."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        details = node.get("details")
        if node.get("type") in TIP_TYPES and isinstance(details, dict) and "amount" in details:
            yield node
            return
        for value in node.values():
            yield from _tip_messages(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _tip_messages(value, depth + 1)


def parse_stripchat_frame(frame: str, platform: str = "stripchat") -> list[TipEvent]:
    tips: list[TipEvent] = []
    for document in _json_documents(frame):
        for msg in _tip_messages(document):
            details = msg["details"]
            try:
                tokens = int(details["amount"])
            except (TypeError, ValueError):
                log.warning("stripchat: tip with invalid amount: %r", details.get("amount"))
                continue
            if tokens <= 0:
                continue
            anonymous = bool(details.get("isAnonymous", False))
            user_data = msg.get("userData") if isinstance(msg.get("userData"), dict) else {}
            username = "anonymous" if anonymous else str(user_data.get("username") or "unknown")
            msg_id = msg.get("id")
            if msg_id is None:
                digest = hashlib.sha1(json.dumps(msg, sort_keys=True).encode()).hexdigest()
                msg_id = f"h{digest[:16]}"
            tips.append(
                TipEvent(
                    platform=platform,
                    username=username,
                    tokens=tokens,
                    message=str(details.get("body") or "")[:200],
                    anonymous=anonymous,
                    event_id=f"{platform}:{msg_id}",
                )
            )
    return tips


@PLATFORMS.register("stripchat")
class StripchatPlatform(BridgePlatform):
    """Stripchat, via a browser userscript (no official tip API)."""

    class Options(BridgeOptions):
        site: str = Field("stripchat", description="Platform name shown in logs and the overlay.")
        mode: Literal["websocket", "dom"] = Field(
            "websocket",
            description="'websocket': read tips from the page's chat connection "
            "(recommended); 'dom': read tip elements from the page (fallback).",
        )
        match: list[str] = Field(
            default_factory=lambda: ["https://stripchat.com/*", "https://*.stripchat.com/*"],
            description="Userscript @match URL patterns (pages to run on).",
        )

    def parse_frame(self, frame: str) -> list[TipEvent]:
        return parse_stripchat_frame(frame, self.site)
