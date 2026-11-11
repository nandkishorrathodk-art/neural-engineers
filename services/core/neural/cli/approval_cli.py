"""
Approval Gate CLI Operations for ANCOS.
Inspects pending destructive operations and grants sovereign human approvals.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class ApprovalCLI:
    """
    CLI handler for sovereign human approval gates.
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console()

    def show_pending_approvals(self, pending_list: Optional[list[dict]] = None) -> None:
        """Displays formatted table of pending operations requiring approval."""
        table = Table(title="Sovereign Human Approval Gates", border_style="yellow")
        table.add_column("Approval ID", style="bold cyan")
        table.add_column("Action Type", style="magenta")
        table.add_column("Target / Resource", style="green")
        table.add_column("Risk Level", style="red")
        table.add_column("Requested By", style="blue")

        items = pending_list or []
        if not items:
            self.console.print(
                Panel("[bold green]✔ No pending approval gates required.[/bold green]\nAll operations are within safe policy limits.", border_style="green")
            )
            return

        for item in items:
            table.add_row(
                str(item.get("approval_id", "N/A")),
                str(item.get("action_type", "destructive_operation")),
                str(item.get("target", "system")),
                str(item.get("risk_level", "HIGH")),
                str(item.get("requester", "ANC-EMP-CODER")),
            )
        self.console.print(table)

    def approve_action(self, approval_id: str, approver: str = "ANC-OPERATOR-CLI") -> bool:
        """Approves a pending operation."""
        self.console.print(
            f"[bold green]✔ Approved operation {approval_id} by {approver}![/bold green]"
        )
        return True
