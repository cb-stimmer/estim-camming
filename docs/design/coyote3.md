# DG-LAB Coyote 3.0 device

**Status: accepted ([ADR-018](decisions.md)), in development.** Phase 1
(protocol functions) is in progress; the plugin itself and the hardware checks
(H1–H6) follow.

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
  100 ms. The 4 segments are interpolated linearly from the last value sent to
  the newest target. Every value lies between two guard-approved levels, so
  the soft start (S5) and the caps (S4) still hold.

Howl uses the same split (its "power" is the channel strength, sent as an
absolute value only when it changes; its patterns drive the waveform).

### Strength life cycle

- **Connect:** BF (limits + balance), then B0 *absolute 0/0* with a sequence
  number, waiting for the matching B1 → the box is known to be at 0.
- **First non-zero level** (after connect or a stop): B0 *absolute `strength`*
  with a sequence number, in the same frame as the first waveform values (the
  guard's soft start ramps the pulse width up from there). The plugin waits for
  the B1 before any further strength change.
- **Wheel:** a B1 with sequence 0 means the performer turned the wheel. The
  plugin logs it and **keeps** the new strength (it can't exceed the soft
  limit) until the next stop (D1).
- **`stop()`** (emergency stop, disarm): immediately, outside the 100 ms rhythm,
  a B0 *absolute 0/0* plus intensity 0 on all segments. Strength is restored
  only by the next non-zero level, i.e. after re-arming.
- **Disconnect:** `stop()` first, then close.

## Plugin structure

`devices/coyote3.py`, registered as `coyote3`, imported in
`devices/__init__.py`. The protocol encoding lives in pure functions
(`encode_b0`, `encode_bf`, `encode_frequency`, `parse_b1`) so it can be tested
byte for byte against the doc's examples. BLE through
[bleak](https://github.com/hbldh/bleak) (async, BlueZ on Linux), as an optional
extra `coyote3 = ["bleak>=…"]`, imported lazily in `connect()` like estim2py.
No worker thread is needed: bleak is asyncio-native, so this also satisfies
"no blocking I/O on the loop".

### Options

| Option | Default | Meaning |
|---|---|---|
| `address` | none | Bluetooth address; unset = scan for the name |
| `name` | `47L121000` | Advertised name to scan for |
| `scan_timeout` | 10 | Seconds to find the box |
| `strength` | **required** | Channel strength (0–200) when output is on; number or `{ A = …, B = … }` |
| `strength_limit` | = `strength` | BF soft limit (0–200), enforced by the box, also against the wheel |
| `min_output` / `max_output` | 0 / 100 | Waveform intensity range for levels > 0 |
| `frequency` | 50 | Carrier in Hz (1–100), per channel or shared (D2) |
| `frequency_balance` | 160 | BF param 1 (0–255) (D3) |
| `intensity_balance` | 0 | BF param 2 (0–255) |
| `stall_timeout` | 1.0 | No successful write for this long = device fault |

`strength` has no default on purpose: like the 2B's `port`, the performer has
to choose it consciously, and it is the number that matters most for safety.
Validation: 0 ≤ `strength` ≤ `strength_limit` ≤ 200 per channel, 0 ≤
`min_output` ≤ `max_output` ≤ 100.

### Tasks and calls

- **`connect()`**: find the box (`address` or scan by `name`, `scan_timeout`),
  connect with a `disconnected_callback`, check that service `0x180C` and both
  characteristics exist, subscribe to `0x150B`, read the battery, write BF,
  zero the strength (B0 + B1 handshake, timeout → `DeviceError`), start the
  writer task. Any failure → `DeviceError`, which aborts start-up.
- **Writer task**: every 100 ms on a monotonic schedule (no drift), build a B0
  from the latest target and any pending strength change, and write it
  **without response** (as Howl and coyote-3-studio do; the B1 handshake
  confirms strength changes). A failed write, a disconnect callback or a
  missing B1 within 0.5 s sets the device error.
- **`set_levels()`**: raise if not connected, if there is an error, or if the
  last successful write is older than `stall_timeout` (the guard then performs
  an emergency stop, S8). Otherwise store the target (box units) and return.
- **`stop()`**: zero the target and write the stop frame at once (see the
  strength life cycle), serialised with the writer by an `asyncio.Lock`.
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
- **Built-in watchdog (if H1 holds):** waveform data is only valid for 100 ms.
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
  normal desktop session. Pairing is not expected to be necessary (*H5*).

## Testing

- **Protocol unit tests** (`tests/test_coyote3.py`): B0 layout and nibbles,
  frequency encoding against the doc's table, strength modes and the doc's
  examples, BF bytes, B1 parsing, interpolation never overshooting.
- **Plugin tests with a fake BleakClient**: connect sequence (BF before any
  B0, strength zeroed and confirmed), 100 ms cadence with a fake clock, latest
  target wins, strength restore after stop, wheel B1, disconnect / write error
  / stall / missing B1 → `set_levels` raises, `SafetyGuard` emergency-stops on a
  Coyote fault (`tests/test_safety.py`), missing bleak → clear `DeviceError`.
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

## Phases

1. Protocol functions + unit tests (no hardware).
2. Device plugin with the fake client + safety tests, docs (`devices.md`,
   user guide page, example config, ADR).
3. Hardware checklist H1–H6 with the user; adjust (for example, the default
   frequency and balance).

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
