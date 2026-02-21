"""
Adaptive Dynamic Batching Policy Engine for Netflix LLM Platform.

Implements a feedback-driven policy engine that continuously adjusts batch sizes
based on real-time GPU telemetry, latency SLOs, and throughput signals. The engine
maintains a rolling history of decisions and their outcomes to auto-tune its own
thresholds over time, ensuring optimal GPU utilization without breaching latency
targets.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from collections import deque
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Signal & Decision Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BatchPolicySignals:
    """Real-time telemetry signals consumed by the policy engine."""

    gpu_utilization_pct: float      # Overall GPU core utilization (0-100)
    sm_occupancy_pct: float         # Streaming-multiprocessor occupancy (0-100)
    gpu_memory_pct: float           # GPU VRAM usage percentage (0-100)
    queue_depth: int                # Current number of pending requests
    p99_latency_ms: float           # 99th-percentile inference latency
    p95_latency_ms: float           # 95th-percentile inference latency
    current_batch_size: int         # Batch size used for the most recent window
    tokens_per_second: float        # Aggregate decode throughput
    error_rate_pct: float           # Fraction of requests that errored (0-100)


@dataclass(frozen=True)
class BatchPolicyDecision:
    """Outcome produced by a single policy evaluation cycle."""

    target_batch_size: int          # Recommended next batch size
    direction: str                  # "grow" | "shrink" | "hold"
    reason: str                     # Human-readable justification
    confidence: float               # Confidence score in [0.0, 1.0]
    max_wait_ms: float              # Adjusted batching-window duration


# ---------------------------------------------------------------------------
# Rolling History for Trend Analysis
# ---------------------------------------------------------------------------

@dataclass
class BatchSizeHistoryEntry:
    """Single observation stored in the rolling window."""

    timestamp: float
    batch_size: int
    throughput: float               # tokens_per_second at this point
    latency_p99: float              # p99 latency at this point


class BatchSizeHistory:
    """Thread-safe rolling window of recent batch-size observations.

    Keeps at most *max_entries* records and exposes helpers used by the
    feedback loop to correlate batch-size changes with throughput deltas.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._max_entries = max_entries
        self._entries: deque[BatchSizeHistoryEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, batch_size: int, throughput: float, latency_p99: float) -> None:
        """Append a new observation with the current wall-clock timestamp."""
        entry = BatchSizeHistoryEntry(
            timestamp=time.time(),
            batch_size=batch_size,
            throughput=throughput,
            latency_p99=latency_p99,
        )
        with self._lock:
            self._entries.append(entry)

    def recent(self, n: int = 20) -> List[BatchSizeHistoryEntry]:
        """Return the *n* most recent entries (oldest first)."""
        with self._lock:
            items = list(self._entries)
        return items[-n:]

    @property
    def length(self) -> int:
        with self._lock:
            return len(self._entries)

    def throughput_delta(self, window: int = 5) -> Optional[float]:
        """Average throughput change over the last *window* entries.

        Returns ``None`` when insufficient data is available.
        """
        entries = self.recent(window + 1)
        if len(entries) < 2:
            return None
        deltas = [
            entries[i].throughput - entries[i - 1].throughput
            for i in range(1, len(entries))
        ]
        return sum(deltas) / len(deltas)

    def queue_growth_rate(self, signals_history: List[int], interval_s: float = 1.0) -> float:
        """Estimate queue growth rate (requests/second) from a short sequence of
        queue-depth samples separated by *interval_s* seconds each."""
        if len(signals_history) < 2:
            return 0.0
        diffs = [
            (signals_history[i] - signals_history[i - 1]) / interval_s
            for i in range(1, len(signals_history))
        ]
        return sum(diffs) / len(diffs)


# ---------------------------------------------------------------------------
# Adaptive Batch Policy Engine
# ---------------------------------------------------------------------------

class AdaptiveBatchPolicy:
    """Core policy engine that maps telemetry signals to batch-size decisions.

    Parameters
    ----------
    min_batch_size : int
        Hard floor for the batch size (never go below this).
    max_batch_size : int
        Hard ceiling for the batch size.
    latency_target_ms : float
        p99 latency SLO target.
    base_wait_ms : float
        Default maximum batching window before a batch is dispatched.
    """

    # Tunable thresholds (class-level defaults) ---------------------
    GPU_UTIL_GROW_CEIL = 70.0
    SM_OCC_GROW_CEIL = 80.0
    SM_OCC_SHRINK_FLOOR = 90.0
    GPU_MEM_SHRINK_FLOOR = 90.0
    ERROR_RATE_GROW_CEIL = 1.0
    ERROR_RATE_SHRINK_FLOOR = 3.0
    EMERGENCY_LATENCY_MS = 500.0
    EMERGENCY_ERROR_PCT = 10.0
    QUEUE_GROWTH_SHRINK_RATE = 10.0     # req/s

    def __init__(
        self,
        min_batch_size: int = 1,
        max_batch_size: int = 128,
        latency_target_ms: float = 200.0,
        base_wait_ms: float = 25.0,
    ) -> None:
        self.min_batch_size = min_batch_size
        self.max_batch_size = max_batch_size
        self.latency_target_ms = latency_target_ms
        self.base_wait_ms = base_wait_ms

        self.history = BatchSizeHistory()
        self._recent_decisions: deque[Tuple[float, BatchPolicyDecision]] = deque(maxlen=200)
        self._queue_depth_samples: List[int] = []
        self._lock = threading.Lock()

        # Feedback-loop auto-tune state
        self._grow_factor: float = 1.5
        self._shrink_dampener: float = 1.0   # multiplied into shrink ratios

    # ----- public API ------------------------------------------------

    def evaluate(self, signals: BatchPolicySignals) -> BatchPolicyDecision:
        """Evaluate current telemetry and return a batch-size decision."""
        self._record_queue_sample(signals.queue_depth)

        # Emergency mode takes absolute priority
        if signals.p99_latency_ms > self.EMERGENCY_LATENCY_MS or signals.error_rate_pct > self.EMERGENCY_ERROR_PCT:
            decision = self._emergency(signals)
        # Shrink conditions (any one triggers)
        elif self._should_shrink(signals):
            decision = self._shrink(signals)
        # Grow conditions (all must hold)
        elif self._should_grow(signals):
            decision = self._grow(signals)
        else:
            decision = self._hold(signals)

        self._persist(signals, decision)
        return decision

    def get_policy_summary(self) -> dict:
        """Return a snapshot of the current policy state and recent history."""
        recent_decisions = []
        with self._lock:
            for ts, d in list(self._recent_decisions)[-10:]:
                recent_decisions.append({
                    "timestamp": ts,
                    "target_batch_size": d.target_batch_size,
                    "direction": d.direction,
                    "reason": d.reason,
                    "confidence": d.confidence,
                })

        throughput_delta = self.history.throughput_delta()
        recent_history = self.history.recent(10)
        perf_correlation = self._compute_performance_correlation(recent_history)

        return {
            "policy_config": {
                "min_batch_size": self.min_batch_size,
                "max_batch_size": self.max_batch_size,
                "latency_target_ms": self.latency_target_ms,
                "grow_factor": self._grow_factor,
                "shrink_dampener": self._shrink_dampener,
            },
            "recent_decisions": recent_decisions,
            "history_length": self.history.length,
            "throughput_trend": throughput_delta,
            "performance_correlation": perf_correlation,
        }

    # ----- condition checks ------------------------------------------

    def _should_grow(self, s: BatchPolicySignals) -> bool:
        queue_rate = self.history.queue_growth_rate(self._queue_depth_samples)
        return (
            s.gpu_utilization_pct < self.GPU_UTIL_GROW_CEIL
            and queue_rate <= 0       # queue is not growing
            and s.p99_latency_ms < (self.latency_target_ms * 0.9)  # headroom under target
            and s.sm_occupancy_pct < self.SM_OCC_GROW_CEIL
            and s.error_rate_pct < self.ERROR_RATE_GROW_CEIL
        )

    def _should_shrink(self, s: BatchPolicySignals) -> bool:
        queue_rate = self.history.queue_growth_rate(self._queue_depth_samples)
        return (
            s.p99_latency_ms > self.latency_target_ms
            or s.sm_occupancy_pct > self.SM_OCC_SHRINK_FLOOR
            or s.gpu_memory_pct > self.GPU_MEM_SHRINK_FLOOR
            or s.error_rate_pct > self.ERROR_RATE_SHRINK_FLOOR
            or queue_rate > self.QUEUE_GROWTH_SHRINK_RATE
        )

    # ----- decision builders -----------------------------------------

    def _grow(self, s: BatchPolicySignals) -> BatchPolicyDecision:
        raw_target = int(s.current_batch_size * self._grow_factor)
        target = min(raw_target, self.max_batch_size)
        target = max(target, self.min_batch_size)

        headroom_ratio = 1.0 - (s.p99_latency_ms / self.latency_target_ms)
        confidence = min(headroom_ratio, 1.0 - (s.gpu_utilization_pct / 100.0))
        confidence = round(max(0.0, min(confidence, 1.0)), 3)

        wait_ms = self.base_wait_ms * 1.2  # slightly larger window to fill batch

        return BatchPolicyDecision(
            target_batch_size=target,
            direction="grow",
            reason=(
                f"GPU util {s.gpu_utilization_pct:.1f}% < {self.GPU_UTIL_GROW_CEIL}% "
                f"and p99 {s.p99_latency_ms:.1f}ms within headroom"
            ),
            confidence=confidence,
            max_wait_ms=round(wait_ms, 2),
        )

    def _shrink(self, s: BatchPolicySignals) -> BatchPolicyDecision:
        ratio = 1.0
        reasons: List[str] = []
        queue_rate = self.history.queue_growth_rate(self._queue_depth_samples)

        if s.p99_latency_ms > self.latency_target_ms:
            ratio = min(ratio, 0.75)
            reasons.append(f"p99 {s.p99_latency_ms:.1f}ms > {self.latency_target_ms}ms target")
        if s.sm_occupancy_pct > self.SM_OCC_SHRINK_FLOOR:
            ratio = min(ratio, 0.60)
            reasons.append(f"SM occupancy {s.sm_occupancy_pct:.1f}% > {self.SM_OCC_SHRINK_FLOOR}%")
        if s.gpu_memory_pct > self.GPU_MEM_SHRINK_FLOOR:
            ratio = min(ratio, 0.50)
            reasons.append(f"GPU memory {s.gpu_memory_pct:.1f}% > {self.GPU_MEM_SHRINK_FLOOR}%")
        if s.error_rate_pct > self.ERROR_RATE_SHRINK_FLOOR:
            ratio = min(ratio, 0.50)
            reasons.append(f"Error rate {s.error_rate_pct:.1f}% > {self.ERROR_RATE_SHRINK_FLOOR}%")
        if queue_rate > self.QUEUE_GROWTH_SHRINK_RATE:
            ratio = min(ratio, 0.80)
            reasons.append(f"Queue growth {queue_rate:.1f} req/s > {self.QUEUE_GROWTH_SHRINK_RATE}")

        effective_ratio = ratio * self._shrink_dampener
        target = max(int(s.current_batch_size * effective_ratio), self.min_batch_size)

        severity = 1.0 - ratio  # higher severity = more confidence in shrink
        confidence = round(max(0.0, min(severity, 1.0)), 3)

        wait_ms = self.base_wait_ms * 0.8  # tighter window under pressure

        return BatchPolicyDecision(
            target_batch_size=target,
            direction="shrink",
            reason="; ".join(reasons),
            confidence=confidence,
            max_wait_ms=round(wait_ms, 2),
        )

    def _hold(self, s: BatchPolicySignals) -> BatchPolicyDecision:
        return BatchPolicyDecision(
            target_batch_size=s.current_batch_size,
            direction="hold",
            reason="All signals within acceptable ranges",
            confidence=0.8,
            max_wait_ms=self.base_wait_ms,
        )

    def _emergency(self, s: BatchPolicySignals) -> BatchPolicyDecision:
        reasons: List[str] = []
        if s.p99_latency_ms > self.EMERGENCY_LATENCY_MS:
            reasons.append(f"p99 {s.p99_latency_ms:.1f}ms exceeds emergency threshold {self.EMERGENCY_LATENCY_MS}ms")
        if s.error_rate_pct > self.EMERGENCY_ERROR_PCT:
            reasons.append(f"Error rate {s.error_rate_pct:.1f}% exceeds emergency threshold {self.EMERGENCY_ERROR_PCT}%")

        return BatchPolicyDecision(
            target_batch_size=self.min_batch_size,
            direction="shrink",
            reason=f"EMERGENCY: {'; '.join(reasons)} -> reset to min batch size",
            confidence=1.0,
            max_wait_ms=self.base_wait_ms * 0.5,
        )

    # ----- internal helpers ------------------------------------------

    def _record_queue_sample(self, depth: int) -> None:
        self._queue_depth_samples.append(depth)
        if len(self._queue_depth_samples) > 30:
            self._queue_depth_samples = self._queue_depth_samples[-30:]

    def _persist(self, signals: BatchPolicySignals, decision: BatchPolicyDecision) -> None:
        now = time.time()
        self.history.record(
            batch_size=decision.target_batch_size,
            throughput=signals.tokens_per_second,
            latency_p99=signals.p99_latency_ms,
        )
        with self._lock:
            self._recent_decisions.append((now, decision))
        self._feedback_autotune()

    def _feedback_autotune(self) -> None:
        """Adjust internal grow/shrink factors based on observed throughput trend.

        If recent grow decisions consistently improved throughput, become slightly
        more aggressive.  If they hurt throughput, become more conservative.  This
        creates a closed feedback loop that refines the policy over time.
        """
        recent = self.history.recent(10)
        if len(recent) < 6:
            return

        # Pair consecutive entries: did a batch-size increase lead to higher throughput?
        positive_outcomes = 0
        negative_outcomes = 0
        for i in range(1, len(recent)):
            prev, curr = recent[i - 1], recent[i]
            size_delta = curr.batch_size - prev.batch_size
            tput_delta = curr.throughput - prev.throughput
            if size_delta > 0:
                if tput_delta > 0:
                    positive_outcomes += 1
                else:
                    negative_outcomes += 1

        total = positive_outcomes + negative_outcomes
        if total < 3:
            return

        success_ratio = positive_outcomes / total

        # Nudge the grow factor toward being more aggressive on success
        if success_ratio > 0.7:
            self._grow_factor = min(self._grow_factor * 1.02, 2.0)
            self._shrink_dampener = min(self._shrink_dampener * 1.01, 1.2)
        elif success_ratio < 0.3:
            self._grow_factor = max(self._grow_factor * 0.98, 1.1)
            self._shrink_dampener = max(self._shrink_dampener * 0.99, 0.8)

    @staticmethod
    def _compute_performance_correlation(entries: List[BatchSizeHistoryEntry]) -> Optional[float]:
        """Pearson correlation between batch size and throughput over recent entries.

        Returns ``None`` when there are fewer than three entries or zero variance.
        """
        if len(entries) < 3:
            return None

        sizes = [e.batch_size for e in entries]
        tputs = [e.throughput for e in entries]

        n = len(sizes)
        mean_s = sum(sizes) / n
        mean_t = sum(tputs) / n

        cov = sum((s - mean_s) * (t - mean_t) for s, t in zip(sizes, tputs)) / n
        std_s = (sum((s - mean_s) ** 2 for s in sizes) / n) ** 0.5
        std_t = (sum((t - mean_t) ** 2 for t in tputs) / n) ** 0.5

        if std_s == 0 or std_t == 0:
            return None
        return round(cov / (std_s * std_t), 4)
