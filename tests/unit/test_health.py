"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Unit Tests for Health Check API Endpoints
Author: Gopi Krishna Vajrala
============================================================================

Tests cover:
    - GET /health - Basic liveness check
    - GET /health/ready - Readiness check with dependency status
    - GET /health/detailed - Detailed health with component info
    - Response status codes, content types, and JSON structure
    - Health check response fields validation
============================================================================
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """
    Creates a FastAPI TestClient for testing HTTP endpoints.

    WHY: TestClient lets us test API endpoints without starting
    a real HTTP server. Requests are handled in-process.
    """
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health - Basic liveness check."""

    def test_health_returns_200(self, client):
        """Health endpoint returns HTTP 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_json(self, client):
        """Health endpoint returns JSON content type."""
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"

    def test_health_status_is_healthy(self, client):
        """Health response status field is 'healthy'."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_has_timestamp(self, client):
        """Health response includes a timestamp."""
        response = client.get("/health")
        data = response.json()
        assert "timestamp" in data
        assert len(data["timestamp"]) > 0

    def test_health_has_uptime(self, client):
        """Health response includes uptime_seconds."""
        response = client.get("/health")
        data = response.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_health_has_service_name(self, client):
        """Health response includes service name 'ECTP'."""
        response = client.get("/health")
        data = response.json()
        assert data["service"] == "ECTP"

    def test_health_has_version(self, client):
        """Health response includes version."""
        response = client.get("/health")
        data = response.json()
        assert "version" in data
        assert data["version"] == "1.0.0"

    def test_health_response_structure(self, client):
        """Health response has all required fields."""
        response = client.get("/health")
        data = response.json()
        required_fields = {"status", "timestamp", "uptime_seconds", "service", "version"}
        assert required_fields.issubset(set(data.keys()))


class TestReadinessEndpoint:
    """Tests for GET /health/ready - Readiness check."""

    def test_readiness_returns_200(self, client):
        """Readiness endpoint returns HTTP 200 when all healthy."""
        response = client.get("/health/ready")
        assert response.status_code == 200

    def test_readiness_returns_json(self, client):
        """Readiness endpoint returns JSON content type."""
        response = client.get("/health/ready")
        assert response.headers["content-type"] == "application/json"

    def test_readiness_has_status(self, client):
        """Readiness response includes status field."""
        response = client.get("/health/ready")
        data = response.json()
        assert "status" in data

    def test_readiness_has_timestamp(self, client):
        """Readiness response includes timestamp."""
        response = client.get("/health/ready")
        data = response.json()
        assert "timestamp" in data

    def test_readiness_has_dependencies(self, client):
        """Readiness response includes dependencies section."""
        response = client.get("/health/ready")
        data = response.json()
        assert "dependencies" in data
        assert isinstance(data["dependencies"], dict)

    def test_readiness_checks_database(self, client):
        """Readiness checks database dependency."""
        response = client.get("/health/ready")
        data = response.json()
        assert "database" in data["dependencies"]

    def test_readiness_checks_redis(self, client):
        """Readiness checks Redis dependency."""
        response = client.get("/health/ready")
        data = response.json()
        assert "redis" in data["dependencies"]

    def test_readiness_checks_aws(self, client):
        """Readiness checks AWS dependency."""
        response = client.get("/health/ready")
        data = response.json()
        assert "aws" in data["dependencies"]

    def test_readiness_dependency_has_status(self, client):
        """Each dependency has a status field."""
        response = client.get("/health/ready")
        data = response.json()
        for dep_name, dep_info in data["dependencies"].items():
            assert "status" in dep_info, f"Dependency '{dep_name}' missing status"


class TestDetailedHealthEndpoint:
    """Tests for GET /health/detailed - Detailed health status."""

    def test_detailed_returns_200(self, client):
        """Detailed health returns HTTP 200."""
        response = client.get("/health/detailed")
        assert response.status_code == 200

    def test_detailed_returns_json(self, client):
        """Detailed health returns JSON content type."""
        response = client.get("/health/detailed")
        assert response.headers["content-type"] == "application/json"

    def test_detailed_has_status(self, client):
        """Detailed response has status field."""
        response = client.get("/health/detailed")
        data = response.json()
        assert data["status"] == "healthy"

    def test_detailed_has_service_info(self, client):
        """Detailed response has service information."""
        response = client.get("/health/detailed")
        data = response.json()
        assert "service" in data
        service = data["service"]
        assert service["name"] == "ECTP"
        assert "version" in service

    def test_detailed_has_components(self, client):
        """Detailed response has components section."""
        response = client.get("/health/detailed")
        data = response.json()
        assert "components" in data
        components = data["components"]
        assert "api" in components
        assert "database" in components
        assert "cache" in components

    def test_detailed_has_integrations(self, client):
        """Detailed response has integrations section."""
        response = client.get("/health/detailed")
        data = response.json()
        assert "integrations" in data
        integrations = data["integrations"]
        assert "servicenow" in integrations
        assert "ellucian" in integrations
        assert "aws" in integrations

    def test_detailed_has_uptime(self, client):
        """Detailed response has uptime information."""
        response = client.get("/health/detailed")
        data = response.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_detailed_has_timestamp(self, client):
        """Detailed response has timestamp."""
        response = client.get("/health/detailed")
        data = response.json()
        assert "timestamp" in data

    def test_detailed_author_attribution(self, client):
        """Detailed response includes author attribution."""
        response = client.get("/health/detailed")
        data = response.json()
        assert data["service"]["author"] == "Gopi Krishna Vajrala"


class TestNonExistentEndpoints:
    """Tests for error handling on non-existent endpoints."""

    def test_404_for_unknown_path(self, client):
        """Non-existent endpoint returns 404."""
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_404_for_health_typo(self, client):
        """Typo in health path returns 404."""
        response = client.get("/healthz")
        assert response.status_code == 404
