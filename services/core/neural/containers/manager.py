import os
from datetime import datetime, timezone
from typing import Any, Optional

from neural.config import get_settings
from neural.constants import ContainerStatus
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


class ContainerManager:
    """
    Manages isolated Docker workstations for AI employees.
    Provisions Xfce4 Linux desktops with TigerVNC, noVNC proxy, and developer toolchains.
    Enforces non-root execution, resource bounds, and sandbox isolation.
    """

    def __init__(self, docker_client: Optional[Any] = None):
        self.settings = get_settings()
        self._docker_client = docker_client
        self._docker_initialized = False
        # Track allocated ports: identifier -> (vnc_port, novnc_port)
        self._allocated_ports: dict[str, tuple[int, int]] = {}
        # Track in-memory active containers
        self._containers: dict[str, dict[str, Any]] = {}
        from neural.containers.broker import RestrictedDockerBroker
        self.broker = RestrictedDockerBroker(workspace_base=self.settings.EMPLOYEE_WORKSPACE_BASE)
        try:
            from neural.security.kill_switch import get_kill_switch
            get_kill_switch().register_container_manager(self)
        except Exception:
            pass


    def _get_client(self) -> Optional[Any]:
        if self._docker_client is not None:
            return self._docker_client
        if not self._docker_initialized:
            self._docker_initialized = True
            if docker is not None:
                try:
                    self._docker_client = docker.from_env()
                except Exception as ex:
                    logger.warning(
                        "Docker daemon connection unavailable; container operations are disabled",
                        error=str(ex)
                    )
                    self._docker_client = None
        return self._docker_client

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

        client = self._get_client()
        if client is None:
            raise ContainerError("Docker runtime is unavailable; workstation was not provisioned.")

        docker_id = f"ancos-desktop-{employee_id.lower()}"
        used_ports = set()
        if container_repo:
            try:
                all_c = await container_repo.list_all()
                used_ports = {c.vnc_port for c in all_c if c.status == ContainerStatus.RUNNING}
            except Exception:
                pass

        vnc_port, novnc_port = self.allocate_ports(docker_id, used_vnc_ports=used_ports)
        workspace_path = self.ensure_workspace_dir(employee_id)

        effective_cpu = cpu_limit or self.settings.DEFAULT_CONTAINER_CPU_LIMIT
        effective_mem = mem_limit or self.settings.DEFAULT_CONTAINER_MEM_LIMIT
        if role_image.startswith("ancos/"):
            image_name = role_image if ":" in role_image else f"{role_image}:latest"
        else:
            image_name = f"ancos/{role_image}:latest" if ":" not in role_image else f"ancos/{role_image}"
        dockerfile_path = os.path.abspath("docker/Dockerfile.employee-base")

        container_spec: dict[str, Any] = {
            "docker_id": docker_id,
            "employee_id": employee_id,
            "image": image_name,
            "dockerfile_path": dockerfile_path,
            "vnc_port": vnc_port,
            "novnc_port": novnc_port,
            "desktop_url": f"http://localhost:{novnc_port}/vnc.html?autoconnect=true",
            "cpu_limit": effective_cpu,
            "mem_limit": effective_mem,
            "pids_limit": 200,
            "user": "1000:1000",
            "privileged": False,
            "workspace": workspace_path,
            "status": ContainerStatus.RUNNING.value,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "network": self.settings.EMPLOYEE_NETWORK
        }

        # Create a real workstation; metadata is recorded only after Docker confirms it.
        try:
            # Remove an existing container with this approved employee name if stale.
            try:
                existing = client.containers.get(docker_id)
                existing.remove(force=True)
            except NotFound:
                pass

            nano_cpus = int(effective_cpu * 1e9)
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
                    "EMPLOYEE_ID": employee_id
                },
                "nano_cpus": nano_cpus,
                "mem_limit": effective_mem,
                "pids_limit": 200,
                "privileged": False,
                "security_opt": ["no-new-privileges:true"]
            }
            hardened_kwargs = self.broker.harden_container_kwargs(run_kwargs)
            client.containers.run(**hardened_kwargs)
            logger.info("Docker container started successfully", docker_id=docker_id)
        except Exception as e:
            logger.warning("Docker workstation provisioning failed", docker_id=docker_id, error=str(e))
            raise ContainerError(f"Workstation {docker_id} was not provisioned: {e}") from e

        self._containers[docker_id] = container_spec

        # Optional database persistence
        if container_repo:
            try:
                db_container = await container_repo.create(
                    docker_id=docker_id,
                    image=image_name,
                    status=ContainerStatus.RUNNING,
                    vnc_port=vnc_port,
                    novnc_port=novnc_port,
                    resource_limits={"cpu_limit": effective_cpu, "mem_limit": effective_mem, "pids_limit": 200},
                    network_config={"network": self.settings.EMPLOYEE_NETWORK},
                    workspace_path=workspace_path
                )
                container_spec["db_id"] = db_container.id
                if employee_repo:
                    await employee_repo.assign_container(employee_id, db_container.id)
            except Exception as db_err:
                logger.warning("Failed to persist container record in DB", error=str(db_err))

        logger.info(
            "Desktop workstation ready",
            employee_id=employee_id,
            desktop_url=container_spec["desktop_url"],
            vnc_port=vnc_port,
            novnc_port=novnc_port
        )
        return container_spec

    async def terminate_desktop(self, docker_id: str, container_repo: Optional[Any] = None) -> None:
        """Stops and removes container, releases ports and updates status."""
        logger.info("Terminating desktop container", docker_id=docker_id)
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            raise ContainerError("Docker runtime is unavailable; workstation termination was not confirmed.")
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
            return spec

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
                    return recovered_spec
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
                return spec

        docker_id = f"ancos-desktop-{employee_id.lower()}"
        if docker_id in self._containers:
            return self._containers[docker_id]

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
                        return recovered_spec
            except Exception as e:
                logger.debug("Failed to recover desktop spec from DB", error=str(e))

        return None

    async def list_active_desktops(self) -> list[dict[str, Any]]:
        """Lists all active desktop workstation specifications."""
        return list(self._containers.values())

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
            raise ContainerError("Docker runtime is unavailable; workstation restart was not performed.")
        try:
            container = client.containers.get(docker_id)
            container.restart(timeout=5)
            logger.info("Docker container restarted", docker_id=docker_id)
        except Exception as e:
            logger.warning("Docker workstation restart failed", docker_id=docker_id, error=str(e))
            raise ContainerError(f"Workstation {docker_id} was not restarted: {e}") from e

        spec["status"] = ContainerStatus.RUNNING.value
        spec["started_at"] = datetime.now(timezone.utc).isoformat()

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
        logger.info("Executing command in container", docker_id=docker_id, command=command)
        self.broker.validate_container_id(docker_id)
        client = self._get_client()
        if client is None:
            raise ContainerError(
                "Docker runtime is unavailable; command was not executed."
            )

        try:
            container = client.containers.get(docker_id)
            exec_workdir = workdir or "/home/employee/workspace"
            exec_res = container.exec_run(
                cmd=command,
                user="1000:1000",
                workdir=exec_workdir
            )
            stdout = exec_res.output.decode("utf-8", errors="replace") if exec_res.output else ""
            return {
                "exit_code": exec_res.exit_code,
                "stdout": stdout,
                "stderr": ""
            }
        except Exception as e:
            logger.warning("Docker exec failed", docker_id=docker_id, error=str(e))
            raise ContainerError(
                f"Command was not executed in container {docker_id}: {e}"
            ) from e

    async def build_employee_image(
        self,
        tag: str = "ancos/employee-base:latest",
        dockerfile: str = "docker/Dockerfile.employee-base"
    ) -> bool:
        """
        Builds the employee base Docker workstation image using Dockerfile.employee-base.
        """
        abs_dockerfile = os.path.abspath(dockerfile)
        if not os.path.exists(abs_dockerfile):
            logger.error("Employee Dockerfile not found", path=abs_dockerfile)
            return False

        client = self._get_client()
        if client is None:
            logger.warning("Docker daemon unavailable; employee image was not built", tag=tag)
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
            return {
                "docker_id": docker_id,
                "status": "runtime_unavailable",
                "runtime_available": False,
                "healthy": False,
            }
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
            logger.error("Cannot freeze workstations because Docker runtime is unavailable")
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
            logger.error("Cannot resume workstations because Docker runtime is unavailable")
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



