from __future__ import annotations

import hashlib
import sys
from types import ModuleType

import pytest
from neural.constants import ModelRuntime
from neural.decision_intelligence.schemas.choice import ChoiceCandidate, ChoiceQuestion
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.decision_intelligence.system_one.model_registry import (
    ModelCheckpointMetadata,
    ModelRegistry,
)


def test_tampered_checkpoint_is_never_loaded(tmp_path):
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "model.safetensors").write_bytes(b"tampered weights")

    registry = ModelRegistry(model_dir=str(tmp_path / "registry"))
    registry.register_checkpoint(
        ModelCheckpointMetadata(
            name="test-laya",
            version="test",
            sha256_hash="0" * 64,
            runtime=ModelRuntime.TORCH,
            model_path=str(checkpoint_dir),
        )
    )

    adapter = LayaAdapter(checkpoint_name="test-laya", registry=registry)

    assert adapter.runtime == ModelRuntime.FALLBACK
    assert adapter._torch_model is None


def _register_onnx_checkpoint(tmp_path, *, contents: bytes, digest: str | None = None) -> ModelRegistry:
    model_path = tmp_path / "decision.onnx"
    model_path.write_bytes(contents)
    registry = ModelRegistry(model_dir=str(tmp_path / "registry"))
    registry.register_checkpoint(
        ModelCheckpointMetadata(
            name="test-onnx",
            version="test",
            sha256_hash=digest if digest is not None else hashlib.sha256(contents).hexdigest(),
            runtime=ModelRuntime.ONNX,
            onnx_path=str(model_path),
        )
    )
    return registry


def _install_onnxruntime(monkeypatch, *, loaded_paths: list[str]) -> None:
    fake_runtime = ModuleType("onnxruntime")

    def inference_session(path: str):
        loaded_paths.append(path)
        return object()

    fake_runtime.InferenceSession = inference_session
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_runtime)


def test_valid_onnx_checkpoint_is_verified_before_session_load(tmp_path, monkeypatch):
    contents = b"pinned onnx checkpoint"
    registry = _register_onnx_checkpoint(tmp_path, contents=contents)
    loaded_paths: list[str] = []
    _install_onnxruntime(monkeypatch, loaded_paths=loaded_paths)

    adapter = LayaAdapter(checkpoint_name="test-onnx", registry=registry)

    assert adapter.runtime == ModelRuntime.ONNX
    assert adapter._onnx_session is not None
    assert loaded_paths == [registry.get_checkpoint("test-onnx").onnx_path]


def test_missing_onnx_checkpoint_falls_back_without_session_load(tmp_path, monkeypatch):
    registry = _register_onnx_checkpoint(tmp_path, contents=b"pinned onnx checkpoint")
    metadata = registry.get_checkpoint("test-onnx").model_copy(
        update={"onnx_path": str(tmp_path / "missing.onnx")}
    )
    registry.register_checkpoint(metadata)
    loaded_paths: list[str] = []
    _install_onnxruntime(monkeypatch, loaded_paths=loaded_paths)

    adapter = LayaAdapter(
        checkpoint_name="test-onnx",
        registry=registry,
        runtime=ModelRuntime.ONNX,
    )

    assert adapter.runtime == ModelRuntime.FALLBACK
    assert adapter._onnx_session is None
    assert loaded_paths == []


@pytest.mark.parametrize("configured_digest", ["0" * 64, ""])
def test_tampered_or_unpinned_onnx_checkpoint_falls_back_without_loading(
    tmp_path,
    monkeypatch,
    configured_digest,
):
    registry = _register_onnx_checkpoint(
        tmp_path,
        contents=b"changed or unpinned onnx checkpoint",
        digest=configured_digest,
    )
    loaded_paths: list[str] = []
    _install_onnxruntime(monkeypatch, loaded_paths=loaded_paths)

    adapter = LayaAdapter(checkpoint_name="test-onnx", registry=registry)

    assert adapter.runtime == ModelRuntime.FALLBACK
    assert adapter._onnx_session is None
    assert loaded_paths == []
    result = adapter.predict_choice(
        ChoiceQuestion(
            context="Need Python engineering implementation",
            candidates=[
                ChoiceCandidate(id="engineering", label="Engineering"),
                ChoiceCandidate(id="research", label="Research"),
            ],
        )
    )
    assert result.selected_id == "engineering"
