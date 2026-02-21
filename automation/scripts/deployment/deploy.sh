#!/usr/bin/env bash
# ============================================================================
# Enterprise Cloud Transformation Platform (ECTP)
# Deployment Script
# Author: Gopi Krishna Vajrala
# ============================================================================
#
# WHY THIS SCRIPT EXISTS:
#   Automates the deployment process for ECTP across all environments.
#   This script is called by the CI/CD pipeline (GitHub Actions) and can
#   also be run manually for emergency deployments.
#
# WHAT IT DOES:
#   1. Validates the deployment environment
#   2. Checks prerequisites (AWS CLI, Terraform, Docker)
#   3. Builds and pushes Docker image to ECR
#   4. Applies Terraform infrastructure changes
#   5. Updates ECS service with new task definition
#   6. Waits for deployment to stabilize
#   7. Runs post-deployment health checks
#
# USAGE:
#   ./deploy.sh <environment> <version>
#   Example: ./deploy.sh dev 1.0.0
#   Example: ./deploy.sh production 1.2.3
#
# SECURITY:
#   - Requires valid AWS credentials (IAM role or profile)
#   - Production deployments require MFA (enforced by IAM policy)
#   - All actions are logged for audit
# ============================================================================

# Exit immediately if any command fails.
# WHY: A failed step in deployment should stop the process immediately
# to prevent partial deployments (which are worse than no deployment).
set -euo pipefail

# ---- Configuration ----
# Script-level constants
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly AWS_REGION="${AWS_REGION:-us-east-1}"
readonly ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
readonly ECR_REPO="ectp/api"
readonly ECS_CLUSTER_PREFIX="ectp"

# ---- Input Validation ----
# Validate that required arguments are provided
ENVIRONMENT="${1:-}"
VERSION="${2:-}"

if [[ -z "${ENVIRONMENT}" || -z "${VERSION}" ]]; then
    echo "ERROR: Missing required arguments"
    echo "Usage: $0 <environment> <version>"
    echo "  environment: dev | qa | uat | production"
    echo "  version: Semantic version (e.g., 1.0.0)"
    exit 1
fi

# Validate environment name
# WHY: Prevents accidental deployment to non-existent environments
# and ensures consistent naming across all tools.
if [[ ! "${ENVIRONMENT}" =~ ^(dev|qa|uat|production)$ ]]; then
    echo "ERROR: Invalid environment '${ENVIRONMENT}'"
    echo "Valid environments: dev, qa, uat, production"
    exit 1
fi

# ---- Logging Functions ----
# Consistent log format with timestamps for troubleshooting

log_info() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [INFO] $*"
}

log_warn() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [WARN] $*" >&2
}

log_error() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2
}

# ---- Prerequisite Checks ----
check_prerequisites() {
    log_info "Checking deployment prerequisites..."

    # Check AWS CLI
    # WHY: Required for ECR login, ECS updates, and all AWS operations
    if ! command -v aws &>/dev/null; then
        log_error "AWS CLI not found. Install: https://aws.amazon.com/cli/"
        exit 1
    fi

    # Check Docker
    # WHY: Required to build and push container images
    if ! command -v docker &>/dev/null; then
        log_error "Docker not found. Install: https://docs.docker.com/install/"
        exit 1
    fi

    # Check Terraform
    # WHY: Required for infrastructure changes
    if ! command -v terraform &>/dev/null; then
        log_error "Terraform not found. Install: https://terraform.io/downloads"
        exit 1
    fi

    # Verify AWS credentials
    # WHY: Ensures we can authenticate before starting the deployment.
    # Catching this early avoids partial deployments.
    if ! aws sts get-caller-identity &>/dev/null; then
        log_error "AWS credentials not configured or expired"
        exit 1
    fi

    local aws_account
    aws_account=$(aws sts get-caller-identity --query "Account" --output text)
    log_info "AWS Account: ${aws_account}"
    log_info "AWS Region: ${AWS_REGION}"
    log_info "Environment: ${ENVIRONMENT}"
    log_info "Version: ${VERSION}"

    log_info "All prerequisites satisfied"
}

# ---- Docker Build & Push ----
build_and_push() {
    log_info "Building Docker image..."

    local image_tag="${ECR_REGISTRY}/${ECR_REPO}:${VERSION}"
    local latest_tag="${ECR_REGISTRY}/${ECR_REPO}:${ENVIRONMENT}-latest"

    # Authenticate Docker with ECR
    # WHY: ECR requires authentication tokens that expire after 12 hours.
    # We get a fresh token before every push.
    aws ecr get-login-password --region "${AWS_REGION}" | \
        docker login --username AWS --password-stdin "${ECR_REGISTRY}"

    # Build the Docker image using the production Dockerfile
    # WHY: Multi-stage build produces a minimal production image
    docker build \
        --file "${PROJECT_ROOT}/deployment/docker/Dockerfile" \
        --tag "${image_tag}" \
        --tag "${latest_tag}" \
        --build-arg VERSION="${VERSION}" \
        --build-arg ENVIRONMENT="${ENVIRONMENT}" \
        "${PROJECT_ROOT}"

    # Push both version-tagged and latest-tagged images
    # WHY: Version tag for rollback capability, latest tag for convenience
    docker push "${image_tag}"
    docker push "${latest_tag}"

    log_info "Docker image pushed: ${image_tag}"
}

# ---- Terraform Apply ----
apply_infrastructure() {
    log_info "Applying Terraform infrastructure changes..."

    local tf_dir="${PROJECT_ROOT}/infrastructure/terraform/environments/${ENVIRONMENT}"

    # Check if Terraform directory exists
    if [[ ! -d "${tf_dir}" ]]; then
        log_error "Terraform directory not found: ${tf_dir}"
        exit 1
    fi

    cd "${tf_dir}"

    # Initialize Terraform (downloads providers, configures backend)
    # WHY: Must be run before any Terraform commands. -reconfigure handles
    # backend changes without interactive prompts.
    terraform init -reconfigure

    # Plan the changes (dry run)
    # WHY: Shows exactly what will change before applying.
    # The plan file ensures the apply matches what was reviewed.
    terraform plan \
        -var="app_version=${VERSION}" \
        -var="environment=${ENVIRONMENT}" \
        -out=tfplan

    # Apply the planned changes
    # WHY: -auto-approve skips confirmation in CI/CD. The plan file
    # ensures only the reviewed changes are applied.
    terraform apply -auto-approve tfplan

    # Clean up the plan file
    rm -f tfplan

    log_info "Terraform changes applied successfully"
}

# ---- ECS Deployment ----
deploy_ecs() {
    log_info "Updating ECS service..."

    local cluster="${ECS_CLUSTER_PREFIX}-${ENVIRONMENT}"
    local service="ectp-api-${ENVIRONMENT}"

    # Force new deployment of the ECS service
    # WHY: This tells ECS to pull the latest image and replace running tasks
    # using the rolling deployment strategy (zero downtime).
    aws ecs update-service \
        --cluster "${cluster}" \
        --service "${service}" \
        --force-new-deployment \
        --region "${AWS_REGION}"

    log_info "ECS service update initiated"

    # Wait for the deployment to stabilize
    # WHY: The script should not exit until the deployment is confirmed stable.
    # This prevents CI/CD from reporting success on a failing deployment.
    log_info "Waiting for deployment to stabilize (timeout: 10 minutes)..."

    aws ecs wait services-stable \
        --cluster "${cluster}" \
        --services "${service}" \
        --region "${AWS_REGION}" || {
        log_error "ECS deployment did not stabilize within timeout"
        exit 1
    }

    log_info "ECS deployment stabilized"
}

# ---- Health Check ----
post_deployment_check() {
    log_info "Running post-deployment health checks..."

    # Determine the health check URL based on environment
    local health_url
    case "${ENVIRONMENT}" in
        dev)        health_url="https://ectp-dev.university.edu/health" ;;
        qa)         health_url="https://ectp-qa.university.edu/health" ;;
        uat)        health_url="https://ectp-uat.university.edu/health" ;;
        production) health_url="https://ectp.university.edu/health" ;;
    esac

    # Retry health check up to 5 times with 10-second intervals
    # WHY: The new container may take a few seconds to start accepting traffic
    local max_retries=5
    local retry_count=0

    while [[ ${retry_count} -lt ${max_retries} ]]; do
        if curl -sf "${health_url}" >/dev/null 2>&1; then
            log_info "Health check passed: ${health_url}"
            return 0
        fi

        retry_count=$((retry_count + 1))
        log_warn "Health check attempt ${retry_count}/${max_retries} failed, retrying in 10s..."
        sleep 10
    done

    log_error "Health check failed after ${max_retries} attempts"
    return 1
}

# ---- Main Execution ----
main() {
    log_info "========================================="
    log_info "ECTP Deployment Starting"
    log_info "Environment: ${ENVIRONMENT}"
    log_info "Version: ${VERSION}"
    log_info "========================================="

    check_prerequisites
    build_and_push
    apply_infrastructure
    deploy_ecs
    post_deployment_check

    log_info "========================================="
    log_info "ECTP Deployment Complete!"
    log_info "Environment: ${ENVIRONMENT}"
    log_info "Version: ${VERSION}"
    log_info "========================================="
}

# Run the main function
main
