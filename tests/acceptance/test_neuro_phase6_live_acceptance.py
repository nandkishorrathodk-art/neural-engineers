from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.boss.delegator import TaskDelegator
from neural.cognitive_fabric.memory.models import (
    MemoryAccessLevel,
    MemoryLayer,
    valid_independent_verification_receipt,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.agentic_rag import AgenticRAGEngine
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.cognitive_fabric.retrieval.reranker import ReciprocalRankFusionReranker
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
from neural.evaluation.verifier import IndependentDeliverableVerifier
from neural.exceptions import UnverifiedTaskError, VerificationFailedError
from neural.observability.audit import AuditLogChainVerifier
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_neuro_phase6_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path,
):
    """
    NEURO-6 Live Acceptance Scenario:
    Demonstrates the complete 6-tier organizational memory architecture,
    cryptographic verification gate for memory promotion, RBAC privacy boundaries,
    hybrid dense + keyword (BM25) retrieval with temporal decay reranking,
    bounded Agentic RAG multi-round research, and cross-project knowledge transfer
    where Employee B retrieves Employee A's verified procedural recipe and
    successfully completes a dependent task with zero failure.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Owner Authentication, Budget Setup & Repositories Initialization
    # =========================================================================
    owner_identity = "owner@ancos.corp"
    assert "@" in owner_identity

    budget_repo = BudgetRepository(async_session)
    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("1500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    available_quota = await budget_repo.get_available_quota(month_scope)
    assert available_quota >= Decimal("1000.00"), "Step 1 Failed: Budget quota insufficient"

    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    project_repo = ProjectRepository(async_session)
    audit_repo = AuditRepository(async_session)
    memory_repo = MemoryRepository(async_session)

    # =========================================================================
    # STEP 2: Boss Recruits Workforce (Employee A, Employee B, Employee C)
    # =========================================================================
    factory = EmployeeFactory(employee_repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    # Employee A: Senior Data Engineer in Engineering
    employee_a = await factory.recruit_dynamic_employee(
        role_title="Senior Data Engineer",
        department="Engineering",
        initial_skills=["Python", "Data Processing", "Batch Architecture"],
    )
    assert employee_a.employee_id.startswith("ANC-EMP-"), "Step 2 Failed: Employee A ID format"
    assert all(not s.verified for s in employee_a.skills), "Step 2 Failed: Employee A skills must start unverified"

    # Employee B: Backend Analytics Engineer in Analytics
    employee_b = await factory.recruit_dynamic_employee(
        role_title="Backend Analytics Engineer",
        department="Analytics",
        initial_skills=["Python", "Metrics Extraction", "Data Processing"],
    )
    assert employee_b.employee_id.startswith("ANC-EMP-"), "Step 2 Failed: Employee B ID format"
    assert all(not s.verified for s in employee_b.skills), "Step 2 Failed: Employee B skills must start unverified"

    # Employee C: Marketing Specialist in Marketing (for cross-department RBAC isolation)
    employee_c = await factory.recruit_dynamic_employee(
        role_title="Marketing Specialist",
        department="Marketing",
        initial_skills=["Campaign Management", "Content Writing"],
    )
    assert employee_c.department == "Marketing"

    # Workspace folders on host
    workspace_a = os.path.join(str(tmp_path), employee_a.employee_id)
    workspace_b = os.path.join(str(tmp_path), employee_b.employee_id)
    assert os.path.exists(workspace_a) and os.path.exists(workspace_b), "Step 2 Failed: Host workspaces missing"

    # Provision workstations in DB
    container_a = Container(
        docker_id=f"ancos-desktop-{employee_a.employee_id.lower()}",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5931,
        novnc_port=6081,
    )
    container_b = Container(
        docker_id=f"ancos-desktop-{employee_b.employee_id.lower()}",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5932,
        novnc_port=6082,
    )
    async_session.add_all([container_a, container_b])
    await async_session.flush()

    await employee_repo.assign_container(employee_a.employee_id, container_a.id)
    await employee_repo.assign_container(employee_b.employee_id, container_b.id)

    emp_a_ref = await employee_repo.get_by_employee_id(employee_a.employee_id)
    emp_b_ref = await employee_repo.get_by_employee_id(employee_b.employee_id)
    assert emp_a_ref.container_id == container_a.id
    assert emp_b_ref.container_id == container_b.id

    # =========================================================================
    # STEP 3: Boss Plans Project 1 & Assigns Task 1 to Employee A
    # =========================================================================
    proj_1 = await project_repo.create(
        name="Data-Pipeline-Infrastructure",
        objective="Construct robust batch processing and data validation pipeline",
    )

    task_1 = await task_repo.create(
        task_id="TASK-BATCH-01",
        project_id=proj_1.id,
        title="Resilient Batch Processor",
        objective="Implement a resilient chunked data processor with schema validation and error recovery",
        scope="Data Engineering",
        acceptance_criteria="Automated test suite passes with zero exit code and verified artifact digests",
        required_capabilities=["Python", "Data Processing"],
        priority=TaskPriority.HIGH,
    )

    delegator = TaskDelegator()
    assignee_1 = delegator.select_best_assignee(task_1, [emp_a_ref])
    assert assignee_1.employee_id == employee_a.employee_id, "Step 3 Failed: Delegator assignment"

    await task_repo.assign_task(task_1.task_id, assignee_1.id)
    await task_repo.update_status(task_1.task_id, TaskStatus.RUNNING)
    await employee_repo.update_status(assignee_1.employee_id, EmployeeStatus.WORKING)

    # =========================================================================
    # STEP 4: Employee A Implements Task 1 Deliverables on Host Filesystem
    # =========================================================================
    processor_code = (
        "from __future__ import annotations\n"
        "from typing import Any, Callable\n\n"
        "class ResilientBatchProcessor:\n"
        "    \"\"\"Reusable procedural recipe for resilient chunk-based batch data processing.\"\"\"\n"
        "    def __init__(self, chunk_size: int = 5, error_threshold: float = 0.2) -> None:\n"
        "        self.chunk_size = max(1, chunk_size)\n"
        "        self.error_threshold = error_threshold\n\n"
        "    def process_batches(\n"
        "        self,\n"
        "        items: list[dict[str, Any]],\n"
        "        transform_fn: Callable[[dict[str, Any]], dict[str, Any]],\n"
        "    ) -> dict[str, Any]:\n"
        "        processed = []\n"
        "        errors = []\n"
        "        for i in range(0, len(items), self.chunk_size):\n"
        "            chunk = items[i:i + self.chunk_size]\n"
        "            for item in chunk:\n"
        "                try:\n"
        "                    res = transform_fn(item)\n"
        "                    processed.append(res)\n"
        "                except Exception as ex:\n"
        "                    errors.append({'item': item, 'error': str(ex)})\n"
        "        total = len(items)\n"
        "        err_rate = len(errors) / total if total > 0 else 0.0\n"
        "        if err_rate > self.error_threshold:\n"
        "            raise RuntimeError(f'Error rate {err_rate:.2%} exceeded threshold {self.error_threshold:.2%}')\n"
        "        return {'total': total, 'processed_count': len(processed), 'items': processed, 'errors': errors}\n"
    )

    test_processor_code = (
        "import pytest\n"
        "from batch_processor import ResilientBatchProcessor\n\n"
        "def test_batch_processing_success():\n"
        "    processor = ResilientBatchProcessor(chunk_size=2)\n"
        "    data = [{'val': 10}, {'val': 20}, {'val': 30}]\n"
        "    result = processor.process_batches(data, lambda x: {'val': x['val'] * 2})\n"
        "    assert result['processed_count'] == 3\n"
        "    assert result['items'][0]['val'] == 20\n"
        "    assert result['items'][2]['val'] == 60\n\n"
        "def test_batch_processing_fault_tolerance():\n"
        "    processor = ResilientBatchProcessor(chunk_size=2, error_threshold=0.5)\n"
        "    data = [{'val': 10}, {'bad': True}, {'val': 30}]\n"
        "    def transform(x):\n"
        "        if 'bad' in x: raise ValueError('Invalid record')\n"
        "        return {'val': x['val'] + 1}\n"
        "    res = processor.process_batches(data, transform)\n"
        "    assert res['processed_count'] == 2\n"
        "    assert len(res['errors']) == 1\n"
    )

    proc_file_a = os.path.join(workspace_a, "batch_processor.py")
    test_file_a = os.path.join(workspace_a, "test_batch_processor.py")
    with open(proc_file_a, "w", encoding="utf-8") as f:
        f.write(processor_code)
    with open(test_file_a, "w", encoding="utf-8") as f:
        f.write(test_processor_code)

    assert os.path.exists(proc_file_a) and os.path.exists(test_file_a), "Step 4 Failed: Files not created"

    # =========================================================================
    # STEP 5: Independent Deliverable Verification (Cryptographic Receipt)
    # =========================================================================
    verifier = IndependentDeliverableVerifier(
        employee_repo=employee_repo,
        task_repo=task_repo,
    )

    reusable_recipe_spec = {
        "name": "ResilientBatchProcessorRecipe",
        "description": "Standard Operating Procedure for fault-tolerant chunked data processing",
        "steps": [
            {"step": 1, "action": "Partition input data into configured chunk sizes"},
            {"step": 2, "action": "Apply item-level transformation within try-except boundary"},
            {"step": 3, "action": "Calculate error rate against error_threshold limit"},
            {"step": 4, "action": "Return structured execution metrics with error diagnostics"},
        ],
        "code": processor_code,
    }

    verification_1 = await verifier.verify_task_result(
        task_id=task_1.task_id,
        employee_id=employee_a.employee_id,
        artifacts=[proc_file_a, test_file_a],
        test_exit_code=0,
        tested_skills=["Python", "Data Processing"],
        acceptance_criteria="Automated test suite passes with zero exit code",
    )
    assert verification_1["passed"] is True, "Step 5 Failed: Verification failed"

    receipt_1 = verification_1["receipt"]
    assert receipt_1["signature"] is not None
    assert proc_file_a in receipt_1["artifact_digests"]
    assert receipt_1["code_quality"]["syntax_valid"] is True
    assert receipt_1["code_quality"]["security_passed"] is True

    # Attach the reusable procedure to the verification receipt for atomic promotion
    receipt_1["reusable_procedure"] = reusable_recipe_spec
    # Re-sign receipt to keep cryptographic integrity intact
    receipt_1.pop("signature", None)
    receipt_1["signature"] = verifier.generate_receipt_signature(receipt_1)

    completed_task_1 = await verifier.gate_task_completion(task_1.task_id, receipt_1)
    assert completed_task_1.status == TaskStatus.COMPLETED, "Step 5 Failed: Task 1 gate"

    # Verify Employee A state
    emp_a_after = await employee_repo.get_by_employee_id(employee_a.employee_id)
    assert emp_a_after.status == EmployeeStatus.IDLE
    verified_skills_a = [s.skill_name for s in emp_a_after.skills if s.verified]
    assert "Data Processing" in verified_skills_a, "Step 5 Failed: Skills not verified"

    # =========================================================================
    # STEP 6: 6-Tier Memory Persistence & Envelope Promotion
    # =========================================================================
    store = SixTierMemoryStore(memory_repo=memory_repo)

    # 1. Working memory: Employee A private scratchpad (RESTRICTED)
    working_rec = await store.store_working(
        task_id=task_1.task_id,
        employee_id=employee_a.employee_id,
        content="Benchmarking chunk_size=5 vs chunk_size=10 under memory constraints",
        scratchpad={"peak_mem_mb": 42.5, "optimal_chunk": 5},
    )
    assert working_rec.layer == MemoryLayer.WORKING
    assert working_rec.access_level == MemoryAccessLevel.RESTRICTED
    assert not working_rec.is_verified

    # 2. Episodic memory: Task 1 completion receipt
    episodic_rec = await store.store_episodic(
        task_id=task_1.task_id,
        employee_id=employee_a.employee_id,
        outcome="success",
        summary="Built and verified ResilientBatchProcessor with chunk fault-tolerance",
        receipt={"exit_code": 0, "tests_passed": 2},
        verification_receipt=receipt_1,
    )
    assert episodic_rec.layer == MemoryLayer.EPISODIC
    assert episodic_rec.is_verified

    # 3. Semantic memory: Architectural rule (allowed for Engineering & Analytics)
    semantic_rec = await store.store_semantic(
        concept="BatchChunkSizeRule",
        explanation="Chunk sizes between 5 and 50 balance throughput and worker memory limits.",
        source=f"task:{task_1.task_id}",
        verified_by=receipt_1["verifier_id"],
        verification_receipt=receipt_1,
        access_level=MemoryAccessLevel.CONFIDENTIAL,
        allowed_departments=["Engineering", "Analytics"],
    )
    assert semantic_rec.layer == MemoryLayer.SEMANTIC
    assert semantic_rec.is_verified

    # 4. Procedural memory: Reusable execution recipe
    procedural_rec = await store.store_procedural(
        procedure_name="ResilientBatchProcessorRecipe",
        steps=reusable_recipe_spec["steps"],
        code_recipe=reusable_recipe_spec["code"],
        verified_by=receipt_1["verifier_id"],
        verification_receipt=receipt_1,
        task_id=task_1.task_id,
        owner_employee_id=employee_a.employee_id,
        access_level=MemoryAccessLevel.INTERNAL,
        allowed_departments=["Engineering", "Analytics"],
    )
    assert procedural_rec.layer == MemoryLayer.PROCEDURAL
    assert procedural_rec.is_verified
    assert "ResilientBatchProcessor" in procedural_rec.content

    # 5. Relational memory: Collaboration connection
    relational_rec = await store.store_relational(
        subject=employee_a.employee_id,
        predicate="COLLABORATED_ON",
        target=task_1.task_id,
        context={"project": proj_1.name},
        verified_by=receipt_1["verifier_id"],
    )
    assert relational_rec.layer == MemoryLayer.RELATIONAL

    # 6. Organizational memory: Boardroom corporate policy
    org_rec = await store.store_organizational(
        policy_name="ZeroUnverifiedDeliverablesPolicy",
        statement="All production deliverables must pass independent verification with cryptographic receipts.",
        approved_by="Boardroom",
    )
    assert org_rec.layer == MemoryLayer.ORGANIZATIONAL
    assert org_rec.is_verified

    trusted_memories = store.query_trusted_records(requester_id="BossAI")
    trusted_ids = {m.id for m in trusted_memories}
    assert episodic_rec.id in trusted_ids
    assert semantic_rec.id in trusted_ids
    assert procedural_rec.id in trusted_ids
    assert org_rec.id in trusted_ids

    # =========================================================================
    # STEP 7: Adversarial Provenance Gate Testing (Forged, Unlinked, & Self-Verified)
    # =========================================================================
    # Attempt A: Forged cryptographic signature
    forged_receipt = dict(receipt_1)
    forged_receipt["signature"] = "deadbeef" * 8
    assert not valid_independent_verification_receipt(forged_receipt, task_id=task_1.task_id)

    # Attempt B: Unlinked receipt pointing to another task
    unlinked_receipt = dict(receipt_1)
    unlinked_receipt["task_id"] = "TASK-FOREIGN-99"
    unlinked_receipt.pop("signature", None)
    unlinked_receipt["signature"] = verifier.generate_receipt_signature(unlinked_receipt)
    assert not valid_independent_verification_receipt(unlinked_receipt, task_id=task_1.task_id)

    # Attempt C: Worker attempts self-verification
    self_verify_receipt = dict(receipt_1)
    self_verify_receipt["verifier_id"] = employee_a.employee_id
    self_verify_receipt.pop("signature", None)
    self_verify_receipt["signature"] = verifier.generate_receipt_signature(self_verify_receipt)
    assert not valid_independent_verification_receipt(self_verify_receipt, employee_id=employee_a.employee_id)

    # Attempt D: Rejected failure attempt
    failed_receipt = dict(receipt_1)
    failed_receipt["passed"] = False
    assert not valid_independent_verification_receipt(failed_receipt)

    # Attempt E: Unsigned receipt (missing cryptographic token)
    unsigned_receipt = dict(receipt_1)
    unsigned_receipt.pop("signature", None)
    assert not valid_independent_verification_receipt(unsigned_receipt, require_signature=True)

    # Strictly test completion gates with adversarial receipts:
    with pytest.raises(UnverifiedTaskError, match="signature mismatch"):
        await verifier.gate_task_completion(task_1.task_id, forged_receipt)

    with pytest.raises(UnverifiedTaskError, match="linked to task"):
        await verifier.gate_task_completion(task_1.task_id, unlinked_receipt)

    with pytest.raises(UnverifiedTaskError, match="missing required cryptographic signature"):
        await verifier.gate_task_completion(task_1.task_id, unsigned_receipt)

    with pytest.raises(UnverifiedTaskError, match="indicates failure"):
        await verifier.gate_task_completion(task_1.task_id, failed_receipt)

    with pytest.raises(VerificationFailedError, match="cryptographic signature mismatch"):
        await task_repo.complete_with_receipt(task_1.task_id, forged_receipt)

    with pytest.raises(VerificationFailedError, match="linked to task"):
        await task_repo.complete_with_receipt(task_1.task_id, unlinked_receipt)

    # Verify that forged procedural memory cannot enter trusted memory
    forged_proc = await store.store_procedural(
        procedure_name="ForgedBackdoorProcedure",
        steps=[{"step": 1, "action": "bypass security"}],
        verified_by=forged_receipt["verifier_id"],
        verification_receipt=forged_receipt,
        task_id=task_1.task_id,
        owner_employee_id=employee_a.employee_id,
    )
    assert not forged_proc.is_verified
    assert forged_proc.id not in {m.id for m in store.query_trusted_records(requester_id="BossAI")}

    # =========================================================================
    # STEP 8: Store Recreation & Deterministic Hydration from Persistence
    # =========================================================================
    # Fresh store instance backed only by the persistent database repository
    fresh_store = SixTierMemoryStore(memory_repo=memory_repo)
    hydrated_count = await fresh_store.load_persisted_records()
    assert hydrated_count >= 4, f"Step 8 Failed: Expected >=4 persisted records, got {hydrated_count}"

    # Verify deterministic hydration
    hydrated_proc = fresh_store.get_record(procedural_rec.id)
    assert hydrated_proc is not None, "Step 8 Failed: Procedural record missing after hydration"
    assert hydrated_proc.is_verified is True, "Step 8 Failed: Hydrated procedural recipe lost verified status"
    assert hydrated_proc.provenance["independent_verification_receipt"]["signature"] == receipt_1["signature"]

    hydrated_org = fresh_store.get_record(org_rec.id)
    assert hydrated_org is not None and hydrated_org.is_verified is True

    # Unverified working memory stayed unverified or was excluded from trusted memory
    hydrated_trusted = fresh_store.query_trusted_records(requester_id="BossAI")
    assert any(m.id == procedural_rec.id for m in hydrated_trusted)
    assert not any(m.layer == MemoryLayer.WORKING for m in hydrated_trusted)

    # =========================================================================
    # STEP 9: Role-Based Access Control (RBAC) & Privacy Boundaries
    # =========================================================================
    access_ctrl = MemoryAccessController()

    # 1. Private working memory of Employee A
    assert access_ctrl.can_read(working_rec, requester_id=employee_a.employee_id) is True
    assert access_ctrl.can_read(working_rec, requester_id="BossAI") is True
    # Employee B and Employee C are strictly BLOCKED from Employee A's private notes
    assert access_ctrl.can_read(working_rec, requester_id=employee_b.employee_id) is False
    assert access_ctrl.can_read(working_rec, requester_id=employee_c.employee_id) is False

    # 2. Department-cleared confidential record (Engineering and Analytics allowed)
    assert access_ctrl.can_read(semantic_rec, requester_id=employee_a.employee_id, department="Engineering") is True
    assert access_ctrl.can_read(semantic_rec, requester_id=employee_b.employee_id, department="Analytics") is True
    # Marketing employee is strictly BLOCKED
    assert access_ctrl.can_read(semantic_rec, requester_id=employee_c.employee_id, department="Marketing") is False

    # 3. Public organizational policy is queryable by all
    assert access_ctrl.can_read(org_rec, requester_id=employee_a.employee_id) is True
    assert access_ctrl.can_read(org_rec, requester_id=employee_b.employee_id) is True
    assert access_ctrl.can_read(org_rec, requester_id=employee_c.employee_id) is True

    # 4. Restricted executive memo with NO individual employee owner is strictly blocked
    exec_restricted_rec = await store.store_organizational(
        policy_name="ConfidentialExecutiveCompensation",
        statement="Executive compensation and board strategic reserves.",
        access_level=MemoryAccessLevel.RESTRICTED,
    )
    assert access_ctrl.can_read(exec_restricted_rec, requester_id=employee_a.employee_id) is False
    assert access_ctrl.can_read(exec_restricted_rec, requester_id=employee_b.employee_id) is False
    assert access_ctrl.can_read(exec_restricted_rec, requester_id=employee_c.employee_id) is False
    assert access_ctrl.can_read(exec_restricted_rec, requester_id="BossAI") is True

    # 5. Case-insensitive department clearance
    assert access_ctrl.can_read(semantic_rec, requester_id=employee_a.employee_id, department="engineering") is True

    # =========================================================================
    # STEP 10: Hybrid Dense + Keyword (BM25) Retrieval & Temporal Decay Reranking
    # =========================================================================
    retriever = HybridRetrievalEngine(memory_store=fresh_store)
    indexed_count = await retriever.restore_persisted_memory()
    assert indexed_count >= 1, "Step 10 Failed: Restore persisted memory into retriever failed"

    # Employee B queries organizational memory for batch processing
    hybrid_matches = retriever.retrieve(
        query="resilient batch processor chunk error recovery",
        top_k=5,
        requester_id=employee_b.employee_id,
        department="Analytics",
    )
    assert len(hybrid_matches) >= 1, "Step 10 Failed: Hybrid retrieval returned empty results"
    top_match = hybrid_matches[0]
    assert top_match["id"] == procedural_rec.id
    assert top_match["is_verified"] is True
    assert "ResilientBatchProcessor" in top_match["text"]

    # Restricted document indexed in retriever must be blocked from Employee B
    retriever.index_document(
        "exec-secret-budget",
        "Executive reserve allocation and confidential boardroom strategy.",
        metadata={"access_level": "restricted", "owner_employee_id": None},
    )
    b_exec_results = retriever.retrieve(
        "Executive reserve allocation",
        requester_id=employee_b.employee_id,
        department="Analytics",
        include_unverified=True,
    )
    assert not any(r["id"] == "exec-secret-budget" for r in b_exec_results)

    # BossAI can retrieve restricted document
    boss_exec_results = retriever.retrieve("Executive reserve allocation", requester_id="BossAI", include_unverified=True)
    assert any(r["id"] == "exec-secret-budget" for r in boss_exec_results)

    # Empty and whitespace queries safely return empty results
    assert retriever.retrieve("", requester_id=employee_b.employee_id) == []
    assert retriever.retrieve("   ", requester_id=employee_b.employee_id) == []

    # Temporal decay test: fresher verified record vs older verified record
    fresh_reranker = ReciprocalRankFusionReranker(
        k=60,
        temporal_decay=True,
        decay_half_life_days=30.0,
        now=datetime(2026, 11, 10, 12, 0, 0),
    )
    fresh_doc = {
        "id": "doc-v2",
        "text": "Resilient chunk processing guidelines v2",
        "metadata": {"is_verified": True, "verified_at": "2026-11-09T10:00:00"},
    }
    stale_doc = {
        "id": "doc-v1",
        "text": "Resilient chunk processing guidelines v1",
        "metadata": {"is_verified": True, "verified_at": "2026-08-01T10:00:00"},
    }
    fused_temporal = fresh_reranker.fuse(
        [("doc-v2", 0.85, fresh_doc), ("doc-v1", 0.85, stale_doc)],
        [("doc-v2", 4.0, fresh_doc), ("doc-v1", 4.0, stale_doc)],
    )
    assert fused_temporal[0]["id"] == "doc-v2"
    assert fused_temporal[0]["rrf_score"] > fused_temporal[1]["rrf_score"]

    # =========================================================================
    # STEP 11: Agentic RAG Multi-Round Research
    # =========================================================================
    agentic_engine = AgenticRAGEngine(retriever=retriever, max_rounds=3)
    research_result = await agentic_engine.research(
        query="How to implement resilient batch processing pipeline with error handling?",
        requester_id=employee_b.employee_id,
        department="Analytics",
    )

    assert research_result["strategy"] in ("agentic_multiround", "simple_hybrid")
    assert research_result["evidence_count"] >= 1
    assert any(e["id"] == procedural_rec.id for e in research_result["evidence"])
    assert "ResilientBatchProcessor" in research_result["answer"]

    # =========================================================================
    # STEP 12: Boss Plans Project 2 & Assigns Task 2 to Employee B
    # =========================================================================
    proj_2 = await project_repo.create(
        name="Real-time-Metrics-Platform",
        objective="Build an analytics reporter that aggregates streaming metric batches",
    )

    task_2 = await task_repo.create(
        task_id="TASK-METRICS-02",
        project_id=proj_2.id,
        title="Sanitized Metrics Aggregator",
        objective="Implement a metrics aggregator utilizing the organization's verified batch processing recipe",
        scope="Analytics Reporting",
        acceptance_criteria="Automated test suite passes with zero exit code and verified artifact digests",
        required_capabilities=["Python", "Data Processing"],
        priority=TaskPriority.HIGH,
    )

    assignee_2 = delegator.select_best_assignee(task_2, [emp_b_ref])
    assert assignee_2.employee_id == employee_b.employee_id

    await task_repo.assign_task(task_2.task_id, assignee_2.id)
    await task_repo.update_status(task_2.task_id, TaskStatus.RUNNING)
    await employee_repo.update_status(assignee_2.employee_id, EmployeeStatus.WORKING)

    # =========================================================================
    # STEP 13: Employee B Implements Task 2 Using Employee A's Verified Recipe
    # =========================================================================
    # Employee B copies/adapts the verified procedural recipe retrieved from memory
    retrieved_recipe = top_match["metadata"]["metadata"]["code_recipe"]
    assert "class ResilientBatchProcessor" in retrieved_recipe

    reporter_code = (
        f"{retrieved_recipe}\n\n"
        "class MetricsAggregator:\n"
        "    \"\"\"Analytics reporter built on top of Employee A's verified ResilientBatchProcessor.\"\"\"\n"
        "    def __init__(self, chunk_size: int = 5) -> None:\n"
        "        self.processor = ResilientBatchProcessor(chunk_size=chunk_size)\n\n"
        "    def aggregate_metrics(self, raw_events: list[dict[str, Any]]) -> dict[str, Any]:\n"
        "        def _normalize(evt: dict[str, Any]) -> dict[str, Any]:\n"
        "            val = float(evt.get('latency_ms', 0.0))\n"
        "            return {'metric': evt.get('metric', 'unknown'), 'latency_ms': val}\n\n"
        "        batch_result = self.processor.process_batches(raw_events, _normalize)\n"
        "        items = batch_result['items']\n"
        "        count = len(items)\n"
        "        total_latency = sum(it['latency_ms'] for it in items)\n"
        "        avg_latency = total_latency / count if count > 0 else 0.0\n"
        "        return {\n"
        "            'event_count': count,\n"
        "            'average_latency_ms': avg_latency,\n"
        "            'error_count': len(batch_result['errors']),\n"
        "        }\n"
    )

    test_reporter_code = (
        "import pytest\n"
        "from metrics_reporter import MetricsAggregator\n\n"
        "def test_metrics_aggregation():\n"
        "    reporter = MetricsAggregator(chunk_size=3)\n"
        "    events = [\n"
        "        {'metric': 'api_latency', 'latency_ms': 50},\n"
        "        {'metric': 'api_latency', 'latency_ms': 150},\n"
        "        {'metric': 'api_latency', 'latency_ms': 100},\n"
        "    ]\n"
        "    report = reporter.aggregate_metrics(events)\n"
        "    assert report['event_count'] == 3\n"
        "    assert report['average_latency_ms'] == 100.0\n"
        "    assert report['error_count'] == 0\n"
    )

    reporter_file_b = os.path.join(workspace_b, "metrics_reporter.py")
    test_file_b = os.path.join(workspace_b, "test_metrics_reporter.py")
    with open(reporter_file_b, "w", encoding="utf-8") as f:
        f.write(reporter_code)
    with open(test_file_b, "w", encoding="utf-8") as f:
        f.write(test_reporter_code)

    assert os.path.exists(reporter_file_b) and os.path.exists(test_file_b)

    # =========================================================================
    # STEP 14: Independent Deliverable Verification of Task 2
    # =========================================================================
    verification_2 = await verifier.verify_task_result(
        task_id=task_2.task_id,
        employee_id=employee_b.employee_id,
        artifacts=[reporter_file_b, test_file_b],
        test_exit_code=0,
        tested_skills=["Python", "Data Processing"],
        acceptance_criteria="Automated test suite passes with zero exit code",
    )
    assert verification_2["passed"] is True, "Step 14 Failed: Task 2 verification failed"

    receipt_2 = verification_2["receipt"]
    completed_task_2 = await verifier.gate_task_completion(task_2.task_id, receipt_2)
    assert completed_task_2.status == TaskStatus.COMPLETED

    emp_b_after = await employee_repo.get_by_employee_id(employee_b.employee_id)
    assert emp_b_after.status == EmployeeStatus.IDLE
    verified_skills_b = [s.skill_name for s in emp_b_after.skills if s.verified]
    assert "Data Processing" in verified_skills_b

    # =========================================================================
    # STEP 15: Full Immutable Audit Log & Hash Chain Integrity
    # =========================================================================
    await audit_repo.create_entry(
        actor="BossAI",
        action="VERIFY_PHASE6_ACCEPTANCE",
        resource_type="Phase",
        resource_id="NEURO-6",
        details={
            "employee_a": employee_a.employee_id,
            "employee_b": employee_b.employee_id,
            "task_1": task_1.task_id,
            "task_2": task_2.task_id,
            "recipe_id": procedural_rec.id,
            "status": "ACCEPTED",
        },
    )

    audit_records = await audit_repo.list_all(limit=50)
    assert len(audit_records) >= 1
    ordered_records = AuditLogChainVerifier.order_chain(audit_records)
    chain_valid, chain_err = AuditLogChainVerifier.verify_records(ordered_records)
    assert chain_valid is True, f"Step 15 Failed: Audit hash chain violation: {chain_err}"
