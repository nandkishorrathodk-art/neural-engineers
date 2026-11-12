"""
ANCOS NEURO-12 Live Acceptance Test Suite.
Empirically verifies Security, Reliability & Operational Resilience across:
1. Scoped Role Authentication, Authorization & Anti-Privilege Escalation
   (Owner, Boss, Worker, Verifier, Security Reviewer; distinct bearer token validation;
   rejection of unauthorized actions, cross-role impersonation, and short/malformed credentials).
2. Zero-Trust Container Broker & Network Perimeter Containment
   (RestrictedDockerBroker naming bounds, dropped caps, no-new-privileges, PIDs limits,
   prohibited host socket/root mounts; NetworkSecurityPerimeter anti-SSRF link-local
   metadata 169.254.169.254, hex/int IP evasion, and RFC1918 private subnet blocking).
3. Emergency Stop & Durable Kill-Switch Fail-Closed Behavior
   (/boss/emergency-stop and /security/emergency-stop set persistent shared DB latch,
   sub-200ms latency, cancel running task attempts, expire attempt leases; HTTP 503 on
   all worker/verifier routes and system readiness probes).
4. Emergency Resume, Monotonic Fencing Increment & Task Requeue
   (/boss/emergency-resume and /security/resume safely requeue interrupted tasks to PENDING,
   monotonically increment task fencing tokens, release expired resource leases, and clear latch).
5. Durable Worker Crash & Session Loss Recovery with Monotonic Fencing Tokens
   (Zombie/stale worker attempts using superseded fencing token are strictly rejected
   with HTTP 409 Conflict; rescheduled worker succeeds with incremented fencing token).
6. Transactional Outbox Persistence & Process Crash Recovery
   (Transactional commits prevent ghost events; background outbox worker crash/restart
   resumes dispatching pending events with monotonic sequence ordering and zero event loss).
7. Disaster Recovery & Clean-Room Backup/Restore with RTO/RPO Validation
   (DisasterRecoveryManager hot SQLite backup, SHA-256 component manifests, clean-room
   full restore, SQLite PRAGMA integrity_check, tampered archive rejection, and RTO/RPO validation).
8. Cryptographic Audit Trail Hash-Chain Integrity
   (AuditLogChainVerifier traverses SHA-256 immutable hash chain from GENESIS_AUDIT_BLOCK,
   verifying tamper-evident link integrity, request_hash matching, and tamper detection).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    SecurityRole,
    TaskPriority,
    TaskStatus,
)
from neural.containers.broker import RestrictedDockerBroker
from neural.db.models import Base, Employee, OutboxEvent
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmergencyStopRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.events.outbox import (
    OutboxEventPublisher,
    OutboxSSEBroadcaster,
    OutboxWorker,
)
from neural.exceptions import (
    AuditIntegrityError,
    NetworkPerimeterViolation,
    SecurityViolationError,
    StorageError,
)
from neural.observability.audit import AuditLogChainVerifier
from neural.ops.backup import DisasterRecoveryManager
from neural.security.kill_switch import get_kill_switch, reset_kill_switch
from neural.security.network import NetworkSecurityPerimeter
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

# Scoped test bearer tokens (>= 32 characters each)
OWNER_TOKEN = "ancos-owner-sec-resilience-token-32b"
BOSS_TOKEN = "ancos-boss-orchestrator-token-32bytes"
WORKER_ID_1 = "worker-primary-node"
WORKER_TOKEN_1 = "ancos-worker-node-alpha-token-32bytes"
WORKER_ID_2 = "worker-standby-node"
WORKER_TOKEN_2 = "ancos-worker-node-bravo-token-32bytes"
VERIFIER_TOKEN = "ancos-verifier-independent-token-32b"
APPROVER_TOKEN = "ancos-approver-sovereign-token-32by"
SECURITY_REVIEWER_TOKEN = "ancos-sec-reviewer-token-32bytes-long"


class FakeContainerManager:
    def __init__(self):
        self.desktops: dict[str, dict[str, Any]] = {}

    async def get_desktop_by_employee_id(self, employee_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.desktops.get(employee_id)

    async def capture_verifier_manifest(self, employee_id: str) -> dict[str, Any]:
        return {"manifest": "initial", "entries": []}


@pytest.fixture(autouse=True)
def clean_security_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase12_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch,
):
    """
    Comprehensive 8-step live acceptance scenario for ANCOS NEURO-12.
    Empirically verifies all Security, Reliability & Operational Resilience pillars.
    """
    # -------------------------------------------------------------------------
    # STEP 1: Scoped Role Authentication, Authorization & Anti-Privilege Escalation
    # -------------------------------------------------------------------------
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("BOSS_API_TOKEN", BOSS_TOKEN)
    monkeypatch.setenv("APPROVER_API_TOKEN", APPROVER_TOKEN)
    monkeypatch.setenv(
        "WORKER_API_TOKENS",
        json.dumps({WORKER_ID_1: WORKER_TOKEN_1, WORKER_ID_2: WORKER_TOKEN_2}),
    )
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    monkeypatch.setenv("VERIFIER_API_TOKEN", VERIFIER_TOKEN)
    monkeypatch.setenv("SECURITY_REVIEWER_API_TOKEN", SECURITY_REVIEWER_TOKEN)
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.OWNER_API_TOKEN == OWNER_TOKEN
    assert settings.BOSS_API_TOKEN == BOSS_TOKEN
    assert settings.VERIFIER_API_TOKEN == VERIFIER_TOKEN
    assert settings.SECURITY_REVIEWER_API_TOKEN == SECURITY_REVIEWER_TOKEN

    # Verify all roles are represented in SecurityRole enum
    assert SecurityRole.OWNER.value == "owner"
    assert SecurityRole.BOSS.value == "boss"
    assert SecurityRole.WORKER.value == "worker"
    assert SecurityRole.VERIFIER.value == "verifier"
    assert SecurityRole.SECURITY_REVIEWER.value == "security_reviewer"

    app = create_app()
    fake_cm = FakeContainerManager()
    fake_cm.desktops["ANC-EMP-SEC-01"] = {
        "status": "running",
        "docker_id": "ancos-desktop-anc-emp-sec-01",
    }

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_container_manager] = lambda: fake_cm

    owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    boss_headers = {"Authorization": f"Bearer {BOSS_TOKEN}"}
    worker1_headers = {"Authorization": f"Bearer {WORKER_TOKEN_1}"}
    verifier_headers = {"Authorization": f"Bearer {VERIFIER_TOKEN}"}
    sec_headers = {"Authorization": f"Bearer {SECURITY_REVIEWER_TOKEN}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # A. Unauthenticated requests to protected endpoints must fail with 401
        res_no_auth = await client.post("/boss/emergency-stop", json={"reason": "test"})
        assert res_no_auth.status_code == 401

        res_no_auth_sec = await client.post("/security/emergency-stop", json={"reason": "test"})
        assert res_no_auth_sec.status_code == 401

        res_no_auth_worker = await client.post(
            "/internal/worker/tasks/claim", json={"execution_modes": ["terminal"]}
        )
        assert res_no_auth_worker.status_code == 401

        # B. Malformed / too-short token rejected in production
        res_short = await client.post(
            "/boss/emergency-stop",
            headers={"Authorization": "Bearer too-short"},
            json={"reason": "test"},
        )
        assert res_short.status_code in (401, 503)

        # C. Controlled Unauthorized-Action Rejection & Anti-Privilege Escalation:
        # Worker bearer token attempting Boss route (/boss/emergency-stop) must be rejected
        res_worker_on_boss = await client.post(
            "/boss/emergency-stop",
            headers=worker1_headers,
            json={"reason": "Worker attempting boss halt"},
        )
        assert res_worker_on_boss.status_code == 401

        # Verifier bearer token attempting Boss route (/boss/emergency-stop) must be rejected
        res_verifier_on_boss = await client.post(
            "/boss/emergency-stop",
            headers=verifier_headers,
            json={"reason": "Verifier attempting boss halt"},
        )
        assert res_verifier_on_boss.status_code == 401

        # Worker bearer token attempting Security route (/security/emergency-stop) must be rejected
        res_worker_on_sec = await client.post(
            "/security/emergency-stop",
            headers=worker1_headers,
            json={"reason": "Worker attempting security halt"},
        )
        assert res_worker_on_sec.status_code == 401

        # Boss token attempting Worker endpoint (/internal/worker/tasks/claim) must be rejected
        res_boss_on_worker = await client.post(
            "/internal/worker/tasks/claim",
            headers=boss_headers,
            json={"execution_modes": ["terminal"]},
        )
        assert res_boss_on_worker.status_code == 401

        # Security Reviewer token attempting Verifier endpoint (/internal/verifier/queue) must be rejected
        res_sec_on_verifier = await client.get(
            "/internal/verifier/queue",
            headers=sec_headers,
        )
        assert res_sec_on_verifier.status_code == 401

        # Verifier bearer token attempting Worker endpoint (/internal/worker/tasks/claim) must be rejected
        res_verifier_on_worker = await client.post(
            "/internal/worker/tasks/claim",
            headers=verifier_headers,
            json={"execution_modes": ["terminal"]},
        )
        assert res_verifier_on_worker.status_code == 401

        # Worker bearer token attempting Verifier endpoint (/internal/verifier/queue) must be rejected
        res_worker_on_verifier = await client.get(
            "/internal/verifier/queue",
            headers=worker1_headers,
        )
        assert res_worker_on_verifier.status_code == 401

        # D. Authorized role access verification across scoped identities
        # Valid boss token on /boss/status succeeds
        res_boss_status = await client.get("/boss/status", headers=boss_headers)
        assert res_boss_status.status_code == 200

        # Valid security reviewer token on /security/status succeeds
        res_sec_status = await client.get("/security/status", headers=sec_headers)
        assert res_sec_status.status_code == 200

        # Valid owner token on /security/status succeeds
        res_status = await client.get("/security/status", headers=owner_headers)
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["kill_switch_active"] is False

    # -------------------------------------------------------------------------
    # STEP 2: Zero-Trust Container Broker & Network Perimeter Containment
    # -------------------------------------------------------------------------
    workspace_base = tmp_path / "workspaces"
    workspace_base.mkdir(parents=True)
    emp_workspace = workspace_base / "ANC-EMP-SEC-01"
    emp_workspace.mkdir()

    broker = RestrictedDockerBroker(workspace_base=str(workspace_base))

    # A. Container naming enforcement
    broker.validate_container_id("ancos-desktop-anc-emp-0001")
    broker.validate_container_id("ancos-desktop-anc-emp-sec-01")

    rogue_names = ["ancos-core-api", "docker-daemon", "evil-box", "ancos-desktop-root", "host-vm"]
    for rogue in rogue_names:
        with pytest.raises(SecurityViolationError, match="Unauthorized container target"):
            broker.validate_container_id(rogue)

    # B. Workstation hardening kwargs
    insecure_spec = {
        "name": "ancos-desktop-anc-emp-sec-01",
        "image": "ancos/employee-base:latest",
        "privileged": True,
        "user": "0:0",
        "cap_add": ["SYS_ADMIN", "NET_ADMIN"],
        "pids_limit": 5000,
        "volumes": {
            str(emp_workspace): {"bind": "/home/employee/workspace", "mode": "rw"}
        },
    }
    hardened = broker.harden_container_kwargs(insecure_spec)
    assert hardened["privileged"] is False
    assert hardened["user"] == "1000:1000"
    assert hardened["cap_drop"] == ["ALL"]
    assert "cap_add" not in hardened
    assert "no-new-privileges:true" in hardened["security_opt"]
    assert hardened["pids_limit"] <= 200

    # C. Host socket and root volume mount prohibition
    with pytest.raises(SecurityViolationError, match="cannot mount host system path"):
        broker.validate_volume_mounts({
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}
        })

    with pytest.raises(SecurityViolationError, match="cannot mount host system path"):
        broker.validate_volume_mounts({
            "/": {"bind": "/home/employee/workspace", "mode": "rw"}
        })

    with pytest.raises(SecurityViolationError, match="outside designated workspace base"):
        broker.validate_volume_mounts({
            str(tmp_path / "outside"): {"bind": "/home/employee/workspace", "mode": "rw"}
        })

    # D. Network Perimeter Containment (SSRF, metadata, private subnets)
    perimeter = NetworkSecurityPerimeter(allow_internal_networks=False, block_metadata=True)

    # Cloud metadata blocks
    metadata_targets = [
        "169.254.169.254",
        "http://169.254.169.254/latest/meta-data/",
        "metadata.google.internal",
        "http://100.100.100.200/computeMetadata/v1/",
        "fd00:ec2::254",
        "2852039166",  # Integer representation of 169.254.169.254
        "0xA9FEA9FE",  # Hex representation of 169.254.169.254
    ]
    for target in metadata_targets:
        blocked, reason = perimeter.is_blocked_target(target)
        assert blocked is True, f"Target {target} should be blocked"
        with pytest.raises(NetworkPerimeterViolation):
            perimeter.validate_outbound_request(target)

    # Private RFC1918 subnets blocked
    private_targets = ["10.0.0.1", "172.16.5.10", "192.168.1.1"]
    for priv in private_targets:
        blocked, _ = perimeter.is_blocked_target(priv)
        assert blocked is True, f"Private IP {priv} should be blocked"

    # Allowlisted public endpoints permitted
    perimeter.allow_target("api.openai.com")
    blocked, _ = perimeter.is_blocked_target("https://api.openai.com/v1/chat/completions")
    assert blocked is False

    # Code scanning detects hardcoded metadata targets
    suspicious_code = "import requests\nresp = requests.get('http://169.254.169.254/latest/user-data')\n"
    is_safe, violations = perimeter.scan_code_for_network_violations(suspicious_code)
    assert is_safe is False
    assert len(violations) >= 1

    # -------------------------------------------------------------------------
    # STEP 3: Emergency Stop & Durable Kill-Switch Fail-Closed Enforcement
    # -------------------------------------------------------------------------
    emp_repo = EmployeeRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    audit_repo = AuditRepository(async_session)

    employee = await emp_repo.create(
        employee_id="ANC-EMP-SEC-01",
        name="Security Resilience Engineer",
        title="Senior Security Engineer",
        department="Engineering",
        system_prompt="Zero-trust security verification identity.",
        status=EmployeeStatus.IDLE,
    )
    container_repo = ContainerRepository(async_session)
    container = await container_repo.create(
        docker_id="ancos-desktop-anc-emp-sec-01",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path=str(emp_workspace),
    )
    await emp_repo.assign_container(employee.employee_id, container.id)
    project = await proj_repo.create(
        name="Security Resilience Project",
        objective="Validate zero-trust and emergency stop durability",
    )
    task = await task_repo.create(
        task_id="TASK-SEC-RESILIENCE-01",
        project_id=project.id,
        assigned_to=employee.id,
        title="Validate kill switch enforcement",
        objective="Ensure immediate fail-closed response under emergency halt",
        scope="Worker and verifier endpoints",
        acceptance_criteria="Automated validation passes",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # A. Verify nominal state responds 200/non-503
        nominal_claim = await client.post(
            "/internal/worker/tasks/claim",
            headers=worker1_headers,
            json={"execution_modes": ["terminal"]},
        )
        assert nominal_claim.status_code != 503

        # B. Trigger Emergency Stop via Boss route (/boss/emergency-stop) with Boss token
        stop_res_boss = await client.post(
            "/boss/emergency-stop",
            headers=boss_headers,
            json={"reason": "Boss AI safety halt detected in execution DAG"},
        )
        assert stop_res_boss.status_code == 200
        stop_receipt_boss = stop_res_boss.json()
        assert stop_receipt_boss["halted"] is True
        assert stop_receipt_boss["status"] == "HALTED"
        assert stop_receipt_boss["actor"] == "boss-ai"
        assert stop_receipt_boss["halt_latency_ms"] < 200.0, "Halt must execute in < 200ms"

        # Also verify Security Reviewer route (/security/emergency-stop) with Security Reviewer token
        stop_res_sec = await client.post(
            "/security/emergency-stop",
            headers=sec_headers,
            json={"reason": "Security intrusion detected in perimeter"},
        )
        assert stop_res_sec.status_code == 200
        stop_receipt_sec = stop_res_sec.json()
        assert stop_receipt_sec["halted"] is True
        assert stop_receipt_sec["actor"] == "security-reviewer"

        # Verify durable DB state
        durable_state = await EmergencyStopRepository(async_session).get_state()
        assert durable_state["active"] is True
        assert durable_state["changed_by"] in ("boss-ai", "security-reviewer")

        # Verify process singleton state
        assert get_kill_switch().is_halted is True

        # C. System readiness probe fails closed with 503
        ready_res = await client.get("/health/ready")
        assert ready_res.status_code == 503
        assert ready_res.json()["status"] == "not_ready"
        assert ready_res.json()["kill_switch_active"] is True
        assert "Emergency halt active" in ready_res.json().get("reason", "")

        # D. All worker execution routes fail closed with 503
        worker_endpoints = [
            ("POST", "/internal/worker/tasks/claim", {"execution_modes": ["terminal"]}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/start", {"fencing_token": 1}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/heartbeat", {"fencing_token": 1}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/exec", {"fencing_token": 1, "command": ["python3", "-c", "print(1)"]}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/submit", {"fencing_token": 1, "execution_receipt": {"status": "done"}}),
            ("POST", f"/internal/worker/tasks/{task.task_id}/fail", {"fencing_token": 1, "error_message": "test"}),
        ]
        for method, path, body in worker_endpoints:
            resp = await client.post(path, headers=worker1_headers, json=body)
            assert resp.status_code == 503, f"{path} returned {resp.status_code}, expected 503"
            assert "Emergency stop is active" in resp.json().get("detail", "")

        # E. All verifier execution routes fail closed with 503
        verifier_endpoints = [
            ("GET", "/internal/verifier/queue", None),
            ("POST", f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/exec", {"fencing_token": 1, "command": ["pytest"]}),
            ("POST", f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/manifest", {"fencing_token": 1}),
            ("POST", f"/internal/verifier/tasks/{task.task_id}/attempts/att-1/review", {"passed": True, "notes": "ok", "evidence_refs": [], "checks": []}),
        ]
        for method, path, body in verifier_endpoints:
            if method == "POST":
                resp = await client.post(path, headers=verifier_headers, json=body)
            else:
                resp = await client.get(path, headers=verifier_headers)
            assert resp.status_code == 503, f"{path} returned {resp.status_code}, expected 503"
            assert "Emergency stop is active" in resp.json().get("detail", "")

        # ---------------------------------------------------------------------
        # STEP 4: Emergency Resume, Monotonic Fencing Increment & Task Requeue
        # ---------------------------------------------------------------------
        token_before_resume = task.fencing_token

        # Boss AI token must be rejected (cannot unilaterally clear emergency stop)
        resume_res_boss = await client.post(
            "/boss/emergency-resume",
            headers=boss_headers,
            json={"recovery_strategy": "checkpoint"},
        )
        assert resume_res_boss.status_code == 401

        # Resume via /boss/emergency-resume with Owner token
        resume_res = await client.post(
            "/boss/emergency-resume",
            headers=owner_headers,
            json={"recovery_strategy": "checkpoint"},
        )
        assert resume_res.status_code == 200
        resume_receipt = resume_res.json()
        assert resume_receipt["resumed"] is True
        assert resume_receipt["status"] == "RUNNING"
        assert resume_receipt["resumed_by"] == "human-owner"

        # Verify durable latch cleared in DB
        durable_state_resumed = await EmergencyStopRepository(async_session).get_state()
        assert durable_state_resumed["active"] is False

        # Verify process singleton unhalted
        assert get_kill_switch().is_halted is False

        # Verify task was requeued to PENDING and fencing token monotonically incremented
        refreshed_task = await task_repo.get_by_task_id(task.task_id)
        assert refreshed_task is not None
        assert refreshed_task.status == TaskStatus.PENDING
        assert refreshed_task.fencing_token > token_before_resume

        # Verify employee status was automatically restored to IDLE by recovery
        await async_session.refresh(employee)
        assert employee.status == EmployeeStatus.IDLE, (
            "Recovered employee status must automatically be reset to IDLE without manual test tampering"
        )

        # Verify readiness probe and worker claim are unblocked
        ready_after = await client.get("/health/ready")
        assert ready_after.status_code == 200
        assert ready_after.json()["status"] == "ready"

        verifier_queue_after = await client.get("/internal/verifier/queue", headers=verifier_headers)
        assert verifier_queue_after.status_code == 200

        # ---------------------------------------------------------------------
        # STEP 5: Durable Worker Crash & Session Loss Recovery with Fencing Tokens
        # ---------------------------------------------------------------------
        # Assign task to employee
        await task_repo.assign_task(refreshed_task.task_id, employee.id)
        await async_session.refresh(employee)

        # Worker 1 claims task attempt with current fencing token
        claim_1 = await task_repo.claim_next_task(
            worker_id=WORKER_ID_1,
            lease_seconds=30,
            execution_modes=[ExecutionMode.TERMINAL],
        )
        assert claim_1 is not None
        current_token = claim_1["fencing_token"]

        # Start task attempt for Worker 1
        started_1 = await task_repo.start_task_attempt(
            task_id=claim_1["task_id"],
            worker_id=WORKER_ID_1,
            fencing_token=current_token,
            workspace_manifest_before={"manifest": "initial"},
        )
        assert started_1 is True

        # Simulate crash / session loss: task is cancelled and rescheduled, incrementing token
        cancellation = await task_repo.cancel_task(
            task_id=claim_1["task_id"],
            reason="Worker 1 heartbeat lost / container ungracefully crashed",
            requeue=True,
            actor="BossAI",
        )
        new_token = cancellation["fencing_token"]
        assert new_token > current_token

        # Verify employee status is IDLE after cancellation
        await async_session.refresh(employee)
        assert employee.status == EmployeeStatus.IDLE

        # Assign task again so Worker 2 can claim it
        await task_repo.assign_task(claim_1["task_id"], employee.id)
        await async_session.refresh(employee)

        # Worker 2 claims the newly requeued task with incremented token
        claim_2 = await task_repo.claim_next_task(
            worker_id=WORKER_ID_2,
            lease_seconds=30,
            execution_modes=[ExecutionMode.TERMINAL],
        )
        assert claim_2 is not None
        new_token = claim_2["fencing_token"]
        assert new_token > current_token

        started_2 = await task_repo.start_task_attempt(
            task_id=claim_2["task_id"],
            worker_id=WORKER_ID_2,
            fencing_token=new_token,
            workspace_manifest_before={"manifest": "rescheduled"},
        )
        assert started_2 is True

        # Zombie Worker 1 wakes up and attempts to submit with STALE token
        zombie_submit = await client.post(
            f"/internal/worker/tasks/{claim_1['task_id']}/submit",
            headers=worker1_headers,
            json={
                "fencing_token": current_token,  # Stale token
                "execution_receipt": {"status": "zombie_result", "notes": "stale execution"},
            },
        )
        assert zombie_submit.status_code == 409, (
            "Zombie worker with superseded fencing token must be rejected with HTTP 409"
        )
        assert "fencing token was superseded" in zombie_submit.json()["detail"]

        # Zombie Worker 1 attempts to execute with STALE token
        zombie_exec = await client.post(
            f"/internal/worker/tasks/{claim_1['task_id']}/exec",
            headers=worker1_headers,
            json={
                "fencing_token": current_token,  # Stale token
                "command": ["python3", "-c", "print('zombie')"],
            },
        )
        assert zombie_exec.status_code == 409, (
            "Zombie worker exec with superseded fencing token must be rejected with HTTP 409"
        )
        assert "fencing token was superseded" in zombie_exec.json()["detail"]

        # Legitimate Worker 2 submits with valid NEW token -> succeeds
        worker2_headers = {"Authorization": f"Bearer {WORKER_TOKEN_2}"}
        valid_submit = await client.post(
            f"/internal/worker/tasks/{claim_2['task_id']}/submit",
            headers=worker2_headers,
            json={
                "fencing_token": new_token,  # Valid incremented token
                "execution_receipt": {"status": "completed", "artifact": "verified_code.py"},
            },
        )
        assert valid_submit.status_code == 200
        assert valid_submit.json()["status"] == "submitted_for_verification"

    # -------------------------------------------------------------------------
    # STEP 6: Transactional Outbox Persistence & Process Crash Recovery
    # -------------------------------------------------------------------------
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)
    subscriber_queue = await broadcaster.subscribe()

    # Publish events within active DB transaction
    evt1 = await publisher.publish_event(
        session=async_session,
        event_type="TASK_SCHEDULED",
        data={"task_id": "TASK-SEC-RESILIENCE-01", "stage": "dispatched"},
        auto_commit=True,
    )
    evt2 = await publisher.publish_event(
        session=async_session,
        event_type="SECURITY_ALERT",
        data={"severity": "INFO", "message": "Zero-trust perimeter intact"},
        auto_commit=True,
    )
    assert evt1["event_id"].startswith("evt_")
    assert evt2["event_id"].startswith("evt_")
    assert int(evt2["event_id"][4:]) > int(evt1["event_id"][4:])

    # Simulate Outbox Worker crash and restart:
    # A fresh OutboxWorker starts up, inspects database, and resumes dispatching
    restarted_worker = OutboxWorker(
        session_factory=None,
        broadcaster=broadcaster,
        worker_id="outbox-worker-recovered",
    )
    # Dispatch pending events using the existing session
    dispatched_count = await restarted_worker.dispatch_once(session=async_session)
    assert dispatched_count >= 2

    # Verify event delivery in subscriber queue
    received_events: list[dict[str, Any]] = []
    while not subscriber_queue.empty():
        received_events.append(subscriber_queue.get_nowait())

    assert len(received_events) >= 2
    event_types = [e["event_type"] for e in received_events]
    assert "TASK_SCHEDULED" in event_types
    assert "SECURITY_ALERT" in event_types

    await broadcaster.unsubscribe(subscriber_queue)

    # -------------------------------------------------------------------------
    # STEP 7: Disaster Recovery & Clean-Room Backup/Restore with RTO/RPO Validation
    # -------------------------------------------------------------------------
    dr_source_data = tmp_path / "dr-source-data"
    dr_source_configs = tmp_path / "dr-source-configs"
    dr_workspace = dr_source_data / "workspaces" / "ANC-EMP-SEC-01"
    dr_workspace.mkdir(parents=True)
    dr_source_configs.mkdir(parents=True)

    # Write deliverables and configs
    deliverable_path = dr_workspace / "security_policy.py"
    deliverable_content = "# Zero-Trust Security Policy Implementation\nIS_SECURE = True\n"
    deliverable_path.write_text(deliverable_content, encoding="utf-8")
    (dr_source_configs / "network_rules.json").write_text('{"block_metadata": true}\n', encoding="utf-8")

    # Populate SQLite database in source data dir
    dr_db_path = dr_source_data / "neural_company.db"
    sqlite_engine = create_engine(f"sqlite:///{dr_db_path}")
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as sync_session:
        sync_session.add(
            Employee(
                employee_id="ANC-EMP-SEC-01",
                name="Security Resilience Engineer",
                title="Senior Security Engineer",
                department="Engineering",
                system_prompt="Zero-trust security verification identity.",
            )
        )
        sync_session.add(
            OutboxEvent(
                event_id="evt_00000001",
                channel="company_events",
                payload=json.dumps({"event_type": "SYSTEM_INITIALIZED", "data": {"status": "nominal"}}),
                status="delivered",
            )
        )
        sync_session.commit()
    sqlite_engine.dispose()

    # Create hot SQLite backup
    dr_manager = DisasterRecoveryManager(
        base_data_dir=str(dr_source_data),
        configs_dir=str(dr_source_configs),
    )
    archive_path, manifest = dr_manager.create_backup("neuro12-acceptance-backup")
    assert os.path.isfile(archive_path)
    assert len(manifest.components) >= 2
    assert dr_manager.verify_backup_integrity(archive_path) is True

    # Clean-room restore into separate clean directory
    clean_room_target = tmp_path / "clean-room-restore"
    clean_room_target.mkdir()

    restore_report = dr_manager.restore_backup(archive_path, str(clean_room_target))
    assert restore_report.status == "SUCCESS"
    assert restore_report.db_integrity_ok is True
    assert restore_report.verified_components == len(manifest.components)

    # Validate SQLite PRAGMA integrity_check on restored database
    restored_root = clean_room_target / "neuro12-acceptance-backup"
    restored_db = restored_root / "database" / "neural_company.db"
    with sqlite3.connect(f"file:{restored_db}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check;").fetchone() == ("ok",)
        emp_row = conn.execute(
            "SELECT employee_id, name, department FROM employees WHERE employee_id = ?",
            ("ANC-EMP-SEC-01",),
        ).fetchone()
        assert emp_row == ("ANC-EMP-SEC-01", "Security Resilience Engineer", "Engineering")

        evt_row = conn.execute(
            "SELECT event_id, status FROM outbox_events WHERE event_id = ?",
            ("evt_00000001",),
        ).fetchone()
        assert evt_row == ("evt_00000001", "delivered")

    # Validate restored artifact content
    restored_artifact = restored_root / "artifacts" / "ANC-EMP-SEC-01" / "security_policy.py"
    assert restored_artifact.read_text(encoding="utf-8") == deliverable_content

    # Validate RTO and RPO metrics
    rto_rpo_result = dr_manager.validate_rto_rpo_and_state_integrity(
        report=restore_report,
        target_dir=str(clean_room_target),
        max_rto_seconds=60.0,
        max_rpo_seconds=300.0,
    )
    assert rto_rpo_result["status"] == "PASSED"
    assert rto_rpo_result["rto_compliant"] is True
    assert rto_rpo_result["rpo_compliant"] is True
    assert rto_rpo_result["db_integrity_ok"] is True
    assert rto_rpo_result["state_integrity_ok"] is True
    assert "employees" in rto_rpo_result["restored_tables"]
    assert "outbox_events" in rto_rpo_result["restored_tables"]

    # Tampered backup archive defense: verify tampered archive is rejected
    tamper_staging = tmp_path / "tamper-staging"
    tamper_staging.mkdir()
    with tarfile.open(archive_path, "r:gz") as bundle:
        bundle.extractall(tamper_staging, filter=dr_manager._safe_archive_filter)

    tampered_root = tamper_staging / "neuro12-acceptance-backup"
    tampered_artifact = tampered_root / "artifacts" / "ANC-EMP-SEC-01" / "security_policy.py"
    tampered_artifact.write_text("# MALICIOUS INJECTION\nIS_SECURE = False\n", encoding="utf-8")

    tampered_archive = tmp_path / "tampered_backup.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as bundle:
        bundle.add(tampered_root, arcname=tampered_root.name)

    assert dr_manager.verify_backup_integrity(str(tampered_archive)) is False

    tamper_dest = tmp_path / "tamper-clean-room"
    tamper_dest.mkdir()
    tamper_report = dr_manager.restore_backup(str(tampered_archive), str(tamper_dest))
    assert tamper_report.status == "FAILED"
    assert tamper_report.verified_components < len(manifest.components)
    assert list(tamper_dest.iterdir()) == [], "Tampered restore must not publish partial tree"

    # Test corrupt manifest raises StorageError
    corrupt_manifest_archive = tmp_path / "corrupt_manifest.tar.gz"
    (tampered_root / "manifest.json").write_text("not a valid json manifest", encoding="utf-8")
    with tarfile.open(corrupt_manifest_archive, "w:gz") as bundle:
        bundle.add(tampered_root, arcname=tampered_root.name)
    with pytest.raises(StorageError):
        dr_manager.restore_backup(str(corrupt_manifest_archive), str(tamper_dest))

    # -------------------------------------------------------------------------
    # STEP 8: Cryptographic Audit Trail Hash-Chain Integrity
    # -------------------------------------------------------------------------
    audit_records = await audit_repo.list_all()
    assert len(audit_records) >= 2, "Audit log must contain operational records"

    # Verify actions were captured
    actions_logged = {record.action for record in audit_records}
    assert "EMERGENCY_STOP" in actions_logged
    assert "RESUME_SYSTEM" in actions_logged

    # Verify cryptographic chain ordering and integrity
    ordered_chain = AuditLogChainVerifier.order_chain(audit_records)
    assert len(ordered_chain) == len(audit_records)

    is_valid, issues = AuditLogChainVerifier.verify_records(ordered_chain)
    assert is_valid is True, f"Audit chain verification failed: {issues}"
    assert len(issues) == 0

    # Test tamper detection: mutating any field in any audit record breaks the chain
    tampered_chain = list(ordered_chain)
    target_record = tampered_chain[-1]
    original_details = target_record.details

    # Mutate details
    target_record.details = {"tampered": "unauthorized modification"}
    tampered_valid, tamper_issues = AuditLogChainVerifier.verify_records(tampered_chain)
    assert tampered_valid is False, "Mutated audit record must fail integrity verification"
    assert len(tamper_issues) > 0

    with pytest.raises(AuditIntegrityError):
        AuditLogChainVerifier.enforce_integrity(tampered_chain)

    # Restore original details and verify clean integrity enforcement
    target_record.details = original_details
    AuditLogChainVerifier.enforce_integrity(ordered_chain)
