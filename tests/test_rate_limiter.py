import asyncio
import pytest
from httpx import AsyncClient
from app.config import settings


@pytest.mark.asyncio
async def test_token_bucket_rate_limiting_burst_and_rejection(client: AsyncClient):
    # Capacity is set to 5 in test environment
    target_url = "https://example.com/ratelimit"
    headers = {"X-API-Key": "test-client-burst"}

    # First 5 requests should succeed
    for i in range(5):
        res = await client.post("/shorten", json={"long_url": target_url}, headers=headers)
        assert res.status_code == 201, f"Request {i+1} failed with status {res.status_code}"
        assert res.headers.get("X-RateLimit-Limit") == "5"

    # 6th request immediately afterwards should be throttled (429)
    throttled_res = await client.post("/shorten", json={"long_url": target_url}, headers=headers)
    assert throttled_res.status_code == 429
    assert "Retry-After" in throttled_res.headers
    assert throttled_res.headers.get("X-RateLimit-Remaining") == "0"
    data = throttled_res.json()
    assert "Rate limit exceeded" in data["detail"]


@pytest.mark.asyncio
async def test_rate_limit_token_refill(client: AsyncClient):
    # Refill rate is 1 token per second in test environment
    headers = {"X-API-Key": "test-client-refill"}

    # Exhaust tokens (5 requests)
    for _ in range(5):
        await client.post("/shorten", json={"long_url": "https://example.com"}, headers=headers)

    # Confirm 429
    res_429 = await client.post("/shorten", json={"long_url": "https://example.com"}, headers=headers)
    assert res_429.status_code == 429

    # Wait 1.1 seconds for 1 token to refill
    await asyncio.sleep(1.1)

    # Next request should now succeed
    res_refilled = await client.post("/shorten", json={"long_url": "https://example.com"}, headers=headers)
    assert res_refilled.status_code == 201


@pytest.mark.asyncio
async def test_exempt_endpoints_not_rate_limited(client: AsyncClient):
    headers = {"X-API-Key": "test-client-exempt"}

    # Hammer /health 20 times (way above capacity of 5)
    for _ in range(20):
        res = await client.get("/health", headers=headers)
        assert res.status_code == 200
