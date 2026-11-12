"""
PTY and Streaming Subprocess Runner with Deadlock Guards.
Provides cross-platform non-blocking streaming execution, safe pipe draining,
timeout enforcement, and child process tree termination to prevent terminal deadlocks.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class SubprocessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined_output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr or ""


class PTYExecutionGuard:
    """
    Subprocess execution guard with pipe-deadlock prevention and streaming output.
    Uses dedicated reader threads for stdout and stderr to guarantee neither stream
    blocks the child process, even when high volumes of output are generated.
    """

    def __init__(
        self,
        default_timeout: float = 60.0,
        cwd: Optional[Path | str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.default_timeout = default_timeout
        self.cwd = Path(cwd).resolve() if cwd else Path.cwd()
        self.env = dict(os.environ if env is None else env)

    def run_command(
        self,
        command: str | list[str],
        cwd: Optional[Path | str] = None,
        timeout: Optional[float] = None,
        env: Optional[dict[str, str]] = None,
        on_stdout_line: Optional[Callable[[str], None]] = None,
        on_stderr_line: Optional[Callable[[str], None]] = None,
        stdin: Any = subprocess.DEVNULL,
    ) -> SubprocessResult:
        """
        Executes a command synchronously with streaming pipe draining and timeout guard.
        """
        run_cwd = str(Path(cwd).resolve()) if cwd else str(self.cwd)
        run_timeout = timeout if timeout is not None else self.default_timeout
        run_env = {**self.env, **(env or {})}

        # Ensure child Python subprocesses can discover site-packages, local modules, and virtualenv
        extra_paths: list[str] = [run_cwd]
        for p in sys.path:
            if p and os.path.isdir(p) and ("site-packages" in p or "dist-packages" in p):
                if p not in extra_paths:
                    extra_paths.append(p)

        repo_root = Path(__file__).resolve().parents[4]
        venv_site = repo_root / ".venv" / "Lib" / "site-packages"
        if venv_site.is_dir() and str(venv_site) not in extra_paths:
            extra_paths.append(str(venv_site))

        core_src = repo_root / "services" / "core"
        if core_src.is_dir() and str(core_src) not in extra_paths:
            extra_paths.append(str(core_src))

        existing_ppath = run_env.get("PYTHONPATH", "")
        combined_ppath = [p for p in extra_paths if p]
        if existing_ppath:
            combined_ppath.extend(existing_ppath.split(os.pathsep))
        seen_paths: set[str] = set()
        deduped_ppath: list[str] = []
        for p in combined_ppath:
            norm = os.path.normpath(p)
            if norm not in seen_paths:
                seen_paths.add(norm)
                deduped_ppath.append(p)
        run_env["PYTHONPATH"] = os.pathsep.join(deduped_ppath)
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        run_env["PYTHONUNBUFFERED"] = "1"

        if "VIRTUAL_ENV" not in run_env:
            venv_dir = repo_root / ".venv"
            if venv_dir.is_dir():
                run_env["VIRTUAL_ENV"] = str(venv_dir)

        shell_mode = isinstance(command, str)
        start_time = time.monotonic()

        popen_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=run_cwd,
            env=run_env,
            shell=shell_mode,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        out_q: queue.Queue[tuple[str, str]] = queue.Queue()

        def _reader(pipe, stream_name: str) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    out_q.put((stream_name, line))
            except Exception:
                pass
            finally:
                pipe.close()

        t_out = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        interrupted = False
        deadline = start_time + run_timeout

        try:
            while True:
                # Drain queue items
                while True:
                    try:
                        stream_name, line = out_q.get_nowait()
                        if stream_name == "stdout":
                            stdout_lines.append(line)
                            if on_stdout_line:
                                try:
                                    on_stdout_line(line)
                                except Exception:
                                    pass
                        else:
                            stderr_lines.append(line)
                            if on_stderr_line:
                                try:
                                    on_stderr_line(line)
                                except Exception:
                                    pass
                    except queue.Empty:
                        break

                if proc.poll() is not None:
                    break

                if time.monotonic() > deadline:
                    timed_out = True
                    self._kill_process_tree(proc)
                    break

                time.sleep(0.02)
        except KeyboardInterrupt:
            interrupted = True
            self._kill_process_tree(proc)

        # Wait for readers to finish draining remaining output
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        # Final drain
        while not out_q.empty():
            try:
                stream_name, line = out_q.get_nowait()
                if stream_name == "stdout":
                    stdout_lines.append(line)
                    if on_stdout_line:
                        try:
                            on_stdout_line(line)
                        except Exception:
                            pass
                else:
                    stderr_lines.append(line)
                    if on_stderr_line:
                        try:
                            on_stderr_line(line)
                        except Exception:
                            pass
            except queue.Empty:
                break

        duration = time.monotonic() - start_time
        if timed_out:
            exit_code = -9
        elif interrupted:
            exit_code = -2
        else:
            exit_code = proc.poll() if proc.poll() is not None else 1

        return SubprocessResult(
            exit_code=exit_code,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines) + ("\nExecution interrupted by user (SIGINT)." if interrupted else ""),
            duration_seconds=duration,
            timed_out=timed_out,
        )

    def _kill_process_tree(self, proc: subprocess.Popen) -> None:
        """Kills the process and any spawned child processes."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
            else:
                import signal
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
