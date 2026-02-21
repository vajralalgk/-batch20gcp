###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# us-east-1 (Primary Region) - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID for us-east-1"
  value       = module.networking.vpc_id
}

output "vpc_cidr" {
  description = "VPC CIDR block"
  value       = module.networking.vpc_cidr
}

output "gpu_inference_subnet_ids" {
  description = "GPU inference subnet IDs"
  value       = module.networking.gpu_inference_subnet_ids
}

output "private_app_subnet_ids" {
  description = "Private application subnet IDs"
  value       = module.networking.private_app_subnet_ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.networking.public_subnet_ids
}

output "vpc_peering_connection_ids" {
  description = "VPC peering connection IDs initiated from this region"
  value       = module.networking.vpc_peering_connection_ids
}

# -----------------------------------------------------------------------------
# EKS / Inference
# -----------------------------------------------------------------------------

output "eks_cluster_name" {
  description = "EKS inference cluster name"
  value       = module.inference.eks_cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.inference.eks_cluster_endpoint
}

output "alb_dns_name" {
  description = "Inference ALB DNS name"
  value       = module.inference.alb_dns_name
}

output "alb_zone_id" {
  description = "Inference ALB hosted zone ID"
  value       = module.inference.alb_zone_id
}

# -----------------------------------------------------------------------------
# GPU Cluster
# -----------------------------------------------------------------------------

output "gpu_node_group_name" {
  description = "GPU node group name"
  value       = module.gpu_cluster.node_group_name
}

output "gpu_autoscaling_group_name" {
  description = "GPU auto-scaling group name"
  value       = module.gpu_cluster.autoscaling_group_name
}

# -----------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------

output "kv_cache_endpoint" {
  description = "KV cache Redis primary endpoint"
  value       = module.cache.kv_cache_primary_endpoint
}

output "session_memory_endpoint" {
  description = "Session memory Redis primary endpoint"
  value       = module.cache.session_memory_primary_endpoint
}

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------

output "kms_key_arn" {
  description = "Platform KMS key ARN"
  value       = module.security.kms_key_arn
}

# -----------------------------------------------------------------------------
# Monitoring
# -----------------------------------------------------------------------------

output "alerts_sns_topic_arn" {
  description = "General alerts SNS topic ARN"
  value       = module.monitoring.alerts_sns_topic_arn
}

output "critical_alerts_sns_topic_arn" {
  description = "Critical alerts SNS topic ARN"
  value       = module.monitoring.critical_alerts_sns_topic_arn
}

output "dashboard_name" {
  description = "CloudWatch dashboard name"
  value       = module.monitoring.dashboard_name
}
