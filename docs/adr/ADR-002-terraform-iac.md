# ADR-002: Terraform for Multi-Region GPU Infrastructure as Code

**Status:** ACCEPTED
**Date:** 2026-02-21
**Author:** Gopi Krishna Vajrala
**Deciders:** Platform Engineering Team, Infrastructure Team
**Category:** Infrastructure

---

## Context

The Netflix LLM Platform requires reproducible provisioning of GPU infrastructure across three AWS regions (us-east-1, us-west-2, eu-west-1). The infrastructure includes:

- EKS clusters with GPU node groups (p4d.24xlarge instances with 8x A100 GPUs)
- ElastiCache Redis 7 clusters for user profile caching and feature vectors
- DynamoDB Global Tables for cross-region user data replication
- S3 buckets with cross-region replication for model artifact storage
- VPC networking with private subnets, NAT gateways, and VPC peering
- IAM roles, policies, and OIDC providers for CI/CD authentication
- Route53 latency-based routing with health checks
- CloudWatch dashboards and alarms for monitoring
- WAF rules and Shield Advanced for security

The infrastructure must be:
- Reproducible across all three regions with minimal configuration differences
- Version-controlled for audit trail and rollback capability
- Testable in staging before production application
- Modular to enable independent updates of individual components
- Drift-detectable to catch manual changes

## Decision

We adopt **Terraform** (HashiCorp Configuration Language) as the Infrastructure as Code tool for all multi-region GPU infrastructure provisioning.

## Alternatives Considered

### AWS CloudFormation

- **Pros:** Native AWS integration, no state management concerns, automatic drift detection, StackSets for multi-region
- **Cons:** AWS-only (limits future multi-cloud), verbose YAML/JSON syntax, slower iteration cycle, limited module reusability, poor error messages
- **Rejected because:** CloudFormation StackSets add operational complexity for multi-region GPU deployments. The verbose syntax makes GPU-specific configurations (NVIDIA device plugin, Triton config maps) difficult to maintain. No support for non-AWS resources (GitHub OIDC, Datadog integration).

### Pulumi

- **Pros:** Real programming languages (Python, TypeScript), strong typing, familiar paradigms, good AWS support
- **Cons:** Smaller ecosystem, fewer community modules, team unfamiliar with Pulumi patterns, state management via Pulumi Cloud adds vendor dependency
- **Rejected because:** The team has extensive Terraform experience. Pulumi's smaller ecosystem means fewer pre-built modules for GPU infrastructure patterns. The state management dependency on Pulumi Cloud is a concern for a critical infrastructure pipeline.

### AWS CDK

- **Pros:** TypeScript/Python, generates CloudFormation, L2/L3 constructs for common patterns
- **Cons:** Still generates CloudFormation (inherits its limitations), abstraction can hide important details, less mature for GPU workloads, limited community patterns for ML infrastructure
- **Rejected because:** CDK abstractions can obscure GPU-specific configurations that require precise control (NVIDIA device plugin versions, GPU topology awareness, EFA networking). The CloudFormation backend limits deployment speed.

## Consequences

### Positive

- **Module reusability:** Custom Terraform modules encapsulate GPU cluster patterns, enabling consistent provisioning across regions with region-specific overrides.
- **State management:** Remote state in S3 with DynamoDB locking enables team collaboration and prevents concurrent modification conflicts.
- **Plan/Apply workflow:** `terraform plan` provides a preview of infrastructure changes before application, critical for GPU infrastructure where misconfigurations can be costly ($32/hr per p4d.24xlarge).
- **Ecosystem:** Rich provider ecosystem for AWS, Kubernetes, Helm, and monitoring tools. Community modules for EKS, VPC, and IAM reduce boilerplate.
- **Drift detection:** `terraform plan` detects manual changes, ensuring infrastructure matches the declared state.

### Negative

- **State file management:** Requires careful handling of the state file. Mitigated with S3 backend, encryption, and DynamoDB locking.
- **HCL learning curve:** HCL is not a general-purpose language; complex logic (conditional GPU provisioning) can be awkward. Mitigated with clear module interfaces.
- **Provider version management:** AWS provider updates can introduce breaking changes. Mitigated with version pinning and automated testing.

### Module Structure

```
terraform/
├── modules/
│   ├── gpu_cluster/           # EKS + GPU node groups + NVIDIA plugins
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── gpu_nodegroup.tf   # p4d.24xlarge configuration
│   │   ├── nvidia_plugin.tf   # NVIDIA device plugin + DCGM
│   │   └── triton.tf          # Triton Inference Server deployment
│   ├── networking/            # VPC, subnets, security groups, EFA
│   │   ├── main.tf
│   │   ├── vpc.tf
│   │   ├── efa.tf             # Elastic Fabric Adapter for GPU nodes
│   │   └── outputs.tf
│   ├── inference/             # Model serving infrastructure
│   │   ├── main.tf
│   │   ├── model_repo.tf      # S3 model repository
│   │   ├── triton_config.tf   # Triton configuration
│   │   └── outputs.tf
│   ├── cache/                 # ElastiCache Redis clusters
│   │   ├── main.tf
│   │   ├── redis.tf
│   │   └── outputs.tf
│   ├── monitoring/            # CloudWatch, Prometheus, Grafana
│   │   ├── main.tf
│   │   ├── dashboards.tf
│   │   ├── alarms.tf
│   │   └── outputs.tf
│   └── security/              # IAM, WAF, Shield, KMS
│       ├── main.tf
│       ├── iam.tf
│       ├── waf.tf
│       ├── oidc.tf            # GitHub Actions OIDC provider
│       └── outputs.tf
├── environments/
│   ├── production/
│   │   ├── us-east-1/         # Primary region
│   │   │   ├── main.tf
│   │   │   ├── terraform.tfvars
│   │   │   └── backend.tf
│   │   ├── us-west-2/         # Secondary region
│   │   │   ├── main.tf
│   │   │   ├── terraform.tfvars
│   │   │   └── backend.tf
│   │   └── eu-west-1/         # EU region
│   │       ├── main.tf
│   │       ├── terraform.tfvars
│   │       └── backend.tf
│   └── staging/
│       └── us-east-1/
│           ├── main.tf
│           ├── terraform.tfvars
│           └── backend.tf
└── global/                    # Cross-region resources
    ├── route53.tf             # Latency-based routing
    ├── dynamodb_global.tf     # Global tables
    ├── s3_replication.tf      # Cross-region model replication
    └── iam_global.tf          # Global IAM roles
```

---

**References:**

- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest)
- [Terraform EKS Module](https://registry.terraform.io/modules/terraform-aws-modules/eks/aws/latest)
- [AWS GPU Instances](https://aws.amazon.com/ec2/instance-types/p4/)
