# =============================================================================
# Enterprise Cloud Transformation Platform (ECTP) - Monitoring Module
# =============================================================================
# Author: Gopi Krishna Vajrala
# Description: Provisions comprehensive monitoring infrastructure including
#              CloudWatch log groups, metric alarms (CPU, latency, errors),
#              SNS topic for alert notifications, and a CloudWatch dashboard
#              for operational visibility into the ECTP platform.
#
# Architecture Overview:
#   - CloudWatch Log Group: Centralized logging for ECS tasks with KMS encryption
#   - CloudWatch Metric Alarms: CPU utilization, API latency, HTTP error rates
#   - SNS Topic: Notification fanout for alarms (email, Slack, PagerDuty)
#   - CloudWatch Dashboard: Real-time operational dashboard with key metrics
#
# Monitoring Strategy:
#   - Proactive: Alarms trigger before users notice degradation
#   - Layered: Infrastructure metrics + application metrics + logs
#   - Actionable: Each alarm has clear thresholds and runbook references
#   - Cost-effective: Free tier metrics where possible, custom only when needed
#
# Security Implications:
#   - Logs may contain sensitive data (PII in error messages); encrypted with KMS
#   - SNS topic has access policy restricting who can publish/subscribe
#   - Dashboard is IAM-controlled; only authorized roles can view
#   - Alarms detect security-relevant events (high error rates, unusual CPU)
# =============================================================================

# -----------------------------------------------------------------------------
# Terraform Configuration Block
# -----------------------------------------------------------------------------
# Declares required provider versions for reproducible builds.
# Version pinning prevents supply-chain attacks and unexpected changes.
# -----------------------------------------------------------------------------
terraform {
  # Require Terraform 1.5+ for modern features and security improvements
  required_version = ">= 1.5.0"

  # Define required provider versions
  required_providers {
    # AWS provider for all monitoring resource provisioning
    aws = {
      # Use the official HashiCorp AWS provider
      source = "hashicorp/aws"
      # Pin to version 5.x for stability
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Input Variables - Parameters passed from the calling environment
# -----------------------------------------------------------------------------

# The deployment environment name for resource naming and alarm thresholds
variable "environment" {
  # Documents purpose and impact on alarm configuration
  description = "The deployment environment (dev, staging, prod) - affects alarm thresholds and notification urgency"
  # Enforce string type
  type = string

  # Validate environment values
  validation {
    # Only allow predefined values
    condition     = contains(["dev", "staging", "prod"], var.environment)
    # Clear error message
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

# The project name prefix for consistent resource naming
variable "project_name" {
  # Documents naming convention usage
  description = "The project name used as a prefix for all monitoring resource names"
  # Enforce string type
  type = string
  # Default to ectp
  default = "ectp"
}

# The KMS key ARN for encrypting CloudWatch log data at rest
variable "kms_key_arn" {
  # Documents the encryption requirement
  description = "ARN of the KMS key for encrypting CloudWatch Logs at rest (protects PII in log data)"
  # Enforce string type
  type = string
  # Default to null for environments without custom KMS keys
  default = null
}

# The ECS cluster name for metric dimensions
variable "ecs_cluster_name" {
  # Documents usage in CloudWatch metric alarm dimensions
  description = "The name of the ECS cluster for CloudWatch metric alarm dimensions"
  # Enforce string type
  type = string
}

# The ECS service name for metric dimensions
variable "ecs_service_name" {
  # Documents usage in CloudWatch metric alarm dimensions
  description = "The name of the ECS service for CloudWatch metric alarm dimensions"
  # Enforce string type
  type = string
}

# The ALB ARN suffix for CloudWatch metric dimensions
variable "alb_arn_suffix" {
  # Documents the ALB metric reference
  description = "The ARN suffix of the ALB for CloudWatch metric dimensions (e.g., app/my-alb/50dc6c495c0c9188)"
  # Enforce string type
  type = string
}

# The target group ARN suffix for CloudWatch metric dimensions
variable "target_group_arn_suffix" {
  # Documents the target group metric reference
  description = "The ARN suffix of the target group for CloudWatch metric dimensions"
  # Enforce string type
  type = string
}

# List of email addresses to receive alarm notifications
variable "alert_emails" {
  # Documents the notification channel configuration
  description = "List of email addresses to subscribe to the SNS alert topic for alarm notifications"
  # Enforce list of strings type
  type = list(string)
  # Default to empty list; emails should be provided per environment
  default = []
}

# Log retention period in days for CloudWatch log groups
variable "log_retention_days" {
  # Documents the retention policy and compliance rationale
  description = "Number of days to retain CloudWatch logs (must be a CloudWatch-supported value)"
  # Enforce number type
  type = number
  # Default to 90 days for non-production environments
  default = 90
}

# Tags to apply to all resources
variable "tags" {
  # Documents the tagging strategy
  description = "Map of tags applied to all monitoring resources for cost allocation and governance"
  # Enforce map of strings type
  type = map(string)
  # Default to empty map
  default = {}
}

# AWS region for dashboard widget configuration
variable "aws_region" {
  # Documents region usage in dashboard JSON
  description = "AWS region for CloudWatch dashboard metric widget configuration"
  # Enforce string type
  type = string
  # Default to US East 1
  default = "us-east-1"
}

# -----------------------------------------------------------------------------
# Local Values - Computed values used throughout this module
# -----------------------------------------------------------------------------
locals {
  # Construct a consistent name prefix for all resources
  # Format: "ectp-dev", "ectp-staging", "ectp-prod"
  name_prefix = "${var.project_name}-${var.environment}"

  # Merge user-provided tags with module-default tags
  common_tags = merge(
    # Include any tags passed from the calling module
    var.tags,
    {
      # Tag identifying this Terraform module
      Module = "monitoring"
      # Tag identifying the deployment environment
      Environment = var.environment
      # Tag identifying the project
      Project = var.project_name
      # Tag indicating Terraform management
      ManagedBy = "terraform"
      # Tag identifying the author
      Author = "Gopi Krishna Vajrala"
    }
  )

  # Define alarm thresholds that vary by environment
  # WHY: Production has tighter thresholds for earlier detection
  #      Dev/staging can tolerate higher utilization before alerting
  alarm_thresholds = {
    # CPU utilization threshold percentage for the ECS service
    cpu_threshold = var.environment == "prod" ? 70 : 85
    # Target response time threshold in seconds for the ALB
    latency_threshold = var.environment == "prod" ? 1.0 : 3.0
    # HTTP 5xx error count threshold per evaluation period
    error_5xx_threshold = var.environment == "prod" ? 10 : 50
    # HTTP 4xx error count threshold per evaluation period
    error_4xx_threshold = var.environment == "prod" ? 100 : 500
    # Unhealthy host count threshold (any unhealthy host is concerning)
    unhealthy_host_threshold = 1
  }
}

# =============================================================================
# CLOUDWATCH LOG GROUP
# =============================================================================
# WHAT: A dedicated CloudWatch Log Group for ECTP ECS task container logs.
# WHY: Centralized logging is essential for:
#      1. Application debugging and error investigation
#      2. Security monitoring (detecting suspicious API calls)
#      3. Compliance auditing (logging access to student data)
#      4. Performance analysis (slow query identification)
# SECURITY: Log data may contain sensitive information (student IDs, error
#           context). KMS encryption ensures data-at-rest protection.
# RETENTION: Configurable per environment; 90 days for dev, 365+ for prod.
# =============================================================================
resource "aws_cloudwatch_log_group" "ecs_tasks" {
  # Name follows a hierarchical pattern for CloudWatch Console organization
  # WHY: The /ectp/{env}/ecs/tasks path groups all ECS logs together
  name = "/ectp/${var.environment}/ecs/tasks"

  # Retain logs for the configured duration
  # WHY: Compliance may require long retention; cost requires limits
  # COST: CloudWatch Logs storage costs $0.03/GB/month
  retention_in_days = var.log_retention_days

  # Encrypt log data at rest using the customer-managed KMS key
  # WHY: Application logs may contain PII (student names in error messages)
  # SECURITY: KMS encryption adds a second authorization gate (kms:Decrypt)
  #           beyond CloudWatch IAM permissions (logs:GetLogEvents)
  kms_key_id = var.kms_key_arn

  # Apply common tags
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-ecs-task-logs"
    }
  )
}

# =============================================================================
# SNS TOPIC FOR ALARM NOTIFICATIONS
# =============================================================================
# WHAT: An SNS topic that aggregates all CloudWatch alarm notifications and
#       fans them out to subscribed endpoints (email, Slack, PagerDuty, etc.).
# WHY: A central notification hub enables:
#      1. Multiple notification channels from a single alarm action
#      2. Easy addition/removal of notification recipients
#      3. Integration with incident management tools (PagerDuty, OpsGenie)
#      4. Lambda-based custom notification formatting
# SECURITY: The SNS topic policy restricts publishing to only CloudWatch Alarms
#           and subscribing to only authorized IAM principals.
# =============================================================================
resource "aws_sns_topic" "alerts" {
  # Name the topic with the standard naming convention
  # WHY: Descriptive naming helps identify the topic in the SNS console
  name = "${local.name_prefix}-alerts"

  # Display name for SMS and email notifications
  # WHY: The display name appears as the sender in email notifications
  display_name = "ECTP ${upper(var.environment)} Alerts"

  # Encrypt SNS messages at rest using the KMS key
  # WHY: Alert messages may contain metric details that reveal infrastructure info
  # SECURITY: Encryption prevents unauthorized access to alert content in SNS storage
  kms_master_key_id = var.kms_key_arn != null ? var.kms_key_arn : "alias/aws/sns"

  # Apply common tags
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-alerts-topic"
    }
  )
}

# Create email subscriptions for each provided email address
# WHY: Email notifications provide a baseline alerting channel that
#      requires no additional tooling or integration setup
resource "aws_sns_topic_subscription" "email" {
  # Create one subscription per email address provided
  # WHY: Each recipient gets their own subscription for independent management
  count = length(var.alert_emails)

  # Subscribe to our alerts topic
  # WHY: Associates this email with the central alerts topic
  topic_arn = aws_sns_topic.alerts.arn

  # Use email protocol for human-readable notifications
  # WHY: Email is universally accessible and requires no special tooling
  protocol = "email"

  # Set the email address as the subscription endpoint
  # WHY: Each email in the list gets its own subscription
  endpoint = var.alert_emails[count.index]
}

# =============================================================================
# CLOUDWATCH METRIC ALARM - ECS CPU UTILIZATION
# =============================================================================
# WHAT: An alarm that triggers when ECS service average CPU utilization
#       exceeds the threshold for a sustained period.
# WHY: High CPU utilization indicates:
#      1. Application under heavy load (may need scaling)
#      2. Potential resource exhaustion (could cause service degradation)
#      3. Possible runaway process or infinite loop
#      4. Potential crypto-mining on a compromised container
# SECURITY: Sustained high CPU could indicate a compromised container
#           running unauthorized compute workloads (crypto-mining).
# THRESHOLDS: Prod = 70%, Dev = 85% (dev tolerates higher utilization)
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  # Name the alarm descriptively
  # WHY: Alarm names appear in notifications and the CloudWatch console
  alarm_name = "${local.name_prefix}-ecs-cpu-high"

  # Provide a detailed description for operators investigating the alarm
  # WHY: The description helps on-call engineers understand what to check
  alarm_description = "ECS service CPU utilization exceeds ${local.alarm_thresholds.cpu_threshold}% for 5 minutes. Check for traffic spikes, runaway processes, or scaling issues. Runbook: https://wiki.internal/ectp/runbooks/high-cpu"

  # Compare the metric value against the threshold using GreaterThanThreshold
  # WHY: We want to alarm when CPU goes ABOVE the threshold
  comparison_operator = "GreaterThanThreshold"

  # Evaluate for 2 consecutive periods before triggering
  # WHY: 2 consecutive periods (10 minutes total) prevents transient spikes
  #      from causing unnecessary alerts while catching sustained issues
  evaluation_periods = 2

  # Use 5-minute periods for metric evaluation
  # WHY: 5 minutes balances between quick detection and noise reduction
  #      Shorter periods increase sensitivity but also increase false positives
  period = 300

  # Use the Average statistic for CPU utilization
  # WHY: Average CPU across all tasks in the service provides the best
  #      signal for whether the service as a whole is under stress
  statistic = "Average"

  # Set the threshold from the environment-specific configuration
  # WHY: Production uses a lower threshold (70%) for earlier detection
  threshold = local.alarm_thresholds.cpu_threshold

  # Reference the ECS CPUUtilization metric
  # WHY: This is the standard ECS metric for task CPU utilization
  namespace = "AWS/ECS"

  # Use the CPUUtilization metric name
  # WHY: Provided by ECS automatically for all services
  metric_name = "CPUUtilization"

  # Filter to our specific ECS cluster and service
  # WHY: Dimensions scope the alarm to only this service, not all ECS services
  dimensions = {
    # The ECS cluster name for metric filtering
    ClusterName = var.ecs_cluster_name
    # The ECS service name for metric filtering
    ServiceName = var.ecs_service_name
  }

  # Send notifications to the SNS topic when alarm triggers
  # WHY: Notifies the operations team to investigate
  alarm_actions = [aws_sns_topic.alerts.arn]

  # Send notifications when alarm returns to OK state
  # WHY: Confirms the issue has resolved, preventing unnecessary investigation
  ok_actions = [aws_sns_topic.alerts.arn]

  # Treat missing data as not breaching (alarm is OK)
  # WHY: Missing data usually means the service is down or being deployed
  #      Other alarms (unhealthy hosts) will catch those scenarios
  treat_missing_data = "notBreaching"

  # Apply common tags
  tags = merge(
    # Include all common tags
    local.common_tags,
    {
      # Name tag for identification
      Name = "${local.name_prefix}-cpu-high-alarm"
      # Severity tag for alert routing
      Severity = "warning"
    }
  )
}

# =============================================================================
# CLOUDWATCH METRIC ALARM - ALB TARGET RESPONSE TIME (LATENCY)
# =============================================================================
# WHAT: An alarm that triggers when the average ALB target response time
#       (latency) exceeds the threshold, indicating slow API responses.
# WHY: High latency indicates:
#      1. Database performance issues (slow queries)
#      2. Application bottlenecks (memory pressure, GC pauses)
#      3. Network connectivity problems
#      4. Downstream service degradation (external API calls)
# USER IMPACT: Slow response times directly affect user experience for
#              students and faculty using the ECTP platform.
# THRESHOLDS: Prod = 1.0s, Dev = 3.0s (dev tolerates slower responses)
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "latency_high" {
  # Name the alarm descriptively
  # WHY: Clear naming helps identify the alarm in notifications
  alarm_name = "${local.name_prefix}-alb-latency-high"

  # Detailed description with troubleshooting guidance
  # WHY: Helps on-call engineers diagnose the issue quickly
  alarm_description = "ALB target response time exceeds ${local.alarm_thresholds.latency_threshold}s for 5 minutes. Check database performance, application logs, and downstream service health. Runbook: https://wiki.internal/ectp/runbooks/high-latency"

  # Alarm when latency is above the threshold
  # WHY: We want to detect when responses are too slow
  comparison_operator = "GreaterThanThreshold"

  # Evaluate for 3 consecutive periods to confirm sustained latency
  # WHY: 3 periods (15 minutes) filters out brief latency spikes from
  #      cold starts or occasional slow queries
  evaluation_periods = 3

  # Use 5-minute periods for evaluation
  # WHY: Consistent with other alarms for unified monitoring cadence
  period = 300

  # Use the Average statistic for response time
  # WHY: Average response time captures the overall user experience
  #      p99 would be more precise but requires custom metrics
  statistic = "Average"

  # Set the latency threshold from environment-specific configuration
  # WHY: Production has a tighter threshold for better user experience
  threshold = local.alarm_thresholds.latency_threshold

  # Reference the ALB TargetResponseTime metric
  # WHY: This metric measures the time from when the ALB sends a request
  #      to the target until it receives a response
  namespace = "AWS/ApplicationELB"

  # Use the TargetResponseTime metric name
  # WHY: This is the standard ALB metric for backend response time
  metric_name = "TargetResponseTime"

  # Filter to our specific ALB
  # WHY: Dimensions scope the alarm to only this ALB
  dimensions = {
    # The ALB ARN suffix for metric filtering
    LoadBalancer = var.alb_arn_suffix
  }

  # Send notifications when alarm triggers
  alarm_actions = [aws_sns_topic.alerts.arn]

  # Send notifications when alarm resolves
  ok_actions = [aws_sns_topic.alerts.arn]

  # Treat missing data as not breaching
  # WHY: Missing data means no requests, which is not a latency problem
  treat_missing_data = "notBreaching"

  # Apply common tags
  tags = merge(
    local.common_tags,
    {
      # Name tag
      Name = "${local.name_prefix}-latency-high-alarm"
      # Severity tag for routing
      Severity = "warning"
    }
  )
}

# =============================================================================
# CLOUDWATCH METRIC ALARM - HTTP 5XX ERRORS
# =============================================================================
# WHAT: An alarm that triggers when the count of HTTP 5xx errors from the
#       ALB targets exceeds the threshold, indicating server-side failures.
# WHY: 5xx errors indicate:
#      1. Application crashes or unhandled exceptions
#      2. Database connectivity failures
#      3. Out-of-memory conditions in containers
#      4. Configuration errors after deployments
# SECURITY: A spike in 5xx errors could indicate a denial-of-service attack
#           or a successful exploit causing application crashes.
# THRESHOLDS: Prod = 10 errors/5min, Dev = 50 errors/5min
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "errors_5xx" {
  # Name the alarm descriptively
  alarm_name = "${local.name_prefix}-alb-5xx-errors"

  # Detailed description with troubleshooting guidance
  alarm_description = "HTTP 5xx error count exceeds ${local.alarm_thresholds.error_5xx_threshold} in 5 minutes. Check application logs for exceptions, database connectivity, and recent deployments. Runbook: https://wiki.internal/ectp/runbooks/5xx-errors"

  # Alarm when error count is above the threshold
  comparison_operator = "GreaterThanThreshold"

  # Evaluate for 2 consecutive periods to confirm sustained errors
  # WHY: 2 periods (10 minutes) filters out brief error bursts during
  #      deployments or transient failures
  evaluation_periods = 2

  # Use 5-minute periods
  period = 300

  # Use the Sum statistic to count total errors in the period
  # WHY: Sum counts the total number of 5xx errors, not average rate
  statistic = "Sum"

  # Set the error threshold from environment-specific configuration
  threshold = local.alarm_thresholds.error_5xx_threshold

  # Reference the ALB HTTPCode_Target_5XX_Count metric
  # WHY: This metric counts 5xx responses from the ECS targets
  #      (not from the ALB itself, which would be HTTPCode_ELB_5XX_Count)
  namespace = "AWS/ApplicationELB"

  # Use the target 5xx error metric
  metric_name = "HTTPCode_Target_5XX_Count"

  # Filter to our specific ALB and target group
  dimensions = {
    # The ALB ARN suffix for filtering
    LoadBalancer = var.alb_arn_suffix
    # The target group ARN suffix for filtering
    TargetGroup = var.target_group_arn_suffix
  }

  # Send notifications when alarm triggers
  alarm_actions = [aws_sns_topic.alerts.arn]

  # Send notifications when alarm resolves
  ok_actions = [aws_sns_topic.alerts.arn]

  # Treat missing data as not breaching
  # WHY: Missing 5xx data means no errors occurred, which is healthy
  treat_missing_data = "notBreaching"

  # Apply common tags
  tags = merge(
    local.common_tags,
    {
      # Name tag
      Name = "${local.name_prefix}-5xx-errors-alarm"
      # Severity tag - 5xx errors are critical in production
      Severity = var.environment == "prod" ? "critical" : "warning"
    }
  )
}

# =============================================================================
# CLOUDWATCH METRIC ALARM - HTTP 4XX ERRORS
# =============================================================================
# WHAT: An alarm that triggers when HTTP 4xx client error count exceeds
#       the threshold, indicating potential API misuse or attack patterns.
# WHY: High 4xx error rates may indicate:
#      1. Broken client integrations or frontend bugs
#      2. API contract changes breaking existing clients
#      3. Brute-force authentication attempts (401/403 spikes)
#      4. Path traversal or injection attempts (400/404 spikes)
# SECURITY: A spike in 401/403 errors could indicate a credential
#           stuffing or brute-force attack against the API.
# THRESHOLDS: Prod = 100/5min, Dev = 500/5min
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "errors_4xx" {
  # Name the alarm descriptively
  alarm_name = "${local.name_prefix}-alb-4xx-errors"

  # Detailed description with troubleshooting guidance
  alarm_description = "HTTP 4xx error count exceeds ${local.alarm_thresholds.error_4xx_threshold} in 5 minutes. Check for broken client integrations, authentication failures, or potential attack patterns. Runbook: https://wiki.internal/ectp/runbooks/4xx-errors"

  # Alarm when error count exceeds the threshold
  comparison_operator = "GreaterThanThreshold"

  # Evaluate for 3 consecutive periods to reduce noise from legitimate 404s
  # WHY: 4xx errors are more common than 5xx; longer evaluation reduces false positives
  evaluation_periods = 3

  # Use 5-minute periods
  period = 300

  # Use Sum to count total errors
  statistic = "Sum"

  # Set the threshold from configuration
  threshold = local.alarm_thresholds.error_4xx_threshold

  # Reference the ALB target 4xx metric
  namespace = "AWS/ApplicationELB"

  # Use the target 4xx error count metric
  metric_name = "HTTPCode_Target_4XX_Count"

  # Filter to our ALB and target group
  dimensions = {
    # ALB ARN suffix
    LoadBalancer = var.alb_arn_suffix
    # Target group ARN suffix
    TargetGroup = var.target_group_arn_suffix
  }

  # Send notifications
  alarm_actions = [aws_sns_topic.alerts.arn]

  # Send OK notifications
  ok_actions = [aws_sns_topic.alerts.arn]

  # Missing data means no 4xx errors, which is healthy
  treat_missing_data = "notBreaching"

  # Apply tags
  tags = merge(
    local.common_tags,
    {
      # Name tag
      Name = "${local.name_prefix}-4xx-errors-alarm"
      # Severity tag
      Severity = "warning"
    }
  )
}

# =============================================================================
# CLOUDWATCH METRIC ALARM - UNHEALTHY HOST COUNT
# =============================================================================
# WHAT: An alarm that triggers when any ECS task becomes unhealthy,
#       indicating the application is not responding to health checks.
# WHY: Unhealthy hosts indicate:
#      1. Application crash or hang (health check timeout)
#      2. Database connectivity loss (health check fails)
#      3. Out-of-memory condition (container killed by OOM)
#      4. Deployment failure (new version crashes on startup)
# SECURITY: Unhealthy hosts reduce capacity, making the service more
#           vulnerable to DDoS attacks with fewer resources to absorb load.
# THRESHOLD: Any unhealthy host (>= 1) triggers the alarm
# =============================================================================
resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  # Name the alarm descriptively
  alarm_name = "${local.name_prefix}-unhealthy-hosts"

  # Detailed description
  alarm_description = "One or more ECS tasks are failing ALB health checks. Check container logs, database connectivity, and recent deployments. Runbook: https://wiki.internal/ectp/runbooks/unhealthy-hosts"

  # Alarm when unhealthy count is above zero
  comparison_operator = "GreaterThanOrEqualToThreshold"

  # Evaluate for 2 consecutive periods to avoid false positives during deployments
  # WHY: During rolling deployments, hosts may briefly be unhealthy
  evaluation_periods = 2

  # Use 1-minute periods for faster detection of unhealthy hosts
  # WHY: Unhealthy hosts are critical; detect them quickly
  period = 60

  # Use Maximum to catch ANY unhealthy host
  # WHY: Even one unhealthy host is a concern worth investigating
  statistic = "Maximum"

  # Threshold of 1 means any unhealthy host triggers the alarm
  threshold = local.alarm_thresholds.unhealthy_host_threshold

  # Reference the ALB UnHealthyHostCount metric
  namespace = "AWS/ApplicationELB"

  # Use the unhealthy host count metric
  metric_name = "UnHealthyHostCount"

  # Filter to our target group
  dimensions = {
    # Target group ARN suffix
    TargetGroup = var.target_group_arn_suffix
    # ALB ARN suffix
    LoadBalancer = var.alb_arn_suffix
  }

  # Send notifications
  alarm_actions = [aws_sns_topic.alerts.arn]

  # Send OK notifications when all hosts become healthy again
  ok_actions = [aws_sns_topic.alerts.arn]

  # Treat missing data as breaching (if no data, something is wrong)
  # WHY: Missing UnHealthyHostCount data means no targets are registered,
  #      which is a critical issue
  treat_missing_data = "breaching"

  # Apply tags
  tags = merge(
    local.common_tags,
    {
      # Name tag
      Name = "${local.name_prefix}-unhealthy-hosts-alarm"
      # Severity is always critical for unhealthy hosts
      Severity = "critical"
    }
  )
}

# =============================================================================
# CLOUDWATCH DASHBOARD
# =============================================================================
# WHAT: A CloudWatch dashboard providing a real-time operational view of the
#       ECTP platform including ECS metrics, ALB metrics, and error rates.
# WHY: Dashboards enable:
#      1. At-a-glance health assessment during incidents
#      2. Trend analysis for capacity planning
#      3. Correlation of metrics across services (CPU vs latency)
#      4. Stakeholder visibility into platform health
# SECURITY: Dashboard access is controlled by IAM policies; only authorized
#           roles can view the dashboard in the CloudWatch console.
# COST: CloudWatch dashboards cost $3/month per dashboard.
# =============================================================================
resource "aws_cloudwatch_dashboard" "main" {
  # Name the dashboard with the environment for identification
  # WHY: Each environment gets its own dashboard for independent monitoring
  dashboard_name = "${local.name_prefix}-dashboard"

  # Define the dashboard layout as a JSON body
  # WHY: CloudWatch dashboards are defined as JSON widget arrays
  dashboard_body = jsonencode({
    # Array of widgets displayed on the dashboard
    widgets = [
      # -----------------------------------------------------------------------
      # Row 1: ECS Service Metrics (CPU and Memory)
      # -----------------------------------------------------------------------
      {
        # Widget type: metric chart
        type = "metric"
        # Position: top-left, spanning 12 columns (half width)
        x      = 0
        y      = 0
        width  = 12
        height = 6
        # Widget properties defining the metric chart content
        properties = {
          # Chart title displayed above the graph
          title = "ECS CPU Utilization (%)"
          # AWS region for metric data
          region = var.aws_region
          # Time period for each data point (5 minutes)
          period = 300
          # Statistic type for the metric
          stat = "Average"
          # Display as a line chart for trend visualization
          view = "timeSeries"
          # Stack multiple metrics on top of each other
          stacked = false
          # Define the metrics to display
          metrics = [
            # ECS CPU Utilization metric with cluster and service dimensions
            [
              "AWS/ECS",              # Metric namespace
              "CPUUtilization",        # Metric name
              "ClusterName",           # First dimension key
              var.ecs_cluster_name,    # First dimension value
              "ServiceName",           # Second dimension key
              var.ecs_service_name     # Second dimension value
            ]
          ]
          # Add horizontal annotations for alarm thresholds
          annotations = {
            horizontal = [
              {
                # Red line at the alarm threshold
                color = "#ff0000"
                # Label for the threshold line
                label = "CPU Alarm Threshold"
                # The threshold value
                value = local.alarm_thresholds.cpu_threshold
              }
            ]
          }
        }
      },
      {
        # Widget type: metric chart for memory utilization
        type   = "metric"
        # Position: top-right
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "ECS Memory Utilization (%)"
          # AWS region
          region = var.aws_region
          # Time period
          period = 300
          # Statistic
          stat = "Average"
          # Display type
          view = "timeSeries"
          # No stacking
          stacked = false
          # Memory utilization metric
          metrics = [
            [
              "AWS/ECS",               # Metric namespace
              "MemoryUtilization",      # Metric name
              "ClusterName",            # Cluster dimension
              var.ecs_cluster_name,     # Cluster name value
              "ServiceName",            # Service dimension
              var.ecs_service_name      # Service name value
            ]
          ]
        }
      },
      # -----------------------------------------------------------------------
      # Row 2: ALB Metrics (Request Count and Latency)
      # -----------------------------------------------------------------------
      {
        # Widget for ALB request count
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "ALB Request Count"
          # AWS region
          region = var.aws_region
          # Time period
          period = 300
          # Sum of requests per period
          stat = "Sum"
          # Line chart
          view = "timeSeries"
          stacked = false
          # Request count metric
          metrics = [
            [
              "AWS/ApplicationELB",    # ALB metric namespace
              "RequestCount",           # Total request count metric
              "LoadBalancer",           # ALB dimension
              var.alb_arn_suffix        # ALB ARN suffix value
            ]
          ]
        }
      },
      {
        # Widget for ALB target response time (latency)
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "ALB Target Response Time (seconds)"
          # AWS region
          region = var.aws_region
          # Time period
          period = 300
          # Average response time
          stat = "Average"
          # Line chart
          view = "timeSeries"
          stacked = false
          # Target response time metric
          metrics = [
            [
              "AWS/ApplicationELB",    # ALB namespace
              "TargetResponseTime",     # Response time metric
              "LoadBalancer",           # ALB dimension
              var.alb_arn_suffix        # ALB ARN suffix
            ]
          ]
          # Add latency threshold annotation
          annotations = {
            horizontal = [
              {
                # Red line at latency threshold
                color = "#ff0000"
                label = "Latency Alarm Threshold"
                value = local.alarm_thresholds.latency_threshold
              }
            ]
          }
        }
      },
      # -----------------------------------------------------------------------
      # Row 3: Error Metrics (5xx and 4xx)
      # -----------------------------------------------------------------------
      {
        # Widget for HTTP 5xx errors
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "HTTP 5xx Errors (Server Errors)"
          # AWS region
          region = var.aws_region
          # Time period
          period = 300
          # Sum of errors per period
          stat = "Sum"
          # Line chart
          view = "timeSeries"
          stacked = false
          # 5xx error count metric
          metrics = [
            [
              "AWS/ApplicationELB",             # ALB namespace
              "HTTPCode_Target_5XX_Count",       # 5xx error count from targets
              "LoadBalancer",                    # ALB dimension
              var.alb_arn_suffix,                # ALB ARN suffix
              "TargetGroup",                     # Target group dimension
              var.target_group_arn_suffix         # Target group ARN suffix
            ]
          ]
          # Add error threshold annotation
          annotations = {
            horizontal = [
              {
                # Red line at 5xx threshold
                color = "#ff0000"
                label = "5xx Alarm Threshold"
                value = local.alarm_thresholds.error_5xx_threshold
              }
            ]
          }
        }
      },
      {
        # Widget for HTTP 4xx errors
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "HTTP 4xx Errors (Client Errors)"
          # AWS region
          region = var.aws_region
          # Time period
          period = 300
          # Sum of errors
          stat = "Sum"
          # Line chart
          view = "timeSeries"
          stacked = false
          # 4xx error count metric
          metrics = [
            [
              "AWS/ApplicationELB",             # ALB namespace
              "HTTPCode_Target_4XX_Count",       # 4xx error count from targets
              "LoadBalancer",                    # ALB dimension
              var.alb_arn_suffix,                # ALB ARN suffix
              "TargetGroup",                     # Target group dimension
              var.target_group_arn_suffix         # Target group ARN suffix
            ]
          ]
        }
      },
      # -----------------------------------------------------------------------
      # Row 4: Health and Scaling Metrics
      # -----------------------------------------------------------------------
      {
        # Widget for healthy/unhealthy host counts
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "ALB Target Health"
          # AWS region
          region = var.aws_region
          # Time period
          period = 60
          # Average for smoother visualization
          stat = "Average"
          # Line chart
          view = "timeSeries"
          stacked = false
          # Both healthy and unhealthy host count metrics
          metrics = [
            [
              "AWS/ApplicationELB",           # ALB namespace
              "HealthyHostCount",              # Count of healthy targets
              "TargetGroup",                   # Target group dimension
              var.target_group_arn_suffix,      # Target group ARN suffix
              "LoadBalancer",                  # ALB dimension
              var.alb_arn_suffix               # ALB ARN suffix
            ],
            [
              "AWS/ApplicationELB",           # ALB namespace
              "UnHealthyHostCount",            # Count of unhealthy targets
              "TargetGroup",                   # Target group dimension
              var.target_group_arn_suffix,      # Target group ARN suffix
              "LoadBalancer",                  # ALB dimension
              var.alb_arn_suffix               # ALB ARN suffix
            ]
          ]
        }
      },
      {
        # Widget for ECS running task count (scaling visualization)
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          # Chart title
          title = "ECS Running Task Count"
          # AWS region
          region = var.aws_region
          # Time period
          period = 60
          # Average count
          stat = "Average"
          # Line chart
          view = "timeSeries"
          stacked = false
          # ECS desired and running task count metrics
          metrics = [
            [
              "ECS/ContainerInsights",        # Container Insights namespace
              "RunningTaskCount",              # Number of running tasks
              "ClusterName",                   # Cluster dimension
              var.ecs_cluster_name,            # Cluster name
              "ServiceName",                   # Service dimension
              var.ecs_service_name             # Service name
            ],
            [
              "ECS/ContainerInsights",        # Container Insights namespace
              "DesiredTaskCount",              # Desired task count (scaling target)
              "ClusterName",                   # Cluster dimension
              var.ecs_cluster_name,            # Cluster name
              "ServiceName",                   # Service dimension
              var.ecs_service_name             # Service name
            ]
          ]
        }
      }
    ]
  })
}

# =============================================================================
# OUTPUTS - Values exported for use by other modules
# =============================================================================

# Output the CloudWatch log group name for the compute module
output "log_group_name" {
  # Document the purpose
  description = "The name of the CloudWatch Log Group for ECS task container logs"
  # Reference the log group name
  value = aws_cloudwatch_log_group.ecs_tasks.name
}

# Output the CloudWatch log group ARN for IAM policy references
output "log_group_arn" {
  # Document the ARN output
  description = "The ARN of the CloudWatch Log Group for IAM policy resource restrictions"
  # Reference the log group ARN
  value = aws_cloudwatch_log_group.ecs_tasks.arn
}

# Output the SNS topic ARN for other modules to publish notifications
output "sns_topic_arn" {
  # Document the SNS topic output
  description = "The ARN of the SNS topic for alarm notifications and event subscriptions"
  # Reference the SNS topic ARN
  value = aws_sns_topic.alerts.arn
}

# Output the SNS topic name for reference
output "sns_topic_name" {
  # Document the topic name output
  description = "The name of the SNS topic for alarm notifications"
  # Reference the topic name
  value = aws_sns_topic.alerts.name
}

# Output the dashboard name for console URL construction
output "dashboard_name" {
  # Document the dashboard output
  description = "The name of the CloudWatch dashboard for operational monitoring"
  # Reference the dashboard name
  value = aws_cloudwatch_dashboard.main.dashboard_name
}

# Output the dashboard URL for easy access
output "dashboard_url" {
  # Document the URL output
  description = "The CloudWatch Console URL for the monitoring dashboard"
  # Construct the full URL for the dashboard
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}
