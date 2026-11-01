from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from neural.db.models import DecisionRecord
from neural.db.repositories import DecisionRecordRepository
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.learning.decision_history")


class DecisionHistoryService:
    """
    Service for persisting and querying System 1 decision records (Section 10).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DecisionRecordRepository(session)

    async def record_decision(
        self,
        actor_id: str,
        decision_type: str,
        model_checkpoint: str,
        input_payload: dict[str, Any],
        output_choice: Optional[str] = None,
        probabilities: Optional[dict[str, float]] = None,
        confidence: float = 0.0,
        calibrated_prob: float = 0.0,
        latency_ms: float = 0.0,
        was_escalated: bool = False,
        shadow_mode: bool = True,
        task_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> DecisionRecord:
        d_id = decision_id or f"dec_{uuid.uuid4().hex[:12]}"
        return await self.repo.create(
            decision_id=d_id,
            actor_id=actor_id,
            task_id=task_id,
            decision_type=decision_type,
            model_checkpoint=model_checkpoint,
            input_payload=input_payload,
            output_choice=output_choice,
            probabilities=probabilities or {},
            confidence=confidence,
            calibrated_prob=calibrated_prob,
            latency_ms=latency_ms,
            was_escalated=was_escalated,
            shadow_mode=shadow_mode,
        )

    async def get_by_decision_id(self, decision_id: str) -> Optional[DecisionRecord]:
        return await self.repo.get_by_decision_id(decision_id)

    async def list_by_task(self, task_id: str) -> Sequence[DecisionRecord]:
        return await self.repo.list_by_task(task_id)

    async def list_recent(
        self,
        limit: int = 100,
        shadow_mode: Optional[bool] = None,
        decision_type: Optional[str] = None,
    ) -> Sequence[DecisionRecord]:
        return await self.repo.list_recent(limit=limit, shadow_mode=shadow_mode, decision_type=decision_type)

