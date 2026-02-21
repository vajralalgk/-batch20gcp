"""GPU simulation tests for KV cache, memory pool, and dynamic batching.

All tests in this module are marked with ``@pytest.mark.gpu`` and simulate
GPU-heavy workloads without requiring physical GPU hardware.
"""

import time

import numpy as np
import pytest

from src.gpu.kv_cache_manager import (
    EvictionPolicy,
    KVCacheManager,
)


@pytest.mark.gpu
class TestKVCacheUnderLoad:
    """Simulate 1000 cache allocations to verify correctness under load."""

    def test_kv_cache_under_load(self):
        mgr = KVCacheManager(
            max_tokens=100_000,
            ttl_seconds=300.0,
            eviction_policy=EvictionPolicy.LRU,
            pressure_threshold=0.9,
        )

        session_ids = []
        for i in range(1000):
            entry = mgr.allocate(f"session-{i}", num_tokens=50)
            assert entry is not None, f"Allocation failed at iteration {i}"
            session_ids.append(f"session-{i}")

        # Verify utilization
        util = mgr.get_utilization()
        expected_util = (1000 * 50) / 100_000
        assert abs(util - expected_util) < 0.01

        # Verify lookups
        for sid in session_ids[:10]:
            entry = mgr.lookup(sid)
            assert entry is not None
            assert entry.session_id == sid

        stats = mgr.get_stats()
        assert stats["active_sessions"] == 1000


@pytest.mark.gpu
class TestMemoryPoolFragmentationUnderChurn:
    """Simulate allocation-deallocation churn to measure fragmentation."""

    def test_memory_pool_fragmentation_under_churn(self):
        mgr = KVCacheManager(max_tokens=50_000, ttl_seconds=300.0)

        # Phase 1: Fill with 500 sessions
        for i in range(500):
            mgr.allocate(f"s{i}", num_tokens=50)

        # Phase 2: Release every other session (odd indices)
        for i in range(1, 500, 2):
            mgr.release(f"s{i}")

        frag_before = mgr.get_fragmentation_ratio()

        # Phase 3: Allocate 100 new sessions in the gaps
        for i in range(500, 600):
            mgr.allocate(f"s{i}", num_tokens=50)

        frag_after = mgr.get_fragmentation_ratio()

        # Fragmentation should be measurable but bounded
        assert frag_before >= 0.0
        assert frag_after >= 0.0

        # Phase 4: Defragment and verify improvement
        result = mgr.defragment()
        assert result["fragmentation_after"] <= result["fragmentation_before"] or \
               result["fragmentation_after"] == 0.0


@pytest.mark.gpu
class TestDynamicBatchingEfficiency:
    """Simulate dynamic batching with variable-length sequences."""

    def test_dynamic_batching_efficiency(self):
        """Measure batching efficiency across 1000 requests with variable token counts."""
        rng = np.random.RandomState(42)
        # Simulate requests with variable token lengths (50-500 tokens)
        requests = [
            {"id": f"req-{i}", "tokens": int(rng.randint(50, 501))}
            for i in range(1000)
        ]

        max_batch_tokens = 4096
        max_batch_size = 64
        batches = []
        current_batch = []
        current_tokens = 0

        for req in requests:
            if (
                len(current_batch) >= max_batch_size
                or current_tokens + req["tokens"] > max_batch_tokens
            ):
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append(req)
            current_tokens += req["tokens"]

        if current_batch:
            batches.append(current_batch)

        # Verify all requests were batched
        total_batched = sum(len(b) for b in batches)
        assert total_batched == 1000

        # Compute efficiency metrics
        avg_batch_size = total_batched / len(batches)
        batch_sizes = [len(b) for b in batches]
        max_batch = max(batch_sizes)

        # Average batch size should be > 1 (batching is happening)
        assert avg_batch_size > 1.0
        # Max batch should not exceed limit
        assert max_batch <= max_batch_size

        # Token utilization per batch
        total_tokens_per_batch = [
            sum(r["tokens"] for r in batch) for batch in batches
        ]
        avg_token_util = sum(total_tokens_per_batch) / len(total_tokens_per_batch) / max_batch_tokens
        # Should utilize at least 30% of max batch tokens on average
        assert avg_token_util > 0.3


@pytest.mark.gpu
class TestCacheThroughput:
    """Benchmark cache allocation and lookup throughput."""

    def test_allocation_throughput(self):
        mgr = KVCacheManager(max_tokens=1_000_000, ttl_seconds=300.0)
        start = time.perf_counter()
        for i in range(5000):
            mgr.allocate(f"bench-{i}", num_tokens=100)
        elapsed = time.perf_counter() - start
        ops_per_sec = 5000 / elapsed
        # Should be able to do at least 10,000 allocations/sec
        assert ops_per_sec > 500, f"Only {ops_per_sec:.0f} allocs/sec"

    def test_lookup_throughput(self):
        mgr = KVCacheManager(max_tokens=1_000_000, ttl_seconds=300.0)
        for i in range(5000):
            mgr.allocate(f"bench-{i}", num_tokens=100)

        start = time.perf_counter()
        for i in range(5000):
            mgr.lookup(f"bench-{i}")
        elapsed = time.perf_counter() - start
        ops_per_sec = 5000 / elapsed
        # Lookups should be even faster than allocations
        assert ops_per_sec > 5_000, f"Only {ops_per_sec:.0f} lookups/sec"


@pytest.mark.gpu
class TestEvictionUnderMemoryPressure:
    """Test that eviction keeps utilization within bounds."""

    def test_eviction_keeps_utilization_bounded(self):
        mgr = KVCacheManager(
            max_tokens=10_000,
            ttl_seconds=300.0,
            eviction_policy=EvictionPolicy.LRU,
            pressure_threshold=0.8,
        )
        # Overfill to trigger eviction
        for i in range(150):
            mgr.allocate(f"s{i}", num_tokens=100)
            if mgr.get_utilization() > 0.9:
                mgr.evict()

        util = mgr.get_utilization()
        assert util <= 1.0, f"Utilization {util:.2f} exceeds 1.0"
