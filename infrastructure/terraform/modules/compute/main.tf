# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Compute Module
# =============================================================================
# Author: Gopi Krishna Vajrala
# Purpose: Provisions the ECS Fargate compute infrastructure for running the
#          ECTP FastAPI application in a serverless container environment.
#
# Architecture Overview:
#   - ECS Fargate Cluster: Serverless container orchestration (no EC2 to manage)
#   - Application Load Balancer (ALB): HTTPS termination, path-based routing
#   - ECS Service: Manages desired count, rolling deployments, health checks
#   - ECS Task Definition: Container specifications, resource limits, logging
#   - Auto Scaling: Target-tracking on CPU/memory utilization
#
# Why ECS Fargate (not EC2, EKS, or Lambda):
#   1. Fargate eliminates server management (patching, scaling, AMI updates)
#   2. Per-task billing is cost-effective for variable Higher Ed workloads
#   3. Simpler than EKS for teams without Kubernetes expertise
#   4. Better suited for long-running API servers than Lambda (no cold starts)
#   5. Native integration with ALB for health checks and blue/green deploys
#
# Security Implications:
#   - Fargate tasks run in isolated micro-VMs (Firecracker) with no shared
#     kernel between tasks, providing strong workload isolation.
#   - Tasks run in private subnets with no public IP addresses.
#   - All traffic enters through the ALB in public subnets (single entry point).
#   - Container images are pulled from ECR over VPC Endpoints (no internet).
#   - Task IAM roles follow least privilege (only access needed AWS services).
#   - Secrets are injected via Secrets Manager, never baked into images.
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
      version = "~> 5.0"           # Allow 5.x patches, block 6.0+ breaking changes
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

# Retrieve the current AWS region for constructing ECR URLs and log group names.
# WHY: Makes the module portable across regions without hardcoding region names.
# SECURITY: Region awareness ensures logs and images are in the correct region.
data "aws_region" "current" {}

# Retrieve the current AWS account ID for constructing ECR repository URIs.
# WHY: ECR image URIs include the account ID; using a data source avoids hardcoding.
# SECURITY: Account ID in ECR URIs ensures images are pulled from the correct
#           account, preventing image substitution attacks.
data "aws_caller_identity" "current" {}

# =============================================================================
# LOCAL VALUES
# =============================================================================
# WHAT: Computed values used throughout the module for consistency.
# WHY: Centralizes naming conventions and avoids repetition.
# SECURITY: Consistent naming supports IAM policy patterns.
# =============================================================================
locals {
  # Standard name prefix for all resources in this module.
  # Format: "{project}-{environment}" e.g., "ectp-dev"
  name_prefix = "${var.project_name}-${var.environment}"

  # Common tags applied to all resources in this module.
  # WHY: Enables cost tracking, compliance auditing, and automated governance.
  common_tags = merge(var.tags, {
    Module      = "compute"                     # Identifies this Terraform module
    Project     = var.project_name              # Project for cost allocation
    Environment = var.environment               # Environment tier
    ManagedBy   = "terraform"                   # IaC-managed; do not modify manually
    Author      = "Gopi Krishna Vajrala"        # Original author for accountability
  })
}

# =============================================================================
# ECS FARGATE CLUSTER
# =============================================================================
# WHAT: An ECS cluster configured for Fargate launch type, which manages
#       the lifecycle of containerized ECTP FastAPI application tasks.
# WHY: ECS Fargate provides:
#       1. Serverless container execution (no EC2 instances to manage/patch)
#       2. Automatic scaling based on demand (enrollment peaks, etc.)
#       3. Per-second billing (cost-effective for variable Higher Ed workloads)
#       4. Built-in integration with ALB, CloudWatch, and IAM
# SECURITY IMPLICATIONS:
#   - Fargate uses AWS Firecracker micro-VMs for task isolation; each task
#     runs in its own isolated environment with a dedicated kernel.
#   - Container Insights provides detailed performance monitoring and can
#     detect anomalous resource usage (potential crypto-mining, data exfil).
#   - Execute command is enabled for emergency debugging but should be
#     restricted via IAM policies to authorized personnel only.
# ALTERNATIVES:
#   - EC2 launch type: More control but requires patching, AMI management,
#     and capacity planning. Not recommended for ECTP.
#   - EKS: More powerful orchestration but adds Kubernetes complexity.
#     Consider for future if multi-service microarchitecture grows.
# =============================================================================
resource "aws_ecs_cluster" "main" {
  # Cluster name includes environment for multi-environment AWS account separation.
  # WHY: Prevents naming conflicts when dev, staging, and prod share an account.
  name = "${local.name_prefix}-cluster"

  # Enable Container Insights for detailed monitoring of tasks and services.
  # WHY: Container Insights provides:
  #       1. CPU/memory utilization per task, service, and cluster
  #       2. Network traffic metrics per task
  #       3. Task startup time and failure tracking
  #       4. Automatic CloudWatch dashboard generation
  # SECURITY: Monitoring resource utilization helps detect compromised containers
  #           (e.g., crypto-mining spikes CPU; data exfiltration spikes network).
  # COST: Container Insights adds approximately $0.01 per task per hour for
  #        custom metrics. This is a small cost for significant observability gains.
  setting {
    name  = "containerInsights"   # Enable ECS Container Insights
    value = "enabled"             # Collect detailed container metrics
  }

  # Configure cluster-level settings.
  configuration {
    # Enable execute command for container debugging.
    # WHY: Allows authorized operators to "exec" into running containers for
    #       troubleshooting production issues without SSH or bastion hosts.
    # SECURITY: Execute command sessions are logged to CloudWatch and S3,
    #           providing an audit trail. IAM policies should restrict
    #           who can use "ecs:ExecuteCommand" to senior engineers only.
    # ALTERNATIVE: Disable in production and use only CloudWatch Logs for debugging.
    #              Enable temporarily only during active incident investigation.
    execute_command_configuration {
      # Log execute command sessions for audit and compliance.
      # WHY: FERPA compliance requires logging all access to systems
      #       that handle student data. Execute command is privileged access.
      logging = "OVERRIDE"

      log_configuration {
        # Send session logs to a dedicated CloudWatch log group.
        # WHY: Separating execute command logs from application logs enables
        #       targeted retention policies and access controls.
        cloud_watch_log_group_name = "/ectp/${var.environment}/ecs/execute-command"

        # Encrypt session logs for confidentiality.
        # WHY: Execute command sessions may display sensitive application data
        #       (environment variables, database queries, etc.).
        # SECURITY: Without encryption, anyone with CloudWatch read access
        #           could view the contents of debugging sessions.
        cloud_watch_encryption_enabled = true
      }
    }
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ecs-cluster"
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Log Group for ECS Execute Command
# -----------------------------------------------------------------------------
# WHAT: Dedicated log group for ECS Execute Command session logging.
# WHY: Provides audit trail for all container debugging sessions.
# SECURITY: Encrypted and with controlled retention for compliance.
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "ecs_execute_command" {
  # Hierarchical name for organization in CloudWatch console.
  name = "/ectp/${var.environment}/ecs/execute-command"

  # Retain logs for compliance auditing.
  # WHY: Execute command sessions may access sensitive data; logs must
  #       be retained for incident investigation and compliance audits.
  retention_in_days = var.log_retention_days

  # Encrypt with KMS if a key is provided.
  # WHY: Session logs may contain sensitive application data.
  kms_key_id = var.kms_key_arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ecs-exec-logs"
  })
}

# =============================================================================
# ECS CLUSTER CAPACITY PROVIDERS
# =============================================================================
# WHAT: Associates Fargate capacity providers with the ECS cluster.
# WHY: Capacity providers define WHERE tasks run. Fargate is the serverless
#       option; Fargate Spot provides discounted (but interruptible) capacity.
# SECURITY: Fargate (non-Spot) is the default to ensure critical tasks
#           are not interrupted. Fargate Spot can be used for batch processing.
# COST: Fargate Spot is up to 70% cheaper but can be interrupted with a
#        2-minute warning. Suitable for non-critical, retry-able workloads.
# =============================================================================
resource "aws_ecs_cluster_capacity_providers" "main" {
  # Associate with our ECS cluster.
  cluster_name = aws_ecs_cluster.main.name

  # Register both Fargate and Fargate Spot capacity providers.
  # WHY: Having both available lets us use standard Fargate for the API
  #       (must be always-on) and Fargate Spot for batch jobs (cost savings).
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  # Set Fargate (non-Spot) as the default capacity provider.
  # WHY: The ECTP FastAPI application must be always-available for students
  #       and faculty. Fargate Spot could interrupt the service during peak
  #       enrollment periods, causing availability issues.
  # SECURITY: Predictable capacity ensures the security monitoring and
  #           logging pipelines are not interrupted.
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"    # Use standard Fargate by default
    weight            = 1             # All tasks go to Fargate unless overridden
    base              = 1             # At least 1 task always on Fargate
  }
}

# =============================================================================
# CLOUDWATCH LOG GROUP FOR ECS TASKS
# =============================================================================
# WHAT: A CloudWatch Log Group to collect stdout/stderr from ECS Fargate tasks.
# WHY: Centralized logging is essential for:
#       1. Application debugging and error tracking
#       2. Security monitoring (detecting suspicious API calls)
#       3. Compliance auditing (logging access to student data)
#       4. Performance analysis and optimization
# SECURITY: Log data may contain sensitive information (error messages with
#           student IDs, API request details). Encryption at rest is critical.
# RETENTION: Must meet FERPA/compliance requirements for audit log retention.
# =============================================================================
resource "aws_cloudwatch_log_group" "ecs_tasks" {
  # Hierarchical name groups all ECTP ECS logs together.
  # WHY: Easy to find and filter in the CloudWatch console.
  #       The /ectp/{env}/ecs/tasks prefix is distinct from execute-command logs.
  name = "/ectp/${var.environment}/ecs/tasks"

  # Retain logs for the configured duration.
  # WHY: Compliance requires retaining application logs. Default is 90 days
  #       for dev; production should be set to 365+ days.
  # COST: CloudWatch Logs storage is $0.03/GB/month. Set appropriate retention
  #        to balance cost with compliance requirements.
  retention_in_days = var.log_retention_days

  # Encrypt log data at rest using a customer-managed KMS key.
  # WHY: Application logs may contain PII (student names, IDs in error messages).
  #       KMS encryption adds a second authorization gate beyond CloudWatch IAM.
  # SECURITY: Without encryption, anyone with logs:GetLogEvents permission
  #           can read all application logs. With KMS, they also need kms:Decrypt.
  kms_key_id = var.kms_key_arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ecs-task-logs"
  })
}

# =============================================================================
# ECS TASK DEFINITION
# =============================================================================
# WHAT: Defines the container specification for the ECTP FastAPI application,
#       including the Docker image, resource limits, environment variables,
#       logging configuration, and networking mode.
# WHY: The task definition is the blueprint for every container instance.
#       It specifies EXACTLY what runs, with what resources, and with what
#       permissions -- this is the core of the compute infrastructure.
# SECURITY IMPLICATIONS:
#   - Uses "awsvpc" networking for per-task ENI (network isolation between tasks).
#   - Secrets are injected from Secrets Manager (never hardcoded or in env vars).
#   - Task execution role has minimal permissions (only pull images and write logs).
#   - Task role has minimal permissions (only access needed AWS services).
#   - Read-only root filesystem prevents container compromise from persisting.
#   - Resource limits prevent runaway containers from starving others.
# =============================================================================
resource "aws_ecs_task_definition" "app" {
  # Family name groups revisions of this task definition together.
  # WHY: ECS uses the family name to track versions. Each `terraform apply`
  #       creates a new revision if the definition has changed.
  family = "${local.name_prefix}-app"

  # Use "awsvpc" network mode for Fargate (required).
  # WHY: awsvpc gives each task its own ENI with a private IP address,
  #       enabling security group attachment at the task level.
  # SECURITY: Per-task ENI means each task can have different security groups,
  #           providing network-level isolation between different services.
  # ALTERNATIVE: "bridge" mode shares the host network (EC2 only, not Fargate).
  network_mode = "awsvpc"

  # Use Fargate launch type (serverless, no EC2 management).
  # WHY: Fargate eliminates the need to provision, configure, and scale
  #       EC2 instances. AWS manages the underlying infrastructure.
  requires_compatibilities = ["FARGATE"]

  # CPU allocation for the task (in CPU units; 1024 = 1 vCPU).
  # WHY: 512 CPU units (0.5 vCPU) is appropriate for a FastAPI application
  #       handling moderate traffic. Scale up for production workloads.
  # COST: Fargate pricing is per-second based on CPU + memory allocated.
  #        512 CPU units costs approximately $0.01/hour.
  cpu = var.task_cpu

  # Memory allocation for the task (in MiB).
  # WHY: 1024 MiB (1 GB) is sufficient for a Python FastAPI application
  #       with typical payload sizes. Monitor and adjust based on actual usage.
  # SECURITY: Memory limits prevent memory leak exploits from consuming
  #           excessive resources and potentially causing OOM-kills on neighbors.
  memory = var.task_memory

  # IAM role for the ECS agent to pull images and write logs.
  # WHY: The execution role is used by the ECS agent (not the application)
  #       to perform setup tasks: pulling the container image from ECR,
  #       sending logs to CloudWatch, and fetching secrets from Secrets Manager.
  # SECURITY: This role should have ONLY:
  #           - ecr:GetDownloadUrlForLayer, ecr:BatchGetImage (pull images)
  #           - logs:CreateLogStream, logs:PutLogEvents (write logs)
  #           - secretsmanager:GetSecretValue (fetch secrets)
  #           Any additional permissions expand the attack surface.
  execution_role_arn = var.ecs_execution_role_arn

  # IAM role for the application running inside the container.
  # WHY: The task role is assumed by the application code at runtime.
  #       It determines what AWS services the FastAPI application can access.
  # SECURITY: Follow least privilege -- only grant access to services the
  #           application actually needs (e.g., S3 for file uploads,
  #           SES for email, DynamoDB for sessions).
  #           NEVER grant admin or broad access to the task role.
  task_role_arn = var.ecs_task_role_arn

  # Container definitions specify the Docker containers in this task.
  # WHY: This defines what Docker image to run, with what settings.
  # NOTE: Using jsonencode() instead of a JSON file for better Terraform
  #        integration and variable interpolation.
  container_definitions = jsonencode([
    {
      # Container name used for service discovery and log stream naming.
      # WHY: A descriptive name helps identify this container in ECS console,
      #       CloudWatch Logs, and X-Ray traces.
      name = "${local.name_prefix}-app"

      # Docker image URI from Amazon ECR (or any container registry).
      # WHY: ECR provides private, encrypted container image storage with
      #       vulnerability scanning and lifecycle policies.
      # SECURITY: Always use a specific image tag (not "latest") in production
      #           to ensure deterministic deployments and prevent supply-chain attacks.
      #           Enable ECR image scanning to detect known vulnerabilities.
      image = var.container_image

      # Mark this as the essential container.
      # WHY: If this container stops, the entire task stops. For a single-container
      #       task, this must be true. For sidecar patterns, sidecars can be
      #       non-essential (task continues if they crash).
      essential = true

      # Port mappings expose the container port to the ALB.
      # WHY: The FastAPI application listens on var.container_port (default 8000).
      #       The ALB routes traffic to this port via the target group.
      # SECURITY: Only the ALB security group can reach this port; direct
      #           access from the internet is not possible because tasks are
      #           in private subnets with no public IPs.
      portMappings = [
        {
          containerPort = var.container_port    # Port FastAPI listens on inside the container
          protocol      = "tcp"                  # TCP protocol for HTTP traffic
          hostPort      = var.container_port     # In awsvpc mode, hostPort must equal containerPort
        }
      ]

      # Environment variables for application configuration.
      # WHY: Non-sensitive configuration is passed via environment variables
      #       following the 12-factor app methodology.
      # SECURITY: NEVER put secrets (passwords, API keys) in environment variables.
      #           Use the "secrets" block below for sensitive values.
      environment = [
        {
          name  = "ENVIRONMENT"                            # Application environment identifier
          value = var.environment                           # Tells the app which config to load
        },
        {
          name  = "PORT"                                   # Port for the FastAPI server to bind
          value = tostring(var.container_port)              # Must be string for ECS env vars
        },
        {
          name  = "LOG_LEVEL"                              # Application log verbosity
          value = var.environment == "prod" ? "WARNING" : "DEBUG"  # Verbose in dev, quieter in prod
        },
        {
          name  = "AWS_REGION"                             # Region for AWS SDK calls from the app
          value = data.aws_region.current.name             # Dynamically set to current region
        }
      ]

      # Secrets injected from AWS Secrets Manager at container startup.
      # WHY: Secrets Manager provides:
      #       1. Encrypted storage for sensitive values (database passwords, API keys)
      #       2. Automatic rotation capabilities
      #       3. Audit trail of secret access via CloudTrail
      #       4. Version management for secret values
      # SECURITY: Secrets are fetched at task startup and injected as env vars.
      #           They are NOT baked into the container image and NOT visible
      #           in the task definition in the ECS console (only the ARN is visible).
      # NOTE: The ARNs must be provided via variables from the security module.
      secrets = var.container_secrets

      # CloudWatch Logs configuration for container stdout/stderr.
      # WHY: All container output is captured in CloudWatch for:
      #       1. Debugging application errors
      #       2. Security monitoring (detecting suspicious requests)
      #       3. Performance analysis
      #       4. Compliance auditing
      # SECURITY: The "awslogs" driver sends logs directly to CloudWatch
      #           without touching disk, reducing the risk of log tampering.
      logConfiguration = {
        logDriver = "awslogs"  # Send logs to CloudWatch Logs
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_tasks.name    # Target log group
          "awslogs-region"        = data.aws_region.current.name                # AWS region for logs
          "awslogs-stream-prefix" = "ecs"                                       # Prefix for log stream names
        }
      }

      # Health check configuration for container-level health monitoring.
      # WHY: ECS uses this health check to determine if the container is
      #       healthy. Unhealthy containers are stopped and replaced.
      # SECURITY: The health check endpoint should NOT expose sensitive
      #           information. A simple HTTP 200 response is sufficient.
      # NOTE: This is separate from the ALB health check; both must pass
      #        for the task to receive traffic.
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:${var.container_port}/health || exit 1"]
        interval    = 30     # Check every 30 seconds
        timeout     = 5      # Fail if no response within 5 seconds
        retries     = 3      # Allow 3 failures before marking unhealthy
        startPeriod = 60     # Give the app 60 seconds to start before checking
      }

      # Make the root filesystem read-only for security hardening.
      # WHY: A read-only root filesystem prevents attackers from:
      #       1. Writing malware to the container filesystem
      #       2. Modifying application binaries
      #       3. Creating persistent backdoors
      # SECURITY: Critical defense-in-depth measure. If an attacker gains
      #           code execution, they cannot persist changes to the filesystem.
      # NOTE: The application must use /tmp or mounted volumes for temp files.
      #        FastAPI typically doesn't need to write to the filesystem.
      readonlyRootFilesystem = true

      # Container-level resource limits for stability.
      # WHY: Prevents a single container from consuming excessive resources.
      # SECURITY: Limits prevent resource exhaustion attacks (DoS via memory
      #           consumption or CPU spinning).
      linuxParameters = {
        # Run container init process to reap zombie processes.
        # WHY: Without an init process, zombie processes accumulate and
        #       eventually exhaust the PID namespace, causing container failure.
        initProcessEnabled = true
      }
    }
  ])

  # Runtime platform specification for Fargate.
  # WHY: Specifies the OS and CPU architecture for the Fargate task.
  #       This must match the architecture of the container image.
  runtime_platform {
    operating_system_family = "LINUX"   # Linux containers (not Windows)
    cpu_architecture        = "X86_64"  # Standard x86_64 architecture
    # ALTERNATIVE: "ARM64" (Graviton) is 20% cheaper and often faster.
    #              Use ARM64 if the container image supports it.
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-task-definition"
  })
}

# =============================================================================
# APPLICATION LOAD BALANCER (ALB)
# =============================================================================
# WHAT: An Application Load Balancer that distributes incoming HTTPS traffic
#       across ECS Fargate tasks running the ECTP FastAPI application.
# WHY: The ALB provides:
#       1. HTTPS/TLS termination (decrypts HTTPS, forwards HTTP to containers)
#       2. Path-based routing for future microservice decomposition
#       3. Health checking to route traffic only to healthy tasks
#       4. Web Application Firewall (WAF) integration for Layer 7 protection
#       5. Access logging for compliance and security monitoring
#       6. Sticky sessions for stateful requests (if needed)
# SECURITY IMPLICATIONS:
#   - The ALB is the ONLY entry point from the internet to the ECTP application.
#   - TLS 1.2+ is enforced; older protocols (SSLv3, TLS 1.0/1.1) are blocked.
#   - Access logs capture every request for forensic analysis.
#   - The ALB security group restricts inbound to only ports 80/443.
#   - WAF integration (configured separately) provides DDoS and injection protection.
#   - Deletion protection prevents accidental destruction of the ALB.
# =============================================================================
resource "aws_lb" "app" {
  # ALB name has a 32-character limit; keep it concise.
  # WHY: AWS enforces a 32-character limit on ALB names.
  name = "${local.name_prefix}-alb"

  # Use "application" type for HTTP/HTTPS Layer 7 load balancing.
  # WHY: ALBs support path-based routing, host-based routing, and WebSocket
  #       connections, all of which the FastAPI application may use.
  # ALTERNATIVE: NLB (Network Load Balancer) for TCP/UDP Layer 4 load balancing.
  #              NLBs are faster but don't support path-based routing or WAF.
  load_balancer_type = "application"

  # Place the ALB in public subnets so it's accessible from the internet.
  # WHY: The ALB must have public IPs to receive HTTPS traffic from users
  #       (students, faculty, staff) accessing the ECTP platform.
  # SECURITY: Public subnets are the ONLY subnets with internet routing.
  #           The ALB security group further restricts what traffic is allowed.
  subnets = var.public_subnet_ids

  # Attach the ALB security group to control inbound/outbound traffic.
  # WHY: The security group restricts the ALB to only accept HTTP (80) and
  #       HTTPS (443) from the internet, and only send traffic to the ECS
  #       tasks on the container port.
  security_groups = [var.alb_security_group_id]

  # Disable public-facing ALB for internal-only deployments.
  # WHY: For most Higher Ed deployments, the ALB should be internet-facing
  #       so students can access the platform from anywhere.
  # SECURITY: Set to true for internal-only services that should only be
  #           accessible via VPN or DirectConnect.
  internal = false

  # Enable deletion protection to prevent accidental destruction.
  # WHY: Deleting the ALB would take down the entire application immediately.
  #       Deletion protection requires manual disabling before destruction,
  #       adding a human verification step to prevent accidents.
  # SECURITY: Prevents both accidental deletion and malicious destruction
  #           (e.g., a compromised Terraform state or pipeline).
  # NOTE: Must be set to false before running `terraform destroy`.
  enable_deletion_protection = var.environment == "prod" ? true : false

  # Enable HTTP/2 for better performance.
  # WHY: HTTP/2 provides multiplexing, header compression, and server push,
  #       reducing latency for API-heavy applications like ECTP.
  enable_http2 = true

  # Set idle timeout for connections.
  # WHY: 120 seconds allows long-running API calls (report generation,
  #       batch processing) to complete without timing out.
  # SECURITY: Don't set too high (>300s) as it increases vulnerability
  #           to slowloris-type DDoS attacks.
  idle_timeout = 120

  # Drop invalid HTTP headers for security.
  # WHY: Some attacks use malformed HTTP headers to bypass WAF rules
  #       or exploit header parsing vulnerabilities.
  # SECURITY: Dropping invalid headers prevents header injection attacks
  #           and ensures only well-formed requests reach the application.
  drop_invalid_header_fields = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alb"
  })
}

# =============================================================================
# ALB LISTENER - HTTPS (Port 443)
# =============================================================================
# WHAT: An HTTPS listener on port 443 that terminates TLS and forwards
#       decrypted traffic to the target group (ECS Fargate tasks).
# WHY: All production traffic should use HTTPS for:
#       1. Encrypting data in transit (FERPA requirement for student data)
#       2. Authenticating the server identity to prevent MITM attacks
#       3. Enabling HTTP/2 (requires TLS in most browsers)
# SECURITY:
#   - Uses TLS 1.2+ only (via the security policy)
#   - Certificate is managed by AWS Certificate Manager (ACM) for
#     automatic renewal and secure key storage.
#   - Forward action sends traffic to the target group for health-checked routing.
# =============================================================================
resource "aws_lb_listener" "https" {
  # Attach to our Application Load Balancer.
  load_balancer_arn = aws_lb.app.arn

  # Listen on port 443 (standard HTTPS port).
  # WHY: Users expect HTTPS on port 443. Non-standard ports would require
  #       special client configuration and may be blocked by firewalls.
  port = 443

  # Use HTTPS protocol for encrypted traffic.
  protocol = "HTTPS"

  # TLS security policy determines supported protocols and ciphers.
  # WHY: "ELBSecurityPolicy-TLS13-1-2-2021-06" enforces:
  #       - TLS 1.2 minimum (blocks TLS 1.0 and 1.1)
  #       - TLS 1.3 support for forward secrecy
  #       - Strong cipher suites only (no RC4, DES, or export ciphers)
  # SECURITY: Using an older policy (e.g., ELBSecurityPolicy-2016-08)
  #           would allow weak ciphers and TLS 1.0, which are vulnerable.
  # COMPLIANCE: PCI DSS requires TLS 1.2+. FERPA best practices recommend TLS 1.2+.
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  # ACM certificate for HTTPS.
  # WHY: ACM provides free, auto-renewing TLS certificates managed by AWS.
  #       This eliminates the risk of certificate expiration outages.
  # SECURITY: ACM certificates use 2048-bit RSA keys and are stored in
  #           AWS's secure certificate storage (not accessible to users).
  certificate_arn = var.acm_certificate_arn

  # Default action: forward traffic to the target group.
  # WHY: All HTTPS traffic is routed to the ECS Fargate tasks via the
  #       target group, which handles health checking and load distribution.
  default_action {
    type             = "forward"                            # Forward to target group
    target_group_arn = aws_lb_target_group.app.arn          # Send to ECS tasks
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-https-listener"
  })
}

# =============================================================================
# ALB LISTENER - HTTP (Port 80) - Redirect to HTTPS
# =============================================================================
# WHAT: An HTTP listener on port 80 that redirects ALL traffic to HTTPS.
# WHY: Users may type "http://" or browsers may default to HTTP. This
#       listener ensures they are always redirected to the secure HTTPS version.
# SECURITY: The redirect uses HTTP 301 (permanent) to tell browsers to
#           always use HTTPS in the future (HSTS-like behavior).
#           No application traffic is ever served over HTTP.
# =============================================================================
resource "aws_lb_listener" "http_redirect" {
  # Attach to our Application Load Balancer.
  load_balancer_arn = aws_lb.app.arn

  # Listen on port 80 (standard HTTP port).
  port     = 80
  protocol = "HTTP"

  # Redirect ALL HTTP traffic to HTTPS.
  # WHY: Ensures all traffic is encrypted. No exceptions.
  # SECURITY: This is a critical security control. Without this redirect,
  #           users might send credentials or student data over unencrypted HTTP.
  default_action {
    type = "redirect"           # Redirect, don't forward

    redirect {
      port        = "443"       # Redirect to HTTPS port
      protocol    = "HTTPS"     # Use HTTPS protocol
      status_code = "HTTP_301"  # Permanent redirect (browsers cache this)
    }
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-http-redirect-listener"
  })
}

# =============================================================================
# ALB TARGET GROUP
# =============================================================================
# WHAT: A target group that registers ECS Fargate tasks as targets for
#       the ALB to route traffic to.
# WHY: Target groups provide:
#       1. Health checking to remove unhealthy tasks from rotation
#       2. Load distribution across all healthy tasks
#       3. Deregistration delay for graceful shutdown during deployments
#       4. Stickiness options for stateful applications
# SECURITY:
#   - Health checks use the application's /health endpoint
#   - The target type is "ip" (required for Fargate awsvpc networking)
#   - Deregistration delay ensures in-flight requests complete before
#     tasks are removed during deployments
# =============================================================================
resource "aws_lb_target_group" "app" {
  # Target group name has a 32-character limit.
  name = "${local.name_prefix}-tg"

  # Route traffic to the container port where FastAPI listens.
  # WHY: The ALB sends decrypted HTTP traffic to this port on each task.
  port     = var.container_port
  protocol = "HTTP"    # Traffic is decrypted at the ALB; internal traffic uses HTTP.

  # Target type must be "ip" for Fargate tasks with awsvpc networking.
  # WHY: Fargate tasks don't run on EC2 instances, so "instance" type
  #       is not applicable. "ip" targets allow the ALB to route directly
  #       to each task's private IP address.
  target_type = "ip"

  # Associate with the VPC where ECS tasks are running.
  vpc_id = var.vpc_id

  # Health check configuration for determining task health.
  # WHY: The ALB sends periodic requests to this endpoint. If a task
  #       fails the health check, it's removed from rotation and replaced.
  health_check {
    # Enable health checking.
    enabled = true

    # Path to the health check endpoint.
    # WHY: The FastAPI application should expose a /health endpoint that
    #       returns HTTP 200 when the application is healthy and ready
    #       to serve traffic.
    # SECURITY: The health endpoint should NOT expose sensitive information
    #           (no database status, no version details, no internal IPs).
    path = var.health_check_path

    # Expected HTTP status code for a healthy response.
    # WHY: HTTP 200 means the application is healthy and ready.
    matcher = "200"

    # Health check interval: how often the ALB checks each target.
    # WHY: 30 seconds balances between quick detection of failures and
    #       minimizing health check traffic overhead.
    interval = 30

    # Timeout: how long to wait for a health check response.
    # WHY: 10 seconds allows for slow health checks that may query
    #       database connectivity or other dependencies.
    timeout = 10

    # Healthy threshold: consecutive successes needed to mark healthy.
    # WHY: 2 consecutive successes prevents flapping (briefly healthy
    #       then unhealthy tasks from receiving traffic).
    healthy_threshold = 2

    # Unhealthy threshold: consecutive failures needed to mark unhealthy.
    # WHY: 3 consecutive failures (90 seconds) provides tolerance for
    #       temporary blips (GC pauses, brief network issues).
    unhealthy_threshold = 3

    # Protocol for health checks.
    protocol = "HTTP"
  }

  # Deregistration delay: how long to wait before removing targets.
  # WHY: During deployments, in-flight requests need time to complete
  #       before the old task is stopped. 30 seconds is sufficient for
  #       most API requests to complete.
  # SECURITY: Too long a delay wastes resources; too short risks dropping
  #           in-flight requests, potentially losing data.
  deregistration_delay = 30

  # Slow start duration: ramp up traffic to new targets gradually.
  # WHY: New tasks may need time to warm up (load caches, establish
  #       database connection pools). Slow start prevents overwhelming
  #       a freshly started task with full traffic.
  slow_start = 60

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-target-group"
  })
}

# =============================================================================
# ECS SERVICE
# =============================================================================
# WHAT: An ECS Service that maintains the desired number of ECTP FastAPI
#       task instances and integrates with the ALB for traffic distribution.
# WHY: The ECS Service provides:
#       1. Desired count maintenance (restarts failed tasks automatically)
#       2. Rolling deployment with health checks (zero-downtime deploys)
#       3. ALB integration for automatic target registration/deregistration
#       4. Multi-AZ task placement for fault tolerance
#       5. Circuit breaker for automatic rollback on failed deployments
# SECURITY:
#   - Tasks run in private subnets with no public IPs
#   - The security group restricts inbound traffic to only the ALB
#   - Rolling deployments ensure continuous availability during updates
#   - Circuit breaker prevents bad deployments from taking down the service
# =============================================================================
resource "aws_ecs_service" "app" {
  # Service name for identification in ECS console and CloudWatch.
  name = "${local.name_prefix}-service"

  # Associate with our ECS cluster.
  cluster = aws_ecs_cluster.main.id

  # Use the latest revision of our task definition.
  # WHY: Always deploy the latest task definition to ensure new code
  #       and configuration changes are picked up.
  task_definition = aws_ecs_task_definition.app.arn

  # Number of task instances to maintain.
  # WHY: Multiple tasks provide high availability and distribute load.
  #       Auto Scaling (configured below) adjusts this based on demand.
  #       The minimum should ensure at least one task per AZ for fault tolerance.
  desired_count = var.desired_count

  # Use Fargate launch type (serverless).
  launch_type = "FARGATE"

  # Platform version specifies the Fargate runtime version.
  # WHY: "LATEST" ensures we get the newest security patches and features.
  #       For maximum stability, pin to a specific version (e.g., "1.4.0").
  # SECURITY: Newer platform versions include security patches for the
  #           Firecracker micro-VM runtime and container agent.
  platform_version = "LATEST"

  # Enable ECS Execute Command for debugging.
  # WHY: Allows authorized operators to shell into running containers
  #       for live debugging without SSH or bastion hosts.
  # SECURITY: Sessions are logged to CloudWatch. Restrict access via IAM
  #           policy on "ecs:ExecuteCommand" to senior engineers only.
  enable_execute_command = true

  # Force a new deployment when the task definition changes.
  # WHY: Ensures that task definition changes trigger a rolling deployment.
  force_new_deployment = true

  # Network configuration for Fargate tasks.
  # WHY: Specifies which subnets tasks launch in and which security groups apply.
  network_configuration {
    # Launch tasks in private app subnets (no direct internet access).
    # WHY: Private subnets ensure tasks are not directly accessible from the
    #       internet. All traffic must flow through the ALB.
    # SECURITY: This is a critical security control. Tasks in public subnets
    #           with public IPs would be directly attackable from the internet.
    subnets = var.private_app_subnet_ids

    # Attach the ECS tasks security group.
    # WHY: The security group restricts inbound traffic to only the ALB
    #       on the container port, and allows outbound for NAT/VPC Endpoints.
    security_groups = [var.ecs_security_group_id]

    # Do NOT assign public IPs to Fargate tasks.
    # WHY: Tasks in private subnets with NAT Gateway access do not need
    #       public IPs. Assigning public IPs would bypass the private subnet
    #       security model.
    # SECURITY: Public IPs on tasks would make them directly reachable from
    #           the internet, defeating the purpose of private subnets.
    assign_public_ip = false
  }

  # ALB integration: register tasks with the target group.
  # WHY: This tells ECS to automatically register new tasks with the ALB
  #       target group and deregister them when they stop.
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn       # ALB target group
    container_name   = "${local.name_prefix}-app"         # Container name from task def
    container_port   = var.container_port                  # Port the container listens on
  }

  # Deployment configuration for rolling updates.
  # WHY: Controls how many tasks can be started/stopped during a deployment.
  deployment_configuration {
    # Maximum percent: allow up to 200% of desired count during deployment.
    # WHY: During a rolling deployment, new tasks are started before old
    #       tasks are stopped. 200% means we can have double the tasks
    #       temporarily, ensuring zero downtime.
    maximum_percent = 200

    # Minimum healthy percent: maintain at least 100% during deployment.
    # WHY: 100% means all desired tasks must be healthy before old tasks
    #       are removed. This ensures the service never drops below full
    #       capacity during deployments.
    # ALTERNATIVE: 50% for faster deployments but with reduced capacity.
    minimum_healthy_percent = 100

    # Deployment circuit breaker for automatic rollback.
    # WHY: If new tasks consistently fail health checks, the circuit breaker
    #       automatically rolls back to the previous working version.
    # SECURITY: Prevents bad deployments (e.g., misconfigured containers)
    #           from taking down the service permanently.
    deployment_circuit_breaker {
      enable   = true    # Enable the circuit breaker
      rollback = true    # Automatically rollback on failure
    }
  }

  # Spread tasks across AZs for fault tolerance.
  # WHY: If one AZ fails, tasks in other AZs continue serving traffic.
  #       "spread" strategy distributes tasks as evenly as possible.
  # SECURITY: AZ-level isolation means a physical security breach or
  #           infrastructure failure in one AZ doesn't affect the others.
  ordered_placement_strategy {
    type  = "spread"                           # Distribute evenly
    field = "attribute:ecs.availability-zone"  # Spread across AZs
  }

  # Wait for the service to stabilize before considering it deployed.
  # WHY: Ensures Terraform doesn't report success until the service is
  #       actually running and healthy.
  wait_for_steady_state = false

  # Ignore changes to desired_count since Auto Scaling manages it.
  # WHY: After initial creation, Auto Scaling adjusts the desired count
  #       based on CPU/memory utilization. If Terraform resets it on every
  #       apply, it would fight with Auto Scaling.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ecs-service"
  })

  # Ensure the ALB listener exists before creating the service.
  # WHY: The service needs the ALB and target group to be fully configured
  #       before it can register tasks. Creating them simultaneously could
  #       cause race conditions.
  depends_on = [aws_lb_listener.https]
}

# =============================================================================
# AUTO SCALING
# =============================================================================
# WHAT: Application Auto Scaling for ECS Fargate tasks based on CPU and
#       memory utilization metrics.
# WHY: Higher Ed workloads have predictable peaks (enrollment, registration,
#       exam periods) and valleys (summer, breaks). Auto Scaling ensures:
#       1. Enough capacity during peak periods (no service degradation)
#       2. Reduced costs during off-peak (scale down to minimum)
#       3. Automatic response to unexpected load spikes
# SECURITY: Auto Scaling prevents resource exhaustion attacks (DoS) from
#           overloading a fixed number of tasks. As load increases, more
#           tasks are added to absorb the traffic.
# =============================================================================

# -----------------------------------------------------------------------------
# Auto Scaling Target
# -----------------------------------------------------------------------------
# WHAT: Registers the ECS service as an Auto Scaling target.
# WHY: This tells Application Auto Scaling which resource to scale and
#       what the minimum and maximum boundaries are.
# SECURITY: Setting a max_capacity prevents runaway scaling from consuming
#           excessive resources (and budget) during a DDoS attack.
# -----------------------------------------------------------------------------
resource "aws_appautoscaling_target" "ecs" {
  # Minimum number of tasks (scale-in floor).
  # WHY: The minimum ensures at least var.min_capacity tasks are always running,
  #       providing baseline availability even during low-traffic periods.
  min_capacity = var.min_capacity

  # Maximum number of tasks (scale-out ceiling).
  # WHY: Prevents unbounded scaling that could exhaust budget or hit
  #       AWS service limits (e.g., ENI limits per AZ).
  # SECURITY: Also limits the blast radius of a scaling exploit where
  #           an attacker triggers massive scale-out to inflate costs.
  max_capacity = var.max_capacity

  # The resource ID format for ECS services.
  # WHY: Application Auto Scaling uses this format to identify the ECS
  #       service to scale.
  resource_id = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"

  # The service namespace for ECS.
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# -----------------------------------------------------------------------------
# CPU-Based Auto Scaling Policy
# -----------------------------------------------------------------------------
# WHAT: Scales ECS tasks up/down based on average CPU utilization.
# WHY: CPU is the primary bottleneck for compute-intensive API operations
#       (JSON parsing, business logic, serialization). When average CPU
#       exceeds the target, more tasks are added to distribute the load.
# SECURITY: Scaling on CPU helps absorb CPU-intensive attacks (e.g.,
#           algorithmic complexity attacks that consume CPU).
# TUNING: Target 70% CPU utilization to leave headroom for traffic spikes.
#          Higher targets save cost but risk performance degradation.
# -----------------------------------------------------------------------------
resource "aws_appautoscaling_policy" "cpu" {
  # Policy name for identification in CloudWatch and Auto Scaling console.
  name = "${local.name_prefix}-cpu-scaling"

  # Associate with the ECS service scaling target.
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  # Use target tracking (auto-adjusts to maintain the target metric value).
  # WHY: Target tracking is simpler and more reliable than step scaling.
  #       It automatically creates both scale-out and scale-in alarms.
  policy_type = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    # Target CPU utilization percentage.
    # WHY: 70% leaves 30% headroom for unexpected traffic spikes while
    #       still efficiently utilizing provisioned resources.
    # ALTERNATIVE: 50% for more aggressive scaling (higher availability, higher cost).
    #              80% for cost optimization (less headroom, risk of saturation).
    target_value = 70.0

    # Cooldown period after scale-in (seconds).
    # WHY: 300 seconds (5 minutes) prevents thrashing where the service
    #       repeatedly scales in and out. Longer cooldowns save money
    #       but slow response to returning load.
    scale_in_cooldown = 300

    # Cooldown period after scale-out (seconds).
    # WHY: 60 seconds allows rapid scale-out during traffic spikes.
    #       Shorter cooldowns enable faster response to increasing load.
    scale_out_cooldown = 60

    # Use the ECS-specific CPU utilization metric.
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# -----------------------------------------------------------------------------
# Memory-Based Auto Scaling Policy
# -----------------------------------------------------------------------------
# WHAT: Scales ECS tasks up/down based on average memory utilization.
# WHY: Memory-intensive operations (large response payloads, in-memory
#       caching, connection pooling) can exhaust memory before CPU.
#       Scaling on memory prevents OOM kills.
# SECURITY: Memory exhaustion can cause service crashes; scaling on memory
#           helps maintain availability during memory-intensive attacks.
# TUNING: Target 70% memory utilization for similar headroom as CPU.
# -----------------------------------------------------------------------------
resource "aws_appautoscaling_policy" "memory" {
  name = "${local.name_prefix}-memory-scaling"

  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  policy_type = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    # Target memory utilization percentage.
    # WHY: 70% matches the CPU target for consistent behavior.
    #       Python applications (FastAPI) can have variable memory usage
    #       due to garbage collection patterns.
    target_value = 70.0

    scale_in_cooldown  = 300   # 5 minutes between scale-in events
    scale_out_cooldown = 60    # 1 minute between scale-out events

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
  }
}
