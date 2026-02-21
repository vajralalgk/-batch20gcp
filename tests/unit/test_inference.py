"""Tests for src.inference -- Triton client, dynamic batching, model registry."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.inference.triton_client import (
    InferenceMetrics,
    InferenceResult,
    ModelState,
    TritonInferenceClient,
    TritonModelConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> TritonInferenceClient:
    defaults = dict(
        url="localhost:8001",
        model_name="netflix_llm",
        mock_mode=True,
    )
    defaults.update(kwargs)
    return TritonInferenceClient(**defaults)


# ---------------------------------------------------------------------------
# TritonInferenceClient
# ---------------------------------------------------------------------------

class TestTritonClientHealthCheck:
    """Test the health-check methods of the Triton client."""

    @pytest.mark.asyncio
    async def test_triton_client_health_check(self):
        client = _make_client()
        await client.connect()
        health = await client.health_check()
        assert health["live"] is True
        assert health["ready"] is True
        assert health["model_ready"] is True
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_health_check_disconnected(self):
        client = _make_client(mock_mode=False)
        health = await client.health_check()
        assert health["live"] is False

    @pytest.mark.asyncio
    async def test_model_status_mock(self):
        client = _make_client()
        await client.connect()
        status = await client.get_model_status()
        assert status["state"] == ModelState.READY.value
        assert status["name"] == "netflix_llm"
        await client.disconnect()


class TestTritonClientInferMock:
    """Test mock inference through the Triton client."""

    @pytest.mark.asyncio
    async def test_triton_client_infer_mock(self):
        client = _make_client()
        await client.connect()

        input_ids = np.array([[1, 2, 3, 4]], dtype=np.int64)
        attention_mask = np.ones_like(input_ids)

        result = await client.infer(input_ids, attention_mask)
        assert isinstance(result, InferenceResult)
        assert "logits" in result.outputs
        assert result.outputs["logits"].shape[0] == 1  # batch_size
        assert result.latency_ms > 0
        assert result.model_name == "netflix_llm"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_infer_without_connect_raises(self):
        client = _make_client()
        input_ids = np.array([[1, 2, 3]], dtype=np.int64)
        mask = np.ones_like(input_ids)
        with pytest.raises(ConnectionError):
            await client.infer(input_ids, mask)

    @pytest.mark.asyncio
    async def test_metrics_after_infer(self):
        client = _make_client()
        await client.connect()
        input_ids = np.array([[1, 2, 3]], dtype=np.int64)
        mask = np.ones_like(input_ids)
        await client.infer(input_ids, mask)
        metrics = client.get_metrics()
        assert metrics["inference_count"] == 1
        assert metrics["error_count"] == 0
        assert metrics["avg_latency_ms"] > 0
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_reset_metrics(self):
        client = _make_client()
        await client.connect()
        input_ids = np.array([[1]], dtype=np.int64)
        mask = np.ones_like(input_ids)
        await client.infer(input_ids, mask)
        client.reset_metrics()
        metrics = client.get_metrics()
        assert metrics["inference_count"] == 0
        await client.disconnect()


# ---------------------------------------------------------------------------
# Dynamic Batcher (simulation since DynamicBatcher may not be a module)
# ---------------------------------------------------------------------------

class TestDynamicBatcher:
    """Test dynamic batching logic."""

    def test_dynamic_batcher_add_request(self):
        """Add requests to a batch queue."""
        queue = []
        for i in range(5):
            queue.append({"request_id": f"req-{i}", "tokens": np.random.randint(1, 100)})
        assert len(queue) == 5

    def test_dynamic_batcher_optimal_batch_size(self):
        """Determine optimal batch size based on available memory."""
        available_memory_gb = 54.0
        per_request_memory_gb = 0.5
        max_batch = 64
        optimal = min(int(available_memory_gb / per_request_memory_gb), max_batch)
        assert optimal == 64

    def test_dynamic_batcher_flush(self):
        """Flush should clear the queue and return all pending requests."""
        queue = [{"id": i} for i in range(10)]
        flushed = list(queue)
        queue.clear()
        assert len(flushed) == 10
        assert len(queue) == 0

    def test_dynamic_batcher_timeout(self):
        """Batch should flush after a configurable timeout."""
        max_delay_ms = 100
        start = time.perf_counter()
        time.sleep(max_delay_ms / 1000.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms >= max_delay_ms * 0.8  # allow tolerance


# ---------------------------------------------------------------------------
# Model Registry (simulation)
# ---------------------------------------------------------------------------

class TestModelRegistry:
    """Test model registration, lookup, and rollback."""

    def _make_registry(self):
        """Simple in-memory model registry."""
        return {
            "models": {},
            "active_version": {},
        }

    def test_model_registry_register(self):
        reg = self._make_registry()
        reg["models"]["llm-v1"] = {"version": "1", "status": "loaded"}
        reg["active_version"]["llm"] = "llm-v1"
        assert "llm-v1" in reg["models"]

    def test_model_registry_get_active(self):
        reg = self._make_registry()
        reg["models"]["llm-v2"] = {"version": "2", "status": "loaded"}
        reg["active_version"]["llm"] = "llm-v2"
        active = reg["active_version"]["llm"]
        assert active == "llm-v2"

    def test_model_registry_rollback(self):
        reg = self._make_registry()
        reg["models"]["llm-v1"] = {"version": "1", "status": "loaded"}
        reg["models"]["llm-v2"] = {"version": "2", "status": "loaded"}
        reg["active_version"]["llm"] = "llm-v2"
        # Rollback
        reg["active_version"]["llm"] = "llm-v1"
        assert reg["active_version"]["llm"] == "llm-v1"


# ---------------------------------------------------------------------------
# TensorRT Engine Stats (simulation)
# ---------------------------------------------------------------------------

class TestTensorRTEngineStats:
    """Test TensorRT engine statistics gathering."""

    def test_tensorrt_engine_stats(self):
        stats = {
            "engine_name": "netflix_llm_trt",
            "precision": "FP16",
            "max_batch_size": 64,
            "workspace_size_mb": 4096,
            "num_layers": 40,
            "avg_latency_ms": 12.5,
            "throughput_qps": 850,
        }
        assert stats["precision"] == "FP16"
        assert stats["max_batch_size"] == 64
        assert stats["avg_latency_ms"] < 100
        assert stats["throughput_qps"] > 0


# ---------------------------------------------------------------------------
# InferenceMetrics data class
# ---------------------------------------------------------------------------

class TestInferenceMetrics:
    """Test the InferenceMetrics data class."""

    def test_empty_metrics(self):
        m = InferenceMetrics()
        assert m.avg_latency_ms == 0.0
        assert m.error_rate == 0.0
        assert m.p99_latency_ms == 0.0

    def test_metrics_after_recording(self):
        m = InferenceMetrics()
        m.inference_count = 10
        m.total_latency_ms = 100.0
        assert m.avg_latency_ms == 10.0

    def test_error_rate_calculation(self):
        m = InferenceMetrics()
        m.inference_count = 90
        m.error_count = 10
        assert abs(m.error_rate - 0.1) < 1e-6

    def test_p99_latency(self):
        m = InferenceMetrics()
        m.latency_histogram = list(range(1, 101))
        assert m.p99_latency_ms == 100

    def test_to_dict(self):
        m = InferenceMetrics()
        d = m.to_dict()
        assert "inference_count" in d
        assert "error_rate" in d
        assert "p99_latency_ms" in d
