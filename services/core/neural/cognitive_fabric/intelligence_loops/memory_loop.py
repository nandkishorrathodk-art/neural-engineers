from __future__ import annotations

from typing import Any, Optional

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.cognitive_fabric.memory.consolidation import MemoryConsolidationEngine
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.memory")


class ControlledMemoryLoop:
    """
    Controlled Memory Consolidation Loop:
    Experience → Extract → Verify → Resolve Contradictions → Store.
    Consolidates task experiences into verified semantic and procedural knowledge
    while detecting contradictions, handling superseding, and guarding against cycle recursion.
    """

    def __init__(
        self,
        memory_store: Optional[SixTierMemoryStore] = None,
        consolidation_engine: Optional[MemoryConsolidationEngine] = None,
        max_iterations: int = 3,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.memory_store = memory_store or SixTierMemoryStore()
        self.consolidation_engine = consolidation_engine or MemoryConsolidationEngine(self.memory_store)
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        task_id: str,
        employee_id: str,
        outcome: str,
        receipt: dict[str, Any],
        verifier_id: str = "BossAI",
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name=f"MemoryLoop-{task_id}",
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )

        status = "in_progress"
        result: dict[str, Any] = {}

        try:
            while status == "in_progress":
                # Step 1: Extract & Consolidate
                result = await self.consolidation_engine.consolidate_task_experience(
                    task_id=task_id,
                    employee_id=employee_id,
                    outcome=outcome,
                    receipt=receipt,
                    verifier_id=verifier_id,
                )

                # Record consolidation state with CycleGuard
                state_sig = {
                    "task_id": task_id,
                    "consolidated_count": result.get("consolidated_records_count", 0),
                    "contradictions": len(result.get("contradictions_detected", [])),
                }
                guard.record_step(
                    step_name="extract_verify_store",
                    state=state_sig,
                    tokens_used=80,
                    cost_usd=0.001,
                    notes=f"Consolidated: {result.get('consolidated_records_count')}, Contradictions: {len(result.get('contradictions_detected', []))}",
                )

                status = "consolidated"
                break

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("Memory loop halted early by guard", error=str(e))
            status = "escalated"

        return {
            "task_id": task_id,
            "employee_id": employee_id,
            "status": status,
            "iterations": guard.iteration,
            "consolidation_result": result,
            "audit_report": guard.get_audit_report(),
        }

