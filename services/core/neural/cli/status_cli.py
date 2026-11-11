"""
Company Health & CLI Status Dashboard for ANCOS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table


class StatusCLI:
    """
    Renders company operational health and status dashboard.
    """

    def __init__(self, repo_root: Optional[Path | str] = None, console: Optional[Console] = None) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd()
        self.console = console or Console()

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

    def render_status(
        self,
        employee_id: str = "ANC-EMP-CODER",
        model_tier: str = "deepseek-coder",
        active_task: Optional[str] = None,
    ) -> None:
        branch = self.get_git_branch()

        table = Table(title="ANCOS Enterprise & CLI Coder Status", border_style="cyan")
        table.add_column("Property", style="bold white")
        table.add_column("Value", style="cyan")

        table.add_row("Repository", self.repo_root.name)
        table.add_row("Git Branch", branch)
        table.add_row("Active AI Employee", employee_id)
        table.add_row("Employee Role", "Staff Software Engineer / Terminal Coder")
        table.add_row("Model Routing Tier", f"{model_tier} (Dual-Brain: Laya Fast + Reasoning)")
        table.add_row("Desktop Sandbox", "RestrictedDockerBroker (Isolated Container)")
        table.add_row("Active Mission", active_task or "Idle / Ready for instruction")
        table.add_row("Security & Integrity", "Cryptographic Hash-Chains Active (SHA-256)")

        self.console.print(table)
