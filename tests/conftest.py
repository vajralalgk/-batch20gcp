"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Pytest Configuration and Shared Fixtures
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    conftest.py is automatically loaded by pytest and provides shared
    test fixtures available to ALL test files without explicit import.
    This enables:
    1. Consistent test setup across all test modules
    2. Reusable mock objects for external dependencies
    3. Test isolation (each test gets fresh fixtures)
    4. Clean test code (no boilerplate setup in each test)

FIXTURES PROVIDED:
    - test_settings: Settings instance with known test values
    - test_client: FastAPI TestClient for HTTP endpoint testing
    - sample_migration_plan: Sample migration plan dict
    - sample_incident: Sample ServiceNow incident dict
    - sample_cost_report: Sample cost governance report dict
    - mock_aws_credentials: Mock AWS credentials for testing
    - clean_env: Ensures clean environment variables for tests
============================================================================
"""

import os
import pytest


# Set test environment variables BEFORE importing application modules.
# WHY: Settings are loaded at import time, so env vars must be set first.
os.environ["ECTP_ENV"] = "development"
os.environ["ECTP_DEBUG"] = "true"
os.environ["ECTP_LOG_LEVEL"] = "DEBUG"
os.environ["ECTP_DB_HOST"] = "localhost"
os.environ["ECTP_DB_NAME"] = "ectp_test"
os.environ["ECTP_DB_USER"] = "test_user"
os.environ["ECTP_DB_PASSWORD"] = "test_password"
os.environ["ECTP_JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["ECTP_AWS_REGION"] = "us-east-1"
os.environ["ECTP_AWS_ACCOUNT_ID"] = "123456789012"
os.environ["ECTP_REDIS_HOST"] = "localhost"
os.environ["ECTP_REDIS_PORT"] = "6379"
os.environ["ECTP_CORS_ORIGINS"] = "http://localhost:3000,http://localhost:8080"


@pytest.fixture
def test_settings():
    """
    Provides a test Settings instance with known values.

    WHY: Tests need predictable configuration values.
    This fixture ensures tests don't depend on the developer's
    local .env file or system environment variables.
    """
    from src.core.config.settings import Settings
    return Settings(
        app_name="ECTP-Test",
        env="development",
        debug=True,
        log_level="DEBUG",
        host="0.0.0.0",
        port=8000,
        db_host="localhost",
        db_port=5432,
        db_name="ectp_test",
        db_user="test_user",
        db_password="test_password",
        db_pool_size=5,
        db_max_overflow=10,
        redis_host="localhost",
        redis_port=6379,
        redis_db=0,
        redis_password="",
        aws_region="us-east-1",
        aws_account_id="123456789012",
        jwt_secret_key="test-secret-key-for-testing-only",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=60,
        cors_origins="http://localhost:3000,http://localhost:8080",
    )


@pytest.fixture
def test_client():
    """
    Provides a FastAPI TestClient for HTTP endpoint testing.

    WHY: TestClient processes requests in-process without a real
    HTTP server, making tests fast and isolated.
    """
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


@pytest.fixture
def sample_migration_plan():
    """
    Provides a sample migration plan for testing.

    WHY: Many tests need a valid migration plan object.
    This fixture provides one with all required fields populated.
    """
    return {
        "name": "Test Banner DB Migration",
        "description": "Migrate Banner database from on-prem to AWS RDS",
        "strategy": "replatform",
        "source_system": "Banner Oracle DB",
        "target_aws_service": "RDS PostgreSQL",
        "priority": 2,
        "owner": "Cloud Operations",
        "estimated_downtime_minutes": 120,
        "estimated_cost": 5000.00,
        "tags": ["database", "banner", "critical"],
    }


@pytest.fixture
def sample_incident():
    """Provides a sample ServiceNow incident for testing."""
    return {
        "short_description": "Test RDS connection pool exhausted",
        "description": "Database connection pool reached maximum capacity during peak enrollment",
        "priority": "2",
        "category": "Cloud Infrastructure",
        "assignment_group": "Cloud Operations",
        "impact": "2",
        "urgency": "2",
        "caller_id": "test_user",
    }


@pytest.fixture
def sample_cost_report():
    """Provides a sample cost governance report for testing."""
    return {
        "period": "2026-02",
        "total_cost": 28500.00,
        "budget": 32000.00,
        "variance_percent": -10.9,
        "breakdown": {
            "compute": 14200.00,
            "database": 7500.00,
            "storage": 2800.00,
            "networking": 1800.00,
            "monitoring": 1200.00,
            "other": 1000.00,
        },
        "recommendations": [
            "Right-size dev environment ECS tasks",
            "Convert QA RDS to reserved instance",
            "Enable S3 Intelligent-Tiering",
        ],
    }


@pytest.fixture
def mock_aws_credentials():
    """
    Provides mock AWS credentials for testing.

    WHY: Tests should never use real AWS credentials.
    These mock values prevent accidental API calls.
    """
    return {
        "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "aws_region": "us-east-1",
        "aws_account_id": "123456789012",
    }


@pytest.fixture
def clean_env(monkeypatch):
    """
    Ensures a clean environment for tests that depend on specific env vars.

    WHY: Some tests need to verify behavior with specific environment
    variable combinations. This fixture removes ECTP_ vars and lets
    individual tests set only what they need.
    """
    env_vars_to_clean = [
        key for key in os.environ.keys()
        if key.startswith("ECTP_") and key not in (
            "ECTP_ENV", "ECTP_DB_HOST", "ECTP_DB_USER",
            "ECTP_DB_PASSWORD", "ECTP_JWT_SECRET_KEY",
        )
    ]
    for var in env_vars_to_clean:
        monkeypatch.delenv(var, raising=False)
