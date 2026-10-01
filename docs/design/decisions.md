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
stays on 3.11+. Faults surface on the next `set_levels()` rather than inside it,
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
