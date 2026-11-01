from __future__ import annotations

import hashlib
import os
from typing import Optional

from pydantic import BaseModel, Field

from neural.constants import ModelRuntime
from neural.exceptions import ModelCheckpointError
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.system_one.model_registry")


class ModelCheckpointMetadata(BaseModel):
    name: str
    version: str
    task_types: list[str] = Field(default_factory=list)
    sha256_hash: str
    runtime: ModelRuntime = ModelRuntime.FALLBACK
    model_path: Optional[str] = None
    onnx_path: Optional[str] = None
    target_latency_ms: float = 35.0
    input_modality: str = "structured_json"
    description: str = ""


# Canonical default metadata pinned by commit / hash
DEFAULT_CHECKPOINTS: dict[str, ModelCheckpointMetadata] = {
    "Laya-English-Base": ModelCheckpointMetadata(
        name="Laya-English-Base",
        version="1.0.0",
        task_types=["routing", "choice"],
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        runtime=ModelRuntime.FALLBACK,
        target_latency_ms=30.0,
        input_modality="structured_prompt_text",
        description="Fast English-only routing and choice selection (25ms - 35ms)",
    ),
    "Laya-Multilingual": ModelCheckpointMetadata(
        name="Laya-Multilingual",
        version="1.0.0",
        task_types=["routing", "choice", "multilingual"],
        sha256_hash="4a5a54e5658cf7db1c3a772f917540203f5ce6240d0246a4e32047d9fed53d7e",
        runtime=ModelRuntime.FALLBACK,
        target_latency_ms=40.0,
        input_modality="multilingual_text",
        description="Multilingual (English/Hindi) decision router (35ms - 45ms)",
    ),
    "Laya-Typed-Decisions": ModelCheckpointMetadata(
        name="Laya-Typed-Decisions",
        version="1.2.0",
        task_types=["choice", "score", "noul", "gui_selection"],
        sha256_hash="8a619b02a1d2f3e8f81a7b45c229068019b841793740cfcf7e52a9a7a67f08c3",
        runtime=ModelRuntime.FALLBACK,
        target_latency_ms=34.0,
        input_modality="strict_json",
        description="Specialized typed decision engine: Choice, Score & Noul (30ms - 38ms)",
    ),
    # Genuine Neural Checkpoints (Official convaiinnovations/laya)
    "Laya-English-Base-Neural": ModelCheckpointMetadata(
        name="Laya-English-Base-Neural",
        version="1.0.0",
        task_types=["routing", "choice"],
        sha256_hash="891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c",
        model_path="./data/models/laya/base",
        runtime=ModelRuntime.TORCH,
        target_latency_ms=35.0,
        input_modality="neural_sequence",
        description="Official Laya ModernBERT neural decision engine (English Base)",
    ),
    "Laya-Multilingual-Neural": ModelCheckpointMetadata(
        name="Laya-Multilingual-Neural",
        version="1.0.0",
        task_types=["routing", "choice", "multilingual"],
        sha256_hash="9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204",
        model_path="./data/models/laya/multilingual",
        runtime=ModelRuntime.TORCH,
        target_latency_ms=45.0,
        input_modality="multilingual_neural_sequence",
        description="Official Laya ModernBERT neural decision engine (Multilingual)",
    ),
    "Laya-Typed-Decisions-Neural": ModelCheckpointMetadata(
        name="Laya-Typed-Decisions-Neural",
        version="1.2.0",
        task_types=["choice", "score", "noul", "gui_selection"],
        sha256_hash="891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c",
        model_path="./data/models/laya/base",
        runtime=ModelRuntime.TORCH,
        target_latency_ms=38.0,
        input_modality="neural_sequence",
        description="Official Laya ModernBERT neural decision engine for Choice, Score & Noul",
    ),
}


class ModelRegistry:
    def __init__(self, model_dir: str = "./data/models/laya") -> None:
        self.model_dir = model_dir
        self._checkpoints: dict[str, ModelCheckpointMetadata] = dict(DEFAULT_CHECKPOINTS)
        os.makedirs(self.model_dir, exist_ok=True)

    def register_checkpoint(self, metadata: ModelCheckpointMetadata) -> None:
        self._checkpoints[metadata.name] = metadata
        logger.info("Registered model checkpoint", name=metadata.name, version=metadata.version)

    def get_checkpoint(self, name: str) -> ModelCheckpointMetadata:
        if name not in self._checkpoints:
            raise ModelCheckpointError(f"Model checkpoint '{name}' not found in registry")
        return self._checkpoints[name]

    def list_checkpoints(self) -> list[ModelCheckpointMetadata]:
        return list(self._checkpoints.values())

    def verify_checkpoint_hash(self, name: str, artifact_bytes: bytes) -> bool:
        ckpt = self.get_checkpoint(name)
        computed_hash = hashlib.sha256(artifact_bytes).hexdigest()
        if computed_hash != ckpt.sha256_hash:
            raise ModelCheckpointError(
                f"Cryptographic hash verification failed for '{name}': "
                f"expected {ckpt.sha256_hash}, computed {computed_hash}"
            )
        return True

    def verify_checkpoint_file(self, name: str, file_path: str) -> bool:
        ckpt = self.get_checkpoint(name)
        if not os.path.exists(file_path):
            raise ModelCheckpointError(f"Model file '{file_path}' does not exist for checkpoint '{name}'")
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        computed_hash = hasher.hexdigest()
        if computed_hash != ckpt.sha256_hash:
            raise ModelCheckpointError(
                f"Cryptographic hash verification failed for '{name}' at '{file_path}': "
                f"expected {ckpt.sha256_hash}, computed {computed_hash}"
            )
        return True

    def select_checkpoint_for_task(self, task_type: str, language: str = "en") -> ModelCheckpointMetadata:
        if language != "en" and "Laya-Multilingual" in self._checkpoints:
            return self._checkpoints["Laya-Multilingual"]
        if task_type in ["score", "noul", "gui_selection"] and "Laya-Typed-Decisions" in self._checkpoints:
            return self._checkpoints["Laya-Typed-Decisions"]
        if "Laya-Typed-Decisions" in self._checkpoints:
            return self._checkpoints["Laya-Typed-Decisions"]
        return list(self._checkpoints.values())[0]


_registry_instance: Optional[ModelRegistry] = None


def get_model_registry() -> ModelRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ModelRegistry()
    return _registry_instance


def reset_model_registry() -> None:
    global _registry_instance
    _registry_instance = None
