###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Cache Module - ElastiCache Redis for KV Cache & Session Memory
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_region" "current" {}

# -----------------------------------------------------------------------------
# ElastiCache Subnet Group
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.project_name}-${var.environment}-redis-${var.region_short}"
  description = "Subnet group for Redis KV cache and session memory in ${var.region_short}"
  subnet_ids  = var.subnet_ids

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-redis-subnet-${var.region_short}"
    Component = "cache"
  })
}

# -----------------------------------------------------------------------------
# Redis Parameter Group - Optimized for KV Cache Workload
# -----------------------------------------------------------------------------

resource "aws_elasticache_parameter_group" "redis" {
  name        = "${var.project_name}-${var.environment}-redis-params-${var.region_short}"
  family      = "redis7"
  description = "Optimized Redis parameters for LLM KV cache and user session memory"

  # Memory management optimized for KV cache workload
  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lfu"
  }

  # Enable active defragmentation for large KV entries
  parameter {
    name  = "activedefrag"
    value = "yes"
  }

  parameter {
    name  = "active-defrag-enabled"
    value = "yes"
  }

  # Optimize for large values (model embeddings, KV cache tensors)
  parameter {
    name  = "hash-max-ziplist-entries"
    value = "128"
  }

  parameter {
    name  = "hash-max-ziplist-value"
    value = "256"
  }

  # Connection and timeout tuning
  parameter {
    name  = "timeout"
    value = "300"
  }

  parameter {
    name  = "tcp-keepalive"
    value = "60"
  }

  # Disable persistence for cache-only workload (speed over durability)
  parameter {
    name  = "appendonly"
    value = "no"
  }

  # Lazy free for background memory reclamation
  parameter {
    name  = "lazyfree-lazy-eviction"
    value = "yes"
  }

  parameter {
    name  = "lazyfree-lazy-expire"
    value = "yes"
  }

  parameter {
    name  = "lazyfree-lazy-server-del"
    value = "yes"
  }

  # Pub/Sub for cache invalidation across inference nodes
  parameter {
    name  = "notify-keyspace-events"
    value = "Ex"
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-redis-params-${var.region_short}"
    Component = "cache"
  })
}

# -----------------------------------------------------------------------------
# Redis Parameter Group - Session Memory (with persistence)
# -----------------------------------------------------------------------------

resource "aws_elasticache_parameter_group" "redis_session" {
  name        = "${var.project_name}-${var.environment}-redis-session-${var.region_short}"
  family      = "redis7"
  description = "Redis parameters for user session memory with persistence enabled"

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  # Enable AOF persistence for session data durability
  parameter {
    name  = "appendonly"
    value = "yes"
  }

  parameter {
    name  = "appendfsync"
    value = "everysec"
  }

  parameter {
    name  = "timeout"
    value = "600"
  }

  parameter {
    name  = "tcp-keepalive"
    value = "60"
  }

  parameter {
    name  = "lazyfree-lazy-eviction"
    value = "yes"
  }

  parameter {
    name  = "lazyfree-lazy-expire"
    value = "yes"
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-redis-session-params-${var.region_short}"
    Component = "session-cache"
  })
}

# -----------------------------------------------------------------------------
# Security Group - Redis Cache
# -----------------------------------------------------------------------------

resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-${var.environment}-redis-"
  description = "Security group for ElastiCache Redis clusters"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from inference nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  ingress {
    description     = "Redis cluster bus from inference nodes"
    from_port       = 16379
    to_port         = 16379
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.common_tags, {
    Name      = "${var.project_name}-${var.environment}-redis-sg-${var.region_short}"
    Component = "cache-security"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Replication Group - KV Cache (Primary)
# -----------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "kv_cache" {
  replication_group_id = "${var.project_name}-kv-${var.region_short}"
  description          = "Redis cluster for LLM KV cache - low-latency tensor storage"

  engine               = "redis"
  engine_version       = var.redis_version
  node_type            = var.kv_cache_node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.redis.name
  subnet_group_name    = aws_elasticache_subnet_group.redis.name

  # Multi-AZ with automatic failover
  automatic_failover_enabled = true
  multi_az_enabled           = true
  num_cache_clusters         = var.kv_cache_num_replicas + 1

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = var.enable_transit_encryption
  kms_key_id                 = var.kms_key_arn
  auth_token                 = var.redis_auth_token != "" ? var.redis_auth_token : null

  # Security
  security_group_ids = [aws_security_group.redis.id]

  # Maintenance
  maintenance_window       = var.maintenance_window
  snapshot_retention_limit = var.snapshot_retention_days
  snapshot_window          = var.snapshot_window
  auto_minor_version_upgrade = true

  # Notifications
  notification_topic_arn = var.notification_sns_topic_arn != "" ? var.notification_sns_topic_arn : null

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-kv-cache-${var.region_short}"
    Component  = "kv-cache"
    CostCenter = "ml-inference"
    Workload   = "llm-kv-cache"
  })

  lifecycle {
    ignore_changes = [num_cache_clusters]
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Replication Group - Session Memory
# -----------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "session_memory" {
  replication_group_id = "${var.project_name}-sess-${var.region_short}"
  description          = "Redis cluster for user session memory - personalization context"

  engine               = "redis"
  engine_version       = var.redis_version
  node_type            = var.session_node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.redis_session.name
  subnet_group_name    = aws_elasticache_subnet_group.redis.name

  # Multi-AZ with automatic failover
  automatic_failover_enabled = true
  multi_az_enabled           = true
  num_cache_clusters         = var.session_num_replicas + 1

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = var.enable_transit_encryption
  kms_key_id                 = var.kms_key_arn
  auth_token                 = var.redis_auth_token != "" ? var.redis_auth_token : null

  # Security
  security_group_ids = [aws_security_group.redis.id]

  # Maintenance
  maintenance_window       = var.maintenance_window
  snapshot_retention_limit = var.session_snapshot_retention_days
  snapshot_window          = var.snapshot_window
  auto_minor_version_upgrade = true

  # Notifications
  notification_topic_arn = var.notification_sns_topic_arn != "" ? var.notification_sns_topic_arn : null

  tags = merge(var.common_tags, {
    Name       = "${var.project_name}-${var.environment}-session-memory-${var.region_short}"
    Component  = "session-memory"
    CostCenter = "ml-inference"
    Workload   = "user-personalization"
  })

  lifecycle {
    ignore_changes = [num_cache_clusters]
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms - KV Cache
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "kv_cache_cpu" {
  alarm_name          = "${var.project_name}-${var.environment}-kv-cache-cpu-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "EngineCPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Average"
  threshold           = var.cpu_alarm_threshold
  alarm_description   = "KV cache Redis CPU utilization exceeds ${var.cpu_alarm_threshold}%"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.kv_cache.id
  }

  tags = merge(var.common_tags, {
    Component = "cache-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "kv_cache_memory" {
  alarm_name          = "${var.project_name}-${var.environment}-kv-cache-memory-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Average"
  threshold           = var.memory_alarm_threshold
  alarm_description   = "KV cache Redis memory usage exceeds ${var.memory_alarm_threshold}%"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.kv_cache.id
  }

  tags = merge(var.common_tags, {
    Component = "cache-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "kv_cache_evictions" {
  alarm_name          = "${var.project_name}-${var.environment}-kv-cache-evictions-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Sum"
  threshold           = var.eviction_alarm_threshold
  alarm_description   = "KV cache eviction rate exceeds ${var.eviction_alarm_threshold} per minute"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.kv_cache.id
  }

  tags = merge(var.common_tags, {
    Component = "cache-monitoring"
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms - Session Memory
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "session_cpu" {
  alarm_name          = "${var.project_name}-${var.environment}-session-cpu-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "EngineCPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Average"
  threshold           = var.cpu_alarm_threshold
  alarm_description   = "Session memory Redis CPU utilization exceeds ${var.cpu_alarm_threshold}%"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.session_memory.id
  }

  tags = merge(var.common_tags, {
    Component = "cache-monitoring"
  })
}

resource "aws_cloudwatch_metric_alarm" "session_replication_lag" {
  alarm_name          = "${var.project_name}-${var.environment}-session-repl-lag-${var.region_short}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "ReplicationLag"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Maximum"
  threshold           = var.replication_lag_threshold_seconds
  alarm_description   = "Session memory replication lag exceeds ${var.replication_lag_threshold_seconds}s"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.session_memory.id
  }

  tags = merge(var.common_tags, {
    Component = "cache-monitoring"
  })
}
