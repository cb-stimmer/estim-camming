"""Command-line interface: ``estim-camming <command>`` (see ``--help``)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import threading
from importlib.resources import files
from pathlib import Path

from estim_camming import __version__
from estim_camming.config import ConfigError, load_config, load_config_file
from estim_camming.plugins import DEVICES, PATTERNS, PLATFORMS, PluginError

DEFAULT_CONFIG = "config.toml"
EXAMPLE_CONFIG = files("estim_camming") / "example_config.toml"


def _build(config_path: str):
    from estim_camming.app import Application
    from estim_camming.rules_io import RulesFile

    config, digest = load_config_file(config_path)
    return Application(config, RulesFile(config_path, digest))


def _watch_stdin(loop: asyncio.AbstractEventLoop, task: asyncio.Task) -> None:
    """Cancel ``task`` when stdin reaches end-of-file.

    Used when the GUI starts the engine: the GUI holds the other end of the
    pipe, so if the GUI exits or crashes the pipe closes and the engine shuts
    down (and zeroes the device) instead of running on unattended.
    """

    def wait() -> None:
        with contextlib.suppress(Exception):
            while sys.stdin.buffer.read(1024):
                pass
        logging.getLogger(__name__).warning("controlling GUI went away - shutting down")
        with contextlib.suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(task.cancel)

    threading.Thread(target=wait, name="stdin-watchdog", daemon=True).start()


async def _run_until_signalled(app, exit_on_stdin_close: bool = False) -> None:
    # Turn SIGTERM into cancellation so the device is stopped on the way out.
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):  # not available on Windows
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    if exit_on_stdin_close:
        _watch_stdin(loop, task)
    with contextlib.suppress(asyncio.CancelledError):
        await app.run()


def cmd_run(args: argparse.Namespace) -> int:
    app = _build(args.config)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_until_signalled(app, args.exit_on_stdin_close))
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from estim_camming.gui.main import run_gui
    except ImportError as exc:
        print(
            f"error: the GUI needs PySide6 (install with: pip install -e '.[gui]'): {exc}",
            file=sys.stderr,
        )
        return 2
    return run_gui(args)


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


def cmd_desktop_entry(args: argparse.Namespace) -> int:
    """Install (or remove) a launcher entry and icon for the GUI in the user's
    data directory (~/.local/share), so menus and taskbars show the app."""
    import shutil

    from estim_camming.gui import model  # no Qt needed

    apps = model.data_home() / "applications"
    icons = model.data_home() / "icons" / "hicolor" / "scalable" / "apps"
    entry = apps / f"{model.APP_ID}.desktop"
    icon = icons / f"{model.APP_ID}.svg"
    if args.remove:
        for path in (entry, icon):
            if path.exists():
                path.unlink()
                print(f"removed {path}")
        return 0
    config = Path(args.config)
    if not config.is_file():
        print(f"error: config file not found: {config}", file=sys.stderr)
        return 2
    load_config(config)  # refuse to install an entry for a broken config
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    entry.write_text(model.desktop_entry(config))
    shutil.copyfile(model.icon_path(), icon)
    print(f"wrote {entry}")
    print(f"wrote {icon}")
    print("estim-camming now appears in the application menu (it may take a moment).")
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
    p.add_argument(
        "--exit-on-stdin-close",
        action="store_true",
        help="shut down when stdin closes (used by the GUI to stop the engine with it)",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("gui", help="open the control window (starts the application too)")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument(
        "--connect",
        action="store_true",
        help="connect to an already running application instead of starting one",
    )
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("desktop-entry", help="add (or --remove) a menu/taskbar entry for the GUI")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    p.add_argument("--remove", action="store_true")
    p.set_defaults(func=cmd_desktop_entry)

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
