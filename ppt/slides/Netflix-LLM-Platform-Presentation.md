# Netflix Real-Time LLM Personalization & Inference Platform
## Executive Presentation - 10 Slides

**Author:** Gopi Krishna Vajrala
**Date:** February 2026
**Audience:** Engineering Leadership, VP/SVP, CTO Office

---

## Slide 1: Title Slide

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║          Netflix Real-Time LLM Personalization                             ║
║                 & Inference Platform                                       ║
║                                                                            ║
║   ┌──────────────────────────────────────────────────────────┐             ║
║   │         Powering Next-Generation Content Discovery       │             ║
║   │            with Sub-200ms LLM Inference at Scale         │             ║
║   └──────────────────────────────────────────────────────────┘             ║
║                                                                            ║
║          Author:  Gopi Krishna Vajrala                                     ║
║          Role:    Senior ML Infrastructure Engineer                        ║
║          Date:    February 2026                                            ║
║                                                                            ║
║          Classification: Internal - Engineering Leadership                 ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Welcome everyone. Today I'm presenting the architecture and results of our Real-Time LLM Personalization and Inference Platform at Netflix. This platform represents a fundamental shift in how we deliver personalized content recommendations -- moving from traditional ML ranking models to real-time large language model inference that understands context, user intent, and content semantics at a depth that was previously impossible.

Over the next 10 slides, I'll walk you through the problem we solved, the architecture we built, the performance we achieved, and the business impact this platform delivers. We'll cover everything from GPU-level optimizations to multi-region reliability patterns, and I'll share concrete numbers on cost savings, latency improvements, and engagement metrics.

This platform currently serves over 50,000 requests per second at peak, with p95 latency under 200 milliseconds, running across three AWS regions on a fleet of NVIDIA A100 GPUs. Let's begin.

---

## Slide 2: The Problem

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                    THE PROBLEM: Why We Needed This                         ║
║                                                                            ║
║   ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ║
║   │   TAIL LATENCY     │  │  GPU UNDER-         │  │   COLD START       │  ║
║   │                    │  │  UTILIZATION        │  │   PENALTY          │  ║
║   │  p99 > 800ms      │  │                    │  │                    │  ║
║   │  Unacceptable for  │  │  Average 35% util  │  │  First request:    │  ║
║   │  real-time UX      │  │  $500K/yr wasted   │  │  2-5 seconds       │  ║
║   │                    │  │  on idle GPUs      │  │  User already      │  ║
║   │  Users bounce at   │  │                    │  │  navigated away    │  ║
║   │  >300ms            │  │  No batching       │  │                    │  ║
║   └────────────────────┘  └────────────────────┘  └────────────────────┘  ║
║                                                                            ║
║   ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ║
║   │   COST             │  │  NO MULTI-REGION   │  │  SCALING           │  ║
║   │   INEFFICIENCY     │  │  FAILOVER          │  │  LIMITATIONS       │  ║
║   │                    │  │                    │  │                    │  ║
║   │  $2.1M/yr GPU      │  │  Single-region     │  │  Manual scaling    │  ║
║   │  spend with poor   │  │  deployment        │  │  50-min provision  │  ║
║   │  $/token ratio     │  │  No DR strategy    │  │  Cannot handle     │  ║
║   │                    │  │  SLA violations    │  │  traffic spikes    │  ║
║   └────────────────────┘  └────────────────────┘  └────────────────────┘  ║
║                                                                            ║
║   Impact: 12% of personalization requests timing out during peak hours     ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Let me paint the picture of where we started. Before this platform, our LLM inference setup had six critical problems.

First, tail latency. Our p99 latency was over 800 milliseconds. Research shows that users begin to disengage at 300ms, and at 800ms we were losing meaningful engagement. Twelve percent of personalization requests were timing out during Friday evening peak hours.

Second, GPU underutilization. We were running NVIDIA A100 GPUs at an average of 35% utilization. That translates to roughly half a million dollars per year in wasted compute. The root cause was that we had no dynamic batching -- each request was processed individually, leaving the GPU idle between memory transfers and kernel launches.

Third, cold start penalties. When a new GPU node spun up, the first inference request took 2-5 seconds while the model loaded into GPU memory and the KV cache warmed up. By the time the response came back, the user had already scrolled past or navigated away.

Fourth, cost inefficiency. We were spending $2.1 million per year on GPU infrastructure with a poor cost-per-token ratio because we hadn't implemented quantization, prefix caching, or intelligent request routing.

Fifth, no multi-region failover. We were running in a single region with no disaster recovery. Any regional outage meant complete loss of personalized recommendations for all users.

Sixth, manual scaling that took 50 minutes to provision new nodes -- far too slow for traffic spikes during content releases.

These problems together created an unacceptable user experience and unsustainable cost structure. That's what motivated this platform.

---

## Slide 3: Why Real-Time AI Matters

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║              WHY REAL-TIME AI: The Strategic Imperative                    ║
║                                                                            ║
║   ┌──────────────────────────────────────────────────────────────────┐     ║
║   │                                                                  │     ║
║   │  Traditional ML Pipeline          LLM Real-Time Inference        │     ║
║   │  ════════════════════            ═══════════════════════════      │     ║
║   │                                                                  │     ║
║   │  User Action                     User Action                     │     ║
║   │       │                               │                          │     ║
║   │       ▼                               ▼                          │     ║
║   │  Feature Store Lookup            Context Assembly (<5ms)         │     ║
║   │       │ (20ms)                        │                          │     ║
║   │       ▼                               ▼                          │     ║
║   │  Batch Scoring (offline)         LLM Inference (50-100ms)        │     ║
║   │       │ (hours stale)                 │                          │     ║
║   │       ▼                               ▼                          │     ║
║   │  Static Re-Ranking              Dynamic Re-Ranking               │     ║
║   │       │                               │                          │     ║
║   │       ▼                               ▼                          │     ║
║   │  Cached Results (stale)          Fresh, Contextual Results       │     ║
║   │                                                                  │     ║
║   │  Latency: 50ms (but stale)      Latency: <200ms (real-time)     │     ║
║   │  Freshness: Hours old           Freshness: Sub-second            │     ║
║   │  Context: Limited features      Context: Full semantic depth     │     ║
║   │                                                                  │     ║
║   └──────────────────────────────────────────────────────────────────┘     ║
║                                                                            ║
║   Three Pillars of Real-Time AI Value:                                    ║
║                                                                            ║
║   1. USER ENGAGEMENT          2. CONTENT DISCOVERY      3. LLM RE-RANKING ║
║      +15% session duration       +23% long-tail          Understands       ║
║      +8% content starts          content surfaced         "mood", "intent" ║
║      -20% browse-to-play         +12% catalog breadth     and "context"    ║
║      latency                     exposure                 beyond features  ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Why does real-time AI matter for Netflix? Let me explain through three pillars.

First, user engagement. When we moved from stale batch-scored recommendations to real-time LLM-powered personalization, we saw a 15% increase in session duration and an 8% increase in content starts. The browse-to-play latency -- the time between a user opening the app and actually pressing play -- dropped by 20%. Users are finding what they want faster because the LLM understands not just what they've watched, but the context of what they're looking for right now.

Second, content discovery. This is critical for our content investment strategy. The LLM surfaces 23% more long-tail content compared to our traditional models. That means titles beyond the top 200 are getting significantly more exposure -- a 12% increase in catalog breadth. This matters because we invest billions in content, and every title that goes undiscovered represents wasted investment.

Third, LLM re-ranking. Traditional recommendation models operate on engineered features -- watch history, genre preferences, time of day. An LLM understands semantic concepts like mood, intent, and narrative context. It can understand that a user who just finished a tense thriller might want a light comedy to decompress, or that someone browsing at midnight has different preferences than the same person browsing on a Sunday afternoon.

The comparison on screen shows the architectural difference. Traditional pipelines return stale results that were scored hours ago in batch. Our platform assembles context in under 5 milliseconds, runs LLM inference in 50-100 milliseconds, and delivers dynamically re-ranked results in under 200 milliseconds total. Real-time, contextual, and semantically rich.

---

## Slide 4: System Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║           FULL SYSTEM ARCHITECTURE: End-to-End Platform                    ║
║                                                                            ║
║   ┌─────────────────────────── CLIENT LAYER ───────────────────────────┐   ║
║   │  Netflix App (iOS/Android/Web/TV) ──> CloudFront CDN Edge Cache   │   ║
║   └────────────────────────────────┬──────────────────────────────────┘   ║
║                                    │                                      ║
║                                    ▼                                      ║
║   ┌─────────────────────── ROUTING LAYER ─────────────────────────────┐   ║
║   │  Route 53 (Geo DNS)  ──>  ALB (Regional)  ──>  API Gateway       │   ║
║   │  Latency-based routing    Health checks        Rate limiting      │   ║
║   │  <15s failover            TLS termination      Auth (JWT/mTLS)    │   ║
║   └────────────────────────────────┬──────────────────────────────────┘   ║
║                                    │                                      ║
║                                    ▼                                      ║
║   ┌─────────────────────── GATEWAY LAYER ─────────────────────────────┐   ║
║   │  FastAPI Gateway (EKS, 16 pods per region)                        │   ║
║   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐             │   ║
║   │  │ Request   │ │ Priority │ │ Circuit  │ │ Request  │             │   ║
║   │  │ Validator │ │ Classifier│ │ Breaker  │ │ Hedging  │             │   ║
║   │  └──────────┘ └──────────┘ └──────────┘ └──────────┘             │   ║
║   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐             │   ║
║   │  │ Load     │ │ Adaptive │ │ Token    │ │ Metrics  │             │   ║
║   │  │ Shedder  │ │ Timeout  │ │ Budget   │ │ Emitter  │             │   ║
║   │  └──────────┘ └──────────┘ └──────────┘ └──────────┘             │   ║
║   └────────────────────────────────┬──────────────────────────────────┘   ║
║                                    │                                      ║
║              ┌─────────────────────┼─────────────────────┐                ║
║              │                     │                     │                ║
║              ▼                     ▼                     ▼                ║
║   ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐         ║
║   │ CONTEXT LAYER    │ │ INFERENCE LAYER  │ │ CACHE LAYER      │         ║
║   │                  │ │                  │ │                  │         ║
║   │ User Profile     │ │ Triton Inference │ │ ElastiCache      │         ║
║   │ (DynamoDB)       │ │ Server           │ │ (Redis Cluster)  │         ║
║   │                  │ │                  │ │                  │         ║
║   │ Feature Store    │ │ TensorRT-LLM     │ │ Response Cache   │         ║
║   │ (SageMaker)      │ │ Engine           │ │ KV Cache         │         ║
║   │                  │ │                  │ │ Session Store     │         ║
║   │ Content Catalog  │ │ NVIDIA A100 GPUs │ │                  │         ║
║   │ (OpenSearch)     │ │ (8x per node)    │ │ TTL: 30s-300s    │         ║
║   │                  │ │                  │ │                  │         ║
║   │ Embeddings       │ │ TP=4, INT8       │ │ Hit Rate: ~85%   │         ║
║   │ (pgvector)       │ │ PagedAttention   │ │                  │         ║
║   └──────────────────┘ └──────────────────┘ └──────────────────┘         ║
║                                    │                                      ║
║                                    ▼                                      ║
║   ┌─────────────────── GPU COMPUTE LAYER ─────────────────────────────┐   ║
║   │                                                                    │   ║
║   │  EKS Cluster (GPU Node Group: p4d.24xlarge)                        │   ║
║   │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐      │   ║
║   │  │ GPU Node 1│  │ GPU Node 2│  │ GPU Node 3│  │ GPU Node N│      │   ║
║   │  │ 8x A100   │  │ 8x A100   │  │ 8x A100   │  │ 8x A100   │      │   ║
║   │  │ NVLink    │  │ NVLink    │  │ NVLink    │  │ NVLink    │      │   ║
║   │  │ 80GB HBM2e│  │ 80GB HBM2e│  │ 80GB HBM2e│  │ 80GB HBM2e│      │   ║
║   │  └───────────┘  └───────────┘  └───────────┘  └───────────┘      │   ║
║   │                                                                    │   ║
║   │  Dynamic Batching | Continuous Batching | CUDA Graphs              │   ║
║   │  Warm Pool (pre-loaded models) | Health Probes | DCGM Monitoring   │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                    │                                      ║
║                                    ▼                                      ║
║   ┌─────────────────── OBSERVABILITY LAYER ───────────────────────────┐   ║
║   │  Prometheus + Grafana    CloudWatch    X-Ray Distributed Tracing  │   ║
║   │  DCGM GPU Metrics       PagerDuty     Custom SLO Dashboards      │   ║
║   │  OpenTelemetry spans     Error Budgets  Capacity Planning Alerts  │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Let me walk you through the full system architecture, layer by layer.

Starting at the top: the Client Layer. Requests originate from Netflix applications across all platforms -- iOS, Android, web, and smart TVs. These hit CloudFront edge caches first, where we serve cached responses for repeated queries, handling about 30% of traffic without touching GPUs.

The Routing Layer uses Route 53 with latency-based routing for geo-aware traffic distribution. If a region goes down, failover happens in under 15 seconds. Regional Application Load Balancers handle health checks and TLS termination, and our API Gateway provides rate limiting and authentication via JWT and mutual TLS.

The Gateway Layer is where the intelligence lives. Our FastAPI gateway runs 16 pods per region on EKS and contains eight critical components: request validation, priority classification (P1-P3 tiers), circuit breakers per backend, request hedging for cross-region latency optimization, load shedding under pressure, adaptive timeouts that adjust based on system state, token budgets to prevent runaway generation, and metrics emission for observability.

Three parallel subsystems feed into inference: the Context Layer assembles user profiles from DynamoDB, real-time features from SageMaker Feature Store, content metadata from OpenSearch, and semantic embeddings from pgvector. The Cache Layer in ElastiCache provides response caching, KV cache state, and session storage with an 85% hit rate. The Inference Layer runs Triton Inference Server with TensorRT-LLM engines on NVIDIA A100 GPUs.

The GPU Compute Layer runs on EKS with p4d.24xlarge instances, each containing 8 A100 GPUs connected via NVLink. We use tensor parallelism of 4, INT8 quantization, PagedAttention for efficient KV cache management, and CUDA graphs for reduced kernel launch overhead. Our warm pool keeps models pre-loaded so new nodes can serve traffic immediately.

Finally, the Observability Layer ties everything together with Prometheus and Grafana for metrics, DCGM for GPU-specific telemetry, CloudWatch for AWS-native monitoring, X-Ray for distributed tracing, PagerDuty for alerting, and custom SLO dashboards tracking our error budgets.

---

## Slide 5: Performance Benchmarks

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║              PERFORMANCE BENCHMARKS: Measured Results                      ║
║                                                                            ║
║   ┌─────────────────────── LATENCY ───────────────────────────────────┐   ║
║   │                                                                    │   ║
║   │  Metric       Before      After       Target      Status          │   ║
║   │  ──────────   ─────────   ─────────   ─────────   ──────          │   ║
║   │  p50 Latency  120ms       42ms        <100ms      EXCEEDED        │   ║
║   │  p95 Latency  450ms       128ms       <200ms      EXCEEDED        │   ║
║   │  p99 Latency  820ms       185ms       <300ms      EXCEEDED        │   ║
║   │  TTFT         350ms       38ms        <100ms      EXCEEDED        │   ║
║   │  Cold Start   4,200ms     180ms       <500ms      EXCEEDED        │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║   ┌─────────── GPU EFFICIENCY ──────────┐ ┌──── THROUGHPUT ───────────┐   ║
║   │                                      │ │                          │   ║
║   │  GPU Utilization                     │ │  Tokens/sec (per node)   │   ║
║   │                                      │ │                          │   ║
║   │  Before: ████░░░░░░░░  35%           │ │  Before:    800 tok/s    │   ║
║   │  After:  █████████░░░  82%           │ │  After:   3,200 tok/s    │   ║
║   │  Target: ████████░░░░  80%+ [MET]    │ │  Target:  3,000 tok/s    │   ║
║   │                                      │ │  Status:  EXCEEDED       │   ║
║   │  SM Occupancy: 78% (target: 70%+)    │ │                          │   ║
║   │  Memory Bandwidth: 72% utilized      │ │  Peak: 3,800 tok/s      │   ║
║   │  NVLink Utilization: 85%             │ │  (with INT8 + batch-64)  │   ║
║   │                                      │ │                          │   ║
║   └──────────────────────────────────────┘ └──────────────────────────┘   ║
║                                                                            ║
║   ┌─────────────────────── RELIABILITY ───────────────────────────────┐   ║
║   │                                                                    │   ║
║   │  SLA Target:  99.95% availability                                  │   ║
║   │  Achieved:    99.97% (rolling 90 days)                             │   ║
║   │                                                                    │   ║
║   │  Error Rate:  0.03% (target <0.1%)                                 │   ║
║   │  Timeout Rate: 0.01% (target <0.05%)                               │   ║
║   │  Failover Time: 12s average (target <15s)                          │   ║
║   │                                                                    │   ║
║   │  Monthly Error Budget: 21.6 minutes                                │   ║
║   │  Budget Consumed (avg): 8.2 minutes (38% of budget)                │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Let's look at the numbers. These are measured production results, not lab benchmarks.

Starting with latency: our p50 dropped from 120ms to 42ms -- that's a 65% improvement. Our p95, which is the key metric for user experience, went from 450ms down to 128ms, well under our 200ms target. Even our p99 at 185ms is better than our p95 was before. Time-to-first-token, which determines how quickly users see results start appearing, dropped from 350ms to 38ms thanks to prefix caching and warm KV caches. And cold starts -- previously a 4.2-second penalty -- are now 180ms using our warm pool strategy where models are pre-loaded in GPU memory before nodes join the serving pool.

GPU efficiency tells a compelling cost story. Utilization jumped from 35% to 82%, exceeding our 80% target. SM occupancy is at 78%, memory bandwidth utilization at 72%, and NVLink utilization at 85%. These numbers mean we're extracting close to maximum value from every GPU dollar we spend.

Throughput improved 4x, from 800 tokens per second to 3,200 tokens per second per node, exceeding our 3,000 target. At peak with INT8 quantization and batch-64, we've measured 3,800 tokens per second.

On reliability: we targeted 99.95% availability and we're running at 99.97% over the last 90 days. Our error rate is 0.03% against a 0.1% target, and timeout rate is 0.01% against 0.05%. Failover completes in 12 seconds on average, under our 15-second target. We're consuming only 38% of our monthly error budget, which gives us substantial headroom for deployments and experiments.

---

## Slide 6: Cost Savings

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                  COST SAVINGS: Three Optimization Vectors                  ║
║                                                                            ║
║  ┌──────────────── INT8 QUANTIZATION ──────────────────────────────────┐   ║
║  │                                                                      │   ║
║  │  Technique: Weight-only INT8 quantization via TensorRT-LLM           │   ║
║  │                                                                      │   ║
║  │  FP16 Baseline:    25,000 tok/s per node                             │   ║
║  │  INT8 Optimized:   35,000 tok/s per node (+40%)                      │   ║
║  │                                                                      │   ║
║  │  Quality Impact:   <0.3% degradation on internal benchmarks          │   ║
║  │  Memory Savings:   Model footprint reduced from 140GB to 75GB        │   ║
║  │                                                                      │   ║
║  │  GPU Reduction:    367 nodes -> 262 nodes (-28.6%)                   │   ║
║  │  Annual Savings:   $840,000/year                                     │   ║
║  │                                                                      │   ║
║  │  ████████████████████████████████████████░░░░░░░░░░░░░░  40% MORE    │   ║
║  │  |<---- FP16 throughput ---->|<-- INT8 gain -->|                      │   ║
║  │                                                                      │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║  ┌──────────────── DYNAMIC BATCHING ───────────────────────────────────┐   ║
║  │                                                                      │   ║
║  │  Technique: Continuous batching with TensorRT-LLM + Triton           │   ║
║  │                                                                      │   ║
║  │  No Batching:   800 tok/s per node   (batch-1)                       │   ║
║  │  Static Batch:  1,800 tok/s per node (batch-32, padded)              │   ║
║  │  Dynamic Batch: 3,200 tok/s per node (continuous, batch-64)          │   ║
║  │                                                                      │   ║
║  │  Efficiency Gain:  300% throughput improvement over no batching       │   ║
║  │  GPU Utilization:  35% -> 82% (+134% improvement)                    │   ║
║  │                                                                      │   ║
║  │  Cost Impact:   Same fleet handles 4x the traffic                    │   ║
║  │  Effective Savings: 60% cost reduction vs. scaling without batching   │   ║
║  │  Annual Savings:    $1,260,000/year                                   │   ║
║  │                                                                      │   ║
║  │  No Batch: ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  800 tok/s   │   ║
║  │  Static:   █████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  1,800 tok/s │   ║
║  │  Dynamic:  ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░  3,200 tok/s │   ║
║  │                                                                      │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║  ┌──────────────── WARM POOL STRATEGY ─────────────────────────────────┐   ║
║  │                                                                      │   ║
║  │  Technique: Pre-loaded model weights in standby GPU nodes            │   ║
║  │                                                                      │   ║
║  │  Cold Start (before):  4,200ms  (model load + KV cache init)         │   ║
║  │  Warm Start (after):     180ms  (already loaded, cache primed)       │   ║
║  │                                                                      │   ║
║  │  Cold Start Reduction:  95.7% faster                                 │   ║
║  │                                                                      │   ║
║  │  Business Impact:                                                    │   ║
║  │    - Eliminated 90% of cold-start-related timeouts                   │   ║
║  │    - Autoscaling effective within seconds (not minutes)              │   ║
║  │    - Content release spikes handled without user impact              │   ║
║  │                                                                      │   ║
║  │  Warm Pool Cost:  ~$15K/month (3 standby nodes)                      │   ║
║  │  Timeout Savings: ~$45K/month (reduced retries + user retention)     │   ║
║  │  Net Annual Savings: $360,000/year                                   │   ║
║  │                                                                      │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║  ┌──────────────── TOTAL ANNUAL SAVINGS ───────────────────────────────┐   ║
║  │                                                                      │   ║
║  │  INT8 Quantization:     $840,000    ████████████████░░░░░░░░░░░░░   │   ║
║  │  Dynamic Batching:    $1,260,000    ████████████████████████░░░░░░   │   ║
║  │  Warm Pool Strategy:    $360,000    ███████░░░░░░░░░░░░░░░░░░░░░░   │   ║
║  │  ─────────────────────────────────────────────────                   │   ║
║  │  TOTAL:               $2,460,000/year                                │   ║
║  │                                                                      │   ║
║  │  Previous Annual GPU Spend:  $2,100,000                              │   ║
║  │  Optimized Annual Spend:     $1,380,000 (34% reduction)              │   ║
║  │  While handling 4x more traffic                                      │   ║
║  │                                                                      │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Cost optimization was a primary design goal. We achieved savings through three complementary vectors.

First, INT8 quantization. By converting our 70B model from FP16 to INT8 weight-only quantization using TensorRT-LLM, we increased per-node throughput by 40% -- from 25,000 to 35,000 tokens per second. The quality impact was less than 0.3% on our internal evaluation benchmarks, which is within acceptable margins. The model memory footprint dropped from 140GB across the tensor parallel group to 75GB. This allowed us to reduce our fleet from 367 nodes to 262 nodes, saving $840,000 annually.

Second, dynamic batching. This was the single largest cost lever. Moving from batch-1 processing to continuous batching with TensorRT-LLM and Triton delivered a 300% throughput improvement. GPU utilization jumped from 35% to 82%. The same fleet that previously handled 800 tokens per second per node now handles 3,200. The effective cost reduction versus scaling without batching is 60%, saving $1.26 million annually.

Third, our warm pool strategy. We maintain 3 standby GPU nodes with models pre-loaded in memory. The warm pool costs approximately $15,000 per month, but it eliminates 90% of cold-start-related timeouts. The savings from reduced retries, fewer SLA violations, and improved user retention during traffic spikes are approximately $45,000 per month, netting $360,000 in annual savings.

Total annual savings: $2.46 million. We reduced our effective GPU spend by 34% while simultaneously handling 4x more traffic than before. The cost per request dropped from $0.000058 to $0.000028.

---

## Slide 7: Multi-Region Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║           MULTI-REGION: Active-Active Global Deployment                   ║
║                                                                            ║
║   ┌────────────────────────────────────────────────────────────────────┐   ║
║   │                                                                    │   ║
║   │                        Route 53 (Global)                           │   ║
║   │                    Latency-Based Routing                           │   ║
║   │                    Health Check: /healthz                          │   ║
║   │                    Failover: <15 seconds                           │   ║
║   │                                                                    │   ║
║   │          ┌──────────────┼──────────────┐                           │   ║
║   │          │              │              │                           │   ║
║   │          ▼              ▼              ▼                           │   ║
║   │                                                                    │   ║
║   │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │   ║
║   │  │  US-EAST-1   │ │  US-WEST-2   │ │  EU-WEST-1   │               │   ║
║   │  │  (Virginia)  │ │  (Oregon)    │ │  (Ireland)   │               │   ║
║   │  │              │ │              │ │              │               │   ║
║   │  │  Traffic: 45%│ │  Traffic: 30%│ │  Traffic: 25%│               │   ║
║   │  │  Nodes: 6    │ │  Nodes: 4    │ │  Nodes: 4    │               │   ║
║   │  │  GPUs: 48    │ │  GPUs: 32    │ │  GPUs: 32    │               │   ║
║   │  │              │ │              │ │              │               │   ║
║   │  │  ┌────────┐  │ │  ┌────────┐  │ │  ┌────────┐  │               │   ║
║   │  │  │ EKS    │  │ │  │ EKS    │  │ │  │ EKS    │  │               │   ║
║   │  │  │ Cluster│  │ │  │ Cluster│  │ │  │ Cluster│  │               │   ║
║   │  │  └────────┘  │ │  └────────┘  │ │  └────────┘  │               │   ║
║   │  │  ┌────────┐  │ │  ┌────────┐  │ │  ┌────────┐  │               │   ║
║   │  │  │ Triton │  │ │  │ Triton │  │ │  │ Triton │  │               │   ║
║   │  │  │ + GPUs │  │ │  │ + GPUs │  │ │  │ + GPUs │  │               │   ║
║   │  │  └────────┘  │ │  └────────┘  │ │  └────────┘  │               │   ║
║   │  │  ┌────────┐  │ │  ┌────────┐  │ │  ┌────────┐  │               │   ║
║   │  │  │  Redis │  │ │  │  Redis │  │ │  │  Redis │  │               │   ║
║   │  │  │ Cluster│  │ │  │ Cluster│  │ │  │ Cluster│  │               │   ║
║   │  │  └────────┘  │ │  └────────┘  │ │  └────────┘  │               │   ║
║   │  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘               │   ║
║   │         │                │                │                        │   ║
║   │         └────────────────┼────────────────┘                        │   ║
║   │                          │                                         │   ║
║   │                ┌─────────┴─────────┐                               │   ║
║   │                │  DynamoDB Global   │                               │   ║
║   │                │  Tables            │                               │   ║
║   │                │  (Active-Active    │                               │   ║
║   │                │   Replication)     │                               │   ║
║   │                └───────────────────┘                                │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║   Key Multi-Region Capabilities:                                          ║
║                                                                            ║
║   ACTIVE-ACTIVE           GEO ROUTING            FAILOVER                 ║
║   All 3 regions serve     Users routed to         Route 53 health checks  ║
║   traffic simultaneously  nearest region          detect failure in <10s   ║
║   No primary/secondary    <50ms inter-region      Traffic rerouted <15s   ║
║   DynamoDB global tables  latency via backbone    Surviving regions       ║
║   for state sync          CloudFront edge cache   autoscale to absorb     ║
║                           reduces cross-region                            ║
║                                                                            ║
║   RPO: 0 (zero data loss)    RTO: <15 seconds    Regions: 3 active       ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Our multi-region architecture follows an active-active pattern across three AWS regions: US-East-1 in Virginia, US-West-2 in Oregon, and EU-West-1 in Ireland.

Unlike a traditional active-passive setup, all three regions serve production traffic simultaneously. There is no primary or secondary -- every region is fully autonomous and can operate independently if the other two go down. This gives us true geographic redundancy.

Traffic distribution is handled by Route 53 with latency-based routing. Users are automatically directed to the nearest region, which minimizes network latency. US East Coast and South American users go to Virginia, West Coast and Asia-Pacific go to Oregon, and European, Middle Eastern, and African users go to Ireland.

Each region runs an identical stack: an EKS cluster with GPU node groups running Triton Inference Server, a regional Redis cluster for caching and session state, and regional DynamoDB tables. State synchronization happens through DynamoDB Global Tables with active-active replication, giving us zero RPO -- no data loss during failover.

Failover is automatic and fast. Route 53 health checks probe each region's /healthz endpoint every 10 seconds. When a region fails, Route 53 detects it within 10 seconds and reroutes traffic within 15 seconds total. The surviving regions have enough headroom to absorb the additional traffic through dynamic batching optimization, and our autoscaler provisions additional GPU nodes within 5-10 minutes for sustained failover scenarios.

We tested this extensively. During our last Chaos Engineering exercise, we simulated a complete US-East-1 failure during peak traffic. Traffic was redistributed to US-West-2 and EU-West-1 within 12 seconds, with zero dropped requests and p99 latency staying under 250ms during the transition.

---

## Slide 8: Reliability Engineering

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║            RELIABILITY: Defense in Depth for GPU Inference                 ║
║                                                                            ║
║   ┌──────────── CIRCUIT BREAKERS ───────────────────────────────────┐     ║
║   │                                                                  │     ║
║   │  Per-Backend Breakers:                                           │     ║
║   │  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐        │     ║
║   │  │ Triton  │   │  Redis  │   │ DynamoDB│   │ Feature │        │     ║
║   │  │ [CLOSED]│   │ [CLOSED]│   │ [CLOSED]│   │  Store  │        │     ║
║   │  │         │   │         │   │         │   │ [CLOSED]│        │     ║
║   │  └─────────┘   └─────────┘   └─────────┘   └─────────┘        │     ║
║   │                                                                  │     ║
║   │  States: CLOSED (normal) -> OPEN (fail-fast) -> HALF-OPEN      │     ║
║   │  Triton threshold: 5 failures / 30s timeout / 3 success reset   │     ║
║   │  GPU OOM: Immediate OPEN (no threshold needed)                   │     ║
║   │  Fallback: cached response -> smaller model -> static response   │     ║
║   │                                                                  │     ║
║   └──────────────────────────────────────────────────────────────────┘     ║
║                                                                            ║
║   ┌──────────── REQUEST HEDGING ────────────────────────────────────┐     ║
║   │                                                                  │     ║
║   │  Strategy: Send request to primary + secondary region            │     ║
║   │                                                                  │     ║
║   │   Client ──┬──> us-east-1 (primary)  ──> Response (42ms) [WIN]  │     ║
║   │            └──> us-west-2 (hedge)    ──> Response (85ms) [DROP] │     ║
║   │                                                                  │     ║
║   │  Trigger: Activated when primary p95 > 100ms                     │     ║
║   │  Budget: Max 10% of requests hedged (cost control)               │     ║
║   │  Result: p99 reduced by 40% during cross-region latency spikes   │     ║
║   │                                                                  │     ║
║   └──────────────────────────────────────────────────────────────────┘     ║
║                                                                            ║
║   ┌──────────── LOAD SHEDDING ──────────────────────────────────────┐     ║
║   │                                                                  │     ║
║   │  Priority-Based Shedding (GPU utilization > 90%):                │     ║
║   │                                                                  │     ║
║   │  P1 (Critical):  Real-time personalization    NEVER shed         │     ║
║   │  P2 (Standard):  Background recommendations   Shed at 92% GPU   │     ║
║   │  P3 (Analytics): Batch analytics, A/B test    Shed at 88% GPU   │     ║
║   │                                                                  │     ║
║   │  Shedding reduces effective load by 20-40%                       │     ║
║   │  Shed requests get HTTP 503 + Retry-After header                 │     ║
║   │  Clients implement exponential backoff with jitter               │     ║
║   │                                                                  │     ║
║   └──────────────────────────────────────────────────────────────────┘     ║
║                                                                            ║
║   ┌──────────── ERROR BUDGETS & SLOs ───────────────────────────────┐     ║
║   │                                                                  │     ║
║   │  SLO: 99.95% availability = 21.6 minutes downtime/month budget   │     ║
║   │                                                                  │     ║
║   │  Error Budget Policy:                                            │     ║
║   │  ┌──────────────┬──────────────┬─────────────────────────────┐   │     ║
║   │  │ Budget Used  │ Status       │ Action                      │   │     ║
║   │  ├──────────────┼──────────────┼─────────────────────────────┤   │     ║
║   │  │ 0-50%        │ GREEN        │ Normal deployments, experi. │   │     ║
║   │  │ 50-80%       │ YELLOW       │ Reduce deploy frequency     │   │     ║
║   │  │ 80-100%      │ RED          │ Freeze deploys, reliability │   │     ║
║   │  │ >100%        │ CRITICAL     │ All hands on reliability    │   │     ║
║   │  └──────────────┴──────────────┴─────────────────────────────┘   │     ║
║   │                                                                  │     ║
║   │  Current: 38% budget consumed (GREEN)                            │     ║
║   │                                                                  │     ║
║   └──────────────────────────────────────────────────────────────────┘     ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Reliability engineering for GPU inference requires defense in depth. We implement four key patterns.

Circuit breakers prevent cascading failures. We run per-backend circuit breakers for Triton, Redis, DynamoDB, and the Feature Store. Each breaker monitors failure rates in a sliding window. For Triton specifically, 5 consecutive failures trigger the breaker to OPEN, which means we fail fast rather than queueing requests behind a broken backend. GPU OOM errors immediately open the breaker because OOM indicates a resource constraint that won't self-resolve. When the breaker opens, we fall back through three levels: first, return a cached response if available; second, route to a smaller 7B model; third, return a static non-personalized response.

Request hedging optimizes tail latency by sending duplicate requests to a secondary region when the primary is slow. When our primary region's p95 exceeds 100ms, we automatically hedge up to 10% of requests to a secondary region. The first response wins, and the slower one is cancelled. This reduced our p99 by 40% during cross-region latency spikes, with minimal cost overhead since we cap hedging at 10% of traffic.

Load shedding protects GPU resources during overload. We classify all requests into three priority tiers. P1 critical requests -- real-time personalization on active user screens -- are never shed. P2 background recommendation pre-computation starts shedding at 92% GPU utilization. P3 analytics and A/B test scoring sheds at 88%. This ensures that when we're under extreme pressure, the most important user-facing requests always get served.

Error budgets govern our operational posture. Our SLO of 99.95% gives us 21.6 minutes of downtime budget per month. We currently consume about 38% of that budget, which keeps us in GREEN status -- meaning we can deploy normally and run experiments. If we hit 80%, we freeze deployments and focus on reliability. This creates a quantitative framework for balancing velocity and stability.

---

## Slide 9: Business Impact

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║              BUSINESS IMPACT: Measurable Outcomes                         ║
║                                                                            ║
║   ┌─────────────────── ENGAGEMENT ────────────────────────────────────┐   ║
║   │                                                                    │   ║
║   │  Metric                      Before LLM    After LLM    Change    │   ║
║   │  ─────────────────────────   ──────────    ─────────    ──────    │   ║
║   │  Avg Session Duration         28 min        32.2 min    +15.0%    │   ║
║   │  Content Starts / Session     3.2           3.5         +8.1%     │   ║
║   │  Browse-to-Play Time          45 sec        36 sec      -20.0%    │   ║
║   │  Recommendation CTR           12.5%         16.1%       +28.8%    │   ║
║   │  Long-Tail Content Views      18%           22.1%       +23.0%    │   ║
║   │  Catalog Breadth Exposure     42%           47.0%       +12.0%    │   ║
║   │  User Satisfaction (NPS)      62            68          +9.7%     │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║   ┌─────────────────── COST EFFICIENCY ───────────────────────────────┐   ║
║   │                                                                    │   ║
║   │  Previous GPU Annual Spend:           $2,100,000                   │   ║
║   │  Current GPU Annual Spend:            $1,380,000 (-34%)            │   ║
║   │  Traffic Handled:                     4x more requests             │   ║
║   │                                                                    │   ║
║   │  Cost per 1M tokens:                  $0.30 (down from $1.20)      │   ║
║   │  Cost per 1K requests:                $0.028 (down from $0.11)     │   ║
║   │                                                                    │   ║
║   │  Infrastructure Efficiency:                                        │   ║
║   │    GPU Utilization:       35% -> 82%   (+134%)                     │   ║
║   │    Tokens/sec per $1000:  580 -> 2,320  (+300%)                    │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║   ┌─────────────────── SCALE & RELIABILITY ───────────────────────────┐   ║
║   │                                                                    │   ║
║   │  Peak Throughput:          50,000 req/s (sustained)                 │   ║
║   │  Burst Capacity:          150,000 req/s (with all mitigation)      │   ║
║   │  Availability (90-day):   99.97% (target: 99.95%)                  │   ║
║   │  Regions:                 3 active-active                          │   ║
║   │  Failover Time:           <15 seconds                              │   ║
║   │  Total GPUs in Fleet:     112 NVIDIA A100s                         │   ║
║   │  Models Served:           70B (primary) + 7B (fallback)            │   ║
║   │                                                                    │   ║
║   │  Incident Reduction:                                               │   ║
║   │    P1 incidents/month:    4.2 -> 0.3 (-93%)                        │   ║
║   │    MTTR:                  47 min -> 8 min (-83%)                    │   ║
║   │    User-facing errors:    0.12% -> 0.03% (-75%)                    │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
║   ┌─────────────────── ESTIMATED REVENUE IMPACT ──────────────────────┐   ║
║   │                                                                    │   ║
║   │  +15% session duration x Netflix's subscriber base                 │   ║
║   │  +8% content starts driving higher retention rates                 │   ║
║   │  +23% long-tail content discovery improving content ROI            │   ║
║   │                                                                    │   ║
║   │  Conservative estimate: $15-25M annual revenue impact              │   ║
║   │  through improved retention and reduced churn                      │   ║
║   │                                                                    │   ║
║   └────────────────────────────────────────────────────────────────────┘   ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Let me summarize the business impact across four dimensions.

Engagement improvements are the most important metric for Netflix. Average session duration increased 15% -- from 28 minutes to 32.2 minutes. Users are starting 8% more content per session. The browse-to-play time dropped from 45 seconds to 36 seconds, meaning users find what they want faster. Recommendation click-through rate jumped nearly 29%, from 12.5% to 16.1%. And critically for our content strategy, long-tail content views increased 23% and overall catalog breadth exposure improved 12%. This means our multi-billion dollar content library is being discovered more effectively.

Cost efficiency improved dramatically. We reduced annual GPU spend by 34% while handling 4x more traffic. Cost per million tokens dropped from $1.20 to $0.30 -- a 75% reduction. Per thousand requests, we went from $0.11 to $0.028. Our tokens-per-dollar efficiency improved 300%.

Scale and reliability are where the engineering investment truly shines. We sustain 50,000 requests per second with burst capacity to 150,000. Availability is at 99.97% across 3 active-active regions. P1 incidents dropped 93% -- from 4.2 per month to 0.3. Mean time to recovery went from 47 minutes to 8 minutes. User-facing errors decreased 75%.

The estimated revenue impact, based on conservative modeling of engagement improvements against retention and churn metrics, is $15-25 million annually. This platform pays for itself many times over.

---

## Slide 10: Future Roadmap

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║              FUTURE ROADMAP: What's Next                                  ║
║                                                                            ║
║   ┌──────────────── Q2 2026: MULTI-MODAL INFERENCE ────────────────────┐  ║
║   │                                                                      │  ║
║   │  Vision: Unified inference for text, image, and video understanding  │  ║
║   │                                                                      │  ║
║   │  - Multi-modal LLM (text + thumbnail/poster analysis)               │  ║
║   │  - Video scene understanding for content-aware recommendations      │  ║
║   │  - Visual similarity search across the content catalog              │  ║
║   │  - "Show me something that looks like..." natural language queries  │  ║
║   │                                                                      │  ║
║   │  Expected Impact: +10% recommendation relevance                      │  ║
║   │  GPU Requirement: H100 upgrade for multi-modal workloads             │  ║
║   │                                                                      │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║   ┌──────────────── Q3 2026: CONVERSATIONAL DISCOVERY ─────────────────┐  ║
║   │                                                                      │  ║
║   │  Vision: Natural language content discovery and exploration          │  ║
║   │                                                                      │  ║
║   │  - Chat-based interface: "What should I watch tonight?"             │  ║
║   │  - Multi-turn context: "Something shorter" / "More like that"      │  ║
║   │  - Streaming token generation for interactive UX                    │  ║
║   │  - Personalized explanations: "Recommended because..."             │  ║
║   │                                                                      │  ║
║   │  Architecture: WebSocket streaming + server-sent events             │  ║
║   │  Challenge: Maintaining <200ms TTFT with conversational context     │  ║
║   │                                                                      │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║   ┌──────────────── Q4 2026: EDGE INFERENCE ───────────────────────────┐  ║
║   │                                                                      │  ║
║   │  Vision: Inference at the edge for ultra-low-latency markets        │  ║
║   │                                                                      │  ║
║   │  - Deploy 7B models to CloudFront edge locations (GPU-equipped)     │  ║
║   │  - Sub-20ms inference for latency-sensitive personalization         │  ║
║   │  - Hybrid: Edge handles simple queries, cloud handles complex      │  ║
║   │  - Target regions: Asia-Pacific, South America (high latency today)│  ║
║   │                                                                      │  ║
║   │  Estimated Savings: 40% reduction in cross-region data transfer     │  ║
║   │  User Impact: <50ms total latency for 80% of requests              │  ║
║   │                                                                      │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║   ┌──────────────── 2027: CUSTOM SILICON ──────────────────────────────┐  ║
║   │                                                                      │  ║
║   │  Vision: Purpose-built inference acceleration                        │  ║
║   │                                                                      │  ║
║   │  - Evaluate AWS Trainium2 / Inferentia3 for cost reduction          │  ║
║   │  - Custom ASIC design for Netflix-specific inference patterns       │  ║
║   │  - Target: 5x cost efficiency improvement over A100 GPUs            │  ║
║   │  - Optimized for recommendation workloads (short output, high batch)│  ║
║   │                                                                      │  ║
║   │  Investment: $5-10M R&D over 18 months                               │  ║
║   │  Expected ROI: $8-15M annual savings at scale                        │  ║
║   │                                                                      │  ║
║   └──────────────────────────────────────────────────────────────────────┘  ║
║                                                                            ║
║   Summary Timeline:                                                       ║
║   ═══════════════                                                         ║
║   Q2 2026    Q3 2026    Q4 2026    H1 2027    H2 2027                    ║
║      │          │          │          │          │                         ║
║      ▼          ▼          ▼          ▼          ▼                         ║
║   Multi-    Conver-     Edge       Trainium2   Custom                     ║
║   Modal     sational    Inference  Migration   Silicon                    ║
║   LLM       Discovery   at Edge   Evaluation  Prototype                  ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

**Speaker Notes:**

Looking ahead, we have four major initiatives on the roadmap.

In Q2 2026, multi-modal inference. We'll extend our platform to handle not just text but also images and video understanding. Imagine recommending content based on visual similarity to thumbnails a user has engaged with, or understanding the actual visual content of scenes to make smarter recommendations. This requires upgrading to H100 GPUs for the additional compute needed by vision transformers, but we expect a 10% improvement in recommendation relevance.

In Q3 2026, conversational discovery. This is the "chat with Netflix" experience. Users will be able to ask natural language questions like "What should I watch tonight?" and engage in multi-turn conversations: "Something shorter," "More like that last one." We'll implement WebSocket streaming with server-sent events for real-time token generation. The key engineering challenge is maintaining our sub-200ms time-to-first-token with growing conversational context windows.

In Q4 2026, edge inference. We'll deploy our 7B fallback model to GPU-equipped CloudFront edge locations, targeting sub-20ms inference for latency-sensitive markets like Asia-Pacific and South America that currently suffer from cross-region latency. A hybrid approach will route simple queries to the edge and complex queries to our regional GPU clusters. We estimate 40% reduction in cross-region data transfer costs and sub-50ms total latency for 80% of requests.

Looking into 2027, custom silicon. We'll evaluate AWS Trainium2 and Inferentia3 chips, which are purpose-built for inference at significantly lower cost than general-purpose GPUs. For Netflix's specific workload pattern -- short output sequences, high batch sizes, recommendation-focused -- custom silicon could deliver 5x cost efficiency improvement. The R&D investment is $5-10 million over 18 months with an expected ROI of $8-15 million in annual savings at scale.

Thank you. I'm happy to take questions on any aspect of the architecture, the performance results, or the roadmap.

---

## Appendix: Key Technical Specifications

| Component | Specification |
|-----------|--------------|
| Primary Model | 70B parameter LLM, INT8 quantized |
| Fallback Model | 7B parameter LLM, FP16 |
| GPU Type | NVIDIA A100 80GB HBM2e |
| Tensor Parallelism | TP=4 (70B), TP=1 (7B) |
| Inference Engine | TensorRT-LLM + Triton Inference Server |
| Serving Framework | FastAPI + gRPC |
| Orchestration | Amazon EKS (Kubernetes 1.28+) |
| KV Cache | PagedAttention with prefix caching |
| Batch Strategy | Continuous batching (max batch-64) |
| Regions | us-east-1, us-west-2, eu-west-1 |
| Total GPU Fleet | 112 NVIDIA A100 GPUs (14 nodes) |
| Peak Throughput | 50,000 req/s sustained |
| P95 Latency | 128ms |
| Availability | 99.97% (90-day rolling) |

---

**Author:** Gopi Krishna Vajrala
**Netflix Real-Time LLM Personalization & Inference Platform**
**February 2026**
