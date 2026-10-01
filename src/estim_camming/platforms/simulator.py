"""Generates random tips: for testing without a live stream."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator

from pydantic import Field, model_validator

from estim_camming.events import TipEvent
from estim_camming.platforms.base import Platform
from estim_camming.plugins import PLATFORMS, PluginOptions


@PLATFORMS.register("simulator")
class SimulatorPlatform(Platform):
    """Random tips for testing without a live stream."""

    class Options(PluginOptions):
        interval: float = Field(10.0, gt=0, description="Average seconds between tips.")
        min_tokens: int = Field(1, ge=1)
        max_tokens: int = Field(100, ge=1)
        usernames: list[str] = Field(
            default_factory=lambda: ["alice", "bob", "carol", "dave", "eve"], min_length=1
        )
        seed: int | None = None

        @model_validator(mode="after")
        def _check_range(self):
            if self.max_tokens < self.min_tokens:
                raise ValueError("max_tokens must be >= min_tokens")
            return self

    async def stream(self) -> AsyncIterator[TipEvent]:
        opts = self.options
        rng = random.Random(opts.seed)
        while True:
            await asyncio.sleep(rng.expovariate(1.0 / opts.interval))
            yield TipEvent(
                platform=self.name,
                username=rng.choice(opts.usernames),
                tokens=rng.randint(opts.min_tokens, opts.max_tokens),
            )
