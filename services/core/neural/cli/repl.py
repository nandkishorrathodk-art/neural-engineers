"""
Interactive Autonomous AI Coder REPL for ANCOS CLI.
Provides Rich terminal banner, command history, multiline prompt navigation,
agentic coding loop, and interactive slash commands (/diff, /test, /evolve, /approve, /status, /exit).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from neural.cli.approval_cli import ApprovalCLI
from neural.cli.coder_agent import TerminalCoderAgent
from neural.cli.diff_viewer import DiffViewer
from neural.cli.self_evolution_cli import CLIAutonomousEvolutionRunner
from neural.cli.status_cli import StatusCLI
from neural.logger import get_logger

logger = get_logger("ancos.cli.repl")


class InteractiveCoderREPL:
    """
    Frontier AI Terminal Coder REPL (Zero-Argument Default Experience).
    """

    def __init__(
        self,
        repo_root: Optional[Path | str] = None,
        employee_id: str = "ANC-EMP-CODER",
        model_tier: str = "deepseek-coder",
        console: Optional[Console] = None,
        input_fn: Optional[Callable[[str], str]] = None,
        history_file: Optional[Path | str] = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd()
        self.employee_id = employee_id
        self.model_tier = model_tier
        self.console = console or Console()
        self.input_fn = input_fn or input

        self.coder_agent = TerminalCoderAgent(
            repo_root=self.repo_root,
            employee_id=self.employee_id,
            model_tier=self.model_tier,
            console=self.console,
        )
        self.evolution_runner = CLIAutonomousEvolutionRunner(
            repository_root=self.repo_root,
            console=self.console,
        )
        self.approval_cli = ApprovalCLI(self.console)
        self.status_cli = StatusCLI(self.repo_root, self.console)
        self.running = False
        self.command_history: list[str] = []
        self.history_file = (
            Path(history_file).resolve()
            if history_file
            else (Path.home() / ".ancos" / "history").resolve()
        )
        self._load_history()

    def _load_history(self) -> None:
        try:
            if self.history_file.is_file():
                content = self.history_file.read_text(encoding="utf-8", errors="replace")
                self.command_history = [line.strip() for line in content.splitlines() if line.strip()]
        except Exception:
            pass

        try:
            import readline
            if self.history_file.is_file():
                readline.read_history_file(str(self.history_file))
            readline.set_history_length(1000)
        except Exception:
            pass

    def _save_history(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            if self.command_history:
                self.history_file.write_text("\n".join(self.command_history[-1000:]) + "\n", encoding="utf-8")
        except Exception:
            pass

        try:
            import readline
            readline.write_history_file(str(self.history_file))
        except Exception:
            pass

    def record_history(self, command: str) -> None:
        """Records command in session history."""
        cmd = command.strip()
        if cmd and (not self.command_history or self.command_history[-1] != cmd):
            self.command_history.append(cmd)
            self._save_history()

    def show_history(self) -> None:
        """Displays recent command history."""
        table = Table(title="Recent Command History", border_style="cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Command", style="white")

        if not self.command_history:
            self.console.print("[dim]No command history recorded yet.[/dim]")
            return

        for idx, cmd in enumerate(self.command_history[-20:], 1):
            table.add_row(str(idx), cmd)
        self.console.print(table)

    def get_git_branch(self) -> str:
        try:
            res = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.stdout.strip() or "main"
        except Exception:
            return "main"

    def print_banner(self) -> None:
        """Displays sleek Rich terminal banner on REPL launch."""
        branch = self.get_git_branch()
        banner_text = (
            f"[bold cyan]⚡ ANCOS Autonomous Neural Company Operating System — CLI Coder[/bold cyan]\n"
            f"[bold white]Repository:[/bold white] [green]{self.repo_root.name}[/green]  "
            f"[bold white]Branch:[/bold white] [yellow]{branch}[/yellow]  "
            f"[bold white]Identity:[/bold white] [bold magenta]{self.employee_id}[/bold magenta]\n"
            f"[bold white]Model Tier:[/bold white] [blue]{self.model_tier}[/blue] [dim](Dual-Brain: Laya Fast + Deep Reasoning)[/dim]\n\n"
            f"[dim]Type natural language instructions or slash commands ([/dim]"
            f"[bold yellow]/help[/bold yellow][dim], [/dim]"
            f"[bold yellow]/diff[/bold yellow][dim], [/dim]"
            f"[bold yellow]/test[/bold yellow][dim], [/dim]"
            f"[bold yellow]/evolve[/bold yellow][dim], [/dim]"
            f"[bold yellow]/approve[/bold yellow][dim], [/dim]"
            f"[bold yellow]/status[/bold yellow][dim], [/dim]"
            f"[bold yellow]/exit[/bold yellow][dim])[/dim]"
        )
        self.console.print(Panel(banner_text, border_style="cyan", expand=False))

    def show_help(self) -> None:
        """Prints slash command directory and usage help."""
        table = Table(title="Interactive Slash Commands", border_style="cyan")
        table.add_column("Command", style="bold yellow")
        table.add_column("Description", style="white")

        table.add_row("/help", "Show this interactive command reference")
        table.add_row("/diff", "Display syntax-highlighted unified diff of last applied changes or working tree")
        table.add_row("/test [path]", "Run pytest suite via streaming subprocess with PTY deadlock guards")
        table.add_row("/evolve", "Launch autonomous self-evolution & bottleneck optimization cycle")
        table.add_row("/approve [token]", "Manage sovereign human approval gates for destructive actions")
        table.add_row("/status", "Display enterprise health, employee stats, and model tier")
        table.add_row("/history", "View recently executed session commands")
        table.add_row("/clear", "Clear terminal screen")
        table.add_row("/exit, /quit", "Exit the interactive REPL session")

        self.console.print(table)
        self.console.print(
            "\n[dim]Tip: You can also write multi-line instructions by ending a line with '\\' or using triple quotes (\"\"\")[/dim]\n"
        )

    def handle_slash_command(self, line: str) -> bool:
        """
        Processes a slash command. Returns True if REPL should continue, False to exit.
        """
        parts = line.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "exit", "quit"):
            self.console.print("[dim]👋 Exiting ANCOS CLI Coder. Goodbye![/dim]")
            return False

        elif cmd == "/help":
            self.show_help()

        elif cmd == "/diff":
            if self.coder_agent.last_diff_summary and self.coder_agent.last_diff_summary.has_changes:
                self.coder_agent.diff_viewer.render_diff(
                    self.coder_agent.last_diff_summary,
                    title="Last Applied Changes",
                )
            elif self.coder_agent.session_diffs:
                self.coder_agent.diff_viewer.render_diffs(
                    self.coder_agent.session_diffs,
                    title_prefix="Session Edit",
                )
            else:
                git_diff = DiffViewer.get_git_diff(self.repo_root)
                if git_diff.has_changes:
                    self.coder_agent.diff_viewer.render_diff(git_diff, title="Git Working Tree Changes")
                else:
                    self.console.print("[yellow]No code modifications or working tree changes detected.[/yellow]")

        elif cmd == "/test":
            test_path = arg if arg else None
            self.coder_agent.run_tests(test_file=test_path, streaming=True)

        elif cmd == "/evolve":
            self.evolution_runner.run_evolution_cycle(
                auto_approve=False,
                interactive=True,
            )

        elif cmd == "/approve":
            if arg:
                self.approval_cli.approve_action(arg)
            else:
                self.approval_cli.show_pending_approvals()

        elif cmd == "/status":
            self.status_cli.render_status(
                employee_id=self.employee_id,
                model_tier=self.model_tier,
            )

        elif cmd == "/history":
            self.show_history()

        elif cmd == "/clear":
            self.console.clear()

        else:
            self.console.print(f"[bold red]Unknown command:[/bold red] {cmd}. Type [yellow]/help[/yellow] for options.")

        return True

    def run_session(self) -> None:
        """Runs the interactive REPL session loop."""
        self.print_banner()
        self.running = True

        while self.running:
            # Stdin disconnected check
            if self.input_fn is input:
                import sys
                if sys.stdin is None or getattr(sys.stdin, "closed", False):
                    self.console.print("\n[dim]Standard input disconnected. Exiting ANCOS REPL.[/dim]")
                    break

            try:
                prompt_str = "ancos > "
                user_input = self._read_multiline_input(prompt_str)
                if user_input is None:
                    # EOF or disconnected input stream encountered
                    self.console.print("\n[dim]Input stream disconnected / EOF received. Exiting ANCOS REPL.[/dim]")
                    break

                cleaned = user_input.strip()
                if not cleaned:
                    continue

                self.record_history(cleaned)

                if cleaned.startswith("/") or cleaned.lower() in ("exit", "quit", "help"):
                    cont = self.handle_slash_command(cleaned)
                    if not cont:
                        break
                else:
                    # Natural language agentic coding instruction
                    self.coder_agent.execute_instruction(cleaned)

            except (EOFError, KeyboardInterrupt, RuntimeError) as e:
                self.console.print(f"\n[dim]{type(e).__name__} received (stream disconnected). Exiting ANCOS REPL.[/dim]")
                break
            except Exception as e:
                self.console.print(f"[bold red]Error in REPL:[/bold red] {e}")

        self.running = False
        self._save_history()

    def run(self) -> None:
        """Alias for run_session."""
        self.run_session()

    def _read_multiline_input(self, prompt: str) -> Optional[str]:
        """Reads input with support for multiline continuation."""
        try:
            line = self.input_fn(prompt)
        except (EOFError, StopIteration, RuntimeError, KeyboardInterrupt):
            return None

        lines = [line]
        # Check if line ends with backslash or starts with triple-quote
        if line.endswith("\\"):
            lines[-1] = line[:-1]
            while True:
                try:
                    sub = self.input_fn("... ")
                    if sub.endswith("\\"):
                        lines.append(sub[:-1])
                    else:
                        lines.append(sub)
                        break
                except (EOFError, StopIteration, RuntimeError, KeyboardInterrupt):
                    break
        elif line.startswith('"""') and not (line.endswith('"""') and len(line) > 3):
            while True:
                try:
                    sub = self.input_fn("... ")
                    lines.append(sub)
                    if '"""' in sub:
                        break
                except (EOFError, StopIteration, RuntimeError, KeyboardInterrupt):
                    break

        return "\n".join(lines)
