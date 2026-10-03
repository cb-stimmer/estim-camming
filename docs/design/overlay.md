# Overlay and control panel

`estim_camming.overlay.server.OverlayServer` is an `aiohttp` web server started
by the application when `overlay.enabled = true`. It serves:

- the **overlay**, a transparent page used as an OBS *Browser Source*,
- the **control panel**, the performer's page with the emergency stop,
- a **WebSocket** that pushes live state to both pages,
- a small **JSON control API**.

It depends only on the `EventBus` and a `Controller` protocol, implemented by
`Application`:

```python
class Controller(Protocol):
    def arm(self) -> None
    async def emergency_stop(self, reason: str = ...) -> None
    def set_scale(self, scale: float) -> None
    def skip_current(self) -> None
    def clear_queue(self) -> None
    def inject_tip(self, username: str, tokens: int, message: str = "") -> None
    def snapshot(self) -> dict
    async def rules_info(self) -> dict
    def check_rules(self, rules) -> dict
    async def replace_rules(self, revision: int, rules) -> dict
```

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Redirect to `/control` |
| GET | `/overlay` | Overlay page (`static/overlay.html`) |
| GET | `/control` | Control panel (`static/control.html`) |
| GET | `/static/{name}` | `.js` / `.css` from `overlay/static` (no sub-paths) |
| GET | `/ws` | WebSocket, see protocol below |
| GET | `/api/state` | Current snapshot as JSON |
| POST | `/api/arm` | Arm output |
| POST | `/api/stop` | Emergency stop (disarms, clears queue) |
| POST | `/api/scale` | `{"scale": 0..1}` master level |
| POST | `/api/skip` | Skip current action |
| POST | `/api/clear` | Clear queue |
| POST | `/api/tip` | `{"username": str, "tokens": int ≥ 1, "message"?: str}` test tip |
| GET | `/api/rules` | Rules, pattern schemas, limits and file state for the rules editor (needs the token) |
| POST | `/api/rules/check` | `{"rules": [...]}` → problems, no side effects |
| POST | `/api/rules` | `{"revision": int, "rules": [...]}` replace the rules and save them (409 on a stale revision, 400 if invalid) |

All `POST`s must be `application/json`, must not come from a foreign `Origin`,
and must carry `X-Control-Token: <token>` (or `?token=`) when a token is
configured. See [Safety](safety.md) S10. `GET /api/rules` needs the token as
well, because it shows hidden rules. The rules routes are described in
[Rules editor](rules-editor.md).

## WebSocket protocol

Server → client only. Every message is:

```text
{"event": {"type": "tip", "timestamp": 1790000000.0, "...": "..."} , "state": { ... }}
```

- On connect, the server sends one message with `"event": null`.
- After that, it sends one message per bus event (see [Events](events.md) for
  `event` shapes).
- `state` is always a complete snapshot, so clients just re-render from it and
  use `event` only for effects such as the tip flash. A client that misses
  messages recovers with the next one.

Snapshot (`Application.snapshot()`):

```text
{
  "armed": true, "scale": 1.0,
  "channels": ["A", "B"], "levels": {"A": 0.2, "B": 0.0},
  "current": {"label": "...", "duration": 10.0, "elapsed": 3.2, "tip": {...}, "...": "..."} ,
  "queue": [ {action}, ... ],
  "recent_tips": [ {tip event}, ... ],
  "total_tokens": 1234,
  "menu": [ {"label": "Tease", "min_tokens": 1, "max_tokens": 24, "duration": 5.0} ],
  "rules_revision": 0,
  "platforms": {"chaturbate (name)": {"connected": true, "detail": ""}}
}
```

Slow clients: each send has a 1 s timeout, after which the client is dropped. It
reconnects automatically (`common.js`, back-off up to 5 s). The server's bus
subscription is bounded (500), so the core never waits for the overlay.

## Overlay page

`overlay.html` has five widgets: `menu`, `current`, `queue`, `levels`, `tips`.
`?widgets=menu,current` selects which ones are shown, so the streamer can add
several Browser Sources and position each widget independently. The page
background is transparent, and colours are CSS variables (`--fg`, `--accent`,
`--bg`, `--muted`) that can be overridden in OBS's *Custom CSS* field. The
`current` widget interpolates progress locally between messages.

## Security notes

- Default bind address is `127.0.0.1`. OBS and the browser run on the same PC.
- All user-provided strings (usernames, tip messages, labels) are HTML-escaped
  in the pages (`escapeHtml` in `common.js`). Keep that when adding widgets:
  usernames and messages are attacker-controlled.
- `/static` only serves known extensions from the package directory, with no
  path separators.

## Extending

- New widget: add a `<section class="widget" id="...">` to `overlay.html` and
  render it from `state` in `render()`.
- New state: add it to `Application.snapshot()` and document it above.
- Alternative visualisations (a different page, or another app such as a
  StreamDeck plugin) can use `/ws` and `/api/*` without changes to the core.
