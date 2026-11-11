from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EngineeringStage(str, Enum):
    SPECIFICATION = "specification"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    QA_REVIEW = "qa_review"
    SECURITY_REVIEW = "security_review"
    APPROVAL_GATE = "approval_gate"
    MERGE = "merge"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewGateError(RuntimeError):
    """Raised when a code review gate fails or is violated."""


class MergeGateError(RuntimeError):
    """Raised when an approval-gated merge operation fails or is blocked."""


@dataclass(frozen=True)
class BugReport:
    """Structured reproduction feedback from QA to Developer."""

    bug_id: str
    task_id: str
    reporter_id: str
    reproduction_command: str
    failure_summary: str
    failed_tests: list[str]
    error_details: str
    affected_files: list[str]
    suggested_action: str
    timestamp: str = field(default_factory=lambda: utc_now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "bug_id": self.bug_id,
            "task_id": self.task_id,
            "reporter_id": self.reporter_id,
            "reproduction_command": self.reproduction_command,
            "failure_summary": self.failure_summary,
            "failed_tests": self.failed_tests,
            "error_details": self.error_details,
            "affected_files": self.affected_files,
            "suggested_action": self.suggested_action,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BugReport:
        return cls(
            bug_id=data["bug_id"],
            task_id=data["task_id"],
            reporter_id=data["reporter_id"],
            reproduction_command=data["reproduction_command"],
            failure_summary=data["failure_summary"],
            failed_tests=list(data.get("failed_tests", [])),
            error_details=data.get("error_details", ""),
            affected_files=list(data.get("affected_files", [])),
            suggested_action=data.get("suggested_action", ""),
            timestamp=data.get("timestamp", utc_now().isoformat()),
        )


@dataclass(frozen=True)
class QAReviewReceipt:
    """Cryptographic receipt for QA review and test execution."""

    receipt_id: str
    task_id: str
    qa_engineer_id: str
    passed: bool
    test_exit_code: int
    tests_run: int
    tests_passed: int
    tests_failed: int
    diff_stat: str
    artifact_digests: dict[str, str]
    timestamp: str
    signature: str
    bug_report: Optional[BugReport] = None
    head_commit: Optional[str] = None

    @classmethod
    def create(
        cls,
        task_id: str,
        qa_engineer_id: str,
        passed: bool,
        test_exit_code: int,
        tests_run: int,
        tests_passed: int,
        tests_failed: int,
        diff_stat: str,
        artifact_digests: dict[str, str],
        bug_report: Optional[BugReport] = None,
        head_commit: Optional[str] = None,
    ) -> QAReviewReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"QA-RECEIPT-"
            f"{hashlib.sha256(f'{task_id}:{qa_engineer_id}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload: dict[str, Any] = {
            "receipt_id": receipt_id,
            "task_id": task_id,
            "qa_engineer_id": qa_engineer_id,
            "passed": passed,
            "test_exit_code": test_exit_code,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "diff_stat": diff_stat,
            "artifact_digests": artifact_digests,
            "has_bug_report": bug_report is not None,
            "timestamp": ts,
        }
        if head_commit is not None:
            payload["head_commit"] = head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            task_id=task_id,
            qa_engineer_id=qa_engineer_id,
            passed=passed,
            test_exit_code=test_exit_code,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            diff_stat=diff_stat,
            artifact_digests=artifact_digests,
            timestamp=ts,
            signature=signature,
            bug_report=bug_report,
            head_commit=head_commit.lower() if head_commit else None,
        )

    def verify_signature(self) -> bool:
        payload: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "qa_engineer_id": self.qa_engineer_id,
            "passed": self.passed,
            "test_exit_code": self.test_exit_code,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "diff_stat": self.diff_stat,
            "artifact_digests": self.artifact_digests,
            "has_bug_report": self.bug_report is not None,
            "timestamp": self.timestamp,
        }
        if self.head_commit is not None:
            payload["head_commit"] = self.head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "qa_engineer_id": self.qa_engineer_id,
            "passed": self.passed,
            "test_exit_code": self.test_exit_code,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "diff_stat": self.diff_stat,
            "artifact_digests": self.artifact_digests,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "bug_report": self.bug_report.to_dict() if self.bug_report else None,
        }
        if self.head_commit is not None:
            res["head_commit"] = self.head_commit
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QAReviewReceipt:
        bug_data = data.get("bug_report")
        bug_report = BugReport.from_dict(bug_data) if bug_data else None
        return cls(
            receipt_id=data["receipt_id"],
            task_id=data["task_id"],
            qa_engineer_id=data["qa_engineer_id"],
            passed=data["passed"],
            test_exit_code=data["test_exit_code"],
            tests_run=data["tests_run"],
            tests_passed=data["tests_passed"],
            tests_failed=data["tests_failed"],
            diff_stat=data["diff_stat"],
            artifact_digests=dict(data.get("artifact_digests", {})),
            timestamp=data["timestamp"],
            signature=data["signature"],
            bug_report=bug_report,
            head_commit=data.get("head_commit"),
        )


@dataclass(frozen=True)
class SecurityReviewReceipt:
    """Cryptographic receipt for security vulnerability and AST scanning."""

    receipt_id: str
    task_id: str
    security_engineer_id: str
    passed: bool
    vulnerabilities_found: list[dict[str, Any]]
    scanned_files_count: int
    security_score: float
    timestamp: str
    signature: str
    head_commit: Optional[str] = None

    @classmethod
    def create(
        cls,
        task_id: str,
        security_engineer_id: str,
        passed: bool,
        vulnerabilities_found: list[dict[str, Any]],
        scanned_files_count: int,
        security_score: float,
        head_commit: Optional[str] = None,
    ) -> SecurityReviewReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"SEC-RECEIPT-"
            f"{hashlib.sha256(f'{task_id}:{security_engineer_id}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload: dict[str, Any] = {
            "receipt_id": receipt_id,
            "task_id": task_id,
            "security_engineer_id": security_engineer_id,
            "passed": passed,
            "vulnerabilities_found": vulnerabilities_found,
            "scanned_files_count": scanned_files_count,
            "security_score": round(security_score, 2),
            "timestamp": ts,
        }
        if head_commit is not None:
            payload["head_commit"] = head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            task_id=task_id,
            security_engineer_id=security_engineer_id,
            passed=passed,
            vulnerabilities_found=vulnerabilities_found,
            scanned_files_count=scanned_files_count,
            security_score=round(security_score, 2),
            timestamp=ts,
            signature=signature,
            head_commit=head_commit.lower() if head_commit else None,
        )

    def verify_signature(self) -> bool:
        payload: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "security_engineer_id": self.security_engineer_id,
            "passed": self.passed,
            "vulnerabilities_found": self.vulnerabilities_found,
            "scanned_files_count": self.scanned_files_count,
            "security_score": round(self.security_score, 2),
            "timestamp": self.timestamp,
        }
        if self.head_commit is not None:
            payload["head_commit"] = self.head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "security_engineer_id": self.security_engineer_id,
            "passed": self.passed,
            "vulnerabilities_found": self.vulnerabilities_found,
            "scanned_files_count": self.scanned_files_count,
            "security_score": self.security_score,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }
        if self.head_commit is not None:
            res["head_commit"] = self.head_commit
        return res

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityReviewReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            task_id=data["task_id"],
            security_engineer_id=data["security_engineer_id"],
            passed=data["passed"],
            vulnerabilities_found=list(data.get("vulnerabilities_found", [])),
            scanned_files_count=data["scanned_files_count"],
            security_score=float(data["security_score"]),
            timestamp=data["timestamp"],
            signature=data["signature"],
            head_commit=data.get("head_commit"),
        )


@dataclass(frozen=True)
class MergeReceipt:
    """Cryptographic provenance receipt for an approval-gated code merge."""

    receipt_id: str
    task_id: str
    source_branch: str
    target_branch: str
    base_commit: str
    merged_commit: str
    approved_by: str
    approval_id: str
    qa_receipt_id: str
    security_receipt_id: str
    timestamp: str
    signature: str

    @classmethod
    def create(
        cls,
        task_id: str,
        source_branch: str,
        target_branch: str,
        base_commit: str,
        merged_commit: str,
        approved_by: str,
        approval_id: str,
        qa_receipt_id: str,
        security_receipt_id: str,
    ) -> MergeReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"MERGE-RECEIPT-"
            f"{hashlib.sha256(f'{task_id}:{merged_commit}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload = {
            "receipt_id": receipt_id,
            "task_id": task_id,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "base_commit": base_commit.lower(),
            "merged_commit": merged_commit.lower(),
            "approved_by": approved_by,
            "approval_id": approval_id,
            "qa_receipt_id": qa_receipt_id,
            "security_receipt_id": security_receipt_id,
            "timestamp": ts,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            task_id=task_id,
            source_branch=source_branch,
            target_branch=target_branch,
            base_commit=base_commit.lower(),
            merged_commit=merged_commit.lower(),
            approved_by=approved_by,
            approval_id=approval_id,
            qa_receipt_id=qa_receipt_id,
            security_receipt_id=security_receipt_id,
            timestamp=ts,
            signature=signature,
        )

    def verify_signature(self) -> bool:
        payload = {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "base_commit": self.base_commit.lower(),
            "merged_commit": self.merged_commit.lower(),
            "approved_by": self.approved_by,
            "approval_id": self.approval_id,
            "qa_receipt_id": self.qa_receipt_id,
            "security_receipt_id": self.security_receipt_id,
            "timestamp": self.timestamp,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "task_id": self.task_id,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "base_commit": self.base_commit,
            "merged_commit": self.merged_commit,
            "approved_by": self.approved_by,
            "approval_id": self.approval_id,
            "qa_receipt_id": self.qa_receipt_id,
            "security_receipt_id": self.security_receipt_id,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MergeReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            task_id=data["task_id"],
            source_branch=data["source_branch"],
            target_branch=data["target_branch"],
            base_commit=data["base_commit"],
            merged_commit=data["merged_commit"],
            approved_by=data["approved_by"],
            approval_id=data["approval_id"],
            qa_receipt_id=data["qa_receipt_id"],
            security_receipt_id=data["security_receipt_id"],
            timestamp=data["timestamp"],
            signature=data["signature"],
        )

