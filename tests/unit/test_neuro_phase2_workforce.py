from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from neural.cognitive_fabric.memory.models import (
    MemoryAccessLevel,
    MemoryLayer,
    MemoryRecord,
    RecordVerificationStatus,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.constants import EmployeeStatus, SkillLevel
from neural.db.repositories import (
    AuditRepository,
    EmployeeRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.employees.promotion import EmployeePromotionManager
from neural.evaluation.verifier import IndependentVerifier
from neural.evolution.progression import SkillProgressionTracker
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_dynamic_employee_creation_and_unverified_skills(async_session: AsyncSession, tmp_path):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    emp = await factory.recruit_dynamic_employee(
        role_title="Distributed Systems Engineer",
        department="Engineering",
        initial_skills=["Python", "Distributed Systems", "Raft Consensus"],
    )

    assert emp.employee_id.startswith("ANC-EMP-")
    assert emp.name is not None and len(emp.name) > 3
    assert emp.title == "Distributed Systems Engineer"
    assert emp.department == "Engineering"
    assert emp.status == EmployeeStatus.IDLE
    assert len(emp.skills) == 3

    # CRITICAL INVARIANT: Skills initially have verified=False
    for s in emp.skills:
        assert s.verified is False
        assert s.verified_at is None
        assert s.evidence_task_ids == []

    # Workspace directory ensured on host
    workspace_dir = os.path.join(str(tmp_path), emp.employee_id)
    assert os.path.exists(workspace_dir)
    assert os.path.isdir(workspace_dir)


@pytest.mark.asyncio
async def test_desk_allocation_uniqueness_and_release(async_session: AsyncSession, tmp_path):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    emp1 = await factory.hire_employee(
        name="Engineer One",
        title="Software Engineer",
        department="Engineering",
        desk_id="WS-ENG-01",
    )
    assert emp1.desk_id == "WS-ENG-01"

    # Conflicting allocation must fail closed with ValueError
    with pytest.raises(ValueError, match="already assigned"):
        await factory.hire_employee(
            name="Engineer Two",
            title="Software Engineer",
            department="Engineering",
            desk_id="WS-ENG-01",
        )

    # Release desk
    released = await repo.release_desk(emp1.employee_id)
    assert released.desk_id is None

    # Now another employee can claim WS-ENG-01
    emp2 = await factory.hire_employee(
        name="Engineer Two",
        title="Software Engineer",
        department="Engineering",
        desk_id="WS-ENG-01",
    )
    assert emp2.desk_id == "WS-ENG-01"


@pytest.mark.asyncio
async def test_empirical_skill_verification_via_independent_verifier(async_session: AsyncSession, tmp_path):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    emp = await factory.hire_employee(
        name="Elena Rostova",
        title="Backend Engineer",
        initial_skills=["FastAPI", "PostgreSQL"],
    )
    assert all(not s.verified for s in emp.skills)

    # Write simulated deliverable file
    code_path = tmp_path / "app.py"
    code_path.write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    verifier = IndependentVerifier(employee_repo=repo)
    res = await verifier.verify_task_result(
        task_id="TASK-VERIFY-01",
        employee_id=emp.employee_id,
        artifacts=[str(code_path)],
        test_exit_code=0,
        tested_skills=["FastAPI", "PostgreSQL"],
        acceptance_criteria="Automated tests pass with zero exit code",
    )

    assert res["passed"] is True
    assert res["receipt"]["signature"] is not None
    assert str(code_path) in res["receipt"]["artifact_digests"]

    # Re-fetch employee: skills certified with evidence
    updated = await repo.get_by_employee_id(emp.employee_id)
    assert updated is not None
    verified_names = {s.skill_name for s in updated.skills if s.verified}
    assert "FastAPI" in verified_names
    assert "PostgreSQL" in verified_names
    for s in updated.skills:
        if s.verified:
            assert "TASK-VERIFY-01" in s.evidence_task_ids


@pytest.mark.asyncio
async def test_skill_progression_tracker_tier_upgrades(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Marcus Vance",
        title="Software Engineer",
        initial_skills=["Python"],
    )

    tracker = SkillProgressionTracker(employee_repo=repo)
    # Upgrade to EXPERT
    upgraded = await tracker.upgrade_employee_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.EXPERT,
        evidence_id="BENCHMARK-PY-EXPERT-01",
    )

    assert upgraded["upgraded"] is True
    assert upgraded["new_level"] == SkillLevel.EXPERT.value
    assert upgraded["verified"] is True

    # Check skill matrix
    matrix_data = tracker.generate_matrix([await repo.get_by_employee_id(emp.employee_id)])
    assert matrix_data["total_employees"] == 1
    assert "Python" in matrix_data["skills_catalog"]
    emp_row = matrix_data["matrix"][0]
    assert emp_row["skills"]["Python"]["level"] == SkillLevel.EXPERT.value
    assert emp_row["skills"]["Python"]["verified"] is True


@pytest.mark.asyncio
async def test_employee_promotion_lifecycle_and_audit(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Kavya Nair",
        title="Software Engineer",
        department="Engineering",
        initial_skills=["System Design", "Python"],
    )

    promo_mgr = EmployeePromotionManager(employee_repo=repo, audit_repo=audit_repo)

    # 1. Initially ineligible with 0 completed tasks
    eligibility = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert eligibility["eligible"] is False
    assert eligibility["verified_tasks_count"] == 0

    with pytest.raises(ValueError, match="not eligible for promotion"):
        await promo_mgr.promote(
            employee_id=emp.employee_id,
            new_title="Senior Software Engineer",
            require_eligibility=True,
        )

    # 2. Simulate task completion and verified skills
    await repo.verify_skill(emp.employee_id, "Python", "TASK-EV-1")
    await repo.verify_skill(emp.employee_id, "System Design", "TASK-EV-2")
    perf = dict(emp.performance_summary or {})
    perf["verified_tasks"] = 3
    perf["verification_attempts"] = 3
    emp.performance_summary = perf
    await async_session.commit()

    # 3. Now eligible for promotion
    eligibility = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert eligibility["eligible"] is True
    assert eligibility["suggested_title"] == "Senior Software Engineer"

    # 4. Promote
    promoted = await promo_mgr.promote(
        employee_id=emp.employee_id,
        new_title="Senior Software Engineer",
        new_model_tier="reasoning",
        additional_tools=["architecture_reviewer", "code_review"],
        reason="Demonstrated deep systems architecture expertise across 3 verified tasks",
        actor="BossAI",
    )

    assert promoted.title == "Senior Software Engineer"
    assert promoted.model_config_data.get("preferred_model_tier") == "reasoning"
    assert "architecture_reviewer" in promoted.approved_tools
    assert "Senior Software Engineer" in promoted.system_prompt
    assert len(promoted.performance_summary.get("promotions", [])) == 1

    # Check audit log entry
    audit_entries = await audit_repo.list_all()
    matching_entries = [e for e in audit_entries if e.action == "PROMOTE_EMPLOYEE"]
    assert len(matching_entries) >= 1
    assert matching_entries[0].resource_id == promoted.employee_id
    assert matching_entries[0].details["new_title"] == "Senior Software Engineer"


@pytest.mark.asyncio
async def test_cross_employee_memory_access_control(async_session: AsyncSession):
    store = SixTierMemoryStore()
    access_ctrl = MemoryAccessController()

    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # Engineer A in Engineering stores internal verified deliverable memory
    rec_internal = MemoryRecord(
        layer=MemoryLayer.EPISODIC,
        category="task_receipt",
        source="task:TASK-ENG-01",
        content="Optimized Raft consensus log compaction with RocksDB snapshotting.",
        confidence=1.0,
        verification_status=RecordVerificationStatus.VERIFIED,
        verified_at=now_ts,
        access_level=MemoryAccessLevel.INTERNAL,
        owner_employee_id="ANC-EMP-ENG-01",
        metadata={"task_id": "TASK-ENG-01"},
        provenance={
            "employee_id": "ANC-EMP-ENG-01",
            "verified_by": "IndependentVerifier",
            "independent_verification_receipt": {
                "task_id": "TASK-ENG-01",
                "attempt_id": "att-1",
                "verifier_id": "IndependentVerifier",
                "passed": True,
                "evidence_refs": ["receipt:test"],
            },
        },
    )
    await store.store_record(rec_internal)

    # Engineer A stores confidential private scratchpad memory
    rec_restricted = MemoryRecord(
        layer=MemoryLayer.WORKING,
        category="scratchpad",
        source="task:TASK-ENG-01:draft",
        content="Private draft unverified notes.",
        confidence=0.5,
        verification_status=RecordVerificationStatus.UNVERIFIED,
        access_level=MemoryAccessLevel.RESTRICTED,
        owner_employee_id="ANC-EMP-ENG-01",
    )
    await store.store_record(rec_restricted)

    # QA Engineer B in Quality & Reliability queries memory
    trusted = store.query_trusted_records(
        requester_id="ANC-EMP-QA-01",
        department="Quality & Reliability",
    )
    # QA Engineer B CAN access INTERNAL verified memory from Engineer A
    assert any(r.id == rec_internal.id for r in trusted)
    # QA Engineer B CANNOT access RESTRICTED scratchpad
    assert all(r.id != rec_restricted.id for r in trusted)

    # Verify access controller blocks restricted record for Engineer B
    assert access_ctrl.can_read(rec_restricted, requester_id="ANC-EMP-QA-01") is False
    assert access_ctrl.can_read(rec_restricted, requester_id="ANC-EMP-ENG-01") is True


@pytest.mark.asyncio
async def test_employee_promotion_api_endpoints(async_session: AsyncSession):
    from httpx import ASGITransport, AsyncClient
    from neural.api.deps import get_db_session
    from neural.api.main import create_app

    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Chen Wu",
        title="Software Engineer",
        department="Engineering",
        initial_skills=["FastAPI", "Python"],
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Check initial eligibility -> ineligible
        res_elig = await client.get(f"/employees/{emp.employee_id}/promotion-eligibility")
        assert res_elig.status_code == 200
        elig_data = res_elig.json()
        assert elig_data["eligible"] is False
        assert elig_data["verified_tasks_count"] == 0

        # Attempt promote when ineligible -> 409 Conflict
        res_promo_bad = await client.post(
            f"/employees/{emp.employee_id}/promote",
            headers={"X-Actor-Identity": "owner@ancos.corp"},
            json={"new_title": "Senior Software Engineer"},
        )
        assert res_promo_bad.status_code == 409

        # Simulate verified achievements
        await repo.verify_skill(emp.employee_id, "FastAPI", "TASK-001")
        await repo.verify_skill(emp.employee_id, "Python", "TASK-001")
        perf = dict(emp.performance_summary or {})
        perf["verified_tasks"] = 2
        perf["verification_attempts"] = 2
        emp.performance_summary = perf
        await async_session.commit()

        # Check eligibility now -> eligible!
        res_elig2 = await client.get(f"/employees/{emp.employee_id}/promotion-eligibility")
        assert res_elig2.status_code == 200
        elig_data2 = res_elig2.json()
        assert elig_data2["eligible"] is True
        assert elig_data2["suggested_title"] == "Senior Software Engineer"

        # Owner promotes employee -> 200 OK
        res_promo_ok = await client.post(
            f"/employees/{emp.employee_id}/promote",
            headers={"X-Actor-Identity": "owner@ancos.corp"},
            json={
                "new_title": "Senior Software Engineer",
                "new_model_tier": "reasoning",
                "additional_tools": ["architecture_reviewer"],
                "reason": "Outstanding delivery and code quality on core API services",
            },
        )
        assert res_promo_ok.status_code == 200
        promoted_data = res_promo_ok.json()
        assert promoted_data["title"] == "Senior Software Engineer"


@pytest.mark.asyncio
async def test_career_ladder_progression_full_spectrum(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Tara Scott",
        title="Junior Software Engineer",
        initial_skills=["Python"],
    )
    await repo.verify_skill(emp.employee_id, "Python", "TASK-INIT")
    perf = dict(emp.performance_summary or {})
    perf["verified_tasks"] = 10
    perf["verification_attempts"] = 10
    emp.performance_summary = perf
    await async_session.commit()

    promo_mgr = EmployeePromotionManager(employee_repo=repo)

    # Junior -> Software Engineer
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert elig["suggested_title"] == "Software Engineer"
    p1 = await promo_mgr.promote(emp.employee_id, elig["suggested_title"], require_eligibility=False)
    assert p1.title == "Software Engineer"

    # Software Engineer -> Senior Software Engineer
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert elig["suggested_title"] == "Senior Software Engineer"
    p2 = await promo_mgr.promote(emp.employee_id, elig["suggested_title"], require_eligibility=False)
    assert p2.title == "Senior Software Engineer"

    # Senior Software Engineer -> Lead Software Engineer
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert elig["suggested_title"] == "Lead Software Engineer"
    p3 = await promo_mgr.promote(emp.employee_id, elig["suggested_title"], require_eligibility=False)
    assert p3.title == "Lead Software Engineer"

    # Lead Software Engineer -> Principal Software Engineer
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert elig["suggested_title"] == "Principal Software Engineer"
    p4 = await promo_mgr.promote(emp.employee_id, elig["suggested_title"], require_eligibility=False)
    assert p4.title == "Principal Software Engineer"

    # Principal Software Engineer -> Distinguished Software Engineer
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id)
    assert elig["suggested_title"] == "Distinguished Software Engineer"
    p5 = await promo_mgr.promote(emp.employee_id, elig["suggested_title"], require_eligibility=False)
    assert p5.title == "Distinguished Software Engineer"


@pytest.mark.asyncio
async def test_promotion_rejected_without_new_verified_tasks(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Alex Mercer",
        title="Software Engineer",
        initial_skills=["Python"],
    )
    await repo.verify_skill(emp.employee_id, "Python", "TASK-INIT")
    perf = dict(emp.performance_summary or {})
    perf["verified_tasks"] = 2
    perf["verification_attempts"] = 2
    emp.performance_summary = perf
    await async_session.commit()

    promo_mgr = EmployeePromotionManager(employee_repo=repo)
    # First promotion succeeds
    elig = await promo_mgr.evaluate_eligibility(emp.employee_id, min_verified_tasks=2)
    assert elig["eligible"] is True
    promoted = await promo_mgr.promote(emp.employee_id, "Senior Software Engineer", require_eligibility=True, min_verified_tasks=2)
    assert promoted.title == "Senior Software Engineer"

    # Immediate second promotion fails because 0 tasks completed since last promotion
    elig_second = await promo_mgr.evaluate_eligibility(emp.employee_id, min_verified_tasks=1)
    assert elig_second["eligible"] is False
    assert elig_second["tasks_since_last_promotion"] == 0

    with pytest.raises(ValueError, match="not eligible for promotion"):
        await promo_mgr.promote(emp.employee_id, "Lead Software Engineer", require_eligibility=True)


@pytest.mark.asyncio
async def test_concurrent_promotion_requests_serialized_safely(async_session: AsyncSession):
    import asyncio
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Rohan Joshi",
        title="Software Engineer",
        initial_skills=["Python"],
    )
    await repo.verify_skill(emp.employee_id, "Python", "TASK-01")
    perf = dict(emp.performance_summary or {})
    perf["verified_tasks"] = 2
    perf["verification_attempts"] = 2
    emp.performance_summary = perf
    await async_session.commit()

    promo_mgr = EmployeePromotionManager(employee_repo=repo)

    async def try_promote():
        return await promo_mgr.promote(
            emp.employee_id,
            "Senior Software Engineer",
            require_eligibility=True,
            min_verified_tasks=1,
        )

    # 4 concurrent requests to promote to Senior Software Engineer
    results = await asyncio.gather(
        try_promote(),
        try_promote(),
        try_promote(),
        try_promote(),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Exactly one succeeded and the other 3 failed
    assert len(successes) == 1
    assert len(failures) == 3
    for exc in failures:
        assert isinstance(exc, ValueError)

    refreshed = await repo.get_by_employee_id(emp.employee_id)
    assert refreshed.title == "Senior Software Engineer"
    promotions = refreshed.performance_summary.get("promotions", [])
    assert len(promotions) == 1


@pytest.mark.asyncio
async def test_case_insensitive_skill_verification(async_session: AsyncSession, tmp_path):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    emp = await factory.hire_employee(
        name="Morgan Gray",
        title="Backend Engineer",
        initial_skills=["Python"],
    )
    assert len(emp.skills) == 1
    assert emp.skills[0].skill_name == "Python"
    assert emp.skills[0].verified is False

    # Verify skill with lowercase "python"
    await repo.verify_skill(emp.employee_id, "python", "TASK-EV-1")

    refreshed = await repo.get_by_employee_id(emp.employee_id)
    assert len(refreshed.skills) == 1
    assert refreshed.skills[0].verified is True
    assert "TASK-EV-1" in refreshed.skills[0].evidence_task_ids

    # Verify skill again with UPPERCASE "PYTHON"
    await repo.verify_skill(emp.employee_id, "PYTHON", "TASK-EV-2")
    refreshed2 = await repo.get_by_employee_id(emp.employee_id)
    # Still only 1 skill row, not 2 or 3
    assert len(refreshed2.skills) == 1
    assert "TASK-EV-2" in refreshed2.skills[0].evidence_task_ids


@pytest.mark.asyncio
async def test_verifier_blocks_deleted_or_tampered_artifacts(async_session: AsyncSession, tmp_path):
    from neural.db.repositories import TaskRepository
    from neural.evaluation.verifier import IndependentDeliverableVerifier, UnverifiedTaskError

    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(
        name="Jordan Reed",
        title="Systems Engineer",
        initial_skills=["C++"],
    )

    art_file = tmp_path / "solution.cpp"
    art_file.write_text("int main() { return 0; }", encoding="utf-8")

    verifier = IndependentDeliverableVerifier(employee_repo=emp_repo, task_repo=task_repo)
    v_res = await verifier.verify_task_result(
        task_id="TASK-TAMPER-01",
        employee_id=emp.employee_id,
        artifacts=[str(art_file)],
        test_exit_code=0,
        tested_skills=["C++"],
        acceptance_criteria="Pass tests",
    )
    receipt = v_res["receipt"]

    # Tamper with artifact after verification
    art_file.write_text("int main() { return 1; /* TAMPERED */ }", encoding="utf-8")

    with pytest.raises(UnverifiedTaskError, match="digest mismatch"):
        await verifier.gate_task_completion("TASK-TAMPER-01", receipt)

    # Now delete artifact
    art_file.unlink()
    with pytest.raises(UnverifiedTaskError, match="missing from disk"):
        await verifier.gate_task_completion("TASK-TAMPER-01", receipt)

