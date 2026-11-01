from pathlib import Path
from typing import AsyncGenerator

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from neural.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG and settings.APP_ENV == "development",
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    if settings.APP_ENV != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    repository_root = next(
        (parent for parent in Path(__file__).resolve().parents if (parent / "alembic.ini").is_file()),
        None,
    )
    if repository_root is None:
        raise RuntimeError("Alembic configuration is missing from the production image.")
    alembic_config = Config(str(repository_root / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
    async with engine.connect() as conn:
        current_revision = await conn.run_sync(
            lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
        )
    if not expected_revision or current_revision != expected_revision:
        raise RuntimeError(
            "Production database schema is not at the Alembic head "
            f"(current={current_revision!r}, expected={expected_revision!r}). "
            "Review and run the approved migration before starting the API."
        )

