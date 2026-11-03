from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from neural.cognitive_fabric.retrieval.evidence_evaluator import EvidenceEvaluator
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.retrieval.agentic")


class AgenticRAGEngine:
    """
    Adaptive Agentic RAG Engine:
    Executes bounded, multi-round research cycles:
    UNDERSTAND -> CLASSIFY -> PLAN -> RETRIEVE -> INSPECT -> GAP-ANALYSIS -> REFINE -> VERIFY -> SYNTHESIZE.
    Detects conflicting claims and evaluates evidence sufficiency.
    Never allows unbounded retrieval loops.
    """

    def __init__(
        self,
        retriever: HybridRetrievalEngine,
        evaluator: Optional[EvidenceEvaluator] = None,
        max_rounds: int = 3,
        timeout_seconds: float = 12.0,
        sufficiency_threshold: float = 0.75,
    ) -> None:
        self.retriever = retriever
        self.evaluator = evaluator or EvidenceEvaluator()
        self.max_rounds = min(5, max(1, max_rounds))
        self.timeout_seconds = timeout_seconds
        self.sufficiency_threshold = sufficiency_threshold

    def classify_information_need(self, query: str) -> str:
        """
        Classifies whether a query requires 'simple' 1-shot retrieval or 'agentic' multi-round research.
        """
        q_lower = query.lower().strip()
        words = q_lower.split()

        # Simple indicators: direct definitions, single-entity lookups, short questions
        if len(words) <= 5 and not any(k in q_lower for k in ["why", "compare", "conflict", "failure", "trace", "and"]):
            return "simple"

        complex_indicators = [
            "why", "how come", "caused", "failure", "conflict", "contradiction",
            "compare", "relationship", "decision", "trade-off", "postmortem", "impact"
        ]
        if any(ind in q_lower for ind in complex_indicators) or len(words) > 10:
            return "agentic"

        return "simple"

    def decompose_query(self, query: str) -> list[str]:
        """
        Decomposes complex research questions into targeted sub-queries.
        """
        sub_queries = [query]
        q_lower = query.lower()

        # Split on conjunctions or clauses if complex
        if " and " in q_lower:
            parts = [p.strip() for p in query.split(" and ") if len(p.strip()) > 5]
            if len(parts) >= 2:
                sub_queries.extend(parts)

        # Failure / Root cause questions
        if "failure" in q_lower or "failed" in q_lower:
            sub_queries.append(f"task failure logs {query}")
            sub_queries.append(f"decisions and meetings related to {query}")

        return list(dict.fromkeys(sub_queries))[:3]  # Deduplicate, max 3 initial sub-queries

    async def research(
        self,
        query: str,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """
        Executes complete bounded agentic research cycle.
        """
        start_time = time.perf_counter()
        logger.info("Starting Agentic RAG research", query=query, requester=requester_id)

        need = self.classify_information_need(query)
        if need == "simple":
            # Direct 1-shot retrieval
            results = self.retriever.retrieve(
                query, top_k=5, requester_id=requester_id, department=department, projects=projects
            )
            sufficiency, gaps = self.evaluator.evaluate_sufficiency(query, results)
            return {
                "query": query,
                "strategy": "simple_hybrid",
                "rounds": 1,
                "evidence_count": len(results),
                "evidence": results,
                "sufficiency_score": sufficiency,
                "conflicts": [],
                "answer": self._synthesize_answer(query, results),
                "duration_ms": (time.perf_counter() - start_time) * 1000,
            }

        # Multi-round Agentic RAG
        accumulated_evidence: dict[str, dict[str, Any]] = {}
        sub_queries = self.decompose_query(query)
        current_queries = sub_queries
        round_num = 0
        conflicts = []
        final_sufficiency = 0.0

        while round_num < self.max_rounds:
            round_num += 1
            if time.perf_counter() - start_time > self.timeout_seconds:
                logger.warning("Agentic RAG loop timeout reached", round=round_num)
                break

            for sq in current_queries:
                batch = self.retriever.retrieve(
                    sq, top_k=5, requester_id=requester_id, department=department, projects=projects
                )
                for item in batch:
                    accumulated_evidence[item["id"]] = item

            evidence_list = list(accumulated_evidence.values())
            final_sufficiency, gaps = self.evaluator.evaluate_sufficiency(query, evidence_list)
            conflicts = self.evaluator.detect_conflicts(evidence_list)

            # Check if sufficiency condition met
            if final_sufficiency >= self.sufficiency_threshold:
                logger.info("Sufficiency threshold reached in Agentic RAG", round=round_num, score=final_sufficiency)
                break

            # Refine search: generate gap-filling queries
            if gaps and round_num < self.max_rounds:
                current_queries = [f"{query} {g}" for g in gaps[:2]]
            else:
                break

        final_evidence = list(accumulated_evidence.values())
        # Sort evidence by verified status and score
        final_evidence.sort(key=lambda x: (1 if x.get("is_verified") else 0, x.get("rrf_score", 0.0)), reverse=True)

        return {
            "query": query,
            "strategy": "agentic_multiround",
            "rounds": round_num,
            "evidence_count": len(final_evidence),
            "evidence": final_evidence[:10],
            "sufficiency_score": final_sufficiency,
            "conflicts": conflicts,
            "escalation_required": final_sufficiency < 0.4,
            "answer": self._synthesize_answer(query, final_evidence[:10]),
            "duration_ms": (time.perf_counter() - start_time) * 1000,
        }

    def _synthesize_answer(self, query: str, evidence: list[dict[str, Any]]) -> str:
        if not evidence:
            return "No relevant verified evidence found in organizational memory."

        lines = [f"Synthesized Answer for query: '{query}'"]
        for idx, item in enumerate(evidence[:5], start=1):
            source = item.get("metadata", {}).get("source", item.get("id"))
            status = "VERIFIED" if item.get("is_verified") else "UNVERIFIED"
            text_snippet = item.get("text", "")[:160].replace("\n", " ")
            lines.append(f"[{idx}] ({status}) [Source: {source}]: {text_snippet}")

        return "\n".join(lines)

