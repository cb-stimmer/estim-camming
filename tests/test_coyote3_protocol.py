"""Coyote 3.0 protocol encoding, checked against the examples in DG-LAB's
official V3 protocol docs (coyote/v3/README.md, coyote/v3/example.md and the
waveform explanation coyote/README.md)."""

import pytest

from estim_camming.devices.coyote3 import (
    NOTIFY_UUID,
    SERVICE_UUID,
    WRITE_UUID,
    StrengthMode,
    Waveform,
    apply_strength,
    encode_b0,
    encode_bf,
    encode_frequency,
    interpolate,
    parse_b1,
    period_ms,
    to_intensity,
)

INC, DEC, ABS = StrengthMode.INCREASE, StrengthMode.DECREASE, StrengthMode.ABSOLUTE


def wave(frequencies, intensities):
    return Waveform(tuple(frequencies), tuple(intensities))


def test_uuids():
    assert SERVICE_UUID == "0000180c-0000-1000-8000-00805f9b34fb"
    assert WRITE_UUID == "0000150a-0000-1000-8000-00805f9b34fb"
    assert NOTIFY_UUID == "0000150b-0000-1000-8000-00805f9b34fb"


# -- B0: the doc's complete frames ---------------------------------------------------


@pytest.mark.parametrize(
    ("a", "hex_frame"),
    [
        (wave([10] * 4, [0, 10, 20, 30]), "B00000000A0A0A0A000A141E0000000000000065"),
        (wave([15] * 4, [40, 50, 60, 70]), "B00000000F0F0F0F28323C460000000000000065"),
        (wave([30] * 4, [80, 90, 100, 100]), "B00000001E1E1E1E505A64640000000000000065"),
        (wave([40, 60, 80, 100], [100, 90, 90, 90]), "B0000000283C5064645A5A5A0000000000000065"),
    ],
)
def test_b0_channel_a_only(a, hex_frame):
    # Example No.1: B gets {0,0,0,0} + {0,0,0,101}, i.e. no data.
    assert encode_b0(a, None) == bytes.fromhex(hex_frame)


def test_b0_relative_strength_and_sequence():
    # Example No.2: A strength +5 without confirmation, later +10 with sequence 1.
    first = encode_b0(wave([10] * 4, [0, 10, 20, 30]), None, mode_a=INC, strength_a=5)
    assert first == bytes.fromhex("B00405000A0A0A0A000A141E0000000000000065")
    fourth = encode_b0(
        wave([40, 60, 80, 100], [100, 90, 90, 90]), None, sequence=1, mode_a=INC, strength_a=10
    )
    assert fourth == bytes.fromhex("B0140A00283C5064645A5A5A0000000000000065")


def test_b0_both_channels():
    # Example No.4.
    frame = encode_b0(wave([30] * 4, [80, 90, 100, 100]), wave([10] * 4, [0, 0, 0, 10]))
    assert frame == bytes.fromhex("B00000001E1E1E1E505A64640A0A0A0A0000000A")


def test_b0_absolute_zero_on_both_channels():
    frame = encode_b0(None, None, sequence=3, mode_a=ABS, mode_b=ABS)
    assert frame[:4] == bytes((0xB0, 0x3F, 0, 0)) and len(frame) == 20


def test_waveform_bytes_match_the_apps_breathing_wave():
    # example.md, "breathing": freq = 10, strength = 20 -> 0A0A0A0A14141414
    frame = encode_b0(Waveform.steady(10, [20] * 4), None)
    assert frame[4:12] == bytes.fromhex("0A0A0A0A14141414")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sequence": 16},
        {"strength_a": 201},
        {"strength_b": -1},
        {"mode_a": 4},
    ],
)
def test_b0_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        encode_b0(None, None, **kwargs)


@pytest.mark.parametrize(
    ("frequencies", "intensities"),
    [([9] * 4, [0] * 4), ([241] * 4, [0] * 4), ([10] * 4, [101] * 4), ([10] * 3, [0] * 3)],
)
def test_waveform_rejects_values_the_box_would_drop(frequencies, intensities):
    with pytest.raises(ValueError):
        wave(frequencies, intensities)


# -- frequency ---------------------------------------------------------------------------


def test_frequency_conversion_examples():
    # Waveform doc, e.g. 1 (1..10 Hz) and e.g. 2 (100..1000 ms).
    periods = [1000, 500, 333, 250, 200, 166, 142, 125, 111, 100]
    assert [encode_frequency(p) for p in periods] == [
        240,
        180,
        146,
        130,
        120,
        113,
        108,
        105,
        102,
        100,
    ]
    periods = list(range(100, 1001, 100))
    assert [encode_frequency(p) for p in periods] == [
        100,
        120,
        140,
        160,
        180,
        200,
        210,
        220,
        230,
        240,
    ]


def test_frequency_conversion_table():
    table = {10: 10, 20: 20, 50: 50, 100: 100, 110: 102, 150: 110, 680: 208, 750: 215}
    assert {p: encode_frequency(p) for p in table} == table
    # The doc's table lists 650 ms -> 204; its own formula gives 205. We follow the formula.
    assert encode_frequency(650) == 205


@pytest.mark.parametrize("period", [9, 1001, 0, 50.0])
def test_frequency_out_of_range_is_an_error(period):
    with pytest.raises(ValueError):
        encode_frequency(period)


def test_period_from_hz():
    assert [period_ms(hz) for hz in (100, 50, 10, 7, 1)] == [10, 20, 100, 143, 1000]
    for hz in (0.5, 101):
        with pytest.raises(ValueError):
            period_ms(hz)


# -- BF and B1 ----------------------------------------------------------------------------


def test_bf_layout():
    assert encode_bf(150, 30, 160, 160, 0, 0) == bytes((0xBF, 150, 30, 160, 160, 0, 0))
    with pytest.raises(ValueError):
        encode_bf(201, 30, 160, 160, 0, 0)
    with pytest.raises(ValueError):
        encode_bf(150, 30, 256, 160, 0, 0)


def test_b1_parsing():
    report = parse_b1(bytes((0xB1, 1, 25, 0)))  # example No.2: A = 25, sequence 1
    assert (report.sequence, report.strength_a, report.strength_b) == (1, 25, 0)
    assert not report.from_wheel
    assert parse_b1(bytearray((0xB1, 0, 11, 0))).from_wheel  # example No.3: the wheel
    for bad in (b"\xb1\x00\x00", b"\xb0\x00\x00\x00"):
        with pytest.raises(ValueError):
            parse_b1(bad)


def test_strength_examples():
    # Strength section: the box's A channel is at 10.
    assert apply_strength(10, INC, 195) == 200
    assert apply_strength(10, DEC, 20) == 0
    assert apply_strength(10, INC, 201) == 10  # out of range counts as 0
    assert apply_strength(10, ABS, 201) == 0
    assert apply_strength(10, StrengthMode.UNCHANGED, 50) == 10
    # Soft limit: A limited to 150 never goes above it.
    assert apply_strength(140, INC, 30, limit=150) == 150
    assert apply_strength(0, ABS, 200, limit=150) == 150


# -- level mapping --------------------------------------------------------------------------


def test_to_intensity():
    assert [to_intensity(x) for x in (-1, 0, 0.0005, 0.5, 1, 2)] == [0, 0, 0, 50, 100, 100]
    assert [to_intensity(x, 10, 40) for x in (0, 0.01, 0.5, 1)] == [0, 10, 25, 40]


def test_interpolation_ramps_up_and_drops_at_once():
    assert interpolate(0, 40) == (10, 20, 30, 40)
    assert interpolate(40, 41) == (40, 40, 41, 41)
    assert interpolate(60, 20) == (20, 20, 20, 20)  # decreases are immediate (S5)
    assert interpolate(60, 0) == (0, 0, 0, 0)
    for previous, target in ((0, 100), (13, 87), (99, 100)):
        steps = interpolate(previous, target)
        assert steps[-1] == target and list(steps) == sorted(steps)
        assert all(previous <= s <= target for s in steps)
