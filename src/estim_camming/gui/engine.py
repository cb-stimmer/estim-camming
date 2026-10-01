"""Runs the application (``estim-camming run``) as a child process of the GUI."""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

STDIN_CLOSE_WAIT_MS = 5000
TERMINATE_WAIT_MS = 3000


class EngineProcess(QObject):
    """The engine runs in its own process so a frozen or crashed window can't
    stall it. It is started with ``--exit-on-stdin-close``: the GUI holds its
    stdin, so when the GUI exits for any reason the engine shuts down and
    zeroes the device."""

    output = Signal(str)
    exited = Signal(int)  # exit code; -1 when it crashed or was killed

    def __init__(self, config_path: str, verbose: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._config = config_path
        self._verbose = verbose
        self._stopping = False
        self._buffer = b""
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")  # log lines arrive as they are written
        self._proc.setProcessEnvironment(env)
        self._proc.readyReadStandardOutput.connect(self._read)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

    @property
    def stopping(self) -> bool:
        return self._stopping

    def arguments(self) -> list[str]:
        args = ["-m", "estim_camming"]
        if self._verbose:
            args.append("-v")
        return [*args, "run", "-c", self._config, "--exit-on-stdin-close"]

    def start(self) -> None:
        self._proc.start(sys.executable, self.arguments())

    def running(self) -> bool:
        return self._proc.state() != QProcess.ProcessState.NotRunning

    def shutdown(self) -> None:
        """Stop the engine; it zeroes the device on the way out. Blocks until
        it has exited (at most a few seconds)."""
        if not self.running():
            return
        self._stopping = True
        self._proc.closeWriteChannel()  # EOF on stdin: graceful shutdown
        if self._proc.waitForFinished(STDIN_CLOSE_WAIT_MS):
            return
        self.output.emit("engine did not stop - terminating it")
        self._proc.terminate()  # SIGTERM: also a graceful shutdown
        if self._proc.waitForFinished(TERMINATE_WAIT_MS):
            return
        self.output.emit("engine still running - killing it; CHECK THE DEVICE")
        self._proc.kill()
        self._proc.waitForFinished(1000)

    def _read(self) -> None:
        self._buffer += bytes(self._proc.readAllStandardOutput().data())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            self.output.emit(line.decode(errors="replace").rstrip())

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._buffer:
            self.output.emit(self._buffer.decode(errors="replace").rstrip())
            self._buffer = b""
        crashed = status == QProcess.ExitStatus.CrashExit
        self.exited.emit(-1 if crashed else code)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.output.emit(f"could not start the engine: {self._proc.errorString()}")
            self.exited.emit(-1)
