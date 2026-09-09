import asyncio
import os
import random
import statistics
import sys
import time
from typing import List, Tuple

# Ensure app package is discoverable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.database import init_db


async def run_benchmark(
    total_requests: int = 1000,
    concurrency: int = 20,
    cache_warmup_count: int = 20,
):
    """Executes a self-contained async benchmark against the in-process FastAPI application."""
    print("=" * 70)
    print("      DISTRIBUTED URL SHORTENER & RATE LIMITER BENCHMARK SUITE       ")
    print("=" * 70)
    print(f"Configuration: {total_requests} requests | {concurrency} concurrent workers")

    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
        # 1. Warm-up: Create sample URLs
        print(f"\n[1/3] Warming up cache with {cache_warmup_count} URLs...")
        seeded_codes = []
        for i in range(cache_warmup_count):
            res = await client.post(
                "/shorten",
                json={"long_url": f"https://system-design-primer.com/article/{i}"},
                headers={"X-API-Key": "benchmark-seeder"},
            )
            if res.status_code == 201:
                seeded_codes.append(res.json()["short_code"])

        print(f"      Seeded {len(seeded_codes)} short URLs successfully.")

        # 2. Concurrently execute traffic
        print(f"\n[2/3] Simulating mixed traffic (80% Redirects, 15% Creates, 5% Bursts)...")
        queue: asyncio.Queue[Tuple[str, dict]] = asyncio.Queue()

        # Build workload
        for i in range(total_requests):
            roll = random.random()
            if roll < 0.80 and seeded_codes:
                # 80% Read traffic (Redirect)
                code = random.choice(seeded_codes)
                queue.put_nowait(("GET", {"url": f"/{code}"}))
            elif roll < 0.95:
                # 15% Write traffic (Create)
                queue.put_nowait((
                    "POST",
                    {
                        "url": "/shorten",
                        "json": {"long_url": f"https://example.com/item/{i}"},
                        "headers": {"X-API-Key": f"user-{i % 50}"},
                    },
                ))
            else:
                # 5% Burst traffic on single key (Tests Rate Limiter)
                queue.put_nowait((
                    "POST",
                    {
                        "url": "/shorten",
                        "json": {"long_url": "https://example.com/burst"},
                        "headers": {"X-API-Key": "burst-abuser"},
                    },
                ))

        latencies_ms: List[float] = []
        status_counts = {}
        cache_hits = 0
        cache_misses = 0

        async def worker():
            nonlocal cache_hits, cache_misses
            while not queue.empty():
                try:
                    method, kwargs = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                start = time.perf_counter()
                if method == "GET":
                    resp = await client.get(kwargs["url"], follow_redirects=False)
                else:
                    resp = await client.post(
                        kwargs["url"],
                        json=kwargs.get("json"),
                        headers=kwargs.get("headers"),
                    )
                duration_ms = (time.perf_counter() - start) * 1000

                latencies_ms.append(duration_ms)
                status = resp.status_code
                status_counts[status] = status_counts.get(status, 0) + 1

                cache_hdr = resp.headers.get("X-Cache")
                if cache_hdr == "HIT":
                    cache_hits += 1
                elif cache_hdr == "MISS":
                    cache_misses += 1

                queue.task_done()

        wall_clock_start = time.perf_counter()
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        total_time_seconds = time.perf_counter() - wall_clock_start

        # 3. Analyze Results
        print("\n[3/3] Compiling benchmark metrics...\n")
        latencies_ms.sort()

        p50 = statistics.median(latencies_ms)
        p90 = latencies_ms[int(len(latencies_ms) * 0.90)]
        p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
        p99 = latencies_ms[int(len(latencies_ms) * 0.99)]
        avg_latency = statistics.mean(latencies_ms)
        rps = len(latencies_ms) / total_time_seconds

        total_cache_queries = cache_hits + cache_misses
        hit_ratio = (cache_hits / total_cache_queries * 100) if total_cache_queries else 0.0

        print("-" * 70)
        print("                  PERFORMANCE BENCHMARK RESULTS                      ")
        print("-" * 70)
        print(f"Total Requests Processed : {len(latencies_ms):,}")
        print(f"Total Wall-Clock Time    : {total_time_seconds:.2f} s")
        print(f"Sustained Throughput     : {rps:,.1f} requests/sec")
        print(f"Average Latency          : {avg_latency:.2f} ms")
        print(f"P50 Latency (Median)     : {p50:.2f} ms")
        print(f"P90 Latency              : {p90:.2f} ms")
        print(f"P95 Latency              : {p95:.2f} ms")
        print(f"P99 Latency              : {p99:.2f} ms")
        print(f"Cache Hit Ratio          : {hit_ratio:.1f}% ({cache_hits} hits / {total_cache_queries} reads)")
        print("\nHTTP Status Code Breakdown:")
        for code, count in sorted(status_counts.items()):
            pct = (count / len(latencies_ms)) * 100
            label = "OK/Created" if code in (200, 201) else "Redirect" if code == 302 else "Rate Limited" if code == 429 else "Other"
            print(f"  HTTP {code} ({label:12s}): {count:6d} ({pct:5.1f}%)")
        print("-" * 70)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
