"""GPU-aware autoscaling logic for the inference fleet.

Monitors GPU utilization, request latency, and queue depth to make
scaling decisions. Supports warm pool management and configurable
cool-down periods between scaling events.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ScalingDirection(Enum):
    """Direction of a scaling action."""

    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class ScalingThresholds:
    """Configurable thresholds that trigger scaling events."""

    gpu_utilization_high: float = 0.80
    gpu_utilization_low: float = 0.40
    p95_latency_ms: float = 180.0
    queue_depth: int = 100
    low_utilization_duration_s: float = 600.0  # 10 minutes


@dataclass
class FleetMetrics:
    """Point-in-time snapshot of fleet-wide metrics."""

    gpu_utilization: float = 0.0
    p95_latency_ms: float = 0.0
    queue_depth: int = 0
    active_instances: int = 0
    healthy_instances: int = 0
    warm_pool_size: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScalingDecision:
    """Outcome of a scaling evaluation."""

    direction: ScalingDirection
    count: int
    reason: str
    metrics_snapshot: FleetMetrics
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScalingEvent:
    """Record of a completed scaling action."""

    direction: ScalingDirection
    count: int
    reason: str
    timestamp: float = field(default_factory=time.time)


class GPUAutoscaler:
    """GPU-aware autoscaler for the LLM inference fleet.

    Evaluates fleet metrics against configurable thresholds to decide
    whether to scale up or scale down. Enforces cool-down periods and
    instance count limits to prevent flapping.

    Args:
        min_instances: Minimum number of GPU instances to keep running.
        max_instances: Maximum number of GPU instances allowed.
        cool_down_period_s: Seconds to wait between consecutive scaling events.
        warm_pool_target: Desired number of pre-warmed containers ready for
            rapid scale-up.
        thresholds: Configurable scaling trigger thresholds.
    """

    def __init__(
        self,
        min_instances: int = 2,
        max_instances: int = 50,
        cool_down_period_s: float = 300.0,
        warm_pool_target: int = 3,
        thresholds: Optional[ScalingThresholds] = None,
    ) -> None:
        if min_instances < 1:
            raise ValueError("min_instances must be >= 1")
        if max_instances < min_instances:
            raise ValueError("max_instances must be >= min_instances")

        self._min_instances = min_instances
        self._max_instances = max_instances
        self._cool_down_period_s = cool_down_period_s
        self._warm_pool_target = warm_pool_target
        self._thresholds = thresholds or ScalingThresholds()

        self._active_instances: int = min_instances
        self._healthy_instances: int = min_instances
        self._warm_pool_size: int = warm_pool_target
        self._current_metrics: FleetMetrics = FleetMetrics(
            active_instances=min_instances,
            healthy_instances=min_instances,
            warm_pool_size=warm_pool_target,
        )
        self._scaling_history: list[ScalingEvent] = []
        self._low_utilization_since: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_metrics(self, metrics: FleetMetrics) -> None:
        """Ingest a new fleet metrics snapshot.

        Args:
            metrics: The latest fleet-wide metrics reading.
        """
        metrics.active_instances = self._active_instances
        metrics.healthy_instances = self._healthy_instances
        metrics.warm_pool_size = self._warm_pool_size
        self._current_metrics = metrics

        # Track how long utilization has been below the low threshold.
        if metrics.gpu_utilization < self._thresholds.gpu_utilization_low:
            if self._low_utilization_since is None:
                self._low_utilization_since = metrics.timestamp
        else:
            self._low_utilization_since = None

    def evaluate_scaling(self) -> ScalingDecision:
        """Evaluate current metrics and return a scaling decision.

        The method checks scale-up triggers first (high GPU utilization,
        high latency, deep queue) and then scale-down triggers (sustained
        low utilization). A cool-down guard prevents flapping.

        Returns:
            A ``ScalingDecision`` describing the recommended action.
        """
        metrics = self._current_metrics

        if self._is_in_cool_down():
            return ScalingDecision(
                direction=ScalingDirection.NO_CHANGE,
                count=0,
                reason="Within cool-down period; no scaling action taken.",
                metrics_snapshot=metrics,
            )

        # --- Scale-up triggers (evaluated in priority order) ----------
        if metrics.gpu_utilization > self._thresholds.gpu_utilization_high:
            needed = self._compute_scale_up_count(metrics)
            return ScalingDecision(
                direction=ScalingDirection.SCALE_UP,
                count=needed,
                reason=(
                    f"GPU utilization {metrics.gpu_utilization:.1%} exceeds "
                    f"threshold {self._thresholds.gpu_utilization_high:.0%}."
                ),
                metrics_snapshot=metrics,
            )

        if metrics.p95_latency_ms > self._thresholds.p95_latency_ms:
            needed = self._compute_scale_up_count(metrics)
            return ScalingDecision(
                direction=ScalingDirection.SCALE_UP,
                count=needed,
                reason=(
                    f"p95 latency {metrics.p95_latency_ms:.0f}ms exceeds "
                    f"threshold {self._thresholds.p95_latency_ms:.0f}ms."
                ),
                metrics_snapshot=metrics,
            )

        if metrics.queue_depth > self._thresholds.queue_depth:
            needed = self._compute_scale_up_count(metrics)
            return ScalingDecision(
                direction=ScalingDirection.SCALE_UP,
                count=needed,
                reason=(
                    f"Queue depth {metrics.queue_depth} exceeds "
                    f"threshold {self._thresholds.queue_depth}."
                ),
                metrics_snapshot=metrics,
            )

        # --- Scale-down trigger ---------------------------------------
        if self._low_utilization_since is not None:
            elapsed = time.time() - self._low_utilization_since
            if elapsed >= self._thresholds.low_utilization_duration_s:
                removable = self._compute_scale_down_count(metrics)
                if removable > 0:
                    return ScalingDecision(
                        direction=ScalingDirection.SCALE_DOWN,
                        count=removable,
                        reason=(
                            f"GPU utilization {metrics.gpu_utilization:.1%} below "
                            f"{self._thresholds.gpu_utilization_low:.0%} for "
                            f"{elapsed:.0f}s (threshold "
                            f"{self._thresholds.low_utilization_duration_s:.0f}s)."
                        ),
                        metrics_snapshot=metrics,
                    )

        return ScalingDecision(
            direction=ScalingDirection.NO_CHANGE,
            count=0,
            reason="All metrics within acceptable range.",
            metrics_snapshot=metrics,
        )

    def get_scaling_decision(self) -> ScalingDecision:
        """Convenience wrapper: evaluate and apply the scaling decision.

        Returns:
            The applied ``ScalingDecision``.
        """
        decision = self.evaluate_scaling()
        if decision.direction == ScalingDirection.SCALE_UP:
            self.scale_up(decision.count)
        elif decision.direction == ScalingDirection.SCALE_DOWN:
            self.scale_down(decision.count)
        return decision

    def scale_up(self, count: int) -> int:
        """Add GPU instances to the fleet.

        Instances are first pulled from the warm pool and new ones are
        provisioned only for the remainder. The final count is clamped to
        ``max_instances``.

        Args:
            count: Desired number of instances to add.

        Returns:
            The actual number of instances added.
        """
        if count <= 0:
            return 0

        headroom = self._max_instances - self._active_instances
        actual = min(count, headroom)
        if actual == 0:
            logger.info("Already at max_instances (%d); cannot scale up.", self._max_instances)
            return 0

        from_warm_pool = min(actual, self._warm_pool_size)
        newly_provisioned = actual - from_warm_pool
        self._warm_pool_size -= from_warm_pool

        self._active_instances += actual
        self._healthy_instances += actual

        self._record_event(ScalingDirection.SCALE_UP, actual, from_warm_pool)
        self._replenish_warm_pool()

        logger.info(
            "Scaled UP by %d (warm=%d, new=%d). Active: %d",
            actual,
            from_warm_pool,
            newly_provisioned,
            self._active_instances,
        )
        return actual

    def scale_down(self, count: int) -> int:
        """Remove GPU instances from the fleet.

        The active count will never drop below ``min_instances``.

        Args:
            count: Desired number of instances to remove.

        Returns:
            The actual number of instances removed.
        """
        if count <= 0:
            return 0

        removable = self._active_instances - self._min_instances
        actual = min(count, removable)
        if actual == 0:
            logger.info(
                "Already at min_instances (%d); cannot scale down.",
                self._min_instances,
            )
            return 0

        self._active_instances -= actual
        self._healthy_instances = min(self._healthy_instances, self._active_instances)
        self._low_utilization_since = None

        self._record_event(ScalingDirection.SCALE_DOWN, actual)

        logger.info(
            "Scaled DOWN by %d. Active: %d",
            actual,
            self._active_instances,
        )
        return actual

    def get_fleet_status(self) -> dict[str, object]:
        """Return a summary of the current fleet state.

        Returns:
            A dictionary with fleet health and capacity information.
        """
        return {
            "active_instances": self._active_instances,
            "healthy_instances": self._healthy_instances,
            "warm_pool_size": self._warm_pool_size,
            "min_instances": self._min_instances,
            "max_instances": self._max_instances,
            "headroom": self._max_instances - self._active_instances,
            "gpu_utilization": self._current_metrics.gpu_utilization,
            "p95_latency_ms": self._current_metrics.p95_latency_ms,
            "queue_depth": self._current_metrics.queue_depth,
            "in_cool_down": self._is_in_cool_down(),
            "scaling_history_length": len(self._scaling_history),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_in_cool_down(self) -> bool:
        if not self._scaling_history:
            return False
        last_event = self._scaling_history[-1]
        return (time.time() - last_event.timestamp) < self._cool_down_period_s

    def _compute_scale_up_count(self, metrics: FleetMetrics) -> int:
        """Heuristic: add ~20% of current fleet, at least 1."""
        desired = max(1, int(self._active_instances * 0.2))
        headroom = self._max_instances - self._active_instances
        return min(desired, headroom)

    def _compute_scale_down_count(self, metrics: FleetMetrics) -> int:
        """Heuristic: remove ~10% of current fleet, at least 1."""
        desired = max(1, int(self._active_instances * 0.1))
        removable = self._active_instances - self._min_instances
        return min(desired, removable)

    def _record_event(
        self,
        direction: ScalingDirection,
        count: int,
        from_warm_pool: int = 0,
    ) -> None:
        warm_note = f" ({from_warm_pool} from warm pool)" if from_warm_pool else ""
        self._scaling_history.append(
            ScalingEvent(
                direction=direction,
                count=count,
                reason=f"{direction.value} by {count}{warm_note}",
            )
        )

    def _replenish_warm_pool(self) -> None:
        """Kick off background pre-warming up to the target size."""
        deficit = self._warm_pool_target - self._warm_pool_size
        if deficit > 0:
            logger.debug("Replenishing warm pool: need %d containers.", deficit)
            # In production this would launch async pre-warming jobs.
            # For now we simply set the target; a background loop would
            # increment _warm_pool_size as containers become ready.
