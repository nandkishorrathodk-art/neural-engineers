from __future__ import annotations

from typing import Any

from neural.cognitive_fabric.memory.models import (
    MemoryLayer,
    utc_now,
)
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.memory.consolidation")


class MemoryConsolidationEngine:
    """
    Consolidates raw episodic experiences into verified semantic facts and procedural recipes.
    Detects contradictions, handles superseding of obsolete decisions, and enforces provenance.
    Never silently overwrites conflicting knowledge.
    """

    def __init__(self, memory_store: SixTierMemoryStore) -> None:
        self.memory_store = memory_store

    async def consolidate_task_experience(
        self,
        task_id: str,
        employee_id: str,
        outcome: str,
        receipt: dict[str, Any],
        verifier_id: str = "BossAI",
    ) -> dict[str, Any]:
        """
        Consolidates completed task execution into:
        1. Episodic receipt.
        2. Semantic learnings if novel technical discovery made.
        3. Procedural recipe if task produced verified reusable workflow.
        """
        logger.info("Consolidating task experience", task_id=task_id, outcome=outcome)

        summary = receipt.get("summary", f"Task {task_id} completed with outcome {outcome}")
        is_success = outcome.lower() in ("success", "passed", "completed")

        # 1. Store episodic record
        episodic_rec = await self.memory_store.store_episodic(
            task_id=task_id,
            employee_id=employee_id,
            outcome=outcome,
            summary=summary,
            receipt=receipt,
            is_verified=is_success,
        )

        consolidated_items = [episodic_rec.id]
        contradictions_detected = []

        # 2. Extract technical claims from receipt if present
        claims = receipt.get("technical_claims", [])
        for claim in claims:
            concept = claim.get("concept")
            statement = claim.get("statement")
            if not concept or not statement:
                continue

            # Check for existing semantic memories about this concept
            existing = self.memory_store.query_records(
                query=concept,
                layer=MemoryLayer.SEMANTIC,
                requester_id="BossAI",
            )

            # Contradiction and conflict detection
            for old_rec in existing:
                if old_rec.metadata.get("concept") == concept:
                    # If existing record has conflicting statement
                    if statement.lower() not in old_rec.content.lower():
                        contradictions_detected.append({
                            "concept": concept,
                            "existing_id": old_rec.id,
                            "new_statement": statement,
                        })
                        if is_success and claim.get("supersedes_old", False):
                            # Mark old as superseded with provenance
                            old_rec.mark_superseded(superseded_by_id=episodic_rec.id)
                        else:
                            # Flag as disputed; never silently overwrite
                            old_rec.mark_disputed(
                                reason=f"Contradicting claim in task {task_id}: {statement}",
                                disputer_id=employee_id,
                            )

            # Store the new verified semantic fact
            sem_rec = await self.memory_store.store_semantic(
                concept=concept,
                explanation=statement,
                source=f"task:{task_id}",
                tags=claim.get("tags", []),
                verified_by=verifier_id if is_success else None,
                confidence=claim.get("confidence", 0.9 if is_success else 0.5),
            )
            consolidated_items.append(sem_rec.id)

        # 3. Extract procedural workflows if present
        procedure = receipt.get("reusable_procedure")
        if procedure and is_success:
            p_name = procedure.get("name", f"Procedure-{task_id}")
            p_steps = procedure.get("steps", [])
            proc_rec = await self.memory_store.store_procedural(
                procedure_name=p_name,
                steps=p_steps,
                code_recipe=procedure.get("code"),
                verified_by=verifier_id,
            )
            consolidated_items.append(proc_rec.id)

        return {
            "task_id": task_id,
            "consolidated_records_count": len(consolidated_items),
            "record_ids": consolidated_items,
            "contradictions_detected": contradictions_detected,
            "timestamp": utc_now().isoformat(),
        }

