from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from neural.logger import get_logger

logger = get_logger("ancos.resource_scheduler.checkpoints")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointManager:
    """
    Durable Checkpoint Manager.
    Persists employee execution checkpoints to durable host disk outside ephemeral container storage.
    Enables standby employees to be suspended and resumed without data loss.
    """

    def __init__(self, base_dir: str = "./data/checkpoints") -> None:
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def _get_checkpoint_path(self, employee_id: str, task_id: str) -> str:
        emp_dir = os.path.join(self.base_dir, employee_id)
        os.makedirs(emp_dir, exist_ok=True)
        safe_task = task_id.replace(":", "_").replace("/", "_")
        return os.path.join(emp_dir, f"{safe_task}_checkpoint.json")

    def save_checkpoint(
        self,
        employee_id: str,
        task_id: str,
        step_index: int,
        state_dict: dict[str, Any],
        pending_operations: Optional[list[dict[str, Any]]] = None,
        workspace_path: Optional[str] = None,
    ) -> str:
        path = self._get_checkpoint_path(employee_id, task_id)
        data = {
            "employee_id": employee_id,
            "task_id": task_id,
            "step_index": step_index,
            "state_dict": state_dict,
            "pending_operations": pending_operations or [],
            "workspace_path": workspace_path or "",
            "saved_at": utc_now_iso(),
            "version": 1,
        }
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, path)

        logger.info(
            "Saved durable execution checkpoint",
            employee_id=employee_id,
            task_id=task_id,
            step_index=step_index,
            path=path,
        )
        return path

    def load_checkpoint(self, employee_id: str, task_id: str) -> Optional[dict[str, Any]]:
        path = self._get_checkpoint_path(employee_id, task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded durable execution checkpoint", employee_id=employee_id, task_id=task_id)
            return data
        except Exception as e:
            logger.error("Failed to load checkpoint", path=path, error=str(e))
            return None

    def has_checkpoint(self, employee_id: str, task_id: str) -> bool:
        path = self._get_checkpoint_path(employee_id, task_id)
        return os.path.exists(path)

    def clear_checkpoint(self, employee_id: str, task_id: str) -> bool:
        path = self._get_checkpoint_path(employee_id, task_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except Exception:
                return False
        return False

    def list_employee_checkpoints(self, employee_id: str) -> list[dict[str, Any]]:
        emp_dir = os.path.join(self.base_dir, employee_id)
        if not os.path.exists(emp_dir):
            return []
        checkpoints = []
        for fname in os.listdir(emp_dir):
            if fname.endswith("_checkpoint.json"):
                fpath = os.path.join(emp_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        checkpoints.append(json.load(f))
                except Exception:
                    pass
        return checkpoints

