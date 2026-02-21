"""GPU fleet scheduling and workload distribution.

Implements bin-packing for GPU allocation, priority queuing for premium
versus standard users, and health-aware scheduling that avoids routing
traffic to unhealthy nodes.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Request priority levels (lower value = higher priority)."""

    PREMIUM = 0
    STANDARD = 1
    BATCH = 2


class NodeStatus(Enum):
    """Health status of a GPU node."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"


@dataclass
class InferenceRequest:
    """A single inference request awaiting scheduling.

    Attributes:
        request_id: Unique identifier.
        user_id: Originating user.
        priority: Scheduling priority.
        gpu_memory_required_gb: Estimated GPU memory footprint.
        estimated_tokens: Expected token count for the completion.
        enqueued_at: Epoch timestamp when the request was received.
    """

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = ""
    priority: Priority = Priority.STANDARD
    gpu_memory_required_gb: float = 4.0
    estimated_tokens: int = 512
    enqueued_at: float = field(default_factory=time.time)


@dataclass
class GPUNode:
    """Represents a single GPU node in the fleet.

    Attributes:
        node_id: Unique identifier for the node.
        total_memory_gb: Total GPU memory on the node.
        used_memory_gb: Currently allocated GPU memory.
        status: Node health status.
        assigned_requests: List of request IDs currently running on this node.
        region: Deployment region.
        gpu_utilization: Current GPU compute utilization (0.0 - 1.0).
        last_health_check: Epoch timestamp of the last successful health probe.
    """

    node_id: str
    total_memory_gb: float = 80.0
    used_memory_gb: float = 0.0
    status: NodeStatus = NodeStatus.HEALTHY
    assigned_requests: list[str] = field(default_factory=list)
    region: str = "us-east-1"
    gpu_utilization: float = 0.0
    last_health_check: float = field(default_factory=time.time)

    @property
    def available_memory_gb(self) -> float:
        return self.total_memory_gb - self.used_memory_gb

    @property
    def is_schedulable(self) -> bool:
        return self.status in (NodeStatus.HEALTHY, NodeStatus.DEGRADED)


@dataclass
class SchedulingResult:
    """Outcome of scheduling a single request."""

    request_id: str
    node_id: Optional[str]
    success: bool
    reason: str
    wait_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class FleetScheduler:
    """GPU fleet scheduler with bin-packing and priority queuing.

    Maintains a registry of GPU nodes and a priority queue of pending
    requests. The ``schedule_request`` method uses a best-fit
    bin-packing strategy to allocate GPU memory, preferring nodes with
    the least available memory that can still fit the request.

    Args:
        nodes: Initial set of GPU nodes in the fleet.
        max_queue_depth: Maximum number of pending requests before
            rejecting new ones.
    """

    def __init__(
        self,
        nodes: Optional[list[GPUNode]] = None,
        max_queue_depth: int = 500,
    ) -> None:
        self._nodes: dict[str, GPUNode] = {}
        self._pending_queue: list[InferenceRequest] = []
        self._max_queue_depth = max_queue_depth
        self._scheduling_history: list[SchedulingResult] = []

        for node in nodes or []:
            self.register_node(node)

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def register_node(self, node: GPUNode) -> None:
        """Add a GPU node to the fleet registry.

        Args:
            node: Node to register.
        """
        self._nodes[node.node_id] = node
        logger.info("Registered node %s (%s).", node.node_id, node.status.value)

    def deregister_node(self, node_id: str) -> Optional[GPUNode]:
        """Remove a node from the fleet.

        Any in-flight requests are *not* cancelled here; that
        responsibility lies with the orchestrator layer.

        Args:
            node_id: ID of the node to remove.

        Returns:
            The removed node, or ``None`` if not found.
        """
        node = self._nodes.pop(node_id, None)
        if node:
            logger.info("Deregistered node %s.", node_id)
        return node

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule_request(self, request: InferenceRequest) -> SchedulingResult:
        """Schedule an inference request onto a GPU node.

        Uses best-fit bin-packing: among all healthy nodes with enough
        free memory, select the one with the *least* available memory to
        minimise fragmentation.

        Premium requests are attempted first and may preempt standard
        requests in future iterations.

        Args:
            request: The inference request to schedule.

        Returns:
            A ``SchedulingResult`` indicating success or failure.
        """
        target_node = self._find_best_fit_node(request)

        if target_node is not None:
            return self._assign_to_node(request, target_node)

        # No node can fit the request right now -- enqueue it.
        if len(self._pending_queue) >= self._max_queue_depth:
            result = SchedulingResult(
                request_id=request.request_id,
                node_id=None,
                success=False,
                reason="Queue is full; request rejected.",
            )
            self._scheduling_history.append(result)
            return result

        self._enqueue(request)
        result = SchedulingResult(
            request_id=request.request_id,
            node_id=None,
            success=False,
            reason="No suitable node available; request enqueued.",
        )
        self._scheduling_history.append(result)
        return result

    def release_request(self, request_id: str, node_id: str) -> bool:
        """Release resources held by a completed request.

        Args:
            request_id: ID of the completed request.
            node_id: Node from which to release resources.

        Returns:
            ``True`` if the request was found and released.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return False

        if request_id not in node.assigned_requests:
            return False

        node.assigned_requests.remove(request_id)
        # Approximate memory release: uniform estimate.
        node.used_memory_gb = max(0.0, node.used_memory_gb - 4.0)
        logger.debug("Released request %s from node %s.", request_id, node_id)

        # Try to drain the pending queue.
        self._drain_queue()
        return True

    def rebalance(self) -> list[SchedulingResult]:
        """Rebalance workload across the fleet.

        Identifies over-loaded and under-loaded nodes and migrates
        requests to improve utilisation spread.

        Returns:
            List of scheduling results for migrated requests.
        """
        results: list[SchedulingResult] = []
        if not self._nodes:
            return results

        schedulable = [n for n in self._nodes.values() if n.is_schedulable]
        if len(schedulable) < 2:
            return results

        avg_utilization = sum(n.gpu_utilization for n in schedulable) / len(schedulable)
        overloaded = [n for n in schedulable if n.gpu_utilization > avg_utilization * 1.3]
        underloaded = [n for n in schedulable if n.gpu_utilization < avg_utilization * 0.7]

        for over_node in overloaded:
            if not over_node.assigned_requests or not underloaded:
                continue
            migrated_id = over_node.assigned_requests[0]
            target = min(underloaded, key=lambda n: n.gpu_utilization)

            over_node.assigned_requests.remove(migrated_id)
            over_node.used_memory_gb = max(0.0, over_node.used_memory_gb - 4.0)

            target.assigned_requests.append(migrated_id)
            target.used_memory_gb += 4.0

            result = SchedulingResult(
                request_id=migrated_id,
                node_id=target.node_id,
                success=True,
                reason=f"Rebalanced from {over_node.node_id} to {target.node_id}.",
            )
            results.append(result)
            logger.info(
                "Rebalanced request %s: %s -> %s",
                migrated_id,
                over_node.node_id,
                target.node_id,
            )

        return results

    def drain_node(self, node_id: str) -> list[str]:
        """Gracefully drain a node by rescheduling its workload.

        The node is marked ``DRAINING`` so it will not accept new work.
        Existing requests are moved to other schedulable nodes on a
        best-effort basis.

        Args:
            node_id: ID of the node to drain.

        Returns:
            List of request IDs that could *not* be rescheduled.
        """
        node = self._nodes.get(node_id)
        if node is None:
            logger.warning("drain_node: node %s not found.", node_id)
            return []

        node.status = NodeStatus.DRAINING
        orphaned: list[str] = []

        for req_id in list(node.assigned_requests):
            # Build a synthetic request for rescheduling.
            synth = InferenceRequest(request_id=req_id)
            target = self._find_best_fit_node(synth, exclude_node_ids={node_id})
            if target is not None:
                node.assigned_requests.remove(req_id)
                node.used_memory_gb = max(0.0, node.used_memory_gb - synth.gpu_memory_required_gb)
                self._assign_to_node(synth, target)
                logger.info("Drained request %s from %s to %s.", req_id, node_id, target.node_id)
            else:
                orphaned.append(req_id)

        if not orphaned:
            node.assigned_requests.clear()
            node.used_memory_gb = 0.0
        return orphaned

    def get_node_assignments(self) -> dict[str, list[str]]:
        """Return a mapping of node IDs to their assigned request IDs.

        Returns:
            Dictionary keyed by node ID, values are lists of request IDs.
        """
        return {
            node_id: list(node.assigned_requests)
            for node_id, node in self._nodes.items()
        }

    def get_fleet_health(self) -> dict[str, object]:
        """Aggregate health summary of the fleet.

        Returns:
            Dictionary with fleet-wide health statistics.
        """
        total = len(self._nodes)
        healthy = sum(1 for n in self._nodes.values() if n.status == NodeStatus.HEALTHY)
        degraded = sum(1 for n in self._nodes.values() if n.status == NodeStatus.DEGRADED)
        unhealthy = sum(1 for n in self._nodes.values() if n.status == NodeStatus.UNHEALTHY)
        draining = sum(1 for n in self._nodes.values() if n.status == NodeStatus.DRAINING)

        total_memory = sum(n.total_memory_gb for n in self._nodes.values())
        used_memory = sum(n.used_memory_gb for n in self._nodes.values())

        return {
            "total_nodes": total,
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "draining": draining,
            "total_memory_gb": round(total_memory, 2),
            "used_memory_gb": round(used_memory, 2),
            "memory_utilization": round(used_memory / total_memory, 4) if total_memory else 0.0,
            "pending_queue_depth": len(self._pending_queue),
            "total_assigned_requests": sum(
                len(n.assigned_requests) for n in self._nodes.values()
            ),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_best_fit_node(
        self,
        request: InferenceRequest,
        exclude_node_ids: Optional[set[str]] = None,
    ) -> Optional[GPUNode]:
        """Best-fit bin-packing: smallest adequate slot wins."""
        exclude = exclude_node_ids or set()
        candidates = [
            n
            for n in self._nodes.values()
            if (
                n.is_schedulable
                and n.node_id not in exclude
                and n.available_memory_gb >= request.gpu_memory_required_gb
            )
        ]
        if not candidates:
            return None

        # Best-fit: pick the node with the *least* available memory that
        # still has enough room.
        return min(candidates, key=lambda n: n.available_memory_gb)

    def _assign_to_node(
        self,
        request: InferenceRequest,
        node: GPUNode,
    ) -> SchedulingResult:
        node.assigned_requests.append(request.request_id)
        node.used_memory_gb += request.gpu_memory_required_gb
        wait_time_ms = (time.time() - request.enqueued_at) * 1000.0

        result = SchedulingResult(
            request_id=request.request_id,
            node_id=node.node_id,
            success=True,
            reason=f"Assigned to {node.node_id} (avail={node.available_memory_gb:.1f}GB).",
            wait_time_ms=round(wait_time_ms, 2),
        )
        self._scheduling_history.append(result)
        logger.debug(
            "Scheduled %s on %s (used=%.1fGB).",
            request.request_id,
            node.node_id,
            node.used_memory_gb,
        )
        return result

    def _enqueue(self, request: InferenceRequest) -> None:
        """Insert into the priority queue (sorted by Priority then arrival)."""
        self._pending_queue.append(request)
        self._pending_queue.sort(key=lambda r: (r.priority, r.enqueued_at))
        logger.debug(
            "Enqueued %s (priority=%s, queue_depth=%d).",
            request.request_id,
            request.priority.name,
            len(self._pending_queue),
        )

    def _drain_queue(self) -> None:
        """Try to schedule as many queued requests as possible."""
        still_pending: list[InferenceRequest] = []
        for req in self._pending_queue:
            target = self._find_best_fit_node(req)
            if target is not None:
                self._assign_to_node(req, target)
            else:
                still_pending.append(req)
        self._pending_queue = still_pending
