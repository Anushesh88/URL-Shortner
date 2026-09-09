import json
import logging
import sys
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.rate_limiter import extract_client_ip

# Configure JSON structured logger
logger = logging.getLogger("api_access")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for distributed tracing with X-Request-ID and structured JSON logging."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Correlation ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.perf_counter()
        client_ip = extract_client_ip(request)

        response: Response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        cache_status = response.headers.get("X-Cache", "NONE")

        # Set response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        # Emit structured log
        log_entry = {
            "timestamp": time.time(),
            "request_id": request_id,
            "client_ip": client_ip,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "cache": cache_status,
        }
        logger.info(json.dumps(log_entry))

        return response
