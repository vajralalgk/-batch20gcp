"""Netflix LLM Personalization Platform - Backpressure Handler.

Implements a multi-signal backpressure evaluation system that drives
graceful degradation across the inference pipeline.  The handler
continuously ingests telemetry signals (GPU utilisation, queue depth,
error rates, feature-store latency, regional health) and maps the
aggregate pressure to one of four discrete levels.  Each level unlocks
a progressively more aggressive set of degradation actions designed to
protect service availability while preserving the best possible
experience for premium traffic.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pressure levels
# ---------------------------------------------------------------------------

class SystemPressureLevel(IntEnum):
    """Ordered severity levels for system-wide backpressure.

    Higher ordinals indicate greater urgency.  The numeric value is used
    when comparing levels (e.g. ``level >= SystemPressureLevel.HIGH``).
    """

    NORMAL = 0
    ELEVATED = 1
    HIGH = 2
    CRITICAL = 3


# ---------------------------------------------------------------------------
# Pressure signals
# ---------------------------------------------------------------------------

@dataclass
class PressureSignals:
    """A snapshot of real-time telemetry used by the backpressure evaluator.

    All numeric fields are expected to be non-negative.  ``region_health``
    must be one of ``"healthy"``, ``"degraded"``, or ``"unhealthy"``.
    """

    gpu_utilization: float = 0.0          # 0-100 percentage
    gpu_memory_pct: float = 0.0           # 0-100 percentage
    feature_store_latency_ms: float = 0.0 # p99 latency to feature store
    queue_depth: int = 0                  # pending inference requests
    error_rate_pct: float = 0.0           # 0-100 percentage of failed reqs
    region_health: str = "healthy"        # healthy | degraded | unhealthy


# ---------------------------------------------------------------------------
# Pressure thresholds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _PressureThresholds:
    """Threshold matrix that maps raw signals to pressure levels.

    ELEVATED : GPU util > 75%  OR  queue > 50   OR  error rate > 2%
    HIGH     : GPU util > 85%  OR  queue > 100  OR  error rate > 5%
               OR feature store latency > 100ms
    CRITICAL : GPU util > 95%  OR  GPU memory > 95%  OR  error rate > 10%
    """

    # ELEVATED
    elevated_gpu_util: float = 75.0
    elevated_queue_depth: int = 50
    elevated_error_rate: float = 2.0

    # HIGH
    high_gpu_util: float = 85.0
    high_queue_depth: int = 100
    high_error_rate: float = 5.0
    high_feature_store_latency_ms: float = 100.0

    # CRITICAL
    critical_gpu_util: float = 95.0
    critical_gpu_memory: float = 95.0
    critical_error_rate: float = 10.0


THRESHOLDS = _PressureThresholds()


# ---------------------------------------------------------------------------
# Degradation action
# ---------------------------------------------------------------------------

@dataclass
class DegradationAction:
    """A single remediation action triggered at a given pressure level.

    Attributes:
        action_type: Machine-readable action identifier.
        description: Human-readable explanation of the action.
        priority_affected: Which traffic priority tiers are impacted.
        estimated_recovery_ms: Estimated wall-clock time before the action
            takes effect and relieves pressure.
    """

    action_type: str
    description: str
    priority_affected: List[str] = field(default_factory=list)
    estimated_recovery_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "description": self.description,
            "priority_affected": self.priority_affected,
            "estimated_recovery_ms": self.estimated_recovery_ms,
        }


# ---------------------------------------------------------------------------
# Traffic shedding policy
# ---------------------------------------------------------------------------

class TrafficPriority(IntEnum):
    """Traffic priority tiers used by the shedding policy.

    PREMIUM     - Never shed; always served.
    STANDARD    - Shed only at CRITICAL pressure.
    BEST_EFFORT - Shed at HIGH pressure and above.
    """

    PREMIUM = 0
    STANDARD = 1
    BEST_EFFORT = 2


@dataclass
class TrafficShedPolicy:
    """Decides whether a request should be shed based on its priority tier
    and the current system pressure level.
    """

    _shed_map: Dict[TrafficPriority, SystemPressureLevel] = field(init=False)

    def __post_init__(self) -> None:
        self._shed_map = {
            # PREMIUM is never shed -- we use a sentinel that cannot be reached
            TrafficPriority.PREMIUM: SystemPressureLevel(SystemPressureLevel.CRITICAL + 1)
            if False
            else SystemPressureLevel.CRITICAL,  # placeholder; overridden below
            TrafficPriority.STANDARD: SystemPressureLevel.CRITICAL,
            TrafficPriority.BEST_EFFORT: SystemPressureLevel.HIGH,
        }
        # PREMIUM traffic is *never* shed.  We handle this explicitly in
        # :meth:`should_shed` rather than relying on sentinel values.

    def should_shed(
        self,
        priority: TrafficPriority,
        pressure_level: SystemPressureLevel,
    ) -> bool:
        """Return ``True`` if the request should be rejected (shed).

        Rules:
        - PREMIUM  : never shed.
        - STANDARD : shed when pressure >= CRITICAL.
        - BEST_EFFORT : shed when pressure >= HIGH.
        """
        if priority == TrafficPriority.PREMIUM:
            return False
        min_shed_level = self._shed_map.get(priority)
        if min_shed_level is None:
            return False
        return pressure_level >= min_shed_level

    def get_shed_reason(
        self,
        priority: TrafficPriority,
        pressure_level: SystemPressureLevel,
    ) -> Optional[str]:
        """Return a human-readable explanation if the request would be shed."""
        if not self.should_shed(priority, pressure_level):
            return None
        return (
            f"Traffic with priority {priority.name} is being shed at "
            f"pressure level {pressure_level.name} to protect system stability."
        )


# ---------------------------------------------------------------------------
# Adaptive batch shrink
# ---------------------------------------------------------------------------

class AdaptiveBatchShrink:
    """Dynamically reduces the maximum batch size according to pressure.

    The scaling factors are:
        NORMAL   -> 100% (no reduction)
        ELEVATED -> 75%
        HIGH     -> 50%
        CRITICAL -> minimum (1 or a configured floor)
    """

    _SCALE_FACTORS: Dict[SystemPressureLevel, float] = {
        SystemPressureLevel.NORMAL: 1.0,
        SystemPressureLevel.ELEVATED: 0.75,
        SystemPressureLevel.HIGH: 0.50,
        SystemPressureLevel.CRITICAL: 0.0,  # sentinel -- forces minimum
    }

    def __init__(self, minimum_batch_size: int = 1) -> None:
        self.minimum_batch_size = max(1, minimum_batch_size)

    def calculate_target_batch_size(
        self,
        current_max: int,
        pressure_level: SystemPressureLevel,
    ) -> int:
        """Return the target maximum batch size for the given pressure.

        The result is always at least ``minimum_batch_size``.
        """
        factor = self._SCALE_FACTORS.get(pressure_level, 1.0)
        if pressure_level == SystemPressureLevel.CRITICAL:
            return self.minimum_batch_size
        target = int(math.floor(current_max * factor))
        return max(target, self.minimum_batch_size)


# ---------------------------------------------------------------------------
# Degradation action catalogue
# ---------------------------------------------------------------------------

def _elevated_actions() -> List[DegradationAction]:
    """Actions applied at ELEVATED pressure."""
    return [
        DegradationAction(
            action_type="shrink_batch_75",
            description="Shrink maximum batch size to 75% of configured limit.",
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=500,
        ),
        DegradationAction(
            action_type="disable_explanation_text",
            description=(
                "Disable natural-language explanation generation to reduce "
                "output token count and free decoder capacity."
            ),
            priority_affected=["BEST_EFFORT"],
            estimated_recovery_ms=200,
        ),
        DegradationAction(
            action_type="increase_batching_window",
            description=(
                "Increase the dynamic-batching wait window so that more "
                "requests can be coalesced into fewer, larger kernel launches."
            ),
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=1000,
        ),
    ]


def _high_actions() -> List[DegradationAction]:
    """Actions applied at HIGH pressure (cumulative with ELEVATED)."""
    return _elevated_actions() + [
        DegradationAction(
            action_type="shrink_batch_50",
            description="Shrink maximum batch size to 50% of configured limit.",
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=500,
        ),
        DegradationAction(
            action_type="serve_cached_recommendations",
            description=(
                "Serve pre-computed cached recommendations instead of running "
                "live inference, accepting slightly stale results."
            ),
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=100,
        ),
        DegradationAction(
            action_type="shed_best_effort_traffic",
            description=(
                "Reject all BEST_EFFORT priority requests with HTTP 503 "
                "and a Retry-After header."
            ),
            priority_affected=["BEST_EFFORT"],
            estimated_recovery_ms=0,
        ),
        DegradationAction(
            action_type="trigger_regional_rebalancing",
            description=(
                "Signal the geo-router to shift traffic away from this "
                "region towards healthier regions."
            ),
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=5000,
        ),
    ]


def _critical_actions() -> List[DegradationAction]:
    """Actions applied at CRITICAL pressure (cumulative with HIGH)."""
    return _high_actions() + [
        DegradationAction(
            action_type="shrink_batch_minimum",
            description="Shrink maximum batch size to the absolute minimum (1).",
            priority_affected=["PREMIUM", "STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=300,
        ),
        DegradationAction(
            action_type="serve_embedding_only_fallback",
            description=(
                "Bypass the LLM entirely and serve embedding-similarity-based "
                "results as a lightweight fallback."
            ),
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=50,
        ),
        DegradationAction(
            action_type="shed_non_premium_traffic",
            description=(
                "Reject all non-PREMIUM traffic with HTTP 503 to protect "
                "the remaining capacity for premium subscribers."
            ),
            priority_affected=["STANDARD", "BEST_EFFORT"],
            estimated_recovery_ms=0,
        ),
        DegradationAction(
            action_type="emergency_scale_up",
            description=(
                "Emit an emergency scale-up signal to the autoscaler so that "
                "additional GPU instances are provisioned immediately."
            ),
            priority_affected=[],
            estimated_recovery_ms=120_000,
        ),
    ]


_DEGRADATION_ACTIONS: Dict[SystemPressureLevel, List[DegradationAction]] = {
    SystemPressureLevel.NORMAL: [],
    SystemPressureLevel.ELEVATED: _elevated_actions(),
    SystemPressureLevel.HIGH: _high_actions(),
    SystemPressureLevel.CRITICAL: _critical_actions(),
}


# ---------------------------------------------------------------------------
# Backpressure handler
# ---------------------------------------------------------------------------

class BackpressureHandler:
    """Central controller that evaluates telemetry signals and determines
    the appropriate degradation response.

    Usage::

        handler = BackpressureHandler()
        signals = PressureSignals(gpu_utilization=88.0, queue_depth=120)
        level = handler.evaluate_pressure(signals)
        actions = handler.get_degradation_actions(level)
    """

    def __init__(
        self,
        thresholds: _PressureThresholds | None = None,
        minimum_batch_size: int = 1,
    ) -> None:
        self._thresholds = thresholds or THRESHOLDS
        self._batch_shrinker = AdaptiveBatchShrink(minimum_batch_size=minimum_batch_size)
        self._shed_policy = TrafficShedPolicy()
        self._last_evaluation: Optional[float] = None
        self._last_level: SystemPressureLevel = SystemPressureLevel.NORMAL

    # -- Evaluation ---------------------------------------------------------

    def evaluate_pressure(self, signals: PressureSignals) -> SystemPressureLevel:
        """Map a set of pressure signals to a discrete pressure level.

        The evaluation proceeds from the most severe level downward so
        that the *highest* applicable level is returned.
        """
        t = self._thresholds

        # CRITICAL
        if (
            signals.gpu_utilization > t.critical_gpu_util
            or signals.gpu_memory_pct > t.critical_gpu_memory
            or signals.error_rate_pct > t.critical_error_rate
        ):
            level = SystemPressureLevel.CRITICAL

        # HIGH
        elif (
            signals.gpu_utilization > t.high_gpu_util
            or signals.queue_depth > t.high_queue_depth
            or signals.error_rate_pct > t.high_error_rate
            or signals.feature_store_latency_ms > t.high_feature_store_latency_ms
        ):
            level = SystemPressureLevel.HIGH

        # ELEVATED
        elif (
            signals.gpu_utilization > t.elevated_gpu_util
            or signals.queue_depth > t.elevated_queue_depth
            or signals.error_rate_pct > t.elevated_error_rate
        ):
            level = SystemPressureLevel.ELEVATED

        else:
            level = SystemPressureLevel.NORMAL

        # Factor in region health: bump up one notch if degraded / unhealthy
        if signals.region_health == "unhealthy" and level < SystemPressureLevel.CRITICAL:
            level = SystemPressureLevel(level + 1)
        elif signals.region_health == "degraded" and level < SystemPressureLevel.HIGH:
            level = SystemPressureLevel(min(level + 1, SystemPressureLevel.CRITICAL))

        self._last_evaluation = time.monotonic()
        self._last_level = level

        logger.info(
            "Backpressure evaluation: level=%s gpu_util=%.1f%% gpu_mem=%.1f%% "
            "queue=%d err=%.2f%% fs_lat=%.1fms region=%s",
            level.name,
            signals.gpu_utilization,
            signals.gpu_memory_pct,
            signals.queue_depth,
            signals.error_rate_pct,
            signals.feature_store_latency_ms,
            signals.region_health,
        )

        return level

    # -- Actions ------------------------------------------------------------

    def get_degradation_actions(
        self,
        level: SystemPressureLevel,
    ) -> List[DegradationAction]:
        """Return the ordered list of degradation actions for *level*."""
        return list(_DEGRADATION_ACTIONS.get(level, []))

    # -- Convenience --------------------------------------------------------

    def calculate_target_batch_size(
        self,
        current_max: int,
        pressure_level: SystemPressureLevel,
    ) -> int:
        """Delegate to :class:`AdaptiveBatchShrink`."""
        return self._batch_shrinker.calculate_target_batch_size(
            current_max, pressure_level
        )

    def should_shed(
        self,
        priority: TrafficPriority,
        pressure_level: SystemPressureLevel,
    ) -> bool:
        """Delegate to :class:`TrafficShedPolicy`."""
        return self._shed_policy.should_shed(priority, pressure_level)

    @property
    def last_level(self) -> SystemPressureLevel:
        """Most recently evaluated pressure level."""
        return self._last_level

    @property
    def last_evaluation_time(self) -> Optional[float]:
        """Monotonic timestamp of the most recent evaluation, or ``None``."""
        return self._last_evaluation

    def summary(self, signals: PressureSignals) -> Dict[str, Any]:
        """Return a JSON-serialisable summary of the current pressure state."""
        level = self.evaluate_pressure(signals)
        actions = self.get_degradation_actions(level)
        return {
            "pressure_level": level.name,
            "signals": {
                "gpu_utilization": signals.gpu_utilization,
                "gpu_memory_pct": signals.gpu_memory_pct,
                "feature_store_latency_ms": signals.feature_store_latency_ms,
                "queue_depth": signals.queue_depth,
                "error_rate_pct": signals.error_rate_pct,
                "region_health": signals.region_health,
            },
            "active_actions": [a.to_dict() for a in actions],
            "target_batch_size": self.calculate_target_batch_size(128, level),
        }
