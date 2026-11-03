from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
)
from neural.cognitive_fabric.intelligence_loops.reasoning_loop import ControlledReasoningLoop
from neural.cognitive_fabric.intelligence_loops.recovery_loop import ControlledRecoveryLoop
from neural.cognitive_fabric.knowledge_graph.graph_rag import GraphRAGEngine
from neural.cognitive_fabric.knowledge_graph.models import EntityType, RelationType
from neural.cognitive_fabric.knowledge_graph.temporal_graph import TemporalKnowledgeGraph
from neural.cognitive_fabric.memory.consolidation import MemoryConsolidationEngine
from neural.cognitive_fabric.memory.models import MemoryLayer
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.organizational_moe.expert_router import OrganizationalMoERouter
from neural.cognitive_fabric.retrieval.agentic_rag import AgenticRAGEngine
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.constants import EmployeeStatus, SkillLevel, TaskPriority
from neural.containers.manager import ContainerManager
from neural.db.models import Employee, EmployeeSkill, Task
from neural.evaluation.cognitive_benchmark import ANCOSCognitiveBenchmark
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.resource_scheduler.hardware_profiler import PROFILE_16GB, HardwareProfiler
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.resource_scheduler.scheduler import DesktopResourceScheduler


@pytest.fixture(autouse=True)
def fake_desktop_containers(monkeypatch):
    """Keep capacity integration tests independent of an installed Docker engine."""
    async def provision(_self, employee_id: str, role_image: str = "employee-base", **_limits):
        return {
            "docker_id": f"ancos-desktop-{employee_id.lower()}",
            "workspace": f"./data/workspaces/{employee_id}",
            "desktop_url": "http://localhost:6081/vnc.html?autoconnect=true",
        }

    async def terminate(_self, _docker_id: str):
        return True

    monkeypatch.setattr(ContainerManager, "provision_employee_desktop", provision)
    monkeypatch.setattr(ContainerManager, "terminate_desktop", terminate)


def create_employee(
    emp_id: str,
    name: str,
    skills: list[tuple[str, bool]],
    department: str = "Engineering",
) -> Employee:
    emp = Employee(
        employee_id=emp_id,
        name=name,
        title="Specialist Engineer",
        department=department,
        personality="Analytical",
        status=EmployeeStatus.IDLE,
        performance_summary={"quality_score": 1.0, "success_rate": 1.0, "completed_tasks": 12},
    )
    emp.skills = [
        EmployeeSkill(
            id=f"sk-{emp_id}-{i}",
            employee_id=emp_id,
            skill_name=s_name,
            level=SkillLevel.EXPERT,
            verified=is_v,
        )
        for i, (s_name, is_v) in enumerate(skills)
    ]
    return emp


@pytest.mark.asyncio
async def test_end_to_end_cognitive_fabric_lifecycle():
    temp_dir = tempfile.mkdtemp()
    try:
        # 1. Initialize Cognitive Fabric Subsystems
        six_tier_store = SixTierMemoryStore()
        consolidation = MemoryConsolidationEngine(six_tier_store)
        hybrid_retriever = HybridRetrievalEngine(memory_store=six_tier_store)
        agentic_rag = AgenticRAGEngine(retriever=hybrid_retriever)

        kg = TemporalKnowledgeGraph()
        graph_rag = GraphRAGEngine(kg=kg, hybrid_retriever=hybrid_retriever)
        moe_router = OrganizationalMoERouter()

        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # 2. Ingest Historical Organizational Knowledge & Graph Relationships
        kg.add_entity("PROJ-CORE", EntityType.PROJECT, "ANCOS Core Platform")
        kg.add_entity("TASK-INCIDENT-9", EntityType.TASK, "Database Connection Pool Starvation")
        kg.add_entity("FAIL-LEAK", EntityType.FAILURE, "Active Connection Exhaustion 503")
        kg.add_entity("DEC-TIMEOUT", EntityType.DECISION, "Lower Idle Timeout to 150s")

        kg.add_relationship(
            from_entity_id="PROJ-CORE",
            to_entity_id="TASK-INCIDENT-9",
            rel_type=RelationType.DEPENDS_ON,
            source="incident_9_log",
            verification_status="verified",
        )
        kg.add_relationship(
            from_entity_id="TASK-INCIDENT-9",
            to_entity_id="FAIL-LEAK",
            rel_type=RelationType.CAUSED_FAILURE,
            source="incident_9_log",
            verification_status="verified",
        )

        hybrid_retriever.index_document(
            "doc-incident-9-rca",
            "Root Cause Analysis: Connection pool exhaustion occurred when background worker threads failed to release SQLite connections under burst traffic.",
            metadata={"source": "incident_9_rca", "is_verified": True, "access_level": "internal"},
        )

        # 3. Step A: Boss investigates incident using Agentic RAG and GraphRAG
        agentic_res = await agentic_rag.research("Why did Database Connection Pool Starvation happen?")
        assert agentic_res["evidence_count"] >= 1

        research_result = await graph_rag.query("Why did Database Connection Pool Starvation happen?")
        assert research_result["graph_facts_count"] >= 1
        assert len(research_result["graph_evidence"]) >= 1
        assert "incident_9_rca" in research_result["answer"] or "incident_9_log" in research_result["answer"]

        # 4. Step B: Boss uses Organizational MoE to assign resolution task
        alice = create_employee("ANC-EMP-ALICE", "Alice Database", [("sqlite", True), ("concurrency", True)])
        bob = create_employee("ANC-EMP-BOB", "Bob UI", [("react", True), ("css", True)])
        charlie = create_employee("ANC-EMP-CHARLIE", "Charlie Security", [("audit", True), ("security", True)])

        resolution_task = Task(
            task_id="TASK-FIX-POOL",
            title="Implement Connection Pool Auto-Recycle and WAL Guard",
            priority=TaskPriority.HIGH,
            required_capabilities=["sqlite", "concurrency"],
        )

        route_res = await moe_router.route_task(
            task=resolution_task,
            available_employees=[alice, bob, charlie],
        )

        assert route_res["selected_employee_id"] == "ANC-EMP-ALICE"
        assert route_res["selected_employee"].name == "Alice Database"

        # 5. Step C: Resource Scheduler provisions desktop slot for Alice
        slot_res = await scheduler.request_desktop_session("ANC-EMP-ALICE", "TASK-FIX-POOL")
        assert slot_res["status"] == "active"
        assert slot_res["slot_id"] == 1

        # 6. Step D: Alice executes task and consolidates experience into 6-tier memory
        receipt = {
            "summary": "Implemented auto-recycle hook after 150s idle time; eliminated connection leakage.",
            "technical_claims": [
                {
                    "concept": "SQLiteConnectionPooling",
                    "statement": "SQLite connections in async workers require pool_pre_ping=True and timeout=150s.",
                    "confidence": 0.98,
                    "supersedes_old": True,
                }
            ],
            "reusable_procedure": {
                "name": "ConfigureSafeSQLiteWorkerPool",
                "steps": [
                    {"step": 1, "action": "set pool_pre_ping to True"},
                    {"step": 2, "action": "set pool_recycle to 150"},
                ],
            },
        }

        cons_res = await consolidation.consolidate_task_experience(
            task_id="TASK-FIX-POOL",
            employee_id="ANC-EMP-ALICE",
            outcome="success",
            receipt=receipt,
            verifier_id="BossAI",
        )

        assert cons_res["consolidated_records_count"] >= 2

        # 7. Step E: Verify Procedural and Semantic Memory are Queryable with RBAC
        # Charlie from Security can query internal procedural memory
        proc_mem = six_tier_store.query_records(
            query="ConfigureSafeSQLiteWorkerPool",
            layer=MemoryLayer.PROCEDURAL,
            requester_id="ANC-EMP-CHARLIE",
            department="Security",
        )
        assert len(proc_mem) >= 1
        assert proc_mem[0].is_verified

        # Store Alice's private scratchpad notes
        await six_tier_store.store_working(
            task_id="TASK-FIX-POOL",
            employee_id="ANC-EMP-ALICE",
            content="Alice private thoughts on database architecture refactoring",
        )

        # Bob cannot read Alice's restricted working memory
        bob_scratch = six_tier_store.query_records(
            query="private thoughts",
            layer=MemoryLayer.WORKING,
            requester_id="ANC-EMP-BOB",
            department="Engineering",
        )
        assert len(bob_scratch) == 0

        # Boss can read Alice's working memory
        boss_scratch = six_tier_store.query_records(
            query="private thoughts",
            layer=MemoryLayer.WORKING,
            requester_id="BossAI",
        )
        assert len(boss_scratch) == 1

        # Clean up desktop slots
        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_controlled_intelligence_loops_and_cycle_guard():
    # 1. Controlled Reasoning Loop: Plan converges with correction
    reasoning_loop = ControlledReasoningLoop(max_iterations=4, timeout_seconds=5.0)

    initial_flawed_plan = {"name": "Deploy API", "phases": []}

    converged_res = await reasoning_loop.execute(
        objective="Deploy backend API",
        initial_plan=initial_flawed_plan,
    )

    assert converged_res["status"] == "converged"
    assert converged_res["iterations"] >= 1
    assert len(converged_res["final_plan"]["phases"]) >= 1

    # 2. Cycle Guard: Detects cycle and halts infinite loop
    guard = ControlledLoopGuard(loop_name="CycleTestLoop", max_iterations=10)

    guard.record_step("step1", state={"state_val": "STATE_A"})
    guard.record_step("step2", state={"state_val": "STATE_B"})

    # Repeating exact previous state must raise CycleDetectedError immediately
    with pytest.raises(CycleDetectedError) as exc_info:
        guard.record_step("step3", state={"state_val": "STATE_B"})
    assert "Immediate loop cycle detected" in str(exc_info.value)

    # 3. Controlled Recovery Loop
    recovery_loop = ControlledRecoveryLoop(max_retries=2, timeout_seconds=5.0)

    # Scenario where automated fix succeeds
    successful_recovery = await recovery_loop.execute_recovery(
        task_id="TASK-ERR-1",
        initial_error="Port 5901 conflict",
        diagnosis_fn=lambda err: "PortInUse",
        recovery_action_fn=lambda diag: True if diag == "PortInUse" else False,
    )
    assert successful_recovery["status"] == "recovered"
    assert not successful_recovery["escalated_to_boss"]

    # Scenario where unfixable error escalates to Boss
    unfixable_recovery = await recovery_loop.execute_recovery(
        task_id="TASK-ERR-2",
        initial_error="Kernel panic in container driver",
        diagnosis_fn=lambda err: "DriverFatal",
        recovery_action_fn=lambda diag: False,
    )
    assert unfixable_recovery["status"] == "escalated"
    assert unfixable_recovery["escalated_to_boss"] is True


@pytest.mark.asyncio
async def test_resource_scheduler_workforce_stress_and_64gb_scaling():
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        profiler = HardwareProfiler(profile=PROFILE_16GB)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=profiler,
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # 8 Employees request desktop slots simultaneously on 16 GB baseline
        emp_ids = [f"ANC-EMP-{i}" for i in range(1, 9)]
        alloc_results = []

        for eid in emp_ids:
            res = await scheduler.request_desktop_session(
                employee_id=eid,
                task_id=f"TASK-STRESS-{eid}",
                priority=TaskPriority.HIGH if eid in ("ANC-EMP-1", "ANC-EMP-5") else TaskPriority.MEDIUM,
            )
            alloc_results.append(res)

        active_count = sum(1 for r in alloc_results if r["status"] == "active")
        standby_count = sum(1 for r in alloc_results if r["status"] == "queued_standby")

        # EXACTLY 4 ACTIVE SLOTS ENFORCED ON 16GB MACHINE
        assert active_count == 4
        assert standby_count == 4
        assert scheduler.slot_pool.get_active_count() == 4
        assert len(scheduler._standby_employees) == 4

        # Suspend ANC-EMP-1 with durable checkpoint
        suspend_res = await scheduler.suspend_and_release(
            employee_id="ANC-EMP-1",
            task_id="TASK-STRESS-ANC-EMP-1",
            current_step=10,
            state_dict={"processed_records": 1000},
        )
        assert suspend_res["status"] == "suspended_to_standby"
        assert suspend_res["checkpoint_saved"] is True

        # Checkpoint is durable on host disk outside container
        assert os.path.exists(suspend_res["checkpoint_path"])

        # ANC-EMP-5 was prioritized from standby and granted the freed slot
        assert suspend_res["dispatched_next"] is not None
        assert suspend_res["dispatched_next"]["employee_id"] == "ANC-EMP-5"
        assert suspend_res["dispatched_next"]["status"] == "active"

        # Dynamically scale capacity to 64 GB profile
        scheduler.set_hardware_profile("64GB")
        assert scheduler.profiler.active_profile.max_active_desktops == 16
        assert scheduler.slot_pool.max_slots == 16

        # Now more standby employees can be admitted into active desktop slots
        standby_admitted = await scheduler.request_desktop_session("ANC-EMP-6", "TASK-STRESS-ANC-EMP-6")
        assert standby_admitted["status"] == "active"

        # Emergency shutdown clean up
        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_ancos_cognitive_benchmark_runner():
    benchmark = ANCOSCognitiveBenchmark()

    # 1. Retrieval Benchmark
    retriever = HybridRetrievalEngine()
    retriever.index_document("doc1", "FastAPI web server with asynchronous endpoints", metadata={"is_verified": True})
    retriever.index_document("doc2", "PostgreSQL database indexing and query tuning", metadata={"is_verified": True})
    retriever.index_document("doc3", "Redis in-memory caching and session store", metadata={"is_verified": True})

    eval_data = [
        {"query": "FastAPI async", "relevant_ids": ["doc1"]},
        {"query": "PostgreSQL indexing", "relevant_ids": ["doc2"]},
    ]

    retrieval_metrics = benchmark.evaluate_retrieval(retriever, eval_data, top_k=2)
    assert retrieval_metrics["recall_at_k"] >= 0.8
    assert retrieval_metrics["precision_at_k"] >= 0.5
    assert retrieval_metrics["mrr"] >= 0.8
    assert retrieval_metrics["avg_latency_ms"] >= 0.0

    # 2. Agentic RAG Benchmark
    agentic_rag = AgenticRAGEngine(retriever=retriever)
    agentic_metrics = await benchmark.evaluate_agentic_rag(agentic_rag, ["FastAPI async", "PostgreSQL indexing"])
    assert agentic_metrics["total_queries"] == 2
    assert agentic_metrics["avg_rounds"] >= 1.0

    # 3. Knowledge Graph Benchmark
    kg = TemporalKnowledgeGraph()
    kg.add_entity("ENT-1", EntityType.PROJECT, "Project Benchmark")
    kg.add_entity("ENT-2", EntityType.TASK, "Task Benchmark")
    kg.add_relationship(
        from_entity_id="ENT-1",
        to_entity_id="ENT-2",
        rel_type=RelationType.DEPENDS_ON,
        source="bench",
        verification_status="verified",
    )
    graph_rag = GraphRAGEngine(kg=kg, hybrid_retriever=retriever)
    kg_scenarios = [
        {"question": "Project Benchmark", "target_entity_id": "ENT-1", "expected_nodes": ["ENT-1", "ENT-2"]}
    ]
    kg_metrics = await benchmark.evaluate_knowledge_graph(graph_rag, kg_scenarios)
    assert kg_metrics["scenarios_count"] == 1
    assert kg_metrics["success_rate"] == 1.0

    # 4. MoE Routing Benchmark
    router = OrganizationalMoERouter()
    emp1 = create_employee("ANC-EMP-BENCH-1", "Alice", [("fastapi", True)])
    task1 = Task(task_id="T1", title="FastAPI task", priority=TaskPriority.HIGH, required_capabilities=["fastapi"])
    moe_metrics = await benchmark.evaluate_moe_routing(router, [task1], [emp1])
    assert moe_metrics["tasks_routed"] == 1
    assert moe_metrics["verified_match_rate"] == 1.0

    # 5. Resource Scheduler Stress Benchmark
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )
        bench_eids = [f"ANC-EMP-B-{i}" for i in range(1, 7)]
        sched_metrics = await benchmark.evaluate_resource_scheduler(scheduler, bench_eids)

        assert sched_metrics["strict_4_slot_limit_enforced"] is True
        assert sched_metrics["actual_active_16gb"] <= 4
        assert sched_metrics["standby_queue_count"] == 2
        assert sched_metrics["max_slots_64gb"] == 16
        await scheduler.emergency_capacity_shutdown()

        # 6. Intelligence Loops Benchmark
        loop_metrics = await benchmark.evaluate_loops()
        assert loop_metrics["reasoning_converged"] is True
        assert loop_metrics["recovery_success"] is True
        assert loop_metrics["audit_entries_logged"] >= 3
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
