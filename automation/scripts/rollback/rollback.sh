#!/usr/bin/env bash
# ============================================================================
# Netflix Real-Time LLM Personalization & Inference Platform
# Rollback Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Provides rapid rollback capability for the Netflix LLM inference platform.
#   When a deployment causes degraded inference quality, latency spikes, or
#   GPU errors, this script restores the previous stable version.
#
# WHAT IT DOES:
#   1. Validates the target rollback version and region
#   2. Rolls back the EKS deployment to the specified version
#   3. Waits for rollout to stabilize
#   4. Runs health checks to confirm inference is operational
#   5. Notifies the team on completion
#
# USAGE:
#   ./rollback.sh <region> <version>
#   Example: ./rollback.sh us-east-1 v2.2.0
#   Example: ./rollback.sh us-west-2 v2.2.0
#
# SUPPORTED REGIONS:
#   - us-east-1  (Primary - N. Virginia)
#   - us-west-2  (Secondary - Oregon)
#   - eu-west-1  (Europe - Ireland)
#
# SECURITY:
#   - Requires EKS cluster access and kubectl credentials
#   - All rollback actions are logged for audit
#   - Automatic PagerDuty incident creation for production rollbacks
# ============================================================================

set -euo pipefail

# ---- Configuration ----
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PLATFORM_NAME="netflix-llm-inference"
readonly EKS_CLUSTER_PREFIX="netflix-llm"
readonly ECR_REPO="netflix/llm-inference"
readonly NAMESPACE="llm-inference"
readonly DEPLOYMENT_NAME="llm-inference-server"
readonly LOG_FILE="/var/log/netflix-llm/rollback-$(date +%Y%m%d-%H%M%S).log"

readonly SUPPORTED_REGIONS=("us-east-1" "us-west-2" "eu-west-1")
readonly HEALTH_CHECK_RETRIES=10
readonly HEALTH_CHECK_INTERVAL=15
readonly ROLLBACK_TIMEOUT="600s"

# ---- Input Validation ----
REGION="${1:-}"
TARGET_VERSION="${2:-}"

if [[ -z "${REGION}" || -z "${TARGET_VERSION}" ]]; then
    echo "ERROR: Missing required arguments"
    echo ""
    echo "Usage: $0 <region> <version>"
    echo ""
    echo "  region:  us-east-1 | us-west-2 | eu-west-1"
    echo "  version: Version to roll back to (e.g., v2.2.0)"
    echo ""
    echo "Examples:"
    echo "  $0 us-east-1 v2.2.0     # Roll back us-east-1 to v2.2.0"
    echo "  $0 us-west-2 v2.1.5     # Roll back us-west-2 to v2.1.5"
    exit 1
fi

# Validate region
region_valid=false
for supported in "${SUPPORTED_REGIONS[@]}"; do
    if [[ "${REGION}" == "${supported}" ]]; then
        region_valid=true
        break
    fi
done

if [[ "${region_valid}" == "false" ]]; then
    echo "ERROR: Unsupported region '${REGION}'"
    echo "Supported regions: ${SUPPORTED_REGIONS[*]}"
    exit 1
fi

# ---- Derived Configuration ----
readonly AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo 'unknown')}"
readonly ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
readonly EKS_CLUSTER="${EKS_CLUSTER_PREFIX}-${REGION}"
readonly ROLLBACK_IMAGE="${ECR_REGISTRY}/${ECR_REPO}:${TARGET_VERSION}"

# ---- Logging Functions ----
mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true

log_info() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [ROLLBACK] [INFO] [${REGION}] $*"
    echo "${msg}"
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_warn() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [ROLLBACK] [WARN] [${REGION}] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [ROLLBACK] [ERROR] [${REGION}] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

# ---- Notification Function ----
send_notification() {
    local severity="$1"
    local message="$2"
    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Send to Slack webhook if configured
    if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
        local color
        case "${severity}" in
            SUCCESS) color="#27AE60" ;;
            FAILURE) color="#E74C3C" ;;
            *) color="#E8A838" ;;
        esac

        curl -sf -X POST "${SLACK_WEBHOOK_URL}" \
            -H "Content-Type: application/json" \
            -d "{
                \"attachments\": [{
                    \"color\": \"${color}\",
                    \"title\": \"LLM Inference Platform - ROLLBACK ${severity}\",
                    \"text\": \"${message}\",
                    \"fields\": [
                        {\"title\": \"Region\", \"value\": \"${REGION}\", \"short\": true},
                        {\"title\": \"Target Version\", \"value\": \"${TARGET_VERSION}\", \"short\": true},
                        {\"title\": \"Timestamp\", \"value\": \"${timestamp}\", \"short\": false}
                    ]
                }]
            }" 2>/dev/null || log_warn "Failed to send Slack notification"
    fi

    # PagerDuty notification for failures
    if [[ "${severity}" == "FAILURE" && -n "${PAGERDUTY_SERVICE_KEY:-}" ]]; then
        curl -sf -X POST "https://events.pagerduty.com/v2/enqueue" \
            -H "Content-Type: application/json" \
            -d "{
                \"routing_key\": \"${PAGERDUTY_SERVICE_KEY}\",
                \"event_action\": \"trigger\",
                \"payload\": {
                    \"summary\": \"ROLLBACK FAILED: ${message}\",
                    \"severity\": \"critical\",
                    \"source\": \"rollback.sh\",
                    \"component\": \"${PLATFORM_NAME}\",
                    \"group\": \"${REGION}\"
                }
            }" 2>/dev/null || log_warn "Failed to send PagerDuty alert"
    fi

    log_info "Notification [${severity}]: ${message}"
}

# ---- Health Check ----
verify_health() {
    log_info "Running post-rollback health checks..."

    local health_endpoint
    case "${REGION}" in
        us-east-1) health_endpoint="https://llm-inference-use1.netflix.internal/health" ;;
        us-west-2) health_endpoint="https://llm-inference-usw2.netflix.internal/health" ;;
        eu-west-1) health_endpoint="https://llm-inference-euw1.netflix.internal/health" ;;
    esac

    local retry_count=0

    while [[ ${retry_count} -lt ${HEALTH_CHECK_RETRIES} ]]; do
        # HTTP health check
        local http_status
        http_status=$(curl -sf -o /dev/null -w "%{http_code}" \
            --max-time 10 "${health_endpoint}" 2>/dev/null || echo "000")

        if [[ "${http_status}" == "200" ]]; then
            log_info "HTTP health check passed (${health_endpoint})"

            # Verify inference endpoint responds
            local inference_test
            inference_test=$(curl -sf --max-time 30 \
                -X POST "${health_endpoint%/health}/v1/predict" \
                -H "Content-Type: application/json" \
                -d '{"user_id": "rollback_check_user", "context": "test", "dry_run": true}' \
                2>/dev/null || echo "")

            if echo "${inference_test}" | jq -e '.prediction' &>/dev/null; then
                log_info "Inference endpoint operational after rollback"

                # Verify pod readiness
                local ready_pods total_pods
                ready_pods=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
                    -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}" \
                    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
                total_pods=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
                    -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}" \
                    -o jsonpath='{.status.replicas}' 2>/dev/null || echo "0")
                log_info "Pod readiness: ${ready_pods}/${total_pods}"

                if [[ "${ready_pods}" -eq "${total_pods}" && "${ready_pods}" -gt 0 ]]; then
                    log_info "All pods ready and healthy"
                    return 0
                else
                    log_warn "Not all pods ready yet (${ready_pods}/${total_pods})"
                fi
            else
                log_warn "Inference test did not return expected response"
            fi
        fi

        retry_count=$((retry_count + 1))
        log_warn "Health check attempt ${retry_count}/${HEALTH_CHECK_RETRIES} (HTTP ${http_status}), retrying in ${HEALTH_CHECK_INTERVAL}s..."
        sleep "${HEALTH_CHECK_INTERVAL}"
    done

    log_error "Health check failed after ${HEALTH_CHECK_RETRIES} attempts"
    return 1
}

# ---- Main Rollback Execution ----
main() {
    local start_time
    start_time=$(date +%s)

    log_info "============================================================"
    log_info "Netflix LLM Inference Platform - ROLLBACK INITIATED"
    log_info "============================================================"
    log_info "Region:         ${REGION}"
    log_info "Target Version: ${TARGET_VERSION}"
    log_info "Cluster:        ${EKS_CLUSTER}"
    log_info "Rollback Image: ${ROLLBACK_IMAGE}"
    log_info "Timestamp:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log_info "============================================================"

    # Step 1: Update kubeconfig
    log_info "Updating kubeconfig for ${EKS_CLUSTER}..."
    aws eks update-kubeconfig \
        --name "${EKS_CLUSTER}" \
        --region "${REGION}" \
        --alias "${EKS_CLUSTER}" || {
        log_error "Failed to update kubeconfig"
        exit 1
    }

    # Step 2: Get current running version
    local current_version
    current_version=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | \
        awk -F: '{print $NF}' || echo "unknown")
    log_info "Current version: ${current_version}"

    if [[ "${current_version}" == "${TARGET_VERSION}" ]]; then
        log_warn "Cluster is already running ${TARGET_VERSION}. No rollback needed."
        exit 0
    fi

    # Step 3: Verify the target image exists in ECR
    log_info "Verifying target image exists in ECR..."
    if ! aws ecr describe-images \
        --repository-name "${ECR_REPO}" \
        --image-ids imageTag="${TARGET_VERSION}" \
        --region "${REGION}" &>/dev/null; then
        log_error "Target image ${ROLLBACK_IMAGE} not found in ECR"
        log_error "Available tags:"
        aws ecr list-images \
            --repository-name "${ECR_REPO}" \
            --region "${REGION}" \
            --query 'imageIds[*].imageTag' \
            --output table 2>/dev/null || true
        exit 1
    fi
    log_info "Target image verified in ECR"

    # Step 4: Remove any canary deployment
    log_info "Cleaning up canary deployments..."
    kubectl delete deployment "${DEPLOYMENT_NAME}-canary" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" 2>/dev/null || true

    # Step 5: Update deployment image
    log_info "Rolling back deployment to ${TARGET_VERSION}..."
    kubectl set image "deployment/${DEPLOYMENT_NAME}" \
        "llm-inference=${ROLLBACK_IMAGE}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}"

    # Step 6: Wait for rollout to stabilize
    log_info "Waiting for rollback to stabilize (timeout: ${ROLLBACK_TIMEOUT})..."
    if ! kubectl rollout status "deployment/${DEPLOYMENT_NAME}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        --timeout="${ROLLBACK_TIMEOUT}"; then
        log_error "Rollback did not stabilize within timeout"
        log_error "MANUAL INTERVENTION REQUIRED"
        send_notification "FAILURE" "Rollback to ${TARGET_VERSION} in ${REGION} did not stabilize. Manual intervention required."
        exit 1
    fi

    log_info "Rollback deployment stabilized"

    # Step 7: Verify health
    if ! verify_health; then
        log_error "Health checks failed after rollback"
        log_error "MANUAL INTERVENTION REQUIRED"
        send_notification "FAILURE" "Rollback to ${TARGET_VERSION} in ${REGION} completed but health checks failed. Manual intervention required."
        exit 1
    fi

    # Step 8: Record rollback metadata
    kubectl annotate deployment "${DEPLOYMENT_NAME}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        --overwrite \
        "deploy.netflix.com/version=${TARGET_VERSION}" \
        "deploy.netflix.com/rollback-from=${current_version}" \
        "deploy.netflix.com/rollback-timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "deploy.netflix.com/rollback-by=$(whoami)" 2>/dev/null || true

    local end_time
    end_time=$(date +%s)
    local duration=$(( end_time - start_time ))

    log_info "============================================================"
    log_info "ROLLBACK COMPLETE"
    log_info "============================================================"
    log_info "Region:          ${REGION}"
    log_info "Rolled back from: ${current_version}"
    log_info "Rolled back to:   ${TARGET_VERSION}"
    log_info "Duration:         ${duration} seconds"
    log_info "Log file:         ${LOG_FILE}"
    log_info "============================================================"

    send_notification "SUCCESS" "Successfully rolled back ${REGION} from ${current_version} to ${TARGET_VERSION} in ${duration}s. All health checks passed."
}

# Run the main function
main
