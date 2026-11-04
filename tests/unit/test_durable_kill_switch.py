from datetime import datetime

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, require_execution_not_halted
from neural.api.main import create_app
from neural.config import get_settings
from neural.db.repositories import EmergencyStopRepository
from neural.exceptions import EmergencyStopTriggered
from neural.security.kill_switch import GlobalEmergencyKillSwitch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_halt_state_is_shared_across_repositories_and_fresh_switch_instances(async_session):
    first_repo = EmergencyStopRepository(async_session)
    first_instance = GlobalEmergencyKillSwitch()

    receipt = await first_instance.emergency_halt(
        reason="Contain a validation incident",
        actor="security-owner",
        state_repo=first_repo,
    )
    assert receipt["halted"] is True

    async with AsyncSession(async_session.bind, expire_on_commit=False) as restarted_session:
        second_repo = EmergencyStopRepository(restarted_session)
        recreated_instance = GlobalEmergencyKillSwitch()
        persisted = await second_repo.get_state()
        recreated_instance.sync_state(persisted)

        assert persisted["active"] is True
        assert persisted["reason"] == "Contain a validation incident"
        assert persisted["changed_by"] == "security-owner"
        assert isinstance(persisted["changed_at"], datetime)
        assert recreated_instance.is_halted is True
        with pytest.raises(EmergencyStopTriggered):
            recreated_instance.guard_active()

        resume = await recreated_instance.resume(actor="security-owner", state_repo=second_repo)
        assert resume["resumed"] is True
        recreated_instance.guard_active()
    async with AsyncSession(async_session.bind, expire_on_commit=False) as recovered_session:
        assert (await EmergencyStopRepository(recovered_session).get_state())["active"] is False


@pytest.mark.asyncio
async def test_worker_route_reads_shared_halt_state_before_claim(async_session, monkeypatch):
    worker_token = "durable-halt-worker-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("WORKER_API_TOKENS", "{}")
    monkeypatch.setenv("OWNER_API_TOKEN", "")
    monkeypatch.setenv("VERIFIER_API_TOKEN", "")
    get_settings.cache_clear()

    await EmergencyStopRepository(async_session).set_state(
        active=True,
        actor="owner",
        reason="Persisted global halt",
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/internal/worker/tasks/claim",
            headers={"Authorization": f"Bearer {worker_token}"},
            json={},
        )
    get_settings.cache_clear()

    assert response.status_code == 503
    assert "Emergency stop is active" in response.json()["detail"]


@pytest.mark.asyncio
async def test_worker_guard_fails_closed_when_shared_state_backend_is_unavailable():
    class UnavailableSession:
        async def get(self, _model, _key):
            raise RuntimeError("database unavailable")

    with pytest.raises(HTTPException) as error:
        await require_execution_not_halted(session=UnavailableSession())

    assert error.value.status_code == 503
    assert "execution is blocked" in error.value.detail
