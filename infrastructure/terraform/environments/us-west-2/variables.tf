###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# us-west-2 (Secondary Region) - Variables
###############################################################################

# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Project name for resource naming and tagging"
  type        = string
  default     = "nflx-llm-platform"
}

variable "environment" {
  description = "Deployment environment"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Environment must be one of: dev, staging, production."
  }
}

variable "aws_region" {
  description = "AWS region for this deployment"
  type        = string
  default     = "us-west-2"
}

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones"
  type        = number
  default     = 3
}

variable "flow_log_retention_days" {
  description = "VPC flow log retention period in days"
  type        = number
  default     = 30
}

variable "vpc_peering_configs" {
  description = "VPC peering configurations to initiate from this region"
  type = map(object({
    peer_vpc_id = string
    peer_region = string
  }))
  default = {}
}

variable "vpc_peering_accepter_ids" {
  description = "VPC peering connection IDs to accept in this region"
  type        = map(string)
  default     = {}
}

variable "peer_vpc_routes" {
  description = "Routes to peered VPCs"
  type = list(object({
    cidr_block            = string
    peering_connection_id = string
  }))
  default = []
}

variable "peer_vpc_cidrs" {
  description = "CIDR blocks of peered VPCs"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# EKS / Inference
# -----------------------------------------------------------------------------

variable "eks_version" {
  description = "Kubernetes version for EKS clusters"
  type        = string
  default     = "1.29"
}

variable "enable_public_endpoint" {
  description = "Enable public access to EKS API endpoint"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 90
}

# -----------------------------------------------------------------------------
# GPU Cluster
# -----------------------------------------------------------------------------

variable "gpu_instance_type" {
  description = "EC2 instance type for GPU nodes"
  type        = string
  default     = "p4d.24xlarge"
}

variable "enable_efa" {
  description = "Enable Elastic Fabric Adapter"
  type        = bool
  default     = true
}

variable "gpu_root_volume_size" {
  description = "Root volume size in GB for GPU nodes"
  type        = number
  default     = 500
}

variable "gpu_node_desired_count" {
  description = "Desired GPU node count"
  type        = number
  default     = 3
}

variable "gpu_node_min_count" {
  description = "Minimum GPU node count"
  type        = number
  default     = 2
}

variable "gpu_node_max_count" {
  description = "Maximum GPU node count"
  type        = number
  default     = 10
}

variable "warm_pool_min_size" {
  description = "Warm pool minimum size"
  type        = number
  default     = 1
}

variable "warm_pool_max_size" {
  description = "Warm pool maximum size"
  type        = number
  default     = 3
}

variable "gpu_scale_out_threshold" {
  description = "GPU utilization target for scale-out"
  type        = number
  default     = 70
}

variable "latency_target_ms" {
  description = "P99 inference latency target in milliseconds"
  type        = number
  default     = 100
}

# -----------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------

variable "kv_cache_node_type" {
  description = "ElastiCache node type for KV cache"
  type        = string
  default     = "cache.r7g.2xlarge"
}

variable "kv_cache_num_replicas" {
  description = "Number of KV cache read replicas"
  type        = number
  default     = 2
}

variable "session_node_type" {
  description = "ElastiCache node type for session memory"
  type        = string
  default     = "cache.r7g.xlarge"
}

variable "session_num_replicas" {
  description = "Number of session memory read replicas"
  type        = number
  default     = 2
}

# -----------------------------------------------------------------------------
# ALB / TLS
# -----------------------------------------------------------------------------

variable "alb_internal" {
  description = "Whether the ALB is internal-facing"
  type        = bool
  default     = true
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for TLS termination"
  type        = string
}

variable "alb_access_logs_bucket" {
  description = "S3 bucket for ALB access logs"
  type        = string
  default     = ""
}

variable "enable_alb_access_logs" {
  description = "Enable ALB access logging"
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------

variable "model_artifacts_bucket" {
  description = "S3 bucket for model artifacts"
  type        = string
  default     = "nflx-llm-model-artifacts"
}

variable "alb_allowed_cidrs" {
  description = "CIDR blocks allowed to access the ALB"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "monitoring_access_cidrs" {
  description = "CIDR blocks for monitoring access"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "management_access_cidrs" {
  description = "CIDR blocks for management access"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

# -----------------------------------------------------------------------------
# Monitoring
# -----------------------------------------------------------------------------

variable "grafana_admin_password" {
  description = "Grafana admin password"
  type        = string
  sensitive   = true
}

variable "grafana_hosts" {
  description = "Hostnames for Grafana ingress"
  type        = list(string)
  default     = []
}

variable "alert_email_addresses" {
  description = "Email addresses for alert notifications"
  type        = list(string)
  default     = []
}

variable "critical_email_addresses" {
  description = "Email addresses for critical alert notifications"
  type        = list(string)
  default     = []
}
