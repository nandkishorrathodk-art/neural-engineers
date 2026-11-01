import pytest
from neural.constants import MemoryCategory, TaskStatus
from neural.db.repositories import (
    EmployeeRepository,
    EvolutionRepository,
    MemoryRepository,
    MethodBenchmarkRepository,
    MethodRepository,
    ProjectRepository,
    TaskRepository,
    ToolRepository,
)
from neural.evolution.meta_learning import (
    FailurePattern,
    FailurePatternAnalyzer,
    PromptOptimizer,
    RecursiveSelfImprovementEngine,
)
from neural.memory.store import OrganizationalMemoryStore
from neural.methods.lab import MethodLabEngine
from neural.sandboxes.workstation import WorkstationSandbox
from neural.tools.engine import ToolsmithEngine
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_failure_pattern_analyzer(async_session: AsyncSession):
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Proj-Fail-Test", objective="Test failures")

    # Add failing tasks with distinct error profiles
    await task_repo.create(
        task_id="TASK-ERR-ASYNC",
        project_id=project.id,
        title="Async Database Query",
        objective="Run async select queries",
        scope="Backend",
        acceptance_criteria="No connection leaks",
        status=TaskStatus.FAILED,
        verification_result={"notes": "async session closed prematurely, missing await in context manager"},
    )
    await task_repo.create(
        task_id="TASK-ERR-TIMEOUT",
        project_id=project.id,
        title="Batch Data Migration",
        objective="Migrate rows",
        scope="Data",
        acceptance_criteria="Fast execution",
        status=TaskStatus.FAILED,
        verification_result={"notes": "execution deadline exceeded, latency timed out after 30s"},
    )
    await task_repo.create(
        task_id="TASK-ERR-SAFETY",
        project_id=project.id,
        title="OS Scripting Utility",
        objective="Run shell",
        scope="Core",
        acceptance_criteria="Clean run",
        status=TaskStatus.FAILED,
        verification_result={"notes": "AST restricted pattern os.system detected violating safety boundary"},
    )

    analyzer = FailurePatternAnalyzer(task_repo=task_repo)
    patterns = await analyzer.analyze_failure_patterns()

    assert len(patterns) >= 3
    pattern_types = {p.pattern_type for p in patterns}
    assert "ASYNC_SESSION_MISMANAGEMENT" in pattern_types
    assert "TIMEOUT_EXCEEDED" in pattern_types
    assert "SAFETY_BOUNDARY_VIOLATION" in pattern_types

    async_pat = next(p for p in patterns if p.pattern_type == "ASYNC_SESSION_MISMANAGEMENT")
    assert async_pat.severity == "high"
    assert "TASK-ERR-ASYNC" in async_pat.sample_task_ids


def test_prompt_optimizer():
    optimizer = PromptOptimizer()
    pat = FailurePattern(
        pattern_id="PAT-TEST-01",
        pattern_type="ASYNC_SESSION_MISMANAGEMENT",
        frequency=2,
        severity="high",
        root_cause="Improper lifecycle of async sessions",
        proposed_remediation="Inject async context rules",
        remediation_type="prompt",
    )

    base_prompt = "You are an ANCOS engineer."
    result = optimizer.optimize_prompt(base_prompt, pat, target_role="Senior Backend Engineer")

    assert result["optimized_quality_score"] > result["baseline_quality_score"]
    assert result["improvement_pct"] > 0
    assert any("async with session.begin()" in rule for rule in result["rules_injected"])
    assert "You are an ANCOS engineer." in result["optimized_prompt"]


@pytest.mark.asyncio
async def test_four_tier_organizational_memory(async_session: AsyncSession):
    mem_repo = MemoryRepository(async_session)
    store = OrganizationalMemoryStore(memory_repo=mem_repo)

    # 1. Episodic
    ep = await store.store_episodic(
        task_id="TASK-100",
        employee_id="ANC-EMP-001",
        outcome="success",
        summary="Optimized SQLite connection pooling with WAL mode",
    )
    assert ep.category == MemoryCategory.EPISODE

    # 2. Semantic
    sem = await store.store_semantic(
        concept="Write-Ahead-Logging",
        explanation="WAL allows concurrent readers and a single writer without contention.",
        tags=["sqlite", "concurrency"],
    )
    assert sem.category == MemoryCategory.KNOWLEDGE

    # 3. Procedural
    proc = await store.store_procedural(
        procedure_name="HotfixDeploymentProcedure",
        steps=[{"step": 1, "action": "checkout release tag"}, {"step": 2, "action": "run smoke tests"}],
    )
    assert proc.category == MemoryCategory.PROCEDURE

    # 4. Policy
    pol = await store.store_policy(
        policy_name="ZeroHardcodedCredentials",
        statement="All API secrets must resolve from environment variables or vault.",
        enforcement_level="mandatory",
    )
    assert pol.category == MemoryCategory.POLICY

    # Query tests
    all_entries = await store.query_memory()
    assert len(all_entries) == 4

    searched = await store.query_memory(query="connection pooling")
    assert len(searched) >= 1
    assert "sqlite" in searched[0].content.lower()

    # Tier stats
    stats = await store.get_tier_stats()
    assert stats[MemoryCategory.EPISODE.value] >= 1
    assert stats[MemoryCategory.KNOWLEDGE.value] >= 1
    assert stats[MemoryCategory.PROCEDURE.value] >= 1
    assert stats[MemoryCategory.POLICY.value] >= 1


@pytest.mark.asyncio
async def test_recursive_self_improvement_cycle(async_session: AsyncSession):
    evo_repo = EvolutionRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)
    mem_repo = MemoryRepository(async_session)
    tool_repo = ToolRepository(async_session)
    method_repo = MethodRepository(async_session)
    bmk_repo = MethodBenchmarkRepository(async_session)

    sandbox = WorkstationSandbox()
    store = OrganizationalMemoryStore(memory_repo=mem_repo)
    toolsmith = ToolsmithEngine(tool_repo=tool_repo, sandbox=sandbox)
    method_lab = MethodLabEngine(method_repo=method_repo, benchmark_repo=bmk_repo)

    engine = RecursiveSelfImprovementEngine(
        evolution_repo=evo_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
        memory_store=store,
        toolsmith=toolsmith,
        method_lab=method_lab,
    )

    sample_failures = [
        {
            "task_id": "TASK-FAIL-1",
            "title": "Parse JSON input",
            "verification_notes": "AST safety boundary violation restricted os.system used",
            "error": "Safety violation",
        },
        {
            "task_id": "TASK-FAIL-2",
            "title": "Async DB Query",
            "verification_notes": "async session error missing await on execute",
            "error": "Session error",
        },
    ]

    report = await engine.run_self_improvement_cycle(
        trigger_reason="test_cycle",
        auto_apply=True,
        sample_failures=sample_failures,
    )

    assert report["status"] == "completed"
    assert len(report["failure_patterns_detected"]) >= 2
    assert len(report["actions_taken"]) >= 2
    assert report["memories_stored_count"] >= 2
    assert report["evolution_record_id"] is not None

    # Check evolution record persisted in database
    rec = await evo_repo.get_by_id(report["evolution_record_id"])
    assert rec is not None
    assert rec.record_type == "meta_learning_cycle"
    assert rec.status == "completed"


@pytest.mark.asyncio
async def test_failure_pattern_analyzer_handles_string_verification_result(async_session: AsyncSession):
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Proj-String-Verif", objective="Test string notes")

    # Add failing task with string verification result (not dict)
    await task_repo.create(
        task_id="TASK-ERR-RAW-STRING",
        project_id=project.id,
        title="Raw String Error Task",
        objective="Test raw string error",
        scope="Core",
        acceptance_criteria="Pass",
        status=TaskStatus.FAILED,
        verification_result="Timeout exceeded: latency exceeded 45s deadline",
    )

    analyzer = FailurePatternAnalyzer(task_repo=task_repo)
    patterns = await analyzer.analyze_failure_patterns()

    assert any(p.pattern_type == "TIMEOUT_EXCEEDED" for p in patterns)
    timeout_pat = next(p for p in patterns if p.pattern_type == "TIMEOUT_EXCEEDED")
    assert "TASK-ERR-RAW-STRING" in timeout_pat.sample_task_ids

