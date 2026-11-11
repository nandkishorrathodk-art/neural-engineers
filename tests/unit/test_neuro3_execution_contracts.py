"""
ANCOS NEURO-3: Unit Test Suite for Explicit Task Mode Contracts,
Capability Policy Enforcement, Timeouts, Cancellation, and Workspace Deltas.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from neural.constants import (
    ALL_EXECUTION_MODES,
    EmployeeStatus,
    ExecutionMode,
    TaskStatus,
)
from neural.containers.execution_limits import (
    BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS,
    TERMINAL_COMMAND_TIMEOUT_SECONDS,
    WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS,
)
from neural.db.models import Employee, Task, TaskAttempt
from neural.db.repositories import AuditRepository, EmployeeRepository, TaskRepository
from neural.exceptions import (
    CapabilityPolicyViolationError,
    StrictGUIContractViolation,
    UnauthorizedToolError,
)
from neural.execution.contracts import (
    ALLOWLISTED_TERMINAL_EXECUTABLES,
    AUTHORIZED_TOOLS_CONTRACT,
    GUI_ONLY_CONTRACT,
    HYBRID_CONTRACT,
    RESEARCH_REASONING_CONTRACT,
    TERMINAL_ONLY_CONTRACT,
    CapabilityPolicyEnforcer,
    TaskCapability,
    get_mode_contract,
)
from neural.execution.fabric import UnifiedExecutionFabric
from neural.verification.workspace_manifest import (
    build_workspace_delta,
    valid_workspace_manifest,
)

# =========================================================================
# 1. Explicit Mode Contracts Tests
# =========================================================================

def test_all_five_execution_modes_have_registered_contracts():
    """Verify all 5 execution modes have explicit, distinct contracts."""
    assert len(ALL_EXECUTION_MODES) == 5
    for mode in ALL_EXECUTION_MODES:
        contract = get_mode_contract(mode)
        assert contract is not None
        assert contract.mode in ALL_EXECUTION_MODES or ExecutionMode.normalize(contract.mode) in ALL_EXECUTION_MODES
        assert contract.allowed_capabilities
        assert isinstance(contract.process_timeout_seconds, float)


def test_terminal_only_contract_specifications():
    """Verify terminal_only contract permissions and boundaries."""
    c = TERMINAL_ONLY_CONTRACT
    assert c.is_capability_allowed(TaskCapability.TERMINAL_EXEC)
    assert c.is_capability_allowed(TaskCapability.WORKSPACE_FILE_IO)
    assert c.is_capability_allowed(TaskCapability.TEST_EXEC)
    assert not c.is_capability_allowed(TaskCapability.GUI_PERCEPTION)
    assert not c.is_capability_allowed(TaskCapability.GUI_MOTOR)
    assert not c.is_capability_allowed(TaskCapability.TOOL_INVOCATION)
    assert not c.requires_visual_loop
    assert c.allows_state_mutation
    assert c.allowlisted_executables == ALLOWLISTED_TERMINAL_EXECUTABLES


def test_gui_only_contract_specifications():
    """Verify gui_only contract permissions and visual-only constraints."""
    c = GUI_ONLY_CONTRACT
    assert c.is_capability_allowed(TaskCapability.GUI_PERCEPTION)
    assert c.is_capability_allowed(TaskCapability.GUI_MOTOR)
    assert c.is_capability_allowed(TaskCapability.GUI_VERIFICATION)
    assert not c.is_capability_allowed(TaskCapability.TERMINAL_EXEC)
    assert not c.is_capability_allowed(TaskCapability.TOOL_INVOCATION)
    assert c.requires_visual_loop
    assert c.allows_state_mutation


def test_hybrid_contract_specifications():
    """Verify hybrid contract permits both terminal and visual interfaces."""
    c = HYBRID_CONTRACT
    assert c.is_capability_allowed(TaskCapability.TERMINAL_EXEC)
    assert c.is_capability_allowed(TaskCapability.GUI_PERCEPTION)
    assert c.is_capability_allowed(TaskCapability.GUI_MOTOR)
    assert c.is_capability_allowed(TaskCapability.WORKSPACE_FILE_IO)
    assert c.requires_visual_loop


def test_authorized_tools_contract_specifications():
    """Verify authorized_tools contract is restricted to tool invocations."""
    c = AUTHORIZED_TOOLS_CONTRACT
    assert c.is_capability_allowed(TaskCapability.TOOL_INVOCATION)
    assert not c.is_capability_allowed(TaskCapability.TERMINAL_EXEC)
    assert not c.is_capability_allowed(TaskCapability.GUI_MOTOR)


def test_research_reasoning_contract_specifications():
    """Verify research_reasoning contract allows analysis but strictly forbids state mutation."""
    c = RESEARCH_REASONING_CONTRACT
    assert c.is_capability_allowed(TaskCapability.COGNITIVE_REASONING)
    assert c.is_capability_allowed(TaskCapability.MEMORY_SEARCH)
    assert c.is_capability_allowed(TaskCapability.REPORT_SYNTHESIS)
    assert not c.is_capability_allowed(TaskCapability.TERMINAL_EXEC)
    assert not c.is_capability_allowed(TaskCapability.GUI_MOTOR)
    assert not c.allows_state_mutation


# =========================================================================
# 2. Capability Policy Enforcement & Mode Violation Defenses
# =========================================================================

def test_terminal_mode_rejects_gui_actions():
    """Terminal tasks attempting GUI interactions must be rejected."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(CapabilityPolicyViolationError, match="strictly rejects GUI action"):
        enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "gui_click")

    with pytest.raises(CapabilityPolicyViolationError, match="strictly rejects GUI action"):
        enforcer.validate_action(ExecutionMode.TERMINAL, "gui_screenshot")


def test_terminal_mode_rejects_unallowlisted_executable():
    """Terminal tasks attempting unallowlisted executables must be blocked."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(CapabilityPolicyViolationError, match="not in allowlisted binaries"):
        enforcer.validate_action(
            ExecutionMode.TERMINAL_ONLY,
            "terminal_command",
            {"command": ["bash", "-c", "echo 1"]},
        )

    with pytest.raises(CapabilityPolicyViolationError, match="not in allowlisted binaries"):
        enforcer.validate_action(
            ExecutionMode.TERMINAL_ONLY,
            "terminal_command",
            {"command": ["curl", "http://malicious.com"]},
        )


def test_terminal_mode_permits_allowlisted_executables():
    """Terminal tasks executing python/pytest/npm are permitted."""
    enforcer = CapabilityPolicyEnforcer()
    # Should not raise
    enforcer.validate_action(
        ExecutionMode.TERMINAL_ONLY,
        "terminal_command",
        {"command": ["python3", "-m", "unittest", "-v"]},
    )
    enforcer.validate_action(
        ExecutionMode.TERMINAL_ONLY,
        "terminal_command",
        {"command": ["pytest", "-q"]},
    )
    enforcer.validate_action(
        ExecutionMode.TERMINAL_ONLY,
        "terminal_command",
        {"command": ["npm", "test"]},
    )


def test_gui_mode_rejects_direct_cli_shortcuts():
    """GUI tasks attempting direct CLI command shortcuts must be blocked."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(CapabilityPolicyViolationError, match="forbids direct terminal/CLI"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "terminal_command",
            {"command": ["python3", "script.py"]},
        )

    with pytest.raises(CapabilityPolicyViolationError, match="forbids direct terminal/CLI"):
        enforcer.validate_action(
            ExecutionMode.GUI,
            "exec_shell",
            {"command": "echo test"},
        )


def test_gui_mode_rejects_dom_and_cdp_cheating():
    """GUI tasks attempting direct DOM or CDP bypasses trigger StrictGUIContractViolation."""
    enforcer = CapabilityPolicyEnforcer()

    with pytest.raises(StrictGUIContractViolation, match="querySelector"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "gui_action",
            {"script": "document.querySelector('.submit').click()"},
        )

    with pytest.raises(StrictGUIContractViolation, match="cdp"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "gui_action",
            {"method": "cdp.Page.navigate"},
        )

    with pytest.raises(StrictGUIContractViolation, match="at-spi"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "gui_action",
            {"tree": "at-spi accessibility dump"},
        )


def test_authorized_tools_mode_fails_closed_on_unauthorized_tool():
    """Tasks under authorized_tools mode fail closed if an unlisted tool is invoked."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(UnauthorizedToolError, match="not in authorized tool allowlist"):
        enforcer.validate_action(
            ExecutionMode.AUTHORIZED_TOOLS,
            "tool_invocation",
            {"tool_name": "arbitrary_network_fetcher"},
            task_context={"authorized_tools": ["code_analyzer", "linter"]},
        )


def test_authorized_tools_mode_blocks_safety_violated_tools():
    """Tools with safety violations are blocked even if authorized."""
    enforcer = CapabilityPolicyEnforcer()
    stub_tool_record = type("StubToolRecord", (), {
        "safety_status": "violated",
        "safety_violations": ["Use of forbidden eval() construct"],
    })()
    with pytest.raises(UnauthorizedToolError, match="safety violations"):
        enforcer.validate_action(
            ExecutionMode.AUTHORIZED_TOOLS,
            "tool_invocation",
            {"tool_name": "code_analyzer", "tool_record": stub_tool_record},
            task_context={"authorized_tools": ["code_analyzer"]},
        )


def test_research_reasoning_mode_blocks_state_mutation():
    """Research reasoning tasks attempting state mutation are blocked."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(CapabilityPolicyViolationError, match="state-mutating action"):
        enforcer.validate_action(
            ExecutionMode.RESEARCH_REASONING,
            "terminal_command",
            {"command": ["rm", "-rf", "/data"]},
        )


# =========================================================================
# 3. Timeouts & Request Deadlines
# =========================================================================

def test_timeout_hierarchy():
    """Verify strict timeout ordering: process (300s) < broker (310s) < worker (320s)."""
    timeouts = UnifiedExecutionFabric.get_timeouts()
    assert timeouts["process_timeout_seconds"] == 300
    assert timeouts["broker_deadline_seconds"] == 310
    assert timeouts["worker_deadline_seconds"] == 320
    assert TERMINAL_COMMAND_TIMEOUT_SECONDS < BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS < WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS


# =========================================================================
# 4. Workspace Baseline, Deltas & SHA-256 Digest
# =========================================================================

def test_workspace_baseline_snapshot_and_delta_generation():
    """Verify task-start baseline capture and verifier delta generation with SHA-256 digest."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Initial state: 1 file
        f1 = os.path.join(tmp_dir, "initial.txt")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("initial content")

        baseline = UnifiedExecutionFabric.capture_workspace_snapshot(tmp_dir)
        assert valid_workspace_manifest(baseline)
        assert baseline["file_count"] == 1
        assert baseline["files"][0]["path"] == "initial.txt"

        # Worker action: modify file 1 and create file 2
        with open(f1, "w", encoding="utf-8") as f:
            f.write("modified initial content")
        f2 = os.path.join(tmp_dir, "deliverable.py")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("def solution(): return 42\n")

        after = UnifiedExecutionFabric.capture_workspace_snapshot(tmp_dir)
        assert valid_workspace_manifest(after)
        assert after["file_count"] == 2

        # Generate delta
        delta = build_workspace_delta(baseline, after)
        assert delta["complete"] is True
        assert delta["counts"]["created"] == 1
        assert delta["counts"]["modified"] == 1
        assert delta["counts"]["deleted"] == 0
        assert delta["created"][0]["path"] == "deliverable.py"
        assert delta["modified"][0]["path"] == "initial.txt"
        assert "sha256_delta_digest" in delta
        assert len(delta["sha256_delta_digest"]) == 64


# =========================================================================
# 5. Task Cancellation, Monotonic Fencing Token & Reassignment
# =========================================================================

@pytest.mark.asyncio
async def test_task_cancellation_and_attempt_invalidation(async_session):
    """Verify clean attempt invalidation, lease quarantine, and monotonic token increment on cancel."""
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)
    fabric = UnifiedExecutionFabric(
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    # 1. Create employee and task
    emp = Employee(
        employee_id="ANC-EMP-CANCEL-01",
        name="Cancel Engineer",
        title="Software Engineer",
        department="Engineering",
        system_prompt="You are a software engineer.",
        status=EmployeeStatus.WORKING,
    )
    async_session.add(emp)
    await async_session.flush()

    task = Task(
        task_id="TASK-CANCEL-TEST-01",
        project_id="PROJ-01",
        title="Task to be cancelled",
        objective="Complete cancellation test.",
        scope="Testing cancellation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.RUNNING,
        assigned_to=emp.id,
        fencing_token=5,
    )
    async_session.add(task)
    await async_session.flush()

    # Active attempt
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-01",
        fencing_token=5,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60),
    )
    async_session.add(attempt)
    await async_session.commit()

    # 2. Cancel task without requeue
    result = await fabric.cancel_task(
        task_id="TASK-CANCEL-TEST-01",
        reason="Owner requested emergency mission cancellation",
        requeue=False,
    )

    assert result["status"] == "cancelled"
    assert result["fencing_token"] == 6
    assert result["old_fencing_token"] == 5
    assert result["attempts_invalidated"] == 1

    # Verify attempt is marked cancelled
    updated_attempt = await async_session.get(TaskAttempt, attempt.id)
    await async_session.refresh(updated_attempt)
    assert updated_attempt.status == "cancelled"
    assert "Owner requested" in updated_attempt.error_message

    # Verify employee was released to IDLE
    updated_emp = await async_session.get(Employee, emp.id)
    assert updated_emp.status == EmployeeStatus.IDLE


@pytest.mark.asyncio
async def test_task_cancellation_with_requeue_and_standby_reassignment(async_session):
    """Verify task cancellation with requeue and atomic standby employee reassignment."""
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)
    fabric = UnifiedExecutionFabric(
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    emp1 = Employee(
        employee_id="ANC-EMP-REASSIGN-01",
        name="Primary Engineer",
        title="Junior Engineer",
        department="Engineering",
        system_prompt="Primary engineer prompt.",
        status=EmployeeStatus.WORKING,
    )
    standby_emp = Employee(
        employee_id="ANC-EMP-STANDBY-02",
        name="Standby Senior Engineer",
        title="Senior Engineer",
        department="Engineering",
        system_prompt="Standby senior engineer prompt.",
        status=EmployeeStatus.IDLE,
    )
    async_session.add_all([emp1, standby_emp])
    await async_session.flush()

    task = Task(
        task_id="TASK-REASSIGN-01",
        project_id="PROJ-01",
        title="Failing task to requeue",
        objective="Complete requeue reassignment test.",
        scope="Testing requeue reassignment",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.RUNNING,
        assigned_to=emp1.id,
        fencing_token=10,
    )
    async_session.add(task)
    await async_session.flush()

    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-01",
        fencing_token=10,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60),
    )
    async_session.add(attempt)
    await async_session.commit()

    # Requeue with standby reassignment
    result = await fabric.cancel_task(
        task_id="TASK-REASSIGN-01",
        reason="Primary worker stalled; reassigning to senior standby",
        requeue=True,
        standby_employee_id=standby_emp.employee_id,
    )

    assert result["status"] == "assigned"
    assert result["fencing_token"] == 11
    assert result["requeued"] is True
    assert result["assigned_to"] == standby_emp.id

    # Check database state
    updated_emp1 = await async_session.get(Employee, emp1.id)
    assert updated_emp1.status == EmployeeStatus.IDLE

    updated_standby = await async_session.get(Employee, standby_emp.id)
    assert updated_standby.status == EmployeeStatus.WORKING

    updated_task = await task_repo.get_by_task_id("TASK-REASSIGN-01")
    assert updated_task.assigned_to == standby_emp.id
    assert updated_task.fencing_token == 11


# =========================================================================
# 6. Mode-Tailored Independent Deliverable Verification
# =========================================================================

@pytest.mark.asyncio
async def test_independent_verification_across_all_five_modes(async_session):
    """Verify independent deliverable verification generates signed receipts for all 5 modes."""
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    fabric = UnifiedExecutionFabric(
        task_repo=task_repo,
        employee_repo=employee_repo,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        modes_and_deliverables = [
            (
                ExecutionMode.TERMINAL_ONLY,
                "solution.py",
                "def solve(): return 100\n",
                {"exit_code": 0},
            ),
            (
                ExecutionMode.GUI_ONLY,
                "screen.png",
                "FAKE_PNG_BYTES",
                {"visual_receipt": {"screenshot_path": "screen.png"}, "exit_code": 0},
            ),
            (
                ExecutionMode.HYBRID,
                "hybrid_output.py",
                "result = True\n",
                {"exit_code": 0},
            ),
            (
                ExecutionMode.AUTHORIZED_TOOLS,
                "tool_result.json",
                json.dumps({"status": "ok"}),
                {"tool_name": "ast_checker", "exit_code": 0},
            ),
            (
                ExecutionMode.RESEARCH_REASONING,
                "report.md",
                "# Research synthesis\nVerified findings.\n",
                {"exit_code": 0},
            ),
        ]

        for mode, filename, content, receipt in modes_and_deliverables:
            fpath = os.path.join(tmp_dir, filename)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)

            task = Task(
                task_id=f"TASK-VERIFY-{mode.value.upper()}",
                project_id="PROJ-VERIFY",
                title=f"Deliverable for {mode.value}",
                scope="Verification testing",
                acceptance_criteria="Automated validation passes",
                execution_mode=mode,
                status=TaskStatus.RUNNING,
                fencing_token=1,
            )

            res = await fabric.verify_deliverables_for_mode(
                task=task,
                execution_mode=mode,
                artifacts=[fpath],
                execution_receipt=receipt,
                workspace_path=tmp_dir,
            )

            assert res["passed"] is True
            assert res["receipt"]["passed"] is True
            assert res["receipt"]["execution_mode"] in (mode.value, ExecutionMode.normalize(mode).value)
            assert "signature" in res["receipt"]
            assert len(res["receipt"]["signature"]) == 64
            assert fpath in res["receipt"]["artifact_digests"]


# =========================================================================
# 7. Robustness & Edge Case Defenses (Reviewer Hardening)
# =========================================================================

def test_contract_registered_capability_properties():
    """Verify registered capability property flags match mode contract invariants."""
    # Terminal Only
    t = TERMINAL_ONLY_CONTRACT
    assert t.allow_terminal is True
    assert t.allow_gui is False
    assert t.allow_tools is False
    assert t.allow_state_mutation is True
    assert t.allow_visual_loop is False
    assert t.allow_cdp_dom_shortcuts is False
    assert t.allow_direct_cli_shortcuts is False
    assert t.allow_destructive_ops is False

    # GUI Only
    g = GUI_ONLY_CONTRACT
    assert g.allow_terminal is False
    assert g.allow_gui is True
    assert g.allow_tools is False
    assert g.allow_state_mutation is True
    assert g.allow_visual_loop is True
    assert g.allow_cdp_dom_shortcuts is False
    assert g.allow_direct_cli_shortcuts is False
    assert g.allow_destructive_ops is False

    # Hybrid
    h = HYBRID_CONTRACT
    assert h.allow_terminal is True
    assert h.allow_gui is True
    assert h.allow_tools is False
    assert h.allow_state_mutation is True
    assert h.allow_visual_loop is True

    # Authorized Tools
    tools_c = AUTHORIZED_TOOLS_CONTRACT
    assert tools_c.allow_terminal is False
    assert tools_c.allow_gui is False
    assert tools_c.allow_tools is True
    assert tools_c.allow_state_mutation is True

    # Research Reasoning
    r = RESEARCH_REASONING_CONTRACT
    assert r.allow_terminal is False
    assert r.allow_gui is False
    assert r.allow_tools is False
    assert r.allow_state_mutation is False


def test_terminal_mode_permits_extended_allowlist_and_paths():
    """Terminal mode permits git, uv, node, and handles full paths and .exe extensions."""
    enforcer = CapabilityPolicyEnforcer()
    # git, uv, node are permitted
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": ["git", "status"]})
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": ["uv", "run", "pytest"]})
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": ["node", "app.js"]})

    # Full paths and .exe extensions are normalized and accepted
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": ["/usr/bin/python3", "main.py"]})
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": [r"C:\Python312\python.exe", "main.py"]})
    enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "terminal_command", {"command": ["pytest.exe", "-v"]})


def test_gui_mode_blocks_page_evaluate_and_cdp_send():
    """Strict GUI contract blocks page.evaluate and cdp.send bypasses."""
    enforcer = CapabilityPolicyEnforcer()
    with pytest.raises(StrictGUIContractViolation, match=r"page"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "gui_action",
            {"script": "await page.evaluate(() => document.title)"},
        )
    with pytest.raises(StrictGUIContractViolation, match=r"cdp"):
        enforcer.validate_action(
            ExecutionMode.GUI_ONLY,
            "gui_action",
            {"command": "cdp.send('Input.dispatchMouseEvent')"},
        )


def test_execution_mode_normalize_none_and_case_insensitive():
    """ExecutionMode.normalize gracefully defaults None to TERMINAL_ONLY and handles aliases."""
    assert ExecutionMode.normalize(None) == ExecutionMode.TERMINAL_ONLY
    assert ExecutionMode.normalize("terminal") == ExecutionMode.TERMINAL_ONLY
    assert ExecutionMode.normalize("gui") == ExecutionMode.GUI_ONLY
    assert ExecutionMode.normalize("research") == ExecutionMode.RESEARCH_REASONING
    assert ExecutionMode.normalize("tools") == ExecutionMode.AUTHORIZED_TOOLS
    assert ExecutionMode.is_terminal(None) is False
    assert ExecutionMode.is_gui(None) is False


def test_validate_command_empty_and_destructive_patterns():
    """Validating empty commands or destructive keywords raises appropriate errors."""
    contract = TERMINAL_ONLY_CONTRACT
    from neural.exceptions import ModeContractViolationError
    with pytest.raises(ModeContractViolationError):
        contract.validate_command([])

    with pytest.raises(CapabilityPolicyViolationError, match="Destructive command pattern"):
        contract.validate_command(["python", "-c", "rm -rf /"])

    with pytest.raises(CapabilityPolicyViolationError, match="Destructive command pattern"):
        contract.validate_command(["python", "-c", "import os; drop database"])


@pytest.mark.asyncio
async def test_cannot_cancel_already_completed_task(async_session):
    """Attempting to cancel an already COMPLETED task raises TaskCancellationError."""
    task_repo = TaskRepository(async_session)
    fabric = UnifiedExecutionFabric(task_repo=task_repo)

    task = Task(
        task_id="TASK-ALREADY-DONE-01",
        project_id="PROJ-01",
        title="Completed task",
        objective="Done",
        scope="Testing",
        acceptance_criteria="Green",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.COMPLETED,
        fencing_token=1,
    )
    async_session.add(task)
    await async_session.commit()

    from neural.exceptions import TaskCancellationError
    with pytest.raises(TaskCancellationError, match="already completed"):
        await fabric.cancel_task(
            task_id="TASK-ALREADY-DONE-01",
            reason="Attempting to cancel finished work",
        )
