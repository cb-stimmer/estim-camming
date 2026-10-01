"""Entry point for ``estim-camming gui``."""

from __future__ import annotations

import argparse
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from estim_camming.config import load_config
from estim_camming.gui import model
from estim_camming.gui.client import ControlClient
from estim_camming.gui.engine import EngineProcess
from estim_camming.gui.window import ControlWindow


def run_gui(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not config.overlay.enabled:
        print(
            "error: the GUI uses the control API of the overlay server; "
            "set [overlay] enabled = true",
            file=sys.stderr,
        )
        return 2
    host, port = model.control_address(config.overlay)
    if not args.connect and model.port_in_use(host, port):
        print(
            f"error: something is already running on {host}:{port}. If that is "
            "estim-camming, use 'estim-camming gui --connect', or stop it first.",
            file=sys.stderr,
        )
        return 2
    token = config.overlay.control_token
    client_token = token.get_secret_value() if token is not None else None

    # The desktop file name becomes the Wayland app_id / X11 WM_CLASS. Without it
    # KDE sees "python3", so taskbar icons and KWin window rules can't target us.
    QApplication.setDesktopFileName(model.APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(model.APP_ID)
    app.setApplicationDisplayName("estim-camming")
    app.setWindowIcon(QIcon(str(model.icon_path())))

    engine = None
    if not args.connect:
        engine = EngineProcess(args.config, verbose=getattr(args, "verbose", False))
    client = ControlClient(model.api_base(config.overlay), client_token)
    window = ControlWindow(client, engine)

    # Ctrl+C / SIGTERM close the window, which shuts the engine down cleanly.
    signal.signal(signal.SIGINT, lambda *_: window.close())
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    wake = QTimer()  # let Python run signal handlers while Qt's loop is busy
    wake.start(200)
    wake.timeout.connect(lambda: None)

    window.show()
    if engine is not None:
        engine.start()
    client.start()
    code = app.exec()
    if engine is not None:
        engine.shutdown()
    return code
