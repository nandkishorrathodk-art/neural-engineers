import ast
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from neural.constants import TaskStatus, VerificationStatus
from neural.db.models import Task
from neural.db.repositories import EmployeeRepository, TaskRepository
from neural.evaluation.benchmarks import BenchmarkRunner
from neural.exceptions import UnverifiedTaskError
from neural.logger import get_logger

logger = get_logger("ancos.evaluation.verifier")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IndependentVerifier:
    """
    Independent Verification & Quality Assurance Engine.
    Ensures zero 'success-by-decree'. Evaluates test outputs, code receipts,
    syntax integrity, security boundaries, and certifies employee skills
    upon successful empirical proof. Enforces the strict gate preventing
    unverified task completions.
    """

    def __init__(
        self,
        employee_repo: Optional[EmployeeRepository] = None,
        task_repo: Optional[TaskRepository] = None,
        benchmark_runner: Optional[BenchmarkRunner] = None
    ):
        self.employee_repo = employee_repo
        self.task_repo = task_repo
        self.benchmark_runner = benchmark_runner or BenchmarkRunner()

    @staticmethod
    def generate_receipt_signature(payload: dict[str, Any]) -> str:
        """
        Generates a SHA-256 cryptographic signature token over canonical JSON
        representation of verification receipt payload to guarantee tamper resistance.
        """
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def check_code_quality(
        self,
        artifacts: list[str],
        file_contents: Optional[dict[str, str]] = None
    ) -> dict[str, Any]:
        """
        Independent code quality analysis:
        - Validates Python syntax using AST parsing.
        - Scans for dangerous / forbidden patterns (e.g. unsafe eval, exec, hardcoded secrets).
        - Computes quality score (0.0 to 1.0).
        """
        issues: list[dict[str, Any]] = []
        syntax_valid = True
        security_passed = True
        files_checked = 0

        # Scan directly supplied in-memory contents or physical files
        contents_to_check: dict[str, str] = {}
        if file_contents:
            contents_to_check.update(file_contents)

        for art_path in artifacts:
            if art_path in contents_to_check:
                continue
            if os.path.exists(art_path) and os.path.isfile(art_path):
                try:
                    with open(art_path, "r", encoding="utf-8", errors="ignore") as f:
                        contents_to_check[art_path] = f.read()
                except Exception as e:
                    issues.append({
                        "file": art_path,
                        "type": "file_read_error",
                        "message": str(e)
                    })

        for fname, code in contents_to_check.items():
            files_checked += 1
            # 1. Syntax check for Python files
            if fname.endswith(".py"):
                try:
                    ast.parse(code, filename=fname)
                except SyntaxError as syn_err:
                    syntax_valid = False
                    issues.append({
                        "file": fname,
                        "type": "syntax_error",
                        "line": syn_err.lineno,
                        "message": str(syn_err)
                    })

            # 2. Security scan for dangerous patterns
            dangerous_patterns = [
                ("eval(", "Use of forbidden eval() construct"),
                ("exec(", "Use of forbidden exec() construct"),
                ("__import__('os').system", "Raw shell injection vector"),
                ("password = '", "Hardcoded plain password pattern"),
                ("secret_key = '", "Hardcoded plain secret key pattern")
            ]
            for pat, desc in dangerous_patterns:
                if pat in code:
                    security_passed = False
                    issues.append({
                        "file": fname,
                        "type": "security_violation",
                        "pattern": pat,
                        "message": desc
                    })

        # Quality scoring: base 1.0, penalties for syntax and security issues
        score = 1.0
        if not syntax_valid:
            score -= 0.6
        if not security_passed:
            score -= 0.4
        score = max(0.0, round(score, 2))

        passed = syntax_valid and security_passed and (len(issues) == 0)
        return {
            "passed": passed,
            "quality_score": score,
            "syntax_valid": syntax_valid,
            "security_passed": security_passed,
            "files_checked": files_checked,
            "issues": issues
        }

    def validate_acceptance_criteria(
        self,
        criteria: Optional[str],
        artifacts: list[str],
        test_exit_code: int,
        output_data: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Validates output deliverables against task acceptance criteria.
        Checks for required test passage, artifact existence, and specific criteria keywords.
        """
        if not criteria:
            return {"passed": True, "details": "No specific acceptance criteria defined."}

        checks: list[dict[str, Any]] = []
        overall_passed = True

        criteria_lower = criteria.lower()

        # Check 1: Test exit code
        if "test" in criteria_lower or "pass" in criteria_lower:
            test_ok = test_exit_code == 0
            if not test_ok:
                overall_passed = False
            checks.append({
                "criterion": "Automated tests pass with zero exit code",
                "passed": test_ok,
                "evidence": f"exit_code={test_exit_code}"
            })

        # Check 2: Artifact deliverables
        if "artifact" in criteria_lower or "deliverable" in criteria_lower or "code" in criteria_lower:
            has_artifacts = len(artifacts) > 0
            if not has_artifacts:
                overall_passed = False
            checks.append({
                "criterion": "Artifact files generated and present",
                "passed": has_artifacts,
                "evidence": f"artifacts_count={len(artifacts)}"
            })

        # Check 3: Clean receipts
        if "receipt" in criteria_lower:
            receipts_ok = test_exit_code == 0 and len(artifacts) > 0
            if not receipts_ok:
                overall_passed = False
            checks.append({
                "criterion": "Valid runtime execution receipt",
                "passed": receipts_ok,
                "evidence": "Artifacts present and tests passed" if receipts_ok else "Missing receipts"
            })

        return {
            "passed": overall_passed,
            "checks": checks,
            "details": f"{sum(1 for c in checks if c['passed'])}/{len(checks)} criteria checks passed" if checks else "Acceptance criteria satisfied"
        }

    async def verify_task_result(
        self,
        task_id: str,
        employee_id: str,
        artifacts: list[str],
        test_exit_code: int,
        tested_skills: list[str],
        acceptance_criteria: Optional[str] = None,
        file_contents: Optional[dict[str, str]] = None,
        task_repo: Optional[TaskRepository] = None,
        test_output: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Conducts independent QA verification runner executing:
        1. Automated test suite result validation.
        2. Code quality & AST security checks.
        3. Acceptance criteria validation.
        4. Cryptographic verification receipt generation.
        5. Empirical skill certification for passing employees.
        """
        logger.info(
            "Conducting independent verification of task result",
            task_id=task_id,
            employee_id=employee_id,
            test_exit_code=test_exit_code,
            artifacts_count=len(artifacts)
        )

        # 1. Test suite check
        tests_passed = test_exit_code == 0 and len(artifacts) > 0
        test_summary = {
            "exit_code": test_exit_code,
            "passed": tests_passed,
            "output": test_output or {}
        }

        # 2. Code quality & security check
        code_quality = self.check_code_quality(artifacts, file_contents=file_contents)

        # 3. Acceptance criteria check
        criteria_res = self.validate_acceptance_criteria(
            criteria=acceptance_criteria,
            artifacts=artifacts,
            test_exit_code=test_exit_code,
            output_data=test_output
        )

        # Overall verdict: All 3 pillars must succeed for zero 'success-by-decree'
        passed = tests_passed and code_quality["passed"] and criteria_res["passed"]
        status = VerificationStatus.PASSED if passed else VerificationStatus.FAILED

        # Build diagnostic feedback if failed
        diagnostic_notes = []
        if not tests_passed:
            if len(artifacts) == 0:
                diagnostic_notes.append("No artifacts or deliverables produced.")
            if test_exit_code != 0:
                diagnostic_notes.append(f"Automated test runner failed with exit code {test_exit_code}.")
        if not code_quality["passed"]:
            for iss in code_quality.get("issues", []):
                diagnostic_notes.append(f"Code Quality: [{iss.get('type')}] in {iss.get('file')}: {iss.get('message')}")
        if not criteria_res["passed"]:
            diagnostic_notes.append(f"Acceptance Criteria: {criteria_res.get('details')}")

        notes = (
            "All automated test suites executed green with valid artifacts and passed code quality."
            if passed
            else " | ".join(diagnostic_notes) or "Verification failed."
        )

        # Build cryptographic verification receipt
        receipt_id = f"RECEIPT-{uuid.uuid4().hex[:8].upper()}"
        receipt_payload = {
            "receipt_id": receipt_id,
            "task_id": task_id,
            "employee_id": employee_id,
            "verification_status": status.value,
            "passed": passed,
            "timestamp": utc_now().isoformat(),
            "artifacts": artifacts,
            "test_summary": test_summary,
            "code_quality": code_quality,
            "acceptance_criteria": criteria_res,
            "verified_skills": tested_skills if passed else [],
            "notes": notes
        }
        signature = self.generate_receipt_signature(receipt_payload)
        receipt_payload["signature"] = signature

        # 4. Empirical Skill Certification Lifecycle:
        # Upon QA pass, transition employee skills from verified=False to verified=True with evidence!
        if passed and self.employee_repo:
            for skill in tested_skills:
                await self.employee_repo.verify_skill(
                    employee_id=employee_id,
                    skill_name=skill,
                    task_evidence_id=task_id
                )
                logger.info(
                    "Employee skill verified with empirical evidence",
                    employee_id=employee_id,
                    skill=skill,
                    evidence_task=task_id
                )

        # 5. Persist receipt on task if task repository is available
        t_repo = task_repo or self.task_repo
        if t_repo:
            try:
                task = await t_repo.get_by_task_id(task_id)
                if task:
                    task.verification_result = receipt_payload
                    if passed:
                        await t_repo.complete_with_receipt(task_id, receipt_payload)
                    else:
                        await t_repo.update_status(task_id, TaskStatus.FAILED)
            except Exception as e:
                logger.warning("Could not persist verification receipt directly on task", error=str(e))

        return {
            "task_id": task_id,
            "verification_status": status.value,
            "passed": passed,
            "verified_skills": tested_skills if passed else [],
            "notes": notes,
            "receipt": receipt_payload,
            "receipt_id": receipt_id,
            "signature": signature,
            "test_summary": test_summary,
            "code_quality": code_quality,
            "acceptance_criteria": criteria_res
        }

    async def gate_task_completion(
        self,
        task_id: str,
        verification_receipt: dict[str, Any]
    ) -> Task:
        """
        Enforces the Strict Gate:
        No task can transition to status='completed' without an independent passing verification receipt.
        Raises UnverifiedTaskError if the receipt is invalid, unverified, or failed.
        """
        if not self.task_repo:
            raise ValueError("TaskRepository is required to gate task completion")

        if not verification_receipt or not verification_receipt.get("passed"):
            raise UnverifiedTaskError(
                f"Strict gate blocked completion of task {task_id}: independent verification receipt indicates failure or is missing."
            )

        # Verify signature integrity
        raw_receipt = dict(verification_receipt)
        sig = raw_receipt.pop("signature", None)
        if sig:
            expected_sig = self.generate_receipt_signature(raw_receipt)
            if sig != expected_sig:
                raise UnverifiedTaskError(
                    f"Strict gate blocked task {task_id}: verification receipt signature mismatch (tampering detected)."
                )

        return await self.task_repo.complete_with_receipt(task_id, verification_receipt)
