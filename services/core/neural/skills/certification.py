from __future__ import annotations

import ast
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from neural.constants import SkillLevel
from neural.db.models import SkillCertification
from neural.db.repositories import (
    EmployeeRepository,
    SkillCertificationRepository,
    compute_certificate_hash,
)
from neural.logger import get_logger
from neural.sandboxes.workstation import ExecutionReceipt, WorkstationSandbox
from neural.skills.registry import SkillRegistry, get_skill_registry

logger = get_logger("ancos.skills.certification")


@dataclass
class CertificationResult:
    passed: bool
    certificate_id: str | None
    certificate_hash: str | None
    skill_name: str
    target_level: str
    employee_id: str
    score: float
    rubric_evaluation: dict[str, Any]
    receipt: dict[str, Any]
    notes: str
    certificate: SkillCertification | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.certificate:
            d["certificate"] = {
                "id": self.certificate.id,
                "certificate_id": self.certificate.certificate_id,
                "skill_name": self.certificate.skill_name,
                "level": self.certificate.level.value if hasattr(self.certificate.level, "value") else str(self.certificate.level),
                "score": self.certificate.score,
                "certificate_hash": self.certificate.certificate_hash,
                "status": self.certificate.status,
                "issued_at": self.certificate.issued_at.isoformat() if self.certificate.issued_at else None,
            }
        return d


class EmpiricalCertificationEngine:
    """
    Rigorous verification harness: Zero success-by-decree.
    Every certified skill is backed by reproducible sandbox execution receipts,
    grading rubrics, AST syntax analysis, security boundary checks,
    and cryptographic SHA-256 anchored certificate records linked to employee profiles.
    """

    def __init__(
        self,
        cert_repo: SkillCertificationRepository,
        employee_repo: EmployeeRepository,
        registry: SkillRegistry | None = None,
        sandbox: WorkstationSandbox | None = None,
    ):
        self.cert_repo = cert_repo
        self.employee_repo = employee_repo
        self.registry = registry or get_skill_registry()
        self.sandbox = sandbox or WorkstationSandbox()

    @staticmethod
    def _evaluate_ast_code_quality(code: str) -> tuple[float, list[str]]:
        issues = []
        try:
            tree = ast.parse(code)
            # Basic metrics: function definitions, statements
            funcs = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if not funcs:
                issues.append("No function definitions found in solution")
                return 0.7, issues
            return 1.0, issues
        except SyntaxError as e:
            issues.append(f"Syntax error at line {e.lineno}: {e.msg}")
            return 0.0, issues

    @staticmethod
    def _evaluate_security(code: str) -> tuple[float, list[str]]:
        import re

        violations: list[str] = []

        # 1. AST syntax inspection for forbidden calls and imports
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Direct function calls: eval, exec, compile, __import__
                    if isinstance(node.func, ast.Name):
                        if node.func.id in ("eval", "exec", "compile", "__import__"):
                            violations.append(f"Forbidden call to {node.func.id}()")
                    # Attribute calls: os.system, os.popen, subprocess.*, pty.spawn
                    elif isinstance(node.func, ast.Attribute):
                        attr_parts = []
                        curr: Any = node.func
                        while isinstance(curr, ast.Attribute):
                            attr_parts.append(curr.attr)
                            curr = curr.value
                        if isinstance(curr, ast.Name):
                            attr_parts.append(curr.id)
                            call_name = ".".join(reversed(attr_parts))
                            if any(call_name.startswith(p) for p in ("os.system", "os.popen", "os.spawn", "subprocess.", "pty.spawn")):
                                violations.append(f"Forbidden process execution call: {call_name}()")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("subprocess", "pty"):
                            violations.append(f"Forbidden import: {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in ("subprocess", "pty"):
                        violations.append(f"Forbidden import: {node.module}")
        except SyntaxError:
            pass

        # 2. Secret and credential patterns (quotes and formatting independent)
        secret_patterns = [
            (r"""(?i)\b(?:password|passwd|api_key|secret_key|auth_token)\s*=\s*['"][^'"]{2,}['"]""", "Hardcoded credentials or API key detected"),
        ]
        for pat, desc in secret_patterns:
            if re.search(pat, code):
                violations.append(desc)

        # 3. Text fallback for destructive patterns
        destructive_patterns = [
            ("rm -rf /", "Destructive shell command (rm -rf /)"),
            ("rm -rf /*", "Destructive shell command (rm -rf /*)"),
            (":(){ :|:& };:", "Fork bomb construct"),
            ("/etc/shadow", "Sensitive system file access (/etc/shadow)"),
        ]
        for pat, desc in destructive_patterns:
            if pat in code and desc not in violations:
                violations.append(desc)

        # Deduplicate preserving order
        seen = set()
        unique_violations = []
        for v in violations:
            if v not in seen:
                seen.add(v)
                unique_violations.append(v)

        if unique_violations:
            return max(0.0, 1.0 - (0.5 * len(unique_violations))), unique_violations
        return 1.0, []

    async def certify_skill(
        self,
        employee_id: str,
        skill_name: str,
        target_level: SkillLevel | str,
        code_submission: str,
        test_override: str | None = None,
        timeout: float | None = None,
    ) -> CertificationResult:
        """
        Executes an empirical certification test:
        1. Validates employee and benchmark existence.
        2. Executes candidate solution in isolated workstation sandbox against test harness.
        3. Parses execution receipts (exit code, assertions, stdout/stderr).
        4. Analyzes AST code quality and security policies.
        5. Grades through multi-criterion rubric.
        6. On pass: issues cryptographically anchored certificate and upgrades employee profile.
        7. On fail: returns failure diagnosis with zero updates (zero success-by-decree).
        """
        if not code_submission or not code_submission.strip():
            raise ValueError("Code submission cannot be empty")

        emp = await self.employee_repo.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee '{employee_id}' not found")

        # Resolve skill and benchmark
        target_lvl_enum = (
            target_level
            if isinstance(target_level, SkillLevel)
            else SkillLevel(target_level.lower())
        )
        skill_entry = self.registry.get_skill(skill_name)
        if not skill_entry:
            raise ValueError(f"Skill '{skill_name}' not found in registry")

        benchmark = self.registry.get_benchmark(skill_name, target_lvl_enum)
        if not benchmark and not test_override:
            raise ValueError(f"No benchmark found for skill '{skill_name}' at level '{target_lvl_enum.value}'")

        test_runner_code = test_override or (benchmark.test_runner_template if benchmark else "")
        benchmark_id = benchmark.benchmark_id if benchmark else "BENCH-CUSTOM-OVERRIDE"

        logger.info(
            "Running empirical skill certification test in sandbox",
            employee_id=employee_id,
            skill=skill_name,
            level=target_lvl_enum.value,
            benchmark_id=benchmark_id,
        )

        # 1. Run in isolated sandbox
        receipt: ExecutionReceipt = await self.sandbox.execute_python_code(
            code=code_submission,
            test_code=test_runner_code,
            filename="solution.py",
            timeout=timeout or 5.0,
        )

        # 2. Parse functional test assertions from stdout
        functional_score = 0.0
        parsed_test_data = {}
        if receipt.exit_code == 0 and receipt.stdout:
            try:
                for line in reversed(receipt.stdout.strip().splitlines()):
                    if line.startswith("{") and line.endswith("}"):
                        parsed_test_data = json.loads(line)
                        break
                if parsed_test_data.get("passed"):
                    functional_score = 1.0
                elif parsed_test_data.get("total_tests", 0) > 0:
                    functional_score = parsed_test_data.get("passed_tests", 0) / parsed_test_data.get("total_tests", 1)
            except Exception:
                functional_score = 1.0 if receipt.exit_code == 0 else 0.0

        # 3. Code Quality (AST)
        quality_score, quality_issues = self._evaluate_ast_code_quality(code_submission)

        # 4. Security Scan
        security_score, security_issues = self._evaluate_security(code_submission)

        # 5. Performance Score
        perf_score = 1.0
        if receipt.timed_out:
            perf_score = 0.0
        elif receipt.execution_time_seconds > 4.0:
            perf_score = 0.6
        elif receipt.execution_time_seconds > 2.0:
            perf_score = 0.85

        # 6. Evaluate Rubric
        criteria_scores = {
            "functional_correctness": functional_score,
            "code_quality": quality_score,
            "security_compliance": security_score,
            "performance": perf_score,
        }
        passed_rubric, overall_score, rubric_eval = skill_entry.grading_rubric.evaluate(criteria_scores)

        # Zero success-by-decree:
        # All test assertions must pass. If parsed_test_data is present, passed must be True.
        test_assertions_passed = (
            parsed_test_data.get("passed", False)
            if parsed_test_data
            else (receipt.exit_code == 0 and not receipt.timed_out)
        )

        passed = (
            passed_rubric
            and test_assertions_passed
            and receipt.exit_code == 0
            and receipt.passed_safety
            and not receipt.timed_out
            and len(security_issues) == 0
        )

        cert_record = None
        cert_id = None
        cert_hash = None

        if passed:
            cert_id = f"CERT-ANC-{uuid.uuid4().hex[:8].upper()}"
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cert_hash = compute_certificate_hash(
                certificate_id=cert_id,
                employee_id=emp.id,
                skill_name=skill_entry.name,
                level_str=target_lvl_enum.value,
                score=overall_score,
                benchmark_id=benchmark_id,
                issued_at_iso=now.isoformat(),
            )


            # Persist certificate
            cert_record = await self.cert_repo.create_certification(
                certificate_id=cert_id,
                employee_id=emp.id,
                skill_name=skill_entry.name,
                level=target_lvl_enum,
                score=overall_score,
                benchmark_id=benchmark_id,
                execution_receipt=receipt.to_dict(),
                rubric_scores=rubric_eval,
                certificate_hash=cert_hash,
                status="certified",
                issued_at=now,
            )

            # Empirically upgrade employee skill level in database
            await self.employee_repo.upgrade_skill_level(
                employee_id=employee_id,
                skill_name=skill_entry.name,
                new_level=target_lvl_enum,
                task_evidence_id=cert_id,
            )

            notes = f"Empirical certification passed with score {overall_score:.2f} ({target_lvl_enum.value}). Certificate {cert_id} anchored."
            logger.info("Skill certification awarded", certificate_id=cert_id, employee_id=employee_id, skill=skill_entry.name)
        else:
            diagnostics = []
            if receipt.timed_out:
                diagnostics.append("Execution timed out in sandbox")
            if receipt.exit_code != 0:
                diagnostics.append(f"Test suite failed with exit code {receipt.exit_code}")
            if parsed_test_data and not parsed_test_data.get("passed"):
                diagnostics.append(f"Test assertions failed ({parsed_test_data.get('passed_tests', 0)}/{parsed_test_data.get('total_tests', 0)} passed)")
            if quality_issues:
                diagnostics.append(f"Code quality: {'; '.join(quality_issues)}")
            if security_issues:
                diagnostics.append(f"Security: {'; '.join(security_issues)}")
            if not passed_rubric:
                diagnostics.append(f"Rubric score {overall_score:.2f} below threshold {skill_entry.grading_rubric.min_pass_score}")

            notes = "Certification rejected: " + (" | ".join(diagnostics) if diagnostics else "Did not meet passing criteria")
            logger.info("Skill certification rejected", employee_id=employee_id, skill=skill_entry.name, reason=notes)

        return CertificationResult(
            passed=passed,
            certificate_id=cert_id,
            certificate_hash=cert_hash,
            skill_name=skill_entry.name,
            target_level=target_lvl_enum.value,
            employee_id=employee_id,
            score=overall_score,
            rubric_evaluation=rubric_eval,
            receipt=receipt.to_dict(),
            notes=notes,
            certificate=cert_record,
        )
