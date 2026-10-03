# Events and the event bus

## Event bus

`estim_camming.bus.EventBus` is a minimal in-process publish/subscribe hub.

- `publish(event)` is synchronous and **never blocks**. It hands the event to
  every subscription whose type filter matches (`isinstance`).
- `subscribe(*types, maxsize=0)` returns a `Subscription`, an async iterator
  backed by an `asyncio.Queue`. With no types it receives every event.
- **Unbounded** (`maxsize=0`) subscriptions never lose events. Use them for
  consumers that must see everything, such as the tip dispatcher.
- **Bounded** subscriptions drop the *oldest* event when full and count drops in
  `Subscription.dropped`. The overlay server uses one (`maxsize=500`), so a stuck
  browser can never stall the core.
- Subscriptions are context managers; `close()` unsubscribes.

Ordering is preserved per subscription. There is no cross-process transport. If
one is ever needed, it goes in a new component that subscribes to the bus.

## Event types

All events are frozen dataclasses deriving from `events.Event`, with a
`timestamp` (Unix seconds) and a class-level `type` string used in JSON.
`Event.to_dict()` gives the JSON shape (nested dataclasses become objects).

| Class | `type` | Fields | Published by |
|---|---|---|---|
| `TipEvent` | `tip` | platform, username, tokens, message, anonymous, event_id | platform supervisor, control panel |
| `PlatformStatus` | `platform_status` | platform, connected, detail | platform supervisor |
| `ActionQueued` | `action_queued` | action | scheduler |
| `ActionRejected` | `action_rejected` | action, reason | scheduler (`"output disarmed"`, `"queue full"`) |
| `ActionStarted` | `action_started` | action | scheduler |
| `ActionFinished` | `action_finished` | action, interrupted | scheduler |
| `QueueCleared` | `queue_cleared` | count | scheduler |
| `LevelsChanged` | `levels` | levels (channel → 0..1 of full scale) | safety guard |
| `SafetyStateChanged` | `safety` | armed, scale, reason | safety guard |
| `RulesChanged` | `rules_changed` | revision, saved | application (rules editor save) |

`LevelsChanged` is only published when a channel moves by more than 0.005 or
returns to zero, which limits it to roughly the tick rate while something is
playing.

## Adding an event type

1. Add a `@dataclass(frozen=True, kw_only=True)` subclass of `Event` with a unique
   `type` ClassVar.
2. Keep fields JSON-serialisable (primitives, lists, dicts, dataclasses).
3. Document it in the table above. If the overlay should react to it, handle it in
   `overlay/static/*.html`.
