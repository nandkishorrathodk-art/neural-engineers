import os

# Pin the environment before importing any neural module. `neural.api.main`
# builds a module-level app from settings at import time, and a developer .env
# prepared for a live run sets APP_ENV=production, which would otherwise make
# that shared app enforce production auth during the suite.
os.environ["APP_ENV"] = "development"

import pytest
import pytest_asyncio
from neural.db.engine import Base
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def disable_cloud_provider_credentials_in_tests(monkeypatch):
    """Never let developer .env credentials trigger billable calls from tests."""
    for name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("BOSS_LLM_PLANNING_ENABLED", "false")
    # A developer .env prepared for a live run sets APP_ENV=production, which
    # trips production-only guards inside unit tests. Pin the environment so the
    # suite is hermetic; tests that need production opt in explicitly.
    monkeypatch.setenv("APP_ENV", "development")
    from neural.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with async_session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

