"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
GPU Device Manager — Multi-GPU Coordination & Health Monitoring
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Netflix's LLM inference fleet consists of hundreds of GPU instances,
    each containing multiple GPUs (typically 4x A100-80GB or 4x A10G-24GB).
    Within a single instance, the GPUs must operate as a coordinated unit
    for Tensor Parallelism (TP=4), where the model is sharded across all
    GPUs and they communicate via NVLink during each forward pass.

    The Device Manager provides:
    1. Multi-GPU initialization and readiness verification
    2. Continuous health monitoring (temperature, ECC errors, memory)
    3. Automatic failover when a GPU becomes unhealthy
    4. Fleet-level aggregation for capacity planning dashboards

DESIGN DECISIONS:
    - TP=4 default: A100 nodes have 4-way NVLink topology, making TP=4
      the natural parallelism degree. TP=8 requires 8-GPU nodes (DGX).
    - Health check thresholds calibrated from Netflix production data:
      * Temperature: WARNING at 80C, CRITICAL at 85C (thermal throttle at 83C)
      * ECC errors: WARNING at 1, CRITICAL at 10 (indicates failing DRAM)
      * Memory: WARNING at 90% utilization, CRITICAL at 95%
    - Failover strategy: When a GPU in a TP group fails, the entire group
      is taken offline and traffic is redistributed. Partial TP operation
      is not supported (would require dynamic re-sharding).

SUPPORTED SKUs:
    - NVIDIA A100-SXM4-80GB: Primary inference GPU, 4x per p4d.24xlarge
    - NVIDIA A10G-24GB: Cost-optimized, 4x per g5.12xlarge
============================================================================
"""

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Attempt to import pynvml for real GPU queries
_NVML_AVAILABLE = False
try:
    import pynvml

    _NVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]


class DeviceHealth(Enum):
    """Health status for a single GPU device.

    HEALTHY: Device is operating normally within all thresholds.
    DEGRADED: One or more WARNING thresholds exceeded. Device is still
        functional but may experience reduced performance (thermal
        throttling) or is at risk of failure (ECC errors).
    UNHEALTHY: One or more CRITICAL thresholds exceeded. Device should
        be removed from the inference pool to prevent data corruption
        (ECC) or hardware damage (thermal).
    OFFLINE: Device has been explicitly taken offline (maintenance,
        failover, or initialization failure).
    UNKNOWN: Device state has not been determined (pre-initialization).
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class GPUSku(Enum):
    """Supported GPU hardware SKUs.

    Each SKU has different memory capacity, compute performance, and
    thermal characteristics that affect health thresholds and capacity
    planning.
    """

    A100_80GB = "A100-80GB"
    A10G_24GB = "A10G-24GB"


@dataclass
class HealthThresholds:
    """Configurable thresholds for GPU health determination.

    These values are calibrated from Netflix production fleet data.
    Different GPU SKUs may need different thresholds.

    Attributes:
        temp_warning_celsius: Temperature above which device is DEGRADED.
        temp_critical_celsius: Temperature above which device is UNHEALTHY.
        ecc_errors_warning: ECC error count threshold for DEGRADED.
        ecc_errors_critical: ECC error count threshold for UNHEALTHY.
        memory_util_warning: Memory utilization % threshold for DEGRADED.
        memory_util_critical: Memory utilization % threshold for UNHEALTHY.
        power_warning_ratio: Power draw ratio of TDP for DEGRADED.
        max_consecutive_failures: Number of failed health checks before
            automatic failover.
    """

    temp_warning_celsius: float = 80.0
    temp_critical_celsius: float = 85.0
    ecc_errors_warning: int = 1
    ecc_errors_critical: int = 10
    memory_util_warning: float = 90.0
    memory_util_critical: float = 95.0
    power_warning_ratio: float = 0.95
    max_consecutive_failures: int = 3


# Default thresholds per SKU
SKU_THRESHOLDS: Dict[GPUSku, HealthThresholds] = {
    GPUSku.A100_80GB: HealthThresholds(
        temp_warning_celsius=80.0,
        temp_critical_celsius=85.0,
        ecc_errors_warning=1,
        ecc_errors_critical=10,
        memory_util_warning=90.0,
        memory_util_critical=95.0,
        power_warning_ratio=0.95,
        max_consecutive_failures=3,
    ),
    GPUSku.A10G_24GB: HealthThresholds(
        temp_warning_celsius=85.0,
        temp_critical_celsius=90.0,
        ecc_errors_warning=1,
        ecc_errors_critical=10,
        memory_util_warning=90.0,
        memory_util_critical=95.0,
        power_warning_ratio=0.95,
        max_consecutive_failures=3,
    ),
}


@dataclass
class GPUDeviceInfo:
    """Comprehensive state for a single GPU device.

    Maintained by the device manager and updated on each health check
    cycle. Includes both static hardware info and dynamic runtime state.

    Attributes:
        device_id: GPU device index (0-based).
        uuid: Unique hardware identifier from the GPU BIOS.
        name: Human-readable device name from the driver.
        sku: GPU hardware SKU classification.
        health: Current health status.
        total_memory_mb: Total GPU memory in megabytes.
        used_memory_mb: Currently used GPU memory.
        temperature_celsius: Current die temperature.
        power_draw_watts: Current power consumption.
        tdp_watts: Thermal Design Power (maximum safe sustained power).
        ecc_errors_total: Cumulative ECC error count.
        gpu_utilization_percent: SM utilization percentage.
        memory_utilization_percent: Memory controller utilization.
        pcie_gen: PCIe generation (3, 4, or 5).
        nvlink_active: Whether NVLink is active (for TP communication).
        consecutive_failures: How many health checks have failed in a row.
        last_health_check: Timestamp of most recent health check.
        assigned_tp_group: Tensor Parallelism group this device belongs to.
        is_primary: Whether this is the primary device in its TP group.
    """

    device_id: int = 0
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Unknown GPU"
    sku: GPUSku = GPUSku.A100_80GB
    health: DeviceHealth = DeviceHealth.UNKNOWN
    total_memory_mb: float = 81920.0
    used_memory_mb: float = 0.0
    temperature_celsius: float = 0.0
    power_draw_watts: float = 0.0
    tdp_watts: float = 400.0
    ecc_errors_total: int = 0
    gpu_utilization_percent: float = 0.0
    memory_utilization_percent: float = 0.0
    pcie_gen: int = 4
    nvlink_active: bool = True
    consecutive_failures: int = 0
    last_health_check: float = 0.0
    assigned_tp_group: Optional[str] = None
    is_primary: bool = False

    @property
    def memory_utilization_ratio(self) -> float:
        """Memory utilization as a fraction (0.0 to 1.0)."""
        if self.total_memory_mb == 0:
            return 0.0
        return self.used_memory_mb / self.total_memory_mb

    @property
    def power_utilization_ratio(self) -> float:
        """Power draw as a fraction of TDP."""
        if self.tdp_watts == 0:
            return 0.0
        return self.power_draw_watts / self.tdp_watts

    @property
    def free_memory_mb(self) -> float:
        """Available GPU memory in megabytes."""
        return max(0.0, self.total_memory_mb - self.used_memory_mb)


@dataclass
class TPGroup:
    """A Tensor Parallelism group of GPUs operating as a single logical unit.

    In TP, the model's weight matrices are sharded across the group's GPUs.
    All GPUs in the group must process the same request simultaneously and
    communicate via NVLink all-reduce operations during each forward pass.

    Attributes:
        group_id: Unique identifier for this TP group.
        device_ids: GPU device indices in this group.
        tp_degree: Number of GPUs in the group.
        health: Aggregate health (worst of any member device).
        is_active: Whether this group is accepting inference requests.
        created_at: Timestamp of group creation.
        total_requests_served: Cumulative request count for observability.
    """

    group_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_ids: List[int] = field(default_factory=list)
    tp_degree: int = 4
    health: DeviceHealth = DeviceHealth.UNKNOWN
    is_active: bool = False
    created_at: float = field(default_factory=time.time)
    total_requests_served: int = 0


class GPUDeviceManager:
    """Manages multi-GPU coordination for Tensor Parallel inference.

    Provides device initialization, health monitoring, TP group management,
    automatic failover, and fleet-level status aggregation. Designed for
    Netflix's inference fleet running on p4d.24xlarge (4x A100) and
    g5.12xlarge (4x A10G) instances.

    Thread Safety:
        All public methods are thread-safe. The health monitoring thread
        runs independently and updates device state atomically.

    Example:
        >>> manager = GPUDeviceManager(num_devices=4, gpu_sku=GPUSku.A100_80GB)
        >>> manager.initialize_devices()
        >>> status = manager.get_device_status()
        >>> group = manager.allocate_for_inference()
        >>> health = manager.health_check()
        >>> manager.shutdown()
    """

    # SKU-specific hardware specifications
    SKU_SPECS: Dict[GPUSku, Dict[str, Any]] = {
        GPUSku.A100_80GB: {
            "name": "NVIDIA A100-SXM4-80GB",
            "total_memory_mb": 81920.0,
            "tdp_watts": 400.0,
            "pcie_gen": 4,
            "nvlink_bandwidth_gbps": 600.0,
            "num_sms": 108,
            "fp16_tflops": 312.0,
        },
        GPUSku.A10G_24GB: {
            "name": "NVIDIA A10G",
            "total_memory_mb": 24576.0,
            "tdp_watts": 150.0,
            "pcie_gen": 4,
            "nvlink_bandwidth_gbps": 0.0,  # A10G has no NVLink
            "num_sms": 80,
            "fp16_tflops": 125.0,
        },
    }

    def __init__(
        self,
        num_devices: int = 4,
        gpu_sku: GPUSku = GPUSku.A100_80GB,
        tp_degree: int = 4,
        mock_mode: Optional[bool] = None,
        health_check_interval_seconds: float = 10.0,
        thresholds: Optional[HealthThresholds] = None,
    ) -> None:
        """Initialize the GPU device manager.

        Args:
            num_devices: Number of GPU devices on this instance.
            gpu_sku: GPU hardware SKU for all devices on this instance.
            tp_degree: Tensor Parallelism degree (GPUs per TP group).
                Must evenly divide num_devices.
            mock_mode: If True, simulate GPU state. If None, auto-detect.
            health_check_interval_seconds: Time between health check cycles.
            thresholds: Custom health thresholds. If None, uses SKU defaults.
        """
        if num_devices % tp_degree != 0:
            raise ValueError(
                f"num_devices ({num_devices}) must be evenly divisible by "
                f"tp_degree ({tp_degree})"
            )

        self._num_devices: int = num_devices
        self._gpu_sku: GPUSku = gpu_sku
        self._tp_degree: int = tp_degree
        self._health_check_interval: float = health_check_interval_seconds

        # Auto-detect mock mode
        if mock_mode is None:
            self._mock_mode = not _NVML_AVAILABLE
        else:
            self._mock_mode = mock_mode

        # Health thresholds
        self._thresholds: HealthThresholds = (
            thresholds
            if thresholds is not None
            else SKU_THRESHOLDS.get(gpu_sku, HealthThresholds())
        )

        # Device state
        self._devices: Dict[int, GPUDeviceInfo] = {}
        self._tp_groups: Dict[str, TPGroup] = {}

        # NVML handles
        self._nvml_handles: Dict[int, Any] = {}
        self._nvml_initialized: bool = False

        # Failover tracking
        self._failed_devices: Set[int] = set()
        self._failover_history: List[Dict[str, Any]] = []

        # Health monitoring thread
        self._health_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Thread safety
        self._lock: threading.RLock = threading.RLock()

        # Initialized flag
        self._initialized: bool = False

        logger.info(
            "gpu_device_manager_created",
            extra={
                "num_devices": num_devices,
                "gpu_sku": gpu_sku.value,
                "tp_degree": tp_degree,
                "mock_mode": self._mock_mode,
            },
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize_devices(self) -> Dict[str, Any]:
        """Initialize all GPU devices and form Tensor Parallelism groups.

        Performs the following steps:
        1. Initialize NVML (if not in mock mode)
        2. Query each device for hardware info and initial state
        3. Form TP groups from healthy devices
        4. Start the background health monitoring thread

        Returns:
            Initialization summary with device count, group count,
            and any devices that failed to initialize.

        Raises:
            RuntimeError: If fewer than tp_degree devices are healthy
                (cannot form even one TP group).
        """
        with self._lock:
            if self._initialized:
                logger.warning("gpu_device_manager_already_initialized")
                return {"status": "already_initialized"}

            # Initialize NVML
            if not self._mock_mode and _NVML_AVAILABLE:
                try:
                    pynvml.nvmlInit()
                    self._nvml_initialized = True
                    logger.info("nvml_initialized_for_device_manager")
                except Exception as e:
                    logger.warning(
                        "nvml_init_failed_using_mock",
                        extra={"error": str(e)},
                    )
                    self._mock_mode = True

            # Initialize each device
            failed_devices: List[int] = []
            for device_id in range(self._num_devices):
                try:
                    device_info = self._initialize_single_device(device_id)
                    self._devices[device_id] = device_info
                    logger.info(
                        "gpu_device_initialized",
                        extra={
                            "device_id": device_id,
                            "name": device_info.name,
                            "memory_mb": device_info.total_memory_mb,
                            "health": device_info.health.value,
                        },
                    )
                except Exception as e:
                    logger.error(
                        "gpu_device_initialization_failed",
                        extra={
                            "device_id": device_id,
                            "error": str(e),
                        },
                    )
                    # Create an offline device entry
                    self._devices[device_id] = GPUDeviceInfo(
                        device_id=device_id,
                        name=f"FAILED-{device_id}",
                        sku=self._gpu_sku,
                        health=DeviceHealth.OFFLINE,
                    )
                    failed_devices.append(device_id)
                    self._failed_devices.add(device_id)

            # Form TP groups from healthy devices
            healthy_devices = [
                did
                for did, dev in self._devices.items()
                if dev.health in (DeviceHealth.HEALTHY, DeviceHealth.DEGRADED)
            ]

            if len(healthy_devices) < self._tp_degree:
                raise RuntimeError(
                    f"Cannot form a TP group: only {len(healthy_devices)} "
                    f"healthy devices available, need {self._tp_degree}. "
                    f"Failed devices: {failed_devices}"
                )

            # Create TP groups from consecutive healthy devices
            num_groups = len(healthy_devices) // self._tp_degree
            for i in range(num_groups):
                start = i * self._tp_degree
                group_devices = healthy_devices[start : start + self._tp_degree]
                self._create_tp_group(group_devices)

            # Start health monitoring
            self._running = True
            self._health_thread = threading.Thread(
                target=self._health_monitoring_loop,
                name="gpu-health-monitor",
                daemon=True,
            )
            self._health_thread.start()

            self._initialized = True

            result = {
                "status": "initialized",
                "total_devices": self._num_devices,
                "healthy_devices": len(healthy_devices),
                "failed_devices": failed_devices,
                "tp_groups_formed": num_groups,
                "tp_degree": self._tp_degree,
                "gpu_sku": self._gpu_sku.value,
                "mock_mode": self._mock_mode,
            }

            logger.info(
                "gpu_device_manager_initialized",
                extra=result,
            )

            return result

    def shutdown(self) -> None:
        """Shut down the device manager and release all resources.

        Stops the health monitoring thread, deactivates all TP groups,
        and shuts down NVML. Should be called during application shutdown.
        """
        with self._lock:
            self._running = False

        if self._health_thread is not None:
            self._health_thread.join(timeout=self._health_check_interval * 2)
            self._health_thread = None

        # Deactivate all TP groups
        with self._lock:
            for group in self._tp_groups.values():
                group.is_active = False

        # Shutdown NVML
        if self._nvml_initialized and _NVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
                self._nvml_initialized = False
            except Exception:
                pass

        logger.info("gpu_device_manager_shutdown")

    # ------------------------------------------------------------------
    # Device Status
    # ------------------------------------------------------------------

    def get_device_status(
        self, device_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Get the current status of one or all GPU devices.

        Args:
            device_id: Specific device to query. If None, returns all.

        Returns:
            Dictionary with device status information.
        """
        with self._lock:
            if device_id is not None:
                device = self._devices.get(device_id)
                if device is None:
                    return {"error": f"Device {device_id} not found"}
                return self._device_to_dict(device)

            return {
                "devices": {
                    did: self._device_to_dict(dev)
                    for did, dev in self._devices.items()
                },
                "total_devices": self._num_devices,
                "healthy_count": sum(
                    1
                    for d in self._devices.values()
                    if d.health == DeviceHealth.HEALTHY
                ),
                "degraded_count": sum(
                    1
                    for d in self._devices.values()
                    if d.health == DeviceHealth.DEGRADED
                ),
                "unhealthy_count": sum(
                    1
                    for d in self._devices.values()
                    if d.health == DeviceHealth.UNHEALTHY
                ),
                "offline_count": sum(
                    1
                    for d in self._devices.values()
                    if d.health == DeviceHealth.OFFLINE
                ),
            }

    # ------------------------------------------------------------------
    # Inference Allocation
    # ------------------------------------------------------------------

    def allocate_for_inference(self) -> Optional[Dict[str, Any]]:
        """Allocate a TP group for an inference request.

        Selects the most available (lowest utilization) active TP group
        and returns its device assignments. The caller uses this information
        to route the inference request to the correct set of GPUs.

        Returns:
            Dictionary with TP group info and device assignments, or None
            if no healthy TP group is available.
        """
        with self._lock:
            # Find the active TP group with the lowest utilization
            best_group: Optional[TPGroup] = None
            best_utilization: float = float("inf")

            for group in self._tp_groups.values():
                if not group.is_active:
                    continue

                # Average GPU utilization across the group
                avg_util = self._group_utilization(group)
                if avg_util < best_utilization:
                    best_utilization = avg_util
                    best_group = group

            if best_group is None:
                logger.error(
                    "no_active_tp_group_available",
                    extra={
                        "total_groups": len(self._tp_groups),
                        "failed_devices": list(self._failed_devices),
                    },
                )
                return None

            best_group.total_requests_served += 1

            return {
                "group_id": best_group.group_id,
                "device_ids": best_group.device_ids,
                "tp_degree": best_group.tp_degree,
                "health": best_group.health.value,
                "avg_utilization": round(best_utilization, 2),
                "total_requests_served": best_group.total_requests_served,
                "devices": [
                    {
                        "device_id": did,
                        "memory_free_mb": round(
                            self._devices[did].free_memory_mb, 2
                        ),
                        "gpu_utilization": round(
                            self._devices[did].gpu_utilization_percent, 2
                        ),
                    }
                    for did in best_group.device_ids
                    if did in self._devices
                ],
            }

    # ------------------------------------------------------------------
    # Health Checking
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """Run an immediate health check on all devices.

        Queries each device for current metrics and evaluates them
        against the configured health thresholds. Updates device health
        status and triggers failover if necessary.

        Returns:
            Health check results for all devices, including any failover
            actions taken.
        """
        with self._lock:
            results: Dict[str, Any] = {
                "timestamp": time.time(),
                "devices": {},
                "failover_actions": [],
            }

            for device_id, device in self._devices.items():
                if device.health == DeviceHealth.OFFLINE:
                    results["devices"][device_id] = {
                        "health": DeviceHealth.OFFLINE.value,
                        "reason": "device_offline",
                    }
                    continue

                try:
                    # Collect current metrics
                    self._update_device_metrics(device_id)
                    device = self._devices[device_id]

                    # Evaluate health
                    health, reasons = self._evaluate_device_health(device)
                    old_health = device.health
                    device.health = health
                    device.last_health_check = time.time()

                    # Track consecutive failures
                    if health == DeviceHealth.UNHEALTHY:
                        device.consecutive_failures += 1
                    else:
                        device.consecutive_failures = 0

                    results["devices"][device_id] = {
                        "health": health.value,
                        "previous_health": old_health.value,
                        "reasons": reasons,
                        "temperature": device.temperature_celsius,
                        "ecc_errors": device.ecc_errors_total,
                        "memory_utilization": round(
                            device.memory_utilization_ratio * 100, 2
                        ),
                        "gpu_utilization": device.gpu_utilization_percent,
                        "consecutive_failures": device.consecutive_failures,
                    }

                    # Trigger failover if needed
                    if (
                        device.consecutive_failures
                        >= self._thresholds.max_consecutive_failures
                        and device_id not in self._failed_devices
                    ):
                        failover_result = self._failover_device(device_id)
                        results["failover_actions"].append(failover_result)

                    # Log health transitions
                    if old_health != health:
                        logger.warning(
                            "gpu_health_transition",
                            extra={
                                "device_id": device_id,
                                "old_health": old_health.value,
                                "new_health": health.value,
                                "reasons": reasons,
                            },
                        )

                except Exception as e:
                    logger.exception(
                        "health_check_failed",
                        extra={"device_id": device_id, "error": str(e)},
                    )
                    results["devices"][device_id] = {
                        "health": "error",
                        "error": str(e),
                    }

            # Update TP group health
            self._update_tp_group_health()

            return results

    # ------------------------------------------------------------------
    # Fleet Summary
    # ------------------------------------------------------------------

    def get_fleet_summary(self) -> Dict[str, Any]:
        """Get a comprehensive summary of the GPU fleet on this instance.

        Provides aggregate metrics for capacity planning, dashboarding,
        and the control plane's fleet orchestration decisions.

        Returns:
            Dictionary with fleet-level metrics and per-group breakdowns.
        """
        with self._lock:
            total_memory_mb = sum(
                d.total_memory_mb for d in self._devices.values()
            )
            used_memory_mb = sum(
                d.used_memory_mb for d in self._devices.values()
            )
            avg_temp = (
                sum(d.temperature_celsius for d in self._devices.values())
                / max(len(self._devices), 1)
            )
            avg_power = (
                sum(d.power_draw_watts for d in self._devices.values())
                / max(len(self._devices), 1)
            )
            total_ecc = sum(
                d.ecc_errors_total for d in self._devices.values()
            )
            avg_gpu_util = (
                sum(
                    d.gpu_utilization_percent for d in self._devices.values()
                )
                / max(len(self._devices), 1)
            )

            health_counts: Dict[str, int] = {}
            for device in self._devices.values():
                key = device.health.value
                health_counts[key] = health_counts.get(key, 0) + 1

            tp_group_summaries = []
            for group in self._tp_groups.values():
                tp_group_summaries.append({
                    "group_id": group.group_id,
                    "device_ids": group.device_ids,
                    "tp_degree": group.tp_degree,
                    "health": group.health.value,
                    "is_active": group.is_active,
                    "total_requests_served": group.total_requests_served,
                    "avg_utilization": round(
                        self._group_utilization(group), 2
                    ),
                })

            sku_spec = self.SKU_SPECS.get(self._gpu_sku, {})

            return {
                "instance_summary": {
                    "gpu_sku": self._gpu_sku.value,
                    "gpu_name": sku_spec.get("name", "Unknown"),
                    "num_devices": self._num_devices,
                    "tp_degree": self._tp_degree,
                    "num_tp_groups": len(self._tp_groups),
                    "active_tp_groups": sum(
                        1 for g in self._tp_groups.values() if g.is_active
                    ),
                    "mock_mode": self._mock_mode,
                    "initialized": self._initialized,
                },
                "memory": {
                    "total_mb": round(total_memory_mb, 2),
                    "used_mb": round(used_memory_mb, 2),
                    "free_mb": round(total_memory_mb - used_memory_mb, 2),
                    "utilization": round(
                        used_memory_mb / max(total_memory_mb, 1) * 100, 2
                    ),
                },
                "compute": {
                    "avg_gpu_utilization": round(avg_gpu_util, 2),
                    "peak_fp16_tflops_per_gpu": sku_spec.get(
                        "fp16_tflops", 0
                    ),
                    "total_fp16_tflops": round(
                        sku_spec.get("fp16_tflops", 0) * self._num_devices, 1
                    ),
                },
                "thermal": {
                    "avg_temperature_celsius": round(avg_temp, 1),
                    "avg_power_watts": round(avg_power, 1),
                    "total_power_watts": round(
                        avg_power * self._num_devices, 1
                    ),
                    "tdp_per_gpu_watts": sku_spec.get("tdp_watts", 0),
                },
                "reliability": {
                    "health_distribution": health_counts,
                    "total_ecc_errors": total_ecc,
                    "failed_devices": list(self._failed_devices),
                    "failover_count": len(self._failover_history),
                },
                "tp_groups": tp_group_summaries,
            }

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _initialize_single_device(self, device_id: int) -> GPUDeviceInfo:
        """Initialize a single GPU device and query its hardware info.

        Args:
            device_id: The GPU device index.

        Returns:
            A fully populated GPUDeviceInfo for the device.
        """
        sku_spec = self.SKU_SPECS.get(self._gpu_sku, {})

        if self._mock_mode:
            return GPUDeviceInfo(
                device_id=device_id,
                uuid=str(uuid.uuid4()),
                name=sku_spec.get("name", f"Mock-GPU-{device_id}"),
                sku=self._gpu_sku,
                health=DeviceHealth.HEALTHY,
                total_memory_mb=sku_spec.get("total_memory_mb", 81920.0),
                used_memory_mb=0.0,
                temperature_celsius=55.0 + device_id * 2.0,
                power_draw_watts=sku_spec.get("tdp_watts", 400.0) * 0.3,
                tdp_watts=sku_spec.get("tdp_watts", 400.0),
                ecc_errors_total=0,
                gpu_utilization_percent=0.0,
                memory_utilization_percent=0.0,
                pcie_gen=sku_spec.get("pcie_gen", 4),
                nvlink_active=sku_spec.get("nvlink_bandwidth_gbps", 0) > 0,
                last_health_check=time.time(),
            )

        # Real NVML initialization
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_id)
        self._nvml_handles[device_id] = handle

        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8")

        gpu_uuid = pynvml.nvmlDeviceGetUUID(handle)
        if isinstance(gpu_uuid, bytes):
            gpu_uuid = gpu_uuid.decode("utf-8")

        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        temperature = pynvml.nvmlDeviceGetTemperature(
            handle, pynvml.NVML_TEMPERATURE_GPU
        )
        power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
        utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)

        # Query ECC errors (volatile, since last reset)
        try:
            ecc_errors = pynvml.nvmlDeviceGetTotalEccErrors(
                handle,
                pynvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED,
                pynvml.NVML_VOLATILE_ECC,
            )
        except Exception:
            ecc_errors = 0

        return GPUDeviceInfo(
            device_id=device_id,
            uuid=gpu_uuid,
            name=name,
            sku=self._gpu_sku,
            health=DeviceHealth.HEALTHY,
            total_memory_mb=mem_info.total / (1024 * 1024),
            used_memory_mb=mem_info.used / (1024 * 1024),
            temperature_celsius=float(temperature),
            power_draw_watts=power,
            tdp_watts=sku_spec.get("tdp_watts", 400.0),
            ecc_errors_total=ecc_errors,
            gpu_utilization_percent=float(utilization.gpu),
            memory_utilization_percent=float(utilization.memory),
            pcie_gen=sku_spec.get("pcie_gen", 4),
            nvlink_active=sku_spec.get("nvlink_bandwidth_gbps", 0) > 0,
            last_health_check=time.time(),
        )

    def _update_device_metrics(self, device_id: int) -> None:
        """Refresh the runtime metrics for a single device.

        Args:
            device_id: The device to update.
        """
        device = self._devices.get(device_id)
        if device is None:
            return

        if self._mock_mode:
            self._update_mock_metrics(device)
            return

        handle = self._nvml_handles.get(device_id)
        if handle is None:
            return

        try:
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            device.used_memory_mb = mem_info.used / (1024 * 1024)
            device.memory_utilization_percent = (
                device.used_memory_mb / device.total_memory_mb * 100
                if device.total_memory_mb > 0
                else 0.0
            )

            device.temperature_celsius = float(
                pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU
                )
            )

            device.power_draw_watts = (
                pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            )

            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            device.gpu_utilization_percent = float(utilization.gpu)

            try:
                device.ecc_errors_total = pynvml.nvmlDeviceGetTotalEccErrors(
                    handle,
                    pynvml.NVML_MEMORY_ERROR_TYPE_UNCORRECTED,
                    pynvml.NVML_VOLATILE_ECC,
                )
            except Exception:
                pass

        except Exception as e:
            logger.warning(
                "nvml_metrics_update_failed",
                extra={"device_id": device_id, "error": str(e)},
            )

    def _update_mock_metrics(self, device: GPUDeviceInfo) -> None:
        """Generate synthetic metrics for mock mode.

        Produces slowly varying metrics that simulate a real GPU under
        moderate inference workload.

        Args:
            device: The device info to update.
        """
        # Simulate gradual metric drift
        device.temperature_celsius += random.gauss(0, 0.3)
        device.temperature_celsius = max(
            40.0, min(90.0, device.temperature_celsius)
        )

        device.gpu_utilization_percent += random.gauss(0, 2.0)
        device.gpu_utilization_percent = max(
            0.0, min(100.0, device.gpu_utilization_percent)
        )

        # Memory usage correlates with GPU utilization
        target_mem_pct = device.gpu_utilization_percent * 0.8
        device.memory_utilization_percent += (
            target_mem_pct - device.memory_utilization_percent
        ) * 0.1
        device.memory_utilization_percent = max(
            0.0, min(100.0, device.memory_utilization_percent)
        )
        device.used_memory_mb = (
            device.total_memory_mb * device.memory_utilization_percent / 100.0
        )

        # Power correlates with utilization
        target_power = device.tdp_watts * (
            0.3 + 0.6 * device.gpu_utilization_percent / 100.0
        )
        device.power_draw_watts += (
            target_power - device.power_draw_watts
        ) * 0.15
        device.power_draw_watts = max(
            0.0, min(device.tdp_watts, device.power_draw_watts)
        )

    def _evaluate_device_health(
        self, device: GPUDeviceInfo
    ) -> Tuple[DeviceHealth, List[str]]:
        """Evaluate a device's health based on current metrics and thresholds.

        Args:
            device: The device to evaluate.

        Returns:
            Tuple of (health_status, list_of_reasons).
        """
        reasons: List[str] = []
        is_critical = False
        is_degraded = False

        # Temperature check
        if device.temperature_celsius >= self._thresholds.temp_critical_celsius:
            reasons.append(
                f"CRITICAL: Temperature {device.temperature_celsius:.1f}C "
                f">= {self._thresholds.temp_critical_celsius}C"
            )
            is_critical = True
        elif device.temperature_celsius >= self._thresholds.temp_warning_celsius:
            reasons.append(
                f"WARNING: Temperature {device.temperature_celsius:.1f}C "
                f">= {self._thresholds.temp_warning_celsius}C"
            )
            is_degraded = True

        # ECC errors check
        if device.ecc_errors_total >= self._thresholds.ecc_errors_critical:
            reasons.append(
                f"CRITICAL: ECC errors {device.ecc_errors_total} "
                f">= {self._thresholds.ecc_errors_critical}"
            )
            is_critical = True
        elif device.ecc_errors_total >= self._thresholds.ecc_errors_warning:
            reasons.append(
                f"WARNING: ECC errors {device.ecc_errors_total} "
                f">= {self._thresholds.ecc_errors_warning}"
            )
            is_degraded = True

        # Memory utilization check
        mem_util_pct = device.memory_utilization_ratio * 100
        if mem_util_pct >= self._thresholds.memory_util_critical:
            reasons.append(
                f"CRITICAL: Memory utilization {mem_util_pct:.1f}% "
                f">= {self._thresholds.memory_util_critical}%"
            )
            is_critical = True
        elif mem_util_pct >= self._thresholds.memory_util_warning:
            reasons.append(
                f"WARNING: Memory utilization {mem_util_pct:.1f}% "
                f">= {self._thresholds.memory_util_warning}%"
            )
            is_degraded = True

        # Power check
        if device.power_utilization_ratio >= self._thresholds.power_warning_ratio:
            reasons.append(
                f"WARNING: Power draw {device.power_draw_watts:.1f}W "
                f"({device.power_utilization_ratio:.0%} of TDP)"
            )
            is_degraded = True

        if is_critical:
            return DeviceHealth.UNHEALTHY, reasons
        elif is_degraded:
            return DeviceHealth.DEGRADED, reasons
        else:
            return DeviceHealth.HEALTHY, ["All metrics within normal range"]

    def _create_tp_group(self, device_ids: List[int]) -> TPGroup:
        """Create a new Tensor Parallelism group.

        Args:
            device_ids: GPU device indices to include in the group.

        Returns:
            The newly created TPGroup.
        """
        group = TPGroup(
            device_ids=device_ids,
            tp_degree=len(device_ids),
            health=DeviceHealth.HEALTHY,
            is_active=True,
        )

        self._tp_groups[group.group_id] = group

        # Assign devices to the group
        for i, did in enumerate(device_ids):
            device = self._devices.get(did)
            if device is not None:
                device.assigned_tp_group = group.group_id
                device.is_primary = (i == 0)

        logger.info(
            "tp_group_created",
            extra={
                "group_id": group.group_id,
                "device_ids": device_ids,
                "tp_degree": group.tp_degree,
            },
        )

        return group

    def _failover_device(self, device_id: int) -> Dict[str, Any]:
        """Handle failover for an unhealthy GPU device.

        When a device in a TP group fails, the entire group must be taken
        offline because Tensor Parallelism requires all shards to be
        available for each forward pass. Traffic is redistributed to
        remaining active TP groups.

        Args:
            device_id: The device that has failed.

        Returns:
            Failover action summary.
        """
        device = self._devices.get(device_id)
        if device is None:
            return {"error": f"Device {device_id} not found"}

        self._failed_devices.add(device_id)
        device.health = DeviceHealth.OFFLINE

        # Find and deactivate the affected TP group
        affected_group_id: Optional[str] = device.assigned_tp_group
        affected_group: Optional[TPGroup] = None

        if affected_group_id:
            affected_group = self._tp_groups.get(affected_group_id)
            if affected_group:
                affected_group.is_active = False
                affected_group.health = DeviceHealth.OFFLINE

                # Mark all devices in the group as offline
                for did in affected_group.device_ids:
                    group_device = self._devices.get(did)
                    if group_device:
                        group_device.health = DeviceHealth.OFFLINE
                        self._failed_devices.add(did)

        failover_record = {
            "timestamp": time.time(),
            "failed_device_id": device_id,
            "affected_tp_group": affected_group_id,
            "affected_devices": (
                affected_group.device_ids if affected_group else [device_id]
            ),
            "remaining_active_groups": sum(
                1 for g in self._tp_groups.values() if g.is_active
            ),
            "reason": (
                f"Device {device_id} exceeded "
                f"{self._thresholds.max_consecutive_failures} "
                f"consecutive health check failures"
            ),
        }

        self._failover_history.append(failover_record)

        logger.critical(
            "gpu_device_failover",
            extra=failover_record,
        )

        return failover_record

    def _update_tp_group_health(self) -> None:
        """Update the aggregate health status of all TP groups.

        A TP group's health is the worst health status among its member
        devices. If any device is UNHEALTHY, the entire group is UNHEALTHY.
        """
        for group in self._tp_groups.values():
            if not group.is_active:
                continue

            worst_health = DeviceHealth.HEALTHY
            health_priority = {
                DeviceHealth.HEALTHY: 0,
                DeviceHealth.DEGRADED: 1,
                DeviceHealth.UNHEALTHY: 2,
                DeviceHealth.OFFLINE: 3,
                DeviceHealth.UNKNOWN: 4,
            }

            for did in group.device_ids:
                device = self._devices.get(did)
                if device is None:
                    worst_health = DeviceHealth.UNKNOWN
                    break
                if health_priority.get(
                    device.health, 0
                ) > health_priority.get(worst_health, 0):
                    worst_health = device.health

            group.health = worst_health

    def _group_utilization(self, group: TPGroup) -> float:
        """Calculate the average GPU utilization across a TP group.

        Args:
            group: The TP group to evaluate.

        Returns:
            Average GPU utilization percentage (0-100).
        """
        if not group.device_ids:
            return 0.0

        total_util = 0.0
        count = 0
        for did in group.device_ids:
            device = self._devices.get(did)
            if device is not None:
                total_util += device.gpu_utilization_percent
                count += 1

        return total_util / max(count, 1)

    def _health_monitoring_loop(self) -> None:
        """Background thread that periodically runs health checks.

        Runs until shutdown() is called. Each iteration performs a full
        health check cycle across all devices and handles any failover
        actions.
        """
        logger.info("gpu_health_monitoring_started")

        while self._running:
            try:
                self.health_check()
            except Exception:
                logger.exception("health_monitoring_loop_error")

            time.sleep(self._health_check_interval)

        logger.info("gpu_health_monitoring_stopped")

    def _device_to_dict(self, device: GPUDeviceInfo) -> Dict[str, Any]:
        """Convert a GPUDeviceInfo to a serializable dictionary.

        Args:
            device: The device info to serialize.

        Returns:
            Dictionary representation of the device state.
        """
        return {
            "device_id": device.device_id,
            "uuid": device.uuid,
            "name": device.name,
            "sku": device.sku.value,
            "health": device.health.value,
            "memory": {
                "total_mb": round(device.total_memory_mb, 2),
                "used_mb": round(device.used_memory_mb, 2),
                "free_mb": round(device.free_memory_mb, 2),
                "utilization_percent": round(
                    device.memory_utilization_ratio * 100, 2
                ),
            },
            "compute": {
                "gpu_utilization_percent": round(
                    device.gpu_utilization_percent, 2
                ),
            },
            "thermal": {
                "temperature_celsius": round(
                    device.temperature_celsius, 1
                ),
                "power_draw_watts": round(device.power_draw_watts, 1),
                "tdp_watts": device.tdp_watts,
                "power_utilization_percent": round(
                    device.power_utilization_ratio * 100, 1
                ),
            },
            "reliability": {
                "ecc_errors_total": device.ecc_errors_total,
                "consecutive_failures": device.consecutive_failures,
                "nvlink_active": device.nvlink_active,
                "pcie_gen": device.pcie_gen,
            },
            "assignment": {
                "tp_group": device.assigned_tp_group,
                "is_primary": device.is_primary,
            },
            "last_health_check": device.last_health_check,
        }

    # ------------------------------------------------------------------
    # Context Manager & Dunder
    # ------------------------------------------------------------------

    def __enter__(self) -> "GPUDeviceManager":
        self.initialize_devices()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def __repr__(self) -> str:
        active_groups = sum(
            1 for g in self._tp_groups.values() if g.is_active
        )
        return (
            f"GPUDeviceManager("
            f"devices={self._num_devices}, "
            f"sku={self._gpu_sku.value}, "
            f"tp={self._tp_degree}, "
            f"active_groups={active_groups}, "
            f"initialized={self._initialized})"
        )
