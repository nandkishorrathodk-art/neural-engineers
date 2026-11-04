import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.db.repositories import EmergencyStopRepository
from neural.security.kill_switch import get_kill_switch, reset_kill_switch


@pytest.fixture(autouse=True)
def reset_local_kill_switch():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_readiness_reads_durable_halt_from_another_api_replica(async_session):
    """A replica with a clear local latch must observe another replica's persisted halt."""
    await EmergencyStopRepository(async_session).set_state(
        active=True,
        actor="remote-owner",
        reason="incident response",
    )
    assert get_kill_switch().is_halted is False

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database_connected": True,
        "kill_switch_active": True,
        "emergency_stop_state_available": True,
        "reason": "Emergency halt active",
    }
    assert get_kill_switch().is_halted is True


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_durable_halt_state_cannot_be_read(async_session, monkeypatch):
    """Database SELECT 1 alone is insufficient if safety state cannot be confirmed."""

    async def fail_read(self):
        raise RuntimeError("state table unavailable")

    monkeypatch.setattr(EmergencyStopRepository, "get_state", fail_read)
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database_connected": True,
        "kill_switch_active": False,
        "emergency_stop_state_available": False,
        "reason": "Emergency-stop state unavailable",
    }


@pytest.mark.asyncio
async def test_readiness_reports_ready_only_after_durable_safety_state_is_read(async_session):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database_connected": True,
        "kill_switch_active": False,
        "emergency_stop_state_available": True,
    }
