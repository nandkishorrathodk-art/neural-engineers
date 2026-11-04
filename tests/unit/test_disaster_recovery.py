from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import tarfile
from types import SimpleNamespace

import pytest
from neural.exceptions import StorageError
from neural.ops.backup import DisasterRecoveryManager


def test_disaster_recovery_backup_and_full_restoration(tmp_path):
    """
    Validates complete backup and clean-room disaster recovery:
    1. Creates populated mock database and artifacts.
    2. Runs DisasterRecoveryManager.create_backup().
    3. Verifies archive integrity and SHA-256 manifest.
    4. Restores into a completely clean directory.
    5. Checks PRAGMA integrity_check on restored SQLite DB and data fidelity.
    """
    # 1. Setup mock environment in tmp_path
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()

    # Create dummy config
    (configs / "policy.yaml").write_text("security: enforce_sandboxing: true", encoding="utf-8")

    # Create dummy workspace artifact
    workspace_dir = base_data / "workspaces" / "ANC-EMP-0001"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "main.py").write_text("print('hello world')", encoding="utf-8")

    # Create dummy operational SQLite database with table and row
    db_file = base_data / "neural_company.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE employees (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO employees VALUES ('ANC-EMP-0001', 'Alice Developer')")
    conn.commit()
    conn.close()

    # 2. Create backup
    dr = DisasterRecoveryManager(base_data_dir=str(base_data), configs_dir=str(configs))
    archive_path, manifest = dr.create_backup(backup_id="test-dr-01")

    assert os.path.exists(archive_path)
    assert len(manifest.components) >= 3
    assert manifest.total_size_bytes > 0
    assert manifest.backup_id == "test-dr-01"

    # 3. Verify integrity
    assert dr.verify_backup_integrity(archive_path) is True

    # 4. Restore into a clean environment
    clean_target = tmp_path / "restored_env"
    clean_target.mkdir()

    report = dr.restore_backup(archive_path, target_dir=str(clean_target))
    assert report.status == "SUCCESS"
    assert report.db_integrity_ok is True
    assert report.verified_components == len(manifest.components)
    assert report.duration_seconds >= 0.0

    restored_root = clean_target / "test-dr-01"
    preserved_file = restored_root / "preserve.txt"
    preserved_file.write_text("do not overwrite an existing restore", encoding="utf-8")
    collision_report = dr.restore_backup(archive_path, target_dir=str(clean_target))
    assert collision_report.status == "FAILED"
    assert preserved_file.read_text(encoding="utf-8") == "do not overwrite an existing restore"

    # 5. Verify restored database has the original data
    restored_db = restored_root / "database" / "neural_company.db"
    assert restored_db.exists()
    restored_conn = sqlite3.connect(str(restored_db))
    cursor = restored_conn.cursor()
    cursor.execute("SELECT name FROM employees WHERE id='ANC-EMP-0001'")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "Alice Developer"
    restored_conn.close()

    # 6. Test restoring into non-empty directory with sibling folders
    clean_target_multi = tmp_path / "restored_env_multi"
    clean_target_multi.mkdir()
    (clean_target_multi / "unrelated_dir").mkdir()
    (clean_target_multi / "unrelated_dir" / "dummy.txt").write_text("random")

    report_multi = dr.restore_backup(archive_path, target_dir=str(clean_target_multi))
    assert report_multi.status == "SUCCESS"
    assert report_multi.db_integrity_ok is True
    assert (clean_target_multi / "test-dr-01" / "database" / "neural_company.db").exists()

    # 7. Tamper verification test: corrupt a component inside archive
    import tarfile
    tampered_archive = tmp_path / "tampered.tar.gz"
    # Unpack, modify a file, and repack
    corrupt_staging = tmp_path / "corrupt_staging"
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=corrupt_staging, filter="data")
    corrupt_file = corrupt_staging / "test-dr-01" / "configs" / "policy.yaml"
    if corrupt_file.exists():
        corrupt_file.write_text("corrupted_content_tampered")
    with tarfile.open(tampered_archive, "w:gz") as tar:
        tar.add(corrupt_staging / "test-dr-01", arcname="test-dr-01")

    # Integrity verification must detect hash mismatch and return False
    assert dr.verify_backup_integrity(str(tampered_archive)) is False


def test_backup_verification_and_restore_reject_tar_path_traversal(tmp_path):
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    dr = DisasterRecoveryManager(base_data_dir=str(base_data), configs_dir=str(configs))
    archive = tmp_path / "traversal.tar.gz"
    payload = b"must not escape the extraction directory"

    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("../../../../escaped.txt")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    target = tmp_path / "restore-target"
    assert dr.verify_backup_integrity(str(archive)) is False
    with pytest.raises(StorageError, match="safe extraction or validation"):
        dr.restore_backup(str(archive), str(target))

    assert not list(tmp_path.rglob("escaped.txt"))
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_backup_verification_and_restore_reject_archive_links(tmp_path):
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    dr = DisasterRecoveryManager(base_data_dir=str(base_data), configs_dir=str(configs))
    archive = tmp_path / "link.tar.gz"

    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("bundle/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        tar.addfile(link)
        member = tarfile.TarInfo("bundle/link/escaped.txt")
        payload = b"must not follow an archive link"
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    target = tmp_path / "restore-target"
    assert dr.verify_backup_integrity(str(archive)) is False
    with pytest.raises(StorageError, match="safe extraction or validation"):
        dr.restore_backup(str(archive), str(target))

    assert not list(tmp_path.rglob("escaped.txt"))
    assert list(target.iterdir()) == []


def test_backup_manifest_paths_must_remain_inside_extracted_root(tmp_path):
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    dr = DisasterRecoveryManager(base_data_dir=str(base_data), configs_dir=str(configs))
    archive = tmp_path / "manifest-traversal.tar.gz"
    manifest = {
        "backup_id": "manifest-traversal",
        "components": [{
            "relative_path": "../../outside/secret.txt",
            "sha256_hash": "0" * 64,
            "size_bytes": 1,
            "component": "test",
        }],
    }
    payload = json.dumps(manifest).encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("manifest-traversal/manifest.json")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    target = tmp_path / "restore-target"
    assert dr.verify_backup_integrity(str(archive)) is False
    with pytest.raises(StorageError, match="safe extraction or validation"):
        dr.restore_backup(str(archive), str(target))
    assert list(target.iterdir()) == []


def test_backup_manifest_must_include_the_operational_database(tmp_path):
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    dr = DisasterRecoveryManager(base_data_dir=str(base_data), configs_dir=str(configs))
    archive = tmp_path / "missing-database.tar.gz"
    payload = json.dumps({"backup_id": "missing-database", "components": []}).encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("missing-database/manifest.json")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    target = tmp_path / "restore-target"
    assert dr.verify_backup_integrity(str(archive)) is False
    with pytest.raises(StorageError, match="safe extraction or validation"):
        dr.restore_backup(str(archive), str(target))
    assert list(target.iterdir()) == []


def test_backup_directory_skips_symbolic_link_sources(tmp_path, monkeypatch):
    base_data = tmp_path / "data"
    base_data.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    source_dir = tmp_path / "workspace"
    source_dir.mkdir()
    candidate = source_dir / "linked-secret.txt"
    candidate.write_text("outside content must not be copied", encoding="utf-8")
    real_lstat = os.lstat

    def lstat_with_link_candidate(path):
        if os.fspath(path) == str(candidate):
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=1, st_ino=2)
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", lstat_with_link_candidate)
    destination = tmp_path / "backup-staging" / "artifacts"
    checksums = DisasterRecoveryManager(
        base_data_dir=str(base_data), configs_dir=str(configs)
    ).backup_directory(str(source_dir), str(destination), "artifacts")

    assert checksums == []
    assert not (destination / "linked-secret.txt").exists()

