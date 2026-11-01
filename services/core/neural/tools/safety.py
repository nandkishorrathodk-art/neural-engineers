from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from neural.logger import get_logger

logger = get_logger("ancos.tools.safety")


class ToolSafetyError(Exception):
    """Raised when authored tool code violates the AST safety boundary."""


@dataclass
class ToolSafetyReport:
    passed: bool
    violations: list[str] = field(default_factory=list)
    exported_functions: list[str] = field(default_factory=list)
    inferred_interface: dict[str, Any] = field(default_factory=dict)
    cyclomatic_complexity: int = 1
    detected_dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "exported_functions": self.exported_functions,
            "inferred_interface": self.inferred_interface,
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "detected_dependencies": self.detected_dependencies,
        }


class ToolASTSafetyValidator(ast.NodeVisitor):
    """
    AST Safety Envelope Validator for agent-authored tools.
    Enforces strict safety rules:
    1. Blocks dangerous modules, syscalls, and low-level OS operations.
    2. Prohibits hardcoded raw credentials, API keys, and sensitive tokens.
    3. Blocks infinite loops without termination conditions and escaped processes.
    4. Automatically infers OpenAPI/JSONSchema interface specifications from type hints and docstrings.
    """

    DISALLOWED_MODULES = {
        "subprocess",
        "pty",
        "commands",
        "ctypes",
        "winreg",
        "socket",
        "socketserver",
        "paramiko",
        "pickle",
        "shelve",
        "posix",
        "importlib",
        "builtins",
        "_thread",
        "threading",
        "multiprocessing",
        "signal",
    }

    DISALLOWED_CALLS = {
        "eval",
        "exec",
        "__import__",
        "compile",
        "input",
    }

    DANGEROUS_ATTRIBUTES = {
        "os": {
            "system", "popen", "spawn", "fork", "remove", "rmdir", "unlink", "rename",
            "kill", "killpg", "execv", "execve", "execl", "execlp", "execle", "chmod", "chown",
        },
        "sys": {"exit", "modules"},
        "shutil": {"rmtree", "rm", "move"},
    }

    CREDENTIAL_PATTERNS = [
        re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key
        re.compile(r"ghp_[0-9a-zA-Z]{36}"),  # GitHub Classic Token
        re.compile(r"github_pat_[0-9a-zA-Z_]{22,}"),  # GitHub Fine-grained PAT
        re.compile(r"glpat-[0-9a-zA-Z_-]{20,}"),  # GitLab Token
        re.compile(r"sk-(?:ant-|proj-)?[a-zA-Z0-9_\-]{20,}"),  # OpenAI/Anthropic/Provider Secret
        re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,}"),  # Slack Token
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),  # Private Key
        re.compile(r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|bearer_token)\s*=\s*['\"][a-zA-Z0-9_\-\.]{16,}['\"]"),
        re.compile(r"(?i)password\s*=\s*['\"][^'\"]{6,}['\"]"),
    ]

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.exported_functions: list[str] = []
        self.inferred_interfaces: dict[str, Any] = {}
        self.complexity: int = 1
        self.detected_dependencies: set[str] = set()
        self.imported_modules: dict[str, str] = {}
        self.imported_symbols: dict[str, tuple[str, str]] = {}

    def validate(self, code: str) -> ToolSafetyReport:
        self.violations = []
        self.exported_functions = []
        self.inferred_interfaces = {}
        self.complexity = 1
        self.detected_dependencies = set()
        self.imported_modules = {}
        self.imported_symbols = {}

        # 1. Regex credential scan on source text
        for pat in self.CREDENTIAL_PATTERNS:
            if pat.search(code):
                self.violations.append(f"Hardcoded credential or private token pattern detected matching: {pat.pattern}")

        # 2. Parse AST
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            self.violations.append(f"Syntax error compiling code: {e}")
            return ToolSafetyReport(
                passed=False,
                violations=self.violations,
            )

        # 3. Visit AST nodes
        self.visit(tree)

        passed = len(self.violations) == 0
        return ToolSafetyReport(
            passed=passed,
            violations=self.violations,
            exported_functions=self.exported_functions,
            inferred_interface=self.inferred_interfaces,
            cyclomatic_complexity=self.complexity,
            detected_dependencies=sorted(self.detected_dependencies),
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            if base_module in self.DISALLOWED_MODULES:
                self.violations.append(f"Disallowed import of dangerous module: {alias.name}")
            else:
                self.detected_dependencies.add(base_module)
                self.imported_modules[alias.asname or alias.name] = base_module
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base_module = node.module.split(".")[0]
            if base_module in self.DISALLOWED_MODULES:
                self.violations.append(f"Disallowed import from dangerous module: {node.module}")
            elif base_module in self.DANGEROUS_ATTRIBUTES:
                for alias in node.names:
                    if alias.name in self.DANGEROUS_ATTRIBUTES[base_module] or alias.name == "*":
                        self.violations.append(f"Disallowed import of dangerous attribute: {base_module}.{alias.name}")
                    else:
                        self.imported_symbols[alias.asname or alias.name] = (base_module, alias.name)
            else:
                self.detected_dependencies.add(base_module)
                for alias in node.names:
                    self.imported_symbols[alias.asname or alias.name] = (base_module, alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct calls like eval(...) or exec(...) or imported symbols like system(...)
        if isinstance(node.func, ast.Name):
            func_id = node.func.id
            if func_id in self.DISALLOWED_CALLS:
                self.violations.append(f"Disallowed direct call to restricted builtin: {func_id}()")
            elif func_id in self.imported_symbols:
                mod, orig_name = self.imported_symbols[func_id]
                if mod in self.DANGEROUS_ATTRIBUTES and orig_name in self.DANGEROUS_ATTRIBUTES[mod]:
                    self.violations.append(f"Disallowed system call: {mod}.{orig_name}() via '{func_id}'")
            elif func_id == "getattr":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    target_attr = node.args[1].value
                    if (
                        target_attr in self.DISALLOWED_CALLS
                        or any(target_attr in attrs for attrs in self.DANGEROUS_ATTRIBUTES.values())
                        or target_attr.startswith("__")
                    ):
                        self.violations.append(f"Disallowed dynamic attribute lookup: getattr(..., '{target_attr}')")

        # Check attribute calls like os.system(...) or o.system(...) or builtins.eval(...)
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in ("__subclasses__", "__bases__", "__class__"):
                self.violations.append(f"Disallowed sandbox introspection call: .{attr_name}()")
            elif attr_name in self.DISALLOWED_CALLS:
                self.violations.append(f"Disallowed call to restricted method/builtin: .{attr_name}()")

            if isinstance(node.func.value, ast.Name):
                raw_mod = node.func.value.id
                module_name = self.imported_modules.get(raw_mod, raw_mod)
                if module_name in self.DANGEROUS_ATTRIBUTES:
                    if attr_name in self.DANGEROUS_ATTRIBUTES[module_name]:
                        self.violations.append(f"Disallowed system call: {module_name}.{attr_name}()")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        is_constant_true = False
        if isinstance(node.test, ast.Constant) and bool(node.test.value) is True:
            is_constant_true = True
        elif isinstance(node.test, ast.Name) and node.test.id == "True":
            is_constant_true = True

        if is_constant_true:
            if not self._while_has_valid_exit(node.body):
                self.violations.append("Infinite while-loop detected without break, return, or raise termination condition")

        self.generic_visit(node)

    def _while_has_valid_exit(self, statements: list[ast.stmt]) -> bool:
        found_exit = False

        def walk_statements(nodes: list[ast.stmt], in_nested_loop: bool = False) -> None:
            nonlocal found_exit
            for stmt in nodes:
                if found_exit:
                    return
                # Do NOT traverse into inner functions, async functions, or classes
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(stmt, ast.Break):
                    if not in_nested_loop:
                        found_exit = True
                        return
                elif isinstance(stmt, (ast.Return, ast.Raise)):
                    found_exit = True
                    return
                elif isinstance(stmt, (ast.For, ast.While)):
                    walk_statements(stmt.body, in_nested_loop=True)
                    walk_statements(stmt.orelse, in_nested_loop=True)
                elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
                    for _, value in ast.iter_fields(stmt):
                        if isinstance(value, list):
                            walk_statements(value, in_nested_loop=in_nested_loop)

        walk_statements(statements)
        return found_exit

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inspect_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inspect_function(node)
        self.generic_visit(node)

    def _inspect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        func_name = node.name
        if not func_name.startswith("_"):
            self.exported_functions.append(func_name)

            docstring = ast.get_docstring(node) or ""
            properties: dict[str, Any] = {}
            required: list[str] = []

            for arg in node.args.args:
                arg_name = arg.arg
                if arg_name in ("self", "cls"):
                    continue
                type_name = "string"
                if arg.annotation:
                    type_str = ast.unparse(arg.annotation).lower()
                    if "int" in type_str:
                        type_name = "integer"
                    elif "float" in type_str:
                        type_name = "number"
                    elif "bool" in type_str:
                        type_name = "boolean"
                    elif "list" in type_str:
                        type_name = "array"
                    elif "dict" in type_str:
                        type_name = "object"

                properties[arg_name] = {
                    "type": type_name,
                    "description": f"Parameter {arg_name}",
                }
                required.append(arg_name)

            # Check defaults
            num_defaults = len(node.args.defaults)
            if num_defaults > 0:
                non_default_count = len(required) - num_defaults
                required = required[:non_default_count]

            self.inferred_interfaces[func_name] = {
                "type": "function",
                "function": {
                    "name": func_name,
                    "description": docstring.strip(),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

