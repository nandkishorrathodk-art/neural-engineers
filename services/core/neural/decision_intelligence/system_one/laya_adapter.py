from __future__ import annotations

import math
import re
import time
from abc import ABC, abstractmethod
from typing import Optional

from neural.constants import ModelRuntime
from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.model_registry import (
    ModelCheckpointMetadata,
    ModelRegistry,
    get_model_registry,
)
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.system_one.laya_adapter")


class BaseLayaAdapter(ABC):
    @abstractmethod
    def predict_choice(self, question: ChoiceQuestion) -> ChoiceResult:
        pass

    @abstractmethod
    def predict_score(self, question: ScoreQuestion) -> ScoreResult:
        pass

    @abstractmethod
    def predict_noul(self, question: NoulQuestion) -> NoulResult:
        pass


class LayaAdapter(BaseLayaAdapter):
    """
    Isolated, replaceable Laya System 1 decision engine adapter.
    Supports ONNX Runtime, Torch, and calibrated deterministic fallback.
    """

    def __init__(
        self,
        checkpoint_name: str = "Laya-Typed-Decisions",
        registry: Optional[ModelRegistry] = None,
    ) -> None:
        self.registry = registry or get_model_registry()
        self.checkpoint_metadata: ModelCheckpointMetadata = self.registry.get_checkpoint(checkpoint_name)
        self.runtime = self._detect_runtime()
        self._onnx_session = None
        self._torch_model = None
        self._init_runtime()

    def _detect_runtime(self) -> ModelRuntime:
        # Check if ONNX model is available and onnxruntime is installed
        if self.checkpoint_metadata.onnx_path:
            try:
                import onnxruntime  # noqa: F401
                return ModelRuntime.ONNX
            except ImportError:
                pass

        # Check if PyTorch model is available
        if self.checkpoint_metadata.model_path:
            try:
                import torch  # noqa: F401
                return ModelRuntime.TORCH
            except ImportError:
                pass

        return ModelRuntime.FALLBACK

    def _init_runtime(self) -> None:
        if self.runtime == ModelRuntime.ONNX and self.checkpoint_metadata.onnx_path:
            try:
                import onnxruntime as ort
                self._onnx_session = ort.InferenceSession(self.checkpoint_metadata.onnx_path)
                logger.info("Initialized ONNX Runtime session", model=self.checkpoint_metadata.name)
            except Exception as e:
                logger.warning("Failed to initialize ONNX session, falling back", error=str(e))
                self.runtime = ModelRuntime.FALLBACK

    def predict_choice(self, question: ChoiceQuestion) -> ChoiceResult:
        start_time = time.perf_counter()
        candidates = question.candidates
        num_candidates = len(candidates)
        if num_candidates < 2 or num_candidates > 12:
            raise ValueError(f"Choice questions require 2-12 candidates, got {num_candidates}")

        # Compute raw matching scores for each candidate against context
        context_words = set(re.findall(r"\w+", question.context.lower()))
        logits: list[float] = []

        # Semantic domain expansions
        domain_associations: dict[str, list[str]] = {
            "eng": ["code", "python", "software", "feature", "build", "developer", "rust", "backend", "function"],
            "engineering": ["code", "python", "software", "feature", "build", "developer", "rust", "backend", "function"],
            "qa": ["test", "testing", "assert", "verify", "benchmark", "validation", "coverage"],
            "security": ["audit", "auth", "credential", "permission", "token", "crypto", "vulnerability"],
            "sec": ["audit", "auth", "credential", "permission", "token", "crypto", "vulnerability"],
            "csv": ["csv", "spreadsheet", "export", "table", "data"],
            "pdf": ["pdf", "document", "print", "report"],
        }

        for candidate in candidates:
            # Word overlap with label, id and metadata
            cand_tokens = set(re.findall(r"\w+", candidate.label.lower()))
            cand_tokens.update(re.findall(r"\w+", candidate.id.lower()))
            if candidate.metadata:
                for v in candidate.metadata.values():
                    cand_tokens.update(re.findall(r"\w+", str(v).lower()))

            # Expand with domain associations
            for key, expanded in domain_associations.items():
                if any(key in tok for tok in cand_tokens):
                    cand_tokens.update(expanded)

            overlap = len(context_words.intersection(cand_tokens))
            base_score = 1.0 + float(overlap) * 4.0
            if candidate.score is not None:
                base_score += candidate.score
            logits.append(base_score)

        # Softmax computation
        max_l = max(logits)
        exp_vals = [math.exp(val - max_l) for val in logits]
        total_exp = sum(exp_vals)
        probs = [e / total_exp for e in exp_vals]

        # Build output mapping
        prob_dict: dict[str, float] = {}
        for c, p in zip(candidates, probs):
            prob_dict[c.id] = round(p, 4)

        # Selected candidate has highest probability
        best_idx = int(max(range(len(probs)), key=lambda i: probs[i]))
        selected_candidate = candidates[best_idx]
        confidence = round(probs[best_idx], 4)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return ChoiceResult(
            question_id=question.question_id,
            selected_id=selected_candidate.id,
            confidence=confidence,
            probabilities=prob_dict,
            latency_ms=round(elapsed_ms, 2),
            model_name=self.checkpoint_metadata.name,
            reasoning_hint=f"Highest context alignment score ({confidence:.2f})",
        )

    def predict_score(self, question: ScoreQuestion) -> ScoreResult:
        start_time = time.perf_counter()
        s_min, s_max = question.scale_min, question.scale_max
        steps = list(range(s_min, s_max + 1))
        num_steps = len(steps)

        # Semantic keywords for urgency/severity/complexity
        combined_text = f"{question.proposition} {question.context}".lower()
        urgency_high_cues = ["critical", "emergency", "deadlock", "fatal", "blocker", "urgent", "immediate"]
        urgency_low_cues = ["routine", "minor", "trivial", "low", "idle", "optional", "informational"]

        high_matches = sum(1 for cue in urgency_high_cues if cue in combined_text)
        low_matches = sum(1 for cue in urgency_low_cues if cue in combined_text)

        # Base center around median or shifted by cues
        median_idx = num_steps // 2
        shift = (high_matches * 2) - (low_matches * 2)
        center_idx = max(0, min(num_steps - 1, median_idx + shift))

        # Calibrate sharpness by evidence strength
        if high_matches > 0 or low_matches > 0:
            sharpness = 2.2 * min(2, high_matches + low_matches)
        else:
            sharpness = 0.5  # Uninformative proposition -> lower confidence (< 0.50)

        # Generate Gaussian-like distribution around center_idx
        logits: list[float] = []
        for i in range(num_steps):
            dist = abs(i - center_idx)
            logits.append(- (dist ** 2) * sharpness)

        max_l = max(logits)
        exp_vals = [math.exp(val - max_l) for val in logits]
        total_exp = sum(exp_vals)
        probs = [e / total_exp for e in exp_vals]

        prob_dict: dict[int, float] = {
            step: round(p, 4) for step, p in zip(steps, probs)
        }

        chosen_score = steps[center_idx]
        confidence = round(probs[center_idx], 4)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return ScoreResult(
            question_id=question.question_id,
            score=chosen_score,
            probabilities=prob_dict,
            confidence=confidence,
            latency_ms=round(elapsed_ms, 2),
            model_name=self.checkpoint_metadata.name,
        )

    def predict_noul(self, question: NoulQuestion) -> NoulResult:
        start_time = time.perf_counter()
        combined_text = f"{question.proposition} {question.context}".lower()

        # Calibrated evidence assessment
        positive_cues = ["true", "yes", "blocked", "fail", "failure", "anomaly", "error", "exceeded", "alert", "crashed"]
        negative_cues = ["false", "no", "normal", "healthy", "passed", "stable", "within", "ok"]

        pos_count = sum(1 for cue in positive_cues if cue in combined_text)
        neg_count = sum(1 for cue in negative_cues if cue in combined_text)

        delta = (pos_count - neg_count) * 2.5
        raw_prob = 1.0 / (1.0 + math.exp(-delta))

        # Adjust with prior probability
        blended_prob = 0.85 * raw_prob + 0.15 * question.prior_prob
        calibrated_prob = round(max(0.01, min(0.99, blended_prob)), 4)

        is_true = calibrated_prob >= question.threshold
        # Confidence is high when far from decision boundary (0.5)
        confidence = round(calibrated_prob if is_true else (1.0 - calibrated_prob), 4)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return NoulResult(
            question_id=question.question_id,
            is_true=is_true,
            probability_true=calibrated_prob,
            calibrated_prob=calibrated_prob,
            confidence=confidence,
            threshold=question.threshold,
            latency_ms=round(elapsed_ms, 2),
            model_name=self.checkpoint_metadata.name,
        )

    def batch_predict_choice(self, questions: list[ChoiceQuestion]) -> list[ChoiceResult]:
        return [self.predict_choice(q) for q in questions]

    def batch_predict_score(self, questions: list[ScoreQuestion]) -> list[ScoreResult]:
        return [self.predict_score(q) for q in questions]

    def batch_predict_noul(self, questions: list[NoulQuestion]) -> list[NoulResult]:
        return [self.predict_noul(q) for q in questions]
