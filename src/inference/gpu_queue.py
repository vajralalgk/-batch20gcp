"""
GPU Queueing Model for Netflix LLM Platform.

Defines how inference requests flow through the system using a Multi-Level
Feedback Queue (MLFQ) with weighted priority scheduling, admission control,
starvation prevention, and preemption support.

Author: Gopi Krishna Vajrala
"""

import time
import uuid
import heapq
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class PriorityLevel(IntEnum):
    """Priority levels for inference requests, ordered highest to lowest."""
    CRITICAL = 0
    PREMIUM = 1
    STANDARD = 2
    BEST_EFFORT = 3


@dataclass
class GPUQueueConfig:
    """Configuration for the GPU queue manager.

    Attributes:
        max_queue_depth: Maximum total number of requests across all queues.
        scheduling_policy: One of 'FIFO', 'priority', or 'weighted_priority'.
        priority_weights: Mapping of priority level names to scheduling weights.
        preemption_enabled: Whether CRITICAL requests can preempt BEST_EFFORT.
        max_wait_ms: Maximum time a request can wait before timing out.
        starvation_prevention_ms: Promote requests waiting longer than this.
    """
    max_queue_depth: int = 256
    scheduling_policy: str = "weighted_priority"  # FIFO, priority, weighted_priority
    priority_weights: dict = field(default_factory=lambda: {
        "CRITICAL": 8,
        "PREMIUM": 4,
        "STANDARD": 2,
        "BEST_EFFORT": 1,
    })
    preemption_enabled: bool = True
    max_wait_ms: float = 100.0  # Max time in queue before timeout
    starvation_prevention_ms: float = 500.0  # Promote starved requests after this


@dataclass
class InferenceRequest:
    """Represents an incoming inference request.

    Attributes:
        request_id: Unique identifier for this request.
        model_name: Name of the model to run inference on.
        priority: Priority level name (CRITICAL, PREMIUM, STANDARD, BEST_EFFORT).
        payload: Arbitrary request payload.
        created_at: Timestamp when the request was created.
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_name: str = ""
    priority: str = "STANDARD"
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class QueuePosition:
    """Returned when a request is successfully enqueued.

    Attributes:
        request_id: ID of the enqueued request.
        position: Position within its priority queue.
        estimated_wait_ms: Estimated time until the request is dequeued.
        priority_level: The effective priority level assigned.
    """
    request_id: str
    position: int
    estimated_wait_ms: float
    priority_level: str


@dataclass
class QueuedRequest:
    """Wrapper around an inference request with queue metadata.

    Attributes:
        request: The original inference request.
        enqueue_time: Monotonic timestamp when the request entered the queue.
        original_priority: The priority the request was submitted with.
        effective_priority: The current priority after potential promotions.
        wait_time_ms: How long the request has been waiting in the queue.
        promoted: Whether the request was promoted due to starvation prevention.
        preempted_by: If preempted, the request_id that caused the preemption.
    """
    request: InferenceRequest
    enqueue_time: float = field(default_factory=time.monotonic)
    original_priority: PriorityLevel = PriorityLevel.STANDARD
    effective_priority: PriorityLevel = PriorityLevel.STANDARD
    wait_time_ms: float = 0.0
    promoted: bool = False
    preempted_by: Optional[str] = None

    def update_wait_time(self) -> float:
        """Recalculate and return current wait time in milliseconds."""
        self.wait_time_ms = (time.monotonic() - self.enqueue_time) * 1000.0
        return self.wait_time_ms

    def __lt__(self, other: "QueuedRequest") -> bool:
        """Comparison for heap ordering: earlier enqueue time wins ties."""
        return self.enqueue_time < other.enqueue_time


@dataclass
class QueueMetrics:
    """Comprehensive statistics about the GPU queue state.

    Attributes:
        total_depth: Total number of requests across all priority queues.
        depth_per_priority: Number of requests in each priority queue.
        avg_wait_ms: Average wait time across all queued requests.
        p99_wait_ms: 99th-percentile wait time across all queued requests.
        timeout_count: Number of requests that timed out since last reset.
        preemption_count: Number of preemptions that occurred since last reset.
        starvation_promotions: Number of starvation promotions since last reset.
        rejected_count: Number of requests rejected due to queue full (503).
        total_enqueued: Total requests enqueued since last reset.
        total_dequeued: Total requests dequeued since last reset.
    """
    total_depth: int = 0
    depth_per_priority: dict = field(default_factory=lambda: {
        "CRITICAL": 0,
        "PREMIUM": 0,
        "STANDARD": 0,
        "BEST_EFFORT": 0,
    })
    avg_wait_ms: float = 0.0
    p99_wait_ms: float = 0.0
    timeout_count: int = 0
    preemption_count: int = 0
    starvation_promotions: int = 0
    rejected_count: int = 0
    total_enqueued: int = 0
    total_dequeued: int = 0


class QueueFullError(Exception):
    """Raised when the queue is at max depth and cannot accept new requests.

    Corresponds to HTTP 503 Service Unavailable with a Retry-After header.
    """

    def __init__(self, retry_after_ms: float = 50.0):
        self.retry_after_ms = retry_after_ms
        super().__init__(
            f"Queue is full. Retry after {retry_after_ms:.0f}ms "
            f"(HTTP 503, Retry-After: {retry_after_ms / 1000.0:.2f}s)"
        )


class RequestTimeoutError(Exception):
    """Raised when a request exceeds the maximum allowed wait time."""

    def __init__(self, request_id: str, wait_time_ms: float, max_wait_ms: float):
        self.request_id = request_id
        self.wait_time_ms = wait_time_ms
        super().__init__(
            f"Request {request_id} timed out after {wait_time_ms:.1f}ms "
            f"(max: {max_wait_ms:.1f}ms)"
        )


class GPUQueueManager:
    """Multi-Level Feedback Queue manager for GPU inference requests.

    Implements a four-level priority queue with weighted scheduling,
    admission control, starvation prevention, and preemption support.
    """

    def __init__(self, config: Optional[GPUQueueConfig] = None):
        self.config = config or GPUQueueConfig()

        # Four priority queues, each a list used as a min-heap by enqueue_time
        self._queues: dict[PriorityLevel, list[QueuedRequest]] = {
            PriorityLevel.CRITICAL: [],
            PriorityLevel.PREMIUM: [],
            PriorityLevel.STANDARD: [],
            PriorityLevel.BEST_EFFORT: [],
        }

        self._lock = threading.Lock()

        # Cumulative counters
        self._timeout_count: int = 0
        self._preemption_count: int = 0
        self._starvation_promotions: int = 0
        self._rejected_count: int = 0
        self._total_enqueued: int = 0
        self._total_dequeued: int = 0

        # Map priority name strings to PriorityLevel enum values
        self._name_to_level: dict[str, PriorityLevel] = {
            "CRITICAL": PriorityLevel.CRITICAL,
            "PREMIUM": PriorityLevel.PREMIUM,
            "STANDARD": PriorityLevel.STANDARD,
            "BEST_EFFORT": PriorityLevel.BEST_EFFORT,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, request: InferenceRequest) -> QueuePosition:
        """Add an inference request to the appropriate priority queue.

        Args:
            request: The inference request to enqueue.

        Returns:
            QueuePosition with the request's position and estimated wait.

        Raises:
            QueueFullError: If total queue depth >= max_queue_depth (HTTP 503).
        """
        priority_level = self._resolve_priority(request.priority)

        with self._lock:
            total_depth = self._total_depth()

            # Admission control: reject when queue is full
            if total_depth >= self.config.max_queue_depth:
                self._rejected_count += 1
                raise QueueFullError(retry_after_ms=self.config.max_wait_ms / 2.0)

            queued = QueuedRequest(
                request=request,
                enqueue_time=time.monotonic(),
                original_priority=priority_level,
                effective_priority=priority_level,
            )

            heapq.heappush(self._queues[priority_level], queued)
            self._total_enqueued += 1

            position = len(self._queues[priority_level])
            estimated_wait = self._estimate_wait(priority_level, position)

            # If CRITICAL and preemption enabled, attempt preemption
            if (
                priority_level == PriorityLevel.CRITICAL
                and self.config.preemption_enabled
            ):
                self._attempt_preemption(queued)

            return QueuePosition(
                request_id=request.request_id,
                position=position,
                estimated_wait_ms=estimated_wait,
                priority_level=priority_level.name,
            )

    def dequeue(self, batch_size: int = 1) -> list[QueuedRequest]:
        """Select up to batch_size requests from the queues for processing.

        Selection strategy depends on the configured scheduling_policy:
          - FIFO: oldest request across all queues
          - priority: drain higher-priority queues first
          - weighted_priority: weighted selection across non-empty queues

        Before dequeuing, starvation prevention and timeout checks run.

        Args:
            batch_size: Maximum number of requests to dequeue.

        Returns:
            A list of QueuedRequest objects ready for GPU execution.
        """
        with self._lock:
            self._run_starvation_prevention()
            self._expire_timed_out_requests()

            results: list[QueuedRequest] = []

            if self.config.scheduling_policy == "FIFO":
                results = self._dequeue_fifo(batch_size)
            elif self.config.scheduling_policy == "priority":
                results = self._dequeue_strict_priority(batch_size)
            else:
                results = self._dequeue_weighted_priority(batch_size)

            for req in results:
                req.update_wait_time()
            self._total_dequeued += len(results)

            return results

    def get_queue_metrics(self) -> QueueMetrics:
        """Compute and return a snapshot of current queue statistics."""
        with self._lock:
            depths = {
                level.name: len(q) for level, q in self._queues.items()
            }
            total = sum(depths.values())

            wait_times: list[float] = []
            now = time.monotonic()
            for q in self._queues.values():
                for req in q:
                    wait_times.append((now - req.enqueue_time) * 1000.0)

            avg_wait = 0.0
            p99_wait = 0.0
            if wait_times:
                wait_times_sorted = sorted(wait_times)
                avg_wait = sum(wait_times_sorted) / len(wait_times_sorted)
                p99_idx = max(0, int(len(wait_times_sorted) * 0.99) - 1)
                p99_wait = wait_times_sorted[p99_idx]

            return QueueMetrics(
                total_depth=total,
                depth_per_priority=depths,
                avg_wait_ms=avg_wait,
                p99_wait_ms=p99_wait,
                timeout_count=self._timeout_count,
                preemption_count=self._preemption_count,
                starvation_promotions=self._starvation_promotions,
                rejected_count=self._rejected_count,
                total_enqueued=self._total_enqueued,
                total_dequeued=self._total_dequeued,
            )

    def total_depth(self) -> int:
        """Return the total number of queued requests (thread-safe)."""
        with self._lock:
            return self._total_depth()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _total_depth(self) -> int:
        """Return total queue depth (caller must hold lock)."""
        return sum(len(q) for q in self._queues.values())

    def _resolve_priority(self, priority_str: str) -> PriorityLevel:
        """Map a priority name string to its PriorityLevel enum value."""
        level = self._name_to_level.get(priority_str.upper())
        if level is None:
            return PriorityLevel.STANDARD
        return level

    def _estimate_wait(self, level: PriorityLevel, position: int) -> float:
        """Estimate wait time in ms based on position and priority weight."""
        weight = self.config.priority_weights.get(level.name, 1)
        base_latency_per_slot = 2.0  # ms per queue slot estimate
        return (position / max(weight, 1)) * base_latency_per_slot

    def _attempt_preemption(self, critical_req: QueuedRequest) -> None:
        """Preempt a BEST_EFFORT request if one is currently queued.

        Removes the oldest BEST_EFFORT request from the queue and marks it
        as preempted.  Caller must hold the lock.
        """
        best_effort_queue = self._queues[PriorityLevel.BEST_EFFORT]
        if best_effort_queue:
            preempted = heapq.heappop(best_effort_queue)
            preempted.preempted_by = critical_req.request.request_id
            self._preemption_count += 1

    def _run_starvation_prevention(self) -> None:
        """Promote requests that have waited beyond the starvation threshold.

        Requests are promoted one priority level up.  CRITICAL requests
        cannot be promoted further.  Caller must hold the lock.
        """
        now = time.monotonic()
        threshold_s = self.config.starvation_prevention_ms / 1000.0

        promotion_pairs = [
            (PriorityLevel.BEST_EFFORT, PriorityLevel.STANDARD),
            (PriorityLevel.STANDARD, PriorityLevel.PREMIUM),
            (PriorityLevel.PREMIUM, PriorityLevel.CRITICAL),
        ]

        for src_level, dst_level in promotion_pairs:
            src_queue = self._queues[src_level]
            to_promote: list[QueuedRequest] = []
            remaining: list[QueuedRequest] = []

            for req in src_queue:
                elapsed = now - req.enqueue_time
                if elapsed >= threshold_s:
                    to_promote.append(req)
                else:
                    remaining.append(req)

            if to_promote:
                # Rebuild source queue from remaining items
                heapq.heapify(remaining)
                self._queues[src_level] = remaining

                for req in to_promote:
                    req.effective_priority = dst_level
                    req.promoted = True
                    heapq.heappush(self._queues[dst_level], req)
                    self._starvation_promotions += 1

    def _expire_timed_out_requests(self) -> None:
        """Remove requests that have exceeded max_wait_ms.

        Caller must hold the lock.
        """
        now = time.monotonic()
        max_wait_s = self.config.max_wait_ms / 1000.0

        for level in PriorityLevel:
            queue = self._queues[level]
            surviving: list[QueuedRequest] = []
            for req in queue:
                if (now - req.enqueue_time) > max_wait_s:
                    self._timeout_count += 1
                else:
                    surviving.append(req)
            if len(surviving) != len(queue):
                heapq.heapify(surviving)
                self._queues[level] = surviving

    def _dequeue_fifo(self, batch_size: int) -> list[QueuedRequest]:
        """Dequeue oldest requests regardless of priority.  Lock must be held."""
        candidates: list[tuple[float, PriorityLevel, QueuedRequest]] = []
        for level, queue in self._queues.items():
            for req in queue:
                candidates.append((req.enqueue_time, level, req))
        candidates.sort(key=lambda c: c[0])

        results: list[QueuedRequest] = []
        removed_ids: set[str] = set()
        for _, _level, req in candidates[:batch_size]:
            results.append(req)
            removed_ids.add(req.request.request_id)

        # Rebuild queues without the dequeued items
        for level in PriorityLevel:
            filtered = [
                r for r in self._queues[level]
                if r.request.request_id not in removed_ids
            ]
            heapq.heapify(filtered)
            self._queues[level] = filtered

        return results

    def _dequeue_strict_priority(self, batch_size: int) -> list[QueuedRequest]:
        """Drain higher-priority queues first.  Lock must be held."""
        results: list[QueuedRequest] = []
        for level in PriorityLevel:
            queue = self._queues[level]
            while queue and len(results) < batch_size:
                results.append(heapq.heappop(queue))
            if len(results) >= batch_size:
                break
        return results

    def _dequeue_weighted_priority(self, batch_size: int) -> list[QueuedRequest]:
        """Dequeue using weighted selection across non-empty queues.

        The number of slots allocated to each priority is proportional to
        its configured weight.  Lock must be held.
        """
        results: list[QueuedRequest] = []
        non_empty = {
            level: q for level, q in self._queues.items() if q
        }
        if not non_empty:
            return results

        total_weight = sum(
            self.config.priority_weights.get(level.name, 1)
            for level in non_empty
        )

        # Allocate slots proportionally
        allocations: dict[PriorityLevel, int] = {}
        remaining_slots = batch_size
        levels_sorted = sorted(non_empty.keys())

        for level in levels_sorted:
            w = self.config.priority_weights.get(level.name, 1)
            share = max(1, int(batch_size * w / total_weight))
            allocations[level] = min(share, len(non_empty[level]), remaining_slots)
            remaining_slots -= allocations[level]
            if remaining_slots <= 0:
                break

        # Distribute any remaining slots to highest-priority non-empty queues
        for level in levels_sorted:
            if remaining_slots <= 0:
                break
            available = len(self._queues[level]) - allocations.get(level, 0)
            take = min(available, remaining_slots)
            if take > 0:
                allocations[level] = allocations.get(level, 0) + take
                remaining_slots -= take

        for level, count in allocations.items():
            queue = self._queues[level]
            for _ in range(count):
                if queue:
                    results.append(heapq.heappop(queue))

        return results

    def reset_counters(self) -> None:
        """Reset all cumulative counters (useful for periodic reporting)."""
        with self._lock:
            self._timeout_count = 0
            self._preemption_count = 0
            self._starvation_promotions = 0
            self._rejected_count = 0
            self._total_enqueued = 0
            self._total_dequeued = 0
