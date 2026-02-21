"""Tests for src.core.utils.helpers -- utility functions."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.core.utils.helpers import (
    calculate_optimal_batch_size,
    calculate_throughput_per_dollar,
    count_tokens,
    format_gpu_memory,
    retry_with_backoff,
    timed,
)


# ---------------------------------------------------------------------------
# Timing decorator
# ---------------------------------------------------------------------------

class TestTimingDecorator:
    """Test the @timed decorator."""

    def test_timing_decorator_sync(self):
        @timed
        def slow_func():
            time.sleep(0.02)
            return 42

        result = slow_func()
        assert result == 42

    @pytest.mark.asyncio
    async def test_timing_decorator_async(self):
        @timed
        async def slow_async():
            await asyncio.sleep(0.02)
            return "done"

        result = await slow_async()
        assert result == "done"


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------

class TestRetryWithBackoff:
    """Test the retry_with_backoff coroutine."""

    @pytest.mark.asyncio
    async def test_retry_with_backoff(self):
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient failure")
            return "success"

        result = await retry_with_backoff(
            flaky,
            max_retries=4,
            base_delay=0.01,
        )
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        def always_fail():
            raise ValueError("Permanent failure")

        with pytest.raises(ValueError, match="Permanent"):
            await retry_with_backoff(
                always_fail,
                max_retries=2,
                base_delay=0.01,
            )

    @pytest.mark.asyncio
    async def test_retry_async_function(self):
        call_count = 0

        async def flaky_async():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise IOError("Transient")
            return "async_ok"

        result = await retry_with_backoff(
            flaky_async,
            max_retries=3,
            base_delay=0.01,
        )
        assert result == "async_ok"


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

class TestTokenCounting:
    """Test the count_tokens function."""

    def test_token_counting(self):
        text = "The quick brown fox jumps over the lazy dog"
        count = count_tokens(text, method="approximate")
        # Approximate: len(text) // 4
        assert count >= 1
        assert count == len(text) // 4

    def test_token_counting_empty(self):
        assert count_tokens("") == 0

    def test_token_counting_invalid_method(self):
        with pytest.raises(ValueError, match="Unknown"):
            count_tokens("hello", method="nonexistent")


# ---------------------------------------------------------------------------
# Batch size calculator
# ---------------------------------------------------------------------------

class TestBatchSizeCalculator:
    """Test the calculate_optimal_batch_size function."""

    def test_batch_size_calculator(self):
        batch = calculate_optimal_batch_size(
            available_memory_mb=80_000,
            model_memory_mb=26_000,
            per_sample_memory_mb=500,
            max_batch_size=64,
        )
        # (80000 - 26000) * 0.9 / 500 = 97.2 => clamped to 64
        assert batch == 64

    def test_batch_size_calculator_small_gpu(self):
        batch = calculate_optimal_batch_size(
            available_memory_mb=16_000,
            model_memory_mb=14_000,
            per_sample_memory_mb=500,
            max_batch_size=64,
        )
        # (16000 - 14000) * 0.9 / 500 = 3.6 => 3
        assert batch == 3

    def test_batch_size_zero_memory(self):
        batch = calculate_optimal_batch_size(
            available_memory_mb=1000,
            model_memory_mb=2000,  # More than available!
            per_sample_memory_mb=100,
        )
        assert batch == 1

    def test_batch_size_respects_max(self):
        batch = calculate_optimal_batch_size(
            available_memory_mb=1_000_000,
            model_memory_mb=0,
            per_sample_memory_mb=1,
            max_batch_size=32,
        )
        assert batch == 32


# ---------------------------------------------------------------------------
# GPU memory formatting
# ---------------------------------------------------------------------------

class TestFormatGpuMemory:
    """Test the format_gpu_memory function."""

    def test_format_gpu_memory(self):
        total = 80 * (1024 ** 3)  # 80 GiB
        used = int(54.4 * (1024 ** 3))
        formatted = format_gpu_memory(used, total)
        assert "GiB" in formatted
        assert "54.4" in formatted
        assert "80.0" in formatted
        assert "%" in formatted

    def test_format_zero_total(self):
        formatted = format_gpu_memory(0, 0)
        assert "0.0" in formatted


# ---------------------------------------------------------------------------
# Throughput per dollar
# ---------------------------------------------------------------------------

class TestThroughputPerDollar:
    """Test the calculate_throughput_per_dollar function."""

    def test_throughput_per_dollar(self):
        tpd = calculate_throughput_per_dollar(
            tokens_per_second=20_000,
            cost_per_hour=32.77,
        )
        # 20000 * 3600 / 32.77 ~ 2.2M tokens per dollar
        assert tpd > 1_000_000

    def test_throughput_per_dollar_zero_cost_raises(self):
        with pytest.raises(ValueError, match="positive"):
            calculate_throughput_per_dollar(
                tokens_per_second=20_000,
                cost_per_hour=0.0,
            )

    def test_throughput_per_dollar_negative_cost_raises(self):
        with pytest.raises(ValueError, match="positive"):
            calculate_throughput_per_dollar(
                tokens_per_second=20_000,
                cost_per_hour=-1.0,
            )
