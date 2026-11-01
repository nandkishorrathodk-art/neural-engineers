from __future__ import annotations

import re
from typing import Optional

from neural.decision_intelligence.schemas.choice import ChoiceCandidate
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.routing.candidate_selector")


class CandidateSelector:
    """
    Candidate Selector & Bounded Reduction Engine (Section 9).
    Enforces strict candidate cardinality bounds: 2 <= K <= 12.
    For high-cardinality collections, performs coarse-to-fine hierarchical filtering.
    """

    MAX_CANDIDATES: int = 12
    MIN_CANDIDATES: int = 2

    def prepare_candidates(
        self,
        candidates: list[ChoiceCandidate],
        context: Optional[str] = None,
    ) -> list[ChoiceCandidate]:
        if len(candidates) < self.MIN_CANDIDATES:
            raise ValueError(f"Need at least {self.MIN_CANDIDATES} candidates for selection, got {len(candidates)}")

        # Deduplicate candidates by ID while preserving order
        seen_ids: set[str] = set()
        deduped: list[ChoiceCandidate] = []
        for c in candidates:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                deduped.append(c)

        if len(deduped) < self.MIN_CANDIDATES:
            raise ValueError(
                f"Need at least {self.MIN_CANDIDATES} unique candidates for selection, got {len(deduped)}"
            )

        if len(deduped) <= self.MAX_CANDIDATES:
            return deduped

        # Coarse-to-fine filtering down to MAX_CANDIDATES
        logger.info(
            "Applying hierarchical candidate reduction",
            original_count=len(deduped),
            target_count=self.MAX_CANDIDATES,
        )

        if not context:
            return deduped[: self.MAX_CANDIDATES]

        # Score candidates by lexical relevance to context
        context_tokens = set(re.findall(r"\w+", context.lower()))
        scored_candidates: list[tuple[float, ChoiceCandidate]] = []

        for idx, c in enumerate(deduped):
            cand_tokens = set(re.findall(r"\w+", c.label.lower()))
            overlap = len(context_tokens.intersection(cand_tokens))
            score = float(overlap) * 2.0
            if c.score is not None:
                score += c.score
            # Maintain stable secondary sort by initial index
            scored_candidates.append((score - (idx * 0.001), c))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored_candidates[: self.MAX_CANDIDATES]]

