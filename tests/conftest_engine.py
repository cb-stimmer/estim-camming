"""Helpers for tests that start the engine as a separate process."""

import socket
import textwrap
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_engine_config(directory: Path, port: int, start_armed: bool = False) -> Path:
    """Dummy device, no platforms, one catch-all rule."""
    path = directory / "config.toml"
    path.write_text(
        textwrap.dedent(
            f"""
            [device]
            type = "dummy"

            [safety]
            start_armed = {"true" if start_armed else "false"}

            [overlay]
            port = {port}

            [[rules]]
            name = "any"
            min_tokens = 1
            intensity = 0.5
            duration = 30
            """
        )
    )
    return path
