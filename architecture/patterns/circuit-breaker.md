# Circuit Breaker Pattern for GPU Inference

**Author:** Gopi Krishna Vajrala
**Context:** Netflix Real-Time LLM Personalization & Inference Platform

---

## Overview

The circuit breaker pattern prevents cascading failures in the inference pipeline by detecting downstream service degradation and failing fast rather than waiting for timeouts. In a GPU inference context, circuit breakers protect against Triton Inference Server overload, ElastiCache failures, DynamoDB throttling, and inter-region communication breakdowns.

## Problem Statement

GPU inference calls to Triton take 30-60ms under normal conditions. When Triton becomes overloaded or unresponsive, requests queue up, consuming thread pool resources in the FastAPI gateway. Without a circuit breaker, a single Triton node failure can cascade to exhaust the gateway's connection pool, causing the entire region to become unresponsive even for requests that could be served by healthy Triton nodes.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   FastAPI     │     │  Circuit Breaker  │     │  Triton Inference │
│   Gateway     │────>│                   │────>│  Server           │
│               │     │  States:          │     │                   │
│               │     │  CLOSED -> OPEN   │     │  (GPU Cluster)    │
│               │<────│  OPEN -> HALF_OPEN│<────│                   │
│               │     │  HALF_OPEN->CLOSED│     │                   │
└──────────────┘     └──────────────────┘     └──────────────────┘
                              │
                              │ (when OPEN)
                              ▼
                     ┌──────────────────┐
                     │  Fallback Layer   │
                     │                   │
                     │  1. Cached response│
                     │  2. Degraded model │
                     │  3. Static response│
                     └──────────────────┘
```

## State Machine

```
         ┌─────────────┐
         │   CLOSED     │ <── Normal operation
         │              │     Requests pass through
         └──────┬───────┘
                │ failure_count >= threshold (5)
                ▼
         ┌─────────────┐
         │    OPEN      │ <── Fail-fast mode
         │              │     All requests return fallback
         └──────┬───────┘
                │ timeout expires (30s)
                ▼
         ┌─────────────┐
         │  HALF-OPEN   │ <── Probe mode
         │              │     Allow 1 request through
         └──────┬───────┘
               / \
              /   \
         success   failure
            │         │
            ▼         ▼
         CLOSED     OPEN
```

## Configuration

```python
class InferenceCircuitBreaker:
    """Circuit breaker for GPU inference backends."""

    def __init__(self):
        self.failure_threshold = 5          # Open after 5 consecutive failures
        self.success_threshold = 3          # Close after 3 consecutive successes in half-open
        self.timeout = 30                   # Seconds before transitioning OPEN -> HALF-OPEN
        self.monitoring_window = 60         # Sliding window for failure rate calculation
        self.half_open_max_requests = 1     # Probe requests in half-open state

        # GPU-specific thresholds
        self.latency_threshold_ms = 150     # Treat slow responses as failures
        self.gpu_oom_auto_open = True       # Immediately open on GPU OOM errors
        self.triton_unavailable_auto_open = True  # Immediately open on Triton down
```

## Protected Services

| Service | Circuit Breaker | Failure Threshold | Timeout | Fallback |
|---------|----------------|-------------------|---------|----------|
| Triton Inference | Per-node breaker | 5 failures | 30s | Route to other Triton nodes |
| ElastiCache | Per-cluster breaker | 3 failures | 15s | Fetch from DynamoDB directly |
| DynamoDB | Per-table breaker | 5 failures | 30s | Use cached profile or default |
| Feature Store | Per-endpoint breaker | 3 failures | 20s | Use stale features |
| Cross-Region | Per-region breaker | 3 failures | 60s | Route53 handles failover |

## Fallback Strategy

When the circuit is OPEN, the fallback layer provides degraded but functional responses:

1. **Level 1 - Cached Response:** Return the most recent cached response for this user/query pattern from ElastiCache
2. **Level 2 - Degraded Model:** Route to a smaller model (7B instead of 70B) on CPU or available GPU
3. **Level 3 - Static Response:** Return a pre-computed, non-personalized response from CDN cache
4. **Level 4 - Graceful Error:** Return HTTP 503 with Retry-After header and estimated recovery time

## Metrics

```
circuit_breaker_state{service="triton",node="0"}     # 0=CLOSED, 1=OPEN, 2=HALF_OPEN
circuit_breaker_failures_total{service="triton"}      # Cumulative failure count
circuit_breaker_fallback_total{service="triton"}      # Fallback invocation count
circuit_breaker_recovery_time_seconds{service="triton"} # Time spent in OPEN state
```

## GPU-Specific Considerations

- **GPU OOM errors** immediately open the circuit (no threshold needed) because OOM indicates a fundamental resource constraint that will not self-resolve quickly
- **Triton queue depth > 100** is treated as a degradation signal even without explicit failures
- **NVLink errors** trigger circuit opening for the affected tensor parallelism group
- **ECC uncorrectable errors** trigger immediate circuit opening and node evacuation

---

**References:**

- Martin Fowler, "Circuit Breaker" pattern
- Netflix Hystrix (inspiration for GPU-adapted implementation)
- Michael Nygard, "Release It!" - Stability Patterns
