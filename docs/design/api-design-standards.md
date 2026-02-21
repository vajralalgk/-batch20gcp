# Netflix Real-Time LLM Personalization & Inference Platform
# API Design Standards

**Document ID:** NFLX-LLM-API-001
**Author:** Gopi Krishna Vajrala
**Version:** 3.0.0
**Date:** 2026-02-21

---

## Table of Contents

1. Protocol Selection: gRPC vs REST
2. Latency Budget Allocation
3. Inference API Endpoints
4. Request / Response Format
5. Status Codes
6. Error Handling Standards
7. Rate Limiting Policies
8. Authentication
9. Versioning Strategy
10. Observability Standards
11. Idempotency & Request Safety
12. Request Size & Payload Limits
13. Circuit Breaking Rules
14. SLA vs SLO Definitions with Error Budgets
15. GPU Queueing Model
16. Backpressure Handling
17. Adaptive Dynamic Batching Policy
18. Model Canary Deployment
19. Failure Scenario Simulation

---

## 1. Protocol Selection: gRPC vs REST

### Decision Matrix

| Criteria | gRPC | REST (HTTP/JSON) | Decision |
|----------|------|-------------------|----------|
| **Real-time inference** | Preferred (binary, streaming) | Acceptable | **gRPC** for internal services |
| **Client-facing API** | Complex client setup | Universal support | **REST** for external clients |
| **Batch inference** | Bidirectional streaming | Single request/response | **gRPC** for batch |
| **Model status / health** | Overkill | Simple and sufficient | **REST** for health/status |
| **Latency (p99)** | ~2ms overhead | ~5ms overhead | **gRPC** for latency-critical |
| **Observability** | Requires interceptors | Native HTTP tracing | **REST** for observability |

### Protocol Assignment

| Endpoint Category | Protocol | Justification |
|-------------------|----------|---------------|
| Real-time prediction | gRPC | Sub-50ms latency budget; binary protobuf reduces serialization overhead |
| Batch prediction | gRPC | Bidirectional streaming for large batch jobs |
| Model management | REST | Low frequency; developer ergonomics preferred |
| Health checks | REST | Standard HTTP health checks for load balancers |
| Metrics / status | REST | Prometheus-compatible scraping |
| External client API | REST | Universal client compatibility (mobile, web, microservices) |

---

## 2. Latency Budget Allocation

Total end-to-end latency target: **50ms (p99)**

```
LATENCY BUDGET BREAKDOWN (50ms total)
================================================================

Component                 Budget (ms)    Target p99 (ms)
----------------------------------------------------------------
TLS termination              1                0.5
API Gateway (auth, routing)  2                1.0
Request validation           1                0.5
Feature assembly (cache)     5                3.0
Prediction cache lookup      1                0.5
GPU inference (forward pass) 30               25.0
Post-processing              3                2.0
Response serialization       2                1.5
Network overhead             5                3.0
----------------------------------------------------------------
TOTAL                       50               37.0

================================================================
```

### Latency Monitoring

| Metric | Alert Threshold | Escalation |
|--------|----------------|------------|
| p50 latency | > 15ms | Slack notification |
| p90 latency | > 35ms | Slack + on-call page |
| p99 latency | > 50ms | PagerDuty P2 |
| p99.9 latency | > 100ms | PagerDuty P1 |
| GPU inference time | > 30ms | ML team notification |

---

## 3. Inference API Endpoints

### 3.1 REST Endpoints

```
BASE URL: https://llm-inference-{region}.netflix.internal/v1
================================================================

POST /v1/predict              Real-time single prediction
POST /v1/recommend            Content recommendation list
POST /v1/embed                Generate embedding vector
POST /v1/batch                Batch prediction (async)
GET  /v1/models               List loaded models and status
GET  /v1/models/{model_id}    Get specific model details
POST /v1/canary/start         Start canary deployment
POST /v1/canary/rollback      Rollback canary deployment
GET  /v1/canary/status        Get canary deployment status
GET  /v1/queue/status         GPU queue depth and metrics
GET  /v1/slo/status           SLO compliance dashboard
GET  /health                  Health check (HTTP 200 = healthy)
GET  /health/ready            Readiness probe (GPU loaded)
GET  /health/live             Liveness probe (process alive)
GET  /metrics                 Prometheus metrics endpoint

================================================================
```

### 3.2 gRPC Service Definition

```protobuf
service InferenceService {
  // Real-time single prediction (< 50ms p99)
  rpc Predict(PredictRequest) returns (PredictResponse);

  // Batch prediction via streaming
  rpc BatchPredict(stream PredictRequest) returns (stream PredictResponse);

  // Generate embedding vector
  rpc Embed(EmbedRequest) returns (EmbedResponse);

  // Health check (gRPC health protocol)
  rpc Check(HealthCheckRequest) returns (HealthCheckResponse);
}

service ModelManagementService {
  // Canary deployment control
  rpc StartCanary(CanaryRequest) returns (CanaryResponse);
  rpc RollbackCanary(RollbackRequest) returns (RollbackResponse);
  rpc GetCanaryStatus(CanaryStatusRequest) returns (CanaryStatusResponse);
}
```

---

## 4. Request / Response Format

### 4.1 Prediction Request (REST)

```json
POST /v1/predict
Content-Type: application/json
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000

{
    "user_id": "u-12345678",
    "context": {
        "page": "homepage",
        "device": "smart_tv",
        "time_of_day": "evening",
        "session_id": "sess-abc123"
    },
    "content_ids": ["tt-001", "tt-002", "tt-003"],
    "model_version": "v2.3.0",
    "options": {
        "num_results": 10,
        "diversity_factor": 0.3,
        "dry_run": false
    }
}
```

### 4.2 Prediction Response

```json
{
    "request_id": "req-7f8a9b0c",
    "predictions": [
        {
            "content_id": "tt-001",
            "score": 0.95,
            "rank": 1,
            "explanation": "Based on viewing history and genre preference"
        },
        {
            "content_id": "tt-003",
            "score": 0.87,
            "rank": 2,
            "explanation": "Trending in similar user cohort"
        }
    ],
    "metadata": {
        "model_version": "v2.3.0",
        "inference_time_ms": 23.4,
        "cache_hit": false,
        "gpu_id": "gpu-0",
        "region": "us-east-1"
    },
    "timing": {
        "total_ms": 31.2,
        "feature_assembly_ms": 3.1,
        "inference_ms": 23.4,
        "post_processing_ms": 2.8,
        "serialization_ms": 1.9
    }
}
```

### 4.3 Error Response

```json
{
    "error": {
        "code": "MODEL_UNAVAILABLE",
        "message": "Requested model version v2.4.0 is not loaded",
        "details": {
            "requested_version": "v2.4.0",
            "available_versions": ["v2.3.0", "v2.2.5"],
            "region": "us-east-1"
        },
        "request_id": "req-7f8a9b0c",
        "timestamp": "2026-02-21T12:00:00.000Z"
    }
}
```

---

## 5. Status Codes

### Success Codes (2xx)

| Code | Name | Usage |
|------|------|-------|
| 200 | OK | Successful prediction, model status query |
| 207 | Multi-Status | Batch prediction with partial results |

### Client Error Codes (4xx)

| Code | Name | Usage |
|------|------|-------|
| 400 | Bad Request | Malformed request body, invalid user_id format |
| 401 | Unauthorized | Missing or invalid authentication token |
| 403 | Forbidden | Insufficient permissions for requested model |
| 404 | Not Found | Unknown model version or user_id |
| 408 | Request Timeout | Inference exceeded latency budget |
| 409 | Conflict | Duplicate request (idempotency key in PENDING state) |
| 413 | Payload Too Large | Request body exceeds MAX_REQUEST_SIZE (2 MB) |
| 422 | Unprocessable Entity | Valid JSON but invalid inference parameters |
| 429 | Too Many Requests | Rate limit exceeded |

### Server Error Codes (5xx)

| Code | Name | Usage |
|------|------|-------|
| 500 | Internal Server Error | Unexpected inference failure |
| 502 | Bad Gateway | GPU node unreachable |
| 503 | Service Unavailable | Model loading in progress, GPU overloaded |
| 504 | Gateway Timeout | Inference timeout (GPU hang) |

---

## 6. Error Handling Standards

### Error Code Registry

| Error Code | HTTP Status | Description | Client Action |
|-----------|------------|-------------|---------------|
| INVALID_REQUEST | 400 | Malformed request payload | Fix request format |
| AUTH_REQUIRED | 401 | No authentication provided | Provide valid token |
| PERMISSION_DENIED | 403 | Insufficient access rights | Request access from admin |
| MODEL_NOT_FOUND | 404 | Model version does not exist | Use available version |
| DUPLICATE_REQUEST | 409 | Idempotency key already processing | Wait for original to complete |
| PAYLOAD_TOO_LARGE | 413 | Request body exceeds size limit | Reduce payload size |
| MODEL_UNAVAILABLE | 503 | Model not loaded on GPU | Retry with backoff |
| GPU_OVERLOADED | 503 | All GPU resources busy | Retry with backoff |
| INFERENCE_TIMEOUT | 504 | GPU inference timed out | Retry or reduce batch size |
| RATE_LIMITED | 429 | Client rate limit exceeded | Wait and retry per Retry-After |
| FEATURE_STORE_ERROR | 502 | Cannot reach feature store | Retry; fallback to default features |
| INTERNAL_ERROR | 500 | Unexpected server error | Retry; report if persistent |

### Retry Policy

| Error Category | Retry | Backoff | Max Retries |
|---------------|-------|---------|-------------|
| 429 Rate Limited | Yes | Respect Retry-After header | 3 |
| 503 Unavailable | Yes | Exponential (100ms, 200ms, 400ms) | 3 |
| 504 Timeout | Yes | Exponential with jitter | 2 |
| 500 Internal | Yes (idempotent only) | Exponential | 2 |
| 409 Conflict | Yes | Poll every 500ms | 5 |
| 4xx Client Error | No | N/A | 0 |

---

## 7. Rate Limiting Policies

### Per-Client Limits

| Client Tier | Rate Limit | Burst Limit | Quota (daily) |
|------------|-----------|-------------|---------------|
| Internal service (P0) | 50,000 req/min | 100,000 req/min | Unlimited |
| Internal service (P1) | 10,000 req/min | 20,000 req/min | 50M req/day |
| External partner | 1,000 req/min | 2,000 req/min | 5M req/day |
| Development / testing | 100 req/min | 200 req/min | 100K req/day |

### Rate Limit Headers

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 50000
X-RateLimit-Remaining: 49234
X-RateLimit-Reset: 1708099200
X-RateLimit-Policy: internal-p0

HTTP/1.1 429 Too Many Requests
Retry-After: 5
X-RateLimit-Limit: 50000
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1708099200
```

---

## 8. Authentication

| Method | Use Case | Header | Token Type |
|--------|----------|--------|------------|
| **mTLS** | Service-to-service (inference) | TLS client certificate | X.509 cert |
| **Bearer Token (JWT)** | External API calls | `Authorization: Bearer <token>` | JWT via Netflix SSO |
| **API Key** | Partner integrations | `X-API-Key: <key>` | Managed API key |

---

## 9. Versioning Strategy

| Rule | Description |
|------|-------------|
| URL-based versioning | `/v1/predict`, `/v2/predict` |
| Major versions only | New version only for breaking changes |
| Model version in request | Client specifies desired model version in request body |
| Deprecation period | 6 months minimum before removing old API version |
| Sunset header | `Sunset: Sat, 01 Jan 2028 00:00:00 GMT` on deprecated versions |

---

## 10. Observability Standards

### Required Headers

Every response must include:

```http
X-Request-Id: req-7f8a9b0c         # Unique request identifier
X-Inference-Time-Ms: 23.4          # GPU inference time
X-Model-Version: v2.3.0            # Model used for prediction
X-Cache-Status: MISS               # HIT or MISS
X-Region: us-east-1                # Serving region
X-Idempotency-Key: 550e8400-...    # Echoed idempotency key
X-Idempotency-Status: new          # new | cached | duplicate
X-Queue-Depth: 42                  # Current GPU queue depth
X-Pressure-Level: NORMAL           # NORMAL | ELEVATED | HIGH | CRITICAL
```

### Distributed Tracing

- All requests propagate OpenTelemetry trace context (`traceparent` header)
- Spans recorded for: API Gateway, feature assembly, cache lookup, GPU inference, post-processing
- Trace data exported to Jaeger / Tempo for analysis

---

## 11. Idempotency & Request Safety

### Overview

GPU inference is expensive and non-reentrant. A single spurious retry of a 512-sample
batch can exhaust an A100's 80 GB HBM budget and cascade-fail co-located workers.
The idempotency layer prevents double GPU execution on client retries.

### Endpoint Classification

| Endpoint | Method | Idempotent | Safe Retry | Dedup Key |
|----------|--------|------------|------------|-----------|
| `/v1/predict` | POST | Yes | Yes | `Idempotency-Key` header |
| `/v1/recommend` | POST | Yes | Yes | `Idempotency-Key` header |
| `/v1/embed` | POST | Yes | Yes | `Idempotency-Key` header |
| `/v1/batch` | POST | Yes | Yes | `request_id` in body |
| `/v1/models` | GET | N/A | Always safe | N/A (read-only) |
| `/health/*` | GET | N/A | Always safe | N/A (read-only) |

### Idempotency-Key Header

```http
POST /v1/predict
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
Content-Type: application/json
```

- **Format:** UUID v4 (RFC 4122). Requests with non-UUID keys receive `400 Bad Request`.
- **Scope:** Key is scoped to the authenticated client (mTLS identity or JWT `sub` claim).
- **TTL:** Records expire after **24 hours**, matching the upstream SLA retry window.
- **Required:** All mutating endpoints (POST/PUT/PATCH/DELETE) must include the header.

### State Machine

```
                ┌─────────────────────────────────────────────┐
                │                                             │
  New Request   │     PENDING ──────────────► COMPLETED       │
  ─────────────►│       │                        │            │
                │       │ (failure)               │ (24h TTL) │
                │       ▼                        ▼            │
                │     FAILED ──► (retry allowed)  Evicted     │
                │                                             │
                └─────────────────────────────────────────────┘
```

| State | Behavior on Duplicate Request |
|-------|-------------------------------|
| **PENDING** | Return `409 Conflict` with `Retry-After: 1` header |
| **COMPLETED** | Replay cached response (same status code, body, headers) |
| **FAILED** | Delete record, allow retry as new request |

### Request Fingerprinting

```
fingerprint = SHA-256(method + path + body_hash + idempotency_key)
```

If a second request arrives with the same `Idempotency-Key` but a different fingerprint,
the server returns `422 Unprocessable Entity` with error code `IDEMPOTENCY_MISMATCH`.

### GPU Execution Safety

The idempotency check executes **before** GPU memory allocation. This prevents:
- Double allocation of KV cache for the same prompt
- Duplicate batch submissions consuming 2x GPU slots
- Race conditions between retried and in-flight requests

---

## 12. Request Size & Payload Limits

### Global Constraints

| Constraint | Value | Rationale |
|-----------|-------|-----------|
| `MAX_REQUEST_SIZE` | 2 MB | Prevents memory exhaustion at API gateway |
| `MAX_PROMPT_TOKENS` | 4,096 | KV cache allocation grows linearly with sequence length |
| `MAX_BATCH_SIZE` | 128 | A100 80GB supports ~128 concurrent sequences at 4K tokens |
| `MAX_STREAMING_WINDOW` | 10 MB | Caps cumulative streaming response size |
| `MAX_CONTENT_IDS` | 500 | Limits feature assembly fan-out |
| `MAX_EMBEDDING_DIM` | 2,048 | Caps output vector size |
| `MAX_CONCURRENT_REQUESTS` | 1,000 | Per-GPU admission control |

### Per-Endpoint Overrides

| Endpoint | Max Body Size | Max Batch | Max Tokens |
|----------|--------------|-----------|------------|
| `/v1/predict` | 512 KB | 1 | 4,096 |
| `/v1/recommend` | 256 KB | 1 | 2,048 |
| `/v1/embed` | 1 MB | 64 | 8,192 |
| `/v1/batch` | 2 MB | 128 | 4,096 |

### Validation Flow

```
Request arrives
    │
    ▼
┌─────────────────────┐
│ Check Content-Length │──► > MAX_REQUEST_SIZE? ──► 413 Payload Too Large
│ header              │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Parse JSON body     │──► Malformed? ──► 400 Bad Request
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Validate token count│──► > MAX_PROMPT_TOKENS? ──► 422 Unprocessable Entity
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Validate batch size │──► > MAX_BATCH_SIZE? ──► 422 Unprocessable Entity
└─────────┬───────────┘
          │
          ▼
    Forward to GPU queue
```

### GPU Memory Protection

These limits directly protect GPU VRAM:
- **KV cache:** Each token requires ~2 KB per layer. At 32 layers, a 4,096-token sequence
  consumes ~256 MB. Capping at 128 concurrent sequences uses ~32 GB of the 80 GB budget.
- **Activation memory:** Reserved 16 GB for model weights + optimizer states.
- **Safety margin:** 32 GB remaining for dynamic allocation, batch overhead, and system use.

---

## 13. Circuit Breaking Rules

### Production Rules

| # | Rule Name | Service | Trigger Metric | Trigger Condition | Action |
|---|-----------|---------|---------------|-------------------|--------|
| 1 | `triton_inference_slow` | Triton | `triton_inference_p95_ms` | p95 > 200ms sustained 30s | Shed non-critical, fallback to cached predictions |
| 2 | `triton_5xx_rate` | Triton | `triton_5xx_rate_pct` | > 5% sustained 60s | Open circuit, divert to embedding fallback |
| 3 | `redis_session_timeout` | Redis | `redis_latency_p95_ms` | p95 > 50ms sustained 15s | Bypass cache, direct inference |
| 4 | `dynamodb_feature_slow` | DynamoDB | `dynamodb_latency_p99_ms` | p99 > 100ms sustained 20s | Use local feature cache |
| 5 | `gpu_queue_overflow` | GPU Queue | `gpu_queue_depth` | depth > 256 sustained 10s | Reject BEST_EFFORT, throttle STANDARD |
| 6 | `cross_region_hedge_fail` | Cross-Region | `hedge_failure_rate_pct` | > 30% sustained 45s | Disable hedging, local-only serving |
| 7 | `kv_cache_pressure` | KV Cache | `kv_cache_utilization_pct` | > 90% sustained 10s | Shrink batch, preempt BEST_EFFORT |
| 8 | `model_accuracy_drop` | Model | `model_accuracy_pct` | drop > 5% sustained 300s | Trigger canary rollback |

### Fallback Chain

```
PRIMARY FALLBACK CHAIN
================================================================

Level 0: LLM Inference (full model)
    │
    ▼ (circuit open)
Level 1: Cached Predictions
    │     - Serve most recent prediction for this user
    │     - TTL: 15 minutes
    │
    ▼ (cache miss)
Level 2: Embedding Similarity
    │     - Pre-computed content embeddings
    │     - Cosine similarity ranking
    │     - Latency: ~5ms
    │
    ▼ (embedding unavailable)
Level 3: Popular/Trending Content
          - Region-specific trending list
          - Updated every 5 minutes
          - Zero-dependency fallback

================================================================
```

### Circuit Breaker State Machine

```
        ┌──────────────────────────────────────────────────────┐
        │                                                      │
        │   CLOSED ────────────────────► OPEN                  │
        │     ▲    (threshold breached)    │                   │
        │     │                            │ (recovery_timeout)│
        │     │    (probes succeed)        ▼                   │
        │     └──────────────────── HALF_OPEN                  │
        │                              │                       │
        │                              │ (any probe fails)     │
        │                              └──────► OPEN           │
        │                                                      │
        └──────────────────────────────────────────────────────┘
```

### CircuitBreakerOrchestrator

The orchestrator evaluates all 8 rules against live Prometheus metrics every 5 seconds.
Multiple circuits can be open simultaneously (e.g., Redis timeout + GPU queue overflow).
Each circuit has independent recovery timeouts and half-open probe counts.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | Rule-specific | Consecutive failures to trip |
| `recovery_timeout` | 30s - 300s | Wait before half-open probes |
| `half_open_max_calls` | 3 | Probe calls allowed in half-open |
| `load_shed_probability` | 0.5 | Probability of shedding vs hedging |

---

## 14. SLA vs SLO Definitions with Error Budgets

### SLO Catalogue (Internal Objectives)

| SLO Name | Metric | Target | Window | Budget |
|----------|--------|--------|--------|--------|
| Availability | Successful requests / total | 99.95% | 30 days | 21.6 min/month |
| Latency p95 | 95th percentile response time | < 200ms | 30 days | N/A |
| Latency p99 | 99th percentile response time | < 250ms | 30 days | N/A |
| Time to First Token | Streaming first token latency | < 100ms | 30 days | N/A |
| Throughput | Tokens per second per GPU | > 3,000 | 30 days | N/A |

### External SLA Guarantees

| SLA Metric | Target | Penalty Tier | Financial Impact |
|-----------|--------|-------------|------------------|
| Availability | 99.9% (43.2 min/month budget) | Tier 1: 99.5-99.9% | 10% service credit |
| | | Tier 2: 99.0-99.5% | 25% service credit |
| | | Tier 3: < 99.0% | 50% service credit |
| Latency p95 | < 250ms | > 250ms for 5+ min | SLA violation event |
| Latency p99 | < 500ms | > 500ms for 5+ min | SLA violation event |

### Error Budget Calculation

```
ERROR BUDGET FORMULA
================================================================

error_budget_minutes = (1 - SLO_target) * window_hours * 60

Example (99.95% SLO, 30-day window):
  budget = (1 - 0.9995) * 720 * 60 = 21.6 minutes

Monthly breakdown:
  Total budget:     21.6 minutes
  Daily allowance:  ~0.72 minutes (43.2 seconds)
  Hourly allowance: ~0.03 minutes (1.8 seconds)

================================================================
```

### Burn Rate Alerts

| Alert Type | Burn Rate | Detection Window | Action |
|-----------|-----------|-----------------|--------|
| **Fast burn (page)** | 14.4x normal | 5 minutes | PagerDuty P1, halt all deployments |
| **Slow burn (ticket)** | 6x normal | 30 minutes | Jira ticket, notify on-call |
| **Trend warning** | 3x normal | 6 hours | Slack alert, review capacity |

### Deployment Gates

| Budget Remaining | Gate Status | Policy |
|-----------------|-------------|--------|
| > 75% | **ALLOW** | All deployments permitted |
| 50% - 75% | **WARN** | Non-critical deployments require TL approval |
| 25% - 50% | **FREEZE** | All deploys frozen; incident response only |
| < 25% | **EMERGENCY** | VP approval required; active incident management |

```
DEPLOYMENT GATE FLOW
================================================================

  Error Budget ─────► Calculate Remaining %
                           │
           ┌───────────────┼───────────────┐
           │               │               │
       > 75%          50-75%          25-50%          < 25%
           │               │               │              │
        ALLOW           WARN           FREEZE        EMERGENCY
           │               │               │              │
     All deploys      TL approval     Incident       VP approval
     permitted        required        response       required

================================================================
```

---

## 15. GPU Queueing Model

### Multi-Level Feedback Queue (MLFQ)

```
GPU QUEUE ARCHITECTURE
================================================================

  Incoming requests
        │
        ▼
  ┌──────────────┐
  │  Admission   │──► Queue full? ──► 503 Service Unavailable
  │  Control     │
  └──────┬───────┘
         │
    Priority classification
         │
    ┌────┴────┬──────────┬──────────────┐
    ▼         ▼          ▼              ▼
┌────────┐┌────────┐┌──────────┐┌─────────────┐
│CRITICAL││PREMIUM ││ STANDARD ││ BEST_EFFORT │
│ (P0)   ││ (P1)   ││  (P2)    ││    (P3)     │
│ W=8    ││ W=4    ││  W=2     ││    W=1      │
└────┬───┘└────┬───┘└────┬─────┘└──────┬──────┘
     │         │         │             │
     └────┬────┴────┬────┴──────┬──────┘
          │         │           │
          ▼         ▼           ▼
    Weighted Round-Robin Scheduler
          │
          ▼
    GPU Execution Slot

================================================================
```

### Priority Levels

| Priority | Weight | Use Case | Max Queue Depth | Preemptible |
|----------|--------|----------|----------------|-------------|
| CRITICAL (P0) | 8 | A/B test serving, live experiments | 64 | No |
| PREMIUM (P1) | 4 | Real-time personalization, homepage | 64 | No |
| STANDARD (P2) | 2 | General recommendations, search | 64 | Yes (by P0) |
| BEST_EFFORT (P3) | 1 | Batch pre-computation, warm cache | 64 | Yes (by P0/P1) |

### Queue Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_total_depth` | 256 | Total across all priority levels |
| `max_per_priority` | 64 | Per-priority-level cap |
| `starvation_timeout_ms` | 500 | Promote to next priority after waiting |
| `preemption_enabled` | true | CRITICAL can preempt lower priorities |
| `scheduling_algorithm` | Weighted Round-Robin | Weights: 8/4/2/1 |

### Admission Control

When total queue depth exceeds thresholds:

| Queue Depth | Action |
|------------|--------|
| 0 - 192 (75%) | Accept all priorities |
| 192 - 230 (90%) | Reject BEST_EFFORT |
| 230 - 250 (98%) | Reject BEST_EFFORT + STANDARD |
| 250 - 256 (100%) | CRITICAL and PREMIUM only |

### Starvation Prevention

Requests waiting longer than `starvation_timeout_ms` (500ms) are automatically
promoted to the next higher priority level. This ensures BEST_EFFORT requests
are eventually served even under sustained high-priority load.

```
Starvation prevention timeline:
  t=0ms     BEST_EFFORT enqueued at P3
  t=500ms   Promoted to STANDARD (P2)
  t=1000ms  Promoted to PREMIUM (P1)
  t=1500ms  Promoted to CRITICAL (P0) -- guaranteed execution
```

---

## 16. Backpressure Handling

### Pressure Levels

| Level | GPU Util | Queue Depth | Error Rate | Actions |
|-------|----------|-------------|-----------|---------|
| **NORMAL** | < 70% | < 128 | < 1% | Normal operation |
| **ELEVATED** | 70-85% | 128-192 | 1-3% | Adaptive batch shrink |
| **HIGH** | 85-95% | 192-230 | 3-5% | Traffic shedding + regional rebalance |
| **CRITICAL** | > 95% | > 230 | > 5% | Emergency circuit break |

### Pressure Signal Inputs

```
PRESSURE EVALUATION
================================================================

Signals consumed every 5 seconds:

  gpu_utilization_pct       ──┐
  gpu_memory_utilization_pct──┤
  sm_occupancy_pct          ──┤
  queue_depth_total         ──┼──► Pressure
  queue_growth_rate_per_sec ──┤    Evaluator  ──► Pressure Level
  error_rate_5m_pct         ──┤
  feature_store_latency_ms  ──┤
  regional_health_score     ──┘

================================================================
```

### Adaptive Batch Shrink (ELEVATED)

When pressure reaches ELEVATED, the batch size is progressively reduced:

| Batch Signal | Shrink Factor | New Max Batch | Rationale |
|-------------|--------------|---------------|-----------|
| GPU util 70-75% | 0.90x | 115 | Gentle reduction |
| GPU util 75-80% | 0.80x | 102 | Moderate reduction |
| GPU util 80-85% | 0.70x | 89 | Aggressive reduction |
| p99 latency > SLO | 0.50x | 64 | Emergency reduction |

### Traffic Shedding Policy (HIGH)

| Priority | Action at HIGH | Action at CRITICAL |
|----------|---------------|-------------------|
| CRITICAL (P0) | Serve normally | Serve with reduced batch |
| PREMIUM (P1) | Serve normally | Serve with cached fallback |
| STANDARD (P2) | 30% shed rate | 80% shed rate |
| BEST_EFFORT (P3) | 100% shed (queue) | 100% rejected |

### Regional Rebalancing (HIGH)

When a region enters HIGH pressure, the API gateway redistributes traffic:

```
REGIONAL REBALANCING
================================================================

Before:  us-east-1 (85% util) ← 40% traffic
         us-west-2 (50% util) ← 30% traffic
         eu-west-1 (55% util) ← 30% traffic

After:   us-east-1 (70% util) ← 25% traffic  (-15%)
         us-west-2 (65% util) ← 38% traffic  (+8%)
         eu-west-1 (63% util) ← 37% traffic  (+7%)

================================================================
```

### Graceful Degradation Matrix

| Pressure Level | Batch Size | Features | Model | Cache | Latency Target |
|---------------|-----------|----------|-------|-------|---------------|
| NORMAL | 128 | Full features | Full LLM | Optional | 50ms p99 |
| ELEVATED | 64-128 | Full features | Full LLM | Preferred | 75ms p99 |
| HIGH | 32-64 | Cached features | Smaller model | Required | 100ms p99 |
| CRITICAL | 1-32 | Default features | Embedding only | Required | 150ms p99 |

---

## 17. Adaptive Dynamic Batching Policy

### Decision Engine

The adaptive batching policy engine continuously adjusts batch sizes based on
real-time GPU telemetry and latency SLOs. It maintains a rolling history of
decisions and outcomes to auto-tune its own thresholds.

### Input Signals

| Signal | Source | Range | Weight |
|--------|--------|-------|--------|
| `gpu_utilization_pct` | DCGM Exporter | 0-100% | High |
| `sm_occupancy_pct` | CUDA Profiler | 0-100% | Medium |
| `gpu_memory_pct` | nvidia-smi | 0-100% | High |
| `queue_depth` | GPU Queue Manager | 0-256 | Medium |
| `p99_latency_ms` | Prometheus histogram | 0-∞ | Critical |
| `error_rate_pct` | Error counter | 0-100% | Critical |
| `tokens_per_second` | Throughput counter | 0-∞ | Medium |

### Decision Matrix

| Decision | Conditions (ALL must hold) | Batch Adjustment |
|----------|---------------------------|-----------------|
| **GROW** | GPU util < 70%, p99 < 180ms (90% of SLO), SM < 80%, errors < 1%, queue stable | `min(current * 1.25, 128)` |
| **SHRINK** | Any: p99 > 200ms, SM > 90%, memory > 90%, errors > 3%, queue growing > 10 req/s | `max(current * 0.75, 1)` |
| **HOLD** | All signals within acceptable ranges | No change |
| **EMERGENCY** | p99 > 500ms OR error rate > 10% | Reset to `min_batch_size` (1) |

### Feedback Loop Auto-Tuning

```
FEEDBACK LOOP
================================================================

  Decision ──► Execute ──► Measure Outcome
      ▲                         │
      │                         │
      │    ┌────────────────────┘
      │    │
      │    ▼
  ┌────────────────┐
  │  History Ring   │  (last 500 decisions)
  │  Buffer         │
  └────────┬───────┘
           │
           ▼
  ┌────────────────┐
  │  Correlation   │  Pearson(batch_size, throughput)
  │  Analysis      │
  └────────┬───────┘
           │
           ▼
  ┌────────────────┐
  │  Auto-Tune     │  Adjust grow_factor (1.1 - 2.0)
  │  Thresholds    │  Adjust shrink_factor (0.5 - 0.9)
  └────────────────┘

================================================================
```

If > 70% of GROW decisions improved throughput, the grow factor nudges up (capped at 2.0).
If < 30% of GROW decisions improved throughput, the grow factor becomes more conservative
(floor at 1.1). The shrink dampener adjusts in tandem.

---

## 18. Model Canary Deployment

### Progressive Rollout Stages

| Stage | Traffic % | Min Bake Time | Advance Criteria |
|-------|----------|---------------|-----------------|
| 1 | 5% | 60s | All health checks pass |
| 2 | 10% | 60s | p95 latency within 5% of baseline |
| 3 | 25% | 120s | Error rate < baseline + 0.5% |
| 4 | 50% | 300s | Accuracy within 1% of baseline |
| 5 | 75% | 300s | GPU memory within 10% of baseline |
| 6 | 100% | 600s | All metrics stable for 10 minutes |

### Auto-Rollback Thresholds

| Metric | Rollback Threshold | Detection Window | Severity |
|--------|-------------------|-----------------|----------|
| p95 latency | > 10% regression vs baseline | 60s | P1 - immediate rollback |
| Error rate | > 1% increase vs baseline | 30s | P1 - immediate rollback |
| Model accuracy | > 2% drop vs baseline | 300s | P2 - rollback after confirmation |
| GPU memory | > 15% increase vs baseline | 60s | P2 - rollback after confirmation |

### Canary Lifecycle

```
CANARY DEPLOYMENT LIFECYCLE
================================================================

  Deploy Request
       │
       ▼
  ┌──────────┐    (start traffic split)
  │ INITIAL  │───────────────────────────────►┌──────────┐
  └──────────┘                                │ RAMPING  │
                                              └────┬─────┘
                                                   │
                        ┌──────────────────────────┤
                        │                          │
                   (health OK)              (health FAIL)
                        │                          │
                        ▼                          ▼
                  ┌──────────┐              ┌─────────────┐
                  │ BAKING   │              │ ROLLED_BACK │
                  └────┬─────┘              └─────────────┘
                       │
                  (bake timer)
                       │
                       ▼
                  ┌───────────┐
                  │ PROMOTING │──────────► ┌───────────┐
                  └───────────┘            │ COMPLETED │
                       │                   └───────────┘
                  (health FAIL)
                       │
                       ▼
                  ┌─────────────┐
                  │ ROLLED_BACK │
                  └─────────────┘

================================================================
```

### Health Comparison Methodology

For each metric, the canary is compared against the baseline (current production model)
using a statistical significance test:

```json
{
    "metric": "p95_latency_ms",
    "baseline_value": 45.2,
    "canary_value": 48.1,
    "delta_pct": 6.4,
    "threshold_pct": 10.0,
    "verdict": "PASS",
    "sample_count": 1250,
    "confidence": 0.95
}
```

---

## 19. Failure Scenario Simulation

### Production Failure Scenarios

| # | Scenario | Trigger | Detection Method | Automated Mitigation | Blast Radius | MTTR Target |
|---|----------|---------|-----------------|---------------------|-------------|-------------|
| 1 | GPU OOM | Batch too large for VRAM | `nvidia-smi` memory alerts | Reduce batch size, preempt BEST_EFFORT | Single GPU | 30s |
| 2 | GPU Thermal Throttle | Sustained >85°C | DCGM temperature metrics | Reduce clock speed, migrate workload | Single GPU | 60s |
| 3 | GPU ECC Error | Uncorrectable memory error | DCGM ECC counters | Drain GPU, failover to healthy nodes | Single GPU | 120s |
| 4 | NVLink Failure | Inter-GPU link down | NVLink bandwidth metrics | Disable tensor parallelism, use single GPU | GPU pair | 300s |
| 5 | KV Cache Overflow | Cache exceeds 95% capacity | Cache utilization metrics | Evict oldest entries, shrink max_seq_len | All requests on GPU | 10s |
| 6 | Triton Server Crash | Process segfault / OOM kill | Process health check failure | Auto-restart, redirect traffic | Single node | 45s |
| 7 | Redis Network Partition | Split-brain between replicas | Sentinel failover detection | Promote replica, bypass cache | Cache-dependent requests | 15s |
| 8 | Region Complete Failure | AZ or region outage | Cross-region health probes | Redirect traffic to healthy regions | Entire region | 120s |
| 9 | Cascading Timeout | Upstream timeout chain reaction | Latency spike correlation | Open circuit breakers, shed load | Multiple services | 60s |
| 10 | Traffic Spike (3x) | Flash event (viral content) | Request rate anomaly detection | Auto-scale + shed BEST_EFFORT | All tiers | 180s |
| 11 | Model Corruption | Bit-flip in weights | Accuracy monitoring | Rollback to previous version | All predictions | 60s |
| 12 | Cold Start Storm | Many GPUs initializing simultaneously | Ready probe failure rate | Staggered warm-up, cached fallback | New deployments | 300s |
| 13 | DNS Failure | Service mesh DNS resolution failure | DNS resolution latency | Fallback to IP-based routing | Cross-service calls | 30s |

### Detailed Scenario: GPU OOM (Scenario 1)

```
TIMELINE: GPU OOM INCIDENT
================================================================

t=0.0s   Large batch (256 samples) submitted to A100 GPU-3
t=0.1s   KV cache allocation: 256 * 4096 tokens * 2KB = 2 GB
t=0.2s   Activation memory spike: +8 GB
t=0.3s   nvidia-smi reports 79.2 GB / 80 GB used
t=0.5s   CUDA malloc fails -- OOM exception raised
t=0.6s   Triton inference worker returns error
t=0.7s   Circuit breaker records failure #1
t=1.0s   Prometheus alert: gpu_memory_utilization > 98%

MITIGATION:
t=1.0s   Adaptive batch policy: EMERGENCY → reset to batch_size=1
t=1.2s   GPU queue: preempt all BEST_EFFORT requests
t=1.5s   CUDA memory freed by failed allocation rollback
t=2.0s   GPU-3 resumes serving with batch_size=1
t=5.0s   Batch size gradually grows: 1 → 2 → 4 → 8
t=30.0s  Batch size stabilized at 64 (post-incident safe level)

MTTR: 30 seconds
Blast radius: GPU-3 only (1 of 8 GPUs)

================================================================
```

### Detailed Scenario: Region Failure (Scenario 8)

```
TIMELINE: REGION COMPLETE FAILURE
================================================================

t=0.0s   us-east-1 AZ-a reports network unreachable
t=0.5s   Cross-region health probe detects failure
t=1.0s   Route 53 health check fails for us-east-1
t=2.0s   DNS TTL expires, traffic begins shifting

MITIGATION:
t=2.0s   API Gateway marks us-east-1 unhealthy
t=3.0s   Traffic redistribution:
           us-west-2: 30% → 55% (+25%)
           eu-west-1: 30% → 45% (+15%)
t=5.0s   Auto-scaler in us-west-2: +4 GPU nodes
t=10.0s  Auto-scaler in eu-west-1: +3 GPU nodes
t=30.0s  New GPU nodes pass readiness probes
t=60.0s  Capacity stabilized in remaining regions

MTTR: 120 seconds (full capacity restored)
Blast radius: 40% of global traffic (temporarily degraded)

================================================================
```

### Detailed Scenario: Traffic Spike 3x (Scenario 10)

```
TIMELINE: 3x TRAFFIC SPIKE
================================================================

t=0.0s   Viral content release -- request rate jumps from 10K to 30K RPS
t=0.5s   GPU queue depth: 64 → 192
t=1.0s   Backpressure level: NORMAL → HIGH
t=1.5s   GPU utilization: 65% → 95%

MITIGATION:
t=1.0s   Traffic shedding activated:
           BEST_EFFORT: 100% shed
           STANDARD: 30% shed
           PREMIUM: serve normally
           CRITICAL: serve normally
t=2.0s   Adaptive batch shrink: 128 → 64
t=5.0s   Auto-scaler triggered: requesting +8 GPU nodes
t=10.0s  Regional rebalancing: spread load across regions
t=60.0s  New GPU nodes online, passing readiness probes
t=120.0s Auto-scaler stabilizes at new capacity
t=180.0s Full traffic restored, shedding disabled

MTTR: 180 seconds (full capacity)
Blast radius: STANDARD + BEST_EFFORT degraded for ~3 minutes

================================================================
```

---

**Author:** Gopi Krishna Vajrala
