from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from neural.cognitive_fabric.knowledge_graph.graph_rag import GraphRAGEngine
from neural.cognitive_fabric.knowledge_graph.graph_retriever import GraphRetriever
from neural.cognitive_fabric.knowledge_graph.models import EntityType, RelationType
from neural.cognitive_fabric.knowledge_graph.temporal_graph import TemporalKnowledgeGraph
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine


def test_temporal_knowledge_graph_entities_and_relationships():
    kg = TemporalKnowledgeGraph()

    # Add entities
    kg.add_entity("org-1", EntityType.COMPANY, "ANCOS Inc.")
    kg.add_entity("dept-eng", EntityType.DEPARTMENT, "Core Engineering")
    kg.add_entity("emp-alice", EntityType.EMPLOYEE, "Alice Architect")
    kg.add_entity("skill-python", EntityType.SKILL, "Python Backend")
    kg.add_entity("proj-phoenix", EntityType.PROJECT, "Project Phoenix")

    assert kg.count_entities() == 5
    assert kg.get_entity("emp-alice").name == "Alice Architect"

    # Add active verified relationships
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rel_dept = kg.add_relationship(
        from_entity_id="emp-alice",
        to_entity_id="dept-eng",
        rel_type=RelationType.BELONGS_TO_DEPARTMENT,
        source="hr_records",
        verification_status="verified",
        valid_from=now - timedelta(days=30),
    )
    assert rel_dept.is_active

    rel_skill = kg.add_relationship(
        from_entity_id="emp-alice",
        to_entity_id="skill-python",
        rel_type=RelationType.HAS_SKILL,
        source="certification_exam",
        verification_status="verified",
        properties={"skill_level": "expert", "score": 98.5},
        valid_from=now - timedelta(days=20),
    )
    assert rel_skill.is_active

    rel_proj = kg.add_relationship(
        from_entity_id="emp-alice",
        to_entity_id="proj-phoenix",
        rel_type=RelationType.CONTRIBUTED_TO_PROJECT,
        source="git_commit_ledger",
        verification_status="verified",
        valid_from=now - timedelta(days=10),
    )
    assert rel_proj.is_active
    assert kg.count_relationships() == 3


def test_temporal_graph_as_of_queries_and_superseding():
    kg = TemporalKnowledgeGraph()
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t1 = datetime(2026, 2, 1, 12, 0, 0)
    t2 = datetime(2026, 3, 1, 12, 0, 0)

    kg.add_entity("emp-bob", EntityType.EMPLOYEE, "Bob Developer")
    kg.add_entity("skill-junior", EntityType.SKILL, "Junior Python")
    kg.add_entity("skill-senior", EntityType.SKILL, "Senior Python Architecture")

    # Bob had Junior Python from t0 to t1
    rel_old = kg.add_relationship(
        from_entity_id="emp-bob",
        to_entity_id="skill-junior",
        rel_type=RelationType.HAS_SKILL,
        source="initial_assessment",
        valid_from=t0,
        valid_to=t1,
        verification_status="verified",
    )
    rel_old.mark_superseded("rel_new")

    # Bob has Senior Python from t1 onwards
    kg.add_relationship(
        from_entity_id="emp-bob",
        to_entity_id="skill-senior",
        rel_type=RelationType.HAS_SKILL,
        source="promotion_board",
        valid_from=t1,
        valid_to=None,
        verification_status="verified",
    )

    # Query as of t0 + 10 days (should have Junior Python)
    mid_t0 = datetime(2026, 1, 10, 12, 0, 0)
    active_at_t0 = kg.query_as_of(mid_t0)
    assert len(active_at_t0) == 1
    assert active_at_t0[0].to_entity_id == "skill-junior"

    # Query as of t2 (should have Senior Python)
    active_at_t2 = kg.query_as_of(t2)
    assert len(active_at_t2) == 1
    assert active_at_t2[0].to_entity_id == "skill-senior"

    # History timeline of Bob
    history = kg.get_entity_history("emp-bob")
    assert len(history) == 2


def test_superseding_and_disputing_decisions():
    kg = TemporalKnowledgeGraph()

    kg.add_entity("dec-1", EntityType.DECISION, "Use MongoDB for Time-Series")
    kg.add_entity("dec-2", EntityType.DECISION, "Migrate Time-Series to SQLite WAL + Timescale")

    kg.add_relationship(
        from_entity_id="dec-1",
        to_entity_id="dec-1",
        rel_type=RelationType.PRODUCED_ARTIFACT,
        source="board_meeting_01",
        verification_status="verified",
    )

    # Supersede dec-1 by dec-2
    ok = kg.supersede_decision("dec-1", "dec-2", source="board_meeting_08")
    assert ok

    # Check that dec-1 has a SUPERSEDED_BY edge to dec-2
    history_dec1 = kg.get_entity_history("dec-1")
    superseded_rel = [r for r in history_dec1 if r["rel_type"] == RelationType.SUPERSEDED_BY.value]
    assert len(superseded_rel) == 1
    assert superseded_rel[0]["to_entity_id"] == "dec-2"

    # Dispute a relationship
    rel = kg.add_relationship(
        from_entity_id="dec-2",
        to_entity_id="dec-2",
        rel_type=RelationType.VERIFIED_BY,
        source="external_audit",
        verification_status="unverified",
    )
    kg.dispute_relationship(rel.rel_id, reason="Audit methodology incomplete", disputer="LeadAuditor")
    assert rel.verification_status == "disputed"


def test_graph_retriever_multi_hop_and_entity_extraction():
    kg = TemporalKnowledgeGraph()
    kg.add_entity("emp-carol", EntityType.EMPLOYEE, "Carol DevOps")
    kg.add_entity("task-deploy", EntityType.TASK, "Deploy Kubernetes Pods")
    kg.add_entity("fail-oom", EntityType.FAILURE, "OOMKilled Container Out of Memory")

    kg.add_relationship(
        from_entity_id="emp-carol",
        to_entity_id="task-deploy",
        rel_type=RelationType.ASSIGNED_TO,
        source="jira_export",
        verification_status="verified",
    )
    kg.add_relationship(
        from_entity_id="task-deploy",
        to_entity_id="fail-oom",
        rel_type=RelationType.CAUSED_FAILURE,
        source="k8s_events_log",
        verification_status="verified",
    )

    retriever = GraphRetriever(kg)

    # Entity extraction from text
    extracted = retriever.extract_entities_from_text("Carol DevOps experienced an OOMKilled Container error")
    assert "emp-carol" in extracted or "fail-oom" in extracted

    # Local neighborhood (1-hop)
    n1 = retriever.get_local_neighborhood("emp-carol", hops=1)
    assert len(n1["neighbors"]) == 1
    assert n1["neighbors"][0]["entity_id"] == "task-deploy"

    # Multi-hop neighborhood (2-hop)
    n2 = retriever.get_local_neighborhood("emp-carol", hops=2)
    neighbor_ids = [nb["entity_id"] for nb in n2["neighbors"]]
    assert "fail-oom" in neighbor_ids

    # Multi-hop path finding
    paths = retriever.find_multi_hop_paths("emp-carol", "fail-oom", max_hops=3)
    assert len(paths) >= 1
    p0 = paths[0]
    assert len(p0) == 3
    assert p0[0]["entity_id"] == "emp-carol"
    assert p0[1]["entity_id"] == "task-deploy"
    assert p0[2]["entity_id"] == "fail-oom"

    # Community summary
    comm = retriever.get_community_summary(EntityType.EMPLOYEE)
    assert comm["total_count"] >= 1
    assert "Carol DevOps" in comm["entities"]


@pytest.mark.asyncio
async def test_graph_rag_evidence_grounded_query():
    kg = TemporalKnowledgeGraph()
    kg.add_entity("proj-alpha", EntityType.PROJECT, "Project Alpha")
    kg.add_entity("task-auth", EntityType.TASK, "Implement OAuth2 Flow")
    kg.add_entity("fail-leak", EntityType.FAILURE, "Token Secret Leakage Failure")

    kg.add_relationship(
        from_entity_id="proj-alpha",
        to_entity_id="task-auth",
        rel_type=RelationType.DEPENDS_ON,
        source="pm_plan",
        verification_status="verified",
    )
    kg.add_relationship(
        from_entity_id="task-auth",
        to_entity_id="fail-leak",
        rel_type=RelationType.CAUSED_FAILURE,
        source="security_postmortem_404",
        verification_status="verified",
    )

    hybrid = HybridRetrievalEngine()
    hybrid.index_document(
        "doc-postmortem-404",
        "During OAuth2 implementation in Project Alpha, unencrypted tokens were logged in debug output.",
        metadata={
            "verification_status": "verified",
            "verified_at": "2026-11-04T12:00:00",
            "source": "security_postmortem_404",
            "access_level": "public",
            "metadata": {"task_id": "TASK-SECURITY-404"},
            "provenance": {
                "verified_by": "security-verifier",
                "independent_verification_receipt": {
                    "passed": True,
                    "task_id": "TASK-SECURITY-404",
                    "attempt_id": "attempt-security-404",
                    "verifier_id": "security-verifier",
                    "evidence_refs": ["test:security-postmortem"],
                },
            },
        },
    )

    graph_rag = GraphRAGEngine(kg=kg, hybrid_retriever=hybrid)

    result = await graph_rag.query("Why did Project Alpha experience Token Secret Leakage Failure?")

    assert result["graph_facts_count"] >= 1
    assert result["text_evidence_count"] >= 1
    assert len(result["graph_evidence"]) >= 1
    assert "Structural Knowledge Graph Relationships" in result["answer"]
    assert "security_postmortem_404" in result["answer"]
    assert result["duration_ms"] > 0


def test_temporal_graph_cyclic_relationships_and_deduplication():
    """Edge Case 2: Multi-hop graph with cycle A -> B -> C -> A. Verifies no infinite loop or duplicate edges."""
    kg = TemporalKnowledgeGraph()
    kg.add_entity("node-A", EntityType.TASK, "Task A")
    kg.add_entity("node-B", EntityType.TASK, "Task B")
    kg.add_entity("node-C", EntityType.TASK, "Task C")

    # Form directed cycle: A -> B -> C -> A
    kg.add_relationship("node-A", "node-B", RelationType.DEPENDS_ON, source="plan", verification_status="verified")
    kg.add_relationship("node-B", "node-C", RelationType.DEPENDS_ON, source="plan", verification_status="verified")
    kg.add_relationship("node-C", "node-A", RelationType.DEPENDS_ON, source="plan", verification_status="verified")

    retriever = GraphRetriever(kg)
    # Explore neighborhood of node-A with 3 hops (traversing the cycle)
    neighborhood = retriever.get_local_neighborhood("node-A", hops=3, active_only=True)

    assert neighborhood["center_entity"]["entity_id"] == "node-A"
    assert len(neighborhood["neighbors"]) == 2  # node-B, node-C

    # Verify no duplicate edge dicts were created in edges_found
    edge_ids = [e["rel_id"] for e in neighborhood["edges"]]
    assert len(edge_ids) == len(set(edge_ids))
    assert len(edge_ids) == 3


def test_temporal_graph_cross_department_disputes():
    """Edge Case 2: Cross-department disputed facts preserving dispute provenance and reasons."""
    kg = TemporalKnowledgeGraph()
    kg.add_entity("emp-alice", EntityType.EMPLOYEE, "Alice Architect")
    kg.add_entity("skill-k8s", EntityType.SKILL, "Kubernetes Clustering")

    rel = kg.add_relationship(
        from_entity_id="emp-alice",
        to_entity_id="skill-k8s",
        rel_type=RelationType.HAS_SKILL,
        source="self_assessment",
        verification_status="unverified",
    )

    # QA disputing the claim with rationale
    ok = kg.dispute_relationship(
        rel_id=rel.rel_id,
        reason="Failed production cluster failover test #12",
        disputer="emp-bob-qa",
        disputer_dept="QA",
    )
    assert ok is True

    updated_rel = kg._relationships[rel.rel_id]
    assert updated_rel.verification_status == "disputed"
    assert updated_rel.properties["dispute"]["disputer_dept"] == "QA"
    assert updated_rel.properties["dispute"]["reason"] == "Failed production cluster failover test #12"

