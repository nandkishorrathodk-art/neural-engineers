from __future__ import annotations

import pytest
from neural.decision_intelligence.calibration.calibration_monitor import CalibrationMonitor
from neural.decision_intelligence.learning.dataset_builder import DatasetBuilder
from neural.decision_intelligence.learning.decision_history import DecisionHistoryService
from neural.decision_intelligence.learning.feedback_collector import FeedbackCollector
from neural.decision_intelligence.observability.decision_audit import (
    DecisionAuditLogger,
    reset_decision_audit_logger,
)
from neural.decision_intelligence.observability.model_health import (
    ModelHealthMonitor,
    reset_model_health_monitor,
)
from neural.decision_intelligence.observability.performance_report import PerformanceReporter
from neural.exceptions import AuditIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def cleanup():
    reset_decision_audit_logger()
    reset_model_health_monitor()
    yield
    reset_decision_audit_logger()
    reset_model_health_monitor()


@pytest.mark.asyncio
async def test_decision_history_and_feedback(async_session: AsyncSession):
    history_svc = DecisionHistoryService(async_session)

    # 1. Record decision
    rec = await history_svc.record_decision(
        actor_id="emp_01",
        decision_type="CHOICE",
        model_checkpoint="Laya-Typed-Decisions",
        input_payload={"context": "Route to team"},
        output_choice="team_qa",
        confidence=0.92,
        calibrated_prob=0.92,
        latency_ms=18.5,
        shadow_mode=False,
        task_id="task_123",
    )
    assert rec.decision_id is not None
    assert rec.output_choice == "team_qa"

    # 2. Retrieve decision
    fetched = await history_svc.get_by_decision_id(rec.decision_id)
    assert fetched is not None
    assert fetched.actor_id == "emp_01"

    # 3. List by task
    task_recs = await history_svc.list_by_task("task_123")
    assert len(task_recs) == 1

    # 4. Attach feedback
    calib_mon = CalibrationMonitor(min_samples_for_eval=2)
    feedback_collector = FeedbackCollector(async_session, monitor=calib_mon)
    updated = await feedback_collector.record_feedback(
        decision_id=rec.decision_id,
        actual_outcome="team_qa",
        category="routing",
    )
    assert updated is not None
    assert updated.actual_outcome == "team_qa"

    # Verify calibration monitor received the sample
    metrics = calib_mon.get_metrics("routing")
    assert metrics["sample_count"] == 1


@pytest.mark.asyncio
async def test_dataset_builder(async_session: AsyncSession):
    history_svc = DecisionHistoryService(async_session)

    # Record 2 decisions: one verified, one unverified
    rec1 = await history_svc.record_decision(
        actor_id="boss",
        decision_type="CHOICE",
        model_checkpoint="Laya-Typed-Decisions",
        input_payload={"context": "Verified choice"},
        output_choice="opt_1",
    )
    # Record unverified
    await history_svc.record_decision(
        actor_id="boss",
        decision_type="SCORE",
        model_checkpoint="Laya-Typed-Decisions",
        input_payload={"context": "Unverified score"},
        output_choice="3",
    )

    # Attach outcome to rec1
    feedback_col = FeedbackCollector(async_session)
    await feedback_col.record_feedback(rec1.decision_id, actual_outcome="opt_1")

    # Build dataset
    builder = DatasetBuilder(async_session)
    dataset = await builder.build_dataset()

    # Only verified records should be included
    assert len(dataset) == 1
    assert dataset[0]["decision_id"] == rec1.decision_id
    assert dataset[0]["was_correct"] is True

    jsonl_output = builder.export_to_jsonl(dataset)
    assert rec1.decision_id in jsonl_output


def test_tamper_evident_decision_audit_logger():
    audit = DecisionAuditLogger()

    # Log 3 decisions
    e1 = audit.log_decision("dec_1", "CHOICE", "opt_a", 0.95)
    e2 = audit.log_decision("dec_2", "SCORE", "4", 0.88)
    e3 = audit.log_decision("dec_3", "NOUL", "True", 0.91)

    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.entry_hash
    assert e3.prev_hash == e2.entry_hash

    # Verify valid chain
    is_valid, broken_idx = audit.verify_integrity()
    assert is_valid is True
    assert broken_idx is None

    # Tamper with middle entry
    audit._chain[1].output_choice = "tampered_choice"
    is_tampered, broken_idx = audit.verify_integrity()
    assert is_tampered is False
    assert broken_idx == 1

    with pytest.raises(AuditIntegrityError):
        audit.verify_integrity(raise_on_failure=True)


def test_model_health_and_circuit_breaker():
    health = ModelHealthMonitor(max_consecutive_failures=3, max_error_rate=0.20)

    # Record 5 successful calls
    for _ in range(5):
        health.record_call(latency_ms=25.0, is_error=False)

    report = health.get_report()
    assert report.state.value == "healthy"
    assert report.circuit_breaker_open is False

    # Record 3 consecutive failures to trip circuit breaker
    for _ in range(3):
        health.record_call(latency_ms=0.0, is_error=True)

    report_broken = health.get_report()
    assert report_broken.circuit_breaker_open is True
    assert report_broken.state.value == "unhealthy"

    # Reset circuit breaker
    health.reset_circuit_breaker()
    assert health.circuit_breaker_open is False


def test_performance_reporter():
    reporter = PerformanceReporter()
    rep = reporter.generate_report()

    assert "ANCOS Phase 8" in rep["title"]
    assert "health_state" in rep
    assert "throughput" in rep

    markdown = reporter.generate_markdown_summary()
    assert "# ANCOS Laya Decision Intelligence" in markdown
    assert "Tokens Saved" in markdown


@pytest.mark.asyncio
async def test_noul_feedback_calibration_consistency(async_session: AsyncSession):
    # 20 accurate negative NOUL decisions must not falsely trigger drift
    history_svc = DecisionHistoryService(async_session)
    calib_mon = CalibrationMonitor(min_samples_for_eval=10, max_ece_threshold=0.08)
    feedback_collector = FeedbackCollector(async_session, monitor=calib_mon)

    for i in range(20):
        rec = await history_svc.record_decision(
            actor_id="emp_01",
            decision_type="NOUL",
            model_checkpoint="Laya-Typed-Decisions",
            input_payload={"proposition": "deadlock"},
            output_choice="False",
            confidence=0.95,
            calibrated_prob=0.05,
            latency_ms=15.0,
            decision_id=f"dec_noul_{i}",
        )
        await feedback_collector.record_feedback(rec.decision_id, actual_outcome="False", category="blocker")

    has_drifted, ece = calib_mon.check_drift("blocker")
    assert has_drifted is False
    assert ece <= 0.08


@pytest.mark.asyncio
async def test_dataset_builder_noul_case_normalization(async_session: AsyncSession):
    history_svc = DecisionHistoryService(async_session)
    rec = await history_svc.record_decision(
        actor_id="boss",
        decision_type="NOUL",
        model_checkpoint="Laya-Typed-Decisions",
        input_payload={"proposition": "is_healthy"},
        output_choice="True",
        confidence=0.92,
        decision_id="dec_case_test",
    )
    feedback_col = FeedbackCollector(async_session)
    await feedback_col.record_feedback(rec.decision_id, actual_outcome="true")

    builder = DatasetBuilder(async_session)
    dataset = await builder.build_dataset(decision_type="NOUL")
    assert len(dataset) == 1
    assert dataset[0]["was_correct"] is True


def test_audit_logger_concurrency():
    import concurrent.futures

    audit = DecisionAuditLogger()

    def worker(worker_id: int):
        for j in range(25):
            audit.log_decision(
                decision_id=f"w{worker_id}_d{j}",
                decision_type="CHOICE",
                output_choice="opt_1",
                confidence=0.90,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, i) for i in range(4)]
        concurrent.futures.wait(futures)

    assert len(audit.get_entries(limit=200)) == 100
    is_valid, broken_idx = audit.verify_integrity()
    assert is_valid is True
    assert broken_idx is None
