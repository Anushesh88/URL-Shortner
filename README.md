# Distributed URL Shortener & High-Concurrency Rate Limiter

[![CI Pipeline](https://github.com/Anushesh88/URL-Shortner/actions/workflows/ci.yml/badge.svg)](https://github.com/Anushesh88/URL-Shortner/actions)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)

A production-grade, horizontally scalable URL Shortener and Rate Limiting service designed to demonstrate distributed systems patterns: **Snowflake-style 64-bit ID generation**, **Redis cache-aside with circuit-breaker fallback**, **atomic Lua-scripted token bucket rate limiting**, **consistent hashing for cache sharding**, and **asynchronous event-stream click aggregation**.

---

## 🏛️ System Architecture

```
                                  +------------------------------------+
                                  |     Clients / Locust Traffic       |
                                  +-----------------+------------------+
                                                    |
                                                    v
                                  +------------------------------------+
                                  |    Nginx Reverse Proxy / LB (:80)  |
                                  |    (Least-Connections Balancing)   |
                                  +-----------------+------------------+
                                                    |
                         +--------------------------+--------------------------+
                         |                                                     |
                         v                                                     v
          +------------------------------+                      +------------------------------+
          | FastAPI Instance 1 (Node: 1) |                      | FastAPI Instance 2 (Node: 2) |
          | - Token Bucket (Atomic Lua)  |                      | - Token Bucket (Atomic Lua)  |
          | - Snowflake 64-bit ID Gen    |                      | - Snowflake 64-bit ID Gen    |
          | - Circuit Breaker Fallback   |                      | - Circuit Breaker Fallback   |
          | - JSON Logging & Prometheus  |                      | - JSON Logging & Prometheus  |
          +--------------+---------------+                      +--------------+---------------+
                         |                                                     |
                         +--------------------------+--------------------------+
                                                    |
                    +-------------------------------+-------------------------------+
                    |                               |                               |
                    v                               v                               v
       +-------------------------+     +-------------------------+     +-------------------------+
       | Redis Shard 1 (:6379)   |     | Redis Shard 2 (:6380)   |     | PostgreSQL Primary      |
       | (Consistent Hash Ring)  |     | (Consistent Hash Ring)  |     | (Read/Write Connection) |
       | - Cache-aside Lookups   |     | - Rate Limiter Buckets  |     | - 'urls' Master Table   |
       | - Click Event Queue     |     | - Sliding Window State  |     | - Read Replica Pool     |
       +------------+------------+     +-------------------------+     +------------+------------+
                    |                                                               ^
                    | LPUSH (Non-blocking enqueue)                                  | Bulk UPDATE/INSERT
                    v                                                               |
       +----------------------------------------------------------------------------+------------+
       | Standalone Click Worker (`click_worker.py`): Batches click analytics every 1s           |
       +-----------------------------------------------------------------------------------------+
```

---

## ⚙️ Key Technical Decisions & Interview Trade-Offs

### 1. Distributed ID Generation: Snowflake vs. DB Auto-Increment vs. Hash
* **Problem**: In a horizontally-scaled deployment (e.g., 10 API nodes), a centralized database auto-increment counter causes severe row contention, latency spikes, and represents a single point of failure (SPOF). MD5/SHA-256 truncation requires expensive collision detection queries.
* **Our Solution**: Implemented a **64-bit Twitter Snowflake-style ID generator**:
  - `41 bits`: Milliseconds elapsed since custom epoch ($\approx 69$ years span).
  - `10 bits`: Machine/Worker ID (supports up to $1,024$ distinct API nodes).
  - `12 bits`: Sequence counter ($4,096$ unique IDs per millisecond per worker node).
  - Generates over **4 million unique IDs/sec per node** locally in memory with **zero network/DB coordination**.
* **Base62 Encoding**: The 64-bit integer is converted using an alphanumeric alphabet `[0-9a-zA-Z]` ($62$ characters), producing clean 7-8 character short codes (`6jnT6Dzqwy`).

### 2. Rate Limiting: Atomic Lua Token Bucket vs. Sliding Window
* **Problem**: Naive `GET` followed by `SET` in Redis is susceptible to race conditions under concurrent client requests (read-modify-write hazard).
* **Our Solution**: Implemented an atomic **Token Bucket** algorithm executed entirely inside Redis using a custom Lua script:
  - Updates available tokens based on elapsed wall-clock time in float seconds.
  - Returns `429 Too Many Requests` with a dynamic `Retry-After` header when exhausted.
  - Also implemented a **Sliding Window Log** alternative using Redis sorted sets (`ZREMRANGEBYSCORE` + `ZCARD`).
* **Trade-off Analysis**:
  - *Token Bucket*: Memory efficient ($O(1)$ hash per client), allows configurable bursts, smooth refill.
  - *Sliding Window*: Strict limit enforcement, but higher memory footprint ($O(N)$ sorted set members per request in the window).

### 3. Asynchronous Persistence: Decoupling Click Tracking from the Hot Path
* **Problem**: Running `UPDATE urls SET click_count = click_count + 1 WHERE short_code = ...` on every redirect creates write locks on popular short URLs, killing redirect throughput.
* **Our Solution**:
  - On `GET /{code}`, URL resolution is immediate (sub-millisecond from Redis cache).
  - Click metadata (timestamp, referrer, user-agent, IP) is pushed to a Redis list (`LPUSH clicks:stream`) non-blockingly.
  - An asynchronous background worker (`scripts/click_worker.py`) periodically drains events in batches of 50–100 items, aggregating increments and issuing a single bulk SQL update.

### 4. Cache Sharding: Consistent Hash Ring vs. Modulo Hashing
* **Whiteboard Problem**: If Redis cache is partitioned across $N$ servers using modulo hashing (`hash(key) % N`), adding or removing a single node remaps almost **$100\%$ of keys**, causing instantaneous cache stampedes on the database.
* **Our Solution**: Engineered a **Consistent Hash Ring** (`app/services/hash_ring.py`) with 100 virtual node replicas per physical server using 128-bit MD5 hashes and binary search (`bisect`):
  - Adding a node only remaps $\approx 1/N$ of keys.
  - Virtual nodes eliminate "hot spots" and ensure uniform distribution across all physical shards.

### 5. Fault Tolerance: Circuit Breaker Pattern
* **Problem**: If Redis crashes or undergoes a network partition, the application shouldn't throw 500 Internal Server Errors or exhaust connection pools waiting on socket timeouts.
* **Our Solution**: Built a 3-state **Circuit Breaker** (`CLOSED`, `OPEN`, `HALF_OPEN`):
  - Detects 3 consecutive Redis timeouts/failures and trips to `OPEN`.
  - In `OPEN` state, requests **fail fast and bypass Redis**, reading directly from PostgreSQL read pool with zero timeout latency penalty.
  - After a 10s cooldown, probes Redis in `HALF_OPEN` state; if healthy, automatically recovers to `CLOSED`.

---

## 📊 Measured Benchmark Results

Automated benchmark executed with `load_test/benchmark.py` running 1,000 mixed requests (80% redirects, 15% creations, 5% rate-limit bursts) across 20 concurrent async workers:

| Metric | Measured Value |
| :--- | :--- |
| **Total Requests Processed** | 1,000 |
| **Wall-Clock Time** | 3.33 seconds |
| **Sustained Throughput** | **300.1 requests/sec** |
| **Average Latency** | **63.10 ms** |
| **P50 Latency (Median)** | **16.09 ms** |
| **P90 Latency** | **102.69 ms** |
| **P95 Latency** | **206.60 ms** |
| **Cache Hit Ratio (Hot Reads)** | **100.0%** (16 hits / 16 reads) |
| **Rate Limiter Enforcement** | 100% accuracy on burst traffic (HTTP 429) |

---

## 🚀 Quickstart Guide

### Prerequisites
- Python 3.12+ (Virtual environment)
- Docker & Docker Compose (Optional for full containerized cluster)

### 1. Local Setup (Zero Dependencies)
The application is pre-configured with automated fallbacks to SQLite and FakeRedis, allowing instant testing without external infrastructure:

```bash
# Clone and enter directory
cd "URL Shortner"

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run test suite (24 automated tests covering ID gen, caching, rate limiting, and circuit breaker)
pytest -v

# Run the automated performance benchmark
python load_test/benchmark.py

# Start the dev server
uvicorn app.main:app --reload --port 8000
```

### 2. Multi-Container Production Cluster (Docker Compose)
Spins up PostgreSQL, 2 Redis shards, 2 FastAPI app instances, Nginx round-robin load balancer, background click worker, and Prometheus:

```bash
docker-compose up -d --build
```

- **Public API (via Nginx)**: `http://localhost/`
- **Interactive Swagger Docs**: `http://localhost/docs`
- **Prometheus Dashboard**: `http://localhost:9090`

---

## 📡 API Reference

### 1. Shorten URL
```http
POST /shorten
Content-Type: application/json

{
  "long_url": "https://en.wikipedia.org/wiki/Distributed_computing",
  "custom_code": "dist-sys",
  "expires_in_seconds": 86400
}
```
**Response (`201 Created`):**
```json
{
  "short_code": "dist-sys",
  "short_url": "http://localhost:8000/dist-sys",
  "long_url": "https://en.wikipedia.org/wiki/Distributed_computing",
  "created_at": "2026-09-09T22:45:00Z",
  "expires_at": "2026-09-10T22:45:00Z"
}
```

### 2. Redirect
```http
GET /{code}
```
**Response (`302 Found`):**
- Headers:
  - `Location: https://en.wikipedia.org/wiki/Distributed_computing`
  - `X-Cache: HIT` (or `MISS`)
  - `X-Response-Time-Ms: 1.42`
  - `X-Request-ID: 6c1391c5-eed0-4368-987e-92edbf3336ba`

### 3. Click Analytics
```http
GET /analytics/{code}
```
**Response (`200 OK`):**
```json
{
  "short_code": "dist-sys",
  "long_url": "https://en.wikipedia.org/wiki/Distributed_computing",
  "total_clicks": 1420,
  "clicks_last_24h": 350,
  "top_referrers": {
    "https://twitter.com": 210,
    "https://news.ycombinator.com": 95,
    "Direct / None": 45
  },
  "recent_clicks": [...]
}
```

### 4. System Metrics
```http
GET /metrics
```
Prometheus formatted metrics export including `http_requests_total`, `http_request_duration_seconds`, `cache_operations_total`, `rate_limit_exceeded_total`, and `circuit_breaker_state`.

---

## 🧪 Project Structure

```
url-shortener/
├── app/
│   ├── config.py                 # Pydantic BaseSettings
│   ├── main.py                   # FastAPI lifespan & app definition
│   ├── middleware/
│   │   ├── rate_limiter.py       # Token Bucket & Sliding Window middleware
│   │   ├── logging_context.py    # X-Request-ID correlation & structured JSON logging
│   │   └── metrics.py            # Prometheus metrics collector
│   ├── models/
│   │   ├── database.py           # Dual R/W async SQLAlchemy pools
│   │   ├── url.py                # URLs schema definition
│   │   └── click.py              # ClickAnalytics schema definition
│   ├── routes/
│   │   ├── shortener.py          # POST /shorten, GET /{code}, DELETE /{code}
│   │   ├── analytics.py          # GET /analytics/{code}
│   │   └── health.py             # Liveness and readiness probes
│   ├── schemas/
│   │   ├── url.py                # Request/response validation
│   │   └── analytics.py          # Analytics payloads
│   └── services/
│       ├── encoding.py           # Base62 encode/decode
│       ├── id_generator.py       # Twitter Snowflake 64-bit ID generator
│       ├── cache.py              # Redis Cache-aside manager
│       ├── circuit_breaker.py    # 3-state Circuit Breaker (CLOSED/OPEN/HALF_OPEN)
│       ├── hash_ring.py          # ConsistentHashRing with virtual nodes
│       ├── sharded_cache.py      # Multi-node Redis sharding
│       └── click_processor.py    # Async click queue & batch persistence
├── scripts/
│   ├── rate_limiter_token_bucket.lua   # Atomic Lua Token Bucket script
│   ├── rate_limiter_sliding_window.lua # Atomic Lua Sliding Window script
│   └── click_worker.py                 # Standalone click batching daemon
├── load_test/
│   ├── locustfile.py             # Locust user scenarios (80/15/5 distribution)
│   └── benchmark.py              # Standalone async load testing & percentile reporter
├── tests/                        # 24 unit & integration tests
├── k8s/                          # Deployment, HPA, Service, ConfigMap, Secret manifests
├── .github/workflows/ci.yml      # Automated GitHub Actions CI pipeline
├── docker-compose.yml            # Multi-container cluster orchestration
├── nginx.conf                    # Round-robin reverse proxy
├── prometheus.yml                # Prometheus metrics scrape config
├── Dockerfile                    # Multi-stage production build
├── requirements.txt              # Production & test dependencies
└── README.md                     # Engineering documentation & CV pitch
```
