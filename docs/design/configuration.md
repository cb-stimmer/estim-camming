# Configuration

## Format and loading

- One TOML file, default `config.toml` in the working directory (`-c` to override).
  `config.toml` is git-ignored because it may contain API tokens.
- `config.load_config(path)` parses it with `tomllib` and validates it with the
  pydantic model `config.AppConfig`. All models forbid unknown keys.
- Errors are raised as `ConfigError`. The CLI prints them and exits with code 2.
- Validation happens in two stages:
  1. **Schema**: `AppConfig` validates types and ranges, and cross-field rules
     such as the token requirement off-loopback.
  2. **Wiring**: `Application.__init__` creates the plugins (validating their
     `options` against their `Options` models), the `RuleEngine` (patterns and
     channels), and the `SafetyGuard` (channel names).
  `estim-camming check` runs both stages without connecting anything.
- The example config ships inside the package (`example_config.toml`).
  `estim-camming init` writes it out, and a test keeps it valid.

## Schema

```text
AppConfig
├── device: PluginConfig                 (required)
├── platforms: list[PluginConfig]        (default [])
├── rules: list[Rule]                    (at least one)
├── safety: SafetyConfig
├── scheduler: SchedulerConfig
└── overlay: OverlayConfig

PluginConfig   type: str, enabled: bool = true, options: table = {}
SchedulerConfig tick_hz: (0, 100] = 20, max_queue: ≥1 = 50
OverlayConfig  enabled = true, host = "127.0.0.1", port = 8765,
               control_token: secret? (required if host is not loopback),
               recent_tips: 0..100 = 10
SafetyConfig   start_armed = false, max_level: 0..1 = 0.5,
               channel_max_level: {channel: 0..1} = {},
               max_change_per_second: > 0 = 0.5,
               max_action_seconds: > 0 = 60, device_timeout: > 0 = 2
Rule           see rules-and-patterns.md
```

Plugin options are free-form in `AppConfig` and validated by each plugin.
`estim-camming plugins` lists every plugin with its options and descriptions.

## Changing the schema

1. Update the pydantic model (with defaults that keep existing configs valid if
   possible).
2. Update `example_config.toml`, this page, and `docs/user-guide/configuration.md`.
3. Safety-related defaults must err on the safe side.
