import threading
import time
from app.config import settings

# Custom Epoch: Jan 1, 2026 00:00:00 UTC (in milliseconds)
CUSTOM_EPOCH = 1767225600000

# Bit allocations
TIMESTAMP_BITS = 41
WORKER_ID_BITS = 10
SEQUENCE_BITS = 12

# Max values
MAX_WORKER_ID = (1 << WORKER_ID_BITS) - 1   # 1023
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1     # 4095

# Bit shifts
WORKER_ID_SHIFT = SEQUENCE_BITS             # 12
TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS  # 22


class ClockBackwardsError(Exception):
    """Raised when system clock moves backwards beyond tolerance."""
    pass


class SnowflakeIDGenerator:
    """Twitter Snowflake 64-bit distributed unique ID generator.
    
    Structure of the 64-bit ID:
    - 1 bit: Unused (sign bit, always 0)
    - 41 bits: Milliseconds since custom epoch (~69 years lifespan)
    - 10 bits: Machine/Worker ID (supports 1,024 independent server nodes)
    - 12 bits: Sequence counter (up to 4,096 IDs per millisecond per node)
    
    This guarantees collision-free, coordination-free distributed ID generation
    at 4+ million IDs/sec per node without database auto-increment lock contention.
    """

    def __init__(self, worker_id: int = None, epoch: int = CUSTOM_EPOCH):
        self.worker_id = worker_id if worker_id is not None else settings.WORKER_ID
        if not (0 <= self.worker_id <= MAX_WORKER_ID):
            raise ValueError(f"worker_id must be between 0 and {MAX_WORKER_ID}, got {self.worker_id}")

        self.epoch = epoch
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

    def _current_timestamp(self) -> int:
        """Returns current time in milliseconds."""
        return int(time.time() * 1000)

    def _wait_next_millis(self, last_ts: int) -> int:
        """Spin-waits until the next millisecond arrives."""
        ts = self._current_timestamp()
        while ts <= last_ts:
            time.sleep(0.0001)
            ts = self._current_timestamp()
        return ts

    def generate_id(self) -> int:
        """Generates a globally unique 64-bit Snowflake ID."""
        with self._lock:
            ts = self._current_timestamp()

            if ts < self.last_timestamp:
                drift = self.last_timestamp - ts
                if drift < 5:
                    # Minor drift (NTP adjustment) - wait it out
                    time.sleep(drift / 1000.0)
                    ts = self._current_timestamp()
                else:
                    raise ClockBackwardsError(
                        f"Clock moved backwards by {drift}ms! Refusing to generate ID."
                    )

            if ts == self.last_timestamp:
                self.sequence = (self.sequence + 1) & MAX_SEQUENCE
                if self.sequence == 0:
                    # Sequence overflow in this millisecond; wait for next ms
                    ts = self._wait_next_millis(self.last_timestamp)
            else:
                self.sequence = 0

            self.last_timestamp = ts

            # Assemble 64-bit integer ID
            snowflake_id = (
                ((ts - self.epoch) << TIMESTAMP_SHIFT) |
                (self.worker_id << WORKER_ID_SHIFT) |
                self.sequence
            )
            return snowflake_id


# Default global instance
id_generator = SnowflakeIDGenerator()
