"""Tests for the E-Stim 2B device, using a fake box with estim2py's API."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from estim_camming.devices.base import DeviceError
from estim_camming.devices.estim2b import Estim2bDevice
from estim_camming.events import SafetyStateChanged
from estim_camming.plugins import DEVICES, PluginError
from estim_camming.safety import SafetyConfig, SafetyGuard

#: Optional features per firmware, as in estim2py's protocol classes.
FEATURES = {
    "2.106": set(),
    "2.119B": {"dynamic", "bias", "output_map", "step", "version"},
    "2.120B": {"dynamic", "bias", "output_map", "step", "version", "warp", "ramp"},
}

#: Bias numbering per firmware (Estim2pyBias value -> number), as in estim2py.
BIAS_CODES = {
    "2.119B": {"A": 0, "B": 1, "average": 2, "max": 3},
    "2.120B": {"max": 0, "A": 1, "B": 2, "average": 3},
}


class FakeBox:
    """Mimics estim2py.Estim2pyConnection: levels are reported doubled, and
    changing power range or mode resets A/B to 0 (as on the real box).
    ``firmware`` sets ``protocol.name``; None leaves out ``protocol``."""

    firmware: str | None = None

    def __init__(self, port, timeout, delay):
        self.port, self.timeout, self.delay = port, timeout, delay
        self.state = {"a": 0, "b": 0, "c": 100, "d": 100, "mode": 0, "power": "L"}
        if self.firmware is not None:
            features = FEATURES[self.firmware]
            codes = BIAS_CODES.get(self.firmware, {})
            self.protocol = SimpleNamespace(
                name=self.firmware,
                supports=features.__contains__,
                bias_code=lambda bias: codes[bias.value],
            )
            if "bias" in features:
                self.state["bias"] = 0
            if "warp" in features:
                self.state.update(warp=0, ramp=0)
        self.calls = []
        self.fail_on = None  # method name that raises
        self.block = None  # threading.Event: set_channel waits on it
        self.ignore_power = False

    def _status(self):
        return SimpleNamespace(battery=500, version="2.106", linked=0, **self.state)

    def _record(self, *call):
        self.calls.append(call)
        if self.fail_on == call[0]:
            raise RuntimeError("serial port gone")

    def get_status(self):
        self._record("get_status")
        return self._status()

    def set_channel(self, channel, value):
        self._record("set_channel", channel, value)
        if self.block is not None:
            self.block.wait(5)
        self.state[channel.lower()] = value * 2
        return self._status()

    def kill(self):
        self._record("kill")
        self.state.update(a=0, b=0)
        return self._status()

    def low(self):
        self._record("low")
        self.state.update(a=0, b=0, power=self.state["power"] if self.ignore_power else "L")
        return self._status()

    def high(self):
        self._record("high")
        self.state.update(a=0, b=0, power="H")
        return self._status()

    def dynamic(self):
        self._record("dynamic")
        self.state.update(a=0, b=0, power="D", bias=0)
        return self._status()

    def set_bias(self, bias):
        self._record("set_bias", bias.value)
        self.state["bias"] = self.protocol.bias_code(bias)
        return self._status()

    def set_warp(self, warp):
        self._record("set_warp", warp)
        self.state["warp"] = warp
        return self._status()

    def set_ramp(self, ramp):
        self._record("set_ramp", ramp)
        self.state["ramp"] = ramp
        return self._status()

    def set_mode(self, mode):
        self._record("set_mode", mode)
        self.state.update(a=0, b=0, c=100, d=100, mode=mode)
        return self._status()


@pytest.fixture
def box(monkeypatch):
    boxes = []

    def factory(port, timeout, delay):
        boxes.append(FakeBox(port, timeout, delay))
        return boxes[-1]

    monkeypatch.setattr(Estim2bDevice, "connection_factory", staticmethod(factory))
    return boxes


def make(**options):
    return DEVICES.create("estim2b", {"port": "/dev/ttyUSB0"} | options)


async def settle(device):
    """Let the writer task process pending levels."""
    for _ in range(50):
        await asyncio.sleep(0.01)
        if device._target == device._sent and device._busy_since is None:
            return


async def test_connect_configures_the_box(box):
    device = make(mode="wave", param_c=40, param_d=60, serial_timeout=1.5, delay=0.05)
    await device.connect()
    fake = box[0]
    assert (fake.port, fake.timeout, fake.delay) == ("/dev/ttyUSB0", 1.5, 0.05)
    assert fake.calls[:6] == [
        ("get_status",),
        ("kill",),
        ("low",),
        ("set_mode", 5),
        ("set_channel", "C", 40),
        ("set_channel", "D", 60),
    ]
    assert fake.state["mode"] == 5 and fake.state["power"] == "L"
    assert (fake.state["a"], fake.state["b"]) == (0, 0)
    await device.disconnect()


async def test_levels_are_scaled_and_only_changes_are_sent(box):
    device = make(max_output=50)
    await device.connect()
    fake = box[0]
    fake.calls.clear()
    await device.set_levels({"A": 0.5, "B": 0.0})
    await settle(device)
    await device.set_levels({"A": 0.5, "B": 1.0})
    await settle(device)
    assert fake.calls == [("set_channel", "A", 25), ("set_channel", "B", 50)]
    assert (fake.state["a"], fake.state["b"]) == (50, 100)  # box reports doubled values
    await device.disconnect()


async def test_slow_box_gets_only_the_latest_level(box):
    device = make()
    await device.connect()
    fake = box[0]
    fake.calls.clear()
    fake.block = threading.Event()
    await device.set_levels({"A": 0.1, "B": 0.0})
    await asyncio.sleep(0.05)  # writer is now stuck sending A=10
    for level in (0.2, 0.3, 0.4):
        await device.set_levels({"A": level, "B": 0.0})
    fake.block.set()
    await settle(device)
    assert [c for c in fake.calls if c[0] == "set_channel"] == [
        ("set_channel", "A", 10),
        ("set_channel", "A", 40),  # 20 and 30 were skipped
    ]
    await device.disconnect()


async def test_stop_kills_output(box):
    device = make()
    await device.connect()
    await device.set_levels({"A": 0.8, "B": 0.8})
    await settle(device)
    await device.stop()
    fake = box[0]
    assert fake.calls[-1] == ("kill",)
    assert (fake.state["a"], fake.state["b"]) == (0, 0)
    await device.disconnect()


async def test_disconnect_zeroes_and_closes(box):
    device = make()
    await device.connect()
    closed = []
    box[0].serial = SimpleNamespace(close=lambda: closed.append(True))
    await device.disconnect()
    assert box[0].calls[-1] == ("kill",) and closed == [True]
    with pytest.raises(DeviceError, match="not connected"):
        await device.set_levels({"A": 0.1})


async def test_serial_error_makes_the_next_update_fail(box):
    device = make()
    await device.connect()
    box[0].fail_on = "set_channel"
    await device.set_levels({"A": 0.5, "B": 0.0})
    await asyncio.sleep(0.05)
    with pytest.raises(DeviceError, match="serial port gone"):
        await device.set_levels({"A": 0.5, "B": 0.0})
    box[0].fail_on = None
    await device.disconnect()


async def test_stalled_box_is_a_fault(box):
    device = make(stall_timeout=0.05)
    await device.connect()
    box[0].block = threading.Event()
    await device.set_levels({"A": 0.5, "B": 0.0})
    await asyncio.sleep(0.15)
    with pytest.raises(DeviceError, match="not responding"):
        await device.set_levels({"A": 0.5, "B": 0.0})
    box[0].block.set()
    await device.disconnect()


async def test_safety_guard_stops_on_2b_fault(box):
    device = make()
    await device.connect()
    from estim_camming.bus import EventBus

    bus = EventBus()
    guard = SafetyGuard(device, SafetyConfig(start_armed=True), bus)
    sub = bus.subscribe(SafetyStateChanged)
    box[0].fail_on = "set_channel"
    await device.set_levels({"A": 0.5, "B": 0.0})  # the writer hits the serial error
    await asyncio.sleep(0.05)
    box[0].fail_on = None
    await guard.set_levels({"A": 1.0})  # device raises -> emergency stop
    assert not guard.armed
    assert (await sub.get()).reason == "device error"
    assert box[0].calls[-1] == ("kill",)
    await device.disconnect()


async def test_refuses_box_that_ignores_power_setting(box, monkeypatch):
    original = FakeBox.__init__

    def high_power_box(self, *args):
        original(self, *args)
        self.state["power"] = "H"
        self.ignore_power = True

    monkeypatch.setattr(FakeBox, "__init__", high_power_box)
    device = make()
    with pytest.raises(DeviceError, match="did not accept"):
        await device.connect()
    assert box[0].calls[-1] == ("kill",)


async def test_missing_library_gives_a_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "estim2py":
            raise ImportError("cannot import name 'deprecated' from 'warnings'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(DeviceError, match="estim2py"):
        await make().connect()


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "nope"},
        {"mode": 42},
        {"mode": 17},
        {"warp": 3},
        {"warp": 64},
        {"ramp": 0},
        {"ramp": 5},
        {"bias": "max"},
        {"power": "high", "bias": "A"},
        {"power": "dynamic", "bias": "middle"},
        {"param_c": 1},
        {"param_d": 0},
        {"power": "max"},
        {"max_output": 0},
        {"delay": 0.01},
    ],
)
def test_invalid_options(options):
    with pytest.raises(PluginError):
        make(**options)


def test_mode_by_number_and_level_mapping():
    device = make(mode=9, max_output=40)
    assert device.mode_id("2.106") == 9 and device.mode_id("2.120B") == 9
    assert [device.to_box(x, "A") for x in (-1, 0, 0.5, 1, 2)] == [0, 0, 20, 40, 40]
    assert make(mode="Continuous").mode_id("2.106") == 2


def test_mode_names_are_numbered_per_firmware():
    step = make(mode="step")
    assert step.mode_id("2.106") == 12
    assert step.mode_id("2.119B") == step.mode_id("2.120B") == 15
    assert make(mode="flo").mode_id("2.120B") == 3
    with pytest.raises(DeviceError, match="no mode 'flo'"):
        make(mode="flo").mode_id("2.106")
    with pytest.raises(DeviceError, match="no mode number 16"):
        make(mode=16).mode_id("2.106")


@pytest.mark.parametrize("firmware", ["2.119B", "2.120B"])
async def test_beta_firmware_gets_its_own_mode_numbers(box, monkeypatch, firmware):
    monkeypatch.setattr(FakeBox, "firmware", firmware)
    device = make(mode="wave")
    await device.connect()
    assert ("set_mode", 6) in box[0].calls and box[0].state["mode"] == 6
    await device.disconnect()


async def test_mode_missing_on_legacy_firmware_is_refused(box, monkeypatch):
    monkeypatch.setattr(FakeBox, "firmware", "2.106")
    device = make(mode="twist")
    with pytest.raises(DeviceError, match="2.106 has no mode 'twist'"):
        await device.connect()
    assert box[0].calls[-1] == ("kill",)
    assert not any(call[0] == "set_mode" for call in box[0].calls)


def test_min_output_maps_non_zero_levels_above_the_threshold():
    device = make(min_output={"A": 10, "B": 20}, max_output={"A": 50, "B": 60})
    # Off stays off (also for tiny rounding leftovers); any output starts at min.
    assert [device.to_box(x, "A") for x in (0, 0.0005, 0.01, 0.5, 1)] == [0, 0, 10, 30, 50]
    assert [device.to_box(x, "B") for x in (0, 0.01, 0.5, 1)] == [0, 20, 40, 60]
    single = make(min_output=15, max_output=40)
    assert [single.to_box(x, "B") for x in (0, 0.2, 1)] == [0, 20, 40]


@pytest.mark.parametrize(
    "options",
    [
        {"min_output": 60, "max_output": 50},
        {"min_output": {"A": 30}, "max_output": {"A": 20}},
        {"min_output": {"C": 5}},
        {"min_output": -1},
        {"min_output": 101},
        {"max_output": {"B": 0}},
    ],
)
def test_invalid_output_ranges(options):
    with pytest.raises(PluginError):
        make(**options)


async def test_min_output_reaches_the_box_and_stop_still_zeroes(box):
    device = make(min_output=12, max_output=40)
    await device.connect()
    fake = box[0]
    await device.set_levels({"A": 0.01, "B": 0.0})
    await settle(device)
    assert (fake.state["a"], fake.state["b"]) == (24, 0)  # 12 on the box (reported doubled)
    await device.set_levels({"A": 0.0, "B": 0.0})
    await settle(device)
    assert fake.state["a"] == 0
    await device.set_levels({"A": 0.5, "B": 0.5})
    await settle(device)
    await device.stop()
    assert (fake.state["a"], fake.state["b"]) == (0, 0)
    await device.disconnect()


async def test_dynamic_power_warp_and_ramp_on_beta_firmware(box, monkeypatch):
    monkeypatch.setattr(FakeBox, "firmware", "2.120B")
    device = make(power="dynamic", mode="continuous", warp=8, ramp=2)
    await device.connect()
    fake = box[0]
    assert fake.calls[1:5] == [
        ("kill",),
        ("dynamic",),
        ("set_mode", 2),
        ("set_warp", 3),
    ]
    assert ("set_ramp", 1) in fake.calls
    assert (fake.state["power"], fake.state["warp"], fake.state["ramp"]) == ("D", 3, 1)
    assert (fake.state["a"], fake.state["b"]) == (0, 0)
    await device.disconnect()


async def test_warp_and_ramp_are_left_alone_when_unset(box, monkeypatch):
    monkeypatch.setattr(FakeBox, "firmware", "2.120B")
    device = make()
    await device.connect()
    assert not any(call[0] in ("dynamic", "set_warp", "set_ramp") for call in box[0].calls)
    await device.disconnect()


@pytest.mark.parametrize(
    ("firmware", "options", "message"),
    [
        ("2.106", {"power": "dynamic"}, "2.106 does not support power = 'dynamic'"),
        (None, {"power": "dynamic"}, "does not support power = 'dynamic'"),
        ("2.119B", {"warp": 2, "ramp": 2}, "2.119B does not support warp, ramp"),
    ],
)
async def test_features_missing_on_the_firmware_are_refused(
    box, monkeypatch, firmware, options, message
):
    monkeypatch.setattr(FakeBox, "firmware", firmware)
    device = make(**options)
    with pytest.raises(DeviceError, match=message):
        await device.connect()
    calls = [call[0] for call in box[0].calls]
    assert calls[-1] == "kill"
    assert not {"low", "high", "dynamic", "set_mode", "set_warp", "set_ramp"} & set(calls)


async def test_refuses_box_that_ignores_warp(box, monkeypatch):
    monkeypatch.setattr(FakeBox, "firmware", "2.120B")
    monkeypatch.setattr(FakeBox, "set_warp", lambda self, warp: self._status())
    device = make(warp=4)
    with pytest.raises(DeviceError, match="did not accept"):
        await device.connect()
    assert box[0].calls[-1] == ("kill",)


@pytest.mark.parametrize(("firmware", "code"), [("2.119B", 3), ("2.120B", 0)])
async def test_dynamic_bias_uses_the_firmware_numbering(box, monkeypatch, firmware, code):
    pytest.importorskip("estim2py")
    monkeypatch.setattr(FakeBox, "firmware", firmware)
    device = make(power="dynamic", bias="max")
    await device.connect()
    calls = [call[0] for call in box[0].calls]
    # Switching to dynamic resets the bias, so it is set afterwards.
    assert calls.index("dynamic") < calls.index("set_bias")
    assert ("set_bias", "max") in box[0].calls and box[0].state["bias"] == code
    await device.disconnect()


async def test_refuses_box_that_ignores_bias(box, monkeypatch):
    pytest.importorskip("estim2py")
    monkeypatch.setattr(FakeBox, "firmware", "2.120B")
    monkeypatch.setattr(FakeBox, "set_bias", lambda self, bias: self._status())
    device = make(power="dynamic", bias="A")
    with pytest.raises(DeviceError, match="did not accept"):
        await device.connect()
    assert box[0].calls[-1] == ("kill",)
