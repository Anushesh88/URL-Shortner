import asyncio
import os
import pytest
import pytest_asyncio
import fakeredis.aioredis as fake_aioredis
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment
os.environ["APP_ENV"] = "testing"
os.environ["RATE_LIMIT_CAPACITY"] = "5"
os.environ["RATE_LIMIT_REFILL_RATE"] = "1.0"

from app.models.database import Base, get_db, get_read_db
import app.models  # Ensures all models (URL, ClickAnalytics) are registered on Base.metadata
from app.services.cache import cache_manager
from app.services.circuit_breaker import redis_circuit_breaker, CircuitState
import app.services.click_processor as cp_module
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
cp_module.AsyncSessionLocal = TestSessionLocal


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provides a clean in-memory database session for each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function", autouse=True)
async def reset_cache_and_circuit():
    """Ensures a clean FakeRedis instance and resets circuit breaker state before every test."""
    fake_redis = fake_aioredis.FakeRedis(decode_responses=True)
    cache_manager._client = fake_redis
    cache_manager._is_fake = True
    redis_circuit_breaker.state = CircuitState.CLOSED
    redis_circuit_breaker.failure_count = 0
    yield fake_redis
    await fake_redis.flushall()


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    """Provides an async HTTP test client with database dependency overrides."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
