from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Application settings
    APP_NAME: str = "Distributed URL Shortener"
    APP_ENV: str = "development"
    BASE_URL: str = "http://localhost:8000"
    LOG_LEVEL: str = "INFO"

    # Database settings (Defaults to SQLite for instant local dev/test, seamlessly overrides with Postgres)
    DATABASE_URL: str = "sqlite+aiosqlite:///./urls.db"
    DATABASE_READ_URL: Optional[str] = None
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    # Redis settings
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_NODES: str = "redis://localhost:6379/0"  # Comma-separated for sharded ring
    CACHE_TTL_SECONDS: int = 86400  # 24 hours

    # Rate limiting
    RATE_LIMIT_CAPACITY: int = 10
    RATE_LIMIT_REFILL_RATE: float = 2.0  # tokens per second
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_MAX_REQUESTS: int = 60
    RATE_LIMIT_STRATEGY: str = "token_bucket"  # "token_bucket" or "sliding_window"

    # Distributed ID (Snowflake)
    WORKER_ID: int = Field(default=1, ge=0, le=1023)
    DATACENTER_ID: int = Field(default=1, ge=0, le=31)

    # Fault Tolerance & Circuit Breaker
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 3
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: float = 10.0  # seconds

    # Click Analytics Batch Worker
    CLICK_BATCH_SIZE: int = 50
    CLICK_FLUSH_INTERVAL: float = 1.0  # seconds
    CLICK_QUEUE_KEY: str = "clicks:stream"

    # Observability
    PROMETHEUS_ENABLED: bool = True

    @property
    def redis_node_list(self) -> List[str]:
        return [node.strip() for node in self.REDIS_NODES.split(",") if node.strip()]


settings = Settings()
