"""Tests for health check API endpoints (src.api.routes.health)."""

import pytest


class TestHealthCheck:
    """Tests for GET /health."""

    def test_health_check(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_format(self, app_client):
        resp = app_client.get("/health")
        data = resp.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert "service" in data
        assert "name" in data["service"]
        assert "version" in data["service"]

    def test_health_returns_json(self, app_client):
        resp = app_client.get("/health")
        assert resp.headers["content-type"] == "application/json"


class TestReadinessCheck:
    """Tests for GET /health/ready."""

    def test_readiness_check(self, app_client):
        resp = app_client.get("/health/ready")
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("ready", "not_ready")
        assert "components" in data

    def test_readiness_has_components(self, app_client):
        resp = app_client.get("/health/ready")
        data = resp.json()
        components = data["components"]
        assert "inference_server" in components
        assert "gpu_fleet" in components
        assert "redis_feature_store" in components


class TestDetailedHealth:
    """Tests for GET /health/detailed."""

    def test_detailed_health(self, app_client):
        resp = app_client.get("/health/detailed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_includes_gpu_status(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "gpu_metrics" in data
        assert "devices" in data["gpu_metrics"]
        devices = data["gpu_metrics"]["devices"]
        assert len(devices) == 8
        for device in devices:
            assert "temperature_c" in device
            assert "utilization_gpu_pct" in device
            assert "status" in device

    def test_health_includes_kv_cache_status(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "kv_cache" in data
        kv = data["kv_cache"]
        assert "status" in kv
        assert "total_blocks" in kv
        assert "hit_rate_pct" in kv

    def test_detailed_health_includes_session_memory(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "session_memory" in data
        assert "active_sessions" in data["session_memory"]

    def test_detailed_health_includes_multi_region(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "multi_region" in data
        assert "active_regions" in data["multi_region"]
        assert len(data["multi_region"]["active_regions"]) >= 1

    def test_detailed_health_includes_model_registry(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "model_registry" in data
        assert "models" in data["model_registry"]

    def test_detailed_health_includes_inference_engine(self, app_client):
        resp = app_client.get("/health/detailed")
        data = resp.json()
        assert "inference_engine" in data
        assert "throughput_tokens_per_sec" in data["inference_engine"]


class TestNonExistentEndpoints:
    """Tests for error handling on non-existent endpoints."""

    def test_404_for_unknown_path(self, app_client):
        response = app_client.get("/nonexistent")
        assert response.status_code == 404

    def test_404_for_health_typo(self, app_client):
        response = app_client.get("/healthz")
        assert response.status_code == 404
