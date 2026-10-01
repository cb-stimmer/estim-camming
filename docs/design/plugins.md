# Plugin system

Three kinds of components are pluggable:

| Kind | Base class | Registry | Entry-point group |
|---|---|---|---|
| Platform (streaming site) | `platforms.base.Platform` | `plugins.PLATFORMS` | `estim_camming.platforms` |
| Device | `devices.base.Device` | `plugins.DEVICES` | `estim_camming.devices` |
| Pattern (waveform) | `patterns.Pattern` | `plugins.PATTERNS` | `estim_camming.patterns` |

## Plugin base class

Every plugin derives from `plugins.Plugin`:

- `name: ClassVar[str]`, which the registry sets to the registered name.
- `Options: ClassVar[type[PluginOptions]]`, a nested pydantic model describing
  the plugin's options. `PluginOptions` forbids unknown keys, so typos in the
  config fail loudly.
- `__init__(options)` stores the validated options on `self.options`.
- `from_config(mapping)` validates a raw mapping (the `options` table from TOML)
  and constructs the plugin. Validation errors are raised as `PluginError`.

Plugins receive **only their own options**. They do not see the global config,
the bus or other components. This keeps them easy to test in isolation.

Field `description`s on `Options` are shown by `estim-camming plugins`, so write
them for end users.

## Registries

`plugins.Registry` maps names to classes.

- Built-ins register with a decorator: `@PLATFORMS.register("chaturbate")`.
- On first lookup the registry imports its built-in package (for example
  `estim_camming.platforms`, whose `__init__` imports every built-in module), then
  loads any **entry points** in its group. A built-in name wins over an entry
  point of the same name. A failing entry point is logged and skipped.
- `get(name)`, `create(name, options)` and `names()` are the public API. Unknown
  names raise `PluginError` listing the available ones.

## Third-party plugins

A separate package can provide plugins without changing this repository:

```toml
# pyproject.toml of the plugin package
[project.entry-points."estim_camming.devices"]
coyote3 = "estim_coyote.device:Coyote3Device"
```

```python
# estim_coyote/device.py
from estim_camming.devices import Device
from estim_camming.plugins import PluginOptions

class Coyote3Device(Device):
    """DG-LAB Coyote 3 over Bluetooth LE."""

    class Options(PluginOptions):
        address: str | None = None
    ...
```

After `pip install estim-coyote`, `type = "coyote3"` works in the config and
`estim-camming plugins` lists it.

## Guidelines

- Plugins must be `async`-friendly: no blocking calls on the event loop.
- Plugins must never log secrets (tokens, API URLs). Use `pydantic.SecretStr` for
  them, and override `describe()` on platforms to give a safe display name.
- Add tests for new built-in plugins under `tests/`.
