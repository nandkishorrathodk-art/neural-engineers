from __future__ import annotations

from typing import Any


class ReciprocalRankFusionReranker:
    """
    Reranks and fuses search candidates from multiple retrieval strategies
    (Dense Semantic Vector + BM25 Lexical Keyword).
    Incorporates verification status and evidence provenance into final ranking.
    """

    def __init__(
        self,
        k: int = 60,
        weight_dense: float = 0.5,
        weight_bm25: float = 0.5,
        verification_boost: float = 0.15,
    ) -> None:
        self.k = k
        self.weight_dense = weight_dense
        self.weight_bm25 = weight_bm25
        self.verification_boost = verification_boost

    def fuse(
        self,
        dense_results: list[tuple[str, float, dict[str, Any]]],
        bm25_results: list[tuple[str, float, dict[str, Any]]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        scores: dict[str, float] = {}
        docs: dict[str, dict[str, Any]] = {}
        dense_ranks: dict[str, int] = {}
        bm25_ranks: dict[str, int] = {}

        for rank, (doc_id, score, doc) in enumerate(dense_results, start=1):
            dense_ranks[doc_id] = rank
            docs[doc_id] = doc
            scores[doc_id] = scores.get(doc_id, 0.0) + self.weight_dense / (self.k + rank)

        for rank, (doc_id, score, doc) in enumerate(bm25_results, start=1):
            bm25_ranks[doc_id] = rank
            if doc_id not in docs:
                docs[doc_id] = doc
            scores[doc_id] = scores.get(doc_id, 0.0) + self.weight_bm25 / (self.k + rank)

        # Apply empirical verification boost and dispute penalty
        for doc_id, doc in docs.items():
            meta = doc.get("metadata", {})
            v_status = meta.get("verification_status", "")
            is_verified = (
                meta.get("is_verified") is True
                or v_status == "verified"
            )
            if is_verified:
                scores[doc_id] += self.verification_boost
            elif v_status == "disputed":
                scores[doc_id] -= self.verification_boost

        sorted_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)

        final_results = []
        for doc_id in sorted_ids[:top_k]:
            doc = docs[doc_id]
            meta = doc.get("metadata", {})
            v_stat = meta.get("verification_status", "verified" if (meta.get("is_verified") is True) else "unverified")
            final_results.append({
                "id": doc_id,
                "text": doc.get("text", ""),
                "metadata": meta,
                "rrf_score": round(scores[doc_id], 4),
                "dense_rank": dense_ranks.get(doc_id),
                "bm25_rank": bm25_ranks.get(doc_id),
                "is_verified": (
                    meta.get("is_verified") is True
                    or v_stat == "verified"
                ),
                "verification_status": v_stat,
            })

        return final_results

