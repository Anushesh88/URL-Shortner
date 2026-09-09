import logging
import os
import time
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.services.cache import cache_manager

logger = logging.getLogger("rate_limiter")

# Load Lua script definitions
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Root project directory is parent of 'app'
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

TOKEN_BUCKET_LUA_PATH = os.path.join(SCRIPTS_DIR, "rate_limiter_token_bucket.lua")
SLIDING_WINDOW_LUA_PATH = os.path.join(SCRIPTS_DIR, "rate_limiter_sliding_window.lua")

with open(TOKEN_BUCKET_LUA_PATH, "r", encoding="utf-8") as f:
    TOKEN_BUCKET_LUA = f.read()

with open(SLIDING_WINDOW_LUA_PATH, "r", encoding="utf-8") as f:
    SLIDING_WINDOW_LUA = f.read()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI Rate Limiting Middleware implementing Token Bucket & Sliding Window.
    
    Operates atomically in Redis via Lua scripts to eliminate race conditions
    under heavy horizontal concurrency.
    """

    def __init__(self, app, exempt_paths: Optional[set] = None):
        super().__init__(app)
        self.exempt_paths = exempt_paths or {
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
            "/health/ready",
            "/health/live",
            "/metrics",
            "/favicon.ico",
        }

    def _get_client_identifier(self, request: Request) -> str:
        """Derives a stable client key from API Key header or forwarded IP."""
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"apikey:{api_key.strip()}"

        # Check proxy forwarding headers (e.g. Nginx)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take first IP in chain
            client_ip = forwarded_for.split(",")[0].strip()
            return f"ip:{client_ip}"

        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"

    async def _check_token_bucket(self, client_id: str) -> tuple[bool, int, float]:
        """Runs the atomic token bucket Lua script in Redis."""
        key = f"rate_limit:tb:{client_id}"
        capacity = settings.RATE_LIMIT_CAPACITY
        refill_rate = settings.RATE_LIMIT_REFILL_RATE
        now = time.time()

        redis = await cache_manager.get_client()
        result = await redis.eval(
            TOKEN_BUCKET_LUA,
            1,
            key,
            capacity,
            refill_rate,
            now
        )
        allowed = bool(result[0] == 1)
        remaining = int(result[1])
        retry_after = round(1.0 / refill_rate, 2) if not allowed else 0.0
        return allowed, remaining, retry_after

    async def _check_sliding_window(self, client_id: str) -> tuple[bool, int, float]:
        """Runs the atomic sliding window counter Lua script in Redis."""
        key = f"rate_limit:sw:{client_id}"
        now_ms = int(time.time() * 1000)
        window_ms = settings.RATE_LIMIT_WINDOW_SECONDS * 1000
        max_requests = settings.RATE_LIMIT_MAX_REQUESTS

        redis = await cache_manager.get_client()
        result = await redis.eval(
            SLIDING_WINDOW_LUA,
            1,
            key,
            now_ms,
            window_ms,
            max_requests
        )
        allowed = bool(result[0] == 1)
        remaining = max(0, int(result[1]))
        retry_after = float(settings.RATE_LIMIT_WINDOW_SECONDS) if not allowed else 0.0
        return allowed, remaining, retry_after

    async def dispatch(self, request: Request, call_next) -> Response:
        # Check if route is exempt from rate limiting
        path = request.url.path
        if path in self.exempt_paths or path.startswith("/metrics"):
            return await call_next(request)

        client_id = self._get_client_identifier(request)

        try:
            if settings.RATE_LIMIT_STRATEGY == "sliding_window":
                allowed, remaining, retry_after = await self._check_sliding_window(client_id)
                limit = settings.RATE_LIMIT_MAX_REQUESTS
            else:
                allowed, remaining, retry_after = await self._check_token_bucket(client_id)
                limit = settings.RATE_LIMIT_CAPACITY

            if not allowed:
                # Track metric if Prometheus is enabled
                from app.middleware.metrics import rate_limit_exceeded_counter
                rate_limit_exceeded_counter.labels(client_id=client_id).inc()

                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too Many Requests. Rate limit exceeded.",
                        "client_id": client_id,
                        "retry_after_seconds": retry_after,
                    },
                    headers={
                        "Retry-After": str(int(retry_after) or 1),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    }
                )

            # Request is allowed, invoke downstream handler
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        except Exception as e:
            # Resilient fail-open: Do not block traffic if rate limiter backing fails
            logger.error(f"Error during rate limit evaluation: {e}. Allowing request (fail-open).")
            return await call_next(request)
