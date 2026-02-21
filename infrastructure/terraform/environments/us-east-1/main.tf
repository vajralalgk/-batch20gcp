###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Primary Region Configuration - us-east-1
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket         = "nflx-llm-platform-terraform-state"
    key            = "us-east-1/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "nflx-llm-platform-terraform-locks"
    encrypt        = true
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

provider "kubernetes" {
  host                   = module.inference.eks_cluster_endpoint
  cluster_ca_certificate = base64decode(module.inference.eks_cluster_certificate_authority)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.inference.eks_cluster_name]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.inference.eks_cluster_endpoint
    cluster_ca_certificate = base64decode(module.inference.eks_cluster_certificate_authority)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.inference.eks_cluster_name]
    }
  }
}

# -----------------------------------------------------------------------------
# Local Variables
# -----------------------------------------------------------------------------

locals {
  region_short = "use1"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    Region      = var.aws_region
    Platform    = "netflix-llm-personalization"
    ManagedBy   = "terraform"
    CostCenter  = "ml-inference"
    Team        = "personalization-platform"
  }
}

# -----------------------------------------------------------------------------
# Security Module
# -----------------------------------------------------------------------------

module "security" {
  source = "../../modules/security"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  vpc_id                  = module.networking.vpc_id
  vpc_cidr                = var.vpc_cidr
  private_subnet_ids      = module.networking.private_app_subnet_ids
  private_route_table_ids = module.networking.private_route_table_ids

  kms_deletion_window_days = var.environment == "production" ? 30 : 7
  enable_multi_region_kms  = true
  eks_cluster_role_arn     = module.security.eks_cluster_role_arn
  model_artifacts_bucket   = var.model_artifacts_bucket

  oidc_provider_arn = module.inference.eks_oidc_provider_arn
  oidc_provider_url = module.inference.eks_oidc_provider_url

  alb_allowed_cidrs       = var.alb_allowed_cidrs
  monitoring_access_cidrs = var.monitoring_access_cidrs
  management_access_cidrs = var.management_access_cidrs
}

# -----------------------------------------------------------------------------
# Networking Module
# -----------------------------------------------------------------------------

module "networking" {
  source = "../../modules/networking"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  vpc_cidr         = var.vpc_cidr
  az_count         = var.az_count
  eks_cluster_name = "${var.project_name}-${var.environment}-inference-${local.region_short}"

  flow_log_role_arn       = module.security.flow_logs_role_arn
  flow_log_retention_days = var.flow_log_retention_days
  kms_key_arn             = module.security.kms_key_arn

  # VPC peering to us-west-2 and eu-west-1 (primary initiates peering)
  vpc_peering_configs = var.vpc_peering_configs
  peer_vpc_routes     = var.peer_vpc_routes
  peer_vpc_cidrs      = var.peer_vpc_cidrs
}

# -----------------------------------------------------------------------------
# Inference Module (EKS + ALB)
# -----------------------------------------------------------------------------

module "inference" {
  source = "../../modules/inference"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  eks_version    = var.eks_version
  cluster_role_arn = module.security.eks_cluster_role_arn
  kms_key_arn      = module.security.kms_key_arn
  ebs_csi_role_arn = module.security.ebs_csi_role_arn

  vpc_id                    = module.networking.vpc_id
  private_subnet_ids        = module.networking.private_app_subnet_ids
  gpu_subnet_ids            = module.networking.gpu_inference_subnet_ids
  public_subnet_ids         = module.networking.public_subnet_ids
  cluster_security_group_id = module.security.eks_cluster_security_group_id
  alb_security_group_id     = module.security.alb_security_group_id

  alb_internal           = var.alb_internal
  acm_certificate_arn    = var.acm_certificate_arn
  alb_access_logs_bucket = var.alb_access_logs_bucket
  enable_alb_access_logs = var.enable_alb_access_logs
  waf_acl_arn            = var.waf_acl_arn

  enable_public_endpoint = var.enable_public_endpoint
  log_retention_days     = var.log_retention_days

  alarm_sns_topic_arns = [module.monitoring.alerts_sns_topic_arn]
  min_healthy_hosts    = var.gpu_node_min_count
}

# -----------------------------------------------------------------------------
# GPU Cluster Module
# -----------------------------------------------------------------------------

module "gpu_cluster" {
  source = "../../modules/gpu_cluster"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  eks_cluster_name    = module.inference.eks_cluster_name
  eks_cluster_version = var.eks_version
  node_role_arn       = module.security.eks_node_role_arn

  subnet_ids         = module.networking.gpu_inference_subnet_ids
  security_group_ids = [
    module.security.inference_nodes_security_group_id,
    module.networking.efa_security_group_id
  ]

  gpu_instance_type     = var.gpu_instance_type
  enable_efa            = var.enable_efa
  root_volume_size      = var.gpu_root_volume_size
  kms_key_arn           = module.security.kms_key_arn

  gpu_node_desired_count = var.gpu_node_desired_count
  gpu_node_min_count     = var.gpu_node_min_count
  gpu_node_max_count     = var.gpu_node_max_count

  warm_pool_min_size = var.warm_pool_min_size
  warm_pool_max_size = var.warm_pool_max_size

  gpu_scale_out_threshold = var.gpu_scale_out_threshold
  latency_target_ms       = var.latency_target_ms

  alarm_sns_topic_arns = [module.monitoring.alerts_sns_topic_arn, module.monitoring.critical_alerts_sns_topic_arn]
}

# -----------------------------------------------------------------------------
# Cache Module (ElastiCache Redis)
# -----------------------------------------------------------------------------

module "cache" {
  source = "../../modules/cache"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  vpc_id     = module.networking.vpc_id
  subnet_ids = module.networking.private_data_subnet_ids

  allowed_security_group_ids = [
    module.security.inference_nodes_security_group_id
  ]

  kv_cache_node_type    = var.kv_cache_node_type
  kv_cache_num_replicas = var.kv_cache_num_replicas
  session_node_type     = var.session_node_type
  session_num_replicas  = var.session_num_replicas

  kms_key_arn               = module.security.kms_key_arn
  enable_transit_encryption = true

  alarm_sns_topic_arns       = [module.monitoring.alerts_sns_topic_arn]
  notification_sns_topic_arn = module.monitoring.alerts_sns_topic_arn
}

# -----------------------------------------------------------------------------
# Monitoring Module
# -----------------------------------------------------------------------------

module "monitoring" {
  source = "../../modules/monitoring"

  project_name = var.project_name
  environment  = var.environment
  region_short = local.region_short
  common_tags  = local.common_tags

  eks_cluster_name = module.inference.eks_cluster_name

  kms_key_arn    = module.security.kms_key_arn
  kms_key_id     = module.security.kms_key_id
  alb_arn_suffix = module.inference.alb_arn_suffix

  acm_certificate_arn    = var.acm_certificate_arn
  grafana_admin_password = var.grafana_admin_password
  grafana_hosts          = var.grafana_hosts
  log_retention_days     = var.log_retention_days

  alert_email_addresses    = var.alert_email_addresses
  critical_email_addresses = var.critical_email_addresses

  # Self-referencing: monitoring SNS topics are created within the module
  alert_sns_topic_arn    = ""
  critical_sns_topic_arn = ""
}
