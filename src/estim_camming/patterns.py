"""Waveform patterns: shape the output level of an action over time.

A pattern maps elapsed time to a relative level in 0..1, which the scheduler
multiplies by the action intensity. Add new patterns by subclassing
:class:`Pattern` and registering it (see ``docs/design/rules-and-patterns.md``).
"""

from __future__ import annotations

import math
from abc import abstractmethod

from pydantic import Field, model_validator

from estim_camming.plugins import PATTERNS, Plugin, PluginOptions


class Pattern(Plugin):
    @abstractmethod
    def level(self, t: float, duration: float) -> float:
        """Relative level (0..1) at ``t`` seconds into an action of ``duration`` seconds."""


@PATTERNS.register("constant")
class Constant(Pattern):
    """Steady output for the whole action."""

    def level(self, t: float, duration: float) -> float:
        return 1.0


@PATTERNS.register("pulse")
class Pulse(Pattern):
    """On/off square wave."""

    class Options(PluginOptions):
        period: float = Field(1.0, gt=0, description="Seconds per on/off cycle.")
        duty: float = Field(0.5, gt=0, le=1, description="Fraction of each cycle that is on.")

    def level(self, t: float, duration: float) -> float:
        period = self.options.period
        return 1.0 if (t % period) < period * self.options.duty else 0.0


@PATTERNS.register("ramp")
class Ramp(Pattern):
    """Linear change from ``start`` to ``end`` over the action."""

    class Options(PluginOptions):
        start: float = Field(0.0, ge=0, le=1)
        end: float = Field(1.0, ge=0, le=1)

    def level(self, t: float, duration: float) -> float:
        progress = min(t / duration, 1.0) if duration > 0 else 1.0
        return self.options.start + (self.options.end - self.options.start) * progress


@PATTERNS.register("wave")
class Wave(Pattern):
    """Smooth sine wave between ``low`` and ``high``."""

    class Options(PluginOptions):
        period: float = Field(2.0, gt=0, description="Seconds per wave.")
        low: float = Field(0.2, ge=0, le=1)
        high: float = Field(1.0, ge=0, le=1)

        @model_validator(mode="after")
        def _check_range(self):
            if self.low > self.high:
                raise ValueError("low must be <= high")
            return self

    def level(self, t: float, duration: float) -> float:
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * t / self.options.period)
        return self.options.low + (self.options.high - self.options.low) * phase
