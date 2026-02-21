###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Inference Module - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# EKS Cluster
# -----------------------------------------------------------------------------

output "eks_cluster_name" {
  description = "Name of the EKS inference cluster"
  value       = aws_eks_cluster.inference.name
}

output "eks_cluster_arn" {
  description = "ARN of the EKS inference cluster"
  value       = aws_eks_cluster.inference.arn
}

output "eks_cluster_endpoint" {
  description = "Endpoint URL for the EKS API server"
  value       = aws_eks_cluster.inference.endpoint
}

output "eks_cluster_certificate_authority" {
  description = "Base64-encoded certificate authority data for the EKS cluster"
  value       = aws_eks_cluster.inference.certificate_authority[0].data
}

output "eks_cluster_version" {
  description = "Kubernetes version of the EKS cluster"
  value       = aws_eks_cluster.inference.version
}

output "eks_cluster_security_group_id" {
  description = "Security group ID created by EKS for cluster communication"
  value       = aws_eks_cluster.inference.vpc_config[0].cluster_security_group_id
}

output "eks_oidc_provider_arn" {
  description = "ARN of the OIDC provider for EKS service account IAM roles"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "eks_oidc_provider_url" {
  description = "URL of the OIDC provider (without https:// prefix)"
  value       = replace(aws_eks_cluster.inference.identity[0].oidc[0].issuer, "https://", "")
}

# -----------------------------------------------------------------------------
# Application Load Balancer
# -----------------------------------------------------------------------------

output "alb_id" {
  description = "ID of the inference Application Load Balancer"
  value       = aws_lb.inference.id
}

output "alb_arn" {
  description = "ARN of the inference Application Load Balancer"
  value       = aws_lb.inference.arn
}

output "alb_dns_name" {
  description = "DNS name of the inference ALB for Route 53 alias records"
  value       = aws_lb.inference.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone ID of the inference ALB for Route 53 alias records"
  value       = aws_lb.inference.zone_id
}

output "alb_arn_suffix" {
  description = "ARN suffix of the inference ALB for CloudWatch metrics"
  value       = aws_lb.inference.arn_suffix
}

# -----------------------------------------------------------------------------
# Target Groups
# -----------------------------------------------------------------------------

output "triton_http_target_group_arn" {
  description = "ARN of the Triton HTTP target group (port 8000)"
  value       = aws_lb_target_group.triton_http.arn
}

output "triton_grpc_target_group_arn" {
  description = "ARN of the Triton gRPC target group (port 8001)"
  value       = aws_lb_target_group.triton_grpc.arn
}

output "triton_metrics_target_group_arn" {
  description = "ARN of the Triton metrics target group (port 8002)"
  value       = aws_lb_target_group.triton_metrics.arn
}

# -----------------------------------------------------------------------------
# Listeners
# -----------------------------------------------------------------------------

output "https_listener_arn" {
  description = "ARN of the HTTPS listener (port 443)"
  value       = aws_lb_listener.https.arn
}

output "grpc_listener_arn" {
  description = "ARN of the gRPC listener (port 9000)"
  value       = aws_lb_listener.grpc.arn
}
