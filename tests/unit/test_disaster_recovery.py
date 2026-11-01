from __future__ import annotations

import os
import sqlite3

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

    # 5. Verify restored database has the original data
    restored_db = clean_target / "test-dr-01" / "database" / "neural_company.db"
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
        tar.extractall(path=corrupt_staging)
    corrupt_file = corrupt_staging / "test-dr-01" / "configs" / "policy.yaml"
    if corrupt_file.exists():
        corrupt_file.write_text("corrupted_content_tampered")
    with tarfile.open(tampered_archive, "w:gz") as tar:
        tar.add(corrupt_staging / "test-dr-01", arcname="test-dr-01")

    # Integrity verification must detect hash mismatch and return False
    assert dr.verify_backup_integrity(str(tampered_archive)) is False

