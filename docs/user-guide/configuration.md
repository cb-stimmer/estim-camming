# Configuration

estim-camming reads a [TOML](https://toml.io) file, `config.toml` by default:

```sh
estim-camming run -c my-show.toml
```

Unknown or misspelled settings are reported as errors. Run
`estim-camming check` after every edit.

:::{note}
`config.toml` may contain secret API tokens. Don't share it or commit it to
git (the repository's `.gitignore` already excludes it).
:::

## Complete example

This is the file `estim-camming init` writes:

```{literalinclude} ../../src/estim_camming/example_config.toml
:language: toml
```

## `[device]`

Which device to drive.

```toml
[device]
type = "dummy"
[device.options]
channels = ["A", "B"]
```

| Device `type` | Options |
|---|---|
| `dummy` | `channels` (list, default `["A", "B"]`), `log_step` (default 0.1) |
| `estim2b` | E-Stim Systems 2B over serial: `port`, `power`, `mode`, `max_output`, ... See [E-Stim 2B setup](estim2b.md) |

Run `estim-camming plugins` to see every installed device and its options.

## `[[platforms]]`

One block per streaming site. Several can run at the same time (multi-streaming).
Set `enabled = false` to switch one off without deleting it.

### Simulator

```toml
[[platforms]]
type = "simulator"
[platforms.options]
interval = 8        # average seconds between tips
min_tokens = 1
max_tokens = 120
# usernames = ["alice", "bob"]
# seed = 42         # same tips every run
```

### Chaturbate

```toml
[[platforms]]
type = "chaturbate"
[platforms.options]
url = "https://eventsapi.chaturbate.com/events/YOUR_USERNAME/YOUR_TOKEN/"
```

Create the Events API URL on Chaturbate, in the broadcaster's Events API /
token settings, and paste the full URL. It contains a secret token: treat it
like a password. If the token leaks, generate a new one.

### Stripchat

Stripchat has no tip API. Tips are read from your browser by a userscript, and
the setup has a few extra steps: see [Stripchat setup](stripchat.md).

```toml
[[platforms]]
type = "stripchat"
[platforms.options]
token = "a-long-random-string-only-you-know"
room = "your_stripchat_username"
```

| Option | Default | Meaning |
|---|---|---|
| `token` | (required) | Shared secret with the userscript, 16+ characters |
| `room` | `""` | Only forward tips on `stripchat.com/<room>` pages; empty = any page |
| `mode` | `websocket` | `websocket`: read Stripchat's tip data (recommended). `dom`: read tip messages from the page (fallback) |
| `port` | `8766` | Local port the userscript sends tips to |
| `ignore_first_seconds` | `5` | Ignore tips right after the page loads |
| `debug` | `false` | Log page data to the browser console |
| `tip_selector` | `""` | dom mode: CSS selector of tip messages; empty = discovery mode |
| `tip_pattern` | `user tipped N` | dom mode: regular expression that reads the user and amount from the tip text |

The generic `bridge` platform works like dom mode for other sites. Also set
`site` (its name) and `match` (the page URLs the userscript runs on).

## `[[rules]]`: the tip menu

Each rule maps an amount of tokens to a stimulation. The **first** matching rule
(top to bottom) wins, so put exact amounts above ranges that contain them.

```toml
[[rules]]
name = "wave"                # identifier
label = "Waves"              # shown in the overlay tip menu
min_tokens = 25              # inclusive
max_tokens = 49              # inclusive; leave out for "and up"
pattern = "wave"
params = { period = 3.0, low = 0.2, high = 1.0 }
intensity = 0.5              # 0..1, relative to safety.max_level
duration = 10                # seconds
# duration_per_token = 0.1   # extra seconds per token tipped
# channels = ["A"]           # default: all channels
# show_in_menu = false       # hidden "secret" rule
```

Use `tokens = 99` instead of `min_tokens`/`max_tokens` for an exact amount.

### Different settings for A and B

To make channels do different things, give each one its own settings under
`[rules.channels.<channel>]`. Anything you leave out comes from the rule itself.
Channels you don't list stay off while the rule plays.

```toml
[[rules]]
name = "special"
label = "Special: 99"
tokens = 99
duration = 20
[rules.channels.A]
pattern = "pulse"
params = { period = 0.5 }
intensity = 0.8
[rules.channels.B]
pattern = "wave"
intensity = 0.5
```

Another example: `pattern`, `params` and `intensity` set on the rule, but B
weaker than A:

```toml
[[rules]]
name = "tease"
min_tokens = 1
max_tokens = 24
pattern = "pulse"
params = { period = 1.0 }
intensity = 0.4
duration = 5
[rules.channels.A]            # inherits pulse, period and 0.4
[rules.channels.B]
intensity = 0.2
```

A channel with its own `pattern` starts from that pattern's default `params`,
not the rule's. `estim-camming check` lists what each channel does for every rule.

### Patterns

| `pattern` | `params` | Feels like |
|---|---|---|
| `constant` | none | steady |
| `pulse` | `period` (s, 1.0), `duty` (0..1, 0.5) | on/off beats |
| `ramp` | `start` (0.0), `end` (1.0) | builds up (or down) over the action |
| `wave` | `period` (s, 2.0), `low` (0.2), `high` (1.0) | smooth waves |

## `[safety]`

| Setting | Default | Meaning |
|---|---|---|
| `start_armed` | `false` | Arm output at start-up. Keep `false` with real devices. |
| `max_level` | `0.5` | Output at intensity 1.0, as a fraction of the device's maximum |
| `channel_max_level` | `{}` | Per-channel override, e.g. `{ A = 0.4, B = 0.6 }` |
| `max_change_per_second` | `0.5` | How fast output may rise (fraction of full scale per second) |
| `max_action_seconds` | `60` | Longest a single tip can run |
| `device_timeout` | `2` | Seconds before an unresponsive device triggers an emergency stop |

Example: with `max_level = 0.4`, a rule with `intensity = 0.5` outputs 20% of
the device's maximum. The control panel's master slider scales this down further.

## `[scheduler]`

| Setting | Default | Meaning |
|---|---|---|
| `tick_hz` | `20` | Updates per second sent to the device |
| `max_queue` | `50` | Tips waiting in line; more are rejected |

## `[overlay]`

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Run the overlay / control panel web server |
| `host` | `127.0.0.1` | Address to listen on. Keep it local unless you know you need otherwise. |
| `port` | `8765` | Port |
| `control_token` | none | Password for control actions. Required if `host` is not local. |
| `recent_tips` | `10` | Number of recent tips shown |

When `control_token` is set, open the control panel as
`http://127.0.0.1:8765/control?token=YOUR_TOKEN`.
