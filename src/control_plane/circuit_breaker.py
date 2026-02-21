"""Circuit breaker pattern for fault isolation in the inference fleet.

Implements the classic CLOSED -> OPEN -> HALF_OPEN state machine with
configurable failure thresholds, recovery timeouts, and adaptive load
shedding. Supports request hedging when the primary path is degraded.
"""

from __future__ import annotations

import logging
import random
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """State of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    """Running counters for the circuit breaker."""

    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    last_state_change: float = field(default_factory=time.time)
    half_open_successes: int = 0
    half_open_calls: int = 0
    shed_count: int = 0
    hedged_count: int = 0


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""


class CircuitBreaker:
    """Circuit breaker with adaptive load shedding and request hedging.

    State transitions:
        * **CLOSED** -- Normal operation. Failures are counted. When
          ``failure_threshold`` consecutive failures occur, the circuit
          moves to OPEN.
        * **OPEN** -- All calls are rejected (or hedged). After
          ``recovery_timeout`` seconds, the circuit transitions to
          HALF_OPEN.
        * **HALF_OPEN** -- A limited number of probe calls are allowed
          (``half_open_max_calls``). If they all succeed, the circuit
          resets to CLOSED. Any failure sends it back to OPEN.

    Args:
        name: Human-readable identifier for logging.
        failure_threshold: Consecutive failures required to trip the circuit.
        recovery_timeout: Seconds to wait in OPEN before moving to HALF_OPEN.
        half_open_max_calls: Number of probe calls allowed in HALF_OPEN.
        load_shed_probability: When OPEN, probability of shedding a request
            instead of hedging it (0.0 -- 1.0).
        hedge_func: Optional callable invoked as a fallback when the circuit
            is open and the request is not shed.
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        load_shed_probability: float = 0.5,
        hedge_func: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._load_shed_probability = load_shed_probability
        self._hedge_func = hedge_func

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute *func* through the circuit breaker.

        Args:
            func: The callable to protect.
            *args: Positional arguments forwarded to *func*.
            **kwargs: Keyword arguments forwarded to *func*.

        Returns:
            The return value of *func* (or the hedge function).

        Raises:
            CircuitBreakerOpenError: If the circuit is open and the
                request is shed (no hedge available).
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            state = self._state

        if state == CircuitState.OPEN:
            return self._handle_open_call(func, *args, **kwargs)

        if state == CircuitState.HALF_OPEN:
            return self._handle_half_open_call(func, *args, **kwargs)

        # CLOSED -- execute normally.
        return self._execute(func, *args, **kwargs)

    def record_success(self) -> None:
        """Manually record a successful call.

        Typically invoked by the ``call`` wrapper, but exposed publicly
        so external health probes can feed results into the breaker.
        """
        with self._lock:
            self._stats.total_calls += 1
            self._stats.success_count += 1
            self._stats.consecutive_failures = 0
            self._stats.last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._stats.half_open_successes += 1
                if self._stats.half_open_successes >= self._half_open_max_calls:
                    self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """Manually record a failed call.

        Exposed publicly so callers with out-of-band failure signals
        (e.g., timeout watchers) can trip the breaker.
        """
        with self._lock:
            self._stats.total_calls += 1
            self._stats.failure_count += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
            elif (
                self._state == CircuitState.CLOSED
                and self._stats.consecutive_failures >= self._failure_threshold
            ):
                self._transition(CircuitState.OPEN)

    def get_state(self) -> dict[str, Any]:
        """Return the current state and statistics of the circuit breaker.

        Returns:
            A dictionary containing state, counters, and timing info.
        """
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.value,
                "total_calls": self._stats.total_calls,
                "success_count": self._stats.success_count,
                "failure_count": self._stats.failure_count,
                "consecutive_failures": self._stats.consecutive_failures,
                "last_failure_time": self._stats.last_failure_time,
                "last_success_time": self._stats.last_success_time,
                "last_state_change": self._stats.last_state_change,
                "half_open_successes": self._stats.half_open_successes,
                "half_open_calls": self._stats.half_open_calls,
                "shed_count": self._stats.shed_count,
                "hedged_count": self._stats.hedged_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "half_open_max_calls": self._half_open_max_calls,
            }

    def reset(self) -> None:
        """Force-reset the circuit breaker to CLOSED with fresh counters."""
        with self._lock:
            self._transition(CircuitState.CLOSED)
            self._stats = CircuitStats()
            logger.info("Circuit breaker '%s' manually reset.", self._name)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run func and record the outcome."""
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def _handle_open_call(
        self, func: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        """Decide whether to shed or hedge when the circuit is open."""
        # Adaptive load shedding.
        if random.random() < self._load_shed_probability:
            with self._lock:
                self._stats.shed_count += 1
            if self._hedge_func is not None:
                with self._lock:
                    self._stats.hedged_count += 1
                logger.debug(
                    "Circuit '%s' OPEN -- shedding primary, invoking hedge.",
                    self._name,
                )
                return self._hedge_func(*args, **kwargs)
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self._name}' is OPEN. Request shed."
            )

        # Allow the call through as a "hedge" attempt.
        if self._hedge_func is not None:
            with self._lock:
                self._stats.hedged_count += 1
            logger.debug(
                "Circuit '%s' OPEN -- hedging request.", self._name,
            )
            return self._hedge_func(*args, **kwargs)

        raise CircuitBreakerOpenError(
            f"Circuit breaker '{self._name}' is OPEN. No hedge configured."
        )

    def _handle_half_open_call(
        self, func: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        """Allow a limited number of probe calls in HALF_OPEN state."""
        with self._lock:
            if self._stats.half_open_calls >= self._half_open_max_calls:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self._name}' HALF_OPEN probe limit reached."
                )
            self._stats.half_open_calls += 1

        return self._execute(func, *args, **kwargs)

    def _maybe_transition_to_half_open(self) -> None:
        """Check if recovery timeout has elapsed while OPEN.

        Must be called while holding ``self._lock``.
        """
        if self._state != CircuitState.OPEN:
            return
        elapsed = time.time() - self._stats.last_state_change
        if elapsed >= self._recovery_timeout:
            self._transition(CircuitState.HALF_OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        """Transition to a new circuit state (caller must hold _lock)."""
        old_state = self._state
        self._state = new_state
        self._stats.last_state_change = time.time()

        if new_state == CircuitState.HALF_OPEN:
            self._stats.half_open_successes = 0
            self._stats.half_open_calls = 0

        if new_state == CircuitState.CLOSED:
            self._stats.consecutive_failures = 0

        logger.info(
            "Circuit breaker '%s': %s -> %s",
            self._name,
            old_state.value,
            new_state.value,
        )
