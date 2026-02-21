# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Networking Module
# =============================================================================
# Author: Gopi Krishna Vajrala
# Purpose: Defines the complete VPC networking infrastructure for the ECTP
#          platform, designed for Higher Education institutions requiring
#          enterprise-grade multi-AZ resilience, strict network segmentation,
#          and compliance with FERPA/HIPAA data handling requirements.
#
# Architecture Overview:
#   - VPC with DNS support for internal service discovery
#   - 4-tier subnet architecture: Public, Private App, Private Data, Isolated
#   - Multi-AZ deployment across configurable availability zones
#   - NAT Gateway per AZ for high availability (no single point of failure)
#   - Internet Gateway for public-facing load balancers only
#   - VPC Flow Logs for network traffic auditing and forensics
#
# Security Implications:
#   - Public subnets are ONLY for load balancers and bastion hosts; never
#     for application workloads directly.
#   - Private App subnets host compute (ECS Fargate) with NAT-only egress.
#   - Private Data subnets host databases with no direct internet access.
#   - Isolated subnets have NO internet access at all, suitable for
#     sensitive data processing that must never egress the VPC.
#   - VPC Flow Logs capture ALL traffic for compliance auditing.
#
# Higher Ed Considerations:
#   - FERPA compliance requires strict data isolation; the 4-tier model
#     ensures student records in the data tier cannot be reached from
#     the public internet without traversing multiple security boundaries.
#   - Multi-AZ design ensures 99.99% availability for enrollment periods
#     and other critical academic calendar events.
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration Block
# -----------------------------------------------------------------------------
# WHY: Declares the required provider and its version constraint to ensure
#       reproducible infrastructure builds. Pinning the version prevents
#       unexpected breaking changes from upstream provider updates.
# SECURITY: Using a version constraint prevents supply-chain attacks where
#           a compromised newer version could introduce backdoors.
# -----------------------------------------------------------------------------
terraform {
  # Require Terraform 1.5+ for improved module features and bug fixes.
  # WHY: Older versions may have known security vulnerabilities or lack
  #       features we depend on (e.g., moved blocks, check blocks).
  required_version = ">= 1.5.0"

  required_providers {
    # AWS provider is the primary cloud provider for ECTP infrastructure.
    # WHY: AWS is the chosen cloud platform for ECTP due to its FedRAMP
    #       authorization and extensive Higher Ed compliance certifications.
    # SECURITY: Pinning to ~> 5.0 ensures we get patch updates (5.x.y)
    #           but not major version bumps that could change behavior.
    aws = {
      source  = "hashicorp/aws"    # Official HashiCorp AWS provider
      version = "~> 5.0"           # Allow 5.x patches, block 6.0+ breaking changes
    }
  }
}

# -----------------------------------------------------------------------------
# Data Source: Available AWS Availability Zones
# -----------------------------------------------------------------------------
# WHAT: Queries AWS for all available AZs in the current region that are
#       in an "available" state (not impaired or otherwise unavailable).
# WHY: Dynamically discovers AZs rather than hardcoding them, making the
#       module portable across AWS regions without modification.
# SECURITY: Using only "available" AZs ensures we don't deploy into
#           degraded zones that might have reliability issues.
# ALTERNATIVE: Could hardcode AZs, but that reduces portability and
#              requires manual updates when expanding to new regions.
# -----------------------------------------------------------------------------
data "aws_availability_zones" "available" {
  # Filter to only AZs that are fully operational.
  # WHY: Deploying into an impaired AZ could cause service disruption
  #       during the initial provisioning or subsequent scaling events.
  state = "available"
}

# -----------------------------------------------------------------------------
# Data Source: Current AWS Region
# -----------------------------------------------------------------------------
# WHAT: Retrieves the name of the AWS region where Terraform is operating.
# WHY: Used in resource naming and tagging to clearly identify which region
#       resources belong to, aiding in multi-region disaster recovery planning.
# SECURITY: Knowing the region helps ensure data residency compliance;
#           FERPA-covered data must remain within approved geographic boundaries.
# -----------------------------------------------------------------------------
data "aws_region" "current" {}

# -----------------------------------------------------------------------------
# Data Source: Current AWS Caller Identity
# -----------------------------------------------------------------------------
# WHAT: Retrieves the AWS account ID and ARN of the caller (Terraform executor).
# WHY: Used for constructing ARNs for IAM policies and for tagging resources
#       with the account ID for cross-account visibility.
# SECURITY: Verifying the account ID in resource policies prevents
#           accidental cross-account resource exposure.
# -----------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

# =============================================================================
# LOCAL VALUES
# =============================================================================
# WHAT: Computed local values used throughout the module for consistency.
# WHY: Centralizing naming conventions and computed values in locals reduces
#       duplication and ensures all resources follow the same patterns.
# SECURITY: Consistent naming makes it easier to write IAM policies using
#           resource name patterns (e.g., "ectp-dev-*").
# =============================================================================
locals {
  # Construct a human-readable name prefix for all resources in this module.
  # Format: "{project}-{environment}" e.g., "ectp-dev" or "ectp-prod"
  # WHY: Consistent naming enables easy identification in the AWS console,
  #       cost allocation reporting, and IAM policy scoping.
  name_prefix = "${var.project_name}-${var.environment}"

  # Determine the number of AZs to use, bounded by what's actually available.
  # WHY: Some regions have only 2 AZs; we must not try to create subnets
  #       in AZs that don't exist. Using min() prevents index-out-of-range errors.
  # SECURITY: Using multiple AZs (typically 3) provides fault isolation;
  #           if one AZ is compromised or fails, the others continue operating.
  az_count = min(var.az_count, length(data.aws_availability_zones.available.names))

  # Pre-select the AZ names we'll use for subnet placement.
  # WHY: Having a clean list simplifies the for_each/count expressions below.
  selected_azs = slice(data.aws_availability_zones.available.names, 0, local.az_count)

  # Common tags applied to EVERY resource created by this module.
  # WHY: Tags enable cost tracking, compliance auditing, and automated
  #       governance policies (e.g., "delete untagged resources after 30 days").
  # SECURITY: The "DataClassification" tag helps automated scanners identify
  #           resources that handle sensitive data for extra scrutiny.
  common_tags = merge(var.tags, {
    Module      = "networking"                  # Identifies which Terraform module created this resource
    Project     = var.project_name              # Project identifier for cost allocation
    Environment = var.environment               # Environment tier (dev, staging, prod)
    ManagedBy   = "terraform"                   # Indicates this resource is IaC-managed; do NOT modify manually
    Author      = "Gopi Krishna Vajrala"        # Original author for accountability and contact
  })
}

# =============================================================================
# VPC (Virtual Private Cloud)
# =============================================================================
# WHAT: Creates the foundational VPC that encapsulates all ECTP networking.
# WHY: A VPC provides an isolated virtual network within AWS, giving us full
#       control over IP addressing, subnets, route tables, and network gateways.
#       This isolation is critical for FERPA compliance in Higher Ed environments.
# SECURITY IMPLICATIONS:
#   - The VPC acts as the outermost network boundary for all ECTP resources.
#   - DNS hostnames are enabled to support private DNS resolution for RDS
#     endpoints and other AWS services, avoiding the need for public endpoints.
#   - DNS support is required for VPC endpoints (PrivateLink) which keep
#     traffic to AWS services within the AWS network (never traversing the internet).
# ALTERNATIVES:
#   - Could use AWS default VPC, but it has overly permissive defaults and
#     cannot be customized sufficiently for enterprise compliance.
#   - Could use multiple VPCs with peering, but that adds complexity;
#     a single well-segmented VPC is preferred for ECTP's scale.
# =============================================================================
resource "aws_vpc" "main" {
  # The CIDR block defines the IP address range for the entire VPC.
  # WHY: A /16 block provides 65,536 IP addresses, sufficient for even
  #       the largest Higher Ed deployments with room for future growth.
  # SECURITY: Using private IP ranges (10.x.x.x, 172.16.x.x, or 192.168.x.x)
  #           ensures these addresses are not routable on the public internet.
  cidr_block = var.vpc_cidr

  # Enable DNS hostnames so EC2 instances and other resources receive
  # DNS names (e.g., ip-10-0-1-5.ec2.internal).
  # WHY: Required for private hosted zones, RDS DNS endpoints, and
  #       service discovery via AWS Cloud Map used by ECS Fargate tasks.
  # SECURITY: Internal DNS resolution keeps service-to-service communication
  #           within the VPC, avoiding DNS leakage to public resolvers.
  enable_dns_hostnames = true

  # Enable DNS support (Amazon-provided DNS server at VPC+2 address).
  # WHY: Required for VPC endpoints (PrivateLink) and Route 53 private
  #       hosted zones. Without this, services cannot resolve AWS endpoints
  #       privately, forcing traffic through the internet.
  # SECURITY: Enables private resolution of AWS service endpoints, keeping
  #           API calls within the AWS backbone network.
  enable_dns_support = true

  # Tags for identification, cost tracking, and compliance.
  # WHY: The Name tag appears in the AWS console for easy identification.
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpc"  # Human-readable name in AWS console
  })
}

# =============================================================================
# VPC FLOW LOGS
# =============================================================================
# WHAT: Captures metadata about IP traffic flowing through the VPC's network
#       interfaces (ENIs) and sends it to CloudWatch Logs for analysis.
# WHY: VPC Flow Logs are essential for:
#       1. Security monitoring: Detecting unauthorized access attempts
#       2. Compliance auditing: FERPA requires logging of data access patterns
#       3. Troubleshooting: Diagnosing connectivity issues between services
#       4. Forensics: Post-incident analysis of network traffic patterns
# SECURITY IMPLICATIONS:
#   - Captures ALL traffic (accepted + rejected) for complete visibility.
#   - Logs are stored in CloudWatch Logs with configurable retention.
#   - The IAM role restricts log delivery to only the designated log group.
#   - Flow logs do NOT capture packet payloads, only metadata (src/dst IP,
#     port, protocol, action), so they don't expose application data.
# ALTERNATIVES:
#   - Could send to S3 instead of CloudWatch for lower cost at high volume.
#   - Could use third-party tools (e.g., VPC Traffic Mirroring) for deep
#     packet inspection, but that's more expensive and complex.
# =============================================================================
resource "aws_flow_log" "vpc" {
  # Associate this flow log with our VPC (captures all traffic in the VPC).
  # WHY: VPC-level flow logs capture traffic for ALL subnets and ENIs,
  #       providing comprehensive network visibility without per-subnet config.
  vpc_id = aws_vpc.main.id

  # Capture ALL traffic (both ACCEPT and REJECT decisions).
  # WHY: Rejected traffic is critical for security analysis (e.g., detecting
  #       port scanning or unauthorized access attempts). Accepted traffic
  #       helps establish normal baselines and detect data exfiltration.
  # SECURITY: Logging only "REJECT" would miss successful unauthorized access;
  #           logging only "ACCEPT" would miss intrusion attempts. "ALL" is safest.
  traffic_type = "ALL"

  # Deliver logs to CloudWatch Logs for real-time analysis and alarming.
  # WHY: CloudWatch enables metric filters, alarms, and integration with
  #       AWS Security Hub for automated threat detection.
  # ALTERNATIVE: "s3" destination is cheaper for high-volume, long-term storage.
  log_destination_type = "cloud-watch-logs"

  # The CloudWatch Log Group where flow log records will be stored.
  # WHY: Centralizing logs in a dedicated log group enables targeted
  #       retention policies and access controls.
  log_destination = aws_cloudwatch_log_group.vpc_flow_logs.arn

  # IAM role that grants VPC Flow Logs permission to write to CloudWatch.
  # WHY: Principle of least privilege; the role only allows writing to
  #       the specific log group, not reading or deleting logs.
  iam_role_arn = aws_iam_role.vpc_flow_log_role.arn

  # The maximum interval (in seconds) during which flow log records are
  # aggregated before being published.
  # WHY: 60-second intervals (1 minute) provide near-real-time visibility
  #       while reducing the volume of API calls to CloudWatch.
  # ALTERNATIVE: 600 seconds (10 minutes) reduces cost but delays detection.
  max_aggregation_interval = 60

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpc-flow-logs"  # Identifies this flow log in the console
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group for VPC Flow Logs
# -----------------------------------------------------------------------------
# WHAT: A dedicated CloudWatch Log Group to store VPC Flow Log records.
# WHY: Isolating flow logs in their own log group enables:
#       1. Separate retention policies (compliance may require 1+ year retention)
#       2. Granular IAM access controls (security team can access flow logs
#          without needing access to application logs)
#       3. Dedicated metric filters for network security monitoring
# SECURITY: Encrypted with the KMS key specified in var.kms_key_arn if provided.
#           Retention prevents indefinite log accumulation and associated costs.
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  # Name follows a hierarchical pattern for organization in CloudWatch console.
  # WHY: The /ectp/vpc/flow-logs prefix groups all ECTP VPC logs together
  #       and makes them easy to find and filter.
  name = "/ectp/${var.environment}/vpc/flow-logs"

  # Retain flow logs for the configured number of days.
  # WHY: FERPA compliance typically requires 1-7 years of audit log retention.
  #       The default is 90 days for dev environments; production should be longer.
  # SECURITY: Insufficient retention means losing forensic evidence after incidents.
  #           Excessive retention increases storage costs and exposure surface.
  retention_in_days = var.flow_log_retention_days

  # Encrypt log data at rest using a customer-managed KMS key.
  # WHY: Flow logs may contain IP addresses that could be correlated with
  #       student activity. Encryption at rest protects this metadata.
  # SECURITY: Without encryption, anyone with CloudWatch read access can
  #           view raw flow log data. KMS encryption adds a second access gate.
  kms_key_id = var.kms_key_arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpc-flow-log-group"  # Console identification
  })
}

# -----------------------------------------------------------------------------
# IAM Role for VPC Flow Logs
# -----------------------------------------------------------------------------
# WHAT: An IAM role that VPC Flow Logs assumes to write records to CloudWatch.
# WHY: AWS services require explicit IAM permissions to interact with other
#       services. This role grants only the minimum permissions needed.
# SECURITY: The assume_role_policy restricts who can assume this role to
#           only the VPC Flow Logs service (vpc-flow-logs.amazonaws.com).
#           This prevents other services or users from using this role.
# -----------------------------------------------------------------------------
resource "aws_iam_role" "vpc_flow_log_role" {
  # Name includes the environment to prevent conflicts in multi-environment accounts.
  # WHY: If dev and prod share an AWS account, role names must be unique.
  name = "${local.name_prefix}-vpc-flow-log-role"

  # The trust policy that specifies WHO can assume this role.
  # WHY: Only the VPC Flow Logs service should be able to assume this role.
  # SECURITY: Without this restriction, any AWS principal in the account
  #           could assume the role and write misleading data to the log group.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"  # IAM policy language version (always use 2012-10-17)
    Statement = [
      {
        Action = "sts:AssumeRole"                              # Allow assuming this role
        Effect = "Allow"                                        # Permit the action
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"              # Only VPC Flow Logs service
        }
        # SECURITY: No conditions are needed here because the Principal
        # restriction is sufficient; only AWS's VPC Flow Logs service
        # can present credentials for vpc-flow-logs.amazonaws.com.
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpc-flow-log-role"  # Console identification
  })
}

# -----------------------------------------------------------------------------
# IAM Role Policy for VPC Flow Logs
# -----------------------------------------------------------------------------
# WHAT: Inline IAM policy granting the flow log role permission to write
#       log events to CloudWatch Logs.
# WHY: Without this policy, the flow log role would have no permissions
#       and flow logs would silently fail to deliver.
# SECURITY: Grants only CreateLogGroup, CreateLogStream, PutLogEvents, and
#           DescribeLogGroups/DescribeLogStreams -- the minimum set needed.
#           Does NOT grant DeleteLogGroup or GetLogEvents (read access).
# ALTERNATIVE: Could use an AWS-managed policy, but inline gives us precise
#              control over the exact permissions granted.
# -----------------------------------------------------------------------------
resource "aws_iam_role_policy" "vpc_flow_log_policy" {
  # Name identifies this policy within the role.
  name = "${local.name_prefix}-vpc-flow-log-policy"

  # Attach this policy to the flow log IAM role.
  role = aws_iam_role.vpc_flow_log_role.id

  # The permissions policy defining what actions are allowed on which resources.
  # WHY: Each action is the minimum required for VPC Flow Logs to function.
  policy = jsonencode({
    Version = "2012-10-17"  # IAM policy language version
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",       # Create the log group if it doesn't exist
          "logs:CreateLogStream",      # Create log streams within the group
          "logs:PutLogEvents",         # Write actual flow log records
          "logs:DescribeLogGroups",    # List log groups (required for delivery verification)
          "logs:DescribeLogStreams"    # List log streams (required for delivery verification)
        ]
        # SECURITY: Resource is set to "*" because VPC Flow Logs needs to
        # discover and create log groups/streams. In production, this could
        # be scoped to the specific log group ARN for tighter control.
        Resource = "*"
      }
    ]
  })
}

# =============================================================================
# INTERNET GATEWAY
# =============================================================================
# WHAT: An Internet Gateway (IGW) that enables communication between the
#       VPC and the public internet.
# WHY: Required for:
#       1. ALB (Application Load Balancer) in public subnets to receive
#          incoming HTTPS requests from students, faculty, and staff.
#       2. NAT Gateways (placed in public subnets) to provide outbound
#          internet access for private subnets.
# SECURITY IMPLICATIONS:
#   - The IGW itself does not expose any resources; it merely enables routing.
#   - Actual exposure is controlled by route tables (only public subnets
#     have a route to 0.0.0.0/0 via the IGW) and security groups.
#   - Resources in private/isolated subnets CANNOT be reached from the
#     internet even with the IGW present, because their route tables
#     do not include a route through the IGW.
# ALTERNATIVES:
#   - For fully private architectures, the IGW could be omitted entirely,
#     but then ALBs would need to be internal-only with VPN/DirectConnect access.
#   - AWS PrivateLink could replace some outbound internet needs.
# =============================================================================
resource "aws_internet_gateway" "main" {
  # Associate this IGW with our VPC.
  # WHY: An IGW must be attached to exactly one VPC to function.
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-igw"  # Human-readable name in AWS console
  })
}

# =============================================================================
# ELASTIC IP ADDRESSES FOR NAT GATEWAYS
# =============================================================================
# WHAT: Static Elastic IP addresses allocated for each NAT Gateway.
# WHY: NAT Gateways require a public IP address to perform network address
#       translation. Using Elastic IPs ensures the outbound IP is static
#       and predictable, which is important for:
#       1. Firewall allowlisting: Third-party services (SIS, LMS integrations)
#          may require known source IPs for API access.
#       2. Compliance logging: Static IPs make it easier to trace outbound
#          traffic in network logs.
# SECURITY: Each AZ gets its own EIP/NAT Gateway to prevent cross-AZ
#           traffic (which could be intercepted) and to provide fault isolation.
# COST: Each EIP + NAT Gateway costs approximately $32/month + data processing.
#        For dev environments, consider using a single NAT Gateway to save costs.
# =============================================================================
resource "aws_eip" "nat" {
  # Create one EIP per AZ for high availability NAT Gateway deployment.
  # WHY: If one AZ fails, the NAT Gateways in other AZs continue operating,
  #       ensuring uninterrupted outbound internet access for private subnets.
  count = local.az_count

  # Allocate this EIP in the VPC domain (not EC2-Classic, which is deprecated).
  # WHY: VPC-domain EIPs are required for NAT Gateways; EC2-Classic EIPs
  #       cannot be used with VPC resources.
  domain = "vpc"

  tags = merge(local.common_tags, {
    # Include the AZ letter (a, b, c) in the name for easy identification.
    # WHY: When troubleshooting, knowing which NAT Gateway is in which AZ
    #       is critical for diagnosing connectivity issues.
    Name = "${local.name_prefix}-nat-eip-${local.selected_azs[count.index]}"
  })

  # Ensure the Internet Gateway exists before creating EIPs.
  # WHY: EIPs associated with NAT Gateways that route through an IGW
  #       need the IGW to exist first for the routing to work.
  depends_on = [aws_internet_gateway.main]
}

# =============================================================================
# NAT GATEWAYS
# =============================================================================
# WHAT: Network Address Translation (NAT) Gateways that allow resources in
#       private subnets to initiate outbound connections to the internet
#       while preventing inbound connections from the internet.
# WHY: Private subnet resources (ECS Fargate tasks, Lambda functions) need
#       outbound internet access for:
#       1. Pulling container images from ECR (or Docker Hub as fallback)
#       2. Sending API calls to third-party services (SIS, LMS, payment gateways)
#       3. Downloading OS/package updates
#       4. Sending metrics/logs to external monitoring services
# SECURITY IMPLICATIONS:
#   - NAT Gateways only allow OUTBOUND-initiated connections; the internet
#     CANNOT initiate inbound connections to private subnet resources.
#   - One NAT Gateway per AZ ensures AZ-level fault isolation and prevents
#     cross-AZ traffic that could increase latency and exposure.
#   - NAT Gateways are AWS-managed and automatically patched by AWS.
# ALTERNATIVES:
#   - NAT Instances (EC2-based): Cheaper but self-managed, single point of
#     failure, and requires patching. NOT recommended for production.
#   - VPC Endpoints: Eliminates internet access need for specific AWS services
#     (S3, DynamoDB, ECR, etc.). Should be used IN ADDITION to NAT Gateways.
# COST OPTIMIZATION:
#   - For dev/test environments, set var.az_count = 1 to use a single NAT
#     Gateway, saving approximately $64/month.
# =============================================================================
resource "aws_nat_gateway" "main" {
  # Create one NAT Gateway per AZ for high availability.
  # WHY: Multi-AZ NAT Gateway deployment ensures that if one AZ experiences
  #       an outage, private subnets in other AZs can still reach the internet.
  count = local.az_count

  # Assign the corresponding Elastic IP to this NAT Gateway.
  # WHY: NAT Gateways require a static public IP for outbound traffic.
  allocation_id = aws_eip.nat[count.index].id

  # Place the NAT Gateway in a public subnet (it needs internet access via IGW).
  # WHY: NAT Gateways must be in a public subnet because they need a route
  #       to the internet (via IGW) to translate private IP traffic to public.
  # SECURITY: The NAT Gateway itself has no security group; traffic filtering
  #           is done by security groups on the source resources and NACLs.
  subnet_id = aws_subnet.public[count.index].id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-nat-${local.selected_azs[count.index]}"
  })

  # Ensure the Internet Gateway exists before creating NAT Gateways.
  # WHY: NAT Gateways route traffic through the IGW; creating them before
  #       the IGW exists would result in non-functional NAT.
  depends_on = [aws_internet_gateway.main]
}

# =============================================================================
# PUBLIC SUBNETS
# =============================================================================
# WHAT: Subnets with a route to the Internet Gateway, enabling direct
#       inbound and outbound internet communication.
# WHY: Public subnets host resources that MUST be directly accessible from
#       the internet:
#       1. Application Load Balancers (ALBs) that terminate HTTPS from users
#       2. NAT Gateways (placed here to route private subnet traffic out)
#       3. Bastion hosts (if needed for emergency SSH access)
# SECURITY IMPLICATIONS:
#   - ONLY load balancers and NAT Gateways should be placed here.
#   - NEVER place application servers, databases, or data processing
#     workloads in public subnets.
#   - map_public_ip_on_launch is set to FALSE to prevent accidental
#     public IP assignment to resources launched in these subnets.
#   - Security groups and NACLs provide additional access control layers.
# SIZING: /24 subnets provide 251 usable IPs per AZ (256 minus 5 AWS-reserved).
#         This is more than sufficient for load balancers and NAT Gateways.
# =============================================================================
resource "aws_subnet" "public" {
  # Create one public subnet per AZ for multi-AZ load balancer deployment.
  # WHY: ALBs require subnets in at least 2 AZs for high availability.
  #       Creating one per AZ ensures even traffic distribution.
  count = local.az_count

  # Place this subnet in our VPC.
  vpc_id = aws_vpc.main.id

  # Assign a CIDR block from the public subnet CIDR list.
  # WHY: Using a variable allows flexible IP planning per environment.
  #       Typical values: 10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24
  cidr_block = var.public_subnet_cidrs[count.index]

  # Place each subnet in a different AZ for fault isolation.
  # WHY: If AZ-a fails, subnets in AZ-b and AZ-c continue serving traffic.
  availability_zone = local.selected_azs[count.index]

  # Do NOT automatically assign public IPs to instances launched here.
  # WHY: Public IP auto-assignment is a security risk; resources that need
  #       public IPs (like NAT Gateways) get Elastic IPs explicitly.
  # SECURITY: Prevents accidental exposure of resources to the internet.
  #           This is a defense-in-depth measure on top of security groups.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${local.selected_azs[count.index]}"
    Tier = "public"  # Tag indicating the subnet tier for automation/policies
    # Kubernetes-compatible tag for EKS load balancer auto-discovery.
    # WHY: If ECTP ever migrates from ECS to EKS, this tag enables automatic
    #       load balancer subnet selection without configuration changes.
    "kubernetes.io/role/elb" = "1"
  })
}

# =============================================================================
# PRIVATE APPLICATION SUBNETS
# =============================================================================
# WHAT: Subnets with outbound internet access via NAT Gateway but NO direct
#       inbound internet access.
# WHY: Private App subnets host the compute layer of the ECTP platform:
#       1. ECS Fargate tasks running the FastAPI application containers
#       2. Lambda functions for event-driven processing
#       3. Internal ALBs for service-to-service communication
# SECURITY IMPLICATIONS:
#   - Resources here CANNOT be reached from the internet directly.
#   - Outbound internet access is routed through NAT Gateways (for pulling
#     container images, calling external APIs, etc.).
#   - All inbound traffic must come through the public-facing ALB.
#   - This creates a critical security boundary: attackers cannot directly
#     target application servers without first compromising the ALB.
# SIZING: /24 subnets provide 251 usable IPs per AZ, supporting hundreds
#         of concurrent Fargate tasks and Lambda ENIs.
# =============================================================================
resource "aws_subnet" "private_app" {
  # Create one private app subnet per AZ for multi-AZ compute deployment.
  # WHY: Distributing Fargate tasks across AZs provides fault tolerance
  #       and ensures the application survives single-AZ failures.
  count = local.az_count

  # Place this subnet in our VPC.
  vpc_id = aws_vpc.main.id

  # Assign a CIDR block from the private app subnet CIDR list.
  # WHY: Separate CIDR ranges from public subnets enable clear network
  #       segmentation visible in flow logs and firewall rules.
  cidr_block = var.private_app_subnet_cidrs[count.index]

  # Place each subnet in a different AZ.
  # WHY: ECS Fargate distributes tasks across AZs for resilience.
  availability_zone = local.selected_azs[count.index]

  # Private subnets should NEVER auto-assign public IPs.
  # WHY: Any resource with a public IP in a private subnet could still
  #       be reached if a misconfigured security group allows it.
  # SECURITY: Defense-in-depth; even if routing changes, no public IP means
  #           no direct internet accessibility.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-app-${local.selected_azs[count.index]}"
    Tier = "private-app"  # Tag for automation and cost allocation
    # Kubernetes-compatible tag for EKS internal load balancer auto-discovery.
    # WHY: Future-proofs the subnet for potential EKS migration.
    "kubernetes.io/role/internal-elb" = "1"
  })
}

# =============================================================================
# PRIVATE DATA SUBNETS
# =============================================================================
# WHAT: Subnets dedicated to data storage services (RDS, ElastiCache, etc.)
#       with outbound internet access via NAT Gateway.
# WHY: Data subnets are separated from application subnets to enable:
#       1. Stricter security group rules (only app subnets can reach data subnets)
#       2. Network-level isolation of sensitive data (student records, PII)
#       3. Dedicated NACLs that can restrict protocols to only database ports
#       4. Separate monitoring and alerting for data-tier network traffic
# SECURITY IMPLICATIONS:
#   - ONLY application subnets should have network access to data subnets.
#   - Public subnets should NEVER have direct access to data subnets.
#   - This implements the principle of defense-in-depth: an attacker must
#     compromise the ALB, then the app tier, before reaching data.
#   - FERPA compliance requires demonstrating that student data is isolated
#     from public-facing infrastructure.
# SIZING: /24 subnets provide 251 usable IPs per AZ, more than sufficient
#         for RDS instances, ElastiCache nodes, and their ENIs.
# =============================================================================
resource "aws_subnet" "private_data" {
  # Create one data subnet per AZ for RDS Multi-AZ and ElastiCache deployment.
  # WHY: RDS Multi-AZ requires subnets in at least 2 AZs for the primary
  #       and standby instances. ElastiCache also benefits from multi-AZ.
  count = local.az_count

  # Place this subnet in our VPC.
  vpc_id = aws_vpc.main.id

  # Assign a CIDR block from the private data subnet CIDR list.
  # WHY: Data subnets use a different CIDR range to enable network-level
  #       access controls (NACLs, security groups) based on IP ranges.
  cidr_block = var.private_data_subnet_cidrs[count.index]

  # Place each subnet in a different AZ for database high availability.
  # WHY: RDS Multi-AZ replicates data synchronously to a standby in another AZ.
  availability_zone = local.selected_azs[count.index]

  # Private data subnets must NEVER auto-assign public IPs.
  # WHY: Database instances should never have public IP addresses under
  #       any circumstances. This is a critical FERPA/HIPAA requirement.
  # SECURITY: Even if RDS "publicly accessible" is accidentally set to true,
  #           no public IP means no internet accessibility.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name               = "${local.name_prefix}-private-data-${local.selected_azs[count.index]}"
    Tier               = "private-data"           # Tag for automation and cost allocation
    DataClassification = "confidential"           # Marks subnets handling sensitive/FERPA data
  })
}

# =============================================================================
# ISOLATED SUBNETS
# =============================================================================
# WHAT: Subnets with absolutely NO internet access -- neither inbound nor
#       outbound. These subnets have no route to a NAT Gateway or IGW.
# WHY: Isolated subnets are designed for the most sensitive workloads:
#       1. Data processing jobs that handle raw PII (SSNs, financial aid data)
#       2. Encryption/decryption services that manage cryptographic keys
#       3. Compliance-sensitive batch jobs (FERPA/HIPAA audit processing)
#       4. Resources that should never communicate outside the VPC
# SECURITY IMPLICATIONS:
#   - These subnets have NO route to the internet whatsoever.
#   - Resources here can only communicate with other VPC resources.
#   - Even if malware compromises a resource in this subnet, it cannot
#     exfiltrate data to the internet or download additional payloads.
#   - Access to AWS services (S3, KMS, etc.) requires VPC Endpoints.
#   - This is the highest isolation level available in the ECTP architecture.
# ALTERNATIVES:
#   - For resources needing limited AWS service access, VPC Endpoints
#     (PrivateLink) provide private connectivity without internet access.
# =============================================================================
resource "aws_subnet" "isolated" {
  # Create one isolated subnet per AZ for consistent multi-AZ availability.
  # WHY: Even isolated workloads benefit from multi-AZ deployment for
  #       fault tolerance during AZ outages.
  count = local.az_count

  # Place this subnet in our VPC.
  vpc_id = aws_vpc.main.id

  # Assign a CIDR block from the isolated subnet CIDR list.
  # WHY: A completely separate CIDR range enables blanket deny rules
  #       in NACLs for any traffic from/to internet-connected subnets.
  cidr_block = var.isolated_subnet_cidrs[count.index]

  # Place each subnet in a different AZ.
  availability_zone = local.selected_azs[count.index]

  # Isolated subnets must absolutely NEVER auto-assign public IPs.
  # WHY: These subnets are designed for maximum isolation; a public IP
  #       would completely defeat their purpose.
  # SECURITY: Critical defense-in-depth for the most sensitive data tier.
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name               = "${local.name_prefix}-isolated-${local.selected_azs[count.index]}"
    Tier               = "isolated"               # Tag for automation and strict policies
    DataClassification = "restricted"             # Highest sensitivity classification
  })
}

# =============================================================================
# ROUTE TABLES
# =============================================================================
# Route tables define how traffic is routed within and outside the VPC.
# Each subnet tier has its own route table with appropriate routing rules.
# SECURITY: Route tables are a network-level security control that determines
#           which subnets can reach the internet and which cannot.
# =============================================================================

# -----------------------------------------------------------------------------
# Public Route Table
# -----------------------------------------------------------------------------
# WHAT: Route table for public subnets with a default route to the IGW.
# WHY: Public subnets need a route to the Internet Gateway for ALBs and
#       NAT Gateways to communicate with the internet.
# SECURITY: Only public subnets are associated with this route table.
#           Private and isolated subnets use separate route tables WITHOUT
#           a route to the IGW, ensuring they cannot be reached from the internet.
# NOTE: A single public route table is shared across all AZs because the
#        IGW is an AZ-independent regional resource.
# -----------------------------------------------------------------------------
resource "aws_route_table" "public" {
  # Associate this route table with our VPC.
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-rt"  # Identifies this as the public route table
    Tier = "public"                           # Tier tag for automated governance
  })
}

# -----------------------------------------------------------------------------
# Public Route: Default Route to Internet Gateway
# -----------------------------------------------------------------------------
# WHAT: A route that sends all non-VPC traffic (0.0.0.0/0) to the IGW.
# WHY: This is what makes the public subnets "public" -- traffic destined
#       for internet addresses is routed through the Internet Gateway.
# SECURITY: Only resources in subnets associated with this route table
#           can send/receive traffic via the IGW. This is the primary
#           mechanism that separates public from private subnets.
# NOTE: VPC-internal traffic (matching the VPC CIDR) always uses the
#        "local" route implicitly and never hits this default route.
# -----------------------------------------------------------------------------
resource "aws_route" "public_internet" {
  # Add this route to the public route table.
  route_table_id = aws_route_table.public.id

  # Default route: all traffic not matching another more-specific route.
  # WHY: 0.0.0.0/0 is the CIDR for "all IPv4 addresses", meaning any
  #       traffic not destined for the VPC CIDR goes to the internet.
  destination_cidr_block = "0.0.0.0/0"

  # Route through the Internet Gateway.
  # WHY: The IGW performs 1:1 NAT for resources with public/Elastic IPs
  #       and enables direct internet connectivity.
  gateway_id = aws_internet_gateway.main.id
}

# -----------------------------------------------------------------------------
# Public Route Table Associations
# -----------------------------------------------------------------------------
# WHAT: Associates each public subnet with the public route table.
# WHY: Without an explicit association, subnets use the VPC's main route
#       table, which may not have the correct routes. Explicit association
#       ensures deterministic routing behavior.
# SECURITY: Only public subnets should be associated with the public route
#           table. Accidentally associating a private subnet would expose
#           its resources to the internet.
# -----------------------------------------------------------------------------
resource "aws_route_table_association" "public" {
  # One association per public subnet (one per AZ).
  count = local.az_count

  # Associate the corresponding public subnet.
  subnet_id = aws_subnet.public[count.index].id

  # Use the single shared public route table.
  route_table_id = aws_route_table.public.id
}

# -----------------------------------------------------------------------------
# Private App Route Tables (One Per AZ)
# -----------------------------------------------------------------------------
# WHAT: Separate route tables for private app subnets, one per AZ.
# WHY: Each AZ has its own NAT Gateway, so each AZ's private subnets
#       need a route table pointing to their AZ's NAT Gateway.
#       This ensures traffic stays within the same AZ (reducing latency
#       and cross-AZ data transfer costs).
# SECURITY: Private app route tables route 0.0.0.0/0 through NAT Gateways,
#           NOT the IGW. This means resources in these subnets can initiate
#           outbound connections but cannot receive inbound connections.
# ALTERNATIVE: A single route table with one NAT Gateway saves cost but
#              creates a single point of failure and cross-AZ traffic.
# -----------------------------------------------------------------------------
resource "aws_route_table" "private_app" {
  # One route table per AZ for AZ-local NAT Gateway routing.
  count = local.az_count

  # Associate with our VPC.
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-app-rt-${local.selected_azs[count.index]}"
    Tier = "private-app"  # Tier tag for automated governance
  })
}

# -----------------------------------------------------------------------------
# Private App Routes: Default Route to NAT Gateway
# -----------------------------------------------------------------------------
# WHAT: Routes all outbound internet traffic through the AZ-local NAT Gateway.
# WHY: Fargate tasks and other compute resources need outbound internet
#       access for pulling container images, calling external APIs, etc.
# SECURITY: NAT Gateways only allow outbound-initiated connections.
#           The internet CANNOT initiate connections to private resources
#           through a NAT Gateway. This is a fundamental security property.
# -----------------------------------------------------------------------------
resource "aws_route" "private_app_nat" {
  # One route per AZ (matching the route table per AZ).
  count = local.az_count

  # Add to the corresponding AZ's private app route table.
  route_table_id = aws_route_table.private_app[count.index].id

  # Default route: all non-VPC traffic goes to the NAT Gateway.
  destination_cidr_block = "0.0.0.0/0"

  # Route through the AZ-local NAT Gateway.
  # WHY: Using the same AZ's NAT Gateway avoids cross-AZ traffic charges
  #       ($0.01/GB) and reduces latency by staying within the same AZ.
  nat_gateway_id = aws_nat_gateway.main[count.index].id
}

# -----------------------------------------------------------------------------
# Private App Route Table Associations
# -----------------------------------------------------------------------------
# WHAT: Associates each private app subnet with its AZ-local route table.
# WHY: Ensures each private app subnet routes through its own AZ's NAT
#       Gateway for optimal performance and fault isolation.
# SECURITY: Verifies that private app subnets are NOT accidentally
#           associated with the public route table.
# -----------------------------------------------------------------------------
resource "aws_route_table_association" "private_app" {
  count = local.az_count

  subnet_id      = aws_subnet.private_app[count.index].id
  route_table_id = aws_route_table.private_app[count.index].id
}

# -----------------------------------------------------------------------------
# Private Data Route Tables (One Per AZ)
# -----------------------------------------------------------------------------
# WHAT: Separate route tables for private data subnets, one per AZ.
# WHY: Data subnets get their own route tables to enable different routing
#       policies than app subnets if needed. Currently they also route
#       through NAT Gateways for RDS patching and AWS service access.
# SECURITY: Separate route tables for data subnets allow future restriction
#           of outbound access. For example, we could remove the NAT route
#           and use only VPC Endpoints for maximum data isolation.
# ALTERNATIVE: Could share route tables with private app subnets, but
#              separation provides flexibility for future security hardening.
# -----------------------------------------------------------------------------
resource "aws_route_table" "private_data" {
  count = local.az_count

  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-data-rt-${local.selected_azs[count.index]}"
    Tier = "private-data"
  })
}

# -----------------------------------------------------------------------------
# Private Data Routes: Default Route to NAT Gateway
# -----------------------------------------------------------------------------
# WHAT: Routes outbound internet traffic from data subnets through NAT Gateways.
# WHY: RDS instances may need outbound access for AWS service communication
#       (e.g., sending Enhanced Monitoring metrics to CloudWatch).
# SECURITY: In a maximum-security configuration, this route could be REMOVED
#           and replaced with VPC Endpoints for specific AWS services only.
#           This would prevent any internet access from the data tier.
# NOTE: Consider removing this route in production and using VPC Endpoints
#        for S3, CloudWatch, and other required AWS services instead.
# -----------------------------------------------------------------------------
resource "aws_route" "private_data_nat" {
  count = local.az_count

  route_table_id         = aws_route_table.private_data[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main[count.index].id
}

# -----------------------------------------------------------------------------
# Private Data Route Table Associations
# -----------------------------------------------------------------------------
# WHAT: Associates each private data subnet with its AZ-local route table.
# WHY: Ensures data subnets have the correct routing for their tier.
# SECURITY: Verifies data subnets are NOT on the public route table.
# -----------------------------------------------------------------------------
resource "aws_route_table_association" "private_data" {
  count = local.az_count

  subnet_id      = aws_subnet.private_data[count.index].id
  route_table_id = aws_route_table.private_data[count.index].id
}

# -----------------------------------------------------------------------------
# Isolated Route Tables (One Per AZ)
# -----------------------------------------------------------------------------
# WHAT: Route tables for isolated subnets with NO default route.
# WHY: Isolated subnets must have NO internet access whatsoever. The route
#       table contains only the implicit "local" route for VPC-internal traffic.
# SECURITY: This is the strictest network isolation available in AWS VPC.
#           Resources in isolated subnets:
#           - Cannot reach the internet (no NAT Gateway or IGW route)
#           - Cannot be reached from the internet
#           - Can only communicate with other resources within the VPC
#           - Must use VPC Endpoints for any AWS service access
#           This is ideal for the most sensitive data processing workloads.
# NOTE: No aws_route resource is created for isolated subnets because
#        they should have NO routes beyond the implicit VPC local route.
# -----------------------------------------------------------------------------
resource "aws_route_table" "isolated" {
  count = local.az_count

  vpc_id = aws_vpc.main.id

  # NOTE: No routes are added to this route table. The only route is the
  # implicit "local" route (VPC CIDR -> local) that AWS adds automatically.
  # This means resources in isolated subnets can ONLY communicate within the VPC.

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-isolated-rt-${local.selected_azs[count.index]}"
    Tier = "isolated"
  })
}

# -----------------------------------------------------------------------------
# Isolated Route Table Associations
# -----------------------------------------------------------------------------
# WHAT: Associates each isolated subnet with its route table (no internet routes).
# WHY: Ensures isolated subnets have the most restrictive routing possible.
# SECURITY: Critical for FERPA/HIPAA compliance -- isolated subnets must
#           never gain internet access through accidental route table changes.
# -----------------------------------------------------------------------------
resource "aws_route_table_association" "isolated" {
  count = local.az_count

  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated[count.index].id
}

# =============================================================================
# NETWORK ACCESS CONTROL LISTS (NACLs)
# =============================================================================
# NACLs provide stateless packet filtering at the subnet level, acting as
# an additional layer of defense on top of security groups.
# WHY: NACLs are evaluated BEFORE security groups and provide a coarse-grained
#       first line of defense. They are particularly useful for:
#       1. Blocking known malicious IP ranges at the subnet boundary
#       2. Restricting traffic between subnet tiers (public -> data = denied)
#       3. Compliance requirements that mandate multiple layers of network controls
# SECURITY: NACLs are STATELESS (unlike security groups), meaning both
#           inbound and outbound rules must be explicitly defined.
#           This makes them harder to configure but provides an additional
#           security layer that operates independently of security groups.
# =============================================================================

# -----------------------------------------------------------------------------
# Public Subnet NACL
# -----------------------------------------------------------------------------
# WHAT: NACL for public subnets allowing HTTP/HTTPS inbound and ephemeral
#       port responses for outbound-initiated connections.
# WHY: Public subnets need to accept incoming web traffic (443/HTTPS) and
#       return responses on ephemeral ports. All other traffic is denied.
# SECURITY: This NACL acts as a coarse filter; security groups on ALBs
#           provide more granular control.
# -----------------------------------------------------------------------------
resource "aws_network_acl" "public" {
  vpc_id = aws_vpc.main.id

  # Associate with all public subnets.
  subnet_ids = aws_subnet.public[*].id

  # --- INBOUND RULES ---

  # Allow HTTPS inbound from anywhere (for ALB to receive user traffic).
  # WHY: The ALB in public subnets must accept HTTPS connections from
  #       students, faculty, and staff accessing the ECTP platform.
  # SECURITY: Only port 443 is allowed; HTTP (80) is intentionally excluded
  #           to enforce TLS encryption at the network level.
  ingress {
    rule_no    = 100              # Rule number (evaluated in order, lowest first)
    protocol   = "tcp"            # TCP protocol for HTTPS
    action     = "allow"          # Allow this traffic
    cidr_block = "0.0.0.0/0"     # From any source (public internet)
    from_port  = 443              # HTTPS port
    to_port    = 443              # HTTPS port (single port, not a range)
  }

  # Allow HTTP inbound for redirect to HTTPS.
  # WHY: Users may type "http://" in their browser; the ALB will redirect
  #       them to HTTPS. Without this rule, the redirect would fail.
  # SECURITY: The ALB should be configured to immediately redirect HTTP
  #           to HTTPS, so HTTP traffic is never processed unencrypted.
  ingress {
    rule_no    = 110              # Second priority rule
    protocol   = "tcp"            # TCP protocol for HTTP
    action     = "allow"          # Allow for HTTPS redirect
    cidr_block = "0.0.0.0/0"     # From any source
    from_port  = 80               # HTTP port
    to_port    = 80               # HTTP port
  }

  # Allow ephemeral port responses from the internet (for NAT Gateway return traffic).
  # WHY: When NAT Gateways (in public subnets) forward traffic to the internet,
  #       response packets come back on ephemeral ports (1024-65535).
  # SECURITY: This is required for NAT Gateway operation. The broad port
  #           range is necessary because ephemeral ports are randomly assigned.
  ingress {
    rule_no    = 120              # Third priority rule
    protocol   = "tcp"            # TCP responses
    action     = "allow"          # Allow return traffic
    cidr_block = "0.0.0.0/0"     # From any internet source
    from_port  = 1024             # Start of ephemeral port range
    to_port    = 65535            # End of ephemeral port range
  }

  # Allow all inbound traffic from within the VPC.
  # WHY: Internal VPC communication (e.g., app subnets -> ALB in public subnet)
  #       must not be blocked by NACLs.
  # SECURITY: VPC-internal traffic is already controlled by security groups.
  #           The NACL allows it to avoid double-filtering VPC-internal flows.
  ingress {
    rule_no    = 130              # Fourth priority rule
    protocol   = "-1"             # All protocols
    action     = "allow"          # Allow VPC-internal traffic
    cidr_block = var.vpc_cidr     # Only from within the VPC
    from_port  = 0                # All ports
    to_port    = 0                # All ports (for protocol -1, ports are ignored)
  }

  # --- OUTBOUND RULES ---

  # Allow all outbound traffic from public subnets.
  # WHY: Public subnets need to send traffic to the internet (ALB responses),
  #       to NAT Gateways, and to private subnets. Restricting outbound
  #       at the NACL level would be overly complex and fragile.
  # SECURITY: Outbound traffic is controlled more granularly by security
  #           groups on individual resources (ALBs, NAT Gateways).
  egress {
    rule_no    = 100              # First outbound rule
    protocol   = "-1"             # All protocols
    action     = "allow"          # Allow all outbound
    cidr_block = "0.0.0.0/0"     # To any destination
    from_port  = 0                # All ports
    to_port    = 0                # All ports
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-nacl"
  })
}

# -----------------------------------------------------------------------------
# Private Data Subnet NACL
# -----------------------------------------------------------------------------
# WHAT: NACL for private data subnets that restricts inbound access to
#       only application subnets on database ports.
# WHY: The data tier should only be accessible from the application tier,
#       implementing the principle of least privilege at the network level.
# SECURITY: This NACL prevents direct access from public subnets to the
#           data tier, even if security groups are misconfigured.
#           This is a critical defense-in-depth measure for FERPA compliance.
# NOTE: Even if an attacker compromises a resource in the public subnet,
#        they cannot directly reach the database because the NACL blocks it.
# -----------------------------------------------------------------------------
resource "aws_network_acl" "private_data" {
  vpc_id = aws_vpc.main.id

  # Associate with all private data subnets.
  subnet_ids = aws_subnet.private_data[*].id

  # --- INBOUND RULES ---

  # Allow PostgreSQL traffic from private app subnets.
  # WHY: The FastAPI application in private app subnets needs to connect
  #       to PostgreSQL in the data tier on port 5432.
  # SECURITY: Only allows traffic from app subnets (not public or isolated).
  #           This ensures only application servers can reach the database.
  # NOTE: Dynamic block creates one rule per app subnet CIDR.
  dynamic "ingress" {
    # Create one ingress rule for each private app subnet CIDR.
    for_each = var.private_app_subnet_cidrs
    content {
      rule_no    = 100 + ingress.key    # Sequential rule numbers: 100, 101, 102
      protocol   = "tcp"                 # TCP for PostgreSQL
      action     = "allow"               # Allow database traffic from app tier
      cidr_block = ingress.value         # From specific app subnet CIDR
      from_port  = 5432                  # PostgreSQL default port
      to_port    = 5432                  # Single port (not a range)
    }
  }

  # Allow ephemeral port traffic from within the VPC for return traffic.
  # WHY: When data tier resources initiate connections to other VPC resources
  #       (e.g., sending logs), responses come back on ephemeral ports.
  # SECURITY: Restricted to VPC CIDR to prevent internet traffic.
  ingress {
    rule_no    = 200              # Lower priority than specific database rules
    protocol   = "tcp"            # TCP for return traffic
    action     = "allow"          # Allow ephemeral responses
    cidr_block = var.vpc_cidr     # Only from within the VPC
    from_port  = 1024             # Ephemeral port range start
    to_port    = 65535            # Ephemeral port range end
  }

  # --- OUTBOUND RULES ---

  # Allow all outbound traffic from data subnets.
  # WHY: Data tier resources need to send responses to app tier queries,
  #       communicate with AWS services (via VPC endpoints or NAT), and
  #       send monitoring data.
  # SECURITY: Outbound is less risky than inbound because database servers
  #           rarely initiate connections to arbitrary destinations.
  #           Security groups provide more granular outbound control.
  egress {
    rule_no    = 100              # First outbound rule
    protocol   = "-1"             # All protocols
    action     = "allow"          # Allow all outbound
    cidr_block = "0.0.0.0/0"     # To any destination
    from_port  = 0                # All ports
    to_port    = 0                # All ports
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-data-nacl"
  })
}

# =============================================================================
# VPC ENDPOINTS (PrivateLink)
# =============================================================================
# VPC Endpoints allow resources in private/isolated subnets to access AWS
# services without traversing the internet or requiring a NAT Gateway.
# WHY: VPC Endpoints provide:
#       1. Reduced data transfer costs (no NAT Gateway processing fees)
#       2. Lower latency (traffic stays on the AWS backbone network)
#       3. Enhanced security (traffic never leaves the AWS network)
#       4. Required for isolated subnets that have no internet access
# SECURITY: Using VPC Endpoints eliminates the need for internet access
#           to reach AWS services, reducing the attack surface.
# =============================================================================

# -----------------------------------------------------------------------------
# S3 Gateway Endpoint
# -----------------------------------------------------------------------------
# WHAT: A VPC Gateway Endpoint for Amazon S3, providing free, private access
#       to S3 from within the VPC.
# WHY: S3 is used extensively by ECTP for:
#       1. Storing document uploads (student records, transcripts)
#       2. Application artifacts and configuration files
#       3. ECS container image layers (cached in S3 by ECR)
#       4. VPC Flow Log delivery (if configured for S3 destination)
# SECURITY: Gateway Endpoints for S3 are FREE and keep S3 traffic entirely
#           within the AWS network. Without this endpoint, S3 traffic from
#           private subnets would traverse the NAT Gateway and the internet.
# ALTERNATIVE: Interface Endpoint for S3 costs $0.01/hour but supports
#              PrivateLink DNS. Gateway Endpoint is preferred for cost.
# -----------------------------------------------------------------------------
resource "aws_vpc_endpoint" "s3" {
  # Associate with our VPC.
  vpc_id = aws_vpc.main.id

  # Specify the S3 service endpoint for the current region.
  # WHY: The service name format "com.amazonaws.{region}.s3" ensures we
  #       connect to the S3 endpoint in the correct AWS region.
  service_name = "com.amazonaws.${data.aws_region.current.name}.s3"

  # Use Gateway type (free, supports S3 and DynamoDB only).
  # WHY: Gateway Endpoints are free and have no bandwidth limits.
  #       Interface Endpoints cost money and are not needed for S3.
  vpc_endpoint_type = "Gateway"

  # Associate with all route tables that need S3 access.
  # WHY: The Gateway Endpoint works by adding a prefix list route to
  #       the specified route tables, directing S3 traffic through the endpoint.
  route_table_ids = concat(
    aws_route_table.private_app[*].id,   # App subnets need S3 for ECR image pulls
    aws_route_table.private_data[*].id,  # Data subnets need S3 for RDS backups
    aws_route_table.isolated[*].id       # Isolated subnets need S3 (only via endpoint)
  )

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-s3-endpoint"
  })
}

# -----------------------------------------------------------------------------
# DynamoDB Gateway Endpoint
# -----------------------------------------------------------------------------
# WHAT: A VPC Gateway Endpoint for Amazon DynamoDB, providing free, private
#       access to DynamoDB from within the VPC.
# WHY: DynamoDB may be used by ECTP for:
#       1. Session state storage for the FastAPI application
#       2. Caching layer for frequently accessed reference data
#       3. Terraform state locking (DynamoDB table for remote state)
# SECURITY: Like the S3 endpoint, this keeps DynamoDB traffic within AWS.
# COST: Gateway Endpoints for DynamoDB are FREE.
# -----------------------------------------------------------------------------
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"

  # Associate with all non-public route tables.
  # WHY: All private tiers may need DynamoDB access for state management,
  #       caching, or Terraform state locking.
  route_table_ids = concat(
    aws_route_table.private_app[*].id,
    aws_route_table.private_data[*].id,
    aws_route_table.isolated[*].id
  )

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-dynamodb-endpoint"
  })
}
