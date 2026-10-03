# Devices

A *device* turns normalised output levels into hardware commands.

## Level model

- A device has one or more named **channels** (for example `("A", "B")` for a
  two-channel e-stim unit).
- Levels are floats in **0..1**, a fraction of the device's full scale.
  `0` means off. Mapping to native units (power steps, mA, 0–200, …) is the
  device plugin's job.
- Levels arrive only from `SafetyGuard`, and are already capped, scaled and
  rate-limited. The device must still clamp to its own hardware limits.
- `set_levels` always receives **every** channel.

## Interface

```python
class Device(Plugin):
    @property
    def channels(self) -> tuple[str, ...]: ...                  # abstract
    async def connect(self) -> None: ...                        # optional
    async def disconnect(self) -> None: ...                     # optional, idempotent
    async def set_levels(self, levels: Mapping[str, float]) -> None: ...  # abstract
    async def stop(self) -> None: ...                           # default: all channels to 0
```

Contract:

- `connect()` is called once before any output and raises `DeviceError` on
  failure, which aborts start-up.
- `set_levels()` is called at up to the scheduler tick rate (default 20 Hz). It
  must return quickly. The guard treats a call that takes longer than
  `safety.device_timeout` (default 2 s) as a fault.
- **Any exception** from `set_levels()` makes the guard perform an emergency stop
  and disarm. The device should therefore raise on lost connections instead of
  silently ignoring them.
- `stop()` must drive all outputs to zero as fast as possible. Override it when
  the hardware has a dedicated "all off" command.
- Devices that need a periodic keep-alive or waveform stream (for example BLE
  units that expect a packet every 100 ms) run their own background task, started
  in `connect()` and cancelled in `disconnect()`, and send the most recent levels.
  If that task fails, the next `set_levels()` must raise.
- If the hardware has its own safety features (power limits, auto-off when
  packets stop), enable them.

## Built-in devices

### `dummy`

Stores levels in memory and logs whenever a channel moves by `log_step` or
returns to zero. `channels` is configurable. Used for testing and demos.

### `estim2b`

E-Stim Systems 2B through its serial link, using estim2py (public domain,
`pip install -e '.[estim2b]'`): the user's fork
[cb-stimmer/estim2py](https://github.com/cb-stimmer/estim2py) at tag `v0.4.1`,
of [sissybecky/estim2py](https://github.com/sissybecky/estim2py). The protocol itself is described in
[STPIHKAL](https://buttplug.io/stpihkal/protocols/estim-systems/). We only use the
library's documented API: `Estim2pyConnection(port, timeout, delay)`,
`get_status`, `kill`, `low`, `high`, `set_mode`, `set_channel`, and the
connection's `protocol.name`.

- **Library version.** The fork is not on PyPI, so the `estim2b` extra is a
  direct git reference (`estim2py @ git+https://…@v0.4.1`, Python ≥ 3.12; hatch
  needs `allow-direct-references`). Upstream 0.3.0 needed Python 3.13
  (`warnings.deprecated`); the fork removed that. The fork also adds the beta
  firmware protocols (see below). The library is imported lazily in `connect()`.
  A failed import becomes a `DeviceError` with install instructions. The plugin
  still calls `serial.reset_input_buffer()` before each command (the library
  does too; harmless).
- **Firmware.** Opening the connection sends one status request, and estim2py
  picks the protocol from the number of fields in the reply: `2.106` (9 fields),
  `2.119B` (11) or `2.120B` (13, also later 2.1xxB). The beta firmwares number
  the modes differently and add `flo`, `cycle` and `twist`, so the plugin keeps
  both tables (`MODES`, `BETA_MODES`, copied from estim2py's `Estim2pyMode`) and
  resolves the configured mode against the detected firmware in `connect()`.
  Config validation accepts any name or number from the beta table. A mode the
  box's firmware lacks (for example `flo` on 2.106) is a `DeviceError` after
  `kill`, before `set_mode`. A mode number is sent as is (the firmware's own
  numbering). A connection without `protocol` counts as 2.106.
- **Beta firmware options.** `power = "dynamic"` sends `Y` (`dynamic()`, 2.119B+).
  Dynamic power merges the low and high ranges based on the dynamic bias
  (`A`, `B`, `average`, `max`; per the user), so it can reach high-power
  strength. `bias` (only valid with dynamic power) is sent as
  `set_bias(Estim2pyBias(...))` *after* `Y`, because `Y` resets the bias to raw
  0, and 0 means `max` on 2.120B+ but `A` on 2.119B. The library maps the name to
  the firmware's number. Unset `bias` keeps that firmware-dependent default.
  `warp` (×1, 2, 4, 8, 16, 32 → `W0`–`W5`) and `ramp` (×1–×4 → `R0`–`R3`) need
  2.120B+. Unset `warp`/`ramp` (the default) leaves the box's own setting alone.
  Support comes from estim2py's
  `protocol.supports("dynamic" | "bias" | "warp" | "ramp")`. A configured option
  the firmware lacks is a `DeviceError` after `kill`, before any other command,
  like a missing mode. **Open question:** the firmware documents don't describe
  what warp and ramp do exactly, so only the documented values are exposed.
  Output map and link are not used.
- **Channels** `A`, `B`. The level maps to box units (integers 0–100) as
  `0` if `level < 0.001` (off), else `round(min_output + level × (max_output −
  min_output))`. Both options are an int or a per-channel table
  (`{ A = 10, B = 15 }`), validated as 0 ≤ min ≤ max ≤ 100 and max ≥ 1 per
  channel. The 0.1 % off threshold ensures `min_output` never keeps the box on
  when the output should be off. A non-zero level therefore starts at
  `min_output`, above the guard's soft start: an intended jump to the sensation
  threshold. `stop()` (`kill`) and all-zero levels always give 0. The box reports
  levels doubled (0–200), and the library halves them.
- **Blocking I/O.** Every library call writes a command, sleeps `delay` (≥ 0.034 s,
  the time to send 33 bytes at 9600 baud) and reads the status reply. All calls
  therefore run in `asyncio.to_thread`, serialised by a `threading.Lock`.
- **Latest value wins.** `set_levels()` only stores the target (converted to box
  units) and wakes a writer task, which sends changed channels one by one.
  Targets that change while a command is in flight are coalesced, so the box
  never works through a backlog of stale levels. That is about 10 updates/s for
  two channels at the default delay.
- **Connect** (in the worker thread): open (estim2py's firmware probe) →
  `get_status` (is it there?) → `kill` → resolve the mode number for the
  firmware and check the beta options → `low`/`high`/`dynamic` → `set_mode` →
  C/D parameters if configured → `set_warp`/`set_ramp`/`set_bias` if configured →
  A=B=0 → `get_status`. Changing power or mode resets A/B on the box. Mode changes
  keep warp, ramp and bias. If the reported power (`L`/`H`/`D`), mode, warp, ramp
  or bias (raw number, compared with `protocol.bias_code`) differs
  from the requested one, the device calls `kill` and raises `DeviceError`. This
  refuses, for example, a box stuck in high power.
- **Faults.** A library exception in the writer, or a command in flight longer
  than `stall_timeout`, makes the next `set_levels()` raise, so the safety
  guard performs an emergency stop (S8). Exceptions are formatted with their
  type and `args`, which is readable for any exception type.
- **Stop** is the box's `K` command (`kill`: A and B to 0, other settings kept),
  run under the same lock, so it waits at most for the command in flight.
- **Not used:** channel link (`link`/`unlink`), which is unreliable per the
  library author.
- **Hardware caveat:** the serial protocol has no keep-alive or watchdog (none
  is documented in STPIHKAL or estim2py). Assume the box holds its last levels if
  the computer stops sending, so its own controls and power switch remain the
  last line of defence (see [Safety](safety.md)). Not verified on hardware.
- **Testing:** `tests/test_estim2b.py` replaces `connection_factory` with a fake
  box that mimics the library's behaviour (doubled levels, A/B reset on
  power/mode change). `tests/test_estim2b_serial.py` runs the real library and
  pyserial against a fake box on a pseudo-terminal (skipped if estim2py isn't
  installed, or on Windows), once as a 2.106 box and once as a 2.131B box
  (13-field status). Verified with estim2py 0.4.1 on Python 3.12.
  **Hardware check 2026-10-01:** the user tested a real 2B (without electrodes):
  basic control and `min_output` work as expected. Per-channel rules (ADR-012)
  have not been checked on hardware yet.

## Adding a device

1. Create `src/estim_camming/devices/<device>.py` with a `Device` subclass
   registered via `@DEVICES.register("<name>")`, or ship it as a separate package
   with an entry point (see [Plugin system](plugins.md)).
2. Import it in `devices/__init__.py` (built-ins only).
3. Put optional hardware dependencies (for example `bleak` for Bluetooth LE,
   `pyserial-asyncio` for serial) in an optional-dependency group in
   `pyproject.toml`, and import them inside the module so the core install stays
   light.
4. Test with a fake transport: verify level mapping, `stop()`, and that transport
   errors raise.

Candidate devices: DG-LAB Coyote (v2/v3, BLE), other serial/Arduino-controlled boxes,
and devices reachable through [Buttplug/Intiface](https://buttplug.io) (a single
adapter would cover many toys). Protocol details must come from official
documentation or published protocol specs, not guesses.
