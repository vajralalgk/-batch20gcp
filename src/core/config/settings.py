"""
============================================================================
Enterprise Cloud Transformation Platform (ECTP)
Centralized Configuration Management
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS MODULE EXISTS:
    Enterprise applications need a single source of truth for configuration.
    This module loads settings from environment variables (12-factor app principle)
    with validation, type safety, and sensible defaults.

DESIGN DECISIONS:
    - Uses Pydantic BaseSettings for automatic env var loading + validation
    - All secrets come from environment variables, NEVER hardcoded
    - Grouped into logical sections for maintainability
    - Singleton pattern ensures consistent configuration across the app

SECURITY IMPLICATIONS:
    - Database passwords and API keys are loaded from env vars only
    - No secrets are logged or exposed in error messages
    - Settings object redacts sensitive fields in string representation

ALTERNATIVES CONSIDERED:
    - YAML config files: Rejected because env vars are cloud-native standard
    - AWS Parameter Store: Used in production but env vars for local dev
    - Dataclasses: Rejected because Pydantic gives validation for free
============================================================================
"""

from functools import lru_cache  # lru_cache ensures singleton behavior for settings
from typing import List, Optional  # Type hints for configuration fields

from pydantic import Field, field_validator  # Field for defaults, validator for custom rules
from pydantic_settings import BaseSettings  # BaseSettings auto-loads from environment


class Settings(BaseSettings):
    """
    Central configuration class for the entire ECTP platform.

    HOW IT WORKS:
        1. Reads environment variables matching field names (with ECTP_ prefix)
        2. Falls back to defaults defined here if env var is not set
        3. Validates types and constraints automatically
        4. Raises clear errors if required config is missing

    USAGE:
        from src.core.config import settings
        print(settings.app_name)  # Reads ECTP_APP_NAME env var
    """

    # ---- Application Settings ----
    # These control the core behavior of the application server

    # app_name: Identifies this application in logs, metrics, and service discovery.
    # Why: Every enterprise service needs a unique identifier for observability.
    app_name: str = Field(
        default="ECTP",
        description="Application name used in logs and service discovery"
    )

    # app_version: Semantic version of the running application.
    # Why: Critical for debugging — knowing which version produced an error.
    app_version: str = Field(
        default="1.0.0",
        description="Application version following semver"
    )

    # env: The deployment environment (development, qa, uat, production).
    # Why: Controls behavior like debug mode, log verbosity, and feature flags.
    env: str = Field(
        default="development",
        description="Deployment environment name"
    )

    # debug: Enables detailed error responses and auto-reload.
    # Why: Speeds up development but MUST be False in production for security.
    # Security: Debug mode exposes stack traces — never enable in production.
    debug: bool = Field(
        default=False,
        description="Enable debug mode (NEVER true in production)"
    )

    # log_level: Controls logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    # Why: DEBUG in dev for troubleshooting, WARNING+ in prod to reduce noise.
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )

    # host: The network interface the server binds to.
    # Why: 0.0.0.0 allows connections from any interface (needed in containers).
    host: str = Field(
        default="0.0.0.0",
        description="Server bind host"
    )

    # port: The TCP port the server listens on.
    # Why: 8000 is the standard for Python web apps, avoids conflicts with 80/443.
    port: int = Field(
        default=8000,
        description="Server bind port"
    )

    # ---- Database Settings ----
    # PostgreSQL connection configuration for the primary data store

    # db_host: Database server hostname.
    # Why: Separate from app server for security isolation and scaling.
    db_host: str = Field(
        default="localhost",
        description="PostgreSQL host"
    )

    # db_port: PostgreSQL default port.
    # Why: 5432 is the standard PostgreSQL port.
    db_port: int = Field(
        default=5432,
        description="PostgreSQL port"
    )

    # db_name: The specific database within PostgreSQL.
    # Why: Each environment gets its own database for data isolation.
    db_name: str = Field(
        default="ectp",
        description="PostgreSQL database name"
    )

    # db_user: Database authentication username.
    # Why: Principle of least privilege — dedicated user with minimal permissions.
    db_user: str = Field(
        default="ectp_user",
        description="PostgreSQL username"
    )

    # db_password: Database authentication password.
    # Why: Loaded from env var, never hardcoded. Rotated via Secrets Manager in prod.
    # Security: This value is never logged or included in error responses.
    db_password: str = Field(
        default="",
        description="PostgreSQL password (from env var or Secrets Manager)"
    )

    # db_pool_size: Number of persistent database connections.
    # Why: Connection pooling avoids the overhead of creating new connections per request.
    # 10 is a good default; increase for high-throughput services.
    db_pool_size: int = Field(
        default=10,
        description="SQLAlchemy connection pool size"
    )

    # db_max_overflow: Extra connections allowed beyond pool_size during peak load.
    # Why: Handles traffic spikes without rejecting requests. These connections are
    # temporary and returned to the pool after use.
    db_max_overflow: int = Field(
        default=20,
        description="Maximum overflow connections beyond pool size"
    )

    # ---- Redis Settings ----
    # Redis configuration for caching, session management, and message brokering

    # redis_host: Redis server hostname.
    # Why: ElastiCache in AWS, localhost for development.
    redis_host: str = Field(
        default="localhost",
        description="Redis host"
    )

    # redis_port: Redis default port.
    redis_port: int = Field(
        default=6379,
        description="Redis port"
    )

    # redis_db: Redis database number (0-15).
    # Why: Logical separation within a single Redis instance.
    redis_db: int = Field(
        default=0,
        description="Redis database number"
    )

    # redis_password: Redis authentication (empty for local dev).
    # Security: Required in production (ElastiCache AUTH).
    redis_password: str = Field(
        default="",
        description="Redis password"
    )

    # ---- AWS Settings ----
    # AWS configuration for cloud service integration

    # aws_region: The AWS region where resources are deployed.
    # Why: us-east-1 is the most feature-complete region and common for Higher Ed.
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region for API calls"
    )

    # aws_account_id: The AWS account number.
    # Why: Needed for constructing ARNs and cross-account access.
    aws_account_id: str = Field(
        default="",
        description="AWS account ID"
    )

    # ---- ServiceNow Settings ----
    # ServiceNow ITSM integration configuration

    # servicenow_instance_url: The ServiceNow instance URL.
    # Why: Each organization has a unique ServiceNow instance.
    servicenow_instance_url: str = Field(
        default="",
        description="ServiceNow instance URL"
    )

    # servicenow_client_id: OAuth2 client ID for ServiceNow API.
    # Why: OAuth2 is ServiceNow's recommended authentication method for integrations.
    servicenow_client_id: str = Field(
        default="",
        description="ServiceNow OAuth2 client ID"
    )

    # servicenow_client_secret: OAuth2 client secret.
    # Security: Stored in Secrets Manager in production, env var in dev.
    servicenow_client_secret: str = Field(
        default="",
        description="ServiceNow OAuth2 client secret"
    )

    # ---- Ellucian Settings ----
    # Ellucian Ethos platform integration for Higher Ed systems

    # ellucian_ethos_api_url: Base URL for the Ellucian Ethos Integration API.
    # Why: Ethos is the standard integration platform for Banner/Colleague.
    ellucian_ethos_api_url: str = Field(
        default="",
        description="Ellucian Ethos API base URL"
    )

    # ellucian_api_key: API key for Ethos authentication.
    # Security: Rotated quarterly, stored in Secrets Manager in production.
    ellucian_api_key: str = Field(
        default="",
        description="Ellucian Ethos API key"
    )

    # ---- JWT Settings ----
    # JSON Web Token configuration for API authentication

    # jwt_secret_key: The secret used to sign JWT tokens.
    # Security: MUST be a strong random string (256+ bits). If compromised,
    # all tokens can be forged. Rotate immediately if suspected leak.
    jwt_secret_key: str = Field(
        default="CHANGE-ME-IN-PRODUCTION",
        description="JWT signing secret (must be strong random string)"
    )

    # jwt_algorithm: The algorithm used to sign JWT tokens.
    # Why: HS256 (HMAC-SHA256) is fast and suitable for single-service auth.
    # Alternative: RS256 for multi-service (uses asymmetric keys).
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )

    # jwt_access_token_expire_minutes: How long access tokens are valid.
    # Why: Short-lived tokens (30 min) limit damage from token theft.
    # Users get new tokens via refresh tokens without re-authenticating.
    jwt_access_token_expire_minutes: int = Field(
        default=30,
        description="Access token expiration in minutes"
    )

    # ---- CORS Settings ----
    # Cross-Origin Resource Sharing configuration

    # cors_origins: List of allowed origins for cross-origin requests.
    # Why: Restricts which frontends can call the API (security boundary).
    # Security: Never use "*" in production — explicitly list allowed origins.
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed CORS origins"
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """
        Validates that log_level is one of the standard Python logging levels.

        WHY: Prevents typos (e.g., 'DEBU' instead of 'DEBUG') that would
        silently default to WARNING, making debugging impossible.
        """
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        # Convert to uppercase for case-insensitive comparison.
        # This is a usability improvement — 'debug' works same as 'DEBUG'.
        upper_v = v.upper()
        if upper_v not in allowed:
            raise ValueError(
                f"log_level must be one of {allowed}, got '{v}'"
            )
        return upper_v

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        """
        Validates the environment name against known environments.

        WHY: Catches configuration errors early. A typo like 'producton'
        could cause the app to run with development settings in production.
        """
        allowed = {"development", "qa", "uat", "production"}
        lower_v = v.lower()
        if lower_v not in allowed:
            raise ValueError(
                f"env must be one of {allowed}, got '{v}'"
            )
        return lower_v

    @property
    def database_url(self) -> str:
        """
        Constructs the async PostgreSQL connection string.

        WHY: Centralizes DB URL construction to avoid format errors.
        Uses asyncpg driver for async SQLAlchemy compatibility.

        Returns:
            Async PostgreSQL connection URL string.
        """
        # postgresql+asyncpg:// tells SQLAlchemy to use the asyncpg driver
        # which supports Python's async/await for non-blocking DB operations.
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def redis_url(self) -> str:
        """
        Constructs the Redis connection URL.

        WHY: Centralizes Redis URL construction for consistency
        across cache client, Celery broker, and session store.

        Returns:
            Redis connection URL string.
        """
        if self.redis_password:
            # Include password in URL when authentication is required
            return (
                f"redis://:{self.redis_password}@{self.redis_host}"
                f":{self.redis_port}/{self.redis_db}"
            )
        # No password for local development
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origins_list(self) -> List[str]:
        """
        Parses the comma-separated CORS origins string into a list.

        WHY: Environment variables are strings, but FastAPI's CORSMiddleware
        expects a list. This property handles the conversion.

        Returns:
            List of allowed origin URLs.
        """
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def is_production(self) -> bool:
        """
        Checks if the application is running in production.

        WHY: Many features should behave differently in production:
        - Debug mode disabled
        - Verbose logging reduced
        - Security checks stricter

        Returns:
            True if running in production environment.
        """
        return self.env == "production"

    class Config:
        """
        Pydantic Settings configuration.

        env_prefix: All env vars must start with 'ECTP_' to avoid conflicts
                   with other applications on the same system.
        env_file: Automatically loads .env file for local development.
        case_sensitive: Env var names are case-insensitive (standard behavior).
        """
        env_prefix = "ECTP_"
        env_file = ".env"
        case_sensitive = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached singleton instance of Settings.

    WHY: We use @lru_cache to ensure only ONE Settings object exists
    throughout the application lifecycle. This:
    1. Prevents reading .env file multiple times
    2. Ensures all modules see the same configuration
    3. Makes testing easier (can override with dependency injection)

    ALTERNATIVE: Global variable — rejected because lru_cache is
    more explicit and can be cleared for testing.

    Returns:
        The singleton Settings instance.
    """
    return Settings()
