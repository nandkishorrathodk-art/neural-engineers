from __future__ import annotations

import hashlib
import math
from typing import Any, Optional

from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.retrieval.vector_store")


def _compute_embedding(text: str, dim: int = 128) -> list[float]:
    """
    Computes a deterministic dense normalized embedding vector for text.
    Uses n-gram hashing and sine/cosine projection to preserve lexical and semantic cues
    without requiring external network calls or heavy GPU checkpoints in constrained 16GB environments.
    """
    cleaned = text.lower().strip()
    words = cleaned.split()
    vec = [0.0] * dim

    for i, word in enumerate(words):
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        for d in range(dim):
            # Positional & hash projection
            phase = (h % 10007) * 0.001 + d * 0.1 + (i % 5) * 0.2
            vec[d] += math.sin(phase) + math.cos(phase * 1.5)

    # Normalize vector to unit length
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        vec = [v / norm for v in vec]
    return vec


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Assuming already normalized, dot is cosine similarity
    return max(-1.0, min(1.0, dot))


class DenseVectorStore:
    """
    Dense Vector Storage & Cosine Similarity Retriever.
    Supports in-memory vector indexing and optional ChromaDB collection sync.
    """

    def __init__(self, collection_name: str = "ancos_knowledge") -> None:
        self.collection_name = collection_name
        self._docs: dict[str, dict[str, Any]] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._chroma_client = None
        self._chroma_collection = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb
            self._chroma_client = chromadb.Client()
            self._chroma_collection = self._chroma_client.get_or_create_collection(self.collection_name)
            logger.info("ChromaDB vector collection initialized", collection=self.collection_name)
        except Exception as e:
            logger.info("ChromaDB initialization skipped; operating in native dense index mode", reason=str(e))
            self._chroma_client = None
            self._chroma_collection = None

    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        embedding: Optional[list[float]] = None,
    ) -> None:
        meta = metadata or {}
        emb = embedding or _compute_embedding(text)
        self._docs[doc_id] = {"id": doc_id, "text": text, "metadata": meta}
        self._embeddings[doc_id] = emb

        if self._chroma_collection is not None:
            try:
                # Sanitize metadata for ChromaDB (flat primitive values only)
                clean_meta = {}
                for k, v in meta.items():
                    if isinstance(v, (str, int, float, bool)):
                        clean_meta[k] = v
                    elif isinstance(v, list):
                        clean_meta[k] = ",".join(str(x) for x in v)
                self._chroma_collection.upsert(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[clean_meta] if clean_meta else None,
                    embeddings=[emb],
                )
            except Exception as e:
                logger.debug("ChromaDB upsert skipped", error=str(e))

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """
        Returns list of (doc_id, similarity_score, doc_dict) sorted descending by score.
        """
        if not self._docs or not query or not query.strip():
            return []

        query_emb = _compute_embedding(query)
        scored: list[tuple[str, float, dict[str, Any]]] = []

        for doc_id, doc in self._docs.items():
            meta = doc["metadata"]
            if metadata_filter:
                match = True
                for k, v in metadata_filter.items():
                    if meta.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            emb = self._embeddings[doc_id]
            sim = _cosine_similarity(query_emb, emb)
            # Map cosine [-1, 1] to [0, 1]
            score = (sim + 1.0) / 2.0
            scored.append((doc_id, score, doc))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self._docs)

