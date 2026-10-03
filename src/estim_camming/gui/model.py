"""Qt-free helpers for the GUI: where to connect, and how to show state."""

from __future__ import annotations

import os
import socket
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from estim_camming.config import OverlayConfig

#: Wayland app_id / X11 window class of the GUI. KWin window rules match on it,
#: and the desktop entry has the same name, so taskbars find the icon.
APP_ID = "estim-camming"


def icon_path() -> Path:
    return Path(str(files("estim_camming.gui") / "estim-camming.svg"))


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _exec_arg(arg: str) -> str:
    """Quote one argument for a .desktop Exec key (Desktop Entry spec)."""
    arg = arg.replace("%", "%%")  # % starts a field code
    if arg and not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    # The whole Exec value is a string key too, so backslashes are escaped again.
    return '"' + escaped.replace("\\", "\\\\") + '"'


def _string_value(value: str) -> str:
    """Escape a value for a .desktop string key (backslash escapes)."""
    return value.replace("\\", "\\\\")


def desktop_entry(config: Path, python: str = sys.executable, icon: Path | None = None) -> str:
    """A freedesktop .desktop file that starts the GUI with ``config``.

    ``icon`` is the installed icon file. An absolute path works at once; a theme
    name (the default, ``APP_ID``) needs the icon theme cache to pick it up."""
    config = config.resolve()
    icon_value = _string_value(str(icon.resolve())) if icon is not None else APP_ID
    args = [python, "-m", "estim_camming", "gui", "-c", str(config)]
    exec_line = " ".join(_exec_arg(a) for a in args)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=estim-camming\n"
        "Comment=Tip-controlled device control panel\n"
        f"Exec={exec_line}\n"
        f"Path={_string_value(str(config.parent))}\n"
        f"Icon={icon_value}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;\n"
        f"StartupWMClass={APP_ID}\n"
    )


def control_address(overlay: OverlayConfig) -> tuple[str, int]:
    """Host/port to reach the control API from this machine."""
    host = overlay.host
    if host in ("", "0.0.0.0", "::"):  # listening on all interfaces
        host = "127.0.0.1"
    return host, overlay.port


def api_base(overlay: OverlayConfig) -> str:
    host, port = control_address(overlay)
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"http://{host}:{port}"


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if something already accepts connections on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def token_range(item: dict[str, Any]) -> str:
    low, high = item.get("min_tokens"), item.get("max_tokens")
    if high is None:
        return f"{low}+"
    if high == low:
        return f"{low}"
    return f"{low}–{high}"


def format_outputs(action: dict[str, Any]) -> str:
    """'A: pulse 80% · B: wave 50%' for an action dict from the state snapshot."""
    return " · ".join(
        f"{o['channel']}: {o['pattern']} {o['intensity']:.0%}" for o in action.get("outputs", [])
    )


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds >= 60 else f"{seconds}s"


def tip_text(tip: dict[str, Any]) -> str:
    return f"{tip.get('username', '?')}  {tip.get('tokens', 0)} tk  ({tip.get('platform', '')})"


def queue_text(action: dict[str, Any]) -> str:
    tip = action.get("tip") or {}
    who = f"  ({tip['username']})" if tip.get("username") else ""
    return f"{action.get('label', '?')}{who}"


def platform_text(name: str, status: dict[str, Any]) -> str:
    state = "connected" if status.get("connected") else "DISCONNECTED"
    detail = f": {status['detail']}" if status.get("detail") else ""
    return f"{name}: {state}{detail}"
