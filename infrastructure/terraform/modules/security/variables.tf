###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Security Module - Variables
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
# Networking References
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC ID where security groups will be created"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block for security group rules"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for VPC interface endpoints"
  type        = list(string)
}

variable "private_route_table_ids" {
  description = "List of private route table IDs for VPC gateway endpoints"
  type        = list(string)
}

# -----------------------------------------------------------------------------
# KMS Configuration
# -----------------------------------------------------------------------------

variable "kms_deletion_window_days" {
  description = "Number of days before KMS key deletion (minimum 7 for production)"
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "KMS deletion window must be between 7 and 30 days."
  }
}

variable "enable_multi_region_kms" {
  description = "Enable multi-region KMS key for cross-region model encryption"
  type        = bool
  default     = true
}

variable "eks_cluster_role_arn" {
  description = "ARN of the EKS cluster IAM role for KMS key policy"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# OIDC Provider (for service account IAM roles)
# -----------------------------------------------------------------------------

variable "oidc_provider_arn" {
  description = "ARN of the EKS OIDC provider for IRSA (IAM Roles for Service Accounts)"
  type        = string
  default     = ""
}

variable "oidc_provider_url" {
  description = "URL of the EKS OIDC provider (without https:// prefix)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# S3 Model Storage
# -----------------------------------------------------------------------------

variable "model_artifacts_bucket" {
  description = "S3 bucket name where ML model artifacts are stored"
  type        = string
  default     = "nflx-llm-model-artifacts"
}

# -----------------------------------------------------------------------------
# Security Group Access Control
# -----------------------------------------------------------------------------

variable "alb_allowed_cidrs" {
  description = "CIDR blocks allowed to access the inference ALB"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "monitoring_access_cidrs" {
  description = "CIDR blocks allowed to access Grafana and monitoring endpoints"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "management_access_cidrs" {
  description = "CIDR blocks allowed SSH access to management hosts"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}
