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
