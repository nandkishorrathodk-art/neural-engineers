"""
Unit tests for CLIAutonomousEvolutionRunner (/evolve).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from neural.cli.self_evolution_cli import CLIAutonomousEvolutionRunner
from rich.console import Console


def test_cli_self_evolution_cycle(tmp_path: Path):
    # Initialize a git repo in tmp_path
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ANCOSCoder"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "coder@ancos.local"], cwd=repo_dir, check=True)

    dummy_file = repo_dir / "README.md"
    dummy_file.write_text("# ANCOS Evolution Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True)

    staging_dir = tmp_path / "worktrees"
    staging_dir.mkdir()

    runner = CLIAutonomousEvolutionRunner(
        repository_root=repo_dir,
        staging_root=staging_dir,
        console=Console(record=True),
    )

    result = runner.run_evolution_cycle(auto_approve=True, interactive=False)

    assert "EVO-" in result.proposal_id
    assert "Fast-Path Vector Kernel" in result.bottleneck_detected
    assert result.speedup_ratio >= 2.0
    assert result.verification_passed is True
    assert result.approved is True
    assert result.promoted is True
    assert result.diff_summary is not None
    assert result.diff_summary.has_changes is True


def test_cli_self_evolution_default_staging_root_outside_repo(tmp_path: Path):
    """Verifies that default runner initializes staging root strictly outside the repo."""
    repo_dir = tmp_path / "repo_default"
    repo_dir.mkdir()
    runner = CLIAutonomousEvolutionRunner(
        repository_root=repo_dir,
        console=Console(record=True),
    )
    # Must be outside repo checkout
    assert not runner.staging_root.is_relative_to(repo_dir)
    assert runner.staging_root.exists()
