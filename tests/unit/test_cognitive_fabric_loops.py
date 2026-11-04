from __future__ import annotations

import pytest
from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.cognitive_fabric.intelligence_loops.evolution_loop import ControlledEvolutionLoop
from neural.cognitive_fabric.intelligence_loops.gui_loop import ControlledGUIExecutionLoop
from neural.cognitive_fabric.intelligence_loops.memory_loop import ControlledMemoryLoop
from neural.cognitive_fabric.intelligence_loops.reasoning_loop import ControlledReasoningLoop
from neural.cognitive_fabric.intelligence_loops.recovery_loop import ControlledRecoveryLoop
from neural.cognitive_fabric.intelligence_loops.retrieval_loop import ControlledRetrievalLoop
from neural.cognitive_fabric.intelligence_loops.skill_loop import ControlledSkillLoop
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine


def test_cycle_guard_detection_and_budgets():
    # 1. Immediate cycle (period 1)
    guard1 = ControlledLoopGuard(loop_name="TestPeriod1", max_iterations=5)
    guard1.record_step("step1", state={"pos": 1})
    with pytest.raises(CycleDetectedError, match="Immediate loop cycle"):
        guard1.record_step("step2", state={"pos": 1})

    # 2. Oscillating cycle (period 2: A -> B -> A)
    guard2 = ControlledLoopGuard(loop_name="TestPeriod2", max_iterations=5)
    guard2.record_step("stepA", state={"view": "A"})
    guard2.record_step("stepB", state={"view": "B"})
    with pytest.raises(CycleDetectedError, match="Oscillating cycle"):
        guard2.record_step("stepA2", state={"view": "A"})

    # 3. Timeout and iteration budgets
    guard3 = ControlledLoopGuard(loop_name="TestBudget", max_iterations=2)
    guard3.record_step("step1", state={"x": 1})
    guard3.record_step("step2", state={"x": 2})
    with pytest.raises(LoopBudgetExceededError, match="Max iterations"):
        guard3.record_step("step3", state={"x": 3})


@pytest.mark.asyncio
async def test_controlled_retrieval_loop_convergence_and_refinement():
    retriever = HybridRetrievalEngine()
    retriever.index_document(
        "doc-sqlite-leak",
        "SQLite connection leak caused by missing close calls in worker loops under high concurrency.",
        metadata={
            "verification_status": "verified",
            "verified_at": "2026-11-04T12:00:00",
            "source": "postmortem-10",
            "provenance": {
                "verified_by": "independent-verifier",
                "independent_verification_receipt": {
                    "passed": True,
                    "task_id": "TASK-SQLITE-10",
                    "attempt_id": "attempt-sqlite-10",
                    "verifier_id": "independent-verifier",
                    "evidence_refs": ["test:sqlite-leak"],
                },
            },
            "task_id": "TASK-SQLITE-10",
        },
    )

    loop = ControlledRetrievalLoop(retriever=retriever, max_iterations=3, sufficiency_threshold=0.6)
    result = await loop.execute("Why did SQLite connection pool leak under concurrency?")

    assert result["status"] == "converged"
    assert result["evidence_count"] >= 1
    assert result["sufficiency_score"] >= 0.6
    assert result["iterations"] >= 1
    assert "doc-sqlite-leak" in [e["id"] for e in result["evidence"]]


@pytest.mark.asyncio
async def test_controlled_reasoning_loop_correction_and_convergence():
    loop = ControlledReasoningLoop(max_iterations=3)

    # Initial plan missing phases
    flawed_plan = {"goal": "Upgrade database schema"}

    result = await loop.execute(
        objective="Upgrade database schema",
        initial_plan=flawed_plan,
    )

    assert result["status"] == "converged"
    assert "phases" in result["final_plan"]
    assert len(result["final_plan"]["phases"]) >= 1
    assert "Plan missing phases" in result["corrections_applied"]


@pytest.mark.asyncio
async def test_controlled_recovery_loop_success_and_escalation():
    loop = ControlledRecoveryLoop(max_retries=3)

    # Test successful recovery on attempt 2
    attempts = 0

    def mock_recovery_success(cause: str) -> bool:
        nonlocal attempts
        attempts += 1
        return attempts >= 2

    res_ok = await loop.execute_recovery(
        task_id="TASK-ERR-1",
        initial_error="Connection refused to port 5432",
        recovery_action_fn=mock_recovery_success,
    )

    assert res_ok["status"] == "recovered"
    assert res_ok["attempts"] == 2
    assert res_ok["escalated_to_boss"] is False

    # Test escalation when error cannot be recovered within budget
    res_escalated = await loop.execute_recovery(
        task_id="TASK-ERR-2",
        initial_error="Fatal hardware bus parity error",
        recovery_action_fn=lambda cause: False,
    )

    assert res_escalated["status"] == "escalated"
    assert res_escalated["escalated_to_boss"] is True


@pytest.mark.asyncio
async def test_controlled_memory_loop_consolidation():
    store = SixTierMemoryStore()
    loop = ControlledMemoryLoop(memory_store=store)

    receipt = {
        "summary": "Completed load balancer tuning with verified receipts",
        "technical_claims": [
            {
                "concept": "LBKeepaliveTimeout",
                "statement": "Keepalive timeout set to 65s prevents 502 bad gateway spikes.",
                "confidence": 0.95,
            }
        ],
    }

    res = await loop.execute(
        task_id="TASK-LB-1",
        employee_id="EMP-NET-1",
        outcome="success",
        receipt=receipt,
    )

    assert res["status"] == "consolidated"
    assert res["consolidation_result"]["consolidated_records_count"] >= 2

    # Verify semantic fact was stored
    memories = store.query_records(query="LBKeepaliveTimeout", requester_id="BossAI")
    assert len(memories) == 1
    assert not memories[0].is_verified
    assert store.query_trusted_records(query="LBKeepaliveTimeout", requester_id="BossAI") == []


@pytest.mark.asyncio
async def test_controlled_memory_loop_requires_independent_receipt_to_trust_memory():
    store = SixTierMemoryStore()
    loop = ControlledMemoryLoop(memory_store=store)
    task_receipt = {
        "summary": "Updated load balancer keepalive settings",
        "technical_claims": [
            {
                "concept": "LBKeepaliveTimeout",
                "statement": "Keepalive timeout set to 65s prevents 502 bad gateway spikes.",
            }
        ],
    }
    independent_receipt = {
        "passed": True,
        "task_id": "TASK-LB-VERIFIED",
        "attempt_id": "attempt-lb-verified",
        "verifier_id": "independent-verifier",
        "evidence_refs": ["verifier:attempt-lb-verified:test:1"],
    }

    result = await loop.execute(
        task_id="TASK-LB-VERIFIED",
        employee_id="EMP-NET-2",
        outcome="success",
        receipt=task_receipt,
        verification_receipt=independent_receipt,
    )

    assert result["status"] == "consolidated"
    trusted = store.query_trusted_records(query="LBKeepaliveTimeout", requester_id="BossAI")
    assert len(trusted) == 1
    assert trusted[0].provenance["verified_by"] == "independent-verifier"


@pytest.mark.asyncio
async def test_controlled_skill_loop_benchmark_and_certification():
    store = SixTierMemoryStore()
    loop = ControlledSkillLoop(memory_store=store, benchmark_pass_threshold=0.8)

    # A successful trace is not a benchmark or independent certification receipt.
    trace = [
        {"cmd": "git fetch", "exit_code": 0},
        {"cmd": "pytest", "exit_code": 0},
        {"cmd": "git push", "exit_code": 0},
    ]

    res = await loop.execute(
        procedure_name="SafeRepoPushProcedure",
        execution_trace=trace,
    )

    assert res["status"] == "not_evaluated"
    assert res["is_certified"] is False
    assert res["benchmark_score"] is None
    assert res["certified_record_id"] is None

    reviewed = await loop.execute(
        procedure_name="SafeRepoPushProcedure",
        execution_trace=trace,
        benchmark_fn=lambda _trace: 1.0,
    )
    assert reviewed["status"] == "awaiting_independent_certification"
    assert reviewed["is_certified"] is False


@pytest.mark.asyncio
async def test_controlled_evolution_loop_promotion():
    store = SixTierMemoryStore()
    loop = ControlledEvolutionLoop(memory_store=store, promotion_threshold=0.85)

    res = await loop.execute(
        domain="MemoryRetrieval",
        observed_limitations=["Sparse index missed rare acronyms"],
        experiment_runner_fn=lambda p: 0.94,  # Benchmark score exceeds 0.85
    )

    assert res["status"] == "awaiting_human_approval"
    assert res["is_promoted"] is False
    assert res["benchmark_score"] == 0.94
    assert res["proposal_for_review"] is not None


@pytest.mark.asyncio
async def test_controlled_gui_execution_loop_screenshot_grounding():
    # Simulate fresh visual frames and click actions
    frame_counter = 0

    def mock_observe() -> dict:
        nonlocal frame_counter
        frame_counter += 1
        return {
            "screenshot_b64": "fake_img_bytes",
            "frame_hash": f"frame_hash_{frame_counter}",
            "timestamp": 1000.0 + frame_counter,
        }

    def mock_reason(obs: dict, goal: str) -> dict:
        if frame_counter == 1:
            return {"action_type": "click", "coordinates": (450, 220)}
        elif frame_counter == 2:
            return {"action_type": "type", "text": "build passing"}
        else:
            return {"action_type": "done"}

    def mock_act(action: dict) -> dict:
        return {"status": "executed", "action": action["action_type"]}

    def mock_verify(obs: dict, goal: str) -> tuple[bool, str]:
        return (True, "Target outcome verified on workstation screen")

    loop = ControlledGUIExecutionLoop(max_steps=5)
    res = await loop.execute(
        task_id="TASK-GUI-1",
        employee_id="EMP-GUI-1",
        goal="Open terminal and type build passing",
        observe_screenshot_fn=mock_observe,
        reason_action_fn=mock_reason,
        execute_action_fn=mock_act,
        verify_outcome_fn=mock_verify,
    )

    assert res["status"] == "verified_success"
    assert res["is_success"] is True
    assert res["steps_executed"] == 3
    assert len(res["action_receipts"]) == 2

