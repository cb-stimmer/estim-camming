# Decision log

Short architecture decision records. Append new ones and don't rewrite old ones.
If a decision is reversed, add a new record that supersedes it.

## ADR-001: Python with asyncio, single process

**Decision.** Python ≥ 3.11 with one `asyncio` event loop and `TaskGroup`.
**Why.** All work is I/O: HTTP long-polling, WebSockets, BLE/serial. `asyncio`
handles it without threads or locks around shared state. 3.11 provides
`TaskGroup` and `tomllib`.
**Consequence.** Plugins must not block, and must use `asyncio.to_thread` for
blocking libraries.

## ADR-002: Plugins via registries and entry points

**Decision.** Platforms, devices and patterns are classes registered by name in a
`Registry`. Built-ins use a decorator, and external packages use entry points.
**Why.** New sites and devices can be added, even by third parties, without
changing the core, and the config selects them by name.

## ADR-003: Normalised levels 0..1

**Decision.** Everything above the device plugin works with channel levels in
0..1 of full scale. Rule intensities are relative to the configured safety
maximum.
**Why.** Rules and patterns stay device-independent. The performer tunes one
number (`max_level`) per session or device instead of rewriting the tip menu.

## ADR-004: SafetyGuard as the single gate to the device

**Decision.** A single object enforces all limits and is the only caller of the
device.
**Why.** Safety logic in one small, heavily tested place cannot be bypassed by
new features. See [Safety](safety.md).

## ADR-005: Disarmed rejects tips instead of queueing them

**Decision.** While disarmed, new actions are rejected (and the queue is cleared
on stop).
**Why.** Otherwise arming after a pause would release a burst of saved-up
actions.

## ADR-006: Overlay as local web pages over a WebSocket

**Decision.** The overlay is HTML served by the app and used as an OBS Browser
Source. Every message carries a full state snapshot.
**Why.** OBS supports Browser Sources natively, styling is plain CSS, and full
snapshots make clients stateless and self-healing. State is small, so the cost is
negligible on localhost.

## ADR-007: TOML config validated by pydantic

**Decision.** One TOML file, pydantic models with `extra="forbid"`, and full
validation (including plugins and rules) before anything connects.
**Why.** TOML is readable for non-programmers and in the standard library.
Config mistakes surface before going live, not during a show.

## ADR-008: Documentation as Markdown in Sphinx (MyST)

**Decision.** Design docs and user guide are Markdown files under `docs/`, built
with Sphinx and `myst-parser`. API reference comes from docstrings (autodoc).
**Why.** Markdown is easy to write and review, and tools (including AI
assistants) can read it directly from the repository. Sphinx provides a
navigable site and API docs.

## ADR-009: Browser bridge for sites without a tip API

**Decision.** For sites without an official tip API (first: Stripchat), a
userscript on the performer's own page forwards the text of tip elements to a
local `bridge` platform. The text is parsed in Python with a configurable regex.
**Why.** Reverse-engineering a site's private WebSocket protocol is fragile and
may break its terms. The page the performer already has open is a stable
source that needs no credentials. Parsing in Python keeps the site-specific
logic testable and configurable without editing JavaScript.
**Consequence.** The user must configure a CSS selector per site, and re-check it
after site redesigns. Tips arrive only while the tab is open.

## ADR-010: Stripchat reads structured WebSocket data, not page text

**Decision.** The `stripchat` bridge defaults to `websocket` mode. A script
injected into the page subclasses `WebSocket`, forwards incoming frames, and
Python extracts messages of type `tip`/`privateTip`. The ADR-009 text/selector
approach remains as `dom` mode.
**Why.** With text parsing, a selector that also matches chat lines lets
viewers fake tips ("bob tipped 1000"). Structured messages come from the site's
server with an explicit type, so chat text can't pose as a tip. It also removes
the per-user selector setup and allows de-duplication by message id. The
technique and format were learned from mermaid-extension. Its code was not
used: no license, unmaintained since 2022, Manifest V2, and it injects into
every site.
**Consequence.** Depends on an undocumented frame format, so the parser searches
for tip objects anywhere in a frame. The user guide includes a verification
step and a `debug` switch.

## ADR-011: E-Stim 2B via estim2py in a worker thread

**Decision.** Support the E-Stim Systems 2B through the estim2py library (serial),
imported lazily, with all calls in a worker thread and a coalescing writer
(latest target wins, unchanged channels not re-sent).
**Why.** The user asked for estim2py. It is public domain and wraps the
documented 2B serial protocol, so we don't write our own. Its calls block for
at least `delay` plus the reply, about 10 commands/s, while the scheduler
produces up to 20 updates/s per channel. Queueing every update would make the
box lag further and further behind; coalescing keeps it current.
**Consequence.** estim2py 0.3.0 needs Python 3.13+, so the extra pins 0.2.2 on
older Pythons (same API; we clear the serial input buffer ourselves). The core
stays on 3.11+. (Superseded for the library version by ADR-014.) Faults surface on the next `set_levels()` rather than inside it,
bounded by `stall_timeout`.

## ADR-012: Actions carry per-channel outputs

**Decision.** An `Action` holds a tuple of `ChannelOutput(channel, pattern,
params, intensity)` instead of a single pattern/intensity plus a channel list.
Rules can give each channel its own settings (`[rules.channels.A]`), inheriting
unset fields from the rule.
**Why.** Performers want A and B to do different things per tip (another rhythm
on each channel, a weaker channel). Resolving per-channel settings once in
`Rule.outputs()` keeps the scheduler simple (one pattern per channel) and
validates everything at start-up. The old `channels = ["A"]` form still works.
**Consequence.** The action JSON in overlay/WebSocket messages has `outputs`
instead of `pattern`/`intensity`/`channels`. The bundled pages never used those
fields.

## ADR-013: Desktop GUI as an API client that owns an engine child process

**Decision.** The optional desktop window (PySide6) talks to the existing
control API, polling `/api/state`. `estim-camming gui` starts the engine as a
child process with `--exit-on-stdin-close`. `--connect` attaches to a running
engine instead.
**Why.** The user wanted a window to tile next to OBS and chat. Reusing the
control API keeps one control path and keeps all safety logic in the engine. A
separate process isolates output timing from the UI, and the stdin pipe ties the
engine's life to the window's on every OS, crashes included, without
platform-specific parent-death signals. Qt was chosen over Tkinter for native
Wayland/KDE support. Polling instead of the WebSocket because QtWebSockets is
not in `PySide6-Essentials`.
**Consequence.** The display updates at about 4 Hz (commands are immediate). The
GUI needs the overlay server enabled. PySide6 is an optional extra (`[gui]`,
about 230 MB).

## ADR-014: estim2py from the user's fork, modes resolved per firmware

**Decision.** The `estim2b` extra installs estim2py 0.4.1 from the user's fork
(`git+https://github.com/cb-stimmer/estim2py@v0.4.1`) instead of PyPI's
0.2.2/0.3.0. The plugin keeps the 2.106 and beta mode tables and picks the mode
number for the firmware the library detects.
**Why.** The fork works on Python 3.12 again and adds the protocols of the
2B beta firmwares (2.119B, 2.120B+), which have longer status lines and
renumbered modes. Sending a 2.106 mode number to a beta box would select a
different mode (12 is `step` on 2.106 but `cycle` on beta).
**Consequence.** The extra is a direct git reference, so the package can't be
uploaded to PyPI as is (`allow-direct-references`), and installing it needs git
and network access to GitHub. Python 3.11 installs the core without estim2py;
the 2B device then reports the missing library. Beta-only features (dynamic
power, bias, warp, ramp, output map) are not exposed yet (dynamic power, warp
and ramp: see ADR-015).

## ADR-015: 2B beta options: dynamic power, bias, warp and ramp

**Decision.** The `estim2b` device gets `power = "dynamic"` with an optional
`bias` (`A`, `B`, `average`, `max`), `warp` (×1–×32) and `ramp` (×1–×4). They are set once at connect and checked in the status reply,
like power and mode. A configured option the firmware lacks stops the connect
after `kill`. Unset `warp`/`ramp` leave the box's setting alone. The default
power stays `low`.
**Why.** The user asked for them. The fork documents the commands (`Y`, `Qn`,
`Wn`, `Rn`) and which firmwares support them. Dynamic power merges low and high
based on the bias, and `Y` resets the bias to a raw 0 that means `max` on
2.120B+ but `A` on 2.119B. Setting the bias by name gives the same strength on
both firmwares. Warp and ramp take the multipliers
shown on the box rather than raw indices, so a config value means the same thing
as the box's display.
**Consequence.** Dynamic power can reach high-power strength, so the docs tell
users to treat it like `high`. `bias` without dynamic power is a config error.
What warp and ramp do exactly is still an open question in
[devices.md](devices.md).

## ADR-016: Rules editor: the engine applies and saves, rules spliced into config.toml

**Decision.** A desktop rules editor edits a draft and sends it to new control
API routes (`GET /api/rules`, `POST /api/rules/check`, `POST /api/rules`). One
**Save** applies the rules to the running engine at once (an atomic swap of the
`RuleEngine`) and writes them to `config.toml`. Saving is allowed while armed;
it affects only tips that arrive afterwards. The engine, not the GUI, writes the
file: it replaces only the `[[rules]]` tables (text splice of a block generated
with `tomlkit`), checks that everything else reads back unchanged, keeps a
`.bak`, writes atomically, and refuses to overwrite a file that was changed by
hand since it was loaded. Revisions make concurrent edits fail with 409 instead
of overwriting each other. Comments between rules are not kept.
**Why.** The user wanted to change the tip menu without editing TOML and
restarting. They chose a single Save that applies directly over separate
Apply/Save buttons (the running rules and the file never differ), allowing it
while armed (no need to stop the show for a typo), losing comments inside
`[[rules]]` (simpler than a separate rules file), and `tomlkit` as a new core
dependency. Keeping the engine as the only writer means the same behaviour with
`gui --connect` and a future web editor, and one place for validation (the GUI
runs the same `check_rules` locally only for instant feedback). Splicing text
instead of editing the tomlkit document keeps comments that sit after the last
rule with the section they describe.
**Consequence.** New invariant S13 (rule changes are all-or-nothing and
forward-only, and can't touch safety settings). `GET /api/rules` needs the
control token because it reveals hidden rules. New `RulesChanged` event and
`rules_revision` in the snapshot. Hand-written comments between rules disappear
on the first save, and `config.toml.bak` keeps the original.

## ADR-017: Manual on GitHub Pages, opened in the browser from the GUI

**Decision.** The Sphinx docs are built and published to GitHub Pages by a
GitHub Actions workflow on every push to `main`. The GUI opens the manual in the
default browser (User guide / Help buttons, context-sensitive F1), preferring a
local `docs/_build/html` build over the published site.
**Why.** The user asked for both. A browser shows the Sphinx theme, search and
navigation as built, and needs nothing beyond `PySide6-Essentials` (an embedded
viewer would need QtWebEngine from `PySide6-Addons`, while `QTextBrowser` can't
render the theme). The local build matches the running code; the published site
works without a build.
**Consequence.** Pages must be enabled once in the repository settings (Source:
GitHub Actions). The published site follows `main`, so it can describe a newer
version than an older checkout without a local build. The CI build uses
`-W`, so a docs warning fails the deployment, as it fails a local build.

