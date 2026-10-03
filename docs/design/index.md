# Design documentation

These documents describe how estim-camming is built and why. They are plain
Markdown (rendered by Sphinx through MyST) so both people and tools can read
them directly from the repository.

Keep them in sync with the code: when a change alters an interface, an
invariant or a data flow described here, update the relevant page in the same
commit.

| Document | Contents |
|---|---|
| [Architecture](architecture.md) | Goals, components, data flow, concurrency model |
| [Events and the event bus](events.md) | Event types and pub/sub semantics |
| [Plugin system](plugins.md) | Registries, options schemas, entry points |
| [Platforms](platforms.md) | Platform interface, reconnects, Chaturbate, browser bridge / Stripchat, adding a site |
| [Devices](devices.md) | Device interface, level model, adding a device |
| [Coyote 3.0](coyote3.md) | DG-LAB Coyote 3.0 plugin over Bluetooth LE |
| [Rules and patterns](rules-and-patterns.md) | Tip → action mapping, waveform patterns |
| [Safety](safety.md) | Safety invariants and how they are enforced |
| [Overlay and control panel](overlay.md) | Web server, WebSocket protocol, OBS integration |
| [Desktop control window](gui.md) | PySide6 GUI, engine child process, stdin watchdog |
| [Rules editor](rules-editor.md) | GUI window and API for editing rules while running |
| [Configuration](configuration.md) | Config file schema and validation |
| [Decision log](decisions.md) | Architecture decision records |

```{toctree}
:hidden:

architecture
events
plugins
platforms
devices
coyote3
rules-and-patterns
safety
overlay
gui
rules-editor
configuration
decisions
```
