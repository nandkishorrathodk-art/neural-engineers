import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import ApprovalStatus, DestructiveOperationType
from neural.db.repositories import ApprovalRepository
from neural.security.kill_switch import reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def reset_system_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_phase6_health_and_observability_endpoints(async_session: AsyncSession):
    """
    Validates Phase 6 health probes, Prometheus exporter, and telemetry feeds:
    - /health/live
    - /health/ready (returns 200 when nominal, 503 when halted)
    - /metrics (Prometheus exposition format)
    - /observability/telemetry (CPU, memory, disk, network dashboard gauges)
    - /observability/audit-logs and /observability/audit-logs/verify
    """
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Liveness probe
        live_res = await client.get("/health/live")
        assert live_res.status_code == 200
        assert live_res.json()["status"] == "alive"

        # 2. Readiness probe (Nominal)
        ready_res = await client.get("/health/ready")
        assert ready_res.status_code == 200
        assert ready_res.json()["status"] == "ready"
        assert ready_res.json()["kill_switch_active"] is False

        # 3. Prometheus metrics endpoint
        metrics_res = await client.get("/metrics")
        assert metrics_res.status_code == 200
        assert "text/plain" in metrics_res.headers["content-type"]
        text_body = metrics_res.text
        assert "ancos_emergency_kill_switch_active" in text_body
        assert "ancos_tasks_total" in text_body
        assert "ancos_system_cpu_usage_ratio" in text_body

        # 4. Telemetry endpoint
        telem_res = await client.get("/observability/telemetry")
        assert telem_res.status_code == 200
        telem_data = telem_res.json()
        assert "cpu" in telem_data
        assert "memory" in telem_data
        assert "disk" in telem_data
        assert "network" in telem_data
        assert "kill_switch" in telem_data

        # 5. Audit logs endpoint
        audit_res = await client.get("/observability/audit-logs")
        assert audit_res.status_code == 200
        assert isinstance(audit_res.json(), list)

        # 6. Audit logs cryptographic chain verification
        verify_res = await client.get("/observability/audit-logs/verify")
        assert verify_res.status_code == 200
        assert verify_res.json()["valid"] is True


@pytest.mark.asyncio
async def test_phase6_security_emergency_stop_and_approval_endpoints(async_session: AsyncSession):
    """
    Validates Zero-Trust security and control room endpoints:
    - /security/status
    - /security/emergency-stop (instant halt < 200ms)
    - /health/ready transitions to 503 under emergency stop
    - /security/resume (recovers state)
    - /security/approvals and /security/approvals/{id}/decide
    """
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Security status
        status_res = await client.get("/security/status")
        assert status_res.status_code == 200
        assert status_res.json()["kill_switch_active"] is False
        assert status_res.json()["network_perimeter"] == "active"

        # 2. Trigger Emergency Stop
        stop_res = await client.post(
            "/security/emergency-stop",
            json={"reason": "Suspicious syscall attempt detected", "actor": "security_agent"},
        )
        assert stop_res.status_code == 200
        receipt = stop_res.json()
        assert receipt["halted"] is True
        assert receipt["halt_latency_ms"] < 200.0
        assert receipt["status"] == "HALTED"

        # 3. Verify readiness probe fails with 503 while halted
        halted_ready = await client.get("/health/ready")
        assert halted_ready.status_code == 503
        assert halted_ready.json()["kill_switch_active"] is True

        # 4. Resume system
        resume_res = await client.post(
            "/security/resume",
            json={"actor": "operator_admin", "recovery_strategy": "checkpoint"},
        )
        assert resume_res.status_code == 200
        assert resume_res.json()["resumed"] is True

        # 5. Verify readiness probe returns to 200 OK
        resumed_ready = await client.get("/health/ready")
        assert resumed_ready.status_code == 200
        assert resumed_ready.json()["kill_switch_active"] is False

        # 6. Create a pending human approval request in DB
        app_repo = ApprovalRepository(async_session)
        created_app = await app_repo.create(
            action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
            description="Deploy payment microservice v2",
            justification="Passed all integration gates",
            requested_by="release_bot",
            details={"env": "production"},
            status=ApprovalStatus.PENDING,
        )

        # 7. List pending approvals
        list_res = await client.get("/security/approvals")
        assert list_res.status_code == 200
        approvals = list_res.json()
        assert len(approvals) >= 1
        assert any(a["id"] == created_app.id for a in approvals)

        # 8. Operator approves
        decide_res = await client.post(
            f"/security/approvals/{created_app.id}/decide",
            json={"decision": "approved", "decided_by": "human_approver"},
        )
        assert decide_res.status_code == 200
        assert decide_res.json()["status"] == "approved"
        assert decide_res.json()["decided_by"] == "human_approver"
