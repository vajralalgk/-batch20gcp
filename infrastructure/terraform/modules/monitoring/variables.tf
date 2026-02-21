###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Monitoring Module - Variables
###############################################################################

# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Project name used for resource naming and tagging"
  type        = string
  default     = "nflx-llm-platform"
}

variable "environment" {
  description = "Deployment environment (dev, staging, production)"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production."
  }
}

variable "region_short" {
  description = "Short region identifier for resource naming (e.g., use1, usw2, euw1)"
  type        = string
}

variable "common_tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# EKS Cluster Reference
# -----------------------------------------------------------------------------

variable "eks_cluster_name" {
  description = "Name of the EKS cluster being monitored"
  type        = string
}

variable "monitoring_namespace" {
  description = "Kubernetes namespace for monitoring components"
  type        = string
  default     = "monitoring"
}

# -----------------------------------------------------------------------------
# Prometheus Configuration
# -----------------------------------------------------------------------------

variable "prometheus_chart_version" {
  description = "Helm chart version for kube-prometheus-stack"
  type        = string
  default     = "56.6.2"
}

variable "prometheus_replicas" {
  description = "Number of Prometheus server replicas"
  type        = number
  default     = 2
}

variable "prometheus_retention" {
  description = "Data retention period for Prometheus"
  type        = string
  default     = "30d"
}

variable "prometheus_storage_size" {
  description = "Storage size for Prometheus persistent volume"
  type        = string
  default     = "200Gi"
}

variable "prometheus_cpu_request" {
  description = "CPU request for Prometheus server"
  type        = string
  default     = "1000m"
}

variable "prometheus_cpu_limit" {
  description = "CPU limit for Prometheus server"
  type        = string
  default     = "4000m"
}

variable "prometheus_memory_request" {
  description = "Memory request for Prometheus server"
  type        = string
  default     = "4Gi"
}

variable "prometheus_memory_limit" {
  description = "Memory limit for Prometheus server"
  type        = string
  default     = "8Gi"
}

# -----------------------------------------------------------------------------
# Grafana Configuration
# -----------------------------------------------------------------------------

variable "grafana_chart_version" {
  description = "Helm chart version for Grafana"
  type        = string
  default     = "7.3.3"
}

variable "grafana_replicas" {
  description = "Number of Grafana replicas"
  type        = number
  default     = 2
}

variable "grafana_storage_size" {
  description = "Storage size for Grafana persistent volume"
  type        = string
  default     = "20Gi"
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana"
  type        = string
  sensitive   = true
}

variable "enable_grafana_ingress" {
  description = "Enable ALB ingress for Grafana"
  type        = bool
  default     = true
}

variable "grafana_hosts" {
  description = "Hostnames for Grafana ingress"
  type        = list(string)
  default     = []
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for Grafana ingress TLS"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# DCGM Exporter Configuration
# -----------------------------------------------------------------------------

variable "dcgm_exporter_image" {
  description = "Docker image for NVIDIA DCGM exporter"
  type        = string
  default     = "nvcr.io/nvidia/k8s/dcgm-exporter:3.3.5-3.4.0-ubuntu22.04"
}

# -----------------------------------------------------------------------------
# CloudWatch Configuration
# -----------------------------------------------------------------------------

variable "log_retention_days" {
  description = "Number of days to retain CloudWatch logs"
  type        = number
  default     = 90
}

variable "kms_key_arn" {
  description = "KMS key ARN for encrypting CloudWatch log groups"
  type        = string
  default     = ""
}

variable "kms_key_id" {
  description = "KMS key ID for SNS topic encryption"
  type        = string
  default     = ""
}

variable "alb_arn_suffix" {
  description = "ARN suffix of the inference ALB for CloudWatch dashboard metrics"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# SNS / Alerting Configuration
# -----------------------------------------------------------------------------

variable "alert_sns_topic_arn" {
  description = "SNS topic ARN for general alerts (used by Alertmanager)"
  type        = string
  default     = ""
}

variable "critical_sns_topic_arn" {
  description = "SNS topic ARN for critical alerts (used by Alertmanager)"
  type        = string
  default     = ""
}

variable "alert_email_addresses" {
  description = "Email addresses to subscribe to the alerts SNS topic"
  type        = list(string)
  default     = []
}

variable "critical_email_addresses" {
  description = "Email addresses to subscribe to the critical alerts SNS topic"
  type        = list(string)
  default     = []
}
