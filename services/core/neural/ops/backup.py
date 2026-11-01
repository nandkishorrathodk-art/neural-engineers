from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from neural.config import get_settings
from neural.exceptions import StorageError
from neural.logger import get_logger

logger = get_logger("ancos.ops.backup")


@dataclass
class ComponentChecksum:
    component: str
    relative_path: str
    sha256_hash: str
    size_bytes: int


@dataclass
class BackupManifest:
    backup_id: str
    timestamp: str
    ancos_version: str
    components: list[ComponentChecksum] = field(default_factory=list)
    total_size_bytes: int = 0
    duration_seconds: float = 0.0


@dataclass
class RestorationReport:
    backup_id: str
    target_dir: str
    duration_seconds: float
    verified_components: int
    db_integrity_ok: bool
    status: str  # "SUCCESS", "FAILED"
    details: dict[str, Any] = field(default_factory=dict)


class DisasterRecoveryManager:
    """
    Enterprise Backup and Disaster Recovery Engine.
    Implements point-in-time snapshots, cryptographic integrity validation,
    and verifiable full restoration into a clean environment.
    """

    def __init__(self, base_data_dir: str = "./data", configs_dir: str = "./configs") -> None:
        self.settings = get_settings()
        self.base_data_dir = os.path.abspath(base_data_dir)
        self.configs_dir = os.path.abspath(configs_dir)
        self.backup_storage_dir = os.path.abspath(os.path.join(self.base_data_dir, "backups"))
        os.makedirs(self.backup_storage_dir, exist_ok=True)

    @staticmethod
    def _compute_sha256(file_path: str) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def backup_database(self, destination_file: str) -> ComponentChecksum:
        """Creates a point-in-time hot backup of the SQLite operational database."""
        db_path = os.path.abspath(os.path.join(self.base_data_dir, "neural_company.db"))
        os.makedirs(os.path.dirname(destination_file), exist_ok=True)

        if os.path.exists(db_path):
            # Use SQLite Online Backup API for transactional consistency
            src_conn = sqlite3.connect(db_path)
            dst_conn = sqlite3.connect(destination_file)
            with dst_conn:
                src_conn.backup(dst_conn, pages=100)
            dst_conn.close()
            src_conn.close()
        else:
            # Create an empty initialized DB if none exists
            conn = sqlite3.connect(destination_file)
            conn.execute("CREATE TABLE IF NOT EXISTS _backup_marker (id INTEGER PRIMARY KEY, ts TEXT)")
            conn.commit()
            conn.close()

        sha256 = self._compute_sha256(destination_file)
        size = os.path.getsize(destination_file)
        return ComponentChecksum(
            component="database",
            relative_path="database/neural_company.db",
            sha256_hash=sha256,
            size_bytes=size,
        )

    def backup_directory(self, src_dir: str, dst_dir: str, component_name: str) -> list[ComponentChecksum]:
        """Recursively copies and checksums directory assets (artifacts, memory, configs)."""
        checksums: list[ComponentChecksum] = []
        if not os.path.exists(src_dir):
            return checksums

        for root, _, files in os.walk(src_dir):
            for file_name in files:
                src_file = os.path.join(root, file_name)
                rel_path = os.path.relpath(src_file, src_dir)
                dst_file = os.path.join(dst_dir, rel_path)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

                sha256 = self._compute_sha256(dst_file)
                size = os.path.getsize(dst_file)
                checksums.append(
                    ComponentChecksum(
                        component=component_name,
                        relative_path=f"{component_name}/{rel_path}".replace("\\", "/"),
                        sha256_hash=sha256,
                        size_bytes=size,
                    )
                )
        return checksums

    def create_backup(
        self,
        backup_id: Optional[str] = None,
        custom_output_archive: Optional[str] = None,
    ) -> tuple[str, BackupManifest]:
        """
        Executes a full system backup across all 7 operational domains:
        1. Operational database
        2. Organizational memory & vector embeddings
        3. Project workspaces and artifacts
        4. Employee profiles and metadata
        5. Audit & event history
        6. Evolution experiment records
        7. Model routing and company policies
        """
        start_time = time.perf_counter()
        bid = backup_id or f"ancos-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        staging_dir = os.path.join(self.backup_storage_dir, f"staging_{bid}")
        os.makedirs(staging_dir, exist_ok=True)

        manifest = BackupManifest(
            backup_id=bid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ancos_version="1.0.0-rc1",
        )

        try:
            # 1. Operational Database
            db_dst = os.path.join(staging_dir, "database", "neural_company.db")
            db_chk = self.backup_database(db_dst)
            manifest.components.append(db_chk)

            # 2. Project Workspaces & Artifacts
            workspaces_src = os.path.join(self.base_data_dir, "workspaces")
            workspaces_dst = os.path.join(staging_dir, "artifacts")
            manifest.components.extend(self.backup_directory(workspaces_src, workspaces_dst, "artifacts"))

            # 3. Model Configurations & Company Policies
            configs_dst = os.path.join(staging_dir, "configs")
            manifest.components.extend(self.backup_directory(self.configs_dir, configs_dst, "configs"))

            # 4. Vector Storage (Chroma) if present
            chroma_src = os.path.join(self.base_data_dir, "chroma")
            if os.path.exists(chroma_src):
                chroma_dst = os.path.join(staging_dir, "memory", "chroma")
                manifest.components.extend(self.backup_directory(chroma_src, chroma_dst, "memory/chroma"))

            # 5. Write manifest
            manifest_path = os.path.join(staging_dir, "manifest.json")
            manifest.total_size_bytes = sum(c.size_bytes for c in manifest.components)
            manifest.duration_seconds = round(time.perf_counter() - start_time, 3)

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(asdict(manifest), f, indent=2)

            # 6. Package into tar.gz archive
            archive_path = custom_output_archive or os.path.join(self.backup_storage_dir, f"{bid}.tar.gz")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(staging_dir, arcname=bid)

            logger.info(
                "Successfully created ANCOS system backup archive",
                backup_id=bid,
                components=len(manifest.components),
                total_size_bytes=manifest.total_size_bytes,
                archive_path=archive_path,
            )
            return archive_path, manifest

        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def verify_backup_integrity(self, archive_path: str) -> bool:
        """Validates that all components in the backup archive match their manifest SHA-256 hashes."""
        if not os.path.exists(archive_path):
            raise StorageError(f"Backup archive '{archive_path}' does not exist")

        verify_dir = os.path.join(self.backup_storage_dir, "verify_staging")
        os.makedirs(verify_dir, exist_ok=True)

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(path=verify_dir)

            # Locate extracted root containing manifest.json
            root = None
            if os.path.exists(os.path.join(verify_dir, "manifest.json")):
                root = verify_dir
            else:
                for item in os.listdir(verify_dir):
                    sub = os.path.join(verify_dir, item)
                    if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "manifest.json")):
                        root = sub
                        break
            if not root:
                return False

            manifest_file = os.path.join(root, "manifest.json")
            if not os.path.exists(manifest_file):
                return False

            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for item in data.get("components", []):
                rel_path = item["relative_path"]
                expected_hash = item["sha256_hash"]
                target_file = os.path.join(root, rel_path)
                if not os.path.exists(target_file):
                    logger.error("Missing component file in backup", path=rel_path)
                    return False
                computed_hash = self._compute_sha256(target_file)
                if computed_hash != expected_hash:
                    logger.error("Hash mismatch in backup component", path=rel_path)
                    return False

            return True
        finally:
            shutil.rmtree(verify_dir, ignore_errors=True)

    def restore_backup(self, archive_path: str, target_dir: str) -> RestorationReport:
        """
        Restores full ANCOS environment from backup into a clean target directory,
        verifying database integrity and component fidelity.
        """
        start_time = time.perf_counter()
        target_dir = os.path.abspath(target_dir)
        os.makedirs(target_dir, exist_ok=True)

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=target_dir)

        # Locate extracted top directory containing manifest.json
        root = None
        if os.path.exists(os.path.join(target_dir, "manifest.json")):
            root = target_dir
        else:
            for item in os.listdir(target_dir):
                sub = os.path.join(target_dir, item)
                if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "manifest.json")):
                    root = sub
                    break
        if not root:
            root = target_dir

        manifest_file = os.path.join(root, "manifest.json")
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        verified_count = 0
        for comp in manifest_data.get("components", []):
            fpath = os.path.join(root, comp["relative_path"])
            if os.path.exists(fpath):
                if self._compute_sha256(fpath) == comp["sha256_hash"]:
                    verified_count += 1

        # Check SQLite database integrity
        db_path = os.path.join(root, "database", "neural_company.db")
        db_ok = False
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                res = cursor.fetchone()
                db_ok = res and res[0] == "ok"
                conn.close()
            except Exception as e:
                logger.error("Restored database integrity check failed", error=str(e))
                db_ok = False

        duration = round(time.perf_counter() - start_time, 3)
        status = "SUCCESS" if db_ok and verified_count == len(manifest_data.get("components", [])) else "FAILED"

        return RestorationReport(
            backup_id=manifest_data.get("backup_id", "unknown"),
            target_dir=target_dir,
            duration_seconds=duration,
            verified_components=verified_count,
            db_integrity_ok=db_ok,
            status=status,
            details={"manifest": manifest_data},
        )

