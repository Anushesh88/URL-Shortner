import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.cache import CacheManager
from app.services.hash_ring import ConsistentHashRing

logger = logging.getLogger("sharded_cache")


class ShardedCacheManager:
    """Manages multi-node Redis sharding using Consistent Hashing.
    
    Routes keys dynamically to specific Redis shards. Adding or removing a Redis node
    only remaps approximately 1/N of keys, avoiding full cache invalidation and
    cache stampedes across database replicas.
    """

    def __init__(self, node_urls: Optional[List[str]] = None, replicas: int = 100):
        urls = node_urls or settings.redis_node_list
        self.hash_ring = ConsistentHashRing(nodes=urls, replicas=replicas)
        self.node_clients: Dict[str, CacheManager] = {
            url: CacheManager(redis_url=url) for url in urls
        }

    def _get_client_for_key(self, key: str) -> CacheManager:
        node = self.hash_ring.get_node(key)
        if not node or node not in self.node_clients:
            # Fallback to first available or create
            if self.node_clients:
                return next(iter(self.node_clients.values()))
            fallback_manager = CacheManager(redis_url=settings.REDIS_URL)
            return fallback_manager
        return self.node_clients[node]

    def add_node(self, node_url: str):
        """Dynamically adds a Redis node to the sharded cluster."""
        if node_url not in self.node_clients:
            self.node_clients[node_url] = CacheManager(redis_url=node_url)
            self.hash_ring.add_node(node_url)
            logger.info(f"Added Redis shard to ring: {node_url}")

    def remove_node(self, node_url: str):
        """Removes a Redis node from the sharded cluster."""
        if node_url in self.node_clients:
            self.hash_ring.remove_node(node_url)
            del self.node_clients[node_url]
            logger.info(f"Removed Redis shard from ring: {node_url}")

    async def get_url(self, short_code: str) -> Optional[Dict[str, Any]]:
        client = self._get_client_for_key(short_code)
        return await client.get_url(short_code)

    async def set_url(self, short_code: str, data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        client = self._get_client_for_key(short_code)
        return await client.set_url(short_code, data, ttl)

    async def delete_url(self, short_code: str) -> bool:
        client = self._get_client_for_key(short_code)
        return await client.delete_url(short_code)

    async def close(self):
        for client in self.node_clients.values():
            await client.close()


sharded_cache_manager = ShardedCacheManager()
