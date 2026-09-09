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


def test_is_trusted_proxy():
    from app.middleware.rate_limiter import is_trusted_proxy

    # Loopback and private subnets in default settings
    assert is_trusted_proxy("127.0.0.1") is True
    assert is_trusted_proxy("::1") is True
    assert is_trusted_proxy("10.0.1.5") is True
    assert is_trusted_proxy("172.20.0.4") is True
    assert is_trusted_proxy("192.168.1.100") is True

    # Untrusted public IPs
    assert is_trusted_proxy("203.0.113.195") is False
    assert is_trusted_proxy("8.8.8.8") is False
    assert is_trusted_proxy("") is False
    assert is_trusted_proxy("invalid-ip") is False


def test_extract_client_ip_trusted_proxy():
    from unittest.mock import MagicMock
    from app.middleware.rate_limiter import extract_client_ip

    # Case 1: Trusted proxy forwards X-Real-IP
    req_trusted = MagicMock()
    req_trusted.client.host = "127.0.0.1"
    req_trusted.headers = {
        "X-Real-IP": "203.0.113.50",
        "X-Forwarded-For": "203.0.113.50, 10.0.0.1",
    }
    assert extract_client_ip(req_trusted) == "203.0.113.50"

    # Case 2: Trusted proxy forwards only X-Forwarded-For
    req_trusted_xff = MagicMock()
    req_trusted_xff.client.host = "172.18.0.2"
    req_trusted_xff.headers = {
        "X-Forwarded-For": "198.51.100.22, 172.18.0.2",
    }
    assert extract_client_ip(req_trusted_xff) == "198.51.100.22"


def test_extract_client_ip_untrusted_peer_rejects_spoofed_headers():
    from unittest.mock import MagicMock
    from app.middleware.rate_limiter import extract_client_ip

    # Untrusted external client attempts to spoof X-Forwarded-For and X-Real-IP
    req_untrusted = MagicMock()
    req_untrusted.client.host = "203.0.113.195"
    req_untrusted.headers = {
        "X-Real-IP": "1.1.1.1",
        "X-Forwarded-For": "8.8.8.8, 9.9.9.9",
    }
    # Headers must be ignored; real peer IP is returned
    assert extract_client_ip(req_untrusted) == "203.0.113.195"


@pytest.mark.asyncio
async def test_spoofed_x_forwarded_for_from_untrusted_peer_cannot_bypass_rate_limits(monkeypatch):
    """Simulates direct connections from an untrusted peer rotating X-Forwarded-For.

    Verifies that the rate limiter binds to the untrusted peer IP, throttling them after 5 requests.
    """
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    untrusted_peer = "198.51.100.77"

    # Custom transport specifying the untrusted client host
    transport = ASGITransport(app=app, client=(untrusted_peer, 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as untrusted_client:
        # Client sends 5 requests, attempting to evade limits by rotating spoofed X-Forwarded-For
        for i in range(5):
            res = await untrusted_client.post(
                "/shorten",
                json={"long_url": f"https://example.com/spoof-{i}"},
                headers={"X-Forwarded-For": f"10.99.{i}.1"},
            )
            assert res.status_code == 201

        # 6th request with yet another spoofed IP should still be throttled (429)
        throttled_res = await untrusted_client.post(
            "/shorten",
            json={"long_url": "https://example.com/spoof-blocked"},
            headers={"X-Forwarded-For": "10.99.99.99"},
        )
        assert throttled_res.status_code == 429
        assert "Rate limit exceeded" in throttled_res.json()["detail"]
