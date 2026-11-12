from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.models import (
    ProtectedPathViolationError,
    SelfEvolutionProposal,
    SelfEvolutionVerificationReceipt,
    is_protected_path,
)
from neural.logger import get_logger

logger = get_logger("ancos.evolution.multi_gate_verifier")


_FORBIDDEN_CALLS = {"eval", "exec", "__import__"}
_FORBIDDEN_MODULE_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execl"),
    ("os", "execle"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execlp"),
    ("os", "execvpe"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnvp"),
    ("os", "spawnvpe"),
    ("posix", "system"),
    ("posix", "popen"),
    ("pty", "spawn"),
}
_SECRET_PATTERNS = [
    re.compile(r"""(?i)(?:api_key|secret|password|auth_token)\s*=\s*['"][a-zA-Z0-9_\-]{16,}['"]"""),
    re.compile(r"""(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}"""),
]


class ASTSecurityScanner:
    """AST scanner inspecting Python files for security violations and forbidden functions."""

    @classmethod
    def scan_code(cls, filename: str, code: str) -> tuple[bool, float, list[dict[str, Any]]]:
        vulnerabilities: list[dict[str, Any]] = []

        # Check for hardcoded secrets
        for pat in _SECRET_PATTERNS:
            for match in pat.finditer(code):
                vulnerabilities.append({
                    "file": filename,
                    "type": "hardcoded_secret",
                    "severity": "CRITICAL",
                    "detail": f"Matched secret pattern '{match.group()[:12]}...'",
                })

        try:
            tree = ast.parse(code, filename=filename)
        except SyntaxError as syn_err:
            vulnerabilities.append({
                "file": filename,
                "type": "syntax_error",
                "severity": "CRITICAL",
                "detail": str(syn_err),
            })
            return False, 0.0, vulnerabilities

        for node in ast.walk(tree):
            # Check for direct or attribute calls
            if isinstance(node, ast.Call):
                func = node.func
                # 1. Direct name call like eval(...) or exec(...)
                if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                    vulnerabilities.append({
                        "file": filename,
                        "type": "forbidden_builtin_call",
                        "severity": "CRITICAL",
                        "line": getattr(node, "lineno", 0),
                        "detail": f"Forbidden built-in call to '{func.id}'",
                    })
                # 2. Attribute call like builtins.eval(...) or os.system(...)
                elif isinstance(func, ast.Attribute):
                    # Flag forbidden builtins accessed as attributes (e.g. builtins.eval, __builtins__.exec)
                    if func.attr in _FORBIDDEN_CALLS:
                        vulnerabilities.append({
                            "file": filename,
                            "type": "forbidden_builtin_call",
                            "severity": "CRITICAL",
                            "line": getattr(node, "lineno", 0),
                            "detail": f"Forbidden built-in call to attribute '{func.attr}'",
                        })
                    elif isinstance(func.value, ast.Name):
                        if (func.value.id, func.attr) in _FORBIDDEN_MODULE_CALLS:
                            vulnerabilities.append({
                                "file": filename,
                                "type": "dangerous_os_call",
                                "severity": "CRITICAL",
                                "line": getattr(node, "lineno", 0),
                                "detail": f"Dangerous low-level call to '{func.value.id}.{func.attr}'",
                            })
                    # 3. Check subprocess with shell=True
                    if func.attr in ("Popen", "run", "call", "check_call", "check_output"):
                        for kw in node.keywords:
                            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                vulnerabilities.append({
                                    "file": filename,
                                    "type": "shell_true_injection",
                                    "severity": "HIGH",
                                    "line": getattr(node, "lineno", 0),
                                    "detail": "Subprocess invocation with shell=True is forbidden",
                                })

        passed = len(vulnerabilities) == 0
        score = 1.0 if passed else max(0.0, 1.0 - (len(vulnerabilities) * 0.3))
        return passed, score, vulnerabilities


class SelfEvolutionMultiGateVerifier:
    """
    Independent Multi-Gate Verification Engine for Self-Evolution.
    Acts under the authority of IndependentDeliverableVerifier to enforce 4 independent gates:
    1. Full Regression Gate: all unit/integration tests pass (exit code 0).
    2. Static Analysis Gate: static inspection (ruff or AST syntax) clean.
    3. AST Security Gate: zero vulnerabilities, no dangerous builtins, score >= 0.99.
    4. Benchmark Comparison Gate: candidate satisfies baseline metrics and target goals.
    Strictly verifies the Protected Core Files Invariant.
    Issues cryptographically signed SelfEvolutionVerificationReceipt.
    """

    def __init__(
        self,
        candidate_manager: SelfEvolutionCandidateManager,
        verifier_id: str = "IndependentDeliverableVerifier",
    ) -> None:
        self.candidate_manager = candidate_manager
        self.verifier_id = verifier_id

    def verify_candidate(
        self,
        proposal: SelfEvolutionProposal,
        test_command: Optional[list[str]] = None,
        candidate_benchmark_metrics: Optional[dict[str, Any]] = None,
        benchmark_command: Optional[list[str]] = None,
    ) -> SelfEvolutionVerificationReceipt:
        """
        Executes the complete multi-gate independent verification suite on a staged candidate.
        """
        proposal_id = proposal.proposal_id
        receipt = self.candidate_manager.verify_candidate_receipt(proposal_id)
        candidate_path = receipt.path
        changed_files = self.candidate_manager.list_changed_files(proposal_id)

        logger.info(
            "Starting multi-gate independent verification for proposal",
            proposal_id=proposal_id,
            changed_files=changed_files,
        )

        diagnostic_notes: list[str] = []

        # Gate 0: Protected Core Files Invariant Check on all changed files
        for f in changed_files:
            if is_protected_path(f):
                raise ProtectedPathViolationError(
                    f"Candidate modification touches prohibited path '{f}'. "
                    f"Self-evolution modifications to root policy, approval gates, or emergency halt latches are forbidden."
                )

        # Gate 0.5: Scope Boundary Check (verify changed files stay within proposal target scope)
        scope_passed = True
        target_names = {Path(p).name.lower() for p in proposal.target_files}
        target_stems = {Path(p).stem.lower().replace("test_", "") for p in proposal.target_files}
        for f in changed_files:
            f_norm = f.replace("\\", "/").lower()
            fname = Path(f).name.lower()
            is_target = any(
                f_norm == p.replace("\\", "/").lower() or f_norm.endswith(p.replace("\\", "/").lower())
                for p in proposal.target_files
            )
            is_target_name = fname in target_names
            is_associated_test = (fname.startswith("test_") or fname.endswith("_test.py")) and (
                any(stem in fname for stem in target_stems) or len(target_stems) == 0
            )
            if not (is_target or is_target_name or is_associated_test):
                scope_passed = False
                diagnostic_notes.append(
                    f"Scope Boundary Gate Failed: File '{f}' is outside authorized target files {proposal.target_files}"
                )

        # Gate 1: Full Regression Testing
        regression_passed, reg_notes = self._run_regression_gate(candidate_path, test_command)
        if not regression_passed:
            diagnostic_notes.append(f"Regression Gate Failed: {reg_notes}")

        # Gate 2: Static Analysis Gate
        static_passed, static_notes = self._run_static_analysis_gate(candidate_path, changed_files)
        if not static_passed:
            diagnostic_notes.append(f"Static Analysis Gate Failed: {static_notes}")

        # Gate 3: AST Security Gate
        ast_passed, ast_notes = self._run_ast_security_gate(candidate_path, changed_files)
        if not ast_passed:
            diagnostic_notes.append(f"AST Security Gate Failed: {ast_notes}")

        # Gate 4: Benchmark Comparison Gate (runs empirical checks, guards against unexecuted/blind trust)
        benchmark_passed, benchmark_comparison, bench_notes = self._run_benchmark_gate(
            proposal=proposal,
            candidate_metrics=candidate_benchmark_metrics,
            candidate_path=candidate_path,
            benchmark_command=benchmark_command,
        )
        if not benchmark_passed:
            diagnostic_notes.append(f"Benchmark Gate Failed: {bench_notes}")

        overall_passed = (
            scope_passed and regression_passed and static_passed and ast_passed and benchmark_passed
        )

        # Artifact digests
        artifact_digests: dict[str, str] = {}
        for f in changed_files:
            target_file = candidate_path / f
            if target_file.is_file():
                artifact_digests[f] = hashlib.sha256(target_file.read_bytes()).hexdigest()

        diff_stat = self.candidate_manager._worktree_git(candidate_path, "diff", "--stat", f"{receipt.base_commit}..HEAD")
        head_commit = self.candidate_manager._worktree_git(candidate_path, "rev-parse", "HEAD").strip()

        notes = (
            "All multi-gate verification checks (regression, static analysis, AST security, benchmark) passed cleanly."
            if overall_passed
            else " | ".join(diagnostic_notes)
        )

        verif_receipt = SelfEvolutionVerificationReceipt.create(
            proposal_id=proposal_id,
            candidate_branch=receipt.branch,
            passed=overall_passed,
            regression_passed=regression_passed,
            static_analysis_passed=static_passed,
            ast_security_passed=ast_passed,
            benchmark_passed=benchmark_passed,
            benchmark_comparison=benchmark_comparison,
            artifact_digests=artifact_digests,
            diff_stat=diff_stat,
            head_commit=head_commit,
            notes=notes,
            verifier_id=self.verifier_id,
        )

        logger.info(
            "Completed multi-gate verification",
            proposal_id=proposal_id,
            passed=verif_receipt.passed,
            receipt_id=verif_receipt.receipt_id,
        )
        return verif_receipt

    def _build_worktree_env(self, candidate_path: Path) -> dict[str, str]:
        """Builds clean execution environment with candidate repository paths in PYTHONPATH."""
        env = os.environ.copy()
        pythonpath_entries: list[str] = [str(candidate_path.resolve())]
        if (candidate_path / "services" / "core").is_dir():
            pythonpath_entries.insert(0, str((candidate_path / "services" / "core").resolve()))
        if "PYTHONPATH" in env and env["PYTHONPATH"]:
            for entry in env["PYTHONPATH"].split(os.pathsep):
                if entry.strip():
                    pythonpath_entries.append(str(Path(entry.strip()).resolve()))
        for p in sys.path:
            if "site-packages" in p:
                resolved_p = str(Path(p).resolve())
                if resolved_p not in pythonpath_entries:
                    pythonpath_entries.append(resolved_p)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
        return env

    def _run_regression_gate(
        self,
        candidate_path: Path,
        test_command: Optional[list[str]] = None,
    ) -> tuple[bool, str]:
        """Runs candidate test suite in candidate worktree."""
        cmd = list(test_command or [sys.executable, "-m", "pytest", "-v"])
        if cmd and cmd[0] in ("python", "python3"):
            cmd[0] = sys.executable

        env = self._build_worktree_env(candidate_path)

        try:
            res = subprocess.run(
                cmd,
                cwd=str(candidate_path),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            passed = res.returncode == 0
            detail = "All tests passed (exit 0)" if passed else (res.stderr or res.stdout)
            return passed, detail
        except Exception as exc:
            return False, f"Test execution exception: {exc}"

    def _run_static_analysis_gate(
        self,
        candidate_path: Path,
        changed_files: list[str],
    ) -> tuple[bool, str]:
        """Runs ruff static analysis or syntax check on changed Python files."""
        py_files = [f for f in changed_files if f.endswith(".py")]
        if not py_files:
            return True, "No Python files to lint."

        # Check if ruff or uv run ruff is available
        ruff_bin = shutil.which("ruff")
        uv_bin = shutil.which("uv")

        if uv_bin:
            try:
                res = subprocess.run(
                    [uv_bin, "run", "ruff", "check", "--select", "E,F", *py_files],
                    cwd=str(candidate_path),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0:
                    return True, "Ruff static analysis clean (0 issues)."
                else:
                    return False, f"Ruff violations: {res.stdout.strip()}"
            except Exception:
                pass

        if ruff_bin:
            try:
                res = subprocess.run(
                    [ruff_bin, "check", "--select", "E,F", *py_files],
                    cwd=str(candidate_path),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0:
                    return True, "Ruff static analysis clean (0 issues)."
                else:
                    return False, f"Ruff violations: {res.stdout.strip()}"
            except Exception:
                pass

        # Fallback to AST compile check
        for f in py_files:
            target = candidate_path / f
            if target.is_file():
                try:
                    compile(target.read_text(encoding="utf-8"), str(target), "exec")
                except SyntaxError as e:
                    return False, f"Syntax compilation error in {f}: {e}"

        return True, "Python compilation syntax validation passed."

    def _run_ast_security_gate(
        self,
        candidate_path: Path,
        changed_files: list[str],
    ) -> tuple[bool, str]:
        """Runs deep AST security scanning and secret checks."""
        py_files = [f for f in changed_files if f.endswith(".py")]
        all_vulns: list[dict[str, Any]] = []

        for f in py_files:
            target = candidate_path / f
            if target.is_file():
                content = target.read_text(encoding="utf-8")
                passed, score, vulns = ASTSecurityScanner.scan_code(f, content)
                if not passed or score < 0.99:
                    all_vulns.extend(vulns)

        if all_vulns:
            return False, f"Security vulnerabilities detected: {all_vulns}"
        return True, "AST security scan passed with 0 vulnerabilities (score 1.00)."

    def _run_benchmark_gate(
        self,
        proposal: SelfEvolutionProposal,
        candidate_metrics: Optional[dict[str, Any]] = None,
        candidate_path: Optional[Path] = None,
        benchmark_command: Optional[list[str]] = None,
    ) -> tuple[bool, dict[str, Any], str]:
        """
        Compares candidate metrics against baseline metrics and validates target goals.
        Runs empirical benchmark execution checks and validates that empirical benchmarks
        were truly executed rather than blindly trusting client-supplied metrics.
        """
        import time

        baseline = proposal.baseline_metrics
        target = proposal.target_goals

        comparison: dict[str, Any] = {
            "baseline": baseline,
            "target_goals": target,
            "candidate": dict(candidate_metrics) if candidate_metrics else {},
        }

        if not candidate_metrics and not benchmark_command:
            # If no empirical benchmark was measured or provided, benchmark gate fails closed
            return False, comparison, "No empirical benchmark metrics or benchmark command provided (fail-closed)."

        # 1. Run empirical benchmark command if provided
        if benchmark_command and candidate_path:
            try:
                env = self._build_worktree_env(candidate_path)
                cmd = list(benchmark_command)
                if cmd and cmd[0] in ("python", "python3"):
                    cmd[0] = sys.executable

                t_bench_start = time.perf_counter()
                res = subprocess.run(
                    cmd,
                    cwd=str(candidate_path),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
                t_bench_elapsed_ms = (time.perf_counter() - t_bench_start) * 1000.0

                if res.returncode != 0:
                    return (
                        False,
                        comparison,
                        f"Empirical benchmark command failed with code {res.returncode}: {res.stderr.strip()[:300]}",
                    )

                comparison["empirical_benchmark_command"] = cmd
                comparison["empirical_measured_time_ms"] = round(t_bench_elapsed_ms, 2)
                comparison["empirical_check_passed"] = True

                if not candidate_metrics:
                    candidate_metrics = {"execution_time_ms": round(t_bench_elapsed_ms, 2)}
                    comparison["candidate"] = candidate_metrics
            except Exception as exc:
                return False, comparison, f"Empirical benchmark execution failed: {exc}"

        # 2. Guard against blind trust of client-supplied metrics:
        # Require verification that metrics represent real execution and not fabricated or simulated placeholders
        if candidate_metrics:
            # Reject explicitly unexecuted, mock, or simulated metrics
            if candidate_metrics.get("executed") is False:
                return False, comparison, "Benchmark gate rejected: candidate metrics explicitly flagged as unexecuted."
            if candidate_metrics.get("simulated") is True:
                return False, comparison, "Benchmark gate rejected: simulated metrics are prohibited."
            if candidate_metrics.get("unverified") is True:
                return False, comparison, "Benchmark gate rejected: metrics marked as unverified."

            # Sanity validation on numeric fields: reject non-numbers, negative, NaN
            for metric_key in ("execution_time_ms", "latency_ms", "benchmark_throughput_ops_sec", "throughput_tokens_sec"):
                val = candidate_metrics.get(metric_key)
                if val is not None:
                    if not isinstance(val, (int, float)) or val < 0 or (val != val):
                        return False, comparison, f"Candidate metric '{metric_key}' has invalid non-numeric value: {val}"

            cand_err_val = candidate_metrics.get("error_rate")
            if cand_err_val is not None:
                if not isinstance(cand_err_val, (int, float)) or not (0.0 <= cand_err_val <= 1.0) or (cand_err_val != cand_err_val):
                    return False, comparison, f"Candidate error_rate must be a valid float in [0.0, 1.0], got: {cand_err_val}"

        # 3. Must have a valid execution time or latency metric
        cand_time = candidate_metrics.get("execution_time_ms") or candidate_metrics.get("latency_ms") if candidate_metrics else None
        if cand_time is None and "empirical_measured_time_ms" in comparison:
            cand_time = comparison["empirical_measured_time_ms"]

        if cand_time is None:
            return False, comparison, "Benchmark gate failed: no measurable execution time or latency found (fail-closed)."

        # Evaluate execution time / latency improvement
        base_time = baseline.get("execution_time_ms") or baseline.get("latency_ms")
        speedup = 1.0
        if base_time and cand_time and cand_time > 0:
            speedup = round(float(base_time) / float(cand_time), 2)
            comparison["speedup_ratio"] = speedup

        # Check target speedup ratio
        min_speedup = target.get("min_speedup_ratio", 1.0)
        if speedup < min_speedup:
            return False, comparison, f"Achieved speedup {speedup}x is below target goal {min_speedup}x"

        # Check error rate reduction
        base_err = baseline.get("error_rate")
        cand_err = candidate_metrics.get("error_rate") if candidate_metrics else None
        target_err = target.get("error_rate")
        if target_err is not None and cand_err is not None:
            if cand_err > target_err:
                return False, comparison, f"Candidate error rate {cand_err} exceeds target threshold {target_err}"
        if base_err is not None and cand_err is not None and cand_err > base_err:
            return False, comparison, f"Candidate error rate {cand_err} regressed above baseline {base_err}"

        # Check throughput improvement
        base_tp = baseline.get("benchmark_throughput_ops_sec") or baseline.get("throughput_tokens_sec")
        cand_tp = (
            candidate_metrics.get("benchmark_throughput_ops_sec") or candidate_metrics.get("throughput_tokens_sec")
            if candidate_metrics
            else None
        )
        if base_tp and cand_tp:
            tp_improvement = round(((cand_tp - base_tp) / base_tp) * 100, 2)
            comparison["throughput_improvement_pct"] = tp_improvement
            if cand_tp < base_tp:
                return False, comparison, "Candidate throughput regressed below baseline."

        comparison["passed"] = True
        return True, comparison, "Candidate satisfies all empirical benchmark targets."
