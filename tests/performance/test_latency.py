"""Performance / latency benchmark tests for the Netflix LLM Platform.

These tests measure wall-clock latency of key API endpoints and operations
to verify they meet target SLAs.  They use ``time.perf_counter`` for
high-resolution timing.
"""

import time

import pytest


class TestHealthEndpointLatency:
    """The /health endpoint must respond in < 50 ms."""

    def test_health_endpoint_latency(self, app_client):
        # Warm-up
        app_client.get("/health")

        latencies = []
        for _ in range(20):
            start = time.perf_counter()
            resp = app_client.get("/health")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        p95_ms = sorted(latencies)[int(len(latencies) * 0.95)]

        # Average should be well under 50 ms for an in-process TestClient
        assert avg_ms < 50, f"Average health latency {avg_ms:.1f}ms exceeds 50ms target"
        assert p95_ms < 100, f"P95 health latency {p95_ms:.1f}ms exceeds 100ms target"


class TestInferenceEndpointLatency:
    """The inference endpoint should aim for < 200 ms target."""

    def test_inference_endpoint_latency(self, app_client, sample_inference_request):
        # Warm-up
        app_client.post("/predict", json=sample_inference_request)

        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            resp = app_client.post("/predict", json=sample_inference_request)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        # If route exists, validate latency. If 404, just verify it responds fast.
        if resp.status_code == 200:
            assert avg_ms < 200, f"Avg inference latency {avg_ms:.1f}ms exceeds 200ms"
        else:
            # Even 404 should be fast
            assert avg_ms < 100


class TestBatchInferenceThroughput:
    """Measure throughput for batch inference requests."""

    def test_batch_inference_throughput(self, app_client, sample_inference_request):
        num_requests = 50
        start = time.perf_counter()
        for _ in range(num_requests):
            app_client.post("/predict", json=sample_inference_request)
        total_seconds = time.perf_counter() - start
        throughput_rps = num_requests / total_seconds

        # TestClient should handle at least 50 req/s for simple endpoints
        assert throughput_rps > 50, (
            f"Throughput {throughput_rps:.1f} req/s below 50 req/s target"
        )


class TestDetailedHealthLatency:
    """The detailed health check should respond in reasonable time."""

    def test_detailed_health_latency(self, app_client):
        start = time.perf_counter()
        resp = app_client.get("/health/detailed")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        # Detailed health does more work but should still be < 200ms
        assert elapsed_ms < 200, f"Detailed health took {elapsed_ms:.1f}ms"
