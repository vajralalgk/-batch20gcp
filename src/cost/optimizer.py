"""Throughput-per-dollar modeling for GPU inference cost optimization.

Tracks hourly cost, cost per 1k tokens, cost per request, and monthly
projections. Factors in GPU type, utilization, batch size, and
quantization level to recommend the cheapest configuration that meets
latency targets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class QuantizationLevel(Enum):
    """Model quantization levels affecting throughput and quality."""

    FP32 = "fp32"
    FP16 = "fp16"
    INT8 = "int8"
    INT4 = "int4"


# Quantization throughput multiplier relative to FP16 baseline.
_QUANT_THROUGHPUT_MULTIPLIER: dict[QuantizationLevel, float] = {
    QuantizationLevel.FP32: 0.50,
    QuantizationLevel.FP16: 1.00,
    QuantizationLevel.INT8: 1.60,
    QuantizationLevel.INT4: 2.20,
}

# Quantization memory reduction factor relative to FP16.
_QUANT_MEMORY_FACTOR: dict[QuantizationLevel, float] = {
    QuantizationLevel.FP32: 2.00,
    QuantizationLevel.FP16: 1.00,
    QuantizationLevel.INT8: 0.50,
    QuantizationLevel.INT4: 0.25,
}


@dataclass(frozen=True)
class GPUConfig:
    """A specific GPU configuration to evaluate.

    Attributes:
        gpu_type: Human-readable GPU name.
        cost_per_hour: On-demand hourly price in USD.
        base_tokens_per_second: Token throughput at FP16 with batch size 1.
        memory_gb: GPU memory in GiB.
    """

    gpu_type: str
    cost_per_hour: float
    base_tokens_per_second: float
    memory_gb: float


# Default catalog of GPU configurations.
DEFAULT_GPU_CONFIGS: dict[str, GPUConfig] = {
    "A100_80GB": GPUConfig(
        gpu_type="A100 80GB",
        cost_per_hour=3.50,
        base_tokens_per_second=2400.0,
        memory_gb=80.0,
    ),
    "A10G_24GB": GPUConfig(
        gpu_type="A10G 24GB",
        cost_per_hour=1.10,
        base_tokens_per_second=800.0,
        memory_gb=24.0,
    ),
    "H100": GPUConfig(
        gpu_type="H100 80GB",
        cost_per_hour=5.50,
        base_tokens_per_second=4800.0,
        memory_gb=80.0,
    ),
}

# Batch-size throughput scaling factors (diminishing returns).
_BATCH_SIZE_SCALING: dict[int, float] = {
    1: 1.0,
    2: 1.8,
    4: 3.2,
    8: 5.5,
    16: 8.0,
    32: 10.5,
    64: 12.0,
}


def _batch_throughput_factor(batch_size: int) -> float:
    """Interpolate the throughput factor for a given batch size."""
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if batch_size in _BATCH_SIZE_SCALING:
        return _BATCH_SIZE_SCALING[batch_size]
    # Linear interpolation between known points.
    keys = sorted(_BATCH_SIZE_SCALING.keys())
    if batch_size > keys[-1]:
        return _BATCH_SIZE_SCALING[keys[-1]] * (batch_size / keys[-1]) * 0.85
    for lo, hi in zip(keys, keys[1:]):
        if lo < batch_size < hi:
            frac = (batch_size - lo) / (hi - lo)
            return _BATCH_SIZE_SCALING[lo] + frac * (
                _BATCH_SIZE_SCALING[hi] - _BATCH_SIZE_SCALING[lo]
            )
    return 1.0


@dataclass
class InferenceConfig:
    """Full configuration for a cost analysis scenario.

    Attributes:
        gpu_key: Key into the GPU config catalog.
        num_gpus: Number of GPUs in the deployment.
        batch_size: Inference batch size.
        quantization: Quantization level of the model.
        utilization: Average GPU utilization (0.0 - 1.0).
        avg_tokens_per_request: Average token count per inference request.
    """

    gpu_key: str = "A100_80GB"
    num_gpus: int = 1
    batch_size: int = 1
    quantization: QuantizationLevel = QuantizationLevel.FP16
    utilization: float = 0.70
    avg_tokens_per_request: int = 512


@dataclass
class CostReport:
    """Structured output of a cost analysis."""

    config_label: str
    hourly_cost: float
    cost_per_1k_tokens: float
    cost_per_request: float
    monthly_projection: float
    effective_tokens_per_second: float
    gpu_utilization: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class OptimizationRecommendation:
    """A single actionable cost optimization recommendation."""

    category: str
    description: str
    estimated_savings_pct: float
    current_value: str
    recommended_value: str


class CostOptimizer:
    """Throughput-per-dollar optimizer for GPU inference workloads.

    Computes effective token throughput by combining the base GPU
    throughput with batch-size scaling and quantization multipliers,
    then derives cost metrics at the desired utilization target.

    Args:
        gpu_configs: Catalog of available GPU configurations.
    """

    def __init__(
        self,
        gpu_configs: Optional[dict[str, GPUConfig]] = None,
    ) -> None:
        self._gpu_configs = gpu_configs or dict(DEFAULT_GPU_CONFIGS)
        self._reports: list[CostReport] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_cost_per_token(self, config: InferenceConfig) -> CostReport:
        """Compute cost metrics for a given inference configuration.

        Args:
            config: The inference configuration to evaluate.

        Returns:
            A ``CostReport`` with per-token, per-request, and monthly costs.

        Raises:
            ValueError: If the GPU key is unknown.
        """
        gpu = self._resolve_gpu(config.gpu_key)

        quant_mult = _QUANT_THROUGHPUT_MULTIPLIER[config.quantization]
        batch_mult = _batch_throughput_factor(config.batch_size)

        # Effective throughput per GPU at the given utilization.
        per_gpu_tps = gpu.base_tokens_per_second * quant_mult * batch_mult * config.utilization
        total_tps = per_gpu_tps * config.num_gpus

        hourly_cost = gpu.cost_per_hour * config.num_gpus
        tokens_per_hour = total_tps * 3600.0

        cost_per_1k = (hourly_cost / tokens_per_hour * 1000.0) if tokens_per_hour > 0 else 0.0
        cost_per_request = cost_per_1k * config.avg_tokens_per_request / 1000.0
        monthly_projection = hourly_cost * 730.0  # average hours / month

        report = CostReport(
            config_label=f"{gpu.gpu_type} x{config.num_gpus} bs={config.batch_size} {config.quantization.value}",
            hourly_cost=round(hourly_cost, 4),
            cost_per_1k_tokens=round(cost_per_1k, 6),
            cost_per_request=round(cost_per_request, 6),
            monthly_projection=round(monthly_projection, 2),
            effective_tokens_per_second=round(total_tps, 2),
            gpu_utilization=config.utilization,
        )
        self._reports.append(report)
        return report

    def get_optimization_recommendations(
        self,
        config: InferenceConfig,
    ) -> list[OptimizationRecommendation]:
        """Analyze a configuration and suggest cost optimizations.

        Args:
            config: The current inference configuration.

        Returns:
            A list of actionable recommendations.
        """
        recommendations: list[OptimizationRecommendation] = []

        # 1. Batch size optimization.
        if config.batch_size < 8:
            recommendations.append(
                OptimizationRecommendation(
                    category="batch_size",
                    description=(
                        "Increasing batch size improves throughput per dollar due "
                        "to better GPU utilization."
                    ),
                    estimated_savings_pct=round(
                        (1.0 - _batch_throughput_factor(config.batch_size)
                         / _batch_throughput_factor(min(config.batch_size * 2, 64)))
                        * 100,
                        1,
                    ),
                    current_value=str(config.batch_size),
                    recommended_value=str(min(config.batch_size * 2, 64)),
                )
            )

        # 2. Quantization optimization.
        if config.quantization in (QuantizationLevel.FP32, QuantizationLevel.FP16):
            target_quant = QuantizationLevel.INT8
            savings = (
                1.0
                - _QUANT_THROUGHPUT_MULTIPLIER[config.quantization]
                / _QUANT_THROUGHPUT_MULTIPLIER[target_quant]
            ) * 100
            recommendations.append(
                OptimizationRecommendation(
                    category="quantization",
                    description=(
                        "Switching to INT8 quantization significantly increases "
                        "throughput with minimal quality loss."
                    ),
                    estimated_savings_pct=round(abs(savings), 1),
                    current_value=config.quantization.value,
                    recommended_value=target_quant.value,
                )
            )

        # 3. Utilization optimization.
        if config.utilization < 0.60:
            recommendations.append(
                OptimizationRecommendation(
                    category="utilization",
                    description=(
                        "GPU utilization is below 60 %. Consolidating workloads "
                        "or reducing fleet size can cut costs."
                    ),
                    estimated_savings_pct=round(
                        (1.0 - config.utilization / 0.75) * 100, 1
                    ),
                    current_value=f"{config.utilization:.0%}",
                    recommended_value="70-80%",
                )
            )

        # 4. GPU SKU optimization.
        current_gpu = self._resolve_gpu(config.gpu_key)
        for key, gpu in self._gpu_configs.items():
            if key == config.gpu_key:
                continue
            current_tpd = current_gpu.base_tokens_per_second / current_gpu.cost_per_hour
            other_tpd = gpu.base_tokens_per_second / gpu.cost_per_hour
            if other_tpd > current_tpd * 1.1:
                savings = (1.0 - current_tpd / other_tpd) * 100
                recommendations.append(
                    OptimizationRecommendation(
                        category="gpu_sku",
                        description=(
                            f"Switching to {gpu.gpu_type} offers {savings:.0f}% "
                            f"better tokens-per-dollar."
                        ),
                        estimated_savings_pct=round(savings, 1),
                        current_value=current_gpu.gpu_type,
                        recommended_value=gpu.gpu_type,
                    )
                )

        return recommendations

    def compare_configurations(
        self,
        configs: list[InferenceConfig],
    ) -> list[CostReport]:
        """Compare multiple inference configurations side by side.

        Args:
            configs: List of configurations to evaluate.

        Returns:
            List of ``CostReport`` objects sorted by ``cost_per_1k_tokens``.
        """
        reports = [self.calculate_cost_per_token(cfg) for cfg in configs]
        reports.sort(key=lambda r: r.cost_per_1k_tokens)
        return reports

    def get_cost_report(self) -> dict[str, object]:
        """Return a summary of all cost analyses performed so far.

        Returns:
            Dictionary with aggregate statistics and individual reports.
        """
        if not self._reports:
            return {"reports": [], "count": 0}

        cheapest = min(self._reports, key=lambda r: r.cost_per_1k_tokens)
        most_throughput = max(self._reports, key=lambda r: r.effective_tokens_per_second)

        return {
            "count": len(self._reports),
            "cheapest_per_1k_tokens": {
                "label": cheapest.config_label,
                "cost_per_1k_tokens": cheapest.cost_per_1k_tokens,
            },
            "highest_throughput": {
                "label": most_throughput.config_label,
                "tokens_per_second": most_throughput.effective_tokens_per_second,
            },
            "reports": [
                {
                    "label": r.config_label,
                    "hourly_cost": r.hourly_cost,
                    "cost_per_1k_tokens": r.cost_per_1k_tokens,
                    "cost_per_request": r.cost_per_request,
                    "monthly_projection": r.monthly_projection,
                    "effective_tps": r.effective_tokens_per_second,
                }
                for r in self._reports
            ],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_gpu(self, key: str) -> GPUConfig:
        gpu = self._gpu_configs.get(key)
        if gpu is None:
            raise ValueError(
                f"Unknown GPU key '{key}'. Available: {list(self._gpu_configs.keys())}"
            )
        return gpu
