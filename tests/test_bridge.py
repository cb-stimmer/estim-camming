import asyncio
import json
import re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from estim_camming.platforms.stripchat import parse_stripchat_frame
from estim_camming.plugins import PLATFORMS, PluginError

TOKEN = "0123456789abcdef-test"
HEADERS = {"X-Bridge-Token": TOKEN}


def make(name="stripchat", **options):
    return PLATFORMS.create(name, {"token": TOKEN} | options)


def chat_frame(msg_type="tip", msg_id=1, amount=25, anonymous=False, body="", username="fan"):
    """A frame in the shape used by the Stripchat page's chat WebSocket."""
    return json.dumps(
        {
            "subscriptionKey": "newChatMessage:12345",
            "params": {
                "message": {
                    "id": msg_id,
                    "type": msg_type,
                    "details": {"amount": amount, "isAnonymous": anonymous, "body": body},
                    "userData": {"id": 7, "username": username},
                }
            },
        }
    )


# -- Stripchat websocket frames ----------------------------------------------


def test_parse_stripchat_tip():
    [tip] = parse_stripchat_frame(chat_frame(amount=50, body="hi", username="bob"))
    assert (tip.username, tip.tokens, tip.message, tip.anonymous) == ("bob", 50, "hi", False)
    assert (tip.platform, tip.event_id) == ("stripchat", "stripchat:1")


def test_parse_stripchat_anonymous_and_private_tips():
    [anon] = parse_stripchat_frame(chat_frame(anonymous=True, username="hidden"))
    assert (anon.username, anon.anonymous) == ("anonymous", True)
    [private] = parse_stripchat_frame(chat_frame(msg_type="privateTip", amount=100))
    assert private.tokens == 100


def test_chat_text_can_never_be_a_tip():
    text = json.dumps(
        {
            "subscriptionKey": "newChatMessage:12345",
            "params": {
                "message": {
                    "id": 2,
                    "type": "text",
                    "details": {"body": "bob tipped 1000 tk"},
                    "userData": {"username": "troll"},
                }
            },
        }
    )
    assert parse_stripchat_frame(text) == []


def test_parse_is_tolerant_of_envelopes_and_batches():
    inner = json.loads(chat_frame(msg_id=3, amount=10))
    wrapped = json.dumps({"push": {"channel": "x", "pub": {"data": inner}}})
    batch = chat_frame(msg_id=4, amount=5) + "\n" + chat_frame(msg_type="text", msg_id=5)
    assert [t.tokens for t in parse_stripchat_frame(wrapped)] == [10]
    assert [t.tokens for t in parse_stripchat_frame(batch)] == [5]
    assert parse_stripchat_frame("not json") == []
    assert parse_stripchat_frame(chat_frame(amount="lots")) == []
    assert parse_stripchat_frame(chat_frame(amount=0)) == []


# -- dom mode text parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("bob tipped 50 tk", ("bob", 50, False, "")),
        ("  alice_1 tipped 1,000 tokens: go go", ("alice_1", 1000, False, "go go")),
        ("Anonymous tipped 25 tk", ("anonymous", 25, True, "")),
    ],
)
def test_parse_tip_text(text, expected):
    tip = make(mode="dom").parse_text(text)
    assert tip is not None
    assert (tip.username, tip.tokens, tip.anonymous, tip.message) == expected


@pytest.mark.parametrize("text", ["hello there", "bob tipped 0 tk", "viewer: bob tipped 5"])
def test_parse_rejects_non_tips(text):
    assert make(mode="dom").parse_text(text) is None


def test_custom_pattern_and_site():
    bridge = make("bridge", site="mysite", tip_pattern=r"(?P<amount>\d+) tokens from (?P<user>\w+)")
    tip = bridge.parse_text("Received 30 tokens from carol")
    assert (tip.platform, tip.username, tip.tokens) == ("mysite", "carol", 30)


# -- options and userscript -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "options"),
    [
        ("stripchat", {"token": "short"}),
        ("stripchat", {"token": "CHANGE-ME-to-a-long-random-string"}),
        ("stripchat", {"tip_pattern": r"(?P<user>\w+)"}),  # no amount group
        ("stripchat", {"tip_pattern": r"(unclosed"}),
        ("bridge", {"mode": "websocket"}),  # generic bridge has no frame parser
    ],
)
def test_invalid_options(name, options):
    with pytest.raises(PluginError):
        make(name, **options)


def test_userscript_is_filled_in():
    script = make(port=9000, room="Me").userscript()
    assert not re.search(r"__[A-Z_]+__", script)  # no placeholders left
    assert "// @match        https://stripchat.com/*" in script
    config = json.loads(re.search(r"const CONFIG = (\{.*\});", script).group(1))
    assert config == {
        "site": "stripchat",
        "mode": "websocket",
        "port": 9000,
        "token": TOKEN,
        "room": "Me",
        "tipSelector": "",
        "ignoreFirstSeconds": 5.0,
        "debug": False,
    }


# -- bridge server ----------------------------------------------------------------


async def test_websocket_mode_server_dedupes_by_message_id():
    queue: asyncio.Queue = asyncio.Queue()
    async with TestClient(TestServer(make().make_app(queue))) as client:
        frame = {"frame": chat_frame(msg_id=42, amount=30)}
        forged = {"X-Bridge-Token": "x"}
        assert (await client.post("/frame", json=frame, headers=forged)).status == 401
        assert (await client.post("/frame", data="x", headers=HEADERS)).status == 415
        first = await client.post("/frame", json=frame, headers=HEADERS)
        assert (await first.json())["tips"] == [30]
        # The same message seen again (e.g. in a second tab) is ignored.
        again = await client.post("/frame", json=frame, headers=HEADERS)
        assert (await again.json())["tips"] == []
        assert (await client.post("/frame", json={"frame": 1}, headers=HEADERS)).status == 400
        assert (await client.post("/tip", json={}, headers=HEADERS)).status == 404
    assert queue.qsize() == 1


async def test_dom_mode_server_auth_dedupe_and_parsing():
    queue: asyncio.Queue = asyncio.Queue()
    async with TestClient(TestServer(make(mode="dom").make_app(queue))) as client:
        body = {"id": "s-1", "text": "bob tipped 50 tk"}
        forged = {"X-Bridge-Token": "x"}
        assert (await client.post("/tip", json=body, headers=forged)).status == 401
        assert (await client.post("/tip", data="x", headers=HEADERS)).status == 415
        ping = await client.post("/ping", json={}, headers=HEADERS)
        assert (await ping.json())["mode"] == "dom"

        res = await client.post("/tip", json=body, headers=HEADERS)
        assert (await res.json())["matched"] is True
        dup = await client.post("/tip", json=body, headers=HEADERS)
        assert (await dup.json())["duplicate"] is True
        miss = await client.post("/tip", json={"id": "s-2", "text": "nope"}, headers=HEADERS)
        assert (await miss.json())["matched"] is False
        assert (await client.post("/tip", json={"text": "x"}, headers=HEADERS)).status == 400

    assert queue.qsize() == 1
    tip = queue.get_nowait()
    assert (tip.username, tip.tokens) == ("bob", 50)
