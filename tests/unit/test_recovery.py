from neural.boss.recovery import FailureCategory, FailureRecoveryManager, classify_failure
from neural.db.models import Task


def test_failure_classification_categories():
    # 1. Transient error
    c_transient = classify_failure("Connection timed out after 30000ms: rate limit exceeded")
    assert c_transient.category == FailureCategory.TRANSIENT
    assert c_transient.is_retryable is True
    assert c_transient.severity == "low"

    # 2. Test failure
    c_test = classify_failure("AssertionError: 200 != 500", exit_code=1)
    assert c_test.category == FailureCategory.TEST_FAILURE
    assert c_test.is_retryable is True
    assert c_test.exit_code == 1

    # 3. Code quality / syntax error
    c_syntax = classify_failure("SyntaxError: invalid syntax in line 12")
    assert c_syntax.category == FailureCategory.CODE_QUALITY
    assert c_syntax.is_retryable is True

    # 4. Environment dependency
    c_env = classify_failure("ModuleNotFoundError: No module named 'cryptography'")
    assert c_env.category == FailureCategory.ENVIRONMENT
    assert c_env.is_retryable is True

    # 5. Security violation
    c_sec = classify_failure("Security policy violation: forbidden eval() construct")
    assert c_sec.category == FailureCategory.SECURITY
    assert c_sec.is_retryable is False
    assert c_sec.severity == "critical"
    assert c_sec.recommended_action == "escalate_to_boss"


def test_exponential_backoff_calculation():
    manager = FailureRecoveryManager(initial_delay=1.0, backoff_multiplier=2.0, max_delay=30.0)
    assert manager.calculate_backoff(0) == 1.0
    assert manager.calculate_backoff(1) == 2.0
    assert manager.calculate_backoff(2) == 4.0
    assert manager.calculate_backoff(3) == 8.0
    assert manager.calculate_backoff(4) == 16.0
    # Capped at max_delay 30.0
    assert manager.calculate_backoff(5) == 30.0
    assert manager.calculate_backoff(10) == 30.0


def test_recovery_manager_retry_loop():
    manager = FailureRecoveryManager(max_retries=3, initial_delay=0.5, backoff_multiplier=2.0)
    task = Task(
        task_id="TASK-RETRY-01",
        project_id="PROJ-01",
        title="Flaky task",
        objective="Obj",
        scope="Scope",
        acceptance_criteria="Crit",
        retry_count=0,
        max_retries=3
    )

    # First failure -> retry 1
    action1 = manager.handle_failure(task, "AssertionError: Expected 2, got 1", exit_code=1)
    assert action1.action_type == "retry"
    assert action1.retry_count == 1
    assert action1.backoff_delay_seconds == 0.5
    assert task.retry_count == 1

    # Second failure -> retry 2
    action2 = manager.handle_failure(task, "AssertionError: Expected 2, got 1", exit_code=1)
    assert action2.action_type == "retry"
    assert action2.retry_count == 2
    assert action2.backoff_delay_seconds == 1.0
    assert task.retry_count == 2

    # Third failure -> retry 3
    action3 = manager.handle_failure(task, "AssertionError: Expected 2, got 1", exit_code=1)
    assert action3.action_type == "retry"
    assert action3.retry_count == 3
    assert action3.backoff_delay_seconds == 2.0
    assert task.retry_count == 3

    # Fourth failure -> Retries exhausted! Escalate to Boss AI
    action4 = manager.handle_failure(task, "AssertionError: Expected 2, got 1", exit_code=1)
    assert action4.action_type == "escalate_to_boss"
    assert action4.reason == "retries_exhausted"
    assert action4.escalation_report is not None
    assert action4.escalation_report["task_id"] == "TASK-RETRY-01"
    assert action4.escalation_report["retry_count"] == 3
    assert action4.escalation_report["failure_category"] == FailureCategory.TEST_FAILURE.value


def test_recovery_manager_immediate_escalation_on_non_retryable_error():
    manager = FailureRecoveryManager(max_retries=3)
    task = Task(
        task_id="TASK-SEC-01",
        project_id="PROJ-01",
        title="Security task",
        objective="Obj",
        scope="Scope",
        acceptance_criteria="Crit",
        retry_count=0,
        max_retries=3
    )

    action = manager.handle_failure(task, "Security violation: unauthorized tool access")
    assert action.action_type == "escalate_to_boss"
    assert action.reason == "non_retryable_failure"
    assert action.escalation_report["failure_category"] == FailureCategory.SECURITY.value
    assert action.escalation_report["severity"] == "critical"
