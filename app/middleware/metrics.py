import time
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Prometheus Metrics Definitions
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests processed",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

cache_operations_total = Counter(
    "cache_operations_total",
    "Total cache operations by type and result",
    ["operation", "status"],
)

rate_limit_exceeded_counter = Counter(
    "rate_limit_exceeded_total",
    "Total requests throttled with 429 Too Many Requests",
    ["client_id"],
)

circuit_breaker_state_gauge = Gauge(
    "circuit_breaker_state",
    "Current state of the Redis circuit breaker (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
    ["name"],
)

click_queue_length_gauge = Gauge(
    "click_queue_length",
    "Number of click tracking events pending in the Redis queue",
    ["queue"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking request duration and count metrics for Prometheus."""

    async def dispatch(self, request: Request, call_next) -> Response:
        endpoint = request.url.path
        method = request.method

        # Normalize common dynamic URL paths to keep metric cardinality low
        if len(endpoint.strip("/").split("/")) == 1 and endpoint not in [
            "/docs", "/redoc", "/openapi.json", "/health", "/metrics", "/shorten"
        ]:
            normalized_endpoint = "/{code}"
        elif endpoint.startswith("/analytics/"):
            normalized_endpoint = "/analytics/{code}"
        else:
            normalized_endpoint = endpoint

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        http_requests_total.labels(
            method=method,
            endpoint=normalized_endpoint,
            status_code=response.status_code,
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=normalized_endpoint,
        ).observe(duration)

        return response


def get_prometheus_metrics_response() -> Response:
    """Generates Prometheus scrapable metrics payload."""
    from app.services.sharded_cache import sharded_cache_manager
    from app.services.circuit_breaker import redis_circuit_breaker

    if sharded_cache_manager.node_clients:
        for url, client_mgr in sharded_cache_manager.node_clients.items():
            circuit_breaker_state_gauge.labels(name=client_mgr.circuit_breaker.name).set(
                client_mgr.circuit_breaker.metric_value
            )
    else:
        circuit_breaker_state_gauge.labels(name="redis_circuit").set(redis_circuit_breaker.metric_value)

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
