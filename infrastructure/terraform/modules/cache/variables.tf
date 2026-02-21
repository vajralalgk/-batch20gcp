###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Cache Module - Variables
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
# Networking
# -----------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC ID where Redis clusters will be deployed"
  type        = string
}

variable "subnet_ids" {
  description = "List of private data subnet IDs for Redis placement"
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "List of security group IDs allowed to connect to Redis"
  type        = list(string)
}

# -----------------------------------------------------------------------------
# Redis Configuration - KV Cache
# -----------------------------------------------------------------------------

variable "redis_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.1"
}

variable "kv_cache_node_type" {
  description = "ElastiCache node type for KV cache (memory-optimized for tensor storage)"
  type        = string
  default     = "cache.r7g.2xlarge"
}

variable "kv_cache_num_replicas" {
  description = "Number of read replicas for the KV cache replication group"
  type        = number
  default     = 2

  validation {
    condition     = var.kv_cache_num_replicas >= 1 && var.kv_cache_num_replicas <= 5
    error_message = "KV cache replicas must be between 1 and 5."
  }
}

# -----------------------------------------------------------------------------
# Redis Configuration - Session Memory
# -----------------------------------------------------------------------------

variable "session_node_type" {
  description = "ElastiCache node type for session memory"
  type        = string
  default     = "cache.r7g.xlarge"
}

variable "session_num_replicas" {
  description = "Number of read replicas for the session memory replication group"
  type        = number
  default     = 2

  validation {
    condition     = var.session_num_replicas >= 1 && var.session_num_replicas <= 5
    error_message = "Session replicas must be between 1 and 5."
  }
}

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------

variable "kms_key_arn" {
  description = "KMS key ARN for at-rest encryption of Redis data"
  type        = string
}

variable "enable_transit_encryption" {
  description = "Enable TLS encryption for data in transit"
  type        = bool
  default     = true
}

variable "redis_auth_token" {
  description = "Auth token (password) for Redis. Leave empty to disable AUTH"
  type        = string
  default     = ""
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Maintenance
# -----------------------------------------------------------------------------

variable "maintenance_window" {
  description = "Weekly maintenance window (UTC)"
  type        = string
  default     = "sun:05:00-sun:07:00"
}

variable "snapshot_retention_days" {
  description = "Number of days to retain KV cache snapshots (0 to disable)"
  type        = number
  default     = 1
}

variable "session_snapshot_retention_days" {
  description = "Number of days to retain session memory snapshots"
  type        = number
  default     = 7
}

variable "snapshot_window" {
  description = "Daily window during which snapshots are taken (UTC)"
  type        = string
  default     = "03:00-05:00"
}

# -----------------------------------------------------------------------------
# Notifications
# -----------------------------------------------------------------------------

variable "notification_sns_topic_arn" {
  description = "SNS topic ARN for ElastiCache event notifications"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Alerting
# -----------------------------------------------------------------------------

variable "alarm_sns_topic_arns" {
  description = "List of SNS topic ARNs for CloudWatch alarm notifications"
  type        = list(string)
  default     = []
}

variable "cpu_alarm_threshold" {
  description = "CPU utilization percentage threshold for alarm"
  type        = number
  default     = 75
}

variable "memory_alarm_threshold" {
  description = "Memory usage percentage threshold for alarm"
  type        = number
  default     = 80
}

variable "eviction_alarm_threshold" {
  description = "Eviction count per minute threshold for alarm"
  type        = number
  default     = 1000
}

variable "replication_lag_threshold_seconds" {
  description = "Maximum acceptable replication lag in seconds"
  type        = number
  default     = 5
}
