# Rules and patterns

## Rules (tip menu)

A `rules.Rule` is one entry of the tip menu, configured as a `[[rules]]` table.

| Field | Type | Meaning |
|---|---|---|
| `name` | str | Identifier, used in logs and events |
| `label` | str? | Text shown in the overlay (default: `name`) |
| `tokens` | int? | Exact amount; shorthand for `min_tokens = max_tokens = tokens` |
| `min_tokens` / `max_tokens` | int? | Inclusive range; `max_tokens` omitted means no upper bound |
| `pattern` | str | Pattern name (default `constant`) |
| `params` | table | Pattern options, validated against the pattern's `Options` |
| `intensity` | 0..1? | Relative intensity; 1.0 = the configured safety maximum. Optional if every channel sets its own |
| `duration` | s > 0 | Base duration |
| `duration_per_token` | s ≥ 0 | Extra seconds per token in the tip |
| `channels` | list or table? | Channels to drive; default all device channels. As a table: per-channel settings (below) |
| `show_in_menu` | bool | Whether the overlay lists it (hidden "secret" rules) |

### Per-channel settings

`channels` can be a table of `ChannelSpec`s. Each channel's `intensity`,
`pattern` and `params` override the rule's values. Unset fields are inherited,
except that a channel with its **own** pattern doesn't inherit the rule's
`params` (they belong to a different pattern), so it uses that pattern's
defaults unless it sets `params` itself. Channels missing from the table stay
at 0 while the rule plays. `duration` is always per rule.

```toml
[[rules]]
name = "split"
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

`Rule.outputs(device_channels)` resolves this into one `ChannelOutput`
(`channel`, `pattern`, `params`, `intensity`) per driven channel. The model
validator rejects rules without an intensity for every driven channel,
duplicate or empty `channels`, and unknown keys in a channel table.

### Matching

`RuleEngine.match(tokens)` returns the **first** rule, in config order, whose
range contains the amount. Put exact-amount specials before the ranges that
contain them. A tip that matches no rule is still recorded and shown, but
produces no output.

### Action construction

`RuleEngine.action_for(tip)` creates an immutable `Action`:

```text
duration = min(rule.duration + rule.duration_per_token * tip.tokens, safety.max_action_seconds)
outputs  = rule.outputs(device.channels)   # one ChannelOutput per driven channel
```

`Action.channels` is a convenience property listing the driven channels.

### Validation

At start-up (and in `estim-camming check`), the engine instantiates every
resolved channel output's pattern with its `params`, and checks that the
channels exist on the device. Any
error aborts start-up, so a typo never shows up for the first time during a live
show.

## Scheduling

`Scheduler` holds a FIFO queue (max `scheduler.max_queue`) and plays one action
at a time:

```text
patterns = one Pattern instance per ChannelOutput
for each tick (1 / tick_hz seconds) while t < duration:
    if skip requested or guard disarmed: interrupted = True; break
    level[ch] = clamp(pattern[ch].level(t, duration), 0, 1) * output[ch].intensity
    guard.set_levels(level + zeros for channels without an output)
finally: guard.set_levels(all zero); publish ActionFinished
```

- `enqueue()` rejects actions (publishing `ActionRejected`) when the guard is
  disarmed or the queue is full. Tips are never silently "saved up" while the
  output is off.
- `clear()` empties the queue. It is called automatically on every emergency stop.
- `skip_current()` ends the playing action early.

Future options (not implemented): combining simultaneous actions instead of
queueing them, priority for large tips, and an idle pattern.

## Patterns

A pattern maps elapsed time to a relative level:

```python
class Pattern(Plugin):
    def level(self, t: float, duration: float) -> float: ...   # 0..1
```

A new pattern instance is created per action, so patterns may keep state (for
example a random walk). Output is clamped to 0..1 by the scheduler anyway.

| Name | Options (default) | Shape |
|---|---|---|
| `constant` | none | always 1 |
| `pulse` | `period` (1.0 s), `duty` (0.5) | square wave, on for `duty` of each period |
| `ramp` | `start` (0.0), `end` (1.0) | linear from start to end over the action |
| `wave` | `period` (2.0 s), `low` (0.2), `high` (1.0) | raised cosine between low and high, starting at low |

Adding a pattern: subclass `Pattern` in `patterns.py` (or in a plugin package
using the `estim_camming.patterns` entry-point group), register it, give it an
`Options` model, and add it to the parametrised range test in
`tests/test_rules_and_patterns.py`.
