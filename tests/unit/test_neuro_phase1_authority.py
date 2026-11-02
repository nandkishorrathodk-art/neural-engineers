import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.boss.brain import BossBrain
from neural.db.repositories import AuditRepository, EmployeeRepository
from neural.employees.factory import EmployeeFactory


@pytest.mark.asyncio
async def test_production_owner_identity_rejects_worker_credentials(monkeypatch):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()

    assert await require_owner_identity(f"Bearer {owner_token}") == "human-owner"
    with pytest.raises(HTTPException) as rejected:
        await require_owner_identity(f"Bearer {worker_token}")
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_boss_staffing_requires_verified_skills(async_session):
    employee_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(employee_repo)
    employee = await factory.hire_employee(
        name="Evidence Test Engineer",
        title="Backend Engineer",
        initial_skills=["Python"],
    )
    plan = {
        "phases": [
            {
                "tasks": [
                    {
                        "role_required": "backend-engineer",
                        "skills_required": ["Python"],
                    }
                ]
            }
        ]
    }

    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {
        "backend-engineer": ["Python"]
    }

    employee.skills[0].verified = True
    await async_session.commit()
    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {}


@pytest.mark.asyncio
async def test_production_objective_is_owner_authenticated_and_audited(
    async_session,
    monkeypatch,
):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {"objective": "Create an evidence-backed API service"}
        assert (await client.post("/boss/objective", json=payload)).status_code == 401
        assert (
            await client.post(
                "/boss/objective",
                json=payload,
                headers={"Authorization": f"Bearer {worker_token}"},
            )
        ).status_code == 401

        accepted = await client.post(
            "/boss/objective",
            json=payload,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert accepted.status_code == 200

    audit_records = await AuditRepository(async_session).list_all()
    owner_events = [record for record in audit_records if record.action == "SUBMIT_OBJECTIVE"]
    staffing_events = [record for record in audit_records if record.action == "STAFFING_DECISION"]
    assert owner_events and owner_events[0].actor == "human-owner"
    assert staffing_events and staffing_events[0].actor == "BossAI"
    assert staffing_events[0].details["recruited_employee_ids"]
