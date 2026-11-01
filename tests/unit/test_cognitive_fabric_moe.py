from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from neural.cognitive_fabric.organizational_moe.capability_matcher import CapabilityMatcher
from neural.cognitive_fabric.organizational_moe.expert_router import OrganizationalMoERouter
from neural.cognitive_fabric.organizational_moe.recruitment_strategy import RecruitmentStrategy
from neural.cognitive_fabric.organizational_moe.workload_selector import WorkloadSelector
from neural.constants import EmployeeStatus, SkillLevel, TaskPriority
from neural.db.models import Employee, EmployeeSkill, Task


def make_test_employee(
    emp_id: str,
    name: str,
    skills: list[tuple[str, bool]],  # (name, verified)
    quality_score: float = 1.0,
    status: EmployeeStatus = EmployeeStatus.IDLE,
) -> Employee:
    emp = Employee(
        employee_id=emp_id,
        name=name,
        title="Software Engineer",
        department="Engineering",
        personality="Methodical",
        status=status,
        performance_summary={"quality_score": quality_score, "success_rate": 1.0, "completed_tasks": 10},
    )
    emp.skills = [
        EmployeeSkill(
            id=f"sk-{i}",
            employee_id=emp_id,
            skill_name=s_name,
            level=SkillLevel.EXPERT,
            verified=is_v,
        )
        for i, (s_name, is_v) in enumerate(skills)
    ]
    return emp


def test_capability_matcher_verified_vs_unverified_skills():
    matcher = CapabilityMatcher()

    # Employee 1: Verified Rust skill
    emp_verified = make_test_employee(
        "EMP-001", "Rust Expert", [("rust", True), ("docker", True)], quality_score=1.0
    )

    # Employee 2: Unverified Rust skill
    emp_unverified = make_test_employee(
        "EMP-002", "Rust Hobbyist", [("rust", False), ("docker", False)], quality_score=1.0
    )

    fit_verified = matcher.evaluate_employee_fit(emp_verified, ["rust", "docker"])
    fit_unverified = matcher.evaluate_employee_fit(emp_unverified, ["rust", "docker"])

    assert fit_verified["verified_matches"] == 2
    assert fit_verified["has_verified_match"] is True
    assert fit_unverified["verified_matches"] == 0
    assert fit_unverified["unverified_matches"] == 2

    # Verified skills must yield a significantly higher composite score than unverified claims
    assert fit_verified["composite_score"] > fit_unverified["composite_score"] * 2.0


def test_workload_selector_penalties_and_gui_constraints():
    selector = WorkloadSelector()
    emp_busy = make_test_employee("EMP-003", "Overloaded Worker", [("python", True)], status=EmployeeStatus.WORKING)
    emp_idle = make_test_employee("EMP-004", "Idle Worker", [("python", True)], status=EmployeeStatus.IDLE)

    base_fit = {"composite_score": 50.0, "employee_id": "test"}

    # Busy employee with 4 tasks backlog should be penalized
    adj_busy = selector.adjust_score_for_workload(
        base_fit, emp_busy, active_task_count=4, requires_gui=False
    )
    # Idle employee should receive availability boost
    adj_idle = selector.adjust_score_for_workload(
        base_fit, emp_idle, active_task_count=0, requires_gui=False
    )

    assert adj_busy["adjusted_score"] < base_fit["composite_score"]
    assert adj_idle["adjusted_score"] > base_fit["composite_score"]

    # GUI required but no desktop slot available -> score heavily penalized
    adj_gui_blocked = selector.adjust_score_for_workload(
        base_fit, emp_idle, requires_gui=True, desktop_slot_available=False
    )
    assert adj_gui_blocked["adjusted_score"] < base_fit["composite_score"] * 0.5


def test_recruitment_strategy_gap_detection_and_profile_generation():
    recruiter = RecruitmentStrategy()

    # When candidate has no matching skills and composite score is low
    poor_candidates = [
        {"composite_score": 10.0, "verified_matches": 0, "unverified_matches": 0}
    ]
    assert recruiter.needs_recruitment(poor_candidates, ["quantum_cryptography"]) is True

    # When candidate has a matching skill
    good_candidates = [
        {"composite_score": 45.0, "verified_matches": 1, "unverified_matches": 0}
    ]
    assert recruiter.needs_recruitment(good_candidates, ["python"]) is False

    # Design recruitment profile
    profile = recruiter.design_recruitment_profile(["solidity", "smart_contracts"], department="Security")
    assert "Solidity" in profile["name"]
    assert profile["department"] == "Security"
    assert "solidity" in profile["required_skills"]
    assert "system_prompt" in profile


@pytest.mark.asyncio
async def test_organizational_moe_routing_and_team_assembly():
    router = OrganizationalMoERouter()

    alice = make_test_employee("EMP-ALICE", "Alice Backend", [("fastapi", True), ("sqlite", True)])
    bob = make_test_employee("EMP-BOB", "Bob Frontend", [("react", True), ("nextjs", True)])
    charlie = make_test_employee("EMP-CHARLIE", "Charlie QA", [("pytest", True), ("security", True)])

    task = Task(
        task_id="TASK-ROUTING-1",
        title="Implement High-Throughput SQLite Backend API",
        priority=TaskPriority.HIGH,
        required_capabilities=["fastapi", "sqlite"],
    )

    result = await router.route_task(
        task=task,
        available_employees=[alice, bob, charlie],
        active_task_counts={"EMP-ALICE": 0, "EMP-BOB": 0, "EMP-CHARLIE": 0},
    )

    assert result["selected_employee_id"] == "EMP-ALICE"
    assert result["selected_employee"].name == "Alice Backend"
    # Because task priority is HIGH, a multi-stage team is assembled (primary + secondary)
    assert len(result["team"]) == 2
    assert result["team"][0].employee_id == "EMP-ALICE"
    assert result["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_organizational_moe_dynamic_recruitment_on_capability_gap():
    # Mock employee factory
    mock_factory = MagicMock()
    mock_hired = make_test_employee(
        "EMP-NEW-01", "Specialist-Blockchain", [("blockchain", True)], quality_score=1.0
    )
    mock_factory.hire_employee = AsyncMock(return_value=mock_hired)

    router = OrganizationalMoERouter(employee_factory=mock_factory)

    # Current workforce lacks 'blockchain' skill
    dave = make_test_employee("EMP-DAVE", "Dave Python", [("python", True)])

    task = Task(
        task_id="TASK-GAP-1",
        title="Audit zero-knowledge proof contracts",
        priority=TaskPriority.MEDIUM,
        required_capabilities=["blockchain"],
    )

    result = await router.route_task(
        task=task,
        available_employees=[dave],
        allow_recruitment=True,
    )

    assert result["recruited_new_specialist"] is True
    assert result["selected_employee_id"] == "EMP-NEW-01"
    mock_factory.hire_employee.assert_awaited_once()


def test_capability_matcher_verified_candidate_always_beats_unverified_claims():
    """Mandate: Unverified skills must NEVER be treated as established expertise.
    A candidate with 1 verified skill must beat a candidate claiming 10 unverified skills.
    """
    matcher = CapabilityMatcher()

    # Candidate with 1 verified skill
    emp_verified = make_test_employee(
        "EMP-VER", "Verified Eng", [("c_plus_plus", True)], quality_score=1.0
    )

    # Candidate with 10 unverified skills
    emp_claims = make_test_employee(
        "EMP-CLAIM", "Claimant Eng", [(f"skill_{i}", False) for i in range(10)] + [("c_plus_plus", False)], quality_score=1.0
    )

    fit_v = matcher.evaluate_employee_fit(emp_verified, ["c_plus_plus"])
    fit_c = matcher.evaluate_employee_fit(emp_claims, ["c_plus_plus"])

    assert fit_v["has_verified_match"] is True
    assert fit_c["has_verified_match"] is False
    assert fit_v["composite_score"] > fit_c["composite_score"]

