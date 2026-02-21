"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
GPU Memory Pool — Allocation, Fragmentation, and OOM Prevention
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    GPU memory is the scarcest resource in LLM inference. On a single
    NVIDIA A100-80GB serving a 13B model, the memory budget is:
    - Model weights (fp16): ~26GB (or ~7GB per GPU with TP=4)
    - KV cache: ~40-60GB (scales with concurrent requests)
    - Activations: ~2-4GB (transient, per-batch)
    - CUDA overhead: ~1-2GB (driver, context, cuBLAS workspace)

    Without a managed memory pool, the PyTorch/CUDA default allocator
    leads to fragmentation under dynamic workloads, eventually causing
    OOM errors even when aggregate free memory should be sufficient.

DESIGN DECISIONS:
    - Slab allocation: Pre-allocates large memory slabs and suballocates
      from them, reducing CUDA malloc overhead and fragmentation.
    - Memory pressure tiers: WARNING (80%), CRITICAL (90%), OOM_IMMINENT (95%)
      trigger progressively aggressive mitigation actions.
    - Per-device pools: Each GPU has an independent pool to avoid
      cross-device synchronization overhead.
    - Preemptive eviction: When pressure reaches CRITICAL, the pool
      proactively evicts low-priority allocations before OOM occurs.

PERFORMANCE TARGETS:
    - Allocation latency: <100us for cached slabs
    - Fragmentation ratio: <10% under steady-state workload
    - OOM events: 0 (prevented by preemptive eviction)
============================================================================
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class MemoryPressureLevel(Enum):
    """Graduated memory pressure levels with associated actions.

    NORMAL: No action needed. Free memory is sufficient for expected load.
    WARNING: Alert operations team. Begin rejecting low-priority requests.
    CRITICAL: Trigger preemptive eviction. Reject all non-essential allocations.
    OOM_IMMINENT: Emergency eviction of all non-pinned allocations.
    """

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    OOM_IMMINENT = "oom_imminent"


class AllocationPriority(IntEnum):
    """Priority levels for memory allocations.

    Higher priority allocations are evicted last under memory pressure.
    Model weights are CRITICAL because evicting them requires a full
    model reload (~30 seconds), which is unacceptable for real-time serving.
    """

    LOW = 0         # Prefetch buffers, speculative decoding
    NORMAL = 1      # Standard KV cache for inference sessions
    HIGH = 2        # Active generation requests in flight
    CRITICAL = 3    # Model weights, CUDA contexts (never evict)


@dataclass
class MemoryAllocation:
    """Tracks a single memory allocation within the GPU pool.

    Each allocation represents a contiguous region of GPU memory assigned
    to a specific purpose (model weights, KV cache, activations, etc.).

    Attributes:
        allocation_id: Unique identifier for this allocation.
        size_mb: Size of the allocation in megabytes.
        device_id: The GPU device this allocation resides on.
        priority: Eviction priority level.
        purpose: Human-readable description of what this memory is for.
        allocated_at: Wall-clock time of allocation.
        last_accessed: Wall-clock time of most recent access.
        is_pinned: If True, this allocation cannot be evicted.
        slab_id: ID of the memory slab this was suballocated from.
    """

    allocation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    size_mb: float = 0.0
    device_id: int = 0
    priority: AllocationPriority = AllocationPriority.NORMAL
    purpose: str = ""
    allocated_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    is_pinned: bool = False
    slab_id: Optional[str] = None

    def touch(self) -> None:
        """Update last-accessed timestamp."""
        self.last_accessed = time.time()


@dataclass
class MemorySlab:
    """A large pre-allocated block of GPU memory for suballocation.

    Slabs are allocated at pool initialization time and serve as the
    backing store for individual allocations. This avoids frequent
    cudaMalloc/cudaFree calls which are expensive (~100us each).

    Attributes:
        slab_id: Unique identifier.
        total_size_mb: Total size of this slab.
        used_size_mb: Currently allocated portion.
        device_id: GPU device this slab resides on.
    """

    slab_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    total_size_mb: float = 0.0
    used_size_mb: float = 0.0
    device_id: int = 0

    @property
    def free_size_mb(self) -> float:
        """Available space in this slab."""
        return self.total_size_mb - self.used_size_mb

    @property
    def utilization(self) -> float:
        """Utilization ratio of this slab."""
        if self.total_size_mb == 0:
            return 0.0
        return self.used_size_mb / self.total_size_mb


@dataclass
class DevicePoolState:
    """Tracks the memory pool state for a single GPU device.

    Attributes:
        device_id: GPU device index.
        total_memory_mb: Total GPU memory in megabytes.
        pool_size_mb: Size of the managed memory pool (may be less
            than total memory to reserve space for CUDA overhead).
        slabs: Memory slabs allocated on this device.
        allocations: Active allocations on this device.
    """

    device_id: int = 0
    total_memory_mb: float = 0.0
    pool_size_mb: float = 0.0
    slabs: Dict[str, MemorySlab] = field(default_factory=dict)
    allocations: Dict[str, MemoryAllocation] = field(default_factory=dict)

    @property
    def used_mb(self) -> float:
        """Total memory currently allocated."""
        return sum(a.size_mb for a in self.allocations.values())

    @property
    def free_mb(self) -> float:
        """Available memory in the pool."""
        return self.pool_size_mb - self.used_mb

    @property
    def utilization(self) -> float:
        """Pool utilization ratio."""
        if self.pool_size_mb == 0:
            return 0.0
        return self.used_mb / self.pool_size_mb


class GPUMemoryPool:
    """Manages GPU memory allocation across multiple devices.

    Provides a slab-based memory allocator with fragmentation tracking,
    memory pressure detection, and preemptive OOM prevention. Each GPU
    device has an independent pool to avoid cross-device synchronization.

    Thread Safety:
        All public methods are thread-safe. A per-instance lock protects
        the internal state from concurrent access by the inference engine's
        request handler threads.

    Example:
        >>> pool = GPUMemoryPool(
        ...     num_devices=4,
        ...     device_memory_mb={0: 81920, 1: 81920, 2: 81920, 3: 81920},
        ... )
        >>> pool.initialize()
        >>> alloc = pool.allocate(size_mb=1024, device_id=0, purpose="kv_cache")
        >>> stats = pool.get_pool_stats()
        >>> pool.free(alloc.allocation_id)
    """

    # Memory pressure thresholds (fraction of pool capacity)
    WARNING_THRESHOLD: float = 0.80
    CRITICAL_THRESHOLD: float = 0.90
    OOM_IMMINENT_THRESHOLD: float = 0.95

    # Default slab size in MB (2GB slabs balance fragmentation vs. flexibility)
    DEFAULT_SLAB_SIZE_MB: float = 2048.0

    def __init__(
        self,
        num_devices: int = 1,
        device_memory_mb: Optional[Dict[int, float]] = None,
        pool_fraction: float = 0.90,
        slab_size_mb: float = DEFAULT_SLAB_SIZE_MB,
        enable_preemptive_eviction: bool = True,
    ) -> None:
        """Initialize the GPU memory pool manager.

        Args:
            num_devices: Number of GPU devices to manage.
            device_memory_mb: Per-device total memory in MB. If None,
                defaults to 81920 MB (80 GB, A100 capacity) for each device.
            pool_fraction: Fraction of device memory to include in the
                managed pool. The remainder is reserved for CUDA overhead,
                cuBLAS workspaces, and driver state.
            slab_size_mb: Size of pre-allocated memory slabs.
            enable_preemptive_eviction: If True, automatically evict
                low-priority allocations when memory pressure is critical.
        """
        self._num_devices: int = num_devices
        self._pool_fraction: float = pool_fraction
        self._slab_size_mb: float = slab_size_mb
        self._enable_preemptive_eviction: bool = enable_preemptive_eviction

        # Default device memory: A100-80GB
        if device_memory_mb is None:
            device_memory_mb = {i: 81920.0 for i in range(num_devices)}
        self._device_memory_mb: Dict[int, float] = device_memory_mb

        # Per-device pool state
        self._devices: Dict[int, DevicePoolState] = {}

        # Global allocation index for O(1) lookup by allocation_id
        self._allocation_index: Dict[str, int] = {}  # alloc_id -> device_id

        # Eviction history for observability
        self._eviction_history: List[Dict[str, Any]] = []

        # Thread safety
        self._lock: threading.RLock = threading.RLock()

        # Initialized flag
        self._initialized: bool = False

        logger.info(
            "gpu_memory_pool_created",
            extra={
                "num_devices": num_devices,
                "pool_fraction": pool_fraction,
                "slab_size_mb": slab_size_mb,
            },
        )

    def initialize(self) -> None:
        """Initialize memory pools for all configured GPU devices.

        Pre-allocates memory slabs on each device to minimize runtime
        cudaMalloc overhead. Should be called once during application
        startup, before the inference engine begins accepting requests.
        """
        with self._lock:
            if self._initialized:
                logger.warning("gpu_memory_pool_already_initialized")
                return

            for device_id in range(self._num_devices):
                total_mem = self._device_memory_mb.get(device_id, 81920.0)
                pool_size = total_mem * self._pool_fraction

                device_state = DevicePoolState(
                    device_id=device_id,
                    total_memory_mb=total_mem,
                    pool_size_mb=pool_size,
                )

                # Pre-allocate slabs to cover the pool
                num_slabs = int(pool_size / self._slab_size_mb)
                for _ in range(num_slabs):
                    slab = MemorySlab(
                        total_size_mb=self._slab_size_mb,
                        device_id=device_id,
                    )
                    device_state.slabs[slab.slab_id] = slab

                self._devices[device_id] = device_state

                logger.info(
                    "gpu_device_pool_initialized",
                    extra={
                        "device_id": device_id,
                        "total_memory_mb": total_mem,
                        "pool_size_mb": pool_size,
                        "num_slabs": num_slabs,
                        "slab_size_mb": self._slab_size_mb,
                    },
                )

            self._initialized = True

    # ------------------------------------------------------------------
    # Allocation & Deallocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        size_mb: float,
        device_id: Optional[int] = None,
        priority: AllocationPriority = AllocationPriority.NORMAL,
        purpose: str = "",
        pinned: bool = False,
    ) -> Optional[MemoryAllocation]:
        """Allocate GPU memory from the pool.

        Finds a slab with sufficient free space and suballocates from it.
        If no single slab can satisfy the request, attempts to allocate
        across multiple slabs (for non-contiguous-safe allocations like
        KV cache pages).

        If memory pressure is CRITICAL and preemptive eviction is enabled,
        low-priority allocations will be evicted to make room.

        Args:
            size_mb: Amount of memory to allocate in megabytes.
            device_id: Target GPU device. If None, selects the device
                with the most free memory.
            priority: Eviction priority for this allocation.
            purpose: Description of what this memory will be used for.
            pinned: If True, the allocation cannot be evicted.

        Returns:
            A MemoryAllocation object, or None if allocation fails.

        Raises:
            RuntimeError: If the pool has not been initialized.
            ValueError: If size_mb is non-positive.
        """
        if not self._initialized:
            raise RuntimeError(
                "GPUMemoryPool must be initialized before allocating. "
                "Call initialize() first."
            )
        if size_mb <= 0:
            raise ValueError(f"size_mb must be positive, got {size_mb}")

        with self._lock:
            # Select target device
            if device_id is None:
                device_id = self._select_device(size_mb)
            if device_id is None:
                logger.error(
                    "gpu_memory_allocation_failed_no_device",
                    extra={"requested_mb": size_mb},
                )
                return None

            device = self._devices.get(device_id)
            if device is None:
                logger.error(
                    "gpu_memory_allocation_failed_invalid_device",
                    extra={"device_id": device_id},
                )
                return None

            # Check memory pressure before allocating
            pressure = self._get_device_pressure(device_id)
            if pressure == MemoryPressureLevel.OOM_IMMINENT:
                if self._enable_preemptive_eviction:
                    self._emergency_evict(device_id, size_mb)
                else:
                    logger.error(
                        "gpu_memory_oom_imminent",
                        extra={
                            "device_id": device_id,
                            "utilization": device.utilization,
                        },
                    )
                    return None
            elif pressure == MemoryPressureLevel.CRITICAL:
                if self._enable_preemptive_eviction:
                    self._preemptive_evict(device_id, size_mb)

            # Find a slab with enough free space
            target_slab = self._find_slab(device, size_mb)
            if target_slab is None:
                # Try eviction then retry
                if self._enable_preemptive_eviction:
                    self._preemptive_evict(device_id, size_mb)
                    target_slab = self._find_slab(device, size_mb)

                if target_slab is None:
                    logger.error(
                        "gpu_memory_allocation_failed_no_slab",
                        extra={
                            "device_id": device_id,
                            "requested_mb": size_mb,
                            "free_mb": device.free_mb,
                        },
                    )
                    return None

            # Create the allocation
            allocation = MemoryAllocation(
                size_mb=size_mb,
                device_id=device_id,
                priority=priority,
                purpose=purpose,
                is_pinned=pinned,
                slab_id=target_slab.slab_id,
            )

            # Update slab usage
            target_slab.used_size_mb += size_mb

            # Register the allocation
            device.allocations[allocation.allocation_id] = allocation
            self._allocation_index[allocation.allocation_id] = device_id

            # Log pressure warnings
            new_pressure = self._get_device_pressure(device_id)
            if new_pressure != MemoryPressureLevel.NORMAL:
                logger.warning(
                    "gpu_memory_pressure_elevated",
                    extra={
                        "device_id": device_id,
                        "pressure_level": new_pressure.value,
                        "utilization": device.utilization,
                        "purpose": purpose,
                    },
                )

            logger.debug(
                "gpu_memory_allocated",
                extra={
                    "allocation_id": allocation.allocation_id,
                    "size_mb": size_mb,
                    "device_id": device_id,
                    "purpose": purpose,
                    "utilization": device.utilization,
                },
            )

            return allocation

    def free(self, allocation_id: str) -> bool:
        """Free a previously allocated memory region.

        Returns the memory to the slab it was allocated from, making it
        available for future allocations. If the allocation is not found
        (e.g., already freed or invalid ID), returns False.

        Args:
            allocation_id: The unique ID of the allocation to free.

        Returns:
            True if the allocation was found and freed, False otherwise.
        """
        with self._lock:
            device_id = self._allocation_index.pop(allocation_id, None)
            if device_id is None:
                logger.warning(
                    "gpu_memory_free_not_found",
                    extra={"allocation_id": allocation_id},
                )
                return False

            device = self._devices.get(device_id)
            if device is None:
                return False

            allocation = device.allocations.pop(allocation_id, None)
            if allocation is None:
                return False

            # Return memory to the slab
            if allocation.slab_id and allocation.slab_id in device.slabs:
                slab = device.slabs[allocation.slab_id]
                slab.used_size_mb = max(
                    0.0, slab.used_size_mb - allocation.size_mb
                )

            logger.debug(
                "gpu_memory_freed",
                extra={
                    "allocation_id": allocation_id,
                    "size_mb": allocation.size_mb,
                    "device_id": device_id,
                    "purpose": allocation.purpose,
                    "utilization": device.utilization,
                },
            )

            return True

    # ------------------------------------------------------------------
    # Fragmentation & Compaction
    # ------------------------------------------------------------------

    def get_fragmentation_ratio(self, device_id: Optional[int] = None) -> float:
        """Calculate memory fragmentation for a device or the entire pool.

        Fragmentation is measured as the ratio of free memory that cannot
        be used for a "typical" allocation (defined as the median allocation
        size). High fragmentation means many small free gaps exist but no
        large contiguous blocks.

        For simplicity, this implementation approximates fragmentation as:
        1 - (largest_slab_free / total_free), which measures how concentrated
        free memory is in a single slab vs. scattered across many slabs.

        Args:
            device_id: Specific device to check. If None, returns the
                weighted average across all devices.

        Returns:
            Fragmentation ratio between 0.0 (no fragmentation) and
            1.0 (fully fragmented).
        """
        with self._lock:
            if device_id is not None:
                return self._device_fragmentation(device_id)

            # Weighted average across all devices
            total_free = 0.0
            weighted_frag = 0.0
            for did in self._devices:
                device = self._devices[did]
                free = device.free_mb
                frag = self._device_fragmentation(did)
                weighted_frag += frag * free
                total_free += free

            if total_free == 0.0:
                return 0.0
            return weighted_frag / total_free

    def compact(self, device_id: Optional[int] = None) -> Dict[str, Any]:
        """Compact memory on a device to reduce fragmentation.

        Reorganizes slab allocations to consolidate free space into
        fewer, larger contiguous blocks. This operation may briefly
        pause allocations on the target device.

        In a real GPU system, this would involve:
        1. Allocating a new contiguous block
        2. Copying tensor data via cudaMemcpy
        3. Updating all pointers
        4. Freeing the old blocks

        Args:
            device_id: Device to compact. If None, compacts all devices.

        Returns:
            Compaction results including fragmentation before/after.
        """
        with self._lock:
            devices_to_compact = (
                [device_id] if device_id is not None
                else list(self._devices.keys())
            )

            results: Dict[str, Any] = {
                "devices_compacted": [],
                "total_memory_moved_mb": 0.0,
            }

            for did in devices_to_compact:
                device = self._devices.get(did)
                if device is None:
                    continue

                frag_before = self._device_fragmentation(did)

                # Compact: redistribute allocations across slabs to minimize gaps
                # Sort slabs by utilization descending; pack allocations into
                # the most-used slabs first.
                sorted_slabs = sorted(
                    device.slabs.values(),
                    key=lambda s: s.utilization,
                    reverse=True,
                )

                # Collect all allocations and redistribute
                all_allocs = list(device.allocations.values())
                # Sort by size descending for best-fit packing
                all_allocs.sort(key=lambda a: a.size_mb, reverse=True)

                # Reset all slab usage
                memory_moved = 0.0
                for slab in sorted_slabs:
                    slab.used_size_mb = 0.0

                # Repack allocations into slabs
                for alloc in all_allocs:
                    packed = False
                    for slab in sorted_slabs:
                        if slab.free_size_mb >= alloc.size_mb:
                            old_slab_id = alloc.slab_id
                            if old_slab_id != slab.slab_id:
                                memory_moved += alloc.size_mb
                            alloc.slab_id = slab.slab_id
                            slab.used_size_mb += alloc.size_mb
                            packed = True
                            break

                    if not packed:
                        # Should not happen if pool accounting is correct
                        logger.error(
                            "gpu_memory_compact_failed",
                            extra={
                                "device_id": did,
                                "allocation_id": alloc.allocation_id,
                                "size_mb": alloc.size_mb,
                            },
                        )

                frag_after = self._device_fragmentation(did)

                device_result = {
                    "device_id": did,
                    "fragmentation_before": round(frag_before, 4),
                    "fragmentation_after": round(frag_after, 4),
                    "memory_moved_mb": round(memory_moved, 2),
                    "allocations_repacked": len(all_allocs),
                }
                results["devices_compacted"].append(device_result)
                results["total_memory_moved_mb"] += memory_moved

                logger.info(
                    "gpu_memory_compacted",
                    extra=device_result,
                )

            results["total_memory_moved_mb"] = round(
                results["total_memory_moved_mb"], 2
            )
            return results

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_pool_stats(self) -> Dict[str, Any]:
        """Return comprehensive memory pool statistics.

        Provides per-device and aggregate metrics for monitoring dashboards
        and capacity planning. Exported to Prometheus/Grafana.

        Returns:
            Dictionary with pool-wide and per-device statistics.
        """
        with self._lock:
            device_stats = {}
            total_capacity = 0.0
            total_used = 0.0
            total_allocations = 0

            for did, device in self._devices.items():
                pressure = self._get_device_pressure(did)
                d_stats = {
                    "device_id": did,
                    "total_memory_mb": device.total_memory_mb,
                    "pool_size_mb": device.pool_size_mb,
                    "used_mb": round(device.used_mb, 2),
                    "free_mb": round(device.free_mb, 2),
                    "utilization": round(device.utilization, 4),
                    "pressure_level": pressure.value,
                    "num_allocations": len(device.allocations),
                    "num_slabs": len(device.slabs),
                    "fragmentation_ratio": round(
                        self._device_fragmentation(did), 4
                    ),
                    "allocations_by_priority": self._allocation_priority_breakdown(did),
                }
                device_stats[did] = d_stats

                total_capacity += device.pool_size_mb
                total_used += device.used_mb
                total_allocations += len(device.allocations)

            aggregate_utilization = (
                total_used / total_capacity if total_capacity > 0 else 0.0
            )

            return {
                "initialized": self._initialized,
                "num_devices": self._num_devices,
                "total_pool_capacity_mb": round(total_capacity, 2),
                "total_used_mb": round(total_used, 2),
                "total_free_mb": round(total_capacity - total_used, 2),
                "aggregate_utilization": round(aggregate_utilization, 4),
                "total_allocations": total_allocations,
                "aggregate_fragmentation": round(
                    self.get_fragmentation_ratio(), 4
                ),
                "preemptive_eviction_enabled": self._enable_preemptive_eviction,
                "eviction_history_count": len(self._eviction_history),
                "devices": device_stats,
                "thresholds": {
                    "warning": self.WARNING_THRESHOLD,
                    "critical": self.CRITICAL_THRESHOLD,
                    "oom_imminent": self.OOM_IMMINENT_THRESHOLD,
                },
            }

    def get_pressure_level(self, device_id: Optional[int] = None) -> MemoryPressureLevel:
        """Get the current memory pressure level.

        Args:
            device_id: Specific device to check. If None, returns the
                highest pressure level across all devices.

        Returns:
            The current memory pressure level.
        """
        with self._lock:
            if device_id is not None:
                return self._get_device_pressure(device_id)

            worst_pressure = MemoryPressureLevel.NORMAL
            pressure_rank = {
                MemoryPressureLevel.NORMAL: 0,
                MemoryPressureLevel.WARNING: 1,
                MemoryPressureLevel.CRITICAL: 2,
                MemoryPressureLevel.OOM_IMMINENT: 3,
            }
            for did in self._devices:
                p = self._get_device_pressure(did)
                if pressure_rank[p] > pressure_rank[worst_pressure]:
                    worst_pressure = p

            return worst_pressure

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _select_device(self, size_mb: float) -> Optional[int]:
        """Select the best device for a new allocation.

        Strategy: Choose the device with the most free memory that can
        satisfy the request. This balances memory pressure across GPUs.

        Args:
            size_mb: Required allocation size.

        Returns:
            Device ID, or None if no device has sufficient free space.
        """
        best_device: Optional[int] = None
        best_free: float = -1.0

        for did, device in self._devices.items():
            if device.free_mb >= size_mb and device.free_mb > best_free:
                best_free = device.free_mb
                best_device = did

        return best_device

    def _find_slab(
        self, device: DevicePoolState, size_mb: float
    ) -> Optional[MemorySlab]:
        """Find a slab with enough free space for the requested allocation.

        Uses best-fit strategy: selects the slab with the smallest free
        space that still satisfies the request. This minimizes wasted
        space within each slab.

        Args:
            device: The device pool state to search.
            size_mb: Required allocation size.

        Returns:
            A suitable MemorySlab, or None if none can satisfy the request.
        """
        best_slab: Optional[MemorySlab] = None
        best_fit: float = float("inf")

        for slab in device.slabs.values():
            if slab.free_size_mb >= size_mb:
                if slab.free_size_mb < best_fit:
                    best_fit = slab.free_size_mb
                    best_slab = slab

        return best_slab

    def _get_device_pressure(self, device_id: int) -> MemoryPressureLevel:
        """Determine the memory pressure level for a device.

        Args:
            device_id: The GPU device to check.

        Returns:
            The current pressure level based on utilization thresholds.
        """
        device = self._devices.get(device_id)
        if device is None:
            return MemoryPressureLevel.NORMAL

        util = device.utilization

        if util >= self.OOM_IMMINENT_THRESHOLD:
            return MemoryPressureLevel.OOM_IMMINENT
        elif util >= self.CRITICAL_THRESHOLD:
            return MemoryPressureLevel.CRITICAL
        elif util >= self.WARNING_THRESHOLD:
            return MemoryPressureLevel.WARNING
        else:
            return MemoryPressureLevel.NORMAL

    def _device_fragmentation(self, device_id: int) -> float:
        """Calculate fragmentation for a single device.

        Args:
            device_id: The GPU device to analyze.

        Returns:
            Fragmentation ratio between 0.0 and 1.0.
        """
        device = self._devices.get(device_id)
        if device is None:
            return 0.0

        total_free = device.free_mb
        if total_free <= 0:
            return 0.0

        # Find the largest free block (slab with most free space)
        largest_free_slab = max(
            (slab.free_size_mb for slab in device.slabs.values()),
            default=0.0,
        )

        if total_free == 0:
            return 0.0

        fragmentation = 1.0 - (largest_free_slab / total_free)
        return max(0.0, min(1.0, fragmentation))

    def _allocation_priority_breakdown(
        self, device_id: int
    ) -> Dict[str, int]:
        """Count allocations by priority level for a device.

        Args:
            device_id: The GPU device to analyze.

        Returns:
            Dictionary mapping priority names to allocation counts.
        """
        device = self._devices.get(device_id)
        if device is None:
            return {}

        breakdown: Dict[str, int] = {
            "low": 0,
            "normal": 0,
            "high": 0,
            "critical": 0,
        }

        for alloc in device.allocations.values():
            if alloc.priority == AllocationPriority.LOW:
                breakdown["low"] += 1
            elif alloc.priority == AllocationPriority.NORMAL:
                breakdown["normal"] += 1
            elif alloc.priority == AllocationPriority.HIGH:
                breakdown["high"] += 1
            elif alloc.priority == AllocationPriority.CRITICAL:
                breakdown["critical"] += 1

        return breakdown

    def _preemptive_evict(self, device_id: int, needed_mb: float) -> float:
        """Evict low-priority allocations to free memory.

        Evicts allocations in priority order (LOW first, then NORMAL)
        until the requested amount of memory is freed or no more
        evictable allocations remain.

        Args:
            device_id: Device to evict from.
            needed_mb: Amount of memory to reclaim.

        Returns:
            Total MB actually reclaimed.
        """
        device = self._devices.get(device_id)
        if device is None:
            return 0.0

        reclaimed = 0.0

        # Sort allocations by priority ascending, then by last_accessed ascending
        eviction_candidates = sorted(
            [
                a for a in device.allocations.values()
                if not a.is_pinned and a.priority < AllocationPriority.CRITICAL
            ],
            key=lambda a: (a.priority, a.last_accessed),
        )

        evicted_ids: List[str] = []

        for alloc in eviction_candidates:
            if reclaimed >= needed_mb:
                break

            evicted_ids.append(alloc.allocation_id)
            reclaimed += alloc.size_mb

            # Return memory to slab
            if alloc.slab_id and alloc.slab_id in device.slabs:
                slab = device.slabs[alloc.slab_id]
                slab.used_size_mb = max(
                    0.0, slab.used_size_mb - alloc.size_mb
                )

            self._eviction_history.append({
                "timestamp": time.time(),
                "device_id": device_id,
                "allocation_id": alloc.allocation_id,
                "size_mb": alloc.size_mb,
                "priority": alloc.priority.name,
                "purpose": alloc.purpose,
                "reason": "preemptive",
            })

        # Remove evicted allocations
        for alloc_id in evicted_ids:
            device.allocations.pop(alloc_id, None)
            self._allocation_index.pop(alloc_id, None)

        if evicted_ids:
            logger.warning(
                "gpu_memory_preemptive_eviction",
                extra={
                    "device_id": device_id,
                    "evicted_count": len(evicted_ids),
                    "reclaimed_mb": round(reclaimed, 2),
                    "needed_mb": needed_mb,
                    "utilization_after": device.utilization,
                },
            )

        return reclaimed

    def _emergency_evict(self, device_id: int, needed_mb: float) -> float:
        """Emergency eviction: evicts all non-pinned, non-CRITICAL allocations.

        This is the last resort before OOM. It aggressively frees memory
        by evicting all allocations except those that are pinned or have
        CRITICAL priority (model weights).

        Args:
            device_id: Device to perform emergency eviction on.
            needed_mb: Minimum amount of memory to reclaim.

        Returns:
            Total MB reclaimed.
        """
        device = self._devices.get(device_id)
        if device is None:
            return 0.0

        reclaimed = 0.0
        evicted_ids: List[str] = []

        for alloc in list(device.allocations.values()):
            if alloc.is_pinned or alloc.priority >= AllocationPriority.CRITICAL:
                continue

            evicted_ids.append(alloc.allocation_id)
            reclaimed += alloc.size_mb

            if alloc.slab_id and alloc.slab_id in device.slabs:
                slab = device.slabs[alloc.slab_id]
                slab.used_size_mb = max(
                    0.0, slab.used_size_mb - alloc.size_mb
                )

            self._eviction_history.append({
                "timestamp": time.time(),
                "device_id": device_id,
                "allocation_id": alloc.allocation_id,
                "size_mb": alloc.size_mb,
                "priority": alloc.priority.name,
                "purpose": alloc.purpose,
                "reason": "emergency",
            })

        for alloc_id in evicted_ids:
            device.allocations.pop(alloc_id, None)
            self._allocation_index.pop(alloc_id, None)

        logger.critical(
            "gpu_memory_emergency_eviction",
            extra={
                "device_id": device_id,
                "evicted_count": len(evicted_ids),
                "reclaimed_mb": round(reclaimed, 2),
                "utilization_after": device.utilization,
            },
        )

        return reclaimed

    # ------------------------------------------------------------------
    # Dunder Methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        total_used = sum(d.used_mb for d in self._devices.values())
        total_cap = sum(d.pool_size_mb for d in self._devices.values())
        return (
            f"GPUMemoryPool("
            f"devices={self._num_devices}, "
            f"used={total_used:.0f}MB/{total_cap:.0f}MB, "
            f"initialized={self._initialized})"
        )
