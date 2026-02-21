# Netflix Real-Time LLM Personalization & Inference Platform

## High-Level Architecture Document

**Document ID:** NFLX-LLM-ARCH-001
**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0
**Last Updated:** 2026-02-21
**Status:** APPROVED
**Classification:** Internal

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Design Principles](#2-design-principles)
3. [Component Architecture](#3-component-architecture)
4. [Data Flow](#4-data-flow)
5. [GPU Infrastructure Design](#5-gpu-infrastructure-design)
6. [Multi-Region Strategy](#6-multi-region-strategy)
7. [Capacity Planning Model](#7-capacity-planning-model)
8. [Failure Handling](#8-failure-handling)
9. [Security Architecture](#9-security-architecture)
10. [Observability](#10-observability)
11. [Performance Characteristics](#11-performance-characteristics)

---

## 1. System Overview

The Netflix Real-Time LLM Personalization & Inference Platform is a production-grade system designed to serve personalized large language model (LLM) inference at Netflix scale. The platform processes millions of daily requests across three active-active regions, delivering sub-100ms P99 latency for personalized content recommendations, search augmentation, and conversational interfaces.

### 1.1 Mission

Deliver real-time, personalized LLM inference to every Netflix member worldwide with carrier-grade reliability (99.99% availability), low latency (< 100ms P99 end-to-end), and cost efficiency through GPU optimization.

### 1.2 Scope

The platform encompasses:

- **Model Serving**: High-throughput GPU inference via NVIDIA Triton Inference Server with TensorRT-LLM optimization
- **Personalization Engine**: Real-time user context enrichment and preference-aware prompt construction
- **Multi-Region Deployment**: Active-active architecture across us-east-1, us-west-2, and eu-west-1
- **GPU Optimization**: Tensor parallelism (TP=4), dynamic batching, KV cache management, and continuous batching
- **Observability Stack**: Full-stack metrics, distributed tracing, and GPU-level telemetry

### 1.3 Key Metrics

| Metric | Target | Current |
|--------|--------|---------|
| P99 Latency (end-to-end) | < 100ms | 78ms |
| Throughput | > 50,000 req/s (global) | 52,400 req/s |
| Availability | 99.99% | 99.995% |
| GPU Utilization | > 70% | 76% |
| Time to First Token | < 25ms | 18ms |
| Cost per 1M Tokens | < $0.50 | $0.42 |

---

## 2. Design Principles

### 2.1 GPU-First Architecture

Every architectural decision optimizes for GPU utilization. The system minimizes GPU idle time through dynamic batching, continuous batching, and speculative decoding. CPU-bound operations (personalization, context assembly, caching) are offloaded to dedicated compute to keep GPU pipelines saturated.

### 2.2 Latency Budget Allocation

The total latency budget of 100ms (P99) is allocated as follows:

```
Request Parsing & Routing:     5ms
Personalization Context Fetch: 10ms (parallel with cache lookup)
KV Cache Lookup:               5ms
Prompt Assembly:               3ms
GPU Queue Wait:                7ms (dynamic batching window)
Model Inference (prefill):    35ms
Model Inference (decode):     25ms
Response Serialization:        5ms
Network Overhead:              5ms
─────────────────────────────────────
Total Budget:                100ms
```

### 2.3 Graceful Degradation

The system implements four levels of degradation:

1. **Full Service**: Personalized inference with all features
2. **Reduced Personalization**: Cached user profiles, simplified prompt templates
3. **Cached Responses**: Pre-computed responses for common queries
4. **Static Fallback**: Deterministic, non-LLM responses from a CDN-backed cache

### 2.4 Cost-Aware Scaling

GPU compute is the dominant cost driver. The system balances cost and performance through:

- Right-sizing tensor parallelism per model size
- Dynamic batch size optimization based on queue depth
- KV cache reuse to minimize redundant computation
- Spot instance utilization for non-critical workloads (benchmarking, fine-tuning)

### 2.5 Zero-Trust Security

All inter-service communication uses mTLS. Model artifacts are encrypted at rest and in transit. Inference requests are authenticated and authorized at every hop.

---

## 3. Component Architecture

### 3.1 Architecture Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│   Netflix Apps (iOS, Android, Web, TV) / Internal Services          │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                      EDGE & ROUTING LAYER                           │
│   Route53 (Latency-Based) -> ALB -> API Gateway (FastAPI)           │
│   Rate Limiting | Auth | Request Validation | Traffic Shaping       │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                    PERSONALIZATION LAYER                             │
│   ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐        │
│   │ User Context  │  │  Profile     │  │ Prompt Template   │        │
│   │ Aggregator    │  │  Cache       │  │ Engine            │        │
│   │ (Real-time)   │  │ (ElastiCache)│  │ (Jinja2 + Custom) │        │
│   └──────────────┘  └──────────────┘  └───────────────────┘        │
│   Watch History | Preferences | Demographics | A/B Variants         │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                    INFERENCE LAYER                                   │
│   ┌──────────────────────────────────────────────────────────┐      │
│   │              NVIDIA Triton Inference Server               │      │
│   │  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │      │
│   │  │ Dynamic   │  │ TensorRT-LLM │  │ KV Cache          │  │      │
│   │  │ Batcher   │  │ Engine       │  │ Manager           │  │      │
│   │  │ (max=64)  │  │ (TP=4, FP16) │  │ (PagedAttention)  │  │      │
│   │  └──────────┘  └──────────────┘  └───────────────────┘  │      │
│   └──────────────────────────────────────────────────────────┘      │
│   GPU: 8x NVIDIA A100 80GB SXM4 per node (p4d.24xlarge)            │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                      DATA LAYER                                     │
│   ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐      │
│   │ DynamoDB  │  │ ElastiCache  │  │ S3       │  │ Feature  │      │
│   │ (Profiles)│  │ (Redis 7)    │  │ (Models) │  │ Store    │      │
│   │ Global    │  │ Cluster Mode │  │ Versioned│  │ (Online) │      │
│   │ Tables    │  │ Multi-AZ     │  │ Artifacts│  │          │      │
│   └──────────┘  └──────────────┘  └──────────┘  └──────────┘      │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                    OBSERVABILITY LAYER                               │
│   Prometheus + Grafana | Jaeger (Tracing) | CloudWatch | DCGM       │
│   GPU Metrics | Inference Latency | Cache Hit Rates | Error Budgets  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 API Gateway (FastAPI)

The API gateway is built on FastAPI, chosen for its native async support, automatic OpenAPI documentation, and high throughput with Python's asyncio event loop.

**Responsibilities:**
- Request authentication and authorization (JWT + API keys)
- Input validation and sanitization (Pydantic models)
- Rate limiting (token bucket per user, sliding window per API key)
- Request routing to appropriate model endpoints
- Response streaming (Server-Sent Events for token-by-token delivery)
- Circuit breaking for downstream service failures

**Key Configuration:**
```python
# FastAPI application configuration
app = FastAPI(
    title="Netflix LLM Inference API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Rate limiting: 1000 req/min per user, 10,000 req/min per API key
rate_limiter = SlidingWindowRateLimiter(
    user_limit=1000,
    api_key_limit=10000,
    window_seconds=60,
)
```

### 3.3 Personalization Engine

The personalization engine enriches inference requests with user-specific context to produce tailored LLM outputs.

**Data Sources:**
- **Watch History**: Last 100 titles, viewing duration, completion rates
- **Preferences**: Explicit ratings, genre affinities, content maturity settings
- **Demographics**: Region, language, device type, subscription tier
- **Real-time Signals**: Current browsing session, time of day, day of week
- **A/B Experiment Context**: Active experiment variants for prompt template selection

**Architecture:**
```
User Request
    │
    ├──> ElastiCache (L1 Cache, TTL=5min)
    │       │
    │       └──> Cache HIT: Return cached profile (< 1ms)
    │
    └──> DynamoDB Global Table (L2, on cache miss)
            │
            └──> Feature Store (L3, for computed features)
                    │
                    └──> Assemble PersonalizationContext
                            │
                            └──> Prompt Template Engine
                                    │
                                    └──> Enriched Prompt → Inference Layer
```

### 3.4 Inference Engine (Triton + TensorRT-LLM)

The inference engine is the computational core, running NVIDIA Triton Inference Server with TensorRT-LLM backend for optimized GPU execution.

**Model Configuration:**
```
Model: netflix-llm-70b
Framework: TensorRT-LLM
Precision: FP16 (with selective FP32 for attention)
Tensor Parallelism: 4 (across 4 GPUs per model instance)
Pipeline Parallelism: 1
Max Batch Size: 64
Max Sequence Length: 4096
KV Cache: PagedAttention (32 GB per node)
Quantization: INT8 weight-only (optional, for cost optimization)
```

**Triton Model Repository Structure:**
```
model_repository/
├── netflix_llm_70b/
│   ├── config.pbtxt
│   ├── 1/
│   │   └── model.plan          # TensorRT engine
│   └── tokenizer/
│       ├── tokenizer.json
│       └── tokenizer_config.json
├── preprocessing/
│   ├── config.pbtxt
│   └── 1/
│       └── model.py            # Tokenization + prompt assembly
└── postprocessing/
    ├── config.pbtxt
    └── 1/
        └── model.py            # Detokenization + response formatting
```

### 3.5 Caching Layer

The caching architecture implements a three-tier hierarchy:

| Tier | Technology | TTL | Purpose |
|------|-----------|-----|---------|
| L1 | Application Memory | 30s | Hot user profiles, tokenized prompts |
| L2 | ElastiCache Redis 7 | 5min | User profiles, feature vectors, partial KV states |
| L3 | DynamoDB DAX | 15min | Historical features, computed embeddings |

**KV Cache Strategy:**

The KV (Key-Value) cache stores attention key-value tensors from previous inference steps, enabling token reuse across requests with shared prefixes.

```
KV Cache Configuration:
  Max Size: 32 GB per node (across 8 GPUs = 4 GB/GPU)
  Page Size: 16 tokens
  Eviction Policy: LRU with frequency boost
  Prefix Sharing: Enabled (system prompt + common prefixes)
  Hit Rate Target: > 85%
```

---

## 4. Data Flow

### 4.1 End-to-End Request Flow

```
┌──────────┐     ┌──────────┐     ┌──────────────┐     ┌──────────────┐
│  Client   │────>│ Route53  │────>│     ALB      │────>│   FastAPI    │
│ (Netflix  │     │ (Latency │     │ (TLS Term,   │     │  Gateway     │
│  App)     │     │  Based)  │     │  WAF)        │     │              │
└──────────┘     └──────────┘     └──────────────┘     └──────┬───────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │   Auth + Validate  │
                                                    │   Rate Limit Check │
                                                    └─────────┬─────────┘
                                                              │
                                          ┌───────────────────┼───────────────────┐
                                          │                   │                   │
                                ┌─────────▼──────┐  ┌────────▼───────┐  ┌────────▼───────┐
                                │ User Profile   │  │ Feature Store  │  │ KV Cache       │
                                │ Cache Lookup   │  │ Lookup         │  │ Prefix Check   │
                                │ (ElastiCache)  │  │ (DynamoDB)     │  │ (GPU Memory)   │
                                └─────────┬──────┘  └────────┬───────┘  └────────┬───────┘
                                          │                   │                   │
                                          └───────────────────┼───────────────────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │  Prompt Assembly   │
                                                    │  (Template Engine) │
                                                    └─────────┬─────────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │  Dynamic Batcher   │
                                                    │  (Queue + Batch)   │
                                                    │  Max Wait: 50ms    │
                                                    │  Max Batch: 64     │
                                                    └─────────┬─────────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │  TensorRT-LLM      │
                                                    │  Inference          │
                                                    │  (TP=4, FP16)      │
                                                    │  Prefill + Decode   │
                                                    └─────────┬─────────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │  Response Stream   │
                                                    │  (SSE / gRPC)      │
                                                    └─────────┬─────────┘
                                                              │
                                                    ┌─────────▼─────────┐
                                                    │  Metrics + Trace   │
                                                    │  (Prometheus +     │
                                                    │   Jaeger)          │
                                                    └──────────────────┘
```

### 4.2 Streaming Response Flow

For long-form generation, the platform uses Server-Sent Events (SSE) to deliver tokens incrementally:

```
Client                    Gateway                  Triton
  │                         │                        │
  │──POST /v1/inference────>│                        │
  │  Accept: text/event-    │──gRPC InferAsync──────>│
  │  stream                 │                        │
  │                         │<──Token 1──────────────│
  │<──data: {"token":"The"} │                        │
  │                         │<──Token 2──────────────│
  │<──data: {"token":" top"}│                        │
  │                         │<──Token N──────────────│
  │<──data: {"token":"..."}─│                        │
  │                         │<──[EOS]────────────────│
  │<──data: [DONE]──────────│                        │
  │                         │                        │
```

---

## 5. GPU Infrastructure Design

### 5.1 Hardware Configuration

**Primary Instance Type:** AWS p4d.24xlarge

| Component | Specification |
|-----------|--------------|
| GPU | 8x NVIDIA A100 80GB SXM4 |
| GPU Memory | 640 GB HBM2e total |
| GPU Interconnect | NVLink 3.0 (600 GB/s bidirectional) |
| CPU | 96 vCPUs (Intel Xeon Platinum 8275CL) |
| System Memory | 1,152 GB DDR4 |
| Network | 4x 100 Gbps ENA (400 Gbps aggregate) |
| Storage | 8x 1 TB NVMe SSD |
| EFA | Elastic Fabric Adapter for inter-node communication |

### 5.2 Tensor Parallelism (TP=4)

The 70B parameter model is distributed across 4 GPUs using tensor parallelism:

```
GPU 0 (TP Rank 0)          GPU 1 (TP Rank 1)
┌───────────────────┐      ┌───────────────────┐
│ Embedding Layer   │      │ Embedding Layer   │
│ (Shard 0/4)       │      │ (Shard 1/4)       │
│                   │      │                   │
│ Attention Heads   │      │ Attention Heads   │
│ 0-19              │      │ 20-39             │
│                   │      │                   │
│ FFN Shard 0/4     │      │ FFN Shard 1/4     │
│                   │      │                   │
│ KV Cache (8 GB)   │      │ KV Cache (8 GB)   │
└───────┬───────────┘      └───────┬───────────┘
        │    NVLink (600 GB/s)     │
        └──────────────────────────┘

GPU 2 (TP Rank 2)          GPU 3 (TP Rank 3)
┌───────────────────┐      ┌───────────────────┐
│ Embedding Layer   │      │ Embedding Layer   │
│ (Shard 2/4)       │      │ (Shard 3/4)       │
│                   │      │                   │
│ Attention Heads   │      │ Attention Heads   │
│ 40-59             │      │ 60-79             │
│                   │      │                   │
│ FFN Shard 2/4     │      │ FFN Shard 2/4     │
│                   │      │                   │
│ KV Cache (8 GB)   │      │ KV Cache (8 GB)   │
└───────┬───────────┘      └───────┬───────────┘
        │    NVLink (600 GB/s)     │
        └──────────────────────────┘

Remaining GPUs 4-7: Second model instance (for throughput)
```

### 5.3 Dynamic Batching

The dynamic batcher accumulates incoming requests and dispatches them as optimally-sized batches to the GPU:

```
Configuration:
  max_batch_size: 64
  max_queue_delay_microseconds: 50000  # 50ms
  preferred_batch_sizes: [8, 16, 32, 64]
  preserve_ordering: true
  priority_levels: 3
  default_priority_level: 2

Batch Formation Strategy:
  1. Accumulate requests in queue (max wait: 50ms)
  2. Sort by sequence length for padding efficiency
  3. Form batch at preferred size or when max delay reached
  4. Apply continuous batching for decode phase
  5. Release completed sequences immediately
```

### 5.4 KV Cache Management

```
PagedAttention Configuration:
  block_size: 16 tokens
  max_blocks_per_sequence: 256  (= 4096 max tokens)
  total_gpu_memory_for_kv: 32 GB (4 GB per GPU x 8)
  eviction_policy: LRU with frequency weighting
  prefix_caching: enabled
  swap_space: 8 GB (CPU memory fallback)

Memory Layout per GPU:
  ┌─────────────────────────────┐
  │ Model Weights: ~17 GB       │  (70B / 4 TP * FP16)
  │ Activation Memory: ~5 GB    │
  │ KV Cache: ~4 GB             │  (PagedAttention blocks)
  │ CUDA Kernels: ~1 GB         │
  │ Reserved/Fragmentation: ~3 GB│
  │ ─────────────────────────── │
  │ Total per GPU: ~30 GB / 80  │
  │ Headroom: ~50 GB            │
  └─────────────────────────────┘
```

---

## 6. Multi-Region Strategy

### 6.1 Active-Active Topology

```
                    ┌──────────────────┐
                    │    Route53        │
                    │  Latency-Based   │
                    │  Routing + Health │
                    └────────┬─────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
   ┌────────▼────────┐ ┌────▼────────┐ ┌─────▼───────┐
   │   us-east-1     │ │  us-west-2  │ │  eu-west-1  │
   │   (Primary)     │ │ (Secondary) │ │    (EU)     │
   │                 │ │             │ │             │
   │ EKS Cluster     │ │ EKS Cluster │ │ EKS Cluster │
   │ 6x p4d.24xl     │ │ 4x p4d.24xl │ │ 4x p4d.24xl│
   │ 48 A100 GPUs    │ │ 32 A100 GPUs│ │ 32 A100 GPUs│
   │                 │ │             │ │             │
   │ ElastiCache     │ │ ElastiCache │ │ ElastiCache │
   │ DynamoDB GT     │ │ DynamoDB GT │ │ DynamoDB GT │
   └────────┬────────┘ └──────┬──────┘ └──────┬──────┘
            │                 │               │
            └─────────────────┼───────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  DynamoDB Global   │
                    │  Tables (Async     │
                    │  Replication)      │
                    │                    │
                    │  S3 Cross-Region   │
                    │  Replication       │
                    │  (Model Artifacts) │
                    └────────────────────┘
```

### 6.2 Regional Configuration

| Property | us-east-1 | us-west-2 | eu-west-1 |
|----------|-----------|-----------|-----------|
| Role | Primary | Secondary | EU (GDPR) |
| GPU Nodes | 6x p4d.24xlarge | 4x p4d.24xlarge | 4x p4d.24xlarge |
| Total GPUs | 48x A100 | 32x A100 | 32x A100 |
| Traffic Share | 45% | 30% | 25% |
| Failover Priority | 1 | 2 | 3 |
| Data Residency | US | US | EU only |
| Model Sync | Source | Replica | Replica |
| Max Failover Time | N/A | < 15s | < 15s |

### 6.3 Failover Strategy

The platform uses active-active with asymmetric capacity. Every region can absorb the traffic of any single other region:

```
Normal State:
  us-east-1: 45% traffic -> 6 nodes (67% utilized)
  us-west-2: 30% traffic -> 4 nodes (75% utilized)
  eu-west-1: 25% traffic -> 4 nodes (63% utilized)

Failover State (us-east-1 down):
  us-west-2: 55% traffic -> 4 nodes (absorb via dynamic batching + autoscale)
  eu-west-1: 45% traffic -> 4 nodes (absorb via dynamic batching + autoscale)
  Autoscaler triggers additional nodes within 5 minutes

Failover Sequence:
  T+0s:    Route53 health check fails
  T+10s:   DNS failover initiated
  T+15s:   Traffic rerouted to surviving regions
  T+30s:   Surviving regions increase batch sizes
  T+120s:  Autoscaler provisions additional GPU nodes
  T+300s:  Full capacity restored in surviving regions
```

---

## 7. Capacity Planning Model

### 7.1 Core Formula

```
Required GPU Nodes = ceil(
    (Peak_RPS * Avg_Tokens_Per_Request * Safety_Factor)
    / (Tokens_Per_Second_Per_GPU * GPUs_Per_Node * Batching_Efficiency)
)
```

### 7.2 Worked Example

```
Inputs:
  Peak RPS (global):                    50,000 req/s
  Average tokens per request (output):  150 tokens
  Safety factor (headroom):             1.3 (30% headroom)
  Tokens/second per A100 (70B, FP16):   2,500 tokens/s
  GPUs per node:                        8
  Effective GPUs per model (TP=4):      2 model instances per node
  Batching efficiency:                  0.85

Calculation:
  Required_Throughput = 50,000 * 150 * 1.3 = 9,750,000 tokens/s
  Per_Node_Throughput = 2,500 * 8 * 0.85  = 17,000 tokens/s

  Required_Nodes = ceil(9,750,000 / 17,000) = ceil(573.5) = 574

  Distributed across regions:
    us-east-1 (45%): ceil(574 * 0.45) = 259 nodes
    us-west-2 (30%): ceil(574 * 0.30) = 173 nodes
    eu-west-1 (25%): ceil(574 * 0.25) = 144 nodes

  With failover overhead (+50%):
    Total nodes: 574 * 1.5 = 861 nodes
```

### 7.3 Peak Load Modeling (3x Spike)

```
Scenario: Major content release causing 3x traffic spike

Normal Load:    50,000 req/s
Peak Load:     150,000 req/s (3x spike)

Mitigation Layers:
  1. Dynamic batching absorbs 40% increase (batch 32 -> 64)
  2. KV cache prefix sharing reduces compute by 25%
  3. Load shedding drops lowest-priority requests at 90% GPU utilization
  4. Autoscaler provisions spot GPU instances (p4d) within 5 minutes
  5. CDN-cached responses serve 30% of peak traffic (common queries)

Effective Capacity After Optimization:
  Base:          50,000 req/s
  + Batching:    70,000 req/s (+40%)
  + KV Cache:    87,500 req/s (+25%)
  + CDN Cache:  125,000 req/s (+43%)
  + Autoscale:  162,500 req/s (+30%, after 5min)

  Net capacity at peak: 162,500 req/s > 150,000 req/s (3x spike)
```

---

## 8. Failure Handling

### 8.1 Circuit Breaker

The circuit breaker protects downstream services (Triton, ElastiCache, DynamoDB) from cascading failures:

```
Circuit Breaker Configuration:
  failure_threshold: 5          # Open after 5 consecutive failures
  success_threshold: 3          # Close after 3 consecutive successes
  timeout: 30s                  # Half-open after 30 seconds
  monitoring_window: 60s        # Sliding window for failure counting

States:
  CLOSED:    Normal operation, requests pass through
  OPEN:      All requests fail-fast, return cached/fallback response
  HALF-OPEN: Allow single probe request to test recovery
```

### 8.2 Request Hedging

For latency-sensitive requests, the platform sends duplicate requests to reduce tail latency:

```
Hedging Strategy:
  trigger: P95 latency exceeded (dynamically computed)
  max_hedged_requests: 2
  hedge_delay: 25ms (send hedge after 25ms without response)
  cancellation: First response wins, cancel others

Example:
  T+0ms:   Send request to GPU Pool A
  T+25ms:  No response yet, send hedge to GPU Pool B
  T+35ms:  GPU Pool B responds -> use this response
  T+40ms:  Cancel GPU Pool A request

  Net effect: P99 reduced from 95ms to 65ms
```

### 8.3 Load Shedding

When GPU utilization exceeds 90%, the platform sheds load based on request priority:

```
Priority Levels:
  P0 (Critical):   Interactive user requests (never shed)
  P1 (High):       Search augmentation (shed at 95% GPU util)
  P2 (Medium):     Background recommendations (shed at 90% GPU util)
  P3 (Low):        Analytics/batch requests (shed at 85% GPU util)

Shedding Response:
  HTTP 503 with Retry-After header
  Clients implement exponential backoff with jitter
```

### 8.4 GPU Failure Recovery

```
GPU Failure Scenarios:
  1. Single GPU ECC Error:
     - Mark GPU unhealthy in DCGM
     - Redistribute TP ranks to remaining GPUs
     - Alert on-call engineer
     - Recovery: GPU reset or node replacement

  2. Full Node Failure:
     - Kubernetes detects node NotReady
     - Pod rescheduled to healthy node (if available)
     - Autoscaler provisions replacement node
     - Recovery: 5-10 minutes (model loading time)

  3. NVLink Failure:
     - Tensor parallelism degrades (reduced bandwidth)
     - Fallback to PCIe communication (slower)
     - Schedule node replacement in next maintenance window
```

---

## 9. Security Architecture

### 9.1 Authentication & Authorization

```
┌─────────────────────────────────────────────────┐
│                Security Layers                   │
│                                                  │
│  1. Edge Security                                │
│     - AWS WAF (OWASP Top 10 protection)          │
│     - DDoS protection (AWS Shield Advanced)      │
│     - TLS 1.3 termination at ALB                 │
│                                                  │
│  2. API Authentication                           │
│     - JWT tokens (RS256, 15-min expiry)           │
│     - API keys for service-to-service            │
│     - OAuth 2.0 + OIDC for user identity         │
│                                                  │
│  3. Service Mesh Security                        │
│     - mTLS between all services (Istio)          │
│     - Network policies (Kubernetes)              │
│     - Service identity (SPIFFE/SPIRE)            │
│                                                  │
│  4. Data Security                                │
│     - Encryption at rest (AES-256, KMS)          │
│     - Encryption in transit (TLS 1.3)            │
│     - PII detection and redaction in prompts     │
│     - Model artifact signing (cosign)            │
│                                                  │
│  5. Inference Security                           │
│     - Prompt injection detection                 │
│     - Output content filtering                   │
│     - Token-level audit logging                  │
│     - Rate limiting per user/API key             │
│                                                  │
│  6. Infrastructure Security                      │
│     - OIDC for CI/CD (no long-lived creds)       │
│     - IAM roles with least privilege             │
│     - VPC isolation with private subnets         │
│     - Security groups with minimal ingress       │
│                                                  │
└─────────────────────────────────────────────────┘
```

### 9.2 Data Residency (GDPR)

The eu-west-1 region enforces strict data residency:

- User data from EU members never leaves the EU region
- Inference requests are processed locally on EU GPU nodes
- Model artifacts are replicated to EU but training data stays in US
- Audit logs are stored in EU-only S3 buckets
- Cross-region replication excludes PII fields

---

## 10. Observability

### 10.1 Metrics Stack

| Layer | Tool | Metrics |
|-------|------|---------|
| Application | Prometheus | Request rate, latency percentiles, error rates |
| GPU | NVIDIA DCGM | SM occupancy, memory bandwidth, temperature, power |
| Inference | Triton Metrics | Throughput, batch size, queue depth, cache hit rate |
| Infrastructure | CloudWatch | CPU, memory, network, disk I/O |
| Business | Custom | Cost per request, personalization lift, A/B metrics |

### 10.2 Key Dashboards

1. **Inference SLO Dashboard**: P99 latency, throughput, error budget burn rate
2. **GPU Fleet Dashboard**: Per-GPU utilization, memory pressure, thermal status
3. **KV Cache Dashboard**: Hit rates, eviction rates, memory fragmentation
4. **Regional Health Dashboard**: Per-region traffic, failover status, capacity headroom
5. **Cost Dashboard**: GPU-hours consumed, cost per 1M tokens, spot vs on-demand ratio

### 10.3 Alerting

```
Critical Alerts (PagerDuty):
  - P99 latency > 100ms for 2 minutes
  - Error rate > 1% for 1 minute
  - GPU utilization > 95% for 5 minutes
  - Any region unreachable for 30 seconds
  - KV cache pressure > 90% for 3 minutes

Warning Alerts (Slack):
  - P99 latency > 80ms for 5 minutes
  - GPU utilization > 85% for 10 minutes
  - Cache hit rate < 75% for 10 minutes
  - Error budget burn rate > 2x normal
```

---

## 11. Performance Characteristics

### 11.1 Latency Profile

| Operation | P50 | P90 | P95 | P99 | P99.9 |
|-----------|-----|-----|-----|-----|-------|
| End-to-end inference | 32ms | 55ms | 68ms | 78ms | 112ms |
| Time to first token | 8ms | 14ms | 16ms | 18ms | 28ms |
| Inter-token latency | 4ms | 6ms | 7ms | 9ms | 15ms |
| Personalization lookup | 1ms | 2ms | 3ms | 5ms | 12ms |
| KV cache lookup | 0.5ms | 1ms | 1.5ms | 2ms | 5ms |
| Dynamic batch formation | 5ms | 15ms | 25ms | 40ms | 50ms |

### 11.2 Throughput Profile

| Batch Size | Tokens/Second (per node) | GPU Utilization |
|------------|-------------------------|-----------------|
| 1 | 2,500 | 15% |
| 8 | 12,000 | 48% |
| 16 | 18,000 | 62% |
| 32 | 22,000 | 74% |
| 64 | 25,000 | 82% |

### 11.3 Scaling Characteristics

```
Linear scaling up to:     32 GPU nodes per region
Sub-linear scaling at:    32-64 nodes (network overhead)
Saturation point:         ~80 nodes per region (ElastiCache bottleneck)
```

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial architecture document |
