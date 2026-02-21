"""
Netflix Real-Time LLM Personalization & Inference Platform
Pytest Configuration and Shared Fixtures
"""

import os
import sys

import pytest
import numpy as np


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client():
    """FastAPI TestClient wired to the application instance.

    Processes requests in-process without a real HTTP server.
    """
    from fastapi.testclient import TestClient
    from src.api.main import app

    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Settings instance with GPU disabled and test-safe defaults."""
    from src.core.config.settings import Settings
    return Settings()


@pytest.fixture
def mock_redis():
    """In-process fake Redis instance backed by fakeredis."""
    try:
        import fakeredis
        return fakeredis.FakeRedis(decode_responses=False)
    except ImportError:
        pytest.skip("fakeredis is not installed")


@pytest.fixture
def sample_user_embedding():
    """Deterministic 768-dimensional user embedding vector (unit length)."""
    rng = np.random.RandomState(42)
    emb = rng.randn(768).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    return emb


@pytest.fixture
def sample_content_embeddings():
    """List of five deterministic 768-d content embeddings."""
    rng = np.random.RandomState(123)
    embeddings = []
    for _ in range(5):
        vec = rng.randn(768).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        embeddings.append(vec)
    return embeddings


@pytest.fixture
def sample_inference_request():
    """Typical inference request payload for API tests."""
    return {
        "user_id": "user-test-001",
        "content_ids": [
            "content-001",
            "content-002",
            "content-003",
        ],
        "context": {
            "time_of_day": "evening",
            "region": "us-east-1",
            "device_type": "smart_tv",
            "language": "en",
        },
        "max_results": 10,
    }


@pytest.fixture
def sample_gpu_status():
    """Mock GPU device status dictionary (8x A100 fleet)."""
    return {
        "gpu_count": 8,
        "devices": [
            {
                "index": i,
                "name": "NVIDIA A100 80GB SXM",
                "temperature_c": 65,
                "utilization_gpu_pct": 72.5,
                "utilization_memory_pct": 68.0,
                "memory_used_gb": 54.4,
                "memory_total_gb": 80.0,
                "power_draw_w": 295,
                "status": "healthy",
            }
            for i in range(8)
        ],
    }
