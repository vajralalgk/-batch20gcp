###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# GPU Cluster Module - Variables
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
  description = "Common tags applied to all resources for cost tracking and organization"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# EKS Cluster Configuration
# -----------------------------------------------------------------------------

variable "eks_cluster_name" {
  description = "Name of the EKS cluster to attach GPU node groups to"
  type        = string
}

variable "eks_cluster_version" {
  description = "Kubernetes version for EKS-optimized GPU AMI lookup"
  type        = string
  default     = "1.29"
}

variable "node_role_arn" {
  description = "IAM role ARN for EKS worker nodes"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for GPU node placement (private subnets recommended)"
  type        = list(string)
}

variable "security_group_ids" {
  description = "List of security group IDs to attach to GPU instances"
  type        = list(string)
}

# -----------------------------------------------------------------------------
# GPU Instance Configuration
# -----------------------------------------------------------------------------

variable "gpu_instance_type" {
  description = "EC2 instance type for GPU inference nodes (p4d.24xlarge = 8x A100 40GB)"
  type        = string
  default     = "p4d.24xlarge"

  validation {
    condition     = contains(["p4d.24xlarge", "p4de.24xlarge", "p5.48xlarge"], var.gpu_instance_type)
    error_message = "GPU instance type must be one of: p4d.24xlarge, p4de.24xlarge, p5.48xlarge."
  }
}

variable "custom_ami_id" {
  description = "Custom AMI ID for GPU nodes. If empty, uses the EKS-optimized GPU AMI"
  type        = string
  default     = ""
}

variable "nvidia_driver_version" {
  description = "NVIDIA driver version to install on GPU nodes"
  type        = string
  default     = "535.129.03"
}

variable "enable_efa" {
  description = "Enable Elastic Fabric Adapter for high-bandwidth GPU-to-GPU communication"
  type        = bool
  default     = true
}

variable "enable_gpu_mps" {
  description = "Enable NVIDIA Multi-Process Service for GPU sharing"
  type        = bool
  default     = false
}

variable "gpu_time_slicing_replicas" {
  description = "Number of GPU time-slicing replicas (0 to disable)"
  type        = number
  default     = 0

  validation {
    condition     = var.gpu_time_slicing_replicas >= 0 && var.gpu_time_slicing_replicas <= 8
    error_message = "GPU time slicing replicas must be between 0 and 8."
  }
}

variable "use_spot_instances" {
  description = "Use Spot instances for GPU nodes (not recommended for production inference)"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Storage Configuration
# -----------------------------------------------------------------------------

variable "root_volume_size" {
  description = "Root EBS volume size in GB for GPU nodes"
  type        = number
  default     = 500

  validation {
    condition     = var.root_volume_size >= 200
    error_message = "Root volume must be at least 200 GB for GPU workloads."
  }
}

variable "kms_key_arn" {
  description = "KMS key ARN for EBS volume encryption"
  type        = string
}

# -----------------------------------------------------------------------------
# Scaling Configuration
# -----------------------------------------------------------------------------

variable "gpu_node_desired_count" {
  description = "Desired number of GPU nodes in the node group"
  type        = number
  default     = 3
}

variable "gpu_node_min_count" {
  description = "Minimum number of GPU nodes (must always be running for inference SLA)"
  type        = number
  default     = 2
}

variable "gpu_node_max_count" {
  description = "Maximum number of GPU nodes for auto-scaling"
  type        = number
  default     = 10
}

variable "max_unavailable_percentage" {
  description = "Maximum percentage of nodes that can be unavailable during updates"
  type        = number
  default     = 25
}

variable "gpu_scale_out_threshold" {
  description = "GPU utilization percentage target for scale-out policy"
  type        = number
  default     = 70
}

variable "latency_target_ms" {
  description = "Target p99 inference latency in milliseconds for scaling decisions"
  type        = number
  default     = 100
}

variable "scale_in_cooldown" {
  description = "Cooldown period in seconds after a scale-in event"
  type        = number
  default     = 600
}

# -----------------------------------------------------------------------------
# Warm Pool Configuration
# -----------------------------------------------------------------------------

variable "warm_pool_min_size" {
  description = "Minimum number of pre-initialized instances in the warm pool"
  type        = number
  default     = 1
}

variable "warm_pool_max_size" {
  description = "Maximum number of pre-initialized instances in the warm pool"
  type        = number
  default     = 3
}

# -----------------------------------------------------------------------------
# Monitoring & Alerting
# -----------------------------------------------------------------------------

variable "alarm_sns_topic_arns" {
  description = "List of SNS topic ARNs for CloudWatch alarm notifications"
  type        = list(string)
  default     = []
}

variable "gpu_alarm_high_threshold" {
  description = "GPU utilization percentage threshold for high-utilization alarm"
  type        = number
  default     = 90
}

variable "gpu_memory_alarm_threshold" {
  description = "GPU memory utilization percentage threshold for alarm"
  type        = number
  default     = 85
}

# -----------------------------------------------------------------------------
# Kubelet Configuration
# -----------------------------------------------------------------------------

variable "kubelet_extra_args" {
  description = "Extra arguments to pass to kubelet on GPU nodes"
  type        = string
  default     = "--max-pods=110 --kube-reserved cpu=1000m,memory=2Gi,ephemeral-storage=10Gi --system-reserved cpu=500m,memory=1Gi,ephemeral-storage=5Gi"
}
