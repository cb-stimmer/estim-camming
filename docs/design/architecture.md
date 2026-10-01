# Architecture

## Goals

1. **React to tips.** Turn tips from one or more streaming sites into device output
   according to a configurable tip menu.
2. **Modularity.** Adding a streaming site, a device or a waveform pattern is a
   single class, without touching the core. Third parties can ship plugins as
   separate Python packages.
3. **Safety.** The performer is always in control: output starts disarmed, is
   capped and soft-started, and can be stopped instantly. See [Safety](safety.md).
4. **Show it on stream.** Viewers see the tip menu, what is playing and the queue
   in an OBS overlay.

## Non-goals

- Hosting or streaming video. OBS does that; we only provide a Browser Source.
- Remote (internet) control by viewers other than through tips on the platform.
- Running on a server away from the performer. The app runs on the streaming PC.

## Components

| Component | Module | Responsibility |
|---|---|---|
| Platform plugins | `estim_camming.platforms` | Connect to a site, yield `TipEvent`s |
| Event bus | `estim_camming.bus` | In-process pub/sub between components |
| Rule engine | `estim_camming.rules` | Map a tip to an `Action` (tip menu) |
| Scheduler | `estim_camming.scheduler` | Queue actions; play one at a time, tick by tick |
| Patterns | `estim_camming.patterns` | Waveform shape of an action over time |
| Safety guard | `estim_camming.safety` | Enforce limits; the *only* caller of the device |
| Device plugins | `estim_camming.devices` | Translate normalised levels to hardware commands |
| Overlay server | `estim_camming.overlay` | OBS overlay, control panel, WebSocket, control API |
| Application | `estim_camming.app` | Build everything from config, run and supervise tasks |
| Desktop GUI | `estim_camming.gui` | Optional Qt control window; client of the control API, starts the engine as a child process ([GUI](gui.md)) |
| CLI | `estim_camming.cli` | `run`, `gui`, `check`, `init`, `userscript`, `plugins` commands |

## Data flow

```text
 ┌────────────┐ TipEvent  ┌──────────┐  TipEvent  ┌────────────┐ Action ┌───────────┐
 │ Platform(s)│──────────▶│ EventBus │───────────▶│ dispatcher │──────▶│ Scheduler │
 └────────────┘ (publish) └──────────┘            │ + RuleEngine│       └─────┬─────┘
       ▲                       │ all events       └────────────┘             │ levels per tick
       │ reconnect             ▼                                              ▼
 ┌─────┴──────┐         ┌──────────────┐  arm / stop / scale / tip   ┌─────────────┐
 │ supervisor │         │OverlayServer │◀───────────────────────────▶│ SafetyGuard │
 └────────────┘         │ /overlay /ws │     (Controller API)        └──────┬──────┘
                        │ /control/api │                                    │ set_levels / stop
                        └──────────────┘                                    ▼
                                                                      ┌──────────┐
                                                                      │  Device  │
                                                                      └──────────┘
```

1. Each enabled platform runs in its own task, wrapped by a **supervisor** that
   reconnects with exponential back-off (1 s → 60 s). Tips are published on the bus.
2. The **dispatcher** (in `Application`) consumes `TipEvent`s from an unbounded
   subscription, records them for the overlay, asks the **RuleEngine** for an
   `Action` and enqueues it on the **Scheduler**.
3. The **Scheduler** plays one action at a time. Every tick (default 20 Hz) it
   evaluates the action's **pattern**, multiplies by the action intensity and
   sends per-channel levels to the **SafetyGuard**.
4. The **SafetyGuard** applies caps, the live master scale and the soft-start rate
   limit, then calls the **Device**. It publishes `LevelsChanged` and
   `SafetyStateChanged` events.
5. The **OverlayServer** forwards every bus event, together with a fresh state
   snapshot, to connected WebSocket clients (overlay and control pages). Control
   requests go to the `Application`, which implements the `Controller` protocol.

## Concurrency model

- Single process, single `asyncio` event loop. No threads.
- `Application.run()` uses an `asyncio.TaskGroup`: scheduler, dispatcher, one
  supervisor per platform, and the overlay server. If a task fails unexpectedly
  the whole group is cancelled, and the `finally` block always runs
  `SafetyGuard.shutdown()` (device to zero) and `Device.disconnect()`.
- `SIGINT` (Ctrl+C) and `SIGTERM` both cancel the main task, so the device is
  stopped on the way out.
- The safety guard serialises device access with an `asyncio.Lock`, so an
  emergency stop from the control panel never interleaves with a scheduler tick.
- Blocking I/O is not allowed on the event loop. A device plugin that needs a
  blocking library must use `asyncio.to_thread`.

## Package layout

```text
src/estim_camming/
  app.py            Application: wiring, supervision, Controller API
  cli.py            command line entry point
  config.py         AppConfig and TOML loading
  events.py         event dataclasses
  actions.py        Action dataclass
  bus.py            EventBus / Subscription
  plugins.py        Plugin base, Registry, PLATFORMS / DEVICES / PATTERNS
  rules.py          Rule model, RuleEngine
  patterns.py       Pattern base + built-in patterns
  scheduler.py      Scheduler
  safety.py         SafetyConfig, SafetyGuard
  example_config.toml
  platforms/        base.py, simulator.py, chaturbate.py, bridge.py (+ bridge.user.js), stripchat.py
  devices/          base.py, dummy.py, estim2b.py
  overlay/          server.py, static/{overlay.html, control.html, common.js}
  gui/              main.py, window.py, client.py, engine.py, model.py (PySide6, optional)
```
