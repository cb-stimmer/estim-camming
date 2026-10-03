"""The coyote3 device plugin against a fake Coyote 3.0 that follows the
documented protocol (strength rules, soft limit, B1 replies, wheel)."""

import asyncio
from types import SimpleNamespace

import pytest

from estim_camming.devices.base import DeviceError
from estim_camming.devices.coyote3 import (
    BATTERY_UUID,
    NOTIFY_UUID,
    WRITE_UUID,
    Coyote3Device,
    StrengthMode,
    apply_strength,
)
from estim_camming.plugins import DEVICES, PluginError

FRAME = 0.02  # seconds between B0 frames in these tests (100 ms on the real box)


class FakeCoyote:
    """Mimics a connected bleak client talking to a Coyote 3.0."""

    def __init__(self, on_disconnect):
        self.on_disconnect = on_disconnect
        self.is_connected = True
        self.missing: set[str] = set()
        self.notify = None
        self.writes: list[bytes] = []
        self.limits = [200, 200]
        self.strength = [7, 3]  # whatever the box had before
        self.answer = True  # send B1 replies
        self.fail_writes = False
        self.hang_writes = False
        self.reply_delay = 0.005
        self.services = SimpleNamespace(
            get_characteristic=lambda uuid: None if uuid in self.missing else object()
        )

    async def start_notify(self, uuid, callback):
        assert uuid == NOTIFY_UUID
        self.notify = callback

    async def read_gatt_char(self, uuid):
        assert uuid == BATTERY_UUID
        return bytearray([87])

    async def write_gatt_char(self, uuid, data, response=None):
        assert uuid == WRITE_UUID and response is False
        if self.hang_writes:
            await asyncio.sleep(10)
        if self.fail_writes:
            raise OSError("BlueZ: not connected")
        data = bytes(data)
        self.writes.append(data)
        if data[0] == 0xBF:
            self.limits = [data[1], data[2]]
        elif data[0] == 0xB0:
            sequence, modes = data[1] >> 4, data[1] & 0x0F
            for i, mode in enumerate((modes >> 2, modes & 0b11)):
                self.strength[i] = apply_strength(
                    self.strength[i], StrengthMode(mode), data[2 + i], self.limits[i]
                )
            if sequence and self.answer:
                self._reply(sequence)

    def _reply(self, sequence):
        report = bytearray((0xB1, sequence, *self.strength))
        asyncio.get_running_loop().call_later(self.reply_delay, self.notify, None, report)

    def wheel(self, channel, step):
        i = "AB".index(channel)
        self.strength[i] = apply_strength(
            self.strength[i],
            StrengthMode.INCREASE if step > 0 else StrengthMode.DECREASE,
            abs(step),
            self.limits[i],
        )
        self.notify(None, bytearray((0xB1, 0, *self.strength)))

    def drop_connection(self):
        self.is_connected = False
        self.on_disconnect(self)

    async def disconnect(self):
        self.is_connected = False
        self.on_disconnect(self)  # bleak calls the callback for our own disconnect too

    # -- reading what was sent ----------------------------------------------------

    def frames(self):
        return [w for w in self.writes if w[0] == 0xB0]

    def strength_frames(self):
        return [f for f in self.frames() if f[1] & 0x0F]

    @staticmethod
    def intensities(frame, channel):
        start = 8 if channel == "A" else 16
        return tuple(frame[start : start + 4])


@pytest.fixture
def box(monkeypatch):
    boxes = []

    async def factory(options, on_disconnect):
        boxes.append(FakeCoyote(on_disconnect))
        return boxes[-1]

    monkeypatch.setattr(Coyote3Device, "client_factory", staticmethod(factory))
    monkeypatch.setattr(Coyote3Device, "frame_seconds", FRAME)
    return boxes


def make(**options):
    return DEVICES.create("coyote3", {"strength": 20} | options)


async def frames_later(n=3):
    await asyncio.sleep(FRAME * n + 0.01)


async def test_connect_writes_limits_first_then_zeroes_the_strength(box):
    device = make(strength={"A": 20, "B": 15}, strength_limit=30, frequency_balance=200)
    await device.connect()
    fake = box[0]
    assert fake.writes[0] == bytes((0xBF, 30, 30, 200, 200, 0, 0))
    first = fake.writes[1]
    assert first[0] == 0xB0 and first[1] == 0x1F  # sequence 1, absolute on A and B
    assert first[2:4] == b"\x00\x00"
    assert fake.strength == [0, 0] and device.strengths == {"A": 0, "B": 0}
    assert device.battery == 87
    await frames_later()
    later = fake.frames()[1:]
    assert later and all(f[1] == 0 for f in later)  # no strength changes while idle
    assert all(FakeCoyote.intensities(f, ch) == (0,) * 4 for f in later for ch in "AB")
    await device.disconnect()


async def test_output_switches_the_strength_on_and_drives_the_pulse_width(box):
    device = make(strength={"A": 20, "B": 15}, frequency={"A": 50, "B": 10})
    await device.connect()
    fake = box[0]
    await device.set_levels({"A": 0.5, "B": 0.0})
    await frames_later()
    on = fake.strength_frames()[-1]
    assert on[1] & 0x0F == 0b1111 and on[2:4] == bytes((20, 15))
    assert FakeCoyote.intensities(on, "A") == (12, 25, 38, 50)  # ramped over 4 x 25 ms
    assert FakeCoyote.intensities(fake.frames()[-1], "A") == (50,) * 4
    assert FakeCoyote.intensities(fake.frames()[-1], "B") == (0,) * 4
    assert fake.frames()[-1][4:8] == bytes((20,) * 4)  # 50 Hz -> 20 ms
    assert fake.frames()[-1][12:16] == bytes((100,) * 4)  # 10 Hz -> 100 ms
    assert fake.strength == [20, 15]
    assert len(fake.strength_frames()) == 2  # zero at connect, on once
    await device.disconnect()


async def test_min_and_max_output_map_the_level(box):
    device = make(min_output=10, max_output={"A": 40, "B": 60})
    assert [device.to_box(x, "A") for x in (0, 0.0005, 0.01, 0.5, 1, 2)] == [0, 0, 10, 25, 40, 40]
    assert device.to_box(1, "B") == 60


async def test_stop_zeroes_at_once_and_output_switches_on_again(box):
    device = make()
    await device.connect()
    fake = box[0]
    await device.set_levels({"A": 1.0, "B": 1.0})
    await frames_later()
    count = len(fake.writes)
    await device.stop()
    stop = fake.writes[count]  # written immediately, not at the next tick
    assert stop[1] & 0x0F == 0b1111 and stop[2:4] == b"\x00\x00"
    assert FakeCoyote.intensities(stop, "A") == FakeCoyote.intensities(stop, "B") == (0,) * 4
    assert fake.strength == [0, 0]
    await frames_later()
    assert fake.strength == [0, 0]  # stays off until there is output again
    await device.set_levels({"A": 0.3, "B": 0.0})
    await frames_later()
    assert fake.strength == [20, 20]
    await device.disconnect()


async def test_wheel_changes_are_kept_until_the_next_stop(box):
    device = make(strength=20, strength_limit=25)
    await device.connect()
    fake = box[0]
    await device.set_levels({"A": 0.5, "B": 0.5})
    await frames_later()
    fake.wheel("A", +10)  # the box caps it at the soft limit
    await frames_later()
    assert fake.strength[0] == 25 and device.strengths["A"] == 25
    assert len(fake.strength_frames()) == 2  # not overridden
    await device.set_levels({"A": 0.6, "B": 0.5})  # still healthy
    await device.disconnect()


async def test_disconnect_stops_and_closes(box):
    device = make()
    await device.connect()
    fake = box[0]
    await device.set_levels({"A": 1.0, "B": 1.0})
    await frames_later()
    await device.disconnect()
    assert fake.strength == [0, 0] and not fake.is_connected
    assert device._error is None  # our own disconnect is not "connection lost"
    await device.disconnect()  # idempotent
    with pytest.raises(DeviceError, match="not connected"):
        await device.set_levels({"A": 0.1, "B": 0.0})


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (lambda fake: fake.drop_connection(), "connection lost"),
        (lambda fake: setattr(fake, "fail_writes", True), "write failed"),
    ],
)
async def test_faults_make_the_next_update_fail(box, fault, message):
    device = make()
    await device.connect()
    fault(box[0])
    await frames_later()
    with pytest.raises(DeviceError, match=message):
        await device.set_levels({"A": 0.1, "B": 0.0})
    await device.disconnect()


async def test_stalled_writes_are_a_fault(box):
    device = make(stall_timeout=0.1)
    await device.connect()
    box[0].hang_writes = True
    await asyncio.sleep(0.2)
    with pytest.raises(DeviceError, match="timed out after 0.1 s"):
        await device.set_levels({"A": 0.1, "B": 0.0})
    await asyncio.wait_for(device.disconnect(), 1)  # a hanging write doesn't block it


async def test_missing_confirmation_when_switching_on_is_a_fault(box):
    device = make(reply_timeout=0.05)
    await device.connect()
    box[0].answer = False
    await device.set_levels({"A": 0.5, "B": 0.0})
    await asyncio.sleep(0.15)
    with pytest.raises(DeviceError, match="did not confirm strength 20/20"):
        await device.set_levels({"A": 0.5, "B": 0.0})
    await device.disconnect()


async def test_stop_while_switching_on_is_not_a_fault(box):
    device = make(reply_timeout=0.2)
    await device.connect()
    box[0].reply_delay = 0.05
    await device.set_levels({"A": 0.5, "B": 0.0})
    await asyncio.sleep(FRAME + 0.005)  # the "on" frame is out, not yet confirmed
    await device.stop()
    await asyncio.sleep(0.3)
    await device.set_levels({"A": 0.0, "B": 0.0})  # no stale timeout
    await device.disconnect()


async def test_strength_above_the_limit_is_a_fault(box):
    device = make(strength=20)
    await device.connect()
    box[0].notify(None, bytearray((0xB1, 0, 40, 0)))
    with pytest.raises(DeviceError, match="above its soft limit"):
        await device.set_levels({"A": 0.1, "B": 0.0})
    await device.disconnect()


async def test_connect_fails_without_confirmation_and_closes(box):
    original = FakeCoyote.__init__

    def silent(self, on_disconnect):
        original(self, on_disconnect)
        self.answer = False

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FakeCoyote, "__init__", silent)
        device = make(reply_timeout=0.05)
        with pytest.raises(DeviceError, match="did not confirm strength 0/0"):
            await device.connect()
    assert not box[0].is_connected


async def test_connect_refuses_a_device_without_the_coyote_characteristics(box, monkeypatch):
    original = FakeCoyote.__init__

    def other(self, on_disconnect):
        original(self, on_disconnect)
        self.missing = {WRITE_UUID}

    monkeypatch.setattr(FakeCoyote, "__init__", other)
    with pytest.raises(DeviceError, match="not a Coyote 3.0"):
        await make().connect()
    assert not box[0].is_connected and box[0].writes == []


async def test_missing_bleak_gives_a_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bleak":
            raise ImportError("No module named 'bleak'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(DeviceError, match=r"pip install -e '\.\[coyote3\]'"):
        await make().connect()


@pytest.mark.parametrize(
    "options",
    [
        {"strength": None},
        {"strength": 201},
        {"strength": 30, "strength_limit": 20},
        {"strength": {"A": 10}},
        {"strength": {"A": 10, "B": 10, "C": 1}},
        {"max_output": 0},
        {"min_output": 50, "max_output": 40},
        {"frequency": 0.5},
        {"frequency": 101},
        {"frequency_balance": 256},
    ],
)
def test_invalid_options(options):
    data = {"strength": 20} | options
    if data["strength"] is None:
        del data["strength"]  # required: no default on purpose
    with pytest.raises(PluginError):
        DEVICES.create("coyote3", data)


def test_strength_limit_defaults_to_the_strength():
    device = make(strength={"A": 20, "B": 15})
    assert device.options.per_channel("strength_limit", "B") == 15


async def test_a_dead_writer_is_noticed(box):
    device = make(stall_timeout=0.1)
    await device.connect()
    device._worker.cancel()  # the stream stops without recording an error
    await asyncio.sleep(0.15)
    with pytest.raises(DeviceError, match="nothing sent"):
        await device.set_levels({"A": 0.1, "B": 0.0})
    await device.disconnect()


async def test_safety_guard_stops_on_a_coyote_fault_and_on_stop(box):
    from estim_camming.bus import EventBus
    from estim_camming.events import SafetyStateChanged
    from estim_camming.safety import SafetyConfig, SafetyGuard

    device = make()
    await device.connect()
    fake = box[0]
    bus = EventBus()
    guard = SafetyGuard(device, SafetyConfig(start_armed=True, max_level=1.0), bus)
    sub = bus.subscribe(SafetyStateChanged)

    for _ in range(10):  # soft start: the guard ramps the level up
        await guard.set_levels({"A": 1.0, "B": 0.0})
        await asyncio.sleep(FRAME)
    assert fake.strength == [20, 20] and device._target["A"] > 0
    await guard.emergency_stop("test")  # STOP: strength 0 on the box at once
    assert fake.strength == [0, 0] and not guard.armed
    assert (await sub.get()).reason == "test"

    guard.arm("test")
    fake.drop_connection()  # Bluetooth gone: the next update is an emergency stop
    await guard.set_levels({"A": 1.0, "B": 0.0})
    assert not guard.armed
    assert [e.reason for e in [await sub.get(), await sub.get()]][-1] == "device error"
    await device.disconnect()


async def test_opening_releases_a_stale_bluez_link_before_scanning(monkeypatch):
    import bleak

    from estim_camming.devices import coyote3

    calls = []

    async def release(options):
        calls.append("release")
        return True

    async def find(options):
        calls.append("scan")
        return SimpleNamespace(address="C3:9F:8B:CC:3B:85")

    class Client:
        def __init__(self, device, disconnected_callback, timeout):
            calls.append(("client", device.address, timeout))

        async def connect(self):
            calls.append("connect")

    monkeypatch.setattr(coyote3, "_release_stale_link", release)
    monkeypatch.setattr(coyote3, "_find_device", find)
    monkeypatch.setattr(bleak, "BleakClient", Client)
    options = Coyote3Device.Options(strength=5, connect_timeout=7)
    client = await coyote3._open_bleak(options, lambda c: None)
    assert isinstance(client, Client)
    assert calls == ["release", "scan", ("client", "C3:9F:8B:CC:3B:85", 7), "connect"]


async def test_opening_reports_a_box_that_isnt_found(monkeypatch):
    from estim_camming.devices import coyote3

    async def release(options):
        return False

    async def find(options):
        return None

    monkeypatch.setattr(coyote3, "_release_stale_link", release)
    monkeypatch.setattr(coyote3, "_find_device", find)
    with pytest.raises(DeviceError, match="no Coyote 3.0 named '47L121000' found within 10 s"):
        await coyote3._open_bleak(Coyote3Device.Options(strength=5), lambda c: None)
