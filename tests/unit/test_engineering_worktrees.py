from __future__ import annotations

import subprocess

import pytest
from neural.engineering.worktrees import EngineeringWorktreeManager, WorktreeProvisionError


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "ANCOS Test")
    _git(repository, "config", "user.email", "ancos-test@example.invalid")
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "baseline")
    return repository


def test_creates_task_branch_in_separate_worktree_without_moving_source_checkout(tmp_path):
    repository = _repository(tmp_path)
    manager = EngineeringWorktreeManager(repository, tmp_path / "worktrees")
    original_head = _git(repository, "rev-parse", "HEAD")

    receipt = manager.create("TASK-42")

    assert receipt.branch == "ancos/task/TASK-42"
    assert receipt.base_commit == original_head
    assert receipt.path == (tmp_path / "worktrees" / "TASK-42").resolve()
    assert _git(receipt.path, "branch", "--show-current") == receipt.branch
    assert _git(receipt.path, "rev-parse", "HEAD") == original_head
    assert _git(repository, "branch", "--show-current") == "main"
    assert _git(repository, "rev-parse", "HEAD") == original_head


def test_refuses_duplicate_task_worktree_without_overwriting_existing(tmp_path):
    repository = _repository(tmp_path)
    manager = EngineeringWorktreeManager(repository, tmp_path / "worktrees")
    receipt = manager.create("TASK-42")
    marker = receipt.path / "agent-change.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(WorktreeProvisionError, match="already exists"):
        manager.create("TASK-42")

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("task_id", ["../escape", "has space", "-flag", ""])
def test_rejects_unsafe_task_ids(tmp_path, task_id):
    repository = _repository(tmp_path)
    manager = EngineeringWorktreeManager(repository, tmp_path / "worktrees")

    with pytest.raises(WorktreeProvisionError, match="task_id"):
        manager.create(task_id)


def test_rejects_managed_root_inside_source_checkout(tmp_path):
    repository = _repository(tmp_path)
    manager = EngineeringWorktreeManager(repository, repository / "worktrees")

    with pytest.raises(WorktreeProvisionError, match="outside the source checkout"):
        manager.create("TASK-42")


def test_detects_checkout_root_when_repository_argument_is_a_subdirectory(tmp_path):
    repository = _repository(tmp_path)
    nested = repository / "src"
    nested.mkdir()
    manager = EngineeringWorktreeManager(nested, repository / "worktrees")

    with pytest.raises(WorktreeProvisionError, match="outside the source checkout"):
        manager.create("TASK-42")


def test_rejects_unknown_base_ref(tmp_path):
    repository = _repository(tmp_path)
    manager = EngineeringWorktreeManager(repository, tmp_path / "worktrees")

    with pytest.raises(WorktreeProvisionError):
        manager.create("TASK-42", base_ref="does-not-exist")
