"""NVIDIA DCGM (Data Center GPU Manager) metrics exporter.

Collects low-level GPU telemetry -- SM occupancy, memory utilisation,
temperature, power draw, ECC errors, PCIe bandwidth, and tensor-core
utilisation -- and exposes it to the platform's observability stack.

The exporter supports a **mock mode** for development and CI environments
that do not have physical GPUs or the DCGM host engine running.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GPUHealthStatus(str, Enum):
    """Overall health assessment for a single GPU."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GPUMetricsSample:
    """A single point-in-time collection of GPU metrics."""

    timestamp: float
    gpu_id: int
    sm_occupancy: float          # percentage 0-100
    memory_used_bytes: int
    memory_total_bytes: int
    temperature_celsius: float
    power_draw_watts: float
    ecc_errors_single: int
    ecc_errors_double: int
    pcie_tx_bandwidth_mbps: float
    pcie_rx_bandwidth_mbps: float
    tensor_core_utilization: float  # percentage 0-100


@dataclass
class GPUHealth:
    """Structured health report for a single GPU."""

    gpu_id: int
    status: GPUHealthStatus
    temperature_celsius: float
    memory_utilization: float
    ecc_errors_single: int
    ecc_errors_double: int
    power_draw_watts: float
    anomalies: List[str] = field(default_factory=list)


@dataclass
class AnomalyReport:
    """Describes a detected anomaly on a GPU."""

    gpu_id: int
    anomaly_type: str
    severity: str          # "warning" | "critical"
    message: str
    value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)


# Anomaly detection thresholds
_TEMP_WARNING_C = 80.0
_TEMP_CRITICAL_C = 90.0
_MEMORY_WARNING_RATIO = 0.90
_MEMORY_CRITICAL_RATIO = 0.95
_ECC_DOUBLE_THRESHOLD = 0
_POWER_WARNING_RATIO = 0.90  # relative to TDP
_DEFAULT_TDP_WATTS = 400.0   # A100 TDP
_HISTORY_MAX_SAMPLES = 8640  # 24 h at 10-second intervals


class DCGMExporter:
    """Export GPU metrics via NVIDIA DCGM or fall back to mock data.

    Parameters
    ----------
    gpu_ids:
        List of GPU device ordinals to monitor. Defaults to ``[0]``.
    mock_mode:
        If *True*, generates synthetic telemetry instead of querying DCGM.
        Automatically enabled when the DCGM bindings are unavailable.
    collection_interval_s:
        Seconds between background collection sweeps (default 10).
    tdp_watts:
        Thermal Design Power for power-anomaly detection (default 400 W).
    """

    def __init__(
        self,
        gpu_ids: Optional[List[int]] = None,
        mock_mode: bool = False,
        collection_interval_s: float = 10.0,
        tdp_watts: float = _DEFAULT_TDP_WATTS,
    ) -> None:
        self._gpu_ids = gpu_ids or [0]
        self._collection_interval = collection_interval_s
        self._tdp_watts = tdp_watts
        self._lock = threading.Lock()

        # Try to import DCGM bindings; fall back to mock if unavailable.
        self._mock_mode = mock_mode
        self._dcgm_handle: Optional[Any] = None
        if not self._mock_mode:
            try:
                import pydcgm  # type: ignore[import-untyped]
                self._dcgm_handle = pydcgm
                logger.info("DCGM bindings loaded successfully")
            except ImportError:
                logger.warning(
                    "pydcgm not available -- falling back to mock mode"
                )
                self._mock_mode = True

        # Per-GPU rolling history
        self._history: Dict[int, Deque[GPUMetricsSample]] = {
            gid: deque(maxlen=_HISTORY_MAX_SAMPLES) for gid in self._gpu_ids
        }

        # Latest sample per GPU
        self._latest: Dict[int, Optional[GPUMetricsSample]] = {
            gid: None for gid in self._gpu_ids
        }

        # Background collector
        self._running = False
        self._thread: Optional[threading.Thread] = None

        logger.info(
            "DCGMExporter initialised (gpus=%s, mock=%s)",
            self._gpu_ids,
            self._mock_mode,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background metrics collection loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._collection_loop, daemon=True, name="dcgm-collector"
        )
        self._thread.start()
        logger.info("DCGM background collector started")

    def stop(self) -> None:
        """Stop the background collector."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._collection_interval * 2)
            self._thread = None
        logger.info("DCGM background collector stopped")

    # ------------------------------------------------------------------
    # Core collection
    # ------------------------------------------------------------------

    def collect_metrics(self) -> Dict[int, GPUMetricsSample]:
        """Collect a fresh sample from every monitored GPU.

        Returns a mapping of ``gpu_id -> GPUMetricsSample``.
        """
        samples: Dict[int, GPUMetricsSample] = {}
        now = time.time()

        for gpu_id in self._gpu_ids:
            if self._mock_mode:
                sample = self._mock_sample(gpu_id, now)
            else:
                sample = self._dcgm_sample(gpu_id, now)

            samples[gpu_id] = sample
            with self._lock:
                self._latest[gpu_id] = sample
                self._history[gpu_id].append(sample)

        return samples

    # ------------------------------------------------------------------
    # Health & anomalies
    # ------------------------------------------------------------------

    def get_gpu_health(self) -> Dict[int, GPUHealth]:
        """Return the health status of every monitored GPU.

        Triggers a fresh collection if no data is available yet.
        """
        with self._lock:
            if all(v is None for v in self._latest.values()):
                # No data yet -- collect now.
                pass  # release lock first
            else:
                return self._evaluate_health()

        self.collect_metrics()
        with self._lock:
            return self._evaluate_health()

    def detect_anomalies(self) -> List[AnomalyReport]:
        """Scan latest metrics for threshold violations.

        Returns a (possibly empty) list of :class:`AnomalyReport` instances.
        """
        anomalies: List[AnomalyReport] = []
        with self._lock:
            for gpu_id, sample in self._latest.items():
                if sample is None:
                    continue
                anomalies.extend(self._check_anomalies(sample))
        return anomalies

    def get_historical_metrics(
        self, duration_seconds: float
    ) -> Dict[int, List[GPUMetricsSample]]:
        """Return historical samples within *duration_seconds* from now.

        Parameters
        ----------
        duration_seconds:
            Look-back window in seconds.  Capped at whatever history is
            currently retained.
        """
        cutoff = time.time() - duration_seconds
        result: Dict[int, List[GPUMetricsSample]] = {}
        with self._lock:
            for gpu_id, history in self._history.items():
                result[gpu_id] = [s for s in history if s.timestamp >= cutoff]
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collection_loop(self) -> None:
        """Background loop that periodically calls :meth:`collect_metrics`."""
        while self._running:
            try:
                self.collect_metrics()
            except Exception:
                logger.exception("Error during DCGM metric collection")
            time.sleep(self._collection_interval)

    def _dcgm_sample(self, gpu_id: int, timestamp: float) -> GPUMetricsSample:
        """Collect a real sample via DCGM bindings.

        This is a placeholder that mirrors the expected DCGM API surface.
        In production, ``pydcgm`` field IDs would be used to query the
        DCGM host engine.
        """
        # In a real deployment this would call:
        #   dcgm_agent.dcgmGetLatestValues(handle, group, fieldGroup)
        # We fall back to mock for safety in case the bindings exist but
        # the host engine is unreachable.
        try:
            return self._query_dcgm_fields(gpu_id, timestamp)
        except Exception:
            logger.warning(
                "DCGM query failed for GPU %d -- using mock data", gpu_id
            )
            return self._mock_sample(gpu_id, timestamp)

    def _query_dcgm_fields(
        self, gpu_id: int, timestamp: float
    ) -> GPUMetricsSample:
        """Query DCGM field values for a single GPU.

        Override this method in subclasses or tests to inject real DCGM
        data.
        """
        raise NotImplementedError("Direct DCGM query not implemented")

    def _mock_sample(self, gpu_id: int, timestamp: float) -> GPUMetricsSample:
        """Generate a plausible synthetic sample for testing."""
        memory_total = 80 * 1024 ** 3  # 80 GiB (A100)
        base_util = 55.0 + gpu_id * 5.0
        return GPUMetricsSample(
            timestamp=timestamp,
            gpu_id=gpu_id,
            sm_occupancy=min(100.0, base_util + random.gauss(0, 8)),
            memory_used_bytes=int(memory_total * random.uniform(0.30, 0.85)),
            memory_total_bytes=memory_total,
            temperature_celsius=round(62.0 + random.gauss(0, 4), 1),
            power_draw_watts=round(250.0 + random.gauss(0, 30), 1),
            ecc_errors_single=0,
            ecc_errors_double=0,
            pcie_tx_bandwidth_mbps=round(random.uniform(8_000, 25_000), 1),
            pcie_rx_bandwidth_mbps=round(random.uniform(8_000, 25_000), 1),
            tensor_core_utilization=min(
                100.0, base_util * 0.9 + random.gauss(0, 6)
            ),
        )

    def _evaluate_health(self) -> Dict[int, GPUHealth]:
        """Evaluate health for every GPU based on the latest sample.

        Must be called with ``self._lock`` held.
        """
        results: Dict[int, GPUHealth] = {}
        for gpu_id, sample in self._latest.items():
            if sample is None:
                results[gpu_id] = GPUHealth(
                    gpu_id=gpu_id,
                    status=GPUHealthStatus.UNKNOWN,
                    temperature_celsius=0.0,
                    memory_utilization=0.0,
                    ecc_errors_single=0,
                    ecc_errors_double=0,
                    power_draw_watts=0.0,
                )
                continue

            anomalies = self._check_anomalies(sample)
            anomaly_msgs = [a.message for a in anomalies]

            has_critical = any(a.severity == "critical" for a in anomalies)
            has_warning = any(a.severity == "warning" for a in anomalies)

            if has_critical:
                status = GPUHealthStatus.CRITICAL
            elif has_warning:
                status = GPUHealthStatus.DEGRADED
            else:
                status = GPUHealthStatus.HEALTHY

            mem_util = (
                sample.memory_used_bytes / sample.memory_total_bytes
                if sample.memory_total_bytes > 0
                else 0.0
            )

            results[gpu_id] = GPUHealth(
                gpu_id=gpu_id,
                status=status,
                temperature_celsius=sample.temperature_celsius,
                memory_utilization=mem_util,
                ecc_errors_single=sample.ecc_errors_single,
                ecc_errors_double=sample.ecc_errors_double,
                power_draw_watts=sample.power_draw_watts,
                anomalies=anomaly_msgs,
            )
        return results

    def _check_anomalies(self, sample: GPUMetricsSample) -> List[AnomalyReport]:
        """Check a single sample against anomaly thresholds."""
        anomalies: List[AnomalyReport] = []

        # Temperature
        if sample.temperature_celsius >= _TEMP_CRITICAL_C:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="temperature",
                    severity="critical",
                    message=f"GPU {sample.gpu_id} temperature {sample.temperature_celsius:.1f}C exceeds critical threshold {_TEMP_CRITICAL_C}C",
                    value=sample.temperature_celsius,
                    threshold=_TEMP_CRITICAL_C,
                    timestamp=sample.timestamp,
                )
            )
        elif sample.temperature_celsius >= _TEMP_WARNING_C:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="temperature",
                    severity="warning",
                    message=f"GPU {sample.gpu_id} temperature {sample.temperature_celsius:.1f}C exceeds warning threshold {_TEMP_WARNING_C}C",
                    value=sample.temperature_celsius,
                    threshold=_TEMP_WARNING_C,
                    timestamp=sample.timestamp,
                )
            )

        # Memory
        mem_ratio = (
            sample.memory_used_bytes / sample.memory_total_bytes
            if sample.memory_total_bytes > 0
            else 0.0
        )
        if mem_ratio >= _MEMORY_CRITICAL_RATIO:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="memory",
                    severity="critical",
                    message=f"GPU {sample.gpu_id} memory utilization {mem_ratio:.1%} exceeds critical threshold {_MEMORY_CRITICAL_RATIO:.0%}",
                    value=mem_ratio,
                    threshold=_MEMORY_CRITICAL_RATIO,
                    timestamp=sample.timestamp,
                )
            )
        elif mem_ratio >= _MEMORY_WARNING_RATIO:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="memory",
                    severity="warning",
                    message=f"GPU {sample.gpu_id} memory utilization {mem_ratio:.1%} exceeds warning threshold {_MEMORY_WARNING_RATIO:.0%}",
                    value=mem_ratio,
                    threshold=_MEMORY_WARNING_RATIO,
                    timestamp=sample.timestamp,
                )
            )

        # ECC double-bit errors (always critical)
        if sample.ecc_errors_double > _ECC_DOUBLE_THRESHOLD:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="ecc_double_bit",
                    severity="critical",
                    message=f"GPU {sample.gpu_id} has {sample.ecc_errors_double} uncorrectable ECC error(s)",
                    value=float(sample.ecc_errors_double),
                    threshold=float(_ECC_DOUBLE_THRESHOLD),
                    timestamp=sample.timestamp,
                )
            )

        # Power draw
        power_ratio = sample.power_draw_watts / self._tdp_watts
        if power_ratio >= _POWER_WARNING_RATIO:
            anomalies.append(
                AnomalyReport(
                    gpu_id=sample.gpu_id,
                    anomaly_type="power",
                    severity="warning",
                    message=f"GPU {sample.gpu_id} power draw {sample.power_draw_watts:.0f}W is {power_ratio:.0%} of TDP ({self._tdp_watts:.0f}W)",
                    value=sample.power_draw_watts,
                    threshold=self._tdp_watts * _POWER_WARNING_RATIO,
                    timestamp=sample.timestamp,
                )
            )

        return anomalies
