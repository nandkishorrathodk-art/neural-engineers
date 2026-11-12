from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


PROHIBITED_SELF_EVOLUTION_PATHS: frozenset[str] = frozenset({
    "services/core/neural/evolution",
    "services/core/neural/governance",
    "neural/evolution",
    "neural/governance",
    "services/core/neural/security/approval_gates.py",
    "services/core/neural/security/kill_switch.py",
    "services/core/neural/security/network.py",
    "services/core/neural/security/profiles.py",
    "services/core/neural/security/resources.py",
    "services/core/neural/security/__init__.py",
    "services/core/neural/observability/audit.py",
    "services/core/neural/containers/broker_api.py",
    "services/core/neural/gui_intelligence/safety/gui_action_policy.py",
    "neural/security/approval_gates.py",
    "neural/security/kill_switch.py",
    "neural/security/network.py",
    "neural/security/profiles.py",
    "neural/security/resources.py",
    "neural/security/__init__.py",
    "neural/observability/audit.py",
    "neural/containers/broker_api.py",
    "neural/gui_intelligence/safety/gui_action_policy.py",
    "approval_gates.py",
    "kill_switch.py",
    "broker_api.py",
    "gui_action_policy.py",
})

_PROTECTED_SUBSTRINGS: tuple[str, ...] = (
    "approval_gate",
    "kill_switch",
    "restricted_broker",
    "broker_api",
    "gui_action_policy",
    "neural/security",
    "neural\\security",
    "audit.py",
    "neural/evolution",
    "neural\\evolution",
    "neural/governance",
    "neural\\governance",
)


def is_protected_path(path: str | Path) -> bool:
    """
    Evaluates whether a target file path falls under protected core invariants.
    Self-evolution code changes are strictly prohibited from touching root policy engines,
    human approval gates, verifiers, security scanners, or emergency halt latches.
    """
    raw_str = str(path).strip()
    if not raw_str:
        return False

    norm_str = raw_str.replace("\\", "/").lower()

    # Prevent traversal attempts
    parts = [p for p in norm_str.split("/") if p and p != "."]
    if ".." in parts:
        return True

    filename = parts[-1] if parts else ""
    if filename in {"approval_gates.py", "kill_switch.py", "broker_api.py", "gui_action_policy.py", "audit.py"}:
        return True

    # Exact, prefix, directory or suffix match in prohibited paths
    for p in PROHIBITED_SELF_EVOLUTION_PATHS:
        p_norm = p.lower().rstrip("/")
        if (
            norm_str == p_norm
            or norm_str.startswith(p_norm + "/")
            or f"/{p_norm}/" in f"/{norm_str}/"
            or norm_str.endswith("/" + p_norm)
            or norm_str.endswith(p_norm)
        ):
            return True

    # Check protected keywords & policy directories
    for sub in _PROTECTED_SUBSTRINGS:
        if sub in norm_str:
            return True

    return False


class ProtectedPathViolationError(RuntimeError):
    """Raised when self-evolution attempts to modify or target a protected core path."""


class SelfEvolutionVerificationError(RuntimeError):
    """Raised when self-evolution multi-gate verification fails."""


class PromotionGateError(RuntimeError):
    """Raised when an approval-gated staged promotion fails or lacks authorization."""


class SelfEvolutionRollbackError(RuntimeError):
    """Raised when automated rollback fails or encounters an error."""


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    STAGED = "staged"
    TESTING = "testing"
    VERIFIED = "verified"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SelfEvolutionProposal:
    """Structured evidence-based improvement proposal."""

    proposal_id: str
    title: str
    description: str
    category: str  # optimization, bug_fix, performance, reliability, capability
    target_files: list[str]
    root_cause_rationale: str
    baseline_metrics: dict[str, Any]
    target_goals: dict[str, Any]
    status: ProposalStatus = ProposalStatus.DRAFT
    proposer_id: str = "SelfEvolutionEngine"
    created_at: str = field(default_factory=lambda: utc_now().isoformat())
    signature: str = ""

    def __post_init__(self) -> None:
        # Validate protected core files invariant
        for f in self.target_files:
            if is_protected_path(f):
                raise ProtectedPathViolationError(
                    f"Proposal {self.proposal_id} targets protected core file '{f}'. "
                    f"Self-evolution modification of root policy, approval gates, or emergency latches is strictly prohibited."
                )
        if not self.signature:
            sig = self.compute_signature()
            object.__setattr__(self, "signature", sig)

    def compute_signature(self) -> str:
        payload = {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "target_files": sorted(self.target_files),
            "root_cause_rationale": self.root_cause_rationale,
            "baseline_metrics": self.baseline_metrics,
            "target_goals": self.target_goals,
            "proposer_id": self.proposer_id,
            "created_at": self.created_at,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def verify_signature(self) -> bool:
        return self.signature == self.compute_signature()

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "target_files": self.target_files,
            "root_cause_rationale": self.root_cause_rationale,
            "baseline_metrics": self.baseline_metrics,
            "target_goals": self.target_goals,
            "status": self.status.value,
            "proposer_id": self.proposer_id,
            "created_at": self.created_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfEvolutionProposal:
        return cls(
            proposal_id=data["proposal_id"],
            title=data["title"],
            description=data["description"],
            category=data["category"],
            target_files=list(data.get("target_files", [])),
            root_cause_rationale=data["root_cause_rationale"],
            baseline_metrics=dict(data.get("baseline_metrics", {})),
            target_goals=dict(data.get("target_goals", {})),
            status=ProposalStatus(data.get("status", ProposalStatus.DRAFT.value)),
            proposer_id=data.get("proposer_id", "SelfEvolutionEngine"),
            created_at=data.get("created_at", utc_now().isoformat()),
            signature=data.get("signature", ""),
        )


@dataclass(frozen=True)
class SelfEvolutionVerificationReceipt:
    """Cryptographic multi-gate verification receipt signed by IndependentDeliverableVerifier."""

    receipt_id: str
    proposal_id: str
    candidate_branch: str
    verifier_id: str
    passed: bool
    regression_passed: bool
    static_analysis_passed: bool
    ast_security_passed: bool
    benchmark_passed: bool
    benchmark_comparison: dict[str, Any]
    artifact_digests: dict[str, str]
    diff_stat: str
    timestamp: str
    signature: str
    head_commit: Optional[str] = None
    notes: str = ""

    @classmethod
    def create(
        cls,
        proposal_id: str,
        candidate_branch: str,
        passed: bool,
        regression_passed: bool,
        static_analysis_passed: bool,
        ast_security_passed: bool,
        benchmark_passed: bool,
        benchmark_comparison: dict[str, Any],
        artifact_digests: dict[str, str],
        diff_stat: str,
        head_commit: Optional[str] = None,
        notes: str = "",
        verifier_id: str = "IndependentDeliverableVerifier",
    ) -> SelfEvolutionVerificationReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"EVO-VERIF-"
            f"{hashlib.sha256(f'{proposal_id}:{candidate_branch}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload: dict[str, Any] = {
            "receipt_id": receipt_id,
            "proposal_id": proposal_id,
            "candidate_branch": candidate_branch,
            "verifier_id": verifier_id,
            "passed": passed,
            "regression_passed": regression_passed,
            "static_analysis_passed": static_analysis_passed,
            "ast_security_passed": ast_security_passed,
            "benchmark_passed": benchmark_passed,
            "benchmark_comparison": benchmark_comparison,
            "artifact_digests": artifact_digests,
            "diff_stat": diff_stat,
            "timestamp": ts,
        }
        if head_commit:
            payload["head_commit"] = head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
            candidate_branch=candidate_branch,
            verifier_id=verifier_id,
            passed=passed,
            regression_passed=regression_passed,
            static_analysis_passed=static_analysis_passed,
            ast_security_passed=ast_security_passed,
            benchmark_passed=benchmark_passed,
            benchmark_comparison=benchmark_comparison,
            artifact_digests=artifact_digests,
            diff_stat=diff_stat,
            timestamp=ts,
            signature=signature,
            head_commit=head_commit.lower() if head_commit else None,
            notes=notes,
        )

    def verify_signature(self) -> bool:
        payload: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "candidate_branch": self.candidate_branch,
            "verifier_id": self.verifier_id,
            "passed": self.passed,
            "regression_passed": self.regression_passed,
            "static_analysis_passed": self.static_analysis_passed,
            "ast_security_passed": self.ast_security_passed,
            "benchmark_passed": self.benchmark_passed,
            "benchmark_comparison": self.benchmark_comparison,
            "artifact_digests": self.artifact_digests,
            "diff_stat": self.diff_stat,
            "timestamp": self.timestamp,
        }
        if self.head_commit:
            payload["head_commit"] = self.head_commit.lower()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "candidate_branch": self.candidate_branch,
            "verifier_id": self.verifier_id,
            "passed": self.passed,
            "regression_passed": self.regression_passed,
            "static_analysis_passed": self.static_analysis_passed,
            "ast_security_passed": self.ast_security_passed,
            "benchmark_passed": self.benchmark_passed,
            "benchmark_comparison": self.benchmark_comparison,
            "artifact_digests": self.artifact_digests,
            "diff_stat": self.diff_stat,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "head_commit": self.head_commit,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfEvolutionVerificationReceipt:
        return cls(
            receipt_id=data["receipt_id"],
            proposal_id=data["proposal_id"],
            candidate_branch=data["candidate_branch"],
            verifier_id=data.get("verifier_id", "IndependentDeliverableVerifier"),
            passed=data["passed"],
            regression_passed=data["regression_passed"],
            static_analysis_passed=data["static_analysis_passed"],
            ast_security_passed=data["ast_security_passed"],
            benchmark_passed=data["benchmark_passed"],
            benchmark_comparison=dict(data.get("benchmark_comparison", {})),
            artifact_digests=dict(data.get("artifact_digests", {})),
            diff_stat=data.get("diff_stat", ""),
            timestamp=data["timestamp"],
            signature=data["signature"],
            head_commit=data.get("head_commit"),
            notes=data.get("notes", ""),
        )


@dataclass(frozen=True)
class SelfEvolutionPromotionReceipt:
    """Cryptographic provenance receipt for an approval-gated self-evolution promotion into production."""

    receipt_id: str
    proposal_id: str
    candidate_branch: str
    target_branch: str
    pre_evolution_commit: str
    promoted_commit: str
    approval_id: str
    approved_by: str
    verification_receipt_id: str
    timestamp: str
    signature: str

    @classmethod
    def create(
        cls,
        proposal_id: str,
        candidate_branch: str,
        target_branch: str,
        pre_evolution_commit: str,
        promoted_commit: str,
        approval_id: str,
        approved_by: str,
        verification_receipt_id: str,
    ) -> SelfEvolutionPromotionReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"EVO-PROMOTE-"
            f"{hashlib.sha256(f'{proposal_id}:{promoted_commit}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload = {
            "receipt_id": receipt_id,
            "proposal_id": proposal_id,
            "candidate_branch": candidate_branch,
            "target_branch": target_branch,
            "pre_evolution_commit": pre_evolution_commit.lower(),
            "promoted_commit": promoted_commit.lower(),
            "approval_id": approval_id,
            "approved_by": approved_by,
            "verification_receipt_id": verification_receipt_id,
            "timestamp": ts,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
            candidate_branch=candidate_branch,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit.lower(),
            promoted_commit=promoted_commit.lower(),
            approval_id=approval_id,
            approved_by=approved_by,
            verification_receipt_id=verification_receipt_id,
            timestamp=ts,
            signature=signature,
        )

    def verify_signature(self) -> bool:
        payload = {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "candidate_branch": self.candidate_branch,
            "target_branch": self.target_branch,
            "pre_evolution_commit": self.pre_evolution_commit.lower(),
            "promoted_commit": self.promoted_commit.lower(),
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "verification_receipt_id": self.verification_receipt_id,
            "timestamp": self.timestamp,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "candidate_branch": self.candidate_branch,
            "target_branch": self.target_branch,
            "pre_evolution_commit": self.pre_evolution_commit,
            "promoted_commit": self.promoted_commit,
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "verification_receipt_id": self.verification_receipt_id,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class SelfEvolutionRollbackReceipt:
    """Cryptographic receipt for an automated rollback restoring pinned pre-evolution commit state."""

    receipt_id: str
    proposal_id: str
    target_branch: str
    pre_evolution_commit: str
    rolled_back_commit: str
    restored_commit: str
    anomaly_detected: dict[str, Any]
    timestamp: str
    signature: str

    @classmethod
    def create(
        cls,
        proposal_id: str,
        target_branch: str,
        pre_evolution_commit: str,
        rolled_back_commit: str,
        restored_commit: str,
        anomaly_detected: dict[str, Any],
    ) -> SelfEvolutionRollbackReceipt:
        ts = utc_now().isoformat()
        receipt_id = (
            f"EVO-ROLLBACK-"
            f"{hashlib.sha256(f'{proposal_id}:{pre_evolution_commit}:{ts}'.encode('utf-8')).hexdigest()[:12].upper()}"
        )
        payload = {
            "receipt_id": receipt_id,
            "proposal_id": proposal_id,
            "target_branch": target_branch,
            "pre_evolution_commit": pre_evolution_commit.lower(),
            "rolled_back_commit": rolled_back_commit.lower(),
            "restored_commit": restored_commit.lower(),
            "anomaly_detected": anomaly_detected,
            "timestamp": ts,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hashlib.sha256(canonical).hexdigest()
        return cls(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit.lower(),
            rolled_back_commit=rolled_back_commit.lower(),
            restored_commit=restored_commit.lower(),
            anomaly_detected=anomaly_detected,
            timestamp=ts,
            signature=signature,
        )

    def verify_signature(self) -> bool:
        payload = {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "target_branch": self.target_branch,
            "pre_evolution_commit": self.pre_evolution_commit.lower(),
            "rolled_back_commit": self.rolled_back_commit.lower(),
            "restored_commit": self.restored_commit.lower(),
            "anomaly_detected": self.anomaly_detected,
            "timestamp": self.timestamp,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        return self.signature == expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "target_branch": self.target_branch,
            "pre_evolution_commit": self.pre_evolution_commit,
            "rolled_back_commit": self.rolled_back_commit,
            "restored_commit": self.restored_commit,
            "anomaly_detected": self.anomaly_detected,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }
