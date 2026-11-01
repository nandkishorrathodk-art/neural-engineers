from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from neural.constants import TaskStatus
from neural.db.models import Task
from neural.logger import get_logger

logger = get_logger("ancos.boss.recovery")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    TEST_FAILURE = "test_failure"
    CODE_QUALITY = "code_quality"
    ENVIRONMENT = "environment"
    SECURITY = "security"
    FATAL = "fatal"


@dataclass
class FailureClassification:
    category: FailureCategory
    error_message: str
    exit_code: Optional[int]
    is_retryable: bool
    severity: str  # low, medium, high, critical
    recommended_action: str
    diagnostic_feedback: str


@dataclass
class RecoveryAction:
    action_type: str  # retry, escalate_to_boss, abort
    retry_count: int
    backoff_delay_seconds: float
    reason: str
    diagnostic_feedback: str
    escalation_report: Optional[dict[str, Any]] = None


def classify_failure(
    error: Union[str, Exception],
    exit_code: Optional[int] = None,
    context: Optional[dict[str, Any]] = None
) -> FailureClassification:
    """
    Intelligently inspects error signatures, exit codes, and execution context
    to categorize failures and recommend recovery strategies.
    """
    err_str = str(error).strip()
    err_lower = err_str.lower()

    # 1. Security policy violations (NEVER retryable, critical severity)
    if "security" in err_lower or "forbidden" in err_lower or "unauthorized" in err_lower or "eval(" in err_lower:
        return FailureClassification(
            category=FailureCategory.SECURITY,
            error_message=err_str,
            exit_code=exit_code,
            is_retryable=False,
            severity="critical",
            recommended_action="escalate_to_boss",
            diagnostic_feedback="Security violation detected: code attempted unauthorized operations or forbidden constructs."
        )

    # 2. Code Quality / Syntax issues (retryable with feedback)
    if (
        "syntaxerror" in err_lower
        or "indentationerror" in err_lower
        or "nameerror" in err_lower
        or "linter" in err_lower
        or "syntax error" in err_lower
    ):
        return FailureClassification(
            category=FailureCategory.CODE_QUALITY,
            error_message=err_str,
            exit_code=exit_code,
            is_retryable=True,
            severity="medium",
            recommended_action="retry_with_backoff",
            diagnostic_feedback=f"Code quality / syntax issue detected: {err_str}. Please review and correct syntax before re-submitting."
        )

    # 3. Unit / Integration test failures (retryable with diagnostic guidance)
    if (
        exit_code is not None and exit_code != 0
        or "assert" in err_lower
        or "test failed" in err_lower
        or "failures=" in err_lower
        or "pytest" in err_lower
    ):
        return FailureClassification(
            category=FailureCategory.TEST_FAILURE,
            error_message=err_str,
            exit_code=exit_code,
            is_retryable=True,
            severity="medium",
            recommended_action="retry_with_backoff",
            diagnostic_feedback=f"Automated test runner failed (exit code {exit_code}): {err_str}. Inspect failed assertions and fix logic."
        )

    # 4. Transient network / provider / rate-limit failures (retryable with backoff)
    if (
        "timeout" in err_lower
        or "ratelimit" in err_lower
        or "rate limit" in err_lower
        or "connection" in err_lower
        or "temporarily unavailable" in err_lower
        or "503" in err_lower
        or "504" in err_lower
    ):
        return FailureClassification(
            category=FailureCategory.TRANSIENT,
            error_message=err_str,
            exit_code=exit_code,
            is_retryable=True,
            severity="low",
            recommended_action="retry_with_backoff",
            diagnostic_feedback=f"Transient infrastructure error: {err_str}. Applying exponential backoff."
        )

    # 5. Missing environment / package dependencies (retryable or escalate)
    if (
        "modulenotfounderror" in err_lower
        or "importerror" in err_lower
        or "command not found" in err_lower
        or "not found" in err_lower
    ):
        return FailureClassification(
            category=FailureCategory.ENVIRONMENT,
            error_message=err_str,
            exit_code=exit_code,
            is_retryable=True,
            severity="medium",
            recommended_action="retry_with_backoff",
            diagnostic_feedback=f"Environment dependency missing: {err_str}. Ensure necessary packages are installed in container."
        )

    # 6. Default Fatal failure (unhandled)
    return FailureClassification(
        category=FailureCategory.FATAL,
        error_message=err_str,
        exit_code=exit_code,
        is_retryable=False,
        severity="high",
        recommended_action="escalate_to_boss",
        diagnostic_feedback=f"Unclassified critical failure: {err_str}."
    )


class FailureRecoveryManager:
    """
    Failure Recovery & Escalation Manager.
    Governs failure classification, retry loops with exponential backoff,
    diagnostic feedback loop to employees, and automated escalation to Boss AI
    when retry budgets are exhausted.
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 60.0
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.backoff_multiplier = backoff_multiplier
        self.max_delay = max_delay

    def calculate_backoff(self, retry_count: int) -> float:
        """Computes exponential backoff delay capped by max_delay."""
        delay = self.initial_delay * (self.backoff_multiplier ** retry_count)
        return min(round(delay, 3), self.max_delay)

    def handle_failure(
        self,
        task: Task,
        error: Union[str, Exception],
        exit_code: Optional[int] = None,
        context: Optional[dict[str, Any]] = None
    ) -> RecoveryAction:
        """
        Processes a task failure:
        1. Classifies failure category and retryability.
        2. Evaluates task retry count against task.max_retries or manager max_retries.
        3. If retryable and budget remains: increments retry_count and returns retry action with delay.
        4. If budget exhausted or non-retryable: escalates to Boss AI with detailed incident report.
        """
        classification = classify_failure(error, exit_code=exit_code, context=context)
        effective_max_retries = getattr(task, "max_retries", self.max_retries) or self.max_retries
        current_retry_count = getattr(task, "retry_count", 0)

        logger.info(
            "Handling task failure",
            task_id=task.task_id,
            category=classification.category.value,
            is_retryable=classification.is_retryable,
            retry_count=current_retry_count,
            max_retries=effective_max_retries
        )

        # Case A: Retryable and retries remaining
        if classification.is_retryable and current_retry_count < effective_max_retries:
            delay = self.calculate_backoff(current_retry_count)
            task.retry_count = current_retry_count + 1
            return RecoveryAction(
                action_type="retry",
                retry_count=task.retry_count,
                backoff_delay_seconds=delay,
                reason="retryable_failure",
                diagnostic_feedback=classification.diagnostic_feedback
            )

        # Case B: Retries exhausted or non-retryable failure -> Escalate to Boss AI
        reason = "retries_exhausted" if current_retry_count >= effective_max_retries else "non_retryable_failure"
        escalation_report = {
            "task_id": task.task_id,
            "project_id": task.project_id,
            "assigned_to": getattr(task, "assigned_to", None),
            "retry_count": current_retry_count,
            "max_retries": effective_max_retries,
            "failure_category": classification.category.value,
            "severity": classification.severity,
            "error_message": classification.error_message,
            "diagnostic_feedback": classification.diagnostic_feedback,
            "recommended_intervention": (
                "convene_incident_review"
                if classification.severity in ("high", "critical")
                else "reassign_to_senior_specialist"
            ),
            "escalated_at": utc_now().isoformat()
        }

        logger.warning(
            "Task failure escalated to Boss AI",
            task_id=task.task_id,
            reason=reason,
            retries=current_retry_count
        )

        return RecoveryAction(
            action_type="escalate_to_boss",
            retry_count=current_retry_count,
            backoff_delay_seconds=0.0,
            reason=reason,
            diagnostic_feedback=classification.diagnostic_feedback,
            escalation_report=escalation_report
        )

    async def escalate_to_boss(
        self,
        task: Task,
        escalation_report: dict[str, Any],
        boss_brain: Optional[Any] = None
    ) -> dict[str, Any]:
        """
        Notifies Boss AI of escalated failure, triggers decision or incident post-mortem.
        """
        task.status = TaskStatus.FAILED
        decision = {
            "task_id": task.task_id,
            "status": "escalated_to_boss",
            "escalation_report": escalation_report,
            "resolution": "Incident logged for Boss AI arbitration"
        }
        if boss_brain and hasattr(boss_brain, "record_incident"):
            await boss_brain.record_incident(task.task_id, escalation_report)
        return decision
