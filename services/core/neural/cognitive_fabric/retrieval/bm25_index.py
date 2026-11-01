from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Optional

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by",
    "can", "did", "do", "does", "doing", "don", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only",
    "or", "other", "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she", "should",
    "so", "some", "such", "t", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "you", "your", "yours", "yourself", "yourselves"
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", text.lower())
    return [w for w in words if w not in STOPWORDS]


class BM25Index:
    """
    BM25 (Best Matching 25) Keyword Retrieval Engine.
    Implements Okapi BM25 scoring with term frequency normalization
    and inverse document frequency weighting.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_tokens: dict[str, list[str]] = {}
        self._doc_freqs: Counter[str] = Counter()
        self._doc_lengths: dict[str, int] = {}
        self._doc_metadata: dict[str, dict[str, Any]] = {}
        self._doc_texts: dict[str, str] = {}
        self._total_length = 0

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict[str, Any]] = None) -> None:
        if doc_id in self._doc_tokens:
            self.remove_document(doc_id)

        tokens = tokenize(text)
        self._doc_tokens[doc_id] = tokens
        self._doc_texts[doc_id] = text
        self._doc_metadata[doc_id] = metadata or {}
        doc_len = len(tokens)
        self._doc_lengths[doc_id] = doc_len
        self._total_length += doc_len

        unique_tokens = set(tokens)
        for t in unique_tokens:
            self._doc_freqs[t] += 1

    def remove_document(self, doc_id: str) -> None:
        if doc_id not in self._doc_tokens:
            return
        tokens = self._doc_tokens.pop(doc_id)
        self._doc_texts.pop(doc_id, None)
        self._doc_metadata.pop(doc_id, None)
        doc_len = self._doc_lengths.pop(doc_id, 0)
        self._total_length -= doc_len

        for t in set(tokens):
            self._doc_freqs[t] -= 1
            if self._doc_freqs[t] <= 0:
                del self._doc_freqs[t]

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filter: Optional[dict[str, Any]] = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        n_docs = len(self._doc_tokens)
        if n_docs == 0:
            return []

        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        avg_dl = self._total_length / n_docs if n_docs > 0 else 1.0
        scores: list[tuple[str, float, dict[str, Any]]] = []

        for doc_id, tokens in self._doc_tokens.items():
            meta = self._doc_metadata[doc_id]
            if metadata_filter:
                match = True
                for k, v in metadata_filter.items():
                    if meta.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            tf_counter = Counter(tokens)
            doc_len = self._doc_lengths[doc_id]
            doc_score = 0.0

            for q in q_tokens:
                df = self._doc_freqs.get(q, 0)
                if df == 0:
                    continue
                # Standard Okapi BM25 IDF
                idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
                tf = tf_counter.get(q, 0)
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / avg_dl))
                doc_score += idf * (numerator / denominator)

            if doc_score > 0.0:
                doc_payload = {
                    "id": doc_id,
                    "text": self._doc_texts.get(doc_id, ""),
                    "metadata": meta,
                }
                scores.append((doc_id, doc_score, doc_payload))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def count(self) -> int:
        return len(self._doc_tokens)

