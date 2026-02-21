"""
Idempotency Middleware for the Netflix LLM Platform.

Author: Gopi Krishna Vajrala

This module implements a production-grade idempotency layer for the FastAPI-based
Netflix LLM Platform inference gateway. It guarantees exactly-once semantics for
mutating API operations by fingerprinting inbound requests, suppressing duplicates
that arrive while a prior identical request is still in-flight, and serving cached
responses for previously completed requests that share the same idempotency key.

Why idempotency matters for GPU workloads
-----------------------------------------
Without idempotency enforcement, automatic client retries (e.g. from load-balancer
timeouts or intermittent network partitions) can **double-trigger GPU execution**.
This is especially critical for the ``/api/v1/inference/batch`` endpoint, which
allocates GPU memory proportional to the batch size on every invocation. A single
spurious retry of a 512-sample batch can exhaust an A100's 80 GB HBM budget and
cascade-fail co-located inference workers. The idempotency layer acts as the
last line of defense before GPU resources are committed.

Design decisions
----------------
* **In-memory store** -- The current implementation uses a dict guarded by an
  ``asyncio.Lock`` to simulate the semantics of a Redis-backed store. In a
  multi-process deployment this should be swapped for a real Redis or DynamoDB
  backend; the ``InMemoryIdempotencyStore`` interface makes this substitution
  trivial.
* **24-hour TTL** -- Idempotency records expire after 24 hours, which aligns
  with the upstream SLA for client-side retry windows.
* **Request fingerprinting** -- A SHA-256 digest of (method, path, body,
  idempotency key) uniquely identifies each logical request, preventing
  collisions even when clients reuse idempotency keys across different
  endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

logger = logging.getLogger("netflix.llm_platform.middleware.idempotency")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IDEMPOTENCY_TTL_SECONDS: int = 86_400  # 24 hours

MUTATING_METHODS: frozenset = frozenset({"POST", "PUT", "PATCH"})

# ---------------------------------------------------------------------------
# Endpoint-Specific Idempotency Registry
# ---------------------------------------------------------------------------
# Each entry describes the safety properties of a registered endpoint.
# * idempotent   -- whether the operation can be safely retried without
#                   side-effects beyond the first successful execution.
# * safe_retry   -- whether an automatic retry is safe (implies idempotent).
# * cacheable    -- whether successful responses should be cached.
# * max_retries  -- upper bound on the number of retries the platform will
#                   attempt internally before surfacing an error to the caller.
# * dedup_key    -- optional JSON field name whose value is used as part of
#                   the deduplication fingerprint (useful for batch payloads).

IDEMPOTENCY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "/api/v1/inference/predict": {
        "idempotent": True,
        "safe_retry": True,
        "cacheable": True,
        "max_retries": 3,
    },
    "/api/v1/inference/batch": {
        "idempotent": True,
        "safe_retry": True,
        "cacheable": True,
        "max_retries": 2,
        "dedup_key": "request_id",
    },
    "/api/v1/personalization/recommend": {
        "idempotent": True,
        "safe_retry": True,
        "cacheable": True,
        "max_retries": 3,
    },
    "/api/v1/personalization/session": {
        "idempotent": False,
        "safe_retry": False,
        "cacheable": False,
        "max_retries": 1,
    },
    "/api/v1/personalization/embeddings": {
        "idempotent": True,
        "safe_retry": True,
        "cacheable": True,
        "max_retries": 3,
    },
    "/api/v1/gpu/defragment": {
        "idempotent": False,
        "safe_retry": False,
        "cacheable": False,
        "max_retries": 1,
    },
}

# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------


class ProcessingState(str, Enum):
    """Lifecycle state of an idempotent request."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RequestFingerprint:
    """Immutable digest that uniquely identifies a logical request.

    The fingerprint is derived from the HTTP method, path, request body, and
    the client-supplied idempotency key.  Using SHA-256 keeps the storage
    footprint constant regardless of payload size.

    Attributes:
        method: HTTP method (e.g. POST).
        path: URL path of the request.
        body_hash: SHA-256 hex digest of the raw request body.
        idempotency_key: Client-supplied UUID idempotency key.
        digest: Computed SHA-256 hex digest combining all fields.
    """

    method: str
    path: str
    body_hash: str
    idempotency_key: str
    digest: str = field(init=False, repr=True)

    def __post_init__(self) -> None:
        raw = f"{self.method}:{self.path}:{self.body_hash}:{self.idempotency_key}"
        object.__setattr__(
            self, "digest", hashlib.sha256(raw.encode("utf-8")).hexdigest()
        )


@dataclass
class IdempotencyConfig:
    """Per-endpoint idempotency configuration.

    Populated from entries in ``IDEMPOTENCY_REGISTRY`` and attached to the
    request context so downstream handlers can inspect retry / caching policy.

    Attributes:
        idempotent:  Whether the endpoint is logically idempotent.
        safe_retry:  Whether automatic retries are safe.
        cacheable:   Whether successful responses should be cached.
        max_retries: Maximum number of internal retries.
        dedup_key:   Optional body-level field used for deduplication.
    """

    idempotent: bool = True
    safe_retry: bool = True
    cacheable: bool = True
    max_retries: int = 3
    dedup_key: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdempotencyConfig":
        """Construct an ``IdempotencyConfig`` from a registry dictionary entry."""
        return cls(
            idempotent=data.get("idempotent", True),
            safe_retry=data.get("safe_retry", True),
            cacheable=data.get("cacheable", True),
            max_retries=data.get("max_retries", 3),
            dedup_key=data.get("dedup_key"),
        )


@dataclass
class IdempotencyRecord:
    """Represents a single idempotency record stored in the cache.

    Tracks the full lifecycle of a request from *PENDING* through *COMPLETED*
    or *FAILED*, together with the cached response payload when applicable.

    Attributes:
        fingerprint:      The SHA-256 fingerprint of the original request.
        idempotency_key:  The raw UUID supplied by the client.
        state:            Current processing state.
        status_code:      HTTP status code of the cached response (if completed).
        response_body:    Serialised response body (if completed and cacheable).
        response_headers: Subset of response headers to replay.
        created_at:       UNIX timestamp when the record was first created.
        updated_at:       UNIX timestamp of the last state transition.
    """

    fingerprint: str
    idempotency_key: str
    state: ProcessingState = ProcessingState.PENDING
    status_code: Optional[int] = None
    response_body: Optional[bytes] = None
    response_headers: Optional[Dict[str, str]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """Return ``True`` if the record has exceeded the TTL window."""
        return (time.time() - self.created_at) > IDEMPOTENCY_TTL_SECONDS

    def mark_completed(
        self,
        status_code: int,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Transition to COMPLETED and persist the response."""
        self.state = ProcessingState.COMPLETED
        self.status_code = status_code
        self.response_body = body
        self.response_headers = headers or {}
        self.updated_at = time.time()

    def mark_failed(self) -> None:
        """Transition to FAILED so that subsequent retries are permitted."""
        self.state = ProcessingState.FAILED
        self.updated_at = time.time()


# ---------------------------------------------------------------------------
# In-Memory Idempotency Store (simulates Redis)
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    """Async-safe, TTL-aware in-memory store for idempotency records.

    This implementation backs the middleware with a plain ``dict`` guarded by
    an ``asyncio.Lock``.  In production this class should be replaced with a
    Redis or DynamoDB implementation that shares state across all gateway
    replicas behind the load balancer.
    """

    def __init__(self) -> None:
        self._store: Dict[str, IdempotencyRecord] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, fingerprint: str) -> Optional[IdempotencyRecord]:
        """Retrieve a record by fingerprint, returning ``None`` if expired or absent."""
        async with self._lock:
            record = self._store.get(fingerprint)
            if record is None:
                return None
            if record.is_expired:
                del self._store[fingerprint]
                logger.debug(
                    "Evicted expired idempotency record key=%s",
                    record.idempotency_key,
                )
                return None
            return record

    async def put(self, record: IdempotencyRecord) -> None:
        """Insert or overwrite a record keyed by its fingerprint digest."""
        async with self._lock:
            self._store[record.fingerprint] = record

    async def delete(self, fingerprint: str) -> None:
        """Remove a record from the store if it exists."""
        async with self._lock:
            self._store.pop(fingerprint, None)

    async def evict_expired(self) -> int:
        """Sweep the store and remove all expired records.

        Returns:
            The number of records evicted.
        """
        async with self._lock:
            now = time.time()
            expired_keys = [
                k
                for k, v in self._store.items()
                if (now - v.created_at) > IDEMPOTENCY_TTL_SECONDS
            ]
            for k in expired_keys:
                del self._store[k]
            if expired_keys:
                logger.info(
                    "Evicted %d expired idempotency records", len(expired_keys)
                )
            return len(expired_keys)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_uuid(value: str) -> bool:
    """Return ``True`` if *value* is a valid UUID (any version)."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _get_endpoint_config(path: str) -> Optional[IdempotencyConfig]:
    """Look up the idempotency configuration for the given request path.

    Returns ``None`` when the endpoint is not registered in the idempotency
    registry, which means default pass-through behaviour applies.
    """
    raw = IDEMPOTENCY_REGISTRY.get(path)
    if raw is None:
        return None
    return IdempotencyConfig.from_dict(raw)


async def _compute_fingerprint(
    request: Request, idempotency_key: str
) -> RequestFingerprint:
    """Build a ``RequestFingerprint`` from the incoming request.

    The request body is consumed and cached on the request state so that
    downstream handlers can still access it.
    """
    body = await request.body()
    body_hash = hashlib.sha256(body).hexdigest()
    return RequestFingerprint(
        method=request.method,
        path=request.url.path,
        body_hash=body_hash,
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Module-level store instance shared by the middleware.  Replace with a
# Redis-backed implementation for multi-replica deployments.
_idempotency_store = InMemoryIdempotencyStore()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces exactly-once request semantics.

    Behaviour summary
    -----------------
    1. **Extract** the ``Idempotency-Key`` header from every request.

       * For mutating methods (POST / PUT / PATCH) the header is **required**.
       * For safe methods (GET, DELETE, OPTIONS, HEAD) the header is optional
         and the request passes through without idempotency checks.

    2. **Fingerprint** the request using SHA-256(method + path + body + key).

    3. **Look up** the fingerprint in the idempotency store:

       * *Miss* -- create a PENDING record and forward the request.
       * *Hit, PENDING* -- return ``409 Conflict`` (duplicate suppression).
       * *Hit, COMPLETED* -- replay the cached response.
       * *Hit, FAILED* -- allow the retry by replacing the record.

    4. **Cache** the response for endpoints marked as cacheable when the
       status code indicates success (200 or 201).

    5. Attach ``X-Idempotency-Status`` and ``X-Idempotency-Key`` response
       headers to every processed response.

    GPU Execution Safety
    --------------------
    This middleware is the primary safeguard against duplicate GPU execution.
    Without it, a retried ``/api/v1/inference/batch`` request would allocate a
    second GPU memory arena and launch a redundant CUDA kernel graph, wasting
    both compute and memory.  For large batch sizes this can trigger OOM kills
    that cascade across the entire inference fleet.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Core dispatch logic implementing the idempotency protocol."""
        method = request.method.upper()
        path = request.url.path

        # ---- Step 1: Extract the idempotency key ---- #
        idempotency_key: Optional[str] = request.headers.get("idempotency-key")

        # For non-mutating methods, pass through without enforcement.
        if method not in MUTATING_METHODS:
            response = await call_next(request)
            if idempotency_key:
                response.headers["X-Idempotency-Key"] = idempotency_key
                response.headers["X-Idempotency-Status"] = "new"
            return response

        # Mutating methods require an idempotency key.
        if not idempotency_key:
            logger.warning(
                "Missing Idempotency-Key header for %s %s", method, path
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "missing_idempotency_key",
                    "detail": (
                        "The Idempotency-Key header is required for "
                        f"{method} requests."
                    ),
                },
            )

        if not _validate_uuid(idempotency_key):
            logger.warning(
                "Invalid Idempotency-Key format: %s", idempotency_key
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_idempotency_key",
                    "detail": "Idempotency-Key must be a valid UUID.",
                },
            )

        # ---- Step 2: Compute request fingerprint ---- #
        fingerprint = await _compute_fingerprint(request, idempotency_key)
        endpoint_config = _get_endpoint_config(path)

        # ---- Step 3: Check the idempotency store ---- #
        existing_record = await _idempotency_store.get(fingerprint.digest)

        if existing_record is not None:
            if existing_record.state == ProcessingState.PENDING:
                # Duplicate request while original is still in-flight.
                logger.info(
                    "Duplicate in-flight request suppressed key=%s path=%s",
                    idempotency_key,
                    path,
                )
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "duplicate_request",
                        "detail": (
                            "A request with this idempotency key is currently "
                            "being processed. Please wait and retry."
                        ),
                    },
                    headers={
                        "X-Idempotency-Key": idempotency_key,
                        "X-Idempotency-Status": "duplicate",
                    },
                )

            if existing_record.state == ProcessingState.COMPLETED:
                # Return the previously cached response verbatim.
                logger.info(
                    "Returning cached response for key=%s path=%s",
                    idempotency_key,
                    path,
                )
                cached_headers = dict(existing_record.response_headers or {})
                cached_headers["X-Idempotency-Key"] = idempotency_key
                cached_headers["X-Idempotency-Status"] = "cached"
                return Response(
                    content=existing_record.response_body,
                    status_code=existing_record.status_code or 200,
                    headers=cached_headers,
                    media_type="application/json",
                )

            if existing_record.state == ProcessingState.FAILED:
                # Previous attempt failed -- allow a fresh retry.
                logger.info(
                    "Previous attempt failed; allowing retry key=%s path=%s",
                    idempotency_key,
                    path,
                )
                await _idempotency_store.delete(fingerprint.digest)

        # ---- Step 4: Create PENDING record and forward request ---- #
        pending_record = IdempotencyRecord(
            fingerprint=fingerprint.digest,
            idempotency_key=idempotency_key,
            state=ProcessingState.PENDING,
        )
        await _idempotency_store.put(pending_record)

        try:
            response = await call_next(request)
        except Exception:
            # Mark as FAILED so that the client can safely retry with the
            # same idempotency key without hitting a 409.
            pending_record.mark_failed()
            await _idempotency_store.put(pending_record)
            logger.exception(
                "Request processing failed key=%s path=%s",
                idempotency_key,
                path,
            )
            raise

        # ---- Step 5: Read response body for caching ---- #
        response_body = b""
        async for chunk in response.body_iterator:  # type: ignore[union-attr]
            if isinstance(chunk, str):
                response_body += chunk.encode("utf-8")
            else:
                response_body += chunk

        is_success = response.status_code in (200, 201)
        should_cache = (
            is_success
            and endpoint_config is not None
            and endpoint_config.cacheable
        )

        if should_cache:
            pending_record.mark_completed(
                status_code=response.status_code,
                body=response_body,
                headers=dict(response.headers),
            )
            await _idempotency_store.put(pending_record)
            logger.debug(
                "Cached response for key=%s path=%s status=%d",
                idempotency_key,
                path,
                response.status_code,
            )
        elif not is_success:
            pending_record.mark_failed()
            await _idempotency_store.put(pending_record)
        else:
            # Success on a non-cacheable endpoint -- remove the PENDING sentinel.
            await _idempotency_store.delete(fingerprint.digest)

        # ---- Step 6: Attach idempotency response headers ---- #
        headers = dict(response.headers)
        headers["X-Idempotency-Key"] = idempotency_key
        headers["X-Idempotency-Status"] = "new"

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )


def get_idempotency_store() -> InMemoryIdempotencyStore:
    """Return the module-level idempotency store instance.

    Useful for health-check endpoints and integration tests that need to
    inspect or reset the store state.
    """
    return _idempotency_store
