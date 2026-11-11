from __future__ import annotations

from datetime import datetime, timezone

import pytest
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.agentic_rag import AgenticRAGEngine
from neural.cognitive_fabric.retrieval.bm25_index import BM25Index, tokenize
from neural.cognitive_fabric.retrieval.evidence_evaluator import EvidenceEvaluator
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.cognitive_fabric.retrieval.reranker import ReciprocalRankFusionReranker
from neural.cognitive_fabric.retrieval.vector_store import DenseVectorStore


def _trusted_metadata(source: str) -> dict:
    task_id = f"task-{source}"
    return {
        "access_level": "public",
        "verification_status": "verified",
        "verified_at": "2026-11-04T00:00:00",
        "provenance": {
            "verified_by": "independent-verifier",
            "independent_verification_receipt": {
                "task_id": task_id,
                "attempt_id": f"attempt-{source}",
                "verifier_id": "independent-verifier",
                "passed": True,
                "evidence_refs": [f"verifier:{source}:tests"],
            },
        },
        "metadata": {"task_id": task_id},
    }


def test_bm25_tokenizer_and_scoring():
    tokens = tokenize("The quick brown fox jumps over the lazy dog!")
    assert "quick" in tokens
    assert "brown" in tokens
    assert "the" not in tokens  # Stopword removed

    index = BM25Index()
    index.add_document("doc1", "FastAPI web framework with async routing and Pydantic validation")
    index.add_document("doc2", "PostgreSQL database indexing and query optimization")
    index.add_document("doc3", "FastAPI authentication middleware and JWT tokens")

    assert index.count() == 3

    # Query matching FastAPI docs
    results = index.search("FastAPI async", top_k=5)
    assert len(results) >= 2
    assert results[0][0] == "doc1"

    # Test document removal
    index.remove_document("doc1")
    assert index.count() == 2
    results_after = index.search("FastAPI async", top_k=5)
    assert not any(r[0] == "doc1" for r in results_after)


def test_dense_vector_store():
    store = DenseVectorStore(collection_name="test_dense")
    store.add_document(
        "docA",
        "Microservice architectural patterns and distributed consensus with Raft",
        metadata={"category": "architecture", "access_level": "public"},
    )
    store.add_document(
        "docB",
        "Frontend React components with Tailwind CSS styling and Next.js hydration",
        metadata={"category": "frontend", "access_level": "public"},
    )
    store.add_document(
        "docC",
        "Distributed key-value store using Raft consensus and WAL logging",
        metadata={"category": "architecture", "access_level": "confidential"},
    )

    assert store.count() == 3

    # Search query
    matches = store.search("Raft consensus architecture", top_k=2)
    assert len(matches) == 2
    matched_ids = [m[0] for m in matches]
    assert "docA" in matched_ids or "docC" in matched_ids

    # Search with metadata filter
    filtered_arch = store.search("Raft consensus", top_k=5, metadata_filter={"category": "architecture"})
    assert len(filtered_arch) == 2
    assert all(r[2]["metadata"]["category"] == "architecture" for r in filtered_arch)

    filtered_none = store.search("Raft consensus", top_k=5, metadata_filter={"category": "nonexistent"})
    assert len(filtered_none) == 0


def test_rrf_reranker_fusion_and_verification_boost():
    reranker = ReciprocalRankFusionReranker(k=60, verification_boost=0.2)

    dense_candidates = [
        ("d1", 0.95, {"id": "d1", "text": "Unverified draft proposal", "metadata": {"is_verified": False}}),
        ("d2", 0.85, {"id": "d2", "text": "Verified technical RFC", "metadata": {"is_verified": True}}),
    ]
    bm25_candidates = [
        ("d1", 4.2, {"id": "d1", "text": "Unverified draft proposal", "metadata": {"is_verified": False}}),
        ("d2", 3.8, {"id": "d2", "text": "Verified technical RFC", "metadata": {"is_verified": True}}),
    ]

    fused = reranker.fuse(dense_candidates, bm25_candidates, top_k=2)
    assert len(fused) == 2

    # Because d2 has verification_boost=0.2, it should surpass d1
    top_doc = fused[0]
    assert top_doc["id"] == "d2"
    assert top_doc["is_verified"] is True
    assert top_doc["rrf_score"] > fused[1]["rrf_score"]


def test_evidence_evaluator_sufficiency_and_conflicts():
    evaluator = EvidenceEvaluator()

    # Sufficiency test with complete evidence
    evidence = [
        {
            "id": "e1",
            "text": "The SQLite database connection pool leak was caused by missing close calls in worker loops.",
            "is_verified": True,
            "metadata": {"source": "postmortem-10"},
        }
    ]

    score, gaps = evaluator.evaluate_sufficiency("Why did SQLite connection pool leak?", evidence)
    assert score > 0.6
    assert len(gaps) == 0

    # Sufficiency test with missing evidence
    score_empty, gaps_empty = evaluator.evaluate_sufficiency("Why did the deployment fail?", [])
    assert score_empty == 0.0
    assert len(gaps_empty) > 0

    # Conflict detection test
    conflicting_evidence = [
        {
            "id": "doc-old",
            "text": "Timeout is 30s",
            "metadata": {"provenance": {"superseded_by": "doc-new"}},
        },
        {
            "id": "doc-new",
            "text": "Timeout is 10s",
            "metadata": {"provenance": {}},
        },
    ]

    conflicts = evaluator.detect_conflicts(conflicting_evidence)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "superseded"
    assert conflicts[0]["older_doc_id"] == "doc-old"
    assert conflicts[0]["newer_doc_id"] == "doc-new"


def test_hybrid_retrieval_rbac_filtering():
    engine = HybridRetrievalEngine()

    engine.index_document(
        "public-1",
        "Public knowledge about company working hours and guidelines.",
        metadata={"access_level": "public"},
    )
    engine.index_document(
        "eng-1",
        "Internal engineering database credentials and connection parameters.",
        metadata={"access_level": "confidential", "allowed_departments": ["Engineering"]},
    )
    engine.index_document(
        "private-alice",
        "Alice personal salary negotiation notes and private reviews.",
        metadata={"access_level": "restricted", "owner_employee_id": "alice"},
    )

    # Bob (QA) querying
    bob_results = engine.retrieve(
        "credentials guidelines salary", requester_id="bob", department="QA", include_unverified=True
    )
    bob_ids = [r["id"] for r in bob_results]
    assert "public-1" in bob_ids
    assert "eng-1" not in bob_ids
    assert "private-alice" not in bob_ids

    # Alice (Engineering) querying
    alice_results = engine.retrieve(
        "credentials guidelines salary", requester_id="alice", department="Engineering", include_unverified=True
    )
    alice_ids = [r["id"] for r in alice_results]
    assert "public-1" in alice_ids
    assert "eng-1" in alice_ids
    assert "private-alice" in alice_ids  # Alice can access her own restricted notes

    # Boss querying
    boss_results = engine.retrieve("credentials guidelines salary", requester_id="BossAI", include_unverified=True)
    boss_ids = [r["id"] for r in boss_results]
    assert "public-1" in boss_ids
    assert "eng-1" in boss_ids
    assert "private-alice" in boss_ids


@pytest.mark.asyncio
async def test_agentic_rag_bounded_cycles_and_decomposition():
    retriever = HybridRetrievalEngine()
    retriever.index_document(
        "doc-fail-1",
        "The Redis cache cluster experienced a failover failure due to split-brain network partition.",
        metadata={**_trusted_metadata("postmortem-redis"), "source": "postmortem-redis"},
    )
    retriever.index_document(
        "doc-fail-2",
        "Task failure logs indicate worker nodes were unable to reconnect to Redis master within 15s timeout.",
        metadata={**_trusted_metadata("task-failure-log-88"), "source": "task-failure-log-88"},
    )

    agentic_rag = AgenticRAGEngine(retriever=retriever, max_rounds=3, sufficiency_threshold=0.7)

    # 1. Simple query should use 1 round
    simple_res = await agentic_rag.research("Redis cache")
    assert simple_res["strategy"] == "simple_hybrid"
    assert simple_res["rounds"] == 1
    assert simple_res["evidence_count"] > 0

    # 2. Complex causal question triggers multi-round research
    complex_q = "Why did the Redis cluster failover fail and what decisions caused the worker task failure?"
    complex_res = await agentic_rag.research(complex_q)

    assert complex_res["strategy"] == "agentic_multiround"
    assert 1 <= complex_res["rounds"] <= 3  # Strictly bounded by max_rounds
    assert complex_res["evidence_count"] >= 1
    assert "postmortem-redis" in complex_res["answer"] or "task-failure-log" in complex_res["answer"]
    assert complex_res["duration_ms"] > 0


def test_hybrid_retrieval_confidential_access_leak_prevention():
    engine = HybridRetrievalEngine()

    # Index confidential document with NO department or project set
    engine.index_document(
        "conf-exec",
        "Executive compensation review and confidential acquisition terms.",
        metadata={"access_level": "confidential", "owner_employee_id": "ceo"},
    )

    # Ordinary employee querying without explicit authorization MUST NOT access it
    results_emp = engine.retrieve(
        "compensation acquisition", requester_id="emp-1", department="Engineering", include_unverified=True
    )
    assert not any(r["id"] == "conf-exec" for r in results_emp)

    # Owner can access it
    results_owner = engine.retrieve("compensation acquisition", requester_id="ceo", include_unverified=True)
    assert any(r["id"] == "conf-exec" for r in results_owner)

    # Boss can access it
    results_boss = engine.retrieve("compensation acquisition", requester_id="BossAI", include_unverified=True)
    assert any(r["id"] == "conf-exec" for r in results_boss)


def test_hybrid_retrieval_defaults_to_independent_verification_evidence():
    engine = HybridRetrievalEngine()
    engine.index_document(
        "worker-claim",
        "The worker says this database migration succeeded.",
        metadata={"is_verified": True, "access_level": "public"},
    )
    engine.index_document(
        "verified-receipt",
        "Independent verifier confirmed the database migration passed tests.",
        metadata={**_trusted_metadata("memory-receipt"), "is_verified": True},
    )

    trusted_results = engine.retrieve("database migration", requester_id="BossAI")
    assert [record["id"] for record in trusted_results] == ["verified-receipt"]

    exploratory_results = engine.retrieve("database migration", requester_id="BossAI", include_unverified=True)
    assert {record["id"] for record in exploratory_results} == {"worker-claim", "verified-receipt"}


@pytest.mark.asyncio
async def test_unverified_episode_cannot_enter_trusted_hybrid_index():
    memory = SixTierMemoryStore()
    unverified = await memory.store_episodic(
        task_id="TASK-RAW-EPISODE",
        employee_id="ANC-EMP-RAW",
        outcome="success",
        summary="Worker claims this migration improved database performance.",
    )
    verified = await memory.store_episodic(
        task_id="TASK-VERIFIED-EPISODE",
        employee_id="ANC-EMP-VERIFIED",
        outcome="success",
        summary="Independent review confirms the database performance migration passed tests.",
        verification_receipt={
            "task_id": "TASK-VERIFIED-EPISODE",
            "attempt_id": "attempt-independent-review",
            "verifier_id": "independent-verifier",
            "passed": True,
            "evidence_refs": ["verifier:attempt-independent-review:tests"],
        },
    )
    retriever = HybridRetrievalEngine()

    assert not retriever.index_record(unverified)
    assert retriever.index_record(verified)
    results = retriever.retrieve("database performance migration", requester_id="BossAI")
    assert [result["id"] for result in results] == [verified.id]


def test_rrf_reranker_temporal_decay_favors_recent_verified_knowledge():
    from datetime import datetime
    ref_now = datetime(2026, 11, 10, 12, 0, 0)
    reranker = ReciprocalRankFusionReranker(
        k=60,
        verification_boost=0.15,
        temporal_decay=True,
        decay_half_life_days=30.0,
        now=ref_now,
    )

    # doc_fresh was verified 1 day ago
    doc_fresh = {
        "id": "doc-fresh",
        "text": "FastAPI async connection pooling guidelines v2",
        "metadata": {
            "is_verified": True,
            "verification_status": "verified",
            "verified_at": "2026-11-09T12:00:00",
        },
    }
    # doc_stale was verified 90 days ago (3 half-lives)
    doc_stale = {
        "id": "doc-stale",
        "text": "FastAPI async connection pooling guidelines v1",
        "metadata": {
            "is_verified": True,
            "verification_status": "verified",
            "verified_at": "2026-08-12T12:00:00",
        },
    }

    # Both have tied ranking in dense and BM25
    dense_candidates = [
        ("doc-fresh", 0.90, doc_fresh),
        ("doc-stale", 0.90, doc_stale),
    ]
    bm25_candidates = [
        ("doc-fresh", 5.0, doc_fresh),
        ("doc-stale", 5.0, doc_stale),
    ]

    fused = reranker.fuse(dense_candidates, bm25_candidates, top_k=2)
    assert len(fused) == 2
    assert fused[0]["id"] == "doc-fresh"
    assert fused[1]["id"] == "doc-stale"
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]


def test_hybrid_retrieval_restricted_memory_leak_prevention():
    engine = HybridRetrievalEngine()

    # Restricted document with NO individual employee owner (executive/boardroom restricted)
    engine.index_document(
        "restricted-executive-memo",
        "Executive board deliberation on acquisition and confidential runway.",
        metadata={"access_level": "restricted", "owner_employee_id": None},
    )

    # Restricted document owned by employee Alice
    engine.index_document(
        "restricted-alice-notes",
        "Alice personal debugging scratchpad for memory leaks.",
        metadata={"access_level": "restricted", "owner_employee_id": "ANC-EMP-ALICE"},
    )

    # Bob MUST NOT retrieve either restricted document
    bob_results = engine.retrieve("deliberation scratchpad", requester_id="ANC-EMP-BOB", include_unverified=True)
    assert not any(r["id"] in ("restricted-executive-memo", "restricted-alice-notes") for r in bob_results)

    # Alice can only retrieve her own notes, NOT the unowned executive memo
    alice_results = engine.retrieve("deliberation scratchpad", requester_id="ANC-EMP-ALICE", include_unverified=True)
    assert any(r["id"] == "restricted-alice-notes" for r in alice_results)
    assert not any(r["id"] == "restricted-executive-memo" for r in alice_results)

    # BossAI has executive clearance for all
    boss_results = engine.retrieve("deliberation scratchpad", requester_id="BossAI", include_unverified=True)
    assert any(r["id"] == "restricted-executive-memo" for r in boss_results)
    assert any(r["id"] == "restricted-alice-notes" for r in boss_results)


def test_empty_query_handling_in_retrieval_and_vector_store():
    store = DenseVectorStore()
    store.add_document("doc-1", "Some arbitrary text about databases.")
    assert store.search("") == []
    assert store.search("   ") == []

    engine = HybridRetrievalEngine()
    engine.index_document("doc-1", "Some arbitrary text about databases.")
    assert engine.retrieve("", requester_id="BossAI") == []
    assert engine.retrieve("   ", requester_id="BossAI") == []


def test_rrf_reranker_deduplication_and_timezone_aware_now():
    now_aware = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
    reranker = ReciprocalRankFusionReranker(
        k=60,
        temporal_decay=True,
        decay_half_life_days=30.0,
        now=now_aware,
    )
    doc_a = {
        "id": "doc-a",
        "text": "Guideline A",
        "metadata": {"is_verified": True, "verified_at": "2026-11-09T12:00:00"},
    }

    # Duplicate entries in candidate list
    dense = [("doc-a", 0.95, doc_a), ("doc-a", 0.50, doc_a)]
    bm25 = [("doc-a", 4.0, doc_a), ("doc-a", 1.0, doc_a)]

    fused = reranker.fuse(dense, bm25, top_k=5)
    assert len(fused) == 1
    assert fused[0]["dense_rank"] == 1
    assert fused[0]["bm25_rank"] == 1


def test_hybrid_retrieval_case_insensitive_department_rbac():
    engine = HybridRetrievalEngine()
    engine.index_document(
        "dept-eng-doc",
        "Engineering specification for high performance distributed caching.",
        metadata={
            "access_level": "confidential",
            "allowed_departments": ["Engineering"],
            "owner_employee_id": None,
        },
    )

    # Department casing lowercase 'engineering' matches 'Engineering'
    matched = engine.retrieve("distributed caching", requester_id="ANC-EMP-01", department="engineering", include_unverified=True)
    assert any(r["id"] == "dept-eng-doc" for r in matched)

    # Mismatched department 'marketing' is blocked
    blocked = engine.retrieve("distributed caching", requester_id="ANC-EMP-02", department="marketing", include_unverified=True)
    assert not any(r["id"] == "dept-eng-doc" for r in blocked)



