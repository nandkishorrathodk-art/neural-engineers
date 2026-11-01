from __future__ import annotations

from typing import Any, Optional, Sequence

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.cognitive_fabric.retrieval.evidence_evaluator import EvidenceEvaluator
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.retrieval")


class ControlledRetrievalLoop:
    """
    Controlled Retrieval Loop: Query → Search → Inspect → Refine → Verify.
    Executes bounded iterative search cycles with cryptographic cycle detection,
    timeouts, and evidence sufficiency evaluation.
    """

    def __init__(
        self,
        retriever: Optional[HybridRetrievalEngine] = None,
        evaluator: Optional[EvidenceEvaluator] = None,
        max_iterations: int = 3,
        timeout_seconds: float = 15.0,
        sufficiency_threshold: float = 0.75,
    ) -> None:
        self.retriever = retriever or HybridRetrievalEngine()
        self.evaluator = evaluator or EvidenceEvaluator()
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.sufficiency_threshold = sufficiency_threshold

    async def execute(
        self,
        query: str,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name=f"RetrievalLoop-{query[:20]}",
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )

        accumulated_evidence: dict[str, dict[str, Any]] = {}
        current_query = query
        status = "in_progress"
        final_sufficiency = 0.0
        conflicts = []

        try:
            while status == "in_progress":
                # Step 1: Search
                batch = self.retriever.retrieve(
                    current_query,
                    top_k=5,
                    requester_id=requester_id,
                    department=department,
                    projects=projects,
                )
                for doc in batch:
                    accumulated_evidence[doc["id"]] = doc

                # Step 2: Inspect & Verify
                evidence_list = list(accumulated_evidence.values())
                final_sufficiency, gaps = self.evaluator.evaluate_sufficiency(query, evidence_list)
                conflicts = self.evaluator.detect_conflicts(evidence_list)

                # Record loop state with CycleGuard
                evidence_signature = sorted(accumulated_evidence.keys())
                guard.record_step(
                    step_name="search_and_inspect",
                    state={"query": current_query, "evidence": evidence_signature, "sufficiency": final_sufficiency},
                    notes=f"Sufficiency: {final_sufficiency}, Gaps: {len(gaps)}",
                )

                # Stop condition: sufficiency reached
                if final_sufficiency >= self.sufficiency_threshold:
                    status = "converged"
                    break

                # Step 3: Refine
                if gaps and guard.iteration < self.max_iterations:
                    current_query = f"{query} {gaps[0]}"
                else:
                    status = "completed_insufficient" if final_sufficiency < 0.4 else "converged"
                    break

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("Retrieval loop halted early by guard", error=str(e))
            status = "escalated"

        final_evidence = list(accumulated_evidence.values())
        final_evidence.sort(key=lambda x: (1 if x.get("is_verified") else 0, x.get("rrf_score", 0.0)), reverse=True)

        return {
            "query": query,
            "status": status,
            "iterations": guard.iteration,
            "evidence_count": len(final_evidence),
            "evidence": final_evidence[:10],
            "sufficiency_score": final_sufficiency,
            "conflicts": conflicts,
            "escalated_to_boss": status == "escalated",
            "audit_report": guard.get_audit_report(),
        }

