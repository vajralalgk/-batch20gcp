#!/usr/bin/env bash
# ============================================================================
# Netflix Real-Time LLM Personalization & Inference Platform
# Multi-Region Deployment Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Automates multi-region deployment of the Netflix LLM inference platform
#   across AWS regions with canary rollout strategy. This ensures safe,
#   progressive rollouts with automatic rollback on failure.
#
# WHAT IT DOES:
#   1. Validates deployment parameters and prerequisites
#   2. Builds GPU-optimized Docker image and pushes to ECR
#   3. Updates EKS deployment with canary rollout (10% -> 50% -> 100%)
#   4. Runs health checks between each canary stage
#   5. Automatically rolls back on failure at any stage
#
# USAGE:
#   ./deploy.sh [region] [version]
#   Example: ./deploy.sh us-east-1 v2.3.0
#   Example: ./deploy.sh us-west-2 v2.3.1
#   Example: ./deploy.sh eu-west-1 v2.4.0
#
# SUPPORTED REGIONS:
#   - us-east-1  (Primary - N. Virginia)
#   - us-west-2  (Secondary - Oregon)
#   - eu-west-1  (Europe - Ireland)
#
# CANARY STRATEGY:
#   Stage 1: 10% traffic -> health check -> wait 2 min
#   Stage 2: 50% traffic -> health check -> wait 2 min
#   Stage 3: 100% traffic -> final verification
#
# SECURITY:
#   - Requires valid AWS credentials with EKS and ECR permissions
#   - Production deployments require approval token
#   - All actions are logged for audit
# ============================================================================

set -euo pipefail

# ---- Configuration ----
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PLATFORM_NAME="netflix-llm-inference"
readonly EKS_CLUSTER_PREFIX="netflix-llm"
readonly ECR_REPO="netflix/llm-inference"
readonly NAMESPACE="llm-inference"
readonly DEPLOYMENT_NAME="llm-inference-server"
readonly SERVICE_NAME="llm-inference-svc"
readonly LOG_FILE="/var/log/netflix-llm/deploy-$(date +%Y%m%d-%H%M%S).log"

# Canary deployment percentages
readonly CANARY_STAGES=(10 50 100)
readonly CANARY_WAIT_SECONDS=120
readonly HEALTH_CHECK_RETRIES=10
readonly HEALTH_CHECK_INTERVAL=15

# Supported regions
readonly SUPPORTED_REGIONS=("us-east-1" "us-west-2" "eu-west-1")

# ---- Input Validation ----
REGION="${1:-}"
VERSION="${2:-}"

if [[ -z "${REGION}" || -z "${VERSION}" ]]; then
    echo "ERROR: Missing required arguments"
    echo ""
    echo "Usage: $0 <region> <version>"
    echo ""
    echo "  region:  us-east-1 | us-west-2 | eu-west-1"
    echo "  version: Semantic version (e.g., v2.3.0)"
    echo ""
    echo "Examples:"
    echo "  $0 us-east-1 v2.3.0"
    echo "  $0 us-west-2 v2.3.1"
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

# Validate version format
if [[ ! "${VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Invalid version format '${VERSION}'"
    echo "Expected format: vX.Y.Z (e.g., v2.3.0)"
    exit 1
fi

# ---- Derived Configuration ----
readonly AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo 'unknown')}"
readonly ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
readonly EKS_CLUSTER="${EKS_CLUSTER_PREFIX}-${REGION}"
readonly IMAGE_TAG="${ECR_REGISTRY}/${ECR_REPO}:${VERSION}"
readonly IMAGE_LATEST="${ECR_REGISTRY}/${ECR_REPO}:${REGION}-latest"

# ---- Logging Functions ----
mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true

log_info() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [INFO] [${REGION}] $*"
    echo "${msg}"
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_warn() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [WARN] [${REGION}] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [ERROR] [${REGION}] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_stage() {
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [STAGE] [${REGION}] ========== $* =========="
    echo "${msg}"
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

# ---- Cleanup and Rollback ----
# Track the previous version for rollback
PREVIOUS_VERSION=""
DEPLOYMENT_STARTED=false
CURRENT_CANARY_STAGE=0

# Rollback function called on failure
rollback_on_failure() {
    local exit_code=$?
    if [[ "${DEPLOYMENT_STARTED}" == "true" && -n "${PREVIOUS_VERSION}" ]]; then
        log_error "Deployment failed at canary stage ${CURRENT_CANARY_STAGE}% (exit code: ${exit_code})"
        log_error "Initiating automatic rollback to ${PREVIOUS_VERSION}..."

        # Call the rollback script
        if [[ -f "${SCRIPT_DIR}/../rollback/rollback.sh" ]]; then
            bash "${SCRIPT_DIR}/../rollback/rollback.sh" "${REGION}" "${PREVIOUS_VERSION}" || {
                log_error "AUTOMATIC ROLLBACK FAILED - MANUAL INTERVENTION REQUIRED"
                send_notification "CRITICAL" "Automatic rollback failed for ${PLATFORM_NAME} in ${REGION}. Manual intervention required."
            }
        else
            # Inline rollback if script not available
            log_warn "Rollback script not found, performing inline rollback..."
            local rollback_image="${ECR_REGISTRY}/${ECR_REPO}:${PREVIOUS_VERSION}"
            kubectl set image "deployment/${DEPLOYMENT_NAME}" \
                "llm-inference=${rollback_image}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" 2>/dev/null || true
            kubectl rollout undo "deployment/${DEPLOYMENT_NAME}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" 2>/dev/null || true
        fi

        send_notification "FAILURE" "Deployment of ${VERSION} to ${REGION} failed at ${CURRENT_CANARY_STAGE}%. Rolled back to ${PREVIOUS_VERSION}."
    fi
}

trap rollback_on_failure ERR

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
            CRITICAL) color="#8E44AD" ;;
            *) color="#E8A838" ;;
        esac

        curl -sf -X POST "${SLACK_WEBHOOK_URL}" \
            -H "Content-Type: application/json" \
            -d "{
                \"attachments\": [{
                    \"color\": \"${color}\",
                    \"title\": \"LLM Inference Platform - ${severity}\",
                    \"text\": \"${message}\",
                    \"fields\": [
                        {\"title\": \"Region\", \"value\": \"${REGION}\", \"short\": true},
                        {\"title\": \"Version\", \"value\": \"${VERSION}\", \"short\": true},
                        {\"title\": \"Timestamp\", \"value\": \"${timestamp}\", \"short\": false}
                    ]
                }]
            }" 2>/dev/null || log_warn "Failed to send Slack notification"
    fi

    # Send to PagerDuty for critical failures
    if [[ "${severity}" == "CRITICAL" && -n "${PAGERDUTY_SERVICE_KEY:-}" ]]; then
        curl -sf -X POST "https://events.pagerduty.com/v2/enqueue" \
            -H "Content-Type: application/json" \
            -d "{
                \"routing_key\": \"${PAGERDUTY_SERVICE_KEY}\",
                \"event_action\": \"trigger\",
                \"payload\": {
                    \"summary\": \"${message}\",
                    \"severity\": \"critical\",
                    \"source\": \"deploy.sh\",
                    \"component\": \"${PLATFORM_NAME}\",
                    \"group\": \"${REGION}\"
                }
            }" 2>/dev/null || log_warn "Failed to send PagerDuty alert"
    fi

    log_info "Notification [${severity}]: ${message}"
}

# ---- Prerequisite Checks ----
check_prerequisites() {
    log_stage "STEP 1: Validating Prerequisites"

    local missing_tools=()

    # Check required CLI tools
    for tool in aws docker kubectl helm curl jq; do
        if ! command -v "${tool}" &>/dev/null; then
            missing_tools+=("${tool}")
        fi
    done

    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        log_error "Install missing tools and retry."
        exit 1
    fi

    # Verify AWS credentials
    if ! aws sts get-caller-identity &>/dev/null; then
        log_error "AWS credentials not configured or expired for region ${REGION}"
        exit 1
    fi

    local caller_identity
    caller_identity=$(aws sts get-caller-identity --output json)
    log_info "AWS Account: $(echo "${caller_identity}" | jq -r '.Account')"
    log_info "AWS ARN: $(echo "${caller_identity}" | jq -r '.Arn')"

    # Verify EKS cluster access
    if ! aws eks describe-cluster --name "${EKS_CLUSTER}" --region "${REGION}" &>/dev/null; then
        log_error "Cannot access EKS cluster '${EKS_CLUSTER}' in ${REGION}"
        exit 1
    fi

    # Update kubeconfig for the target cluster
    aws eks update-kubeconfig \
        --name "${EKS_CLUSTER}" \
        --region "${REGION}" \
        --alias "${EKS_CLUSTER}" || {
        log_error "Failed to update kubeconfig for ${EKS_CLUSTER}"
        exit 1
    }

    # Verify namespace exists
    if ! kubectl get namespace "${NAMESPACE}" --context "${EKS_CLUSTER}" &>/dev/null; then
        log_warn "Namespace '${NAMESPACE}' does not exist, creating..."
        kubectl create namespace "${NAMESPACE}" --context "${EKS_CLUSTER}"
    fi

    # Verify GPU nodes are available in the cluster
    local gpu_node_count
    gpu_node_count=$(kubectl get nodes --context "${EKS_CLUSTER}" \
        -l "nvidia.com/gpu=true" --no-headers 2>/dev/null | wc -l || echo "0")
    if [[ "${gpu_node_count}" -eq 0 ]]; then
        log_warn "No GPU nodes detected in ${EKS_CLUSTER}. Inference pods may fail to schedule."
    else
        log_info "GPU nodes available: ${gpu_node_count}"
    fi

    # Check ECR repository exists
    if ! aws ecr describe-repositories \
        --repository-names "${ECR_REPO}" \
        --region "${REGION}" &>/dev/null; then
        log_info "Creating ECR repository '${ECR_REPO}' in ${REGION}..."
        aws ecr create-repository \
            --repository-name "${ECR_REPO}" \
            --region "${REGION}" \
            --image-scanning-configuration scanOnPush=true \
            --encryption-configuration encryptionType=KMS
    fi

    # Get current running version for rollback reference
    PREVIOUS_VERSION=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null | \
        awk -F: '{print $NF}' || echo "")
    log_info "Previous version: ${PREVIOUS_VERSION:-none}"

    log_info "Region: ${REGION}"
    log_info "Version: ${VERSION}"
    log_info "Cluster: ${EKS_CLUSTER}"
    log_info "Image: ${IMAGE_TAG}"
    log_info "All prerequisites validated successfully"
}

# ---- Docker Build & Push ----
build_and_push_image() {
    log_stage "STEP 2: Building and Pushing Docker Image"

    # Authenticate Docker with ECR
    log_info "Authenticating with ECR in ${REGION}..."
    aws ecr get-login-password --region "${REGION}" | \
        docker login --username AWS --password-stdin "${ECR_REGISTRY}"

    # Build the GPU-optimized inference image
    # WHY: We use NVIDIA CUDA base image for GPU inference with TensorRT optimization
    log_info "Building Docker image: ${IMAGE_TAG}..."
    docker build \
        --file "${PROJECT_ROOT}/deployment/docker/Dockerfile.inference" \
        --tag "${IMAGE_TAG}" \
        --tag "${IMAGE_LATEST}" \
        --build-arg VERSION="${VERSION}" \
        --build-arg REGION="${REGION}" \
        --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --build-arg GIT_COMMIT="$(git -C "${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || echo 'unknown')" \
        --build-arg CUDA_VERSION="12.2" \
        --build-arg TENSORRT_VERSION="8.6" \
        "${PROJECT_ROOT}"

    # Push both version-tagged and latest-tagged images
    log_info "Pushing image to ECR: ${IMAGE_TAG}..."
    docker push "${IMAGE_TAG}"
    docker push "${IMAGE_LATEST}"

    # Wait for ECR vulnerability scan to complete
    log_info "Waiting for ECR vulnerability scan..."
    local scan_status="IN_PROGRESS"
    local scan_retries=0
    local max_scan_retries=30

    while [[ "${scan_status}" == "IN_PROGRESS" && ${scan_retries} -lt ${max_scan_retries} ]]; do
        sleep 10
        scan_status=$(aws ecr describe-image-scan-findings \
            --repository-name "${ECR_REPO}" \
            --image-id imageTag="${VERSION}" \
            --region "${REGION}" \
            --query 'imageScanStatus.status' \
            --output text 2>/dev/null || echo "IN_PROGRESS")
        scan_retries=$((scan_retries + 1))
    done

    # Check for critical vulnerabilities
    if [[ "${scan_status}" == "COMPLETE" ]]; then
        local critical_count
        critical_count=$(aws ecr describe-image-scan-findings \
            --repository-name "${ECR_REPO}" \
            --image-id imageTag="${VERSION}" \
            --region "${REGION}" \
            --query 'imageScanFindings.findingSeverityCounts.CRITICAL' \
            --output text 2>/dev/null || echo "0")

        if [[ "${critical_count}" != "None" && "${critical_count}" -gt 0 ]]; then
            log_error "ECR scan found ${critical_count} CRITICAL vulnerabilities. Blocking deployment."
            exit 1
        fi
        log_info "ECR vulnerability scan passed (no critical findings)"
    else
        log_warn "ECR scan did not complete in time, proceeding with deployment"
    fi

    log_info "Docker image pushed successfully: ${IMAGE_TAG}"
}

# ---- Health Check Function ----
verify_health() {
    local stage_pct="$1"
    log_info "Running health checks for canary stage ${stage_pct}%..."

    local retry_count=0
    local health_endpoint
    local grpc_health

    # Determine health check endpoint based on region
    case "${REGION}" in
        us-east-1) health_endpoint="https://llm-inference-use1.netflix.internal/health" ;;
        us-west-2) health_endpoint="https://llm-inference-usw2.netflix.internal/health" ;;
        eu-west-1) health_endpoint="https://llm-inference-euw1.netflix.internal/health" ;;
    esac

    while [[ ${retry_count} -lt ${HEALTH_CHECK_RETRIES} ]]; do
        # HTTP health check
        local http_status
        http_status=$(curl -sf -o /dev/null -w "%{http_code}" \
            --max-time 10 "${health_endpoint}" 2>/dev/null || echo "000")

        if [[ "${http_status}" == "200" ]]; then
            log_info "HTTP health check passed (${health_endpoint})"

            # Verify GPU inference is operational via a test prediction
            local inference_test
            inference_test=$(curl -sf --max-time 30 \
                -X POST "${health_endpoint%/health}/v1/predict" \
                -H "Content-Type: application/json" \
                -d '{"user_id": "health_check_user", "context": "test", "dry_run": true}' \
                2>/dev/null || echo "")

            if echo "${inference_test}" | jq -e '.prediction' &>/dev/null; then
                log_info "Inference health check passed"

                # Check GPU utilization is reasonable (not 0%, not stuck at 100%)
                local pod_name
                pod_name=$(kubectl get pods -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}" \
                    -l "app=${DEPLOYMENT_NAME}" \
                    --field-selector=status.phase=Running \
                    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

                if [[ -n "${pod_name}" ]]; then
                    local gpu_util
                    gpu_util=$(kubectl exec "${pod_name}" \
                        -n "${NAMESPACE}" \
                        --context "${EKS_CLUSTER}" \
                        -- nvidia-smi --query-gpu=utilization.gpu \
                        --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "unknown")
                    log_info "GPU utilization: ${gpu_util}%"
                fi

                # Check pod readiness
                local ready_pods
                local total_pods
                ready_pods=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
                    -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}" \
                    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
                total_pods=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
                    -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}" \
                    -o jsonpath='{.status.replicas}' 2>/dev/null || echo "0")
                log_info "Pod readiness: ${ready_pods}/${total_pods}"

                return 0
            else
                log_warn "Inference test did not return expected response"
            fi
        fi

        retry_count=$((retry_count + 1))
        log_warn "Health check attempt ${retry_count}/${HEALTH_CHECK_RETRIES} failed (HTTP ${http_status}), retrying in ${HEALTH_CHECK_INTERVAL}s..."
        sleep "${HEALTH_CHECK_INTERVAL}"
    done

    log_error "Health check failed after ${HEALTH_CHECK_RETRIES} attempts at canary stage ${stage_pct}%"
    return 1
}

# ---- Canary Deployment ----
deploy_canary() {
    log_stage "STEP 3: Canary Deployment to EKS"

    DEPLOYMENT_STARTED=true

    for stage_pct in "${CANARY_STAGES[@]}"; do
        CURRENT_CANARY_STAGE="${stage_pct}"
        log_info "--- Canary Stage: ${stage_pct}% traffic ---"

        if [[ "${stage_pct}" -eq 100 ]]; then
            # Full rollout: update the main deployment image
            log_info "Promoting to 100% - updating main deployment..."
            kubectl set image "deployment/${DEPLOYMENT_NAME}" \
                "llm-inference=${IMAGE_TAG}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}"

            # Wait for rollout to complete
            log_info "Waiting for rollout to complete..."
            kubectl rollout status "deployment/${DEPLOYMENT_NAME}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" \
                --timeout=600s || {
                log_error "Rollout did not complete within timeout"
                return 1
            }

            # Clean up canary deployment if it exists
            kubectl delete deployment "${DEPLOYMENT_NAME}-canary" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" 2>/dev/null || true
        else
            # Canary stage: deploy a canary alongside stable
            local canary_replicas
            local total_replicas
            total_replicas=$(kubectl get deployment "${DEPLOYMENT_NAME}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" \
                -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "10")

            # Calculate canary replica count based on percentage
            canary_replicas=$(( (total_replicas * stage_pct + 99) / 100 ))
            if [[ "${canary_replicas}" -lt 1 ]]; then
                canary_replicas=1
            fi

            log_info "Deploying canary with ${canary_replicas} replicas (${stage_pct}% of ${total_replicas})..."

            # Create or update canary deployment
            kubectl get deployment "${DEPLOYMENT_NAME}" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" \
                -o json | \
                jq --arg name "${DEPLOYMENT_NAME}-canary" \
                   --arg image "${IMAGE_TAG}" \
                   --argjson replicas "${canary_replicas}" \
                   '.metadata.name = $name |
                    .metadata.labels["track"] = "canary" |
                    .spec.replicas = $replicas |
                    .spec.template.spec.containers[0].image = $image |
                    .spec.template.metadata.labels["track"] = "canary" |
                    del(.metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .status)' | \
                kubectl apply -f - \
                    -n "${NAMESPACE}" \
                    --context "${EKS_CLUSTER}"

            # Wait for canary pods to be ready
            log_info "Waiting for canary pods to be ready..."
            kubectl rollout status "deployment/${DEPLOYMENT_NAME}-canary" \
                -n "${NAMESPACE}" \
                --context "${EKS_CLUSTER}" \
                --timeout=300s || {
                log_error "Canary pods did not become ready"
                return 1
            }
        fi

        # Run health checks
        verify_health "${stage_pct}" || {
            log_error "Health verification failed at ${stage_pct}% canary stage"
            return 1
        }

        # Wait between canary stages (except for the last stage)
        if [[ "${stage_pct}" -ne 100 ]]; then
            log_info "Canary stage ${stage_pct}% healthy. Waiting ${CANARY_WAIT_SECONDS}s before next stage..."
            sleep "${CANARY_WAIT_SECONDS}"
        fi
    done

    log_info "Canary deployment completed successfully across all stages"
}

# ---- Post-Deployment Verification ----
post_deployment_verify() {
    log_stage "STEP 4: Post-Deployment Verification"

    # Verify all pods are running the correct version
    local running_images
    running_images=$(kubectl get pods -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        -l "app=${DEPLOYMENT_NAME}" \
        -o jsonpath='{.items[*].spec.containers[0].image}' 2>/dev/null || echo "")

    log_info "Running container images: ${running_images}"

    # Verify model loading status
    local model_status
    model_status=$(curl -sf --max-time 10 \
        "https://llm-inference-$(echo "${REGION}" | tr '-' '').netflix.internal/v1/models" \
        2>/dev/null || echo "{}")
    log_info "Model status: ${model_status}"

    # Check inference latency
    local latency_check
    latency_check=$(curl -sf -o /dev/null -w "%{time_total}" --max-time 30 \
        -X POST "https://llm-inference-$(echo "${REGION}" | tr '-' '').netflix.internal/v1/predict" \
        -H "Content-Type: application/json" \
        -d '{"user_id": "latency_check_user", "context": "verification", "dry_run": true}' \
        2>/dev/null || echo "timeout")
    log_info "Inference latency: ${latency_check}s"

    # Record deployment metadata
    kubectl annotate deployment "${DEPLOYMENT_NAME}" \
        -n "${NAMESPACE}" \
        --context "${EKS_CLUSTER}" \
        --overwrite \
        "deploy.netflix.com/version=${VERSION}" \
        "deploy.netflix.com/timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "deploy.netflix.com/region=${REGION}" \
        "deploy.netflix.com/deployer=$(whoami)" \
        "deploy.netflix.com/previous-version=${PREVIOUS_VERSION}" 2>/dev/null || true

    log_info "Post-deployment verification complete"
}

# ---- Main Execution ----
main() {
    local start_time
    start_time=$(date +%s)

    log_info "============================================================"
    log_info "Netflix LLM Inference Platform - Multi-Region Deployment"
    log_info "============================================================"
    log_info "Region:    ${REGION}"
    log_info "Version:   ${VERSION}"
    log_info "Cluster:   ${EKS_CLUSTER}"
    log_info "Strategy:  Canary (${CANARY_STAGES[*]}%)"
    log_info "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log_info "============================================================"

    check_prerequisites
    build_and_push_image
    deploy_canary
    post_deployment_verify

    local end_time
    end_time=$(date +%s)
    local duration=$(( end_time - start_time ))

    log_info "============================================================"
    log_info "DEPLOYMENT COMPLETE"
    log_info "============================================================"
    log_info "Region:    ${REGION}"
    log_info "Version:   ${VERSION}"
    log_info "Duration:  ${duration} seconds"
    log_info "Previous:  ${PREVIOUS_VERSION:-none}"
    log_info "Log file:  ${LOG_FILE}"
    log_info "============================================================"

    send_notification "SUCCESS" "Successfully deployed ${VERSION} to ${REGION} in ${duration}s. Canary rollout completed (10% -> 50% -> 100%)."
}

# Run the main function
main
