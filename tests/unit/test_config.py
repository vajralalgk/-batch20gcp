"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Unit Tests for Core Configuration (settings.py)
Author: Gopi Krishna Vajrala
============================================================================

Tests cover:
    - Default values for all settings fields
    - Environment variable loading via ECTP_ prefix
    - Validation of log_level and env fields
    - Database URL construction (async PostgreSQL)
    - Redis URL construction (with and without password)
    - CORS origins parsing from comma-separated string
    - Production detection property
    - Singleton behavior via get_settings()
============================================================================
"""

import os
import pytest

from src.core.config.settings import Settings, get_settings


class TestDefaultValues:
    """Tests that all settings have correct default values."""

    def test_default_app_name(self):
        """App name defaults to 'ECTP'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.app_name == "ECTP"

    def test_default_app_version(self):
        """App version defaults to '1.0.0'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.app_version == "1.0.0"

    def test_default_env(self):
        """Environment defaults to 'development'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.env == "development"

    def test_default_debug_is_false(self):
        """Debug is False by default for security."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.debug is False

    def test_default_log_level(self):
        """Log level defaults to 'INFO'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.log_level == "INFO"

    def test_default_host(self):
        """Host defaults to '0.0.0.0'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.host == "0.0.0.0"

    def test_default_port(self):
        """Port defaults to 8000."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.port == 8000

    def test_default_db_port(self):
        """Database port defaults to 5432."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.db_port == 5432

    def test_default_db_name(self):
        """Database name defaults to 'ectp'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.db_name == "ectp"

    def test_default_db_pool_size(self):
        """Connection pool size defaults to 10."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.db_pool_size == 10

    def test_default_db_max_overflow(self):
        """Max overflow defaults to 20."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.db_max_overflow == 20

    def test_default_redis_host(self):
        """Redis host defaults to 'localhost'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_host == "localhost"

    def test_default_redis_port(self):
        """Redis port defaults to 6379."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_port == 6379

    def test_default_redis_db(self):
        """Redis database defaults to 0."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_db == 0

    def test_default_aws_region(self):
        """AWS region defaults to 'us-east-1'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.aws_region == "us-east-1"

    def test_default_jwt_algorithm(self):
        """JWT algorithm defaults to 'HS256'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.jwt_algorithm == "HS256"

    def test_default_jwt_expire_minutes(self):
        """JWT token expiry defaults to 30 minutes."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.jwt_access_token_expire_minutes == 30

    def test_default_cors_origins(self):
        """CORS origins defaults to 'http://localhost:3000'."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.cors_origins == "http://localhost:3000"


class TestEnvironmentVariableLoading:
    """Tests that settings load correctly from constructor (simulating env vars)."""

    def test_custom_app_name(self):
        settings = Settings(
            app_name="Custom-ECTP",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.app_name == "Custom-ECTP"

    def test_custom_port(self):
        settings = Settings(
            port=9000,
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.port == 9000

    def test_custom_db_settings(self):
        settings = Settings(
            db_host="prod-db.example.com",
            db_port=5433,
            db_name="ectp_prod",
            db_user="prod_user",
            db_password="prod_password",
            jwt_secret_key="key",
        )
        assert settings.db_host == "prod-db.example.com"
        assert settings.db_port == 5433
        assert settings.db_name == "ectp_prod"
        assert settings.db_user == "prod_user"
        assert settings.db_password == "prod_password"

    def test_debug_enabled(self):
        settings = Settings(
            debug=True,
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.debug is True


class TestValidation:
    """Tests for settings field validation."""

    def test_valid_log_levels(self):
        """All standard Python log levels are accepted."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            settings = Settings(
                log_level=level,
                db_host="localhost", db_user="test",
                db_password="test", jwt_secret_key="key",
            )
            assert settings.log_level == level

    def test_log_level_case_insensitive(self):
        """Log level validation is case-insensitive."""
        settings = Settings(
            log_level="debug",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.log_level == "DEBUG"

    def test_invalid_log_level_raises_error(self):
        """Invalid log level raises a validation error."""
        with pytest.raises(Exception):
            Settings(
                log_level="INVALID",
                db_host="localhost", db_user="test",
                db_password="test", jwt_secret_key="key",
            )

    def test_valid_environments(self):
        """All four deployment environments are accepted."""
        for env in ["development", "qa", "uat", "production"]:
            settings = Settings(
                env=env,
                db_host="localhost", db_user="test",
                db_password="test", jwt_secret_key="key",
            )
            assert settings.env == env

    def test_env_case_insensitive(self):
        """Environment validation is case-insensitive."""
        settings = Settings(
            env="PRODUCTION",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.env == "production"

    def test_invalid_env_raises_error(self):
        """Invalid environment name raises a validation error."""
        with pytest.raises(Exception):
            Settings(
                env="staging",
                db_host="localhost", db_user="test",
                db_password="test", jwt_secret_key="key",
            )


class TestDatabaseUrlConstruction:
    """Tests for the database_url property."""

    def test_database_url_format(self):
        """Database URL follows asyncpg format."""
        settings = Settings(
            db_host="myhost", db_port=5432, db_name="mydb",
            db_user="myuser", db_password="mypass",
            jwt_secret_key="key",
        )
        assert settings.database_url == "postgresql+asyncpg://myuser:mypass@myhost:5432/mydb"

    def test_database_url_contains_asyncpg_driver(self):
        """Database URL uses the asyncpg driver."""
        settings = Settings(
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert "asyncpg" in settings.database_url

    def test_database_url_custom_port(self):
        """Database URL uses custom port when specified."""
        settings = Settings(
            db_host="localhost", db_port=5433,
            db_user="test", db_password="test",
            jwt_secret_key="key",
        )
        assert ":5433/" in settings.database_url

    def test_database_url_includes_all_components(self):
        """Database URL includes host, port, user, password, and database name."""
        settings = Settings(
            db_host="dbhost", db_port=5432, db_name="testdb",
            db_user="testuser", db_password="testpass",
            jwt_secret_key="key",
        )
        url = settings.database_url
        assert "testuser" in url
        assert "testpass" in url
        assert "dbhost" in url
        assert "5432" in url
        assert "testdb" in url


class TestRedisUrlConstruction:
    """Tests for the redis_url property."""

    def test_redis_url_without_password(self):
        """Redis URL omits password for local development."""
        settings = Settings(
            redis_host="localhost", redis_port=6379,
            redis_db=0, redis_password="",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_url == "redis://localhost:6379/0"

    def test_redis_url_with_password(self):
        """Redis URL includes password when set."""
        settings = Settings(
            redis_host="cache.example.com", redis_port=6379,
            redis_db=0, redis_password="redis_secret",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_url == "redis://:redis_secret@cache.example.com:6379/0"

    def test_redis_url_custom_db(self):
        """Redis URL uses custom database number."""
        settings = Settings(
            redis_host="localhost", redis_port=6379,
            redis_db=5, redis_password="",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.redis_url.endswith("/5")

    def test_redis_url_custom_port(self):
        """Redis URL uses custom port."""
        settings = Settings(
            redis_host="localhost", redis_port=6380,
            redis_db=0, redis_password="",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert ":6380/" in settings.redis_url


class TestCorsOriginsParsing:
    """Tests for the cors_origins_list property."""

    def test_single_origin(self):
        """Single CORS origin parsed correctly."""
        settings = Settings(
            cors_origins="http://localhost:3000",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.cors_origins_list == ["http://localhost:3000"]

    def test_multiple_origins(self):
        """Multiple comma-separated origins parsed correctly."""
        settings = Settings(
            cors_origins="http://localhost:3000,https://portal.example.edu,https://admin.example.edu",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        origins = settings.cors_origins_list
        assert len(origins) == 3
        assert "http://localhost:3000" in origins
        assert "https://portal.example.edu" in origins
        assert "https://admin.example.edu" in origins

    def test_origins_with_spaces_stripped(self):
        """Whitespace around origins is stripped."""
        settings = Settings(
            cors_origins="http://localhost:3000 , http://localhost:8080 ",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        origins = settings.cors_origins_list
        assert "http://localhost:3000" in origins
        assert "http://localhost:8080" in origins


class TestProductionDetection:
    """Tests for the is_production property."""

    def test_production_detected(self):
        settings = Settings(
            env="production",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.is_production is True

    def test_development_not_production(self):
        settings = Settings(
            env="development",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.is_production is False

    def test_qa_not_production(self):
        settings = Settings(
            env="qa",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.is_production is False

    def test_uat_not_production(self):
        settings = Settings(
            env="uat",
            db_host="localhost", db_user="test",
            db_password="test", jwt_secret_key="key",
        )
        assert settings.is_production is False


class TestSettingsConfig:
    """Tests for Pydantic settings configuration."""

    def test_env_prefix(self):
        """Settings use ECTP_ prefix for environment variables."""
        assert hasattr(Settings, 'Config') and Settings.Config.env_prefix == "ECTP_"
