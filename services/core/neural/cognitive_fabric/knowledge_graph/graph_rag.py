from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from neural.cognitive_fabric.knowledge_graph.graph_retriever import GraphRetriever
from neural.cognitive_fabric.knowledge_graph.temporal_graph import TemporalKnowledgeGraph
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.knowledge_graph.graph_rag")


class GraphRAGEngine:
    """
    GraphRAG & Agentic GraphRAG Engine:
    Combines Knowledge Graph Traversal with Vector/BM25 Hybrid Retrieval.
    Traverses multi-hop causal paths and grounds synthesized answers in verified receipts.
    """

    def __init__(
        self,
        kg: TemporalKnowledgeGraph,
        hybrid_retriever: Optional[HybridRetrievalEngine] = None,
        retriever: Optional[GraphRetriever] = None,
    ) -> None:
        self.kg = kg
        self.retriever = retriever or GraphRetriever(kg)
        self.hybrid_retriever = hybrid_retriever or HybridRetrievalEngine()

    async def query(
        self,
        question: str,
        target_entity_id: Optional[str] = None,
        max_hops: int = 2,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """
        Executes GraphRAG retrieval:
        1. Extract entities mentioned in question.
        2. Explore local neighborhood & multi-hop paths.
        3. Query hybrid vector/BM25 index for contextual details.
        4. Fuse graph structure + text evidence into traceable answer.
        """
        start_time = time.perf_counter()
        logger.info("Executing GraphRAG query", question=question, target_entity=target_entity_id)

        # 1. Identify starting entities
        matched_entity_ids = []
        if target_entity_id:
            matched_entity_ids.append(target_entity_id)
        matched_entity_ids.extend(self.retriever.extract_entities_from_text(question))
        matched_entity_ids = list(dict.fromkeys(matched_entity_ids))

        # 2. Graph Traversal
        graph_evidence: list[dict[str, Any]] = []
        connected_edges: list[dict[str, Any]] = []
        explored_entities: set[str] = set()

        for eid in matched_entity_ids[:3]:
            neighborhood = self.retriever.get_local_neighborhood(eid, hops=max_hops, active_only=True)
            if neighborhood["center_entity"]:
                explored_entities.add(eid)
                connected_edges.extend(neighborhood["edges"])
                for n in neighborhood["neighbors"]:
                    explored_entities.add(n["entity_id"])

        # Format graph paths / facts
        for edge in connected_edges:
            from_name = self.kg.get_entity(edge["from_entity_id"])
            to_name = self.kg.get_entity(edge["to_entity_id"])
            if from_name and to_name:
                dispute_note = ""
                if edge.get("verification_status") == "disputed":
                    disp = edge.get("properties", {}).get("dispute", {})
                    if disp.get("reason"):
                        dispute_note = f", Dispute: {disp.get('reason')}"
                fact_str = (
                    f"({from_name.name} [{from_name.entity_type.value}]) "
                    f"--[{edge['rel_type']}]--> "
                    f"({to_name.name} [{to_name.entity_type.value}]) "
                    f"(Source: {edge['source']}, Status: {edge['verification_status']}{dispute_note})"
                )
                graph_evidence.append({
                    "fact": fact_str,
                    "rel_id": edge["rel_id"],
                    "rel_type": edge["rel_type"],
                    "source": edge["source"],
                    "status": edge["verification_status"],
                })

        # 3. Hybrid vector/BM25 retrieval for contextual nuances
        hybrid_docs = self.hybrid_retriever.retrieve(
            question, top_k=5, requester_id=requester_id, department=department, projects=projects
        )

        # 4. Synthesize traceable answer
        answer = self._synthesize_graph_answer(question, graph_evidence, hybrid_docs)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "question": question,
            "entities_identified": matched_entity_ids,
            "nodes_traversed_count": len(explored_entities),
            "graph_facts_count": len(graph_evidence),
            "graph_evidence": graph_evidence[:15],
            "text_evidence_count": len(hybrid_docs),
            "text_evidence": hybrid_docs,
            "answer": answer,
            "duration_ms": duration_ms,
        }

    def _synthesize_graph_answer(
        self,
        question: str,
        graph_facts: list[dict[str, Any]],
        text_docs: list[dict[str, Any]],
    ) -> str:
        lines = [f"GraphRAG Evidence-Grounded Explanation for: '{question}'", ""]

        if graph_facts:
            lines.append("Structural Knowledge Graph Relationships:")
            for idx, fact in enumerate(graph_facts[:6], start=1):
                lines.append(f"  {idx}. {fact['fact']}")
            lines.append("")

        if text_docs:
            lines.append("Supporting Documentation & Artifact Receipts:")
            for idx, doc in enumerate(text_docs[:3], start=1):
                src = doc.get("metadata", {}).get("source", doc.get("id"))
                snippet = doc.get("text", "")[:140].replace("\n", " ")
                lines.append(f"  [{idx}] [Source: {src}]: {snippet}")
            lines.append("")

        if not graph_facts and not text_docs:
            return "No verified graph relationships or document evidence found for this query."

        return "\n".join(lines)

