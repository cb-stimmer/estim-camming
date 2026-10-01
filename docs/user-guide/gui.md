# Control window

Instead of the control page in the browser, you can use a separate desktop
window. It is narrow and tall, so it fits next to OBS and your chat browser
when you tile them.

## Install

The window uses Qt (PySide6), which is an extra download of about 230 MB:

```sh
pip install -e '.[gui]'
```

## Start

```sh
estim-camming gui
```

This starts estim-camming **and** opens the window. You no longer need a
separate `estim-camming run`. Closing the window stops everything and turns the
device off. The device is also stopped if the window crashes.

The OBS overlay keeps working as before (`http://127.0.0.1:8765/overlay`), and
so does the browser control page. You can use both at the same time.

If estim-camming is already running (for example started with
`estim-camming run`), connect the window to it instead:

```sh
estim-camming gui --connect
```

Closing the window then leaves estim-camming running.

## The window

- **Status** at the top: **ARMED** (green), **DISARMED**, **NOT CONNECTED** or
  **ENGINE STOPPED** (red).
- **STOP**: emergency stop. Disarms and clears the queue. **Esc** or **Space**
  does the same while the window is focused (Space still types in the test tip
  fields).
- **Arm output**: only shown while disarmed. The keyboard can't press it, which
  prevents arming by accident.
- **Master**: lowers all output, like the slider in the web panel.
- **Output**: the actual level of each channel.
- **Now playing**: what is playing, for whom, what each channel does, time left,
  and the queue. With **Skip current** and **Clear queue**.
- **Tips**: tokens this session, recent tips, and whether your platforms are
  connected.
- **Test tip**: send a tip to try your tip menu.
- **Engine log**: estim-camming's messages. Opens by itself if something goes
  wrong.

## Menu entry and taskbar icon (optional)

To start the window from the application menu (and pin it to the taskbar), run
once from your project folder:

```sh
estim-camming desktop-entry
```

This adds an "estim-camming" entry with its icon to your menu. It starts the
window with **this** folder's `config.toml` (use `-c` for another file). To
remove it again: `estim-camming desktop-entry --remove`.

The window shows its own icon in the taskbar even without this entry.

## Keep the window on top

On **KDE Plasma (Wayland)** an application can't keep itself on top; only KWin
can. The window identifies itself as **estim-camming**, so a rule affects only
this window, not other Python programs:

1. Start the window. Right-click its title bar → **More Actions** →
   **Configure Special Window Settings…**.
2. KWin fills in the window class `estim-camming` by itself. Leave the matching
   on **Exact Match**.
3. Click **Add Property…** → **Keep above other windows**, set it to **Force**
   and **Yes**, then **OK**.

From then on the window always stays on top, also after restarts. You can
change the rule later in System Settings → Window Management → Window Rules.

On X11 desktops the window shows a **Keep window on top** checkbox instead.

## Tiling tips

- KDE: drag the window to a screen edge, or use **Meta+←/→** to tile it. The
  window can be as narrow as 300 pixels and scrolls when it is short. STOP stays
  at the top.
- Put the window where you can reach STOP quickly, for example between OBS and
  the chat.

## If something goes wrong

- **"error: something is already running on 127.0.0.1:8765"**: estim-camming is
  already running. Use `estim-camming gui --connect`, or stop the other one first.
- **"the GUI needs PySide6"**: run `pip install -e '.[gui]'`.
- **NOT CONNECTED** for more than a few seconds after starting: open
  **Engine log**. It usually shows a configuration problem.
- **ENGINE STOPPED**: estim-camming quit unexpectedly. The log shows why. Close
  the window and start again.
