# Netflix Real-Time LLM Personalization & Inference Platform
# API Design Standards

**Document ID:** NFLX-LLM-API-001
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Date:** 2026-02-21

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
GET  /v1/models               List loaded models and status
GET  /v1/models/{model_id}    Get specific model details
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
```

---

## 4. Request / Response Format

### 4.1 Prediction Request (REST)

```json
POST /v1/predict
Content-Type: application/json

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
```

### Distributed Tracing

- All requests propagate OpenTelemetry trace context (`traceparent` header)
- Spans recorded for: API Gateway, feature assembly, cache lookup, GPU inference, post-processing
- Trace data exported to Jaeger / Tempo for analysis

---

**Author:** Gopi Krishna Vajrala
