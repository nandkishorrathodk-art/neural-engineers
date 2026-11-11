from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from neural.engineering.models import BugReport, QAReviewReceipt, ReviewGateError
from neural.engineering.worktrees import EngineeringWorktreeManager, WorktreeProvisionError
from neural.logger import get_logger

logger = get_logger("ancos.engineering.qa_review")


class QAReviewEngine:
    """
    Independent QA Review & Verification Engine.
    Executes automated tests in isolated worktree sandboxes, analyzes diffs,
    detects regressions, produces structured bug reproduction feedback,
    and signs cryptographic verification receipts.
    """

    def __init__(self, default_timeout_seconds: int = 60) -> None:
        self.default_timeout_seconds = default_timeout_seconds

    def review_task_worktree(
        self,
        worktree_manager: EngineeringWorktreeManager,
        task_id: str,
        qa_engineer_id: str,
        test_command: Optional[list[str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> QAReviewReceipt:
        """
        Executes an independent QA review of the task worktree:
        1. Cryptographically verifies worktree provenance.
        2. Inspects git diff and changed files.
        3. Executes test command in the isolated worktree directory.
        4. Detects failures and builds structured bug report if broken.
        5. Issues signed QAReviewReceipt.
        """
        try:
            worktree_receipt = worktree_manager.verify_receipt(task_id)
        except WorktreeProvisionError as exc:
            raise ReviewGateError(f"Cannot perform QA review: worktree provenance invalid: {exc}") from exc

        worktree_path = worktree_receipt.path

        # Ensure worktree has no uncommitted changes (ignoring runtime test caches)
        status_lines = [
            line.strip()
            for line in worktree_manager._git_at(worktree_path, "status", "--porcelain").splitlines()
            if line.strip()
        ]
        dirty = []
        for line in status_lines:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                path_part = parts[1].strip()
                if any(ign in path_part for ign in ["__pycache__", ".pytest_cache", ".coverage", ".ruff_cache"]):
                    continue
            dirty.append(line)
        if dirty:
            raise ReviewGateError(
                "Cannot perform QA review: task worktree has uncommitted changes. "
                "All code changes must be committed before review."
            )

        head_commit = worktree_manager.get_head_commit(task_id)
        diff = worktree_manager.get_diff(task_id)
        changed_files = worktree_manager.list_changed_files(task_id)
        diff_stat = f"{len(changed_files)} files changed, {len(diff.splitlines())} diff lines"

        cmd = test_command or [sys.executable, "-m", "pytest", "-q"]
        timeout = timeout_seconds or self.default_timeout_seconds

        # Build clean environment with absolute PYTHONPATH entries so changing cwd to worktree
        # does not break package discovery (e.g. pytest or dependencies in .venv).
        env = os.environ.copy()
        pythonpath_entries: list[str] = [str(worktree_path.resolve())]
        if "PYTHONPATH" in env and env["PYTHONPATH"]:
            for entry in env["PYTHONPATH"].split(os.pathsep):
                if entry.strip():
                    pythonpath_entries.append(str(Path(entry.strip()).resolve()))
        for p in sys.path:
            if "site-packages" in p:
                resolved_p = str(Path(p).resolve())
                if resolved_p not in pythonpath_entries:
                    pythonpath_entries.append(resolved_p)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

        logger.info(
            "Executing QA review test suite",
            task_id=task_id,
            qa_engineer=qa_engineer_id,
            cmd=cmd,
            worktree=str(worktree_path),
        )

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = getattr(exc, "stdout", "") or ""
            stderr = f"Test execution timed out after {timeout} seconds"
        except (OSError, subprocess.SubprocessError) as exc:
            exit_code = 127
            stdout = ""
            stderr = f"Failed to execute test runner: {exc}"

        combined_output = f"{stdout}\n{stderr}".strip()

        # Compute artifact digests for changed files
        artifact_digests = self._compute_artifact_digests(worktree_path, changed_files)

        if exit_code != 0:
            failed_tests = self._extract_failed_tests(combined_output)
            bug_report = BugReport(
                bug_id=f"BUG-{uuid.uuid4().hex[:8].upper()}",
                task_id=task_id,
                reporter_id=qa_engineer_id,
                reproduction_command=" ".join(cmd),
                failure_summary=f"Automated test runner failed with exit code {exit_code}",
                failed_tests=failed_tests,
                error_details=combined_output[:4000],
                affected_files=changed_files,
                suggested_action="Fix failing assertion / logic in affected files and re-run test suite",
            )
            tests_run, tests_passed, tests_failed = self._parse_test_counts(combined_output, fallback_failed=True)
            return QAReviewReceipt.create(
                task_id=task_id,
                qa_engineer_id=qa_engineer_id,
                passed=False,
                test_exit_code=exit_code,
                tests_run=tests_run,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                diff_stat=diff_stat,
                artifact_digests=artifact_digests,
                bug_report=bug_report,
                head_commit=head_commit,
            )

        tests_run, tests_passed, tests_failed = self._parse_test_counts(combined_output, fallback_failed=False)
        return QAReviewReceipt.create(
            task_id=task_id,
            qa_engineer_id=qa_engineer_id,
            passed=True,
            test_exit_code=0,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            diff_stat=diff_stat,
            artifact_digests=artifact_digests,
            bug_report=None,
            head_commit=head_commit,
        )

    @staticmethod
    def _compute_artifact_digests(root: Path, relative_paths: list[str]) -> dict[str, str]:
        digests: dict[str, str] = {}
        for rel in relative_paths:
            full = (root / rel).resolve()
            if full.is_file():
                try:
                    digests[rel] = hashlib.sha256(full.read_bytes()).hexdigest()
                except OSError:
                    pass
        return digests

    @staticmethod
    def _extract_failed_tests(output: str) -> list[str]:
        failed = []
        for line in output.splitlines():
            line_str = line.strip()
            if line_str.startswith("FAILED ") or line_str.startswith("ERROR "):
                parts = line_str.split()
                if len(parts) >= 2:
                    failed.append(parts[1])
            elif "AssertionError" in line_str or "Error:" in line_str:
                if line_str not in failed:
                    failed.append(line_str[:120])
        return failed or ["unspecified_failure"]

    @staticmethod
    def _parse_test_counts(output: str, fallback_failed: bool) -> tuple[int, int, int]:
        # Pytest summary match: e.g. "5 passed, 1 failed in 0.23s", "3 passed in 0.05s", "1 error in 0.12s"
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        error_match = re.search(r"(\d+)\s+error", output)
        passed = int(passed_match.group(1)) if passed_match else (0 if fallback_failed else 1)
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(error_match.group(1)) if error_match else 0
        total_failed = failed + errors
        if fallback_failed and total_failed == 0:
            total_failed = 1
        total = passed + total_failed
        return max(total, 1), passed, total_failed
        failed = int(failed_match.group(1)) if failed_match else (1 if fallback_failed else 0)
        total = passed + failed
        return max(total, 1), passed, failed
