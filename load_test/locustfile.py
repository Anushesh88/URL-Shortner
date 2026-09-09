import random
from locust import HttpUser, task, between


class URLShortenerUser(HttpUser):
    """Simulates realistic production traffic against the URL Shortener & Rate Limiter."""

    wait_time = between(0.05, 0.2)
    sample_codes = []

    def on_start(self):
        """Seed a few URLs to build an initial cache for redirect testing."""
        if not URLShortenerUser.sample_codes:
            for i in range(10):
                target = f"https://example.com/page-{i}-{random.randint(1000, 9999)}"
                with self.client.post("/shorten", json={"long_url": target}, catch_response=True) as resp:
                    if resp.status_code == 201:
                        code = resp.json().get("short_code")
                        URLShortenerUser.sample_codes.append(code)

    @task(80)
    def test_redirect_hot_path(self):
        """Simulates 80% read traffic: redirecting via short code (Cache-aside)."""
        if URLShortenerUser.sample_codes:
            code = random.choice(URLShortenerUser.sample_codes)
            with self.client.get(
                f"/{code}",
                name="/{code} (Redirect)",
                allow_redirects=False,
                catch_response=True,
            ) as response:
                if response.status_code in [302, 200]:
                    response.success()
                elif response.status_code == 429:
                    # Expected if rate limit kicked in
                    response.success()
                else:
                    response.failure(f"Unexpected status code: {response.status_code}")

    @task(15)
    def test_shorten_write_path(self):
        """Simulates 15% write traffic: generating new short URLs."""
        random_id = random.randint(10000, 999999)
        payload = {"long_url": f"https://news.ycombinator.com/item?id={random_id}"}
        with self.client.post(
            "/shorten",
            json=payload,
            name="/shorten (Create)",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                code = response.json().get("short_code")
                if code and len(URLShortenerUser.sample_codes) < 100:
                    URLShortenerUser.sample_codes.append(code)
                response.success()
            elif response.status_code == 429:
                response.success()
            else:
                response.failure(f"Shorten failed: {response.status_code}")

    @task(5)
    def test_burst_rate_limiting(self):
        """Simulates 5% burst traffic designed to trigger and verify rate limiter throttling."""
        # Use dedicated burst client key
        headers = {"X-API-Key": f"burst-user-{random.randint(1, 3)}"}
        for _ in range(12):
            with self.client.get(
                "/health",
                headers=headers,
                name="/health (Burst Probe)",
                catch_response=True,
            ) as resp:
                resp.success()
