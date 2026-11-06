from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorktreeProvisionError(RuntimeError):
    """Raised when an isolated engineering worktree cannot be safely created."""


@dataclass(frozen=True)
class WorktreeReceipt:
    task_id: str
    branch: str
    path: Path
    base_commit: str
    repository: Path
    receipt_sha256: str


class EngineeringWorktreeManager:
    """Create task-scoped branches without changing the source checkout.

    The caller supplies a dedicated managed root. Worktrees are created there
    with a deterministic task branch and a pinned base commit. The source
    checkout's current branch and working tree are never checked out or reset.
    This class intentionally has no merge, push, or cleanup operation; those
    actions require a separate review and approval lifecycle.
    """

    def __init__(self, repository: str | Path, managed_root: str | Path) -> None:
        requested_repository = Path(repository).resolve(strict=True)
        try:
            result = subprocess.run(
                ["git", "-C", str(requested_repository), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise WorktreeProvisionError(str(detail or exc).strip()) from exc
        self.repository = Path(result.stdout.strip()).resolve(strict=True)
        self.managed_root = Path(managed_root).resolve()

    def create(self, task_id: str, base_ref: str = "HEAD") -> WorktreeReceipt:
        if not _TASK_ID_RE.fullmatch(task_id):
            raise WorktreeProvisionError("task_id contains unsupported characters")
        if not base_ref or base_ref.startswith("-") or "\x00" in base_ref:
            raise WorktreeProvisionError("base_ref is invalid")
        if self._is_within(self.managed_root, self.repository):
            raise WorktreeProvisionError("managed_root must be outside the source checkout")

        self.managed_root.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creation to ensure symlinked parents cannot redirect
        # the target into the source checkout.
        root = self.managed_root.resolve(strict=True)
        if self._is_within(root, self.repository):
            raise WorktreeProvisionError("managed_root resolves inside the source checkout")
        target = (root / task_id).resolve()
        if not self._is_within(target, root):
            raise WorktreeProvisionError("worktree path escapes the managed root")
        receipt_path = root / f"{task_id}.receipt.json"
        if target.exists() or receipt_path.exists():
            raise WorktreeProvisionError("worktree path already exists")

        branch = f"ancos/task/{task_id}"
        base_commit = self._git("rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_commit):
            raise WorktreeProvisionError("git did not return a valid base commit")

        self._git("worktree", "add", "-b", branch, str(target), base_commit)
        record: dict[str, Any] = {
            "schema_version": 1,
            "task_id": task_id,
            "branch": branch,
            "path": str(target),
            "repository": str(self.repository),
            "base_commit": base_commit.lower(),
        }
        # Bind a stable provenance record to the task, source checkout, branch,
        # worktree path, and exact base commit. The manifest is outside both
        # Git worktrees so task code cannot accidentally include it in a patch.
        digest = self._receipt_digest(record)
        record["receipt_sha256"] = digest
        try:
            with receipt_path.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
        except OSError as exc:
            # Roll back only the worktree and branch created by this call.
            try:
                self._git("worktree", "remove", "--force", str(target))
                self._git("branch", "-D", branch)
            except WorktreeProvisionError:
                pass
            raise WorktreeProvisionError(f"Could not persist worktree provenance receipt: {exc}") from exc
        return WorktreeReceipt(task_id, branch, target, base_commit.lower(), self.repository, digest)

    def verify_receipt(self, task_id: str) -> WorktreeReceipt:
        """Load persisted provenance and confirm the live Git worktree still matches it."""
        if not _TASK_ID_RE.fullmatch(task_id):
            raise WorktreeProvisionError("task_id contains unsupported characters")
        root = self.managed_root.resolve(strict=True)
        if self._is_within(root, self.repository):
            raise WorktreeProvisionError("managed_root must be outside the source checkout")
        receipt_path = (root / f"{task_id}.receipt.json").resolve(strict=True)
        if not self._is_within(receipt_path, root):
            raise WorktreeProvisionError("worktree receipt escapes the managed root")
        try:
            raw = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorktreeProvisionError("worktree provenance receipt is unreadable") from exc
        if not isinstance(raw, dict):
            raise WorktreeProvisionError("worktree provenance receipt is invalid")
        digest = raw.pop("receipt_sha256", None)
        if not isinstance(digest, str) or digest != self._receipt_digest(raw):
            raise WorktreeProvisionError("worktree provenance receipt integrity check failed")
        expected = {
            "schema_version": 1,
            "task_id": task_id,
            "branch": f"ancos/task/{task_id}",
            "path": str((root / task_id).resolve()),
            "repository": str(self.repository),
        }
        if any(raw.get(key) != value for key, value in expected.items()):
            raise WorktreeProvisionError("worktree provenance receipt identity mismatch")
        base_commit = raw.get("base_commit")
        if not isinstance(base_commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
            raise WorktreeProvisionError("worktree receipt base commit is invalid")
        worktree_path = Path(raw["path"])
        try:
            branch = self._git_at(worktree_path, "branch", "--show-current").strip()
            top_level = Path(self._git_at(worktree_path, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
            head = self._git_at(worktree_path, "rev-parse", "HEAD").strip().lower()
            self._git("merge-base", "--is-ancestor", base_commit, head)
        except WorktreeProvisionError as exc:
            raise WorktreeProvisionError("recorded task worktree is unavailable or no longer valid") from exc
        if branch != raw["branch"] or top_level != worktree_path or not re.fullmatch(r"[0-9a-f]{40,64}", head):
            raise WorktreeProvisionError("recorded task worktree no longer matches its provenance")
        return WorktreeReceipt(task_id, branch, worktree_path, base_commit, self.repository, digest)

    @staticmethod
    def _receipt_digest(record: dict[str, Any]) -> str:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _git(self, *args: str) -> str:
        return self._git_at(self.repository, *args)

    @staticmethod
    def _git_at(path: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise WorktreeProvisionError(str(detail or exc).strip()) from exc
        return result.stdout

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
