# Platforms

A *platform* is a source of tips, usually a webcam streaming site.

## Interface

```python
class Platform(Plugin):
    def stream(self) -> AsyncIterator[TipEvent]: ...   # abstract, usually an async generator
    def describe(self) -> str: ...                     # safe display name, default: self.name
```

Contract:

- `stream()` connects, then yields `TipEvent`s **forever** until cancelled.
- It is an async generator. Resources (HTTP sessions, sockets) must be opened
  inside it with `async with` so cancellation cleans them up.
- On a connection problem it **raises**; it does not retry internally. Raise
  `PlatformError(msg)` for transient problems, or `PlatformError(msg, fatal=True)`
  when retrying cannot help (bad credentials, account not found). Any other
  exception counts as transient.
- It must de-duplicate tips if the site can deliver one twice, using `event_id`
  where available.
- `TipEvent.platform` should be `self.name` (or the configured site name for the
  generic bridge). For anonymous tips, set
  `anonymous=True` and `username="anonymous"`.
- `describe()` must not reveal secrets. It is shown in logs and the control panel.

## Supervision

`Application._supervise()` wraps each platform:

```text
loop:
    status = connected
    for tip in platform.stream(): publish(tip); reset back-off
    on fatal PlatformError  -> status = disconnected(detail); stop this platform
    on other exception/end  -> status = disconnected(detail); sleep(back-off); back-off *= 2 (max 60 s)
```

Status changes are published as `PlatformStatus` events and shown in the control
panel. A failing platform never affects other platforms or the device.

## Built-in platforms

### `simulator`

Generates random tips with exponentially distributed gaps (`interval` = mean
seconds), amounts uniformly in `[min_tokens, max_tokens]`, and random usernames.
An optional `seed` makes it deterministic. Use it to test rules and the overlay.

### `chaturbate`

Uses the broadcaster **Events API**, an HTTP long-polling JSON API that needs no
browser scraping.

- Option `url` is the personal Events API URL the broadcaster generates on the
  site. It contains a secret token and is stored as `SecretStr`.
  `describe()` shows only the username part of the path.
- Each request adds `timeout=<seconds>` (default 10), so the server holds the
  request open until events arrive or the timeout expires.
- The response is `{"events": [...], "nextUrl": "..."}`. The next request goes to
  `nextUrl`, which acts as a cursor so no events are missed between polls.
- Only events with `method == "tip"` are used:
  `object.tip.tokens`, `object.tip.isAnon`, `object.tip.message`,
  `object.user.username`, and event `id` for de-duplication.
- HTTP 401/403/404 → fatal `PlatformError` (wrong URL/token). Other non-200 →
  transient.

Parsing lives in the pure function `parse_tips(payload)` so it can be unit
tested without the network.

Open question: whether the first request after (re)connecting can return tips
that were already processed in a previous run. If that shows up in practice, add
an option to skip the backlog of the first response.

### `bridge` (browser bridge) and `stripchat`

For sites **without** an official tip API. Stripchat is one: no public events,
tip or chat API was found (researched 2026-09). The bridge doesn't open its own
connection to the site (which would need credentials and a private protocol).
It uses the page the performer already has open in the browser. A userscript
(Tampermonkey) forwards data to a local HTTP endpoint run by the platform.

```text
browser tab (site page)                                estim-camming
┌──────────────────────────────────────┐   POST        ┌──────────────────────────────┐
│ page context: WebSocket subclass ────┼─┐ /frame      │ BridgePlatform 127.0.0.1:port│
│   (websocket mode)   postMessage+nonce │ or /tip     │  X-Bridge-Token, JSON only   │
│ userscript sandbox ◀───────────────────┘ ──────────▶ │  parse_frame() / parse_text()│
│   MutationObserver (dom mode)          │             │  dedupe by id → TipEvent     │
└──────────────────────────────────────┘               └──────────────────────────────┘
```

#### Modes

**`websocket`** (default for `stripchat`, requires a `parse_frame`
implementation):

- The hook wraps the getter of `MessageEvent.prototype.data` in the page. Each
  time the site reads a message whose `target` is a `WebSocket`, the hook sees
  the data once (`WeakSet` per event) and returns it unchanged. Unlike
  subclassing `WebSocket`, this also catches connections the page opened before
  the userscript ran. Tampermonkey on current Chrome (userScripts API) does not
  reliably run `document-start` scripts before the page's own scripts. This was
  observed in testing.
- The hook is installed in the page in two ways:
  1. **direct**: via `unsafeWindow` (`@grant unsafeWindow`,
     `@sandbox JavaScript`). It isn't affected by the site's Content Security
     Policy, and delivers frames by a direct function call.
  2. **script**: a `<script>` element (fallback, blocked by a strict CSP). It
     sends frames to the userscript with `window.postMessage`, tagged with a
     random per-page nonce. The userscript accepts only messages from the page's
     own origin carrying the nonce. (It can't compare `event.source` with
     `window`: with `@grant`, Tampermonkey's `window` is a sandbox proxy.) This
     path sets `data-estim-bridge="hooked"` on `<html>`.
  If both work, only direct-hook data is used. If neither installs, the badge
  says so. The badge counts text and binary frames seen, the quickest check
  that the hook receives the chat connection.
- The userscript POSTs frames to `/frame` (`{"frame": str}`, ≤ 200 000 chars).
- `StripchatPlatform.parse_frame` → `parse_stripchat_frame()`:
  - A frame is one JSON document or several newline-separated ones.
  - It searches the whole document (depth ≤ 12) for **message objects** with
    `type` ∈ {`tip`, `privateTip`} and a `details` object containing `amount`.
    It doesn't depend on the envelope (`subscriptionKey` / `params.message` in
    2022), so it survives wrapper changes.
  - `details.amount` → tokens (must be > 0), `details.isAnonymous`,
    `details.body` → message, `userData.username`, message `id` → `event_id`
    (a hash of the object when there is no id).
- Chat text arrives as `type: "text"` messages. A viewer therefore **cannot**
  fake a tip by typing text, which is the main reason this mode is the default.
- The same message seen twice (two tabs, reconnects) is de-duplicated by
  `event_id`.
- Provenance: the frame format and the hooking technique come from reading
  [mermaid-extension](https://github.com/prohetamine/mermaid-extension)
  (`src/webcam-sites/stripchat-script.js`, last updated 2022-12). No code was
  copied: that repository has no license. The format must be re-verified
  against the live site (user guide, step 4). If it has changed, record the new
  shape here and in the tests.

**`dom`** (fallback, and the only mode of the generic `bridge`):

- The userscript sends the whitespace-normalised text of each new element
  matching `tip_selector` to `/tip` (`{"id": str, "text": str}`), 250 ms after
  insertion so frameworks can fill in its text. Elements already present at
  start are skipped.
- `parse_text()` applies `tip_pattern` (named groups `amount`, optional `user`;
  commas allowed). The rest of the text becomes the message. `anonymous`/`anon`
  users become anonymous tips. Matched elements that don't parse are logged as
  warnings.
- With an empty `tip_selector` the script is in **discovery mode**: it forwards
  nothing and logs new elements containing digits, with a short CSS path, to the
  console.
- Risk: a selector that also matches normal chat lines lets viewers fake tips.

#### Common to both modes

- **Server**: aiohttp bound to 127.0.0.1 inside `stream()`. Every request must be
  `application/json` and carry `X-Bridge-Token` (constant-time compare). Web
  pages cannot send such requests to localhost without a CORS preflight, which
  is never answered. `/ping` returns the mode, so the userscript can detect a
  stale script. Only the endpoint of the configured mode exists.
- **`room`**: when set, the userscript forwards only while the first URL path
  segment equals it (case-insensitive, evaluated per event because the site is a
  single-page app). This stops tips from another room the performer is browsing
  during a show.
- **`ignore_first_seconds`**: nothing is forwarded shortly after page load.
- **Badge**: on-page status (connected / not reachable / wrong token / mode
  mismatch / hook blocked / not your room / last tip).
- **`GM_xmlhttpRequest`** is used because the site's CSP and mixed-content rules
  would block a normal `fetch` to `http://127.0.0.1`.
- **Generation**: `userscript()` fills the `bridge.user.js` template (`@match`
  lines, JSON `CONFIG`: site, mode, port, token, room, tipSelector,
  ignoreFirstSeconds, debug). `estim-camming userscript` writes
  `<site>.user.js` per enabled bridge platform. The script is **never served
  over HTTP**, because a page including it with `<script src>` could steal the
  token. Generated scripts in the repository root are git-ignored.
- **Options**: `BridgeOptions.mode` is `Literal["dom"]`. `StripchatPlatform.Options`
  widens it to `Literal["websocket", "dom"]` (default `websocket`). A new
  site-specific bridge that implements `parse_frame` does the same.

#### Known limitations

- The Stripchat frame format is undocumented. **Verified 2026-09-30** on a live
  room in Chrome with Tampermonkey (direct hook). The page had two chat
  connections, `wss://websocket-sp-v6.stripchat.com/connection/websocket` and
  `wss://websocket-extensions-v6.stripchat.com/connection/websocket` (Centrifugo
  endpoints). Tips were parsed with correct amounts, anonymous flags and message
  ids. It can change at any time. If Stripchat moves its chat to binary frames, a Web
  Worker or an iframe, the hook won't see it. `debug = true` shows what arrives.
- Tips only arrive while the tab is open.
- In dom mode, two tabs produce duplicate tips (different ids).

## Adding a platform

1. Create `src/estim_camming/platforms/<site>.py`:

   ```python
   @PLATFORMS.register("mysite")
   class MySitePlatform(Platform):
       """One-line description shown by `estim-camming plugins`."""

       class Options(PluginOptions):
           token: SecretStr = Field(description="API token from your MySite account.")

       async def stream(self) -> AsyncIterator[TipEvent]:
           async with aiohttp.ClientSession() as session:
               async with session.ws_connect(URL, headers=...) as ws:
                   async for msg in ws:
                       if tip := parse(msg):
                           yield tip
   ```

2. Import the module in `platforms/__init__.py`.
3. Keep payload parsing in a pure function and test it with recorded example
   payloads in `tests/test_platforms.py`.
4. Document the options in `docs/user-guide/configuration.md` and add a section
   here.

Preferred integration methods, from best to worst: official event/tip API or
webhook → official WebSocket → site "bot"/"app" framework → scraping the chat
(fragile, avoid).

Candidate sites for future adapters: BongaCams, CamSoda, MyFreeCams. Each needs
an investigation of the official integration options first. Sites without an API
can use the generic `bridge` platform.
