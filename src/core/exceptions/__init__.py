"""Netflix LLM Platform - Custom Exceptions Module.

Re-exports all custom exceptions for convenient imports::

    from src.core.exceptions import InferenceError, GPUMemoryError
"""

from src.core.exceptions.handlers import (
    PlatformBaseError,
    InferenceError,
    GPUMemoryError,
    KVCacheOverflowError,
    ModelNotFoundError,
    RegionFailoverError,
    RateLimitExceededError,
    CircuitBreakerOpenError,
    register_exception_handlers,
)

__all__ = [
    "PlatformBaseError",
    "InferenceError",
    "GPUMemoryError",
    "KVCacheOverflowError",
    "ModelNotFoundError",
    "RegionFailoverError",
    "RateLimitExceededError",
    "CircuitBreakerOpenError",
    "register_exception_handlers",
]
