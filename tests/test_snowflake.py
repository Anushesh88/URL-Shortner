from concurrent.futures import ThreadPoolExecutor
import pytest
from app.services.id_generator import (
    SnowflakeIDGenerator,
    TIMESTAMP_SHIFT,
    WORKER_ID_SHIFT,
    MAX_SEQUENCE,
    MAX_WORKER_ID,
    ClockBackwardsError,
)


def test_snowflake_id_structure():
    worker_id = 42
    gen = SnowflakeIDGenerator(worker_id=worker_id)
    new_id = gen.generate_id()

    assert new_id > 0
    # Extract worker_id from bit shifts
    extracted_worker_id = (new_id >> WORKER_ID_SHIFT) & MAX_WORKER_ID
    assert extracted_worker_id == worker_id


def test_snowflake_monotonic_increasing():
    gen = SnowflakeIDGenerator(worker_id=1)
    ids = [gen.generate_id() for _ in range(1000)]

    for i in range(len(ids) - 1):
        assert ids[i] < ids[i + 1], f"Snowflake ID is not strictly monotonic: {ids[i]} >= {ids[i+1]}"


def test_snowflake_concurrency_no_collisions():
    gen = SnowflakeIDGenerator(worker_id=7)
    num_threads = 8
    ids_per_thread = 500

    def generate_batch():
        return [gen.generate_id() for _ in range(ids_per_thread)]

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(generate_batch) for _ in range(num_threads)]
        all_ids = []
        for f in futures:
            all_ids.extend(f.result())

    assert len(all_ids) == num_threads * ids_per_thread
    # Ensure zero collisions
    assert len(set(all_ids)) == len(all_ids)


def test_snowflake_invalid_worker_id():
    with pytest.raises(ValueError):
        SnowflakeIDGenerator(worker_id=-1)

    with pytest.raises(ValueError):
        SnowflakeIDGenerator(worker_id=1024)
