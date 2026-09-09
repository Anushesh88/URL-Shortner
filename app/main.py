import asyncio
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models.database import init_db
from app.services.cache import cache_manager
from app.services.sharded_cache import sharded_cache_manager
from app.services.click_processor import click_processor
from app.routes.shortener import router as shortener_router
from app.routes.analytics import router as analytics_router
from app.routes.health import router as health_router
from app.middleware.logging_context import RequestLoggingMiddleware
from app.middleware.metrics import MetricsMiddleware, get_prometheus_metrics_response
from app.middleware.rate_limiter import RateLimitMiddleware

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("main")


async def background_click_drainer():
    """Periodic in-process worker to flush click events when running as a single container/process."""
    while True:
        try:
            await asyncio.sleep(settings.CLICK_FLUSH_INTERVAL)
            await click_processor.process_batch(batch_size=settings.CLICK_BATCH_SIZE)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Click drainer error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting {settings.APP_NAME} (Worker ID: {settings.WORKER_ID})...")
    await init_db()

    # Launch in-process click flusher task
    drain_task = asyncio.create_task(background_click_drainer())

    yield

    # Shutdown
    logger.info("Shutting down application...")
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass
    await sharded_cache_manager.close()
    await cache_manager.close()


app = FastAPI(
    title="Distributed URL Shortener & Rate Limiter",
    description=(
        "Production-grade distributed URL shortener engineered for massive horizontal scale. "
        "Features Base62 + Twitter Snowflake IDs, Redis cache-aside, atomic Lua token-bucket rate limiting, "
        "consistent hash sharding, and asynchronous click event stream processing."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Application Middlewares (Executed in reverse order of addition)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# API Routers
app.include_router(health_router)
app.include_router(analytics_router)
app.include_router(shortener_router)


# Prometheus metrics endpoint
@app.get("/metrics", include_in_schema=False)
async def metrics():
    return get_prometheus_metrics_response()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
