"""Netflix LLM Platform - Custom Exceptions & FastAPI Handlers.

Defines the platform exception hierarchy and registers FastAPI exception
handlers that convert exceptions into structured JSON error responses with
correlation IDs and appropriate HTTP status codes.

Usage::

    from src.core.exceptions import InferenceError, GPUMemoryError

    raise InferenceError("Beam search diverged", model_name="llama-3-70b")
"""

from __future__ import annotations

import time
import traceback
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------

class PlatformBaseError(Exception):
    """Base exception for all Netflix LLM Platform errors.

    Attributes:
        message: Human-readable error description.
        error_code: Machine-readable error code for programmatic handling.
        status_code: Suggested HTTP status code for API responses.
        details: Optional mapping of additional context.
    """

    def __init__(
        self,
        message: str = "An unexpected platform error occurred",
        *,
        error_code: str = "PLATFORM_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the error to a JSON-safe dictionary."""
        payload: Dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


# ---------------------------------------------------------------------------
# Inference errors
# ---------------------------------------------------------------------------

class InferenceError(PlatformBaseError):
    """Raised when LLM inference fails (e.g. beam search divergence, OOM)."""

    def __init__(
        self,
        message: str = "Inference failed",
        *,
        model_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if model_name:
            _details["model_name"] = model_name
        super().__init__(
            message,
            error_code="INFERENCE_ERROR",
            status_code=500,
            details=_details,
        )


class GPUMemoryError(PlatformBaseError):
    """Raised when GPU memory is exhausted or allocation fails."""

    def __init__(
        self,
        message: str = "GPU memory allocation failed",
        *,
        device_id: Optional[int] = None,
        requested_mb: Optional[float] = None,
        available_mb: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if device_id is not None:
            _details["device_id"] = device_id
        if requested_mb is not None:
            _details["requested_mb"] = requested_mb
        if available_mb is not None:
            _details["available_mb"] = available_mb
        super().__init__(
            message,
            error_code="GPU_MEMORY_ERROR",
            status_code=503,
            details=_details,
        )


class KVCacheOverflowError(PlatformBaseError):
    """Raised when the KV-cache exceeds its configured token limit."""

    def __init__(
        self,
        message: str = "KV cache overflow: token limit exceeded",
        *,
        max_tokens: Optional[int] = None,
        current_tokens: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if max_tokens is not None:
            _details["max_tokens"] = max_tokens
        if current_tokens is not None:
            _details["current_tokens"] = current_tokens
        super().__init__(
            message,
            error_code="KV_CACHE_OVERFLOW",
            status_code=503,
            details=_details,
        )


class ModelNotFoundError(PlatformBaseError):
    """Raised when a requested model is not available in the registry."""

    def __init__(
        self,
        message: str = "Model not found",
        *,
        model_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if model_name:
            _details["model_name"] = model_name
        super().__init__(
            message,
            error_code="MODEL_NOT_FOUND",
            status_code=404,
            details=_details,
        )


# ---------------------------------------------------------------------------
# Infrastructure errors
# ---------------------------------------------------------------------------

class RegionFailoverError(PlatformBaseError):
    """Raised when cross-region failover is triggered or fails."""

    def __init__(
        self,
        message: str = "Region failover triggered",
        *,
        failed_region: Optional[str] = None,
        target_region: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if failed_region:
            _details["failed_region"] = failed_region
        if target_region:
            _details["target_region"] = target_region
        super().__init__(
            message,
            error_code="REGION_FAILOVER_ERROR",
            status_code=503,
            details=_details,
        )


class RateLimitExceededError(PlatformBaseError):
    """Raised when a client exceeds the configured rate limit."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after_seconds: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if retry_after_seconds is not None:
            _details["retry_after_seconds"] = retry_after_seconds
        super().__init__(
            message,
            error_code="RATE_LIMIT_EXCEEDED",
            status_code=429,
            details=_details,
        )
        self.retry_after_seconds = retry_after_seconds


class CircuitBreakerOpenError(PlatformBaseError):
    """Raised when a circuit breaker is in the open state."""

    def __init__(
        self,
        message: str = "Circuit breaker is open",
        *,
        service_name: Optional[str] = None,
        reset_after_seconds: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        _details = details or {}
        if service_name:
            _details["service_name"] = service_name
        if reset_after_seconds is not None:
            _details["reset_after_seconds"] = reset_after_seconds
        super().__init__(
            message,
            error_code="CIRCUIT_BREAKER_OPEN",
            status_code=503,
            details=_details,
        )


# ---------------------------------------------------------------------------
# FastAPI exception handlers
# ---------------------------------------------------------------------------

def _build_error_response(
    request: Request,
    status_code: int,
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Build a standardised JSON error response with a correlation ID."""
    correlation_id = request.headers.get(
        "x-correlation-id", str(uuid.uuid4())
    )

    body: Dict[str, Any] = {
        "error": {
            "code": error_code,
            "message": message,
            "correlation_id": correlation_id,
            "timestamp": time.time(),
        }
    }
    if details:
        body["error"]["details"] = details

    headers: Dict[str, str] = {"x-correlation-id": correlation_id}

    # Attach Retry-After header for rate-limit responses.
    if status_code == 429 and details and "retry_after_seconds" in details:
        headers["Retry-After"] = str(details["retry_after_seconds"])

    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=headers,
    )


async def _platform_error_handler(
    request: Request, exc: PlatformBaseError
) -> JSONResponse:
    """Handle all ``PlatformBaseError`` sub-classes."""
    return _build_error_response(
        request,
        status_code=exc.status_code,
        error_code=exc.error_code,
        message=exc.message,
        details=exc.details,
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for unexpected exceptions -- avoids leaking stack traces."""
    return _build_error_response(
        request,
        status_code=500,
        error_code="INTERNAL_SERVER_ERROR",
        message="An unexpected internal error occurred",
        details={"exception_type": type(exc).__name__},
    )


async def _validation_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle Pydantic / FastAPI request validation errors."""
    from fastapi.exceptions import RequestValidationError

    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return _build_error_response(
        request,
        status_code=422,
        error_code="VALIDATION_ERROR",
        message="Request validation failed",
        details={"validation_errors": errors},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all custom exception handlers on a FastAPI application.

    Call this during application startup::

        app = FastAPI()
        register_exception_handlers(app)

    Args:
        app: The FastAPI application instance.
    """
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(PlatformBaseError, _platform_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
