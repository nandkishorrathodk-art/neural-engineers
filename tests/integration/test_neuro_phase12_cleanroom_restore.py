"""Clean-room restore acceptance for a real ANCOS operational schema."""

from __future__ import annotations

import json
import sqlite3
import tarfile

from neural.db.models import Base, Employee
from neural.ops.backup import DisasterRecoveryManager
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _populated_operational_store(tmp_path):
    data_dir = tmp_path / "source-data"
    configs_dir = tmp_path / "source-configs"
    workspace = data_dir / "workspaces" / "ANC-EMP-RESTORE"
    workspace.mkdir(parents=True)
    configs_dir.mkdir()
    artifact = workspace / "deliverable.md"
    artifact.write_text("Verified release notes\n", encoding="utf-8")
    (configs_dir / "policy.json").write_text('{"mode":"safe"}\n', encoding="utf-8")

    db_path = data_dir / "neural_company.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Employee(
                employee_id="ANC-EMP-RESTORE",
                name="Restore Validation Employee",
                title="Integration Engineer",
                department="Engineering",
                system_prompt="Temporary clean-room restore validation identity.",
            )
        )
        session.commit()
    engine.dispose()
    return data_dir, configs_dir, artifact


def test_clean_room_restore_recovers_operational_schema_and_artifacts(tmp_path):
    data_dir, configs_dir, artifact = _populated_operational_store(tmp_path)
    manager = DisasterRecoveryManager(str(data_dir), str(configs_dir))
    archive, manifest = manager.create_backup("neuro12-clean-room")

    target = tmp_path / "clean-room"
    report = manager.restore_backup(archive, str(target))

    assert report.status == "SUCCESS"
    assert report.db_integrity_ok is True
    assert report.verified_components == len(manifest.components)
    restored_root = target / "neuro12-clean-room"
    restored_db = restored_root / "database" / "neural_company.db"
    with sqlite3.connect(f"file:{restored_db}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        employee = connection.execute(
            "SELECT employee_id, name, title FROM employees WHERE employee_id = ?",
            ("ANC-EMP-RESTORE",),
        ).fetchone()
    assert employee == (
        "ANC-EMP-RESTORE",
        "Restore Validation Employee",
        "Integration Engineer",
    )

    restored_artifact = (
        restored_root / "artifacts" / "ANC-EMP-RESTORE" / artifact.name
    )
    assert restored_artifact.read_text(encoding="utf-8") == "Verified release notes\n"
    assert (restored_root / "configs" / "policy.json").read_text(encoding="utf-8") == (
        '{"mode":"safe"}\n'
    )
    assert manager.verify_backup_integrity(archive) is True


def test_clean_room_restore_rejects_tampered_artifact_without_publishing_partial_tree(
    tmp_path,
):
    data_dir, configs_dir, _ = _populated_operational_store(tmp_path)
    manager = DisasterRecoveryManager(str(data_dir), str(configs_dir))
    archive, _ = manager.create_backup("neuro12-tampered-clean-room")

    unpacked = tmp_path / "tamper-staging"
    unpacked.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(unpacked, filter="data")
    root = unpacked / "neuro12-tampered-clean-room"
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    component = next(
        item
        for item in manifest["components"]
        if item["relative_path"].startswith("artifacts/")
    )
    (root / component["relative_path"]).write_text("tampered\n", encoding="utf-8")
    tampered_archive = tmp_path / "tampered-clean-room.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as bundle:
        bundle.add(root, arcname=root.name)

    target = tmp_path / "restore-target"
    target.mkdir()
    report = manager.restore_backup(str(tampered_archive), str(target))

    assert report.status == "FAILED"
    assert report.verified_components < len(manifest["components"])
    assert list(target.iterdir()) == []
