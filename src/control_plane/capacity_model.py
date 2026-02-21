"""Capacity planning and forecasting for the GPU inference fleet.

Uses workload characteristics (tokens/sec, prompt length, concurrency)
to compute the required number of GPUs. Supports peak-load modeling,
region-aware distribution, and cost-integrated planning.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Region(Enum):
    """Supported deployment regions."""

    US_EAST_1 = "us-east-1"
    US_WEST_2 = "us-west-2"
    EU_WEST_1 = "eu-west-1"
    AP_SOUTHEAST_1 = "ap-southeast-1"


@dataclass(frozen=True)
class GPUSpec:
    """Hardware specification for a single GPU SKU."""

    name: str
    tokens_per_second: float
    memory_gb: float
    cost_per_hour: float


# Reference GPU specifications used for capacity math.
DEFAULT_GPU_SPECS: dict[str, GPUSpec] = {
    "A100_80GB": GPUSpec(
        name="A100 80GB",
        tokens_per_second=2400.0,
        memory_gb=80.0,
        cost_per_hour=3.50,
    ),
    "A10G_24GB": GPUSpec(
        name="A10G 24GB",
        tokens_per_second=800.0,
        memory_gb=24.0,
        cost_per_hour=1.10,
    ),
    "H100": GPUSpec(
        name="H100 80GB",
        tokens_per_second=4800.0,
        memory_gb=80.0,
        cost_per_hour=5.50,
    ),
}


@dataclass
class WorkloadProfile:
    """Describes an inference workload for capacity planning.

    Attributes:
        tokens_per_second: Aggregate token throughput required.
        avg_prompt_length: Average number of tokens per prompt.
        concurrency: Number of simultaneous requests.
        gpu_sku: Key into ``DEFAULT_GPU_SPECS`` for the target GPU type.
    """

    tokens_per_second: float
    avg_prompt_length: float
    concurrency: int
    gpu_sku: str = "A100_80GB"


@dataclass
class CapacityReport:
    """Structured output of a capacity calculation."""

    required_gpus: int
    gpu_sku: str
    tokens_per_second: float
    concurrency: int
    utilization_target: float
    estimated_hourly_cost: float
    estimated_monthly_cost: float
    headroom_pct: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class DemandForecast:
    """Projected demand for a future time window."""

    hours_ahead: int
    base_demand_gpus: int
    peak_multiplier: float
    forecasted_gpus: int
    confidence: float
    region_distribution: dict[str, int]
    timestamp: float = field(default_factory=time.time)


# Hour-of-week traffic multipliers (0 = Monday 00:00 UTC).
# Friday 20:00 UTC ~ index 116 is the global peak at 3x baseline.
_HOURLY_MULTIPLIERS: dict[int, float] = {
    116: 3.0,  # Friday 20:00
    117: 2.8,  # Friday 21:00
    118: 2.5,  # Friday 22:00
    115: 2.6,  # Friday 19:00
    140: 2.4,  # Saturday 20:00
    141: 2.3,  # Saturday 21:00
    164: 2.2,  # Sunday 20:00
}


def _traffic_multiplier(hour_of_week: int) -> float:
    """Return the traffic multiplier for a given hour of the week."""
    return _HOURLY_MULTIPLIERS.get(hour_of_week, 1.0)


class CapacityModel:
    """Capacity planning engine for the GPU inference fleet.

    The core formula is:

        GPU_Required = (Tokens_per_sec * Avg_Prompt_Length * Concurrency)
                       / Tokens_per_sec_per_GPU

    A configurable headroom factor (default 20 %) is applied on top of the
    raw requirement to absorb burst traffic without triggering autoscaling.

    Args:
        gpu_specs: GPU catalog keyed by SKU identifier.
        headroom_pct: Extra capacity fraction above the raw calculation.
        region_weights: Mapping of ``Region`` to its share of total traffic
            (values should sum to 1.0).
    """

    def __init__(
        self,
        gpu_specs: Optional[dict[str, GPUSpec]] = None,
        headroom_pct: float = 0.20,
        region_weights: Optional[dict[Region, float]] = None,
    ) -> None:
        self._gpu_specs = gpu_specs or dict(DEFAULT_GPU_SPECS)
        self._headroom_pct = headroom_pct
        self._region_weights = region_weights or {
            Region.US_EAST_1: 0.40,
            Region.US_WEST_2: 0.30,
            Region.EU_WEST_1: 0.20,
            Region.AP_SOUTHEAST_1: 0.10,
        }
        self._history: list[CapacityReport] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_required_gpus(self, workload: WorkloadProfile) -> int:
        """Compute the number of GPUs needed for a given workload.

        Args:
            workload: The workload profile to size for.

        Returns:
            Number of GPUs required (including headroom).

        Raises:
            ValueError: If the requested GPU SKU is not in the catalog.
        """
        spec = self._resolve_spec(workload.gpu_sku)
        raw = (
            workload.tokens_per_second
            * workload.avg_prompt_length
            * workload.concurrency
        ) / spec.tokens_per_second
        with_headroom = raw * (1.0 + self._headroom_pct)
        return max(1, math.ceil(with_headroom))

    def forecast_demand(self, hours_ahead: int = 24) -> DemandForecast:
        """Forecast GPU demand for an upcoming time window.

        Uses hour-of-week traffic multipliers to estimate peak demand
        within the forecast window.

        Args:
            hours_ahead: Number of hours into the future to forecast.

        Returns:
            A ``DemandForecast`` with the projected GPU count.
        """
        if hours_ahead < 1:
            raise ValueError("hours_ahead must be >= 1")

        # Derive current hour-of-week (Monday 00:00 = 0).
        now = time.time()
        struct = time.gmtime(now)
        current_hour_of_week = struct.tm_wday * 24 + struct.tm_hour

        # Find the peak multiplier within the forecast window.
        peak_mult = 1.0
        for offset in range(hours_ahead):
            hour = (current_hour_of_week + offset) % 168
            peak_mult = max(peak_mult, _traffic_multiplier(hour))

        # Use the last known capacity report as the baseline, or a
        # sensible default when no history exists.
        base_gpus = self._history[-1].required_gpus if self._history else 10
        forecasted = max(1, math.ceil(base_gpus * peak_mult))

        region_dist = self._distribute_across_regions(forecasted)

        confidence = max(0.5, 1.0 - 0.01 * hours_ahead)

        return DemandForecast(
            hours_ahead=hours_ahead,
            base_demand_gpus=base_gpus,
            peak_multiplier=peak_mult,
            forecasted_gpus=forecasted,
            confidence=round(confidence, 2),
            region_distribution=region_dist,
        )

    def get_capacity_report(self, workload: WorkloadProfile) -> CapacityReport:
        """Generate a full capacity report for a workload profile.

        Args:
            workload: The workload profile to evaluate.

        Returns:
            A ``CapacityReport`` with GPU counts and cost estimates.
        """
        spec = self._resolve_spec(workload.gpu_sku)
        required = self.calculate_required_gpus(workload)
        hourly_cost = required * spec.cost_per_hour
        monthly_cost = hourly_cost * 730  # average hours per month

        report = CapacityReport(
            required_gpus=required,
            gpu_sku=workload.gpu_sku,
            tokens_per_second=workload.tokens_per_second,
            concurrency=workload.concurrency,
            utilization_target=1.0 / (1.0 + self._headroom_pct),
            estimated_hourly_cost=round(hourly_cost, 2),
            estimated_monthly_cost=round(monthly_cost, 2),
            headroom_pct=self._headroom_pct,
        )
        self._history.append(report)
        return report

    def simulate_peak_load(
        self,
        workload: WorkloadProfile,
        peak_multiplier: float = 3.0,
    ) -> CapacityReport:
        """Simulate capacity requirements under peak traffic.

        The default multiplier of 3x corresponds to the observed
        Friday 8 PM traffic spike.

        Args:
            workload: Baseline workload profile.
            peak_multiplier: Multiplicative factor applied to the workload.

        Returns:
            A ``CapacityReport`` sized for the peak scenario.
        """
        peak_workload = WorkloadProfile(
            tokens_per_second=workload.tokens_per_second * peak_multiplier,
            avg_prompt_length=workload.avg_prompt_length,
            concurrency=int(workload.concurrency * peak_multiplier),
            gpu_sku=workload.gpu_sku,
        )
        return self.get_capacity_report(peak_workload)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_spec(self, sku: str) -> GPUSpec:
        spec = self._gpu_specs.get(sku)
        if spec is None:
            raise ValueError(
                f"Unknown GPU SKU '{sku}'. Available: {list(self._gpu_specs.keys())}"
            )
        return spec

    def _distribute_across_regions(self, total_gpus: int) -> dict[str, int]:
        """Distribute GPUs across regions proportionally."""
        distribution: dict[str, int] = {}
        allocated = 0
        sorted_regions = sorted(
            self._region_weights.items(), key=lambda kv: kv[1], reverse=True
        )
        for region, weight in sorted_regions[:-1]:
            count = max(1, round(total_gpus * weight))
            distribution[region.value] = count
            allocated += count

        # Assign remainder to the last region.
        last_region = sorted_regions[-1][0]
        distribution[last_region.value] = max(1, total_gpus - allocated)
        return distribution
