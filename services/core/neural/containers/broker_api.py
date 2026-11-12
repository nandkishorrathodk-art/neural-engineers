"""Narrow Docker control API. Only this process receives Docker socket access."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import string
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from neural.config import get_settings
from neural.containers.execution_limits import command_with_timeout

try:
    from docker.errors import APIError, ImageNotFound, NotFound

    import docker
except ImportError:  # pragma: no cover - packaging error surfaced as runtime unavailable
    docker = None  # type: ignore[assignment]
    APIError = Exception  # type: ignore[assignment,misc]
    ImageNotFound = Exception  # type: ignore[assignment,misc]
    NotFound = Exception  # type: ignore[assignment,misc]


settings = get_settings()
app = FastAPI(title="ANCOS Restricted Docker Broker", docs_url=None, redoc_url=None, openapi_url=None)
_docker_client: Any = None
_port_lock = threading.Lock()
_provision_lock = threading.Lock()
_token_lock = threading.Lock()

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


class ProvisionRequest(BaseModel):
    employee_id: str = Field(pattern=EMPLOYEE_ID_PATTERN)
    image: str
    cpu_limit: float = Field(default=2.0, gt=0, le=2.0, allow_inf_nan=False)
    mem_limit: Literal["1g", "2g", "4g"] = "4g"


class ExecRequest(BaseModel):
    command: str | list[str]
    workdir: Literal["/home/employee/workspace"] = "/home/employee/workspace"


class VerificationExecRequest(BaseModel):
    employee_id: str = Field(pattern=EMPLOYEE_ID_PATTERN)
    command: list[Annotated[str, Field(min_length=1, max_length=8192)]] = Field(
        min_length=1, max_length=64
    )

    @field_validator("command")
    @classmethod
    def require_test_runner(cls, command: list[str]) -> list[str]:
        is_pytest = command[0] == "pytest" or (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "pytest"]
        )
        is_unittest = (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "unittest"]
        )
        is_npm_test = command[0] == "npm" and (
            command[1:2] == ["test"] or command[1:3] == ["run", "test"]
        )
        if not (is_pytest or is_unittest or is_npm_test):
            raise ValueError("Verifier sandbox accepts only pytest, unittest, or npm test commands.")
        return command


class VerificationManifestRequest(BaseModel):
    employee_id: str = Field(pattern=EMPLOYEE_ID_PATTERN)


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


def employee_network_name(employee_id: str) -> str:
    return f"{settings.EMPLOYEE_NETWORK}-{employee_id.lower()}"


def ensure_network(client: Any, employee_id: str) -> Any:
    network_name = employee_network_name(employee_id)
    try:
        network = client.networks.get(network_name)
    except NotFound:
        try:
            network = client.networks.create(
                network_name,
                driver="bridge",
                internal=True,
                labels={"ancos.managed": "true", "ancos.employee_id": employee_id},
                check_duplicate=True,
            )
        except APIError:
            # Another broker instance may have created this fixed network concurrently.
            network = client.networks.get(network_name)
    if hasattr(network, "reload"):
        network.reload()
    network_attrs = getattr(network, "attrs", {}) or {}
    network_labels = network_attrs.get("Labels") or {}
    if (
        not bool(network_attrs.get("Internal"))
        or network_labels.get("ancos.managed") != "true"
        or network_labels.get("ancos.employee_id") != employee_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Employee network must be Docker-internal; refusing workstation provisioning.",
        )
    try:
        gateway = client.containers.get("ancos-workstation-gateway")
    except NotFound as exc:
        raise HTTPException(status_code=503, detail="Workstation gateway is unavailable.") from exc
    if hasattr(gateway, "reload"):
        gateway.reload()
    gateway_state = ((getattr(gateway, "attrs", {}) or {}).get("State") or {})
    gateway_health = gateway_state.get("Health") or {}
    if gateway_state.get("Status") != "running" or gateway_health.get("Status") != "healthy":
        raise HTTPException(status_code=503, detail="Workstation gateway is not healthy.")
    attached = (getattr(network, "attrs", {}) or {}).get("Containers") or {}
    if gateway.id not in attached:
        network.connect(gateway, aliases=["ancos-egress-gateway"])
    return network


def remove_employee_network(client: Any, employee_id: str) -> None:
    try:
        network = client.networks.get(employee_network_name(employee_id))
        network_attrs = getattr(network, "attrs", {}) or {}
        network_labels = network_attrs.get("Labels") or {}
        if (
            not network_attrs.get("Internal")
            or network_labels.get("ancos.managed") != "true"
            or network_labels.get("ancos.employee_id") != employee_id
        ):
            raise HTTPException(status_code=409, detail="Employee network ownership check failed.")
        gateway = client.containers.get("ancos-workstation-gateway")
        attached = (getattr(network, "attrs", {}) or {}).get("Containers") or {}
        if gateway.id in attached:
            network.disconnect(gateway)
        network.remove()
    except NotFound:
        return


def token_file_path() -> Path:
    return Path(settings.VNC_TOKEN_DIR) / "tokens"


def write_vnc_token(container_name: str) -> str:
    token = secrets.token_urlsafe(32)
    token_path = token_file_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with _token_lock:
        current = token_path.read_text(encoding="utf-8").splitlines() if token_path.exists() else []
        target = f"{container_name}:5901"
        current = [line for line in current if line.rpartition(": ")[2] != target]
        current.append(f"{token}: {target}")
        temporary = token_path.with_name(f"tokens.{uuid.uuid4().hex}.tmp")
        temporary.write_text("\n".join(current) + "\n", encoding="utf-8")
        if token_path.exists():
            try:
                os.chmod(token_path, 0o666)
            except OSError:
                pass
        os.replace(temporary, token_path)
        try:
            os.chmod(token_path, 0o444)
        except OSError:
            pass
    return token


def read_vnc_token(container_name: str) -> str | None:
    token_path = token_file_path()
    if not token_path.exists():
        return None
    target = f"{container_name}:5901"
    for line in token_path.read_text(encoding="utf-8").splitlines():
        token, separator, mapped_target = line.partition(": ")
        if separator and mapped_target == target:
            return token
    return None


def revoke_vnc_token(container_name: str) -> None:
    token_path = token_file_path()
    with _token_lock:
        if not token_path.exists():
            return
        current = token_path.read_text(encoding="utf-8").splitlines()
        target = f"{container_name}:5901"
        remaining = [line for line in current if line.rpartition(": ")[2] != target]
        temporary = token_path.with_name(f"tokens.{uuid.uuid4().hex}.tmp")
        temporary.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
        if token_path.exists():
            try:
                os.chmod(token_path, 0o666)
            except OSError:
                pass
        os.replace(temporary, token_path)
        try:
            os.chmod(token_path, 0o444)
        except OSError:
            pass


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
    result = {
        "docker_id": container.name,
        "employee_id": employee_id,
        "image": container.image.tags[0] if container.image.tags else "unknown",
        "status": container.status,
        "vnc_port": 5901 if container.status == "running" else None,
        "novnc_port": 12003 if container.status == "running" else None,
        "workspace": labels.get("ancos.workspace_volume"),
        "cpu_limit": labels.get("ancos.cpu_limit"),
        "mem_limit": labels.get("ancos.mem_limit"),
        "user": "1000:1000",
        "privileged": False,
        "started_at": container.attrs.get("State", {}).get("StartedAt"),
        "network": next(
            iter((container.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys()),
            None,
        ),
    }
    token = read_vnc_token(container.name)
    if token and container.status == "running":
        result["desktop_url"] = (
            "http://localhost:12003/vnc.html?autoconnect=true&path="
            f"websockify%3Ftoken%3D{token}"
        )
    return result


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
    # Serialize admission and creation so concurrent requests cannot exceed
    # the configured workstation ceiling.
    with _provision_lock:
        return _provision_single_container(payload)


def _provision_single_container(payload: ProvisionRequest) -> dict[str, Any]:
    if payload.image not in ALLOWED_IMAGES:
        raise HTTPException(status_code=400, detail="Requested employee image is not allowlisted.")

    client = docker_client()
    existing_workstations = client.containers.list(
        all=True,
        filters={"label": "ancos.managed=true"},
    )
    try:
        # Import lazily because resource_scheduler exports DesktopSlotPool,
        # which depends on ContainerManager.
        from neural.resource_scheduler.workstation_lease import workstation_slot_limit

        workstation_limit = workstation_slot_limit()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Desktop capacity setting is invalid.") from exc
    existing_workstations = [
        container for container in existing_workstations
        if container.name != employee_container_id(payload.employee_id)
    ]
    if len(existing_workstations) >= workstation_limit:
        raise HTTPException(
            status_code=409,
            detail=f"Desktop capacity is full ({len(existing_workstations)}/{workstation_limit}).",
        )

    name = employee_container_id(payload.employee_id)
    try:
        existing = client.containers.get(name)
    except NotFound:
        existing = None
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Workstation {name} already exists; refusing replacement.")

    # Self-heal a missing allowlisted image: role images are aliases of the pinned
    # base workstation image, so build the base and tag the alias on demand instead
    # of failing workstation provisioning on a fresh deployment.
    ensure_employee_image(client, payload.image)

    novnc_port = 12003
    volume_name = ensure_employee_volume(client, payload.employee_id)
    network = ensure_network(client, payload.employee_id)
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
            volumes={volume_name: {"bind": "/home/employee/workspace", "mode": "rw"}},
            environment={
                "DISPLAY": ":1",
                "VNC_PORT": "5901",
                "NOVNC_PORT": "6081",
                "EMPLOYEE_ID": payload.employee_id,
                "VNC_PASSWORD": vnc_password,
                "HTTP_PROXY": "http://ancos-egress-gateway:3128",
                "HTTPS_PROXY": "http://ancos-egress-gateway:3128",
                "http_proxy": "http://ancos-egress-gateway:3128",
                "https_proxy": "http://ancos-egress-gateway:3128",
                "NO_PROXY": "localhost,127.0.0.1,::1,ancos-core-api,ancos-egress-gateway",
                "no_proxy": "localhost,127.0.0.1,::1,ancos-core-api,ancos-egress-gateway",
            },
            nano_cpus=int(payload.cpu_limit * 1_000_000_000),
            mem_limit=payload.mem_limit,
            memswap_limit=payload.mem_limit,
            pids_limit=200,
            privileged=False,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            network=network.name,
        )
        token = write_vnc_token(name)
        result = serialize_container(container, payload.employee_id)
        result["novnc_port"] = novnc_port
        result["desktop_url"] = (
            "http://localhost:12003/vnc.html?autoconnect=true&path="
            f"websockify%3Ftoken%3D{token}"
        )
        result["vnc_password"] = vnc_password
        return result
    except Exception as exc:
        revoke_vnc_token(name)
        try:
            container = client.containers.get(name)
            container.remove(force=True)
        except Exception:
            pass
        try:
            remove_employee_network(client, payload.employee_id)
        except Exception:
            pass
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
    revoke_vnc_token(container_id)
    employee_id = (container.labels or {}).get("ancos.employee_id")
    if employee_id:
        remove_employee_network(docker_client(), employee_id)
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
        cmd=command_with_timeout(payload.command),
        user="1000:1000",
        workdir=payload.workdir,
    )
    output = result.output.decode("utf-8", errors="replace") if result.output else ""
    return {"exit_code": result.exit_code, "stdout": output, "stderr": ""}


@app.post("/v1/verifier/exec")
def execute_verifier_test(payload: VerificationExecRequest) -> dict[str, Any]:
    """Run a test command in a disposable, offline container with a read-only employee workspace."""
    verifier_container = create_verification_sandbox(payload.employee_id)
    try:
        result = verifier_container.exec_run(
            cmd=["/usr/bin/timeout", "300s", *payload.command],
            user="1000:1000",
            workdir="/home/employee/workspace",
            environment={
                "HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "TMPDIR": "/tmp",
            },
        )
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        return {"exit_code": result.exit_code, "stdout": output, "stderr": ""}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Isolated verifier execution failed.") from exc
    finally:
        remove_verification_sandbox(verifier_container)


@app.post("/v1/verifier/manifest")
def capture_verification_manifest(payload: VerificationManifestRequest) -> dict[str, Any]:
    """Hash workspace files without exposing file contents or allowing workspace writes."""
    verifier_container = create_verification_sandbox(payload.employee_id)
    manifest_script = (
        "import hashlib,json,os,stat,time\n"
        "root='/home/employee/workspace'\n"
        "skip={'.git','.venv','node_modules','__pycache__','.pytest_cache'}\n"
        "files=[]; errors=[]; total=0; max_files=2000; max_file=10485760; max_total=104857600\n"
        "for current,dirs,names in os.walk(root,followlinks=False):\n"
        " if errors: break\n"
        " dirs[:]=sorted(d for d in dirs if d not in skip and not os.path.islink(os.path.join(current,d)))\n"
        " for name in sorted(names):\n"
        "  path=os.path.join(current,name)\n"
        "  try:\n"
        "   info=os.stat(path,follow_symlinks=False)\n"
        "   if not stat.S_ISREG(info.st_mode): continue\n"
        "   rel=os.path.relpath(path,root).replace(os.sep,'/')\n"
        "   if len(rel)>2048: errors.append('path length limit exceeded'); dirs[:]=[]; break\n"
        "   if len(files)>=max_files or info.st_size>max_file or total+info.st_size>max_total:\n"
        "    errors.append('manifest size limit exceeded'); dirs[:]=[]; break\n"
        "   digest=hashlib.sha256()\n"
        "   fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))\n"
        "   with os.fdopen(fd,'rb') as stream:\n"
        "    before=os.fstat(stream.fileno()); read_size=0\n"
        "    if not stat.S_ISREG(before.st_mode): raise OSError('not regular')\n"
        "    for chunk in iter(lambda:stream.read(1048576),b''):\n"
        "     read_size+=len(chunk)\n"
        "     if read_size>max_file: raise OSError('file grew beyond limit')\n"
        "     digest.update(chunk)\n"
        "    after=os.fstat(stream.fileno())\n"
        "    if read_size!=before.st_size or after.st_size!=before.st_size or after.st_mtime_ns!=before.st_mtime_ns: raise OSError('file changed while hashing')\n"
        "   if read_size!=info.st_size: raise OSError('file changed before hashing')\n"
        "   files.append({'path':rel,'size_bytes':info.st_size,'sha256':digest.hexdigest()})\n"
        "   total+=info.st_size\n"
        "  except OSError: errors.append('unreadable file'); dirs[:]=[]; break\n"
        "manifest={'captured_at_unix':time.time(),'files':files,'file_count':len(files),"
        "'total_size_bytes':total,'complete':not errors,'errors':errors,"
        "'scope':'verification-time workspace snapshot; no task-start diff'}\n"
        "print(json.dumps(manifest,separators=(',',':')))\n"
    )
    try:
        result = verifier_container.exec_run(
            cmd=["/usr/bin/timeout", "300s", "python3", "-I", "-B", "-c", manifest_script],
            user="1000:1000",
            workdir="/home/employee/workspace",
            demux=True,
            environment={
                "HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": "/tmp",
            },
        )
        if isinstance(result.output, tuple):
            stdout_bytes, _stderr_bytes = result.output
        else:
            stdout_bytes, _stderr_bytes = result.output, None
        output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        if result.exit_code != 0:
            raise HTTPException(status_code=503, detail="Workspace manifest command failed.")
        import json

        manifest = json.loads(output)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise HTTPException(status_code=503, detail="Workspace manifest output is invalid.")
        return manifest
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail="Workspace manifest capture failed.") from exc
    finally:
        remove_verification_sandbox(verifier_container)


def create_verification_sandbox(employee_id: str) -> Any:
    client = docker_client()
    workstation = get_managed_container(employee_container_id(employee_id))
    labels = workstation.labels or {}
    workspace = workspace_volume_name(employee_id)
    if labels.get("ancos.employee_id") != employee_id or labels.get("ancos.workspace_volume") != workspace:
        raise HTTPException(status_code=409, detail="Employee workspace ownership could not be verified.")
    try:
        volume = client.volumes.get(workspace)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Employee workspace volume not found.") from exc
    volume_labels = volume.attrs.get("Labels") or {}
    if volume_labels.get("ancos.managed") != "true" or volume_labels.get("ancos.employee_id") != employee_id:
        raise HTTPException(status_code=409, detail="Employee workspace volume ownership check failed.")
    image_tags = workstation.image.tags or []
    image = next((tag for tag in image_tags if tag in ALLOWED_IMAGES), None)
    if not image:
        raise HTTPException(status_code=409, detail="Employee workstation image is not allowlisted.")
    workspace = workspace_volume_name(employee_id)
    verifier_name = f"ancos-verifier-{employee_id.lower()}-{uuid.uuid4().hex[:12]}"
    try:
        return client.containers.run(
            image=image,
            entrypoint=["/bin/sleep"],
            command=["3600"],
            name=verifier_name,
            detach=True,
            labels={
                "ancos.managed": "true",
                "ancos.verifier": "true",
                "ancos.employee_id": employee_id,
                "ancos.workspace_volume": workspace,
            },
            user="1000:1000",
            volumes={workspace: {"bind": "/home/employee/workspace", "mode": "ro"}},
            network_disabled=True,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            nano_cpus=1_000_000_000,
            mem_limit="1g",
            memswap_limit="1g",
            pids_limit=128,
            privileged=False,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not provision isolated verifier sandbox.") from exc


def remove_verification_sandbox(verifier_container: Any) -> None:
    try:
        verifier_container.remove(force=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Verifier sandbox cleanup failed.") from exc


@app.get("/v1/containers/{container_id}/vnc-token")
@app.post("/v1/containers/{container_id}/vnc-token")
def issue_or_get_vnc_token(container_id: str) -> dict[str, Any]:
    validate_container_id(container_id)
    container = get_managed_container(container_id)
    token = read_vnc_token(container.name)
    if not token:
        token = write_vnc_token(container.name)
    novnc_port = 12003
    return {
        "docker_id": container_id,
        "token": token,
        "ws_path": f"websockify?token={token}",
        "desktop_url": f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&path=websockify%3Ftoken%3D{token}",
        "read_only_desktop_url": f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&view_only=true&path=websockify%3Ftoken%3D{token}",
    }


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
    ensure_employee_image(docker_client(), payload.tag)
    return {"tag": payload.tag, "built": True}


BUILD_SOURCE_DIR = Path("/opt/ancos-docker")
BASE_IMAGE = "ancos/employee-base:latest"


def _image_exists(client: Any, tag: str) -> bool:
    try:
        client.images.get(tag)
        return True
    except ImageNotFound:
        return False


def _build_base_employee_image(client: Any) -> None:
    source = BUILD_SOURCE_DIR
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
                tag=BASE_IMAGE,
                rm=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Fixed employee image build failed: {exc}") from exc


def ensure_employee_image(client: Any, tag: str) -> None:
    """Guarantee an allowlisted employee image exists.

    Role images (swe/frontend/qa/devops) are content-identical aliases of the
    pinned base workstation image, so a fresh deployment only needs to build the
    base once and tag the role aliases from it. Without this, provisioning a role
    workstation fails on any deployment where the aliases were never built.
    """
    if tag not in ALLOWED_IMAGES:
        raise HTTPException(status_code=400, detail="Requested employee image is not allowlisted.")
    if _image_exists(client, tag):
        return
    if not _image_exists(client, BASE_IMAGE):
        _build_base_employee_image(client)
    if tag != BASE_IMAGE:
        try:
            client.images.get(BASE_IMAGE).tag(tag)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Employee image alias tagging failed: {exc}") from exc


def get_managed_container(container_id: str) -> Any:
    try:
        container = docker_client().containers.get(container_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Workstation not found.") from exc
    if (container.labels or {}).get("ancos.managed") != "true":
        raise HTTPException(status_code=404, detail="Managed workstation not found.")
    return container
