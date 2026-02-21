###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Inference Module - Variables
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
# EKS Cluster Configuration
# -----------------------------------------------------------------------------

variable "eks_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.29"
}

variable "cluster_role_arn" {
  description = "IAM role ARN for the EKS cluster control plane"
  type        = string
}

variable "service_cidr" {
  description = "CIDR block for Kubernetes service IPs"
  type        = string
  default     = "172.20.0.0/16"
}

variable "enable_public_endpoint" {
  description = "Enable public access to the EKS API server endpoint"
  type        = bool
  default     = false
}

variable "public_access_cidrs" {
  description = "List of CIDR blocks that can access the public EKS API endpoint"
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "Number of days to retain EKS cluster logs in CloudWatch"
  type        = number
  default     = 90
}

# -----------------------------------------------------------------------------
# EKS Addon Versions
# -----------------------------------------------------------------------------

variable "vpc_cni_version" {
  description = "Version of the VPC CNI addon"
  type        = string
  default     = "v1.16.0-eksbuild.1"
}

variable "coredns_version" {
  description = "Version of the CoreDNS addon"
  type        = string
  default     = "v1.11.1-eksbuild.6"
}

variable "kube_proxy_version" {
  description = "Version of the kube-proxy addon"
  type        = string
  default     = "v1.29.0-eksbuild.3"
}

variable "ebs_csi_version" {
  description = "Version of the EBS CSI driver addon"
  type        = string
  default     = "v1.28.0-eksbuild.1"
}

variable "ebs_csi_role_arn" {
  description = "IAM role ARN for the EBS CSI driver service account"
  type        = string
}

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC ID where inference resources will be deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for application workloads"
  type        = list(string)
}

variable "gpu_subnet_ids" {
  description = "List of GPU inference subnet IDs"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for external-facing ALB"
  type        = list(string)
}

variable "cluster_security_group_id" {
  description = "Security group ID for the EKS cluster control plane"
  type        = string
}

variable "alb_security_group_id" {
  description = "Security group ID for the inference Application Load Balancer"
  type        = string
}

# -----------------------------------------------------------------------------
# Encryption
# -----------------------------------------------------------------------------

variable "kms_key_arn" {
  description = "KMS key ARN for encrypting EKS secrets and log data"
  type        = string
}

# -----------------------------------------------------------------------------
# ALB Configuration
# -----------------------------------------------------------------------------

variable "alb_internal" {
  description = "Whether the ALB is internal-facing (true) or internet-facing (false)"
  type        = bool
  default     = true
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for HTTPS/TLS termination on the ALB"
  type        = string
}

variable "alb_access_logs_bucket" {
  description = "S3 bucket name for ALB access logs"
  type        = string
  default     = ""
}

variable "enable_alb_access_logs" {
  description = "Enable ALB access logging to S3"
  type        = bool
  default     = true
}

variable "waf_acl_arn" {
  description = "WAFv2 Web ACL ARN to associate with the inference ALB (empty to disable)"
  type        = string
  default     = ""
}

variable "monitoring_cidr_blocks" {
  description = "CIDR blocks allowed to scrape the /metrics endpoint"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

# -----------------------------------------------------------------------------
# Alerting
# -----------------------------------------------------------------------------

variable "alarm_sns_topic_arns" {
  description = "List of SNS topic ARNs for CloudWatch alarm notifications"
  type        = list(string)
  default     = []
}

variable "alb_5xx_threshold" {
  description = "Threshold for ALB 5xx error count alarm"
  type        = number
  default     = 10
}

variable "alb_latency_threshold_seconds" {
  description = "Threshold in seconds for ALB p99 latency alarm"
  type        = number
  default     = 0.5
}

variable "min_healthy_hosts" {
  description = "Minimum number of healthy hosts before alarm triggers"
  type        = number
  default     = 2
}
