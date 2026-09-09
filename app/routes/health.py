import logging
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.models.database import AsyncSessionLocal
import logging
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.models.database import AsyncSessionLocal
from app.services.sharded_cache import sharded_cache_manager
from app.services.circuit_breaker import redis_circuit_breaker

logger = logging.getLogger("health_router")
router = APIRouter(tags=["Health"])


@router.get("/health", summary="Basic liveness check")
async def health_check():
    shard_circuits = {
        mgr.circuit_breaker.name: mgr.circuit_breaker.state.value
        for mgr in sharded_cache_manager.node_clients.values()
    }
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "worker_id": settings.WORKER_ID,
        "circuit_breakers": shard_circuits or {"default": redis_circuit_breaker.state.value},
    }


@router.get("/health/ready", summary="Comprehensive readiness probe")
async def readiness_probe():
    checks = {
        "database": False,
        "redis_shards": {},
    }

    # 1. Database check
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as e:
        logger.error(f"Health check: Database failure: {e}")

    # 2. Redis shards check
    redis_any_healthy = False
    for url, client_mgr in sharded_cache_manager.node_clients.items():
        try:
            client = await client_mgr.get_client()
            await client.ping()
            checks["redis_shards"][url] = {
                "healthy": True,
                "circuit_breaker": client_mgr.circuit_breaker.state.value,
            }
            redis_any_healthy = True
        except Exception as e:
            logger.warning(f"Health check: Redis shard {url} ping failure: {e}")
            checks["redis_shards"][url] = {
                "healthy": False,
                "circuit_breaker": client_mgr.circuit_breaker.state.value,
                "error": str(e),
            }

    all_ready = checks["database"]  # DB is critical, Redis has circuit breaker fallback
    status_code = status.HTTP_200_OK if all_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ready else "degraded",
            "checks": checks,
            "worker_id": settings.WORKER_ID,
        },
    )
