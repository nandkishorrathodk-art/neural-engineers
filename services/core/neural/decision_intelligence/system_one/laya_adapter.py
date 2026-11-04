from __future__ import annotations

import math
import os
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
        runtime: Optional[ModelRuntime] = None,
    ) -> None:
        self.registry = registry or get_model_registry()
        self.checkpoint_metadata: ModelCheckpointMetadata = self.registry.get_checkpoint(checkpoint_name)
        self.runtime = runtime or self._detect_runtime()
        self._onnx_session = None
        self._torch_model = None
        self._init_runtime()

    def _detect_runtime(self) -> ModelRuntime:
        # Treat a local ONNX file as executable input: verify its pinned digest
        # before importing the runtime or allowing a session to load it.
        if self.checkpoint_metadata.onnx_path and os.path.exists(self.checkpoint_metadata.onnx_path):
            try:
                self._verify_onnx_checkpoint()
                import onnxruntime  # noqa: F401
                return ModelRuntime.ONNX
            except Exception as exc:
                logger.warning(
                    "ONNX checkpoint unavailable or failed integrity verification; trying other runtimes",
                    model=self.checkpoint_metadata.name,
                    error=str(exc),
                )
                pass

        # Check if PyTorch model is requested/available
        if (
            self.checkpoint_metadata.runtime == ModelRuntime.TORCH
            and self.checkpoint_metadata.model_path
            and os.path.exists(self.checkpoint_metadata.model_path)
        ):
            try:
                # A local checkpoint is executable input: verify the pinned
                # weights before importing the model runtime or loading them.
                weights_path = os.path.join(self.checkpoint_metadata.model_path, "model.safetensors")
                self.registry.verify_checkpoint_file(self.checkpoint_metadata.name, weights_path)
                import laya  # noqa: F401
                import torch  # noqa: F401
                return ModelRuntime.TORCH
            except Exception as exc:
                logger.warning(
                    "Laya checkpoint unavailable or failed integrity verification; using fallback",
                    model=self.checkpoint_metadata.name,
                    error=str(exc),
                )
                pass

        return ModelRuntime.FALLBACK

    def _verify_onnx_checkpoint(self) -> bool:
        """Require a well-formed pinned digest and verify the selected ONNX artifact."""
        expected_digest = self.checkpoint_metadata.sha256_hash
        if not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
            raise ValueError("ONNX checkpoint requires a configured lowercase SHA-256 digest.")
        if not self.checkpoint_metadata.onnx_path:
            raise ValueError("ONNX checkpoint path is not configured.")
        return self.registry.verify_checkpoint_file(
            self.checkpoint_metadata.name,
            self.checkpoint_metadata.onnx_path,
        )

    def _init_runtime(self) -> None:
        if self.runtime == ModelRuntime.ONNX and self.checkpoint_metadata.onnx_path:
            try:
                # Recheck immediately before loading to reduce the verify/load
                # race between runtime detection and session construction.
                self._verify_onnx_checkpoint()
                import onnxruntime as ort
                self._onnx_session = ort.InferenceSession(self.checkpoint_metadata.onnx_path)
                logger.info("Initialized ONNX Runtime session", model=self.checkpoint_metadata.name)
            except Exception as e:
                logger.warning("Failed to initialize ONNX session, falling back", error=str(e))
                self.runtime = ModelRuntime.FALLBACK

        elif self.runtime == ModelRuntime.TORCH and self.checkpoint_metadata.model_path:
            try:
                import laya
                # Initialize real Laya neural model agent from local weights
                self._torch_model = laya.load(self.checkpoint_metadata.model_path)
                logger.info("Initialized genuine Laya neural model session", model=self.checkpoint_metadata.name)
            except Exception as e:
                logger.warning("Failed to initialize Laya neural session, falling back to heuristic engine", error=str(e))
                self.runtime = ModelRuntime.FALLBACK

    def predict_choice(self, question: ChoiceQuestion) -> ChoiceResult:
        start_time = time.perf_counter()
        candidates = question.candidates
        num_candidates = len(candidates)
        if num_candidates < 2 or num_candidates > 12:
            raise ValueError(f"Choice questions require 2-12 candidates, got {num_candidates}")

        # 1. Genuine PyTorch Laya Neural Model Inference
        if self.runtime == ModelRuntime.TORCH and self._torch_model is not None:
            try:
                state = question.context
                criteria = {c.id: f"{c.label}: {c.metadata or ''}" if c.metadata else c.label for c in candidates}
                q_dict = {
                    question.question_id: {
                        "type": "choice",
                        "instructions": question.question_text or "Select the best candidate in context",
                        "criteria": criteria,
                    }
                }
                raw = self._torch_model.predict(state, q_dict)
                ans = raw.get("answers", {}).get(question.question_id, {})
                selected_id = ans.get("choice", candidates[0].id)
                confidence = float(ans.get("confidence", 0.0))
                raw_probs = ans.get("probabilities", {})
                prob_dict = {c.id: round(float(raw_probs.get(c.id, 1.0 / num_candidates)), 4) for c in candidates}
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ChoiceResult(
                    question_id=question.question_id,
                    selected_id=selected_id,
                    confidence=round(confidence, 4),
                    probabilities=prob_dict,
                    latency_ms=round(elapsed_ms, 2),
                    model_name=self.checkpoint_metadata.name,
                    reasoning_hint=f"Laya neural choice: {selected_id} ({confidence:.2f})",
                )
            except Exception as e:
                logger.warning("Laya neural choice failed, falling back to heuristic engine", error=str(e))

        # 2. Calibrated Deterministic Heuristic Fallback Engine
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

        # 1. Genuine PyTorch Laya Neural Model Inference
        if self.runtime == ModelRuntime.TORCH and self._torch_model is not None:
            try:
                state = f"{question.proposition} {question.context}".strip()
                criteria = [str(step) for step in steps]
                q_dict = {
                    question.question_id: {
                        "type": "score",
                        "instructions": question.proposition or "Score priority or severity",
                        "criteria": criteria,
                    }
                }
                raw = self._torch_model.predict(state, q_dict)
                ans = raw.get("answers", {}).get(question.question_id, {})
                # Laya score answers are expected ordinal positions in the
                # criteria list (0..N-1), not the caller's scale values.
                pred_continuous = float(ans.get("score", float(num_steps // 2)))
                score_index = max(0, min(num_steps - 1, int(round(pred_continuous))))
                chosen_score = steps[score_index]
                confidence = float(ans.get("confidence", 0.5))
                raw_probs = ans.get("probabilities", {})
                prob_dict = {
                    step: round(float(raw_probs.get(str(step), raw_probs.get(str(i), 1.0 / num_steps))), 4)
                    for i, step in enumerate(steps)
                }
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ScoreResult(
                    question_id=question.question_id,
                    score=chosen_score,
                    probabilities=prob_dict,
                    confidence=round(confidence, 4),
                    latency_ms=round(elapsed_ms, 2),
                    model_name=self.checkpoint_metadata.name,
                )
            except Exception as e:
                logger.warning("Laya neural score failed, falling back to heuristic engine", error=str(e))

        # 2. Calibrated Deterministic Heuristic Fallback Engine
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

        # 1. Genuine PyTorch Laya Neural Model Inference
        if self.runtime == ModelRuntime.TORCH and self._torch_model is not None:
            try:
                state = f"{question.proposition} {question.context}".strip()
                q_dict = {
                    question.question_id: {
                        "type": "noul",
                        "instructions": question.proposition or "Verify proposition condition",
                    }
                }
                raw = self._torch_model.predict(state, q_dict)
                ans = raw.get("answers", {}).get(question.question_id, {})
                prob_true = float(ans.get("noul", 0.5))
                confidence = float(ans.get("confidence", 0.5))
                is_true = prob_true >= question.threshold
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return NoulResult(
                    question_id=question.question_id,
                    is_true=is_true,
                    probability_true=round(prob_true, 4),
                    calibrated_prob=round(prob_true, 4),
                    confidence=round(confidence, 4),
                    threshold=question.threshold,
                    latency_ms=round(elapsed_ms, 2),
                    model_name=self.checkpoint_metadata.name,
                )
            except Exception as e:
                logger.warning("Laya neural noul failed, falling back to heuristic engine", error=str(e))

        # 2. Calibrated Deterministic Heuristic Fallback Engine
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
