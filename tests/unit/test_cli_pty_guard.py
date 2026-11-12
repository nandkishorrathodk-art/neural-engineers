"""
Unit tests for PTYExecutionGuard and deadlock prevention.
"""

from __future__ import annotations

import sys
from pathlib import Path

from neural.cli.pty_guard import PTYExecutionGuard


def test_pty_guard_basic_command(tmp_path: Path):
    guard = PTYExecutionGuard(cwd=tmp_path)
    res = guard.run_command([sys.executable, "-c", "print('hello from pty')"])
    assert res.exit_code == 0
    assert "hello from pty" in res.stdout
    assert not res.timed_out
    assert res.success


def test_pty_guard_streaming_callbacks(tmp_path: Path):
    guard = PTYExecutionGuard(cwd=tmp_path)
    lines_captured: list[str] = []

    def on_line(line: str):
        lines_captured.append(line.strip())

    script = "for i in range(5): print(f'line_{i}')"
    res = guard.run_command(
        [sys.executable, "-c", script],
        on_stdout_line=on_line,
    )
    assert res.exit_code == 0
    assert len(lines_captured) == 5
    assert lines_captured[0] == "line_0"
    assert lines_captured[4] == "line_4"


def test_pty_guard_deadlock_prevention_large_output(tmp_path: Path):
    """
    Ensures that generating large volume output (exceeding standard OS pipe buffer)
    does not deadlock the process.
    """
    guard = PTYExecutionGuard(cwd=tmp_path, default_timeout=10.0)
    # Output 10,000 lines of 50 chars each = ~500 KB, well beyond standard 4KB-64KB pipe buffer
    script = "import sys\nfor i in range(10000):\n    sys.stdout.write(f'data_chunk_{i:06d}\\n')\n"
    res = guard.run_command([sys.executable, "-c", script])
    assert res.exit_code == 0
    assert not res.timed_out
    assert "data_chunk_009999" in res.stdout


def test_pty_guard_timeout_protection(tmp_path: Path):
    guard = PTYExecutionGuard(cwd=tmp_path)
    # Sleep longer than timeout
    script = "import time\ntime.sleep(5)\n"
    res = guard.run_command([sys.executable, "-c", script], timeout=0.5)
    assert res.timed_out is True
    assert res.exit_code == -9
    assert not res.success


def test_pty_guard_stdin_devnull_prevents_interactive_hang(tmp_path: Path):
    """
    Ensures that commands prompting for interactive stdin do not hang indefinitely,
    because stdin is set to subprocess.DEVNULL by default.
    """
    guard = PTYExecutionGuard(cwd=tmp_path, default_timeout=5.0)
    # Calling input() with stdin=DEVNULL raises EOFError immediately without hanging
    script = "try:\n    val = input('prompt: ')\n    print('got', val)\nexcept EOFError:\n    print('handled EOFError')\n"
    res = guard.run_command([sys.executable, "-c", script], timeout=3.0)
    assert res.exit_code == 0
    assert not res.timed_out
    assert "handled EOFError" in res.stdout


def test_pty_guard_pythonunbuffered_env(tmp_path: Path):
    """Ensures PYTHONUNBUFFERED=1 is set in the subprocess environment."""
    guard = PTYExecutionGuard(cwd=tmp_path)
    script = "import os; print('UNBUFFERED=' + os.environ.get('PYTHONUNBUFFERED', '0'))"
    res = guard.run_command([sys.executable, "-c", script])
    assert res.exit_code == 0
    assert "UNBUFFERED=1" in res.stdout

