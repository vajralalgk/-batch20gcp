"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Inference Package
============================================================================
Re-exports the key classes from the inference sub-modules for convenient
imports:

    from src.inference import (
        TritonInferenceClient,
        TensorRTEngine,
        DynamicBatcher,
        ModelRegistry,
    )
============================================================================
"""

from src.inference.triton_client import (
    TritonInferenceClient,
    InferenceResult,
    InferenceMetrics,
    TritonModelConfig,
    ModelState,
)
from src.inference.tensorrt_engine import (
    TensorRTEngine,
    QuantizationConfig,
    TensorParallelConfig,
    MemoryProfile,
    EngineStats,
    EngineState,
    QuantizationType,
    GPUType,
)
from src.inference.dynamic_batcher import (
    DynamicBatcher,
    InferenceRequest,
    BatchResult,
    BatcherStats,
    RequestPriority,
)
from src.inference.model_registry import (
    ModelRegistry,
    ModelVersion,
    ModelBenchmark,
    ABTestConfig,
    ModelStatus,
    DeploymentStrategy,
)

__all__ = [
    # Triton client
    "TritonInferenceClient",
    "InferenceResult",
    "InferenceMetrics",
    "TritonModelConfig",
    "ModelState",
    # TensorRT engine
    "TensorRTEngine",
    "QuantizationConfig",
    "TensorParallelConfig",
    "MemoryProfile",
    "EngineStats",
    "EngineState",
    "QuantizationType",
    "GPUType",
    # Dynamic batcher
    "DynamicBatcher",
    "InferenceRequest",
    "BatchResult",
    "BatcherStats",
    "RequestPriority",
    # Model registry
    "ModelRegistry",
    "ModelVersion",
    "ModelBenchmark",
    "ABTestConfig",
    "ModelStatus",
    "DeploymentStrategy",
]
