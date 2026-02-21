"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Triton Inference Server gRPC Client
============================================================================

Provides a production-grade gRPC client for NVIDIA Triton Inference Server
with connection pooling, retry logic, async inference, and comprehensive
metrics collection. Includes a mock/simulation mode for development and
testing environments where Triton is unavailable.

Usage:
    from src.inference.triton_client import TritonInferenceClient

    client = TritonInferenceClient(url="localhost:8001")
    await client.connect()
    result = await client.infer(input_ids=ids, attention_mask=mask)
============================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class ModelState(str, Enum):
    """Possible states of a model hosted on Triton."""
    READY = "READY"
    LOADING = "LOADING"
    UNLOADING = "UNLOADING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class InferenceResult:
    """Encapsulates the output returned from a single Triton inference call."""
    request_id: str
    outputs: Dict[str, np.ndarray]
    latency_ms: float
    model_name: str
    model_version: str


@dataclass
class InferenceMetrics:
    """Aggregated metrics collected across inference calls."""
    inference_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    latency_histogram: List[float] = field(default_factory=list)

    # --- derived helpers ---------------------------------------------------

    @property
    def avg_latency_ms(self) -> float:
        if self.inference_count == 0:
            return 0.0
        return self.total_latency_ms / self.inference_count

    @property
    def error_rate(self) -> float:
        total = self.inference_count + self.error_count
        if total == 0:
            return 0.0
        return self.error_count / total

    @property
    def p99_latency_ms(self) -> float:
        if not self.latency_histogram:
            return 0.0
        sorted_latencies = sorted(self.latency_histogram)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inference_count": self.inference_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "min_latency_ms": round(self.min_latency_ms, 3) if self.min_latency_ms != float("inf") else 0.0,
            "max_latency_ms": round(self.max_latency_ms, 3),
            "p99_latency_ms": round(self.p99_latency_ms, 3),
        }


@dataclass
class TritonModelConfig:
    """Subset of Triton model configuration relevant to the client."""
    name: str
    version: str
    platform: str
    max_batch_size: int
    inputs: List[Dict[str, Any]]
    outputs: List[Dict[str, Any]]
    instance_count: int = 1
    dynamic_batching_enabled: bool = False


# ---------------------------------------------------------------------------
# Client implementation
# ---------------------------------------------------------------------------

class TritonInferenceClient:
    """High-level async client for NVIDIA Triton Inference Server (gRPC).

    Features
    --------
    * Async ``infer`` with configurable timeout.
    * Transparent connection pooling over a single gRPC channel.
    * Automatic retries with exponential back-off.
    * Built-in metrics collection (latency, throughput, errors).
    * Mock / simulation mode for local development without a GPU cluster.

    Parameters
    ----------
    url:
        ``host:port`` of the Triton gRPC endpoint (default ``localhost:8001``).
    model_name:
        Name of the model repository entry to target.
    model_version:
        Model version string (empty string = latest).
    timeout_s:
        Per-request timeout in seconds.
    max_retries:
        Number of retry attempts on transient failures.
    retry_backoff_s:
        Initial back-off duration between retries (doubles each attempt).
    pool_size:
        Number of gRPC channels to maintain in the connection pool.
    mock_mode:
        When ``True`` the client returns synthetic outputs instead of
        calling Triton.  Useful for unit tests and CI pipelines.
    """

    def __init__(
        self,
        url: str = "localhost:8001",
        model_name: str = "netflix_llm",
        model_version: str = "",
        timeout_s: float = 30.0,
        max_retries: int = 3,
        retry_backoff_s: float = 0.5,
        pool_size: int = 4,
        mock_mode: bool = False,
    ) -> None:
        self._url = url
        self._model_name = model_name
        self._model_version = model_version
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s
        self._pool_size = pool_size
        self._mock_mode = mock_mode

        # Connection pool: list of (grpc_client, in_use_flag)
        self._channel_pool: List[Tuple[Any, bool]] = []
        self._pool_lock = asyncio.Lock()
        self._connected = False

        # Metrics
        self._metrics = InferenceMetrics()

        # Track in-flight requests for graceful shutdown
        self._inflight: int = 0
        self._inflight_lock = asyncio.Lock()

        logger.info(
            "triton_client_init",
            extra={
                "url": self._url,
                "model": self._model_name,
                "mock_mode": self._mock_mode,
                "pool_size": self._pool_size,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the gRPC connection pool to Triton.

        In mock mode this is a no-op that simply marks the client as
        connected.
        """
        if self._connected:
            logger.warning("triton_client_already_connected")
            return

        if self._mock_mode:
            logger.info("triton_client_mock_connected")
            self._connected = True
            return

        try:
            # Attempt to import tritonclient at connect-time so that the
            # module can be imported in environments without the SDK
            # installed (mock-mode only scenarios).
            import tritonclient.grpc.aio as grpcclient  # type: ignore[import-untyped]

            for _ in range(self._pool_size):
                client = grpcclient.InferenceServerClient(
                    url=self._url,
                    verbose=False,
                )
                self._channel_pool.append((client, False))

            # Validate connectivity against the first channel
            is_live = await self._channel_pool[0][0].is_server_live()
            if not is_live:
                raise ConnectionError(
                    f"Triton server at {self._url} is not live"
                )

            self._connected = True
            logger.info(
                "triton_client_connected",
                extra={"url": self._url, "pool_size": self._pool_size},
            )
        except ImportError:
            logger.warning(
                "tritonclient_not_installed_falling_back_to_mock",
            )
            self._mock_mode = True
            self._connected = True
        except Exception as exc:
            logger.error("triton_client_connect_failed", extra={"error": str(exc)})
            raise

    async def disconnect(self) -> None:
        """Gracefully close all pooled gRPC channels."""
        if not self._connected:
            return

        # Wait for in-flight requests (up to timeout)
        deadline = time.monotonic() + self._timeout_s
        while self._inflight > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        if not self._mock_mode:
            for client, _ in self._channel_pool:
                try:
                    await client.close()
                except Exception:
                    pass

        self._channel_pool.clear()
        self._connected = False
        logger.info("triton_client_disconnected")

    # ------------------------------------------------------------------
    # Connection pool helpers
    # ------------------------------------------------------------------

    async def _acquire_channel(self) -> Any:
        """Acquire an idle channel from the pool (round-robin)."""
        async with self._pool_lock:
            for idx, (client, in_use) in enumerate(self._channel_pool):
                if not in_use:
                    self._channel_pool[idx] = (client, True)
                    return client
        # All channels busy -- wait briefly and retry
        await asyncio.sleep(0.05)
        return await self._acquire_channel()

    async def _release_channel(self, client: Any) -> None:
        """Return a channel to the pool."""
        async with self._pool_lock:
            for idx, (c, _) in enumerate(self._channel_pool):
                if c is client:
                    self._channel_pool[idx] = (c, False)
                    return

    # ------------------------------------------------------------------
    # Core inference
    # ------------------------------------------------------------------

    async def infer(
        self,
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
        request_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> InferenceResult:
        """Run a single inference request against Triton.

        Parameters
        ----------
        input_ids:
            Token IDs array of shape ``(batch, seq_len)`` with dtype int64.
        attention_mask:
            Attention mask array of same shape, dtype int64.
        request_id:
            Optional client-supplied request identifier.  Auto-generated
            when not provided.
        timeout_s:
            Per-request timeout override.

        Returns
        -------
        InferenceResult
            Inference outputs, latency, and metadata.

        Raises
        ------
        ConnectionError
            If the client has not been connected.
        TimeoutError
            If the request exceeds the configured timeout.
        RuntimeError
            On exhausted retries.
        """
        if not self._connected:
            raise ConnectionError("Client is not connected. Call connect() first.")

        request_id = request_id or str(uuid.uuid4())
        effective_timeout = timeout_s or self._timeout_s
        start = time.perf_counter()

        async with self._inflight_lock:
            self._inflight += 1

        try:
            result = await self._infer_with_retry(
                input_ids=input_ids,
                attention_mask=attention_mask,
                request_id=request_id,
                timeout_s=effective_timeout,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0
            self._record_success(latency_ms)

            return InferenceResult(
                request_id=request_id,
                outputs=result,
                latency_ms=latency_ms,
                model_name=self._model_name,
                model_version=self._model_version or "latest",
            )
        except Exception:
            self._metrics.error_count += 1
            raise
        finally:
            async with self._inflight_lock:
                self._inflight -= 1

    async def _infer_with_retry(
        self,
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
        request_id: str,
        timeout_s: float,
    ) -> Dict[str, np.ndarray]:
        """Execute inference with exponential-backoff retry."""
        last_exception: Optional[Exception] = None
        backoff = self._retry_backoff_s

        for attempt in range(1, self._max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._do_infer(input_ids, attention_mask, request_id),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "triton_infer_timeout",
                    extra={"attempt": attempt, "request_id": request_id},
                )
                last_exception = TimeoutError(
                    f"Inference timed out after {timeout_s}s "
                    f"(attempt {attempt}/{self._max_retries})"
                )
            except Exception as exc:
                logger.warning(
                    "triton_infer_error",
                    extra={
                        "attempt": attempt,
                        "request_id": request_id,
                        "error": str(exc),
                    },
                )
                last_exception = exc

            if attempt < self._max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError(
            f"Inference failed after {self._max_retries} attempts"
        ) from last_exception

    async def _do_infer(
        self,
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
        request_id: str,
    ) -> Dict[str, np.ndarray]:
        """Execute a single inference call (real or mock)."""
        if self._mock_mode:
            return await self._mock_infer(input_ids, attention_mask)

        import tritonclient.grpc.aio as grpcclient  # type: ignore[import-untyped]

        client = await self._acquire_channel()
        try:
            inp_ids = grpcclient.InferInput(
                "input_ids", list(input_ids.shape), "INT64"
            )
            inp_ids.set_data_from_numpy(input_ids.astype(np.int64))

            inp_mask = grpcclient.InferInput(
                "attention_mask", list(attention_mask.shape), "INT64"
            )
            inp_mask.set_data_from_numpy(attention_mask.astype(np.int64))

            output_logits = grpcclient.InferRequestedOutput("logits")

            response = await client.infer(
                model_name=self._model_name,
                model_version=self._model_version,
                inputs=[inp_ids, inp_mask],
                outputs=[output_logits],
                request_id=request_id,
            )

            return {
                name: response.as_numpy(name)
                for name in ["logits"]
            }
        finally:
            await self._release_channel(client)

    async def _mock_infer(
        self,
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """Return synthetic inference outputs for dev/testing."""
        # Simulate variable network + compute latency
        await asyncio.sleep(0.005 + 0.010 * np.random.random())

        batch_size, seq_len = input_ids.shape
        vocab_size = 32000  # typical LLM vocabulary
        logits = np.random.randn(batch_size, seq_len, vocab_size).astype(np.float32)
        return {"logits": logits}

    # ------------------------------------------------------------------
    # Health & status
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Return the liveness and readiness state of the Triton server.

        Returns a dict with keys ``live``, ``ready``, and ``model_ready``.
        """
        if self._mock_mode:
            return {"live": True, "ready": True, "model_ready": True}

        if not self._connected or not self._channel_pool:
            return {"live": False, "ready": False, "model_ready": False}

        client = self._channel_pool[0][0]
        try:
            live = await client.is_server_live()
            ready = await client.is_server_ready()
            model_ready = await client.is_model_ready(
                self._model_name, self._model_version
            )
            return {
                "live": live,
                "ready": ready,
                "model_ready": model_ready,
            }
        except Exception as exc:
            logger.error("triton_health_check_failed", extra={"error": str(exc)})
            return {"live": False, "ready": False, "model_ready": False}

    async def get_model_status(self) -> Dict[str, Any]:
        """Fetch extended model status from the Triton server."""
        if self._mock_mode:
            return {
                "name": self._model_name,
                "version": self._model_version or "1",
                "state": ModelState.READY.value,
                "backend": "tensorrtllm",
                "device": "GPU",
                "inference_count": self._metrics.inference_count,
            }

        if not self._connected or not self._channel_pool:
            return {"name": self._model_name, "state": ModelState.UNAVAILABLE.value}

        client = self._channel_pool[0][0]
        try:
            metadata = await client.get_model_metadata(
                self._model_name, self._model_version
            )
            return {
                "name": metadata.name,
                "version": metadata.versions[0] if metadata.versions else "unknown",
                "state": ModelState.READY.value,
                "platform": metadata.platform,
                "inputs": [
                    {"name": inp.name, "datatype": inp.datatype, "shape": list(inp.shape)}
                    for inp in metadata.inputs
                ],
                "outputs": [
                    {"name": out.name, "datatype": out.datatype, "shape": list(out.shape)}
                    for out in metadata.outputs
                ],
            }
        except Exception as exc:
            logger.error("triton_model_status_failed", extra={"error": str(exc)})
            return {"name": self._model_name, "state": ModelState.UNAVAILABLE.value, "error": str(exc)}

    async def get_model_config(self) -> TritonModelConfig:
        """Retrieve the full model configuration from Triton.

        In mock mode a representative default configuration is returned.
        """
        if self._mock_mode:
            return TritonModelConfig(
                name=self._model_name,
                version=self._model_version or "1",
                platform="tensorrtllm",
                max_batch_size=64,
                inputs=[
                    {"name": "input_ids", "datatype": "INT64", "shape": [-1, -1]},
                    {"name": "attention_mask", "datatype": "INT64", "shape": [-1, -1]},
                ],
                outputs=[
                    {"name": "logits", "datatype": "FP32", "shape": [-1, -1, 32000]},
                ],
                instance_count=4,
                dynamic_batching_enabled=True,
            )

        client = self._channel_pool[0][0]
        config_resp = await client.get_model_config(
            self._model_name, self._model_version
        )
        config = config_resp.config

        return TritonModelConfig(
            name=config.name,
            version=self._model_version or "1",
            platform=config.platform,
            max_batch_size=config.max_batch_size,
            inputs=[
                {"name": inp.name, "datatype": inp.data_type, "shape": list(inp.dims)}
                for inp in config.input
            ],
            outputs=[
                {"name": out.name, "datatype": out.data_type, "shape": list(out.dims)}
                for out in config.output
            ],
            instance_count=sum(
                ig.count for ig in config.instance_group
            ) if config.instance_group else 1,
            dynamic_batching_enabled=config.HasField("dynamic_batching"),
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> Dict[str, Any]:
        """Return a snapshot of collected inference metrics."""
        return self._metrics.to_dict()

    def reset_metrics(self) -> None:
        """Reset all collected metrics to zero."""
        self._metrics = InferenceMetrics()

    def _record_success(self, latency_ms: float) -> None:
        m = self._metrics
        m.inference_count += 1
        m.total_latency_ms += latency_ms
        m.min_latency_ms = min(m.min_latency_ms, latency_ms)
        m.max_latency_ms = max(m.max_latency_ms, latency_ms)
        # Keep a bounded histogram for percentile computation
        if len(m.latency_histogram) >= 10_000:
            m.latency_histogram = m.latency_histogram[-5_000:]
        m.latency_histogram.append(latency_ms)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    def __repr__(self) -> str:
        return (
            f"TritonInferenceClient(url={self._url!r}, "
            f"model={self._model_name!r}, "
            f"mock={self._mock_mode}, "
            f"connected={self._connected})"
        )
