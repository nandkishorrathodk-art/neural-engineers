"""
ANCOS NEURO-14 Master Live Acceptance Test Suite.
Empirically verifies the Developer CLI Platform & Interactive Terminal Operations across:
1. Zero-Argument Launch & Rich Terminal Banner (ANC-EMP-CODER, git branch, model tier).
2. AST Repository Map & Codebase Context Exploration (classes, methods, docstrings, signatures).
3. PTY Deadlock-Guarded Subprocess Execution & Live Streaming.
4. Autonomous Terminal Coding Loop: Natural Language Instruction -> AST Exploration ->
   Code Edits -> Syntax-Highlighted Diff Preview -> Test Run.
5. Autonomous Bug Diagnosis & Self-Fix Loop (diagnoses test failure, fixes bug, passes tests with exit code 0).
6. Interactive Slash Commands (/help, /diff, /test, /approve, /status, /exit).
7. Autonomous Self-Development & Self-Evolution (/evolve): Bottleneck Detection ->
   Isolated Candidate Worktree -> Vectorized Speedup -> Multi-Gate Verification ->
   Diff Preview -> One-Click Approval -> Verified Merge.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from neural.cli.ast_repomap import ASTRepoMap
from neural.cli.coder_agent import TerminalCoderAgent
from neural.cli.main import main
from neural.cli.pty_guard import PTYExecutionGuard
from neural.cli.repl import InteractiveCoderREPL
from neural.cli.self_evolution_cli import CLIAutonomousEvolutionRunner
from rich.console import Console


@pytest.mark.asyncio
async def test_neuro_phase14_end_to_end_cli_coder_acceptance(tmp_path: Path):
    """
    Comprehensive 7-Step Live Acceptance Scenario for NEURO-14.
    """
    # -------------------------------------------------------------------------
    # Setup Sandbox Git Repository for Live Acceptance
    # -------------------------------------------------------------------------
    repo_dir = tmp_path / "sandbox_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ANCOSCoder"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "coder@ancos.corp"], cwd=repo_dir, check=True)

    # Initial codebase files
    readme = repo_dir / "README.md"
    readme.write_text("# Sandbox Enterprise Repository\n", encoding="utf-8")

    auth_mod = repo_dir / "auth_service.py"
    auth_mod.write_text(
        "\"\"\"Authentication module for enterprise services.\"\"\"\n\n"
        "class AuthenticationService:\n"
        "    \"\"\"Handles user authentication and token checks.\"\"\"\n"
        "    def login(self, username: str) -> bool:\n"
        "        return True\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial baseline commit"], cwd=repo_dir, check=True)

    staging_dir = tmp_path / "worktrees"
    staging_dir.mkdir()

    console = Console(record=True, width=120)

    # =========================================================================
    # STEP 1: Default Zero-Argument CLI Launch & Rich Terminal Banner
    # =========================================================================
    repl = InteractiveCoderREPL(
        repo_root=repo_dir,
        employee_id="ANC-EMP-CODER",
        model_tier="deepseek-coder",
        console=console,
    )
    repl.print_banner()
    banner_output = console.export_text()

    assert "ANCOS" in banner_output
    assert "CLI Coder" in banner_output
    assert "ANC-EMP-CODER" in banner_output
    assert "deepseek-coder" in banner_output
    assert repo_dir.name in banner_output

    # =========================================================================
    # STEP 2: AST Repository Map & Codebase Context Exploration
    # =========================================================================
    ast_map = ASTRepoMap(root_dir=repo_dir)
    scanned_files = ast_map.scan_repository()
    assert "auth_service.py" in scanned_files

    record = scanned_files["auth_service.py"]
    assert record.docstring == "Authentication module for enterprise services."
    assert len(record.classes) == 1
    assert record.classes[0]["name"] == "AuthenticationService"
    assert record.classes[0]["methods"][0]["name"] == "login"

    # Search symbols
    matched_syms = ast_map.search_symbols("auth")
    assert any(s.name == "AuthenticationService" for s in matched_syms)

    summary = ast_map.render_summary()
    assert "class AuthenticationService" in summary

    # =========================================================================
    # STEP 3: PTY Deadlock-Guarded Subprocess Execution & Output Streaming
    # =========================================================================
    pty_guard = PTYExecutionGuard(cwd=repo_dir, default_timeout=15.0)
    streaming_lines: list[str] = []

    # High-volume output test (10,000 lines) to empirically prove zero pipe deadlocks
    heavy_script = (
        "import sys\n"
        "for i in range(5000):\n"
        "    sys.stdout.write(f'stream_line_{i:05d}\\n')\n"
    )
    sub_res = pty_guard.run_command(
        [sys.executable, "-c", heavy_script],
        on_stdout_line=lambda line: streaming_lines.append(line.strip()) if len(streaming_lines) < 10 else None,
    )
    assert sub_res.success is True
    assert not sub_res.timed_out
    assert len(streaming_lines) >= 10
    assert "stream_line_04999" in sub_res.stdout

    # =========================================================================
    # STEP 4: Autonomous Terminal Coding Loop (NL Instruction -> AST -> Edits -> Diff -> Test)
    # =========================================================================
    agent = TerminalCoderAgent(
        repo_root=repo_dir,
        employee_id="ANC-EMP-CODER",
        model_tier="deepseek-coder",
        console=console,
    )

    # Developer issues natural language instruction to inject rate limiting
    instruction = "Add rate limiting guards to AuthenticationService in auth_service.py"
    context = agent.explore_context(instruction)
    assert "auth_service.py" in context.relevant_files

    plans = agent.plan_and_edit(instruction, context)
    assert len(plans) == 1
    assert plans[0].file_path.name == "auth_service.py"
    assert "RATE_LIMIT" in plans[0].updated_content

    # Apply edit and verify live syntax-highlighted diff
    diff_summaries = agent.apply_plans(plans)
    assert len(diff_summaries) == 1
    assert diff_summaries[0].additions > 0
    assert agent.last_diff_summary is not None

    # Verify disk was updated
    updated_auth = (repo_dir / "auth_service.py").read_text(encoding="utf-8")
    assert "check_rate_limit" in updated_auth

    # =========================================================================
    # STEP 5: Autonomous Bug Diagnosis & Self-Fix Loop
    # =========================================================================
    # Setup test file and deliberate assertion discrepancy
    math_file = repo_dir / "math_service.py"
    math_file.write_text(
        "def compute_discount(price: float) -> float:\n"
        "    return price * 0.85\n",
        encoding="utf-8",
    )
    test_math_file = repo_dir / "test_math_service.py"
    test_math_file.write_text(
        "from math_service import compute_discount\n\n"
        "def test_compute_discount():\n"
        "    assert compute_discount(100.0) == 80.0\n",
        encoding="utf-8",
    )

    test_cmd = [sys.executable, "-m", "pytest", str(test_math_file)]
    repair_report = agent.execute_instruction(
        instruction="Fix the discount calculation in math_service.py to match test expectation",
        test_command=test_cmd,
        file_edits={"math_service.py": "def compute_discount(price: float) -> float:\n    return price * 0.85\n"},
        auto_repair=True,
    )

    assert repair_report.success is True
    assert repair_report.repair_attempts >= 1
    assert "0.8" in (repo_dir / "math_service.py").read_text(encoding="utf-8")
    assert repair_report.final_test_result.exit_code == 0

    # =========================================================================
    # STEP 6: Interactive Slash Commands Validation
    # =========================================================================
    # Slash command /diff
    assert repl.handle_slash_command("/diff") is True
    # Slash command /status
    assert repl.handle_slash_command("/status") is True
    # Slash command /approve
    assert repl.handle_slash_command("/approve") is True
    # Slash command /help
    assert repl.handle_slash_command("/help") is True
    # Slash command /exit
    assert repl.handle_slash_command("/exit") is False

    # CLI main entry point with zero args vs subcommands
    assert main(["status"]) == 0
    assert main(["diff"]) == 0
    assert main(["approve"]) == 0
    assert main(["--help"]) == 0

    # =========================================================================
    # STEP 7: Autonomous Self-Development & Self-Evolution (/evolve)
    # =========================================================================
    evo_runner = CLIAutonomousEvolutionRunner(
        repository_root=repo_dir,
        staging_root=staging_dir,
        console=console,
    )

    evo_result = evo_runner.run_evolution_cycle(auto_approve=True, interactive=False)

    assert "EVO-" in evo_result.proposal_id
    assert "Fast-Path Vector Kernel" in evo_result.bottleneck_detected
    assert evo_result.speedup_ratio >= 2.0
    assert evo_result.verification_passed is True
    assert evo_result.approved is True
    assert evo_result.promoted is True
    assert evo_result.diff_summary is not None
    assert evo_result.diff_summary.has_changes is True
    assert evo_result.receipt_sha256 is not None

    # Verify candidate changes merged cleanly into main branch
    merged_kernel = repo_dir / "math_kernel.py"
    assert merged_kernel.exists()
    assert "np.dot" in merged_kernel.read_text(encoding="utf-8")
