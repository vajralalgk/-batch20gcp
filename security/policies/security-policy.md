# Netflix Real-Time LLM Personalization & Inference Platform
# Security Policy

**Document ID:** NFLX-LLM-SEC-001
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Date:** 2026-02-21
**Classification:** Internal - Confidential
**Status:** Approved

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 2.0.0 | 2026-02-21 | Gopi Krishna Vajrala | Comprehensive security policy for LLM inference platform |
| 1.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Initial release |

---

## 1. Network Security

### 1.1 VPC Isolation

Each deployment region (us-east-1, us-west-2, eu-west-1) runs an isolated VPC with strict network segmentation.

| Tier | Subnet Type | Resources | Internet Access |
|------|-------------|-----------|-----------------|
| Public | Public subnet | NLB, NAT Gateway | Direct (IGW) |
| Inference | Private subnet | GPU inference pods (EKS) | None (air-gapped) |
| Services | Private subnet | API Gateway, Feature Store, Cache | Outbound only (NAT) |
| Data | Isolated subnet | Model Registry (DynamoDB), Logs (S3) | None |
| Management | Private subnet | Monitoring, Bastion (if needed) | Outbound only (NAT) |

### 1.2 Security Groups

| Security Group | Inbound Rules | Source | Purpose |
|----------------|--------------|--------|---------|
| NLB SG | HTTPS (443), gRPC (8443) | 0.0.0.0/0 (via WAF) | Client traffic ingress |
| API Gateway SG | HTTP (8080) | NLB SG only | API routing |
| Inference SG | gRPC (8001), HTTP (8000), Metrics (8002) | API Gateway SG only | Model inference |
| Redis SG | Redis (6379) | Inference SG, Services SG | Feature store, cache |
| Monitoring SG | Prometheus (9090), Grafana (3000) | Management SG only | Observability |

### 1.3 Network Policies (Kubernetes)

- **Default deny:** All ingress and egress traffic denied by default in the `llm-inference` namespace
- **Explicit allow:** Only inference pods can reach Redis (feature store and prediction cache)
- **GPU node isolation:** GPU nodes accept traffic only from the Kubernetes API server and inference pods
- **Cross-namespace denied:** No cross-namespace traffic without explicit NetworkPolicy

### 1.4 WAF Configuration

| Rule | Action | Purpose |
|------|--------|---------|
| AWS Managed - Core Rule Set | Block | Common web exploits (SQLi, XSS) |
| AWS Managed - Known Bad Inputs | Block | Known malicious payloads |
| AWS Managed - Bot Control | Count/Block | Automated attack detection |
| Rate Limiting | Block > 10K req/5min per IP | DDoS protection |
| Request Size Limit | Block > 1MB | Prevent oversized inference requests |
| Custom - Inference Payload Validation | Block | Reject malformed prediction requests |

### 1.5 Network Monitoring

- VPC Flow Logs enabled on all subnets (sent to CloudWatch Logs and S3)
- GuardDuty enabled for threat detection across all regions
- AWS Shield Advanced for DDoS protection on NLB endpoints
- DNS query logging via Route53 Resolver
- Cross-region VPC peering traffic monitored via CloudWatch

---

## 2. Data Security

### 2.1 Encryption at Rest

| Resource | Encryption Method | Key Management | Rotation |
|----------|------------------|----------------|----------|
| Model Artifacts (S3) | AES-256 (SSE-KMS) | Customer-managed CMK | Annual |
| Model Registry (DynamoDB) | AES-256 via KMS | AWS-managed CMK | Automatic |
| Feature Store (Redis) | AES-256 at-rest | AWS-managed key | Automatic |
| Prediction Cache (Redis) | AES-256 at-rest | AWS-managed key | Automatic |
| Inference Logs (S3) | AES-256 (SSE-KMS) | Customer-managed CMK | Annual |
| EBS Volumes (GPU nodes) | AES-256 via KMS | Customer-managed CMK | Annual |
| Secrets Manager | AES-256 via KMS | Customer-managed CMK | 90 days |
| CloudWatch Logs | AES-256 via KMS | Customer-managed CMK | Annual |

### 2.2 Encryption in Transit

| Connection | Protocol | Minimum Version | Certificate |
|-----------|----------|-----------------|-------------|
| Client to NLB | TLS | 1.3 | ACM-managed certificate |
| NLB to API Gateway | TLS | 1.2 | Internal certificate |
| API Gateway to Inference | gRPC + TLS | 1.2 | mTLS (mutual) |
| Inference to Redis | TLS | 1.2 | ElastiCache in-transit encryption |
| Inference to S3 | HTTPS | 1.2 | AWS certificate |
| Cross-region replication | TLS | 1.3 | AWS-managed |
| Prometheus scraping | HTTPS | 1.2 | Internal certificate |

### 2.3 Data Classification

| Level | Definition | Examples | Controls |
|-------|-----------|----------|----------|
| **Restricted** | PII, user viewing history | User profiles, watch history, preferences | Encryption, access logging, data minimization |
| **Confidential** | Model weights, business logic | LLM weights, ranking algorithms, A/B configs | Encryption, role-based access, audit trail |
| **Internal** | Architecture, operational data | Inference logs, GPU metrics, capacity plans | Authentication required |
| **Public** | Published APIs, documentation | API specs, public health endpoints | No special controls |

### 2.4 Model Security

- Model artifacts are cryptographically signed before upload to S3
- Model checksums verified on every load to GPU memory
- Model version pinning prevents unauthorized model swaps
- Model access audit trail maintained in DynamoDB
- No model weights exposed via API responses

---

## 3. Access Control

### 3.1 Identity and Access Management (IAM)

| Control | Implementation | Enforcement |
|---------|---------------|-------------|
| Multi-Factor Authentication | Required for all AWS console and kubectl access | AWS IAM MFA policy |
| Service Accounts | Dedicated IAM roles per service (IRSA for EKS) | No shared credentials |
| Temporary Credentials | STS AssumeRole for all access | No long-lived access keys |
| Least Privilege | Scoped IAM policies per role (see iam-policy-template.json) | IAM Access Analyzer |
| Pod Identity | EKS Pod Identity / IRSA for Kubernetes workloads | No node-level credentials |

### 3.2 RBAC Role Definitions

| Role | AWS Access | Kubernetes Access | Approval |
|------|-----------|-------------------|----------|
| Platform Admin | Full LLM platform access | cluster-admin in llm-inference NS | CISO |
| ML Engineer | S3 models (read/write staging), SageMaker | edit in llm-inference NS | Tech Lead |
| Data Scientist | S3 models (read-only), metrics (read) | view in llm-inference NS | Team Lead |
| SRE / DevOps | EKS, ECR, CloudWatch, deployment | edit in llm-inference NS | SRE Lead |
| Security Engineer | GuardDuty, WAF, CloudTrail | view (audit) in all NS | CISO |
| Read-Only Auditor | CloudTrail, Config, billing | view in all NS | Compliance Officer |

### 3.3 Access Review Schedule

| Activity | Frequency | Owner |
|----------|-----------|-------|
| IAM role and policy review | Quarterly | Security Lead |
| Kubernetes RBAC review | Quarterly | Platform Admin |
| Service account credential rotation | Every 90 days | SRE Lead |
| KMS key access review | Semi-annually | Security Lead |
| Privileged access review | Monthly | CISO |

---

## 4. GPU Security

### 4.1 Isolated Inference Nodes

| Control | Implementation |
|---------|---------------|
| Dedicated GPU node pools | GPU nodes run only inference workloads (taints and tolerations) |
| Node isolation | GPU nodes in isolated subnets with no internet access |
| Container runtime | containerd with seccomp and AppArmor profiles |
| GPU device plugin | NVIDIA device plugin with MIG (Multi-Instance GPU) support |
| No SSH access | GPU nodes have no SSH keys; access only via kubectl exec (audited) |
| Read-only filesystem | Inference containers run with read-only root filesystem |

### 4.2 GPU Memory Security

- GPU memory cleared between inference sessions (no data leakage between users)
- NVIDIA MPS (Multi-Process Service) disabled to prevent cross-container GPU access
- ECC (Error Correcting Code) enabled and monitored on all GPU devices
- GPU firmware updates applied during maintenance windows

### 4.3 Model Integrity

| Check | Frequency | Action on Failure |
|-------|-----------|-------------------|
| Model checksum verification | Every model load | Reject model, alert, use previous version |
| Model signature validation | Every deployment | Block deployment, notify security team |
| Model drift detection | Hourly | Alert ML team, trigger investigation |
| Adversarial input detection | Real-time | Log, rate limit, escalate if pattern detected |

---

## 5. Compliance Requirements

### 5.1 Data Privacy (CCPA / GDPR)

| Requirement | Implementation |
|-------------|---------------|
| User data access requests | API for data export within 30 days |
| Right to deletion | User profile and history deletion pipeline |
| Data minimization | Only necessary features stored; TTL on all caches |
| Consent management | Feature flags for personalization opt-out |
| Cross-border data | EU user data processed only in eu-west-1 |

### 5.2 SOC 2 Type II

| Trust Service Criteria | Implementation |
|----------------------|---------------|
| **Security** | WAF, encryption, IAM, vulnerability scanning, GPU isolation |
| **Availability** | Multi-region, auto-scaling, 99.95% SLA target, DR plan |
| **Processing Integrity** | Model validation, input validation, output verification |
| **Confidentiality** | Data classification, encryption, access controls |
| **Privacy** | CCPA/GDPR compliance, data minimization, user consent |

### 5.3 Compliance Monitoring

| Control | Tool | Frequency |
|---------|------|-----------|
| AWS Config rules | AWS Config | Continuous |
| Security Hub findings | AWS Security Hub | Continuous |
| Container image scanning | ECR + Trivy | Every build |
| SAST (Python) | Bandit + Semgrep | Every PR |
| Dependency scanning | Dependabot + Snyk | Daily |
| Penetration testing | Third-party vendor | Annually |
| GPU security audit | Internal team | Semi-annually |

---

## 6. Incident Response

### 6.1 Security Incident Classification

| Severity | Examples | Response Time | Response Team |
|----------|---------|---------------|---------------|
| P1 - Critical | Model exfiltration, data breach, GPU cluster compromise | 15 minutes | CISO, Security, SRE, Legal |
| P2 - High | Unauthorized API access, DDoS attack, credential leak | 1 hour | Security, SRE |
| P3 - Medium | Vulnerability in dependency, failed security scan | 24 hours | Security, DevOps |
| P4 - Low | Policy violation, access anomaly | 1 week | Security Lead |

### 6.2 Incident Response Process

1. **Detection:** Alert from GuardDuty, WAF, CloudTrail, or anomaly detection
2. **Triage:** On-call SRE assesses severity and engages response team
3. **Containment:** Isolate affected resources (revoke credentials, block IPs, cordon GPU nodes)
4. **Eradication:** Remove threat (patch vulnerability, rotate credentials, rebuild containers)
5. **Recovery:** Restore services from known-good state, verify model integrity
6. **Post-Incident:** Blameless postmortem, update policies, implement preventive measures

### 6.3 Evidence Preservation

- CloudTrail logs retained for 365 days (immutable in S3 with Object Lock)
- VPC Flow Logs retained for 90 days
- Inference request/response logs retained for 30 days
- GPU health metrics retained for 90 days
- All evidence preserved per legal hold requirements

---

## 7. Vulnerability Management

### 7.1 Scanning Schedule

| Scan Type | Frequency | Tool | Scope |
|-----------|-----------|------|-------|
| Container image scan | Every build | ECR scanning + Trivy | All inference images |
| Python dependency scan | Daily | Dependabot + Snyk | All Python packages |
| SAST (Static) | Every PR | Bandit + Semgrep | Python inference code |
| Infrastructure scan | Weekly | AWS Config + Security Hub | All AWS resources |
| GPU driver CVE check | Weekly | NVIDIA Security Bulletin | GPU driver versions |
| DAST (Dynamic) | Monthly | OWASP ZAP | Inference API endpoints |
| Penetration test | Annually | Third-party vendor | Full platform |

### 7.2 Remediation SLAs

| Severity | CVSS Score | Remediation SLA | Escalation |
|----------|-----------|-----------------|------------|
| Critical | 9.0 - 10.0 | 24 hours | CISO immediately |
| High | 7.0 - 8.9 | 7 days | Security Lead |
| Medium | 4.0 - 6.9 | 30 days | SRE Lead |
| Low | 0.1 - 3.9 | 90 days | Next sprint |

---

## 8. Security Training

| Training | Audience | Frequency | Delivery |
|----------|----------|-----------|----------|
| ML security awareness | All ML engineers | Annually | Online module |
| Secure inference practices | Inference team | Quarterly | Workshop |
| Incident response drill | SRE + Security | Semi-annually | Tabletop exercise |
| GPU security hardening | Platform team | Annually | Hands-on lab |
| Adversarial ML defense | Data scientists | Semi-annually | Research seminar |

---

**Document Author:** Gopi Krishna Vajrala
**Review Status:** Approved
**Approved By:** Chief Information Security Officer
**Next Review Date:** 2026-08-21
