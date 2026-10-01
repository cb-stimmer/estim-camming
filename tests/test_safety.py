import pytest

from estim_camming.events import SafetyStateChanged


def test_output_is_scaled_by_max_level(make_guard):
    guard = make_guard(max_level=0.5, channel_max_level={"B": 0.2})
    out = guard.compute({"A": 1.0, "B": 1.0}, dt=0.1)
    assert out == pytest.approx({"A": 0.5, "B": 0.2})


def test_requested_levels_are_clamped(make_guard):
    guard = make_guard(max_level=1.0)
    out = guard.compute({"A": 5.0, "B": -1.0}, dt=0.1)
    assert out == {"A": 1.0, "B": 0.0}


def test_increase_is_rate_limited_but_decrease_is_instant(make_guard):
    guard = make_guard(max_level=1.0, max_change_per_second=0.5)
    assert guard.compute({"A": 1.0}, dt=0.1)["A"] == pytest.approx(0.05)
    # A long idle gap must not allow a big jump.
    assert guard.compute({"A": 1.0}, dt=10.0)["A"] == pytest.approx(0.05)
    guard._levels = {"A": 0.8, "B": 0.0}
    assert guard.compute({"A": 0.0}, dt=0.01)["A"] == 0.0


async def test_disarmed_guard_outputs_zero(make_guard, device):
    guard = make_guard(start_armed=False)
    assert guard.compute({"A": 1.0, "B": 1.0}, dt=0.1) == {"A": 0.0, "B": 0.0}
    await guard.set_levels({"A": 1.0, "B": 1.0})
    assert device.levels == {"A": 0.0, "B": 0.0}


async def test_emergency_stop_zeroes_and_notifies(make_guard, device, bus):
    guard = make_guard(max_level=1.0)
    cleared = []
    guard.on_disarm(lambda: cleared.append(True))
    await guard.set_levels({"A": 1.0})
    sub = bus.subscribe(SafetyStateChanged)
    await guard.emergency_stop("test")
    assert not guard.armed
    assert device.levels == {"A": 0.0, "B": 0.0}
    assert cleared == [True]
    event = await sub.get()
    assert event.armed is False and event.reason == "test"


def test_scale_reduces_output(make_guard):
    guard = make_guard(max_level=0.8)
    guard.set_scale(0.5)
    assert guard.compute({"A": 1.0}, dt=0.1)["A"] == pytest.approx(0.4)
    guard.set_scale(3.0)
    assert guard.scale == 1.0


async def test_device_error_triggers_emergency_stop(make_guard, device):
    guard = make_guard()

    async def broken(levels):
        raise RuntimeError("usb unplugged")

    device.set_levels = broken
    await guard.set_levels({"A": 1.0})
    assert not guard.armed


def test_unknown_channel_in_safety_config(make_guard):
    with pytest.raises(ValueError, match="unknown channel"):
        make_guard(channel_max_level={"Z": 0.1})
