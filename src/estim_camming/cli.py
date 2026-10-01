"""Command-line interface: ``estim-camming {run,check,init,userscript,plugins}``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from importlib.resources import files
from pathlib import Path

from estim_camming import __version__
from estim_camming.config import ConfigError, load_config
from estim_camming.plugins import DEVICES, PATTERNS, PLATFORMS, PluginError

DEFAULT_CONFIG = "config.toml"
EXAMPLE_CONFIG = files("estim_camming") / "example_config.toml"


def _build(config_path: str):
    from estim_camming.app import Application

    return Application(load_config(config_path))


async def _run_until_signalled(app) -> None:
    # Turn SIGTERM into cancellation so the device is stopped on the way out.
    task = asyncio.current_task()
    assert task is not None
    with contextlib.suppress(NotImplementedError):  # not available on Windows
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await app.run()


def cmd_run(args: argparse.Namespace) -> int:
    app = _build(args.config)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_until_signalled(app))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    app = _build(args.config)
    print(f"config OK: device '{app.device.name}' channels {list(app.device.channels)}")
    for p in app.platforms:
        print(f"  platform: {p.describe()}")
    for rule in app.rules.rules:
        upper = rule.max_tokens if rule.max_tokens is not None else "+"
        outputs = ", ".join(
            f"{o.channel}: {o.pattern} {o.intensity:.0%}" for o in rule.outputs(app.device.channels)
        )
        print(f"  rule: {rule.min_tokens}..{upper} -> {rule.display_label} ({outputs})")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.write_text(EXAMPLE_CONFIG.read_text())
    print(f"wrote {target}")
    return 0


def cmd_userscript(args: argparse.Namespace) -> int:
    from estim_camming.platforms.bridge import BridgePlatform

    app = _build(args.config)
    bridges = [p for p in app.platforms if isinstance(p, BridgePlatform)]
    if not bridges:
        print("no enabled bridge platforms (e.g. 'stripchat') in the config", file=sys.stderr)
        return 1
    out_dir = Path(args.output_dir)
    for platform in bridges:
        target = out_dir / f"{platform.site}.user.js"
        target.write_text(platform.userscript())
        note = f" ({platform.options.mode} mode)"
        if platform.options.mode == "dom" and not platform.options.tip_selector:
            note = " (dom discovery mode: tip_selector is empty)"
        print(f"wrote {target}{note}")
    print("Install it in Tampermonkey (Dashboard > Utilities > Import from file). It contains")
    print("your bridge token: don't share it.")
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    for title, registry in (("platforms", PLATFORMS), ("devices", DEVICES), ("patterns", PATTERNS)):
        print(f"{title}:")
        for name in registry.names():
            cls = registry.get(name)
            doc = (cls.__doc__ or "").strip().splitlines()
            print(f"  {name:12} {doc[0] if doc else ''}")
            for field, info in cls.Options.model_fields.items():
                desc = f" - {info.description}" if info.description else ""
                print(f"      {field}{desc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="estim-camming", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the application")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="validate a config file without connecting")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("init", help="write an example config file")
    p.add_argument("path", nargs="?", default=DEFAULT_CONFIG)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("userscript", help="generate browser userscripts for bridge platforms")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("-o", "--output-dir", default=".")
    p.set_defaults(func=cmd_userscript)

    p = sub.add_parser("plugins", help="list available platforms, devices and patterns")
    p.set_defaults(func=cmd_plugins)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return args.func(args)
    except (ConfigError, PluginError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
