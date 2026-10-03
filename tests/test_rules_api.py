"""The rules editor API: /api/rules, /api/rules/check and saving to config.toml."""

import asyncio
import tomllib
from importlib.resources import files

import pytest
from aiohttp.test_utils import TestClient, TestServer

from estim_camming.cli import _build
from estim_camming.events import RulesChanged
from estim_camming.overlay.server import OverlayServer

TOKEN = {"X-Control-Token": "s3cret"}
NEW_RULES = [
    {"name": "small", "min_tokens": 1, "max_tokens": 9, "intensity": 0.2, "duration": 1},
    {"name": "hidden", "min_tokens": 10, "intensity": 0.4, "duration": 1, "show_in_menu": False},
]


@pytest.fixture
def config_path(tmp_path):
    text = (files("estim_camming") / "example_config.toml").read_text()
    assert "# control_token" in text
    path = tmp_path / "config.toml"
    path.write_text(text.replace("# control_token", 'control_token = "s3cret"  #', 1))
    return path


@pytest.fixture
async def api(config_path):
    app = _build(str(config_path))
    server = OverlayServer(app.config.overlay, app.bus, app)
    async with TestClient(TestServer(server.make_app())) as client:
        yield app, client


async def test_get_rules_needs_the_token(api):
    app, client = api
    assert (await client.get("/api/rules")).status == 401
    info = await (await client.get("/api/rules", headers=TOKEN)).json()
    assert info["revision"] == 0
    assert [r["name"] for r in info["rules"]] == [r.name for r in app.rules.rules]
    assert info["channels"] == ["A", "B"]
    assert "period" in info["patterns"]["pulse"]["schema"]["properties"]
    assert info["patterns"]["pulse"]["description"] == "On/off square wave."
    assert info["limits"]["max_action_seconds"] == app.config.safety.max_action_seconds
    assert info["file"]["writable"] and not info["file"]["changed_on_disk"]


async def test_check_reports_without_changing_anything(api):
    app, client = api
    before = app.rules
    reply = await client.post(
        "/api/rules/check",
        json={"rules": [NEW_RULES[0] | {"intensity": 5}]},
        headers=TOKEN,
    )
    result = await reply.json()
    assert result["ok"] is False
    assert result["errors"][0]["rule"] == 0 and result["errors"][0]["field"] == "intensity"
    assert app.rules is before and app.rules_revision == 0


async def test_save_swaps_rules_publishes_and_writes_the_file(api, config_path):
    app, client = api
    events = app.bus.subscribe(RulesChanged)
    reply = await client.post("/api/rules", json={"revision": 0, "rules": NEW_RULES}, headers=TOKEN)
    assert reply.status == 200
    result = await reply.json()
    assert result["revision"] == 1 and result["saved"] is True
    assert [r.name for r in app.rules.rules] == ["small", "hidden"]
    event = await asyncio.wait_for(events.get(), 1)
    assert (event.revision, event.saved) == (1, True)
    state = await (await client.get("/api/state")).json()
    assert state["rules_revision"] == 1
    assert [m["label"] for m in state["menu"]] == ["small"]  # hidden rule not in the menu
    saved = tomllib.loads(config_path.read_text())
    assert [r["name"] for r in saved["rules"]] == ["small", "hidden"]
    assert saved["overlay"]["control_token"] == "s3cret"  # everything else kept


async def test_stale_revision_is_a_conflict(api):
    app, client = api
    ok = await client.post("/api/rules", json={"revision": 0, "rules": NEW_RULES}, headers=TOKEN)
    assert ok.status == 200
    stale = await client.post(
        "/api/rules", json={"revision": 0, "rules": NEW_RULES[:1]}, headers=TOKEN
    )
    assert stale.status == 409
    assert (await stale.json())["revision"] == 1
    assert len(app.rules.rules) == 2


async def test_invalid_rules_keep_the_old_ones(api, config_path):
    app, client = api
    before, text = app.rules, config_path.read_text()
    reply = await client.post(
        "/api/rules",
        json={"revision": 0, "rules": [NEW_RULES[0] | {"channels": ["X"]}]},
        headers=TOKEN,
    )
    assert reply.status == 400
    assert (await reply.json())["errors"][0]["field"] == "channels"
    assert app.rules is before and app.rules_revision == 0
    assert config_path.read_text() == text


async def test_bad_requests(api):
    _, client = api
    assert (await client.post("/api/rules", json={"rules": []}, headers=TOKEN)).status == 400
    no_token = await client.post("/api/rules", json={"revision": 0, "rules": NEW_RULES})
    assert no_token.status == 401
    foreign = await client.post(
        "/api/rules",
        json={"revision": 0, "rules": NEW_RULES},
        headers=TOKEN | {"Origin": "https://evil.example"},
    )
    assert foreign.status == 403


async def test_hand_edited_file_is_not_overwritten(api, config_path):
    app, client = api
    config_path.write_text(config_path.read_text() + "\n# hand edit\n")
    reply = await client.post("/api/rules", json={"revision": 0, "rules": NEW_RULES}, headers=TOKEN)
    result = await reply.json()
    assert reply.status == 200 and result["saved"] is False
    assert "changed by hand" in result["detail"]
    assert [r.name for r in app.rules.rules] == ["small", "hidden"]  # live anyway
    assert config_path.read_text().endswith("# hand edit\n")


async def test_new_tips_use_new_rules_and_queued_actions_keep_theirs(api):
    app, client = api
    app.guard.arm("test")
    app.scheduler.enqueue(app.rules.action_for(_tip(30)))
    queued = app.scheduler.queue[0]
    await client.post("/api/rules", json={"revision": 0, "rules": NEW_RULES}, headers=TOKEN)
    assert app.scheduler.queue[0] is queued and queued.rule == "wave"
    assert app.rules.action_for(_tip(30)).rule == "hidden"


async def test_without_a_rules_file_changes_are_live_only(config_path):
    from estim_camming.app import Application
    from estim_camming.config import load_config

    app = Application(load_config(config_path))
    result = await app.replace_rules(0, NEW_RULES)
    assert result["saved"] is False and "live only" in result["detail"]
    assert (await app.rules_info())["file"] is None


def _tip(tokens):
    from estim_camming.events import TipEvent

    return TipEvent(platform="test", username="u", tokens=tokens)
