"""
Diff Viewer & Syntax Highlighter for ANCOS CLI.
Computes unified diffs and renders syntax-highlighted previews in Rich terminal.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax


@dataclass(frozen=True)
class DiffSummary:
    file_path: str
    additions: int
    deletions: int
    raw_diff: str

    @property
    def has_changes(self) -> bool:
        return self.additions > 0 or self.deletions > 0


class DiffViewer:
    """
    Renders syntax-highlighted unified diffs with terminal-friendly colorization.
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console()

    @staticmethod
    def compute_diff(
        old_text: str,
        new_text: str,
        from_file: str = "a/code",
        to_file: str = "b/code",
    ) -> DiffSummary:
        """Computes unified diff and calculates addition/deletion statistics."""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_file,
                tofile=to_file,
            )
        )

        raw_diff = "".join(diff_lines)
        additions = 0
        deletions = 0

        for line in diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return DiffSummary(
            file_path=to_file,
            additions=additions,
            deletions=deletions,
            raw_diff=raw_diff,
        )

    def render_diff(
        self,
        diff_summary: DiffSummary,
        title: Optional[str] = None,
        console: Optional[Console] = None,
    ) -> None:
        """Prints syntax-highlighted diff to the specified or default console."""
        target_console = console or self.console

        if not diff_summary.has_changes:
            target_console.print(f"[dim]No changes detected in {diff_summary.file_path}[/dim]")
            return

        header = title or f"Diff: {diff_summary.file_path} (+{diff_summary.additions} / -{diff_summary.deletions})"
        syntax = Syntax(
            diff_summary.raw_diff,
            "diff",
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        panel = Panel(
            syntax,
            title=f"[bold cyan]{header}[/bold cyan]",
            border_style="cyan",
            expand=False,
        )
        target_console.print(panel)

    def render_diffs(
        self,
        summaries: Sequence[DiffSummary],
        title_prefix: Optional[str] = None,
    ) -> None:
        """Renders multiple diff summaries in order."""
        for summary in summaries:
            if summary.has_changes:
                t = f"{title_prefix}: {summary.file_path}" if title_prefix else None
                self.render_diff(summary, title=t)

    @classmethod
    def get_git_diff(
        cls,
        repo_root: Optional[Path | str] = None,
        staged_only: bool = False,
    ) -> DiffSummary:
        """Computes unified diff from git working tree."""
        root = Path(repo_root).resolve() if repo_root else Path.cwd()
        cmd = ["git", "-C", str(root), "diff"]
        if staged_only:
            cmd.append("--cached")
        else:
            cmd.append("HEAD")

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            raw = res.stdout
            if not raw:
                # Fallback to plain working-tree diff if HEAD is unborn or not found
                res = subprocess.run(["git", "-C", str(root), "diff"], capture_output=True, text=True, check=False)
                raw = res.stdout
        except Exception:
            raw = ""

        additions = 0
        deletions = 0
        for line in raw.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return DiffSummary(
            file_path="working tree",
            additions=additions,
            deletions=deletions,
            raw_diff=raw,
        )

    @classmethod
    def diff_files(cls, original_path: Path | str, updated_path: Path | str) -> DiffSummary:
        """Diffs two files on disk."""
        orig = Path(original_path)
        upd = Path(updated_path)
        old_text = orig.read_text(encoding="utf-8") if orig.exists() else ""
        new_text = upd.read_text(encoding="utf-8") if upd.exists() else ""
        return cls.compute_diff(old_text, new_text, str(orig), str(upd))
