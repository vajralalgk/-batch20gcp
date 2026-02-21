###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Security Module - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# KMS
# -----------------------------------------------------------------------------

output "kms_key_arn" {
  description = "ARN of the platform KMS key for encryption"
  value       = aws_kms_key.platform.arn
}

output "kms_key_id" {
  description = "ID of the platform KMS key"
  value       = aws_kms_key.platform.key_id
}

output "kms_alias_name" {
  description = "Alias name of the platform KMS key"
  value       = aws_kms_alias.platform.name
}

# -----------------------------------------------------------------------------
# IAM Roles
# -----------------------------------------------------------------------------

output "eks_cluster_role_arn" {
  description = "ARN of the EKS cluster control plane IAM role"
  value       = aws_iam_role.eks_cluster.arn
}

output "eks_cluster_role_name" {
  description = "Name of the EKS cluster control plane IAM role"
  value       = aws_iam_role.eks_cluster.name
}

output "eks_node_role_arn" {
  description = "ARN of the EKS worker node IAM role"
  value       = aws_iam_role.eks_node.arn
}

output "eks_node_role_name" {
  description = "Name of the EKS worker node IAM role"
  value       = aws_iam_role.eks_node.name
}

output "ebs_csi_role_arn" {
  description = "ARN of the EBS CSI driver IAM role"
  value       = aws_iam_role.ebs_csi.arn
}

output "monitoring_role_arn" {
  description = "ARN of the monitoring IAM role (Grafana CloudWatch access)"
  value       = aws_iam_role.monitoring.arn
}

output "flow_logs_role_arn" {
  description = "ARN of the VPC flow logs IAM role"
  value       = aws_iam_role.flow_logs.arn
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------

output "eks_cluster_security_group_id" {
  description = "Security group ID for the EKS cluster control plane"
  value       = aws_security_group.eks_cluster.id
}

output "inference_nodes_security_group_id" {
  description = "Security group ID for GPU inference worker nodes"
  value       = aws_security_group.inference_nodes.id
}

output "alb_security_group_id" {
  description = "Security group ID for the inference Application Load Balancer"
  value       = aws_security_group.alb.id
}

output "monitoring_security_group_id" {
  description = "Security group ID for monitoring components"
  value       = aws_security_group.monitoring.id
}

output "management_security_group_id" {
  description = "Security group ID for management and bastion hosts"
  value       = aws_security_group.management.id
}

output "vpc_endpoints_security_group_id" {
  description = "Security group ID for VPC interface endpoints"
  value       = aws_security_group.vpc_endpoints.id
}

# -----------------------------------------------------------------------------
# VPC Endpoints
# -----------------------------------------------------------------------------

output "s3_endpoint_id" {
  description = "ID of the S3 VPC gateway endpoint"
  value       = aws_vpc_endpoint.s3.id
}

output "dynamodb_endpoint_id" {
  description = "ID of the DynamoDB VPC gateway endpoint"
  value       = aws_vpc_endpoint.dynamodb.id
}

output "ecr_api_endpoint_id" {
  description = "ID of the ECR API VPC interface endpoint"
  value       = aws_vpc_endpoint.ecr_api.id
}

output "ecr_dkr_endpoint_id" {
  description = "ID of the ECR Docker VPC interface endpoint"
  value       = aws_vpc_endpoint.ecr_dkr.id
}

# -----------------------------------------------------------------------------
# Secrets Manager
# -----------------------------------------------------------------------------

output "redis_auth_secret_arn" {
  description = "ARN of the Redis auth token secret in Secrets Manager"
  value       = aws_secretsmanager_secret.redis_auth.arn
}

output "grafana_admin_secret_arn" {
  description = "ARN of the Grafana admin credentials secret in Secrets Manager"
  value       = aws_secretsmanager_secret.grafana_admin.arn
}

output "model_api_keys_secret_arn" {
  description = "ARN of the model API keys secret in Secrets Manager"
  value       = aws_secretsmanager_secret.model_api_keys.arn
}
