import os

import pytest
from neural.db.repositories import ProjectRepository, TaskRepository
from neural.exceptions import ArtifactIntegrityError, ArtifactNotFoundError
from neural.tasks.artifacts import ArtifactManager
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_artifact_checksum_calculation():
    text = "def hello(): return 'world'"
    checksum = ArtifactManager.calculate_checksum(text)
    assert len(checksum) == 64
    # Deterministic SHA-256
    assert checksum == ArtifactManager.calculate_checksum(text.encode("utf-8"))
    assert checksum != ArtifactManager.calculate_checksum("def other(): pass")


@pytest.mark.asyncio
async def test_artifact_store_and_retrieve(async_session: AsyncSession, tmp_path):
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Project-Artifact-Test", objective="Artifact storage testing")

    storage_dir = str(tmp_path / "artifacts")
    manager = ArtifactManager(session=async_session, storage_base_path=storage_dir)

    code = "import fastapi\napp = fastapi.FastAPI()\n"
    artifact = await manager.store_artifact(
        project_id=project.id,
        name="api/main.py",
        content=code,
        artifact_type="code"
    )

    assert artifact.id is not None
    assert artifact.name == "api/main.py"
    assert artifact.version == 1
    assert artifact.checksum == ArtifactManager.calculate_checksum(code)
    assert os.path.exists(artifact.path)

    # Retrieve by ID
    loaded = await manager.get_artifact(artifact.id)
    assert loaded is not None
    assert loaded.id == artifact.id

    # Retrieve content
    content_text = manager.get_content_text(loaded)
    assert content_text == code
    assert manager.verify_checksum(loaded) is True


@pytest.mark.asyncio
async def test_artifact_versioning_increments(async_session: AsyncSession, tmp_path):
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Project-Versioning", objective="Version tracking")

    manager = ArtifactManager(session=async_session, storage_base_path=str(tmp_path / "artifacts"))

    v1_code = "# Version 1 implementation\nval = 1\n"
    v2_code = "# Version 2 implementation with bugfix\nval = 2\n"
    v3_code = "# Version 3 optimized\nval = 3\n"

    art1 = await manager.store_artifact(project.id, "service.py", v1_code, "code")
    art2 = await manager.store_artifact(project.id, "service.py", v2_code, "code")
    art3 = await manager.store_artifact(project.id, "service.py", v3_code, "code")

    assert art1.version == 1
    assert art2.version == 2
    assert art3.version == 3

    # Check version history
    history = await manager.get_version_history(project.id, "service.py")
    assert len(history) == 3
    assert [a.version for a in history] == [1, 2, 3]

    # Get latest version (defaults to v3)
    latest = await manager.get_artifact_by_name(project.id, "service.py")
    assert latest is not None
    assert latest.version == 3
    assert manager.get_content_text(latest) == v3_code

    # Get specific historical version (v1)
    historical_v1 = await manager.get_artifact_by_name(project.id, "service.py", version=1)
    assert historical_v1 is not None
    assert historical_v1.version == 1
    assert manager.get_content_text(historical_v1) == v1_code


@pytest.mark.asyncio
async def test_artifact_integrity_verification_and_tamper_detection(async_session: AsyncSession, tmp_path):
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Project-Integrity", objective="Tamper detection")

    manager = ArtifactManager(session=async_session, storage_base_path=str(tmp_path / "artifacts"))
    content = "SECRET_HASH_VERIFIED_DATA"
    art = await manager.store_artifact(project.id, "secure.txt", content, "config")

    assert manager.verify_checksum(art) is True

    # Tamper with the file on disk directly
    with open(art.path, "w", encoding="utf-8") as f:
        f.write("TAMPERED_MALICIOUS_CONTENT")

    # verify_checksum returns False
    assert manager.verify_checksum(art) is False

    # get_content raises ArtifactIntegrityError
    with pytest.raises(ArtifactIntegrityError):
        manager.get_content(art)


@pytest.mark.asyncio
async def test_artifact_list_by_project_task_and_type(async_session: AsyncSession, tmp_path):
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    project = await proj_repo.create(name="Project-Filtering", objective="Query testing")
    task1 = await task_repo.create(
        task_id="TASK-ART-01",
        project_id=project.id,
        title="Task 1",
        objective="Obj",
        scope="Scope",
        acceptance_criteria="Crit"
    )
    task2 = await task_repo.create(
        task_id="TASK-ART-02",
        project_id=project.id,
        title="Task 2",
        objective="Obj",
        scope="Scope",
        acceptance_criteria="Crit"
    )

    manager = ArtifactManager(session=async_session, storage_base_path=str(tmp_path / "artifacts"))

    await manager.store_artifact(project.id, "code.py", "print(1)", "code", task_id=task1.task_id)
    await manager.store_artifact(project.id, "config.yaml", "port: 80", "config", task_id=task1.task_id)
    await manager.store_artifact(project.id, "report.json", '{"pass": true}', "test_output", task_id=task2.task_id)

    # Filter by task1
    task1_artifacts = await manager.list_artifacts(project.id, task_id=task1.task_id)
    assert len(task1_artifacts) == 2

    # Filter by artifact_type
    configs = await manager.list_artifacts(project.id, artifact_type="config")
    assert len(configs) == 1
    assert configs[0].name == "config.yaml"


@pytest.mark.asyncio
async def test_artifact_rollback_to_version(async_session: AsyncSession, tmp_path):
    proj_repo = ProjectRepository(async_session)
    project = await proj_repo.create(name="Project-Rollback", objective="Rollback testing")

    manager = ArtifactManager(session=async_session, storage_base_path=str(tmp_path / "artifacts"))

    await manager.store_artifact(project.id, "app.py", "# Stable v1", "code")
    await manager.store_artifact(project.id, "app.py", "# Buggy v2", "code")

    # Rollback to v1
    v3_restored = await manager.rollback_to_version(project.id, "app.py", target_version=1)
    assert v3_restored.version == 3
    assert manager.get_content_text(v3_restored) == "# Stable v1"


@pytest.mark.asyncio
async def test_artifact_not_found_handling(async_session: AsyncSession, tmp_path):
    manager = ArtifactManager(session=async_session, storage_base_path=str(tmp_path / "artifacts"))
    with pytest.raises(ArtifactNotFoundError):
        manager.get_content("/non/existent/path.txt")

    with pytest.raises(ArtifactNotFoundError):
        await manager.rollback_to_version("dummy-proj", "non-existent.py", 1)
