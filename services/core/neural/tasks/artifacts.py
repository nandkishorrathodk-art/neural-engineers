import hashlib
import os
from typing import Optional, Sequence, Union

from sqlalchemy.ext.asyncio import AsyncSession

from neural.config import get_settings
from neural.db.models import Artifact
from neural.db.repositories import ArtifactRepository
from neural.exceptions import ArtifactIntegrityError, ArtifactNotFoundError
from neural.logger import get_logger

logger = get_logger("ancos.tasks.artifacts")


class ArtifactManager:
    """
    Task Artifact Management & Checksum Versioning Engine.
    Stores and retrieves code deliverables, configuration specs, and test execution
    receipts with cryptographic SHA-256 checksums and immutable version histories.
    """

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        repository: Optional[ArtifactRepository] = None,
        storage_base_path: Optional[str] = None
    ):
        self.session = session
        self.repo = repository or (ArtifactRepository(session) if session else None)
        settings = get_settings()
        self.storage_base_path = os.path.abspath(
            storage_base_path or getattr(settings, "ARTIFACT_STORAGE_PATH", "./data/artifacts")
        )
        os.makedirs(self.storage_base_path, exist_ok=True)

    @staticmethod
    def calculate_checksum(content: Union[str, bytes]) -> str:
        """Computes SHA-256 hexadecimal digest of artifact content."""
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        return hashlib.sha256(content_bytes).hexdigest()

    async def store_artifact(
        self,
        project_id: str,
        name: str,
        content: Union[str, bytes],
        artifact_type: str = "code",
        task_id: Optional[str] = None
    ) -> Artifact:
        """
        Stores artifact content on disk with SHA-256 checksum and persists
        versioned metadata in the database.
        """
        if not self.repo:
            raise ValueError("ArtifactRepository or AsyncSession is required to store artifacts")

        checksum = self.calculate_checksum(content)

        # Sanitize name
        sanitized_name = name.replace("\\", "/").strip("/")
        base_filename = os.path.basename(sanitized_name) or "artifact.bin"

        # Determine next sequential version
        latest_version = await self.repo.get_latest_version(project_id, sanitized_name)
        new_version = latest_version + 1

        # Determine physical storage path: data/artifacts/{project_id}/{name}/v{version}/{filename}
        storage_dir = os.path.join(
            self.storage_base_path,
            project_id,
            sanitized_name.replace("/", "_"),
            f"v{new_version}"
        )
        os.makedirs(storage_dir, exist_ok=True)
        file_path = os.path.join(storage_dir, base_filename)

        # Write to disk in binary mode to prevent OS-level CRLF newline mangling
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        with open(file_path, "wb") as f:
            f.write(content_bytes)

        # Persist record in repository
        artifact = await self.repo.create(
            project_id=project_id,
            task_id=task_id,
            name=sanitized_name,
            artifact_type=artifact_type,
            path=file_path,
            checksum=checksum,
            version=new_version
        )

        logger.info(
            "Stored task artifact",
            artifact_id=artifact.id,
            name=sanitized_name,
            version=new_version,
            checksum=checksum[:12],
            path=file_path
        )
        return artifact

    async def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        """Retrieves artifact record by primary key ID."""
        if not self.repo:
            return None
        return await self.repo.get_by_id(artifact_id)

    async def get_artifact_by_name(
        self,
        project_id: str,
        name: str,
        version: Optional[int] = None
    ) -> Optional[Artifact]:
        """Retrieves latest or specific version of an artifact by name."""
        if not self.repo:
            return None
        sanitized_name = name.replace("\\", "/").strip("/")
        return await self.repo.get_by_name(project_id, sanitized_name, version=version)

    def get_content(self, artifact: Union[Artifact, str]) -> bytes:
        """
        Reads binary content of artifact from disk and verifies cryptographic integrity.
        """
        path = artifact.path if isinstance(artifact, Artifact) else artifact
        if not os.path.exists(path):
            raise ArtifactNotFoundError(f"Artifact file not found at path: {path}")

        with open(path, "rb") as f:
            content = f.read()

        # If artifact model provided, verify integrity
        if isinstance(artifact, Artifact):
            current_checksum = hashlib.sha256(content).hexdigest()
            if current_checksum != artifact.checksum:
                raise ArtifactIntegrityError(
                    f"Checksum mismatch for artifact {artifact.id}: expected {artifact.checksum}, got {current_checksum}"
                )

        return content

    def get_content_text(self, artifact: Union[Artifact, str], encoding: str = "utf-8") -> str:
        """Reads text content of artifact with integrity verification."""
        content_bytes = self.get_content(artifact)
        return content_bytes.decode(encoding)

    def verify_checksum(self, artifact: Artifact) -> bool:
        """
        Verifies whether file on disk matches stored SHA-256 checksum.
        Returns False if file is corrupted or tampered.
        """
        if not os.path.exists(artifact.path):
            return False
        with open(artifact.path, "rb") as f:
            content = f.read()
        return hashlib.sha256(content).hexdigest() == artifact.checksum

    async def list_artifacts(
        self,
        project_id: str,
        task_id: Optional[str] = None,
        artifact_type: Optional[str] = None
    ) -> Sequence[Artifact]:
        """Lists artifacts for a project, optionally filtered by task or type."""
        if not self.repo:
            return []
        if task_id:
            return await self.repo.list_by_task(task_id)
        return await self.repo.list_by_project(project_id, artifact_type=artifact_type)

    async def get_version_history(self, project_id: str, name: str) -> Sequence[Artifact]:
        """Returns full version history of an artifact in chronological order."""
        if not self.repo:
            return []
        sanitized_name = name.replace("\\", "/").strip("/")
        return await self.repo.list_versions(project_id, sanitized_name)

    async def rollback_to_version(
        self,
        project_id: str,
        name: str,
        target_version: int,
        task_id: Optional[str] = None
    ) -> Artifact:
        """
        Restores a previous historical version by saving its contents as the newest version.
        """
        target_artifact = await self.get_artifact_by_name(project_id, name, version=target_version)
        if not target_artifact:
            raise ArtifactNotFoundError(
                f"Cannot rollback: version {target_version} of artifact '{name}' does not exist"
            )

        content = self.get_content(target_artifact)
        return await self.store_artifact(
            project_id=project_id,
            name=name,
            content=content,
            artifact_type=target_artifact.artifact_type,
            task_id=task_id
        )
