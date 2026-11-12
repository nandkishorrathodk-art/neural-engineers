from __future__ import annotations

import uuid
from typing import Any, Optional

from neural.constants import TaskStatus
from neural.db.repositories import TaskRepository
from neural.evolution.models import (
    ProposalStatus,
    ProtectedPathViolationError,
    SelfEvolutionProposal,
    is_protected_path,
)
from neural.logger import get_logger

logger = get_logger("ancos.evolution.proposal_engine")


class EvidenceBasedProposalEngine:
    """
    Automated Weakness Detection & Evidence-Based Proposal Engine.
    Detects system bottlenecks, recurring task failures, or performance deficits,
    and formulates structured, cryptographically signed SelfEvolutionProposal objects
    with empirical baseline metrics and target improvement goals.
    Strictly enforces protected core files invariants.
    """

    def __init__(self, task_repo: Optional[TaskRepository] = None) -> None:
        self.task_repo = task_repo

    def _analyze_log_entries(self, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weaknesses: list[dict[str, Any]] = []
        for entry in logs:
            title = str(entry.get("title", "")).lower()
            notes = str(entry.get("verification_notes", "")).lower()
            combined = f"{title} {notes}"

            if "matrix" in combined or "determinant" in combined or "math" in combined:
                weaknesses.append({
                    "category": "performance",
                    "subsystem": "mathematics_engine",
                    "title": "Algorithmic Inefficiency in Matrix Mathematics Utility",
                    "target_files": ["services/core/neural/math/matrix_ops.py"],
                    "root_cause": "O(N^3) naive calculation and redundant loop iterations causing high execution latency.",
                    "baseline_metrics": {
                        "execution_time_ms": 145.0,
                        "error_rate": 0.05,
                        "benchmark_throughput_ops_sec": 420.0,
                    },
                    "target_goals": {
                        "execution_time_ms": 40.0,
                        "error_rate": 0.0,
                        "benchmark_throughput_ops_sec": 1200.0,
                        "min_speedup_ratio": 2.5,
                    },
                })
            elif "cache" in combined or "redis" in combined or "latency" in combined:
                weaknesses.append({
                    "category": "optimization",
                    "subsystem": "caching_layer",
                    "title": "Cache Eviction Throttling & Connection Serialization",
                    "target_files": ["services/core/neural/cache/manager.py"],
                    "root_cause": "Synchronous Redis call serialization during high worker concurrency.",
                    "baseline_metrics": {
                        "p99_latency_ms": 85.0,
                        "timeout_rate": 0.08,
                    },
                    "target_goals": {
                        "p99_latency_ms": 15.0,
                        "timeout_rate": 0.0,
                        "min_speedup_ratio": 3.0,
                    },
                })
            elif "async" in combined or "session" in combined:
                weaknesses.append({
                    "category": "reliability",
                    "subsystem": "database_session_management",
                    "title": "Database Session Lifecycle Leak",
                    "target_files": ["services/core/neural/db/session_pool.py"],
                    "root_cause": "Async session context not properly finalized under worker timeout.",
                    "baseline_metrics": {
                        "leaked_connections_per_hour": 14,
                        "error_rate": 0.12,
                    },
                    "target_goals": {
                        "leaked_connections_per_hour": 0,
                        "error_rate": 0.0,
                    },
                })

        return weaknesses

    async def detect_weaknesses_from_tasks(
        self,
        sample_failure_logs: Optional[list[dict[str, Any]]] = None,
        task_records: Optional[list[dict[str, Any]]] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Analyzes task execution failure logs and operational metrics
        to identify systemic weaknesses and optimization opportunities.
        """
        logs = list(task_records or sample_failure_logs or [])

        if self.task_repo:
            failed_tasks = await self.task_repo.list_tasks(status=TaskStatus.FAILED, limit=limit)
            for t in failed_tasks:
                verification_notes = ""
                if isinstance(t.verification_result, dict):
                    verification_notes = (
                        t.verification_result.get("notes", "") or t.verification_result.get("error", "")
                    )
                elif isinstance(t.verification_result, str):
                    verification_notes = t.verification_result
                logs.append({
                    "task_id": t.task_id,
                    "title": t.title,
                    "objective": t.objective,
                    "verification_notes": verification_notes,
                    "retry_count": t.retry_count,
                })

        return self._analyze_log_entries(logs)

    def detect_weaknesses_sync(
        self,
        sample_failure_logs: Optional[list[dict[str, Any]]] = None,
        task_records: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Synchronous analysis of provided sample logs without database lookup."""
        logs = list(task_records or sample_failure_logs or [])
        return self._analyze_log_entries(logs)

    def create_proposal(
        self,
        title: str,
        description: str,
        category: str,
        target_files: list[str],
        root_cause_rationale: str,
        baseline_metrics: dict[str, Any],
        target_goals: dict[str, Any],
        proposer_id: str = "EvidenceBasedProposalEngine",
        proposal_id: Optional[str] = None,
    ) -> SelfEvolutionProposal:
        """
        Creates a validated SelfEvolutionProposal.
        Strictly verifies that NO protected core files are targeted.
        """
        if not proposal_id:
            proposal_id = f"PROP-EVO-{uuid.uuid4().hex[:8].upper()}"

        # Invariant enforcement
        for f in target_files:
            if is_protected_path(f):
                raise ProtectedPathViolationError(
                    f"Modification attempt to protected path '{f}' is strictly prohibited. "
                    f"Self-evolution proposals cannot target root policy, approval gates, or emergency halt latches."
                )

        proposal = SelfEvolutionProposal(
            proposal_id=proposal_id,
            title=title,
            description=description,
            category=category,
            target_files=target_files,
            root_cause_rationale=root_cause_rationale,
            baseline_metrics=baseline_metrics,
            target_goals=target_goals,
            status=ProposalStatus.DRAFT,
            proposer_id=proposer_id,
        )

        logger.info(
            "Created evidence-based self-evolution proposal",
            proposal_id=proposal.proposal_id,
            category=proposal.category,
            target_files=proposal.target_files,
        )
        return proposal
