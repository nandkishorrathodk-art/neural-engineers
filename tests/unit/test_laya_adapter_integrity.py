from __future__ import annotations

from neural.constants import ModelRuntime
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
