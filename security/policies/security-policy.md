# Enterprise Cloud Transformation Platform (ECTP)
# Security Policy

**Document ID:** ECTP-SEC-001
**Author:** Gopi Krishna Vajrala
**Version:** 2.0.0
**Date:** 2026-02-16
**Classification:** Internal - Confidential
**Status:** Approved

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 2.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Comprehensive security policy |
| 1.0.0 | 2026-02-16 | Gopi Krishna Vajrala | Initial release |

---

## 1. Access Control

### 1.1 Identity and Access Management

| Control | Implementation | Enforcement |
|---------|---------------|-------------|
| Multi-Factor Authentication | Required for all AWS console and VPN access | AWS IAM MFA policy |
| Single Sign-On (SSO) | AWS SSO with institutional IdP (SAML 2.0) | Enforced via IAM Identity Center |
| Least Privilege | Role-based access with minimal permissions | IAM Access Analyzer reviews |
| Service Accounts | Dedicated IAM roles for each service (no shared credentials) | IAM policy enforcement |
| Temporary Credentials | STS AssumeRole for all cross-account access | No long-lived access keys |

### 1.2 RBAC Role Definitions

| Role | AWS Access | Application Access | Approval |
|------|-----------|-------------------|----------|
| Platform Admin | Full ECTP account access | Admin API access | CISO |
| DevOps Engineer | ECS, ECR, CloudWatch, S3 | Deploy permissions | Tech Lead |
| Developer | Read-only production, full dev/qa | API read/write (non-prod) | Team Lead |
| Security Engineer | GuardDuty, WAF, CloudTrail, Config | Security audit API | CISO |
| DBA | RDS, ElastiCache, Secrets Manager | Database admin | DBA Lead |
| Read-Only Auditor | CloudTrail, Config, billing | Read-only API | Compliance Officer |

### 1.3 Access Review Schedule

| Activity | Frequency | Owner |
|----------|-----------|-------|
| IAM user/role access review | Quarterly | Security Lead |
| Service account credential rotation | Every 90 days | DevOps Lead |
| SSH key rotation | Every 90 days | SRE Lead |
| Privileged access review | Monthly | CISO |
| Dormant account cleanup | Monthly | Security Lead |

---

## 2. Encryption

### 2.1 Encryption at Rest

| Resource | Encryption Method | Key Management |
|----------|------------------|----------------|
| RDS PostgreSQL | AES-256 via AWS KMS | Customer-managed KMS key |
| S3 Buckets | AES-256 (SSE-KMS) | Customer-managed KMS key |
| EBS Volumes | AES-256 via AWS KMS | Customer-managed KMS key |
| ElastiCache Redis | AES-256 at-rest encryption | AWS-managed key |
| Secrets Manager | AES-256 via AWS KMS | Customer-managed KMS key |
| CloudWatch Logs | AES-256 via AWS KMS | Customer-managed KMS key |

### 2.2 Encryption in Transit

| Connection | Protocol | Minimum Version | Certificate |
|-----------|----------|-----------------|-------------|
| Client to ALB | TLS | 1.3 | ACM-managed certificate |
| ALB to ECS | TLS | 1.2 | Internal certificate |
| App to RDS | TLS | 1.2 | RDS CA certificate |
| App to ElastiCache | TLS | 1.2 | ElastiCache in-transit encryption |
| App to ServiceNow | TLS | 1.2 | ServiceNow certificate |
| App to Ellucian | TLS | 1.2 | Ellucian certificate |

### 2.3 KMS Key Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "KeyAdministration",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::ACCOUNT:role/ectp-key-admin"},
      "Action": ["kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*",
                  "kms:Put*", "kms:Update*", "kms:Revoke*", "kms:Disable*",
                  "kms:Get*", "kms:Delete*", "kms:ScheduleKeyDeletion"],
      "Resource": "*"
    },
    {
      "Sid": "KeyUsage",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::ACCOUNT:role/ectp-app-role"},
      "Action": ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
      "Resource": "*"
    }
  ]
}
```

---

## 3. Network Security

### 3.1 VPC Architecture

| Tier | Subnet Type | Resources | Internet Access |
|------|-------------|-----------|-----------------|
| Public | Public subnet | ALB, NAT Gateway | Direct (IGW) |
| Application | Private subnet | ECS tasks | Outbound only (NAT) |
| Data | Isolated subnet | RDS, ElastiCache | None |
| Management | Private subnet | Bastion (if needed) | Outbound only (NAT) |

### 3.2 Security Groups

| Security Group | Inbound Rules | Source |
|----------------|--------------|--------|
| ALB SG | HTTPS (443) | 0.0.0.0/0 (via WAF) |
| ECS SG | HTTP (8000) | ALB SG only |
| RDS SG | PostgreSQL (5432) | ECS SG only |
| ElastiCache SG | Redis (6379) | ECS SG only |

### 3.3 WAF Rules

| Rule | Action | Purpose |
|------|--------|---------|
| AWS Managed - Core Rule Set | Block | Common web exploits (SQLi, XSS) |
| AWS Managed - Known Bad Inputs | Block | Known malicious inputs |
| AWS Managed - IP Reputation | Block | Known malicious IPs |
| Rate Limiting | Block >1000 req/5min per IP | DDoS protection |
| Geo-blocking | Block non-US traffic | Reduce attack surface |
| Custom - SQL Injection | Block | Additional SQLi patterns |

### 3.4 Network Monitoring

- VPC Flow Logs enabled on all subnets (sent to CloudWatch Logs)
- GuardDuty enabled for threat detection
- AWS Shield Standard for DDoS protection
- DNS query logging via Route53 Resolver

---

## 4. Vulnerability Management

### 4.1 Scanning Schedule

| Scan Type | Frequency | Tool | Scope |
|-----------|-----------|------|-------|
| Container image scan | Every build (CI/CD) | Amazon ECR scanning | All container images |
| Dependency scan | Daily | GitHub Dependabot | Python dependencies |
| Infrastructure scan | Weekly | AWS Config + Security Hub | All AWS resources |
| DAST (Dynamic) | Monthly | OWASP ZAP | API endpoints |
| SAST (Static) | Every PR | Bandit + Semgrep | Python source code |
| Penetration test | Annually | Third-party vendor | Full platform |

### 4.2 Vulnerability Severity and SLA

| Severity | CVSS Score | Remediation SLA | Escalation |
|----------|-----------|-----------------|------------|
| Critical | 9.0 - 10.0 | 24 hours | CISO immediately |
| High | 7.0 - 8.9 | 7 days | Security Lead |
| Medium | 4.0 - 6.9 | 30 days | DevOps Lead |
| Low | 0.1 - 3.9 | 90 days | Next sprint |

### 4.3 Patch Management

- Critical patches applied within 24 hours of availability
- High-priority patches applied within the next maintenance window
- Regular patches bundled into monthly security patching window
- All patches tested in QA/UAT before production deployment
- Emergency patching follows the emergency change process

---

## 5. Incident Response

### 5.1 Security Incident Classification

| Category | Examples | Response Team |
|----------|---------|---------------|
| Data Breach | Unauthorized data access, data exfiltration | CISO, Legal, Security Team |
| Account Compromise | Stolen credentials, unauthorized access | Security Team, DevOps |
| Malware | Compromised container, supply chain attack | Security Team, SRE |
| DDoS Attack | Service degradation from traffic flood | SRE Team, AWS Support |
| Insider Threat | Unauthorized data access by employee | CISO, HR, Legal |

### 5.2 Security Incident Response Process

1. **Detection:** Alert from GuardDuty, WAF, CloudTrail, or manual report
2. **Containment:** Isolate affected resources (revoke credentials, block IPs, stop containers)
3. **Eradication:** Remove threat (patch vulnerability, rotate credentials, update WAF rules)
4. **Recovery:** Restore services from known-good state, verify integrity
5. **Post-Incident:** Blameless review, update policies, implement preventive measures
6. **Notification:** Comply with breach notification requirements (FERPA 72h, HIPAA 60 days)

### 5.3 Evidence Preservation

- CloudTrail logs retained for 365 days (immutable in S3)
- VPC Flow Logs retained for 90 days
- Application logs retained for 90 days
- GuardDuty findings retained for 90 days
- All evidence must be preserved per legal hold requirements

---

## 6. Data Classification

### 6.1 Data Classification Levels

| Level | Definition | Examples | Controls |
|-------|-----------|----------|----------|
| **Restricted** | Highest sensitivity, regulatory protected | SSN, FERPA records, HIPAA PHI, payment data | Encryption, access logging, DLP, annual audit |
| **Confidential** | Internal business-sensitive | Employee records, financial data, API keys | Encryption, role-based access, audit trail |
| **Internal** | Not for public disclosure | Architecture docs, runbooks, meeting notes | Authentication required, no public sharing |
| **Public** | Approved for public access | Marketing content, public APIs, job postings | No special controls |

### 6.2 Data Handling Requirements

| Action | Restricted | Confidential | Internal | Public |
|--------|-----------|-------------|----------|--------|
| Storage | Encrypted (KMS) | Encrypted (KMS) | Encrypted | No requirement |
| Transit | TLS 1.3 required | TLS 1.2+ required | TLS 1.2+ | HTTPS preferred |
| Access | Named individuals only | Role-based | Authenticated users | Anyone |
| Logging | Full audit trail | Audit trail | Standard logging | Standard logging |
| Retention | Per regulation | 7 years | 3 years | No limit |
| Disposal | Secure deletion + certificate | Secure deletion | Standard deletion | Standard deletion |
| Sharing | CISO approval required | Manager approval | Internal only | No restriction |

---

## 7. Compliance Requirements

### 7.1 FERPA (Family Educational Rights and Privacy Act)

| Requirement | Implementation |
|-------------|---------------|
| Access controls on student records | RBAC with named access to student data |
| Directory information designation | Configurable per institution policy |
| Breach notification | 72-hour notification process documented |
| Annual notification | Integrated with institutional process |
| Audit trail | CloudTrail + application-level logging |
| Data minimization | Only necessary student data stored |

### 7.2 HIPAA (Health Insurance Portability and Accountability Act)

| Requirement | Implementation |
|-------------|---------------|
| Business Associate Agreement (BAA) | Signed with AWS |
| PHI encryption at rest | AES-256 via KMS |
| PHI encryption in transit | TLS 1.2+ enforced |
| Access controls | Role-based, minimum necessary |
| Audit controls | Full audit trail via CloudTrail |
| Integrity controls | Checksums, immutable logging |
| Breach notification | 60-day notification process documented |
| Risk assessment | Annual HIPAA risk assessment |

### 7.3 SOC 2 (Service Organization Control)

| Trust Service Criteria | Implementation |
|----------------------|---------------|
| **Security** | WAF, encryption, IAM, vulnerability scanning |
| **Availability** | Multi-AZ, auto-scaling, DR plan, 99.9% SLA |
| **Processing Integrity** | Input validation, error handling, monitoring |
| **Confidentiality** | Data classification, encryption, access controls |
| **Privacy** | FERPA/HIPAA compliance, data minimization |

### 7.4 Compliance Monitoring

| Control | Tool | Frequency |
|---------|------|-----------|
| AWS Config rules | AWS Config | Continuous |
| Security Hub findings | AWS Security Hub | Continuous |
| Data classification scanning | Amazon Macie | Weekly |
| Access pattern analysis | IAM Access Analyzer | Daily |
| Compliance dashboard | Custom CloudWatch | Real-time |

---

## 8. Security Training and Awareness

| Training | Audience | Frequency | Delivery |
|----------|----------|-----------|----------|
| Security awareness | All IT staff | Annually | Online module |
| Cloud security best practices | Cloud team | Quarterly | Workshop |
| Incident response drill | SRE + Security | Semi-annually | Tabletop exercise |
| FERPA/HIPAA compliance | Data handlers | Annually | Online module |
| Secure coding practices | Developers | Quarterly | Code review sessions |

---

**Document Author:** Gopi Krishna Vajrala
**Review Status:** Approved
**Approved By:** Chief Information Security Officer
**Next Review Date:** 2026-08-16
