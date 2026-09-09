import time
import pytest
from httpx import AsyncClient
from app.services.circuit_breaker import CircuitBreaker, CircuitState, redis_circuit_breaker
from app.services.cache import cache_manager


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

    # 2. Intentionally trip the circuit breaker to OPEN
    redis_circuit_breaker.state = CircuitState.OPEN
    redis_circuit_breaker.last_failure_time = time.time()

    # 3. Request redirect: should bypass Redis and redirect from DB without 500 error!
    redirect_res = await client.get(f"/{code}", follow_redirects=False)
    assert redirect_res.status_code == 302
    assert redirect_res.headers["location"] == target_url
    assert redirect_res.headers.get("X-Cache") == "MISS"
