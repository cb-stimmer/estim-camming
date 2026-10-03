# E-Stim Systems 2B setup

estim-camming controls the 2B through its serial link (the "link cable" or USB
serial adapter), using the [estim2py](https://github.com/cb-stimmer/estim2py) library. It works
with the original firmware (2.106) and the newer beta firmwares (2.119B, 2.120B
and later). estim-camming detects the firmware when it connects.

:::{warning}
Read [Safety first](safety.md) before connecting the 2B. Start with the box in
**low** power mode and a low `max_output`, and keep the box within reach. If the
computer freezes, assume the 2B keeps its last setting until you turn it down or
off on the box itself.
:::

## Requirements

- **The estim2py library:**

  ```sh
  .venv/bin/pip install -e '.[estim2b]'
  ```

  This downloads estim2py 0.4.1 from GitHub, so it needs `git` and an internet
  connection. It needs Python 3.12 or newer.

- **Serial port access (Linux).** Your user must be in the `dialout` group:
  `sudo usermod -aG dialout $USER`, then log out and back in.

## Find the serial port

Plug in the link cable and run `ls /dev/ttyUSB* /dev/ttyACM*` on Linux, or check
Device Manager → Ports on Windows (for example `COM3`). The port that appears
when you plug the cable in is the one to use.

## Configure

```toml
[device]
type = "estim2b"
[device.options]
port = "/dev/ttyUSB0"
power = "low"          # "low", "high" or "dynamic" (beta firmware)
# bias = "average"     # dynamic power only: "A", "B", "average" or "max"
mode = "continuous"    # see the table below
min_output = 10        # box level for the weakest output you can feel (0 = off stays off)
max_output = 40        # box level (0-100) at full scale: a hard ceiling
# param_c = 50         # mode parameter C (usually speed), 2-100
# param_d = 50         # mode parameter D (usually feel), 1-100
# warp = 1             # time warp x1, x2, x4, x8, x16 or x32 (firmware 2.120B+)
# ramp = 1             # ramp step x1-x4 (firmware 2.120B+)
```

| Option | Default | Meaning |
|---|---|---|
| `port` | (required) | Serial port of the link cable |
| `power` | `low` | Power range of the box: `low`, `high` (much stronger) or `dynamic` (beta firmware 2.119B+: merges low and high based on `bias`) |
| `bias` | box default | Dynamic bias: `A`, `B`, `average` or `max`. Only with `power = "dynamic"` |
| `mode` | `continuous` | 2B mode, by name (recommended) or number |
| `param_c` | box default | Mode parameter C (usually speed), 2–100 |
| `param_d` | box default | Mode parameter D (usually feel), 1–100 |
| `warp` | box setting | Time warp: `1`, `2`, `4`, `8`, `16` or `32` (×1–×32). Firmware 2.120B or newer |
| `ramp` | box setting | Ramp step: `1`–`4` (×1–×4). Firmware 2.120B or newer |
| `min_output` | `0` | Box level for the lowest non-zero output. Off (0) always stays 0 |
| `max_output` | `100` | Box level sent at full scale. estim-camming never sends more than this |
| `serial_timeout` | `2` | Seconds to wait for the box to answer |
| `delay` | `0.04` | Pause after each command before reading the answer (minimum 0.034) |
| `stall_timeout` | `3` | A command that takes longer than this counts as a fault and stops output |

`min_output` and `max_output` can be one number for both channels, or set per
channel, for example `min_output = { A = 10, B = 15 }`. That's useful when the
electrodes on A and B don't feel the same.

How the limits combine: estim-camming first computes the output level

```text
level = rule intensity × master slider × safety.max_level      (0 … 1)
```

and the box gets

```text
0                                                  when level is 0 (off)
min_output + level × (max_output − min_output)     otherwise
```

For example, with `max_level = 0.5`, `min_output = 10` and `max_output = 40`, a
rule with intensity 1.0 sets the box to 10 + 0.5 × 30 = 25, and the weakest
possible output sets it to 10.

:::{note}
With `min_output` set, output switches on **at** `min_output`. The soft start
(`safety.max_change_per_second`) ramps up from there. Choose a `min_output` you
can just feel, not one that is already strong. The output bars in the control
panel show `level`, not the box value.
:::

### Modes

Use the mode **name**: estim-camming sends the right number for your box's
firmware. The beta firmwares number the modes differently, so a mode number
means a different mode depending on the firmware.

| Name | No. (2.106) | No. (beta firmware) |
|---|---|---|
| `pulse` | 0 | 0 |
| `bounce` | 1 | 1 |
| `continuous` | 2 | 2 |
| `flo` | – | 3 |
| `asplit` | 3 | 4 |
| `bsplit` | 4 | 5 |
| `wave` | 5 | 6 |
| `waterfall` | 6 | 7 |
| `squeeze` | 7 | 8 |
| `milk` | 8 | 9 |
| `throb` | 9 | 10 |
| `thrust` | 10 | 11 |
| `cycle` | – | 12 |
| `twist` | – | 13 |
| `random` | 11 | 14 |
| `step` | 12 | 15 |
| `training` | 13 | 16 |

`flo`, `cycle` and `twist` only exist on the beta firmwares. If you choose one
on a 2.106 box, estim-camming refuses to start the device and says so in the log.

estim-camming's own patterns (pulse, wave, ...) shape the A/B levels over time.
The 2B's mode shapes the signal inside the box. `continuous` gives the most
direct control. Other modes combine both effects.

:::{warning}
**Dynamic** power merges the low and high ranges based on the dynamic bias, so
it can get as strong as `high`. Treat it like `high`: start with a low
`max_output` and test without electrodes first.
:::

## First test

1. Run `estim-camming check`. It validates the config without touching the box.
2. Turn the box on, with the electrodes **not** attached.
3. Run `estim-camming run`. The log should show
   `2B connected on /dev/ttyUSB0: firmware … (protocol …), battery …`. At start-up the box is
   set to your power range and mode, and A/B are set to 0.
4. Open the control panel, arm, and send a small test tip. The A/B display on
   the box should follow the output bars in the control panel. Press **STOP**
   and check that A/B go to 0.
5. Only then attach the electrodes, starting with low settings.

## Good to know

- The box answers each command before the next one is sent, so it takes about
  10 changes per second. If levels change faster (fast patterns), in-between
  values are skipped and the newest level is always sent.
- If the cable is unplugged or the box stops answering, estim-camming performs
  an emergency stop and disarms. Fix the connection, restart estim-camming, and
  arm again.
- Changing the power range or mode on the box itself resets A/B to 0. Adjusting
  A/B on the box works, but estim-camming overwrites it on the next change.
- `dynamic` power, `warp` and `ramp` only work on the beta firmwares (see the
  table above). If your box's firmware doesn't have them, estim-camming refuses
  to start the device and says so in the log. When `warp` or `ramp` is not set,
  the box keeps whatever it is set to.
- Switching to dynamic power resets the box's bias, and the default differs per
  firmware (`max` on 2.120B and newer, `A` on 2.119B). Set `bias` to get the same
  behaviour on every box.
- Channel link and output map are not used.
