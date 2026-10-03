# Rules editor

The rules (the tip menu) can be edited while the engine runs, from a desktop
window (`gui/rules.py`). Saving applies the rules at once and writes them to
`config.toml`. The window is a client of the control API like the main window
(see [GUI](gui.md)): the engine owns validation, the live rules and the config
file. Decisions: [ADR-016](decisions.md).

## Goals and non-goals

Goals:

- Add, edit, duplicate, delete and **reorder** rules. Order matters because the
  first match wins.
- Edit every `Rule` field, including per-channel settings and pattern params,
  without knowing the TOML syntax.
- Catch mistakes before they go live: invalid values, unknown channels, bad
  pattern params, and rules that can never match.
- Apply changes to the running engine without a restart, and keep
  `config.toml` in sync.

Non-goals:

- Editing safety limits, the device, platforms or the overlay. Those stay in
  `config.toml`, and changing them still needs a restart. The editor must not
  become a way to raise output limits (see the safety section below).
- A web version. The API is generic, so the web control panel could get an
  editor later.

## Data flow

```text
RulesWindow (GUI)                         Engine (Application)
┌──────────────────────────┐   GET /api/rules          ┌─────────────────────────────┐
│ draft: list[dict]        │──────────────────────────▶│ rules_info()                │
│ check_rules() locally    │   POST /api/rules/check   │ check_rules(): same code,   │
│  (instant, same code)    │──────────────────────────▶│   engine's plugins/channels │
│                          │   POST /api/rules         │ replace_rules():            │
│ Save ────────────────────┼──────────────────────────▶│   check → swap → write file │
└──────────────────────────┘   (token, JSON, origin)   │   → publish RulesChanged    │
                                                       └─────────────────────────────┘
```

- **The engine is the only writer** of the live rules and of `config.toml`. The
  GUI never writes the config file itself. This works the same with
  `gui --connect`.
- **Atomic swap.** `Application.replace_rules` validates the complete set with
  `rules_io.check_rules` (schema, wiring and the extra checks below), builds a
  new `RuleEngine` and assigns `app.rules = new_engine` in one statement on the
  event loop. The dispatcher reads `app.rules` per tip, so a tip is matched
  against either the old rule set or the new one, never a mix. An invalid set
  changes nothing.
- **Queued and playing actions are not changed.** `Action`s are immutable and
  already carry their outputs and duration. New rules only affect tips that
  arrive after the swap.
- `replace_rules` runs under an `asyncio.Lock`, so concurrent saves are
  serialised (revision check, swap and file write together).
- **`RulesChanged`** (`revision`, `saved`) is published after the file write.
  The overlay menu updates through the normal snapshot push, and the snapshot
  carries `rules_revision` so other editors notice the change.

## Control API

| Method | Path | Body → reply |
|---|---|---|
| GET | `/api/rules` | → `{revision, rules, channels, patterns, limits, file}` |
| POST | `/api/rules/check` | `{rules}` → `{ok, errors, warnings}`, no side effects |
| POST | `/api/rules` | `{revision, rules}` → `{revision, saved, detail, warnings}`; 400 `{ok, errors, warnings}`; 409 `{error, revision}` |

- `rules` uses the same shape as the config file: a list of `[[rules]]` tables
  as JSON. `GET` returns them compactly (`rules_io.rule_to_data`: defaults left
  out, `tokens = N` for an exact amount).
- `patterns`: `{name: {description, schema}}`, where `schema` is the pattern's
  `Options.model_json_schema()`. The GUI builds its params forms from this.
- `limits`: `max_action_seconds`, `max_level`, `channel_max_level`, read-only,
  so the editor can show what an intensity means on the device.
- `file`: `{path, writable, changed_on_disk}`, or `null` when the engine was
  started without a file (then rule changes are live only).
- Problems are `{level, message, rule, field}`: `level` is `error`, `warning` or
  `info`, `rule` the index in the list (`null` for the list as a whole), and
  `field` a dotted path such as `channels.A.params.period`.
- **Revision check.** `POST /api/rules` must send the `revision` it started
  from. A different current revision → `409`, so the web panel, a second GUI or
  a reload never silently overwrite each other.
- **Auth.** All three routes go through the S10 middleware. `GET /api/rules`
  also needs the token when one is configured (other GETs don't), because it
  reveals hidden rules (`show_in_menu = false`).
- `Controller` protocol: `rules_info()`, `check_rules(rules)`,
  `replace_rules(revision, rules)`.

### Checks

`rules_io.check_rules(data, device_channels, max_action_seconds)` is used by the
engine (authoritative) and by the GUI (instant feedback while typing). Errors
block saving:

- Everything `Rule` checks (types, ranges, `tokens` vs `min/max_tokens`,
  `duration_max ≥ duration`, an intensity for every driven channel, duplicate or
  empty `channels`), with the pydantic error location as `field`.
- Wiring (`rules.wiring_problems`, shared with start-up validation): unknown
  channels, unknown patterns, and pattern params validated against the
  pattern's `Options`, one problem per param (`params.period: …`).
- Duplicate rule names, and an empty list.

Warnings and info don't block saving:

- **Warning, never matches:** the earlier rules together cover the rule's whole
  token range (for example `25–99` before a `69` special).
- **Info, partly covered:** some amounts go to an earlier rule. This is the
  normal "special before range" setup, so it's info only.
- **Warning, capped duration:** the duration can exceed
  `safety.max_action_seconds` and will be cut (S6): always, for random durations
  above the cap, or from a given tip size with `duration_per_token`.
- **Info, gaps:** amounts that trigger nothing.

## Writing `config.toml`

`rules_io.RulesFile` writes the rules. It is created by `cli._build` with the
SHA-256 of the bytes the config was loaded from (`config.load_config_file`).

- **Text splice.** `rules_to_toml` generates the `[[rules]]` block with
  `tomlkit` (one blank line between rules, `params = { … }` inline,
  `[rules.channels.X]` sub-tables, whole seconds as ints). `splice_rules`
  replaces the lines from the first `[[rules]]` header up to the next other
  section header. Comment and blank lines right above that header are kept
  with it. Everything outside the rules stays byte for byte the same.
  **Comments between rules are lost** (accepted, ADR-016). Rules split up by
  other sections are refused with a message. A file without `[[rules]]` tables
  (for example an inline array) gets the key replaced through `tomlkit`.
- **Verification before writing:** the new text must parse, every other setting
  must be identical to the old file's, and the rules must read back exactly as
  sent. Otherwise nothing is written.
- **Safe write:** the first write in a session copies the original to
  `config.toml.bak`. The new text goes to `config.toml.tmp` (fsync, file mode
  copied), then `os.replace`. All of this runs in `asyncio.to_thread`.
- **External edits:** if the file on disk no longer matches the SHA-256 the
  engine loaded (or last wrote), it is not overwritten. The new rules are live
  anyway, and the reply says `saved: false` with the reason ("changed by hand
  …; restart the engine to load it").
- Order: check → swap live → write file. A write failure (read-only file, disk
  full) leaves the new rules live and reports `saved: false`. It never rolls
  back rules the performer has just replaced.

## Window

`RulesWindow` is a separate top-level window, opened from **Edit rules…** in the
main window's controls. It is not modal, so STOP stays usable. It works on a
**draft** until Save.

```text
┌ Rules ─ estim-camming ────────────────────────────────────────────────────────┐
│ [+ Add] [Duplicate] [Delete] [▲] [▼]       Try: [ 77 tokens] → rule 3 "Surprise!", 5–30 s │
├────────────────────────────────────┬──────────────────────────────────────────┤
│ #   Tokens  Label     Pattern  Time│ Rule: Name, Label                        │
│ 1   1–24    Tease     pulse    5 s │   Tokens (•) exactly [77]  ( ) from [ ] to [no limit] │
│ 2   25–49   Waves     wave    10 s │   Time [5 s] ☑ random up to [30 s]  [0 s per token] │
│ 3   77      Surprise! random_… 5–30│   ☑ Show in the tip menu                 │
│ 4   50–98   Build up  ramp    15 s │ Output: (•) Same on all channels ( ) Per channel │
│ 5   99      Special   per ch… 20 s │   Pattern [random_level ▾]  Low High Seed│
│ ⚠ 6 100+    Big tip   constant 10 s│   Intensity [========|] 1.00             │
│                                    │   ≈ A 50%, B 50% of the device range     │
│                                    │   Channels ☑ A ☑ B   Preview ▒▒▒▒▒▒▒▒▒   │
├────────────────────────────────────┴──────────────────────────────────────────┤
│ ⚠ 6 "Big tip": duration_per_token: tips of 501+ tokens can be cut to …        │
│ ℹ 4 "Build up": tokens: tips of 77 go to rule 3 "Surprise!" first             │
├───────────────────────────────────────────────────────────────────────────────┤
│ ● Unsaved changes · ARMED: saved rules apply to the next tip  [Revert] [Save] │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Rule list

- A `QTreeWidget` over the draft. Columns: number, tokens (`69`, `1–24`, `100+`),
  label (italic and "(hidden)" for hidden rules), pattern (`per channel` if
  channels have their own), time (`20 s`, `10–30 s`, `+0.1/tk`).
- Reorder with drag and drop, ▲/▼ or Ctrl+↑/↓. The list order is the match
  order. After a drop, `_sync_order` takes the order from the list (the items
  carry stable keys).
- Rows with errors get ⛔, rows with warnings ⚠, with the messages as tooltip.
- **Add** inserts a template (`constant`, intensity 0.3, 5 s, tokens = the
  smallest amount no rule takes) after the selected rule. **Duplicate** copies
  it as `<name>-copy`. **Delete** asks first, unless the rule was added in this
  draft.

### Rule form

- **Tokens:** *exactly* (writes `tokens`) or *from … to* (writes
  `min_tokens`/`max_tokens`, "no limit" = no upper bound). Both at once can't
  happen.
- **Time:** duration, *random up to* (`duration_max`), seconds per token, and a
  hint line with the result and the cap.
- **Output, same on all channels:** pattern, params form, intensity, and channel
  checkboxes (all checked = no `channels` key).
- **Output, per channel:** the rule's pattern/params/intensity become *rule
  defaults* (intensity optional), plus one tab per device channel: *plays this
  rule* (unchecked = stays at 0), *own pattern* (pattern and params; the params
  are always written explicitly, so nothing is inherited implicitly) and *own
  intensity*. Switching back to "same on all channels" asks first if channels
  have their own settings.
- **Params form from the JSON schema** (`rules_model.param_fields`): `number` →
  `QDoubleSpinBox`, `integer` → `QSpinBox` (bounds from
  `minimum`/`maximum`/`exclusive*`), `boolean` → checkbox, `enum` → combo,
  optional (`anyOf` with `null`) → a "set" checkbox plus the field, anything
  else → a JSON text field. Only values that differ from the default are
  written. Unknown keys are kept (and reported by the check). The `description`
  is the tooltip.
- **Intensity hint:** `intensity × channel_max(c)` per channel as a percentage of
  the device range (the S4 formula without the live master slider). 1.0 means
  "the safety maximum", not "the device maximum".
- **Preview:** the level per channel over the base duration, from
  `Rule.outputs()` and the local `PATTERNS` registry, 200 samples. Several fresh
  pattern instances are sampled, and if they differ (random patterns) the range
  is drawn as a band instead of a line. "Fix the errors to see a preview" while
  the rule is invalid.
- Spin boxes and combos ignore the mouse wheel unless focused, so scrolling the
  form doesn't change values.

### Top and bottom bars

- **Try:** which draft rule an amount triggers, and its duration (capped). Local
  only, it never sends a tip.
- **Problems list:** the local check runs on every edit. `/api/rules/check` runs
  300 ms after the last edit, and its result replaces the local one if it still
  belongs to the current draft (it differs only if the engine has other
  plugins). Clicking a problem selects the rule and focuses the field.
- **Save** (Ctrl+S) is disabled while there are errors. The status line shows
  unsaved changes, "ARMED: saved rules apply to the next tip", the error count,
  and the result of the last save ("Saved and applied", or in orange "Applied,
  but NOT saved to the file: …"). **Revert** discards the draft (after asking)
  and reloads.
- **Changes elsewhere:** when the snapshot's `rules_revision` changes, a clean
  editor reloads silently. With unsaved edits, an orange banner offers *Reload*
  or *Keep editing*. Saving then gets a 409, and the banner offers *Overwrite*
  (save again on top of the newer revision) or *Reload*.
- **Closing** with unsaved changes asks Save / Discard / Cancel. Closing the
  main window closes the editor first (same question). Cancel keeps both open.

## Safety

The editor changes *which* output a tip causes. It cannot change *how much*
output the device may give:

- Rule intensity is limited to 0..1 and multiplied by `scale × channel_max`
  (S4). Durations are capped by `max_action_seconds` (S6). The soft start (S5)
  still applies. None of these limits can be edited from this window.
- Saving rules is a control action. It goes through the S10 middleware (JSON
  only, no foreign origin, token), like arming.
- **S13** (see [Safety](safety.md)): rule changes are all-or-nothing, never
  change safety settings, and never change queued or playing actions.
- The application-wide Esc/Space emergency stop filter also covers this window.
  Space still types in text and number fields, but on a checkbox, combo or
  button it triggers STOP instead. That's on purpose: stopping wins over
  convenience. The editor has no Arm control.
- Labels and names are shown only in plain-text widgets. The overlay escapes
  them (`escapeHtml`).

## Testing

- `tests/test_rules_io.py`: round trip for every rule shape, TOML style, splicing
  (comments outside the rules kept, inline arrays, split rules refused), all
  checks, and the file writer (backup, atomic write, refusing after a hand
  edit).
- `tests/test_rules_api.py`: the three routes with a real `Application`, the
  token on `GET /api/rules`, CSRF, 409, 400 with field paths, the atomic swap,
  `RulesChanged`, the file written, new tips using new rules while queued
  actions keep theirs, `saved: false` after a hand edit, and live-only without
  a file.
- `tests/test_gui_rules_model.py`: summaries, the try box, params fields from
  schemas, intensity hints and preview bands (Qt-free).
- `tests/test_gui_rules.py` (offscreen Qt, real engine): edit → Save → the
  engine's menu and the file change, shadow warning and reorder, drag and drop,
  errors disable Save, a per-channel round trip, change elsewhere → banner → 409
  → overwrite, the unsaved-changes prompt, and Esc in the editor stops output.
