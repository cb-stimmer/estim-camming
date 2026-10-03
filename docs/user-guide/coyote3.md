# DG-LAB Coyote 3.0 setup

estim-camming controls the Coyote 3.0 directly over **Bluetooth**, without the
DG-LAB app or a phone.

:::{warning}
Support for the Coyote is **new**. It was tested on a real box without
electrodes, including that the box stops by itself when estim-camming stops
sending. Do the [first test](#first-test) without electrodes, then continue with
the lowest strength. Read [Safety first](safety.md) before connecting the box. Keep the box
within reach: its own wheel and power button always work.
:::

## Requirements

- **A Coyote 3.0.** The older Coyote 2.0 uses a different protocol and does not
  work.
- **Bluetooth LE on your computer**, switched on: KDE System Settings →
  Bluetooth, or `bluetoothctl power on`.
- **The bleak library:**

  ```sh
  .venv/bin/pip install -e '.[coyote3]'
  ```

- **The DG-LAB app must not be connected** to the box. The box talks to one
  device at a time. Close the app or disconnect it first.

No pairing is needed: estim-camming finds the box by its Bluetooth name
(`47L121000`).

## How it works

The Coyote has two settings per channel:

- **Strength** (0–200, the number on the box) sets how strong each pulse can be.
  In estim-camming this is a **fixed setting**, `strength`, that you choose.
  estim-camming also stores it in the box as its **limit**, so even the wheel on
  the box can't go higher.
- **Pulse width** (0–100) is what estim-camming changes: your tip rules and
  patterns move it between 0 and `max_output`.

So `strength` is your ceiling, like the volume knob, and the rules play within
it. Output starts at strength 0. When the first tip plays, estim-camming sets
the box to your `strength`. After **STOP**, the strength goes back to 0 until
the next tip after you arm again.

If you turn the **wheel** on the box during a show, estim-camming keeps your
setting (up to the limit) until the next STOP.

## Configure

```toml
[device]
type = "coyote3"
[device.options]
strength = 20          # 0-200: choose a LOW value to start; required
# strength = { A = 20, B = 15 }   # or per channel
# strength_limit = 30  # the box's limit (default: strength); the wheel can't go higher
# max_output = 100     # pulse width at full level (1-100)
# min_output = 10      # pulse width for the weakest output you can feel (0 = off stays off)
# frequency = 50       # pulses per second, 1-100
```

| Option | Default | Meaning |
|---|---|---|
| `strength` | (required) | Strength (0–200) while output is on. A number, or per channel `{ A = …, B = … }` |
| `strength_limit` | `strength` | Limit stored in the box (0–200). Nothing, not even the wheel, goes above it |
| `min_output` | `0` | Pulse width for the lowest non-zero output. Off always stays 0 |
| `max_output` | `100` | Pulse width at full output (1–100) |
| `frequency` | `50` | Pulses per second (1–100), per channel or shared |
| `frequency_balance` | `160` | Box setting (0–255): higher makes low frequencies feel stronger |
| `intensity_balance` | `0` | Box setting (0–255): pulse width balance |
| `address` | (none) | Bluetooth address of the box, if you have more than one |
| `scan_timeout` | `10` | Seconds to look for the box |
| `stall_timeout` | `1` | If sending to the box stalls this long, output is stopped |

How the limits combine:

```text
level      = rule intensity × master slider × safety.max_level      (0 … 1)
pulse width = min_output + level × (max_output − min_output)          (0 when level is 0)
strength    = your strength setting, never above strength_limit
```

:::{note}
`strength_limit`, `frequency_balance` and `intensity_balance` are **stored in
the box** and stay after you switch it off. They also apply when you use the
box with the DG-LAB app later. If the app's wheel stops at a lower value than
you expect, that's why: raise the limit in the app, or set `strength_limit` in
estim-camming.
:::

## First test

1. Run `estim-camming check`. It validates the config without touching the box.
2. Switch the box on, **without electrodes**. Disconnect the DG-LAB app.
3. Run `estim-camming run` (or `estim-camming gui`). The log should show
   `Coyote 3.0 connected: battery …%, strength 20/20 …`.
4. Open the control panel, arm, and send a small test tip. The log shows
   `Coyote 3.0: strength on (A=20, B=20)` when the tip starts, and
   `Coyote 3.0: strength 0` after **STOP**.
5. Check that output stops by itself if estim-camming stops: while a tip plays,
   switch your computer's Bluetooth off. estim-camming must stop and disarm, and
   the box must stop its output within a fraction of a second. If you can't
   tell from the box whether it still outputs, do this step with electrodes at
   strength 1–2.
6. Only then use the electrodes normally, starting with a low `strength`.

## Good to know

- The box gets a new update every 0.1 s. If estim-camming freezes or the
  Bluetooth connection drops, the box runs out of instructions and should stop
  by itself. That is checked in the first test above.
- If the connection drops, estim-camming performs an emergency stop and disarms.
  Fix the connection, restart estim-camming, and arm again.
- **"no Coyote 3.0 named '47L121000' found"**: the box is off, out of range,
  still connected to the DG-LAB app, or your computer's Bluetooth is off.
