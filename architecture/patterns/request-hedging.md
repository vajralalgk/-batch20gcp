# Request Hedging Pattern for Cross-Region Latency Optimization

**Author:** Gopi Krishna Vajrala
**Context:** Netflix Real-Time LLM Personalization & Inference Platform
**Pattern Type:** Reliability / Latency Optimization
**Last Updated:** 2026-02-21

---

## Overview

Request hedging is a latency optimization technique where duplicate inference requests are sent to multiple GPU clusters (typically in different regions) simultaneously. The first response to arrive is used, and the slower duplicate is cancelled. This pattern dramatically reduces tail latency (p95/p99) by exploiting the statistical independence of latency distributions across regions.

In the Netflix LLM Platform, request hedging is a critical component of our reliability stack, working alongside circuit breakers and load shedding to ensure sub-200ms p95 latency even during cross-region latency spikes.

## Problem Statement

GPU inference latency is generally predictable at the p50 level (40-60ms), but tail latency (p99) can spike to 300ms+ due to:

- **KV cache misses** requiring full prefill computation
- **GPU memory pressure** causing slower memory allocation
- **Network congestion** between the API gateway and Triton Inference Server
- **Cross-region routing** when a region is partially degraded
- **Batch formation delays** when waiting for a full batch

These tail latency events are largely uncorrelated across regions, making hedging an effective mitigation strategy.

## Architecture

```
Request Hedging Flow:

  ┌──────────────┐
  │   Client      │
  │   Request     │
  └──────┬───────┘
         │
         ▼
  ┌──────────────────────────────────────────────┐
  │            FastAPI Gateway                    │
  │                                              │
  │   ┌────────────────────────────────────┐     │
  │   │   Hedging Decision Engine          │     │
  │   │                                    │     │
  │   │   1. Check primary region p95      │     │
  │   │   2. Check hedge budget (< 10%)    │     │
  │   │   3. Select hedge target region    │     │
  │   │   4. Apply hedge delay (optional)  │     │
  │   └───────────────┬────────────────────┘     │
  │                   │                          │
  │          ┌────────┴────────┐                 │
  │          │                 │                 │
  │          ▼                 ▼                 │
  │   ┌─────────────┐  ┌──────────────┐         │
  │   │  Primary     │  │  Hedge       │         │
  │   │  us-east-1   │  │  us-west-2   │         │
  │   │  (42ms avg)  │  │  (85ms avg)  │         │
  │   └──────┬──────┘  └──────┬───────┘         │
  │          │                 │                 │
  │          └────────┬────────┘                 │
  │                   │                          │
  │          ┌────────▼────────┐                 │
  │          │  First Response  │                 │
  │          │  Wins            │                 │
  │          │                  │                 │
  │          │  Cancel slower   │                 │
  │          │  request via     │                 │
  │          │  CancellationToken│                │
  │          └─────────────────┘                 │
  │                                              │
  └──────────────────────────────────────────────┘
```

## Hedging Decision Logic

```python
class HedgingDecisionEngine:
    """Decides whether to hedge a request to a secondary region."""

    def __init__(self):
        self.hedge_budget_percent = 10.0     # Max 10% of requests hedged
        self.p95_threshold_ms = 100          # Hedge when primary p95 > 100ms
        self.hedge_delay_ms = 20             # Wait 20ms before sending hedge
        self.min_request_priority = "P1"     # Only hedge P1 (critical) requests
        self.cooldown_after_hedge_ms = 100   # Min gap between hedged requests

    def should_hedge(self, request, primary_region_metrics, budget_tracker):
        """
        Decision tree:
        1. Is request priority P1 (critical)? -> If no, don't hedge
        2. Is hedge budget available? -> If no, don't hedge
        3. Is primary region p95 > threshold? -> If no, don't hedge
        4. Is secondary region healthy? -> If no, don't hedge
        5. All checks passed -> HEDGE
        """
        if request.priority != "P1":
            return False

        if budget_tracker.hedged_percent >= self.hedge_budget_percent:
            return False

        if primary_region_metrics.p95_ms <= self.p95_threshold_ms:
            return False

        secondary_region = self.select_hedge_target(request)
        if not secondary_region.is_healthy():
            return False

        return True

    def select_hedge_target(self, request):
        """Select the best region for hedging based on current metrics."""
        # Prefer the region with lowest current p50 latency
        # Exclude the primary region and any unhealthy regions
        candidates = [r for r in regions if r != request.primary_region and r.is_healthy()]
        return min(candidates, key=lambda r: r.current_p50_ms)
```

## Hedge Delay Strategy

```
Hedge delay is the time to wait before sending the duplicate request.
This is a critical tuning parameter:

  Too short (0ms):  Every hedged request doubles GPU cost
  Too long (100ms): Hedge arrives too late to help with tail latency
  Optimal (20ms):   Catches ~80% of tail latency events, low cost overhead

  Strategy: Adaptive Hedge Delay
  ───────────────────────────────

  If primary p95 < 80ms:   hedge_delay = 30ms (conservative)
  If primary p95 80-120ms: hedge_delay = 20ms (standard)
  If primary p95 > 120ms:  hedge_delay = 10ms (aggressive)
  If primary p95 > 200ms:  hedge_delay = 0ms  (immediate, region may be failing)

  The delay also serves as a natural filter:
  - If the primary responds within the delay window, the hedge is never sent
  - This reduces the actual hedge rate well below the 10% budget
  - Typical observed hedge rate: 3-5% of P1 requests
```

## Cancellation Mechanism

```
When the first response arrives, the slower request must be cancelled
to free GPU resources:

  Primary Responds First (most common):
    1. Return primary response to client
    2. Send gRPC Cancel to secondary region's Triton endpoint
    3. Secondary region releases GPU resources for the cancelled request
    4. KV cache blocks for the cancelled request are freed

  Hedge Responds First (tail latency scenario):
    1. Return hedge response to client
    2. Primary response arrives later and is discarded
    3. No explicit cancel needed (response already sent)

  Both Fail:
    1. Wait for timeout (adaptive: 150-300ms)
    2. Return circuit breaker fallback response
    3. Log as hedging failure for metrics
```

## Cost Control

```
Hedging Cost Budget:

  Budget: Max 10% of requests hedged
  Implementation: Token bucket rate limiter

    bucket_size = 100 tokens
    refill_rate = max_rps * 0.10 tokens/second

    At 50,000 req/s: refill_rate = 5,000 tokens/s
    Each hedge consumes 1 token

  Cost Impact:
    Worst case: 10% more GPU inference requests
    Typical: 3-5% more inference requests (due to hedge delay filtering)
    At $0.04/1K requests: additional $72-$120/day
    Latency improvement value far exceeds this cost

  Cost Mitigation:
    1. Hedge delay reduces actual hedge rate
    2. Only P1 requests are hedged (not P2/P3)
    3. Cancellation frees GPU resources quickly
    4. Secondary region may serve from cache (no GPU cost)
```

## Performance Impact

```
Measured Results (Production, 30-day average):

  Without Hedging:
    p50: 42ms
    p95: 128ms
    p99: 185ms

  With Hedging (10% budget, 20ms delay):
    p50: 42ms  (no change - hedging doesn't affect median)
    p95: 115ms (-10%)
    p99: 145ms (-22%)

  During Cross-Region Latency Events:
    Without hedging p99: 350-500ms
    With hedging p99:    150-200ms  (-40% to -60%)

  Hedge Success Rate:
    Hedge sent:           4.2% of P1 requests
    Hedge won (faster):   38% of hedged requests
    Hedge cancelled:      62% of hedged requests (primary was faster)
    Net cost overhead:    1.6% increase in total GPU inference
```

## Configuration

```yaml
# Helm values for request hedging
hedging:
  enabled: true
  budgetPercent: 10
  delayMs: 20
  adaptiveDelay: true
  minPriority: P1
  targetRegions:
    - us-west-2
    - eu-west-1
  metrics:
    enabled: true
    histogramBuckets: [5, 10, 20, 50, 100, 200, 500]
  circuitBreaker:
    # Disable hedging if secondary region circuit breaker is open
    respectCircuitBreaker: true
```

## Metrics

```
# Prometheus metrics emitted by the hedging engine
hedging_requests_total{region, target_region, result}
  # result: "primary_won", "hedge_won", "both_failed", "not_hedged"

hedging_latency_savings_ms{quantile}
  # Distribution of latency saved by hedging (hedge_won cases only)

hedging_budget_utilization_percent
  # Current hedge budget usage (should stay < 10%)

hedging_cancel_latency_ms{region}
  # Time to cancel the slower request
```

## When NOT to Hedge

```
Do not use hedging when:
  1. Both regions are healthy and p95 < 80ms (no benefit, only cost)
  2. Secondary region circuit breaker is OPEN (hedge will fail anyway)
  3. Request is P2 or P3 priority (not worth the GPU cost)
  4. System is under load shedding (adding hedge requests worsens pressure)
  5. Request payload is very large (> 4KB) - doubles network bandwidth
  6. Hedge budget is exhausted for current window
```

## Relationship to Other Patterns

```
Request Hedging interacts with:

  Circuit Breakers: If secondary region breaker is OPEN, hedging is disabled
  Load Shedding:    If primary region is shedding, hedging is disabled
  Dynamic Batching: Hedge requests participate in batch formation normally
  CDN Caching:      Hedge may be served from CDN cache (zero GPU cost)
  Error Budgets:    Hedging failures count toward error budget
```

---

**References:**

- Dean & Barroso, "The Tail at Scale" (Google, 2013) - Foundational paper on hedged requests
- Netflix Zuul - Request hedging in API gateway layer
- gRPC Hedging Policy specification
