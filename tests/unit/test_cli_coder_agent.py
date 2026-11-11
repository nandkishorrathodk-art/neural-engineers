"""
Unit tests for TerminalCoderAgent and autonomous coding loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from neural.cli.coder_agent import TerminalCoderAgent
from rich.console import Console


def test_coder_agent_context_exploration(tmp_path: Path):
    auth_file = tmp_path / "auth_service.py"
    auth_file.write_text(
        "class AuthManager:\n"
        "    def authenticate(self, user: str) -> bool:\n"
        "        return True\n",
        encoding="utf-8",
    )

    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))
    context = agent.explore_context("Add rate limiting to AuthManager in auth_service.py")

    assert "auth_service.py" in context.relevant_files
    assert any(s.name == "AuthManager" for s in context.matched_symbols)


def test_coder_agent_apply_edits_and_diff(tmp_path: Path):
    target = tmp_path / "routes.py"
    target.write_text("def index(): return 'ok'\n", encoding="utf-8")

    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))
    context = agent.explore_context("routes", target_files=["routes.py"])
    plans = agent.plan_and_edit(
        "Update routes with rate limiting",
        context,
        file_edits={"routes.py": "def index(): return 'ok'\n# Rate limiting applied\n"},
    )
    assert len(plans) == 1

    diffs = agent.apply_plans(plans)
    assert len(diffs) == 1
    assert diffs[0].additions > 0
    assert agent.last_diff_summary is not None
    assert "Rate limiting applied" in target.read_text(encoding="utf-8")


def test_coder_agent_autonomous_diagnosis_and_repair(tmp_path: Path):
    # Setup code file with a bug: returning 41 instead of 42
    calc_file = tmp_path / "calc.py"
    calc_file.write_text(
        "def compute_magic() -> int:\n"
        "    return 41\n",
        encoding="utf-8",
    )

    # Setup test file
    test_file = tmp_path / "test_calc.py"
    test_file.write_text(
        "from calc import compute_magic\n\n"
        "def test_compute_magic():\n"
        "    assert compute_magic() == 42\n",
        encoding="utf-8",
    )

    test_cmd = [sys.executable, "-m", "pytest", str(test_file)]
    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))

    report = agent.execute_instruction(
        instruction="Fix the bug in calc.py so test_compute_magic passes",
        test_command=test_cmd,
        file_edits={"calc.py": "def compute_magic() -> int:\n    return 41\n"},
        auto_repair=True,
    )

    assert report.success is True
    assert report.repair_attempts >= 1
    assert "return 42" in calc_file.read_text(encoding="utf-8")
    assert report.final_test_result is not None
    assert report.final_test_result.exit_code == 0


def test_coder_agent_flipped_assertion_repair(tmp_path: Path):
    calc_file = tmp_path / "calc_flipped.py"
    calc_file.write_text("def get_value():\n    return 99\n", encoding="utf-8")

    test_file = tmp_path / "test_calc_flipped.py"
    test_file.write_text(
        "from calc_flipped import get_value\n\n"
        "def test_flipped():\n"
        "    assert 100 == get_value()\n",
        encoding="utf-8",
    )

    test_cmd = [sys.executable, "-m", "pytest", str(test_file)]
    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))

    report = agent.execute_instruction(
        instruction="Fix value in calc_flipped.py",
        test_command=test_cmd,
        file_edits={"calc_flipped.py": "def get_value():\n    return 99\n"},
        auto_repair=True,
    )
    assert report.success is True
    assert "100" in calc_file.read_text(encoding="utf-8")


def test_coder_agent_test_file_auto_discovery(tmp_path: Path):
    mod_file = tmp_path / "service_a.py"
    mod_file.write_text("def ping(): return True\n", encoding="utf-8")
    test_file = tmp_path / "test_service_a.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")

    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))
    found = agent.find_relevant_test([mod_file])
    assert found == "test_service_a.py"
