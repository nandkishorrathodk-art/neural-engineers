from __future__ import annotations

import re
from typing import Any, Optional, Sequence


class EvidenceEvaluator:
    """
    Evaluates evidence sufficiency, source faithfulness, and identifies knowledge gaps.
    Detects contradictory evidence between different sources.
    """

    @staticmethod
    def evaluate_sufficiency(
        query: str,
        retrieved_evidence: Sequence[dict[str, Any]],
        required_concepts: Optional[Sequence[str]] = None,
    ) -> tuple[float, list[str]]:
        """
        Returns (sufficiency_score, missing_gaps).
        Score from 0.0 to 1.0.
        """
        if not retrieved_evidence:
            return (0.0, ["No evidence retrieved."])

        # Extract keywords and concepts from query
        q_words = set(re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", query.lower()))
        combined_text = " ".join(e.get("text", "").lower() for e in retrieved_evidence)

        # Check coverage of required concepts if specified
        missing_gaps: list[str] = []
        if required_concepts:
            for c in required_concepts:
                if c.lower() not in combined_text:
                    missing_gaps.append(f"Missing concept: '{c}'")
        else:
            # Check coverage of query words
            unmatched = [w for w in q_words if w not in combined_text]
            if len(unmatched) > len(q_words) * 0.4:
                missing_gaps.append(f"Incomplete query coverage; missing terms: {unmatched[:3]}")

        coverage = 1.0 - (len(missing_gaps) / (len(required_concepts) if required_concepts else max(1, len(q_words))))
        score = max(0.1, min(1.0, coverage))

        # Check evidence verification status
        verified_count = sum(1 for e in retrieved_evidence if e.get("is_verified") is True)
        if verified_count == 0:
            score *= 0.7  # Penalize unverified evidence

        return (round(score, 3), missing_gaps)

    @staticmethod
    def detect_conflicts(retrieved_evidence: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Detects contradictory claims among retrieved evidence documents.
        """
        conflicts = []
        for i in range(len(retrieved_evidence)):
            for j in range(i + 1, len(retrieved_evidence)):
                e1 = retrieved_evidence[i]
                e2 = retrieved_evidence[j]

                # If one is superseded by the other
                m1 = e1.get("metadata") or {}
                m2 = e2.get("metadata") or {}
                prov1 = m1.get("provenance") or {}
                prov2 = m2.get("provenance") or {}

                if prov1.get("superseded_by") == e2.get("id"):
                    conflicts.append({
                        "type": "superseded",
                        "older_doc_id": e1.get("id"),
                        "newer_doc_id": e2.get("id"),
                        "description": f"Document {e1.get('id')} has been superseded by {e2.get('id')}",
                    })
                elif prov2.get("superseded_by") == e1.get("id"):
                    conflicts.append({
                        "type": "superseded",
                        "older_doc_id": e2.get("id"),
                        "newer_doc_id": e1.get("id"),
                        "description": f"Document {e2.get('id')} has been superseded by {e1.get('id')}",
                    })
                elif m1.get("verification_status") == "disputed" or m2.get("verification_status") == "disputed":
                    conflicts.append({
                        "type": "dispute",
                        "doc1_id": e1.get("id"),
                        "doc2_id": e2.get("id"),
                        "description": "One or more documents carry an active dispute marker",
                    })

        return conflicts

