from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
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

    def _get_employee_dir(self, employee_id: str) -> str:
        if not employee_id or not all(
            char.isascii() and (char.isalnum() or char in "_-") for char in employee_id
        ):
            raise ValueError("employee_id must be one safe path component")
        emp_dir = os.path.join(self.base_dir, employee_id)
        os.makedirs(emp_dir, exist_ok=True)
        return emp_dir

    def _get_checkpoint_path(self, employee_id: str, task_id: str) -> str:
        emp_dir = self._get_employee_dir(employee_id)
        if not task_id or "\x00" in task_id:
            raise ValueError("task_id must be a non-empty string without null bytes")
        safe_task = task_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        return os.path.join(emp_dir, f"{safe_task}_checkpoint.json")

    @staticmethod
    def _integrity_digest(data: dict[str, Any]) -> str:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _validate_checkpoint(
        cls,
        data: Any,
        employee_id: str,
        task_id: str,
    ) -> Optional[dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        digest = data.get("integrity_sha256")
        payload = {key: value for key, value in data.items() if key != "integrity_sha256"}
        version = payload.get("version", 1)
        if (
            version not in {1, 2}
            or payload.get("employee_id") != employee_id
            or payload.get("task_id") != task_id
            or not isinstance(payload.get("step_index"), int)
            or payload["step_index"] < 0
            or not isinstance(payload.get("state_dict"), dict)
            or not isinstance(payload.get("pending_operations"), list)
            or not isinstance(payload.get("workspace_path"), str)
        ):
            return None
        if version == 1 and digest is None:
            # Keep legacy data readable for operator recovery, but never treat it
            # as verified enough for automatic task resumption.
            return {**data, "integrity_verified": False}
        if version != 2 or not isinstance(digest, str):
            return None
        if not hmac.compare_digest(digest, cls._integrity_digest(payload)):
            return None
        return {**data, "integrity_verified": True}

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
            "version": 2,
        }
        data["integrity_sha256"] = self._integrity_digest(data)
        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=os.path.dirname(path),
                prefix=".checkpoint-",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = f.name
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            raise

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
            data = self._validate_checkpoint(data, employee_id, task_id)
            if data is None:
                logger.error(
                    "Checkpoint failed identity, version, schema, or integrity validation",
                    employee_id=employee_id,
                    task_id=task_id,
                    path=path,
                )
                return None
            logger.info("Loaded durable execution checkpoint", employee_id=employee_id, task_id=task_id)
            return data
        except Exception as e:
            logger.error("Failed to load checkpoint", path=path, error=str(e))
            return None

    def has_checkpoint(self, employee_id: str, task_id: str) -> bool:
        return self.load_checkpoint(employee_id, task_id) is not None

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
        emp_dir = self._get_employee_dir(employee_id)
        if not os.path.exists(emp_dir):
            return []
        checkpoints = []
        for fname in os.listdir(emp_dir):
            if fname.endswith("_checkpoint.json"):
                fpath = os.path.join(emp_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        candidate = json.load(f)
                    task_id = candidate.get("task_id") if isinstance(candidate, dict) else None
                    if isinstance(task_id, str):
                        checkpoint = self.load_checkpoint(employee_id, task_id)
                        if checkpoint is not None:
                            checkpoints.append(checkpoint)
                except Exception:
                    pass
        return checkpoints

