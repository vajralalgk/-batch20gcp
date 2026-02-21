# Event-Driven Architecture Pattern for LLM Inference

**Author:** Gopi Krishna Vajrala
**Context:** Netflix Real-Time LLM Personalization & Inference Platform

---

## Overview

The event-driven architecture pattern decouples components in the LLM inference pipeline through asynchronous event publication and consumption. In the Netflix LLM Platform, event-driven patterns handle model updates, user profile changes, cache invalidation, GPU health state changes, and observability data flow. The synchronous inference hot path remains request-response for latency, while supporting workflows use events.

## Problem Statement

The LLM platform has multiple components that need to react to state changes without tight coupling:

- When a new model version is deployed, all Triton instances must reload
- When a user updates preferences, the personalization cache must invalidate across regions
- When a GPU node becomes unhealthy, the load balancer must reroute traffic
- When inference latency exceeds SLOs, auto-scaling must trigger
- When the KV cache reaches pressure thresholds, eviction policies must activate

Polling-based approaches introduce latency and waste resources. Direct service-to-service calls create tight coupling and cascade risks.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Event Bus (Amazon EventBridge)                │
│                                                                  │
│  Topics:                                                         │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────┐  │
│  │ model.updated   │  │ gpu.health     │  │ inference.metrics │  │
│  │ model.deployed  │  │ gpu.oom        │  │ inference.error   │  │
│  │ model.rollback  │  │ gpu.ecc_error  │  │ inference.timeout │  │
│  └────────┬───────┘  └────────┬───────┘  └─────────┬─────────┘  │
│           │                   │                     │            │
│  ┌────────┴───────┐  ┌───────┴────────┐  ┌────────┴──────────┐  │
│  │ cache.invalidate│  │ user.profile   │  │ scaling.trigger   │  │
│  │ cache.warm      │  │ user.preference│  │ scaling.complete  │  │
│  │ cache.evict     │  │ user.session   │  │ scaling.failed    │  │
│  └────────────────┘  └────────────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Event Schemas

### Model Events

```json
{
  "event_type": "model.deployed",
  "timestamp": "2026-02-21T10:30:00Z",
  "source": "cd-pipeline",
  "data": {
    "model_name": "netflix_llm_70b",
    "model_version": "2",
    "region": "us-east-1",
    "image_tag": "abc1234",
    "deployment_strategy": "canary",
    "canary_weight": 10
  }
}
```

### GPU Health Events

```json
{
  "event_type": "gpu.health_changed",
  "timestamp": "2026-02-21T10:35:00Z",
  "source": "dcgm-exporter",
  "data": {
    "node_id": "ip-10-0-1-42.ec2.internal",
    "gpu_index": 3,
    "previous_state": "healthy",
    "current_state": "degraded",
    "reason": "ecc_correctable_errors_threshold",
    "ecc_error_count": 150,
    "gpu_temperature_celsius": 78,
    "action_required": "schedule_replacement"
  }
}
```

### Inference Metrics Events

```json
{
  "event_type": "inference.slo_breach",
  "timestamp": "2026-02-21T10:40:00Z",
  "source": "prometheus-alertmanager",
  "data": {
    "metric": "p99_latency_ms",
    "threshold": 100,
    "actual_value": 115,
    "duration_seconds": 120,
    "region": "us-east-1",
    "affected_model": "netflix_llm_70b"
  }
}
```

## Event Consumers

| Event | Consumer | Action |
|-------|----------|--------|
| `model.deployed` | Triton Manager | Reload model from S3 repository |
| `model.deployed` | Cache Warmer | Pre-populate KV cache with common prefixes |
| `model.rollback` | Triton Manager | Revert to previous model version |
| `gpu.health_changed` | Load Balancer | Adjust routing weights for affected node |
| `gpu.oom` | Circuit Breaker | Open circuit for affected GPU pool |
| `gpu.ecc_error` | Node Manager | Cordon node, schedule replacement |
| `cache.invalidate` | ElastiCache | Delete affected cache keys across regions |
| `user.preference_updated` | Profile Cache | Invalidate user profile in all regions |
| `inference.slo_breach` | Autoscaler | Trigger scale-out event |
| `scaling.trigger` | EKS Autoscaler | Provision additional GPU nodes |

## Cross-Region Event Propagation

```
┌───────────────┐          ┌───────────────┐          ┌───────────────┐
│   us-east-1   │          │   us-west-2   │          │   eu-west-1   │
│               │          │               │          │               │
│  EventBridge  │──event──>│  EventBridge  │──event──>│  EventBridge  │
│  (Source)     │  bridge  │  (Target)     │  bridge  │  (Target)     │
│               │          │               │          │               │
│  Event Bus    │          │  Event Bus    │          │  Event Bus    │
│  Local Rules  │          │  Local Rules  │          │  Local Rules  │
└───────────────┘          └───────────────┘          └───────────────┘

Cross-region propagation latency: < 1 second (EventBridge global endpoints)
```

## Async vs Sync Decision Matrix

| Operation | Pattern | Rationale |
|-----------|---------|-----------|
| Inference request | Synchronous (REST/gRPC) | Latency-critical, user-facing |
| Model deployment | Event-driven | Multi-consumer, eventually consistent |
| Cache invalidation | Event-driven | Cross-region, idempotent |
| GPU health monitoring | Event-driven | Decoupled from hot path |
| User preference update | Event-driven | Cross-region replication |
| Auto-scaling decision | Event-driven | Complex multi-signal correlation |
| Streaming tokens (SSE) | Synchronous (streaming) | Real-time user experience |

## Reliability Guarantees

- **At-least-once delivery:** EventBridge guarantees at-least-once delivery. Consumers must be idempotent.
- **Ordering:** Events within a single partition are ordered. Cross-partition ordering is not guaranteed.
- **Dead letter queue:** Failed event processing routes to SQS DLQ for manual investigation.
- **Replay:** EventBridge archive enables event replay for disaster recovery and debugging.

---

**References:**

- Martin Fowler, "Event-Driven Architecture"
- Amazon EventBridge documentation
- Netflix Mantis (event stream processing platform)
