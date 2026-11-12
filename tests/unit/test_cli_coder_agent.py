"""
Unit tests for TerminalCoderAgent and autonomous coding loop.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from neural.cli.coder_agent import TerminalCoderAgent
from neural.config import Settings
from neural.llm.gateway import LLMGateway, LLMResponse
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

    prompts_seen: list[str] = []

    async def fake_provider_invoker(*, model: str, prompt: str, **kwargs):
        prompts_seen.append(prompt)
        return {
            "content": "```python\ndef compute_magic() -> int:\n    return 42\n```\n",
            "input_tokens": 45,
            "output_tokens": 18,
            "provider": "mock-openai",
        }

    gateway = LLMGateway(provider_invoker=fake_provider_invoker)
    test_cmd = [sys.executable, "-m", "pytest", str(test_file)]
    agent = TerminalCoderAgent(
        repo_root=tmp_path,
        console=Console(record=True),
        llm_gateway=gateway,
    )

    report = agent.execute_instruction(
        instruction="Fix the bug in calc.py so test_compute_magic passes",
        test_command=test_cmd,
        file_edits={"calc.py": "def compute_magic() -> int:\n    return 41\n"},
        auto_repair=True,
    )

    assert len(prompts_seen) == 1
    assert "return 41" in prompts_seen[0]
    assert "41 == 42" in prompts_seen[0]
    assert report.success is True
    assert report.repair_attempts == 1
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

    class StubLLMGateway:
        async def generate(self, prompt: str, **kwargs) -> LLMResponse:
            assert "return 99" in prompt
            assert "100 == 99" in prompt
            return LLMResponse(
                content="```python\ndef get_value():\n    return 100\n```\n",
                model="stub-coder",
                tier="coding",
                input_tokens=30,
                output_tokens=15,
                cost_usd=0.0,
                provider="stub",
                latency_seconds=0.01,
            )

    test_cmd = [sys.executable, "-m", "pytest", str(test_file)]
    agent = TerminalCoderAgent(
        repo_root=tmp_path,
        console=Console(record=True),
        llm_gateway=StubLLMGateway(),
    )

    report = agent.execute_instruction(
        instruction="Fix value in calc_flipped.py",
        test_command=test_cmd,
        file_edits={"calc_flipped.py": "def get_value():\n    return 99\n"},
        auto_repair=True,
    )
    assert report.success is True
    assert report.repair_attempts == 1
    assert "100" in calc_file.read_text(encoding="utf-8")
    assert report.final_test_result is not None
    assert report.final_test_result.exit_code == 0


def _clear_llm_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(env_key, raising=False)
    empty_settings = Settings(
        _env_file=None,
        OPENAI_API_KEY=None,
        ANTHROPIC_API_KEY=None,
        GEMINI_API_KEY=None,
        NVIDIA_API_KEY=None,
    )
    monkeypatch.setattr("neural.llm.gateway.get_settings", lambda: empty_settings)


def test_coder_agent_fail_closed_when_no_llm_fix_synthesized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_llm_credentials(monkeypatch)

    calc_file = tmp_path / "calc.py"
    original_code = "def compute_magic() -> int:\n    return 41\n"
    calc_file.write_text(original_code, encoding="utf-8")

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
        instruction="Fix the bug in calc.py",
        test_command=test_cmd,
        file_edits={"calc.py": original_code},
        auto_repair=True,
    )

    assert report.success is False
    assert report.repair_attempts == 0
    assert "Diagnosis could not derive new changes; stopping repair loop." in report.repair_notes
    assert calc_file.read_text(encoding="utf-8") == original_code
    assert report.final_test_result is not None
    assert report.final_test_result.success is False
    assert report.final_test_result.exit_code != 0


def test_coder_agent_fail_closed_zero_division_and_invalid_llm_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _clear_llm_credentials(monkeypatch)

    div_file = tmp_path / "divider.py"
    original_div = "def ratio(x: int) -> float:\n    return x / 0\n"
    div_file.write_text(original_div, encoding="utf-8")

    test_file = tmp_path / "test_divider.py"
    test_file.write_text(
        "from divider import ratio\n\n"
        "def test_ratio():\n"
        "    assert ratio(10) == 5.0\n",
        encoding="utf-8",
    )

    test_cmd = [sys.executable, "-m", "pytest", str(test_file)]

    # 1. No LLM credentials: must fail closed without '/ 0' -> '/ 1' hack
    agent_no_llm = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))
    report_no_llm = agent_no_llm.execute_instruction(
        instruction="Fix division in divider.py",
        test_command=test_cmd,
        file_edits={"divider.py": original_div},
        auto_repair=True,
    )
    assert report_no_llm.success is False
    assert report_no_llm.repair_attempts == 0
    assert "/ 0" in div_file.read_text(encoding="utf-8")
    assert report_no_llm.final_test_result is not None
    assert report_no_llm.final_test_result.exit_code != 0

    # 2. Invalid/unparseable LLM output: must fail closed without corrupting file
    class BrokenOutputGateway:
        async def generate(self, prompt: str, **kwargs) -> LLMResponse:
            return LLMResponse(
                content="```python\ndef ratio(x:\n    # broken syntax\n```",
                model="stub-coder",
                tier="coding",
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.0,
                provider="stub",
                latency_seconds=0.01,
            )

    agent_bad_llm = TerminalCoderAgent(
        repo_root=tmp_path,
        console=Console(record=True),
        llm_gateway=BrokenOutputGateway(),
    )
    report_bad_llm = agent_bad_llm.execute_instruction(
        instruction="Fix division in divider.py",
        test_command=test_cmd,
        file_edits={"divider.py": original_div},
        auto_repair=True,
    )
    assert report_bad_llm.success is False
    assert report_bad_llm.repair_attempts == 0
    assert div_file.read_text(encoding="utf-8") == original_div
    assert report_bad_llm.final_test_result is not None
    assert report_bad_llm.final_test_result.exit_code != 0


def test_coder_agent_atomic_write_uses_tempfile_and_os_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "module.py"
    original = "VALUE = 1\n"
    target.write_text(original, encoding="utf-8")

    replace_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        replace_calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracking_replace)
    TerminalCoderAgent._atomic_write(target, "VALUE = 2\n")

    assert len(replace_calls) == 1
    assert replace_calls[0][0].parent == target.parent
    assert replace_calls[0][1] == target
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    # Simulate os.replace failure: original file remains intact and temp file is cleaned up
    def failing_replace(src, dst):
        raise OSError("Simulated atomic rename failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="Simulated atomic rename failure"):
        TerminalCoderAgent._atomic_write(target, "VALUE = 999\n")

    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_coder_agent_test_file_auto_discovery(tmp_path: Path):
    mod_file = tmp_path / "service_a.py"
    mod_file.write_text("def ping(): return True\n", encoding="utf-8")
    test_file = tmp_path / "test_service_a.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")

    agent = TerminalCoderAgent(repo_root=tmp_path, console=Console(record=True))
    found = agent.find_relevant_test([mod_file])
    assert found == "test_service_a.py"
