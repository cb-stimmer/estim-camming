"""The engine shuts down (and zeroes the device) when its controlling GUI goes away."""

import subprocess
import sys
import time

from conftest_engine import free_port, write_engine_config


def start_engine(tmp_path, *extra):
    config = write_engine_config(tmp_path, free_port(), start_armed=True)
    return subprocess.Popen(
        [sys.executable, "-m", "estim_camming", "run", "-c", str(config), *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_for_line(proc, needle, seconds=10.0):
    lines = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if needle in line:
            return lines
    raise AssertionError(f"{needle!r} not seen in output:\n{''.join(lines)}")


def test_engine_stops_when_stdin_closes(tmp_path):
    proc = start_engine(tmp_path, "--exit-on-stdin-close")
    try:
        wait_for_line(proc, "control panel")
        proc.stdin.close()  # what happens when the GUI exits or crashes
        assert proc.wait(timeout=10) == 0
        rest = proc.stdout.read()
        assert "controlling GUI went away" in rest
        assert "EMERGENCY STOP (shutdown)" in rest  # device zeroed on the way out
    finally:
        proc.kill()


def test_engine_ignores_stdin_without_the_flag(tmp_path):
    proc = start_engine(tmp_path)
    try:
        wait_for_line(proc, "control panel")
        proc.stdin.close()
        time.sleep(1)
        assert proc.poll() is None  # still running
        proc.terminate()
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()
