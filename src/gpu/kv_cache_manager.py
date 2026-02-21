"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
KV Cache Manager — Token-Level Cache Lifecycle Management
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    In transformer-based LLM inference, the Key-Value (KV) cache stores
    intermediate attention states so that previously computed tokens do not
    need to be recomputed on each generation step. For Netflix's real-time
    personalization (e.g., generating per-user content descriptions), the
    KV cache is the single largest consumer of GPU memory.

    On an A100-80GB running a 13B parameter model with TP=4:
    - Model weights: ~7GB per GPU (sharded)
    - KV cache: up to ~60GB per GPU (dominates memory)
    - Activations: ~2-4GB (transient)

    Without intelligent cache management, the system either:
    (a) Runs out of GPU memory (OOM kill), or
    (b) Wastes memory on stale sessions, reducing concurrent request capacity.

DESIGN DECISIONS:
    - Hybrid LRU+TTL eviction: TTL prevents unbounded staleness; LRU
      prioritizes recently active sessions when memory pressure is high.
    - Partition-aware eviction: Each GPU shard maintains its own cache
      partition; eviction decisions consider cross-shard consistency.
    - Background eviction thread: Expired entries are cleaned up
      asynchronously so that inference latency is not impacted.
    - Token-level granularity: Rather than evicting entire sessions,
      we can trim the oldest tokens from long sessions (prefix caching).

PERFORMANCE TARGETS:
    - Cache hit rate: >85% for personalization workloads
    - Eviction latency: <1ms (background, non-blocking)
    - Fragmentation ratio: <15% (defragmentation triggers at 20%)
============================================================================
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EvictionPolicy(Enum):
    """Supported cache eviction strategies.

    LRU: Evicts least-recently-used entries first. Best for workloads with
         strong temporal locality (recent sessions are likely to continue).
    TTL: Evicts entries whose time-to-live has expired. Ensures staleness
         bound regardless of access patterns.
    HYBRID: Combines LRU and TTL — expired entries are evicted first, then
            LRU ordering is used for memory pressure relief. This is the
            default and recommended strategy for Netflix workloads.
    """

    LRU = "lru"
    TTL = "ttl"
    HYBRID = "hybrid"


@dataclass
class CacheEntry:
    """Represents a single session's KV cache allocation.

    Each entry tracks a contiguous block of token slots allocated for one
    inference session. The entry maintains both TTL metadata (for staleness
    eviction) and access timestamps (for LRU ordering).

    Attributes:
        session_id: Unique identifier for the inference session.
        num_tokens: Number of token slots currently occupied.
        allocated_at: Wall-clock time when the entry was created.
        last_accessed: Wall-clock time of the most recent read/write.
        ttl_seconds: Time-to-live in seconds from last access.
        partition_id: GPU shard partition this entry belongs to.
        region: Deployment region for cross-region alignment.
        token_offset: Starting offset in the cache memory block.
        is_pinned: If True, entry is exempt from eviction (system prompts).
    """

    session_id: str
    num_tokens: int
    allocated_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0
    partition_id: int = 0
    region: str = "us-east-1"
    token_offset: int = 0
    is_pinned: bool = False

    @property
    def is_expired(self) -> bool:
        """Check whether this entry has exceeded its TTL.

        The TTL is measured from the last access time, not the allocation
        time. This means actively used sessions will not expire even if
        they have been alive for a long time.
        """
        return (time.time() - self.last_accessed) > self.ttl_seconds

    def touch(self) -> None:
        """Update the last-accessed timestamp to prevent LRU eviction.

        Called on every cache hit to refresh the entry's position in the
        LRU ordering.
        """
        self.last_accessed = time.time()


@dataclass
class EvictionStats:
    """Tracks cumulative eviction statistics for observability.

    These metrics are exported to Prometheus/Grafana for dashboarding
    and alerting. Key SLOs are defined on hit_rate and fragmentation_ratio.

    Attributes:
        total_evictions: Cumulative number of entries evicted.
        ttl_evictions: Evictions triggered by TTL expiration.
        lru_evictions: Evictions triggered by memory pressure (LRU).
        tokens_reclaimed: Total number of token slots freed.
        total_lookups: Total cache lookup attempts.
        cache_hits: Lookups that found a valid entry.
    """

    total_evictions: int = 0
    ttl_evictions: int = 0
    lru_evictions: int = 0
    tokens_reclaimed: int = 0
    total_lookups: int = 0
    cache_hits: int = 0


class KVCacheManager:
    """Manages the KV cache lifecycle for LLM inference sessions.

    This class provides token-level allocation, TTL-based expiration,
    hybrid LRU+TTL eviction, and cross-region shard alignment for the
    KV cache used by the inference engine.

    The manager runs a background thread that periodically scans for
    expired entries and reclaims their token slots. When memory pressure
    exceeds the configured threshold, it additionally evicts entries in
    LRU order until utilization drops below the target.

    Thread Safety:
        All public methods are thread-safe. Internal state is protected
        by a reentrant lock to allow nested calls (e.g., allocate calling
        evict internally).

    Example:
        >>> manager = KVCacheManager(max_tokens=100_000, ttl_seconds=300)
        >>> manager.start()
        >>> entry = manager.allocate("session-abc", num_tokens=2048)
        >>> stats = manager.get_stats()
        >>> manager.stop()
    """

    def __init__(
        self,
        max_tokens: int = 100_000,
        ttl_seconds: float = 300.0,
        eviction_policy: EvictionPolicy = EvictionPolicy.HYBRID,
        num_partitions: int = 4,
        region: str = "us-east-1",
        eviction_interval_seconds: float = 10.0,
        pressure_threshold: float = 0.85,
        defrag_threshold: float = 0.20,
    ) -> None:
        """Initialize the KV cache manager.

        Args:
            max_tokens: Maximum number of token slots across all partitions.
                On an A100-80GB with a 13B model (TP=4), this is typically
                ~100K tokens per GPU shard.
            ttl_seconds: Default time-to-live for cache entries in seconds.
                Set to 300s (5 min) based on Netflix session duration P95.
            eviction_policy: Strategy for selecting entries to evict.
            num_partitions: Number of GPU shard partitions. Matches the
                Tensor Parallelism degree (TP=4 for A100 clusters).
            region: Deployment region identifier for cross-region alignment.
            eviction_interval_seconds: How often the background thread
                scans for expired entries.
            pressure_threshold: Utilization ratio above which LRU eviction
                is triggered (0.0 to 1.0).
            defrag_threshold: Fragmentation ratio above which automatic
                defragmentation is triggered.
        """
        # Capacity configuration
        self._max_tokens: int = max_tokens
        self._ttl_seconds: float = ttl_seconds
        self._eviction_policy: EvictionPolicy = eviction_policy
        self._num_partitions: int = num_partitions
        self._region: str = region
        self._eviction_interval: float = eviction_interval_seconds
        self._pressure_threshold: float = pressure_threshold
        self._defrag_threshold: float = defrag_threshold

        # Cache state: session_id -> CacheEntry
        self._entries: Dict[str, CacheEntry] = {}

        # Partition tracking: partition_id -> set of session_ids
        self._partitions: Dict[int, set] = {
            i: set() for i in range(num_partitions)
        }

        # Token accounting
        self._used_tokens: int = 0

        # Free block list for fragmentation tracking.
        # Each element is (offset, size) representing a contiguous free block.
        self._free_blocks: List[Tuple[int, int]] = [(0, max_tokens)]

        # Eviction statistics
        self._stats: EvictionStats = EvictionStats()

        # Thread safety
        self._lock: threading.RLock = threading.RLock()

        # Background eviction thread
        self._eviction_thread: Optional[threading.Thread] = None
        self._running: bool = False

        logger.info(
            "kv_cache_manager_initialized",
            extra={
                "max_tokens": max_tokens,
                "ttl_seconds": ttl_seconds,
                "eviction_policy": eviction_policy.value,
                "num_partitions": num_partitions,
                "region": region,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background eviction thread.

        The background thread periodically scans the cache for expired
        entries and evicts them. This prevents memory from being consumed
        by stale sessions that are no longer generating tokens.

        Must be called before the inference engine begins processing
        requests. Typically invoked during application startup.
        """
        with self._lock:
            if self._running:
                logger.warning("kv_cache_manager_already_running")
                return

            self._running = True
            self._eviction_thread = threading.Thread(
                target=self._background_eviction_loop,
                name="kv-cache-evictor",
                daemon=True,
            )
            self._eviction_thread.start()
            logger.info("kv_cache_background_evictor_started")

    def stop(self) -> None:
        """Stop the background eviction thread gracefully.

        Signals the background thread to exit and waits for it to
        finish its current eviction cycle. Called during application
        shutdown to ensure clean resource release.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._eviction_thread is not None:
            self._eviction_thread.join(timeout=self._eviction_interval * 2)
            self._eviction_thread = None

        logger.info("kv_cache_background_evictor_stopped")

    # ------------------------------------------------------------------
    # Core Operations
    # ------------------------------------------------------------------

    def allocate(
        self,
        session_id: str,
        num_tokens: int,
        partition_id: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
        pinned: bool = False,
    ) -> Optional[CacheEntry]:
        """Allocate KV cache token slots for an inference session.

        Finds a contiguous free block large enough to hold the requested
        number of tokens. If insufficient space is available, triggers
        eviction to reclaim tokens before retrying.

        Args:
            session_id: Unique identifier for the inference session.
            num_tokens: Number of token slots to allocate. Determined
                by the model's maximum sequence length and current
                generation position.
            partition_id: GPU shard partition to allocate on. If None,
                the partition with the most free space is selected.
            ttl_seconds: Override the default TTL for this entry.
            pinned: If True, the entry will not be evicted (used for
                system prompts that are shared across many sessions).

        Returns:
            The allocated CacheEntry, or None if allocation fails even
            after eviction attempts.

        Raises:
            ValueError: If num_tokens is non-positive or exceeds max capacity.
        """
        if num_tokens <= 0:
            raise ValueError(
                f"num_tokens must be positive, got {num_tokens}"
            )
        if num_tokens > self._max_tokens:
            raise ValueError(
                f"num_tokens ({num_tokens}) exceeds max capacity "
                f"({self._max_tokens})"
            )

        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds

        with self._lock:
            # If the session already has an allocation, extend it
            if session_id in self._entries:
                existing = self._entries[session_id]
                existing.touch()
                additional = num_tokens - existing.num_tokens
                if additional <= 0:
                    # Requested size is within existing allocation
                    return existing
                # Need to grow the allocation
                num_tokens = additional

            # Select the least-loaded partition if none specified
            if partition_id is None:
                partition_id = self._select_partition()

            # Attempt allocation; evict if necessary
            offset = self._find_free_block(num_tokens)
            if offset is None:
                # Trigger eviction to reclaim space
                reclaimed = self._evict_for_space(num_tokens)
                if reclaimed < num_tokens:
                    logger.error(
                        "kv_cache_allocation_failed",
                        extra={
                            "session_id": session_id,
                            "requested_tokens": num_tokens,
                            "reclaimed_tokens": reclaimed,
                            "used_tokens": self._used_tokens,
                            "max_tokens": self._max_tokens,
                        },
                    )
                    return None
                offset = self._find_free_block(num_tokens)
                if offset is None:
                    return None

            # Create or update the cache entry
            if session_id in self._entries:
                entry = self._entries[session_id]
                entry.num_tokens += num_tokens
                entry.touch()
            else:
                entry = CacheEntry(
                    session_id=session_id,
                    num_tokens=num_tokens,
                    ttl_seconds=effective_ttl,
                    partition_id=partition_id,
                    region=self._region,
                    token_offset=offset,
                    is_pinned=pinned,
                )
                self._entries[session_id] = entry
                self._partitions[partition_id].add(session_id)

            self._used_tokens += num_tokens

            logger.debug(
                "kv_cache_allocated",
                extra={
                    "session_id": session_id,
                    "num_tokens": entry.num_tokens,
                    "partition_id": partition_id,
                    "utilization": self.get_utilization(),
                },
            )

            # Check if defragmentation is needed
            if self.get_fragmentation_ratio() > self._defrag_threshold:
                self._schedule_defrag()

            return entry

    def lookup(self, session_id: str) -> Optional[CacheEntry]:
        """Look up a cache entry by session ID.

        Updates access statistics and refreshes the entry's LRU position
        on a successful lookup. Returns None for expired entries (they
        are lazily evicted on access).

        Args:
            session_id: The session to look up.

        Returns:
            The CacheEntry if found and valid, None otherwise.
        """
        with self._lock:
            self._stats.total_lookups += 1

            entry = self._entries.get(session_id)
            if entry is None:
                return None

            # Lazy TTL expiration: evict on access if expired
            if entry.is_expired and not entry.is_pinned:
                self._evict_entry(session_id, reason="ttl_lazy")
                return None

            entry.touch()
            self._stats.cache_hits += 1
            return entry

    def evict(self, max_entries: Optional[int] = None) -> int:
        """Run a manual eviction cycle.

        First evicts all TTL-expired entries. If the eviction policy
        includes LRU and memory pressure exceeds the threshold,
        additionally evicts entries in LRU order.

        Args:
            max_entries: Maximum number of entries to evict in this cycle.
                If None, evicts all expired entries plus enough LRU entries
                to bring utilization below the pressure threshold.

        Returns:
            The number of entries evicted.
        """
        with self._lock:
            evicted_count = 0

            # Phase 1: Evict all TTL-expired entries
            expired_sessions = [
                sid
                for sid, entry in self._entries.items()
                if entry.is_expired and not entry.is_pinned
            ]

            for sid in expired_sessions:
                if max_entries is not None and evicted_count >= max_entries:
                    break
                self._evict_entry(sid, reason="ttl")
                evicted_count += 1

            # Phase 2: LRU eviction if policy permits and pressure is high
            if self._eviction_policy in (
                EvictionPolicy.LRU,
                EvictionPolicy.HYBRID,
            ):
                utilization = self.get_utilization()
                if utilization > self._pressure_threshold:
                    # Sort by last_accessed ascending (oldest first)
                    lru_candidates = sorted(
                        [
                            (sid, entry)
                            for sid, entry in self._entries.items()
                            if not entry.is_pinned
                        ],
                        key=lambda item: item[1].last_accessed,
                    )

                    for sid, _entry in lru_candidates:
                        if self.get_utilization() <= self._pressure_threshold:
                            break
                        if max_entries is not None and evicted_count >= max_entries:
                            break
                        self._evict_entry(sid, reason="lru")
                        evicted_count += 1

            if evicted_count > 0:
                logger.info(
                    "kv_cache_eviction_cycle_completed",
                    extra={
                        "evicted_count": evicted_count,
                        "utilization": self.get_utilization(),
                        "remaining_entries": len(self._entries),
                    },
                )

            return evicted_count

    def release(self, session_id: str) -> bool:
        """Explicitly release a session's cache allocation.

        Called when an inference session completes normally (as opposed to
        being evicted due to pressure or TTL). This is the preferred way
        to free cache space — it avoids the overhead of eviction scanning.

        Args:
            session_id: The session whose cache should be released.

        Returns:
            True if the session was found and released, False otherwise.
        """
        with self._lock:
            if session_id not in self._entries:
                return False
            self._evict_entry(session_id, reason="explicit_release")
            return True

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_utilization(self) -> float:
        """Return the current cache utilization as a ratio (0.0 to 1.0).

        This is the primary metric for capacity planning and autoscaling
        decisions. The inference gateway uses this to route new requests
        away from GPU instances with high cache pressure.

        Returns:
            Ratio of used tokens to max tokens.
        """
        if self._max_tokens == 0:
            return 0.0
        return self._used_tokens / self._max_tokens

    def get_fragmentation_ratio(self) -> float:
        """Calculate the memory fragmentation ratio.

        Fragmentation occurs when token slots are freed non-contiguously,
        leaving gaps that are individually too small for new allocations.
        High fragmentation (>20%) degrades allocation performance and
        wastes effective capacity.

        Returns:
            Ratio of fragmented (unusable) free space to total free space.
            Returns 0.0 if there is no free space.
        """
        with self._lock:
            total_free = self._max_tokens - self._used_tokens
            if total_free <= 0:
                return 0.0

            # The largest free block determines how much is actually usable
            largest_block = max(
                (size for _, size in self._free_blocks),
                default=0,
            )

            # Fragmentation = 1 - (largest_usable_block / total_free)
            if total_free == 0:
                return 0.0
            fragmentation = 1.0 - (largest_block / total_free)
            return max(0.0, min(1.0, fragmentation))

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive cache statistics for monitoring.

        These metrics are exported to the observability pipeline
        (Prometheus -> Grafana) and used for:
        - SLO tracking (hit rate, latency percentiles)
        - Capacity planning (utilization trends)
        - Alerting (fragmentation, eviction rate spikes)

        Returns:
            Dictionary containing all cache metrics.
        """
        with self._lock:
            hit_rate = (
                self._stats.cache_hits / self._stats.total_lookups
                if self._stats.total_lookups > 0
                else 0.0
            )

            partition_stats = {}
            for pid, session_ids in self._partitions.items():
                partition_tokens = sum(
                    self._entries[sid].num_tokens
                    for sid in session_ids
                    if sid in self._entries
                )
                partition_stats[pid] = {
                    "active_sessions": len(session_ids),
                    "used_tokens": partition_tokens,
                }

            return {
                "total_capacity": self._max_tokens,
                "used_capacity": self._used_tokens,
                "free_capacity": self._max_tokens - self._used_tokens,
                "utilization": round(self.get_utilization(), 4),
                "active_sessions": len(self._entries),
                "eviction_count": self._stats.total_evictions,
                "ttl_evictions": self._stats.ttl_evictions,
                "lru_evictions": self._stats.lru_evictions,
                "tokens_reclaimed": self._stats.tokens_reclaimed,
                "hit_rate": round(hit_rate, 4),
                "total_lookups": self._stats.total_lookups,
                "cache_hits": self._stats.cache_hits,
                "fragmentation_ratio": round(
                    self.get_fragmentation_ratio(), 4
                ),
                "num_partitions": self._num_partitions,
                "partition_stats": partition_stats,
                "eviction_policy": self._eviction_policy.value,
                "ttl_seconds": self._ttl_seconds,
                "region": self._region,
                "num_free_blocks": len(self._free_blocks),
                "pressure_threshold": self._pressure_threshold,
                "is_running": self._running,
            }

    def defragment(self) -> Dict[str, Any]:
        """Compact the cache by coalescing free blocks and relocating entries.

        Defragmentation consolidates scattered free token slots into
        contiguous blocks, improving allocation success rates and reducing
        search overhead. This operation is O(n log n) in the number of
        entries and should be called during low-traffic periods.

        In production, this is triggered automatically when fragmentation
        exceeds the configured threshold (default 20%).

        Returns:
            Dictionary with defragmentation results including tokens moved
            and new fragmentation ratio.
        """
        with self._lock:
            before_frag = self.get_fragmentation_ratio()
            before_blocks = len(self._free_blocks)

            # Sort entries by their current offset to enable compaction
            sorted_entries = sorted(
                self._entries.values(),
                key=lambda e: e.token_offset,
            )

            # Compact: reassign offsets contiguously
            current_offset = 0
            tokens_moved = 0

            for entry in sorted_entries:
                if entry.token_offset != current_offset:
                    tokens_moved += entry.num_tokens
                    entry.token_offset = current_offset
                current_offset += entry.num_tokens

            # Rebuild free block list: single contiguous block at the end
            free_space = self._max_tokens - self._used_tokens
            if free_space > 0:
                self._free_blocks = [(current_offset, free_space)]
            else:
                self._free_blocks = []

            after_frag = self.get_fragmentation_ratio()

            result = {
                "tokens_moved": tokens_moved,
                "entries_relocated": sum(
                    1 for _ in sorted_entries if tokens_moved > 0
                ),
                "free_blocks_before": before_blocks,
                "free_blocks_after": len(self._free_blocks),
                "fragmentation_before": round(before_frag, 4),
                "fragmentation_after": round(after_frag, 4),
            }

            logger.info(
                "kv_cache_defragmented",
                extra=result,
            )

            return result

    # ------------------------------------------------------------------
    # Cross-Region Shard Alignment
    # ------------------------------------------------------------------

    def align_with_region(
        self, remote_region: str, remote_session_ids: List[str]
    ) -> Dict[str, Any]:
        """Synchronize cache state with a remote region's shard.

        In a multi-region deployment, users may be routed to different
        regions based on latency. This method ensures that KV cache
        state is aligned between regions so that session handoffs do
        not require full recomputation.

        This does NOT transfer the actual KV tensors — those are too large
        to move across regions in real time. Instead, it synchronizes
        metadata so that the receiving region knows which sessions have
        valid cache state and can prefetch accordingly.

        Args:
            remote_region: The region to align with (e.g., "us-west-2").
            remote_session_ids: Session IDs that have active cache in
                the remote region.

        Returns:
            Alignment summary with counts of shared, local-only, and
            remote-only sessions.
        """
        with self._lock:
            local_ids = set(self._entries.keys())
            remote_ids = set(remote_session_ids)

            shared = local_ids & remote_ids
            local_only = local_ids - remote_ids
            remote_only = remote_ids - local_ids

            result = {
                "local_region": self._region,
                "remote_region": remote_region,
                "shared_sessions": len(shared),
                "local_only_sessions": len(local_only),
                "remote_only_sessions": len(remote_only),
                "alignment_ratio": (
                    len(shared) / len(local_ids) if local_ids else 0.0
                ),
            }

            logger.info(
                "kv_cache_region_alignment",
                extra=result,
            )

            return result

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _select_partition(self) -> int:
        """Select the partition with the most available capacity.

        Uses a simple least-loaded strategy: the partition with the fewest
        active sessions receives the next allocation. This balances memory
        pressure across GPU shards in the Tensor Parallel group.

        Returns:
            The partition ID with the most free capacity.
        """
        min_load = float("inf")
        best_partition = 0

        for pid, session_ids in self._partitions.items():
            partition_tokens = sum(
                self._entries[sid].num_tokens
                for sid in session_ids
                if sid in self._entries
            )
            if partition_tokens < min_load:
                min_load = partition_tokens
                best_partition = pid

        return best_partition

    def _find_free_block(self, num_tokens: int) -> Optional[int]:
        """Find a contiguous free block of the requested size.

        Uses a first-fit strategy: scans the free block list and returns
        the first block large enough. First-fit is O(n) but has lower
        fragmentation than best-fit for our workload pattern.

        Args:
            num_tokens: Number of contiguous token slots needed.

        Returns:
            The starting offset of the allocated block, or None if no
            suitable block exists.
        """
        for i, (offset, size) in enumerate(self._free_blocks):
            if size >= num_tokens:
                # Allocate from the beginning of this block
                if size == num_tokens:
                    # Exact fit: remove the block entirely
                    self._free_blocks.pop(i)
                else:
                    # Partial fit: shrink the block
                    self._free_blocks[i] = (
                        offset + num_tokens,
                        size - num_tokens,
                    )
                return offset

        return None

    def _release_block(self, offset: int, size: int) -> None:
        """Return a block of token slots to the free list.

        Attempts to coalesce with adjacent free blocks to reduce
        fragmentation. Two free blocks that are contiguous in the
        token address space are merged into a single larger block.

        Args:
            offset: Starting offset of the block to free.
            size: Number of token slots in the block.
        """
        # Insert in sorted order by offset
        insert_idx = 0
        for i, (blk_offset, _) in enumerate(self._free_blocks):
            if blk_offset > offset:
                break
            insert_idx = i + 1

        self._free_blocks.insert(insert_idx, (offset, size))

        # Coalesce with the next block if adjacent
        if insert_idx + 1 < len(self._free_blocks):
            curr_offset, curr_size = self._free_blocks[insert_idx]
            next_offset, next_size = self._free_blocks[insert_idx + 1]
            if curr_offset + curr_size == next_offset:
                self._free_blocks[insert_idx] = (
                    curr_offset,
                    curr_size + next_size,
                )
                self._free_blocks.pop(insert_idx + 1)

        # Coalesce with the previous block if adjacent
        if insert_idx > 0:
            prev_offset, prev_size = self._free_blocks[insert_idx - 1]
            curr_offset, curr_size = self._free_blocks[insert_idx]
            if prev_offset + prev_size == curr_offset:
                self._free_blocks[insert_idx - 1] = (
                    prev_offset,
                    prev_size + curr_size,
                )
                self._free_blocks.pop(insert_idx)

    def _evict_entry(self, session_id: str, reason: str = "unknown") -> int:
        """Evict a single cache entry and reclaim its token slots.

        Args:
            session_id: The session to evict.
            reason: Why the entry is being evicted (for logging/metrics).

        Returns:
            Number of tokens reclaimed.
        """
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return 0

        tokens_freed = entry.num_tokens
        self._used_tokens -= tokens_freed
        self._release_block(entry.token_offset, tokens_freed)

        # Update partition tracking
        partition_sessions = self._partitions.get(entry.partition_id)
        if partition_sessions is not None:
            partition_sessions.discard(session_id)

        # Update statistics
        self._stats.total_evictions += 1
        self._stats.tokens_reclaimed += tokens_freed
        if reason == "ttl" or reason == "ttl_lazy":
            self._stats.ttl_evictions += 1
        elif reason == "lru":
            self._stats.lru_evictions += 1

        logger.debug(
            "kv_cache_entry_evicted",
            extra={
                "session_id": session_id,
                "tokens_freed": tokens_freed,
                "reason": reason,
                "utilization": self.get_utilization(),
            },
        )

        return tokens_freed

    def _evict_for_space(self, needed_tokens: int) -> int:
        """Evict entries until enough space is available.

        Uses the configured eviction policy to select victims. Expired
        entries are always evicted first (regardless of policy), followed
        by LRU-ordered entries if the policy permits.

        Args:
            needed_tokens: Number of token slots that must be freed.

        Returns:
            Total number of tokens actually reclaimed.
        """
        reclaimed = 0

        # Phase 1: Evict expired entries
        expired = [
            sid
            for sid, entry in self._entries.items()
            if entry.is_expired and not entry.is_pinned
        ]
        for sid in expired:
            if reclaimed >= needed_tokens:
                break
            reclaimed += self._evict_entry(sid, reason="ttl")

        # Phase 2: LRU eviction if more space is needed
        if reclaimed < needed_tokens and self._eviction_policy in (
            EvictionPolicy.LRU,
            EvictionPolicy.HYBRID,
        ):
            lru_sorted = sorted(
                [
                    (sid, entry)
                    for sid, entry in self._entries.items()
                    if not entry.is_pinned
                ],
                key=lambda item: item[1].last_accessed,
            )
            for sid, _entry in lru_sorted:
                if reclaimed >= needed_tokens:
                    break
                reclaimed += self._evict_entry(sid, reason="lru")

        return reclaimed

    def _schedule_defrag(self) -> None:
        """Schedule an asynchronous defragmentation.

        In production, this would enqueue a defrag task to run during
        the next low-traffic window. For now, it logs a warning.
        """
        logger.warning(
            "kv_cache_defrag_recommended",
            extra={
                "fragmentation_ratio": self.get_fragmentation_ratio(),
                "threshold": self._defrag_threshold,
            },
        )

    def _background_eviction_loop(self) -> None:
        """Background thread that periodically evicts expired entries.

        Runs continuously until stop() is called. Each iteration:
        1. Sleeps for the configured interval
        2. Evicts all TTL-expired entries
        3. If utilization exceeds the pressure threshold, evicts LRU entries
        4. If fragmentation exceeds the threshold, triggers defragmentation

        This thread is daemonic so it will not prevent process shutdown.
        """
        logger.info("kv_cache_eviction_loop_started")

        while self._running:
            time.sleep(self._eviction_interval)

            if not self._running:
                break

            try:
                evicted = self.evict()

                # Auto-defragment if fragmentation is high
                if self.get_fragmentation_ratio() > self._defrag_threshold:
                    self.defragment()

            except Exception:
                logger.exception("kv_cache_eviction_loop_error")

        logger.info("kv_cache_eviction_loop_stopped")

    # ------------------------------------------------------------------
    # Context Manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "KVCacheManager":
        """Support usage as a context manager."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Stop the background thread on context exit."""
        self.stop()

    def __repr__(self) -> str:
        return (
            f"KVCacheManager("
            f"max_tokens={self._max_tokens}, "
            f"used={self._used_tokens}, "
            f"sessions={len(self._entries)}, "
            f"policy={self._eviction_policy.value})"
        )
