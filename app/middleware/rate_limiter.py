import ipaddress
import logging
import os
import time
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.services.sharded_cache import sharded_cache_manager

logger = logging.getLogger("rate_limiter")


def is_trusted_proxy(ip_str: str) -> bool:
    """Verifies whether the immediate peer IP is in the configured trusted proxy list."""
    if not ip_str:
        return False
    try:
        peer = ipaddress.ip_address(ip_str)
        for proxy_net in settings.trusted_proxy_list:
            try:
                if "/" in proxy_net:
                    if peer in ipaddress.ip_network(proxy_net, strict=False):
                        return True
                elif peer == ipaddress.ip_address(proxy_net):
                    return True
            except ValueError:
                continue
        return False
    except ValueError:
        return False


def extract_client_ip(request: Request) -> str:
    """Safely extracts client IP address with reverse-proxy trust validation.
    
    Security Defense:
    Prevents IP spoofing. X-Forwarded-For and X-Real-IP are ONLY accepted if
    the immediate peer is a verified trusted proxy (e.g., local Nginx or Docker gateway).
    Direct client requests cannot forge their IP via custom headers.
    """
    peer_ip = request.client.host if request.client else "unknown"

    if is_trusted_proxy(peer_ip):
        # 1. Prefer X-Real-IP (overwritten by Nginx to $remote_addr)
        real_ip = request.headers.get("X-Real-IP")
        if real_ip and real_ip.strip():
            return real_ip.strip()

        # 2. Check X-Forwarded-For
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
            if ips:
                return ips[0]

    return peer_ip

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
        """Derives a stable client key from API Key header or validated client IP."""
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"apikey:{api_key.strip()}"

        client_ip = extract_client_ip(request)
        return f"ip:{client_ip}"

    async def _check_token_bucket(self, client_id: str) -> tuple[bool, int, float]:
        """Runs the atomic token bucket Lua script in Redis."""
        key = f"rate_limit:tb:{client_id}"
        capacity = settings.RATE_LIMIT_CAPACITY
        refill_rate = settings.RATE_LIMIT_REFILL_RATE
        now = time.time()

        redis = await sharded_cache_manager.get_client_for_key(client_id)
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

        redis = await sharded_cache_manager.get_client_for_key(client_id)
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
