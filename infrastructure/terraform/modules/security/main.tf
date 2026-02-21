# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Security Module
# =============================================================================
# Author: Gopi Krishna Vajrala
# Description: Provisions security infrastructure including KMS encryption keys,
#              IAM roles for ECS tasks, Secrets Manager for database credentials,
#              and tiered security groups for ALB, application, and database layers.
#
# Architecture Overview:
#   - KMS Key: Customer-managed encryption key for data at rest
#   - IAM Roles: ECS task execution role and ECS task role (least privilege)
#   - Secrets Manager: Stores and rotates database credentials securely
#   - Security Groups: Three-tier model (ALB -> App -> DB) with least privilege
#
# Security Principles:
#   - Defense in depth: Multiple layers of security controls
#   - Least privilege: Each role/group has only the permissions needed
#   - Encryption everywhere: KMS for data at rest, SSL/TLS for data in transit
#   - Secret management: No hardcoded credentials, all via Secrets Manager
#   - Audit trail: CloudTrail logs all KMS and IAM operations
#
# FERPA/Compliance:
#   - Customer-managed KMS key provides granular access control and audit trail
#   - Secrets Manager enables automatic rotation of database credentials
#   - Security groups enforce network-level isolation between tiers
#   - IAM roles follow least privilege for FERPA data access controls
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration Block
# -----------------------------------------------------------------------------
# Declares required provider versions for reproducible infrastructure builds.
# Version pinning prevents unexpected breaking changes from provider updates.
# -----------------------------------------------------------------------------
terraform {
  # Require Terraform 1.5+ for module features and security improvements
  required_version = ">= 1.5.0"

  # Define required providers with version constraints
  required_providers {
    # AWS provider for all security resource provisioning
    aws = {
      # Use the official HashiCorp AWS provider from the registry
      source = "hashicorp/aws"
      # Pin to version 5.x for stability while getting patch updates
      version = "~> 5.0"
    }
    # Random provider for generating secure database passwords
    random = {
      # Use the official HashiCorp random provider
      source = "hashicorp/random"
      # Pin to version 3.x for consistent random value generation
      version = "~> 3.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

# Retrieve the current AWS account ID for constructing IAM policy ARNs
# WHY: Account-scoped ARNs ensure IAM policies reference the correct account
# SECURITY: Using data source avoids hardcoding account IDs in policies
data "aws_caller_identity" "current" {}

# Retrieve the current AWS region for constructing service-specific ARNs
# WHY: Region-scoped ARNs ensure resources are referenced in the correct region
data "aws_region" "current" {}

# -----------------------------------------------------------------------------
# Input Variables - Parameters passed from the calling environment
# -----------------------------------------------------------------------------

# The deployment environment name for resource naming and policy decisions
variable "environment" {
  # Documents purpose: controls naming, key policies, and rotation schedules
  description = "The deployment environment name (dev, staging, prod) - affects key policies and secret rotation"
  # Enforce string type for the environment identifier
  type = string

  # Validate only known environment names are accepted
  validation {
    # Only allow predefined environment values to prevent misconfigurations
    condition     = contains(["dev", "staging", "prod"], var.environment)
    # Clear error message for invalid environment values
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

# The project name prefix for consistent resource naming
variable "project_name" {
  # Documents purpose as naming prefix used across all resources
  description = "The project name used as a prefix for all resource names and IAM policies"
  # Enforce string type
  type = string
  # Default to ectp for the Enterprise Cloud Transformation Platform
  default = "ectp"
}

# The VPC ID where security groups will be created
variable "vpc_id" {
  # Documents that security groups are VPC-scoped resources
  description = "The ID of the VPC where security groups will be created (SGs are VPC-scoped)"
  # Enforce string type
  type = string
}

# The VPC CIDR block for internal traffic rules in security groups
variable "vpc_cidr" {
  # Documents usage in security group rules for VPC-internal traffic
  description = "The VPC CIDR block used in security group rules to allow VPC-internal traffic"
  # Enforce string type
  type = string
}

# The container port that the ECS application listens on
variable "container_port" {
  # Documents the default port and its usage in security group rules
  description = "The port the application container listens on (used in app-tier SG ingress rules)"
  # Enforce number type
  type = number
  # Default to 8080 as the standard non-privileged HTTP port
  default = 8080
}

# The database port for PostgreSQL security group rules
variable "db_port" {
  # Documents the PostgreSQL default port used in data-tier SG rules
  description = "The PostgreSQL database port (used in database-tier SG ingress rules)"
  # Enforce number type
  type = number
  # Default to 5432 which is the PostgreSQL standard port
  default = 5432
}

# The database name stored in the Secrets Manager secret
variable "db_name" {
  # Documents that this value is included in the Secrets Manager JSON
  description = "The database name stored in the Secrets Manager secret for application retrieval"
  # Enforce string type
  type = string
  # Default to ectp_db as the standard database name
  default = "ectp_db"
}

# The database master username stored in the Secrets Manager secret
variable "db_username" {
  # Documents that this is stored in Secrets Manager alongside the password
  description = "The database master username stored in the Secrets Manager secret"
  # Enforce string type
  type = string
  # Default to ectp_admin as the descriptive admin username
  default = "ectp_admin"
}

# Tags to apply to all resources for cost tracking and governance
variable "tags" {
  # Documents the tagging strategy
  description = "Map of tags applied to all resources for cost allocation and compliance tracking"
  # Enforce map of strings type
  type = map(string)
  # Default to empty map; merged with module-level default tags
  default = {}
}

# -----------------------------------------------------------------------------
# Local Values - Computed values used throughout this module
# -----------------------------------------------------------------------------
locals {
  # Construct a consistent name prefix from project name and environment
  # Format: "ectp-dev", "ectp-staging", "ectp-prod"
  name_prefix = "${var.project_name}-${var.environment}"

  # The AWS account ID retrieved from the data source
  # WHY: Used in IAM policies and KMS key policies for account-scoped access
  account_id = data.aws_caller_identity.current.account_id

  # The AWS region name retrieved from the data source
  # WHY: Used in ARN construction for region-specific resources
  region = data.aws_region.current.name

  # Merge user-provided tags with module-default tags for governance
  common_tags = merge(
    # Include any tags passed from the calling module or environment
    var.tags,
    {
      # Tag identifying which Terraform module provisioned this resource
      Module = "security"
      # Tag identifying the deployment environment for filtering
      Environment = var.environment
      # Tag identifying the project for cost allocation
      Project = var.project_name
      # Tag indicating this resource is managed by Terraform
      ManagedBy = "terraform"
      # Tag identifying the original author for accountability
      Author = "Gopi Krishna Vajrala"
    }
  )
}

# =============================================================================
# KMS KEY FOR ENCRYPTION AT REST
# =============================================================================
# WHAT: A customer-managed KMS key used to encrypt data at rest across all
#       ECTP services: RDS storage, EBS volumes, S3 objects, CloudWatch Logs,
#       Secrets Manager secrets, and SNS messages.
# WHY: Customer-managed keys provide:
#      1. Granular access control via key policy (who can encrypt/decrypt)
#      2. Automatic annual key rotation for security best practices
#      3. CloudTrail audit trail of all encryption/decryption operations
#      4. Ability to revoke access by disabling or deleting the key
#      5. Cross-service encryption with a single managed key
# SECURITY: The key policy follows least privilege, granting:
#           - Root account: Full key administration (for break-glass scenarios)
#           - IAM policies: Delegated access control via IAM (normal operation)
#           - RDS/CloudWatch: Service-specific grant for encryption operations
# COMPLIANCE: FERPA requires encryption of student data at rest; a customer-managed
#             key satisfies this requirement with full audit trail capability.
# =============================================================================
resource "aws_kms_key" "main" {
  # Provide a detailed description for key identification in the KMS console
  # WHY: Descriptions help operators understand which key to use for which service
  description = "Customer-managed KMS key for ${local.name_prefix} - encrypts RDS, CloudWatch Logs, Secrets Manager, and S3"

  # Enable automatic annual key rotation for security
  # WHY: Key rotation limits the amount of data encrypted with a single key version,
  #      reducing the impact if a key is ever compromised
  # SECURITY: AWS handles rotation transparently; old key versions remain available
  #           for decrypting previously encrypted data
  enable_key_rotation = true

  # Set the deletion waiting period to 30 days (maximum) for safety
  # WHY: Once a KMS key is deleted, ALL data encrypted with it becomes
  #      permanently unrecoverable. The 30-day window allows time to
  #      realize the mistake and cancel the deletion.
  # SECURITY: This is a critical safety net; accidental key deletion
  #           would be catastrophic for the entire platform
  deletion_window_in_days = 30

  # Set the key usage to ENCRYPT_DECRYPT for symmetric encryption
  # WHY: Symmetric encryption is used for data at rest (RDS, S3, CloudWatch)
  #      Asymmetric keys are used for signing, not bulk data encryption
  key_usage = "ENCRYPT_DECRYPT"

  # Use symmetric encryption for all data-at-rest use cases
  # WHY: SYMMETRIC_DEFAULT uses AES-256-GCM which provides both confidentiality
  #      and integrity protection for encrypted data
  customer_master_key_spec = "SYMMETRIC_DEFAULT"

  # Enable the key for immediate use after creation
  # WHY: A disabled key cannot encrypt or decrypt data; enabling ensures
  #      dependent resources (RDS, CloudWatch) can use it immediately
  is_enabled = true

  # Define the key policy controlling who can manage and use the key
  # WHY: The key policy is the primary access control mechanism for KMS keys
  #      It works in conjunction with IAM policies for defense in depth
  # SECURITY: The policy grants:
  #           1. Root account: Full administration for break-glass access
  #           2. IAM delegation: Allows IAM policies to grant key access
  #           3. Service principals: RDS and CloudWatch can use the key
  policy = jsonencode({
    # Use the current IAM policy language version
    Version = "2012-10-17"
    # Define the access control statements
    Statement = [
      {
        # Statement ID for identification in policy analysis
        Sid = "EnableRootAccountAccess"
        # Allow the specified actions
        Effect = "Allow"
        # Grant access to the root account (full administration)
        # WHY: Root access ensures the key can always be managed even if
        #      IAM roles/users are accidentally deleted or misconfigured
        Principal = {
          # The root principal of the AWS account
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        # Grant all KMS actions for full key administration
        # WHY: Root needs full access for break-glass scenarios
        Action = "kms:*"
        # Apply to this specific KMS key
        Resource = "*"
      },
      {
        # Statement allowing IAM policies to delegate key access
        Sid = "AllowIAMPolicyDelegation"
        # Allow the specified actions
        Effect = "Allow"
        # Grant access to all principals in this account (filtered by IAM policies)
        Principal = {
          # Any IAM principal in this account can be granted access via IAM policy
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        # Specific KMS actions needed for encryption/decryption operations
        Action = [
          # Allow encrypting data with this key
          "kms:Encrypt",
          # Allow decrypting data encrypted with this key
          "kms:Decrypt",
          # Allow re-encrypting data (e.g., during key rotation)
          "kms:ReEncrypt*",
          # Allow generating data keys for envelope encryption
          "kms:GenerateDataKey*",
          # Allow describing the key metadata
          "kms:DescribeKey",
          # Allow creating grants for AWS service integration
          "kms:CreateGrant",
          # Allow listing grants on this key
          "kms:ListGrants",
          # Allow revoking grants
          "kms:RevokeGrant"
        ]
        # Apply to this specific KMS key
        Resource = "*"
      },
      {
        # Statement allowing CloudWatch Logs to use the key for log encryption
        Sid = "AllowCloudWatchLogs"
        # Allow the specified actions
        Effect = "Allow"
        # Grant access to the CloudWatch Logs service principal
        Principal = {
          # CloudWatch Logs service needs direct key access for log encryption
          Service = "logs.${local.region}.amazonaws.com"
        }
        # CloudWatch Logs needs these specific KMS actions
        Action = [
          # Encrypt log data as it is written
          "kms:Encrypt",
          # Decrypt log data when read by authorized principals
          "kms:Decrypt",
          # Re-encrypt during key rotation
          "kms:ReEncrypt*",
          # Generate data keys for envelope encryption of log data
          "kms:GenerateDataKey*",
          # Describe key to verify key state before operations
          "kms:DescribeKey"
        ]
        # Apply to this specific KMS key
        Resource = "*"
        # Condition restricting which CloudWatch log groups can use this key
        Condition = {
          ArnLike = {
            # Only allow log groups in this account and region to use the key
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:*"
          }
        }
      }
    ]
  })

  # Apply common tags plus a descriptive Name tag
  tags = merge(
    # Include all common tags for governance
    local.common_tags,
    {
      # Name tag for identification in the KMS console
      Name = "${local.name_prefix}-kms-key"
    }
  )
}

# Create a human-readable alias for the KMS key
# WHY: Aliases provide a friendly name (alias/ectp-dev-key) instead of the
#      UUID key ID, making it easier to reference in CLI commands and documentation
resource "aws_kms_alias" "main" {
  # Set the alias name with the standard naming convention
  # WHY: The alias/ prefix is required by AWS for KMS key aliases
  name = "alias/${local.name_prefix}-key"

  # Reference the KMS key created above
  # WHY: Associates this human-readable alias with the actual KMS key
  target_key_id = aws_kms_key.main.key_id
}

# =============================================================================
# IAM ROLE - ECS TASK EXECUTION ROLE
# =============================================================================
# WHAT: The IAM role assumed by the ECS agent (not the application) to perform
#       infrastructure operations: pulling container images, writing logs,
#       and fetching secrets from Secrets Manager.
# WHY: The execution role is separate from the task role to follow the
#      principle of least privilege. The ECS agent needs different permissions
#      than the application running inside the container.
# SECURITY:
#   - Trust policy restricts assumption to only the ECS Tasks service
#   - Permissions are scoped to only ECR, CloudWatch Logs, and Secrets Manager
#   - No wildcard actions; each permission is explicitly listed
#   - KMS decrypt permission is scoped to the project KMS key only
# =============================================================================
resource "aws_iam_role" "ecs_task_execution" {
  # Name the role with the standard naming convention
  # WHY: Including environment prevents naming conflicts in shared accounts
  name = "${local.name_prefix}-ecs-task-execution-role"

  # Trust policy defining which service can assume this role
  # WHY: Only the ECS Tasks service should be able to assume this role
  # SECURITY: Restricting the principal prevents unauthorized role assumption
  assume_role_policy = jsonencode({
    # Use the current IAM policy language version
    Version = "2012-10-17"
    # Define the trust relationship
    Statement = [
      {
        # Allow the AssumeRole action
        Action = "sts:AssumeRole"
        # Permit this action
        Effect = "Allow"
        # Restrict to the ECS Tasks service principal only
        Principal = {
          # Only ECS Tasks can assume this role
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  # Apply common tags for governance
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for IAM console identification
      Name = "${local.name_prefix}-ecs-task-execution-role"
    }
  )
}

# Attach the AWS-managed ECS task execution policy
# WHY: This managed policy provides the baseline permissions for ECS task execution:
#      ECR image pull, CloudWatch Logs write, and basic ECS operations
# SECURITY: The managed policy is maintained by AWS and follows best practices
resource "aws_iam_role_policy_attachment" "ecs_task_execution_base" {
  # Attach to our custom execution role
  role = aws_iam_role.ecs_task_execution.name
  # Use the AWS-managed policy for ECS task execution
  # WHY: Provides ecr:GetDownloadUrlForLayer, ecr:BatchGetImage, logs:CreateLogStream, etc.
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Custom policy for Secrets Manager access (not in the managed policy)
# WHY: The managed ECS execution policy does not include Secrets Manager access.
#      We need this for injecting database credentials into containers at startup.
# SECURITY: Scoped to only the specific secrets and KMS key needed by this project
resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  # Name the policy for identification
  name = "${local.name_prefix}-ecs-execution-secrets-policy"
  # Attach to the execution role
  role = aws_iam_role.ecs_task_execution.id

  # Define the permissions for Secrets Manager and KMS access
  policy = jsonencode({
    # Use the current IAM policy language version
    Version = "2012-10-17"
    # Define the permission statements
    Statement = [
      {
        # Statement for Secrets Manager read access
        Sid = "AllowSecretsManagerRead"
        # Allow the specified actions
        Effect = "Allow"
        # Only allow reading secret values (not creating, updating, or deleting)
        Action = [
          # Retrieve the current secret value for container injection
          "secretsmanager:GetSecretValue"
        ]
        # Scope to only the database secret created by this module
        # WHY: Least privilege - the execution role should only access
        #      the specific secrets needed for the ECTP application
        Resource = [
          # Reference the DB credentials secret ARN
          aws_secretsmanager_secret.db_credentials.arn
        ]
      },
      {
        # Statement for KMS decrypt access (needed to decrypt secrets)
        Sid = "AllowKMSDecrypt"
        # Allow the specified action
        Effect = "Allow"
        # Only allow decryption (not encryption, key management, etc.)
        Action = [
          # Decrypt the secret value using the KMS key
          "kms:Decrypt"
        ]
        # Scope to only the project KMS key
        # WHY: The execution role should only decrypt using the project key,
        #      not any arbitrary KMS key in the account
        Resource = [
          # Reference the KMS key ARN created above
          aws_kms_key.main.arn
        ]
      }
    ]
  })
}

# =============================================================================
# IAM ROLE - ECS TASK ROLE
# =============================================================================
# WHAT: The IAM role assumed by the application code running inside the
#       container. This role determines what AWS services the ECTP FastAPI
#       application can access at runtime.
# WHY: Separate from the execution role because the application has different
#      permission needs than the ECS agent. The task role should only grant
#      access to services the application code actually calls.
# SECURITY:
#   - Follows strict least privilege for application-level access
#   - No wildcard resources; each permission targets specific ARNs
#   - Permissions can be expanded as the application needs grow
#   - CloudTrail logs all API calls made with this role
# =============================================================================
resource "aws_iam_role" "ecs_task" {
  # Name the role with the standard naming convention
  # WHY: Clear naming distinguishes this from the execution role
  name = "${local.name_prefix}-ecs-task-role"

  # Trust policy allowing only ECS Tasks to assume this role
  # WHY: The application container assumes this role automatically via
  #      the ECS task definition configuration
  # SECURITY: Only ECS Tasks service can assume this role
  assume_role_policy = jsonencode({
    # Use the current IAM policy language version
    Version = "2012-10-17"
    # Define the trust relationship
    Statement = [
      {
        # Allow the AssumeRole action
        Action = "sts:AssumeRole"
        # Permit this action
        Effect = "Allow"
        # Restrict to ECS Tasks service principal
        Principal = {
          # Only ECS Tasks can assume this role
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  # Apply common tags for governance
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-ecs-task-role"
    }
  )
}

# Custom policy for application-level AWS service access
# WHY: Grants the running application permissions to interact with
#      specific AWS services (S3, SES, CloudWatch, etc.)
# SECURITY: Each action and resource is explicitly listed; no wildcards
resource "aws_iam_role_policy" "ecs_task_app_permissions" {
  # Name the policy for identification
  name = "${local.name_prefix}-ecs-task-app-policy"
  # Attach to the task role (not the execution role)
  role = aws_iam_role.ecs_task.id

  # Define application-level permissions
  policy = jsonencode({
    # Use the current IAM policy language version
    Version = "2012-10-17"
    # Define the permission statements for each service
    Statement = [
      {
        # Statement for CloudWatch Metrics publishing
        Sid = "AllowCloudWatchMetrics"
        # Allow the specified action
        Effect = "Allow"
        # Allow publishing custom application metrics to CloudWatch
        Action = [
          # Publish custom metrics (API response times, error counts, etc.)
          "cloudwatch:PutMetricData"
        ]
        # CloudWatch PutMetricData does not support resource-level restrictions
        Resource = "*"
        # Condition to restrict metric namespace to the project
        Condition = {
          StringEquals = {
            # Only allow publishing to the project-specific metric namespace
            "cloudwatch:namespace" = "ECTP/${var.environment}"
          }
        }
      },
      {
        # Statement for Secrets Manager read access at runtime
        Sid = "AllowSecretsManagerReadRuntime"
        # Allow the specified actions
        Effect = "Allow"
        # Allow reading secrets for runtime configuration refresh
        Action = [
          # Retrieve secret values for database credentials
          "secretsmanager:GetSecretValue",
          # Describe secret metadata for rotation status checking
          "secretsmanager:DescribeSecret"
        ]
        # Scope to only the database credentials secret
        Resource = [
          # Only the DB credentials secret, not all secrets in the account
          aws_secretsmanager_secret.db_credentials.arn
        ]
      },
      {
        # Statement for KMS decrypt access for secret decryption
        Sid = "AllowKMSDecryptRuntime"
        # Allow decryption only
        Effect = "Allow"
        # Allow decrypting secrets and encrypted configuration
        Action = [
          # Decrypt encrypted secret values
          "kms:Decrypt",
          # Generate data keys for client-side encryption if needed
          "kms:GenerateDataKey"
        ]
        # Scope to only the project KMS key
        Resource = [
          # Only the project KMS key, not any key in the account
          aws_kms_key.main.arn
        ]
      },
      {
        # Statement for ECS Execute Command support (debugging)
        Sid = "AllowSSMForExecuteCommand"
        # Allow the specified actions
        Effect = "Allow"
        # Allow SSM actions required for ECS Execute Command
        Action = [
          # Create SSM control channel for execute command sessions
          "ssmmessages:CreateControlChannel",
          # Create SSM data channel for session I/O
          "ssmmessages:CreateDataChannel",
          # Open SSM control channel for bidirectional communication
          "ssmmessages:OpenControlChannel",
          # Open SSM data channel for session data transfer
          "ssmmessages:OpenDataChannel"
        ]
        # SSM messages do not support resource-level restrictions
        Resource = "*"
      }
    ]
  })
}

# =============================================================================
# SECRETS MANAGER - DATABASE CREDENTIALS
# =============================================================================
# WHAT: An AWS Secrets Manager secret that stores the database master password
#       and connection details as a JSON object.
# WHY: Secrets Manager provides:
#      1. Encrypted storage for sensitive credentials (KMS-encrypted)
#      2. Automatic rotation capability (rotate passwords without downtime)
#      3. Fine-grained IAM access control (who can read the secret)
#      4. CloudTrail audit trail (who accessed the secret and when)
#      5. Version management for secret values (rollback capability)
# SECURITY:
#   - Secret value is encrypted with the project KMS key
#   - Access is restricted via IAM policies on the execution/task roles
#   - Secret versions enable safe rotation and rollback
#   - CloudTrail logs every GetSecretValue call for audit
# COMPLIANCE: FERPA requires that database credentials be stored securely
#             and access be logged. Secrets Manager satisfies both requirements.
# =============================================================================

# Generate a random password for the database master user
# WHY: Programmatic password generation ensures strong, unique passwords
#      without human-generated weak passwords or password reuse
# SECURITY: 32 characters with mixed case, numbers, and special characters
#           provides high entropy resistance to brute-force attacks
resource "random_password" "db_master" {
  # Set password length to 32 characters for high entropy
  # WHY: Longer passwords exponentially increase brute-force difficulty
  length = 32

  # Include special characters for additional complexity
  # WHY: Special characters increase the character space for brute-force resistance
  special = true

  # Exclude characters that cause issues in connection strings and shell commands
  # WHY: Characters like @, /, \, ", and ' can break connection string parsing
  #      or cause shell escaping issues in scripts and container environments
  override_special = "!#$%&*()-_=+[]{}<>:?"

  # Do not use numeric-only prefix to avoid interpretation issues
  # WHY: Some tools may truncate or misinterpret passwords starting with numbers
  numeric = true

  # Include uppercase letters for complexity
  # WHY: Mixed case increases the character space for password strength
  upper = true

  # Include lowercase letters for complexity
  # WHY: Required for most password policies
  lower = true

  # Minimum number of special characters required
  # WHY: Ensures the generated password meets complexity requirements
  min_special = 2

  # Minimum number of uppercase characters
  # WHY: Ensures mixed case for password strength
  min_upper = 2

  # Minimum number of lowercase characters
  # WHY: Ensures variety in character types
  min_lower = 2

  # Minimum number of numeric characters
  # WHY: Ensures digits are included for password complexity
  min_numeric = 2
}

# Create the Secrets Manager secret (metadata container)
# WHY: The secret resource is the metadata wrapper; the actual secret value
#      is stored separately in a secret version (below)
resource "aws_secretsmanager_secret" "db_credentials" {
  # Name the secret with a hierarchical path for organization
  # WHY: The ectp/{env}/db/credentials path makes secrets easy to find
  #      and enables IAM policies scoped to specific paths
  name = "${local.name_prefix}/db/credentials"

  # Provide a detailed description for operators
  # WHY: Descriptions help during audits and troubleshooting
  description = "Database credentials for ${local.name_prefix} PostgreSQL instance - contains username, password, host, port, and database name"

  # Encrypt the secret with the project KMS key
  # WHY: Customer-managed KMS key provides:
  #      1. Audit trail of all secret access via CloudTrail
  #      2. Fine-grained key policy for access control
  #      3. Key rotation independent of secret rotation
  kms_key_id = aws_kms_key.main.arn

  # Set the recovery window to 7 days for safety
  # WHY: If a secret is accidentally deleted, it can be recovered within
  #      this window. After the window, the secret is permanently deleted.
  # SECURITY: 7 days balances between quick cleanup and safety
  recovery_window_in_days = var.environment == "prod" ? 30 : 7

  # Apply common tags for governance
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-db-credentials"
      # Data classification tag for compliance scanning
      DataClassification = "confidential"
    }
  )
}

# Store the initial secret value with database connection details
# WHY: The secret version contains the actual JSON payload with all
#      connection details needed by the application
resource "aws_secretsmanager_secret_version" "db_credentials" {
  # Associate this version with the secret created above
  # WHY: Secret versions are stored independently from the secret metadata
  secret_id = aws_secretsmanager_secret.db_credentials.id

  # Store the database credentials as a JSON string
  # WHY: JSON format allows storing multiple related values in a single secret:
  #      username, password, host, port, and database name
  # SECURITY: All values are encrypted at rest with the KMS key
  secret_string = jsonencode({
    # The database master username
    username = var.db_username
    # The generated random password (32 characters, high entropy)
    password = random_password.db_master.result
    # Database engine identifier for programmatic access
    engine = "postgres"
    # Database port (will be updated when RDS instance is created)
    port = var.db_port
    # Database name for connection string construction
    dbname = var.db_name
  })
}

# =============================================================================
# SECURITY GROUP - ALB TIER
# =============================================================================
# WHAT: Security group for the Application Load Balancer controlling inbound
#       internet traffic and outbound traffic to the application tier.
# WHY: The ALB is the single entry point from the internet. This security group
#      ensures only HTTP (80) and HTTPS (443) traffic can reach the ALB,
#      and only traffic to the container port can exit toward the app tier.
# SECURITY:
#   - Inbound: Only ports 80 (redirect to HTTPS) and 443 (HTTPS) from anywhere
#   - Outbound: Only the container port to the app security group
#   - No other ports or protocols are allowed
#   - This is the outermost security boundary for the ECTP platform
# =============================================================================
resource "aws_security_group" "alb" {
  # Name the security group with a descriptive identifier
  # WHY: Clear naming helps operators identify the purpose during audits
  name = "${local.name_prefix}-alb-sg"

  # Provide a detailed description for documentation
  # WHY: Descriptions appear in the AWS Console and help during security reviews
  description = "Security group for ALB - allows HTTP/HTTPS inbound from internet, outbound to app tier only"

  # Associate with the VPC where the ALB will be deployed
  # WHY: Security groups are VPC-scoped and must match the ALB's VPC
  vpc_id = var.vpc_id

  # Apply common tags for governance
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for AWS Console identification
      Name = "${local.name_prefix}-alb-sg"
      # Tier tag for automated security policy enforcement
      Tier = "public"
    }
  )
}

# ALB Ingress Rule: Allow HTTPS (port 443) from anywhere
# WHY: The ALB must accept HTTPS traffic from the public internet so that
#      students, faculty, and staff can access the ECTP platform
# SECURITY: Only port 443 with TLS encryption is the primary entry point
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  # Attach to the ALB security group
  security_group_id = aws_security_group.alb.id
  # Allow traffic from any IPv4 source (public internet)
  cidr_ipv4 = "0.0.0.0/0"
  # Allow only port 443 (HTTPS) for encrypted traffic
  from_port = 443
  # Single port (not a range)
  to_port = 443
  # Use TCP protocol for HTTPS connections
  ip_protocol = "tcp"
  # Describe the rule for audit documentation
  description = "Allow HTTPS inbound from the internet for user access"
  # Apply tags for governance
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-https-ingress" })
}

# ALB Ingress Rule: Allow HTTP (port 80) for redirect to HTTPS
# WHY: Users may type http:// in their browser; the ALB redirects them to HTTPS
# SECURITY: HTTP traffic is never processed; it is immediately redirected to HTTPS
resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  # Attach to the ALB security group
  security_group_id = aws_security_group.alb.id
  # Allow traffic from any IPv4 source
  cidr_ipv4 = "0.0.0.0/0"
  # Allow only port 80 (HTTP) for the HTTPS redirect
  from_port = 80
  # Single port
  to_port = 80
  # Use TCP protocol for HTTP connections
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow HTTP inbound from the internet (redirected to HTTPS by ALB)"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-http-ingress" })
}

# ALB Egress Rule: Allow traffic to the application tier on the container port
# WHY: The ALB forwards decrypted traffic to ECS Fargate tasks on the container port
# SECURITY: Egress is restricted to only the container port and only to the app SG
resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  # Attach to the ALB security group
  security_group_id = aws_security_group.alb.id
  # Allow traffic to the app security group (not a CIDR, more precise)
  referenced_security_group_id = aws_security_group.app.id
  # Allow only the container port for application traffic
  from_port = var.container_port
  # Single port
  to_port = var.container_port
  # Use TCP protocol
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow outbound to app tier ECS tasks on container port ${var.container_port}"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-to-app-egress" })
}

# ALB Egress Rule: Allow HTTPS outbound for health checks and AWS API calls
# WHY: The ALB needs outbound HTTPS access for:
#      1. Health check responses from targets
#      2. AWS API calls for authentication/authorization
# SECURITY: Allows HTTPS (443) outbound to any destination
resource "aws_vpc_security_group_egress_rule" "alb_https_out" {
  # Attach to the ALB security group
  security_group_id = aws_security_group.alb.id
  # Allow traffic to any destination
  cidr_ipv4 = "0.0.0.0/0"
  # Allow HTTPS port for outbound API calls
  from_port = 443
  # Single port
  to_port = 443
  # Use TCP protocol
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow HTTPS outbound for AWS API calls and certificate validation"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-https-egress" })
}

# =============================================================================
# SECURITY GROUP - APPLICATION TIER
# =============================================================================
# WHAT: Security group for ECS Fargate tasks running the ECTP API application.
# WHY: Controls which traffic can reach the application containers and what
#      outbound connections the containers can make.
# SECURITY:
#   - Inbound: Only from the ALB security group on the container port
#   - Outbound: To the database on port 5432, and HTTPS for AWS API calls
#   - No direct internet inbound access (containers are in private subnets)
#   - This is the middle tier in the three-tier security model
# =============================================================================
resource "aws_security_group" "app" {
  # Name the security group descriptively
  # WHY: Clear naming aids security reviews and incident response
  name = "${local.name_prefix}-app-sg"

  # Provide a detailed description
  # WHY: Documentation in the SG description helps during audits
  description = "Security group for ECS Fargate tasks - allows inbound from ALB only, outbound to DB and AWS services"

  # Associate with the same VPC as the ECS tasks
  # WHY: SGs must be in the same VPC as the resources they protect
  vpc_id = var.vpc_id

  # Apply common tags for governance
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-app-sg"
      # Tier tag for automation
      Tier = "private-app"
    }
  )
}

# App Ingress Rule: Allow traffic from ALB on the container port
# WHY: ECS tasks receive traffic only from the ALB after TLS termination
# SECURITY: Using SG reference ensures only the ALB can send traffic, not
#           any resource with a matching IP address
resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  # Attach to the app security group
  security_group_id = aws_security_group.app.id
  # Allow traffic from the ALB security group specifically
  referenced_security_group_id = aws_security_group.alb.id
  # Allow only the container port
  from_port = var.container_port
  # Single port
  to_port = var.container_port
  # Use TCP protocol
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow inbound from ALB on container port ${var.container_port}"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-app-from-alb-ingress" })
}

# App Egress Rule: Allow traffic to the database on PostgreSQL port
# WHY: The application needs to connect to PostgreSQL for data operations
# SECURITY: Restricted to only port 5432 and only to the DB security group
resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  # Attach to the app security group
  security_group_id = aws_security_group.app.id
  # Allow traffic to the database security group
  referenced_security_group_id = aws_security_group.db.id
  # Allow only the PostgreSQL port
  from_port = var.db_port
  # Single port
  to_port = var.db_port
  # Use TCP protocol
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow outbound to database tier on PostgreSQL port ${var.db_port}"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-app-to-db-egress" })
}

# App Egress Rule: Allow HTTPS outbound for AWS API calls and external services
# WHY: ECS tasks need HTTPS access for:
#      1. Pulling container images from ECR
#      2. Sending logs to CloudWatch
#      3. Fetching secrets from Secrets Manager
#      4. Calling external APIs (SIS, LMS integrations)
# SECURITY: Port 443 to any destination is required for AWS service endpoints
resource "aws_vpc_security_group_egress_rule" "app_https_out" {
  # Attach to the app security group
  security_group_id = aws_security_group.app.id
  # Allow traffic to any destination for AWS API calls
  cidr_ipv4 = "0.0.0.0/0"
  # Allow HTTPS port for encrypted API communication
  from_port = 443
  # Single port
  to_port = 443
  # Use TCP protocol
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow HTTPS outbound for AWS API calls (ECR, CloudWatch, Secrets Manager) and external services"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-app-https-egress" })
}

# App Egress Rule: Allow DNS resolution
# WHY: ECS tasks need DNS resolution to resolve AWS service endpoints,
#      database hostnames, and external service domains
# SECURITY: DNS (port 53) is required for basic network functionality
resource "aws_vpc_security_group_egress_rule" "app_dns_tcp" {
  # Attach to the app security group
  security_group_id = aws_security_group.app.id
  # Allow DNS traffic to the VPC CIDR (VPC DNS resolver is at VPC+2 address)
  cidr_ipv4 = var.vpc_cidr
  # Allow DNS port
  from_port = 53
  # Single port
  to_port = 53
  # Use TCP protocol for DNS over TCP
  ip_protocol = "tcp"
  # Describe the rule
  description = "Allow DNS resolution via TCP within the VPC"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-app-dns-tcp-egress" })
}

# App Egress Rule: Allow DNS resolution over UDP
# WHY: Most DNS queries use UDP; TCP is used as fallback for large responses
# SECURITY: Restricted to VPC CIDR to use only the VPC DNS resolver
resource "aws_vpc_security_group_egress_rule" "app_dns_udp" {
  # Attach to the app security group
  security_group_id = aws_security_group.app.id
  # Allow DNS traffic to the VPC DNS resolver
  cidr_ipv4 = var.vpc_cidr
  # Allow DNS port
  from_port = 53
  # Single port
  to_port = 53
  # Use UDP protocol for standard DNS queries
  ip_protocol = "udp"
  # Describe the rule
  description = "Allow DNS resolution via UDP within the VPC"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-app-dns-udp-egress" })
}

# =============================================================================
# SECURITY GROUP - DATABASE TIER
# =============================================================================
# WHAT: Security group for the RDS PostgreSQL instance controlling database access.
# WHY: Restricts database access to only the application security group on port 5432.
#      This is the innermost security boundary protecting sensitive data.
# SECURITY:
#   - Inbound: Only from the app security group on port 5432
#   - Outbound: All outbound for RDS managed operations (backups, monitoring)
#   - No public access, no access from ALB or internet
#   - Defense in depth: even if the app tier is compromised, the attacker
#     must have valid database credentials to access data
# =============================================================================
resource "aws_security_group" "db" {
  # Name the security group descriptively
  # WHY: Clear naming for security reviews and compliance audits
  name = "${local.name_prefix}-db-sg"

  # Provide a detailed description
  # WHY: Descriptions document the purpose and access patterns
  description = "Security group for RDS PostgreSQL - allows inbound from app tier only on port 5432"

  # Associate with the VPC
  # WHY: Must match the VPC where the RDS instance is deployed
  vpc_id = var.vpc_id

  # Apply common tags
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-db-sg"
      # Tier tag for automation and compliance scanning
      Tier = "private-data"
      # Data classification for compliance tools
      DataClassification = "confidential"
    }
  )
}

# DB Ingress Rule: Allow PostgreSQL traffic from the application tier
# WHY: Only ECS Fargate tasks in the app tier should connect to the database
# SECURITY: SG-to-SG reference is more secure than CIDR-based rules because
#           it dynamically tracks security group membership
resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  # Attach to the database security group
  security_group_id = aws_security_group.db.id
  # Allow traffic from the application security group
  referenced_security_group_id = aws_security_group.app.id
  # Allow only PostgreSQL port 5432
  from_port = var.db_port
  # Single port
  to_port = var.db_port
  # Use TCP protocol for PostgreSQL connections
  ip_protocol = "tcp"
  # Describe the rule for audit documentation
  description = "Allow PostgreSQL connections from application tier ECS tasks"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-db-from-app-ingress" })
}

# DB Egress Rule: Allow all outbound for RDS managed operations
# WHY: RDS needs outbound connectivity for:
#      1. Sending Enhanced Monitoring metrics to CloudWatch
#      2. Uploading backups and snapshots to S3
#      3. Communicating with AWS service endpoints for patching
# SECURITY: Outbound from RDS is low-risk as the database engine does not
#           initiate arbitrary connections. AWS manages outbound operations.
resource "aws_vpc_security_group_egress_rule" "db_all_outbound" {
  # Attach to the database security group
  security_group_id = aws_security_group.db.id
  # Allow traffic to any destination for AWS managed operations
  cidr_ipv4 = "0.0.0.0/0"
  # Allow all protocols (AWS managed operations use various protocols)
  ip_protocol = "-1"
  # Describe the rule
  description = "Allow all outbound for RDS managed operations (backups, monitoring, patching)"
  # Apply tags
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-db-egress-all" })
}

# =============================================================================
# OUTPUTS - Values exported for use by other modules
# =============================================================================

# Output the KMS key ARN for use by other modules requiring encryption
output "kms_key_arn" {
  # Document the purpose of this output
  description = "ARN of the customer-managed KMS key for encrypting data at rest across all ECTP services"
  # Reference the KMS key ARN
  value = aws_kms_key.main.arn
}

# Output the KMS key ID for services that accept key ID instead of ARN
output "kms_key_id" {
  # Document the key ID output
  description = "The ID of the customer-managed KMS key (UUID format)"
  # Reference the key ID
  value = aws_kms_key.main.key_id
}

# Output the KMS key alias for human-readable references
output "kms_key_alias" {
  # Document the alias output
  description = "The alias of the KMS key (alias/ectp-{env}-key format)"
  # Reference the alias name
  value = aws_kms_alias.main.name
}

# Output the ECS task execution role ARN for the compute module
output "ecs_task_execution_role_arn" {
  # Document the execution role ARN output
  description = "ARN of the ECS task execution role (for image pull, log write, secret fetch)"
  # Reference the execution role ARN
  value = aws_iam_role.ecs_task_execution.arn
}

# Output the ECS task role ARN for the compute module
output "ecs_task_role_arn" {
  # Document the task role ARN output
  description = "ARN of the ECS task role (for application-level AWS API access)"
  # Reference the task role ARN
  value = aws_iam_role.ecs_task.arn
}

# Output the Secrets Manager secret ARN for the database and compute modules
output "db_secret_arn" {
  # Document the secret ARN output
  description = "ARN of the Secrets Manager secret containing database credentials"
  # Reference the secret ARN
  value = aws_secretsmanager_secret.db_credentials.arn
}

# Output the generated database password for the database module
output "db_master_password" {
  # Document that this is sensitive and should not be logged
  description = "The generated master password for the RDS instance (sensitive)"
  # Reference the generated password
  value = random_password.db_master.result
  # Mark as sensitive to prevent display in terraform output
  sensitive = true
}

# Output the ALB security group ID for the compute module
output "alb_security_group_id" {
  # Document the ALB SG output
  description = "Security group ID for the Application Load Balancer"
  # Reference the ALB security group ID
  value = aws_security_group.alb.id
}

# Output the application security group ID for the compute module
output "app_security_group_id" {
  # Document the app SG output
  description = "Security group ID for ECS Fargate application tasks"
  # Reference the app security group ID
  value = aws_security_group.app.id
}

# Output the database security group ID for the database module
output "db_security_group_id" {
  # Document the DB SG output
  description = "Security group ID for the RDS PostgreSQL database instance"
  # Reference the DB security group ID
  value = aws_security_group.db.id
}
