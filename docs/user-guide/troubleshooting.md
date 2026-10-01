# Troubleshooting

Run with `-v` for detailed logs: `estim-camming -v run`.

**`error: config file not found`**
: Create one with `estim-camming init`, or pass the path with `-c`.

**`error: ... Extra inputs are not permitted`**
: A setting is misspelled or in the wrong section. Compare with
  [Configuration](configuration.md) or `estim-camming plugins`.

**`unknown platform/device/pattern 'x' (available: ...)`**
: The `type` or `pattern` name is wrong, or the plugin package isn't installed.

**Tips appear but nothing happens**
: The output is probably **disarmed**. The control panel shows the status, and
  the log says `rejected '...': output disarmed`. Press **Arm output**. Also
  check that a rule matches the amount (`no matching rule` in the log) and that
  the master level slider isn't at 0%.

**The output turned itself off**
: An emergency stop happened. The log says why (`EMERGENCY STOP (device error)`,
  for example). Fix the cause, then arm again.

**Platform shows "disconnected"**
: The app retries automatically, waiting up to 60 seconds between attempts. For
  Chaturbate, `HTTP 401/403/404 ... check the URL/token` means the Events API URL
  is wrong or was regenerated: paste the new one and restart.

**Stripchat: no badge on the page**
: Tampermonkey isn't allowed to run scripts. In `chrome://extensions` →
  Tampermonkey → Details, turn on **Allow User Scripts** (or **Developer mode**
  on older Chrome), then reload the page.

**Stripchat: badge says "app not reachable"**
: estim-camming isn't running, or `port` in the config differs from the one in the
  installed userscript. Run `estim-camming userscript` and re-import it.

**Stripchat: badge says "wrong token"**
: The token in the userscript doesn't match `config.toml`. Regenerate and
  re-import the userscript.

**Stripchat: badge says "not your room, ignoring"**
: The page address doesn't match `room`. Tips are only forwarded from your own
  room. Check the spelling, or leave `room` empty.

**Stripchat: no tips arrive**
: Check that the Stripchat tab is open and the badge is green. See
  [Stripchat setup: if no tips come through](stripchat.md#if-no-tips-come-through).

**`tip element did not match tip_pattern`** (dom mode)
: The userscript found a tip message, but its text doesn't match `tip_pattern`.
  The log shows the exact text, so adjust the pattern to it.

**`address already in use` at start-up**
: Another program (or a second copy of estim-camming) uses port 8765 (overlay)
  or 8766 (Stripchat bridge). Change `overlay.port` (and the OBS source URL) or
  the bridge `port` (and regenerate the userscript).

**Control panel buttons do nothing**
: If `control_token` is set, open the panel with `?token=...` in the URL. The
  browser's developer console shows the error.
