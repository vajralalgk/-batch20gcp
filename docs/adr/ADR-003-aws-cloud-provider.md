# ADR-003: AWS as Primary Cloud Provider with Multi-Region Active-Active

**Status:** ACCEPTED
**Date:** 2026-02-21
**Author:** Gopi Krishna Vajrala
**Deciders:** Platform Engineering Team, VP of Engineering
**Category:** Cloud Infrastructure

---

## Context

The Netflix LLM Platform requires a cloud provider that supports:

- High-performance GPU instances with NVIDIA A100 80GB GPUs (minimum 8 GPUs per node)
- Multi-region deployment with active-active capability across US and EU
- Low-latency networking between GPU nodes (NVLink within node, EFA between nodes)
- Managed Kubernetes for GPU workload orchestration
- Managed caching (Redis) and NoSQL databases with global replication
- Latency-based DNS routing with health checks
- GDPR-compliant data residency controls for EU region
- OIDC-based authentication for CI/CD (no long-lived credentials)

The platform requires p4d.24xlarge instances (8x NVIDIA A100 80GB SXM4) for tensor-parallel inference of the 70B parameter model. GPU availability, pricing, and networking capabilities are critical selection criteria.

## Decision

We adopt **AWS** as the primary cloud provider with a multi-region active-active deployment across us-east-1, us-west-2, and eu-west-1.

## Alternatives Considered

### Google Cloud Platform (GCP)

- **Pros:** TPU availability, strong ML ecosystem (Vertex AI), competitive GPU pricing, GKE is mature
- **Cons:** A100 availability is more constrained in some regions, DynamoDB Global Tables equivalent (Spanner) is more expensive, Netflix's existing infrastructure is AWS-native
- **Rejected because:** Migrating from Netflix's existing AWS infrastructure would add 6-12 months of migration effort. GCP A3 instances (H100) are newer but availability is less predictable than AWS p4d/p5 instances. Spanner pricing for the required read throughput is 40% higher than DynamoDB.

### Microsoft Azure

- **Pros:** ND A100 v4 instances available, Azure Kubernetes Service is mature, strong enterprise support
- **Cons:** GPU instance availability is inconsistent across regions, Azure Cache for Redis lacks cluster-mode features parity with ElastiCache, Cosmos DB global replication has higher latency than DynamoDB Global Tables
- **Rejected because:** GPU instance availability in Azure is less reliable for the required scale (14+ p4d-equivalent nodes). Azure's networking for GPU workloads (InfiniBand) is primarily available in specific regions, limiting multi-region flexibility.

### Multi-Cloud (AWS + GCP)

- **Pros:** Avoid vendor lock-in, leverage best-of-breed services, redundancy
- **Cons:** Massive operational complexity, cross-cloud networking latency, different GPU instance types require different TensorRT-LLM builds, doubled infrastructure code, team must maintain expertise on two platforms
- **Rejected because:** The operational complexity of running Triton across two cloud providers with different GPU instance types would double infrastructure engineering effort without proportional benefit. Cross-cloud networking adds 5-15ms latency that violates our P99 budget.

## Consequences

### Positive

- **GPU availability:** AWS p4d.24xlarge instances are available in all three target regions with reserved instance pricing, providing cost predictability.
- **EKS maturity:** Amazon EKS has mature GPU support including NVIDIA device plugin, GPU topology awareness, and EFA networking.
- **Managed services:** ElastiCache (Redis 7, cluster mode), DynamoDB (Global Tables), S3 (cross-region replication) reduce operational burden.
- **Networking:** 400 Gbps aggregate bandwidth per p4d instance, EFA for inter-node GPU communication, and Transit Gateway for cross-VPC routing.
- **OIDC support:** Native OIDC provider for GitHub Actions eliminates long-lived AWS credentials in CI/CD.
- **Compliance:** AWS has GDPR-compliant regions (eu-west-1) with data residency controls and audit capabilities.

### Negative

- **Vendor lock-in:** Deep integration with AWS-specific services (DynamoDB, ElastiCache, EKS) makes future migration costly. Mitigated by abstracting service interfaces behind internal APIs.
- **Cost:** p4d.24xlarge on-demand pricing is $32.77/hr. Mitigated with 1-year reserved instances (40% discount) and spot instances for non-critical workloads.
- **GPU supply:** During high-demand periods, p4d instances may have limited availability. Mitigated with reserved capacity and Capacity Reservations in each region.

### AWS Services Architecture

| Service | Purpose | Configuration |
|---------|---------|---------------|
| **EKS** | GPU workload orchestration | v1.28+, GPU node groups, Karpenter autoscaler |
| **EC2 p4d.24xlarge** | GPU inference nodes | 8x A100 80GB, NVLink 3.0, 400 Gbps network |
| **ElastiCache** | User profile cache, feature vectors | Redis 7, cluster mode, r7g.xlarge, 6 shards |
| **DynamoDB** | User profiles, global replication | Global Tables, on-demand capacity, DAX |
| **S3** | Model artifact storage | Versioned, cross-region replication, SSE-KMS |
| **Route53** | Latency-based routing | Health checks every 10s, failover < 15s |
| **CloudWatch** | Metrics, logs, alarms | Custom metrics for GPU utilization, DCGM integration |
| **WAF** | API protection | OWASP Top 10 rules, rate limiting, IP reputation |
| **Shield Advanced** | DDoS protection | Layer 3/4/7 protection, response team |
| **KMS** | Encryption key management | Automatic key rotation, cross-region keys |
| **IAM** | Access control | OIDC for CI/CD, least-privilege roles |
| **ECR** | Container image registry | Cross-region replication, vulnerability scanning |
| **EFA** | GPU inter-node networking | Elastic Fabric Adapter, libfabric |

### Regional Cost Estimate (Monthly)

```
Per Region (us-east-1, 6 nodes):
  GPU Compute (6x p4d.24xlarge, reserved):  $85,000
  ElastiCache (6-shard r7g.xlarge):          $4,200
  DynamoDB (Global Tables):                  $3,500
  S3 (Model storage + replication):            $800
  Networking (NAT, EFA, data transfer):      $5,500
  Route53 + WAF + Shield:                    $2,000
  CloudWatch + Logging:                      $1,500
  ────────────────────────────────────────────────────
  Regional Total:                          $102,500

Global Total (3 regions):                  $290,000/month
Annual Total:                            $3,480,000/year
```

---

**References:**

- [AWS p4d Instance Type](https://aws.amazon.com/ec2/instance-types/p4/)
- [Amazon EKS GPU Support](https://docs.aws.amazon.com/eks/latest/userguide/gpu-ami.html)
- [DynamoDB Global Tables](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GlobalTables.html)
