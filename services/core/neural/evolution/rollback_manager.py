from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from neural.db.repositories import AuditRepository
from neural.evolution.models import (
    SelfEvolutionRollbackError,
    SelfEvolutionRollbackReceipt,
    utc_now,
)
from neural.logger import get_logger

logger = get_logger("ancos.evolution.rollback_manager")


@dataclass(frozen=True)
class HealthStatusReport:
    healthy: bool
    status: str
    anomaly_detected: Optional[dict[str, Any]] = None
    checked_at: str = field(default_factory=lambda: utc_now().isoformat())
    details: str = ""


class PostPromotionHealthMonitor:
    """
    Automated Post-Promotion Health Monitor.
    Runs continuous post-promotion canary probes, anomaly evaluations,
    and synthetic regression detection to guard production stability.
    """

    def __init__(self, repository: Path) -> None:
        self.repository = Path(repository)

    def check_health(
        self,
        health_probe: Optional[Callable[[], bool]] = None,
        canary_command: Optional[list[str]] = None,
        anomaly_payload: Optional[dict[str, Any]] = None,
    ) -> HealthStatusReport:
        """
        Evaluates health of the repository and codebase.
        Detects regressions, test failures, or injected anomalies.
        """
        # If an explicit anomaly payload is provided (e.g. synthetic fault injection)
        if anomaly_payload:
            return HealthStatusReport(
                healthy=False,
                status="regression_detected",
                anomaly_detected=anomaly_payload,
                details=f"Injected anomaly detected: {anomaly_payload.get('type')}",
            )

        # Custom health probe check
        if health_probe is not None:
            try:
                healthy = health_probe()
                if not healthy:
                    return HealthStatusReport(
                        healthy=False,
                        status="probe_failed",
                        anomaly_detected={
                            "type": "CANARY_PROBE_FAILURE",
                            "severity": "CRITICAL",
                            "detail": "Custom health probe function returned False.",
                        },
                        details="Canary health probe failed.",
                    )
            except Exception as exc:
                return HealthStatusReport(
                    healthy=False,
                    status="probe_error",
                    anomaly_detected={
                        "type": "PROBE_EXCEPTION",
                        "severity": "CRITICAL",
                        "detail": str(exc),
                    },
                    details=f"Health probe raised exception: {exc}",
                )

        # Canary test command check
        if canary_command:
            try:
                cmd = list(canary_command)
                if cmd and cmd[0] in ("python", "python3"):
                    cmd[0] = sys.executable

                env = os.environ.copy()
                pythonpath_entries: list[str] = [str(self.repository.resolve())]
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

                res = subprocess.run(
                    cmd,
                    cwd=str(self.repository),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                )
                if res.returncode != 0:
                    return HealthStatusReport(
                        healthy=False,
                        status="canary_tests_failed",
                        anomaly_detected={
                            "type": "POST_PROMOTION_REGRESSION_DETECTED",
                            "severity": "CRITICAL",
                            "exit_code": res.returncode,
                            "stderr": res.stderr.strip()[:300],
                        },
                        details=f"Canary test failure: exit code {res.returncode}",
                    )
            except Exception as exc:
                return HealthStatusReport(
                    healthy=False,
                    status="canary_execution_error",
                    anomaly_detected={
                        "type": "CANARY_EXECUTION_ERROR",
                        "severity": "CRITICAL",
                        "detail": str(exc),
                    },
                    details=f"Canary test execution failed: {exc}",
                )

        return HealthStatusReport(
            healthy=True,
            status="healthy",
            anomaly_detected=None,
            details="All post-promotion health checks and canary probes passed.",
        )


class SelfEvolutionRollbackManager:
    """
    Automated Fail-Closed Rollback Manager for Self-Evolution.
    Executes controlled rollback restoring the repository to the pinned
    pre-evolution commit state whenever post-promotion monitoring detects
    an anomaly or regression.
    """

    def __init__(self, repository: Path) -> None:
        self.repository = Path(repository).resolve(strict=True)

    async def execute_rollback(
        self,
        proposal_id: str,
        target_branch: str,
        pre_evolution_commit: str,
        anomaly_detected: dict[str, Any],
        audit_repo: Optional[AuditRepository] = None,
        requested_by: str = "PostPromotionHealthMonitor",
    ) -> SelfEvolutionRollbackReceipt:
        """
        Executes fail-closed rollback:
        1. Identifies the current rolled-back HEAD commit.
        2. Resets the target branch to the pinned pre-evolution commit state.
        3. Cleans any untracked or residual artifacts.
        4. Verifies the restored HEAD commit matches the pinned pre-evolution commit.
        5. Issues tamper-evident SelfEvolutionRollbackReceipt.
        6. Records audit entry with cryptographic hash chain.
        """
        logger.warning(
            "Triggering automated fail-closed rollback for self-evolution proposal",
            proposal_id=proposal_id,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit,
            anomaly=anomaly_detected,
        )

        # 1. Capture rolled-back commit (current bad state)
        try:
            rolled_back_commit = subprocess.run(
                ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip().lower()
        except subprocess.SubprocessError as exc:
            raise SelfEvolutionRollbackError(f"Failed to inspect current HEAD before rollback: {exc}") from exc

        # 2. Hard reset to pinned pre_evolution_commit
        try:
            subprocess.run(
                ["git", "-C", str(self.repository), "reset", "--hard", pre_evolution_commit],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(self.repository), "clean", "-fdx"],
                check=True,
                capture_output=True,
            )
        except subprocess.SubprocessError as exc:
            raise SelfEvolutionRollbackError(
                f"Fail-closed reset to pre-evolution commit '{pre_evolution_commit}' failed: {exc}"
            ) from exc

        # 3. Verify restored HEAD
        restored_commit = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().lower()

        if restored_commit != pre_evolution_commit.lower():
            raise SelfEvolutionRollbackError(
                f"Rollback verification mismatch: restored commit '{restored_commit}' "
                f"does not match pinned pre-evolution commit '{pre_evolution_commit.lower()}'"
            )

        # 4. Issue cryptographic SelfEvolutionRollbackReceipt
        receipt = SelfEvolutionRollbackReceipt.create(
            proposal_id=proposal_id,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit,
            rolled_back_commit=rolled_back_commit,
            restored_commit=restored_commit,
            anomaly_detected=anomaly_detected,
        )

        # 5. Record immutable audit log
        if audit_repo:
            await audit_repo.log(
                actor=requested_by,
                action="ROLLBACK_RESTORED_PRISTINE_STATE",
                resource_type="codebase",
                resource_id=proposal_id,
                details={
                    "restored_commit": restored_commit,
                    "pre_evolution_commit": pre_evolution_commit,
                    "rolled_back_commit": rolled_back_commit,
                    "rollback_receipt_id": receipt.receipt_id,
                    "anomaly_detected": anomaly_detected,
                },
            )

        logger.info(
            "Automated rollback completed successfully, restored pristine state",
            proposal_id=proposal_id,
            restored_commit=restored_commit,
            receipt_id=receipt.receipt_id,
        )
        return receipt
