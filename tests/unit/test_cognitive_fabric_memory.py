from __future__ import annotations

import pytest
from neural.cognitive_fabric.memory.consolidation import MemoryConsolidationEngine
from neural.cognitive_fabric.memory.models import (
    MemoryAccessLevel,
    MemoryLayer,
    MemoryRecord,
    RecordVerificationStatus,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.db.repositories import MemoryRepository


def _verification_receipt(task_id: str, verifier_id: str = "independent-verifier") -> dict:
    return {
        "task_id": task_id,
        "attempt_id": f"attempt-{task_id}",
        "verifier_id": verifier_id,
        "passed": True,
        "evidence_refs": [f"verifier:{task_id}:tests", f"workspace-delta:{task_id}"],
    }


@pytest.mark.asyncio
async def test_six_tier_memory_layers_storage():
    store = SixTierMemoryStore()

    # 1. Working memory
    w = await store.store_working(
        task_id="TASK-001",
        employee_id="EMP-101",
        content="Analyzing DB connection leak in worker pool",
        scratchpad={"conn_count": 42, "suspect": "idle_timeout"},
    )
    assert w.layer == MemoryLayer.WORKING
    assert w.access_level == MemoryAccessLevel.RESTRICTED
    assert w.owner_employee_id == "EMP-101"
    assert not w.is_verified

    # 2. Episodic memory
    ep = await store.store_episodic(
        task_id="TASK-001",
        employee_id="EMP-101",
        outcome="success",
        summary="Fixed DB leak by lowering pool idle timeout to 300s",
        receipt={"exit_code": 0, "fixed": True},
        verification_receipt=_verification_receipt("TASK-001", "LeadVerifier"),
    )
    assert ep.layer == MemoryLayer.EPISODIC
    assert ep.is_verified
    assert ep.verification_status == RecordVerificationStatus.VERIFIED

    # 3. Semantic memory
    sem = await store.store_semantic(
        concept="ConnectionPoolLeaseTimeout",
        explanation="Idle connections must terminate within 300s to avoid pool starvation.",
        source="doc:architecture-v2",
        verified_by="BossAI",
        verification_receipt=_verification_receipt("DOC-CONNECTION-POOL", "BossAI"),
        confidence=0.95,
        access_level=MemoryAccessLevel.INTERNAL,
        allowed_departments=["Engineering"],
    )
    assert sem.layer == MemoryLayer.SEMANTIC
    assert sem.is_verified
    assert sem.provenance["verified_by"] == "BossAI"

    # 4. Procedural memory
    proc = await store.store_procedural(
        procedure_name="RestartWorkerPoolGracefully",
        steps=[
            {"step": 1, "cmd": "drain active leases"},
            {"step": 2, "cmd": "restart pool service"},
        ],
        code_recipe="def restart(): pass",
        verified_by="LeadEngineer",
        verification_receipt=_verification_receipt("PROC-RESTART-POOL", "LeadEngineer"),
    )
    assert proc.layer == MemoryLayer.PROCEDURAL
    assert proc.is_verified

    # 5. Relational memory
    rel = await store.store_relational(
        subject="EMP-101",
        predicate="RESOLVED_INCIDENT",
        target="INC-99",
        context={"severity": "P1"},
        verified_by="BossAI",
    )
    assert rel.layer == MemoryLayer.RELATIONAL
    assert rel.is_verified

    # 6. Organizational memory
    org = await store.store_organizational(
        policy_name="ZeroUnverifiedExecutions",
        statement="All production mutations require cryptographically signed execution receipts.",
        enforcement_level="strict",
        approved_by="Boardroom",
    )
    assert org.layer == MemoryLayer.ORGANIZATIONAL
    assert org.is_verified

    stats = store.get_layer_stats()
    assert stats[MemoryLayer.WORKING.value] == 1
    assert stats[MemoryLayer.EPISODIC.value] == 1
    assert stats[MemoryLayer.SEMANTIC.value] == 1
    assert stats[MemoryLayer.PROCEDURAL.value] == 1
    assert stats[MemoryLayer.RELATIONAL.value] == 1
    assert stats[MemoryLayer.ORGANIZATIONAL.value] == 1


@pytest.mark.asyncio
async def test_memory_record_verification_and_provenance():
    rec = MemoryRecord(
        layer=MemoryLayer.SEMANTIC,
        category="fact",
        source="test_runner",
        content="Unverified hypothesis regarding memory fragmentation",
        confidence=0.6,
    )
    assert not rec.is_verified
    assert rec.verification_status == RecordVerificationStatus.UNVERIFIED

    # Verify record
    rec.mark_verified(verifier_id="LeadVerifier", confidence=0.98, notes="Verified in isolated container benchmark")
    assert rec.is_verified
    assert rec.verification_status == RecordVerificationStatus.VERIFIED
    assert rec.provenance["verifier_id"] == "LeadVerifier"
    assert rec.confidence == 0.98

    # Dispute record
    rec.mark_disputed(reason="Counter-evidence found in stress run #4", disputer_id="AuditorBot")
    assert rec.verification_status == RecordVerificationStatus.DISPUTED
    assert not rec.is_verified
    assert rec.provenance["dispute"]["reason"] == "Counter-evidence found in stress run #4"

    # Mark superseded
    rec.mark_superseded(superseded_by_id="REC-200")
    assert rec.verification_status == RecordVerificationStatus.SUPERSEDED
    assert rec.provenance["superseded_by"] == "REC-200"

    d = rec.to_dict()
    assert d["verification_status"] == "superseded"
    assert d["provenance"]["superseded_by"] == "REC-200"


@pytest.mark.asyncio
async def test_memory_access_control_and_privacy_isolation():
    controller = MemoryAccessController()

    # Private record of Alice
    alice_private = MemoryRecord(
        layer=MemoryLayer.WORKING,
        category="scratchpad",
        source="task:1",
        content="Alice personal scratchpad notes",
        access_level=MemoryAccessLevel.RESTRICTED,
        owner_employee_id="alice",
    )

    # Bob should NOT be able to read Alice's private notes
    assert not controller.can_read(alice_private, requester_id="bob")
    # Alice can read her own notes
    assert controller.can_read(alice_private, requester_id="alice")
    # Boss has administrative oversight
    assert controller.can_read(alice_private, requester_id="boss")
    assert controller.can_read(alice_private, requester_id="admin")

    # Confidential department record
    eng_confidential = MemoryRecord(
        layer=MemoryLayer.SEMANTIC,
        category="design",
        source="eng_spec",
        content="Next-gen architecture blueprints",
        access_level=MemoryAccessLevel.CONFIDENTIAL,
        allowed_departments=["Engineering"],
        allowed_projects=["ProjectPhoenix"],
    )

    # QA engineer without department or project clearance is blocked
    assert not controller.can_read(eng_confidential, requester_id="charlie", department="QA", projects=["ProjectOther"])
    # Engineer without project clearance is blocked from this specific project spec
    assert not controller.can_read(eng_confidential, requester_id="alice", department="Engineering", projects=["ProjectOther"])
    # Engineer with both department and project clearance is allowed
    assert controller.can_read(eng_confidential, requester_id="alice", department="Engineering", projects=["ProjectPhoenix"])

    # Record with department-only restriction
    dept_only_record = MemoryRecord(
        layer=MemoryLayer.SEMANTIC,
        category="design",
        source="eng_guidelines",
        content="General engineering guidelines",
        access_level=MemoryAccessLevel.INTERNAL,
        allowed_departments=["Engineering"],
    )
    assert controller.can_read(dept_only_record, requester_id="alice", department="Engineering", projects=[])
    assert not controller.can_read(dept_only_record, requester_id="charlie", department="Marketing", projects=[])

    # Public record
    public_doc = MemoryRecord(
        layer=MemoryLayer.ORGANIZATIONAL,
        category="code_of_conduct",
        source="hr",
        content="Company Code of Conduct",
        access_level=MemoryAccessLevel.PUBLIC,
    )
    assert controller.can_read(public_doc, requester_id="any_employee")


@pytest.mark.asyncio
async def test_six_tier_store_query_and_filtering():
    store = SixTierMemoryStore()

    await store.store_semantic(
        concept="FastAPI Routing",
        explanation="Use APIRouter for modular sub-applications.",
        verified_by="BossAI",
        verification_receipt=_verification_receipt("DOC-FASTAPI", "BossAI"),
        allowed_departments=["Engineering"],
    )
    await store.store_semantic(
        concept="Unverified Proposal",
        explanation="Experimental change without benchmark verification.",
        verified_by=None,
        allowed_departments=["Engineering"],
    )

    # Query all
    all_res = store.query_records(requester_id="BossAI")
    assert len(all_res) == 2

    # Query verified only
    v_res = store.query_records(verified_only=True, requester_id="BossAI")
    assert len(v_res) == 1
    assert "FastAPI Routing" in v_res[0].content

    # Query by keyword
    kw_res = store.query_records(query="APIRouter", requester_id="BossAI")
    assert len(kw_res) == 1
    assert "FastAPI Routing" in kw_res[0].content


@pytest.mark.asyncio
async def test_memory_consolidation_contradictions_and_superseding():
    store = SixTierMemoryStore()
    engine = MemoryConsolidationEngine(store)

    # Step 1: Initial verified belief
    old_sem = await store.store_semantic(
        concept="DefaultTimeout",
        explanation="Default HTTP client timeout is 30 seconds.",
        verified_by="BossAI",
    )

    # Step 2: Task completed with contradicting claim
    receipt_with_conflict = {
        "summary": "Benchmarked HTTP timeouts under high load",
        "technical_claims": [
            {
                "concept": "DefaultTimeout",
                "statement": "Default HTTP client timeout is 10 seconds to prevent thread starvation.",
                "supersedes_old": True,
                "confidence": 0.95,
            }
        ],
    }

    result = await engine.consolidate_task_experience(
        task_id="TASK-PERF-1",
        employee_id="EMP-PERF",
        outcome="success",
        receipt=receipt_with_conflict,
        verifier_id="BossAI",
        verification_receipt=_verification_receipt("TASK-PERF-1", "BossAI"),
    )

    assert result["consolidated_records_count"] >= 2
    assert len(result["contradictions_detected"]) == 1

    # Check that old record was marked as SUPERSEDED with provenance
    updated_old = store.get_record(old_sem.id)
    assert updated_old.verification_status == RecordVerificationStatus.SUPERSEDED
    assert updated_old.provenance["superseded_by"] is not None

    # Step 3: Unapproved contradicting claim flags record as disputed instead of overwriting
    receipt_unapproved = {
        "summary": "Random proposal from junior worker",
        "technical_claims": [
            {
                "concept": "DefaultTimeout",
                "statement": "Default HTTP client timeout is 500 seconds.",
                "supersedes_old": False,  # Not approved as direct replacement
                "confidence": 0.4,
            }
        ],
    }

    result2 = await engine.consolidate_task_experience(
        task_id="TASK-RANDOM-2",
        employee_id="EMP-JUNIOR",
        outcome="failure",
        receipt=receipt_unapproved,
    )

    assert len(result2["contradictions_detected"]) >= 1
    # Check that new memory was stored without overwriting older records
    newest = store.query_records(query="500 seconds", requester_id="BossAI")
    assert len(newest) == 1
    assert not newest[0].is_verified


@pytest.mark.asyncio
async def test_success_outcome_without_independent_receipt_stays_untrusted_and_does_not_promote():
    store = SixTierMemoryStore()
    engine = MemoryConsolidationEngine(store)

    result = await engine.consolidate_task_experience(
        task_id="TASK-UNVERIFIED-SUCCESS",
        employee_id="ANC-EMP-WORKER",
        outcome="success",
        receipt={
            "summary": "Worker reports a successful database optimization.",
            "technical_claims": [{"concept": "UnsafeClaim", "statement": "The change is correct."}],
            "reusable_procedure": {"name": "UnsafeRecipe", "steps": [{"step": 1}]},
        },
        verifier_id="BossAI",
    )

    episodic, = store.query_records(layer=MemoryLayer.EPISODIC, requester_id="BossAI")
    assert not episodic.is_verified
    assert store.query_trusted_records(layer=MemoryLayer.EPISODIC, requester_id="BossAI") == []
    assert result["consolidated_records_count"] == 2  # episode plus an unverified semantic draft
    assert store.query_records(layer=MemoryLayer.PROCEDURAL, requester_id="BossAI") == []
    semantic, = store.query_records(layer=MemoryLayer.SEMANTIC, requester_id="BossAI")
    assert not semantic.is_verified


@pytest.mark.asyncio
async def test_task_derived_memory_requires_a_distinct_verifier_and_matching_receipt():
    store = SixTierMemoryStore()
    worker_receipt = _verification_receipt("TASK-SAME-ACTOR", "ANC-EMP-WORKER")
    episodic = await store.store_episodic(
        task_id="TASK-SAME-ACTOR",
        employee_id="ANC-EMP-WORKER",
        outcome="success",
        summary="Worker asserted success without independent review.",
        verification_receipt=worker_receipt,
    )
    mismatched_receipt = _verification_receipt("TASK-OTHER")
    mismatched = await store.store_episodic(
        task_id="TASK-CURRENT",
        employee_id="ANC-EMP-WORKER",
        outcome="success",
        summary="Receipt belongs to a different task.",
        verification_receipt=mismatched_receipt,
    )

    assert not episodic.is_verified
    assert not mismatched.is_verified
    assert store.query_trusted_records(layer=MemoryLayer.EPISODIC, requester_id="BossAI") == []


@pytest.mark.asyncio
async def test_six_tier_memory_records_reload_from_relational_repository(async_session):
    repository = MemoryRepository(async_session)
    original = SixTierMemoryStore(memory_repo=repository)
    trusted = await original.store_episodic(
        task_id="TASK-PERSISTED",
        employee_id="ANC-EMP-PERSISTED",
        outcome="success",
        summary="Verified outcome survives a fresh store instance.",
        receipt={"worker_command": "tests passed"},
        verification_receipt=_verification_receipt("TASK-PERSISTED"),
    )
    await original.store_episodic(
        task_id="TASK-PERSISTED-UNVERIFIED",
        employee_id="ANC-EMP-PERSISTED",
        outcome="success",
        summary="Unverified result stays out of trusted memory after reload.",
    )

    restored = SixTierMemoryStore(memory_repo=repository)
    retriever = HybridRetrievalEngine(memory_store=restored)
    count = await retriever.restore_persisted_memory()

    assert count == 2
    trusted_after_restart = restored.get_record(trusted.id)
    assert trusted_after_restart is not None and trusted_after_restart.is_verified
    assert [r.id for r in restored.query_trusted_records(layer=MemoryLayer.EPISODIC, requester_id="BossAI")] == [trusted.id]
    retrieved = retriever.retrieve("verified outcome survives", requester_id="BossAI")
    assert [record["id"] for record in retrieved] == [trusted.id]
