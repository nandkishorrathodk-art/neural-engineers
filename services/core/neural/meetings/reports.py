import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from neural.exceptions import FabricatedProgressError
from neural.logger import get_logger

logger = get_logger("ancos.meetings.reports")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class StructuredProgressClaim(BaseModel):
    """
    Structured Progress Claim presented during a meeting.
    Must be validated against independent test runs, repository commit, and artifact digests.
    """
    task_id: str = Field(..., min_length=1, description="Unique task identifier in TaskRepository")
    claim: str = Field(..., min_length=3, description="Descriptive progress accomplishment claim")
    commit_sha: str = Field(..., min_length=7, description="Repository commit SHA where changes reside")
    artifact_hash: str = Field(..., min_length=64, description="SHA-256 digest of deliverable artifact")
    test_run_id: str = Field(..., min_length=1, description="Independent test run / verification receipt ID")
    verification_status: str = Field(..., description="Must be 'PASSED'")
    verified_at: str = Field(..., min_length=1, description="ISO timestamp when verification passed")

    @field_validator("verification_status")
    @classmethod
    def validate_verification_status(cls, v: str) -> str:
        clean = str(v).strip().upper()
        if clean != "PASSED":
            raise FabricatedProgressError(
                f"Claim verification_status must be 'PASSED' (received: '{v}'). "
                "Unverified claims or failed tests cannot be reported as progress."
            )
        return clean


class FactGroundedProgressReport(BaseModel):
    """
    Fact-Grounded Progress Report for an Employee.
    Enforces empirical receipts over fabricated status assertions.
    """
    employee_id: str
    task_id: str
    what_was_done: str = Field(..., min_length=3, description="Concrete accomplishments during the period")
    what_remains: str = Field(..., min_length=3, description="Remaining deliverables or next immediate steps")
    what_is_blocking: str | None = Field(None, description="Explicit blocker description, or None if unblocked")
    verification_receipt: dict[str, Any] = Field(
        ...,
        description="Factual verification receipt: exit codes, git commits, test outputs, or execution logs"
    )
    factual_status: str = Field(
        default="in_progress",
        description="'completed', 'in_progress', 'blocked', or 'failed'"
    )
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())

    @field_validator("verification_receipt")
    @classmethod
    def validate_receipt_substance(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v or not isinstance(v, dict):
            raise FabricatedProgressError(
                "Missing verification receipt. Reports must include empirical evidence (exit_code, git_commits, or logs)."
            )
        has_evidence = False
        if v.get("exit_code") is not None:
            has_evidence = True
        elif v.get("git_commits") or v.get("commit_hash"):
            has_evidence = True
        elif v.get("test_results"):
            has_evidence = True
        elif v.get("execution_logs"):
            has_evidence = True
        elif v.get("artifacts"):
            has_evidence = True

        if not has_evidence:
            raise FabricatedProgressError(
                "Verification receipt must contain at least one verifiable proof: "
                "exit_code, git_commits, test_results, execution_logs, or artifacts."
            )
        return v


def validate_report_data(data: dict[str, Any]) -> None:
    """
    Validates raw progress report dictionary against anti-fabrication rules.
    Rejects fabricated progress percentages, ungrounded completion claims, and empty receipts.
    """
    # 0. Validate structured progress claim if present
    claim_payload = data.get("progress_claim") or (
        data.get("claim") if isinstance(data.get("claim"), dict) else None
    )
    if claim_payload:
        StructuredProgressClaim(**claim_payload)
    # 1. Check verification receipt substance
    receipt = data.get("verification_receipt")
    if not receipt or not isinstance(receipt, dict):
        raise FabricatedProgressError("Missing mandatory answer: 'What is the verification receipt?'")

    has_evidence = False
    if receipt.get("exit_code") is not None:
        has_evidence = True
    elif receipt.get("git_commits") or receipt.get("commit_hash"):
        has_evidence = True
    elif receipt.get("test_results"):
        has_evidence = True
    elif receipt.get("execution_logs"):
        has_evidence = True
    elif receipt.get("artifacts"):
        has_evidence = True

    if not has_evidence:
        raise FabricatedProgressError(
            "Verification receipt must contain at least one verifiable proof: "
            "exit_code, git_commits, test_results, execution_logs, or artifacts."
        )

    # 2. Reject fabricated percentage metrics
    for forbidden_key in ("progress_percentage", "percentage", "progress_pct", "percent_complete", "pct_done"):
        if forbidden_key in data and data[forbidden_key] is not None:
            val_str = str(data[forbidden_key]).rstrip("%").strip()
            try:
                val_float = float(val_str)
            except (ValueError, TypeError):
                raise FabricatedProgressError(
                    f"Fabricated progress percentage '{data[forbidden_key]}' rejected. "
                    "ANCOS strictly prohibits non-empirical percentage assertions."
                )

            # Allow only if mathematically backed by discrete subtask counts with receipts
            subtasks_total = receipt.get("subtasks_total")
            subtasks_done = receipt.get("subtasks_done")
            try:
                st_total = int(subtasks_total) if subtasks_total is not None else None
                st_done = int(subtasks_done) if subtasks_done is not None else None
            except (ValueError, TypeError):
                st_total, st_done = None, None

            if st_total and st_done is not None and st_total > 0 and 0 <= st_done <= st_total:
                expected_pct = (st_done / st_total) * 100
                if abs(val_float - expected_pct) > 1.0:
                    raise FabricatedProgressError(
                        f"Fabricated percentage detected: claimed {data[forbidden_key]}% but receipts show {st_done}/{st_total} ({expected_pct:.1f}%)."
                    )
            else:
                raise FabricatedProgressError(
                    f"Fabricated progress percentage '{data[forbidden_key]}' rejected. "
                    "ANCOS strictly prohibits fabricated progress percentages. "
                    "Reports must be grounded in real execution logs, git commits, and exit codes."
                )

    # 3. Check for vague/fabricated percentage text in descriptions or status
    text_content = (
        f"{data.get('what_was_done', '')} {data.get('what_remains', '')} "
        f"{data.get('what_is_blocking', '')} {data.get('summary', '')} {data.get('description', '')}"
    )
    pattern = (
        r"(?:(?:done|completed?|finished|progress(?:ed)?)\s*(?:with|of|about)?\s*(\d{1,3}(?:\.\d+)?)%|"
        r"(\d{1,3}(?:\.\d+)?)%\s*(?:done|completed?|finished|progress(?:ed)?|through))"
    )
    raw_matches = re.findall(pattern, text_content, re.IGNORECASE)
    percentage_matches = [m[0] or m[1] for m in raw_matches if (m[0] or m[1])]

    if percentage_matches:
        subtasks_total = receipt.get("subtasks_total")
        subtasks_done = receipt.get("subtasks_done")
        try:
            st_total = int(subtasks_total) if subtasks_total is not None else None
            st_done = int(subtasks_done) if subtasks_done is not None else None
        except (ValueError, TypeError):
            st_total, st_done = None, None

        if not (st_total and st_done is not None and st_total > 0 and 0 <= st_done <= st_total):
            raise FabricatedProgressError(
                f"Fabricated progress percentage '{percentage_matches[0]}%' found in text. "
                "ANCOS mandates empirical proof rather than speculative percentage estimates."
            )
        expected_pct = (st_done / st_total) * 100
        claimed_pct = float(percentage_matches[0])
        if abs(claimed_pct - expected_pct) > 1.0:
            raise FabricatedProgressError(
                f"Fabricated progress percentage in text '{percentage_matches[0]}%' does not match receipts: "
                f"{st_done}/{st_total} subtasks ({expected_pct:.1f}%)."
            )

    # 4. Check 4 mandatory questions
    if not data.get("what_was_done") or len(str(data.get("what_was_done")).strip()) < 3:
        raise FabricatedProgressError("Missing mandatory answer: 'What was done?'")

    if not data.get("what_remains") or len(str(data.get("what_remains")).strip()) < 3:
        raise FabricatedProgressError("Missing mandatory answer: 'What remains?'")

    # 5. Consistency check: Can't claim completed if exit_code is non-zero
    exit_code = receipt.get("exit_code")
    factual_status = data.get("factual_status", "in_progress")
    if factual_status == "completed" and exit_code is not None and exit_code != 0:
        raise FabricatedProgressError(
            f"Cannot report task as 'completed' when verification receipt shows exit code {exit_code}."
        )


class ProgressReportCompiler:
    """
    Compiles factual, receipt-grounded progress reports from live container execution telemetry.
    """

    @classmethod
    def compile(
        cls,
        employee_id: str,
        task_id: str,
        what_was_done: str,
        what_remains: str,
        what_is_blocking: str | None = None,
        execution_logs: list[str] | str | None = None,
        git_commits: list[str] | None = None,
        exit_code: int | None = None,
        test_results: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        verification_receipt: dict[str, Any] | None = None,
        factual_status: str | None = None,
        raw_payload: dict[str, Any] | None = None
    ) -> FactGroundedProgressReport:
        """
        Validates, compiles, and returns a verified FactGroundedProgressReport.
        """
        # Build composite verification receipt
        receipt: dict[str, Any] = dict(verification_receipt or {})

        if exit_code is not None:
            receipt["exit_code"] = exit_code
        if git_commits is not None:
            receipt["git_commits"] = git_commits
        if test_results is not None:
            receipt["test_results"] = test_results
        if artifacts is not None:
            receipt["artifacts"] = artifacts
        if execution_logs is not None:
            if isinstance(execution_logs, str):
                receipt["execution_logs"] = [execution_logs]
            else:
                receipt["execution_logs"] = execution_logs

        # Determine factual status from receipts and blocker
        if factual_status:
            status = factual_status
        elif what_is_blocking and what_is_blocking.strip().lower() not in ("none", "n/a", "no", "false", ""):
            status = "blocked"
        elif exit_code == 0 and (test_results is None or test_results.get("passed", True)):
            status = "completed"
        elif exit_code is not None and exit_code != 0:
            status = "failed"
        else:
            status = "in_progress"

        report_dict = {
            "employee_id": employee_id,
            "task_id": task_id,
            "what_was_done": what_was_done,
            "what_remains": what_remains,
            "what_is_blocking": what_is_blocking if what_is_blocking and what_is_blocking.strip().lower() not in ("none", "n/a") else None,
            "verification_receipt": receipt,
            "factual_status": status
        }

        # If raw_payload was passed (e.g. from user input), validate for fabricated percentages
        if raw_payload:
            merged = {**raw_payload, **report_dict}
            validate_report_data(merged)
        else:
            validate_report_data(report_dict)

        report = FactGroundedProgressReport(**report_dict)
        logger.info(
            "Compiled fact-grounded progress report",
            employee_id=employee_id,
            task_id=task_id,
            status=report.factual_status
        )
        return report
