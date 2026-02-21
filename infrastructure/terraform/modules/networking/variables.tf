###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Networking Module - Variables
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
# VPC Configuration
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Must be /16 to support 4-tier subnet architecture across 3 AZs"
  type        = string

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "VPC CIDR must be a valid CIDR block."
  }
}

variable "az_count" {
  description = "Number of availability zones to deploy across (minimum 2 for HA, recommended 3)"
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "AZ count must be between 2 and 4."
  }
}

variable "enable_ipv6" {
  description = "Enable IPv6 CIDR block assignment on the VPC"
  type        = bool
  default     = false
}

variable "eks_cluster_name" {
  description = "Name of the EKS cluster for Kubernetes subnet tagging"
  type        = string
}

# -----------------------------------------------------------------------------
# VPC Peering Configuration
# -----------------------------------------------------------------------------

variable "vpc_peering_configs" {
  description = "Map of VPC peering connection configurations to initiate from this region"
  type = map(object({
    peer_vpc_id = string
    peer_region = string
  }))
  default = {}
}

variable "vpc_peering_accepter_ids" {
  description = "Map of VPC peering connection IDs to accept in this region (initiated from other regions)"
  type        = map(string)
  default     = {}
}

variable "peer_vpc_routes" {
  description = "List of routes to peered VPCs to add to route tables"
  type = list(object({
    cidr_block              = string
    peering_connection_id   = string
  }))
  default = []
}

variable "peer_vpc_cidrs" {
  description = "List of CIDR blocks from peered VPCs for Network ACL rules"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Flow Logs
# -----------------------------------------------------------------------------

variable "flow_log_role_arn" {
  description = "IAM role ARN for VPC flow log delivery to CloudWatch"
  type        = string
}

variable "flow_log_retention_days" {
  description = "Number of days to retain VPC flow logs in CloudWatch"
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "KMS key ARN for encrypting flow log data in CloudWatch"
  type        = string
  default     = ""
}
