from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkScenario:
    scenario_id: str
    name: str
    description: str
    input_context: dict[str, Any]
    expected_output_keys: list[str] = field(default_factory=list)
    difficulty: str = "medium"  # low, medium, high
    complexity_weight: float = 1.0


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    latency_ms: float
    token_count: int
    output_summary: str
    reproduced: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "output_summary": self.output_summary,
            "reproduced": self.reproduced,
        }


@dataclass
class MethodBenchmarkScorecard:
    benchmark_id: str
    method_id: str
    baseline_method_id: str | None
    total_scenarios: int
    passed_scenarios: int
    accuracy: float
    avg_latency_ms: float
    token_efficiency: float
    reproduction_rate: float
    composite_score: float
    baseline_accuracy: float | None
    baseline_latency_ms: float | None
    baseline_token_efficiency: float | None
    relative_improvement_pct: float
    recommendation: str  # "promote", "iterate", "reject"
    scenario_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "method_id": self.method_id,
            "baseline_method_id": self.baseline_method_id,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "accuracy": self.accuracy,
            "avg_latency_ms": self.avg_latency_ms,
            "token_efficiency": self.token_efficiency,
            "reproduction_rate": self.reproduction_rate,
            "composite_score": self.composite_score,
            "baseline_accuracy": self.baseline_accuracy,
            "baseline_latency_ms": self.baseline_latency_ms,
            "baseline_token_efficiency": self.baseline_token_efficiency,
            "relative_improvement_pct": self.relative_improvement_pct,
            "recommendation": self.recommendation,
            "scenario_results": self.scenario_results,
        }


class BenchmarkUnavailableError(RuntimeError):
    """Raised until benchmark scenarios are executed by a real method runner."""


def get_default_method_benchmark_scenarios() -> list[BenchmarkScenario]:
    return [
        BenchmarkScenario(
            scenario_id="SCENARIO-API-01",
            name="Asynchronous Database Session Management",
            description="Synthesize reliable async context session handlers under high concurrency.",
            input_context={"concurrency": 50, "framework": "FastAPI", "db": "PostgreSQL"},
            expected_output_keys=["session_context", "rollback_on_error", "connection_pool"],
            difficulty="medium",
            complexity_weight=1.0,
        ),
        BenchmarkScenario(
            scenario_id="SCENARIO-DEBUG-02",
            name="Intermittent Lock Contention Diagnostic",
            description="Isolate root cause for SQLite lock starvation during parallel DAG execution.",
            input_context={"error_code": "sqlite3.OperationalError: database is locked", "threads": 4},
            expected_output_keys=["root_cause", "mitigation", "retry_backoff"],
            difficulty="high",
            complexity_weight=1.5,
        ),
        BenchmarkScenario(
            scenario_id="SCENARIO-TOOL-03",
            name="AST Policy Boundary Compliance",
            description="Generate utility function without importing dangerous or restricted builtins.",
            input_context={"target_function": "parse_user_query", "restricted": ["os.system", "eval"]},
            expected_output_keys=["safe_ast", "no_eval", "strict_typing"],
            difficulty="medium",
            complexity_weight=1.0,
        ),
        BenchmarkScenario(
            scenario_id="SCENARIO-RECURSION-04",
            name="Self-Healing Code Synthesis Under Test Failure",
            description="Iterative test failure feedback driving patch synthesis until tests pass.",
            input_context={"initial_exit_code": 1, "test_failure": "AssertionError: expected 42"},
            expected_output_keys=["patch_applied", "final_receipt", "zero_regressions"],
            difficulty="high",
            complexity_weight=1.5,
        ),
    ]


class MethodBenchmarkHarness:
    """
    Benchmark contract for comparing problem-solving methods.
    It fails closed until an executor-backed scenario runner is configured.
    """

    def __init__(self, scenarios: list[BenchmarkScenario] | None = None) -> None:
        self.scenarios = scenarios if scenarios is not None else get_default_method_benchmark_scenarios()

    async def evaluate_method(
        self,
        method_id: str,
        steps: list[dict[str, Any]],
        strategy_type: str,
        baseline_metrics: dict[str, Any] | None = None,
        baseline_method_id: str | None = None,
    ) -> MethodBenchmarkScorecard:
        raise BenchmarkUnavailableError(
            "No method execution adapter is configured. Structural heuristics cannot produce benchmark scores or promotion recommendations."
        )
