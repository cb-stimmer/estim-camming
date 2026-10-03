# DG-LAB Coyote 3.0 device

**Status: accepted ([ADR-018](decisions.md)), implemented, verified on hardware
without electrodes (2026-10-03).** H1–H5 passed; H6 (feel, needs electrodes)
is open. See
[Hardware check](#hardware-check-2026-10-03).

A device plugin `coyote3` drives a DG-LAB Coyote 3.0 ("郊狼 3.0", pulse host)
directly over **Bluetooth LE** from the PC, with no phone or DG-LAB app in
between.

## Sources

- **Official protocol** (DG-LAB, Chinese): `coyote/v3/README.md` and the waveform
  explanation `coyote/README.md` in
  [DG-LAB-OPENSOURCE](https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE)
  (now `dungeonlab-open/dglab-bluetooth-protocol`). Free for non-commercial
  use. An [English translation](https://github.com/sdewis/dg-lab-opensource-english)
  exists; the Chinese original is authoritative.
- The V2 protocol doc (`coyote/v2/README.md`) for behaviour the V3 doc doesn't
  spell out (see H1).
- Cross-checked against independent implementations (not used as sources of
  truth): [Howl](https://github.com/Amethyst-Sysadmin/Howl)
  (`OutputCoyote3.kt`), [coyote-3-studio](https://github.com/dglab-deviant/coyote-3-studio)
  (Python, bleak) and [pydglab](https://github.com/shilapi/DGLAB-python-driver).

The alternative, DG-LAB's app "SOCKET control" (WebSocket via the phone app),
was rejected: more latency, the phone as an extra link, and the app buffers up
to 50 s of waveform, so output can continue after estim-camming dies.

## Protocol summary (what the plugin uses)

Base UUID `0000xxxx-0000-1000-8000-00805f9b34fb`. Advertised name of the pulse
host: **`47L121000`**.

| Service | Characteristic | Use |
|---|---|---|
| `0x180C` | `0x150A` write (≤ 20 bytes) | all commands |
| `0x180C` | `0x150B` notify | all replies (B1) |
| `0x180A` | `0x1500` read / notify | battery, 1 byte |

No byte-order conversion (unlike V2).

### B0: strength and waveform, every 100 ms (20 bytes)

| Bytes | Field | Values |
|---|---|---|
| 0 | `0xB0` | |
| 1, high nibble | sequence number | 0–15. Non-zero → the box answers with B1 carrying the same number |
| 1, low nibble | strength mode, 2 bits per channel (A high, B low) | `00` unchanged, `01` +, `10` −, `11` absolute |
| 2, 3 | strength A, B | 0–200; outside → treated as 0 |
| 4–7 | A waveform frequency × 4 | 10–240 (encoded, see below) |
| 8–11 | A waveform intensity × 4 | 0–100 |
| 12–15 | B waveform frequency × 4 | 10–240 |
| 16–19 | B waveform intensity × 4 | 0–100 |

- **Channel strength** (0–200) sets the pulse **voltage**. The box's absolute
  range is 0–200.
- Each frequency/intensity pair is **25 ms** of output; 4 pairs = 100 ms.
- **Waveform frequency** is the length of one output unit (pulses + gap) in
  **ms**, 10–1000, encoded to 10–240:
  `10..100: v`; `101..600: (v − 100)/5 + 100`; `601..1000: (v − 600)/10 + 200`,
  with integer division (the official conversion code; its own examples give
  333 ms → 146 and 1000 ms → 240). The waveform doc's table agrees (10 ms
  (100 Hz) → 10, 100 ms (10 Hz) → 100, 150 ms → 110, 680 ms → 208, 750 ms → 215)
  except for one row, 650 ms → 204, where the formula gives 205. The plugin
  follows the formula. The official code maps out-of-range input to 10; the
  plugin rejects it instead (it never sends a value it didn't mean). How pulses and gap split the unit is set by the
  frequency balance (BF).
- **Waveform intensity** (0–100) is the relative **pulse width**: wider feels
  stronger. No unit.
- An out-of-range frequency or intensity makes the box **drop all 4 pairs of
  that channel**. The doc's examples address one channel only by sending
  frequencies `{0,0,0,0}` and intensities `{0,0,0,101}` for the other; the
  plugin uses exactly this as its "no data for this channel" form.
- After a strength change with a sequence number, wait for the matching B1
  before the next strength change on that channel (the doc suggests this; Howl
  and coyote-3-studio do the same).

### BF: soft limits and balance (7 bytes, no reply)

`0xBF`, soft limit A, soft limit B, frequency balance A, B, intensity balance A, B.

- **Soft limit** (0–200): the channel strength can never exceed it, whether
  changed by B0 or by the **wheel on the box**. Out-of-range values leave it
  unchanged. **Kept after power-off.**
- **Frequency balance** (0–255, "param 1"): how pulses and gap share a unit;
  higher = stronger low-frequency impact. **Kept after power-off.**
- **Intensity balance** (0–255, "param 2"): adjusts pulse width; higher =
  stronger low-frequency stimulus. **Kept after power-off.**
- The doc's 🚨 warning: BF takes effect immediately and has no reply, so it
  **must be written after every (re)connect** to avoid unexpected limits.

### B1: strength report (notify)

`0xB1`, sequence number, strength A, strength B. Sent after a B0 with a
non-zero sequence number (same number) and whenever the **wheel** on the box
changes a strength (sequence 0).

### Output window

The V2 doc states it outright: each waveform parameter set is valid for 0.1 s,
after which the box **stops output** until the next set arrives. V3 keeps the
100 ms window (4 × 25 ms per B0) but doesn't repeat the sentence. Channel
strength, in contrast, "changes immediately and stays" (V2 doc). **H1** checks
the V3 behaviour on hardware; the safety argument below depends on it.

## Mapping to the level model

The two-part control maps naturally onto the 0..1 level model:

- **Channel strength (voltage) = a fixed setting**, `strength` per channel,
  like the 2B's `max_output`: the ceiling of what the box can deliver. The box
  enforces it as the **BF soft limit** (`strength_limit`, default = `strength`),
  so neither software nor the wheel can exceed it.
- **Level → waveform intensity (pulse width)**: `0` when `level < 0.001`,
  otherwise `round(min_output + level × (max_output − min_output))` with
  `min_output`/`max_output` in 0–100 (defaults 0 and 100), per channel or
  shared, exactly like the 2B's options. Off stays off.
- **Waveform frequency = a fixed setting**, `frequency` in Hz per channel
  (1–100 Hz → 1000–10 ms → encoded). estim-camming's patterns shape the
  *level*; the box's carrier stays steady (the doc advises a stable frequency
  for a stable feel).
- **Smoothing**: the guard updates at up to 20 Hz, the box takes 4 values per
  100 ms. An increase is interpolated linearly over the 4 steps from the last
  value sent to the newest target; every step lies between two guard-approved
  levels, so the soft start (S5) and the caps (S4) still hold. A decrease
  (including a stop) applies to all 4 steps at once, as the guard applies
  decreases immediately.

Howl uses the same split (its "power" is the channel strength, sent as an
absolute value only when it changes; its patterns drive the waveform).

### Strength life cycle

- **Connect:** BF (limits + balance), then B0 *absolute 0/0* with a sequence
  number, waiting for the matching B1 → the box is known to be at 0.
- **First non-zero level** (after connect or a stop): B0 *absolute `strength`*
  with a sequence number, in the same frame as the first waveform values (the
  guard's soft start ramps the pulse width up from there). The B1 confirmation
  is awaited in a separate task, so the 100 ms stream never pauses for it; a
  missing or different confirmation is a fault. A newer strength change (a
  stop) supersedes an unconfirmed one, which is not a fault.
- **Wheel:** a B1 with sequence 0 means the performer turned the wheel. The
  plugin logs it and **keeps** the new strength (it can't exceed the soft
  limit) until the next stop (D1).
- **`stop()`** (emergency stop, disarm): immediately, outside the 100 ms rhythm,
  a B0 *absolute 0/0* plus intensity 0 on all segments, then waits for the
  box's confirmation (at most `reply_timeout`, inside the guard's
  `device_timeout`). Strength is restored only by the next non-zero level, i.e.
  after re-arming. The log shows `strength on (A=…, B=…)` and `strength 0`.
- **Disconnect:** `stop()` first, then close.

## Plugin structure

`devices/coyote3.py`, registered as `coyote3`, imported in
`devices/__init__.py`. The protocol encoding lives in pure functions
(`encode_b0`, `encode_bf`, `encode_frequency`, `parse_b1`, plus
`apply_strength`, the box's documented reaction to a strength field, for the
fake box in tests) so it can be tested
byte for byte against the doc's examples. BLE through
[bleak](https://github.com/hbldh/bleak) (async, BlueZ on Linux), as an optional
extra `coyote3 = ["bleak>=3.0"]`, imported lazily in `connect()` like estim2py.
No worker thread is needed: bleak is asyncio-native, so this also satisfies
"no blocking I/O on the loop".

### Options

| Option | Default | Meaning |
|---|---|---|
| `address` | none | Bluetooth address; unset = scan for the name |
| `name` | `47L121000` | Advertised name to scan for |
| `scan_timeout` | 10 | Seconds to find the box |
| `connect_timeout` | 10 | Seconds to connect |
| `strength` | **required** | Channel strength (0–200) when output is on; number or `{ A = …, B = … }` |
| `strength_limit` | = `strength` | BF soft limit (0–200), enforced by the box, also against the wheel |
| `min_output` / `max_output` | 0 / 100 | Waveform intensity range for levels > 0 |
| `frequency` | 50 | Carrier in Hz (1–100), per channel or shared (D2) |
| `frequency_balance` | 160 | BF param 1 (0–255) (D3) |
| `intensity_balance` | 0 | BF param 2 (0–255) |
| `stall_timeout` | 1.0 | No successful write for this long = device fault; also the timeout of each write |
| `reply_timeout` | 0.5 | Seconds for the box to confirm a strength change (B1) |

`strength` has no default on purpose: like the 2B's `port`, the performer has
to choose it consciously, and it is the number that matters most for safety.
Validation: 0 ≤ `strength` ≤ `strength_limit` ≤ 200 per channel, 0 ≤
`min_output` ≤ `max_output` ≤ 100, 1 ≤ `frequency` ≤ 100. A per-channel table
must name both A and B.

### Tasks and calls

- **`connect()`**: release a stale BlueZ link (see "Linux / BlueZ"), find the
  box (`address` or scan by `name`, `scan_timeout`),
  connect with a `disconnected_callback`, check that service `0x180C` and both
  characteristics exist, subscribe to `0x150B`, read the battery, write BF,
  zero the strength (B0 + B1 handshake, timeout → `DeviceError`), start the
  writer task. Any failure → `DeviceError`, which aborts start-up.
- **Writer task**: every 100 ms on a monotonic schedule (no drift; if it falls
  behind it doesn't burst to catch up), build a B0 from the latest target, and
  write it **without response** (as Howl and coyote-3-studio do; the B1
  handshake confirms strength changes). Every write is bounded by
  `stall_timeout`, so a hanging Bluetooth stack can't block a stop or
  shutdown. A failed or timed-out write, a disconnect callback or a missing B1
  within `reply_timeout` sets the device error and ends the task.
- **`set_levels()`**: raise if not connected, if there is an error, or if the
  last successful write is older than `stall_timeout` (for example a writer
  task that died; the guard then performs an emergency stop, S8). Otherwise
  store the target (box units) and return.
- **`stop()`**: zero the target and write the stop frame at once (see the
  strength life cycle), serialised with the writer by an `asyncio.Lock`.
- **`disconnect()`**: cancel the writer and confirmation tasks, `stop()` (unless
  the connection is already broken; a failure is logged as CRITICAL, check the
  box), then close. Idempotent.
- **B1 notifications**: update the known strength; sequence 0 = wheel, logged.
  A reported strength above `strength_limit` would be a firmware fault →
  device error.
- **Battery**: logged at connect; a low-battery warning is a later addition.

## Safety

- **S1** unchanged: only the `SafetyGuard` calls the plugin; the writer task only
  sends what `set_levels`/`stop` stored.
- **Output limits in the box itself:** the voltage can't exceed
  `strength_limit` (BF soft limit, written on every connect as the doc
  requires), not even with the wheel. Pulse width ≤ `max_output`. On top of
  that, S4 (the guard's caps) and S5 (soft start) apply to the level.
- **Built-in watchdog (confirmed by H1):** waveform data is only valid for 100 ms.
  If estim-camming freezes, crashes, or the Bluetooth link drops, the box
  receives no new B0 and stops output within about 100 ms, although the
  channel strength stays set. That's stronger than the 2B, which holds its last
  levels. The design does not rely on it alone: stop and disconnect still set
  strength 0 explicitly.
- **Faults** (write error, disconnect, missing B1, stall) surface on the next
  `set_levels()` → emergency stop and disarm (S8). The box has then already
  stopped by itself (H1).
- **Side effect to document for users:** soft limit and balance are stored in
  the box and stay after power-off, so they also apply when the box is later
  used with the DG-LAB app. The user guide must say so.
- One connection at a time: the box can't be connected to the DG-LAB app and
  estim-camming simultaneously (*H5*).

## Linux / BlueZ

- The adapter must be powered (`bluetoothctl power on` or KDE's Bluetooth
  settings). The user's machine: Intel AX2xx (`8087:0032`), Bluetooth 5.3,
  "central" role, BlueZ 5.72: suitable.
- bleak talks to BlueZ over D-Bus; no root and no special group are needed on a
  normal desktop session. No pairing is needed (H5).
- **Stale links.** When a connection dies without a proper disconnect
  (Bluetooth switched off, out of range, a crash), BlueZ keeps the box on its
  auto-connect list and reconnects it by itself once possible. Nobody uses that
  link, so there is no output, but while it exists the box doesn't advertise and
  can't be found (seen in H2). Before scanning, `connect()` therefore asks BlueZ
  over D-Bus (`dbus_fast`, a bleak dependency) for a connected device with the
  configured address (or name) and disconnects it, logging a warning. Any
  problem with that check (not Linux, no BlueZ) is ignored. Manual equivalent:
  `bluetoothctl disconnect <address>`.

## Testing

- **Protocol unit tests** (`tests/test_coyote3_protocol.py`, done): the doc's
  complete B0 hex frames (examples No.1, 2 and 4), the app's "breathing" bytes,
  frequency conversion lists and table, the strength examples and soft limit,
  BF bytes, B1 parsing, rejected values, level → intensity, and interpolation
  (ramps up within the two values, drops at once).
- **Plugin tests** (`tests/test_coyote3.py`, done) with a fake box that
  follows the documented strength rules, soft limit, B1 replies and wheel:
  connect sequence (BF before any B0, strength zeroed and confirmed), output
  switches the strength on and ramps the pulse width, frequency bytes,
  `min/max_output`, stop zeroes at once and output switches on again, the
  wheel is kept, disconnect, faults (connection lost, write error, write
  timeout, dead writer, missing confirmation, strength above the limit) →
  `set_levels` raises, a stop while switching on is not a fault, a device
  without the Coyote characteristics is refused, missing bleak → clear
  `DeviceError`, option validation, and the `SafetyGuard` emergency-stopping on
  a Coyote fault and zeroing the box on STOP. The frame interval is 20 ms in
  the tests instead of 100 ms.
- **Hardware checklist** (by the user, **without electrodes first**, then on
  skin at the lowest strength):
  - **H1** Output stops within ~100 ms when B0 frames stop (pause the engine
    with `kill -STOP`, or switch the PC's Bluetooth off), and what happens to
    strength. Without electrodes, observe the box's output indicator if it has
    one (*to check*); otherwise test at strength 1–2 on skin.
  - **H2** After a Bluetooth drop, the box keeps its strength but produces no
    output; after reconnecting, BF is re-applied.
  - **H3** Writes without response arrive reliably every 100 ms on BlueZ; B1
    arrives within 0.5 s.
  - **H4** The wheel can't exceed `strength_limit`, and B1 reports wheel turns.
  - **H5** Connecting needs no pairing; the DG-LAB app must be disconnected first.
  - **H6** `frequency` and the balance values feel as expected.

## Hardware check 2026-10-03

The user's Coyote 3.0 (`47L121000`, battery 100 %), Intel AX2xx with BlueZ 5.72,
bleak 3.0.2, strength and soft limit 5, **no electrodes**. Claude ran the
checks through the plugin and the `SafetyGuard`; the user watched the box.

| Check | Result |
|---|---|
| H1 | ✅ When the frames stopped (writer cancelled, no stop command), the user saw the box stop its output at once, and resume when the frames resumed. The box kept reporting strength 5, as expected: strength stays, the waveform runs out. |
| H2 | ✅ `bluetoothctl power off` during output (twice): the plugin noticed within 3–65 ms (disconnect callback or a failed write), the guard emergency-stopped and was disarmed 0.18–0.24 s after the switch-off; its `stop()` failed as it must without Bluetooth and logged the CRITICAL "check the device physically". After Bluetooth came back, BlueZ had **reconnected the box by itself** (see "Linux / BlueZ"); with the fix the plugin reconnected in 5.1 s, re-applying BF and strength 0. Whether the box stopped its output was for the user to watch (same mechanism as H1). |
| H3 | ✅ 31 frames in 3 s, gaps 99–101 ms. Strength changes confirmed by B1 in 0.13–0.27 s (`reply_timeout` 0.5 s). Characteristic `0x150A` offers only write-without-response, the mode the plugin uses. |
| H4 | ✅ Wheel down/up reported as B1 with sequence 0 within ~30 ms (5 → 4 → 3 → 4 → 5); turning further up stayed at the soft limit 5. |
| H5 | ✅ Found by name and connected in ~3.5 s without pairing. (Whether the DG-LAB app must be disconnected first was not tried.) |
| H6 | ⏳ Needs electrodes. |

Found and fixed during the check:

- bleak calls the disconnected callback for an intentional disconnect too,
  which the plugin logged as "Bluetooth connection lost" (ERROR). The plugin now
  ignores the callback while it closes the connection itself.
- After a Bluetooth drop, BlueZ reconnected the box by itself, so it could not
  be found again ("no Coyote 3.0 … found", three times in a row). The plugin now
  releases such a stale link before scanning (see "Linux / BlueZ").

## Phases

1. Protocol functions + unit tests (no hardware). **Done.**
2. Device plugin with the fake client + safety tests, docs (`devices.md`,
   user guide page, example config, ADR). **Done.**
3. Hardware checklist H1–H6 with the user; adjust (for example, the default
   frequency and balance). **H1–H5 done; H6 open.**

## Decisions

Accepted by the user on 2026-10-03 (ADR-018):

- **D1 Wheel:** wheel changes are kept until the next stop; the soft limit
  still caps them.
- **D2 Default frequency:** 50 Hz (not a DG-LAB value; revisit after H6).
- **D3 Balance defaults:** frequency balance 160 (as coyote-3-studio; Howl uses
  200, DG-LAB's docs give none), intensity balance 0 (both agree). Revisit after
  H6.
- **D4 Patterns and frequency:** the carrier frequency stays fixed per channel;
  patterns shape the level only.
