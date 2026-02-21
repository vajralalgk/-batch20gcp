"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
GPU Resource Management Package
Author: Gopi Krishna Vajrala
============================================================================

WHY THIS PACKAGE EXISTS:
    The GPU package provides comprehensive management of GPU resources for
    the real-time LLM inference platform. At Netflix scale, efficient GPU
    utilization is critical — a single percentage point improvement in GPU
    utilization across the fleet can save millions in infrastructure costs.

MODULES:
    - kv_cache_manager: Token-level KV cache lifecycle with TTL and eviction
    - memory_pool: GPU memory allocation, fragmentation tracking, and OOM prevention
    - sm_monitor: Streaming Multiprocessor occupancy and bottleneck detection
    - device_manager: Multi-GPU coordination with Tensor Parallelism (TP=4)

ARCHITECTURE:
    These modules sit between the inference engine (vLLM/TensorRT-LLM) and
    the physical GPU hardware. They provide:
    1. Predictable latency by preventing memory pressure spikes
    2. Maximized throughput via intelligent cache management
    3. Fleet reliability through health monitoring and automatic failover
    4. Observability for capacity planning and cost optimization

SUPPORTED HARDWARE:
    - NVIDIA A100 80GB (primary inference SKU, SXM4 form factor)
    - NVIDIA A10G 24GB (cost-optimized inference for smaller models)
============================================================================
"""

from src.gpu.kv_cache_manager import KVCacheManager
from src.gpu.memory_pool import GPUMemoryPool
from src.gpu.sm_monitor import SMOccupancyMonitor
from src.gpu.device_manager import GPUDeviceManager

__all__ = [
    "KVCacheManager",
    "GPUMemoryPool",
    "SMOccupancyMonitor",
    "GPUDeviceManager",
]
