"""Prometheus metrics collection for the Netflix LLM Inference Platform.

Collects and exposes key operational metrics including inference throughput,
latency distributions, GPU utilization, cache performance, and error rates
for real-time monitoring and alerting.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# Latency histogram buckets targeting p50/p95/p99 analysis
_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.075,
    0.1, 0.15, 0.2, 0.25, 0.3,
    0.4, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0,
)

_BATCH_SIZE_BUCKETS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512)


@dataclass
class MetricsSummary:
    """Snapshot of current platform metrics."""

    total_requests: float = 0.0
    total_errors: float = 0.0
    avg_latency_seconds: float = 0.0
    gpu_utilization_percent: float = 0.0
    gpu_memory_used_bytes: float = 0.0
    kv_cache_utilization: float = 0.0
    cache_hit_rate: float = 0.0
    active_sessions: int = 0
    tokens_per_second: float = 0.0
    queue_depth: int = 0
    uptime_seconds: float = 0.0


class MetricsCollector:
    """Prometheus-based metrics collector for the LLM inference platform.

    Tracks inference requests, latencies, GPU utilisation, cache performance,
    and error rates.  All metrics are registered in a dedicated
    ``CollectorRegistry`` so they can be served independently of the global
    default registry used by other libraries.

    Parameters
    ----------
    namespace:
        Prometheus metric namespace prefix (default ``"netflix_llm"``).
    registry:
        Optional pre-existing ``CollectorRegistry``.  When *None* a new
        private registry is created.
    """

    def __init__(
        self,
        namespace: str = "netflix_llm",
        registry: Optional[CollectorRegistry] = None,
    ) -> None:
        self._namespace = namespace
        self._registry = registry or CollectorRegistry()
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

        # Running averages kept locally for the summary endpoint.
        self._total_latency: float = 0.0
        self._total_tokens: int = 0
        self._request_count: int = 0

        # ---- Counters ----
        self.inference_requests_total = Counter(
            f"{namespace}_inference_requests_total",
            "Total number of inference requests processed",
            labelnames=["model", "status"],
            registry=self._registry,
        )

        self.error_rate = Counter(
            f"{namespace}_errors_total",
            "Total number of errors by type",
            labelnames=["error_type"],
            registry=self._registry,
        )

        # ---- Histograms ----
        self.inference_latency_seconds = Histogram(
            f"{namespace}_inference_latency_seconds",
            "Inference request latency in seconds",
            labelnames=["model"],
            buckets=_LATENCY_BUCKETS,
            registry=self._registry,
        )

        self.batch_size = Histogram(
            f"{namespace}_batch_size",
            "Batch sizes used for inference",
            buckets=_BATCH_SIZE_BUCKETS,
            registry=self._registry,
        )

        # ---- Gauges ----
        self.gpu_utilization_percent = Gauge(
            f"{namespace}_gpu_utilization_percent",
            "Current GPU compute utilization percentage",
            labelnames=["gpu_id"],
            registry=self._registry,
        )

        self.gpu_memory_used_bytes = Gauge(
            f"{namespace}_gpu_memory_used_bytes",
            "GPU memory currently in use (bytes)",
            labelnames=["gpu_id"],
            registry=self._registry,
        )

        self.kv_cache_utilization = Gauge(
            f"{namespace}_kv_cache_utilization",
            "KV-cache utilization ratio (0-1)",
            registry=self._registry,
        )

        self.kv_cache_hit_rate = Gauge(
            f"{namespace}_kv_cache_hit_rate",
            "KV-cache hit rate ratio (0-1)",
            registry=self._registry,
        )

        self.active_sessions = Gauge(
            f"{namespace}_active_sessions",
            "Number of active inference sessions",
            registry=self._registry,
        )

        self.tokens_per_second = Gauge(
            f"{namespace}_tokens_per_second",
            "Current token generation throughput",
            registry=self._registry,
        )

        self.model_load_time_seconds = Gauge(
            f"{namespace}_model_load_time_seconds",
            "Time taken to load the current model (seconds)",
            labelnames=["model"],
            registry=self._registry,
        )

        self.queue_depth = Gauge(
            f"{namespace}_queue_depth",
            "Current inference request queue depth",
            registry=self._registry,
        )

        logger.info("MetricsCollector initialised (namespace=%s)", namespace)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def record_inference(
        self,
        latency: float,
        batch_size: int,
        tokens: int,
        model: str = "default",
        status: str = "success",
    ) -> None:
        """Record a completed inference request.

        Parameters
        ----------
        latency:
            Wall-clock latency of the request in **seconds**.
        batch_size:
            Number of sequences in the batch.
        tokens:
            Total tokens generated by the request.
        model:
            Model identifier label.
        status:
            ``"success"`` or ``"error"``.
        """
        self.inference_requests_total.labels(model=model, status=status).inc()
        self.inference_latency_seconds.labels(model=model).observe(latency)
        self.batch_size.observe(batch_size)

        with self._lock:
            self._total_latency += latency
            self._total_tokens += tokens
            self._request_count += 1

        # Update throughput gauge.
        elapsed = time.monotonic() - self._start_time
        if elapsed > 0:
            self.tokens_per_second.set(self._total_tokens / elapsed)

        if status == "error":
            self.error_rate.labels(error_type="inference").inc()

    def update_gpu_metrics(
        self,
        utilization: float,
        memory: float,
        gpu_id: str = "0",
    ) -> None:
        """Update GPU utilisation and memory gauges.

        Parameters
        ----------
        utilization:
            GPU SM utilization percentage (0-100).
        memory:
            GPU memory in use (bytes).
        gpu_id:
            GPU device identifier.
        """
        self.gpu_utilization_percent.labels(gpu_id=gpu_id).set(utilization)
        self.gpu_memory_used_bytes.labels(gpu_id=gpu_id).set(memory)

    def update_cache_metrics(
        self,
        utilization: float,
        hit_rate: float,
    ) -> None:
        """Update KV-cache metrics.

        Parameters
        ----------
        utilization:
            Cache utilization ratio (0.0 - 1.0).
        hit_rate:
            Cache hit rate ratio (0.0 - 1.0).
        """
        self.kv_cache_utilization.set(utilization)
        self.kv_cache_hit_rate.set(hit_rate)

    def record_error(self, error_type: str) -> None:
        """Increment the error counter for a specific error category."""
        self.error_rate.labels(error_type=error_type).inc()

    def set_model_load_time(self, load_time: float, model: str = "default") -> None:
        """Record the time taken to load a model."""
        self.model_load_time_seconds.labels(model=model).set(load_time)

    def set_queue_depth(self, depth: int) -> None:
        """Update the current queue depth gauge."""
        self.queue_depth.set(depth)

    def set_active_sessions(self, count: int) -> None:
        """Update the active sessions gauge."""
        self.active_sessions.set(count)

    def get_summary(self) -> MetricsSummary:
        """Return a point-in-time summary of the most important metrics.

        This is a lightweight snapshot intended for internal dashboards and
        the health-check endpoint -- it does *not* replace the full
        Prometheus scrape.
        """
        with self._lock:
            avg_latency = (
                self._total_latency / self._request_count
                if self._request_count > 0
                else 0.0
            )
            summary = MetricsSummary(
                total_requests=self._request_count,
                avg_latency_seconds=avg_latency,
                tokens_per_second=self._total_tokens / max(time.monotonic() - self._start_time, 1e-9),
                uptime_seconds=time.monotonic() - self._start_time,
            )
        return summary

    def generate_prometheus_output(self) -> bytes:
        """Serialise all collected metrics in the Prometheus exposition format."""
        return generate_latest(self._registry)
