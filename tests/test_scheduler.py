import asyncio

from estim_camming.actions import Action, ChannelOutput
from estim_camming.events import ActionFinished, ActionRejected, ActionStarted
from estim_camming.scheduler import Scheduler


def action(duration: float = 0.2, intensity: float = 1.0, channels=("A",)) -> Action:
    outputs = tuple(
        ChannelOutput(channel=ch, pattern="constant", intensity=intensity) for ch in channels
    )
    return Action(label="t", duration=duration, outputs=outputs)


async def test_plays_actions_in_order_and_returns_to_zero(make_guard, device, bus):
    guard = make_guard(max_level=1.0)
    scheduler = Scheduler(guard, bus, device.channels, tick_hz=100)
    sub = bus.subscribe(ActionStarted, ActionFinished)
    first, second = action(), action()
    scheduler.enqueue(first)
    scheduler.enqueue(second)
    task = asyncio.create_task(scheduler.run())
    seen = [await asyncio.wait_for(sub.get(), 2) for _ in range(4)]
    task.cancel()
    assert [(type(e).__name__, e.action.id) for e in seen] == [
        ("ActionStarted", first.id),
        ("ActionFinished", first.id),
        ("ActionStarted", second.id),
        ("ActionFinished", second.id),
    ]
    assert device.levels == {"A": 0.0, "B": 0.0}


async def test_disarmed_scheduler_rejects_actions(make_guard, device, bus):
    scheduler = Scheduler(make_guard(start_armed=False), bus, device.channels)
    sub = bus.subscribe(ActionRejected)
    assert scheduler.enqueue(action()) is False
    assert (await sub.get()).reason == "output disarmed"


async def test_queue_limit(make_guard, device, bus):
    scheduler = Scheduler(make_guard(), bus, device.channels, max_queue=1)
    assert scheduler.enqueue(action())
    assert not scheduler.enqueue(action())


async def test_emergency_stop_interrupts_and_clears(make_guard, device, bus):
    guard = make_guard(max_level=1.0)
    scheduler = Scheduler(guard, bus, device.channels, tick_hz=100)
    guard.on_disarm(scheduler.clear)
    finished = bus.subscribe(ActionFinished)
    started = bus.subscribe(ActionStarted)
    scheduler.enqueue(action(duration=10))
    scheduler.enqueue(action(duration=10))
    task = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(started.get(), 1)
    await asyncio.sleep(0.05)
    assert device.levels["A"] > 0
    await guard.emergency_stop("test")
    event = await asyncio.wait_for(finished.get(), 1)
    task.cancel()
    assert event.interrupted
    assert scheduler.queue == ()
    assert device.levels == {"A": 0.0, "B": 0.0}


async def test_channels_play_their_own_pattern_and_intensity(make_guard, device, bus):
    guard = make_guard(max_level=1.0)
    scheduler = Scheduler(guard, bus, device.channels, tick_hz=100)
    seen = []
    original = guard.set_levels

    async def record(levels):
        seen.append(dict(levels))
        await original(levels)

    guard.set_levels = record
    outputs = (
        ChannelOutput(channel="A", pattern="constant", intensity=0.8),
        ChannelOutput(channel="B", pattern="ramp", intensity=0.5, params={"start": 0, "end": 1}),
    )
    scheduler.enqueue(Action(label="split", duration=0.3, outputs=outputs))
    finished = bus.subscribe(ActionFinished)
    task = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(finished.get(), 2)
    task.cancel()
    playing = seen[:-1]  # the last update returns everything to 0
    assert all(lv["A"] == 0.8 for lv in playing)
    assert playing[0]["B"] < 0.1 < playing[-1]["B"] <= 0.5  # B ramps up to its own intensity
    assert seen[-1] == {"A": 0.0, "B": 0.0}


async def test_channels_without_output_stay_off(make_guard, device, bus):
    guard = make_guard(max_level=1.0)
    scheduler = Scheduler(guard, bus, device.channels, tick_hz=100)
    started = bus.subscribe(ActionStarted)
    scheduler.enqueue(action(duration=5, channels=("B",)))
    task = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(started.get(), 1)
    await asyncio.sleep(0.05)
    task.cancel()
    assert device.levels["A"] == 0.0 and device.levels["B"] > 0.0
