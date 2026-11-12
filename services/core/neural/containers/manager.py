import asyncio
import base64
import os
from datetime import datetime, timezone
from typing import Any, Optional

from neural.config import get_settings
from neural.constants import ContainerStatus
from neural.containers.execution_limits import (
    BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS,
    command_with_timeout,
)
from neural.exceptions import ContainerError
from neural.logger import get_logger

try:
    from docker.errors import DockerException, NotFound

    import docker
except ImportError:
    docker = None  # type: ignore
    DockerException = Exception  # type: ignore
    NotFound = Exception  # type: ignore

logger = get_logger("ancos.containers")


class DockerBrokerRequestError(ContainerError):
    def __init__(self, message: str, status_code: int):
        self.status_code = status_code
        super().__init__(message)


class ContainerManager:
    """
    Manages isolated Docker workstations for AI employees.
    Provisions Xfce4 Linux desktops with TigerVNC, noVNC proxy, and developer toolchains.
    Enforces non-root execution, resource bounds, and sandbox isolation.
    """

    def __init__(self, docker_client: Optional[Any] = None):
        self.settings = get_settings()
        if docker_client is not None and self.settings.APP_ENV == "production":
            raise ContainerError("Direct Docker clients are prohibited in production; configure the restricted broker.")
        self._docker_client = docker_client
        # Track allocated ports: identifier -> (vnc_port, novnc_port)
        self._allocated_ports: dict[str, tuple[int, int]] = {}
        # Track in-memory active containers
        self._containers: dict[str, dict[str, Any]] = {}
        self._provision_lock = asyncio.Lock()
        from neural.containers.broker import RestrictedDockerBroker
        self.broker = RestrictedDockerBroker(workspace_base=self.settings.EMPLOYEE_WORKSPACE_BASE)
        try:
            from neural.security.kill_switch import get_kill_switch
            get_kill_switch().register_container_manager(self)
        except Exception:
            pass


    def _get_client(self) -> Optional[Any]:
        """Returns only an explicitly injected test client; the API never gets Docker socket access."""
        return self._docker_client

    def _has_remote_broker(self) -> bool:
        return bool(self.settings.DOCKER_BROKER_URL and self.settings.DOCKER_BROKER_TOKEN)

    async def _broker_request(
        self, method: str, path: str, timeout: float = 60.0, **kwargs: Any
    ) -> dict[str, Any]:
        if not self._has_remote_broker():
            raise ContainerError("Restricted Docker broker is not configured.")
        import httpx

        headers = {"Authorization": f"Bearer {self.settings.DOCKER_BROKER_TOKEN}"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    f"{self.settings.DOCKER_BROKER_URL.rstrip('/')}{path}",
                    headers=headers,
                    **kwargs,
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ContainerError("Restricted Docker broker returned an invalid response.")
            return data
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except Exception:
                detail = exc.response.text
            raise DockerBrokerRequestError(
                f"Restricted Docker broker rejected operation: {detail}",
                status_code=exc.response.status_code,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ContainerError(f"Restricted Docker broker request failed: {exc}") from exc

    async def _refresh_remote_status(
        self,
        spec: dict[str, Any],
        container_repo: Optional[Any] = None,
    ) -> dict[str, Any]:
        if self._get_client() is not None:
            return spec
        try:
            live = await self._broker_request("GET", f"/v1/containers/{spec['docker_id']}")
        except ContainerError as exc:
            missing_container = isinstance(exc, DockerBrokerRequestError) and exc.status_code == 404
            spec["status"] = ContainerStatus.FAILED.value if missing_container else "runtime_unavailable"
            spec["runtime_available"] = missing_container
            if missing_container and container_repo:
                try:
                    await container_repo.update_status(spec["docker_id"], ContainerStatus.FAILED)
                except Exception as persist_error:
                    logger.warning("Failed to persist missing workstation status", error=str(persist_error))
            logger.warning("Workstation status could not be refreshed", docker_id=spec["docker_id"], error=str(exc))
            return spec

        runtime_status = live.get("status")
        if runtime_status == "running":
            target_status = ContainerStatus.RUNNING
        elif runtime_status in {"paused", "exited", "created"}:
            target_status = ContainerStatus.STOPPED
        else:
            target_status = ContainerStatus.FAILED
        previous_status = spec.get("status")
        spec.update({key: value for key, value in live.items() if value is not None})
        spec["status"] = target_status.value
        spec["runtime_available"] = True
        if container_repo and previous_status != target_status.value:
            try:
                await container_repo.update_status(spec["docker_id"], target_status)
            except Exception as exc:
                logger.warning("Failed to persist refreshed workstation status", error=str(exc))
        if spec.get("novnc_port") and not live.get("desktop_url"):
            spec["desktop_url"] = f"http://localhost:{spec['novnc_port']}/vnc.html?autoconnect=true"
        return spec

    def allocate_ports(self, identifier: str, used_vnc_ports: Optional[set[int]] = None) -> tuple[int, int]:
        """
        Allocates unique sequential ports:
        VNC starts at VNC_PORT_START (5901)
        noVNC starts at NOVNC_PORT_START (6081)
        """
        if identifier in self._allocated_ports:
            return self._allocated_ports[identifier]

        used_vnc = {vnc for vnc, _ in self._allocated_ports.values()}
        if used_vnc_ports:
            used_vnc.update(used_vnc_ports)

        port_candidate = self.settings.VNC_PORT_START
        while port_candidate in used_vnc:
            port_candidate += 1

        offset = port_candidate - self.settings.VNC_PORT_START
        novnc_candidate = self.settings.NOVNC_PORT_START + offset

        self._allocated_ports[identifier] = (port_candidate, novnc_candidate)
        logger.info(
            "Allocated workstation ports",
            identifier=identifier,
            vnc_port=port_candidate,
            novnc_port=novnc_candidate
        )
        return port_candidate, novnc_candidate

    def release_ports(self, identifier: str) -> Optional[tuple[int, int]]:
        """Releases ports allocated to a container/employee."""
        released = self._allocated_ports.pop(identifier, None)
        if released:
            logger.info("Released workstation ports", identifier=identifier, ports=released)
        return released

    def ensure_workspace_dir(self, employee_id: str) -> str:
        """
        Ensures isolated workspace directory exists on host filesystem:
        ./data/workspaces/{employee_id}
        """
        base_dir = os.path.abspath(self.settings.EMPLOYEE_WORKSPACE_BASE)
        workspace = os.path.join(base_dir, employee_id)
        os.makedirs(workspace, exist_ok=True)
        return workspace

    async def provision_employee_desktop(
        self,
        employee_id: str,
        role_image: str = "employee-base",
        cpu_limit: Optional[float] = None,
        mem_limit: Optional[str] = None,
        container_repo: Optional[Any] = None,
        employee_repo: Optional[Any] = None
    ) -> dict[str, Any]:
        async with self._provision_lock:
            return await self._provision_employee_desktop_locked(
                employee_id=employee_id,
                role_image=role_image,
                cpu_limit=cpu_limit,
                mem_limit=mem_limit,
                container_repo=container_repo,
                employee_repo=employee_repo,
            )

    async def _provision_employee_desktop_locked(
        self,
        employee_id: str,
        role_image: str = "employee-base",
        cpu_limit: Optional[float] = None,
        mem_limit: Optional[str] = None,
        container_repo: Optional[Any] = None,
        employee_repo: Optional[Any] = None
    ) -> dict[str, Any]:
        """
        Provisions isolated desktop container for an employee with:
        - Image built from or referencing docker/Dockerfile.employee-base
        - Dedicated VNC (5901+) and noVNC (6081+) ports
        - Isolated workspace directory mounted
        - Non-root user (uid 1000)
        - Strict resource bounds (CPU, memory, pids)
        """
        logger.info(
            "Provisioning isolated desktop container for employee",
            employee_id=employee_id,
            image=role_image
        )

        active_statuses = {
            ContainerStatus.PROVISIONING.value,
            ContainerStatus.RUNNING.value,
            ContainerStatus.STOPPED.value,
        }
        try:
            # Import lazily: resource_scheduler's package exports DesktopSlotPool,
            # which itself imports ContainerManager.
            from neural.resource_scheduler.workstation_lease import workstation_slot_limit

            workstation_limit = workstation_slot_limit()
        except ValueError as exc:
            raise ContainerError("ANCOS_MAX_ACTIVE_DESKTOPS must be a non-negative integer.") from exc
        active_employees = {
            existing.get("employee_id")
            for existing in self._containers.values()
            if existing.get("status") in active_statuses
        }
        if employee_id not in active_employees and len(active_employees) >= workstation_limit:
            raise DockerBrokerRequestError(
                f"Desktop capacity is full ({len(active_employees)}/{workstation_limit}).",
                status_code=409,
            )
        docker_id = f"ancos-desktop-{employee_id.lower()}"
        if container_repo:
            existing_records = await container_repo.list_all()
            active_db = [
                row for row in existing_records
                if row.docker_id != docker_id
                and (row.status.value if hasattr(row.status, "value") else str(row.status)) in active_statuses
            ]
            if len(active_db) >= workstation_limit:
                raise DockerBrokerRequestError(
                    f"Desktop capacity is full ({len(active_db)}/{workstation_limit}).",
                    status_code=409,
                )
        client = self._get_client()
        if client is not None:
            existing_workstations = [
                item for item in client.containers.list(all=True)
                if item.name.startswith("ancos-desktop-") and item.name != docker_id
            ]
            if len(existing_workstations) >= workstation_limit:
                raise DockerBrokerRequestError(
                    f"Desktop capacity is full ({len(existing_workstations)}/{workstation_limit}).",
                    status_code=409,
                )
        effective_cpu = cpu_limit or self.settings.DEFAULT_CONTAINER_CPU_LIMIT
        effective_mem = mem_limit or self.settings.DEFAULT_CONTAINER_MEM_LIMIT
        if role_image.startswith("ancos/"):
            image_name = role_image if ":" in role_image else f"{role_image}:latest"
        else:
            image_name = f"ancos/{role_image}:latest" if ":" not in role_image else f"ancos/{role_image}"
        client = self._get_client()
        vnc_password: Optional[str] = None
        remote: dict[str, Any] = {}

        if client is None:
            remote = await self._broker_request(
                "POST",
                "/v1/containers",
                json={
                    "employee_id": employee_id,
                    "image": image_name,
                    "cpu_limit": effective_cpu,
                    "mem_limit": effective_mem,
                },
            )
            vnc_port = int(remote["vnc_port"])
            novnc_port = int(remote["novnc_port"])
            workspace_path = str(remote["workspace"])
            vnc_password = str(remote["vnc_password"])
            self._allocated_ports[docker_id] = (vnc_port, novnc_port)
        else:
            import secrets
            import string

            vnc_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
            used_ports = set()
            if container_repo:
                try:
                    all_c = await container_repo.list_all()
                    used_ports = {c.vnc_port for c in all_c if c.status == ContainerStatus.RUNNING}
                except Exception:
                    pass
            vnc_port, novnc_port = self.allocate_ports(docker_id, used_vnc_ports=used_ports)
            workspace_path = self.ensure_workspace_dir(employee_id)

            try:
                try:
                    existing = client.containers.get(docker_id)
                    existing.remove(force=True)
                except NotFound:
                    pass

                run_kwargs = {
                    "image": image_name,
                    "name": docker_id,
                    "detach": True,
                    "user": "1000:1000",
                    "ports": {"5901/tcp": vnc_port, "6081/tcp": novnc_port},
                    "volumes": {workspace_path: {"bind": "/home/employee/workspace", "mode": "rw"}},
                    "environment": {
                        "DISPLAY": ":1",
                        "VNC_PORT": str(vnc_port),
                        "NOVNC_PORT": str(novnc_port),
                        "EMPLOYEE_ID": employee_id,
                        "VNC_PASSWORD": vnc_password,
                    },
                    "nano_cpus": int(effective_cpu * 1e9),
                    "mem_limit": effective_mem,
                    "pids_limit": 200,
                    "privileged": False,
                    "security_opt": ["no-new-privileges:true"]
                }
                hardened_kwargs = self.broker.harden_container_kwargs(run_kwargs)
                client.containers.run(**hardened_kwargs)
            except Exception as e:
                logger.warning("Docker workstation provisioning failed", docker_id=docker_id, error=str(e))
                raise ContainerError(f"Workstation {docker_id} was not provisioned: {e}") from e

        container_spec: dict[str, Any] = {
            "docker_id": docker_id,
            "employee_id": employee_id,
            "image": image_name,
            "dockerfile_path": "broker-managed-image",
            "vnc_port": vnc_port,
            "novnc_port": novnc_port,
            "desktop_url": str(
                remote.get("desktop_url")
                or f"http://localhost:{novnc_port}/vnc.html?autoconnect=true"
            ),
            "cpu_limit": effective_cpu,
            "mem_limit": effective_mem,
            "pids_limit": 200,
            "user": "1000:1000",
            "privileged": False,
            "workspace": workspace_path,
            "status": str(remote.get("status", "running")) if client is None else ContainerStatus.RUNNING.value,
            "started_at": str(remote.get("started_at") or datetime.now(timezone.utc).isoformat()) if client is None else datetime.now(timezone.utc).isoformat(),
            "network": str(remote.get("network") or self.settings.EMPLOYEE_NETWORK)
        }

        self._containers[docker_id] = container_spec

        # Optional database persistence
        if container_repo:
            try:
                db_container = await container_repo.upsert_current(
                    docker_id=docker_id,
                    image=image_name,
                    status=ContainerStatus.RUNNING,
                    vnc_port=vnc_port,
                    novnc_port=novnc_port,
                    resource_limits={"cpu_limit": effective_cpu, "mem_limit": effective_mem, "pids_limit": 200},
                    network_config={"network": container_spec["network"]},
                    workspace_path=workspace_path
                )
                container_spec["db_id"] = db_container.id
                if employee_repo:
                    await employee_repo.assign_container(employee_id, db_container.id)
            except Exception as db_err:
                logger.exception(
                    "Workstation started but its database record could not be persisted",
                    docker_id=docker_id,
                    error=str(db_err),
                )
                raise ContainerError(
                    f"Workstation {docker_id} started, but its lifecycle record could not be persisted."
                ) from db_err

        logger.info(
            "Desktop workstation ready",
            employee_id=employee_id,
            desktop_url=(
                "localhost:12003/workstation session"
                if "token=" in container_spec["desktop_url"]
                else container_spec["desktop_url"]
            ),
            vnc_port=vnc_port,
            novnc_port=novnc_port
        )
        response_spec = dict(container_spec)
        if vnc_password:
            response_spec["vnc_password"] = vnc_password
        return response_spec

    async def terminate_desktop(self, docker_id: str, container_repo: Optional[Any] = None) -> None:
        """Stops and removes container, releases ports and updates status."""
        logger.info("Terminating desktop container", docker_id=docker_id)
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            await self._broker_request("POST", f"/v1/containers/{docker_id}/terminate")
        else:
            try:
                container = client.containers.get(docker_id)
                container.stop(timeout=5)
                container.remove()
                logger.info("Docker container removed", docker_id=docker_id)
            except Exception as e:
                logger.warning("Docker workstation termination failed", docker_id=docker_id, error=str(e))
                raise ContainerError(f"Workstation {docker_id} was not terminated: {e}") from e

        self.release_ports(docker_id)
        if docker_id in self._containers:
            self._containers[docker_id]["status"] = ContainerStatus.TERMINATED.value

        if container_repo:
            try:
                await container_repo.update_status(docker_id, ContainerStatus.TERMINATED)
            except Exception as ex:
                logger.debug("Failed to update container status in DB", error=str(ex))

    async def get_desktop_status(
        self,
        docker_id: str,
        container_repo: Optional[Any] = None
    ) -> Optional[dict[str, Any]]:
        """Retrieves live or cached status of desktop workstation, with DB recovery."""
        spec = self._containers.get(docker_id)
        if spec:
            if spec.get("status") == ContainerStatus.TERMINATED.value:
                return spec
            return await self._refresh_remote_status(spec, container_repo)

        if container_repo:
            try:
                db_c = await container_repo.get_by_docker_id(docker_id)
                if db_c:
                    recovered_spec = {
                        "docker_id": db_c.docker_id,
                        "image": db_c.image,
                        "status": db_c.status.value if hasattr(db_c.status, "value") else str(db_c.status),
                        "vnc_port": db_c.vnc_port,
                        "novnc_port": db_c.novnc_port,
                        "desktop_url": f"http://localhost:{db_c.novnc_port}/vnc.html?autoconnect=true",
                        "cpu_limit": (db_c.resource_limits or {}).get("cpu_limit", self.settings.DEFAULT_CONTAINER_CPU_LIMIT),
                        "mem_limit": (db_c.resource_limits or {}).get("mem_limit", self.settings.DEFAULT_CONTAINER_MEM_LIMIT),
                        "pids_limit": 200,
                        "user": "1000:1000",
                        "privileged": False,
                        "workspace": db_c.workspace_path or self.ensure_workspace_dir(docker_id.replace("ancos-desktop-", "")),
                        "started_at": db_c.started_at.isoformat() if db_c.started_at else None
                    }
                    self._containers[docker_id] = recovered_spec
                    self._allocated_ports[docker_id] = (db_c.vnc_port, db_c.novnc_port)
                    return await self._refresh_remote_status(recovered_spec, container_repo)
            except Exception as e:
                logger.debug("Failed to lookup container from repo", error=str(e))
        return None

    async def get_desktop_by_employee_id(
        self,
        employee_id: str,
        container_repo: Optional[Any] = None,
        employee_repo: Optional[Any] = None
    ) -> Optional[dict[str, Any]]:
        """Finds desktop workstation specification for a given employee ID, recovering from DB if needed."""
        for spec in self._containers.values():
            if spec.get("employee_id") == employee_id:
                return await self._refresh_remote_status(spec, container_repo)

        docker_id = f"ancos-desktop-{employee_id.lower()}"
        if docker_id in self._containers:
            return await self._refresh_remote_status(self._containers[docker_id], container_repo)

        if employee_repo and container_repo:
            try:
                emp = await employee_repo.get_by_employee_id(employee_id)
                if emp and emp.container_id:
                    db_c = await container_repo.get_by_id(emp.container_id)
                    if db_c:
                        recovered_spec = {
                            "docker_id": db_c.docker_id,
                            "employee_id": emp.employee_id,
                            "image": db_c.image,
                            "status": db_c.status.value if hasattr(db_c.status, "value") else str(db_c.status),
                            "vnc_port": db_c.vnc_port,
                            "novnc_port": db_c.novnc_port,
                            "desktop_url": f"http://localhost:{db_c.novnc_port}/vnc.html?autoconnect=true",
                            "cpu_limit": (db_c.resource_limits or {}).get("cpu_limit", self.settings.DEFAULT_CONTAINER_CPU_LIMIT),
                            "mem_limit": (db_c.resource_limits or {}).get("mem_limit", self.settings.DEFAULT_CONTAINER_MEM_LIMIT),
                            "pids_limit": 200,
                            "user": "1000:1000",
                            "privileged": False,
                            "workspace": db_c.workspace_path or self.ensure_workspace_dir(emp.employee_id),
                            "started_at": db_c.started_at.isoformat() if db_c.started_at else None
                        }
                        self._containers[db_c.docker_id] = recovered_spec
                        self._allocated_ports[db_c.docker_id] = (db_c.vnc_port, db_c.novnc_port)
                        return await self._refresh_remote_status(recovered_spec, container_repo)
            except Exception as e:
                logger.debug("Failed to recover desktop spec from DB", error=str(e))

        return None

    async def list_active_desktops(self) -> list[dict[str, Any]]:
        """Lists all active desktop workstation specifications."""
        return [await self._refresh_remote_status(spec) for spec in self._containers.values()]

    async def restart_desktop(
        self,
        docker_id: str,
        container_repo: Optional[Any] = None
    ) -> dict[str, Any]:
        """
        Restarts a desktop container, fulfilling NFR-2 (recovery from container crashes).
        Preserves port assignments and workspace volume mounts.
        """
        logger.info("Restarting desktop workstation", docker_id=docker_id)
        self.broker.validate_container_id(docker_id)
        spec = self._containers.get(docker_id)
        if not spec:
            raise ContainerError(f"Container {docker_id} not found in manager registry")

        client = self._get_client()
        if client is None:
            remote = await self._broker_request("POST", f"/v1/containers/{docker_id}/restart")
        else:
            try:
                container = client.containers.get(docker_id)
                container.restart(timeout=5)
                logger.info("Docker container restarted", docker_id=docker_id)
                remote = {}
            except Exception as e:
                logger.warning("Docker workstation restart failed", docker_id=docker_id, error=str(e))
                raise ContainerError(f"Workstation {docker_id} was not restarted: {e}") from e

        spec["status"] = str(remote.get("status", ContainerStatus.RUNNING.value))
        spec["started_at"] = str(remote.get("started_at") or datetime.now(timezone.utc).isoformat())

        if container_repo:
            try:
                await container_repo.update_status(docker_id, ContainerStatus.RUNNING)
            except Exception as ex:
                logger.debug("Failed to update container status in DB", error=str(ex))

        return spec

    async def execute_command(
        self,
        docker_id: str,
        command: str | list[str],
        workdir: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Executes a command within the employee's isolated container environment.
        Enforces non-root execution (uid 1000).
        """
        logger.info(
            "Executing operation in employee workstation",
            docker_id=docker_id,
            command_kind="argv" if isinstance(command, list) else "shell",
            argument_count=len(command) if isinstance(command, list) else None,
        )
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            return await self._broker_request(
                "POST",
                f"/v1/containers/{docker_id}/exec",
                timeout=float(BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS),
                json={
                    "command": command,
                    "workdir": workdir or "/home/employee/workspace",
                },
            )

        try:
            container = client.containers.get(docker_id)
            exec_res = container.exec_run(
                cmd=command_with_timeout(command),
                user="1000:1000",
                workdir=workdir or "/home/employee/workspace"
            )
            stdout = exec_res.output.decode("utf-8", errors="replace") if exec_res.output else ""
            return {"exit_code": exec_res.exit_code, "stdout": stdout, "stderr": ""}
        except Exception as e:
            logger.warning("Docker exec failed", docker_id=docker_id, error=str(e))
            raise ContainerError(f"Command was not executed in container {docker_id}: {e}") from e

    async def execute_verifier_command(self, employee_id: str, command: list[str]) -> dict[str, Any]:
        """Run verifier tests through the broker in a disposable read-only workspace container."""
        return await self._broker_request(
            "POST",
            "/v1/verifier/exec",
            timeout=310.0,
            json={"employee_id": employee_id, "command": command},
        )

    def execute_command_sync(
        self,
        docker_id: str,
        command: str | list[str],
        workdir: Optional[str] = None,
    ) -> dict[str, Any]:
        """Synchronous variant of :meth:`execute_command` for thread-bound drivers.

        The GUI execution loop is synchronous, so it runs in a worker thread; this
        blocks that thread (never the event loop) while the command runs inside the
        employee container through the same restricted-broker path.
        """
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            if not self._has_remote_broker():
                raise ContainerError("Restricted Docker broker is not configured.")
            import httpx

            headers = {"Authorization": f"Bearer {self.settings.DOCKER_BROKER_TOKEN}"}
            try:
                with httpx.Client(timeout=float(BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS)) as http:
                    response = http.post(
                        f"{self.settings.DOCKER_BROKER_URL.rstrip('/')}/v1/containers/{docker_id}/exec",
                        headers=headers,
                        json={"command": command, "workdir": workdir or "/home/employee/workspace"},
                    )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ContainerError("Restricted Docker broker returned an invalid response.")
                return data
            except httpx.HTTPStatusError as exc:
                raise DockerBrokerRequestError(
                    f"Restricted Docker broker rejected operation: {exc.response.text}",
                    status_code=exc.response.status_code,
                ) from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise ContainerError(f"Restricted Docker broker request failed: {exc}") from exc

        try:
            container = client.containers.get(docker_id)
            exec_res = container.exec_run(
                cmd=command_with_timeout(command),
                user="1000:1000",
                workdir=workdir or "/home/employee/workspace",
            )
            stdout = exec_res.output.decode("utf-8", errors="replace") if exec_res.output else ""
            return {"exit_code": exec_res.exit_code, "stdout": stdout, "stderr": ""}
        except Exception as e:
            logger.warning("Docker exec failed", docker_id=docker_id, error=str(e))
            raise ContainerError(f"Command was not executed in container {docker_id}: {e}") from e

    async def capture_verifier_manifest(self, employee_id: str) -> dict[str, Any]:
        """Capture path, size, and SHA-256 metadata through the restricted broker."""
        return await self._broker_request(
            "POST",
            "/v1/verifier/manifest",
            timeout=310.0,
            json={"employee_id": employee_id},
        )

    async def capture_screenshot(self, docker_id: str) -> bytes:
        """Capture real workstation pixels through the restricted broker."""
        self.broker.validate_container_id(docker_id)
        result = await self.execute_command(
            docker_id,
            [
                "sh", "-lc",
                "if ! command -v scrot >/dev/null 2>&1; then echo 'scrot is not installed' >&2; exit 127; fi; scrot - | base64 -w 0",
            ],
            workdir="/home/employee/workspace",
        )
        if result.get("exit_code") != 0:
            raise ContainerError("Live workstation screenshot capture failed.")
        encoded = str(result.get("stdout", "")).strip()
        if not encoded or len(encoded) > 24_000_000:
            raise ContainerError("Live workstation screenshot is empty or exceeds the size limit.")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ContainerError("Live workstation returned an invalid screenshot payload.") from exc
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ContainerError("Live workstation screenshot is not a PNG image.")
        return image_bytes

    async def get_or_issue_vnc_token(self, docker_id: str, *, rotate: bool = False) -> dict[str, Any]:
        """Issue (or fetch) a workstation's noVNC token through the broker.

        The broker owns the token file the gateway reads and the only Docker
        connection that can repair the gateway bridge, so tokens must be minted
        there. Minting them in the core API's own filesystem produced URLs the
        gateway could never validate.
        """
        self.broker.validate_container_id(docker_id)
        if self._get_client() is None:
            return await self._broker_request(
                "POST",
                f"/v1/containers/{docker_id}/vnc-token",
                params={"rotate": "true"} if rotate else None,
            )
        from neural.containers.broker_api import read_vnc_token, write_vnc_token

        token = read_vnc_token(docker_id)
        if not token or rotate:
            token = write_vnc_token(docker_id)
        password = (self._containers.get(docker_id) or {}).get("vnc_password")
        suffix = f"&password={password}" if password else ""
        novnc_port = (self._containers.get(docker_id) or {}).get("novnc_port") or 12003
        return {
            "docker_id": docker_id,
            "token": token,
            "ws_path": f"websockify?token={token}",
            "desktop_url": (
                f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&path=websockify%3Ftoken%3D{token}{suffix}"
            ),
            "read_only_desktop_url": (
                f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&view_only=true"
                f"&path=websockify%3Ftoken%3D{token}{suffix}"
            ),
        }

    async def rotate_vnc_password(self, docker_id: str) -> str:
        """Rotates one workstation's VNC credential and returns it once to the caller."""
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            result = await self._broker_request("POST", f"/v1/containers/{docker_id}/vnc-password")
            return str(result["vnc_password"])

        import secrets
        import string

        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        try:
            container = client.containers.get(docker_id)
            result = container.exec_run(
                cmd=[
                    "/bin/sh",
                    "-c",
                    "printf '%s\\n' \"$1\" | vncpasswd -f > /home/employee/.vnc/passwd && chmod 600 /home/employee/.vnc/passwd && (vncserver -kill :1 >/dev/null 2>&1 || true) && vncserver :1 -geometry 1280x800 -depth 24 -localhost no",
                    "ancos-vnc-password",
                    password,
                ],
                user="1000:1000",
                workdir="/home/employee/workspace",
            )
            if result.exit_code != 0:
                raise ContainerError("VNC credential rotation failed.")
            return password
        except Exception as exc:
            logger.warning("VNC credential rotation failed", docker_id=docker_id)
            raise ContainerError(f"VNC credential rotation failed for {docker_id}.") from exc

    async def build_employee_image(
        self,
        tag: str = "ancos/employee-base:latest",
        dockerfile: str = "docker/Dockerfile.employee-base"
    ) -> bool:
        """
        Builds the employee base Docker workstation image using Dockerfile.employee-base.
        """
        abs_dockerfile = os.path.abspath(dockerfile)
        client = self._get_client()
        if client is None:
            if not self._has_remote_broker():
                logger.warning("No restricted Docker broker is configured; employee image was not built", tag=tag)
                return False
            try:
                result = await self._broker_request(
                    "POST",
                    "/v1/images/build",
                    json={"tag": tag},
                )
                return result.get("built") is True and result.get("tag") == tag
            except ContainerError as e:
                logger.warning("Restricted employee image build failed", error=str(e))
                return False
        if not os.path.exists(abs_dockerfile):
            logger.error("Employee Dockerfile not found", path=abs_dockerfile)
            return False
        try:
            logger.info("Building employee workstation image", tag=tag, dockerfile=abs_dockerfile)
            client.images.build(
                path=os.path.abspath("."),
                dockerfile=dockerfile,
                tag=tag,
                rm=True
            )
            logger.info("Successfully built employee workstation image", tag=tag)
            return True
        except Exception as e:
            logger.warning("Docker image build failed", error=str(e))
            return False

    async def inspect_workstation_health(self, docker_id: str) -> dict[str, Any]:
        """Inspects live health, resource utilization, and ports of a workstation."""
        self.broker.validate_container_id(docker_id)
        spec = self._containers.get(docker_id)
        if not spec:
            return {"status": "not_found", "docker_id": docker_id}

        client = self._get_client()
        if client is None:
            spec = await self._refresh_remote_status(spec)
            if not spec.get("runtime_available"):
                return {
                    "docker_id": docker_id,
                    "status": "runtime_unavailable",
                    "runtime_available": False,
                    "healthy": False,
                }
        else:
            try:
                container = client.containers.get(docker_id)
                container.reload()
                runtime_status = container.status
                spec["status"] = (
                    ContainerStatus.RUNNING.value
                    if runtime_status == "running"
                    else ContainerStatus.STOPPED.value
                )
            except Exception as e:
                logger.warning("Workstation health inspection failed", docker_id=docker_id, error=str(e))
                return {
                    "docker_id": docker_id,
                    "status": "inspection_failed",
                    "runtime_available": True,
                    "healthy": False,
                }

        return {
            "docker_id": docker_id,
            "employee_id": spec.get("employee_id"),
            "status": spec.get("status"),
            "runtime_available": True,
            "vnc_port": spec.get("vnc_port"),
            "novnc_port": spec.get("novnc_port"),
            "desktop_url": spec.get("desktop_url"),
            "cpu_limit": spec.get("cpu_limit"),
            "mem_limit": spec.get("mem_limit"),
            "user": spec.get("user"),
            "privileged": spec.get("privileged"),
            "healthy": spec.get("status") == ContainerStatus.RUNNING.value
        }

    async def freeze_all_workstations(self) -> int:
        """Emergency halt: immediately freezes or pauses all active workstation containers."""
        client = self._get_client()
        if client is None:
            if not self._has_remote_broker():
                logger.error("Cannot freeze workstations because restricted Docker broker is unavailable")
                return 0
            try:
                return int((await self._broker_request("POST", "/v1/workstations/freeze")).get("frozen", 0))
            except ContainerError as e:
                logger.error("Restricted Docker broker could not freeze workstations", error=str(e))
                return 0
        frozen_count = 0
        for docker_id, spec in self._containers.items():
            if spec.get("status") == ContainerStatus.RUNNING.value:
                try:
                    c = client.containers.get(docker_id)
                    c.pause()
                    spec["status"] = ContainerStatus.STOPPED.value
                    frozen_count += 1
                except Exception as e:
                    logger.error("Container pause failed", docker_id=docker_id, error=str(e))
        logger.warning("All active workstations frozen by emergency stop", count=frozen_count)
        return frozen_count

    async def unfreeze_all_workstations(self) -> int:
        """Resumes frozen workstation containers upon emergency resume."""
        client = self._get_client()
        if client is None:
            if not self._has_remote_broker():
                logger.error("Cannot resume workstations because restricted Docker broker is unavailable")
                return 0
            try:
                return int((await self._broker_request("POST", "/v1/workstations/resume")).get("resumed", 0))
            except ContainerError as e:
                logger.error("Restricted Docker broker could not resume workstations", error=str(e))
                return 0
        resumed_count = 0
        for docker_id, spec in self._containers.items():
            if spec.get("status") == ContainerStatus.STOPPED.value:
                try:
                    c = client.containers.get(docker_id)
                    c.unpause()
                    spec["status"] = ContainerStatus.RUNNING.value
                    resumed_count += 1
                except Exception as e:
                    logger.error("Container unpause failed", docker_id=docker_id, error=str(e))
        logger.info("All workstations resumed from freeze", count=resumed_count)
        return resumed_count



