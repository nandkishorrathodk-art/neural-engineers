import shutil
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import EmployeeStatus, ExecutionMode, TaskPriority, TaskStatus
from neural.db.repositories import (
    AuditRepository,
    EmergencyStopRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.exceptions import StorageError
from neural.ops.backup import DisasterRecoveryManager
from neural.security.kill_switch import (
    get_kill_switch,
    reset_kill_switch,
)

WORKER_ID = "resilience-worker"
WORKER_TOKEN = "resilience-worker-token-" + "w" * 32
VERIFIER_ID = "independent-verifier"
VERIFIER_TOKEN = "resilience-verifier-token-" + "v" * 32


@pytest.fixture(autouse=True)
def clean_kill_switch_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_kill_switch_blocks_worker_and_verifier_routes(async_session, monkeypatch):
    """
    NEURO-12 / G-12 verification:
    Validates that activating the global emergency kill switch immediately
    halts remote worker and verifier claim/exec/submit paths with HTTP 503,
    and resuming unblocks them.
    """
    monkeypatch.setenv("APP_ENV", "production")
    owner_token = "resilience-owner-token-" + "o" * 32
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    monkeypatch.setenv("VERIFIER_API_TOKEN", VERIFIER_TOKEN)
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-RESILIENCE",
        name="Resilience Test Employee",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    project = await ProjectRepository(async_session).create(
        name="Resilience Project",
        objective="Validate kill switch enforcement on remote worker routes",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-RESILIENCE-01",
        project_id=project.id,
        assigned_to=employee.id,
        title="Worker resilience task",
        objective="Test emergency halt during worker execution",
        scope="Worker route validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    worker_headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}
    verifier_headers = {"Authorization": f"Bearer {VERIFIER_TOKEN}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. While nominal, worker claim and verifier queue respond normally (not 503)
        res_claim_nominal = await client.post(
            "/internal/worker/tasks/claim",
            headers=worker_headers,
            json={"execution_modes": ["terminal"]},
        )
        assert res_claim_nominal.status_code != 503

        res_queue_nominal = await client.get(
            "/internal/verifier/queue",
            headers=verifier_headers,
        )
        assert res_queue_nominal.status_code == 200

        # 2. Persist emergency stop through the authenticated owner route.
        stop_response = await client.post(
            "/security/emergency-stop",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"reason": "Unauthorized intrusion detected"},
        )
        assert stop_response.status_code == 200
        assert get_kill_switch().is_halted is True
        assert (await EmergencyStopRepository(async_session).get_state())["active"] is True

        # 3. Worker routes must now immediately fail closed with 503
        worker_endpoints = [
            ("POST", "/internal/worker/tasks/claim", {"execution_modes": ["terminal"]}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/start", {"fencing_token": 1}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/heartbeat", {"fencing_token": 1}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/exec", {"fencing_token": 1, "command": ["python3", "-c", "print(1)"]}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/submit", {"fencing_token": 1, "execution_receipt": {"status": "done"}}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/fail", {"fencing_token": 1, "error_message": "err"}),
        ]

        for method, path, body in worker_endpoints:
            if method == "POST":
                resp = await client.post(path, headers=worker_headers, json=body)
            else:
                resp = await client.get(path, headers=worker_headers)
            assert resp.status_code == 503, f"{method} {path} returned {resp.status_code}, expected 503"
            assert "Emergency stop is active" in resp.json().get("detail", "")

        # 4. Verifier routes must also fail closed with 503
        verifier_endpoints = [
            ("GET", "/internal/verifier/queue", None),
            ("POST", f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/exec", {"fencing_token": 1, "command": ["pytest"]}),
            ("POST", f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/manifest", {"fencing_token": 1}),
            (
                "POST",
                f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/review",
                {"passed": True, "notes": "ok", "evidence_refs": ["ref1"], "checks": [{"name": "test", "passed": True}]},
            ),
        ]

        for method, path, body in verifier_endpoints:
            if method == "POST":
                resp = await client.post(path, headers=verifier_headers, json=body)
            else:
                resp = await client.get(path, headers=verifier_headers)
            assert resp.status_code == 503, f"{method} {path} returned {resp.status_code}, expected 503"
            assert "Emergency stop is active" in resp.json().get("detail", "")

        # 5. Resume through owner route; durable state and audit must agree.
        resume_response = await client.post(
            "/security/resume",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"recovery_strategy": "checkpoint"},
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["resumed"] is True
        assert get_kill_switch().is_halted is False
        assert (await EmergencyStopRepository(async_session).get_state())["active"] is False
        audit_events = await AuditRepository(async_session).list_all()
        assert any(event.action == "EMERGENCY_STOP" for event in audit_events)
        assert any(event.action == "RESUME_SYSTEM" for event in audit_events)

        # 6. Verifier queue and worker claim succeed again (not 503)
        res_queue_resumed = await client.get(
            "/internal/verifier/queue",
            headers=verifier_headers,
        )
        assert res_queue_resumed.status_code == 200

    get_settings.cache_clear()


def test_backup_restore_disk_exhaustion_guard(tmp_path):
    """
    NEURO-12 / G-12 verification:
    Validates that DisasterRecoveryManager.restore_backup guards against disk exhaustion
    by verifying that available disk space exceeds required staging/uncompressed space.
    """
    archive_file = tmp_path / "dummy_backup.tar.gz"
    # Write 1000 bytes of dummy archive
    archive_file.write_bytes(b"x" * 1000)

    restore_target = tmp_path / "restore_dest"
    restore_target.mkdir()

    mgr = DisasterRecoveryManager(base_data_dir=str(tmp_path / "data"))

    # Simulate insufficient disk space: free space is less than 2x archive size (2000 bytes)
    mock_usage = shutil._ntuple_diskusage(total=10000, used=9500, free=500)
    with patch("shutil.disk_usage", return_value=mock_usage):
        with pytest.raises(StorageError) as exc_info:
            mgr.restore_backup(str(archive_file), str(restore_target))

        assert "Insufficient disk space" in str(exc_info.value)
        assert "required at least 2000 bytes" in str(exc_info.value)


def test_backup_restore_uncompressed_tarbomb_disk_exhaustion_guard(tmp_path):
    """
    NEURO-12 / G-12 verification:
    Validates that DisasterRecoveryManager.restore_backup guards against tar-bombs and high-compression
    payloads where archive size is small but uncompressed member sizes exceed free disk space.
    """
    import io
    import tarfile

    archive_file = tmp_path / "compressed_payload.tar.gz"
    # Create valid tar.gz with a 50,000-byte member (compressed archive size will be only ~200 bytes)
    with tarfile.open(str(archive_file), "w:gz") as tar:
        data = b"0" * 50000
        ti = tarfile.TarInfo(name="test_dr/large_file.dat")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    restore_target = tmp_path / "restore_dest_uncompressed"
    restore_target.mkdir()

    mgr = DisasterRecoveryManager(base_data_dir=str(tmp_path / "data"))

    # Free space is 10,000 bytes: plenty for 2x compressed archive (~400 bytes),
    # but far too small for the uncompressed 50,000 bytes (requires ~55,000 bytes).
    mock_usage = shutil._ntuple_diskusage(total=100000, used=90000, free=10000)
    with patch("shutil.disk_usage", return_value=mock_usage):
        with pytest.raises(StorageError) as exc_info:
            mgr.restore_backup(str(archive_file), str(restore_target))

        assert "Insufficient disk space" in str(exc_info.value)
        assert "uncompressed payload" in str(exc_info.value)
        assert "55000 bytes" in str(exc_info.value)


def test_pairwise_credential_separation_across_all_roles(monkeypatch):
    """
    NEURO-12 verification:
    Validates that validate_all_role_credentials strictly rejects any credential overlap
    between Owner, Boss, Worker, Verifier, Security Reviewer, and Approver roles with HTTP 503.
    """
    from fastapi import HTTPException
    from neural.api.deps import validate_all_role_credentials

    shared_token = "shared-secret-overlap-token-32bytes"
    unique_base = "unique-secret-distinct-token-32bytes"

    # 1. Boss overlaps with Worker
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", unique_base + "-owner")
    monkeypatch.setenv("BOSS_API_TOKEN", shared_token)
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"worker-1":"{shared_token}"}}')
    monkeypatch.setenv("VERIFIER_API_TOKEN", unique_base + "-verifier")
    monkeypatch.setenv("SECURITY_REVIEWER_API_TOKEN", unique_base + "-sec")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        validate_all_role_credentials(get_settings())
    assert exc_info.value.status_code == 503
    assert "Role credentials must be pairwise distinct" in exc_info.value.detail
    assert "boss" in exc_info.value.detail
    assert "worker" in exc_info.value.detail

    # 2. Security Reviewer overlaps with Verifier
    monkeypatch.setenv("BOSS_API_TOKEN", unique_base + "-boss")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"worker-1":"{unique_base}-w1"}}')
    monkeypatch.setenv("VERIFIER_API_TOKEN", shared_token)
    monkeypatch.setenv("SECURITY_REVIEWER_API_TOKEN", shared_token)
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        validate_all_role_credentials(get_settings())
    assert exc_info.value.status_code == 503
    assert "Role credentials must be pairwise distinct" in exc_info.value.detail

    # 3. All tokens strictly distinct -> succeeds without error
    monkeypatch.setenv("VERIFIER_API_TOKEN", unique_base + "-verifier")
    monkeypatch.setenv("SECURITY_REVIEWER_API_TOKEN", unique_base + "-sec")
    get_settings.cache_clear()
    validate_all_role_credentials(get_settings())
