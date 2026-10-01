# Safety first

:::{warning}
This software lets **other people** trigger electrical stimulation on your body.
Read this page before connecting a real device.
:::

## Physical safety

Follow the manual of your device. In particular, the standard e-stim rules apply:

- **Never** place electrodes so that current can cross the chest or heart, or on
  the head or neck. Keep all electrodes below the waist.
- Do not use e-stim if you have a pacemaker or other implanted electronics, a heart
  condition, epilepsy, or are pregnant, unless your doctor has said it is safe.
- Do not use it on broken or irritated skin, or while under the influence of
  alcohol or drugs.
- Only use devices designed for body use. Never improvise with mains-powered
  equipment.
- Keep the device's own power controls and power switch **within reach** at all
  times. Software is not a substitute for a hardware off switch.

## How the software protects you

- **Starts disarmed.** Nothing happens until you press **Arm** in the control
  panel. While disarmed, tips are shown but produce no output and are not saved
  for later.
- **Your maximum.** `safety.max_level` is the strongest output any tip can cause
  (as a fraction of the device's maximum). All tip menu intensities are relative
  to it. Start low.
- **Master level slider** in the control panel lowers everything live.
- **Soft start.** Output rises gradually (`max_change_per_second`) and never jumps.
- **Time limit.** No single action runs longer than `max_action_seconds`.
- **Emergency stop.** The big **STOP** button (or **Esc** / **Space** with the
  control panel focused) turns everything off, disarms, and clears the queue.
  **Ctrl+C** in the terminal also stops the device before exiting.
- **Device problems stop output.** If the device stops responding, the app
  performs an emergency stop.

## Before every show

1. Start with the `dummy` device and the `simulator` platform to check your tip
   menu (see [Quick start](quickstart.md)).
2. With the real device, set `max_level` low. Arm, send test tips from the control
   panel, and increase only as far as you are comfortable.
3. Keep the control panel open on a screen you can reach, and test the STOP button.
4. Check your device's own power setting as an extra hardware limit.
