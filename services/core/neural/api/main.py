import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neural.api.routes import (
    boss,
    comms,
    decision_intelligence,
    employees,
    evolution,
    governance,
    gui_intelligence,
    health,
    llm,
    meetings,
    meta_learning,
    methods,
    observability,
    projects,
    resource_leases,
    security,
    skills,
    tasks,
    toolsmith,
    verifier,
    worker,
)
from neural.config import get_settings
from neural.db.engine import init_db
from neural.exceptions import ContainerError
from neural.logger import configure_logging, get_logger

logger = get_logger("ancos.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    settings = get_settings()
    logger.info("Initializing ANCOS Core Operating System...")
    message_bus = None
    try:
        await init_db()
        if settings.APP_ENV == "production":
            from neural.api.deps import get_message_bus

            message_bus = get_message_bus()
            await message_bus.connect()
        if settings.DOCKER_BROKER_URL:
            from neural.api.deps import get_container_manager

            await get_container_manager()._broker_request("GET", "/health")
        elif settings.APP_ENV == "production":
            raise RuntimeError("Production startup requires the restricted Docker broker.")
        os.makedirs(os.path.abspath(settings.EMPLOYEE_WORKSPACE_BASE), exist_ok=True)
        if message_bus is not None:
            await message_bus.start_outbox_dispatcher()
        logger.info("Database and workspace directories initialized successfully.")
        yield
    finally:
        if message_bus is not None:
            await message_bus.close()
    logger.info("Shutting down ANCOS Core Operating System.")



def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="Autonomous Neural Company Operating System Backend API",
        lifespan=lifespan,
        docs_url=None if settings.APP_ENV == "production" else "/docs",
        redoc_url=None if settings.APP_ENV == "production" else "/redoc",
        openapi_url=None if settings.APP_ENV == "production" else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ContainerError)
    async def container_operation_error(request: Request, exc: ContainerError):
        status_code = getattr(exc, "status_code", 503)
        if not isinstance(status_code, int) or not 400 <= status_code <= 599:
            status_code = 503
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    @app.middleware("http")
    async def require_owner_token(request, call_next):
        # Development remains convenient for the loopback-only local dashboard.
        # Deployed production APIs require an independently provisioned owner token.
        if (
            settings.APP_ENV == "production"
            and request.method != "OPTIONS"
            and request.url.path not in {"/health/live", "/health/ready"}
            and not request.url.path.startswith(
                ("/internal/worker/", "/internal/verifier/", "/internal/resources/leases/")
            )
        ):
            configured_token = settings.OWNER_API_TOKEN
            if not configured_token or len(configured_token) < 32:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Production owner API token must be configured with at least 32 characters."},
                )
            authorization = request.headers.get("authorization", "")
            scheme, _, supplied_token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not supplied_token or not secrets.compare_digest(
                supplied_token, configured_token
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Valid bearer token required."},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(observability.router)
    app.include_router(security.router)
    app.include_router(boss.router)
    app.include_router(projects.router)
    app.include_router(employees.router)
    app.include_router(meetings.router)
    app.include_router(tasks.router)
    app.include_router(worker.router)
    app.include_router(resource_leases.router)
    app.include_router(verifier.router)
    app.include_router(llm.router)
    app.include_router(comms.router)
    app.include_router(governance.router)
    app.include_router(skills.router)
    app.include_router(evolution.router)
    app.include_router(toolsmith.router)
    app.include_router(methods.router)
    app.include_router(meta_learning.router)
    app.include_router(gui_intelligence.router)
    app.include_router(decision_intelligence.router)

    return app






app = create_app()

