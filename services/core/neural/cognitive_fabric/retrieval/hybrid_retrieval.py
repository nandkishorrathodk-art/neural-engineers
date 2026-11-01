from __future__ import annotations

from typing import Any, Optional, Sequence

from neural.cognitive_fabric.memory.models import MemoryRecord
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

    def index_record(self, record: MemoryRecord) -> None:
        meta = record.to_dict()
        self.vector_store.add_document(record.id, record.content, metadata=meta)
        self.bm25_index.add_document(record.id, record.content, metadata=meta)

    def index_document(self, doc_id: str, text: str, metadata: Optional[dict[str, Any]] = None) -> None:
        meta = metadata or {}
        self.vector_store.add_document(doc_id, text, metadata=meta)
        self.bm25_index.add_document(doc_id, text, metadata=meta)

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Executes hybrid retrieval:
        1. Query dense index.
        2. Query BM25 index.
        3. Filter by access control permissions (pre-return RBAC enforcement).
        4. Fuse and rerank via RRF.
        """
        # Fetch dense and BM25 candidates
        dense_results = self.vector_store.search(query, top_k=top_k * 3, metadata_filter=metadata_filter)
        bm25_results = self.bm25_index.search(query, top_k=top_k * 3, metadata_filter=metadata_filter)

        # Apply access-control filtering
        filtered_dense = []
        for doc_id, score, doc in dense_results:
            meta = doc.get("metadata", {})
            if self._check_access(meta, requester_id, department, projects):
                filtered_dense.append((doc_id, score, doc))

        filtered_bm25 = []
        for doc_id, score, doc in bm25_results:
            meta = doc.get("metadata", {})
            if self._check_access(meta, requester_id, department, projects):
                filtered_bm25.append((doc_id, score, doc))

        # Reciprocal Rank Fusion
        fused = self.reranker.fuse(filtered_dense, filtered_bm25, top_k=top_k)
        return fused

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

        allowed_depts = meta.get("allowed_departments", [])
        if allowed_depts and (not department or department not in allowed_depts):
            return False

        allowed_projs = meta.get("allowed_projects", [])
        if allowed_projs:
            active_projs = set(projects or [])
            if not active_projs.intersection(set(allowed_projs)):
                return False

        # Confidential records require explicit department or project assignment
        if acc_level == "confidential":
            has_dept = bool(department and allowed_depts and department in allowed_depts)
            has_proj = bool(projects and allowed_projs and set(projects).intersection(set(allowed_projs)))
            if not (has_dept or has_proj or (owner == requester_id)):
                return False

        return True

