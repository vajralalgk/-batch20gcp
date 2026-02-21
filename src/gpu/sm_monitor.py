"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
SM Occupancy Monitor — Streaming Multiprocessor Utilization Tracking
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Streaming Multiprocessors (SMs) are the computational cores of NVIDIA
    GPUs. An A100 has 108 SMs, each capable of running thousands of threads
    concurrently. For LLM inference, SM occupancy directly determines:

    1. Throughput: Higher occupancy = more tokens generated per second.
    2. Latency: Under-occupied SMs mean wasted compute cycles that could
       have reduced time-to-first-token (TTFT).
    3. Cost efficiency: Each idle SM cycle is wasted GPU-hour billing.

    At Netflix scale (~1000 A100 GPUs for inference), a 5% improvement
    in SM occupancy translates to ~50 GPUs worth of additional capacity,
    saving approximately $3M/year in cloud costs.

DESIGN DECISIONS:
    - Mock mode: Supports environments without physical GPUs (CI/CD,
      development, testing) by generating realistic synthetic metrics.
    - Sliding window history: Maintains a configurable window of
      historical samples for trend detection and anomaly alerting.
    - Bottleneck detection: Identifies whether workload is compute-bound,
      memory-bound, or IO-bound based on SM and memory bandwidth metrics.
    - NVML integration: Uses pynvml (NVIDIA Management Library) for
      hardware-level telemetry when available.

METRICS TRACKED:
    - SM occupancy (%): Fraction of SM warps that are active.
    - Memory bandwidth utilization (%): How much of peak DRAM bandwidth
      is being used. LLM inference is often memory-bandwidth-bound.
    - Tensor core utilization (%): Usage of dedicated matrix-multiply units.
    - Temperature (C): Thermal throttling starts at ~83C on A100.
    - Power draw (W): Approaches TDP limit under sustained load.
============================================================================
"""

import logging
import random
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Attempt to import pynvml for real GPU telemetry.
# Falls back to mock mode if not available (e.g., development environments).
_NVML_AVAILABLE = False
try:
    import pynvml

    _NVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]


class BottleneckType(Enum):
    """Classification of GPU performance bottlenecks.

    COMPUTE_BOUND: SMs are fully occupied but throughput is limited by
        arithmetic intensity. Solutions: quantization, operator fusion.
    MEMORY_BOUND: Memory bandwidth is saturated while SMs are under-occupied.
        This is the most common bottleneck for LLM inference (large KV
        cache reads during attention). Solutions: KV cache compression,
        FlashAttention, batch size tuning.
    IO_BOUND: Neither compute nor memory is saturated — the bottleneck is
        in data transfer (PCIe, NVLink, network). Solutions: prefetching,
        pipeline parallelism, larger batch accumulation.
    THERMAL_THROTTLED: GPU is throttling due to high temperature.
        Solutions: improve cooling, reduce sustained load.
    BALANCED: No single bottleneck dominates. This is the ideal state.
    """

    COMPUTE_BOUND = "compute_bound"
    MEMORY_BOUND = "memory_bound"
    IO_BOUND = "io_bound"
    THERMAL_THROTTLED = "thermal_throttled"
    BALANCED = "balanced"


@dataclass
class SMSample:
    """A single point-in-time measurement of SM metrics.

    Attributes:
        timestamp: Unix timestamp of the measurement.
        sm_occupancy_percent: SM occupancy as a percentage (0-100).
        memory_bandwidth_utilization: Memory bandwidth usage as a
            percentage of peak (0-100).
        tensor_core_utilization: Tensor core usage as a percentage (0-100).
        temperature_celsius: GPU die temperature.
        power_draw_watts: Current power consumption.
        gpu_clock_mhz: Current GPU core clock frequency.
        memory_clock_mhz: Current memory clock frequency.
    """

    timestamp: float = field(default_factory=time.time)
    sm_occupancy_percent: float = 0.0
    memory_bandwidth_utilization: float = 0.0
    tensor_core_utilization: float = 0.0
    temperature_celsius: float = 0.0
    power_draw_watts: float = 0.0
    gpu_clock_mhz: float = 0.0
    memory_clock_mhz: float = 0.0


@dataclass
class GPUSpec:
    """Hardware specifications for a GPU SKU.

    Used to contextualize utilization metrics against the hardware's
    theoretical peak capabilities.
    """

    name: str = "NVIDIA A100-SXM4-80GB"
    num_sms: int = 108
    max_threads_per_sm: int = 2048
    peak_fp16_tflops: float = 312.0
    peak_memory_bandwidth_gbps: float = 2039.0
    tdp_watts: float = 400.0
    throttle_temp_celsius: float = 83.0
    max_gpu_clock_mhz: float = 1410.0
    max_memory_clock_mhz: float = 1593.0


# Pre-defined GPU specifications for supported SKUs.
GPU_SPECS: Dict[str, GPUSpec] = {
    "A100": GPUSpec(
        name="NVIDIA A100-SXM4-80GB",
        num_sms=108,
        max_threads_per_sm=2048,
        peak_fp16_tflops=312.0,
        peak_memory_bandwidth_gbps=2039.0,
        tdp_watts=400.0,
        throttle_temp_celsius=83.0,
        max_gpu_clock_mhz=1410.0,
        max_memory_clock_mhz=1593.0,
    ),
    "A10G": GPUSpec(
        name="NVIDIA A10G",
        num_sms=80,
        max_threads_per_sm=1536,
        peak_fp16_tflops=125.0,
        peak_memory_bandwidth_gbps=600.0,
        tdp_watts=150.0,
        throttle_temp_celsius=92.0,
        max_gpu_clock_mhz=1695.0,
        max_memory_clock_mhz=6251.0,
    ),
}


class SMOccupancyMonitor:
    """Monitors GPU Streaming Multiprocessor occupancy and performance.

    Collects hardware-level telemetry from NVIDIA GPUs (via pynvml) or
    generates realistic synthetic metrics in mock mode. Maintains a
    sliding window of historical measurements for trend analysis and
    bottleneck detection.

    Thread Safety:
        All public methods are thread-safe. The monitor can run a
        background collection thread or be polled manually.

    Example:
        >>> monitor = SMOccupancyMonitor(device_id=0, gpu_sku="A100")
        >>> monitor.start()
        >>> occupancy = monitor.get_occupancy()
        >>> bottleneck = monitor.detect_bottleneck()
        >>> recommendations = monitor.get_recommendations()
        >>> monitor.stop()
    """

    def __init__(
        self,
        device_id: int = 0,
        gpu_sku: str = "A100",
        mock_mode: Optional[bool] = None,
        history_window_seconds: float = 300.0,
        sample_interval_seconds: float = 1.0,
        max_history_samples: int = 3600,
    ) -> None:
        """Initialize the SM occupancy monitor.

        Args:
            device_id: The GPU device index to monitor.
            gpu_sku: GPU model identifier ("A100" or "A10G").
            mock_mode: If True, use synthetic metrics. If False, require
                real NVML. If None, auto-detect based on pynvml availability.
            history_window_seconds: How far back to keep history for
                trend analysis.
            sample_interval_seconds: Time between metric samples.
            max_history_samples: Maximum number of samples to retain.
        """
        self._device_id: int = device_id
        self._gpu_sku: str = gpu_sku
        self._spec: GPUSpec = GPU_SPECS.get(gpu_sku, GPU_SPECS["A100"])
        self._sample_interval: float = sample_interval_seconds
        self._history_window: float = history_window_seconds
        self._max_history: int = max_history_samples

        # Auto-detect mock mode if not explicitly set
        if mock_mode is None:
            self._mock_mode = not _NVML_AVAILABLE
        else:
            self._mock_mode = mock_mode

        # NVML handle (initialized in start() if not in mock mode)
        self._nvml_handle: Optional[Any] = None
        self._nvml_initialized: bool = False

        # Historical samples (ring buffer via deque)
        self._history: Deque[SMSample] = deque(maxlen=max_history_samples)

        # Latest sample for quick access
        self._latest_sample: Optional[SMSample] = None

        # Background collection thread
        self._collection_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Thread safety
        self._lock: threading.RLock = threading.RLock()

        # Mock state: smooth random walk for realistic synthetic metrics
        self._mock_state: Dict[str, float] = {
            "sm_occupancy": 65.0,
            "memory_bw": 72.0,
            "tensor_core": 45.0,
            "temperature": 62.0,
            "power": 280.0,
            "gpu_clock": 1350.0,
            "mem_clock": 1550.0,
        }

        mode_str = "mock" if self._mock_mode else "nvml"
        logger.info(
            "sm_monitor_initialized",
            extra={
                "device_id": device_id,
                "gpu_sku": gpu_sku,
                "mode": mode_str,
                "sample_interval_seconds": sample_interval_seconds,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background metric collection thread.

        Initializes the NVML library (if not in mock mode) and begins
        periodic metric sampling. Must be called before querying metrics.
        """
        with self._lock:
            if self._running:
                logger.warning("sm_monitor_already_running")
                return

            # Initialize NVML if available and not in mock mode
            if not self._mock_mode and _NVML_AVAILABLE:
                try:
                    pynvml.nvmlInit()
                    self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(
                        self._device_id
                    )
                    self._nvml_initialized = True
                    logger.info(
                        "nvml_initialized",
                        extra={"device_id": self._device_id},
                    )
                except Exception as e:
                    logger.warning(
                        "nvml_initialization_failed_falling_back_to_mock",
                        extra={"error": str(e)},
                    )
                    self._mock_mode = True

            self._running = True
            self._collection_thread = threading.Thread(
                target=self._collection_loop,
                name=f"sm-monitor-{self._device_id}",
                daemon=True,
            )
            self._collection_thread.start()
            logger.info(
                "sm_monitor_started",
                extra={"device_id": self._device_id},
            )

    def stop(self) -> None:
        """Stop the background collection thread and release NVML resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._collection_thread is not None:
            self._collection_thread.join(timeout=self._sample_interval * 3)
            self._collection_thread = None

        # Shutdown NVML
        if self._nvml_initialized and _NVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
                self._nvml_initialized = False
            except Exception:
                pass

        logger.info(
            "sm_monitor_stopped",
            extra={"device_id": self._device_id},
        )

    # ------------------------------------------------------------------
    # Core Metrics
    # ------------------------------------------------------------------

    def get_occupancy(self) -> Dict[str, Any]:
        """Get the latest SM occupancy and related metrics.

        Returns the most recent sample along with summary statistics
        (mean, P50, P95, P99) over the history window.

        Returns:
            Dictionary with current and historical occupancy metrics.
        """
        with self._lock:
            if self._latest_sample is None:
                # Take a sample now if none exists
                self._collect_sample()

            sample = self._latest_sample
            if sample is None:
                return {
                    "status": "no_data",
                    "device_id": self._device_id,
                    "gpu_sku": self._gpu_sku,
                }

            # Compute summary stats from history
            occupancy_values = [s.sm_occupancy_percent for s in self._history]
            mem_bw_values = [
                s.memory_bandwidth_utilization for s in self._history
            ]
            tensor_values = [s.tensor_core_utilization for s in self._history]

            return {
                "device_id": self._device_id,
                "gpu_sku": self._gpu_sku,
                "gpu_name": self._spec.name,
                "timestamp": sample.timestamp,
                "current": {
                    "sm_occupancy_percent": round(
                        sample.sm_occupancy_percent, 2
                    ),
                    "memory_bandwidth_utilization": round(
                        sample.memory_bandwidth_utilization, 2
                    ),
                    "tensor_core_utilization": round(
                        sample.tensor_core_utilization, 2
                    ),
                    "temperature_celsius": round(
                        sample.temperature_celsius, 1
                    ),
                    "power_draw_watts": round(sample.power_draw_watts, 1),
                    "gpu_clock_mhz": round(sample.gpu_clock_mhz, 0),
                    "memory_clock_mhz": round(sample.memory_clock_mhz, 0),
                },
                "summary": {
                    "sm_occupancy": self._compute_percentile_stats(
                        occupancy_values
                    ),
                    "memory_bandwidth": self._compute_percentile_stats(
                        mem_bw_values
                    ),
                    "tensor_core": self._compute_percentile_stats(
                        tensor_values
                    ),
                },
                "history_samples": len(self._history),
                "num_sms": self._spec.num_sms,
                "peak_fp16_tflops": self._spec.peak_fp16_tflops,
            }

    def get_utilization_history(
        self,
        last_n_seconds: Optional[float] = None,
        metric: str = "sm_occupancy_percent",
    ) -> List[Dict[str, float]]:
        """Retrieve historical utilization samples.

        Args:
            last_n_seconds: Only return samples from the last N seconds.
                If None, returns the entire history window.
            metric: Which metric to retrieve. One of:
                "sm_occupancy_percent", "memory_bandwidth_utilization",
                "tensor_core_utilization", "temperature_celsius",
                "power_draw_watts".

        Returns:
            List of (timestamp, value) dictionaries, sorted by time.
        """
        with self._lock:
            cutoff = (
                time.time() - last_n_seconds
                if last_n_seconds is not None
                else 0.0
            )

            result: List[Dict[str, float]] = []
            for sample in self._history:
                if sample.timestamp < cutoff:
                    continue
                value = getattr(sample, metric, None)
                if value is not None:
                    result.append({
                        "timestamp": sample.timestamp,
                        "value": round(value, 2),
                    })

            return result

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def detect_bottleneck(self) -> Dict[str, Any]:
        """Analyze recent metrics to detect the primary performance bottleneck.

        Uses the following heuristics (calibrated on Netflix LLM workloads):
        - If temperature >= throttle temp: THERMAL_THROTTLED
        - If SM occupancy > 80% and memory BW < 60%: COMPUTE_BOUND
        - If memory BW > 80% and SM occupancy < 60%: MEMORY_BOUND
        - If both SM and memory BW < 50%: IO_BOUND
        - Otherwise: BALANCED

        Returns:
            Dictionary with bottleneck classification, confidence, and
            supporting metrics.
        """
        with self._lock:
            if len(self._history) < 5:
                return {
                    "bottleneck": "insufficient_data",
                    "confidence": 0.0,
                    "device_id": self._device_id,
                    "message": (
                        "Need at least 5 samples for bottleneck detection. "
                        f"Currently have {len(self._history)}."
                    ),
                }

            # Use the last 30 seconds of data (or all if less available)
            recent_cutoff = time.time() - 30.0
            recent = [
                s for s in self._history if s.timestamp >= recent_cutoff
            ]
            if len(recent) < 3:
                recent = list(self._history)[-30:]

            avg_sm = statistics.mean(
                s.sm_occupancy_percent for s in recent
            )
            avg_mem_bw = statistics.mean(
                s.memory_bandwidth_utilization for s in recent
            )
            avg_tensor = statistics.mean(
                s.tensor_core_utilization for s in recent
            )
            avg_temp = statistics.mean(
                s.temperature_celsius for s in recent
            )
            avg_power = statistics.mean(s.power_draw_watts for s in recent)

            # Determine bottleneck type
            bottleneck: BottleneckType
            confidence: float

            if avg_temp >= self._spec.throttle_temp_celsius:
                bottleneck = BottleneckType.THERMAL_THROTTLED
                confidence = min(
                    1.0,
                    (avg_temp - self._spec.throttle_temp_celsius) / 10.0 + 0.7,
                )
            elif avg_sm > 80.0 and avg_mem_bw < 60.0:
                bottleneck = BottleneckType.COMPUTE_BOUND
                confidence = min(1.0, (avg_sm - 60.0) / 40.0)
            elif avg_mem_bw > 80.0 and avg_sm < 60.0:
                bottleneck = BottleneckType.MEMORY_BOUND
                confidence = min(1.0, (avg_mem_bw - 60.0) / 40.0)
            elif avg_sm < 50.0 and avg_mem_bw < 50.0:
                bottleneck = BottleneckType.IO_BOUND
                confidence = min(
                    1.0, 1.0 - (avg_sm + avg_mem_bw) / 100.0
                )
            else:
                bottleneck = BottleneckType.BALANCED
                # Balanced has lower confidence as it's the "no clear bottleneck" case
                confidence = 0.5

            return {
                "bottleneck": bottleneck.value,
                "confidence": round(confidence, 3),
                "device_id": self._device_id,
                "gpu_sku": self._gpu_sku,
                "metrics": {
                    "avg_sm_occupancy": round(avg_sm, 2),
                    "avg_memory_bandwidth": round(avg_mem_bw, 2),
                    "avg_tensor_core": round(avg_tensor, 2),
                    "avg_temperature": round(avg_temp, 1),
                    "avg_power": round(avg_power, 1),
                },
                "samples_analyzed": len(recent),
                "analysis_window_seconds": 30.0,
            }

    def get_recommendations(self) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on current bottleneck.

        Analyzes the detected bottleneck and returns specific, prioritized
        recommendations for improving GPU utilization. These recommendations
        are tailored to LLM inference workloads on Netflix's infrastructure.

        Returns:
            List of recommendation dictionaries, sorted by impact (highest first).
        """
        bottleneck_info = self.detect_bottleneck()
        bottleneck = bottleneck_info.get("bottleneck", "insufficient_data")
        metrics = bottleneck_info.get("metrics", {})

        recommendations: List[Dict[str, Any]] = []

        if bottleneck == "insufficient_data":
            return [{
                "priority": "info",
                "category": "monitoring",
                "recommendation": (
                    "Insufficient data for analysis. Ensure the monitor "
                    "has been running for at least 30 seconds."
                ),
                "expected_impact": "N/A",
            }]

        if bottleneck == BottleneckType.MEMORY_BOUND.value:
            recommendations.extend([
                {
                    "priority": "high",
                    "category": "inference_engine",
                    "recommendation": (
                        "Enable FlashAttention-2 to reduce memory bandwidth "
                        "requirements during attention computation. This can "
                        "reduce memory reads by 5-20x for long sequences."
                    ),
                    "expected_impact": "15-30% throughput improvement",
                },
                {
                    "priority": "high",
                    "category": "model_optimization",
                    "recommendation": (
                        "Apply KV cache quantization (FP8 or INT8) to halve "
                        "the memory bandwidth consumed by cache reads during "
                        "the decode phase."
                    ),
                    "expected_impact": "20-40% decode throughput improvement",
                },
                {
                    "priority": "medium",
                    "category": "batching",
                    "recommendation": (
                        "Increase continuous batching batch size to amortize "
                        "memory access overhead across more tokens. Current "
                        f"memory BW utilization: {metrics.get('avg_memory_bandwidth', 0):.1f}%."
                    ),
                    "expected_impact": "10-25% throughput improvement",
                },
                {
                    "priority": "low",
                    "category": "hardware",
                    "recommendation": (
                        "Consider upgrading to H100 GPUs which offer 3.35 TB/s "
                        "memory bandwidth (vs 2.0 TB/s on A100), providing "
                        "~1.6x headroom for memory-bound workloads."
                    ),
                    "expected_impact": "40-60% throughput improvement",
                },
            ])

        elif bottleneck == BottleneckType.COMPUTE_BOUND.value:
            recommendations.extend([
                {
                    "priority": "high",
                    "category": "model_optimization",
                    "recommendation": (
                        "Apply weight quantization (GPTQ/AWQ INT4) to reduce "
                        "compute requirements per token. SM occupancy is high "
                        f"({metrics.get('avg_sm_occupancy', 0):.1f}%) indicating "
                        "arithmetic saturation."
                    ),
                    "expected_impact": "30-50% throughput improvement",
                },
                {
                    "priority": "medium",
                    "category": "inference_engine",
                    "recommendation": (
                        "Enable speculative decoding with a smaller draft model "
                        "to generate multiple tokens per forward pass, reducing "
                        "the total number of compute-heavy forward passes."
                    ),
                    "expected_impact": "2-3x decode speedup",
                },
                {
                    "priority": "medium",
                    "category": "parallelism",
                    "recommendation": (
                        "Increase Tensor Parallelism degree to distribute "
                        "compute across more SMs. Consider TP=8 if NVLink "
                        "bandwidth permits."
                    ),
                    "expected_impact": "Up to 2x with TP=8",
                },
            ])

        elif bottleneck == BottleneckType.IO_BOUND.value:
            recommendations.extend([
                {
                    "priority": "high",
                    "category": "batching",
                    "recommendation": (
                        "Increase request batch size to improve GPU utilization. "
                        "Both SM occupancy and memory bandwidth are low, "
                        "indicating the GPU is starved for work."
                    ),
                    "expected_impact": "2-5x throughput improvement",
                },
                {
                    "priority": "medium",
                    "category": "infrastructure",
                    "recommendation": (
                        "Enable CUDA graph capture for the decode loop to "
                        "eliminate CPU-GPU kernel launch overhead."
                    ),
                    "expected_impact": "10-30% latency reduction",
                },
                {
                    "priority": "medium",
                    "category": "data_pipeline",
                    "recommendation": (
                        "Implement request prefetching and tokenization "
                        "pipelining to ensure the GPU always has work queued."
                    ),
                    "expected_impact": "15-25% throughput improvement",
                },
            ])

        elif bottleneck == BottleneckType.THERMAL_THROTTLED.value:
            recommendations.extend([
                {
                    "priority": "critical",
                    "category": "infrastructure",
                    "recommendation": (
                        f"GPU temperature ({metrics.get('avg_temperature', 0):.1f}C) "
                        f"exceeds throttle threshold ({self._spec.throttle_temp_celsius}C). "
                        "Check datacenter cooling systems and airflow. Thermal "
                        "throttling reduces clock speeds and degrades performance."
                    ),
                    "expected_impact": "Prevent 10-30% performance degradation",
                },
                {
                    "priority": "high",
                    "category": "workload",
                    "recommendation": (
                        "Reduce sustained load on this GPU by rebalancing "
                        "traffic across the fleet. Use the device_manager "
                        "to redistribute inference requests."
                    ),
                    "expected_impact": "Immediate thermal relief",
                },
            ])

        elif bottleneck == BottleneckType.BALANCED.value:
            recommendations.append({
                "priority": "info",
                "category": "status",
                "recommendation": (
                    "GPU utilization is balanced across compute and memory "
                    "subsystems. No immediate optimization needed. Continue "
                    "monitoring for workload pattern changes."
                ),
                "expected_impact": "N/A (healthy state)",
            })

        return recommendations

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _collection_loop(self) -> None:
        """Background thread that periodically collects SM metrics.

        Runs until stop() is called. Each iteration samples the GPU's
        current state and appends it to the history ring buffer.
        """
        logger.info(
            "sm_monitor_collection_loop_started",
            extra={"device_id": self._device_id},
        )

        while self._running:
            try:
                self._collect_sample()
            except Exception:
                logger.exception(
                    "sm_monitor_collection_error",
                    extra={"device_id": self._device_id},
                )

            time.sleep(self._sample_interval)

        logger.info(
            "sm_monitor_collection_loop_stopped",
            extra={"device_id": self._device_id},
        )

    def _collect_sample(self) -> None:
        """Collect a single metric sample from the GPU or mock source."""
        with self._lock:
            if self._mock_mode:
                sample = self._generate_mock_sample()
            else:
                sample = self._collect_nvml_sample()

            self._latest_sample = sample
            self._history.append(sample)

            # Trim history to window
            cutoff = time.time() - self._history_window
            while self._history and self._history[0].timestamp < cutoff:
                self._history.popleft()

    def _collect_nvml_sample(self) -> SMSample:
        """Collect real metrics from NVIDIA Management Library.

        Uses pynvml to query the GPU driver for current utilization,
        temperature, power, and clock frequencies.

        Returns:
            An SMSample populated with real hardware metrics.
        """
        if self._nvml_handle is None:
            return self._generate_mock_sample()

        try:
            # GPU utilization (SM occupancy approximation)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(
                self._nvml_handle
            )
            sm_occupancy = float(utilization.gpu)
            memory_bw = float(utilization.memory)

            # Temperature
            temperature = float(
                pynvml.nvmlDeviceGetTemperature(
                    self._nvml_handle,
                    pynvml.NVML_TEMPERATURE_GPU,
                )
            )

            # Power draw
            power_mw = pynvml.nvmlDeviceGetPowerUsage(self._nvml_handle)
            power_watts = float(power_mw) / 1000.0

            # Clock frequencies
            gpu_clock = float(
                pynvml.nvmlDeviceGetClockInfo(
                    self._nvml_handle,
                    pynvml.NVML_CLOCK_GRAPHICS,
                )
            )
            mem_clock = float(
                pynvml.nvmlDeviceGetClockInfo(
                    self._nvml_handle,
                    pynvml.NVML_CLOCK_MEM,
                )
            )

            # Tensor core utilization is not directly available via NVML.
            # Approximate it as a fraction of GPU utilization when power
            # draw is above 70% of TDP (tensor cores are power-hungry).
            power_ratio = power_watts / self._spec.tdp_watts
            tensor_core_approx = sm_occupancy * min(1.0, power_ratio / 0.7) * 0.6

            return SMSample(
                sm_occupancy_percent=sm_occupancy,
                memory_bandwidth_utilization=memory_bw,
                tensor_core_utilization=tensor_core_approx,
                temperature_celsius=temperature,
                power_draw_watts=power_watts,
                gpu_clock_mhz=gpu_clock,
                memory_clock_mhz=mem_clock,
            )

        except Exception as e:
            logger.warning(
                "nvml_sample_failed_using_mock",
                extra={"error": str(e), "device_id": self._device_id},
            )
            return self._generate_mock_sample()

    def _generate_mock_sample(self) -> SMSample:
        """Generate a realistic synthetic metric sample.

        Uses a bounded random walk to produce time-correlated metrics
        that resemble real GPU workload patterns. The random walk is
        bounded to stay within physically plausible ranges.

        Returns:
            An SMSample with synthetic but realistic values.
        """
        # Random walk parameters
        state = self._mock_state

        def _walk(current: float, min_val: float, max_val: float, step: float) -> float:
            """Bounded random walk with mean reversion."""
            midpoint = (min_val + max_val) / 2.0
            # Slight pull toward midpoint (mean reversion)
            drift = (midpoint - current) * 0.02
            delta = random.gauss(drift, step)
            return max(min_val, min(max_val, current + delta))

        state["sm_occupancy"] = _walk(state["sm_occupancy"], 20.0, 95.0, 3.0)
        state["memory_bw"] = _walk(state["memory_bw"], 30.0, 95.0, 2.5)
        state["tensor_core"] = _walk(state["tensor_core"], 10.0, 85.0, 2.0)
        state["temperature"] = _walk(state["temperature"], 45.0, 88.0, 0.5)
        state["power"] = _walk(
            state["power"],
            self._spec.tdp_watts * 0.3,
            self._spec.tdp_watts * 0.98,
            5.0,
        )
        state["gpu_clock"] = _walk(
            state["gpu_clock"],
            self._spec.max_gpu_clock_mhz * 0.7,
            self._spec.max_gpu_clock_mhz,
            10.0,
        )
        state["mem_clock"] = _walk(
            state["mem_clock"],
            self._spec.max_memory_clock_mhz * 0.8,
            self._spec.max_memory_clock_mhz,
            5.0,
        )

        return SMSample(
            sm_occupancy_percent=state["sm_occupancy"],
            memory_bandwidth_utilization=state["memory_bw"],
            tensor_core_utilization=state["tensor_core"],
            temperature_celsius=state["temperature"],
            power_draw_watts=state["power"],
            gpu_clock_mhz=state["gpu_clock"],
            memory_clock_mhz=state["mem_clock"],
        )

    @staticmethod
    def _compute_percentile_stats(
        values: List[float],
    ) -> Dict[str, Optional[float]]:
        """Compute summary statistics for a list of metric values.

        Args:
            values: List of float measurements.

        Returns:
            Dictionary with mean, min, max, P50, P95, P99 statistics.
        """
        if not values:
            return {
                "mean": None,
                "min": None,
                "max": None,
                "p50": None,
                "p95": None,
                "p99": None,
                "count": 0,
            }

        sorted_vals = sorted(values)
        n = len(sorted_vals)

        def _percentile(pct: float) -> float:
            idx = int(pct / 100.0 * (n - 1))
            return sorted_vals[min(idx, n - 1)]

        return {
            "mean": round(statistics.mean(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "p50": round(_percentile(50.0), 2),
            "p95": round(_percentile(95.0), 2),
            "p99": round(_percentile(99.0), 2),
            "count": n,
        }

    # ------------------------------------------------------------------
    # Context Manager & Dunder
    # ------------------------------------------------------------------

    def __enter__(self) -> "SMOccupancyMonitor":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def __repr__(self) -> str:
        mode = "mock" if self._mock_mode else "nvml"
        samples = len(self._history)
        return (
            f"SMOccupancyMonitor("
            f"device={self._device_id}, "
            f"sku={self._gpu_sku}, "
            f"mode={mode}, "
            f"samples={samples})"
        )
