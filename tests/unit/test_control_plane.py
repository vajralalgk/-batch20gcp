"""Tests for src.control_plane -- autoscaler, capacity model, fleet scheduler, circuit breaker."""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.control_plane.autoscaler import (
    FleetMetrics,
    GPUAutoscaler,
    ScalingDirection,
    ScalingThresholds,
)
from src.control_plane.capacity_model import (
    CapacityModel,
    WorkloadProfile,
)
from src.control_plane.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from src.control_plane.fleet_scheduler import (
    FleetScheduler,
    GPUNode,
    InferenceRequest,
    NodeStatus,
    Priority,
)


# =========================================================================
# GPUAutoscaler
# =========================================================================

class TestAutoscalerScaleUpTrigger:
    """Test scale-up decision logic."""

    def test_autoscaler_scale_up_trigger(self):
        scaler = GPUAutoscaler(
            min_instances=2,
            max_instances=16,
            cool_down_period_s=0,
            thresholds=ScalingThresholds(gpu_utilization_high=0.80),
        )
        metrics = FleetMetrics(gpu_utilization=0.90, p95_latency_ms=50)
        scaler.update_metrics(metrics)
        decision = scaler.evaluate_scaling()
        assert decision.direction == ScalingDirection.SCALE_UP
        assert decision.count >= 1


class TestAutoscalerScaleDownTrigger:
    """Test scale-down decision logic."""

    def test_autoscaler_scale_down_trigger(self):
        scaler = GPUAutoscaler(
            min_instances=2,
            max_instances=16,
            cool_down_period_s=0,
            thresholds=ScalingThresholds(
                gpu_utilization_low=0.40,
                low_utilization_duration_s=0,  # instant
            ),
        )
        # Start at 10 instances
        scaler._active_instances = 10
        scaler._healthy_instances = 10
        # Supply low-utilization metrics
        metrics = FleetMetrics(gpu_utilization=0.15, p95_latency_ms=20)
        scaler.update_metrics(metrics)
        # Force the low-utilization timer to have already expired
        scaler._low_utilization_since = time.time() - 1
        decision = scaler.evaluate_scaling()
        assert decision.direction == ScalingDirection.SCALE_DOWN
        assert decision.count >= 1


class TestAutoscalerCooldown:
    """Test that the cooldown period prevents oscillation."""

    def test_autoscaler_cooldown(self):
        scaler = GPUAutoscaler(
            min_instances=2,
            max_instances=16,
            cool_down_period_s=60,
        )
        # First scale-up
        metrics = FleetMetrics(gpu_utilization=0.95)
        scaler.update_metrics(metrics)
        d1 = scaler.get_scaling_decision()
        assert d1.direction == ScalingDirection.SCALE_UP
        # Second call within cooldown
        scaler.update_metrics(metrics)
        d2 = scaler.evaluate_scaling()
        assert d2.direction == ScalingDirection.NO_CHANGE

    def test_fleet_status(self):
        scaler = GPUAutoscaler(min_instances=4, max_instances=20)
        status = scaler.get_fleet_status()
        assert status["active_instances"] == 4
        assert status["min_instances"] == 4
        assert status["max_instances"] == 20


# =========================================================================
# CapacityModel
# =========================================================================

class TestCapacityModelCalculateGPUs:
    """Test GPU count calculation for a given workload."""

    def test_capacity_model_calculate_gpus(self):
        model = CapacityModel()
        workload = WorkloadProfile(
            tokens_per_second=100,
            avg_prompt_length=500,
            concurrency=10,
            gpu_sku="A100_80GB",
        )
        gpus = model.calculate_required_gpus(workload)
        assert gpus >= 1
        assert isinstance(gpus, int)


class TestCapacityModelPeakLoad:
    """Test peak-load capacity estimation."""

    def test_capacity_model_peak_load(self):
        model = CapacityModel()
        base_workload = WorkloadProfile(
            tokens_per_second=100,
            avg_prompt_length=500,
            concurrency=10,
        )
        base_report = model.get_capacity_report(base_workload)
        peak_report = model.simulate_peak_load(base_workload, peak_multiplier=3.0)
        assert peak_report.required_gpus > base_report.required_gpus

    def test_unknown_sku_raises(self):
        model = CapacityModel()
        workload = WorkloadProfile(
            tokens_per_second=100,
            avg_prompt_length=500,
            concurrency=10,
            gpu_sku="NONEXISTENT",
        )
        with pytest.raises(ValueError):
            model.calculate_required_gpus(workload)


# =========================================================================
# FleetScheduler
# =========================================================================

class TestFleetSchedulerSchedule:
    """Test fleet scheduling decisions."""

    def test_fleet_scheduler_schedule(self):
        nodes = [
            GPUNode(node_id=f"node-{i}", total_memory_gb=80.0, region="us-east-1")
            for i in range(4)
        ]
        scheduler = FleetScheduler(nodes=nodes)
        req = InferenceRequest(user_id="u1", gpu_memory_required_gb=4.0)
        result = scheduler.schedule_request(req)
        assert result.success is True
        assert result.node_id is not None


class TestFleetSchedulerRebalance:
    """Test fleet rebalancing across nodes."""

    def test_fleet_scheduler_rebalance(self):
        nodes = [
            GPUNode(node_id="node-A", total_memory_gb=80.0, gpu_utilization=0.95,
                    used_memory_gb=60.0, assigned_requests=["r1", "r2"]),
            GPUNode(node_id="node-B", total_memory_gb=80.0, gpu_utilization=0.10,
                    used_memory_gb=5.0, assigned_requests=[]),
        ]
        scheduler = FleetScheduler(nodes=nodes)
        results = scheduler.rebalance()
        # Should migrate at least one request from A to B
        assert len(results) >= 1
        assert results[0].node_id == "node-B"

    def test_fleet_health(self):
        nodes = [
            GPUNode(node_id="n1", status=NodeStatus.HEALTHY),
            GPUNode(node_id="n2", status=NodeStatus.DEGRADED),
        ]
        scheduler = FleetScheduler(nodes=nodes)
        health = scheduler.get_fleet_health()
        assert health["total_nodes"] == 2
        assert health["healthy"] == 1
        assert health["degraded"] == 1


# =========================================================================
# CircuitBreaker
# =========================================================================

class TestCircuitBreakerClosedState:
    """Test normal (closed) circuit behavior."""

    def test_circuit_breaker_closed_state(self):
        cb = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
            half_open_max_calls=3,
        )
        state = cb.get_state()
        assert state["state"] == CircuitState.CLOSED.value


class TestCircuitBreakerOpenAfterFailures:
    """Test that repeated failures open the circuit."""

    def test_circuit_breaker_open_after_failures(self):
        cb = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30.0,
            half_open_max_calls=1,
        )
        for _ in range(3):
            cb.record_failure()
        state = cb.get_state()
        assert state["state"] == CircuitState.OPEN.value
        # Trying to call should raise
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: "ok")


class TestCircuitBreakerHalfOpenRecovery:
    """Test half-open recovery flow."""

    def test_circuit_breaker_half_open_recovery(self):
        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=0.01,
            half_open_max_calls=1,
        )
        cb.record_failure()
        cb.record_failure()
        assert cb.get_state()["state"] == CircuitState.OPEN.value
        # Wait for recovery timeout
        time.sleep(0.05)
        # Next call attempt transitions to HALF_OPEN and tries the function
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.get_state()["state"] == CircuitState.CLOSED.value


class TestCircuitBreakerStats:
    """Test circuit breaker stats reporting."""

    def test_stats(self):
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        stats = cb.get_state()
        assert stats["success_count"] == 2
        assert stats["failure_count"] == 1
        assert stats["state"] == CircuitState.CLOSED.value

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.get_state()["state"] == CircuitState.OPEN.value
        cb.reset()
        assert cb.get_state()["state"] == CircuitState.CLOSED.value
