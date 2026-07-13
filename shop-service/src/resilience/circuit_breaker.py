from dataclasses import dataclass
from enum import Enum
import time
from typing import Callable


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class CircuitTransition:
    previous_state: CircuitState
    state: CircuitState
    failure_count: int
    reason: str


@dataclass
class _Circuit:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float | None = None
    half_open_calls: int = 0


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int,
        reset_seconds: float,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_seconds = max(0.0, reset_seconds)
        self.half_open_max_calls = max(1, half_open_max_calls)
        self._clock = clock or time.monotonic
        self._circuit = _Circuit()

    def before_call(self) -> CircuitTransition | None:
        if self._circuit.state == CircuitState.OPEN:
            if self._circuit.opened_at is not None and self._clock() - self._circuit.opened_at >= self.reset_seconds:
                return self._transition_to(CircuitState.HALF_OPEN, "RESET_TIMEOUT_REACHED")
            raise CircuitBreakerOpenError("Invoice-Service circuit breaker is OPEN")

        if self._circuit.state == CircuitState.HALF_OPEN:
            if self._circuit.half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpenError("Invoice-Service circuit breaker is HALF_OPEN")
            self._circuit.half_open_calls += 1
        return None

    def record_success(self) -> CircuitTransition | None:
        if self._circuit.state == CircuitState.CLOSED:
            self._circuit.failures = 0
            return None
        return self._transition_to(CircuitState.CLOSED, "INVOICE_CALL_SUCCEEDED", reset_failures=True)

    def record_failure(self, reason: str) -> CircuitTransition | None:
        self._circuit.failures += 1
        if self._circuit.state == CircuitState.HALF_OPEN or self._circuit.failures >= self.failure_threshold:
            return self._transition_to(CircuitState.OPEN, reason)
        return None

    @property
    def state(self) -> CircuitState:
        return self._circuit.state

    @property
    def failure_count(self) -> int:
        return self._circuit.failures

    def _transition_to(
        self,
        state: CircuitState,
        reason: str,
        reset_failures: bool = False,
    ) -> CircuitTransition:
        previous_state = self._circuit.state
        if state == CircuitState.OPEN:
            self._circuit.opened_at = self._clock()
            self._circuit.half_open_calls = 0
        elif state == CircuitState.HALF_OPEN:
            self._circuit.half_open_calls = 1
        elif state == CircuitState.CLOSED:
            self._circuit.opened_at = None
            self._circuit.half_open_calls = 0
        self._circuit.state = state
        if reset_failures:
            self._circuit.failures = 0
        return CircuitTransition(previous_state, state, self._circuit.failures, reason)
