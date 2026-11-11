from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from neural.exceptions import DuplicateStatementError, MeetingPolicyViolationError
from neural.logger import get_logger

logger = get_logger("ancos.meetings.policy")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


DEFAULT_AGENDA_STAGES = [
    "CONVENE",
    "PROGRESS_REPORTS",
    "BLOCKERS",
    "DELIBERATION",
    "DECISIONS",
    "ACTIONS",
    "ADJOURN",
]


@dataclass
class MeetingPolicyConfig:
    """Policy constraints governing boardroom meetings to guarantee deterministic termination."""
    max_turns: int = 20
    per_speaker_token_budget: int = 3000
    max_consecutive_speaker_turns: int = 3
    deadline: datetime | None = None
    agenda_stages: list[str] = field(default_factory=lambda: list(DEFAULT_AGENDA_STAGES))
    decision_threshold: int = 0
    duplicate_similarity_threshold: float = 0.85
    require_all_stages: bool = False


class MeetingPolicyEnforcer:
    """
    Enforces deterministic termination, cost ceilings, and anti-looping rules:
    1. Maximum turns ceiling (prevents endless filibustering).
    2. Per-speaker token budgets (hard token limit per participant).
    3. Meeting deadline (temporal cutoff).
    4. Agenda stages progression (monotonic stage progression).
    5. Speaker timeout (consecutive turns limit).
    6. Duplicate statement / message detection.
    7. Decision threshold enforcement (concludes only when required decisions reached).
    """

    def __init__(self, config: MeetingPolicyConfig | None = None):
        self.config = config or MeetingPolicyConfig()

    @staticmethod
    def _normalize_text(text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
        return " ".join(cleaned.split())

    @classmethod
    def init_policy_state(
        cls,
        agenda: list[str] | None = None,
        config: MeetingPolicyConfig | None = None,
    ) -> dict[str, Any]:
        cfg = config or MeetingPolicyConfig()
        stages = list(cfg.agenda_stages)
        return {
            "current_stage": stages[0] if stages else "CONVENE",
            "stages": stages,
            "completed_stages": [],
            "total_turns": 0,
            "max_turns": cfg.max_turns,
            "speaker_turns": {},
            "speaker_tokens": {},
            "per_speaker_token_budget": cfg.per_speaker_token_budget,
            "last_speaker": None,
            "consecutive_turns": 0,
            "max_consecutive_speaker_turns": cfg.max_consecutive_speaker_turns,
            "deadline": cfg.deadline.isoformat() if cfg.deadline else None,
            "decision_threshold": cfg.decision_threshold,
            "statements_history": [],
            "concluded_cleanly": False,
        }

    def check_turn_allowed(
        self,
        policy_state: dict[str, Any],
        speaker_id: str,
        estimated_tokens: int = 50,
    ) -> None:
        """Validates whether a new turn can proceed under meeting policy constraints."""
        # 1. Temporal deadline check
        dl_str = policy_state.get("deadline")
        if dl_str:
            try:
                dl = datetime.fromisoformat(dl_str.replace("Z", "+00:00"))
                dl_naive = dl.astimezone(UTC).replace(tzinfo=None) if dl.tzinfo else dl
                if utc_now() > dl_naive:
                    raise MeetingPolicyViolationError(
                        f"Meeting deadline ({dl_str}) has expired. Turn rejected; meeting must conclude."
                    )
            except (ValueError, TypeError):
                pass

        # 2. Maximum turns ceiling check
        total_turns = policy_state.get("total_turns", 0)
        max_turns = policy_state.get("max_turns", self.config.max_turns)
        if total_turns >= max_turns:
            raise MeetingPolicyViolationError(
                f"Meeting maximum turns ceiling ({max_turns}) reached. No further speaking turns allowed."
            )

        # 3. Speaker consecutive turns check
        last_speaker = policy_state.get("last_speaker")
        consecutive = policy_state.get("consecutive_turns", 0)
        max_consecutive = policy_state.get(
            "max_consecutive_speaker_turns", self.config.max_consecutive_speaker_turns
        )
        if last_speaker == speaker_id and consecutive >= max_consecutive:
            raise MeetingPolicyViolationError(
                f"Speaker '{speaker_id}' has held the floor for {consecutive} consecutive turns "
                f"(max allowed: {max_consecutive}). Speaking floor must be transitioned."
            )

        # 4. Per-speaker token budget check
        current_tokens = policy_state.get("speaker_tokens", {}).get(speaker_id, 0)
        token_budget = policy_state.get(
            "per_speaker_token_budget", self.config.per_speaker_token_budget
        )
        if current_tokens + estimated_tokens > token_budget:
            raise MeetingPolicyViolationError(
                f"Speaker '{speaker_id}' has exceeded their per-speaker token budget "
                f"({current_tokens + estimated_tokens} > {token_budget})."
            )

    def record_statement(
        self,
        policy_state: dict[str, Any],
        speaker_id: str,
        statement: str,
        tokens: int = 50,
    ) -> dict[str, Any]:
        """
        Validates duplicate statements and updates policy counters.
        """
        self.check_turn_allowed(policy_state, speaker_id, estimated_tokens=tokens)

        # Duplicate statement detection
        norm_statement = self._normalize_text(statement)
        if len(norm_statement) > 10:
            history = policy_state.get("statements_history", [])
            for past in history:
                if past.get("speaker_id") == speaker_id:
                    past_norm = past.get("normalized", "")
                    if past_norm == norm_statement:
                        raise DuplicateStatementError(
                            f"Duplicate statement detected for speaker '{speaker_id}': "
                            "repetitive dialogue is strictly prohibited by meeting policy."
                        )
                    # Check difflib similarity
                    similarity = difflib.SequenceMatcher(None, norm_statement, past_norm).ratio()
                    if similarity >= self.config.duplicate_similarity_threshold:
                        raise DuplicateStatementError(
                            f"Near-duplicate statement detected ({similarity:.2f} similarity) for '{speaker_id}'. "
                            "Repetitive dialogue is prohibited."
                        )

        # Update state
        policy_state["total_turns"] = policy_state.get("total_turns", 0) + 1

        speaker_turns = dict(policy_state.get("speaker_turns", {}))
        speaker_turns[speaker_id] = speaker_turns.get(speaker_id, 0) + 1
        policy_state["speaker_turns"] = speaker_turns

        speaker_tokens = dict(policy_state.get("speaker_tokens", {}))
        speaker_tokens[speaker_id] = speaker_tokens.get(speaker_id, 0) + tokens
        policy_state["speaker_tokens"] = speaker_tokens

        last_speaker = policy_state.get("last_speaker")
        if last_speaker == speaker_id:
            policy_state["consecutive_turns"] = policy_state.get("consecutive_turns", 0) + 1
        else:
            policy_state["last_speaker"] = speaker_id
            policy_state["consecutive_turns"] = 1

        statements_history = list(policy_state.get("statements_history", []))
        statements_history.append({
            "speaker_id": speaker_id,
            "statement": statement,
            "normalized": norm_statement,
            "tokens": tokens,
            "timestamp": utc_now().isoformat(),
        })
        policy_state["statements_history"] = statements_history

        return policy_state

    def advance_agenda_stage(
        self,
        policy_state: dict[str, Any],
        target_stage: str,
    ) -> str:
        """
        Advances the meeting to the next designated agenda stage in monotonic order.
        """
        stages = policy_state.get("stages", list(self.config.agenda_stages))
        current = policy_state.get("current_stage", stages[0])
        target_upper = target_stage.strip().upper()

        if target_upper not in stages:
            raise MeetingPolicyViolationError(
                f"Unknown agenda stage '{target_stage}'. Valid stages: {stages}"
            )

        curr_idx = stages.index(current) if current in stages else -1
        target_idx = stages.index(target_upper)

        if target_idx < curr_idx:
            raise MeetingPolicyViolationError(
                f"Cannot advance agenda backward ('{current}' -> '{target_stage}')."
            )

        if target_idx == curr_idx:
            # Idempotent replay: already at target stage
            return target_upper

        completed = list(policy_state.get("completed_stages", []))
        if current not in completed:
            completed.append(current)

        policy_state["completed_stages"] = completed
        policy_state["current_stage"] = target_upper

        logger.info(
            "Meeting agenda stage advanced",
            previous_stage=current,
            new_stage=target_upper,
        )
        return target_upper

    def check_can_conclude(
        self,
        policy_state: dict[str, Any],
        decisions_count: int,
        require_all_stages: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Verifies that meeting conclusion is based on agenda completion and required decisions,
        not arbitrary premature exit.
        """
        # Decision threshold check
        threshold = policy_state.get("decision_threshold", self.config.decision_threshold)
        if decisions_count < threshold:
            return (
                False,
                f"Meeting requires at least {threshold} decision(s) before conclusion; only {decisions_count} recorded.",
            )

        # Stage progression check
        stages = policy_state.get("stages", list(self.config.agenda_stages))
        completed = set(policy_state.get("completed_stages", []))
        current = policy_state.get("current_stage")

        req_stages = require_all_stages or policy_state.get("require_all_stages", self.config.require_all_stages)
        if req_stages:
            required = set(stages[:-1])  # All except ADJOURN
            missing = required - completed - {current}
            if missing:
                return False, f"Cannot conclude: mandatory agenda stages incomplete ({missing})."

        return True, None
