from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorktreeProvisionError(RuntimeError):
    """Raised when an isolated engineering worktree cannot be safely created."""


@dataclass(frozen=True)
class WorktreeReceipt:
    task_id: str
    branch: str
    path: Path
    base_commit: str


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
        if target.exists():
            raise WorktreeProvisionError("worktree path already exists")

        branch = f"ancos/task/{task_id}"
        base_commit = self._git("rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_commit):
            raise WorktreeProvisionError("git did not return a valid base commit")

        self._git("worktree", "add", "-b", branch, str(target), base_commit)
        return WorktreeReceipt(
            task_id=task_id,
            branch=branch,
            path=target,
            base_commit=base_commit.lower(),
        )

    def _git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository), *args],
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
