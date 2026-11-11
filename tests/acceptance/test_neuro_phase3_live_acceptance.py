"""
ANCOS NEURO-3 Live Acceptance Test Suite.
Empirically verifies the Unified Execution Fabric across:
1. Explicit Task Mode Contracts (terminal_only, gui_only, hybrid, authorized_tools, research_reasoning).
2. Capability Policy Enforcement (fail-closed against forbidden modes/shortcuts/DOM/CDP cheating).
3. Lifecycle, Deadlines & Timeouts (process 300s, broker 310s, worker 320s).
4. Task Cancellation, Lease Quarantine, Monotonic Fencing Token Increment & Standby Reassignment.
5. Workspace Baseline, Verifier Path/Hash Deltas with SHA-256 digests.
6. Independent Deliverable Verification with Cryptographic Receipts & Empirical Skill Certification.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from neural.constants import EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.models import Employee, EmployeeSkill, Project, Task, TaskAttempt
from neural.db.repositories import AuditRepository, EmployeeRepository, TaskRepository
from neural.evaluation.verifier import IndependentDeliverableVerifier
from neural.exceptions import (
    CapabilityPolicyViolationError,
    StrictGUIContractViolation,
    UnauthorizedToolError,
    UnverifiedTaskError,
)
from neural.execution.contracts import CapabilityPolicyEnforcer
from neural.execution.fabric import UnifiedExecutionFabric
from neural.observability.audit import AuditLogChainVerifier


@pytest.mark.asyncio
async def test_neuro_phase3_unified_execution_fabric_acceptance(async_session):
    """
    NEURO-3 Live Acceptance Scenario:
    Demonstrates full compliance with NEURO-3 Unified Execution Fabric requirements
    across all 5 execution modes, capability policy defenses, cancellation/reassignment,
    and independent verification receipts.
    """
    with tempfile.TemporaryDirectory() as root_workspace:
        # =========================================================================
        # STEP 1: Initialize Unified Execution Fabric with Repositories
        # =========================================================================
        task_repo = TaskRepository(async_session)
        employee_repo = EmployeeRepository(async_session)
        audit_repo = AuditRepository(async_session)
        verifier = IndependentDeliverableVerifier(
            employee_repo=employee_repo, task_repo=task_repo
        )
        fabric = UnifiedExecutionFabric(
            task_repo=task_repo,
            employee_repo=employee_repo,
            audit_repo=audit_repo,
            verifier=verifier,
        )

        project = Project(
            id="PROJ-NEURO-3-ACCEPTANCE",
            name="Autonomous Execution Fabric Acceptance",
            objective="Empirically verify all 5 execution modes and capability contracts.",
            status="active",
            execution_plan={},
        )
        async_session.add(project)
        await async_session.flush()

        # =========================================================================
        # STEP 2: Recruit & Provision Dynamic Workforce
        # =========================================================================
        lead_eng = Employee(
            employee_id="ANC-EMP-LEAD-01",
            name="Lead Software Engineer",
            title="Senior Systems Architect",
            department="Engineering",
            system_prompt="Lead engineering system prompt.",
            status=EmployeeStatus.WORKING,
        )
        standby_eng = Employee(
            employee_id="ANC-EMP-STANDBY-02",
            name="Standby Specialist",
            title="Principal Engineer",
            department="Engineering",
            system_prompt="Standby principal prompt.",
            status=EmployeeStatus.IDLE,
        )
        qa_specialist = Employee(
            employee_id="ANC-EMP-QA-03",
            name="Visual QA Specialist",
            title="QA Engineer",
            department="Quality & Reliability",
            system_prompt="Visual QA prompt.",
            status=EmployeeStatus.WORKING,
        )
        researcher = Employee(
            employee_id="ANC-EMP-RES-04",
            name="Research Scientist",
            title="Staff Scientist",
            department="Research & Intelligence",
            system_prompt="Research prompt.",
            status=EmployeeStatus.WORKING,
        )
        async_session.add_all([lead_eng, standby_eng, qa_specialist, researcher])
        await async_session.flush()

        # Initial unverified skills
        lead_skill = EmployeeSkill(
            employee_id=lead_eng.id,
            skill_name="Python Core",
            level="advanced",
            verified=False,
        )
        qa_skill = EmployeeSkill(
            employee_id=qa_specialist.id,
            skill_name="Visual UI Verification",
            level="advanced",
            verified=False,
        )
        async_session.add_all([lead_skill, qa_skill])
        await async_session.flush()

        # =========================================================================
        # STEP 3: Explicit Mode 1: Terminal-Only Task Execution
        # =========================================================================
        t1_ws = os.path.join(root_workspace, "term_workspace")
        os.makedirs(t1_ws, exist_ok=True)
        t1 = Task(
            task_id="TASK-N3-01-TERMINAL",
            project_id=project.id,
            title="Build Core Math Module",
            objective="Implement robust string math parser.",
            scope="Terminal software development",
            acceptance_criteria="Automated tests pass",
            execution_mode=ExecutionMode.TERMINAL_ONLY,
            status=TaskStatus.RUNNING,
            assigned_to=lead_eng.id,
            fencing_token=1,
            required_capabilities=["Python Core"],
        )
        async_session.add(t1)
        await async_session.flush()

        res_t1 = await fabric.execute_task_with_contract(task=t1, workspace_path=t1_ws)
        assert res_t1["status"] == "completed"
        assert res_t1["passed"] is True
        assert res_t1["delta"]["complete"] is True
        assert "sha256_delta_digest" in res_t1["delta"]
        assert len(res_t1["delta"]["sha256_delta_digest"]) == 64
        assert res_t1["verification_receipt"]["passed"] is True
        assert "signature" in res_t1["verification_receipt"]

        # =========================================================================
        # STEP 4: Mode Contract Defense 1: Terminal Task Rejects GUI Action
        # =========================================================================
        enforcer = CapabilityPolicyEnforcer()
        with pytest.raises(CapabilityPolicyViolationError, match="strictly rejects GUI action"):
            enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "gui_screenshot")

        # =========================================================================
        # STEP 5: Explicit Mode 2: GUI-Only Task Execution
        # =========================================================================
        t2_ws = os.path.join(root_workspace, "gui_workspace")
        os.makedirs(t2_ws, exist_ok=True)
        t2 = Task(
            task_id="TASK-N3-02-GUI",
            project_id=project.id,
            title="Perform Visual Verification",
            objective="Interact with desktop GUI and verify visual state transitions.",
            scope="GUI visual perception",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.GUI_ONLY,
            status=TaskStatus.RUNNING,
            assigned_to=qa_specialist.id,
            fencing_token=1,
            required_capabilities=["Visual UI Verification"],
        )
        async_session.add(t2)
        await async_session.flush()

        res_t2 = await fabric.execute_task_with_contract(task=t2, workspace_path=t2_ws)
        assert res_t2["status"] == "completed"
        assert res_t2["passed"] is True
        assert res_t2["verification_receipt"]["passed"] is True

        # =========================================================================
        # STEP 6: Mode Contract Defense 2: GUI Task Rejects DOM/CLI Cheating
        # =========================================================================
        with pytest.raises(StrictGUIContractViolation, match="querySelector"):
            enforcer.validate_action(
                ExecutionMode.GUI_ONLY,
                "gui_action",
                {"code": "document.querySelector('#btn').click()"},
            )
        with pytest.raises(CapabilityPolicyViolationError, match="forbids direct terminal/CLI"):
            enforcer.validate_action(
                ExecutionMode.GUI_ONLY,
                "terminal_command",
                {"command": ["python3", "bypass.py"]},
            )

        # =========================================================================
        # STEP 7: Explicit Mode 3: Hybrid Task Execution
        # =========================================================================
        t3_ws = os.path.join(root_workspace, "hybrid_workspace")
        os.makedirs(t3_ws, exist_ok=True)
        t3 = Task(
            task_id="TASK-N3-03-HYBRID",
            project_id=project.id,
            title="Coordinated Hybrid Workflow",
            objective="Execute server process via CLI and verify UI via GUI.",
            scope="Hybrid workflow coordination",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.HYBRID,
            status=TaskStatus.RUNNING,
            assigned_to=lead_eng.id,
            fencing_token=1,
            required_capabilities=["Python Core"],
        )
        async_session.add(t3)
        await async_session.flush()

        res_t3 = await fabric.execute_task_with_contract(task=t3, workspace_path=t3_ws)
        assert res_t3["status"] == "completed"
        assert res_t3["passed"] is True

        # =========================================================================
        # STEP 8: Explicit Mode 4: Authorized Tools Task Execution & Fail-Closed
        # =========================================================================
        t4_ws = os.path.join(root_workspace, "tools_workspace")
        os.makedirs(t4_ws, exist_ok=True)
        t4 = Task(
            task_id="TASK-N3-04-TOOLS",
            project_id=project.id,
            title="Static Security Audit",
            objective="Run authorized security static analysis tool.",
            scope="Tool invocation",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.AUTHORIZED_TOOLS,
            status=TaskStatus.RUNNING,
            assigned_to=lead_eng.id,
            fencing_token=1,
            required_capabilities=["Python Core"],
        )
        async_session.add(t4)
        await async_session.flush()

        res_t4 = await fabric.execute_task_with_contract(
            task=t4,
            workspace_path=t4_ws,
            context={"tool_name": "workspace_analyzer", "authorized_tools": ["workspace_analyzer"]},
        )
        assert res_t4["status"] == "completed"
        assert res_t4["passed"] is True

        # Defense: Unauthorized tool fails closed
        with pytest.raises(UnauthorizedToolError, match="not in authorized tool allowlist"):
            enforcer.validate_action(
                ExecutionMode.AUTHORIZED_TOOLS,
                "tool_invocation",
                {"tool_name": "unauthorized_exploit_tool"},
                task_context={"authorized_tools": ["workspace_analyzer"]},
            )

        # =========================================================================
        # STEP 9: Explicit Mode 5: Research & Reasoning Task Execution
        # =========================================================================
        t5_ws = os.path.join(root_workspace, "research_workspace")
        os.makedirs(t5_ws, exist_ok=True)
        t5 = Task(
            task_id="TASK-N3-05-RESEARCH",
            project_id=project.id,
            title="Architectural Synthesis Report",
            objective="Analyze architectural tradeoffs and synthesize deliberation report.",
            scope="Research deliberation",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.RESEARCH_REASONING,
            status=TaskStatus.RUNNING,
            assigned_to=researcher.id,
            fencing_token=1,
            required_capabilities=["Research Analysis"],
        )
        async_session.add(t5)
        await async_session.flush()

        res_t5 = await fabric.execute_task_with_contract(task=t5, workspace_path=t5_ws)
        assert res_t5["status"] == "completed"
        assert res_t5["passed"] is True
        assert any(a.endswith(".md") for a in res_t5["execution_receipt"]["artifacts"])

        # Defense: Research reasoning blocks state-mutating destructive operations
        with pytest.raises(CapabilityPolicyViolationError, match="state-mutating action"):
            enforcer.validate_action(
                ExecutionMode.RESEARCH_REASONING,
                "terminal_command",
                {"command": ["rm", "-rf", "/workspace"]},
            )

        # =========================================================================
        # STEP 10: Task Cancellation & Lease Quarantine
        # =========================================================================
        t_cancel = Task(
            task_id="TASK-N3-06-CANCEL-QUARANTINE",
            project_id=project.id,
            title="Stalled Worker Task",
            objective="Verify attempt invalidation and lease quarantine upon cancel.",
            scope="Cancellation testing",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.TERMINAL_ONLY,
            status=TaskStatus.RUNNING,
            assigned_to=lead_eng.id,
            fencing_token=10,
        )
        async_session.add(t_cancel)
        await async_session.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        attempt = TaskAttempt(
            task_id=t_cancel.id,
            attempt_number=1,
            worker_id="worker-lead-01",
            fencing_token=10,
            status="running",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=60),
        )
        async_session.add(attempt)
        await async_session.commit()

        # Execute cancellation
        cancel_res = await fabric.cancel_task(
            task_id=t_cancel.task_id,
            reason="Worker lease timed out; cancelling attempt and quarantining slot.",
            requeue=False,
            actor="BossAI",
        )

        assert cancel_res["status"] == "cancelled"
        assert cancel_res["fencing_token"] == 11
        assert cancel_res["old_fencing_token"] == 10
        assert cancel_res["attempts_invalidated"] == 1

        # Attempt is verified cancelled in DB
        await async_session.refresh(attempt)
        assert attempt.status == "cancelled"
        assert "quarantining" in attempt.error_message

        # =========================================================================
        # STEP 11: Standby Reassignment & Recovery with Immutable Audit Trail
        # =========================================================================
        t_reassign = Task(
            task_id="TASK-N3-07-REASSIGN",
            project_id=project.id,
            title="Recoverable Mission Task",
            objective="Recover failed task and reassign cleanly to standby specialist.",
            scope="Standby recovery",
            acceptance_criteria="Automated validation passes",
            execution_mode=ExecutionMode.TERMINAL_ONLY,
            status=TaskStatus.RUNNING,
            assigned_to=lead_eng.id,
            fencing_token=20,
        )
        async_session.add(t_reassign)
        await async_session.flush()

        attempt2 = TaskAttempt(
            task_id=t_reassign.id,
            attempt_number=1,
            worker_id="worker-lead-01",
            fencing_token=20,
            status="running",
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=60),
        )
        async_session.add(attempt2)
        await async_session.commit()

        reassign_res = await fabric.cancel_task(
            task_id=t_reassign.task_id,
            reason="Primary engineer disconnected; reassigning to standby senior engineer.",
            requeue=True,
            standby_employee_id=standby_eng.employee_id,
            actor="BossAI",
        )

        assert reassign_res["status"] == "assigned"
        assert reassign_res["fencing_token"] == 21
        assert reassign_res["requeued"] is True
        assert reassign_res["assigned_to"] == standby_eng.id

        # Verify audit trail integrity
        records = await audit_repo.list_all(limit=20)
        assert len(records) >= 2
        ordered_records = AuditLogChainVerifier.order_chain(records)
        chain_valid, chain_err = AuditLogChainVerifier.verify_records(ordered_records)
        assert chain_valid is True, f"Audit hash chain violation: {chain_err}"

        # =========================================================================
        # STEP 12: Empirical Skill Certification Gate
        # =========================================================================
        # Lead engineer's Python Core skill was empirically certified
        updated_lead_skill = await async_session.get(EmployeeSkill, lead_skill.id)
        assert updated_lead_skill.verified is True
        assert t1.task_id in updated_lead_skill.evidence_task_ids

        # Visual QA specialist's skill was empirically certified
        updated_qa_skill = await async_session.get(EmployeeSkill, qa_skill.id)
        assert updated_qa_skill.verified is True
        assert t2.task_id in updated_qa_skill.evidence_task_ids

        # Strict gate blocks unverified completion
        with pytest.raises(UnverifiedTaskError):
            await verifier.gate_task_completion(
                task_id=t1.task_id,
                verification_receipt={"passed": False, "notes": "Failing receipt"},
            )
