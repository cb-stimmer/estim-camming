"""Open the user manual in the default browser (F1, User guide buttons)."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from estim_camming.gui import model


def open_help(page: tuple[str, str]) -> str | None:
    """Open ``(path, anchor)`` of the manual. Returns the URL, or None if no
    browser could be started."""
    url = model.help_url(*page, local_root=model.local_docs())
    return url if QDesktopServices.openUrl(QUrl(url)) else None
