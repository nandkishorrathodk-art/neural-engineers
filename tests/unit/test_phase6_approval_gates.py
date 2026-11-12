import pytest
from neural.constants import ApprovalStatus, DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.exceptions import HumanApprovalRequiredError
from neural.security.approval_gates import HumanApprovalGate
from sqlalchemy.ext.asyncio import AsyncSession


def test_human_approval_gate_identifies_destructive_operations():
    """Validates classification of high-risk destructive operations requiring human gates."""
    # Enum types
    for op in DestructiveOperationType:
        assert HumanApprovalGate.is_destructive(op.value) is True

    # Keyword variants
    destructive_actions = [
        "DROP_TABLE_USERS",
        "rm -rf /data/workspace",
        "deploy_production_v2",
        "rotate_jwt_secret_key",
        "destroy_cluster_nodes",
    ]
    for act in destructive_actions:
        assert HumanApprovalGate.is_destructive(act) is True, f"Expected {act} to be destructive"

    # Non-destructive operational tasks
    safe_actions = [
        "run_unit_tests",
        "compile_typescript",
        "format_code_ruff",
        "inspect_container_health",
        "read_task_artifact",
    ]
    for act in safe_actions:
        assert HumanApprovalGate.is_destructive(act) is False, f"Expected {act} to be safe"


@pytest.mark.asyncio
async def test_human_approval_gate_intercepts_unapproved_destructive_action(async_session: AsyncSession):
    """
    Validates that a destructive action attempted without prior approval
    is blocked, creates a PENDING approval record, and raises HumanApprovalRequiredError.
    """
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    executed = False

    def dangerous_migration():
        nonlocal executed
        executed = True
        return "table dropped"

    # Attempt execution without approval ID
    with pytest.raises(HumanApprovalRequiredError) as exc_info:
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.DATABASE_MIGRATION.value,
            requested_by="ai_devops_agent",
            description="Drop legacy payments archive partition",
            operation_callable=dangerous_migration,
            details={"table": "legacy_payments", "rows": 100000},
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    # Operation must NOT have executed
    assert executed is False
    assert "Destructive operation intercepted" in str(exc_info.value)

    # Verify pending approval request in database
    pending_list = await approval_repo.list_pending()
    assert len(pending_list) == 1
    req = pending_list[0]
    assert req.action_type == DestructiveOperationType.DATABASE_MIGRATION.value
    assert req.status == ApprovalStatus.PENDING
    assert req.requested_by == "ai_devops_agent"


@pytest.mark.asyncio
async def test_human_approval_gate_executes_when_operator_approves(async_session: AsyncSession):
    """
    Validates end-to-end sovereignty flow:
    1. Interception creates pending approval.
    2. Human operator approves.
    3. Execution proceeds cleanly and logs audit trail.
    """
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    executed = False

    async def deploy_production():
        nonlocal executed
        executed = True
        return {"status": "deployed", "cluster": "us-east-prod"}

    # 1. Create request
    app_req = await HumanApprovalGate.request_approval(
        action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
        requested_by="boss_ai",
        description="Deploy v2.4.0 payment microservice to production",
        justification="Passed all empirical QA benchmarks and security tests",
        details={"version": "2.4.0", "target": "prod-k8s"},
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    assert app_req.status == ApprovalStatus.PENDING

    # 2. Operator reviews and approves
    approved_app = await HumanApprovalGate.decide(
        approval_id=app_req.id,
        decision="approved",
        decided_by="human_owner",
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    assert approved_app.status == ApprovalStatus.APPROVED

    # 3. Execute with granted approval ID
    result = await HumanApprovalGate.execute_with_gate(
        action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
        requested_by="boss_ai",
        description="Deploy v2.4.0 payment microservice to production",
        operation_callable=deploy_production,
        details={"version": "2.4.0"},
        approval_id=approved_app.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )

    assert executed is True
    assert result["status"] == "deployed"


@pytest.mark.asyncio
async def test_human_approval_gate_blocks_rejected_request(async_session: AsyncSession):
    """Validates that a rejected approval request strictly blocks execution."""
    approval_repo = ApprovalRepository(async_session)

    app_req = await HumanApprovalGate.request_approval(
        action_type=DestructiveOperationType.CREDENTIAL_ROTATION.value,
        requested_by="ai_worker",
        description="Rotate root signing key",
        justification="Routine hygiene",
        details={},
        approval_repo=approval_repo,
    )

    # Operator rejects
    await HumanApprovalGate.decide(
        approval_id=app_req.id,
        decision="rejected",
        decided_by="security_admin",
        approval_repo=approval_repo,
    )

    # Attempt execution with rejected ID -> must raise
    with pytest.raises(HumanApprovalRequiredError):
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.CREDENTIAL_ROTATION.value,
            requested_by="ai_worker",
            description="Rotate root signing key",
            operation_callable=lambda: "key_rotated",
            approval_id=app_req.id,
            approval_repo=approval_repo,
        )


@pytest.mark.asyncio
async def test_human_approval_gate_replay_prevention_single_use(async_session: AsyncSession):
    """
    Validates that an approval token is strictly single-use:
    After successful execution, it is marked consumed and cannot be replayed.
    """
    approval_repo = ApprovalRepository(async_session)

    app_req = await HumanApprovalGate.request_approval(
        action_type=DestructiveOperationType.DATABASE_MIGRATION.value,
        requested_by="ai_agent",
        description="Migrate schema v1 to v2",
        justification="Schema update",
        details={"migration": "v2"},
        approval_repo=approval_repo,
    )
    await HumanApprovalGate.decide(
        approval_id=app_req.id,
        decision="approved",
        decided_by="human_owner",
        approval_repo=approval_repo,
    )

    exec_count = 0

    def run_migration():
        nonlocal exec_count
        exec_count += 1
        return "migration_done"

    # First execution succeeds and consumes the approval
    res = await HumanApprovalGate.execute_with_gate(
        action_type=DestructiveOperationType.DATABASE_MIGRATION.value,
        requested_by="ai_agent",
        description="Migrate schema v1 to v2",
        operation_callable=run_migration,
        details={"migration": "v2"},
        approval_id=app_req.id,
        approval_repo=approval_repo,
    )
    assert res == "migration_done"
    assert exec_count == 1

    # Verify database record is consumed
    app = await approval_repo.get_by_id(app_req.id)
    assert app.consumed_at is not None

    # check_authorized returns False once consumed
    assert await HumanApprovalGate.check_authorized(app_req.id, approval_repo) is False

    # Second execution attempt (replay attack) fails closed
    with pytest.raises(HumanApprovalRequiredError):
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.DATABASE_MIGRATION.value,
            requested_by="ai_agent",
            description="Migrate schema v1 to v2",
            operation_callable=run_migration,
            details={"migration": "v2"},
            approval_id=app_req.id,
            approval_repo=approval_repo,
        )
    assert exec_count == 1


@pytest.mark.asyncio
async def test_human_approval_gate_blocks_action_type_mismatch(async_session: AsyncSession):
    """
    Validates that an approved token for action A cannot be used to execute action B.
    """
    approval_repo = ApprovalRepository(async_session)

    app_req = await HumanApprovalGate.request_approval(
        action_type=DestructiveOperationType.DATABASE_MIGRATION.value,
        requested_by="ai_agent",
        description="Migrate schema",
        justification="Legitimate migration",
        details={"target": "db"},
        approval_repo=approval_repo,
    )
    await HumanApprovalGate.decide(
        approval_id=app_req.id,
        decision="approved",
        decided_by="human_owner",
        approval_repo=approval_repo,
    )

    # check_authorized with wrong action_type returns False
    assert await HumanApprovalGate.check_authorized(
        app_req.id,
        approval_repo,
        action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
    ) is False

    # Attempting to execute PRODUCTION_DEPLOYMENT with DATABASE_MIGRATION approval fails closed
    with pytest.raises(HumanApprovalRequiredError, match="action_type"):
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
            requested_by="ai_agent",
            description="Deploy prod",
            operation_callable=lambda: "deployed",
            details={"target": "db"},
            approval_id=app_req.id,
            approval_repo=approval_repo,
        )


@pytest.mark.asyncio
async def test_human_approval_gate_blocks_during_emergency_halt(async_session: AsyncSession):
    """
    Validates that destructive operations cannot execute when the global emergency kill switch is active.
    """
    from neural.exceptions import EmergencyStopTriggered
    from neural.security.kill_switch import get_kill_switch, reset_kill_switch

    reset_kill_switch()
    ks = get_kill_switch()
    await ks.emergency_halt(reason="Containment drill", actor="sec_lead")

    approval_repo = ApprovalRepository(async_session)
    app_req = await HumanApprovalGate.request_approval(
        action_type=DestructiveOperationType.INFRASTRUCTURE_DESTROY.value,
        requested_by="ai_agent",
        description="Destroy cluster",
        justification="Decommissioning",
        details={},
        approval_repo=approval_repo,
    )
    await HumanApprovalGate.decide(
        approval_id=app_req.id,
        decision="approved",
        decided_by="human_owner",
        approval_repo=approval_repo,
    )

    with pytest.raises(EmergencyStopTriggered):
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.INFRASTRUCTURE_DESTROY.value,
            requested_by="ai_agent",
            description="Destroy cluster",
            operation_callable=lambda: "cluster_destroyed",
            approval_id=app_req.id,
            approval_repo=approval_repo,
        )

    reset_kill_switch()
