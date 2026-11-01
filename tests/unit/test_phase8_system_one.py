from __future__ import annotations

import asyncio

import pytest
from neural.decision_intelligence.schemas.choice import (
    ChoiceCandidate,
    ChoiceQuestion,
    ChoiceResult,
)
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.batch_manager import MicroBatchManager
from neural.decision_intelligence.system_one.contracts import (
    DecisionPrimitive,
    System1DecisionRequest,
)
from neural.decision_intelligence.system_one.inference_service import (
    LayaInferenceService,
    reset_inference_service,
)
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.decision_intelligence.system_one.model_registry import (
    ModelRegistry,
    reset_model_registry,
)
from neural.exceptions import ModelCheckpointError


@pytest.fixture(autouse=True)
def cleanup_singletons():
    reset_model_registry()
    reset_inference_service()
    yield
    reset_model_registry()
    reset_inference_service()


def test_choice_schema_validation():
    # Valid candidate set (3 candidates)
    cands = [
        ChoiceCandidate(id="c1", label="Option 1"),
        ChoiceCandidate(id="c2", label="Option 2"),
        ChoiceCandidate(id="c3", label="Option 3"),
    ]
    q = ChoiceQuestion(context="Choose between options", candidates=cands)
    assert len(q.candidates) == 3

    # Rejection of < 2 candidates
    with pytest.raises(ValueError, match="at least 2"):
        ChoiceQuestion(context="test", candidates=[cands[0]])

    # Rejection of > 12 candidates
    many_cands = [ChoiceCandidate(id=f"c_{i}", label=f"Option {i}") for i in range(13)]
    with pytest.raises(ValueError, match="strictly capped at 12"):
        ChoiceQuestion(context="test", candidates=many_cands)

    # Rejection of duplicate candidate IDs
    dup_cands = [
        ChoiceCandidate(id="c1", label="Option 1"),
        ChoiceCandidate(id="c1", label="Option 2"),
    ]
    with pytest.raises(ValueError, match="unique"):
        ChoiceQuestion(context="test", candidates=dup_cands)


def test_score_schema_validation():
    q = ScoreQuestion(proposition="Rate system load", scale_min=1, scale_max=5)
    assert q.scale_max == 5

    with pytest.raises(ValueError, match="greater than scale_min"):
        ScoreQuestion(proposition="test", scale_min=5, scale_max=3)


def test_noul_schema_validation():
    q = NoulQuestion(proposition="Is disk full?", prior_prob=0.2, threshold=0.6)
    assert q.threshold == 0.6
    assert q.prior_prob == 0.2


def test_model_registry_and_hash_verification():
    reg = ModelRegistry()
    checkpoints = reg.list_checkpoints()
    assert len(checkpoints) >= 3

    typed_ckpt = reg.get_checkpoint("Laya-Typed-Decisions")
    assert typed_ckpt.name == "Laya-Typed-Decisions"
    assert typed_ckpt.target_latency_ms <= 40.0

    # Cryptographic hash verification check
    test_content = b""  # Empty content hash matches Laya-English-Base pinned hash
    eng_ckpt = reg.get_checkpoint("Laya-English-Base")
    assert eng_ckpt.name == "Laya-English-Base"
    assert reg.verify_checkpoint_hash("Laya-English-Base", test_content) is True

    # Tampered content should raise ModelCheckpointError
    tampered_content = b"tampered_weights_corrupt"
    with pytest.raises(ModelCheckpointError, match="hash verification failed"):
        reg.verify_checkpoint_hash("Laya-English-Base", tampered_content)


def test_laya_adapter_choice_prediction():
    adapter = LayaAdapter(checkpoint_name="Laya-Typed-Decisions")
    cands = [
        ChoiceCandidate(id="route_eng", label="Engineering Department"),
        ChoiceCandidate(id="route_sec", label="Security Department"),
        ChoiceCandidate(id="route_qa", label="QA Department"),
    ]
    q = ChoiceQuestion(
        context="Write Python unit tests for the authentication API",
        candidates=cands,
    )
    result = adapter.predict_choice(q)

    assert isinstance(result, ChoiceResult)
    assert result.question_id == q.question_id
    assert result.selected_id in ["route_eng", "route_qa"]
    assert 0.0 <= result.confidence <= 1.0
    # Softmax probabilities should sum close to 1.0
    assert abs(sum(result.probabilities.values()) - 1.0) < 0.05
    assert result.latency_ms < 50.0  # Fast inference budget


def test_laya_adapter_score_prediction():
    adapter = LayaAdapter(checkpoint_name="Laya-Typed-Decisions")
    q = ScoreQuestion(
        proposition="Production database primary host crashed with deadlock error",
        context="Immediate critical failure",
        scale_min=1,
        scale_max=5,
    )
    result = adapter.predict_score(q)

    assert isinstance(result, ScoreResult)
    assert result.question_id == q.question_id
    assert result.score in [4, 5]  # Critical error cues
    assert 0.0 <= result.confidence <= 1.0
    assert abs(sum(result.probabilities.values()) - 1.0) < 0.05


def test_laya_adapter_noul_prediction():
    adapter = LayaAdapter(checkpoint_name="Laya-Typed-Decisions")
    q_blocked = NoulQuestion(
        proposition="Execution process crashed with out-of-memory error",
        context="blocked failed alert",
        threshold=0.5,
    )
    result_blocked = adapter.predict_noul(q_blocked)
    assert isinstance(result_blocked, NoulResult)
    assert result_blocked.is_true is True
    assert result_blocked.probability_true > 0.5

    q_healthy = NoulQuestion(
        proposition="Service healthcheck passed and verified memory stable",
        context="healthy ok passed normal",
        threshold=0.5,
    )
    result_healthy = adapter.predict_noul(q_healthy)
    assert result_healthy.is_true is False
    assert result_healthy.probability_true < 0.5


@pytest.mark.asyncio
async def test_micro_batch_manager_coalescing():
    adapter = LayaAdapter(checkpoint_name="Laya-Typed-Decisions")
    batch_mgr = MicroBatchManager(adapter=adapter, batch_window_ms=10, max_batch_size=8)
    await batch_mgr.start()

    try:
        # Submit 4 concurrent choice questions
        cands = [
            ChoiceCandidate(id="opt_a", label="Option Alpha"),
            ChoiceCandidate(id="opt_b", label="Option Beta"),
        ]
        questions = [
            ChoiceQuestion(context=f"Context query {i}", candidates=cands)
            for i in range(4)
        ]

        tasks = [batch_mgr.enqueue_choice(q) for q in questions]
        results = await asyncio.gather(*tasks)

        assert len(results) == 4
        for r in results:
            assert isinstance(r, ChoiceResult)
            assert r.selected_id in ["opt_a", "opt_b"]
    finally:
        await batch_mgr.stop()


@pytest.mark.asyncio
async def test_inference_service_lifecycle_and_health():
    service = LayaInferenceService()
    health = service.get_health()

    assert health["status"] == "healthy"
    assert health["total_requests"] == 0

    cands = [
        ChoiceCandidate(id="a", label="Alpha"),
        ChoiceCandidate(id="b", label="Beta"),
    ]
    q = ChoiceQuestion(context="Select between Alpha and Beta", candidates=cands)

    res = await service.decide_choice(q, use_batching=False)
    assert res.selected_id in ["a", "b"]

    updated_health = service.get_health()
    assert updated_health["total_requests"] == 1
    assert updated_health["error_rate"] == 0.0

    # Polymorphic decide test
    req = System1DecisionRequest(
        primitive=DecisionPrimitive.CHOICE,
        choice_question=q,
    )
    poly_res = await service.decide(req, use_batching=False)
    assert poly_res.primitive == DecisionPrimitive.CHOICE
    assert poly_res.choice_result is not None

    await service.shutdown()


def test_model_registry_file_verification(tmp_path):
    reg = ModelRegistry()
    test_file = tmp_path / "model.bin"
    test_file.write_bytes(b"")

    # Empty file matches Laya-English-Base SHA-256
    assert reg.verify_checkpoint_file("Laya-English-Base", str(test_file)) is True

    # Tampered file must fail
    test_file.write_bytes(b"corrupt_weights")
    with pytest.raises(ModelCheckpointError, match="hash verification failed"):
        reg.verify_checkpoint_file("Laya-English-Base", str(test_file))

    # Missing file must fail
    with pytest.raises(ModelCheckpointError, match="does not exist"):
        reg.verify_checkpoint_file("Laya-English-Base", str(tmp_path / "nonexistent.bin"))


@pytest.mark.asyncio
async def test_batch_manager_size_mismatch_error():
    from unittest.mock import MagicMock

    from neural.exceptions import BatchManagerError

    adapter = LayaAdapter()
    # Mock batch_predict_choice returning empty list (mismatch)
    adapter.batch_predict_choice = MagicMock(return_value=[])

    batch_mgr = MicroBatchManager(adapter=adapter, batch_window_ms=5)
    await batch_mgr.start()
    try:
        q = ChoiceQuestion(
            context="Test mismatch",
            candidates=[ChoiceCandidate(id="1", label="1"), ChoiceCandidate(id="2", label="2")],
        )
        with pytest.raises(BatchManagerError, match="Choice batch size mismatch"):
            await batch_mgr.enqueue_choice(q)
    finally:
        await batch_mgr.stop()
