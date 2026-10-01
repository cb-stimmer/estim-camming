"""E-Stim Systems 2B over its serial link, via the ``estim2py`` library.

``estim2py`` (public domain, https://github.com/sissybecky/estim2py) talks to
the box with blocking serial calls: every command waits ``delay`` seconds and
then reads the box's status reply. To keep the event loop free, all calls run
in a worker thread, one at a time. :meth:`set_levels` only records the newest
target and wakes the worker, so a slow box never builds up a backlog of stale
levels (latest value wins), and unchanged channels are not re-sent.

Install with ``pip install -e '.[estim2b]'``: estim2py 0.3.0 on Python 3.13+,
0.2.2 on older Pythons (0.3.0 imports ``warnings.deprecated``, new in 3.13).
See ``docs/design/devices.md``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from estim_camming.devices.base import Device, DeviceError
from estim_camming.plugins import DEVICES, PluginOptions

log = logging.getLogger(__name__)

#: Mode ids of the 2B, as listed by estim2py's ``Estim2pyMode.modes``.
MODES: dict[str, int] = {
    "pulse": 0,
    "bounce": 1,
    "continuous": 2,
    "asplit": 3,
    "bsplit": 4,
    "wave": 5,
    "waterfall": 6,
    "squeeze": 7,
    "milk": 8,
    "throb": 9,
    "thrust": 10,
    "random": 11,
    "step": 12,
    "training": 13,
}

CHANNELS = ("A", "B")

#: Levels below this (0.1 % of full scale) count as "off" and send 0, so
#: ``min_output`` never keeps the box on when the output should be off.
OFF_THRESHOLD = 0.001

BoxLevel = int | dict[str, int]


def _describe(exc: BaseException) -> str:
    # Not str(exc): Estim2pyError.__str__ in estim2py 0.3.0 raises AttributeError.
    return f"{type(exc).__name__}{exc.args!r}"


def _open_estim2py(port: str, timeout: float, delay: float) -> Any:
    try:
        from estim2py import Estim2pyConnection
    except ImportError as exc:
        raise DeviceError(
            "the estim2b device needs the estim2py library (install with: "
            "pip install -e '.[estim2b]'; on Python < 3.13 that installs estim2py 0.2.2, "
            "because 0.3.0 needs Python 3.13); "
            f"import failed: {_describe(exc)}"
        ) from exc
    return Estim2pyConnection(port, timeout=timeout, delay=delay)


@DEVICES.register("estim2b")
class Estim2bDevice(Device):
    """E-Stim Systems 2B via its serial link (estim2py library)."""

    class Options(PluginOptions):
        port: str = Field(description="Serial port of the 2B, e.g. /dev/ttyUSB0 or COM3.")
        power: Literal["low", "high"] = Field(
            "low", description="Power range of the box. 'high' is much stronger."
        )
        mode: str | int = Field(
            "continuous", description=f"2B mode, by name ({', '.join(MODES)}) or number."
        )
        param_c: int | None = Field(
            None, ge=2, le=100, description="Mode parameter C (usually speed), 2-100."
        )
        param_d: int | None = Field(
            None, ge=1, le=100, description="Mode parameter D (usually feel), 1-100."
        )
        min_output: BoxLevel = Field(
            0,
            description="Box level (0-100) for the lowest non-zero output; 0 = off stays off. "
            "A number, or per channel: { A = 10, B = 15 }.",
        )
        max_output: BoxLevel = Field(
            100,
            description="Box level (1-100) sent at full scale (level 1.0). "
            "A number, or per channel: { A = 40, B = 50 }.",
        )
        serial_timeout: float = Field(2.0, gt=0, description="Serial read timeout, seconds.")
        delay: float = Field(
            0.04, ge=0.034, le=1, description="Wait after each command before reading the reply."
        )
        stall_timeout: float = Field(
            3.0, gt=0, description="A command taking longer than this is a device fault."
        )

        @field_validator("min_output", "max_output")
        @classmethod
        def _check_box_level(cls, value: BoxLevel, info) -> BoxLevel:
            values = value if isinstance(value, dict) else {"all": value}
            unknown = set(values) - set(CHANNELS) - {"all"}
            if unknown:
                raise ValueError(f"unknown channel(s) {sorted(unknown)}; the 2B has A and B")
            low = 1 if info.field_name == "max_output" else 0
            for ch, v in values.items():
                if not low <= v <= 100:
                    raise ValueError(f"{info.field_name}[{ch}] must be {low}-100")
            return value

        def box_range(self, channel: str) -> tuple[int, int]:
            def pick(value: BoxLevel, default: int) -> int:
                return value.get(channel, default) if isinstance(value, dict) else value

            return pick(self.min_output, 0), pick(self.max_output, 100)

        @model_validator(mode="after")
        def _check_min_below_max(self):
            for ch in CHANNELS:
                low, high = self.box_range(ch)
                if low > high:
                    raise ValueError(f"min_output ({low}) > max_output ({high}) for channel {ch}")
            return self

        @field_validator("mode")
        @classmethod
        def _check_mode(cls, value: str | int) -> str | int:
            if isinstance(value, str):
                if value.lower() not in MODES:
                    raise ValueError(f"unknown mode {value!r}; choose from {', '.join(MODES)}")
                return value.lower()
            if value not in MODES.values():
                raise ValueError(f"mode number must be 0-{max(MODES.values())}")
            return value

    #: Creates the estim2py connection; replaced in tests with a fake box.
    connection_factory: Callable[[str, float, float], Any] = staticmethod(_open_estim2py)

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self._conn: Any = None
        self._io_lock = threading.Lock()  # one serial command at a time
        self._target = dict.fromkeys(CHANNELS, 0)
        self._sent = dict.fromkeys(CHANNELS, 0)
        self._wakeup = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._error: str | None = None
        self._busy_since: float | None = None

    @property
    def channels(self) -> tuple[str, ...]:
        return CHANNELS

    @property
    def mode_id(self) -> int:
        mode = self.options.mode
        return MODES[mode] if isinstance(mode, str) else mode

    def to_box(self, level: float, channel: str) -> int:
        """Normalised level (0..1) -> box level: 0 when off, otherwise
        min_output..max_output of that channel."""
        if level < OFF_THRESHOLD:
            return 0
        low, high = self.options.box_range(channel)
        return round(low + min(level, 1.0) * (high - low))

    # -- serial calls (worker thread) ------------------------------------

    def _call(self, method: str, *args: Any) -> Any:
        with self._io_lock:
            self._busy_since = time.monotonic()
            try:
                # Drop stale bytes (e.g. a reply that arrived after a timeout) so
                # the reply read next belongs to this command. estim2py 0.3.0
                # does this itself; 0.2.2 (the last version for Python < 3.13)
                # does not.
                port = getattr(self._conn, "serial", None)
                if port is not None and hasattr(port, "reset_input_buffer"):
                    port.reset_input_buffer()
                return getattr(self._conn, method)(*args)
            except Exception as exc:
                raise DeviceError(f"2B {method}{args}: {_describe(exc)}") from exc
            finally:
                self._busy_since = None

    def _open_and_configure(self) -> None:
        opts = self.options
        try:
            self._conn = self.connection_factory(opts.port, opts.serial_timeout, opts.delay)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"cannot open 2B on {opts.port}: {_describe(exc)}") from exc
        self._call("get_status")
        self._call("kill")
        # Changing power range or mode resets A/B to 0 on the box.
        self._call("high" if opts.power == "high" else "low")
        self._call("set_mode", self.mode_id)
        if opts.param_c is not None:
            self._call("set_channel", "C", opts.param_c)
        if opts.param_d is not None:
            self._call("set_channel", "D", opts.param_d)
        for ch in CHANNELS:
            self._call("set_channel", ch, 0)
        status = self._call("get_status")
        expected_power = "H" if opts.power == "high" else "L"
        if status.power != expected_power or status.mode != self.mode_id:
            self._call("kill")
            raise DeviceError(
                f"2B did not accept the settings: power={status.power!r} mode={status.mode!r}, "
                f"expected power={expected_power!r} mode={self.mode_id}"
            )
        log.info(
            "2B connected on %s: firmware %s, battery %s, mode %s, %s power",
            opts.port,
            status.version,
            status.battery,
            self.mode_id,
            opts.power,
        )

    # -- Device interface --------------------------------------------------

    async def connect(self) -> None:
        await asyncio.to_thread(self._open_and_configure)
        self._error = None
        self._worker = asyncio.create_task(self._run(), name="estim2b-writer")

    async def set_levels(self, levels: Mapping[str, float]) -> None:
        self._check_healthy()
        self._target = {ch: self.to_box(levels.get(ch, 0.0), ch) for ch in CHANNELS}
        self._wakeup.set()

    async def stop(self) -> None:
        self._target = dict.fromkeys(CHANNELS, 0)
        if self._conn is None:
            return
        await asyncio.to_thread(self._call, "kill")
        self._sent = dict.fromkeys(CHANNELS, 0)

    async def disconnect(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
        if self._conn is None:
            return
        try:
            await self.stop()
        except DeviceError:
            log.critical("could not zero the 2B on disconnect - check the box!", exc_info=True)
        serial_port = getattr(self._conn, "serial", None)
        if serial_port is not None and hasattr(serial_port, "close"):
            await asyncio.to_thread(serial_port.close)
        self._conn = None

    # -- internals -----------------------------------------------------------

    def _check_healthy(self) -> None:
        if self._conn is None:
            raise DeviceError("2B is not connected")
        if self._error is not None:
            raise DeviceError(self._error)
        busy = self._busy_since
        if busy is not None and time.monotonic() - busy > self.options.stall_timeout:
            raise DeviceError(f"2B not responding for {self.options.stall_timeout:.0f}s")

    async def _run(self) -> None:
        """Send the newest target levels to the box; stale ones are skipped."""
        try:
            while True:
                await self._wakeup.wait()
                self._wakeup.clear()
                for ch in CHANNELS:
                    value = self._target[ch]
                    if value == self._sent[ch]:
                        continue
                    status = await asyncio.to_thread(self._call, "set_channel", ch, value)
                    self._sent[ch] = value
                    reported = getattr(status, ch.lower(), None)
                    if isinstance(reported, int) and reported // 2 != value:
                        log.warning("2B reports %s=%s after setting %s", ch, reported // 2, value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc) if isinstance(exc, DeviceError) else _describe(exc)
            log.error("2B writer stopped: %s", self._error)
