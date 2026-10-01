# Desktop control window (GUI)

`estim-camming gui` opens a compact Qt (PySide6) window with the same controls as
the web control panel. It is meant to be tiled next to OBS and the chat browser.

## Process model

```text
estim-camming gui  (Qt main thread)                 estim-camming run --exit-on-stdin-close
┌─────────────────────────────────┐   QProcess      ┌──────────────────────────────────┐
│ ControlWindow                   │──── stdin ─────▶│ stdin watchdog thread            │
│   ▲ stateChanged    │ commands  │◀─── stdout ─────│ (log lines → "Engine log")       │
│ ControlClient ──────┴───────────┼── HTTP ────────▶│ OverlayServer /api/state, /api/* │
│   (QNetworkAccessManager)       │  127.0.0.1:port │ Application → SafetyGuard → Device│
└─────────────────────────────────┘                 └──────────────────────────────────┘
```

- **The GUI is only a client** of the control API (see [Overlay](overlay.md)), like
  the web control panel. It never touches the device or the safety guard (S1).
  All limits and invariants stay in the engine.
- **The engine runs in a child process** (`EngineProcess`, a `QProcess` running
  `python -m estim_camming run -c <config> --exit-on-stdin-close`). A frozen or
  crashed window therefore never stalls the scheduler or the safety guard, and
  Qt (GIL, rendering) doesn't add jitter to output timing.
- **Lifetime coupling (stdin watchdog).** The GUI holds the engine's stdin. With
  `--exit-on-stdin-close`, a daemon thread in the engine reads stdin, and on EOF
  cancels the main task. That is the normal SIGTERM path: `guard.shutdown()`
  zeroes the device (S9). EOF happens when the GUI closes the pipe on purpose,
  and also when the GUI process exits or crashes, because the OS closes its file
  descriptors. So the engine never runs on unattended after its window is gone.
- **Closing the window**: stop polling → close stdin → wait up to 5 s → SIGTERM →
  wait 3 s → SIGKILL as a last resort, with a log line asking to check the device.
  `run_gui` calls `shutdown()` again after the Qt loop ends (idempotent).
  Ctrl+C / SIGTERM to the GUI close the window, and a 200 ms timer lets Python run
  signal handlers inside Qt's loop.
- **`--connect`** skips the child process and attaches to an already running
  engine. Closing the window then leaves that engine running. Without
  `--connect`, the GUI refuses to start if the control port is already in use,
  so it can never silently attach to a different instance while its own engine
  fails to bind.
- Requires `[overlay] enabled = true`, since the API is served by the overlay
  server. The address comes from the config: `0.0.0.0`/`::` map to
  `127.0.0.1`. `control_token` is sent as `X-Control-Token`.

## Client

`ControlClient` (`gui/client.py`) uses `QNetworkAccessManager` on the Qt event
loop, so no threads are involved:

- **State:** polls `GET /api/state` every 250 ms, with one request in flight at
  most and a 2 s timeout. It emits `stateChanged(dict)` and
  `connectionChanged(bool, detail)`. Polling instead of the WebSocket keeps the
  client stateless and self-healing across engine restarts. QtWebSockets isn't
  part of `PySide6-Essentials`, and the snapshot is small.
- **Commands:** `POST /api/{arm,stop,scale,skip,clear,tip}` with
  `Content-Type: application/json`, the token header, and a 3 s timeout. Every
  reply triggers an immediate poll. Failures emit `commandFailed` and show in the
  status bar and the engine log.

## Window

`ControlWindow` (`gui/window.py`), top to bottom, inside a scroll area so it
works in short tiles:

| Section | Contents |
|---|---|
| Status | ARMED / DISARMED / NOT CONNECTED / ENGINE STOPPED, connection state |
| Controls | Big STOP button, Arm (hidden while armed), master slider, "Keep window on top" |
| Output | One bar per device channel (actual post-safety level) |
| Now playing | Action label, time left, tipper and per-channel outputs, progress, queue, Skip/Clear |
| Tips | Session total, recent tips, platform status |
| Test tip | Username + tokens → `/api/tip` |
| Engine log | Collapsible stdout/stderr of the child process (only when the GUI started it). Opens by itself if the engine exits unexpectedly |

Details that matter for safety:

- **Keyboard stop:** an application-wide event filter turns Esc and Space into an
  emergency stop before any widget sees the key. Space still types in text and
  number fields. Auto-repeat is ignored.
- STOP and Arm have `NoFocus`, so Enter/Space can never activate Arm.
- The master slider sends on release (or on each keyboard/wheel step), and isn't
  overwritten by polls while it is being dragged.
- Usernames and messages are shown only in plain-text widgets (`QLabel` with
  `PlainText`, `QListWidget` items). No rich text, so no HTML injection from
  strangers.
- "Keep window on top" sets `WindowStaysOnTopHint`. On Wayland the compositor
  decides. KWin honours it, and other compositors may need a window rule.

## Desktop integration

- **Identity:** `QApplication.setDesktopFileName("estim-camming")` is called before
  the `QApplication` is created. It becomes the Wayland `app_id` and the X11
  `WM_CLASS`, so KDE no longer sees the window as "python3". Taskbars and KWin
  window rules can then match this window only (`model.APP_ID`).
- **Icon:** `gui/estim-camming.svg` is packaged and set with
  `QApplication.setWindowIcon`. With Qt ≥ 6.9 and KWin ≥ 6.2 the icon reaches the
  taskbar through the `xdg-toplevel-icon` protocol, even without a desktop file.
- **`estim-camming desktop-entry [-c config] [--remove]`** writes
  `$XDG_DATA_HOME/applications/estim-camming.desktop` (Exec = this Python
  interpreter `-m estim_camming gui -c <absolute config>`, `Path=` the config
  directory, `StartupWMClass=estim-camming`) and
  `icons/hicolor/scalable/apps/estim-camming.svg`. It validates the config
  first. Exec arguments are quoted per the Desktop Entry spec (double quotes,
  escaped `"` `` ` `` `$` `\`, `%%`). That was verified with GLib's parser and
  `desktop-file-validate`. It is never run implicitly, since it writes outside
  the project.
- **Always on top:** Wayland has no protocol for a client to keep itself on top,
  so `WindowStaysOnTopHint` has no effect there. On the `wayland` platform the
  checkbox is hidden and a hint points to a KWin window rule (matching the
  `app_id`), which the user guide describes. On X11 the checkbox sets the hint.

## Testing

- `tests/test_gui_model.py`: Qt-free helpers (addresses, formatting).
- `tests/test_engine_process.py`: real engine subprocess. Closing stdin with
  `--exit-on-stdin-close` shuts it down with an emergency stop; without the flag
  stdin is ignored.
- `tests/test_gui_model.py` also covers the desktop entry and Exec quoting, and
  `tests/test_desktop_entry_cli.py` covers install/remove with `XDG_DATA_HOME`
  in a temp dir.
- `tests/test_gui.py` (skipped without PySide6): offscreen Qt
  (`QT_QPA_PLATFORM=offscreen`) with a real engine process. It covers connect,
  arm, test tip, output bars, Esc stop, Space in a text field vs. elsewhere,
  close (engine exits via the watchdog), showing an engine crash, that the icon
  loads, and that the on-top checkbox is replaced by the KWin hint on Wayland.

## Extending

New controls go through the control API: add the route to `OverlayServer` and
the `Controller` protocol first (so web and desktop stay equal), then a method
on `ControlClient` and a widget in `ControlWindow`.
