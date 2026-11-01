from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from neural.constants import CommitteeRole, VoteChoice
from neural.db.models import Employee, Vote


@dataclass
class CommitteeMember:
    member_id: str
    name: str
    role: CommitteeRole
    department: str
    voting_weight: float = 1.0
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "name": self.name,
            "role": self.role.value,
            "department": self.department,
            "voting_weight": self.voting_weight,
            "is_active": self.is_active,
        }


class BoardroomCommittee:
    """
    Boardroom Executive Committee & Department Heads.
    Manages voting membership, quorum calculation, and vote tallying.
    """

    def __init__(self, members: list[CommitteeMember] | None = None):
        self._members: dict[str, CommitteeMember] = {}
        if members:
            for m in members:
                self._members[m.member_id] = m
        else:
            self._load_default_committee()

    def _load_default_committee(self) -> None:
        defaults = [
            CommitteeMember(
                member_id="BossAI",
                name="Central Brain (CEO)",
                role=CommitteeRole.CHAIR,
                department="Executive",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-ENG",
                name="Head of Engineering",
                role=CommitteeRole.HEAD_OF_ENGINEERING,
                department="Engineering",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-PROD",
                name="Head of Product",
                role=CommitteeRole.HEAD_OF_PRODUCT,
                department="Product",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-QA",
                name="Head of Quality Assurance",
                role=CommitteeRole.HEAD_OF_QA,
                department="Quality Assurance",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-SEC",
                name="Head of Information Security",
                role=CommitteeRole.HEAD_OF_SECURITY,
                department="Security",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-INFRA",
                name="Head of Infrastructure & Cloud",
                role=CommitteeRole.HEAD_OF_INFRASTRUCTURE,
                department="Infrastructure",
                voting_weight=1.0,
            ),
            CommitteeMember(
                member_id="HEAD-FIN",
                name="Head of Finance & Operations",
                role=CommitteeRole.HEAD_OF_FINANCE,
                department="Finance",
                voting_weight=1.0,
            ),
        ]
        for d in defaults:
            self._members[d.member_id] = d

    @classmethod
    def from_employees(cls, employees: Sequence[Employee]) -> BoardroomCommittee:
        """
        Dynamically constructs committee from hired workforce plus BossAI chair.
        """
        committee = cls(members=[])
        # Always add BossAI chair
        committee.add_member(
            CommitteeMember(
                member_id="BossAI",
                name="Central Brain (CEO)",
                role=CommitteeRole.CHAIR,
                department="Executive",
                voting_weight=1.0,
            )
        )

        dept_roles: dict[str, CommitteeRole] = {
            "engineering": CommitteeRole.HEAD_OF_ENGINEERING,
            "product": CommitteeRole.HEAD_OF_PRODUCT,
            "quality assurance": CommitteeRole.HEAD_OF_QA,
            "qa": CommitteeRole.HEAD_OF_QA,
            "security": CommitteeRole.HEAD_OF_SECURITY,
            "infosec": CommitteeRole.HEAD_OF_SECURITY,
            "infrastructure": CommitteeRole.HEAD_OF_INFRASTRUCTURE,
            "devops": CommitteeRole.HEAD_OF_INFRASTRUCTURE,
            "finance": CommitteeRole.HEAD_OF_FINANCE,
        }

        # Add employees by department
        seen_depts: set[str] = set()
        for emp in employees:
            dept_key = emp.department.lower().strip()
            role = dept_roles.get(dept_key, CommitteeRole.MEMBER)
            # Higher weight or title for Leads/Principals/Heads
            weight = 1.0
            if any(term in emp.title.lower() for term in ["head", "lead", "principal", "director"]):
                weight = 1.5

            committee.add_member(
                CommitteeMember(
                    member_id=emp.employee_id,
                    name=emp.name,
                    role=role,
                    department=emp.department,
                    voting_weight=weight,
                )
            )
            seen_depts.add(dept_key)

        return committee

    def get_members(self, active_only: bool = True) -> list[CommitteeMember]:
        if active_only:
            return [m for m in self._members.values() if m.is_active]
        return list(self._members.values())

    def get_member(self, member_id: str) -> CommitteeMember | None:
        return self._members.get(member_id)

    def add_member(self, member: CommitteeMember) -> None:
        self._members[member.member_id] = member

    @property
    def total_members(self) -> int:
        return len([m for m in self._members.values() if m.is_active])

    def calculate_quorum(
        self,
        votes_cast: int,
        quorum_threshold: float = 0.5
    ) -> tuple[bool, float]:
        active_count = self.total_members
        if active_count == 0:
            return False, 0.0
        percentage = (votes_cast / active_count) * 100.0
        is_met = (votes_cast / active_count) >= quorum_threshold
        return is_met, round(percentage, 2)

    def tally_votes(
        self,
        votes: Sequence[Vote],
        approval_threshold: float = 0.5,
        quorum_threshold: float = 0.5
    ) -> dict[str, Any]:
        """
        Tallies votes, calculates quorum, and returns structured decision summary.
        """
        total_active = self.total_members

        # Deduplicate votes by voter_id, taking the latest vote if multiple exist
        latest_votes: dict[str, Vote] = {}
        for v in votes:
            if v.voter_id not in latest_votes:
                latest_votes[v.voter_id] = v
            else:
                existing = latest_votes[v.voter_id]
                if v.voted_at and existing.voted_at and v.voted_at >= existing.voted_at:
                    latest_votes[v.voter_id] = v

        # Only count votes from registered active committee members if committee is configured
        valid_votes = list(latest_votes.values())
        if self._members:
            valid_votes = [
                v for v in valid_votes
                if (m := self.get_member(v.voter_id)) is not None and m.is_active
            ]

        votes_cast = len(valid_votes)

        is_quorum_met, quorum_percentage = self.calculate_quorum(
            votes_cast=votes_cast,
            quorum_threshold=quorum_threshold
        )

        aye_count = 0
        nay_count = 0
        abstain_count = 0
        weighted_aye = 0.0
        weighted_nay = 0.0
        weighted_abstain = 0.0

        for v in valid_votes:
            if v.choice == VoteChoice.AYE:
                aye_count += 1
                weighted_aye += v.weight
            elif v.choice == VoteChoice.NAY:
                nay_count += 1
                weighted_nay += v.weight
            elif v.choice == VoteChoice.ABSTAIN:
                abstain_count += 1
                weighted_abstain += v.weight

        total_decisive_weighted = weighted_aye + weighted_nay
        approval_rate = (
            (weighted_aye / total_decisive_weighted) if total_decisive_weighted > 0 else 0.0
        )

        is_approved = False
        rejection_reason = None

        if not is_quorum_met:
            is_approved = False
            rejection_reason = (
                f"Quorum not reached: {quorum_percentage}% participation "
                f"(threshold: {quorum_threshold * 100}%)"
            )
        elif total_decisive_weighted == 0:
            is_approved = False
            rejection_reason = "No decisive votes cast (all abstained)"
        elif (approval_rate > approval_threshold) if approval_threshold == 0.5 else (round(approval_rate, 4) >= round(approval_threshold, 4)):
            is_approved = True
        else:
            is_approved = False
            rejection_reason = (
                f"Approval threshold not met: {round(approval_rate * 100, 1)}% in favor "
                f"(threshold: {approval_threshold * 100}%)"
            )

        return {
            "total_committee_members": total_active,
            "votes_cast": votes_cast,
            "quorum_reached": is_quorum_met,
            "quorum_percentage": quorum_percentage,
            "quorum_threshold_percentage": quorum_threshold * 100.0,
            "aye_count": aye_count,
            "nay_count": nay_count,
            "abstain_count": abstain_count,
            "weighted_aye": round(weighted_aye, 2),
            "weighted_nay": round(weighted_nay, 2),
            "weighted_abstain": round(weighted_abstain, 2),
            "approval_rate": round(approval_rate * 100.0, 1),
            "approval_threshold_percentage": approval_threshold * 100.0,
            "is_approved": is_approved,
            "rejection_reason": rejection_reason,
        }
