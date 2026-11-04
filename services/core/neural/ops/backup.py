from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
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

    @staticmethod
    def _safe_archive_filter(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo:
        """Allow only regular files/directories with portable relative archive paths."""
        archive_path = PurePosixPath(member.name.replace("\\", "/"))
        windows_path = PureWindowsPath(member.name)
        if (
            archive_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in archive_path.parts
        ):
            raise tarfile.FilterError("Backup archive contains an unsafe member path.")

        filtered = tarfile.data_filter(member, destination)
        if filtered is None or not (filtered.isdir() or filtered.isfile()):
            raise tarfile.FilterError("Backup archive contains a non-file or link member.")
        return filtered

    @staticmethod
    def _manifest_component_path(root: str, relative_path: Any) -> str:
        """Resolve a manifest path only when it remains inside the extracted root."""
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise StorageError("Backup manifest contains an invalid component path.")

        normalized = relative_path.replace("\\", "/")
        relative = PurePosixPath(normalized)
        windows = PureWindowsPath(relative_path)
        if (
            relative.is_absolute()
            or windows.is_absolute()
            or windows.drive
            or ".." in relative.parts
        ):
            raise StorageError("Backup manifest component path escapes the backup root.")

        root_path = Path(root).resolve()
        component_path = root_path.joinpath(*relative.parts).resolve(strict=False)
        if not component_path.is_relative_to(root_path):
            raise StorageError("Backup manifest component path escapes the backup root.")
        return str(component_path)

    @classmethod
    def _validate_manifest_components(
        cls, root: str, manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        components = manifest.get("components")
        if not isinstance(components, list) or not components:
            raise StorageError("Backup manifest must list at least the operational database.")

        seen_paths: set[str] = set()
        has_database = False
        for component in components:
            if not isinstance(component, dict):
                raise StorageError("Backup manifest contains an invalid component record.")
            relative_path = component.get("relative_path")
            cls._manifest_component_path(root, relative_path)
            normalized_path = PurePosixPath(relative_path.replace("\\", "/")).as_posix()
            if normalized_path in seen_paths:
                raise StorageError("Backup manifest contains duplicate component paths.")
            seen_paths.add(normalized_path)
            if not isinstance(component.get("sha256_hash"), str):
                raise StorageError("Backup manifest contains an invalid component checksum.")
            if normalized_path == "database/neural_company.db":
                has_database = True
        if not has_database:
            raise StorageError("Backup manifest does not include the operational database.")
        return components

    @staticmethod
    def _find_backup_root(extraction_dir: str) -> str:
        if os.path.isfile(os.path.join(extraction_dir, "manifest.json")):
            return extraction_dir
        roots = [
            os.path.join(extraction_dir, item)
            for item in os.listdir(extraction_dir)
            if os.path.isdir(os.path.join(extraction_dir, item))
            and os.path.isfile(os.path.join(extraction_dir, item, "manifest.json"))
        ]
        if len(roots) != 1:
            raise StorageError("Backup archive must contain exactly one root manifest.")
        return roots[0]

    @staticmethod
    def _restore_directory_name(root: str, extraction_dir: str, manifest: dict[str, Any]) -> str:
        name = os.path.basename(root) if root != extraction_dir else manifest.get("backup_id")
        if not isinstance(name, str) or not name.strip() or name in {".", ".."}:
            raise StorageError("Backup manifest contains an invalid backup identifier.")
        if "/" in name or "\\" in name or PureWindowsPath(name).drive:
            raise StorageError("Backup identifier cannot be used as a restore directory name.")
        return name

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

        source_root = os.path.realpath(src_dir)

        for root, _, files in os.walk(src_dir):
            for file_name in files:
                src_file = os.path.join(root, file_name)
                try:
                    source_stat = os.lstat(src_file)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(source_stat.st_mode):
                    logger.warning("Skipping non-regular backup source", path=src_file)
                    continue
                resolved_source = os.path.realpath(src_file)
                try:
                    if os.path.commonpath((source_root, resolved_source)) != source_root:
                        logger.warning("Skipping backup source outside component root", path=src_file)
                        continue
                except ValueError:
                    logger.warning("Skipping backup source outside component root", path=src_file)
                    continue

                rel_path = os.path.relpath(src_file, src_dir)
                dst_file = os.path.join(dst_dir, rel_path)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)

                source_fd: int | None = None
                try:
                    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    source_fd = os.open(src_file, open_flags)
                    opened_stat = os.fstat(source_fd)
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or (opened_stat.st_dev, opened_stat.st_ino)
                        != (source_stat.st_dev, source_stat.st_ino)
                    ):
                        logger.warning("Skipping backup source changed during inspection", path=src_file)
                        continue
                    with os.fdopen(source_fd, "rb") as source_file:
                        source_fd = None
                        with open(dst_file, "wb") as destination_file:
                            shutil.copyfileobj(source_file, destination_file)
                except OSError:
                    if os.path.islink(src_file):
                        logger.warning("Skipping symbolic link in backup source", path=src_file)
                        continue
                    raise
                finally:
                    if source_fd is not None:
                        os.close(source_fd)

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

        try:
            with tempfile.TemporaryDirectory(
                prefix="verify_", dir=self.backup_storage_dir
            ) as verify_dir:
                return self._verify_extracted_archive(archive_path, verify_dir)
        except (OSError, tarfile.TarError, ValueError, TypeError, KeyError, StorageError) as exc:
            logger.warning("Rejected backup archive during integrity verification", error=str(exc))
            return False

    def _verify_extracted_archive(self, archive_path: str, verify_dir: str) -> bool:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=verify_dir, filter=self._safe_archive_filter)

        try:
            root = self._find_backup_root(verify_dir)
            manifest_file = os.path.join(root, "manifest.json")
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            components = self._validate_manifest_components(root, data)

            for item in components:
                target_file = self._manifest_component_path(root, item.get("relative_path"))
                expected_hash = item.get("sha256_hash")
                if not isinstance(expected_hash, str) or not os.path.isfile(target_file):
                    logger.error("Missing or invalid component file in backup")
                    return False
                if self._compute_sha256(target_file) != expected_hash:
                    logger.error("Hash mismatch in backup component")
                    return False
            return True
        except (OSError, ValueError, TypeError, KeyError, StorageError) as exc:
            logger.warning("Rejected backup manifest during integrity verification", error=str(exc))
            return False

    def restore_backup(self, archive_path: str, target_dir: str) -> RestorationReport:
        """
        Restores full ANCOS environment from backup into a clean target directory,
        verifying database integrity and component fidelity.
        """
        start_time = time.perf_counter()
        target_dir = os.path.abspath(target_dir)
        os.makedirs(target_dir, exist_ok=True)
        stage_dir = tempfile.mkdtemp(prefix=".ancos-restore-", dir=target_dir)
        manifest_data: dict[str, Any] = {}
        verified_count = 0
        db_ok = False

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(path=stage_dir, filter=self._safe_archive_filter)

            root = self._find_backup_root(stage_dir)
            manifest_file = os.path.join(root, "manifest.json")
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            if not isinstance(manifest_data, dict):
                raise StorageError("Backup manifest must be a JSON object.")
            components = self._validate_manifest_components(root, manifest_data)

            for comp in components:
                component_path = self._manifest_component_path(root, comp.get("relative_path"))
                expected_hash = comp.get("sha256_hash")
                if not isinstance(expected_hash, str) or not os.path.isfile(component_path):
                    continue
                if self._compute_sha256(component_path) == expected_hash:
                    verified_count += 1

            # Check SQLite database integrity before exposing the restored files.
            db_path = self._manifest_component_path(root, "database/neural_company.db")
            if os.path.isfile(db_path):
                conn: sqlite3.Connection | None = None
                try:
                    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    res = conn.execute("PRAGMA integrity_check;").fetchone()
                    db_ok = bool(res and res[0] == "ok")
                except sqlite3.Error as exc:
                    logger.error("Restored database integrity check failed", error=str(exc))
                finally:
                    if conn is not None:
                        conn.close()

            duration = round(time.perf_counter() - start_time, 3)
            success = db_ok and verified_count == len(components)
            if success:
                restore_name = self._restore_directory_name(root, stage_dir, manifest_data)
                destination = os.path.join(target_dir, restore_name)
                if os.path.lexists(destination):
                    success = False
                else:
                    os.replace(root, destination)

            return RestorationReport(
                backup_id=str(manifest_data.get("backup_id", "unknown")),
                target_dir=target_dir,
                duration_seconds=duration,
                verified_components=verified_count,
                db_integrity_ok=db_ok,
                status="SUCCESS" if success else "FAILED",
                details={"manifest": manifest_data},
            )
        except (OSError, tarfile.TarError, ValueError, TypeError, KeyError, StorageError) as exc:
            logger.error("Rejected backup archive during restore", error=str(exc))
            raise StorageError("Backup archive failed safe extraction or validation.") from exc
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

