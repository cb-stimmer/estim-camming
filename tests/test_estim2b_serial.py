"""End-to-end test of the 2B device with the real estim2py library and pyserial,
against a fake box on a pseudo-terminal. Skipped when estim2py isn't installed
(optional dependency) or on Windows (no pty)."""

import asyncio
import os
import sys
import threading

import pytest

pytest.importorskip("estim2py")
if sys.platform == "win32":
    pytest.skip("needs a pseudo-terminal", allow_module_level=True)

from estim_camming.devices.base import DeviceError  # noqa: E402
from estim_camming.plugins import DEVICES  # noqa: E402


class PtyBox:
    """Answers 2B serial commands (terminated by CR) with a status line
    'battery:A:B:C:D:mode:power:linked:version', levels doubled like the box."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        self.port = os.ttyname(self.slave)
        self.state = {"a": 0, "b": 0, "c": 100, "d": 100, "mode": 0, "power": "L"}
        self.commands = []
        self.garble = False
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _reply(self):
        s = self.state
        line = f"512:{s['a']}:{s['b']}:{s['c']}:{s['d']}:{s['mode']}:{s['power']}:0:2.106\n"
        os.write(self.master, b"garbage\n" if self.garble else line.encode())

    def _handle(self, cmd):
        self.commands.append(cmd)
        s = self.state
        if cmd[:1] in ("A", "B", "C", "D"):
            s[cmd[0].lower()] = int(cmd[1:]) * 2
        elif cmd == "K":
            s.update(a=0, b=0)
        elif cmd in ("L", "H"):
            s.update(a=0, b=0, power=cmd)
        elif cmd.startswith("M"):
            s.update(a=0, b=0, c=100, d=100, mode=int(cmd[1:]))
        self._reply()

    def _serve(self):
        buf = b""
        while not self._stop:
            try:
                chunk = os.read(self.master, 64)
            except OSError:
                return
            buf += chunk
            while b"\r" in buf:
                raw, buf = buf.split(b"\r", 1)
                self._handle(raw.decode().strip())

    def close(self):
        self._stop = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def pty_box():
    box = PtyBox()
    yield box
    box.close()


async def wait_for(predicate, seconds=3.0):
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_real_library_over_serial(pty_box):
    device = DEVICES.create(
        "estim2b",
        {"port": pty_box.port, "mode": "continuous", "param_c": 30, "max_output": 60},
    )
    await device.connect()
    assert pty_box.commands[:5] == ["", "K", "L", "M2", "C30"]
    assert pty_box.state["mode"] == 2 and pty_box.state["power"] == "L"

    await device.set_levels({"A": 0.5, "B": 0.25})
    assert await wait_for(lambda: (pty_box.state["a"], pty_box.state["b"]) == (60, 30))

    await device.stop()
    assert pty_box.commands[-1] == "K" and pty_box.state["a"] == 0
    await device.disconnect()


async def test_garbled_reply_is_a_fault(pty_box):
    device = DEVICES.create("estim2b", {"port": pty_box.port, "serial_timeout": 0.5})
    await device.connect()
    pty_box.garble = True
    await device.set_levels({"A": 0.5, "B": 0.0})
    assert await wait_for(lambda: device._error is not None)
    with pytest.raises(DeviceError):
        await device.set_levels({"A": 0.5, "B": 0.0})
    pty_box.garble = False
    await device.disconnect()
