# ADR-005: Active-Active Multi-Region Deployment Architecture

**Status:** ACCEPTED
**Date:** 2026-02-21
**Author:** Gopi Krishna Vajrala
**Deciders:** Platform Engineering Team, VP of Engineering, SRE Team
**Category:** Deployment Architecture

---

## Context

The Netflix LLM Platform serves real-time inference requests for personalized content recommendations and conversational interfaces. The platform must maintain:

- **99.99% availability** (< 52.6 minutes of downtime per year)
- **Sub-100ms P99 latency** for users across North America and Europe
- **< 15 second failover time** when a region becomes unavailable
- **Zero data loss** during regional failures
- **GDPR compliance** with EU data residency requirements

The platform runs on GPU infrastructure (p4d.24xlarge with 8x A100 GPUs per node). GPU nodes take 5-10 minutes to provision and load models, making cold-start failover strategies (standby regions that spin up on demand) unacceptable for real-time inference workloads.

Streaming inference (token-by-token generation) means that an in-flight request cannot be seamlessly migrated to another region mid-stream. The failover strategy must route new requests, not migrate existing ones.

## Decision

We adopt an **active-active multi-region deployment** across three AWS regions: us-east-1 (primary), us-west-2 (secondary), and eu-west-1 (EU).

All three regions continuously serve production traffic. There is no standby or passive region. Traffic is distributed via Route53 latency-based routing, and any region can absorb the full traffic of any other single region during failures.

## Alternatives Considered

### Active-Passive (Primary + Hot Standby)

- **Pros:** Lower cost (standby runs at minimal capacity), simpler data consistency (single write region), less complex routing
- **Cons:** Cold-start failover for GPU workloads takes 5-10 minutes (model loading), standby region may have stale model versions, failover testing is infrequent and risky
- **Rejected because:** GPU model loading takes 5-10 minutes. During this cold-start period, all inference requests would fail or queue, violating the 99.99% availability SLO. For streaming inference, even 30 seconds of downtime during failover is unacceptable.

### Active-Passive with Pre-Warmed GPUs

- **Pros:** Lower cost than active-active (standby handles no traffic), pre-warmed GPUs eliminate cold-start delay
- **Cons:** Paying for idle GPU capacity (p4d.24xlarge at $32.77/hr), standby GPUs degrade without load testing, no real-world traffic validation, model cache (KV cache) is cold
- **Rejected because:** Pre-warmed standby GPUs cost 60-70% of active-active (idle GPUs still consume compute for model residency). The KV cache in standby regions is cold, meaning the first minutes after failover see significantly degraded latency. The marginal cost savings do not justify the operational risk.

### Single Region with Multi-AZ

- **Pros:** Simplest architecture, lowest cost, no cross-region data replication concerns
- **Cons:** No protection against regional outages, all users experience same latency regardless of geography, GDPR non-compliant (EU data must stay in EU)
- **Rejected because:** AWS regional outages, while rare, have occurred (us-east-1 in December 2021, lasting several hours). A multi-hour outage of the LLM platform would violate the 99.99% SLO by orders of magnitude. Additionally, GDPR requires EU user data to be processed in EU regions.

## Consequences

### Positive

- **< 15 second failover:** Route53 health checks detect region failures within 10 seconds. DNS TTL of 60 seconds means most clients failover within 15 seconds. No GPU cold-start delay because target regions are already warm and serving traffic.
- **Geographic latency reduction:** Users are routed to the nearest region, reducing network latency by 20-40ms for cross-continental requests.
- **Continuous validation:** All regions are continuously serving production traffic, ensuring model versions, configurations, and GPU health are constantly validated.
- **GDPR compliance:** eu-west-1 processes EU user data locally, with data residency controls preventing cross-region PII transfer.
- **Graceful degradation during partial failures:** If one region degrades (elevated latency but not fully down), Route53 health checks can shift traffic proportionally to healthier regions.

### Negative

- **Higher cost:** Running active infrastructure in three regions costs approximately 2.5x a single-region deployment. For GPU infrastructure, this is significant (~$290K/month vs ~$115K/month for single region). Mitigated by right-sizing regional capacity (not 3x, but 1.5x total for N+1 redundancy).
- **Data consistency complexity:** User profiles and features must be replicated across regions. DynamoDB Global Tables provide eventual consistency with typical replication lag of 1-2 seconds. For real-time personalization, this means a user who updates preferences in us-east-1 may see stale data if routed to eu-west-1 within the replication window. Mitigated by session affinity in Route53.
- **Model deployment complexity:** New model versions must be deployed to all three regions in a coordinated manner. Mitigated by the CD pipeline with progressive deployment (us-east-1 first, then secondary regions after validation).
- **Operational complexity:** Three production regions means 3x the monitoring, alerting, and on-call surface area. Mitigated by unified observability stack and runbooks.

### Architecture

```
                        ┌──────────────────────┐
                        │      Route53          │
                        │  Latency-Based DNS    │
                        │  Health Check: 10s    │
                        │  Failover: < 15s      │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼───────┐  ┌────────▼────────┐
     │   us-east-1     │  │  us-west-2    │  │   eu-west-1     │
     │   PRIMARY       │  │  SECONDARY    │  │   EU            │
     │                 │  │               │  │                 │
     │ Traffic: 45%    │  │ Traffic: 30%  │  │ Traffic: 25%    │
     │ Nodes: 6x p4d   │  │ Nodes: 4x p4d │  │ Nodes: 4x p4d   │
     │ GPUs: 48 A100   │  │ GPUs: 32 A100 │  │ GPUs: 32 A100   │
     │                 │  │               │  │                 │
     │ Capacity: 75%   │  │ Capacity: 60% │  │ Capacity: 55%   │
     │ (headroom for   │  │ (can absorb   │  │ (can absorb     │
     │  failover)      │  │  us-east-1    │  │  us-east-1      │
     │                 │  │  partially)   │  │  partially)     │
     └────────┬────────┘  └───────┬───────┘  └────────┬────────┘
              │                    │                    │
              │         ┌─────────▼─────────┐          │
              └────────>│  DynamoDB Global   │<─────────┘
                        │  Tables            │
                        │  (Async Repl ~1s)  │
                        └────────────────────┘
```

### Failover Timeline

```
T+0s:     Region failure occurs (e.g., us-east-1 becomes unreachable)
T+10s:    Route53 health check fails (3 consecutive failures x 10s interval / 3)
T+10s:    Route53 removes failed region from DNS responses
T+15s:    Clients receive updated DNS, begin routing to surviving regions
T+15s:    Surviving regions handle increased load via dynamic batching
T+30s:    Autoscaler detects increased GPU utilization, begins scaling
T+120s:   Additional GPU nodes provisioned and models loaded
T+300s:   Full capacity restored in surviving regions
T+300s+:  Incident response begins for failed region

Impact Window: T+0 to T+15s
  - In-flight requests in failed region: LOST (client retries)
  - New requests: Routed to surviving regions within 15s
  - Effective downtime: < 15 seconds for new requests
```

### Cost Comparison

| Architecture | Monthly Cost | Availability | Failover Time |
|-------------|-------------|-------------|---------------|
| Single Region | $115,000 | 99.9% | N/A (total outage) |
| Active-Passive | $185,000 | 99.95% | 5-10 minutes |
| Active-Passive (Pre-Warmed) | $220,000 | 99.97% | 30-60 seconds |
| **Active-Active (Chosen)** | **$290,000** | **99.99%** | **< 15 seconds** |

The $105K/month premium for active-active over active-passive is justified by the 40x improvement in failover time (15s vs 10min) and the continuous validation benefit.

---

**References:**

- [AWS Multi-Region Architecture](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-options-in-the-cloud.html)
- [Route53 Health Checks](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-types.html)
- [DynamoDB Global Tables](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GlobalTables.html)
