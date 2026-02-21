###############################################################################
# Netflix Real-Time LLM Personalization & Inference Platform
# Cache Module - Outputs
###############################################################################

# -----------------------------------------------------------------------------
# KV Cache Replication Group
# -----------------------------------------------------------------------------

output "kv_cache_replication_group_id" {
  description = "ID of the KV cache Redis replication group"
  value       = aws_elasticache_replication_group.kv_cache.id
}

output "kv_cache_primary_endpoint" {
  description = "Primary endpoint address for the KV cache (write operations)"
  value       = aws_elasticache_replication_group.kv_cache.primary_endpoint_address
}

output "kv_cache_reader_endpoint" {
  description = "Reader endpoint address for the KV cache (read operations)"
  value       = aws_elasticache_replication_group.kv_cache.reader_endpoint_address
}

output "kv_cache_port" {
  description = "Port number for the KV cache Redis cluster"
  value       = aws_elasticache_replication_group.kv_cache.port
}

output "kv_cache_arn" {
  description = "ARN of the KV cache replication group"
  value       = aws_elasticache_replication_group.kv_cache.arn
}

# -----------------------------------------------------------------------------
# Session Memory Replication Group
# -----------------------------------------------------------------------------

output "session_memory_replication_group_id" {
  description = "ID of the session memory Redis replication group"
  value       = aws_elasticache_replication_group.session_memory.id
}

output "session_memory_primary_endpoint" {
  description = "Primary endpoint address for session memory (write operations)"
  value       = aws_elasticache_replication_group.session_memory.primary_endpoint_address
}

output "session_memory_reader_endpoint" {
  description = "Reader endpoint address for session memory (read operations)"
  value       = aws_elasticache_replication_group.session_memory.reader_endpoint_address
}

output "session_memory_port" {
  description = "Port number for the session memory Redis cluster"
  value       = aws_elasticache_replication_group.session_memory.port
}

output "session_memory_arn" {
  description = "ARN of the session memory replication group"
  value       = aws_elasticache_replication_group.session_memory.arn
}

# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------

output "redis_security_group_id" {
  description = "Security group ID for Redis clusters"
  value       = aws_security_group.redis.id
}

# -----------------------------------------------------------------------------
# Connection Strings (for application configuration)
# -----------------------------------------------------------------------------

output "kv_cache_connection_string" {
  description = "Connection string for KV cache (host:port format)"
  value       = "${aws_elasticache_replication_group.kv_cache.primary_endpoint_address}:${aws_elasticache_replication_group.kv_cache.port}"
}

output "session_memory_connection_string" {
  description = "Connection string for session memory (host:port format)"
  value       = "${aws_elasticache_replication_group.session_memory.primary_endpoint_address}:${aws_elasticache_replication_group.session_memory.port}"
}
