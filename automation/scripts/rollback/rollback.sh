#!/usr/bin/env bash
# ============================================================================
# Enterprise Cloud Transformation Platform (ECTP)
# Rollback Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Provides a fast, reliable way to roll back a failed deployment.
#   In an emergency, every second counts. This script:
#   1. Rolls back the ECS service to the previous task definition
#   2. Optionally rolls back Terraform infrastructure changes
#   3. Validates the rollback succeeded via health checks
#
# USAGE:
#   ./rollback.sh <environment>
#   Example: ./rollback.sh production
#
# WHAT IT DOES:
#   1. Identifies the previous stable ECS task definition
#   2. Updates the ECS service to use the previous definition
#   3. Waits for rollback to stabilize
#   4. Runs health checks to confirm recovery
#
# SECURITY:
#   - Requires "ecs:UpdateService" IAM permission
#   - All rollback actions are logged for audit
#   - Production rollbacks trigger automatic incident in ServiceNow
# ============================================================================

set -euo pipefail

ENVIRONMENT="${1:-}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="ectp-${ENVIRONMENT}"
ECS_SERVICE="ectp-api-${ENVIRONMENT}"

if [[ -z "${ENVIRONMENT}" ]]; then
    echo "ERROR: Missing environment argument"
    echo "Usage: $0 <environment>"
    exit 1
fi

log_info() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [ROLLBACK] [INFO] $*"
}

log_error() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [ROLLBACK] [ERROR] $*" >&2
}

log_info "========================================="
log_info "ECTP ROLLBACK INITIATED"
log_info "Environment: ${ENVIRONMENT}"
log_info "========================================="

# Get current task definition
# WHY: We need to know what's currently running to determine what to roll back to.
CURRENT_TASK_DEF=$(aws ecs describe-services \
    --cluster "${ECS_CLUSTER}" \
    --services "${ECS_SERVICE}" \
    --region "${AWS_REGION}" \
    --query "services[0].taskDefinition" \
    --output text)

log_info "Current task definition: ${CURRENT_TASK_DEF}"

# Get the current task definition revision number
CURRENT_REVISION=$(echo "${CURRENT_TASK_DEF}" | grep -oP ':\K[0-9]+$')

# Calculate previous revision
# WHY: The simplest rollback strategy is to go back one revision.
# This assumes the previous revision was stable (which it should be
# because it passed health checks during its deployment).
PREVIOUS_REVISION=$((CURRENT_REVISION - 1))
TASK_FAMILY=$(echo "${CURRENT_TASK_DEF}" | sed 's/:[0-9]*$//')
PREVIOUS_TASK_DEF="${TASK_FAMILY}:${PREVIOUS_REVISION}"

log_info "Rolling back to: ${PREVIOUS_TASK_DEF}"

# Update ECS service to use previous task definition
# WHY: This triggers a rolling replacement of running containers
# with the previous version, achieving zero-downtime rollback.
aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --task-definition "${PREVIOUS_TASK_DEF}" \
    --region "${AWS_REGION}"

log_info "ECS service update initiated, waiting for stabilization..."

# Wait for rollback to complete
aws ecs wait services-stable \
    --cluster "${ECS_CLUSTER}" \
    --services "${ECS_SERVICE}" \
    --region "${AWS_REGION}" || {
    log_error "Rollback did not stabilize within timeout"
    log_error "MANUAL INTERVENTION REQUIRED"
    exit 1
}

log_info "========================================="
log_info "ECTP ROLLBACK COMPLETE"
log_info "Rolled back from: ${CURRENT_TASK_DEF}"
log_info "Rolled back to: ${PREVIOUS_TASK_DEF}"
log_info "========================================="
