from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from neural.constants import SkillLevel


@dataclass
class RubricCriterion:
    name: str
    weight: float
    min_score: float
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GradingRubric:
    criteria: list[RubricCriterion] = field(
        default_factory=lambda: [
            RubricCriterion("functional_correctness", 0.45, 0.75, "Automated test suite assertions passing"),
            RubricCriterion("code_quality", 0.25, 0.70, "AST syntax, structure, modularity, and cleanliness"),
            RubricCriterion("security_compliance", 0.20, 0.80, "Absence of forbidden calls, injection, or credentials"),
            RubricCriterion("performance", 0.10, 0.50, "Execution within bounded latency limits and low resource overhead"),
        ]
    )
    min_pass_score: float = 0.80

    def evaluate(
        self,
        scores: dict[str, float]
    ) -> tuple[bool, float, dict[str, Any]]:
        weighted_sum = 0.0
        total_weight = 0.0
        criterion_results = {}
        criteria_passed = True

        for c in self.criteria:
            score = max(0.0, min(1.0, scores.get(c.name, 0.0)))
            passed = score >= c.min_score
            if not passed:
                criteria_passed = False
            criterion_results[c.name] = {
                "score": round(score, 3),
                "weight": c.weight,
                "min_score": c.min_score,
                "passed": passed,
                "description": c.description,
            }
            weighted_sum += score * c.weight
            total_weight += c.weight

        overall_score = round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0
        passed_overall = criteria_passed and (overall_score >= self.min_pass_score)

        return passed_overall, overall_score, {
            "passed": passed_overall,
            "overall_score": overall_score,
            "min_pass_score": self.min_pass_score,
            "criteria": criterion_results,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_pass_score": self.min_pass_score,
            "criteria": [c.to_dict() for c in self.criteria],
        }


@dataclass
class SkillBenchmarkSuite:
    benchmark_id: str
    skill_name: str
    level: SkillLevel
    title: str
    description: str
    test_cases: list[dict[str, Any]]
    test_runner_template: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "skill_name": self.skill_name,
            "level": self.level.value if hasattr(self.level, "value") else str(self.level),
            "title": self.title,
            "description": self.description,
            "test_cases": self.test_cases,
            "test_runner_template": self.test_runner_template,
        }


@dataclass
class SkillCatalogEntry:
    skill_id: str
    name: str
    category: str
    description: str
    grading_rubric: GradingRubric
    benchmarks: dict[str, SkillBenchmarkSuite] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "grading_rubric": self.grading_rubric.to_dict(),
            "benchmarks": {k: b.to_dict() for k, b in self.benchmarks.items()},
        }


# ============================================================================
# Benchmark Runners and Challenge Generators
# ============================================================================

def _build_python_runner(test_invocations: list[str]) -> str:
    body = "\n    ".join(test_invocations)
    return f"""import json, sys
try:
    from solution import solve
except Exception as e:
    print(json.dumps({{"passed": False, "error": f"Import error: {{e}}", "tests": []}}))
    sys.exit(1)

results = []
def run_tests():
    {body}

try:
    run_tests()
    passed_cnt = sum(1 for r in results if r["passed"])
    total_cnt = len(results)
    all_ok = (passed_cnt == total_cnt) and total_cnt > 0
    print(json.dumps({{"passed": all_ok, "passed_tests": passed_cnt, "total_tests": total_cnt, "tests": results}}))
    sys.exit(0 if all_ok else 1)
except Exception as ex:
    print(json.dumps({{"passed": False, "error": str(ex), "tests": results}}))
    sys.exit(1)
"""


class SkillRegistry:
    """
    Central catalog of verified organizational competencies, grading rubrics,
    and empirical tiered benchmark test suites.
    """

    def __init__(self):
        self._skills: dict[str, SkillCatalogEntry] = {}
        self._bootstrap_standard_catalog()

    def register_skill(self, entry: SkillCatalogEntry) -> None:
        self._skills[entry.name.lower()] = entry
        self._skills[entry.skill_id.lower()] = entry

    def get_skill(self, identifier: str) -> SkillCatalogEntry | None:
        return self._skills.get(identifier.lower())

    def list_skills(self, category: str | None = None) -> list[SkillCatalogEntry]:
        unique = {e.skill_id: e for e in self._skills.values()}
        res = list(unique.values())
        if category:
            res = [s for s in res if s.category.lower() == category.lower()]
        return res

    def get_benchmark(self, skill_name: str, level: SkillLevel | str) -> SkillBenchmarkSuite | None:
        entry = self.get_skill(skill_name)
        if not entry:
            return None
        lvl_key = level.value if hasattr(level, "value") else str(level)
        return entry.benchmarks.get(lvl_key)

    def list_benchmarks(
        self,
        skill_name: str | None = None,
        level: SkillLevel | str | None = None
    ) -> list[SkillBenchmarkSuite]:
        benchmarks = []
        target_skills = [self.get_skill(skill_name)] if skill_name else self.list_skills()
        lvl_str = (level.value if hasattr(level, "value") else str(level)) if level else None

        for s in target_skills:
            if not s:
                continue
            for b_lvl, b in s.benchmarks.items():
                if lvl_str and b_lvl.lower() != lvl_str.lower():
                    continue
                benchmarks.append(b)
        return benchmarks

    def _bootstrap_standard_catalog(self) -> None:
        # 1. Python
        py_rubric = GradingRubric()
        py_benchmarks = {
            SkillLevel.NOVICE.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-PY-NOV-01",
                skill_name="Python",
                level=SkillLevel.NOVICE,
                title="Python Novice: Collections and Transformations",
                description="Evaluate list comprehension, dictionary mapping, and condition filtering",
                test_cases=[
                    {"input": [1, 2, 3, 4, 5, 6], "expected": [4, 16, 36]},
                    {"input": [10, 15, 20], "expected": [100, 400]},
                ],
                test_runner_template=_build_python_runner([
                    "res1 = solve([1, 2, 3, 4, 5, 6])\n    results.append({'id': 'c1', 'passed': res1 == [4, 16, 36], 'expected': [4, 16, 36], 'actual': res1})",
                    "res2 = solve([10, 15, 20])\n    results.append({'id': 'c2', 'passed': res2 == [100, 400], 'expected': [100, 400], 'actual': res2})",
                ]),
            ),
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-PY-COMP-01",
                skill_name="Python",
                level=SkillLevel.COMPETENT,
                title="Python Competent: Aggregation & Error Resilience",
                description="Handle structured records, grouping, aggregation, and null values safely",
                test_cases=[
                    {"input": [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}, {"cat": "A", "val": 30}], "expected": {"A": 40, "B": 20}},
                    {"input": [], "expected": {}},
                ],
                test_runner_template=_build_python_runner([
                    "res1 = solve([{'cat': 'A', 'val': 10}, {'cat': 'B', 'val': 20}, {'cat': 'A', 'val': 30}])\n    results.append({'id': 'c1', 'passed': res1 == {'A': 40, 'B': 20}, 'actual': res1})",
                    "res2 = solve([])\n    results.append({'id': 'c2', 'passed': res2 == {}, 'actual': res2})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-PY-EXP-01",
                skill_name="Python",
                level=SkillLevel.EXPERT,
                title="Python Expert: Concurrency, Decorators & Generator Pipelines",
                description="Process streams with custom generator pipeline, memoization decorator, and type checks",
                test_cases=[
                    {"input": "stream_batch_eval", "expected": True},
                ],
                test_runner_template=_build_python_runner([
                    "res = solve('stream_test')\n    results.append({'id': 'c1', 'passed': res.get('status') == 'success' and res.get('processed', 0) > 0, 'actual': res})",
                ]),
            ),
            SkillLevel.MASTER.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-PY-MST-01",
                skill_name="Python",
                level=SkillLevel.MASTER,
                title="Python Master: High-Throughput Async State Engine",
                description="Architect an async event coordinator with backpressure, cancellation, and invariant guarantees",
                test_cases=[
                    {"input": "async_concurrency_stress", "expected": True},
                ],
                test_runner_template=_build_python_runner([
                    "res = solve('master_stress')\n    results.append({'id': 'c1', 'passed': res.get('invariants_held') is True and res.get('exit_code') == 0, 'actual': res})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-PY-001",
            name="Python",
            category="Backend",
            description="Core Python programming, OOP, functional patterns, async, and systems architecture.",
            grading_rubric=py_rubric,
            benchmarks=py_benchmarks,
        ))

        # 2. FastAPI
        fastapi_benchmarks = {
            SkillLevel.NOVICE.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-FASTAPI-NOV-01",
                skill_name="FastAPI",
                level=SkillLevel.NOVICE,
                title="FastAPI Novice: Basic Routes and Responses",
                description="Create route schema declaration with status codes and Pydantic validation",
                test_cases=[{"input": "health", "expected": 200}],
                test_runner_template=_build_python_runner([
                    "res = solve({'endpoint': 'health'})\n    results.append({'id': 'c1', 'passed': res.get('status_code') == 200, 'actual': res})",
                ]),
            ),
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-FASTAPI-COMP-01",
                skill_name="FastAPI",
                level=SkillLevel.COMPETENT,
                title="FastAPI Competent: CRUD, Dependency Injection & Status Codes",
                description="Declare create/read routes with Depends, 201 Created and 404 Not Found handling",
                test_cases=[{"input": "create_user", "expected": 201}],
                test_runner_template=_build_python_runner([
                    "r1 = solve({'action': 'create', 'data': {'name': 'alice'}})\n    results.append({'id': 'c1', 'passed': r1.get('status_code') == 201, 'actual': r1})",
                    "r2 = solve({'action': 'get', 'id': 'missing'})\n    results.append({'id': 'c2', 'passed': r2.get('status_code') == 404, 'actual': r2})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-FASTAPI-EXP-01",
                skill_name="FastAPI",
                level=SkillLevel.EXPERT,
                title="FastAPI Expert: Middleware, Auth & Background Tasks",
                description="Implement rate-limiting middleware and JWT bearer token authorization",
                test_cases=[{"input": "auth_flow", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'action': 'authenticate', 'token': 'valid_token'})\n    results.append({'id': 'c1', 'passed': r.get('authenticated') is True, 'actual': r})",
                ]),
            ),
            SkillLevel.MASTER.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-FASTAPI-MST-01",
                skill_name="FastAPI",
                level=SkillLevel.MASTER,
                title="FastAPI Master: High-Concurrency Streaming & Lifespan Architecture",
                description="Design zero-copy streaming responses with lifespan context and graceful shutdown",
                test_cases=[{"input": "streaming_lifespan", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'action': 'stream_events'})\n    results.append({'id': 'c1', 'passed': r.get('stream_active') is True, 'actual': r})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-FASTAPI-001",
            name="FastAPI",
            category="Backend",
            description="Modern high-performance asynchronous REST API design with FastAPI and Pydantic.",
            grading_rubric=GradingRubric(),
            benchmarks=fastapi_benchmarks,
        ))

        # 3. pytest
        pytest_benchmarks = {
            SkillLevel.NOVICE.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-QA-NOV-01",
                skill_name="pytest",
                level=SkillLevel.NOVICE,
                title="pytest Novice: Assertions and Basic Test Cases",
                description="Validate test exit codes and truth assertions",
                test_cases=[{"input": {"exit_code": 0}, "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'exit_code': 0, 'failures': 0})\n    results.append({'id': 'c1', 'passed': r is True, 'actual': r})",
                ]),
            ),
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-QA-COMP-01",
                skill_name="pytest",
                level=SkillLevel.COMPETENT,
                title="pytest Competent: Fixtures, Parameterization & Mocking",
                description="Parametric test generation and fixture dependency injection",
                test_cases=[{"input": "fixture_test", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'test_type': 'parameterized'})\n    results.append({'id': 'c1', 'passed': r.get('executed_cases', 0) >= 3, 'actual': r})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-QA-EXP-01",
                skill_name="pytest",
                level=SkillLevel.EXPERT,
                title="pytest Expert: Adversarial Fault Injection & Mutation Testing",
                description="Detect subtle regressions and injected faults with rigorous boundary conditions",
                test_cases=[{"input": "fault_injection", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'fault_injected': True})\n    results.append({'id': 'c1', 'passed': r.get('detected_fault') is True, 'actual': r})",
                ]),
            ),
            SkillLevel.MASTER.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-QA-MST-01",
                skill_name="pytest",
                level=SkillLevel.MASTER,
                title="pytest Master: Chaos Engineering & Distributed Invariant Verification",
                description="Execute concurrent race condition stress tests and verify ACID guarantees",
                test_cases=[{"input": "chaos_suite", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'mode': 'chaos_verification'})\n    results.append({'id': 'c1', 'passed': r.get('flakiness_pct', 100) == 0, 'actual': r})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-QA-001",
            name="pytest",
            category="QA",
            description="Automated software testing, test fixtures, mocking, and adversarial verification.",
            grading_rubric=GradingRubric(),
            benchmarks=pytest_benchmarks,
        ))

        # 4. Security Auditing
        sec_rubric = GradingRubric(
            criteria=[
                RubricCriterion("functional_correctness", 0.40, 0.80, "Vulnerability detection accuracy"),
                RubricCriterion("code_quality", 0.20, 0.70, "Clean reporting syntax"),
                RubricCriterion("security_compliance", 0.30, 0.90, "Zero false negatives on dangerous patterns"),
                RubricCriterion("performance", 0.10, 0.50, "Fast static scan duration"),
            ],
            min_pass_score=0.85,
        )
        sec_benchmarks = {
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-SEC-COMP-01",
                skill_name="Security Auditing",
                level=SkillLevel.COMPETENT,
                title="Security Auditing Competent: SQLi & Hardcoded Secret Detection",
                description="Identify unsafe SQL string interpolations and hardcoded passwords",
                test_cases=[{"input": "SELECT * FROM users WHERE id = '" + "{val}" + "'", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r1 = solve(\"SELECT * FROM users WHERE id = '\" + \"{val}\" + \"'\")\n    results.append({'id': 'c1', 'passed': r1.get('vulnerable') is True, 'actual': r1})",
                    "r2 = solve(\"SELECT * FROM users WHERE id = :user_id\")\n    results.append({'id': 'c2', 'passed': r2.get('vulnerable') is False, 'actual': r2})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-SEC-EXP-01",
                skill_name="Security Auditing",
                level=SkillLevel.EXPERT,
                title="Security Auditing Expert: RCE, SSRF & Insecure Deserialization",
                description="Flag dangerous eval/exec calls, pickling unverified payloads, and SSRF URLs",
                test_cases=[{"input": "eval(user_input)", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve(\"eval(user_data)\")\n    results.append({'id': 'c1', 'passed': r.get('risk_level') == 'CRITICAL', 'actual': r})",
                ]),
            ),
            SkillLevel.MASTER.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-SEC-MST-01",
                skill_name="Security Auditing",
                level=SkillLevel.MASTER,
                title="Security Auditing Master: Threat Modeling & Cryptographic Ledger Audit",
                description="Audit cryptographic hash chains, timing attack vectors, and zero-trust sandbox boundaries",
                test_cases=[{"input": "crypto_chain_audit", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'audit_target': 'ledger_chain'})\n    results.append({'id': 'c1', 'passed': r.get('tamper_evident') is True, 'actual': r})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-SEC-001",
            name="Security Auditing",
            category="Security",
            description="Static security analysis, vulnerability detection, penetration testing, and cryptographic verification.",
            grading_rubric=sec_rubric,
            benchmarks=sec_benchmarks,
        ))

        # 5. Docker
        docker_benchmarks = {
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-DOCKER-COMP-01",
                skill_name="Docker",
                level=SkillLevel.COMPETENT,
                title="Docker Competent: Non-root Containerization & Resource Limits",
                description="Validate Dockerfile spec with non-root user (uid 1000) and port allocations",
                test_cases=[{"input": "docker_spec", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'user': '1000:1000', 'ports': [5901, 6081]})\n    results.append({'id': 'c1', 'passed': r.get('is_valid') is True and r.get('non_root') is True, 'actual': r})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-DOCKER-EXP-01",
                skill_name="Docker",
                level=SkillLevel.EXPERT,
                title="Docker Expert: Multi-Stage Optimization & Healthchecks",
                description="Multi-stage build minimizing image footprint with automated health probes",
                test_cases=[{"input": "multi_stage_spec", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'multi_stage': True, 'healthcheck': True})\n    results.append({'id': 'c1', 'passed': r.get('optimized') is True, 'actual': r})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-DOCKER-001",
            name="Docker",
            category="DevOps",
            description="Containerization, workstation image construction, resource cgroups, and network isolation.",
            grading_rubric=GradingRubric(),
            benchmarks=docker_benchmarks,
        ))

        # 6. System Design
        sys_benchmarks = {
            SkillLevel.COMPETENT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-SYS-COMP-01",
                skill_name="System Design",
                level=SkillLevel.COMPETENT,
                title="System Design Competent: Service Decomposition & DB Scaling",
                description="Decompose monolithic requirements into decoupled services with explicit schemas",
                test_cases=[{"input": "decompose_monolith", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'architecture': 'microservices'})\n    results.append({'id': 'c1', 'passed': r.get('services_count', 0) >= 2, 'actual': r})",
                ]),
            ),
            SkillLevel.EXPERT.value: SkillBenchmarkSuite(
                benchmark_id="BENCH-SYS-EXP-01",
                skill_name="System Design",
                level=SkillLevel.EXPERT,
                title="System Design Expert: Distributed Consensus & Partition Tolerance",
                description="Design CAP-theorem resilient state machine with idempotent event bus",
                test_cases=[{"input": "distributed_consensus", "expected": True}],
                test_runner_template=_build_python_runner([
                    "r = solve({'partition_tolerant': True})\n    results.append({'id': 'c1', 'passed': r.get('split_brain_prevented') is True, 'actual': r})",
                ]),
            ),
        }
        self.register_skill(SkillCatalogEntry(
            skill_id="SKILL-SYS-001",
            name="System Design",
            category="Architecture",
            description="Distributed systems, microservices, consensus protocols, and enterprise reliability architecture.",
            grading_rubric=GradingRubric(),
            benchmarks=sys_benchmarks,
        ))


_global_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
    return _global_registry
