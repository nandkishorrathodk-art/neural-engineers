"""Static validation of model-generated Python artifacts before execution.

Model-authored test scripts are executed inside the employee container. When a
generated script is malformed (a syntax error, an undefined name, a misuse of
the pytest API, an unterminated string) the command exits non-zero and the task
fails for a reason that has nothing to do with the *work* the task represents.
These are generation failures, not infrastructure failures, and they must be
caught and named as such before a single byte reaches the container.

This module parses each generated Python file and reports the concrete problems
it can prove from the source alone. It deliberately proves only what is
statically certain: an undefined name is reported only when it is not a known
builtin, not bound anywhere in the file, not a pytest fixture/attribute, and not
imported by the ``from __future__``/import machinery. It never rewrites the
source and never fabricates a repair; callers decide whether to replan.
"""

from __future__ import annotations

import ast
import base64
import binascii
import builtins
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# pytest's public surface, taken from the pinned runtime in the employee image
# (pytest 6.2.5). A generated script that reaches into ``pytest.<name>`` for an
# attribute outside this set is calling an API the runtime does not expose.
_PYTEST_PUBLIC_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "Cache",
        "CaptureFixture",
        "Class",
        "Collector",
        "ExitCode",
        "File",
        "FixtureLookupError",
        "FixtureRequest",
        "Function",
        "Instance",
        "Item",
        "LogCaptureFixture",
        "Module",
        "MonkeyPatch",
        "Package",
        "PytestAssertRewriteWarning",
        "PytestCacheWarning",
        "PytestCollectionWarning",
        "PytestConfigWarning",
        "PytestDeprecationWarning",
        "PytestExperimentalApiWarning",
        "PytestUnhandledCoroutineWarning",
        "PytestUnhandledThreadExceptionWarning",
        "PytestUnknownMarkWarning",
        "PytestUnraisableExceptionWarning",
        "PytestWarning",
        "Pytester",
        "Session",
        "TempPathFactory",
        "TempdirFactory",
        "Testdir",
        "UsageError",
        "WarningsRecorder",
        "approx",
        "cmdline",
        "collect",
        "console_main",
        "deprecated_call",
        "exit",
        "fail",
        "fixture",
        "freeze_includes",
        "hookimpl",
        "hookspec",
        "importorskip",
        "main",
        "mark",
        "param",
        "raises",
        "register_assert_rewrite",
        "set_trace",
        "skip",
        "warns",
        "xfail",
        "yield_fixture",
    }
)

_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins)) | {"__name__", "__file__", "__doc__"}


@dataclass
class GeneratedScriptProblem:
    path: str
    kind: str
    detail: str

    def render(self) -> str:
        return f"{self.path}: {self.kind}: {self.detail}"


@dataclass
class GeneratedScriptReport:
    problems: list[GeneratedScriptProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        return "; ".join(problem.render() for problem in self.problems)


def _is_pytest_module_alias(name: str) -> bool:
    return name in {"pytest", "pt"}


class _ScopeCollector(ast.NodeVisitor):
    """Collect every name bound in a module and every free name it reads."""

    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.loaded: list[tuple[str, int]] = []
        self.pytest_attribute_uses: list[tuple[str, int]] = []
        self._pytest_aliases: set[str] = set()

    def _bind(self, name: str | None) -> None:
        if name:
            self.bound.add(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self._bind(local)
            if alias.name == "pytest" and local == "pytest":
                self._pytest_aliases.add(local)
            if alias.name == "pytest":
                self._pytest_aliases.add(local)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._bind(node.name)
        self._bind_function_signature(node.args)
        for child in node.body:
            self.visit(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._bind(node.name)
        self._bind_function_signature(node.args)
        for child in node.body:
            self.visit(child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name)
        for child in node.body:
            self.visit(child)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # ``except X as e:`` binds ``e`` via a raw string, not a Name node, so
        # the generic visit never sees it. Reading ``e`` in the handler body is
        # valid and must not be reported as an undefined name.
        if node.name:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda's parameters are bound by its signature; without this the
        # body's reads would look free.
        self._bind_function_signature(node.args)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self._bind(name)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self._bind(name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self._bind(node.rest)
        self.generic_visit(node)

    def _bind_function_signature(self, args: ast.arguments) -> None:
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self._bind(arg.arg)
        if args.vararg:
            self._bind(args.vararg.arg)
        if args.kwarg:
            self._bind(args.kwarg.arg)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)
        else:
            self.loaded.append((node.id, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and _is_pytest_module_alias(node.value.id)
            and node.value.id in self._pytest_aliases
        ):
            self.pytest_attribute_uses.append((node.attr, node.lineno))
        self.generic_visit(node)


def _has_unprovable_namespace(tree: ast.Module) -> bool:
    """True when the module can bind names the parser cannot see (star import/exec)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"exec", "eval", "globals", "locals", "vars"}:
                return True
    return False


def _collect_pytest_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            aliases.add(node.module or "pytest")
    return aliases


def validate_generated_python(path: str, content: str) -> list[GeneratedScriptProblem]:
    """Return the statically provable problems in one generated Python file."""
    problems: list[GeneratedScriptProblem] = []
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as exc:
        problems.append(
            GeneratedScriptProblem(
                path=path,
                kind="syntax_error",
                detail=f"line {exc.lineno}: {exc.msg}",
            )
        )
        return problems
    except ValueError as exc:
        problems.append(
            GeneratedScriptProblem(path=path, kind="syntax_error", detail=str(exc))
        )
        return problems

    collector = _ScopeCollector()
    collector.visit(tree)
    pytest_aliases = _collect_pytest_aliases(tree) or {"pytest"}

    # A star import or a dynamic ``exec``/``globals()`` binding can define any
    # name at runtime, so an undefined-name verdict would not be provable.
    if _has_unprovable_namespace(tree):
        return problems

    for name, lineno in collector.loaded:
        if name in collector.bound or name in _BUILTIN_NAMES:
            continue
        if name in pytest_aliases:
            continue
        if name in {"self", "cls"}:
            continue
        problems.append(
            GeneratedScriptProblem(
                path=path,
                kind="undefined_name",
                detail=f"name {name!r} at line {lineno} is never defined or imported",
            )
        )

    for attribute, lineno in collector.pytest_attribute_uses:
        if attribute not in _PYTEST_PUBLIC_ATTRIBUTES:
            problems.append(
                GeneratedScriptProblem(
                    path=path,
                    kind="invalid_pytest_api",
                    detail=(
                        f"pytest.{attribute} at line {lineno} is not part of the "
                        "pytest 6.2.5 public API"
                    ),
                )
            )
    return problems


def validate_generated_files(
    files: Iterable[tuple[str, str]],
) -> list[GeneratedScriptProblem]:
    """Validate every Python file in a plan; ignore non-Python artifacts."""
    problems: list[GeneratedScriptProblem] = []
    for path, content in files:
        if not str(path).lower().endswith(".py"):
            continue
        problems.extend(validate_generated_python(str(path), content or ""))
    return problems


# ``TerminalPlan.materialize`` persists each file as argv writes of the form
# ``python3 -c "import base64; open('path','wb').write(base64.b64decode('<chunk>'))"``.
# When the model bypasses the ``files`` field and emits those writes as raw
# commands, the script never appears in ``files`` and the file validator above
# never sees it. This pattern recovers the persisted path and base64 payload so
# the same static checks still run.
_WRITE_COMMAND_RE = re.compile(
    r"open\(\s*(?P<path>'[^']*'|\"[^\"]*\")\s*,\s*'(?P<mode>[wa]b)'\s*\)\s*"
    r"\.write\(\s*base64\.b64decode\(\s*(?P<chunk>'[^']*'|\"[^\"]*\")\s*\)\s*\)"
)


def iter_persisted_python(
    commands: Iterable[Any],
) -> Iterable[tuple[str, str]]:
    """Yield ``(path, content)`` for Python files a plan persists via argv writes.

    Each path's chunks are concatenated in argv order (first write truncates,
    later writes append), mirroring how the container would materialize them.
    """
    buffers: dict[str, list[str]] = {}
    order: list[str] = []
    for command in commands:
        if not isinstance(command, (list, tuple)) or not command:
            continue
        if command[0] not in {"python", "python3"} or "-c" not in command:
            continue
        index = list(command).index("-c") + 1
        if index >= len(command):
            continue
        code = str(command[index])
        for match in _WRITE_COMMAND_RE.finditer(code):
            path = match.group("path")[1:-1]
            chunk = match.group("chunk")[1:-1]
            try:
                decoded = base64.b64decode(chunk).decode("utf-8", "replace")
            except (ValueError, binascii.Error):
                continue
            if path not in buffers:
                buffers[path] = []
                order.append(path)
            buffers[path].append(decoded)
    for path in order:
        if path.lower().endswith(".py"):
            yield path, "".join(buffers[path])


def validate_generated_commands(commands: Iterable[Any]) -> list[GeneratedScriptProblem]:
    """Validate Python scripts a plan persists through raw write commands."""
    return validate_generated_files(iter_persisted_python(commands))


__all__ = [
    "GeneratedScriptProblem",
    "GeneratedScriptReport",
    "validate_generated_commands",
    "validate_generated_files",
    "iter_persisted_python",
    "validate_generated_python",
]
