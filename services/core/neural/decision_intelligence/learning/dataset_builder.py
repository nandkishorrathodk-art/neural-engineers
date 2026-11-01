from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from neural.db.models import DecisionRecord
from neural.db.repositories import DecisionRecordRepository
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.learning.dataset_builder")


class DatasetBuilder:
    """
    Compiles validated decision traces into fine-tuning & calibration datasets (Section 13).
    Filters exclusively for verified ground-truth records.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DecisionRecordRepository(session)

    async def build_dataset(
        self,
        decision_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        recent_records: Sequence[DecisionRecord] = await self.repo.list_recent(
            limit=limit,
            decision_type=decision_type,
        )

        dataset: list[dict[str, Any]] = []

        for r in recent_records:
            # Only include records with verified empirical ground truth
            if not r.actual_outcome:
                continue

            item = {
                "decision_id": r.decision_id,
                "decision_type": r.decision_type,
                "input": r.input_payload,
                "predicted": r.output_choice,
                "ground_truth": r.actual_outcome,
                "confidence": r.confidence,
                "calibrated_prob": r.calibrated_prob,
                "latency_ms": r.latency_ms,
                "model_checkpoint": r.model_checkpoint,
                "was_correct": (
                    str(r.output_choice).strip().lower() == str(r.actual_outcome).strip().lower()
                    if r.decision_type == "NOUL"
                    else str(r.output_choice).strip() == str(r.actual_outcome).strip()
                ),
            }
            dataset.append(item)

        logger.info("Compiled verified dataset", samples=len(dataset), type=decision_type)
        return dataset

    @staticmethod
    def export_to_jsonl(dataset: list[dict[str, Any]]) -> str:
        return "\n".join(json.dumps(d) for d in dataset)

