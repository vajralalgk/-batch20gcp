# Netflix LLM Platform - Capacity Planning Model

**Document ID:** NFLX-LLM-CAP-001
**Author:** Gopi Krishna Vajrala
**Version:** 1.0.0
**Last Updated:** 2026-02-21
**Status:** APPROVED

---

## Table of Contents

1. [GPU Capacity Formula](#1-gpu-capacity-formula)
2. [Worked Examples](#2-worked-examples)
3. [Peak Load Modeling (3x Spike)](#3-peak-load-modeling-3x-spike)
4. [Regional Distribution Strategy](#4-regional-distribution-strategy)
5. [Cost-Capacity Tradeoffs](#5-cost-capacity-tradeoffs)
6. [Capacity Monitoring and Alerts](#6-capacity-monitoring-and-alerts)

---

## 1. GPU Capacity Formula

### 1.1 Core Formula

```
Required_GPU_Nodes = ceil(
    (Peak_RPS x Avg_Output_Tokens x Safety_Factor)
    /
    (Tokens_Per_Second_Per_GPU x GPUs_Per_Node x Batching_Efficiency x (1 - KV_Cache_Savings))
)
```

### 1.2 Variable Definitions

| Variable | Description | Unit | Typical Range |
|----------|-------------|------|---------------|
| `Peak_RPS` | Peak requests per second (global) | req/s | 10,000 - 200,000 |
| `Avg_Output_Tokens` | Average output token count per request | tokens | 50 - 500 |
| `Safety_Factor` | Headroom for burst traffic and failures | ratio | 1.2 - 1.5 |
| `Tokens_Per_Second_Per_GPU` | Single-GPU throughput (model-dependent) | tokens/s | 1,000 - 5,000 |
| `GPUs_Per_Node` | GPUs per physical node | count | 8 (p4d.24xlarge) |
| `Batching_Efficiency` | Effective throughput ratio with batching | ratio | 0.7 - 0.9 |
| `KV_Cache_Savings` | Compute reduction from KV cache hits | ratio | 0.0 - 0.4 |

### 1.3 Per-GPU Throughput by Model Size

| Model | Precision | TP Degree | Batch-1 (tok/s) | Batch-32 (tok/s) | Batch-64 (tok/s) |
|-------|-----------|-----------|-----------------|------------------|------------------|
| 7B | FP16 | 1 | 8,000 | 45,000 | 52,000 |
| 13B | FP16 | 2 | 5,000 | 28,000 | 35,000 |
| 70B | FP16 | 4 | 2,500 | 22,000 | 25,000 |
| 70B | INT8 | 4 | 3,200 | 28,000 | 32,000 |

Note: Throughput is per-node (all GPUs combined, accounting for TP overhead).

### 1.4 Batching Efficiency Factors

```
Batching_Efficiency = Base_Efficiency x Padding_Factor x Scheduling_Overhead

Where:
  Base_Efficiency:        0.90 (batch overhead vs. ideal throughput)
  Padding_Factor:         0.92 - 0.98 (sequence length variance in batch)
  Scheduling_Overhead:    0.97 (dynamic batcher queue management)

Typical combined efficiency: 0.80 - 0.90
```

---

## 2. Worked Examples

### 2.1 Example 1: Standard Production Load

```
Scenario: Normal weekday traffic for Netflix LLM Platform

Inputs:
  Peak_RPS:                   50,000 req/s
  Avg_Output_Tokens:          150 tokens
  Safety_Factor:              1.3
  Tokens_Per_Second_Per_Node: 25,000 tok/s (70B, FP16, TP=4, batch-64)
  Batching_Efficiency:        0.85
  KV_Cache_Savings:           0.20 (20% compute saved from prefix caching)

Calculation:
  Required_Throughput = 50,000 x 150 x 1.3
                      = 9,750,000 tokens/s

  Effective_Node_Throughput = 25,000 x 0.85 x (1 / (1 - 0.20))
                            = 25,000 x 0.85 x 1.25
                            = 26,562 tokens/s

  Required_Nodes = ceil(9,750,000 / 26,562)
                 = ceil(367)
                 = 367 GPU nodes

  With failover overhead (+40% for N+1 across 3 regions):
    Total_Nodes = ceil(367 x 1.4) = 514 GPU nodes
    Total_GPUs  = 514 x 8 = 4,112 NVIDIA A100 GPUs
```

### 2.2 Example 2: Lean Deployment (Early Stage)

```
Scenario: Initial launch with limited traffic

Inputs:
  Peak_RPS:                   5,000 req/s
  Avg_Output_Tokens:          100 tokens
  Safety_Factor:              1.5
  Tokens_Per_Second_Per_Node: 25,000 tok/s
  Batching_Efficiency:        0.75 (lower efficiency at lower traffic)
  KV_Cache_Savings:           0.10

Calculation:
  Required_Throughput = 5,000 x 100 x 1.5
                      = 750,000 tokens/s

  Effective_Node_Throughput = 25,000 x 0.75 x (1 / (1 - 0.10))
                            = 25,000 x 0.75 x 1.11
                            = 20,833 tokens/s

  Required_Nodes = ceil(750,000 / 20,833)
                 = ceil(36)
                 = 36 GPU nodes

  With failover overhead (+50%, 3 regions, smaller scale):
    Total_Nodes = ceil(36 x 1.5) = 54 GPU nodes
    Total_GPUs  = 54 x 8 = 432 NVIDIA A100 GPUs
```

### 2.3 Example 3: High-Traffic Event (Content Release)

```
Scenario: Major content release driving 3x normal traffic

Inputs:
  Peak_RPS:                   150,000 req/s (3x normal)
  Avg_Output_Tokens:          200 tokens (longer responses during browsing)
  Safety_Factor:              1.2 (lower safety factor, accepting some shedding)
  Tokens_Per_Second_Per_Node: 25,000 tok/s
  Batching_Efficiency:        0.90 (higher efficiency at high load)
  KV_Cache_Savings:           0.25 (more prefix sharing with popular content)

Calculation:
  Required_Throughput = 150,000 x 200 x 1.2
                      = 36,000,000 tokens/s

  Effective_Node_Throughput = 25,000 x 0.90 x (1 / (1 - 0.25))
                            = 25,000 x 0.90 x 1.33
                            = 30,000 tokens/s

  Required_Nodes = ceil(36,000,000 / 30,000)
                 = ceil(1,200)
                 = 1,200 GPU nodes

  Strategy: This exceeds standing capacity. Apply mitigation layers:
    Standing capacity:    514 nodes (from Example 1)
    Dynamic batching:     +40% effective capacity = 720 nodes equivalent
    KV cache prefix:      +25% = 900 nodes equivalent
    Load shedding (P2/P3): -30% traffic = 840K tokens/s to shed
    CDN cached responses:  -30% traffic = handles 45,000 req/s

  Adjusted peak requiring live GPU inference:
    150,000 - 45,000 (CDN) = 105,000 req/s
    Required_Throughput = 105,000 x 200 x 1.2 = 25,200,000 tokens/s
    With optimizations: 25,200,000 / 30,000 = 840 nodes
    Shortfall vs standing: 840 - 514 = 326 nodes (autoscale + spot)
```

---

## 3. Peak Load Modeling (3x Spike)

### 3.1 Traffic Pattern

```
Traffic Multiplier by Time of Day (US-centric):

  06:00 UTC (01:00 ET):  0.3x  (overnight low)
  12:00 UTC (07:00 ET):  0.6x  (morning ramp)
  18:00 UTC (13:00 ET):  0.8x  (afternoon)
  22:00 UTC (17:00 ET):  1.0x  (evening peak)
  02:00 UTC (21:00 ET):  1.2x  (prime time)
  04:00 UTC (23:00 ET):  0.7x  (late night decline)

  Content Release Spike: Up to 3.0x over evening peak
  Duration: 2-4 hours
  Ramp-up time: 15-30 minutes
```

### 3.2 Mitigation Stack (Ordered by Activation)

```
Layer 1: Dynamic Batch Optimization (Always Active)
  Trigger: Queue depth > 16
  Action: Increase batch size from preferred 32 to max 64
  Capacity Gain: +40% throughput
  Latency Impact: +10-15ms P99

Layer 2: KV Cache Prefix Sharing (Always Active)
  Trigger: Common prefix detected (system prompt + popular content)
  Action: Share KV cache across requests with identical prefixes
  Capacity Gain: +20-30% compute reduction
  Latency Impact: None (actually reduces TTFT)

Layer 3: Response Caching (CDN)
  Trigger: Cache hit for identical or semantically similar queries
  Action: Return cached response from CloudFront edge
  Capacity Gain: 30% of requests served without GPU
  Latency Impact: -50ms (much faster from CDN)

Layer 4: Load Shedding
  Trigger: GPU utilization > 90% for 30 seconds
  Action: Shed P3 (analytics) and P2 (background) requests
  Capacity Gain: Reduces load by 20-40%
  Latency Impact: HTTP 503 for shed requests

Layer 5: Autoscaling (Spot Instances)
  Trigger: GPU utilization > 80% for 5 minutes
  Action: Provision additional p4d.24xlarge spot instances
  Capacity Gain: Linear with new nodes (after 5-10min warmup)
  Latency Impact: None (new capacity absorbs overflow)

Layer 6: Degraded Mode
  Trigger: All above layers insufficient
  Action: Switch to smaller model (7B) for P1/P2 requests
  Capacity Gain: 10x throughput improvement (7B vs 70B)
  Latency Impact: Reduced quality but maintains availability
```

### 3.3 Capacity Waterfall

```
Standing Capacity:                 50,000 req/s (100%)
+ Dynamic Batching:                70,000 req/s (+40%)
+ KV Cache Optimization:           87,500 req/s (+25%)
+ CDN Response Cache:             125,000 req/s (+43%)
+ Load Shedding:                  150,000 req/s (+20% through shedding)
+ Autoscale (5 min):              195,000 req/s (+30%)
+ Degraded Mode:                  250,000 req/s (fallback to 7B model)

Result: 3x spike fully absorbed within 5 minutes
  0-5 minutes: Layers 1-4 handle up to 150,000 req/s
  5+ minutes: Layer 5 (autoscale) provides full capacity
```

---

## 4. Regional Distribution Strategy

### 4.1 Traffic-Weighted Distribution

```
Region Distribution (Normal Operation):

  us-east-1:  45% of global traffic (East Coast US, South America)
  us-west-2:  30% of global traffic (West Coast US, Asia-Pacific)
  eu-west-1:  25% of global traffic (Europe, Middle East, Africa)

Node Allocation:
  Region       | Traffic | Nodes | Capacity  | Utilization | Headroom
  -------------|---------|-------|-----------|-------------|--------
  us-east-1    | 45%     | 6     | 150K t/s  | 67%         | 33%
  us-west-2    | 30%     | 4     | 100K t/s  | 75%         | 25%
  eu-west-1    | 25%     | 4     | 100K t/s  | 63%         | 37%
  -------------|---------|-------|-----------|-------------|--------
  Total        | 100%    | 14    | 350K t/s  | 68% avg     | 32%
```

### 4.2 Failover Capacity Requirements

```
N+1 Failover Requirement:
  Any single region failure must be absorbable by remaining regions.

Worst Case: us-east-1 fails (45% traffic redistributed)
  us-west-2 receives: 30% + (45% x 55%) = 55% of global traffic
  eu-west-1 receives: 25% + (45% x 45%) = 45% of global traffic

  us-west-2 utilization: 55/30 x 75% = 137%  --> EXCEEDS CAPACITY
  eu-west-1 utilization: 45/25 x 63% = 113%  --> EXCEEDS CAPACITY

  Solution: Each region must have capacity for 1.5x its normal traffic:
    us-west-2: 4 nodes -> handles up to 55% with dynamic batching + autoscale
    eu-west-1: 4 nodes -> handles up to 45% with dynamic batching + autoscale

  Timeline:
    T+0-5min:  Dynamic batching absorbs initial surge (+40%)
    T+5-10min: Autoscaler provisions 2-3 additional nodes per region
    T+10-15min: Full capacity restored
```

### 4.3 Regional Scaling Triggers

| Metric | Scale-Out Trigger | Scale-In Trigger | Cooldown |
|--------|-------------------|------------------|----------|
| GPU Utilization | > 80% for 5 min | < 40% for 15 min | 10 min |
| Request Queue Depth | > 100 for 2 min | < 10 for 10 min | 5 min |
| P99 Latency | > 90ms for 3 min | < 50ms for 15 min | 10 min |
| KV Cache Pressure | > 85% for 5 min | < 50% for 15 min | 10 min |

---

## 5. Cost-Capacity Tradeoffs

### 5.1 Instance Pricing

| Instance | GPU | On-Demand ($/hr) | 1yr RI ($/hr) | 3yr RI ($/hr) | Spot ($/hr) |
|----------|-----|-------------------|---------------|---------------|-------------|
| p4d.24xlarge | 8x A100 80GB | $32.77 | $19.66 | $13.08 | $9.83 |
| p4de.24xlarge | 8x A100 80GB (UltraMem) | $40.97 | $24.58 | $16.39 | N/A |
| p5.48xlarge | 8x H100 80GB | $98.32 | $58.99 | $39.33 | $29.50 |

### 5.2 Cost Optimization Strategies

```
Strategy 1: Reserved Instances for Base Capacity
  Base capacity (60% of peak): Reserved instances (1yr RI)
  Cost: 40% savings vs on-demand
  Risk: Committed for 1 year

Strategy 2: Spot Instances for Burst Capacity
  Burst capacity (40% of peak): Spot instances
  Cost: 70% savings vs on-demand
  Risk: 2-minute interruption warning
  Mitigation: Diversified spot pools across AZs

Strategy 3: INT8 Quantization
  Effect: 25-30% higher throughput per GPU
  Cost: Proportional GPU reduction
  Savings: ~$75K/month at current scale
  Risk: Marginal quality reduction (< 0.5% on benchmarks)

Strategy 4: Smaller Model for Low-Complexity Queries
  Route simple queries to 7B model (1 GPU vs 4 GPUs for TP)
  Traffic eligible: ~40% of queries
  Savings: ~$50K/month
  Risk: Lower quality for misrouted complex queries
```

### 5.3 Monthly Cost Projections

```
Scenario A: Conservative (All On-Demand)
  14 nodes x $32.77/hr x 730 hours = $335,000/month

Scenario B: Optimized (RI + Spot Mix)
  10 nodes RI x $19.66/hr x 730 hours = $143,500
  4 nodes Spot x $9.83/hr x 730 hours  = $28,700
  Total: $172,200/month (49% savings)

Scenario C: Aggressive (RI + Spot + INT8 + 7B Offload)
  7 nodes RI x $19.66/hr x 730 hours  = $100,500
  3 nodes Spot x $9.83/hr x 730 hours  = $21,500
  Total: $122,000/month (64% savings)

  Note: Scenario C requires INT8 model validation and
  query complexity router implementation.
```

### 5.4 Cost per Request Breakdown

```
At 50,000 req/s average (4.32B requests/month):

  Scenario B cost: $172,200/month
  Cost per request: $0.000040 ($0.04 per 1,000 requests)
  Cost per 1M tokens: $0.42

  Scenario C cost: $122,000/month
  Cost per request: $0.000028 ($0.028 per 1,000 requests)
  Cost per 1M tokens: $0.30
```

---

## 6. Capacity Monitoring and Alerts

### 6.1 Capacity Dashboard Metrics

```
Key Capacity Indicators:
  - GPU Fleet Utilization (%) by region
  - Available GPU Headroom (nodes)
  - Requests per Second (global, per-region)
  - Tokens per Second (global, per-region)
  - Batch Size Distribution
  - KV Cache Utilization (%)
  - Queue Depth Trend
  - Cost per 1M Tokens (rolling 24h)
```

### 6.2 Capacity Alerts

| Alert | Threshold | Action |
|-------|-----------|--------|
| Fleet utilization > 75% sustained | 1 hour | Review capacity plan, consider scaling |
| Fleet utilization > 85% sustained | 15 min | Trigger autoscaler, notify on-call |
| Fleet utilization > 95% sustained | 5 min | Activate load shedding, page SRE |
| No capacity headroom for N+1 failover | Immediate | Provision additional nodes urgently |
| Spot instance reclamation > 2 nodes | Immediate | Provision on-demand replacements |
| Monthly cost > 110% of budget | Daily check | Review with finance, optimize |

---

**Document Revision History:**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Initial capacity planning model |
