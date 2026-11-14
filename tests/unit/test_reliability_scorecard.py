"""Unit tests for the sustained-workload reliability scorecard.

The scorecard must never inflate reliability: in-flight tasks must not count
toward a success rate, and human-relative speed must read NOT_MEASURED unless a
real baseline ratio is supplied.
"""

from __future__ import annotations

from neural.reliability.scorecard import (
    HUMAN_SPEED_NOT_MEASURED,
    ModeReliability,
    build_scorecard,
)


def test_success_rate_uses_decided_tasks_only():
    rows = [
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "failed"},
        # In-flight work must not count for or against the rate.
        {"execution_mode": "terminal", "status": "pending"},
        {"execution_mode": "terminal", "status": "running"},
    ]
    card = build_scorecard(rows)
    terminal = card.per_mode["terminal"]
    assert terminal.decided == 3
    assert terminal.in_flight == 2
    assert terminal.success_rate == round(2 / 3, 4)
    assert card.overall_success_rate == round(2 / 3, 4)


def test_per_mode_breakdown_separates_gui_and_terminal():
    rows = [
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "failed"},
        {"execution_mode": "gui", "status": "completed"},
        {"execution_mode": "gui", "status": "failed"},
        {"execution_mode": "hybrid", "status": "failed"},
    ]
    card = build_scorecard(rows)
    assert card.per_mode["terminal"].success_rate == 0.75
    assert card.per_mode["gui"].success_rate == 0.5
    assert card.per_mode["hybrid"].success_rate == 0.0
    # GUI-bearing = gui + hybrid = 1 completed / 3 decided.
    assert card.gui_bearing_success_rate() == round(1 / 3, 4)


def test_gui_bearing_rate_is_none_when_no_gui_tasks_seen():
    card = build_scorecard([{"execution_mode": "terminal", "status": "completed"}])
    assert card.gui_bearing_success_rate() is None


def test_cancelled_counts_as_decided_and_drags_rate():
    rows = [
        {"execution_mode": "terminal", "status": "completed"},
        {"execution_mode": "terminal", "status": "cancelled"},
    ]
    card = build_scorecard(rows)
    terminal = card.per_mode["terminal"]
    assert terminal.cancelled == 1
    assert terminal.decided == 2
    assert terminal.success_rate == 0.5


def test_dominant_failure_mode_is_the_most_common_reason():
    attempts = [
        {"status": "rejected", "error_message": "the live desktop could not be observed"},
        {"status": "rejected", "error_message": "the live desktop could not be observed"},
        {"status": "rejected", "error_message": "the live desktop could not be observed"},
        {"status": "failed", "error_message": "terminal command exited with code 1"},
        {"status": "verified", "error_message": None},
    ]
    card = build_scorecard([], attempts)
    assert card.dominant_failure_mode() == "verifier: live desktop not observed"
    assert card.attempt_status_counts == {"rejected": 3, "failed": 1, "verified": 1}


def test_on_screen_terms_classified_more_specifically_than_live_desktop():
    # The full rejection message contains both markers; the specific one wins.
    message = (
        "Independent visual verification failed closed: the live desktop could not "
        "be observed, the independently captured frame did not match the worker's "
        "post-action frame, the required on-screen deliverable terms were not read "
        "by the verifier, or a hybrid terminal phase was not independently replayable."
    )
    card = build_scorecard([], [{"status": "rejected", "error_message": message}])
    assert card.dominant_failure_mode() == "verifier: required on-screen terms not observed"


def test_human_speed_is_not_measured_without_a_baseline():
    card = build_scorecard([{"execution_mode": "terminal", "status": "completed"}])
    assert card.human_speed_status == HUMAN_SPEED_NOT_MEASURED
    assert card.human_speed_ratio is None
    assert card.to_dict()["human_speed"] == {"status": HUMAN_SPEED_NOT_MEASURED, "ratio": None}


def test_human_speed_measured_only_when_ratio_supplied():
    card = build_scorecard(
        [{"execution_mode": "terminal", "status": "completed"}], human_speed_ratio=4.2
    )
    assert card.human_speed_status == "MEASURED"
    assert card.human_speed_ratio == 4.2


def test_empty_input_yields_zero_rate_not_a_division_error():
    card = build_scorecard([])
    assert card.overall_success_rate == 0.0
    assert card.total_decided == 0
    assert card.dominant_failure_mode() is None


def test_enum_inputs_are_accepted_like_strings():
    from neural.constants import ExecutionMode, TaskStatus

    card = build_scorecard(
        [{"execution_mode": ExecutionMode.HYBRID, "status": TaskStatus.COMPLETED}]
    )
    assert card.per_mode["hybrid"].completed == 1


def test_mode_reliability_dict_shape():
    m = ModeReliability(mode="gui", completed=1, failed=2)
    assert m.to_dict() == {
        "mode": "gui",
        "completed": 1,
        "failed": 2,
        "cancelled": 0,
        "in_flight": 0,
        "decided": 3,
        "success_rate": round(1 / 3, 4),
    }


def test_markdown_report_flags_unmeasured_speed():
    card = build_scorecard(
        [
            {"execution_mode": "terminal", "status": "completed"},
            {"execution_mode": "gui", "status": "failed"},
        ]
    )
    text = card.to_markdown()
    assert "terminal" in text
    assert "gui" in text
    assert "NOT_MEASURED" in text
