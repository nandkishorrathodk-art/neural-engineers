"""Typed worker failures that carry structured data across the failure contract."""

from __future__ import annotations


class TerminalCommandFailure(RuntimeError):
    """A terminal command exited non-zero during task execution.

    The exit code is carried structurally so retry classification never depends on
    parsing a free-text error message.
    """

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


__all__ = ["TerminalCommandFailure"]
