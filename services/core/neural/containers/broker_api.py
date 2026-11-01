"""Narrow Docker control API. Only this process receives Docker socket access."""

from __future__ import annotations

import secrets
import re
import shutil
import string
import threading
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from neural.config import get_settings

try:
    import docker
    from docker.errors import APIError, ImageNotFound, NotFound
except ImportError:  # pragma: no cover - packaging error surfaced as runtime unavailable
    docker = None  # type: ignore[assignment]
    APIError = Exception  # type: ignore[assignment,misc]
    ImageNotFound = Exception  # type: ignore[assignment,misc]
    NotFound = Exception  # type: ignore[assignment,misc]


settings = get_settings()
app = FastAPI(title="ANCOS Restricted Docker Broker", docs_url=None, redoc_url=None, openapi_url=None)
_docker_client: Any = None
_port_lock = threading.Lock()

EMPLOYEE_ID_PATTERN = r"^ANC-EMP-[0-9A-Za-z_-]{1,48}$"
ALLOWED_IMAGES = {
    "ancos/employee-base:latest",
    "ancos/employee-swe:latest",
    "ancos/employee-frontend:latest",
    "ancos/employee-qa:latest",
    "ancos/employee-devops:latest",
}
WORKSPACE_VOLUME_PREFIX = "ancos-workspace-"
CONTAINER_PREFIX = "ancos-desktop-"
NETWORK_NAME = "ancos-bridge-net"


class ProvisionRequest(BaseModel):
    employee_id: str = Field(pattern=EMPLOYEE_ID_PATTERN)
    image: str
    cpu_limit: float = Field(default=2.0, gt=0, le=2.0, allow_inf_nan=False)
    mem_limit: Literal["1g", "2g", "4g"] = "4g"


class ExecRequest(BaseModel):
    command: str | list[str]
    workdir: Literal["/home/employee/workspace"] = "/home/employee/workspace"


class ImageBuildRequest(BaseModel):
    tag: Literal[
        "ancos/employee-base:latest",
        "ancos/employee-swe:latest",
        "ancos/employee-frontend:latest",
        "ancos/employee-qa:latest",
        "ancos/employee-devops:latest",
    ]


def docker_client() -> Any:
    global _docker_client
    if docker is None:
        raise HTTPException(status_code=503, detail="Docker SDK is unavailable in broker runtime.")
    try:
        if _docker_client is None:
            _docker_client = docker.from_env()
        _docker_client.ping()
        return _docker_client
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Docker runtime is unavailable.") from exc


def employee_container_id(employee_id: str) -> str:
    return f"{CONTAINER_PREFIX}{employee_id.lower()}"


def workspace_volume_name(employee_id: str) -> str:
    return f"{WORKSPACE_VOLUME_PREFIX}{employee_id.lower()}"


def generate_vnc_password() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))


def validate_container_id(container_id: str) -> None:
    if not re.fullmatch(r"ancos-desktop-anc-emp-[0-9a-z_-]+", container_id, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Only ANCOS employee workstations are addressable.")


def ensure_network(client: Any) -> None:
    try:
        client.networks.get(NETWORK_NAME)
    except NotFound:
        try:
            client.networks.create(NETWORK_NAME, driver="bridge", check_duplicate=True)
        except APIError:
            # Another broker instance may have created this fixed network concurrently.
            client.networks.get(NETWORK_NAME)


def list_used_ports(client: Any) -> set[int]:
    used: set[int] = set()
    containers = client.containers.list(all=True)
    for container in containers:
        container.reload()
        port_map = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
        for bindings in port_map.values():
            for binding in bindings or []:
                try:
                    used.add(int(binding["HostPort"]))
                except (KeyError, TypeError, ValueError):
                    continue
    return used


def allocate_ports(client: Any) -> tuple[int, int]:
    with _port_lock:
        used = list_used_ports(client)
        for offset in range(100):
            vnc, novnc = 5901 + offset, 6081 + offset
            if vnc not in used and novnc not in used:
                return vnc, novnc
    raise HTTPException(status_code=503, detail="No workstation VNC ports are available.")


def ensure_employee_volume(client: Any, employee_id: str) -> str:
    name = workspace_volume_name(employee_id)
    labels = {"ancos.managed": "true", "ancos.employee_id": employee_id}
    try:
        volume = client.volumes.get(name)
    except NotFound:
        volume = client.volumes.create(name=name, driver="local", labels=labels)
    actual_labels = volume.attrs.get("Labels") or {}
    if actual_labels.get("ancos.managed") != "true" or actual_labels.get("ancos.employee_id") != employee_id:
        raise HTTPException(status_code=409, detail="Workspace volume ownership check failed.")
    return name


def serialize_container(container: Any, employee_id: str | None = None) -> dict[str, Any]:
    container.reload()
    labels = container.labels or {}
    employee_id = employee_id or labels.get("ancos.employee_id")
    bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}

    def host_port(target: str) -> int | None:
        values = bindings.get(target) or []
        if not values:
            return None
        try:
            return int(values[0]["HostPort"])
        except (KeyError, TypeError, ValueError):
            return None

    return {
        "docker_id": container.name,
        "employee_id": employee_id,
        "image": container.image.tags[0] if container.image.tags else "unknown",
        "status": container.status,
        "vnc_port": host_port("5901/tcp"),
        "novnc_port": host_port("6081/tcp"),
        "workspace": labels.get("ancos.workspace_volume"),
        "cpu_limit": labels.get("ancos.cpu_limit"),
        "mem_limit": labels.get("ancos.mem_limit"),
        "user": "1000:1000",
        "privileged": False,
        "started_at": container.attrs.get("State", {}).get("StartedAt"),
    }


@app.middleware("http")
async def require_broker_token(request: Request, call_next):
    token = settings.DOCKER_BROKER_TOKEN
    if not token or len(token) < 32:
        return JSONResponse(status_code=503, content={"detail": "Docker broker token is not configured safely."})
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, token):
        return JSONResponse(
            status_code=401,
            content={"detail": "Valid broker bearer token required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    if not settings.DOCKER_BROKER_TOKEN or len(settings.DOCKER_BROKER_TOKEN) < 32:
        raise HTTPException(status_code=503, detail="Docker broker token is not configured safely.")
    docker_client()
    return {"status": "ready"}


@app.post("/v1/containers")
def provision_container(payload: ProvisionRequest) -> dict[str, Any]:
    if payload.image not in ALLOWED_IMAGES:
        raise HTTPException(status_code=400, detail="Requested employee image is not allowlisted.")

    client = docker_client()
    name = employee_container_id(payload.employee_id)
    try:
        existing = client.containers.get(name)
    except NotFound:
        existing = None
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Workstation {name} already exists; refusing replacement.")

    try:
        client.images.get(payload.image)
    except ImageNotFound as exc:
        raise HTTPException(status_code=409, detail=f"Allowlisted image {payload.image} has not been built.") from exc

    vnc_port, novnc_port = allocate_ports(client)
    volume_name = ensure_employee_volume(client, payload.employee_id)
    ensure_network(client)
    labels = {
        "ancos.managed": "true",
        "ancos.employee_id": payload.employee_id,
        "ancos.workspace_volume": volume_name,
        "ancos.cpu_limit": str(payload.cpu_limit),
        "ancos.mem_limit": payload.mem_limit,
    }
    vnc_password = generate_vnc_password()
    try:
        container = client.containers.run(
            image=payload.image,
            name=name,
            detach=True,
            labels=labels,
            user="1000:1000",
            ports={
                "5901/tcp": ("127.0.0.1", vnc_port),
                "6081/tcp": ("127.0.0.1", novnc_port),
            },
            volumes={volume_name: {"bind": "/home/employee/workspace", "mode": "rw"}},
            environment={
                "DISPLAY": ":1",
                "VNC_PORT": "5901",
                "NOVNC_PORT": "6081",
                "EMPLOYEE_ID": payload.employee_id,
                "VNC_PASSWORD": vnc_password,
            },
            nano_cpus=int(payload.cpu_limit * 1_000_000_000),
            mem_limit=payload.mem_limit,
            memswap_limit=payload.mem_limit,
            pids_limit=200,
            privileged=False,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            network=NETWORK_NAME,
        )
        result = serialize_container(container, payload.employee_id)
        result["vnc_password"] = vnc_password
        return result
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Docker refused workstation creation: {exc}") from exc


@app.get("/v1/containers/{container_id}")
def inspect_container(container_id: str) -> dict[str, Any]:
    validate_container_id(container_id)
    try:
        container = docker_client().containers.get(container_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Workstation not found.") from exc
    if (container.labels or {}).get("ancos.managed") != "true":
        raise HTTPException(status_code=404, detail="Managed workstation not found.")
    return serialize_container(container)


@app.post("/v1/containers/{container_id}/restart")
def restart_container(container_id: str) -> dict[str, Any]:
    validate_container_id(container_id)
    container = get_managed_container(container_id)
    container.restart(timeout=5)
    return serialize_container(container)


@app.post("/v1/containers/{container_id}/terminate")
def terminate_container(container_id: str) -> dict[str, str]:
    validate_container_id(container_id)
    container = get_managed_container(container_id)
    if container.status == "running":
        container.stop(timeout=5)
    container.remove()
    return {"docker_id": container_id, "status": "terminated"}


@app.post("/v1/containers/{container_id}/pause")
def pause_container(container_id: str) -> dict[str, str]:
    validate_container_id(container_id)
    container = get_managed_container(container_id)
    if container.status == "running":
        container.pause()
    return {"docker_id": container_id, "status": "paused"}


@app.post("/v1/containers/{container_id}/resume")
def resume_container(container_id: str) -> dict[str, str]:
    validate_container_id(container_id)
    container = get_managed_container(container_id)
    if container.status == "paused":
        container.unpause()
    return {"docker_id": container_id, "status": "running"}


@app.post("/v1/containers/{container_id}/exec")
def execute_in_container(container_id: str, payload: ExecRequest) -> dict[str, Any]:
    validate_container_id(container_id)
    if isinstance(payload.command, str) and len(payload.command) > 32768:
        raise HTTPException(status_code=413, detail="Command exceeds the maximum allowed length.")
    if isinstance(payload.command, list) and (not payload.command or len(payload.command) > 256):
        raise HTTPException(status_code=400, detail="Command argument list is invalid.")
    container = get_managed_container(container_id)
    result = container.exec_run(
        cmd=payload.command,
        user="1000:1000",
        workdir=payload.workdir,
    )
    output = result.output.decode("utf-8", errors="replace") if result.output else ""
    return {"exit_code": result.exit_code, "stdout": output, "stderr": ""}


@app.post("/v1/containers/{container_id}/vnc-password")
def rotate_vnc_password(container_id: str) -> dict[str, str]:
    validate_container_id(container_id)
    password = generate_vnc_password()
    container = get_managed_container(container_id)
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
        raise HTTPException(status_code=503, detail="VNC credential rotation failed.")
    return {"docker_id": container_id, "vnc_password": password}


@app.post("/v1/workstations/freeze")
def freeze_workstations() -> dict[str, int]:
    client = docker_client()
    count = 0
    for container in client.containers.list(filters={"label": "ancos.managed=true", "status": "running"}):
        container.pause()
        count += 1
    return {"frozen": count}


@app.post("/v1/workstations/resume")
def resume_workstations() -> dict[str, int]:
    client = docker_client()
    count = 0
    for container in client.containers.list(filters={"label": "ancos.managed=true", "status": "paused"}):
        container.unpause()
        count += 1
    return {"resumed": count}


@app.post("/v1/images/build")
def build_employee_image(payload: ImageBuildRequest) -> dict[str, Any]:
    client = docker_client()
    source = Path("/opt/ancos-docker")
    dockerfile = source / "Dockerfile.employee-base"
    if not dockerfile.is_file():
        raise HTTPException(status_code=503, detail="Fixed employee base Dockerfile is unavailable.")
    with tempfile.TemporaryDirectory(prefix="ancos-build-") as temp_dir:
        context = Path(temp_dir)
        docker_dir = context / "docker"
        docker_dir.mkdir()
        shutil.copytree(source / "configs", docker_dir / "configs")
        shutil.copy2(source / "entrypoint-employee.sh", docker_dir / "entrypoint-employee.sh")
        shutil.copy2(dockerfile, docker_dir / "Dockerfile.employee-base")
        try:
            client.images.build(
                path=str(context),
                dockerfile="docker/Dockerfile.employee-base",
                tag=payload.tag,
                rm=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Fixed employee image build failed: {exc}") from exc
    return {"tag": payload.tag, "built": True}


def get_managed_container(container_id: str) -> Any:
    try:
        container = docker_client().containers.get(container_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Workstation not found.") from exc
    if (container.labels or {}).get("ancos.managed") != "true":
        raise HTTPException(status_code=404, detail="Managed workstation not found.")
    return container
