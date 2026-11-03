from __future__ import annotations

import os
import socket
from typing import Optional


def resolve_resource_pool_id(resource_pool_id: Optional[str] = None) -> str:
    value = resource_pool_id or os.environ.get("ANCOS_RESOURCE_POOL_ID") or socket.gethostname()
    normalized = value.strip()
    if not normalized or len(normalized) > 96:
        raise ValueError("Resource pool ID must contain between 1 and 96 characters.")
    return normalized


def desktop_slot_resource_key(slot_id: int, resource_pool_id: Optional[str] = None) -> str:
    if slot_id < 1:
        raise ValueError("Desktop slot ID must be a positive integer.")
    return f"desktop:{resolve_resource_pool_id(resource_pool_id)}:slot-{slot_id}"
