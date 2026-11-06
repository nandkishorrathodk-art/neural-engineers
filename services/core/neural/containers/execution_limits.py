"""Shared limits for bounded terminal commands and their HTTP request chain."""

TERMINAL_COMMAND_TIMEOUT_SECONDS = 300
BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS = TERMINAL_COMMAND_TIMEOUT_SECONDS + 10
WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS = BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS + 10


def command_with_timeout(command: str | list[str]) -> list[str]:
    """Run a command under GNU timeout and preserve shell-string semantics."""
    bounded = [
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=5s",
        f"{TERMINAL_COMMAND_TIMEOUT_SECONDS}s",
    ]
    if isinstance(command, str):
        return [*bounded, "/bin/sh", "-lc", command]
    return [*bounded, *command]
