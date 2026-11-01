import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    security,
    skills,
    tasks,
    toolsmith,
)
from neural.config import get_settings
from neural.db.engine import init_db
from neural.logger import configure_logging, get_logger

logger = get_logger("ancos.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    settings = get_settings()
    logger.info("Initializing ANCOS Core Operating System...")
    await init_db()
    os.makedirs(os.path.abspath(settings.EMPLOYEE_WORKSPACE_BASE), exist_ok=True)
    logger.info("Database and workspace directories initialized successfully.")
    yield
    logger.info("Shutting down ANCOS Core Operating System.")



def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="Autonomous Neural Company Operating System Backend API",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(observability.router)
    app.include_router(security.router)
    app.include_router(boss.router)
    app.include_router(projects.router)
    app.include_router(employees.router)
    app.include_router(meetings.router)
    app.include_router(tasks.router)
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

