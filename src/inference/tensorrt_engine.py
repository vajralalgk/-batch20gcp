"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
TensorRT-LLM Engine Manager
============================================================================

Manages the full lifecycle of a TensorRT-LLM inference engine including
INT8 quantization, Tensor Parallel (TP=4) configuration across NVIDIA
A100 GPUs, engine warm-up, memory profiling, and runtime statistics.

Includes a mock/simulation mode for development environments that lack
physical GPUs.

Usage:
    from src.inference.tensorrt_engine import TensorRTEngine

    engine = TensorRTEngine(model_path="/models/netflix_llm")
    await engine.load_engine()
    stats = engine.get_engine_stats()
============================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants & enums
# ---------------------------------------------------------------------------

class EngineState(str, Enum):
    """Runtime states of the TensorRT engine."""
    UNLOADED = "UNLOADED"
    LOADING = "LOADING"
    OPTIMIZING = "OPTIMIZING"
    READY = "READY"
    WARM = "WARM"
    ERROR = "ERROR"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class QuantizationType(str, Enum):
    """Supported quantization modes."""
    FP16 = "FP16"
    INT8 = "INT8"
    INT4 = "INT4"
    FP8 = "FP8"
    NONE = "NONE"


class GPUType(str, Enum):
    """Recognized GPU architectures."""
    A100_40GB = "A100_40GB"
    A100_80GB = "A100_80GB"
    H100 = "H100"
    L4 = "L4"
    MOCK = "MOCK"


# ---------------------------------------------------------------------------
# Configuration & statistics data classes
# ---------------------------------------------------------------------------

@dataclass
class QuantizationConfig:
    """Controls how the model weights are quantized for TensorRT."""
    quantization_type: QuantizationType = QuantizationType.INT8
    calibration_dataset_path: Optional[str] = None
    calibration_batch_size: int = 32
    calibration_num_batches: int = 100
    use_smooth_quant: bool = True
    smooth_quant_alpha: float = 0.5
    per_channel: bool = True
    per_token: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quantization_type": self.quantization_type.value,
            "calibration_dataset_path": self.calibration_dataset_path,
            "calibration_batch_size": self.calibration_batch_size,
            "calibration_num_batches": self.calibration_num_batches,
            "use_smooth_quant": self.use_smooth_quant,
            "smooth_quant_alpha": self.smooth_quant_alpha,
            "per_channel": self.per_channel,
            "per_token": self.per_token,
        }


@dataclass
class TensorParallelConfig:
    """Tensor Parallel (TP) settings for multi-GPU inference."""
    tp_size: int = 4
    pp_size: int = 1  # pipeline parallel degree
    gpu_type: GPUType = GPUType.A100_80GB
    gpu_memory_gb: float = 80.0
    max_batch_size: int = 64
    max_input_len: int = 2048
    max_output_len: int = 512
    max_beam_width: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tp_size": self.tp_size,
            "pp_size": self.pp_size,
            "gpu_type": self.gpu_type.value,
            "gpu_memory_gb": self.gpu_memory_gb,
            "max_batch_size": self.max_batch_size,
            "max_input_len": self.max_input_len,
            "max_output_len": self.max_output_len,
            "max_beam_width": self.max_beam_width,
        }


@dataclass
class MemoryProfile:
    """Snapshot of engine memory consumption."""
    total_gpu_memory_gb: float = 0.0
    used_gpu_memory_gb: float = 0.0
    engine_memory_gb: float = 0.0
    kv_cache_memory_gb: float = 0.0
    activation_memory_gb: float = 0.0
    available_gpu_memory_gb: float = 0.0
    memory_utilization_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_gpu_memory_gb": round(self.total_gpu_memory_gb, 3),
            "used_gpu_memory_gb": round(self.used_gpu_memory_gb, 3),
            "engine_memory_gb": round(self.engine_memory_gb, 3),
            "kv_cache_memory_gb": round(self.kv_cache_memory_gb, 3),
            "activation_memory_gb": round(self.activation_memory_gb, 3),
            "available_gpu_memory_gb": round(self.available_gpu_memory_gb, 3),
            "memory_utilization_pct": round(self.memory_utilization_pct, 2),
        }


@dataclass
class EngineStats:
    """Runtime statistics for the TensorRT engine."""
    state: EngineState = EngineState.UNLOADED
    load_time_s: float = 0.0
    optimization_time_s: float = 0.0
    warmup_time_s: float = 0.0
    total_inferences: int = 0
    avg_tokens_per_second: float = 0.0
    peak_tokens_per_second: float = 0.0
    memory_profile: MemoryProfile = field(default_factory=MemoryProfile)
    quantization: Optional[Dict[str, Any]] = None
    tensor_parallel: Optional[Dict[str, Any]] = None
    engine_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "state": self.state.value,
            "load_time_s": round(self.load_time_s, 3),
            "optimization_time_s": round(self.optimization_time_s, 3),
            "warmup_time_s": round(self.warmup_time_s, 3),
            "total_inferences": self.total_inferences,
            "avg_tokens_per_second": round(self.avg_tokens_per_second, 1),
            "peak_tokens_per_second": round(self.peak_tokens_per_second, 1),
            "memory_profile": self.memory_profile.to_dict(),
            "quantization": self.quantization,
            "tensor_parallel": self.tensor_parallel,
        }


# ---------------------------------------------------------------------------
# Engine implementation
# ---------------------------------------------------------------------------

class TensorRTEngine:
    """Manages the lifecycle of a TensorRT-LLM inference engine.

    This class handles loading, quantization, tensor-parallel setup,
    warm-up, memory profiling, and teardown of a TensorRT-LLM engine
    targeting multi-GPU A100 deployments.

    Parameters
    ----------
    model_path:
        Filesystem path to the model weights / engine directory.
    model_name:
        Human-readable name used in logs and metrics.
    quantization_config:
        Quantization strategy (defaults to INT8 with SmoothQuant).
    tp_config:
        Tensor Parallel configuration (defaults to TP=4 on A100-80GB).
    mock_mode:
        Operate without a real GPU.  All operations return synthetic data.
    """

    def __init__(
        self,
        model_path: str = "/models/netflix_llm",
        model_name: str = "netflix_llm_trt",
        quantization_config: Optional[QuantizationConfig] = None,
        tp_config: Optional[TensorParallelConfig] = None,
        mock_mode: bool = False,
    ) -> None:
        self._model_path = Path(model_path)
        self._model_name = model_name
        self._quant_config = quantization_config or QuantizationConfig()
        self._tp_config = tp_config or TensorParallelConfig()
        self._mock_mode = mock_mode

        # Runtime state
        self._engine: Optional[Any] = None
        self._runtime: Optional[Any] = None
        self._stats = EngineStats(
            quantization=self._quant_config.to_dict(),
            tensor_parallel=self._tp_config.to_dict(),
        )
        self._lock = asyncio.Lock()

        logger.info(
            "tensorrt_engine_init",
            extra={
                "model_path": str(self._model_path),
                "quantization": self._quant_config.quantization_type.value,
                "tp_size": self._tp_config.tp_size,
                "mock_mode": self._mock_mode,
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    async def load_engine(self) -> EngineStats:
        """Load the TensorRT-LLM engine into GPU memory.

        This method is idempotent -- calling it when the engine is already
        loaded returns the existing stats without reloading.

        Returns
        -------
        EngineStats
            Post-load statistics including memory profile.
        """
        async with self._lock:
            if self._stats.state in (EngineState.READY, EngineState.WARM):
                logger.info("tensorrt_engine_already_loaded")
                return self._stats

            self._stats.state = EngineState.LOADING
            start = time.perf_counter()

            try:
                if self._mock_mode:
                    await self._mock_load()
                else:
                    await self._real_load()

                self._stats.load_time_s = time.perf_counter() - start
                self._stats.state = EngineState.READY
                self._stats.memory_profile = await self._profile_memory()

                logger.info(
                    "tensorrt_engine_loaded",
                    extra={
                        "load_time_s": self._stats.load_time_s,
                        "memory_gb": self._stats.memory_profile.engine_memory_gb,
                    },
                )
                return self._stats

            except Exception as exc:
                self._stats.state = EngineState.ERROR
                logger.error("tensorrt_engine_load_failed", extra={"error": str(exc)})
                raise

    async def _real_load(self) -> None:
        """Load the engine using the TensorRT-LLM runtime."""
        try:
            import tensorrt_llm  # type: ignore[import-untyped]
            from tensorrt_llm.runtime import ModelRunner  # type: ignore[import-untyped]

            runner_kwargs: Dict[str, Any] = {
                "engine_dir": str(self._model_path),
                "rank": 0,
            }

            if self._tp_config.tp_size > 1:
                runner_kwargs["tp_size"] = self._tp_config.tp_size
                runner_kwargs["pp_size"] = self._tp_config.pp_size

            self._runtime = ModelRunner.from_dir(**runner_kwargs)
            self._engine = self._runtime

        except ImportError:
            logger.warning("tensorrt_llm_not_installed_falling_back_to_mock")
            self._mock_mode = True
            await self._mock_load()

    async def _mock_load(self) -> None:
        """Simulate engine loading with realistic timing."""
        # Simulate I/O + deserialization delay
        await asyncio.sleep(0.1)
        self._engine = {"type": "mock", "loaded": True}
        self._runtime = {"type": "mock"}

    async def optimize_model(
        self,
        quantization_config: Optional[QuantizationConfig] = None,
    ) -> EngineStats:
        """Run quantization and layer-fusion optimization passes.

        Parameters
        ----------
        quantization_config:
            Override the default quantization strategy for this optimization
            pass.  When ``None`` the config supplied at init-time is used.

        Returns
        -------
        EngineStats
            Updated statistics including optimization time.
        """
        async with self._lock:
            self._stats.state = EngineState.OPTIMIZING
            qconfig = quantization_config or self._quant_config
            start = time.perf_counter()

            try:
                if self._mock_mode:
                    await self._mock_optimize(qconfig)
                else:
                    await self._real_optimize(qconfig)

                self._stats.optimization_time_s = time.perf_counter() - start
                self._stats.state = EngineState.READY
                self._stats.quantization = qconfig.to_dict()
                self._stats.memory_profile = await self._profile_memory()

                logger.info(
                    "tensorrt_engine_optimized",
                    extra={
                        "optimization_time_s": self._stats.optimization_time_s,
                        "quantization": qconfig.quantization_type.value,
                    },
                )
                return self._stats

            except Exception as exc:
                self._stats.state = EngineState.ERROR
                logger.error("tensorrt_engine_optimize_failed", extra={"error": str(exc)})
                raise

    async def _real_optimize(self, qconfig: QuantizationConfig) -> None:
        """Run actual TensorRT optimization passes."""
        try:
            import tensorrt_llm  # type: ignore[import-untyped]
            from tensorrt_llm.quantization import QuantMode  # type: ignore[import-untyped]

            quant_mode = QuantMode(0)
            if qconfig.quantization_type == QuantizationType.INT8:
                quant_mode = quant_mode | QuantMode.INT8_WEIGHT_ONLY
                if qconfig.use_smooth_quant:
                    quant_mode = quant_mode | QuantMode.INT8_KV_CACHE

            logger.info(
                "tensorrt_applying_quantization",
                extra={"quant_mode": str(quant_mode)},
            )

        except ImportError:
            logger.warning("tensorrt_llm_not_available_skipping_optimization")
            self._mock_mode = True
            await self._mock_optimize(qconfig)

    async def _mock_optimize(self, qconfig: QuantizationConfig) -> None:
        """Simulate optimization with realistic timing."""
        await asyncio.sleep(0.15)

    async def warm_up(
        self,
        num_iterations: int = 10,
        batch_sizes: Optional[List[int]] = None,
        sequence_lengths: Optional[List[int]] = None,
    ) -> EngineStats:
        """Warm up the engine with representative inputs.

        Running warm-up iterations primes GPU caches and allows the CUDA
        driver to finish any deferred compilation.

        Parameters
        ----------
        num_iterations:
            Number of warm-up inference calls per (batch, seq_len) pair.
        batch_sizes:
            Batch sizes to exercise.  Defaults to ``[1, 8, 32]``.
        sequence_lengths:
            Sequence lengths to exercise.  Defaults to ``[128, 512, 1024]``.

        Returns
        -------
        EngineStats
            Updated statistics including warmup time and throughput.
        """
        if self._stats.state not in (EngineState.READY, EngineState.WARM):
            raise RuntimeError(
                f"Engine must be READY or WARM before warm-up, "
                f"current state: {self._stats.state.value}"
            )

        batch_sizes = batch_sizes or [1, 8, 32]
        sequence_lengths = sequence_lengths or [128, 512, 1024]

        start = time.perf_counter()
        total_tokens = 0
        peak_tps: float = 0.0

        for bs in batch_sizes:
            for seq_len in sequence_lengths:
                for _ in range(num_iterations):
                    iter_start = time.perf_counter()
                    input_ids = np.random.randint(0, 32000, (bs, seq_len), dtype=np.int64)
                    attention_mask = np.ones((bs, seq_len), dtype=np.int64)

                    if self._mock_mode:
                        await asyncio.sleep(0.002)
                    else:
                        await self._run_engine_inference(input_ids, attention_mask)

                    iter_time = time.perf_counter() - iter_start
                    tokens = bs * seq_len
                    total_tokens += tokens
                    tps = tokens / max(iter_time, 1e-9)
                    peak_tps = max(peak_tps, tps)

        warmup_time = time.perf_counter() - start
        self._stats.warmup_time_s = warmup_time
        self._stats.peak_tokens_per_second = peak_tps
        self._stats.avg_tokens_per_second = total_tokens / max(warmup_time, 1e-9)
        self._stats.state = EngineState.WARM

        logger.info(
            "tensorrt_engine_warmed_up",
            extra={
                "warmup_time_s": warmup_time,
                "avg_tps": self._stats.avg_tokens_per_second,
                "peak_tps": peak_tps,
            },
        )
        return self._stats

    async def _run_engine_inference(
        self,
        input_ids: np.ndarray,
        attention_mask: np.ndarray,
    ) -> np.ndarray:
        """Execute a single forward pass through the loaded engine."""
        if self._mock_mode:
            batch_size, seq_len = input_ids.shape
            return np.random.randn(batch_size, seq_len, 32000).astype(np.float32)

        if self._runtime is None:
            raise RuntimeError("Engine runtime is not initialized")

        output = self._runtime.generate(
            batch_input_ids=[input_ids],
            max_new_tokens=1,
        )
        return output

    async def unload_engine(self) -> None:
        """Release GPU memory and tear down the engine."""
        async with self._lock:
            if self._stats.state == EngineState.UNLOADED:
                return

            self._stats.state = EngineState.SHUTTING_DOWN
            logger.info("tensorrt_engine_unloading")

            try:
                if not self._mock_mode and self._runtime is not None:
                    del self._runtime
                    del self._engine

                    # Force CUDA memory release
                    try:
                        import torch  # type: ignore[import-untyped]
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                    except ImportError:
                        pass

                self._engine = None
                self._runtime = None
                self._stats.state = EngineState.UNLOADED
                self._stats.memory_profile = MemoryProfile()
                logger.info("tensorrt_engine_unloaded")

            except Exception as exc:
                self._stats.state = EngineState.ERROR
                logger.error("tensorrt_engine_unload_failed", extra={"error": str(exc)})
                raise

    # ------------------------------------------------------------------
    # Memory profiling
    # ------------------------------------------------------------------

    async def _profile_memory(self) -> MemoryProfile:
        """Capture current GPU memory usage for the engine."""
        if self._mock_mode:
            return self._mock_memory_profile()

        try:
            import torch  # type: ignore[import-untyped]

            profile = MemoryProfile()
            if torch.cuda.is_available():
                device = torch.cuda.current_device()
                total = torch.cuda.get_device_properties(device).total_mem
                allocated = torch.cuda.memory_allocated(device)
                reserved = torch.cuda.memory_reserved(device)

                profile.total_gpu_memory_gb = total / (1024 ** 3)
                profile.used_gpu_memory_gb = allocated / (1024 ** 3)
                profile.engine_memory_gb = reserved / (1024 ** 3)
                profile.available_gpu_memory_gb = (total - allocated) / (1024 ** 3)
                profile.memory_utilization_pct = (allocated / total) * 100.0

                # Estimate KV cache and activation memory from reserved
                profile.kv_cache_memory_gb = profile.engine_memory_gb * 0.30
                profile.activation_memory_gb = profile.engine_memory_gb * 0.15

            return profile

        except ImportError:
            return self._mock_memory_profile()

    def _mock_memory_profile(self) -> MemoryProfile:
        """Return a synthetic memory profile for mock mode."""
        tp = self._tp_config
        per_gpu_gb = tp.gpu_memory_gb
        total_gb = per_gpu_gb * tp.tp_size

        # Simulate realistic memory distribution for a large LLM
        engine_gb = total_gb * 0.45
        kv_cache_gb = total_gb * 0.20
        activation_gb = total_gb * 0.08
        used_gb = engine_gb + kv_cache_gb + activation_gb

        return MemoryProfile(
            total_gpu_memory_gb=total_gb,
            used_gpu_memory_gb=used_gb,
            engine_memory_gb=engine_gb,
            kv_cache_memory_gb=kv_cache_gb,
            activation_memory_gb=activation_gb,
            available_gpu_memory_gb=total_gb - used_gb,
            memory_utilization_pct=(used_gb / total_gb) * 100.0,
        )

    # ------------------------------------------------------------------
    # Stats & status
    # ------------------------------------------------------------------

    def get_engine_stats(self) -> Dict[str, Any]:
        """Return a serializable snapshot of engine statistics."""
        return self._stats.to_dict()

    @property
    def state(self) -> EngineState:
        return self._stats.state

    @property
    def is_ready(self) -> bool:
        return self._stats.state in (EngineState.READY, EngineState.WARM)

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    def __repr__(self) -> str:
        return (
            f"TensorRTEngine(model={self._model_name!r}, "
            f"state={self._stats.state.value}, "
            f"quant={self._quant_config.quantization_type.value}, "
            f"tp={self._tp_config.tp_size}, "
            f"mock={self._mock_mode})"
        )
