"""HTTP client for the control API, running on the Qt event loop."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

POLL_INTERVAL_MS = 250
STATE_TIMEOUT_MS = 2000
COMMAND_TIMEOUT_MS = 3000


class ControlClient(QObject):
    """Polls ``/api/state`` and sends control commands.

    Polling (not the WebSocket) keeps the client simple and self-healing: it
    reconnects by itself when the application (re)starts. Commands are sent
    immediately and trigger an extra poll, so the display reacts at once.
    """

    stateChanged = Signal(dict)
    connectionChanged = Signal(bool, str)  # connected, detail
    commandFailed = Signal(str, str)  # path, error

    def __init__(self, base_url: str, token: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._base = base_url.rstrip("/")
        self._token = token
        self._nam = QNetworkAccessManager(self)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll)
        self._polling: QNetworkReply | None = None
        self._connected: bool | None = None

    @property
    def base_url(self) -> str:
        return self._base

    def start(self) -> None:
        self._timer.start()
        self.poll()

    def stop(self) -> None:
        self._timer.stop()

    # -- state ---------------------------------------------------------------

    def poll(self) -> None:
        if self._polling is not None:  # previous request still running
            return
        request = QNetworkRequest(QUrl(self._base + "/api/state"))
        request.setTransferTimeout(STATE_TIMEOUT_MS)
        reply = self._nam.get(request)
        self._polling = reply
        reply.finished.connect(lambda: self._on_state(reply))

    def _on_state(self, reply: QNetworkReply) -> None:
        self._polling = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_connected(False, reply.errorString())
                return
            try:
                state = json.loads(bytes(reply.readAll().data()))
            except ValueError:
                self._set_connected(False, "invalid response")
                return
            self._set_connected(True, "")
            self.stateChanged.emit(state)
        finally:
            reply.deleteLater()

    def _set_connected(self, connected: bool, detail: str) -> None:
        if connected != self._connected:
            self._connected = connected
            self.connectionChanged.emit(connected, detail)

    # -- commands ------------------------------------------------------------

    def post(self, path: str, body: dict[str, Any] | None = None) -> None:
        request = QNetworkRequest(QUrl(self._base + path))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if self._token:
            request.setRawHeader(QByteArray(b"X-Control-Token"), QByteArray(self._token.encode()))
        request.setTransferTimeout(COMMAND_TIMEOUT_MS)
        reply = self._nam.post(request, QByteArray(json.dumps(body or {}).encode()))
        reply.finished.connect(lambda: self._on_command(path, reply))

    def _on_command(self, path: str, reply: QNetworkReply) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                detail = bytes(reply.readAll().data()).decode(errors="replace").strip()
                self.commandFailed.emit(path, detail or reply.errorString())
        finally:
            reply.deleteLater()
        self.poll()

    def arm(self) -> None:
        self.post("/api/arm")

    def emergency_stop(self) -> None:
        self.post("/api/stop")

    def set_scale(self, scale: float) -> None:
        self.post("/api/scale", {"scale": scale})

    def skip(self) -> None:
        self.post("/api/skip")

    def clear_queue(self) -> None:
        self.post("/api/clear")

    def send_tip(self, username: str, tokens: int) -> None:
        self.post("/api/tip", {"username": username, "tokens": tokens})
