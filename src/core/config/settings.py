"""Netflix LLM Platform - Centralized Configuration.

Uses pydantic-settings for type-safe configuration with automatic
environment variable loading. Every setting can be overridden via
environment variables using the prefix hierarchy (e.g., ``APP_NAME``,
``GPU_ENABLE_GPU``, ``REDIS_URL``).

Usage::

    from src.core.config import settings

    print(settings.app.name)
    print(settings.gpu.max_batch_size)
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Nested settings groups
# ---------------------------------------------------------------------------

class AppSettings(BaseSettings):
    """Core application identity and runtime settings."""

    model_config = SettingsConfigDict(env_prefix="APP_")

    name: str = Field(default="netflix-llm-platform", description="Application name")
    version: str = Field(default="1.0.0", description="Application version")
    environment: str = Field(default="development", description="Runtime environment (development|staging|production)")
    debug: bool = Field(default=False, description="Enable debug mode")
    host: str = Field(default="0.0.0.0", description="Server bind host")
    port: int = Field(default=8000, description="Server bind port")

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got '{v}'")
        return v


class GPUSettings(BaseSettings):
    """GPU acceleration and batching configuration."""

    model_config = SettingsConfigDict(env_prefix="GPU_")

    enable_gpu: bool = Field(default=True, description="Enable GPU acceleration")
    device_ids: List[int] = Field(default=[0], description="CUDA device IDs to use")
    tensor_parallel_size: int = Field(default=1, description="Tensor parallelism degree")
    memory_fraction: float = Field(
        default=0.90,
        ge=0.1,
        le=1.0,
        description="Fraction of GPU memory to allocate",
    )
    max_batch_size: int = Field(default=64, ge=1, description="Maximum inference batch size")
    dynamic_batching: bool = Field(
        default=True,
        description="Enable dynamic batching to maximise throughput",
    )


class TritonSettings(BaseSettings):
    """NVIDIA Triton Inference Server connection settings."""

    model_config = SettingsConfigDict(env_prefix="TRITON_")

    server_url: str = Field(
        default="localhost:8001",
        description="Triton gRPC endpoint",
    )
    model_name: str = Field(
        default="ensemble_llm",
        description="Triton model repository name",
    )
    model_version: str = Field(default="1", description="Model version to serve")
    max_queue_delay_ms: int = Field(
        default=100,
        ge=0,
        description="Maximum queue delay in milliseconds before flushing a batch",
    )


class ModelSettings(BaseSettings):
    """LLM model configuration."""

    model_config = SettingsConfigDict(env_prefix="MODEL_")

    model_name: str = Field(
        default="meta-llama/Llama-3-70B-Instruct",
        description="HuggingFace model identifier or local path",
    )
    quantization: Optional[str] = Field(
        default=None,
        description="Quantization method (awq|gptq|fp8|None)",
    )
    max_sequence_length: int = Field(
        default=4096,
        ge=1,
        description="Maximum input+output sequence length",
    )
    kv_cache_max_tokens: int = Field(
        default=32768,
        ge=1,
        description="Maximum tokens stored in the KV cache",
    )
    kv_cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        description="Time-to-live for KV cache entries in seconds",
    )

    @field_validator("quantization")
    @classmethod
    def validate_quantization(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"awq", "gptq", "fp8"}
            if v.lower() not in allowed:
                raise ValueError(f"quantization must be one of {allowed}, got '{v}'")
            return v.lower()
        return v


class RedisSettings(BaseSettings):
    """Redis connection and session cache settings."""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
    cluster_enabled: bool = Field(
        default=False,
        description="Connect to a Redis Cluster instead of standalone",
    )
    max_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum connections in the pool",
    )
    session_ttl_seconds: int = Field(
        default=1800,
        ge=0,
        description="Session data TTL in seconds (default 30 min)",
    )


class AWSSettings(BaseSettings):
    """AWS service configuration."""

    model_config = SettingsConfigDict(env_prefix="AWS_")

    region: str = Field(default="us-east-1", description="Primary AWS region")
    dynamodb_user_table: str = Field(
        default="netflix-llm-user-profiles",
        description="DynamoDB table for user profile data",
    )
    dynamodb_session_table: str = Field(
        default="netflix-llm-sessions",
        description="DynamoDB table for session state",
    )
    dynamodb_model_table: str = Field(
        default="netflix-llm-model-metadata",
        description="DynamoDB table for model metadata",
    )
    s3_bucket: str = Field(
        default="netflix-llm-artifacts",
        description="S3 bucket for model weights and artifacts",
    )


class ObservabilitySettings(BaseSettings):
    """Observability stack configuration (Prometheus, DCGM, OpenTelemetry)."""

    model_config = SettingsConfigDict(env_prefix="OBS_")

    prometheus_enabled: bool = Field(default=True, description="Enable Prometheus metrics")
    prometheus_port: int = Field(default=9090, description="Prometheus metrics port")
    dcgm_enabled: bool = Field(
        default=True,
        description="Enable NVIDIA DCGM GPU metrics exporter",
    )
    dcgm_port: int = Field(default=9400, description="DCGM exporter port")
    otel_enabled: bool = Field(
        default=True,
        description="Enable OpenTelemetry tracing",
    )
    otel_exporter_endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP gRPC exporter endpoint",
    )
    otel_service_name: str = Field(
        default="netflix-llm-platform",
        description="OTel service name tag",
    )
    otel_sample_rate: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Trace sampling rate (0.0 - 1.0)",
    )


class MultiRegionSettings(BaseSettings):
    """Multi-region deployment and failover configuration."""

    model_config = SettingsConfigDict(env_prefix="REGION_")

    primary_region: str = Field(default="us-east-1", description="Primary serving region")
    secondary_regions: List[str] = Field(
        default=["us-west-2", "eu-west-1"],
        description="Ordered list of failover regions",
    )
    failover_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Seconds to wait before triggering failover",
    )
    health_check_interval_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Interval between cross-region health probes",
    )


class RateLimitSettings(BaseSettings):
    """API rate limiting configuration."""

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_")

    enabled: bool = Field(default=True, description="Enable rate limiting")
    requests_per_minute: int = Field(
        default=60,
        ge=1,
        description="Maximum requests per minute per client",
    )
    burst_size: int = Field(
        default=10,
        ge=1,
        description="Token bucket burst allowance",
    )
    backend: str = Field(
        default="redis",
        description="Rate limit backend (redis|memory)",
    )


class SecuritySettings(BaseSettings):
    """Security, authentication, and encryption settings."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_")

    jwt_secret_key: str = Field(
        default="CHANGE-ME-IN-PRODUCTION",
        description="JWT signing secret (use a strong secret in production)",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm")
    jwt_expiration_minutes: int = Field(
        default=30,
        ge=1,
        description="JWT token expiration in minutes",
    )
    encryption_key: str = Field(
        default="CHANGE-ME-IN-PRODUCTION",
        description="Fernet / AES encryption key for data at rest",
    )
    mtls_enabled: bool = Field(
        default=False,
        description="Require mutual TLS for service-to-service calls",
    )
    mtls_cert_path: Optional[str] = Field(
        default=None,
        description="Path to mTLS client certificate",
    )
    mtls_key_path: Optional[str] = Field(
        default=None,
        description="Path to mTLS client private key",
    )
    mtls_ca_path: Optional[str] = Field(
        default=None,
        description="Path to mTLS CA bundle",
    )


# ---------------------------------------------------------------------------
# Root settings aggregator
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Root configuration object that aggregates all sub-settings.

    Environment variables are loaded automatically. Each sub-settings group
    uses its own prefix (``APP_``, ``GPU_``, ``TRITON_``, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    gpu: GPUSettings = Field(default_factory=GPUSettings)
    triton: TritonSettings = Field(default_factory=TritonSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    multi_region: MultiRegionSettings = Field(default_factory=MultiRegionSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @property
    def is_production(self) -> bool:
        """Return ``True`` when running in the production environment."""
        return self.app.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton ``Settings`` instance.

    The first call constructs the object (reading env vars / .env);
    subsequent calls return the same instance from the LRU cache.
    """
    return Settings()
