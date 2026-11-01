from __future__ import annotations

import pytest
from neural.decision_intelligence.calibration.calibration_monitor import (
    CalibrationMonitor,
    reset_calibration_monitor,
)
from neural.decision_intelligence.calibration.reliability import (
    apply_platt_scaling,
    calculate_brier_score,
    calculate_ece,
    calculate_reliability_diagram,
)
from neural.decision_intelligence.calibration.thresholds import (
    reset_threshold_manager,
)
from neural.decision_intelligence.routing.candidate_selector import CandidateSelector
from neural.decision_intelligence.routing.complexity_classifier import ComplexityClassifier
from neural.decision_intelligence.routing.decision_router import (
    IntelligentDecisionRouter,
    reset_decision_router,
)
from neural.decision_intelligence.schemas.choice import ChoiceCandidate, ChoiceQuestion
from neural.exceptions import CalibrationDriftError, EmergencyStopTriggered
from neural.security.kill_switch import activate_kill_switch, reset_kill_switch


@pytest.fixture(autouse=True)
def cleanup():
    reset_kill_switch()
    reset_threshold_manager()
    reset_calibration_monitor()
    reset_decision_router()
    from neural.decision_intelligence.system_one.inference_service import reset_inference_service
    reset_inference_service()
    yield
    reset_kill_switch()
    reset_threshold_manager()
    reset_calibration_monitor()
    reset_decision_router()
    reset_inference_service()


def test_complexity_classifier():
    classifier = ComplexityClassifier()

    # Routine selection -> eligible for System 1
    routine_res = classifier.assess(
        context="route_task to specialist employee",
        num_candidates=3,
    )
    assert routine_res.is_eligible_for_system1 is True
    assert routine_res.risk_level == "LOW"

    # High-risk destructive action -> ineligible, critical risk
    destructive_res = classifier.assess(
        context="Drop table and delete production records from database",
        num_candidates=2,
    )
    assert destructive_res.is_eligible_for_system1 is False
    assert destructive_res.risk_level == "CRITICAL"

    # Too many candidates (> 12) -> ineligible
    cardinality_res = classifier.assess(
        context="Select item",
        num_candidates=15,
    )
    assert cardinality_res.is_eligible_for_system1 is False

    # Deep reasoning keywords
    deep_res = classifier.assess(
        context="Conduct architecture_review and strategic_initiative planning",
        num_candidates=4,
    )
    assert deep_res.complexity_score > 0.5


def test_candidate_selector_bounding_and_reduction():
    selector = CandidateSelector()

    # Rejection of < 2 candidates
    with pytest.raises(ValueError, match="at least 2"):
        selector.prepare_candidates([ChoiceCandidate(id="c1", label="Option 1")])

    # Valid bounding between 2 and 12
    valid_cands = [ChoiceCandidate(id=f"c_{i}", label=f"Option {i}") for i in range(5)]
    prepared = selector.prepare_candidates(valid_cands)
    assert len(prepared) == 5

    # Reduction from 15 candidates down to 12
    many_cands = [
        ChoiceCandidate(id=f"c_{i}", label=f"Option {i} related to python test" if i == 2 else f"Option {i}")
        for i in range(15)
    ]
    reduced = selector.prepare_candidates(many_cands, context="Need python test option")
    assert len(reduced) == 12
    # Verify the most relevant candidate was kept
    assert any(c.id == "c_2" for c in reduced)


def test_reliability_ece_and_brier():
    # Perfect calibration test
    probs = [0.1, 0.9]
    truths = [0, 1]
    ece = calculate_ece(probs, truths, num_bins=10)
    assert ece <= 0.15

    # Brier score test
    brier = calculate_brier_score(probs, truths)
    assert brier < 0.05

    # Reliability diagram
    diagram = calculate_reliability_diagram(probs, truths, num_bins=5)
    assert len(diagram) == 5

    # Platt scaling test
    scaled = apply_platt_scaling(0.5, a=1.0, b=0.0)
    assert abs(scaled - 0.5) < 0.05


def test_calibration_monitor_drift_detection():
    monitor = CalibrationMonitor(max_ece_threshold=0.08, min_samples_for_eval=10)

    # Record 20 well-calibrated samples
    for _ in range(10):
        monitor.record(0.95, 1, category="routing")
        monitor.record(0.05, 0, category="routing")

    metrics = monitor.get_metrics("routing")
    assert metrics["is_calibrated"] is True
    assert metrics["ece"] <= 0.08

    # Now inject severe miscalibration: high confidence predictions that are wrong
    for _ in range(30):
        monitor.record(0.99, 0, category="routing")

    has_drifted, drift_ece = monitor.check_drift("routing")
    assert has_drifted is True
    assert drift_ece > 0.08

    with pytest.raises(CalibrationDriftError, match="Calibration drift detected"):
        monitor.check_drift("routing", raise_on_drift=True)


@pytest.mark.asyncio
async def test_decision_router_and_escalation():
    router = IntelligentDecisionRouter()

    # Routine choice routing
    cands = [
        ChoiceCandidate(id="eng", label="Engineering Department"),
        ChoiceCandidate(id="qa", label="QA Department"),
    ]
    q_routine = ChoiceQuestion(
        context="Write Python code for feature",
        candidates=cands,
        domain="routing",
    )
    res_routine = await router.route_choice(q_routine)
    assert res_routine.selected_id in ["eng", "qa"]
    assert res_routine.was_escalated is False
    assert res_routine.latency_ms < 50.0

    # Complex / high-risk action must escalate to Deep Brain
    q_destructive = ChoiceQuestion(
        context="Drop table and delete production records from database",
        candidates=cands,
        domain="routing",
    )
    res_destructive = await router.route_choice(q_destructive)
    assert res_destructive.was_escalated is True
    assert "System2" in res_destructive.model_name

    # Kill switch must block decisions immediately
    activate_kill_switch("operator", "Emergency security incident")
    with pytest.raises(EmergencyStopTriggered, match="Kill Switch"):
        await router.route_choice(q_routine)


def test_candidate_selector_duplicate_rejection():
    selector = CandidateSelector()
    # List of 2 candidates with identical IDs deduplicates to 1, which must be rejected
    dup_candidates = [
        ChoiceCandidate(id="dup_id", label="Option A"),
        ChoiceCandidate(id="dup_id", label="Option B"),
    ]
    with pytest.raises(ValueError, match="Need at least 2 unique candidates"):
        selector.prepare_candidates(dup_candidates)


@pytest.mark.asyncio
async def test_escalation_probability_sums_to_one():
    from neural.decision_intelligence.routing.escalation import EscalationController
    from neural.decision_intelligence.schemas.score import ScoreQuestion

    controller = EscalationController()

    # Choice escalation with 5 candidates
    cands = [ChoiceCandidate(id=f"c_{i}", label=f"Candidate {i}") for i in range(5)]
    q_choice = ChoiceQuestion(context="test escalation", candidates=cands)
    choice_res = await controller.escalate_choice(q_choice, reason="Testing normalization")
    assert abs(sum(choice_res.probabilities.values()) - 1.0) < 1e-4

    # Score escalation with 5 steps
    q_score = ScoreQuestion(proposition="Test score escalation", scale_min=1, scale_max=5)
    score_res = await controller.escalate_score(q_score, reason="Testing normalization")
    assert abs(sum(score_res.probabilities.values()) - 1.0) < 1e-4


@pytest.mark.asyncio
async def test_score_uninformative_escalation():
    from neural.decision_intelligence.schemas.score import ScoreQuestion

    router = IntelligentDecisionRouter()
    # Proposition with zero urgency cues should result in low confidence and escalate
    q_neutral = ScoreQuestion(
        proposition="Periodic status poll of running tasks",
        context="general background query",
        scale_min=1,
        scale_max=5,
        domain="urgency",
    )
    res = await router.route_score(q_neutral)
    assert res.was_escalated is True
    assert "System2" in res.model_name
