import pytest
from neural.db.repositories import AuditRepository
from neural.exceptions import AuditIntegrityError
from neural.observability.audit import AuditLogChainVerifier
from neural.observability.metrics import PrometheusExporter
from neural.observability.telemetry import SystemTelemetryCollector
from sqlalchemy.ext.asyncio import AsyncSession


def test_system_telemetry_collector_gauges():
    """Validates real-time telemetry extraction for dashboard gauges."""
    telemetry = SystemTelemetryCollector.collect_all_telemetry()

    assert "cpu" in telemetry
    assert "memory" in telemetry
    assert "disk" in telemetry
    assert "network" in telemetry
    assert "workstations" in telemetry
    assert "kill_switch" in telemetry
    assert "timestamp" in telemetry

    # Value ranges
    assert 0.0 <= telemetry["cpu"]["percent"] <= 100.0
    assert telemetry["cpu"]["cores"] >= 1
    assert telemetry["memory"]["total_mb"] > 0
    assert 0.0 <= telemetry["memory"]["percent"] <= 100.0
    assert telemetry["disk"]["total_gb"] > 0
    assert telemetry["network"]["bytes_sent"] >= 0


@pytest.mark.asyncio
async def test_prometheus_exporter_format(async_session: AsyncSession):
    """Validates standard Prometheus text exposition format and exported metric keys."""
    metrics_text = await PrometheusExporter.collect_metrics_text(async_session)

    assert "# HELP ancos_emergency_kill_switch_active" in metrics_text
    assert "# TYPE ancos_emergency_kill_switch_active gauge" in metrics_text
    assert "ancos_emergency_kill_switch_active" in metrics_text

    assert "# HELP ancos_system_cpu_usage_ratio" in metrics_text
    assert "ancos_system_cpu_usage_ratio" in metrics_text

    assert "# HELP ancos_system_memory_usage_ratio" in metrics_text
    assert "ancos_system_memory_usage_ratio" in metrics_text

    assert "# HELP ancos_tasks_total" in metrics_text
    assert 'ancos_tasks_total{status="pending"}' in metrics_text
    assert 'ancos_tasks_total{status="completed"}' in metrics_text

    assert "ancos_active_containers_count" in metrics_text
    assert "ancos_pending_approvals_count" in metrics_text
    assert "ancos_audit_records_total" in metrics_text


@pytest.mark.asyncio
async def test_audit_log_cryptographic_hash_chain_verification(async_session: AsyncSession):
    """
    Validates tamper-evident audit logging:
    - Automatically computes SHA-256 request_hash and signature.
    - Chains each entry to the preceding entry's signature via previous_hash.
    - Validates unbroken chain across multiple recorded events.
    """
    repo = AuditRepository(async_session)

    e1 = await repo.log(
        actor="operator_alice",
        action="PROJECT_CREATED",
        resource_type="project",
        resource_id="PROJ-01",
        details={"name": "Project Alpha"},
        payload={"tier": "production"},
    )
    assert e1.previous_hash == "GENESIS_AUDIT_BLOCK"
    assert len(e1.signature) == 64
    assert len(e1.request_hash) == 64

    e2 = await repo.log(
        actor="ai_worker_bob",
        action="TASK_STARTED",
        resource_type="task",
        resource_id="TASK-01",
        details={"title": "Setup DB"},
        payload={"framework": "fastapi"},
    )
    assert e2.previous_hash == e1.signature
    assert len(e2.signature) == 64

    e3 = await repo.log(
        actor="qa_verifier",
        action="TASK_VERIFIED",
        resource_type="task",
        resource_id="TASK-01",
        details={"verdict": "PASSED"},
        payload={"exit_code": 0},
    )
    assert e3.previous_hash == e2.signature

    # Verify chain integrity via repository
    is_valid, issues = await repo.verify_chain_integrity()
    assert is_valid is True
    assert len(issues) == 0

    # Also verify via AuditLogChainVerifier
    records = await repo.list_all(limit=10)
    sorted_recs = sorted(records, key=lambda r: r.timestamp)
    valid_chain, chain_issues = AuditLogChainVerifier.verify_records(sorted_recs)
    assert valid_chain is True
    assert len(chain_issues) == 0


@pytest.mark.asyncio
async def test_audit_log_detects_tampered_records(async_session: AsyncSession):
    """
    Validates that any malicious alteration of actor, action, or previous_hash
    is immediately detected by cryptographic audit verification.
    """
    repo = AuditRepository(async_session)

    e1 = await repo.log(
        actor="operator",
        action="ACTION_1",
        resource_type="type1",
        resource_id="id1",
        details={},
    )
    e2 = await repo.log(
        actor="worker",
        action="ACTION_2",
        resource_type="type2",
        resource_id="id2",
        details={},
    )

    records = [e1, e2]

    # Clean records pass
    valid, issues = AuditLogChainVerifier.verify_records(records)
    assert valid is True

    # Tamper with e1 actor
    e1.actor = "malicious_hacker"
    valid_tampered, tamper_issues = AuditLogChainVerifier.verify_records(records)
    assert valid_tampered is False
    assert len(tamper_issues) > 0
    assert any("mismatch" in issue.lower() for issue in tamper_issues)

    with pytest.raises(AuditIntegrityError):
        AuditLogChainVerifier.enforce_integrity(records)


def test_audit_log_order_chain_traversal():
    """Validates that order_chain reconstructs correct linear order even when records are shuffled or have identical timestamps."""
    from neural.db.models import AuditLog

    # Create dummy chain: block 0 (genesis) -> block 1 -> block 2
    b0 = AuditLog(
        id="b0",
        actor="a0",
        action="act0",
        resource_type="rt0",
        resource_id="rid0",
        request_hash="h0",
        previous_hash="GENESIS_AUDIT_BLOCK",
    )
    b0.signature = AuditLogChainVerifier.compute_signature(b0)

    b1 = AuditLog(
        id="b1",
        actor="a1",
        action="act1",
        resource_type="rt1",
        resource_id="rid1",
        request_hash="h1",
        previous_hash=b0.signature,
    )
    b1.signature = AuditLogChainVerifier.compute_signature(b1)

    b2 = AuditLog(
        id="b2",
        actor="a2",
        action="act2",
        resource_type="rt2",
        resource_id="rid2",
        request_hash="h2",
        previous_hash=b1.signature,
    )
    b2.signature = AuditLogChainVerifier.compute_signature(b2)

    # Shuffled input
    shuffled = [b2, b0, b1]
    ordered = AuditLogChainVerifier.order_chain(shuffled)
    assert [r.id for r in ordered] == ["b0", "b1", "b2"]

    valid, issues = AuditLogChainVerifier.verify_records(ordered)
    assert valid is True
    assert len(issues) == 0
