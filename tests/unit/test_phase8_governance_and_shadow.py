from __future__ import annotations

import pytest
from neural.decision_intelligence.evaluation.accuracy_metrics import AccuracyEvaluator
from neural.decision_intelligence.evaluation.benchmark_runner import BenchmarkRunner
from neural.decision_intelligence.evaluation.decision_comparison import DecisionComparator
from neural.decision_intelligence.evaluation.latency_metrics import LatencyTracker
from neural.decision_intelligence.governance.promotion import (
    PromotionCriteria,
    PromotionManager,
)
from neural.decision_intelligence.governance.rollback import (
    RollbackEngine,
    reset_rollback_engine,
)
from neural.decision_intelligence.governance.shadow_mode import (
    ShadowModeManager,
    reset_shadow_mode_manager,
)
from neural.exceptions import ShadowModeViolationError


@pytest.fixture(autouse=True)
def cleanup():
    reset_shadow_mode_manager()
    reset_rollback_engine()
    from neural.decision_intelligence.routing.decision_router import reset_decision_router
    from neural.decision_intelligence.system_one.inference_service import reset_inference_service
    reset_decision_router()
    reset_inference_service()
    yield
    reset_shadow_mode_manager()
    reset_rollback_engine()
    reset_decision_router()
    reset_inference_service()


def test_shadow_mode_enforcement():
    shadow_mgr = ShadowModeManager()

    # GUI selection starts in shadow mode
    assert shadow_mgr.is_in_shadow_mode("gui_selection") is True

    # Side-effects should be blocked
    with pytest.raises(ShadowModeViolationError, match="Illegal side-effect in shadow mode"):
        shadow_mgr.assert_no_side_effects("gui_selection", "mouse_click_execute")

    # Change state to live
    shadow_mgr.set_shadow_mode("gui_selection", False)
    assert shadow_mgr.is_in_shadow_mode("gui_selection") is False
    # Now asserting no side effects should not raise
    shadow_mgr.assert_no_side_effects("gui_selection", "mouse_click_execute")


def test_promotion_manager_criteria_checker():
    shadow_mgr = ShadowModeManager()
    criteria = PromotionCriteria(
        min_episodes=100,
        min_accuracy=0.96,
        max_ece=0.08,
        max_p95_latency_ms=40.0,
    )
    promotion_mgr = PromotionManager(criteria=criteria, shadow_manager=shadow_mgr)

    # 1. Failure due to insufficient episodes (< 100)
    res_few = promotion_mgr.evaluate_promotion(
        category="test_cat",
        predictions=["a"] * 50,
        ground_truths=["a"] * 50,
        confidences=[0.95] * 50,
        latencies_ms=[20.0] * 50,
    )
    assert res_few.eligible_for_promotion is False
    assert any("Insufficient episodes" in r for r in res_few.rejection_reasons)

    # 2. Failure due to low accuracy (< 96%)
    res_inaccurate = promotion_mgr.evaluate_promotion(
        category="test_cat",
        predictions=["a"] * 90 + ["b"] * 10,
        ground_truths=["a"] * 100,
        confidences=[0.95] * 100,
        latencies_ms=[20.0] * 100,
    )
    assert res_inaccurate.eligible_for_promotion is False
    assert any("Accuracy" in r for r in res_inaccurate.rejection_reasons)

    # 3. Successful promotion meeting all criteria
    res_success = promotion_mgr.evaluate_promotion(
        category="gui_selection",
        predictions=["a"] * 98 + ["b"] * 2,
        ground_truths=["a"] * 98 + ["b"] * 2,
        confidences=[0.96] * 100,
        latencies_ms=[25.0] * 100,
        auto_promote=True,
    )
    assert res_success.eligible_for_promotion is True
    assert len(res_success.rejection_reasons) == 0
    # Shadow mode should have been disabled
    assert shadow_mgr.is_in_shadow_mode("gui_selection") is False


def test_rollback_engine():
    shadow_mgr = ShadowModeManager()
    shadow_mgr.set_shadow_mode("routing", False)
    assert shadow_mgr.is_in_shadow_mode("routing") is False

    rollback_engine = RollbackEngine(shadow_manager=shadow_mgr)
    event = rollback_engine.execute_rollback(
        category="routing",
        reason="Severe calibration drift ECE > 0.12 detected",
    )

    assert event.category == "routing"
    assert event.reverted_to == "shadow_mode"
    # Category must now be back in shadow mode
    assert shadow_mgr.is_in_shadow_mode("routing") is True
    assert len(rollback_engine.list_rollback_history()) == 1


def test_accuracy_and_latency_evaluation():
    # Classification metrics
    y_true = ["a", "b", "c", "a"]
    y_pred = ["a", "b", "b", "a"]
    metrics = AccuracyEvaluator.compute_classification_metrics(y_true, y_pred)
    assert metrics["accuracy"] == 0.75
    assert metrics["total_samples"] == 4.0

    # Selective accuracy
    sel_metrics = AccuracyEvaluator.compute_selective_accuracy(
        y_true=y_true,
        y_pred=y_pred,
        confidences=[0.9, 0.9, 0.6, 0.9],
        threshold=0.85,
    )
    assert sel_metrics["coverage"] == 0.75
    assert sel_metrics["selective_accuracy"] == 1.0  # Errant sample was escalated

    # Latency tracker
    tracker = LatencyTracker(target_p95_ms=40.0)
    for ms in [10.0, 15.0, 20.0, 25.0, 30.0]:
        tracker.record(ms)
    summary = tracker.get_summary()
    assert summary["count"] == 5
    assert summary["p50_ms"] == 20.0
    assert summary["meets_sla"] is True


def test_decision_comparator():
    s1_choices = ["a", "b", "c", "d"]
    s2_choices = ["a", "b", "c", "x"]
    s1_lats = [20.0, 25.0, 22.0, 21.0]
    s2_lats = [2000.0, 2500.0, 2200.0, 2100.0]

    comparison = DecisionComparator.compare_decisions(s1_choices, s2_choices, s1_lats, s2_lats)
    assert comparison.total_compared == 4
    assert comparison.agreement_rate == 0.75
    assert comparison.speedup_factor > 50.0
    assert comparison.estimated_tokens_saved == 4 * 450


@pytest.mark.asyncio
async def test_benchmark_runner():
    runner = BenchmarkRunner()
    report = await runner.run_benchmark()

    assert report.total_scenarios >= 5
    assert report.passed_scenarios >= 4
    assert report.accuracy >= 0.80
    assert report.p95_latency_ms < 60.0
    assert report.meets_calibration_gate is True
