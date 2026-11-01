from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DesktopSessionLease:
    slot_id: int
    employee_id: str
    lease_id: str = field(default_factory=lambda: f"lease_{uuid.uuid4().hex[:8]}")
    ttl_seconds: float = 1800.0  # 30 minutes default
    granted_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

    @property
    def expires_at(self) -> float:
        return self.last_heartbeat + self.ttl_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def heartbeat(self) -> None:
        self.last_heartbeat = time.time()

    def to_dict(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "slot_id": self.slot_id,
            "employee_id": self.employee_id,
            "granted_at": self.granted_at,
            "last_heartbeat": self.last_heartbeat,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired(),
        }


class SessionLeaseManager:
    def __init__(self, default_ttl_seconds: float = 1800.0) -> None:
        self.default_ttl = default_ttl_seconds
        self._leases: dict[int, DesktopSessionLease] = {}  # slot_id -> lease

    def grant(self, slot_id: int, employee_id: str, ttl_seconds: Optional[float] = None) -> DesktopSessionLease:
        ttl = ttl_seconds or self.default_ttl
        lease = DesktopSessionLease(slot_id=slot_id, employee_id=employee_id, ttl_seconds=ttl)
        self._leases[slot_id] = lease
        return lease

    def get_lease(self, slot_id: int) -> Optional[DesktopSessionLease]:
        return self._leases.get(slot_id)

    def heartbeat(self, slot_id: int) -> bool:
        lease = self._leases.get(slot_id)
        if lease and not lease.is_expired():
            lease.heartbeat()
            return True
        return False

    def revoke(self, slot_id: int) -> Optional[DesktopSessionLease]:
        return self._leases.pop(slot_id, None)

    def find_expired_slots(self) -> list[int]:
        return [slot_id for slot_id, lease in self._leases.items() if lease.is_expired()]

