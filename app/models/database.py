from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

Base = declarative_base()

# Connection arguments
is_sqlite = settings.DATABASE_URL.startswith("sqlite")
engine_kwargs = {}
if not is_sqlite:
    engine_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_pre_ping": True,
    })

# Primary (Write) Database Engine
primary_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    **engine_kwargs
)

# Replica (Read) Database Engine - conceptual read replica
replica_url = settings.DATABASE_READ_URL or settings.DATABASE_URL
replica_kwargs = {}
if not replica_url.startswith("sqlite"):
    replica_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_pre_ping": True,
    })

replica_engine = create_async_engine(
    replica_url,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    **replica_kwargs
)

# Session factories
AsyncSessionLocal = async_sessionmaker(
    bind=primary_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

AsyncReadSessionLocal = async_sessionmaker(
    bind=replica_engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a write-capable DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a read-replica DB session."""
    async with AsyncReadSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Creates database tables if they do not already exist."""
    async with primary_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
