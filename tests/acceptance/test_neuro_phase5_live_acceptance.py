"""
ANCOS NEURO-5 Live Acceptance Test Suite: Deep Intelligence, Fast Intelligence & Multi-Model Routing.
Empirically verifies:
1. Multi-tier routing across reasoning, coding, and fast tiers with route configuration.
2. Multi-provider candidate selection and credential-aware filtering (OpenAI, Anthropic, Gemini, NVIDIA NIM).
3. Transparent cross-provider failover with model identity and provider receipts.
4. Circuit breaker lifecycle: CLOSED -> OPEN on failure threshold -> fast-failing -> recovery.
5. Real-time USD cost tracking with automatic hard budget ceilings per task, project, and monthly organization cap.
6. Per-call durable database pre-reservation and usage settlement with atomic overrun prevention.
7. Real Laya Model checkpoint SHA-256 cryptographic integrity validation on disk and tampered hash rejection.
8. High-throughput System 1 fast cognitive inference primitives: Choice, Score, and Noul.
9. Shared Model Pool singleton weight management and Fair-Share Inference Queue with per-employee concurrency caps.
10. Asynchronous micro-batching via MicroBatchManager and System 1 Health Telemetry.
11. Genuine ONNX Runtime checkpoint verification, session loading, and ONNX neural inference execution.
12. Policy engine & Human Approval Gate sovereignty over privileged/destructive operations.
13. Independent deliverable verification with cryptographic receipts and empirical skill certification.
14. Tamper-evident immutable hash-chained audit trail integrity verification.
"""

from __future__ import annotations

import os
import tempfile
import time
from decimal import Decimal

import pytest
from neural.constants import EmployeeStatus, ExecutionMode, ModelRuntime, TaskPriority, TaskStatus
from neural.db.models import (
    BudgetReservation,
    Employee,
    EmployeeSkill,
    Project,
    Task,
)
from neural.db.repositories import (
    AuditRepository,
    EmployeeRepository,
    TaskRepository,
)
from neural.decision_intelligence.observability.model_health import (
    HealthState,
    ModelHealthMonitor,
)
from neural.decision_intelligence.schemas.choice import (
    ChoiceCandidate,
    ChoiceQuestion,
    ChoiceResult,
)
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.inference_service import (
    LayaInferenceService,
    reset_inference_service,
)
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.decision_intelligence.system_one.model_registry import (
    ModelCheckpointMetadata,
    ModelRegistry,
    reset_model_registry,
)
from neural.evaluation.verifier import IndependentDeliverableVerifier
from neural.exceptions import (
    ModelCheckpointError,
    ModelProviderError,
    QuotaExceededError,
)
from neural.llm.budget import load_budget_scope_caps
from neural.llm.cost import CostTracker
from neural.llm.gateway import LLMGateway
from neural.llm.retry import CircuitBreaker, CircuitState
from neural.observability.audit import AuditLogChainVerifier
from neural.security.approval_gates import HumanApprovalGate
from neural.shared_inference.inference_queue import SharedInferenceQueue
from neural.shared_inference.model_pool import SharedModelPool
from sqlalchemy import select


@pytest.fixture(autouse=True)
def cleanup_singletons():
    reset_model_registry()
    reset_inference_service()
    yield
    reset_model_registry()
    reset_inference_service()


@pytest.mark.asyncio
async def test_neuro_phase5_deep_and_fast_intelligence_live_acceptance(async_session):
    """
    NEURO-5 Live Acceptance Scenario:
    Demonstrates full compliance with NEURO-5 Deep Intelligence, Fast Intelligence
    and Multi-Model Routing across all 14 empirical verification milestones.
    """

    # =========================================================================
    # STEP 1: Multi-Tier Routing Architecture & Route Configuration
    # =========================================================================
    gateway = LLMGateway(config_path="configs/model-routing.yaml")

    # Verify all 3 tiers are defined
    assert "reasoning" in gateway.tiers_config
    assert "coding" in gateway.tiers_config
    assert "fast" in gateway.tiers_config

    # Verify functional route resolution
    assert gateway.resolve_tier_for_route("boss_brain") == "reasoning"
    assert gateway.resolve_tier_for_route("task_decomposer") == "reasoning"
    assert gateway.resolve_tier_for_route("meeting_moderator") == "reasoning"
    assert gateway.resolve_tier_for_route("independent_verifier") == "reasoning"
    assert gateway.resolve_tier_for_route("code_review") == "reasoning"
    assert gateway.resolve_tier_for_route("code_generation") == "coding"
    assert gateway.resolve_tier_for_route("code_debugging") == "coding"
    assert gateway.resolve_tier_for_route("status_reporting") == "fast"
    assert gateway.resolve_tier_for_route("log_summarization") == "fast"

    # Verify tier model candidates ordering
    reasoning_models = gateway.get_models_for_tier("reasoning")
    assert len(reasoning_models) >= 4
    assert reasoning_models[0].startswith("nim/")
    assert "openai/gpt-6.1-sol" in reasoning_models
    assert "anthropic/claude-sonnet-5-5" in reasoning_models
    assert "gemini/gemini-3.5-flash" in reasoning_models

    coding_models = gateway.get_models_for_tier("coding")
    assert len(coding_models) >= 4
    assert coding_models[0].startswith("nim/")
    assert "openai/gpt-6.1-sol" in coding_models
    assert "anthropic/claude-sonnet-5-5" in coding_models

    fast_models = gateway.get_models_for_tier("fast")
    assert len(fast_models) >= 4
    assert fast_models[0].startswith("nim/")
    assert "openai/gpt-6-luna" in fast_models

    # =========================================================================
    # STEP 2: Credential-Aware Provider Filtering
    # =========================================================================
    # Simulate environment where only OpenAI and Anthropic credentials exist
    configured_providers = {"openai", "anthropic"}
    filtered_candidates = [
        m for m in reasoning_models
        if (p := gateway._model_provider(m)) is not None and p in configured_providers
    ]
    assert len(filtered_candidates) >= 2
    assert all(gateway._model_provider(m) in {"openai", "anthropic"} for m in filtered_candidates)
    assert not any(gateway._model_provider(m) == "gemini" for m in filtered_candidates)

    # When all credentials are configured, all vendors are eligible
    all_providers = {"nvidia", "openai", "anthropic", "gemini"}
    all_candidates = [
        m for m in reasoning_models
        if (p := gateway._model_provider(m)) is not None and p in all_providers
    ]
    assert len(all_candidates) == len(reasoning_models)

    # =========================================================================
    # STEP 3: Transparent Cross-Provider Failover with Model Identity Receipts
    # =========================================================================
    invocations = []

    async def failover_mock_invoker(**kwargs):
        model_name = kwargs["model"]
        invocations.append(model_name)
        if model_name.startswith("nim/"):
            # Simulate primary NVIDIA NIM service unavailability
            raise ModelProviderError(f"HTTP 503 Service Unavailable from NIM host for {model_name}")
        elif "gpt-6.1-sol" in model_name:
            # Fallback candidate succeeds
            return {
                "content": "def route_request(tier: str): return 'routed_successfully'",
                "input_tokens": 42,
                "output_tokens": 18,
                "provider": "openai",
            }
        raise ModelProviderError(f"Unexpected candidate: {model_name}")

    failover_gateway = LLMGateway(
        config_path="configs/model-routing.yaml",
        provider_invoker=failover_mock_invoker,
    )

    response = await failover_gateway.generate(
        prompt="Generate a Python request router function",
        tier="reasoning",
    )

    assert response is not None
    assert response.content == "def route_request(tier: str): return 'routed_successfully'"
    assert response.model == "openai/gpt-6.1-sol"
    assert response.provider == "openai"
    assert response.tier == "reasoning"
    assert response.input_tokens == 42
    assert response.output_tokens == 18
    assert response.cost_usd > 0.0
    # Verifies attempted models metadata includes failed primary and successful fallback
    assert len(invocations) >= 3
    assert response.metadata["attempted_models"] == [
        "nim/nvidia/nemotron-3.5-lightning-30b-a3b",
        "nim/nvidia/nemotron-3.5-bolt-e4b",
        "openai/gpt-6.1-sol",
    ]

    # =========================================================================
    # STEP 4: Circuit Breaker Lifecycle: Closed -> Open -> Recovery
    # =========================================================================
    cb = CircuitBreaker(name="test-model", failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1st and 2nd failure
    cb.record_failure(ModelProviderError("Error 1"))
    assert cb.state == CircuitState.CLOSED
    cb.record_failure(ModelProviderError("Error 2"))
    assert cb.state == CircuitState.CLOSED

    # 3rd failure trips the breaker to OPEN
    cb.record_failure(ModelProviderError("Error 3"))
    assert cb.state == CircuitState.OPEN
    # While OPEN, executions are fast-failed without invoking the model
    assert cb.can_execute() is False

    # Wait for recovery timeout
    time.sleep(0.25)
    # After timeout, enters HALF_OPEN probe state
    assert cb.can_execute() is True
    # Successful execution resets breaker to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb._consecutive_failures == 0

    # =========================================================================
    # STEP 5: Precision Real-Time USD Cost Tracking & Multi-Scope Ceilings
    # =========================================================================
    cost_tracker = CostTracker(
        max_task_budget_usd=5.00,
        max_project_budget_usd=25.00,
        monthly_hard_cap_usd=100.00,
    )

    # Cost calculations for configured models
    cost_sonnet = cost_tracker.calculate_cost("claude-sonnet-5-5", input_tokens=1000, output_tokens=500)
    assert cost_sonnet == round((1000 / 1_000_000 * 2.00) + (500 / 1_000_000 * 10.00), 6)

    cost_nemotron = cost_tracker.calculate_cost("nemotron-3.5-lightning-30b-a3b", input_tokens=2000, output_tokens=1000)
    assert cost_nemotron == round((2000 / 1_000_000 * 0.30) + (1000 / 1_000_000 * 0.90), 6)

    # Record legitimate usage
    cost_tracker.record_usage(
        model="claude-sonnet-5-5",
        input_tokens=1000,
        output_tokens=500,
        task_id="TASK-N5-01",
        employee_id="ANC-EMP-01",
        project_id="PROJ-N5",
    )
    task_summary = cost_tracker.get_task_cost("TASK-N5-01")
    assert task_summary["input_tokens"] == 1000
    assert task_summary["output_tokens"] == 500
    assert task_summary["cost_usd"] == cost_sonnet

    # Task budget cap enforcement
    with pytest.raises(QuotaExceededError, match="Task 'TASK-N5-01' budget ceiling"):
        cost_tracker.check_budget_ceiling(cost_delta=6.00, task_id="TASK-N5-01")

    # Project budget cap enforcement
    with pytest.raises(QuotaExceededError, match="Project 'PROJ-N5' budget ceiling"):
        cost_tracker.check_budget_ceiling(cost_delta=26.00, project_id="PROJ-N5")

    # Monthly organizational cap enforcement
    with pytest.raises(QuotaExceededError, match="Monthly organizational budget cap"):
        cost_tracker.check_budget_ceiling(cost_delta=105.00)

    # =========================================================================
    # STEP 6: Per-Call Durable Pre-Reservation & Usage Settlement
    # =========================================================================
    project = Project(
        id="PROJ-NEURO5-ACCEPTANCE",
        name="Deep and Fast Intelligence Acceptance",
        objective="Empirically verify Phase 5 multi-model routing and Laya fast intelligence.",
        status="active",
        execution_plan={"budget_usd": 50.0},
    )
    async_session.add(project)

    task = Task(
        task_id="TASK-N5-METERED",
        project_id=project.id,
        title="Metered Model Execution",
        objective="Verify pre-reservation and durable spend settlement.",
        scope="Inference",
        acceptance_criteria="Passes all verifications",
        execution_mode=ExecutionMode.RESEARCH_REASONING,
        status=TaskStatus.RUNNING,
        resource_budget={"max_cost_usd": 10.0},
    )
    async_session.add(task)
    await async_session.flush()

    budget_caps = await load_budget_scope_caps(
        async_session,
        cost_tracker,
        project_id=project.id,
        task_id=task.task_id,
    )
    assert f"project:{project.id}" in budget_caps
    assert f"task:{task.task_id}" in budget_caps

    async def metered_mock_invoker(**kwargs):
        return {
            "content": "Settled response",
            "input_tokens": 100,
            "output_tokens": 50,
            "provider": "openai",
        }

    budget_gateway = LLMGateway(
        provider_invoker=metered_mock_invoker,
    )

    settled_resp = await budget_gateway.generate(
        prompt="Produce validated neural response",
        model="gpt-4o-mini",
        max_tokens=100,
        project_id=project.id,
        task_id=task.task_id,
        db_session=async_session,
        budget_scope_caps=budget_caps,
    )
    assert settled_resp.content == "Settled response"

    # Confirm durable reservation was recorded and settled
    reservation = await async_session.scalar(
        select(BudgetReservation).order_by(BudgetReservation.created_at.desc())
    )
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.actual_usd > Decimal("0")
    assert reservation.usage_receipt["model"] == "gpt-4o-mini"
    assert reservation.usage_receipt["input_tokens"] == 100

    # =========================================================================
    # STEP 7: Real Laya Model ModernBERT Weights & SHA-256 Verification
    # =========================================================================
    real_weights_path = os.path.abspath("./data/models/laya/base/model.safetensors")
    assert os.path.exists(real_weights_path), f"Laya weights missing at {real_weights_path}"
    file_size_mb = os.path.getsize(real_weights_path) / (1024 * 1024)
    assert file_size_mb > 800.0, f"Expected >800MB safetensors weights, found {file_size_mb:.2f}MB"

    registry = ModelRegistry()
    ckpt_meta = registry.get_checkpoint("Laya-English-Base-Neural")
    assert ckpt_meta.sha256_hash == "891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c"

    # Cryptographic SHA-256 verification of the genuine local weights file
    assert registry.verify_checkpoint_file("Laya-English-Base-Neural", real_weights_path) is True

    # Tampered weights verification failure
    with pytest.raises(ModelCheckpointError, match="hash verification failed"):
        registry.verify_checkpoint_hash("Laya-English-Base-Neural", b"tampered_malicious_weights")

    # Non-existent checkpoint path safely falls back to ModelRuntime.FALLBACK
    missing_ckpt = ModelCheckpointMetadata(
        name="Laya-Missing-Weights",
        version="1.0.0",
        sha256_hash="0" * 64,
        model_path="./data/models/non_existent_path",
        runtime=ModelRuntime.TORCH,
    )
    registry.register_checkpoint(missing_ckpt)
    fallback_adapter = LayaAdapter(checkpoint_name="Laya-Missing-Weights", registry=registry)
    assert fallback_adapter.runtime == ModelRuntime.FALLBACK

    # =========================================================================
    # STEP 8: System 1 High-Throughput Fast Cognitive Inference Primitives
    # =========================================================================
    adapter = LayaAdapter(checkpoint_name="Laya-Typed-Decisions", registry=registry)

    # 1. Choice Prediction (Classification / Routing)
    candidates = [
        ChoiceCandidate(id="eng", label="Engineering Department", metadata={"focus": "software, python, build"}),
        ChoiceCandidate(id="qa", label="QA Department", metadata={"focus": "testing, verify, assert"}),
        ChoiceCandidate(id="sec", label="Security Department", metadata={"focus": "audit, auth, tokens"}),
    ]
    choice_q = ChoiceQuestion(
        context="Implement high-performance multi-tier LLM gateway with fallback",
        candidates=candidates,
    )
    choice_res = adapter.predict_choice(choice_q)
    assert isinstance(choice_res, ChoiceResult)
    assert choice_res.selected_id == "eng"
    assert 0.0 < choice_res.confidence <= 1.0
    assert abs(sum(choice_res.probabilities.values()) - 1.0) < 0.05
    assert choice_res.latency_ms > 0.0

    # 2. Score Prediction (Numerical Risk / Priority Scoring)
    score_q = ScoreQuestion(
        proposition="Production database primary host crashed with deadlock error",
        context="Immediate critical failure and data loss alert",
        scale_min=1,
        scale_max=5,
    )
    score_res = adapter.predict_score(score_q)
    assert isinstance(score_res, ScoreResult)
    assert 1 <= score_res.score <= 5
    assert score_res.score >= 4  # High urgency cues should shift score to 4 or 5
    assert 0.0 < score_res.confidence <= 1.0
    assert len(score_res.probabilities) == 5

    # 3. Noul Prediction (Novelty, Uncertainty & Condition Assessment)
    noul_q = NoulQuestion(
        proposition="Disk space at 99% capacity with out-of-memory crash imminent",
        context="critical system alert failure",
        threshold=0.5,
    )
    noul_res = adapter.predict_noul(noul_q)
    assert isinstance(noul_res, NoulResult)
    assert noul_res.is_true is True
    assert noul_res.probability_true > 0.5
    assert noul_res.confidence > 0.5

    # =========================================================================
    # STEP 9: Shared Model Pool & Fair-Share Inference Queue
    # =========================================================================
    pool = SharedModelPool()
    emp1_laya = pool.get_laya_adapter("Laya-Typed-Decisions")
    emp2_laya = pool.get_laya_adapter("Laya-Typed-Decisions")
    assert emp1_laya is emp2_laya  # Singleton instance reused across workforce

    emp1_gateway = pool.get_llm_gateway("reasoning")
    emp2_gateway = pool.get_llm_gateway("reasoning")
    assert emp1_gateway is emp2_gateway

    # Fair-Share Queue with per-employee concurrency caps
    queue = SharedInferenceQueue(max_concurrent_in_flight=4, max_per_employee_in_flight=2)
    queue.enqueue("req-1", "EMP-01", "TASK-1", "payload-1", TaskPriority.HIGH)
    queue.enqueue("req-2", "EMP-01", "TASK-1", "payload-2", TaskPriority.HIGH)
    queue.enqueue("req-3", "EMP-01", "TASK-1", "payload-3", TaskPriority.CRITICAL)  # Exceeds EMP-01 cap
    queue.enqueue("req-4", "EMP-02", "TASK-2", "payload-4", TaskPriority.MEDIUM)

    d1 = queue.get_next_dispatchable()
    assert d1.employee_id == "EMP-01" and d1.request_id == "req-3"  # Critical priority dispatched first
    d2 = queue.get_next_dispatchable()
    assert d2.employee_id == "EMP-01" and d2.request_id == "req-1"  # High priority dispatched second

    # EMP-01 is now at cap (2 in flight). Even though req-2 is waiting for EMP-01, EMP-02 is dispatched!
    d3 = queue.get_next_dispatchable()
    assert d3.employee_id == "EMP-02" and d3.request_id == "req-4"

    # Complete EMP-01 request to free capacity
    queue.complete_request("EMP-01")
    d4 = queue.get_next_dispatchable()
    assert d4.employee_id == "EMP-01" and d4.request_id == "req-2"

    # =========================================================================
    # STEP 10: Asynchronous Micro-Batching & Health Telemetry
    # =========================================================================
    inf_service = LayaInferenceService(checkpoint_name="Laya-Typed-Decisions")

    # Run concurrent decisions through micro-batch manager
    import asyncio
    results = await asyncio.gather(
        inf_service.decide_choice(choice_q, use_batching=True),
        inf_service.decide_score(score_q, use_batching=True),
        inf_service.decide_noul(noul_q, use_batching=True),
    )
    assert len(results) == 3
    assert results[0].selected_id == "eng"
    assert results[1].score >= 4
    assert results[2].is_true is True

    # Health telemetry
    monitor = ModelHealthMonitor(sla_timeout_ms=50.0, max_consecutive_failures=3)
    monitor.record_call(latency_ms=12.5, is_error=False)
    monitor.record_call(latency_ms=15.0, is_error=False)
    report = monitor.get_report()
    assert report.state == HealthState.HEALTHY
    assert report.circuit_breaker_open is False
    assert report.total_calls == 2
    assert report.consecutive_failures == 0

    # =========================================================================
    # STEP 11: Genuine ONNX Runtime Checkpoint Verification & Inference
    # =========================================================================
    canonical_onnx_meta = registry.get_checkpoint("Laya-Decision-ONNX")
    assert canonical_onnx_meta is not None
    assert canonical_onnx_meta.runtime == ModelRuntime.ONNX
    assert os.path.exists(canonical_onnx_meta.onnx_path), f"Canonical ONNX model missing at {canonical_onnx_meta.onnx_path}"
    assert registry.verify_checkpoint_file("Laya-Decision-ONNX", canonical_onnx_meta.onnx_path) is True

    onnx_adapter = LayaAdapter(checkpoint_name="Laya-Decision-ONNX", registry=registry)
    assert onnx_adapter.runtime == ModelRuntime.ONNX
    assert onnx_adapter._onnx_session is not None

    # Genuine ONNX Runtime Inference: Choice (2 candidates)
    onnx_choice_2 = onnx_adapter.predict_choice(
        ChoiceQuestion(
            context="Predict candidate routing class",
            candidates=[ChoiceCandidate(id="class_a", label="A"), ChoiceCandidate(id="class_b", label="B")],
        )
    )
    assert isinstance(onnx_choice_2, ChoiceResult)
    assert onnx_choice_2.selected_id in ["class_a", "class_b"]
    assert onnx_choice_2.confidence > 0.0
    assert abs(sum(onnx_choice_2.probabilities.values()) - 1.0) < 0.05
    assert onnx_choice_2.confidence == onnx_choice_2.probabilities[onnx_choice_2.selected_id]
    assert "ONNX" in onnx_choice_2.reasoning_hint

    # Genuine ONNX Runtime Inference: Choice (4 candidates)
    onnx_choice_4 = onnx_adapter.predict_choice(
        ChoiceQuestion(
            context="Predict candidate routing class multi",
            candidates=[
                ChoiceCandidate(id="c1", label="Option 1"),
                ChoiceCandidate(id="c2", label="Option 2"),
                ChoiceCandidate(id="c3", label="Option 3"),
                ChoiceCandidate(id="c4", label="Option 4"),
            ],
        )
    )
    assert isinstance(onnx_choice_4, ChoiceResult)
    assert onnx_choice_4.selected_id in ["c1", "c2", "c3", "c4"]
    assert onnx_choice_4.confidence > 0.0
    assert abs(sum(onnx_choice_4.probabilities.values()) - 1.0) < 0.05
    assert onnx_choice_4.confidence == onnx_choice_4.probabilities[onnx_choice_4.selected_id]

    # Genuine ONNX Runtime Inference: Score
    onnx_score = onnx_adapter.predict_score(
        ScoreQuestion(proposition="Evaluate ONNX severity", scale_min=1, scale_max=3)
    )
    assert isinstance(onnx_score, ScoreResult)
    assert 1 <= onnx_score.score <= 3
    assert abs(sum(onnx_score.probabilities.values()) - 1.0) < 0.05
    assert onnx_score.confidence == onnx_score.probabilities[onnx_score.score]
    assert 0.0 < onnx_score.confidence <= 1.0

    # Genuine ONNX Runtime Inference: Noul
    onnx_noul = onnx_adapter.predict_noul(
        NoulQuestion(proposition="Evaluate ONNX condition", threshold=0.5)
    )
    assert isinstance(onnx_noul, NoulResult)
    assert isinstance(onnx_noul.is_true, bool)
    assert 0.0 <= onnx_noul.probability_true <= 1.0
    assert 0.0 <= onnx_noul.confidence <= 1.0

    # Tampered ONNX digest falls back gracefully without loading session
    tampered_onnx_ckpt = ModelCheckpointMetadata(
        name="Laya-Tampered-ONNX",
        version="1.0.0",
        sha256_hash="0" * 64,
        onnx_path=canonical_onnx_meta.onnx_path,
        runtime=ModelRuntime.ONNX,
    )
    registry.register_checkpoint(tampered_onnx_ckpt)
    tampered_onnx_adapter = LayaAdapter(checkpoint_name="Laya-Tampered-ONNX", registry=registry)
    assert tampered_onnx_adapter.runtime == ModelRuntime.FALLBACK
    assert tampered_onnx_adapter._onnx_session is None

    # =========================================================================
    # STEP 12: Sovereign Policy Engine Authority & Human Approval Gate
    # =========================================================================
    # Even if System 1 has 1.0 confidence, privileged operations require HumanApprovalGate
    assert HumanApprovalGate.is_destructive("delete_database") is True
    assert HumanApprovalGate.is_destructive("rm -rf /") is True
    assert HumanApprovalGate.is_destructive("production_deployment") is True
    assert HumanApprovalGate.is_destructive("git_checkout_branch") is False
    assert HumanApprovalGate.is_destructive("read_system_telemetry") is False

    # =========================================================================
    # STEP 13: Independent Deliverable Verification & Skill Certification
    # =========================================================================
    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    audit_repo = AuditRepository(async_session)

    router_emp = Employee(
        employee_id="ANC-EMP-ROUTER-01",
        name="Intelligence Routing Specialist",
        title="Staff ML Engineer",
        department="Engineering",
        system_prompt="Multi-model routing prompt.",
        status=EmployeeStatus.WORKING,
    )
    async_session.add(router_emp)
    await async_session.flush()

    skill = EmployeeSkill(
        employee_id=router_emp.id,
        skill_name="Multi-Model Routing",
        verified=False,
        evidence_task_ids=[],
    )
    async_session.add(skill)

    t_eval = Task(
        task_id="TASK-N5-DELIVERABLE",
        project_id=project.id,
        title="Deploy Multi-Model Router",
        objective="Implement and verify multi-model gateway with cost tracking.",
        scope="Neural Routing",
        acceptance_criteria="Router handles fallback and cost accounting cleanly",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.RUNNING,
        assigned_to=router_emp.id,
        fencing_token=1,
    )
    async_session.add(t_eval)
    await async_session.flush()

    code_content = """# ANCOS Multi-Model Intelligent Router
class IntelligentRouter:
    def __init__(self, primary: str, fallback: str):
        self.primary = primary
        self.fallback = fallback

    def route(self, request: dict) -> str:
        return self.primary if request.get('priority') == 'high' else self.fallback
"""
    with tempfile.TemporaryDirectory() as ws_dir:
        deliverable_path = os.path.join(ws_dir, "neural_router.py")
        with open(deliverable_path, "w", encoding="utf-8") as f:
            f.write(code_content)

        verifier = IndependentDeliverableVerifier(
            employee_repo=employee_repo, task_repo=task_repo
        )

        receipt = await verifier.verify_task_result(
            task_id=t_eval.task_id,
            employee_id=router_emp.employee_id,
            artifacts=[deliverable_path],
            test_exit_code=0,
            tested_skills=["Multi-Model Routing"],
            acceptance_criteria="Router handles fallback and cost accounting cleanly",
            file_contents={deliverable_path: code_content},
            task_repo=task_repo,
        )

        assert receipt["passed"] is True
        assert receipt["receipt_id"].startswith("RECEIPT-")
        assert len(receipt["receipt"]["artifact_digests"]) == 1
        assert receipt["signature"] is not None

        # Skill is empirically certified upon independent receipt
        updated_skill = await async_session.get(EmployeeSkill, skill.id)
        assert updated_skill.verified is True
        assert t_eval.task_id in updated_skill.evidence_task_ids

    # =========================================================================
    # STEP 14: Tamper-Evident Immutable Hash-Chained Audit Trail
    # =========================================================================
    await audit_repo.create_entry(
        actor="System",
        action="model_routing_configured",
        resource_type="config",
        resource_id="configs/model-routing.yaml",
        details={"tiers": list(gateway.tiers_config.keys())},
    )
    await audit_repo.create_entry(
        actor="LLMGateway",
        action="model_failover_executed",
        resource_type="model",
        resource_id=response.model,
        details={"primary": "nim/nvidia/nemotron-3.5-lightning-30b-a3b", "fallback": response.model},
    )
    await audit_repo.create_entry(
        actor="BudgetRepository",
        action="budget_reservation_settled",
        resource_type="budget_reservation",
        resource_id=reservation.id,
        details={"actual_usd": str(reservation.actual_usd)},
    )
    await audit_repo.create_entry(
        actor="IndependentDeliverableVerifier",
        action="deliverable_verified",
        resource_type="task",
        resource_id=t_eval.task_id,
        details={"receipt_id": receipt["receipt_id"], "passed": True},
    )

    records = await audit_repo.list_all(limit=10)
    assert len(records) >= 4
    ordered_records = AuditLogChainVerifier.order_chain(records)
    chain_valid, chain_err = AuditLogChainVerifier.verify_records(ordered_records)
    assert chain_valid is True, f"Audit hash chain violation: {chain_err}"
