# Safety

Electrical stimulation can hurt people. Viewers, not the performer, decide when
output happens, so the software must keep the performer in control and fail
safe. **This page defines invariants that every change must preserve.** Tests in
`tests/test_safety.py` and `tests/test_scheduler.py` cover them. When changing
safety behaviour, update this page and the tests together.

## Invariants

S1. **Single path to the device.** Only `SafetyGuard` calls `Device.set_levels()`
    and `Device.stop()` during operation. No other component holds a reference it
    uses for output.

S2. **Disarmed means zero.** While disarmed, the guard sends 0 on every channel
    regardless of the request, and the scheduler does not accept new actions.

S3. **Start disarmed.** By default (`safety.start_armed = false`) the app starts
    disarmed and the performer must arm it explicitly from the control panel.

S4. **Bounded output.** The output on channel *c* is

    ```text
    out(c) = clamp(requested(c), 0, 1) × scale × channel_max(c)
    channel_max(c) = safety.channel_max_level[c]  if set, else safety.max_level
    ```

    `scale` is the live master level (0..1) from the control panel. It can only
    reduce output below the configured maxima, never raise it above them.

S5. **Soft start.** Increases are limited to `max_change_per_second × dt` per
    update, with `dt` capped at 0.1 s so a long idle gap never allows a jump.
    Decreases are applied immediately.

S6. **Bounded duration.** No action lasts longer than
    `safety.max_action_seconds`.

S7. **Emergency stop.** `emergency_stop()`:
    1. clears the armed flag synchronously, so the scheduler stops at its next tick,
    2. runs disarm callbacks, which clear the queue,
    3. calls `Device.stop()` under the device lock, with a timeout,
    4. publishes `SafetyStateChanged`.
    Re-arming is always a separate, explicit action.

S8. **Faults stop output.** An exception or timeout (`device_timeout`) from the
    device triggers an emergency stop. If `Device.stop()` itself fails, a CRITICAL
    log tells the performer to check the device physically.

S9. **Stop on exit.** However the application ends (Ctrl+C, SIGTERM, crash in a
    task), `Application.run()` calls `guard.shutdown()` (emergency stop) and
    `device.disconnect()` in a `finally` block.

S10. **Protected controls.** The control API only accepts `POST` requests with
     `Content-Type: application/json` and no foreign `Origin`, so other web pages
     open in the performer's browser cannot arm the device (CSRF). If
     `overlay.control_token` is set it is required. Listening on anything other
     than loopback *requires* a token (validated in config).

S11. **No forged tips.** A forged tip is a way to trigger the device.
     - Platforms that accept tips over a local socket (the browser bridge) listen
       on 127.0.0.1 only, require a secret token of 16+ characters (example
       placeholders are rejected), and accept only `application/json` POSTs.
     - Prefer structured tip data over chat text. The Stripchat bridge defaults
       to `websocket` mode, where only messages the site marks as tips count, so
       chat text can never become a tip. Text-based (`dom`) parsing is a fallback
       and must match tip-specific elements only.
     - Inside the page, the injected hook talks to the userscript only via
       `postMessage` with a random per-page nonce, and the userscript checks the
       nonce and that the message comes from the page's own origin.
     - The bridge `room` option stops tips from other rooms the performer is
       browsing.

## Controls available to the performer

- Control panel **STOP** button, plus keyboard shortcuts **Esc** and **Space**.
- **Arm** button (the only way to arm unless `start_armed`).
- **Master level** slider (S4 `scale`).
- **Skip current** and **Clear queue**.
- Ctrl+C in the terminal (S9).

## Known limitations

- The software cannot detect physical problems (electrode placement, a loose
  pad). The user guide's safety page covers usage rules.
- If the computer freezes, the software cannot stop the device. Devices that
  stop output when commands stop arriving should have that feature enabled.
  Device plugins should use it where the hardware supports it.
- A hardware stop within reach (power switch, unplugging) is still required.
- The E-Stim 2B serial protocol has no documented keep-alive: assume that if
  the computer stops sending, the box keeps its last levels until they are
  changed on the box.
