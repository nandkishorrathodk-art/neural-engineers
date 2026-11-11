from __future__ import annotations

from typing import Any, Optional, Sequence

from neural.cognitive_fabric.memory.models import (
    MemoryRecord,
    valid_independent_verification_receipt,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.bm25_index import BM25Index
from neural.cognitive_fabric.retrieval.reranker import ReciprocalRankFusionReranker
from neural.cognitive_fabric.retrieval.vector_store import DenseVectorStore
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.retrieval.hybrid")


class HybridRetrievalEngine:
    """
    Hybrid RAG Retrieval Engine:
    Combines Dense Vector Semantic Search + BM25 Lexical Keyword Search.
    Applies access-control filtering (RBAC) and Reciprocal Rank Fusion (RRF) reranking.
    """

    def __init__(
        self,
        memory_store: Optional[SixTierMemoryStore] = None,
        vector_store: Optional[DenseVectorStore] = None,
        bm25_index: Optional[BM25Index] = None,
        reranker: Optional[ReciprocalRankFusionReranker] = None,
    ) -> None:
        self.memory_store = memory_store or SixTierMemoryStore()
        self.vector_store = vector_store or DenseVectorStore()
        self.bm25_index = bm25_index or BM25Index()
        self.reranker = reranker or ReciprocalRankFusionReranker()
        self.access_controller = MemoryAccessController()

    def index_record(self, record: MemoryRecord, *, allow_unverified: bool = False) -> bool:
        if not allow_unverified and not self._has_trusted_provenance(record.to_dict()):
            return False
        meta = record.to_dict()
        self.vector_store.add_document(record.id, record.content, metadata=meta)
        self.bm25_index.add_document(record.id, record.content, metadata=meta)
        return True

    def index_document(self, doc_id: str, text: str, metadata: Optional[dict[str, Any]] = None) -> None:
        meta = metadata or {}
        self.vector_store.add_document(doc_id, text, metadata=meta)
        self.bm25_index.add_document(doc_id, text, metadata=meta)

    async def restore_persisted_memory(self, limit: int = 1000) -> int:
        """Hydrate the backing memory store and index only receipt-verified records."""
        loaded = await self.memory_store.load_persisted_records(limit=limit)
        for record in self.memory_store.query_trusted_records(requester_id="BossAI", limit=limit):
            self.index_record(record)
        return loaded

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
        metadata_filter: Optional[dict[str, Any]] = None,
        include_unverified: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Executes hybrid retrieval:
        1. Query dense index.
        2. Query BM25 index.
        3. Filter by access control permissions (pre-return RBAC enforcement).
        4. Fuse and rerank via RRF.
        """
        if not query or not query.strip():
            return []

        # Fetch dense and BM25 candidates
        dense_results = self.vector_store.search(query, top_k=top_k * 3, metadata_filter=metadata_filter)
        bm25_results = self.bm25_index.search(query, top_k=top_k * 3, metadata_filter=metadata_filter)

        # Apply access-control filtering
        filtered_dense = []
        for doc_id, score, doc in dense_results:
            meta = doc.get("metadata") or {}
            if not include_unverified and not self._has_trusted_provenance(meta):
                continue
            if self._check_access(meta, requester_id, department, projects):
                filtered_dense.append((doc_id, score, doc))

        filtered_bm25 = []
        for doc_id, score, doc in bm25_results:
            meta = doc.get("metadata") or {}
            if not include_unverified and not self._has_trusted_provenance(meta):
                continue
            if self._check_access(meta, requester_id, department, projects):
                filtered_bm25.append((doc_id, score, doc))

        # Reciprocal Rank Fusion
        fused = self.reranker.fuse(filtered_dense, filtered_bm25, top_k=top_k)
        return fused

    @staticmethod
    def _has_trusted_provenance(meta: dict[str, Any]) -> bool:
        if meta.get("verification_status") != "verified" or not meta.get("verified_at"):
            return False
        provenance = meta.get("provenance")
        if not isinstance(provenance, dict):
            return False
        if meta.get("layer") == "organizational" and provenance.get("approved_by") in ("Boardroom", "BossAI", "Executive", "Governance"):
            return True
        verifier_id = provenance.get("verified_by") or provenance.get("verifier_id")
        receipt = provenance.get("independent_verification_receipt")
        task_id = (
            (meta.get("metadata") or {}).get("task_id")
            or (meta.get("record_metadata") or {}).get("task_id")
            or meta.get("task_id")
            or provenance.get("task_id")
        )
        return bool(
            verifier_id
            and isinstance(receipt, dict)
            and receipt.get("verifier_id") == verifier_id
            and valid_independent_verification_receipt(
                receipt,
                employee_id=meta.get("owner_employee_id"),
                task_id=task_id,
            )
        )

    def _check_access(
        self,
        meta: dict[str, Any],
        requester_id: str,
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> bool:
        clean_req = requester_id.strip().lower()
        if clean_req in ("boss", "bossai", "admin", "system"):
            return True

        acc_level = meta.get("access_level", "internal")
        if acc_level == "public":
            return True

        owner = meta.get("owner_employee_id")
        if owner:
            if owner == requester_id:
                return True
            if acc_level == "restricted":
                return False

        # Any restricted record where requester is not the owner or an executive must be blocked
        if acc_level == "restricted":
            return False

        allowed_depts = meta.get("allowed_departments", [])
        if allowed_depts:
            dept_set = {d.strip().casefold() for d in allowed_depts if d}
            req_dept = department.strip().casefold() if department else None
            if not req_dept or req_dept not in dept_set:
                return False

        allowed_projs = meta.get("allowed_projects", [])
        if allowed_projs:
            proj_set = {p.strip().casefold() for p in allowed_projs if p}
            active_projs = {p.strip().casefold() for p in (projects or []) if p}
            if not active_projs.intersection(proj_set):
                return False

        # Confidential records require explicit department or project assignment
        if acc_level == "confidential":
            dept_set = {d.strip().casefold() for d in allowed_depts if d}
            req_dept = department.strip().casefold() if department else None
            has_dept = bool(req_dept and dept_set and req_dept in dept_set)
            proj_set = {p.strip().casefold() for p in allowed_projs if p}
            active_projs = {p.strip().casefold() for p in (projects or []) if p}
            has_proj = bool(active_projs and proj_set and active_projs.intersection(proj_set))
            if not (has_dept or has_proj or (owner == requester_id)):
                return False

        return True

