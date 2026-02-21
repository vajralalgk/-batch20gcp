"""Tail-latency tracking with rolling-window percentile computation.

Maintains multiple time-based windows (5-minute, 1-hour, 24-hour) of
inference latency samples so the platform can detect SLA violations
early and break down latency into constituent phases.
"""

from __future__ import annotations

import bisect
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# SLA thresholds (milliseconds)
_SLA_P95_MS = 200.0
_SLA_P99_MS = 250.0

# Window durations (seconds)
_WINDOW_5M = 300
_WINDOW_1H = 3_600
_WINDOW_24H = 86_400


class LatencyPhase(str, Enum):
    """Named phases that make up end-to-end inference latency."""

    NETWORK = "network"
    QUEUE = "queue"
    INFERENCE = "inference"
    POSTPROCESSING = "postprocessing"


@dataclass
class SLAViolation:
    """Describes a detected SLA threshold breach."""

    percentile: int            # e.g. 95, 99
    threshold_ms: float
    actual_ms: float
    window_name: str
    timestamp: float = field(default_factory=time.time)

    @property
    def exceeded_by_ms(self) -> float:
        return self.actual_ms - self.threshold_ms


@dataclass
class LatencyBreakdown:
    """Per-phase breakdown of a single request's latency."""

    network_ms: float = 0.0
    queue_ms: float = 0.0
    inference_ms: float = 0.0
    postprocessing_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return (
            self.network_ms
            + self.queue_ms
            + self.inference_ms
            + self.postprocessing_ms
        )


@dataclass
class HistogramBucket:
    """A single bucket in a latency histogram."""

    lower_ms: float
    upper_ms: float
    count: int


class _RollingWindow:
    """Thread-safe fixed-duration rolling window of timestamped values."""

    def __init__(self, duration_seconds: int, name: str) -> None:
        self.duration = duration_seconds
        self.name = name
        self._samples: Deque[Tuple[float, float]] = deque()  # (timestamp, value)
        self._lock = threading.Lock()

    def add(self, value: float, timestamp: Optional[float] = None) -> None:
        ts = timestamp or time.time()
        with self._lock:
            self._samples.append((ts, value))
            self._evict(ts)

    def values(self) -> List[float]:
        """Return all values currently within the window, sorted."""
        now = time.time()
        with self._lock:
            self._evict(now)
            vals = [v for _, v in self._samples]
        vals.sort()
        return vals

    def count(self) -> int:
        now = time.time()
        with self._lock:
            self._evict(now)
            return len(self._samples)

    def _evict(self, now: float) -> None:
        cutoff = now - self.duration
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


class LatencyTracker:
    """Track inference latencies across multiple rolling windows.

    Parameters
    ----------
    sla_p95_ms:
        p95 latency SLA threshold in milliseconds (default 200).
    sla_p99_ms:
        p99 latency SLA threshold in milliseconds (default 250).
    histogram_buckets_ms:
        Bucket boundaries for the latency histogram (default covers
        0 - 500 ms in 25 ms increments, then coarser up to 2 000 ms).
    """

    def __init__(
        self,
        sla_p95_ms: float = _SLA_P95_MS,
        sla_p99_ms: float = _SLA_P99_MS,
        histogram_buckets_ms: Optional[List[float]] = None,
    ) -> None:
        self._sla_p95 = sla_p95_ms
        self._sla_p99 = sla_p99_ms

        # Rolling windows
        self._windows: Dict[str, _RollingWindow] = {
            "5m": _RollingWindow(_WINDOW_5M, "5m"),
            "1h": _RollingWindow(_WINDOW_1H, "1h"),
            "24h": _RollingWindow(_WINDOW_24H, "24h"),
        }

        # Phase-level tracking (latest N breakdowns)
        self._breakdowns: Deque[LatencyBreakdown] = deque(maxlen=10_000)
        self._breakdown_lock = threading.Lock()

        # Histogram bucket boundaries
        if histogram_buckets_ms is None:
            # 0-500 ms in 25 ms steps, then 500-2000 in 250 ms steps
            self._bucket_boundaries: List[float] = (
                [i * 25.0 for i in range(21)]
                + [750.0, 1000.0, 1500.0, 2000.0]
            )
        else:
            self._bucket_boundaries = sorted(histogram_buckets_ms)

        logger.info(
            "LatencyTracker initialised (sla_p95=%.0fms, sla_p99=%.0fms)",
            self._sla_p95,
            self._sla_p99,
        )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        latency_ms: float,
        breakdown: Optional[LatencyBreakdown] = None,
    ) -> None:
        """Record a latency observation (in milliseconds).

        Parameters
        ----------
        latency_ms:
            End-to-end latency in milliseconds.
        breakdown:
            Optional per-phase breakdown for root-cause analysis.
        """
        now = time.time()
        for window in self._windows.values():
            window.add(latency_ms, now)

        if breakdown is not None:
            with self._breakdown_lock:
                self._breakdowns.append(breakdown)

    # ------------------------------------------------------------------
    # Percentile queries
    # ------------------------------------------------------------------

    def get_percentile(self, p: float, window: str = "5m") -> float:
        """Compute the *p*-th percentile (0-100) of latencies in *window*.

        Returns ``0.0`` if no samples exist.
        """
        vals = self._windows[window].values()
        if not vals:
            return 0.0
        return self._percentile(vals, p)

    def get_p95(self, window: str = "5m") -> float:
        """Shortcut for the 95th percentile."""
        return self.get_percentile(95.0, window)

    def get_p99(self, window: str = "5m") -> float:
        """Shortcut for the 99th percentile."""
        return self.get_percentile(99.0, window)

    # ------------------------------------------------------------------
    # Histogram
    # ------------------------------------------------------------------

    def get_histogram(self, window: str = "5m") -> List[HistogramBucket]:
        """Build a histogram from the samples in the given window.

        Returns a list of :class:`HistogramBucket` instances.
        """
        vals = self._windows[window].values()
        boundaries = self._bucket_boundaries
        buckets: List[HistogramBucket] = []

        prev = 0.0
        for upper in boundaries:
            lo_idx = bisect.bisect_left(vals, prev)
            hi_idx = bisect.bisect_right(vals, upper)
            buckets.append(HistogramBucket(lower_ms=prev, upper_ms=upper, count=hi_idx - lo_idx))
            prev = upper

        # Overflow bucket
        lo_idx = bisect.bisect_left(vals, prev)
        if lo_idx < len(vals):
            buckets.append(
                HistogramBucket(lower_ms=prev, upper_ms=math.inf, count=len(vals) - lo_idx)
            )

        return buckets

    # ------------------------------------------------------------------
    # SLA
    # ------------------------------------------------------------------

    def check_sla_violations(self) -> List[SLAViolation]:
        """Check all windows for SLA threshold breaches.

        Returns a list of :class:`SLAViolation` instances, which may be
        empty when all percentiles are within acceptable bounds.
        """
        violations: List[SLAViolation] = []
        now = time.time()

        for window_name, window in self._windows.items():
            vals = window.values()
            if not vals:
                continue

            p95 = self._percentile(vals, 95.0)
            if p95 > self._sla_p95:
                violations.append(
                    SLAViolation(
                        percentile=95,
                        threshold_ms=self._sla_p95,
                        actual_ms=p95,
                        window_name=window_name,
                        timestamp=now,
                    )
                )

            p99 = self._percentile(vals, 99.0)
            if p99 > self._sla_p99:
                violations.append(
                    SLAViolation(
                        percentile=99,
                        threshold_ms=self._sla_p99,
                        actual_ms=p99,
                        window_name=window_name,
                        timestamp=now,
                    )
                )

        return violations

    # ------------------------------------------------------------------
    # Breakdown analysis
    # ------------------------------------------------------------------

    def get_phase_averages(self) -> Dict[str, float]:
        """Return mean latency per phase across retained breakdowns.

        Returns a dict mapping :class:`LatencyPhase` values to average
        milliseconds.
        """
        with self._breakdown_lock:
            if not self._breakdowns:
                return {p.value: 0.0 for p in LatencyPhase}

            totals = {p.value: 0.0 for p in LatencyPhase}
            count = len(self._breakdowns)
            for bd in self._breakdowns:
                totals[LatencyPhase.NETWORK.value] += bd.network_ms
                totals[LatencyPhase.QUEUE.value] += bd.queue_ms
                totals[LatencyPhase.INFERENCE.value] += bd.inference_ms
                totals[LatencyPhase.POSTPROCESSING.value] += bd.postprocessing_ms

        return {k: v / count for k, v in totals.items()}

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def reset_window(self, window: str = "5m") -> None:
        """Clear all samples from the specified window.

        Useful for benchmarking or after a deployment where historical
        data is no longer representative.
        """
        if window not in self._windows:
            raise ValueError(
                f"Unknown window '{window}'. Valid: {list(self._windows)}"
            )
        w = self._windows[window]
        with w._lock:
            w._samples.clear()
        logger.info("Latency window '%s' reset", window)

    def get_window_names(self) -> List[str]:
        """Return the names of all available rolling windows."""
        return list(self._windows.keys())

    def get_sample_count(self, window: str = "5m") -> int:
        """Return the number of samples in a window."""
        return self._windows[window].count()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(sorted_values: List[float], p: float) -> float:
        """Compute percentile using the *nearest-rank* method.

        ``sorted_values`` **must** already be sorted in ascending order.
        ``p`` is expressed as a percentage (0 - 100).
        """
        if not sorted_values:
            return 0.0
        n = len(sorted_values)
        rank = (p / 100.0) * (n - 1)
        lower = int(math.floor(rank))
        upper = min(lower + 1, n - 1)
        weight = rank - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
