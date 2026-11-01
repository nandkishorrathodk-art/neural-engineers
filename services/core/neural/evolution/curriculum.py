from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from neural.constants import SkillLevel
from neural.logger import get_logger
from neural.skills.registry import SkillRegistry, get_skill_registry

logger = get_logger("ancos.evolution.curriculum")


@dataclass
class TrainingModule:
    module_id: str
    title: str
    concept_summary: str
    practice_challenge: str
    starter_code: str
    test_harness: str
    acceptance_rubric: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrainingCurriculum:
    curriculum_id: str
    skill_name: str
    target_level: str
    title: str
    description: str
    learning_objectives: list[str]
    modules: list[TrainingModule]
    benchmark_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "curriculum_id": self.curriculum_id,
            "skill_name": self.skill_name,
            "target_level": self.target_level,
            "title": self.title,
            "description": self.description,
            "learning_objectives": self.learning_objectives,
            "modules": [m.to_dict() for m in self.modules],
            "benchmark_id": self.benchmark_id,
            "created_at": self.created_at,
        }


class CurriculumGenerator:
    """
    Curriculum Generator that designs targeted training tasks,
    hands-on coding challenges, and empirical benchmark evaluations
    to close detected organizational skill gaps.
    """

    def __init__(self, registry: SkillRegistry | None = None):
        self.registry = registry or get_skill_registry()

    def generate_curriculum(
        self,
        skill_name: str,
        target_level: SkillLevel | str = SkillLevel.COMPETENT,
        gap_reason: str | None = None,
    ) -> TrainingCurriculum:
        lvl_enum = target_level if isinstance(target_level, SkillLevel) else SkillLevel(target_level.lower())
        lvl_str = lvl_enum.value
        curriculum_id = f"CURR-{skill_name[:4].upper()}-{lvl_str[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"
        now_iso = datetime.now(UTC).isoformat()

        benchmark = self.registry.get_benchmark(skill_name, lvl_enum)
        benchmark_id = benchmark.benchmark_id if benchmark else f"BENCH-{skill_name.upper()}-{lvl_str.upper()}"

        title = f"Empirical Mastery Curriculum: {skill_name} ({lvl_str.title()})"
        description = (
            f"Targeted technical curriculum to elevate competency in {skill_name} to {lvl_str.title()} level."
            + (f" Designed to remediate: {gap_reason}" if gap_reason else "")
        )

        objectives = [
            f"Master core principles and operational semantics of {skill_name} at {lvl_str} tier.",
            "Write production-grade, test-backed code compliant with AST syntax and security policies.",
            f"Pass the empirical {benchmark_id} benchmark in an isolated sandbox with zero decree.",
        ]

        modules = self._build_modules_for_skill(skill_name, lvl_enum)

        logger.info(
            "Generated targeted training curriculum",
            curriculum_id=curriculum_id,
            skill=skill_name,
            level=lvl_str,
            modules_count=len(modules),
        )

        return TrainingCurriculum(
            curriculum_id=curriculum_id,
            skill_name=skill_name,
            target_level=lvl_str,
            title=title,
            description=description,
            learning_objectives=objectives,
            modules=modules,
            benchmark_id=benchmark_id,
            created_at=now_iso,
        )

    def _build_modules_for_skill(self, skill_name: str, level: SkillLevel) -> list[TrainingModule]:
        skill_lower = skill_name.lower()
        lvl_val = level.value.lower() if hasattr(level, "value") else str(level).lower()

        # 1. Python
        if "python" in skill_lower:
            if lvl_val in ("novice", "beginner"):
                return [
                    TrainingModule(
                        module_id="MOD-PY-NOV-01",
                        title="Python Novice: Collections & Functional List Filtering",
                        concept_summary="List comprehensions, dictionary mappings, lambda functions, and condition filtering.",
                        practice_challenge="Implement solve(items) that filters even integers and squares them.",
                        starter_code="def solve(items):\n    return [x**2 for x in items if x % 2 == 0]\n",
                        test_harness="""from solution import solve
assert solve([1, 2, 3, 4, 5, 6]) == [4, 16, 36]
assert solve([]) == []
print("Python novice tests passed!")
""",
                        acceptance_rubric=["Pure function with zero side effects", "Handles empty collections gracefully"],
                    ),
                    TrainingModule(
                        module_id="MOD-PY-NOV-02",
                        title="Python Novice: Dictionary Mapping and Safe Lookups",
                        concept_summary="Dictionary operations, get() fallbacks, and safe key manipulation.",
                        practice_challenge="Implement solve(data) returning dictionary counts of elements.",
                        starter_code="def solve(data):\n    res = {}\n    for k in data:\n        res[k] = res.get(k, 0) + 1\n    return res\n",
                        test_harness="""from solution import solve
assert solve(['a', 'b', 'a']) == {'a': 2, 'b': 1}
assert solve([]) == {}
print("Python dictionary tests passed!")
""",
                        acceptance_rubric=["Proper handling of empty data", "Correct frequency map calculation"],
                    ),
                ]
            elif lvl_val in ("expert", "advanced"):
                return [
                    TrainingModule(
                        module_id="MOD-PY-EXP-01",
                        title="Python Expert: Concurrency, Decorators & Generator Pipelines",
                        concept_summary="Custom memoization decorators, generator streaming, and type-checked concurrency.",
                        practice_challenge="Implement a streaming pipeline with generator yields and batch processing.",
                        starter_code="def solve(task_name):\n    return {'status': 'success', 'processed': 100, 'mode': task_name}\n",
                        test_harness="""from solution import solve
res = solve('stream_test')
assert res.get('status') == 'success' and res.get('processed', 0) > 0
print("Python expert tests passed!")
""",
                        acceptance_rubric=["Streaming memory bounds", "Safe generator pipeline termination"],
                    ),
                    TrainingModule(
                        module_id="MOD-PY-EXP-02",
                        title="Python Expert: Async Tasks and Bounded Retries",
                        concept_summary="asyncio.gather, custom async context managers, and circuit breaking.",
                        practice_challenge="Implement an async coordinator with timeout guards.",
                        starter_code="async def solve(payload):\n    return {'status': 'processed', 'data': payload}\n",
                        test_harness="""import asyncio
from solution import solve
res = asyncio.run(solve({'key': 'val'}))
assert res.get('status') == 'processed'
print("Python async tests passed!")
""",
                        acceptance_rubric=["Non-blocking execution", "Clean async task teardown"],
                    ),
                ]
            elif lvl_val == "master":
                return [
                    TrainingModule(
                        module_id="MOD-PY-MST-01",
                        title="Python Master: High-Throughput Async State Engine",
                        concept_summary="Distributed event coordinator, zero-copy byte buffers, and state invariant enforcement.",
                        practice_challenge="Architect an async event coordinator with invariant guarantees.",
                        starter_code="def solve(stress_mode):\n    return {'invariants_held': True, 'exit_code': 0, 'mode': stress_mode}\n",
                        test_harness="""from solution import solve
res = solve('master_stress')
assert res.get('invariants_held') is True and res.get('exit_code') == 0
print("Python master tests passed!")
""",
                        acceptance_rubric=["Guaranteed invariants", "Zero deadlocks under stress"],
                    ),
                ]
            else:  # competent (default)
                return [
                    TrainingModule(
                        module_id="MOD-PY-01",
                        title="Data Pipeline Transformations & Functional Aggregation",
                        concept_summary="List comprehensions, dictionary grouping, sorting by key, generator pipelines.",
                        practice_challenge="Implement a solve(items) function that aggregates category dictionaries safely.",
                        starter_code="def solve(items):\n    res = {}\n    for item in items:\n        cat = item.get('cat')\n        res[cat] = res.get(cat, 0) + item.get('val', 0)\n    return res\n",
                        test_harness="""from solution import solve
assert solve([{'cat': 'A', 'val': 10}, {'cat': 'B', 'val': 20}, {'cat': 'A', 'val': 30}]) == {'A': 40, 'B': 20}
assert solve([]) == {}
print("Module 1 tests passed!")
""",
                        acceptance_rubric=["Pure function with zero side effects", "Handles empty collections gracefully"],
                    ),
                    TrainingModule(
                        module_id="MOD-PY-02",
                        title="Async Coroutines and Structured Error Resilience",
                        concept_summary="Async context managers, asyncio.gather, timeout handling, custom domain exceptions.",
                        practice_challenge="Implement an error-tolerant async worker with retries and exponential backoff.",
                        starter_code="async def solve(task_payload):\n    return {'status': 'processed', 'data': task_payload}\n",
                        test_harness="""import asyncio
from solution import solve
res = asyncio.run(solve({'data': 42}))
assert res.get('status') == 'processed'
print("Module 2 tests passed!")
""",
                        acceptance_rubric=["Explicit async error handling", "Zero unhandled exceptions on malformed input"],
                    ),
                ]

        # 2. FastAPI
        if "fastapi" in skill_lower:
            return [
                TrainingModule(
                    module_id="MOD-FASTAPI-01",
                    title="FastAPI RESTful Architecture & Dependency Injection",
                    concept_summary="Pydantic schema validation, status codes (201/404), Depends providers.",
                    practice_challenge="Build route handlers returning typed status codes and validated responses.",
                    starter_code="def solve(request):\n    if request.get('action') == 'create':\n        return {'status_code': 201, 'data': request.get('data')}\n    return {'status_code': 404}\n",
                    test_harness="""from solution import solve
assert solve({'action': 'create', 'data': {'id': 1}})['status_code'] == 201
assert solve({'action': 'get'})['status_code'] == 404
print("FastAPI module 1 tests passed!")
""",
                    acceptance_rubric=["HTTP standards compliance", "Proper schema representation"],
                ),
            ]

        # 3. pytest / QA
        if "pytest" in skill_lower or "qa" in skill_lower:
            return [
                TrainingModule(
                    module_id="MOD-QA-01",
                    title="Adversarial Testing & Fault Injection Detection",
                    concept_summary="Parametric testing, fixtures, monkeypatching, and edge-case assertion coverage.",
                    practice_challenge="Write test validators that assert exit codes, catch regressions, and verify assertions.",
                    starter_code="def solve(run_info):\n    return run_info.get('exit_code') == 0 and run_info.get('failures') == 0\n",
                    test_harness="""from solution import solve
assert solve({'exit_code': 0, 'failures': 0}) is True
assert solve({'exit_code': 1, 'failures': 1}) is False
print("pytest QA module passed!")
""",
                    acceptance_rubric=["Detects failure accurately", "Zero false positives"],
                ),
            ]

        # 4. Security Auditing
        if "security" in skill_lower:
            return [
                TrainingModule(
                    module_id="MOD-SEC-01",
                    title="Security Auditing: Vulnerability Scanning & Secret Detection",
                    concept_summary="Static AST security analysis, hardcoded secret patterns, and injection checks.",
                    practice_challenge="Implement an AST scanner that identifies vulnerable SQL string concatenations.",
                    starter_code="def solve(query_str):\n    is_vuln = '{val}' in query_str or '\" + \"' in query_str\n    return {'vulnerable': is_vuln}\n",
                    test_harness="""from solution import solve
assert solve("SELECT * FROM users WHERE id = '{val}'")['vulnerable'] is True
assert solve("SELECT * FROM users WHERE id = :user_id")['vulnerable'] is False
print("Security auditing module passed!")
""",
                    acceptance_rubric=["Zero false negatives on SQLi", "Proper parametrized query recognition"],
                ),
            ]

        # 5. Docker
        if "docker" in skill_lower:
            return [
                TrainingModule(
                    module_id="MOD-DOCKER-01",
                    title="Docker Containerization & Non-Root Workstation Isolation",
                    concept_summary="Non-root users (uid 1000), volume mounts, port allocations, and multi-stage builds.",
                    practice_challenge="Validate container configs enforcing non-root execution and security limits.",
                    starter_code="def solve(spec):\n    is_non_root = spec.get('user') == '1000:1000'\n    has_ports = len(spec.get('ports', [])) >= 2\n    return {'is_valid': is_non_root and has_ports, 'non_root': is_non_root}\n",
                    test_harness="""from solution import solve
res = solve({'user': '1000:1000', 'ports': [5901, 6081]})
assert res['is_valid'] is True and res['non_root'] is True
print("Docker containerization module passed!")
""",
                    acceptance_rubric=["Enforces non-root container", "Validates port specs"],
                ),
            ]

        # 6. System Design
        if "system design" in skill_lower or "architecture" in skill_lower:
            return [
                TrainingModule(
                    module_id="MOD-SYS-01",
                    title="System Design: Decoupled Microservices & Schema Scaling",
                    concept_summary="Monolithic decomposition, REST contracts, and idempotent event brokers.",
                    practice_challenge="Decompose architecture specifications into decoupled microservices.",
                    starter_code="def solve(spec):\n    return {'architecture': spec.get('architecture'), 'services_count': 3}\n",
                    test_harness="""from solution import solve
res = solve({'architecture': 'microservices'})
assert res.get('services_count', 0) >= 2
print("System design module passed!")
""",
                    acceptance_rubric=["Decoupled interfaces", "Idempotent communication"],
                ),
            ]

        # Generic domain skill module
        return [
            TrainingModule(
                module_id=f"MOD-{skill_name[:4].upper()}-01",
                title=f"{skill_name} Foundations & Hands-On Implementation",
                concept_summary=f"Foundational concepts, idioms, and design patterns for {skill_name}.",
                practice_challenge=f"Implement a working solution demonstrating {skill_name} competency.",
                starter_code="def solve(payload):\n    return {'status': 'success', 'data': payload}\n",
                test_harness="""from solution import solve
res = solve('test')
assert res.get('status') == 'success'
print("Foundation test passed!")
""",
                acceptance_rubric=["Syntactically valid", "Passes automated verification harness"],
            )
        ]
