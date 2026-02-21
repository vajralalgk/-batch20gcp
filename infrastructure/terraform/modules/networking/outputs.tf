###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Networking Module - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# VPC
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}

output "vpc_arn" {
  description = "ARN of the VPC"
  value       = aws_vpc.main.arn
}

# -----------------------------------------------------------------------------
# Subnets
# -----------------------------------------------------------------------------

output "public_subnet_ids" {
  description = "List of public subnet IDs for load balancers"
  value       = aws_subnet.public[*].id
}

output "public_subnet_cidrs" {
  description = "List of public subnet CIDR blocks"
  value       = aws_subnet.public[*].cidr_block
}

output "gpu_inference_subnet_ids" {
  description = "List of GPU inference subnet IDs (EFA-enabled, private)"
  value       = aws_subnet.gpu_inference[*].id
}

output "gpu_inference_subnet_cidrs" {
  description = "List of GPU inference subnet CIDR blocks"
  value       = aws_subnet.gpu_inference[*].cidr_block
}

output "private_app_subnet_ids" {
  description = "List of private application subnet IDs"
  value       = aws_subnet.private_app[*].id
}

output "private_app_subnet_cidrs" {
  description = "List of private application subnet CIDR blocks"
  value       = aws_subnet.private_app[*].cidr_block
}

output "private_data_subnet_ids" {
  description = "List of private data subnet IDs for cache and databases"
  value       = aws_subnet.private_data[*].id
}

output "private_data_subnet_cidrs" {
  description = "List of private data subnet CIDR blocks"
  value       = aws_subnet.private_data[*].cidr_block
}

# -----------------------------------------------------------------------------
# Gateways
# -----------------------------------------------------------------------------

output "internet_gateway_id" {
  description = "ID of the Internet Gateway"
  value       = aws_internet_gateway.main.id
}

output "nat_gateway_ids" {
  description = "List of NAT Gateway IDs (one per AZ)"
  value       = aws_nat_gateway.main[*].id
}

output "nat_gateway_public_ips" {
  description = "List of NAT Gateway public IP addresses"
  value       = aws_eip.nat[*].public_ip
}

# -----------------------------------------------------------------------------
# Route Tables
# -----------------------------------------------------------------------------

output "public_route_table_id" {
  description = "ID of the public route table"
  value       = aws_route_table.public.id
}

output "private_route_table_ids" {
  description = "List of private route table IDs (one per AZ)"
  value       = aws_route_table.private[*].id
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------

output "efa_security_group_id" {
  description = "ID of the EFA security group for GPU inter-node communication"
  value       = aws_security_group.efa.id
}

# -----------------------------------------------------------------------------
# VPC Peering
# -----------------------------------------------------------------------------

output "vpc_peering_connection_ids" {
  description = "Map of VPC peering connection IDs initiated from this region"
  value       = { for k, v in aws_vpc_peering_connection.cross_region : k => v.id }
}

# -----------------------------------------------------------------------------
# Flow Logs
# -----------------------------------------------------------------------------

output "flow_log_group_name" {
  description = "Name of the CloudWatch log group for VPC flow logs"
  value       = aws_cloudwatch_log_group.vpc_flow_logs.name
}

# -----------------------------------------------------------------------------
# Availability Zones
# -----------------------------------------------------------------------------

output "availability_zones" {
  description = "List of availability zones used"
  value       = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}
