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

E-Stim Systems 2B through its serial link, using
[estim2py](https://github.com/sissybecky/estim2py) (public domain,
`pip install -e '.[estim2b]'`). The protocol itself is described in
[STPIHKAL](https://buttplug.io/stpihkal/protocols/estim-systems/). We only use the
library's documented API: `Estim2pyConnection(port, timeout, delay)`,
`get_status`, `kill`, `low`, `high`, `set_mode`, `set_channel`.

- **Library versions.** estim2py 0.3.0 imports `warnings.deprecated` (3.13) and
  uses 3.12 syntax, although its metadata says `>=3.8`. The `estim2b` extra
  therefore pins `estim2py==0.2.2` on Python < 3.13 and `>=0.3.0` on 3.13+
  (environment markers). 0.2.2 has the same API for everything we use. Checked
  differences from 0.3.0:
  - 0.2.2 doesn't clear the serial input buffer before a command, so a late reply
    could shift every following reply by one. The plugin calls
    `serial.reset_input_buffer()` before each command itself (harmless with 0.3.0).
  - 0.2.2's reply parser raises plain `ValueError`/`IndexError` on a timeout or
    garbled reply instead of `Estim2pyError`. That is still an exception, so it
    is still a fault.
  - The default `delay` differs (0.10 vs 0.04). We always pass it explicitly.
  The library is imported lazily in `connect()`. A failed import becomes a
  `DeviceError` with install instructions.
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
- **Connect** (in the worker thread): open → `get_status` (is it there?) →
  `kill` → `low`/`high` → `set_mode` → C/D parameters if configured → A=B=0 →
  `get_status`. Changing power or mode resets A/B on the box. If the reported
  power range or mode differs from the requested one, the device calls `kill` and
  raises `DeviceError`. This refuses, for example, a box stuck in high power.
- **Faults.** A library exception in the writer, or a command in flight longer
  than `stall_timeout`, makes the next `set_levels()` raise, so the safety
  guard performs an emergency stop (S8). Exceptions are formatted with their
  type and `args`: `Estim2pyError.__str__` in 0.3.0 references a missing
  attribute and would raise.
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
  installed, or on Windows). Verified with estim2py 0.2.2 on Python 3.12. It was
  not tested with a real 2B in this repository.

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
