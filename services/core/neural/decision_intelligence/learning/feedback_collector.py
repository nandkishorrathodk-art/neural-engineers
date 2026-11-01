from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from neural.db.models import DecisionRecord
from neural.db.repositories import DecisionRecordRepository
from neural.decision_intelligence.calibration.calibration_monitor import (
    CalibrationMonitor,
    get_calibration_monitor,
)
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.learning.feedback_collector")


class FeedbackCollector:
    """
    Feedback Collector for ground-truth verification outcomes (Section 10 & 13).
    Attaches independent QA receipts to DecisionRecords and feeds calibration monitor.
    """

    def __init__(
        self,
        session: AsyncSession,
        monitor: Optional[CalibrationMonitor] = None,
    ) -> None:
        self.session = session
        self.repo = DecisionRecordRepository(session)
        self.monitor = monitor or get_calibration_monitor()

    async def record_feedback(
        self,
        decision_id: str,
        actual_outcome: str,
        category: str = "general",
    ) -> Optional[DecisionRecord]:
        record = await self.repo.update_outcome(decision_id=decision_id, actual_outcome=actual_outcome)
        if not record:
            logger.warning("Decision record not found for feedback", decision_id=decision_id)
            return None

        # Determine binary correctness
        is_correct = 0
        if record.decision_type == "CHOICE":
            is_correct = 1 if record.output_choice == actual_outcome else 0
            self.monitor.record(
                probability=record.confidence,
                outcome=is_correct,
                category=category,
            )
        elif record.decision_type == "SCORE":
            is_correct = 1 if str(record.output_choice) == str(actual_outcome) else 0
            self.monitor.record(
                probability=record.confidence,
                outcome=is_correct,
                category=category,
            )
        elif record.decision_type == "NOUL":
            is_correct = 1 if str(record.output_choice).strip().lower() == str(actual_outcome).strip().lower() else 0
            self.monitor.record(
                probability=record.confidence,
                outcome=is_correct,
                category=category,
            )

        logger.info(
            "Recorded decision feedback",
            decision_id=decision_id,
            actual_outcome=actual_outcome,
            is_correct=is_correct,
        )
        return record

