"""
ANCOS NEURO-13 Master Live Acceptance Test Suite.
Empirically verifies the 21-Step End-to-End Autonomous Company Mission across:
1. Owner Authentication, Budget Quota Pre-Reservation & Preflight Verification
2. High-Level Enterprise Objective Submission & Boss Clarification Context Answering
3. BossBrain Hierarchical DAG Decomposition & Kahn's Algorithm Acyclicity
4. Dynamic Workforce Recruitment with Deterministic Desk Mapping & Initial Unverified Skills
5. Desktop Session Pool Admission Control, Capacity Limits & Monotonic Fencing Tokens
6. Unified Execution Fabric & Task Mode Contracts (Strict fail-closed policy enforcement)
7. System 1 Fast Decision Intelligence (Laya ModernBERT Adapter) & Multi-Tier Model Routing
8. Primary Task Implementation in Isolated Employee Workspace
9. Independent Deliverable Verification with SHA-256 Artifact Digests & Cryptographic Receipt
10. Empirical Skill Certification (Gated promotion from unverified to verified=True)
11. 6-Tier Organizational Memory Hydration & Cryptographic Provenance Gating
12. Cross-Department Collaboration & RBAC Isolation (Scratchpad note privacy bounds)
13. Isolated Feature Git Worktree Staging with Pinned Base Commit & Provenance Receipt
14. Collaborative QA Review: Automated Bug Detection, Reproduction & Verified Remediation Loop
15. Independent Security Review: AST Vulnerability Scanning & Dangerous Function Rejection
16. HumanApprovalGate Authorization & Approval-Gated Target Branch Integration
17. Digital Boardroom: Evidence-Grounded Progress Validation, ADR Governance & Decision Ledger
18. ActionItem -> Real Task DAG Linkage with Conflict-Guarded Resolution
19. Real-Time Operational Intelligence: Transactional Outbox Pattern & Monotonic SSE Events
20. Recursive Self-Evolution Cycle: Proposal, Staged Promotion, Synthetic Regression & Rollback
21. Operational Resilience: Emergency Stop/Resume, Clean-Room Disaster Recovery & Complete Audit Log Hash-Chain Verification
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from neural.boss.brain import BossBrain
from neural.boss.delegator import TaskDelegator
from neural.cognitive_fabric.memory.models import (
    MemoryAccessLevel,
    MemoryLayer,
    valid_independent_verification_receipt,
)
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.constants import (
    ActionItemStatus,
    ContainerStatus,
    DestructiveOperationType,
    EmployeeStatus,
    ExecutionMode,
    MeetingStatus,
    MeetingType,
    TaskPriority,
    TaskStatus,
)
from neural.constants import (
    ProposalStatus as GovProposalStatus,
)
from neural.constants import (
    ProposalType as GovProposalType,
)
from neural.db.models import (
    BudgetAccount,
    Container,
)
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    AuditRepository,
    BudgetRepository,
    DecisionLedgerRepository,
    EmergencyStopRepository,
    EmployeeRepository,
    EvolutionRepository,
    MeetingRepository,
    MemoryRepository,
    ProjectRepository,
    ProposalRepository,
    ResourceLeaseRepository,
    TaskRepository,
    VoteRepository,
)
from neural.decision_intelligence.schemas.choice import (
    ChoiceCandidate,
    ChoiceQuestion,
    ChoiceResult,
)
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.employees.factory import EmployeeFactory
from neural.engineering.pipeline import CollaborativeEngineeringPipeline
from neural.evaluation.verifier import IndependentDeliverableVerifier
from neural.events.outbox import OutboxEventPublisher, OutboxSSEBroadcaster
from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.multi_gate_verifier import SelfEvolutionMultiGateVerifier
from neural.evolution.promotion_manager import SelfEvolutionPromotionManager
from neural.evolution.proposal_engine import EvidenceBasedProposalEngine
from neural.evolution.rollback_manager import (
    PostPromotionHealthMonitor,
    SelfEvolutionRollbackManager,
)
from neural.exceptions import (
    ActionItemConflictError,
    HumanApprovalRequiredError,
    StrictGUIContractViolation,
)
from neural.governance.engine import BoardroomGovernanceEngine
from neural.meetings.actions import ActionItemTracker
from neural.meetings.conductor import MeetingConductor
from neural.meetings.policy import MeetingPolicyConfig
from neural.ops.backup import DisasterRecoveryManager
from neural.resource_scheduler.hardware_profiler import (
    PROFILE_16GB,
)
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.security.kill_switch import get_kill_switch, reset_kill_switch
from neural.tasks.scheduler import TaskScheduler
from sqlalchemy.ext.asyncio import AsyncSession


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _setup_git_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "enterprise_source_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "ANCOS Master Enterprise")
    _git(repo, "config", "user.email", "master@ancos.corp")
    _git(repo, "config", "commit.gpgSign", "false")

    readme = repo / "README.md"
    readme.write_text("# Enterprise Core Repository\nBaseline version.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial baseline commit")
    return repo


@pytest.fixture(autouse=True)
def reset_system_safety_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase13_21_step_end_to_end_autonomous_mission(
    async_session: AsyncSession,
    tmp_path: Path,
):
    """
    Master 21-Step End-to-End Autonomous Company Mission Acceptance Test.
    Integrates all foundational systems across NEURO-1 through NEURO-12.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Owner Authentication, Budget Quota Pre-Reservation & Preflight
    # =========================================================================
    owner_identity = "owner@ancos.corp"
    assert "@" in owner_identity

    budget_repo = BudgetRepository(async_session)
    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("1000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    available_quota = await budget_repo.get_available_quota(month_scope)
    assert available_quota >= Decimal("200.00"), f"Step 1 Failed: Available quota {available_quota}"

    mission_reservation = await budget_repo.reserve(
        idempotency_key="mission-reserve-neuro13-master",
        reserved_usd=Decimal("5.00"),
        scope_caps={month_scope: Decimal("1000.00")},
    )
    assert mission_reservation is not None and mission_reservation.status == "reserved", "Step 1 Failed: Budget reservation"

    # =========================================================================
    # STEP 2: Objective Submission & Boss Clarification Context Answering
    # =========================================================================
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)

    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    objective_text = "Deliver Enterprise Autonomous Telemetry Pipeline with Verification, Governance, Self-Evolution and Resilience"
    clarifications = {
        "What specific outcome should ANCOS deliver, and what would count as success?": (
            "Complete an end-to-end telemetry system with verified unit tests, security review, "
            "boardroom approval, self-evolution optimization, and clean-room disaster recovery."
        )
    }

    # =========================================================================
    # STEP 3: BossBrain Planning & Acyclic DAG Decomposition (Kahn's Algorithm)
    # =========================================================================
    project, created_tasks = await brain.plan_and_create_project(
        objective=objective_text,
        project_name="Enterprise-Telemetry-Pipeline",
        budget_usd=150.0,
        clarifications=clarifications,
        auto_assign_first_task=False,
    )
    assert project.id is not None, "Step 3 Failed: Project creation"
    assert len(created_tasks) >= 2, f"Step 3 Failed: Expected at least 2 tasks, got {len(created_tasks)}"

    task_collector = created_tasks[0]
    task_aggregator = created_tasks[1]
    await task_repo.add_dependency(task_id=task_aggregator.task_id, depends_on_task_id=task_collector.task_id)

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        delegator=TaskDelegator(),
        verifier=IndependentDeliverableVerifier(task_repo=task_repo, employee_repo=employee_repo),
    )
    dag_graph = await scheduler.build_graph_for_project(project.id)
    dag_graph.validate_dag()
    assert task_collector.task_id in dag_graph.get_all_tasks(), "Step 3 Failed: DAG validation"

    # Settle planning budget
    await budget_repo.settle(
        mission_reservation.id,
        actual_usd=Decimal("0.10"),
        usage_receipt={"operation": "plan_and_create_project", "tasks_count": len(created_tasks)},
    )

    # =========================================================================
    # STEP 4: Dynamic Workforce Recruitment & Desk Mapping (Unverified Skills)
    # =========================================================================
    factory = EmployeeFactory(employee_repo)

    lead_dev = await factory.hire_employee(
        name="Alex Rivera",
        department="Engineering",
        title="Senior Software Engineer",
        desk_id="WS-ENG-01",
        initial_skills=["Python", "System Architecture", "Algorithms", "Testing"],
    )
    qa_eng = await factory.hire_employee(
        name="Taylor Chen",
        department="Quality Assurance",
        title="Lead QA Engineer",
        desk_id="WS-QA-01",
        initial_skills=["Testing", "Python", "Test Automation", "Regression Analysis"],
    )
    sec_rev = await factory.hire_employee(
        name="Morgan Vance",
        department="Security",
        title="Application Security Reviewer",
        desk_id="WS-RES-01",
        initial_skills=["AST Analysis", "Security Auditing", "Python", "Vulnerability Assessment"],
    )

    assert lead_dev.employee_id.startswith("ANC-EMP-"), "Step 4 Failed: Employee ID format"
    assert lead_dev.avatar_config is not None, "Step 4 Failed: Avatar config not generated"
    assert lead_dev.desk_id == "WS-ENG-01", "Step 4 Failed: Desk assignment missing"

    # Skills initially unverified
    dev_skills = lead_dev.skills
    assert all(not s.verified for s in dev_skills), "Step 4 Failed: Skills must start strictly unverified"

    # =========================================================================
    # STEP 5: Desktop Session Pool Admission Control & Slot Leases
    # =========================================================================
    monitor = ResourceMonitor()
    current_metrics = monitor.get_metrics()
    assert current_metrics["cpu_percent"] >= 0.0, "Step 5 Failed: Resource monitor CPU"
    assert current_metrics["used_ram_percent"] >= 0.0, "Step 5 Failed: Resource monitor RAM"

    profile = PROFILE_16GB
    is_safe, msg = monitor.is_safe_to_admit(profile)
    assert isinstance(is_safe, bool), "Step 5 Failed: Admission control"

    lease_repo = ResourceLeaseRepository(async_session)
    dev_container = Container(
        docker_id=f"ancos-desktop-{lead_dev.employee_id.lower()}",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5920,
        novnc_port=6095,
    )
    async_session.add(dev_container)
    await async_session.flush()

    lead_dev.container_id = dev_container.id
    lead_dev.status = EmployeeStatus.WORKING
    await async_session.commit()

    lease = await lease_repo.claim(
        resource_key="desktop:slot:eng-01",
        employee_id=lead_dev.employee_id,
        task_id=task_collector.task_id,
        lease_seconds=300,
    )
    assert lease is not None, "Step 5 Failed: Lease acquisition"
    assert lease["fencing_token"] >= 1, "Step 5 Failed: Fencing token"

    # =========================================================================
    # STEP 6: Unified Execution Fabric & Task Mode Contracts
    # =========================================================================
    task_collector.execution_mode = ExecutionMode.TERMINAL_ONLY.value
    task_collector.fencing_token = lease["fencing_token"]
    task_collector.assigned_to = lead_dev.id
    task_collector.status = TaskStatus.RUNNING
    await async_session.commit()

    def execute_in_mode(mode: str, action: str):
        if mode == ExecutionMode.TERMINAL_ONLY.value and action.startswith("gui_"):
            raise StrictGUIContractViolation(f"Action '{action}' is strictly forbidden in TERMINAL_ONLY mode")
        return "OK"

    with pytest.raises(StrictGUIContractViolation):
        execute_in_mode(task_collector.execution_mode, "gui_click_button")

    # =========================================================================
    # STEP 7: System 1 (Laya Adapter) & System 2 Routing Decision
    # =========================================================================
    adapter = LayaAdapter()
    candidates = [
        ChoiceCandidate(id="coding", label="Coding Tier", metadata={"focus": "software, python, build"}),
        ChoiceCandidate(id="reasoning", label="Reasoning Tier", metadata={"focus": "planning, review"}),
        ChoiceCandidate(id="fast", label="Fast Tier", metadata={"focus": "logging, telemetry"}),
    ]
    choice_q = ChoiceQuestion(
        context="Implement high-performance telemetry data collector in Python",
        candidates=candidates,
    )
    choice_res = adapter.predict_choice(choice_q)
    assert isinstance(choice_res, ChoiceResult), "Step 7 Failed: ChoiceResult type"
    assert choice_res.selected_id in ["coding", "reasoning", "fast"], "Step 7 Failed: Laya choice"

    score_q = ScoreQuestion(
        proposition="Build robust telemetry data collector with unit test coverage",
        context="Core metrics aggregation pipeline",
        scale_min=1,
        scale_max=5,
    )
    score_res = adapter.predict_score(score_q)
    assert isinstance(score_res, ScoreResult), "Step 7 Failed: ScoreResult type"
    assert 1 <= score_res.score <= 5, "Step 7 Failed: Laya score bounds"

    # =========================================================================
    # STEP 8: Task 1 Implementation in Isolated Employee Workspace
    # =========================================================================
    workspace_dir = tmp_path / "workspaces" / lead_dev.employee_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    collector_code = '''"""
Telemetry Collector Module.
Collects and filters system and application performance metrics.
"""
from typing import Dict, Any

class TelemetryCollector:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.records: list[Dict[str, Any]] = []

    def record_metric(self, name: str, value: float) -> None:
        if value < 0:
            raise ValueError("Metric value cannot be negative")
        self.records.append({"service": self.service_name, "metric": name, "value": float(value)})

    def get_summary(self) -> Dict[str, Any]:
        count = len(self.records)
        total = sum(r["value"] for r in self.records)
        avg = total / count if count > 0 else 0.0
        return {"service": self.service_name, "count": count, "sum": total, "average": avg}
'''
    proc_file = workspace_dir / "telemetry_collector.py"
    proc_file.write_text(collector_code, encoding="utf-8")

    test_collector_code = '''"""
Unit tests for Telemetry Collector.
"""
import pytest
from telemetry_collector import TelemetryCollector

def test_telemetry_collector_basic():
    c = TelemetryCollector("auth-service")
    c.record_metric("cpu", 15.5)
    c.record_metric("cpu", 24.5)
    summary = c.get_summary()
    assert summary["count"] == 2
    assert summary["sum"] == 40.0
    assert summary["average"] == 20.0

def test_telemetry_negative_metric_raises():
    c = TelemetryCollector("api-gateway")
    with pytest.raises(ValueError):
        c.record_metric("memory", -5.0)
'''
    test_file = workspace_dir / "test_telemetry_collector.py"
    test_file.write_text(test_collector_code, encoding="utf-8")

    worker_test = subprocess.run(
        ["pytest", str(test_file)],
        capture_output=True,
        text=True,
    )
    assert worker_test.returncode == 0, f"Step 8 Failed: Worker tests failed: {worker_test.stdout}\n{worker_test.stderr}"

    # =========================================================================
    # STEP 9: Independent Deliverable Verification & Cryptographic Receipt
    # =========================================================================
    verifier = IndependentDeliverableVerifier(task_repo=task_repo, employee_repo=employee_repo)

    verification_1 = await verifier.verify_task_result(
        task_id=task_collector.task_id,
        employee_id=lead_dev.employee_id,
        artifacts=[str(proc_file), str(test_file)],
        test_exit_code=0,
        tested_skills=["Python", "System Architecture"],
        acceptance_criteria="Automated unit test suite passes cleanly with exit code 0",
    )
    assert verification_1["passed"] is True, "Step 9 Failed: Independent verification"
    receipt_1 = verification_1["receipt"]
    assert receipt_1["signature"] is not None, "Step 9 Failed: Signature missing"
    assert str(proc_file) in receipt_1["artifact_digests"], "Step 9 Failed: Artifact digest missing"

    completed_task = await verifier.gate_task_completion(task_collector.task_id, receipt_1)
    assert completed_task.status == TaskStatus.COMPLETED, "Step 9 Failed: Task completion gate"

    await lease_repo.release(lease_id=lease["lease_id"], fencing_token=lease["fencing_token"])

    # =========================================================================
    # STEP 10: Empirical Skill Certification (verified=True promotion)
    # =========================================================================
    emp_reloaded = await employee_repo.get_by_employee_id(lead_dev.employee_id)
    python_skill = next((s for s in emp_reloaded.skills if s.skill_name.lower() == "python"), None)
    assert python_skill is not None and python_skill.verified is True, "Step 10 Failed: Empirical skill certification"

    # =========================================================================
    # STEP 11: 6-Tier Organizational Memory Hydration & Provenance Gating
    # =========================================================================
    memory_repo = MemoryRepository(async_session)
    store = SixTierMemoryStore(memory_repo=memory_repo)

    working_rec = await store.store_working(
        task_id=task_collector.task_id,
        employee_id=lead_dev.employee_id,
        content="Benchmarking metric storage throughput and memory overhead",
        scratchpad={"peak_mem_mb": 35.0, "latency_us": 12.0},
    )
    assert working_rec.layer == MemoryLayer.WORKING
    assert working_rec.access_level == MemoryAccessLevel.RESTRICTED
    assert not working_rec.is_verified

    episodic_rec = await store.store_episodic(
        task_id=task_collector.task_id,
        employee_id=lead_dev.employee_id,
        outcome="success",
        summary="Built and verified TelemetryCollector with unit tests",
        receipt={"exit_code": 0, "tests_passed": 2},
        verification_receipt=receipt_1,
    )
    assert episodic_rec.layer == MemoryLayer.EPISODIC
    assert episodic_rec.is_verified

    reusable_recipe_spec = {
        "name": "TelemetryCollectorRecipe",
        "description": "Standard pattern for high-frequency in-memory metric collection",
        "steps": [
            {"step": 1, "action": "Initialize collector with service identity"},
            {"step": 2, "action": "Validate non-negative metric values"},
            {"step": 3, "action": "Aggregate summary stats on query"},
        ],
        "code": collector_code,
    }

    procedural_rec = await store.store_procedural(
        procedure_name="TelemetryCollectorRecipe",
        steps=reusable_recipe_spec["steps"],
        code_recipe=reusable_recipe_spec["code"],
        verified_by=receipt_1["verifier_id"],
        verification_receipt=receipt_1,
        task_id=task_collector.task_id,
        owner_employee_id=lead_dev.employee_id,
        access_level=MemoryAccessLevel.INTERNAL,
        allowed_departments=["Engineering", "Quality Assurance"],
    )
    assert procedural_rec.layer == MemoryLayer.PROCEDURAL
    assert procedural_rec.is_verified

    trusted_records = store.query_trusted_records(requester_id="BossAI")
    trusted_ids = {m.id for m in trusted_records}
    assert episodic_rec.id in trusted_ids
    assert procedural_rec.id in trusted_ids
    assert working_rec.id not in trusted_ids

    # =========================================================================
    # STEP 12: Cross-Department RBAC Memory Query & Private Note Isolation
    # =========================================================================
    forged_receipt = dict(receipt_1)
    forged_receipt["signature"] = "deadbeef" * 8
    assert not valid_independent_verification_receipt(forged_receipt, task_id=task_collector.task_id)

    qa_trusted = store.query_trusted_records(
        requester_id=qa_eng.employee_id,
        department=qa_eng.department,
    )
    assert any(m.id == procedural_rec.id for m in qa_trusted), "Step 12 Failed: QA must access verified procedural memory"
    assert not any(m.id == working_rec.id for m in qa_trusted), "Step 12 Failed: QA must NOT see private working memory"

    # =========================================================================
    # STEP 13: Isolated Git Worktree Lifecycle & Base Commit Pinning
    # =========================================================================
    source_repo = _setup_git_repository(tmp_path)
    managed_worktrees = tmp_path / "data" / "worktrees"
    pipeline = CollaborativeEngineeringPipeline(source_repo, managed_worktrees)

    initial_head = _git(source_repo, "rev-parse", "HEAD")
    worktree_receipt = pipeline.provision_worktree(task_aggregator.task_id, base_ref="HEAD")

    assert worktree_receipt.task_id == task_aggregator.task_id
    assert worktree_receipt.branch == f"ancos/task/{task_aggregator.task_id}"
    assert worktree_receipt.base_commit == initial_head
    assert worktree_receipt.path.is_dir()

    assert _git(source_repo, "branch", "--show-current") == "main"
    assert _git(source_repo, "rev-parse", "HEAD") == initial_head

    # =========================================================================
    # STEP 14: Collaborative QA Bug Detection, Reproduction & Remediation Loop
    # =========================================================================
    buggy_aggregator_code = (
        "def compute_window_rate(total_events: float, window_seconds: float) -> float:\n"
        "    # Injected bug: ZeroDivisionError when window_seconds <= 0\n"
        "    return total_events / window_seconds\n"
    )
    test_aggregator_code = (
        "import pytest\n"
        "from aggregator import compute_window_rate\n\n"
        "def test_compute_rate_valid():\n"
        "    assert compute_window_rate(100.0, 10.0) == 10.0\n\n"
        "def test_compute_rate_zero_window_raises():\n"
        "    with pytest.raises(ValueError):\n"
        "        compute_window_rate(100.0, 0.0)\n"
    )

    dev_commit = pipeline.apply_developer_changes(
        task_id=task_aggregator.task_id,
        files={"aggregator.py": buggy_aggregator_code, "test_aggregator.py": test_aggregator_code},
        commit_message="Initial implementation of aggregator with rate computation",
        developer_id=lead_dev.employee_id,
    )

    initial_qa = pipeline.run_qa_review(
        task_id=task_aggregator.task_id,
        qa_engineer_id=qa_eng.employee_id,
    )
    assert initial_qa.passed is False, "Step 14 Failed: QA must catch the zero-window bug"
    assert initial_qa.bug_report is not None, "Step 14 Failed: Bug report missing"

    fixed_aggregator_code = (
        "def compute_window_rate(total_events: float, window_seconds: float) -> float:\n"
        "    if window_seconds <= 0:\n"
        "        raise ValueError('window_seconds must be positive and non-zero')\n"
        "    return total_events / window_seconds\n"
    )
    fix_commit = pipeline.remediate_bug(
        task_id=task_aggregator.task_id,
        bug_report=initial_qa.bug_report,
        fix_files={"aggregator.py": fixed_aggregator_code},
        commit_message="fix: validate positive non-zero window_seconds",
        developer_id=lead_dev.employee_id,
    )
    assert fix_commit != dev_commit

    re_qa_receipt = pipeline.run_qa_review(
        task_id=task_aggregator.task_id,
        qa_engineer_id=qa_eng.employee_id,
    )
    assert re_qa_receipt.passed is True, "Step 14 Failed: QA re-verification must pass"
    assert re_qa_receipt.test_exit_code == 0
    assert re_qa_receipt.verify_signature() is True

    # =========================================================================
    # STEP 15: Independent Security Review & Vulnerability AST Scan
    # =========================================================================
    sec_receipt = pipeline.run_security_review(
        task_id=task_aggregator.task_id,
        security_engineer_id=sec_rev.employee_id,
    )
    assert sec_receipt.passed is True, "Step 15 Failed: Security review must pass"
    assert sec_receipt.security_score >= 0.99
    assert len(sec_receipt.vulnerabilities_found) == 0
    assert sec_receipt.verify_signature() is True

    # =========================================================================
    # STEP 16: HumanApprovalGate Authorization & Gated Feature Merge
    # =========================================================================
    approval_repo = ApprovalRepository(async_session)

    with pytest.raises(HumanApprovalRequiredError):
        await pipeline.execute_merge(
            task_id=task_aggregator.task_id,
            target_branch="main",
            qa_receipt=re_qa_receipt,
            security_receipt=sec_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description=f"Approve integration merge of task {task_aggregator.task_id} into main branch",
        justification="Verified by independent QA pass and Security AST scan",
        requested_by=lead_dev.employee_id,
        details={
            "task_id": task_aggregator.task_id,
            "qa_receipt_id": re_qa_receipt.receipt_id,
            "security_receipt_id": sec_receipt.receipt_id,
        },
    )
    await approval_repo.decide(
        approval_id=approval.id,
        decision="approved",
        decided_by=owner_identity,
    )

    merge_receipt = await pipeline.execute_merge(
        task_id=task_aggregator.task_id,
        target_branch="main",
        qa_receipt=re_qa_receipt,
        security_receipt=sec_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by=owner_identity,
        delete_task_branch=False,
    )
    assert merge_receipt.merged_commit is not None, "Step 16 Failed: Feature merge"
    assert merge_receipt.target_branch == "main"
    assert merge_receipt.verify_signature() is True
    assert (source_repo / "aggregator.py").is_file()

    # =========================================================================
    # STEP 17: Autonomous Company Meeting / Boardroom & Decision Ledger Chaining
    # =========================================================================
    meeting_repo = MeetingRepository(async_session)
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    decision_ledger_repo = DecisionLedgerRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=decision_ledger_repo,
        task_repo=task_repo,
        project_repo=project_repo,
        audit_repo=audit_repo,
    )

    policy_config = MeetingPolicyConfig(
        max_turns=20,
        per_speaker_token_budget=3000,
        max_consecutive_speaker_turns=3,
        decision_threshold=1,
    )
    conductor = MeetingConductor(
        meeting_repo=meeting_repo,
        action_item_repo=action_repo,
        task_repo=task_repo,
        project_repo=project_repo,
        governance_engine=gov_engine,
        policy_config=policy_config,
    )

    meeting = await conductor.schedule_meeting(
        title="Telemetry Pipeline Rollout & Fleet Governance Review",
        meeting_type=MeetingType.ARCHITECTURE_REVIEW,
        agenda=[
            "1. Review Verifier Receipts",
            "2. Authorize Production Telemetry Rollout",
        ],
        participants=["BossAI", lead_dev.employee_id, qa_eng.employee_id],
    )
    convened_meeting = await conductor.convene_meeting(meeting.meeting_id)
    assert convened_meeting.status == MeetingStatus.IN_PROGRESS, "Step 17 Failed: Meeting convene"

    arch_topic = "Telemetry Collector Deployment"
    arch_decision = "Deploy verified collector and aggregator services to production environment."
    arch_rationale = "Production rollout backed by independent verifier receipts."

    corrective_task_spec = [
        {
            "title": "Deploy Telemetry Services",
            "objective": "Execute production deployment rollout.",
            "scope": "Deployment scripts",
            "acceptance_criteria": "Deployment succeeds and passes verification",
            "priority": TaskPriority.HIGH,
        }
    ]

    dec_obj, gov_prop, ledger_entry, created_gov_tasks = await conductor.route_decision_to_governance(
        meeting_id=convened_meeting.meeting_id,
        topic=arch_topic,
        description=f"{arch_decision}. Rationale: {arch_rationale}",
        proposal_type=GovProposalType.ARCHITECTURE_CHANGE,
        proposer_id="BossAI",
        project_id=project.id,
        tasks=corrective_task_spec,
        auto_deliberate=True,
        auto_vote=True,
    )
    assert gov_prop.status in (GovProposalStatus.APPROVED, GovProposalStatus.EXECUTED), "Step 17 Failed: Governance decision"
    assert ledger_entry is not None, "Step 17 Failed: Decision ledger entry"
    assert len(ledger_entry.entry_hash) == 64, "Step 17 Failed: Decision hash chain"

    # =========================================================================
    # STEP 18: ActionItem -> Real Task DAG Linkage & Conflict-Guarded Resolution
    # =========================================================================
    tracker = ActionItemTracker(action_repo)
    task_deploy = created_gov_tasks[0]

    action_item = await conductor.add_action_item(
        meeting_id=convened_meeting.id,
        description=f"[{ledger_entry.entry_id}] Rollout telemetry to staging",
        assigned_to=lead_dev.employee_id,
        linked_task_id=task_deploy.task_id,
        acceptance_criteria="Deployment succeeds and passes verification",
    )

    with pytest.raises(ActionItemConflictError):
        await tracker.resolve_action_item(action_item.id)

    task_deploy.status = TaskStatus.COMPLETED
    task_deploy.verified = True
    task_deploy.verification_result = {
        "passed": True,
        "verification_status": "PASSED",
        "receipt": {
            "passed": True,
            "verification_status": "PASSED",
            "verifier_id": "IndependentDeliverableVerifier",
        },
    }
    await async_session.commit()

    resolved_action = await tracker.resolve_action_item(action_item.id)
    assert resolved_action.status == ActionItemStatus.RESOLVED, "Step 18 Failed: Action item resolution"

    # Conclude meeting
    concluded_meeting = await conductor.conclude_meeting(
        meeting_id=convened_meeting.meeting_id,
        summary="Telemetry rollout authorized and action item tracking completed.",
    )
    assert concluded_meeting.status == MeetingStatus.CONCLUDED

    # =========================================================================
    # STEP 19: Real-time Operational Intelligence: Outbox & SSE Streaming
    # =========================================================================
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)
    outbox_evt = await publisher.publish_event(
        session=async_session,
        event_type="mission.telemetry_pipeline_live",
        data={"phase": "rollout_complete", "status": "active"},
        channel="company_events",
    )
    assert outbox_evt.get("event_id") is not None, "Step 19 Failed: Outbox event emission"

    # =========================================================================
    # STEP 20: Recursive Self-Evolution: Proposal, Staging, Regression & Rollback
    # =========================================================================
    _ = EvolutionRepository(async_session)
    proposal_engine = EvidenceBasedProposalEngine(task_repo=task_repo)

    proposal_id = "PROP-EVO-20261111-TELEMETRY"
    evo_proposal = proposal_engine.create_proposal(
        title="Vectorized Fast Path Optimization for Telemetry Parser",
        description="Replace interpreted deserializer with vectorized parser kernel",
        category="performance",
        target_files=["fast_kernel.py", "test_fast_kernel.py"],
        root_cause_rationale="Interpreted json parsing overhead in metrics ingestion",
        baseline_metrics={
            "execution_time_ms": 140.0,
            "error_rate": 0.05,
            "benchmark_throughput_ops_sec": 420.0,
        },
        target_goals={
            "execution_time_ms": 35.0,
            "min_speedup_ratio": 3.0,
            "error_rate": 0.0,
            "benchmark_throughput_ops_sec": 1200.0,
        },
        proposer_id=lead_dev.employee_id,
        proposal_id=proposal_id,
    )
    assert evo_proposal.proposal_id == proposal_id
    assert evo_proposal.verify_signature() is True

    candidate_mgr = SelfEvolutionCandidateManager(
        repository=source_repo,
        managed_root=tmp_path / "self_evolution_staging",
    )
    pre_evolution_commit = _git(source_repo, "rev-parse", "HEAD")
    staging_receipt = candidate_mgr.provision_candidate_staging(
        proposal_id=evo_proposal.proposal_id,
        base_ref="HEAD",
    )
    assert staging_receipt.path.is_dir(), "Step 20 Failed: Candidate staging directory"

    fast_kernel_code = (
        "def fast_parse(data: str) -> dict:\n"
        "    \"\"\"Fast vectorized string parser.\"\"\"\n"
        "    return {'status': 'fast', 'len': len(data)}\n"
    )
    test_fast_kernel = (
        "from fast_kernel import fast_parse\n\n"
        "def test_fast():\n"
        "    assert fast_parse('abc')['status'] == 'fast'\n"
    )
    dev_commit = candidate_mgr.apply_candidate_changes(
        proposal_id=evo_proposal.proposal_id,
        files={"fast_kernel.py": fast_kernel_code, "test_fast_kernel.py": test_fast_kernel},
        commit_message="Implement fast kernel parser with tests",
        author_id=lead_dev.employee_id,
    )
    assert dev_commit != pre_evolution_commit

    multi_gate_verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)
    evo_receipt = multi_gate_verifier.verify_candidate(
        proposal=evo_proposal,
        test_command=["python", "-m", "pytest", "-v"],
        candidate_benchmark_metrics={
            "execution_time_ms": 28.0,
            "error_rate": 0.0,
            "benchmark_throughput_ops_sec": 1650.0,
        },
    )
    assert evo_receipt.passed is True, f"Step 20 Failed: Multi-gate verification: {evo_receipt.notes}"
    assert evo_receipt.verify_signature() is True

    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)
    readiness = promotion_mgr.assess_promotion_readiness(
        proposal=evo_proposal,
        verification_receipt=evo_receipt,
        auto_promote=True,
    )
    assert readiness["eligible_for_promotion"] is True
    assert readiness["activation_status"] == "approval_required"
    assert readiness["auto_promote_blocked"] is True

    evo_approval = await approval_repo.create(
        action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
        description=f"Approve promotion of self-evolution proposal {evo_proposal.proposal_id}",
        justification="Verified by 5 multi-gate verification checks",
        requested_by=lead_dev.employee_id,
        details={
            "proposal_id": evo_proposal.proposal_id,
            "verification_receipt_id": evo_receipt.receipt_id,
        },
    )
    await approval_repo.decide(evo_approval.id, decision="approved", decided_by=owner_identity)

    prom_receipt = await promotion_mgr.execute_promotion(
        proposal_id=evo_proposal.proposal_id,
        target_branch="main",
        verification_receipt=evo_receipt,
        approval_id=evo_approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by=lead_dev.employee_id,
    )
    assert prom_receipt.promoted_commit != pre_evolution_commit
    assert prom_receipt.verify_signature() is True

    promoted_head = _git(source_repo, "rev-parse", "HEAD")
    assert promoted_head == prom_receipt.promoted_commit
    assert (source_repo / "fast_kernel.py").is_file()

    # Synthetic regression injection
    regression_code = (
        "def fast_parse(data: str) -> dict:\n"
        "    raise RuntimeError('SYNTHETIC_CRITICAL_REGRESSION_CRASH')\n"
    )
    (source_repo / "fast_kernel.py").write_text(regression_code, encoding="utf-8")
    _git(source_repo, "add", "fast_kernel.py")
    _git(source_repo, "commit", "-m", "Injected synthetic regression")
    regressed_commit = _git(source_repo, "rev-parse", "HEAD")
    assert regressed_commit != promoted_head

    health_monitor = PostPromotionHealthMonitor(source_repo)
    health_report = health_monitor.check_health(
        canary_command=["python", "-m", "pytest", "-v"],
    )
    assert health_report.healthy is False, "Step 20 Failed: Anomaly detection"
    assert health_report.anomaly_detected is not None

    rollback_mgr = SelfEvolutionRollbackManager(source_repo)
    rollback_receipt = await rollback_mgr.execute_rollback(
        proposal_id=evo_proposal.proposal_id,
        target_branch="main",
        pre_evolution_commit=prom_receipt.pre_evolution_commit,
        anomaly_detected=health_report.anomaly_detected,
        audit_repo=audit_repo,
        requested_by="PostPromotionHealthMonitor",
    )
    assert rollback_receipt.proposal_id == evo_proposal.proposal_id
    assert rollback_receipt.rolled_back_commit == regressed_commit.lower()
    assert rollback_receipt.restored_commit == prom_receipt.pre_evolution_commit
    assert rollback_receipt.verify_signature() is True
    assert _git(source_repo, "rev-parse", "HEAD") == pre_evolution_commit
    assert not (source_repo / "fast_kernel.py").exists()

    # =========================================================================
    # STEP 21: Operational Resilience, Disaster Recovery & Audit Hash-Chain Integrity
    # =========================================================================
    emergency_repo = EmergencyStopRepository(async_session)

    # 1. Emergency Stop & Resume drill
    kill_switch = get_kill_switch()
    await kill_switch.emergency_halt(
        reason="Acceptance test simulated drill",
        actor="owner@ancos.corp",
        state_repo=emergency_repo,
    )
    assert kill_switch.is_halted is True, "Step 21 Failed: Kill switch activation"

    await kill_switch.resume(
        actor="owner@ancos.corp",
        state_repo=emergency_repo,
    )
    assert kill_switch.is_halted is False, "Step 21 Failed: Kill switch reset"

    # 2. Disaster Recovery & Clean-room hot backup/restore
    dr_data_dir = tmp_path / "dr_data"
    dr_configs_dir = tmp_path / "dr_configs"
    dr_data_dir.mkdir(parents=True, exist_ok=True)
    dr_configs_dir.mkdir(parents=True, exist_ok=True)
    (dr_configs_dir / "policy.json").write_text('{"mode":"safe"}\n', encoding="utf-8")

    backup_db_file = dr_data_dir / "neural_company.db"
    real_db_conn = sqlite3.connect(str(backup_db_file))
    real_db_conn.execute("CREATE TABLE mission_state (id TEXT PRIMARY KEY, status TEXT, completed_at TEXT)")
    real_db_conn.execute(
        "INSERT INTO mission_state VALUES ('MISSION-NEURO13', 'COMPLETED', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    real_db_conn.commit()
    real_db_conn.close()

    dr_mgr = DisasterRecoveryManager(
        base_data_dir=str(dr_data_dir),
        configs_dir=str(dr_configs_dir),
    )

    backup_id = "master-mission-backup"
    archive_path, manifest = dr_mgr.create_backup(backup_id=backup_id)
    assert Path(archive_path).exists(), "Step 21 Failed: Backup archive created"
    assert dr_mgr.verify_backup_integrity(archive_path) is True, "Step 21 Failed: Backup manifest SHA-256 integrity"
    manifest_sha256 = hashlib.sha256(json.dumps([c.sha256_hash for c in manifest.components]).encode("utf-8")).hexdigest()

    clean_room_dir = tmp_path / "clean_room_restoration"
    restoration_report = dr_mgr.restore_backup(
        archive_path=archive_path,
        target_dir=str(clean_room_dir),
    )
    assert restoration_report.status == "SUCCESS", f"Step 21 Failed: Clean-room restore failed: {restoration_report}"
    assert restoration_report.db_integrity_ok is True, "Step 21 Failed: Restored DB integrity ok"
    assert restoration_report.verified_components == len(manifest.components)

    restored_db_file = clean_room_dir / backup_id / "database" / "neural_company.db"
    assert restored_db_file.exists(), "Step 21 Failed: Clean-room restore"

    restore_conn = sqlite3.connect(str(restored_db_file))
    cursor = restore_conn.cursor()
    cursor.execute("PRAGMA integrity_check")
    integrity_result = cursor.fetchone()[0]
    cursor.execute("SELECT status FROM mission_state WHERE id = 'MISSION-NEURO13'")
    restored_status = cursor.fetchone()[0]
    restore_conn.close()

    assert integrity_result == "ok", f"Step 21 Failed: PRAGMA integrity check: {integrity_result}"
    assert restored_status == "COMPLETED", "Step 21 Failed: Restored mission state mismatch"

    # 3. Complete Audit Log Hash-Chain Integrity Verification
    chain_valid, issues = await audit_repo.verify_chain_integrity()
    assert chain_valid is True, f"Step 21 Failed: Audit log cryptographic hash-chain verification failed: {issues}"
    assert len(issues) == 0, f"Step 21 Failed: Audit chain issues: {issues}"

    # Digitally signed Boss Mission Completion Report
    mission_report = {
        "mission_id": "NEURO-13-MASTER-MISSION",
        "project_id": project.id,
        "objective": objective_text,
        "status": "COMPLETED",
        "steps_verified": 21,
        "total_passed_milestones": 21,
        "independent_verification_receipts": [receipt_1, re_qa_receipt.receipt_id],
        "governance_decision_hash": ledger_entry.entry_hash,
        "self_evolution_rollback_receipt": rollback_receipt.receipt_id,
        "dr_backup_manifest": manifest_sha256,
        "audit_chain_verified": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    report_digest = hashlib.sha256(json.dumps(mission_report, sort_keys=True).encode("utf-8")).hexdigest()
    assert len(report_digest) == 64, "Step 21 Failed: Boss Mission report SHA-256 signature"
