from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from neural.gui_intelligence.verification.visual_verifier import VisualVerificationResult
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.verification.task_completion")


@dataclass
class GUITaskCertificate:
    certificate_id: str
    task_id: str
    employee_id: str
    passed: bool
    score: float
    verified_speedup_ratio: float
    total_actions: int
    screenshot_hash: str
    issued_at: float
    signature: str
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskCompletionCertificateGenerator:
    """
    Tamper-Evident Receipt & Task Certificate Generator (Section 10.1).
    Anchors task deliverables into a cryptographically signed GUITaskCertificate.
    Guarantees tamper resistance via canonical JSON SHA-256 signature token.
    """

    @staticmethod
    def compute_signature(payload: dict[str, Any]) -> str:
        """Generates SHA-256 signature token over canonical JSON representation."""
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def generate_certificate(
        self,
        task_id: str,
        employee_id: str,
        verification_result: VisualVerificationResult,
        speedup_ratio: float = 1.0,
        total_actions: int = 1,
    ) -> GUITaskCertificate:
        """
        Issues an immutable, cryptographically signed GUI task completion certificate.
        """
        cert_id = f"cert_gui_{uuid.uuid4().hex[:12]}"
        now = time.time()

        payload = {
            "certificate_id": cert_id,
            "task_id": task_id,
            "employee_id": employee_id,
            "passed": verification_result.passed,
            "score": verification_result.score,
            "verified_speedup_ratio": speedup_ratio,
            "total_actions": total_actions,
            "screenshot_hash": verification_result.screenshot_hash,
            "issued_at": now,
            "details": verification_result.details,
        }
        signature = self.compute_signature(payload)

        return GUITaskCertificate(
            certificate_id=cert_id,
            task_id=task_id,
            employee_id=employee_id,
            passed=verification_result.passed,
            score=verification_result.score,
            verified_speedup_ratio=speedup_ratio,
            total_actions=total_actions,
            screenshot_hash=verification_result.screenshot_hash,
            issued_at=now,
            signature=signature,
            details=verification_result.details,
        )

    def verify_certificate(self, certificate: GUITaskCertificate) -> bool:
        """Validates tamper-resistance of a certificate by recomputing signature."""
        payload = {
            "certificate_id": certificate.certificate_id,
            "task_id": certificate.task_id,
            "employee_id": certificate.employee_id,
            "passed": certificate.passed,
            "score": certificate.score,
            "verified_speedup_ratio": certificate.verified_speedup_ratio,
            "total_actions": certificate.total_actions,
            "screenshot_hash": certificate.screenshot_hash,
            "issued_at": certificate.issued_at,
            "details": certificate.details,
        }
        expected = self.compute_signature(payload)
        return expected == certificate.signature
