# Request Hedging Pattern for GPU Inference

**Author:** Gopi Krishna Vajrala
**Context:** Netflix Real-Time LLM Personalization & Inference Platform

---

## Overview

Request hedging reduces tail latency (P99, P99.9) by sending redundant requests to multiple GPU inference backends after a configurable delay. The first response wins, and remaining in-flight requests are cancelled. This pattern is particularly effective for GPU inference where tail latency variance is high due to dynamic batching queue depth, KV cache miss patterns, and GPU thermal throttling.

## Problem Statement

GPU inference latency has high variance between P50 and P99:

```
P50:  32ms  (typical request in a well-formed batch)
P90:  55ms  (request in a larger batch or longer sequence)
P99:  78ms  (request queued behind full batch, or KV cache miss)
P99.9: 112ms (GPU thermal throttling, NVLink contention, GC pause)
```

The gap between P50 and P99.9 is 80ms (3.5x). Request hedging can reduce P99.9 to near P50 levels by exploiting the statistical independence of different GPU nodes' queue depths and processing times.

## How It Works

```
Timeline Without Hedging:
  T+0ms:   Send request to GPU Node A
  T+95ms:  GPU Node A responds (tail latency)
  Result:  95ms total latency

Timeline With Hedging (25ms delay):
  T+0ms:   Send request to GPU Node A
  T+25ms:  No response yet -> send hedge to GPU Node B
  T+35ms:  GPU Node B responds (normal latency from B)
  T+35ms:  Cancel request on GPU Node A
  Result:  35ms total latency (63% reduction)

Statistical Model:
  P(both slow) = P(A slow) x P(B slow)
  If P(slow) = 5% (P95), then P(both slow) = 0.25%
  Effective tail latency drops from P95 to ~P99.75
```

## Architecture

```
┌──────────────┐
│   FastAPI     │
│   Gateway     │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────┐
│         Hedging Controller            │
│                                       │
│  1. Send primary request to Pool A    │
│  2. Start hedge timer (25ms)          │
│  3. If no response by timer:          │
│     Send hedge to Pool B              │
│  4. Use first response, cancel other  │
│                                       │
│  Pool Selection:                      │
│  - Round-robin across healthy pools   │
│  - Avoid same physical node           │
│  - Prefer pools with lower queue depth│
└──────┬───────────────┬───────────────┘
       │               │
       ▼               ▼
┌──────────────┐ ┌──────────────┐
│  GPU Pool A   │ │  GPU Pool B   │
│  (Triton 0-3) │ │  (Triton 4-7) │
│  Node 1       │ │  Node 2       │
└──────────────┘ └──────────────┘
```

## Configuration

```python
class HedgingConfig:
    """Configuration for request hedging in GPU inference."""

    # Hedging delay before sending redundant request
    hedge_delay_ms: int = 25

    # Maximum number of hedged requests (including original)
    max_hedged_requests: int = 2

    # Dynamic delay based on recent latency percentiles
    dynamic_delay: bool = True
    dynamic_delay_percentile: float = 0.75  # Hedge at P75

    # Budget: Maximum percentage of requests that can be hedged
    # Prevents hedging from doubling GPU load
    hedge_budget_percent: float = 10.0

    # Only hedge for specific request priorities
    eligible_priorities: list = ["P0", "P1"]  # Interactive requests only

    # Cancel in-flight requests when first response arrives
    cancel_on_first_response: bool = True

    # Do not hedge if GPU utilization exceeds threshold
    max_gpu_utilization_for_hedging: float = 0.85
```

## Dynamic Hedge Delay

The hedge delay is dynamically adjusted based on recent latency observations:

```python
def compute_hedge_delay(recent_latencies: list[float]) -> float:
    """Compute hedge delay as the P75 of recent latencies.

    The delay should be long enough to avoid unnecessary hedging
    (most requests complete before the delay) but short enough
    to catch tail latency cases.

    Target: ~75% of requests complete before hedge fires
    """
    p75 = np.percentile(recent_latencies, 75)
    return max(10, min(p75, 50))  # Clamp between 10ms and 50ms
```

```
Adaptive Delay Behavior:

  Normal load (batch-16, P75=20ms):
    Hedge delay: 20ms
    Hedging rate: ~25% of requests
    GPU overhead: ~2.5% (25% * 10% budget)

  High load (batch-64, P75=35ms):
    Hedge delay: 35ms
    Hedging rate: ~25% of requests
    GPU overhead: ~2.5%

  Degraded state (GPU throttling, P75=60ms):
    Hedge delay: 50ms (capped)
    Hedging rate: ~25% of requests
    GPU overhead: ~2.5%
```

## Budget Management

Hedging increases GPU load because redundant requests consume compute. A budget system prevents hedging from causing the very overload it aims to mitigate:

```
Budget Calculation:
  Max hedged requests per second = Total RPS * hedge_budget_percent / 100
  Example: 50,000 RPS * 10% = 5,000 hedged requests/s

  If hedging rate exceeds budget:
    1. Increase hedge delay (fewer requests qualify)
    2. Restrict hedging to P0 requests only
    3. Disable hedging entirely (circuit breaker)

GPU Utilization Guard:
  If GPU utilization > 85%:
    Hedging is DISABLED (would worsen overload)
  If GPU utilization < 70%:
    Hedging is ENABLED (spare capacity available)
```

## Effectiveness Metrics

| Metric | Without Hedging | With Hedging | Improvement |
|--------|----------------|--------------|-------------|
| P99 Latency | 78ms | 52ms | 33% reduction |
| P99.9 Latency | 112ms | 58ms | 48% reduction |
| P50 Latency | 32ms | 32ms | No change |
| GPU Overhead | 0% | 2-3% | Marginal cost |
| Requests Hedged | 0% | ~8% | Budget-controlled |

## When NOT to Hedge

- GPU utilization > 85% (hedging would worsen the situation)
- Batch (P3) requests (not latency-sensitive)
- Streaming requests (cannot meaningfully hedge mid-stream)
- Model training/fine-tuning workloads
- During active auto-scaling (wait for capacity)

## Observability

```
Metrics:
  hedge_requests_total              # Total hedged requests
  hedge_won_by_primary_total        # Primary response arrived first
  hedge_won_by_secondary_total      # Hedge response arrived first
  hedge_latency_saved_seconds       # Cumulative latency saved
  hedge_budget_utilization_percent  # Current budget consumption
  hedge_cancelled_requests_total    # Successfully cancelled requests

Logging:
  INFO: "Hedged request {id} after {delay}ms, won by {primary|secondary}, saved {saved}ms"
  WARN: "Hedging budget exceeded, reducing hedge rate"
  WARN: "Hedging disabled due to high GPU utilization ({util}%)"
```

---

**References:**

- Jeff Dean, "The Tail at Scale" (Google, 2013)
- Netflix Zuul - Request hedging implementation
- gRPC hedging policy specification
