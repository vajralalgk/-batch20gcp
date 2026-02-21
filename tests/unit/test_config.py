"""Tests for src.core.config.settings -- Netflix LLM Platform configuration."""

import os
from unittest.mock import patch

import pytest

from src.core.config.settings import (
    AppSettings,
    GPUSettings,
    ModelSettings,
    MultiRegionSettings,
    ObservabilitySettings,
    RedisSettings,
    SecuritySettings,
    Settings,
    TritonSettings,
    get_settings,
)


class TestDefaultSettings:
    """Verify that every settings group ships with sensible defaults."""

    def test_default_settings(self):
        settings = Settings()
        assert settings.app.name == "netflix-llm-platform"
        assert settings.app.version == "1.0.0"
        assert settings.app.host == "0.0.0.0"
        assert settings.app.port == 8000
        assert settings.triton.model_name == "ensemble_llm"
        assert settings.model.max_sequence_length == 4096
        assert settings.rate_limit.enabled is True

    def test_gpu_settings(self):
        gpu = GPUSettings()
        assert isinstance(gpu.enable_gpu, bool)
        assert gpu.max_batch_size >= 1
        assert gpu.tensor_parallel_size >= 1
        assert 0.1 <= gpu.memory_fraction <= 1.0
        assert isinstance(gpu.dynamic_batching, bool)
        assert isinstance(gpu.device_ids, list)

    def test_redis_settings(self):
        redis = RedisSettings()
        assert "redis" in redis.url.lower() or "localhost" in redis.url
        assert redis.max_connections >= 1
        assert redis.session_ttl_seconds >= 0
        assert isinstance(redis.cluster_enabled, bool)

    def test_multi_region_settings(self):
        region = MultiRegionSettings()
        assert region.primary_region == "us-east-1"
        assert isinstance(region.secondary_regions, list)
        assert len(region.secondary_regions) >= 1
        assert region.failover_timeout_seconds > 0
        assert region.health_check_interval_seconds > 0

    def test_triton_settings_defaults(self):
        triton = TritonSettings()
        assert triton.server_url == "localhost:8001"
        assert triton.model_version == "1"
        assert triton.max_queue_delay_ms >= 0

    def test_observability_settings_defaults(self):
        obs = ObservabilitySettings()
        assert isinstance(obs.prometheus_enabled, bool)
        assert isinstance(obs.dcgm_enabled, bool)
        assert isinstance(obs.otel_enabled, bool)
        assert 0.0 <= obs.otel_sample_rate <= 1.0


class TestSettingsFromEnv:
    """Verify that environment variables are picked up correctly."""

    def test_settings_from_env(self):
        with patch.dict(os.environ, {"APP_NAME": "test-app", "APP_ENVIRONMENT": "staging"}):
            app = AppSettings()
            assert app.name == "test-app"
            assert app.environment == "staging"

    def test_gpu_env_override(self):
        with patch.dict(os.environ, {"GPU_ENABLE_GPU": "false", "GPU_MAX_BATCH_SIZE": "128"}):
            gpu = GPUSettings()
            assert gpu.enable_gpu is False
            assert gpu.max_batch_size == 128

    def test_security_env_override(self):
        with patch.dict(os.environ, {"SECURITY_JWT_SECRET_KEY": "super-secret"}):
            sec = SecuritySettings()
            assert sec.jwt_secret_key == "super-secret"

    def test_triton_env_override(self):
        with patch.dict(os.environ, {"TRITON_SERVER_URL": "triton:9001"}):
            triton = TritonSettings()
            assert triton.server_url == "triton:9001"


class TestSettingsValidation:
    """Ensure validators reject invalid values."""

    def test_invalid_environment(self):
        with pytest.raises(Exception):
            AppSettings(environment="invalid-env")

    def test_invalid_quantization(self):
        with pytest.raises(Exception):
            ModelSettings(quantization="bfloat16")

    def test_valid_quantization_values(self):
        for q in ("awq", "gptq", "fp8"):
            m = ModelSettings(quantization=q)
            assert m.quantization == q

    def test_none_quantization_is_valid(self):
        m = ModelSettings(quantization=None)
        assert m.quantization is None

    def test_gpu_memory_fraction_bounds(self):
        with pytest.raises(Exception):
            GPUSettings(memory_fraction=0.0)
        with pytest.raises(Exception):
            GPUSettings(memory_fraction=1.5)

    def test_valid_environments(self):
        for env in ("development", "staging", "production"):
            app = AppSettings(environment=env)
            assert app.environment == env


class TestSettingsSingleton:
    """Test the cached get_settings() factory."""

    def test_get_settings_singleton(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        get_settings.cache_clear()

    def test_is_production_property(self):
        with patch.dict(os.environ, {"APP_ENVIRONMENT": "production"}):
            s = Settings()
            assert s.is_production is True

    def test_is_not_production(self):
        with patch.dict(os.environ, {"APP_ENVIRONMENT": "development"}):
            s = Settings()
            assert s.is_production is False
