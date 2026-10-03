import asyncio
import tomllib
from importlib.resources import files

import pytest
from aiohttp.test_utils import TestClient, TestServer

from estim_camming.app import Application
from estim_camming.config import ConfigError, parse_config
from estim_camming.events import ActionFinished
from estim_camming.overlay.server import OverlayServer


@pytest.fixture
def config_data():
    return tomllib.loads((files("estim_camming") / "example_config.toml").read_text())


def test_example_config_is_valid(config_data):
    app = Application(parse_config(config_data))
    assert app.device.channels == ("A", "B")
    assert [p.name for p in app.platforms] == ["simulator"]


def test_control_token_required_off_localhost(config_data):
    config_data["overlay"]["host"] = "0.0.0.0"
    with pytest.raises(ConfigError, match="control_token"):
        parse_config(config_data)


async def test_injected_tip_is_played(config_data):
    config_data["platforms"] = []
    config_data["overlay"]["enabled"] = False
    config_data["safety"].update(start_armed=True, max_action_seconds=0.1)
    app = Application(parse_config(config_data))
    finished = app.bus.subscribe(ActionFinished)
    task = asyncio.create_task(app.run())
    await asyncio.sleep(0)
    app.inject_tip("tester", 30)
    event = await asyncio.wait_for(finished.get(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert event.action.rule == "wave"
    assert app.total_tokens == 30
    assert app.device.levels == {"A": 0.0, "B": 0.0}


async def test_control_api(config_data):
    config_data["platforms"] = []
    config_data["overlay"]["control_token"] = "s3cret"
    app = Application(parse_config(config_data))
    server = OverlayServer(app.config.overlay, app.bus, app)
    async with TestClient(TestServer(server.make_app())) as client:
        headers = {"X-Control-Token": "s3cret"}
        assert (await client.post("/api/arm", json={})).status == 401
        assert (await client.post("/api/arm", json={}, headers=headers)).status == 200
        assert app.guard.armed
        # CSRF: non-JSON and foreign-origin POSTs are refused.
        assert (await client.post("/api/stop", data="x", headers=headers)).status == 415
        assert (
            await client.post(
                "/api/stop", json={}, headers=headers | {"Origin": "https://evil.example"}
            )
        ).status == 403
        assert (await client.post("/api/stop", json={}, headers=headers)).status == 200
        assert not app.guard.armed
        state = await (await client.get("/api/state")).json()
        assert state["armed"] is False and len(state["menu"]) == 6
        assert (await client.get("/overlay")).status == 200
        assert (await client.get("/static/common.js")).status == 200
        assert (await client.get("/static/..%2Fserver.py")).status == 404
