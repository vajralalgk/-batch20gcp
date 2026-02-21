"""Cross-region request hedging for latency optimisation.

When an inference request does not complete within the observed p50
latency of the primary region, a duplicate ("hedge") request is
dispatched to a secondary region.  The first response wins and the
duplicate is cancelled, reducing tail-latency at the cost of slightly
increased aggregate load.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class HedgeOutcome(str, Enum):
    """Outcome of a hedged request pair."""

    PRIMARY_WON = "primary_won"
    HEDGE_WON = "hedge_won"
    BOTH_FAILED = "both_failed"
    NOT_HEDGED = "not_hedged"


@dataclass
class HedgingStats:
    """Aggregate hedging statistics."""

    total_requests: int = 0
    hedged_requests: int = 0
    primary_wins: int = 0
    hedge_wins: int = 0
    both_failed: int = 0
    total_latency_saved_ms: float = 0.0

    @property
    def hedge_rate(self) -> float:
        """Fraction of requests that triggered a hedge."""
        if self.total_requests == 0:
            return 0.0
        return self.hedged_requests / self.total_requests

    @property
    def hedge_success_rate(self) -> float:
        """Fraction of hedged requests where the hedge beat the primary."""
        if self.hedged_requests == 0:
            return 0.0
        return self.hedge_wins / self.hedged_requests

    @property
    def avg_latency_improvement_ms(self) -> float:
        """Average latency saved per hedge-winning request."""
        if self.hedge_wins == 0:
            return 0.0
        return self.total_latency_saved_ms / self.hedge_wins


@dataclass
class HedgeResult:
    """Result of processing a (possibly hedged) request."""

    request_id: str
    outcome: HedgeOutcome
    primary_latency_ms: Optional[float] = None
    hedge_latency_ms: Optional[float] = None
    winning_region: str = ""
    response: Any = None
    latency_saved_ms: float = 0.0


@dataclass
class _InflightHedge:
    """Tracks an in-flight pair of primary + hedge requests."""

    request_id: str
    primary_region: str
    hedge_region: str
    dispatched_at: float
    hedge_dispatched_at: Optional[float] = None
    cancelled: bool = False


class RequestHedger:
    """Cross-region request hedger for tail-latency reduction.

    Parameters
    ----------
    primary_region:
        Default primary region for outgoing requests.
    secondary_region:
        Default secondary (hedge) region.
    p50_latency_ms:
        Observed p50 latency in milliseconds; hedging triggers after this
        delay.  Updated dynamically.
    max_hedge_ratio:
        Maximum fraction of total requests that may be hedged (default
        0.25, i.e. 25%).
    hedge_timeout_ms:
        Maximum time (ms) to wait for either response before declaring
        failure (default 5000).
    dispatch_fn:
        Async callable ``(region, request) -> response`` used to dispatch
        requests.  Must be supplied before calling :meth:`hedge_request`.
    """

    def __init__(
        self,
        primary_region: str = "us-east-1",
        secondary_region: str = "us-west-2",
        p50_latency_ms: float = 50.0,
        max_hedge_ratio: float = 0.25,
        hedge_timeout_ms: float = 5000.0,
        dispatch_fn: Optional[
            Callable[[str, Dict[str, Any]], Coroutine[Any, Any, Any]]
        ] = None,
    ) -> None:
        self._primary_region = primary_region
        self._secondary_region = secondary_region
        self._p50_ms = p50_latency_ms
        self._max_hedge_ratio = max_hedge_ratio
        self._hedge_timeout_s = hedge_timeout_ms / 1000.0
        self._dispatch_fn = dispatch_fn

        self._lock = threading.Lock()
        self._stats = HedgingStats()
        self._inflight: Dict[str, _InflightHedge] = {}
        self._cancelled: Set[str] = set()

        # Adaptive p50 tracking (exponential moving average)
        self._ema_alpha = 0.1

        logger.info(
            "RequestHedger initialised (primary=%s, secondary=%s, p50=%.1fms)",
            primary_region,
            secondary_region,
            p50_latency_ms,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def hedge_request(
        self,
        request: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> HedgeResult:
        """Process a request with optional hedging.

        1. Dispatch to the primary region.
        2. If no response within the current p50 latency, send a hedge
           to the secondary region.
        3. Return the first successful response and cancel the other.

        Parameters
        ----------
        request:
            The inference request payload.
        request_id:
            Optional caller-provided request identifier.
        """
        rid = request_id or str(uuid.uuid4())

        with self._lock:
            self._stats.total_requests += 1

        do_hedge = self.should_hedge(request)

        if not do_hedge or self._dispatch_fn is None:
            # Straight-through path (no hedging)
            result = await self._dispatch_primary(rid, request)
            return result

        return await self._dispatch_with_hedge(rid, request)

    def should_hedge(self, request: Optional[Dict[str, Any]] = None) -> bool:
        """Determine whether a request qualifies for hedging.

        Considers the current hedge ratio against the configured maximum.
        """
        with self._lock:
            if self._stats.total_requests == 0:
                return True
            current_ratio = self._stats.hedged_requests / max(
                self._stats.total_requests, 1
            )
        return current_ratio < self._max_hedge_ratio

    def cancel_duplicate(self, request_id: str) -> bool:
        """Mark the hedge for *request_id* as cancelled.

        Returns True if there was an in-flight hedge to cancel.
        """
        with self._lock:
            if request_id in self._inflight:
                self._inflight[request_id].cancelled = True
                self._cancelled.add(request_id)
                logger.debug("Cancelled hedge for request %s", request_id)
                return True
        return False

    def get_hedging_stats(self) -> HedgingStats:
        """Return a snapshot of aggregate hedging statistics."""
        with self._lock:
            return HedgingStats(
                total_requests=self._stats.total_requests,
                hedged_requests=self._stats.hedged_requests,
                primary_wins=self._stats.primary_wins,
                hedge_wins=self._stats.hedge_wins,
                both_failed=self._stats.both_failed,
                total_latency_saved_ms=self._stats.total_latency_saved_ms,
            )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def update_p50(self, latency_ms: float) -> None:
        """Update the p50 latency estimate (exponential moving average)."""
        with self._lock:
            self._p50_ms = (
                self._ema_alpha * latency_ms
                + (1 - self._ema_alpha) * self._p50_ms
            )

    def set_dispatch_fn(
        self,
        fn: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, Any]],
    ) -> None:
        """Set or replace the async dispatch function."""
        self._dispatch_fn = fn

    # ------------------------------------------------------------------
    # Internal dispatch helpers
    # ------------------------------------------------------------------

    async def _dispatch_primary(
        self, request_id: str, request: Dict[str, Any]
    ) -> HedgeResult:
        """Dispatch to primary only (no hedge)."""
        start = time.monotonic()
        try:
            if self._dispatch_fn is not None:
                response = await asyncio.wait_for(
                    self._dispatch_fn(self._primary_region, request),
                    timeout=self._hedge_timeout_s,
                )
            else:
                response = None

            latency_ms = (time.monotonic() - start) * 1000
            self._update_p50_ema(latency_ms)

            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.NOT_HEDGED,
                primary_latency_ms=latency_ms,
                winning_region=self._primary_region,
                response=response,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "Primary dispatch failed for %s: %s", request_id, exc
            )
            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.BOTH_FAILED,
                primary_latency_ms=latency_ms,
            )

    async def _dispatch_with_hedge(
        self, request_id: str, request: Dict[str, Any]
    ) -> HedgeResult:
        """Dispatch to primary and, if slow, hedge to secondary."""
        assert self._dispatch_fn is not None

        with self._lock:
            self._stats.hedged_requests += 1
            hedge_delay_s = self._p50_ms / 1000.0
            inflight = _InflightHedge(
                request_id=request_id,
                primary_region=self._primary_region,
                hedge_region=self._secondary_region,
                dispatched_at=time.monotonic(),
            )
            self._inflight[request_id] = inflight

        primary_start = time.monotonic()

        # Create primary task
        primary_task = asyncio.ensure_future(
            self._dispatch_fn(self._primary_region, request)
        )

        # Wait for p50 delay -- if primary finishes early, great.
        done, _ = await asyncio.wait(
            {primary_task}, timeout=hedge_delay_s
        )

        if done:
            # Primary responded within p50 -- no hedge needed.
            primary_latency_ms = (time.monotonic() - primary_start) * 1000
            self._update_p50_ema(primary_latency_ms)
            self._cleanup_inflight(request_id)

            try:
                response = primary_task.result()
            except Exception as exc:
                logger.warning("Primary fast-path failed: %s", exc)
                with self._lock:
                    self._stats.primary_wins += 1
                return HedgeResult(
                    request_id=request_id,
                    outcome=HedgeOutcome.PRIMARY_WON,
                    primary_latency_ms=primary_latency_ms,
                    winning_region=self._primary_region,
                    response=None,
                )

            with self._lock:
                self._stats.primary_wins += 1
            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.PRIMARY_WON,
                primary_latency_ms=primary_latency_ms,
                winning_region=self._primary_region,
                response=response,
            )

        # Primary is slow -- dispatch hedge
        with self._lock:
            inflight.hedge_dispatched_at = time.monotonic()

        hedge_start = time.monotonic()
        hedge_task = asyncio.ensure_future(
            self._dispatch_fn(self._secondary_region, request)
        )

        # Race primary vs hedge
        remaining_timeout = max(
            0.0,
            self._hedge_timeout_s - (time.monotonic() - primary_start),
        )
        done, pending = await asyncio.wait(
            {primary_task, hedge_task},
            timeout=remaining_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )

        primary_latency_ms = (time.monotonic() - primary_start) * 1000
        hedge_latency_ms = (time.monotonic() - hedge_start) * 1000

        # Cancel the loser
        for task in pending:
            task.cancel()

        self._cleanup_inflight(request_id)

        if not done:
            # Both timed out
            with self._lock:
                self._stats.both_failed += 1
            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.BOTH_FAILED,
                primary_latency_ms=primary_latency_ms,
                hedge_latency_ms=hedge_latency_ms,
            )

        winner_task = done.pop()
        try:
            response = winner_task.result()
        except Exception:
            response = None

        if winner_task is primary_task:
            self._update_p50_ema(primary_latency_ms)
            with self._lock:
                self._stats.primary_wins += 1
            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.PRIMARY_WON,
                primary_latency_ms=primary_latency_ms,
                hedge_latency_ms=hedge_latency_ms,
                winning_region=self._primary_region,
                response=response,
            )
        else:
            saved = primary_latency_ms - hedge_latency_ms
            with self._lock:
                self._stats.hedge_wins += 1
                if saved > 0:
                    self._stats.total_latency_saved_ms += saved
            return HedgeResult(
                request_id=request_id,
                outcome=HedgeOutcome.HEDGE_WON,
                primary_latency_ms=primary_latency_ms,
                hedge_latency_ms=hedge_latency_ms,
                winning_region=self._secondary_region,
                response=response,
                latency_saved_ms=max(saved, 0.0),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_p50_ema(self, latency_ms: float) -> None:
        """Update the p50 EMA with a fresh observation."""
        with self._lock:
            self._p50_ms = (
                self._ema_alpha * latency_ms
                + (1 - self._ema_alpha) * self._p50_ms
            )

    def _cleanup_inflight(self, request_id: str) -> None:
        with self._lock:
            self._inflight.pop(request_id, None)
            self._cancelled.discard(request_id)
