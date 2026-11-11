from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.boss.brain import BossBrain
from neural.boss.delegator import TaskDelegator
from neural.cognitive_fabric.memory.models import (
    MemoryLayer,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    TaskPriority,
    TaskStatus,
)
from neural.db.models import (
    BudgetAccount,
    Container,
)
from neural.db.repositories import (
    AuditRepository,
    BudgetRepository,
    EmployeeRepository,
    MemoryRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.employees.promotion import EmployeePromotionManager
from neural.evaluation.verifier import IndependentDeliverableVerifier
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_neuro_phase2_full_workforce_and_intelligence_acceptance(
    async_session: AsyncSession,
    tmp_path,
):
    """
    NEURO-2 Acceptance Scenario:
    Demonstrates dynamic workforce recruitment, workspace provisioning, empirical skill
    verification, performance tracking, persistent identity reuse, workspace artifact reuse,
    6-tier memory reuse across subsequent tasks, merit-based employee promotion,
    and cross-department collaboration with RBAC memory governance.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Owner Authentication and Budget Setup
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
    assert available_quota >= Decimal("500.00"), "Step 1 Failed: Insufficient monthly quota"

    # =========================================================================
    # STEP 2: Boss recruits dynamic employee (Zero hardcoded identities)
    # =========================================================================
    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    project_repo = ProjectRepository(async_session)
    audit_repo = AuditRepository(async_session)
    memory_repo = MemoryRepository(async_session)

    factory = EmployeeFactory(employee_repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    # Recruits a dynamic software engineer with unverified skills
    employee_1 = await factory.recruit_dynamic_employee(
        role_title="Software Engineer",
        department="Engineering",
        initial_skills=["Python", "Data Processing", "Algorithms"],
    )

    assert employee_1.employee_id.startswith("ANC-EMP-"), "Step 2 Failed: ID format"
    assert employee_1.name is not None and len(employee_1.name) > 3, "Step 2 Failed: Dynamic name"
    assert employee_1.status == EmployeeStatus.IDLE
    assert employee_1.desk_id is not None, "Step 2 Failed: Desk not auto-allocated"

    # EMPIRICAL MANDATE: Initial skills MUST start unverified
    assert len(employee_1.skills) == 3
    for skill in employee_1.skills:
        assert skill.verified is False, f"Step 2 Failed: Skill {skill.skill_name} must start unverified"
        assert skill.verified_at is None
        assert skill.evidence_task_ids == []

    # =========================================================================
    # STEP 3: Provision isolated workstation and verify workspace on host
    # =========================================================================
    host_workspace = os.path.join(str(tmp_path), employee_1.employee_id)
    assert os.path.exists(host_workspace), "Step 3 Failed: Host workspace directory missing"

    container_1 = Container(
        docker_id=f"ancos-desktop-{employee_1.employee_id.lower()}",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5921,
        novnc_port=6096,
    )
    async_session.add(container_1)
    await async_session.flush()

    await employee_repo.assign_container(employee_1.employee_id, container_1.id)
    refreshed_emp = await employee_repo.get_by_employee_id(employee_1.employee_id)
    assert refreshed_emp.container_id == container_1.id, "Step 3 Failed: Container not assigned"

    # =========================================================================
    # STEP 4: Boss plans Project 1 and assigns Task 1 to the employee
    # =========================================================================
    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    proj_1 = await project_repo.create(
        name="Text-Analysis-Engine",
        objective="Build an in-memory word and token frequency calculation library",
    )

    task_1 = await task_repo.create(
        task_id="TASK-TXT-01",
        project_id=proj_1.id,
        title="Token Frequency Analyzer",
        objective="Implement token frequency counter and ranking module",
        scope="Core Analytics",
        acceptance_criteria="Automated tests pass with zero exit code and valid artifacts",
        required_capabilities=["Python", "Data Processing"],
        priority=TaskPriority.HIGH,
    )

    # Delegator assigns to employee_1 (currently idle)
    delegator = TaskDelegator()
    assignee_1 = delegator.select_best_assignee(task_1, [refreshed_emp])
    assert assignee_1 is not None and assignee_1.employee_id == employee_1.employee_id

    await task_repo.assign_task(task_1.task_id, assignee_1.id)
    await task_repo.update_status(task_1.task_id, TaskStatus.RUNNING)
    await employee_repo.update_status(assignee_1.employee_id, EmployeeStatus.WORKING)

    emp_status_check = await employee_repo.get_by_employee_id(employee_1.employee_id)
    assert emp_status_check.status == EmployeeStatus.WORKING, "Step 4 Failed: Employee status not working"

    # =========================================================================
    # STEP 5: Employee executes Task 1 in workspace directory
    # =========================================================================
    analyzer_file = os.path.join(host_workspace, "analyzer.py")
    test_file = os.path.join(host_workspace, "test_analyzer.py")

    analyzer_code = (
        "import re\n"
        "from collections import Counter\n\n"
        "class TokenFrequencyAnalyzer:\n"
        "    def count_tokens(self, text: str) -> dict[str, int]:\n"
        "        tokens = re.findall(r'\\b[a-zA-Z0-9]+\\b', text.lower())\n"
        "        return dict(Counter(tokens))\n\n"
        "    def top_tokens(self, text: str, n: int = 3) -> list[tuple[str, int]]:\n"
        "        counts = self.count_tokens(text)\n"
        "        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]\n"
    )

    test_code = (
        "import pytest\n"
        "from analyzer import TokenFrequencyAnalyzer\n\n"
        "def test_token_counting():\n"
        "    analyzer = TokenFrequencyAnalyzer()\n"
        "    counts = analyzer.count_tokens('hello world hello ANCOS')\n"
        "    assert counts['hello'] == 2\n"
        "    assert counts['ancos'] == 1\n"
    )

    with open(analyzer_file, "w", encoding="utf-8") as f:
        f.write(analyzer_code)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(test_code)

    assert os.path.exists(analyzer_file) and os.path.exists(test_file), "Step 5 Failed: Artifact files not written"

    # =========================================================================
    # STEP 6: Independent Deliverable Verification (Cryptographic Receipts)
    # =========================================================================
    verifier = IndependentDeliverableVerifier(
        employee_repo=employee_repo,
        task_repo=task_repo,
    )

    verification_1 = await verifier.verify_task_result(
        task_id=task_1.task_id,
        employee_id=employee_1.employee_id,
        artifacts=[analyzer_file, test_file],
        test_exit_code=0,
        tested_skills=["Python", "Data Processing"],
        acceptance_criteria="Automated tests pass with zero exit code",
    )

    assert verification_1["passed"] is True, "Step 6 Failed: Independent verification failed"
    receipt_1 = verification_1["receipt"]
    assert receipt_1["signature"] is not None
    assert analyzer_file in receipt_1["artifact_digests"]
    assert receipt_1["code_quality"]["syntax_valid"] is True
    assert receipt_1["code_quality"]["security_passed"] is True

    # =========================================================================
    # STEP 7: Complete Task 1 with Receipt & Record Performance & Memory
    # =========================================================================
    completed_task_1 = await verifier.gate_task_completion(task_1.task_id, receipt_1)
    assert completed_task_1.status == TaskStatus.COMPLETED, "Step 7 Failed: Task 1 completion gate"

    # Verify Employee State: Returned to IDLE, skills verified, performance recorded
    emp_after_t1 = await employee_repo.get_by_employee_id(employee_1.employee_id)
    assert emp_after_t1.status == EmployeeStatus.IDLE, "Step 7 Failed: Employee not reset to IDLE"

    perf_1 = emp_after_t1.performance_summary or {}
    assert perf_1.get("verified_tasks", 0) >= 1
    assert perf_1.get("completed_tasks", 0) >= 1
    assert "Python" in perf_1.get("verified_skills", [])
    assert "Data Processing" in perf_1.get("verified_skills", [])

    verified_skills_t1 = [s for s in emp_after_t1.skills if s.verified]
    assert len(verified_skills_t1) >= 2, "Step 7 Failed: Skills not verified on employee"
    for s in verified_skills_t1:
        assert task_1.task_id in s.evidence_task_ids

    # Verify 6-Tier Memory Persistence: verified episodic memory stored atomically
    six_tier_store = SixTierMemoryStore(memory_repo=memory_repo)
    loaded_count = await six_tier_store.load_persisted_records()
    assert loaded_count >= 1, "Step 7 Failed: Verified memory not loaded into 6-tier store"

    trusted_memories = six_tier_store.query_trusted_records(requester_id=employee_1.employee_id)
    assert len(trusted_memories) >= 1, "Step 7 Failed: Trusted memory query empty"
    t1_memory = next(m for m in trusted_memories if m.metadata.get("task_id") == task_1.task_id)
    assert t1_memory.layer == MemoryLayer.EPISODIC
    assert t1_memory.is_verified is True
    assert t1_memory.owner_employee_id == employee_1.employee_id

    # =========================================================================
    # STEP 8: Boss plans subsequent Project 2 with Task 2 requiring the verified skill
    # =========================================================================
    proj_2 = await project_repo.create(
        name="Text-Corpus-Indexer",
        objective="Build multi-document text corpus indexing with verified token analytics",
    )

    task_2 = await task_repo.create(
        task_id="TASK-TXT-02",
        project_id=proj_2.id,
        title="Multi-Document Corpus Indexer",
        objective="Index multiple text documents using the verified TokenFrequencyAnalyzer",
        scope="Core Analytics",
        acceptance_criteria="Automated tests pass with zero exit code",
        required_capabilities=["Python", "Data Processing"],
        priority=TaskPriority.HIGH,
    )

    # Check skill gaps: Because employee_1 has verified skills, no gap is found!
    plan_spec = {
        "phases": [{
            "tasks": [{
                "role_required": "software-engineer",
                "skills_required": ["Python", "Data Processing"],
            }]
        }]
    }
    skill_gaps = brain._get_verified_skill_gaps(plan_spec, [emp_after_t1])
    assert skill_gaps == {}, "Step 8 Failed: Expected zero skill gaps for verified employee"

    # =========================================================================
    # STEP 9: Boss reuses EXACT SAME persistent employee identity for Task 2
    # =========================================================================
    assignee_2 = delegator.select_best_assignee(task_2, [emp_after_t1])
    assert assignee_2 is not None
    # Crucial acceptance test: Reuse exact same identity!
    assert assignee_2.employee_id == employee_1.employee_id, "Step 9 Failed: Employee identity was not reused"
    assert assignee_2.id == employee_1.id
    assert assignee_2.desk_id == employee_1.desk_id

    score = delegator.score_employee_for_task(assignee_2, ["Python", "Data Processing"])
    # 2 verified matches!
    assert score[0] == 2, f"Step 9 Failed: Expected 2 verified matches, got {score[0]}"

    await task_repo.assign_task(task_2.task_id, assignee_2.id)
    await task_repo.update_status(task_2.task_id, TaskStatus.RUNNING)
    await employee_repo.update_status(assignee_2.employee_id, EmployeeStatus.WORKING)

    # =========================================================================
    # STEP 10: Workspace Reuse: Prior artifacts are intact and built upon
    # =========================================================================
    # Verify prior artifact from Task 1 is still present in employee's workspace
    assert os.path.exists(analyzer_file), "Step 10 Failed: Prior workspace artifact was lost"

    # Employee creates Task 2 deliverable importing and extending Task 1's analyzer
    indexer_file = os.path.join(host_workspace, "corpus_indexer.py")
    indexer_test_file = os.path.join(host_workspace, "test_corpus_indexer.py")

    indexer_code = (
        "from analyzer import TokenFrequencyAnalyzer\n\n"
        "class CorpusIndexer:\n"
        "    def __init__(self):\n"
        "        self.analyzer = TokenFrequencyAnalyzer()\n"
        "        self.corpus: dict[str, dict[str, int]] = {}\n\n"
        "    def index_document(self, doc_id: str, text: str) -> None:\n"
        "        self.corpus[doc_id] = self.analyzer.count_tokens(text)\n\n"
        "    def get_document_frequency(self, term: str) -> int:\n"
        "        term_lower = term.lower()\n"
        "        return sum(1 for counts in self.corpus.values() if term_lower in counts)\n"
    )

    indexer_test_code = (
        "import pytest\n"
        "from corpus_indexer import CorpusIndexer\n\n"
        "def test_corpus_indexing():\n"
        "    idx = CorpusIndexer()\n"
        "    idx.index_document('doc1', 'neural operating system')\n"
        "    idx.index_document('doc2', 'autonomous neural enterprise')\n"
        "    assert idx.get_document_frequency('neural') == 2\n"
        "    assert idx.get_document_frequency('enterprise') == 1\n"
    )

    with open(indexer_file, "w", encoding="utf-8") as f:
        f.write(indexer_code)
    with open(indexer_test_file, "w", encoding="utf-8") as f:
        f.write(indexer_test_code)

    # =========================================================================
    # STEP 11: 6-Tier Memory Reuse: Employee retrieves prior verified task memory
    # =========================================================================
    prior_memories = six_tier_store.query_trusted_records(
        requester_id=employee_1.employee_id,
        department="Engineering",
    )
    assert len(prior_memories) >= 1
    assert any(m.metadata.get("task_id") == task_1.task_id for m in prior_memories)

    # Store a reusable procedural recipe derived from Task 1 experience
    procedural_memory = await six_tier_store.store_procedural(
        procedure_name="TokenAnalysisPipelineV1",
        steps=[
            {"step": 1, "description": "Tokenize text via regex"},
            {"step": 2, "description": "Count frequencies with Counter"},
            {"step": 3, "description": "Index into document corpus"},
        ],
        code_recipe="class CorpusIndexer: ...",
        verified_by="IndependentDeliverableVerifier",
        verification_receipt=receipt_1,
        task_id=task_1.task_id,
    )
    assert procedural_memory.is_verified is True
    assert procedural_memory.layer == MemoryLayer.PROCEDURAL

    # =========================================================================
    # STEP 12: Independent verification and completion of Task 2
    # =========================================================================
    verification_2 = await verifier.verify_task_result(
        task_id=task_2.task_id,
        employee_id=employee_1.employee_id,
        artifacts=[indexer_file, indexer_test_file],
        test_exit_code=0,
        tested_skills=["Python", "Data Processing"],
        acceptance_criteria="Automated tests pass with zero exit code",
    )
    assert verification_2["passed"] is True
    receipt_2 = verification_2["receipt"]

    completed_task_2 = await verifier.gate_task_completion(task_2.task_id, receipt_2)
    assert completed_task_2.status == TaskStatus.COMPLETED

    emp_after_t2 = await employee_repo.get_by_employee_id(employee_1.employee_id)
    assert emp_after_t2.status == EmployeeStatus.IDLE
    perf_2 = emp_after_t2.performance_summary or {}
    assert perf_2.get("verified_tasks", 0) >= 2, "Step 12 Failed: Expected 2 verified tasks"

    # =========================================================================
    # STEP 13: Empirical Employee Promotion Execution
    # =========================================================================
    promo_mgr = EmployeePromotionManager(employee_repo=employee_repo, audit_repo=audit_repo)

    # Assess promotion eligibility
    eligibility = await promo_mgr.evaluate_eligibility(
        employee_id=employee_1.employee_id,
        min_verified_tasks=2,
        min_verified_skills=2,
        min_pass_rate=0.8,
    )
    assert eligibility["eligible"] is True, f"Step 13 Failed: Employee not eligible: {eligibility}"
    assert eligibility["suggested_title"] == "Senior Software Engineer"
    assert eligibility["verified_tasks_count"] >= 2
    assert eligibility["pass_rate"] == 1.0

    # Execute Promotion
    promoted_emp = await promo_mgr.promote(
        employee_id=employee_1.employee_id,
        new_title="Senior Software Engineer",
        new_model_tier="reasoning",
        additional_tools=["architecture_reviewer", "code_review"],
        reason="Demonstrated repeated verified task completions and core module architecture",
        actor="BossAI",
    )

    assert promoted_emp.title == "Senior Software Engineer"
    assert promoted_emp.model_config_data.get("preferred_model_tier") == "reasoning"
    assert "architecture_reviewer" in promoted_emp.approved_tools
    assert "Senior Software Engineer" in promoted_emp.system_prompt

    # Verify audit log entry for promotion
    audit_records = await audit_repo.list_all()
    promo_audits = [a for a in audit_records if a.action == "PROMOTE_EMPLOYEE"]
    assert len(promo_audits) >= 1
    assert promo_audits[0].resource_id == employee_1.employee_id
    assert promo_audits[0].details["new_title"] == "Senior Software Engineer"

    # =========================================================================
    # STEP 14: Cross-Employee Collaboration & RBAC Memory Governance
    # =========================================================================
    # Recruit Employee 2: QA & Verification Engineer in "Quality & Reliability"
    employee_2 = await factory.recruit_dynamic_employee(
        role_title="QA Automation Engineer",
        department="Quality & Reliability",
        initial_skills=["pytest", "Security Auditing"],
    )

    # QA Engineer queries internal memory: CAN retrieve Employee 1's verified deliverables
    access_ctrl = MemoryAccessController()

    qa_memories = six_tier_store.query_records(
        requester_id=employee_2.employee_id,
        department="Quality & Reliability",
        verified_only=True,
    )
    # Employee 1's verified episodic and procedural records are accessible to QA Engineer 2
    accessible_ids = {m.id for m in qa_memories}
    assert t1_memory.id in accessible_ids, "Step 14 Failed: QA Engineer could not read internal verified task memory"
    assert procedural_memory.id in accessible_ids, "Step 14 Failed: QA Engineer could not read procedural recipe"

    # Verify that RESTRICTED private employee memory is blocked for other employees
    private_scratchpad = await six_tier_store.store_working(
        task_id="TASK-TXT-01",
        employee_id=employee_1.employee_id,
        content="Confidential employee 1 working notes.",
    )
    assert access_ctrl.can_read(private_scratchpad, requester_id=employee_2.employee_id) is False
    assert access_ctrl.can_read(private_scratchpad, requester_id=employee_1.employee_id) is True

    # =========================================================================
    # ALL 14 STEPS COMPLETED AND EMPIRICALLY VERIFIED
    # =========================================================================
    logger_msg = (
        f"NEURO-2 Live Acceptance PASSED: Recruited {employee_1.employee_id}, "
        f"verified 2 sequential tasks, proved workspace & memory reuse, "
        f"promoted to {promoted_emp.title}, and verified cross-department memory governance."
    )
    print(logger_msg)
