# Stripchat setup

Stripchat has no public API for tips. Instead, estim-camming reads tips from your
own Stripchat page in the browser:

```text
your Stripchat page ──userscript──▶ estim-camming (127.0.0.1:8766) ──▶ tip menu
```

A small **userscript** runs in your browser via the free
[Tampermonkey](https://www.tampermonkey.net/) extension. It listens to the chat
connection your Stripchat page already has open. When Stripchat reports a tip,
the userscript passes it to estim-camming on your own computer. It doesn't use
or store your Stripchat password.

Tips are recognised from Stripchat's own **tip data**, not from chat text. A
viewer typing "bob tipped 1000" in chat is an ordinary chat message and never
triggers anything.

:::{important}
Stripchat doesn't document this data, and it can change. The format used here
comes from an older open-source project. Before your first show, check that tips
come through (step 4). Check again if they stop after a Stripchat update.
:::

## 1. Configure

Add to `config.toml`:

```toml
[[platforms]]
type = "stripchat"
[platforms.options]
token = "a-long-random-string-only-you-know"   # 16+ characters
room = "your_stripchat_username"
```

- `token`: choose your own random string. It stops other web pages from sending
  fake tips to the app.
- `room`: your Stripchat username. Tips are only forwarded while the address of
  the page is `stripchat.com/<room>...`, so browsing someone else's room during
  your show never triggers your device. Leave it empty while testing in other
  rooms (step 4). If your own broadcast page has a different address, leave it
  empty and don't browse other rooms while armed.

## 2. Install Tampermonkey and the userscript

1. Install the Tampermonkey extension for your browser. **In Chrome (and other
   Chromium browsers), also allow it to run scripts:** open
   `chrome://extensions` → Tampermonkey → **Details** and turn on **Allow User
   Scripts**. On older versions, turn on **Developer mode** at the top right of
   `chrome://extensions` instead. Without this, Tampermonkey lists the script as
   active but it never runs: there's no badge, and nothing appears in the console.
2. Generate the userscript from your config:

   ```sh
   estim-camming userscript
   ```

   This writes `stripchat.user.js`.
3. In Tampermonkey: **Dashboard → Utilities → Import from file**, select
   `stripchat.user.js`, and confirm the installation. Tampermonkey asks for
   permission to connect to `127.0.0.1` and for page access (`unsafeWindow`).
   Allow both.

Run `estim-camming userscript` and re-import after **every** change to the
Stripchat options in `config.toml`.

## 3. Start

1. Start estim-camming (`estim-camming run`). The output stays **disarmed**.
2. Open (or reload) a Stripchat page. A badge at the bottom-left should say
   *estim bridge (websocket): connected · frames: N*. The frame count should go
   up while the chat is active; it shows the script sees the chat connection.

## 4. Test

1. Temporarily set `room = ""` (and regenerate/re-import), then open a busy room
   where people are tipping.
2. When someone tips, the app log shows `tip 25 from someuser (stripchat) -> ...`
   and the badge shows the last tip. While disarmed, tips are logged but produce
   no output.
3. Compare a few tips with what the Stripchat chat shows.
4. Set `room` back to your username, regenerate and re-import the userscript.
5. Then go to **your own** broadcast page, arm, and follow
   [Safety first](safety.md#before-every-show).

### If no tips come through

- **No badge at all:** the script doesn't run. Check **Allow User Scripts** (step 2)
  and that the script is enabled in Tampermonkey, then reload the page.
- Badge says **could not hook the page**: the browser blocked the script that
  listens to the chat connection. Try the [fallback mode](#fallback-dom-mode).
- Badge shows **frames: 0** while the chat is moving: the chat connection isn't
  visible to the script. Report this, or try the fallback mode.
- Badge is green and frames are counted, but tips never show up: set
  `debug = true`, then regenerate and re-import. The browser console
  (**F12 → Console**) then shows every chat frame (`[estim-bridge] frame from ...`),
  and marks frames that look like tips (`TIP CANDIDATE`, `POSSIBLE TIP`). Those are
  also sent to the app, which logs `frame without a recognised tip: ...` when it
  can't read one. That usually means Stripchat has changed its format. Report it
  with that log line (remove usernames), or use the fallback mode.
- The console only shows frames with `debug = true`. Without it, check the app
  log or the control panel for tips.

## Fallback: dom mode

If the default mode stops working, the userscript can instead read tip messages
from the page itself. This is less safe: you must make sure it only matches real
tip notifications.

```toml
[platforms.options]
mode = "dom"
tip_selector = ""        # filled in below
```

1. With `tip_selector` empty, the userscript runs in **discovery mode**. It logs
   new page elements to the browser console, for example:

   ```text
   [estim-bridge] "someuser tipped 25 tk"
       div.messages > div.message.tip-message > span.text
   ```

2. Pick a CSS class that **only** tip notifications have. You can also
   right-click a tip in the chat → **Inspect**. Then set it, for example
   `tip_selector = ".tip-message"`. That class name is only an example: use what
   you see on the real page.
3. Check that `tip_pattern` matches the tip text. The default expects
   `<username> tipped <amount>`. The app log shows
   `tip element did not match tip_pattern: '...'` when it doesn't.

:::{warning}
In dom mode, if the selector also matches normal chat messages, a viewer could
type `bob tipped 1000` and trigger your device. Test with normal chat messages
too.
:::

## Good to know

- The userscript only works while the Stripchat tab is open. Keep your broadcast
  page open during the show.
- Tips in the first 5 seconds after the page loads are ignored
  (`ignore_first_seconds`).
- In the default mode, the same tip seen in two tabs is counted once. In dom
  mode it is counted twice, so keep one tab open.
- To run another bridge (for example a second site) at the same time, give it a
  different `port`.
