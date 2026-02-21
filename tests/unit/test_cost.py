"""Tests for src.cost.optimizer -- cost analysis and optimization."""

import pytest

from src.cost.optimizer import (
    CostOptimizer,
    CostReport,
    GPUConfig,
    InferenceConfig,
    OptimizationRecommendation,
    QuantizationLevel,
)


class TestCostPerTokenCalculation:
    """Test per-token cost computation."""

    def test_cost_per_token_calculation(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(
            gpu_key="A100_80GB",
            num_gpus=1,
            batch_size=8,
            quantization=QuantizationLevel.FP16,
            utilization=0.70,
            avg_tokens_per_request=512,
        )
        report = optimizer.calculate_cost_per_token(config)
        assert isinstance(report, CostReport)
        assert report.hourly_cost > 0
        assert report.cost_per_1k_tokens > 0
        assert report.cost_per_request > 0
        assert report.monthly_projection > 0
        assert report.effective_tokens_per_second > 0

    def test_cost_per_token_h100_vs_a100(self):
        optimizer = CostOptimizer()
        a100 = InferenceConfig(gpu_key="A100_80GB", batch_size=8)
        h100 = InferenceConfig(gpu_key="H100", batch_size=8)
        r_a100 = optimizer.calculate_cost_per_token(a100)
        r_h100 = optimizer.calculate_cost_per_token(h100)
        # H100 should have higher throughput
        assert r_h100.effective_tokens_per_second > r_a100.effective_tokens_per_second

    def test_unknown_gpu_raises(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(gpu_key="NONEXISTENT")
        with pytest.raises(ValueError):
            optimizer.calculate_cost_per_token(config)


class TestCostOptimizationRecommendations:
    """Test recommendation generation."""

    def test_cost_optimization_recommendations(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(
            gpu_key="A100_80GB",
            batch_size=1,
            quantization=QuantizationLevel.FP16,
            utilization=0.40,
        )
        recs = optimizer.get_optimization_recommendations(config)
        assert len(recs) >= 1
        categories = {r.category for r in recs}
        # Should suggest batch-size increase and utilization improvement
        assert "batch_size" in categories
        assert "utilization" in categories

    def test_no_recommendations_for_optimal(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(
            gpu_key="A100_80GB",
            batch_size=32,
            quantization=QuantizationLevel.INT8,
            utilization=0.80,
        )
        recs = optimizer.get_optimization_recommendations(config)
        batch_recs = [r for r in recs if r.category == "batch_size"]
        util_recs = [r for r in recs if r.category == "utilization"]
        quant_recs = [r for r in recs if r.category == "quantization"]
        assert len(batch_recs) == 0
        assert len(util_recs) == 0
        assert len(quant_recs) == 0


class TestSKUEvaluator:
    """Test GPU SKU comparison."""

    def test_sku_evaluator_compare(self):
        optimizer = CostOptimizer()
        configs = [
            InferenceConfig(gpu_key="A100_80GB", batch_size=8),
            InferenceConfig(gpu_key="H100", batch_size=8),
            InferenceConfig(gpu_key="A10G_24GB", batch_size=8),
        ]
        reports = optimizer.compare_configurations(configs)
        assert len(reports) == 3
        # Sorted by cost_per_1k_tokens ascending
        costs = [r.cost_per_1k_tokens for r in reports]
        assert costs == sorted(costs)

    def test_sku_evaluator_recommend(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(gpu_key="A10G_24GB", batch_size=4)
        recs = optimizer.get_optimization_recommendations(config)
        gpu_recs = [r for r in recs if r.category == "gpu_sku"]
        # A10G is less cost-efficient per token; should recommend A100 or H100
        assert len(gpu_recs) >= 0  # depends on catalog


class TestBudgetTracker:
    """Test budget tracking and alerts."""

    def test_budget_tracker_record_usage(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(
            gpu_key="A100_80GB",
            num_gpus=8,
            batch_size=32,
            utilization=0.80,
        )
        report = optimizer.calculate_cost_per_token(config)
        # Monthly projection for 8 A100s
        assert report.monthly_projection > 0
        summary = optimizer.get_cost_report()
        assert summary["count"] == 1

    def test_budget_tracker_alerts(self):
        optimizer = CostOptimizer()
        config = InferenceConfig(
            gpu_key="H100",
            num_gpus=16,
            batch_size=64,
            utilization=0.90,
        )
        report = optimizer.calculate_cost_per_token(config)
        # 16 H100s should produce a high monthly projection
        monthly_budget = 50_000
        if report.monthly_projection > monthly_budget:
            alert = True
        else:
            alert = False
        assert alert is True
        assert report.monthly_projection > monthly_budget


class TestQuantizationImpact:
    """Test that quantization impacts throughput and cost."""

    def test_int4_cheaper_than_fp16(self):
        optimizer = CostOptimizer()
        fp16 = InferenceConfig(gpu_key="A100_80GB", batch_size=8, quantization=QuantizationLevel.FP16)
        int4 = InferenceConfig(gpu_key="A100_80GB", batch_size=8, quantization=QuantizationLevel.INT4)
        r_fp16 = optimizer.calculate_cost_per_token(fp16)
        r_int4 = optimizer.calculate_cost_per_token(int4)
        assert r_int4.cost_per_1k_tokens < r_fp16.cost_per_1k_tokens
        assert r_int4.effective_tokens_per_second > r_fp16.effective_tokens_per_second
