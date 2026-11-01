from typing import ClassVar

from neural.constants import ProposalStatus


class ProposalStateMachine:
    """
    Deterministic State Machine for Corporate Governance Proposals.
    Enforces valid transitions:
    DRAFT -> DISCUSSION / CANCELLED / VETOED
    DISCUSSION -> VOTING / CANCELLED / VETOED
    VOTING -> APPROVED / REJECTED / VETOED
    APPROVED -> EXECUTED / VETOED
    """

    ALLOWED_TRANSITIONS: ClassVar[dict[ProposalStatus, set[ProposalStatus]]] = {
        ProposalStatus.DRAFT: {
            ProposalStatus.DISCUSSION,
            ProposalStatus.CANCELLED,
            ProposalStatus.VETOED,
        },
        ProposalStatus.DISCUSSION: {
            ProposalStatus.VOTING,
            ProposalStatus.CANCELLED,
            ProposalStatus.VETOED,
        },
        ProposalStatus.VOTING: {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.VETOED,
            ProposalStatus.CANCELLED,
        },
        ProposalStatus.APPROVED: {
            ProposalStatus.EXECUTED,
            ProposalStatus.VETOED,
        },
        ProposalStatus.REJECTED: set(),
        ProposalStatus.VETOED: set(),
        ProposalStatus.EXECUTED: set(),
        ProposalStatus.CANCELLED: set(),
    }

    TERMINAL_STATES: ClassVar[set[ProposalStatus]] = {
        ProposalStatus.REJECTED,
        ProposalStatus.VETOED,
        ProposalStatus.EXECUTED,
        ProposalStatus.CANCELLED,
    }

    @classmethod
    def can_transition(cls, current: ProposalStatus, target: ProposalStatus) -> bool:
        if current == target:
            return True
        return target in cls.ALLOWED_TRANSITIONS.get(current, set())

    @classmethod
    def transition(cls, current: ProposalStatus, target: ProposalStatus) -> ProposalStatus:
        if current == target:
            return target
        if not cls.can_transition(current, target):
            raise ValueError(
                f"Illegal proposal state transition from '{current.value}' to '{target.value}'. "
                f"Allowed target states: {[s.value for s in cls.ALLOWED_TRANSITIONS.get(current, set())]}"
            )
        return target

    @classmethod
    def is_terminal(cls, status: ProposalStatus) -> bool:
        return status in cls.TERMINAL_STATES

    @classmethod
    def can_vote(cls, status: ProposalStatus) -> bool:
        return status == ProposalStatus.VOTING

    @classmethod
    def can_deliberate(cls, status: ProposalStatus) -> bool:
        return status in {ProposalStatus.DRAFT, ProposalStatus.DISCUSSION}
