from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional


class ReciprocalRankFusionReranker:
    """
    Reranks and fuses search candidates from multiple retrieval strategies
    (Dense Semantic Vector + BM25 Lexical Keyword).
    Incorporates verification status, temporal decay, and evidence provenance into final ranking.
    """

    def __init__(
        self,
        k: int = 60,
        weight_dense: float = 0.5,
        weight_bm25: float = 0.5,
        verification_boost: float = 0.15,
        temporal_decay: bool = True,
        decay_half_life_days: float = 30.0,
        now: Optional[datetime] = None,
    ) -> None:
        self.k = k
        self.weight_dense = weight_dense
        self.weight_bm25 = weight_bm25
        self.verification_boost = verification_boost
        self.temporal_decay = temporal_decay
        self.decay_half_life_days = decay_half_life_days
        self._now = now

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

        seen_dense: set[str] = set()
        for rank, (doc_id, score, doc) in enumerate(dense_results, start=1):
            if doc_id in seen_dense:
                continue
            seen_dense.add(doc_id)
            dense_ranks[doc_id] = rank
            docs[doc_id] = doc
            scores[doc_id] = scores.get(doc_id, 0.0) + self.weight_dense / (self.k + rank)

        seen_bm25: set[str] = set()
        for rank, (doc_id, score, doc) in enumerate(bm25_results, start=1):
            if doc_id in seen_bm25:
                continue
            seen_bm25.add(doc_id)
            bm25_ranks[doc_id] = rank
            if doc_id not in docs:
                docs[doc_id] = doc
            scores[doc_id] = scores.get(doc_id, 0.0) + self.weight_bm25 / (self.k + rank)

        ref_now = self._now.replace(tzinfo=None) if self._now else datetime.now(timezone.utc).replace(tzinfo=None)

        # Apply temporal decay and empirical verification boost and dispute penalty
        for doc_id, doc in docs.items():
            meta = doc.get("metadata") or {}

            # Temporal decay: recent verified knowledge retains higher score
            if self.temporal_decay and self.decay_half_life_days > 0:
                raw_ts = meta.get("verified_at") or meta.get("created_at")
                if raw_ts:
                    try:
                        if isinstance(raw_ts, datetime):
                            ts_dt = raw_ts.replace(tzinfo=None)
                        else:
                            ts_str = str(raw_ts).replace("Z", "+00:00")
                            ts_dt = datetime.fromisoformat(ts_str).replace(tzinfo=None)
                        age_days = max(0.0, (ref_now - ts_dt).total_seconds() / 86400.0)
                        decay_factor = math.pow(0.5, age_days / self.decay_half_life_days)
                        scores[doc_id] = scores[doc_id] * decay_factor
                    except (ValueError, TypeError):
                        pass

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

