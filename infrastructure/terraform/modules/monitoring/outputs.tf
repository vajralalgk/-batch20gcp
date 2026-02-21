###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Monitoring Module - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# SNS Topics
# -----------------------------------------------------------------------------

output "alerts_sns_topic_arn" {
  description = "ARN of the general alerts SNS topic"
  value       = aws_sns_topic.alerts.arn
}

output "critical_alerts_sns_topic_arn" {
  description = "ARN of the critical alerts SNS topic"
  value       = aws_sns_topic.critical_alerts.arn
}

output "alerts_sns_topic_name" {
  description = "Name of the general alerts SNS topic"
  value       = aws_sns_topic.alerts.name
}

output "critical_alerts_sns_topic_name" {
  description = "Name of the critical alerts SNS topic"
  value       = aws_sns_topic.critical_alerts.name
}

# -----------------------------------------------------------------------------
# CloudWatch Log Groups
# -----------------------------------------------------------------------------

output "inference_metrics_log_group_name" {
  description = "Name of the CloudWatch log group for inference metrics"
  value       = aws_cloudwatch_log_group.inference_metrics.name
}

output "gpu_metrics_log_group_name" {
  description = "Name of the CloudWatch log group for GPU metrics"
  value       = aws_cloudwatch_log_group.gpu_metrics.name
}

output "application_logs_group_name" {
  description = "Name of the CloudWatch log group for application logs"
  value       = aws_cloudwatch_log_group.application_logs.name
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard
# -----------------------------------------------------------------------------

output "dashboard_name" {
  description = "Name of the CloudWatch inference platform dashboard"
  value       = aws_cloudwatch_dashboard.inference_platform.dashboard_name
}

output "dashboard_arn" {
  description = "ARN of the CloudWatch inference platform dashboard"
  value       = aws_cloudwatch_dashboard.inference_platform.dashboard_arn
}

# -----------------------------------------------------------------------------
# Monitoring Namespace
# -----------------------------------------------------------------------------

output "monitoring_namespace" {
  description = "Kubernetes namespace where monitoring components are deployed"
  value       = kubernetes_namespace.monitoring.metadata[0].name
}

# -----------------------------------------------------------------------------
# Prometheus
# -----------------------------------------------------------------------------

output "prometheus_release_name" {
  description = "Helm release name for Prometheus stack"
  value       = helm_release.prometheus.name
}

output "prometheus_release_status" {
  description = "Status of the Prometheus Helm release"
  value       = helm_release.prometheus.status
}

# -----------------------------------------------------------------------------
# Grafana
# -----------------------------------------------------------------------------

output "grafana_release_name" {
  description = "Helm release name for Grafana"
  value       = helm_release.grafana.name
}

output "grafana_release_status" {
  description = "Status of the Grafana Helm release"
  value       = helm_release.grafana.status
}
