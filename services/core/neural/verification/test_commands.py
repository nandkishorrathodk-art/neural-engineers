"""Shared recognition rules for independently replayable test commands."""

from __future__ import annotations

import re
from typing import Any

TEST_ONLY_ACCEPTANCE = frozenset({
    "test passes",
    "tests pass",
    "tests exit successfully",
    "automated tests pass",
    "automated validation passes",
    "pytest passes",
})


def normalize_acceptance_criteria(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def replayable_test_commands(receipt: Any) -> list[list[str]]:
    planned = receipt.get("planned_commands") if isinstance(receipt, dict) else None
    if not isinstance(planned, list):
        return []
    tests: list[list[str]] = []
    for candidate in planned[:12]:
        if not isinstance(candidate, list) or not candidate or not all(isinstance(arg, str) for arg in candidate):
            continue
        command = candidate
        is_pytest = command[0] == "pytest" or (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "pytest"]
        )
        is_unittest = command[0] in {"python", "python3"} and command[1:3] == ["-m", "unittest"]
        is_npm_test = command[0] == "npm" and (
            command[1:2] == ["test"] or command[1:3] == ["run", "test"]
        )
        if (is_pytest or is_unittest or is_npm_test) and command not in tests:
            tests.append(command)
    return tests


def validate_test_plan(acceptance_criteria: Any, planned: Any) -> None:
    """Fail closed before execution when a test-only task has no replayable suite."""
    normalized = normalize_acceptance_criteria(acceptance_criteria)
    if normalized in TEST_ONLY_ACCEPTANCE and not replayable_test_commands({"planned_commands": planned}):
        raise ValueError(
            "Test-only task plans must include a replayable pytest, unittest, or npm test command."
        )
