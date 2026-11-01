from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application Info
    APP_NAME: str = "ANCOS - Autonomous Neural Company Operating System"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 12000
    HOST: str = "127.0.0.1"

    # Security
    SECRET_KEY: Optional[str] = None
    OWNER_API_TOKEN: Optional[str] = None
    WORKER_API_TOKEN: Optional[str] = None
    VERIFIER_API_TOKEN: Optional[str] = None
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:12001",
        "http://127.0.0.1:12001",
    ]
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    EMERGENCY_STOP_ENABLED: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/neural_company.db"

    # Redis & Message Bus
    REDIS_URL: str = "redis://localhost:6379/0"

    # Vector Storage
    CHROMA_PERSIST_DIRECTORY: str = "./data/chroma"

    # Docker Infrastructure
    DOCKER_HOST: str = "unix:///var/run/docker.sock"
    DOCKER_BROKER_URL: Optional[str] = None
    DOCKER_BROKER_TOKEN: Optional[str] = None
    EMPLOYEE_WORKSPACE_BASE: str = "./data/workspaces"
    EMPLOYEE_NETWORK: str = "ancos-bridge-net"
    VNC_PORT_START: int = 5901
    NOVNC_PORT_START: int = 6081

    # AI Model Providers
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # Model Defaults
    BOSS_MODEL: str = "gpt-4o"
    BOSS_TEMPERATURE: float = 0.2
    EMPLOYEE_DEFAULT_MODEL: str = "gpt-4o-mini"
    CODE_AGENT_MODEL: str = "claude-3-5-sonnet-20241022"

    # Quotas & Limits
    MAX_CONCURRENT_EMPLOYEES: int = 10
    DEFAULT_CONTAINER_CPU_LIMIT: float = 2.0
    DEFAULT_CONTAINER_MEM_LIMIT: str = "4g"
    DEFAULT_TASK_TIMEOUT_SECONDS: int = 7200

    # Human Approval Gates
    REQUIRE_HUMAN_APPROVAL_DELETIONS: bool = True
    REQUIRE_HUMAN_APPROVAL_PAYMENTS: bool = True
    REQUIRE_HUMAN_APPROVAL_PRODUCTION: bool = True
    REQUIRE_HUMAN_APPROVAL_MIGRATIONS: bool = True
    REQUIRE_HUMAN_APPROVAL_CREDENTIALS: bool = True

    # Zero-Trust & Sandbox Hardening
    DEFAULT_WORKSPACE_DISK_QUOTA_MB: int = 250
    SECURITY_PERIMETER_BLOCK_METADATA: bool = True
    ENFORCE_SYSCALL_FILTERING: bool = True

    # Phase 8 — Laya Neural Decision Intelligence
    LAYA_MODEL_DIR: str = "./data/models/laya"
    LAYA_DEFAULT_CHECKPOINT: str = "Laya-Typed-Decisions"
    LAYA_SHADOW_MODE_DEFAULT: bool = True
    LAYA_BATCH_WINDOW_MS: int = 10
    LAYA_MAX_BATCH_SIZE: int = 32
    LAYA_CONFIDENCE_THRESHOLD: float = 0.85
    LAYA_ECE_MAX_THRESHOLD: float = 0.08
    LAYA_MAX_LATENCY_MS: float = 40.0



    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()

