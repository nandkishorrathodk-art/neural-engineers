"""Shared recognition rules for independently replayable test commands."""

from __future__ import annotations

import ast
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


def validate_workspace_artifact_plan(
    task_context: str,
    acceptance_criteria: Any,
    planned: Any,
) -> None:
    """Require explicitly requested files to be persisted before replayable tests run."""
    if normalize_acceptance_criteria(acceptance_criteria) not in TEST_ONLY_ACCEPTANCE:
        return

    context = str(task_context or "")
    expected_paths = set(re.findall(
        r"(?i)(?<![\w.-])[\w.-]+\.(?:py|js|ts|tsx|jsx|json|md|txt|ya?ml|toml|html|css|sql)(?![\w.-])",
        context,
    ))
    file_intent = bool(expected_paths) or bool(
        re.search(r"(?i)\b(create|write|implement|modify|edit|save|add|generate)\b", context)
    )
    if not file_intent:
        return

    planned_commands = planned if isinstance(planned, list) else []
    written_paths = _python_persisted_paths(planned_commands)
    if expected_paths:
        expected_basenames = {path.lower().split("/")[-1] for path in expected_paths}
        persisted = any(
            path.lower().replace("\\", "/").split("/")[-1] in expected_basenames
            for path in written_paths
        )
    else:
        persisted = bool(written_paths)
    if not persisted:
        raise ValueError(
            "Task requests a workspace file, but the plan does not persist a requested file before tests. "
            "Write the deliverable to disk, then run a separate replayable test command."
        )


def _python_persisted_paths(planned: list[Any]) -> set[str]:
    written: set[str] = set()
    for command in planned[:12]:
        if not isinstance(command, list) or not command or command[0] not in {"python", "python3"}:
            continue
        if "-c" not in command:
            continue
        code_index = command.index("-c") + 1
        if code_index >= len(command):
            continue
        try:
            tree = ast.parse(command[code_index])
        except (SyntaxError, ValueError):
            continue
        opened_variables: dict[str, str] = {}
        for scoped in ast.walk(tree):
            if not isinstance(scoped, (ast.With, ast.AsyncWith)):
                continue
            for item in scoped.items:
                path = _writable_open_path(item.context_expr)
                if path and isinstance(item.optional_vars, ast.Name):
                    opened_variables[item.optional_vars.id] = path
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr in {"write_text", "write_bytes"}:
                target = function.value
                if isinstance(target, ast.Call) and target.args and isinstance(target.args[0], ast.Constant):
                    if isinstance(target.args[0].value, str):
                        written.add(target.args[0].value)
            elif isinstance(function, ast.Attribute) and function.attr in {"write", "writelines"}:
                path = (
                    opened_variables.get(function.value.id)
                    if isinstance(function.value, ast.Name)
                    else _writable_open_path(function.value)
                )
                if path:
                    written.add(path)
    return written


def _writable_open_path(open_call: ast.expr) -> str | None:
    if not isinstance(open_call, ast.Call):
        return None
    path_node: ast.expr | None = None
    mode_node: ast.expr | None = None
    if isinstance(open_call.func, ast.Name) and open_call.func.id == "open":
        path_node = open_call.args[0] if open_call.args else None
        mode_node = open_call.args[1] if len(open_call.args) > 1 else None
    elif isinstance(open_call.func, ast.Attribute) and open_call.func.attr == "open":
        path_object = open_call.func.value
        if isinstance(path_object, ast.Call) and path_object.args:
            path_node = path_object.args[0]
        mode_node = open_call.args[0] if open_call.args else None
    if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
        return None
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return None
    if not any(mode in mode_node.value for mode in "wax"):
        return None
    return path_node.value
