from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.api.routes.verifier import capture_verifier_manifest, execute_verifier_test
from neural.api.routes.worker import execute_terminal_command
from neural.api.schemas.requests import (
    VerifierCommandRequest,
    VerifierManifestRequest,
    WorkerCommandRequest,
)
from neural.boss.brain import BossBrain
from neural.cognitive_fabric.memory.models import MemoryLayer
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.cognitive_fabric.retrieval.hybrid_retrieval import HybridRetrievalEngine
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    SkillLevel,
    TaskPriority,
    TaskStatus,
)
from neural.db.models import TaskAttempt
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    MemoryRepository,
    PerformanceRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory


@pytest.mark.asyncio
async def test_verified_task_review_persists_idempotent_memory_for_restart_retrieval(async_session):
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-MEMORY-LIFECYCLE",
        name="Memory Lifecycle Validation",
        title="Software Engineer",
        system_prompt="Memory lifecycle test employee.",
    )
    project = await ProjectRepository(async_session).create(
        name="Verified memory lifecycle",
        objective="Promote verified outcomes into persistent organizational memory",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-MEMORY-LIFECYCLE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Persist a verified task result",
        objective="Create a memory only after an independent passing receipt",
        scope="Memory integration",
        acceptance_criteria="A passing verifier receipt is persisted and retrievable after store recreation",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=9,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="memory-lifecycle-worker",
        fencing_token=9,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1),
        execution_receipt={"summary": "Added durable verifier-bound memory promotion."},
    )
    async_session.add(attempt)
    await async_session.flush()
    attempt_id = attempt.id
    await async_session.commit()
    persisted_task = await task_repo.get_by_task_id("TASK-MEMORY-LIFECYCLE")
    assert persisted_task is not None and persisted_task.status == TaskStatus.RUNNING
    persisted_attempt = await async_session.get(TaskAttempt, attempt_id)
    assert persisted_attempt is not None and persisted_attempt.status == "submitted"
    assert persisted_task.fencing_token == persisted_attempt.fencing_token == 9

    accepted = await task_repo.review_task_attempt(
        task.task_id,
        attempt_id,
        "independent-memory-verifier",
        {
            "passed": True,
            "verifier_id": "employee-supplied-forgery",
            "attempt_id": "forged-attempt",
            "task_id": "forged-task",
            "notes": "The independently replayed task result is correct and reusable.",
            "evidence_refs": ["verifier:memory-lifecycle:tests", "workspace-delta:sha256:abc123"],
            "checks": [{"name": "independent tests", "passed": True}],
            "test_summary": {"passed": 3},
        },
    )
    assert accepted is not None and accepted["task_status"] == TaskStatus.COMPLETED.value

    memory_repo = MemoryRepository(async_session)
    entries = await memory_repo.list_entries(source=f"task-attempt:{attempt_id}")
    assert len(entries) == 1
    persisted_receipt = entries[0].metadata_json["provenance"]["independent_verification_receipt"]
    assert persisted_receipt["verifier_id"] == "independent-memory-verifier"
    assert persisted_receipt["attempt_id"] == attempt_id
    assert persisted_receipt["task_id"] == task.task_id

    # The accepted transition is one-shot; replaying the review cannot duplicate memory.
    assert await task_repo.review_task_attempt(
        task.task_id,
        attempt_id,
        "independent-memory-verifier",
        {"passed": True, "notes": "duplicate", "evidence_refs": ["duplicate"], "checks": [], "test_summary": {}},
    ) is None
    assert len(await memory_repo.list_entries(source=f"task-attempt:{attempt_id}")) == 1

    # Recreate the store to model process restart, then ensure trusted retrieval indexes it.
    restarted_store = SixTierMemoryStore(memory_repo=memory_repo)
    retriever = HybridRetrievalEngine(memory_store=restarted_store)
    assert await retriever.restore_persisted_memory() == 1
    restored = restarted_store.query_trusted_records(
        requester_id="BossAI",
        layer=MemoryLayer.EPISODIC,
        query="independently replayed task result",
    )
    assert len(restored) == 1
    assert retriever.retrieve("independently replayed task result", requester_id="BossAI")


@pytest.mark.asyncio
async def test_rejected_task_review_does_not_create_trusted_memory(async_session):
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-MEMORY-REJECTED",
        name="Rejected Memory Validation",
        title="Software Engineer",
        system_prompt="Rejected memory lifecycle test employee.",
    )
    project = await ProjectRepository(async_session).create(
        name="Rejected memory lifecycle",
        objective="Ensure rejected work is not promoted as trusted memory",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-MEMORY-REJECTED",
        project_id=project.id,
        assigned_to=employee.id,
        title="Reject a task result",
        objective="Reject failed independent verification",
        scope="Memory integration",
        acceptance_criteria="Failed verification creates no trusted memory",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=4,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="memory-reject-worker",
        fencing_token=4,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.flush()
    attempt_id = attempt.id
    await async_session.commit()
    rejected = await task_repo.review_task_attempt(
        task.task_id,
        attempt_id,
        "independent-memory-verifier",
        {
            "passed": False,
            "notes": "Independent replay failed.",
            "evidence_refs": ["verifier:failed"],
            "checks": [{"name": "tests", "passed": False}],
            "test_summary": {"failed": 1},
        },
    )
    assert rejected is not None and rejected["attempt_status"] == "rejected"
    assert await MemoryRepository(async_session).list_entries(source=f"task-attempt:{attempt_id}") == []


@pytest.mark.asyncio
async def test_worker_claim_reserves_idle_employee_and_prevents_overlapping_tasks(async_session):
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-WORKER-CLAIM",
        name="Worker Claim Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-worker-claim",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/worker-claim-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Worker claim capacity",
        objective="Claim only while an employee workstation is available",
    )
    task_repo = TaskRepository(async_session)
    for task_id in ("TASK-CLAIM-1", "TASK-CLAIM-2"):
        await task_repo.create(
            task_id=task_id,
            project_id=project.id,
            assigned_to=employee.id,
            title=f"Run {task_id}",
            objective="Run the assigned terminal task",
            scope="Validation",
            acceptance_criteria="A verifier receipt is stored",
            execution_mode=ExecutionMode.TERMINAL,
            priority=TaskPriority.HIGH,
            status=TaskStatus.ASSIGNED,
        )

    await async_session.refresh(employee)
    claimed = await task_repo.claim_next_task("worker-claim-test", execution_modes=[ExecutionMode.TERMINAL])
    assert claimed is not None
    assert claimed["task_id"] == "TASK-CLAIM-1"
    await async_session.refresh(employee)
    assert employee.status == EmployeeStatus.WORKING
    assert await task_repo.claim_next_task("worker-claim-test-2", execution_modes=[ExecutionMode.TERMINAL]) is None

    assert await task_repo.release_task_claim(
        claimed["task_id"],
        "worker-claim-test",
        claimed["fencing_token"],
        "Validation releases the unstarted claim.",
    )
    await async_session.refresh(employee)
    assert employee.status == EmployeeStatus.IDLE

    next_claim = await task_repo.claim_next_task("worker-claim-test-2", execution_modes=[ExecutionMode.TERMINAL])
    assert next_claim is not None
    assert next_claim["task_id"] == "TASK-CLAIM-2"
    await async_session.refresh(employee)
    assert employee.status == EmployeeStatus.WORKING
    assert await task_repo.release_task_claim(
        next_claim["task_id"],
        "worker-claim-test-2",
        next_claim["fencing_token"],
        "Validation releases the second unstarted claim.",
    )


@pytest.mark.asyncio
async def test_worker_claim_orders_by_real_priority_then_earliest_deadline(async_session):
    employee_repo = EmployeeRepository(async_session)
    project = await ProjectRepository(async_session).create(
        name="Deadline dispatch",
        objective="Dispatch critical and deadline-sensitive work first",
    )
    task_repo = TaskRepository(async_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task_specs = [
        ("TASK-A-LOW", TaskPriority.LOW, now + timedelta(hours=1)),
        ("TASK-B-HIGH-LATE", TaskPriority.HIGH, now + timedelta(days=3)),
        ("TASK-C-CRITICAL", TaskPriority.CRITICAL, now + timedelta(days=20)),
        ("TASK-D-HIGH-SOON", TaskPriority.HIGH, now + timedelta(days=1)),
    ]
    for index, (task_id, priority, deadline) in enumerate(task_specs):
        employee = await employee_repo.create(
            employee_id=f"ANC-EMP-DISPATCH-{index}",
            name=f"Dispatch Employee {index}",
            title="Software Engineer",
            system_prompt="Task dispatch priority fixture",
            status=EmployeeStatus.IDLE,
        )
        container = await ContainerRepository(async_session).create(
            docker_id=f"ancos-desktop-anc-emp-dispatch-{index}",
            image="ancos/employee-base:latest",
            status=ContainerStatus.RUNNING,
            resource_limits={},
            network_config={},
            workspace_path=f"/tmp/dispatch-{index}",
        )
        await employee_repo.assign_container(employee.employee_id, container.id)
        await task_repo.create(
            task_id=task_id,
            project_id=project.id,
            assigned_to=employee.id,
            title=task_id,
            objective="Run the assigned task",
            scope="Priority validation",
            acceptance_criteria="Independent verification passes",
            execution_mode=ExecutionMode.TERMINAL,
            priority=priority,
            deadline=deadline,
            status=TaskStatus.ASSIGNED,
        )

    claimed_ids = []
    for number in range(len(task_specs)):
        claim = await task_repo.claim_next_task(f"worker-priority-{number}")
        if claim is None:
            containers = ContainerRepository(async_session)
            container_statuses = [
                (employee.employee_id, (await containers.get_by_id(employee.container_id)).status)
                for employee in await employee_repo.list_all()
            ]
            pytest.fail(
                f"no claim at {number}; claimed={claimed_ids}; "
                f"tasks={[(t.task_id, t.priority, t.deadline, t.status) for t in await task_repo.list_by_project(project.id)]}; "
                f"employees={[(e.employee_id, e.status) for e in await employee_repo.list_all()]}; "
                f"containers={container_statuses}"
            )
        claimed_ids.append(claim["task_id"])

    assert claimed_ids == [
        "TASK-C-CRITICAL",
        "TASK-D-HIGH-SOON",
        "TASK-B-HIGH-LATE",
        "TASK-A-LOW",
    ]


@pytest.mark.asyncio
async def test_worker_claim_dispatches_next_dag_task_only_after_verified_prerequisite(async_session):
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-DAG-CONTINUE",
        name="DAG Continuation Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-dag-continue",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/dag-continuation-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Verified DAG continuation",
        objective="Dispatch dependent work after independent verification",
    )
    task_repo = TaskRepository(async_session)
    first = await task_repo.create(
        task_id="TASK-DAG-FIRST",
        project_id=project.id,
        assigned_to=employee.id,
        title="First task",
        objective="Complete the prerequisite",
        scope="Validation",
        acceptance_criteria="The prerequisite has an independent passing receipt",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )
    second = await task_repo.create(
        task_id="TASK-DAG-SECOND",
        project_id=project.id,
        title="Dependent task",
        objective="Continue the project after verification",
        scope="Validation",
        acceptance_criteria="The dependent task is dispatched only after verified completion",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.PENDING,
    )
    await task_repo.add_dependency(second.task_id, first.task_id)

    await async_session.refresh(employee)
    live_employee = await employee_repo.get_by_employee_id(employee.employee_id)
    assert live_employee is not None and live_employee.container is not None
    assert live_employee.status == EmployeeStatus.IDLE
    assert live_employee.container.status == ContainerStatus.RUNNING
    first_claim = await task_repo.claim_next_task(
        "worker-dag-continuation",
        execution_modes=[ExecutionMode.TERMINAL],
    )
    assert first_claim is not None
    assert first_claim["task_id"] == first.task_id
    assert await task_repo.start_task_attempt(
        first.task_id,
        "worker-dag-continuation",
        first_claim["fencing_token"],
        {
            "captured_at_unix": 1790000000.0,
            "files": [],
            "file_count": 0,
            "total_size_bytes": 0,
            "complete": True,
            "errors": [],
        },
    )
    assert await task_repo.submit_task_attempt(
        first.task_id,
        "worker-dag-continuation",
        first_claim["fencing_token"],
        {"planned_commands": [["python3", "-m", "unittest", "-v"]]},
    )
    attempts = await task_repo.list_attempts_by_project(project.id)
    first_attempt = next(attempt for attempt in attempts if attempt.task_id == first.id)
    reviewed = await task_repo.review_task_attempt(
        first.task_id,
        first_attempt.id,
        "independent-verifier",
        {
            "passed": True,
            "notes": "Independent prerequisite verification passed.",
            "evidence_refs": ["verifier:first-task"],
            "checks": [{"name": "tests", "passed": True}],
            "test_summary": {"passed": 1},
        },
    )
    assert reviewed is not None
    assert reviewed["task_status"] == TaskStatus.COMPLETED.value

    next_claim = await task_repo.claim_next_task(
        "worker-dag-continuation",
        execution_modes=[ExecutionMode.TERMINAL],
    )
    assert next_claim is not None
    assert next_claim["task_id"] == second.task_id
    assert next_claim["employee_id"] == employee.employee_id


@pytest.mark.asyncio
async def test_worker_claim_does_not_unlock_dependency_with_unverified_completion(async_session):
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-UNVERIFIED-DEPENDENCY",
        name="Unverified Dependency Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-unverified-dependency",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/unverified-dependency-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Unverified dependency gate",
        objective="Do not release dependent work from legacy completion",
    )
    task_repo = TaskRepository(async_session)
    prerequisite = await task_repo.create(
        task_id="TASK-LEGACY-COMPLETE",
        project_id=project.id,
        title="Legacy completed task",
        objective="This row has no verifier proof",
        scope="Validation",
        acceptance_criteria="An independent verifier receipt is required",
        status=TaskStatus.COMPLETED,
    )
    dependent = await task_repo.create(
        task_id="TASK-WAITING-FOR-VERIFICATION",
        project_id=project.id,
        title="Task waiting for proof",
        objective="Remain pending while prerequisite proof is absent",
        scope="Validation",
        acceptance_criteria="Only verified tasks may unlock this task",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.PENDING,
    )
    await task_repo.add_dependency(dependent.task_id, prerequisite.task_id)

    assert await task_repo.claim_next_task(
        "worker-unverified-dependency",
        execution_modes=[ExecutionMode.TERMINAL],
    ) is None
    persisted = await task_repo.get_by_task_id(dependent.task_id)
    assert persisted.status == TaskStatus.PENDING
    assert persisted.assigned_to is None


@pytest.mark.asyncio
async def test_independent_verifier_receipts_persist_employee_performance_history(async_session):
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-VERIFIER-PERFORMANCE",
        name="Verified Performance Employee",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.WORKING,
    )
    project = await ProjectRepository(async_session).create(
        name="Verified workforce performance",
        objective="Persist employee performance from independent verifier outcomes",
    )
    task_repo = TaskRepository(async_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    passing_task = await task_repo.create(
        task_id="TASK-PERFORMANCE-PASS",
        project_id=project.id,
        assigned_to=employee.id,
        title="Verified passing attempt",
        objective="Store a successful verifier-backed performance record",
        scope="Performance validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=1,
    )
    passing_attempt = TaskAttempt(
        task_id=passing_task.id,
        attempt_number=1,
        worker_id="worker-performance-test",
        fencing_token=1,
        status="submitted",
        started_at=now - timedelta(seconds=37),
        finished_at=now,
        lease_expires_at=now,
        execution_receipt={
            "model": {"input_tokens": 125, "output_tokens": 41, "cost_usd": 0.0042}
        },
    )
    async_session.add(passing_attempt)
    await async_session.commit()

    accepted = await task_repo.review_task_attempt(
        passing_task.task_id,
        passing_attempt.id,
        "verifier-performance-test",
        {
            "passed": True,
            "notes": "Independent replay passed.",
            "evidence_refs": ["verifier:performance-pass"],
            "checks": [{"name": "tests", "passed": True}],
            "test_summary": {"independent_test_runs": 1},
        },
    )
    assert accepted is not None and accepted["attempt_status"] == "verified"

    performance_repo = PerformanceRepository(async_session)
    passing_records = await performance_repo.list_by_employee(employee.employee_id)
    assert len(passing_records) == 1
    assert passing_records[0].task_id == passing_task.task_id
    assert passing_records[0].outcome == "success"
    assert passing_records[0].quality_score == 1.0
    assert passing_records[0].completion_time_seconds == pytest.approx(37.0, abs=0.1)
    assert passing_records[0].cost_tokens == 166
    assert passing_records[0].cost_usd == pytest.approx(0.0042)

    rejected_task = await task_repo.create(
        task_id="TASK-PERFORMANCE-REJECT",
        project_id=project.id,
        assigned_to=employee.id,
        title="Verifier rejected attempt",
        objective="Record a failed independent verification",
        scope="Performance validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=4,
    )
    rejected_attempt = TaskAttempt(
        task_id=rejected_task.id,
        attempt_number=1,
        worker_id="worker-performance-test",
        fencing_token=4,
        status="submitted",
        started_at=now - timedelta(seconds=8),
        lease_expires_at=now,
        execution_receipt={"model": {"input_tokens": 20, "output_tokens": 7, "cost_usd": 0.001}},
    )
    async_session.add(rejected_attempt)
    await async_session.commit()

    rejected = await task_repo.review_task_attempt(
        rejected_task.task_id,
        rejected_attempt.id,
        "verifier-performance-test",
        {
            "passed": False,
            "notes": "Independent replay failed.",
            "evidence_refs": ["verifier:performance-reject"],
            "checks": [{"name": "tests", "passed": False}],
            "test_summary": {"independent_test_runs": 1},
        },
    )
    assert rejected is not None and rejected["attempt_status"] == "rejected"

    records = await performance_repo.list_by_employee(employee.employee_id)
    assert len(records) == 2
    rejected_record = next(record for record in records if record.task_id == rejected_task.task_id)
    assert rejected_record.outcome == "failure"
    assert rejected_record.quality_score == 0.0
    assert rejected_record.cost_tokens == 27
    persisted_employee = await employee_repo.get_by_employee_id(employee.employee_id)
    assert persisted_employee is not None
    assert persisted_employee.performance_summary["verification_attempts"] == 2
    assert persisted_employee.performance_summary["verified_tasks"] == 1
    assert persisted_employee.performance_summary["verification_rejections"] == 1
    assert persisted_employee.performance_summary["last_verified_task_id"] == rejected_task.task_id
    assert persisted_employee.performance_summary["last_verifier_id"] == "verifier-performance-test"
    assert persisted_employee.performance_summary["last_verification_passed"] is False


@pytest.mark.asyncio
async def test_production_owner_identity_rejects_worker_credentials(monkeypatch):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()

    assert await require_owner_identity(f"Bearer {owner_token}") == "human-owner"
    with pytest.raises(HTTPException) as rejected:
        await require_owner_identity(f"Bearer {worker_token}")
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_verifier_queue_requires_separate_verifier_credential(async_session, monkeypatch):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    verifier_token = "verifier-token-" + "v" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", verifier_token)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()

    project = await ProjectRepository(async_session).create(name="Verifier auth", objective="Protect queue")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-VERIFIER-AUTH",
        project_id=project.id,
        title="Pending terminal review",
        objective="Wait for verifier",
        scope="Tests",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=11,
    )
    async_session.add(TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-auth-test",
        fencing_token=11,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
        execution_receipt={"planned_commands": [["pytest", "-q"]]},
    ))
    await async_session.commit()

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/internal/verifier/queue")).status_code == 401
        assert (
            await client.get(
                "/internal/verifier/queue",
                headers={"Authorization": f"Bearer {worker_token}"},
            )
        ).status_code == 401
        response = await client.get(
            "/internal/verifier/queue",
            headers={"Authorization": f"Bearer {verifier_token}"},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["task_id"] == "TASK-VERIFIER-AUTH"
    assert items[0]["execution_mode"] == "terminal"
    assert items[0]["execution_receipt"]["planned_commands"] == [["pytest", "-q"]]


@pytest.mark.asyncio
async def test_boss_staffing_requires_verified_skills(async_session):
    employee_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(employee_repo)
    employee = await factory.hire_employee(
        name="Evidence Test Engineer",
        title="Backend Engineer",
        initial_skills=["Python"],
    )
    plan = {
        "phases": [
            {
                "tasks": [
                    {
                        "role_required": "backend-engineer",
                        "skills_required": ["Python"],
                    }
                ]
            }
        ]
    }

    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {
        "backend-engineer": ["Python"]
    }

    employee.skills[0].verified = True
    await async_session.commit()
    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {}


@pytest.mark.asyncio
async def test_production_objective_is_owner_authenticated_and_audited(
    async_session,
    monkeypatch,
):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("BOSS_LLM_PLANNING_ENABLED", "false")
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {"objective": "Create an evidence-backed API service", "deadline_days": 3}
        assert (await client.post("/boss/objective", json=payload)).status_code == 401
        assert (
            await client.post(
                "/boss/objective",
                json=payload,
                headers={"Authorization": f"Bearer {worker_token}"},
            )
        ).status_code == 401

        clarification = await client.post(
            "/boss/objective",
            json={"objective": "fix it"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert clarification.status_code == 409
        assert clarification.json()["detail"]["code"] == "clarification_required"
        assert clarification.json()["detail"]["questions"]
        assert await ProjectRepository(async_session).list_all() == []
        assert await EmployeeRepository(async_session).list_all() == []
        assert await AuditRepository(async_session).list_all() == []

        budget_request = await client.post(
            "/boss/objective",
            json={"objective": "Create a cost-bounded API service", "budget_usd": 10},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert budget_request.status_code == 501
        assert await ProjectRepository(async_session).list_all() == []
        assert await EmployeeRepository(async_session).list_all() == []
        assert await AuditRepository(async_session).list_all() == []

        accepted = await client.post(
            "/boss/objective",
            json=payload,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert accepted.status_code == 200
        project_id = accepted.json()["id"]

        assert (await client.get(f"/boss/projects/{project_id}/report")).status_code == 401
        report_response = await client.get(
            f"/boss/projects/{project_id}/report",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["task_counts"]["verified"] == 0
        assert report["task_counts"]["completed_without_verification"] == 0
        assert report["task_counts"]["assigned"] >= 1
        project = await ProjectRepository(async_session).get_by_id(project_id)
        assert project is not None
        assert project.execution_plan["owner_constraints"]["deadline_days"] == 3
        persisted_tasks = await TaskRepository(async_session).list_by_project(project_id)
        assert persisted_tasks
        assert all(task.deadline is not None for task in persisted_tasks)

    audit_records = await AuditRepository(async_session).list_all()
    owner_events = [record for record in audit_records if record.action == "SUBMIT_OBJECTIVE"]
    staffing_events = [record for record in audit_records if record.action == "STAFFING_DECISION"]
    assert owner_events and owner_events[0].actor == "human-owner"
    assert staffing_events and staffing_events[0].actor == "BossAI"
    assert staffing_events[0].details["recruited_employee_ids"]


@pytest.mark.asyncio
async def test_project_report_distinguishes_unverified_completion_and_attempt_failure(async_session):
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    project = await project_repo.create(name="Report evidence", objective="Report stored task state")
    await task_repo.create(
        task_id="TASK-UNVERIFIED-REPORT",
        project_id=project.id,
        title="Legacy completion",
        objective="Detect a completion missing proof",
        scope="Reporting",
        acceptance_criteria="A passing verifier receipt exists",
        status=TaskStatus.COMPLETED,
        verification_result=None,
    )
    failed_task = await task_repo.create(
        task_id="TASK-FAILED-REPORT",
        project_id=project.id,
        title="Failed execution",
        objective="Expose an actual failed attempt",
        scope="Reporting",
        acceptance_criteria="Failure details are visible",
        status=TaskStatus.FAILED,
    )
    await task_repo.create(
        task_id="TASK-RUNNING-WITHOUT-ATTEMPT",
        project_id=project.id,
        title="Orphaned running state",
        objective="Report a running task without a current attempt",
        scope="Execution integrity",
        acceptance_criteria="The report identifies the missing worker attempt",
        status=TaskStatus.RUNNING,
        fencing_token=3,
    )
    active_task = await task_repo.create(
        task_id="TASK-RUNNING-WITH-ACTIVE-ATTEMPT",
        project_id=project.id,
        title="Active worker attempt",
        objective="Report an active worker attempt",
        scope="Execution integrity",
        acceptance_criteria="The current attempt has a live lease",
        status=TaskStatus.RUNNING,
        fencing_token=4,
    )
    submitted_task = await task_repo.create(
        task_id="TASK-RUNNING-AWAITING-VERIFIER",
        project_id=project.id,
        title="Submitted worker attempt",
        objective="Report a submitted attempt waiting for review",
        scope="Execution integrity",
        acceptance_criteria="Independent verification is pending",
        status=TaskStatus.RUNNING,
        fencing_token=5,
    )
    expired_task = await task_repo.create(
        task_id="TASK-RUNNING-EXPIRED-LEASE",
        project_id=project.id,
        title="Expired worker lease",
        objective="Report an expired current worker lease",
        scope="Execution integrity",
        acceptance_criteria="The expired lease is surfaced for recovery",
        status=TaskStatus.RUNNING,
        fencing_token=6,
    )
    leased_task = await task_repo.create(
        task_id="TASK-RUNNING-CLAIM-NOT-STARTED",
        project_id=project.id,
        title="Claimed but not started",
        objective="Report a current attempt that has not started",
        scope="Execution integrity",
        acceptance_criteria="The claim state is visible",
        status=TaskStatus.RUNNING,
        fencing_token=7,
    )
    stale_fence_task = await task_repo.create(
        task_id="TASK-RUNNING-STALE-FENCE",
        project_id=project.id,
        title="Stale attempt fence",
        objective="Report when a running task only has an old attempt fence",
        scope="Execution integrity",
        acceptance_criteria="Only the current fencing token counts as active",
        status=TaskStatus.RUNNING,
        fencing_token=9,
    )
    passing_task = await task_repo.create(
        task_id="TASK-VERIFIED-REPORT",
        project_id=project.id,
        title="Verified work",
        objective="Include a passing independent receipt",
        scope="Reporting",
        acceptance_criteria="A passing verifier receipt is stored",
    )
    verified_receipt = {
        "passed": True,
        "notes": "Acceptance checks passed",
        "evidence_refs": [
            "verifier:attempt-report:test:1",
            f"workspace:sha256:{'a' * 64}",
        ],
        "artifacts": ["verified-artifact"],
        "test_summary": {
            "independent_test_runs": [{
                "command": ["python3", "-m", "unittest", "--password", "must-not-leak"],
                "worker_exit_code": 0,
                "verifier_exit_code": 0,
                "evidence_ref": "verifier:attempt-report:test:1",
            }],
            "workspace_manifest_before": {"complete": True, "file_count": 0},
            "workspace_manifest": {
                "complete": True,
                "file_count": 1,
                "files": [{"path": "src/main.py", "size_bytes": 14, "sha256": "a" * 64}],
            },
            "workspace_delta": {
                "complete": True,
                "created": [{"path": "src/main.py", "size_bytes": 14, "sha256": "a" * 64}],
                "modified": [],
                "counts": {"created": 1, "modified": 0, "deleted": 0},
            },
        },
        "checks": [{"name": "tests_passed", "passed": True}],
    }
    await task_repo.complete_with_receipt(
        passing_task.task_id,
        verified_receipt,
    )
    async_session.add(
        TaskAttempt(
            task_id=passing_task.id,
            attempt_number=1,
            worker_id="worker-report",
            fencing_token=1,
            status="verified",
            lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            execution_receipt={
                "model": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "api_key": "must-not-leak",
                },
            },
            verification_receipt=verified_receipt,
            verified_by="verifier-report",
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    async_session.add(
        TaskAttempt(
            task_id=failed_task.id,
            attempt_number=1,
            worker_id="worker-test",
            fencing_token=1,
            status="failed",
            lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            error_message="Process returned exit code 2",
            execution_receipt={},
        )
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async_session.add_all([
        TaskAttempt(
            task_id=active_task.id,
            attempt_number=1,
            worker_id="worker-active",
            fencing_token=active_task.fencing_token,
            status="running",
            lease_expires_at=now + timedelta(minutes=2),
            heartbeat_at=now,
            execution_receipt={},
        ),
        TaskAttempt(
            task_id=submitted_task.id,
            attempt_number=1,
            worker_id="worker-submitted",
            fencing_token=submitted_task.fencing_token,
            status="submitted",
            lease_expires_at=now,
            execution_receipt={"passed": False},
        ),
        TaskAttempt(
            task_id=expired_task.id,
            attempt_number=1,
            worker_id="worker-expired",
            fencing_token=expired_task.fencing_token,
            status="running",
            lease_expires_at=now - timedelta(minutes=1),
            execution_receipt={},
        ),
        TaskAttempt(
            task_id=leased_task.id,
            attempt_number=1,
            worker_id="worker-leased",
            fencing_token=leased_task.fencing_token,
            status="leased",
            lease_expires_at=now + timedelta(minutes=1),
            execution_receipt={},
        ),
        TaskAttempt(
            task_id=stale_fence_task.id,
            attempt_number=1,
            worker_id="worker-stale-fence",
            fencing_token=8,
            status="running",
            lease_expires_at=now + timedelta(minutes=1),
            execution_receipt={},
        ),
    ])
    await async_session.commit()

    report = await BossBrain(project_repo=project_repo, task_repo=task_repo).build_project_report(project.id)
    assert report is not None
    assert report["task_counts"]["verified"] == 1
    assert report["task_counts"]["completed_without_verification"] == 1
    assert report["task_counts"]["failed"] == 1
    assert report["task_counts"]["failed_attempts"] == 1
    reported_orphan = next(
        task for task in report["tasks"]
        if task["task_id"] == "TASK-RUNNING-WITHOUT-ATTEMPT"
    )
    assert reported_orphan["execution_health"] == "missing_current_attempt"
    assert reported_orphan["attempts"] == []
    assert next(task for task in report["tasks"] if task["task_id"] == stale_fence_task.task_id)["execution_health"] == "missing_current_attempt"
    assert next(task for task in report["tasks"] if task["task_id"] == active_task.task_id)["execution_health"] == "active"
    assert next(task for task in report["tasks"] if task["task_id"] == submitted_task.task_id)["execution_health"] == "awaiting_verification"
    assert next(task for task in report["tasks"] if task["task_id"] == expired_task.task_id)["execution_health"] == "expired_worker_lease"
    assert next(task for task in report["tasks"] if task["task_id"] == leased_task.task_id)["execution_health"] == "claimed_not_started"
    reported_failure = next(task for task in report["tasks"] if task["task_id"] == "TASK-FAILED-REPORT")
    assert reported_failure["attempts"][0]["error_message"] == "Process returned exit code 2"
    reported_success = next(task for task in report["tasks"] if task["task_id"] == "TASK-VERIFIED-REPORT")
    assert reported_success["verified"] is True
    assert reported_success["evidence_refs"] == [
        "verifier:attempt-report:test:1",
        f"workspace:sha256:{'a' * 64}",
    ]
    assert reported_success["attempts"][0]["execution_model"] == {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "input_tokens": 123,
        "output_tokens": 45,
    }
    assert reported_success["verification_evidence"]["independent_test_runs"] == [{
        "executable": "python3",
        "worker_exit_code": 0,
        "verifier_exit_code": 0,
        "evidence_ref": "verifier:attempt-report:test:1",
    }]
    assert "must-not-leak" not in str(reported_success)


@pytest.mark.asyncio
async def test_worker_failure_retries_only_retryable_errors_within_budget(async_session):
    project = await ProjectRepository(async_session).create(name="Recovery", objective="Retry safely")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-RECOVERY-RETRY",
        project_id=project.id,
        title="Retry transient failure",
        objective="Retry connection timeout",
        scope="Execution",
        acceptance_criteria="Independent verification passes",
        status=TaskStatus.RUNNING,
        max_retries=1,
        fencing_token=1,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-test",
        fencing_token=1,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    retry = await task_repo.fail_task_attempt(task.task_id, "worker-test", 1, "connection timeout")
    assert retry == {
        "retrying": True,
        "retry_count": 1,
        "retry_after_seconds": 1,
        "reassigned": False,
        "previous_employee_id": None,
        "assigned_employee_id": None,
    }
    retried_task = await task_repo.get_by_task_id(task.task_id)
    assert retried_task.status == TaskStatus.ASSIGNED
    assert retried_task.fencing_token == 2
    first_attempt = await async_session.get(TaskAttempt, attempt.id)
    assert first_attempt.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

    next_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=2,
        worker_id="worker-test",
        fencing_token=3,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    retried_task.status = TaskStatus.RUNNING
    retried_task.fencing_token = 3
    async_session.add(next_attempt)
    await async_session.commit()

    exhausted = await task_repo.fail_task_attempt(task.task_id, "worker-test", 3, "connection timeout")
    assert exhausted == {
        "retrying": False,
        "retry_count": 1,
        "retry_after_seconds": 0,
        "reassigned": False,
        "previous_employee_id": None,
        "assigned_employee_id": None,
    }
    final_task = await task_repo.get_by_task_id(task.task_id)
    assert final_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_retryable_worker_failure_reassigns_to_idle_employee_with_live_desktop(async_session):
    employee_repo = EmployeeRepository(async_session)
    failed_employee = await employee_repo.create(
        employee_id="ANC-EMP-FAILED-WORKER",
        name="Failed Worker",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.WORKING,
    )
    recovery_employee = await employee_repo.create(
        employee_id="ANC-EMP-RECOVERY-WORKER",
        name="Recovery Worker",
        title="Software Engineer",
        system_prompt="Test recovery employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-recovery-worker",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/recovery-worker-validation",
    )
    await employee_repo.assign_container(recovery_employee.employee_id, container.id)
    await employee_repo.add_skill(
        recovery_employee.employee_id,
        "Python",
        level=SkillLevel.EXPERT,
        verified=True,
    )
    await async_session.refresh(recovery_employee)

    project = await ProjectRepository(async_session).create(
        name="Worker reassignment",
        objective="Retry eligible work with a different live employee",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-REASSIGN-AFTER-FAILURE",
        project_id=project.id,
        assigned_to=failed_employee.id,
        title="Recover failed worker task",
        objective="Reassign retryable work to an available specialist",
        scope="Recovery validation",
        acceptance_criteria="The next attempt uses the eligible recovery employee",
        required_capabilities=["Python"],
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        max_retries=2,
        fencing_token=7,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="terminal-worker",
        fencing_token=7,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    recovery = await task_repo.fail_task_attempt(
        task.task_id,
        "terminal-worker",
        7,
        "connection timeout",
    )

    assert recovery == {
        "retrying": True,
        "retry_count": 1,
        "retry_after_seconds": 1,
        "reassigned": True,
        "previous_employee_id": failed_employee.employee_id,
        "assigned_employee_id": recovery_employee.employee_id,
    }
    persisted = await task_repo.get_by_task_id(task.task_id)
    old_employee = await employee_repo.get_by_employee_id(failed_employee.employee_id)
    reserved_employee = await employee_repo.get_by_employee_id(recovery_employee.employee_id)
    assert persisted.status == TaskStatus.ASSIGNED
    assert persisted.assigned_to == recovery_employee.id
    assert persisted.fencing_token == 8
    assert old_employee.status == EmployeeStatus.IDLE
    assert reserved_employee.status == EmployeeStatus.WORKING


@pytest.mark.asyncio
async def test_worker_failure_route_returns_and_audits_recovery_decision(async_session, monkeypatch):
    from neural.config import get_settings

    owner_token = "owner-recovery-" + "o" * 40
    worker_token = "worker-recovery-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("WORKER_API_TOKENS", "{}")
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-ROUTE-RECOVERY",
        name="Route Recovery Worker",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.WORKING,
    )
    project = await ProjectRepository(async_session).create(
        name="Worker failure route",
        objective="Persist a retry and recovery decision",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-WORKER-FAILURE-ROUTE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Retry failed execution",
        objective="Route the failure through Boss recovery policy",
        scope="Recovery route test",
        acceptance_criteria="The recovery decision is audited",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=12,
    )
    async_session.add(
        TaskAttempt(
            task_id=task.id,
            attempt_number=1,
            worker_id="worker-service",
            fencing_token=12,
            status="running",
            lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
            execution_receipt={},
        )
    )
    await async_session.commit()

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/fail",
            headers={"Authorization": f"Bearer {worker_token}"},
            json={"fencing_token": 12, "error_message": "connection timeout"},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "retrying"
    assert response.json()["reassigned"] is False
    events = await AuditRepository(async_session).list_all()
    recovery_events = [event for event in events if event.action == "TASK_FAILURE_RECOVERY"]
    assert recovery_events
    assert recovery_events[-1].actor == "BossAI"
    assert recovery_events[-1].details["retrying"] is True
    assert recovery_events[-1].details["previous_employee_id"] == employee.employee_id


@pytest.mark.asyncio
async def test_verifier_rejection_retries_with_backoff_then_fails_closed(async_session):
    project = await ProjectRepository(async_session).create(name="Verifier recovery", objective="Retry rejected work")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-VERIFIER-RETRY",
        project_id=project.id,
        title="Retry rejected output",
        objective="Correct failing acceptance checks",
        scope="Execution",
        acceptance_criteria="Independent verification passes",
        status=TaskStatus.RUNNING,
        fencing_token=1,
        max_retries=1,
    )
    first_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-test",
        fencing_token=1,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
        execution_receipt={"verification_status": "awaiting_independent_verifier"},
    )
    async_session.add(first_attempt)
    await async_session.commit()

    rejection = {
        "passed": False,
        "notes": "Acceptance check failed.",
        "evidence_refs": ["command:pytest"],
        "checks": [{"name": "tests", "passed": False}],
    }
    first_result = await task_repo.review_task_attempt(task.task_id, first_attempt.id, "verifier-test", rejection)
    assert first_result is not None
    assert first_result["task_status"] == TaskStatus.ASSIGNED.value
    assert first_result["attempt_status"] == "rejected"
    assert first_result["retrying"] is True
    assert first_result["retry_count"] == 1
    assert first_result["retry_after_seconds"] == 1
    assert first_result["fencing_token"] == 2
    assert await task_repo.list_pending_verification() == []

    second_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=2,
        worker_id="worker-test",
        fencing_token=2,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
        execution_receipt={"verification_status": "awaiting_independent_verifier"},
    )
    task.status = TaskStatus.RUNNING
    async_session.add(second_attempt)
    await async_session.commit()

    exhausted = await task_repo.review_task_attempt(task.task_id, second_attempt.id, "verifier-test", rejection)
    assert exhausted is not None
    assert exhausted["task_status"] == TaskStatus.FAILED.value
    assert exhausted["attempt_status"] == "rejected"
    assert exhausted["retrying"] is False
    assert exhausted["retry_count"] == 1
    assert exhausted["retry_after_seconds"] == 0
    assert exhausted["fencing_token"] == 3


@pytest.mark.asyncio
async def test_expired_worker_lease_consumes_retry_budget(async_session):
    project = await ProjectRepository(async_session).create(name="Lease recovery", objective="Recover worker loss")
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LEASE-TEST",
        name="Lease Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-EXPIRED-LEASE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Recover expired worker",
        objective="Bound lease retries",
        scope="Execution",
        acceptance_criteria="Retry budget is respected",
        status=TaskStatus.RUNNING,
        fencing_token=1,
        max_retries=1,
    )
    expired_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-lost",
        fencing_token=1,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        execution_receipt={},
    )
    async_session.add(expired_attempt)
    await async_session.commit()

    assert await task_repo.claim_next_task("worker-replacement") is None
    recovered = await task_repo.get_by_task_id(task.task_id)
    assert recovered.status == TaskStatus.ASSIGNED
    assert recovered.retry_count == 1
    assert recovered.fencing_token == 2
    report = await BossBrain(project_repo=ProjectRepository(async_session), task_repo=task_repo).build_project_report(project.id)
    assert report is not None
    assert report["task_counts"]["retrying"] == 1
    retried_report = report["tasks"][0]
    assert retried_report["retry_count"] == 1
    assert retried_report["max_retries"] == 1
    await async_session.refresh(expired_attempt)
    assert expired_attempt.status == "failed"
    assert expired_attempt.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert await task_repo.claim_next_task("worker-replacement") is None


@pytest.mark.asyncio
async def test_expired_worker_lease_exhaustion_fails_task_and_releases_employee(async_session):
    project = await ProjectRepository(async_session).create(name="Lease exhaustion", objective="Stop retry loops")
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-LEASE-EXHAUST",
        name="Exhausted Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-EXPIRED-LEASE-EXHAUST",
        project_id=project.id,
        assigned_to=employee.id,
        title="Exhaust worker retries",
        objective="Stop retrying after the budget",
        scope="Execution",
        acceptance_criteria="Task fails and worker is released",
        status=TaskStatus.RUNNING,
        fencing_token=4,
        retry_count=1,
        max_retries=1,
    )
    expired_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=2,
        worker_id="worker-lost",
        fencing_token=4,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        execution_receipt={},
    )
    async_session.add(expired_attempt)
    await async_session.commit()

    assert await task_repo.claim_next_task("worker-replacement") is None
    failed_task = await task_repo.get_by_task_id(task.task_id)
    released_employee = await employee_repo.get_by_employee_id(employee.employee_id)
    assert failed_task.status == TaskStatus.FAILED
    assert failed_task.retry_count == 1
    assert released_employee.status == EmployeeStatus.IDLE


@pytest.mark.asyncio
async def test_cancelling_task_fences_and_closes_active_worker_attempt(async_session):
    project = await ProjectRepository(async_session).create(name="Cancellation", objective="Stop active work")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-CANCEL-ACTIVE-WORKER",
        project_id=project.id,
        title="Cancel leased work",
        objective="Stop the current worker attempt",
        scope="Execution",
        acceptance_criteria="Attempt is terminal and stale heartbeat is rejected",
        status=TaskStatus.RUNNING,
        fencing_token=7,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-active",
        fencing_token=7,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    await task_repo.update_status(task.task_id, TaskStatus.CANCELLED)
    cancelled_task = await task_repo.get_by_task_id(task.task_id)
    await async_session.refresh(attempt)
    assert cancelled_task.status == TaskStatus.CANCELLED
    assert cancelled_task.fencing_token == 8
    assert attempt.status == "cancelled"
    assert attempt.finished_at is not None
    assert await task_repo.heartbeat_task_attempt(task.task_id, "worker-active", 7) is None


@pytest.mark.asyncio
async def test_worker_claim_filters_execution_modes_before_leasing(async_session):
    project = await ProjectRepository(async_session).create(name="Mode filtering", objective="Only claim supported work")
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-MODE-TEST",
        name="Mode Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-TERMINAL-MODE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Terminal task",
        objective="Run terminal work",
        scope="Execution",
        acceptance_criteria="A supported-mode worker may claim this",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
    )

    assert await task_repo.claim_next_task("worker-gui", execution_modes=[ExecutionMode.GUI]) is None
    assert await task_repo.list_attempts_by_project(project.id) == []
    persisted = await task_repo.get_by_task_id(task.task_id)
    assert persisted.fencing_token == 0


@pytest.mark.asyncio
async def test_worker_claim_waits_until_scheduled_task_is_due(async_session):
    project = await ProjectRepository(async_session).create(name="Scheduled", objective="Wait for schedule")
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-scheduled-test",
        image="test-image",
        status=ContainerStatus.RUNNING,
        workspace_path="/tmp/scheduled-test",
    )
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-SCHEDULED-TEST",
        name="Scheduled Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.IDLE,
        container_id=container.id,
    )
    task_repo = TaskRepository(async_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task = await task_repo.create(
        task_id="TASK-SCHEDULED-TEST",
        project_id=project.id,
        assigned_to=employee.id,
        title="Wait for start time",
        objective="Do not claim this work before its scheduled time",
        scope="Scheduling",
        acceptance_criteria="Claim only after the schedule is due",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        scheduled_at=now + timedelta(hours=1),
    )

    assert await task_repo.claim_next_task("scheduled-worker", execution_modes=[ExecutionMode.TERMINAL]) is None
    assert await task_repo.list_attempts_by_project(project.id) == []

    task.scheduled_at = now - timedelta(seconds=1)
    await async_session.commit()
    claimed = await task_repo.claim_next_task("scheduled-worker", execution_modes=[ExecutionMode.TERMINAL])
    assert claimed is not None
    assert claimed["task_id"] == task.task_id


@pytest.mark.asyncio
async def test_dag_scheduler_holds_future_tasks_until_their_start_time(async_session):
    from neural.tasks.scheduler import TaskScheduler

    project = await ProjectRepository(async_session).create(name="Scheduled DAG", objective="Wait for start time")
    task_repo = TaskRepository(async_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task = await task_repo.create(
        task_id="TASK-SCHEDULED-DAG",
        project_id=project.id,
        title="Wait for scheduled dispatch",
        objective="Remain pending until start time",
        scope="Scheduling",
        acceptance_criteria="Dispatch only after the scheduled time",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.PENDING,
        scheduled_at=now + timedelta(hours=1),
    )
    scheduler = TaskScheduler(task_repo=task_repo)

    assert await scheduler.get_ready_tasks(project.id) == []
    task.scheduled_at = now - timedelta(seconds=1)
    await async_session.commit()
    ready = await scheduler.get_ready_tasks(project.id)
    assert [ready_task.task_id for ready_task in ready] == [task.task_id]


def test_worker_terminal_command_contract_rejects_shells_and_unapproved_executables():
    assert WorkerCommandRequest.model_validate(
        {"fencing_token": 3, "command": ["pytest", "-q", "tests"]}
    ).command[0] == "pytest"
    with pytest.raises(ValueError):
        WorkerCommandRequest.model_validate({"fencing_token": 3, "command": ["sh", "-c", "id"]})


def test_verifier_command_contract_allows_only_test_runners():
    assert VerifierCommandRequest.model_validate(
        {"fencing_token": 3, "command": ["python", "-m", "pytest", "-q"]}
    ).command[0] == "python"
    with pytest.raises(ValueError):
        VerifierCommandRequest.model_validate({"fencing_token": 3, "command": ["python", "-c", "print(1)"]})
    with pytest.raises(ValueError):
        VerifierCommandRequest.model_validate({"fencing_token": 3, "command": ["npm", "install", "pkg"]})


def test_verifier_manifest_contract_requires_only_fencing_token():
    assert VerifierManifestRequest.model_validate({"fencing_token": 4}).fencing_token == 4


@pytest.mark.asyncio
async def test_worker_lease_validation_requires_active_fence(async_session):
    project = await ProjectRepository(async_session).create(name="Lease validation", objective="Fence execution")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-LEASE-VALIDATION",
        project_id=project.id,
        title="Validate current worker",
        objective="Allow execution only for active lease",
        scope="Execution",
        acceptance_criteria="Current attempt and fence match",
        status=TaskStatus.RUNNING,
        fencing_token=5,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-current",
        fencing_token=5,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    assert await task_repo.worker_lease_is_current(task.task_id, "worker-current", 5)
    assert not await task_repo.worker_lease_is_current(task.task_id, "worker-other", 5)
    assert not await task_repo.worker_lease_is_current(task.task_id, "worker-current", 4)


@pytest.mark.asyncio
async def test_worker_command_route_requires_current_terminal_lease(async_session):
    project = await ProjectRepository(async_session).create(name="Terminal execution", objective="Execute under fence")
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-EXEC-TEST",
        name="Terminal Executor",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-EXEC-GATE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Execute safe argv",
        objective="Run a command in the employee workspace",
        scope="Terminal command execution",
        acceptance_criteria="Broker returns an exit code",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=5,
    )
    async_session.add(TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-current",
        fencing_token=5,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1),
        execution_receipt={},
    ))
    await async_session.commit()

    class StubContainerManager:
        async def get_desktop_by_employee_id(self, *_args, **_kwargs):
            return {"status": "running", "docker_id": "ancos-desktop-anc-emp-exec-test"}

        async def execute_command(self, docker_id, command, workdir=None):
            assert docker_id == "ancos-desktop-anc-emp-exec-test"
            assert command == ["pytest", "-q"]
            assert workdir == "/home/employee/workspace"
            return {"exit_code": 0, "stdout": "1 passed", "stderr": ""}

    result = await execute_terminal_command(
        task_id=task.task_id,
        request=WorkerCommandRequest(fencing_token=5, command=["pytest", "-q"]),
        worker_id="worker-current",
        task_repo=task_repo,
        employee_repo=employee_repo,
        container_repo=None,
        container_manager=StubContainerManager(),
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "1 passed"

    with pytest.raises(HTTPException) as rejected:
        await execute_terminal_command(
            task_id=task.task_id,
            request=WorkerCommandRequest(fencing_token=4, command=["pytest", "-q"]),
            worker_id="worker-current",
            task_repo=task_repo,
            employee_repo=employee_repo,
            container_repo=None,
            container_manager=StubContainerManager(),
        )
    assert rejected.value.status_code == 409


@pytest.mark.asyncio
async def test_verifier_exec_requires_current_submitted_attempt(async_session):
    project = await ProjectRepository(async_session).create(name="Verifier execution", objective="Replay test safely")
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-VERIFY-EXEC",
        name="Verifier Target",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-VERIFY-EXEC",
        project_id=project.id,
        assigned_to=employee.id,
        title="Verify test output",
        objective="Rerun tests through the broker",
        scope="Terminal verification",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.RUNNING,
        fencing_token=8,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-current",
        fencing_token=8,
        status="submitted",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
        execution_receipt={"planned_commands": [["pytest", "-q"]]},
    )
    async_session.add(attempt)
    await async_session.commit()

    class StubContainerManager:
        async def get_desktop_by_employee_id(self, *_args, **_kwargs):
            return {"status": "running", "docker_id": "ancos-desktop-anc-emp-verify-exec"}

        async def execute_verifier_command(self, employee_id, command):
            assert employee_id == "ANC-EMP-VERIFY-EXEC"
            assert command == ["pytest", "-q"]
            return {"exit_code": 0, "stdout": "2 passed", "stderr": ""}

        async def capture_verifier_manifest(self, employee_id):
            assert employee_id == "ANC-EMP-VERIFY-EXEC"
            return {"complete": True, "files": []}

    result = await execute_verifier_test(
        task_id=task.task_id,
        attempt_id=attempt.id,
        request=VerifierCommandRequest(fencing_token=8, command=["pytest", "-q"]),
        task_repo=task_repo,
        employee_repo=employee_repo,
        container_repo=None,
        container_manager=StubContainerManager(),
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "2 passed"

    manifest = await capture_verifier_manifest(
        task_id=task.task_id,
        attempt_id=attempt.id,
        request=VerifierManifestRequest(fencing_token=8),
        task_repo=task_repo,
        employee_repo=employee_repo,
        container_repo=None,
        container_manager=StubContainerManager(),
    )
    assert manifest["complete"] is True

    with pytest.raises(HTTPException) as stale:
        await execute_verifier_test(
            task_id=task.task_id,
            attempt_id=attempt.id,
            request=VerifierCommandRequest(fencing_token=7, command=["pytest", "-q"]),
            task_repo=task_repo,
            employee_repo=employee_repo,
            container_repo=None,
            container_manager=StubContainerManager(),
        )
    assert stale.value.status_code == 409

    with pytest.raises(HTTPException) as stale_manifest:
        await capture_verifier_manifest(
            task_id=task.task_id,
            attempt_id=attempt.id,
            request=VerifierManifestRequest(fencing_token=7),
            task_repo=task_repo,
            employee_repo=employee_repo,
            container_repo=None,
            container_manager=StubContainerManager(),
        )
    assert stale_manifest.value.status_code == 409


@pytest.mark.asyncio
async def test_orphan_task_recovery_requires_independent_approval_and_fences_old_worker(
    async_session,
    monkeypatch,
):
    from neural.api.deps import get_container_manager
    from neural.config import get_settings
    from neural.containers.manager import DockerBrokerRequestError
    from neural.db.models import Approval

    owner_token = "owner-orphan-recovery-" + "o" * 40
    approver_token = "approver-orphan-recovery-" + "a" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("APPROVER_API_TOKEN", approver_token)
    monkeypatch.setenv("WORKER_API_TOKEN", "worker-orphan-recovery-" + "w" * 40)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-orphan-recovery-" + "v" * 40)
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-ORPHAN-RECOVERY",
        name="Orphan Recovery Worker",
        title="Software Engineer",
        system_prompt="Test recovery employee",
        status=EmployeeStatus.WORKING,
    )
    project = await ProjectRepository(async_session).create(
        name="Orphan recovery",
        objective="Safely requeue an unstarted task",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-ORPHAN-RECOVERY",
        project_id=project.id,
        assigned_to=employee.id,
        title="Recover unstarted work",
        objective="Requeue only after an independent approval",
        scope="Execution recovery",
        acceptance_criteria="The next worker uses a fresh fencing token",
        status=TaskStatus.RUNNING,
        fencing_token=12,
    )

    class MissingWorkstationBroker:
        async def _broker_request(self, method, path):
            assert method == "GET"
            assert path == "/v1/containers/ancos-desktop-anc-emp-orphan-recovery"
            raise DockerBrokerRequestError("not found", 404)

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_container_manager] = MissingWorkstationBroker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        task_url = f"/boss/tasks/{task.task_id}"
        requested = await client.post(
            f"{task_url}/recovery-approvals",
            json={"justification": "The assigned worker never started an attempt."},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert requested.status_code == 200
        approval_id = requested.json()["id"]

        unchanged = await TaskRepository(async_session).get_by_task_id(task.task_id)
        assert unchanged.status == TaskStatus.RUNNING
        assert unchanged.fencing_token == 12

        self_approval = await client.post(
            f"/security/approvals/{approval_id}/decide",
            json={"decision": "approved"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert self_approval.status_code == 401

        decision = await client.post(
            f"/security/approvals/{approval_id}/decide",
            json={"decision": "approved"},
            headers={"Authorization": f"Bearer {approver_token}"},
        )
        assert decision.status_code == 200

        recovered = await client.post(
            f"{task_url}/recover",
            json={"approval_id": approval_id},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == TaskStatus.PENDING.value
        assert recovered.json()["assigned_to"] is None
        assert recovered.json()["fencing_token"] == 13
        assert recovered.json()["employee_status"] == EmployeeStatus.IDLE.value

    persisted = await TaskRepository(async_session).get_by_task_id(task.task_id)
    assert persisted.status == TaskStatus.PENDING
    assert persisted.fencing_token == 13
    assert persisted.assigned_to is None
    approval = await async_session.get(Approval, approval_id)
    assert approval.consumed_at is not None
    events = await AuditRepository(async_session).list_all()
    recovery_events = [event for event in events if event.action == "TASK_RECOVERY_REQUEUED"]
    assert len(recovery_events) == 1
    assert recovery_events[0].details["fencing_token_before"] == 12
    assert recovery_events[0].details["fencing_token_after"] == 13


@pytest.mark.asyncio
async def test_runtime_heartbeat_endpoints_require_role_credentials_and_persist_latest_instance(
    async_session,
    monkeypatch,
):
    from neural.api.deps import get_service_heartbeat_repo
    from neural.config import get_settings
    from neural.db.repositories import ServiceHeartbeatRepository

    worker_token = "worker-runtime-" + "w" * 40
    verifier_token = "verifier-runtime-" + "v" * 40
    owner_token = "owner-runtime-" + "o" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", verifier_token)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-runtime-" + "a" * 40)
    get_settings.cache_clear()

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_service_heartbeat_repo] = lambda: ServiceHeartbeatRepository(async_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        worker_path = "/internal/worker/runtime/heartbeat"
        verifier_path = "/internal/verifier/runtime/heartbeat"
        assert (await client.post(worker_path, json={"instance_id": "x" * 32})).status_code == 401
        assert (
            await client.post(
                worker_path,
                json={"instance_id": "x" * 32},
                headers={"Authorization": f"Bearer {owner_token}"},
            )
        ).status_code == 401

        worker_receipt = await client.post(
            worker_path,
            json={"instance_id": "a" * 32},
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        verifier_receipt = await client.post(
            verifier_path,
            json={"instance_id": "b" * 32},
            headers={"Authorization": f"Bearer {verifier_token}"},
        )
        assert worker_receipt.status_code == 200
        assert worker_receipt.json()["service_id"] == "worker-service"
        assert verifier_receipt.status_code == 200
        assert verifier_receipt.json()["service_type"] == "verifier"

    rows = await ServiceHeartbeatRepository(async_session).list_all()
    assert {(row.service_type, row.instance_id) for row in rows} == {
        ("worker", "a" * 32),
        ("verifier", "b" * 32),
    }
