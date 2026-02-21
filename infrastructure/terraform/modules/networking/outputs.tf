# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Networking Outputs
# =============================================================================
# Author: Gopi Krishna Vajrala
# Purpose: Exports networking resource identifiers and attributes for use by
#          other modules (compute, database, security, monitoring).
#
# Design Principle:
#   Outputs are the primary interface between Terraform modules. Each output
#   provides a specific piece of information needed by downstream modules,
#   following the principle of minimal exposure -- only export what is needed.
#
# Security Note:
#   - No sensitive values are exported (no secrets, passwords, or private keys).
#   - Only resource IDs, ARNs, and CIDR blocks are exposed.
#   - Downstream modules use these IDs to create security groups, launch
#     resources into specific subnets, and configure routing.
# =============================================================================

# -----------------------------------------------------------------------------
# VPC Outputs
# -----------------------------------------------------------------------------

output "vpc_id" {
  # WHAT: The unique identifier of the VPC created by this module.
  # WHY: Nearly every other module needs the VPC ID to:
  #       1. Create security groups (security module)
  #       2. Launch ECS tasks into the VPC (compute module)
  #       3. Create RDS subnet groups (database module)
  #       4. Associate VPC-level resources (monitoring, endpoints, etc.)
  # SECURITY: The VPC ID is not sensitive, but knowing it allows someone
  #           to reference the VPC in their own resources (if they have
  #           cross-account access). Ensure IAM policies prevent unauthorized
  #           cross-account VPC usage.
  description = "The ID of the VPC. Used by compute, database, security, and monitoring modules to associate their resources with this VPC."
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  # WHAT: The CIDR block of the VPC.
  # WHY: Needed by security group rules that reference the entire VPC
  #       CIDR range (e.g., "allow all traffic from within the VPC").
  #       Also used for NACLs and route table configurations in other modules.
  # SECURITY: Knowing the VPC CIDR reveals the IP address space used by
  #           the infrastructure, which could aid targeted attacks. However,
  #           this is generally low-risk for private IP ranges.
  description = "The CIDR block of the VPC. Used in security group rules and NACLs to reference the entire VPC address space."
  value       = aws_vpc.main.cidr_block
}

output "vpc_arn" {
  # WHAT: The Amazon Resource Name (ARN) of the VPC.
  # WHY: ARNs are used in IAM policies and CloudWatch metrics to uniquely
  #       identify the VPC across the entire AWS ecosystem. Required for
  #       resource-level IAM policies and cross-account references.
  # SECURITY: ARNs include the AWS account ID, which should not be treated
  #           as a secret but also should not be publicly broadcast.
  description = "The ARN of the VPC. Used in IAM policies for resource-level access control and cross-account configurations."
  value       = aws_vpc.main.arn
}

# -----------------------------------------------------------------------------
# Subnet ID Outputs
# -----------------------------------------------------------------------------

output "public_subnet_ids" {
  # WHAT: List of public subnet IDs, one per availability zone.
  # WHY: The compute module needs these to place the Application Load Balancer
  #       in public subnets. ALBs require subnets in at least 2 AZs.
  # SECURITY: Public subnet IDs should only be used for ALBs and NAT Gateways.
  #           Application workloads should NEVER reference these subnet IDs.
  description = "List of public subnet IDs (one per AZ). Use ONLY for ALBs and NAT Gateways. Never place application workloads in public subnets."
  value       = aws_subnet.public[*].id
}

output "private_app_subnet_ids" {
  # WHAT: List of private application subnet IDs, one per availability zone.
  # WHY: The compute module needs these to launch ECS Fargate tasks in
  #       private app subnets where they have NAT-based internet access
  #       but are not directly reachable from the internet.
  # SECURITY: These subnets are for compute workloads only. They have
  #           outbound internet access via NAT but no inbound access.
  description = "List of private application subnet IDs (one per AZ). Used by the compute module for ECS Fargate tasks and Lambda functions."
  value       = aws_subnet.private_app[*].id
}

output "private_data_subnet_ids" {
  # WHAT: List of private data subnet IDs, one per availability zone.
  # WHY: The database module needs these to create RDS subnet groups and
  #       launch database instances in the isolated data tier.
  # SECURITY: Data subnet IDs should only be used by the database module.
  #           Access to data subnets is restricted to application subnets
  #           via NACLs and security groups.
  description = "List of private data subnet IDs (one per AZ). Used by the database module for RDS subnet groups and ElastiCache deployments."
  value       = aws_subnet.private_data[*].id
}

output "isolated_subnet_ids" {
  # WHAT: List of isolated subnet IDs, one per availability zone.
  # WHY: Used for highly sensitive workloads that must never communicate
  #       with the internet. Resources here can only reach AWS services
  #       through VPC Endpoints.
  # SECURITY: These subnets have NO internet access. Any resource placed
  #           here can only communicate within the VPC and via VPC Endpoints.
  #           This is the most secure subnet tier in the architecture.
  description = "List of isolated subnet IDs (one per AZ). Used for highly sensitive workloads with no internet access. Resources can only communicate within the VPC and via VPC Endpoints."
  value       = aws_subnet.isolated[*].id
}

# -----------------------------------------------------------------------------
# Subnet CIDR Outputs
# -----------------------------------------------------------------------------

output "public_subnet_cidrs" {
  # WHAT: The CIDR blocks assigned to public subnets.
  # WHY: Security group and NACL rules may need to reference subnet CIDRs
  #       to allow or deny traffic from specific subnet tiers.
  # SECURITY: Exposing CIDR blocks is low-risk but enables precise
  #           network access control rules in downstream modules.
  description = "CIDR blocks of public subnets. Used in security group rules and NACLs for tier-based access control."
  value       = aws_subnet.public[*].cidr_block
}

output "private_app_subnet_cidrs" {
  # WHAT: The CIDR blocks assigned to private application subnets.
  # WHY: The security module needs these to create security group rules
  #       allowing traffic from app subnets to data subnets (e.g., allowing
  #       PostgreSQL traffic from app tier to database tier).
  # SECURITY: These CIDRs are used in database security group ingress rules
  #           to restrict database access to only application servers.
  description = "CIDR blocks of private application subnets. Used in database security group rules to restrict access to the app tier only."
  value       = aws_subnet.private_app[*].cidr_block
}

output "private_data_subnet_cidrs" {
  # WHAT: The CIDR blocks assigned to private data subnets.
  # WHY: May be needed for NACLs or security group rules that reference
  #       the data tier CIDR range for monitoring or audit purposes.
  description = "CIDR blocks of private data subnets. Used for monitoring configurations and network access control auditing."
  value       = aws_subnet.private_data[*].cidr_block
}

output "isolated_subnet_cidrs" {
  # WHAT: The CIDR blocks assigned to isolated subnets.
  # WHY: Security automation may need to verify that isolated subnets
  #       truly have no internet routes, using the CIDR to identify them.
  description = "CIDR blocks of isolated subnets. Used for security automation to verify network isolation policies."
  value       = aws_subnet.isolated[*].cidr_block
}

# -----------------------------------------------------------------------------
# Availability Zone Outputs
# -----------------------------------------------------------------------------

output "availability_zones" {
  # WHAT: The list of availability zones used by this networking module.
  # WHY: Other modules need to know which AZs are in use to distribute
  #       their resources (ECS tasks, RDS instances) across the same AZs.
  #       This ensures resources are placed in AZs that have subnets.
  # SECURITY: AZ names are public information and not sensitive.
  description = "List of availability zones used for subnet deployment. Other modules should distribute resources across these same AZs for consistent multi-AZ deployment."
  value       = local.selected_azs
}

output "az_count" {
  # WHAT: The number of availability zones in use.
  # WHY: Other modules may use this count to determine how many replicas
  #       or instances to create for multi-AZ distribution.
  description = "The number of availability zones in use. Useful for determining replica counts and multi-AZ distribution in other modules."
  value       = local.az_count
}

# -----------------------------------------------------------------------------
# Gateway and NAT Outputs
# -----------------------------------------------------------------------------

output "internet_gateway_id" {
  # WHAT: The ID of the Internet Gateway attached to the VPC.
  # WHY: May be needed by other modules that create additional public route
  #       tables or need to reference the IGW for VPN configurations.
  # SECURITY: The IGW ID alone does not grant internet access; route table
  #           associations are also required.
  description = "The ID of the Internet Gateway. May be needed for additional route table configurations or VPN setups."
  value       = aws_internet_gateway.main.id
}

output "nat_gateway_ids" {
  # WHAT: List of NAT Gateway IDs, one per availability zone.
  # WHY: May be needed by modules that create additional private route tables
  #       or need to monitor NAT Gateway metrics (bytes processed, etc.).
  # SECURITY: NAT Gateway IDs are not sensitive, but monitoring their metrics
  #           can reveal outbound traffic patterns.
  description = "List of NAT Gateway IDs (one per AZ). Useful for monitoring NAT Gateway metrics and creating additional private route tables."
  value       = aws_nat_gateway.main[*].id
}

output "nat_gateway_public_ips" {
  # WHAT: The public (Elastic) IP addresses assigned to NAT Gateways.
  # WHY: Required for:
  #       1. Firewall allowlisting: Third-party services may need to whitelist
  #          these IPs for API access from the ECTP platform.
  #       2. DNS/SPF records: If the platform sends emails, these IPs may
  #          need to be in SPF records.
  #       3. Security monitoring: Unexpected traffic from these IPs could
  #          indicate compromised resources using the NAT Gateway.
  # SECURITY: These are public IPs and should be treated as semi-sensitive.
  #           Sharing them with external parties should follow proper
  #           change management procedures.
  description = "Public Elastic IP addresses of NAT Gateways. Needed for third-party firewall allowlisting and SPF record configuration. Treat as semi-sensitive information."
  value       = aws_eip.nat[*].public_ip
}

# -----------------------------------------------------------------------------
# Route Table Outputs
# -----------------------------------------------------------------------------

output "public_route_table_id" {
  # WHAT: The ID of the public route table.
  # WHY: May be needed to add additional routes (e.g., VPN or Transit Gateway routes)
  #       to the public route table from other modules.
  description = "The ID of the public route table. Used for adding additional routes (VPN, Transit Gateway) from other modules."
  value       = aws_route_table.public.id
}

output "private_app_route_table_ids" {
  # WHAT: List of private app route table IDs, one per AZ.
  # WHY: May be needed to add VPC Endpoint routes or VPN routes to private
  #       app subnets from other modules.
  description = "List of private app route table IDs (one per AZ). Used for adding VPC Endpoint or VPN routes from other modules."
  value       = aws_route_table.private_app[*].id
}

output "private_data_route_table_ids" {
  # WHAT: List of private data route table IDs, one per AZ.
  # WHY: May be needed to add VPC Endpoint routes to data subnets.
  description = "List of private data route table IDs (one per AZ). Used for adding VPC Endpoint routes from other modules."
  value       = aws_route_table.private_data[*].id
}

output "isolated_route_table_ids" {
  # WHAT: List of isolated route table IDs, one per AZ.
  # WHY: Essential for adding VPC Endpoint routes to isolated subnets,
  #       which is the ONLY way isolated subnets can access AWS services.
  # SECURITY: Any route added to isolated route tables should be carefully
  #           reviewed, as these subnets are designed for maximum isolation.
  description = "List of isolated route table IDs (one per AZ). Critical for adding VPC Endpoint routes -- the only way isolated subnets can access AWS services."
  value       = aws_route_table.isolated[*].id
}

# -----------------------------------------------------------------------------
# VPC Flow Log Outputs
# -----------------------------------------------------------------------------

output "vpc_flow_log_group_name" {
  # WHAT: The name of the CloudWatch Log Group storing VPC Flow Logs.
  # WHY: The monitoring module needs this to create metric filters and
  #       alarms on network traffic patterns (e.g., alert on rejected traffic).
  # SECURITY: The log group name enables security tools to subscribe to
  #           flow logs for real-time threat detection.
  description = "The CloudWatch Log Group name for VPC Flow Logs. Used by the monitoring module for metric filters, alarms, and security analysis."
  value       = aws_cloudwatch_log_group.vpc_flow_logs.name
}

output "vpc_flow_log_group_arn" {
  # WHAT: The ARN of the CloudWatch Log Group storing VPC Flow Logs.
  # WHY: ARNs are needed for IAM policies that grant read access to
  #       flow logs for security teams and automated scanning tools.
  # SECURITY: Grant read access to flow logs only to security team roles
  #           and automated security scanning services.
  description = "The ARN of the CloudWatch Log Group for VPC Flow Logs. Used in IAM policies to grant flow log read access to security teams."
  value       = aws_cloudwatch_log_group.vpc_flow_logs.arn
}

# -----------------------------------------------------------------------------
# VPC Endpoint Outputs
# -----------------------------------------------------------------------------

output "s3_endpoint_id" {
  # WHAT: The ID of the S3 VPC Gateway Endpoint.
  # WHY: May be needed for endpoint policy modifications or monitoring.
  # SECURITY: The S3 endpoint can be configured with a policy to restrict
  #           which S3 buckets can be accessed from within the VPC.
  description = "The ID of the S3 VPC Gateway Endpoint. Can be used to add endpoint policies restricting which S3 buckets are accessible from the VPC."
  value       = aws_vpc_endpoint.s3.id
}

output "dynamodb_endpoint_id" {
  # WHAT: The ID of the DynamoDB VPC Gateway Endpoint.
  # WHY: May be needed for endpoint policy modifications or monitoring.
  description = "The ID of the DynamoDB VPC Gateway Endpoint. Can be used to add endpoint policies restricting DynamoDB access from the VPC."
  value       = aws_vpc_endpoint.dynamodb.id
}
