"""Inference-aware load balancing across GPU nodes.

Distributes inference requests to GPU nodes based on real-time telemetry
-- GPU utilisation, queue depth, recent latency, and health status -- using
pluggable algorithms (least-loaded, round-robin with GPU awareness,
weighted).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BalancingAlgorithm(str, Enum):
    """Supported load-balancing strategies."""

    LEAST_LOADED = "least_loaded"
    ROUND_ROBIN = "round_robin"
    WEIGHTED = "weighted"


class NodeStatus(str, Enum):
    """Operational status of a GPU inference node."""

    ACTIVE = "active"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


@dataclass
class NodeStats:
    """Live telemetry for a single inference node."""

    gpu_utilization: float = 0.0       # 0-100
    gpu_memory_used_bytes: int = 0
    gpu_memory_total_bytes: int = 0
    queue_depth: int = 0
    avg_latency_ms: float = 0.0
    active_requests: int = 0
    status: NodeStatus = NodeStatus.ACTIVE
    weight: float = 1.0
    last_updated: float = field(default_factory=time.time)

    @property
    def gpu_memory_utilization(self) -> float:
        if self.gpu_memory_total_bytes == 0:
            return 0.0
        return self.gpu_memory_used_bytes / self.gpu_memory_total_bytes


@dataclass
class NodeSelection:
    """The outcome of a load-balancing decision."""

    node_id: str
    algorithm_used: str
    score: float
    reason: str
    node_stats: NodeStats


class InferenceLoadBalancer:
    """GPU-aware load balancer for inference workloads.

    Parameters
    ----------
    algorithm:
        Default balancing strategy (can be overridden per-request).
    gpu_utilization_weight:
        How heavily GPU utilisation factors into the score (default 0.4).
    queue_depth_weight:
        Weight for queue depth in scoring (default 0.3).
    latency_weight:
        Weight for recent average latency (default 0.2).
    health_weight:
        Weight for health status in scoring (default 0.1).
    stale_threshold_s:
        Seconds after which a node's stats are considered stale (default 30).
    """

    def __init__(
        self,
        algorithm: BalancingAlgorithm = BalancingAlgorithm.LEAST_LOADED,
        gpu_utilization_weight: float = 0.4,
        queue_depth_weight: float = 0.3,
        latency_weight: float = 0.2,
        health_weight: float = 0.1,
        stale_threshold_s: float = 30.0,
    ) -> None:
        self._algorithm = algorithm
        self._weights = {
            "gpu": gpu_utilization_weight,
            "queue": queue_depth_weight,
            "latency": latency_weight,
            "health": health_weight,
        }
        self._stale_threshold = stale_threshold_s

        self._nodes: OrderedDict[str, NodeStats] = OrderedDict()
        self._lock = threading.Lock()

        # Round-robin index
        self._rr_index: int = 0

        logger.info(
            "InferenceLoadBalancer initialised (algorithm=%s)", algorithm.value
        )

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, stats: Optional[NodeStats] = None) -> None:
        """Register a new GPU node with optional initial stats."""
        with self._lock:
            if node_id in self._nodes:
                logger.warning("Node %s already registered -- updating", node_id)
            self._nodes[node_id] = stats or NodeStats()
        logger.info("Node %s added to load balancer", node_id)

    def remove_node(self, node_id: str) -> None:
        """De-register a GPU node."""
        with self._lock:
            removed = self._nodes.pop(node_id, None)
        if removed is not None:
            logger.info("Node %s removed from load balancer", node_id)
        else:
            logger.warning("Attempted to remove unknown node %s", node_id)

    def update_node_stats(self, node_id: str, stats: NodeStats) -> None:
        """Push updated telemetry for a node."""
        with self._lock:
            if node_id not in self._nodes:
                logger.warning(
                    "update_node_stats: unknown node %s -- registering", node_id
                )
            stats.last_updated = time.time()
            self._nodes[node_id] = stats

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select_node(
        self,
        request: Optional[Dict[str, Any]] = None,
        algorithm: Optional[BalancingAlgorithm] = None,
    ) -> NodeSelection:
        """Select the best node for an incoming inference request.

        Parameters
        ----------
        request:
            Optional request metadata that may influence routing (e.g.
            required model, batch size hints).
        algorithm:
            Override the default balancing algorithm for this call.

        Raises
        ------
        RuntimeError
            If no routable nodes are available.
        """
        algo = algorithm or self._algorithm

        with self._lock:
            candidates = self._get_routable_nodes()

        if not candidates:
            raise RuntimeError("No routable inference nodes available")

        if algo == BalancingAlgorithm.LEAST_LOADED:
            return self._select_least_loaded(candidates)
        elif algo == BalancingAlgorithm.ROUND_ROBIN:
            return self._select_round_robin(candidates)
        elif algo == BalancingAlgorithm.WEIGHTED:
            return self._select_weighted(candidates)
        else:
            # Fallback
            return self._select_least_loaded(candidates)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_load_distribution(self) -> Dict[str, Dict[str, Any]]:
        """Return current load metrics for every registered node."""
        with self._lock:
            return {
                nid: {
                    "gpu_utilization": s.gpu_utilization,
                    "gpu_memory_utilization": s.gpu_memory_utilization,
                    "queue_depth": s.queue_depth,
                    "avg_latency_ms": s.avg_latency_ms,
                    "active_requests": s.active_requests,
                    "status": s.status.value,
                    "weight": s.weight,
                    "stale": self._is_stale(s),
                }
                for nid, s in self._nodes.items()
            }

    def get_node_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    # ------------------------------------------------------------------
    # Algorithms
    # ------------------------------------------------------------------

    def _select_least_loaded(
        self, candidates: Dict[str, NodeStats]
    ) -> NodeSelection:
        """Pick the node with the lowest composite load score."""
        scored: List[tuple[str, float]] = []
        for nid, stats in candidates.items():
            scored.append((nid, self._compute_score(stats)))

        scored.sort(key=lambda x: x[1])
        best_id, best_score = scored[0]

        return NodeSelection(
            node_id=best_id,
            algorithm_used=BalancingAlgorithm.LEAST_LOADED.value,
            score=best_score,
            reason=f"Lowest composite score ({best_score:.3f})",
            node_stats=candidates[best_id],
        )

    def _select_round_robin(
        self, candidates: Dict[str, NodeStats]
    ) -> NodeSelection:
        """Cycle through nodes in order, skipping overloaded ones."""
        ids = list(candidates.keys())
        n = len(ids)

        # Try up to n candidates starting from current index.
        for i in range(n):
            idx = (self._rr_index + i) % n
            nid = ids[idx]
            stats = candidates[nid]
            if stats.gpu_utilization < 95.0:
                with self._lock:
                    self._rr_index = (idx + 1) % n
                return NodeSelection(
                    node_id=nid,
                    algorithm_used=BalancingAlgorithm.ROUND_ROBIN.value,
                    score=self._compute_score(stats),
                    reason=f"Round-robin index {idx}",
                    node_stats=stats,
                )

        # All candidates are at capacity -- fall back to least-loaded.
        return self._select_least_loaded(candidates)

    def _select_weighted(
        self, candidates: Dict[str, NodeStats]
    ) -> NodeSelection:
        """Select using operator-defined weights combined with load score."""
        scored: List[tuple[str, float]] = []
        for nid, stats in candidates.items():
            load_score = self._compute_score(stats)
            # Lower weight => higher effective score (worse)
            effective = load_score / max(stats.weight, 0.01)
            scored.append((nid, effective))

        scored.sort(key=lambda x: x[1])
        best_id, best_score = scored[0]

        return NodeSelection(
            node_id=best_id,
            algorithm_used=BalancingAlgorithm.WEIGHTED.value,
            score=best_score,
            reason=f"Weighted score ({best_score:.3f}, weight={candidates[best_id].weight})",
            node_stats=candidates[best_id],
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _compute_score(self, stats: NodeStats) -> float:
        """Compute a 0-1 composite load score (lower is better).

        Factors and their contributions:
        - GPU utilisation (normalised 0-1)
        - Queue depth (sigmoid-mapped)
        - Average latency (sigmoid-mapped to 0-1, midpoint 100ms)
        - Health penalty
        """
        gpu_score = stats.gpu_utilization / 100.0

        # Sigmoid mapping for queue depth (midpoint at depth 10)
        queue_score = 1.0 / (1.0 + 2.718 ** (-(stats.queue_depth - 10) / 3.0))

        # Sigmoid mapping for latency (midpoint at 100 ms)
        latency_score = 1.0 / (1.0 + 2.718 ** (-(stats.avg_latency_ms - 100) / 30.0))

        # Health: 0 for active, 0.5 for degraded-ish, 1.0 for problematic
        health_map = {
            NodeStatus.ACTIVE: 0.0,
            NodeStatus.DRAINING: 0.8,
            NodeStatus.UNHEALTHY: 1.0,
            NodeStatus.OFFLINE: 1.0,
        }
        health_score = health_map.get(stats.status, 1.0)

        # Stale data penalty
        if self._is_stale(stats):
            health_score = max(health_score, 0.5)

        composite = (
            self._weights["gpu"] * gpu_score
            + self._weights["queue"] * queue_score
            + self._weights["latency"] * latency_score
            + self._weights["health"] * health_score
        )
        return round(composite, 6)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_routable_nodes(self) -> Dict[str, NodeStats]:
        """Return nodes eligible to receive traffic (lock must be held)."""
        return {
            nid: stats
            for nid, stats in self._nodes.items()
            if stats.status in (NodeStatus.ACTIVE,)
        }

    def _is_stale(self, stats: NodeStats) -> bool:
        return (time.time() - stats.last_updated) > self._stale_threshold
