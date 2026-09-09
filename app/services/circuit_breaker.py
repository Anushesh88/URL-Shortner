from enum import Enum
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger("circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Circuit Breaker pattern to protect against cascading failures during Redis outages.
    
    States:
    - CLOSED: Normal operation. Errors increment failure counter.
    - OPEN: Tripped. Bypasses Redis directly to prevent latency spikes and socket exhaustion.
    - HALF_OPEN: Trial state after recovery timeout. Sends a single probe request to check Redis health.
    """

    def __init__(
        self,
        name: str = "redis_circuit",
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.last_state_change = time.time()

    def can_execute(self) -> bool:
        """Determines whether a call can be attempted based on current circuit state."""
        now = time.time()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.recovery_timeout:
                logger.warning(
                    f"Circuit Breaker [{self.name}]: Recovery timeout elapsed. Switching OPEN -> HALF_OPEN (probing)."
                )
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Only allow one test execution through
            return True

        return True

    def record_success(self):
        """Records a successful operation and resets circuit to CLOSED."""
        if self.state != CircuitState.CLOSED:
            logger.info(
                f"Circuit Breaker [{self.name}]: Probe succeeded! Switching {self.state.value} -> CLOSED."
            )
            self.state = CircuitState.CLOSED
            self.last_state_change = time.time()
        self.failure_count = 0

    def record_failure(self, error: Optional[Exception] = None):
        """Records a failed operation. Trips circuit to OPEN if threshold reached."""
        self.last_failure_time = time.time()
        self.failure_count += 1
        logger.warning(
            f"Circuit Breaker [{self.name}]: Operation failed (count: {self.failure_count}/{self.failure_threshold}): {error}"
        )

        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.error(
                    f"Circuit Breaker [{self.name}]: Failure threshold breached! Tripping to OPEN."
                )
                self.state = CircuitState.OPEN
                self.last_state_change = self.last_failure_time

    @property
    def metric_value(self) -> int:
        """Returns integer value for Prometheus metrics (0: CLOSED, 1: OPEN, 2: HALF_OPEN)."""
        mapping = {
            CircuitState.CLOSED: 0,
            CircuitState.OPEN: 1,
            CircuitState.HALF_OPEN: 2,
        }
        return mapping[self.state]


# Global default circuit breaker for Redis cache
redis_circuit_breaker = CircuitBreaker()
