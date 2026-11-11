from __future__ import annotations

import ast
import re

from neural.engineering.models import ReviewGateError, SecurityReviewReceipt
from neural.engineering.worktrees import EngineeringWorktreeManager, WorktreeProvisionError
from neural.logger import get_logger

logger = get_logger("ancos.engineering.security_review")

_DANGEROUS_AST_FUNCS = {"eval", "exec", "__import__", "system", "popen"}
_DANGEROUS_SYSTEM_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("posix", "system"),
}
_SECRET_PATTERNS = [
    re.compile(
        r"""(?i)(?:password|passwd|secret|secret_key|api_key|api_secret|client_secret|token|auth_token|access_token|private_key)\s*=\s*["'][^"'\s]{6,}["']"""
    ),
    re.compile(r"""-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"""),
]


class SecurityASTVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[dict[str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct functions: eval, exec, __import__, system, popen
        if isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_AST_FUNCS:
                self.violations.append({
                    "file": self.filename,
                    "line": str(node.lineno),
                    "type": "dangerous_function",
                    "details": f"Use of forbidden function '{node.func.id}'",
                })
            elif node.func.id in {"run", "call", "check_call", "check_output", "Popen"}:
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and bool(kw.value.value):
                        self.violations.append({
                            "file": self.filename,
                            "line": str(node.lineno),
                            "type": "shell_true_injection",
                            "details": f"Direct call to '{node.func.id}' with shell=True",
                        })
        # Check attribute calls: os.system, subprocess with shell=True
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                pair = (node.func.value.id, node.func.attr)
                if pair in _DANGEROUS_SYSTEM_CALLS:
                    self.violations.append({
                        "file": self.filename,
                        "line": str(node.lineno),
                        "type": "system_injection_vector",
                        "details": f"Call to dangerous '{node.func.value.id}.{node.func.attr}'",
                    })
                if node.func.value.id in {"subprocess", "asyncio"}:
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and bool(kw.value.value):
                            self.violations.append({
                                "file": self.filename,
                                "line": str(node.lineno),
                                "type": "shell_true_injection",
                                "details": "subprocess called with shell=True",
                            })
        self.generic_visit(node)


class SecurityReviewEngine:
    """
    Independent Security Review Engine.
    Conducts AST-based vulnerability analysis, secret pattern detection,
    dangerous system call inspection, and signs cryptographic SecurityReviewReceipt.
    """

    def review_task_worktree(
        self,
        worktree_manager: EngineeringWorktreeManager,
        task_id: str,
        security_engineer_id: str,
    ) -> SecurityReviewReceipt:
        try:
            worktree_receipt = worktree_manager.verify_receipt(task_id)
        except WorktreeProvisionError as exc:
            raise ReviewGateError(f"Cannot perform security review: worktree provenance invalid: {exc}") from exc

        worktree_path = worktree_receipt.path

        # Ensure worktree has no uncommitted changes (ignoring runtime test caches)
        status_lines = [
            line.strip()
            for line in worktree_manager._git_at(worktree_path, "status", "--porcelain").splitlines()
            if line.strip()
        ]
        dirty = []
        for line in status_lines:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                path_part = parts[1].strip()
                if any(ign in path_part for ign in ["__pycache__", ".pytest_cache", ".coverage", ".ruff_cache"]):
                    continue
            dirty.append(line)
        if dirty:
            raise ReviewGateError(
                "Cannot perform security review: task worktree has uncommitted changes. "
                "All code changes must be committed before review."
            )

        head_commit = worktree_manager.get_head_commit(task_id)
        changed_files = worktree_manager.list_changed_files(task_id)
        if not changed_files:
            # If no committed changes listed via diff yet, scan worktree files
            changed_files = [
                str(p.relative_to(worktree_path))
                for p in worktree_path.rglob("*.py")
                if not any(part.startswith(".") for part in p.parts)
            ]

        vulnerabilities: list[dict[str, str]] = []
        scanned_count = 0

        for rel_path in changed_files:
            file_path = (worktree_path / rel_path).resolve()
            if not worktree_manager._is_within(file_path, worktree_path):
                continue
            if not file_path.is_file():
                continue
            scanned_count += 1
            content = file_path.read_text(encoding="utf-8", errors="replace")

            # 1. Secret scanning
            for pat in _SECRET_PATTERNS:
                if pat.search(content):
                    vulnerabilities.append({
                        "file": rel_path,
                        "line": "0",
                        "type": "hardcoded_secret",
                        "details": "Hardcoded secret or private key pattern detected",
                    })

            # 2. Python AST scanning
            if file_path.suffix == ".py":
                try:
                    tree = ast.parse(content, filename=rel_path)
                    visitor = SecurityASTVisitor(rel_path)
                    visitor.visit(tree)
                    vulnerabilities.extend(visitor.violations)
                except SyntaxError as syn_err:
                    vulnerabilities.append({
                        "file": rel_path,
                        "line": str(syn_err.lineno or 0),
                        "type": "syntax_error",
                        "details": f"SyntaxError in code during security AST scan: {syn_err}",
                    })

        # Calculate security score
        score = 1.0
        for vuln in vulnerabilities:
            if vuln["type"] in {"dangerous_function", "system_injection_vector", "shell_true_injection", "hardcoded_secret"}:
                score -= 0.5
            else:
                score -= 0.2
        score = max(0.0, round(score, 2))
        passed = (len(vulnerabilities) == 0) and (score >= 0.8)

        logger.info(
            "Completed independent security review",
            task_id=task_id,
            security_engineer=security_engineer_id,
            scanned_files=scanned_count,
            vulnerabilities_count=len(vulnerabilities),
            score=score,
            passed=passed,
        )

        return SecurityReviewReceipt.create(
            task_id=task_id,
            security_engineer_id=security_engineer_id,
            passed=passed,
            vulnerabilities_found=vulnerabilities,
            scanned_files_count=scanned_count,
            security_score=score,
            head_commit=head_commit,
        )
