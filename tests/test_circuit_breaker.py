import time
import pytest
from httpx import AsyncClient
from app.config import settings
from app.services.circuit_breaker import CircuitBreaker, CircuitState, redis_circuit_breaker
from app.services.cache import CacheManager
from app.services.sharded_cache import sharded_cache_manager


def test_circuit_breaker_state_transitions():
    cb = CircuitBreaker(name="test_cb", failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1. First 2 failures: still CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 2. 3rd failure: Trips to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # 3. Wait for recovery timeout (1.0s)
    time.sleep(1.1)

    # 4. First check after timeout should transition to HALF_OPEN
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # 5. Successful probe resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_graceful_db_fallback_when_circuit_breaker_open(client: AsyncClient):
    # 1. Create a URL in DB
    target_url = "https://example.com/fallback-test"
    res = await client.post("/shorten", json={"long_url": target_url})
    code = res.json()["short_code"]

    # 2. Intentionally trip all circuit breakers to OPEN
    for client_mgr in sharded_cache_manager.node_clients.values():
        client_mgr.circuit_breaker.state = CircuitState.OPEN
        client_mgr.circuit_breaker.last_failure_time = time.time()
    redis_circuit_breaker.state = CircuitState.OPEN
    redis_circuit_breaker.last_failure_time = time.time()

    # 3. Request redirect: should bypass Redis and redirect from DB without 500 error!
    redirect_res = await client.get(f"/{code}", follow_redirects=False)
    assert redirect_res.status_code == 302
    assert redirect_res.headers["location"] == target_url
    assert redirect_res.headers.get("X-Cache") == "MISS"


@pytest.mark.asyncio
async def test_production_env_refuses_fakeredis_and_trips_circuit_breaker():
    """Verifies that in production, Redis connection failure raises instead of silently using FakeRedis,
    allowing the circuit breaker to properly trip."""
    original_env = settings.APP_ENV
    original_allow = settings.ALLOW_FAKE_REDIS
    try:
        settings.APP_ENV = "production"
        settings.ALLOW_FAKE_REDIS = False

        test_cb = CircuitBreaker(name="prod_unreachable", failure_threshold=2, recovery_timeout=5.0)
        # Point to unreachable port
        failing_cache = CacheManager(redis_url="redis://127.0.0.1:59999/0", circuit_breaker=test_cb)

        # 1. get_client must raise rather than silently swapping FakeRedis
        with pytest.raises(Exception):
            await failing_cache.get_client()

        # 2. get_url catches the connection failure and records it on the circuit breaker
        assert test_cb.state == CircuitState.CLOSED
        val1 = await failing_cache.get_url("test_key_1")
        assert val1 is None
        assert test_cb.failure_count == 1
        assert test_cb.state == CircuitState.CLOSED

        # 3. Second failure reaches threshold (2) -> Trips to OPEN!
        val2 = await failing_cache.get_url("test_key_2")
        assert val2 is None
        assert test_cb.state == CircuitState.OPEN
        assert test_cb.can_execute() is False

    finally:
        settings.APP_ENV = original_env
        settings.ALLOW_FAKE_REDIS = original_allow
