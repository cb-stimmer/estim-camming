"""DG-LAB Coyote 3.0 over Bluetooth LE: protocol encoding.

Based on DG-LAB's official V3 protocol (``coyote/v3/README.md`` and the waveform
explanation ``coyote/README.md`` in https://github.com/DG-LAB-OPENSOURCE/DG-LAB-OPENSOURCE).
Pure functions only (no Bluetooth, no I/O), so every byte can be tested against
the doc's examples. The device plugin built on them follows in phase 2. See
``docs/design/coyote3.md``.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

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
