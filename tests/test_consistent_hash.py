from collections import Counter
from app.services.hash_ring import ConsistentHashRing


def test_consistent_hash_deterministic_mapping():
    nodes = ["redis-node-1", "redis-node-2", "redis-node-3"]
    ring = ConsistentHashRing(nodes=nodes, replicas=100)

    # The same key always maps to the same node
    node_a = ring.get_node("user_session_100")
    node_b = ring.get_node("user_session_100")
    assert node_a == node_b
    assert node_a in nodes


def test_consistent_hash_distribution():
    nodes = ["redis-node-1", "redis-node-2", "redis-node-3"]
    ring = ConsistentHashRing(nodes=nodes, replicas=150)

    key_counts = Counter()
    total_keys = 6000
    for i in range(total_keys):
        key = f"cache:key:{i}"
        node = ring.get_node(key)
        key_counts[node] += 1

    # In 3 nodes, ideal average is 33.33% (~2000 keys). Each node should receive between 22% and 44%
    for node, count in key_counts.items():
        percentage = (count / total_keys) * 100
        assert 20.0 < percentage < 45.0, f"Node {node} received {percentage}% of keys (uneven distribution)"


def test_minimal_key_remapping_on_node_addition():
    """Validates the core distributed system property: adding a node only remaps ~1/N keys."""
    initial_nodes = ["node-1", "node-2", "node-3"]
    ring = ConsistentHashRing(nodes=initial_nodes, replicas=150)

    keys = [f"key_{i}" for i in range(2000)]
    initial_mapping = {k: ring.get_node(k) for k in keys}

    # Add 4th node
    ring.add_node("node-4")
    new_mapping = {k: ring.get_node(k) for k in keys}

    remapped_keys = sum(1 for k in keys if initial_mapping[k] != new_mapping[k])
    remapping_ratio = remapped_keys / len(keys)

    # In modulo hashing (hash(key) % N), adding a node remaps ~75% of all keys.
    # In consistent hashing with 4 nodes, theoretical optimal is 1/4 = 25%.
    # We assert remapped keys is significantly less than 45% (typically 20-30%).
    assert 0.15 <= remapping_ratio <= 0.35, (
        f"Expected ~25% remapping on adding 4th node, got {remapping_ratio * 100:.1f}%"
    )


def test_sharded_cache_manager_multi_node_distribution():
    """Verifies that ShardedCacheManager dynamically routes keys across multiple Redis shards
    with isolated circuit breakers per shard."""
    from app.services.sharded_cache import ShardedCacheManager

    shard_nodes = ["redis://redis-shard-1:6379/0", "redis://redis-shard-2:6379/0"]
    mgr = ShardedCacheManager(node_urls=shard_nodes, replicas=100)

    # 1. Ensure separate CacheManagers and CircuitBreakers exist per shard
    assert len(mgr.node_clients) == 2
    assert "redis://redis-shard-1:6379/0" in mgr.node_clients
    assert "redis://redis-shard-2:6379/0" in mgr.node_clients
    assert mgr.node_clients[shard_nodes[0]].circuit_breaker.name != mgr.node_clients[shard_nodes[1]].circuit_breaker.name

    # 2. Hash ring must distribute keys to BOTH shards
    shard_counts = Counter()
    for i in range(200):
        key = f"code_{i}"
        target_shard = mgr.get_node_for_key(key)
        shard_counts[target_shard] += 1

    assert shard_counts[shard_nodes[0]] > 0
    assert shard_counts[shard_nodes[1]] > 0
    # Both shards should have roughly balanced distribution
    assert 60 <= shard_counts[shard_nodes[0]] <= 140
    assert 60 <= shard_counts[shard_nodes[1]] <= 140
