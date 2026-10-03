"""DG-LAB Coyote 3.0 over Bluetooth LE.

Based on DG-LAB's official V3 protocol (``coyote/v3/README.md`` and the waveform
explanation ``coyote/README.md`` in https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE).
The first part is pure protocol encoding (no Bluetooth, no I/O), tested byte for
byte against the doc's examples. The second part is the ``coyote3`` device
plugin, which talks to the box through bleak (``pip install -e '.[coyote3]'``).

The channel strength (pulse voltage) is a fixed setting, enforced by the box as
its soft limit; the 0..1 level drives the waveform intensity (pulse width). A
B0 frame with 100 ms of waveform goes out every 100 ms. See
``docs/design/coyote3.md``.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator, model_validator

from estim_camming.devices.base import Device, DeviceError
from estim_camming.plugins import DEVICES, PluginOptions

log = logging.getLogger(__name__)

# -- GATT layout ------------------------------------------------------------------------

_BASE_UUID = "0000{:04x}-0000-1000-8000-00805f9b34fb"

#: Advertised name of the Coyote 3.0 pulse host.
DEVICE_NAME = "47L121000"
SERVICE_UUID = _BASE_UUID.format(0x180C)
#: All commands are written here (at most 20 bytes).
WRITE_UUID = _BASE_UUID.format(0x150A)
#: All replies (B1) arrive here as notifications.
NOTIFY_UUID = _BASE_UUID.format(0x150B)
BATTERY_SERVICE_UUID = _BASE_UUID.format(0x180A)
#: Battery level, 1 byte, read / notify.
BATTERY_UUID = _BASE_UUID.format(0x1500)

# -- value ranges -----------------------------------------------------------------------

#: B0 frames are sent every 100 ms; each carries 4 steps of 25 ms per channel.
FRAME_SECONDS = 0.1
STEPS_PER_FRAME = 4
STRENGTH_MAX = 200  # channel strength (pulse voltage), absolute range of the box
INTENSITY_MAX = 100  # waveform intensity (relative pulse width)
FREQUENCY_MIN, FREQUENCY_MAX = 10, 240  # encoded waveform frequency
PERIOD_MIN_MS, PERIOD_MAX_MS = 10, 1000  # waveform frequency as a period in ms
BALANCE_MAX = 255
SEQUENCE_MAX = 15


class StrengthMode(enum.IntEnum):
    """How the box interprets a strength value in B0 (2 bits per channel)."""

    UNCHANGED = 0b00
    INCREASE = 0b01
    DECREASE = 0b10
    ABSOLUTE = 0b11


def _check(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an int in {low}..{high}, got {value!r}")
    return value


# -- waveform -----------------------------------------------------------------------------


def encode_frequency(period_ms: int) -> int:
    """Waveform frequency byte for an output unit of ``period_ms`` (10..1000 ms).

    The official conversion (integer division): 10..100 unchanged,
    101..600 -> (v - 100) / 5 + 100, 601..1000 -> (v - 600) / 10 + 200. Out of
    range is an error here (the official code silently uses 10)."""
    _check("period_ms", period_ms, PERIOD_MIN_MS, PERIOD_MAX_MS)
    if period_ms <= 100:
        return period_ms
    if period_ms <= 600:
        return (period_ms - 100) // 5 + 100
    return (period_ms - 600) // 10 + 200


def period_ms(frequency_hz: float) -> int:
    """Pulse frequency in Hz (1..100) as the output-unit length in ms (1000..10)."""
    if not 1 <= frequency_hz <= 100:
        raise ValueError(f"frequency must be 1..100 Hz, got {frequency_hz!r}")
    return round(1000 / frequency_hz)


@dataclass(frozen=True)
class Waveform:
    """100 ms of one channel: 4 steps of (encoded frequency, intensity)."""

    frequencies: tuple[int, int, int, int]
    intensities: tuple[int, int, int, int]

    def __post_init__(self) -> None:
        if len(self.frequencies) != STEPS_PER_FRAME or len(self.intensities) != STEPS_PER_FRAME:
            raise ValueError(f"a waveform has {STEPS_PER_FRAME} steps")
        for f in self.frequencies:
            _check("frequency", f, FREQUENCY_MIN, FREQUENCY_MAX)
        for i in self.intensities:
            _check("intensity", i, 0, INTENSITY_MAX)

    @classmethod
    def steady(cls, frequency: int, intensities: Sequence[int]) -> Waveform:
        """Same encoded frequency on all steps."""
        return cls((frequency,) * STEPS_PER_FRAME, tuple(intensities))  # type: ignore[arg-type]


#: "No data for this channel": out of range, so the box drops the channel's
#: 4 steps (the form used in the official examples).
_NO_DATA = bytes((0, 0, 0, 0, 0, 0, 0, INTENSITY_MAX + 1))


def _waveform_bytes(wave: Waveform | None) -> bytes:
    if wave is None:
        return _NO_DATA
    return bytes(wave.frequencies) + bytes(wave.intensities)


# -- commands -------------------------------------------------------------------------------


def encode_b0(
    a: Waveform | None,
    b: Waveform | None,
    *,
    sequence: int = 0,
    mode_a: StrengthMode = StrengthMode.UNCHANGED,
    mode_b: StrengthMode = StrengthMode.UNCHANGED,
    strength_a: int = 0,
    strength_b: int = 0,
) -> bytes:
    """The 20-byte B0 frame: strength change and 100 ms of waveform per channel.

    ``None`` sends no waveform for that channel. A non-zero ``sequence`` asks
    the box to confirm the resulting strengths with a B1 of the same number."""
    _check("sequence", sequence, 0, SEQUENCE_MAX)
    _check("strength_a", strength_a, 0, STRENGTH_MAX)
    _check("strength_b", strength_b, 0, STRENGTH_MAX)
    mode_a, mode_b = StrengthMode(mode_a), StrengthMode(mode_b)
    header = bytes((0xB0, sequence << 4 | mode_a << 2 | mode_b, strength_a, strength_b))
    frame = header + _waveform_bytes(a) + _waveform_bytes(b)
    assert len(frame) == 20
    return frame


def encode_bf(
    limit_a: int,
    limit_b: int,
    frequency_balance_a: int,
    frequency_balance_b: int,
    intensity_balance_a: int,
    intensity_balance_b: int,
) -> bytes:
    """The 7-byte BF frame: soft limits and balance parameters. No reply; must be
    written after every (re)connect. The box keeps these values after power-off."""
    _check("limit_a", limit_a, 0, STRENGTH_MAX)
    _check("limit_b", limit_b, 0, STRENGTH_MAX)
    for name, value in (
        ("frequency_balance_a", frequency_balance_a),
        ("frequency_balance_b", frequency_balance_b),
        ("intensity_balance_a", intensity_balance_a),
        ("intensity_balance_b", intensity_balance_b),
    ):
        _check(name, value, 0, BALANCE_MAX)
    return bytes(
        (
            0xBF,
            limit_a,
            limit_b,
            frequency_balance_a,
            frequency_balance_b,
            intensity_balance_a,
            intensity_balance_b,
        )
    )


@dataclass(frozen=True)
class StrengthReport:
    """B1: the box's channel strengths. ``sequence`` 0 = changed on the box (wheel)."""

    sequence: int
    strength_a: int
    strength_b: int

    @property
    def from_wheel(self) -> bool:
        return self.sequence == 0


def parse_b1(data: bytes | bytearray) -> StrengthReport:
    """Decode a B1 notification. Raises ValueError for anything else."""
    if len(data) < 4 or data[0] != 0xB1:
        raise ValueError(f"not a B1 message: {bytes(data).hex()}")
    return StrengthReport(sequence=data[1], strength_a=data[2], strength_b=data[3])


def apply_strength(current: int, mode: StrengthMode, value: int, limit: int = STRENGTH_MAX) -> int:
    """The box's documented reaction to a B0 strength field (for tests and fakes).

    Out-of-range values (above 200) count as 0; results are clamped to
    0..``limit`` (the soft limit, at most 200)."""
    value = value if 0 <= value <= STRENGTH_MAX else 0
    if mode is StrengthMode.UNCHANGED:
        result = current
    elif mode is StrengthMode.INCREASE:
        result = current + value
    elif mode is StrengthMode.DECREASE:
        result = current - value
    else:
        result = value
    return min(max(result, 0), min(limit, STRENGTH_MAX))


# -- level mapping -------------------------------------------------------------------------

#: Levels below this count as off and send intensity 0 (as the 2B plugin).
OFF_THRESHOLD = 0.001


def to_intensity(level: float, low: int = 0, high: int = INTENSITY_MAX) -> int:
    """Normalised level (0..1) -> waveform intensity: 0 when off, otherwise
    ``low``..``high`` (each 0..100)."""
    if level < OFF_THRESHOLD:
        return 0
    return round(low + min(level, 1.0) * (high - low))


def interpolate(previous: int, target: int) -> tuple[int, int, int, int]:
    """Four 25 ms steps from ``previous`` towards ``target``.

    Increases are spread over the steps, ending at the target; every step lies
    between the two values, so a ramp approved by the safety guard is never
    overshot. Decreases (including a stop) are immediate on all steps, as the
    guard applies them (S5)."""
    if target <= previous:
        return (target,) * STEPS_PER_FRAME  # type: ignore[return-value]
    steps = tuple(
        round(previous + (target - previous) * (i + 1) / STEPS_PER_FRAME)
        for i in range(STEPS_PER_FRAME)
    )
    return steps  # type: ignore[return-value]


# -- the device plugin ---------------------------------------------------------------------

CHANNELS = ("A", "B")

PerChannel = int | dict[str, int]
PerChannelFloat = float | dict[str, float]

#: (low, high) of the per-channel options, for validation.
_RANGES: dict[str, tuple[float, float]] = {
    "strength": (0, STRENGTH_MAX),
    "strength_limit": (0, STRENGTH_MAX),
    "min_output": (0, INTENSITY_MAX),
    "max_output": (1, INTENSITY_MAX),
    "frequency": (1, 100),
    "frequency_balance": (0, BALANCE_MAX),
    "intensity_balance": (0, BALANCE_MAX),
}


def _pick(value: Any, channel: str) -> Any:
    return value.get(channel) if isinstance(value, dict) else value


#: Connects to the box and returns a connected client (bleak's BleakClient API).
#: Replaced in tests with a fake box.
ClientFactory = Callable[["Coyote3Device.Options", Callable[[Any], None]], Awaitable[Any]]


async def _open_bleak(options: Coyote3Device.Options, on_disconnect: Callable[[Any], None]) -> Any:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise DeviceError(
            "the coyote3 device needs the bleak library (install with: "
            f"pip install -e '.[coyote3]'); import failed: {exc!r}"
        ) from exc
    try:
        if options.address:
            device = await BleakScanner.find_device_by_address(
                options.address, timeout=options.scan_timeout
            )
        else:
            device = await BleakScanner.find_device_by_name(
                options.name, timeout=options.scan_timeout
            )
    except Exception as exc:  # BleakError, D-Bus errors: Bluetooth off or missing
        raise DeviceError(f"Bluetooth scan failed (is Bluetooth switched on?): {exc!r}") from exc
    if device is None:
        target = options.address or f"named {options.name!r}"
        raise DeviceError(
            f"no Coyote 3.0 {target} found within {options.scan_timeout:g} s. Is it switched "
            "on, and disconnected from the DG-LAB app?"
        )
    client = BleakClient(
        device, disconnected_callback=on_disconnect, timeout=options.connect_timeout
    )
    try:
        await client.connect()
    except Exception as exc:
        raise DeviceError(f"cannot connect to the Coyote 3.0 ({device.address}): {exc!r}") from exc
    return client


@DEVICES.register("coyote3")
class Coyote3Device(Device):
    """DG-LAB Coyote 3.0 over Bluetooth LE (bleak)."""

    class Options(PluginOptions):
        address: str | None = Field(
            None, description="Bluetooth address of the box; unset: find it by name."
        )
        name: str = Field(DEVICE_NAME, description="Advertised name to scan for.")
        scan_timeout: float = Field(10.0, gt=0, description="Seconds to find the box.")
        connect_timeout: float = Field(10.0, gt=0, description="Seconds to connect.")
        strength: PerChannel = Field(
            description="Channel strength (pulse voltage, 0-200) when output is on. "
            "A number, or per channel: { A = 20, B = 15 }. Start low."
        )
        strength_limit: PerChannel | None = Field(
            None,
            description="Soft limit (0-200) the box enforces, also against its wheel. "
            "Default: strength. Stored in the box.",
        )
        min_output: PerChannel = Field(
            0, description="Pulse width (0-100) for the lowest non-zero level; off stays off."
        )
        max_output: PerChannel = Field(
            100, description="Pulse width (1-100) at full scale (level 1.0)."
        )
        frequency: PerChannelFloat = Field(50.0, description="Pulse frequency in Hz (1-100).")
        frequency_balance: PerChannel = Field(
            160, description="Box setting 0-255: higher = stronger low frequencies. Stored."
        )
        intensity_balance: PerChannel = Field(
            0, description="Box setting 0-255: pulse width balance. Stored in the box."
        )
        stall_timeout: float = Field(
            1.0, gt=0, description="No successful write for this long = device fault."
        )
        reply_timeout: float = Field(
            0.5, gt=0, description="Seconds to wait for the box to confirm a strength change."
        )

        @field_validator(
            "strength",
            "strength_limit",
            "min_output",
            "max_output",
            "frequency",
            "frequency_balance",
            "intensity_balance",
        )
        @classmethod
        def _check_range(cls, value: Any, info) -> Any:
            if value is None:
                return value
            values = value if isinstance(value, dict) else {"all": value}
            unknown = set(values) - set(CHANNELS) - {"all"}
            if unknown:
                raise ValueError(f"unknown channel(s) {sorted(unknown)}; the Coyote has A and B")
            if isinstance(value, dict) and set(values) != set(CHANNELS):
                raise ValueError("a per-channel table needs both A and B")
            low, high = _RANGES[info.field_name]
            for ch, v in values.items():
                if not low <= v <= high:
                    raise ValueError(f"{info.field_name}[{ch}] must be {low:g}-{high:g}")
            return value

        def per_channel(self, field: str, channel: str) -> Any:
            if field == "strength_limit" and self.strength_limit is None:
                return _pick(self.strength, channel)
            return _pick(getattr(self, field), channel)

        @model_validator(mode="after")
        def _check_order(self):
            for ch in CHANNELS:
                strength, limit = (
                    self.per_channel("strength", ch),
                    self.per_channel("strength_limit", ch),
                )
                if strength > limit:
                    raise ValueError(f"strength ({strength}) > strength_limit ({limit}) for {ch}")
                low, high = self.per_channel("min_output", ch), self.per_channel("max_output", ch)
                if low > high:
                    raise ValueError(f"min_output ({low}) > max_output ({high}) for {ch}")
            return self

    #: Opens the connection; replaced in tests with a fake box.
    client_factory: ClientFactory = staticmethod(_open_bleak)
    #: Seconds between B0 frames (the protocol's 100 ms; shorter in tests).
    frame_seconds: float = FRAME_SECONDS

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self._client: Any = None
        self._lock = asyncio.Lock()  # one write at a time: writer task vs stop()
        self._worker: asyncio.Task[None] | None = None
        self._target = dict.fromkeys(CHANNELS, 0)  # waveform intensity per channel
        self._sent = dict.fromkeys(CHANNELS, 0)
        self._strength_on = False  # the configured strength has been sent since the last stop
        self._sequence = 0
        self._awaiting: tuple[int, asyncio.Future[StrengthReport]] | None = None
        self._confirming: asyncio.Task[None] | None = None
        self.strengths = dict.fromkeys(CHANNELS, 0)  # as last reported by the box (B1)
        self.battery: int | None = None
        self._error: str | None = None
        self._last_write = 0.0
        opts = self.options
        self._frequency = {
            ch: encode_frequency(period_ms(opts.per_channel("frequency", ch))) for ch in CHANNELS
        }

    @property
    def channels(self) -> tuple[str, ...]:
        return CHANNELS

    def to_box(self, level: float, channel: str) -> int:
        """Normalised level -> waveform intensity (pulse width) of ``channel``."""
        opts = self.options
        return to_intensity(
            level, opts.per_channel("min_output", channel), opts.per_channel("max_output", channel)
        )

    # -- Device interface ----------------------------------------------------------

    async def connect(self) -> None:
        opts = self.options
        self._error = None
        self._client = await self.client_factory(opts, self._on_disconnect)
        try:
            await self._setup()
        except BaseException:
            await self._close()
            raise
        self._worker = asyncio.create_task(self._run(), name="coyote3-writer")

    async def _setup(self) -> None:
        opts, client = self.options, self._client
        services = client.services
        missing = [
            uuid for uuid in (WRITE_UUID, NOTIFY_UUID) if services.get_characteristic(uuid) is None
        ]
        if missing:
            raise DeviceError(f"not a Coyote 3.0: characteristic(s) {missing} missing")
        try:
            await client.start_notify(NOTIFY_UUID, self._on_notify)
        except Exception as exc:
            raise DeviceError(f"Coyote 3.0: cannot subscribe to replies: {exc!r}") from exc
        if services.get_characteristic(BATTERY_UUID) is not None:
            try:
                self.battery = (await client.read_gatt_char(BATTERY_UUID))[0]
            except Exception:
                log.warning("Coyote 3.0: could not read the battery level", exc_info=True)
        # Soft limits and balance first: the doc requires BF after every (re)connect.
        bf = encode_bf(
            *(opts.per_channel("strength_limit", ch) for ch in CHANNELS),
            *(opts.per_channel("frequency_balance", ch) for ch in CHANNELS),
            *(opts.per_channel("intensity_balance", ch) for ch in CHANNELS),
        )
        await self._write(bf)
        # Then zero the strength and wait until the box confirms it.
        await self._confirm(await self._send_strength(0, 0), 0, 0)
        log.info(
            "Coyote 3.0 connected: battery %s%%, strength %s/%s (limit %s/%s), %s Hz",
            self.battery,
            *(opts.per_channel("strength", ch) for ch in CHANNELS),
            *(opts.per_channel("strength_limit", ch) for ch in CHANNELS),
            "/".join(f"{opts.per_channel('frequency', ch):g}" for ch in CHANNELS),
        )

    async def set_levels(self, levels: Mapping[str, float]) -> None:
        self._check_healthy()
        self._target = {ch: self.to_box(levels.get(ch, 0.0), ch) for ch in CHANNELS}

    async def stop(self) -> None:
        """Strength 0 and no pulses on both channels at once, outside the 100 ms rhythm."""
        self._target = dict.fromkeys(CHANNELS, 0)
        if self._client is None:
            return
        await self._confirm(await self._send_strength(0, 0), 0, 0)
        log.info("Coyote 3.0: strength 0")

    async def disconnect(self) -> None:
        for task in (self._worker, self._confirming):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._worker = self._confirming = None
        if self._client is None:
            return
        if self._error is None:
            try:
                await self.stop()
            except DeviceError:
                log.critical(
                    "could not zero the Coyote on disconnect - check the box!", exc_info=True
                )
        await self._close()

    # -- internals -----------------------------------------------------------------

    async def _close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            log.warning("Coyote 3.0: error while disconnecting", exc_info=True)

    def _check_healthy(self) -> None:
        if self._client is None:
            raise DeviceError("Coyote 3.0 is not connected")
        if self._error is not None:
            raise DeviceError(self._error)
        silent = time.monotonic() - self._last_write
        if silent > self.options.stall_timeout:
            raise DeviceError(f"Coyote 3.0: nothing sent for {silent:.1f} s")

    def _fail(self, message: str) -> None:
        if self._error is None:
            self._error = message
            log.error("%s", message)

    def _on_disconnect(self, _client: Any) -> None:
        self._fail("Coyote 3.0: Bluetooth connection lost")
        if self._awaiting is not None and not self._awaiting[1].done():
            self._awaiting[1].set_exception(DeviceError("Bluetooth connection lost"))

    def _on_notify(self, _characteristic: Any, data: bytearray) -> None:
        if not data or data[0] != 0xB1:
            return
        try:
            report = parse_b1(data)
        except ValueError:
            log.warning("Coyote 3.0: malformed reply %s", bytes(data).hex())
            return
        self.strengths = {"A": report.strength_a, "B": report.strength_b}
        for ch, value in self.strengths.items():
            if value > self.options.per_channel("strength_limit", ch):
                self._fail(f"Coyote 3.0 reports strength {ch}={value} above its soft limit")
        if report.from_wheel:
            log.info("Coyote 3.0 wheel: strength A=%d B=%d", report.strength_a, report.strength_b)
        elif self._awaiting is not None and report.sequence == self._awaiting[0]:
            future = self._awaiting[1]
            self._awaiting = None
            if not future.done():
                future.set_result(report)

    def _next_sequence(self) -> int:
        self._sequence = self._sequence % SEQUENCE_MAX + 1  # 1..15; 0 means "no reply"
        return self._sequence

    async def _write(self, data: bytes) -> None:
        # Bounded: a hanging Bluetooth stack must not block a stop or shutdown.
        timeout = self.options.stall_timeout
        try:
            await asyncio.wait_for(
                self._client.write_gatt_char(WRITE_UUID, data, response=False), timeout
            )
        except TimeoutError as exc:
            self._fail(f"Coyote 3.0: write timed out after {timeout:g} s")
            raise DeviceError(self._error) from exc
        except Exception as exc:
            self._fail(f"Coyote 3.0: write failed: {exc!r}")
            raise DeviceError(self._error) from exc
        self._last_write = time.monotonic()

    def _waveform(self, channel: str) -> Waveform:
        steps = interpolate(self._sent[channel], self._target[channel])
        self._sent[channel] = self._target[channel]
        return Waveform.steady(self._frequency[channel], steps)

    async def _send_strength(
        self, strength_a: int, strength_b: int
    ) -> asyncio.Future[StrengthReport]:
        """Set both strengths absolutely, in a frame with the current waveform.
        Returns the future of the box's confirmation (B1 with the same sequence
        number). A newer strength change supersedes an unconfirmed older one."""
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._awaiting is not None:
                self._awaiting[1].cancel()  # superseded, not a fault
            sequence = self._next_sequence()
            future: asyncio.Future[StrengthReport] = loop.create_future()
            self._awaiting = (sequence, future)
            if strength_a == strength_b == 0:
                self._sent = dict.fromkeys(CHANNELS, 0)
                self._strength_on = False
            frame = encode_b0(
                self._waveform("A"),
                self._waveform("B"),
                sequence=sequence,
                mode_a=StrengthMode.ABSOLUTE,
                mode_b=StrengthMode.ABSOLUTE,
                strength_a=strength_a,
                strength_b=strength_b,
            )
            await self._write(frame)
        return future

    async def _confirm(
        self, future: asyncio.Future[StrengthReport], strength_a: int, strength_b: int
    ) -> None:
        """Wait for the box to confirm a strength change; a missing or different
        confirmation is a device fault. Raises CancelledError if superseded."""
        try:
            report = await asyncio.wait_for(future, self.options.reply_timeout)
        except TimeoutError:
            self._fail(
                f"Coyote 3.0 did not confirm strength {strength_a}/{strength_b} "
                f"within {self.options.reply_timeout:g} s"
            )
            raise DeviceError(self._error) from None
        finally:
            if self._awaiting is not None and self._awaiting[1] is future:
                self._awaiting = None
        if (report.strength_a, report.strength_b) != (strength_a, strength_b):
            self._fail(
                f"Coyote 3.0 set strength {report.strength_a}/{report.strength_b}, "
                f"expected {strength_a}/{strength_b}"
            )
            raise DeviceError(self._error)

    async def _confirm_in_background(self, future, strength_a: int, strength_b: int) -> None:
        try:
            await self._confirm(future, strength_a, strength_b)
        except (asyncio.CancelledError, DeviceError):
            return  # superseded, or recorded in self._error
        log.info("Coyote 3.0: strength on (A=%d, B=%d)", strength_a, strength_b)

    async def _run(self) -> None:
        """Send a B0 frame every 100 ms with the newest target; the box needs the
        stream (each frame is 100 ms of waveform)."""
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        try:
            while self._error is None:
                if any(self._target.values()) and not self._strength_on:
                    # First output since connect or a stop: switch the strength on. The
                    # confirmation is awaited alongside, so the 100 ms stream never pauses.
                    self._strength_on = True
                    on = [self.options.per_channel("strength", ch) for ch in CHANNELS]
                    future = await self._send_strength(*on)
                    self._confirming = asyncio.create_task(
                        self._confirm_in_background(future, *on), name="coyote3-confirm"
                    )
                else:
                    async with self._lock:
                        await self._write(encode_b0(self._waveform("A"), self._waveform("B")))
                next_at += self.frame_seconds
                delay = next_at - loop.time()
                if delay < 0:  # fell behind (slow Bluetooth): don't burst to catch up
                    next_at = loop.time()
                    delay = 0
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except DeviceError:
            pass  # recorded in self._error; the next set_levels() raises
        except Exception as exc:
            self._fail(f"Coyote 3.0 writer stopped: {exc!r}")
