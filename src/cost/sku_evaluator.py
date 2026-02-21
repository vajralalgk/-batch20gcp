"""GPU SKU comparison and recommendation engine.

Evaluates A100 80GB, A10G 24GB, and H100 GPUs across throughput,
cost-per-token, memory, and power-efficiency dimensions. Simulates
workloads on each SKU to produce data-driven recommendations.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GPUSku:
    """Full specification of a GPU SKU.

    Attributes:
        name: Human-readable name.
        sku_id: Short identifier for programmatic use.
        memory_gb: GPU memory in GiB.
        tokens_per_second: Baseline token throughput (FP16, batch 1).
        cost_per_hour: On-demand price in USD.
        tdp_watts: Thermal design power in watts.
        max_batch_size: Maximum supported batch size.
        fp8_support: Whether FP8 inference is supported.
    """

    name: str
    sku_id: str
    memory_gb: float
    tokens_per_second: float
    cost_per_hour: float
    tdp_watts: float
    max_batch_size: int
    fp8_support: bool


# Canonical GPU catalog.
GPU_CATALOG: dict[str, GPUSku] = {
    "A100_80GB": GPUSku(
        name="NVIDIA A100 80GB",
        sku_id="A100_80GB",
        memory_gb=80.0,
        tokens_per_second=2400.0,
        cost_per_hour=3.50,
        tdp_watts=300,
        max_batch_size=64,
        fp8_support=False,
    ),
    "A10G_24GB": GPUSku(
        name="NVIDIA A10G 24GB",
        sku_id="A10G_24GB",
        memory_gb=24.0,
        tokens_per_second=800.0,
        cost_per_hour=1.10,
        tdp_watts=150,
        max_batch_size=16,
        fp8_support=False,
    ),
    "H100": GPUSku(
        name="NVIDIA H100 80GB",
        sku_id="H100",
        memory_gb=80.0,
        tokens_per_second=4800.0,
        cost_per_hour=5.50,
        tdp_watts=350,
        max_batch_size=128,
        fp8_support=True,
    ),
}


@dataclass
class WorkloadProfile:
    """Describes the workload characteristics for SKU evaluation.

    Attributes:
        required_tokens_per_second: Target aggregate throughput.
        avg_prompt_tokens: Average input prompt length.
        avg_completion_tokens: Average output completion length.
        concurrency: Number of simultaneous requests.
        model_size_gb: GPU memory required to load the model weights.
        latency_target_ms: Maximum acceptable p95 latency.
    """

    required_tokens_per_second: float = 5000.0
    avg_prompt_tokens: int = 256
    avg_completion_tokens: int = 512
    concurrency: int = 50
    model_size_gb: float = 14.0
    latency_target_ms: float = 200.0


@dataclass
class SKUEvaluation:
    """Evaluation result for a single GPU SKU against a workload.

    Attributes:
        sku: The evaluated SKU.
        gpus_required: Number of GPUs needed.
        total_cost_per_hour: Hourly fleet cost.
        cost_per_1k_tokens: Marginal cost per 1,000 tokens.
        effective_tokens_per_second: Achievable throughput.
        memory_headroom_gb: Spare memory per GPU after loading model.
        power_efficiency_tokens_per_watt: Tokens per watt-hour.
        meets_latency_target: Whether the p95 latency target is met.
        score: Composite score (higher is better).
        timestamp: When the evaluation was computed.
    """

    sku: GPUSku
    gpus_required: int
    total_cost_per_hour: float
    cost_per_1k_tokens: float
    effective_tokens_per_second: float
    memory_headroom_gb: float
    power_efficiency_tokens_per_watt: float
    meets_latency_target: bool
    score: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class SKURecommendation:
    """Final recommendation from SKU evaluation."""

    recommended_sku: str
    reason: str
    evaluations: list[SKUEvaluation]
    timestamp: float = field(default_factory=time.time)


class SKUEvaluator:
    """GPU SKU comparison and recommendation engine.

    Evaluates each SKU in the catalog against a workload profile,
    computing a composite score that balances cost, throughput, memory
    headroom, power efficiency, and latency compliance.

    Args:
        catalog: GPU SKU catalog. Defaults to the built-in catalog.
        cost_weight: Weight of cost-efficiency in the composite score.
        throughput_weight: Weight of raw throughput in the composite score.
        memory_weight: Weight of memory headroom in the composite score.
        power_weight: Weight of power efficiency in the composite score.
    """

    def __init__(
        self,
        catalog: Optional[dict[str, GPUSku]] = None,
        cost_weight: float = 0.35,
        throughput_weight: float = 0.30,
        memory_weight: float = 0.20,
        power_weight: float = 0.15,
    ) -> None:
        self._catalog = catalog or dict(GPU_CATALOG)
        self._weights = {
            "cost": cost_weight,
            "throughput": throughput_weight,
            "memory": memory_weight,
            "power": power_weight,
        }
        self._evaluation_history: list[SKURecommendation] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_skus(
        self,
        workload_profile: WorkloadProfile,
    ) -> list[SKUEvaluation]:
        """Evaluate every SKU in the catalog against a workload.

        Args:
            workload_profile: The target workload to size for.

        Returns:
            List of ``SKUEvaluation`` objects sorted by composite score
            (best first).
        """
        evaluations = [
            self._evaluate_single(sku, workload_profile)
            for sku in self._catalog.values()
        ]
        # Normalize and assign composite scores.
        self._compute_composite_scores(evaluations)
        evaluations.sort(key=lambda e: e.score, reverse=True)
        return evaluations

    def recommend_sku(
        self,
        requirements: WorkloadProfile,
    ) -> SKURecommendation:
        """Return a concrete SKU recommendation for the given workload.

        Args:
            requirements: Workload requirements.

        Returns:
            A ``SKURecommendation`` with the best SKU and reasoning.
        """
        evaluations = self.evaluate_skus(requirements)

        # Filter to SKUs that meet the latency target; fall back to all
        # if none qualify.
        viable = [e for e in evaluations if e.meets_latency_target]
        if not viable:
            viable = evaluations

        best = viable[0]
        reason_parts = [
            f"Best composite score ({best.score:.2f}).",
            f"Requires {best.gpus_required} GPU(s) at ${best.total_cost_per_hour:.2f}/hr.",
            f"Cost per 1k tokens: ${best.cost_per_1k_tokens:.4f}.",
        ]
        if best.meets_latency_target:
            reason_parts.append("Meets latency target.")
        else:
            reason_parts.append("WARNING: Does not meet latency target.")

        recommendation = SKURecommendation(
            recommended_sku=best.sku.sku_id,
            reason=" ".join(reason_parts),
            evaluations=evaluations,
        )
        self._evaluation_history.append(recommendation)
        return recommendation

    def get_price_performance(self) -> list[dict[str, object]]:
        """Return price-performance metrics for all SKUs.

        Returns:
            List of dictionaries with per-SKU metrics.
        """
        results: list[dict[str, object]] = []
        for sku in self._catalog.values():
            tokens_per_dollar = sku.tokens_per_second / sku.cost_per_hour * 3600
            tokens_per_watt = sku.tokens_per_second / sku.tdp_watts
            memory_bandwidth_ratio = sku.tokens_per_second / sku.memory_gb

            results.append({
                "sku_id": sku.sku_id,
                "name": sku.name,
                "cost_per_hour": sku.cost_per_hour,
                "tokens_per_second": sku.tokens_per_second,
                "tokens_per_dollar_hour": round(tokens_per_dollar, 2),
                "tokens_per_watt": round(tokens_per_watt, 2),
                "memory_gb": sku.memory_gb,
                "memory_bandwidth_ratio": round(memory_bandwidth_ratio, 2),
                "cost_per_1k_tokens": round(sku.cost_per_hour / (sku.tokens_per_second * 3.6), 6),
                "fp8_support": sku.fp8_support,
                "max_batch_size": sku.max_batch_size,
            })

        results.sort(key=lambda r: r["tokens_per_dollar_hour"], reverse=True)  # type: ignore[arg-type]
        return results

    def simulate_workload(
        self,
        sku: str,
        workload: WorkloadProfile,
    ) -> dict[str, object]:
        """Run a detailed simulation for a specific SKU and workload.

        Args:
            sku: SKU identifier.
            workload: The workload to simulate.

        Returns:
            Dictionary with simulation results.

        Raises:
            ValueError: If the SKU is not in the catalog.
        """
        gpu = self._resolve_sku(sku)
        eval_result = self._evaluate_single(gpu, workload)

        # Estimate queue wait time under concurrency pressure.
        service_rate = eval_result.effective_tokens_per_second / max(
            workload.avg_completion_tokens, 1
        )
        arrival_rate = workload.concurrency
        utilization_rho = arrival_rate / max(service_rate, 0.01)
        queue_wait_ms = (
            (utilization_rho / (1 - min(utilization_rho, 0.99))) * 10.0
            if utilization_rho < 1.0
            else float("inf")
        )

        total_tokens_per_request = (
            workload.avg_prompt_tokens + workload.avg_completion_tokens
        )
        daily_tokens = (
            eval_result.effective_tokens_per_second * 3600 * 24
        )

        return {
            "sku": gpu.name,
            "gpus_required": eval_result.gpus_required,
            "effective_tps": eval_result.effective_tokens_per_second,
            "cost_per_hour": eval_result.total_cost_per_hour,
            "cost_per_1k_tokens": eval_result.cost_per_1k_tokens,
            "estimated_queue_wait_ms": round(queue_wait_ms, 2),
            "memory_headroom_gb": eval_result.memory_headroom_gb,
            "meets_latency_target": eval_result.meets_latency_target,
            "daily_token_capacity": round(daily_tokens, 0),
            "daily_request_capacity": round(
                daily_tokens / max(total_tokens_per_request, 1), 0
            ),
            "daily_cost": round(eval_result.total_cost_per_hour * 24, 2),
            "monthly_cost": round(eval_result.total_cost_per_hour * 730, 2),
            "power_consumption_kw": round(
                gpu.tdp_watts * eval_result.gpus_required / 1000, 2
            ),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_sku(self, sku_id: str) -> GPUSku:
        sku = self._catalog.get(sku_id)
        if sku is None:
            raise ValueError(
                f"Unknown SKU '{sku_id}'. Available: {list(self._catalog.keys())}"
            )
        return sku

    def _evaluate_single(
        self,
        sku: GPUSku,
        workload: WorkloadProfile,
    ) -> SKUEvaluation:
        """Core evaluation logic for a single SKU."""
        # Memory check: model must fit with room for KV cache.
        memory_headroom = sku.memory_gb - workload.model_size_gb
        if memory_headroom < 0:
            # Need multiple GPUs for model parallelism.
            gpus_for_memory = math.ceil(workload.model_size_gb / sku.memory_gb)
        else:
            gpus_for_memory = 1

        # Throughput sizing.
        effective_batch = min(workload.concurrency, sku.max_batch_size)
        batch_factor = self._batch_throughput_factor(effective_batch)
        per_gpu_tps = sku.tokens_per_second * batch_factor
        gpus_for_throughput = math.ceil(
            workload.required_tokens_per_second / per_gpu_tps
        )

        gpus_required = max(gpus_for_memory, gpus_for_throughput, 1)
        total_tps = per_gpu_tps * gpus_required
        total_cost_per_hour = sku.cost_per_hour * gpus_required
        tokens_per_hour = total_tps * 3600.0
        cost_per_1k = (
            (total_cost_per_hour / tokens_per_hour * 1000.0) if tokens_per_hour > 0 else 0.0
        )

        # Power efficiency.
        total_watts = sku.tdp_watts * gpus_required
        power_eff = total_tps / total_watts if total_watts > 0 else 0.0

        # Latency estimate: simple model based on concurrency / throughput.
        estimated_latency_ms = (
            (workload.avg_completion_tokens / per_gpu_tps) * 1000
            * (1 + workload.concurrency / (gpus_required * 10))
        )
        meets_latency = estimated_latency_ms <= workload.latency_target_ms

        return SKUEvaluation(
            sku=sku,
            gpus_required=gpus_required,
            total_cost_per_hour=round(total_cost_per_hour, 2),
            cost_per_1k_tokens=round(cost_per_1k, 6),
            effective_tokens_per_second=round(total_tps, 2),
            memory_headroom_gb=round(max(memory_headroom, 0.0), 2),
            power_efficiency_tokens_per_watt=round(power_eff, 4),
            meets_latency_target=meets_latency,
            score=0.0,  # Filled in by _compute_composite_scores.
        )

    def _compute_composite_scores(
        self,
        evaluations: list[SKUEvaluation],
    ) -> None:
        """Normalize metrics and compute weighted composite scores in-place."""
        if not evaluations:
            return

        # Collect raw metric values.
        costs = [e.cost_per_1k_tokens for e in evaluations]
        tps_vals = [e.effective_tokens_per_second for e in evaluations]
        mem_vals = [e.memory_headroom_gb for e in evaluations]
        pow_vals = [e.power_efficiency_tokens_per_watt for e in evaluations]

        def _normalize(values: list[float], invert: bool = False) -> list[float]:
            lo, hi = min(values), max(values)
            if hi == lo:
                return [1.0] * len(values)
            normed = [(v - lo) / (hi - lo) for v in values]
            if invert:
                normed = [1.0 - n for n in normed]
            return normed

        norm_cost = _normalize(costs, invert=True)  # lower cost -> higher score
        norm_tps = _normalize(tps_vals)
        norm_mem = _normalize(mem_vals)
        norm_pow = _normalize(pow_vals)

        w = self._weights
        for i, ev in enumerate(evaluations):
            score = (
                w["cost"] * norm_cost[i]
                + w["throughput"] * norm_tps[i]
                + w["memory"] * norm_mem[i]
                + w["power"] * norm_pow[i]
            )
            # Bonus for meeting latency target.
            if ev.meets_latency_target:
                score += 0.10
            ev.score = round(score, 4)

    @staticmethod
    def _batch_throughput_factor(batch_size: int) -> float:
        """Diminishing-returns throughput factor for batching."""
        if batch_size <= 1:
            return 1.0
        return 1.0 + math.log2(batch_size) * 0.5
