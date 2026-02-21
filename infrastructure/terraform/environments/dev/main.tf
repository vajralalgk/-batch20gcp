# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Dev Environment
# =============================================================================
# Author: Gopi Krishna Vajrala
# Description: Root configuration for the ECTP development environment.
#              Calls all infrastructure modules (networking, security, compute,
#              database, monitoring) with dev-specific values optimized for
#              cost savings, fast iteration, and developer experience.
#
# Environment Characteristics:
#   - Cost-optimized: Smaller instances, fewer AZs, shorter log retention
#   - Fast iteration: Deletion protection disabled for easy teardown
#   - Full stack: All modules deployed for integration testing
#   - Dev-specific: Lower alarm thresholds, relaxed security policies
#
# Usage:
#   1. Copy terraform.tfvars.example to terraform.tfvars
#   2. Fill in the required values (AWS region, account, container image)
#   3. Run: terraform init
#   4. Run: terraform plan
#   5. Run: terraform apply
#
# State Management:
#   - State is stored in S3 with DynamoDB locking for team collaboration
#   - State file is encrypted at rest using AES-256
#   - DynamoDB table prevents concurrent modifications
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration Block
# -----------------------------------------------------------------------------
# Configures the Terraform backend for remote state storage and provider versions.
# Remote state in S3 with DynamoDB locking enables team collaboration.
# -----------------------------------------------------------------------------
terraform {
  # Require Terraform 1.5+ for modern features and security improvements
  required_version = ">= 1.5.0"

  # Configure the S3 backend for remote state storage
  # WHY: Remote state enables:
  #      1. Team collaboration (multiple engineers can run terraform)
  #      2. State locking (prevents concurrent modifications)
  #      3. State encryption (protects sensitive data in the state file)
  #      4. State versioning (rollback capability via S3 versioning)
  backend "s3" {
    # S3 bucket for storing the Terraform state file
    # WHY: S3 provides durable, versioned storage for the state file
    # SECURITY: Bucket should have versioning, encryption, and access logging enabled
    bucket = "ectp-terraform-state-dev"

    # State file path within the S3 bucket
    # WHY: Organizing state files by environment prevents accidental
    #      cross-environment modifications
    key = "dev/terraform.tfstate"

    # AWS region for the S3 bucket and DynamoDB table
    # WHY: State storage should be in the same region as the infrastructure
    #      for lowest latency and data residency compliance
    region = "us-east-1"

    # Enable server-side encryption for the state file at rest
    # WHY: The Terraform state file contains sensitive data including
    #      resource IDs, endpoint URLs, and potentially secret values
    # SECURITY: AES-256 encryption via S3-managed keys (SSE-S3)
    encrypt = true

    # DynamoDB table for state locking and consistency checking
    # WHY: Prevents two engineers from running terraform apply simultaneously,
    #      which could corrupt the state file or create conflicting resources
    # SECURITY: The lock record contains the user identity for audit purposes
    dynamodb_table = "ectp-terraform-locks-dev"

    # Use path-style access for compatibility
    # WHY: Some organizations require path-style S3 access for VPC endpoints
    # NOTE: This is deprecated by AWS but still used in some environments
    # use_path_style = true
  }

  # Define required provider versions
  required_providers {
    # AWS provider for all infrastructure resources
    aws = {
      # Use the official HashiCorp AWS provider
      source = "hashicorp/aws"
      # Pin to version 5.x for stability
      version = "~> 5.0"
    }
    # Random provider for password generation in the security module
    random = {
      # Use the official HashiCorp random provider
      source = "hashicorp/random"
      # Pin to version 3.x
      version = "~> 3.0"
    }
  }
}

# -----------------------------------------------------------------------------
# AWS Provider Configuration
# -----------------------------------------------------------------------------
# Configures the AWS provider with the region and default tags applied to
# all resources created in this environment.
# -----------------------------------------------------------------------------
provider "aws" {
  # Set the AWS region for all resources
  # WHY: All ECTP dev resources are deployed in a single region
  region = var.aws_region

  # Apply default tags to every resource created by this provider
  # WHY: Default tags ensure consistent tagging without repeating in each module
  # SECURITY: Tags enable cost allocation, compliance scanning, and governance
  default_tags {
    tags = {
      # Project name for cost allocation reports
      Project = var.project_name
      # Environment for filtering and access control
      Environment = var.environment
      # Terraform management indicator to prevent manual modifications
      ManagedBy = "terraform"
      # Author for accountability and contact
      Author = "Gopi Krishna Vajrala"
      # Workspace identifier for multi-workspace setups
      Workspace = terraform.workspace
    }
  }
}

# -----------------------------------------------------------------------------
# Input Variables for the Dev Environment
# -----------------------------------------------------------------------------

# The AWS region for deploying all resources
variable "aws_region" {
  # Documents the region selection rationale
  description = "AWS region for deploying all ECTP dev resources"
  # Enforce string type
  type = string
  # Default to US East 1 for broadest service availability
  default = "us-east-1"
}

# The project name for resource naming across all modules
variable "project_name" {
  # Documents the naming convention
  description = "Project name used as prefix for all resource names across modules"
  # Enforce string type
  type = string
  # Default to ectp for the Enterprise Cloud Transformation Platform
  default = "ectp"
}

# The environment name passed to all modules
variable "environment" {
  # Documents that this is fixed to dev for this environment
  description = "The environment name (fixed to 'dev' for this configuration)"
  # Enforce string type
  type = string
  # Default to dev since this is the dev environment configuration
  default = "dev"
}

# The container image URI for the ECTP API
variable "container_image" {
  # Documents the expected format
  description = "Docker container image URI for the ECTP API (e.g., ECR repo URI with tag)"
  # Enforce string type
  type = string
}

# The ACM certificate ARN for HTTPS (optional in dev)
variable "acm_certificate_arn" {
  # Documents that HTTPS is optional in dev environments
  description = "ACM certificate ARN for HTTPS on the ALB (optional in dev, required in prod)"
  # Enforce string type
  type = string
  # Default to empty; HTTP-only in dev if no certificate is available
  default = ""
}

# Email addresses for alarm notifications
variable "alert_emails" {
  # Documents the notification setup
  description = "List of email addresses to receive CloudWatch alarm notifications"
  # Enforce list of strings type
  type = list(string)
  # Default to empty list
  default = []
}

# =============================================================================
# MODULE: NETWORKING
# =============================================================================
# Provisions the VPC, subnets (public, private app, private data, isolated),
# Internet Gateway, NAT Gateways, route tables, NACLs, and VPC endpoints.
# Dev environment uses 2 AZs (instead of 3) to reduce NAT Gateway costs.
# =============================================================================
module "networking" {
  # Source the networking module from the modules directory
  # WHY: Using relative paths keeps all modules within the same repository
  source = "../../modules/networking"

  # Pass the project name for consistent resource naming
  project_name = var.project_name

  # Pass the environment name for environment-specific configuration
  environment = var.environment

  # Use a /16 VPC CIDR for ample address space in dev
  # WHY: /16 provides 65,536 IPs, sufficient for dev with room for growth
  vpc_cidr = "10.0.0.0/16"

  # Use 2 AZs for dev to reduce costs (saves ~$32/month on NAT Gateways)
  # WHY: Dev does not need the same level of fault tolerance as production
  #      2 AZs still provides basic HA for testing multi-AZ functionality
  az_count = 2

  # Define public subnet CIDRs for ALB and NAT Gateways (2 AZs)
  # WHY: Public subnets host only the ALB and NAT Gateways
  public_subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24"]

  # Define private app subnet CIDRs for ECS Fargate tasks (2 AZs)
  # WHY: Application containers run in private subnets behind the ALB
  private_app_subnet_cidrs = ["10.0.11.0/24", "10.0.12.0/24"]

  # Define private data subnet CIDRs for RDS PostgreSQL (2 AZs)
  # WHY: Database instances reside in isolated data subnets
  private_data_subnet_cidrs = ["10.0.21.0/24", "10.0.22.0/24"]

  # Define isolated subnet CIDRs for sensitive data processing (2 AZs)
  # WHY: Isolated subnets have no internet access for maximum security
  isolated_subnet_cidrs = ["10.0.31.0/24", "10.0.32.0/24"]

  # Retain VPC Flow Logs for 30 days in dev (shorter than prod)
  # WHY: Dev environments generate diagnostic logs that are rarely needed
  #      beyond 30 days; shorter retention reduces costs
  flow_log_retention_days = 30

  # Use the KMS key from the security module for log encryption
  # WHY: Even dev logs may contain test data with PII patterns
  kms_key_arn = module.security.kms_key_arn

  # Apply environment-specific tags
  tags = {
    # Cost center for dev environment billing
    CostCenter = "engineering-dev"
    # Data classification for dev environment
    DataClassification = "internal"
  }
}

# =============================================================================
# MODULE: SECURITY
# =============================================================================
# Provisions the KMS encryption key, IAM roles for ECS tasks, Secrets Manager
# secret for database credentials, and three-tier security groups.
# =============================================================================
module "security" {
  # Source the security module from the modules directory
  source = "../../modules/security"

  # Pass the project name for resource naming
  project_name = var.project_name

  # Pass the environment name for security policy decisions
  environment = var.environment

  # Pass the VPC ID from the networking module for security group creation
  # WHY: Security groups are VPC-scoped and must reference the correct VPC
  vpc_id = module.networking.vpc_id

  # Pass the VPC CIDR for internal traffic security group rules
  # WHY: DNS and internal traffic rules reference the VPC CIDR
  vpc_cidr = module.networking.vpc_cidr

  # Set the container port for app-tier security group rules
  # WHY: The ALB forwards traffic to containers on this port
  container_port = 8080

  # Set the database port for data-tier security group rules
  # WHY: Application containers connect to PostgreSQL on this port
  db_port = 5432

  # Set the database name stored in Secrets Manager
  # WHY: The secret JSON includes the database name for application use
  db_name = "ectp_db"

  # Set the database username stored in Secrets Manager
  # WHY: The secret JSON includes the username for application use
  db_username = "ectp_admin"

  # Apply tags
  tags = {
    # Cost center
    CostCenter = "engineering-dev"
  }
}

# =============================================================================
# MODULE: COMPUTE
# =============================================================================
# Provisions the ECS Fargate cluster, task definition (512 CPU, 1024 memory),
# ALB with target group, and auto-scaling policies.
# Dev environment uses smaller desired count and relaxed scaling limits.
# =============================================================================
module "compute" {
  # Source the compute module from the modules directory
  source = "../../modules/compute"

  # Pass the project name for resource naming
  project_name = var.project_name

  # Pass the environment name
  environment = var.environment

  # Pass the VPC ID for target group association
  vpc_id = module.networking.vpc_id

  # Pass public subnet IDs for ALB placement across AZs
  # WHY: ALB requires subnets in at least 2 AZs for high availability
  public_subnet_ids = module.networking.public_subnet_ids

  # Pass private app subnet IDs for ECS task placement
  # WHY: Fargate tasks run in private subnets behind the ALB
  private_app_subnet_ids = module.networking.private_app_subnet_ids

  # Pass the container image URI for the ECTP API
  # WHY: This is the Docker image that ECS will run
  container_image = var.container_image

  # Set the container port the application listens on
  # WHY: Must match the application's configured listening port
  container_port = 8080

  # Set desired task count to 1 for dev (cost savings)
  # WHY: Dev does not need multiple tasks; 1 is sufficient for testing
  #      Auto-scaling can still add more tasks under load
  desired_count = 1

  # Pass the ALB security group from the security module
  # WHY: Controls inbound traffic to the ALB
  alb_security_group_id = module.security.alb_security_group_id

  # Pass the app security group from the security module
  # WHY: Controls traffic to and from ECS tasks
  ecs_security_group_id = module.security.app_security_group_id

  # Pass the ECS task execution role from the security module
  # WHY: Required for image pulls, log writes, and secret fetches
  ecs_execution_role_arn = module.security.ecs_task_execution_role_arn

  # Pass the ECS task role from the security module
  # WHY: Application-level permissions for AWS API access
  ecs_task_role_arn = module.security.ecs_task_role_arn

  # Pass the log group name from the monitoring module
  # WHY: Container logs are sent to this CloudWatch log group
  log_group_name = module.monitoring.log_group_name

  # Pass the KMS key ARN for log encryption
  # WHY: CloudWatch logs are encrypted with the project KMS key
  kms_key_arn = module.security.kms_key_arn

  # Pass the Secrets Manager secret ARN for database credentials
  # WHY: Database credentials are injected into containers at startup
  container_secrets = [
    {
      # Inject database username from Secrets Manager
      name      = "DB_USERNAME"
      valueFrom = "${module.security.db_secret_arn}:username::"
    },
    {
      # Inject database password from Secrets Manager
      name      = "DB_PASSWORD"
      valueFrom = "${module.security.db_secret_arn}:password::"
    }
  ]

  # Pass the ACM certificate ARN for HTTPS (if available)
  acm_certificate_arn = var.acm_certificate_arn

  # Set ECS task CPU to 512 units (0.5 vCPU) for the ECTP API
  # WHY: 512 CPU units is cost-effective for dev workloads
  task_cpu = 512

  # Set ECS task memory to 1024 MiB (1 GB) for the ECTP API
  # WHY: 1 GB is sufficient for a FastAPI application in dev
  task_memory = 1024

  # Set minimum capacity for auto-scaling
  # WHY: 1 task minimum in dev for cost savings
  min_capacity = 1

  # Set maximum capacity for auto-scaling
  # WHY: 4 tasks maximum in dev to cap costs during testing
  max_capacity = 4

  # Apply tags
  tags = {
    # Cost center
    CostCenter = "engineering-dev"
  }
}

# =============================================================================
# MODULE: DATABASE
# =============================================================================
# Provisions RDS PostgreSQL Multi-AZ with db.r6g.large, encrypted storage,
# 35-day backup retention, custom parameter group, and event subscription.
# Dev environment uses the same instance class for production parity testing.
# =============================================================================
module "database" {
  # Source the database module from the modules directory
  source = "../../modules/database"

  # Pass the project name for resource naming
  project_name = var.project_name

  # Pass the environment name for protection and sizing decisions
  environment = var.environment

  # Pass the VPC ID for security group and subnet group creation
  vpc_id = module.networking.vpc_id

  # Pass private data subnet IDs for the DB subnet group
  # WHY: RDS requires subnets in at least 2 AZs for Multi-AZ deployment
  private_data_subnet_ids = module.networking.private_data_subnet_ids

  # Pass app subnet CIDRs for database security group ingress rules
  # WHY: Only application subnets should be able to reach the database
  app_subnet_cidrs = module.networking.private_app_subnet_cidrs

  # Pass the app security group for SG-to-SG ingress rules
  # WHY: More secure than CIDR-based rules for database access control
  app_security_group_id = module.security.app_security_group_id

  # Use db.r6g.large for production parity in dev
  # WHY: Testing with the same instance class catches size-related issues
  #      Consider db.t3.medium for strict cost-saving dev environments
  instance_class = "db.r6g.large"

  # Use PostgreSQL 15.4 engine version
  # WHY: Match the planned production version for compatibility testing
  engine_version = "15.4"

  # Set the database name
  database_name = "ectp_db"

  # Set the master username
  master_username = "ectp_admin"

  # Pass the generated master password from the security module
  # WHY: Passwords are generated and stored in Secrets Manager by the security module
  master_password = module.security.db_master_password

  # Set initial storage to 50 GB for dev (lower than prod)
  # WHY: Dev environments have less data; 50 GB saves costs
  allocated_storage = 50

  # Set max storage to 100 GB for dev auto-scaling cap
  # WHY: Prevents unbounded storage growth in dev
  max_allocated_storage = 100

  # Pass the KMS key for storage encryption
  # WHY: Even dev databases should be encrypted for security consistency
  kms_key_arn = module.security.kms_key_arn

  # Set backup retention to 35 days as required
  # WHY: 35 days provides compliance-grade backup retention
  backup_retention_period = 35

  # Set backup window during low-usage hours (UTC)
  # WHY: 3-4 AM UTC minimizes impact on development activities
  preferred_backup_window = "03:00-04:00"

  # Set maintenance window to Sunday early morning (UTC)
  # WHY: Sunday 5-6 AM UTC avoids impacting weekday development
  preferred_maintenance_window = "sun:05:00-sun:06:00"

  # Pass the SNS topic ARN for database event notifications
  # WHY: Notifications alert the team about failovers, maintenance, and backups
  sns_topic_arn = module.monitoring.sns_topic_arn

  # Apply tags
  tags = {
    # Cost center
    CostCenter = "engineering-dev"
    # Data classification for the database
    DataClassification = "confidential"
  }
}

# =============================================================================
# MODULE: MONITORING
# =============================================================================
# Provisions CloudWatch log group, metric alarms (CPU, latency, errors),
# SNS topic for notifications, and an operational dashboard.
# Dev environment uses relaxed thresholds and shorter log retention.
# =============================================================================
module "monitoring" {
  # Source the monitoring module from the modules directory
  source = "../../modules/monitoring"

  # Pass the project name for resource naming
  project_name = var.project_name

  # Pass the environment name for threshold configuration
  environment = var.environment

  # Pass the AWS region for dashboard configuration
  aws_region = var.aws_region

  # Pass the KMS key for CloudWatch log encryption
  kms_key_arn = module.security.kms_key_arn

  # Pass ECS cluster and service names for metric dimensions
  # WHY: Alarms need to reference the specific ECS cluster and service
  ecs_cluster_name = module.compute.ecs_cluster_name
  ecs_service_name = module.compute.ecs_service_name

  # Pass ALB and target group ARN suffixes for ALB metric dimensions
  # WHY: ALB metrics use ARN suffixes as dimension values
  alb_arn_suffix         = module.compute.alb_arn_suffix
  target_group_arn_suffix = module.compute.target_group_arn_suffix

  # Pass email addresses for alarm notifications
  # WHY: Dev team members receive alarm emails for monitoring
  alert_emails = var.alert_emails

  # Set log retention to 30 days for dev (cost savings)
  # WHY: Dev logs are rarely needed beyond 30 days
  log_retention_days = 30

  # Apply tags
  tags = {
    # Cost center
    CostCenter = "engineering-dev"
  }
}

# =============================================================================
# OUTPUTS - Key values from the dev environment deployment
# =============================================================================

# Output the VPC ID for reference
output "vpc_id" {
  # Document the output purpose
  description = "The VPC ID of the dev environment network"
  # Reference from the networking module
  value = module.networking.vpc_id
}

# Output the ALB DNS name for accessing the ECTP API
output "alb_dns_name" {
  # Document how to access the application
  description = "The ALB DNS name - use this URL to access the ECTP API in dev"
  # Reference from the compute module
  value = module.compute.alb_dns_name
}

# Output the RDS endpoint for database connections
output "db_endpoint" {
  # Document the database endpoint
  description = "The RDS PostgreSQL endpoint for database connections"
  # Reference from the database module
  value = module.database.db_endpoint
}

# Output the ECS cluster name for CLI operations
output "ecs_cluster_name" {
  # Document usage for ECS CLI commands
  description = "The ECS cluster name for AWS CLI operations (ecs describe-services, etc.)"
  # Reference from the compute module
  value = module.compute.ecs_cluster_name
}

# Output the ECS service name for deployment operations
output "ecs_service_name" {
  # Document usage for deployment commands
  description = "The ECS service name for deployment operations (update-service, etc.)"
  # Reference from the compute module
  value = module.compute.ecs_service_name
}

# Output the CloudWatch dashboard URL for monitoring
output "monitoring_dashboard_url" {
  # Document the monitoring dashboard access
  description = "URL to the CloudWatch monitoring dashboard for the dev environment"
  # Reference from the monitoring module
  value = module.monitoring.dashboard_url
}

# Output the Secrets Manager secret ARN for reference
output "db_secret_arn" {
  # Document the secret ARN for operational use
  description = "ARN of the Secrets Manager secret containing database credentials"
  # Reference from the security module
  value = module.security.db_secret_arn
}

# Output the KMS key ARN for reference
output "kms_key_arn" {
  # Document the KMS key ARN
  description = "ARN of the KMS key used for encryption across all ECTP services"
  # Reference from the security module
  value = module.security.kms_key_arn
}

# Output the SNS topic ARN for additional subscriptions
output "sns_topic_arn" {
  # Document the SNS topic for additional integrations
  description = "ARN of the SNS topic for alarm notifications (add Slack, PagerDuty, etc.)"
  # Reference from the monitoring module
  value = module.monitoring.sns_topic_arn
}
