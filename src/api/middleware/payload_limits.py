"""Netflix LLM Personalization Platform - Request Size & Payload Validation Middleware.

Enforces strict request-size envelopes, token budgets, and per-endpoint
payload constraints so that oversized or malformed requests are rejected
at the edge before they can consume GPU resources.

GPU Memory Protection
---------------------
These limits prevent GPU out-of-memory (OOM) errors by capping the input
sizes that directly affect KV-cache allocation and activation memory.
For example, a single unbounded prompt can inflate the KV cache linearly
with sequence length, potentially evicting other in-flight requests from
device memory.  The constraints defined here act as the first line of
defence against such scenarios.

Author: Gopi Krishna Vajrala
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Payload constraints
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PayloadConstraints:
    """Hard ceilings on request dimensions.

    Every value here is chosen to stay within the safe operating envelope of
    the current GPU fleet (A100-80 GB / H100-80 GB).  Changing these values
    without a corresponding capacity review is strongly discouraged.
    """

    MAX_PROMPT_TOKENS: int = 4096
    MAX_BATCH_SIZE: int = 128
    MAX_REQUEST_SIZE_BYTES: int = 2 * 1024 * 1024          # 2 MB
    MAX_STREAMING_WINDOW_BYTES: int = 10 * 1024 * 1024     # 10 MB
    MAX_CONTENT_IDS: int = 500
    MAX_CONTEXT_FIELDS: int = 20
    MAX_EMBEDDING_DIMENSIONS: int = 768


CONSTRAINTS = PayloadConstraints()


# ---------------------------------------------------------------------------
# Endpoint-specific limits
# ---------------------------------------------------------------------------

ENDPOINT_LIMITS: Dict[str, Dict[str, int]] = {
    "/api/v1/inference/predict": {
        "max_tokens": 4096,
        "max_content_ids": 100,
    },
    "/api/v1/inference/batch": {
        "max_tokens": 4096,
        "max_batch_size": 128,
        "max_content_ids": 500,
    },
    "/api/v1/personalization/recommend": {
        "max_results": 100,
        "max_content_ids": 200,
    },
}


# ---------------------------------------------------------------------------
# Validation error helpers
# ---------------------------------------------------------------------------

class PayloadErrorCode(str, Enum):
    """Machine-readable error codes returned to callers."""

    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    PROMPT_TOO_LONG = "PROMPT_TOO_LONG"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    TOO_MANY_CONTENT_IDS = "TOO_MANY_CONTENT_IDS"
    TOO_MANY_CONTEXT_FIELDS = "TOO_MANY_CONTEXT_FIELDS"
    EMBEDDING_DIMS_EXCEEDED = "EMBEDDING_DIMS_EXCEEDED"
    ENDPOINT_LIMIT_EXCEEDED = "ENDPOINT_LIMIT_EXCEEDED"


@dataclass
class PayloadValidationError:
    """Structured validation error returned in HTTP responses."""

    code: PayloadErrorCode
    message: str
    limit: Optional[int] = None
    actual: Optional[int] = None
    endpoint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "error": {
                "code": self.code.value,
                "message": self.message,
            },
        }
        if self.limit is not None:
            result["error"]["limit"] = self.limit
        if self.actual is not None:
            result["error"]["actual"] = self.actual
        if self.endpoint is not None:
            result["error"]["endpoint"] = self.endpoint
        return result


# ---------------------------------------------------------------------------
# Request validation middleware
# ---------------------------------------------------------------------------

@dataclass
class PayloadLimitsMiddleware:
    """FastAPI-compatible middleware that enforces payload constraints.

    Usage with FastAPI::

        from starlette.types import ASGIApp
        app.add_middleware(PayloadLimitsMiddleware)

    The middleware performs the following checks in order:

    1. ``Content-Length`` header vs ``MAX_REQUEST_SIZE_BYTES``
       -> 413 Payload Too Large
    2. Body JSON parse
       -> 400 Bad Request for malformed payloads
    3. Endpoint-specific field validation (token count, batch size, etc.)
       -> 400 Bad Request with structured error detail
    """

    app: Any = None  # ASGI app reference
    constraints: PayloadConstraints = field(default_factory=lambda: CONSTRAINTS)

    # -- ASGI interface -----------------------------------------------------

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        """ASGI entrypoint."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = self._get_content_length(headers)

        # 1. Check Content-Length against max request size
        if content_length is not None and content_length > self.constraints.MAX_REQUEST_SIZE_BYTES:
            error = PayloadValidationError(
                code=PayloadErrorCode.REQUEST_TOO_LARGE,
                message=(
                    f"Request body of {content_length} bytes exceeds the "
                    f"maximum allowed size of {self.constraints.MAX_REQUEST_SIZE_BYTES} bytes."
                ),
                limit=self.constraints.MAX_REQUEST_SIZE_BYTES,
                actual=content_length,
            )
            await self._send_error_response(send, status=413, error=error)
            return

        # Collect body chunks
        body_chunks: List[bytes] = []
        total_size = 0

        async def _receive_wrapper() -> Dict[str, Any]:
            nonlocal total_size
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                body_chunks.append(chunk)
                total_size += len(chunk)
                if total_size > self.constraints.MAX_REQUEST_SIZE_BYTES:
                    raise _PayloadTooLargeSignal(total_size)
            return message

        try:
            # Wrap send to inject X-Max-Request-Size header
            async def _send_wrapper(message: Dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    headers_list: List = list(message.get("headers", []))
                    headers_list.append(
                        (
                            b"x-max-request-size",
                            str(self.constraints.MAX_REQUEST_SIZE_BYTES).encode(),
                        )
                    )
                    message["headers"] = headers_list
                await send(message)

            await self.app(scope, _receive_wrapper, _send_wrapper)

        except _PayloadTooLargeSignal as exc:
            error = PayloadValidationError(
                code=PayloadErrorCode.REQUEST_TOO_LARGE,
                message=(
                    f"Streamed request body ({exc.actual_size} bytes so far) "
                    f"exceeds maximum of {self.constraints.MAX_REQUEST_SIZE_BYTES} bytes."
                ),
                limit=self.constraints.MAX_REQUEST_SIZE_BYTES,
                actual=exc.actual_size,
            )
            await self._send_error_response(send, status=413, error=error)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _get_content_length(headers: Dict[bytes, bytes]) -> Optional[int]:
        raw = headers.get(b"content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    async def _send_error_response(
        send: Callable,
        *,
        status: int,
        error: PayloadValidationError,
    ) -> None:
        body = json.dumps(error.to_dict()).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (
                        b"x-max-request-size",
                        str(CONSTRAINTS.MAX_REQUEST_SIZE_BYTES).encode(),
                    ),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _PayloadTooLargeSignal(Exception):
    """Internal signal raised when streamed body exceeds the size limit."""

    def __init__(self, actual_size: int) -> None:
        self.actual_size = actual_size
        super().__init__(f"Payload exceeded limit: {actual_size} bytes")


# ---------------------------------------------------------------------------
# Body-level validators (called from route handlers or dependencies)
# ---------------------------------------------------------------------------

def validate_payload(body: Dict[str, Any], endpoint: str) -> Optional[PayloadValidationError]:
    """Validate a parsed JSON body against global and endpoint-specific limits.

    Returns ``None`` when the payload is valid, or a
    :class:`PayloadValidationError` describing the first violation found.
    """

    # --- Global constraints ------------------------------------------------

    prompt_tokens: Optional[int] = body.get("prompt_tokens") or body.get("max_tokens")
    if prompt_tokens is not None and prompt_tokens > CONSTRAINTS.MAX_PROMPT_TOKENS:
        return PayloadValidationError(
            code=PayloadErrorCode.PROMPT_TOO_LONG,
            message=f"Prompt token count {prompt_tokens} exceeds maximum of {CONSTRAINTS.MAX_PROMPT_TOKENS}.",
            limit=CONSTRAINTS.MAX_PROMPT_TOKENS,
            actual=prompt_tokens,
            endpoint=endpoint,
        )

    batch_items: Optional[List] = body.get("batch") or body.get("items")
    if batch_items is not None and len(batch_items) > CONSTRAINTS.MAX_BATCH_SIZE:
        return PayloadValidationError(
            code=PayloadErrorCode.BATCH_TOO_LARGE,
            message=f"Batch size {len(batch_items)} exceeds maximum of {CONSTRAINTS.MAX_BATCH_SIZE}.",
            limit=CONSTRAINTS.MAX_BATCH_SIZE,
            actual=len(batch_items),
            endpoint=endpoint,
        )

    content_ids: Optional[List] = body.get("content_ids")
    if content_ids is not None and len(content_ids) > CONSTRAINTS.MAX_CONTENT_IDS:
        return PayloadValidationError(
            code=PayloadErrorCode.TOO_MANY_CONTENT_IDS,
            message=f"Content ID count {len(content_ids)} exceeds maximum of {CONSTRAINTS.MAX_CONTENT_IDS}.",
            limit=CONSTRAINTS.MAX_CONTENT_IDS,
            actual=len(content_ids),
            endpoint=endpoint,
        )

    context: Optional[Dict] = body.get("context")
    if context is not None and len(context) > CONSTRAINTS.MAX_CONTEXT_FIELDS:
        return PayloadValidationError(
            code=PayloadErrorCode.TOO_MANY_CONTEXT_FIELDS,
            message=f"Context field count {len(context)} exceeds maximum of {CONSTRAINTS.MAX_CONTEXT_FIELDS}.",
            limit=CONSTRAINTS.MAX_CONTEXT_FIELDS,
            actual=len(context),
            endpoint=endpoint,
        )

    embedding_dim: Optional[int] = body.get("embedding_dimensions")
    if embedding_dim is not None and embedding_dim > CONSTRAINTS.MAX_EMBEDDING_DIMENSIONS:
        return PayloadValidationError(
            code=PayloadErrorCode.EMBEDDING_DIMS_EXCEEDED,
            message=f"Embedding dimensions {embedding_dim} exceed maximum of {CONSTRAINTS.MAX_EMBEDDING_DIMENSIONS}.",
            limit=CONSTRAINTS.MAX_EMBEDDING_DIMENSIONS,
            actual=embedding_dim,
            endpoint=endpoint,
        )

    # --- Per-endpoint constraints ------------------------------------------

    limits = ENDPOINT_LIMITS.get(endpoint)
    if limits is not None:
        ep_max_tokens = limits.get("max_tokens")
        if ep_max_tokens is not None and prompt_tokens is not None and prompt_tokens > ep_max_tokens:
            return PayloadValidationError(
                code=PayloadErrorCode.ENDPOINT_LIMIT_EXCEEDED,
                message=f"Token count {prompt_tokens} exceeds endpoint limit of {ep_max_tokens} for {endpoint}.",
                limit=ep_max_tokens,
                actual=prompt_tokens,
                endpoint=endpoint,
            )

        ep_max_batch = limits.get("max_batch_size")
        if ep_max_batch is not None and batch_items is not None and len(batch_items) > ep_max_batch:
            return PayloadValidationError(
                code=PayloadErrorCode.ENDPOINT_LIMIT_EXCEEDED,
                message=f"Batch size {len(batch_items)} exceeds endpoint limit of {ep_max_batch} for {endpoint}.",
                limit=ep_max_batch,
                actual=len(batch_items),
                endpoint=endpoint,
            )

        ep_max_content = limits.get("max_content_ids")
        if ep_max_content is not None and content_ids is not None and len(content_ids) > ep_max_content:
            return PayloadValidationError(
                code=PayloadErrorCode.ENDPOINT_LIMIT_EXCEEDED,
                message=f"Content ID count {len(content_ids)} exceeds endpoint limit of {ep_max_content} for {endpoint}.",
                limit=ep_max_content,
                actual=len(content_ids),
                endpoint=endpoint,
            )

        ep_max_results = limits.get("max_results")
        max_results_val: Optional[int] = body.get("max_results")
        if ep_max_results is not None and max_results_val is not None and max_results_val > ep_max_results:
            return PayloadValidationError(
                code=PayloadErrorCode.ENDPOINT_LIMIT_EXCEEDED,
                message=f"max_results {max_results_val} exceeds endpoint limit of {ep_max_results} for {endpoint}.",
                limit=ep_max_results,
                actual=max_results_val,
                endpoint=endpoint,
            )

    return None
