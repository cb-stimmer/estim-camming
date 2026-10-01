# E-Stim Systems 2B setup

estim-camming controls the 2B through its serial link (the "link cable" or USB
serial adapter), using the [estim2py](https://github.com/sissybecky/estim2py)
library.

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

  On Python 3.13 or newer this installs estim2py 0.3.0. On older Pythons (such
  as 3.12) it installs 0.2.2, because 0.3.0 only works on 3.13, even though its
  package information says otherwise. Both versions work with estim-camming.

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
power = "low"          # "low" or "high"
mode = "continuous"    # see the table below
min_output = 10        # box level for the weakest output you can feel (0 = off stays off)
max_output = 40        # box level (0-100) at full scale: a hard ceiling
# param_c = 50         # mode parameter C (usually speed), 2-100
# param_d = 50         # mode parameter D (usually feel), 1-100
```

| Option | Default | Meaning |
|---|---|---|
| `port` | (required) | Serial port of the link cable |
| `power` | `low` | Power range of the box. `high` is much stronger |
| `mode` | `continuous` | 2B mode, by name or number |
| `param_c` | box default | Mode parameter C (usually speed), 2–100 |
| `param_d` | box default | Mode parameter D (usually feel), 1–100 |
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

| Name | No. | Name | No. |
|---|---|---|---|
| `pulse` | 0 | `squeeze` | 7 |
| `bounce` | 1 | `milk` | 8 |
| `continuous` | 2 | `throb` | 9 |
| `asplit` | 3 | `thrust` | 10 |
| `bsplit` | 4 | `random` | 11 |
| `wave` | 5 | `step` | 12 |
| `waterfall` | 6 | `training` | 13 |

estim-camming's own patterns (pulse, wave, ...) shape the A/B levels over time.
The 2B's mode shapes the signal inside the box. `continuous` gives the most
direct control. Other modes combine both effects.

## First test

1. Run `estim-camming check`. It validates the config without touching the box.
2. Turn the box on, with the electrodes **not** attached.
3. Run `estim-camming run`. The log should show
   `2B connected on /dev/ttyUSB0: firmware …, battery …`. At start-up the box is
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
- Channel link (`link`/`unlink`) is not used, because it doesn't work reliably
  in estim2py 0.3.0.
