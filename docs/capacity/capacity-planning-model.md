# Netflix LLM Platform - Capacity Planning Model

**Document ID:** NFLX-LLM-CAP-001
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Last Updated:** 2026-02-21
**Status:** APPROVED

---

## Table of Contents

1. [GPU Capacity Formula](#1-gpu-capacity-formula)
2. [Worked Examples with Real Numbers](#2-worked-examples-with-real-numbers)
3. [Peak Load Modeling (3x Friday 8 PM Spike)](#3-peak-load-modeling-3x-friday-8-pm-spike)
4. [Regional Distribution Strategy](#4-regional-distribution-strategy)
5. [Cost-Capacity Tradeoffs](#5-cost-capacity-tradeoffs)
6. [Capacity Monitoring and Alerts](#6-capacity-monitoring-and-alerts)

---

## 1. GPU Capacity Formula

### 1.1 Core Formula

```
GPU_Required = (Tokens_per_sec_demand * Safety_Factor)
               ────────────────────────────────────────
               (Tokens_per_sec_per_GPU * Batching_Efficiency * (1 + KV_Cache_Savings))
```

**Expanded form:**

```
GPU_Required = ceil(
    (Peak_RPS x Avg_Prompt_Length x Concurrency_Factor x Safety_Factor)
    ÷
    (Tokens_per_sec_per_GPU x Batching_Efficiency x (1 + KV_Cache_Savings))
)
```

Where the demand side computes total tokens/sec needed, and the supply side computes effective per-GPU throughput.

### 1.2 Variable Definitions

| Variable | Description | Unit | Typical Range |
|----------|-------------|------|---------------|
| `Peak_RPS` | Peak requests per second (global) | req/s | 10,000 - 200,000 |
| `Avg_Prompt_Length` | Average input + output token count per request | tokens | 50 - 500 |
| `Concurrency_Factor` | Effective concurrency multiplier (overlapping requests) | ratio | 1.0 - 1.5 |
| `Safety_Factor` | Headroom for burst traffic and failure scenarios | ratio | 1.2 - 1.5 |
| `Tokens_per_sec_per_GPU` | Single-GPU throughput (model and precision dependent) | tokens/s | 1,000 - 5,000 |
| `Batching_Efficiency` | Effective throughput multiplier with dynamic batching | ratio | 0.7 - 0.9 |
| `KV_Cache_Savings` | Compute reduction from prefix caching / KV reuse | ratio | 0.0 - 0.4 |

### 1.3 Per-GPU Throughput by Model Size

| Model | Precision | TP Degree | Batch-1 (tok/s) | Batch-32 (tok/s) | Batch-64 (tok/s) |
|-------|-----------|-----------|-----------------|------------------|------------------|
| 7B | FP16 | 1 | 8,000 | 45,000 | 52,000 |
| 13B | FP16 | 2 | 5,000 | 28,000 | 35,000 |
| 70B | FP16 | 4 | 2,500 | 22,000 | 25,000 |
| 70B | INT8 | 4 | 3,200 | 28,000 | 32,000 |

**Note:** Throughput is per-node (all GPUs combined, accounting for TP overhead).

### 1.4 Batching Efficiency Factors

```
Batching_Efficiency = Base_Efficiency x Padding_Factor x Scheduling_Overhead

Where:
  Base_Efficiency:        0.90 (batch overhead vs. ideal throughput)
  Padding_Factor:         0.92 - 0.98 (sequence length variance within a batch)
  Scheduling_Overhead:    0.97 (dynamic batcher queue management cost)

Typical combined efficiency: 0.80 - 0.90
```

---

## 2. Worked Examples with Real Numbers

### 2.1 Example 1: Standard Weekday Production Load

```
Scenario: Normal weekday evening traffic for Netflix LLM recommendation platform

Inputs:
  Peak_RPS:                   50,000 req/s
  Avg_Prompt_Length:          150 tokens (input) + 50 tokens (output) = 200 tokens total
  Concurrency_Factor:         1.0 (requests are independent)
  Safety_Factor:              1.3 (30% headroom)
  Tokens_per_sec_per_GPU:     3,200 tok/s (single A100, 70B INT8, batch-64)
  GPUs_per_Node:              8 (p4d.24xlarge)
  Batching_Efficiency:        0.85
  KV_Cache_Savings:           0.20 (20% compute saved from prefix caching)

Step-by-Step Calculation:

  Step 1: Total tokens/sec demand
    Demand = Peak_RPS x Avg_Prompt_Length x Concurrency x Safety
    Demand = 50,000 x 200 x 1.0 x 1.3
    Demand = 13,000,000 tokens/s

  Step 2: Effective per-node throughput (8 GPUs)
    Per_GPU_Effective = Tokens_per_sec_per_GPU x Batching_Efficiency x (1 + KV_Cache_Savings)
    Per_GPU_Effective = 3,200 x 0.85 x 1.20
    Per_GPU_Effective = 3,264 tok/s

    Per_Node_Effective = Per_GPU_Effective x GPUs_per_Node / TP_Degree
    Per_Node_Effective = 3,264 x 8 / 4
    Per_Node_Effective = 6,528 tok/s per TP group x 2 TP groups per node
    Per_Node_Effective = 13,056 tok/s per node

    (Simplified: 8 GPUs, TP=4, means 2 independent inference engines per node)

  Step 3: Nodes required
    Required_Nodes = ceil(13,000,000 / 13,056)
    Required_Nodes = ceil(995.7)
    Required_Nodes = 996 GPU nodes (PURE COMPUTE)

    This is the theoretical compute requirement. At Netflix scale with 200M+
    subscribers, this represents full-fleet LLM inference.

  Step 4: Practical deployment (scoped to initial rollout)
    Initial rollout: 10% of traffic = 5,000 req/s
    Adjusted_Demand = 5,000 x 200 x 1.0 x 1.3 = 1,300,000 tok/s
    Required_Nodes = ceil(1,300,000 / 13,056) = 100 nodes

  Step 5: With regional failover overhead (+40% for N+1 across 3 regions)
    Total_Nodes = ceil(100 x 1.4) = 140 GPU nodes
    Total_GPUs  = 140 x 8 = 1,120 NVIDIA A100 GPUs

Result: 140 p4d.24xlarge nodes for initial 10% rollout
```

### 2.2 Example 2: Lean Minimum Viable Deployment

```
Scenario: MVP launch serving personalization for 1% of traffic

Inputs:
  Peak_RPS:                   500 req/s (1% of 50K peak)
  Avg_Prompt_Length:          150 tokens
  Safety_Factor:              1.5 (higher safety for smaller fleet)
  Tokens_per_sec_per_Node:    13,056 tok/s (from Example 1 calculation)
  Batching_Efficiency:        0.75 (lower efficiency at lower traffic volumes)
  KV_Cache_Savings:           0.10 (less prefix sharing with fewer requests)

Calculation:
  Demand = 500 x 150 x 1.5 = 112,500 tok/s
  Per_Node_Adjusted = 3,200 x 8/4 x 0.75 x 1.10 = 10,560 tok/s
  Required_Nodes = ceil(112,500 / 10,560) = 11 nodes

  With failover overhead (+50%, 3 regions, smaller scale):
    Total_Nodes = ceil(11 x 1.5) = 17 GPU nodes
    Total_GPUs  = 17 x 8 = 136 NVIDIA A100 GPUs

Result: 17 nodes spread across 3 regions (6 + 6 + 5)
Monthly Cost (RI): 17 x $19.66/hr x 730 = $243,900/month
Cost per request: $243,900 / (500 x 3600 x 24 x 30) = $0.000189
```

### 2.3 Example 3: Content Release Spike (3x Normal)

```
Scenario: Major content release (e.g., new season of top show) driving 3x traffic

Inputs:
  Peak_RPS:                   150,000 req/s (3x normal 50K peak)
  Avg_Prompt_Length:          200 tokens (longer browsing sessions = longer prompts)
  Safety_Factor:              1.2 (lower safety -- accept graceful degradation)
  Tokens_per_sec_per_Node:    13,056 tok/s
  Batching_Efficiency:        0.90 (higher efficiency at high load)
  KV_Cache_Savings:           0.25 (more prefix sharing with popular content)

Raw Calculation:
  Demand = 150,000 x 200 x 1.2 = 36,000,000 tok/s
  Per_Node_Adjusted = 3,200 x 8/4 x 0.90 x 1.25 = 14,400 tok/s
  Required_Nodes = ceil(36,000,000 / 14,400) = 2,500 nodes

  This exceeds standing capacity. Apply mitigation stack:

Mitigation Waterfall:
  Standing capacity (from Example 1):                 140 nodes
  + Dynamic batching at max efficiency:               Handles 1,820,000 tok/s
  + KV cache prefix optimization:                     +25% = 2,275,000 tok/s
  + CDN cached responses (30% of requests):           Removes 45K req/s
  + Load shedding (P2/P3 requests):                   Removes 30K req/s
  + Remaining live GPU demand:                        75,000 req/s

  Adjusted demand = 75,000 x 200 x 1.2 = 18,000,000 tok/s
  Required = ceil(18,000,000 / 14,400) = 1,250 nodes
  Shortfall vs standing: 1,250 - 140 = 1,110 nodes

  Autoscaler provisions spot instances:
    T+0-5min:   Layers 1-4 absorb initial surge
    T+5-15min:  Spot autoscaler adds 50-100 nodes
    T+15-30min: Full spot fleet provisioned

  Practical limit: Spot pool availability caps at ~200 additional nodes
  Combined capacity: 140 standing + 200 spot = 340 nodes
  Effective capacity with all optimizations: ~75,000 req/s

  For requests beyond capacity: return CDN-cached or degraded (7B model) responses
```

---

## 3. Peak Load Modeling (3x Friday 8 PM Spike)

### 3.1 Traffic Pattern Analysis

```
Weekly Traffic Pattern (normalized to weekday average):

  Monday:     0.85x  (lowest weekday)
  Tuesday:    0.90x
  Wednesday:  0.92x
  Thursday:   0.95x
  Friday:     1.20x  (weekend start)
  Saturday:   1.15x
  Sunday:     1.10x

Daily Traffic Pattern (US-centric, normalized to daily average):

  Time (ET)   Multiplier   Description
  ─────────   ──────────   ──────────────────────────
  01:00 AM    0.30x        Overnight low
  07:00 AM    0.60x        Morning ramp
  12:00 PM    0.75x        Lunch browsing
  05:00 PM    1.00x        Evening start
  08:00 PM    1.50x        PRIME TIME PEAK (Friday = 1.50 x 1.20 = 1.80x)
  10:00 PM    1.20x        Late prime time
  12:00 AM    0.70x        Late night decline

Friday 8 PM Peak Calculation:
  Base weekday peak:           50,000 req/s
  Friday multiplier:           1.20x
  8 PM prime time multiplier:  1.50x
  Combined:                    50,000 x 1.20 x 1.50 = 90,000 req/s

Content Release Spike (on top of Friday peak):
  Content release multiplier:  2.0-3.0x over current traffic
  Worst case: 90,000 x 3.0 = 270,000 req/s
  Realistic case: 90,000 x 2.0 = 180,000 req/s (most spikes are 2x)

Ramp-up characteristics:
  Time to peak: 15-30 minutes after content drops
  Duration at peak: 2-4 hours
  Decay: Exponential, returns to 1.5x within 6 hours
```

### 3.2 Mitigation Stack (Ordered by Activation Threshold)

```
Layer 1: Dynamic Batch Optimization (ALWAYS ACTIVE)
  ─────────────────────────────────────────────────
  Trigger:        Queue depth > 16 requests
  Action:         Increase batch size from preferred 32 to max 64
  Capacity Gain:  +40% effective throughput
  Latency Impact: +10-15ms to p99 (acceptable)
  Cost:           Zero (software optimization)

Layer 2: KV Cache Prefix Sharing (ALWAYS ACTIVE)
  ─────────────────────────────────────────────────
  Trigger:        Common prefix detected (system prompt + popular content IDs)
  Action:         Share KV cache blocks across requests with identical prefixes
  Capacity Gain:  +20-30% compute reduction for prefill phase
  Latency Impact: None (actually reduces TTFT)
  Cost:           Zero (memory already allocated)

Layer 3: CDN Response Caching (ALWAYS ACTIVE)
  ─────────────────────────────────────────────────
  Trigger:        Cache hit for identical or semantically similar query
  Action:         Return cached response from CloudFront edge
  Capacity Gain:  Offloads 30% of requests (no GPU needed)
  Latency Impact: -50ms (faster from CDN than GPU inference)
  Cost:           ~$2K/month CloudFront costs

Layer 4: Load Shedding (ACTIVATED AT GPU > 88%)
  ─────────────────────────────────────────────────
  Trigger:        GPU utilization > 88% for 30 seconds
  Action:         Shed P3 (analytics) requests first, then P2 (background)
  Capacity Gain:  Reduces load by 20-40% depending on traffic mix
  Latency Impact: HTTP 503 for shed requests (clients retry with backoff)
  Cost:           Degraded experience for non-critical traffic

Layer 5: GPU Autoscaling - Spot Instances (ACTIVATED AT GPU > 80%)
  ─────────────────────────────────────────────────
  Trigger:        GPU utilization > 80% sustained for 5 minutes
  Action:         Provision additional p4d.24xlarge spot instances
  Capacity Gain:  Linear with new nodes (5-10 min warmup per node)
  Latency Impact: None once warmed (warm pool pre-loads model)
  Cost:           $9.83/hr per spot node
  Risk:           2-minute spot interruption warning (mitigated by diversity)

Layer 6: Degraded Mode - Model Downgrade (LAST RESORT)
  ─────────────────────────────────────────────────
  Trigger:        All above layers insufficient, queue growing unbounded
  Action:         Route P1/P2 requests to 7B model instead of 70B
  Capacity Gain:  10x throughput (7B uses 1 GPU vs 4 GPUs for 70B)
  Latency Impact: Faster inference, lower quality recommendations
  Cost:           Quality degradation (acceptable during extreme spikes)
```

### 3.3 Capacity Waterfall (Friday 8 PM + Content Release)

```
Capacity vs Demand Over Time:

  Time       Demand (req/s)   Capacity (req/s)   Status
  ─────      ──────────────   ────────────────   ──────────────────
  T+0 min    90,000           50,000 (standing)  Layer 1-3 absorbing
  T+1 min    120,000          70,000 (batching)  Load shedding active
  T+3 min    150,000          100,000 (+ CDN)    Autoscaler triggered
  T+5 min    180,000          125,000 (+ shed)   Spot nodes warming
  T+10 min   180,000          160,000 (+ spot)   Spot nodes online
  T+15 min   180,000          195,000 (full)     All layers active
  T+30 min   150,000          195,000            Excess capacity
  T+2 hr     120,000          195,000            Scale-in cooldown
  T+4 hr     90,000           120,000            Spot nodes terminating
  T+6 hr     60,000           50,000 (standing)  Back to normal

Result: Spike fully absorbed within 15 minutes
  0-5 minutes:   Layers 1-4 handle degraded but available service
  5-15 minutes:  Spot autoscaler restores full capacity
  15+ minutes:   Full capacity, all request priorities served
```

---

## 4. Regional Distribution Strategy

### 4.1 Traffic-Weighted Distribution

```
Region Distribution (Normal Operation):

  us-east-1 (Virginia): 45% of global traffic
    Users: East Coast US, South America, Eastern Canada
    Nodes: 6 (standing)
    GPUs:  48 NVIDIA A100

  us-west-2 (Oregon):   30% of global traffic
    Users: West Coast US, Asia-Pacific overflow, Western Canada
    Nodes: 4 (standing)
    GPUs:  32 NVIDIA A100

  eu-west-1 (Ireland):  25% of global traffic
    Users: Europe, Middle East, Africa
    Nodes: 4 (standing)
    GPUs:  32 NVIDIA A100

  Total Standing Fleet:
    Nodes: 14
    GPUs:  112 NVIDIA A100
    Capacity: ~50,000 req/s (combined, all optimizations active)
```

### 4.2 Failover Capacity Requirements

```
N+1 Failover Requirement:
  Any single region failure must be absorbable by remaining 2 regions.

Worst Case: us-east-1 fails (45% of traffic must be redistributed)
  us-west-2 receives: 30% + (45% x 55%) = 54.75% of global traffic
  eu-west-1 receives: 25% + (45% x 45%) = 45.25% of global traffic

  us-west-2 load increase: 54.75% / 30% = 1.83x normal
  eu-west-1 load increase: 45.25% / 25% = 1.81x normal

  Mitigation timeline:
    T+0-15s:   Route 53 detects failure, reroutes traffic
    T+0-5min:  Dynamic batching absorbs initial surge (+40% capacity)
    T+5-10min: Autoscaler provisions 2-3 spot nodes per surviving region
    T+10-15min: Full capacity restored with spot fleet

  Required: Each region must sustain 1.8x normal load for 10-15 minutes
    with dynamic batching alone (no additional nodes).

  Validation:
    us-west-2 at 1.83x with batch optimization: 4 nodes x 1.4 = 5.6 effective nodes
    Required for 54.75%: ~7.7 nodes  -> Shortfall of ~2 nodes (spot fills in 5-10 min)
    During gap: Load shedding handles excess (P3 and P2 requests deferred)
```

### 4.3 Regional Scaling Triggers

| Metric | Scale-Out Trigger | Scale-In Trigger | Cooldown |
|--------|-------------------|------------------|----------|
| GPU Utilization | > 80% for 5 min | < 40% for 15 min | 10 min |
| Request Queue Depth | > 100 for 2 min | < 10 for 10 min | 5 min |
| P99 Latency | > 150ms for 3 min | < 80ms for 15 min | 10 min |
| KV Cache Pressure | > 85% for 5 min | < 50% for 15 min | 10 min |
| Active Connections | > 10,000 per pod | < 2,000 per pod | 15 min |

---

## 5. Cost-Capacity Tradeoffs

### 5.1 Instance Pricing (February 2026)

| Instance | GPU | On-Demand ($/hr) | 1yr RI ($/hr) | 3yr RI ($/hr) | Spot ($/hr) |
|----------|-----|-------------------|---------------|---------------|-------------|
| p4d.24xlarge | 8x A100 80GB | $32.77 | $19.66 | $13.08 | $9.83 |
| p4de.24xlarge | 8x A100 80GB UltraMem | $40.97 | $24.58 | $16.39 | N/A |
| p5.48xlarge | 8x H100 80GB | $98.32 | $58.99 | $39.33 | $29.50 |

### 5.2 Cost Optimization Strategies

```
Strategy 1: Reserved Instances for Base Capacity
  ──────────────────────────────────────────────
  Scope:    60% of standing fleet (base load that never scales down)
  Savings:  40% vs on-demand pricing
  Risk:     1-year commitment, cannot downsize
  Recommendation: 8 of 14 nodes on 1-year RI

  Monthly cost: 8 x $19.66 x 730 = $114,800
  vs On-Demand: 8 x $32.77 x 730 = $191,400
  Monthly savings: $76,600

Strategy 2: Spot Instances for Burst Capacity
  ──────────────────────────────────────────────
  Scope:    All autoscaled nodes beyond standing fleet
  Savings:  70% vs on-demand pricing
  Risk:     2-minute interruption warning
  Mitigation: Diversify across 3+ AZs, use warm pool for fast replacement

  Typical burst: 4-8 spot nodes during peaks
  Monthly cost: avg 6 nodes x $9.83 x 200 hrs = $11,800
  vs On-Demand: 6 x $32.77 x 200 = $39,300
  Monthly savings: $27,500

Strategy 3: INT8 Quantization (Already Implemented)
  ──────────────────────────────────────────────
  Effect:   25-40% higher throughput per GPU (fewer nodes needed)
  Cost:     Zero incremental (one-time engine rebuild)
  Savings:  ~$75K/month at 14-node standing fleet
  Risk:     <0.5% quality degradation (validated by A/B test)

Strategy 4: Query Complexity Routing
  ──────────────────────────────────────────────
  Concept:  Route simple queries (< 50 tokens) to 7B model
  Eligible: ~40% of all queries
  Savings:  ~$50K/month (4x fewer GPUs for simple queries)
  Risk:     Misrouted complex queries get lower quality
  Status:   Planned for Q3 2026
```

### 5.3 Monthly Cost Projections

```
Scenario A: Conservative (All On-Demand, No Optimization)
  14 nodes x $32.77/hr x 730 hours = $335,000/month
  Cost per request: $0.000078 ($0.078 per 1K requests)

Scenario B: Current Optimized (RI + Spot Mix)
  8 nodes RI x $19.66/hr x 730 hrs  = $114,800
  6 nodes On-Demand x $32.77 x 730  = $143,500
  Avg 4 spot nodes x $9.83 x 200    = $7,900
  Total: ~$172,200/month (49% savings vs Scenario A)

  Cost per request: $0.000040 ($0.040 per 1K requests)
  Cost per 1M tokens: $0.42

Scenario C: Aggressive (RI + Spot + INT8 + 7B Offload)
  6 nodes RI x $19.66 x 730         = $86,100
  4 nodes On-Demand x $32.77 x 730  = $95,700
  Avg 3 spot x $9.83 x 200          = $5,900
  Total: ~$122,000/month (64% savings vs Scenario A)

  Cost per request: $0.000028 ($0.028 per 1K requests)
  Cost per 1M tokens: $0.30

  Note: Scenario C requires query complexity router (Q3 2026)
```

### 5.4 Cost per Request Breakdown

```
At 50,000 req/s average (4.32 billion requests/month):

  ┌─────────────┬──────────────┬────────────────┬──────────────────┐
  │ Scenario    │ Monthly Cost │ Cost/1K Req    │ Cost/1M Tokens   │
  ├─────────────┼──────────────┼────────────────┼──────────────────┤
  │ A (no opt)  │ $335,000     │ $0.078         │ $1.20            │
  │ B (current) │ $172,200     │ $0.040         │ $0.42            │
  │ C (future)  │ $122,000     │ $0.028         │ $0.30            │
  └─────────────┴──────────────┴────────────────┴──────────────────┘

  Comparison with external LLM APIs:
    OpenAI GPT-4 (at similar quality): ~$15.00 per 1M tokens
    Netflix Platform (Scenario B):     ~$0.42 per 1M tokens
    Savings: 97% cost reduction vs external API

  Break-even analysis:
    If external API cost = $15/1M tokens
    And we process 6.5B tokens/month (50K req/s x 200 tok x 730 hrs)
    External cost would be: $97,500,000/month
    Our infrastructure: $172,200/month
    Savings: $97.3M/month (99.8% cheaper at scale)
```

---

## 6. Capacity Monitoring and Alerts

### 6.1 Capacity Dashboard Metrics

```
Key Capacity Indicators (Grafana Dashboard: "LLM Capacity Planning"):

  Row 1: Fleet Overview
    - GPU Fleet Utilization (%) by region [gauge, 3 panels]
    - Available GPU Headroom (nodes remaining) [stat panel]
    - Total Requests per Second (global) [time series]

  Row 2: Throughput
    - Tokens per Second (global, per-region) [time series]
    - Batch Size Distribution [histogram]
    - KV Cache Utilization (%) per region [gauge]

  Row 3: Cost Efficiency
    - Cost per 1M Tokens (rolling 24h) [stat panel]
    - Spot Instance Count and Availability [time series]
    - Reserved vs On-Demand vs Spot mix [pie chart]

  Row 4: Capacity Projections
    - Queue Depth Trend (leading indicator) [time series]
    - 7-day Traffic Forecast [time series with prediction band]
    - Days Until Capacity Exhaustion (at current growth) [stat panel]
```

### 6.2 Capacity Alerts

| Alert | Threshold | Response Time | Action |
|-------|-----------|---------------|--------|
| Fleet utilization > 75% sustained | 1 hour | Next business day | Review capacity plan, consider adding nodes |
| Fleet utilization > 85% sustained | 15 min | Within 1 hour | Trigger autoscaler, notify on-call SRE |
| Fleet utilization > 95% sustained | 5 min | Immediate | Activate load shedding, page SRE lead |
| No headroom for N+1 failover | Immediate | Within 4 hours | Provision additional nodes urgently |
| Spot instance reclamation > 2 nodes | Immediate | Within 15 min | Provision on-demand replacements |
| Monthly cost > 110% of budget | Daily check | Next business day | Review with finance, identify optimization |
| Days to capacity exhaustion < 14 | Daily check | Within 1 week | Initiate procurement of additional capacity |

### 6.3 Quarterly Capacity Review Checklist

```
Every quarter, the ML Platform team reviews:

[ ] Traffic growth rate vs projection (are we on track?)
[ ] GPU utilization trends by region (any hotspots?)
[ ] Spot instance interruption frequency (is diversity sufficient?)
[ ] Reserved Instance utilization (are we wasting RI capacity?)
[ ] Cost per request trend (are optimizations delivering savings?)
[ ] New model requirements (larger models, multi-modal needs?)
[ ] Regional traffic shifts (do we need to rebalance?)
[ ] Hardware lifecycle (any GPUs approaching end of support?)
```

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial capacity planning model |
| 2.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Added Friday 8 PM spike modeling, expanded cost tradeoffs, quarterly review checklist |
