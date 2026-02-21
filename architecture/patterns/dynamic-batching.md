# Dynamic Batching Pattern for GPU Inference

**Author:** Gopi Krishna Vajrala
**Context:** Netflix Real-Time LLM Personalization & Inference Platform

---

## Overview

Dynamic batching accumulates individual inference requests into optimally-sized batches before dispatching them to the GPU. This pattern transforms GPU workloads from latency-optimized (single request, low utilization) to throughput-optimized (batch of requests, high utilization), dramatically improving GPU cost efficiency while maintaining acceptable latency through careful queue management.

## Problem Statement

GPU inference has fundamentally different economics than CPU workloads:

```
Single Request (Batch=1):
  GPU Utilization:  15%
  Throughput:       2,500 tokens/s
  Cost per request: $0.0013

Batched Request (Batch=64):
  GPU Utilization:  82%
  Throughput:       25,000 tokens/s (10x improvement)
  Cost per request: $0.00013 (10x cheaper)
```

Without dynamic batching, each GPU processes one request at a time, wasting 85% of its compute capacity. The challenge is forming batches efficiently without adding unacceptable latency.

## How It Works

```
Request Arrival Timeline:
  T+0ms:   Request 1 arrives -> Queue (1/64)
  T+3ms:   Request 2 arrives -> Queue (2/64)
  T+7ms:   Request 3 arrives -> Queue (3/64)
  T+12ms:  Request 4 arrives -> Queue (4/64)
  ...
  T+45ms:  Request 16 arrives -> Queue (16/64)
  T+48ms:  Preferred batch size (16) reached
           -> Dispatch batch to GPU
  T+50ms:  Max queue delay reached (50ms)
           -> Would dispatch any remaining queued requests

Batch Formation Decision:
  IF queue.size >= preferred_batch_size[0]:
    DISPATCH immediately
  ELIF time_in_queue >= max_queue_delay:
    DISPATCH with current queue contents
  ELSE:
    WAIT for more requests
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Dynamic Batcher                        │
│                                                           │
│  ┌─────────────┐     ┌──────────────┐     ┌───────────┐  │
│  │ Request      │     │  Batch       │     │ GPU       │  │
│  │ Queue        │────>│  Former      │────>│ Dispatch  │  │
│  │              │     │              │     │           │  │
│  │ Priority     │     │ Sort by      │     │ Tensor    │  │
│  │ Levels:      │     │ seq length   │     │ Parallel  │  │
│  │  P0 (urgent) │     │              │     │ Execution │  │
│  │  P1 (high)   │     │ Pad to       │     │           │  │
│  │  P2 (medium) │     │ uniform len  │     │ TP=4      │  │
│  │  P3 (low)    │     │              │     │ (4 GPUs)  │  │
│  └─────────────┘     └──────────────┘     └───────────┘  │
│        │                                       │          │
│        │              ┌──────────────┐         │          │
│        │              │ Continuous   │         │          │
│        └─────────────>│ Batching     │<────────┘          │
│         (new requests)│ (In-Flight   │ (completed         │
│                       │  Batching)   │  sequences)        │
│                       └──────────────┘                    │
└──────────────────────────────────────────────────────────┘
```

## Configuration

```
# Triton Dynamic Batching Configuration (config.pbtxt)

dynamic_batching {
  # Maximum time a request waits in queue before batch dispatch
  max_queue_delay_microseconds: 50000   # 50ms

  # Preferred batch sizes (dispatched immediately when reached)
  preferred_batch_size: [8, 16, 32, 64]

  # Maximum batch size (hard limit)
  # max_batch_size: 64   (set at model level)

  # Preserve request ordering within a batch
  preserve_ordering: true

  # Priority levels (lower number = higher priority)
  priority_levels: 3
  default_priority_level: 2

  # Priority-specific queue policies
  priority_queue_policy {
    key: 1   # P0 - Interactive
    value: {
      timeout_action: REJECT
      default_timeout_microseconds: 100000   # 100ms max wait
      max_queue_size: 256
    }
  }
  priority_queue_policy {
    key: 2   # P1 - Standard
    value: {
      timeout_action: REJECT
      default_timeout_microseconds: 200000   # 200ms max wait
      max_queue_size: 512
    }
  }
  priority_queue_policy {
    key: 3   # P2 - Background
    value: {
      timeout_action: REJECT
      default_timeout_microseconds: 1000000  # 1s max wait
      max_queue_size: 1024
    }
  }
}
```

## Batch Formation Strategies

### Strategy 1: Fixed Window Batching

```
Method: Wait fixed duration (e.g., 50ms), then dispatch whatever is queued
Pros:  Simple, predictable latency addition
Cons:  May dispatch small batches during low traffic
Best for: Consistent traffic patterns

Example:
  Traffic: 1,000 req/s
  Window: 50ms
  Average batch size: 50 requests (1,000 * 0.05)
```

### Strategy 2: Size-Triggered Batching

```
Method: Dispatch immediately when batch reaches preferred size
Pros:  No unnecessary waiting during high traffic
Cons:  May wait forever during very low traffic (needs timeout fallback)
Best for: Variable traffic with peaks

Example:
  Preferred size: 32
  At 10,000 req/s: Batch formed in ~3.2ms (minimal added latency)
  At 100 req/s: Batch formed in ~320ms (falls back to timeout)
```

### Strategy 3: Adaptive Batching (Recommended)

```
Method: Combine size-triggered and time-bounded with adaptive thresholds
Rules:
  1. If queue >= preferred_size: dispatch immediately
  2. If queue_wait >= max_delay: dispatch with current contents
  3. Adjust max_delay based on GPU utilization:
     - GPU < 50%: max_delay = 100ms (prioritize batch size)
     - GPU 50-80%: max_delay = 50ms (balanced)
     - GPU > 80%: max_delay = 20ms (prioritize latency)

Pros:  Optimizes for both throughput and latency
Cons:  More complex to tune and debug
Best for: Production inference workloads
```

## Continuous Batching (In-Flight Batching)

For auto-regressive LLM inference, continuous batching significantly improves throughput by allowing new requests to enter a batch while existing requests are still generating tokens:

```
Traditional Static Batching:
  ┌────────────────────────────────────────┐
  │ Batch 1: [Req A (100 tokens), Req B (200 tokens), Req C (50 tokens)] │
  │                                                                       │
  │ T=0:    Start all 3 requests                                          │
  │ T=50:   Req C completes -> GPU idle for C's slot                      │
  │ T=100:  Req A completes -> GPU idle for A's slot                      │
  │ T=200:  Req B completes -> Batch done, process Batch 2                │
  │                                                                       │
  │ GPU Utilization: Starts at 100%, drops to 33% by end                  │
  └────────────────────────────────────────┘

Continuous Batching:
  ┌────────────────────────────────────────┐
  │ T=0:    Start [Req A, Req B, Req C]                                   │
  │ T=50:   Req C completes -> IMMEDIATELY add Req D to batch             │
  │ T=100:  Req A completes -> IMMEDIATELY add Req E to batch             │
  │ T=150:  Req D completes -> IMMEDIATELY add Req F to batch             │
  │ ...                                                                   │
  │                                                                       │
  │ GPU Utilization: Maintains ~90%+ continuously                         │
  └────────────────────────────────────────┘
```

## Padding Efficiency

Requests within a batch may have different sequence lengths, requiring padding to the longest sequence:

```
Naive Padding (Pad to Max):
  Batch: [128 tokens, 256 tokens, 512 tokens, 1024 tokens]
  All padded to 1024 tokens
  Wasted compute: (1024-128) + (1024-256) + (1024-512) + 0 = 2,176 tokens
  Efficiency: 1,920 / (4 * 1024) = 47%

Bucketed Batching:
  Bucket 1 (128): [128 tokens] -> pad to 128
  Bucket 2 (256): [256 tokens] -> pad to 256
  Bucket 3 (512): [512 tokens] -> pad to 512
  Bucket 4 (1024): [1024 tokens] -> pad to 1024
  Efficiency: 1,920 / (128 + 256 + 512 + 1024) = 100%

  Trade-off: Smaller batches per bucket, potentially lower GPU utilization
  Solution: Accumulate requests across multiple queue windows to fill buckets
```

## Latency vs Throughput Tradeoff

```
                    Throughput
                    (tokens/s)
                         │
  25,000 ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ *  (batch=64)
                         │                            *
  20,000 ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  *
                         │                       *
  15,000 ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ *
                         │                *
  10,000 ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ *
                         │          *
   5,000 ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ *
                         │   *
   2,500 ─ ─ ─ ─ ─ ─ ─ ─│*
                         └──────────────────────────────────
                         1    4    8    16   32   48   64
                                   Batch Size

  Added Latency vs Batch Size:
  Batch  1:  +0ms   (no batching overhead)
  Batch  8:  +5ms   (queue wait + padding)
  Batch 16:  +12ms  (queue wait + padding)
  Batch 32:  +25ms  (queue wait + padding)
  Batch 64:  +45ms  (queue wait + padding)

  Sweet Spot: Batch 32
    Throughput: 22,000 tokens/s (88% of max)
    Added Latency: +25ms (within budget)
    GPU Utilization: 74%
```

## Metrics

```
Prometheus Metrics:
  triton_batch_size_histogram          # Distribution of actual batch sizes
  triton_queue_depth_gauge             # Current queue depth
  triton_queue_wait_seconds_histogram  # Time spent in queue
  triton_batch_padding_efficiency      # (useful tokens) / (total tokens in batch)
  triton_continuous_batch_inflight     # Active sequences in continuous batch
  triton_batch_dispatch_rate           # Batches dispatched per second
  triton_request_timeout_total         # Requests that exceeded queue timeout

Dashboard Panels:
  1. Batch Size Distribution (histogram over time)
  2. Queue Depth vs GPU Utilization (correlation)
  3. Padding Efficiency (target > 80%)
  4. Queue Wait Time Percentiles (P50, P90, P99)
  5. Batch Dispatch Rate vs Request Arrival Rate
```

## Tuning Guide

| Traffic Level | Recommended Config |
|--------------|-------------------|
| Low (< 100 req/s) | max_delay=100ms, preferred=[4,8,16], continuous_batching=off |
| Medium (100-1000 req/s) | max_delay=50ms, preferred=[8,16,32], continuous_batching=on |
| High (1000-10000 req/s) | max_delay=30ms, preferred=[16,32,64], continuous_batching=on |
| Very High (> 10000 req/s) | max_delay=20ms, preferred=[32,64], continuous_batching=on |

---

**References:**

- NVIDIA Triton Dynamic Batching documentation
- Orca: A Distributed Serving System for Transformer-Based Generative Models (continuous batching)
- vLLM: Efficient Memory Management for Large Language Model Serving (PagedAttention)
