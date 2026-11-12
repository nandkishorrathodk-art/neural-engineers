import pytest
from fastapi import HTTPException
from neural.api.routes.worker import start_task_attempt
from neural.api.schemas.requests import WorkerLeaseRequest
from neural.constants import ExecutionMode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.GUI, ExecutionMode.HYBRID, ExecutionMode.AUTHORIZED_TOOLS],
)
async def test_terminal_worker_releases_claim_for_unsupported_execution_mode(execution_mode):
    class StubTask:
        def __init__(self):
            self.assigned_to = None
            self.execution_mode = execution_mode

    class StubTaskRepository:
        released = None

        async def worker_claim_is_current(self, *_args):
            return True

        async def get_by_task_id(self, _task_id):
            return StubTask()

        async def release_task_claim(self, *args):
            self.released = args
            return True

    class UnexpectedDependency:
        def __getattr__(self, _name):
            raise AssertionError("Unsupported modes must be rejected before workspace access.")

    task_repo = StubTaskRepository()
    with pytest.raises(HTTPException) as rejected:
        await start_task_attempt(
            task_id="TASK-UNSUPPORTED-MODE",
            request=WorkerLeaseRequest(fencing_token=7),
            task_repo=task_repo,
            employee_repo=UnexpectedDependency(),
            container_repo=UnexpectedDependency(),
            container_manager=UnexpectedDependency(),
            worker_id="terminal-worker",
        )

    assert rejected.value.status_code == 409
    assert "cannot execute" in rejected.value.detail
    assert task_repo.released == (
        "TASK-UNSUPPORTED-MODE",
        "terminal-worker",
        7,
        f"Worker 'terminal-worker' cannot execute {execution_mode.value}-mode tasks.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.GUI, ExecutionMode.GUI_ONLY, ExecutionMode.HYBRID],
)
async def test_gui_worker_is_not_blocked_by_terminal_only_guard(execution_mode):
    """A GUI-identity worker must be allowed to start GUI-bearing tasks.

    The guard is per-identity: it used to reject every non-terminal task for every
    caller, which stranded GUI tasks claimed by the GUI worker itself.
    """
    from neural.constants import worker_declared_modes

    assert worker_declared_modes("gui-worker") is not None
    assert execution_mode in worker_declared_modes("gui-worker")
    # A generic identity names no mode family and stays unrestricted.
    assert worker_declared_modes("worker-service") is None
