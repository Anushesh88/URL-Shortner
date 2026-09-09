import pytest
from httpx import AsyncClient
from app.services.click_processor import click_processor


@pytest.mark.asyncio
async def test_async_click_queue_and_batch_processing(client: AsyncClient):
    # 1. Create URL
    res = await client.post("/shorten", json={"long_url": "https://news.ycombinator.com"})
    code = res.json()["short_code"]

    # 2. Issue 3 redirects with various referrers
    await client.get(f"/{code}", headers={"referer": "https://twitter.com"}, follow_redirects=False)
    await client.get(f"/{code}", headers={"referer": "https://twitter.com"}, follow_redirects=False)
    await client.get(f"/{code}", headers={"referer": "https://google.com"}, follow_redirects=False)

    # 3. Drain batch from queue to DB
    processed = await click_processor.process_batch(batch_size=50)
    assert processed == 3

    # 4. Check Analytics endpoint
    analytics_res = await client.get(f"/analytics/{code}")
    assert analytics_res.status_code == 200
    analytics_data = analytics_res.json()

    assert analytics_data["total_clicks"] == 3
    assert analytics_data["clicks_last_24h"] == 3
    assert analytics_data["top_referrers"].get("https://twitter.com") == 2
    assert analytics_data["top_referrers"].get("https://google.com") == 1
    assert len(analytics_data["recent_clicks"]) == 3
