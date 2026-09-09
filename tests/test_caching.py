import pytest
from httpx import AsyncClient
from app.services.cache import cache_manager


@pytest.mark.asyncio
async def test_cache_aside_hit_and_miss(client: AsyncClient):
    target_url = "https://example.com/caching-test"
    res = await client.post("/shorten", json={"long_url": target_url})
    code = res.json()["short_code"]

    # Post populates cache -> First GET should be a HIT
    get_res1 = await client.get(f"/{code}", follow_redirects=False)
    assert get_res1.status_code == 302
    assert get_res1.headers.get("X-Cache") == "HIT"

    # Invalidate cache manually
    await cache_manager.delete_url(code)

    # Next GET should be a MISS, and re-populate the cache
    get_res2 = await client.get(f"/{code}", follow_redirects=False)
    assert get_res2.status_code == 302
    assert get_res2.headers.get("X-Cache") == "MISS"

    # Subsequent GET should be a HIT again
    get_res3 = await client.get(f"/{code}", follow_redirects=False)
    assert get_res3.status_code == 302
    assert get_res3.headers.get("X-Cache") == "HIT"


@pytest.mark.asyncio
async def test_cache_invalidation_on_delete(client: AsyncClient):
    res = await client.post("/shorten", json={"long_url": "https://example.com/cache-delete"})
    code = res.json()["short_code"]

    # Verify key in cache
    cached = await cache_manager.get_url(code)
    assert cached is not None
    assert cached["short_code"] == code

    # Delete URL
    del_res = await client.delete(f"/{code}")
    assert del_res.status_code == 200

    # Ensure key is removed from Redis
    cached_after = await cache_manager.get_url(code)
    assert cached_after is None
