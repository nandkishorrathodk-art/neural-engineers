"""Unit tests for the NEURO-12A batch measurement logic.

These exercise the pure aggregation in ``batch_runner`` (no live deployment):
unique-objective sampling, in-flight/timeout separation, per-mode isolation,
retry frequency, latency/cost/resource reporting, comparability guards, and the
smoke gate. The live driver in ``batch_driver`` is deliberately not unit tested
here because it requires a real stack; it is validated by an actual cohort run.
"""

from neural.reliability.batch_runner import (
    COHORT_EXTENDED,
    COHORT_SMOKE,
    GUI_MODE,
    HYBRID_MODE,
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_IN_FLIGHT,
    OUTCOME_TIMEOUT,
    ObjectiveObservation,
    build_mode_cohort_stats,
    classify_outcome,
    compare_cohorts,
    evaluate_smoke_gate,
    extract_failure_reasons,
    extract_receipt_cost_usd,
    gui_objective,
    hybrid_objective,
    summarize_attempts,
)


def _obs(
    mode,
    outcome,
    *,
    cohort=COHORT_SMOKE,
    retries=0,
    latency=10.0,
    cost=0.0,
    reasons=None,
    contract_violation="",
    verified=False,
    resources=None,
):
    return ObjectiveObservation(
        sample_id=f"{mode}-{outcome}-{id(object())}",
        mode=mode,
        cohort=cohort,
        objective="x",
        outcome=outcome,
        retries=retries,
        latency_seconds=latency,
        cost_usd=cost,
        verified=verified,
        failure_reasons=reasons or [],
        contract_violation=contract_violation,
        resource_samples=resources or [],
    )


def test_unique_objectives_are_samples_not_retries():
    # Three unique GUI objectives, one of which needed two retries.
    observations = [
        _obs(GUI_MODE, OUTCOME_COMPLETED, retries=0),
        _obs(GUI_MODE, OUTCOME_COMPLETED, retries=2),
        _obs(GUI_MODE, OUTCOME_FAILED, retries=1),
    ]
    stats = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    assert stats.counted_samples == 3  # objectives, not retries
    assert stats.total_retries == 3
    assert stats.retry_frequency == 1.0
    assert stats.success_rate == 0.6667


def test_in_flight_and_timeout_never_count_as_success():
    observations = [
        _obs(GUI_MODE, OUTCOME_COMPLETED),
        _obs(GUI_MODE, OUTCOME_IN_FLIGHT),
        _obs(GUI_MODE, OUTCOME_TIMEOUT),
    ]
    stats = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    assert stats.completed == 1
    assert stats.in_flight == 1
    assert stats.timed_out == 1
    assert stats.counted_samples == 2  # in-flight excluded from denominator
    assert stats.decided == 2
    assert stats.success_rate == 0.5
    assert stats.timeout_rate == 0.5


def test_modes_and_cohorts_stay_separate():
    observations = [
        _obs(GUI_MODE, OUTCOME_COMPLETED, cohort=COHORT_SMOKE),
        _obs(HYBRID_MODE, OUTCOME_FAILED, cohort=COHORT_SMOKE),
        _obs(GUI_MODE, OUTCOME_FAILED, cohort=COHORT_EXTENDED),
    ]
    gui_smoke = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    hybrid_smoke = build_mode_cohort_stats(observations, mode=HYBRID_MODE, cohort=COHORT_SMOKE)
    gui_ext = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_EXTENDED)
    assert (gui_smoke.counted_samples, gui_smoke.success_rate) == (1, 1.0)
    assert (hybrid_smoke.counted_samples, hybrid_smoke.success_rate) == (1, 0.0)
    assert (gui_ext.counted_samples, gui_ext.success_rate) == (1, 0.0)


def test_contract_violations_are_visible_but_not_sampled():
    observations = [
        _obs(GUI_MODE, OUTCOME_IN_FLIGHT, contract_violation="planner produced mode 'terminal'"),
        _obs(GUI_MODE, OUTCOME_COMPLETED),
    ]
    stats = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    assert stats.contract_violations == 1
    assert stats.counted_samples == 1
    assert stats.success_rate == 1.0


def test_latency_cost_and_resource_reporting():
    observations = [
        _obs(
            GUI_MODE,
            OUTCOME_COMPLETED,
            latency=10.0,
            cost=0.02,
            resources=[{"cpu_percent": 40.0, "mem_mib": 200.0}],
        ),
        _obs(
            GUI_MODE,
            OUTCOME_COMPLETED,
            latency=30.0,
            cost=0.04,
            resources=[{"cpu_percent": 90.0, "mem_mib": 350.0}],
        ),
    ]
    stats = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    assert stats.mean_latency_seconds == 20.0
    assert stats.p95_latency_seconds is not None and stats.p95_latency_seconds > 20.0
    assert stats.total_cost_usd == 0.06
    assert stats.cost_per_sample_usd == 0.03
    assert stats.peak_cpu_percent == 90.0
    assert stats.peak_mem_mib == 350.0


def test_failure_reasons_are_aggregated_verbatim():
    observations = [
        _obs(GUI_MODE, OUTCOME_FAILED, reasons=["verifier: required on-screen terms not observed"]),
        _obs(GUI_MODE, OUTCOME_FAILED, reasons=["verifier: required on-screen terms not observed"]),
        _obs(GUI_MODE, OUTCOME_FAILED, reasons=["provision failed: HTTP 409"]),
    ]
    stats = build_mode_cohort_stats(observations, mode=GUI_MODE, cohort=COHORT_SMOKE)
    assert stats.failure_reasons["verifier: required on-screen terms not observed"] == 2
    assert stats.failure_reasons["provision failed: HTTP 409"] == 1


def test_classify_outcome_maps_statuses_and_timeout():
    assert classify_outcome("completed") == OUTCOME_COMPLETED
    assert classify_outcome("failed") == OUTCOME_FAILED
    assert classify_outcome("cancelled") == OUTCOME_CANCELLED
    assert classify_outcome("running") == OUTCOME_IN_FLIGHT
    assert classify_outcome("pending") == OUTCOME_IN_FLIGHT
    assert classify_outcome("running", timed_out=True) == OUTCOME_TIMEOUT
    assert classify_outcome("completed", timed_out=True) == OUTCOME_TIMEOUT


def test_comparison_refuses_different_workloads():
    pre = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(12)],
        mode=GUI_MODE,
        cohort=COHORT_SMOKE,
    )
    post = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_FAILED) for _ in range(12)],
        mode=GUI_MODE,
        cohort=COHORT_EXTENDED,
    )
    comparison = compare_cohorts(pre, post, pre_workload="gui-smoke", post_workload="gui-extended")
    assert comparison.comparable is False
    assert "differ" in comparison.reason


def test_comparison_refuses_insufficient_samples():
    pre = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(3)], mode=GUI_MODE, cohort=COHORT_SMOKE
    )
    post = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(30)],
        mode=GUI_MODE,
        cohort=COHORT_EXTENDED,
    )
    comparison = compare_cohorts(pre, post, pre_workload="w", post_workload="w", min_samples=10)
    assert comparison.comparable is False
    assert "Insufficient" in comparison.reason


def test_comparison_allows_comparable_workloads():
    pre = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(10)], mode=GUI_MODE, cohort=COHORT_SMOKE
    )
    post = build_mode_cohort_stats(
        [_obs(GUI_MODE, OUTCOME_FAILED, cohort=COHORT_EXTENDED) for _ in range(10)],
        mode=GUI_MODE,
        cohort=COHORT_EXTENDED,
    )
    comparison = compare_cohorts(pre, post, pre_workload="w", post_workload="w", min_samples=10)
    assert comparison.comparable is True
    assert comparison.pre["success_rate"] == 1.0
    assert comparison.post["success_rate"] == 0.0


def test_smoke_gate_requires_full_samples_and_rate():
    # 10 completed per mode clears the gate.
    passing = [_obs(mode, OUTCOME_COMPLETED) for mode in (GUI_MODE, HYBRID_MODE) for _ in range(10)]
    assert evaluate_smoke_gate(passing)["passed"] is True

    # Fewer than 10 samples per mode must not pass.
    partial = [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(10)] + [
        _obs(HYBRID_MODE, OUTCOME_COMPLETED) for _ in range(4)
    ]
    assert evaluate_smoke_gate(partial)["passed"] is False

    # A failing mode must not pass even with full samples.
    failing = [_obs(GUI_MODE, OUTCOME_COMPLETED) for _ in range(10)] + [
        _obs(HYBRID_MODE, OUTCOME_FAILED) for _ in range(10)
    ]
    assert evaluate_smoke_gate(failing)["passed"] is False


def test_extract_failure_reasons_collects_notes_and_attempts():
    task = {
        "verification_notes": "Independent visual verification failed closed",
        "attempts": [
            {
                "status": "rejected",
                "error_message": "verifier: required on-screen terms not observed",
            },
            {"status": "failed", "error_message": None},
        ],
    }
    reasons = extract_failure_reasons(task)
    assert "Independent visual verification failed closed" in reasons
    assert "verifier: required on-screen terms not observed" in reasons
    assert "attempt status: failed" in reasons


def test_summarize_attempts_prefers_explicit_retry_count():
    assert summarize_attempts({"attempts": [{}, {}], "retry_count": 1}) == (2, 1)
    # Falls back to attempts-1 when retry_count is absent.
    assert summarize_attempts({"attempts": [{}, {}, {}]}) == (3, 2)
    assert summarize_attempts({"attempts": []}) == (0, 0)


def test_extract_receipt_cost_sums_gui_and_hybrid_phase_costs():
    gui_receipts = [
        {"execution_receipt": {"model": {"cost_usd": 0.0012}}},
        {"execution_receipt": {"model": {"cost_usd": 0.0008}}},
    ]
    assert extract_receipt_cost_usd(gui_receipts) == 0.002

    # Hybrid records no top-level model; its cost lives on each phase.
    hybrid_receipts = [
        {
            "execution_receipt": {
                "phases": [
                    {"model": {"cost_usd": 0.000339}},
                    {"model": {"cost_usd": 0.000664}},
                    {"model": None},
                ]
            }
        }
    ]
    assert extract_receipt_cost_usd(hybrid_receipts) == 0.001003

    # The control-plane report exposes the same figure under execution_model.
    assert extract_receipt_cost_usd([{"execution_model": {"cost_usd": 0.0005}}]) == 0.0005


def test_extract_receipt_cost_never_fabricates_missing_or_invalid_cost():
    # Absent cost is reported as 0.0 (not recorded), never invented.
    assert extract_receipt_cost_usd([{"execution_receipt": {}}]) == 0.0
    assert extract_receipt_cost_usd([{"execution_receipt": None}]) == 0.0
    assert extract_receipt_cost_usd([]) == 0.0
    # Booleans, negatives, non-numeric, and absurd values are ignored.
    assert extract_receipt_cost_usd([{"execution_model": {"cost_usd": True}}]) == 0.0
    assert extract_receipt_cost_usd([{"execution_model": {"cost_usd": -1}}]) == 0.0
    assert extract_receipt_cost_usd([{"execution_model": {"cost_usd": "free"}}]) == 0.0
    assert extract_receipt_cost_usd([{"execution_model": {"cost_usd": 10**9}}]) == 0.0


def test_objectives_embed_unique_detectable_markers_and_mode_contract():
    gui = gui_objective("RUN1-gui-001")
    # Short, uppercase, OCR-friendly marker (the verifier reads it via OCR).
    assert "NEURO12A-GUI-001" in gui
    assert "RUN1" not in gui
    assert '"gui"' in gui
    assert "not terminal" in gui
    # Constraint block prevents the live planner from decomposing the objective.
    assert "exactly ONE task" in gui

    hybrid = hybrid_objective("RUN1-hybrid-001")
    assert '"hybrid"' in hybrid
    assert "handoff.txt" in hybrid
    assert "GUI_EDIT=HYBRID_GUI_OK" in hybrid
    assert "ANCOS_HYBRID_TEST" in hybrid
    assert "Stage 3 (terminal)" in hybrid
    assert "exactly ONE task" in hybrid
