"""Adaptive rate limiting based on real-time system capacity.

Implements a token-bucket algorithm with an adaptive refill rate that
responds to current GPU load and queue depth.  Three priority tiers
(premium, standard, best-effort) receive progressively tighter limits
under heavy load, enabling graceful degradation.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PriorityTier(str, Enum):
    """Client priority classification."""

    PREMIUM = "premium"
    STANDARD = "standard"
    BEST_EFFORT = "best_effort"


class RateLimitDecision(str, Enum):
    """Outcome of a rate-limit check."""

    ALLOWED = "allowed"
    THROTTLED = "throttled"
    REJECTED = "rejected"


@dataclass
class TierConfig:
    """Rate-limit configuration for a priority tier."""

    max_tokens: float                # bucket capacity
    base_refill_rate: float          # tokens per second at nominal load
    min_refill_rate: float           # floor under extreme load
    burst_allowance: float = 1.0     # multiplier for short bursts
    degradation_factor: float = 0.5  # how aggressively to cut under load

    def effective_refill(self, system_load: float) -> float:
        """Compute the adaptive refill rate given *system_load* (0-1)."""
        if system_load <= 0.5:
            return self.base_refill_rate
        # Linear ramp-down from base to min between load 0.5 and 1.0
        scale = 1.0 - (system_load - 0.5) * 2.0 * self.degradation_factor
        return max(self.base_refill_rate * scale, self.min_refill_rate)


# Default tier configurations
_DEFAULT_TIERS: Dict[PriorityTier, TierConfig] = {
    PriorityTier.PREMIUM: TierConfig(
        max_tokens=100.0,
        base_refill_rate=50.0,
        min_refill_rate=20.0,
        burst_allowance=1.5,
        degradation_factor=0.3,
    ),
    PriorityTier.STANDARD: TierConfig(
        max_tokens=50.0,
        base_refill_rate=25.0,
        min_refill_rate=5.0,
        burst_allowance=1.2,
        degradation_factor=0.5,
    ),
    PriorityTier.BEST_EFFORT: TierConfig(
        max_tokens=20.0,
        base_refill_rate=10.0,
        min_refill_rate=1.0,
        burst_allowance=1.0,
        degradation_factor=0.8,
    ),
}


@dataclass
class _TokenBucket:
    """Internal token bucket state for a single client."""

    tokens: float
    max_tokens: float
    last_refill: float = field(default_factory=time.monotonic)
    total_allowed: int = 0
    total_throttled: int = 0
    total_rejected: int = 0

    def refill(self, rate: float) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + rate * elapsed)
        self.last_refill = now

    def try_consume(self, cost: float = 1.0) -> bool:
        """Try to consume *cost* tokens.  Returns True on success."""
        if self.tokens >= cost:
            self.tokens -= cost
            self.total_allowed += 1
            return True
        return False


@dataclass
class RateLimitResult:
    """Detailed outcome of a rate-limit check."""

    decision: RateLimitDecision
    client_id: str
    tier: str
    tokens_remaining: float
    retry_after_seconds: Optional[float] = None
    current_refill_rate: float = 0.0
    message: str = ""


@dataclass
class ClientUsage:
    """Usage statistics for a single client."""

    client_id: str
    tier: str
    tokens_remaining: float
    max_tokens: float
    total_allowed: int
    total_throttled: int
    total_rejected: int
    current_refill_rate: float


class AdaptiveRateLimiter:
    """Adaptive, tier-aware rate limiter.

    Parameters
    ----------
    tier_configs:
        Override default tier configurations.
    default_tier:
        Tier assigned to clients with no explicit mapping (default
        ``STANDARD``).
    cleanup_interval_s:
        Seconds between stale-bucket eviction passes (default 300).
    bucket_ttl_s:
        Seconds of inactivity before a client's bucket is evicted
        (default 3600).
    """

    def __init__(
        self,
        tier_configs: Optional[Dict[PriorityTier, TierConfig]] = None,
        default_tier: PriorityTier = PriorityTier.STANDARD,
        cleanup_interval_s: float = 300.0,
        bucket_ttl_s: float = 3600.0,
    ) -> None:
        self._tiers: Dict[PriorityTier, TierConfig] = (
            dict(tier_configs) if tier_configs else dict(_DEFAULT_TIERS)
        )
        self._default_tier = default_tier
        self._bucket_ttl = bucket_ttl_s
        self._cleanup_interval = cleanup_interval_s

        self._system_load: float = 0.0  # 0.0 - 1.0
        self._lock = threading.Lock()

        # client_id -> (PriorityTier, _TokenBucket)
        self._buckets: Dict[str, Tuple[PriorityTier, _TokenBucket]] = {}

        # Client-to-tier mapping (explicitly registered)
        self._client_tiers: Dict[str, PriorityTier] = {}

        self._last_cleanup = time.monotonic()

        logger.info(
            "AdaptiveRateLimiter initialised (default_tier=%s)",
            default_tier.value,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_rate_limit(
        self,
        client_id: str,
        cost: float = 1.0,
    ) -> RateLimitResult:
        """Evaluate whether *client_id* may proceed.

        Parameters
        ----------
        client_id:
            Unique identifier for the calling client or API key.
        cost:
            Number of tokens this request costs (default 1).

        Returns
        -------
        RateLimitResult
            Includes the decision, remaining tokens, and suggested
            retry-after when throttled.
        """
        with self._lock:
            self._maybe_cleanup()

            tier, bucket = self._get_or_create_bucket(client_id)
            tier_cfg = self._tiers[tier]
            refill_rate = tier_cfg.effective_refill(self._system_load)

            bucket.refill(refill_rate)

            if bucket.try_consume(cost):
                return RateLimitResult(
                    decision=RateLimitDecision.ALLOWED,
                    client_id=client_id,
                    tier=tier.value,
                    tokens_remaining=bucket.tokens,
                    current_refill_rate=refill_rate,
                    message="Request allowed",
                )

            # Not enough tokens -- decide between throttle and reject.
            retry_after = cost / max(refill_rate, 0.001)

            if tier == PriorityTier.BEST_EFFORT and self._system_load > 0.9:
                bucket.total_rejected += 1
                return RateLimitResult(
                    decision=RateLimitDecision.REJECTED,
                    client_id=client_id,
                    tier=tier.value,
                    tokens_remaining=bucket.tokens,
                    retry_after_seconds=retry_after,
                    current_refill_rate=refill_rate,
                    message="System under heavy load -- best-effort requests rejected",
                )

            bucket.total_throttled += 1
            return RateLimitResult(
                decision=RateLimitDecision.THROTTLED,
                client_id=client_id,
                tier=tier.value,
                tokens_remaining=bucket.tokens,
                retry_after_seconds=retry_after,
                current_refill_rate=refill_rate,
                message=f"Rate limit exceeded; retry after {retry_after:.2f}s",
            )

    def get_current_limits(self) -> Dict[str, Dict[str, Any]]:
        """Return effective limits for each tier under current system load."""
        result: Dict[str, Dict[str, Any]] = {}
        for tier, cfg in self._tiers.items():
            refill = cfg.effective_refill(self._system_load)
            result[tier.value] = {
                "max_tokens": cfg.max_tokens,
                "base_refill_rate": cfg.base_refill_rate,
                "effective_refill_rate": refill,
                "min_refill_rate": cfg.min_refill_rate,
                "burst_allowance": cfg.burst_allowance,
                "system_load": self._system_load,
            }
        return result

    def adjust_limits(self, system_load: float) -> None:
        """Update the current system load, which drives adaptive refill rates.

        Parameters
        ----------
        system_load:
            A value between 0.0 (idle) and 1.0 (saturated).
        """
        clamped = max(0.0, min(1.0, system_load))
        with self._lock:
            prev = self._system_load
            self._system_load = clamped
        if abs(clamped - prev) > 0.1:
            logger.info(
                "System load updated: %.2f -> %.2f", prev, clamped
            )

    def get_client_usage(self, client_id: str) -> Optional[ClientUsage]:
        """Return usage statistics for a specific client.

        Returns *None* if the client has no active bucket.
        """
        with self._lock:
            entry = self._buckets.get(client_id)
            if entry is None:
                return None
            tier, bucket = entry
            cfg = self._tiers[tier]
            return ClientUsage(
                client_id=client_id,
                tier=tier.value,
                tokens_remaining=bucket.tokens,
                max_tokens=bucket.max_tokens,
                total_allowed=bucket.total_allowed,
                total_throttled=bucket.total_throttled,
                total_rejected=bucket.total_rejected,
                current_refill_rate=cfg.effective_refill(self._system_load),
            )

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def register_client(self, client_id: str, tier: PriorityTier) -> None:
        """Explicitly assign a priority tier to a client."""
        with self._lock:
            self._client_tiers[client_id] = tier
            # Reset bucket if tier changed
            if client_id in self._buckets:
                old_tier, _ = self._buckets[client_id]
                if old_tier != tier:
                    del self._buckets[client_id]
        logger.info("Client %s registered as %s", client_id, tier.value)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create_bucket(
        self, client_id: str
    ) -> Tuple[PriorityTier, _TokenBucket]:
        """Return the token bucket for *client_id*, creating if needed.

        Must be called with ``self._lock`` held.
        """
        if client_id in self._buckets:
            return self._buckets[client_id]

        tier = self._client_tiers.get(client_id, self._default_tier)
        cfg = self._tiers[tier]
        bucket = _TokenBucket(tokens=cfg.max_tokens, max_tokens=cfg.max_tokens)
        self._buckets[client_id] = (tier, bucket)
        return tier, bucket

    def _maybe_cleanup(self) -> None:
        """Evict idle buckets if the cleanup interval has elapsed.

        Must be called with ``self._lock`` held.
        """
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        cutoff = now - self._bucket_ttl
        stale = [
            cid
            for cid, (_, bucket) in self._buckets.items()
            if bucket.last_refill < cutoff
        ]
        for cid in stale:
            del self._buckets[cid]
        if stale:
            logger.debug("Evicted %d stale rate-limit buckets", len(stale))
