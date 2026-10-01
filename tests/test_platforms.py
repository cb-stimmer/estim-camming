import asyncio

import pytest

from estim_camming.platforms.chaturbate import parse_tips
from estim_camming.plugins import PLATFORMS, PluginError


def test_parse_chaturbate_events():
    payload = {
        "events": [
            {
                "method": "tip",
                "id": "1",
                "object": {
                    "tip": {"tokens": 25, "isAnon": False, "message": "hi"},
                    "user": {"username": "fan1"},
                },
            },
            {
                "method": "tip",
                "id": "2",
                "object": {
                    "tip": {"tokens": 10, "isAnon": True, "message": ""},
                    "user": {"username": "secret"},
                },
            },
            {"method": "chatMessage", "id": "3", "object": {}},
            {"method": "tip", "id": "4", "object": {"tip": {"tokens": "bad"}}},
        ],
        "nextUrl": "https://example.invalid/next",
    }
    tips = list(parse_tips(payload))
    assert [(t.username, t.tokens, t.anonymous, t.event_id) for t in tips] == [
        ("fan1", 25, False, "1"),
        ("anonymous", 10, True, "2"),
    ]
    assert tips[0].message == "hi"


def test_chaturbate_describe_hides_token():
    platform = PLATFORMS.create(
        "chaturbate", {"url": "https://eventsapi.chaturbate.com/events/model/SECRET123/"}
    )
    assert platform.describe() == "chaturbate (model)"
    assert "SECRET123" not in repr(platform.options)


def test_chaturbate_requires_https():
    with pytest.raises(PluginError):
        PLATFORMS.create("chaturbate", {"url": "http://insecure"})


async def test_simulator_yields_tips_in_range():
    sim = PLATFORMS.create(
        "simulator", {"interval": 0.001, "min_tokens": 5, "max_tokens": 6, "seed": 1}
    )
    stream = sim.stream()
    tips = [await asyncio.wait_for(anext(stream), 1) for _ in range(5)]
    assert all(5 <= t.tokens <= 6 for t in tips)
