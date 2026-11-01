from __future__ import annotations

import math
from typing import Any


class LatencyTracker:
    """
    Latency metrics tracker for System 1 decisions (<40ms SLA).
    """

    def __init__(self, target_p95_ms: float = 40.0) -> None:
        self.target_p95_ms = target_p95_ms
        self._samples: list[float] = []

    def record(self, latency_ms: float) -> None:
        self._samples.append(float(latency_ms))

    def get_summary(self) -> dict[str, Any]:
        if not self._samples:
            return {
                "count": 0,
                "p50_ms": 0.0,
                "p90_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "mean_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
                "meets_sla": True,
            }

        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)

        def percentile(p: float) -> float:
            k = (n - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_samples[int(k)]
            d0 = sorted_samples[int(f)] * (c - k)
            d1 = sorted_samples[int(c)] * (k - f)
            return d0 + d1

        p50 = percentile(0.50)
        p90 = percentile(0.90)
        p95 = percentile(0.95)
        p99 = percentile(0.99)
        mean_val = sum(sorted_samples) / n

        return {
            "count": n,
            "p50_ms": round(p50, 2),
            "p90_ms": round(p90, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "mean_ms": round(mean_val, 2),
            "min_ms": round(sorted_samples[0], 2),
            "max_ms": round(sorted_samples[-1], 2),
            "meets_sla": p95 <= self.target_p95_ms,
        }

    def reset(self) -> None:
        self._samples.clear()

