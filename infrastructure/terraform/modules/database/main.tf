# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Database Module
# =============================================================================
# Author: Gopi Krishna Vajrala
# Purpose: Provisions a production-grade Amazon RDS PostgreSQL database for
#          the ECTP platform, with Multi-AZ deployment, encryption at rest
#          and in transit, automated backups, and comprehensive monitoring.
#
# Architecture Overview:
#   - RDS PostgreSQL: Managed relational database service
#   - Multi-AZ: Synchronous standby replica in a different AZ for HA
#   - Encryption: KMS-encrypted storage and enforced SSL/TLS connections
#   - Backups: Automated daily snapshots with configurable retention
#   - Monitoring: Enhanced Monitoring (OS-level) + Performance Insights
#   - Subnet Group: Database placed in private data subnets (no internet)
#   - Parameter Group: Hardened PostgreSQL configuration for security
#
# Why RDS PostgreSQL (not Aurora, DynamoDB, or self-managed):
#   1. PostgreSQL is the FastAPI ecosystem standard (SQLAlchemy, Alembic)
#   2. RDS is fully managed (patching, backups, failover handled by AWS)
#   3. Multi-AZ provides 99.95% SLA (sufficient for Higher Ed)
#   4. Aurora costs 20-40% more and is overkill for ECTP's scale
#   5. DynamoDB is NoSQL and would require significant application changes
#
# Security Implications:
#   - Database is placed in private data subnets with NO public accessibility
#   - Encryption at rest using customer-managed KMS key
#   - Encryption in transit enforced via SSL/TLS (rds.force_ssl = 1)
#   - IAM database authentication available for passwordless access
#   - Automated backups encrypted with the same KMS key
#   - Deletion protection prevents accidental data loss
#   - Enhanced Monitoring detects performance anomalies (potential attacks)
#
# FERPA/Compliance:
#   - Student data at rest is encrypted with a customer-managed KMS key
#   - Network isolation ensures database is unreachable from the internet
#   - Audit logging captures all database connections and queries
#   - Backup retention meets institutional data retention policies
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration Block
# -----------------------------------------------------------------------------
# WHY: Declares provider requirements for reproducible infrastructure builds.
# SECURITY: Version pinning prevents supply-chain attacks from compromised
#           provider updates.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"    # Official HashiCorp AWS provider
      version = "~> 5.0"           # Allow 5.x patches, block 6.0+
    }
    random = {
      source  = "hashicorp/random" # For generating secure random passwords
      version = "~> 3.0"           # Stable version for random resource generation
    }
  }
}

# =============================================================================
# LOCAL VALUES
# =============================================================================
# WHAT: Computed values used throughout the module for consistency.
# WHY: Centralizes naming conventions and avoids repetition.
# =============================================================================
locals {
  # Standard name prefix for all resources.
  name_prefix = "${var.project_name}-${var.environment}"

  # Common tags for all resources in this module.
  common_tags = merge(var.tags, {
    Module      = "database"                    # Identifies this Terraform module
    Project     = var.project_name              # Project for cost allocation
    Environment = var.environment               # Environment tier
    ManagedBy   = "terraform"                   # IaC-managed; do not modify manually
    Author      = "Gopi Krishna Vajrala"        # Original author for accountability
  })
}

# =============================================================================
# RDS SUBNET GROUP
# =============================================================================
# WHAT: A DB Subnet Group that tells RDS which subnets to place the primary
#       and standby database instances in.
# WHY: RDS requires a subnet group with subnets in at least 2 AZs for
#       Multi-AZ deployment. This ensures the primary and standby are in
#       different AZs for fault isolation.
# SECURITY: By using private data subnets (not public or app subnets),
#           the database is placed in the most isolated network tier.
#           These subnets have NACLs that only allow traffic from app subnets.
# ALTERNATIVE: Could use a single-AZ deployment for dev to save costs,
#              but Multi-AZ is recommended even for dev to test failover.
# =============================================================================
resource "aws_db_subnet_group" "main" {
  # Name for the subnet group (used in RDS console).
  name = "${local.name_prefix}-db-subnet-group"

  # Description helps operators understand the purpose in the console.
  description = "ECTP ${var.environment} database subnet group - private data subnets across multiple AZs for RDS Multi-AZ deployment"

  # Associate with private data subnets.
  # WHY: Private data subnets have no direct internet access and are
  #       protected by NACLs that only allow traffic from app subnets.
  # SECURITY: Placing the database in data subnets means an attacker must
  #           compromise the ALB, then the app tier, then breach the data
  #           tier's security groups and NACLs to reach the database.
  subnet_ids = var.private_data_subnet_ids

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-db-subnet-group"
  })
}

# =============================================================================
# RDS PARAMETER GROUP
# =============================================================================
# WHAT: A custom parameter group that configures PostgreSQL server settings
#       with security-hardened values.
# WHY: The default parameter group uses generic settings. A custom group
#       allows us to:
#       1. Force SSL/TLS connections (rds.force_ssl = 1)
#       2. Enable query logging for compliance auditing
#       3. Tune performance parameters for the ECTP workload
#       4. Configure connection limits and timeouts
# SECURITY IMPLICATIONS:
#   - rds.force_ssl = 1: All client connections MUST use SSL/TLS. Unencrypted
#     connections are rejected. This prevents credential sniffing and
#     data interception on the network.
#   - log_connections/log_disconnections: Creates an audit trail of who
#     connected to the database and when.
#   - log_statement = 'ddl': Logs all schema changes for change management.
#   - shared_preload_libraries includes pg_stat_statements for query
#     performance monitoring without exposing query data.
# =============================================================================
resource "aws_db_parameter_group" "main" {
  # Name includes the PostgreSQL family version for clarity.
  # WHY: Parameter groups are version-specific; having the version in
  #       the name prevents confusion when upgrading PostgreSQL versions.
  name = "${local.name_prefix}-pg-params"

  # PostgreSQL family version (must match the engine_version on the instance).
  # WHY: Parameter groups are tied to a PostgreSQL major version.
  #       "postgres15" supports PostgreSQL 15.x versions.
  family = "postgres${var.engine_major_version}"

  # Description for the AWS console.
  description = "ECTP ${var.environment} PostgreSQL parameters - security hardened with forced SSL, audit logging, and performance tuning"

  # --- Security Parameters ---

  # Force all connections to use SSL/TLS encryption.
  # WHAT: When set to 1, PostgreSQL rejects any connection attempt that
  #       does not use SSL/TLS. This is a server-side enforcement.
  # WHY: Encrypts data in transit between the application and database.
  #       Without this, credentials and student data would traverse the
  #       network in plaintext (even within the VPC).
  # SECURITY: This is a CRITICAL security control for FERPA compliance.
  #           Even though traffic is within the VPC, defense-in-depth
  #           requires encryption in transit.
  # ALTERNATIVE: Could set to 0 and rely on application-side SSL config,
  #              but server-side enforcement is more reliable.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  # --- Audit Logging Parameters ---

  # Log all connection attempts (successful and failed).
  # WHAT: Each connection event (open/close) is logged with timestamp,
  #       username, source IP, and database name.
  # WHY: Provides an audit trail for compliance (who accessed the database)
  #       and security monitoring (detecting unauthorized access attempts).
  # SECURITY: Failed connection attempts may indicate brute-force attacks.
  #           Monitoring these logs enables automated alerting.
  parameter {
    name  = "log_connections"
    value = "1"
  }

  # Log all disconnection events.
  # WHAT: Each disconnection is logged with connection duration.
  # WHY: Combined with connection logs, provides a complete session audit
  #       trail including how long each session lasted.
  # SECURITY: Abnormally long sessions may indicate data exfiltration.
  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  # Log DDL statements (CREATE, ALTER, DROP).
  # WHAT: All schema-changing statements are logged.
  # WHY: Schema changes should be tracked for change management and
  #       to detect unauthorized modifications (e.g., added backdoor triggers).
  # SECURITY: Logging DDL detects unauthorized schema changes that could
  #           create data exfiltration channels (e.g., triggers that copy data).
  # ALTERNATIVE: 'all' logs every query (very verbose, high storage cost).
  #              'ddl' is a good balance for production environments.
  #              'mod' logs DDL + INSERT/UPDATE/DELETE (more comprehensive).
  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  # Set minimum duration for slow query logging (milliseconds).
  # WHAT: Queries taking longer than this threshold are logged.
  # WHY: Identifies slow queries that may indicate:
  #       1. Missing indexes (common cause of performance issues)
  #       2. N+1 query patterns in the FastAPI application
  #       3. Lock contention during concurrent operations
  #       4. SQL injection attacks that exploit expensive queries
  # SECURITY: Slow queries can be a sign of SQL injection attacks that
  #           use time-based blind SQL injection (e.g., SLEEP() functions).
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"    # Log queries taking more than 1 second
  }

  # --- Performance Parameters ---

  # Enable pg_stat_statements for query performance tracking.
  # WHAT: Tracks execution statistics for all SQL statements.
  # WHY: Enables Performance Insights and manual query optimization.
  #       Without this, we can't identify the most resource-intensive queries.
  # SECURITY: pg_stat_statements stores normalized query text (no bind
  #           parameter values), so it doesn't expose sensitive data.
  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"  # Requires a database restart to apply
  }

  # Track all statements, not just top-level queries.
  # WHY: Captures queries from functions, triggers, and prepared statements
  #       that might otherwise be invisible.
  parameter {
    name  = "pg_stat_statements.track"
    value = "all"
  }

  # Password encryption method.
  # WHAT: Uses scram-sha-256 for password hashing instead of the older md5.
  # WHY: SCRAM-SHA-256 is resistant to password sniffing and replay attacks.
  #       MD5 password hashes are vulnerable to rainbow table attacks.
  # SECURITY: This is a significant security improvement over the default md5.
  parameter {
    name  = "password_encryption"
    value = "scram-sha-256"
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-db-parameter-group"
  })

  # Create the new parameter group before destroying the old one.
  # WHY: During parameter group updates, the old group must be deassociated
  #       before deletion. Create-before-destroy prevents downtime.
  lifecycle {
    create_before_destroy = true
  }
}

# =============================================================================
# RANDOM PASSWORD GENERATION
# =============================================================================
# WHAT: Generates a cryptographically random password for the RDS master user.
# WHY: Using a random password instead of a hardcoded one ensures:
#       1. Each environment gets a unique password
#       2. The password is never stored in source code or tfvars files
#       3. Password complexity requirements are automatically met
#       4. Terraform state is the only place the password is stored
# SECURITY IMPLICATIONS:
#   - The password is stored in Terraform state. The state file MUST be
#     encrypted (S3 + KMS) and access-controlled.
#   - The password is also stored in Secrets Manager (see below) for
#     application access with audit trail.
#   - Using 'special = false' avoids connection string parsing issues in
#     some database drivers. The 32-character length provides sufficient
#     entropy (approximately 190 bits) even without special characters.
# ALTERNATIVE: Use IAM database authentication instead of passwords.
#              This eliminates password management entirely but requires
#              application-level changes to use IAM token-based auth.
# =============================================================================
resource "random_password" "db_master" {
  # Password length provides high entropy for brute-force resistance.
  # WHY: 32 characters of alphanumeric characters provides approximately
  #       190 bits of entropy, making brute-force attacks infeasible.
  #       Even at 1 trillion guesses per second, this would take longer
  #       than the age of the universe to crack.
  length = 32

  # Exclude special characters to avoid connection string issues.
  # WHY: Some database drivers and connection string formats have trouble
  #       with special characters (e.g., @ in URLs, / in JDBC strings).
  #       The entropy from 32 alphanumeric characters is sufficient.
  # ALTERNATIVE: Include specials for higher entropy per character, but
  #              test thoroughly with all database drivers first.
  special = false

  # Include uppercase, lowercase, and numbers for complexity.
  # WHY: AWS RDS requires passwords with at least one letter. Including
  #       all character types maximizes entropy per character.
  upper   = true   # Include uppercase letters (A-Z)
  lower   = true   # Include lowercase letters (a-z)
  numeric = true   # Include digits (0-9)
}

# =============================================================================
# AWS SECRETS MANAGER - DATABASE CREDENTIALS
# =============================================================================
# WHAT: Stores the database credentials (username, password, host, port)
#       in AWS Secrets Manager for secure retrieval by the application.
# WHY: Secrets Manager provides:
#       1. Encrypted storage with KMS customer-managed key
#       2. Automatic rotation capabilities (can rotate passwords periodically)
#       3. Audit trail via CloudTrail (who accessed the secret and when)
#       4. Fine-grained IAM access control (only authorized roles can read)
#       5. Version management for secret values
# SECURITY IMPLICATIONS:
#   - The ECS task role (from the security module) is the only IAM role
#     that should have secretsmanager:GetSecretValue for this secret.
#   - CloudTrail logs every GetSecretValue call for compliance auditing.
#   - The secret is encrypted with a customer-managed KMS key, adding
#     a second authorization gate (IAM + KMS) for access.
#   - Automatic rotation (not configured here but supported) can rotate
#     the database password on a schedule without application downtime.
# ALTERNATIVE: Could use AWS Systems Manager Parameter Store (SecureString),
#              which is cheaper but lacks rotation and cross-account features.
# =============================================================================
resource "aws_secretsmanager_secret" "db_credentials" {
  # Secret name includes environment for multi-environment separation.
  # WHY: Prevents dev credentials from being accidentally used in production.
  name = "${local.name_prefix}/database/credentials"

  # Description for the Secrets Manager console.
  description = "ECTP ${var.environment} RDS PostgreSQL master credentials. Contains host, port, username, password, and database name. Accessed by ECS task role only."

  # Encrypt with customer-managed KMS key.
  # WHY: AWS-managed keys (default) don't provide audit trail of key usage.
  #       Customer-managed keys enable key rotation policies and usage monitoring.
  # SECURITY: The KMS key policy controls who can encrypt/decrypt the secret.
  #           This is a second authorization gate beyond IAM permissions.
  kms_key_id = var.kms_key_arn

  # Recovery window for deleted secrets (days).
  # WHY: If the secret is accidentally deleted, it can be recovered within
  #       this window. 7 days provides a safety net for accidental deletion.
  # SECURITY: During the recovery window, the secret is not permanently
  #           deleted and can be restored. Set to 0 for immediate deletion
  #           (NOT recommended for production).
  recovery_window_in_days = var.environment == "prod" ? 30 : 7

  tags = merge(local.common_tags, {
    Name               = "${local.name_prefix}-db-credentials"
    DataClassification = "restricted"  # Highest classification for credentials
  })
}

# -----------------------------------------------------------------------------
# Secret Version (Actual Credential Values)
# -----------------------------------------------------------------------------
# WHAT: Stores the actual credential values as a JSON object in the secret.
# WHY: The secret "container" (above) is separate from its "value" (this resource).
#       This separation allows updating the value without recreating the secret.
# SECURITY: The secret value is stored in Terraform state in plaintext.
#           Ensure the state backend is encrypted (S3 + KMS) and access-controlled.
# FORMAT: JSON object with all connection parameters for easy parsing by
#          the application using json.loads().
# -----------------------------------------------------------------------------
resource "aws_secretsmanager_secret_version" "db_credentials" {
  # Associate with the secret container.
  secret_id = aws_secretsmanager_secret.db_credentials.id

  # The actual secret value as a JSON string.
  # WHY: JSON format allows the application to parse all connection parameters
  #       from a single secret, reducing the number of secrets to manage.
  secret_string = jsonencode({
    username = var.db_master_username                              # Database master username
    password = random_password.db_master.result                    # Generated random password
    host     = aws_db_instance.main.address                        # RDS endpoint hostname
    port     = aws_db_instance.main.port                           # Database port (5432)
    dbname   = var.db_name                                         # Database name
    engine   = "postgresql"                                         # Engine type for driver selection
    # Connection string for convenience (application can use this directly).
    # WHY: Some frameworks (SQLAlchemy, Django) prefer a connection URI format.
    url = "postgresql://${var.db_master_username}:${random_password.db_master.result}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/${var.db_name}?sslmode=require"
  })
}

# =============================================================================
# RDS POSTGRESQL INSTANCE
# =============================================================================
# WHAT: The primary RDS PostgreSQL database instance for the ECTP platform.
# WHY: This is the core data store for:
#       1. Student records, enrollment data, and academic information
#       2. Application configuration and metadata
#       3. User authentication and authorization data
#       4. Audit logs and transaction history
# SECURITY IMPLICATIONS (extensive):
#   - Multi-AZ: Synchronous replication to standby in a different AZ
#   - Encrypted storage: Customer-managed KMS key encrypts all data at rest
#   - SSL/TLS enforced: Parameter group forces encrypted connections
#   - No public access: publicly_accessible = false
#   - Private data subnets: No route to internet from database subnets
#   - Automated backups: Point-in-time recovery with encrypted snapshots
#   - Deletion protection: Prevents accidental database destruction
#   - Enhanced Monitoring: OS-level metrics for anomaly detection
#   - Performance Insights: Query-level performance data for optimization
#   - Copy tags to snapshots: Snapshots inherit compliance tags
# =============================================================================
resource "aws_db_instance" "main" {
  # Database instance identifier (must be unique within the AWS account/region).
  # WHY: Used in the AWS console, CloudWatch metrics, and API calls to
  #       identify this specific database instance.
  identifier = "${local.name_prefix}-postgresql"

  # --- Engine Configuration ---

  # Use PostgreSQL engine.
  # WHY: PostgreSQL is the chosen database for ECTP due to:
  #       1. Excellent JSON support for flexible data models
  #       2. Strong ACID compliance for data integrity
  #       3. Rich extension ecosystem (PostGIS, pgvector, etc.)
  #       4. Native integration with SQLAlchemy (FastAPI's ORM)
  engine = "postgres"

  # PostgreSQL version.
  # WHY: Version 15 provides the latest features and security patches.
  #       Using a specific version ensures deterministic behavior across
  #       environments.
  # SECURITY: Always use the latest minor version within the major version
  #           for security patches. Enable auto_minor_version_upgrade for this.
  engine_version = var.engine_version

  # --- Instance Sizing ---

  # Instance class (compute and memory allocation).
  # WHY: db.r6g.large (Graviton2) provides a good balance of CPU and memory
  #       for a PostgreSQL workload. The 'r' family is memory-optimized,
  #       which is ideal for databases with large working sets.
  # COST: Graviton instances are 15-20% cheaper than equivalent Intel instances
  #        with similar or better performance.
  # ALTERNATIVE: db.t3.medium for dev (burstable, cheaper but less predictable)
  #              db.r6g.xlarge for prod (more CPU and memory for heavier workloads)
  instance_class = var.instance_class

  # Allocated storage in GiB.
  # WHY: Initial storage allocation. RDS can grow storage automatically
  #       (if max_allocated_storage is set) but cannot shrink it.
  # SIZING: Start with the minimum needed and let autoscaling handle growth.
  allocated_storage = var.allocated_storage

  # Maximum storage for autoscaling (GiB).
  # WHY: RDS automatically increases storage when the database approaches
  #       the allocated limit. This prevents out-of-space errors.
  # SECURITY: Set a reasonable maximum to prevent runaway storage costs
  #           from data injection attacks or logging storms.
  max_allocated_storage = var.max_allocated_storage

  # Storage type: General Purpose SSD (gp3).
  # WHY: gp3 provides consistent baseline performance (3000 IOPS, 125 MiB/s)
  #       with the ability to provision additional IOPS independently of storage.
  # ALTERNATIVE: io2 for high-IOPS workloads (more expensive but more performant).
  #              gp2 is the older generation; gp3 is cheaper with better performance.
  storage_type = "gp3"

  # --- Database Configuration ---

  # Database name created on instance launch.
  # WHY: Creates the initial database that the ECTP application connects to.
  #       Additional databases can be created via SQL migrations.
  db_name = var.db_name

  # Master username for the database superuser.
  # WHY: The master user has full privileges on the instance. The application
  #       should use a separate, less-privileged user created via migrations.
  # SECURITY: Avoid using "admin", "root", or "postgres" as the master username
  #           to prevent credential guessing. Use a unique, non-obvious name.
  username = var.db_master_username

  # Master password from the random generator.
  # WHY: Using a randomly generated password ensures uniqueness and strength.
  # SECURITY: This password is stored in Terraform state (must be encrypted)
  #           and in Secrets Manager (for application access). It should
  #           never be hardcoded, committed to git, or shared via email/chat.
  password = random_password.db_master.result

  # --- High Availability ---

  # Enable Multi-AZ deployment for high availability.
  # WHAT: Creates a synchronous standby replica in a different AZ.
  # WHY: Multi-AZ provides:
  #       1. Automatic failover (30-120 seconds) if the primary fails
  #       2. Zero data loss (synchronous replication)
  #       3. Maintenance window updates with minimal downtime
  #       4. Protection against AZ-level failures
  # SECURITY: Multi-AZ also provides AZ-level fault isolation. If one AZ
  #           is compromised, the standby in another AZ takes over.
  # COST: Multi-AZ doubles the instance cost. Consider single-AZ for dev.
  multi_az = var.multi_az

  # --- Network Configuration ---

  # Use the subnet group we created (private data subnets).
  # WHY: Places the database in the most isolated network tier.
  db_subnet_group_name = aws_db_subnet_group.main.name

  # Attach the database security group.
  # WHY: The security group restricts inbound to only the app tier
  #       on port 5432 (PostgreSQL). All other traffic is denied.
  # SECURITY: This is a critical access control. Without proper security
  #           group rules, the database could be accessible from public subnets.
  vpc_security_group_ids = [var.db_security_group_id]

  # CRITICAL: Do NOT make the database publicly accessible.
  # WHAT: When false, the database does not get a public DNS name or IP.
  # WHY: Databases should NEVER be directly accessible from the internet.
  #       All access must go through the application tier.
  # SECURITY: This is one of the most important security settings. Setting
  #           this to true would expose the database to the entire internet,
  #           making it vulnerable to brute-force attacks, SQL injection
  #           from any source, and data exfiltration.
  # COMPLIANCE: FERPA, HIPAA, PCI DSS, and SOC 2 all require databases
  #             to be non-publicly accessible.
  publicly_accessible = false

  # Database port (PostgreSQL default).
  # WHY: Using the default port 5432 is standard. Non-standard ports provide
  #       minimal security benefit ("security through obscurity").
  # ALTERNATIVE: Could use a non-standard port, but this complicates
  #              configuration without meaningfully improving security.
  port = 5432

  # --- Encryption ---

  # Enable encryption at rest using a customer-managed KMS key.
  # WHAT: All data files, backups, snapshots, and replicas are encrypted.
  # WHY: Encryption at rest protects data if the underlying storage is
  #       physically compromised (e.g., disk theft, improper disposal).
  # SECURITY: This is a MANDATORY requirement for FERPA compliance when
  #           storing student data. Without encryption, data on decommissioned
  #           disks could be recovered by unauthorized parties.
  # NOTE: Once enabled, encryption cannot be disabled. This is intentional.
  storage_encrypted = true

  # KMS key for encryption.
  # WHY: Customer-managed keys provide:
  #       1. Key rotation policies
  #       2. Audit trail of key usage via CloudTrail
  #       3. Fine-grained access control via key policy
  #       4. Cross-account key sharing (if needed for DR)
  # ALTERNATIVE: AWS-managed key (default) is simpler but doesn't provide
  #              key usage auditing or custom rotation.
  kms_key_id = var.kms_key_arn

  # --- Backup Configuration ---

  # Automated backup retention period (days).
  # WHAT: RDS takes daily automated snapshots and retains them for this many days.
  # WHY: Backups enable point-in-time recovery (PITR) within the retention window.
  #       This is critical for:
  #       1. Recovering from accidental data deletion
  #       2. Recovering from data corruption (application bugs, SQL injection)
  #       3. Compliance with data retention policies
  # SECURITY: Backups are encrypted with the same KMS key as the instance.
  #           Access to backups requires both RDS permissions and KMS decrypt.
  # RECOMMENDATION: 7 days for dev, 35 days for prod (maximum supported by RDS).
  backup_retention_period = var.backup_retention_period

  # Preferred backup window (UTC).
  # WHY: Schedule backups during the lowest-traffic period to minimize
  #       I/O impact. For US Higher Ed, 4:00-5:00 UTC (11 PM - midnight EST)
  #       is typically the lowest-traffic period.
  # NOTE: Cannot overlap with the maintenance window.
  backup_window = var.backup_window

  # Preferred maintenance window (UTC).
  # WHY: Schedule maintenance (minor version upgrades, patching) during
  #       off-peak hours. Sunday night/Monday morning is typical for Higher Ed.
  # NOTE: Multi-AZ instances perform maintenance on the standby first,
  #        then failover, then patch the old primary -- minimizing downtime.
  maintenance_window = var.maintenance_window

  # Copy all tags to snapshots.
  # WHY: Snapshots should inherit the same tags as the instance for:
  #       1. Cost allocation (which project/environment owns this snapshot?)
  #       2. Compliance tracking (is this snapshot encrypted? what data class?)
  #       3. Automated lifecycle policies (delete old snapshots by tag)
  # SECURITY: Tags like "DataClassification = confidential" on snapshots
  #           help security scanners identify snapshots needing protection.
  copy_tags_to_snapshot = true

  # --- Version Management ---

  # Enable automatic minor version upgrades.
  # WHAT: AWS automatically upgrades to the latest minor version during
  #       the maintenance window (e.g., 15.3 -> 15.4).
  # WHY: Minor versions include security patches and bug fixes.
  #       Disabling this means manually tracking and applying patches.
  # SECURITY: Automatic patching closes known vulnerabilities quickly.
  #           Manual patching introduces a window of exposure between
  #           patch availability and application.
  auto_minor_version_upgrade = true

  # Allow major version upgrades (must be initiated manually).
  # WHY: Major version upgrades (e.g., 15 -> 16) may include breaking
  #       changes and should be tested thoroughly before applying.
  # SECURITY: Setting this to true doesn't trigger automatic upgrades;
  #           it only allows using `modify-db-instance` with the new version.
  allow_major_version_upgrade = false

  # --- Monitoring ---

  # Enable Enhanced Monitoring with 60-second intervals.
  # WHAT: Enhanced Monitoring provides OS-level metrics (CPU, memory, disk I/O,
  #       file system, network) at granular intervals.
  # WHY: Standard CloudWatch metrics are at 1-minute intervals and only
  #       cover high-level metrics. Enhanced Monitoring provides:
  #       1. Per-process CPU and memory usage
  #       2. Disk I/O queue depth and latency
  #       3. File system space utilization
  #       4. OS-level network statistics
  # SECURITY: OS-level metrics help detect anomalous database behavior
  #           (e.g., unexpected processes, excessive disk I/O from data export).
  # COST: Enhanced Monitoring costs approximately $3/month for 60-second intervals.
  monitoring_interval = var.monitoring_interval

  # IAM role for Enhanced Monitoring to publish metrics to CloudWatch.
  # WHY: Enhanced Monitoring requires an IAM role with permissions to
  #       write metrics to CloudWatch Logs.
  monitoring_role_arn = var.monitoring_interval > 0 ? var.rds_monitoring_role_arn : null

  # Enable Performance Insights for query-level performance monitoring.
  # WHAT: Provides detailed query execution statistics, wait events, and
  #       database load metrics.
  # WHY: Performance Insights helps:
  #       1. Identify slow queries consuming the most database resources
  #       2. Detect lock contention between concurrent transactions
  #       3. Analyze wait events to find bottlenecks
  #       4. Plan capacity based on actual workload characteristics
  # SECURITY: Performance Insights may display query text (without bind
  #           parameter values), which could reveal table/column names.
  #           Access is controlled by IAM permissions.
  # COST: Free tier includes 7 days of retention. Extended retention costs extra.
  performance_insights_enabled = true

  # Retain Performance Insights data for the configured period.
  # WHY: 7 days is the free tier. Extend for production to enable
  #       historical performance analysis and capacity planning.
  performance_insights_retention_period = var.environment == "prod" ? 731 : 7

  # Encrypt Performance Insights data with KMS.
  # WHY: Performance data may contain query patterns that reveal
  #       sensitive information about data access patterns.
  performance_insights_kms_key_id = var.kms_key_arn

  # --- Protection ---

  # Enable deletion protection to prevent accidental database destruction.
  # WHAT: When enabled, the database instance cannot be deleted via API or console.
  # WHY: Accidental deletion of a production database would cause catastrophic
  #       data loss. Deletion protection requires manual disabling first,
  #       adding a human verification step.
  # SECURITY: Protects against both accidental deletion and malicious
  #           destruction (e.g., compromised IAM credentials running delete).
  # NOTE: Must be disabled before running `terraform destroy`.
  deletion_protection = var.environment == "prod" ? true : false

  # Skip final snapshot when destroying (for dev/test only).
  # WHY: In dev environments, we don't need a final snapshot when destroying
  #       the database (speeds up tear-down). In production, this should be
  #       false to ensure a final backup exists.
  # SECURITY: In production, ALWAYS take a final snapshot to prevent
  #           irreversible data loss.
  skip_final_snapshot = var.environment == "prod" ? false : true

  # Name for the final snapshot (required if skip_final_snapshot = false).
  final_snapshot_identifier = var.environment == "prod" ? "${local.name_prefix}-final-snapshot" : null

  # Use the custom parameter group.
  parameter_group_name = aws_db_parameter_group.main.name

  # Enable CloudWatch log exports for PostgreSQL.
  # WHAT: Exports PostgreSQL logs to CloudWatch Logs for centralized
  #       analysis and alerting.
  # WHY: Having database logs in CloudWatch enables:
  #       1. Metric filters for error detection and alerting
  #       2. Cross-referencing with application logs for debugging
  #       3. Long-term retention for compliance auditing
  #       4. Integration with SIEM tools for security monitoring
  # SECURITY: "postgresql" logs include connection events, errors, and
  #           slow queries -- all valuable for security monitoring.
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # Apply changes immediately (for dev) or during maintenance window (for prod).
  # WHY: In dev, we want changes applied immediately for fast iteration.
  #       In prod, changes should be scheduled during maintenance windows
  #       to minimize impact on users.
  # SECURITY: Immediate changes in production could cause unexpected downtime.
  apply_immediately = var.environment == "prod" ? false : true

  # Enable IAM database authentication.
  # WHAT: Allows connecting to the database using IAM credentials instead
  #       of (or in addition to) a database password.
  # WHY: IAM authentication provides:
  #       1. No password to manage or rotate (uses temporary IAM tokens)
  #       2. Centralized access control via IAM policies
  #       3. CloudTrail logging of authentication events
  #       4. Integration with AWS SSO/Identity Center
  # SECURITY: IAM auth is more secure than password auth because:
  #           - Tokens expire after 15 minutes (cannot be reused)
  #           - Access is controlled by IAM policies (not database grants)
  #           - CloudTrail provides audit trail of every authentication
  iam_database_authentication_enabled = true

  tags = merge(local.common_tags, {
    Name               = "${local.name_prefix}-postgresql"
    DataClassification = "confidential"  # Database contains sensitive student data
  })

  # Ensure subnet group and parameter group exist before creating the instance.
  depends_on = [
    aws_db_subnet_group.main,
    aws_db_parameter_group.main
  ]
}

# =============================================================================
# CLOUDWATCH LOG GROUP FOR RDS LOGS
# =============================================================================
# WHAT: CloudWatch Log Group for PostgreSQL logs exported from RDS.
# WHY: RDS automatically creates log groups when exporting logs, but
#       pre-creating them allows us to set retention and encryption.
# SECURITY: Encrypting database logs protects query text and error
#           messages that may contain sensitive data patterns.
# NOTE: RDS creates the log group at /aws/rds/instance/{identifier}/postgresql
#        if it doesn't exist. Pre-creating it lets us control settings.
# =============================================================================
resource "aws_cloudwatch_log_group" "rds_logs" {
  # Match the naming convention that RDS uses for log group creation.
  name = "/aws/rds/instance/${local.name_prefix}-postgresql/postgresql"

  # Retain database logs for compliance.
  # WHY: Database logs are critical for security auditing and compliance.
  #       FERPA requires maintaining records of data access.
  retention_in_days = var.log_retention_days

  # Encrypt with KMS for data protection.
  kms_key_id = var.kms_key_arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rds-log-group"
  })
}

# =============================================================================
# OUTPUTS
# =============================================================================
# These outputs provide connection information and resource identifiers
# for other modules (compute, monitoring) to reference.
# =============================================================================

output "db_instance_id" {
  # WHAT: The RDS instance identifier.
  # WHY: Used for CloudWatch alarms, maintenance operations, and API calls.
  description = "The RDS instance identifier for CloudWatch alarms and API operations."
  value       = aws_db_instance.main.id
}

output "db_instance_arn" {
  # WHAT: The ARN of the RDS instance.
  # WHY: Used in IAM policies to grant access to specific database resources.
  description = "The ARN of the RDS instance for IAM policies and cross-service references."
  value       = aws_db_instance.main.arn
}

output "db_endpoint" {
  # WHAT: The DNS endpoint for connecting to the database.
  # WHY: Applications use this endpoint to connect. It automatically
  #       resolves to the current primary instance (even after failover).
  # SECURITY: This endpoint is only resolvable within the VPC (not public).
  description = "The connection endpoint for the RDS instance. Resolves to the primary instance and follows failover automatically."
  value       = aws_db_instance.main.endpoint
}

output "db_address" {
  # WHAT: The hostname portion of the endpoint (without port).
  # WHY: Some applications require the hostname and port separately.
  description = "The hostname of the RDS instance (without port)."
  value       = aws_db_instance.main.address
}

output "db_port" {
  # WHAT: The port the database listens on.
  description = "The port the RDS instance listens on (typically 5432 for PostgreSQL)."
  value       = aws_db_instance.main.port
}

output "db_name" {
  # WHAT: The name of the database created on the instance.
  description = "The name of the PostgreSQL database."
  value       = aws_db_instance.main.db_name
}

output "db_credentials_secret_arn" {
  # WHAT: The ARN of the Secrets Manager secret containing database credentials.
  # WHY: The compute module needs this ARN to inject credentials into
  #       ECS Fargate task environment variables via the secrets block.
  # SECURITY: Only the ECS task execution role should have GetSecretValue
  #           permission for this specific secret ARN.
  description = "The ARN of the Secrets Manager secret containing database credentials. Grant GetSecretValue to the ECS task execution role only."
  value       = aws_secretsmanager_secret.db_credentials.arn
}

output "db_credentials_secret_name" {
  # WHAT: The name of the Secrets Manager secret.
  # WHY: Some integrations reference secrets by name rather than ARN.
  description = "The name of the Secrets Manager secret containing database credentials."
  value       = aws_secretsmanager_secret.db_credentials.name
}
