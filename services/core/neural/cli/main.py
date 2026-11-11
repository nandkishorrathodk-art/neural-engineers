"""
ANCOS CLI Main Entrypoint.
Defaults to Interactive AI Coder REPL when invoked with zero arguments.
Supports optional direct subcommands (evolve, test, diff, approve, status).
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from rich.console import Console

from neural.cli.approval_cli import ApprovalCLI
from neural.cli.coder_agent import TerminalCoderAgent
from neural.cli.repl import InteractiveCoderREPL
from neural.cli.self_evolution_cli import CLIAutonomousEvolutionRunner
from neural.cli.status_cli import StatusCLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Main entry point for ANCOS CLI.
    If invoked with no arguments (e.g. typing `ancos` or `python -m neural.cli`),
    immediately launches the Interactive Autonomous AI Coder REPL!
    """
    args = list(sys.argv[1:] if argv is None else argv)
    console = Console()

    # Default Zero-Argument Experience: Interactive Terminal AI Coder REPL
    if not args or args == ["repl"]:
        repl = InteractiveCoderREPL(console=console)
        repl.run_session()
        return 0

    first_arg = args[0].strip()

    if first_arg in ("--help", "-h", "help"):
        console.print(
            "[bold cyan]⚡ ANCOS Autonomous Neural Company CLI Coder[/bold cyan]\n\n"
            "[bold white]Usage:[/bold white]\n"
            "  ancos                  Launch Interactive AI Coder REPL (Default)\n"
            "  ancos evolve           Trigger Autonomous Self-Evolution cycle\n"
            "  ancos test [path]      Run tests with PTY deadlock guards & streaming\n"
            "  ancos approve [id]     Review or grant sovereign approvals\n"
            "  ancos status           View company and workspace operational status\n"
            "  ancos \"<instruction>\"  Run natural language coding instruction non-interactively\n"
        )
        return 0

    if first_arg == "evolve":
        auto_approve = "--yes" in args or "-y" in args
        runner = CLIAutonomousEvolutionRunner(console=console)
        res = runner.run_evolution_cycle(auto_approve=auto_approve, interactive=not auto_approve)
        return 0 if res.verification_passed else 1

    if first_arg == "test":
        test_path = args[1] if len(args) > 1 else None
        agent = TerminalCoderAgent(console=console)
        res = agent.run_tests(test_file=test_path, streaming=True)
        return 0 if res.success else res.exit_code

    if first_arg == "diff":
        agent = TerminalCoderAgent(console=console)
        if agent.last_diff_summary and agent.last_diff_summary.has_changes:
            agent.diff_viewer.render_diff(agent.last_diff_summary)
        else:
            from neural.cli.diff_viewer import DiffViewer
            git_diff = DiffViewer.get_git_diff()
            if git_diff.has_changes:
                agent.diff_viewer.render_diff(git_diff, title="Git Working Tree Changes")
            else:
                console.print("[dim]No active diff recorded in current process or git working tree.[/dim]")
        return 0

    if first_arg == "approve":
        app_cli = ApprovalCLI(console=console)
        if len(args) > 1:
            app_cli.approve_action(args[1])
        else:
            app_cli.show_pending_approvals()
        return 0

    if first_arg == "status":
        status_cli = StatusCLI(console=console)
        status_cli.render_status()
        return 0

    # Otherwise treat the combined arguments as a natural language coding instruction
    instruction = " ".join(args)
    agent = TerminalCoderAgent(console=console)
    report = agent.execute_instruction(instruction)
    return 0 if report.success else 1


def entrypoint() -> None:
    """Installed console script entrypoint."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
