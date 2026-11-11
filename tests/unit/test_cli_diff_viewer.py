"""
Unit tests for DiffViewer and syntax highlighting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from neural.cli.diff_viewer import DiffSummary, DiffViewer
from rich.console import Console


def test_diff_viewer_compute_diff():
    old = "def foo():\n    return 1\n"
    new = "def foo():\n    return 2\n"

    diff = DiffViewer.compute_diff(old, new, from_file="a/code.py", to_file="b/code.py")
    assert diff.has_changes is True
    assert diff.additions == 1
    assert diff.deletions == 1
    assert "-    return 1" in diff.raw_diff
    assert "+    return 2" in diff.raw_diff


def test_diff_viewer_no_changes():
    text = "hello world\n"
    diff = DiffViewer.compute_diff(text, text)
    assert diff.has_changes is False
    assert diff.additions == 0
    assert diff.deletions == 0


def test_diff_viewer_render_to_console():
    console = Console(record=True, width=100)
    viewer = DiffViewer(console=console)

    summary = DiffSummary(
        file_path="service.py",
        additions=2,
        deletions=1,
        raw_diff="--- a/service.py\n+++ b/service.py\n@@ -1 +1,2 @@\n-old\n+new_1\n+new_2\n",
    )
    viewer.render_diff(summary)
    output = console.export_text()
    assert "Diff: service.py" in output
    assert "+new_1" in output


def test_diff_viewer_get_git_diff(tmp_path: Path):
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DiffTester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=repo_dir, check=True)

    f = repo_dir / "file.txt"
    f.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    # Make uncommitted modification
    f.write_text("initial\nmodified line\n", encoding="utf-8")
    git_diff = DiffViewer.get_git_diff(repo_dir)
    assert git_diff.has_changes is True
    assert git_diff.additions >= 1
    assert "+modified line" in git_diff.raw_diff
