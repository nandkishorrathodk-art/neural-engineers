"""
Unit tests for InteractiveCoderREPL and CLI main entrypoint.
"""

from __future__ import annotations

from pathlib import Path

from neural.cli.main import main
from neural.cli.repl import InteractiveCoderREPL
from rich.console import Console


def test_repl_banner_and_help(tmp_path: Path):
    console = Console(record=True, width=120)
    repl = InteractiveCoderREPL(repo_root=tmp_path, console=console)

    repl.print_banner()
    output = console.export_text()
    assert "ANCOS" in output
    assert "CLI Coder" in output
    assert "ANC-EMP-CODER" in output
    assert "deepseek-coder" in output


def test_repl_slash_commands(tmp_path: Path):
    console = Console(record=True, width=120)
    repl = InteractiveCoderREPL(repo_root=tmp_path, console=console)

    # Test /help
    assert repl.handle_slash_command("/help") is True
    # Test /status
    assert repl.handle_slash_command("/status") is True
    # Test /diff when no diff
    assert repl.handle_slash_command("/diff") is True
    # Test /exit
    assert repl.handle_slash_command("/exit") is False

    text = console.export_text()
    assert "Interactive Slash Commands" in text
    assert "ANCOS Enterprise & CLI Coder Status" in text
    assert "Exiting ANCOS CLI Coder" in text


def test_repl_session_simulation(tmp_path: Path):
    inputs = iter([
        "/status",
        "/help",
        "/exit",
    ])
    console = Console(record=True, width=120)
    repl = InteractiveCoderREPL(
        repo_root=tmp_path,
        console=console,
        input_fn=lambda _: next(inputs),
        history_file=tmp_path / "test_session_history",
    )
    repl.run_session()

    assert repl.running is False
    text = console.export_text()
    assert "ANCOS Enterprise & CLI Coder Status" in text


def test_repl_multiline_input(tmp_path: Path):
    inputs = iter([
        "first line\\",
        "second line",
    ])
    repl = InteractiveCoderREPL(
        repo_root=tmp_path,
        console=Console(record=True),
        input_fn=lambda _: next(inputs),
        history_file=tmp_path / "test_multiline_history",
    )
    res = repl._read_multiline_input("prompt > ")
    assert res == "first line\nsecond line"


def test_main_subcommands(tmp_path: Path):
    # Test ancos --help
    code_help = main(["--help"])
    assert code_help == 0

    # Test ancos status
    code_status = main(["status"])
    assert code_status == 0

    # Test ancos diff
    code_diff = main(["diff"])
    assert code_diff == 0

    # Test ancos approve
    code_approve = main(["approve"])
    assert code_approve == 0


def test_repl_command_history(tmp_path: Path):
    console = Console(record=True, width=120)
    repl = InteractiveCoderREPL(
        repo_root=tmp_path,
        console=console,
        history_file=tmp_path / "custom_history",
    )

    repl.record_history("/status")
    repl.record_history("Add rate limiting")
    assert len(repl.command_history) == 2
    assert repl.command_history[0] == "/status"
    assert repl.command_history[1] == "Add rate limiting"

    # Test /history command
    assert repl.handle_slash_command("/history") is True
    output = console.export_text()
    assert "Recent Command History" in output
    assert "Add rate limiting" in output
