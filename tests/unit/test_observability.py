"""Tests for src.observability -- metrics collector, latency tracker, DCGM exporter, health checker."""

import asyncio
import time

import pytest

from src.observability.metrics_collector import MetricsCollector, MetricsSummary
from src.observability.latency_tracker import (
    LatencyBreakdown,
    LatencyTracker,
    SLAViolation,
)
from src.observability.dcgm_exporter import (
    DCGMExporter,
    GPUHealthStatus,
    GPUMetricsSample,
)
from src.observability.health_checker import (
    ComponentHealth,
    ComponentStatus,
    HealthChecker,
    HealthReport,
    OverallStatus,
)


# =========================================================================
# MetricsCollector
# =========================================================================

class TestMetricsCollectorRecordInference:
    """Test inference metric recording."""

    def test_metrics_collector_record_inference(self):
        mc = MetricsCollector(namespace="test_ns")
        mc.record_inference(
            latency=0.05,
            batch_size=8,
            tokens=512,
            model="netflix_llm",
            status="success",
        )
        summary = mc.get_summary()
        assert summary.total_requests == 1
        assert summary.avg_latency_seconds > 0
        assert summary.tokens_per_second > 0

    def test_multiple_recordings(self):
        mc = MetricsCollector(namespace="test_multi")
        for i in range(10):
            mc.record_inference(
                latency=0.05 + i * 0.01,
                batch_size=8,
                tokens=256,
            )
        summary = mc.get_summary()
        assert summary.total_requests == 10
        assert summary.avg_latency_seconds > 0

    def test_error_recording(self):
        mc = MetricsCollector(namespace="test_errors")
        mc.record_inference(latency=0.1, batch_size=1, tokens=0, status="error")
        mc.record_error("timeout")
        # No crash; error counter incremented (Prometheus internal)

    def test_prometheus_output(self):
        mc = MetricsCollector(namespace="test_prom")
        mc.record_inference(latency=0.05, batch_size=4, tokens=128)
        output = mc.generate_prometheus_output()
        assert isinstance(output, bytes)
        assert b"test_prom" in output


class TestMetricsCollectorGPUMetrics:
    """Test GPU metric updates."""

    def test_metrics_collector_gpu_metrics(self):
        mc = MetricsCollector(namespace="test_gpu_m")
        mc.update_gpu_metrics(utilization=72.5, memory=54.4e9, gpu_id="0")
        mc.update_gpu_metrics(utilization=68.0, memory=50.0e9, gpu_id="1")
        mc.update_cache_metrics(utilization=0.75, hit_rate=0.92)
        # Verify no errors; gauges are set internally
        summary = mc.get_summary()
        assert summary is not None


# =========================================================================
# LatencyTracker
# =========================================================================

class TestLatencyTrackerPercentiles:
    """Test percentile computation."""

    def test_latency_tracker_percentiles(self):
        tracker = LatencyTracker()
        # Record 100 samples with known distribution
        for i in range(100):
            tracker.record(latency_ms=float(i))
        p50 = tracker.get_percentile(50.0)
        p95 = tracker.get_p95()
        p99 = tracker.get_p99()
        assert 40 < p50 < 60
        assert p95 > p50
        assert p99 > p95

    def test_empty_percentile(self):
        tracker = LatencyTracker()
        assert tracker.get_percentile(50.0) == 0.0

    def test_single_sample(self):
        tracker = LatencyTracker()
        tracker.record(latency_ms=42.0)
        assert tracker.get_percentile(50.0) == 42.0

    def test_window_names(self):
        tracker = LatencyTracker()
        names = tracker.get_window_names()
        assert "5m" in names
        assert "1h" in names
        assert "24h" in names

    def test_sample_count(self):
        tracker = LatencyTracker()
        for _ in range(50):
            tracker.record(latency_ms=10.0)
        assert tracker.get_sample_count("5m") == 50


class TestLatencyTrackerSLAViolation:
    """Test SLA violation detection."""

    def test_latency_tracker_sla_violation(self):
        tracker = LatencyTracker(sla_p95_ms=100.0, sla_p99_ms=150.0)
        # All samples exceed SLA
        for _ in range(100):
            tracker.record(latency_ms=200.0)
        violations = tracker.check_sla_violations()
        assert len(violations) >= 1
        assert any(v.percentile == 95 for v in violations)

    def test_no_sla_violation(self):
        tracker = LatencyTracker(sla_p95_ms=200.0, sla_p99_ms=250.0)
        for i in range(100):
            tracker.record(latency_ms=float(i))
        violations = tracker.check_sla_violations()
        assert len(violations) == 0

    def test_breakdown_tracking(self):
        tracker = LatencyTracker()
        breakdown = LatencyBreakdown(
            network_ms=5.0,
            queue_ms=10.0,
            inference_ms=80.0,
            postprocessing_ms=5.0,
        )
        tracker.record(latency_ms=breakdown.total_ms, breakdown=breakdown)
        averages = tracker.get_phase_averages()
        assert averages["inference"] == 80.0
        assert averages["network"] == 5.0

    def test_histogram(self):
        tracker = LatencyTracker()
        for _ in range(50):
            tracker.record(latency_ms=100.0)
        hist = tracker.get_histogram("5m")
        assert len(hist) > 0
        total_count = sum(b.count for b in hist)
        assert total_count >= 50


# =========================================================================
# DCGMExporter
# =========================================================================

class TestDCGMExporterCollect:
    """Test DCGM metric collection in mock mode."""

    def test_dcgm_exporter_collect(self):
        exporter = DCGMExporter(gpu_ids=[0, 1, 2, 3], mock_mode=True)
        samples = exporter.collect_metrics()
        assert len(samples) == 4
        for gpu_id, sample in samples.items():
            assert isinstance(sample, GPUMetricsSample)
            assert 0 <= sample.sm_occupancy <= 100
            assert sample.memory_total_bytes > 0
            assert sample.temperature_celsius > 0

    def test_gpu_health_assessment(self):
        exporter = DCGMExporter(gpu_ids=[0], mock_mode=True)
        health = exporter.get_gpu_health()
        assert 0 in health
        assert health[0].status in (
            GPUHealthStatus.HEALTHY,
            GPUHealthStatus.DEGRADED,
            GPUHealthStatus.CRITICAL,
        )

    def test_anomaly_detection(self):
        exporter = DCGMExporter(gpu_ids=[0], mock_mode=True)
        exporter.collect_metrics()
        anomalies = exporter.detect_anomalies()
        assert isinstance(anomalies, list)
        # Mock data may or may not trigger anomalies

    def test_historical_metrics(self):
        exporter = DCGMExporter(gpu_ids=[0], mock_mode=True)
        exporter.collect_metrics()
        history = exporter.get_historical_metrics(duration_seconds=60)
        assert 0 in history
        assert len(history[0]) >= 1


# =========================================================================
# HealthChecker
# =========================================================================

class TestHealthCheckerAllHealthy:
    """Test health checker aggregation."""

    def test_health_checker_all_healthy(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.HEALTHY),
            "inference": ComponentHealth(name="inference", status=ComponentStatus.HEALTHY),
            "gpu": ComponentHealth(name="gpu", status=ComponentStatus.HEALTHY),
        }
        status = HealthChecker._aggregate_status(components)
        assert status == OverallStatus.HEALTHY

    def test_degraded_if_any_degraded(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.HEALTHY),
            "inference": ComponentHealth(name="inference", status=ComponentStatus.DEGRADED),
        }
        status = HealthChecker._aggregate_status(components)
        assert status == OverallStatus.DEGRADED

    def test_unhealthy_if_any_unhealthy(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.HEALTHY),
            "inference": ComponentHealth(name="inference", status=ComponentStatus.UNHEALTHY),
        }
        status = HealthChecker._aggregate_status(components)
        assert status == OverallStatus.UNHEALTHY

    def test_health_report_to_dict(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.HEALTHY, message="OK"),
        }
        report = HealthReport(
            status=OverallStatus.HEALTHY,
            components=components,
        )
        d = report.to_dict()
        assert d["status"] == "healthy"
        assert "api" in d["components"]
        assert d["components"]["api"]["status"] == "healthy"

    def test_report_is_ready_property(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.HEALTHY),
            "gpu": ComponentHealth(name="gpu", status=ComponentStatus.DEGRADED),
        }
        report = HealthReport(status=OverallStatus.DEGRADED, components=components)
        assert report.is_ready is True  # No UNHEALTHY component

    def test_report_not_ready_when_unhealthy(self):
        components = {
            "api": ComponentHealth(name="api", status=ComponentStatus.UNHEALTHY),
        }
        report = HealthReport(status=OverallStatus.UNHEALTHY, components=components)
        assert report.is_ready is False
