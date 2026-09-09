import bisect
import hashlib
from typing import Dict, List, Optional


class ConsistentHashRing:
    """Consistent Hash Ring with virtual nodes (replicas) for distributed sharding.
    
    Prevents cache stampedes and massive key re-mappings when scaling Redis nodes
    horizontally. While modulo hashing remaps almost 100% of keys when a node is added
    or removed (keys % N), consistent hashing remaps only K/N keys (where K is total keys
    and N is number of nodes).
    
    Virtual nodes ensure uniform distribution across all physical nodes.
    """

    def __init__(self, nodes: Optional[List[str]] = None, replicas: int = 100):
        """Initializes the hash ring.
        
        Args:
            nodes: Initial list of node identifiers (e.g. host:port or URLs).
            replicas: Number of virtual nodes per physical node to ensure uniform distribution.
        """
        self.replicas = replicas
        self.ring: Dict[int, str] = {}
        self.sorted_keys: List[int] = []
        self._nodes: set = set()

        for node in nodes or []:
            self.add_node(node)

    def _hash(self, key: str) -> int:
        """Computes a 128-bit MD5 integer hash for a given key."""
        return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)

    def add_node(self, node: str) -> None:
        """Adds a physical node and its virtual replicas to the ring."""
        if node in self._nodes:
            return
        self._nodes.add(node)
        for i in range(self.replicas):
            virtual_key = f"{node}#replica_{i}"
            h = self._hash(virtual_key)
            self.ring[h] = node
            bisect.insort(self.sorted_keys, h)

    def remove_node(self, node: str) -> None:
        """Removes a physical node and its virtual replicas from the ring."""
        if node not in self._nodes:
            return
        self._nodes.remove(node)
        for i in range(self.replicas):
            virtual_key = f"{node}#replica_{i}"
            h = self._hash(virtual_key)
            if h in self.ring:
                del self.ring[h]
                idx = bisect.bisect_left(self.sorted_keys, h)
                if idx < len(self.sorted_keys) and self.sorted_keys[idx] == h:
                    del self.sorted_keys[idx]

    def get_node(self, key: str) -> Optional[str]:
        """Maps an arbitrary key to the nearest clockwise physical node on the ring."""
        if not self.ring:
            return None
        h = self._hash(key)
        idx = bisect.bisect(self.sorted_keys, h) % len(self.sorted_keys)
        return self.ring[self.sorted_keys[idx]]

    @property
    def nodes(self) -> List[str]:
        return list(self._nodes)
