"""HTTP/WebSocket server for the OBS overlay and the performer control panel.

Routes (see ``docs/design/overlay.md``):

* ``GET /overlay``   - page for an OBS Browser Source (transparent background)
* ``GET /control``   - control panel: arm, emergency stop, scale, test tips
* ``GET /ws``        - WebSocket pushing ``{"event": ..., "state": ...}`` messages
* ``GET /api/state`` - current state snapshot
* ``POST /api/...``  - control actions (token-protected when configured)
* ``GET /api/rules`` - rules for the editor (token-protected: shows hidden rules)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from importlib.resources import files
from typing import Any, Protocol

from aiohttp import WSMsgType, web

from estim_camming.bus import EventBus
from estim_camming.config import OverlayConfig
from estim_camming.events import Event
from estim_camming.rules_io import RulesConflict, RulesInvalid

log = logging.getLogger(__name__)

STATIC = files("estim_camming.overlay") / "static"
_SEND_TIMEOUT = 1.0
#: GET routes that need the control token too (they reveal hidden rules).
_PROTECTED_GETS = frozenset({"/api/rules"})


class Controller(Protocol):
    def arm(self) -> None: ...
    async def emergency_stop(self, reason: str = ...) -> None: ...
    def set_scale(self, scale: float) -> None: ...
    def skip_current(self) -> None: ...
    def clear_queue(self) -> None: ...
    def inject_tip(self, username: str, tokens: int, message: str = "") -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
    async def rules_info(self) -> dict[str, Any]: ...
    def check_rules(self, rules: Any) -> dict[str, Any]: ...
    async def replace_rules(self, revision: int, rules: Any) -> dict[str, Any]: ...


class OverlayServer:
    def __init__(self, config: OverlayConfig, bus: EventBus, controller: Controller) -> None:
        self._config = config
        self._bus = bus
        self._controller = controller
        self._clients: set[web.WebSocketResponse] = set()

    def make_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware])
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/overlay", self._page("overlay.html")),
                web.get("/control", self._page("control.html")),
                web.get("/static/{name}", self._static),
                web.get("/ws", self._websocket),
                web.get("/api/state", self._state),
                web.post("/api/arm", self._arm),
                web.post("/api/stop", self._stop),
                web.post("/api/scale", self._scale),
                web.post("/api/skip", self._skip),
                web.post("/api/clear", self._clear),
                web.post("/api/tip", self._tip),
                web.get("/api/rules", self._rules),
                web.post("/api/rules/check", self._rules_check),
                web.post("/api/rules", self._rules_replace),
            ]
        )
        return app

    async def run(self) -> None:
        runner = web.AppRunner(self.make_app(), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self._config.host, self._config.port)
        await site.start()
        base = f"http://{self._config.host}:{self._config.port}"
        log.info("overlay (OBS browser source): %s/overlay", base)
        log.info("control panel: %s/control", base)
        try:
            await self._broadcast_loop()
        finally:
            for ws in list(self._clients):
                await ws.close()
            await runner.cleanup()

    # -- broadcasting ----------------------------------------------------

    def _message(self, event: Event | None) -> str:
        return json.dumps(
            {"event": event.to_dict() if event else None, "state": self._controller.snapshot()}
        )

    async def _broadcast_loop(self) -> None:
        with self._bus.subscribe(maxsize=500) as sub:
            async for event in sub:
                if not self._clients:
                    continue
                message = self._message(event)
                await asyncio.gather(*(self._send(ws, message) for ws in list(self._clients)))

    async def _send(self, ws: web.WebSocketResponse, message: str) -> None:
        try:
            await asyncio.wait_for(ws.send_str(message), _SEND_TIMEOUT)
        except Exception:
            self._clients.discard(ws)

    # -- handlers --------------------------------------------------------

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.method != "POST":
            if request.path in _PROTECTED_GETS:
                self._check_token(request)
            return await handler(request)
        # CSRF protection: a JSON content type forces a CORS preflight for
        # cross-origin requests, and a foreign Origin is rejected outright.
        # Otherwise any web page open in the browser could arm the output.
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Content-Type must be application/json")
        origin = request.headers.get("Origin")
        if origin is not None and origin != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden(text="cross-origin request refused")
        self._check_token(request)
        return await handler(request)

    def _check_token(self, request: web.Request) -> None:
        token = self._config.control_token
        if token is not None:
            given = request.headers.get("X-Control-Token") or request.query.get("token", "")
            if not hmac.compare_digest(given.encode(), token.get_secret_value().encode()):
                raise web.HTTPUnauthorized(text="invalid control token")

    async def _index(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/control")

    def _page(self, name: str):
        async def handler(request: web.Request) -> web.Response:
            return web.Response(text=(STATIC / name).read_text(), content_type="text/html")

        return handler

    async def _static(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        types = {".js": "application/javascript", ".css": "text/css"}
        suffix = name[name.rfind(".") :] if "." in name else ""
        resource = STATIC / name
        if "/" in name or ".." in name or suffix not in types or not resource.is_file():
            raise web.HTTPNotFound()
        return web.Response(text=resource.read_text(), content_type=types[suffix])

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        await ws.send_str(self._message(None))
        self._clients.add(ws)
        try:
            async for msg in ws:  # clients do not send anything meaningful
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.discard(ws)
        return ws

    async def _state(self, request: web.Request) -> web.Response:
        return web.json_response(self._controller.snapshot())

    async def _arm(self, request: web.Request) -> web.Response:
        self._controller.arm()
        return web.json_response({"ok": True})

    async def _stop(self, request: web.Request) -> web.Response:
        await self._controller.emergency_stop("control panel")
        return web.json_response({"ok": True})

    async def _scale(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            scale = float(body["scale"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="expected {'scale': 0..1}") from None
        self._controller.set_scale(scale)
        return web.json_response({"ok": True})

    async def _skip(self, request: web.Request) -> web.Response:
        self._controller.skip_current()
        return web.json_response({"ok": True})

    async def _clear(self, request: web.Request) -> web.Response:
        self._controller.clear_queue()
        return web.json_response({"ok": True})

    async def _tip(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            tokens = int(body["tokens"])
            if tokens < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="expected {'tokens': int >= 1}") from None
        username = str(body.get("username") or "test")[:64]
        self._controller.inject_tip(username, tokens, str(body.get("message") or "")[:200])
        return web.json_response({"ok": True})

    async def _rules(self, request: web.Request) -> web.Response:
        return web.json_response(await self._controller.rules_info())

    async def _rules_check(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(self._controller.check_rules(body.get("rules")))

    async def _rules_replace(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        revision = body.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise web.HTTPBadRequest(text="expected {'revision': int, 'rules': [...]}")
        try:
            result = await self._controller.replace_rules(revision, body.get("rules"))
        except RulesConflict as exc:
            return web.json_response({"error": str(exc), "revision": exc.revision}, status=409)
        except RulesInvalid as exc:
            return web.json_response(exc.check.to_dict(), status=400)
        return web.json_response(result)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return body
