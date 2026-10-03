# CLAUDE.md

## What this project is

**estim-camming** is a Python (≥3.11, asyncio) application that turns tips on
webcam streaming sites into output on a device (for example a two-channel e-stim
unit). It shows the tip menu, the current action and the queue in an OBS overlay,
and gives the performer a control panel with an emergency stop.

Streaming sites (**platforms**), **devices** and waveform **patterns** are
plugins. The core does not know about any specific site or device.

## Design documentation (read before changing things)

All design docs are Markdown in `docs/design/`, and they are the source of truth
for interfaces and invariants:

- [docs/design/architecture.md](docs/design/architecture.md): components, data flow, concurrency, package layout
- [docs/design/safety.md](docs/design/safety.md): **safety invariants S1–S12, which must never be broken**
- [docs/design/plugins.md](docs/design/plugins.md): registries, `Options` models, entry points
- [docs/design/platforms.md](docs/design/platforms.md): `Platform` contract, supervision, Chaturbate, browser bridge / Stripchat, adding a site
- [docs/design/devices.md](docs/design/devices.md): `Device` contract, 0..1 level model, adding a device
- [docs/design/rules-and-patterns.md](docs/design/rules-and-patterns.md): tip menu matching, scheduler, patterns
- [docs/design/events.md](docs/design/events.md): event types and bus semantics
- [docs/design/overlay.md](docs/design/overlay.md): web routes, WebSocket protocol, OBS overlay
- [docs/design/gui.md](docs/design/gui.md): desktop control window, engine child process, stdin watchdog
- [docs/design/configuration.md](docs/design/configuration.md): config schema and validation stages
- [docs/design/decisions.md](docs/design/decisions.md): ADR log (append-only)

The user guide is in `docs/user-guide/` (end-user language, not internals).

## Commands

```sh
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # tests
.venv/bin/ruff check src tests && .venv/bin/ruff format src tests
.venv/bin/sphinx-build -W --keep-going -b html docs docs/_build/html   # docs (warnings are errors)
.venv/bin/estim-camming init && .venv/bin/estim-camming run            # simulator + dummy device
.venv/bin/estim-camming check -c config.toml       # validate config without connecting
.venv/bin/estim-camming plugins                    # list plugins and their options
.venv/bin/estim-camming userscript                 # write <site>.user.js for bridge platforms
.venv/bin/estim-camming gui                        # desktop control window (starts the engine too)
.venv/bin/estim-camming desktop-entry              # menu entry + icon in ~/.local/share (--remove)
```

Control panel: http://127.0.0.1:8765/control. Overlay: http://127.0.0.1:8765/overlay.

## Code map

```text
src/estim_camming/
  app.py          Application: builds everything, supervises platforms, Controller API, snapshot()
  safety.py       SafetyGuard: the ONLY component that talks to the device
  scheduler.py    queue + tick loop (pattern x intensity -> guard.set_levels)
  rules.py        Rule (pydantic) + RuleEngine (first match wins)
  patterns.py     Pattern base + constant/pulse/ramp/wave
  plugins.py      Plugin, PluginOptions, Registry, PLATFORMS/DEVICES/PATTERNS
  bus.py events.py actions.py config.py cli.py
  platforms/      base.py, simulator.py, chaturbate.py (Events API),
                  bridge.py + bridge.user.js (userscript bridge: websocket/dom modes),
                  stripchat.py (bridge + WebSocket frame parser)
  devices/        base.py, dummy.py, estim2b.py (E-Stim 2B via estim2py 0.4.1 fork from git; 2.106 + beta firmware)
  overlay/        server.py (aiohttp), static/ (overlay.html, control.html, common.js)
  gui/            PySide6 control window: client of the control API; engine as child process
  example_config.toml   shipped example; `init` writes it; a test keeps it valid
tests/            pytest (asyncio_mode=auto)
docs/             Sphinx + MyST; design/ and user-guide/ are Markdown
```

Dependencies live only in `pyproject.toml`; `requirements*.txt` just install the
package editable (`requirements-dev.txt` adds the `dev,docs,gui,estim2b` extras so
all tests run; GUI and 2B serial tests skip without them).

## Rules for working in this repo

- **Safety is non-negotiable.** Never add a path to the device that bypasses
  `SafetyGuard`, never make defaults less safe (`start_armed=false`, low
  `max_level`), and keep the emergency stop instant. Any change touching output
  needs tests in `tests/test_safety.py` / `tests/test_scheduler.py`.
- **Keep docs in sync.** If you change an interface, invariant, event, route or
  config field, update the matching `docs/design/*.md` page (and the user guide
  and `example_config.toml` for user-visible config) in the same change. Record
  significant design choices as a new ADR in `docs/design/decisions.md`.
- **Plugins stay isolated.** A plugin gets only its validated `Options`. No global
  config, bus or other components. New built-ins must be imported in their
  package `__init__.py`. Hardware and site libraries go in optional dependencies.
- **Async only.** No blocking I/O on the event loop (use `asyncio.to_thread`).
- **Secrets.** Platform tokens and URLs use `SecretStr` and must never be logged;
  `describe()` must be safe to display. `config.toml` is git-ignored.
- **Untrusted input.** Usernames and tip messages come from strangers. Always
  escape them in HTML (`escapeHtml`).
- **Don't invent protocols.** When adding a platform or device, base it on
  official API docs or published protocol specs. If unsure, say so and leave a
  documented open question instead of guessing.
- Style: ruff (line length 100), type hints, pydantic v2 models with
  `extra="forbid"`, frozen dataclasses for events.
