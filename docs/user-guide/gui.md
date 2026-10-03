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
- **Edit rules…**: opens the rules editor (below).
- **User guide** (or **F1**): opens this guide in your web browser.
- **Test tip**: send a tip to try your tip menu.
- **Engine log**: estim-camming's messages. Opens by itself if something goes
  wrong.

## Editing the tip menu

**Edit rules…** opens a second window where you can change the tip menu while
estim-camming runs. No need to edit `config.toml` or restart.

- **The list** on the left shows the rules in the order they are checked. The
  **first rule that matches** a tip wins, so put specials (like exactly 69)
  above ranges that contain them (like 25–99). Drag rules, or use ▲/▼, to change
  the order.
- **+ Add**, **Duplicate** and **Delete** do what they say.
- **The form** on the right edits the selected rule: name, label (the text in
  the overlay menu), tokens (an exact amount or a range), time (fixed, random, or
  longer for bigger tips), and the output: pattern, its options and intensity.
  Choose **Per channel** to give A and B different patterns or intensities.
- **Intensity** is relative to your safety maximum: the line under it shows what
  it means on the device, for example "≈ A 30%". The editor can't raise your
  safety limits; those stay in `config.toml`.
- **Preview** draws the output over the rule's time. A shaded band means it
  varies per tip (random patterns).
- **Try** shows which rule a tip amount would trigger, without sending a tip.
- **Problems** under the list: ⛔ errors must be fixed before saving. ⚠ warnings
  (for example a rule that can never match because an earlier rule takes all
  its amounts) and ℹ notes can be saved anyway.
- **Save** (Ctrl+S) applies the rules **at once**, also while armed: the next
  tip uses them. Whatever is playing or queued keeps its old settings. Save also
  writes the rules to `config.toml`, so they are there after a restart. The
  first save keeps a copy of your old file as `config.toml.bak`.
- **Revert** throws your changes away and loads the current rules again.
- **Help** (or **F1**) opens this section of the guide in your browser.

Good to know:

- Comments you wrote **between** the `[[rules]]` in `config.toml` are removed
  when you save from the editor. Comments elsewhere in the file are kept.
- If you edited `config.toml` by hand while estim-camming was running, the
  editor won't overwrite it. Your new rules are still used, but the status line
  says they were **not saved**. Restart estim-camming to load your hand edits.
- If the rules were changed somewhere else meanwhile (another window), an orange
  bar lets you reload them or overwrite them with yours.
- **Esc** stops the output in this window too. **Space** also stops, unless you
  are typing in a text or number field.

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

## The user guide

**User guide** and **F1** open this guide in your default web browser. If you
built the documentation yourself (`sphinx-build -b html docs docs/_build/html`
in the project folder), that copy is opened, because it matches your version of
estim-camming. Otherwise the online guide is opened:
<https://cb-stimmer.github.io/estim-camming/>.

## If something goes wrong

- **"error: something is already running on 127.0.0.1:8765"**: estim-camming is
  already running. Use `estim-camming gui --connect`, or stop the other one first.
- **"the GUI needs PySide6"**: run `pip install -e '.[gui]'`.
- **NOT CONNECTED** for more than a few seconds after starting: open
  **Engine log**. It usually shows a configuration problem.
- **ENGINE STOPPED**: estim-camming quit unexpectedly. The log shows why. Close
  the window and start again.
