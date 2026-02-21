# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Networking Variables
# =============================================================================
# Author: Gopi Krishna Vajrala
# Purpose: Defines all input variables for the ECTP networking module.
#          Each variable includes a detailed description, type constraint,
#          and validation rules where applicable.
#
# Variable Naming Convention:
#   - snake_case for all variable names (Terraform standard)
#   - Descriptive names that indicate the resource and attribute
#   - Default values provided for non-sensitive, commonly-used settings
#   - No defaults for environment-specific values (forces explicit configuration)
#
# Security Note:
#   - No sensitive values (passwords, keys) are defined in this module.
#   - CIDR blocks should be carefully planned to avoid overlapping with
#     existing on-premises or other VPC networks.
# =============================================================================

# -----------------------------------------------------------------------------
# Project Identification Variables
# -----------------------------------------------------------------------------

variable "project_name" {
  # WHAT: A short identifier for the project, used in resource naming.
  # WHY: Consistent naming across all resources enables:
  #       1. Cost allocation via AWS Cost Explorer tags
  #       2. IAM policy scoping using resource name patterns
  #       3. Easy identification in the AWS console
  #       4. Automated governance policies (e.g., tag-based access control)
  # SECURITY: Used in IAM policy resource ARN patterns; keep it short and
  #           alphanumeric to avoid policy parsing issues.
  description = "The name of the project (e.g., 'ectp'). Used as a prefix in resource naming for identification, cost allocation, and IAM policy scoping."
  type        = string

  # Default to "ectp" for the Enterprise Cloud Transformation Platform.
  # WHY: Provides a sensible default that matches the project name.
  default = "ectp"

  # Validate that the project name is lowercase alphanumeric with hyphens only.
  # WHY: AWS resource names have restrictions; some services don't allow
  #       uppercase letters or special characters. Enforcing this early
  #       prevents cryptic errors during resource creation.
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project_name))
    error_message = "Project name must be 2-21 characters, start with a lowercase letter, and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "environment" {
  # WHAT: The deployment environment tier (dev, staging, prod).
  # WHY: Environment isolation is critical for:
  #       1. Preventing dev/test data from mixing with production
  #       2. Applying different security policies per environment
  #       3. Cost tracking and budget allocation per environment
  #       4. Different scaling configurations (dev = small, prod = large)
  # SECURITY: Production environments should have stricter access controls,
  #           longer log retention, and more restrictive network policies.
  description = "The deployment environment (dev, staging, prod). Determines resource sizing, security policies, and naming. Production environments enforce stricter security controls."
  type        = string

  # Validate that only known environment names are used.
  # WHY: Prevents typos (e.g., "prodd") that could create orphaned resources
  #       in unmonitored environments.
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod. Other values are not supported to prevent configuration drift."
  }
}

# -----------------------------------------------------------------------------
# VPC Configuration Variables
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  # WHAT: The IPv4 CIDR block for the entire VPC.
  # WHY: Defines the total IP address space available for all subnets.
  #       A /16 provides 65,536 addresses, sufficient for enterprise deployments.
  # SECURITY: Must use RFC 1918 private address ranges (10.0.0.0/8,
  #           172.16.0.0/12, or 192.168.0.0/16). Using public IP ranges
  #           would prevent communication with those public addresses.
  # PLANNING: The CIDR must not overlap with:
  #           - On-premises networks (if VPN/DirectConnect is used)
  #           - Other VPCs (if VPC peering is used)
  #           - Partner/vendor networks (if shared services are used)
  description = "The IPv4 CIDR block for the VPC (e.g., '10.0.0.0/16'). Must use RFC 1918 private ranges and not overlap with on-premises or other VPC networks. A /16 provides 65,536 addresses for all subnets."
  type        = string

  # Default to 10.0.0.0/16 which provides ample address space.
  # WHY: 10.0.0.0/16 is a common choice for isolated VPCs with no peering.
  #       For multi-VPC architectures, each VPC should use a different /16.
  default = "10.0.0.0/16"

  # Validate that the CIDR is a valid IPv4 CIDR block.
  # WHY: An invalid CIDR would cause the VPC creation to fail with a
  #       confusing AWS API error. Validating early provides a clear message.
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "VPC CIDR must be a valid IPv4 CIDR block (e.g., '10.0.0.0/16')."
  }
}

# -----------------------------------------------------------------------------
# Subnet CIDR Configuration Variables
# -----------------------------------------------------------------------------

variable "public_subnet_cidrs" {
  # WHAT: List of CIDR blocks for public subnets (one per AZ).
  # WHY: Public subnets host load balancers and NAT Gateways that need
  #       internet connectivity via the Internet Gateway.
  # SECURITY: Keep public subnets small (/24 = 251 usable IPs) since they
  #           should only contain a few resources (ALBs, NAT Gateways).
  #           Larger subnets waste address space and increase the blast radius.
  # PLANNING: Must be within the VPC CIDR range and not overlap with
  #           other subnet CIDRs in this module.
  description = "List of CIDR blocks for public subnets, one per availability zone. Public subnets host ALBs and NAT Gateways. Use /24 blocks for adequate address space."
  type        = list(string)

  # Default: Three /24 subnets in the 10.0.1.x - 10.0.3.x range.
  # WHY: Placed at the beginning of the VPC CIDR for easy visual identification.
  #       Each /24 provides 251 usable IPs, more than enough for ALBs and NATs.
  default = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "private_app_subnet_cidrs" {
  # WHAT: List of CIDR blocks for private application subnets (one per AZ).
  # WHY: Private app subnets host ECS Fargate tasks, Lambda functions, and
  #       other compute resources that need outbound internet (via NAT) but
  #       should never be directly accessible from the internet.
  # SECURITY: These subnets are the compute tier; they should only accept
  #           traffic from the public ALB and only send traffic to the data tier.
  # SIZING: /24 provides 251 usable IPs. ECS Fargate uses one IP per task,
  #          so this supports up to ~250 concurrent tasks per AZ.
  #          For larger deployments, use /20 (4,091 IPs per AZ).
  description = "List of CIDR blocks for private application subnets, one per AZ. Hosts ECS Fargate tasks and compute workloads. Each subnet supports up to 251 concurrent tasks with /24 blocks."
  type        = list(string)

  # Default: Three /24 subnets in the 10.0.11.x - 10.0.13.x range.
  # WHY: Offset from public subnets (10.0.1-3.x) to leave room for growth.
  #       The 10.0.11-13.x range is visually distinct from public subnets.
  default = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
}

variable "private_data_subnet_cidrs" {
  # WHAT: List of CIDR blocks for private data subnets (one per AZ).
  # WHY: Data subnets are isolated from the compute tier and host databases
  #       (RDS PostgreSQL), caches (ElastiCache), and other data stores
  #       containing sensitive student and institutional data.
  # SECURITY: These subnets should ONLY be accessible from private app subnets
  #           on specific database ports. Public subnet access is blocked
  #           at both the NACL and security group levels.
  # COMPLIANCE: FERPA requires that student data be stored in isolated
  #             network segments with access limited to authorized systems.
  description = "List of CIDR blocks for private data subnets, one per AZ. Hosts RDS, ElastiCache, and other data stores. Isolated from public subnets for FERPA compliance."
  type        = list(string)

  # Default: Three /24 subnets in the 10.0.21.x - 10.0.23.x range.
  # WHY: Further offset from app subnets to maintain clear visual separation
  #       and leave room for additional app subnets in the 10.0.14-20.x range.
  default = ["10.0.21.0/24", "10.0.22.0/24", "10.0.23.0/24"]
}

variable "isolated_subnet_cidrs" {
  # WHAT: List of CIDR blocks for isolated subnets (one per AZ).
  # WHY: Isolated subnets have NO internet access at all (no NAT Gateway,
  #       no Internet Gateway route). They are used for the most sensitive
  #       data processing operations that must never communicate externally.
  # SECURITY: Maximum network isolation. Resources here can only communicate
  #           with other VPC resources and AWS services via VPC Endpoints.
  #           Even if compromised, resources cannot exfiltrate data to the internet.
  # USE CASES:
  #   - Processing raw student PII (Social Security Numbers, FAFSA data)
  #   - Running compliance audit batch jobs
  #   - Key management and cryptographic operations
  description = "List of CIDR blocks for isolated subnets, one per AZ. These subnets have NO internet access (no NAT/IGW routes). Used for highly sensitive data processing that must never egress the VPC."
  type        = list(string)

  # Default: Three /24 subnets in the 10.0.31.x - 10.0.33.x range.
  # WHY: Placed in a clearly distinct range to visually identify isolated
  #       subnets in network diagrams and flow log analysis.
  default = ["10.0.31.0/24", "10.0.32.0/24", "10.0.33.0/24"]
}

# -----------------------------------------------------------------------------
# Availability Zone Configuration
# -----------------------------------------------------------------------------

variable "az_count" {
  # WHAT: The number of Availability Zones to deploy across.
  # WHY: Multi-AZ deployment provides fault tolerance against AZ-level failures.
  #       Using 3 AZs provides:
  #       1. Quorum-based systems (e.g., RDS Multi-AZ) work optimally with 3 AZs
  #       2. Losing one AZ still leaves 2 operational (66% capacity)
  #       3. Most AWS regions have at least 3 AZs available
  # SECURITY: More AZs means better fault isolation; if one AZ is compromised
  #           (e.g., physical security breach), the others are unaffected.
  # COST: Each additional AZ adds NAT Gateway costs (~$32/month per AZ).
  #        Dev environments may use 2 AZs to save costs while maintaining
  #        basic high availability.
  description = "Number of Availability Zones to deploy across. Minimum 2 for HA, recommended 3 for production. Each AZ adds ~$32/month for NAT Gateway costs. Use 2 for dev to save costs."
  type        = number

  # Default to 3 AZs for production-grade resilience.
  # WHY: 3 AZs is the standard for enterprise AWS deployments, balancing
  #       cost with resilience. Most AWS regions support at least 3 AZs.
  default = 3

  # Validate that at least 2 AZs are used (minimum for high availability).
  # WHY: A single AZ provides no fault tolerance; if it fails, the entire
  #       application goes down. 2 AZs is the absolute minimum for HA.
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 6
    error_message = "AZ count must be between 2 and 6. Minimum 2 for high availability, maximum 6 (the most AZs in any AWS region)."
  }
}

# -----------------------------------------------------------------------------
# Logging and Monitoring Configuration
# -----------------------------------------------------------------------------

variable "flow_log_retention_days" {
  # WHAT: Number of days to retain VPC Flow Logs in CloudWatch.
  # WHY: Flow logs are essential for security monitoring, compliance auditing,
  #       and forensic analysis after security incidents.
  # SECURITY: Retention must meet compliance requirements:
  #           - FERPA: No specific retention requirement, but 1-3 years recommended
  #           - HIPAA: 6 years minimum
  #           - SOC 2: 1 year minimum
  #           - PCI DSS: 1 year minimum
  #           Shorter retention for dev environments saves costs while
  #           maintaining basic troubleshooting capability.
  # COST: CloudWatch Logs storage costs $0.03/GB/month. High-traffic VPCs
  #        can generate significant log volume.
  description = "Number of days to retain VPC Flow Logs in CloudWatch. Must meet compliance requirements: FERPA recommends 1-3 years, HIPAA requires 6 years minimum. Use 90 days for dev, 365+ for prod."
  type        = number

  # Default to 90 days for cost-effective dev/staging retention.
  # WHY: 90 days provides sufficient troubleshooting window for non-production
  #       environments. Production should override this to 365 or more.
  default = 90

  # Validate against CloudWatch's supported retention values.
  # WHY: CloudWatch only supports specific retention periods; arbitrary
  #       values will cause an API error.
  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.flow_log_retention_days
    )
    error_message = "Flow log retention must be a CloudWatch-supported value: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, or 3653 days."
  }
}

# -----------------------------------------------------------------------------
# Encryption Configuration
# -----------------------------------------------------------------------------

variable "kms_key_arn" {
  # WHAT: ARN of a KMS key for encrypting CloudWatch Log Groups.
  # WHY: Encrypting logs at rest protects sensitive metadata (IP addresses,
  #       connection patterns) from unauthorized access.
  # SECURITY: Without KMS encryption, anyone with CloudWatch read permissions
  #           can view flow log data. KMS adds a second authorization gate.
  #           The KMS key policy controls who can decrypt the logs.
  # NOTE: Set to null to use CloudWatch's default encryption (AWS-managed key).
  #        Custom KMS keys provide more control but add key management overhead.
  description = "ARN of the KMS key for encrypting CloudWatch Log Groups. Provides encryption at rest for VPC Flow Logs. Set to null to use CloudWatch default encryption (AWS-managed key). Custom keys recommended for production."
  type        = string

  # Default to null, meaning CloudWatch uses its default AWS-managed encryption.
  # WHY: For dev environments, the default encryption is sufficient.
  #       Production should use a customer-managed KMS key for audit trail
  #       of who decrypted logs and fine-grained access control.
  default = null
}

# -----------------------------------------------------------------------------
# Tagging Configuration
# -----------------------------------------------------------------------------

variable "tags" {
  # WHAT: A map of additional tags to apply to all resources in this module.
  # WHY: Tags enable:
  #       1. Cost allocation and chargeback to departments/projects
  #       2. Automated governance (e.g., shut down untagged resources)
  #       3. Compliance tracking (e.g., "DataClassification" tag)
  #       4. Operational visibility (e.g., "OnCallTeam" tag)
  # SECURITY: Some organizations use tag-based IAM policies (ABAC) to
  #           control access. Consistent tagging is essential for ABAC to work.
  # NOTE: Module-specific tags (Module, Project, Environment, ManagedBy, Author)
  #        are added automatically in the locals block and will merge with
  #        these user-provided tags.
  description = "Additional tags to apply to all resources. Module-specific tags (Module, Project, Environment, ManagedBy, Author) are added automatically. Use this for organization-specific tags like CostCenter, DataClassification, etc."
  type        = map(string)

  # Default to an empty map (no additional tags).
  # WHY: The module already applies essential tags via locals.common_tags.
  #       Callers can add organization-specific tags as needed.
  default = {}
}
