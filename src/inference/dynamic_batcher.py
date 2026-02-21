"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Dynamic Batching Engine
============================================================================

Implements adaptive batch-size optimization driven by real-time GPU
utilization and request-queue depth.  Supports configurable timeouts,
priority queuing (premium users are served first), and comprehensive
throughput/latency metrics.

Usage:
    from src.inference.dynamic_batcher import DynamicBatcher

    batcher = DynamicBatcher(max_batch_size=64)
    await batcher.start()
    future = await batcher.add_request(request)
    result = await future
============================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------

class RequestPriority(IntEnum):
    """Request priority tiers (lower numeric value = higher priority)."""
    CRITICAL = 0
    PREMIUM = 1
    STANDARD = 2
    LOW = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class InferenceRequest:
    """A single inference request enqueued for batching.

    Attributes
    ----------
    request_id:
        Unique identifier for tracing.
    input_ids:
        Token IDs, shape ``(seq_len,)`` or ``(1, seq_len)``.
    attention_mask:
        Attention mask of same shape.
    priority:
        Scheduling priority tier.
    user_tier:
        Originating user tier label (e.g. ``"premium"``, ``"free"``).
    max_wait_ms:
        Maximum time this request should wait in the queue before
        being flushed even if the batch is not full.
    metadata:
        Arbitrary caller-supplied metadata forwarded through the pipeline.
    enqueued_at:
        Monotonic timestamp recorded when the request enters the queue.
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    input_ids: Optional[np.ndarray] = None
    attention_mask: Optional[np.ndarray] = None
    priority: RequestPriority = RequestPriority.STANDARD
    user_tier: str = "standard"
    max_wait_ms: float = 50.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class BatchResult:
    """Result of a single batched inference execution."""
    batch_id: str
    request_ids: List[str]
    outputs: Dict[str, np.ndarray]
    batch_size: int
    latency_ms: float
    queue_wait_ms: float


@dataclass
class BatcherStats:
    """Cumulative statistics for the batcher."""
    total_requests: int = 0
    total_batches: int = 0
    total_timeouts: int = 0
    batch_sizes: List[int] = field(default_factory=list)
    wait_times_ms: List[float] = field(default_factory=list)
    inference_latencies_ms: List[float] = field(default_factory=list)
    requests_per_second: float = 0.0
    _start_time: float = field(default_factory=time.monotonic)

    # -- derived helpers ----------------------------------------------------

    @property
    def avg_batch_size(self) -> float:
        return float(np.mean(self.batch_sizes)) if self.batch_sizes else 0.0

    @property
    def avg_wait_time_ms(self) -> float:
        return float(np.mean(self.wait_times_ms)) if self.wait_times_ms else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return float(np.mean(self.inference_latencies_ms)) if self.inference_latencies_ms else 0.0

    @property
    def throughput_rps(self) -> float:
        elapsed = time.monotonic() - self._start_time
        if elapsed <= 0:
            return 0.0
        return self.total_requests / elapsed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "total_batches": self.total_batches,
            "total_timeouts": self.total_timeouts,
            "avg_batch_size": round(self.avg_batch_size, 2),
            "avg_wait_time_ms": round(self.avg_wait_time_ms, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "throughput_rps": round(self.throughput_rps, 2),
            "batch_size_histogram": self._histogram(self.batch_sizes),
        }

    @staticmethod
    def _histogram(values: List[int]) -> Dict[str, int]:
        if not values:
            return {}
        hist: Dict[str, int] = {}
        for v in values:
            key = str(v)
            hist[key] = hist.get(key, 0) + 1
        return hist


# ---------------------------------------------------------------------------
# Internal queue entry (request + its Future)
# ---------------------------------------------------------------------------

@dataclass
class _QueueEntry:
    request: InferenceRequest
    future: asyncio.Future[BatchResult]

    def __lt__(self, other: _QueueEntry) -> bool:  # type: ignore[override]
        """Priority-queue ordering: lower enum value = higher priority,
        then FIFO by enqueue timestamp."""
        if self.request.priority != other.request.priority:
            return self.request.priority < other.request.priority
        return self.request.enqueued_at < other.request.enqueued_at


# ---------------------------------------------------------------------------
# DynamicBatcher
# ---------------------------------------------------------------------------

class DynamicBatcher:
    """Adaptive batch-size optimizer with priority queuing.

    The batcher accumulates incoming inference requests and dispatches
    them in optimally-sized batches to a caller-supplied inference
    function.  Batch size adapts based on current GPU utilization:
    higher utilization leads to smaller batches to avoid OOM, while
    low utilization permits larger batches for throughput.

    Parameters
    ----------
    max_batch_size:
        Hard upper bound on requests per batch.
    min_batch_size:
        Minimum number of requests before dispatching (unless timeout
        fires first).
    max_wait_ms:
        Default maximum time a request waits in the queue.
    target_gpu_utilization:
        GPU utilization percentage the batcher tries to stay under.
    inference_fn:
        Async callable ``(input_ids, attention_mask) -> dict[str, ndarray]``
        invoked for each batch.  When ``None`` a mock function is used.
    enable_priority:
        When ``True`` premium / critical requests are served before
        standard-tier ones.
    """

    def __init__(
        self,
        max_batch_size: int = 64,
        min_batch_size: int = 1,
        max_wait_ms: float = 50.0,
        target_gpu_utilization: float = 80.0,
        inference_fn: Optional[Callable[..., Coroutine[Any, Any, Dict[str, np.ndarray]]]] = None,
        enable_priority: bool = True,
    ) -> None:
        self._max_batch_size = max_batch_size
        self._min_batch_size = min_batch_size
        self._max_wait_ms = max_wait_ms
        self._target_gpu_util = target_gpu_utilization
        self._inference_fn = inference_fn or self._mock_inference
        self._enable_priority = enable_priority

        # Internal priority queue (sorted list, drained on flush)
        self._queue: List[_QueueEntry] = []
        self._queue_lock = asyncio.Lock()

        # Background flush task handle
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._running = False

        # Current GPU utilization estimate (updated externally or via mock)
        self._gpu_utilization: float = 0.0

        # Metrics
        self._stats = BatcherStats()

        logger.info(
            "dynamic_batcher_init",
            extra={
                "max_batch_size": max_batch_size,
                "min_batch_size": min_batch_size,
                "max_wait_ms": max_wait_ms,
                "priority_enabled": enable_priority,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._running = True
        self._stats = BatcherStats()
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("dynamic_batcher_started")

    async def stop(self) -> None:
        """Stop the flush loop and drain remaining requests."""
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # Flush any remaining queued requests
        await self.flush_batch()
        logger.info("dynamic_batcher_stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_request(
        self,
        request: InferenceRequest,
    ) -> asyncio.Future[BatchResult]:
        """Enqueue an inference request and return a Future for its result.

        The future resolves when the request has been included in a batch
        and inference completes.

        Parameters
        ----------
        request:
            The inference request to enqueue.

        Returns
        -------
        asyncio.Future[BatchResult]
            Resolves with the ``BatchResult`` containing this request's
            outputs.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BatchResult] = loop.create_future()

        request.enqueued_at = time.monotonic()

        # Map user tier to priority when not explicitly set
        if request.user_tier == "premium" and request.priority == RequestPriority.STANDARD:
            request.priority = RequestPriority.PREMIUM

        entry = _QueueEntry(request=request, future=future)

        async with self._queue_lock:
            # Insert in sorted order for priority scheduling
            if self._enable_priority:
                self._insert_sorted(entry)
            else:
                self._queue.append(entry)

        self._stats.total_requests += 1

        # If the queue has reached max_batch_size, flush immediately
        if len(self._queue) >= self._max_batch_size:
            asyncio.ensure_future(self.flush_batch())

        return future

    async def flush_batch(self) -> Optional[BatchResult]:
        """Immediately dispatch all queued requests as a single batch.

        Returns ``None`` if the queue is empty.
        """
        async with self._queue_lock:
            if not self._queue:
                return None

            optimal_size = self.get_optimal_batch_size()
            batch_entries = self._queue[:optimal_size]
            self._queue = self._queue[optimal_size:]

        return await self._execute_batch(batch_entries)

    def get_optimal_batch_size(self) -> int:
        """Compute the ideal batch size based on current GPU utilization.

        The algorithm linearly scales batch size from ``max_batch_size``
        (at 0 % GPU util) down to ``min_batch_size`` (at target util).
        Above target utilization the minimum batch size is used.
        """
        utilization = self._gpu_utilization
        if utilization >= self._target_gpu_util:
            return self._min_batch_size

        # Linear interpolation
        scale = 1.0 - (utilization / self._target_gpu_util)
        size_range = self._max_batch_size - self._min_batch_size
        optimal = self._min_batch_size + int(size_range * scale)

        # Clamp and do not exceed queue depth
        optimal = max(self._min_batch_size, min(optimal, self._max_batch_size))
        return min(optimal, len(self._queue)) if self._queue else optimal

    def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of batcher statistics."""
        stats = self._stats.to_dict()
        stats["queue_depth"] = len(self._queue)
        stats["optimal_batch_size"] = self.get_optimal_batch_size()
        stats["gpu_utilization_pct"] = round(self._gpu_utilization, 1)
        stats["running"] = self._running
        return stats

    def update_gpu_utilization(self, utilization_pct: float) -> None:
        """Feed a new GPU utilization reading into the batcher.

        This value is typically obtained from NVML or DCGM and pushed
        periodically by an external monitoring loop.
        """
        self._gpu_utilization = max(0.0, min(100.0, utilization_pct))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _insert_sorted(self, entry: _QueueEntry) -> None:
        """Insert *entry* into ``self._queue`` maintaining sort order."""
        lo, hi = 0, len(self._queue)
        while lo < hi:
            mid = (lo + hi) // 2
            if entry < self._queue[mid]:
                hi = mid
            else:
                lo = mid + 1
        self._queue.insert(lo, entry)

    async def _flush_loop(self) -> None:
        """Background loop that periodically flushes the queue."""
        while self._running:
            try:
                await asyncio.sleep(self._max_wait_ms / 1000.0)

                if not self._queue:
                    continue

                # Check if the oldest entry has exceeded its wait budget
                oldest = self._queue[0]
                wait_ms = (time.monotonic() - oldest.request.enqueued_at) * 1000.0
                if wait_ms >= self._max_wait_ms or len(self._queue) >= self._min_batch_size:
                    await self.flush_batch()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("dynamic_batcher_flush_error", extra={"error": str(exc)})
                await asyncio.sleep(0.01)

    async def _execute_batch(
        self,
        entries: List[_QueueEntry],
    ) -> BatchResult:
        """Stack requests into a batch, run inference, and resolve futures."""
        batch_id = str(uuid.uuid4())[:12]
        batch_size = len(entries)

        # Compute wait time for metrics
        now = time.monotonic()
        wait_times = [(now - e.request.enqueued_at) * 1000.0 for e in entries]
        avg_wait = float(np.mean(wait_times))

        # Pad / stack inputs to uniform shape
        input_ids_list: List[np.ndarray] = []
        mask_list: List[np.ndarray] = []
        max_seq_len = 0

        for entry in entries:
            ids = entry.request.input_ids
            mask = entry.request.attention_mask
            if ids is not None:
                if ids.ndim == 1:
                    ids = ids.reshape(1, -1)
                if mask is not None and mask.ndim == 1:
                    mask = mask.reshape(1, -1)
                max_seq_len = max(max_seq_len, ids.shape[-1])
                input_ids_list.append(ids)
                mask_list.append(mask if mask is not None else np.ones_like(ids))

        if not input_ids_list:
            # Degenerate batch (metadata-only requests)
            empty_result = BatchResult(
                batch_id=batch_id,
                request_ids=[e.request.request_id for e in entries],
                outputs={},
                batch_size=batch_size,
                latency_ms=0.0,
                queue_wait_ms=avg_wait,
            )
            for entry in entries:
                if not entry.future.done():
                    entry.future.set_result(empty_result)
            return empty_result

        # Right-pad to max_seq_len
        padded_ids: List[np.ndarray] = []
        padded_masks: List[np.ndarray] = []
        for ids, mask in zip(input_ids_list, mask_list):
            pad_len = max_seq_len - ids.shape[-1]
            if pad_len > 0:
                ids = np.pad(ids, ((0, 0), (0, pad_len)), constant_values=0)
                mask = np.pad(mask, ((0, 0), (0, pad_len)), constant_values=0)
            padded_ids.append(ids)
            padded_masks.append(mask)

        batched_ids = np.concatenate(padded_ids, axis=0).astype(np.int64)
        batched_mask = np.concatenate(padded_masks, axis=0).astype(np.int64)

        # Run inference
        infer_start = time.perf_counter()
        try:
            outputs = await self._inference_fn(batched_ids, batched_mask)
            latency_ms = (time.perf_counter() - infer_start) * 1000.0
        except Exception as exc:
            # Propagate the error to all waiting futures
            for entry in entries:
                if not entry.future.done():
                    entry.future.set_exception(exc)
            raise

        result = BatchResult(
            batch_id=batch_id,
            request_ids=[e.request.request_id for e in entries],
            outputs=outputs,
            batch_size=batch_size,
            latency_ms=latency_ms,
            queue_wait_ms=avg_wait,
        )

        # Resolve all waiting futures
        for entry in entries:
            if not entry.future.done():
                entry.future.set_result(result)

        # Record metrics (keep bounded history)
        self._stats.total_batches += 1
        self._stats.batch_sizes.append(batch_size)
        self._stats.wait_times_ms.append(avg_wait)
        self._stats.inference_latencies_ms.append(latency_ms)
        self._trim_metrics()

        logger.debug(
            "dynamic_batcher_batch_executed",
            extra={
                "batch_id": batch_id,
                "batch_size": batch_size,
                "latency_ms": round(latency_ms, 2),
                "avg_wait_ms": round(avg_wait, 2),
            },
        )

        return result

    def _trim_metrics(self, max_history: int = 10_000) -> None:
        """Keep metric lists bounded to avoid unbounded memory growth."""
        if len(self._stats.batch_sizes) > max_history:
            half = max_history // 2
            self._stats.batch_sizes = self._stats.batch_sizes[-half:]
            self._stats.wait_times_ms = self._stats.wait_times_ms[-half:]
            self._stats.inference_latencies_ms = self._stats.inference_latencies_ms[-half:]

    # ------------------------------------------------------------------
    # Mock inference fallback
    # ------------------------------------------------------------------

    @staticmethod
    async def _mock_inference(
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Synthetic inference function for development / testing."""
        await asyncio.sleep(0.005 + 0.005 * np.random.random())
        batch_size, seq_len = input_ids.shape
        vocab_size = 32000
        return {
            "logits": np.random.randn(batch_size, seq_len, vocab_size).astype(np.float32),
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    @property
    def is_running(self) -> bool:
        return self._running

    def __repr__(self) -> str:
        return (
            f"DynamicBatcher(max_batch={self._max_batch_size}, "
            f"queue={len(self._queue)}, "
            f"running={self._running})"
        )
