import json
import logging
from typing import Any, Dict, Optional
import redis.asyncio as aioredis

from app.config import settings
from app.services.circuit_breaker import redis_circuit_breaker

logger = logging.getLogger("cache_service")


class CacheManager:
    """Manages Redis cache-aside operations with integrated Circuit Breaker protection."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._client: Optional[aioredis.Redis] = None
        self._is_fake = False

    async def get_client(self) -> aioredis.Redis:
        """Lazily initializes and returns an async Redis client, with fakeredis fallback."""
        if self._client is None:
            try:
                client = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                    socket_timeout=1.0,
                )
                await client.ping()
                self._client = client
                self._is_fake = False
                logger.info(f"Connected to live Redis at {self.redis_url}")
            except Exception as e:
                logger.warning(
                    f"Could not connect to Redis at {self.redis_url}: {e}. Initializing in-memory FakeRedis."
                )
                import fakeredis.aioredis as fake_aioredis
                self._client = fake_aioredis.FakeRedis(decode_responses=True)
                self._is_fake = True
        return self._client

    async def get_url(self, short_code: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached URL metadata by short code using cache-aside pattern."""
        if not redis_circuit_breaker.can_execute():
            logger.info("Circuit breaker OPEN. Skipping Redis cache lookup.")
            return None

        key = f"url:{short_code}"
        try:
            client = await self.get_client()
            raw_data = await client.get(key)
            if raw_data:
                redis_circuit_breaker.record_success()
                return json.loads(raw_data)
            return None
        except Exception as e:
            redis_circuit_breaker.record_failure(e)
            logger.warning(f"Cache lookup failed for key {key}: {e}. Falling back to DB.")
            return None

    async def set_url(self, short_code: str, data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """Caches URL metadata with TTL."""
        if not redis_circuit_breaker.can_execute():
            return False

        key = f"url:{short_code}"
        ttl_seconds = ttl if ttl is not None else settings.CACHE_TTL_SECONDS
        try:
            client = await self.get_client()
            serialized = json.dumps(data)
            await client.set(key, serialized, ex=ttl_seconds)
            redis_circuit_breaker.record_success()
            return True
        except Exception as e:
            redis_circuit_breaker.record_failure(e)
            logger.warning(f"Cache write failed for key {key}: {e}")
            return False

    async def delete_url(self, short_code: str) -> bool:
        """Invalidates cached URL metadata on deletion."""
        if not redis_circuit_breaker.can_execute():
            return False

        key = f"url:{short_code}"
        try:
            client = await self.get_client()
            await client.delete(key)
            redis_circuit_breaker.record_success()
            return True
        except Exception as e:
            redis_circuit_breaker.record_failure(e)
            logger.warning(f"Cache invalidation failed for key {key}: {e}")
            return False

    async def close(self):
        """Closes Redis connections."""
        if self._client and not self._is_fake:
            await self._client.aclose()
            self._client = None


cache_manager = CacheManager()
