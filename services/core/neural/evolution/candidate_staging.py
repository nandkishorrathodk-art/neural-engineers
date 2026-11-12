from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neural.evolution.models import (
    ProtectedPathViolationError,
    is_protected_path,
)
from neural.logger import get_logger

logger = get_logger("ancos.evolution.candidate_staging")

_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class CandidateStagingError(RuntimeError):
    """Raised when an isolated candidate worktree cannot be provisioned or modified safely."""


@dataclass(frozen=True)
class CandidateStagingReceipt:
    proposal_id: str
    branch: str
    path: Path
    base_commit: str
    repository: Path
    receipt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "branch": self.branch,
            "path": str(self.path),
            "base_commit": self.base_commit,
            "repository": str(self.repository),
            "receipt_sha256": self.receipt_sha256,
        }


class SelfEvolutionCandidateManager:
    """
    Candidate Staging & Isolated Worktree Lifecycle Manager for Self-Evolution.
    Strictly provisions isolated candidate branches with canonical naming:
        self_evolution/{proposal_id}
    Ensures:
    1. Candidate worktree paths reside outside the main source checkout.
    2. Zero changes affect the pristine production checkout during candidate development.
    3. Protected Core Files Invariant: any attempt to modify root policy engines,
       human approval gates, or emergency halt latches fails closed immediately.
    """

    def __init__(self, repository: str | Path, managed_root: str | Path) -> None:
        requested_repo = Path(repository).resolve(strict=True)
        try:
            result = subprocess.run(
                ["git", "-C", str(requested_repo), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.repository = Path(result.stdout.strip()).resolve(strict=True)
        except (OSError, subprocess.SubprocessError):
            # Defer the failure to provisioning time so the manager can be
            # constructed against a plain directory (e.g. the interactive REPL
            # launched outside a git checkout). Git-backed operations fail closed.
            self.repository = requested_repo

        self.managed_root = Path(managed_root).resolve()

    def _git(self, *args: str) -> str:
        try:
            res = subprocess.run(
                ["git", "-C", str(self.repository), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return res.stdout
        except subprocess.CalledProcessError as err:
            raise CandidateStagingError(f"Git command failed: {err.stderr.strip()}") from err

    def _worktree_git(self, worktree_path: Path, *args: str) -> str:
        try:
            res = subprocess.run(
                ["git", "-C", str(worktree_path), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return res.stdout
        except subprocess.CalledProcessError as err:
            raise CandidateStagingError(f"Worktree git command failed: {err.stderr.strip()}") from err

    @staticmethod
    def _is_within(candidate: Path, parent: Path) -> bool:
        try:
            candidate.relative_to(parent)
            return True
        except ValueError:
            return False

    def get_candidate_branch_name(self, proposal_id: str) -> str:
        return f"self_evolution/{proposal_id}"

    def provision_candidate_staging(
        self,
        proposal_id: str,
        base_ref: str = "HEAD",
    ) -> CandidateStagingReceipt:
        """
        Creates an isolated candidate branch and worktree for a self-evolution proposal.
        The branch is strictly named: self_evolution/{proposal_id}
        """
        if not _PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise CandidateStagingError(f"proposal_id '{proposal_id}' contains invalid characters")
        if proposal_id.upper() in _WINDOWS_RESERVED:
            raise CandidateStagingError(f"proposal_id '{proposal_id}' is a reserved device name")
        if not base_ref or base_ref.startswith("-") or "\x00" in base_ref:
            raise CandidateStagingError("base_ref is invalid")
        if self._is_within(self.managed_root, self.repository):
            raise CandidateStagingError("managed_root must be outside the source checkout")

        self.managed_root.mkdir(parents=True, exist_ok=True)
        root = self.managed_root.resolve(strict=True)
        if self._is_within(root, self.repository):
            raise CandidateStagingError("managed_root resolves inside the source checkout")

        target = (root / proposal_id).resolve()
        if not self._is_within(target, root):
            raise CandidateStagingError("candidate worktree path escapes managed root")

        receipt_path = root / f"{proposal_id}.receipt.json"
        if target.exists() or receipt_path.exists():
            raise CandidateStagingError(f"Candidate worktree for proposal '{proposal_id}' already exists")

        branch = self.get_candidate_branch_name(proposal_id)
        base_commit = self._git("rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_commit):
            raise CandidateStagingError("git did not return a valid base commit")

        self._git("worktree", "add", "-b", branch, str(target), base_commit)

        record: dict[str, Any] = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "branch": branch,
            "path": str(target),
            "repository": str(self.repository),
            "base_commit": base_commit.lower(),
        }
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        receipt_sha256 = hashlib.sha256(canonical).hexdigest()
        record["receipt_sha256"] = receipt_sha256

        receipt_path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        receipt = CandidateStagingReceipt(
            proposal_id=proposal_id,
            branch=branch,
            path=target,
            base_commit=base_commit.lower(),
            repository=self.repository,
            receipt_sha256=receipt_sha256,
        )
        logger.info(
            "Provisioned isolated self-evolution candidate worktree",
            proposal_id=proposal_id,
            branch=branch,
            path=str(target),
            base_commit=base_commit,
        )
        return receipt

    def verify_candidate_receipt(self, proposal_id: str) -> CandidateStagingReceipt:
        receipt_path = self.managed_root / f"{proposal_id}.receipt.json"
        if not receipt_path.is_file():
            raise CandidateStagingError(f"Receipt missing for proposal '{proposal_id}'")

        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CandidateStagingError(f"Receipt for proposal '{proposal_id}' unreadable") from exc

        stored_sha = payload.get("receipt_sha256")
        expected_payload = dict(payload)
        expected_payload.pop("receipt_sha256", None)
        canonical = json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        computed_sha = hashlib.sha256(canonical).hexdigest()
        if stored_sha != computed_sha:
            raise CandidateStagingError(f"Receipt signature tamper detected for proposal '{proposal_id}'")

        return CandidateStagingReceipt(
            proposal_id=payload["proposal_id"],
            branch=payload["branch"],
            path=Path(payload["path"]),
            base_commit=payload["base_commit"],
            repository=Path(payload["repository"]),
            receipt_sha256=stored_sha,
        )

    def apply_candidate_changes(
        self,
        proposal_id: str,
        files: dict[str, str],
        commit_message: str,
        author_id: str = "SelfEvolutionEngine",
    ) -> str:
        """
        Applies code modifications strictly within the isolated candidate worktree.
        Fails closed IMMEDIATELY with ProtectedPathViolationError if any file in files
        targets a protected core policy, approval gate, or emergency halt latch.
        """
        receipt = self.verify_candidate_receipt(proposal_id)
        worktree_path = receipt.path
        if not worktree_path.is_dir():
            raise CandidateStagingError(f"Candidate worktree directory '{worktree_path}' does not exist")

        # 1. STRICT PROTECTED CORE FILES INVARIANT CHECK
        for rel_path in files:
            if is_protected_path(rel_path):
                raise ProtectedPathViolationError(
                    f"Modification attempt to protected path '{rel_path}' is strictly prohibited. "
                    f"Self-evolution candidate changes cannot touch root policy engines, "
                    f"human approval gates, or emergency halt latches. Failing closed immediately."
                )

        # 2. Write candidate files safely to candidate worktree
        for rel_path, content in files.items():
            dest = (worktree_path / rel_path).resolve()
            if not self._is_within(dest, worktree_path):
                raise CandidateStagingError(f"Target path '{rel_path}' escapes candidate worktree")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        # 3. Commit changes in candidate worktree
        self._worktree_git(worktree_path, "add", "-A")
        self._worktree_git(
            worktree_path,
            "-c", f"user.name={author_id}",
            "-c", f"user.email={author_id.lower()}@ancos.corp",
            "commit",
            "-m", commit_message,
        )
        new_commit = self._worktree_git(worktree_path, "rev-parse", "HEAD").strip()
        logger.info(
            "Committed self-evolution candidate changes",
            proposal_id=proposal_id,
            commit=new_commit,
            author=author_id,
        )
        return new_commit

    def list_changed_files(self, proposal_id: str) -> list[str]:
        receipt = self.verify_candidate_receipt(proposal_id)
        raw = self._worktree_git(
            receipt.path,
            "diff",
            "--name-only",
            f"{receipt.base_commit}..HEAD",
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def cleanup_candidate_worktree(self, proposal_id: str, delete_branch: bool = True) -> None:
        receipt_path = self.managed_root / f"{proposal_id}.receipt.json"
        if not receipt_path.is_file():
            return
        branch = None
        try:
            receipt = self.verify_candidate_receipt(proposal_id)
            target = receipt.path
            branch = receipt.branch
            if target.exists():
                subprocess.run(
                    ["git", "-C", str(self.repository), "worktree", "remove", "--force", str(target)],
                    check=False,
                    capture_output=True,
                )
            if delete_branch and branch:
                subprocess.run(
                    ["git", "-C", str(self.repository), "branch", "-D", branch],
                    check=False,
                    capture_output=True,
                )
        finally:
            if receipt_path.is_file():
                receipt_path.unlink()
