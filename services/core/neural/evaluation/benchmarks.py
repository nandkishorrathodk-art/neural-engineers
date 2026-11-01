import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from neural.constants import SkillLevel
from neural.logger import get_logger

logger = get_logger("ancos.evaluation.benchmarks")


@dataclass
class BenchmarkTestCase:
    """A test case within an empirical skill benchmark."""
    id: str
    name: str
    description: str
    input_data: Any
    expected_output: Any
    comparator: str = "equals"  # equals, contains, custom
    custom_validator: Optional[Callable[[Any, Any], bool]] = None

    def evaluate(self, actual: Any) -> tuple[bool, str]:
        try:
            if self.custom_validator:
                passed = bool(self.custom_validator(self.input_data, actual))
                msg = "Custom validation passed" if passed else "Custom validation failed"
                return passed, msg

            if self.comparator == "equals":
                passed = actual == self.expected_output
                msg = f"Expected {self.expected_output!r}, got {actual!r}"
                return passed, msg

            if self.comparator == "contains":
                if isinstance(actual, (str, list, tuple, set, dict)):
                    passed = self.expected_output in actual
                    msg = f"Target {self.expected_output!r} in output" if passed else f"Target {self.expected_output!r} missing from output"
                    return passed, msg
                return False, f"Output {actual!r} is not a collection"

            return False, f"Unknown comparator {self.comparator}"
        except Exception as e:
            return False, f"Evaluation error: {str(e)}"


@dataclass
class BenchmarkResult:
    """Result of running an empirical skill benchmark."""
    benchmark_id: str
    skill_name: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    pass_rate: float
    passed: bool
    duration_seconds: float
    quality_score: float
    details: list[dict[str, Any]] = field(default_factory=list)


class SkillBenchmark:
    """Base class for empirical employee skill benchmarks."""

    def __init__(
        self,
        benchmark_id: str,
        name: str,
        skill_name: str,
        target_level: SkillLevel = SkillLevel.INTERMEDIATE,
        test_cases: Optional[list[BenchmarkTestCase]] = None,
        min_pass_rate: float = 1.0
    ):
        self.benchmark_id = benchmark_id
        self.name = name
        self.skill_name = skill_name
        self.target_level = target_level
        self.test_cases = test_cases or []
        self.min_pass_rate = min_pass_rate

    def add_case(self, case: BenchmarkTestCase) -> None:
        self.test_cases.append(case)

    def run(self, candidate: Union[Callable, dict[str, Any], str]) -> BenchmarkResult:
        """
        Executes benchmark test cases against candidate callable or output.
        """
        t_start = time.perf_counter()
        passed_count = 0
        details = []

        for case in self.test_cases:
            case_start = time.perf_counter()
            try:
                if callable(candidate):
                    if inspect.iscoroutinefunction(candidate):
                        import asyncio
                        # synchronous wrapper if needed
                        actual = asyncio.run(candidate(case.input_data))
                    else:
                        actual = candidate(case.input_data)
                elif isinstance(candidate, dict):
                    actual = candidate.get(case.id, candidate.get("output"))
                else:
                    actual = str(candidate)

                case_passed, msg = case.evaluate(actual)
            except Exception as e:
                case_passed = False
                msg = f"Execution exception: {str(e)}"

            if case_passed:
                passed_count += 1

            details.append({
                "case_id": case.id,
                "name": case.name,
                "passed": case_passed,
                "duration_ms": round((time.perf_counter() - case_start) * 1000, 2),
                "message": msg
            })

        total = len(self.test_cases)
        pass_rate = (passed_count / total) if total > 0 else 1.0
        passed = pass_rate >= self.min_pass_rate
        duration = time.perf_counter() - t_start
        quality_score = round(pass_rate * 1.0, 4)

        return BenchmarkResult(
            benchmark_id=self.benchmark_id,
            skill_name=self.skill_name,
            total_tests=total,
            passed_tests=passed_count,
            failed_tests=total - passed_count,
            pass_rate=pass_rate,
            passed=passed,
            duration_seconds=round(duration, 4),
            quality_score=quality_score,
            details=details
        )


# ============================================================================
# Standard Built-in Skill Benchmarks
# ============================================================================

def create_python_backend_benchmark() -> SkillBenchmark:
    bench = SkillBenchmark(
        benchmark_id="BENCH-SKILL-PY-01",
        name="Python Backend Core Engineering",
        skill_name="Python",
        target_level=SkillLevel.ADVANCED,
        min_pass_rate=1.0
    )
    # Case 1: Data transformations
    bench.add_case(BenchmarkTestCase(
        id="case-transform",
        name="List transformation and filtering",
        description="Filters even numbers and squares them",
        input_data=[1, 2, 3, 4, 5, 6],
        expected_output=[4, 16, 36],
        comparator="equals"
    ))
    # Case 2: Dict aggregation
    bench.add_case(BenchmarkTestCase(
        id="case-aggregate",
        name="Key-value aggregation",
        description="Aggregates values by category",
        input_data=[{"cat": "a", "val": 10}, {"cat": "b", "val": 20}, {"cat": "a", "val": 30}],
        expected_output={"a": 40, "b": 20},
        comparator="equals"
    ))
    return bench


def create_fastapi_benchmark() -> SkillBenchmark:
    bench = SkillBenchmark(
        benchmark_id="BENCH-SKILL-FASTAPI-01",
        name="FastAPI Service Architecture",
        skill_name="FastAPI",
        target_level=SkillLevel.ADVANCED,
        min_pass_rate=1.0
    )
    bench.add_case(BenchmarkTestCase(
        id="case-route-spec",
        name="REST Route and Schema Declaration",
        description="Produces valid route schema with status 200/201 and response model",
        input_data={"resource": "users", "action": "create"},
        expected_output="201",
        comparator="contains"
    ))
    bench.add_case(BenchmarkTestCase(
        id="case-error-handling",
        name="HTTP Exception handling",
        description="Returns 404 on missing resource",
        input_data={"resource": "users", "action": "get_missing"},
        expected_output="404",
        comparator="contains"
    ))
    return bench


def create_qa_testing_benchmark() -> SkillBenchmark:
    bench = SkillBenchmark(
        benchmark_id="BENCH-SKILL-QA-01",
        name="QA & Adversarial Verification",
        skill_name="pytest",
        target_level=SkillLevel.ADVANCED,
        min_pass_rate=1.0
    )
    bench.add_case(BenchmarkTestCase(
        id="case-exit-zero",
        name="Green Test Suite Verification",
        description="Evaluates test suite exit code and zero failures",
        input_data={"exit_code": 0, "failures": 0},
        expected_output=True,
        comparator="custom",
        custom_validator=lambda inp, act: inp.get("exit_code") == 0 and inp.get("failures") == 0 and act is True
    ))
    bench.add_case(BenchmarkTestCase(
        id="case-detect-failure",
        name="Adversarial Fault Detection",
        description="Detects injected bug and flags exit code > 0",
        input_data={"exit_code": 1, "failures": 1},
        expected_output=False,
        comparator="custom",
        custom_validator=lambda inp, act: inp.get("exit_code") != 0 and act is False
    ))
    return bench


def create_security_audit_benchmark() -> SkillBenchmark:
    bench = SkillBenchmark(
        benchmark_id="BENCH-SKILL-SEC-01",
        name="Security Auditing & Code Vulnerability Analysis",
        skill_name="Security Auditing",
        target_level=SkillLevel.EXPERT,
        min_pass_rate=1.0
    )
    bench.add_case(BenchmarkTestCase(
        id="case-sqli-detection",
        name="SQL Injection Detection",
        description="Flags unsafe f-string in SQL query",
        input_data="SELECT * FROM users WHERE id = '" + "{user_input}" + "'",
        expected_output=True,
        comparator="custom",
        custom_validator=lambda inp, act: "user_input" in inp and act is True
    ))
    bench.add_case(BenchmarkTestCase(
        id="case-safe-parameterized",
        name="Safe Parameterized Query",
        description="Approves parameterized query with placeholders",
        input_data="SELECT * FROM users WHERE id = :user_id",
        expected_output=False,
        comparator="custom",
        custom_validator=lambda inp, act: ":user_id" in inp and act is False
    ))
    return bench


class BenchmarkRunner:
    """
    Runner for empirical benchmarks, regression tracking, and A/B evaluations.
    """

    def __init__(self):
        self._benchmarks: dict[str, SkillBenchmark] = {}
        # Pre-register built-in standard benchmarks
        for factory in [
            create_python_backend_benchmark,
            create_fastapi_benchmark,
            create_qa_testing_benchmark,
            create_security_audit_benchmark
        ]:
            b = factory()
            self.register_benchmark(b)

    def register_benchmark(self, benchmark: SkillBenchmark) -> None:
        self._benchmarks[benchmark.benchmark_id] = benchmark
        self._benchmarks[benchmark.skill_name.lower()] = benchmark

    def get_benchmark(self, identifier: str) -> Optional[SkillBenchmark]:
        return self._benchmarks.get(identifier) or self._benchmarks.get(identifier.lower())

    def run_benchmark(self, identifier: str, candidate: Any) -> BenchmarkResult:
        bench = self.get_benchmark(identifier)
        if not bench:
            raise ValueError(f"Benchmark '{identifier}' not found in registry")
        return bench.run(candidate)

    def run_all_for_skills(self, skills: list[str], candidate: Any) -> list[BenchmarkResult]:
        results = []
        for skill in skills:
            bench = self.get_benchmark(skill)
            if bench:
                res = bench.run(candidate)
                results.append(res)
        return results

    def compare_a_b(
        self,
        result_a: BenchmarkResult,
        result_b: BenchmarkResult
    ) -> dict[str, Any]:
        """
        Compares baseline (A) vs candidate (B) to detect regressions or improvements.
        """
        pass_rate_diff = round(result_b.pass_rate - result_a.pass_rate, 4)
        duration_diff = round(result_b.duration_seconds - result_a.duration_seconds, 4)
        is_regression = result_b.pass_rate < result_a.pass_rate

        return {
            "benchmark_id": result_a.benchmark_id,
            "skill_name": result_a.skill_name,
            "baseline_pass_rate": result_a.pass_rate,
            "candidate_pass_rate": result_b.pass_rate,
            "pass_rate_delta": pass_rate_diff,
            "duration_delta_seconds": duration_diff,
            "is_regression": is_regression,
            "improvement": pass_rate_diff > 0,
            "verdict": "regression" if is_regression else ("improved" if pass_rate_diff > 0 else "neutral")
        }
