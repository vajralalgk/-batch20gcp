"""Netflix LLM Platform - Shared Utility Functions.

Provides reusable helpers for timing, retry logic, token accounting,
GPU memory formatting, and cost-efficiency calculations used across
the inference platform.

Usage::

    from src.core.utils import timed, retry_with_backoff, count_tokens

    @timed
    async def predict(payload):
        ...

    result = await retry_with_backoff(flaky_call, max_retries=3)
"""

from __future__ import annotations

import asyncio
import functools
import math
import time
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Timing decorator
# ---------------------------------------------------------------------------

def timed(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that logs wall-clock execution time of sync and async functions.

    Emits a structured log event with ``duration_ms`` after each call.

    Usage::

        @timed
        def compute():
            ...

        @timed
        async def fetch():
            ...
    """
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1_000
                logger.info(
                    "function_executed",
                    function=func.__qualname__,
                    duration_ms=round(elapsed_ms, 3),
                )
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1_000
                logger.info(
                    "function_executed",
                    function=func.__qualname__,
                    duration_ms=round(elapsed_ms, 3),
                )
        return sync_wrapper


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------

async def retry_with_backoff(
    func: Callable[..., Union[T, Awaitable[T]]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Sequence[Type[BaseException]] = (Exception,),
    **kwargs: Any,
) -> T:
    """Execute *func* with exponential backoff on transient failures.

    Args:
        func: The callable (sync or async) to invoke.
        *args: Positional arguments forwarded to *func*.
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on delay between retries.
        backoff_factor: Multiplier applied to the delay after each retry.
        retryable_exceptions: Exception types that trigger a retry.
        **kwargs: Keyword arguments forwarded to *func*.

    Returns:
        The return value of *func* on the first successful invocation.

    Raises:
        The last exception raised by *func* after all retries are exhausted.
    """
    last_exception: BaseException = RuntimeError("retry_with_backoff: no attempts made")
    delay = base_delay

    for attempt in range(1, max_retries + 2):  # attempt 1 is the initial call
        try:
            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result  # type: ignore[return-value]
            return result  # type: ignore[return-value]
        except tuple(retryable_exceptions) as exc:
            last_exception = exc
            if attempt > max_retries:
                logger.error(
                    "retry_exhausted",
                    function=getattr(func, "__qualname__", str(func)),
                    attempts=attempt,
                    last_error=str(exc),
                )
                raise

            logger.warning(
                "retry_attempt",
                function=getattr(func, "__qualname__", str(func)),
                attempt=attempt,
                delay_seconds=round(delay, 3),
                error=str(exc),
            )
            await asyncio.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)

    raise last_exception  # Unreachable in practice, satisfies type checker.


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(
    text: str,
    *,
    method: str = "tiktoken",
    model: str = "cl100k_base",
) -> int:
    """Count the number of tokens in *text*.

    Args:
        text: Input string to tokenise.
        method: Tokenisation back-end. One of ``"tiktoken"`` (preferred,
            uses OpenAI's tiktoken library) or ``"approximate"`` (fast
            heuristic: 1 token ~ 4 characters).
        model: tiktoken encoding name (default ``cl100k_base``).

    Returns:
        Estimated token count.
    """
    if not text:
        return 0

    if method == "tiktoken":
        try:
            import tiktoken

            encoding = tiktoken.get_encoding(model)
            return len(encoding.encode(text))
        except ImportError:
            logger.warning(
                "tiktoken_unavailable",
                fallback="approximate",
            )
            method = "approximate"

    if method == "approximate":
        return max(1, len(text) // 4)

    raise ValueError(f"Unknown tokenisation method: '{method}'")


# ---------------------------------------------------------------------------
# Batch size calculator
# ---------------------------------------------------------------------------

def calculate_optimal_batch_size(
    available_memory_mb: float,
    model_memory_mb: float,
    per_sample_memory_mb: float,
    *,
    max_batch_size: int = 64,
    memory_safety_margin: float = 0.9,
) -> int:
    """Determine the largest batch size that fits in available GPU memory.

    Args:
        available_memory_mb: Free GPU memory in MiB.
        model_memory_mb: Static memory consumed by the model weights.
        per_sample_memory_mb: Incremental memory per batch sample
            (activations, KV cache, etc.).
        max_batch_size: Hard upper limit on batch size.
        memory_safety_margin: Fraction of free memory to actually use
            (0.0 - 1.0) to prevent OOM spikes.

    Returns:
        Optimal batch size (at least 1, at most *max_batch_size*).
    """
    usable_memory = (available_memory_mb - model_memory_mb) * memory_safety_margin

    if usable_memory <= 0 or per_sample_memory_mb <= 0:
        return 1

    calculated = int(math.floor(usable_memory / per_sample_memory_mb))
    return max(1, min(calculated, max_batch_size))


# ---------------------------------------------------------------------------
# GPU memory formatter
# ---------------------------------------------------------------------------

def format_gpu_memory(
    used_bytes: int,
    total_bytes: int,
) -> str:
    """Format GPU memory usage into a human-readable string.

    Args:
        used_bytes: Currently consumed GPU memory in bytes.
        total_bytes: Total GPU memory in bytes.

    Returns:
        Formatted string, e.g. ``"12.4 GiB / 80.0 GiB (15.5%)"``
    """
    gib = 1024 ** 3
    used_gib = used_bytes / gib
    total_gib = total_bytes / gib
    pct = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0

    return f"{used_gib:.1f} GiB / {total_gib:.1f} GiB ({pct:.1f}%)"


# ---------------------------------------------------------------------------
# Cost efficiency calculator
# ---------------------------------------------------------------------------

def calculate_throughput_per_dollar(
    tokens_per_second: float,
    cost_per_hour: float,
) -> float:
    """Calculate inference cost efficiency as tokens per dollar.

    This metric normalises throughput by infrastructure cost, enabling
    apples-to-apples comparison of different GPU SKUs or configurations.

    Args:
        tokens_per_second: Sustained token generation throughput.
        cost_per_hour: Hourly infrastructure cost in USD (GPU instance,
            networking, storage, etc.).

    Returns:
        Tokens generated per dollar of infrastructure spend.

    Raises:
        ValueError: If *cost_per_hour* is zero or negative.
    """
    if cost_per_hour <= 0:
        raise ValueError(
            f"cost_per_hour must be positive, got {cost_per_hour}"
        )

    tokens_per_hour = tokens_per_second * 3_600
    return tokens_per_hour / cost_per_hour
