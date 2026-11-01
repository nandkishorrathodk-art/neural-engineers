from __future__ import annotations

from pydantic import BaseModel, Field


class ComparisonSummary(BaseModel):
    total_compared: int
    agreements: int
    agreement_rate: float = Field(ge=0.0, le=1.0)
    avg_system1_latency_ms: float
    avg_system2_latency_ms: float
    speedup_factor: float
    estimated_tokens_saved: int


class DecisionComparator:
    """
    Side-by-side comparator for System 1 (Laya) vs System 2 (Deep Brain) (Section 11 & 13).
    """

    AVG_TOKENS_PER_SYSTEM2_CALL: int = 450

    @classmethod
    def compare_decisions(
        cls,
        system1_choices: list[str | int],
        system2_choices: list[str | int],
        system1_latencies: list[float],
        system2_latencies: list[float],
    ) -> ComparisonSummary:
        if not system1_choices or not system2_choices:
            return ComparisonSummary(
                total_compared=0,
                agreements=0,
                agreement_rate=0.0,
                avg_system1_latency_ms=0.0,
                avg_system2_latency_ms=0.0,
                speedup_factor=1.0,
                estimated_tokens_saved=0,
            )

        n = min(len(system1_choices), len(system2_choices))
        agreements = sum(1 for i in range(n) if system1_choices[i] == system2_choices[i])
        rate = agreements / n if n > 0 else 0.0

        avg_s1_lat = sum(system1_latencies[:n]) / n if n > 0 else 0.0
        avg_s2_lat = sum(system2_latencies[:n]) / n if n > 0 else 0.0
        speedup = (avg_s2_lat / avg_s1_lat) if avg_s1_lat > 0 else 1.0

        tokens_saved = n * cls.AVG_TOKENS_PER_SYSTEM2_CALL

        return ComparisonSummary(
            total_compared=n,
            agreements=agreements,
            agreement_rate=round(rate, 4),
            avg_system1_latency_ms=round(avg_s1_lat, 2),
            avg_system2_latency_ms=round(avg_s2_lat, 2),
            speedup_factor=round(speedup, 2),
            estimated_tokens_saved=tokens_saved,
        )
