<div align="center">

# ADR-003: AWS as Primary Cloud Provider

![Status](https://img.shields.io/badge/Status-ACCEPTED-brightgreen?style=for-the-badge)
![Category](https://img.shields.io/badge/Category-Cloud_Provider-blue?style=for-the-badge)
![Priority](https://img.shields.io/badge/Priority-Critical-red?style=for-the-badge)

</div>

---

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║   DECISION SUMMARY                                                         ║
║                                                                            ║
║   Use Amazon Web Services (AWS) as the primary cloud provider for ECTP.    ║
║                                                                            ║
║   AWS offers the broadest service catalog, highest adoption in Higher      ║
║   Education, comprehensive compliance certifications (FERPA, HIPAA,        ║
║   SOC2, FedRAMP), and the largest partner ecosystem -- making it the       ║
║   optimal choice for all ECTP infrastructure, compute, storage, and        ║
║   managed services.                                                        ║
║                                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Document Metadata

| Field | Details |
|:---|:---|
| **ADR Number** | ADR-003 |
| **Title** | AWS as Primary Cloud Provider |
| **Author** | Gopi Krishna Vajrala |
| **Date** | 2026-02-16 |
| **Status** | ![ACCEPTED](https://img.shields.io/badge/ACCEPTED-brightgreen?style=flat-square) |
| **Reviewers** | Platform Architecture Team |
| **Category** | Cloud Provider Selection |
| **Supersedes** | N/A |

---

## Context

### Problem Statement

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   ECTP requires a cloud provider for all infrastructure, compute,        │
│   storage, and managed services.                                         │
│                                                                          │
│   Key Requirements:                                                      │
│   ├── Broadest service catalog for enterprise workloads                  │
│   ├── Strong adoption in Higher Education institutions                   │
│   ├── Comprehensive compliance certifications                            │
│   ├── Robust security services and tooling                               │
│   ├── Mature cost management and optimization tools                      │
│   └── Large partner ecosystem (Ellucian, ServiceNow, etc.)              │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Decision

> **We will use Amazon Web Services (AWS) as the primary cloud provider for ECTP.**

---

## Rationale

### Key Decision Drivers

<table>
<tr>
<td width="50%" valign="top">

```
┌─────────────────────────────┐
│   Market Leadership         │
│                             │
│   AWS is the market leader  │
│   with the broadest service │
│   catalog available.        │
└─────────────────────────────┘
```

</td>
<td width="50%" valign="top">

```
┌─────────────────────────────┐
│   Higher Ed Adoption        │
│                             │
│   Highest adoption rate     │
│   among Higher Education    │
│   institutions nationally.  │
└─────────────────────────────┘
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

```
┌─────────────────────────────┐
│   Compliance & Security     │
│                             │
│   FERPA, HIPAA, SOC2, and  │
│   FedRAMP compliance        │
│   certifications.           │
└─────────────────────────────┘
```

</td>
<td width="50%" valign="top">

```
┌─────────────────────────────┐
│   Partner Ecosystem         │
│                             │
│   Largest partner ecosystem │
│   for Higher Ed: Ellucian,  │
│   ServiceNow, and more.     │
└─────────────────────────────┘
```

</td>
</tr>
</table>

### Full Rationale Breakdown

| # | Factor | Details |
|:---:|:---|:---|
| 1 | **Market Leadership** | Broadest service catalog among all cloud providers |
| 2 | **Higher Ed Adoption** | Highest adoption in Higher Education institutions |
| 3 | **Compliance** | FERPA, HIPAA, SOC2, FedRAMP compliance certifications |
| 4 | **Partner Ecosystem** | Largest partner ecosystem for Higher Ed (Ellucian, ServiceNow) |
| 5 | **Security Services** | Comprehensive tooling: GuardDuty, Security Hub, Inspector |
| 6 | **Cost Management** | Mature tools: Cost Explorer, Budgets, Savings Plans |
| 7 | **Regional Presence** | Strong presence in us-east-1 (closest to many institutions) |

### Cloud Provider Comparison

<table>
<tr>
<th width="33%">AWS</th>
<th width="33%">Azure</th>
<th width="33%">GCP</th>
</tr>
<tr>
<td>

```
┌────────────────┐
│      AWS       │
│                │
│  Score: 9.5/10 │
│  ★★★★★★★★★★   │
│                │
│   SELECTED     │
└────────────────┘
```

- Market leader
- Broadest services
- Best Higher Ed fit
- Top compliance
- Best cost tools

</td>
<td>

```
┌────────────────┐
│     Azure      │
│                │
│  Score: 7.5/10 │
│  ★★★★★★★★☆☆   │
│                │
│   CONSIDERED   │
└────────────────┘
```

- Strong enterprise
- Good compliance
- MS integration
- Growing Higher Ed
- Complex pricing

</td>
<td>

```
┌────────────────┐
│      GCP       │
│                │
│  Score: 6.5/10 │
│  ★★★★★★★☆☆☆   │
│                │
│   CONSIDERED   │
└────────────────┘
```

- Best data/ML
- Strong Kubernetes
- Simpler pricing
- Smaller Higher Ed
- Fewer services

</td>
</tr>
</table>

### Compliance Coverage

```
  Compliance Certifications
  ─────────────────────────────────────────────────────

  FERPA     ██████████████████████████████████████  AWS: Full Coverage
  HIPAA     ██████████████████████████████████████  AWS: Full Coverage
  SOC2      ██████████████████████████████████████  AWS: Full Coverage
  FedRAMP   ██████████████████████████████████████  AWS: Full Coverage

  ─────────────────────────────────────────────────────
```

---

## Consequences

### Positive Outcomes

| # | Outcome | Impact |
|:---:|:---|:---|
| &#9989; | **Broadest service availability** -- 200+ services covering all ECTP requirements | High |
| &#9989; | **Compliance certifications** -- Pre-certified for FERPA, HIPAA, SOC2, FedRAMP | High |
| &#9989; | **Higher Ed adoption** -- Proven track record with similar institutions | High |
| &#9989; | **Partner ecosystem** -- Direct integrations with Ellucian, ServiceNow | High |
| &#9989; | **Security tooling** -- GuardDuty, Security Hub, Inspector provide layered security | Medium |
| &#9989; | **Cost management** -- Cost Explorer, Budgets enable fine-grained cost control | Medium |
| &#9989; | **Regional proximity** -- us-east-1 provides low latency for many institutions | Medium |

### Negative Outcomes

| # | Outcome | Mitigation |
|:---:|:---|:---|
| &#9888; | **Risk of vendor lock-in** to a single cloud provider | Mitigated by containerization (Docker/ECS/EKS) and Terraform abstraction layer enabling future portability |

---

## References

| Resource | Link |
|:---|:---|
| AWS Official Documentation | [https://docs.aws.amazon.com](https://docs.aws.amazon.com) |
| AWS Compliance Programs | [https://aws.amazon.com/compliance/programs](https://aws.amazon.com/compliance/programs) |
| AWS for Education | [https://aws.amazon.com/education](https://aws.amazon.com/education) |
| AWS Well-Architected Framework | [https://aws.amazon.com/architecture/well-architected](https://aws.amazon.com/architecture/well-architected) |

---

<div align="center">

**Author:** Gopi Krishna Vajrala

![Status](https://img.shields.io/badge/Status-ACCEPTED-brightgreen?style=flat-square)
&nbsp;&nbsp;|&nbsp;&nbsp;
**ADR-003**
&nbsp;&nbsp;|&nbsp;&nbsp;
**2026-02-16**

</div>
