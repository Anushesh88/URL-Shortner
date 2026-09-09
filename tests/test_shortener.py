import asyncio
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_shorten_and_redirect_happy_path(client: AsyncClient):
    # 1. Shorten URL
    target_url = "https://example.com/system-design/url-shortener"
    response = await client.post("/shorten", json={"long_url": target_url})
    assert response.status_code == 201
    data = response.json()
    assert "short_code" in data
    assert data["long_url"] == target_url
    assert data["short_url"].endswith(data["short_code"])

    short_code = data["short_code"]

    # 2. Redirect
    redirect_res = await client.get(f"/{short_code}", follow_redirects=False)
    assert redirect_res.status_code == 302
    assert redirect_res.headers["location"] == target_url


@pytest.mark.asyncio
async def test_shorten_with_custom_code(client: AsyncClient):
    custom_alias = "my-custom-slug"
    target_url = "https://fastapi.tiangolo.com/"

    res = await client.post(
        "/shorten",
        json={"long_url": target_url, "custom_code": custom_alias},
    )
    assert res.status_code == 201
    assert res.json()["short_code"] == custom_alias

    # Duplicate alias conflict
    dup_res = await client.post(
        "/shorten",
        json={"long_url": "https://other.com", "custom_code": custom_alias},
    )
    assert dup_res.status_code == 409
    assert "already taken" in dup_res.json()["detail"]


@pytest.mark.asyncio
async def test_shorten_invalid_url(client: AsyncClient):
    # Missing scheme
    res = await client.post("/shorten", json={"long_url": "ftp://example.com/file"})
    assert res.status_code == 422

    res2 = await client.post("/shorten", json={"long_url": "not-a-valid-url"})
    assert res2.status_code == 422


@pytest.mark.asyncio
async def test_redirect_non_existent_code(client: AsyncClient):
    res = await client.get("/nonexistent123", follow_redirects=False)
    assert res.status_code == 404
    assert res.json()["detail"] == "Short URL not found."


@pytest.mark.asyncio
async def test_url_expiration(client: AsyncClient):
    target_url = "https://example.com/expires-soon"
    # Expires in 1 second
    res = await client.post(
        "/shorten",
        json={"long_url": target_url, "expires_in_seconds": 1},
    )
    assert res.status_code == 201
    code = res.json()["short_code"]

    # First request: should succeed
    res_valid = await client.get(f"/{code}", follow_redirects=False)
    assert res_valid.status_code == 302

    # Wait for expiration
    await asyncio.sleep(1.2)

    # Subsequent request: should return 410 Gone
    res_expired = await client.get(f"/{code}", follow_redirects=False)
    assert res_expired.status_code == 410
    assert "expired" in res_expired.json()["detail"]


@pytest.mark.asyncio
async def test_delete_url(client: AsyncClient):
    target_url = "https://example.com/to-be-deleted"
    res = await client.post("/shorten", json={"long_url": target_url})
    code = res.json()["short_code"]

    # Delete
    del_res = await client.delete(f"/{code}")
    assert del_res.status_code == 200

    # Ensure 404 after deletion
    get_res = await client.get(f"/{code}", follow_redirects=False)
    assert get_res.status_code == 404
